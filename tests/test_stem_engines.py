#!/usr/bin/env python3
"""Syntopik regression suite: Deterministic STEM engines: math, physics, chemistry, plots and code verification.

Shared fixtures live in tests/_helpers.py. Run via:
    python3 -B -m unittest discover -s tests -t tests -v
"""

from __future__ import annotations

import argparse
import contextlib
import json
import math
import os
import re
import sqlite3
import sys
import tempfile
import unittest
import unittest.mock
import zipfile
from pathlib import Path
from typing import Any

from _helpers import (
    ROOT,
    SCRIPTS_DIR,
    _EncodedStringIO,
    call_json_cmd,
    load_script_module,
    requires,
    run_cli,
)


class TestStemEngines(unittest.TestCase):
    """2. Existing STEM engines & Blueprint Section 2.5 Regression Watchlist."""

    @requires("sympy")
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

    @requires("chempy", "molmass", "scipy", "sympy")
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

    @requires("rdkit", "py2opsin")
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

    @requires("genanki")
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

    @requires("pymupdf", "pymupdf4llm")
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

    @requires(binaries=("typst",))
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

    @requires("srt", "PIL")
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


class TestPhysicsDraw(unittest.TestCase):
    """7. P1.1 Precision Physics Diagram Engine (all 7 parametric presets + edge cases)."""

    @requires("matplotlib", "numpy")
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

    @requires("rdkit", "PIL")
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

    @requires("matplotlib", "numpy", "scipy", "sympy")
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


class TestCodeVerifyEngine(unittest.TestCase):
    """18. Deterministic CS / Algorithms code verification engine (scripts/code_verify.py)."""

    def test_linear_vs_linearithmic_needs_decisive_evidence(self) -> None:
        cv = load_script_module("code_verify.py")
        # Real macOS runner timings of a plain linear loop: ~6% extra per doubling from noise.
        noisy_linear = [
            {"n": 5000, "medianNs": 143417.0},
            {"n": 10000, "medianNs": 304000.0},
            {"n": 20000, "medianNs": 650417.0},
            {"n": 40000, "medianNs": 1381500.0},
        ]
        fit = cv.classify_measurements(noisy_linear)
        self.assertEqual(fit["estimatedComplexity"], "O(N)")
        self.assertLessEqual(fit["confidence"], 0.6)
        # A genuine N log N curve is still recognized.
        nlogn = [{"n": n, "medianNs": n * math.log(n) * 10} for n in (5000, 10000, 20000, 40000)]
        self.assertEqual(cv.classify_measurements(nlogn)["estimatedComplexity"], "O(N log N)")

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
