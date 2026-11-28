#!/usr/bin/env python3
"""Scale regression tests for the hot paths behind `syntopik next` / `review`.

Pure stdlib. Builds synthetic graphs and checks that FIRe due-queue compression
stays near-linear, that the cached prerequisite map gives exactly the same
footprints as the uncached path, and that `next`, `review` and `readiness` stay
within a time budget on a 3,000-concept workspace.
"""

from __future__ import annotations

import json
import random
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from syntopik.fsrs_core import (  # noqa: E402
    build_incoming_prereq_map,
    compress_due_queue,
    fire_ancestor_footprint,
)
from syntopik.interleave import strip_chapter_cues  # noqa: E402
from syntopik.isomorphism import _keyword_matches, match_proposition_archetypes  # noqa: E402


def _synthetic_graph(n: int, seed: int = 7) -> tuple[list[dict], list[dict]]:
    rng = random.Random(seed)
    props = [{"id": f"p{i}", "status": "learning", "retrievability": rng.random()} for i in range(n)]
    chains = []
    for i in range(1, n):
        for j in rng.sample(range(i), min(2, i)):
            chains.append({"id": f"c{i}-{j}", "from": f"p{j}", "to": f"p{i}", "relation": "implies"})
    return props, chains


class TestFireScale(unittest.TestCase):
    def test_cached_prereq_map_matches_uncached_footprints(self) -> None:
        _props, chains = _synthetic_graph(300)
        incoming = build_incoming_prereq_map(chains)
        for nid in ("p5", "p57", "p150", "p299"):
            self.assertEqual(
                fire_ancestor_footprint(nid, chains),
                fire_ancestor_footprint(nid, chains, incoming=incoming),
            )

    def test_compress_due_queue_is_fast_on_large_graphs(self) -> None:
        props, chains = _synthetic_graph(2000)
        due = props[::3]
        t0 = time.perf_counter()
        res = compress_due_queue(due, chains)
        elapsed = time.perf_counter() - t0
        self.assertEqual(res["originalCount"], len(due))
        self.assertEqual(
            res["compressedCount"] + res["knockedOutCount"],
            res["originalCount"],
        )
        # Previously ~O(due x chains): tens of seconds at this size. Generous CI budget.
        self.assertLess(elapsed, 5.0, f"compress_due_queue took {elapsed:.2f}s for {len(due)} due items")


    def test_compress_due_queue_scales_to_large_vaults(self) -> None:
        # 12k due items: the old full-rescan greedy loop took ~14s here; lazy greedy ~0.3s.
        props, chains = _synthetic_graph(12000)
        t0 = time.perf_counter()
        res = compress_due_queue(props, chains)
        elapsed = time.perf_counter() - t0
        self.assertGreater(res["knockedOutCount"], 0)
        self.assertEqual(res["compressedCount"] + res["knockedOutCount"], len(props))
        self.assertLess(elapsed, 4.0, f"compress_due_queue took {elapsed:.2f}s for {len(props)} due items")

    def test_compress_due_queue_greedy_order_is_deterministic(self) -> None:
        props, chains = _synthetic_graph(400, seed=11)
        due = props[::2]
        a = compress_due_queue(due, chains, min_knockouts=1)
        b = compress_due_queue(list(due), list(chains), min_knockouts=1)
        self.assertEqual(a, b)
        # Composites come first and each knocks out >= min_knockouts prerequisites
        knocked = {k["id"] for k in a["knockedOut"]}
        self.assertFalse(knocked & {s["id"] for s in a["selected"]})


class TestInterleaveCueStripping(unittest.TestCase):
    def test_strip_chapter_cues_is_fast_on_large_vaults(self) -> None:
        # One regex compile per proposition used to dominate `syntopik interleave` (~5s on 20k props).
        t0 = time.perf_counter()
        for i in range(20000):
            strip_chapter_cues(f"Concept {i} in Simple Harmonic Motion", chapter=f"Ch{i % 40} Waves", topic="SHM", prop_id=f"prop-{i}")
        elapsed = time.perf_counter() - t0
        self.assertLess(elapsed, 2.5, f"strip_chapter_cues took {elapsed:.2f}s for 20k propositions")

    def test_strip_chapter_cues_semantics(self) -> None:
        self.assertEqual(strip_chapter_cues("In SHM, the period is 2pi sqrt(m/k)", chapter="Simple Harmonic Motion (SHM)"), "The period is 2pi sqrt(m/k)")
        self.assertEqual(strip_chapter_cues("prop-7 restoring force [Kinematics]", chapter="Ch1 Kinematics", prop_id="prop-7"), "Restoring force")
        self.assertEqual(strip_chapter_cues("PROP-7: quorum overlap", prop_id="prop-7"), "Quorum overlap")
        # Code and multi-line setups keep their case, line breaks and indentation.
        code = "def is_up_to_date(a, b):\n    # compare terms first\n    return  a >= b"
        self.assertEqual(strip_chapter_cues(code, chapter="Raft"), "def is_up_to_date(a, b):\n    # compare terms first\n    return a >= b")
        self.assertEqual(strip_chapter_cues("scores = q @ k.T", chapter="Attention"), "scores = q @ k.T")
        # Non-ASCII text skips the fast pre-check and still matches case-insensitively
        self.assertEqual(strip_chapter_cues("Größe in Ämter, Einheit", chapter="ämter"), "Größe Einheit")


class TestRosettaKeywordMatching(unittest.TestCase):
    def test_keyword_matching_semantics(self) -> None:
        self.assertTrue(_keyword_matches("harmonic oscillator", "a harmonic-oscillator model", "a harmonic-oscillator model"))
        self.assertFalse(_keyword_matches("spring", "springs are elastic", "springs are elastic"))
        self.assertTrue(_keyword_matches("F = -kx", "hooke: f = -kx holds", "hooke: f = -kx holds"))
        self.assertTrue(_keyword_matches("f=-kx", "hooke: f = -kx", "hooke: f = -kx"))
        self.assertFalse(_keyword_matches("", "anything", "anything"))
        # The substring pre-check must never reject a real match across separators.
        self.assertTrue(_keyword_matches("spring-mass system", "a spring mass   system", "a spring mass   system"))
        self.assertTrue(_keyword_matches("rc circuit", "an rc-circuit", "an rc-circuit"))
        self.assertFalse(_keyword_matches("rc circuit", "a circuit", "a circuit"))

    def test_match_handles_empty_and_invalid_props(self) -> None:
        self.assertEqual(match_proposition_archetypes({}), [])
        self.assertEqual(match_proposition_archetypes("not a dict"), [])  # type: ignore[arg-type]


class TestConfusablePairs(unittest.TestCase):
    """`find_confusables` prunes and caches its O(n^2) pair scan; results must match the full scan."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="synt-confusables-")
        kg = Path(self._tmp.name) / "knowledge-graph"
        kg.mkdir()
        rng = random.Random(5)
        words = "entropy energy heat work quorum leader log term vote spring force mass charge field wave limit".split()
        tags = ["proof", "limit", "conservation", "symmetry"]
        with (kg / "propositions.jsonl").open("w", encoding="utf-8") as f:
            for i in range(160):
                f.write(json.dumps({
                    "id": f"p{i}",
                    "title": f"{rng.choice(words)} {rng.choice(words)}",
                    "statement": " ".join(rng.sample(words, rng.randint(2, 7))),
                    "chapter": f"ch{i % 6}",
                    "status": rng.choice(["solid", "shaky", "planned"]),
                    "tags": rng.sample(tags, rng.randint(0, 2)),
                }) + "\n")
        with (kg / "chains.jsonl").open("w", encoding="utf-8") as f:
            for i in range(1, 160):
                for j in rng.sample(range(i), min(2, i)):
                    f.write(json.dumps({"from": f"p{j}", "to": f"p{i}", "relation": "implies"}) + "\n")
            for _ in range(8):
                a, b = rng.sample(range(160), 2)
                f.write(json.dumps({"from": f"p{a}", "to": f"p{b}", "relation": "contrasts_with"}) + "\n")
        with (kg / "mistakes.jsonl").open("w", encoding="utf-8") as f:
            for _ in range(10):
                a, b = rng.sample(range(160), 2)
                f.write(json.dumps({"propId": f"p{a}", "confusedWith": f"p{b}"}) + "\n")
        self.kg = kg

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_pruned_top_k_equals_full_ranking(self) -> None:
        gi = _graph_intel()
        G = gi._ensure_graph(self.kg)
        for min_sim in (0.0, 0.3, 0.6):
            # A top large enough never fills the buffer, so nothing is pruned: the full ranking.
            full = gi.find_confusables(G, kg_dir=self.kg, top=10**6, min_similarity=min_sim)
            self.assertGreater(len(full), 300)
            for k in (1, 3, 8, 40, 300):
                with self.subTest(min_sim=min_sim, k=k):
                    self.assertEqual(gi.find_confusables(G, kg_dir=self.kg, top=k, min_similarity=min_sim), full[:k])

    def test_cache_reflects_display_only_changes(self) -> None:
        gi = _graph_intel()
        first = gi.find_confusables(gi._ensure_graph(self.kg), kg_dir=self.kg, top=5)
        rows = [json.loads(line) for line in (self.kg / "propositions.jsonl").read_text(encoding="utf-8").splitlines()]
        target = first[0]["nodeA"]["id"]
        for r in rows:
            if r["id"] == target:
                r["status"] = "misconception"
        (self.kg / "propositions.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
        again = gi.find_confusables(gi._ensure_graph(self.kg), kg_dir=self.kg, top=5)
        self.assertEqual(again[0]["nodeA"]["status"], "misconception")
        self.assertEqual([p["confusableScore"] for p in again], [p["confusableScore"] for p in first])

    def test_disk_cache_survives_restarts_and_bad_files(self) -> None:
        gi = _graph_intel()
        first = gi.find_confusables(gi._ensure_graph(self.kg), kg_dir=self.kg, top=6)
        cache_file = self.kg / ".cache" / "confusables.json"
        self.assertTrue(cache_file.is_file())
        gi._CONFUSABLE_CACHE.clear()  # a new process starts with an empty in-memory cache
        self.assertEqual(gi.find_confusables(gi._ensure_graph(self.kg), kg_dir=self.kg, top=6), first)
        cache_file.write_text("{not json", encoding="utf-8")
        gi._CONFUSABLE_CACHE.clear()
        self.assertEqual(gi.find_confusables(gi._ensure_graph(self.kg), kg_dir=self.kg, top=6), first)
        self.assertIn("entries", json.loads(cache_file.read_text(encoding="utf-8")))


def _graph_intel():
    scripts = str(ROOT / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    import graph_intel  # noqa: PLC0415

    return graph_intel


# End-to-end budget: a learner workspace the size the plan measured (3,000 concepts,
# ~6,000 links, 30,000 reviews). Before the brainstem fix `next` took 9.6 s here;
# it now takes well under 1 s. The budget leaves room for slow CI runners while still
# catching a return of that kind of quadratic slowdown.
LARGE_VAULT_PROPS = 3000
LARGE_VAULT_BUDGET_SEC = 6.0


def _write_large_vault(ws: Path, n: int = LARGE_VAULT_PROPS, seed: int = 11) -> None:
    rng = random.Random(seed)
    kg = ws / "knowledge-graph"
    kg.mkdir(parents=True)
    (ws / "chapters").mkdir()
    now = datetime(2026, 11, 27, tzinfo=timezone.utc)
    statuses = ["solid", "shaky", "learning", "planned", "misconception"]
    ratings = ["again", "hard", "good", "easy"]
    with (kg / "propositions.jsonl").open("w", encoding="utf-8") as f:
        for i in range(n):
            row = {
                "id": f"p{i}",
                "title": f"Concept {i} about topic {i % 97}",
                "statement": f"Statement {i} " + "word " * 25,
                "chapter": f"ch{i % 40}",
                "status": rng.choice(statuses),
                "nextReview": (now + timedelta(days=rng.randint(-20, 30))).date().isoformat(),
                "stability": round(rng.random() * 30, 3),
                "difficulty": round(rng.random() * 10, 3),
            }
            f.write(json.dumps(row) + "\n")
    with (kg / "chains.jsonl").open("w", encoding="utf-8") as f:
        for i in range(1, n):
            for j in rng.sample(range(i), min(2, i)):
                f.write(json.dumps({"id": f"c{i}-{j}", "from": f"p{j}", "to": f"p{i}", "relation": "implies"}) + "\n")
    with (kg / "revlog.jsonl").open("w", encoding="utf-8") as f:
        for _ in range(n * 10):
            f.write(
                json.dumps(
                    {
                        "propId": f"p{rng.randrange(n)}",
                        "ts": (now - timedelta(days=rng.random() * 365)).isoformat(),
                        "rating": rng.choice(ratings),
                        "elapsedDays": rng.randint(0, 30),
                        "durationMs": rng.randint(5000, 90000),
                    }
                )
                + "\n"
            )


class TestLargeVaultBudget(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory(prefix="synt-bigvault-")
        cls.ws = Path(cls._tmp.name)
        _write_large_vault(cls.ws)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def _timed_cli(self, *args: str) -> tuple[float, dict]:
        start = time.perf_counter()
        proc = subprocess.run(
            [sys.executable, "-B", "-m", "syntopik.cli", *args, "--workspace", str(self.ws), "--json"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        elapsed = time.perf_counter() - start
        self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])
        return elapsed, json.loads(proc.stdout)

    def test_daily_commands_stay_fast_on_a_3000_concept_vault(self) -> None:
        for cmd in ("next", "review", "readiness"):
            with self.subTest(cmd=cmd):
                elapsed, payload = self._timed_cli(cmd)
                self.assertIsInstance(payload, dict)
                self.assertLess(
                    elapsed,
                    LARGE_VAULT_BUDGET_SEC,
                    f"syntopik {cmd} took {elapsed:.2f}s on {LARGE_VAULT_PROPS} concepts (budget {LARGE_VAULT_BUDGET_SEC}s)",
                )


if __name__ == "__main__":
    unittest.main()
