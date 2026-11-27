#!/usr/bin/env python3
"""Scale regression tests for the hot paths behind `syntopik next` / `review`.

Pure stdlib. Builds a synthetic graph in memory (no workspace writes) and checks
that FIRe due-queue compression stays near-linear and that the cached
prerequisite map gives exactly the same footprints as the uncached path.
"""

from __future__ import annotations

import random
import sys
import time
import unittest
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


class TestRosettaKeywordMatching(unittest.TestCase):
    def test_keyword_matching_semantics(self) -> None:
        self.assertTrue(_keyword_matches("harmonic oscillator", "a harmonic-oscillator model", "a harmonic-oscillator model"))
        self.assertFalse(_keyword_matches("spring", "springs are elastic", "springs are elastic"))
        self.assertTrue(_keyword_matches("F = -kx", "hooke: f = -kx holds", "hooke: f = -kx holds"))
        self.assertTrue(_keyword_matches("f=-kx", "hooke: f = -kx", "hooke: f = -kx"))
        self.assertFalse(_keyword_matches("", "anything", "anything"))

    def test_match_handles_empty_and_invalid_props(self) -> None:
        self.assertEqual(match_proposition_archetypes({}), [])
        self.assertEqual(match_proposition_archetypes("not a dict"), [])  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
