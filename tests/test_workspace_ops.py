#!/usr/bin/env python3
"""Syntopik regression suite: Backup and hub sync, self-improvement and doctor, fresh-clone setup.

Shared fixtures live in tests/_helpers.py. Run via:
    python3 -B -m unittest discover -s tests -t tests -v
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from typing import Any

from _helpers import (
    ROOT,
    SCRIPTS_DIR,
    SKILLS_DIR,
    _EncodedStringIO,
    load_script_module,
    parse_yaml_frontmatter,
    run_cli,
)


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

    @unittest.skipUnless(
        os.environ.get("SYNTOPIK_TEST_FULL_STACK") == "1",
        "full-stack doctor needs every STEM extra plus LaTeX, dvisvgm and Typst; set SYNTOPIK_TEST_FULL_STACK=1",
    )
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

    def test_study_doctor_core_only_zero_warn_zero_fail(self) -> None:
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
