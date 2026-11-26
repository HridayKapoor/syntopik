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
            + sorted((ROOT / "tests").glob("*.py"))
        )
        self.assertGreaterEqual(len(py_files), 43, "Expected at least 43 Python files across syntopik/, scripts/, tests/")
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
        self.assertGreaterEqual(len(skill_files), 17)
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
            7500,
            f"Combined GEMINI.md + AGENTS.md ({total_bytes} B) exceeds v2.0 7,500 B ceiling",
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

            # 2. tools/list: exact 17 tools (9 model-visible: 6 UI synt_*/mlh_* + 3 ambient headless; 8 visibility:["app"])
            tools = by_id[2]["result"]["tools"]
            tools_by_name = {tl["name"]: tl for tl in tools}
            self.assertEqual(len(tools), 17)
            model_tools = [tl for tl in tools if "app" not in tl.get("_meta", {}).get("ui", {}).get("visibility", [])]
            app_tools = [tl for tl in tools if tl.get("_meta", {}).get("ui", {}).get("visibility") == ["app"]]
            self.assertEqual(
                {tl["name"] for tl in model_tools},
                {
                    "synt_quiz_card",
                    "synt_practice_card",
                    "synt_lesson_studio",
                    "synt_capture_concept",
                    "synt_get_frontier",
                    "synt_log_review",
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
                    "mlh_app_load_state",
                    "mlh_app_rate_flashcard",
                    "mlh_app_save_sketch",
                    "mlh_app_card_state",
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
        self.assertGreaterEqual(report["summary"]["total"], 48)
        self.assertGreaterEqual(report["summary"]["ok"], 48)
        self.assertEqual(report["summary"]["warn"], 0, f"Doctor warnings: {[c for c in report['checks'] if c['status'] == 'warn']}")
        self.assertEqual(report["summary"]["fail"], 0, f"Doctor failures: {[c for c in report['checks'] if c['status'] == 'fail']}")
        checks_by_name = {c["name"]: c for c in report["checks"]}
        self.assertGreaterEqual(checks_by_name["Workspace Scripts (scripts/)"]["count"], 32)
        self.assertGreaterEqual(checks_by_name["Project Skills (.agents/skills)"]["count"], 17)

        # Bare system python3 --core-only mode (zero third-party .venv packages required)
        bare_py = shutil.which("python3") or sys.executable
        core_proc = run_cli([bare_py, "-B", str(SCRIPTS_DIR / "study_doctor.py"), "--core-only", "--json"])
        self.assertEqual(core_proc.returncode, 0, f"study_doctor --core-only failed: {core_proc.stderr}\n{core_proc.stdout}")
        core_report = json.loads(core_proc.stdout)
        self.assertTrue(core_report.get("coreOnly"))
        self.assertTrue(core_report["healthy"], f"Core-only doctor unhealthy: {core_report['summary']}")
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
            self.assertEqual(rc_js, 0)
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

        # Always-on rule budget: GEMINI.md + AGENTS.md <= 7,500 B (v2.0 diet)
        agents_bytes = (ROOT / "AGENTS.md").read_bytes()
        gemini_bytes = (ROOT / "GEMINI.md").read_bytes()
        combined_rules = len(agents_bytes) + len(gemini_bytes)
        self.assertLessEqual(
            combined_rules,
            7500,
            f"Combined GEMINI.md + AGENTS.md ({combined_rules} B) exceeds v2.0 7,500 B ceiling",
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

        # 1. Built-in 15-scenario golden benchmark passes 15/15
        bench = pe.run_benchmark()
        self.assertTrue(bench["ok"])
        self.assertEqual(bench["total"], 15)
        self.assertEqual(bench["passed"], 15)

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
                self.assertEqual(state_obj["hub"]["schemaVersion"], 4)
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
        self.assertEqual(pyp["project"]["version"], "2.0.0")
        self.assertEqual(pyp["project"]["dependencies"], [])
        self.assertEqual(pyp["project"]["scripts"]["syntopik"], "syntopik.cli:main")
        force_inc = pyp["tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"]
        self.assertIn("scripts", force_inc)

        # 2. Validate package.json + bin/syntopik.mjs --help / --version
        pkg = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
        self.assertEqual(pkg["name"], "syntopik")
        self.assertEqual(pkg["version"], "2.0.0")
        self.assertEqual(pkg["dependencies"], {})
        self.assertEqual(pkg["bin"]["syntopik"], "./bin/syntopik.mjs")

        h_proc = run_cli(["node", str(ROOT / "bin" / "syntopik.mjs"), "--help"])
        self.assertEqual(h_proc.returncode, 0, h_proc.stderr)
        self.assertIn("syntopik", h_proc.stdout.lower())
        v_proc = run_cli(["node", str(ROOT / "bin" / "syntopik.mjs"), "--version"])
        self.assertEqual(v_proc.returncode, 0, v_proc.stderr)
        self.assertIn("2.0.0", v_proc.stdout)

        # 3. Validate .claude-plugin/{marketplace,plugin,hooks}.json & gemini-extension.json
        cp_mkt = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
        cp_plg = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        cp_hks = json.loads((ROOT / ".claude-plugin" / "hooks.json").read_text(encoding="utf-8"))
        gem_ext = json.loads((ROOT / "gemini-extension.json").read_text(encoding="utf-8"))

        self.assertEqual(cp_mkt["name"], "syntopik")
        self.assertEqual(cp_plg["version"], "2.0.0")
        self.assertTrue(cp_plg["skills"].startswith("./"))
        self.assertIn("SessionStart", cp_hks["hooks"])
        self.assertIn("PostToolUse", cp_hks["hooks"])
        self.assertEqual(gem_ext["name"], "syntopik")
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

            # 1. Exact 17 tools & single com.google.antigravity/ui entrypoint ONLY on synt_lesson_studio
            tools = by_id[2]["result"]["tools"]
            self.assertEqual(len(tools), 17)
            tool_names = {tl["name"] for tl in tools}
            for ambient in ("synt_capture_concept", "synt_get_frontier", "synt_log_review"):
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
        # 1. Combined AGENTS.md + GEMINI.md <= 7,500 B (v2.0 Context-Budget Diet)
        agents_raw = (ROOT / "AGENTS.md").read_bytes()
        gemini_raw = (ROOT / "GEMINI.md").read_bytes()
        combined = len(agents_raw) + len(gemini_raw)
        self.assertLessEqual(combined, 7500, f"AGENTS.md + GEMINI.md ({combined} B) exceeds 7,500 B v2.0 budget")
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
