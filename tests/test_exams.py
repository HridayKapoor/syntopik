#!/usr/bin/env python3
"""Syntopik regression suite: Mistake analytics, CBT exam runner and PYQ banks.

Shared fixtures live in tests/_helpers.py. Run via:
    python3 -B -m unittest discover -s tests -t tests -v
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import re
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from _helpers import (
    ROOT,
    SCRIPTS_DIR,
    _EncodedStringIO,
    call_json_cmd,
    load_script_module,
    requires,
    run_cli,
)


class TestMistakeAnalytics(unittest.TestCase):
    """4. P0.1 Mistake Taxonomy, Analytics, Cheatsheet Export & Turn-0 Hook Injection."""

    @requires(binaries=("typst",))
    def test_mistake_log_summary_export_typst_and_session_hook(self) -> None:
        ma = load_script_module("mistake_analytics.py")
        cs = load_script_module("cheatsheet_pdf.py")

        with tempfile.TemporaryDirectory(prefix="mlh-p01-") as tmp:
            tmp_dir = Path(tmp)
            kg_dir = tmp_dir / "kg"
            kg_dir.mkdir(parents=True, exist_ok=True)

            # (a) Reject invalid category with non-zero exit
            bad = run_cli([
                sys.executable, "-B", str(SCRIPTS_DIR / "mistake_analytics.py"),
                "log",
                "--kg-dir", str(kg_dir),
                "--no-sync",
                "--subject", "Physics",
                "--chapter", "Work-Energy",
                "--category", "not_a_real_category",
                "--trigger-cue", "spring compression",
                "--wrong-move", "guessed sign",
                "--correct-move", "check restoring direction",
            ])
            self.assertNotEqual(bad.returncode, 0, "Invalid category must fail with non-zero exit")

            # (b) Log 4 valid mistakes across 2 chapters (1 via CLI, 3 via in-process main for speed)
            cli_log = run_cli([
                sys.executable, "-B", str(SCRIPTS_DIR / "mistake_analytics.py"),
                "log",
                "--kg-dir", str(kg_dir),
                "--no-sync",
                "--subject", "Physics",
                "--chapter", "Work-Energy",
                "--category", "sign_error",
                "--prop-id", "phys-work-spring",
                "--trigger-cue", "Spring displaced from $x_1$ to $x_2$",
                "--wrong-move", "Wrote $W_s = +\\frac{1}{2}k(x_2^2 - x_1^2)$",
                "--correct-move", "Use $W_s = -\\Delta U = \\frac{1}{2}k(x_1^2 - x_2^2)$",
                "--source-ref", "JEE-Main-2024",
                "--json",
            ])
            self.assertEqual(cli_log.returncode, 0, f"mistake_analytics log failed: {cli_log.stderr}")
            rec1 = json.loads(cli_log.stdout)
            self.assertRegex(rec1["id"], r"^m-[0-9a-f]{8}$")
            self.assertEqual(rec1["category"], "sign_error")

            ma.append_mistake(
                kg_dir,
                subject="Physics",
                chapter="Work-Energy",
                category="sign_error",
                prop_id="phys-work-spring",
                trigger_cue="Friction on moving block",
                wrong_move="Assumed kinetic friction always does negative work",
                correct_move="Check displacement in ground frame before assigning sign",
                source_ref="PYQ-2023",
                no_sync=True,
            )
            ma.append_mistake(
                kg_dir,
                subject="Physics",
                chapter="Work-Energy",
                category="condition_missed",
                prop_id="phys-cons-energy",
                trigger_cue="Inelastic collision on smooth track",
                wrong_move="Conserved mechanical energy across impact",
                correct_move="Conserve momentum across impact, energy only after",
                no_sync=True,
            )
            ma.append_mistake(
                kg_dir,
                subject="Chemistry",
                chapter="Ionic Equilibrium",
                category="unit_trap",
                prop_id="chem-dilute-ph",
                trigger_cue="Dilute $10^{-8}\\text{ M}$ HCl pH question",
                wrong_move="Took $-\\log_{10}(10^{-8}) = 8$",
                correct_move="Include water $K_w = 10^{-14}$ in exact charge balance",
                no_sync=True,
            )

            # (c) summary --json ranks top categories & hottest chapters accurately
            buf_sum = _EncodedStringIO()
            with contextlib.redirect_stdout(buf_sum):
                ma.main(["summary", "--kg-dir", str(kg_dir), "--json"])
            summary = json.loads(buf_sum.getvalue())
            self.assertEqual(summary["total"], 4)
            self.assertEqual(summary["topCategories"][0]["category"], "sign_error")
            self.assertEqual(summary["topCategories"][0]["count"], 2)
            self.assertEqual(summary["hottestChapters"][0]["chapter"], "Work-Energy")
            self.assertEqual(summary["hottestChapters"][0]["count"], 3)
            self.assertGreaterEqual(len(summary["repeatTraps"]), 1)

            # (d) export-traps --chapter Work-Energy --format cheatsheet -> compiles to real PDF via Typst
            trap_json = tmp_dir / "work-energy-traps.json"
            trap_pdf = tmp_dir / "work-energy-traps.pdf"
            buf_exp = _EncodedStringIO()
            with contextlib.redirect_stdout(buf_exp):
                ma.main([
                    "export-traps",
                    "--kg-dir", str(kg_dir),
                    "--chapter", "Work-Energy",
                    "--format", "cheatsheet",
                    "--out", str(trap_json),
                ])
            exp_meta = json.loads(buf_exp.getvalue())
            self.assertEqual(exp_meta["matchedMistakes"], 3)
            self.assertTrue(trap_json.exists())

            pdf_res = call_json_cmd(
                cs.cmd_build,
                argparse.Namespace(
                    input=str(trap_json),
                    out=str(trap_pdf),
                    columns=2,
                    paper="a4",
                    accent="crimson",
                    keep_typ=False,
                ),
            )
            self.assertTrue(trap_pdf.exists())
            self.assertGreater(pdf_res["bytes"], 1000)
            self.assertTrue(pdf_res["withinTwoPages"])

            # (e) Sandboxed Turn-0 hook check via MLH_KG_DIR verifies mistake trap injection
            env = {**os.environ, "MLH_KG_DIR": str(kg_dir)}
            ss = run_cli(
                ["node", str(ROOT / ".agents" / "hooks" / "session-start.mjs")],
                stdin_text="{}",
                env=env,
            )
            self.assertEqual(ss.returncode, 0, ss.stderr)
            msg = json.loads(ss.stdout)["injectSteps"][0]["systemMessage"]["systemMessage"]
            self.assertIn("Top recurring mistake traps (4 logged)", msg)
            self.assertIn("sign_error (2)", msg)
            self.assertIn("Work-Energy (3)", msg)


class TestExamRunner(unittest.TestCase):
    """5. P0.3 Offline NTA JEE CBT Builder, Template & Deterministic Grader."""

    def test_exam_build_xss_safety_and_full_jee_marking_pipeline(self) -> None:
        er = load_script_module("exam_runner.py")
        self.assertTrue((ROOT / "demos" / "exam-cbt-template.html").is_file())

        with tempfile.TemporaryDirectory(prefix="mlh-p03-") as tmp:
            tmp_dir = Path(tmp)
            kg_dir = tmp_dir / "kg"
            kg_dir.mkdir(parents=True, exist_ok=True)

            # Create a tiny local SVG figure to verify standalone inlining
            svg_path = tmp_dir / "mini-circuit.svg"
            svg_path.write_text(
                '<svg xmlns="http://www.w3.org/2000/svg" width="80" height="40"><rect width="80" height="40" fill="#2563eb"/></svg>',
                encoding="utf-8",
            )

            opts4 = [
                {"id": "A", "text": "Option A"},
                {"id": "B", "text": "Option B"},
                {"id": "C", "text": "Option C"},
                {"id": "D", "text": "Option D"},
            ]
            spec = {
                "examSlug": "regression-jee-adv-01",
                "title": "Regression JEE Adv Mock </script><script>alert(1)</script>",
                "examType": "JEE Advanced",
                "durationMinutes": 30,
                "questions": [
                    {
                        "id": "Q1",
                        "type": "single_correct",
                        "subject": "Physics",
                        "chapter": "Mechanics",
                        "stem": "Single correct exact check </script><script>alert(1)</script>",
                        "figure": str(svg_path),
                        "options": opts4,
                        "answer": "B",
                        "marksCorrect": 4,
                        "marksWrong": -1,
                    },
                    {
                        "id": "Q2",
                        "type": "single_correct",
                        "subject": "Physics",
                        "chapter": "Rotational Mechanics",
                        "propId": "phy-rot-rolling",
                        "stem": "Single correct wrong + slow (>180s) triggers time_pressure",
                        "options": opts4,
                        "answer": "A",
                        "marksCorrect": 4,
                        "marksWrong": -1,
                    },
                    {
                        "id": "Q3",
                        "type": "multi_correct",
                        "subject": "Chemistry",
                        "chapter": "Electrochemistry",
                        "stem": "Multi-correct all 4 selected -> +4",
                        "options": opts4,
                        "answer": ["A", "B", "C", "D"],
                    },
                    {
                        "id": "Q4",
                        "type": "multi_correct",
                        "subject": "Chemistry",
                        "chapter": "Electrochemistry",
                        "stem": "Multi-correct 3 of 4 selected -> +3",
                        "options": opts4,
                        "answer": ["A", "B", "C", "D"],
                    },
                    {
                        "id": "Q5",
                        "type": "multi_correct",
                        "subject": "Chemistry",
                        "chapter": "Electrochemistry",
                        "stem": "Multi-correct 2 of 3 selected -> +2",
                        "options": opts4,
                        "answer": ["A", "B", "C"],
                    },
                    {
                        "id": "Q6",
                        "type": "multi_correct",
                        "subject": "Chemistry",
                        "chapter": "Electrochemistry",
                        "stem": "Multi-correct 1 of 2 selected -> +1",
                        "options": opts4,
                        "answer": ["A", "B"],
                    },
                    {
                        "id": "Q7",
                        "type": "multi_correct",
                        "subject": "Chemistry",
                        "chapter": "Electrochemistry",
                        "stem": "Multi-correct unattempted -> 0",
                        "options": opts4,
                        "answer": ["A", "B"],
                    },
                    {
                        "id": "Q8",
                        "type": "multi_correct",
                        "subject": "Chemistry",
                        "chapter": "Electrochemistry",
                        "propId": "chem-elec-e0",
                        "trapCategory": "misconception",
                        "trapNote": "E0 is intensive, never scales with stoichiometric multiplier.",
                        "stem": "Multi-correct with any wrong option -> -2",
                        "options": opts4,
                        "answer": ["A", "B"],
                    },
                    {
                        "id": "Q9",
                        "type": "numerical",
                        "subject": "Maths",
                        "chapter": "Calculus",
                        "stem": "Numerical boundary tolerance check (1.25 +/- 0.01)",
                        "answer": 1.25,
                        "tolerance": 0.01,
                        "marksCorrect": 4,
                        "marksWrong": 0,
                    },
                    {
                        "id": "Q10",
                        "type": "numerical",
                        "subject": "Maths",
                        "chapter": "Calculus",
                        "stem": "Numerical JEE Main Sec B negative marking (-1)",
                        "answer": 4.5,
                        "tolerance": 0.01,
                        "marksCorrect": 4,
                        "marksWrong": -1,
                    },
                    {
                        "id": "Q11",
                        "type": "matrix_match",
                        "subject": "Physics",
                        "chapter": "Thermodynamics",
                        "stem": "Matrix match 3 of 4 rows exact -> +3",
                        "rows": [
                            {"id": "P", "text": "Isothermal"},
                            {"id": "Q", "text": "Isochoric"},
                            {"id": "R", "text": "Adiabatic"},
                            {"id": "S", "text": "Isobaric"},
                        ],
                        "cols": [
                            {"id": "1", "text": "dU=0"},
                            {"id": "2", "text": "W=0"},
                            {"id": "3", "text": "Q=0"},
                            {"id": "4", "text": "dP=0"},
                        ],
                        "answer": {"P": ["1"], "Q": ["2"], "R": ["3"], "S": ["4"]},
                        "marksCorrect": 4,
                        "marksPerRow": 1,
                        "marksWrong": 0,
                    },
                ],
            }
            spec_path = tmp_dir / "spec.json"
            spec_path.write_text(json.dumps(spec), encoding="utf-8")

            # (a) Sandboxed build produces standalone HTML with safe window.__EXAM_SPEC__
            out_html = tmp_dir / "mock.html"
            build_meta = call_json_cmd(
                er.cmd_build,
                argparse.Namespace(spec=str(spec_path), out=str(out_html), no_sync=True),
            )
            self.assertEqual(build_meta["questionCount"], 11)
            self.assertEqual(build_meta["maxScore"], 44.0)
            html_text = out_html.read_text(encoding="utf-8")
            self.assertIn("window.__EXAM_SPEC__", html_text)
            self.assertNotIn("</script><script>alert(1)", html_text)
            self.assertIn("\\u003c/script", html_text)
            self.assertIn("rect width=", html_text)

            # Verify every inline <script> block in compiled HTML parses cleanly under Node
            script_blocks = re.findall(r"<script[^>]*>(.*?)</script>", html_text, flags=re.DOTALL | re.IGNORECASE)
            self.assertGreaterEqual(len(script_blocks), 1)
            js_file = tmp_dir / "compiled-cbt.js"
            js_file.write_text("\n;\n".join(script_blocks), encoding="utf-8")
            chk = run_cli(["node", "--check", str(js_file)])
            self.assertEqual(chk.returncode, 0, f"Compiled CBT JS syntax error: {chk.stderr}")

            # (b) Sandboxed grade verifies exact partial marking, slow-Q, mistakes, exam-attempts, and SRS
            attempt = {
                "schemaVersion": 1,
                "examSlug": "regression-jee-adv-01",
                "timeSpentSec": 900,
                "responses": {
                    "Q1": {"answer": "B", "status": "answered", "timeSpentSec": 45},
                    "Q2": {"answer": "C", "status": "answered", "timeSpentSec": 215},
                    "Q3": {"answer": ["A", "B", "C", "D"], "status": "answered", "timeSpentSec": 60},
                    "Q4": {"answer": ["A", "B", "C"], "status": "answered_marked", "timeSpentSec": 70},
                    "Q5": {"answer": ["A", "B"], "status": "answered", "timeSpentSec": 55},
                    "Q6": {"answer": ["A"], "status": "answered", "timeSpentSec": 40},
                    "Q7": {"answer": [], "status": "marked", "timeSpentSec": 25},
                    "Q8": {"answer": ["A", "D"], "status": "answered", "timeSpentSec": 80},
                    "Q9": {"answer": "1.24", "status": "answered", "timeSpentSec": 50},
                    "Q10": {"answer": "4.70", "status": "answered", "timeSpentSec": 60},
                    "Q11": {"answer": {"P": ["1"], "Q": ["2"], "R": ["3"], "S": ["1"]}, "status": "answered", "timeSpentSec": 95},
                },
            }
            attempt_path = tmp_dir / "attempt.json"
            attempt_path.write_text(json.dumps(attempt), encoding="utf-8")

            report = call_json_cmd(
                er.cmd_grade,
                argparse.Namespace(
                    spec=str(spec_path),
                    attempt=str(attempt_path),
                    kg_dir=str(kg_dir),
                    no_sync=True,
                    no_log=False,
                    no_srs=False,
                    slow_sec=180,
                    out_report=None,
                    json=True,
                ),
            )

            marks_by_id = {q["id"]: q["marks"] for q in report["questions"]}
            self.assertEqual(marks_by_id, {
                "Q1": 4.0,
                "Q2": -1.0,
                "Q3": 4.0,
                "Q4": 3.0,
                "Q5": 2.0,
                "Q6": 1.0,
                "Q7": 0.0,
                "Q8": -2.0,
                "Q9": 4.0,
                "Q10": -1.0,
                "Q11": 3.0,
            })
            self.assertEqual(report["score"], 17.0)
            self.assertEqual(report["maxScore"], 44.0)
            self.assertEqual(report["correctCount"], 3)
            self.assertEqual(report["partialCount"], 4)
            self.assertEqual(report["wrongCount"], 3)
            self.assertEqual(report["unattemptedCount"], 1)
            self.assertEqual(report["negativeMarksLost"], 4.0)
            self.assertEqual(report["slowQuestions"], ["Q2"])
            self.assertEqual(report["srsErrors"], [])
            self.assertEqual(report["mistakesLogged"], 7)
            self.assertEqual(report["srsUpdated"], 2)

            # Verify sandboxed ledgers written cleanly
            mistakes_lines = [
                json.loads(ln)
                for ln in (kg_dir / "mistakes.jsonl").read_text(encoding="utf-8").splitlines()
                if ln.strip()
            ]
            self.assertEqual(len(mistakes_lines), 7)
            by_src = {m["sourceRef"]: m for m in mistakes_lines}
            self.assertEqual(by_src["exam:regression-jee-adv-01:Q2"]["category"], "time_pressure")
            self.assertEqual(by_src["exam:regression-jee-adv-01:Q8"]["category"], "misconception")

            attempt_lines = [
                json.loads(ln)
                for ln in (kg_dir / "exam-attempts.jsonl").read_text(encoding="utf-8").splitlines()
                if ln.strip()
            ]
            self.assertEqual(len(attempt_lines), 1)
            self.assertEqual(attempt_lines[0]["score"], 17.0)
            self.assertEqual(attempt_lines[0]["slowQuestions"], ["Q2"])

            props_lines = [
                json.loads(ln)
                for ln in (kg_dir / "propositions.jsonl").read_text(encoding="utf-8").splitlines()
                if ln.strip()
            ]
            props_by_id = {p["id"]: p for p in props_lines}
            self.assertIn("phy-rot-rolling", props_by_id)
            self.assertEqual(props_by_id["phy-rot-rolling"]["status"], "shaky")
            self.assertIn("chem-elec-e0", props_by_id)
            self.assertEqual(props_by_id["chem-elec-e0"]["status"], "misconception")

            # (c) Non-finite numerical inputs must grade as unattempted (0 marks, 0 neg loss)
            num_q = er.normalize_spec(spec)["questions"][9]  # Q10: +4 / -1
            for bad_tok in ("", ".", "-", "NaN", "inf", "-inf", "Infinity"):
                sc_bad = er.score_question(num_q, bad_tok)
                self.assertEqual(sc_bad["status"], "unattempted", f"Token {bad_tok!r} must be unattempted")
                self.assertEqual(sc_bad["marks"], 0.0)
                self.assertEqual(sc_bad["negativeLost"], 0.0)


class TestEdunitiPyqBank(unittest.TestCase):
    """16. Curated Eduniti JEE Main Physics PYQ extractor & query CLI (sandboxed in /tmp)."""

    def test_ingest_zip_skips_website_boilerplate_and_exports_chapter(self) -> None:
        ed = load_script_module("eduniti_pyq.py")
        with tempfile.TemporaryDirectory(prefix="mlh-eduniti-test-") as tmp:
            t = Path(tmp)
            zp = t / "portal.zip"
            sample_data = {
                "chaptersOrder": ["Simple Harmonic Motion", "Kinematics"],
                "papers": [
                    {"id": "jee_2024_top200", "title": "JEE Main 2024", "shortTitle": "2024", "yearLabel": "2024", "category": "pyq", "totalQuestions": 2},
                    {"id": "comp_rank_booster", "title": "Rank Booster", "shortTitle": "RB", "yearLabel": "RB", "category": "rb", "totalQuestions": 1},
                ],
                "questions": [
                    {
                        "uid": "jee_2024_top200::12",
                        "paperId": "jee_2024_top200",
                        "paperTitle": "JEE Main 2024",
                        "year": 2024,
                        "yearLabel": "2024",
                        "number": 12,
                        "displayNumber": "Q12",
                        "chapter": "Simple Harmonic Motion",
                        "canonicalChapter": "Simple Harmonic Motion",
                        "examTag": "JEE MAIN 2024",
                        "type": "mcq",
                        "promptLatex": "Find time period $T$ for spring-block SHM.",
                        "options": ["$2\\pi\\sqrt{m/k}$", "$\\pi\\sqrt{m/k}$"],
                        "hasDiagram": True,
                        "diagramDataUrl": "assets/diagrams/sample.png",
                        "correctAnswer": "A",
                    },
                    {
                        "uid": "comp_rank_booster::5",
                        "paperId": "comp_rank_booster",
                        "paperTitle": "Rank Booster",
                        "number": 5,
                        "displayNumber": "Q5",
                        "chapter": "Simple Harmonic Motion",
                        "canonicalChapter": "Simple Harmonic Motion",
                        "examTag": "RANK BOOSTER",
                        "type": "numerical",
                        "promptLatex": "Compute amplitude in cm.",
                        "options": [],
                        "hasDiagram": False,
                        "correctAnswer": "4",
                    },
                ],
            }
            with zipfile.ZipFile(zp, "w") as zf:
                zf.writestr("index.html", "<html>junk</html>")
                zf.writestr("styles.css", "body{}")
                zf.writestr("vendor/katex/katex.min.js", "// junk")
                zf.writestr("assets/diagrams/sample.png", b"\x89PNG\r\n\x1a\nfake")
                zf.writestr("data.js", "window.PHYSICS_DATA=" + json.dumps(sample_data) + ";\n")

            out_dir = t / "bank"
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = ed.main(["ingest-zip", str(zp), "--out-dir", str(out_dir), "--json"])
            self.assertEqual(rc, 0)
            ing = json.loads(buf.getvalue())
            self.assertEqual(ing["totalQuestions"], 2)
            self.assertEqual(ing["diagramsExtracted"], 1)
            self.assertGreaterEqual(ing["skippedWebsiteFiles"], 3)
            self.assertTrue((out_dir / "diagrams" / "sample.png").is_file())
            self.assertFalse((out_dir / "index.html").exists())

            bank_file = Path(ing["bankFile"])
            buf_all = io.StringIO()
            with contextlib.redirect_stdout(buf_all):
                ed.main(["export-chapter", "SHM", "--bank", str(bank_file), "--json"])
            exp_all = json.loads(buf_all.getvalue())
            self.assertEqual(exp_all["count"], 2)

            buf_pyq = io.StringIO()
            with contextlib.redirect_stdout(buf_pyq):
                ed.main(["export-chapter", "SHM", "--bank", str(bank_file), "--pyq-only", "--json"])
            exp_pyq = json.loads(buf_pyq.getvalue())
            self.assertEqual(exp_pyq["count"], 1)
            self.assertEqual(exp_pyq["questions"][0]["edunitiUid"], "jee_2024_top200::12")

            buf_show = io.StringIO()
            with contextlib.redirect_stdout(buf_show):
                ed.main(["show", "pyq-jee-2024-top200-12", "--bank", str(bank_file), "--json"])
            shown = json.loads(buf_show.getvalue())
            self.assertEqual(shown["correctAnswer"], "A")
            self.assertTrue(shown["diagramAbsPath"].endswith("sample.png"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
