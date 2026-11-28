#!/usr/bin/env python3
"""Syntopik regression suite: Knowledge hypergraph, cross-chapter intelligence, FIRe calibration and the stdlib core.

Shared fixtures live in tests/_helpers.py. Run via:
    python3 -B -m unittest discover -s tests -t tests -v
"""

from __future__ import annotations

import contextlib
import importlib.util
import json
import math
import shutil
import sqlite3
import tempfile
import unittest
import unittest.mock
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from _helpers import (
    SCRIPTS_DIR,
    _EncodedStringIO,
    load_script_module,
    requires,
    run_cli,
)


class TestKgHyperedgesAndMultiCue(unittest.TestCase):
    """11. LearnHarness Parity Slice C: Lexical dedup, Multi-Cue FSRS-6 & Joint Hyperedges."""

    @requires("genanki")
    def test_find_similar_multicue_fsrs_anki_and_hyperedge_sync(self) -> None:
        srs = load_script_module("srs_engine.py")

        def _run_srs(argv: list[str]) -> dict[str, Any]:
            buf = _EncodedStringIO()
            with contextlib.redirect_stdout(buf):
                srs.main(argv)
            return json.loads(buf.getvalue())

        with tempfile.TemporaryDirectory(prefix="mlh-lh-kg-") as tmp:
            kg_dir = Path(tmp)
            props = [
                {
                    "id": "phy-newton2",
                    "subject": "Physics",
                    "chapter": "Laws of Motion",
                    "title": "Newton second law F = ma",
                    "kind": "axiom",
                    "status": "solid",
                },
                {
                    "id": "phy-work-def",
                    "subject": "Physics",
                    "chapter": "Work-Energy",
                    "title": "Definition of mechanical work integral F dot dx",
                    "kind": "definition",
                    "status": "solid",
                },
                {
                    "id": "phy-work-energy",
                    "subject": "Physics",
                    "chapter": "Work-Energy",
                    "title": "Work-energy theorem $W_{\\text{net}} = \\Delta K$",
                    "aliases": ["work kinetic energy theorem"],
                    "kind": "derivation",
                    "status": "shaky",
                },
            ]
            (kg_dir / "propositions.jsonl").write_text(
                "\n".join(json.dumps(p) for p in props) + "\n",
                encoding="utf-8",
            )
            chains = [
                {"from": "phy-newton2", "to": "phy-work-energy", "relation": "requires", "why": "Dynamics input"},
                {
                    "premises": ["phy-newton2", "phy-work-def"],
                    "conclusion": "phy-work-energy",
                    "relation": "derives",
                    "why": "Integrate $F = m dv/dt$ over $dx$",
                },
            ]
            (kg_dir / "chains.jsonl").write_text(
                "\n".join(json.dumps(c) for c in chains) + "\n",
                encoding="utf-8",
            )

            # (a) find-similar ranks lexical duplicate cleanly
            sim = _run_srs(["find-similar", "Work kinetic energy theorem", "--kg-dir", str(kg_dir), "--json"])
            self.assertGreaterEqual(len(sim["matches"]), 1)
            self.assertEqual(sim["matches"][0]["id"], "phy-work-energy")
            self.assertGreaterEqual(sim["matches"][0]["score"], 0.5)

            # (b) add-cue + review --cue-id + weighted next-cue
            _run_srs([
                "add-cue", "phy-work-energy",
                "--q", "Does $W_{\\text{net}} = \\Delta K$ hold with friction?",
                "--a", "Yes, if $W_{\\text{net}}$ includes all forces (conservative and non-conservative).",
                "--cue-id", "c1", "--kg-dir", str(kg_dir), "--no-sync", "--json",
            ])
            _run_srs([
                "add-cue", "phy-work-energy",
                "--q", "What frame must kinetic energy be evaluated in?",
                "--a", "Inertial frame (or include pseudo-force work in non-inertial frame).",
                "--cue-id", "c2", "--kg-dir", str(kg_dir), "--no-sync", "--json",
            ])
            _run_srs([
                "review", "phy-work-energy", "--rating", "again", "--cue-id", "c1",
                "--kg-dir", str(kg_dir), "--no-sync",
            ])
            _run_srs([
                "review", "phy-work-energy", "--rating", "easy", "--cue-id", "c2",
                "--kg-dir", str(kg_dir), "--no-sync",
            ])
            nxt = _run_srs(["next-cue", "phy-work-energy", "--seed", "7", "--kg-dir", str(kg_dir), "--json"])
            self.assertEqual(nxt["totalCues"], 2)
            weights_map = nxt["weights"]
            if isinstance(weights_map, list):
                weights_map = {w["id"]: w["weight"] for w in weights_map}
            self.assertEqual(weights_map["c1"], 4)
            self.assertEqual(weights_map["c2"], 1)

            # (c) Multi-cue export-anki emits 4 notes (1 + 1 + 2 cues) with MathJax & joint '∧' chain
            apkg = kg_dir / "multicue.apkg"
            exp = _run_srs(["export-anki", "--kg-dir", str(kg_dir), "--out", str(apkg)])
            self.assertEqual(exp["exportedCards"], 4)
            with zipfile.ZipFile(apkg, "r") as zf:
                db_name = "collection.anki21" if "collection.anki21" in zf.namelist() else "collection.anki2"
                db_p = kg_dir / db_name
                db_p.write_bytes(zf.read(db_name))
            conn = sqlite3.connect(str(db_p))
            try:
                notes = [r[0] for r in conn.execute("SELECT flds FROM notes").fetchall()]
            finally:
                conn.close()
            self.assertEqual(len(notes), 4)
            joined_notes = "\n".join(notes)
            self.assertIn(r"\(W_{\text{net}} = \Delta K\)", joined_notes)
            self.assertIn("∧", joined_notes)

            # (d) sync-graph.mjs handles BOTH binary from/to AND multi-premise hyperedges cleanly
            out_js = kg_dir / "graph-data.js"
            sync_proc = run_cli([
                "node", str(SCRIPTS_DIR / "sync-graph.mjs"),
                "--kg-dir", str(kg_dir), "--out", str(out_js), "--quiet",
            ])
            self.assertEqual(sync_proc.returncode, 0, f"sync-graph.mjs failed: {sync_proc.stderr}")
            self.assertTrue(out_js.is_file())
            eval_proc = run_cli([
                "node", "-e",
                f"const fs=require('fs');const vm=require('vm');const sb={{window:{{}}}};vm.runInNewContext(fs.readFileSync({json.dumps(str(out_js))},'utf8'),sb);console.log(JSON.stringify({{graph:sb.window.__STUDY_GRAPH__,hub:sb.window.__STUDY_HUB__}}));",
            ])
            self.assertEqual(eval_proc.returncode, 0, eval_proc.stderr)
            loaded = json.loads(eval_proc.stdout)
            g, hub = loaded["graph"], loaded["hub"]
            self.assertEqual(g["chainCount"], 2)
            self.assertEqual(hub["stats"]["hyperedgeCount"], 1)
            self.assertEqual(len(g["normalizedEdges"]), 3)  # 1 binary + 2 from joint hyperedge
            binary = [c for c in g["chains"] if len(c.get("premises", [])) == 1]
            hyper = [c for c in g["chains"] if len(c.get("premises", [])) == 2]
            self.assertEqual(len(binary), 1)
            self.assertEqual(binary[0]["from"], "phy-newton2")
            self.assertEqual(binary[0]["to"], "phy-work-energy")
            self.assertEqual(len(hyper), 1)
            self.assertEqual(set(hyper[0]["premises"]), {"phy-newton2", "phy-work-def"})
            self.assertEqual(hyper[0]["conclusion"], "phy-work-energy")


class TestStdlibBetweenness(unittest.TestCase):
    def test_sparse_brandes_matches_brute_force_shortest_paths(self) -> None:
        """The stdlib betweenness must equal the textbook definition (count of shortest paths)."""
        from collections import deque
        from itertools import permutations

        gi = load_script_module("graph_intel.py")
        G = gi.StdlibDiGraph()
        for n in "abcdefgh":
            G.add_node(n)
        for u, v in [("a", "b"), ("a", "c"), ("b", "d"), ("c", "d"), ("d", "e"), ("e", "f"), ("b", "e"), ("g", "a")]:
            G.add_edge(u, v)
        # "h" is isolated on purpose.

        def all_shortest(s: str, t: str) -> list[list[str]]:
            paths: list[list[str]] = []
            q = deque([[s]])
            best = None
            while q:
                path = q.popleft()
                if best is not None and len(path) > best:
                    break
                if path[-1] == t:
                    best = len(path)
                    paths.append(path)
                    continue
                for w in G.successors(path[-1]):
                    if w not in path:
                        q.append([*path, w])
            return paths

        nodes = list(G.nodes())
        expected = dict.fromkeys(nodes, 0.0)
        for s, t in permutations(nodes, 2):
            paths = all_shortest(s, t)
            for v in nodes:
                if v not in (s, t) and paths:
                    expected[v] += sum(1 for p in paths if v in p) / len(paths)
        n = len(nodes)
        got = gi._graph_betweenness(G)
        for v in nodes:
            self.assertAlmostEqual(got[v], expected[v] / ((n - 1) * (n - 2)), places=12, msg=v)
        self.assertGreater(got["d"], 0.0)
        self.assertEqual(got["h"], 0.0)


class TestP2CrossChapterIntel(unittest.TestCase):
    """14. P2.1–P2.4: graph_intel, Visual Anki media, audio_brief & daily_plan."""

    @requires("genanki", binaries=("ffmpeg",))
    @unittest.skipUnless(
        any(shutil.which(b) for b in ("say", "espeak-ng", "espeak")),
        "offline TTS engine (macOS say or espeak-ng) not installed",
    )
    def test_graph_intel_visual_anki_audio_brief_and_daily_plan(self) -> None:
        from PIL import Image

        gi = load_script_module("graph_intel.py")
        srs = load_script_module("srs_engine.py")
        ab = load_script_module("audio_brief.py")
        dp = load_script_module("daily_plan.py")

        def _cap(fn, argv: list[str]) -> dict[str, Any]:
            buf = _EncodedStringIO()
            with contextlib.redirect_stdout(buf):
                fn(argv)
            return json.loads(buf.getvalue())

        with tempfile.TemporaryDirectory(prefix="mlh-p2-") as tmp:
            t = Path(tmp)
            kg_dir = t / "knowledge-graph"
            ch_dir = t / "chapters"
            demos_dir = t / "demos"
            kg_dir.mkdir()
            ch_dir.mkdir()
            demos_dir.mkdir()

            # Create a tiny real PNG for P2.2 Visual Anki media embedding
            img_path = demos_dir / "sn2-mech.png"
            Image.new("RGB", (64, 48), (30, 64, 175)).save(str(img_path), format="PNG")

            props = [
                {"id": "ax1", "subject": "Physics", "chapter": "Kinematics", "title": "Position and velocity definitions", "kind": "axiom", "status": "solid", "bktMastery": 0.95},
                {"id": "bn1", "subject": "Physics", "chapter": "Mechanics", "title": "Newton second law restoring force", "kind": "derivation", "status": "shaky", "bktMastery": 0.35, "nextReview": "2020-01-01"},
                {"id": "mid1", "subject": "Physics", "chapter": "Oscillations", "title": "Harmonic oscillation energy and restoring equilibrium", "kind": "derivation", "status": "solid", "bktMastery": 0.9, "media": ["demos/sn2-mech.png"]},
                {"id": "adv1", "subject": "Physics", "chapter": "Waves", "title": "Progressive wave energy transport", "kind": "derivation", "status": "planned", "bktMastery": 0.2},
                {"id": "isl1", "subject": "Chemistry", "chapter": "Kinetics", "title": "Arrhenius rote formula without derivation", "kind": "formula", "status": "solid", "bktMastery": 0.85},
                {"id": "elec1", "subject": "Physics", "chapter": "Electrostatics", "title": "Dipole oscillation energy and restoring equilibrium in field", "kind": "derivation", "status": "solid", "bktMastery": 0.92},
            ]
            chains = [
                {"from": "ax1", "to": "bn1", "relation": "requires"},
                {"from": "bn1", "to": "mid1", "relation": "derives"},
                {"premises": ["bn1", "mid1"], "conclusion": "adv1", "relation": "derives", "why": "Combine dynamics and oscillation"},
            ]
            (kg_dir / "propositions.jsonl").write_text("\n".join(json.dumps(p) for p in props) + "\n", encoding="utf-8")
            (kg_dir / "chains.jsonl").write_text("\n".join(json.dumps(c) for c in chains) + "\n", encoding="utf-8")
            (kg_dir / "mistakes.jsonl").write_text(
                json.dumps({
                    "id": "m-11223344", "date": "2026-11-25", "subject": "Physics",
                    "chapter": "Oscillations", "propId": "bn1", "category": "sign_error",
                    "triggerCue": "Spring displacement", "wrongMove": "Dropped minus",
                    "correctMove": "Use F = -kx", "sourceRef": "test",
                }) + "\n",
                encoding="utf-8",
            )
            (ch_dir / "shm-tracker.json").write_text(
                json.dumps({
                    "schemaVersion": 1, "slug": "shm", "title": "Simple Harmonic Motion",
                    "subject": "Physics", "active": True,
                    "topics": [{"id": "t1", "order": 1, "title": "Restoring Law", "level": "basics", "status": "in_progress"}],
                    "questions": [], "milestones": {"sourcesIndexed": True, "probed": True},
                }),
                encoding="utf-8",
            )

            # (a) P2.1 graph_intel.py: bottlenecks, islands, path-to, combos over binary + hyperedges
            gi_res = _cap(gi.main, ["--kg-dir", str(kg_dir), "--all", "--path-to", "adv1", "--json"])
            self.assertGreaterEqual(len(gi_res["bottlenecks"]), 1)
            self.assertEqual(gi_res["bottlenecks"][0]["id"], "bn1")
            self.assertGreaterEqual(gi_res["bottlenecks"][0]["downstreamDescendants"], 2)

            island_ids = {x["id"] for x in gi_res["islands"]}
            self.assertIn("isl1", island_ids)
            self.assertNotIn("ax1", island_ids)

            path_seq = [step["id"] for step in gi_res["pathTo"]["sequence"]]
            self.assertEqual(path_seq[-1], "adv1")
            self.assertIn("bn1", path_seq[:-1])
            self.assertIn("mid1", path_seq[:-1])

            self.assertGreaterEqual(len(gi_res["combos"]), 1)

            # (b) P2.2 Visual Anki media embedding inside .apkg
            apkg = t / "visual.apkg"
            exp = _cap(srs.main, ["export-anki", "--kg-dir", str(kg_dir), "--out", str(apkg)])
            self.assertGreaterEqual(exp["mediaCount"], 1)
            with zipfile.ZipFile(apkg, "r") as zf:
                names = set(zf.namelist())
                self.assertIn("media", names)
                media_map = json.loads(zf.read("media").decode("utf-8"))
                self.assertTrue(any("sn2-mech.png" in v for v in media_map.values()))
                for blob_key in media_map:
                    self.assertIn(blob_key, names)
                db_name = "collection.anki21" if "collection.anki21" in names else "collection.anki2"
                db_p = t / db_name
                db_p.write_bytes(zf.read(db_name))
            conn = sqlite3.connect(str(db_p))
            try:
                all_flds = "\n".join(r[0] for r in conn.execute("SELECT flds FROM notes").fetchall())
            finally:
                conn.close()
            self.assertIn("<img src=", all_flds)

            # (c) P2.3 audio_brief.py offline say -> valid mp3 (> 2000 B) fast
            cards_file = t / "cards.json"
            cards_file.write_text(
                json.dumps([{"q": "State Hooke law $F = -kx$.", "a": "Force opposes displacement."}]),
                encoding="utf-8",
            )
            mp3_out = t / "brief.mp3"
            ab_res = _cap(ab.main, [
                "--cards", str(cards_file), "--engine", "say",
                "--pause-sec", "1.0", "--max-cards", "1", "--out", str(mp3_out), "--json",
            ])
            self.assertTrue(mp3_out.is_file())
            self.assertGreater(mp3_out.stat().st_size, 2000)
            self.assertEqual(ab_res["cardsCount"], 1)

            # (d) P2.4 daily_plan.py time-boxed blocks sum to --minutes
            plan = _cap(dp.main, ["--minutes", "60", "--kg-dir", str(kg_dir), "--chapters-dir", str(ch_dir), "--json"])
            self.assertEqual(plan["totalMinutes"], 60)
            self.assertEqual(sum(b["minutes"] for b in plan["blocks"]), 60)
            plan_txt = json.dumps(plan)
            self.assertIn("bn1", plan_txt)
            self.assertIn("shm", plan_txt)


class TestFireCalibrationAndStdlibCore(unittest.TestCase):
    """23. Syntopik v2.0 Pure-Stdlib Core: True FIRe, SRC, Calibration, Recover, Leeches, Graph Intel, Daily Plan, Crib Sheet & Exam Autopsy."""

    def test_fire_calibration_src_recover_leeches_graph_intel_daily_plan_crib_and_exam(self) -> None:
        import syntopik.fsrs_core as fc

        srs = load_script_module("srs_engine.py")
        gi = load_script_module("graph_intel.py")
        dp = load_script_module("daily_plan.py")
        cs = load_script_module("cheatsheet_pdf.py")
        er = load_script_module("exam_runner.py")
        ct = load_script_module("chapter_tracker.py")

        def _cap(fn, argv: list[str]) -> dict[str, Any]:
            buf = _EncodedStringIO()
            with contextlib.redirect_stdout(buf):
                fn(argv)
            return json.loads(buf.getvalue())

        with tempfile.TemporaryDirectory(prefix="synt-v2-core-") as tmp:
            t = Path(tmp)
            kg = t / "knowledge-graph"
            ch = t / "chapters"
            kg.mkdir()
            ch.mkdir()

            now = datetime.now(timezone.utc)
            today_str = now.date().isoformat()
            d_14_ago = (now - timedelta(days=14)).isoformat()
            d_10_ago_date = (now - timedelta(days=10)).date().isoformat()
            d_1h_ago = (now - timedelta(hours=1)).isoformat()
            d_future = (now + timedelta(days=20)).date().isoformat()
            d_yesterday = (now - timedelta(days=1)).date().isoformat()

            # 1. Seed DAG for True FIRe propagation, (1-R_u) early-review discount, misconception protection & leeches
            props = [
                {
                    "id": "p-root",
                    "subject": "Physics",
                    "chapter": "Mechanics",
                    "title": "Newton second law restoring force dynamics",
                    "kind": "axiom",
                    "status": "solid",
                    "bktMastery": 0.80,
                    "nextReview": d_yesterday,
                    "lastReviewed": d_14_ago,
                    "fsrs": {
                        "card_id": 1001,
                        "state": 2,
                        "step": None,
                        "stability": 4.0,
                        "difficulty": 4.5,
                        "due": (now - timedelta(days=2)).isoformat(),
                        "last_review": d_14_ago,
                    },
                },
                {
                    "id": "p-early",
                    "subject": "Physics",
                    "chapter": "Mechanics",
                    "title": "Freshly reviewed conservation law",
                    "kind": "axiom",
                    "status": "solid",
                    "bktMastery": 0.90,
                    "nextReview": d_future,
                    "lastReviewed": d_1h_ago,
                    "fsrs": {
                        "card_id": 1002,
                        "state": 2,
                        "step": None,
                        "stability": 12.0,
                        "difficulty": 3.0,
                        "due": (now + timedelta(days=12)).isoformat(),
                        "last_review": d_1h_ago,
                    },
                },
                {
                    "id": "p-misc",
                    "subject": "Physics",
                    "chapter": "Oscillations",
                    "title": "Phasor velocity direction sign rule",
                    "kind": "derivation",
                    "status": "misconception",
                    "bktMastery": 0.15,
                    "nextReview": d_yesterday,
                    "fsrs": {
                        "card_id": 1003,
                        "state": 2,
                        "step": None,
                        "stability": 1.0,
                        "difficulty": 8.0,
                        "due": (now - timedelta(days=3)).isoformat(),
                        "last_review": d_14_ago,
                    },
                },
                {
                    "id": "p-mid",
                    "subject": "Physics",
                    "chapter": "Oscillations",
                    "title": "Spring block oscillation equation frequency period",
                    "kind": "derivation",
                    "status": "shaky",
                    "bktMastery": 0.45,
                    "nextReview": d_yesterday,
                    "fsrs": {
                        "card_id": 1004,
                        "state": 2,
                        "step": None,
                        "stability": 2.5,
                        "difficulty": 5.5,
                        "due": (now - timedelta(days=1)).isoformat(),
                        "last_review": d_14_ago,
                    },
                },
                {
                    "id": "p-top",
                    "subject": "Physics",
                    "chapter": "Oscillations",
                    "title": "Composite spring block oscillation frequency period analysis",
                    "kind": "derivation",
                    "status": "shaky",
                    "bktMastery": 0.50,
                    "nextReview": d_yesterday,
                    "fsrs": {
                        "card_id": 1005,
                        "state": 2,
                        "step": None,
                        "stability": 3.0,
                        "difficulty": 5.0,
                        "due": (now - timedelta(days=1)).isoformat(),
                        "last_review": d_14_ago,
                    },
                },
                {
                    "id": "p-leech",
                    "subject": "Physics",
                    "chapter": "Oscillations",
                    "title": "Chronic leech trap card",
                    "kind": "formula",
                    "status": "shaky",
                    "lapses": 5,
                    "bktMastery": 0.20,
                    "nextReview": d_yesterday,
                    "fsrs": {
                        "card_id": 1006,
                        "state": 2,
                        "step": None,
                        "stability": 0.5,
                        "difficulty": 9.2,
                        "due": (now - timedelta(days=1)).isoformat(),
                        "last_review": d_14_ago,
                    },
                },
                {
                    "id": "p-rocksolid",
                    "subject": "Physics",
                    "chapter": "Oscillations",
                    "title": "Already rock-solid mastered truth",
                    "kind": "axiom",
                    "status": "solid",
                    "lapses": 0,
                    "bktMastery": 0.98,
                    "nextReview": d_future,
                    "lastReviewed": d_1h_ago,
                    "fsrs": {
                        "card_id": 1007,
                        "state": 2,
                        "step": None,
                        "stability": 90.0,
                        "difficulty": 2.0,
                        "due": (now + timedelta(days=90)).isoformat(),
                        "last_review": d_1h_ago,
                    },
                },
            ]
            chains = [
                {"from": "p-root", "to": "p-mid", "relation": "derives"},
                {"from": "p-misc", "to": "p-mid", "relation": "requires"},
                {"premises": ["p-mid", "p-early"], "conclusion": "p-top", "relation": "derives"},
            ]
            (kg / "propositions.jsonl").write_text("\n".join(json.dumps(p) for p in props) + "\n", encoding="utf-8")
            (kg / "chains.jsonl").write_text("\n".join(json.dumps(c) for c in chains) + "\n", encoding="utf-8")

            # (a) True FIRe review on p-top: refreshes p-mid & p-root, early-discounts p-early, NEVER auto-clears p-misc
            rev_top = srs.record_review(
                kg,
                "p-top",
                "good",
                confidence=0.85,
                duration_ms=4200,
                no_sync=True,
            )
            fc_by_id = {c["propId"]: c for c in rev_top.get("fireCredits", [])}
            self.assertIn("p-mid", fc_by_id, f"Expected FIRe credit on p-mid: {rev_top}")
            self.assertIn("p-root", fc_by_id, f"Expected transitive FIRe credit on p-root: {rev_top}")
            # Early-review (1 - R_u) discount: p-early (reviewed 1h ago, R_u ~ 1) gets strictly less credit than overdue p-root
            early_eff = fc_by_id.get("p-early", {}).get("credit", 0.0)
            root_eff = fc_by_id["p-root"]["credit"]
            self.assertGreater(root_eff, early_eff)
            self.assertLess(early_eff, 0.05)

            props_after = {
                row["id"]: row
                for row in (json.loads(ln) for ln in (kg / "propositions.jsonl").read_text(encoding="utf-8").splitlines() if ln.strip())
            }
            self.assertEqual(props_after["p-misc"]["status"], "misconception", "FIRe MUST NOT auto-clear misconception!")
            self.assertGreater(float(props_after["p-root"].get("implicitCredit") or 0.0), 0.0)
            self.assertTrue(props_after["p-root"].get("lastImplicitReview"))

            # (b) revlog.jsonl + Brier / Hypercorrection / Lucky-Guess damper
            rev_hc = srs.record_review(kg, "p-mid", "again", confidence=0.95, duration_ms=3100, no_sync=True)
            self.assertTrue(rev_hc.get("hypercorrection"))
            rev_lg = srs.record_review(kg, "p-mid", "good", confidence=0.20, duration_ms=8500, no_sync=True)
            self.assertTrue(rev_lg.get("luckyGuess"))

            bkt_lucky = fc.bkt_update(0.30, True, learner_confidence=0.20)
            bkt_confident = fc.bkt_update(0.30, True, learner_confidence=0.90)
            self.assertLess(bkt_lucky, bkt_confident, "Lucky-guess damper must restrain low-confidence BKT jump")

            revlog_rows = [
                json.loads(ln)
                for ln in (kg / "revlog.jsonl").read_text(encoding="utf-8").splitlines()
                if ln.strip()
            ]
            self.assertGreaterEqual(len(revlog_rows), 3)
            cal = fc.compute_calibration(revlog_rows)
            self.assertGreaterEqual(cal["count"], 3)
            self.assertIsInstance(cal["brierScore"], float)
            self.assertGreaterEqual(cal["hypercorrectionCount"], 1)
            self.assertGreaterEqual(cal["luckyGuessCount"], 1)

            # (c) leeches CLI flags chronic lapse/high-difficulty card
            leech_res = _cap(srs.main, ["leeches", "--kg-dir", str(kg)])
            self.assertGreaterEqual(leech_res["leechCount"], 1)
            self.assertIn("p-leech", [x["id"] for x in leech_res["leeches"]])

            # (d) due --compress knockouts on a 5-node due chain
            kg_src = t / "kg-src"
            kg_src.mkdir()
            src_props = [
                {"id": f"d{i}", "subject": "CS", "chapter": "Graphs", "title": f"Step {i}", "status": "shaky", "nextReview": d_yesterday}
                for i in range(1, 6)
            ]
            src_chains = [
                {"from": "d1", "to": "d2", "relation": "derives"},
                {"from": "d2", "to": "d3", "relation": "derives"},
                {"from": "d3", "to": "d4", "relation": "derives"},
                {"from": "d4", "to": "d5", "relation": "derives"},
            ]
            (kg_src / "propositions.jsonl").write_text("\n".join(json.dumps(p) for p in src_props) + "\n", encoding="utf-8")
            (kg_src / "chains.jsonl").write_text("\n".join(json.dumps(c) for c in src_chains) + "\n", encoding="utf-8")
            due_comp = _cap(srs.main, ["due", "--compress", "--kg-dir", str(kg_src), "--today", today_str])
            self.assertTrue(due_comp["compressed"])
            self.assertEqual(due_comp["originalDueCount"], 5)
            self.assertLess(due_comp["dueCount"], 5)
            self.assertGreaterEqual(due_comp["knockedOutCount"], 2)

            # (e) recover --daily-cap 5 --days 6 --apply smooths overdue pile
            kg_rec = t / "kg-rec"
            kg_rec.mkdir()
            overdue_props = [
                {
                    "id": f"ov-{i:02d}",
                    "subject": "Math",
                    "chapter": "Calculus",
                    "title": f"Overdue node {i}",
                    "status": "solid",
                    "nextReview": d_10_ago_date,
                    "fsrs": {"stability": 3.0, "difficulty": 5.0, "due": d_14_ago, "last_review": d_14_ago},
                }
                for i in range(22)
            ]
            (kg_rec / "propositions.jsonl").write_text("\n".join(json.dumps(p) for p in overdue_props) + "\n", encoding="utf-8")
            (kg_rec / "chains.jsonl").write_text("", encoding="utf-8")
            rec_res = _cap(srs.main, ["recover", "--daily-cap", "5", "--days", "6", "--today", today_str, "--apply", "--no-sync", "--kg-dir", str(kg_rec)])
            self.assertTrue(rec_res["applied"])
            self.assertEqual(rec_res["dailyCap"], 5)
            self.assertGreaterEqual(len(rec_res["rescheduleMap"]), 15)
            for day_bucket in rec_res.get("schedule", []):
                self.assertLessEqual(day_bucket["count"], 5)

            # (f) graph_intel.py forced pure-stdlib Tarjan/PageRank + --confusables
            kg_gi = t / "kg-gi"
            kg_gi.mkdir()
            gi_props = [
                {"id": "prem-a", "subject": "Physics", "chapter": "SHM", "title": "Hooke restoring force axiom", "kind": "axiom", "status": "solid", "bktMastery": 0.95},
                {"id": "prem-b", "subject": "Physics", "chapter": "SHM", "title": "Small angle pendulum torque axiom", "kind": "axiom", "status": "solid", "bktMastery": 0.95},
                {"id": "conf-a", "subject": "Physics", "chapter": "SHM", "title": "Spring oscillation angular frequency restoring equilibrium period", "kind": "derivation", "status": "shaky", "bktMastery": 0.40},
                {"id": "conf-b", "subject": "Physics", "chapter": "SHM", "title": "Pendulum oscillation angular frequency restoring equilibrium period", "kind": "derivation", "status": "shaky", "bktMastery": 0.40},
                {"id": "cyc-1", "subject": "Physics", "chapter": "SHM", "title": "Cycle node one", "kind": "derivation", "status": "shaky", "bktMastery": 0.30},
                {"id": "cyc-2", "subject": "Physics", "chapter": "SHM", "title": "Cycle node two", "kind": "derivation", "status": "planned", "bktMastery": 0.20},
            ]
            gi_chains = [
                {"from": "prem-a", "to": "conf-a", "relation": "derives"},
                {"from": "prem-b", "to": "conf-b", "relation": "derives"},
                {"from": "conf-a", "to": "cyc-1", "relation": "requires"},
                {"from": "cyc-1", "to": "cyc-2", "relation": "requires"},
                {"from": "cyc-2", "to": "cyc-1", "relation": "requires"},
            ]
            (kg_gi / "propositions.jsonl").write_text("\n".join(json.dumps(p) for p in gi_props) + "\n", encoding="utf-8")
            (kg_gi / "chains.jsonl").write_text("\n".join(json.dumps(c) for c in gi_chains) + "\n", encoding="utf-8")
            with unittest.mock.patch.object(gi, "nx", None):
                gi_res = _cap(gi.main, ["--all", "--path-to", "cyc-2", "--kg-dir", str(kg_gi), "--json"])
            self.assertEqual(gi_res.get("backend"), "stdlib")
            self.assertGreaterEqual(len(gi_res["bottlenecks"]), 1)
            self.assertTrue(gi_res["pathTo"]["sequence"])
            self.assertGreaterEqual(len(gi_res["confusables"]), 1)
            pair_ids = {gi_res["confusables"][0]["nodeA"]["id"], gi_res["confusables"][0]["nodeB"]["id"]}
            self.assertEqual(pair_ids, {"conf-a", "conf-b"})

            # (g) daily_plan.py: --micro 5, --energy low|normal|deep, --resume (daysAbsent >= 4), --triage-hours 48
            kg_dp = t / "kg-dp"
            ch_dp = t / "ch-dp"
            kg_dp.mkdir()
            ch_dp.mkdir()
            d_10_ago_iso = (now - timedelta(days=10)).isoformat()
            d_3_ago_iso = (now - timedelta(days=3)).isoformat()
            # Choose stability=2.4 & 10 days elapsed -> R ~ 0.75 (inside [0.60, 0.85] warm-start band)
            dp_props = [
                {
                    "id": "p-warm",
                    "subject": "Physics",
                    "chapter": "SHM",
                    "title": "Warm-start SHM energy conservation",
                    "kind": "derivation",
                    "status": "solid",
                    "bktMastery": 0.82,
                    "nextReview": d_yesterday,
                    "lastReviewed": d_10_ago_iso,
                    "fsrs": {"stability": 3.2, "difficulty": 4.0, "due": d_3_ago_iso, "last_review": d_10_ago_iso},
                },
                {
                    "id": "p-weak",
                    "subject": "Physics",
                    "chapter": "SHM",
                    "title": "High-yield SHM phasor bottleneck",
                    "kind": "derivation",
                    "status": "misconception",
                    "bktMastery": 0.15,
                    "nextReview": d_yesterday,
                    "lastReviewed": d_10_ago_iso,
                    "fsrs": {"stability": 0.8, "difficulty": 7.5, "due": d_3_ago_iso, "last_review": d_10_ago_iso},
                },
                {
                    "id": "p-coldleaf",
                    "subject": "Physics",
                    "chapter": "SHM",
                    "title": "Unseen cold leaf derivation",
                    "kind": "derivation",
                    "status": "planned",
                    "bktMastery": 0.10,
                },
                {
                    "id": "p-solid",
                    "subject": "Physics",
                    "chapter": "SHM",
                    "title": "Locked-in rock solid leaf",
                    "kind": "axiom",
                    "status": "solid",
                    "lapses": 0,
                    "bktMastery": 0.99,
                    "nextReview": d_future,
                    "lastReviewed": d_10_ago_iso,
                    "fsrs": {"stability": 200.0, "difficulty": 1.5, "due": (now + timedelta(days=190)).isoformat(), "last_review": d_10_ago_iso},
                },
            ]
            (kg_dp / "propositions.jsonl").write_text("\n".join(json.dumps(p) for p in dp_props) + "\n", encoding="utf-8")
            (kg_dp / "chains.jsonl").write_text(
                json.dumps({"from": "p-weak", "to": "p-warm", "relation": "derives"}) + "\n",
                encoding="utf-8",
            )
            (kg_dp / "mistakes.jsonl").write_text(
                json.dumps({
                    "id": "m-aa112233", "date": d_10_ago_date, "subject": "Physics", "chapter": "SHM",
                    "propId": "p-weak", "category": "sign_error",
                    "triggerCue": "Phasor velocity sign", "wrongMove": "Picked shortest chord ignoring +v",
                    "correctMove": "Check quadrant velocity direction", "sourceRef": "test",
                }) + "\n",
                encoding="utf-8",
            )
            _cap(ct.main, ["init", "shm", "--title", "SHM", "--subject", "Physics", "--set-active", "--chapters-dir", str(ch_dp), "--kg-dir", str(kg_dp), "--no-sync", "--json"])
            _cap(ct.main, ["checkpoint", "shm", "--breadcrumb", "Paused right after phasor Q2", "--first-action", "Check p-warm", "--chapters-dir", str(ch_dp), "--kg-dir", str(kg_dp), "--no-sync", "--json"])
            # Backdate tracker updatedAt so daysAbsent reflects 10 days away
            tpath = ch_dp / "shm-tracker.json"
            tdata = json.loads(tpath.read_text(encoding="utf-8"))
            tdata["updatedAt"] = d_10_ago_iso
            tpath.write_text(json.dumps(tdata), encoding="utf-8")

            micro = _cap(dp.main, ["--micro", "5", "--kg-dir", str(kg_dp), "--chapters-dir", str(ch_dp), "--today", today_str, "--json"])
            self.assertEqual(micro["mode"], "micro")
            self.assertEqual(micro["totalMinutes"], 5)

            for em in ("low", "normal", "deep"):
                p_em = _cap(dp.main, ["--minutes", "45", "--energy", em, "--kg-dir", str(kg_dp), "--chapters-dir", str(ch_dp), "--today", today_str, "--json"])
                self.assertEqual(p_em["energyMode"], em)
                if em == "low":
                    self.assertIn("Zero New Theory", p_em["blocks"][1]["title"])

            p_resume = _cap(dp.main, ["--minutes", "45", "--resume", "--kg-dir", str(kg_dp), "--chapters-dir", str(ch_dp), "--today", today_str, "--json"])
            self.assertIsNotNone(p_resume["reentry"])
            self.assertGreaterEqual(p_resume["reentry"]["daysAbsent"], 4)
            self.assertTrue(p_resume["reentry"]["suppressOverdueCount"])
            self.assertEqual(p_resume["reentry"]["warmStartPropId"], "p-warm")
            self.assertIn("Paused right after phasor Q2", str(p_resume["reentry"]["breadcrumb"]))
            # The Studio's fast path must return exactly the same brief as the full plan.
            for resume_flag in (True, False):
                full = dp.build_daily_plan(kg_dir=kg_dp, chapters_dir=ch_dp, today=today_str, resume=resume_flag)
                self.assertEqual(
                    dp.build_reentry_only(kg_dir=kg_dp, chapters_dir=ch_dp, today=today_str, resume=resume_flag),
                    full["reentry"],
                )

            triage = _cap(dp.main, ["--triage-hours", "48", "--sleep-hours", "7", "--kg-dir", str(kg_dp), "--chapters-dir", str(ch_dp), "--today", today_str, "--json"])
            self.assertEqual(triage["mode"], "exam_triage")
            self.assertGreater(triage["sleepProtectedHours"], 0)
            self.assertGreater(triage["usableStudyHours"], 0)
            self.assertEqual(set(triage["buckets"].keys()), {"salvage_70pct", "protect_20pct", "explicitly_cut"})
            salvage_ids = {x["propId"] for x in triage["buckets"]["salvage_70pct"]}
            self.assertIn("p-weak", salvage_ids)
            # Blocks never run past 90m, are labelled with their real length, and cover every salvage target.
            for blk in triage["blocks"]:
                self.assertLessEqual(blk["minutes"], 90)
                self.assertEqual(blk["slot"], f"Block {blk['order']} ({blk['minutes']}m)")
            scheduled = {t["propId"] for blk in triage["blocks"] for t in blk.get("targets", [])}
            self.assertTrue(salvage_ids <= scheduled)
            self.assertEqual(sum(b["minutes"] for b in triage["blocks"]), int(round(triage["usableStudyHours"] * 60)))

            # (h) cheatsheet_pdf.py --from-kg --anti-mastery --spec-only (zero Typst required!)
            crib_spec = _cap(cs.main, ["build", "--from-kg", "--kg-dir", str(kg), "--anti-mastery", "--pages", "1", "--spec-only"])
            meta = crib_spec["antiMasteryMeta"]
            self.assertIn("p-rocksolid", meta["excludedSolidIds"])
            self.assertIn("p-misc", meta["includedPropIds"])
            self.assertNotIn("p-rocksolid", meta["includedPropIds"])

            # (i) exam_runner.py in-process `good` credit + 3-bucket autopsy
            kg_ex = t / "kg-ex"
            kg_ex.mkdir()
            (kg_ex / "propositions.jsonl").write_text(
                "\n".join(
                    json.dumps(x)
                    for x in [
                        {"id": "p-solved", "subject": "Physics", "chapter": "SHM", "title": "Solved concept", "status": "shaky", "bktMastery": 0.45},
                        {"id": "p-know", "subject": "Physics", "chapter": "SHM", "title": "Concept gap node", "status": "shaky", "bktMastery": 0.30},
                        {"id": "p-exec", "subject": "Physics", "chapter": "SHM", "title": "Execution slip node", "status": "solid", "bktMastery": 0.80},
                    ]
                ) + "\n",
                encoding="utf-8",
            )
            (kg_ex / "chains.jsonl").write_text("", encoding="utf-8")
            opts = [{"id": "A", "text": "A"}, {"id": "B", "text": "B"}, {"id": "C", "text": "C"}, {"id": "D", "text": "D"}]
            ex_spec = er.normalize_spec({
                "examSlug": "v2-autopsy-test",
                "title": "v2 Autopsy Test",
                "examType": "JEE Main",
                "durationMinutes": 20,
                "questions": [
                    {"id": "Q1", "type": "single_correct", "subject": "Physics", "chapter": "SHM", "propId": "p-solved", "stem": "Q1", "options": opts, "answer": "A"},
                    {"id": "Q2", "type": "single_correct", "subject": "Physics", "chapter": "SHM", "propId": "p-know", "trapCategory": "misconception", "stem": "Q2", "options": opts, "answer": "B"},
                    {"id": "Q3", "type": "single_correct", "subject": "Physics", "chapter": "SHM", "propId": "p-exec", "trapCategory": "algebra_slip", "stem": "Q3", "options": opts, "answer": "C"},
                    {"id": "Q4", "type": "single_correct", "subject": "Physics", "chapter": "SHM", "stem": "Q4", "options": opts, "answer": "D"},
                ],
            })
            ex_attempt = {
                "examSlug": "v2-autopsy-test",
                "timeSpentSec": 500,
                "responses": {
                    "Q1": {"answer": "A", "status": "answered", "timeSpentSec": 40},
                    "Q2": {"answer": "C", "status": "answered", "timeSpentSec": 50},
                    "Q3": {"answer": "A", "status": "answered", "timeSpentSec": 45},
                    "Q4": {"answer": "D", "status": "answered", "timeSpentSec": 300},
                },
            }
            graded = er.grade_attempt(ex_spec, ex_attempt, kg_dir=kg_ex, slow_sec=180, no_sync=True)
            self.assertEqual(graded["srsErrors"], [])
            self.assertGreaterEqual(graded["srsUpdated"], 3)
            ex_props = {
                r["id"]: r
                for r in (json.loads(ln) for ln in (kg_ex / "propositions.jsonl").read_text(encoding="utf-8").splitlines() if ln.strip())
            }
            self.assertGreater(ex_props["p-solved"]["bktMastery"], 0.45, "In-process exam_runner must credit `good` on solved propIds!")
            buckets = graded["autopsy"]["buckets"]
            self.assertIn("Q2", buckets["knowledge"])
            self.assertIn("Q3", buckets["execution"])
            self.assertIn("Q4", buckets["time_sink"])

    def test_scipy_lbfgsb_optimizer_when_available(self) -> None:
        if importlib.util.find_spec("scipy") is None:
            self.skipTest("scipy not installed (bare-stdlib mode)")

        import syntopik.fsrs_core as fc

        # Synthesize 120 chronological revlog rows across 12 cards with slower forgetting than defaults
        base_dt = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
        rows: list[dict[str, Any]] = []
        for step in range(10):
            for c_idx in range(12):
                cid = f"card-{c_idx}"
                ts = (base_dt + timedelta(days=step * 4 + (c_idx % 3), hours=c_idx)).isoformat()
                elapsed = 0.0 if step == 0 else float(4 + (c_idx % 3))
                # Deterministic pattern: longer-retention learner succeeds ~92% of the time
                rating = 1 if (step in (2, 7) and c_idx % 6 == 0) else 3
                rows.append({
                    "ts": ts,
                    "propId": cid,
                    "rating": rating,
                    "elapsedDays": elapsed,
                    "stability": max(0.5, 2.0 + step * 1.5),
                })

        res = fc.optimize_fsrs_params(rows, min_reviews=100, max_iter=30)
        self.assertTrue(res.get("optimized"), f"Optimizer did not run SciPy L-BFGS-B: {res}")
        self.assertEqual(len(res["parameters"]), 21)
        self.assertEqual(len(res["candidateParameters"]), 21)
        self.assertIsInstance(res["defaultOosLogLoss"], float)
        self.assertIsInstance(res["optimizedOosLogLoss"], float)
        self.assertTrue(math.isfinite(res["optimizedOosLogLoss"]))
        self.assertLess(res["optimizedTrainLogLoss"], res["defaultTrainLogLoss"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
