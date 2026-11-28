#!/usr/bin/env python3
"""Syntopik regression suite: v3.2 universal compat, importers and exam readiness.

Shared fixtures live in tests/_helpers.py. Run via:
    python3 -B -m unittest discover -s tests -t tests -v
"""

from __future__ import annotations

import argparse
import contextlib
import http.client
import io
import json
import re
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
import unittest
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from _helpers import (
    ROOT,
    SKILLS_DIR,
    load_script_module,
    parse_yaml_frontmatter,
    run_cli,
)


class TestV32UniversalCompatImportersAndExamReadiness(unittest.TestCase):
    """34. Syntopik v3.2 6-Type CBT/EV-Skip, Blind Interleave, Readiness/Taper/Pre-Mortem, 4 Importers, 14 Agents & Loopback POST /mcp."""

    def test_v32_six_exam_types_zero_leak_distractor_autopsy_and_ev_calibration(self) -> None:
        er = load_script_module("exam_runner.py")
        self.assertEqual(
            er.VALID_QUESTION_TYPES,
            ("single_correct", "multi_correct", "numerical", "matrix_match", "assertion_reason", "vignette_stem"),
        )

        secret_why_wrong = "SECRET_WHY_WRONG_TRAP_94721"
        secret_flat_reason = "SECRET_FLAT_DISTRACTOR_88319"

        raw_spec = {
            "examSlug": "v32-universal-6type-mock",
            "title": "Syntopik v3.2 6-Type Universal CBT Mock",
            "examType": "Universal STEM",
            "durationMinutes": 45,
            "blindChapterLabels": True,
            "vignettes": [
                {
                    "id": "V_TOP",
                    "title": "Top-Level Clinical/Physics Case",
                    "kind": "passage",
                    "body": "A damped harmonic oscillator with mass m=1 kg and k=4 N/m starts from x=2 m.",
                }
            ],
            "questions": [
                # 1. Single Correct (+4/-1, p_be = 0.20) with option-level whyWrong/trapCategory/confusedWith
                {
                    "id": "Q1_SC",
                    "type": "single_correct",
                    "subject": "Physics",
                    "chapter": "Rotational Mechanics",
                    "propId": "phy-rot-sphere-incline",
                    "vignetteId": "V_TOP",
                    "stem": "A solid sphere rolls without slipping down an incline of angle theta. Its acceleration is:",
                    "options": [
                        {
                            "id": "A",
                            "text": "g sin(theta)",
                            "whyWrong": secret_why_wrong,
                            "trapCategory": "condition_missed",
                            "confusedWith": "Frictionless sliding block",
                        },
                        {"id": "B", "text": "(5/7) g sin(theta)"},
                        {"id": "C", "text": "(2/3) g sin(theta)"},
                        {"id": "D", "text": "(1/2) g sin(theta)"},
                    ],
                    "distractorReasons": {"C": secret_flat_reason},
                    "answer": "B",
                    "marksCorrect": 4,
                    "marksWrong": -1,
                    "solution": "Solid sphere I = 2/5 MR^2 -> a = 5/7 g sin(theta).",
                    "trapNote": "Account for rotational inertia I/(MR^2) = 2/5.",
                    "trapCategory": "concept_gap",
                },
                # 2. Assertion-Reason (4opt auto-populated)
                {
                    "id": "Q2_AR4",
                    "type": "assertion_reason",
                    "subject": "Physics",
                    "chapter": "Thermodynamics",
                    "propId": "phy-thermo-adiabatic",
                    "assertion": "In a reversible adiabatic expansion of an ideal gas, temperature decreases.",
                    "reason": "Work is done by the gas at the expense of internal energy since Q = 0.",
                    "answer": "A",
                    "marksCorrect": 4,
                    "marksWrong": -1,
                    "marksUnattempted": 0.5,  # p_be = (1 + 0.5) / (4 + 1) = 0.30!
                    "causalLinkExplanation": "First law dU = -dW directly explains the temperature drop.",
                },
                # 3. Assertion-Reason (5opt)
                {
                    "id": "Q3_AR5",
                    "type": "assertion_reason",
                    "subject": "Chemistry",
                    "chapter": "Electrochemistry",
                    "propId": "chem-elec-intensive",
                    "arVariant": "5opt",
                    "assertion": "Standard cell potential E0 is an intensive property.",
                    "reason": "Doubling the stoichiometric coefficients leaves E0 unchanged.",
                    "answer": "A",
                    "marksCorrect": 4,
                    "marksWrong": -1,
                },
                # 4. Multi-Correct: JEE Adv MSQ (+4/+3/+2/+1/0/-2)
                {
                    "id": "Q4_MSQ_JEE",
                    "type": "multi_correct",
                    "markingProfile": "jee_adv_msq",
                    "subject": "Physics",
                    "chapter": "SHM",
                    "propId": "phy-shm-energy",
                    "stem": "Select all invariants of an ideal linear SHM oscillator:",
                    "options": [
                        {"id": "A", "text": "Total mechanical energy is constant"},
                        {"id": "B", "text": "Period is independent of amplitude"},
                        {"id": "C", "text": "Acceleration is proportional to -x"},
                        {"id": "D", "text": "Speed is maximum at turning points", "whyWrong": "Speed is zero at turning points", "trapCategory": "misconception"},
                    ],
                    "answer": ["A", "B", "C"],
                },
                # 5. Multi-Correct: GATE MSQ (+2/0, partialMarking=False)
                {
                    "id": "Q5_MSQ_GATE",
                    "type": "multi_correct",
                    "markingProfile": "gate_msq",
                    "subject": "CS",
                    "chapter": "Graphs",
                    "propId": "cs-mst-props",
                    "stem": "Which statements hold for Minimum Spanning Trees with distinct edge weights?",
                    "options": [
                        {"id": "A", "text": "The MST is unique"},
                        {"id": "B", "text": "The lightest edge across any cut belongs to the MST"},
                        {"id": "C", "text": "Dijkstra always computes the MST"},
                    ],
                    "answer": ["A", "B"],
                },
                # 6. Multi-Correct: strict_all_or_nothing (+4/-2, partialMarking=False)
                {
                    "id": "Q6_MSQ_STRICT",
                    "type": "multi_correct",
                    "markingProfile": "strict_all_or_nothing",
                    "subject": "Maths",
                    "chapter": "Linear Algebra",
                    "propId": "math-la-orthog",
                    "stem": "For an orthogonal matrix Q:",
                    "options": [
                        {"id": "A", "text": "Q^T Q = I"},
                        {"id": "B", "text": "|det(Q)| = 1"},
                        {"id": "C", "text": "All eigenvalues are real"},
                    ],
                    "answer": ["A", "B"],
                },
                # 7. Matrix Match (grid mode)
                {
                    "id": "Q7_MAT_GRID",
                    "type": "matrix_match",
                    "matrixMode": "grid",
                    "subject": "Physics",
                    "chapter": "Thermodynamics",
                    "propId": "phy-thermo-matrix",
                    "stem": "Match ideal gas processes with invariants:",
                    "rows": [{"id": "P", "text": "Isothermal"}, {"id": "Q", "text": "Isochoric"}, {"id": "R", "text": "Adiabatic"}],
                    "cols": [{"id": "1", "text": "dU=0"}, {"id": "2", "text": "W=0"}, {"id": "3", "text": "Q=0"}],
                    "answer": {"P": ["1"], "Q": ["2"], "R": ["3"]},
                    "marksPerRow": 1,
                    "marksWrong": 0,
                },
                # 8. Matrix Match (code_options mode)
                {
                    "id": "Q8_MAT_CODE",
                    "type": "matrix_match",
                    "matrixMode": "code_options",
                    "subject": "CS",
                    "chapter": "Algorithms",
                    "propId": "cs-algo-complex",
                    "stem": "Match sorting algorithm to worst-case time:",
                    "rows": [{"id": "P", "text": "MergeSort"}, {"id": "Q", "text": "QuickSort"}],
                    "cols": [{"id": "1", "text": "O(n log n)"}, {"id": "2", "text": "O(n^2)"}],
                    "options": [
                        {"id": "A", "text": "P->1, Q->2"},
                        {"id": "B", "text": "P->2, Q->1"},
                    ],
                    "optionRowMap": {
                        "A": {"P": ["1"], "Q": ["2"]},
                        "B": {"P": ["2"], "Q": ["1"]},
                    },
                    "rowAnswer": {"P": ["1"], "Q": ["2"]},
                    "answer": "A",
                    "marksCorrect": 4,
                    "marksWrong": -1,
                },
                # 9. Inline vignette_stem with 2 subQuestions (flattened into spec.questions)
                {
                    "id": "V_INLINE",
                    "type": "vignette_stem",
                    "title": "Distributed Consensus Stem",
                    "kind": "system_design",
                    "subject": "CS",
                    "chapter": "Distributed Systems",
                    "stem": "A 5-node Raft cluster experiences a network partition splitting {N1, N2} from {N3, N4, N5}.",
                    "subQuestions": [
                        {
                            "type": "single_correct",
                            "propId": "cs-raft-quorum",
                            "stem": "Which partition can elect a leader?",
                            "options": [
                                {"id": "A", "text": "{N1, N2}", "whyWrong": "2 < 3 quorum", "trapCategory": "condition_missed"},
                                {"id": "B", "text": "{N3, N4, N5}"},
                                {"id": "C", "text": "Both partitions"},
                                {"id": "D", "text": "Neither partition"},
                            ],
                            "answer": "B",
                            "marksCorrect": 4,
                            "marksWrong": -1,
                        },
                        {
                            "type": "numerical",
                            "propId": "cs-raft-min-quorum",
                            "stem": "What is the minimum quorum size for a 5-node cluster?",
                            "answer": 3,
                            "tolerance": 0.01,
                            "marksCorrect": 4,
                            "marksWrong": -1,
                        },
                    ],
                },
            ],
        }

        spec = er.normalize_spec(raw_spec, inline_media=False)
        q_by_id = {q["id"]: q for q in spec["questions"]}
        # Inline vignette flattened into 2 sub-questions: 8 + 2 = 10 total questions
        self.assertEqual(len(spec["questions"]), 10)
        self.assertIn("V_INLINE", spec["vignettes"])
        self.assertIn("V_TOP", spec["vignettes"])
        self.assertIn("V_INLINE.1", q_by_id)
        self.assertIn("V_INLINE.2", q_by_id)
        self.assertEqual(q_by_id["V_INLINE.1"]["vignetteId"], "V_INLINE")
        self.assertEqual(q_by_id["V_INLINE.1"]["vignetteOrder"], 1)
        self.assertEqual(q_by_id["V_INLINE.2"]["vignetteCount"], 2)
        self.assertEqual(q_by_id["V_INLINE.2"]["subject"], "CS")
        # Assertion-Reason auto-populated options (4opt & 5opt)
        self.assertEqual([o["id"] for o in q_by_id["Q2_AR4"]["options"]], ["A", "B", "C", "D"])
        self.assertEqual([o["id"] for o in q_by_id["Q3_AR5"]["options"]], ["A", "B", "C", "D", "E"])
        # Flat distractorReasons merged onto option C
        opt_c = next(o for o in q_by_id["Q1_SC"]["options"] if o["id"] == "C")
        self.assertEqual(opt_c.get("whyWrong"), secret_flat_reason)

        # Deterministic arErrorKind across all 4 diagnostic failure modes
        self.assertEqual(er.score_question(q_by_id["Q2_AR4"], "B")["arErrorKind"], "causal_link_trap")
        self.assertEqual(er.score_question(q_by_id["Q2_AR4"], "D")["arErrorKind"], "assertion_truth_error")
        self.assertEqual(er.score_question(q_by_id["Q2_AR4"], "C")["arErrorKind"], "reason_truth_error")
        self.assertEqual(er.score_question(q_by_id["Q3_AR5"], "E")["arErrorKind"], "double_truth_flip")

        # Multi-correct marking profiles
        sc_jee_partial = er.score_question(q_by_id["Q4_MSQ_JEE"], ["A", "B"])
        self.assertEqual(sc_jee_partial["status"], "partial")
        self.assertEqual(sc_jee_partial["marks"], 2.0)
        self.assertEqual(sc_jee_partial["missedCorrect"], ["C"])
        self.assertEqual(sc_jee_partial["wrongSelected"], [])

        sc_jee_wrong = er.score_question(q_by_id["Q4_MSQ_JEE"], ["A", "D"])
        self.assertEqual(sc_jee_wrong["status"], "wrong")
        self.assertEqual(sc_jee_wrong["marks"], -2.0)
        self.assertEqual(sc_jee_wrong["wrongSelected"], ["D"])

        sc_gate_partial = er.score_question(q_by_id["Q5_MSQ_GATE"], ["A"])
        self.assertEqual(sc_gate_partial["status"], "wrong")
        self.assertEqual(sc_gate_partial["marks"], 0.0)
        self.assertEqual(sc_gate_partial["negativeLost"], 0.0)

        sc_strict_partial = er.score_question(q_by_id["Q6_MSQ_STRICT"], ["A"])
        self.assertEqual(sc_strict_partial["status"], "wrong")
        self.assertEqual(sc_strict_partial["marks"], -2.0)

        # Matrix match rowResults (grid + code_options)
        sc_mat_grid = er.score_question(q_by_id["Q7_MAT_GRID"], {"P": ["1"], "Q": ["3"]})
        self.assertEqual(sc_mat_grid["status"], "partial")
        self.assertEqual(sc_mat_grid["marks"], 1.0)
        self.assertEqual(sc_mat_grid["rowResults"], {"P": "correct", "Q": "wrong", "R": "blank"})

        sc_mat_code = er.score_question(q_by_id["Q8_MAT_CODE"], "B")
        self.assertEqual(sc_mat_code["status"], "wrong")
        self.assertEqual(sc_mat_code["rowResults"], {"P": "wrong", "Q": "wrong"})

        # Zero-leak strip_presubmit_bundle() & compiled CBT HTML check
        public_bundle, sealed_autopsy = er.strip_presubmit_bundle(spec)
        pub_dump = json.dumps(public_bundle)
        self.assertNotIn(secret_why_wrong, pub_dump, "strip_presubmit_bundle leaked option whyWrong!")
        self.assertNotIn(secret_flat_reason, pub_dump, "strip_presubmit_bundle leaked flat distractorReasons!")
        self.assertNotIn("distractorReasons", pub_dump)
        for q_pub in public_bundle["questions"]:
            for o_pub in q_pub.get("options") or []:
                for forbidden in ("whyWrong", "trapCategory", "confusedWith"):
                    self.assertNotIn(forbidden, o_pub, f"Option leaked {forbidden}")
        self.assertEqual(sealed_autopsy["Q1_SC"]["A"]["whyWrong"], secret_why_wrong)
        self.assertEqual(sealed_autopsy["Q1_SC"]["A"]["trapCategory"], "condition_missed")

        with tempfile.TemporaryDirectory(prefix="synt-v32-exam-") as tmp:
            t = Path(tmp)
            kg = t / "knowledge-graph"
            kg.mkdir(parents=True)
            spec_file = t / "spec.json"
            spec_file.write_text(json.dumps(raw_spec), encoding="utf-8")
            html_out = t / "exam.html"
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                er.cmd_build(argparse.Namespace(spec=str(spec_file), out=str(html_out), no_sync=True))
            html_txt = html_out.read_text(encoding="utf-8")
            self.assertNotIn(secret_why_wrong, html_txt, "Compiled CBT HTML leaked plaintext whyWrong!")
            self.assertNotIn(secret_flat_reason, html_txt, "Compiled CBT HTML leaked plaintext flat distractor!")

            # Grade attempt testing exact EV-skip math, distractor autopsy, mistakes.jsonl & vignetteBreakdown
            attempt = {
                "schemaVersion": 1,
                "examSlug": spec["examSlug"],
                "timeSpentSec": 900,
                "responses": {
                    # Q1 (+4/-1, p_be=0.20): picked wrong A with low confidence 0.15 -> neg_ev_gamble!
                    "Q1_SC": {"answer": "A", "status": "answered", "timeSpentSec": 80, "confidence": 0.15},
                    # Q2 (+4/-1, U=0.5, p_be=0.30): unattempted with confidence 0.60 > 0.30 -> underconfident_skip!
                    # EV(attempt) = 0.60*4 - 0.40*1 = 2.0; marks left = 2.0 - 0.5 = 1.5
                    "Q2_AR4": {"answer": None, "status": "not_answered", "timeSpentSec": 45, "confidence": 0.60},
                    # Q3: wrong B (causal_link_trap) with high confidence 0.80 -> positive_ev attempt
                    "Q3_AR5": {"answer": "B", "status": "answered", "timeSpentSec": 50, "confidence": 0.80},
                    # Q4: partial +2
                    "Q4_MSQ_JEE": {"answer": ["A", "B"], "status": "answered", "timeSpentSec": 90, "confidence": 0.85},
                    # Q5: correct +2
                    "Q5_MSQ_GATE": {"answer": ["A", "B"], "status": "answered", "timeSpentSec": 60, "confidence": 0.90},
                    # Q6: unattempted with confidence 0.10 < p_be -> rational_skip
                    "Q6_MSQ_STRICT": {"answer": [], "status": "not_answered", "timeSpentSec": 30, "confidence": 0.10},
                    # Q7: partial grid
                    "Q7_MAT_GRID": {"answer": {"P": ["1"], "Q": ["3"]}, "status": "answered", "timeSpentSec": 70},
                    # Q8: correct code_options
                    "Q8_MAT_CODE": {"answer": "A", "status": "answered", "timeSpentSec": 40, "confidence": 0.90},
                    # V_INLINE.1 & .2 both wrong -> whole_vignette_failure!
                    "V_INLINE.1": {"answer": "A", "status": "answered", "timeSpentSec": 55, "confidence": 0.70},
                    "V_INLINE.2": {"answer": "2", "status": "answered", "timeSpentSec": 40, "confidence": 0.65},
                },
            }

            report = er.grade_attempt(spec, attempt, kg_dir=kg, no_log=False, no_srs=False, no_sync=True)
            evc = report["evCalibration"]
            self.assertEqual(evc["evBasis"], "confidence_exact")
            pq_ev = {row["id"]: row for row in evc["perQuestion"]}
            self.assertAlmostEqual(pq_ev["Q1_SC"]["pBreakEven"], 0.20, places=4)
            self.assertEqual(pq_ev["Q1_SC"]["verdict"], "neg_ev_gamble")
            self.assertAlmostEqual(pq_ev["Q2_AR4"]["pBreakEven"], 0.30, places=4)
            self.assertEqual(pq_ev["Q2_AR4"]["verdict"], "underconfident_skip")
            self.assertEqual(pq_ev["Q6_MSQ_STRICT"]["verdict"], "rational_skip")
            self.assertEqual(evc["negEvGamblesCount"], 1)
            self.assertAlmostEqual(evc["marksLostToNegEv"], 1.0, places=2)
            self.assertEqual(evc["underconfidentSkipsCount"], 1)
            self.assertAlmostEqual(evc["marksLeftOnTable"], 1.5, places=2)
            self.assertGreaterEqual(evc["evDisciplineScore"], 0.0)
            self.assertLessEqual(evc["evDisciplineScore"], 100.0)

            # Distractor autopsy + mistakes.jsonl enrichment
            dsum = report["distractorSummary"]
            self.assertGreaterEqual(dsum["triggeredCount"], 2)
            q1_det = next(q for q in report["questions"] if q["id"] == "Q1_SC")
            self.assertEqual(q1_det["distractorAutopsy"][0]["optionId"], "A")
            self.assertEqual(q1_det["distractorAutopsy"][0]["whyWrong"], secret_why_wrong)
            mistakes_rows = [
                json.loads(ln)
                for ln in (kg / "mistakes.jsonl").read_text(encoding="utf-8").splitlines()
                if ln.strip()
            ]
            q1_mistake = next((m for m in mistakes_rows if m.get("sourceRef") == f"exam:{spec['examSlug']}:Q1_SC:A"), None)
            self.assertIsNotNone(q1_mistake, f"Expected option-level mistake for Q1_SC:A in {mistakes_rows}")
            assert q1_mistake is not None
            self.assertEqual(q1_mistake.get("confusedWith"), "Frictionless sliding block")
            self.assertEqual(q1_mistake.get("category"), "condition_missed")

            # Vignette breakdown
            vbd = report["vignetteBreakdown"]
            self.assertIn("V_INLINE", vbd)
            self.assertEqual(vbd["V_INLINE"]["pattern"], "whole_vignette_failure")
            self.assertEqual(vbd["V_INLINE"]["wrong"], 2)

            # Elimination-derived EV basis check (strike out 2 of 4 options -> p_elim = 0.5 > 0.20)
            elim_attempt = {
                "schemaVersion": 1,
                "examSlug": spec["examSlug"],
                "responses": {
                    "Q1_SC": {"answer": None, "status": "not_answered", "timeSpentSec": 30, "eliminated": ["A", "D"]},
                },
            }
            rep_elim = er.grade_attempt(spec, elim_attempt, kg_dir=kg, no_log=True, no_srs=True, no_sync=True)
            self.assertEqual(rep_elim["evCalibration"]["evBasis"], "elimination_derived")
            self.assertEqual(rep_elim["evCalibration"]["underconfidentSkipsCount"], 1)

    def test_v32_blind_interleave_readiness_taper_premortem_and_brainstem(self) -> None:
        import syntopik.brainstem as bs
        import syntopik.interleave as ilv
        import syntopik.learner_model as lm
        import syntopik.readiness as rd

        er = load_script_module("exam_runner.py")

        with tempfile.TemporaryDirectory(prefix="synt-v32-prep-") as tmp:
            ws = Path(tmp) / "ws"
            kg = ws / "knowledge-graph"
            ch = ws / "chapters"
            kg.mkdir(parents=True)
            ch.mkdir(parents=True)

            # 1. Empty vault -> STRICT non-fabrication (`bullets: []`, `sourceBasis: "cold_start_minimal"`)
            empty_shield = rd.build_premortem_shield(ws, kg_dir=kg)
            self.assertEqual(empty_shield["sourceBasis"], "cold_start_minimal")
            self.assertEqual(empty_shield["bullets"], [])
            self.assertEqual(empty_shield["count"], 0)
            empty_rd = rd.compute_readiness(ws, kg_dir=kg, chapters_dir=ch)
            self.assertTrue(empty_rd["ok"])
            self.assertEqual(empty_rd["preMortemShield"]["bullets"], [])
            self.assertEqual(empty_rd["preMortemShield"]["sourceBasis"], "cold_start_minimal")
            self.assertEqual(empty_rd["readinessIndex"], 0.0)
            self.assertEqual(empty_rd["band"], "needs_foundation")

            # 2. Cue stripping check
            stripped = ilv.strip_chapter_cues(
                "[SHM] Chapter 2: In Simple Harmonic Motion (SHM), find period for phy-shm-period.",
                chapter="Simple Harmonic Motion (SHM)",
                prop_id="phy-shm-period",
            )
            self.assertNotIn("SHM", stripped)
            self.assertNotIn("Chapter 2", stripped)
            self.assertNotIn("phy-shm-period", stripped)

            # 3. Populate multi-chapter vault with FSRS cards, chains, due fork, mistakes & mock attempt
            now_utc = datetime.now(timezone.utc)
            last_rev = (now_utc - timedelta(days=2)).isoformat()
            props = [
                {
                    "id": "phy-shm-period",
                    "subject": "Physics",
                    "chapter": "Simple Harmonic Motion (SHM)",
                    "title": "Spring-Block Angular Frequency Invariant",
                    "statement": "Angular frequency squared equals stiffness over effective mass.",
                    "status": "solid",
                    "bktMastery": 0.90,
                    "nextReview": "2099-01-01",
                    "fsrs": {"stability": 6.0, "difficulty": 4.2, "last_review": last_rev},
                    "cues": [{"id": "c1", "q": "[SHM] L1: A block on a spring oscillates in SHM; how does period scale with mass?", "a": "T = 2*pi*sqrt(m/k)"}],
                },
                {
                    "id": "phy-shm-energy",
                    "subject": "Physics",
                    "chapter": "Simple Harmonic Motion (SHM)",
                    "title": "Turning-Point Energy Partition",
                    "statement": "Total mechanical energy equals half k A squared.",
                    "status": "solid",
                    "bktMastery": 0.88,
                    "nextReview": "2099-01-01",
                    "fsrs": {"stability": 14.0, "difficulty": 3.5, "last_review": last_rev},
                    "cues": [{"id": "c1", "q": "A block on a spring has amplitude A; what is its speed at x = A/2?", "a": "sqrt(3)/2 * omega * A"}],
                },
                {
                    "id": "phy-rot-incline",
                    "subject": "Physics",
                    "chapter": "Rotational Mechanics",
                    "title": "Rolling Without Slipping Constraint",
                    "statement": "Static friction enforces a_cm = alpha * R without dissipating energy.",
                    "status": "misconception",
                    "bktMastery": 0.25,
                    "nextReview": "2020-01-01",
                    "fsrs": {"stability": 1.2, "difficulty": 7.0, "last_review": last_rev},
                    "cues": [{"id": "c1", "q": "[Rotational Mechanics] A solid sphere rolls down an incline; why is static friction non-dissipative?", "a": "Instantaneous contact velocity is zero."}],
                },
                {
                    "id": "phy-rot-angmom",
                    "subject": "Physics",
                    "chapter": "Rotational Mechanics",
                    "title": "Angular Momentum Conservation About Pivot",
                    "statement": "When net external torque about a pivot vanishes, L about that pivot is conserved.",
                    "status": "solid",
                    "bktMastery": 0.86,
                    "nextReview": "2099-01-01",
                    "fsrs": {"stability": 8.0, "difficulty": 4.5, "last_review": last_rev},
                    "cues": [{"id": "c1", "q": "A bullet strikes a hinged rod; which quantity is conserved during impact?", "a": "Angular momentum about the hinge."}],
                },
                {
                    "id": "phy-elec-gauss",
                    "subject": "Physics",
                    "chapter": "Electrostatics",
                    "title": "Gauss Flux Symmetry Law",
                    "statement": "Closed-surface electric flux equals enclosed charge over epsilon_0.",
                    "status": "solid",
                    "bktMastery": 0.91,
                    "nextReview": "2099-01-01",
                    "fsrs": {"stability": 10.0, "difficulty": 4.0, "last_review": last_rev},
                    "cues": [{"id": "c1", "q": "[Electrostatics] Compute flux through a symmetric shell enclosing charge Q.", "a": "Q / epsilon_0"}],
                },
                {
                    "id": "phy-elec-cap",
                    "subject": "Physics",
                    "chapter": "Electrostatics",
                    "title": "Dielectric Capacitor Boundary Invariant",
                    "statement": "Isolated capacitor holds Q constant; battery-connected holds V constant.",
                    "status": "shaky",
                    "bktMastery": 0.50,
                    "nextReview": "2020-01-01",
                    "fsrs": {"stability": 2.5, "difficulty": 6.0, "last_review": last_rev},
                    "cues": [{"id": "c1", "q": "Inserting a dielectric slab into an isolated charged capacitor changes stored energy how?", "a": "Energy decreases by factor kappa."}],
                },
            ]
            (kg / "propositions.jsonl").write_text("\n".join(json.dumps(p) for p in props) + "\n", encoding="utf-8")
            (kg / "chains.jsonl").write_text(
                json.dumps({"premises": ["phy-rot-incline"], "conclusion": "phy-rot-angmom", "relation": "requires", "encompassWeight": 0.7}) + "\n",
                encoding="utf-8",
            )
            (kg / "forks.jsonl").write_text(
                json.dumps({
                    "id": "fk-rot-01",
                    "propId": "phy-rot-incline",
                    "subject": "Physics",
                    "chapter": "Rotational Mechanics",
                    "status": "due",
                    "lapses": 2,
                    "winCount": 0,
                    "nextReview": "2020-01-01",
                    "forkPrompt": "Cylinder starts rolling on rough incline — assume f = mu*N?",
                    "sealedWinningMove": "Solve a = alpha*R first and check f <= mu*N.",
                    "refutationMsg": "Static friction is self-adjusting, not automatically mu*N.",
                }) + "\n",
                encoding="utf-8",
            )
            (kg / "mistakes.jsonl").write_text(
                "\n".join(
                    json.dumps({
                        "id": f"m-rot-{i}",
                        "date": "2026-11-20",
                        "subject": "Physics",
                        "chapter": "Rotational Mechanics",
                        "propId": "phy-rot-incline",
                        "category": "condition_missed",
                        "triggerCue": "Rolling incline static friction magnitude",
                        "wrongMove": "Substituted f = mu*N directly",
                        "correctMove": "Verify rolling constraint f <= mu*N first",
                        "confusedWith": "Electrostatics",
                    })
                    for i in range(3)
                ) + "\n",
                encoding="utf-8",
            )
            (kg / "exam-attempts.jsonl").write_text(
                json.dumps({
                    "id": "ex-mock-1",
                    "date": "2026-11-22",
                    "examSlug": "mini-phys",
                    "score": 28,
                    "maxScore": 40,
                    "percentage": 70.0,
                    "negativeMarksLost": 4.0,
                    "evCalibration": {"negEvGamblesCount": 2, "marksLostToNegEv": 2.0, "evDisciplineScore": 72.0},
                }) + "\n",
                encoding="utf-8",
            )

            # 4. Blind-Chapter Interleave: zero blocked runs, cue strip, zero leak, Step-1 before Step-2, CBT export
            built = ilv.build_interleave_set(ws, n=6, seed=42, kg_dir=kg)
            self.assertTrue(built["ok"])
            self.assertEqual(built["count"], 6)
            self.assertGreaterEqual(built["distinctChaptersCount"], 2)
            self.assertTrue(built["blinded"])
            pub_dump = json.dumps(built["items"])
            for sealed_key in ("sealedChapter", "sealedPrinciple", "sealedPrincipleId", "sealedAnswer", "sealedPropId", "sealedStep2Prompt"):
                self.assertNotIn(sealed_key, pub_dump, f"Public interleave payload leaked {sealed_key}!")
            for it in built["items"]:
                self.assertNotIn("[SHM]", it["prompt"])
                self.assertNotIn("[Rotational Mechanics]", it["prompt"])
                self.assertNotIn("[Electrostatics]", it["prompt"])

            # Verify zero blocked runs in sealed session on disk
            sess_rows = [json.loads(ln) for ln in (kg / ilv.INTERLEAVE_SESSIONS_FILENAME).read_text(encoding="utf-8").splitlines() if ln.strip()]
            sealed_items = sess_rows[-1]["items"]
            for i in range(1, len(sealed_items)):
                self.assertNotEqual(
                    sealed_items[i]["sealedChapter"],
                    sealed_items[i - 1]["sealedChapter"],
                    f"Blocked consecutive chapter run at index {i}: {[x['sealedChapter'] for x in sealed_items]}",
                )

            # Step-1 check BEFORE Step-2 reveal: I1 correct, I2 wrong cross-chapter decoy
            s_i1 = sealed_items[0]
            chk1 = ilv.check_principle_step(
                ws,
                session_id=built["sessionId"],
                item_id=s_i1["itemId"],
                selected_principle=s_i1["sealedPrincipleId"],
                confidence=0.85,
                duration_ms=4200,
                kg_dir=kg,
            )
            self.assertTrue(chk1["ok"])
            self.assertTrue(chk1["correct"])
            self.assertTrue(chk1["step2Unlocked"])
            self.assertNotIn("sealedAnswer", chk1)
            self.assertEqual(chk1["revealedChapter"], s_i1["sealedChapter"])

            s_i2 = sealed_items[1]
            wrong_opt = next(
                c["id"] for c in s_i2["principleChoices"]
                if not c.get("isDontKnow") and c["id"] != s_i2["sealedPrincipleId"]
            )
            chk2 = ilv.check_principle_step(
                ws,
                session_id=built["sessionId"],
                item_id=s_i2["itemId"],
                selected_principle=wrong_opt,
                confidence=0.60,
                duration_ms=6100,
                kg_dir=kg,
            )
            self.assertTrue(chk2["ok"])
            self.assertFalse(chk2["correct"])
            self.assertEqual(chk2["tier1CheckedCount"], 2)
            self.assertAlmostEqual(chk2["tier1AccuracyPct"], 50.0, places=1)

            comp = ilv.complete_interleave_session(
                ws,
                session_id=built["sessionId"],
                results={s_i1["itemId"]: {"solved": True}, s_i2["itemId"]: {"solved": False}},
                kg_dir=kg,
            )
            self.assertTrue(comp["ok"])
            self.assertEqual(comp["status"], "completed")
            self.assertEqual(comp["tier1Correct"], 1)
            self.assertEqual(comp["tier1Total"], 2)
            self.assertTrue(comp["confusionMatrix"])
            self.assertGreaterEqual(comp["mistakesLogged"], 1)

            stats = ilv.get_interleave_stats(ws, kg_dir=kg)
            self.assertEqual(stats["completedSessions"], 1)
            self.assertEqual(stats["totalItemsChecked"], 2)

            cbt_spec = ilv.export_interleave_cbt_spec(ws, n=4, seed=42, kg_dir=kg)
            self.assertTrue(cbt_spec["blindChapterLabels"])
            norm_cbt = er.normalize_spec(cbt_spec, inline_media=False)
            self.assertEqual(len(norm_cbt["questions"]), 4)

            # 5. Readiness Forecaster, FSRS projection, ROI-sorted topLevers, Taper & Pre-Mortem Shield
            near_exam = (now_utc + timedelta(days=3)).date().isoformat()
            far_exam = (now_utc + timedelta(days=30)).date().isoformat()
            rd_near = rd.compute_readiness(ws, exam_date=near_exam, max_marks=100, today=now_utc, kg_dir=kg, chapters_dir=ch)
            rd_far = rd.compute_readiness(ws, exam_date=far_exam, max_marks=100, today=now_utc, kg_dir=kg, chapters_dir=ch)
            self.assertTrue(rd_near["ok"])
            self.assertGreater(
                rd_near["pillars"]["projectedRetention"],
                rd_far["pillars"]["projectedRetention"],
                "FSRS exam-day retention should decay over longer horizons!",
            )
            levers = rd_near["topLevers"]
            self.assertGreaterEqual(len(levers), 2)
            self.assertLessEqual(len(levers), 3)
            for i in range(1, len(levers)):
                self.assertGreaterEqual(
                    levers[i - 1]["roiMarksPerHour"],
                    levers[i]["roiMarksPerHour"],
                    f"topLevers not sorted descending by roiMarksPerHour: {levers}",
                )
            shield = rd_near["preMortemShield"]
            self.assertEqual(shield["sourceBasis"], "observed_logs")
            self.assertGreaterEqual(len(shield["bullets"]), 1)
            self.assertLessEqual(len(shield["bullets"]), 3)
            for b in shield["bullets"]:
                self.assertTrue(b["ifCue"].startswith("IF "), b)
                self.assertTrue(b["thenAction"].startswith("THEN "), b)

            # Taper mode transitions: none (>72h), taper_72h (48h), final_24h (12h)
            t_none = rd.detect_taper_mode(kg, now=now_utc, exam_date=(now_utc + timedelta(hours=120)).isoformat())
            self.assertFalse(t_none["active"])
            self.assertEqual(t_none["phase"], "none")
            self.assertTrue(t_none["allowNewConcepts"])

            t_72 = rd.detect_taper_mode(kg, now=now_utc, exam_date=(now_utc + timedelta(hours=48)).isoformat())
            self.assertTrue(t_72["active"])
            self.assertEqual(t_72["phase"], "taper_72h")
            self.assertFalse(t_72["allowNewConcepts"])
            self.assertGreaterEqual(t_72["sleepFloorHours"], 7.0)

            t_24 = rd.detect_taper_mode(kg, now=now_utc, exam_date=(now_utc + timedelta(hours=12)).isoformat())
            self.assertTrue(t_24["active"])
            self.assertEqual(t_24["phase"], "final_24h")
            self.assertFalse(t_24["allowNewConcepts"])
            self.assertEqual(t_24["maxSessionMinutes"], 60)

            # 6. Brainstem routing: pre_exam_taper & blind_interleave (both <= 180 tokens)
            ws_bs = Path(tmp) / "ws-bs"
            kg_bs = ws_bs / "knowledge-graph"
            ch_bs = ws_bs / "chapters"
            kg_bs.mkdir(parents=True)
            ch_bs.mkdir(parents=True)
            solid_two_ch = [
                {"id": "p-ch1-a", "subject": "Physics", "chapter": "SHM", "title": "SHM Period", "status": "solid", "bktMastery": 0.9, "nextReview": "2099-01-01"},
                {"id": "p-ch2-b", "subject": "Physics", "chapter": "Optics", "title": "Snell Law", "status": "solid", "bktMastery": 0.9, "nextReview": "2099-01-01"},
            ]
            (kg_bs / "propositions.jsonl").write_text("\n".join(json.dumps(p) for p in solid_two_ch) + "\n", encoding="utf-8")
            (kg_bs / "chains.jsonl").write_text("", encoding="utf-8")
            prof_bs = lm.load_profile(kg_bs)
            lm.apply_tier1_calibration(prof_bs, entry_ramp="balanced", challenge_appetite="balanced", prose_density="standard")
            lm.save_profile(kg_bs, prof_bs)
            lm.record_telemetry_event(kg_bs, domain="Physics", prop_id="p-ch1-a", correct=True, confidence=0.90, duration_ms=8000)

            # 6a. pre_exam_taper when examDate is 30h away
            (kg_bs / "study-config.json").write_text(
                json.dumps({"examDate": (now_utc + timedelta(hours=30)).isoformat()}),
                encoding="utf-8",
            )
            env_taper = bs.plan_next_move(ws_bs)
            self.assertEqual(env_taper["recommendedMode"], "pre_exam_taper", env_taper)
            self.assertEqual(env_taper["exactCmd"], "syntopik readiness --json")
            self.assertLessEqual(bs._envelope_token_count(env_taper), 180)
            self.assertTrue(any("taper" in a.lower() for a in env_taper.get("alerts") or []))

            # 6b. blind_interleave when NOT tapering and >= 2 chapters exist
            (kg_bs / "study-config.json").write_text(
                json.dumps({"examDate": (now_utc + timedelta(days=30)).isoformat()}),
                encoding="utf-8",
            )
            env_ilv = bs.plan_next_move(ws_bs)
            self.assertEqual(env_ilv["recommendedMode"], "blind_interleave", env_ilv)
            self.assertEqual(env_ilv["exactCmd"], "syntopik interleave build --n 6 --json")
            self.assertEqual(env_ilv["skillHint"], "practice-drill")
            self.assertLessEqual(bs._envelope_token_count(env_ilv), 180)

    def test_v32_four_student_importers_security_and_auto_ingest(self) -> None:
        import syntopik.importers as imp
        import syntopik.ingest as ing

        def _make_classic_anki_db(db_path: Path) -> None:
            conn = sqlite3.connect(str(db_path))
            cur = conn.cursor()
            cur.execute("CREATE TABLE col (models TEXT, decks TEXT)")
            cur.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY, guid TEXT, mid INTEGER, tags TEXT, flds TEXT)")
            cur.execute("CREATE TABLE cards (id INTEGER PRIMARY KEY, nid INTEGER, did INTEGER)")
            models = {
                "1": {"name": "Basic", "type": 0, "flds": [{"name": "Front"}, {"name": "Back"}]},
                "2": {"name": "Cloze", "type": 1, "flds": [{"name": "Text"}, {"name": "Extra"}]},
            }
            decks = {"10": {"name": "Physics::Mechanics"}}
            cur.execute("INSERT INTO col VALUES (?, ?)", (json.dumps(models), json.dumps(decks)))
            cur.execute(
                "INSERT INTO notes VALUES (?, ?, ?, ?, ?)",
                (1, "g-basic", 1, " newton force ", "<script>alert(1)</script><b>Newton 2nd Law</b> \\(F=ma\\) [sound:rec.mp3]\x1fNet force equals rate of change of momentum."),
            )
            cur.execute(
                "INSERT INTO notes VALUES (?, ?, ?, ?, ?)",
                (2, "g-cloze", 2, " gauss ", "{{c1::Gauss's Law::flux}} relates electric flux to enclosed charge.\x1fValid for any closed surface."),
            )
            cur.execute(
                "INSERT INTO notes VALUES (?, ?, ?, ?, ?)",
                (3, "g-stub", 1, "", "Please update to the latest Anki version\x1fStub"),
            )
            cur.execute("INSERT INTO cards VALUES (101, 1, 10)")
            cur.execute("INSERT INTO cards VALUES (102, 2, 10)")
            cur.execute("INSERT INTO cards VALUES (103, 3, 10)")
            conn.commit()
            conn.close()

        def _make_modern_anki_db(db_path: Path) -> None:
            conn = sqlite3.connect(str(db_path))
            cur = conn.cursor()
            cur.execute("CREATE TABLE notetypes (id INTEGER PRIMARY KEY, name TEXT)")
            cur.execute("CREATE TABLE fields (ntid INTEGER, ord INTEGER, name TEXT)")
            cur.execute("CREATE TABLE decks (id INTEGER PRIMARY KEY, name TEXT)")
            cur.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY, guid TEXT, mid INTEGER, tags TEXT, flds TEXT)")
            cur.execute("CREATE TABLE cards (id INTEGER PRIMARY KEY, nid INTEGER, did INTEGER)")
            cur.execute("INSERT INTO notetypes VALUES (11, 'Basic')")
            cur.execute("INSERT INTO fields VALUES (11, 0, 'Front'), (11, 1, 'Back')")
            cur.execute("INSERT INTO decks VALUES (20, 'CS\x1fSystems')")
            cur.execute("INSERT INTO notes VALUES (10, 'g-mod-1', 11, ' raft ', 'What is a Raft quorum?\x1fStrict majority floor(N/2)+1.')")
            cur.execute("INSERT INTO cards VALUES (201, 10, 20)")
            conn.commit()
            conn.close()

        with tempfile.TemporaryDirectory(prefix="synt-v32-imp-") as tmp:
            t = Path(tmp)
            ws = t / "ws"
            kg = ws / "knowledge-graph"
            kg.mkdir(parents=True)

            # 1a. Anki classic collection.anki2 + HTML/MathJax + Cloze + stub skip + zero fake mastery
            db_classic = t / "classic.sqlite"
            _make_classic_anki_db(db_classic)
            apkg_classic = t / "mechanics.apkg"
            with zipfile.ZipFile(apkg_classic, "w") as zf:
                zf.write(db_classic, "collection.anki2")

            r_anki2 = imp.import_anki_apkg(apkg_classic, workspace=ws, kg_dir=kg, subject="Physics")
            self.assertTrue(r_anki2["ok"])
            self.assertEqual(r_anki2["format"], "anki2")
            self.assertEqual(r_anki2["importedCount"], 2)
            self.assertEqual(r_anki2["skippedStubCount"], 1)
            for p in r_anki2["propositions"]:
                self.assertEqual(p["status"], "planned")
                self.assertEqual(p["reps"], 0)
                self.assertEqual(p["lapses"], 0)
                self.assertEqual(p["bktMastery"], 0.1)
                for forbidden in ("fsrs", "nextReview", "lastTested", "confidence", "revlog"):
                    self.assertNotIn(forbidden, p, f"Fake mastery key {forbidden} leaked on Anki import!")
            basic_p = next(p for p in r_anki2["propositions"] if p["kind"] == "recall")
            cloze_p = next(p for p in r_anki2["propositions"] if p["kind"] == "cloze")
            self.assertNotIn("<script>", basic_p["cues"][0]["q"])
            self.assertNotIn("alert(1)", basic_p["cues"][0]["q"])
            self.assertNotIn("[sound:", basic_p["cues"][0]["q"])
            self.assertIn("$F=ma$", basic_p["cues"][0]["q"])
            self.assertIn("{{c1::Gauss's Law::flux}}", cloze_p["cues"][0]["q"])

            # 1b. Modern collection.anki21 + non-destructive dedup preserving existing mastery
            db_mod = t / "modern.sqlite"
            _make_modern_anki_db(db_mod)
            apkg_mod = t / "systems.apkg"
            with zipfile.ZipFile(apkg_mod, "w") as zf:
                zf.write(db_mod, "collection.anki21")

            r_anki21 = imp.import_anki_apkg(apkg_mod, workspace=ws, kg_dir=kg, subject="CS")
            self.assertTrue(r_anki21["ok"])
            self.assertEqual(r_anki21["format"], "anki21")
            self.assertEqual(r_anki21["schemaStyle"], "modern")
            self.assertEqual(r_anki21["importedCount"], 1)
            raft_id = r_anki21["propositions"][0]["id"]

            # Promote imported node to solid + fsrs, then re-import and confirm ZERO overwrite
            disk_props = [json.loads(ln) for ln in (kg / "propositions.jsonl").read_text(encoding="utf-8").splitlines() if ln.strip()]
            for dp in disk_props:
                if dp["id"] == raft_id:
                    dp["status"] = "solid"
                    dp["fsrs"] = {"stability": 15.0, "difficulty": 3.0}
            (kg / "propositions.jsonl").write_text("\n".join(json.dumps(dp) for dp in disk_props) + "\n", encoding="utf-8")
            r_anki21_dup = imp.import_anki_apkg(apkg_mod, workspace=ws, kg_dir=kg, subject="CS")
            self.assertEqual(r_anki21_dup["importedCount"], 0)
            self.assertEqual(r_anki21_dup["deduplicatedCount"], 1)
            disk_after = {json.loads(ln)["id"]: json.loads(ln) for ln in (kg / "propositions.jsonl").read_text(encoding="utf-8").splitlines() if ln.strip()}
            self.assertEqual(disk_after[raft_id]["status"], "solid")
            self.assertEqual(disk_after[raft_id]["fsrs"]["stability"], 15.0)

            # 1c. collection.anki21b graceful error (without importing dummy stub)
            apkg_21b = t / "modern_zstd.apkg"
            with zipfile.ZipFile(apkg_21b, "w") as zf:
                zf.writestr("collection.anki21b", b"NOT_VALID_ZSTD_PAYLOAD_21B")
            r_21b = imp.import_anki_apkg(apkg_21b, workspace=ws, kg_dir=kg)
            self.assertFalse(r_21b["ok"])
            self.assertEqual(r_21b["format"], "anki21b_zstd")
            self.assertIn("Support older Anki versions", r_21b["error"])

            # 1d. Zip-slip .apkg blocked
            apkg_slip = t / "slip.apkg"
            with zipfile.ZipFile(apkg_slip, "w") as zf:
                zf.writestr("../escape.txt", "boom")
                zf.write(db_mod, "collection.anki21")
            with self.assertRaises(ValueError):
                imp.import_anki_apkg(apkg_slip, workspace=ws, kg_dir=kg)

            # 2. CSV/TSV Importer: UTF-8 BOM + Anki `#separator:` directives + Quizlet tab export
            anki_tsv = t / "anki-export.tsv"
            anki_tsv.write_bytes(
                b"\xef\xbb\xbf"
                + (
                    "#separator:tab\n"
                    "#html:true\n"
                    "#tags column:3\n"
                    "#deck:Optics\n"
                    "<b>Snell's Law</b>\tn1*sin(theta1) = n2*sin(theta2)\toptics refraction\n"
                    "{{c1::Brewster angle}} gives polarized reflection\tatan(n2/n1)\toptics polarization\n"
                ).encode("utf-8")
            )
            r_tsv = imp.import_delimited_cards(anki_tsv, workspace=ws, kg_dir=kg, subject="Physics")
            self.assertTrue(r_tsv["ok"])
            self.assertEqual(r_tsv["delimiter"], "\t")
            self.assertEqual(r_tsv["importedCount"], 2)
            self.assertEqual(r_tsv["propositions"][0]["chapter"], "Optics")
            self.assertIn("refraction", r_tsv["propositions"][0]["tags"])
            self.assertEqual(r_tsv["propositions"][1]["kind"], "cloze")

            quizlet_tsv = t / "quizlet-terms.tsv"
            quizlet_tsv.write_text(
                "Entropy\tMeasure of accessible microstates S = k_B ln(Omega)\n"
                "Enthalpy\tState function H = U + PV\n",
                encoding="utf-8",
            )
            r_qz = imp.import_delimited_cards(quizlet_tsv, workspace=ws, kg_dir=kg, subject="Chemistry")
            self.assertTrue(r_qz["ok"])
            self.assertFalse(r_qz["hasHeader"])
            self.assertEqual(r_qz["delimiter"], "\t")
            self.assertEqual(r_qz["importedCount"], 2)

            # 3. Jupyter Notebook `.ipynb`: AST-only zero execution canary + `code-tests.json` export
            canary = t / "ipynb-exec-canary.txt"
            nb_path = t / "binary_search.ipynb"
            nb_payload = {
                "nbformat": 4,
                "nbformat_minor": 5,
                "metadata": {"kernelspec": {"language": "python"}},
                "cells": [
                    {
                        "cell_type": "markdown",
                        "source": [
                            "# Binary Search Mastery\n",
                            "## Loop Invariant\n",
                            "The target index always remains inside [lo, hi] across every halving iteration.\n",
                            "**Termination Bound**: At most floor(log2(n)) + 1 comparisons.\n",
                            "![img](data:image/png;base64," + ("A" * 120) + ")\n",
                        ],
                    },
                    {
                        "cell_type": "code",
                        "source": [
                            f"open({str(canary)!r}, 'w').write('EXECUTED')\n",
                            "def double_val(x):\n",
                            "    \"\"\"Return twice the input integer.\"\"\"\n",
                            "    return 2 * x\n",
                            "assert double_val(4) == 8\n",
                            "assert math.isclose(double_val(1.5), 3.0, abs_tol=1e-6)\n",
                            f"assert __import__('os').system({f'touch {canary}'!r}) == 0\n",
                        ],
                        "outputs": [],
                    },
                ],
            }
            nb_path.write_text(json.dumps(nb_payload), encoding="utf-8")
            r_nb = imp.import_jupyter_notebook(nb_path, workspace=ws, kg_dir=kg, subject="CS")
            self.assertTrue(r_nb["ok"])
            self.assertFalse(canary.exists(), "SECURITY VIOLATION: .ipynb importer executed code cell!")
            self.assertGreaterEqual(r_nb["strippedBase64Count"], 1)
            self.assertEqual(r_nb["extractedCasesCount"], 2)
            self.assertGreaterEqual(r_nb["unparsedAssertsCount"], 1)
            ct_path = Path(r_nb["codeTestsPath"])
            self.assertTrue(ct_path.is_file())
            ct_data = json.loads(ct_path.read_text(encoding="utf-8"))
            self.assertEqual(ct_data["entry"], "double_val")
            self.assertEqual(len(ct_data["cases"]), 2)
            self.assertEqual(ct_data["cases"][0]["expected"], 8)
            for p in r_nb["propositions"]:
                self.assertEqual(p["status"], "planned")

            # 4. Notion `.zip` & local MD folder: 32-hex UUID strip + link rewrite + zip-slip block
            notion_zip = t / "Notion Export 0123456789abcdef0123456789abcdef.zip"
            with zipfile.ZipFile(notion_zip, "w") as zf:
                zf.writestr(
                    "Physics Notes 0123456789abcdef0123456789abcdef/SHM Intro a1b2c3d4e5f60718293a4b5c6d7e8f90.md",
                    (
                        "---\n"
                        "title: SHM Intro\n"
                        "tags: [physics, waves]\n"
                        "---\n"
                        "# SHM Intro\n"
                        "See [Waves](Waves%20fedcba9876543210fedcba9876543210.md).\n\n"
                        ":::cue\n"
                        "Q: What restores a pendulum?\n"
                        "A: Tangential gravity component -mg sin(theta).\n"
                        ":::\n\n"
                        "Q:: What is omega for a simple pendulum?\n"
                        "A:: sqrt(g / L)\n"
                    ),
                )
                zf.writestr(
                    "Physics Notes 0123456789abcdef0123456789abcdef/Waves fedcba9876543210fedcba9876543210.md",
                    "# Waves\nWave speed equals {{c1::frequency times wavelength}}.\n",
                )
            r_not = imp.import_markdown_folder(notion_zip, workspace=ws, kg_dir=kg, subject="Physics")
            self.assertTrue(r_not["ok"])
            self.assertEqual(r_not["fileCount"], 2)
            self.assertGreaterEqual(r_not["importedCount"], 3)
            clean_paths = {f["cleanPath"] for f in r_not["files"]}
            self.assertIn("physics-notes/shm-intro.md", clean_paths)
            self.assertIn("physics-notes/waves.md", clean_paths)
            rewritten_md = (Path(r_not["outputDir"]) / "files" / "physics-notes" / "shm-intro.md").read_text(encoding="utf-8")
            self.assertIn("(waves.md)", rewritten_md)
            self.assertNotIn("fedcba9876543210fedcba9876543210", rewritten_md)

            # Zip-slip Notion zip blocked
            bad_not_zip = t / "bad-notion.zip"
            with zipfile.ZipFile(bad_not_zip, "w") as zf:
                zf.writestr("../../pwn.md", "# Bad")
            with self.assertRaises(ValueError):
                imp.import_markdown_folder(bad_not_zip, workspace=ws, kg_dir=kg)

            # Local Markdown folder
            md_dir = t / "local-notes"
            (md_dir / ".obsidian").mkdir(parents=True)
            (md_dir / ".obsidian" / "ignored.md").write_text("# Ignored\n", encoding="utf-8")
            (md_dir / "calculus.md").write_text("# Calculus\nQ:: Derivative of sin(x)?\nA:: cos(x)\n", encoding="utf-8")
            r_dir = imp.import_markdown_folder(md_dir, workspace=ws, kg_dir=kg, subject="Maths")
            self.assertTrue(r_dir["ok"])
            self.assertEqual(r_dir["fileCount"], 1)

            # 5. detect_source_type() & ingest_auto() routing across all 4 formats
            self.assertEqual(ing.detect_source_type(str(apkg_mod)), "anki")
            self.assertEqual(ing.detect_source_type(str(quizlet_tsv)), "cards")
            self.assertEqual(ing.detect_source_type(str(nb_path)), "notebook")
            self.assertEqual(ing.detect_source_type(str(notion_zip)), "notes_batch")
            self.assertEqual(ing.detect_source_type(str(md_dir)), "notes_batch")

            ws_auto = t / "ws-auto"
            (ws_auto / "knowledge-graph").mkdir(parents=True)
            for src_path, exp_kind in (
                (apkg_mod, "anki"),
                (quizlet_tsv, "cards"),
                (nb_path, "notebook"),
                (notion_zip, "notes_batch"),
            ):
                res_a = ing.ingest_auto(str(src_path), workspace=ws_auto)
                self.assertTrue(res_a["ok"], f"ingest_auto failed for {exp_kind}: {res_a}")
                self.assertEqual(res_a["detectedKind"], exp_kind)

    def test_v32_14_agent_compat_matrix_guards_and_doctor_compat(self) -> None:
        import syntopik.agents_compat as ac

        expected_14 = (
            "antigravity", "claude", "codex", "cursor", "windsurf", "copilot", "gemini",
            "opencode", "cline", "roo", "zed", "aider", "continue", "kiro",
        )
        self.assertEqual(ac.ALL_HARNESSES, expected_14)
        self.assertEqual(len(ac.HARNESS_SPECS), 14)

        # 1. Ephemeral install-root guard (_npx, uv/archive-v0, pipx, OS temp)
        self.assertTrue(ac.is_ephemeral_install_root("/opt/runner/.npm/_npx/abcd123/node_modules/syntopik"))
        self.assertTrue(ac.is_ephemeral_install_root("/opt/runner/.cache/uv/archive-v0/xyz987/syntopik"))
        self.assertTrue(ac.is_ephemeral_install_root("D:\\Runner\\AppData\\Local\\Temp\\synt-run"))
        self.assertFalse(ac.is_ephemeral_install_root(ROOT))

        with tempfile.TemporaryDirectory(prefix="synt-v32-agents-") as tmp:
            t = Path(tmp)
            fake_home = t / "home"
            fake_home.mkdir(parents=True)
            vault = t / "vault"
            vault.mkdir(parents=True)
            http_url = "http://127.0.0.1:4173/mcp"

            # 2. Antigravity-only `~/.gemini` MUST NOT falsely detect Gemini CLI!
            (fake_home / ".gemini" / "antigravity").mkdir(parents=True)
            det_map = {d["id"]: d for d in ac.detect_installed_agents(env={}, home=fake_home)}
            self.assertEqual(len(det_map), 14)
            self.assertTrue(det_map["antigravity"]["detected"])
            self.assertFalse(det_map["gemini"]["detected"], "Antigravity ~/.gemini must not falsely trigger gemini CLI!")

            # 3. Attach ALL 14 harnesses in BOTH stdio and http transport & verify every schema quirk
            for tr in ("stdio", "http"):
                proj = t / f"proj-{tr}"
                proj.mkdir(parents=True)
                tr_home = t / f"home-{tr}"
                tr_home.mkdir(parents=True)
                for hid in expected_14:
                    res = ac.attach_harness_config(
                        hid,
                        proj,
                        vault_dir=vault,
                        pkg_root=ROOT,
                        transport=tr,
                        http_url=http_url,
                        home=tr_home,
                        write_global=True,
                        env={},
                    )
                    self.assertTrue(res["ok"], f"{hid}/{tr} failed: {res}")
                    self.assertFalse(res["ephemeralInstallRoot"])

                if tr == "http":
                    # Windsurf -> `serverUrl` (NOT `url`!)
                    ws_cfg = json.loads((tr_home / ".codeium" / "windsurf" / "mcp_config.json").read_text(encoding="utf-8"))
                    ws_ent = ws_cfg["mcpServers"]["syntopik-studio"]
                    self.assertEqual(ws_ent["serverUrl"], http_url)
                    self.assertNotIn("url", ws_ent)

                    # Copilot -> root key `servers` (NOT `mcpServers`!)
                    cp_cfg = json.loads((proj / ".vscode" / "mcp.json").read_text(encoding="utf-8"))
                    self.assertIn("servers", cp_cfg)
                    self.assertNotIn("mcpServers", cp_cfg)
                    self.assertEqual(cp_cfg["servers"]["syntopik-studio"]["type"], "http")
                    self.assertEqual(cp_cfg["servers"]["syntopik-studio"]["url"], http_url)

                    # Gemini CLI -> `httpUrl` (NOT `url`!)
                    gm_cfg = json.loads((proj / ".gemini" / "settings.json").read_text(encoding="utf-8"))
                    gm_ent = gm_cfg["mcpServers"]["syntopik-studio"]
                    self.assertEqual(gm_ent["httpUrl"], http_url)
                    self.assertNotIn("url", gm_ent)

                    # OpenCode -> root key `mcp`, `type: "remote"`
                    oc_cfg = json.loads((proj / "opencode.json").read_text(encoding="utf-8"))
                    self.assertEqual(oc_cfg["mcp"]["syntopik-studio"]["type"], "remote")
                    self.assertEqual(oc_cfg["mcp"]["syntopik-studio"]["url"], http_url)

                    # Cline -> camelCase `streamableHttp`
                    cl_cfg = json.loads((tr_home / ".cline" / "data" / "settings" / "cline_mcp_settings.json").read_text(encoding="utf-8"))
                    self.assertEqual(cl_cfg["mcpServers"]["syntopik-studio"]["type"], "streamableHttp")

                    # Roo -> hyphenated `streamable-http`
                    roo_cfg = json.loads((proj / ".roo" / "mcp.json").read_text(encoding="utf-8"))
                    self.assertEqual(roo_cfg["mcpServers"]["syntopik-studio"]["type"], "streamable-http")

                    # Zed -> `context_servers`
                    zed_cfg = json.loads((proj / ".zed" / "settings.json").read_text(encoding="utf-8"))
                    self.assertEqual(zed_cfg["context_servers"]["syntopik-studio"]["url"], http_url)

                    # Continue -> YAML `requestOptions.headers`
                    cont_yaml = (proj / ".continue" / "mcpServers" / "syntopik.yaml").read_text(encoding="utf-8")
                    self.assertIn("type: streamable-http", cont_yaml)
                    self.assertIn("requestOptions:", cont_yaml)

                    # Kiro -> steering + mcp.json
                    kiro_cfg = json.loads((proj / ".kiro" / "settings" / "mcp.json").read_text(encoding="utf-8"))
                    self.assertEqual(kiro_cfg["mcpServers"]["syntopik-studio"]["url"], http_url)
                    self.assertTrue((proj / ".kiro" / "steering" / "syntopik.md").is_file())

                    # Codex -> TOML bearer_token_env_var
                    codex_toml = (proj / ".codex" / "config.toml").read_text(encoding="utf-8")
                    self.assertIn("[mcp_servers.syntopik-studio]", codex_toml)
                    self.assertIn('bearer_token_env_var = "SYNTOPIK_TOKEN"', codex_toml)

            # 4. `.clinerules` regular-file guard
            proj_cr = t / "proj-clinerules-file"
            proj_cr.mkdir(parents=True)
            host_cr = "# Existing regular file .clinerules — DO NOT REPLACE\n"
            (proj_cr / ".clinerules").write_text(host_cr, encoding="utf-8")
            res_cr = ac.attach_harness_config("cline", proj_cr, vault_dir=vault, pkg_root=ROOT, home=fake_home, write_global=False, env={})
            self.assertTrue(res_cr["ok"])
            self.assertIn(".clinerules", res_cr["preserved"])
            self.assertTrue((proj_cr / ".clinerules").is_file())
            self.assertEqual((proj_cr / ".clinerules").read_text(encoding="utf-8"), host_cr)

            # 5. Zed 9-file precedence guard + JSONC comment merge
            proj_zed_guard = t / "proj-zed-guard"
            proj_zed_guard.mkdir(parents=True)
            (proj_zed_guard / "AGENTS.md").write_text("# Host AGENTS.md\n", encoding="utf-8")
            (proj_zed_guard / ".zed").mkdir(parents=True)
            (proj_zed_guard / ".zed" / "settings.json").write_text(
                "{\n  // user theme comment\n  \"theme\": \"One Dark\",\n}\n",
                encoding="utf-8",
            )
            res_zg = ac.attach_harness_config("zed", proj_zed_guard, vault_dir=vault, pkg_root=ROOT, home=fake_home, env={})
            self.assertTrue(res_zg["ok"])
            self.assertFalse((proj_zed_guard / ".rules").exists(), "Zed attach MUST NOT write .rules when AGENTS.md exists!")
            self.assertIn("AGENTS.md", res_zg["preserved"])
            self.assertTrue((proj_zed_guard / ".agents" / "syntopik-rules.md").is_file())
            zg_settings = json.loads((proj_zed_guard / ".zed" / "settings.json").read_text(encoding="utf-8"))
            self.assertEqual(zg_settings["theme"], "One Dark")
            self.assertIn("syntopik-studio", zg_settings["context_servers"])

            proj_zed_clean = t / "proj-zed-clean"
            proj_zed_clean.mkdir(parents=True)
            res_zc = ac.attach_harness_config("zed", proj_zed_clean, vault_dir=vault, pkg_root=ROOT, home=fake_home, env={})
            self.assertTrue(res_zc["ok"])
            self.assertTrue((proj_zed_clean / ".rules").is_file())

            # 6. Aider non-destructive YAML `read:` merge
            proj_aider = t / "proj-aider"
            proj_aider.mkdir(parents=True)
            (proj_aider / ".aider.conf.yml").write_text("model: claude-sonnet\nread:\n  - docs/arch.md\n", encoding="utf-8")
            res_ai = ac.attach_harness_config("aider", proj_aider, vault_dir=vault, pkg_root=ROOT, home=fake_home, env={})
            self.assertTrue(res_ai["ok"])
            ai_yml = (proj_aider / ".aider.conf.yml").read_text(encoding="utf-8")
            self.assertIn("model: claude-sonnet", ai_yml)
            self.assertIn("docs/arch.md", ai_yml)
            self.assertIn(".agents/syntopik-rules.md", ai_yml)

            # 7. Ephemeral pkg_root guard emits portable `npx -y syntopik mcp` (NEVER `_npx` or temp path!)
            eph_pkg = t / "_npx" / "transient-pkg"
            eph_pkg.mkdir(parents=True)
            proj_eph = t / "proj-ephemeral"
            proj_eph.mkdir(parents=True)
            res_eph = ac.attach_harness_config("cursor", proj_eph, vault_dir=vault, pkg_root=eph_pkg, transport="stdio", home=fake_home, env={})
            self.assertTrue(res_eph["ephemeralInstallRoot"])
            eph_mcp = json.loads((proj_eph / ".cursor" / "mcp.json").read_text(encoding="utf-8"))
            eph_ent = eph_mcp["mcpServers"]["syntopik-studio"]
            self.assertEqual(eph_ent["command"], "npx")
            self.assertNotIn("_npx", json.dumps(eph_ent))

            # 8. `syntopik doctor --compat --json` CLI check
            p_doc = run_cli([sys.executable, "-B", "-m", "syntopik.cli", "doctor", "--compat", "--json"])
            self.assertEqual(p_doc.returncode, 0, f"doctor --compat failed: {p_doc.stderr}\n{p_doc.stdout}")
            doc_j = json.loads(p_doc.stdout)
            self.assertTrue(doc_j["ok"])
            self.assertEqual(doc_j["totalHarnesses"], 14)
            self.assertEqual([h["id"] for h in doc_j["harnesses"]], list(expected_14))

    def test_v32_loopback_mcp_http_and_server_v32_apis(self) -> None:
        import base64
        import syntopik.mcp_http as mh
        import syntopik.server as srv_mod

        def _req(
            port: int,
            method: str,
            path: str,
            *,
            headers: dict[str, str] | None = None,
            body: Any = None,
        ) -> tuple[int, dict[str, str], bytes]:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10.0)
            try:
                hdrs = dict(headers or {})
                raw: bytes | None = None
                if body is not None:
                    if isinstance(body, (bytes, bytearray)):
                        raw = bytes(body)
                    elif isinstance(body, str):
                        raw = body.encode("utf-8")
                    else:
                        raw = json.dumps(body).encode("utf-8")
                    hdrs.setdefault("Content-Type", "application/json")
                conn.request(method, path, body=raw, headers=hdrs)
                resp = conn.getresponse()
                return resp.status, {k.lower(): v for k, v in resp.getheaders()}, resp.read()
            finally:
                conn.close()

        expected_7_tools = {
            "synt_next_move",
            "synt_get_frontier",
            "synt_capture_concept",
            "synt_log_review",
            "synt_whiteboard_step",
            "synt_readiness",
            "synt_interleave",
        }
        self.assertEqual({t["name"] for t in mh.list_http_mcp_tools()}, expected_7_tools)

        with tempfile.TemporaryDirectory(prefix="synt-v32-mcp-http-") as tmp:
            ws = Path(tmp) / "ws"
            kg = ws / "knowledge-graph"
            ch = ws / "chapters"
            kg.mkdir(parents=True)
            ch.mkdir(parents=True)
            (kg / "propositions.jsonl").write_text(
                "\n".join([
                    json.dumps({
                        "id": "p-shm-1",
                        "subject": "Physics",
                        "chapter": "SHM",
                        "title": "Restoring Force Law",
                        "statement": "F = -kx produces sinusoidal motion.",
                        "status": "solid",
                        "bktMastery": 0.90,
                        "cues": [{"id": "c1", "q": "[SHM] What is the restoring force in a linear spring?", "a": "F = -kx"}],
                    }),
                    json.dumps({
                        "id": "p-opt-1",
                        "subject": "Physics",
                        "chapter": "Optics",
                        "title": "Snell Refraction Law",
                        "statement": "n1 sin(theta1) = n2 sin(theta2) at dielectric interfaces.",
                        "status": "solid",
                        "bktMastery": 0.88,
                        "cues": [{"id": "c1", "q": "[Optics] How do ray angles relate across an index boundary?", "a": "n1 sin(theta1) = n2 sin(theta2)"}],
                    }),
                ]) + "\n",
                encoding="utf-8",
            )
            (kg / "chains.jsonl").write_text("", encoding="utf-8")

            # A. Direct syntopik.mcp_http tests across all 7 tools + zero-leak + NaN rejection
            st_i, res_i = mh.handle_mcp_http_request(ws, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26"}})
            self.assertEqual(st_i, 200)
            assert isinstance(res_i, dict)
            self.assertEqual(res_i["result"]["serverInfo"]["version"], "3.2.0")

            st_n, res_n = mh.handle_mcp_http_request(ws, {"jsonrpc": "2.0", "method": "notifications/initialized"})
            self.assertEqual(st_n, 202)
            self.assertIsNone(res_n)

            st_tl, res_tl = mh.handle_mcp_http_request(ws, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
            self.assertEqual(st_tl, 200)
            assert isinstance(res_tl, dict)
            self.assertEqual({t["name"] for t in res_tl["result"]["tools"]}, expected_7_tools)

            # Strict NaN rejection (raw JSON string AND Python float NaN)
            _s_nan1, r_nan1 = mh.handle_mcp_http_request(ws, b'{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"synt_readiness","arguments":{"maxMarks":NaN}}}')
            assert isinstance(r_nan1, dict)
            self.assertEqual(r_nan1["error"]["code"], mh.JSONRPC_PARSE_ERROR)
            _s_nan2, r_nan2 = mh.handle_mcp_http_request(ws, {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "synt_readiness", "arguments": {"maxMarks": float("nan")}}})
            assert isinstance(r_nan2, dict)
            self.assertEqual(r_nan2["error"]["code"], mh.JSONRPC_INVALID_REQUEST)

            # Call all 7 tools directly
            secret_wb_ans = "SECRET_WB_GATE_ANSWER_5519"
            for cid, tname, targs in (
                (10, "synt_capture_concept", {"id": "p-elec-gauss", "title": "Gauss Flux Law", "statement": "Flux = Q_enc / eps_0", "subject": "Physics", "chapter": "Electrostatics"}),
                (11, "synt_log_review", {"propId": "p-elec-gauss", "rating": "good", "confidence": 0.85, "durationMs": 4500}),
                (12, "synt_get_frontier", {"limit": 5}),
                (13, "synt_next_move", {"energy": "normal"}),
                (14, "synt_whiteboard_step", {"kind": "prediction_gate", "title": "Predict Flux", "prompt": "What is flux?", "expectedAnswer": secret_wb_ans, "revealedBody": secret_wb_ans}),
                (15, "synt_readiness", {"maxMarks": 100}),
                (16, "synt_interleave", {"action": "build", "n": 3, "seed": 7}),
            ):
                st_c, res_c = mh.handle_mcp_http_request(
                    ws,
                    {"jsonrpc": "2.0", "id": cid, "method": "tools/call", "params": {"name": tname, "arguments": targs}},
                )
                self.assertEqual(st_c, 200, f"{tname} failed: {res_c}")
                assert isinstance(res_c, dict) and "result" in res_c, f"{tname} error: {res_c}"
                self.assertFalse(res_c["result"].get("isError", False), f"{tname} returned isError: {res_c}")
                if tname == "synt_whiteboard_step":
                    self.assertNotIn(secret_wb_ans, json.dumps(res_c), "synt_whiteboard_step leaked locked gate answer!")
                if tname == "synt_interleave":
                    ilv_dump = json.dumps(res_c)
                    for sk in ("sealedChapter", "sealedPrinciple", "sealedAnswer"):
                        self.assertNotIn(sk, ilv_dump, f"synt_interleave leaked {sk}!")

            # B. Live ephemeral-port `syntopik.server` v3.2 HTTP & `POST /mcp` tests
            srv = srv_mod.create_server(workspace=ws, host="127.0.0.1", port=0, lan=False)
            t_srv = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
            t_srv.start()
            try:
                port = srv.bound_port
                tok_hdr = {"X-Syntopik-Token": srv.csrf_token}
                bearer_hdr = {"Authorization": f"Bearer {srv.csrf_token}"}

                # 1. POST /mcp security: missing token -> 403, bad Origin -> 403, GET /mcp -> 405
                st_no_tok, _, _ = _req(port, "POST", "/mcp", body={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
                self.assertEqual(st_no_tok, 403)
                st_bad_orig, _, _ = _req(
                    port,
                    "POST",
                    "/mcp",
                    headers={**bearer_hdr, "Origin": "https://evil.example"},
                    body={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
                )
                self.assertEqual(st_bad_orig, 403)
                st_get_mcp, _, _ = _req(port, "GET", "/mcp", headers=tok_hdr)
                self.assertEqual(st_get_mcp, 405)

                # 2. POST /mcp initialize (Bearer), tools/list (7 tools), tools/call, notification (202), NaN reject
                st_init, _, raw_init = _req(
                    port,
                    "POST",
                    "/mcp",
                    headers=bearer_hdr,
                    body={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26"}},
                )
                self.assertEqual(st_init, 200, raw_init)
                self.assertEqual(json.loads(raw_init)["result"]["serverInfo"]["version"], "3.2.0")

                st_notif, _, _ = _req(
                    port,
                    "POST",
                    "/mcp",
                    headers=tok_hdr,
                    body={"jsonrpc": "2.0", "method": "notifications/initialized"},
                )
                self.assertEqual(st_notif, 202)

                st_list, _, raw_list = _req(
                    port,
                    "POST",
                    "/mcp",
                    headers=tok_hdr,
                    body={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
                )
                self.assertEqual(st_list, 200, raw_list)
                self.assertEqual({t["name"] for t in json.loads(raw_list)["result"]["tools"]}, expected_7_tools)

                st_call, _, raw_call = _req(
                    port,
                    "POST",
                    "/mcp",
                    headers=bearer_hdr,
                    body={"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "synt_readiness", "arguments": {"maxMarks": 100}}},
                )
                self.assertEqual(st_call, 200, raw_call)
                self.assertFalse(json.loads(raw_call)["result"].get("isError", False))

                st_nan, _, raw_nan = _req(
                    port,
                    "POST",
                    "/mcp",
                    headers=tok_hdr,
                    body='{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"synt_readiness","arguments":{"maxMarks":NaN}}}',
                )
                self.assertTrue(
                    st_nan == 400 or (st_nan == 200 and json.loads(raw_nan).get("error", {}).get("code") in (-32700, -32600)),
                    f"POST /mcp must reject NaN JSON: status={st_nan}, body={raw_nan!r}",
                )

                # 3. GET /api/readiness
                st_rd, _, raw_rd = _req(port, "GET", "/api/readiness")
                self.assertEqual(st_rd, 200, raw_rd)
                rd_j = json.loads(raw_rd)
                self.assertTrue(rd_j["ok"])
                self.assertIn("readinessIndex", rd_j)
                self.assertIn("preMortemShield", rd_j)
                self.assertIn("taperMode", rd_j)

                # 4. POST /api/interleave/{build,check,complete} + zero-leak check
                st_ib_csrf, _, _ = _req(port, "POST", "/api/interleave/build", body={"n": 3, "seed": 11})
                self.assertEqual(st_ib_csrf, 403)
                st_ib, _, raw_ib = _req(port, "POST", "/api/interleave/build", headers=tok_hdr, body={"n": 3, "seed": 11})
                self.assertEqual(st_ib, 200, raw_ib)
                ib_text = raw_ib.decode("utf-8")
                for sk in ("sealedChapter", "sealedPrinciple", "sealedAnswer", "sealedPrincipleId"):
                    self.assertNotIn(sk, ib_text, f"POST /api/interleave/build leaked {sk}!")
                ib_j = json.loads(ib_text)
                self.assertTrue(ib_j["ok"])
                sid = ib_j["sessionId"]
                first_item = ib_j["items"][0]

                st_ic, _, raw_ic = _req(
                    port,
                    "POST",
                    "/api/interleave/check",
                    headers=tok_hdr,
                    body={
                        "sessionId": sid,
                        "itemId": first_item["itemId"],
                        "selectedPrinciple": first_item["principleChoices"][0]["id"],
                        "confidence": 0.75,
                    },
                )
                self.assertEqual(st_ic, 200, raw_ic)
                ic_j = json.loads(raw_ic)
                self.assertTrue(ic_j["ok"])
                self.assertTrue(ic_j["step2Unlocked"])

                st_icomp, _, raw_icomp = _req(
                    port,
                    "POST",
                    "/api/interleave/complete",
                    headers=tok_hdr,
                    body={"sessionId": sid, "results": {first_item["itemId"]: {"solved": True}}},
                )
                self.assertEqual(st_icomp, 200, raw_icomp)
                self.assertEqual(json.loads(raw_icomp)["status"], "completed")

                # 5. GET /api/capabilities (14 agents + mcpHttpEndpoint + mcpHttpTools)
                st_cap, _, raw_cap = _req(port, "GET", "/api/capabilities")
                self.assertEqual(st_cap, 200, raw_cap)
                cap_j = json.loads(raw_cap)
                self.assertIn(f"127.0.0.1:{port}/mcp", str(cap_j.get("mcpHttpEndpoint") or ""))
                self.assertEqual(set(cap_j.get("mcpHttpTools") or []), expected_7_tools)
                self.assertEqual((cap_j.get("agentsCompat") or {}).get("totalHarnesses"), 14)

                # 6. POST /api/import (base64 CSV upload + path-traversal rejection -> 400/403)
                csv_b64 = base64.b64encode(b"Front,Back\nOhm Law,V = IR\n").decode("ascii")
                st_imp_ok, _, raw_imp_ok = _req(
                    port,
                    "POST",
                    "/api/import",
                    headers=tok_hdr,
                    body={"kind": "cards", "filename": "circuits.csv", "contentBase64": csv_b64, "subject": "Physics"},
                )
                self.assertEqual(st_imp_ok, 200, raw_imp_ok)
                imp_j = json.loads(raw_imp_ok)
                self.assertTrue(imp_j["ok"])
                self.assertGreaterEqual(int(imp_j.get("importedCount", 0)), 1)

                st_imp_trav, _, _ = _req(
                    port,
                    "POST",
                    "/api/import",
                    headers=tok_hdr,
                    body={"kind": "cards", "filename": "../../etc/passwd", "contentBase64": csv_b64},
                )
                self.assertIn(st_imp_trav, (400, 403), f"Path traversal upload must return 400/403, got {st_imp_trav}")
            finally:
                srv.shutdown()
                srv.server_close()
                t_srv.join(timeout=3.0)

            # C. --lan hard kill-switch: POST /mcp MUST return 403 even from 127.0.0.1 with valid token!
            srv_lan = srv_mod.create_server(workspace=ws, host="127.0.0.1", port=0, lan=True)
            t_lan = threading.Thread(target=srv_lan.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
            t_lan.start()
            try:
                lport = srv_lan.bound_port
                st_lan_mcp, _, _ = _req(
                    lport,
                    "POST",
                    "/mcp",
                    headers={"Authorization": f"Bearer {srv_lan.csrf_token}"},
                    body={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
                )
                self.assertEqual(st_lan_mcp, 403, f"POST /mcp MUST return 403 when --lan is enabled, got {st_lan_mcp}")
            finally:
                srv_lan.shutdown()
                srv_lan.server_close()
                t_lan.join(timeout=3.0)

    def test_v32_version_budget_skills_and_zero_legacy_cli_leaks(self) -> None:
        import syntopik

        # 1. Unified 3.2.0 version across Python, Node, MCP, Claude & Gemini manifests
        self.assertEqual(syntopik.__version__, "3.2.0")
        pyp = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertEqual(pyp["project"]["version"], "3.2.0")
        self.assertEqual(json.loads((ROOT / "package.json").read_text(encoding="utf-8"))["version"], "3.2.0")
        self.assertEqual(json.loads((ROOT / "mcp-app" / "mlh-studio" / "package.json").read_text(encoding="utf-8"))["version"], "3.2.0")
        self.assertEqual(json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))["version"], "3.2.0")
        self.assertEqual(json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))["metadata"]["version"], "3.2.0")
        self.assertEqual(json.loads((ROOT / "gemini-extension.json").read_text(encoding="utf-8"))["version"], "3.2.0")

        # 2. Tight v3.2 context budget: AGENTS.md + GEMINI.md <= 7,500 B (and < 9,500 B), @AGENTS.md on line 1
        agents_raw = (ROOT / "AGENTS.md").read_bytes()
        gemini_raw = (ROOT / "GEMINI.md").read_bytes()
        claude_text = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
        combined = len(agents_raw) + len(gemini_raw)
        self.assertLessEqual(combined, 7500, f"AGENTS.md + GEMINI.md ({combined} B) exceeds v3.2 7,500 B target!")
        self.assertLess(combined, 9500)
        self.assertTrue(gemini_raw.decode("utf-8").startswith("@AGENTS.md"))
        self.assertTrue(claude_text.startswith("@AGENTS.md"))

        # 3. Exact 17 skills with clean YAML frontmatter
        skill_files = sorted(SKILLS_DIR.glob("*/SKILL.md"))
        self.assertEqual(len(skill_files), 17, f"Expected exact 17 skills, found {len(skill_files)}")
        total_desc = 0
        for sk in skill_files:
            fm = parse_yaml_frontmatter(sk.read_text(encoding="utf-8"))
            self.assertIsNotNone(fm)
            assert fm is not None
            desc = fm.get("description", "")
            self.assertNotIn("<", desc)
            self.assertNotIn(">", desc)
            total_desc += len(desc.encode("utf-8"))
        self.assertLessEqual(total_desc, 3600)

        # 4. Zero legacy student-facing CLI leaks in dashboard.html & knowledge-graph/viewer.html
        legacy_leaks = (
            "scripts/study_doctor.py",
            "scripts/daily_plan.py",
            "scripts/exam_runner.py",
            "scripts/setup_harness.py",
            "scripts/srs_engine.py",
            "scripts/mistake_analytics.py",
            "node scripts/sync-graph.mjs",
        )
        for ui_rel in ("dashboard.html", "knowledge-graph/viewer.html"):
            ui_txt = (ROOT / ui_rel).read_text(encoding="utf-8")
            for needle in legacy_leaks:
                self.assertNotIn(needle, ui_txt, f"Legacy CLI leak {needle!r} found in {ui_rel}!")

    def test_v32_hardening_p0_p1_p2_regressions(self) -> None:
        import base64
        import contextlib
        import http.client
        import inspect
        import io
        import threading
        import zipfile
        import exam_runner as er
        import syntopik.agents_compat as ac
        import syntopik.cli as cli_mod
        import syntopik.importers as imp
        import syntopik.interleave as ilv
        import syntopik.mcp_http as mcp_http
        import syntopik.readiness as rd
        import syntopik.server as srv_mod

        def _req(
            port: int,
            method: str,
            path: str,
            *,
            headers: dict[str, str] | None = None,
            body: Any = None,
        ) -> tuple[int, dict[str, str], bytes]:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
            hdrs = dict(headers or {})
            payload: bytes | None = None
            if isinstance(body, (dict, list)):
                payload = json.dumps(body).encode("utf-8")
                hdrs.setdefault("Content-Type", "application/json")
            elif isinstance(body, str):
                payload = body.encode("utf-8")
                hdrs.setdefault("Content-Type", "application/json")
            elif isinstance(body, (bytes, bytearray)):
                payload = bytes(body)
            try:
                conn.request(method, path, body=payload, headers=hdrs)
                resp = conn.getresponse()
                raw = resp.read()
                return resp.status, {k.lower(): v for k, v in resp.getheaders()}, raw
            finally:
                conn.close()

        # P0 #2: CLOZE_PATTERN linear-time anti-ReDoS (<100ms on 20 KB adversarial unclosed cloze)
        adv_cloze = "{{c1::a::b" * 2000  # ~18 KB adversarial unclosed clozes
        t0 = time.perf_counter()
        adv_matches = imp.CLOZE_PATTERN.findall(adv_cloze)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        self.assertEqual(adv_matches, [])
        self.assertLess(elapsed_ms, 100.0, f"CLOZE_PATTERN ReDoS regression: took {elapsed_ms:.1f}ms")
        valid_matches = imp.CLOZE_PATTERN.findall("Force is {{c1::m*a::Newton 2nd}} and {{c2::E=mc^2}}.")
        self.assertEqual(valid_matches, [("1", "m*a", "Newton 2nd"), ("2", "E=mc^2", "")])

        # P0 #3: Windows-safe Path.as_uri() for SQLite URI in _read_anki_sqlite_notes
        sqlite_src = inspect.getsource(imp._read_anki_sqlite_notes)
        self.assertIn(".as_uri()", sqlite_src)
        self.assertNotIn("urllib.parse.quote(str(", sqlite_src)

        # P2 #4: Aligned MCP HTTP body cap & non-finite err_id guard
        self.assertEqual(srv_mod.MAX_MCP_HTTP_BODY_BYTES, mcp_http.MAX_MCP_BODY_BYTES)
        st_nf, resp_nf = mcp_http.handle_mcp_http_request(
            ROOT,
            {"jsonrpc": "2.0", "id": float("nan"), "method": "tools/list", "params": {}},
        )
        self.assertEqual(st_nf, 200)
        self.assertIsInstance(resp_nf, dict)
        assert isinstance(resp_nf, dict)
        self.assertIsNone(resp_nf.get("id"))
        json.dumps(resp_nf, allow_nan=False)  # must not raise ValueError

        # P2 #1: No legacy CLI invocation in demos/exam-cbt-template.html
        cbt_tpl = (ROOT / "demos" / "exam-cbt-template.html").read_text(encoding="utf-8")
        self.assertNotIn(".venv/bin/python scripts/exam_runner.py", cbt_tpl)
        self.assertIn("syntopik run exam_runner grade", cbt_tpl)

        with tempfile.TemporaryDirectory(prefix="synt-v32-harden-") as tmp:
            t = Path(tmp)
            ws = t / "ws"
            kg = ws / "knowledge-graph"
            kg.mkdir(parents=True)
            (ws / "chapters").mkdir(parents=True)
            (ws / "sources").mkdir(parents=True)

            # Seed propositions across 3 chapters + unresolved forks with long (>110) & multiline refutationMsg
            secret_long_refute = (
                "Always check the boundary sign before integrating across the discontinuity "
                "SECRET_REFUTATION_TOKEN_LONG_12345 because naive substitution drops the impulse jump."
            )
            secret_multiline_refute = "Check symmetry first\n   SHORT_SECRET_LINE2_XYZ"
            props_seed = [
                {
                    "id": "h-p1",
                    "title": "Energy Conservation in SHM",
                    "statement": "Total mechanical energy in undamped SHM is constant: E = 0.5*k*A^2.",
                    "subject": "Physics",
                    "chapter": "SHM",
                    "status": "misconception",
                    "stability": 2.0,
                    "difficulty": 5.5,
                    "reps": 2,
                    "lapses": 1,
                    "lastReviewed": "2026-11-20",
                    "nextReview": "2026-11-22",
                    "cues": [{"id": "c1", "prompt": "Find total energy of undamped SHM at amplitude A.", "answer": "0.5 k A^2"}],
                },
                {
                    "id": "h-p2",
                    "title": "Kirchhoff Loop Law",
                    "statement": "Sum of potential differences around any closed circuit loop equals zero.",
                    "subject": "Physics",
                    "chapter": "Circuits",
                    "status": "solid",
                    "stability": 12.0,
                    "difficulty": 4.5,
                    "reps": 4,
                    "lapses": 0,
                    "lastReviewed": "2026-11-24",
                    "nextReview": "2026-12-10",
                    "cues": [{"id": "c2", "prompt": "Sum voltages around a closed resistor loop.", "answer": "0 V"}],
                },
                {
                    "id": "h-p3",
                    "title": "Snell Refraction Law",
                    "statement": "n1 * sin(theta1) = n2 * sin(theta2) across a planar optical interface.",
                    "subject": "Physics",
                    "chapter": "Optics",
                    "status": "shaky",
                    "stability": 3.0,
                    "difficulty": 5.0,
                    "reps": 2,
                    "lapses": 0,
                    "lastReviewed": "2026-11-23",
                    "nextReview": "2026-11-26",
                    "cues": [{"id": "c3", "prompt": "Relate incident and refracted angles across n1 -> n2.", "answer": "n1 sin(theta1) = n2 sin(theta2)"}],
                },
            ]
            (kg / "propositions.jsonl").write_text(
                "\n".join(json.dumps(p) for p in props_seed) + "\n",
                encoding="utf-8",
            )
            forks_seed = [
                {
                    "id": "fk-long-1",
                    "propId": "h-p1",
                    "subject": "Physics",
                    "chapter": "SHM",
                    "status": "active",
                    "lapses": 3,
                    "winCount": 0,
                    "forkPrompt": "Block crosses equilibrium with friction present",
                    "sealedWinningMove": "Include work done by friction",
                    "sealedBlunder": "Assume E stays 0.5*k*A^2",
                    "refutationMsg": secret_long_refute,
                },
                {
                    "id": "fk-multi-2",
                    "propId": "h-p3",
                    "subject": "Physics",
                    "chapter": "Optics",
                    "status": "active",
                    "lapses": 2,
                    "winCount": 0,
                    "forkPrompt": "Ray moves from glass into air near critical angle",
                    "sealedWinningMove": "Check sin(theta_c) = n2/n1 first",
                    "sealedBlunder": "Apply arcsin blindly",
                    "refutationMsg": secret_multiline_refute,
                },
            ]
            (kg / "forks.jsonl").write_text(
                "\n".join(json.dumps(f) for f in forks_seed) + "\n",
                encoding="utf-8",
            )

            # P0 #1: Fork refutationMsg NEVER leaks in compute_readiness, build_premortem_shield, or server sanitizer
            shield = rd.build_premortem_shield(ws, kg_dir=kg)
            rd_out = rd.compute_readiness(ws, kg_dir=kg, chapters_dir=ws / "chapters")
            for payload_obj in (shield, rd_out):
                dump = json.dumps(payload_obj)
                self.assertNotIn("SECRET_REFUTATION_TOKEN_LONG_12345", dump)
                self.assertNotIn("SHORT_SECRET_LINE2_XYZ", dump)
                self.assertNotIn("naive substitution drops", dump)
                self.assertNotIn("Check symmetry first", dump)

            # Defense-in-depth sanitizer test with word-boundary clipped (>110) & multiline bullets
            crafted_shield = {
                "ok": True,
                "sourceBasis": "observed_logs",
                "count": 2,
                "bullets": [
                    {
                        "rank": 1,
                        "sourceKind": "blunder_fork",
                        "forkId": "fk-long-1",
                        "ifCue": "IF you hit fork setup",
                        "thenAction": "THEN stop and verify before committing: Always check the boundary sign before integrating across the discontinuity SECRET_REFUTATION_TOKEN...",
                    },
                    {
                        "rank": 2,
                        "sourceKind": "blunder_fork",
                        "forkId": "fk-multi-2",
                        "ifCue": "IF you hit fork 2",
                        "thenAction": "THEN Check symmetry first SHORT_SECRET_LINE2_XYZ",
                    },
                ],
            }
            san = srv_mod._sanitize_premortem_shield(crafted_shield, kg)
            san_dump = json.dumps(san)
            self.assertNotIn("SECRET_REFUTATION_TOKEN", san_dump)
            self.assertNotIn("SHORT_SECRET_LINE2_XYZ", san_dump)
            self.assertNotIn("Check symmetry first", san_dump)

            # P0 #4: Untrusted .ipynb unsafe call extraction blocked across ALL 4 assert patterns
            nb_unsafe = {
                "nbformat": 4,
                "nbformat_minor": 5,
                "metadata": {"kernelspec": {"language": "python"}},
                "cells": [
                    {
                        "cell_type": "code",
                        "source": [
                            "def solve(x):\n",
                            "    return x * 2\n",
                            "\n",
                            "assert solve(3) == 6\n",
                            "assert math.isclose(solve(2.5), 5.0)\n",
                            "assert os.system('id') == 0\n",
                            "assert eval('1+1') == 2\n",
                            "assert subprocess.run(['id']) == 0\n",
                            "assert math.isclose(eval('1.0'), 1.0)\n",
                            "assert os.system('id')\n",
                            "assert not os.system('id')\n",
                        ],
                        "outputs": [],
                    }
                ],
            }
            nb_path = ws / "sources" / "untrusted.ipynb"
            nb_path.write_text(json.dumps(nb_unsafe), encoding="utf-8")
            nb_res = imp.import_jupyter_notebook(nb_path, workspace=ws, kg_dir=kg, subject="CS")
            self.assertTrue(nb_res["ok"])
            ct_path = Path(nb_res["outputDir"]) / "code-tests.json"
            ct_data = json.loads(ct_path.read_text(encoding="utf-8"))
            entries = [c["entry"] for c in ct_data.get("cases", [])]
            self.assertEqual(entries, ["solve", "solve"])

            nb_no_defs = {
                "nbformat": 4,
                "nbformat_minor": 5,
                "metadata": {"kernelspec": {"language": "python"}},
                "cells": [
                    {
                        "cell_type": "code",
                        "source": ["assert eval('1+1') == 2\n", "assert os.system('id')\n", "assert not open('/etc/passwd')\n"],
                        "outputs": [],
                    }
                ],
            }
            nb_empty_path = ws / "sources" / "nodefs.ipynb"
            nb_empty_path.write_text(json.dumps(nb_no_defs), encoding="utf-8")
            nb_empty_res = imp.import_jupyter_notebook(nb_empty_path, workspace=ws, kg_dir=kg, subject="CS")
            self.assertEqual(nb_empty_res["extractedCasesCount"], 0)

            # P1 #5: Corrupted .apkg SQLite raises ValueError (and HTTP 400 on POST /api/import)
            corrupt_apkg = t / "corrupt.apkg"
            with zipfile.ZipFile(corrupt_apkg, "w") as zf:
                zf.writestr("collection.anki2", b"NOT_A_VALID_SQLITE_DATABASE_HEADER_000000")
            with self.assertRaises(ValueError):
                imp.import_anki_apkg(corrupt_apkg, workspace=ws, kg_dir=kg)

            # P1 #1: JSONC agent config preserved cleanly (comments + user servers) & malformed JSON untouched
            repo_py = t / "repo-py"
            (repo_py / ".vscode").mkdir(parents=True)
            vscode_jsonc = (
                "{\n"
                "  // User existing VS Code MCP config\n"
                '  "servers": {\n'
                '    "my-db": { "type": "stdio", "command": "pg-mcp" },\n'
                "  },\n"
                "}\n"
            )
            (repo_py / ".vscode" / "mcp.json").write_text(vscode_jsonc, encoding="utf-8")
            broken_raw = "{ broken: not valid json at all\n"
            (repo_py / "opencode.json").write_text(broken_raw, encoding="utf-8")

            res_cop = ac.attach_harness_config(
                "copilot",
                repo_py,
                vault_dir=ws,
                pkg_root=ROOT,
                transport="stdio",
            )
            self.assertTrue(res_cop["ok"])
            merged_vs = json.loads((repo_py / ".vscode" / "mcp.json").read_text(encoding="utf-8"))
            self.assertIn("my-db", merged_vs["servers"])
            self.assertIn("syntopik-studio", merged_vs["servers"])

            res_oc = ac.attach_harness_config(
                "opencode",
                repo_py,
                vault_dir=ws,
                pkg_root=ROOT,
                transport="stdio",
            )
            self.assertEqual((repo_py / "opencode.json").read_text(encoding="utf-8"), broken_raw)
            self.assertIn("opencode.json", res_oc["preserved"])
            self.assertNotIn("opencode.json", res_oc["written"])
            self.assertTrue(any("opencode.json" in n for n in res_oc["notes"]))

            # Also verify Node wrapper `bin/syntopik.mjs attach` preserves JSONC & malformed JSON identically
            repo_js = t / "repo-js"
            (repo_js / ".vscode").mkdir(parents=True)
            (repo_js / ".vscode" / "mcp.json").write_text(vscode_jsonc, encoding="utf-8")
            (repo_js / "opencode.json").write_text(broken_raw, encoding="utf-8")
            node_cop = subprocess.run(
                [
                    "node",
                    str(ROOT / "bin" / "syntopik.mjs"),
                    "attach",
                    str(repo_js),
                    "--harness",
                    "copilot",
                    "--vault",
                    "local",
                    "--json",
                ],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            self.assertEqual(node_cop.returncode, 0, node_cop.stderr or node_cop.stdout)
            js_vs = json.loads((repo_js / ".vscode" / "mcp.json").read_text(encoding="utf-8"))
            self.assertIn("my-db", js_vs["servers"])
            self.assertIn("syntopik-studio", js_vs["servers"])
            node_oc = subprocess.run(
                [
                    "node",
                    str(ROOT / "bin" / "syntopik.mjs"),
                    "attach",
                    str(repo_js),
                    "--harness",
                    "opencode",
                    "--vault",
                    "local",
                    "--json",
                ],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            self.assertEqual(node_oc.returncode, 0, node_oc.stderr or node_oc.stdout)
            self.assertEqual((repo_js / "opencode.json").read_text(encoding="utf-8"), broken_raw)
            js_report = json.loads(node_oc.stdout)
            self.assertIn("opencode.json", js_report.get("preserved", []))

            # P1 #2 + P2 #6: Pre-submit CBT HTML zero-leak (blindChapterLabels=True) + marksWrong==0, U>0 EV-skip
            secret_sol = "SECRET_SOLUTION_TOKEN_987"
            secret_trap = "SECRET_TRAP_NOTE_654"
            secret_why = "SECRET_OPTION_WHY_WRONG_321"
            secret_chap = "SECRET_CHAPTER_BLIND_XYZ"
            secret_prop = "secret-prop-blind-xyz"
            raw_cbt_spec = {
                "examSlug": "harden-zero-leak-cbt",
                "title": "Hardening Zero-Leak CBT",
                "durationMinutes": 30,
                "blindChapterLabels": True,
                "questions": [
                    {
                        "id": "Q1",
                        "type": "single_correct",
                        "subject": "Physics",
                        "chapter": secret_chap,
                        "propId": secret_prop,
                        "marksCorrect": 4,
                        "marksWrong": 0,
                        "marksUnattempted": 1,
                        "stem": "A block oscillates on a frictionless table.",
                        "options": [
                            {"id": "A", "text": "Option A", "whyWrong": secret_why, "trapCategory": "sign_error"},
                            {"id": "B", "text": "Option B"},
                        ],
                        "answer": "B",
                        "solution": secret_sol,
                        "trapCategory": "condition_missed",
                        "trapNote": secret_trap,
                    },
                    {
                        "id": "Q2",
                        "type": "matrix_match",
                        "matrixMode": "code_options",
                        "subject": "Physics",
                        "chapter": secret_chap,
                        "propId": secret_prop,
                        "marksCorrect": 4,
                        "marksWrong": -1,
                        "stem": "Match List I with List II.",
                        "rows": [{"id": "P", "text": "Row P"}, {"id": "Q", "text": "Row Q"}],
                        "cols": [{"id": "1", "text": "Col 1"}, {"id": "2", "text": "Col 2"}],
                        "options": [
                            {"id": "A", "text": "P->1, Q->2"},
                            {"id": "B", "text": "P->1, Q->1"},
                        ],
                        "optionRowMap": {"A": {"P": ["1"], "Q": ["2"]}, "B": {"P": ["1"], "Q": ["1"]}},
                        "rowAnswer": {"P": ["1"], "Q": ["2"]},
                        "answer": "A",
                        "solution": "SECRET_MAT_SOLUTION_111",
                    },
                ],
            }
            spec_p = t / "cbt-spec.json"
            html_p = t / "cbt-exam.html"
            spec_p.write_text(json.dumps(raw_cbt_spec), encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                er.cmd_build(argparse.Namespace(spec=str(spec_p), out=str(html_p), no_sync=True))
            compiled_html = html_p.read_text(encoding="utf-8")
            for forbidden_plain in (secret_sol, secret_trap, secret_why, secret_chap, secret_prop, "SECRET_MAT_SOLUTION_111"):
                self.assertNotIn(forbidden_plain, compiled_html, f"Compiled CBT HTML leaked plaintext {forbidden_plain}!")

            m_spec = re.search(r"window\.__EXAM_SPEC__\s*=\s*(\{.*?\});\s*\nwindow\.__EXAM_SEALED_POST__", compiled_html, re.DOTALL)
            self.assertIsNotNone(m_spec, "Could not locate window.__EXAM_SPEC__ in compiled HTML")
            assert m_spec is not None
            pub_spec = json.loads(m_spec.group(1))
            for q_pub in pub_spec["questions"]:
                for fk in ("answer", "range", "solution", "trapCategory", "trapNote", "rowAnswer", "chapter", "propId"):
                    self.assertNotIn(fk, q_pub, f"window.__EXAM_SPEC__ leaked field {fk!r}!")

            m_sealed = re.search(r'window\.__EXAM_SEALED_POST__\s*=\s*"([^"]*)";', compiled_html)
            self.assertIsNotNone(m_sealed)
            assert m_sealed is not None
            sealed_decoded = json.loads(base64.b64decode(m_sealed.group(1)).decode("utf-8"))
            self.assertEqual(sealed_decoded["_questions"]["Q1"]["answer"], "B")
            self.assertEqual(sealed_decoded["_questions"]["Q1"]["solution"], secret_sol)
            self.assertEqual(sealed_decoded["_questions"]["Q1"]["trapNote"], secret_trap)
            self.assertEqual(sealed_decoded["_questions"]["Q1"]["chapter"], secret_chap)
            self.assertEqual(sealed_decoded["_questions"]["Q1"]["propId"], secret_prop)
            self.assertEqual(sealed_decoded["Q1"]["A"]["whyWrong"], secret_why)

            # P2 #6: EV-skip discipline triggers when marksWrong == 0 and marksUnattempted > 0 (p_be = 1/4 = 0.25)
            norm_spec = er.normalize_spec(raw_cbt_spec)
            ev_rep = er.grade_attempt(
                norm_spec,
                {
                    "schemaVersion": 1,
                    "examSlug": "harden-zero-leak-cbt",
                    "responses": {
                        "Q1": {"answer": "A", "status": "answered", "timeSpentSec": 30, "confidence": 0.15},
                        "Q2": {"answer": "B", "status": "answered", "timeSpentSec": 25, "confidence": 0.90},
                    },
                },
                kg_dir=kg,
                no_log=True,
                no_srs=True,
                no_sync=True,
            )
            pq_map = {row["id"]: row for row in ev_rep["evCalibration"]["perQuestion"]}
            self.assertAlmostEqual(pq_map["Q1"]["pBreakEven"], 0.25, places=4)
            self.assertEqual(pq_map["Q1"]["verdict"], "neg_ev_gamble")
            self.assertEqual(ev_rep["evCalibration"]["negEvGamblesCount"], 1)
            self.assertAlmostEqual(ev_rep["evCalibration"]["marksLostToNegEv"], 1.0, places=2)
            q2_graded = next(q for q in ev_rep["questions"] if q["id"] == "Q2")
            self.assertEqual(q2_graded["rowResults"], {"P": "correct", "Q": "wrong"})

            # P1 #3: Idempotent complete_interleave_session() + confidence=0.0 preserved & bool/NaN/str handled
            built = ilv.build_interleave_set(ws, n=3, seed=7, kg_dir=kg)
            self.assertTrue(built["ok"])
            sid = built["sessionId"]
            i0 = built["items"][0]
            i1 = built["items"][1]
            c1 = ilv.complete_interleave_session(
                ws,
                session_id=sid,
                results={
                    i0["itemId"]: {"selectedPrinciple": i0["principleChoices"][0]["id"], "confidence": 0.0, "solved": True},
                    i1["itemId"]: {"selectedPrinciple": i1["principleChoices"][0]["id"], "confidence": "not-a-num", "solved": False},
                },
                kg_dir=kg,
            )
            self.assertTrue(c1["ok"])
            self.assertFalse(c1.get("alreadyCompleted", False))
            sess_rows = [json.loads(ln) for ln in (kg / "interleave-sessions.jsonl").read_text(encoding="utf-8").splitlines() if ln.strip()]
            saved_i0 = next(it for it in sess_rows[-1]["items"] if it["itemId"] == i0["itemId"])
            self.assertEqual(saved_i0["step1Result"]["confidence"], 0.0)
            rev_before = len([ln for ln in (kg / "revlog.jsonl").read_text(encoding="utf-8").splitlines() if ln.strip()])
            self.assertGreaterEqual(rev_before, 2)
            rev_i0 = json.loads((kg / "revlog.jsonl").read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(rev_i0.get("learnerConfidence"), 0.0)
            mis_before = len([ln for ln in (kg / "mistakes.jsonl").read_text(encoding="utf-8").splitlines() if ln.strip()]) if (kg / "mistakes.jsonl").exists() else 0

            c2 = ilv.complete_interleave_session(
                ws,
                session_id=sid,
                results={i0["itemId"]: {"selectedPrinciple": i0["principleChoices"][0]["id"], "confidence": 0.0, "solved": True}},
                kg_dir=kg,
            )
            self.assertTrue(c2["ok"])
            self.assertTrue(c2.get("alreadyCompleted"))
            self.assertEqual(c2["srsUpdated"], 0)
            self.assertEqual(c2["mistakesLogged"], 0)
            rev_after = len([ln for ln in (kg / "revlog.jsonl").read_text(encoding="utf-8").splitlines() if ln.strip()])
            mis_after = len([ln for ln in (kg / "mistakes.jsonl").read_text(encoding="utf-8").splitlines() if ln.strip()]) if (kg / "mistakes.jsonl").exists() else 0
            self.assertEqual(rev_after, rev_before, "Second complete_interleave_session duplicated revlog.jsonl!")
            self.assertEqual(mis_after, mis_before, "Second complete_interleave_session duplicated mistakes.jsonl!")

            # P1 #4 + P2 #5: CLI human output prints lever title, numeric Tier-1 accuracy, and propsFile
            buf_rd = io.StringIO()
            with contextlib.redirect_stdout(buf_rd):
                rc_rd = cli_mod._cmd_readiness(
                    argparse.Namespace(workspace=str(ws), premortem_only=False, exam_date=None, subject=None, max_marks=100, json=False)
                )
            self.assertEqual(rc_rd, 0)
            rd_txt = buf_rd.getvalue()
            self.assertIn("Best next steps", rd_txt)
            self.assertIn("    1. ", rd_txt)
            self.assertNotIn("Blunder Fork", rd_txt)
            self.assertNotIn("{'kind':", rd_txt, f"_cmd_readiness dumped raw dict repr: {rd_txt}")

            buf_ilv = io.StringIO()
            with contextlib.redirect_stdout(buf_ilv):
                rc_ilv = cli_mod._cmd_interleave(
                    argparse.Namespace(workspace=str(ws), action="stats", subjects=None, json=False)
                )
            self.assertEqual(rc_ilv, 0)
            ilv_txt = buf_ilv.getvalue()
            self.assertNotIn("Tier-1 accuracy=n/a", ilv_txt)
            self.assertRegex(ilv_txt, r"Tier-1 accuracy=\d+(?:\.\d+)?%")

            csv_sample = ws / "sources" / "cli-cards.csv"
            csv_sample.write_text("Front,Back\nWork Energy Theorem,W_net = delta K\n", encoding="utf-8")
            buf_imp = io.StringIO()
            with contextlib.redirect_stdout(buf_imp):
                rc_imp = cli_mod._cmd_import(
                    argparse.Namespace(
                        workspace=str(ws),
                        target=str(csv_sample),
                        kind="cards",
                        slug=None,
                        subject="Physics",
                        register_tracker=False,
                        dry_run=True,
                        json=False,
                    )
                )
            self.assertEqual(rc_imp, 0)
            self.assertIn("propositions.jsonl", buf_imp.getvalue())

            # Live server checks: P0 #1 (/api/readiness & /mcp synt_readiness), P1 #5 (corrupt .apkg -> 400),
            # P2 #2 (dryRun base64 upload does NOT persist to sources/_uploads), P2 #3 (PATCH /mcp -> 405 Allow: POST)
            srv = srv_mod.create_server(workspace=ws, host="127.0.0.1", port=0, lan=False)
            t_srv = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
            t_srv.start()
            try:
                port = srv.bound_port
                tok_hdr = {"X-Syntopik-Token": srv.csrf_token}

                # P0 #1 over HTTP GET /api/readiness & POST /mcp synt_readiness
                st_r, _, raw_r = _req(port, "GET", "/api/readiness")
                self.assertEqual(st_r, 200)
                self.assertNotIn("SECRET_REFUTATION_TOKEN_LONG_12345", raw_r.decode("utf-8"))
                self.assertNotIn("SHORT_SECRET_LINE2_XYZ", raw_r.decode("utf-8"))

                for pm_only in (False, True):
                    st_m, _, raw_m = _req(
                        port,
                        "POST",
                        "/mcp",
                        headers=tok_hdr,
                        body={
                            "jsonrpc": "2.0",
                            "id": 99,
                            "method": "tools/call",
                            "params": {"name": "synt_readiness", "arguments": {"premortemOnly": pm_only}},
                        },
                    )
                    self.assertEqual(st_m, 200, raw_m)
                    self.assertNotIn("SECRET_REFUTATION_TOKEN_LONG_12345", raw_m.decode("utf-8"))
                    self.assertNotIn("SHORT_SECRET_LINE2_XYZ", raw_m.decode("utf-8"))

                # P1 #5: corrupt .apkg returns HTTP 400 (NOT 500)
                corrupt_b64 = base64.b64encode(corrupt_apkg.read_bytes()).decode("ascii")
                st_bad_apkg, _, raw_bad_apkg = _req(
                    port,
                    "POST",
                    "/api/import",
                    headers=tok_hdr,
                    body={"kind": "anki", "filename": "corrupt.apkg", "contentBase64": corrupt_b64, "dryRun": True},
                )
                self.assertEqual(st_bad_apkg, 400, f"Expected 400 for corrupt .apkg, got {st_bad_apkg}: {raw_bad_apkg!r}")

                # P2 #2: dryRun base64 upload MUST NOT persist into sources/_uploads/
                uploads_dir = ws / "sources" / "_uploads"
                csv_b64 = base64.b64encode(b"Front,Back\nDry Run Card,Zero disk persistence\n").decode("ascii")
                st_dry, _, raw_dry = _req(
                    port,
                    "POST",
                    "/api/import",
                    headers=tok_hdr,
                    body={"kind": "cards", "filename": "dryrun-only.csv", "contentBase64": csv_b64, "dryRun": True},
                )
                self.assertEqual(st_dry, 200, raw_dry)
                self.assertFalse(
                    uploads_dir.exists() and any(uploads_dir.iterdir()),
                    f"dryRun upload persisted file into {uploads_dir}!",
                )

                # P2 #3: PATCH /mcp returns 405 with Allow: POST
                st_patch, hdrs_patch, _ = _req(port, "PATCH", "/mcp", headers=tok_hdr, body={})
                self.assertEqual(st_patch, 405)
                self.assertEqual(hdrs_patch.get("allow"), "POST")
            finally:
                srv.shutdown()
                srv.server_close()
                t_srv.join(timeout=3.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
