#!/usr/bin/env python3
"""Tests for `syntopik report` (syntopik/report.py). Pure stdlib, sandboxed in a temp dir."""

from __future__ import annotations

import http.client
import json
import subprocess
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from syntopik.report import (  # noqa: E402
    build_progress_report,
    render_report_lines,
    render_report_markdown,
)

NOW = datetime(2026, 11, 27, 18, 0, tzinfo=timezone.utc)


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def _ts(days_ago: float) -> str:
    return (NOW - timedelta(days=days_ago)).isoformat().replace("+00:00", "Z")


class TestProgressReport(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="synt-report-")
        self.ws = Path(self._tmp.name)
        self.kg = self.ws / "knowledge-graph"
        self.kg.mkdir()
        _write_jsonl(
            self.kg / "propositions.jsonl",
            [
                {"id": "a", "title": "Quorum intersection", "chapter": "Raft", "status": "solid", "nextReview": "2026-12-20"},
                {"id": "b", "title": "Term monotonicity", "chapter": "Raft", "status": "shaky", "nextReview": "2026-12-01"},
                {"id": "c", "title": "Log matching", "chapter": "Raft", "status": "learning", "nextReview": "2026-11-26"},
                {"id": "d", "title": "Hooke's law", "chapter": "SHM", "status": "misconception"},
                {"id": "e", "title": "Archived", "chapter": "SHM", "status": "solid", "archived": True},
            ],
        )
        _write_jsonl(
            self.kg / "revlog.jsonl",
            [
                # current window (today + 6 days before)
                {"propId": "a", "ts": _ts(0.1), "rating": "good", "elapsedDays": 5, "durationMs": 60000, "hintsUsed": 0, "targetRetention": 0.9},
                {"propId": "b", "ts": _ts(1), "rating": "again", "elapsedDays": 3, "durationMs": 120000, "hintsUsed": 2, "targetRetention": 0.9},
                {"propId": "b", "ts": _ts(1.1), "rating": "again", "elapsedDays": 0, "durationMs": 30000, "hintsUsed": 1},
                {"propId": "c", "ts": _ts(2), "rating": "easy", "elapsedDays": 0},
                # previous window
                {"propId": "a", "ts": _ts(9), "rating": "good", "elapsedDays": 2, "targetRetention": 0.9},
                {"propId": "c", "ts": _ts(10), "ratingInt": 1, "elapsedDays": 4, "targetRetention": 0.9},
                # outside both windows / in the future / malformed: ignored
                {"propId": "a", "ts": _ts(30), "rating": "good"},
                {"propId": "a", "ts": _ts(-2), "rating": "good"},
                {"propId": "a", "ts": "not-a-date", "rating": "good"},
            ],
        )
        _write_jsonl(
            self.kg / "mistakes.jsonl",
            [
                {"date": _ts(1)[:10], "category": "concept_gap"},
                {"date": _ts(3)[:10], "category": "concept_gap"},
                {"date": _ts(20)[:10], "category": "arithmetic"},
            ],
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_window_metrics_are_evidence_based(self) -> None:
        rep = build_progress_report(self.kg, days=7, now=NOW)
        cur, prev = rep["current"], rep["previous"]
        self.assertEqual(cur["reviews"], 4)
        self.assertEqual(cur["lapses"], 2)
        self.assertEqual(cur["accuracy"], 0.5)
        self.assertEqual(cur["activeDays"], 3)
        self.assertEqual(cur["studyMinutes"], 3.5)
        self.assertEqual(cur["uniqueConcepts"], 3)
        # Mature (elapsedDays >= 1) reviews only: a good, b again -> 50%
        self.assertEqual(cur["matureReviews"], 2)
        self.assertEqual(cur["trueRetention"], 0.5)
        self.assertEqual(cur["targetRetention"], 0.9)
        self.assertEqual(prev["reviews"], 2)
        self.assertEqual(prev["lapses"], 1)  # ratingInt 1 counts as a lapse
        self.assertIsNone(prev["studyMinutes"])  # no data -> None, never a fake zero
        self.assertEqual(rep["deltas"]["reviews"], 2.0)
        self.assertEqual(rep["streakDays"], 3)

    def test_mastery_due_struggling_and_focus(self) -> None:
        rep = build_progress_report(self.kg, days=7, now=NOW)
        self.assertEqual(rep["mastery"]["total"], 4)  # archived excluded
        self.assertEqual(rep["mastery"]["byStatus"]["solid"], 1)
        self.assertEqual(rep["due"]["now"], 3)  # shaky, misconception, overdue c
        self.assertEqual(rep["due"]["next7Days"], 0)
        self.assertEqual(rep["struggling"][0]["id"], "b")
        self.assertEqual(rep["struggling"][0]["lapses"], 2)
        self.assertEqual(rep["focus"]["id"], "b")
        self.assertEqual(rep["mistakes"]["count"], 2)
        self.assertEqual(rep["mistakes"]["byCategory"], {"concept_gap": 2})
        # Tie on needsWork (1 each) -> lower solid share first
        self.assertEqual([c["chapter"] for c in rep["chapters"]], ["SHM", "Raft"])

    def test_empty_workspace_reports_none_not_zero(self) -> None:
        empty = self.ws / "empty-kg"
        empty.mkdir()
        rep = build_progress_report(empty, now=NOW)
        self.assertEqual(rep["current"]["reviews"], 0)
        self.assertIsNone(rep["current"]["accuracy"])
        self.assertIsNone(rep["current"]["trueRetention"])
        self.assertIsNone(rep["mastery"]["solidPct"])
        self.assertIsNone(rep["focus"])
        self.assertTrue(render_report_lines(rep))
        self.assertIn("| Reviews | 0 | 0 |", render_report_markdown(rep))

    def test_cli_report_json_and_markdown(self) -> None:
        md = self.ws / "out" / "report.md"
        proc = subprocess.run(
            [sys.executable, "-B", "-m", "syntopik.cli", "report", "--workspace", str(self.ws), "--md", str(md), "--json"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertTrue(data["ok"])
        self.assertEqual(data["kind"], "progress_report")
        self.assertTrue(md.is_file())
        self.assertIn("# Syntopik progress", md.read_text(encoding="utf-8"))


    def test_studio_serves_report_in_state_and_api(self) -> None:
        from syntopik import server as srv_mod

        srv = srv_mod.create_server(workspace=self.ws, host="127.0.0.1", port=0, lan=False)
        t_srv = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        t_srv.start()

        def get(path: str) -> tuple[int, dict]:
            conn = http.client.HTTPConnection("127.0.0.1", srv.bound_port, timeout=15.0)
            try:
                conn.request("GET", path)
                resp = conn.getresponse()
                return resp.status, json.loads(resp.read().decode("utf-8"))
            finally:
                conn.close()

        try:
            code, state = get("/api/state")
            self.assertEqual(code, 200)
            rep = state["progressReport"]
            self.assertEqual(rep["kind"], "progress_report")
            self.assertEqual(rep["windowDays"], 7)
            self.assertEqual(state["hub"]["progressReport"]["kind"], "progress_report")
            code, month = get("/api/report?days=30")
            self.assertEqual(code, 200)
            self.assertEqual(month["windowDays"], 30)
            for bad in ("0", "366", "abc"):
                code, err = get(f"/api/report?days={bad}")
                self.assertEqual(code, 400, err)
        finally:
            srv.shutdown()
            srv.server_close()

if __name__ == "__main__":
    unittest.main()
