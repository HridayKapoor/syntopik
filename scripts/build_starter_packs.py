#!/usr/bin/env python3
"""Deterministic Generator & Verifier for Syntopik v3.0 Zero-PII Flagship Starter Packs.

Builds and verifies 3 curated, 100% original textbook-grade Starter Packs in `packs/`:
  1. `packs/raft-consensus.syntopik-pack.zip` (Distributed Systems)
  2. `packs/shm-physics.syntopik-pack.zip` (Physics — 100% original textbook content, zero user data)
  3. `packs/transformers-attention.syntopik-pack.zip` (Machine Learning)

Each pack uses `syntopik.packs.export_pack()` + `verify_pack()` + `inspect_pack()`,
includes 10 propositions + conjunctive `∧` chains, a chapter Markdown file with `:::cue`
strips and a validated `:::explore` lab, 2 zero-PII Blunder FEN Forks, 1 live-verified
Counterexample Arena, 1 Derivation DAG (`derivations.jsonl`), and a 4-question timed exam
spec — all SHA-256 checksummed, deterministic, `< 40 KB` each, with ZERO PII keys.

Pure Python 3.12+ standard library only (zero external dependencies).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import zipfile
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from syntopik.explorable import parse_explorable_blocks  # noqa: E402
from syntopik.packs import (  # noqa: E402
    CHECKSUMS_NAME,
    DATA_NAME,
    EXCLUDED_PERSONAL_FILES,
    MANIFEST_NAME,
    STRIPPED_PERSONAL_FIELDS,
    export_pack,
    import_pack,
    inspect_pack,
    verify_pack,
)

FIXED_CREATED_AT = "2026-11-26T00:00:00Z"
FIXED_ZIP_TIME = (2026, 11, 26, 0, 0, 0)
MAX_STARTER_PACK_BYTES = 40 * 1024  # 40 KB ceiling per starter pack
_STRIPPED_SET = frozenset(STRIPPED_PERSONAL_FIELDS)
_ABS_PATH_RE = re.compile(r"(?:/Users/[^\s\"']+|/home/[^\s\"']+|[A-Za-z]:\\[^\s\"']+)")


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _deep_strip(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _deep_strip(v) for k, v in obj.items() if k not in _STRIPPED_SET}
    if isinstance(obj, list):
        return [_deep_strip(item) for item in obj]
    return obj


def _scan_pii(obj: Any, prefix: str = "") -> list[str]:
    leaks: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in _STRIPPED_SET:
                leaks.append(f"{prefix}{k}")
            leaks.extend(_scan_pii(v, f"{prefix}{k}."))
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            leaks.extend(_scan_pii(item, f"{prefix}[{i}]."))
    elif isinstance(obj, str):
        if _ABS_PATH_RE.search(obj):
            leaks.append(f"{prefix}<abs-path>")
    return leaks


# ===========================================================================
# PACK 1: RAFT CONSENSUS (Distributed Systems)
# ===========================================================================


def _spec_raft_consensus() -> dict[str, Any]:
    slug = "raft-consensus"
    chapter = "Raft Consensus"
    subject = "Systems"

    propositions = [
        {
            "id": "sys-raft-quorum",
            "title": "Quorum Intersection Principle",
            "subject": subject,
            "chapter": chapter,
            "kind": "axiom",
            "statement": "In a cluster of N servers, any two majority quorums of size floor(N/2) + 1 intersect in at least one server.",
            "boundaryConditions": ["Requires static/joint membership N known consistently during overlap."],
            "cues": [
                {
                    "id": "c1",
                    "q": "Why must two majority quorums in an N-node Raft cluster share at least one server?",
                    "a": "Because 2 * (floor(N/2) + 1) > N, so by Pigeonhole Principle at least one server belongs to both sets.",
                }
            ],
        },
        {
            "id": "sys-raft-term-monotonic",
            "title": "Monotonic Logical Terms & Single-Vote-Per-Term",
            "subject": subject,
            "chapter": chapter,
            "kind": "axiom",
            "statement": "Each Raft server stores a monotonically non-decreasing currentTerm and grants at most one vote per term on a first-come-first-served basis.",
            "boundaryConditions": ["currentTerm and votedFor must be persisted to stable storage before replying to RPCs."],
            "cues": [
                {
                    "id": "c1",
                    "q": "What invariant breaks if a server forgets votedFor across a crash-restart in the same term?",
                    "a": "It could vote for a second candidate in the same term, enabling two leaders in one term.",
                }
            ],
        },
        {
            "id": "sys-raft-election-safety",
            "title": "Election Safety Invariant",
            "subject": subject,
            "chapter": chapter,
            "kind": "theorem",
            "statement": "At most one leader can be elected in any given Raft term.",
            "cues": [
                {
                    "id": "c1",
                    "q": "Which two axioms jointly guarantee Election Safety?",
                    "a": "Quorum Intersection (any two majorities overlap) and Single-Vote-Per-Term (the overlapping voter cannot endorse both candidates).",
                }
            ],
        },
        {
            "id": "sys-raft-log-append",
            "title": "Leader Append-Only Log Structure",
            "subject": subject,
            "chapter": chapter,
            "kind": "definition",
            "statement": "A Raft leader never overwrites or deletes entries in its own log; it only appends new (term, index, command) entries.",
            "cues": [
                {
                    "id": "c1",
                    "q": "How are log entries uniquely identified across servers?",
                    "a": "By the pair (index, term), where term records when the leader created the entry.",
                }
            ],
        },
        {
            "id": "sys-raft-log-matching",
            "title": "Log Matching Property",
            "subject": subject,
            "chapter": chapter,
            "kind": "theorem",
            "statement": "If two logs contain an entry with the same index and term, then the logs are identical in all entries up through that index.",
            "cues": [
                {
                    "id": "c1",
                    "q": "What two mechanisms enforce the Log Matching Property inductively?",
                    "a": "At most one entry is created at a given (index, term), and AppendEntries verifies prevLogIndex/prevLogTerm before appending.",
                }
            ],
        },
        {
            "id": "sys-raft-uptodate-log",
            "title": "Up-to-Date Log Election Restriction",
            "subject": subject,
            "chapter": chapter,
            "kind": "invariant",
            "statement": "A voter grants a RequestVote only if the candidate's log has a strictly higher lastTerm, or equal lastTerm and length >= voter's log length.",
            "boundaryConditions": ["Comparing log length before lastTerm violates Leader Completeness."],
            "cues": [
                {
                    "id": "c1",
                    "q": "Candidate A has log [(1,a),(1,b),(1,c)] and Candidate B has [(1,a),(2,d)]. Which log is more up-to-date?",
                    "a": "Candidate B, because its last term (2) exceeds Candidate A's last term (1) despite shorter length.",
                }
            ],
        },
        {
            "id": "sys-raft-leader-completeness",
            "title": "Leader Completeness Property",
            "subject": subject,
            "chapter": chapter,
            "kind": "theorem",
            "statement": "If a log entry is committed in a given term, that entry will be present in the logs of all leaders for all higher-numbered terms.",
            "cues": [
                {
                    "id": "c1",
                    "q": "Why must a newly elected leader contain every previously committed entry?",
                    "a": "Its election quorum overlaps the commit quorum in at least one voter holding the entry, and the Up-to-Date check rejects any candidate missing it.",
                }
            ],
        },
        {
            "id": "sys-raft-figure8-commit",
            "title": "Current-Term Commit Rule (Figure 8 Safety)",
            "subject": subject,
            "chapter": chapter,
            "kind": "invariant",
            "statement": "A leader never commits log entries from previous terms purely by counting replicas; it only commits entries from its own currentTerm, which commits prior entries indirectly via Log Matching.",
            "boundaryConditions": ["Applies even when an old-term entry is already stored on a majority of servers."],
            "cues": [
                {
                    "id": "c1",
                    "q": "Why can a majority-replicated entry from an older term still be overwritten if committed directly?",
                    "a": "A server with a higher intermediate term entry can win election over the majority that only holds the older term, then overwrite that index (Raft Figure 8).",
                }
            ],
        },
        {
            "id": "sys-raft-split-brain",
            "title": "Partition Safety & Split-Brain Prevention",
            "subject": subject,
            "chapter": chapter,
            "kind": "application",
            "statement": "Under a network partition, only the side containing a majority quorum can elect a leader and commit entries; an isolated minority leader cannot commit writes.",
            "cues": [
                {
                    "id": "c1",
                    "q": "In a 5-node cluster partitioned 2 vs 3, what happens to writes sent to an old leader stuck in the 2-node side?",
                    "a": "They cannot reach 3 acknowledgements, so they never commit and are rolled back once the partition heals.",
                }
            ],
        },
        {
            "id": "sys-raft-state-machine-safety",
            "title": "State Machine Safety Invariant",
            "subject": subject,
            "chapter": chapter,
            "kind": "theorem",
            "statement": "If any server has applied a log entry at a given index to its state machine, no other server will ever apply a different command for that same index.",
            "cues": [
                {
                    "id": "c1",
                    "q": "How do Leader Completeness and Log Matching combine to prove State Machine Safety?",
                    "a": "Every future leader inherits committed entries (Leader Completeness), and matching (index, term) forces identical prefixes (Log Matching).",
                }
            ],
        },
    ]

    chains = [
        {
            "premises": ["sys-raft-quorum", "sys-raft-term-monotonic"],
            "conclusion": "sys-raft-election-safety",
            "relation": "proves",
            "why": "Overlapping majorities + at most one vote per term forbid dual leaders in one term.",
            "encompassWeight": 0.45,
        },
        {
            "premises": ["sys-raft-election-safety", "sys-raft-log-append"],
            "conclusion": "sys-raft-log-matching",
            "relation": "proves",
            "why": "Unique leader per term + append-only + prevLog check guarantee identical prefixes.",
            "encompassWeight": 0.45,
        },
        {
            "premises": ["sys-raft-term-monotonic", "sys-raft-log-append"],
            "conclusion": "sys-raft-uptodate-log",
            "relation": "requires",
            "why": "Log recency ordering depends on monotonic terms and append-only indices.",
            "encompassWeight": 0.40,
        },
        {
            "premises": ["sys-raft-quorum", "sys-raft-uptodate-log", "sys-raft-log-matching"],
            "conclusion": "sys-raft-leader-completeness",
            "relation": "proves",
            "why": "Commit and election quorums intersect at a voter whose up-to-date log blocks stale candidates.",
            "encompassWeight": 0.38,
        },
        {
            "premises": ["sys-raft-leader-completeness", "sys-raft-term-monotonic"],
            "conclusion": "sys-raft-figure8-commit",
            "relation": "requires",
            "why": "Current-term commit closes the Figure 8 gap where an older term loses to a higher-term log.",
            "encompassWeight": 0.42,
        },
        {
            "premises": ["sys-raft-quorum", "sys-raft-election-safety"],
            "conclusion": "sys-raft-split-brain",
            "relation": "implies",
            "why": "Minority partitions lack quorum and cannot elect or commit.",
            "encompassWeight": 0.45,
        },
        {
            "premises": ["sys-raft-log-matching", "sys-raft-leader-completeness", "sys-raft-figure8-commit"],
            "conclusion": "sys-raft-state-machine-safety",
            "relation": "proves",
            "why": "Safe commit + leader completeness + prefix matching enforce identical applied commands.",
            "encompassWeight": 0.38,
        },
    ]

    topics = [
        {
            "id": "raft-t1",
            "order": 1,
            "title": "Quorums, Terms & Election Safety",
            "level": "basics",
            "prereqTopicIds": [],
            "propIds": ["sys-raft-quorum", "sys-raft-term-monotonic", "sys-raft-election-safety"],
        },
        {
            "id": "raft-t2",
            "order": 2,
            "title": "Log Replication & Up-to-Date Restriction",
            "level": "main",
            "prereqTopicIds": ["raft-t1"],
            "propIds": ["sys-raft-log-append", "sys-raft-log-matching", "sys-raft-uptodate-log"],
        },
        {
            "id": "raft-t3",
            "order": 3,
            "title": "Leader Completeness, Figure 8 & Partition Safety",
            "level": "advanced",
            "prereqTopicIds": ["raft-t2"],
            "propIds": [
                "sys-raft-leader-completeness",
                "sys-raft-figure8-commit",
                "sys-raft-split-brain",
                "sys-raft-state-machine-safety",
            ],
        },
    ]

    mistakes = [
        {
            "propId": "sys-raft-uptodate-log",
            "subject": subject,
            "chapter": chapter,
            "category": "condition_missed",
            "triggerCue": "Comparing candidate vs voter logs in RequestVote",
            "wrongMove": "Checked len(candidate_log) >= len(voter_log) before comparing lastLogTerm",
            "correctMove": "Compare lastLogTerm first; break ties by log length only when lastLogTerm is equal",
        },
        {
            "propId": "sys-raft-figure8-commit",
            "subject": subject,
            "chapter": chapter,
            "category": "misconception",
            "triggerCue": "Leader sees a previous-term entry replicated on a majority of nodes",
            "wrongMove": "Advanced commitIndex directly by counting replicas on an older-term entry",
            "correctMove": "Wait until an entry from currentTerm is replicated on a majority, committing prior entries indirectly",
        },
        {
            "propId": "sys-raft-quorum",
            "subject": subject,
            "chapter": chapter,
            "category": "off_by_one",
            "triggerCue": "Sizing a cluster to tolerate f crash failures",
            "wrongMove": "Used N = 2*f nodes instead of 2*f + 1 (leaving even-sized split 2-vs-2 deadlocks)",
            "correctMove": "Require N = 2*f + 1 servers so quorum size floor(N/2)+1 = f+1 tolerates f failures cleanly",
        },
    ]

    derivations = [
        {
            "id": "deriv-raft-safety",
            "propId": "sys-raft-state-machine-safety",
            "chapter": chapter,
            "title": "From Quorum Intersection to State Machine Safety",
            "domain": subject,
            "steps": [
                {
                    "id": "s1",
                    "label": "Quorum Intersection",
                    "expr": "2 * (floor(N / 2) + 1) > N",
                    "role": "axiom",
                    "dependsOn": [],
                    "note": "Any two majorities overlap in >= 1 server.",
                },
                {
                    "id": "s2",
                    "label": "Single Vote Per Term",
                    "expr": "votes_granted(server, term) <= 1",
                    "role": "axiom",
                    "dependsOn": [],
                    "note": "Persisted votedFor prevents double voting in a term.",
                },
                {
                    "id": "s3",
                    "label": "Election Safety",
                    "expr": "leaders(term) <= 1",
                    "role": "step",
                    "dependsOn": ["s1", "s2"],
                    "note": "Overlapping voter cannot grant votes to two candidates in the same term.",
                },
                {
                    "id": "s4",
                    "label": "Log Matching",
                    "expr": "same_term_and_index => identical_prefix",
                    "role": "step",
                    "dependsOn": ["s3"],
                    "note": "AppendEntries prevLogIndex/prevLogTerm check inducts backward.",
                },
                {
                    "id": "s5",
                    "label": "State Machine Safety",
                    "expr": "applied(s_a, i) == applied(s_b, i)",
                    "role": "theorem",
                    "dependsOn": ["s1", "s4"],
                    "note": "Leader Completeness + Log Matching guarantee identical commands at every index.",
                },
            ],
        }
    ]

    chapter_md = """# Raft Consensus — Safety Invariants & Fault Tolerance

Consensus reduces a replicated state machine to one disciplined question: **how do independent servers agree on a single ordered log despite crashes and network partitions?**

:::cue id=raft-quorum-cue prop=sys-raft-quorum
Q: What is the minimum cluster size N required to tolerate f simultaneous crash failures while preserving majority availability?
A: N = 2f + 1 servers (quorum Q = f + 1). An even size 2f gives no extra fault tolerance over 2f - 1 because Q = f + 1 leaves only f - 1 spare nodes.
:::

## Quorum Availability & Fault-Tolerance Explorer

Manipulate total servers `N` and crashed nodes `f` to see why odd cluster sizes minimize overhead while guaranteeing strict quorum overlap.

:::explore raft-quorum-lab
{
  "id": "raft-quorum-lab",
  "title": "Raft Quorum Overlap & Crash Tolerance Lab",
  "prompt": "Compare majority quorum Q = floor(N/2) + 1 against available nodes (N - f). Notice why N = 4 tolerates only f = 1 crash—no better than N = 3.",
  "params": [
    {"name": "N", "label": "Cluster size N", "min": 3, "max": 15, "step": 1, "default": 5, "unit": "nodes"},
    {"name": "f", "label": "Failed nodes f", "min": 0, "max": 7, "step": 1, "default": 1, "unit": "nodes"}
  ],
  "outputs": [
    {"label": "Majority quorum Q", "expr": "floor(N / 2) + 1", "unit": "nodes"},
    {"label": "Max crash tolerance", "expr": "floor((N - 1) / 2)", "unit": "nodes"},
    {"label": "Healthy margin (N - f - Q)", "expr": "(N - f) - (floor(N / 2) + 1)", "unit": "nodes"},
    {"label": "Min quorum overlap (2Q - N)", "expr": "2 * (floor(N / 2) + 1) - N", "unit": "nodes"}
  ],
  "curve": {
    "xVar": "x",
    "xMin": 3,
    "xMax": 15,
    "samples": 25,
    "exactExpr": "floor(x / 2) + 1",
    "approxExpr": "x / 2 + 0.5",
    "regimeThreshold": 9,
    "yLabel": "Quorum size Q(N)"
  },
  "limitCheck": {
    "question": "For a 5-node cluster (N = 5), what is the exact minimum quorum size Q?",
    "var": "N",
    "target": 5,
    "expectedExpr": "floor(N / 2) + 1",
    "expectedVal": 3,
    "hint": "Compute floor(5/2) + 1 so two quorums sum to 6 > 5."
  }
}
:::

## Log Matching & The Figure 8 Rule

Every committed entry must survive all future leader elections. That requires two guards working in conjunction:

1. **Up-to-Date Log Restriction**: compare `lastLogTerm` strictly *before* comparing log length.
2. **Current-Term Commit Rule**: a leader commits entries from previous terms only indirectly once an entry from its *current* term reaches a majority.

:::cue id=raft-fig8-cue prop=sys-raft-figure8-commit
Q: A term-4 leader sees a term-2 log entry replicated on 3 of 5 nodes. May it immediately advance commitIndex to that entry?
A: No. Under the Figure 8 rule, it must wait until a term-4 entry is replicated on a majority; counting replicas on an old-term entry can still be overwritten by a higher-term competitor.
:::
"""

    forks = [
        {
            "id": "fork-raft-uptodate",
            "propId": "sys-raft-uptodate-log",
            "subject": subject,
            "chapter": chapter,
            "prefixSteps": [
                "def is_log_up_to_date(cand_term, cand_len, voter_term, voter_len):",
                "    # Decide whether candidate log is at least as up-to-date as voter log",
            ],
            "forkPrompt": "Write the single return expression implementing Raft's up-to-date log check.",
            "sealedBlunder": "return cand_len >= voter_len",
            "sealedWinningMove": "return (cand_term, cand_len) >= (voter_term, voter_len)",
            "refutationMsg": "Checking length alone lets a stale term-1 log of length 5 beat a committed term-3 log of length 2. Compare last term first, then length.",
            "verifySpec": {
                "engine": "code",
                "lang": "python",
                "entry": "is_log_up_to_date",
                "template": "def is_log_up_to_date(cand_term, cand_len, voter_term, voter_len):\n    {{MOVE}}\n",
                "tests": [
                    {"args": [3, 2, 2, 5], "expected": True},
                    {"args": [2, 5, 3, 2], "expected": False},
                    {"args": [3, 4, 3, 4], "expected": True},
                    {"args": [3, 3, 3, 4], "expected": False},
                ],
            },
            "status": "active",
        },
        {
            "id": "fork-raft-quorum-size",
            "propId": "sys-raft-quorum",
            "subject": subject,
            "chapter": chapter,
            "prefixSteps": [
                "A Raft cluster must tolerate f simultaneous crash failures.",
                "Every write needs a strict majority quorum of the total N servers.",
            ],
            "forkPrompt": "Express the minimum cluster size N in terms of f.",
            "sealedBlunder": "2*f",
            "sealedWinningMove": "2*f + 1",
            "refutationMsg": "With N = 2*f, quorum is f + 1, leaving only f - 1 spare servers—so f failures halt the cluster.",
            "verifySpec": {"engine": "math"},
            "status": "active",
        },
    ]

    arenas = [
        {
            "id": "arena-raft-uptodate-check",
            "propId": "sys-raft-uptodate-log",
            "domain": "code",
            "title": "Hunt Bugs in Raft RequestVote Up-to-Date Log Check",
            "claim": "is_log_up_to_date(cand_term, cand_idx, voter_term, voter_idx) returns True iff (cand_term > voter_term) or (cand_term == voter_term and cand_idx >= voter_idx) for non-negative integers.",
            "entry": "is_log_up_to_date",
            "lang": "python",
            "sealedReference": {
                "entry": "is_log_up_to_date",
                "code": (
                    "def is_log_up_to_date(cand_term, cand_idx, voter_term, voter_idx):\n"
                    "    if cand_term != voter_term:\n"
                    "        return cand_term > voter_term\n"
                    "    return cand_idx >= voter_idx\n"
                ),
            },
            "sealedMutants": [
                {
                    "id": "m1",
                    "trapTag": "length_before_term",
                    "code": (
                        "def is_log_up_to_date(cand_term, cand_idx, voter_term, voter_idx):\n"
                        "    if cand_idx != voter_idx:\n"
                        "        return cand_idx > voter_idx\n"
                        "    return cand_term >= voter_term\n"
                    ),
                },
                {
                    "id": "m2",
                    "trapTag": "strict_gt_on_equal_term",
                    "code": (
                        "def is_log_up_to_date(cand_term, cand_idx, voter_term, voter_idx):\n"
                        "    if cand_term != voter_term:\n"
                        "        return cand_term > voter_term\n"
                        "    return cand_idx > voter_idx\n"
                    ),
                },
                {
                    "id": "m3",
                    "trapTag": "ignore_index_on_tie",
                    "code": (
                        "def is_log_up_to_date(cand_term, cand_idx, voter_term, voter_idx):\n"
                        "    return cand_term >= voter_term\n"
                    ),
                },
            ],
            "assumptionPredicates": [
                {
                    "description": "4 non-negative integer arguments (cand_term, cand_idx, voter_term, voter_idx)",
                    "code": (
                        "def check(raw):\n"
                        "    a = raw.get('args') if isinstance(raw, dict) else raw\n"
                        "    return isinstance(a, list) and len(a) == 4 and all(isinstance(v, int) and not isinstance(v, bool) and v >= 0 for v in a)\n"
                    ),
                },
            ],
            "killedMutants": [],
            "lemmaRepairs": [],
        }
    ]

    exam = {
        "schemaVersion": 1,
        "examSlug": f"{slug}-exam",
        "title": "Raft Consensus — 4-Question Mastery Check",
        "examType": "mastery",
        "durationMinutes": 12,
        "instructions": [
            "Answer all 4 questions without consulting notes.",
            "Verify quorum overlap and Figure 8 preconditions before committing an answer.",
        ],
        "questions": [
            {
                "id": "q1",
                "type": "single_correct",
                "subject": subject,
                "chapter": chapter,
                "propId": "sys-raft-uptodate-log",
                "stem": "Voter V has log [(term 1, idx 1), (term 2, idx 2)]. Candidate C has [(term 1, idx 1), (term 1, idx 2), (term 1, idx 3)]. What does V reply to C's RequestVote?",
                "options": [
                    {"id": "A", "text": "Reject vote; voter has higher last term"},
                    {"id": "B", "text": "Grant vote; candidate has longer log"},
                    {"id": "C", "text": "Grant vote; terms match at index 1"},
                    {"id": "D", "text": "Defer vote until heartbeat timeout"},
                ],
                "answer": "A",
                "marksCorrect": 4,
                "marksWrong": -1,
                "solution": "Raft compares lastLogTerm first (2 > 1), so V rejects C despite C having a longer log.",
            },
            {
                "id": "q2",
                "type": "multi_correct",
                "subject": subject,
                "chapter": chapter,
                "propId": "sys-raft-leader-completeness",
                "stem": "Select ALL properties required to prove Raft's Election Safety and Leader Completeness invariants:",
                "options": [
                    {"id": "A", "text": "Any two majority quorums intersect in at least one server"},
                    {"id": "B", "text": "Each server grants at most one vote per term"},
                    {"id": "C", "text": "Synchronized physical wall-clock timestamps across servers"},
                    {"id": "D", "text": "Voters reject candidates whose logs are less up-to-date"},
                ],
                "answer": ["A", "B", "D"],
                "marksCorrect": 4,
                "marksWrong": -2,
                "partialMarking": True,
                "solution": "Raft safety never depends on physical clocks; only quorum overlap, single-vote-per-term, and the up-to-date log check are required.",
            },
            {
                "id": "q3",
                "type": "numerical",
                "subject": subject,
                "chapter": chapter,
                "propId": "sys-raft-quorum",
                "stem": "A Raft cluster has N = 7 servers. What is the minimum majority quorum size Q needed to commit an entry?",
                "answer": 4,
                "tolerance": 0.0,
                "marksCorrect": 4,
                "marksWrong": 0,
                "solution": "Q = floor(7 / 2) + 1 = 4 servers.",
            },
            {
                "id": "q4",
                "type": "matrix_match",
                "subject": subject,
                "chapter": chapter,
                "propId": "sys-raft-state-machine-safety",
                "stem": "Match each Raft safety property (P–R) to the primary mechanism that enforces it (1–3):",
                "rows": [
                    {"id": "P", "text": "Election Safety"},
                    {"id": "Q", "text": "Log Matching"},
                    {"id": "R", "text": "Figure 8 Commit Safety"},
                ],
                "cols": [
                    {"id": "1", "text": "Quorum intersection + 1 vote per term"},
                    {"id": "2", "text": "AppendEntries prevLogIndex/prevLogTerm check"},
                    {"id": "3", "text": "Commit prior terms only via a current-term majority entry"},
                ],
                "answer": {"P": ["1"], "Q": ["2"], "R": ["3"]},
                "marksCorrect": 6,
                "marksPerRow": 2,
                "marksWrong": 0,
                "solution": "P->1, Q->2, R->3.",
            },
        ],
    }

    return {
        "slug": slug,
        "title": "Raft Consensus — Safety Invariants & Partitions",
        "subject": subject,
        "chapter": chapter,
        "propositions": propositions,
        "chains": chains,
        "topics": topics,
        "mistakes": mistakes,
        "derivations": derivations,
        "chapter_md": chapter_md,
        "forks": forks,
        "arenas": arenas,
        "exam": exam,
        "arenaTestInputs": [
            {"args": [2, 5, 3, 2], "kills": "m1"},
            {"args": [3, 4, 3, 4], "kills": "m2"},
            {"args": [3, 2, 3, 4], "kills": "m3"},
        ],
    }


# ===========================================================================
# PACK 2: SIMPLE HARMONIC MOTION (Physics — 100% Original Textbook Content)
# ===========================================================================


def _spec_shm_physics() -> dict[str, Any]:
    slug = "shm-physics"
    chapter = "Simple Harmonic Motion"
    subject = "Physics"

    propositions = [
        {
            "id": "phys-shm-linearization",
            "title": "Equilibrium Linearization & Restoring Curvature",
            "subject": subject,
            "chapter": chapter,
            "kind": "axiom",
            "statement": "Near any stable potential minimum x0 where U'(x0) = 0 and k_eff = U''(x0) > 0, small displacements obey the linear restoring law F(x) = -k_eff*(x - x0).",
            "boundaryConditions": ["Holds strictly for small amplitudes where cubic/quartic terms in U(x) remain negligible."],
            "cues": [
                {
                    "id": "c1",
                    "q": "How is the effective stiffness k_eff extracted from a smooth potential energy curve U(x) at equilibrium x0?",
                    "a": "k_eff = d^2 U / dx^2 evaluated at x = x0 (provided U'(x0) = 0 and U''(x0) > 0).",
                }
            ],
        },
        {
            "id": "phys-shm-ode-solution",
            "title": "Canonical 1-D SHM Equation & Kinematics",
            "subject": subject,
            "chapter": chapter,
            "kind": "theorem",
            "statement": "The differential equation d^2x/dt^2 + omega^2 x = 0 has general solution x(t) = A cos(omega t + phi), giving v^2 = omega^2 (A^2 - x^2) and period T = 2*pi/omega independent of amplitude A.",
            "cues": [
                {
                    "id": "c1",
                    "q": "Does doubling the amplitude A of an ideal linear oscillator change its period T?",
                    "a": "No. In linear SHM, restoring acceleration scales linearly with A, so T = 2*pi/omega is strictly amplitude-independent (isochronous).",
                }
            ],
        },
        {
            "id": "phys-shm-phasor-circle",
            "title": "Phasor Circle & Direction-Sensitive Elapsed Time",
            "subject": subject,
            "chapter": chapter,
            "kind": "derivation",
            "statement": "Projecting uniform circular motion of radius A and angular speed omega onto the x-axis maps each (x, sign(v)) state to a unique angle phi, turning elapsed time into delta_t = delta_phi / omega.",
            "boundaryConditions": ["Two states with the same x but opposite velocity signs occupy mirror quadrants on the phasor circle."],
            "cues": [
                {
                    "id": "c1",
                    "q": "For x(t) = A cos(omega t), why is elapsed time from x = 0 to x = A/2 (T/12) much shorter than from x = A/2 to x = A (T/6)?",
                    "a": "Because cos(phi) changes fastest near phi = pi/2 (high speed at mean) and flattens near phi = 0 (turning point), spanning pi/6 vs pi/3 radians.",
                }
            ],
        },
        {
            "id": "phys-shm-energy-curvature",
            "title": "Quadratic Energy Partition & 2*omega Frequency",
            "subject": subject,
            "chapter": chapter,
            "kind": "theorem",
            "statement": "Total mechanical energy E = (1/2) k A^2 stays constant while K(t) = E sin^2(omega t + phi) and U(t) = E cos^2(omega t + phi) each oscillate at angular frequency 2*omega.",
            "cues": [
                {
                    "id": "c1",
                    "q": "If displacement oscillates with period T, what is the oscillation period of kinetic energy K(t)?",
                    "a": "T/2 (angular frequency 2*omega), because K = (1/2) m v^2 peaks twice per cycle as the particle crosses equilibrium in both directions.",
                }
            ],
        },
        {
            "id": "phys-shm-constant-force",
            "title": "Period Invariance Under Constant External Force",
            "subject": subject,
            "chapter": chapter,
            "kind": "theorem",
            "statement": "Adding a constant force F0 (such as gravity on a vertical spring) shifts the equilibrium position by delta_x = F0 / k without changing stiffness k or oscillation period T.",
            "cues": [
                {
                    "id": "c1",
                    "q": "A vertical mass-spring oscillator is taken to the Moon where gravity is g/6. How does its period change?",
                    "a": "Unchanged: gravity shifts equilibrium extension mg/k but does not alter restoring stiffness k = U''(x0).",
                }
            ],
        },
        {
            "id": "phys-shm-spring-combos",
            "title": "Series & Parallel Spring Stiffness Laws",
            "subject": subject,
            "chapter": chapter,
            "kind": "derivation",
            "statement": "Parallel springs share displacement x so k_eq = k1 + k2; series springs share tension F so 1/k_eq = 1/k1 + 1/k2, giving k_eq = k1*k2/(k1 + k2).",
            "cues": [
                {
                    "id": "c1",
                    "q": "If you cut a uniform spring of stiffness k into two equal halves, what is the stiffness of each half?",
                    "a": "2k, because two halves in series must recombine to k: 1/(2k) + 1/(2k) = 1/k.",
                }
            ],
        },
        {
            "id": "phys-shm-reduced-mass",
            "title": "Two-Body Spring Oscillator & Reduced Mass",
            "subject": subject,
            "chapter": chapter,
            "kind": "derivation",
            "statement": "Two free masses m1 and m2 joined by a massless spring of stiffness k oscillate in relative coordinate r = x2 - x1 with period T = 2*pi*sqrt(mu / k), where mu = m1*m2 / (m1 + m2).",
            "cues": [
                {
                    "id": "c1",
                    "q": "What does the reduced mass mu = m1*m2/(m1 + m2) approach when m2 >> m1?",
                    "a": "mu approaches m1, recovering the fixed-wall single-mass oscillator.",
                }
            ],
        },
        {
            "id": "phys-shm-compound-pendulum",
            "title": "Compound Pendulum & Minimum-Period Pivot",
            "subject": subject,
            "chapter": chapter,
            "kind": "derivation",
            "statement": "A rigid body pivoted a distance d from its center of mass oscillates for small angles with T = 2*pi*sqrt(I / (m g d)) = 2*pi*sqrt((k_g^2 + d^2) / (g d)), minimized when d = k_g.",
            "boundaryConditions": ["Valid for small angular amplitude theta0 << 1 rad; larger amplitudes lengthen T via elliptic correction."],
            "cues": [
                {
                    "id": "c1",
                    "q": "At what pivot distance d from the center of mass is a compound pendulum's small-angle period minimized?",
                    "a": "At d = k_g (radius of gyration about the CM), by AM-GM on (k_g^2 / d) + d >= 2*k_g.",
                }
            ],
        },
        {
            "id": "phys-shm-damped-regimes",
            "title": "Viscous Damping Regimes & Shifted Frequency",
            "subject": subject,
            "chapter": chapter,
            "kind": "theorem",
            "statement": "Adding linear drag -b v yields x'' + 2*gamma*x' + omega0^2 x = 0 (gamma = b/(2m)): underdamped motion (gamma < omega0) decays as exp(-gamma t) at lowered frequency omega_d = sqrt(omega0^2 - gamma^2).",
            "cues": [
                {
                    "id": "c1",
                    "q": "Does light viscous damping increase or decrease an oscillator's natural frequency?",
                    "a": "It decreases the frequency slightly to omega_d = sqrt(omega0^2 - gamma^2).",
                }
            ],
        },
        {
            "id": "phys-shm-driven-resonance",
            "title": "Driven Resonance Amplitude & Phase Lag",
            "subject": subject,
            "chapter": chapter,
            "kind": "theorem",
            "statement": "Under harmonic drive F0 cos(omega t), steady-state amplitude is A(omega) = (F0/m) / sqrt((omega0^2 - omega^2)^2 + (2*gamma*omega)^2), with amplitude peak at sqrt(omega0^2 - 2*gamma^2) and power resonance at omega = omega0 (phase lag pi/2).",
            "cues": [
                {
                    "id": "c1",
                    "q": "At exact undamped natural frequency omega = omega0, what is the phase lag between driving force and steady-state displacement?",
                    "a": "Exactly pi/2 radians (90 degrees), so driving force is perfectly in phase with velocity for maximum power transfer.",
                }
            ],
        },
    ]

    chains = [
        {
            "premises": ["phys-shm-linearization"],
            "conclusion": "phys-shm-ode-solution",
            "relation": "derives",
            "why": "F = -k_eff x divided by inertia yields x'' + omega^2 x = 0.",
            "encompassWeight": 0.55,
        },
        {
            "premises": ["phys-shm-ode-solution"],
            "conclusion": "phys-shm-phasor-circle",
            "relation": "derives",
            "why": "x(t) = A cos(omega t + phi) is the horizontal projection of uniform circular motion.",
            "encompassWeight": 0.55,
        },
        {
            "premises": ["phys-shm-linearization", "phys-shm-ode-solution"],
            "conclusion": "phys-shm-energy-curvature",
            "relation": "derives",
            "why": "Integrating -kx gives quadratic U(x) and squaring sin/cos doubles oscillation frequency to 2*omega.",
            "encompassWeight": 0.45,
        },
        {
            "premises": ["phys-shm-linearization", "phys-shm-energy-curvature"],
            "conclusion": "phys-shm-constant-force",
            "relation": "proves",
            "why": "A linear term -F0 x shifts the parabola vertex without altering curvature U''(x) = k.",
            "encompassWeight": 0.45,
        },
        {
            "premises": ["phys-shm-linearization"],
            "conclusion": "phys-shm-spring-combos",
            "relation": "derives",
            "why": "Linear Hooke's law allows force-balance/displacement-sum superposition.",
            "encompassWeight": 0.50,
        },
        {
            "premises": ["phys-shm-ode-solution", "phys-shm-spring-combos"],
            "conclusion": "phys-shm-reduced-mass",
            "relation": "derives",
            "why": "Subtracting equations of motion yields mu * r'' = -k r.",
            "encompassWeight": 0.42,
        },
        {
            "premises": ["phys-shm-linearization", "phys-shm-energy-curvature"],
            "conclusion": "phys-shm-compound-pendulum",
            "relation": "derives",
            "why": "Linearizing sin(theta) near theta=0 gives angular restoring stiffness mgd against inertia I.",
            "encompassWeight": 0.45,
        },
        {
            "premises": ["phys-shm-ode-solution", "phys-shm-energy-curvature"],
            "conclusion": "phys-shm-damped-regimes",
            "relation": "extends",
            "why": "Linear drag dissipates mechanical energy exponentially and shifts roots off the imaginary axis.",
            "encompassWeight": 0.42,
        },
        {
            "premises": ["phys-shm-phasor-circle", "phys-shm-damped-regimes"],
            "conclusion": "phys-shm-driven-resonance",
            "relation": "derives",
            "why": "Steady-state phasor balance between drive, inertia, stiffness, and drag determines A(omega) and phase lag.",
            "encompassWeight": 0.42,
        },
    ]

    topics = [
        {
            "id": "shm-t1",
            "order": 1,
            "title": "Linearization, SHM ODE & Phasor Geometry",
            "level": "basics",
            "prereqTopicIds": [],
            "propIds": ["phys-shm-linearization", "phys-shm-ode-solution", "phys-shm-phasor-circle"],
        },
        {
            "id": "shm-t2",
            "order": 2,
            "title": "Energy Curvature, Constant Forces & Spring Systems",
            "level": "main",
            "prereqTopicIds": ["shm-t1"],
            "propIds": [
                "phys-shm-energy-curvature",
                "phys-shm-constant-force",
                "phys-shm-spring-combos",
                "phys-shm-reduced-mass",
            ],
        },
        {
            "id": "shm-t3",
            "order": 3,
            "title": "Compound Pendulum, Damping & Driven Resonance",
            "level": "advanced",
            "prereqTopicIds": ["shm-t2"],
            "propIds": ["phys-shm-compound-pendulum", "phys-shm-damped-regimes", "phys-shm-driven-resonance"],
        },
    ]

    mistakes = [
        {
            "propId": "phys-shm-spring-combos",
            "subject": subject,
            "chapter": chapter,
            "category": "misconception",
            "triggerCue": "Two springs k1 and k2 connected end-to-end in series",
            "wrongMove": "Added stiffnesses directly as k1 + k2 like series resistors",
            "correctMove": "Series springs share tension and add extensions, so 1/k_eq = 1/k1 + 1/k2",
        },
        {
            "propId": "phys-shm-energy-curvature",
            "subject": subject,
            "chapter": chapter,
            "category": "condition_missed",
            "triggerCue": "Frequency of kinetic or potential energy in SHM",
            "wrongMove": "Claimed K(t) oscillates at frequency f instead of 2f",
            "correctMove": "Since K(t) = E sin^2(omega t) = (E/2)(1 - cos(2*omega t)), energy oscillates at 2f",
        },
        {
            "propId": "phys-shm-linearization",
            "subject": subject,
            "chapter": chapter,
            "category": "condition_missed",
            "triggerCue": "Large-amplitude pendulum released from theta0 = 60 degrees",
            "wrongMove": "Applied small-angle T = 2*pi*sqrt(L/g) without checking sin(theta) vs theta divergence",
            "correctMove": "Exact restoring torque sin(theta) < theta softens stiffness at large angles, lengthening T by ~(1 + theta0^2 / 16)",
        },
    ]

    derivations = [
        {
            "id": "deriv-shm-compound-pendulum",
            "propId": "phys-shm-compound-pendulum",
            "chapter": chapter,
            "title": "Small-Angle Compound Pendulum Period",
            "domain": subject,
            "steps": [
                {
                    "id": "s1",
                    "label": "Exact Gravitational Torque",
                    "expr": "I * alpha = -m * g * d * sin(theta)",
                    "role": "axiom",
                    "dependsOn": [],
                    "note": "Torque about pivot located distance d from center of mass.",
                },
                {
                    "id": "s2",
                    "label": "Small-Angle Linearization",
                    "expr": "sin(theta) = theta",
                    "role": "axiom",
                    "dependsOn": [],
                    "note": "Valid regime theta << 1 radian (breaks down at large amplitude).",
                },
                {
                    "id": "s3",
                    "label": "Angular SHM ODE",
                    "expr": "alpha = -(m * g * d / I) * theta",
                    "role": "step",
                    "dependsOn": ["s1", "s2"],
                    "note": "Matches canonical SHM form alpha = -omega^2 * theta.",
                },
                {
                    "id": "s4",
                    "label": "Angular Frequency",
                    "expr": "omega = sqrt(m * g * d / I)",
                    "role": "step",
                    "dependsOn": ["s3"],
                    "note": "Extract omega^2 = m g d / I.",
                },
                {
                    "id": "s5",
                    "label": "Compound Pendulum Period",
                    "expr": "T = 2 * pi * sqrt(I / (m * g * d))",
                    "role": "theorem",
                    "dependsOn": ["s4"],
                    "note": "Uses I = I_cm + m*d^2 via parallel-axis theorem.",
                },
            ],
        }
    ]

    chapter_md = """# Simple Harmonic Motion — Linearization, Phasors & Regime Limits

Every harmonic oscillator in physics stems from one structural fact: **smooth potential wells look locally parabolic near a stable equilibrium**.

:::cue id=shm-lin-cue prop=phys-shm-linearization
Q: Why does a constant external force (like gravity on a vertical spring) leave the oscillation period completely unchanged?
A: Adding -F0*x tilts the potential energy curve and shifts the minimum x0 = F0/k, while the second derivative k = U''(x0) governing restoring acceleration stays identical.
:::

## Regime-Break Peeler: Small-Angle vs Exact Pendulum

Drag initial amplitude `theta0` past `0.35 rad` (~20 deg) to watch exact restoring torque `sin(theta)` peel away from the linear approximation `theta`, lengthening the true period.

:::explore shm-pendulum-regime
{
  "id": "shm-pendulum-regime",
  "title": "Small-Angle vs Exact Pendulum Regime-Break Peeler",
  "prompt": "Compare linear SHM torque (theta) against exact pendulum torque (sin(theta)) and inspect the period error as initial amplitude theta0 grows.",
  "params": [
    {"name": "theta0", "label": "Initial angle theta0", "min": 0.05, "max": 2.4, "step": 0.05, "default": 0.3, "unit": "rad"},
    {"name": "L", "label": "Length L", "min": 0.25, "max": 4.0, "step": 0.25, "default": 1.0, "unit": "m"},
    {"name": "g", "label": "Gravity g", "min": 1.6, "max": 20.0, "step": 0.2, "default": 9.8, "unit": "m/s^2"}
  ],
  "outputs": [
    {"label": "Small-angle period T0", "expr": "2 * pi * sqrt(L / g)", "unit": "s"},
    {"label": "Nonlinear period T_exact", "expr": "2 * pi * sqrt(L / g) * (1 + (theta0 ** 2) / 16 + 11 * (theta0 ** 4) / 3072)", "unit": "s"},
    {"label": "Period lengthen error", "expr": "100 * ((theta0 ** 2) / 16 + 11 * (theta0 ** 4) / 3072)", "unit": "%"}
  ],
  "curve": {
    "xVar": "theta",
    "xMin": 0.0,
    "xMax": 2.2,
    "samples": 48,
    "exactExpr": "sin(theta)",
    "approxExpr": "theta",
    "regimeThreshold": 0.35,
    "yLabel": "Restoring factor"
  },
  "limitCheck": {
    "question": "As theta0 -> 0, what value does the period ratio T_exact / T0 approach?",
    "var": "theta0",
    "target": 0,
    "expectedExpr": "1 + (theta0 ** 2) / 16",
    "expectedVal": 1.0,
    "hint": "Every higher-order amplitude term theta0^(2n) vanishes in the small-angle limit."
  }
}
:::

## Springs, Energy & Damped Resonance

1. **Energy oscillates at $2\\omega$**: $K(t)$ and $U(t)$ reach two maxima per full displacement period.
2. **Series vs Parallel stiffness**: series springs share force ($1/k_{\\text{eq}} = 1/k_1 + 1/k_2$); parallel springs share displacement ($k_{\\text{eq}} = k_1 + k_2$).

:::cue id=shm-energy-cue prop=phys-shm-energy-curvature
Q: A mass executes SHM at 3 Hz. At what frequency does its kinetic energy oscillate?
A: 6 Hz (twice the displacement frequency), since K(t) = (1/2) m v^2 peaks on both the +x and -x passes through equilibrium.
:::
"""

    forks = [
        {
            "id": "fork-shm-series-springs",
            "propId": "phys-shm-spring-combos",
            "subject": subject,
            "chapter": chapter,
            "prefixSteps": [
                "Two massless springs of stiffness k1 and k2 are joined end-to-end in series.",
                "Under tension F, total extension is x = x1 + x2 = F/k1 + F/k2.",
            ],
            "forkPrompt": "Write the exact equivalent stiffness k_eq in terms of k1 and k2.",
            "sealedBlunder": "k1 + k2",
            "sealedWinningMove": "k1*k2/(k1 + k2)",
            "refutationMsg": "k1 + k2 is stiffer than either spring alone—but joining springs in series makes the chain softer because both springs stretch under the same tension F.",
            "verifySpec": {"engine": "math"},
            "status": "active",
        },
        {
            "id": "fork-shm-energy-freq",
            "propId": "phys-shm-energy-curvature",
            "subject": subject,
            "chapter": chapter,
            "prefixSteps": [
                "x(t) = A * cos(omega * t)",
                "K(t) = (1/2) * m * A**2 * omega**2 * sin(omega * t)**2",
            ],
            "forkPrompt": "Apply the double-angle identity to state the angular frequency of K(t) in terms of omega.",
            "sealedBlunder": "omega",
            "sealedWinningMove": "2*omega",
            "refutationMsg": "sin^2(omega*t) = (1 - cos(2*omega*t))/2 completes two full oscillations for every single period of x(t).",
            "verifySpec": {"engine": "math"},
            "status": "active",
        },
    ]

    arenas = [
        {
            "id": "arena-shm-series-stiffness",
            "propId": "phys-shm-spring-combos",
            "domain": "math",
            "title": "Falsify Broken Series-Spring Stiffness Formulas",
            "claim": "For two positive springs k1 > 0 and k2 > 0 in series, equivalent stiffness is k_eq(k1, k2) = k1*k2 / (k1 + k2).",
            "sealedReference": {"expr": "k1*k2/(k1 + k2)"},
            "sealedMutants": [
                {"id": "m1", "trapTag": "parallel_sum_confusion", "expr": "k1 + k2"},
                {"id": "m2", "trapTag": "symmetric_halving_shortcut", "expr": "k1 / 2"},
                {"id": "m3", "trapTag": "harmonic_mean_factor2", "expr": "2*k1*k2/(k1 + k2)"},
            ],
            "assumptionPredicates": [
                {"description": "k1 > 0", "expr": "k1 > 0"},
                {"description": "k2 > 0", "expr": "k2 > 0"},
            ],
            "killedMutants": [],
            "lemmaRepairs": [],
        }
    ]

    exam = {
        "schemaVersion": 1,
        "examSlug": f"{slug}-exam",
        "title": "Simple Harmonic Motion — 4-Question CBT Mini-Exam",
        "examType": "jee_main",
        "durationMinutes": 15,
        "instructions": [
            "Standard +4/-1 for single-correct, partial marking for multi-correct, 0 negative on numerical.",
            "Check velocity direction on the phasor circle before finalizing elapsed-time answers.",
        ],
        "questions": [
            {
                "id": "q1",
                "type": "single_correct",
                "subject": subject,
                "chapter": chapter,
                "propId": "phys-shm-phasor-circle",
                "stem": "A particle in SHM of period T moves from x = 0 toward +A, reaches +A, and returns to x = +A/2. What total time elapses?",
                "options": [
                    {"id": "A", "text": "T/3"},
                    {"id": "B", "text": "5T/12"},
                    {"id": "C", "text": "T/2"},
                    {"id": "D", "text": "T/4"},
                ],
                "answer": "B",
                "marksCorrect": 4,
                "marksWrong": -1,
                "solution": "x=0 to +A takes T/4; +A back to +A/2 spans pi/3 radians = T/6. Total = T/4 + T/6 = 5T/12.",
            },
            {
                "id": "q2",
                "type": "multi_correct",
                "subject": subject,
                "chapter": chapter,
                "propId": "phys-shm-constant-force",
                "stem": "A block hangs from a vertical ideal spring of stiffness k inside an elevator accelerating upward at constant a0. Which statements are true?",
                "options": [
                    {"id": "A", "text": "The equilibrium extension increases by m*a0 / k"},
                    {"id": "B", "text": "The time period T = 2*pi*sqrt(m/k) remains unchanged"},
                    {"id": "C", "text": "The effective stiffness doubles"},
                    {"id": "D", "text": "Kinetic energy still oscillates at frequency 2*omega"},
                ],
                "answer": ["A", "B", "D"],
                "marksCorrect": 4,
                "marksWrong": -2,
                "partialMarking": True,
                "solution": "Constant pseudo-force -m*a0 shifts equilibrium without changing k, period T, or the 2*omega energy frequency.",
            },
            {
                "id": "q3",
                "type": "numerical",
                "subject": subject,
                "chapter": chapter,
                "propId": "phys-shm-spring-combos",
                "stem": "Two springs with stiffness k1 = 60 N/m and k2 = 30 N/m are connected in series. Find the equivalent stiffness k_eq in N/m.",
                "answer": 20.0,
                "tolerance": 0.05,
                "marksCorrect": 4,
                "marksWrong": 0,
                "solution": "k_eq = (60 * 30) / (60 + 30) = 1800 / 90 = 20 N/m.",
            },
            {
                "id": "q4",
                "type": "matrix_match",
                "subject": subject,
                "chapter": chapter,
                "propId": "phys-shm-damped-regimes",
                "stem": "Match each oscillator regime/quantity (P–R) to its defining expression (1–3):",
                "rows": [
                    {"id": "P", "text": "Reduced mass of two-body spring"},
                    {"id": "Q", "text": "Underdamped oscillation frequency"},
                    {"id": "R", "text": "Minimum-period compound pendulum pivot"},
                ],
                "cols": [
                    {"id": "1", "text": "m1*m2 / (m1 + m2)"},
                    {"id": "2", "text": "sqrt(omega0^2 - gamma^2)"},
                    {"id": "3", "text": "d = k_g (radius of gyration)"},
                ],
                "answer": {"P": ["1"], "Q": ["2"], "R": ["3"]},
                "marksCorrect": 6,
                "marksPerRow": 2,
                "marksWrong": 0,
                "solution": "P->1, Q->2, R->3.",
            },
        ],
    }

    return {
        "slug": slug,
        "title": "Simple Harmonic Motion — Linearization, Phasors & Resonance",
        "subject": subject,
        "chapter": chapter,
        "propositions": propositions,
        "chains": chains,
        "topics": topics,
        "mistakes": mistakes,
        "derivations": derivations,
        "chapter_md": chapter_md,
        "forks": forks,
        "arenas": arenas,
        "exam": exam,
        "arenaTestInputs": [
            {"input": "k1=6, k2=3", "kills": "all"},
        ],
    }


# ===========================================================================
# PACK 3: TRANSFORMERS & ATTENTION (Machine Learning)
# ===========================================================================


def _spec_transformers_attention() -> dict[str, Any]:
    slug = "transformers-attention"
    chapter = "Transformers & Attention"
    subject = "Machine Learning"

    propositions = [
        {
            "id": "ml-attn-dot-variance",
            "title": "Dot-Product Variance Growth with Dimension d_k",
            "subject": subject,
            "chapter": chapter,
            "kind": "derivation",
            "statement": "If query and key entries q_i, k_i are independent with mean 0 and variance 1, then E[q·k] = 0 and Var(q·k) = sum_{i=1}^{d_k} Var(q_i)Var(k_i) = d_k.",
            "cues": [
                {
                    "id": "c1",
                    "q": "When q_i, k_i are i.i.d. with mean 0 and variance 1, how does the standard deviation of q·k scale with head dimension d_k?",
                    "a": "Standard deviation scales as sqrt(d_k), because variance scales linearly as d_k.",
                }
            ],
        },
        {
            "id": "ml-attn-softmax-saturation",
            "title": "Softmax Saturation & Vanishing Jacobian Gradients",
            "subject": subject,
            "chapter": chapter,
            "kind": "theorem",
            "statement": "The softmax Jacobian diag(p) - p p^T shrinks toward zero whenever large logit spreads push one probability p_max -> 1 and all others -> 0.",
            "cues": [
                {
                    "id": "c1",
                    "q": "What happens to d(softmax)/d(logits) as logit temperature tau -> 0?",
                    "a": "Softmax collapses to a one-hot argmax vector where p_i(1 - p_i) -> 0 everywhere, starving upstream Q/K projections of gradient.",
                }
            ],
        },
        {
            "id": "ml-attn-scaled-dot",
            "title": "Scaled Dot-Product Attention (1/sqrt(d_k))",
            "subject": subject,
            "chapter": chapter,
            "kind": "theorem",
            "statement": "Attention(Q, K, V) = softmax(Q K^T / sqrt(d_k)) V restores unit pre-softmax variance Var(q·k / sqrt(d_k)) = 1 independent of head dimension d_k.",
            "boundaryConditions": ["Dividing by d_k instead of sqrt(d_k) shrinks variance to 1/d_k, collapsing weights toward uniform averaging."],
            "cues": [
                {
                    "id": "c1",
                    "q": "Why do we divide Q K^T by sqrt(d_k) rather than d_k?",
                    "a": "Because Var(c*X) = c^2 Var(X); dividing by sqrt(d_k) scales variance by 1/d_k to 1, whereas dividing by d_k shrinks variance to 1/d_k.",
                }
            ],
        },
        {
            "id": "ml-attn-causal-mask",
            "title": "Pre-Softmax Causal Masking (-inf Upper Triangle)",
            "subject": subject,
            "chapter": chapter,
            "kind": "invariant",
            "statement": "Autoregressive decoding enforces causality by setting future logit positions j > i to -infinity BEFORE softmax so exp(-inf) = 0 exactly and remaining weights renormalize to 1.",
            "boundaryConditions": ["Setting future logits to 0 leaks future tokens because exp(0) = 1."],
            "cues": [
                {
                    "id": "c1",
                    "q": "What bug occurs if you set masked future attention logits to 0.0 instead of -inf before softmax?",
                    "a": "exp(0) = 1 assigns positive attention weight to future tokens, leaking ground-truth targets during training.",
                }
            ],
        },
        {
            "id": "ml-attn-multihead-subspace",
            "title": "Multi-Head Representation Subspaces",
            "subject": subject,
            "chapter": chapter,
            "kind": "definition",
            "statement": "Splitting d_model into H heads of dimension d_k = d_model / H lets each head compute an independent similarity metric and attend to distinct positions simultaneously at equal projection FLOPs.",
            "cues": [
                {
                    "id": "c1",
                    "q": "Why doesn't splitting 1 head of width 512 into 8 heads of width 64 multiply W_Q parameter count by 8?",
                    "a": "Because H * (d_model * d_k) = d_model * (H * d_k) = d_model^2, keeping total projection parameters constant.",
                }
            ],
        },
        {
            "id": "ml-attn-permutation-equiv",
            "title": "Permutation Equivariance & Positional Injection",
            "subject": subject,
            "chapter": chapter,
            "kind": "theorem",
            "statement": "Unmasked self-attention is strictly permutation-equivariant: permuting input rows produces the exact same permutation of outputs unless positional information is injected.",
            "cues": [
                {
                    "id": "c1",
                    "q": "Without positional encodings or causal masks, can self-attention distinguish 'dog bites man' from 'man bites dog'?",
                    "a": "No; dot products depend only on token embeddings as an unordered set, making outputs permutation-equivariant.",
                }
            ],
        },
        {
            "id": "ml-attn-rope",
            "title": "Rotary Positional Embeddings (RoPE)",
            "subject": subject,
            "chapter": chapter,
            "kind": "derivation",
            "statement": "RoPE rotates each 2-D pair of Q and K coordinates by angle m*theta_j; because rotation matrices satisfy R(m)^T R(n) = R(n - m), the attention dot product depends purely on relative offset (n - m).",
            "cues": [
                {
                    "id": "c1",
                    "q": "Why does rotating Q_m by angle m*theta and K_n by angle n*theta make Q_m · K_n depend only on relative distance?",
                    "a": "Because orthogonal 2-D rotation matrices obey R(m)^T R(n) = R(n - m), cancelling absolute position shifts.",
                }
            ],
        },
        {
            "id": "ml-attn-quad-complexity",
            "title": "Quadratic O(T^2 d) Attention Bottleneck",
            "subject": subject,
            "chapter": chapter,
            "kind": "invariant",
            "statement": "Materializing the T x T attention score matrix across sequence length T incurs O(T^2 d) compute and O(T^2) HBM memory per head during prefill unless tiled via FlashAttention.",
            "cues": [
                {
                    "id": "c1",
                    "q": "How does FlashAttention reduce attention memory from O(T^2) to O(T) without changing exact mathematical output?",
                    "a": "It tiles Q, K, V in fast SRAM and maintains online running row-max and softmax normalizer statistics, never materializing the full T x T matrix in HBM.",
                }
            ],
        },
        {
            "id": "ml-attn-kv-cache",
            "title": "Autoregressive KV-Cache Complexity Reduction",
            "subject": subject,
            "chapter": chapter,
            "kind": "application",
            "statement": "Because causal masking prevents earlier keys and values from changing when new tokens arrive, caching past K and V drops per-token decode compute from O(T^2 d) to O(T d) at O(L * T * d_kv) memory cost.",
            "cues": [
                {
                    "id": "c1",
                    "q": "Why can we cache past K and V vectors across autoregressive decoding steps without recomputing them?",
                    "a": "Causal masking ensures hidden states at step i <= t depend strictly on tokens <= i, so past K_i and V_i remain invariant when token t+1 is appended.",
                }
            ],
        },
        {
            "id": "ml-attn-gqa-mqa",
            "title": "Grouped-Query Attention (GQA) Memory-Bandwidth Tradeoff",
            "subject": subject,
            "chapter": chapter,
            "kind": "application",
            "statement": "Sharing each Key/Value head across G query heads shrinks KV-cache size and decode HBM traffic by factor G while preserving most of Multi-Head Attention's representational capacity.",
            "cues": [
                {
                    "id": "c1",
                    "q": "If a model has 32 query heads and 8 KV heads (GQA-4), by what factor does KV-cache memory shrink relative to standard MHA?",
                    "a": "By a factor of 4x (32 / 8 = 4), cutting autoregressive memory bandwidth linearly.",
                }
            ],
        },
    ]

    chains = [
        {
            "premises": ["ml-attn-dot-variance", "ml-attn-softmax-saturation"],
            "conclusion": "ml-attn-scaled-dot",
            "relation": "derives",
            "why": "Growing dot-product variance saturates softmax unless normalized by 1/sqrt(d_k).",
            "encompassWeight": 0.45,
        },
        {
            "premises": ["ml-attn-scaled-dot"],
            "conclusion": "ml-attn-causal-mask",
            "relation": "extends",
            "why": "Adding -inf pre-softmax mask restricts scaled attention to causal prefixes.",
            "encompassWeight": 0.50,
        },
        {
            "premises": ["ml-attn-scaled-dot"],
            "conclusion": "ml-attn-multihead-subspace",
            "relation": "extends",
            "why": "Parallel projected subspaces generalize single-head scaled attention.",
            "encompassWeight": 0.50,
        },
        {
            "premises": ["ml-attn-scaled-dot"],
            "conclusion": "ml-attn-permutation-equiv",
            "relation": "implies",
            "why": "Pairwise dot products over unordered rows are invariant to index order.",
            "encompassWeight": 0.50,
        },
        {
            "premises": ["ml-attn-permutation-equiv", "ml-attn-multihead-subspace"],
            "conclusion": "ml-attn-rope",
            "relation": "requires",
            "why": "RoPE breaks permutation equivariance inside each head via relative 2-D rotations.",
            "encompassWeight": 0.42,
        },
        {
            "premises": ["ml-attn-scaled-dot", "ml-attn-causal-mask"],
            "conclusion": "ml-attn-quad-complexity",
            "relation": "implies",
            "why": "Computing all pairwise token scores scales quadratically with T.",
            "encompassWeight": 0.45,
        },
        {
            "premises": ["ml-attn-causal-mask", "ml-attn-quad-complexity"],
            "conclusion": "ml-attn-kv-cache",
            "relation": "derives",
            "why": "Causal invariance of past K/V turns O(T^2) per-step decode into O(T) lookup.",
            "encompassWeight": 0.45,
        },
        {
            "premises": ["ml-attn-multihead-subspace", "ml-attn-kv-cache"],
            "conclusion": "ml-attn-gqa-mqa",
            "relation": "extends",
            "why": "Sharing KV heads across query groups shrinks the KV-cache bottleneck.",
            "encompassWeight": 0.45,
        },
    ]

    topics = [
        {
            "id": "attn-t1",
            "order": 1,
            "title": "Dot-Product Variance, Softmax Saturation & Scaling",
            "level": "basics",
            "prereqTopicIds": [],
            "propIds": ["ml-attn-dot-variance", "ml-attn-softmax-saturation", "ml-attn-scaled-dot"],
        },
        {
            "id": "attn-t2",
            "order": 2,
            "title": "Causal Masking, Multi-Head Subspaces & RoPE",
            "level": "main",
            "prereqTopicIds": ["attn-t1"],
            "propIds": [
                "ml-attn-causal-mask",
                "ml-attn-multihead-subspace",
                "ml-attn-permutation-equiv",
                "ml-attn-rope",
            ],
        },
        {
            "id": "attn-t3",
            "order": 3,
            "title": "Quadratic Complexity, KV-Cache & GQA Systems Tradeoffs",
            "level": "advanced",
            "prereqTopicIds": ["attn-t2"],
            "propIds": ["ml-attn-quad-complexity", "ml-attn-kv-cache", "ml-attn-gqa-mqa"],
        },
    ]

    mistakes = [
        {
            "propId": "ml-attn-scaled-dot",
            "subject": subject,
            "chapter": chapter,
            "category": "algebra_slip",
            "triggerCue": "Normalizing dot-product attention logits by head dimension d_k",
            "wrongMove": "Divided Q @ K.T by d_k instead of sqrt(d_k)",
            "correctMove": "Divide by sqrt(d_k) because Var(X / c) = Var(X) / c^2",
        },
        {
            "propId": "ml-attn-causal-mask",
            "subject": subject,
            "chapter": chapter,
            "category": "edge_case_missed",
            "triggerCue": "Masking future tokens j > i before applying softmax",
            "wrongMove": "Filled upper-triangular future scores with 0.0 instead of -inf",
            "correctMove": "Fill j > i logits with -inf before softmax so exp(-inf) = 0 exactly",
        },
        {
            "propId": "ml-attn-kv-cache",
            "subject": subject,
            "chapter": chapter,
            "category": "complexity_trap",
            "triggerCue": "Analyzing per-token autoregressive decoding cost with KV-cache",
            "wrongMove": "Assumed KV-cache makes total memory O(1) with respect to context length T",
            "correctMove": "KV-cache cuts per-step compute to O(T*d) while growing memory linearly as O(L*T*d_kv)",
        },
    ]

    derivations = [
        {
            "id": "deriv-attn-variance-scaling",
            "propId": "ml-attn-scaled-dot",
            "chapter": chapter,
            "title": "Deriving the 1/sqrt(d_k) Attention Scaling Factor",
            "domain": subject,
            "steps": [
                {
                    "id": "s1",
                    "label": "Unit-Variance Coordinate Prior",
                    "expr": "E[q_i] = 0, Var(q_i) = 1, E[k_i] = 0, Var(k_i) = 1",
                    "role": "axiom",
                    "dependsOn": [],
                    "note": "Independent zero-mean unit-variance query/key entries.",
                },
                {
                    "id": "s2",
                    "label": "Product Variance",
                    "expr": "Var(q_i * k_i) = Var(q_i) * Var(k_i) = 1",
                    "role": "step",
                    "dependsOn": ["s1"],
                    "note": "Zero-mean independence implies Var(XY) = E[X^2]E[Y^2] = 1.",
                },
                {
                    "id": "s3",
                    "label": "Unscaled Dot-Product Variance",
                    "expr": "Var(q . k) = d_k",
                    "role": "step",
                    "dependsOn": ["s2"],
                    "note": "Sum of d_k independent unit-variance products has variance d_k.",
                },
                {
                    "id": "s4",
                    "label": "Variance Scaling Law",
                    "expr": "Var((q . k) / c) = d_k / c**2",
                    "role": "step",
                    "dependsOn": ["s3"],
                    "note": "Dividing a random variable by c divides its variance by c^2.",
                },
                {
                    "id": "s5",
                    "label": "Unit-Variance Normalization",
                    "expr": "c = sqrt(d_k)",
                    "role": "theorem",
                    "dependsOn": ["s4"],
                    "note": "Setting d_k / c^2 = 1 forces c = sqrt(d_k) to prevent softmax saturation.",
                },
            ],
        }
    ]

    chapter_md = """# Transformers & Attention — Variance Scaling, Causality & KV-Cache

Scaled dot-product attention is simple algebra paired with one crucial numerical invariant: **keep pre-softmax logits at $O(1)$ variance so gradients survive**.

:::cue id=attn-var-cue prop=ml-attn-scaled-dot
Q: If head dimension grows from d_k = 64 to d_k = 4096 without 1/sqrt(d_k) scaling, what happens to softmax entropy and gradients?
A: Logit standard deviation jumps 8x (from 8 to 64), collapsing softmax into near-one-hot saturation where the Jacobian p_i(1 - p_i) vanishes.
:::

## Softmax Temperature & Variance-Scaling Lab

Sweep `d_k` and temperature `temp` to watch an unscaled two-token logit gap collapse into hard saturation while the `1/sqrt(d_k)`-scaled softmax keeps healthy gradient flow.

:::explore attn-softmax-scaling
{
  "id": "attn-softmax-scaling",
  "title": "Softmax Temperature & Variance-Scaling Saturation Lab",
  "prompt": "Compare scaled vs unscaled top attention probability and gradient strength p*(1-p) as head dimension d_k grows.",
  "params": [
    {"name": "d_k", "label": "Head dim d_k", "min": 16, "max": 1024, "step": 16, "default": 64, "unit": "dims"},
    {"name": "temp", "label": "Temperature T", "min": 0.2, "max": 4.0, "step": 0.1, "default": 1.0, "unit": ""},
    {"name": "delta", "label": "Unit logit gap delta", "min": 0.2, "max": 3.0, "step": 0.1, "default": 1.0, "unit": "sigma"}
  ],
  "outputs": [
    {"label": "Unscaled logit std", "expr": "sqrt(d_k)", "unit": ""},
    {"label": "Scaled top weight p", "expr": "1 / (1 + exp(-delta / temp))", "unit": ""},
    {"label": "Unscaled top weight", "expr": "1 / (1 + exp(-min(40, delta * sqrt(d_k) / temp)))", "unit": ""},
    {"label": "Scaled gradient p(1-p)", "expr": "(1 / (1 + exp(-delta / temp))) * (1 - 1 / (1 + exp(-delta / temp)))", "unit": ""}
  ],
  "curve": {
    "xVar": "x",
    "xMin": 0.0,
    "xMax": 6.0,
    "samples": 48,
    "exactExpr": "1 / (1 + exp(-x / temp))",
    "approxExpr": "min(1, 0.5 + x / (4 * temp))",
    "regimeThreshold": 2.0,
    "yLabel": "Top softmax weight"
  },
  "limitCheck": {
    "question": "As logit gap delta -> 0 at temp = 1, what attention weight does the scaled two-token softmax assign?",
    "var": "delta",
    "target": 0,
    "expectedExpr": "1 / (1 + exp(-delta / temp))",
    "expectedVal": 0.5,
    "hint": "Evaluate 1 / (1 + exp(0))."
  }
}
:::

## Causality, RoPE & KV-Cache Systems Cost

1. **Pre-softmax `-inf` mask**: zeroing future logits *before* softmax fails because $e^0 = 1$.
2. **KV-Cache + GQA**: causal invariance lets inference cache past $K, V$ ($O(T d)$ per decode step), and GQA cuts KV HBM bandwidth by $H_Q / H_{KV}$.

:::cue id=attn-causal-cue prop=ml-attn-causal-mask
Q: Why must causal masking happen BEFORE softmax using -inf rather than zeroing future weights after softmax?
A: Setting j > i logits to -inf makes exp(-inf) = 0 so the remaining valid positions i >= j automatically normalize to sum to 1 without leaking future probability mass.
:::
"""

    forks = [
        {
            "id": "fork-attn-scale-denom",
            "propId": "ml-attn-scaled-dot",
            "subject": subject,
            "chapter": chapter,
            "prefixSteps": [
                "q_i, k_i are independent with mean 0 and variance 1 for i = 1..d_k.",
                "Therefore Var(q · k) = d_k.",
            ],
            "forkPrompt": "Write the exact scalar denominator c so that Var((q · k) / c) = 1.",
            "sealedBlunder": "d_k",
            "sealedWinningMove": "sqrt(d_k)",
            "refutationMsg": "Dividing by d_k scales variance by 1/d_k^2, giving Var = 1/d_k -> 0 and flattening attention into uniform averages.",
            "verifySpec": {"engine": "math"},
            "status": "active",
        },
        {
            "id": "fork-attn-causal-fill",
            "propId": "ml-attn-causal-mask",
            "subject": subject,
            "chapter": chapter,
            "prefixSteps": [
                "scores = (Q @ K.T) / math.sqrt(d_k)",
                "# Mask future positions j > i BEFORE applying softmax",
            ],
            "forkPrompt": "What scalar value must fill future positions (j > i) in `scores` before softmax?",
            "sealedBlunder": "0",
            "sealedWinningMove": "-inf",
            "refutationMsg": "Filling future scores with 0 produces exp(0) = 1, giving future tokens positive attention weight and leaking targets.",
            "verifySpec": {"engine": "exact"},
            "status": "active",
        },
    ]

    arenas = [
        {
            "id": "arena-attn-causal-softmax",
            "propId": "ml-attn-causal-mask",
            "domain": "code",
            "title": "Falsify Broken Causal Attention Masking Implementations",
            "claim": "causal_attention_weights(scores) returns an N x N matrix of row-wise causally masked softmax weights (rounded to 6 decimals) where positions j > i have weight 0.0 and each row sums to 1.0.",
            "entry": "causal_attention_weights",
            "lang": "python",
            "sealedReference": {
                "entry": "causal_attention_weights",
                "code": (
                    "import math\n"
                    "def causal_attention_weights(scores):\n"
                    "    n = len(scores)\n"
                    "    out = []\n"
                    "    for i in range(n):\n"
                    "        row = scores[i][:i + 1]\n"
                    "        m = max(row)\n"
                    "        exps = [math.exp(x - m) for x in row]\n"
                    "        s = sum(exps)\n"
                    "        weights = [round(e / s, 6) for e in exps] + [0.0] * (n - i - 1)\n"
                    "        out.append(weights)\n"
                    "    return out\n"
                ),
            },
            "sealedMutants": [
                {
                    "id": "m1",
                    "trapTag": "zero_mask_before_softmax",
                    "code": (
                        "import math\n"
                        "def causal_attention_weights(scores):\n"
                        "    n = len(scores)\n"
                        "    out = []\n"
                        "    for i in range(n):\n"
                        "        masked = [scores[i][j] if j <= i else 0.0 for j in range(n)]\n"
                        "        m = max(masked)\n"
                        "        exps = [math.exp(x - m) for x in masked]\n"
                        "        s = sum(exps)\n"
                        "        out.append([round(e / s, 6) for e in exps])\n"
                        "    return out\n"
                    ),
                },
                {
                    "id": "m2",
                    "trapTag": "post_softmax_zero_no_renorm",
                    "code": (
                        "import math\n"
                        "def causal_attention_weights(scores):\n"
                        "    n = len(scores)\n"
                        "    out = []\n"
                        "    for i in range(n):\n"
                        "        m = max(scores[i])\n"
                        "        exps = [math.exp(x - m) for x in scores[i]]\n"
                        "        s = sum(exps)\n"
                        "        out.append([round(exps[j] / s, 6) if j <= i else 0.0 for j in range(n)])\n"
                        "    return out\n"
                    ),
                },
                {
                    "id": "m3",
                    "trapTag": "strict_lt_blocks_self_diagonal",
                    "code": (
                        "import math\n"
                        "def causal_attention_weights(scores):\n"
                        "    n = len(scores)\n"
                        "    out = []\n"
                        "    for i in range(n):\n"
                        "        row = scores[i][:i]\n"
                        "        if not row:\n"
                        "            out.append([0.0] * n)\n"
                        "            continue\n"
                        "        m = max(row)\n"
                        "        exps = [math.exp(x - m) for x in row]\n"
                        "        s = sum(exps)\n"
                        "        out.append([round(e / s, 6) for e in exps] + [0.0] * (n - i))\n"
                        "    return out\n"
                    ),
                },
            ],
            "assumptionPredicates": [
                {
                    "description": "scores is a non-empty square N x N numeric matrix",
                    "code": (
                        "def check(raw):\n"
                        "    m = raw['args'][0] if isinstance(raw, dict) and isinstance(raw.get('args'), list) and raw['args'] else raw\n"
                        "    return isinstance(m, list) and len(m) >= 1 and all(isinstance(r, list) and len(r) == len(m) for r in m)\n"
                    ),
                },
            ],
            "killedMutants": [],
            "lemmaRepairs": [],
        }
    ]

    exam = {
        "schemaVersion": 1,
        "examSlug": f"{slug}-exam",
        "title": "Transformers & Attention — 4-Question Mastery Check",
        "examType": "mastery",
        "durationMinutes": 12,
        "instructions": [
            "Compute variances and head-dimension tradeoffs from first principles.",
            "Double-check pre-softmax vs post-softmax masking semantics.",
        ],
        "questions": [
            {
                "id": "q1",
                "type": "single_correct",
                "subject": subject,
                "chapter": chapter,
                "propId": "ml-attn-scaled-dot",
                "stem": "If q_i, k_i are independent with mean 0 and variance 1 and d_k = 256, what is Var((q · k) / 256)?",
                "options": [
                    {"id": "A", "text": "1"},
                    {"id": "B", "text": "1/16"},
                    {"id": "C", "text": "1/256"},
                    {"id": "D", "text": "16"},
                ],
                "answer": "C",
                "marksCorrect": 4,
                "marksWrong": -1,
                "solution": "Var(q·k) = 256, so dividing by 256 scales variance by 1/256^2, giving 256 / 256^2 = 1/256.",
            },
            {
                "id": "q2",
                "type": "multi_correct",
                "subject": subject,
                "chapter": chapter,
                "propId": "ml-attn-rope",
                "stem": "Select ALL true statements about Rotary Positional Embeddings (RoPE) and KV-Cache:",
                "options": [
                    {"id": "A", "text": "RoPE inner products depend on relative token offset (n - m)"},
                    {"id": "B", "text": "Causal masking allows past K and V tensors to be cached without recomputation"},
                    {"id": "C", "text": "Setting masked future logits to 0.0 prevents future token leakage"},
                    {"id": "D", "text": "Grouped-Query Attention reduces KV-cache memory by factor H_Q / H_KV"},
                ],
                "answer": ["A", "B", "D"],
                "marksCorrect": 4,
                "marksWrong": -2,
                "partialMarking": True,
                "solution": "A, B, and D are exact; C is false because exp(0) = 1 leaks future probability mass.",
            },
            {
                "id": "q3",
                "type": "numerical",
                "subject": subject,
                "chapter": chapter,
                "propId": "ml-attn-gqa-mqa",
                "stem": "A decoder has 32 query heads and 4 KV heads (GQA). By what integer factor is its KV-cache smaller than standard 32-head MHA?",
                "answer": 8,
                "tolerance": 0.0,
                "marksCorrect": 4,
                "marksWrong": 0,
                "solution": "Reduction factor = H_Q / H_KV = 32 / 4 = 8x.",
            },
            {
                "id": "q4",
                "type": "matrix_match",
                "subject": subject,
                "chapter": chapter,
                "propId": "ml-attn-kv-cache",
                "stem": "Match each Transformer mechanism (P–R) to the primary problem it solves (1–3):",
                "rows": [
                    {"id": "P", "text": "1 / sqrt(d_k) scaling"},
                    {"id": "Q", "text": "Pre-softmax -inf mask"},
                    {"id": "R", "text": "Autoregressive KV-cache"},
                ],
                "cols": [
                    {"id": "1", "text": "Prevents softmax saturation and vanishing gradients"},
                    {"id": "2", "text": "Prevents future-token leakage while preserving row sum 1"},
                    {"id": "3", "text": "Cuts per-token decode compute from O(T^2 d) to O(T d)"},
                ],
                "answer": {"P": ["1"], "Q": ["2"], "R": ["3"]},
                "marksCorrect": 6,
                "marksPerRow": 2,
                "marksWrong": 0,
                "solution": "P->1, Q->2, R->3.",
            },
        ],
    }

    return {
        "slug": slug,
        "title": "Transformers & Attention — Scaling, Causality & KV-Cache",
        "subject": subject,
        "chapter": chapter,
        "propositions": propositions,
        "chains": chains,
        "topics": topics,
        "mistakes": mistakes,
        "derivations": derivations,
        "chapter_md": chapter_md,
        "forks": forks,
        "arenas": arenas,
        "exam": exam,
        "arenaTestInputs": [
            {"args": [[[1.0, 2.0], [1.0, 2.0]]], "kills": "all"},
        ],
    }


PACK_SPECS = (
    _spec_raft_consensus,
    _spec_shm_physics,
    _spec_transformers_attention,
)


# ===========================================================================
# Deterministic Builder & End-to-End Verifier
# ===========================================================================


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(json.dumps(r, ensure_ascii=False) for r in rows)
    path.write_text((body + "\n") if body else "", encoding="utf-8")


def _seal_deterministic_zip(
    base_zip: Path,
    *,
    slug: str,
    chapter_md: str,
    forks: list[dict[str, Any]],
    arenas: list[dict[str, Any]],
) -> None:
    """Attach zero-PII companion curriculum files (`chapters/<slug>.md`, `forks.jsonl`,
    `arenas.jsonl`) to an `export_pack()` archive, updating `manifest["sha256"]` and
    `SHA256SUMS.json` deterministically so `syntopik.packs.verify_pack()` passes 100%.
    """
    with zipfile.ZipFile(base_zip, "r") as zf:
        data_bytes = zf.read(DATA_NAME)
        manifest = json.loads(zf.read(MANIFEST_NAME).decode("utf-8"))

    # Fix createdAt for byte-for-byte deterministic builds
    manifest["createdAt"] = FIXED_CREATED_AT

    clean_forks = [_deep_strip(f) for f in forks]
    clean_arenas = [_deep_strip(a) for a in arenas]

    chapter_rel = f"chapters/{slug}.md"
    extra_members: list[tuple[str, bytes]] = [
        (chapter_rel, chapter_md.strip().encode("utf-8") + b"\n"),
        ("forks.jsonl", ("\n".join(json.dumps(r, ensure_ascii=False) for r in clean_forks) + "\n").encode("utf-8")),
        ("arenas.jsonl", ("\n".join(json.dumps(r, ensure_ascii=False) for r in clean_arenas) + "\n").encode("utf-8")),
    ]

    # Record companion files in manifest['sha256'] & SHA256SUMS.json
    manifest_sha_map: dict[str, str] = {DATA_NAME: _sha256_bytes(data_bytes)}
    for rel, payload in extra_members:
        manifest_sha_map[rel] = _sha256_bytes(payload)
    manifest["sha256"] = manifest_sha_map
    manifest["companionFiles"] = [rel for rel, _ in extra_members]

    manifest_bytes = (json.dumps(manifest, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    sums_map = {
        MANIFEST_NAME: _sha256_bytes(manifest_bytes),
        **manifest_sha_map,
    }
    checksums_obj = {
        "packSchema": manifest.get("packSchema", 1),
        "sha256": dict(sorted(sums_map.items())),
    }
    checksums_bytes = (json.dumps(checksums_obj, indent=2, ensure_ascii=False) + "\n").encode("utf-8")

    members: list[tuple[str, bytes]] = [
        (DATA_NAME, data_bytes),
        (MANIFEST_NAME, manifest_bytes),
        *extra_members,
        (CHECKSUMS_NAME, checksums_bytes),
    ]

    fd, tmp_name = tempfile.mkstemp(dir=str(base_zip.parent), prefix=f".{base_zip.name}.", suffix=".tmp")
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        with zipfile.ZipFile(tmp_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
            for name, payload in sorted(members, key=lambda kv: kv[0]):
                zi = zipfile.ZipInfo(name, date_time=FIXED_ZIP_TIME)
                zi.compress_type = zipfile.ZIP_DEFLATED
                zi.external_attr = 0o100644 << 16
                zf.writestr(zi, payload)
        os.chmod(tmp_path, 0o644)
        os.replace(tmp_path, base_zip)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


def build_pack(spec: dict[str, Any], out_dir: Path) -> dict[str, Any]:
    """Build one starter pack in an isolated temp workspace using `syntopik.packs.export_pack()`."""
    slug = spec["slug"]
    out_path = (out_dir / f"{slug}.syntopik-pack.zip").resolve()

    # Validate :::explore blocks upfront via syntopik.explorable
    explorables = parse_explorable_blocks(spec["chapter_md"], strict=True)
    if not explorables:
        raise ValueError(f"Pack {slug}: chapter Markdown must contain at least one valid :::explore block.")

    with tempfile.TemporaryDirectory(prefix=f"synt-pack-build-{slug}-") as tmp:
        ws = Path(tmp) / "ws"
        kg = ws / "knowledge-graph"
        ch = ws / "chapters"
        ex = ws / "exams"
        kg.mkdir(parents=True)
        ch.mkdir(parents=True)
        ex.mkdir(parents=True)

        _write_jsonl(kg / "propositions.jsonl", spec["propositions"])
        _write_jsonl(kg / "chains.jsonl", spec["chains"])
        _write_jsonl(kg / "mistakes.jsonl", spec["mistakes"])
        _write_jsonl(kg / "derivations.jsonl", spec["derivations"])

        tracker = {
            "schemaVersion": 1,
            "slug": slug,
            "title": spec["title"],
            "subject": spec["subject"],
            "active": True,
            "topics": spec["topics"],
        }
        (ch / f"{slug}-tracker.json").write_text(json.dumps(tracker, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        (ex / f"{slug}-exam.json").write_text(json.dumps(spec["exam"], indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

        exp = export_pack(
            slug,
            workspace=ws,
            kg_dir=kg,
            chapters_dir=ch,
            out_path=out_path,
            author="Syntopik Curated Textbook Packs",
            license_id="CC-BY-4.0",
            title=spec["title"],
            include_exams=True,
        )

    _seal_deterministic_zip(
        out_path,
        slug=slug,
        chapter_md=spec["chapter_md"],
        forks=spec["forks"],
        arenas=spec["arenas"],
    )
    return {
        "slug": slug,
        "path": str(out_path),
        "bytes": out_path.stat().st_size,
        "packSha256": _sha256_bytes(out_path.read_bytes()),
        "contentSha256": exp["contentSha256"],
        "explorables": len(explorables),
    }


def verify_starter_pack(pack_path: Path, spec: dict[str, Any] | None = None) -> dict[str, Any]:
    """Deep-verify a built starter pack: `verify_pack()`, `inspect_pack()`, size `< 40 KB`,
    0 PII keys across ALL members, valid `:::explore`, valid exam, valid derivation DAG,
    live mutant-kill test on arena, and clean round-trip `import_pack()` into temp workspace!
    """
    errors: list[str] = []
    if not pack_path.is_file():
        return {"ok": False, "path": str(pack_path), "errors": [f"Missing pack: {pack_path}"]}

    size_b = pack_path.stat().st_size
    if size_b > MAX_STARTER_PACK_BYTES:
        errors.append(f"Pack size {size_b} B exceeds {MAX_STARTER_PACK_BYTES} B limit.")

    ver = verify_pack(pack_path)
    if not ver.get("ok"):
        errors.extend(ver.get("errors") or ["verify_pack failed"])

    ins = inspect_pack(pack_path)
    if not ins.get("ok"):
        errors.extend(ins.get("errors") or ["inspect_pack failed"])
    elif not ins.get("zeroPiiVerified"):
        errors.append(f"PII leak detected by inspect_pack: {ins.get('piiLeaks')}")

    # Deep scan EVERY member inside the zip for PII keys, personal filenames, & companion validity
    pii_leaks: list[str] = []
    companion_counts: dict[str, int] = {"explorables": 0, "cues": 0, "forks": 0, "arenas": 0}
    excluded_set = set(EXCLUDED_PERSONAL_FILES)

    with zipfile.ZipFile(pack_path, "r") as zf:
        names = zf.namelist()
        for nm in names:
            if Path(nm).name.lower() in excluded_set:
                errors.append(f"Forbidden personal file present: {nm}")
            raw_txt = zf.read(nm).decode("utf-8")
            if nm.endswith(".json"):
                obj = json.loads(raw_txt)
                # Manifest itself intentionally lists strippedFields metadata names (list of strings), not dict keys
                if nm == DATA_NAME:
                    pii_leaks.extend(_scan_pii(obj, f"{nm}:"))
                elif _ABS_PATH_RE.search(json.dumps(obj, ensure_ascii=False).replace("\\n", " ")):
                    pii_leaks.append(f"{nm}:<abs-path>")
            elif nm.endswith(".jsonl"):
                rows = [json.loads(ln) for ln in raw_txt.splitlines() if ln.strip()]
                for idx, row in enumerate(rows):
                    pii_leaks.extend(_scan_pii(row, f"{nm}[{idx}]:"))
                if nm == "forks.jsonl":
                    companion_counts["forks"] = len(rows)
                elif nm == "arenas.jsonl":
                    companion_counts["arenas"] = len(rows)
            elif nm.endswith(".md"):
                if _ABS_PATH_RE.search(raw_txt):
                    pii_leaks.append(f"{nm}:<abs-path>")
                exps = parse_explorable_blocks(raw_txt, strict=True)
                companion_counts["explorables"] = len(exps)
                companion_counts["cues"] = len(re.findall(r"^:::cue\b", raw_txt, flags=re.MULTILINE))

    if pii_leaks:
        errors.append(f"PII leaks found: {pii_leaks}")

    # Round-trip import_pack + derivation_forge + exam_runner + falsify_arena live checks in /tmp
    with tempfile.TemporaryDirectory(prefix="synt-pack-verify-") as tmp:
        ws = Path(tmp) / "ws"
        kg = ws / "knowledge-graph"
        ch = ws / "chapters"
        kg.mkdir(parents=True)
        ch.mkdir(parents=True)
        imp = import_pack(pack_path, workspace=ws, kg_dir=kg, chapters_dir=ch, no_sync=True)
        if not imp.get("ok"):
            errors.extend(imp.get("errors") or ["import_pack failed"])
        elif imp.get("addedPropositions", 0) < 10:
            errors.append(f"Expected >= 10 imported propositions, got {imp.get('addedPropositions')}")

        # Validate derivations via scripts/derivation_forge.py
        import scripts.derivation_forge as df_mod  # noqa: E402

        derivs = df_mod.load_derivations(kg_dir=kg)
        if not derivs:
            errors.append("No derivation DAG imported into derivations.jsonl")
        else:
            for d in derivs:
                ax_ids = [s["id"] for s in d.get("steps", []) if s.get("role") == "axiom"]
                if ax_ids:
                    cut_res = df_mod.grade_axiom_cut(d["id"], ax_ids[0], kg_dir=kg)
                    if not cut_res.get("ok") or not cut_res.get("invalidatedSteps"):
                        errors.append(f"Derivation {d['id']} failed axiom-cut validation.")

        # Validate exam spec via scripts/exam_runner.py
        import scripts.exam_runner as er_mod  # noqa: E402

        with zipfile.ZipFile(pack_path, "r") as zf:
            pdata = json.loads(zf.read(DATA_NAME).decode("utf-8"))
            exams = pdata.get("exams") or []
            if not exams or len(exams[0].get("questions") or []) != 4:
                errors.append("Expected 4-question exam spec inside pack-data.json")
            else:
                er_mod.normalize_spec(exams[0], spec_dir=ws, inline_media=False)

            # Live-verify Counterexample Arena mutants via scripts/falsify_arena.py
            import scripts.falsify_arena as fa_mod  # noqa: E402

            arena_rows = [json.loads(ln) for ln in zf.read("arenas.jsonl").decode("utf-8").splitlines() if ln.strip()]
            if not arena_rows:
                errors.append("Missing arenas.jsonl entries")
            else:
                ar = arena_rows[0]
                fa_mod.create_arena(
                    prop_id=ar.get("propId") or "",
                    domain=ar["domain"],
                    claim=ar["claim"],
                    sealed_reference=ar["sealedReference"],
                    sealed_mutants=ar["sealedMutants"],
                    assumption_predicates=ar.get("assumptionPredicates"),
                    title=ar.get("title"),
                    entry=ar.get("entry"),
                    lang=ar.get("lang") or "python",
                    arena_id=ar["id"],
                    kg_dir=kg,
                )
                if spec and spec.get("arenaTestInputs"):
                    for case in spec["arenaTestInputs"]:
                        if ar["domain"] == "code":
                            inp_str = json.dumps({"args": case["args"]})
                        else:
                            inp_str = str(case["input"])
                        tres = fa_mod.test_arena_input(ar["id"], inp_str, kg_dir=kg)
                        if not tres.get("ok"):
                            errors.append(f"Arena {ar['id']} test input failed: {tres}")
                    final_list = fa_mod.list_arenas(kg_dir=kg)["arenas"]
                    if not final_list or final_list[0]["survivedCount"] != 0:
                        errors.append(f"Arena {ar['id']} still has surviving mutants after canonical test inputs: {final_list}")

    return {
        "ok": len(errors) == 0,
        "path": str(pack_path),
        "slug": ins.get("id") if ins.get("ok") else pack_path.stem,
        "bytes": size_b,
        "packSha256": _sha256_bytes(pack_path.read_bytes()),
        "contentSha256": ins.get("contentSha256") if ins.get("ok") else None,
        "zeroPiiVerified": len(pii_leaks) == 0 and bool(ins.get("zeroPiiVerified")),
        "counts": {
            **(ins.get("counts") or {}),
            **companion_counts,
        },
        "errors": errors,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="build_starter_packs.py",
        description="Build and verify Syntopik v3.0 zero-PII flagship starter packs in packs/",
    )
    parser.add_argument("--out-dir", type=Path, default=ROOT / "packs", help="Output directory (default: packs/)")
    parser.add_argument("--verify", action="store_true", help="Build (unless --verify-only) and strictly verify all 3 packs")
    parser.add_argument("--verify-only", action="store_true", help="Verify existing packs without rebuilding")
    parser.add_argument("--json", action="store_true", help="Emit JSON output")
    args = parser.parse_args(argv)

    out_dir = args.out_dir.expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    built: list[dict[str, Any]] = []
    verified: list[dict[str, Any]] = []

    for spec_fn in PACK_SPECS:
        spec = spec_fn()
        pack_path = out_dir / f"{spec['slug']}.syntopik-pack.zip"
        if not args.verify_only:
            built.append(build_pack(spec, out_dir))
        if args.verify or args.verify_only:
            verified.append(verify_starter_pack(pack_path, spec))

    all_ok = all(v["ok"] for v in verified) if verified else True
    summary = {
        "ok": all_ok,
        "outDir": str(out_dir),
        "builtCount": len(built),
        "verifiedCount": len(verified),
        "passedCount": sum(1 for v in verified if v["ok"]),
        "built": built,
        "verified": verified,
    }

    if args.json:
        print(json.dumps(summary, indent=2, ensure_ascii=False))
    else:
        for row in (verified if verified else built):
            ok_tag = "PASS" if row.get("ok", True) else "FAIL"
            cnt = row.get("counts") or {}
            c_str = (
                f"{cnt.get('propositions', '?')} props · {cnt.get('chains', '?')} chains · "
                f"{cnt.get('derivations', '?')} deriv · {cnt.get('forks', '?')} forks · "
                f"{cnt.get('arenas', '?')} arena · {cnt.get('explorables', '?')} explore · "
                f"{cnt.get('exams', '?')} exam · zeroPII={row.get('zeroPiiVerified', True)}"
                if cnt
                else f"explorables={row.get('explorables', 0)}"
            )
            print(
                f"[{ok_tag}] {row['slug']} ({row['bytes']:,} B) | sha256={row['packSha256'][:16]}... | {c_str}"
            )
            for err in row.get("errors") or []:
                print(f"       ERROR: {err}", file=sys.stderr)
        if verified:
            print(f"\nStarter Packs Verification: {summary['passedCount']}/{summary['verifiedCount']} PASS")

    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
