#!/usr/bin/env python3
"""Syntopik regression suite: Chapter autopilot, brainstem, learner psychology and work-mentor pedagogy.

Shared fixtures live in tests/_helpers.py. Run via:
    python3 -B -m unittest discover -s tests -t tests -v
"""

from __future__ import annotations

import contextlib
import json
import math
import os
import re
import tempfile
import unittest
import unittest.mock
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from _helpers import (
    ROOT,
    _EncodedStringIO,
    load_script_module,
    requires,
    run_cli,
)


class TestChapterAutopilot(unittest.TestCase):
    """13. Autonomous Chapter Autopilot (chapter_tracker.py + session-start.mjs)."""

    @requires("pymupdf", "pymupdf4llm")
    def test_full_autopilot_lifecycle_zero_repetition_and_turn0_hook(self) -> None:
        import pymupdf as fitz

        ct = load_script_module("chapter_tracker.py")

        def _ct(argv: list[str]) -> dict[str, Any]:
            buf = _EncodedStringIO()
            with contextlib.redirect_stdout(buf):
                ct.main(argv)
            return json.loads(buf.getvalue())

        with tempfile.TemporaryDirectory(prefix="mlh-ap-") as tmp:
            t = Path(tmp)
            ch_dir = t / "chapters"
            kg_dir = t / "kg"
            src_dir = t / "sources"
            ch_dir.mkdir()
            kg_dir.mkdir()
            src_dir.mkdir()

            # Create a tiny 2-page PDF so init --source-dir discovers it cleanly
            pdf_file = src_dir / "shm-whiteboard.pdf"
            doc = fitz.open()
            doc.new_page()
            doc.new_page()
            doc.save(str(pdf_file))
            doc.close()

            base = ["--chapters-dir", str(ch_dir), "--kg-dir", str(kg_dir), "--no-sync", "--json"]

            # 1. init -> next must be index_sources
            init_res = _ct(["init", "shm", "--title", "Simple Harmonic Motion", "--subject", "Physics", "--source-dir", str(src_dir), "--set-active", *base])
            self.assertEqual(init_res["slug"], "shm")
            init_sources = init_res["tracker"]["sources"]
            self.assertGreaterEqual(len(init_sources), 1)
            self.assertEqual(_ct(["next", "shm", *base])["action"], "index_sources")

            # 2. Mark sourcesIndexed + add topics & questions -> next must be probe_frontier
            _ct(["set-milestone", "shm", "sourcesIndexed", "--value", "true", *base])
            topics = [
                {"id": "t1", "order": 1, "title": "Restoring Law", "level": "basics", "prereqTopicIds": [], "sourceRefs": [{"sourceId": init_sources[0]["id"], "pages": "1-2"}]},
                {"id": "t2", "order": 2, "title": "Spring Combinations", "level": "main", "prereqTopicIds": ["t1"]},
            ]
            questions = [
                {"qid": "ex1-q1", "label": "Ex-1 Q1", "topicId": "t1", "level": "basics", "page": 1},
                {"qid": "ex1-q2", "label": "Ex-1 Q2", "topicId": "t1", "level": "basics", "page": 2},
                {"qid": "ex2-q1", "label": "Ex-2 Q1", "topicId": "t2", "level": "main", "page": 2},
            ]
            _ct(["add-topics", "shm", "--json-data", json.dumps(topics), *base])
            _ct(["add-questions", "shm", "--json-data", json.dumps(questions), *base])
            self.assertEqual(_ct(["next", "shm", *base])["action"], "probe_frontier")

            # 3. Mark probed -> teach_topic t1; test pause-doubt / clear-doubt
            _ct(["set-milestone", "shm", "probed", "--value", "true", *base])
            n_t1 = _ct(["next", "shm", *base])
            self.assertEqual(n_t1["action"], "teach_topic")
            self.assertEqual(n_t1["topic"]["id"], "t1")

            _ct(["pause-doubt", "shm", "--query", "Why does phase jump by pi?", *base])
            n_doubt = _ct(["next", "shm", *base])
            self.assertEqual(n_doubt["action"], "resolve_doubt")
            self.assertIn("activeDoubt", n_doubt)
            self.assertEqual(n_doubt["resumeAction"]["action"], "teach_topic")
            _ct(["clear-doubt", "shm", *base])

            # 4. Master t1 -> solve_question ex1-q1; mark ex1-q1 retest -> retestGap skips it to ex1-q2
            _ct(["set-topic", "shm", "t1", "--status", "mastered", *base])
            n_q1 = _ct(["next", "shm", *base])
            self.assertEqual(n_q1["action"], "solve_question")
            self.assertEqual(n_q1["question"]["qid"], "ex1-q1")

            _ct(["mark-question", "shm", "ex1-q1", "--status", "retest", "--mistake-category", "sign_error", *base])
            n_q2 = _ct(["next", "shm", *base])
            self.assertEqual(n_q2["action"], "solve_question")
            self.assertEqual(n_q2["question"]["qid"], "ex1-q2")

            # 5. Mark ex1-q2 solved + ZERO-REPETITION INVARIANT: re-upserting ex1-q2 as unseen MUST NOT downgrade it!
            _ct(["mark-question", "shm", "ex1-q2", "--status", "solved", *base])
            _ct(["add-questions", "shm", "--json-data", json.dumps([{"qid": "ex1-q2", "label": "Ex-1 Q2", "topicId": "t1", "level": "basics", "status": "unseen"}]), *base])
            tracker_now = ct.load_tracker("shm", ch_dir)
            q2_row = next(q for q in tracker_now["questions"] if q["qid"] == "ex1-q2")
            self.assertEqual(q2_row["status"], "solved")

            # 6. Next moves to t2 (since ex1-q1 is still cooling within retestGap=3); advance steps so ex1-q1 resurfaces!
            n_t2 = _ct(["next", "shm", *base])
            self.assertEqual(n_t2["action"], "teach_topic")
            self.assertEqual(n_t2["topic"]["id"], "t2")
            _ct(["set-topic", "shm", "t2", "--status", "mastered", *base])
            _ct(["mark-question", "shm", "ex2-q1", "--status", "solved", *base])

            # Now stepCounter - ex1-q1.lastStep >= 3 -> retest_question resurfaces ex1-q1!
            n_ret = _ct(["next", "shm", *base])
            self.assertEqual(n_ret["action"], "retest_question")
            self.assertEqual(n_ret["question"]["qid"], "ex1-q1")

            # 7. Turn-0 hook injection via MLH_CHAPTERS_DIR while active tracker has open work
            env = {**os.environ, "MLH_CHAPTERS_DIR": str(ch_dir), "MLH_KG_DIR": str(kg_dir)}
            ss = run_cli(["node", str(ROOT / ".agents" / "hooks" / "session-start.mjs")], stdin_text="{}", env=env)
            self.assertEqual(ss.returncode, 0, ss.stderr)
            msg = json.loads(ss.stdout)["injectSteps"][0]["systemMessage"]["systemMessage"]
            self.assertIn("Active Chapter Autopilot:", msg)
            self.assertIn("Simple Harmonic Motion", msg)
            self.assertIn("Autopilot Next:", msg)

            # 8. Master ex1-q1 -> build_revision_sheet -> run_cbt_mock -> chapter_complete
            _ct(["mark-question", "shm", "ex1-q1", "--status", "mastered", *base])
            self.assertEqual(_ct(["next", "shm", *base])["action"], "build_revision_sheet")
            _ct(["set-milestone", "shm", "revisionSheetBuilt", "--value", "true", *base])
            self.assertEqual(_ct(["next", "shm", *base])["action"], "run_cbt_mock")
            _ct(["set-milestone", "shm", "cbtMockPassed", "--value", "true", *base])
            self.assertEqual(_ct(["next", "shm", *base])["action"], "chapter_complete")


class TestUniversalAutopilot(unittest.TestCase):
    """20. Subject-agnostic Autopilot, set-scope, SYNT_*>MLH_* env precedence & CS/ML graph_intel."""

    def test_arbitrary_subjects_scope_charter_env_precedence_and_cs_ml_themes(self) -> None:
        ct = load_script_module("chapter_tracker.py")
        gi = load_script_module("graph_intel.py")

        def _ct(argv: list[str]) -> dict[str, Any]:
            buf = _EncodedStringIO()
            with contextlib.redirect_stdout(buf):
                ct.main(argv)
            return json.loads(buf.getvalue())

        with tempfile.TemporaryDirectory(prefix="synt-univ-ap-") as tmp:
            t = Path(tmp)
            ch_dir = t / "chapters"
            kg_dir = t / "kg"
            ch_dir.mkdir()
            kg_dir.mkdir()

            # 1. Arbitrary non-JEE subjects initialize cleanly
            for slug, title, subj in (
                ("cs-graphs", "Graph Algorithms", "Computer Science"),
                ("ml-transformers", "Transformer Architectures", "Machine Learning"),
                ("rust-ownership", "Ownership & Lifetimes", "Rust"),
                ("dist-raft", "Raft Consensus", "Distributed Systems"),
            ):
                res = _ct([
                    "init", slug, "--title", title, "--subject", subj,
                    "--chapters-dir", str(ch_dir), "--kg-dir", str(kg_dir),
                    "--no-sync", "--json",
                ])
                self.assertEqual(res["tracker"]["subject"], subj)

            # 2. `set-scope` sets Scope Charter fields cleanly
            sc_res = _ct([
                "set-scope", "dist-raft",
                "--goal-profile", "coding",
                "--target-depth", "mastery",
                "--allow-web-research", "true",
                "--in-scope", "Leader election,Log replication",
                "--out-of-scope", "Byzantine consensus",
                "--chapters-dir", str(ch_dir), "--kg-dir", str(kg_dir),
                "--no-sync", "--json",
            ])
            sc = sc_res["scopeCharter"]
            self.assertEqual(sc["goalProfile"], "coding")
            self.assertEqual(sc["targetDepth"], "mastery")
            self.assertTrue(sc["allowWebResearch"])
            self.assertEqual(sc["inScope"], ["Leader election", "Log replication"])
            self.assertEqual(sc["outOfScope"], ["Byzantine consensus"])

            # 3. Expanded source kinds (`code_repo`, `paper_pdf`, `web_research`) + level aliases (`foundations`/`core`/`mastery`)
            for sid, label, path_val, kind in (
                ("repo", "Raft Reference Repo", "sources/raft-repo", "code_repo"),
                ("paper", "Ongaro & Ousterhout Raft Paper", "sources/raft.pdf", "paper_pdf"),
                ("web", "Curated Notes", "https://raft.github.io", "web_research"),
            ):
                add_s = _ct([
                    "add-source", "dist-raft",
                    "--id", sid, "--label", label, "--path", path_val, "--kind", kind,
                    "--chapters-dir", str(ch_dir), "--kg-dir", str(kg_dir),
                    "--no-sync", "--json",
                ])
                self.assertEqual(add_s["source"]["id"], sid)
                self.assertEqual(add_s["source"]["kind"], kind)

            topics_payload = json.dumps([
                {"id": "t-f", "order": 1, "title": "State Machine Basics", "level": "foundations"},
                {"id": "t-c", "order": 2, "title": "Leader Election", "level": "core"},
                {"id": "t-m", "order": 3, "title": "Log Safety Proof", "level": "mastery"},
            ])
            _ct([
                "add-topics", "dist-raft", "--json-data", topics_payload,
                "--chapters-dir", str(ch_dir), "--kg-dir", str(kg_dir),
                "--no-sync", "--json",
            ])
            dist_tracker = ct.load_tracker("dist-raft", ch_dir)
            levels_by_id = {tp["id"]: tp["level"] for tp in dist_tracker["topics"]}
            self.assertEqual(levels_by_id, {"t-f": "basics", "t-c": "main", "t-m": "advanced"})

            # 4. Profile-aware milestone labels: coding/CS -> "timed mastery assessment" vs JEE -> "timed NTA JEE CBT"
            for flag in ("sourcesIndexed", "probed", "revisionSheetBuilt"):
                _ct([
                    "set-milestone", "dist-raft", flag, "--value", "true",
                    "--chapters-dir", str(ch_dir), "--kg-dir", str(kg_dir),
                    "--no-sync", "--json",
                ])
            for tid in ("t-f", "t-c", "t-m"):
                _ct([
                    "set-topic", "dist-raft", tid, "--status", "mastered",
                    "--chapters-dir", str(ch_dir), "--kg-dir", str(kg_dir),
                    "--no-sync", "--json",
                ])
            cs_next = _ct(["next", "dist-raft", "--chapters-dir", str(ch_dir), "--kg-dir", str(kg_dir), "--json"])
            self.assertIn("timed mastery assessment", cs_next["summary"].lower())
            self.assertNotIn("nta jee", cs_next["summary"].lower())

            # Compare against a JEE-profile tracker at the identical milestone
            _ct([
                "init", "jee-rot", "--title", "Rotational Motion", "--subject", "Physics",
                "--chapters-dir", str(ch_dir), "--kg-dir", str(kg_dir),
                "--no-sync", "--json",
            ])
            _ct([
                "set-scope", "jee-rot", "--goal-profile", "jee",
                "--chapters-dir", str(ch_dir), "--kg-dir", str(kg_dir),
                "--no-sync", "--json",
            ])
            for flag in ("sourcesIndexed", "probed", "revisionSheetBuilt"):
                _ct([
                    "set-milestone", "jee-rot", flag, "--value", "true",
                    "--chapters-dir", str(ch_dir), "--kg-dir", str(kg_dir),
                    "--no-sync", "--json",
                ])
            jee_next = _ct(["next", "jee-rot", "--chapters-dir", str(ch_dir), "--kg-dir", str(kg_dir), "--json"])
            self.assertIn("nta jee cbt", jee_next["summary"].lower())

            # 4b. University STEM subject-only inference guard (#105 P1.2) + CS mistake categories & aliases (#105 P1.1)
            mit_init = _ct([
                "init", "mit-qm", "--title", "MIT 8.04 Quantum Mechanics", "--subject", "Physics",
                "--chapters-dir", str(ch_dir), "--kg-dir", str(kg_dir),
                "--no-sync", "--json",
            ])
            self.assertEqual(mit_init["tracker"]["scopeCharter"]["goalProfile"], "general")
            for flag in ("sourcesIndexed", "probed", "revisionSheetBuilt"):
                _ct([
                    "set-milestone", "mit-qm", flag, "--value", "true",
                    "--chapters-dir", str(ch_dir), "--kg-dir", str(kg_dir),
                    "--no-sync", "--json",
                ])
            mit_next = _ct(["next", "mit-qm", "--chapters-dir", str(ch_dir), "--kg-dir", str(kg_dir), "--json"])
            self.assertEqual(mit_next["assessmentKind"], "mastery_assessment")
            self.assertIn("timed mastery assessment", mit_next["summary"].lower())
            self.assertNotIn("jee", mit_next["summary"].lower())
            _ct([
                "add-topics", "mit-qm",
                "--json-data", json.dumps([{"id": "qm-1", "order": 1, "title": "Wavefunctions", "level": "foundations"}]),
                "--chapters-dir", str(ch_dir), "--kg-dir", str(kg_dir), "--no-sync", "--json",
            ])
            _ct([
                "add-questions", "mit-qm",
                "--json-data", json.dumps([
                    {"qid": "q-mut", "label": "PSet 1 Q1", "topicId": "qm-1", "level": "core"},
                    {"qid": "q-tle", "label": "PSet 1 Q2", "topicId": "qm-1", "level": "core"},
                ]),
                "--chapters-dir", str(ch_dir), "--kg-dir", str(kg_dir), "--no-sync", "--json",
            ])
            mq_mut = _ct([
                "mark-question", "mit-qm", "q-mut", "--status", "retest",
                "--mistake-category", "state_mutation_bug",
                "--chapters-dir", str(ch_dir), "--kg-dir", str(kg_dir), "--no-sync", "--json",
            ])
            self.assertEqual(mq_mut["question"]["lastMistakeCategory"], "state_mutation_bug")
            mq_tle = _ct([
                "mark-question", "mit-qm", "q-tle", "--status", "retest",
                "--mistake-category", "tle",
                "--chapters-dir", str(ch_dir), "--kg-dir", str(kg_dir), "--no-sync", "--json",
            ])
            self.assertEqual(mq_tle["question"]["lastMistakeCategory"], "complexity_trap")

            # 5. SYNT_* > MLH_* environment variable precedence
            synt_ch = t / "env-synt-ch"
            mlh_ch = t / "env-mlh-ch"
            synt_kg = t / "env-synt-kg"
            mlh_kg = t / "env-mlh-kg"
            for d in (synt_ch, mlh_ch, synt_kg, mlh_kg):
                d.mkdir()
            with unittest.mock.patch.dict(
                os.environ,
                {
                    "SYNT_CHAPTERS_DIR": str(synt_ch),
                    "MLH_CHAPTERS_DIR": str(mlh_ch),
                    "SYNT_KG_DIR": str(synt_kg),
                    "MLH_KG_DIR": str(mlh_kg),
                },
                clear=False,
            ):
                resolved_ch, _ = ct.resolve_chapters_dir(None)
                resolved_kg, _ = ct.resolve_kg_dir(None)
                self.assertEqual(resolved_ch, synt_ch.resolve())
                self.assertEqual(resolved_kg, synt_kg.resolve())

            # 6. `graph_intel.py` CS/ML structural theme detection across chapters
            cs_ml_props = [
                {
                    "id": "cs-dijkstra-greedy",
                    "subject": "Computer Science",
                    "chapter": "Graph Algorithms",
                    "statement": "Dijkstra shortest path uses a greedy priority heap when edge weights are non-negative.",
                    "status": "solid",
                    "tags": ["graphs", "shortest-path"],
                },
                {
                    "id": "cs-dp-shortest",
                    "subject": "Computer Science",
                    "chapter": "Dynamic Programming",
                    "statement": "Bellman-Ford relaxes shortest path subproblems via dynamic programming and memoization.",
                    "status": "solid",
                    "tags": ["dp", "shortest-path"],
                },
                {
                    "id": "ml-sgd-backprop",
                    "subject": "Machine Learning",
                    "chapter": "Deep Learning",
                    "statement": "Backprop computes gradient descent updates for softmax cross-entropy loss.",
                    "status": "solid",
                    "tags": ["optimization"],
                },
                {
                    "id": "ml-convex-reg",
                    "subject": "Machine Learning",
                    "chapter": "Statistical Learning",
                    "statement": "Convex regularization bounds generalization error and bias-variance tradeoff under gradient descent.",
                    "status": "solid",
                    "tags": ["optimization"],
                },
            ]
            (kg_dir / "propositions.jsonl").write_text(
                "\n".join(json.dumps(p) for p in cs_ml_props) + "\n",
                encoding="utf-8",
            )
            (kg_dir / "chains.jsonl").write_text("", encoding="utf-8")
            (kg_dir / "mistakes.jsonl").write_text("", encoding="utf-8")

            buf_gi = _EncodedStringIO()
            with contextlib.redirect_stdout(buf_gi):
                gi.main(["--combos", "--kg-dir", str(kg_dir), "--json"])
            audit = json.loads(buf_gi.getvalue())
            all_shared_themes = {
                th
                for combo in audit.get("combos", [])
                for th in combo.get("sharedThemes", [])
            }
            self.assertIn("algorithms_complexity", all_shared_themes)
            self.assertIn("ml_optimization", all_shared_themes)


class TestLearnerPsychologyAndBrainstem(unittest.TestCase):
    """29. Syntopik v3.0 3-Cadence Learner Psychology, Causal Root-Cause, 4-Tier Evidence, Brainstem & Zero-PII Packs."""

    def test_tier1_tier2_tier3_all_six_affects_expertise_reversal_and_overrides(self) -> None:
        import syntopik.learner_model as lm

        with tempfile.TemporaryDirectory(prefix="synt-v3-psych-") as tmp:
            kg = Path(tmp) / "knowledge-graph"
            kg.mkdir(parents=True)

            # 1. Tier-1 Onboarding Calibration: zero VARK, prior decay 6/(6+N), prose cap <= 155w
            prof = lm.load_profile(kg)
            self.assertNotIn("vark", json.dumps(prof).lower())
            self.assertNotIn("learningStyle", json.dumps(prof))

            prof = lm.apply_tier1_calibration(
                prof,
                entry_ramp="intuition_first",
                challenge_appetite="adversarial",
                domain_fluency={"Physics": 1, "Computer Science": 3},
                session_minutes=30,
                prose_density="rich",
                micro_task_results=[
                    {"correct": False, "confidence": 0.90, "duration_ms": 14000},
                    {"correct": False, "confidence": 0.85, "duration_ms": 16000},
                ],
            )
            lm.save_profile(kg, prof)
            self.assertNotIn("vark", json.dumps(prof).lower())
            self.assertTrue(prof["calibration"]["completed"])
            self.assertEqual(prof["calibration"]["targetSuccessRate"], 0.78)
            self.assertLessEqual(prof["calibration"]["maxProseWordsPrior"], 155)
            self.assertEqual(prof["calibration"]["maxProseWordsPrior"], 155)
            self.assertEqual(prof["calibration"]["calibrationPrior"], "overconfident")
            self.assertAlmostEqual(prof["globalStats"]["priorWeight"], 1.0, places=4)
            self.assertEqual(prof["domains"]["Physics"]["elo"], 1350.0)
            self.assertEqual(prof["domains"]["Computer Science"]["elo"], 1750.0)

            # 2. Tier-2 Adaptive Placement: KG `solid` bypass (R >= 0.80), Pelanek Elo & 2-probe early stops
            kg_props = [
                {"id": "p-solid-high", "status": "solid", "bktMastery": 0.92, "retrievability": 0.91},
                {"id": "p-weak-1", "status": "shaky", "bktMastery": 0.40},
                {"id": "p-weak-2", "status": "planned"},
                {"id": "p-adv-1", "status": "planned"},
            ]
            cands = [
                {"id": "p-solid-high", "difficulty": 1350},
                {"id": "p-weak-1", "difficulty": 1200},
                {"id": "p-weak-2", "difficulty": 1400},
                {"id": "p-adv-1", "difficulty": 1750},
            ]
            sel = lm.select_next_placement_probe(prof, "Physics", "shm", cands, kg_props=kg_props, chains=[])
            self.assertFalse(sel["done"])
            self.assertIn("p-solid-high", sel["bypassedIds"])
            self.assertNotEqual(sel["probe"]["id"], "p-solid-high")

            # Lucky guess & hypercorrection scoring + 2-probe early stop (both_basics_failed)
            p_nov = lm.load_profile(kg)
            r1 = lm.score_placement_probe(
                p_nov, "Physics", "chap-fail", difficulty=1150, correct=False, confidence=0.85, duration_ms=15000, probe_index=1
            )
            self.assertTrue(r1["hypercorrection"])
            self.assertLess(r1["newTheta"], r1["oldTheta"])
            self.assertFalse(r1["done"])
            r2 = lm.score_placement_probe(
                p_nov, "Physics", "chap-fail", difficulty=1200, correct=False, confidence=0.60, duration_ms=18000, probe_index=2
            )
            self.assertTrue(r2["done"])
            self.assertTrue(r2["earlyStop"])
            self.assertEqual(r2["earlyStopReason"], "both_basics_failed")

            # 2-probe early stop (both_advanced_aced)
            p_adv = lm.load_profile(kg)
            a1 = lm.score_placement_probe(
                p_adv, "Computer Science", "chap-ace", difficulty=1750, correct=True, confidence=0.90, duration_ms=25000, probe_index=1
            )
            self.assertGreater(a1["newTheta"], a1["oldTheta"])
            a2 = lm.score_placement_probe(
                p_adv, "Computer Science", "chap-ace", difficulty=1800, correct=True, confidence=0.92, duration_ms=22000, probe_index=2
            )
            self.assertTrue(a2["done"])
            self.assertTrue(a2["earlyStop"])
            self.assertEqual(a2["earlyStopReason"], "both_advanced_aced")

            # 3. All 6 sensor-free affective states via classify_affective_state
            base_t = datetime(2026, 11, 26, 10, 0, 0, tzinfo=timezone.utc)
            def _mk_ev(mins: float, ok: bool, lat: float = 1.0, hints: int = 0, skipped: bool = False) -> dict[str, Any]:
                return {
                    "ts": (base_t + timedelta(minutes=mins)).isoformat().replace("+00:00", "Z"),
                    "correct": ok,
                    "latencyRatio": lat,
                    "hintsUsed": hints,
                    "skipped": skipped,
                    "durationMs": int(20000 * lat),
                }

            st_flow = lm.classify_affective_state(
                [_mk_ev(0, True, 1.0, 0), _mk_ev(2, True, 0.95, 0), _mk_ev(4, False, 1.05, 1), _mk_ev(6, True, 1.0, 0)],
                session_target_min=30,
            )
            self.assertEqual(st_flow["state"], "flow")

            st_pc = lm.classify_affective_state(
                [_mk_ev(0, True, 1.4, 1), _mk_ev(3, False, 1.6, 1), _mk_ev(6, True, 1.5, 0)],
                session_target_min=30,
            )
            self.assertEqual(st_pc["state"], "productive_confusion")

            st_ov = lm.classify_affective_state(
                [_mk_ev(0, True, 1.2, 1), _mk_ev(3, False, 2.2, 3), _mk_ev(6, False, 2.5, 3), _mk_ev(9, False, 2.4, 3)],
                session_target_min=30,
            )
            self.assertEqual(st_ov["state"], "overloaded_frustrated")

            st_bo = lm.classify_affective_state(
                [_mk_ev(i * 2, True, 0.50, 0) for i in range(5)],
                session_target_min=30,
            )
            self.assertEqual(st_bo["state"], "bored_underchallenged")

            st_gm = lm.classify_affective_state(
                [_mk_ev(0, False, 0.20, 0), _mk_ev(1, False, 0.22, 0), _mk_ev(2, True, 0.25, 3)],
                session_target_min=30,
            )
            self.assertEqual(st_gm["state"], "gaming_rushing")

            st_ft = lm.classify_affective_state(
                [
                    _mk_ev(0, True, 0.9, 0),
                    _mk_ev(10, True, 0.9, 0),
                    _mk_ev(20, True, 1.0, 0),
                    _mk_ev(32, False, 1.9, 1),
                    _mk_ev(42, False, 2.0, 1),
                    _mk_ev(50, True, 2.1, 1),
                ],
                session_target_min=30,
            )
            self.assertEqual(st_ft["state"], "fatigued")

            # 4. Kalyuga Expertise-Reversal scaffolding transitions (novice vs advanced)
            nov_prof = lm.load_profile(kg)
            nov_prof["domains"]["Physics"]["elo"] = 1120.0
            nov_prof["affectiveState"] = {"state": "flow"}
            pol_nov = lm.compile_teaching_policy(nov_prof, domain="Physics", node_bkt=0.18)
            self.assertEqual(pol_nov["scaffoldingMode"], "worked_example_parsons")
            self.assertTrue(pol_nov["allowWorkedExample"])
            self.assertFalse(pol_nov["allowProductiveFailure"])

            adv_prof = lm.load_profile(kg)
            adv_prof["domains"]["Computer Science"]["elo"] = 1820.0
            adv_prof["affectiveState"] = {"state": "flow"}
            pol_adv = lm.compile_teaching_policy(adv_prof, domain="Computer Science", node_bkt=0.92)
            self.assertEqual(pol_adv["scaffoldingMode"], "challenge_transfer")
            self.assertFalse(pol_adv["allowWorkedExample"])
            self.assertTrue(pol_adv["allowProductiveFailure"])

            # 5. Tier-3 telemetry, prior decay 6/(6+N), Failure DNA, 20-event FIFO cap (< 8 KB), overrides & 2-line summary
            mistakes_path = kg / "mistakes.jsonl"
            mistakes_path.write_text(
                "\n".join(json.dumps({"id": f"m-{i}", "category": "sign_error", "chapter": "SHM"}) for i in range(4)) + "\n",
                encoding="utf-8",
            )
            for idx in range(25):
                ev_t = base_t + timedelta(minutes=idx * 2)
                lm.record_telemetry_event(
                    kg,
                    domain="Physics",
                    prop_id=f"p-{idx % 4}",
                    kind="review",
                    correct=(idx % 3 != 0),
                    difficulty=1450.0,
                    confidence=0.75,
                    duration_ms=16000,
                    hints_used=0 if idx % 3 != 0 else 1,
                    mistake_category="sign_error" if idx % 3 == 0 else None,
                    now=ev_t,
                )

            after_prof = lm.load_profile(kg)
            self.assertEqual(after_prof["globalStats"]["totalEvents"], 25)
            self.assertAlmostEqual(after_prof["globalStats"]["priorWeight"], 6.0 / (6.0 + 25.0), places=3)
            self.assertLessEqual(len(after_prof["recentEvents"]), 20)
            self.assertEqual(after_prof["failureDna"]["dominantTier"], "process_encoding")
            self.assertGreaterEqual(after_prof["failureDna"]["tierShares"]["process_encoding"], 0.50)

            prof_bytes = (kg / lm.PROFILE_FILENAME).stat().st_size
            self.assertLess(prof_bytes, 8192, f"learner-profile.json exceeded 8 KB FIFO cap ({prof_bytes} B)")

            ov_res = lm.apply_user_overrides(
                kg,
                {"scaffoldingMode": "faded", "allowProductiveFailure": False, "maxProseWords": 110},
            )
            ov_pol = lm.compile_teaching_policy(ov_res, domain="Physics")
            self.assertEqual(ov_pol["scaffoldingMode"], "faded")
            self.assertFalse(ov_pol["allowProductiveFailure"])
            self.assertEqual(ov_pol["maxProseWords"], 110)

            lines = lm.format_compact_policy_lines(ov_res, domain="Physics")
            self.assertEqual(len(lines), 2)
            self.assertLessEqual(sum(len(ln.split()) for ln in lines), 55)

    def test_root_cause_evidence_ledger_brainstem_and_packs_zero_pii(self) -> None:
        import syntopik.brainstem as bs
        import syntopik.evidence as ev
        import syntopik.forks as fk
        import syntopik.learner_model as lm
        import syntopik.packs as pk
        import syntopik.root_cause as rc
        import syntopik.whiteboard as wb

        with tempfile.TemporaryDirectory(prefix="synt-v3-core-") as tmp:
            ws = Path(tmp) / "ws"
            kg = ws / "knowledge-graph"
            ch = ws / "chapters"
            kg.mkdir(parents=True)
            ch.mkdir(parents=True)

            # Seed multi-chapter DAG: p-root (shaky) + p-solid (solid) -> p-mid (solid) -> p-leaf (shaky) & p-other (shaky, Ch2)
            props = [
                {"id": "p-root", "subject": "Physics", "chapter": "Ch1 Kinematics", "title": "Vector sign convention", "kind": "axiom", "status": "shaky", "bktMastery": 0.35},
                {"id": "p-solid", "subject": "Physics", "chapter": "Ch1 Kinematics", "title": "Conservation of mass", "kind": "axiom", "status": "solid", "bktMastery": 0.94, "fsrs": {"stability": 30.0, "difficulty": 4.0, "lastReview": "2026-11-25T10:00:00Z"}},
                {"id": "p-mid", "subject": "Physics", "chapter": "Ch2 Dynamics", "title": "Restoring force law F = -kx", "kind": "derivation", "status": "solid", "bktMastery": 0.88, "fsrs": {"stability": 20.0, "difficulty": 4.5, "lastReview": "2026-11-25T10:00:00Z"}},
                {"id": "p-leaf", "subject": "Physics", "chapter": "Ch3 SHM", "title": "SHM phase equation", "kind": "theorem", "status": "shaky", "bktMastery": 0.42},
                {"id": "p-other", "subject": "Physics", "chapter": "Ch2 Dynamics", "title": "Spring energy potential", "kind": "theorem", "status": "shaky", "bktMastery": 0.45},
            ]
            chains = [
                {"premises": ["p-root", "p-solid"], "conclusion": "p-mid", "relation": "derives", "why": "Hooke restoring force"},
                {"premises": ["p-mid"], "conclusion": "p-leaf", "relation": "derives", "why": "Solve ODE for SHM"},
                {"premises": ["p-root"], "conclusion": "p-other", "relation": "requires", "why": "Sign of work"},
            ]
            (kg / "propositions.jsonl").write_text("\n".join(json.dumps(p) for p in props) + "\n", encoding="utf-8")
            (kg / "chains.jsonl").write_text("\n".join(json.dumps(c) for c in chains) + "\n", encoding="utf-8")
            (kg / "mistakes.jsonl").write_text(
                json.dumps({"id": "m-1", "propId": "p-leaf", "chapter": "Ch3 SHM", "category": "sign_error"}) + "\n",
                encoding="utf-8",
            )

            # 1. root_cause.py: Noisy-OR non-destructive suspect scoring, Delta-H probe, cross-chapter blast radius
            before_props_raw = (kg / "propositions.jsonl").read_text(encoding="utf-8")
            loc = rc.localize_fault(kg, "p-leaf")
            self.assertTrue(loc["ok"])
            self.assertTrue(loc["nonDestructive"])
            self.assertEqual(loc["topSuspect"]["propId"], "p-root")
            self.assertGreater(loc["topSuspect"]["suspectRootScore"], 0.4)
            # Solid nodes MUST remain untouched on disk
            self.assertEqual((kg / "propositions.jsonl").read_text(encoding="utf-8"), before_props_raw)

            probe = rc.select_diagnostic_probe(kg, "p-leaf")
            self.assertTrue(probe["ok"])
            self.assertIsNotNone(probe["bestProbe"])
            self.assertGreater(probe["bestProbe"]["deltaHBits"], 0.0)

            blast = rc.find_blast_radius(kg, limit=5)
            self.assertGreaterEqual(len(blast), 1)
            self.assertEqual(blast[0]["propId"], "p-root")
            self.assertGreaterEqual(len(blast[0]["affectedChapters"]), 2)

            # 2. evidence.py: 4-tier T1..T4 inference, SHA-256 hash chain + tamper catch, anti-inflation, zero-PII/zero-CDN dossier
            synthetic_local_path = "/" + "Users" + "/alice/private/lecture.pdf"
            ev.record_evidence(ws, "p-root", 1, "manual", detail=f"Read {synthetic_local_path}")
            ev.record_evidence(ws, "p-solid", 2, "fsrs_review", detail="Cold recall 14d")
            ev.record_evidence(ws, "p-other", 3, "fork_replay", detail="Woodpecker cleared")
            ev.record_evidence(ws, "p-leaf", 4, "viva_chamber", detail="5-turn viva pass")

            chk_ok = ev.verify_ledger_chain(kg)
            self.assertTrue(chk_ok["ok"], chk_ok)
            self.assertEqual(chk_ok["count"], 4)
            self.assertIsNone(chk_ok["brokenAt"])

            summary = ev.compute_evidence_summary(ws)
            self.assertTrue(summary["ok"])
            self.assertEqual(summary["propositions"]["p-root"]["tier"], 1)
            self.assertEqual(summary["propositions"]["p-solid"]["tier"], 2)
            self.assertEqual(summary["propositions"]["p-other"]["tier"], 3)
            self.assertEqual(summary["propositions"]["p-leaf"]["tier"], 4)
            # Anti-inflation gate catches p-mid / p-root bottlenecks stuck at T1/T2
            alert_ids = {a["propId"] for a in summary["antiInflationAlerts"]}
            self.assertIn("p-root", alert_ids)
            self.assertIn("p-mid", alert_ids)

            dossier_html = Path(tmp) / "mastery-dossier.html"
            dos_res = ev.export_mastery_dossier(ws, dossier_html)
            self.assertTrue(dos_res["ok"])
            self.assertTrue(dossier_html.is_file())
            dos_txt = dossier_html.read_text(encoding="utf-8")
            self.assertNotIn("alice", dos_txt)
            self.assertIsNone(re.search(r"<(?:script|link)\b[^>]*\b(?:src|href)\s*=\s*['\"]https?://", dos_txt, re.I))
            self.assertIn(chk_ok["headHash"][:12], dos_txt)

            # Tamper entry #2 and assert verify_ledger_chain catches brokenAt == 2
            ledger_file = kg / ev.LEDGER_FILENAME
            l_lines = ledger_file.read_text(encoding="utf-8").splitlines()
            tampered = json.loads(l_lines[1])
            tampered["tier"] = 4
            l_lines[1] = json.dumps(tampered)
            ledger_file.write_text("\n".join(l_lines) + "\n", encoding="utf-8")
            chk_bad = ev.verify_ledger_chain(kg)
            self.assertFalse(chk_bad["ok"])
            self.assertEqual(chk_bad["brokenAt"], 2)

            # 3. brainstem.py: priority hierarchy + <= 180-token envelope + drop_sources Teacher-First spine
            ws_b = Path(tmp) / "ws-brainstem"
            kg_b = ws_b / "knowledge-graph"
            ch_b = ws_b / "chapters"
            kg_b.mkdir(parents=True)
            ch_b.mkdir(parents=True)
            (kg_b / "propositions.jsonl").write_text(
                json.dumps({"id": "b-node", "subject": "CS", "chapter": "Raft", "title": "Leader election", "status": "solid", "nextReview": "2099-01-01", "fsrs": {"stability": 30.0}}) + "\n",
                encoding="utf-8",
            )
            (kg_b / "chains.jsonl").write_text("", encoding="utf-8")
            (ch_b / "raft-tracker.json").write_text(
                json.dumps({
                    "schemaVersion": 1, "slug": "raft", "title": "Raft Consensus", "subject": "CS", "active": True,
                    "topics": [{"id": "t1", "order": 1, "title": "Log replication", "level": "basics", "prereqTopicIds": [], "propIds": ["b-node"], "status": "pending"}],
                }),
                encoding="utf-8",
            )

            # (a) onboarding_calibration when total_events == 0 & uncalibrated
            m_cal = bs.plan_next_move(ws_b)
            self.assertEqual(m_cal["recommendedMode"], "onboarding_calibration")
            self.assertLessEqual(len(re.findall(r"\S+", json.dumps(m_cal))), 180)

            # (b) whiteboard_pin_answer overrides everything
            wb.push_frame(kg_b, {"kind": "derivation_step", "title": "Step 1", "body": "Quorum intersection"})
            pin_r = wb.pin_step(kg_b, 0, "Why strict majority?")
            m_pin = bs.plan_next_move(ws_b)
            self.assertEqual(m_pin["recommendedMode"], "whiteboard_pin_answer")
            self.assertLessEqual(len(re.findall(r"\S+", json.dumps(m_pin))), 180)
            wb.resolve_pin(kg_b, pin_r["pin"]["id"], resolution="Answered")

            # (c) cold_start_reentry when calibrated & >= 4 days away
            p_b = lm.load_profile(kg_b)
            p_b = lm.apply_tier1_calibration(p_b, entry_ramp="balanced", challenge_appetite="balanced")
            lm.save_profile(kg_b, p_b)
            old_ts = (datetime.now(timezone.utc) - timedelta(days=6)).isoformat().replace("+00:00", "Z")
            (kg_b / "revlog.jsonl").write_text(
                json.dumps({"ts": old_ts, "propId": "b-node", "rating": "good", "correct": True}) + "\n",
                encoding="utf-8",
            )
            m_cold = bs.plan_next_move(ws_b)
            self.assertEqual(m_cold["recommendedMode"], "cold_start_reentry")
            self.assertLessEqual(len(re.findall(r"\S+", json.dumps(m_cold))), 180)

            # (d) fork_replay when recent activity & due fork exists
            now_ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            (kg_b / "revlog.jsonl").write_text(
                json.dumps({"ts": now_ts, "propId": "b-node", "rating": "good", "correct": True}) + "\n",
                encoding="utf-8",
            )
            fk.capture_fork(
                prop_id="b-node",
                prefix_steps=["Split brain partition occurs"],
                fork_prompt="Minimum nodes to elect leader?",
                sealed_blunder="N/2",
                sealed_winning_move="floor(N/2)+1",
                refutation_msg="N/2 allows two disjoint quorums.",
                kg_dir=kg_b,
                workspace=ws_b,
            )
            m_fork = bs.plan_next_move(ws_b)
            self.assertEqual(m_fork["recommendedMode"], "fork_replay")
            self.assertLessEqual(len(re.findall(r"\S+", json.dumps(m_fork))), 180)

            # (e) teach_frontier when forks cleared & nothing due
            (kg_b / "forks.jsonl").write_text("", encoding="utf-8")
            m_front = bs.plan_next_move(ws_b)
            self.assertEqual(m_front["recommendedMode"], "teach_frontier")
            self.assertLessEqual(len(re.findall(r"\S+", json.dumps(m_front))), 180)

            # (f) drop_sources Teacher-First spine classification
            drop_dir = Path(tmp) / "drops"
            drop_dir.mkdir(parents=True)
            lec_md = drop_dir / "lecture_01_notes.md"
            lec_md.write_text("# Consensus Safety\nMajority quorums always intersect.\n", encoding="utf-8")
            pyq_md = drop_dir / "raft_pyq_problems.md"
            pyq_md.write_text("# Practice Problems\nQ1: 5-node cluster.\n", encoding="utf-8")
            drop_res = bs.drop_sources(ws_b, [pyq_md, lec_md], slug="raft-drop")
            self.assertTrue(drop_res["ok"])
            self.assertEqual(drop_res["spineSource"]["role"], "teacher_spine")
            self.assertIn("lecture_01_notes.md", drop_res["spineSource"]["input"])
            roles_by_name = {Path(c["input"]).name: c["role"] for c in drop_res["classifiedSources"]}
            self.assertEqual(roles_by_name["lecture_01_notes.md"], "teacher_spine")
            self.assertEqual(roles_by_name["raft_pyq_problems.md"], "problem_bank")
            self.assertTrue(drop_res["probeOneHint"])

            # 4. packs.py: fail-closed exclusion & smuggle rejection of all v3 personal files
            personal_files = (
                "learner-profile.json",
                "whiteboard-session.json",
                "evidence-ledger.jsonl",
                "viva-sessions.jsonl",
            )
            for pf in personal_files:
                self.assertIn(pf, pk.EXCLUDED_PERSONAL_FILES)
                (kg / pf).write_text('{"secretPersonal": true}\n', encoding="utf-8")

            pack_zip = Path(tmp) / "clean-v3.syntopik-pack.zip"
            exp_r = pk.export_pack("Physics", workspace=ws, kg_dir=kg, chapters_dir=ch, out_path=pack_zip)
            self.assertTrue(exp_r["ok"])
            with zipfile.ZipFile(pack_zip, "r") as zf:
                names = set(zf.namelist())
                for pf in personal_files:
                    self.assertNotIn(pf, names)
                    self.assertFalse(any(n.endswith("/" + pf) for n in names))
            ver_clean = pk.verify_pack(pack_zip)
            self.assertTrue(ver_clean["ok"], ver_clean)

            # Craft smuggled ZIPs for each personal file -> verify_pack & import_pack MUST reject!
            for pf in personal_files:
                with self.subTest(smuggled=pf):
                    bad_zip = Path(tmp) / f"smuggle-{pf}.zip"
                    with zipfile.ZipFile(pack_zip, "r") as src_z, zipfile.ZipFile(bad_zip, "w") as dst_z:
                        for info in src_z.infolist():
                            dst_z.writestr(info, src_z.read(info.filename))
                        dst_z.writestr(f"knowledge-graph/{pf}", '{"smuggled": true}\n')
                    ver_bad = pk.verify_pack(bad_zip)
                    self.assertFalse(ver_bad["ok"], f"verify_pack failed to reject smuggled {pf}")
                    imp_dir = Path(tmp) / f"imp-{pf}"
                    imp_bad = pk.import_pack(bad_zip, workspace=imp_dir, no_sync=True)
                    self.assertFalse(imp_bad["ok"], f"import_pack failed to reject smuggled {pf}")
                    self.assertTrue(imp_bad.get("errors"), imp_bad)
                    self.assertFalse((imp_dir / "knowledge-graph" / pf).exists())


class TestV3PedagogyAndWorkMentor(unittest.TestCase):
    """30. Syntopik v3.0 Explorable RPN Sandbox, Derivation Forge, Viva Chamber, Work Mentor, Sunset, Auto-Pacer & Whiteboard."""

    def test_explorable_parser_rpn_curves_limit_check_and_ast_sandbox(self) -> None:
        import syntopik.explorable as ex

        md = """
# Small-Angle Regime Break

:::explore pendulum-limit
{
  "title": "Pendulum Small-Angle Approximation",
  "prompt": "Drag theta to see where sin(theta) diverges from theta.",
  "params": [
    {"name": "A", "label": "Amplitude", "min": 0.1, "max": 2.0, "step": 0.1, "default": 1.0}
  ],
  "outputs": [
    {"label": "Peak Restoring Ratio", "expr": "sin(A) / A"}
  ],
  "curve": {
    "xVar": "theta",
    "xMin": 0.05,
    "xMax": 1.5,
    "samples": 32,
    "exactExpr": "A * sin(theta)",
    "approxExpr": "A * theta",
    "regimeThreshold": 0.25,
    "yLabel": "Torque"
  },
  "limitCheck": {
    "question": "As theta -> 0, what is cos(theta)?",
    "var": "theta",
    "target": 0,
    "expectedExpr": "cos(theta)"
  }
}
:::
"""
        specs = ex.parse_explorable_blocks(md, strict=True)
        self.assertEqual(len(specs), 1)
        spec = specs[0]
        self.assertEqual(spec["id"], "pendulum-limit")
        self.assertTrue(spec["outputs"][0]["rpn"])
        val = ex.evaluate_rpn(spec["outputs"][0]["rpn"], {"A": 1.0})
        self.assertAlmostEqual(val, math.sin(1.0), places=6)

        curve = ex.sample_explorable_curve(spec, {"A": 2.0})
        self.assertTrue(curve["ok"])
        self.assertEqual(len(curve["points"]), 32)
        self.assertGreater(curve["maxAbsError"], 0.5)

        lim_pass = ex.evaluate_limit_check(spec, "1.0")
        self.assertTrue(lim_pass["passed"])
        lim_fail = ex.evaluate_limit_check(spec, "0.2")
        self.assertFalse(lim_fail["passed"])
        self.assertTrue(lim_fail["hint"])

        # Strict AST sandbox refusal of malicious & DoS payloads
        bad_payloads = [
            "__import__('os').system('id')",
            "lambda x: x + 1",
            "open('/etc/passwd').read()",
            "9**9**9",
            "x**13",
            "(1).__class__.__mro__",
        ]
        for payload in bad_payloads:
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):
                    ex.compile_expr_to_rpn(payload, allowed_vars={"x"})

    def test_derivation_forge_viva_chamber_work_mentor_sunset_pace_and_whiteboard(self) -> None:
        import syntopik.evidence as ev
        import syntopik.whiteboard as wb
        import syntopik.work_mentor as wm

        df = load_script_module("derivation_forge.py")
        vc = load_script_module("viva_chamber.py")
        gi = load_script_module("graph_intel.py")
        dp = load_script_module("daily_plan.py")

        with tempfile.TemporaryDirectory(prefix="synt-v3-ped-") as tmp:
            ws = Path(tmp) / "ws"
            kg = ws / "knowledge-graph"
            ch = ws / "chapters"
            kg.mkdir(parents=True)
            ch.mkdir(parents=True)

            # Seed propositions + chains for Viva & Sunset
            props = [
                {
                    "id": "phy-shm-period",
                    "subject": "Physics",
                    "chapter": "SHM",
                    "title": "Simple pendulum small-angle period T = 2*pi*sqrt(L/g)",
                    "kind": "theorem",
                    "status": "solid",
                    "bktMastery": 0.90,
                    "boundaryConditions": ["small angle theta << 1 rad", "uniform gravity field", "massless inextensible string"],
                    "antiPatterns": ["claiming period is independent of amplitude at large angles"],
                },
                {
                    "id": "phy-fragile-solid",
                    "subject": "Physics",
                    "chapter": "SHM",
                    "title": "Hooke law linear restoring force F = -kx",
                    "kind": "axiom",
                    "status": "solid",
                    "bktMastery": 0.92,
                    "boundaryConditions": ["elastic linear limit", "massless spring"],
                    "antiPatterns": ["assuming linearity beyond elastic yield threshold"],
                },
                {"id": "shm-ax-1", "subject": "Physics", "chapter": "SHM", "title": "Newton 2nd law restoring ODE", "kind": "axiom", "status": "solid"},
                {"id": "shm-leaf-1", "subject": "Physics", "chapter": "SHM", "title": "Vertical spring shift", "kind": "fact", "status": "solid"},
                {"id": "shm-leaf-2", "subject": "Physics", "chapter": "SHM", "title": "Series spring shortcut", "kind": "fact", "status": "solid"},
                {"id": "shm-leaf-3", "subject": "Physics", "chapter": "SHM", "title": "Parallel spring shortcut", "kind": "fact", "status": "solid"},
                {"id": "shm-leaf-4", "subject": "Physics", "chapter": "SHM", "title": "Cut spring k scaling", "kind": "fact", "status": "solid"},
                {"id": "waves-link", "subject": "Physics", "chapter": "Waves", "title": "Wave equation coupling", "kind": "theorem", "status": "shaky"},
            ]
            chains = [
                {"premises": ["shm-ax-1"], "conclusion": "phy-shm-period", "relation": "derives"},
                {"premises": ["shm-ax-1"], "conclusion": "shm-leaf-1", "relation": "derives"},
                {"premises": ["shm-ax-1"], "conclusion": "shm-leaf-2", "relation": "derives"},
                {"premises": ["shm-ax-1"], "conclusion": "shm-leaf-3", "relation": "derives"},
                {"premises": ["shm-ax-1"], "conclusion": "shm-leaf-4", "relation": "derives"},
                {"premises": ["shm-ax-1"], "conclusion": "waves-link", "relation": "requires"},
            ]
            (kg / "propositions.jsonl").write_text("\n".join(json.dumps(p) for p in props) + "\n", encoding="utf-8")
            (kg / "chains.jsonl").write_text("\n".join(json.dumps(c) for c in chains) + "\n", encoding="utf-8")

            # 1. derivation_forge.py: create, blackout (zero leak), check (math equiv), cut BFS Axiom-Cut Surgery
            secret_step_expr = "2*pi*sqrt(m/k)"
            created = df.create_derivation(
                {
                    "id": "deriv-shm-T",
                    "propId": "phy-shm-period",
                    "chapter": "SHM",
                    "title": "Spring-Mass Period Derivation",
                    "domain": "Physics",
                    "steps": [
                        {"id": "a1", "label": "Newton 2nd Law", "expr": "m*a = -k*x", "role": "axiom", "dependsOn": []},
                        {"id": "a2", "label": "Energy Scale", "expr": "E = 0.5*k*A**2", "role": "axiom", "dependsOn": []},
                        {"id": "s1", "label": "Angular frequency", "expr": "sqrt(k/m)", "role": "step", "dependsOn": ["a1"]},
                        {"id": "s2", "label": "Period formula", "expr": secret_step_expr, "role": "theorem", "dependsOn": ["s1"]},
                        {"id": "s3", "label": "Max energy check", "expr": "0.5*k*A**2", "role": "step", "dependsOn": ["a2"]},
                    ],
                },
                kg_dir=kg,
                workspace=ws,
            )
            self.assertTrue(created["ok"])

            bo = df.get_blackout_challenge("deriv-shm-T", level=3, kg_dir=kg, workspace=ws)
            self.assertEqual(bo["hiddenCount"], 3)
            self.assertNotIn(secret_step_expr, json.dumps(bo), "Blackout challenge leaked hidden theorem expr!")
            self.assertNotIn("sqrt(k/m)", json.dumps(bo), "Blackout challenge leaked hidden intermediate expr!")

            chk_eq = df.check_blackout_step("deriv-shm-T", "s2", "sqrt(m/k) * 2 * pi", kg_dir=kg, workspace=ws)
            self.assertTrue(chk_eq["passed"], chk_eq)
            self.assertEqual(chk_eq["method"], "math_equivalence")

            cut_res = df.grade_axiom_cut("deriv-shm-T", "a1", guess_step_id="s1", kg_dir=kg, workspace=ws)
            self.assertTrue(cut_res["passed"], cut_res)
            self.assertEqual(cut_res["earliestBreak"], "s1")
            self.assertEqual(cut_res["invalidatedSteps"], ["s1", "s2"])
            self.assertEqual(cut_res["survivingSteps"], ["a2", "s3"])
            self.assertTrue(cut_res["evidenceRecorded"])

            # 2. viva_chamber.py: (a) 5-turn pass (>= 0.75 + T4 evidence)
            v_pass = vc.start_viva("phy-shm-period", kg_dir=kg, workspace=ws)
            self.assertEqual(v_pass["currentTurn"], 1)
            strong_turns = [
                "Because restoring torque scales as sin(theta), the simple pendulum period T = 2*pi*sqrt(L/g) holds only in the small angle boundary regime where theta << 1 rad under uniform gravity and a massless inextensible string, and it breaks when large angles invalidate the linear approximation.",
                "When the small angle boundary condition is violated at large theta, the restoring force sin(theta) falls below theta, therefore the linear approximation breaks first because curvature decreases and period increases beyond the small-angle limit.",
                "Claiming period is independent of amplitude at large angles is false; instead a concrete counterexample at theta = 90 degrees refutes it because the elliptic integral correction shifts period upward by roughly 18 percent.",
                "If we transfer the pendulum mechanism into an accelerating elevator or large amplitude edge regime, effective gravity shifts downstream and we can verify the prediction by comparing measured period against the limiting case g -> 0 where period diverges.",
                "To reconcile Turn 1 with Turns 2-4, the sharpened invariant is that pendulum period requires small angle linear restoring torque under uniform gravity; violating that boundary causes amplitude dependence that we verify via nonlinear perturbation.",
            ]
            last_p = None
            for resp in strong_turns:
                last_p = vc.submit_viva_turn(v_pass["sessionId"], resp, confidence=0.85, kg_dir=kg, workspace=ws)
            assert last_p is not None
            self.assertEqual(last_p["status"], "completed")
            self.assertEqual(last_p["outcome"], "passed", last_p)
            self.assertGreaterEqual(last_p["scores"]["composite"], 0.75)
            self.assertTrue(last_p["evidenceRecorded"])
            self.assertTrue(ev.verify_ledger_chain(kg)["ok"])

            # (b) Critical boundary collapse demoting `solid -> shaky`
            v_fail = vc.start_viva("phy-fragile-solid", kg_dir=kg, workspace=ws)
            weak_turns = [
                "Hooke law always holds unconditionally with no exceptions regardless of stretching.",
                "It always works everywhere without failing.",
                "No exceptions ever.",
                "Always true.",
                "Same formula always works.",
            ]
            last_f = None
            for resp in weak_turns:
                last_f = vc.submit_viva_turn(v_fail["sessionId"], resp, confidence=0.95, kg_dir=kg, workspace=ws)
            assert last_f is not None
            self.assertEqual(last_f["outcome"], "boundary_collapse", last_f)
            self.assertTrue(last_f["demotedToShaky"])
            props_after = {json.loads(ln)["id"]: json.loads(ln) for ln in (kg / "propositions.jsonl").read_text(encoding="utf-8").splitlines() if ln.strip()}
            self.assertEqual(props_after["phy-fragile-solid"]["status"], "shaky")

            # 3. work_mentor.py: debrief_git_diff + dedup + flag injection rejection + mentor gate
            sample_diff = """diff --git a/search.py b/search.py
--- a/search.py
+++ b/search.py
@@ -1,6 +1,8 @@
 def bsearch(nums, target):
+    if nums is None or len(nums) == 0:
+        return -1
     lo, hi = 0, len(nums) - 1
-    while lo < hi:
+    while lo <= hi:
         mid = (lo + hi) // 2
         if nums[mid] == target:
             return mid
"""
            deb1 = wm.debrief_git_diff(ws, diff_text=sample_diff, kg_dir=kg)
            self.assertTrue(deb1["ok"])
            self.assertGreaterEqual(deb1["findingsCount"], 1)
            self.assertGreaterEqual(len(deb1["crystallized"]["forks"]), 1)
            self.assertGreaterEqual(len(deb1["crystallized"]["arenas"]), 1)
            self.assertGreaterEqual(len(deb1["crystallized"]["propositions"]), 1)
            n_props_1 = len([ln for ln in (kg / "propositions.jsonl").read_text(encoding="utf-8").splitlines() if ln.strip()])
            n_arenas_1 = len([ln for ln in (kg / "arenas.jsonl").read_text(encoding="utf-8").splitlines() if ln.strip()])

            # Rerun on identical diff MUST deduplicate cleanly
            deb2 = wm.debrief_git_diff(ws, diff_text=sample_diff, kg_dir=kg)
            self.assertTrue(deb2["ok"])
            n_props_2 = len([ln for ln in (kg / "propositions.jsonl").read_text(encoding="utf-8").splitlines() if ln.strip()])
            n_arenas_2 = len([ln for ln in (kg / "arenas.jsonl").read_text(encoding="utf-8").splitlines() if ln.strip()])
            self.assertEqual(n_props_1, n_props_2)
            self.assertEqual(n_arenas_1, n_arenas_2)

            # Flag-injection refusal on git ref starting with '-'
            for bad_ref in ("--exec=touch /tmp/pwn", "-u", "--output=/tmp/x"):
                with self.assertRaises(ValueError):
                    wm.validate_git_ref(bad_ref)
                with self.assertRaises(ValueError):
                    wm.debrief_git_diff(ws, ref=bad_ref, kg_dir=kg)

            # Mentor mode gate: silent passthrough on solid T3+ vs 20s gate on shaky
            wm.set_mentor_mode(ws, True, kg_dir=kg)
            gate_solid = wm.evaluate_mentor_gate(ws, ["phy-shm-period"], kg_dir=kg)
            self.assertFalse(gate_solid["gated"])
            self.assertEqual(gate_solid["action"], "passthrough")
            self.assertEqual(gate_solid["durationSec"], 0)

            gate_shaky = wm.evaluate_mentor_gate(ws, ["phy-fragile-solid"], kg_dir=kg)
            self.assertTrue(gate_shaky["gated"])
            self.assertEqual(gate_shaky["action"], "micro_prediction")
            self.assertEqual(gate_shaky["durationSec"], 20)
            self.assertIn("phy-fragile-solid", gate_shaky["prompt"])

            # 4. graph_intel.py --sunset & daily_plan.py --pace --rebalance --ics
            ss_plan = gi.compute_sunset_plan(kg, "shm", keep_ratio=0.20, target_retention=0.80)
            self.assertTrue(ss_plan["ok"])
            self.assertGreaterEqual(ss_plan["keepCount"], 1)
            self.assertGreater(ss_plan["archiveCount"], ss_plan["keepCount"])
            self.assertEqual(ss_plan["keepAxioms"][0]["id"], "shm-ax-1")
            self.assertEqual(ss_plan["targetRetention"], 0.80)

            (kg / "study-config.json").write_text(
                json.dumps({
                    "dailyMinutes": 90,
                    "slackBufferPct": 20,
                    "exams": [
                        {"name": "Physics Midterm", "subject": "Physics", "chapterSlug": "shm", "examDate": "2026-12-10", "weight": 1.2, "missedDays": 1},
                        {"name": "Systems Final", "subject": "CS", "examDate": "2026-12-18", "weight": 1.0},
                    ],
                }),
                encoding="utf-8",
            )
            pace_plan = dp.build_pace_plan(kg_dir=kg, chapters_dir=ch, today="2026-11-26", rebalance=True, horizon_days=10)
            self.assertEqual(pace_plan["mode"], "pace")
            self.assertTrue(pace_plan["rebalanced"])
            self.assertEqual(pace_plan["courseCount"], 2)
            self.assertGreaterEqual(pace_plan["bufferDaysScheduled"], 1)

            ics_out = Path(tmp) / "pace.ics"
            dp.export_pace_ics(pace_plan, ics_out)
            ics_txt = ics_out.read_text(encoding="utf-8")
            self.assertIn("BEGIN:VCALENDAR", ics_txt)
            self.assertIn("VERSION:2.0", ics_txt)
            self.assertIn("BEGIN:VEVENT", ics_txt)
            self.assertIn("END:VEVENT", ics_txt)
            self.assertIn("END:VCALENDAR", ics_txt)

            # 5. whiteboard.py: push/unlock/pin/resolve/clear + atomic revisions
            r_p1 = wb.push_frame(kg, {"kind": "derivation_step", "title": "Step 1", "body": "Write Newton 2nd law"})
            self.assertEqual(r_p1["revision"], 1)
            r_p2 = wb.push_frame(
                kg,
                {
                    "id": "gate-1",
                    "kind": "prediction_gate",
                    "title": "Sign Check",
                    "prompt": "Predict restoring sign",
                    "revealedBody": "SECRET_REVEALED_BODY",
                    "expectedAnswer": "negative",
                },
            )
            self.assertEqual(r_p2["revision"], 2)
            self.assertTrue(r_p2["frame"]["locked"])
            self.assertEqual(r_p2["frame"]["body"], "")

            r_pin = wb.pin_step(kg, 0, "Why is damping ignored?", frame_id=r_p1["frame"]["id"])
            self.assertEqual(r_pin["revision"], 3)
            self.assertEqual(wb.get_session(kg)["pendingPinCount"], 1)

            r_unl = wb.unlock_gate(kg, "gate-1", "negative restoring force", confidence=80)
            self.assertEqual(r_unl["revision"], 4)
            self.assertFalse(r_unl["frame"]["locked"])
            self.assertEqual(r_unl["frame"]["body"], "SECRET_REVEALED_BODY")
            self.assertAlmostEqual(r_unl["confidence"], 0.8, places=4)

            r_res = wb.resolve_pin(kg, r_pin["pin"]["id"], resolution="Ideal frictionless model")
            self.assertEqual(r_res["revision"], 5)
            self.assertEqual(wb.get_session(kg)["pendingPinCount"], 0)

            r_clr = wb.clear_session(kg)
            self.assertEqual(r_clr["revision"], 6)
            self.assertEqual(r_clr["session"]["frameCount"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
