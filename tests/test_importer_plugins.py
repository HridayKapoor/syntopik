#!/usr/bin/env python3
"""Tests for the importer plugin API (syntopik/importer_api.py) and the built-in Kindle importer."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from syntopik.importer_api import (  # noqa: E402
    RESERVED_KINDS,
    _spec_from_object,
    discover_importers,
    list_importers,
    run_plugin_import,
)
from syntopik.storage import load_jsonl  # noqa: E402

CLIPPINGS = """﻿Thinking, Fast and Slow (Daniel Kahneman)
- Your Highlight on page 20 | Location 301-303 | Added on Monday, 3 March 2025 21:04:11

System 1 operates automatically and quickly, with little or no effort.
==========
Thinking, Fast and Slow (Daniel Kahneman)
- Your Note on page 20 | Location 302 | Added on Monday, 3 March 2025 21:05:00

compare with dual-process theory
==========
Thinking, Fast and Slow (Daniel Kahneman)
- Your Bookmark on page 21 | Location 310 | Added on Monday, 3 March 2025 21:06:00


==========
Thinking, Fast and Slow (Daniel Kahneman)
- Your Highlight on page 33 | Location 480-481 | Added on Monday, 3 March 2025 21:10:00

A reliable way to make people believe in falsehoods
==========
Thinking, Fast and Slow (Daniel Kahneman)
- Your Highlight on page 33 | Location 480-482 | Added on Monday, 3 March 2025 21:10:30

A reliable way to make people believe in falsehoods is frequent repetition.
==========
Designing Data-Intensive Applications (Martin Kleppmann)
- Your Highlight on page 5 | Location 70-71 | Added on Tuesday, 4 March 2025 08:00:00

A system is reliable if it continues to work correctly even when faults occur.
==========
"""

LOCAL_PLUGIN = '''
def parse(path, options):
    for line in path.read_text(encoding="utf-8").splitlines():
        term, _, meaning = line.partition(" = ")
        if meaning:
            yield {"q": f"What does {term} mean?", "a": meaning, "tags": ["glossary"], "fsrs": {"stability": 99}}
    yield "not a dict"
    yield {"a": "answer without a question"}

SYNTOPIK_IMPORTER = {"name": "glossary", "description": "term = meaning", "extensions": ["txt"], "parse": parse}
'''


class TestImporterPlugins(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="synt-plugins-")
        self.ws = Path(self._tmp.name)
        self.kg = self.ws / "knowledge-graph"
        self.kg.mkdir()
        (self.kg / "propositions.jsonl").write_text("", encoding="utf-8")
        self.clip = self.ws / "My Clippings.txt"
        self.clip.write_text(CLIPPINGS, encoding="utf-8")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_kindle_builtin_parses_merges_notes_and_dedupes_extended_highlights(self) -> None:
        res = run_plugin_import("kindle", self.clip, workspace=self.ws, kg_dir=self.kg)
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["importedCount"], 3)  # bookmark skipped, extended highlight kept once
        props = load_jsonl(self.kg / "propositions.jsonl")
        by_stmt = {p["statement"]: p for p in props}
        self.assertIn("A reliable way to make people believe in falsehoods is frequent repetition.", by_stmt)
        sys1 = by_stmt["System 1 operates automatically and quickly, with little or no effort."]
        self.assertEqual(sys1["chapter"], "Thinking, Fast and Slow")
        self.assertIn("compare with dual-process theory", sys1["notes"])
        self.assertIn("p. 20", sys1["notes"])
        for p in props:  # zero fake mastery
            self.assertEqual(p["status"], "planned")
            self.assertEqual(p["reps"], 0)
            self.assertIn("kindle", p["tags"])
        # Re-import is idempotent
        again = run_plugin_import("kindle", self.clip, workspace=self.ws, kg_dir=self.kg)
        self.assertEqual(again["importedCount"], 0)
        self.assertEqual(again["deduplicatedCount"], 3)
        self.assertEqual(len(load_jsonl(self.kg / "propositions.jsonl")), 3)

    def test_local_plugins_are_off_until_opted_in(self) -> None:
        plugdir = self.ws / "importers"
        plugdir.mkdir()
        (plugdir / "glossary.py").write_text(LOCAL_PLUGIN, encoding="utf-8")
        src = self.ws / "terms.txt"
        src.write_text("entropy = expected surprise\nquorum = majority subset\n", encoding="utf-8")

        self.assertNotIn("glossary", discover_importers(self.ws, kg_dir=self.kg).importers)
        off = run_plugin_import("glossary", src, workspace=self.ws, kg_dir=self.kg)
        self.assertFalse(off["ok"])
        self.assertIn("Unknown importer", off["error"])

        (self.kg / "study-config.json").write_text(json.dumps({"allowLocalImporters": True}), encoding="utf-8")
        on = run_plugin_import("glossary", src, workspace=self.ws, kg_dir=self.kg)
        self.assertTrue(on["ok"], on)
        self.assertEqual(on["importedCount"], 2)
        self.assertEqual(on["skippedCount"], 2)  # non-dict and question-less items
        for p in load_jsonl(self.kg / "propositions.jsonl"):
            self.assertNotIn("fsrs", p)  # plugins cannot inject scheduling state
            self.assertEqual(p["source"]["plugin"], "local:glossary.py")
        self.assertFalse(list(plugdir.glob("__pycache__")))

    def test_bad_plugins_are_reported_not_raised(self) -> None:
        with self.assertRaises(ValueError):
            _spec_from_object({"name": "anki", "parse": lambda p, o: []}, source="t")
        with self.assertRaises(ValueError):
            _spec_from_object({"name": "x" * 40, "parse": lambda p, o: []}, source="t")
        with self.assertRaises(TypeError):
            _spec_from_object({"name": "noparse"}, source="t")
        with self.assertRaises(ValueError):
            _spec_from_object({"name": "future", "apiVersion": 99, "parse": lambda p, o: []}, source="t")
        self.assertIn("cards", RESERVED_KINDS)

        plugdir = self.ws / "importers"
        plugdir.mkdir()
        (plugdir / "broken.py").write_text("raise RuntimeError('boom')\n", encoding="utf-8")
        (plugdir / "crashy.py").write_text(
            "def parse(path, options):\n    raise ValueError('bad row')\n    yield {}\n"
            "SYNTOPIK_IMPORTER = {'name': 'crashy', 'parse': parse}\n",
            encoding="utf-8",
        )
        (self.kg / "study-config.json").write_text(json.dumps({"allowLocalImporters": True}), encoding="utf-8")
        listing = list_importers(self.ws)
        self.assertIn("kindle", [p["name"] for p in listing["plugins"]])
        self.assertTrue(any("boom" in e["error"] for e in listing["errors"]))
        res = run_plugin_import("crashy", self.clip, workspace=self.ws, kg_dir=self.kg)
        self.assertFalse(res["ok"])
        self.assertIn("bad row", res["error"])
        self.assertEqual(load_jsonl(self.kg / "propositions.jsonl"), [])

    def test_limit_dry_run_and_missing_target(self) -> None:
        res = run_plugin_import("kindle", self.clip, workspace=self.ws, kg_dir=self.kg, limit=1, dry_run=True)
        self.assertTrue(res["ok"])
        self.assertTrue(res["truncated"])
        self.assertEqual(res["importedCount"], 1)
        self.assertEqual(load_jsonl(self.kg / "propositions.jsonl"), [])
        missing = run_plugin_import("kindle", self.ws / "nope.txt", workspace=self.ws, kg_dir=self.kg)
        self.assertFalse(missing["ok"])
        self.assertIn("not found", missing["error"])

    def test_cli_list_and_import(self) -> None:
        def cli(*args: str) -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                [sys.executable, "-B", "-m", "syntopik.cli", "import", *args, "--workspace", str(self.ws), "--json"],
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )

        listed = cli("--list-importers")
        self.assertEqual(listed.returncode, 0, listed.stderr)
        self.assertIn("kindle", [p["name"] for p in json.loads(listed.stdout)["plugins"]])
        imported = cli(str(self.clip), "--kind", "kindle")
        self.assertEqual(imported.returncode, 0, imported.stderr)
        self.assertEqual(json.loads(imported.stdout)["importedCount"], 3)
        unknown = cli(str(self.clip), "--kind", "nosuch")
        self.assertEqual(unknown.returncode, 1)
        self.assertFalse(json.loads(unknown.stdout)["ok"])


if __name__ == "__main__":
    unittest.main()
