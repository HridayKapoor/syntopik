#!/usr/bin/env python3
"""Tests for learner-ledger integrity scanning and quarantine repair (syntopik/ledger_check.py)."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from syntopik.ledger_check import repair_ledgers, scan_ledgers  # noqa: E402
from syntopik.storage import load_jsonl  # noqa: E402

NOW = datetime(2026, 11, 27, 12, 0, tzinfo=timezone.utc)


class TestLedgerIntegrity(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="synt-ledger-")
        self.ws = Path(self._tmp.name)
        self.kg = self.ws / "knowledge-graph"
        self.kg.mkdir()
        (self.kg / "propositions.jsonl").write_text(
            '{"id": "a", "title": "A"}\n'
            '{"id": "b", "title": "B"}\n'
            '{"id": "a", "title": "A again"}\n'
            '{"id": "c", "title": "trunc\n'  # crash mid-append
            "[1, 2]\n"  # valid JSON, not an object
            '{"title": "no id"}\n',
            encoding="utf-8",
        )
        (self.kg / "chains.jsonl").write_text(
            '{"from": "a", "to": "b"}\n{"premises": ["a", "ghost"], "conclusion": "b"}\n', encoding="utf-8"
        )
        (self.kg / "evidence-ledger.jsonl").write_text('{"seq": 1}\nnot json\n', encoding="utf-8")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_scan_reports_every_issue_with_line_numbers(self) -> None:
        rep = scan_ledgers(self.kg)
        self.assertFalse(rep["ok"])
        by_file = {f["file"]: f for f in rep["files"]}
        self.assertEqual([b["line"] for b in by_file["propositions.jsonl"]["corrupt"]], [4, 5])
        self.assertEqual([b["line"] for b in by_file["evidence-ledger.jsonl"]["corrupt"]], [2])
        self.assertEqual(rep["corruptLines"], 3)
        self.assertEqual(rep["duplicateIds"], ["a"])
        self.assertEqual(rep["propositionsMissingId"], 1)
        self.assertEqual(rep["danglingChains"], 1)
        self.assertEqual(rep["danglingExamples"][0]["missing"], ["ghost"])

    def test_repair_quarantines_without_losing_data(self) -> None:
        res = repair_ledgers(self.kg, now=NOW)
        self.assertEqual(res["quarantinedLines"], 2)
        qfile = self.kg / ".quarantine" / "propositions.20261127T120000Z.jsonl"
        self.assertTrue(qfile.is_file())
        self.assertEqual(qfile.read_text(encoding="utf-8").splitlines(), ['{"id": "c", "title": "trunc', "[1, 2]"])
        # Valid rows survive untouched, in order (duplicates are reported, never auto-deleted)
        self.assertEqual([r.get("id") for r in load_jsonl(self.kg / "propositions.jsonl")], ["a", "b", "a", None])
        # The hash-chained evidence ledger is never rewritten
        self.assertEqual((self.kg / "evidence-ledger.jsonl").read_text(encoding="utf-8"), '{"seq": 1}\nnot json\n')
        after = scan_ledgers(self.kg)
        by_file = {f["file"]: f for f in after["files"]}
        self.assertEqual(by_file["propositions.jsonl"]["corrupt"], [])
        # Idempotent
        self.assertEqual(repair_ledgers(self.kg, now=NOW)["quarantinedLines"], 0)

    def test_clean_and_empty_workspaces_are_ok(self) -> None:
        empty = self.ws / "empty"
        empty.mkdir()
        self.assertTrue(scan_ledgers(empty)["ok"])
        clean = self.ws / "clean"
        clean.mkdir()
        (clean / "propositions.jsonl").write_text(json.dumps({"id": "x"}) + "\n", encoding="utf-8")
        (clean / "chains.jsonl").write_text("", encoding="utf-8")
        rep = scan_ledgers(clean)
        self.assertTrue(rep["ok"], rep)
        self.assertEqual(rep["issues"], 0)

    def test_cli_doctor_fix_quarantines(self) -> None:
        proc = subprocess.run(
            [sys.executable, "-B", "-m", "syntopik.cli", "doctor", "--core-only", "--fix", "--workspace", str(self.ws), "--json"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        report = json.loads(proc.stdout)
        self.assertTrue(any("quarantined 2" in a for a in report.get("fixActions", [])), report.get("fixActions"))
        ledger = next(c for c in report["checks"] if c["name"] == "Learner Ledger Integrity")
        self.assertEqual(ledger["status"], "warn")  # duplicates + dangling chain still need the learner
        self.assertEqual(ledger["corruptLines"], 1)  # only the never-rewritten evidence ledger line remains


if __name__ == "__main__":
    unittest.main()
