#!/usr/bin/env python3
"""Syntopik regression suite: PDF studio, universal importers, Obsidian export and research scouting.

Shared fixtures live in tests/_helpers.py. Run via:
    python3 -B -m unittest discover -s tests -t tests -v
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path
from typing import Any

from _helpers import (
    SCRIPTS_DIR,
    _EncodedStringIO,
    call_json_cmd,
    load_script_module,
    requires,
    run_cli,
)


class TestPdfStudioUpgrades(unittest.TestCase):
    """10. LearnHarness Parity Slice B: PDF math_density triage & white-halo annotate."""

    @requires("pymupdf", "pymupdf4llm")
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
