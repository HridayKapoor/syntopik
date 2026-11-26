#!/usr/bin/env python3
"""Automated Regression Test Suite for Syntopik (Universal AI Deep-Mastery OS).

Standard-library `unittest` only. 100% sandboxed in `tempfile.TemporaryDirectory()`
— NEVER mutates real `knowledge-graph/`, `chapters/`, `demos/`, `animations/`, or `exams/`.

Run via:
    .venv/bin/python -B -m unittest discover -s tests -v
"""

from __future__ import annotations

import argparse
import concurrent.futures
import contextlib
import http.client
import importlib.util
import io
import json
import math
import os
import py_compile
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import tomllib
import unittest
import unittest.mock
import urllib.error
import urllib.parse
import urllib.request
import warnings
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# Never pollute scripts/ or tests/ with __pycache__ bytecode during test runs.
sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
SCRIPTS_DIR = ROOT / "scripts"
SKILLS_DIR = ROOT / ".agents" / "skills"
HOOKS_JSON = ROOT / ".agents" / "hooks.json"

_MODULE_CACHE: dict[str, Any] = {}


def load_script_module(script_name: str) -> Any:
    """Import a script from scripts/ cleanly without leaving bytecode behind."""
    if script_name in _MODULE_CACHE:
        return _MODULE_CACHE[script_name]
    path = SCRIPTS_DIR / script_name
    if not path.exists():
        raise FileNotFoundError(f"Expected script not found: {path}")
    mod_name = f"_mlh_test_{path.stem}"
    spec = importlib.util.spec_from_file_location(mod_name, str(path))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not create module spec for {path}")
    mod = importlib.util.module_from_spec(spec)
    # Allow cross-script imports (e.g. exam_runner importing mistake_analytics)
    scripts_str = str(SCRIPTS_DIR)
    if scripts_str not in sys.path:
        sys.path.insert(0, scripts_str)
    spec.loader.exec_module(mod)
    _MODULE_CACHE[script_name] = mod
    return mod


class _EncodedStringIO(io.StringIO):
    """StringIO with a utf-8 .encoding attribute (required by libraries like py2opsin)."""

    @property
    def encoding(self) -> str:
        return "utf-8"


def call_json_cmd(fn, args: argparse.Namespace) -> Any:
    """Invoke a script's cmd_* function in-process and parse its JSON stdout."""
    buf = _EncodedStringIO()
    with contextlib.redirect_stdout(buf):
        fn(args)
    raw = buf.getvalue().strip()
    return json.loads(raw)


def parse_yaml_frontmatter(text: str) -> dict[str, str] | None:
    """Minimal stdlib YAML frontmatter parser supporting single-line and >-/| multiline values."""
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, flags=re.DOTALL)
    if not m:
        return None
    fields: dict[str, str] = {}
    lines = m.group(1).splitlines()
    idx = 0
    while idx < len(lines):
        line = lines[idx]
        km = re.match(r"^([A-Za-z0-9_-]+)\s*:\s*(.*)$", line)
        if not km:
            idx += 1
            continue
        key = km.group(1).strip()
        val = km.group(2).strip()
        if val in (">", ">-", ">+", "|", "|-", "|+"):
            block_lines: list[str] = []
            idx += 1
            while idx < len(lines) and (lines[idx].startswith((" ", "\t")) or not lines[idx].strip()):
                if lines[idx].strip():
                    block_lines.append(lines[idx].strip())
                idx += 1
            fields[key] = " ".join(block_lines).strip()
        else:
            fields[key] = val.strip("\"'")
            idx += 1
    return fields


def run_cli(
    cmd: list[str],
    *,
    stdin_text: str | None = None,
    timeout: float = 15.0,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=str(ROOT),
        input=stdin_text,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
        check=False,
    )


class TestSyntaxAndCLI(unittest.TestCase):
    """1. Syntax compilation (zero SyntaxWarning) & CLI --help contracts."""

    def test_all_python_scripts_compile_without_syntax_warnings(self) -> None:
        py_files = (
            sorted((ROOT / "syntopik").glob("*.py"))
            + sorted(SCRIPTS_DIR.glob("*.py"))
            + sorted((ROOT / "benchmarks").glob("*.py"))
            + sorted((ROOT / "tests").glob("*.py"))
        )
        self.assertGreaterEqual(len(py_files), 50, "Expected at least 50 Python files across syntopik/, scripts/, benchmarks/, tests/")
        with tempfile.TemporaryDirectory(prefix="mlh-pycompile-") as tmp:
            tmp_dir = Path(tmp)
            for idx, py_path in enumerate(py_files):
                with self.subTest(script=py_path.name):
                    with warnings.catch_warnings():
                        warnings.simplefilter("error", SyntaxWarning)
                        py_compile.compile(
                            str(py_path),
                            cfile=str(tmp_dir / f"{idx}_{py_path.stem}.pyc"),
                            doraise=True,
                        )

    def test_all_node_scripts_syntax_check(self) -> None:
        node_files = [
            ROOT / "bin" / "syntopik.mjs",
            SCRIPTS_DIR / "sync-graph.mjs",
            ROOT / ".agents" / "hooks" / "session-start.mjs",
            ROOT / ".agents" / "hooks" / "post-graph-sync.mjs",
            ROOT / "mcp-app" / "mlh-studio" / "dist" / "server.mjs",
        ]
        for nf in node_files:
            with self.subTest(script=nf.name):
                self.assertTrue(nf.exists(), f"Missing Node script: {nf}")
                proc = run_cli(["node", "--check", str(nf)])
                self.assertEqual(proc.returncode, 0, f"node --check failed for {nf}: {proc.stderr}")

    def test_all_cli_scripts_help_exit_zero(self) -> None:
        cli_scripts = [
            "pdf_studio.py",
            "math_verify.py",
            "chem_3d.py",
            "chem_physical.py",
            "circuit_draw.py",
            "srs_engine.py",
            "cheatsheet_pdf.py",
            "narrate_video.py",
            "study_doctor.py",
            "mistake_analytics.py",
            "exam_runner.py",
            "physics_draw.py",
            "organic_reagents.py",
            "math_plot.py",
            "install_mcp_app.py",
            "chapter_tracker.py",
            "graph_intel.py",
            "audio_brief.py",
            "daily_plan.py",
            "backup_harness.py",
            "eduniti_pyq.py",
            "setup_harness.py",
            "code_verify.py",
            "research_scout.py",
            "ingest_source.py",
            "vault_sync.py",
            "pedagogy_eval.py",
            "fork_replay.py",
            "falsify_arena.py",
            "decision_blitz.py",
            "learner_profile.py",
            "derivation_forge.py",
            "viva_chamber.py",
            "build_starter_packs.py",
        ]

        def _check_help(name: str) -> tuple[str, int, str, str]:
            path = SCRIPTS_DIR / name
            if not path.exists():
                return (name, -1, "", f"Missing script: {path}")
            proc = run_cli([sys.executable, "-B", str(path), "--help"])
            return (name, proc.returncode, proc.stdout, proc.stderr)

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
            results = list(pool.map(_check_help, cli_scripts))

        for name, rc, out, err in results:
            with self.subTest(script=name):
                self.assertEqual(rc, 0, f"{name} --help failed (rc={rc}): {err}")
                self.assertTrue(out.strip(), f"{name} --help produced empty stdout")
                self.assertNotIn("Traceback", err)
                if name == "narrate_video.py":
                    # Guard against unescaped '%' argparse formatting regression
                    self.assertIn("+5%", out)


class TestStemEngines(unittest.TestCase):
    """2. Existing STEM engines & Blueprint Section 2.5 Regression Watchlist."""

    def test_math_verify_ast_injection_refusal_and_core(self) -> None:
        mv = load_script_module("math_verify.py")

        # (a) Unsafe AST injection refusal (must fail cleanly WITHOUT executing)
        with tempfile.TemporaryDirectory(prefix="mlh-mv-") as tmp:
            canary = Path(tmp) / "pwned.txt"
            payloads = [
                f"__import__('os').system('touch {canary}')",
                "__import__('os').system('echo pwn')",
                "open('/etc/passwd').read()",
            ]
            for expr in payloads:
                with self.subTest(payload=expr):
                    args = argparse.Namespace(expr=expr, defs="", expect=None)
                    with self.assertRaises(SystemExit) as ctx:
                        mv.cmd_check_dim(args)
                    self.assertNotEqual(ctx.exception.code, 0)
            self.assertFalse(canary.exists(), "AST injection executed shell command!")

        # (b) Valid dimensional consistency check with CODATA constants & user defs
        dim_spring = call_json_cmd(
            mv.cmd_check_dim,
            argparse.Namespace(expr="sqrt(k/m)", defs="k=N/m,m=kg", expect="1/s"),
        )
        self.assertTrue(dim_spring["consistent"])

        dim_c = call_json_cmd(
            mv.cmd_check_dim,
            argparse.Namespace(expr="1/sqrt(mu_0*epsilon_0)", defs="", expect="m/s"),
        )
        self.assertTrue(dim_c["consistent"])
        self.assertAlmostEqual(dim_c["siValue"], 299792458.0, delta=10.0)

        # (c) Complex roots in `solve` (Regression Watchlist #4)
        solved = call_json_cmd(
            mv.cmd_solve,
            argparse.Namespace(equation="x**2 + 1 = 0", var="x", vars="x", real=False),
        )
        self.assertEqual(set(solved["solutions"]), {"-I", "I"})

        # (d) Symbolic `eval` & JEE `error-prop`
        ev = call_json_cmd(
            mv.cmd_eval,
            argparse.Namespace(expr="diff(sin(x**2), x)", vars="x", complex=False, latex=False),
        )
        self.assertEqual(ev["simplified"], "2*x*cos(x**2)")

        err = call_json_cmd(
            mv.cmd_error_prop,
            argparse.Namespace(expr="4*pi^2*L/T^2", vars_unc="L=1.00+/-0.01, T=2.00+/-0.02"),
        )
        self.assertAlmostEqual(err["nominal"], 9.8696044, places=4)
        self.assertAlmostEqual(err["maxRelPercent_JEE"], 3.0, places=3)
        self.assertAlmostEqual(err["rssRelPercent"], 2.2361, places=3)

    def test_chem_physical_dilute_hcl_ph_and_redox_conservation(self) -> None:
        cp = load_script_module("chem_physical.py")

        # (a) Exact charge-balance pH of 10^-8 M HCl must be < 7.0 (~6.9786), NOT 8.0!
        ph_res = call_json_cmd(
            cp.cmd_ph,
            argparse.Namespace(mode="strong-acid", conc=1e-8, kw=1e-14),
        )
        self.assertLess(ph_res["pH"], 7.0)
        self.assertAlmostEqual(ph_res["pH"], 6.9786, delta=0.005)
        self.assertTrue(ph_res["waterAutoionizationSignificant"])

        # (b) Acidic ionic redox balancing: MnO4- + Fe2+ -> Mn2+ + Fe3+
        bal = cp.balance_reaction_smart("MnO4- + Fe2+ -> Mn2+ + Fe3+", medium="acidic")
        self.assertTrue(bal["verified"])
        self.assertEqual(bal["reactants"], {"MnO4-": 1, "Fe+2": 5, "H+": 8})
        self.assertEqual(bal["products"], {"Mn+2": 1, "Fe+3": 5, "H2O": 4})

        # Independently verify atom and net-charge conservation
        lhs_atoms: dict[str, int] = {}
        rhs_atoms: dict[str, int] = {}
        lhs_q = 0
        rhs_q = 0
        for sp_name, coeff in bal["reactants"].items():
            comp, q = cp._parse_species_composition(sp_name)
            lhs_q += coeff * q
            for el, n in comp.items():
                lhs_atoms[el] = lhs_atoms.get(el, 0) + coeff * n
        for sp_name, coeff in bal["products"].items():
            comp, q = cp._parse_species_composition(sp_name)
            rhs_q += coeff * q
            for el, n in comp.items():
                rhs_atoms[el] = rhs_atoms.get(el, 0) + coeff * n
        self.assertEqual(lhs_atoms, rhs_atoms)
        self.assertEqual(lhs_q, rhs_q)
        self.assertEqual(lhs_q, 17)

    def test_chem_3d_offline_py2opsin_and_cip_stereo(self) -> None:
        c3d = load_script_module("chem_3d.py")

        with unittest.mock.patch.object(
            c3d.urllib.request,
            "urlopen",
            side_effect=AssertionError("Network call forbidden in offline chem_3d test"),
        ):
            # (a) Direct SMILES CIP stereochemistry check
            res_smi = call_json_cmd(
                c3d.cmd_inspect,
                argparse.Namespace(input="C[C@H](O)CC", name=False),
            )
            self.assertEqual(len(res_smi["chiralCenters"]), 1)
            self.assertEqual(res_smi["chiralCenters"][0]["cip"], "S")

            # (b) Offline JEE dictionary check
            res_dict = call_json_cmd(
                c3d.cmd_inspect,
                argparse.Namespace(input="(R)-2-butanol", name=True),
            )
            self.assertEqual(res_dict["resolvedVia"], "offline-dict")
            self.assertEqual(res_dict["chiralCenters"][0]["cip"], "R")

            # (c) Offline systematic IUPAC via py2opsin (not in OFFLINE_NAME_TO_SMILES)
            res_opsin = call_json_cmd(
                c3d.cmd_inspect,
                argparse.Namespace(input="(2R)-2-chlorobutane", name=True),
            )
            self.assertEqual(res_opsin["resolvedVia"], "py2opsin")
            self.assertEqual(len(res_opsin["chiralCenters"]), 1)
            self.assertEqual(res_opsin["chiralCenters"][0]["cip"], "R")

            # (d) Zero-CDN offline 3D HTML output (no cdn.jsdelivr.net or external https:// <script src=>)
            with tempfile.TemporaryDirectory(prefix="synt-chem3d-") as tmp3d:
                out_html = Path(tmp3d) / "butanol_3d.html"
                buf3d = _EncodedStringIO()
                with contextlib.redirect_stdout(buf3d):
                    c3d.cmd_render3d(
                        argparse.Namespace(
                            input="C[C@H](O)CC",
                            name=False,
                            title="(S)-2-Butanol",
                            conformers=2,
                            out=str(out_html),
                        )
                    )
                self.assertTrue(out_html.is_file(), "chem_3d render3d did not write HTML")
                html_text = out_html.read_text(encoding="utf-8")
                self.assertNotIn("cdn.jsdelivr.net", html_text)
                self.assertIsNone(
                    re.search(r"<script[^>]+src\s*=\s*['\"]https?://", html_text, flags=re.IGNORECASE),
                    "chem_3d 3D HTML must not embed external https:// <script src=> tags",
                )

    def test_srs_engine_bkt_non_inflation_and_mathjax_anki(self) -> None:
        srs = load_script_module("srs_engine.py")

        with tempfile.TemporaryDirectory(prefix="mlh-srs-") as tmp:
            kg_dir = Path(tmp)
            # Seed a low-prior proposition with inline $...$ and display $$...$$ math
            seed_prop = {
                "id": "phys-work-sign",
                "subject": "Physics",
                "chapter": "Work Energy Power",
                "title": "Work-energy theorem: $W = \\Delta K$ and $$E = x^2$$",
                "kind": "derivation",
                "status": "shaky",
                "bktMastery": 0.10,
                "notes": "Sign trap: always check $x^2 \\ge 0$.",
            }
            (kg_dir / "propositions.jsonl").write_text(json.dumps(seed_prop) + "\n", encoding="utf-8")
            (kg_dir / "chains.jsonl").write_text(
                json.dumps({
                    "from": "phys-newton2",
                    "to": "phys-work-sign",
                    "relation": "derives",
                    "why": "Integrate $F = ma$ over $dx$",
                }) + "\n",
                encoding="utf-8",
            )

            # (a) Failed review (--rating again) from low prior MUST NOT inflate BKT mastery
            rev_again = call_json_cmd(
                srs.cmd_review,
                argparse.Namespace(
                    kg_dir=str(kg_dir),
                    prop_id="phys-work-sign",
                    rating="again",
                    title=None,
                    subject=None,
                    chapter=None,
                    kind="derivation",
                    notes=None,
                    misconception=False,
                    no_sync=True,
                ),
            )
            self.assertLessEqual(rev_again["bktMastery"], 0.10)
            self.assertEqual(rev_again["status"], "shaky")

            # (b) review-queue.md generated cleanly inside sandboxed kg_dir
            rq = kg_dir / "review-queue.md"
            self.assertTrue(rq.exists())
            self.assertIn("phys-work-sign", rq.read_text(encoding="utf-8"))

            # (c) Anki .apkg export converts $...$ -> \(...\) and $$...$$ -> \[...\]
            apkg_path = kg_dir / "test-deck.apkg"
            exp = call_json_cmd(
                srs.cmd_export_anki,
                argparse.Namespace(kg_dir=str(kg_dir), subject=None, out=str(apkg_path)),
            )
            self.assertEqual(exp["exportedCards"], 1)
            self.assertTrue(apkg_path.exists())
            self.assertGreater(apkg_path.stat().st_size, 500)

            with zipfile.ZipFile(apkg_path, "r") as zf:
                db_name = "collection.anki21" if "collection.anki21" in zf.namelist() else "collection.anki2"
                db_path = kg_dir / db_name
                db_path.write_bytes(zf.read(db_name))

            conn = sqlite3.connect(str(db_path))
            try:
                rows = conn.execute("SELECT flds FROM notes").fetchall()
            finally:
                conn.close()
            self.assertEqual(len(rows), 1)
            flds = rows[0][0]
            self.assertIn(r"\(x^2 \ge 0\)", flds)
            self.assertIn(r"\[E = x^2\]", flds)
            self.assertIn(r"\(W = \Delta K\)", flds)
            self.assertNotIn("$x^2", flds)

    def test_pdf_studio_inspect_tree_index_tables_and_clean_json(self) -> None:
        import pymupdf as fitz

        pdf_mod = load_script_module("pdf_studio.py")

        with tempfile.TemporaryDirectory(prefix="mlh-pdf-") as tmp:
            tmp_dir = Path(tmp)
            pdf_path = tmp_dir / "sample-chapter.pdf"

            # Build a realistic 2-page PDF with multi-line heading + bordered table + body text
            doc = fitz.open()
            p1 = doc.new_page(width=595, height=842)
            p1.insert_text((72, 90), "Trigonometric", fontsize=22, fontname="helv")
            p1.insert_text((72, 118), "Equations", fontsize=22, fontname="helv")
            for idx in range(8):
                p1.insert_text(
                    (72, 160 + idx * 16),
                    f"Standard body paragraph line {idx + 1} discussing general solutions of trigonometric identities.",
                    fontsize=10,
                    fontname="helv",
                )
            # Draw a clean 3x3 bordered table
            col_x = [72, 210, 340, 480]
            row_y = [320, 345, 370, 395]
            table_data = [
                ["Function", "Principal", "Period"],
                ["sin(x)", "[-pi/2, pi/2]", "2pi"],
                ["tan(x)", "(-pi/2, pi/2)", "pi"],
            ]
            for r in range(3):
                for c in range(3):
                    rect = fitz.Rect(col_x[c], row_y[r], col_x[c + 1], row_y[r + 1])
                    p1.draw_rect(rect, color=(0, 0, 0), width=0.8)
                    p1.insert_text((col_x[c] + 6, row_y[r] + 16), table_data[r][c], fontsize=10, fontname="helv")

            p2 = doc.new_page(width=595, height=842)
            p2.insert_text((72, 90), "2. Worked Examples", fontsize=18, fontname="helv")
            for idx in range(6):
                p2.insert_text(
                    (72, 130 + idx * 16),
                    f"Worked example body text line {idx + 1} showing cosine symmetry and periodicity.",
                    fontsize=10,
                    fontname="helv",
                )
            doc.save(str(pdf_path))
            doc.close()

            # (a) inspect
            ins = call_json_cmd(
                pdf_mod.cmd_inspect,
                argparse.Namespace(pdf=str(pdf_path), limit=20),
            )
            self.assertEqual(ins["totalPages"], 2)

            # (b) tree-index merges multi-line heading "Trigonometric" + "Equations"
            tree_out = tmp_dir / "tree"
            tree = call_json_cmd(
                pdf_mod.cmd_tree_index,
                argparse.Namespace(pdf=str(pdf_path), out=str(tree_out), chunk_size=12),
            )
            titles = [s["title"] for s in tree["preview"]]
            self.assertTrue(
                any("Trigonometric Equations" in t for t in titles),
                f"Expected merged multi-line heading 'Trigonometric Equations', got {titles}",
            )

            # (c) tables extraction
            tbl = call_json_cmd(
                pdf_mod.cmd_tables,
                argparse.Namespace(pdf=str(pdf_path), pages="1-2", out=None),
            )
            self.assertGreaterEqual(tbl["tableCount"], 1)
            first_md = tbl["pages"][0]["tables"][0]["markdown"]
            self.assertIn("Function", first_md)
            self.assertIn("sin(x)", first_md)

            # (d) Subprocess check on `extract` (exercises pymupdf4llm fd-1 chatter suppression)
            ext_out = tmp_dir / "ext"
            proc = run_cli([
                sys.executable, "-B", str(SCRIPTS_DIR / "pdf_studio.py"),
                "extract", str(pdf_path), "--pages", "1", "--ocr", "never", "--out", str(ext_out),
            ])
            self.assertEqual(proc.returncode, 0, f"pdf_studio extract failed: {proc.stderr}")
            parsed_ext = json.loads(proc.stdout)
            self.assertEqual(parsed_ext["pagesExtracted"], 1)

    def test_cheatsheet_pdf_typst_compile(self) -> None:
        cs = load_script_module("cheatsheet_pdf.py")

        with tempfile.TemporaryDirectory(prefix="mlh-typst-") as tmp:
            tmp_dir = Path(tmp)
            spec_path = tmp_dir / "mini-sheet.json"
            out_pdf = tmp_dir / "mini-sheet.pdf"
            spec = {
                "title": "Electrostatics Quick Sheet",
                "subject": "Physics",
                "examTag": "JEE Main + Advanced",
                "subtitle": "Regression Test Spec",
                "columns": 2,
                "sections": [
                    {
                        "heading": "1. Gauss Law",
                        "items": [
                            {"label": "Flux", "math": r"\oint \vec{E}\cdot d\vec{A} = \frac{q_{\text{in}}}{\varepsilon_0}"}
                        ],
                        "traps": ["Only enclosed charge $q_{\\text{in}}$ contributes to net flux."],
                    }
                ],
                "traps": ["Field inside a conductor cavity is zero only if cavity has no charge."],
                "quickChecks": ["Check units: $\\text{N}\\cdot\\text{m}^2/\\text{C}$."],
            }
            spec_path.write_text(json.dumps(spec), encoding="utf-8")

            res = call_json_cmd(
                cs.cmd_build,
                argparse.Namespace(
                    input=str(spec_path),
                    out=str(out_pdf),
                    columns=2,
                    paper="a4",
                    accent="blue",
                    keep_typ=False,
                ),
            )
            self.assertTrue(out_pdf.exists())
            self.assertGreater(res["bytes"], 1000)
            self.assertEqual(res["pages"], 1)
            self.assertTrue(res["withinTwoPages"])

    def test_narrate_video_pillow_caption_and_overlay_burn(self) -> None:
        from PIL import Image

        nv = load_script_module("narrate_video.py")
        ffmpeg_bin = nv.find_bin("ffmpeg")
        ffprobe_bin = nv.find_bin("ffprobe")

        with tempfile.TemporaryDirectory(prefix="mlh-narrate-") as tmp:
            tmp_dir = Path(tmp)
            cap_png = tmp_dir / "caption.png"
            nv.render_caption_png("Conserve momentum: p_i = p_f", 320, 180, cap_png)
            self.assertTrue(cap_png.exists())
            with Image.open(cap_png) as img:
                self.assertEqual(img.mode, "RGBA")
                self.assertEqual(img.size, (320, 180))
                # Top-left corner must be transparent; bottom caption pill must have visible pixels
                self.assertEqual(img.getpixel((2, 2))[3], 0)
                bottom_band = img.crop((40, 120, 280, 178))
                self.assertGreater(bottom_band.getbbox() is not None, 0)

            # Tiny 0.5s synthetic video + audio + overlay burn-in test
            vid_in = tmp_dir / "in.mp4"
            aud_in = tmp_dir / "in.m4a"
            srt_in = tmp_dir / "in.srt"
            vid_out = tmp_dir / "out.mp4"
            p_v = run_cli([
                ffmpeg_bin, "-y", "-v", "error",
                "-f", "lavfi", "-i", "color=c=0x10141c:s=320x180:d=0.5:r=15",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "ultrafast", str(vid_in),
            ])
            self.assertEqual(p_v.returncode, 0, p_v.stderr)
            p_a = run_cli([
                ffmpeg_bin, "-y", "-v", "error",
                "-f", "lavfi", "-t", "0.5", "-i", "anullsrc=r=48000:cl=stereo",
                "-c:a", "aac", "-b:a", "64k", str(aud_in),
            ])
            self.assertEqual(p_a.returncode, 0, p_a.stderr)
            scheduled = [{"start": 0.05, "end": 0.45, "text": "Conserve momentum: p_i = p_f"}]
            nv.write_srt_file(scheduled, srt_in)

            _padded, mode = nv.mux_video(
                vid_in, aud_in, srt_in, vid_out,
                video_dur=0.5, audio_dur=0.5, burn_subs=True,
                scheduled=scheduled, work_dir=tmp_dir,
                ffmpeg_bin=ffmpeg_bin, ffprobe_bin=ffprobe_bin,
            )
            self.assertIn(mode, ("pillow-overlay", "libass-subtitles"))
            self.assertTrue(vid_out.exists())
            self.assertGreater(vid_out.stat().st_size, 1000)
            self.assertGreaterEqual(nv.probe_duration(vid_out, ffprobe_bin), 0.4)


class TestHooksSkillsBudget(unittest.TestCase):
    """3. Hooks JSON/stdout contracts, Skill YAML frontmatter, & Token-Budget hygiene."""

    def test_hooks_json_valid_and_scripts_exist(self) -> None:
        self.assertTrue(HOOKS_JSON.exists(), "Missing .agents/hooks.json")
        data = json.loads(HOOKS_JSON.read_text(encoding="utf-8"))
        self.assertIsInstance(data, dict)

        commands: list[str] = []

        def _collect(obj: Any) -> None:
            if isinstance(obj, dict):
                if isinstance(obj.get("command"), str):
                    commands.append(obj["command"])
                for v in obj.values():
                    _collect(v)
            elif isinstance(obj, list):
                for item in obj:
                    _collect(item)

        _collect(data)
        self.assertGreaterEqual(len(commands), 2, "Expected at least SessionStart and PostToolUse commands")
        for cmd in commands:
            parts = cmd.strip().split()
            self.assertGreaterEqual(len(parts), 2, f"Malformed hook command: {cmd}")
            script_rel = parts[1]
            resolved = (ROOT / ".agents" / script_rel).resolve()
            self.assertTrue(resolved.exists(), f"Hook script referenced in hooks.json does not exist: {script_rel}")

    def test_session_start_and_post_graph_sync_contracts(self) -> None:
        ss = run_cli(["node", str(ROOT / ".agents" / "hooks" / "session-start.mjs")], stdin_text="{}")
        self.assertEqual(ss.returncode, 0, f"session-start.mjs failed: {ss.stderr}")
        ss_json = json.loads(ss.stdout)
        msg = ss_json["injectSteps"][0]["systemMessage"]["systemMessage"]
        self.assertIsInstance(msg, str)
        self.assertIn("# Syntopik — Live Study Snapshot", msg)
        # Guard against exam-cbt-template.html leaking into Interactive demos count
        self.assertNotIn("exam-cbt-template.html", msg)

        ps = run_cli(["node", str(ROOT / ".agents" / "hooks" / "post-graph-sync.mjs")], stdin_text="{}")
        self.assertEqual(ps.returncode, 0, f"post-graph-sync.mjs failed: {ps.stderr}")
        self.assertEqual(ps.stdout.strip(), "{}")

    def test_all_skills_valid_frontmatter(self) -> None:
        skill_files = sorted(SKILLS_DIR.glob("*/SKILL.md"))
        self.assertEqual(len(skill_files), 17, f"Expected exact 17 skills, found {len(skill_files)}")
        for sk in skill_files:
            with self.subTest(skill=sk.parent.name):
                text = sk.read_text(encoding="utf-8")
                fm = parse_yaml_frontmatter(text)
                self.assertIsNotNone(fm, f"{sk} is missing YAML frontmatter")
                assert fm is not None
                name_val = fm.get("name", "")
                desc_val = fm.get("description", "")
                self.assertEqual(
                    name_val,
                    sk.parent.name,
                    f"{sk} frontmatter name '{name_val}' != folder '{sk.parent.name}'",
                )
                self.assertGreaterEqual(len(desc_val), 15, f"{sk} description too short: {desc_val!r}")
                self.assertNotIn("<", desc_val, f"{sk} YAML description contains '<'")
                self.assertNotIn(">", desc_val, f"{sk} YAML description contains '>'")

    def test_always_on_rules_token_budget_and_no_frontmatter(self) -> None:
        gemini = ROOT / "GEMINI.md"
        agents = ROOT / "AGENTS.md"
        total_bytes = 0
        for rule_file in (gemini, agents):
            with self.subTest(file=rule_file.name):
                self.assertTrue(rule_file.exists(), f"Missing {rule_file.name}")
                raw = rule_file.read_bytes()
                self.assertLess(len(raw), 24576, f"{rule_file.name} exceeds 24 KB limit ({len(raw)} B)")
                text = raw.decode("utf-8")
                self.assertFalse(
                    text.lstrip().startswith("---"),
                    f"{rule_file.name} must NOT contain YAML frontmatter",
                )
                total_bytes += len(raw)
        self.assertLessEqual(
            total_bytes,
            9500,
            f"Combined GEMINI.md + AGENTS.md ({total_bytes} B) exceeds v3.0 9,500 B unified ceiling",
        )


class TestMistakeAnalytics(unittest.TestCase):
    """4. P0.1 Mistake Taxonomy, Analytics, Cheatsheet Export & Turn-0 Hook Injection."""

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


class TestPhysicsDraw(unittest.TestCase):
    """7. P1.1 Precision Physics Diagram Engine (all 7 parametric presets + edge cases)."""

    def test_all_seven_presets_and_physics_edge_cases(self) -> None:
        pd = load_script_module("physics_draw.py")

        def _run_pd(argv: list[str]) -> dict[str, Any]:
            buf = _EncodedStringIO()
            with contextlib.redirect_stdout(buf):
                pd.main(argv)
            return json.loads(buf.getvalue())

        with tempfile.TemporaryDirectory(prefix="mlh-p11-") as tmp:
            t = Path(tmp)

            # 1. fbd-incline
            fbd_svg = t / "fbd.svg"
            fbd = _run_pd(["fbd-incline", "--angle", "30", "--mass", "2", "--g", "9.8", "--mu", "0.2", "--out", str(fbd_svg), "--json"])
            self.assertTrue(fbd_svg.exists() and fbd["bytes"] > 1000)
            self.assertIn("<svg", fbd_svg.read_text(encoding="utf-8"))
            exp_n = 2.0 * 9.8 * math.cos(math.radians(30.0))
            exp_a = (2.0 * 9.8 * math.sin(math.radians(30.0)) - 0.2 * exp_n) / 2.0
            self.assertAlmostEqual(fbd["normalN"], exp_n, places=3)
            self.assertAlmostEqual(fbd["acceleration"], exp_a, places=3)
            self.assertEqual(fbd["regime"], "sliding_down")

            # 2. pulley (Atwood)
            pul_svg = t / "pulley.svg"
            pul = _run_pd(["pulley", "--preset", "atwood", "--m1", "3", "--m2", "5", "--g", "9.8", "--out", str(pul_svg), "--json"])
            self.assertTrue(pul_svg.exists() and pul["bytes"] > 1000)
            self.assertAlmostEqual(pul["acceleration"], 9.8 * (5.0 - 3.0) / (3.0 + 5.0), places=4)
            self.assertAlmostEqual(pul["tensionN"], 2.0 * 3.0 * 5.0 * 9.8 / 8.0, places=4)

            # 3. optics-lens: real, virtual (dark PNG), and u == -f infinity edge case
            lens_real = _run_pd(["optics-lens", "--f", "15", "--u", "-30", "--out", str(t / "lens_real.svg"), "--json"])
            self.assertAlmostEqual(lens_real["v"], 30.0, places=4)
            self.assertAlmostEqual(1.0 / lens_real["v"] - 1.0 / lens_real["u"], 1.0 / lens_real["f"], places=6)
            self.assertEqual(lens_real["nature"], "real_inverted")

            lens_virt_png = t / "lens_virt.png"
            lens_virt = _run_pd(["optics-lens", "--f", "15", "--u", "-10", "--theme", "dark", "--dpi", "120", "--out", str(lens_virt_png), "--json"])
            self.assertTrue(lens_virt_png.read_bytes().startswith(b"\x89PNG"))
            self.assertAlmostEqual(lens_virt["v"], -30.0, places=4)
            self.assertAlmostEqual(lens_virt["magnification"], 3.0, places=4)
            self.assertEqual(lens_virt["nature"], "virtual_erect")

            lens_inf = _run_pd(["optics-lens", "--f", "15", "--u", "-15", "--out", str(t / "lens_inf.svg"), "--json"])
            self.assertIsNone(lens_inf["v"])
            self.assertTrue(lens_inf["atInfinity"])
            self.assertEqual(lens_inf["nature"], "at_infinity")

            # 4. optics-mirror
            mir = _run_pd(["optics-mirror", "--type", "concave", "--f", "10", "--u", "-30", "--out", str(t / "mirror.svg"), "--json"])
            self.assertAlmostEqual(mir["f"], -10.0, places=4)
            self.assertAlmostEqual(mir["v"], -15.0, places=4)
            self.assertAlmostEqual(1.0 / mir["v"] + 1.0 / mir["u"], 1.0 / mir["f"], places=6)
            self.assertEqual(mir["nature"], "real_inverted")

            # 5. optics-prism: normal min-dev + TIR edge case without crashing
            p_norm = _run_pd(["optics-prism", "--A", "60", "--n", "1.5", "--min-dev", "--out", str(t / "prism_norm.svg"), "--json"])
            self.assertFalse(p_norm["tir"])
            self.assertAlmostEqual(p_norm["r1"], 30.0, places=4)
            self.assertAlmostEqual(p_norm["r2"], 30.0, places=4)
            self.assertAlmostEqual(p_norm["i1"], p_norm["i2"], places=4)
            self.assertAlmostEqual(p_norm["delta"], 2.0 * p_norm["i1"] - 60.0, places=3)

            p_tir = _run_pd(["optics-prism", "--A", "60", "--n", "1.5", "--i1", "15", "--out", str(t / "prism_tir.svg"), "--json"])
            self.assertTrue(p_tir["tir"])
            self.assertGreater(p_tir["r2"], p_tir["criticalAngle"])
            self.assertIsNone(p_tir["i2"])

            # 6. field-charges
            fld_svg = t / "field.svg"
            fld = _run_pd(["field-charges", "--charges", "1,-1.5,0;-1,1.5,0", "--out", str(fld_svg), "--json"])
            self.assertTrue(fld_svg.exists() and fld["bytes"] > 1000)
            self.assertAlmostEqual(fld["netCharge"], 0.0, places=6)

            # 7. projectile (level + up-incline)
            proj_lvl = _run_pd(["projectile", "--v0", "20", "--angle", "45", "--g", "9.8", "--out", str(t / "proj_lvl.svg"), "--json"])
            self.assertAlmostEqual(proj_lvl["range"], 400.0 / 9.8, places=3)

            proj_inc = _run_pd(["projectile", "--v0", "20", "--angle", "45", "--incline", "15", "--g", "9.8", "--out", str(t / "proj_inc.svg"), "--json"])
            a_rad, b_rad = math.radians(45.0), math.radians(15.0)
            exp_r_inc = (20.0 ** 2 / (9.8 * math.cos(b_rad) ** 2)) * (math.sin(2.0 * a_rad - b_rad) - math.sin(b_rad))
            self.assertAlmostEqual(proj_inc["range"], exp_r_inc, places=3)


class TestOrganicReagents(unittest.TestCase):
    """8. P1.2 Organic Chemistry Reagent DB (RDKit validation) & Discrimination Matrix CLI."""

    def test_reagents_db_rdkit_valid_and_cli_subcommands(self) -> None:
        from rdkit import Chem

        org = load_script_module("organic_reagents.py")
        db_path = ROOT / "knowledge-graph" / "reagents.json"
        self.assertTrue(db_path.is_file(), "Missing knowledge-graph/reagents.json")

        db = json.loads(db_path.read_text(encoding="utf-8"))
        reagents = db.get("reagents", [])
        self.assertGreaterEqual(len(reagents), 28, f"Expected >=28 reagents, got {len(reagents)}")

        # Validate EVERY reactantSmiles & productSmiles directly via RDKit
        for r in reagents:
            ex = r.get("example", {})
            for fld in ("reactantSmiles", "productSmiles"):
                smi = ex.get(fld, "")
                with self.subTest(reagent=r.get("id"), field=fld):
                    self.assertTrue(smi, f"Empty {fld} for {r.get('id')}")
                    self.assertIsNotNone(Chem.MolFromSmiles(smi), f"Invalid SMILES for {r.get('id')}.{fld}: {smi}")

        # (a) list
        listed = call_json_cmd(
            org.cmd_list,
            argparse.Namespace(db=str(db_path), category="reduction", json=True),
        )
        self.assertGreaterEqual(listed["count"], 8)

        # (b) lookup alias resolution (LAH & DIBAL)
        lah = call_json_cmd(
            org.cmd_lookup,
            argparse.Namespace(db=str(db_path), query="LAH", json=True),
        )
        self.assertEqual(lah["reagent"]["id"], "lialh4")

        dibal = call_json_cmd(
            org.cmd_lookup,
            argparse.Namespace(db=str(db_path), query="DIBAL", json=True),
        )
        self.assertEqual(dibal["reagent"]["id"], "dibal_h")

        # (c) compare + RDKit 2D PNG grid
        with tempfile.TemporaryDirectory(prefix="mlh-p12-") as tmp:
            grid_png = Path(tmp) / "grid.png"
            cmp_res = call_json_cmd(
                org.cmd_compare,
                argparse.Namespace(
                    db=str(db_path),
                    reagents=["NaBH4", "LiAlH4", "B2H6/THF"],
                    groups=None,
                    out_grid=str(grid_png),
                    dark=False,
                    json=True,
                ),
            )
            self.assertTrue(grid_png.is_file())
            self.assertTrue(grid_png.read_bytes().startswith(b"\x89PNG"))
            self.assertGreater(grid_png.stat().st_size, 5000)
            self.assertEqual(cmp_res["matrix"]["ester"]["nabh4"], "no_reaction")
            self.assertNotEqual(cmp_res["matrix"]["ester"]["lialh4"], "no_reaction")
            self.assertNotEqual(cmp_res["matrix"]["carboxylic_acid"]["b2h6_thf"], "no_reaction")
            self.assertIn("ester", cmp_res["differentiatingGroups"])
            self.assertIn("carboxylic_acid", cmp_res["differentiatingGroups"])

        # (d) selectivity
        sel = call_json_cmd(
            org.cmd_selectivity,
            argparse.Namespace(db=str(db_path), functional_group="carboxylic_acid", reagents=None, category="reduction", json=True),
        )
        react_ids = {r["id"] for r in sel["reacts"]}
        intact_ids = {r["id"] for r in sel["leavesIntact"]}
        self.assertIn("b2h6_thf", react_ids)
        self.assertIn("lialh4", react_ids)
        self.assertIn("nabh4", intact_ids)

        # (e) drill-pair (zero-spoiler prompt + rubric)
        drill = call_json_cmd(
            org.cmd_drill_pair,
            argparse.Namespace(db=str(db_path), r1="HBr", r2="HBr/ROOR", json=True),
        )
        self.assertIn("studentPrompt", drill)
        self.assertIn("answerKey", drill)
        self.assertTrue(drill["studentPrompt"]["questions"])


class TestMathPlot(unittest.TestCase):
    """9. P1.4 Coordinate Geometry, 3D Vectors & Calculus Plotter (SymPy + Zero-CDN HTML)."""

    def test_math_plot_all_subcommands_offline_html_and_safe_parser(self) -> None:
        mp = load_script_module("math_plot.py")

        def _run_mp(argv: list[str]) -> tuple[int, dict[str, Any]]:
            buf = _EncodedStringIO()
            with contextlib.redirect_stdout(buf):
                rc = mp.main(argv)
            return rc, json.loads(buf.getvalue())

        # (a) Safe parser injection refusal
        for bad_expr in (
            "__import__('os').system('echo pwn')",
            "open('/etc/passwd').read()",
            "(1).__class__.__mro__",
            "lambda x: x",
        ):
            with self.subTest(bad_expr=bad_expr):
                with self.assertRaises(ValueError):
                    mp.safe_parse_expr(bad_expr)

        with tempfile.TemporaryDirectory(prefix="mlh-p14-") as tmp:
            t = Path(tmp)

            # (b) conic: parabola SVG, ellipse SVG, hyperbola offline HTML (with XSS check & a<b guard)
            rc_p, para = _run_mp([
                "conic", "--type", "parabola", "--p", "2", "--tangent-t", "1",
                "--show-director", "--out", str(t / "para.svg"), "--json",
            ])
            self.assertEqual(rc_p, 0)
            self.assertEqual(para["foci"][0]["x"], 2.0)
            self.assertEqual(para["latusRectum"], 8.0)
            self.assertEqual(para["directorCircleStatus"], "directrix")

            rc_e, ell = _run_mp([
                "conic", "--type", "ellipse", "--a", "5", "--b", "3",
                "--show-director", "--out", str(t / "ell.svg"), "--json",
            ])
            self.assertEqual(rc_e, 0)
            self.assertAlmostEqual(ell["eccentricity"], 0.8, places=5)
            self.assertEqual(ell["directorCircleStatus"], "real")
            self.assertAlmostEqual(ell["directorCircleRadius"], math.sqrt(34.0), places=5)

            hyp_html = t / "hyp.html"
            rc_h, hyp = _run_mp([
                "conic", "--type", "hyperbola", "--a", "5", "--b", "3",
                "--show-director", "--title", "Hyp </script><script>alert(1)</script>",
                "--out", str(hyp_html), "--json",
            ])
            self.assertEqual(rc_h, 0)
            self.assertEqual(hyp["directorCircleStatus"], "real")
            self.assertAlmostEqual(hyp["directorCircleRadius"], 4.0, places=5)
            h_txt = hyp_html.read_text(encoding="utf-8")
            self.assertNotIn("</script><script>alert(1)", h_txt)
            self.assertIsNone(re.search(r"<(?:script|link)\b[^>]*\b(?:src|href)\s*=\s*['\"]https?://", h_txt, re.I))
            h_scripts = re.findall(r"<script[^>]*>(.*?)</script>", h_txt, flags=re.DOTALL | re.I)
            self.assertGreaterEqual(len(h_scripts), 1)
            js_h = t / "hyp.js"
            js_h.write_text("\n;\n".join(h_scripts), encoding="utf-8")
            self.assertEqual(run_cli(["node", "--check", str(js_h)]).returncode, 0)

            # Hyperbola a < b director circle guard (must not crash on negative sqrt)
            rc_h2, hyp2 = _run_mp([
                "conic", "--type", "hyperbola", "--a", "3", "--b", "5",
                "--show-director", "--out", str(t / "hyp2.svg"), "--json",
            ])
            self.assertEqual(rc_h2, 0)
            self.assertEqual(hyp2["directorCircleStatus"], "none")

            # (c) argand
            rc_a, arg = _run_mp([
                "argand", "--loci", "circle:0,2;perp-bisector:0,4", "--points", "0;2;4",
                "--out", str(t / "argand.svg"), "--json",
            ])
            self.assertEqual(rc_a, 0)
            self.assertGreaterEqual(len(arg.get("intersections", [])), 1)

            # (d) integral: sin(x) on [0, 2*pi] -> signed 0 vs geometric area 4
            rc_i, integ = _run_mp([
                "integral", "--expr", "sin(x)", "--a", "0", "--b", "2*pi",
                "--out", str(t / "integral.svg"), "--json",
            ])
            self.assertEqual(rc_i, 0)
            self.assertAlmostEqual(integ["signedIntegral"], 0.0, places=6)
            self.assertAlmostEqual(integ["geometricArea"], 4.0, places=6)

            # (e) vector3d: skew lines exact shortest distance + offline HTML
            v3d_html = t / "v3d.html"
            rc_v, v3d = _run_mp([
                "vector3d", "--lines", "0,0,0|1,1,0;0,0,2|1,-1,0",
                "--out", str(v3d_html), "--json",
            ])
            self.assertEqual(rc_v, 0)
            self.assertEqual(v3d["relation"], "skew")
            self.assertAlmostEqual(v3d["shortestDistance"], 2.0, places=6)
            self.assertAlmostEqual(v3d["angleDeg"], 90.0, places=4)
            v_txt = v3d_html.read_text(encoding="utf-8")
            self.assertIsNone(re.search(r"<(?:script|link)\b[^>]*\b(?:src|href)\s*=\s*['\"]https?://", v_txt, re.I))
            v_scripts = re.findall(r"<script[^>]*>(.*?)</script>", v_txt, flags=re.DOTALL | re.I)
            self.assertGreaterEqual(len(v_scripts), 1)
            js_v = t / "v3d.js"
            js_v.write_text("\n;\n".join(v_scripts), encoding="utf-8")
            self.assertEqual(run_cli(["node", "--check", str(js_v)]).returncode, 0)

            # (f) func: x**3 - 3*x exact roots, extrema, and inflection point
            rc_f, fn_res = _run_mp([
                "func", "--exprs", "x**3 - 3*x", "--xmin", "-3", "--xmax", "3",
                "--out", str(t / "func.svg"), "--json",
            ])
            self.assertEqual(rc_f, 0)
            root_xs = sorted(r["x"] for r in fn_res["roots"])
            self.assertEqual(len(root_xs), 3)
            self.assertAlmostEqual(root_xs[0], -math.sqrt(3.0), places=4)
            self.assertAlmostEqual(root_xs[1], 0.0, places=4)
            self.assertAlmostEqual(root_xs[2], math.sqrt(3.0), places=4)
            ext_kinds = {round(e["x"], 3): e["kind"] for e in fn_res["extrema"]}
            self.assertEqual(ext_kinds.get(-1.0), "local_max")
            self.assertEqual(ext_kinds.get(1.0), "local_min")
            self.assertEqual(len(fn_res["inflectionPoints"]), 1)
            self.assertAlmostEqual(fn_res["inflectionPoints"][0]["x"], 0.0, places=4)


class TestPdfStudioUpgrades(unittest.TestCase):
    """10. LearnHarness Parity Slice B: PDF math_density triage & white-halo annotate."""

    def test_pdf_inspect_math_density_and_annotate_svg_png_html(self) -> None:
        import pymupdf as fitz

        pdf_mod = load_script_module("pdf_studio.py")

        with tempfile.TemporaryDirectory(prefix="mlh-lh-pdf-") as tmp:
            t = Path(tmp)
            pdf_path = t / "math-diagram.pdf"
            doc = fitz.open()

            # Page 1: high math density + vector diagram
            p1 = doc.new_page(width=595, height=842)
            p1.insert_text((60, 80), "E = mc^2 + 1/2 mv^2 >= 0; F = ma; x^2 + y^2 <= R^2", fontsize=14, fontname="helv")
            p1.draw_rect(fitz.Rect(80, 140, 360, 320), color=(0.1, 0.2, 0.5), fill=(0.92, 0.95, 1.0), width=1.5)
            p1.draw_line((100, 280), (320, 170), color=(0.8, 0.1, 0.1), width=2.0)

            # Page 2: plain prose only
            p2 = doc.new_page(width=595, height=842)
            p2.insert_text(
                (60, 80),
                "Historical overview discussing classical mechanics principles in clear ordinary prose.",
                fontsize=11,
                fontname="helv",
            )
            doc.save(str(pdf_path))
            doc.close()

            ins = call_json_cmd(
                pdf_mod.cmd_inspect,
                argparse.Namespace(pdf=str(pdf_path), limit=20),
            )
            self.assertEqual(ins["size_class"], "short")
            self.assertTrue(ins.get("recommended_strategy"))
            p1_info, p2_info = ins["pages"][0], ins["pages"][1]
            self.assertGreaterEqual(p1_info["math_density"], 0.03)
            self.assertTrue(p1_info["needs_vision"])
            self.assertEqual(p2_info["math_density"], 0.0)
            self.assertFalse(p2_info["needs_vision"])

            # Annotate cropped figure -> SVG (white-halo) + PNG (>500B) + zero-CDN HTML embed
            out_svg = t / "ann.svg"
            out_png = t / "ann.png"
            out_html = t / "ann.html"
            marks = [
                {"type": "arrow", "from": [0.15, 0.2], "to": [0.5, 0.5], "label": "Force vector", "color": "#e11d48"},
                {"type": "box", "rect": [0.2, 0.25, 0.75, 0.75], "label": "Region A", "color": "#2563eb"},
                {"type": "circle", "center": [0.55, 0.45], "r": 0.08, "label": "Pivot", "color": "#d97706"},
                {"type": "label", "at": [0.5, 0.88], "text": "Key Check", "color": "#059669"},
            ]
            ann = call_json_cmd(
                pdf_mod.cmd_annotate,
                argparse.Namespace(
                    image=None,
                    pdf=str(pdf_path),
                    page=1,
                    crop="0.05,0.05,0.8,0.5",
                    dpi=140,
                    marks=json.dumps(marks),
                    coords="norm",
                    out=str(out_svg),
                    out_png=str(out_png),
                    out_html=str(out_html),
                    title="Annotated Mechanics Figure",
                    json=True,
                ),
            )
            self.assertEqual(ann["marksCount"], 4)
            svg_txt = out_svg.read_text(encoding="utf-8")
            self.assertIn("<svg", svg_txt)
            self.assertIn('paint-order="stroke"', svg_txt)
            self.assertIn("data:image/png;base64,", svg_txt)

            self.assertTrue(out_png.is_file())
            png_bytes = out_png.read_bytes()
            self.assertGreater(len(png_bytes), 500)
            self.assertTrue(png_bytes.startswith(b"\x89PNG"))

            html_txt = out_html.read_text(encoding="utf-8")
            self.assertIsNone(re.search(r"<(?:script|link)\b[^>]*\b(?:src|href)\s*=\s*['\"]https?://", html_txt, re.I))
            self.assertIn("transparent", html_txt.lower())


class TestKgHyperedgesAndMultiCue(unittest.TestCase):
    """11. LearnHarness Parity Slice C: Lexical dedup, Multi-Cue FSRS-6 & Joint Hyperedges."""

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


class TestMcpAppStudio(unittest.TestCase):
    """12. LearnHarness Parity Tier 2: Official SEP-1865 mlh-studio MCP App & Installer."""

    def test_bundles_zero_cdn_stdio_protocol_and_installer_lifecycle(self) -> None:
        dist_dir = ROOT / "mcp-app" / "mlh-studio" / "dist"
        server_mjs = dist_dir / "server.mjs"
        self.assertTrue(server_mjs.is_file() and server_mjs.stat().st_size > 1000)

        for html_name in ("quiz-card.html", "practice-card.html", "studio-pane.html"):
            hp = dist_dir / html_name
            with self.subTest(bundle=html_name):
                self.assertTrue(hp.is_file() and hp.stat().st_size > 1000, f"Missing bundle: {hp}")
                txt = hp.read_text(encoding="utf-8")
                self.assertIsNone(
                    re.search(r"<(?:script|link)\b[^>]*\b(?:src|href)\s*=\s*['\"]https?://", txt, re.I),
                    f"External CDN tag forbidden in MCP App bundle {html_name}",
                )

        # Live stdio JSON-RPC test against bundled server.mjs in isolated /tmp workspace
        with tempfile.TemporaryDirectory(prefix="mlh-mcp-test-") as tmp:
            t = Path(tmp)
            ws_dir = t / "ws"
            (ws_dir / "knowledge-graph").mkdir(parents=True, exist_ok=True)
            (ws_dir / "knowledge-graph" / "propositions.jsonl").write_text("", encoding="utf-8")
            (ws_dir / "chapters").mkdir(parents=True, exist_ok=True)
            (ws_dir / "chapters" / "shm-tracker.json").write_text(
                json.dumps({
                    "schemaVersion": 1,
                    "slug": "shm",
                    "title": "Simple Harmonic Motion",
                    "subject": "Physics",
                    "active": True,
                    "topics": [{"id": "t1", "order": 1, "title": "Restoring Law", "level": "basics", "status": "mastered"}],
                    "questions": [{"qid": "q1", "label": "Ex-1 Q1", "topicId": "t1", "level": "basics", "status": "unseen"}],
                    "milestones": {"sourcesIndexed": True, "probed": True},
                }),
                encoding="utf-8",
            )

            secret_sol = "SECRET_MODEL_SOLUTION_NEVER_LEAK_4291"
            reqs = [
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {"name": "mlh-unittest", "version": "1.0"},
                    },
                },
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
                {"jsonrpc": "2.0", "id": 3, "method": "resources/list", "params": {}},
                {"jsonrpc": "2.0", "id": 4, "method": "resources/read", "params": {"uri": "ui://mlh/quiz-card.html"}},
                {
                    "jsonrpc": "2.0",
                    "id": 5,
                    "method": "tools/call",
                    "params": {
                        "name": "mlh_quiz_card",
                        "arguments": {"role": "check", "question": "Bad quiz missing options", "explanation": "Because"},
                    },
                },
                {
                    "jsonrpc": "2.0",
                    "id": 6,
                    "method": "tools/call",
                    "params": {
                        "name": "mlh_quiz_card",
                        "arguments": {
                            "role": "check",
                            "nodeId": "phy-work",
                            "question": "Does normal force do work on a fixed incline?",
                            "options": [{"id": "a", "label": "Zero work"}, {"id": "b", "label": "Positive work"}],
                            "explanation": "Normal force is perpendicular to instantaneous displacement on a fixed track.",
                            "correctAnswer": "a",
                        },
                    },
                },
                {
                    "jsonrpc": "2.0",
                    "id": 7,
                    "method": "tools/call",
                    "params": {
                        "name": "mlh_practice_card",
                        "arguments": {
                            "action": "pose",
                            "problemId": "prob-1",
                            "statement": "Find acceleration of a solid sphere rolling down angle $\\theta$.",
                            "mode": "recall",
                            "targets": ["phy-rot-rolling"],
                            "modelSolution": secret_sol,
                        },
                    },
                },
                {
                    "jsonrpc": "2.0",
                    "id": 8,
                    "method": "tools/call",
                    "params": {
                        "name": "mlh_practice_card",
                        "arguments": {
                            "action": "pose",
                            "problemId": "prob-2",
                            "statement": "Second problem while first active must fail",
                            "modelSolution": "Other",
                        },
                    },
                },
                {
                    "jsonrpc": "2.0",
                    "id": 9,
                    "method": "tools/call",
                    "params": {
                        "name": "mlh_practice_card",
                        "arguments": {
                            "action": "conclude",
                            "problemId": "prob-1",
                            "outcome": "solved",
                            "verdict": "Exact rolling constraint applied.",
                            "rung": None,
                        },
                    },
                },
                {
                    "jsonrpc": "2.0",
                    "id": 10,
                    "method": "tools/call",
                    "params": {"name": "mlh_lesson_studio", "arguments": {}},
                },
                {
                    "jsonrpc": "2.0",
                    "id": 11,
                    "method": "tools/call",
                    "params": {"name": "mlh_app_load_state", "arguments": {"section": "all"}},
                },
            ]

            stdin_payload = "\n".join(json.dumps(r) for r in reqs) + "\n"
            proc = run_cli(
                ["node", str(server_mjs), f"--workspace={ws_dir}"],
                stdin_text=stdin_payload,
                timeout=10.0,
            )
            self.assertEqual(proc.returncode, 0, f"MCP server exited {proc.returncode}: {proc.stderr}")

            by_id: dict[int, dict[str, Any]] = {}
            for line in proc.stdout.splitlines():
                line = line.strip()
                if not line:
                    continue
                msg = json.loads(line)
                if "id" in msg:
                    by_id[int(msg["id"])] = msg

            # 1. initialize
            self.assertIn("serverInfo", by_id[1]["result"])
            self.assertEqual(by_id[1]["result"]["serverInfo"]["version"], "3.2.0")

            # 2. tools/list: exact 23 tools (13 model-visible: 6 UI synt_*/mlh_* + 7 ambient headless; 10 visibility:["app"])
            tools = by_id[2]["result"]["tools"]
            tools_by_name = {tl["name"]: tl for tl in tools}
            self.assertEqual(len(tools), 23)
            model_tools = [tl for tl in tools if "app" not in tl.get("_meta", {}).get("ui", {}).get("visibility", [])]
            app_tools = [tl for tl in tools if tl.get("_meta", {}).get("ui", {}).get("visibility") == ["app"]]
            self.assertEqual(
                {tl["name"] for tl in model_tools},
                {
                    "synt_quiz_card",
                    "synt_practice_card",
                    "synt_lesson_studio",
                    "synt_next_move",
                    "synt_whiteboard_step",
                    "synt_capture_concept",
                    "synt_get_frontier",
                    "synt_log_review",
                    "synt_readiness",
                    "synt_interleave",
                    "mlh_quiz_card",
                    "mlh_practice_card",
                    "mlh_lesson_studio",
                },
            )
            self.assertEqual(
                {tl["name"] for tl in app_tools},
                {
                    "synt_app_load_state",
                    "synt_app_rate_flashcard",
                    "synt_app_save_sketch",
                    "synt_app_card_state",
                    "synt_app_update_profile",
                    "mlh_app_load_state",
                    "mlh_app_rate_flashcard",
                    "mlh_app_save_sketch",
                    "mlh_app_card_state",
                    "mlh_app_update_profile",
                },
            )

            # Check ui:// URIs, explanation-before-correctAnswer ordering, and aux_pane + global entrypoints
            self.assertEqual(tools_by_name["synt_quiz_card"]["_meta"]["ui"]["resourceUri"], "ui://syntopik/quiz-card.html")
            self.assertEqual(tools_by_name["mlh_quiz_card"]["_meta"]["ui"]["resourceUri"], "ui://mlh/quiz-card.html")
            for q_tool_name in ("synt_quiz_card", "mlh_quiz_card"):
                q_props = list(tools_by_name[q_tool_name]["inputSchema"]["properties"].keys())
                self.assertLess(q_props.index("explanation"), q_props.index("correctAnswer"))
            # Antigravity UI entrypoints MUST appear ONLY on synt_lesson_studio (never duplicated on mlh_lesson_studio)
            ep_tools = [
                tl["name"]
                for tl in tools
                if tl.get("_meta", {}).get("com.google.antigravity/ui", {}).get("entrypoints")
            ]
            self.assertEqual(ep_tools, ["synt_lesson_studio"], f"Duplicate or missing UI entrypoints: {ep_tools}")
            eps = tools_by_name["synt_lesson_studio"]["_meta"]["com.google.antigravity/ui"]["entrypoints"]
            self.assertEqual({e["type"] for e in eps}, {"aux_pane", "global"})

            # 3 & 4. resources/list & resources/read MIME type (6 dual-namespace UI resources)
            res_uris = {r["uri"] for r in by_id[3]["result"]["resources"]}
            self.assertEqual(
                res_uris,
                {
                    "ui://syntopik/quiz-card.html",
                    "ui://syntopik/practice-card.html",
                    "ui://syntopik/studio-pane.html",
                    "ui://mlh/quiz-card.html",
                    "ui://mlh/practice-card.html",
                    "ui://mlh/studio-pane.html",
                },
            )
            read_content = by_id[4]["result"]["contents"][0]
            self.assertEqual(read_content["mimeType"], "text/html;profile=mcp-app")
            self.assertIn("<!DOCTYPE html>", read_content["text"])

            # 5 & 6. mlh_quiz_card validation rejection + valid payload
            self.assertTrue(by_id[5]["result"].get("isError"))
            self.assertFalse(by_id[6]["result"].get("isError", False))
            self.assertEqual(by_id[6]["result"]["structuredContent"]["role"], "check")

            # 7, 8, 9. mlh_practice_card hidden modelSolution gate + 1-active gate + verbatim conclude
            pose_resp = by_id[7]["result"]
            self.assertFalse(pose_resp.get("isError", False))
            self.assertNotIn(secret_sol, json.dumps(pose_resp), "modelSolution leaked during pose!")
            self.assertTrue(by_id[8]["result"].get("isError"), "1-active-problem gate did not reject second pose")
            conc_resp = by_id[9]["result"]
            self.assertFalse(conc_resp.get("isError", False))
            self.assertEqual(conc_resp["structuredContent"]["modelSolution"], secret_sol)

            # 10 & 11. mlh_lesson_studio empty {} launch + mlh_app_load_state chapterTrackers/activeChapter
            studio_resp = by_id[10]["result"]
            self.assertFalse(studio_resp.get("isError", False))
            self.assertEqual(studio_resp["structuredContent"]["kind"], "mlh_lesson_studio")
            self.assertEqual(studio_resp["structuredContent"]["activeChapter"]["slug"], "shm")

            state_resp = by_id[11]["result"]
            self.assertFalse(state_resp.get("isError", False))
            state_sc = state_resp["structuredContent"]
            self.assertEqual(len(state_sc["chapterTrackers"]), 1)
            self.assertEqual(state_sc["activeChapter"]["slug"], "shm")
            self.assertEqual(state_sc["activeChapter"]["topicsMastered"], 1)

            # (a2) Cross-process disk persistence test (survives chat switches & server restarts!)
            data_dir = t / "plugin-data"
            persist_secret = "PERSISTED_ACROSS_RESTART_SOL_9812"
            p1_reqs = [
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {"name": "mlh-persist-1", "version": "1.0"},
                    },
                },
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {
                        "name": "mlh_app_card_state",
                        "arguments": {
                            "op": "save",
                            "kind": "quiz",
                            "id": "quiz-shm-probe-1",
                            "state": {
                                "revealed": True,
                                "selectedIds": ["c"],
                                "dontKnow": False,
                                "confidence": 85,
                                "thinking": "Pseudo-force shifts equilibrium only.",
                                "submitted": True,
                                "verdict": "correct",
                                "statusMsg": "Response sent to tutor.",
                                "statusKind": "success",
                            },
                        },
                    },
                },
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {
                        "name": "mlh_app_card_state",
                        "arguments": {
                            "op": "save",
                            "kind": "quiz",
                            "id": "quiz-shm-probe-1",
                            "state": {"submitted": False, "confidence": 50, "selectedIds": []},
                        },
                    },
                },
                {
                    "jsonrpc": "2.0",
                    "id": 4,
                    "method": "tools/call",
                    "params": {
                        "name": "mlh_practice_card",
                        "arguments": {
                            "action": "pose",
                            "problemId": "prob-persist-1",
                            "statement": "Find time period of SHM with $k$.",
                            "modelSolution": persist_secret,
                        },
                    },
                },
                {
                    "jsonrpc": "2.0",
                    "id": 5,
                    "method": "tools/call",
                    "params": {
                        "name": "mlh_lesson_studio",
                        "arguments": {
                            "planMermaid": "graph TD\n  shm_law[Restoring Law] --> shm_phase[Phase Circle]",
                            "activeNodeId": "shm_phase",
                        },
                    },
                },
            ]
            p1 = run_cli(
                ["node", str(server_mjs), f"--workspace={ws_dir}", f"--data-dir={data_dir}"],
                stdin_text="\n".join(json.dumps(r) for r in p1_reqs) + "\n",
                timeout=10.0,
            )
            self.assertEqual(p1.returncode, 0, p1.stderr)
            self.assertTrue((data_dir / "ui-state.json").is_file())

            # Launch a brand-new server process against the same --data-dir
            p2_reqs = [
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {"name": "mlh-persist-2", "version": "1.0"},
                    },
                },
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {
                        "name": "mlh_app_card_state",
                        "arguments": {"op": "get", "kind": "quiz", "id": "quiz-shm-probe-1"},
                    },
                },
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {
                        "name": "mlh_practice_card",
                        "arguments": {
                            "action": "conclude",
                            "problemId": "prob-persist-1",
                            "outcome": "solved",
                            "verdict": "Exact period derived.",
                        },
                    },
                },
                {
                    "jsonrpc": "2.0",
                    "id": 4,
                    "method": "tools/call",
                    "params": {"name": "mlh_lesson_studio", "arguments": {}},
                },
            ]
            p2 = run_cli(
                ["node", str(server_mjs), f"--workspace={ws_dir}", f"--data-dir={data_dir}"],
                stdin_text="\n".join(json.dumps(r) for r in p2_reqs) + "\n",
                timeout=10.0,
            )
            self.assertEqual(p2.returncode, 0, p2.stderr)
            p2_by_id = {
                int(m["id"]): m
                for m in (json.loads(ln) for ln in p2.stdout.splitlines() if ln.strip())
                if "id" in m
            }
            restored_quiz = p2_by_id[2]["result"]["structuredContent"]
            self.assertTrue(restored_quiz["found"])
            # Monotonic protection kept submitted=True & confidence=85 despite stale draft attempt
            self.assertTrue(restored_quiz["state"]["submitted"])
            self.assertEqual(restored_quiz["state"]["selectedIds"], ["c"])
            self.assertEqual(restored_quiz["state"]["confidence"], 85)
            self.assertEqual(restored_quiz["state"]["verdict"], "correct")
            # Practice conclude across restart revealed the exact sealed modelSolution
            self.assertEqual(p2_by_id[3]["result"]["structuredContent"]["modelSolution"], persist_secret)
            # Study Studio kept persisted Plan DAG across restart
            self.assertEqual(p2_by_id[4]["result"]["structuredContent"]["plan"]["activeNodeId"], "shm_phase")
            self.assertIn("shm_law", p2_by_id[4]["result"]["structuredContent"]["plan"]["planMermaid"])

            # (b) install_mcp_app.py install -> status --json -> uninstall strictly in /tmp
            inst_mod = load_script_module("install_mcp_app.py")
            plugin_target = t / "plugin-target"

            buf_i = _EncodedStringIO()
            with contextlib.redirect_stdout(buf_i):
                rc_i = inst_mod.cmd_install(argparse.Namespace(
                    target=str(plugin_target), workspace=str(ws_dir), node=None, json=True,
                ))
            self.assertEqual(rc_i, 0)
            i_meta = json.loads(buf_i.getvalue())
            self.assertEqual(i_meta["status"], "installed")
            self.assertFalse(i_meta["hasNodeModules"])

            buf_s = _EncodedStringIO()
            with contextlib.redirect_stdout(buf_s):
                rc_s = inst_mod.cmd_status(argparse.Namespace(target=str(plugin_target), json=True))
            self.assertEqual(rc_s, 0)
            s_meta = json.loads(buf_s.getvalue())
            self.assertTrue(s_meta["installed"])
            self.assertTrue(s_meta["healthy"])
            self.assertEqual(s_meta["missingFiles"], [])

            buf_u = _EncodedStringIO()
            with contextlib.redirect_stdout(buf_u):
                rc_u = inst_mod.cmd_uninstall(argparse.Namespace(target=str(plugin_target), json=True))
            self.assertEqual(rc_u, 0)
            self.assertFalse(plugin_target.exists())


class TestChapterAutopilot(unittest.TestCase):
    """13. Autonomous Chapter Autopilot (chapter_tracker.py + session-start.mjs)."""

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


class TestP2CrossChapterIntel(unittest.TestCase):
    """14. P2.1–P2.4: graph_intel, Visual Anki media, audio_brief & daily_plan."""

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


class TestP3BackupAndHubSync(unittest.TestCase):
    """15. P3.1–P3.2: backup_harness (SHA-256 + zip-slip) & sync-graph chapter tracker/reader sync."""

    def test_backup_snapshot_verify_restore_zipslip_and_sync_trackers(self) -> None:
        bh = load_script_module("backup_harness.py")

        with tempfile.TemporaryDirectory(prefix="mlh-p3-") as tmp:
            t = Path(tmp)
            ws = t / "workspace"
            (ws / "knowledge-graph").mkdir(parents=True)
            (ws / "chapters").mkdir(parents=True)
            (ws / "animations" / "media").mkdir(parents=True)
            (ws / ".venv" / "bin").mkdir(parents=True)
            (ws / "chapters" / "__pycache__").mkdir(parents=True)
            (ws / "mcp-app" / "mlh-studio" / "node_modules" / "pkg").mkdir(parents=True)

            (ws / "GEMINI.md").write_text("# Test Rules\n", encoding="utf-8")
            (ws / "knowledge-graph" / "propositions.jsonl").write_text('{"id":"p1","title":"Test"}\n', encoding="utf-8")
            (ws / "chapters" / "shm.md").write_text("# SHM Notes\n\n$T = 2\\pi\\sqrt{m/k}$\n", encoding="utf-8")
            (ws / "chapters" / "shm-tracker.json").write_text(
                json.dumps({
                    "schemaVersion": 1, "slug": "shm", "title": "Simple Harmonic Motion",
                    "subject": "Physics", "active": True,
                    "topics": [{"id": "t1", "order": 1, "title": "Restoring Law", "level": "basics", "status": "mastered"}],
                    "questions": [{"qid": "q1", "label": "Ex-1 Q1", "topicId": "t1", "status": "solved"}],
                    "milestones": {"sourcesIndexed": True, "probed": True},
                }),
                encoding="utf-8",
            )
            (ws / "animations" / "scene.py").write_text("print('ok')\n", encoding="utf-8")
            # Excluded junk that MUST NOT appear in backup archive
            (ws / ".venv" / "bin" / "python").write_text("bin", encoding="utf-8")
            (ws / "chapters" / "__pycache__" / "junk.pyc").write_bytes(b"\x00\x01")
            (ws / "mcp-app" / "mlh-studio" / "node_modules" / "pkg" / "index.js").write_text("x", encoding="utf-8")
            (ws / "animations" / "media" / "big.mp4").write_bytes(b"video")

            # (a) snapshot -> excludes noise, includes study data + manifest
            arch = t / "backup.tar.gz"
            snap = bh.create_snapshot(ws, arch)
            self.assertTrue(snap["ok"])
            self.assertTrue(arch.is_file())
            with tarfile.open(arch, "r:gz") as tf:
                member_names = tf.getnames()
            self.assertIn("backup-manifest.json", member_names)
            self.assertIn("chapters/shm.md", member_names)
            self.assertIn("animations/scene.py", member_names)
            for forbidden in (".venv", "__pycache__", "node_modules", "animations/media"):
                self.assertFalse(any(forbidden in n for n in member_names), f"Excluded path leaked into backup: {forbidden}")

            # (b) verify -> ok, zero mismatches, zero unsafe paths
            ver = bh.verify_archive(arch)
            self.assertTrue(ver["ok"], ver.get("errors"))
            self.assertEqual(ver["mismatches"], [])
            self.assertEqual(ver["unsafePaths"], [])

            # (c) restore -> intact SHA-256 verified files
            restored = t / "restored"
            res = bh.restore_archive(arch, restored, force=False)
            self.assertTrue(res["ok"], res.get("errors"))
            self.assertEqual(
                (restored / "chapters" / "shm.md").read_text(encoding="utf-8"),
                (ws / "chapters" / "shm.md").read_text(encoding="utf-8"),
            )

            # (d) Zip-slip rejection: malicious ../evil.txt archive MUST fail verify & restore
            evil_arch = t / "evil.tar.gz"
            evil_target = t / "evil-restore"
            evil_escape = t / "evil.txt"
            with tarfile.open(evil_arch, "w:gz") as tf:
                data = b"pwned"
                info = tarfile.TarInfo(name="../evil.txt")
                info.size = len(data)
                tf.addfile(info, io.BytesIO(data))
            ver_evil = bh.verify_archive(evil_arch)
            self.assertFalse(ver_evil["ok"])
            self.assertTrue(ver_evil["unsafePaths"])
            res_evil = bh.restore_archive(evil_arch, evil_target, force=True)
            self.assertFalse(res_evil["ok"])
            self.assertFalse(evil_escape.exists(), "Zip-slip escaped target directory!")

            # (e) sync-graph.mjs scans chapters/*-tracker.json + embeds chapter markdown for offline reader
            out_js = t / "graph-data.js"
            sp = run_cli([
                "node", str(SCRIPTS_DIR / "sync-graph.mjs"),
                "--workspace", str(ws), "--kg-dir", str(ws / "knowledge-graph"),
                "--chapters-dir", str(ws / "chapters"), "--out", str(out_js), "--quiet",
            ])
            self.assertEqual(sp.returncode, 0, sp.stderr)
            ev = run_cli([
                "node", "-e",
                f"const fs=require('fs');const vm=require('vm');const sb={{window:{{}}}};vm.runInNewContext(fs.readFileSync({json.dumps(str(out_js))},'utf8'),sb);console.log(JSON.stringify(sb.window.__STUDY_HUB__));",
            ])
            self.assertEqual(ev.returncode, 0, ev.stderr)
            hub = json.loads(ev.stdout)
            self.assertEqual(hub["stats"]["trackersCount"], 1)
            self.assertEqual(hub["activeChapterTracker"]["slug"], "shm")
            md_ch = next(c for c in hub["chapters"] if c["relPath"].endswith("shm.md"))
            self.assertIn("SHM Notes", md_ch["content"])


class TestSelfImproveAndDoctor(unittest.TestCase):
    """6. Required Skills Presence & study_doctor.py 0-warn / 0-fail Gate."""

    def test_p0_and_p1_skills_exist_with_valid_frontmatter(self) -> None:
        for folder in (
            "harness-self-improve",
            "exam-simulator",
            "grade-handwriting",
            "chapter-autopilot",
            "topic-onboarding",
        ):
            with self.subTest(skill=folder):
                sk = SKILLS_DIR / folder / "SKILL.md"
                self.assertTrue(sk.exists(), f"Missing required skill: {sk}")
                text = sk.read_text(encoding="utf-8")
                fm = parse_yaml_frontmatter(text)
                self.assertIsNotNone(fm, f"{sk} missing YAML frontmatter")
                assert fm is not None
                self.assertEqual(fm.get("name"), folder)
                self.assertGreaterEqual(len(fm.get("description", "")), 20)
                if folder == "grade-handwriting":
                    self.assertIn("R0", text)
                    self.assertIn("R2", text)
                    self.assertIn("mistake_analytics", text)
                    self.assertIn("math_verify", text)
                if folder == "chapter-autopilot":
                    self.assertIn("chapter_tracker", text)
                if folder == "topic-onboarding":
                    self.assertIn("research_scout", text)
                    self.assertIn("manifest", text)

    def test_study_doctor_zero_warn_zero_fail(self) -> None:
        proc = run_cli([sys.executable, "-B", str(SCRIPTS_DIR / "study_doctor.py"), "--json"])
        self.assertEqual(proc.returncode, 0, f"study_doctor.py exited {proc.returncode}: {proc.stderr}\n{proc.stdout}")
        report = json.loads(proc.stdout)
        self.assertTrue(report["healthy"], f"Doctor unhealthy: {report['summary']}")
        self.assertGreaterEqual(report["summary"]["total"], 49)
        self.assertGreaterEqual(report["summary"]["ok"], 49)
        self.assertEqual(report["summary"]["warn"], 0, f"Doctor warnings: {[c for c in report['checks'] if c['status'] == 'warn']}")
        self.assertEqual(report["summary"]["fail"], 0, f"Doctor failures: {[c for c in report['checks'] if c['status'] == 'fail']}")
        checks_by_name = {c["name"]: c for c in report["checks"]}
        self.assertGreaterEqual(checks_by_name["Workspace Scripts (scripts/)"]["count"], 32)
        self.assertGreaterEqual(checks_by_name["Project Skills (.agents/skills)"]["count"], 17)

        # Bare system python3 --core-only mode (zero third-party .venv packages required)
        # On Windows avoid Microsoft Store python3.exe stub; use sys.executable directly.
        bare_py = sys.executable if os.name == "nt" else (shutil.which("python3") or sys.executable)
        core_proc = run_cli([bare_py, "-B", str(SCRIPTS_DIR / "study_doctor.py"), "--core-only", "--json"])
        self.assertEqual(core_proc.returncode, 0, f"study_doctor --core-only failed: {core_proc.stderr}\n{core_proc.stdout}")
        core_report = json.loads(core_proc.stdout)
        self.assertTrue(core_report.get("coreOnly"))
        self.assertTrue(core_report["healthy"], f"Core-only doctor unhealthy: {core_report['summary']}")
        self.assertGreaterEqual(core_report["summary"]["total"], 16)
        self.assertGreaterEqual(core_report["summary"]["ok"], 16)
        self.assertEqual(core_report["summary"]["warn"], 0, f"Core-only warnings: {[c for c in core_report['checks'] if c['status'] == 'warn']}")
        self.assertEqual(core_report["summary"]["fail"], 0, f"Core-only failures: {[c for c in core_report['checks'] if c['status'] == 'fail']}")


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


class TestSetupHarnessFreshClone(unittest.TestCase):
    """17. Fresh-clone auto-bootstrap & non-destructive init-data (scripts/setup_harness.py)."""

    def test_fresh_clone_auto_bootstrap_and_non_destructive_init_data(self) -> None:
        sh = load_script_module("setup_harness.py")

        def _sh(argv: list[str]) -> tuple[int, dict[str, Any]]:
            buf = _EncodedStringIO()
            with contextlib.redirect_stdout(buf):
                rc = sh.main(argv)
            return rc, json.loads(buf.getvalue())

        with tempfile.TemporaryDirectory(prefix="synt-fresh-clone-") as tmp:
            ws = Path(tmp) / "syntopik-clone"
            ws.mkdir(parents=True)

            # Copy tracked repo skeleton ONLY (zero gitignored learner ledgers)
            (ws / "scripts").mkdir()
            shutil.copy2(SCRIPTS_DIR / "sync-graph.mjs", ws / "scripts" / "sync-graph.mjs")
            (ws / "knowledge-graph").mkdir()
            for static_kg in ("viewer.html", "reagents.json"):
                src = ROOT / "knowledge-graph" / static_kg
                if src.is_file():
                    shutil.copy2(src, ws / "knowledge-graph" / static_kg)
            for top_doc in ("AGENTS.md", "GEMINI.md", "CLAUDE.md", "dashboard.html"):
                src = ROOT / top_doc
                if src.is_file():
                    shutil.copy2(src, ws / top_doc)
            # Minimal .agents/skills + broken plain-file .claude/skills stub (Windows checkout simulation)
            sk_dir = ws / ".agents" / "skills" / "topic-onboarding"
            sk_dir.mkdir(parents=True)
            shutil.copy2(SKILLS_DIR / "topic-onboarding" / "SKILL.md", sk_dir / "SKILL.md")
            (ws / ".claude").mkdir()
            (ws / ".claude" / "skills").write_text("../.agents/skills", encoding="utf-8")

            # Confirm gitignored learner files do NOT exist before bootstrap
            kg = ws / "knowledge-graph"
            expected_ledgers = [
                "propositions.jsonl",
                "chains.jsonl",
                "mistakes.jsonl",
                "exam-attempts.jsonl",
                "harness-log.jsonl",
                "review-queue.md",
                "graph-data.js",
            ]
            for fname in expected_ledgers:
                self.assertFalse((kg / fname).exists(), f"Pre-condition failed: {fname} already exists")

            # 1. Run `setup_harness.py auto --workspace <ws> --json`
            rc_auto, auto_res = _sh(["auto", "--workspace", str(ws), "--json"])
            self.assertEqual(rc_auto, 0)
            self.assertTrue(auto_res["ok"])
            self.assertTrue(auto_res["bootstrap"]["syncedGraphDataJs"])

            for dname in ("chapters", "demos", "exams", "animations", "sources", "knowledge-graph"):
                self.assertTrue((ws / dname).is_dir(), f"Missing bootstrapped dir: {dname}")
            for fname in expected_ledgers:
                self.assertTrue((kg / fname).is_file(), f"Missing bootstrapped learner file: {fname}")

            gdata = (kg / "graph-data.js").read_text(encoding="utf-8")
            self.assertIn("window.__STUDY_GRAPH__", gdata)
            self.assertIn("window.__STUDY_HUB__", gdata)
            self.assertIn("# Spaced Review", (kg / "review-queue.md").read_text(encoding="utf-8"))
            # Broken Windows plain-file .claude/skills stub must be repaired into a working directory/symlink
            self.assertTrue((ws / ".claude" / "skills").is_dir())
            self.assertTrue((ws / ".claude" / "skills" / "topic-onboarding" / "SKILL.md").is_file())

            # 2. Populate sentinel learner data and re-run `init-data` -> NEVER overwrite existing files!
            sentinel_prop = '{"id":"sentinel-cs-1","subject":"CS","chapter":"Graphs","status":"solid"}\n'
            sentinel_mistake = '{"id":"m-deadbeef","category":"logic_bug"}\n'
            sentinel_rq = "# Custom Learner Review Queue Sentinel\n"
            (kg / "propositions.jsonl").write_text(sentinel_prop, encoding="utf-8")
            (kg / "mistakes.jsonl").write_text(sentinel_mistake, encoding="utf-8")
            (kg / "review-queue.md").write_text(sentinel_rq, encoding="utf-8")

            rc_init, init_res = _sh(["init-data", "--workspace", str(ws), "--json"])
            self.assertEqual(rc_init, 0)
            self.assertTrue(init_res["ok"])
            self.assertEqual(init_res["bootstrap"]["createdFiles"], [])
            self.assertGreaterEqual(init_res["bootstrap"]["existingPreservedCount"], 7)

            self.assertEqual((kg / "propositions.jsonl").read_text(encoding="utf-8"), sentinel_prop)
            self.assertEqual((kg / "mistakes.jsonl").read_text(encoding="utf-8"), sentinel_mistake)
            self.assertEqual((kg / "review-queue.md").read_text(encoding="utf-8"), sentinel_rq)


class TestCodeVerifyEngine(unittest.TestCase):
    """18. Deterministic CS / Algorithms code verification engine (scripts/code_verify.py)."""

    def test_run_test_cases_complexity_and_ast_check(self) -> None:
        cv = load_script_module("code_verify.py")

        def _cv(argv: list[str]) -> tuple[int, dict[str, Any]]:
            buf = _EncodedStringIO()
            with contextlib.redirect_stdout(buf):
                rc = cv.main(argv)
            return rc, json.loads(buf.getvalue())

        # 1. `run`: Python + Node + timeout enforcement + Linux RLIMIT_AS Node/V8 skip
        with unittest.mock.patch.object(cv, "_make_preexec_fn", wraps=cv._make_preexec_fn) as spy_preexec:
            rc_py, res_py = _cv(["run", "--lang", "python", "--code", "print(6 * 7)", "--json"])
            self.assertEqual(rc_py, 0)
            self.assertTrue(res_py["ok"])
            self.assertFalse(res_py["timedOut"])
            self.assertEqual(res_py["stdout"].strip(), "42")
            if os.name != "nt":
                self.assertTrue(spy_preexec.call_args.kwargs.get("apply_rlimit_as"))

            rc_js, res_js = _cv(["run", "--lang", "node", "--code", "console.log(2 ** 10)", "--json"])
            self.assertEqual(rc_js, 0, f"node sandbox run failed: {res_js}")
            self.assertTrue(res_js["ok"])
            self.assertEqual(res_js["stdout"].strip(), "1024")
            if os.name != "nt":
                self.assertFalse(spy_preexec.call_args.kwargs.get("apply_rlimit_as"))

        self.assertTrue(cv._is_node_cmd(["/usr/bin/node", "solution.js"]))
        self.assertFalse(cv._is_node_cmd([sys.executable, "solution.py"]))
        if cv.resource is not None and hasattr(cv.resource, "RLIMIT_AS"):
            with (
                unittest.mock.patch.object(cv.sys, "platform", "linux"),
                unittest.mock.patch.object(cv.resource, "setrlimit") as mock_rlimit,
            ):
                fn_py = cv._make_preexec_fn(256, apply_rlimit_as=True)
                self.assertIsNotNone(fn_py)
                fn_py()
                self.assertIn(cv.resource.RLIMIT_AS, [c.args[0] for c in mock_rlimit.call_args_list])

                mock_rlimit.reset_mock()
                fn_node = cv._make_preexec_fn(256, apply_rlimit_as=False)
                self.assertIsNotNone(fn_node)
                fn_node()
                called_node = [c.args[0] for c in mock_rlimit.call_args_list]
                self.assertNotIn(cv.resource.RLIMIT_AS, called_node)
                if hasattr(cv.resource, "RLIMIT_CORE"):
                    self.assertIn(cv.resource.RLIMIT_CORE, called_node)

        rc_to, res_to = _cv(["run", "--lang", "python", "--timeout", "0.4", "--code", "while True:\n    pass", "--json"])
        self.assertNotEqual(rc_to, 0)
        self.assertFalse(res_to["ok"])
        self.assertTrue(res_to["timedOut"])

        # 2. `test-cases`: --entry function mode (pass + fail), stdin/stdout mode, --reference-code mode
        fn_code = (
            "def two_sum_indices(nums, target):\n"
            "    seen = {}\n"
            "    for i, x in enumerate(nums):\n"
            "        if target - x in seen:\n"
            "            return [seen[target - x], i]\n"
            "        seen[x] = i\n"
            "    return []\n"
        )
        fn_cases = json.dumps([
            {"id": "basic", "args": [[2, 7, 11, 15], 9], "expected": [0, 1]},
            {"id": "dups", "args": [[3, 3], 6], "expected": [0, 1]},
            {"id": "none", "args": [[1, 2, 3], 99], "expected": []},
        ])
        rc_tc, res_tc = _cv(["test-cases", "--lang", "python", "--entry", "two_sum_indices", "--code", fn_code, "--cases", fn_cases, "--json"])
        self.assertEqual(rc_tc, 0)
        self.assertTrue(res_tc["allPassed"])
        self.assertEqual(res_tc["passedCount"], 3)
        self.assertEqual(res_tc["totalCount"], 3)

        rc_bad, res_bad = _cv([
            "test-cases", "--lang", "python", "--entry", "two_sum_indices",
            "--code", "def two_sum_indices(nums, target): return [0, 0]",
            "--cases", fn_cases, "--json",
        ])
        self.assertNotEqual(rc_bad, 0)
        self.assertFalse(res_bad["allPassed"])
        self.assertEqual(res_bad["passedCount"], 0)

        # Stdin/stdout mode
        io_code = "import sys\nn = int(sys.stdin.read().strip())\nprint(n * n)\n"
        io_cases = json.dumps([
            {"stdin": "5\n", "expected": "25"},
            {"stdin": "12\n", "expected": "144"},
        ])
        rc_io, res_io = _cv(["test-cases", "--lang", "python", "--code", io_code, "--cases", io_cases, "--json"])
        self.assertEqual(rc_io, 0)
        self.assertTrue(res_io["allPassed"])
        self.assertEqual(res_io["passedCount"], 2)

        # Reference-code differential verification
        cand_fib = (
            "def fib(n):\n"
            "    a, b = 0, 1\n"
            "    for _ in range(n):\n"
            "        a, b = b, a + b\n"
            "    return a\n"
        )
        ref_fib = "def fib(n):\n    return n if n <= 1 else fib(n - 1) + fib(n - 2)\n"
        ref_cases = json.dumps([{"args": [0]}, {"args": [1]}, {"args": [6]}, {"args": [10]}])
        rc_ref, res_ref = _cv([
            "test-cases", "--lang", "python", "--entry", "fib",
            "--code", cand_fib, "--reference-code", ref_fib, "--cases", ref_cases, "--json",
        ])
        self.assertEqual(rc_ref, 0)
        self.assertTrue(res_ref["allPassed"])
        self.assertEqual(res_ref["passedCount"], 4)

        # Unexported Node.js function with test-cases --entry (#105 P0.1 regression lock)
        node_add_cases = json.dumps([
            {"args": [2, 3], "expected": 5},
            {"args": [-4, 10], "expected": 6},
        ])
        rc_nadd, res_nadd = _cv([
            "test-cases", "--lang", "node", "--entry", "add",
            "--code", "function add(a, b) { return a + b; }",
            "--cases", node_add_cases, "--json",
        ])
        self.assertEqual(rc_nadd, 0, res_nadd)
        self.assertTrue(res_nadd["allPassed"])
        self.assertEqual(res_nadd["passedCount"], 2)

        # 3. `complexity`: O(1) vs O(N) vs O(N^2) + --expect verification + Node --entry & unsupported lang (#105 P0.2)
        # Use a fixed constant-work loop (~15us, independent of n and int size) so OS jitter cannot skew O(1).
        const_code = (
            "def const_sum(n):\n"
            "    acc = n & 7\n"
            "    for k in range(350):\n"
            "        acc = (acc + k) & 7\n"
            "    return acc\n"
        )
        rc_c1, res_c1 = _cv([
            "complexity", "--lang", "python", "--entry", "const_sum", "--generator", "int",
            "--code", const_code,
            "--sizes", "1000,2000,4000,8000,16000", "--repeats", "5", "--expect", "O(1)", "--json",
        ])
        self.assertEqual(rc_c1, 0, res_c1)
        self.assertEqual(res_c1["estimatedComplexity"], "O(1)")
        self.assertTrue(res_c1["meetsExpected"])

        rc_cn, res_cn = _cv([
            "complexity", "--lang", "python", "--entry", "lin_sum", "--generator", "int",
            "--code", "def lin_sum(n):\n    t = 0\n    for i in range(n): t += i\n    return t",
            "--sizes", "5000,10000,20000,40000", "--repeats", "7", "--expect", "O(N)", "--json",
        ])
        self.assertEqual(rc_cn, 0, res_cn)
        self.assertEqual(res_cn["estimatedComplexity"], "O(N)")
        self.assertTrue(res_cn["meetsExpected"])

        quad_code = (
            "def quad_pairs(n):\n"
            "    t = 0\n"
            "    for i in range(n):\n"
            "        for j in range(n):\n"
            "            t += 1\n"
            "    return t\n"
        )
        rc_cq, res_cq = _cv([
            "complexity", "--lang", "python", "--entry", "quad_pairs", "--generator", "int",
            "--code", quad_code,
            "--sizes", "60,120,240,480", "--repeats", "2", "--expect", "O(N^2)", "--json",
        ])
        self.assertEqual(rc_cq, 0, res_cq)
        self.assertEqual(res_cq["estimatedComplexity"], "O(N^2)")
        self.assertTrue(res_cq["meetsExpected"])

        rc_c_miss, res_c_miss = _cv([
            "complexity", "--lang", "python", "--entry", "quad_pairs", "--generator", "int",
            "--code", quad_code,
            "--sizes", "60,120,240,480", "--repeats", "2", "--expect", "O(N)", "--json",
        ])
        self.assertNotEqual(rc_c_miss, 0)
        self.assertFalse(res_c_miss["meetsExpected"])

        # Node O(N^2) bubbleSort with --lang node --entry bubbleSort --expect "O(N log N)" MUST fail (#105 P0.2)
        node_bubble = (
            "function bubbleSort(arr) {\n"
            "  const a = arr.slice();\n"
            "  for (let i = 0; i < a.length; i++) {\n"
            "    for (let j = 0; j < a.length - 1 - i; j++) {\n"
            "      if (a[j] > a[j + 1]) { const tmp = a[j]; a[j] = a[j + 1]; a[j + 1] = tmp; }\n"
            "    }\n"
            "  }\n"
            "  return a;\n"
            "}\n"
        )
        rc_nb, res_nb = _cv([
            "complexity", "--lang", "node", "--entry", "bubbleSort",
            "--code", node_bubble,
            "--sizes", "300,600,1200,2400", "--repeats", "3", "--expect", "O(N log N)", "--json",
        ])
        self.assertNotEqual(rc_nb, 0, res_nb)
        self.assertFalse(res_nb["meetsExpected"])
        self.assertEqual(res_nb["estimatedComplexity"], "O(N^2)")

        # Unsupported --lang cpp --entry foo MUST return rc=2 (#105 P0.2)
        rc_cpp, res_cpp = _cv([
            "complexity", "--lang", "cpp", "--entry", "foo",
            "--code", "int foo(int n) { return n; }", "--json",
        ])
        self.assertEqual(rc_cpp, 2, res_cpp)
        self.assertFalse(res_cpp["ok"])

        # 4. `ast-check`: --forbid-imports, --require-recursion, --max-loop-nesting
        rec_code = "def fact(n):\n    return 1 if n <= 1 else n * fact(n - 1)\n"
        rc_ap, res_ap = _cv([
            "ast-check", "--code", rec_code,
            "--forbid-imports", "os,subprocess", "--require-recursion", "--max-loop-nesting", "0", "--json",
        ])
        self.assertEqual(rc_ap, 0)
        self.assertTrue(res_ap["ok"])
        self.assertEqual(res_ap["violations"], [])
        self.assertIn("fact", res_ap["metrics"]["recursiveFunctions"])

        bad_ast_code = (
            "import os\n"
            "def nested(n):\n"
            "    s = 0\n"
            "    for i in range(n):\n"
            "        for j in range(n):\n"
            "            s += i + j\n"
            "    return s\n"
        )
        rc_af, res_af = _cv([
            "ast-check", "--code", bad_ast_code,
            "--forbid-imports", "os", "--require-recursion", "--max-loop-nesting", "1", "--json",
        ])
        self.assertNotEqual(rc_af, 0)
        self.assertFalse(res_af["ok"])
        v_rules = {v["rule"] for v in res_af["violations"]}
        self.assertGreaterEqual(len(res_af["violations"]), 3)
        self.assertTrue(any("import" in r for r in v_rules), f"Expected import violation in {v_rules}")
        self.assertTrue(any("recurs" in r for r in v_rules), f"Expected recursion violation in {v_rules}")
        self.assertTrue(any("loop" in r or "nesting" in r for r in v_rules), f"Expected loop nesting violation in {v_rules}")


class TestResearchScout(unittest.TestCase):
    """19. Offline Scope Charter & bounded research manifest tests (scripts/research_scout.py)."""

    def test_status_portability_and_manifest_anti_rabbit_hole_bounds(self) -> None:
        rs = load_script_module("research_scout.py")

        def _rs(argv: list[str]) -> tuple[int, dict[str, Any]]:
            buf = _EncodedStringIO()
            with contextlib.redirect_stdout(buf):
                rc = rs.main(argv)
            return rc, json.loads(buf.getvalue())

        # Zero hardcoded personal user home paths inside research_scout.py
        rs_source = (SCRIPTS_DIR / "research_scout.py").read_text(encoding="utf-8")
        personal_home_re = re.compile(
            r"(?:/Users/[A-Za-z0-9._-]+(?:/|[\"'\s]|$)|/home/[A-Za-z0-9._-]+(?:/|[\"'\s]|$)|C:\\Users\\[A-Za-z0-9._-]+\\)"
        )
        self.assertIsNone(personal_home_re.search(rs_source))

        with tempfile.TemporaryDirectory(prefix="synt-scout-") as tmp:
            t = Path(tmp)
            curated = t / "_curated"

            # 1. `status --json` (offline portable tier inspection)
            rc_st, st = _rs(["status", "--workspace", str(t), "--curated-dir", str(curated), "--json"])
            self.assertEqual(rc_st, 0)
            self.assertTrue(st["ok"])
            self.assertEqual(st["hardMaxSources"], 8)
            self.assertEqual(
                set(st["tiers"].keys()),
                {"tier1_mcp", "tier2_researchctl", "tier3_scholar_https", "tier4_offline_local"},
            )

            # 2. `manifest init` with inScope/outOfScope fences & maxSources=3
            rc_init, m0 = _rs([
                "manifest", "init", "distributed-raft",
                "--workspace", str(t), "--curated-dir", str(curated),
                "--title", "Raft Consensus Protocol",
                "--subject", "Distributed Systems",
                "--goal", "Implement leader election and log replication",
                "--depth", "mastery",
                "--in-scope", "leader election,log replication,safety proof",
                "--out-of-scope", "Byzantine fault tolerance,Paxos variants",
                "--max-sources", "3",
                "--json",
            ])
            self.assertEqual(rc_init, 0)
            self.assertTrue(m0["ok"])
            self.assertEqual(m0["maxSources"], 3)
            self.assertEqual(m0["manifest"]["inScope"], ["leader election", "log replication", "safety proof"])
            self.assertEqual(m0["manifest"]["outOfScope"], ["Byzantine fault tolerance", "Paxos variants"])
            self.assertTrue((curated / "distributed-raft" / "manifest.json").is_file())

            # 3. Add 3 distinct sources cleanly
            sources_to_add = [
                ("In Search of an Understandable Consensus Algorithm", "https://raft.github.io/raft.pdf", "primary_spine", "Canonical Raft paper"),
                ("MIT 6.824 Lab 2 Spec", "https://pdos.csail.mit.edu/6.824/labs/lab-raft.html", "problem_bank", "Implementation tests"),
                ("Students' Guide to Raft", "https://thesquareplanet.com/blog/students-guide-to-raft/", "secondary_reference", "Common pitfall guide"),
            ]
            for title, url, role, why in sources_to_add:
                rc_a, ma = _rs([
                    "manifest", "add-source", "distributed-raft",
                    "--workspace", str(t), "--curated-dir", str(curated),
                    "--source-title", title, "--source-url", url,
                    "--source-role", role, "--why-in-scope", why, "--json",
                ])
                self.assertEqual(rc_a, 0)
                self.assertTrue(ma["ok"])
            self.assertEqual(ma["sourceCount"], 3)

            # 4. URL deduplication: adding same URL updates in place (count stays 3, NOT 4)
            rc_dup, mdup = _rs([
                "manifest", "add-source", "distributed-raft",
                "--workspace", str(t), "--curated-dir", str(curated),
                "--source-title", "MIT 6.824 Raft Lab (Updated)",
                "--source-url", "https://pdos.csail.mit.edu/6.824/labs/lab-raft.html",
                "--source-role", "problem_bank",
                "--why-in-scope", "Canonical implementation exercises",
                "--json",
            ])
            self.assertEqual(rc_dup, 0)
            self.assertEqual(mdup["sourceCount"], 3)

            # 5. Anti-rabbit-hole cap: 4th distinct source exceeds maxSources=3 -> rejected
            rc_over, mover = _rs([
                "manifest", "add-source", "distributed-raft",
                "--workspace", str(t), "--curated-dir", str(curated),
                "--source-title", "Extra Rabbit Hole Paper",
                "--source-url", "https://example.org/extra-4th",
                "--source-role", "paper",
                "--json",
            ])
            self.assertNotEqual(rc_over, 0)
            self.assertFalse(mover["ok"])
            self.assertIn("maxSources", mover["error"])

            # 6. Hard cap: maxSources > 8 is rejected
            rc_cap, mcap = _rs([
                "manifest", "init", "too-many",
                "--workspace", str(t), "--curated-dir", str(curated),
                "--max-sources", "9", "--json",
            ])
            self.assertNotEqual(rc_cap, 0)
            self.assertFalse(mcap["ok"])
            self.assertIn("maxSources", mcap["error"])

            # 7. Approve manifest + verify outOfScope fence filtering
            rc_app, mapp = _rs([
                "manifest", "approve", "distributed-raft",
                "--workspace", str(t), "--curated-dir", str(curated), "--json",
            ])
            self.assertEqual(rc_app, 0)
            self.assertTrue(mapp["manifest"]["approved"])

            kept, dropped = rs._filter_by_scope_fences(
                [
                    {"title": "Raft Leader Election Safety", "snippet": "Quorum intersection proof"},
                    {"title": "Byzantine Fault Tolerance Overview", "snippet": "PBFT protocol"},
                ],
                mapp["manifest"],
            )
            self.assertEqual(dropped, 1)
            self.assertEqual(len(kept), 1)
            self.assertIn("Raft", kept[0]["title"])

            # 8. Scheme guard (#105 P0.3): `read file:///etc/passwd --json` MUST fail BEFORE Tier 2/3 dispatch
            marker = t / "rctl_called.marker"
            fake_rctl = t / "fake_researchctl"
            fake_rctl.write_text(
                f"#!/bin/sh\necho invoked > {str(marker)!r}\necho '{{\"ok\":true}}'\nexit 0\n",
                encoding="utf-8",
            )
            fake_rctl.chmod(0o755)
            with unittest.mock.patch.dict(os.environ, {"RESEARCHCTL_BIN": str(fake_rctl)}, clear=False):
                rc_file, res_file = _rs([
                    "read", "file:///etc/passwd", "--workspace", str(t), "--json",
                ])
            self.assertNotEqual(rc_file, 0)
            self.assertFalse(res_file["ok"])
            self.assertIn("http://", res_file["error"])
            self.assertFalse(marker.exists(), "file:// URL reached Tier 2 researchctl!")

            # 9. Non-`--json` `search` outputs concise human-readable text (#105 P1.3)
            mock_scholar = {
                "ok": True,
                "tier": 3,
                "query": "Raft consensus",
                "count": 1,
                "results": [
                    {
                        "provider": "arxiv",
                        "title": "In Search of an Understandable Consensus Algorithm",
                        "year": 2014,
                        "url": "https://raft.github.io/raft.pdf",
                        "snippet": "Consensus protocol",
                    }
                ],
            }
            buf_hum = _EncodedStringIO()
            with (
                unittest.mock.patch.object(rs, "run_scholar_search", return_value=mock_scholar),
                contextlib.redirect_stdout(buf_hum),
            ):
                rc_hum = rs.main(["search", "Raft consensus", "--scholar-only", "--workspace", str(t)])
            self.assertEqual(rc_hum, 0)
            hum_out = buf_hum.getvalue().strip()
            self.assertFalse(hum_out.startswith("{"), f"Expected human text, got JSON: {hum_out[:120]}")
            self.assertIn("Search 'Raft consensus'", hum_out)
            self.assertIn("Understandable Consensus", hum_out)


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


class TestTriProtocolHooksAndMcp(unittest.TestCase):
    """21. Tri-protocol hooks (AGY/Claude/Codex), multi-harness configs & Terminal non-UI MCP safeguard."""

    def test_tri_protocol_hooks_configs_and_terminal_non_ui_mcp_safeguard(self) -> None:
        # 1. Validate all multi-harness configuration files
        claude_settings = json.loads((ROOT / ".claude" / "settings.json").read_text(encoding="utf-8"))
        self.assertIn("SessionStart", claude_settings["hooks"])
        self.assertIn("PostToolUse", claude_settings["hooks"])
        self.assertIn("session-start.mjs", json.dumps(claude_settings["hooks"]["SessionStart"]))
        self.assertIn("post-graph-sync.mjs", json.dumps(claude_settings["hooks"]["PostToolUse"]))

        mcp_json = json.loads((ROOT / ".mcp.json").read_text(encoding="utf-8"))
        srv_cfg = mcp_json["mcpServers"]["syntopik-studio"]
        self.assertEqual(srv_cfg["command"], "node")
        self.assertTrue(any("dist/server.mjs" in a for a in srv_cfg["args"]))

        codex_toml = tomllib.loads((ROOT / ".codex" / "config.toml").read_text(encoding="utf-8"))
        self.assertGreaterEqual(codex_toml.get("project_doc_max_bytes", 0), 32768)
        self.assertTrue(codex_toml.get("features", {}).get("hooks"))
        self.assertEqual(codex_toml["mcp_servers"]["syntopik-studio"]["command"], "node")

        codex_hooks = json.loads((ROOT / ".codex" / "hooks.json").read_text(encoding="utf-8"))
        self.assertIn("SessionStart", codex_hooks["hooks"])
        self.assertIn("PostToolUse", codex_hooks["hooks"])
        self.assertIn("apply_patch", json.dumps(codex_hooks["hooks"]["PostToolUse"]))

        with tempfile.TemporaryDirectory(prefix="synt-tri-hooks-") as tmp:
            ws = Path(tmp) / "ws"
            kg = ws / "knowledge-graph"
            ch = ws / "chapters"
            kg.mkdir(parents=True)
            ch.mkdir(parents=True)
            (kg / "propositions.jsonl").write_text(
                json.dumps({"id": "cs-raft-1", "subject": "CS", "chapter": "Raft", "statement": "At most one leader per term.", "status": "solid"}) + "\n",
                encoding="utf-8",
            )
            (kg / "chains.jsonl").write_text("", encoding="utf-8")
            (kg / "mistakes.jsonl").write_text("", encoding="utf-8")

            base_env = {
                k: v
                for k, v in os.environ.items()
                if k not in ("CLAUDECODE", "CLAUDE_PROJECT_DIR", "CODEX_HOME", "MLH_WORKSPACE", "MLH_KG_DIR", "MLH_CHAPTERS_DIR")
            }
            base_env["SYNT_WORKSPACE"] = str(ws)
            base_env["SYNT_KG_DIR"] = str(kg)
            base_env["SYNT_CHAPTERS_DIR"] = str(ch)

            ss_script = str(ROOT / ".agents" / "hooks" / "session-start.mjs")
            ps_script = str(ROOT / ".agents" / "hooks" / "post-graph-sync.mjs")

            # 2a. session-start.mjs in Antigravity mode -> injectSteps
            ag = run_cli(["node", ss_script], stdin_text="{}", env=base_env)
            self.assertEqual(ag.returncode, 0, ag.stderr)
            ag_out = json.loads(ag.stdout)
            self.assertIn("injectSteps", ag_out)
            self.assertIn("# Syntopik — Live Study Snapshot", ag_out["injectSteps"][0]["systemMessage"]["systemMessage"])

            # 2b. session-start.mjs in Claude Code mode -> hookSpecificOutput.additionalContext
            cc_env = dict(base_env, CLAUDECODE="1")
            cc = run_cli(
                ["node", ss_script],
                stdin_text=json.dumps({"hook_event_name": "SessionStart", "session_id": "cc-test"}),
                env=cc_env,
            )
            self.assertEqual(cc.returncode, 0, cc.stderr)
            cc_out = json.loads(cc.stdout)
            self.assertNotIn("injectSteps", cc_out)
            self.assertEqual(cc_out["hookSpecificOutput"]["hookEventName"], "SessionStart")
            self.assertIn("# Syntopik — Live Study Snapshot", cc_out["hookSpecificOutput"]["additionalContext"])

            # 2c. session-start.mjs in Codex mode -> hookSpecificOutput.additionalContext
            cx_env = dict(base_env, CODEX_HOME=str(Path(tmp) / "codex-home"))
            cx = run_cli(
                ["node", ss_script],
                stdin_text=json.dumps({"hook_event_name": "SessionStart"}),
                env=cx_env,
            )
            self.assertEqual(cx.returncode, 0, cx.stderr)
            cx_out = json.loads(cx.stdout)
            self.assertIn("# Syntopik — Live Study Snapshot", cx_out["hookSpecificOutput"]["additionalContext"])

            # 3. post-graph-sync.mjs with Claude `tool_input.file_path` AND Codex `apply_patch` (strictly in /tmp ws)
            gdata = kg / "graph-data.js"
            if gdata.exists():
                gdata.unlink()
            ps_cc = run_cli(
                ["node", ps_script],
                stdin_text=json.dumps({
                    "tool_name": "Write",
                    "tool_input": {"file_path": str(kg / "propositions.jsonl")},
                }),
                env=base_env,
            )
            self.assertEqual(ps_cc.returncode, 0, ps_cc.stderr)
            self.assertEqual(ps_cc.stdout.strip(), "{}")
            self.assertTrue(gdata.is_file(), "post-graph-sync.mjs did not sync graph-data.js on Claude tool_input.file_path")
            self.assertIn("cs-raft-1", gdata.read_text(encoding="utf-8"))

            gdata.unlink()
            ps_cx = run_cli(
                ["node", ps_script],
                stdin_text=json.dumps({
                    "tool_name": "apply_patch",
                    "tool_input": {"patch": "*** Begin Patch\n*** Update File: chapters/raft-tracker.json\n*** End Patch\n"},
                }),
                env=base_env,
            )
            self.assertEqual(ps_cx.returncode, 0, ps_cx.stderr)
            self.assertEqual(ps_cx.stdout.strip(), "{}")
            self.assertTrue(gdata.is_file(), "post-graph-sync.mjs did not sync graph-data.js on Codex apply_patch")

            # 4. MCP Studio Server: BOTH synt_* and mlh_* tools + Terminal non-UI anti-leak safeguard
            server_mjs = ROOT / "mcp-app" / "mlh-studio" / "dist" / "server.mjs"
            data_dir = Path(tmp) / "mcp-data"
            secret_expl = "SECRET_QUIZ_EXPLANATION_NEVER_LEAK_7731"
            secret_sol = "SECRET_PRACTICE_SOLUTION_NEVER_LEAK_9914"
            reqs = [
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2024-11-05",
                        # Deliberately NO extensions["io.modelcontextprotocol/ui"] -> Terminal non-UI client!
                        "capabilities": {},
                        "clientInfo": {"name": "terminal-cli-harness", "version": "1.0"},
                    },
                },
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {
                        "name": "synt_quiz_card",
                        "arguments": {
                            "role": "probe",
                            "question": "What is the worst-case time complexity of binary search?",
                            "options": [
                                {"id": "a", "label": "O(log n)"},
                                {"id": "b", "label": "O(n)"},
                                {"id": "c", "label": "O(1)"},
                            ],
                            "explanation": secret_expl,
                            "correctAnswer": "a",
                        },
                    },
                },
                {
                    "jsonrpc": "2.0",
                    "id": 4,
                    "method": "tools/call",
                    "params": {
                        "name": "synt_practice_card",
                        "arguments": {
                            "action": "pose",
                            "problemId": "cs-dijkstra-1",
                            "statement": "Construct a 3-vertex directed graph where Dijkstra fails due to a negative edge.",
                            "mode": "recall",
                            "modelSolution": secret_sol,
                        },
                    },
                },
                {
                    "jsonrpc": "2.0",
                    "id": 5,
                    "method": "tools/call",
                    "params": {
                        "name": "synt_practice_card",
                        "arguments": {
                            "action": "conclude",
                            "problemId": "cs-dijkstra-1",
                            "outcome": "solved",
                            "verdict": "Exact counterexample verified.",
                            "rung": None,
                        },
                    },
                },
            ]
            proc = run_cli(
                ["node", str(server_mjs), f"--workspace={ws}", f"--data-dir={data_dir}"],
                stdin_text="\n".join(json.dumps(r) for r in reqs) + "\n",
                timeout=10.0,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            by_id = {
                int(m["id"]): m
                for m in (json.loads(ln) for ln in proc.stdout.splitlines() if ln.strip())
                if "id" in m
            }

            tool_names = {tl["name"] for tl in by_id[2]["result"]["tools"]}
            for required_name in (
                "synt_quiz_card", "synt_practice_card", "synt_lesson_studio",
                "mlh_quiz_card", "mlh_practice_card", "mlh_lesson_studio",
            ):
                self.assertIn(required_name, tool_names)

            # Terminal non-UI quiz response: bare question + options + "I don't know", ZERO answer/explanation leak!
            q_res = by_id[3]["result"]
            self.assertFalse(q_res.get("isError", False))
            self.assertTrue(q_res["structuredContent"].get("terminalFallback"))
            q_text = q_res["content"][0]["text"]
            self.assertIn("binary search", q_text)
            self.assertIn("O(log n)", q_text)
            self.assertIn("I don't know", q_text)
            q_dump = json.dumps(q_res)
            self.assertNotIn(secret_expl, q_dump, "Quiz explanation leaked to terminal client!")
            self.assertNotIn('"correctAnswer"', q_dump, "Quiz correctAnswer field leaked to terminal client!")

            # Terminal non-UI practice pose: statement present, modelSolution strictly hidden server-side!
            p_pose = by_id[4]["result"]
            self.assertFalse(p_pose.get("isError", False))
            self.assertTrue(p_pose["structuredContent"].get("terminalFallback"))
            self.assertTrue(p_pose["structuredContent"].get("hasHiddenSolution"))
            self.assertIn("3-vertex directed graph", p_pose["content"][0]["text"])
            self.assertNotIn(secret_sol, json.dumps(p_pose), "Practice modelSolution leaked during terminal pose!")

            # Practice conclude reveals committed modelSolution verbatim
            p_conc = by_id[5]["result"]
            self.assertFalse(p_conc.get("isError", False))
            self.assertEqual(p_conc["structuredContent"]["modelSolution"], secret_sol)
            self.assertIn(secret_sol, p_conc["content"][0]["text"])


class TestGitHubHygieneAndPortability(unittest.TestCase):
    """22. Public-repo hygiene, zero machine/user leaks, rule budgets, & cross-harness portability."""

    def test_zero_personal_path_leaks_and_github_release_hygiene(self) -> None:
        # Build forbidden search patterns generically so zero real usernames appear in repo tests.
        personal_home_re = re.compile(
            r"(?:/Users/[A-Za-z0-9._-]+(?:/|[\"'\s]|$)|/home/[A-Za-z0-9._-]+(?:/|[\"'\s]|$)|C:\\Users\\[A-Za-z0-9._-]+\\)"
        )
        hardcoded_brew_node = "/opt/homebrew/" + "bin/node"

        ls_tracked = run_cli(["git", "ls-files"])
        self.assertEqual(ls_tracked.returncode, 0, ls_tracked.stderr)
        ls_untracked = run_cli(["git", "ls-files", "--others", "--exclude-standard"])
        self.assertEqual(ls_untracked.returncode, 0, ls_untracked.stderr)

        candidates = sorted(
            set(ls_tracked.stdout.splitlines() + ls_untracked.stdout.splitlines())
        )
        self.assertGreater(len(candidates), 40)

        personal_leaks: list[str] = []
        config_node_leaks: list[str] = []
        config_suffixes = {".json", ".toml", ".md", ".mjs", ".js", ".ts", ".html"}

        for rel in candidates:
            if not rel or rel.startswith((".venv/", ".git/", "node_modules/", "mcp-app/mlh-studio/node_modules/")):
                continue
            p = ROOT / rel
            if not p.is_file() or p.is_symlink():
                continue
            try:
                raw = p.read_bytes()
            except OSError:
                continue
            if b"\x00" in raw[:4096]:
                continue
            text = raw.decode("utf-8", errors="ignore")
            if personal_home_re.search(text):
                personal_leaks.append(rel)
            if p.suffix.lower() in config_suffixes and hardcoded_brew_node in text:
                config_node_leaks.append(rel)

        self.assertEqual(personal_leaks, [], f"Personal path leak found in tracked/non-ignored files: {personal_leaks}")
        self.assertEqual(config_node_leaks, [], f"Hardcoded Homebrew node path found in config/doc/JS files: {config_node_leaks}")

        # Always-on rule budget: GEMINI.md + AGENTS.md <= 9,500 B (v3.0 unified ceiling)
        agents_bytes = (ROOT / "AGENTS.md").read_bytes()
        gemini_bytes = (ROOT / "GEMINI.md").read_bytes()
        combined_rules = len(agents_bytes) + len(gemini_bytes)
        self.assertLessEqual(
            combined_rules,
            9500,
            f"Combined GEMINI.md + AGENTS.md ({combined_rules} B) exceeds v3.0 9,500 B unified ceiling",
        )

        # CLAUDE.md starts with @AGENTS.md and has no YAML frontmatter
        claude_md = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
        self.assertTrue(claude_md.startswith("@AGENTS.md"), "CLAUDE.md must begin with '@AGENTS.md'")
        self.assertFalse(claude_md.lstrip().startswith("---"))

        # Exact 17/17 valid SKILL.md frontmatters with 0 '<' or '>' in YAML description
        skill_files = sorted(SKILLS_DIR.glob("*/SKILL.md"))
        self.assertEqual(len(skill_files), 17, f"Expected 17 skills, found {len(skill_files)}")
        for sk in skill_files:
            fm = parse_yaml_frontmatter(sk.read_text(encoding="utf-8"))
            self.assertIsNotNone(fm, f"{sk} missing frontmatter")
            assert fm is not None
            self.assertEqual(fm.get("name"), sk.parent.name)
            desc = fm.get("description", "")
            self.assertGreaterEqual(len(desc), 15)
            self.assertNotIn("<", desc, f"{sk} description has '<'")
            self.assertNotIn(">", desc, f"{sk} description has '>'")

        # .claude/skills resolves to .agents/skills and exposes all 17 skills
        claude_skills = ROOT / ".claude" / "skills"
        self.assertTrue(claude_skills.exists() and claude_skills.is_dir(), ".claude/skills must resolve to a directory")
        self.assertEqual(claude_skills.resolve(), SKILLS_DIR.resolve())
        self.assertEqual(len(list(claude_skills.glob("*/SKILL.md"))), 17)

        # Open-source & onboarding top-level docs exist and are non-empty
        for doc_name in ("LICENSE", "BOOTSTRAP.md", "CONTRIBUTING.md", "README.md"):
            doc_path = ROOT / doc_name
            self.assertTrue(doc_path.is_file(), f"Missing top-level doc: {doc_name}")
            self.assertGreater(doc_path.stat().st_size, 200, f"Top-level doc unexpectedly small: {doc_name}")


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

            triage = _cap(dp.main, ["--triage-hours", "48", "--sleep-hours", "7", "--kg-dir", str(kg_dp), "--chapters-dir", str(ch_dp), "--today", today_str, "--json"])
            self.assertEqual(triage["mode"], "exam_triage")
            self.assertGreater(triage["sleepProtectedHours"], 0)
            self.assertGreater(triage["usableStudyHours"], 0)
            self.assertEqual(set(triage["buckets"].keys()), {"salvage_70pct", "protect_20pct", "explicitly_cut"})
            salvage_ids = {x["propId"] for x in triage["buckets"]["salvage_70pct"]}
            self.assertIn("p-weak", salvage_ids)

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


class TestUniversalIngestObsidianAndPedagogy(unittest.TestCase):
    """24. Syntopik v2.0 Universal Source Ingestion, Bidirectional Obsidian/Mnemonic Sync & Pedagogy Linter."""

    def test_ingest_youtube_arxiv_repo_url_and_reading_queue(self) -> None:
        import syntopik.ingest as ing

        with tempfile.TemporaryDirectory(prefix="synt-v2-ingest-") as tmp:
            ws = Path(tmp) / "ws"
            (ws / "knowledge-graph").mkdir(parents=True)
            (ws / "chapters").mkdir(parents=True)

            # 1. Mocked YouTube VTT (~12 min -> multiple chunks with [mm:ss] links + Active Pause-Points)
            vtt_lines = ["WEBVTT", ""]
            for i in range(18):
                t0 = i * 40
                t1 = t0 + 35
                h0, m0, s0 = t0 // 3600, (t0 % 3600) // 60, t0 % 60
                h1, m1, s1 = t1 // 3600, (t1 % 3600) // 60, t1 % 60
                vtt_lines.append(f"{h0:02d}:{m0:02d}:{s0:02d}.000 --> {h1:02d}:{m1:02d}:{s1:02d}.000")
                vtt_lines.append(f"Segment {i + 1} explains consensus leader election invariants and quorum safety.")
                vtt_lines.append("")
            yt = ing.ingest_youtube(
                "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                workspace=ws,
                slug="raft-lecture",
                vtt_text="\n".join(vtt_lines),
                register_tracker=True,
                subject="Distributed Systems",
                title="Raft Consensus Lecture",
            )
            self.assertTrue(yt["ok"])
            lec_md = Path(yt["lecturePath"]).read_text(encoding="utf-8")
            self.assertIn("[00:00]", lec_md)
            self.assertIn("Active Pause-Point", lec_md)
            self.assertTrue((ws / "chapters" / "raft-lecture-tracker.json").is_file())

            # 2. Mocked arXiv Atom XML -> Keshav 3-Pass guide
            atom_xml = """<?xml version="1.0" encoding="UTF-8"?>
            <feed xmlns="http://www.w3.org/2005/Atom">
              <entry>
                <id>http://arxiv.org/abs/1706.03762v7</id>
                <updated>2023-08-02T00:00:00Z</updated>
                <published>2017-06-12T00:00:00Z</published>
                <title>Attention Is All You Need</title>
                <summary>We propose the Transformer, a model architecture relying entirely on self-attention.</summary>
                <author><name>Ashish Vaswani</name></author>
                <author><name>Noam Shazeer</name></author>
                <link title="pdf" href="http://arxiv.org/pdf/1706.03762v7" rel="related" type="application/pdf"/>
              </entry>
            </feed>"""
            ax = ing.ingest_arxiv(
                "1706.03762",
                workspace=ws,
                slug="transformer-paper",
                atom_xml=atom_xml,
                subject="Machine Learning",
            )
            self.assertTrue(ax["ok"])
            keshav_md = Path(ax["guidePath"]).read_text(encoding="utf-8")
            self.assertIn("Attention Is All You Need", keshav_md)
            self.assertIn("Pass 1", keshav_md)
            self.assertIn("Pass 2", keshav_md)
            self.assertIn("Pass 3", keshav_md)

            # 3. Local repo Golden-Path scan (skips .git, node_modules, .venv)
            dummy_repo = Path(tmp) / "sample-repo"
            (dummy_repo / "src").mkdir(parents=True)
            (dummy_repo / "tests").mkdir(parents=True)
            (dummy_repo / ".git").mkdir(parents=True)
            (dummy_repo / "node_modules" / "pkg").mkdir(parents=True)
            (dummy_repo / ".venv" / "lib").mkdir(parents=True)
            (dummy_repo / "README.md").write_text("# Sample Repo\nFast graph engine.\n", encoding="utf-8")
            (dummy_repo / "src" / "main.py").write_text("def main():\n    return 42\n", encoding="utf-8")
            (dummy_repo / "tests" / "test_main.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
            (dummy_repo / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
            (dummy_repo / "node_modules" / "pkg" / "index.js").write_text("module.exports = 1;\n", encoding="utf-8")
            (dummy_repo / ".venv" / "lib" / "site.py").write_text("x = 1\n", encoding="utf-8")

            rp = ing.ingest_repo(dummy_repo, workspace=ws, slug="sample-repo")
            self.assertTrue(rp["ok"])
            repo_map = Path(rp["repoMapPath"]).read_text(encoding="utf-8")
            self.assertIn("src/main.py", repo_map)
            self.assertNotIn("node_modules", repo_map)
            self.assertNotIn(".venv/lib", repo_map)

            # 4. `url` strict scheme & SSRF guards reject file://, javascript:, ftp://, loopback & link-local IPs
            for bad_url in (
                "file:///etc/passwd",
                "javascript:alert(1)",
                "ftp://example.org/a",
                "http://127.0.0.1/",
                "http://[::1]/",
                "http://169.254.169.254/latest/meta-data/",
                "http://localhost:4173/api/state",
            ):
                with self.subTest(bad_url=bad_url):
                    with self.assertRaises(ValueError):
                        ing.validate_http_url(bad_url)
                    with self.assertRaises(ValueError):
                        ing.ingest_url(bad_url, workspace=ws, slug="bad-url")
            bad_cli = run_cli([
                sys.executable, "-B", str(SCRIPTS_DIR / "ingest_source.py"),
                "url", "file:///etc/passwd", "--workspace", str(ws),
            ])
            self.assertNotEqual(bad_cli.returncode, 0)

            html_doc = "<html><head><title>Quorum Basics</title></head><body><h1>Quorum Basics</h1><p>Two majorities always intersect.</p></body></html>"
            u_res = ing.ingest_url("https://example.org/quorum", workspace=ws, slug="quorum-doc", html_text=html_doc)
            self.assertTrue(u_res["ok"])
            self.assertIn("Two majorities always intersect.", Path(u_res["articlePath"]).read_text(encoding="utf-8"))

            # 5. Incremental reading queue: add | due | advance (queued -> extracted -> clozed -> crystallized)
            q_lo = ing.queue_add("Low priority excerpt", "sources/a.md", workspace=ws, slug="s1", priority=25, item_id="rq-low")
            q_hi = ing.queue_add("High priority excerpt", "sources/b.md", workspace=ws, slug="s1", priority=95, item_id="rq-high")
            self.assertEqual(q_lo["stage"], "queued")
            self.assertEqual(q_hi["stage"], "queued")

            due_q = ing.queue_due(workspace=ws, limit=50)
            ids_ranked = [it["id"] for it in due_q["items"]]
            self.assertIn("rq-high", ids_ranked)
            self.assertIn("rq-low", ids_ranked)
            self.assertLess(ids_ranked.index("rq-high"), ids_ranked.index("rq-low"))

            s1 = ing.queue_advance("rq-high", workspace=ws)
            self.assertEqual(s1["item"]["stage"], "extracted")
            s2 = ing.queue_advance("rq-high", workspace=ws)
            self.assertEqual(s2["item"]["stage"], "clozed")
            s3 = ing.queue_advance("rq-high", workspace=ws, linked_prop_id="cs-quorum-1")
            self.assertEqual(s3["item"]["stage"], "crystallized")

    def test_obsidian_vault_sync_markers_wikilinks_and_fsrs_preservation(self) -> None:
        import syntopik.obsidian as obs

        with tempfile.TemporaryDirectory(prefix="synt-v2-obsidian-") as tmp:
            ws = Path(tmp) / "ws"
            kg = ws / "knowledge-graph"
            vault = Path(tmp) / "obsidian-vault"
            kg.mkdir(parents=True)

            props = [
                {
                    "id": "phys-shm-law",
                    "subject": "Physics",
                    "chapter": "SHM",
                    "title": "Restoring force proportional to displacement",
                    "kind": "axiom",
                    "status": "solid",
                    "bktMastery": 0.88,
                    "nextReview": "2026-12-10",
                    "fsrs": {"stability": 14.5, "difficulty": 3.2, "due": "2026-12-10T12:00:00+00:00"},
                },
                {
                    "id": "phys-shm-period",
                    "subject": "Physics",
                    "chapter": "SHM",
                    "title": "SHM period T = 2*pi*sqrt(m/k)",
                    "kind": "derivation",
                    "status": "shaky",
                    "bktMastery": 0.40,
                },
            ]
            (kg / "propositions.jsonl").write_text("\n".join(json.dumps(p) for p in props) + "\n", encoding="utf-8")
            (kg / "chains.jsonl").write_text(
                json.dumps({"from": "phys-shm-law", "to": "phys-shm-period", "relation": "derives", "why": "Solve ODE"}) + "\n",
                encoding="utf-8",
            )
            (kg / "mistakes.jsonl").write_text(
                json.dumps({
                    "id": "m-11112222", "date": "2026-11-25", "subject": "Physics", "chapter": "SHM",
                    "propId": "phys-shm-period", "category": "sign_error",
                    "triggerCue": "Vertical spring", "wrongMove": "Included g in T",
                    "correctMove": "Gravity shifts x_eq only", "sourceRef": "test",
                }) + "\n",
                encoding="utf-8",
            )

            # 1. Export to Obsidian vault -> minimal YAML, [[wikilinks]], Trap-MOC.md, markers
            exp1 = obs.export_vault(vault, workspace=ws, kg_dir=kg)
            self.assertTrue(exp1["ok"])
            self.assertTrue((vault / "Trap-MOC.md").is_file())
            md_files = list(vault.rglob("*.md"))
            law_file = next(p for p in md_files if "syntopik_id: phys-shm-law" in p.read_text(encoding="utf-8"))
            law_txt = law_file.read_text(encoding="utf-8")
            self.assertIn(obs.SYNT_BEGIN, law_txt)
            self.assertIn(obs.SYNT_END, law_txt)
            self.assertIn("[[", law_txt)
            fm_part = law_txt.split("---", 2)[1]
            self.assertNotIn("stability", fm_part)
            self.assertNotIn("fsrs", fm_part)

            # 2. Add custom YAML, personal prose outside markers, :::cue block, and {{c1::...}} cloze
            custom_block = (
                "\nMy personal insight above stays forever.\n\n"
                ":::cue {propId: phys-shm-law, id: c-vault-1, kind: recall}\n"
                "Q: What sign relates restoring force F and displacement x?\n"
                "A: Opposite sign: F = -kx.\n"
                ":::\n\n"
                "Key invariant: {{c1::restoring force::physics}} always points toward equilibrium.\n"
            )
            edited = law_txt.replace("syntopik_id: phys-shm-law", "syntopik_id: phys-shm-law\ncustom_author_tag: keep-me", 1)
            edited = edited + custom_block
            law_file.write_text(edited, encoding="utf-8")

            # 3. Re-export MUST preserve custom YAML and user prose outside markers
            exp2 = obs.export_vault(vault, workspace=ws, kg_dir=kg)
            self.assertTrue(exp2["ok"])
            re_txt = law_file.read_text(encoding="utf-8")
            self.assertIn("custom_author_tag: keep-me", re_txt)
            self.assertIn("My personal insight above stays forever.", re_txt)

            # 4. Import twice (idempotent) -> imports :::cue + {{c1::...}} AND preserves FSRS stability & bktMastery!
            imp1 = obs.import_vault(vault, workspace=ws, kg_dir=kg, no_sync=True)
            self.assertTrue(imp1["ok"])
            self.assertGreaterEqual(imp1["cuesAdded"], 2)
            self.assertGreaterEqual(imp1["cuesParsed"], 1)
            self.assertGreaterEqual(imp1["clozesParsed"], 1)
            imp2 = obs.import_vault(vault, workspace=ws, kg_dir=kg, no_sync=True)
            self.assertTrue(imp2["ok"])
            self.assertEqual(imp2["cuesAdded"], 0)

            reloaded = {
                r["id"]: r
                for r in (json.loads(ln) for ln in (kg / "propositions.jsonl").read_text(encoding="utf-8").splitlines() if ln.strip())
            }
            self.assertEqual(reloaded["phys-shm-law"]["fsrs"]["stability"], 14.5)
            self.assertEqual(reloaded["phys-shm-law"]["bktMastery"], 0.88)
            cue_ids = [c["id"] for c in reloaded["phys-shm-law"].get("cues", [])]
            self.assertIn("c-vault-1", cue_ids)
            self.assertEqual(len(cue_ids), len(set(cue_ids)), "Repeated import duplicated cues!")

            # 5. Marker & fence breakout hardening: literal `<!-- syntopik:end -->` and `:::` stay byte-for-byte idempotent!
            reloaded["phys-shm-period"]["notes"] = "Trap note <!-- syntopik:end -->\n:::\nstill inside managed block"
            reloaded["phys-shm-period"]["cues"] = [
                {"id": "c-fence", "q": "Check line 1\n:::\nCheck line 2", "a": "Answer survives ::: fence cleanly.", "kind": "recall"}
            ]
            (kg / "propositions.jsonl").write_text(
                "\n".join(json.dumps(r) for r in reloaded.values()) + "\n",
                encoding="utf-8",
            )
            obs.export_vault(vault, workspace=ws, kg_dir=kg)
            period_file = next(p for p in vault.rglob("*.md") if "syntopik_id: phys-shm-period" in p.read_text(encoding="utf-8"))
            snap_1 = period_file.read_text(encoding="utf-8")
            obs.export_vault(vault, workspace=ws, kg_dir=kg)
            snap_2 = period_file.read_text(encoding="utf-8")
            self.assertEqual(snap_1, snap_2, "Obsidian re-export mutated/duplicated note containing marker/fence strings!")
            obs.import_vault(vault, workspace=ws, kg_dir=kg, no_sync=True)
            re_after = {
                r["id"]: r
                for r in (json.loads(ln) for ln in (kg / "propositions.jsonl").read_text(encoding="utf-8").splitlines() if ln.strip())
            }
            c_fence = next(c for c in re_after["phys-shm-period"].get("cues", []) if c["id"] == "c-fence")
            self.assertIn("Answer survives", c_fence["a"])

    def test_pedagogy_eval_bench_lint_and_check_card(self) -> None:
        pe = load_script_module("pedagogy_eval.py")

        # 1. Built-in 40-scenario TutorBench v3.0 golden benchmark passes 40/40
        bench = pe.run_benchmark()
        self.assertTrue(bench["ok"])
        self.assertEqual(bench["total"], 40)
        self.assertEqual(bench["passed"], 40)

        # 2. `lint` catches sycophancy & >160w prose, passes calm concise turn
        syco = pe.lint_turn("Great question! You nailed it! Predict the equilibrium shift.")
        self.assertFalse(syco["passed"])
        self.assertIn("zero_sycophancy", {v["rule"] for v in syco["violations"]})

        long_turn = " ".join(["invariant"] * 175) + " What happens next?"
        over = pe.lint_turn(long_turn, mode="teach")
        self.assertFalse(over["passed"])
        self.assertTrue(any("word" in v["rule"] or "prose" in v["rule"] for v in over["violations"]))

        clean = pe.lint_turn(
            "Constant external force shifts equilibrium $x_0 = mg/k$ without changing curvature $k$.\n\n"
            "$$T = 2\\pi\\sqrt{\\frac{m}{k}}$$\n\n"
            "Does doubling gravity change $T$?",
            mode="teach",
        )
        self.assertTrue(clean["passed"], clean)

        # 3. `check-card` catches `"because"` justification leak & missing `"I don't know"`
        bad_card = pe.check_quiz_card({
            "role": "check",
            "question": "How does gravity affect a vertical spring period?",
            "options": [
                {"id": "a", "label": "Unchanged because gravity only shifts equilibrium"},
                {"id": "b", "label": "Period decreases"},
            ],
            "explanation": "Restoring stiffness k is unchanged.",
            "correctAnswer": "a",
        })
        self.assertFalse(bad_card["passed"])
        bad_rules = {v["rule"] for v in bad_card["violations"]}
        self.assertIn("option_justification_leak", bad_rules)
        self.assertIn("missing_idk_option", bad_rules)


class TestMoonshotEnginesAndPocketPacks(unittest.TestCase):
    """25. Syntopik v2.0 Moonshot Engines (Fork Replay, Falsify Arena, Decision Blitz) + Portable Study Packs & Pocket Deck."""

    def test_fork_replay_falsify_arena_and_decision_blitz(self) -> None:
        import syntopik.forks as fk

        fa = load_script_module("falsify_arena.py")
        db = load_script_module("decision_blitz.py")

        with tempfile.TemporaryDirectory(prefix="synt-v2-moonshots-") as tmp:
            ws = Path(tmp) / "ws"
            kg = ws / "knowledge-graph"
            ch = ws / "chapters"
            kg.mkdir(parents=True)
            ch.mkdir(parents=True)

            # Seed propositions & mistakes for arena lemma persistence + blitz build
            seed_props = [
                {"id": "cs-binsearch", "subject": "CS", "chapter": "Search", "title": "Binary search inclusive loop invariant lo <= hi", "status": "shaky", "bktMastery": 0.4},
                {"id": "phys-work-spring", "subject": "Physics", "chapter": "Work-Energy", "title": "Spring restoring work equals minus delta U", "status": "shaky", "bktMastery": 0.4},
                {"id": "phys-work-friction", "subject": "Physics", "chapter": "Work-Energy", "title": "Kinetic friction work sign depends on ground displacement", "status": "shaky", "bktMastery": 0.4},
                {"id": "phys-shm-spring", "subject": "Physics", "chapter": "SHM", "title": "Spring block time period invariant under constant force", "status": "solid", "bktMastery": 0.85},
            ]
            (kg / "propositions.jsonl").write_text("\n".join(json.dumps(p) for p in seed_props) + "\n", encoding="utf-8")
            (kg / "chains.jsonl").write_text("", encoding="utf-8")
            (kg / "mistakes.jsonl").write_text(
                "\n".join(
                    json.dumps(m)
                    for m in [
                        {
                            "id": "m-f1111111", "date": "2026-11-25", "subject": "Physics", "chapter": "Work-Energy",
                            "propId": "phys-work-spring", "category": "sign_error",
                            "triggerCue": "Spring stretched x1 to x2", "wrongMove": "Used +1/2 k (x2^2 - x1^2)",
                            "correctMove": "Use -delta U = 1/2 k (x1^2 - x2^2)", "sourceRef": "test",
                        },
                        {
                            "id": "m-f2222222", "date": "2026-11-25", "subject": "Physics", "chapter": "Work-Energy",
                            "propId": "phys-work-friction", "category": "condition_missed",
                            "triggerCue": "Two stacked blocks with friction", "wrongMove": "Assumed friction work always negative",
                            "correctMove": "Check block displacement relative to ground", "sourceRef": "test",
                        },
                    ]
                ) + "\n",
                encoding="utf-8",
            )

            # 1. Blunder FEN Fork Replay: repeat-blunder refutation WITHOUT leaking winning move + mutation + latency
            win_secret = "1/2*k*(x1**2 - x2**2)"
            cap_f = fk.capture_fork(
                prop_id="phys-work-spring",
                prefix_steps=["F_s(x) = -k*x", "W_s = integral_{x1}^{x2} F_s dx"],
                fork_prompt="Evaluate work done by spring from x1 to x2",
                sealed_blunder="1/2*k*(x2**2 - x1**2)",
                sealed_winning_move=win_secret,
                refutation_msg="Stretching x2 > x1 opposes restoring force, so W_s must be negative!",
                verify_spec={"engine": "math"},
                workspace=ws,
                kg_dir=kg,
            )
            fid = cap_f["id"]
            due_f = fk.list_due_forks(workspace=ws, kg_dir=kg, include_sealed=False)
            self.assertNotIn(win_secret, json.dumps(due_f), "list_due_forks leaked sealedWinningMove!")

            # Repeat exact blunder -> plays refutation WITHOUT leaking winning move!
            chk_blunder = fk.check_fork_move(fid, "0.5*k*(x2^2 - x1^2)", latency_ms=11000, workspace=ws, kg_dir=kg)
            self.assertTrue(chk_blunder["repeatedBlunder"])
            self.assertFalse(chk_blunder["correct"])
            self.assertIn("must be negative", chk_blunder["refutation"])
            self.assertIsNone(chk_blunder["winningMove"])
            self.assertNotIn(win_secret, json.dumps(chk_blunder), "Repeat blunder leaked winning move!")

            # Submit algebraically equivalent winning move faster -> win + Woodpecker latency compression!
            chk_win = fk.check_fork_move(fid, "k*(x1**2 - x2**2)/2", latency_ms=3500, workspace=ws, kg_dir=kg)
            self.assertTrue(chk_win["correct"])
            self.assertEqual(chk_win["winCount"], 1)
            self.assertGreater(chk_win["latencyTrend"]["compressionRatio"], 2.0)

            mut = fk.mutate_fork(fid, seed=7, workspace=ws, kg_dir=kg, include_sealed=False)
            self.assertTrue(mut["ok"])
            self.assertNotIn("sealedWinningMove", mut["variant"])

            # 2. Adversarial Counterexample Arena: kill mutant with ZERO survivor leak + Lakatos lemma repair
            ar = fa.create_arena(
                prop_id="cs-binsearch",
                domain="code",
                claim="Return index of target in sorted list or -1",
                entry="bsearch",
                sealed_reference={
                    "code": (
                        "def bsearch(a, t):\n"
                        "    lo, hi = 0, len(a) - 1\n"
                        "    while lo <= hi:\n"
                        "        m = (lo + hi) // 2\n"
                        "        if a[m] == t: return m\n"
                        "        if a[m] < t: lo = m + 1\n"
                        "        else: hi = m - 1\n"
                        "    return -1\n"
                    )
                },
                sealed_mutants=[
                    {
                        "id": "m-offbyone",
                        "trapTag": "strict_lt_loop",
                        "code": (
                            "def bsearch(a, t):\n"
                            "    lo, hi = 0, len(a) - 1\n"
                            "    while lo < hi:\n"
                            "        m = (lo + hi) // 2\n"
                            "        if a[m] == t: return m\n"
                            "        if a[m] < t: lo = m + 1\n"
                            "        else: hi = m - 1\n"
                            "    return -1\n"
                        ),
                    },
                    {
                        "id": "m-secret-survivor",
                        "trapTag": "SECRET_EMPTY_CRASH_TAG",
                        "code": (
                            "def bsearch(a, t):\n"
                            "    if a[0] == t: return 0\n"
                            "    return -1\n"
                        ),
                    },
                ],
                assumption_predicates=["len(args) == 2"],
                workspace=ws,
                kg_dir=kg,
            )
            aid = ar["id"]
            t1 = fa.test_arena_input(aid, json.dumps({"args": [[5], 5]}), workspace=ws, kg_dir=kg)
            self.assertTrue(t1["validAssumption"], t1)
            self.assertIn("m-offbyone", t1["killed"])
            self.assertEqual(t1["survivedCount"], 1)
            t1_dump = json.dumps(t1)
            self.assertNotIn("m-secret-survivor", t1_dump, "Surviving mutant ID leaked!")
            self.assertNotIn("SECRET_EMPTY_CRASH_TAG", t1_dump, "Surviving mutant trapTag leaked!")
            self.assertNotIn("a[0]", t1_dump, "Surviving mutant code leaked!")

            t2 = fa.test_arena_input(aid, json.dumps({"args": [[], 5]}), workspace=ws, kg_dir=kg)
            self.assertIn("m-secret-survivor", t2["killed"])
            self.assertEqual(t2["survivedCount"], 0)
            self.assertTrue(t2["allKilled"])

            lemma = "Invariant requires inclusive lo <= hi and empty-list guard."
            rep = fa.repair_arena_lemma(aid, lemma, workspace=ws, kg_dir=kg)
            self.assertTrue(rep["ok"])
            self.assertTrue(rep["persistedToProp"])
            reloaded_props = {
                r["id"]: r
                for r in (json.loads(ln) for ln in (kg / "propositions.jsonl").read_text(encoding="utf-8").splitlines() if ln.strip())
            }
            self.assertIn(lemma, reloaded_props["cs-binsearch"].get("boundaryConditions", []))

            # 3. ShadowBox Decision Blitz: sealed build + grade + Newman Tier-1 stats
            blitz = db.build_blitz(n=3, seed=42, workspace=ws, kg_dir=kg, chapters_dir=ch)
            self.assertTrue(blitz["ok"])
            self.assertGreaterEqual(len(blitz["items"]), 2)
            # Sealed key must NOT leak in public blitz payload
            self.assertNotIn("answerKey", json.dumps(blitz))
            first_item = blitz["items"][0]
            self.assertIn("I don't know", json.dumps(first_item))

            # Grade 2 decisions (1 via sealed blitz session, 1 explicit) and verify Newman Tier-1 stats
            sessions = json.loads((kg / "blitz-sessions.json").read_text(encoding="utf-8"))
            sealed_key = sessions[blitz["blitzId"]]["items"][first_item["itemId"]]
            g1 = db.grade_decision(
                blitz_id=blitz["blitzId"],
                item_id=first_item["itemId"],
                kill_cue_answer=sealed_key["killCue"],
                first_move_answer=sealed_key["firstMove"],
                rejected_decoy_answer=sealed_key["rejectedDecoy"],
                decision_ms=11000,
                workspace=ws,
                kg_dir=kg,
            )
            self.assertTrue(g1["ok"])
            self.assertEqual(g1["fieldsHit"], 3)
            self.assertEqual(g1["itemAccuracy"], 1.0)
            self.assertIn("expertLensDiff", g1)

            g2 = db.grade_decision(
                prop_id="phys-work-friction",
                kill_cue_hit=True,
                first_move_hit=True,
                decoy_hit=True,
                decision_ms=15000,
                workspace=ws,
                kg_dir=kg,
            )
            self.assertTrue(g2["ok"])
            stats = db.compute_decision_stats(workspace=ws, kg_dir=kg)
            self.assertEqual(stats["totalDecisions"], 2)
            self.assertAlmostEqual(stats["medianDecisionSec"], 13.0, places=2)
            self.assertEqual(stats["speedAccuracyZone"], "expert_fluent")

            # 4. Security hardening (#172 P0.1): falsify_arena & forks reject RCE + 9**9**8 DoS in < 0.5s with zero execution
            canary = Path(tmp) / "rce_canary.txt"
            math_ar = fa.create_arena(
                prop_id="phys-work-spring",
                domain="math",
                claim="Check polynomial identity",
                sealed_reference={"expr": "x + 1"},
                sealed_mutants=[{"id": "m-bad", "trapTag": "wrong_const", "expr": "x + 2"}],
                workspace=ws,
                kg_dir=kg,
            )
            rce_payload = f"__import__('os').system('touch {canary}')"
            open_payload = f"open({str(canary)!r}, 'w').write('pwn')"
            t0 = time.monotonic()
            for payload in (rce_payload, open_payload, "9**9**8"):
                # Arena math input: must reject cleanly without executing or hanging
                try:
                    ar_res = fa.test_arena_input(math_ar["id"], json.dumps({"x": payload}), workspace=ws, kg_dir=kg)
                    self.assertFalse(ar_res.get("allKilled", False))
                except ValueError:
                    pass
                # Fork math move: must reject cleanly without executing or hanging
                fk_res = fk.check_fork_move(fid, payload, workspace=ws, kg_dir=kg)
                self.assertFalse(fk_res["correct"])
            elapsed_sec = time.monotonic() - t0
            self.assertLess(elapsed_sec, 0.5, f"RCE/DoS guard took too long ({elapsed_sec:.3f}s)")
            self.assertFalse(canary.exists(), "RCE payload executed in falsify_arena or forks!")

    def test_portable_study_packs_security_and_pocket_deck_qr(self) -> None:
        import syntopik.packs as pk
        import syntopik.pocket as po

        with tempfile.TemporaryDirectory(prefix="synt-v2-pack-pocket-") as tmp:
            t = Path(tmp)
            ws_src = t / "ws-src"
            kg_src = ws_src / "knowledge-graph"
            ch_src = ws_src / "chapters"
            kg_src.mkdir(parents=True)
            ch_src.mkdir(parents=True)

            src_props = [
                {
                    "id": "shm-root",
                    "subject": "Physics",
                    "chapter": "SHM",
                    "title": "Linear restoring force F = -kx",
                    "kind": "axiom",
                    "status": "solid",
                    "bktMastery": 0.95,
                    "lapses": 0,
                    "nextReview": "2026-12-20",
                    "lastReviewed": "2026-11-25T10:00:00Z",
                    "notes": "PRIVATE_PERSONAL_NOTE_NEVER_EXPORT",
                    "fsrs": {"stability": 30.0, "difficulty": 2.0, "due": "2026-12-20T10:00:00+00:00"},
                },
                {
                    "id": "shm-energy",
                    "subject": "Physics",
                    "chapter": "SHM",
                    "title": "Total SHM mechanical energy E = 1/2 k A^2",
                    "kind": "derivation",
                    "status": "shaky",
                    "bktMastery": 0.45,
                    "nextReview": "2026-11-20",
                    "fsrs": {"stability": 2.0, "difficulty": 5.5, "due": "2026-11-20T10:00:00+00:00"},
                },
            ]
            (kg_src / "propositions.jsonl").write_text("\n".join(json.dumps(p) for p in src_props) + "\n", encoding="utf-8")
            (kg_src / "chains.jsonl").write_text(
                json.dumps({"from": "shm-root", "to": "shm-energy", "relation": "derives"}) + "\n",
                encoding="utf-8",
            )
            (kg_src / "mistakes.jsonl").write_text("", encoding="utf-8")

            # 1. Export Study Pack -> zero PII fields + valid SHA-256 checksums
            pack_zip = t / "shm.syntopik-pack.zip"
            exp = pk.export_pack("SHM", workspace=ws_src, kg_dir=kg_src, chapters_dir=ch_src, out_path=pack_zip, author="Test")
            self.assertTrue(exp["ok"])
            self.assertTrue(pack_zip.is_file())
            ver = pk.verify_pack(pack_zip)
            self.assertTrue(ver["ok"], ver)

            with zipfile.ZipFile(pack_zip, "r") as zf:
                pack_data_txt = zf.read(pk.DATA_NAME).decode("utf-8")
            self.assertNotIn("PRIVATE_PERSONAL_NOTE_NEVER_EXPORT", pack_data_txt)
            pack_data = json.loads(pack_data_txt)
            for p_item in pack_data["propositions"]:
                for pii_field in ("fsrs", "bktMastery", "nextReview", "lapses", "notes", "lastReviewed"):
                    self.assertNotIn(pii_field, p_item, f"PII field {pii_field!r} leaked into exported pack!")

            # 2. Tamper & stripped-checksum rejection (SHA-256 mismatch AND missing checksums both fail closed!)
            tampered_zip = t / "tampered.syntopik-pack.zip"
            with zipfile.ZipFile(pack_zip, "r") as zin, zipfile.ZipFile(tampered_zip, "w") as zout:
                for info in zin.infolist():
                    data = zin.read(info.filename)
                    if info.filename == pk.DATA_NAME:
                        data = data + b" "
                    zout.writestr(info, data)
            ver_tamper = pk.verify_pack(tampered_zip)
            self.assertFalse(ver_tamper["ok"])

            stripped_zip = t / "stripped-checksums.syntopik-pack.zip"
            with zipfile.ZipFile(pack_zip, "r") as zin, zipfile.ZipFile(stripped_zip, "w") as zout:
                for info in zin.infolist():
                    if info.filename == pk.CHECKSUMS_NAME:
                        continue
                    data = zin.read(info.filename)
                    if info.filename == pk.MANIFEST_NAME:
                        mf = json.loads(data.decode("utf-8"))
                        mf.pop("sha256", None)
                        mf.pop("contentSha256", None)
                        data = json.dumps(mf).encode("utf-8")
                    zout.writestr(info, data)
            ver_stripped = pk.verify_pack(stripped_zip)
            self.assertFalse(ver_stripped["ok"], f"Stripped-checksum ZIP must fail verify_pack: {ver_stripped}")

            # 2b. `export_pack(..., include_exams=True)` excludes personal `*-attempt-*.json` while keeping exam specs
            ex_src_dir = ws_src / "exams"
            ex_src_dir.mkdir(parents=True, exist_ok=True)
            (ex_src_dir / "shm-mock.json").write_text(
                json.dumps({
                    "examSlug": "shm-mock",
                    "title": "SHM Mock",
                    "subject": "Physics",
                    "chapter": "SHM",
                    "questions": [{"id": "Q1", "subject": "Physics", "chapter": "SHM", "stem": "Check T", "answer": "A"}],
                }),
                encoding="utf-8",
            )
            (ex_src_dir / "shm-attempt-20261126T120000Z.json").write_text(
                json.dumps({
                    "examSlug": "shm-mock",
                    "startedAt": "2026-11-26T12:00:00Z",
                    "submittedAt": "2026-11-26T12:10:00Z",
                    "timeSpentSec": 600,
                    "responses": {"Q1": {"answer": "LEARNER_SECRET_RESPONSE"}},
                }),
                encoding="utf-8",
            )
            pack_with_exams = t / "shm-exams.syntopik-pack.zip"
            exp_ex = pk.export_pack(
                "SHM", workspace=ws_src, kg_dir=kg_src, chapters_dir=ch_src,
                out_path=pack_with_exams, include_exams=True,
            )
            self.assertTrue(exp_ex["ok"])
            with zipfile.ZipFile(pack_with_exams, "r") as zf:
                ex_data_txt = zf.read(pk.DATA_NAME).decode("utf-8")
            self.assertNotIn("LEARNER_SECRET_RESPONSE", ex_data_txt)
            self.assertNotIn("shm-attempt-", ex_data_txt)
            self.assertIn("shm-mock", ex_data_txt)

            # 3. Zip-slip rejection
            zipslip_zip = t / "zipslip.syntopik-pack.zip"
            with zipfile.ZipFile(zipslip_zip, "w") as zf:
                zf.writestr("../escape.txt", "pwn")
            self.assertFalse(pk.verify_pack(zipslip_zip)["ok"])

            # 4. POSIX symlink rejection
            symlink_zip = t / "symlink.syntopik-pack.zip"
            with zipfile.ZipFile(symlink_zip, "w") as zf:
                zi = zipfile.ZipInfo("evil-link")
                zi.create_system = 3
                zi.external_attr = (0o120777 << 16)
                zf.writestr(zi, "/etc/passwd")
            self.assertFalse(pk.verify_pack(symlink_zip)["ok"])

            # 5. Non-destructive `suspend-all` import preserves existing `solid` FSRS!
            ws_dst = t / "ws-dst"
            kg_dst = ws_dst / "knowledge-graph"
            ch_dst = ws_dst / "chapters"
            kg_dst.mkdir(parents=True)
            ch_dst.mkdir(parents=True)
            existing_solid = {
                "id": "shm-root",
                "subject": "Physics",
                "chapter": "SHM",
                "title": "Linear restoring force F = -kx",
                "kind": "axiom",
                "status": "solid",
                "bktMastery": 0.96,
                "fsrs": {"stability": 42.0, "difficulty": 1.8, "due": "2027-01-10T00:00:00+00:00"},
            }
            (kg_dst / "propositions.jsonl").write_text(json.dumps(existing_solid) + "\n", encoding="utf-8")
            (kg_dst / "chains.jsonl").write_text("", encoding="utf-8")

            imp = pk.import_pack(pack_zip, workspace=ws_dst, kg_dir=kg_dst, chapters_dir=ch_dst, mode="suspend-all", no_sync=True)
            self.assertTrue(imp["ok"])
            dst_props = {
                r["id"]: r
                for r in (json.loads(ln) for ln in (kg_dst / "propositions.jsonl").read_text(encoding="utf-8").splitlines() if ln.strip())
            }
            self.assertEqual(dst_props["shm-root"]["status"], "solid")
            self.assertEqual(dst_props["shm-root"]["fsrs"]["stability"], 42.0)
            self.assertIn("shm-energy", dst_props)
            self.assertEqual(dst_props["shm-energy"]["status"], "planned")

            # 6. Zero-CDN Offline Pocket HTML + XSS SVG Sanitization + Idempotent Sync (FSRS + FIRe) + Pure-Stdlib QR
            xss_prop = dict(src_props[1])
            xss_prop["svg"] = (
                '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
                '<script>alert("xss")</script>'
                '<image href="x" onerror="window.__PWN__=1"/>'
                '<rect width="50" height="50" fill="#4f46e5"/></svg>'
            )
            (kg_src / "propositions.jsonl").write_text(
                "\n".join(json.dumps(p) for p in [src_props[0], xss_prop]) + "\n",
                encoding="utf-8",
            )
            pocket_html = t / "pocket-review.html"
            p_exp = po.export_pocket_html(workspace=ws_src, out_path=pocket_html, limit=10, kg_dir=kg_src)
            self.assertTrue(p_exp["ok"])
            self.assertTrue(pocket_html.is_file())
            html_txt = pocket_html.read_text(encoding="utf-8")
            self.assertIsNone(re.search(r"<(?:script|link)\b[^>]*\b(?:src|href)\s*=\s*['\"]https?://", html_txt, re.I))
            self.assertIn("shm-energy", html_txt)
            self.assertNotIn("onerror=", html_txt.lower(), "Pocket HTML failed to strip SVG onerror handler!")
            self.assertNotIn('alert("xss")', html_txt, "Pocket HTML failed to strip SVG <script> payload!")
            self.assertIn("data:image/svg+xml", html_txt, "Pocket HTML must render SVG via <img src='data:image/svg+xml,...'>")
            self.assertIn("<img", html_txt)

            sync_payload = {
                "schemaVersion": 1,
                "reviews": [
                    {
                        "reviewId": "pkt-rev-001",
                        "propId": "shm-energy",
                        "rating": "good",
                        "confidence": 0.85,
                        "durationMs": 4000,
                        "ts": datetime.now(timezone.utc).isoformat(),
                    }
                ],
            }
            s1 = po.import_pocket_sync(workspace=ws_src, sync_path=sync_payload, kg_dir=kg_src, no_sync=True)
            self.assertTrue(s1["ok"])
            self.assertEqual(s1["importedCount"], 1)
            self.assertEqual(s1["skippedDuplicateCount"], 0)
            rev_count_1 = len([ln for ln in (kg_src / "revlog.jsonl").read_text(encoding="utf-8").splitlines() if ln.strip()])

            # Re-importing exact same reviewId MUST be idempotent (0 imported, 1 duplicate skipped)
            s2 = po.import_pocket_sync(workspace=ws_src, sync_path=sync_payload, kg_dir=kg_src, no_sync=True)
            self.assertTrue(s2["ok"])
            self.assertEqual(s2["importedCount"], 0)
            self.assertEqual(s2["skippedDuplicateCount"], 1)
            rev_count_2 = len([ln for ln in (kg_src / "revlog.jsonl").read_text(encoding="utf-8").splitlines() if ln.strip()])
            self.assertEqual(rev_count_1, rev_count_2)

            # Pure-stdlib QR encoder (matrix, SVG, ASCII)
            qr_url = "http://192.168.1.42:4173/pocket?pair=demo-token-123"
            mat = po.encode_qr_matrix(qr_url)
            self.assertGreaterEqual(len(mat), 21)
            self.assertEqual(len(mat), len(mat[0]))
            svg = po.render_qr_svg(qr_url)
            self.assertIn("<svg", svg)
            self.assertIn("<rect", svg)
            ascii_qr = po.render_qr_ascii(qr_url)
            self.assertGreaterEqual(len(ascii_qr.splitlines()), 10)


class TestLocalStudioServerLive(unittest.TestCase):
    """26. Syntopik v2.0 2-Way Live Local Studio Server: Security, REST Routes, SSE & LAN Pocket Scoping."""

    def test_local_studio_server_security_routes_sse_and_lan_pocket_scoping(self) -> None:
        import syntopik.forks as fk
        import syntopik.server as srv_mod

        fa = load_script_module("falsify_arena.py")
        ct = load_script_module("chapter_tracker.py")

        def _req(
            port: int,
            method: str,
            path: str,
            *,
            headers: dict[str, str] | None = None,
            body: Any = None,
        ) -> tuple[int, dict[str, str], bytes]:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=6.0)
            try:
                hdrs = dict(headers or {})
                raw_body: bytes | None = None
                if body is not None:
                    raw_body = json.dumps(body).encode("utf-8") if not isinstance(body, (bytes, str)) else (
                        body.encode("utf-8") if isinstance(body, str) else body
                    )
                    hdrs.setdefault("Content-Type", "application/json")
                conn.request(method, path, body=raw_body, headers=hdrs)
                resp = conn.getresponse()
                data = resp.read()
                resp_hdrs = {k.lower(): v for k, v in resp.getheaders()}
                return resp.status, resp_hdrs, data
            finally:
                conn.close()

        with tempfile.TemporaryDirectory(prefix="synt-v2-server-") as tmp:
            ws = Path(tmp) / "ws"
            kg = ws / "knowledge-graph"
            ch = ws / "chapters"
            kg.mkdir(parents=True)
            ch.mkdir(parents=True)

            now = datetime.now(timezone.utc)
            d_old = (now - timedelta(days=12)).isoformat()
            d_past = (now - timedelta(days=5)).date().isoformat()
            props = [
                {
                    "id": "p-anc",
                    "subject": "Physics",
                    "chapter": "SHM",
                    "title": "Restoring force law",
                    "kind": "axiom",
                    "status": "solid",
                    "bktMastery": 0.80,
                    "nextReview": d_past,
                    "fsrs": {"stability": 3.0, "difficulty": 4.0, "due": d_old, "last_review": d_old},
                },
                {
                    "id": "p-child",
                    "subject": "Physics",
                    "chapter": "SHM",
                    "title": "SHM angular frequency",
                    "kind": "derivation",
                    "status": "shaky",
                    "bktMastery": 0.45,
                    "nextReview": d_past,
                    "fsrs": {"stability": 2.0, "difficulty": 5.0, "due": d_old, "last_review": d_old},
                },
            ] + [
                {
                    "id": f"p-ov-{i}",
                    "subject": "Physics",
                    "chapter": "SHM",
                    "title": f"Overdue item {i}",
                    "kind": "formula",
                    "status": "solid",
                    "nextReview": d_past,
                    "fsrs": {"stability": 3.0, "difficulty": 5.0, "due": d_old, "last_review": d_old},
                }
                for i in range(12)
            ]
            (kg / "propositions.jsonl").write_text("\n".join(json.dumps(p) for p in props) + "\n", encoding="utf-8")
            (kg / "chains.jsonl").write_text(
                json.dumps({"from": "p-anc", "to": "p-child", "relation": "derives"}) + "\n",
                encoding="utf-8",
            )
            (kg / "mistakes.jsonl").write_text("", encoding="utf-8")

            # Seed tracker, fork, and arena inside /tmp workspace
            buf_ct = _EncodedStringIO()
            with contextlib.redirect_stdout(buf_ct):
                ct.main(["init", "shm", "--title", "SHM", "--subject", "Physics", "--set-active", "--chapters-dir", str(ch), "--kg-dir", str(kg), "--no-sync", "--json"])
                ct.main([
                    "add-topics", "shm",
                    "--json-data", json.dumps([{"id": "t1", "order": 1, "title": "Restoring Law", "level": "basics"}]),
                    "--chapters-dir", str(ch), "--kg-dir", str(kg), "--no-sync", "--json",
                ])

            fork_rec = fk.capture_fork(
                prop_id="p-child",
                prefix_steps=["F = -kx"],
                fork_prompt="Write omega",
                sealed_blunder="k/m",
                sealed_winning_move="sqrt(k/m)",
                refutation_msg="Dimensional check: k/m has units s^-2, not s^-1!",
                verify_spec={"engine": "math"},
                workspace=ws,
                kg_dir=kg,
            )
            arena_rec = fa.create_arena(
                prop_id="p-child",
                domain="code",
                claim="Return max of non-empty list",
                entry="my_max",
                sealed_reference={"code": "def my_max(xs):\n    return max(xs)\n"},
                sealed_mutants=[{"id": "m-zero", "trapTag": "neg_bug", "code": "def my_max(xs):\n    m = 0\n    for x in xs:\n        if x > m: m = x\n    return m\n"}],
                assumption_predicates=["len(args[0]) >= 1"],
                workspace=ws,
                kg_dir=kg,
            )

            # 1. Start default loopback Studio server on ephemeral port=0
            srv = srv_mod.create_server(workspace=ws, host="127.0.0.1", port=0, lan=False)
            t_srv = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
            t_srv.start()
            try:
                port = srv.bound_port

                # (a) GET /api/capabilities
                st, _h, raw = _req(port, "GET", "/api/capabilities")
                self.assertEqual(st, 200)
                cap = json.loads(raw)
                self.assertTrue(cap["live"])
                token = cap["token"]
                self.assertEqual(token, srv.csrf_token)

                # (b) Security gates: missing/wrong X-Syntopik-Token, bad Origin, path traversal -> 403
                st_no_tok, _, _ = _req(port, "POST", "/api/review", body={"propId": "p-child", "rating": "good"})
                self.assertEqual(st_no_tok, 403)
                st_bad_tok, _, _ = _req(
                    port, "POST", "/api/review",
                    headers={"X-Syntopik-Token": "wrong-token"},
                    body={"propId": "p-child", "rating": "good"},
                )
                self.assertEqual(st_bad_tok, 403)
                st_bad_org, _, _ = _req(port, "GET", "/api/state", headers={"Origin": "https://evil.example"})
                self.assertEqual(st_bad_org, 403)
                st_trav, _, _ = _req(port, "GET", "/chapters/../../etc/passwd")
                self.assertEqual(st_trav, 403)

                auth_hdr = {"X-Syntopik-Token": token}

                # (c) GET /api/state
                st_state, _, raw_state = _req(port, "GET", "/api/state")
                self.assertEqual(st_state, 200)
                state_obj = json.loads(raw_state)
                self.assertTrue(state_obj["ok"])
                self.assertEqual(state_obj["hub"]["schemaVersion"], 5)
                self.assertGreaterEqual(state_obj["arenasCount"], 1)

                # (d) SSE /api/events receives ready -> then changed upon POST /api/review
                sse_events: list[dict[str, Any]] = []
                sse_ready = threading.Event()
                sse_changed = threading.Event()

                def _sse_reader() -> None:
                    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5.0)
                    try:
                        c.request("GET", "/api/events")
                        r = c.getresponse()
                        while not sse_changed.is_set():
                            line = r.fp.readline()
                            if not line:
                                break
                            dec = line.decode("utf-8", errors="ignore").strip()
                            if dec.startswith("data:"):
                                ev = json.loads(dec[5:].strip())
                                sse_events.append(ev)
                                if ev.get("type") == "ready":
                                    sse_ready.set()
                                elif ev.get("type") == "changed":
                                    sse_changed.set()
                                    break
                    except Exception:
                        pass
                    finally:
                        c.close()

                t_sse = threading.Thread(target=_sse_reader, daemon=True)
                t_sse.start()
                self.assertTrue(sse_ready.wait(timeout=3.0), "SSE stream did not emit ready event")

                # (e) POST /api/review persists propositions.jsonl + revlog.jsonl + FIRe & wakes SSE!
                st_rev, _, raw_rev = _req(
                    port, "POST", "/api/review", headers=auth_hdr,
                    body={"propId": "p-child", "rating": "good", "confidence": 0.9},
                )
                self.assertEqual(st_rev, 200, raw_rev)
                rev_json = json.loads(raw_rev)
                self.assertTrue(rev_json["ok"])
                self.assertGreaterEqual(rev_json["fireRefreshedCount"], 1)
                self.assertTrue((kg / "revlog.jsonl").is_file())
                self.assertTrue(sse_changed.wait(timeout=3.0), "SSE stream did not receive changed event on POST /api/review")
                t_sse.join(timeout=2.0)

                # (f) POST /api/recover
                st_rec, _, raw_rec = _req(port, "POST", "/api/recover", headers=auth_hdr, body={"dailyCap": 4, "days": 5})
                self.assertEqual(st_rec, 200, raw_rec)
                rec_json = json.loads(raw_rec)
                self.assertTrue(rec_json["applied"])
                self.assertGreaterEqual(rec_json["rescheduledCount"], 1)

                # (g) POST /api/exam/submit -> 3-bucket autopsy & persists attempts/mistakes
                opts = [{"id": "A", "text": "A"}, {"id": "B", "text": "B"}]
                st_ex, _, raw_ex = _req(
                    port, "POST", "/api/exam/submit", headers=auth_hdr,
                    body={
                        "examSlug": "srv-mock",
                        "spec": {
                            "examSlug": "srv-mock",
                            "title": "Server Mock",
                            "durationMinutes": 10,
                            "questions": [
                                {"id": "Q1", "type": "single_correct", "subject": "Physics", "chapter": "SHM", "propId": "p-child", "stem": "Q1", "options": opts, "answer": "A"},
                                {"id": "Q2", "type": "single_correct", "subject": "Physics", "chapter": "SHM", "trapCategory": "misconception", "stem": "Q2", "options": opts, "answer": "B"},
                            ],
                        },
                        "attempt": {
                            "examSlug": "srv-mock",
                            "timeSpentSec": 90,
                            "responses": {
                                "Q1": {"answer": "A", "status": "answered", "timeSpentSec": 40},
                                "Q2": {"answer": "A", "status": "answered", "timeSpentSec": 50},
                            },
                        },
                    },
                )
                self.assertEqual(st_ex, 200, raw_ex)
                ex_json = json.loads(raw_ex)
                self.assertTrue(ex_json["ok"])
                self.assertIn("knowledge", ex_json["autopsy"])
                self.assertIn("execution", ex_json["autopsy"])
                self.assertIn("time_sink", ex_json["autopsy"])
                self.assertTrue((kg / "exam-attempts.jsonl").is_file())
                self.assertTrue((kg / "mistakes.jsonl").read_text(encoding="utf-8").strip())

                # (h) POST /api/tracker/topic & POST /api/tracker/milestone
                st_tp, _, raw_tp = _req(
                    port, "POST", "/api/tracker/topic", headers=auth_hdr,
                    body={"slug": "shm", "topicId": "t1", "status": "mastered"},
                )
                self.assertEqual(st_tp, 200, raw_tp)
                self.assertEqual(json.loads(raw_tp)["status"], "mastered")

                st_ms, _, raw_ms = _req(
                    port, "POST", "/api/tracker/milestone", headers=auth_hdr,
                    body={"slug": "shm", "key": "probed", "value": True},
                )
                self.assertEqual(st_ms, 200, raw_ms)
                self.assertTrue(json.loads(raw_ms)["milestones"]["probed"])

                # (i) POST /api/forks/check & POST /api/arena/submit
                st_fk, _, raw_fk = _req(
                    port, "POST", "/api/forks/check", headers=auth_hdr,
                    body={"forkId": fork_rec["id"], "move": "k/m", "latencyMs": 5000},
                )
                self.assertEqual(st_fk, 200, raw_fk)
                fk_json = json.loads(raw_fk)
                self.assertTrue(fk_json["repeatedBlunder"])
                self.assertIsNone(fk_json["winningMove"])

                st_ar, _, raw_ar = _req(
                    port, "POST", "/api/arena/submit", headers=auth_hdr,
                    body={"arenaId": arena_rec["id"], "input": [[-5, -2]]},
                )
                self.assertEqual(st_ar, 200, raw_ar)
                ar_json = json.loads(raw_ar)
                self.assertIn("m-zero", ar_json["killed"])
                self.assertEqual(ar_json["survivedCount"], 0)

                # (j) Wave 3A hardening (#172): /demos/ jail, forged Host, unknown ID 404, downloadable pack export
                st_leak_srv, _, _ = _req(port, "GET", "/demos/syntopik/server.py")
                self.assertIn(st_leak_srv, (403, 404))
                st_leak_kg, _, _ = _req(port, "GET", "/demos/knowledge-graph/propositions.jsonl")
                self.assertIn(st_leak_kg, (403, 404))
                st_cbt, _, raw_cbt = _req(port, "GET", "/demos/exam-cbt-template.html")
                self.assertEqual(st_cbt, 200)
                self.assertIn(b"<!DOCTYPE html>", raw_cbt)

                st_bad_host, _, _ = _req(port, "GET", "/api/capabilities", headers={"Host": "[::1]evil.com"})
                self.assertEqual(st_bad_host, 403)

                st_fk_404, _, _ = _req(
                    port, "POST", "/api/forks/check", headers=auth_hdr,
                    body={"forkId": "fork-does-not-exist", "move": "1"},
                )
                self.assertEqual(st_fk_404, 404)
                st_ar_404, _, _ = _req(
                    port, "POST", "/api/arena/submit", headers=auth_hdr,
                    body={"arenaId": "arena-does-not-exist", "input": [1]},
                )
                self.assertEqual(st_ar_404, 404)

                st_pk_exp, _, raw_pk_exp = _req(
                    port, "POST", "/api/pack/export", headers=auth_hdr,
                    body={"target": "SHM"},
                )
                self.assertEqual(st_pk_exp, 200, raw_pk_exp)
                pk_json = json.loads(raw_pk_exp)
                self.assertTrue(str(pk_json.get("url", "")).startswith("/chapters/"), pk_json)
                st_pk_dl, _, raw_pk_dl = _req(port, "GET", pk_json["url"])
                self.assertEqual(st_pk_dl, 200)
                self.assertTrue(raw_pk_dl.startswith(b"PK\x03\x04"))
            finally:
                srv.shutdown()
                srv.server_close()
                t_srv.join(timeout=3.0)

            # 2. Opt-in `--lan` server: one-time QR pair cookie strictly scoped to `/pocket`, `/api/pocket/state`, `POST /api/review/batch`
            srv_lan = srv_mod.create_server(workspace=ws, host="127.0.0.1", port=0, lan=True)
            t_lan = threading.Thread(target=srv_lan.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
            t_lan.start()
            try:
                lport = srv_lan.bound_port
                pair_tok = srv_lan.pair_token
                self.assertTrue(pair_tok)
                lan_host = f"192.168.1.99:{lport}"

                # Unpaired LAN client blocked on /api/pocket/state
                st_unp, _, _ = _req(lport, "GET", "/api/pocket/state", headers={"Host": lan_host})
                self.assertEqual(st_unp, 403)

                # One-time pair token -> 302 + HttpOnly SameSite=Lax cookie
                st_pair, hdrs_pair, _ = _req(lport, "GET", f"/pocket?pair={pair_tok}", headers={"Host": lan_host})
                self.assertEqual(st_pair, 302)
                set_cookie = hdrs_pair.get("set-cookie", "")
                self.assertIn("syntopik_pocket_session=", set_cookie)
                self.assertIn("HttpOnly", set_cookie)
                cookie_pair = set_cookie.split(";", 1)[0]

                # Second reuse of one-time pair token MUST be rejected (403)
                st_reuse, _, _ = _req(lport, "GET", f"/pocket?pair={pair_tok}", headers={"Host": lan_host})
                self.assertEqual(st_reuse, 403)

                # POST /api/export/pocket rotates a fresh valid pair token after the first is consumed!
                st_rot, _, raw_rot = _req(
                    lport, "POST", "/api/export/pocket",
                    headers={"X-Syntopik-Token": srv_lan.csrf_token},
                    body={"limit": 10},
                )
                self.assertEqual(st_rot, 200, raw_rot)
                rot_json = json.loads(raw_rot)
                rot_url = str(rot_json.get("lanUrl") or "")
                self.assertIn("pair=", rot_url)
                rot_tok = urllib.parse.parse_qs(urllib.parse.urlsplit(rot_url).query).get("pair", [""])[0]
                self.assertTrue(rot_tok and rot_tok != pair_tok)
                st_pair2, hdrs_pair2, _ = _req(lport, "GET", f"/pocket?pair={rot_tok}", headers={"Host": lan_host})
                self.assertEqual(st_pair2, 302)
                self.assertIn("syntopik_pocket_session=", hdrs_pair2.get("set-cookie", ""))

                # Valid LAN cookie succeeds ONLY on the 3 Pocket routes
                st_pkt, _, raw_pkt = _req(lport, "GET", "/pocket", headers={"Host": lan_host, "Cookie": cookie_pair})
                self.assertEqual(st_pkt, 200)
                self.assertIn(b"<!DOCTYPE html>", raw_pkt)

                st_ps, _, raw_ps = _req(lport, "GET", "/api/pocket/state", headers={"Host": lan_host, "Cookie": cookie_pair})
                self.assertEqual(st_ps, 200)
                self.assertTrue(json.loads(raw_ps)["ok"])

                st_rb, _, raw_rb = _req(
                    lport, "POST", "/api/review/batch",
                    headers={"Host": lan_host, "Origin": f"http://{lan_host}", "Cookie": cookie_pair},
                    body={"reviews": [{"reviewId": "lan-rev-1", "propId": "p-child", "rating": "good"}]},
                )
                self.assertEqual(st_rb, 200, raw_rb)
                self.assertTrue(json.loads(raw_rb)["ok"])

                # STRICT SCOPE GUARD: LAN cookie MUST get 403 on `/api/state` and `POST /api/import`!
                st_blk_state, _, _ = _req(lport, "GET", "/api/state", headers={"Host": lan_host, "Cookie": cookie_pair})
                self.assertEqual(st_blk_state, 403)
                st_blk_imp, _, _ = _req(
                    lport, "POST", "/api/import",
                    headers={"Host": lan_host, "Origin": f"http://{lan_host}", "Cookie": cookie_pair},
                    body={"target": "https://example.org"},
                )
                self.assertEqual(st_blk_imp, 403)
            finally:
                srv_lan.shutdown()
                srv_lan.server_close()
                t_lan.join(timeout=3.0)


class TestPackagingAttachAndAmbientMcp(unittest.TestCase):
    """27. Syntopik v2.0 Packaging Manifests, Non-Destructive `syntopik attach`, & 17-Tool Ambient/Parsons MCP."""

    def test_packaging_manifests_cli_and_non_destructive_git_attach(self) -> None:
        import syntopik.cli as cli_mod

        # 1. Validate pyproject.toml (zero required deps, Python >=3.12, hatchling force-include)
        pyp = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertEqual(pyp["project"]["name"], "syntopik")
        self.assertEqual(pyp["project"]["version"], "3.2.0")
        self.assertEqual(pyp["project"]["dependencies"], [])
        self.assertEqual(pyp["project"]["scripts"]["syntopik"], "syntopik.cli:main")
        force_inc = pyp["tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"]
        self.assertIn("scripts", force_inc)

        # 2. Validate package.json + bin/syntopik.mjs --help / --version
        pkg = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
        self.assertEqual(pkg["name"], "syntopik")
        self.assertEqual(pkg["version"], "3.2.0")
        self.assertEqual(pkg["dependencies"], {})
        self.assertEqual(pkg["bin"]["syntopik"], "./bin/syntopik.mjs")

        h_proc = run_cli(["node", str(ROOT / "bin" / "syntopik.mjs"), "--help"])
        self.assertEqual(h_proc.returncode, 0, h_proc.stderr)
        self.assertIn("syntopik", h_proc.stdout.lower())
        v_proc = run_cli(["node", str(ROOT / "bin" / "syntopik.mjs"), "--version"])
        self.assertEqual(v_proc.returncode, 0, v_proc.stderr)
        self.assertIn("3.2.0", v_proc.stdout)

        # 3. Validate .claude-plugin/{marketplace,plugin,hooks}.json & gemini-extension.json
        cp_mkt = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
        cp_plg = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        cp_hks = json.loads((ROOT / ".claude-plugin" / "hooks.json").read_text(encoding="utf-8"))
        gem_ext = json.loads((ROOT / "gemini-extension.json").read_text(encoding="utf-8"))

        self.assertEqual(cp_mkt["name"], "syntopik")
        self.assertEqual(cp_mkt["metadata"]["version"], "3.2.0")
        self.assertEqual(cp_plg["version"], "3.2.0")
        self.assertTrue(cp_plg["skills"].startswith("./"))
        self.assertIn("SessionStart", cp_hks["hooks"])
        self.assertIn("PostToolUse", cp_hks["hooks"])
        self.assertEqual(gem_ext["name"], "syntopik")
        self.assertEqual(gem_ext["version"], "3.2.0")
        self.assertEqual(gem_ext["contextFileName"], "GEMINI.md")

        # 4. Non-destructive `syntopik attach` on dummy /tmp git repos (Python CLI + Node CLI)
        host_agents = "# Existing Host Repo AGENTS.md — DO NOT MODIFY\n"
        host_claude = "# Existing Host Repo CLAUDE.md — DO NOT MODIFY\n"
        host_gitignore = "*.log\ndist/\n"

        with tempfile.TemporaryDirectory(prefix="synt-v2-attach-") as tmp:
            for mode_name in ("python", "node"):
                repo = Path(tmp) / f"host-{mode_name}"
                repo.mkdir(parents=True)
                self.assertEqual(subprocess.run(["git", "init", "-q"], cwd=str(repo), check=False).returncode, 0)
                subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=str(repo), check=True)
                subprocess.run(["git", "config", "user.name", "Syntopik Test"], cwd=str(repo), check=True)
                (repo / "AGENTS.md").write_text(host_agents, encoding="utf-8")
                (repo / "CLAUDE.md").write_text(host_claude, encoding="utf-8")
                (repo / ".gitignore").write_text(host_gitignore, encoding="utf-8")
                subprocess.run(["git", "add", "."], cwd=str(repo), check=True)
                subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=str(repo), check=True)

                if mode_name == "python":
                    att = cli_mod.attach_workspace(repo, harness="auto", vault="local")
                    self.assertTrue(att["ok"])
                else:
                    p_att = run_cli(["node", str(ROOT / "bin" / "syntopik.mjs"), "attach", str(repo), "--vault", "local", "--json"])
                    self.assertEqual(p_att.returncode, 0, p_att.stderr)
                    self.assertTrue(json.loads(p_att.stdout)["ok"])

                # Host files 100% preserved byte-for-byte & `git status --short` completely EMPTY!
                self.assertEqual((repo / "AGENTS.md").read_text(encoding="utf-8"), host_agents)
                self.assertEqual((repo / "CLAUDE.md").read_text(encoding="utf-8"), host_claude)
                self.assertEqual((repo / ".gitignore").read_text(encoding="utf-8"), host_gitignore)
                st_out = subprocess.check_output(["git", "status", "--short"], cwd=str(repo), text=True)
                self.assertEqual(st_out.strip(), "", f"{mode_name} attach polluted git status: {st_out!r}")
                excl = (repo / ".git" / "info" / "exclude").read_text(encoding="utf-8")
                self.assertIn(".syntopik/", excl)
                self.assertTrue((repo / ".syntopik" / "knowledge-graph" / "propositions.jsonl").is_file())

            # 5. Wave 3A CLI/Attach parity (#172 P0.5 & P1.9): `syntopik.cli sync --json` strict JSON & Node `attach --harness gemini`
            gem_repo = Path(tmp) / "host-gemini-node"
            gem_repo.mkdir(parents=True)
            self.assertEqual(subprocess.run(["git", "init", "-q"], cwd=str(gem_repo), check=False).returncode, 0)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=str(gem_repo), check=True)
            subprocess.run(["git", "config", "user.name", "Syntopik Test"], cwd=str(gem_repo), check=True)
            (gem_repo / "README.md").write_text("# Host\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=str(gem_repo), check=True)
            subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=str(gem_repo), check=True)

            p_gem = run_cli([
                "node", str(ROOT / "bin" / "syntopik.mjs"),
                "attach", str(gem_repo), "--harness", "gemini", "--vault", "local", "--json",
            ])
            self.assertEqual(p_gem.returncode, 0, p_gem.stderr)
            self.assertTrue(json.loads(p_gem.stdout)["ok"])
            gem_settings = gem_repo / ".gemini" / "settings.json"
            self.assertTrue(gem_settings.is_file(), "Node attach --harness gemini did not write .gemini/settings.json")
            self.assertIn("syntopik-studio", json.loads(gem_settings.read_text(encoding="utf-8")).get("mcpServers", {}))
            self.assertEqual(subprocess.check_output(["git", "status", "--short"], cwd=str(gem_repo), text=True).strip(), "")

            sync_ws = gem_repo / ".syntopik"
            p_sync = run_cli([
                sys.executable, "-B", "-m", "syntopik.cli",
                "sync", "--workspace", str(sync_ws), "--json",
            ])
            self.assertEqual(p_sync.returncode, 0, p_sync.stderr)
            sync_parsed = json.loads(p_sync.stdout)
            self.assertTrue(sync_parsed.get("ok"), sync_parsed)

    def test_mcp_stdio_v2_17_tools_ambient_and_parsons_zero_leak(self) -> None:
        server_mjs = ROOT / "mcp-app" / "mlh-studio" / "dist" / "server.mjs"
        self.assertTrue(server_mjs.is_file())

        with tempfile.TemporaryDirectory(prefix="synt-v2-mcp-") as tmp:
            t = Path(tmp)
            ws = t / "ws"  # Deliberately NO ws/scripts/ -> tests PACKAGE_ROOT fallback!
            kg = ws / "knowledge-graph"
            ch = ws / "chapters"
            kg.mkdir(parents=True)
            ch.mkdir(parents=True)
            (kg / "propositions.jsonl").write_text(
                json.dumps({
                    "id": "cs-base-heap",
                    "subject": "CS",
                    "chapter": "Graphs",
                    "title": "Binary min-heap extract-min invariant",
                    "kind": "axiom",
                    "status": "solid",
                    "bktMastery": 0.85,
                    "nextReview": "2020-01-01",
                }) + "\n",
                encoding="utf-8",
            )
            (kg / "chains.jsonl").write_text("", encoding="utf-8")
            (kg / "mistakes.jsonl").write_text("", encoding="utf-8")
            data_dir = t / "mcp-data"

            secret_sol = "SECRET_PARSONS_SOLUTION_9182"
            reqs = [
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},  # Terminal non-UI mode!
                        "clientInfo": {"name": "syntopik-v2-unittest", "version": "2.0"},
                    },
                },
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {
                        "name": "synt_capture_concept",
                        "arguments": {
                            "id": "cs-dijkstra-relax",
                            "title": "Dijkstra greedy edge relaxation invariant",
                            "statement": "With non-negative weights, popped vertex distance is final.",
                            "subject": "CS",
                            "chapter": "Graphs",
                            "cuePrompt": "Why do negative edges break Dijkstra?",
                            "cueAnswer": "A later negative edge can lower an already-popped vertex distance.",
                            "premises": ["cs-base-heap"],
                        },
                    },
                },
                {
                    "jsonrpc": "2.0",
                    "id": 4,
                    "method": "tools/call",
                    "params": {
                        "name": "synt_capture_concept",
                        "arguments": {
                            "title": "Dijkstra greedy edge relaxation invariant",
                            "statement": "Duplicate title capture should deduplicate.",
                            "subject": "CS",
                            "chapter": "Graphs",
                        },
                    },
                },
                {
                    "jsonrpc": "2.0",
                    "id": 5,
                    "method": "tools/call",
                    "params": {
                        "name": "synt_log_review",
                        "arguments": {
                            "propId": "cs-dijkstra-relax",
                            "rating": "good",
                            "confidence": 85,
                            "durationMs": 3200,
                        },
                    },
                },
                {
                    "jsonrpc": "2.0",
                    "id": 6,
                    "method": "tools/call",
                    "params": {
                        "name": "synt_get_frontier",
                        "arguments": {"limit": 10, "compress": True},
                    },
                },
                {
                    "jsonrpc": "2.0",
                    "id": 7,
                    "method": "tools/call",
                    "params": {
                        "name": "synt_practice_card",
                        "arguments": {
                            "action": "pose",
                            "problemId": "parsons-dijkstra-1",
                            "mode": "parsons",
                            "statement": "Order the valid Dijkstra relaxation steps (omit the distractor).",
                            "blocks": [
                                {"id": "blk-pop", "code": "d, u = heappop(pq)", "indent": 0},
                                {"id": "blk-skip", "code": "if d > dist[u]: continue", "indent": 0},
                                {"id": "blk-dist", "code": "if d < dist[u]: break", "indent": 0, "isDistractor": True},
                            ],
                            "canonicalOrder": ["blk-pop", "blk-skip"],
                            "modelSolution": secret_sol,
                        },
                    },
                },
                {
                    "jsonrpc": "2.0",
                    "id": 8,
                    "method": "tools/call",
                    "params": {
                        "name": "synt_practice_card",
                        "arguments": {
                            "action": "conclude",
                            "problemId": "parsons-dijkstra-1",
                            "outcome": "solved",
                            "verdict": "Exact stale-entry guard chosen.",
                            "rung": None,
                        },
                    },
                },
            ]

            proc = run_cli(
                ["node", str(server_mjs), f"--workspace={ws}", f"--data-dir={data_dir}"],
                stdin_text="\n".join(json.dumps(r) for r in reqs) + "\n",
                timeout=15.0,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            by_id = {
                int(m["id"]): m
                for m in (json.loads(ln) for ln in proc.stdout.splitlines() if ln.strip())
                if "id" in m
            }

            # 1. Exact 23 tools & single com.google.antigravity/ui entrypoint ONLY on synt_lesson_studio
            tools = by_id[2]["result"]["tools"]
            self.assertEqual(len(tools), 23)
            tool_names = {tl["name"] for tl in tools}
            for ambient in (
                "synt_capture_concept",
                "synt_get_frontier",
                "synt_log_review",
                "synt_next_move",
                "synt_whiteboard_step",
                "synt_readiness",
                "synt_interleave",
            ):
                self.assertIn(ambient, tool_names)
            ep_tools = [
                tl["name"]
                for tl in tools
                if tl.get("_meta", {}).get("com.google.antigravity/ui", {}).get("entrypoints")
            ]
            self.assertEqual(ep_tools, ["synt_lesson_studio"])

            # 2. synt_capture_concept creates + deduplicates
            cap1 = by_id[3]["result"]
            self.assertFalse(cap1.get("isError", False), cap1)
            self.assertEqual(cap1["structuredContent"]["id"], "cs-dijkstra-relax")
            self.assertTrue(cap1["structuredContent"]["cueAdded"])
            self.assertTrue(cap1["structuredContent"]["chainAdded"])

            cap2 = by_id[4]["result"]
            self.assertFalse(cap2.get("isError", False), cap2)
            self.assertTrue(cap2["structuredContent"]["deduplicated"])

            # 3. synt_log_review persists revlog.jsonl
            rev_r = by_id[5]["result"]
            self.assertFalse(rev_r.get("isError", False), rev_r)
            self.assertTrue((kg / "revlog.jsonl").is_file())

            # 4. synt_get_frontier returns due items & calibration summary
            fr_r = by_id[6]["result"]
            self.assertFalse(fr_r.get("isError", False), fr_r)
            self.assertEqual(fr_r["structuredContent"]["kind"], "synt_frontier")

            # 5. Parsons pose in terminal mode: ZERO leak of modelSolution, canonicalOrder, or isDistractor!
            pose_r = by_id[7]["result"]
            self.assertFalse(pose_r.get("isError", False), pose_r)
            pose_dump = json.dumps(pose_r)
            self.assertNotIn(secret_sol, pose_dump, "Parsons pose leaked modelSolution!")
            self.assertNotIn("canonicalOrder", pose_dump, "Parsons pose leaked canonicalOrder!")
            self.assertNotIn("isDistractor", pose_dump, "Parsons pose leaked isDistractor!")
            self.assertEqual(len(pose_r["structuredContent"]["blocks"]), 3)
            self.assertTrue(pose_r["structuredContent"]["terminalFallback"])

            # 6. Parsons conclude reveals canonicalOrder & modelSolution verbatim
            conc_r = by_id[8]["result"]
            self.assertFalse(conc_r.get("isError", False), conc_r)
            self.assertEqual(conc_r["structuredContent"]["modelSolution"], secret_sol)
            self.assertEqual(conc_r["structuredContent"]["canonicalOrder"], ["blk-pop", "blk-skip"])


class TestV2ContextDietAndHygiene(unittest.TestCase):
    """28. Syntopik v2.0 Context-Budget Diet, 17-Skill YAML Budget, Zero Leaks/Pycache & Live SHM Integrity."""

    def test_v2_context_diet_skills_hygiene_no_pycache_and_shm_intact(self) -> None:
        # 1. Combined AGENTS.md + GEMINI.md <= 9,500 B (v3.0 unified ceiling)
        agents_raw = (ROOT / "AGENTS.md").read_bytes()
        gemini_raw = (ROOT / "GEMINI.md").read_bytes()
        combined = len(agents_raw) + len(gemini_raw)
        self.assertLessEqual(combined, 9500, f"AGENTS.md + GEMINI.md ({combined} B) exceeds 9,500 B v3.0 unified ceiling")
        self.assertFalse(agents_raw.decode("utf-8").lstrip().startswith("---"))
        self.assertFalse(gemini_raw.decode("utf-8").lstrip().startswith("---"))

        # 2. Exact 17/17 valid skills with total YAML description bytes < 3,600 B
        skill_files = sorted(SKILLS_DIR.glob("*/SKILL.md"))
        self.assertEqual(len(skill_files), 17)
        total_desc_bytes = 0
        for sk in skill_files:
            fm = parse_yaml_frontmatter(sk.read_text(encoding="utf-8"))
            self.assertIsNotNone(fm, f"{sk} missing YAML frontmatter")
            assert fm is not None
            self.assertEqual(fm.get("name"), sk.parent.name)
            desc = fm.get("description", "")
            self.assertGreaterEqual(len(desc), 15)
            self.assertNotIn("<", desc)
            self.assertNotIn(">", desc)
            total_desc_bytes += len(desc.encode("utf-8"))
        self.assertLess(total_desc_bytes, 3600, f"17-skill YAML descriptions ({total_desc_bytes} B) >= 3,600 B ceiling")

        # 3. Zero /Users/ (or personal home) leaks across tracked + non-ignored repo files
        personal_re = re.compile(
            r"(?:/Users/[A-Za-z0-9._-]+(?:/|[\"'\s]|$)|/home/[A-Za-z0-9._-]+(?:/|[\"'\s]|$)|C:\\Users\\[A-Za-z0-9._-]+\\)"
        )
        tracked = run_cli(["git", "ls-files"]).stdout.splitlines()
        untracked = run_cli(["git", "ls-files", "--others", "--exclude-standard"]).stdout.splitlines()
        leaks: list[str] = []
        for rel in sorted(set(tracked + untracked)):
            if not rel or rel.startswith((".venv/", ".git/", "node_modules/", "mcp-app/mlh-studio/node_modules/")):
                continue
            p = ROOT / rel
            if not p.is_file() or p.is_symlink():
                continue
            raw = p.read_bytes()
            if b"\x00" in raw[:4096]:
                continue
            if personal_re.search(raw.decode("utf-8", errors="ignore")):
                leaks.append(rel)
        self.assertEqual(leaks, [], f"Personal path leaks detected: {leaks}")

        # 4. Zero __pycache__ directories anywhere outside .venv / node_modules
        stray_pycache: list[str] = []
        for dirpath, dirnames, _ in os.walk(ROOT):
            rel_dir = Path(dirpath).relative_to(ROOT)
            parts = rel_dir.parts
            if parts and parts[0] in (".venv", ".git", "node_modules"):
                dirnames[:] = []
                continue
            if "node_modules" in parts:
                dirnames[:] = []
                continue
            if "__pycache__" in dirnames:
                stray_pycache.append(str(rel_dir / "__pycache__"))
        self.assertEqual(stray_pycache, [], f"Stray __pycache__ directories found outside .venv: {stray_pycache}")

        # 5. Live SHM files never tracked in git; when present locally, verify 4/4 live SHM files + 4/4 extracted dirs intact
        live_shm_files = [
            ROOT / "chapters" / "shm-tracker.json",
            ROOT / "knowledge-graph" / "propositions.jsonl",
            ROOT / "knowledge-graph" / "mistakes.jsonl",
            ROOT / "knowledge-graph" / "review-queue.md",
        ]
        tracked_set = set(tracked)
        for lf in live_shm_files:
            self.assertNotIn(str(lf.relative_to(ROOT)), tracked_set)

        if live_shm_files[0].is_file():
            for lf in live_shm_files:
                self.assertTrue(lf.is_file() and lf.stat().st_size > 0, f"Live SHM file missing or empty: {lf}")
            shm_tracker = json.loads(live_shm_files[0].read_text(encoding="utf-8"))
            self.assertEqual(shm_tracker.get("slug"), "shm")
            self.assertEqual(len(shm_tracker.get("topics", [])), 10)
            self.assertEqual(len(shm_tracker.get("questions", [])), 172)
            shm_props = [
                json.loads(ln)
                for ln in live_shm_files[1].read_text(encoding="utf-8").splitlines()
                if ln.strip()
            ]
            self.assertGreaterEqual(len(shm_props), 3)
            shm_extracted = ROOT / "sources" / "_extracted"
            if shm_extracted.is_dir():
                for dname in (
                    "426-simple-harmonic-motion-shm-simple-harmonic-motion-module",
                    "eduniti-pyqs",
                    "shm",
                    "shm-final",
                ):
                    dp_dir = shm_extracted / dname
                    self.assertTrue(dp_dir.is_dir() and any(dp_dir.iterdir()), f"Live SHM extracted dir missing: {dp_dir}")


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


class TestV3ServerHooksMcpAndWindowsPortability(unittest.TestCase):
    """31. Syntopik v3.0 Studio Server Endpoints, Hooks/sync-graph v5, Fixed CLI Flags, --vault local, Windows Configs & MCP v3."""

    def test_v3_studio_server_endpoints_zero_leak_csrf_and_lan_isolation(self) -> None:
        import syntopik.forks as fk
        import syntopik.server as srv_mod
        import syntopik.whiteboard as wb

        df = load_script_module("derivation_forge.py")
        fa = load_script_module("falsify_arena.py")

        def _req(
            port: int,
            method: str,
            path: str,
            *,
            headers: dict[str, str] | None = None,
            body: Any = None,
        ) -> tuple[int, dict[str, str], bytes]:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=6.0)
            try:
                hdrs = dict(headers or {})
                raw = None
                if body is not None:
                    raw = json.dumps(body).encode("utf-8") if isinstance(body, (dict, list)) else body
                    hdrs.setdefault("Content-Type", "application/json")
                conn.request(method, path, body=raw, headers=hdrs)
                resp = conn.getresponse()
                data = resp.read()
                return resp.status, {k.lower(): v for k, v in resp.getheaders()}, data
            finally:
                conn.close()

        with tempfile.TemporaryDirectory(prefix="synt-v3-srv-") as tmp:
            ws = Path(tmp) / "ws"
            kg = ws / "knowledge-graph"
            ch = ws / "chapters"
            kg.mkdir(parents=True)
            ch.mkdir(parents=True)

            (kg / "propositions.jsonl").write_text(
                "\n".join([
                    json.dumps({
                        "id": "p-root", "subject": "Physics", "chapter": "SHM",
                        "title": "Restoring force", "kind": "axiom", "status": "shaky", "bktMastery": 0.4,
                        "boundaryConditions": ["linear spring"], "antiPatterns": ["ignores minus sign"],
                    }),
                    json.dumps({
                        "id": "p-leaf", "subject": "Physics", "chapter": "SHM",
                        "title": "SHM period", "kind": "theorem", "status": "shaky", "bktMastery": 0.45,
                        "boundaryConditions": ["small amplitude"], "antiPatterns": ["uses g instead of k/m"],
                    }),
                ]) + "\n",
                encoding="utf-8",
            )
            (kg / "chains.jsonl").write_text(
                json.dumps({"premises": ["p-root"], "conclusion": "p-leaf", "relation": "derives"}) + "\n",
                encoding="utf-8",
            )

            # Seed secrets across forks, arenas, decisions, derivations, and locked whiteboard gate
            canary_fork_win = "SECRET_WIN_MOVE_7741"
            canary_fork_blunder = "SECRET_BLUNDER_7742"
            canary_mutant = "SECRET_MUTANT_CODE_7743"
            canary_wb_ans = "SECRET_GATE_ANS_7744"
            canary_wb_body = "SECRET_GATE_BODY_7745"
            canary_deriv_expr = "2*pi*sqrt(m/k)"

            fk.capture_fork(
                prop_id="p-leaf",
                prefix_steps=["Spring mass m, stiffness k"],
                fork_prompt="Write angular frequency squared.",
                sealed_blunder=canary_fork_blunder,
                sealed_winning_move=canary_fork_win,
                refutation_msg="Check units",
                kg_dir=kg,
                workspace=ws,
            )
            fa.create_arena(
                prop_id="p-leaf",
                domain="code",
                claim="Return max of list",
                entry="my_max",
                sealed_reference={"code": "def my_max(xs):\n    return max(xs)\n"},
                sealed_mutants=[{"id": "m1", "trapTag": "neg", "code": f"# {canary_mutant}\ndef my_max(xs):\n    return 0\n"}],
                assumption_predicates=["len(args[0]) >= 1"],
                workspace=ws,
                kg_dir=kg,
            )
            (kg / "decisions.jsonl").write_text(
                json.dumps({
                    "id": "dec-1", "propId": "p-leaf", "prompt": "Pick first move",
                    "killCue": "SECRET_KILL_CUE_7746", "sealedAnswer": "SECRET_DEC_ANS_7747",
                }) + "\n",
                encoding="utf-8",
            )
            df.create_derivation(
                {
                    "id": "d-srv-1", "propId": "p-leaf", "chapter": "SHM", "title": "SHM T", "domain": "Physics",
                    "steps": [
                        {"id": "a1", "label": "Axiom", "expr": "a = -w**2*x", "role": "axiom", "dependsOn": []},
                        {"id": "s1", "label": "Period", "expr": canary_deriv_expr, "role": "theorem", "dependsOn": ["a1"]},
                    ],
                },
                kg_dir=kg,
                workspace=ws,
            )
            wb.push_frame(
                kg,
                {
                    "id": "wb-gate-1",
                    "kind": "prediction_gate",
                    "title": "Locked Gate",
                    "prompt": "Predict before unlock",
                    "expectedAnswer": canary_wb_ans,
                    "revealedBody": canary_wb_body,
                },
            )

            srv = srv_mod.create_server(workspace=ws, host="127.0.0.1", port=0, lan=False)
            t_srv = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
            t_srv.start()
            try:
                port = srv.bound_port
                auth = {"X-Syntopik-Token": srv.csrf_token}

                # 1. GET /api/state -> schemaVersion == 5 & ZERO leaked secrets!
                st_st, _, raw_st = _req(port, "GET", "/api/state")
                self.assertEqual(st_st, 200)
                st_text = raw_st.decode("utf-8")
                state_obj = json.loads(st_text)
                self.assertEqual(state_obj["hub"]["schemaVersion"], 5)
                for secret in (canary_fork_win, canary_fork_blunder, canary_mutant, canary_wb_ans, canary_wb_body, "SECRET_DEC_ANS_7747"):
                    self.assertNotIn(secret, st_text, f"GET /api/state leaked secret: {secret}")

                # 2. CSRF 403 enforcement on v3 POST/PATCH routes
                st_csrf1, _, _ = _req(port, "POST", "/api/profile/calibrate", body={"entryRamp": "balanced"})
                self.assertEqual(st_csrf1, 403)
                st_csrf2, _, _ = _req(port, "PATCH", "/api/profile/override", body={"maxProseWords": 110})
                self.assertEqual(st_csrf2, 403)
                st_csrf3, _, _ = _req(port, "POST", "/api/whiteboard/push", body={"kind": "summary", "body": "x"})
                self.assertEqual(st_csrf3, 403)

                # 3. Profile GET / calibrate / PATCH override
                st_p0, _, raw_p0 = _req(port, "GET", "/api/profile")
                self.assertEqual(st_p0, 200)
                self.assertTrue(json.loads(raw_p0)["ok"])

                st_cal, _, raw_cal = _req(
                    port, "POST", "/api/profile/calibrate", headers=auth,
                    body={"entryRamp": "formalism_first", "challengeAppetite": "balanced", "proseDensity": "terse"},
                )
                self.assertEqual(st_cal, 200, raw_cal)
                self.assertTrue(json.loads(raw_cal)["profile"]["calibration"]["completed"])

                st_ov, _, raw_ov = _req(
                    port, "PATCH", "/api/profile/override", headers=auth,
                    body={"scaffoldingMode": "standard", "maxProseWords": 120},
                )
                self.assertEqual(st_ov, 200, raw_ov)
                self.assertEqual(json.loads(raw_ov)["compiledPolicy"]["maxProseWords"], 120)

                # 4. /api/next-move, /api/root-cause, /api/evidence
                st_nm, _, raw_nm = _req(port, "GET", "/api/next-move")
                self.assertEqual(st_nm, 200)
                self.assertIn("recommendedMode", json.loads(raw_nm))

                st_rc, _, raw_rc = _req(port, "GET", "/api/root-cause?nodeId=p-leaf")
                self.assertEqual(st_rc, 200)
                rc_j = json.loads(raw_rc)
                self.assertTrue(rc_j["ok"])
                self.assertEqual(rc_j["localization"]["topSuspect"]["propId"], "p-root")

                st_ev, _, raw_ev = _req(port, "GET", "/api/evidence")
                self.assertEqual(st_ev, 200)
                self.assertIn("tierCounts", json.loads(raw_ev))

                # 5. /api/whiteboard*
                st_wbg, _, raw_wbg = _req(port, "GET", "/api/whiteboard")
                self.assertEqual(st_wbg, 200)
                self.assertNotIn(canary_wb_ans, raw_wbg.decode("utf-8"))
                self.assertNotIn(canary_wb_body, raw_wbg.decode("utf-8"))

                st_wbp, _, raw_wbp = _req(
                    port, "POST", "/api/whiteboard/push", headers=auth,
                    body={"kind": "derivation_step", "title": "Step 2", "body": "Integrate"},
                )
                self.assertEqual(st_wbp, 200, raw_wbp)
                st_wbpin, _, raw_wbpin = _req(
                    port, "POST", "/api/whiteboard/pin", headers=auth,
                    body={"stepIndex": 0, "note": "Check sign"},
                )
                self.assertEqual(st_wbpin, 200, raw_wbpin)
                st_wbu, _, raw_wbu = _req(
                    port, "POST", "/api/whiteboard/unlock", headers=auth,
                    body={"frameId": "wb-gate-1", "prediction": "negative", "confidence": 0.8},
                )
                self.assertEqual(st_wbu, 200, raw_wbu)
                self.assertTrue(json.loads(raw_wbu)["unlocked"])
                st_wbc, _, raw_wbc = _req(port, "POST", "/api/whiteboard/clear", headers=auth, body={})
                self.assertEqual(st_wbc, 200, raw_wbc)
                self.assertTrue(json.loads(raw_wbc)["cleared"])

                # 6. /api/derivation/* & /api/viva/*
                st_dg, _, raw_dg = _req(port, "GET", "/api/derivations")
                self.assertEqual(st_dg, 200)
                self.assertEqual(json.loads(raw_dg)["count"], 1)

                st_dc, _, raw_dc = _req(
                    port, "POST", "/api/derivation/check", headers=auth,
                    body={"derivationId": "d-srv-1", "stepId": "s1", "answer": "sqrt(m/k)*2*pi"},
                )
                self.assertEqual(st_dc, 200, raw_dc)
                self.assertTrue(json.loads(raw_dc)["passed"])

                st_dcut, _, raw_dcut = _req(
                    port, "POST", "/api/derivation/cut", headers=auth,
                    body={"derivationId": "d-srv-1", "axiomId": "a1", "guessStepId": "s1"},
                )
                self.assertEqual(st_dcut, 200, raw_dcut)
                self.assertTrue(json.loads(raw_dcut)["passed"])

                st_vs, _, raw_vs = _req(port, "POST", "/api/viva/start", headers=auth, body={"propId": "p-leaf"})
                self.assertEqual(st_vs, 200, raw_vs)
                v_sid = json.loads(raw_vs)["sessionId"]
                st_vt, _, raw_vt = _req(
                    port, "POST", "/api/viva/turn", headers=auth,
                    body={"sessionId": v_sid, "response": "Because SHM requires small amplitude linear restoring force.", "confidence": 0.8},
                )
                self.assertEqual(st_vt, 200, raw_vt)
                self.assertEqual(json.loads(raw_vt)["turnCompleted"], 1)

                # 7. /api/demo/load (valid starter pack + traversal 400)
                st_bad_demo, _, _ = _req(port, "POST", "/api/demo/load", headers=auth, body={"packSlug": "../evil"})
                self.assertEqual(st_bad_demo, 400)
                st_ok_demo, _, raw_ok_demo = _req(port, "POST", "/api/demo/load", headers=auth, body={"packSlug": "raft-consensus"})
                self.assertEqual(st_ok_demo, 200, raw_ok_demo)
                self.assertTrue(json.loads(raw_ok_demo)["ok"])
            finally:
                srv.shutdown()
                srv.server_close()
                t_srv.join(timeout=3.0)

            # 8. --lan paired-cookie isolation: MUST block /api/profile & /api/whiteboard (403)
            srv_lan = srv_mod.create_server(workspace=ws, host="127.0.0.1", port=0, lan=True)
            t_lan = threading.Thread(target=srv_lan.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
            t_lan.start()
            try:
                lport = srv_lan.bound_port
                lan_host = f"192.168.1.77:{lport}"
                st_p, hdrs_p, _ = _req(lport, "GET", f"/pocket?pair={srv_lan.pair_token}", headers={"Host": lan_host})
                self.assertEqual(st_p, 302)
                cookie = hdrs_p["set-cookie"].split(";", 1)[0]

                st_pkt, _, _ = _req(lport, "GET", "/api/pocket/state", headers={"Host": lan_host, "Cookie": cookie})
                self.assertEqual(st_pkt, 200)
                for blocked_path in ("/api/profile", "/api/whiteboard", "/api/next-move", "/api/root-cause", "/api/evidence"):
                    st_b, _, _ = _req(lport, "GET", blocked_path, headers={"Host": lan_host, "Cookie": cookie})
                    self.assertEqual(st_b, 403, f"--lan cookie MUST be blocked (403) on {blocked_path}")
                st_bpost, _, _ = _req(
                    lport, "POST", "/api/whiteboard/push",
                    headers={"Host": lan_host, "Origin": f"http://{lan_host}", "Cookie": cookie},
                    body={"kind": "summary", "body": "blocked"},
                )
                self.assertEqual(st_bpost, 403)
            finally:
                srv_lan.shutdown()
                srv_lan.server_close()
                t_lan.join(timeout=3.0)

    def test_v3_hooks_sync_graph_cli_flags_vault_local_attach_and_windows_configs(self) -> None:
        import syntopik.cli as cli_mod
        import syntopik.paths as paths_mod
        import syntopik.whiteboard as wb

        with tempfile.TemporaryDirectory(prefix="synt-v3-cli-") as tmp:
            t = Path(tmp)
            ws = t / "ws"
            kg = ws / "knowledge-graph"
            ch = ws / "chapters"
            kg.mkdir(parents=True)
            ch.mkdir(parents=True)

            (kg / "propositions.jsonl").write_text(
                json.dumps({
                    "id": "cs-q1", "subject": "CS", "chapter": "Consensus", "title": "Quorum intersection",
                    "kind": "axiom", "status": "shaky", "nextReview": "2020-01-01",
                }) + "\n",
                encoding="utf-8",
            )
            (kg / "chains.jsonl").write_text("", encoding="utf-8")
            (kg / "mistakes.jsonl").write_text("", encoding="utf-8")
            (kg / "forks.jsonl").write_text(
                json.dumps({
                    "id": "fk-1", "propId": "cs-q1", "prompt": "Min quorum?",
                    "sealedBlunder": "SECRET_SYNC_BLUNDER", "sealedWinningMove": "SECRET_SYNC_WIN",
                    "nextReview": "2020-01-01",
                }) + "\n",
                encoding="utf-8",
            )
            wb.push_frame(
                kg,
                {
                    "id": "g-sync", "kind": "prediction_gate", "title": "Gate",
                    "prompt": "Predict?", "expectedAnswer": "SECRET_SYNC_GATE", "revealedBody": "SECRET_SYNC_BODY",
                },
            )
            wb.pin_step(kg, 0, "Need intuition on quorum")

            (ch / "consensus.md").write_text(
                "# Consensus\n\n:::explore quorum-lab\n"
                '{"title":"Quorum Size","params":[{"name":"N","min":3,"max":11,"step":2,"default":5}],"outputs":[{"label":"Quorum","expr":"floor(N/2)+1"}]}\n'
                ":::\n",
                encoding="utf-8",
            )

            # 1. session-start.mjs --workspace <tmp>, post-graph-sync.mjs --workspace <tmp>, sync-graph.mjs v5
            ss = run_cli(["node", str(ROOT / ".agents" / "hooks" / "session-start.mjs"), "--workspace", str(ws)], stdin_text="{}")
            self.assertEqual(ss.returncode, 0, ss.stderr)
            msg = json.loads(ss.stdout)["injectSteps"][0]["systemMessage"]["systemMessage"]
            self.assertIn("Learner Profile:", msg)
            self.assertIn("Active TeachingPolicy:", msg)
            self.assertIn("Due Forks/Arenas/Re-Entry:", msg)
            self.assertIn("Pending Whiteboard Pins (1):", msg)
            self.assertIn("Need intuition on quorum", msg)

            ps = run_cli(
                ["node", str(ROOT / ".agents" / "hooks" / "post-graph-sync.mjs"), "--workspace", str(ws)],
                stdin_text=json.dumps({"tool_name": "Write", "tool_input": {"file_path": str(kg / "learner-profile.json")}}),
            )
            self.assertEqual(ps.returncode, 0, ps.stderr)
            self.assertTrue((kg / "graph-data.js").is_file())

            gd_out = kg / "graph-data-v5.js"
            sg = run_cli([
                "node", str(SCRIPTS_DIR / "sync-graph.mjs"),
                "--kg-dir", str(kg), "--chapters-dir", str(ch), "--out", str(gd_out),
            ])
            self.assertEqual(sg.returncode, 0, sg.stderr)
            gd_txt = gd_out.read_text(encoding="utf-8")
            for secret in ("SECRET_SYNC_BLUNDER", "SECRET_SYNC_WIN", "SECRET_SYNC_GATE", "SECRET_SYNC_BODY"):
                self.assertNotIn(secret, gd_txt, f"sync-graph.mjs leaked secret: {secret}")
            # Parse window.__STUDY_HUB__ via Node cleanly
            hub_eval = run_cli(
                ["node", "-e", f"globalThis.window={{}}; require({json.dumps(str(gd_out))}); console.log(JSON.stringify(window.__STUDY_HUB__));"]
            )
            self.assertEqual(hub_eval.returncode, 0, hub_eval.stderr)
            hub = json.loads(hub_eval.stdout)
            self.assertEqual(hub["schemaVersion"], 5)
            self.assertGreaterEqual(len(hub.get("starterPacks") or []), 3)
            expl_map = hub.get("explorables") or {}
            self.assertIsInstance(expl_map, dict)
            all_expls = [item for arr in expl_map.values() if isinstance(arr, list) for item in arr]
            self.assertGreaterEqual(len(all_expls), 1)
            self.assertTrue(all_expls[0]["outputs"][0]["rpn"])

            # 2. All 5 fixed CLI flags (triage --exam-in 48h, review --recover, doctor --fix, import --chapter, export json|dossier|--pocket|--vault)
            def _run_py_cli(args: list[str]) -> dict[str, Any]:
                p = run_cli([sys.executable, "-B", "-m", "syntopik.cli", *args])
                self.assertEqual(p.returncode, 0, f"CLI {args} failed (rc={p.returncode}):\nSTDOUT: {p.stdout}\nSTDERR: {p.stderr}")
                return json.loads(p.stdout)

            tr_res = _run_py_cli(["triage", "--exam-in", "48h", "--workspace", str(ws), "--json"])
            self.assertTrue(tr_res["ok"])
            self.assertAlmostEqual(tr_res["triage"]["hoursLeft"], 48.0, places=1)

            rev_res = _run_py_cli(["review", "--recover", "--daily-cap", "10", "--workspace", str(ws), "--json"])
            self.assertTrue(rev_res["ok"])
            self.assertEqual(rev_res["mode"], "recover")

            ws_fix = t / "ws-fix"
            doc_res = _run_py_cli(["doctor", "--fix", "--core-only", "--workspace", str(ws_fix), "--json"])
            self.assertTrue(doc_res["fixApplied"])
            self.assertTrue((ws_fix / "knowledge-graph" / "propositions.jsonl").is_file())

            note_file = t / "my_notes.md"
            note_file.write_text("# Cache Coherence\nMESI protocol states.\n", encoding="utf-8")
            imp_res = _run_py_cli(["import", str(note_file), "--chapter", "mesi-cache", "--workspace", str(ws), "--json"])
            self.assertEqual(imp_res["slug"], "mesi-cache")

            exp_json_path = t / "export.json"
            ej = _run_py_cli(["export", "json", "--workspace", str(ws), "--out", str(exp_json_path), "--json"])
            self.assertTrue(ej["ok"] and exp_json_path.is_file())
            self.assertEqual(json.loads(exp_json_path.read_text(encoding="utf-8"))["format"], "json")

            exp_dos_path = t / "dossier.html"
            ed = _run_py_cli(["export", "dossier", "--workspace", str(ws), "--out", str(exp_dos_path), "--json"])
            self.assertTrue(ed["ok"] and exp_dos_path.is_file())

            exp_pkt_path = t / "pocket.html"
            ep = _run_py_cli(["export", "--pocket", "--workspace", str(ws), "--out", str(exp_pkt_path), "--json"])
            self.assertTrue(ep["ok"] and exp_pkt_path.is_file())

            exp_vault_dir = t / "obs-vault"
            eo = _run_py_cli(["export", "obsidian", "--vault", str(exp_vault_dir), "--workspace", str(ws), "--json"])
            self.assertTrue(eo["ok"] and exp_vault_dir.is_dir())

            # 3. --vault local split-brain parity + attach --harness auto|antigravity + Claude settings hook merge preservation
            repo = t / "host-repo"
            repo.mkdir(parents=True)
            subprocess.run(["git", "init", "-q"], cwd=str(repo), check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=str(repo), check=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=str(repo), check=True)
            host_agents = "# Custom Host AGENTS\n"
            (repo / "AGENTS.md").write_text(host_agents, encoding="utf-8")
            (repo / ".claude").mkdir(parents=True)
            custom_hook = {"hooks": [{"type": "command", "command": "echo existing-host-hook"}]}
            (repo / ".claude" / "settings.json").write_text(
                json.dumps({"hooks": {"SessionStart": [custom_hook]}}),
                encoding="utf-8",
            )
            subprocess.run(["git", "add", "."], cwd=str(repo), check=True)
            subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=str(repo), check=True)

            att_auto = cli_mod.attach_workspace(repo, harness="auto", vault="local")
            self.assertTrue(att_auto["ok"])
            self.assertIn("claude", att_auto["harnesses"])
            # Split-brain parity: resolve_workspace(repo) MUST equal <repo>/.syntopik!
            self.assertEqual(paths_mod.resolve_workspace(repo), (repo / ".syntopik").resolve())
            self.assertEqual(paths_mod.resolve_kg_dir(repo), (repo / ".syntopik" / "knowledge-graph").resolve())

            # Existing host hook MUST be preserved alongside Syntopik hook (and deduped on rerun)
            merged_1 = json.loads((repo / ".claude" / "settings.json").read_text(encoding="utf-8"))
            ss_entries_1 = merged_1["hooks"]["SessionStart"]
            cmds_1 = [h.get("command", "") for entry in ss_entries_1 for h in entry.get("hooks", [])]
            self.assertIn("echo existing-host-hook", cmds_1)
            self.assertTrue(any("session-start.mjs" in c for c in cmds_1))
            cli_mod.attach_workspace(repo, harness="claude", vault="local")
            merged_2 = json.loads((repo / ".claude" / "settings.json").read_text(encoding="utf-8"))
            self.assertEqual(len(merged_2["hooks"]["SessionStart"]), len(ss_entries_1))

            att_ag = cli_mod.attach_workspace(repo, harness="antigravity", vault="local")
            self.assertTrue(att_ag["ok"])
            self.assertTrue((repo / ".agents" / "hooks.json").is_file())
            self.assertEqual((repo / "AGENTS.md").read_text(encoding="utf-8"), host_agents)

            # 4. Zero Windows-breaking POSIX shell patterns in tracked hook & MCP configs!
            posix_forbidden = ("SYNT_WORKSPACE=", "$(git rev-parse", "${VAR:-", "MLH_WORKSPACE=")
            for rel_cfg in (
                ".claude/settings.json",
                ".codex/hooks.json",
                ".claude-plugin/hooks.json",
                ".agents/hooks.json",
                ".mcp.json",
            ):
                cfg_p = ROOT / rel_cfg
                self.assertTrue(cfg_p.is_file(), f"Missing tracked config: {rel_cfg}")
                txt = cfg_p.read_text(encoding="utf-8")
                for bad_tok in posix_forbidden:
                    self.assertNotIn(bad_tok, txt, f"Windows-breaking POSIX token {bad_tok!r} found in {rel_cfg}")
                self.assertIsNone(re.search(r"\$\{[A-Za-z0-9_]+:-", txt), f"POSIX default expansion found in {rel_cfg}")

    def test_mcp_v3_stdio_brainstem_whiteboard_frontier_and_blackout_zero_leak(self) -> None:
        import syntopik.whiteboard as wb

        server_mjs = ROOT / "mcp-app" / "mlh-studio" / "dist" / "server.mjs"
        self.assertTrue(server_mjs.is_file())

        with tempfile.TemporaryDirectory(prefix="synt-v3-mcp-") as tmp:
            t = Path(tmp)
            ws = t / "ws"
            kg = ws / "knowledge-graph"
            ch = ws / "chapters"
            kg.mkdir(parents=True)
            ch.mkdir(parents=True)
            (kg / "propositions.jsonl").write_text(
                json.dumps({
                    "id": "p-mcp-1", "subject": "Math", "chapter": "Linear Algebra",
                    "title": "Rank-nullity theorem", "kind": "theorem", "status": "shaky", "nextReview": "2020-01-01",
                }) + "\n",
                encoding="utf-8",
            )
            (kg / "chains.jsonl").write_text("", encoding="utf-8")
            (kg / "mistakes.jsonl").write_text("", encoding="utf-8")
            wb.push_frame(kg, {"kind": "derivation_step", "title": "Basis", "body": "Pick kernel basis"})
            wb.pin_step(kg, 0, "Why extend basis?")

            secret_expr = "SECRET_BLACKOUT_EXPR_8821"
            secret_sol = "SECRET_BLACKOUT_MODEL_8822"
            secret_gate = "SECRET_WB_GATE_ANS_8823"

            reqs = [
                {
                    "jsonrpc": "2.0", "id": 1, "method": "initialize",
                    "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "syntopik-v3-test", "version": "3.0"}},
                },
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "synt_next_move", "arguments": {"domain": "Math"}}},
                {
                    "jsonrpc": "2.0", "id": 3, "method": "tools/call",
                    "params": {
                        "name": "synt_whiteboard_step",
                        "arguments": {
                            "kind": "prediction_gate",
                            "title": "Kernel Dimension Gate",
                            "prompt": "What is dim(ker T) + dim(im T)?",
                            "expectedAnswer": secret_gate,
                            "revealedBody": "Equals dim(V).",
                        },
                    },
                },
                {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "synt_get_frontier", "arguments": {"limit": 5}}},
                {
                    "jsonrpc": "2.0", "id": 5, "method": "tools/call",
                    "params": {
                        "name": "synt_practice_card",
                        "arguments": {
                            "action": "pose",
                            "problemId": "bo-rank-nullity",
                            "mode": "blackout",
                            "derivationId": "deriv-rn",
                            "blackoutLevel": 2,
                            "statement": "Reconstruct the hidden rank-nullity proof step.",
                            "steps": [
                                {"id": "a1", "label": "Kernel basis", "role": "axiom", "expr": "span(u_1..u_k) = ker(T)", "hidden": False},
                                {"id": "s1", "label": "Extend & map", "role": "step", "expr": secret_expr, "expectedExpr": secret_expr, "hidden": True, "dependsOn": ["a1"]},
                            ],
                            "modelSolution": secret_sol,
                        },
                    },
                },
                {
                    "jsonrpc": "2.0", "id": 6, "method": "tools/call",
                    "params": {
                        "name": "synt_practice_card",
                        "arguments": {
                            "action": "conclude",
                            "problemId": "bo-rank-nullity",
                            "outcome": "solved",
                            "verdict": "Exact linear independence proof.",
                            "rung": None,
                        },
                    },
                },
            ]

            proc = run_cli(
                ["node", str(server_mjs), f"--workspace={ws}", f"--data-dir={t / 'mcp-data'}"],
                stdin_text="\n".join(json.dumps(r) for r in reqs) + "\n",
                timeout=15.0,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            by_id = {
                int(m["id"]): m
                for m in (json.loads(ln) for ln in proc.stdout.splitlines() if ln.strip())
                if "id" in m
            }

            self.assertEqual(by_id[1]["result"]["serverInfo"]["version"], "3.2.0")

            # 1. synt_next_move returns Action Envelope honoring pending pin
            nm = by_id[2]["result"]
            self.assertFalse(nm.get("isError", False), nm)
            self.assertEqual(nm["structuredContent"]["recommendedMode"], "whiteboard_pin_answer")
            self.assertIn("teachingPolicy", nm["structuredContent"])

            # 2. synt_whiteboard_step pushes frame, returns pendingPins, and seals expectedAnswer
            wb_r = by_id[3]["result"]
            self.assertFalse(wb_r.get("isError", False), wb_r)
            self.assertNotIn(secret_gate, json.dumps(wb_r), "synt_whiteboard_step leaked expectedAnswer!")
            self.assertGreaterEqual(len(wb_r["structuredContent"].get("pendingPins") or []), 1)

            # 3. synt_get_frontier enriched with v3 psychology & gym telemetry
            fr = by_id[4]["result"]
            self.assertFalse(fr.get("isError", False), fr)
            fr_sc = fr["structuredContent"]
            for key in ("learnerProfile", "teachingPolicy", "dueForks", "arenasCount", "coldStartDays", "pendingPins", "rosettaBridgesCount", "readinessSummary", "taperActive"):
                self.assertIn(key, fr_sc, f"synt_get_frontier missing v3.2 key: {key}")

            # 4. synt_practice_card(mode="blackout") ZERO leak on pose, verbatim reveal on conclude
            bo_pose = by_id[5]["result"]
            self.assertFalse(bo_pose.get("isError", False), bo_pose)
            bo_dump = json.dumps(bo_pose)
            self.assertNotIn(secret_expr, bo_dump, "Blackout pose leaked hidden step expr!")
            self.assertNotIn(secret_sol, bo_dump, "Blackout pose leaked modelSolution!")
            self.assertNotIn("expectedExpr", bo_dump, "Blackout pose leaked expectedExpr key!")
            self.assertEqual(bo_pose["structuredContent"]["cardMode"], "blackout")
            self.assertEqual(bo_pose["structuredContent"]["hiddenStepIds"], ["s1"])

            bo_conc = by_id[6]["result"]
            self.assertFalse(bo_conc.get("isError", False), bo_conc)
            self.assertEqual(bo_conc["structuredContent"]["modelSolution"], secret_sol)


class TestSyntopikEvalAndStarterPacks(unittest.TestCase):
    """32. Syntopik v3.0 Deterministic 60-Day Cognitive Benchmark (7/7 Gates) & 3 Curated Zero-PII Starter Packs."""

    def test_syntopik_eval_deterministic_reproducible_and_all_7_gates_green(self) -> None:
        bench_script = ROOT / "benchmarks" / "syntopik_bench.py"
        self.assertTrue(bench_script.is_file())

        cmd = [sys.executable, "-B", str(bench_script), "--days", "60", "--seed", "42", "--check", "--json"]
        p1 = run_cli(cmd, timeout=20.0)
        self.assertEqual(p1.returncode, 0, f"syntopik_bench run 1 failed: {p1.stderr}")
        p2 = run_cli(cmd, timeout=20.0)
        self.assertEqual(p2.returncode, 0, f"syntopik_bench run 2 failed: {p2.stderr}")

        r1 = json.loads(p1.stdout)
        r2 = json.loads(p2.stdout)
        self.assertTrue(r1["gates"]["ok"], r1["gates"])
        self.assertEqual(r1["gates"]["total"], 7)
        self.assertEqual(r1["gates"]["passed"], 7)

        # Strip wall-clock timing and assert exact determinism across runs
        r1.pop("elapsedMs", None)
        r2.pop("elapsedMs", None)
        self.assertEqual(r1, r2, "Syntopik-Eval benchmark output is not deterministic across repeated runs!")

        # Also verify SVG generation into /tmp produces valid XML SVGs
        with tempfile.TemporaryDirectory(prefix="synt-v3-svg-") as tmp:
            p_svg = run_cli(
                [sys.executable, "-B", str(bench_script), "--days", "60", "--seed", "42", "--svg", "--svg-dir", tmp, "--check", "--json"],
                timeout=20.0,
            )
            self.assertEqual(p_svg.returncode, 0, p_svg.stderr)
            svg_meta = json.loads(p_svg.stdout)["svgOutputs"]
            self.assertTrue(svg_meta["ablation"]["validXml"])
            self.assertTrue(svg_meta["calibration"]["validXml"])
            self.assertTrue((Path(tmp) / "benchmark-ablation.svg").is_file())
            self.assertTrue((Path(tmp) / "benchmark-calibration.svg").is_file())

    def test_all_three_starter_packs_verify_zero_pii_and_roundtrip_import(self) -> None:
        import syntopik.packs as pk

        expected_packs = [
            "raft-consensus.syntopik-pack.zip",
            "shm-physics.syntopik-pack.zip",
            "transformers-attention.syntopik-pack.zip",
        ]
        packs_dir = ROOT / "packs"
        self.assertTrue(packs_dir.is_dir())

        for pack_name in expected_packs:
            pack_path = packs_dir / pack_name
            with self.subTest(pack=pack_name):
                self.assertTrue(pack_path.is_file(), f"Missing bundled starter pack: {pack_path}")

                ver = pk.verify_pack(pack_path)
                self.assertTrue(ver["ok"], f"verify_pack failed for {pack_name}: {ver}")
                self.assertEqual(ver.get("errors") or [], [])
                self.assertEqual(ver.get("unsafePaths") or [], [])
                self.assertEqual(ver.get("mismatches") or [], [])

                ins = pk.inspect_pack(pack_path)
                self.assertTrue(ins["ok"], ins)
                self.assertTrue(ins.get("zeroPiiVerified"), f"PII leaks found in {pack_name}: {ins.get('piiLeaks')}")
                self.assertEqual(ins.get("piiLeaks") or [], [])
                self.assertGreaterEqual(ins["counts"]["propositions"], 8)
                self.assertGreaterEqual(ins["counts"]["chains"], 5)
                self.assertGreaterEqual(ins["counts"]["derivations"], 1)

                with tempfile.TemporaryDirectory(prefix=f"synt-pack-{pack_path.stem}-") as tmp:
                    ws = Path(tmp) / "ws"
                    kg = ws / "knowledge-graph"
                    ch = ws / "chapters"
                    kg.mkdir(parents=True)
                    ch.mkdir(parents=True)

                    imp1 = pk.import_pack(pack_path, workspace=ws, kg_dir=kg, chapters_dir=ch, no_sync=True)
                    self.assertTrue(imp1["ok"], imp1)
                    self.assertGreaterEqual(imp1["addedPropositions"], 8)
                    self.assertGreaterEqual(imp1["addedChains"], 5)
                    self.assertGreaterEqual(imp1["addedDerivations"], 1)
                    self.assertTrue((kg / "propositions.jsonl").is_file())
                    self.assertTrue((kg / "chains.jsonl").is_file())
                    self.assertTrue((kg / "derivations.jsonl").is_file())
                    self.assertTrue(any(ch.glob("*-tracker.json")))

                    # Idempotent re-import skips duplicates cleanly
                    imp2 = pk.import_pack(pack_path, workspace=ws, kg_dir=kg, chapters_dir=ch, no_sync=True)
                    self.assertTrue(imp2["ok"], imp2)
                    self.assertEqual(imp2["addedPropositions"], 0)
                    self.assertGreaterEqual(imp2["skippedExisting"], 8)

    def test_wave4_security_pack_import_sympy_dos_evidence_cli_and_paths(self) -> None:
        """Wave 4C Lock: P0.1, P0.3, P1.1, P1.2, P1.5, P1.6, P2.1."""
        import syntopik.evidence as ev_mod
        import syntopik.forks as forks_mod
        import syntopik.learner_model as lm_mod
        import syntopik.packs as pk_mod
        import syntopik.paths as paths_mod
        import syntopik.work_mentor as wm_mod

        fa_mod = load_script_module("falsify_arena.py")
        df_mod = load_script_module("derivation_forge.py")
        sh_mod = load_script_module("setup_harness.py")

        # --- P0.1: import_pack populates chapters/*.md, forks.jsonl, exams/* & gates code arenas ---
        raft_pack = ROOT / "packs" / "raft-consensus.syntopik-pack.zip"
        shm_pack = ROOT / "packs" / "shm-physics.syntopik-pack.zip"
        with tempfile.TemporaryDirectory(prefix="synt-w4-p01-") as tmp:
            ws = Path(tmp) / "ws"
            kg = ws / "knowledge-graph"
            ch = ws / "chapters"
            kg.mkdir(parents=True)
            ch.mkdir(parents=True)

            res_default = pk_mod.import_pack(raft_pack, workspace=ws, kg_dir=kg, chapters_dir=ch, no_sync=True)
            self.assertTrue(res_default["ok"], res_default)
            self.assertEqual(res_default["addedChapters"], 1)
            self.assertTrue((ch / "raft-consensus.md").is_file())
            self.assertGreaterEqual(res_default["addedForks"], 2)
            self.assertTrue((kg / "forks.jsonl").is_file())
            self.assertGreaterEqual(res_default["addedExams"], 1)
            self.assertTrue(any((ws / "exams").glob("raft-consensus*")))
            # Code-domain arena MUST be skipped by default (allow_code_arenas=False)
            self.assertEqual(res_default["addedArenas"], 0)
            self.assertEqual(res_default["skippedCodeArenas"], 1)
            self.assertIn("arena-raft-uptodate-check", res_default.get("skippedCodeArenaIds") or [])

            # Math-domain arena imports even when allow_code_arenas=False
            res_shm = pk_mod.import_pack(shm_pack, workspace=ws, kg_dir=kg, chapters_dir=ch, no_sync=True)
            self.assertTrue(res_shm["ok"], res_shm)
            self.assertEqual(res_shm["addedArenas"], 1)
            self.assertEqual(res_shm["skippedCodeArenas"], 0)

            # Explicit allow_code_arenas=True imports the Raft code arena
            res_allow = pk_mod.import_pack(
                raft_pack, workspace=ws, kg_dir=kg, chapters_dir=ch, allow_code_arenas=True, no_sync=True
            )
            self.assertTrue(res_allow["ok"], res_allow)
            self.assertEqual(res_allow["addedArenas"], 1)
            self.assertEqual(res_allow["skippedCodeArenas"], 0)
            arena_rows = [json.loads(ln) for ln in (kg / "arenas.jsonl").read_text(encoding="utf-8").splitlines() if ln.strip()]
            self.assertIn("arena-raft-uptodate-check", {r.get("id") for r in arena_rows})

        # --- P0.3: SymPy symbolic-exponent CPU DoS guard across all 3 math evaluators (<0.5s) ---
        dos_cases = [
            ("forks_poly80", lambda: forks_mod._math_equivalent("(x+y+z)**80", "x**80")),
            ("forks_pow1000", lambda: forks_mod._math_equivalent("(x+1)**1000 - x**1000", "1")),
            ("forge_poly80", lambda: df_mod._math_equivalent("(x+y+z)**80", "x**80")),
            ("forge_pow1000", lambda: df_mod._math_equivalent("(x+1)**1000 - x**1000", "1")),
            ("arena_poly80", lambda: fa_mod._try_sympy_eval("x", {"x": "(a+b+c)**80"})),
            ("arena_pow1000", lambda: fa_mod._try_sympy_eval("(x+1)**1000 - x**1000", {"x": 2})),
        ]
        for label, fn in dos_cases:
            with self.subTest(dos_case=label):
                t0 = time.monotonic()
                res_val = fn()
                dt = time.monotonic() - t0
                self.assertLess(dt, 0.5, f"{label} took {dt:.3f}s (exceeds 0.5s DoS ceiling)")
                if label.startswith("arena_"):
                    self.assertFalse(res_val[0], f"{label} should reject/skip DoS payload: {res_val}")
                else:
                    self.assertFalse(res_val, f"{label} should return False for non-equivalent DoS expr")
        # Legitimate low-degree algebraic equivalence still passes
        self.assertTrue(forks_mod._math_equivalent("(x+1)**2", "x**2 + 2*x + 1"))
        self.assertTrue(df_mod._math_equivalent("2*pi*sqrt(m/k)", "sqrt(m/k)*2*pi"))

        # --- P1.1: evidence.py T3 from sealedMutants kill ratio & T4 from Viva outcome/scores.passed ---
        with tempfile.TemporaryDirectory(prefix="synt-w4-p11-") as tmp:
            ws = Path(tmp) / "ws"
            kg = ws / "knowledge-graph"
            kg.mkdir(parents=True)
            (kg / "propositions.jsonl").write_text(
                "\n".join([
                    json.dumps({"id": "p-t3-arena", "subject": "CS", "chapter": "Raft", "title": "Arena Prop", "status": "solid"}),
                    json.dumps({"id": "p-t4-viva", "subject": "CS", "chapter": "Raft", "title": "Viva Prop", "status": "solid"}),
                ]) + "\n",
                encoding="utf-8",
            )
            (kg / "chains.jsonl").write_text("", encoding="utf-8")
            (kg / "arenas.jsonl").write_text(
                json.dumps({
                    "id": "a-p11",
                    "propId": "p-t3-arena",
                    "sealedMutants": [{"id": "m1"}, {"id": "m2"}, {"id": "m3"}, {"id": "m4"}],
                    "killedMutants": ["m1", "m2", "m3"],
                }) + "\n",
                encoding="utf-8",
            )
            (kg / "viva-sessions.jsonl").write_text(
                json.dumps({
                    "id": "v-p11",
                    "propId": "p-t4-viva",
                    "status": "completed",
                    "outcome": "passed",
                    "scores": {"composite": 0.85, "passed": True},
                }) + "\n",
                encoding="utf-8",
            )
            ev_sum = ev_mod.compute_evidence_summary(ws)
            self.assertEqual(ev_sum["propositions"]["p-t3-arena"]["tier"], 3, ev_sum["propositions"]["p-t3-arena"])
            self.assertEqual(ev_sum["propositions"]["p-t4-viva"]["tier"], 4, ev_sum["propositions"]["p-t4-viva"])

        # --- P1.2: CLI board push --content writes frame["body"] & profile override --prose-density terse ---
        with tempfile.TemporaryDirectory(prefix="synt-w4-p12-") as tmp:
            ws = Path(tmp) / "ws"
            kg = ws / "knowledge-graph"
            kg.mkdir(parents=True)
            (kg / "propositions.jsonl").write_text("", encoding="utf-8")
            (kg / "chains.jsonl").write_text("", encoding="utf-8")

            p_wb = run_cli([
                sys.executable, "-B", "-m", "syntopik.cli",
                "board", "push", "--kind", "derivation_step", "--title", "Sign Check",
                "--content", "Keep restoring minus sign", "--workspace", str(ws), "--json",
            ])
            self.assertEqual(p_wb.returncode, 0, p_wb.stderr)
            wb_json = json.loads(p_wb.stdout)
            self.assertEqual(wb_json["frame"]["body"], "Keep restoring minus sign")
            wb_disk = json.loads((kg / "whiteboard-session.json").read_text(encoding="utf-8"))
            self.assertEqual(wb_disk["frames"][-1]["body"], "Keep restoring minus sign")

            p_cal = run_cli([
                sys.executable, "-B", "-m", "syntopik.cli",
                "profile", "calibrate", "--domain", "Physics", "--entry-ramp", "formalism_first",
                "--workspace", str(ws), "--json",
            ])
            self.assertEqual(p_cal.returncode, 0, p_cal.stderr)

            p_ov_txt = run_cli([
                sys.executable, "-B", "-m", "syntopik.cli",
                "profile", "override", "--prose-density", "terse", "--domain", "Physics", "--workspace", str(ws),
            ])
            self.assertEqual(p_ov_txt.returncode, 0, p_ov_txt.stderr)
            self.assertNotIn("Uncalibrated", p_ov_txt.stdout)
            self.assertIn("<= 105w", p_ov_txt.stdout)
            prof_disk = lm_mod.load_profile(kg)
            self.assertEqual(prof_disk["userOverrides"].get("maxProseWords"), 105)
            self.assertEqual(lm_mod.compile_teaching_policy(prof_disk, domain="Physics")["maxProseWords"], 105)

        # --- P1.5: learner_model NaN/Inf guards & ValueError on malformed now ---
        self.assertEqual(lm_mod._clamp(float("nan"), 0.0, 1.0), 0.0)
        self.assertEqual(lm_mod._clamp(float("inf"), 0.0, 1.0), 1.0)
        self.assertEqual(lm_mod._clamp(float("-inf"), 0.0, 1.0), 0.0)
        with tempfile.TemporaryDirectory(prefix="synt-w4-p15-") as tmp:
            kg = Path(tmp) / "kg"
            kg.mkdir(parents=True)
            tel = lm_mod.record_telemetry_event(
                kg,
                domain="Physics",
                prop_id="p-nan",
                correct=True,
                confidence=float("nan"),
                duration_ms=float("inf"),  # type: ignore[arg-type]
                now="2026-11-26T12:00:00Z",
            )
            self.assertTrue(tel["ok"])
            last_ev = tel["profile"]["recentEvents"][-1]
            self.assertIsNone(last_ev["confidence"])
            self.assertIsNone(last_ev["durationMs"])

            with self.assertRaises(ValueError):
                lm_mod.record_telemetry_event(kg, domain="Physics", prop_id="p-bad", correct=True, now="not-a-timestamp")
            with self.assertRaises(ValueError):
                lm_mod.classify_affective_state([], now="not-a-timestamp")

        # --- P1.6: Windows executable lookup returns full suffixed path (.cmd/.exe) ---
        with tempfile.TemporaryDirectory(prefix="synt-w4-p16-") as tmp:
            bin_dir = Path(tmp) / "winbin"
            bin_dir.mkdir(parents=True)
            cmd_path = bin_dir / "synt_fake_cmd.cmd"
            exe_path = bin_dir / "synt_fake_exe.exe"
            cmd_path.write_text("@echo off\n", encoding="utf-8")
            exe_path.write_text("MZ", encoding="utf-8")

            host_path_cls = type(Path())
            for mod_name, mod in (("syntopik.paths", paths_mod), ("setup_harness", sh_mod)):
                with self.subTest(module=mod_name):
                    with unittest.mock.patch.object(mod, "Path", host_path_cls), unittest.mock.patch.object(
                        mod.os, "name", "nt"
                    ):
                        hit_cmd = mod.find_binary("synt_fake_cmd", extra_dirs=[bin_dir])
                        hit_exe = mod.find_binary("synt_fake_exe", extra_dirs=[bin_dir])
                        self.assertEqual(hit_cmd, str(cmd_path))
                        self.assertEqual(hit_exe, str(exe_path))

        # --- P2.1: work_mentor validate_git_ref rejects HEAD:../etc/passwd while allowing HEAD~1..HEAD ---
        for bad_ref in ("HEAD:../etc/passwd", "../etc/passwd", "main:secret.txt", "HEAD --exec=evil"):
            with self.subTest(bad_ref=bad_ref):
                with self.assertRaises(ValueError):
                    wm_mod.validate_git_ref(bad_ref)
        for ok_ref in ("HEAD~1..HEAD", "main...feature", "origin/main..feature/v2.1"):
            with self.subTest(ok_ref=ok_ref):
                self.assertEqual(wm_mod.validate_git_ref(ok_ref), ok_ref)

    def test_wave4_level3_blackout_dossier_jail_and_explorable_sandbox(self) -> None:
        """Wave 4C Lock: P0.4 (server + sync-graph.mjs), P1.4 (explorable.py + sync-graph.mjs + dashboard.html RPN), P2.2."""
        import syntopik.explorable as expl_mod
        import syntopik.server as srv_mod

        def _req(
            port: int,
            method: str,
            path: str,
            *,
            headers: dict[str, str] | None = None,
            body: Any = None,
        ) -> tuple[int, bytes]:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10.0)
            hdrs = dict(headers or {})
            payload: bytes | None = None
            if body is not None:
                payload = json.dumps(body).encode("utf-8")
                hdrs.setdefault("Content-Type", "application/json")
            try:
                conn.request(method, path, body=payload, headers=hdrs)
                resp = conn.getresponse()
                return resp.status, resp.read()
            finally:
                conn.close()

        # --- P1.4 (Python explorable.py) ---
        with self.assertRaises(ValueError):
            expl_mod.compile_expr_to_rpn("9" * 400)
        with self.assertRaises(ValueError):
            expl_mod.compile_explorable_spec({
                "id": "bad-empty-params",
                "title": "Proto Probe",
                "params": [],
                "outputs": [{"label": "Leak", "expr": "constructor + toString"}],
            })
        exact_rpn = expl_mod.compile_expr_to_rpn("x * 2", allowed_vars={"x"})
        hi_curve = expl_mod.sample_explorable_curve({
            "params": [{"name": "x", "default": 1.0}],
            "curve": {"xVar": "x", "xMin": 0.0, "xMax": 1.0, "samples": 100000, "exactRpn": exact_rpn},
        })
        self.assertEqual(len(hi_curve["points"]), 200)
        lo_curve = expl_mod.sample_explorable_curve({
            "params": [{"name": "x", "default": 1.0}],
            "curve": {"xVar": "x", "xMin": 0.0, "xMax": 1.0, "samples": -5, "exactRpn": exact_rpn},
        })
        self.assertEqual(len(lo_curve["points"]), 2)

        # --- P0.4 (server + sync-graph.mjs Level-3 blackout) & P2.2 (dossier out-of-jail 403) ---
        canary_step = "SECRET_STEP_EXPR_4C_9101"
        canary_step_note = "SECRET_STEP_NOTE_4C_9102"
        canary_thm = "SECRET_THEOREM_EXPR_4C_9103"
        canary_thm_note = "SECRET_THEOREM_NOTE_4C_9104"

        with tempfile.TemporaryDirectory(prefix="synt-w4-srv-") as tmp:
            ws = Path(tmp) / "ws"
            kg = ws / "knowledge-graph"
            ch = ws / "chapters"
            kg.mkdir(parents=True)
            ch.mkdir(parents=True)
            (kg / "propositions.jsonl").write_text(
                json.dumps({"id": "p-w4", "subject": "Physics", "chapter": "SHM", "title": "Pendulum", "status": "solid"}) + "\n",
                encoding="utf-8",
            )
            (kg / "chains.jsonl").write_text("", encoding="utf-8")
            (kg / "mistakes.jsonl").write_text("", encoding="utf-8")
            (kg / "derivations.jsonl").write_text(
                json.dumps({
                    "id": "deriv-w4-l3",
                    "propId": "p-w4",
                    "title": "Level-3 Masking Test",
                    "steps": [
                        {"id": "a1", "role": "axiom", "label": "Axiom", "expr": "F = -k*x", "note": "Visible axiom", "dependsOn": []},
                        {"id": "s1", "role": "step", "label": "Mid Step", "expr": canary_step, "note": canary_step_note, "dependsOn": ["a1"]},
                        {"id": "s2", "role": "theorem", "label": "Theorem", "expr": canary_thm, "note": canary_thm_note, "dependsOn": ["s1"]},
                    ],
                }) + "\n",
                encoding="utf-8",
            )
            # Chapter with both malicious prototype :::explore blocks AND valid over-sampled block
            (ch / "sandbox.md").write_text(
                "# Sandbox\n\n"
                ":::explore bad-proto-1\n"
                '{"title":"Bad 1","params":[],"outputs":[{"label":"X","expr":"constructor(1)"}]}\n'
                ":::\n\n"
                ":::explore bad-proto-2\n"
                '{"title":"Bad 2","params":[{"name":"__proto__","min":0,"max":1,"default":0}],"outputs":[{"label":"X","expr":"__proto__ + 1"}]}\n'
                ":::\n\n"
                ":::explore good-lab\n"
                '{"title":"Good Lab","params":[{"name":"x","min":0,"max":10,"default":2}],"outputs":[{"label":"Sq","expr":"x**2"}],"curve":{"xVar":"x","xMin":0,"xMax":10,"samples":9999,"exactExpr":"x**2"}}\n'
                ":::\n",
                encoding="utf-8",
            )

            # (a) sync-graph.mjs Level-3 blackout masking & prototype-safe RPN parser
            gd_out = kg / "graph-data-w4.js"
            sg = run_cli([
                "node", str(SCRIPTS_DIR / "sync-graph.mjs"),
                "--workspace", str(ws), "--kg-dir", str(kg), "--chapters-dir", str(ch), "--out", str(gd_out),
            ])
            self.assertEqual(sg.returncode, 0, sg.stderr)
            gd_text = gd_out.read_text(encoding="utf-8")
            for secret in (canary_step, canary_step_note, canary_thm, canary_thm_note):
                self.assertNotIn(secret, gd_text, f"sync-graph.mjs leaked derivation secret: {secret}")
            hub_proc = run_cli([
                "node", "-e",
                f"globalThis.window={{}}; require({json.dumps(str(gd_out))}); console.log(JSON.stringify(window.__STUDY_HUB__));",
            ])
            self.assertEqual(hub_proc.returncode, 0, hub_proc.stderr)
            hub = json.loads(hub_proc.stdout)
            d_row = hub["derivations"][0]
            self.assertEqual(d_row["blackoutLevel"], 3)
            steps_by_id = {s["id"]: s for s in d_row["steps"]}
            self.assertFalse(steps_by_id["a1"]["hidden"])
            self.assertEqual(steps_by_id["a1"]["expr"], "F = -k*x")
            for hid in ("s1", "s2"):
                self.assertTrue(steps_by_id[hid]["hidden"])
                self.assertIsNone(steps_by_id[hid]["expr"])
                self.assertIsNone(steps_by_id[hid]["note"])

            # Malicious :::explore blocks rejected; valid block compiled & samples clamped to 200
            sb_expl = (hub.get("explorables") or {}).get("sandbox") or []
            self.assertEqual([e.get("id") for e in sb_expl], ["good-lab"])
            self.assertEqual(sb_expl[0]["curve"]["samples"], 200)

            # (b) Live server GET /api/state, GET /api/derivations (Level-3), POST /api/export/dossier jail
            srv = srv_mod.create_server(workspace=ws, host="127.0.0.1", port=0)
            t_srv = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
            t_srv.start()
            try:
                port = srv.bound_port
                auth = {"X-Syntopik-Token": srv.csrf_token}

                for ep in ("/api/state", "/api/derivations"):
                    st_code, raw_b = _req(port, "GET", ep)
                    self.assertEqual(st_code, 200, f"{ep} failed: {raw_b}")
                    raw_s = raw_b.decode("utf-8")
                    for secret in (canary_step, canary_step_note, canary_thm, canary_thm_note):
                        self.assertNotIn(secret, raw_s, f"{ep} leaked derivation secret: {secret}")
                    body_j = json.loads(raw_s)
                    derivs = body_j["derivations"]
                    self.assertEqual(derivs[0]["hiddenCount"], 2)
                    self.assertEqual(derivs[0]["hiddenStepIds"], ["s1", "s2"])
                    st_map = {s["id"]: s for s in derivs[0]["steps"]}
                    self.assertFalse(st_map["a1"]["hidden"])
                    self.assertEqual(st_map["a1"]["expr"], "F = -k*x")
                    self.assertTrue(st_map["s1"]["hidden"] and st_map["s1"]["expr"] is None and st_map["s1"]["note"] is None)
                    self.assertTrue(st_map["s2"]["hidden"] and st_map["s2"]["expr"] is None and st_map["s2"]["note"] is None)

                # P2.2: out-of-jail knowledge-graph/pwn.html MUST return 403 and NOT create file
                st_jail, _ = _req(
                    port, "POST", "/api/export/dossier", headers=auth, body={"outPath": "knowledge-graph/pwn.html"}
                )
                self.assertEqual(st_jail, 403)
                self.assertFalse((kg / "pwn.html").exists())

                # In-jail chapters/ok-dossier.html MUST succeed (200)
                st_ok, raw_ok = _req(
                    port, "POST", "/api/export/dossier", headers=auth, body={"outPath": "chapters/ok-dossier.html"}
                )
                self.assertEqual(st_ok, 200, raw_ok)
                self.assertTrue((ch / "ok-dossier.html").is_file())
            finally:
                srv.shutdown()
                srv.server_close()
                t_srv.join(timeout=3.0)

        # --- P1.4 (Node runtime verification of dashboard.html null-prototype RPN sandbox) ---
        dash_src = (ROOT / "dashboard.html").read_text(encoding="utf-8")
        rpn_start = dash_src.index("const RPN_MAX_OPS = 256;")
        rpn_end = dash_src.index("function ensureCompiledExploreSpec(", rpn_start)
        rpn_snippet = dash_src[rpn_start:rpn_end]
        node_rpn_check = (
            rpn_snippet
            + "\n"
            + "if (Object.getPrototypeOf(RPN_FUNCS) !== null) throw new Error('RPN_FUNCS has prototype');\n"
            + "if (Object.getPrototypeOf(RPN_CONSTS) !== null) throw new Error('RPN_CONSTS has prototype');\n"
            + "if (compileExprToRpnJs('constructor(1)') !== null) throw new Error('constructor call allowed');\n"
            + "if (compileExprToRpnJs('__proto__ + 1') !== null) throw new Error('__proto__ var allowed');\n"
            + "if (validateRpnArray([{op:'call', fn:'constructor', argc:0}]) !== null) throw new Error('raw constructor op allowed');\n"
            + "if (validateRpnArray([{op:'var', name:'__proto__'}]) !== null) throw new Error('raw __proto__ op allowed');\n"
            + "const okOps = compileExprToRpnJs('sin(x)**2 + cos(x)**2');\n"
            + "const val = evaluateRpn(okOps, {x: 1.23});\n"
            + "if (!Number.isFinite(val) || Math.abs(val - 1.0) > 1e-9) throw new Error('valid RPN failed: ' + val);\n"
            + "console.log('RPN_OK');\n"
        )
        rpn_proc = run_cli(["node", "-e", node_rpn_check])
        self.assertEqual(rpn_proc.returncode, 0, f"dashboard.html RPN check failed: {rpn_proc.stderr}")
        self.assertIn("RPN_OK", rpn_proc.stdout)

    def test_wave4_ui_xss_pack_mode_svg_uri_and_static_demo_site_locks(self) -> None:
        """Wave 4C Lock: P0.2, P1.3, P1.7, and docs/{index,viewer,pocket}.html static sandbox gates."""
        dash_src = (ROOT / "dashboard.html").read_text(encoding="utf-8")
        pane_src = (ROOT / "mcp-app" / "mlh-studio" / "src" / "ui" / "studio-pane.mjs").read_text(encoding="utf-8")
        docs_dir = ROOT / "docs"
        index_path = docs_dir / "index.html"
        viewer_path = docs_dir / "viewer.html"
        pocket_path = docs_dir / "pocket.html"
        nojekyll_path = docs_dir / ".nojekyll"

        for p in (index_path, viewer_path, pocket_path, nojekyll_path):
            self.assertTrue(p.is_file(), f"Missing static demo artifact: {p}")

        index_src = index_path.read_text(encoding="utf-8")

        # --- P0.2: Arena res.newlyKilled XSS escape & studio-pane maxWords escape ---
        for label, html_txt in (("dashboard.html", dash_src), ("docs/index.html", index_src)):
            with self.subTest(xss_target=label):
                self.assertIn('esc(res.newlyKilled.join(", "))', html_txt)
                self.assertNotIn('${res.newlyKilled.join(', html_txt)
        self.assertIn("escapeHtml(String(maxWords))", pane_src)

        # --- P1.3: Pack import mode uses 'suspend-all' (never broken 'replace') ---
        for label, html_txt in (("dashboard.html", dash_src), ("docs/index.html", index_src)):
            with self.subTest(pack_mode_target=label):
                self.assertIn('value="suspend-all"', html_txt)
                self.assertNotIn('value="replace"', html_txt)

        # --- P1.7: SVG <image href> URI scheme hardening in dashboard.html & studio-pane.mjs ---
        dash_occ_start = dash_src.index("function isSafeOcclusionDataUri(")
        dash_occ_end = dash_src.index("function renderOcclusionStageHtml(", dash_occ_start)
        dash_occ_js = (
            "function relHref(p) { return './' + p; }\n"
            + dash_src[dash_occ_start:dash_occ_end]
            + "\n"
            + "const png = 'data:image/png;base64,iVBORw0KGgo=';\n"
            + "const badSvg = 'data:image/svg+xml;base64,' + Buffer.from('<svg><script>alert(1)</script></svg>').toString('base64');\n"
            + "const extSvg = 'data:image/svg+xml;base64,' + Buffer.from('<svg><image href=\"https://evil.example/x.png\"/></svg>').toString('base64');\n"
            + "for (const spec of [\n"
            + "  {src: 'https://evil.example/a.png'}, {src: 'http://evil.example/a.png'},\n"
            + "  {src: 'javascript:alert(1)'}, {dataUri: 'javascript:alert(1)'},\n"
            + "  {dataUri: 'https://evil.example/a.png'}, {dataUri: 'data:text/html;base64,PGgxPg=='},\n"
            + "  {dataUri: badSvg}, {dataUri: extSvg}, {src: '../secret.png'}\n"
            + "]) {\n"
            + "  const r = resolveSafeOcclusionHref(spec);\n"
            + "  if (r !== '') throw new Error('dashboard allowed unsafe href: ' + JSON.stringify(spec) + ' -> ' + r);\n"
            + "}\n"
            + "if (resolveSafeOcclusionHref({dataUri: png}) !== png) throw new Error('dashboard rejected valid png dataUri');\n"
            + "if (resolveSafeOcclusionHref({src: 'assets/fig1.png'}) !== './assets/fig1.png') throw new Error('dashboard rejected local asset');\n"
            + "console.log('DASH_SVG_OK');\n"
        )
        p_dash_svg = run_cli(["node", "-e", dash_occ_js])
        self.assertEqual(p_dash_svg.returncode, 0, f"dashboard.html SVG URI check failed: {p_dash_svg.stderr}")
        self.assertIn("DASH_SVG_OK", p_dash_svg.stdout)

        pane_occ_start = pane_src.index("function isSafeOcclusionDataUri(")
        pane_occ_end = pane_src.index("function renderOcclusionWidget(", pane_occ_start)
        pane_occ_js = (
            pane_src[pane_occ_start:pane_occ_end]
            + "\n"
            + "const png = 'data:image/png;base64,iVBORw0KGgo=';\n"
            + "const badSvg = 'data:image/svg+xml;base64,' + Buffer.from('<svg onload=\"alert(1)\"></svg>').toString('base64');\n"
            + "for (const spec of [\n"
            + "  {src: 'https://evil.example/a.png'}, {src: 'http://evil.example/a.png'},\n"
            + "  {src: 'javascript:alert(1)'}, {dataUri: 'javascript:alert(1)'},\n"
            + "  {dataUri: 'https://evil.example/a.png'}, {dataUri: badSvg}\n"
            + "]) {\n"
            + "  const r = resolveSafeOcclusionImageHref(spec);\n"
            + "  if (r !== '') throw new Error('studio-pane allowed unsafe href: ' + JSON.stringify(spec) + ' -> ' + r);\n"
            + "}\n"
            + "if (resolveSafeOcclusionImageHref({dataUri: png}) !== png) throw new Error('studio-pane rejected valid png dataUri');\n"
            + "console.log('PANE_SVG_OK');\n"
        )
        p_pane_svg = run_cli(["node", "-e", pane_occ_js])
        self.assertEqual(p_pane_svg.returncode, 0, f"studio-pane.mjs SVG URI check failed: {p_pane_svg.stderr}")
        self.assertIn("PANE_SVG_OK", p_pane_svg.stdout)

        # --- Static GitHub Pages Demo Site Gates (<= 650 KB, zero CDN, zero user paths, zero eval, Level-3 masking) ---
        max_bytes = 650 * 1024
        ext_cdn_re = re.compile(
            r"""(?:<script\b[^>]*\bsrc\s*=\s*["']https?://|<link\b[^>]*\bhref\s*=\s*["']https?://"""
            r"""|<img\b[^>]*\bsrc\s*=\s*["']https?://|@import\s+(?:url\s*\(\s*)?["']?https?://"""
            r"""|fonts\.googleapis\.com|cdn\.jsdelivr\.net|unpkg\.com|cdnjs\.cloudflare\.com)""",
            re.IGNORECASE,
        )
        user_path_re = re.compile(
            r"(?:/Users/[A-Za-z0-9._-]+|/home/[A-Za-z0-9._-]+|[A-Za-z]:\\Users\\[A-Za-z0-9._-]+)"
        )
        dangerous_js_re = re.compile(r"\b(?:eval\s*\(|new\s+Function\s*\()")
        for doc_file in (index_path, viewer_path, pocket_path):
            with self.subTest(demo_file=doc_file.name):
                size = doc_file.stat().st_size
                self.assertGreater(size, 1000)
                self.assertLessEqual(size, max_bytes, f"{doc_file.name} ({size} B) exceeds 650 KB ceiling ({max_bytes} B)")
                txt = doc_file.read_text(encoding="utf-8")
                self.assertEqual(ext_cdn_re.findall(txt), [], f"{doc_file.name} has external CDN/resource links")
                self.assertEqual(user_path_re.findall(txt), [], f"{doc_file.name} leaks personal home path")
                self.assertEqual(dangerous_js_re.findall(txt), [], f"{doc_file.name} contains eval/new Function")
                for stok in ("sealedWinningMove", "sealedBlunder", "sealedMutants", "sealedReference"):
                    self.assertNotIn(stok, txt, f"{doc_file.name} leaks sealed token {stok!r}")

        # Verify docs/index.html embeds Level-3 masked derivations
        hub_match = re.search(r"window\.__STUDY_HUB__\s*=\s*(\{.*?\});\n</script>", index_src)
        self.assertIsNotNone(hub_match, "docs/index.html missing inline window.__STUDY_HUB__")
        assert hub_match is not None
        demo_hub = json.loads(hub_match.group(1))
        self.assertGreaterEqual(len(demo_hub.get("derivations") or []), 3)
        for dv in demo_hub["derivations"]:
            self.assertEqual(dv.get("blackoutLevel"), 3, f"docs/index.html derivation {dv.get('id')} not Level-3 masked")
            for st in dv.get("steps") or []:
                if st.get("role") != "axiom":
                    self.assertTrue(st.get("hidden"), f"Non-axiom step {st.get('id')} exposed in docs/index.html")
                    self.assertIsNone(st.get("expr"), f"Non-axiom expr exposed in docs/index.html")


class TestV31RosettaSyncReplayAndProofDiff(unittest.TestCase):
    """33. Syntopik v3.1 Rosetta Isomorphism Engine, CRDT Delta Sync, Policy Replay & Proof-Graph Handwriting Diff."""

    def test_rosetta_isomorphism_archetypes_fire_credit_and_brainstem(self) -> None:
        import syntopik.brainstem as bs
        import syntopik.fsrs_core as fc
        import syntopik.isomorphism as iso
        import syntopik.learner_model as lm

        # 1. Archetype library schema (>= 10 archetypes with valid breaksAt, variableMap, bijectionQuestion)
        archetypes = iso.load_archetypes()
        self.assertGreaterEqual(len(archetypes), 10)
        for arch in archetypes:
            with self.subTest(archetype=arch.get("id")):
                self.assertTrue(str(arch.get("id") or "").strip())
                self.assertTrue(str(arch.get("name") or "").strip())
                breaks = arch.get("breaksAt")
                self.assertIsInstance(breaks, list)
                self.assertGreaterEqual(len(breaks), 1)
                self.assertTrue(all(isinstance(b, str) and b.strip() for b in breaks))
                self.assertTrue(str(arch.get("bijectionQuestion") or "").strip())
                domains = arch.get("domains")
                self.assertIsInstance(domains, list)
                self.assertGreaterEqual(len(domains), 2)
                for slot in domains:
                    vmap = slot.get("variableMap")
                    self.assertIsInstance(vmap, dict)
                    self.assertGreaterEqual(len(vmap), 2)

        with tempfile.TemporaryDirectory(prefix="synt-v31-rosetta-") as tmp:
            ws = Path(tmp) / "ws"
            kg = ws / "knowledge-graph"
            ch = ws / "chapters"
            kg.mkdir(parents=True)
            ch.mkdir(parents=True)

            seed_props = [
                {
                    "id": "phys-damped-shm",
                    "subject": "Physics",
                    "chapter": "SHM",
                    "title": "Damped Simple Harmonic Motion (SHM) Oscillator",
                    "statement": "Linear restoring force and viscous damping obey m x'' + b x' + k x = 0 with resonance and underdamped/overdamped regimes.",
                    "kind": "theorem",
                    "status": "solid",
                    "bktMastery": 0.92,
                    "nextReview": "2099-01-01",
                },
                {
                    "id": "ee-series-rlc",
                    "subject": "Electrical Engineering",
                    "chapter": "AC Circuits",
                    "title": "Series RLC Resonant Circuit",
                    "statement": "Inductor L, resistor R, and capacitor C obey L d^2q/dt^2 + R dq/dt + q/C = 0 with quality factor and resonant frequency 1/sqrt(LC).",
                    "kind": "theorem",
                    "status": "planned",
                    "bktMastery": 0.10,
                },
                {
                    "id": "ml-attn-softmax",
                    "subject": "Machine Learning",
                    "chapter": "Transformers",
                    "title": "Attention Softmax Temperature & Saturation",
                    "statement": "Scaled attention logits pass through softmax with vanishing Jacobian under saturation and temperature scaling.",
                    "kind": "theorem",
                    "status": "solid",
                    "bktMastery": 0.88,
                    "nextReview": "2099-01-01",
                },
                {
                    "id": "phys-boltzmann",
                    "subject": "Physics",
                    "chapter": "Statistical Mechanics",
                    "title": "Canonical Boltzmann Distribution & Partition Function",
                    "statement": "Thermal equilibrium microstate probability follows exp(-E_i / k_B T) / Z with Helmholtz free energy.",
                    "kind": "theorem",
                    "status": "planned",
                    "bktMastery": 0.20,
                },
                # Distractors that MUST yield zero false-positive chords
                {
                    "id": "sys-raft-rpc-framing",
                    "subject": "Systems",
                    "chapter": "Raft",
                    "title": "Raft Log Append RPC Byte Framing",
                    "statement": "AppendEntries RPC frames prevLogIndex and entries over length-prefixed TCP packets.",
                    "kind": "definition",
                    "status": "solid",
                    "bktMastery": 0.90,
                    "nextReview": "2099-01-01",
                },
                {
                    "id": "ml-kv-cache-bytes",
                    "subject": "Machine Learning",
                    "chapter": "Inference",
                    "title": "KV Cache Byte Footprint Accounting",
                    "statement": "Key-value tensor allocation scales linearly with batch size, context window, and fp16 width.",
                    "kind": "definition",
                    "status": "solid",
                    "bktMastery": 0.85,
                    "nextReview": "2099-01-01",
                },
            ]
            (kg / "propositions.jsonl").write_text(
                "\n".join(json.dumps(p) for p in seed_props) + "\n",
                encoding="utf-8",
            )
            (kg / "chains.jsonl").write_text("", encoding="utf-8")
            (kg / "mistakes.jsonl").write_text("", encoding="utf-8")

            # 2. discover_isomorphisms(): cross-domain chords & zero false positives
            disc = iso.discover_isomorphisms(kg)
            self.assertTrue(disc["ok"])
            by_pair = {frozenset((c["sourceId"], c["targetId"])): c for c in disc["chords"]}
            shm_rlc = by_pair.get(frozenset(("phys-damped-shm", "ee-series-rlc")))
            self.assertIsNotNone(shm_rlc, f"Expected Damped SHM <-> Series RLC chord, got {disc['chords']}")
            assert shm_rlc is not None
            self.assertEqual(shm_rlc["archetypeId"], "second_order_oscillator")
            self.assertTrue(shm_rlc["variableBijection"])
            self.assertTrue(shm_rlc["breaksAt"])

            sm_boltz = by_pair.get(frozenset(("ml-attn-softmax", "phys-boltzmann")))
            self.assertIsNotNone(sm_boltz, f"Expected Softmax <-> Boltzmann chord, got {disc['chords']}")
            assert sm_boltz is not None
            self.assertEqual(sm_boltz["archetypeId"], "boltzmann_softmax")

            distractors = {"sys-raft-rpc-framing", "ml-kv-cache-bytes"}
            for chord in disc["chords"]:
                self.assertNotIn(chord["sourceId"], distractors, f"False-positive chord on distractor: {chord}")
                self.assertNotIn(chord["targetId"], distractors, f"False-positive chord on distractor: {chord}")

            # 3. link_isomorphism_chord(): idempotent across both orientations & persists relation="isomorphic_to", weight=0.25
            link1 = iso.link_isomorphism_chord(kg, "phys-damped-shm", "ee-series-rlc", "second_order_oscillator")
            self.assertTrue(link1["ok"])
            self.assertTrue(link1["created"])
            self.assertFalse(link1["alreadyExists"])
            self.assertEqual(link1["chord"]["relation"], "isomorphic_to")
            self.assertAlmostEqual(link1["chord"]["encompassWeight"], 0.25, places=4)

            link2 = iso.link_isomorphism_chord(kg, "ee-series-rlc", "phys-damped-shm", "second_order_oscillator")
            self.assertTrue(link2["ok"])
            self.assertFalse(link2["created"])
            self.assertTrue(link2["alreadyExists"])

            persisted_chains = [
                json.loads(ln)
                for ln in (kg / "chains.jsonl").read_text(encoding="utf-8").splitlines()
                if ln.strip()
            ]
            self.assertEqual(len(persisted_chains), 1)
            self.assertEqual(persisted_chains[0]["relation"], "isomorphic_to")
            self.assertAlmostEqual(persisted_chains[0]["encompassWeight"], 0.25, places=4)

            # 4. Cross-domain FIRe credit <= 0.30, never clears misconception twin, SRC never knocks out isomorphic chords
            ref_now = datetime(2026, 11, 26, 12, 0, 0, tzinfo=timezone.utc)
            d_14_ago = (ref_now - timedelta(days=14)).date().isoformat()
            fire_props = [
                {
                    "id": "phys-damped-shm",
                    "status": "solid",
                    "fsrs": {"stability": 10.0, "difficulty": 4.0, "due": ref_now.date().isoformat(), "last_review": d_14_ago},
                },
                {
                    "id": "ee-series-rlc",
                    "status": "shaky",
                    "fsrs": {"stability": 3.0, "difficulty": 5.0, "due": d_14_ago, "last_review": d_14_ago},
                },
                {
                    "id": "ctrl-pid-misc",
                    "status": "misconception",
                    "fsrs": {"stability": 2.0, "difficulty": 6.0, "due": d_14_ago, "last_review": d_14_ago},
                },
            ]
            fire_chains = [
                {
                    "premises": ["phys-damped-shm"],
                    "conclusion": "ee-series-rlc",
                    "relation": "isomorphic_to",
                    "encompassWeight": 0.25,
                    "archetypeId": "second_order_oscillator",
                },
                {
                    "premises": ["phys-damped-shm"],
                    "conclusion": "ctrl-pid-misc",
                    "relation": "isomorphic_to",
                    "encompassWeight": 0.90,  # Must be capped <= 0.30 and blocked on misconception
                    "archetypeId": "second_order_oscillator",
                },
            ]
            credits = fc.apply_fire_credit("phys-damped-shm", "good", fire_props, fire_chains, now=ref_now)
            self.assertEqual(credits.isomorphicRefreshed, 1)
            self.assertEqual(credits.refreshed, 1)
            self.assertEqual(len(credits), 1)
            self.assertEqual(credits[0]["propId"], "ee-series-rlc")
            self.assertTrue(credits[0]["isomorphic"])
            self.assertEqual(credits[0]["relation"], "isomorphic_to")
            self.assertGreater(credits[0]["credit"], 0.0)
            self.assertLessEqual(credits[0]["credit"], 0.30)
            self.assertGreater(fire_props[1]["fsrs"]["stability"], 3.0)
            # Misconception twin MUST remain untouched
            self.assertEqual(fire_props[2]["status"], "misconception")
            self.assertEqual(fire_props[2]["fsrs"]["stability"], 2.0)

            # SRC compress_due_queue MUST NOT knock out isomorphic chords
            due_items = [
                {"id": "phys-damped-shm", "status": "solid"},
                {"id": "ee-series-rlc", "status": "shaky"},
                {"id": "ctrl-pid-misc", "status": "misconception"},
            ]
            comp = fc.compress_due_queue(due_items, fire_chains, min_knockouts=1)
            self.assertEqual(comp["knockedOutCount"], 0)
            self.assertEqual(comp["knockedOut"], [])
            self.assertEqual(len(comp["selected"]), 3)

            # 5. scripts/graph_intel.py --rosetta --json returns clusters, warnings & transfer opportunities
            gi_proc = run_cli([
                sys.executable, "-B", str(SCRIPTS_DIR / "graph_intel.py"),
                "--kg-dir", str(kg), "--rosetta", "--json",
            ])
            self.assertEqual(gi_proc.returncode, 0, f"graph_intel --rosetta failed: {gi_proc.stderr}")
            gi_data = json.loads(gi_proc.stdout)
            ros = gi_data["rosetta"]
            self.assertTrue(ros["ok"])
            cluster_ids = {c["archetypeId"] for c in ros["clusters"]}
            self.assertIn("second_order_oscillator", cluster_ids)
            self.assertIn("boltzmann_softmax", cluster_ids)
            self.assertGreaterEqual(len(ros["negativeTransferWarnings"]), 2)
            self.assertGreaterEqual(len(ros["transferOpportunities"]), 2)

            # 6. brainstem.plan_next_move() attaches isomorphicBridge & enforces <= 180-token envelope
            import syntopik.evidence as ev_mod

            ev_mod.record_evidence(ws, "phys-damped-shm", 3, "derivation_blackout", detail={"verified": True})
            prof_ros = lm.load_profile(kg)
            lm.apply_tier1_calibration(
                prof_ros,
                entry_ramp="balanced",
                challenge_appetite="balanced",
                prose_density="standard",
            )
            lm.save_profile(kg, prof_ros)
            lm.record_telemetry_event(
                kg,
                domain="Physics",
                prop_id="phys-damped-shm",
                correct=True,
                confidence=0.90,
                duration_ms=10000,
            )
            env = bs.plan_next_move(ws)
            self.assertTrue(env["ok"])
            self.assertEqual(env["recommendedMode"], "teach_frontier")
            self.assertEqual(env["targetNodeId"], "ee-series-rlc")
            bridge = env.get("isomorphicBridge")
            self.assertIsInstance(bridge, dict, f"Expected isomorphicBridge in envelope: {env}")
            assert isinstance(bridge, dict)
            self.assertEqual(bridge["knownPropId"], "phys-damped-shm")
            self.assertTrue(bridge.get("archetypeName"))
            self.assertTrue(bridge.get("breaksAt"))
            self.assertTrue(bridge.get("openerPrompt"))
            tok_count = len(re.findall(r"\S+", json.dumps(env, ensure_ascii=False)))
            self.assertLessEqual(tok_count, 180, f"Action Envelope exceeded 180 tokens ({tok_count}): {env}")

    def test_crdt_multi_device_delta_sync_monotonic_commutative_idempotent_and_tamper_proof(self) -> None:
        import copy
        import syntopik.evidence as ev_mod
        import syntopik.learner_model as lm
        import syntopik.sync_delta as sd

        with tempfile.TemporaryDirectory(prefix="synt-v31-sync-") as tmp:
            t = Path(tmp)
            dev_a = t / "device-a"
            dev_b = t / "device-b"
            for ws_root in (dev_a, dev_b):
                (ws_root / "knowledge-graph").mkdir(parents=True)
                (ws_root / "chapters").mkdir(parents=True)

            kg_a, ch_a = dev_a / "knowledge-graph", dev_a / "chapters"
            kg_b, ch_b = dev_b / "knowledge-graph", dev_b / "chapters"

            # Divergent + overlapping propositions
            (kg_a / "propositions.jsonl").write_text(
                "\n".join([
                    json.dumps({
                        "id": "p-shared",
                        "subject": "Physics",
                        "chapter": "SHM",
                        "title": "Restoring Force Law",
                        "status": "solid",
                        "reps": 4,
                        "lastReviewed": "2026-11-24",
                        "updatedAt": "2026-11-24T10:00:00Z",
                        "tags": ["shm", "core"],
                        "cues": [{"prompt": "Why negative sign?", "a": "Always opposes displacement"}],
                        "boundaryConditions": ["small amplitude"],
                        "antiPatterns": ["sign flip"],
                    }),
                    json.dumps({
                        "id": "p-only-a",
                        "subject": "Physics",
                        "chapter": "SHM",
                        "title": "Energy Conservation in SHM",
                        "status": "solid",
                        "reps": 2,
                        "updatedAt": "2026-11-24T11:00:00Z",
                    }),
                ]) + "\n",
                encoding="utf-8",
            )
            (kg_b / "propositions.jsonl").write_text(
                "\n".join([
                    json.dumps({
                        "id": "p-shared",
                        "subject": "Physics",
                        "chapter": "SHM",
                        "title": "Restoring Force Law",
                        "status": "shaky",
                        "reps": 2,
                        "lastReviewed": "2026-11-23",
                        "updatedAt": "2026-11-23T09:00:00Z",
                        "tags": ["physics", "core"],
                        "cues": [
                            {"prompt": "Why negative sign?", "a": "Always opposes displacement"},
                            {"prompt": "Equilibrium position?", "a": "Net force zero"},
                        ],
                        "boundaryConditions": ["linear spring"],
                        "antiPatterns": ["unit trap"],
                    }),
                    json.dumps({
                        "id": "p-only-b",
                        "subject": "Maths",
                        "chapter": "ODEs",
                        "title": "Characteristic Roots",
                        "status": "shaky",
                        "reps": 1,
                        "updatedAt": "2026-11-24T12:00:00Z",
                    }),
                ]) + "\n",
                encoding="utf-8",
            )

            # Overlapping + disjoint chains
            shared_chain = {"premises": ["p-shared"], "conclusion": "p-only-a", "relation": "derives", "encompassWeight": 0.6}
            (kg_a / "chains.jsonl").write_text(json.dumps(shared_chain) + "\n", encoding="utf-8")
            (kg_b / "chains.jsonl").write_text(
                "\n".join([
                    json.dumps(shared_chain),
                    json.dumps({"premises": ["p-only-b"], "conclusion": "p-shared", "relation": "supports", "encompassWeight": 0.4}),
                ]) + "\n",
                encoding="utf-8",
            )

            # Overlapping + disjoint revlog & mistakes
            shared_rev = {"propId": "p-shared", "ts": "2026-11-20T09:00:00Z", "rating": "good", "correct": True, "learnerConfidence": 0.85, "durationMs": 9000}
            (kg_a / "revlog.jsonl").write_text(
                "\n".join([
                    json.dumps(shared_rev),
                    json.dumps({"propId": "p-only-a", "ts": "2026-11-21T09:00:00Z", "rating": "good", "correct": True, "learnerConfidence": 0.90, "durationMs": 8000}),
                ]) + "\n",
                encoding="utf-8",
            )
            (kg_b / "revlog.jsonl").write_text(
                "\n".join([
                    json.dumps(shared_rev),
                    json.dumps({"propId": "p-only-b", "ts": "2026-11-22T09:00:00Z", "rating": "again", "correct": False, "learnerConfidence": 0.70, "durationMs": 14000}),
                ]) + "\n",
                encoding="utf-8",
            )

            shared_mist = {"id": "m-shared01", "date": "2026-11-20", "subject": "Physics", "chapter": "SHM", "category": "sign_error", "triggerCue": "spring", "wrongMove": "+kx", "correctMove": "-kx"}
            (kg_a / "mistakes.jsonl").write_text(
                "\n".join([
                    json.dumps(shared_mist),
                    json.dumps({"id": "m-onlya001", "date": "2026-11-21", "subject": "Physics", "chapter": "SHM", "category": "unit_trap", "triggerCue": "cm", "wrongMove": "cm", "correctMove": "m"}),
                ]) + "\n",
                encoding="utf-8",
            )
            (kg_b / "mistakes.jsonl").write_text(
                "\n".join([
                    json.dumps(shared_mist),
                    json.dumps({"id": "m-onlyb002", "date": "2026-11-22", "subject": "Maths", "chapter": "ODEs", "category": "algebra_slip", "triggerCue": "roots", "wrongMove": "real", "correctMove": "complex"}),
                ]) + "\n",
                encoding="utf-8",
            )

            # Evidence ledger chains (overlapping + disjoint)
            ev_mod.record_evidence(dev_a, "p-shared", 2, "review", detail={"ok": True})
            shutil.copy2(kg_a / "evidence-ledger.jsonl", kg_b / "evidence-ledger.jsonl")
            ev_mod.record_evidence(dev_a, "p-only-a", 3, "fork_replay", detail={"win": True})
            ev_mod.record_evidence(dev_b, "p-only-b", 2, "quiz", detail={"ok": True})

            # Monotonic join-semilattice trackers: A has t1=mastered/q1=solved, B has t1=unseen/q1=unseen!
            (ch_a / "shm-tracker.json").write_text(
                json.dumps({
                    "schemaVersion": 1,
                    "slug": "shm",
                    "title": "Simple Harmonic Motion",
                    "subject": "Physics",
                    "active": True,
                    "createdAt": "2026-11-18T08:00:00Z",
                    "updatedAt": "2026-11-24T10:00:00Z",
                    "topics": [
                        {"id": "t1", "order": 1, "title": "Restoring Law", "status": "mastered"},
                        {"id": "t2", "order": 2, "title": "Energy", "status": "unseen"},
                    ],
                    "questions": [
                        {"qid": "q1", "topicId": "t1", "status": "solved", "attempts": 2, "lastDate": "2026-11-24"},
                        {"qid": "q2", "topicId": "t2", "status": "unseen", "attempts": 0},
                    ],
                    "milestones": {"sourcesIndexed": True, "probed": False},
                }),
                encoding="utf-8",
            )
            (ch_b / "shm-tracker.json").write_text(
                json.dumps({
                    "schemaVersion": 1,
                    "slug": "shm",
                    "title": "Simple Harmonic Motion",
                    "subject": "Physics",
                    "active": True,
                    "createdAt": "2026-11-19T08:00:00Z",
                    "updatedAt": "2026-11-24T12:00:00Z",
                    "topics": [
                        {"id": "t1", "order": 1, "title": "Restoring Law", "status": "unseen"},
                        {"id": "t2", "order": 2, "title": "Energy", "status": "practiced"},
                    ],
                    "questions": [
                        {"qid": "q1", "topicId": "t1", "status": "unseen", "attempts": 0},
                        {"qid": "q2", "topicId": "t2", "status": "retest", "attempts": 1, "lastDate": "2026-11-23"},
                    ],
                    "milestones": {"sourcesIndexed": False, "probed": True},
                }),
                encoding="utf-8",
            )

            # Divergent learner profiles (A calibrated + Physics events; B uncalibrated + Physics & Maths events)
            prof_a = lm.load_profile(kg_a)
            lm.apply_tier1_calibration(prof_a, entry_ramp="formalism_first", challenge_appetite="balanced", prose_density="terse")
            lm.save_profile(kg_a, prof_a)
            lm.record_telemetry_event(kg_a, domain="Physics", prop_id="p-shared", correct=True, confidence=0.85, duration_ms=9000, now="2026-11-20T09:00:00Z")
            lm.record_telemetry_event(kg_a, domain="Physics", prop_id="p-only-a", correct=True, confidence=0.90, duration_ms=8000, now="2026-11-21T09:00:00Z")

            lm.record_telemetry_event(kg_b, domain="Physics", prop_id="p-shared", correct=True, confidence=0.80, duration_ms=11000, now="2026-11-22T08:00:00Z")
            lm.record_telemetry_event(kg_b, domain="Maths", prop_id="p-only-b", correct=False, confidence=0.70, duration_ms=14000, now="2026-11-22T09:00:00Z")

            # Export & verify both deltas
            delta_a_path = t / "delta-a.json"
            delta_b_path = t / "delta-b.json"
            exp_a = sd.export_sync_delta(dev_a, out_path=delta_a_path, device_id="device-alpha-01")
            exp_b = sd.export_sync_delta(dev_b, out_path=delta_b_path, device_id="device-beta-02")
            self.assertTrue(exp_a["ok"] and exp_b["ok"])
            self.assertTrue(sd.verify_sync_delta(delta_a_path)["ok"])
            self.assertTrue(sd.verify_sync_delta(delta_b_path)["ok"])

            # Fail-closed security checks: tampered hash, missing hash, ../escape tracker slug, oversized payload
            tampered = copy.deepcopy(exp_a["delta"])
            tampered["payload"]["propositions"][0]["title"] = "TAMPERED_TITLE"
            v_tamp = sd.verify_sync_delta(tampered)
            self.assertFalse(v_tamp["ok"])
            self.assertTrue(any("contentSha256" in e for e in v_tamp["errors"]))
            m_tamp = sd.merge_sync_delta(dev_b, tampered)
            self.assertFalse(m_tamp["ok"])

            missing_hash = copy.deepcopy(exp_a["delta"])
            missing_hash.pop("contentSha256", None)
            v_miss = sd.verify_sync_delta(missing_hash)
            self.assertFalse(v_miss["ok"])

            bad_slug = copy.deepcopy(exp_a["delta"])
            bad_slug["payload"]["trackers"]["../escape"] = {"slug": "../escape", "topics": []}
            bad_slug["contentSha256"] = sd._compute_delta_sha256(bad_slug)
            v_slug = sd.verify_sync_delta(bad_slug)
            self.assertFalse(v_slug["ok"])
            self.assertTrue(any("slug" in e.lower() for e in v_slug["errors"]))
            m_slug = sd.merge_sync_delta(dev_b, bad_slug)
            self.assertFalse(m_slug["ok"])
            self.assertFalse((dev_b / "escape-tracker.json").exists())

            oversized_path = t / "oversized-delta.json"
            oversized_path.write_bytes(b" " * (sd.MAX_DELTA_BYTES + 128))
            v_over = sd.verify_sync_delta(oversized_path)
            self.assertFalse(v_over["ok"])
            self.assertTrue(any("MAX_DELTA_BYTES" in e for e in v_over["errors"]))

            # Commutative merge: ws_ab = A (+) B, ws_ba = B (+) A
            ws_ab = t / "ws-ab"
            ws_ba = t / "ws-ba"
            shutil.copytree(dev_a, ws_ab)
            shutil.copytree(dev_b, ws_ba)

            m_ab = sd.merge_sync_delta(ws_ab, delta_b_path)
            m_ba = sd.merge_sync_delta(ws_ba, delta_a_path)
            self.assertTrue(m_ab["ok"], m_ab)
            self.assertTrue(m_ba["ok"], m_ba)
            self.assertGreater(m_ab["added"], 0)
            self.assertGreater(m_ba["added"], 0)

            # Monotonicity: mastered/solved NEVER regressed, milestones ORed
            for ws_m in (ws_ab, ws_ba):
                trk = json.loads((ws_m / "chapters" / "shm-tracker.json").read_text(encoding="utf-8"))
                topics_by_id = {x["id"]: x for x in trk["topics"]}
                qs_by_id = {x["qid"]: x for x in trk["questions"]}
                self.assertEqual(topics_by_id["t1"]["status"], "mastered")
                self.assertEqual(topics_by_id["t2"]["status"], "practiced")
                self.assertEqual(qs_by_id["q1"]["status"], "solved")
                self.assertEqual(qs_by_id["q2"]["status"], "retest")
                self.assertEqual(trk["milestones"], {"probed": True, "sourcesIndexed": True})

                props_m = {
                    r["id"]: r
                    for r in (json.loads(ln) for ln in (ws_m / "knowledge-graph" / "propositions.jsonl").read_text(encoding="utf-8").splitlines() if ln.strip())
                }
                self.assertEqual(set(props_m.keys()), {"p-shared", "p-only-a", "p-only-b"})
                self.assertEqual(props_m["p-shared"]["reps"], 4)
                self.assertEqual(set(props_m["p-shared"]["tags"]), {"core", "physics", "shm"})
                self.assertEqual(len(props_m["p-shared"]["cues"]), 2)
                self.assertEqual(set(props_m["p-shared"]["boundaryConditions"]), {"linear spring", "small amplitude"})
                self.assertEqual(set(props_m["p-shared"]["antiPatterns"]), {"sign flip", "unit trap"})

            # Exact Commutativity (A (+) B == B (+) A) across ledgers, propositions, chains, trackers & profile
            for rel_file in (
                "knowledge-graph/propositions.jsonl",
                "knowledge-graph/chains.jsonl",
                "knowledge-graph/revlog.jsonl",
                "knowledge-graph/mistakes.jsonl",
                "knowledge-graph/evidence-ledger.jsonl",
                "chapters/shm-tracker.json",
            ):
                with self.subTest(commutativity_file=rel_file):
                    self.assertEqual(
                        (ws_ab / rel_file).read_text(encoding="utf-8"),
                        (ws_ba / rel_file).read_text(encoding="utf-8"),
                        f"Commutativity mismatch in {rel_file}",
                    )
            prof_ab = json.loads((ws_ab / "knowledge-graph" / "learner-profile.json").read_text(encoding="utf-8"))
            prof_ba = json.loads((ws_ba / "knowledge-graph" / "learner-profile.json").read_text(encoding="utf-8"))
            prof_ab.pop("updatedAt", None)
            prof_ba.pop("updatedAt", None)
            self.assertEqual(prof_ab, prof_ba, "Merged learner-profile.json not commutative")
            self.assertTrue(prof_ab["calibration"]["completed"])
            self.assertEqual(prof_ab["domains"]["Physics"]["n"], 3)
            self.assertEqual(prof_ab["domains"]["Maths"]["n"], 1)

            # Exact Idempotence on second merge (added == 0, updated == 0)
            m_ab_2 = sd.merge_sync_delta(ws_ab, delta_b_path)
            self.assertTrue(m_ab_2["ok"])
            self.assertEqual(m_ab_2["added"], 0)
            self.assertEqual(m_ab_2["updated"], 0)
            self.assertTrue(m_ab_2["idempotentNoop"])

            # Merged evidence-ledger.jsonl passes SHA-256 chain verification
            ev_check_ab = ev_mod.verify_ledger_chain(ws_ab / "knowledge-graph")
            ev_check_ba = ev_mod.verify_ledger_chain(ws_ba / "knowledge-graph")
            self.assertTrue(ev_check_ab["ok"], ev_check_ab)
            self.assertTrue(ev_check_ba["ok"], ev_check_ba)
            self.assertEqual(ev_check_ab["count"], 3)
            self.assertEqual(ev_check_ab["headHash"], ev_check_ba["headHash"])

    def test_longitudinal_telemetry_replay_and_bounded_hyperparam_adoption(self) -> None:
        import syntopik.learner_model as lm
        import syntopik.sim_eval as se

        # 1. Strict cognitive bounds enforcement in learner_model.resolve_calibrated_hyperparams
        self.assertEqual(lm.resolve_calibrated_hyperparams(None), lm.DEFAULT_CALIBRATED_HYPERPARAMS)
        hi_clamped = lm.resolve_calibrated_hyperparams({
            "calibratedHyperparams": {
                "eloKBase": 999.0,
                "erNoviceCutoff": 0.95,
                "erChallengeCutoff": 0.99,
                "fatigueMultiplier": 5.0,
            }
        })
        self.assertEqual(hi_clamped["eloKBase"], 96.0)
        self.assertEqual(hi_clamped["erNoviceCutoff"], 0.45)
        self.assertEqual(hi_clamped["erChallengeCutoff"], 0.88)
        self.assertEqual(hi_clamped["fatigueMultiplier"], 1.50)

        lo_nan_clamped = lm.resolve_calibrated_hyperparams({
            "calibratedHyperparams": {
                "eloKBase": -10.0,
                "erNoviceCutoff": float("nan"),
                "erChallengeCutoff": 0.10,
                "fatigueMultiplier": float("inf"),
            }
        })
        self.assertEqual(lo_nan_clamped["eloKBase"], 32.0)
        self.assertEqual(lo_nan_clamped["erNoviceCutoff"], lm.DEFAULT_CALIBRATED_HYPERPARAMS["erNoviceCutoff"])
        self.assertEqual(lo_nan_clamped["erChallengeCutoff"], 0.72)
        self.assertEqual(lo_nan_clamped["fatigueMultiplier"], lm.DEFAULT_CALIBRATED_HYPERPARAMS["fatigueMultiplier"])

        bench_script = ROOT / "benchmarks" / "syntopik_bench.py"
        base_dt = datetime(2026, 10, 1, 9, 0, 0, tzinfo=timezone.utc)

        with tempfile.TemporaryDirectory(prefix="synt-v31-replay-") as tmp:
            t = Path(tmp)

            # 2. Insufficient data (5 events) -> {ok: True, insufficientData: True, applied: False}
            ws_small = t / "ws-small"
            kg_small = ws_small / "knowledge-graph"
            kg_small.mkdir(parents=True)
            small_rows = [
                {
                    "propId": f"p-{i}",
                    "domain": "Physics",
                    "ts": (base_dt + timedelta(days=i)).isoformat().replace("+00:00", "Z"),
                    "rating": "good",
                    "correct": True,
                    "learnerConfidence": 0.85,
                    "difficulty": 1500,
                    "hintsUsed": 0,
                    "durationMs": 10000,
                }
                for i in range(5)
            ]
            (kg_small / "revlog.jsonl").write_text("\n".join(json.dumps(r) for r in small_rows) + "\n", encoding="utf-8")
            res_small = se.replay_learner_history(ws_small, apply_if_improved=True)
            self.assertTrue(res_small["ok"])
            self.assertTrue(res_small["insufficientData"])
            self.assertEqual(res_small["eventCount"], 5)
            self.assertFalse(res_small["applied"])

            # 3. 30 chronological improving events via CLI -> 70/30 split, OOS gain >= 0.005, bounded adoption
            ws_imp = t / "ws-improving"
            kg_imp = ws_imp / "knowledge-graph"
            kg_imp.mkdir(parents=True)
            imp_rows = [
                {
                    "propId": f"p-imp-{i % 6}",
                    "domain": "Physics",
                    "ts": (base_dt + timedelta(days=i)).isoformat().replace("+00:00", "Z"),
                    "rating": "good",
                    "correct": True,
                    "learnerConfidence": 0.92,
                    "difficulty": 1680,
                    "hintsUsed": 0,
                    "durationMs": 9000,
                }
                for i in range(30)
            ]
            (kg_imp / "revlog.jsonl").write_text("\n".join(json.dumps(r) for r in imp_rows) + "\n", encoding="utf-8")
            p_imp = run_cli([
                sys.executable, "-B", str(bench_script),
                "--replay", "--workspace", str(ws_imp), "--apply", "--json",
            ])
            self.assertEqual(p_imp.returncode, 0, f"bench --replay improving failed: {p_imp.stderr}")
            r_imp = json.loads(p_imp.stdout)
            self.assertTrue(r_imp["ok"])
            self.assertFalse(r_imp["insufficientData"])
            self.assertEqual(r_imp["split"]["method"], "chronological_time_series_70_30")
            self.assertEqual(r_imp["split"]["trainEvents"], 21)
            self.assertEqual(r_imp["split"]["testEvents"], 9)
            self.assertTrue(r_imp["improved"], r_imp)
            self.assertGreaterEqual(r_imp["oosImprovement"], 0.005)
            self.assertTrue(r_imp["applied"])

            prof_imp = lm.load_profile(kg_imp)
            cal_hp = prof_imp.get("calibratedHyperparams")
            self.assertIsInstance(cal_hp, dict)
            resolved_hp = lm.resolve_calibrated_hyperparams(prof_imp)
            for key, (lo, hi) in lm.CALIBRATED_HYPERPARAM_BOUNDS.items():
                self.assertGreaterEqual(resolved_hp[key], lo)
                self.assertLessEqual(resolved_hp[key], hi)
                self.assertAlmostEqual(resolved_hp[key], r_imp["candidateParams"][key], places=4)

            # 4. 30 chronological regime-shift events -> non-improving OOS shift MUST be rejected (applied=False)
            ws_shift = t / "ws-shift"
            kg_shift = ws_shift / "knowledge-graph"
            kg_shift.mkdir(parents=True)
            shift_rows = []
            for i in range(30):
                if i < 21:
                    shift_rows.append({
                        "propId": f"p-sh-{i % 5}",
                        "domain": "Physics",
                        "ts": (base_dt + timedelta(days=i)).isoformat().replace("+00:00", "Z"),
                        "rating": "good",
                        "correct": True,
                        "learnerConfidence": 0.95,
                        "difficulty": 1680,
                        "hintsUsed": 0,
                        "durationMs": 8500,
                    })
                else:
                    shift_rows.append({
                        "propId": f"p-sh-{i % 5}",
                        "domain": "Physics",
                        "ts": (base_dt + timedelta(days=i)).isoformat().replace("+00:00", "Z"),
                        "rating": "again",
                        "correct": False,
                        "learnerConfidence": 0.85,
                        "difficulty": 1680,
                        "hintsUsed": 3,
                        "durationMs": 45000,
                    })
            (kg_shift / "revlog.jsonl").write_text("\n".join(json.dumps(r) for r in shift_rows) + "\n", encoding="utf-8")
            p_shift = run_cli([
                sys.executable, "-B", str(bench_script),
                "--replay", "--workspace", str(ws_shift), "--apply", "--json",
            ])
            self.assertEqual(p_shift.returncode, 0, f"bench --replay shift failed: {p_shift.stderr}")
            r_shift = json.loads(p_shift.stdout)
            self.assertTrue(r_shift["ok"])
            self.assertFalse(r_shift["insufficientData"])
            self.assertFalse(r_shift["improved"], r_shift)
            self.assertLess(r_shift["oosImprovement"], 0.005)
            self.assertFalse(r_shift["applied"])
            self.assertNotIn("calibratedHyperparams", lm.load_profile(kg_shift))

    def test_proof_graph_handwriting_diff_fork_spawn_and_v31_cli_and_server(self) -> None:
        import copy
        import syntopik.server as srv_mod
        import syntopik.sync_delta as sd

        df = load_script_module("derivation_forge.py")

        def _req(
            port: int,
            method: str,
            path: str,
            *,
            headers: dict[str, str] | None = None,
            body: Any = None,
        ) -> tuple[int, dict[str, str], bytes]:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=8.0)
            try:
                hdrs = dict(headers or {})
                raw = None
                if body is not None:
                    raw = json.dumps(body).encode("utf-8") if isinstance(body, (dict, list)) else body
                    hdrs.setdefault("Content-Type", "application/json")
                conn.request(method, path, body=raw, headers=hdrs)
                resp = conn.getresponse()
                data = resp.read()
                return resp.status, {k.lower(): v for k, v in resp.getheaders()}, data
            finally:
                conn.close()

        with tempfile.TemporaryDirectory(prefix="synt-v31-pdiff-") as tmp:
            t = Path(tmp)
            ws = t / "ws"
            kg = ws / "knowledge-graph"
            ch = ws / "chapters"
            kg.mkdir(parents=True)
            ch.mkdir(parents=True)

            (kg / "propositions.jsonl").write_text(
                "\n".join([
                    json.dumps({
                        "id": "p-hooke-sign",
                        "subject": "Physics",
                        "chapter": "SHM",
                        "title": "Hooke's Law Restoring Sign",
                        "kind": "axiom",
                        "status": "shaky",
                        "bktMastery": 0.35,
                    }),
                    json.dumps({
                        "id": "p-shm-osc",
                        "subject": "Physics",
                        "chapter": "SHM",
                        "title": "Damped Simple Harmonic Motion (SHM) Oscillator",
                        "statement": "Restoring force and viscous damping satisfy m x'' + b x' + k x = 0 with resonance.",
                        "kind": "theorem",
                        "status": "shaky",
                        "bktMastery": 0.40,
                    }),
                    json.dumps({
                        "id": "ee-series-rlc",
                        "subject": "Electrical Engineering",
                        "chapter": "AC Circuits",
                        "title": "Series RLC Resonant Circuit",
                        "statement": "Inductor L, resistor R, capacitor C obey L d^2q/dt^2 + R dq/dt + q/C = 0 with resonant frequency 1/sqrt(LC).",
                        "kind": "theorem",
                        "status": "solid",
                        "bktMastery": 0.90,
                    }),
                ]) + "\n",
                encoding="utf-8",
            )
            (kg / "chains.jsonl").write_text(
                json.dumps({"premises": ["p-hooke-sign"], "conclusion": "p-shm-osc", "relation": "derives"}) + "\n",
                encoding="utf-8",
            )
            (kg / "mistakes.jsonl").write_text("", encoding="utf-8")

            df.create_derivation(
                {
                    "id": "deriv-shm-diff",
                    "propId": "p-shm-osc",
                    "chapter": "SHM",
                    "title": "Spring-Block SHM Angular Frequency",
                    "domain": "Physics",
                    "steps": [
                        {"id": "a1", "role": "axiom", "label": "Hooke's Law", "expr": "F = -k*x", "dependsOn": []},
                        {"id": "a2", "role": "axiom", "label": "Newton Second Law", "expr": "F = m*a", "dependsOn": []},
                        {"id": "s1", "role": "step", "label": "Acceleration Law", "expr": "a = -(k/m)*x", "dependsOn": ["a1", "a2"]},
                        {"id": "s2", "role": "theorem", "label": "Angular Frequency", "expr": "omega = sqrt(k/m)", "dependsOn": ["s1"]},
                    ],
                },
                kg_dir=kg,
                workspace=ws,
            )

            # 1. Clean proof-diff pass
            clean_steps = [
                {"line": 1, "expr": "F = -k*x"},
                {"line": 2, "expr": "m*a = F"},
                {"line": 3, "expr": "a = -k*x/m"},
                {"line": 4, "expr": "omega = sqrt(k/m)"},
            ]
            ok_diff = df.diff_handwritten_steps(kg, "deriv-shm-diff", clean_steps, auto_spawn_fork=False, workspace=ws)
            self.assertTrue(ok_diff["ok"])
            self.assertTrue(ok_diff["allVerified"])
            self.assertIsNone(ok_diff["earliestBreakLine"])
            self.assertEqual(ok_diff["verifiedCount"], 4)

            # 2. Line-3 sign flip -> earliestBreakLine=3, errorKind="sign_flip", zero-leak R0-R2, Blunder Fork + diagnosticProbe
            flip_steps = [
                {"line": 1, "expr": "F = -k*x"},
                {"line": 2, "expr": "F = m*a"},
                {"line": 3, "expr": "a = (k/m)*x"},
                {"line": 4, "expr": "omega = sqrt(k/m)"},
            ]
            bad_diff = df.diff_handwritten_steps(kg, "deriv-shm-diff", flip_steps, auto_spawn_fork=True, workspace=ws)
            self.assertTrue(bad_diff["ok"])
            self.assertFalse(bad_diff["allVerified"])
            self.assertEqual(bad_diff["earliestBreakLine"], 3)
            self.assertEqual(bad_diff["earliestBreakStepId"], "s1")
            self.assertEqual(bad_diff["errorKind"], "sign_flip")
            self.assertEqual(bad_diff["mistakeCategory"], "sign_error")
            self.assertEqual([s["status"] for s in bad_diff["steps"]], ["verified", "verified", "broken", "skipped_dependency"])

            nudges = bad_diff.get("nudges") or {}
            for lvl in ("R0", "R1", "R2"):
                self.assertTrue(str(nudges.get(lvl) or "").strip(), f"Missing nudge {lvl}")
                self.assertNotIn("-(k/m)*x", nudges[lvl], f"Nudge {lvl} leaked canonical target expr!")
                self.assertNotIn("-k*x/m", nudges[lvl], f"Nudge {lvl} leaked canonical target expr!")

            fork_rec = bad_diff.get("spawnedFork")
            self.assertIsInstance(fork_rec, dict)
            assert isinstance(fork_rec, dict)
            self.assertTrue(fork_rec.get("id"))
            forks_disk = [
                json.loads(ln)
                for ln in (kg / "forks.jsonl").read_text(encoding="utf-8").splitlines()
                if ln.strip()
            ]
            self.assertIn(fork_rec["id"], {f.get("id") for f in forks_disk})
            self.assertEqual(bad_diff["verifiedPrefix"], ["F = -k*x", "F = m*a"])

            probe = bad_diff.get("diagnosticProbe")
            self.assertIsInstance(probe, dict)
            assert isinstance(probe, dict)
            self.assertEqual((probe.get("bestProbe") or {}).get("propId"), "p-hooke-sign")

            # 3. AST injection refusal & > 40 step bound
            canary_path = t / "pdiff-ast-pwned.txt"
            ast_res = df.diff_handwritten_steps(
                kg,
                "deriv-shm-diff",
                [{"line": 1, "expr": f"__import__('os').system('touch {canary_path}')"}],
                auto_spawn_fork=False,
                workspace=ws,
            )
            self.assertFalse(ast_res["allVerified"])
            self.assertEqual(ast_res["earliestBreakLine"], 1)
            self.assertFalse(canary_path.exists(), "AST injection executed in diff_handwritten_steps!")

            with self.assertRaises(ValueError):
                df.diff_handwritten_steps(
                    kg,
                    "deriv-shm-diff",
                    [{"line": i + 1, "expr": "x = 1"} for i in range(41)],
                    auto_spawn_fork=False,
                    workspace=ws,
                )

            # 4. v3.1+ Python & Node CLI verbs (--version == 3.2.0, rosetta, sync-delta, calibrate-policy, proof-diff)
            py_cli = [sys.executable, "-B", "-m", "syntopik.cli"]
            node_cli = ["node", str(ROOT / "bin" / "syntopik.mjs")]

            py_ver = run_cli([*py_cli, "--version"])
            self.assertEqual(py_ver.returncode, 0, py_ver.stderr)
            self.assertIn("3.2.0", py_ver.stdout)
            node_ver = run_cli([*node_cli, "--version"])
            self.assertEqual(node_ver.returncode, 0, node_ver.stderr)
            self.assertIn("3.2.0", node_ver.stdout)

            p_arch = run_cli([*py_cli, "rosetta", "archetypes", "--json"])
            self.assertEqual(p_arch.returncode, 0, p_arch.stderr)
            self.assertGreaterEqual(json.loads(p_arch.stdout)["count"], 10)

            p_scan = run_cli([*node_cli, "rosetta", "scan", "--workspace", str(ws), "--json"])
            self.assertEqual(p_scan.returncode, 0, p_scan.stderr)
            self.assertGreaterEqual(json.loads(p_scan.stdout)["chordCount"], 1)

            p_link = run_cli([
                *py_cli, "rosetta", "link",
                "--source", "p-shm-osc", "--target", "ee-series-rlc", "--archetype", "second_order_oscillator",
                "--workspace", str(ws), "--json",
            ])
            self.assertEqual(p_link.returncode, 0, p_link.stderr)
            self.assertTrue(json.loads(p_link.stdout)["ok"])

            p_br = run_cli([*py_cli, "rosetta", "bridge", "--prop-id", "p-shm-osc", "--workspace", str(ws), "--json"])
            self.assertEqual(p_br.returncode, 0, p_br.stderr)
            self.assertTrue(json.loads(p_br.stdout)["found"])

            cli_delta = t / "cli-delta.json"
            p_sd_st = run_cli([*node_cli, "sync-delta", "status", "--workspace", str(ws), "--json"])
            self.assertEqual(p_sd_st.returncode, 0, p_sd_st.stderr)
            self.assertTrue(json.loads(p_sd_st.stdout)["ok"])

            p_sd_ex = run_cli([*py_cli, "sync-delta", "export", "--workspace", str(ws), "--out", str(cli_delta), "--json"])
            self.assertEqual(p_sd_ex.returncode, 0, p_sd_ex.stderr)
            self.assertTrue(cli_delta.is_file())

            p_sd_vr = run_cli([*py_cli, "sync-delta", "verify", "--file", str(cli_delta), "--json"])
            self.assertEqual(p_sd_vr.returncode, 0, p_sd_vr.stderr)
            self.assertTrue(json.loads(p_sd_vr.stdout)["ok"])

            p_sd_mg = run_cli([*py_cli, "sync-delta", "merge", "--workspace", str(ws), "--file", str(cli_delta), "--dry-run", "--json"])
            self.assertEqual(p_sd_mg.returncode, 0, p_sd_mg.stderr)
            self.assertTrue(json.loads(p_sd_mg.stdout)["ok"])

            p_cal = run_cli([*node_cli, "calibrate-policy", "--workspace", str(ws), "--json"])
            self.assertEqual(p_cal.returncode, 0, p_cal.stderr)
            cal_j = json.loads(p_cal.stdout)
            self.assertTrue(cal_j["ok"])
            self.assertTrue(cal_j["insufficientData"])

            steps_file = t / "flip-steps.json"
            steps_file.write_text(json.dumps(flip_steps), encoding="utf-8")
            p_pd = run_cli([
                *node_cli, "proof-diff",
                "--id", "deriv-shm-diff", "--steps-json", str(steps_file),
                "--no-spawn-fork", "--workspace", str(ws), "--json",
            ])
            self.assertEqual(p_pd.returncode, 0, p_pd.stderr)
            pd_j = json.loads(p_pd.stdout)
            self.assertEqual(pd_j["earliestBreakLine"], 3)
            self.assertEqual(pd_j["errorKind"], "sign_flip")

            # 5. Live syntopik/server.py v3.1 endpoints: 200 OK, CSRF 403, tampered delta 400 & --lan cookie 403
            srv = srv_mod.create_server(workspace=ws, host="127.0.0.1", port=0, lan=False)
            t_srv = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
            t_srv.start()
            try:
                port = srv.bound_port
                auth = {"X-Syntopik-Token": srv.csrf_token}

                # GET /api/rosetta & POST /api/rosetta/link
                st_r, _, raw_r = _req(port, "GET", "/api/rosetta")
                self.assertEqual(st_r, 200, raw_r)
                r_j = json.loads(raw_r)
                self.assertTrue(r_j["ok"])
                self.assertGreaterEqual(len(r_j.get("chords") or []), 1)

                st_rl_csrf, _, _ = _req(
                    port, "POST", "/api/rosetta/link",
                    body={"sourceId": "p-shm-osc", "targetId": "ee-series-rlc", "archetypeId": "second_order_oscillator"},
                )
                self.assertEqual(st_rl_csrf, 403)
                st_rl, _, raw_rl = _req(
                    port, "POST", "/api/rosetta/link", headers=auth,
                    body={"sourceId": "p-shm-osc", "targetId": "ee-series-rlc", "archetypeId": "second_order_oscillator"},
                )
                self.assertEqual(st_rl, 200, raw_rl)
                self.assertTrue(json.loads(raw_rl)["ok"])

                # GET /api/sync/status, POST /api/sync/export, POST /api/sync/merge (+ tampered 400)
                st_ss, _, raw_ss = _req(port, "GET", "/api/sync/status")
                self.assertEqual(st_ss, 200, raw_ss)
                self.assertTrue(json.loads(raw_ss)["ok"])

                st_se_csrf, _, _ = _req(port, "POST", "/api/sync/export", body={})
                self.assertEqual(st_se_csrf, 403)
                st_se, _, raw_se = _req(port, "POST", "/api/sync/export", headers=auth, body={})
                self.assertEqual(st_se, 200, raw_se)
                se_j = json.loads(raw_se)
                self.assertTrue(se_j["ok"])
                exported_delta = se_j["delta"]
                self.assertTrue(sd.verify_sync_delta(exported_delta)["ok"])

                st_sm_csrf, _, _ = _req(port, "POST", "/api/sync/merge", body={"delta": exported_delta, "dryRun": True})
                self.assertEqual(st_sm_csrf, 403)

                bad_delta = copy.deepcopy(exported_delta)
                bad_delta["contentSha256"] = "0" * 64
                st_sm_bad, _, _ = _req(
                    port, "POST", "/api/sync/merge", headers=auth,
                    body={"delta": bad_delta, "dryRun": False},
                )
                self.assertEqual(st_sm_bad, 400)

                st_sm_ok, _, raw_sm_ok = _req(
                    port, "POST", "/api/sync/merge", headers=auth,
                    body={"delta": exported_delta, "dryRun": True},
                )
                self.assertEqual(st_sm_ok, 200, raw_sm_ok)
                self.assertTrue(json.loads(raw_sm_ok)["ok"])

                # POST /api/profile/replay
                st_pr_csrf, _, _ = _req(port, "POST", "/api/profile/replay", body={"apply": False})
                self.assertEqual(st_pr_csrf, 403)
                st_pr, _, raw_pr = _req(port, "POST", "/api/profile/replay", headers=auth, body={"apply": False})
                self.assertEqual(st_pr, 200, raw_pr)
                self.assertTrue(json.loads(raw_pr)["ok"])

                # POST /api/derivation/diff (CSRF 403 + 200 OK + zero leak)
                diff_body = {
                    "derivationId": "deriv-shm-diff",
                    "studentSteps": flip_steps,
                    "autoSpawnFork": False,
                }
                st_dd_csrf, _, _ = _req(port, "POST", "/api/derivation/diff", body=diff_body)
                self.assertEqual(st_dd_csrf, 403)
                st_dd, _, raw_dd = _req(port, "POST", "/api/derivation/diff", headers=auth, body=diff_body)
                self.assertEqual(st_dd, 200, raw_dd)
                dd_text = raw_dd.decode("utf-8")
                self.assertNotIn("-(k/m)*x", dd_text, "POST /api/derivation/diff leaked target step expr!")
                dd_j = json.loads(dd_text)
                self.assertTrue(dd_j["ok"])
                self.assertEqual(dd_j["earliestBreakLine"], 3)
                self.assertEqual(dd_j["errorKind"], "sign_flip")
            finally:
                srv.shutdown()
                srv.server_close()
                t_srv.join(timeout=3.0)

            # 6. --lan paired-cookie isolation: ALL 7 v3.1 routes MUST return 403
            srv_lan = srv_mod.create_server(workspace=ws, host="127.0.0.1", port=0, lan=True)
            t_lan = threading.Thread(target=srv_lan.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
            t_lan.start()
            try:
                lport = srv_lan.bound_port
                lan_host = f"192.168.1.88:{lport}"
                st_pair, hdrs_pair, _ = _req(lport, "GET", f"/pocket?pair={srv_lan.pair_token}", headers={"Host": lan_host})
                self.assertEqual(st_pair, 302)
                cookie = hdrs_pair["set-cookie"].split(";", 1)[0]

                lan_get_hdrs = {"Host": lan_host, "Cookie": cookie}
                lan_post_hdrs = {"Host": lan_host, "Origin": f"http://{lan_host}", "Cookie": cookie}
                for method, path, payload in (
                    ("GET", "/api/rosetta", None),
                    ("POST", "/api/rosetta/link", {"sourceId": "p-shm-osc", "targetId": "ee-series-rlc", "archetypeId": "second_order_oscillator"}),
                    ("GET", "/api/sync/status", None),
                    ("POST", "/api/sync/export", {}),
                    ("POST", "/api/sync/merge", {"delta": {}, "dryRun": True}),
                    ("POST", "/api/profile/replay", {"apply": False}),
                    ("POST", "/api/derivation/diff", {"derivationId": "deriv-shm-diff", "studentSteps": []}),
                ):
                    st_b, _, _ = _req(
                        lport,
                        method,
                        path,
                        headers=lan_get_hdrs if method == "GET" else lan_post_hdrs,
                        body=payload,
                    )
                    self.assertEqual(st_b, 403, f"--lan cookie MUST return 403 on {method} {path}, got {st_b}")
            finally:
                srv_lan.shutdown()
                srv_lan.server_close()
                t_lan.join(timeout=3.0)

    def test_v31_wave3b_adversarial_hardening_locks(self) -> None:
        """Wave 3B-Harden Lock: all 4 P0, 4 P1, and 3 P2 findings from #256."""
        import copy
        import syntopik.brainstem as bs
        import syntopik.evidence as ev_mod
        import syntopik.forks as forks_mod
        import syntopik.isomorphism as iso_mod
        import syntopik.learner_model as lm
        import syntopik.root_cause as rc_mod
        import syntopik.schemas as sch_mod
        import syntopik.server as srv_mod
        import syntopik.sim_eval as se_mod
        import syntopik.sync_delta as sd_mod

        df_mod = load_script_module("derivation_forge.py")
        fa_mod = load_script_module("falsify_arena.py")

        def _req(
            port: int,
            method: str,
            path: str,
            *,
            headers: dict[str, str] | None = None,
            body: Any = None,
        ) -> tuple[int, bytes]:
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
                return resp.status, resp.read()
            finally:
                conn.close()

        # --- P0.4: 6-term degree-8 rational SymPy DoS payload (<0.5s across all 3 evaluators) ---
        dos_expr = "((a+b+c+d+e+f)**8 + (-k*x)*(a+b+c+d+e+f)**8) / (1 + (a+b+c+d+e+f)**8)"
        for label, fn in (
            ("forks_deg8_dos", lambda: forks_mod._math_equivalent(dos_expr, "-k*x")),
            ("forge_deg8_dos", lambda: df_mod._math_equivalent(dos_expr, "-k*x")),
            ("arena_deg8_dos", lambda: fa_mod._try_sympy_eval(dos_expr, {})),
        ):
            with self.subTest(sympy_dos=label):
                t0 = time.monotonic()
                res_val = fn()
                dt = time.monotonic() - t0
                self.assertLess(dt, 0.5, f"{label} took {dt:.3f}s (exceeds 0.5s DoS ceiling)")
                if not label.startswith("arena_"):
                    self.assertFalse(res_val)
        # Equivalent high-expansion rational still passes fast via bounded AST numeric sampling
        t0_eq = time.monotonic()
        self.assertTrue(
            forks_mod._math_equivalent(
                "((-k*x) + (-k*x)*(a+b+c+d+e+f)**8) / (1 + (a+b+c+d+e+f)**8)",
                "-k*x",
            )
        )
        self.assertLess(time.monotonic() - t0_eq, 0.5)
        self.assertTrue(forks_mod._math_equivalent("(x+1)**2", "x**2 + 2*x + 1"))

        with tempfile.TemporaryDirectory(prefix="synt-v31-harden-") as tmp:
            t = Path(tmp)

            # --- P0.1: CRDT Sync NaN/Inf rejection & numeric bounds clamping ---
            ws_src = t / "ws-sync-src"
            ws_dst = t / "ws-sync-dst"
            for wr in (ws_src, ws_dst):
                (wr / "knowledge-graph").mkdir(parents=True)
                (wr / "chapters").mkdir(parents=True)
            (ws_src / "knowledge-graph" / "propositions.jsonl").write_text(
                json.dumps({
                    "id": "p-bounds",
                    "subject": "Physics",
                    "chapter": "SHM",
                    "title": "Restoring Force",
                    "status": "solid",
                    "bktMastery": 0.85,
                    "fsrs": {"stability": 5.0, "difficulty": 4.5},
                }) + "\n",
                encoding="utf-8",
            )
            lm.record_telemetry_event(
                ws_src / "knowledge-graph",
                domain="Physics",
                prop_id="p-bounds",
                correct=True,
                confidence=0.85,
                duration_ms=9000,
                now="2026-11-24T10:00:00Z",
            )
            base_delta = sd_mod.export_sync_delta(ws_src, device_id="dev-src-01")["delta"]

            # 1a. Non-finite string "NaN" / "Infinity" inside strict RFC-8259 JSON rejected by verify & merge
            for bad_field, mutator in (
                ("elo_nan_str", lambda d: d["payload"]["learnerProfile"]["domains"]["Physics"].__setitem__("elo", "NaN")),
                ("elo_inf_str", lambda d: d["payload"]["learnerProfile"]["globalStats"].__setitem__("elo", "Infinity")),
                ("bkt_ninf_str", lambda d: d["payload"]["propositions"][0].__setitem__("bktMastery", "-Infinity")),
                ("fsrs_stab_nan_str", lambda d: d["payload"]["propositions"][0]["fsrs"].__setitem__("stability", "NaN")),
            ):
                with self.subTest(bad_sync=bad_field):
                    bad = copy.deepcopy(base_delta)
                    mutator(bad)
                    bad["contentSha256"] = sd_mod._compute_delta_sha256(bad)
                    ver = sd_mod.verify_sync_delta(bad)
                    self.assertFalse(ver["ok"], f"{bad_field} MUST be rejected: {ver}")
                    mrg = sd_mod.merge_sync_delta(ws_dst, bad)
                    self.assertFalse(mrg["ok"], f"{bad_field} merge MUST fail closed")

            # 1b. Python float NaN dict & literal JSON NaN file rejected
            nan_dict = copy.deepcopy(base_delta)
            nan_dict["payload"]["propositions"][0]["bktMastery"] = float("nan")
            self.assertFalse(sd_mod.verify_sync_delta(nan_dict)["ok"])

            raw_nan_file = t / "literal-nan.json"
            raw_nan_file.write_text(
                json.dumps(base_delta).replace("0.85", "NaN", 1),
                encoding="utf-8",
            )
            self.assertFalse(sd_mod.verify_sync_delta(raw_nan_file)["ok"])

            # 1c. Out-of-bounds finite values clamped cleanly on merge
            oob = copy.deepcopy(base_delta)
            oob["payload"]["propositions"][0]["bktMastery"] = -999
            oob["payload"]["propositions"][0]["fsrs"]["stability"] = -5
            oob["payload"]["propositions"][0]["fsrs"]["difficulty"] = 999
            oob["payload"]["learnerProfile"]["domains"]["Physics"]["elo"] = 9999
            oob["payload"]["learnerProfile"]["domains"]["Physics"]["n"] = -7
            oob["payload"]["learnerProfile"]["domains"]["Physics"]["deviceStates"] = {
                "dev-src-01": {"elo": 9999, "n": -7, "lastUpdated": "2026-11-24T10:00:00Z"}
            }
            oob["payload"]["learnerProfile"]["globalStats"]["elo"] = 100
            oob["payload"]["learnerProfile"]["globalStats"]["totalEvents"] = -3
            oob["payload"]["learnerProfile"]["globalStats"]["deviceStates"] = {
                "dev-src-01": {"elo": 100, "n": -3, "pfAttempts": -2, "pfSurvivalRate": 5.0, "analogyFormalismRatio": -1.0, "latencyEwma": 0.0}
            }
            oob["contentSha256"] = sd_mod._compute_delta_sha256(oob)
            m_oob = sd_mod.merge_sync_delta(ws_dst, oob)
            self.assertTrue(m_oob["ok"], m_oob)
            dst_prop = json.loads((ws_dst / "knowledge-graph" / "propositions.jsonl").read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(dst_prop["bktMastery"], 0.0)
            self.assertEqual(dst_prop["fsrs"]["stability"], 0.01)
            self.assertEqual(dst_prop["fsrs"]["difficulty"], 10.0)
            dst_prof = json.loads((ws_dst / "knowledge-graph" / "learner-profile.json").read_text(encoding="utf-8"))
            self.assertEqual(dst_prof["domains"]["Physics"]["elo"], 2600.0)
            self.assertEqual(dst_prof["domains"]["Physics"]["n"], 0)
            self.assertGreaterEqual(dst_prof["globalStats"]["elo"], 800.0)
            self.assertLessEqual(dst_prof["globalStats"]["elo"], 2600.0)
            self.assertGreaterEqual(dst_prof["globalStats"]["totalEvents"], 0)

            # --- P0.2: Rosetta isomorphic_to metadata preserved & undirected dedup on CRDT merge ---
            nc_test = sch_mod.normalize_chain({
                "from": "shm-1",
                "to": "rlc-1",
                "relation": "isomorphic_to",
                "encompassWeight": 0.25,
                "archetypeId": "second_order_oscillator",
                "breaksAt": ["Non-linear spring breakdown"],
                "createdAt": "2026-11-20T08:00:00Z",
            })
            assert nc_test is not None
            self.assertEqual(nc_test["archetypeId"], "second_order_oscillator")
            self.assertEqual(nc_test["breaksAt"], ["Non-linear spring breakdown"])
            self.assertEqual(nc_test["createdAt"], "2026-11-20T08:00:00Z")

            iso_a = t / "iso-dev-a"
            iso_b = t / "iso-dev-b"
            for wr in (iso_a, iso_b):
                (wr / "knowledge-graph").mkdir(parents=True)
                (wr / "chapters").mkdir(parents=True)
            (iso_a / "knowledge-graph" / "chains.jsonl").write_text(
                json.dumps({
                    "from": "shm-1",
                    "to": "rlc-1",
                    "premises": ["shm-1"],
                    "conclusion": "rlc-1",
                    "relation": "isomorphic_to",
                    "encompassWeight": 0.25,
                    "archetypeId": "second_order_oscillator",
                    "breaksAt": ["Linearity fails at large amplitude"],
                    "why": "Harmonic oscillator duality",
                    "createdAt": "2026-11-21T09:00:00Z",
                }) + "\n",
                encoding="utf-8",
            )
            (iso_b / "knowledge-graph" / "chains.jsonl").write_text(
                json.dumps({
                    "from": "rlc-1",
                    "to": "shm-1",
                    "premises": ["rlc-1"],
                    "conclusion": "shm-1",
                    "relation": "isomorphic_to",
                    "encompassWeight": 0.25,
                    "archetypeId": "second_order_oscillator",
                    "breaksAt": ["Inductor core saturates at high current"],
                    "why": "Harmonic oscillator duality",
                    "createdAt": "2026-11-20T08:00:00Z",
                }) + "\n",
                encoding="utf-8",
            )
            exp_iso_a = sd_mod.export_sync_delta(iso_a, device_id="dev-iso-a")["delta"]
            exp_iso_b = sd_mod.export_sync_delta(iso_b, device_id="dev-iso-b")["delta"]
            iso_ab = t / "iso-ab"
            iso_ba = t / "iso-ba"
            shutil.copytree(iso_a, iso_ab)
            shutil.copytree(iso_b, iso_ba)
            self.assertTrue(sd_mod.merge_sync_delta(iso_ab, exp_iso_b)["ok"])
            self.assertTrue(sd_mod.merge_sync_delta(iso_ba, exp_iso_a)["ok"])
            chains_ab = [json.loads(ln) for ln in (iso_ab / "knowledge-graph" / "chains.jsonl").read_text(encoding="utf-8").splitlines() if ln.strip()]
            chains_ba = [json.loads(ln) for ln in (iso_ba / "knowledge-graph" / "chains.jsonl").read_text(encoding="utf-8").splitlines() if ln.strip()]
            self.assertEqual(len(chains_ab), 1, f"Expected 1 deduped undirected chord, got {chains_ab}")
            self.assertEqual(chains_ab, chains_ba, "Undirected isomorphic_to merge must be commutative")
            self.assertEqual(chains_ab[0]["archetypeId"], "second_order_oscillator")
            self.assertEqual(
                set(chains_ab[0]["breaksAt"]),
                {"Linearity fails at large amplitude", "Inductor core saturates at high current"},
            )
            self.assertEqual(chains_ab[0]["createdAt"], "2026-11-20T08:00:00Z")

            # --- P1.1: root_cause.py & evidence.py ignore isomorphic_to / contrasts_with / analogous ---
            ws_nc = t / "ws-noncausal"
            kg_nc = ws_nc / "knowledge-graph"
            kg_nc.mkdir(parents=True)
            (kg_nc / "propositions.jsonl").write_text(
                "\n".join([
                    json.dumps({"id": "shm-osc", "subject": "Physics", "chapter": "SHM", "title": "SHM", "status": "shaky"}),
                    json.dumps({"id": "rlc-osc", "subject": "EE", "chapter": "RLC", "title": "RLC", "status": "solid"}),
                    json.dumps({"id": "pid-ctrl", "subject": "Control", "chapter": "PID", "title": "PID", "status": "solid"}),
                ]) + "\n",
                encoding="utf-8",
            )
            (kg_nc / "chains.jsonl").write_text(
                "\n".join([
                    json.dumps({"premises": ["shm-osc"], "conclusion": "rlc-osc", "relation": "isomorphic_to", "encompassWeight": 0.25, "archetypeId": "second_order_oscillator"}),
                    json.dumps({"premises": ["shm-osc"], "conclusion": "pid-ctrl", "relation": "isomorphic_to", "encompassWeight": 0.25, "archetypeId": "second_order_oscillator"}),
                    json.dumps({"premises": ["rlc-osc"], "conclusion": "pid-ctrl", "relation": "contrasts_with", "encompassWeight": 0.5}),
                    json.dumps({"premises": ["rlc-osc"], "conclusion": "shm-osc", "relation": "analogous", "encompassWeight": 0.5}),
                ]) + "\n",
                encoding="utf-8",
            )
            loc = rc_mod.localize_fault(kg_nc, "rlc-osc")
            self.assertTrue(loc["ok"])
            self.assertEqual(loc["suspects"], [], f"isomorphic_to must not pollute localize_fault: {loc}")
            self.assertEqual(rc_mod.find_blast_radius(kg_nc), [])
            ev_nc = ev_mod.compute_evidence_summary(ws_nc)
            self.assertEqual(ev_nc["propositions"]["shm-osc"]["outDegree"], 0)
            self.assertEqual(ev_nc["propositions"]["shm-osc"]["descendantsCount"], 0)
            self.assertEqual(ev_nc["antiInflationAlerts"], [])

            # --- P1.2: Replay NaN/Inf hintsUsed, corrupt profile floats, & Windows pre-1970 dates ---
            ws_rep = t / "ws-replay-edge"
            kg_rep = ws_rep / "knowledge-graph"
            kg_rep.mkdir(parents=True)
            (kg_rep / "learner-profile.json").write_text(
                json.dumps({
                    "schemaVersion": 1,
                    "calibration": {
                        "completed": True,
                        "sessionTargetMinutes": "NaN",
                        "baselineLatencyMs": "Infinity",
                    },
                }),
                encoding="utf-8",
            )
            edge_revlog = []
            for i in range(14):
                ts_iso = "0001-01-01T00:00:00Z" if i == 0 else f"2026-10-{i + 1:02d}T10:00:00Z"
                edge_revlog.append({
                    "propId": f"p-e-{i % 4}",
                    "domain": "Physics",
                    "ts": ts_iso,
                    "rating": "good" if i % 3 != 0 else "again",
                    "correct": i % 3 != 0,
                    "learnerConfidence": 0.80,
                    "difficulty": 1520,
                    "hintsUsed": "NaN" if i == 1 else ("Infinity" if i == 2 else 0),
                    "durationMs": 12000,
                })
            (kg_rep / "revlog.jsonl").write_text("\n".join(json.dumps(r) for r in edge_revlog) + "\n", encoding="utf-8")
            rep_edge = se_mod.replay_learner_history(ws_rep, min_events=12, apply_if_improved=False)
            self.assertTrue(rep_edge["ok"], rep_edge)
            self.assertFalse(rep_edge["insufficientData"])
            self.assertEqual(rep_edge["eventCount"], 14)
            self.assertEqual(rep_edge["split"]["trainStart"], "0001-01-01T00:00:00Z")

            # --- P1.3: Pip-wheel archetypes.json lookup in scripts/sync-graph.mjs ---
            wheel_root = t / "wheel_pkg"
            (wheel_root / "data").mkdir(parents=True)
            (wheel_root / "_bundle" / "scripts").mkdir(parents=True)
            shutil.copy2(ROOT / "syntopik" / "data" / "archetypes.json", wheel_root / "data" / "archetypes.json")
            shutil.copy2(SCRIPTS_DIR / "sync-graph.mjs", wheel_root / "_bundle" / "scripts" / "sync-graph.mjs")
            wheel_ws = t / "wheel-ws"
            (wheel_ws / "knowledge-graph").mkdir(parents=True)
            (wheel_ws / "chapters").mkdir(parents=True)
            (wheel_ws / "knowledge-graph" / "propositions.jsonl").write_text("", encoding="utf-8")
            (wheel_ws / "knowledge-graph" / "chains.jsonl").write_text("", encoding="utf-8")
            wheel_out = wheel_ws / "knowledge-graph" / "graph-data.js"
            p_wh = run_cli([
                "node", str(wheel_root / "_bundle" / "scripts" / "sync-graph.mjs"),
                "--workspace", str(wheel_ws),
                "--kg-dir", str(wheel_ws / "knowledge-graph"),
                "--chapters-dir", str(wheel_ws / "chapters"),
                "--out", str(wheel_out),
            ])
            self.assertEqual(p_wh.returncode, 0, f"Wheel sync-graph.mjs failed: {p_wh.stderr}")
            p_wh_read = run_cli([
                "node", "-e",
                f"globalThis.window={{}}; require({json.dumps(str(wheel_out))}); console.log(window.__STUDY_HUB__.rosetta.archetypesCount);",
            ])
            self.assertEqual(p_wh_read.returncode, 0, p_wh_read.stderr)
            self.assertGreaterEqual(int(p_wh_read.stdout.strip()), 10)

            # --- P1.4: Brainstem worst-case envelope strictly <= 180 tokens ---
            ws_bs = t / "ws-brainstem-budget"
            kg_bs = ws_bs / "knowledge-graph"
            ch_bs = ws_bs / "chapters"
            kg_bs.mkdir(parents=True)
            ch_bs.mkdir(parents=True)
            long_known = "physics-very-long-second-order-damped-harmonic-oscillator-node-alpha-001"
            long_target = "electrical-engineering-very-long-series-rlc-resonant-circuit-node-beta-002"
            (kg_bs / "propositions.jsonl").write_text(
                "\n".join([
                    json.dumps({
                        "id": long_known,
                        "subject": "Physics",
                        "chapter": "SHM",
                        "title": "Damped Simple Harmonic Motion (SHM) Oscillator",
                        "statement": "Linear restoring force m x'' + b x' + k x = 0 with viscous damping and resonance.",
                        "status": "solid",
                        "bktMastery": 0.94,
                        "nextReview": "2099-01-01",
                    }),
                    json.dumps({
                        "id": long_target,
                        "subject": "Electrical Engineering",
                        "chapter": "AC Circuits",
                        "title": "Series RLC Resonant Circuit",
                        "statement": "Inductor L, resistor R, capacitor C obey L q'' + R q' + q/C = 0 with resonance 1/sqrt(LC).",
                        "status": "planned",
                        "bktMastery": 0.12,
                    }),
                ]) + "\n",
                encoding="utf-8",
            )
            (kg_bs / "chains.jsonl").write_text("", encoding="utf-8")
            iso_mod.link_isomorphism_chord(kg_bs, long_known, long_target, "second_order_oscillator")
            (kg_bs / "whiteboard-session.json").write_text(
                json.dumps({
                    "frames": [{"title": "Step 1", "kind": "derivation_step", "body": "Derive resonance"}],
                    "pins": [
                        {"id": f"pin-very-long-identifier-{i}", "stepIndex": i, "resolved": False, "note": " ".join([f"word{i}_{j}" for j in range(25)])}
                        for i in range(3)
                    ],
                }),
                encoding="utf-8",
            )
            prof_bs = lm.load_profile(kg_bs)
            lm.apply_tier1_calibration(prof_bs, entry_ramp="balanced", challenge_appetite="balanced", prose_density="standard")
            lm.save_profile(kg_bs, prof_bs)
            lm.record_telemetry_event(kg_bs, domain="Physics", prop_id=long_known, correct=True, confidence=0.90, duration_ms=9000)

            env_pins = bs.plan_next_move(ws_bs)
            self.assertLessEqual(bs._envelope_token_count(env_pins), 180, f"Pins envelope > 180 tokens: {env_pins}")

            # Also stress _enforce_envelope_budget directly with simultaneous bridge + pins + alerts + long rationale
            worst = {
                "ok": True,
                "recommendedMode": "teach_frontier",
                "targetNodeId": long_target,
                "chapterSlug": "super-long-chapter-slug-for-worst-case-token-budget-stress-test",
                "rationale": " ".join(["rationale"] * 40),
                "teachingPolicy": {
                    "domain": "Electrical Engineering",
                    "scaffoldingMode": "faded",
                    "allowWorkedExample": False,
                    "allowProductiveFailure": True,
                    "maxProseWords": 135,
                    "targetSuccessRate": 0.85,
                    "requirePredictionFirst": True,
                    "affectState": "flow",
                    "affectIntervention": "none",
                },
                "policyConstraints": [" ".join(["constraint"] * 12) for _ in range(3)],
                "exactCmd": "syntopik run chapter_tracker next --slug super-long-chapter-slug --extra-flag " + " ".join(["arg"] * 20),
                "skillHint": "teach-chapter",
                "whiteboardHint": " ".join(["whiteboard"] * 30),
                "pendingPins": [{"id": f"pin-{i}", "stepIndex": i, "note": " ".join(["pinword"] * 18)} for i in range(3)],
                "alerts": [" ".join(["alertword"] * 15) for _ in range(3)],
                "isomorphicBridge": {
                    "knownPropId": long_known,
                    "archetypeName": "Second-Order Damped Linear Oscillator Structural Archetype",
                    "breaksAt": [" ".join(["breakword"] * 25)],
                    "openerPrompt": " ".join(["openerword"] * 35),
                },
            }
            trimmed = bs._enforce_envelope_budget(worst, max_tokens=180)
            self.assertLessEqual(bs._envelope_token_count(trimmed), 180)

            # --- P0.3 (Proof-Graph Diff zero-leak via mistakes.jsonl & GET /api/state) + P0.1 & P2 HTTP locks ---
            ws_srv = t / "ws-srv-harden"
            kg_srv = ws_srv / "knowledge-graph"
            ch_srv = ws_srv / "chapters"
            kg_srv.mkdir(parents=True)
            ch_srv.mkdir(parents=True)
            secret_step_expr = "m*a = -k*x"
            (kg_srv / "propositions.jsonl").write_text(
                json.dumps({"id": "p-shm-seal", "subject": "Physics", "chapter": "SHM", "title": "SHM Seal", "status": "shaky"}) + "\n",
                encoding="utf-8",
            )
            (kg_srv / "chains.jsonl").write_text("", encoding="utf-8")
            (kg_srv / "mistakes.jsonl").write_text("", encoding="utf-8")
            df_mod.create_derivation(
                {
                    "id": "deriv-seal-check",
                    "propId": "p-shm-seal",
                    "chapter": "SHM",
                    "title": "Hooke Restoring Law",
                    "domain": "Physics",
                    "steps": [
                        {"id": "a1", "role": "axiom", "label": "Newton Second Law", "expr": "F = m*a", "dependsOn": []},
                        {"id": "s1", "role": "step", "label": "Restoring Equation", "expr": secret_step_expr, "dependsOn": ["a1"]},
                    ],
                },
                kg_dir=kg_srv,
                workspace=ws_srv,
            )

            srv = srv_mod.create_server(workspace=ws_srv, host="127.0.0.1", port=0, lan=False)
            t_srv = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
            t_srv.start()
            try:
                port = srv.bound_port
                auth = {"X-Syntopik-Token": srv.csrf_token}

                # P0.3: POST /api/derivation/diff with autoSpawnFork=True MUST NOT leak canonical step into mistakes.jsonl or GET /api/state
                st_dd, raw_dd = _req(
                    port,
                    "POST",
                    "/api/derivation/diff",
                    headers=auth,
                    body={
                        "derivationId": "deriv-seal-check",
                        "studentSteps": [{"line": 1, "expr": "F = m*a"}, {"line": 2, "expr": "m*a = +k*x"}],
                        "autoSpawnFork": True,
                    },
                )
                self.assertEqual(st_dd, 200, raw_dd)
                self.assertNotIn(secret_step_expr, raw_dd.decode("utf-8"))
                self.assertNotIn("-k*x", raw_dd.decode("utf-8"))

                mistakes_txt = (kg_srv / "mistakes.jsonl").read_text(encoding="utf-8")
                self.assertNotIn(secret_step_expr, mistakes_txt, "mistakes.jsonl leaked canonical expected_expr!")
                self.assertNotIn("-k*x", mistakes_txt, "mistakes.jsonl leaked canonical RHS!")
                st_state, raw_state = _req(port, "GET", "/api/state")
                self.assertEqual(st_state, 200, raw_state)
                state_txt = raw_state.decode("utf-8")
                self.assertNotIn(secret_step_expr, state_txt, "GET /api/state leaked canonical expected_expr!")
                self.assertNotIn("-k*x", state_txt, "GET /api/state leaked canonical RHS!")

                # P0.1 over HTTP: literal JSON NaN & string "NaN" delta rejected with 400; OOB finite clamped with 200
                st_lit_nan, _ = _req(port, "POST", "/api/sync/merge", headers=auth, body='{"delta": {"elo": NaN}}')
                self.assertEqual(st_lit_nan, 400)
                bad_http_delta = copy.deepcopy(base_delta)
                bad_http_delta["payload"]["learnerProfile"]["domains"]["Physics"]["elo"] = "NaN"
                bad_http_delta["contentSha256"] = sd_mod._compute_delta_sha256(bad_http_delta)
                st_str_nan, _ = _req(port, "POST", "/api/sync/merge", headers=auth, body={"delta": bad_http_delta, "dryRun": False})
                self.assertEqual(st_str_nan, 400)
                st_oob_http, raw_oob_http = _req(port, "POST", "/api/sync/merge", headers=auth, body={"delta": oob, "dryRun": False})
                self.assertEqual(st_oob_http, 200, raw_oob_http)

                # P2: POST /api/sync/export rejects outPath="exports" (directory) & non-.json with 400
                st_dir, _ = _req(port, "POST", "/api/sync/export", headers=auth, body={"outPath": "exports"})
                self.assertEqual(st_dir, 400, "POST /api/sync/export must reject exports directory with 400")
                st_ext, _ = _req(port, "POST", "/api/sync/export", headers=auth, body={"outPath": "exports/bad.txt"})
                self.assertEqual(st_ext, 400, "POST /api/sync/export must reject non-.json suffix with 400")
                st_ok_exp, raw_ok_exp = _req(port, "POST", "/api/sync/export", headers=auth, body={"outPath": "exports/clean.json"})
                self.assertEqual(st_ok_exp, 200, raw_ok_exp)
                self.assertTrue((ws_srv / "exports" / "clean.json").is_file())
            finally:
                srv.shutdown()
                srv.server_close()
                t_srv.join(timeout=3.0)

        # --- P2 UI Polish locks: dashboard.html (eloBarPct NaN + lift sign) & viewer.html esc(') ---
        dash_src = (ROOT / "dashboard.html").read_text(encoding="utf-8")
        viewer_src = (ROOT / "knowledge-graph" / "viewer.html").read_text(encoding="utf-8")
        self.assertIn("Number.isFinite(n)", dash_src)
        self.assertNotIn("<strong>+${lift.toFixed(4)} OOS lift</strong>", dash_src)
        self.assertIn('"&#39;"', viewer_src)
        p_ui = run_cli([
            "node", "-e",
            (
                "const eloBarPct = (elo) => { const n = Number(elo); return Number.isFinite(n) ? Math.max(6, Math.min(100, Math.round(((n - 1000) / 900) * 100))) : 0; };\n"
                "if (eloBarPct(NaN) !== 0 || eloBarPct('NaN') !== 0) throw new Error('eloBarPct NaN failed');\n"
                "const fmt = (lift) => `${lift >= 0 ? '+' : ''}${lift.toFixed(4)}`;\n"
                "if (fmt(-0.01) !== '-0.0100' || fmt(0.02) !== '+0.0200') throw new Error('lift fmt failed');\n"
                "console.log('UI_POLISH_OK');\n"
            ),
        ])
        self.assertEqual(p_ui.returncode, 0, p_ui.stderr)
        self.assertIn("UI_POLISH_OK", p_ui.stdout)


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
        import sqlite3
        import threading
        import time
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
            self.assertIn("Lever #1", rd_txt)
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
