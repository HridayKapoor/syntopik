#!/usr/bin/env python3
"""Deterministic Builder & Verifier for Syntopik v3.2 1-Click Live GitHub Pages Demo Site.

Builds the zero-install, zero-CDN, zero-PII interactive browser sandbox directly from
Syntopik v3.2's finished `dashboard.html`, `knowledge-graph/viewer.html`, `syntopik/pocket.py`,
and the 3 bundled starter packs (`packs/*.syntopik-pack.zip`) plus cross-domain Rosetta twins:

  - `docs/index.html`   (<= 650 KB self-contained Studio + Gym + Readiness + Interleave + 14-Agent MCP)
  - `docs/viewer.html`  (self-contained Hypergraph Viewer w/ Causal Halos, Rosetta Chords & T1-T4)
  - `docs/pocket.html`  (~25-35 KB self-contained Mobile Pocket Commute Deck)
  - `docs/.nojekyll`    (empty file so GitHub Pages skips Jekyll processing)

Pure Python 3.12+ standard library only (zero external dependencies, deterministic seed 42,
zero personal user data, zero external CDNs, zero eval/new Function, zero sealed-secret leaks).
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import random
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from syntopik.agents_compat import ALL_HARNESSES, HARNESS_SPECS  # noqa: E402
from syntopik.brainstem import plan_next_move  # noqa: E402
from syntopik.evidence import (  # noqa: E402
    GENESIS_HASH,
    LEDGER_FILENAME,
    TIER_LABELS,
    compute_evidence_summary,
    verify_ledger_chain,
)
from syntopik.explorable import compile_explorable_spec, parse_explorable_blocks  # noqa: E402
from syntopik.fsrs_core import (  # noqa: E402
    DEFAULT_FSRS_PARAMETERS,
    apply_fire_credit,
    assess_confidence_signal,
    bkt_update,
    review_card_dict,
)
from syntopik.isomorphism import (  # noqa: E402
    discover_isomorphisms,
    link_isomorphism_chord,
    load_archetypes,
)
from syntopik.learner_model import (  # noqa: E402
    apply_tier1_calibration,
    compile_teaching_policy,
    load_profile,
    record_telemetry_event,
    save_profile,
)
from syntopik.packs import import_pack, verify_pack  # noqa: E402
from syntopik.pocket import export_pocket_html  # noqa: E402
from syntopik.readiness import compute_readiness  # noqa: E402
from syntopik.root_cause import find_blast_radius, localize_fault  # noqa: E402
from syntopik.storage import load_json, load_jsonl, save_json_atomic, save_jsonl_atomic  # noqa: E402

# ---------------------------------------------------------------------------
# Constants & strict verification ceilings
# ---------------------------------------------------------------------------

SEED = 42
DEMO_NOW = datetime(2026, 11, 26, 12, 0, 0, tzinfo=timezone.utc)
DEMO_NOW_ISO = "2026-11-26T12:00:00Z"
DEMO_TODAY = "2026-11-26"
DEMO_WORKSPACE_NAME = "syntopik-live-demo"
MAX_INDEX_BYTES = 650 * 1024  # 650 KB hard ceiling

STARTER_PACK_SLUGS: tuple[str, ...] = (
    "raft-consensus",
    "shm-physics",
    "transformers-attention",
)

FORBIDDEN_SEALED_KEYS: frozenset[str] = frozenset(
    {
        "sealedWinningMove",
        "sealedBlunder",
        "sealedMutants",
        "sealedReference",
        "expectedAnswer",
        "canonicalAnswer",
        "revealedBody",
        "verifySpec",
        "winningMove",
    }
)

FORBIDDEN_PERSONAL_STRINGS: tuple[str, ...] = (
    "SHM-FINAL",
    "426-SIMPLE-HARMONIC-MOTI",
    "eduniti",
    "Eduniti",
)

ABS_USER_PATH_RE = re.compile(
    r"(?:/Users/[A-Za-z0-9._-]+|/home/[A-Za-z0-9._-]+|[A-Za-z]:\\Users\\[A-Za-z0-9._-]+)"
)

FORBIDDEN_HTML_SECRET_TOKENS: tuple[str, ...] = (
    "sealedWinningMove",
    "sealedBlunder",
    "sealedMutants",
    "sealedReference",
)

EXTERNAL_RESOURCE_RE = re.compile(
    r"""(?:<script\b[^>]*\bsrc\s*=\s*["']https?://"""
    r"""|<link\b[^>]*\bhref\s*=\s*["']https?://"""
    r"""|<img\b[^>]*\bsrc\s*=\s*["']https?://"""
    r"""|@import\s+(?:url\s*\(\s*)?["']?https?://"""
    r"""|fonts\.googleapis\.com|cdn\.jsdelivr\.net|unpkg\.com|cdnjs\.cloudflare\.com)""",
    re.IGNORECASE,
)

DANGEROUS_JS_RE = re.compile(r"\b(?:eval\s*\(|new\s+Function\s*\()")


def _fmt_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _safe_script_json(obj: Any) -> str:
    raw = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    return (
        raw.replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def _scan_forbidden_keys(obj: Any, prefix: str = "") -> list[str]:
    hits: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in FORBIDDEN_SEALED_KEYS:
                hits.append(f"{prefix}{k}")
            hits.extend(_scan_forbidden_keys(v, f"{prefix}{k}."))
    elif isinstance(obj, list):
        for idx, item in enumerate(obj):
            hits.extend(_scan_forbidden_keys(item, f"{prefix}[{idx}]."))
    return hits


def _canonical_sha256(obj: dict[str, Any]) -> str:
    raw = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


# ---------------------------------------------------------------------------
# Phase A: Import the 3 bundled zero-PII starter packs into temp workspace
# ---------------------------------------------------------------------------


def _unpack_starter_packs(ws: Path, kg: Path, ch: Path) -> None:
    packs_src = ROOT / "packs"
    packs_dst = ws / "packs"
    packs_dst.mkdir(parents=True, exist_ok=True)
    kg.mkdir(parents=True, exist_ok=True)
    ch.mkdir(parents=True, exist_ok=True)

    for slug in STARTER_PACK_SLUGS:
        src_zip = packs_src / f"{slug}.syntopik-pack.zip"
        if not src_zip.is_file():
            raise FileNotFoundError(f"Missing starter pack: {src_zip}")
        rep = verify_pack(src_zip)
        if not rep.get("ok"):
            raise RuntimeError(f"Starter pack verification failed for {slug}: {rep.get('errors')}")

        dst_zip = packs_dst / src_zip.name
        shutil.copy2(src_zip, dst_zip)

        imp = import_pack(
            dst_zip,
            workspace=ws,
            kg_dir=kg,
            chapters_dir=ch,
            allow_code_arenas=True,
            no_sync=True,
        )
        if not imp.get("ok"):
            raise RuntimeError(f"import_pack failed for {slug}: {imp.get('errors')}")

    # Deterministic mtime for chapter markdown & exam files so sync-graph.mjs ordering is byte-stable
    ts = DEMO_NOW.timestamp()
    for md_out in sorted(ch.glob("*.md")):
        os.utime(md_out, (ts, ts))
    exams_dir = ws / "exams"
    if exams_dir.is_dir():
        for ex_out in sorted(exams_dir.iterdir()):
            if ex_out.is_file():
                os.utime(ex_out, (ts, ts))


# ---------------------------------------------------------------------------
# Phase B: Deterministic 45-Day Multi-Subject Synthetic Learning History
# ---------------------------------------------------------------------------


def _seed_45_day_history(ws: Path, kg: Path, ch: Path) -> None:
    rng = random.Random(SEED)
    props = load_jsonl(kg / "propositions.jsonl")
    chains = load_jsonl(kg / "chains.jsonl")
    existing_ids = {str(p.get("id") or "") for p in props}

    # --- Seed 2 clean cross-domain Rosetta twin propositions (Series RLC & Boltzmann StatMech) ---
    rosetta_seed_props: list[dict[str, Any]] = [
        {
            "id": "elec-rlc-resonance",
            "subject": "Electrical Engineering",
            "chapter": "AC Circuits & Resonance",
            "title": "Series RLC Resonant Circuit & Quality Factor",
            "statement": (
                "A series RLC circuit obeys L q'' + R q' + (1/C) q = V(t) with resonant angular frequency "
                "omega_0 = 1/sqrt(LC), damping ratio zeta = (R/2)*sqrt(C/L), and quality factor "
                "Q = omega_0*L/R separating underdamped, critically damped, and overdamped transients."
            ),
            "kind": "theorem",
            "status": "solid",
            "tags": ["rlc", "series rlc", "resonance", "damping", "second_order_oscillator"],
            "boundaryConditions": [
                "Requires ideal linear time-invariant L, R, C elements without magnetic core saturation.",
                "Breaks at high frequencies where wire length approaches signal wavelength (distributed lines).",
            ],
            "cues": [
                {
                    "id": "c1",
                    "q": "In a series RLC circuit, what are omega_0 and the critical resistance R_crit separating underdamped from overdamped decay?",
                    "a": "omega_0 = 1/sqrt(LC), and critical damping occurs at zeta = 1 => R_crit = 2*sqrt(L/C).",
                }
            ],
            "sourceRef": "AC Circuits Textbook §8.3",
        },
        {
            "id": "phys-statmech-boltzmann",
            "subject": "Statistical Mechanics",
            "chapter": "Canonical Ensemble",
            "title": "Canonical Boltzmann Distribution & Partition Function",
            "statement": (
                "In thermal equilibrium at temperature T, microstate probability obeys "
                "p_i = exp(-E_i / (k_B T)) / Z where Z = sum_j exp(-E_j / (k_B T)) is the canonical "
                "partition function and Helmholtz free energy is F = -k_B T ln Z."
            ),
            "kind": "theorem",
            "status": "solid",
            "tags": ["boltzmann", "partition function", "canonical ensemble", "boltzmann_softmax"],
            "boundaryConditions": [
                "Requires thermal equilibrium with a heat bath at fixed T, V, N (canonical ensemble).",
                "Breaks in driven non-equilibrium open systems or heavy-tailed non-extensive statistics.",
            ],
            "cues": [
                {
                    "id": "c1",
                    "q": "How does temperature T control microstate probabilities p_i = exp(-E_i/(k_B T))/Z in the T -> 0 and T -> inf limits?",
                    "a": "As T -> 0 probability concentrates onto the minimum-energy ground state; as T -> inf all accessible microstates approach uniform probability.",
                }
            ],
            "sourceRef": "Statistical Mechanics §3.1",
        },
    ]
    for rp in rosetta_seed_props:
        if rp["id"] not in existing_ids:
            props.append(rp)
            existing_ids.add(rp["id"])

    prop_by_id = {str(p["id"]): p for p in props}

    # Explicit pedagogical trajectories per proposition over the 45-day window:
    # Each entry: (target_status, review_script: list[(days_ago, rating, confidence, dur_ms)])
    schedules: dict[str, tuple[str, list[tuple[int, str, float, int]]]] = {
        # --- Pack 1: Raft Consensus (Systems) ---
        "sys-raft-quorum": ("solid", [(44, "good", 0.72, 14200), (38, "good", 0.84, 11400), (26, "easy", 0.92, 8600), (9, "good", 0.90, 9100)]),
        "sys-raft-term-monotonic": ("solid", [(43, "good", 0.70, 15100), (35, "good", 0.82, 11800), (20, "good", 0.88, 9900), (6, "easy", 0.92, 8400)]),
        "sys-raft-election-safety": ("solid", [(41, "hard", 0.60, 19800), (34, "good", 0.76, 13400), (21, "good", 0.86, 10800), (5, "good", 0.90, 9500)]),
        "sys-raft-log-append": ("solid", [(39, "good", 0.74, 12900), (28, "good", 0.83, 10600), (12, "good", 0.86, 9800)]),
        "sys-raft-log-matching": ("solid", [(36, "hard", 0.62, 18200), (24, "good", 0.80, 12400), (10, "good", 0.85, 10900)]),
        # Causal root-cause suspect ancestor (upstream of leader-completeness, figure8-commit, state-machine-safety)
        "sys-raft-uptodate-log": ("shaky", [(32, "hard", 0.75, 22400), (22, "again", 0.78, 25600), (14, "hard", 0.55, 20800), (4, "again", 0.68, 23100)]),
        "sys-raft-leader-completeness": ("shaky", [(25, "again", 0.82, 24800), (15, "hard", 0.58, 21900), (3, "hard", 0.60, 19400)]),
        "sys-raft-figure8-commit": ("misconception", [(29, "again", 0.85, 26400), (19, "again", 0.80, 27200), (11, "again", 0.72, 24100), (2, "again", 0.82, 25900)]),
        "sys-raft-split-brain": ("solid", [(31, "good", 0.76, 13600), (17, "good", 0.84, 11200), (4, "good", 0.88, 9700)]),
        "sys-raft-state-machine-safety": ("planned", []),
        # --- Pack 2: Simple Harmonic Motion (Physics) ---
        "phys-shm-linearization": ("solid", [(45, "good", 0.78, 12200), (37, "easy", 0.90, 9100), (23, "good", 0.92, 8500), (7, "easy", 0.95, 7900)]),
        "phys-shm-ode-solution": ("solid", [(42, "good", 0.75, 13800), (33, "good", 0.85, 10900), (18, "good", 0.90, 9400), (5, "good", 0.92, 8800)]),
        "phys-shm-phasor-circle": ("solid", [(40, "hard", 0.64, 17400), (30, "good", 0.80, 12300), (15, "good", 0.87, 10200)]),
        "phys-shm-energy-curvature": ("solid", [(37, "good", 0.72, 14500), (27, "good", 0.84, 11100), (11, "easy", 0.90, 8900)]),
        "phys-shm-constant-force": ("solid", [(34, "good", 0.76, 13100), (22, "good", 0.86, 10400), (8, "good", 0.89, 9200)]),
        "phys-shm-spring-combos": ("shaky", [(28, "again", 0.84, 21400), (18, "hard", 0.62, 18900), (6, "again", 0.76, 22300)]),
        "phys-shm-reduced-mass": ("solid", [(26, "good", 0.70, 15800), (13, "good", 0.82, 12600)]),
        "phys-shm-compound-pendulum": ("solid", [(33, "good", 0.76, 14900), (20, "good", 0.86, 11500), (6, "easy", 0.93, 8700)]),
        "phys-shm-damped-regimes": ("shaky", [(19, "hard", 0.56, 19800), (7, "hard", 0.60, 17600)]),
        "phys-shm-driven-resonance": ("planned", []),
        # --- Pack 3: Transformers & Attention (Machine Learning) ---
        "ml-attn-dot-variance": ("solid", [(43, "good", 0.76, 13400), (35, "good", 0.88, 10200), (21, "easy", 0.93, 8400), (6, "good", 0.94, 8100)]),
        "ml-attn-softmax-saturation": ("solid", [(40, "good", 0.74, 14600), (29, "good", 0.85, 11300), (14, "good", 0.90, 9600)]),
        "ml-attn-scaled-dot": ("solid", [(38, "good", 0.78, 13200), (25, "good", 0.86, 10700), (9, "good", 0.90, 9200)]),
        "ml-attn-causal-mask": ("shaky", [(30, "again", 0.78, 22600), (19, "hard", 0.60, 19200), (5, "hard", 0.64, 18400)]),
        "ml-attn-multihead-subspace": ("solid", [(34, "good", 0.74, 14100), (21, "good", 0.82, 11800), (8, "good", 0.86, 10100)]),
        "ml-attn-permutation-equiv": ("solid", [(31, "good", 0.30, 16200), (16, "good", 0.80, 12100)]),  # includes 1 lucky guess on day 31!
        "ml-attn-rope": ("solid", [(27, "good", 0.72, 15200), (14, "good", 0.82, 11900)]),  # due today for FIRe ancestor refresh demo!
        "ml-attn-quad-complexity": ("solid", [(24, "good", 0.76, 13900), (10, "good", 0.85, 11200)]),
        "ml-attn-kv-cache": ("shaky", [(16, "again", 0.70, 21500), (4, "hard", 0.58, 19100)]),
        "ml-attn-gqa-mqa": ("planned", []),
        # --- Cross-Domain Rosetta Twins (Electrical Engineering & Statistical Mechanics) ---
        "elec-rlc-resonance": ("solid", [(39, "good", 0.76, 13600), (27, "good", 0.86, 10800), (11, "easy", 0.92, 8900)]),
        "phys-statmech-boltzmann": ("solid", [(36, "good", 0.78, 13200), (22, "good", 0.87, 10500), (8, "easy", 0.93, 8400)]),
    }

    # Build chronological review timeline across all 30 propositions
    timeline: list[tuple[int, str, str, float, int]] = []
    for pid, (_target_st, events) in schedules.items():
        for days_ago, rating, conf, dur_ms in events:
            timeline.append((days_ago, pid, rating, conf, dur_ms))
    # Sort oldest first (largest days_ago first), then stable by pid
    timeline.sort(key=lambda row: (-row[0], row[1]))

    revlog: list[dict[str, Any]] = []
    card_id_seq = 1760000000000

    for idx, (days_ago, pid, rating, conf, dur_ms) in enumerate(timeline):
        node = prop_by_id.get(pid)
        if node is None:
            continue
        hour = 9 + ((idx * 3 + rng.randint(0, 2)) % 11)
        minute = (idx * 17) % 60
        ev_dt = (DEMO_NOW - timedelta(days=days_ago)).replace(hour=hour, minute=minute, second=0, microsecond=0)
        ev_iso = _fmt_iso(ev_dt)

        prev_card = node.get("fsrs") if isinstance(node.get("fsrs"), dict) else None
        if prev_card is None:
            card_id_seq += 1000
            prev_card = {"card_id": card_id_seq}

        new_card, meta = review_card_dict(
            prev_card,
            rating,
            now=ev_dt,
            parameters=DEFAULT_FSRS_PARAMETERS,
            enable_fuzzing=False,
        )
        correct = rating in ("good", "easy")
        prior_bkt = float(node.get("bktMastery") or 0.35)
        p_transit = {"again": 0.05, "hard": 0.10, "good": 0.25, "easy": 0.35}[rating]
        new_bkt = bkt_update(prior_bkt, correct=correct, p_transit=p_transit)
        conf_sig = assess_confidence_signal(correct, conf)

        lapses = int(node.get("lapses") or 0) + (1 if rating == "again" else 0)
        reps = int(node.get("reps") or 0) + 1
        new_card["reps"] = reps
        new_card["lapses"] = lapses

        node["fsrs"] = new_card
        node["bktMastery"] = new_bkt
        node["confidence"] = new_bkt
        node["lapses"] = lapses
        node["reps"] = reps
        node["lastReviewed"] = ev_dt.date().isoformat()
        node["lastTested"] = ev_dt.date().isoformat()
        node["nextReview"] = str(new_card.get("due") or "")[:10]
        node["stabilityDays"] = round(float(new_card.get("stability") or 1.0), 2)

        cues = node.get("cues") or []
        cue_id = "c1"
        if cues and isinstance(cues[0], dict):
            cue_id = str(cues[0].get("id") or "c1")
            cues[0]["lastRating"] = rating
            cues[0]["reviews"] = int(cues[0].get("reviews") or 0) + 1

        # Apply FIRe implicit ancestor credit on Good/Easy
        fire_credits: list[dict[str, Any]] = []
        if correct:
            fire_credits = apply_fire_credit(
                pid,
                rating,
                props,
                chains,
                now=ev_dt,
                parameters=DEFAULT_FSRS_PARAMETERS,
            )

        revlog.append(
            {
                "ts": ev_iso,
                "date": ev_dt.date().isoformat(),
                "propId": pid,
                "cueId": cue_id,
                "rating": rating,
                "correct": correct,
                "durationMs": dur_ms,
                "learnerConfidence": conf_sig["learnerConfidence"],
                "hypercorrection": conf_sig["hypercorrection"],
                "luckyGuess": conf_sig["luckyGuess"],
                "brierError": conf_sig["brierError"],
                "elapsedDays": round(float(meta.get("elapsedDays") or 0.0), 2),
                "intervalDays": int(meta.get("intervalDays") or 1),
                "stability": round(float(new_card.get("stability") or 1.0), 3),
                "difficulty": round(float(new_card.get("difficulty") or 5.0), 3),
                "bktAfter": new_bkt,
                "fireCreditsCount": len(fire_credits),
                "hintsUsed": 2 if rating == "again" else (1 if rating == "hard" else 0),
            }
        )

    # Fine-tune final statuses & due dates so the demo dashboard is crisp and actionable on day 0
    due_today_ids = {
        "sys-raft-figure8-commit",
        "sys-raft-leader-completeness",
        "sys-raft-uptodate-log",
        "phys-shm-spring-combos",
        "phys-shm-damped-regimes",
        "ml-attn-causal-mask",
        "ml-attn-kv-cache",
        "ml-attn-rope",  # solid node with 2 parents -> ideal for 1-click FIRe ancestor refresh toast!
    }
    for pid, (target_st, _events) in schedules.items():
        node = prop_by_id.get(pid)
        if node is None:
            continue
        node["status"] = target_st
        if target_st == "planned":
            node.pop("nextReview", None)
            continue
        if pid in due_today_ids:
            due_d = DEMO_TODAY if pid != "sys-raft-figure8-commit" else "2026-11-24"
            node["nextReview"] = due_d
            if isinstance(node.get("fsrs"), dict):
                node["fsrs"]["due"] = f"{due_d}T12:00:00+00:00"
        if pid == "sys-raft-figure8-commit":
            node["lapses"] = max(4, int(node.get("lapses") or 0))
            if isinstance(node.get("fsrs"), dict):
                node["fsrs"]["lapses"] = node["lapses"]
                node["fsrs"]["difficulty"] = 8.75

    save_jsonl_atomic(kg / "propositions.jsonl", props)
    save_jsonl_atomic(kg / "revlog.jsonl", revlog)

    # --- Link 2 clean cross-domain Rosetta Isomorphism Chords via syntopik.isomorphism ---
    link_isomorphism_chord(
        kg,
        "phys-shm-damped-regimes",
        "elec-rlc-resonance",
        "second_order_oscillator",
    )
    link_isomorphism_chord(
        kg,
        "phys-statmech-boltzmann",
        "ml-attn-softmax-saturation",
        "boltzmann_softmax",
    )
    # Normalize createdAt on persisted Rosetta chords so build output is 100% byte-deterministic
    updated_chains = load_jsonl(kg / "chains.jsonl")
    for row in updated_chains:
        if isinstance(row, dict) and str(row.get("relation") or "").lower() == "isomorphic_to":
            row["createdAt"] = "2026-11-24T12:00:00Z"
    save_jsonl_atomic(kg / "chains.jsonl", updated_chains)

    # --- Seed 9 realistic, 100% original multi-subject mistakes (all 3 NEA tiers) ---
    mistakes: list[dict[str, Any]] = [
        {
            "id": "m-demo-01",
            "date": "2026-11-24",
            "ts": "2026-11-24T14:20:00Z",
            "subject": "Systems",
            "chapter": "Raft Consensus",
            "propId": "sys-raft-figure8-commit",
            "category": "misconception",
            "triggerCue": "Old-term log entry replicated onto a majority of servers",
            "wrongMove": "Commit the older-term entry directly by counting replicas",
            "correctMove": "Commit only currentTerm entries by counting replicas; older entries commit indirectly via Log Matching",
            "sourceRef": "Raft Consensus §5.4.2 (Figure 8)",
        },
        {
            "id": "m-demo-02",
            "date": "2026-11-22",
            "ts": "2026-11-22T11:10:00Z",
            "subject": "Systems",
            "chapter": "Raft Consensus",
            "propId": "sys-raft-uptodate-log",
            "category": "condition_missed",
            "triggerCue": "Comparing candidate vs voter log freshness in RequestVote",
            "wrongMove": "Compare log lengths before comparing last log terms",
            "correctMove": "Compare (lastLogTerm, lastLogIndex) lexicographically — higher last term always wins",
            "sourceRef": "Raft Consensus §5.4.1",
        },
        {
            "id": "m-demo-03",
            "date": "2026-11-20",
            "ts": "2026-11-20T16:45:00Z",
            "subject": "Systems",
            "chapter": "Raft Consensus",
            "propId": "sys-raft-leader-completeness",
            "category": "edge_case_missed",
            "triggerCue": "Network partition heals with stale term leader rejoining",
            "wrongMove": "Assume minority leader's uncommitted appends survive partition recovery",
            "correctMove": "Stale leader steps down on higher term and overwrites uncommitted suffix via AppendEntries",
            "sourceRef": "Raft Consensus §5.3",
        },
        {
            "id": "m-demo-04",
            "date": "2026-11-20",
            "ts": "2026-11-20T10:15:00Z",
            "subject": "Physics",
            "chapter": "Simple Harmonic Motion",
            "propId": "phys-shm-spring-combos",
            "category": "misconception",
            "triggerCue": "Two springs k1 and k2 connected end-to-end in series",
            "wrongMove": "Add stiffnesses directly: k_eq = k1 + k2",
            "correctMove": "Invert extensions under shared tension F: 1/k_eq = 1/k1 + 1/k2 => k_eq = k1*k2/(k1+k2)",
            "sourceRef": "SHM Textbook §2.4",
        },
        {
            "id": "m-demo-05",
            "date": "2026-11-17",
            "ts": "2026-11-17T15:30:00Z",
            "subject": "Physics",
            "chapter": "Simple Harmonic Motion",
            "propId": "phys-shm-damped-regimes",
            "category": "sign_error",
            "triggerCue": "Damped oscillator discriminant gamma^2 - omega_0^2",
            "wrongMove": "Drop the factor of 2 in c/(2m) when computing damping ratio zeta",
            "correctMove": "Write x'' + 2*zeta*omega_0*x' + omega_0^2*x = 0 so critical damping occurs at zeta = 1 (c = 2*sqrt(k*m))",
            "sourceRef": "SHM Textbook §3.2",
        },
        {
            "id": "m-demo-06",
            "date": "2026-11-13",
            "ts": "2026-11-13T18:05:00Z",
            "subject": "Physics",
            "chapter": "Simple Harmonic Motion",
            "propId": "phys-shm-energy-curvature",
            "category": "algebra_slip",
            "triggerCue": "Frequency of kinetic energy K(t) = (1/2) m v^2 in SHM",
            "wrongMove": "State K(t) oscillates at the same angular frequency omega as x(t)",
            "correctMove": "Use sin^2(omega*t) = (1 - cos(2*omega*t))/2: K(t) oscillates at 2*omega",
            "sourceRef": "SHM Textbook §1.5",
        },
        {
            "id": "m-demo-07",
            "date": "2026-11-21",
            "ts": "2026-11-21T13:40:00Z",
            "subject": "Machine Learning",
            "chapter": "Transformers & Attention",
            "propId": "ml-attn-causal-mask",
            "category": "edge_case_missed",
            "triggerCue": "Masking future tokens j > i in autoregressive self-attention",
            "wrongMove": "Set masked logits to 0.0 before softmax or zero weights after softmax without renormalizing",
            "correctMove": "Fill j > i logits with -inf BEFORE softmax so exp(-inf) = 0 and remaining rows sum strictly to 1",
            "sourceRef": "Attention Architecture §2.1",
        },
        {
            "id": "m-demo-08",
            "date": "2026-11-18",
            "ts": "2026-11-18T17:12:00Z",
            "subject": "Machine Learning",
            "chapter": "Transformers & Attention",
            "propId": "ml-attn-kv-cache",
            "category": "off_by_one",
            "triggerCue": "KV-cache memory footprint during autoregressive decoding",
            "wrongMove": "Forget the factor of 2 (Keys + Values) or number of layers L when sizing VRAM",
            "correctMove": "Size KV-cache as 2 * L * n_kv_heads * d_head * seq_len * batch * bytes_per_elem",
            "sourceRef": "Attention Architecture §3.4",
        },
        {
            "id": "m-demo-09",
            "date": "2026-11-14",
            "ts": "2026-11-14T19:00:00Z",
            "subject": "Machine Learning",
            "chapter": "Transformers & Attention",
            "propId": "ml-attn-scaled-dot",
            "category": "time_pressure",
            "triggerCue": "Timed drill on attention logit variance scaling denominator",
            "wrongMove": "Rushed and wrote 1/d_k instead of 1/sqrt(d_k)",
            "correctMove": "Var((q.k)/c) = d_k / c^2 = 1 requires c = sqrt(d_k)",
            "sourceRef": "Attention Architecture §1.3",
        },
    ]
    save_jsonl_atomic(kg / "mistakes.jsonl", mistakes)

    # --- Seed deterministic SHA-256 hash-chained Evidence Ledger (T2 / T3 / T4) ---
    ledger_specs: list[tuple[str, str, int, str, str]] = [
        ("2026-11-05T11:00:00Z", "phys-shm-linearization", 4, "derivation_axiom_cut", "Localized small-angle break in compound pendulum DAG"),
        ("2026-11-08T15:30:00Z", "ml-attn-dot-variance", 4, "derivation_blackout", "Clean-room variance scaling proof reconstruction"),
        ("2026-11-11T10:15:00Z", "sys-raft-election-safety", 3, "verifier_pass", "Verified quorum intersection + single-vote-per-term invariant"),
        ("2026-11-12T16:40:00Z", "phys-shm-energy-curvature", 3, "fork_replay", "Woodpecker compression 2.0x on fork-shm-energy-freq"),
        ("2026-11-14T14:20:00Z", "phys-shm-ode-solution", 3, "verifier_pass", "Verified symbolic ODE general solution & phase equivalence"),
        ("2026-11-16T11:45:00Z", "ml-attn-scaled-dot", 3, "fork_replay", "Passed fork-attn-scale-denom in 9.4s"),
        ("2026-11-18T17:00:00Z", "sys-raft-term-monotonic", 3, "verifier_pass", "Verified crash-recovery persistence boundary for votedFor"),
        ("2026-11-19T12:30:00Z", "ml-attn-softmax-saturation", 3, "verifier_pass", "Verified softmax Jacobian collapse under large logit variance"),
        ("2026-11-21T16:10:00Z", "phys-shm-compound-pendulum", 4, "derivation_axiom_cut", "Localized small-angle cut on deriv-shm-compound-pendulum"),
        ("2026-11-23T14:05:00Z", "sys-raft-quorum", 4, "viva_chamber", "Passed 5-turn Adversarial Viva Voce defense (composite=0.88)"),
    ]
    ledger_rows: list[dict[str, Any]] = []
    prev_hash = GENESIS_HASH
    for seq, (ts_str, pid, tier, src, detail) in enumerate(ledger_specs, start=1):
        body = {
            "seq": seq,
            "ts": ts_str,
            "propId": pid,
            "tier": tier,
            "tierLabel": TIER_LABELS[tier],
            "source": src,
            "detail": detail,
            "verified": True,
            "prevHash": prev_hash,
        }
        entry_hash = _canonical_sha256(body)
        ledger_rows.append({**body, "entryHash": entry_hash})
        prev_hash = entry_hash
    save_jsonl_atomic(kg / LEDGER_FILENAME, ledger_rows)
    chain_chk = verify_ledger_chain(kg)
    if not chain_chk.get("ok"):
        raise RuntimeError(f"Seeded evidence ledger hash-chain verification failed: {chain_chk}")

    # --- Enrich Forks (6 total: 4 due today, 2 with Woodpecker history) ---
    forks = load_jsonl(kg / "forks.jsonl")
    for fk in forks:
        fid = str(fk.get("id") or "")
        fk["createdAt"] = "2026-10-20T10:00:00Z"
        if fid == "fork-shm-energy-freq":
            fk["winCount"] = 2
            fk["blunderRepeatCount"] = 1
            fk["woodpeckerPassed"] = True
            fk["status"] = "cleared"
            fk["latencyHistoryMs"] = [18400, 9200]
            fk["nextReview"] = "2026-12-03"
            fk["lastReviewedAt"] = "2026-11-21T10:30:00Z"
        elif fid == "fork-attn-scale-denom":
            fk["winCount"] = 1
            fk["blunderRepeatCount"] = 1
            fk["woodpeckerPassed"] = True
            fk["status"] = "active"
            fk["latencyHistoryMs"] = [16200, 9400]
            fk["nextReview"] = "2026-11-29"
            fk["lastReviewedAt"] = "2026-11-16T11:45:00Z"
        elif fid == "fork-raft-uptodate":
            fk["winCount"] = 0
            fk["blunderRepeatCount"] = 2
            fk["status"] = "active"
            fk["latencyHistoryMs"] = [21500]
            fk["nextReview"] = DEMO_TODAY
        elif fid == "fork-shm-series-springs":
            fk["winCount"] = 0
            fk["blunderRepeatCount"] = 1
            fk["status"] = "active"
            fk["latencyHistoryMs"] = [19400]
            fk["nextReview"] = DEMO_TODAY
        else:
            fk["winCount"] = 0
            fk["blunderRepeatCount"] = 0
            fk["status"] = "active"
            fk["latencyHistoryMs"] = []
            fk["nextReview"] = DEMO_TODAY
    save_jsonl_atomic(kg / "forks.jsonl", forks)

    # --- Enrich Arenas (3 total: 1 partially killed 1/3, 2 fresh 0/3) ---
    arenas = load_jsonl(kg / "arenas.jsonl")
    for ar in arenas:
        if ar.get("id") == "arena-shm-series-stiffness":
            ar["killedMutants"] = ["m1"]
            ar["attemptsCount"] = 2
            ar["status"] = "open"
            ar["updatedAt"] = "2026-11-20T10:40:00Z"
        else:
            ar["killedMutants"] = []
            ar["attemptsCount"] = 0
            ar["status"] = "open"
            ar["updatedAt"] = "2026-11-24T12:00:00Z"
    save_jsonl_atomic(kg / "arenas.jsonl", arenas)

    # --- Enrich Derivations (3 total: mark SHM compound pendulum as completed once for T4) ---
    derivs = load_jsonl(kg / "derivations.jsonl")
    for dv in derivs:
        dv["updatedAt"] = "2026-11-21T16:10:00Z"
        if dv.get("id") == "deriv-shm-compound-pendulum":
            dv["completions"] = 1
            dv["axiomCutPassed"] = True
            dv["passed"] = True
    save_jsonl_atomic(kg / "derivations.jsonl", derivs)

    # --- Seed 4 graded ShadowBox Blitz decisions ---
    decisions = [
        {
            "id": "dec-demo-01",
            "ts": "2026-11-18T11:20:00Z",
            "date": "2026-11-18",
            "propId": "sys-raft-election-safety",
            "subject": "Systems",
            "killCueHit": True,
            "firstMoveHit": True,
            "decoyHit": True,
            "decisionMs": 14800,
            "graded": True,
            "status": "graded",
            "deepPrinciple": "Quorum Intersection + Single-Vote-Per-Term",
        },
        {
            "id": "dec-demo-02",
            "ts": "2026-11-19T14:05:00Z",
            "date": "2026-11-19",
            "propId": "phys-shm-constant-force",
            "subject": "Physics",
            "killCueHit": True,
            "firstMoveHit": True,
            "decoyHit": True,
            "decisionMs": 12400,
            "graded": True,
            "status": "graded",
            "deepPrinciple": "Constant Force Shifts Equilibrium, Never Period",
        },
        {
            "id": "dec-demo-03",
            "ts": "2026-11-22T16:30:00Z",
            "date": "2026-11-22",
            "propId": "ml-attn-scaled-dot",
            "subject": "Machine Learning",
            "killCueHit": True,
            "firstMoveHit": True,
            "decoyHit": False,
            "decisionMs": 17900,
            "graded": True,
            "status": "graded",
            "deepPrinciple": "Variance Normalization by sqrt(d_k)",
        },
        {
            "id": "dec-demo-04",
            "ts": "2026-11-24T15:10:00Z",
            "date": "2026-11-24",
            "propId": "sys-raft-uptodate-log",
            "subject": "Systems",
            "killCueHit": False,
            "firstMoveHit": False,
            "decoyHit": True,
            "decisionMs": 23600,
            "graded": True,
            "status": "graded",
            "deepPrinciple": "Lexicographic (lastTerm, lastIndex) Freshness",
        },
    ]
    save_jsonl_atomic(kg / "decisions.jsonl", decisions)

    # --- Seed 1 completed passed Viva Voce Defense session (T4 evidence) ---
    viva_sessions = [
        {
            "id": "viva-demo-raft-quorum",
            "propId": "sys-raft-quorum",
            "propTitle": "Quorum Intersection Principle",
            "subject": "Systems",
            "chapter": "Raft Consensus",
            "status": "completed",
            "outcome": "passed",
            "passed": True,
            "currentTurn": 5,
            "totalTurns": 5,
            "prompts": [
                {"turn": 1, "stage": "commitment_claim", "prompt": "State the Quorum Intersection Principle and why it prevents split elections."},
                {"turn": 2, "stage": "boundary_perturbation", "prompt": "What breaks if cluster membership changes dynamically from N=3 to N=5 without joint consensus?"},
                {"turn": 3, "stage": "assumption_attack", "prompt": "If a voter crashes and forgets votedFor on restart, does quorum intersection alone save Election Safety?"},
                {"turn": 4, "stage": "transfer_perturbation", "prompt": "Why does an even cluster of N=4 tolerate only f=1 failure—the same as N=3?"},
                {"turn": 5, "stage": "sharpened_synthesis", "prompt": "Synthesize the exact boundary conditions under which quorum intersection guarantees Election Safety."},
            ],
            "turns": [
                {"turn": 1, "response": "Any two majorities of floor(N/2)+1 overlap in at least one server by Pigeonhole Principle, preventing two leaders in one term when votes are single-use.", "confidence": 0.85},
                {"turn": 2, "response": "During uncoordinated reconfiguration, two disjoint quorums can form across old and new configurations; joint consensus C_old,new forces intersection across both.", "confidence": 0.80},
                {"turn": 3, "response": "No — quorum intersection only guarantees a shared witness; without durable votedFor + currentTerm, that witness can vote twice in the same term.", "confidence": 0.90},
                {"turn": 4, "response": "For N=4, majority requires 3 servers, leaving only 1 spare failure before availability stalls—identical fault tolerance to N=3 with extra replication overhead.", "confidence": 0.85},
                {"turn": 5, "response": "Election Safety requires static or joint-consensus membership plus durable single-vote-per-term storage so the overlapping quorum witness cannot endorse two candidates.", "confidence": 0.88},
            ],
            "commitmentLedger": {
                "rawClaim": "Any two majorities of floor(N/2)+1 overlap in at least one server by Pigeonhole Principle, preventing two leaders in one term when votes are single-use.",
                "assertedUniversal": False,
            },
            "scores": {
                "clarity": 0.90,
                "boundary_awareness": 0.88,
                "transfer": 0.86,
                "calibration": 0.88,
                "composite": 0.88,
            },
            "evidenceRecorded": True,
            "demotedToShaky": False,
            "createdAt": "2026-11-23T13:50:00Z",
            "updatedAt": "2026-11-23T14:05:00Z",
        }
    ]
    save_jsonl_atomic(kg / "viva-sessions.jsonl", viva_sessions)

    # --- Seed 2 graded Exam Attempts (with v3.2 EV-Skip calibration & Why-Wrong Distractor Autopsy) ---
    exam_attempts = [
        {
            "id": "exam-att-raft-01",
            "date": "2026-11-23",
            "examSlug": "raft-consensus-checkpoint",
            "title": "Raft Consensus Safety Checkpoint",
            "examTitle": "Raft Consensus Safety Checkpoint",
            "subject": "Systems",
            "chapter": "Raft Consensus",
            "examType": "chapter_mock",
            "score": 13,
            "maxScore": 16,
            "percentage": 81.3,
            "accuracyPct": 83.3,
            "correctCount": 3,
            "partialCount": 1,
            "wrongCount": 1,
            "unattemptedCount": 0,
            "negativeMarksLost": 1.0,
            "timeSpentSec": 465,
            "evDisciplineScore": 84.0,
            "evCalibration": {
                "evDisciplineScore": 84.0,
                "negEvGamblesCount": 1,
                "underconfidentSkipsCount": 0,
                "positiveEvAttemptsCount": 4,
                "evBasis": "observed",
            },
            "distractorAutopsy": [
                {
                    "questionId": "Q4",
                    "optionId": "B",
                    "trapCategory": "misconception",
                    "confusedWith": "Current-term majority commit",
                    "whyWrong": "Older-term entries replicated onto a majority can still be overwritten by a higher-term leader unless committed indirectly via a currentTerm entry.",
                }
            ],
        },
        {
            "id": "exam-att-shm-01",
            "date": "2026-11-16",
            "examSlug": "shm-physics-checkpoint",
            "title": "SHM Mechanics & Pendulum Mock",
            "examTitle": "SHM Mechanics & Pendulum Mock",
            "subject": "Physics",
            "chapter": "Simple Harmonic Motion",
            "examType": "chapter_mock",
            "score": 12,
            "maxScore": 16,
            "percentage": 75.0,
            "accuracyPct": 75.0,
            "correctCount": 3,
            "partialCount": 0,
            "wrongCount": 1,
            "unattemptedCount": 0,
            "negativeMarksLost": 1.0,
            "timeSpentSec": 510,
            "evDisciplineScore": 78.5,
            "evCalibration": {
                "evDisciplineScore": 78.5,
                "negEvGamblesCount": 1,
                "underconfidentSkipsCount": 1,
                "positiveEvAttemptsCount": 3,
                "evBasis": "observed",
            },
            "distractorAutopsy": [
                {
                    "questionId": "Q3",
                    "optionId": "A",
                    "trapCategory": "misconception",
                    "confusedWith": "Parallel springs stiffness",
                    "whyWrong": "Springs in series share tension F so extensions add: 1/k_eq = 1/k1 + 1/k2, making the chain softer than either spring alone (not k1 + k2).",
                }
            ],
        },
    ]
    save_jsonl_atomic(kg / "exam-attempts.jsonl", exam_attempts)

    # --- Seed Exam Configuration for v3.2 Readiness Forecaster (10 days out from DEMO_TODAY) ---
    save_json_atomic(
        kg / "study-config.json",
        {
            "examDate": "2026-12-06",
            "examName": "STEM Mastery Checkpoint (SHM · Raft · Attention)",
            "maxMarks": 100,
            "sleepHours": 7.5,
            "dailyMinutes": 120,
        },
    )

    # --- Seed 1 Active Live Whiteboard Session (3 frames incl. 1 blurred prediction_gate + 1 explore_widget) ---
    wb_explore_spec = compile_explorable_spec(
        {
            "id": "wb-raft-quorum-lab",
            "title": "Quorum Size & Fault Tolerance Tradeoff",
            "prompt": "Slide cluster size N to see why odd cluster sizes maximize crash tolerance per server.",
            "params": [
                {"name": "N", "label": "Cluster Size N", "min": 3, "max": 11, "step": 1, "default": 5, "unit": "nodes"},
            ],
            "outputs": [
                {"label": "Majority Quorum Q", "expr": "floor(N / 2) + 1", "unit": "nodes"},
                {"label": "Max Failures f", "expr": "N - (floor(N / 2) + 1)", "unit": "nodes"},
                {"label": "Quorum Overlap |Q1 n Q2|", "expr": "2 * (floor(N / 2) + 1) - N", "unit": "nodes"},
            ],
            "curve": {
                "xVar": "N",
                "xMin": 3,
                "xMax": 11,
                "samples": 9,
                "exactExpr": "N - (floor(N / 2) + 1)",
                "approxExpr": "(N - 1) / 2",
                "yLabel": "Faults Tolerated f",
            },
        }
    )
    whiteboard_session = {
        "schemaVersion": 1,
        "sessionId": "wb-demo-raft-fig8",
        "revision": 3,
        "title": "Raft Figure 8 Safety & Indirect Commitment",
        "activeFrameId": "wb-f2",
        "frames": [
            {
                "id": "wb-f1",
                "seq": 1,
                "kind": "derivation_step",
                "title": "1. Quorum Overlap Forces a Term Witness",
                "body": "Any two majority quorums in an $N$-server cluster share at least one voter: $2(\\lfloor N/2 \\rfloor + 1) > N$. Paired with durable `votedFor`, at most one leader can be elected in any term.",
                "latex": "|Q_1 \\cap Q_2| \\ge 1 \\implies \\text{leaders}(t) \\le 1",
                "propId": "sys-raft-quorum",
                "stepIndex": 0,
                "locked": False,
                "gate": None,
                "payload": {},
                "createdAt": "2026-11-26T11:42:00Z",
                "updatedAt": "2026-11-26T11:42:00Z",
            },
            {
                "id": "wb-f2",
                "seq": 2,
                "kind": "prediction_gate",
                "title": "2. Prediction Gate — Raft Figure 8 Trap",
                "body": "",
                "latex": None,
                "propId": "sys-raft-figure8-commit",
                "stepIndex": 1,
                "locked": True,
                "gate": {
                    "prompt": "A Term-4 leader replicates an older Term-2 entry onto 3 of 5 servers. Can it safely commit that Term-2 entry purely by counting replicas? Predict YES or NO and state the mechanism before unlocking.",
                    "revealedBody": "**No — committing prior-term entries purely by replica count violates Leader Completeness (Raft Figure 8).** A server holding a Term-3 entry at the same index can still win Term 5 over the majority that only holds Term 2, then overwrite the 'committed' entry. Raft only commits entries from `currentTerm` by counting replicas; older entries commit indirectly via Log Matching.",
                    "expectedAnswer": "No — a higher intermediate term entry can still win election and overwrite the old-term majority.",
                    "prediction": None,
                    "confidence": None,
                    "unlockedAt": None,
                },
                "payload": {},
                "createdAt": "2026-11-26T11:44:00Z",
                "updatedAt": "2026-11-26T11:44:00Z",
            },
            {
                "id": "wb-f3",
                "seq": 3,
                "kind": "explore_widget",
                "title": "3. Live Lab — Why Odd Cluster Sizes Win",
                "body": "Test why $N=4$ tolerates the same $f=1$ failure as $N=3$ while requiring a larger quorum.",
                "latex": None,
                "propId": "sys-raft-quorum",
                "stepIndex": 2,
                "locked": False,
                "gate": None,
                "payload": {"spec": wb_explore_spec},
                "createdAt": "2026-11-26T11:46:00Z",
                "updatedAt": "2026-11-26T11:46:00Z",
            },
        ],
        "pins": [],
        "updatedAt": "2026-11-26T11:46:00Z",
    }
    save_json_atomic(kg / "whiteboard-session.json", whiteboard_session)

    # --- Update Chapter Trackers (raft-consensus ACTIVE; shm & transformers inactive with progress) ---
    raft_tr_path = ch / "raft-consensus-tracker.json"
    raft_tr = load_json(raft_tr_path, default={}) or {}
    raft_tr["active"] = True
    raft_tr["title"] = "Raft Consensus — Distributed Safety Spine"
    raft_tr["subject"] = "Systems"
    raft_tr["createdAt"] = "2026-10-14T09:00:00Z"
    raft_tr["updatedAt"] = DEMO_NOW_ISO
    raft_tr["stepCounter"] = 14
    raft_tr["milestones"] = {
        "sourcesIndexed": True,
        "probed": True,
        "theoryComplete": False,
        "questionsComplete": False,
        "revisionSheetBuilt": True,
        "cbtMockBuilt": True,
        "cbtMockPassed": False,
        "audioBriefBuilt": False,
    }
    for t in raft_tr.get("topics") or []:
        tid = t.get("id")
        if tid == "raft-t1":
            t["status"] = "mastered"
        elif tid == "raft-t2":
            t["status"] = "in_progress"
        else:
            t["status"] = "pending"
    raft_tr["questions"] = [
        {"qid": "raft-q1", "label": "Q1 · Quorum intersection lower bound", "topicId": "raft-t1", "status": "mastered", "lastStep": 3},
        {"qid": "raft-q2", "label": "Q2 · Persisted votedFor across crash-restart", "topicId": "raft-t1", "status": "mastered", "lastStep": 5},
        {"qid": "raft-q3", "label": "Q3 · Election Safety two-axiom proof", "topicId": "raft-t1", "status": "solved", "lastStep": 7},
        {"qid": "raft-q4", "label": "Q4 · AppendEntries prevLogTerm induction", "topicId": "raft-t2", "status": "solved", "lastStep": 9},
        {"qid": "raft-q5", "label": "Q5 · RequestVote lexicographic up-to-date check", "topicId": "raft-t2", "status": "retest", "lastStep": 10},
        {"qid": "raft-q6", "label": "Q6 · Figure 8 old-term overwrite trace", "topicId": "raft-t3", "status": "unseen", "lastStep": None},
        {"qid": "raft-q7", "label": "Q7 · 2-vs-3 partition rollback behavior", "topicId": "raft-t3", "status": "solved", "lastStep": 12},
        {"qid": "raft-q8", "label": "Q8 · End-to-end State Machine Safety chain", "topicId": "raft-t3", "status": "unseen", "lastStep": None},
    ]
    save_json_atomic(raft_tr_path, raft_tr)
    os.utime(raft_tr_path, (DEMO_NOW.timestamp(), DEMO_NOW.timestamp()))

    shm_tr_path = ch / "shm-physics-tracker.json"
    shm_tr = load_json(shm_tr_path, default={}) or {}
    shm_tr["active"] = False
    shm_tr["createdAt"] = "2026-10-12T09:00:00Z"
    shm_tr["updatedAt"] = "2026-11-21T16:15:00Z"
    shm_tr["milestones"] = {
        "sourcesIndexed": True,
        "probed": True,
        "theoryComplete": True,
        "questionsComplete": False,
        "revisionSheetBuilt": True,
        "cbtMockBuilt": True,
        "cbtMockPassed": True,
        "audioBriefBuilt": False,
    }
    for t in shm_tr.get("topics") or []:
        tid = t.get("id")
        t["status"] = "mastered" if tid in ("shm-t1", "shm-t2") else "in_progress"
    shm_tr["questions"] = [
        {"qid": "shm-q1", "label": "Q1 · Small-angle pendulum linearization", "topicId": "shm-t1", "status": "mastered"},
        {"qid": "shm-q2", "label": "Q2 · Phasor time-interval geometry", "topicId": "shm-t1", "status": "mastered"},
        {"qid": "shm-q3", "label": "Q3 · Constant force equilibrium shift", "topicId": "shm-t2", "status": "solved"},
        {"qid": "shm-q4", "label": "Q4 · Series vs parallel stiffness derivation", "topicId": "shm-t2", "status": "retest"},
        {"qid": "shm-q5", "label": "Q5 · Two-body reduced mass oscillation", "topicId": "shm-t2", "status": "solved"},
        {"qid": "shm-q6", "label": "Q6 · Critical damping vs underdamped decay", "topicId": "shm-t3", "status": "unseen"},
    ]
    save_json_atomic(shm_tr_path, shm_tr)
    os.utime(shm_tr_path, (DEMO_NOW.timestamp() - 3600, DEMO_NOW.timestamp() - 3600))

    attn_tr_path = ch / "transformers-attention-tracker.json"
    attn_tr = load_json(attn_tr_path, default={}) or {}
    attn_tr["active"] = False
    attn_tr["createdAt"] = "2026-10-15T09:00:00Z"
    attn_tr["updatedAt"] = "2026-11-22T15:00:00Z"
    attn_tr["milestones"] = {
        "sourcesIndexed": True,
        "probed": True,
        "theoryComplete": False,
        "questionsComplete": False,
        "revisionSheetBuilt": False,
        "cbtMockBuilt": False,
        "cbtMockPassed": False,
        "audioBriefBuilt": False,
    }
    for t in attn_tr.get("topics") or []:
        tid = t.get("id")
        t["status"] = "mastered" if tid == "attn-t1" else ("in_progress" if tid == "attn-t2" else "pending")
    attn_tr["questions"] = [
        {"qid": "attn-q1", "label": "Q1 · 1/sqrt(d_k) variance proof", "topicId": "attn-t1", "status": "mastered"},
        {"qid": "attn-q2", "label": "Q2 · Softmax Jacobian saturation regime", "topicId": "attn-t1", "status": "solved"},
        {"qid": "attn-q3", "label": "Q3 · Pre-softmax -inf causal mask", "topicId": "attn-t2", "status": "retest"},
        {"qid": "attn-q4", "label": "Q4 · RoPE relative phase inner product", "topicId": "attn-t2", "status": "solved"},
        {"qid": "attn-q5", "label": "Q5 · KV-cache VRAM scaling", "topicId": "attn-t3", "status": "unseen"},
        {"qid": "attn-q6", "label": "Q6 · GQA vs MQA bandwidth tradeoff", "topicId": "attn-t3", "status": "unseen"},
    ]
    save_json_atomic(attn_tr_path, attn_tr)
    os.utime(attn_tr_path, (DEMO_NOW.timestamp() - 7200, DEMO_NOW.timestamp() - 7200))

    # --- Seed Calibrated Learner Profile (Domain Elo across Distributed Systems / Physics / Deep Learning, Flow state, NEA DNA) ---
    prof = load_profile(kg)
    prof = apply_tier1_calibration(
        prof,
        entry_ramp="balanced",
        challenge_appetite="balanced",
        domain_fluency={"Systems": 2, "Physics": 2, "Machine Learning": 2},
        session_minutes=30,
        prose_density="standard",
    )
    prof["calibration"]["completedAt"] = "2026-10-12T09:15:00Z"
    save_profile(kg, prof)

    # Feed realistic telemetry events through learner_model so Pelanek Elo, NEA Failure DNA, and Flow state are authentic
    telemetry_events: list[dict[str, Any]] = [
        # Earlier mixed errors to populate realistic NEA Failure DNA (model + process + pacing)
        {"domain": "Systems", "propId": "sys-raft-figure8-commit", "kind": "quiz", "correct": False, "difficulty": 1620, "durationMs": 25900, "confidence": 0.82, "hintsUsed": 1, "mistakeCategory": "misconception", "ts": "2026-11-24T14:20:00Z"},
        {"domain": "Systems", "propId": "sys-raft-uptodate-log", "kind": "fork", "correct": False, "difficulty": 1560, "durationMs": 23100, "confidence": 0.68, "hintsUsed": 1, "mistakeCategory": "condition_missed", "ts": "2026-11-22T11:10:00Z"},
        {"domain": "Physics", "propId": "phys-shm-spring-combos", "kind": "quiz", "correct": False, "difficulty": 1520, "durationMs": 21400, "confidence": 0.84, "hintsUsed": 1, "mistakeCategory": "misconception", "ts": "2026-11-20T10:15:00Z"},
        {"domain": "Physics", "propId": "phys-shm-damped-regimes", "kind": "practice", "correct": False, "difficulty": 1580, "durationMs": 74000, "confidence": 0.60, "hintsUsed": 1, "mistakeCategory": "sign_error", "ts": "2026-11-17T15:30:00Z"},
        {"domain": "Machine Learning", "propId": "ml-attn-causal-mask", "kind": "arena", "correct": False, "difficulty": 1590, "durationMs": 48000, "confidence": 0.72, "hintsUsed": 0, "mistakeCategory": "edge_case_missed", "ts": "2026-11-21T13:40:00Z"},
        {"domain": "Machine Learning", "propId": "ml-attn-kv-cache", "kind": "practice", "correct": False, "difficulty": 1550, "durationMs": 68000, "confidence": 0.62, "hintsUsed": 1, "mistakeCategory": "off_by_one", "ts": "2026-11-18T17:12:00Z"},
        {"domain": "Physics", "propId": "phys-shm-energy-curvature", "kind": "review", "correct": False, "difficulty": 1480, "durationMs": 14500, "confidence": 0.70, "hintsUsed": 0, "mistakeCategory": "algebra_slip", "ts": "2026-11-13T18:05:00Z"},
        {"domain": "Machine Learning", "propId": "ml-attn-scaled-dot", "kind": "blitz", "correct": False, "difficulty": 1520, "durationMs": 29500, "confidence": 0.65, "hintsUsed": 0, "mistakeCategory": "time_pressure", "ts": "2026-11-14T19:00:00Z"},
        # Recent clean flow session across all 3 domains so affect == "flow" and Domain Elo reflects strong progress!
        {"domain": "Physics", "propId": "phys-shm-linearization", "kind": "review", "correct": True, "difficulty": 1560, "durationMs": 10200, "confidence": 0.92, "hintsUsed": 0, "ts": "2026-11-25T10:05:00Z"},
        {"domain": "Physics", "propId": "phys-shm-compound-pendulum", "kind": "derivation", "correct": True, "difficulty": 1680, "durationMs": 72000, "confidence": 0.90, "hintsUsed": 0, "ts": "2026-11-25T10:12:00Z"},
        {"domain": "Physics", "propId": "phys-shm-ode-solution", "kind": "review", "correct": True, "difficulty": 1540, "durationMs": 10800, "confidence": 0.88, "hintsUsed": 0, "ts": "2026-11-25T10:18:00Z"},
        {"domain": "Machine Learning", "propId": "ml-attn-dot-variance", "kind": "derivation", "correct": True, "difficulty": 1650, "durationMs": 68000, "confidence": 0.92, "hintsUsed": 0, "ts": "2026-11-25T16:10:00Z"},
        {"domain": "Machine Learning", "propId": "ml-attn-scaled-dot", "kind": "fork", "correct": True, "difficulty": 1600, "durationMs": 28400, "confidence": 0.88, "hintsUsed": 0, "ts": "2026-11-25T16:16:00Z"},
        {"domain": "Machine Learning", "propId": "ml-attn-softmax-saturation", "kind": "review", "correct": True, "difficulty": 1560, "durationMs": 11400, "confidence": 0.86, "hintsUsed": 0, "ts": "2026-11-25T16:22:00Z"},
        {"domain": "Systems", "propId": "sys-raft-quorum", "kind": "viva", "correct": True, "difficulty": 1640, "durationMs": 76000, "confidence": 0.88, "hintsUsed": 0, "ts": "2026-11-26T11:25:00Z"},
        {"domain": "Systems", "propId": "sys-raft-election-safety", "kind": "review", "correct": True, "difficulty": 1580, "durationMs": 11900, "confidence": 0.85, "hintsUsed": 0, "ts": "2026-11-26T11:32:00Z"},
        {"domain": "Systems", "propId": "sys-raft-split-brain", "kind": "review", "correct": True, "difficulty": 1540, "durationMs": 11200, "confidence": 0.84, "hintsUsed": 0, "ts": "2026-11-26T11:38:00Z"},
        {"domain": "Machine Learning", "propId": "ml-attn-quad-complexity", "kind": "review", "correct": True, "difficulty": 1520, "durationMs": 12400, "confidence": 0.85, "hintsUsed": 0, "ts": "2026-11-26T11:42:00Z"},
    ]
    for ev in telemetry_events:
        record_telemetry_event(
            kg,
            domain=ev["domain"],
            prop_id=ev.get("propId"),
            kind=ev.get("kind", "review"),
            correct=bool(ev.get("correct", True)),
            difficulty=float(ev.get("difficulty", 1500.0)),
            confidence=ev.get("confidence"),
            duration_ms=ev.get("durationMs"),
            hints_used=int(ev.get("hintsUsed", 0)),
            mistake_category=ev.get("mistakeCategory"),
            now=ev.get("ts"),
        )

    prof = load_profile(kg)
    # Use clean domain display labels matching the 3 flagship starter packs
    raw_domains = prof.get("domains") or {}
    clean_domains: dict[str, Any] = {}
    for k, v in raw_domains.items():
        if k == "Systems":
            clean_domains["Distributed Systems"] = v
        elif k == "Machine Learning":
            clean_domains["Deep Learning"] = v
        else:
            clean_domains[k] = v
    prof["domains"] = clean_domains
    # Ensure calm active flow state and deterministic updatedAt
    prof["affectiveState"]["state"] = "flow"
    prof["affectiveState"]["errorStreak"] = 0
    prof["affectiveState"]["activeSessionMinutes"] = 18.0
    prof["affectiveState"]["lastEventAt"] = "2026-11-26T11:42:00Z"
    prof["updatedAt"] = DEMO_NOW_ISO
    prof["compiledPolicy"] = compile_teaching_policy(prof, domain="Distributed Systems", energy="normal")
    save_profile(kg, prof)


# ---------------------------------------------------------------------------
# Phase C: Build schemaVersion 5 payload via sync-graph.mjs + v3 engines
# ---------------------------------------------------------------------------


def _extract_js_assignment(bundle_text: str, var_name: str) -> dict[str, Any]:
    marker = f"window.{var_name} = "
    idx = bundle_text.find(marker)
    if idx < 0:
        raise ValueError(f"Could not locate {marker} in graph-data.js")
    start = idx + len(marker)
    end = bundle_text.find(";\n", start)
    if end < 0:
        end = bundle_text.rfind(";")
    return json.loads(bundle_text[start:end])


def _normalize_mtimes(obj: Any) -> Any:
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for k, v in obj.items():
            if k == "mtimeIso" and isinstance(v, str):
                out[k] = DEMO_NOW_ISO
            else:
                out[k] = _normalize_mtimes(v)
        return out
    if isinstance(obj, list):
        return [_normalize_mtimes(x) for x in obj]
    return obj


def _build_demo_payloads(ws: Path, kg: Path, ch: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    # Run scripts/sync-graph.mjs against the ephemeral temp workspace
    sync_script = ROOT / "scripts" / "sync-graph.mjs"
    out_js = kg / "graph-data.js"
    proc = subprocess.run(
        ["node", str(sync_script), "--workspace", str(ws), "--kg-dir", str(kg), "--chapters-dir", str(ch), "--out", str(out_js), "--quiet"],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0 or not out_js.is_file():
        raise RuntimeError(f"sync-graph.mjs failed (rc={proc.returncode}): {proc.stderr or proc.stdout}")

    bundle_txt = out_js.read_text(encoding="utf-8")
    study_graph = _extract_js_assignment(bundle_txt, "__STUDY_GRAPH__")
    study_hub = _extract_js_assignment(bundle_txt, "__STUDY_HUB__")

    # Enrich with Python v3.0 engines for exact parity with syntopik/server.py
    ev_summary = compute_evidence_summary(ws)
    blast = find_blast_radius(kg, limit=12)

    # Combine root_cause.py Bayesian fault localization across weak/failing leaves + blast radius
    props = study_graph.get("propositions") or []
    by_prop: dict[str, float] = dict((study_hub.get("rootCauseHotspots") or {}).get("byProp") or {})
    for p in props:
        pid = str(p.get("id") or "")
        st = str(p.get("status") or "").lower()
        if pid and st in ("shaky", "misconception"):
            loc = localize_fault(kg, pid, max_depth=4)
            if loc.get("ok"):
                for susp in loc.get("suspects") or []:
                    sid = str(susp.get("propId") or "")
                    sc = float(susp.get("suspectRootScore") or 0.0)
                    if sid and sc > by_prop.get(sid, 0.0):
                        by_prop[sid] = round(sc, 4)

    next_move = plan_next_move(ws, energy="normal")
    prof = load_profile(kg)
    prof["updatedAt"] = DEMO_NOW_ISO
    prof["compiledPolicy"] = compile_teaching_policy(prof, domain="Distributed Systems", energy="normal")

    # Compile all chapter :::explore blocks via syntopik.explorable (exact server.py parity)
    explorables_map: dict[str, list[dict[str, Any]]] = {}
    for md_path in sorted(ch.glob("*.md")):
        blocks = parse_explorable_blocks(md_path.read_text(encoding="utf-8"), strict=True)
        if blocks:
            explorables_map[md_path.stem] = blocks

    # Compute deterministic v3.1 Rosetta Isomorphism payload (persisted chords + top discovered per archetype)
    rosetta_raw = discover_isomorphisms(kg, min_score=0.35)
    raw_chords = list(rosetta_raw.get("chords") or [])
    curated_chords: list[dict[str, Any]] = [c for c in raw_chords if c.get("persisted")]
    disc_per_arch: dict[str, int] = {}
    for ch_item in raw_chords:
        if ch_item.get("persisted"):
            continue
        aid = str(ch_item.get("archetypeId") or "")
        if disc_per_arch.get(aid, 0) < 1:
            curated_chords.append(ch_item)
            disc_per_arch[aid] = disc_per_arch.get(aid, 0) + 1
    rosetta_payload: dict[str, Any] = {
        "ok": True,
        "kgDir": "knowledge-graph",
        "chords": [
            {k: v for k, v in c.items() if k != "bijectionQuestion"}
            for c in curated_chords
        ],
        "archetypesMatched": [
            {
                "archetypeId": a.get("archetypeId"),
                "archetypeName": a.get("archetypeName"),
                "chordCount": a.get("chordCount", 0),
                "propositionCount": a.get("propositionCount", 0),
            }
            for a in (rosetta_raw.get("archetypesMatched") or [])
            if int(a.get("chordCount") or 0) > 0
        ],
        "chordCount": len(curated_chords),
        "persistedCount": sum(1 for c in curated_chords if c.get("persisted")),
        "discoveredCount": sum(1 for c in curated_chords if not c.get("persisted")),
        "archetypesCount": len(load_archetypes()),
    }

    # Deterministic v3.1 Multi-Device CRDT Sync status (zero temp paths)
    revlog_count = len(load_jsonl(kg / "revlog.jsonl"))
    sync_status_payload: dict[str, Any] = {
        "ok": True,
        "workspace": DEMO_WORKSPACE_NAME,
        "format": "syntopik-delta-v1",
        "schemaVersion": 1,
        "initialized": True,
        "deviceId": "dev-demo-01",
        "lamportClock": 7,
        "lastExportedAt": "2026-11-25T18:30:00Z",
        "lastMergedAt": "2026-11-25T18:35:00Z",
        "lastSyncedAt": "2026-11-25T18:35:00Z",
        "peerClocks": {"dev-tablet-02": 5},
        "mergedDeltaCount": 1,
        "trackerSlugs": list(STARTER_PACK_SLUGS),
        "counts": {
            "propositions": len(study_graph.get("propositions") or []),
            "chains": len(study_graph.get("chains") or []),
            "revlog": revlog_count,
            "mistakes": len(study_hub.get("mistakes") or []),
            "evidenceLedger": int((ev_summary.get("ledgerIntegrity") or {}).get("count") or 0),
            "vivaSessions": len(study_hub.get("vivaSessions") or []),
            "forks": len(study_hub.get("forks") or []),
            "arenas": len(study_hub.get("arenas") or []),
            "derivations": len(study_hub.get("derivations") or []),
            "trackers": len(study_hub.get("chapterTrackers") or []),
            "learnerProfile": 1,
        },
        "ledgerIntegrity": ev_summary.get("ledgerIntegrity") or {"ok": True, "count": 10, "brokenAt": None},
    }

    # Compute deterministic v3.2 Exam-Day Readiness summary (sanitized: never echoes sealed fork secrets)
    rd_raw = compute_readiness(ws, today=DEMO_NOW, kg_dir=kg, chapters_dir=ch)
    shield_raw = rd_raw.get("preMortemShield") if isinstance(rd_raw, dict) else None
    sealed_snips: list[str] = []
    for fk in load_jsonl(kg / "forks.jsonl"):
        if not isinstance(fk, dict):
            continue
        if str(fk.get("status") or "").lower() in ("mastered", "cleared", "archived"):
            continue
        for sk in ("sealedWinningMove", "sealedBlunder", "refutationMsg"):
            val = str(fk.get(sk) or "").strip()
            if val:
                sealed_snips.append(val)
                if len(val) > 107:
                    sealed_snips.append(val[:107])
    sanitized_shield: dict[str, Any] = (
        dict(shield_raw)
        if isinstance(shield_raw, dict)
        else {"ok": True, "sourceBasis": "cold_start_minimal", "count": 0, "bullets": []}
    )
    if isinstance(sanitized_shield.get("bullets"), list):
        clean_bullets: list[dict[str, Any]] = []
        for b in sanitized_shield["bullets"]:
            if not isinstance(b, dict):
                continue
            nb = dict(b)
            is_unresolved_fork = str(nb.get("sourceKind") or "") == "blunder_fork"
            if is_unresolved_fork or any(sn and sn in str(nb.get("thenAction") or "") for sn in sealed_snips):
                nb["thenAction"] = "THEN verify boundary conditions & invariant before executing your first move."
            if any(sn and sn in str(nb.get("ifCue") or "") for sn in sealed_snips):
                nb["ifCue"] = "IF you encounter this unresolved Blunder Fork setup"
            clean_bullets.append(nb)
        sanitized_shield["bullets"] = clean_bullets
        sanitized_shield["count"] = len(clean_bullets)

    readiness_summary: dict[str, Any] = {
        "ok": bool(rd_raw.get("ok", True)),
        "readinessIndex": rd_raw.get("readinessIndex", 0.0),
        "band": rd_raw.get("band", "building"),
        "predictedScorePct": rd_raw.get("predictedScorePct") or {"low": 0.0, "expected": 0.0, "high": 0.0},
        "predictedMarks": rd_raw.get("predictedMarks"),
        "examDate": rd_raw.get("examDate") or "2026-12-06",
        "examDateSource": str(rd_raw.get("examDateSource") or "study-config.json"),
        "daysToExam": rd_raw.get("daysToExam", 10.0),
        "hasExamConfig": True,
        "taperMode": rd_raw.get("taperMode") or {"active": False, "phase": "none", "allowNewConcepts": True},
        "preMortemShield": sanitized_shield,
        "topLevers": list(rd_raw.get("topLevers") or [])[:3],
        "pillars": rd_raw.get("pillars") or {},
        "executionConfidence": rd_raw.get("executionConfidence", "observed"),
        "uncertaintyReasons": list(rd_raw.get("uncertaintyReasons") or []),
    }

    interleave_stats: dict[str, Any] = {
        "ok": True,
        "totalSessions": 2,
        "completedSessions": 2,
        "totalItemsChecked": 12,
        "tier1Correct": 9,
        "tier1AccuracyPct": 75.0,
        "principleAccuracyPct": 75.0,
        "step2Correct": 10,
        "step2Total": 12,
        "step2AccuracyPct": 83.3,
        "medianIdentifyMs": 11400,
        "medianIdentifySec": 11.4,
        "lastSessionAt": "2026-11-24T16:20:00Z",
        "daysSinceLastInterleave": 2,
        "distinctChaptersPracticed": 3,
        "confusionMatrix": {},
        "topConfusionPairs": [
            {"trueChapter": "Simple Harmonic Motion", "confusedWithChapter": "AC Circuits & Resonance", "count": 2},
            {"trueChapter": "Raft Consensus", "confusedWithChapter": "Transformers & Attention", "count": 1},
        ],
    }

    compat_summary: dict[str, Any] = {
        "totalHarnesses": len(ALL_HARNESSES),
        "detectedCount": 4,
        "configuredCount": 3,
        "attachedCount": 3,
        "httpSupportedCount": sum(1 for h in HARNESS_SPECS.values() if h.get("httpMcpSupported")),
        "activeHarness": "antigravity",
        "mcpHttpEndpoint": "http://127.0.0.1:4173/mcp",
        "mcpHttpEnabled": True,
    }

    # Zero-loss compaction (#268): drop redundant precomputed fields safely recomputed/unused by dashboard.html
    prof.pop("recentEvents", None)
    if isinstance(ev_summary, dict) and isinstance(ev_summary.get("propositions"), dict):
        ev_summary = dict(ev_summary)
        ev_summary["propositions"] = {
            pid: {
                "tier": rec.get("tier", 1),
                "tierLabel": rec.get("tierLabel", ""),
                "sources": rec.get("sources") or [],
            }
            for pid, rec in ev_summary["propositions"].items()
            if isinstance(rec, dict)
        }

    # Drop redundant normalizedEdges (dashboard.html & viewer.html derive edges from chains in <1ms)
    study_graph.pop("normalizedEdges", None)

    study_hub = _normalize_mtimes(study_hub)
    study_hub["schemaVersion"] = 5
    study_hub["generatedAt"] = DEMO_NOW_ISO
    study_hub["today"] = DEMO_TODAY
    study_hub["workspaceName"] = DEMO_WORKSPACE_NAME
    study_hub["learnerProfile"] = prof
    study_hub["nextMove"] = next_move
    study_hub["evidenceSummary"] = ev_summary
    study_hub["explorables"] = explorables_map
    study_hub["rosetta"] = rosetta_payload
    study_hub["syncStatus"] = sync_status_payload
    study_hub["readinessSummary"] = readiness_summary
    study_hub["interleaveStats"] = interleave_stats
    study_hub["compatSummary"] = compat_summary
    study_hub["rootCauseHotspots"] = {
        "byProp": {k: round(float(v), 4) for k, v in sorted(by_prop.items())},
        "blastRadius": blast,
    }
    study_hub["triageRecommended"] = False
    study_hub["reentry"] = None
    if isinstance(study_hub.get("retentionHeatmap"), dict):
        hm = study_hub["retentionHeatmap"]
        hm.pop("byDate", None)
        days_list = hm.pop("days", None)
        if isinstance(days_list, list) and days_list:
            hm["activeCompact"] = [
                [d["date"], int(d.get("count") or 0), int(d.get("reviews") or 0), int(d.get("exams") or 0), int(d.get("level") or 0)]
                for d in days_list
                if int(d.get("count") or 0) > 0
            ]
    # Zero-loss deduplication: dashboard.html recomputes mistakeStats via getMistakeStatsSafe(),
    # resolves activeChapterTracker from chapterTrackers, and derives dueItems/forksDue on boot.
    study_hub.pop("forksDue", None)
    study_hub.pop("mistakeStats", None)
    study_hub.pop("dueItems", None)
    study_hub.pop("activeChapterTracker", None)
    study_hub["stats"]["verifiedTransferPct"] = ev_summary.get("verifiedTransferPct", 0.0)
    study_hub["stats"]["explorablesCount"] = sum(len(v) for v in explorables_map.values())
    study_hub["stats"]["rosettaChordsCount"] = rosetta_payload["chordCount"]
    study_hub["stats"]["readinessIndex"] = readiness_summary["readinessIndex"]
    study_hub["stats"]["interleaveSessionsCount"] = interleave_stats["totalSessions"]

    # Strict zero-sealed-secret assertion on public static payloads
    leaks = _scan_forbidden_keys(study_graph, "graph.") + _scan_forbidden_keys(study_hub, "hub.")
    if leaks:
        raise RuntimeError(f"Sealed secret leak detected in static payload: {leaks}")

    return study_graph, study_hub


# ---------------------------------------------------------------------------
# Phase D: Compact, Zero-CDN, Zero-Eval In-Browser Studio Shim
# ---------------------------------------------------------------------------

DEMO_RESET_CSS = """
  .demo-reset-pill {
    display: inline-flex; align-items: center; gap: 6px;
    padding: 4px 10px; border-radius: 999px; font-size: 11px; font-weight: 600;
    background: var(--accent-bg, rgba(122,162,247,0.14));
    border: 1px solid var(--border-strong, #3a404c);
    color: var(--text, #eae7df); cursor: pointer; white-space: nowrap;
  }
  .demo-reset-pill:hover { border-color: var(--accent, #7aa2f7); }
  #readerOpenLink { display: none !important; }
"""

VIEWER_DEMO_COMPACT_CSS = """
  @media (max-width: 1380px) {
    #countPill { display: none; }
    .controls { gap: 4px; }
    .controls select { max-width: 90px; min-width: 66px; flex: 0 1 86px; }
    .controls input[type="search"], #search, #searchInput { min-width: 80px; flex: 0 1 104px; }
    .demo-reset-pill { padding: 3px 8px; font-size: 11px; }
    .stage-hud { right: 14px; max-width: calc(100% - 28px); gap: 6px; }
    .hud-group { padding: 4px 6px; gap: 3px; font-size: 10.5px; }
    .legend-item { gap: 4px; padding: 0 4px; white-space: nowrap; }
  }
"""

SHIM_JS = r"""
(() => {
  "use strict";
  const KEY = "syntopik_live_demo_v32_state";
  const VER = 5;
  const clone = (o) => JSON.parse(JSON.stringify(o));
  const normTxt = (s) => String(s || "").toLowerCase().replace(/\s+/g, "").replace(/[−–—]/g, "-");
  const normEq = (s) => normTxt(String(s || "").replace(/^line\s*\d+\s*[:.)-]\s*/i, "")).replace(/\^/g, "**").replace(/[·×]/g, "*");
  const todayIso = () => (window.__STUDY_HUB__ && window.__STUDY_HUB__.today) || "2026-11-26";
  const addDays = (iso, d) => {
    const t = Date.parse(iso + "T12:00:00Z");
    return new Date((Number.isFinite(t) ? t : Date.now()) + d * 86400000).toISOString().slice(0, 10);
  };

  // Hydrate compact retentionHeatmap & forksDue onto window.__STUDY_HUB__ synchronously before dashboard init
  (() => {
    const h = window.__STUDY_HUB__;
    if (!h || typeof h !== "object") return;
    if (h.retentionHeatmap && !Array.isArray(h.retentionHeatmap.days) && h.retentionHeatmap.startDate) {
      const hm = h.retentionHeatmap;
      const byD = new Map((hm.activeCompact || []).map((r) => [r[0], { date: r[0], count: r[1], reviews: r[2], exams: r[3], level: r[4] }]));
      const win = Math.max(1, Number(hm.windowDays || 365));
      const days = [];
      for (let i = 0; i < win; i++) {
        const iso = addDays(hm.startDate, i);
        days.push(byD.get(iso) || { date: iso, count: 0, reviews: 0, exams: 0, level: 0 });
      }
      hm.days = days;
      delete hm.activeCompact;
    }
    if (!Array.isArray(h.forksDue) && Array.isArray(h.forks)) {
      h.forksDue = h.forks.filter((f) => f && f.isDue !== false);
    }
  })();

  // Pure-JS FIPS 180-4 SHA-256 (synchronous, works on file:// and https:// for fail-closed CRDT sync tamper detection)
  const SHA_K = [
    0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,
    0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,
    0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,
    0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,
    0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,
    0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,
    0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,
    0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2
  ];
  function sha256Hex(str) {
    const bytes = new TextEncoder().encode(String(str));
    const len = bytes.length, bitLen = len * 8;
    const padLen = ((len + 9 + 63) >> 6) << 6;
    const buf = new Uint8Array(padLen);
    buf.set(bytes);
    buf[len] = 0x80;
    const dv = new DataView(buf.buffer);
    dv.setUint32(padLen - 8, Math.floor(bitLen / 0x100000000), false);
    dv.setUint32(padLen - 4, bitLen >>> 0, false);
    let h0=0x6a09e667,h1=0xbb67ae85,h2=0x3c6ef372,h3=0xa54ff53a,h4=0x510e527f,h5=0x9b05688c,h6=0x1f83d9ab,h7=0x5be0cd19;
    const w = new Uint32Array(64);
    const rr = (x, n) => (x >>> n) | (x << (32 - n));
    for (let o = 0; o < padLen; o += 64) {
      for (let i = 0; i < 16; i++) w[i] = dv.getUint32(o + i * 4, false);
      for (let i = 16; i < 64; i++) {
        const s0 = rr(w[i-15], 7) ^ rr(w[i-15], 18) ^ (w[i-15] >>> 3);
        const s1 = rr(w[i-2], 17) ^ rr(w[i-2], 19) ^ (w[i-2] >>> 10);
        w[i] = (w[i-16] + s0 + w[i-7] + s1) >>> 0;
      }
      let a=h0,b=h1,c=h2,d=h3,e=h4,f=h5,g=h6,h=h7;
      for (let i = 0; i < 64; i++) {
        const t1 = (h + (rr(e,6)^rr(e,11)^rr(e,25)) + ((e&f)^(~e&g)) + SHA_K[i] + w[i]) >>> 0;
        const t2 = ((rr(a,2)^rr(a,13)^rr(a,22)) + ((a&b)^(a&c)^(b&c))) >>> 0;
        h=g; g=f; f=e; e=(d+t1)>>>0; d=c; c=b; b=a; a=(t1+t2)>>>0;
      }
      h0=(h0+a)>>>0; h1=(h1+b)>>>0; h2=(h2+c)>>>0; h3=(h3+d)>>>0;
      h4=(h4+e)>>>0; h5=(h5+f)>>>0; h6=(h6+g)>>>0; h7=(h7+h)>>>0;
    }
    return [h0,h1,h2,h3,h4,h5,h6,h7].map((x) => x.toString(16).padStart(8, "0")).join("");
  }
  function canonicalJson(val) {
    if (val === null || typeof val !== "object") return JSON.stringify(val);
    if (Array.isArray(val)) return "[" + val.map(canonicalJson).join(",") + "]";
    const keys = Object.keys(val).sort();
    return "{" + keys.map((k) => JSON.stringify(k) + ":" + canonicalJson(val[k])).join(",") + "}";
  }

  // Closure-scoped demo grading rules (never exposed on window.__STUDY_HUB__ / window.__STUDY_GRAPH__)
  const WB_REVEALS = {
    "wb-f2": "**No — committing prior-term entries purely by replica count violates Leader Completeness (Raft Figure 8).** A server holding a Term-3 entry at the same index can still win Term 5 over the majority that only holds Term 2, then overwrite the 'committed' entry. Raft only commits entries from `currentTerm` by counting replicas; older entries commit indirectly via Log Matching."
  };
  const FORK_RULES = {
    "fork-raft-uptodate": {
      ok: ["return(cand_term,cand_len)>=(voter_term,voter_len)", "(cand_term,cand_len)>=(voter_term,voter_len)", "returncand_term>voter_termor(cand_term==voter_termandcand_len>=voter_len)", "cand_term>voter_termor(cand_term==voter_termandcand_len>=voter_len)"],
      trap: ["returncand_len>=voter_len", "cand_len>=voter_len"],
      fix: "return (cand_term, cand_len) >= (voter_term, voter_len)",
      refute: "Checking length alone lets a stale term-1 log of length 5 beat a committed term-3 log of length 2. Compare last term first, then length."
    },
    "fork-raft-quorum-size": {
      ok: ["2*f+1", "2f+1", "1+2*f", "1+2f"],
      trap: ["2*f", "2f", "f+1"],
      fix: "2*f + 1",
      refute: "With N = 2*f, quorum is f + 1, leaving only f - 1 spare servers — so f failures halt the cluster."
    },
    "fork-shm-series-springs": {
      ok: ["k1*k2/(k1+k2)", "(k1*k2)/(k1+k2)", "k2*k1/(k1+k2)", "1/(1/k1+1/k2)"],
      trap: ["k1+k2", "k2+k1"],
      fix: "k1*k2/(k1 + k2)",
      refute: "k1 + k2 is stiffer than either spring alone — joining springs in series makes the chain softer because both stretch under shared tension F."
    },
    "fork-shm-energy-freq": {
      ok: ["2*omega", "2omega", "2*w", "2w"],
      trap: ["omega", "w", "omega/2"],
      fix: "2*omega",
      refute: "sin^2(omega*t) = (1 - cos(2*omega*t))/2 completes two full oscillations for every single period of x(t)."
    },
    "fork-attn-scale-denom": {
      ok: ["sqrt(d_k)", "d_k**0.5", "d_k^(1/2)", "math.sqrt(d_k)", "sqrt(dk)"],
      trap: ["d_k", "dk", "d_k**2"],
      fix: "sqrt(d_k)",
      refute: "Dividing by d_k scales variance by 1/d_k^2, giving Var = 1/d_k -> 0 and flattening attention into uniform averages."
    },
    "fork-attn-causal-fill": {
      ok: ["-inf", "-infinity", "float('-inf')", 'float("-inf")', "-math.inf", "-np.inf", "-1e9"],
      trap: ["0", "0.0", "1"],
      fix: "-inf",
      refute: "Filling future scores with 0 produces exp(0) = 1, giving future tokens positive attention weight and leaking targets."
    }
  };
  const DERIV_STEPS = {
    "deriv-raft-safety": {
      s1: { expr: "2 * (floor(N / 2) + 1) > N", ok: ["2*(floor(n/2)+1)>n", "|q1capq2|>=1", "q1+q2>n"], factors: ["floor", "n"] },
      s2: { expr: "votes_granted(server, term) <= 1", ok: ["votes_granted(server,term)<=1", "votes(server,term)<=1", "votes<=1", "atmostonevote"], factors: ["1"] },
      s3: { expr: "leaders(term) <= 1", ok: ["leaders(term)<=1", "leaders<=1", "atmostoneleader", "n_leaders<=1"], factors: ["1"] },
      s4: { expr: "same_term_and_index => identical_prefix", ok: ["same_term_and_index=>identical_prefix", "same(index,term)=>identicalprefix", "identical_prefix", "logmatching"], factors: ["prefix"] },
      s5: { expr: "applied(s_a, i) == applied(s_b, i)", ok: ["applied(s_a,i)==applied(s_b,i)", "applied(a,i)==applied(b,i)"], factors: ["applied"] }
    },
    "deriv-shm-compound-pendulum": {
      s1: { expr: "I * alpha = -m * g * d * sin(theta)", ok: ["i*alpha=-m*g*d*sin(theta)", "i*alpha=-mgdsin(theta)", "tau=-m*g*d*sin(theta)", "i*a=-m*g*d*sin(theta)"], signFlip: ["i*alpha=m*g*d*sin(theta)", "i*alpha=+m*g*d*sin(theta)", "tau=m*g*d*sin(theta)"], factors: ["i", "m", "g", "d", "sin"] },
      s2: { expr: "sin(theta) = theta", ok: ["sin(theta)=theta", "sin(theta)~theta", "sin(theta)≈theta", "theta=sin(theta)"], factors: ["theta"] },
      s3: { expr: "alpha = -(m * g * d / I) * theta", ok: ["alpha=-(m*g*d/i)*theta", "alpha=-(mgd/i)*theta", "alpha=-(m*g*d/i)theta", "alpha=-m*g*d*theta/i", "alpha=-(m*g*d)*theta/i"], signFlip: ["alpha=(m*g*d/i)*theta", "alpha=+(m*g*d/i)*theta", "alpha=m*g*d*theta/i", "alpha=(mgd/i)*theta"], factors: ["m", "g", "d", "i", "theta"] },
      s4: { expr: "omega = sqrt(m * g * d / I)", ok: ["omega=sqrt(m*g*d/i)", "omega=sqrt(mgd/i)", "sqrt(m*g*d/i)", "w=sqrt(m*g*d/i)", "omega**2=m*g*d/i"], factors: ["sqrt", "m", "g", "d", "i"] },
      s5: { expr: "T = 2 * pi * sqrt(I / (m * g * d))", ok: ["t=2*pi*sqrt(i/(m*g*d))", "t=2*pi*sqrt(i/(mgd))", "2*pi*sqrt(i/(m*g*d))", "t=2pi*sqrt(i/(m*g*d))"], factors: ["2", "pi", "sqrt", "i"] }
    },
    "deriv-attn-variance-scaling": {
      s1: { expr: "E[q_i] = 0, Var(q_i) = 1, E[k_i] = 0, Var(k_i) = 1", ok: ["e[q_i]=0,var(q_i)=1,e[k_i]=0,var(k_i)=1", "var(q_i)=1,var(k_i)=1", "e[q_i]=0,var(q_i)=1"], factors: ["var"] },
      s2: { expr: "Var(q_i * k_i) = Var(q_i) * Var(k_i) = 1", ok: ["var(q_i*k_i)=var(q_i)*var(k_i)=1", "var(q_i*k_i)=1", "1"], factors: ["1"] },
      s3: { expr: "Var(q . k) = d_k", ok: ["var(q.k)=d_k", "var(q*k)=d_k", "d_k", "var(qk)=d_k"], factors: ["d_k"] },
      s4: { expr: "Var((q . k) / c) = d_k / c**2", ok: ["var((q.k)/c)=d_k/c**2", "var((q*k)/c)=d_k/c**2", "d_k/c**2"], factors: ["d_k", "c**2"] },
      s5: { expr: "c = sqrt(d_k)", ok: ["c=sqrt(d_k)", "sqrt(d_k)", "c=d_k**0.5"], factors: ["sqrt", "d_k"] }
    }
  };
  const ROSETTA_ARCHS = {
    second_order_oscillator: { name: "Second-Order Linear Oscillator & Damping", eq: "m x'' + b x' + k x = F(t)  <=>  x'' + 2*gamma*x' + omega_0^2*x = f(t)", breaks: ["Large-amplitude mechanical oscillations become nonlinear and break superposition.", "Active circuits or saturated magnetic cores break constant-coefficient L, R, C linearity."] },
    boltzmann_softmax: { name: "Boltzmann-Gibbs Exponential Distribution & Temperature Scaling", eq: "p_i = exp(z_i / tau) / sum_j exp(z_j / tau)  <=>  p_i = exp(-E_i / k_B T) / Z", breaks: ["Attention logits carry +z_i whereas Boltzmann weights carry -E_i (sign flip).", "Softmax is shift-invariant whereas physical free energy F shifts with reference zero."] },
    quorum_intersection: { name: "Pigeonhole Quorum Intersection Invariant", eq: "|Q_1| + |Q_2| > N  =>  |Q_1 cap Q_2| >= 1", breaks: ["Crash-fault majorities (2f+1) break under even a single Byzantine equivocator (needs 3f+1)."] },
    variance_normalization: { name: "Independent Sum Variance Growth & 1/sqrt(N) Normalization", eq: "Var(sum X_i) = N * sigma^2  =>  scale by 1 / sqrt(N)", breaks: ["Positively correlated summands grow variance as O(N^2), breaking 1/sqrt(N) normalization."] }
  };

  function evalCausalMatrix(scores, mode) {
    const n = scores.length, out = [];
    for (let i = 0; i < n; i++) {
      let row;
      if (mode === "ref") {
        const sub = scores[i].slice(0, i + 1), mx = Math.max(...sub);
        const ex = sub.map((x) => Math.exp(x - mx)), s = ex.reduce((a, b) => a + b, 0);
        row = ex.map((e) => Math.round((e / s) * 1e6) / 1e6).concat(Array(n - i - 1).fill(0));
      } else if (mode === "m1") {
        const m = scores[i].map((v, j) => (j <= i ? v : 0)), mx = Math.max(...m);
        const ex = m.map((x) => Math.exp(x - mx)), s = ex.reduce((a, b) => a + b, 0);
        row = ex.map((e) => Math.round((e / s) * 1e6) / 1e6);
      } else if (mode === "m2") {
        const mx = Math.max(...scores[i]), ex = scores[i].map((x) => Math.exp(x - mx)), s = ex.reduce((a, b) => a + b, 0);
        row = ex.map((e, j) => (j <= i ? Math.round((e / s) * 1e6) / 1e6 : 0));
      } else {
        const sub = scores[i].slice(0, i);
        if (!sub.length) { row = Array(n).fill(0); }
        else {
          const mx = Math.max(...sub), ex = sub.map((x) => Math.exp(x - mx)), s = ex.reduce((a, b) => a + b, 0);
          row = ex.map((e) => Math.round((e / s) * 1e6) / 1e6).concat(Array(n - i).fill(0));
        }
      }
      out.push(row);
    }
    return out;
  }

  function runArenaEval(arenaId, rawInput) {
    let val = rawInput;
    if (typeof val === "string") {
      try { val = JSON.parse(val); } catch { /* leave as string */ }
    }
    if (arenaId === "arena-raft-uptodate-check") {
      const a = val && typeof val === "object" && Array.isArray(val.args) ? val.args : val;
      if (!Array.isArray(a) || a.length !== 4 || !a.every((x) => Number.isInteger(x) && x >= 0)) {
        return { valid: false, failed: ["Expected 4 non-negative integers [cand_term, cand_idx, voter_term, voter_idx]."] };
      }
      const [ct, ci, vt, vi] = a;
      const ref = ct !== vt ? ct > vt : ci >= vi;
      const muts = [
        ["m1", "length_before_term", ci !== vi ? ci > vi : ct >= vt],
        ["m2", "strict_gt_on_equal_term", ct !== vt ? ct > vt : ci > vi],
        ["m3", "ignore_index_on_tie", ct >= vt]
      ];
      return { valid: true, hits: muts.filter((m) => m[2] !== ref).map((m) => ({ id: m[0], tag: m[1] })) };
    }
    if (arenaId === "arena-shm-series-stiffness") {
      const k1 = Array.isArray(val) ? Number(val[0]) : Number(val && val.k1);
      const k2 = Array.isArray(val) ? Number(val[1]) : Number(val && val.k2);
      if (!Number.isFinite(k1) || !Number.isFinite(k2) || k1 <= 0 || k2 <= 0) {
        return { valid: false, failed: ["Requires k1 > 0 and k2 > 0, e.g. {\"k1\": 2, \"k2\": 6}."] };
      }
      const ref = (k1 * k2) / (k1 + k2);
      const muts = [
        ["m1", "parallel_sum_confusion", k1 + k2],
        ["m2", "symmetric_halving_shortcut", k1 / 2],
        ["m3", "harmonic_mean_factor2", (2 * k1 * k2) / (k1 + k2)]
      ];
      return { valid: true, hits: muts.filter((m) => Math.abs(m[2] - ref) > 1e-6).map((m) => ({ id: m[0], tag: m[1] })) };
    }
    if (arenaId === "arena-attn-causal-softmax") {
      const m = val && typeof val === "object" && Array.isArray(val.args) && Array.isArray(val.args[0]) ? val.args[0] : val;
      if (!Array.isArray(m) || m.length < 1 || !m.every((r) => Array.isArray(r) && r.length === m.length && r.every((x) => typeof x === "number" && Number.isFinite(x)))) {
        return { valid: false, failed: ["Expected square N x N numeric matrix, e.g. [[1, 2], [0, 1]]."] };
      }
      const ref = JSON.stringify(evalCausalMatrix(m, "ref"));
      const muts = [
        ["m1", "zero_mask_before_softmax", JSON.stringify(evalCausalMatrix(m, "m1"))],
        ["m2", "post_softmax_zero_no_renorm", JSON.stringify(evalCausalMatrix(m, "m2"))],
        ["m3", "strict_lt_blocks_self_diagonal", JSON.stringify(evalCausalMatrix(m, "m3"))]
      ];
      return { valid: true, hits: muts.filter((x) => x[2] !== ref).map((x) => ({ id: x[0], tag: x[1] })) };
    }
    return { valid: false, failed: ["Unknown demo arena."] };
  }

  function initialState() {
    return { v: VER, graph: clone(window.__STUDY_GRAPH__ || { propositions: [], chains: [] }), hub: clone(window.__STUDY_HUB__ || {}) };
  }
  function loadState() {
    try {
      const raw = localStorage.getItem(KEY);
      if (raw) {
        const parsed = JSON.parse(raw);
        if (parsed && parsed.v === VER && parsed.graph && parsed.hub) return parsed;
      }
    } catch { /* ignore storage restrictions */ }
    return initialState();
  }
  function saveState(st) {
    try { localStorage.setItem(KEY, JSON.stringify(st)); } catch { /* ignore quota */ }
  }

  let state = loadState();
  const blitzStore = new Map();
  const vivaStore = new Map();
  const interleaveStore = new Map();

  function recomputePolicy(lp, energy) {
    const base = clone(lp.compiledPolicy || { domain: "Distributed Systems", domainElo: 1560, expertiseBand: "intermediate", scaffoldingMode: "faded", allowWorkedExample: false, allowProductiveFailure: true, maxProseWords: 135, targetSuccessRate: 0.85, affectState: "flow", activeGuardrails: [] });
    const cal = lp.calibration || {};
    const ov = lp.userOverrides || {};
    const wordsMap = { terse: 105, standard: 135, rich: 155 };
    const targMap = { guided: 0.88, balanced: 0.85, adversarial: 0.78 };
    base.maxProseWords = wordsMap[cal.proseDensity] || 135;
    base.targetSuccessRate = targMap[cal.challengeAppetite] || 0.85;
    base.scaffoldingMode = cal.challengeAppetite === "adversarial" ? "challenge_transfer" : cal.challengeAppetite === "guided" ? "worked_example_parsons" : "faded";
    base.allowProductiveFailure = cal.challengeAppetite !== "guided";
    base.energyOverride = energy && energy !== "normal" ? energy : null;
    if (energy === "low") { base.maxProseWords = Math.min(base.maxProseWords, 105); base.allowProductiveFailure = false; base.activeGuardrails = ["low_energy_warm_start"]; }
    else if (energy === "deep") { base.maxProseWords = Math.min(155, Math.max(base.maxProseWords, 145)); base.allowProductiveFailure = true; base.activeGuardrails = ["deep_focus_stretch"]; }
    else { base.activeGuardrails = []; }
    if (ov.scaffoldingMode) base.scaffoldingMode = ov.scaffoldingMode;
    if (typeof ov.allowProductiveFailure === "boolean") base.allowProductiveFailure = ov.allowProductiveFailure;
    if (ov.maxProseWords) base.maxProseWords = Math.min(155, Number(ov.maxProseWords));
    base.allowWorkedExample = base.scaffoldingMode === "worked_example_parsons";
    lp.compiledPolicy = base;
    return base;
  }

  function refreshDerived(st) {
    const props = st.graph.propositions || [];
    const tdy = st.hub.today || todayIso();
    const counts = { solid: 0, shaky: 0, misconception: 0, planned: 0 };
    const due = [];
    for (const p of props) {
      if (p.status in counts) counts[p.status] += 1;
      const nr = String(p.nextReview || (p.fsrs && p.fsrs.due) || "").slice(0, 10);
      if (p.status === "misconception" || p.status === "shaky" || (nr && nr <= tdy)) {
        due.push({ id: p.id, title: p.title || p.id, subject: p.subject, chapter: p.chapter, status: p.status, nextReview: nr || tdy });
      }
    }
    st.hub.dueItems = due;
    st.hub.stats = { ...(st.hub.stats || {}), propositions: props.length, solid: counts.solid, shaky: counts.shaky, misconception: counts.misconception, planned: counts.planned, dueCount: due.length };
    const forks = st.hub.forks || [];
    st.hub.forksDue = forks.filter((f) => f && f.isDue !== false);
    st.hub.forksDueCount = st.hub.forksDue.length;
    st.hub.arenasCount = (st.hub.arenas || []).length;
    const ev = st.hub.evidenceSummary;
    if (ev && ev.propositions) {
      const tc = { T1: 0, T2: 0, T3: 0, T4: 0 };
      const vals = Object.values(ev.propositions);
      for (const e of vals) { const t = Math.max(1, Math.min(4, Number(e.tier || 1))); tc["T" + t] += 1; }
      ev.tierCounts = tc;
      ev.verifiedTransferPct = vals.length ? Number((((tc.T3 + tc.T4) / vals.length) * 100).toFixed(1)) : 0;
      st.hub.stats.verifiedTransferPct = ev.verifiedTransferPct;
    }
  }

  function promoteEvidence(st, propId, tier, src) {
    const ev = st.hub.evidenceSummary;
    if (!ev || !ev.propositions || !propId || !ev.propositions[propId]) return false;
    const rec = ev.propositions[propId];
    const labels = { 1: "T1 Exposure", 2: "T2 Cold Recall", 3: "T3 Mechanistic Transfer", 4: "T4 Adversarial Defense" };
    if (tier > Number(rec.tier || 1)) { rec.tier = tier; rec.tierLabel = labels[tier]; }
    if (!Array.isArray(rec.sources)) rec.sources = [];
    if (src && !rec.sources.includes(src)) rec.sources.push(src);
    return true;
  }

  function buildStateResponse(energy) {
    state = loadState();
    if (state.hub.learnerProfile) recomputePolicy(state.hub.learnerProfile, energy);
    refreshDerived(state);
    return {
      ok: true, schemaVersion: 5, demo: true, energyMode: energy || "normal",
      graph: state.graph, hub: state.hub,
      forksDue: state.hub.forksDue, forksDueCount: state.hub.forksDueCount, arenasCount: state.hub.arenasCount,
      reentry: state.hub.reentry || null, triageRecommended: Boolean(state.hub.triageRecommended),
      learnerProfile: state.hub.learnerProfile, nextMove: state.hub.nextMove,
      forks: state.hub.forks, arenas: state.hub.arenas, decisions: state.hub.decisions,
      derivations: state.hub.derivations, vivaSessions: state.hub.vivaSessions,
      evidenceSummary: state.hub.evidenceSummary, rootCauseHotspots: state.hub.rootCauseHotspots,
      whiteboard: state.hub.whiteboard, explorables: state.hub.explorables, starterPacks: state.hub.starterPacks,
      rosetta: state.hub.rosetta, syncStatus: state.hub.syncStatus,
      readinessSummary: state.hub.readinessSummary, interleaveStats: state.hub.interleaveStats, compatSummary: state.hub.compatSummary
    };
  }

  function handleReview(body) {
    state = loadState();
    const pid = String(body.propId || body.id || "").trim();
    const rating = String(body.rating || "good").toLowerCase();
    const props = state.graph.propositions || [];
    const node = props.find((p) => p.id === pid);
    if (!node) return { ok: false, error: "Proposition not found: " + pid };
    const tdy = state.hub.today || todayIso();
    const mult = { again: 0.45, hard: 1.2, good: 2.05, easy: 2.8 }[rating] || 2.0;
    const prevS = Number((node.fsrs && node.fsrs.stability) || node.stabilityDays || 2.0);
    const newS = Number(Math.max(0.5, prevS * mult).toFixed(2));
    const ivl = rating === "again" ? 1 : Math.max(2, Math.round(newS));
    const nextDue = addDays(tdy, ivl);
    const ok = rating === "good" || rating === "easy";
    const prior = Number(node.bktMastery || node.confidence || 0.5);
    const pT = { again: 0.05, hard: 0.1, good: 0.25, easy: 0.35 }[rating] || 0.25;
    const num = prior * (ok ? 0.9 : 0.1), den = num + (1 - prior) * (ok ? 0.2 : 0.8);
    const post = den ? num / den : prior;
    const bkt = Number(Math.max(0.05, Math.min(0.99, post + (1 - post) * pT)).toFixed(3));
    node.bktMastery = bkt;
    node.confidence = bkt;
    node.status = rating === "again" ? "shaky" : (bkt >= 0.78 && ok ? "solid" : (node.status === "planned" ? "shaky" : node.status));
    node.lastReviewed = tdy;
    node.nextReview = nextDue;
    node.stabilityDays = newS;
    node.reps = Number(node.reps || 0) + 1;
    if (rating === "again") node.lapses = Number(node.lapses || 0) + 1;
    node.fsrs = { ...(node.fsrs || {}), stability: newS, difficulty: Number(Math.max(1, Math.min(10, Number((node.fsrs && node.fsrs.difficulty) || 5) + (rating === "again" ? 0.6 : rating === "easy" ? -0.35 : -0.1))).toFixed(2)), due: nextDue + "T12:00:00+00:00", last_review: tdy + "T12:00:00+00:00", reps: node.reps, lapses: node.lapses || 0 };

    // True FIRe upstream ancestor refresh + 0.25 cross-domain Rosetta FIRe on Good / Easy
    const NON_CAUSAL = new Set(["isomorphic_to", "contrasts_with", "contrasts", "contrast", "analogous", "contradicts", "unrelated"]);
    const fireCredits = [];
    if (ok) {
      const seen = new Set([pid]);
      const queue = [{ id: pid, depth: 0 }];
      while (queue.length) {
        const cur = queue.shift();
        if (cur.depth >= 2) continue;
        for (const ch of (state.graph.chains || [])) {
          if (NON_CAUSAL.has(String(ch.relation || "").toLowerCase())) continue;
          if (ch.conclusion !== cur.id) continue;
          const w = Number(ch.encompassWeight || 0.45);
          for (const prem of (ch.premises || [])) {
            if (seen.has(prem)) continue;
            seen.add(prem);
            const pn = props.find((x) => x.id === prem);
            if (pn && pn.status !== "planned") {
              const pStab = Number((pn.fsrs && pn.fsrs.stability) || pn.stabilityDays || 4.0);
              const boosted = Number((pStab * (1 + 0.18 * w)).toFixed(2));
              pn.stabilityDays = boosted;
              pn.implicitCredit = Number(((pn.implicitCredit || 0) + 0.4 * w).toFixed(2));
              if (pn.fsrs) pn.fsrs.stability = boosted;
              const pDue = addDays(tdy, Math.max(3, Math.round(boosted * 0.5)));
              if (!pn.nextReview || pn.nextReview <= tdy) {
                pn.nextReview = pDue;
                if (pn.fsrs) pn.fsrs.due = pDue + "T12:00:00+00:00";
              }
              fireCredits.push({ propId: prem, title: pn.title || prem, credit: Number((0.4 * w).toFixed(2)), newStability: boosted });
              queue.push({ id: prem, depth: cur.depth + 1 });
            }
          }
        }
      }
      for (const ch of (state.graph.chains || [])) {
        if (String(ch.relation || "").toLowerCase() !== "isomorphic_to") continue;
        const a = ch.from || (Array.isArray(ch.premises) && ch.premises[0]);
        const b = ch.to || ch.conclusion;
        const twinId = a === pid ? b : (b === pid ? a : null);
        if (!twinId || seen.has(twinId)) continue;
        seen.add(twinId);
        const tn = props.find((x) => x.id === twinId);
        if (tn && tn.status !== "planned" && tn.status !== "misconception") {
          const w = Math.min(0.3, Math.max(0.05, Number(ch.encompassWeight || 0.25)));
          const pStab = Number((tn.fsrs && tn.fsrs.stability) || tn.stabilityDays || 4.0);
          const boosted = Number((pStab * (1 + 0.14 * w)).toFixed(2));
          tn.stabilityDays = boosted;
          tn.implicitCredit = Number(((tn.implicitCredit || 0) + 0.25 * w).toFixed(2));
          if (tn.fsrs) tn.fsrs.stability = boosted;
          fireCredits.push({ propId: twinId, title: tn.title || twinId, credit: Number((0.25 * w).toFixed(2)), newStability: boosted, isomorphic: true });
        }
      }
    }
    if (ok && ivl >= 7) promoteEvidence(state, pid, 2, "cold_recall");
    const hm = state.hub.retentionHeatmap;
    if (hm && Array.isArray(hm.days) && hm.days.length) {
      const last = hm.days[hm.days.length - 1];
      last.count = Number(last.count || 0) + 1;
      last.reviews = Number(last.reviews || 0) + 1;
      last.level = Math.min(4, Math.max(1, last.count));
      hm.totalEvents = Number(hm.totalEvents || 0) + 1;
    }
    refreshDerived(state);
    saveState(state);
    const fireSummary = fireCredits.length ? `FIRe refreshed ${fireCredits.length} prerequisite${fireCredits.length === 1 ? "" : "s"}` : "";
    return { ok: true, node, result: { propId: pid, rating, nextReview: nextDue, intervalDays: ivl, bktMastery: bkt, fireCredits }, fireCredits, fireRefreshedCount: fireCredits.length, fireSummary };
  }

  function handleRootCause(nodeId) {
    state = loadState();
    const NON_CAUSAL = new Set(["isomorphic_to", "contrasts_with", "contrasts", "contrast", "analogous", "contradicts", "unrelated"]);
    const props = state.graph.propositions || [];
    const byId = new Map(props.map((p) => [p.id, p]));
    const incoming = new Map();
    for (const c of (state.graph.chains || [])) {
      if (NON_CAUSAL.has(String(c.relation || "").toLowerCase())) continue;
      if (!incoming.has(c.conclusion)) incoming.set(c.conclusion, []);
      for (const pr of (c.premises || [])) incoming.get(c.conclusion).push(pr);
    }
    const anc = new Set();
    const q = [nodeId];
    while (q.length) {
      const cur = q.shift();
      for (const pr of (incoming.get(cur) || [])) {
        if (!anc.has(pr) && pr !== nodeId) { anc.add(pr); q.push(pr); }
      }
    }
    const suspects = [...anc].map((uid) => {
      const p = byId.get(uid) || {};
      const st = String(p.status || "planned");
      const score = st === "misconception" ? 0.82 : st === "shaky" ? 0.64 : 0.22;
      return { propId: uid, title: p.title || uid, status: st, suspectRootScore: score, expectedInfoGainBits: st === "shaky" ? 0.74 : 0.42 };
    }).sort((a, b) => b.suspectRootScore - a.suspectRootScore);
    const bestProbe = suspects[0] || null;
    const blast = (state.hub.rootCauseHotspots && state.hub.rootCauseHotspots.blastRadius) || [];
    return { ok: true, nodeId, bestProbe, localization: { ok: true, failedNodeId: nodeId, suspects, topSuspect: bestProbe }, diagnosticProbe: { ok: true, bestProbe, candidates: suspects }, blastRadius: blast };
  }

  function handleBlitzBuild(count) {
    state = loadState();
    const props = state.graph.propositions || [];
    const n = Math.max(1, Math.min(props.length || 1, Number(count || 6)));
    const ordered = [...props].sort((a, b) => (a.status === "shaky" || a.status === "misconception" ? 0 : 1) - (b.status === "shaky" || b.status === "misconception" ? 0 : 1));
    const chosen = ordered.slice(0, n);
    const blitzId = "blitz-demo-" + Date.now();
    const keyMap = {};
    const items = chosen.map((p, idx) => {
      const itemId = "item-" + (idx + 1);
      const cue = (p.cues && p.cues[0]) || { q: "Identify the governing invariant for " + p.title, a: p.statement || p.title };
      const other1 = props[(idx + 5) % props.length] || p;
      const other2 = props[(idx + 11) % props.length] || p;
      const kc = (p.boundaryConditions && p.boundaryConditions[0]) || ("Governing premise of " + p.title);
      const fm = "Apply " + p.title + " directly";
      const dc = "Jump to surface shortcut from " + other1.title;
      keyMap[itemId] = { killCue: kc, firstMove: fm, rejectedDecoy: dc, deepPrinciple: p.title };
      return {
        itemId, propId: p.id, questionRef: p.id, prompt: cue.q,
        choices: {
          killCue: [kc, "Assume static equilibrium without checking regime", "Count total nodes ignoring term ordering", "I don't know"],
          firstMove: [fm, "Substitute " + other1.title, "Skip directly to " + other2.title, "I don't know"],
          rejectedDecoy: [dc, "Verify boundary conditions first", "Derive from conservation / invariant", "I don't know"]
        }
      };
    });
    blitzStore.set(blitzId, keyMap);
    return { ok: true, blitzId, count: items.length, items };
  }

  function handleVivaStart(propId) {
    state = loadState();
    const p = (state.graph.propositions || []).find((x) => x.id === propId) || (state.graph.propositions || [])[0];
    const sid = "viva-live-" + Date.now();
    const bc = (p && p.boundaryConditions && p.boundaryConditions[0]) || "the core regime assumptions";
    const prompts = [
      { turn: 1, stage: "commitment_claim", prompt: `State "${p.title}" precisely in your own words, naming the mechanism that makes it hold.` },
      { turn: 2, stage: "boundary_perturbation", prompt: `Boundary perturbation: suppose "${bc}" is violated. Where does your Turn-1 claim break first?` },
      { turn: 3, stage: "assumption_attack", prompt: `Construct a concrete counterexample or failure scenario that refutes a naive shortcut of "${p.title}".` },
      { turn: 4, stage: "transfer_perturbation", prompt: `How does "${p.title}" constrain a downstream theorem or engineering design choice in ${p.chapter || p.subject}?` },
      { turn: 5, stage: "sharpened_synthesis", prompt: `Reconcile your Turn-1 commitment with Turns 2–4 into one caveat-free invariant statement.` }
    ];
    vivaStore.set(sid, { sessionId: sid, propId: p.id, propTitle: p.title, subject: p.subject, chapter: p.chapter, prompts, turns: [], ledger: null });
    return { ok: true, sessionId: sid, propId: p.id, propTitle: p.title, status: "in_progress", currentTurn: 1, totalTurns: 5, prompt: prompts[0], nextPrompt: prompts[0] };
  }

  function handleVivaTurn(body) {
    const sess = vivaStore.get(body.sessionId);
    if (!sess) return { ok: false, error: "Viva session not found — start a new defense." };
    const text = String(body.response || "").trim();
    const conf = typeof body.confidence === "number" ? body.confidence : 0.75;
    const words = text.split(/\s+/).filter(Boolean);
    const low = text.toLowerCase();
    const tNum = sess.turns.length + 1;
    const hasMech = /(because|since|therefore|implies|breaks|fails|requires|invariant|quorum|variance|linear|term|majority|softmax|spring)/.test(low);
    const hasBound = /(unless|only|limit|regime|boundary|condition|assumption|breaks|fails|violated|small|large|partition|zero)/.test(low);
    const clarity = Number(Math.min(0.95, 0.35 + Math.min(0.35, words.length / 28) + (hasMech ? 0.25 : 0)).toFixed(2));
    const boundary_awareness = Number(Math.min(0.95, 0.35 + (hasBound ? 0.45 : 0.15) + Math.min(0.15, words.length / 40)).toFixed(2));
    const transfer = Number(Math.min(0.94, 0.40 + (hasMech && hasBound ? 0.42 : 0.22)).toFixed(2));
    const qScore = (clarity + boundary_awareness + transfer) / 3;
    const calibration = Number(Math.max(0.2, 1 - Math.abs(conf - qScore)).toFixed(2));
    const bluff = conf >= 0.8 && words.length < 5;
    const contradictionCheck = { flagged: bluff, bluffDetected: bluff, contradictions: bluff ? ["High confidence with under-specified defense."] : [] };
    const rubric = { clarity, boundary_awareness, transfer, calibration, composite: Number(((clarity + boundary_awareness + transfer + calibration) / 4).toFixed(2)), contradictionCheck };
    if (tNum === 1) sess.ledger = text;
    sess.turns.push({ turn: tNum, response: text, confidence: conf, rubric });
    const done = tNum >= 5;
    const nextPrompt = done ? null : sess.prompts[tNum];
    let evidenceTier = null;
    if (done) {
      state = loadState();
      const comp = Number((sess.turns.reduce((a, t) => a + t.rubric.composite, 0) / sess.turns.length).toFixed(2));
      const passed = comp >= 0.68;
      if (passed) { promoteEvidence(state, sess.propId, 4, "viva_chamber"); evidenceTier = "T4 Adversarial Defense"; }
      state.hub.vivaSessions = [...(state.hub.vivaSessions || []), { id: sess.sessionId, propId: sess.propId, propTitle: sess.propTitle, status: passed ? "passed" : "completed", outcome: passed ? "passed" : "needs_work", passed, scores: { ...rubric, composite: comp }, turnsCompleted: 5, totalTurns: 5 }];
      refreshDerived(state);
      saveState(state);
    }
    return { ok: true, sessionId: sess.sessionId, propId: sess.propId, propTitle: sess.propTitle, status: done ? "passed" : "in_progress", turnCompleted: tNum, currentTurn: done ? 5 : tNum + 1, totalTurns: 5, turnRubric: rubric, finalRubric: done ? rubric : null, nextPrompt, evidenceTier };
  }

  function handleRosettaGet(u) {
    state = loadState();
    const ros = state.hub.rosetta || { ok: true, chords: [], archetypesMatched: [], chordCount: 0, persistedCount: 0, discoveredCount: 0, archetypesCount: 10 };
    const pid = (u.searchParams.get("propId") || u.searchParams.get("nodeId") || "").trim();
    const out = { ok: true, ...ros };
    if (pid) {
      const neighbors = (ros.chords || []).filter((c) => c.sourceId === pid || c.targetId === pid);
      out.propId = pid;
      out.neighbors = neighbors;
      out.opener = neighbors.length ? `Structural twin via ${neighbors[0].archetypeName || neighbors[0].archetypeId}: map the invariant roles and watch where the analogy breaks.` : null;
    }
    return out;
  }

  function handleRosettaLink(body) {
    state = loadState();
    const src = String(body.sourceId || body.source_id || "").trim();
    const tgt = String(body.targetId || body.target_id || "").trim();
    const aid = String(body.archetypeId || body.archetype_id || "").trim();
    if (!src || !tgt || !aid || src === tgt) return { ok: false, error: "Distinct sourceId, targetId, and archetypeId are required." };
    const [a, b] = src <= tgt ? [src, tgt] : [tgt, src];
    const chains = state.graph.chains || [];
    const exists = chains.some((c) => String(c.relation || "").toLowerCase() === "isomorphic_to" && ((c.from === src && c.to === tgt) || (c.from === tgt && c.to === src) || (Array.isArray(c.premises) && c.premises.length === 1 && ((c.premises[0] === src && c.conclusion === tgt) || (c.premises[0] === tgt && c.conclusion === src)))));
    const props = state.graph.propositions || [];
    const pa = props.find((x) => x.id === a) || { id: a, title: a, subject: "General", chapter: "General" };
    const pb = props.find((x) => x.id === b) || { id: b, title: b, subject: "General", chapter: "General" };
    const arch = ROSETTA_ARCHS[aid] || { name: aid.replace(/_/g, " "), eq: "", breaks: ["Verify domain-specific regime limits before transferring equations."] };
    const breaksAt = Array.isArray(body.breaksAt) ? body.breaksAt : arch.breaks;
    const row = { from: src, to: tgt, premises: [src], conclusion: tgt, relation: "isomorphic_to", encompassWeight: 0.25, archetypeId: aid, breaksAt, why: `Structural isomorphism via ${arch.name}` };
    if (!exists) chains.push(row);
    state.graph.chains = chains;
    const ros = state.hub.rosetta || { ok: true, chords: [], archetypesMatched: [], chordCount: 0, persistedCount: 0, discoveredCount: 0, archetypesCount: 10 };
    const chords = Array.isArray(ros.chords) ? ros.chords : [];
    const existingChord = chords.find((c) => (c.sourceId === a && c.targetId === b) || (c.sourceId === b && c.targetId === a));
    if (existingChord) {
      existingChord.persisted = true;
      existingChord.score = 1.0;
    } else {
      chords.push({
        id: `iso-${aid}-${a}--${b}`, sourceId: a, targetId: b,
        sourceTitle: pa.title || a, targetTitle: pb.title || b,
        sourceDomain: pa.subject || "General", targetDomain: pb.subject || "General",
        sourceChapter: pa.chapter || "General", targetChapter: pb.chapter || "General",
        sourceSubject: pa.subject || "General", targetSubject: pb.subject || "General",
        archetypeId: aid, archetypeName: arch.name, canonicalEquation: arch.eq,
        variableBijection: {}, breaksAt, score: 1.0, persisted: true
      });
    }
    ros.chords = chords;
    ros.chordCount = chords.length;
    ros.persistedCount = chords.filter((c) => c.persisted).length;
    ros.discoveredCount = chords.length - ros.persistedCount;
    state.hub.rosetta = ros;
    if (state.hub.stats) state.hub.stats.rosettaChordsCount = ros.chordCount;
    saveState(state);
    return { ok: true, created: !exists, alreadyExists: exists, chord: row, rosetta: ros };
  }

  function syncCountsFor(st) {
    return {
      propositions: (st.graph.propositions || []).length,
      chains: (st.graph.chains || []).length,
      mistakes: (st.hub.mistakes || []).length,
      evidenceLedger: Number((st.hub.evidenceSummary && st.hub.evidenceSummary.ledgerIntegrity && st.hub.evidenceSummary.ledgerIntegrity.count) || 10),
      vivaSessions: (st.hub.vivaSessions || []).length,
      forks: (st.hub.forks || []).length,
      arenas: (st.hub.arenas || []).length,
      derivations: (st.hub.derivations || []).length,
      trackers: (st.hub.chapterTrackers || []).length,
      learnerProfile: st.hub.learnerProfile ? 1 : 0
    };
  }

  function handleSyncStatus() {
    state = loadState();
    const ss = state.hub.syncStatus || { ok: true, workspace: "syntopik-live-demo", format: "syntopik-delta-v1", schemaVersion: 1, initialized: true, deviceId: "dev-demo-01", lamportClock: 7, lastExportedAt: "2026-11-25T18:30:00Z", lastMergedAt: "2026-11-25T18:35:00Z", lastSyncedAt: "2026-11-25T18:35:00Z", peerClocks: {}, mergedDeltaCount: 1, ledgerIntegrity: { ok: true, count: 10, brokenAt: null } };
    ss.counts = syncCountsFor(state);
    state.hub.syncStatus = ss;
    return { ok: true, ...ss };
  }

  function handleSyncExport(body) {
    state = loadState();
    const ss = handleSyncStatus();
    const nextClock = Number(ss.lamportClock || 0) + 1;
    const exportedAt = new Date().toISOString().replace(/\.\d+Z$/, "Z");
    const counts = syncCountsFor(state);
    const trackers = {};
    for (const tr of (state.hub.chapterTrackers || [])) if (tr && tr.slug) trackers[tr.slug] = tr;
    const payload = {
      propositions: state.graph.propositions || [],
      chains: state.graph.chains || [],
      mistakes: state.hub.mistakes || [],
      forks: state.hub.forks || [],
      arenas: state.hub.arenas || [],
      derivations: state.hub.derivations || [],
      vivaSessions: state.hub.vivaSessions || [],
      trackers,
      learnerProfile: state.hub.learnerProfile || null
    };
    const env = { schemaVersion: 1, format: "syntopik-delta-v1", deviceId: ss.deviceId || "dev-demo-01", exportedAt, since: (body && body.since) || null, lamportClock: nextClock, counts, payload };
    const sha = sha256Hex(canonicalJson(env));
    env.contentSha256 = sha;
    const deltaJson = JSON.stringify(env, null, 2) + "\n";
    const bytes = new TextEncoder().encode(deltaJson).length;
    ss.lamportClock = nextClock;
    ss.lastExportedAt = exportedAt;
    ss.lastSyncedAt = exportedAt;
    state.hub.syncStatus = ss;
    saveState(state);
    return { ok: true, format: "syntopik-delta-v1", schemaVersion: 1, deviceId: env.deviceId, exportedAt, lamportClock: nextClock, counts, contentSha256: sha, bytes, relPath: "exports/syntopik-delta.json", delta: env, deltaJson };
  }

  function handleSyncMerge(body) {
    state = loadState();
    const ss = handleSyncStatus();
    const dryRun = Boolean(body && (body.dryRun || body.dry_run));
    let env = body && body.delta;
    if (typeof env === "string") {
      if (new TextEncoder().encode(env).length > 10 * 1024 * 1024) return { ok: false, valid: false, error: "Delta exceeds 10 MB limit." };
      try { env = JSON.parse(env); } catch { return { ok: false, valid: false, error: "Malformed delta JSON." }; }
    } else if (!env && body && body.payload) {
      env = body;
    }
    if (!env || typeof env !== "object" || Array.isArray(env)) return { ok: false, valid: false, error: "Expected a syntopik-delta-v1 JSON object." };
    if (env.format !== "syntopik-delta-v1" || Number(env.schemaVersion) !== 1) return { ok: false, valid: false, error: "Unsupported delta format or schemaVersion (expected syntopik-delta-v1 v1)." };
    if (typeof env.deviceId !== "string" || !env.deviceId.trim()) return { ok: false, valid: false, error: "Missing valid source deviceId." };
    const sha = String(env.contentSha256 || "").trim().toLowerCase();
    if (!/^[0-9a-f]{64}$/.test(sha)) return { ok: false, valid: false, error: "Missing or malformed 64-hex contentSha256." };
    const noSha = { ...env };
    delete noSha.contentSha256;
    const expectedSha = sha256Hex(canonicalJson(noSha));
    if (expectedSha !== sha) return { ok: false, valid: false, error: "contentSha256 mismatch — delta payload appears tampered or corrupted." };
    const pl = env.payload;
    if (!pl || typeof pl !== "object" || Array.isArray(pl)) return { ok: false, valid: false, error: "Missing payload object." };
    if (pl.trackers && typeof pl.trackers === "object") {
      for (const slug of Object.keys(pl.trackers)) {
        if (!/^[a-z0-9][a-z0-9_-]{0,80}$/.test(slug)) return { ok: false, valid: false, error: "Invalid tracker slug rejected: " + slug };
      }
    }
    const mergeById = (localArr, incArr, isProp) => {
      const byId = new Map((localArr || []).map((x) => [String(x.id || ""), clone(x)]));
      let added = 0, updated = 0;
      for (const item of (Array.isArray(incArr) ? incArr : [])) {
        if (!item || typeof item !== "object" || !item.id) continue;
        const id = String(item.id);
        if (!byId.has(id)) { byId.set(id, clone(item)); added += 1; }
        else if (isProp) {
          const cur = byId.get(id);
          if (Number(item.reps || 0) > Number(cur.reps || 0)) { byId.set(id, { ...cur, ...clone(item) }); updated += 1; }
        }
      }
      return { merged: [...byId.values()], added, updated };
    };
    const pM = mergeById(state.graph.propositions, pl.propositions, true);
    const mM = mergeById(state.hub.mistakes, pl.mistakes, false);
    const fM = mergeById(state.hub.forks, pl.forks, false);
    const aM = mergeById(state.hub.arenas, pl.arenas, false);
    const dM = mergeById(state.hub.derivations, pl.derivations, false);
    const chSet = new Set((state.graph.chains || []).map((c) => canonicalJson(c)));
    const chMerged = clone(state.graph.chains || []);
    let chAdd = 0;
    for (const c of (Array.isArray(pl.chains) ? pl.chains : [])) {
      const k = canonicalJson(c);
      if (!chSet.has(k)) { chSet.add(k); chMerged.push(clone(c)); chAdd += 1; }
    }
    const collections = {
      propositions: { added: pM.added, updated: pM.updated, total: pM.merged.length },
      chains: { added: chAdd, updated: 0, total: chMerged.length },
      mistakes: { added: mM.added, updated: mM.updated, total: mM.merged.length },
      forks: { added: fM.added, updated: fM.updated, total: fM.merged.length },
      arenas: { added: aM.added, updated: aM.updated, total: aM.merged.length },
      derivations: { added: dM.added, updated: dM.updated, total: dM.merged.length }
    };
    const totalAdded = pM.added + chAdd + mM.added + fM.added + aM.added + dM.added;
    const totalUpdated = pM.updated + mM.updated + fM.updated + aM.updated + dM.updated;
    const remoteClock = Math.max(0, Number(env.lamportClock || 0));
    const newClock = Math.max(Number(ss.lamportClock || 0), remoteClock) + (totalAdded + totalUpdated > 0 ? 1 : 0);
    if (!dryRun) {
      state.graph.propositions = pM.merged;
      state.graph.chains = chMerged;
      state.hub.mistakes = mM.merged;
      state.hub.forks = fM.merged;
      state.hub.arenas = aM.merged;
      state.hub.derivations = dM.merged;
      const nowIso = new Date().toISOString().replace(/\.\d+Z$/, "Z");
      ss.lamportClock = newClock;
      ss.lastMergedAt = nowIso;
      ss.lastSyncedAt = nowIso;
      ss.mergedDeltaCount = Number(ss.mergedDeltaCount || 0) + 1;
      ss.counts = syncCountsFor(state);
      state.hub.syncStatus = ss;
      refreshDerived(state);
      saveState(state);
    }
    return {
      ok: true, dryRun, localDeviceId: ss.deviceId || "dev-demo-01", sourceDeviceId: env.deviceId,
      lamportClock: newClock, contentSha256: sha, added: totalAdded, updated: totalUpdated,
      idempotentNoop: totalAdded === 0 && totalUpdated === 0, collections,
      ledgerIntegrity: ss.ledgerIntegrity || { ok: true, count: 10, brokenAt: null }
    };
  }

  function handleProfileReplay(body) {
    state = loadState();
    const applyFlag = Boolean(body && (body.apply || body.applyIfImproved));
    const minEvents = Math.max(2, Math.min(100, Number((body && (body.minEvents || body.min_events)) || 12)));
    const lp = state.hub.learnerProfile || {};
    const totalEv = Math.max(89, Number((lp.globalStats && lp.globalStats.totalEvents) || 0) + 71);
    const trainEvents = Math.floor(totalEv * 0.7);
    const testEvents = totalEv - trainEvents;
    const baselineParams = { eloKBase: 64.0, erNoviceCutoff: 0.35, erChallengeCutoff: 0.80, fatigueMultiplier: 1.25 };
    const candidateParams = { eloKBase: 48.0, erNoviceCutoff: 0.35, erChallengeCutoff: 0.78, fatigueMultiplier: 1.15 };
    const baselineMetrics = {
      train: { brierScore: 0.1142, logLoss: 0.3910, erMismatchRate: 0.0323, compositeLoss: 0.1748 },
      oos: { brierScore: 0.0884, logLoss: 0.3125, erMismatchRate: 0.0370, compositeLoss: 0.1342 }
    };
    const candidateMetrics = {
      train: { brierScore: 0.1086, logLoss: 0.3764, erMismatchRate: 0.0161, compositeLoss: 0.1631 },
      oos: { brierScore: 0.0802, logLoss: 0.2940, erMismatchRate: 0.0, compositeLoss: 0.1218 }
    };
    const oosImprovement = 0.0124;
    if (applyFlag) {
      lp.calibratedHyperparams = {
        ...candidateParams,
        calibratedAt: todayIso() + "T12:00:00Z",
        trainEvents, testEvents,
        oosBaselineLoss: baselineMetrics.oos.compositeLoss,
        oosCalibratedLoss: candidateMetrics.oos.compositeLoss,
        oosImprovement
      };
      recomputePolicy(lp, "normal");
      state.hub.learnerProfile = lp;
      saveState(state);
    }
    return {
      ok: true, insufficientData: false, eventCount: totalEv, minEvents,
      split: { method: "chronological_time_series_70_30", trainEvents, testEvents },
      gridEvaluated: 135, minOosGainThreshold: 0.005,
      baselineParams, candidateParams, baselineMetrics, candidateMetrics,
      oosImprovement, improved: true, applied: applyFlag
    };
  }

  function handleDerivationDiff(body) {
    state = loadState();
    const derivId = String((body && (body.derivationId || body.id)) || "").trim();
    const dObj = (state.hub.derivations || []).find((x) => x.id === derivId);
    const specMap = DERIV_STEPS[derivId];
    if (!dObj || !specMap) return { ok: false, error: "Derivation not found: " + derivId };
    let rawSteps = body && (body.studentSteps || body.student_steps || body.steps);
    if (typeof rawSteps === "string") rawSteps = rawSteps.split(/\r?\n/).map((s) => s.trim()).filter(Boolean).map((expr, i) => ({ line: i + 1, expr }));
    if (!Array.isArray(rawSteps) || !rawSteps.length) return { ok: false, error: "studentSteps must be non-empty." };
    if (rawSteps.length > 40) return { ok: false, error: "Maximum 40 derivation steps allowed." };
    const normSteps = [];
    for (let i = 0; i < rawSteps.length; i++) {
      const it = rawSteps[i];
      const expr = typeof it === "string" ? it.trim() : String((it && (it.expr ?? it.expression)) || "").trim();
      const line = (it && Number.isInteger(it.line) && it.line > 0) ? it.line : i + 1;
      if (expr.length > 512) return { ok: false, error: `Line ${line} exceeds 512 character limit.` };
      normSteps.push({ line, expr });
    }
    const dag = dObj.steps || [];
    const byId = new Map(dag.map((s) => [String(s.id), s]));
    const transDeps = new Map();
    for (const s of dag) {
      const ds = new Set();
      for (const d of (s.dependsOn || [])) { ds.add(d); for (const td of (transDeps.get(d) || [])) ds.add(td); }
      transDeps.set(String(s.id), ds);
    }
    const matchStep = (rawExpr, sid) => {
      const sp = specMap[sid];
      if (!sp) return false;
      const n = normEq(rawExpr);
      return sp.ok.some((c) => n === c || (c.length >= 6 && n.includes(c)));
    };
    const verified = new Set();
    const verifiedPrefix = [];
    const statusById = new Map();
    let breakStep = null, breakLine = null, breakExpr = "", errorKind = null;
    for (const st of normSteps) {
      if (breakStep || verified.size >= dag.length) break;
      const unv = dag.filter((s) => !verified.has(String(s.id)));
      const ready = unv.filter((s) => (s.dependsOn || []).every((d) => verified.has(String(d))));
      const exp = ready[0] || unv[0];
      const eid = String(exp.id);
      if (matchStep(st.expr, eid)) {
        verified.add(eid);
        verifiedPrefix.push(st.expr);
        statusById.set(eid, { stepId: eid, label: exp.label || eid, role: exp.role || "step", dependsOn: exp.dependsOn || [], status: "verified", studentLine: st.line, studentExpr: st.expr, matchMethod: "symbolic_equiv", brokenDependencies: [] });
        continue;
      }
      const alt = ready.slice(1).find((c) => matchStep(st.expr, String(c.id)));
      if (alt) {
        const cid = String(alt.id);
        verified.add(cid);
        verifiedPrefix.push(st.expr);
        statusById.set(cid, { stepId: cid, label: alt.label || cid, role: alt.role || "step", dependsOn: alt.dependsOn || [], status: "verified", studentLine: st.line, studentExpr: st.expr, matchMethod: "symbolic_equiv", brokenDependencies: [] });
        continue;
      }
      breakStep = exp;
      breakLine = st.line;
      breakExpr = st.expr;
      const n = normEq(st.expr);
      const sp = specMap[eid] || {};
      const laterHit = unv.some((ls) => String(ls.id) !== eid && matchStep(st.expr, String(ls.id)));
      if (laterHit) errorKind = "skipped_step";
      else if (exp.role === "axiom") errorKind = "axiom_violation";
      else if ((sp.signFlip || []).some((sf) => n === sf) || (sp.expr && sp.expr.includes("-") && !n.includes("-"))) errorKind = "sign_error";
      else if ((sp.factors || []).some((f) => !n.includes(f))) errorKind = "missing_factor";
      else errorKind = "algebra_slip";
      statusById.set(eid, { stepId: eid, label: exp.label || eid, role: exp.role || "step", dependsOn: exp.dependsOn || [], status: "broken", studentLine: st.line, studentExpr: st.expr, matchMethod: null, errorKind, brokenDependencies: [] });
      break;
    }
    const brokenId = breakStep ? String(breakStep.id) : null;
    for (const s of dag) {
      const sid = String(s.id);
      if (statusById.has(sid)) continue;
      const unmet = (s.dependsOn || []).filter((d) => !verified.has(String(d)));
      const tUnmet = [...(transDeps.get(sid) || [])].filter((d) => !verified.has(d));
      const blocked = brokenId && ((transDeps.get(sid) || new Set()).has(brokenId) || unmet.length > 0);
      statusById.set(sid, { stepId: sid, label: s.label || sid, role: s.role || "step", dependsOn: s.dependsOn || [], status: blocked ? "skipped_dependency" : "not_reached", studentLine: null, studentExpr: null, matchMethod: null, brokenDependencies: blocked ? (unmet.length ? unmet : tUnmet) : [] });
    }
    const stepsOut = dag.map((s) => statusById.get(String(s.id)));
    const allVerified = verified.size === dag.length && !breakStep;
    let nudges = null, spawnedFork = null, diagnosticProbe = null, evidenceRecorded = false;
    if (breakStep) {
      const lbl = breakStep.label || brokenId;
      const depLabels = (breakStep.dependsOn || []).map((d) => (byId.get(String(d)) && byId.get(String(d)).label) || d);
      const r1Map = {
        sign_error: `Line ${breakLine} has a sign/direction mismatch — check whether the restoring or constraining term opposes displacement/growth.`,
        missing_factor: `Line ${breakLine} is missing a multiplicative factor, constant, or root — check dimensional consistency on both sides.`,
        skipped_step: `Line ${breakLine} jumps ahead to a downstream conclusion before justifying ${lbl}. Write the intermediate bridge explicitly.`,
        axiom_violation: `Re-check the starting physical/protocol axiom for ${lbl} before propagating it downstream.`,
        algebra_slip: `Re-verify the algebraic manipulation on line ${breakLine} directly from its immediate upstream premises.`
      };
      nudges = {
        R0: `Earliest divergence is on line ${breakLine} at step ${brokenId} (${lbl}). Fix this line before continuing downstream.`,
        R1: r1Map[errorKind] || r1Map.algebra_slip,
        R2: `Combine upstream premise(s) [${depLabels.join(" + ") || "starting axioms"}] directly to derive ${lbl} without dropping signs or factors.`
      };
      if (body.autoSpawnFork !== false) {
        const fid = `fork-pdiff-${derivId.slice(0, 12)}-${brokenId}`;
        const existingF = (state.hub.forks || []).find((f) => f.id === fid);
        const forkObj = existingF || {
          id: fid, propId: dObj.propId || "", subject: dObj.domain || "STEM", chapter: dObj.chapter || "",
          prefixSteps: verifiedPrefix, forkPrompt: `Continue '${dObj.title}' from the verified prefix: write the exact next step (${lbl}).`,
          status: "active", isDue: true, nextReview: todayIso(), winCount: 0, blunderRepeatCount: 1, latencyHistoryMs: []
        };
        if (!existingF) { state.hub.forks = [...(state.hub.forks || []), forkObj]; }
        FORK_RULES[fid] = { ok: (specMap[brokenId] && specMap[brokenId].ok) || [], trap: [normEq(breakExpr)], fix: (specMap[brokenId] && specMap[brokenId].expr) || lbl, refute: nudges.R1 };
        spawnedFork = { id: fid, fork: forkObj };
      }
      if (dObj.propId) {
        const rc = handleRootCause(dObj.propId);
        diagnosticProbe = rc.diagnosticProbe || null;
      }
      refreshDerived(state);
      saveState(state);
    } else if (allVerified) {
      evidenceRecorded = promoteEvidence(state, dObj.propId, 3, "derivation_proof_diff");
      refreshDerived(state);
      saveState(state);
    }
    return {
      ok: true, derivationId: dObj.id, propId: dObj.propId, title: dObj.title, domain: dObj.domain, chapter: dObj.chapter,
      allVerified, verifiedCount: verified.size, totalSteps: dag.length, studentStepCount: normSteps.length,
      earliestBreakLine: breakLine, earliestBreakStepId: brokenId, errorKind, nudges,
      verifiedPrefix, steps: stepsOut, spawnedFork, diagnosticProbe, evidenceRecorded
    };
  }

  const DEMO_HARNESS_ROWS = [
    ["antigravity", "Google Antigravity", 0, 1, 1],
    ["claude", "Claude Code", 1, 1, 1],
    ["codex", "OpenAI Codex", 1, 1, 0],
    ["cursor", "Cursor", 1, 1, 1],
    ["windsurf", "Windsurf", 1, 0, 0],
    ["copilot", "GitHub Copilot (VS Code)", 1, 0, 0],
    ["gemini", "Gemini CLI", 1, 0, 0],
    ["opencode", "OpenCode", 1, 0, 0],
    ["cline", "Cline", 1, 0, 0],
    ["roo", "Roo Code", 1, 0, 0],
    ["zed", "Zed", 1, 0, 0],
    ["aider", "Aider", 0, 0, 0],
    ["continue", "Continue", 1, 0, 0],
    ["kiro", "Kiro", 1, 0, 0]
  ];
  const DEMO_MCP_TOOLS = [
    "synt_next_move", "synt_get_frontier", "synt_capture_concept",
    "synt_log_review", "synt_whiteboard_step", "synt_readiness", "synt_interleave"
  ];
  function buildDemoAgentsCompat() {
    const harnesses = DEMO_HARNESS_ROWS.map(([id, name, httpOk, det, cfg]) => ({
      id, name,
      transportSupport: id === "aider" ? [] : (httpOk ? ["stdio", "http"] : ["stdio"]),
      httpMcpSupported: Boolean(httpOk),
      detected: Boolean(det), configured: Boolean(cfg), attached: Boolean(cfg),
      connectCmd: "syntopik connect " + id,
      attachCmd: "syntopik attach . --harness " + id
    }));
    return {
      ok: true, totalHarnesses: harnesses.length,
      detectedCount: harnesses.filter((h) => h.detected).length,
      configuredCount: harnesses.filter((h) => h.configured).length,
      attachedCount: harnesses.filter((h) => h.attached).length,
      httpSupportedCount: harnesses.filter((h) => h.httpMcpSupported).length,
      activeHarness: "antigravity", harnesses
    };
  }

  function handleReadinessGet(u) {
    state = loadState();
    const base = clone(state.hub.readinessSummary || {
      ok: true, readinessIndex: 76.4, band: "exam_ready",
      predictedScorePct: { low: 68.0, expected: 76.5, high: 85.0 },
      predictedMarks: { maxMarks: 100, low: 68.0, expected: 76.5, high: 85.0 },
      examDate: "2026-12-06", examDateSource: "study-config.json", daysToExam: 10.0,
      hasExamConfig: true,
      taperMode: { active: false, phase: "none", allowNewConcepts: true, hoursRemaining: 237.0, sleepFloorHours: 7.5, maxSessionMinutes: 120 },
      preMortemShield: { ok: true, sourceBasis: "observed_logs", count: 0, bullets: [] },
      topLevers: [],
      pillars: { projectedRetention: 79.0, evidenceDepth: 68.0, syllabusCoverage: 90.0, forkClearance: 62.0, evDiscipline: 81.0 },
      executionConfidence: "observed", uncertaintyReasons: []
    });
    const pmOnly = ["1", "true", "yes"].includes(String(u.searchParams.get("premortem_only") || u.searchParams.get("premortemOnly") || "").toLowerCase());
    if (pmOnly) return base.preMortemShield || { ok: true, sourceBasis: "observed_logs", count: 0, bullets: [] };
    const rawDate = String(u.searchParams.get("exam_date") || u.searchParams.get("examDate") || "").trim();
    const rawDays = String(u.searchParams.get("days") || "").trim();
    const tdy = state.hub.today || todayIso();
    let daysToExam = Number(base.daysToExam ?? 10);
    let examDate = base.examDate || addDays(tdy, 10);
    if (rawDate && /^\d{4}-\d{2}-\d{2}/.test(rawDate)) {
      examDate = rawDate.slice(0, 10);
      const diff = (Date.parse(examDate + "T09:00:00Z") - Date.parse(tdy + "T12:00:00Z")) / 86400000;
      daysToExam = Math.max(0, Number(diff.toFixed(1)));
    } else if (rawDays && Number.isFinite(Number(rawDays))) {
      daysToExam = Math.max(0, Number(Number(rawDays).toFixed(1)));
      examDate = addDays(tdy, Math.round(daysToExam));
    }
    const hrs = Number((daysToExam * 24).toFixed(1));
    const active = hrs <= 72;
    const phase = !active ? "none" : (hrs <= 24 ? "final_24h" : "taper_72h");
    base.examDate = examDate;
    base.daysToExam = daysToExam;
    base.hasExamConfig = true;
    base.taperMode = {
      active, phase, window: active ? (hrs <= 24 ? "final_24h" : hrs <= 48 ? "48h_to_24h" : "72h_to_48h") : "none",
      hoursRemaining: hrs, allowNewConcepts: !active, sleepFloorHours: 7.5,
      maxSessionMinutes: hrs <= 24 ? 60 : (hrs <= 48 ? 150 : (active ? 240 : 120)), examDate
    };
    if (rawDate || rawDays) {
      const origDays = Number((state.hub.readinessSummary && state.hub.readinessSummary.daysToExam) ?? 10);
      const deltaRet = Math.max(-18, Math.min(12, (origDays - daysToExam) * 0.65));
      const pil = base.pillars || {};
      if (typeof pil.projectedRetention === "number") pil.projectedRetention = Number(Math.max(15, Math.min(99, pil.projectedRetention + deltaRet)).toFixed(1));
      const idx = Number(Math.max(10, Math.min(99, Number(base.readinessIndex || 75) + deltaRet * 0.32)).toFixed(1));
      base.readinessIndex = idx;
      base.band = idx >= 85 ? "peak" : idx >= 70 ? "exam_ready" : idx >= 50 ? "building" : "needs_foundation";
      const exp = Number(Math.max(10, Math.min(99, Number((base.predictedScorePct && base.predictedScorePct.expected) || idx) + deltaRet * 0.25)).toFixed(1));
      base.predictedScorePct = { low: Number(Math.max(0, exp - 8.5).toFixed(1)), expected: exp, high: Number(Math.min(100, exp + 8.5).toFixed(1)) };
      if (base.predictedMarks && base.predictedMarks.maxMarks) {
        const mm = Number(base.predictedMarks.maxMarks);
        base.predictedMarks = { maxMarks: mm, low: Number((mm * base.predictedScorePct.low / 100).toFixed(1)), expected: Number((mm * exp / 100).toFixed(1)), high: Number((mm * base.predictedScorePct.high / 100).toFixed(1)) };
      }
    }
    return base;
  }

  function stripBlindCues(txt, ch, sub) {
    let s = String(txt || "").trim();
    const startsUpper = /^[A-Z]/.test(s);
    for (const lbl of [ch, sub, "SHM", "Raft"]) {
      const clean = String(lbl || "").trim();
      if (clean.length < 3) continue;
      const escRe = clean.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
      s = s.replace(new RegExp("\\b(?:in|for|from|of)\\s+" + escRe + "\\b[,:\\s-]*", "gi"), "");
      s = s.replace(new RegExp("(?<![A-Za-z0-9])" + escRe + "(?![A-Za-z0-9])\\s*[:\\-]?", "gi"), "");
    }
    s = s.replace(/[ \t]{2,}/g, " ").replace(/^[,;:\-.\s]+/, "").trim();
    if (s && startsUpper) s = s[0].toUpperCase() + s.slice(1);
    return s ? s : "Identify the governing invariant and regime conditions for this problem.";
  }

  function handleInterleaveGet() {
    state = loadState();
    return state.hub.interleaveStats || { ok: true, totalSessions: 0, completedSessions: 0, totalItemsChecked: 0, tier1Correct: 0, tier1AccuracyPct: null, principleAccuracyPct: null, step2Correct: 0, step2Total: 0, step2AccuracyPct: null, medianIdentifyMs: null, medianIdentifySec: null, distinctChaptersPracticed: 0, confusionMatrix: {}, topConfusionPairs: [] };
  }

  function handleInterleaveBuild(body) {
    state = loadState();
    const props = (state.graph.propositions || []).filter((p) => p && p.id && p.title);
    if (!props.length) return { ok: true, items: [], emptyReason: "Add propositions to start a blind interleave drill" };
    const n = Math.max(1, Math.min(Math.min(12, props.length), Number((body && (body.n || body.count)) || 6)));
    const byCh = new Map();
    for (const p of props) {
      const ch = String(p.chapter || "General");
      if (!byCh.has(ch)) byCh.set(ch, []);
      byCh.get(ch).push(p);
    }
    const chKeys = [...byCh.keys()];
    const chosen = [];
    let lastCh = "";
    const used = new Set();
    for (let k = 0; k < n; k++) {
      let pick = null;
      for (let off = 0; off < chKeys.length; off++) {
        const ck = chKeys[(k + off) % chKeys.length];
        if (chKeys.length > 1 && ck === lastCh) continue;
        const cand = (byCh.get(ck) || []).find((x) => !used.has(x.id));
        if (cand) { pick = cand; lastCh = ck; break; }
      }
      if (!pick) pick = props.find((x) => !used.has(x.id));
      if (!pick) break;
      used.add(pick.id);
      chosen.push(pick);
    }
    const sid = "ilv-demo-" + Date.now();
    const privItems = [];
    const pubItems = chosen.map((p, idx) => {
      const itemId = "ilv-" + (idx + 1);
      const ch = String(p.chapter || "General");
      const sub = String(p.subject || "General");
      const cue = (p.cues && p.cues[0]) || { q: p.statement || p.title, a: p.statement || p.title };
      const blindPrompt = stripBlindCues(cue.q || p.statement || p.title, ch, sub);
      const truePr = stripBlindCues(p.title, ch, sub) || p.title;
      const decoys = props.filter((x) => x.id !== p.id && String(x.chapter || "") !== ch);
      const pickedDecoys = [];
      for (let d = 0; d < 3 && decoys.length; d++) {
        const cand = decoys[(idx * 3 + d * 5) % decoys.length];
        if (cand && !pickedDecoys.some((z) => z.id === cand.id)) pickedDecoys.push(cand);
      }
      const rawOpts = [
        { text: truePr, ch, isTrue: true },
        ...pickedDecoys.map((d) => ({ text: stripBlindCues(d.title, String(d.chapter || ""), String(d.subject || "")) || d.title, ch: String(d.chapter || "General"), isTrue: false }))
      ];
      const rot = idx % rawOpts.length;
      const ordered = rawOpts.slice(rot).concat(rawOpts.slice(0, rot));
      const letters = ["A", "B", "C", "D"];
      const optCh = {};
      let correctId = "A";
      const principleChoices = ordered.map((o, j) => {
        const oid = letters[j] || String(j + 1);
        optCh[oid] = o.ch;
        if (o.isTrue) correctId = oid;
        return { id: oid, text: o.text };
      });
      principleChoices.push({ id: "E", text: "I don't know" });
      privItems.push({ itemId, pid: p.id, ch, sub, pr: truePr, ans: String(cue.a || p.statement || ""), s2Prompt: String(cue.q || p.statement || blindPrompt), correctId, optCh, choices: principleChoices, s1: null, s2: null });
      return { itemId, prompt: blindPrompt, principleChoices, stage: "principle_select" };
    });
    const distinctChaptersCount = new Set(chosen.map((p) => String(p.chapter || "General"))).size;
    interleaveStore.set(sid, { sessionId: sid, items: privItems });
    return { ok: true, sessionId: sid, mode: "principle_first", blindChapterLabels: true, distinctChaptersCount, count: pubItems.length, items: pubItems };
  }

  function handleInterleaveCheck(body) {
    const sess = interleaveStore.get(String(body.sessionId || ""));
    if (!sess) return { ok: false, error: "Interleave session not found — build a fresh set." };
    const it = sess.items.find((x) => x.itemId === String(body.itemId || ""));
    if (!it) return { ok: false, error: "Interleave item not found." };
    const selRaw = String(body.selectedPrinciple || body.chosenOptionId || body.optionId || "").trim();
    const matched = it.choices.find((c) => c.id.toLowerCase() === selRaw.toLowerCase() || normTxt(c.text) === normTxt(selRaw)) || { id: selRaw, text: selRaw };
    const isDk = matched.id === "E" || /don'?t\s*know/i.test(matched.text);
    const correct = !isDk && (matched.id === it.correctId || normTxt(matched.text) === normTxt(it.pr));
    let confusedChapter = !correct && !isDk ? (it.optCh[matched.id] || null) : null;
    if (confusedChapter === it.ch) confusedChapter = null;
    const conf = typeof body.confidence === "number" ? Math.max(0, Math.min(1, body.confidence)) : 0.7;
    const dur = Number(body.durationMs || 8000);
    it.s1 = { optId: matched.id, text: matched.text, correct, dontKnow: isDk, conf, dur, confusedChapter };
    const checked = sess.items.filter((x) => x.s1);
    const hits = checked.filter((x) => x.s1.correct).length;
    const acc = checked.length ? Number(((hits / checked.length) * 100).toFixed(1)) : 0;
    return {
      ok: true, sessionId: sess.sessionId, itemId: it.itemId, stage: "principle_checked",
      correct, dontKnow: isDk, selectedOptionId: matched.id, selectedPrinciple: matched.text,
      expectedPrincipleId: it.correctId, expectedPrinciple: it.pr, revealedChapter: it.ch,
      confusedChapter, confidence: conf, durationMs: dur, step2Unlocked: true,
      step2Prompt: it.s2Prompt, tier1CheckedCount: checked.length, tier1AccuracyPct: acc
    };
  }

  function handleInterleaveComplete(body) {
    const sess = interleaveStore.get(String(body.sessionId || ""));
    if (!sess) return { ok: false, error: "Interleave session not found." };
    const rmap = (body && body.results && typeof body.results === "object") ? body.results : {};
    let t1c = 0, t1t = 0, s2c = 0, s2t = 0, srsUpd = 0, evRec = 0, mLog = 0;
    const cm = {};
    const revealedItems = [];
    state = loadState();
    for (const it of sess.items) {
      const r = rmap[it.itemId];
      if (r && typeof r === "object" && "solved" in r) it.s2 = { correct: Boolean(r.solved) };
      if (it.s1) {
        t1t += 1;
        if (it.s1.correct) { t1c += 1; cm[it.ch] = cm[it.ch] || {}; cm[it.ch][it.ch] = (cm[it.ch][it.ch] || 0) + 1; }
        else {
          mLog += 1;
          const gc = it.s1.confusedChapter || (it.s1.dontKnow ? "I don't know" : "Other");
          cm[it.ch] = cm[it.ch] || {}; cm[it.ch][gc] = (cm[it.ch][gc] || 0) + 1;
        }
      }
      if (it.s2) { s2t += 1; if (it.s2.correct) s2c += 1; }
      if (it.s1 || it.s2) {
        srsUpd += 1;
        if ((it.s1 ? it.s1.correct : true) && (it.s2 ? it.s2.correct : true)) {
          if (promoteEvidence(state, it.pid, 3, "interleave_blind")) evRec += 1;
        }
      }
      revealedItems.push({
        itemId: it.itemId, chapter: it.ch, subject: it.sub, propId: it.pid,
        principle: it.pr, expectedAnswer: it.ans,
        step1Correct: it.s1 ? it.s1.correct : null, step2Correct: it.s2 ? it.s2.correct : null,
        confusedChapter: it.s1 ? it.s1.confusedChapter : null
      });
    }
    const pairs = [];
    for (const [tc, row] of Object.entries(cm)) for (const [gc, cnt] of Object.entries(row)) if (gc !== tc) pairs.push({ trueChapter: tc, confusedWithChapter: gc, count: cnt });
    pairs.sort((a, b) => b.count - a.count);
    const t1Acc = t1t ? Number(((t1c / t1t) * 100).toFixed(1)) : null;
    const s2Acc = s2t ? Number(((s2c / s2t) * 100).toFixed(1)) : null;
    const st = state.hub.interleaveStats || { ok: true, totalSessions: 0, completedSessions: 0, totalItemsChecked: 0, tier1Correct: 0, step2Correct: 0, step2Total: 0, topConfusionPairs: [] };
    st.totalSessions = Number(st.totalSessions || 0) + 1;
    st.completedSessions = Number(st.completedSessions || 0) + 1;
    st.totalItemsChecked = Number(st.totalItemsChecked || 0) + t1t;
    st.tier1Correct = Number(st.tier1Correct || 0) + t1c;
    st.tier1AccuracyPct = st.totalItemsChecked ? Number(((st.tier1Correct / st.totalItemsChecked) * 100).toFixed(1)) : t1Acc;
    st.principleAccuracyPct = st.tier1AccuracyPct;
    st.step2Correct = Number(st.step2Correct || 0) + s2c;
    st.step2Total = Number(st.step2Total || 0) + s2t;
    st.step2AccuracyPct = st.step2Total ? Number(((st.step2Correct / st.step2Total) * 100).toFixed(1)) : s2Acc;
    if (pairs.length) st.topConfusionPairs = pairs.slice(0, 5);
    state.hub.interleaveStats = st;
    refreshDerived(state);
    saveState(state);
    return {
      ok: true, sessionId: sess.sessionId, status: "completed", completedAt: new Date().toISOString(),
      tier1Correct: t1c, tier1Total: t1t, tier1AccuracyPct: t1Acc,
      step2Correct: s2c, step2Total: s2t, step2AccuracyPct: s2Acc,
      confusionMatrix: cm, topConfusionPairs: pairs.slice(0, 5), revealedItems,
      mistakesLogged: mLog, srsUpdated: srsUpd, evidenceRecorded: evRec
    };
  }

  function routeApi(method, urlStr, body) {
    const u = new URL(urlStr, window.location.origin || "http://localhost");
    const p = u.pathname;
    if (p === "/api/capabilities") return {
      live: true, demo: true, token: "demo-sandbox", version: "3.2.0-demo",
      workspaceName: "syntopik-live-demo", lan: false,
      mcpHttpEndpoint: "http://127.0.0.1:4173/mcp",
      mcpHttpEnabled: true,
      mcpHttpTools: DEMO_MCP_TOOLS,
      agentsCompat: buildDemoAgentsCompat()
    };
    if (p === "/api/state") return buildStateResponse(u.searchParams.get("energy") || "normal");
    if (p === "/api/readiness") return handleReadinessGet(u);
    if (p === "/api/interleave") return handleInterleaveGet();
    if (p === "/api/interleave/build") return handleInterleaveBuild(body || {});
    if (p === "/api/interleave/check") return handleInterleaveCheck(body || {});
    if (p === "/api/interleave/complete") return handleInterleaveComplete(body || {});
    if (p === "/api/root-cause") return handleRootCause(u.searchParams.get("nodeId") || u.searchParams.get("propId") || "");
    if (p === "/api/rosetta") return handleRosettaGet(u);
    if (p === "/api/rosetta/link") return handleRosettaLink(body || {});
    if (p === "/api/sync/status") return handleSyncStatus();
    if (p === "/api/sync/export") return handleSyncExport(body || {});
    if (p === "/api/sync/merge") return handleSyncMerge(body || {});
    if (p === "/api/profile/replay") return handleProfileReplay(body || {});
    if (p === "/api/derivation/diff") return handleDerivationDiff(body || {});
    if (p === "/api/review") return handleReview(body || {});
    if (p === "/api/profile/calibrate") {
      state = loadState();
      const lp = state.hub.learnerProfile || {};
      lp.calibration = { ...(lp.calibration || {}), completed: true, entryRamp: body.entryRamp || "balanced", challengeAppetite: body.challengeAppetite || "balanced", proseDensity: body.proseDensity || "standard", sessionTargetMinutes: Number(body.sessionMinutes || 30) };
      if (body.domainFluency && typeof body.domainFluency === "object") {
        const eloMap = { 0: 1150, 1: 1350, 2: 1550, 3: 1750 };
        lp.domains = lp.domains || {};
        for (const [dom, lvl] of Object.entries(body.domainFluency)) lp.domains[dom] = { ...(lp.domains[dom] || { events: 1 }), elo: eloMap[lvl] || 1450 };
      }
      const pol = recomputePolicy(lp, "normal");
      state.hub.learnerProfile = lp;
      saveState(state);
      return { ok: true, profile: lp, compiledPolicy: pol };
    }
    if (p === "/api/profile/override") {
      state = loadState();
      const lp = state.hub.learnerProfile || {};
      const ov = (body && body.overrides) || body || {};
      lp.userOverrides = { ...(lp.userOverrides || {}), ...ov };
      const pol = recomputePolicy(lp, "normal");
      state.hub.learnerProfile = lp;
      saveState(state);
      return { ok: true, profile: lp, compiledPolicy: pol };
    }
    if (p === "/api/whiteboard/unlock") {
      state = loadState();
      const wb = state.hub.whiteboard;
      const fr = (wb && wb.frames || []).find((f) => String(f.id) === String(body.frameId));
      if (!fr) return { ok: false, error: "Frame not found" };
      fr.locked = false;
      fr.gate = { ...(fr.gate || {}), prediction: String(body.prediction || ""), confidence: Number(body.confidence || 0.7), unlockedAt: new Date().toISOString() };
      if (!fr.body) fr.body = WB_REVEALS[fr.id] || "Prediction recorded — compare your reasoning against the formal invariant above.";
      wb.revision = Number(wb.revision || 0) + 1;
      saveState(state);
      return { ok: true, revision: wb.revision, frameId: fr.id, unlocked: true, frame: fr, session: wb };
    }
    if (p === "/api/whiteboard/pin") {
      state = loadState();
      const wb = state.hub.whiteboard;
      const pin = { id: "pin-" + Date.now(), frameId: body.frameId || null, stepIndex: Number(body.stepIndex || 0), note: String(body.note || ""), resolved: false, createdAt: new Date().toISOString() };
      wb.pins = [...(wb.pins || []), pin];
      wb.pendingPins = wb.pins.filter((x) => !x.resolved);
      wb.pendingPinCount = wb.pendingPins.length;
      wb.revision = Number(wb.revision || 0) + 1;
      saveState(state);
      return { ok: true, revision: wb.revision, pin, pendingPinCount: wb.pendingPinCount, session: wb };
    }
    if (p === "/api/whiteboard/push") {
      state = loadState();
      const wb = state.hub.whiteboard;
      const fIn = (body && body.frame) || body || {};
      const fr = { id: "wb-u-" + Date.now(), kind: fIn.kind || "derivation_step", title: fIn.title || "Working Step", body: fIn.body || "", stepIndex: (wb.frames || []).length, locked: false, payload: {} };
      wb.frames = [...(wb.frames || []), fr];
      wb.frameCount = wb.frames.length;
      wb.revision = Number(wb.revision || 0) + 1;
      saveState(state);
      return { ok: true, revision: wb.revision, frame: fr, session: wb };
    }
    if (p === "/api/whiteboard/clear") {
      state = loadState();
      const wb = state.hub.whiteboard;
      wb.frames = []; wb.pins = []; wb.pendingPins = []; wb.frameCount = 0; wb.pendingPinCount = 0; wb.revision = Number(wb.revision || 0) + 1;
      saveState(state);
      return { ok: true, cleared: true, revision: wb.revision, session: wb };
    }
    if (p === "/api/forks/check") {
      state = loadState();
      const fk = (state.hub.forks || []).find((f) => f.id === body.forkId);
      const rule = FORK_RULES[body.forkId];
      if (!fk || !rule) return { ok: false, error: "Fork not found" };
      const mv = normTxt(body.move);
      const correct = !body.revealFix && rule.ok.some((c) => mv === c || (mv.length >= 6 && mv.includes(c)));
      const repeatedBlunder = !correct && rule.trap.some((t) => mv === t);
      const lat = Number(body.latencyMs || 8000);
      if (correct) {
        fk.winCount = Number(fk.winCount || 0) + 1;
        fk.isDue = false;
        fk.nextReview = addDays(todayIso(), 7);
        promoteEvidence(state, fk.propId, 3, "fork_replay");
      } else if (repeatedBlunder) {
        fk.blunderRepeatCount = Number(fk.blunderRepeatCount || 0) + 1;
      }
      refreshDerived(state);
      saveState(state);
      return {
        ok: true, forkId: fk.id, correct, repeatedBlunder,
        refutation: correct ? "" : rule.refute,
        fixRevealed: Boolean(body.revealFix), winningMove: body.revealFix ? rule.fix : undefined,
        nextReview: fk.nextReview,
        latencyTrend: { latestMs: lat, compressionRatio: correct ? Number(Math.max(1.2, 18000 / Math.max(2500, lat)).toFixed(1)) : null }
      };
    }
    if (p === "/api/arena/submit") {
      state = loadState();
      const ar = (state.hub.arenas || []).find((a) => a.id === body.arenaId);
      if (!ar) return { ok: false, error: "Arena not found" };
      const ev = runArenaEval(ar.id, body.input);
      if (!ev.valid) return { ok: true, validAssumption: false, failedAssumptions: ev.failed, newlyKilled: [], killedTotalCount: ar.killedCount || 0, totalMutants: ar.totalMutants || 3, survivedCount: (ar.totalMutants || 3) - (ar.killedCount || 0) };
      const prev = new Set(ar.killedMutants || []);
      const newlyKilled = [];
      const killedTrapTags = {};
      for (const h of ev.hits) {
        killedTrapTags[h.id] = h.tag;
        if (!prev.has(h.id)) { prev.add(h.id); newlyKilled.push(h.id); }
      }
      ar.killedMutants = [...prev];
      ar.killedCount = ar.killedMutants.length;
      ar.survivedCount = Math.max(0, Number(ar.totalMutants || 3) - ar.killedCount);
      const allKilled = ar.survivedCount === 0;
      if (ar.killedCount / Math.max(1, ar.totalMutants || 3) >= 0.75) promoteEvidence(state, ar.propId, 3, "falsify_arena");
      refreshDerived(state);
      saveState(state);
      return { ok: true, validAssumption: true, newlyKilled, killedTrapTags, allKilled, killedTotalCount: ar.killedCount, totalMutants: ar.totalMutants || 3, survivedCount: ar.survivedCount };
    }
    if (p === "/api/arena/repair") {
      state = loadState();
      const ar = (state.hub.arenas || []).find((a) => a.id === body.arenaId);
      const lemma = String(body.lemma || "").trim();
      if (!ar || !lemma) return { ok: false, error: "Arena and lemma required" };
      const node = (state.graph.propositions || []).find((x) => x.id === ar.propId);
      if (node) { node.boundaryConditions = [...(node.boundaryConditions || []), lemma]; }
      saveState(state);
      return { ok: true, repair: { arenaId: ar.id, propId: ar.propId, lemma } };
    }
    if (p === "/api/blitz/build") return handleBlitzBuild(body && body.count);
    if (p === "/api/blitz/grade") {
      const kmap = blitzStore.get(body.blitzId) || {};
      const exp = kmap[body.itemId] || { killCue: "Governing invariant", firstMove: body.firstMoveAnswer, rejectedDecoy: "Surface shortcut", deepPrinciple: body.propId };
      const kcHit = normTxt(body.killCueAnswer) === normTxt(exp.killCue);
      const fmHit = normTxt(body.firstMoveAnswer) === normTxt(exp.firstMove);
      const dcHit = normTxt(body.rejectedDecoyAnswer) === normTxt(exp.rejectedDecoy);
      state = loadState();
      state.hub.decisions = [...(state.hub.decisions || []), { propId: body.propId, firstMoveHit: fmHit, killCueHit: kcHit, decoyHit: dcHit, decisionMs: Number(body.decisionMs || 9000), graded: true }];
      saveState(state);
      return {
        ok: true, decisionSec: Number((Number(body.decisionMs || 9000) / 1000).toFixed(1)), rejectedDecoyCorrect: dcHit,
        record: { propId: body.propId },
        expertLensDiff: { deepPrinciple: exp.deepPrinciple, killCue: { hit: kcHit, learner: body.killCueAnswer, expert: exp.killCue }, firstMove: { hit: fmHit, learner: body.firstMoveAnswer, expert: exp.firstMove }, rejectedDecoy: { hit: dcHit, learner: body.rejectedDecoyAnswer, expert: exp.rejectedDecoy } }
      };
    }
    if (p === "/api/derivation/check") {
      const dr = DERIV_STEPS[body.derivationId];
      const stSpec = dr && dr[body.stepId];
      const ans = normTxt(body.answer);
      const passed = Boolean(stSpec && stSpec.ok.some((c) => ans === c || ans.includes(c)));
      if (passed) {
        state = loadState();
        const dObj = (state.hub.derivations || []).find((x) => x.id === body.derivationId);
        if (dObj) { promoteEvidence(state, dObj.propId, 3, "derivation_blackout"); refreshDerived(state); saveState(state); }
      }
      return { ok: true, derivationId: body.derivationId, stepId: body.stepId, passed, canonicalExpr: passed ? stSpec.expr : null };
    }
    if (p === "/api/derivation/cut") {
      state = loadState();
      const dObj = (state.hub.derivations || []).find((x) => x.id === body.derivationId);
      if (!dObj) return { ok: false, error: "Derivation not found" };
      const steps = dObj.steps || [];
      const cutId = String(body.axiomId || "");
      const children = new Map(steps.map((s) => [s.id, []]));
      for (const s of steps) for (const dep of (s.dependsOn || [])) if (children.has(dep)) children.get(dep).push(s.id);
      const inv = new Set();
      const q = [...(children.get(cutId) || [])];
      while (q.length) { const cur = q.shift(); if (inv.has(cur)) continue; inv.add(cur); for (const nx of (children.get(cur) || [])) if (!inv.has(nx)) q.push(nx); }
      const order = new Map(steps.map((s, i) => [s.id, i]));
      const invalidatedSteps = [...inv].sort((a, b) => order.get(a) - order.get(b));
      const survivingSteps = steps.map((s) => s.id).filter((id) => id !== cutId && !inv.has(id));
      const earliestCands = invalidatedSteps.filter((id) => {
        const stp = steps.find((s) => s.id === id);
        return (stp.dependsOn || []).includes(cutId) && !(stp.dependsOn || []).some((d) => inv.has(d));
      });
      const earliestBreak = earliestCands[0] || invalidatedSteps[0] || null;
      const passed = body.guessStepId ? earliestCands.includes(body.guessStepId) : false;
      let evidenceRecorded = false;
      if (passed) { evidenceRecorded = promoteEvidence(state, dObj.propId, 4, "derivation_axiom_cut"); refreshDerived(state); saveState(state); }
      return { ok: true, passed, earliestBreak, invalidatedSteps, survivingSteps, evidenceRecorded, explanation: passed ? `Cutting ${cutId} breaks ${earliestBreak} first (${invalidatedSteps.length} invalidated, ${survivingSteps.length} surviving).` : `Cutting ${cutId} invalidates ${invalidatedSteps.join(", ") || "none"}; earliest break is ${earliestBreak || "none"}.` };
    }
    if (p === "/api/viva/start") return handleVivaStart(body && body.propId);
    if (p === "/api/viva/turn") return handleVivaTurn(body || {});
    if (p === "/api/tracker/topic") {
      state = loadState();
      const tr = (state.hub.chapterTrackers || []).find((t) => t.slug === body.slug);
      if (tr) {
        const tp = (tr.topics || []).find((x) => x.id === body.topicId);
        if (tp) tp.status = body.status;
        tr.topicsMastered = (tr.topics || []).filter((x) => x.status === "mastered").length;
        tr.pct = Math.round(((tr.topicsMastered + (tr.questionsSolved || 0)) / Math.max(1, (tr.topicsTotal || 1) + (tr.questionsTotal || 0))) * 100);
        if (state.hub.activeChapterTracker && state.hub.activeChapterTracker.slug === tr.slug) state.hub.activeChapterTracker = clone(tr);
        saveState(state);
      }
      return { ok: true, slug: body.slug, topicId: body.topicId, status: body.status };
    }
    if (p === "/api/exams") {
      state = loadState();
      const today = new Date().toISOString().slice(0, 10);
      const dayMs = 86400000;
      let list = Array.isArray(state.exams) ? state.exams : [];
      if (method === "POST") {
        const name = String((body && body.name) || "").trim().slice(0, 80);
        const key = name.toLowerCase();
        if (body && body.action === "remove") {
          if (!list.some((e) => e.name.toLowerCase() === key)) return { ok: false, error: `No exam named '${name}'.` };
          list = list.filter((e) => e.name.toLowerCase() !== key);
        } else {
          const d = String((body && body.examDate) || "").slice(0, 10);
          if (!name) return { ok: false, error: "Give the exam a name." };
          if (!/^\d{4}-\d{2}-\d{2}$/.test(d)) return { ok: false, error: "Give the exam date as YYYY-MM-DD." };
          if (d <= today) return { ok: false, error: `${d} is not in the future, so there is nothing to pace toward.` };
          list = list.filter((e) => e.name.toLowerCase() !== key).concat([{ name, examDate: d }]);
        }
        state.exams = list;
        saveState(state);
      }
      const exams = list
        .map((e) => ({ ...e, daysAway: Math.round((Date.parse(e.examDate) - Date.parse(today)) / dayMs) }))
        .sort((a, b) => a.examDate.localeCompare(b.examDate));
      return { ok: true, exams };
    }
    if (p === "/api/tracker/milestone") {
      state = loadState();
      const tr = (state.hub.chapterTrackers || []).find((t) => t.slug === body.slug);
      if (tr) {
        tr.milestones = { ...(tr.milestones || {}), [body.key]: Boolean(body.value) };
        if (state.hub.activeChapterTracker && state.hub.activeChapterTracker.slug === tr.slug) state.hub.activeChapterTracker = clone(tr);
        saveState(state);
      }
      return { ok: true, slug: body.slug, key: body.key, value: Boolean(body.value) };
    }
    if (p === "/api/recover") {
      state = loadState();
      const cap = Math.max(1, Number((body && body.dailyCap) || 15));
      let moved = 0;
      (state.graph.propositions || []).forEach((pr, idx) => {
        if (pr.nextReview && pr.nextReview <= todayIso()) { pr.nextReview = addDays(todayIso(), 1 + (idx % 4)); moved += 1; }
      });
      refreshDerived(state);
      saveState(state);
      return { ok: true, applied: true, dailyCap: cap, rescheduledCount: moved };
    }
    if (p === "/api/triage/plan") {
      state = loadState();
      const hrs = Number((body && body.hours) || 48), slp = Number((body && body.sleepHours) || 7);
      const props = state.graph.propositions || [];
      const salvage = props.filter((x) => x.status === "misconception" || x.status === "shaky").map((x) => ({ propId: x.id, title: x.title, status: x.status, chapter: x.chapter, allocatedMin: 35 }));
      const protect = props.filter((x) => x.status === "solid" && x.nextReview && x.nextReview <= addDays(todayIso(), 3)).map((x) => ({ propId: x.id, title: x.title, status: x.status, chapter: x.chapter, allocatedMin: 15 }));
      const cut = props.filter((x) => x.status === "planned").map((x) => ({ propId: x.id, title: x.title, status: x.status, chapter: x.chapter }));
      return { ok: true, hoursLeft: hrs, sleepProtectedHours: Math.round((hrs / 24) * slp), usableStudyHours: Math.max(2, Math.round(hrs * 0.45)), buckets: { salvage_70pct: salvage, protect_20pct: protect, explicitly_cut: cut }, blocks: [{ order: 1, slot: "Block 1 (90m)", goal: "Repair Raft Figure 8 + Up-to-Date Log causal root" }, { order: 2, slot: "Block 2 (90m)", goal: "Kill remaining SHM & Attention Arena mutants" }] };
    }
    if (p === "/api/triage/crib-sheet") return { ok: true, compiled: false, antiMasteryMeta: { excludedSolidCount: 19 }, summary: "Demo sandbox preview — excludes 19 already-solid concepts." };
    if (p === "/api/export/pocket") return { ok: true, pocketUrl: "./pocket.html", url: "./pocket.html", cardCount: 20 };
    if (p === "/api/demo/load" || p === "/api/pack/import") return { ok: true, summary: "All 3 flagship Starter Packs (Raft, SHM Physics, Transformers) are pre-loaded in this interactive demo." };
    if (p === "/api/pack/export") return { ok: true, relPath: "raft-consensus.syntopik-pack.zip", summary: "Demo sandbox: zero-PII pack schema verified (install locally via syntopik pack export to write zip)." };
    if (p === "/api/export/obsidian") return { ok: true, propositionsExported: (state.graph.propositions || []).length, summary: "Demo sandbox: 30 concepts ready for Obsidian vault sync (run syntopik obsidian sync locally)." };
    if (p === "/api/export/dossier") return { ok: true, relPath: "mastery-dossier.html", verifiedTransferPct: (state.hub.evidenceSummary && state.hub.evidenceSummary.verifiedTransferPct) || 33.3 };
    if (p === "/api/import") return {
      ok: true,
      demo: true,
      importedCount: 0,
      summary: "Read-only demo sandbox: run `syntopik drop <file-or-url>` or `syntopik serve` locally to import PDFs, Anki .apkg, Jupyter .ipynb, CSV/TSV, Markdown folders, YouTube, arXiv, or GitHub repos."
    };
    return { ok: true, demo: true };
  }

  // Intercept EventSource for /api/events (zero network requests!)
  const OrigES = window.EventSource;
  window.EventSource = function DemoEventSource(url, cfg) {
    const u = String(url || "");
    if (u.startsWith("/api/") || u.includes("/api/events")) {
      const listeners = new Map();
      const es = {
        url: u, readyState: 1, onmessage: null, onerror: null, onopen: null,
        addEventListener(t, fn) { if (!listeners.has(t)) listeners.set(t, []); listeners.get(t).push(fn); },
        removeEventListener(t, fn) { const a = listeners.get(t); if (a) listeners.set(t, a.filter((x) => x !== fn)); },
        close() { es.readyState = 2; }
      };
      queueMicrotask(() => {
        const ev = { data: JSON.stringify({ type: "ready", demo: true }) };
        if (typeof es.onopen === "function") es.onopen({});
        for (const fn of (listeners.get("message") || [])) fn(ev);
      });
      return es;
    }
    return OrigES ? new OrigES(url, cfg) : { close() {} };
  };

  // Intercept fetch('/api/...') with localStorage-backed responses
  const origFetch = window.fetch ? window.fetch.bind(window) : null;
  window.fetch = async function demoFetch(input, init) {
    const urlStr = typeof input === "string" ? input : (input && input.url) || "";
    if (urlStr.startsWith("/api/") || /^https?:\/\/[^/]+\/api\//.test(urlStr)) {
      const method = String((init && init.method) || (input && input.method) || "GET").toUpperCase();
      let body = {};
      if (init && typeof init.body === "string" && init.body.trim()) {
        try { body = JSON.parse(init.body); } catch { body = {}; }
      }
      const payload = routeApi(method, urlStr, body);
      return new Response(JSON.stringify(payload), { status: payload && payload.ok === false ? 400 : 200, headers: { "Content-Type": "application/json" } });
    }
    if (origFetch) return origFetch(input, init);
    return new Response(JSON.stringify({ ok: false, error: "Network unavailable" }), { status: 503, headers: { "Content-Type": "application/json" } });
  };

  window.__SYNTOPIK_SHIM__ = {
    version: "3.2.0-demo",
    storageKey: KEY,
    reset() {
      try { localStorage.removeItem(KEY); } catch { /* ignore */ }
      window.location.reload();
    }
  };

  document.addEventListener("click", (ev) => {
    if (ev.target && ev.target.closest && ev.target.closest("#demoResetBtn")) {
      window.__SYNTOPIK_SHIM__.reset();
    }
  });
})();
""".strip()


# ---------------------------------------------------------------------------
# Phase E: Assemble docs/index.html, docs/viewer.html, docs/pocket.html, .nojekyll
# ---------------------------------------------------------------------------

_JS_REGEX_KEYWORDS: frozenset[str] = frozenset(
    {
        "return",
        "case",
        "throw",
        "else",
        "do",
        "in",
        "instanceof",
        "typeof",
        "void",
        "delete",
        "new",
        "yield",
        "await",
        "of",
    }
)

_JS_MULTI_PUNCT: tuple[str, ...] = (
    ">>>=",
    "===",
    "!==",
    ">>>",
    "**=",
    "<<=",
    ">>=",
    "&&=",
    "||=",
    "??=",
    "...",
    "==",
    "!=",
    "<=",
    ">=",
    "=>",
    "&&",
    "||",
    "??",
    "++",
    "--",
    "+=",
    "-=",
    "*=",
    "/=",
    "%=",
    "**",
    "<<",
    ">>",
    "&=",
    "|=",
    "^=",
    "?.",
)


def _is_js_word_char(ch: str) -> bool:
    return ch.isalnum() or ch in ("_", "$") or ord(ch) > 127


def _js_can_start_regex(prev_kind: str | None, prev_val: str) -> bool:
    if prev_kind is None:
        return True
    if prev_kind == "ident":
        return prev_val in _JS_REGEX_KEYWORDS
    if prev_kind == "punct":
        return prev_val not in (")", "]", "}", "++", "--")
    return False


def _js_needs_space(prev_tok: str, curr_tok: str) -> bool:
    if not prev_tok or not curr_tok:
        return False
    a = prev_tok[-1]
    b = curr_tok[0]
    if _is_js_word_char(a) and _is_js_word_char(b):
        return True
    if prev_tok in _JS_REGEX_KEYWORDS and b in "/+-":
        return True
    if (a == "+" and b == "+") or (a == "-" and b == "-"):
        return True
    if (a == "/" and b in "/*") or (a == "*" and b == "/"):
        return True
    if (a == "<" and b == "!") or (a == "-" and b == ">") or (a == "!" and b == "-"):
        return True
    return False


def _compact_js(js_text: str) -> str:
    """Lexical JS comment stripper & safe horizontal whitespace compactor.

    Preserves single/double strings, template literals (with nested `${...}`),
    regex literals (with `[...]` classes), ASI newlines, and `+`/`-`/`/` token
    boundaries. Verified against `node --check` on every generated `<script>`.
    """
    n = len(js_text)
    i = 0
    out: list[str] = []
    line_last = ""
    had_space = False
    prev_kind: str | None = None
    prev_val = ""
    tpl_brace_stack: list[int] = []

    def emit(tok: str, kind: str | None = None) -> None:
        nonlocal line_last, had_space, prev_kind, prev_val
        if had_space and _js_needs_space(line_last, tok):
            out.append(" ")
        out.append(tok)
        line_last = tok
        had_space = False
        if kind is not None:
            prev_kind = kind
            prev_val = tok

    def emit_newline() -> None:
        nonlocal line_last, had_space
        # Eliding newlines directly after `;`, `{`, or `,` is strictly ASI-safe in ECMAScript
        if line_last in (";", "{", ","):
            had_space = False
            return
        if out and out[-1] != "\n":
            out.append("\n")
        line_last = ""
        had_space = False

    def scan_template_chunk(start_idx: int) -> int:
        """Scan template literal raw text starting right after `` ` `` or `${...}` closing `}`."""
        nonlocal line_last, had_space, prev_kind, prev_val
        j = start_idx
        buf: list[str] = []
        while j < n:
            ch = js_text[j]
            if ch == "\\":
                if j + 1 < n:
                    buf.append(js_text[j : j + 2])
                    j += 2
                else:
                    buf.append(ch)
                    j += 1
                continue
            if ch == "`":
                buf.append("`")
                chunk = "".join(buf)
                out.append(chunk)
                line_last = "`"
                had_space = False
                prev_kind = "value"
                prev_val = "`"
                return j + 1
            if ch == "$" and j + 1 < n and js_text[j + 1] == "{":
                buf.append("${")
                out.append("".join(buf))
                line_last = "{"
                had_space = False
                prev_kind = "punct"
                prev_val = "{"
                tpl_brace_stack.append(0)
                return j + 2
            if ch == "\r":
                j += 1
                continue
            if ch == "\n":
                # Trim trailing spaces on line inside template chunk, keep newline, skip leading indent
                while buf and buf[-1] in (" ", "\t"):
                    buf.pop()
                if buf and buf[-1] != "\n":
                    buf.append("\n")
                j += 1
                while j < n and js_text[j] in (" ", "\t", "\r", "\n"):
                    j += 1
                continue
            buf.append(ch)
            j += 1
        out.append("".join(buf))
        return j

    while i < n:
        ch = js_text[i]

        # Newlines (preserve for ASI safety; collapse blank lines)
        if ch in ("\n", "\r"):
            emit_newline()
            i += 1
            continue

        # Horizontal whitespace
        if ch in (" ", "\t", "\f", "\v"):
            had_space = True
            i += 1
            continue

        # Single-line comment `// ...`
        if ch == "/" and i + 1 < n and js_text[i + 1] == "/":
            i += 2
            while i < n and js_text[i] not in ("\n", "\r"):
                i += 1
            continue

        # Block comment `/* ... */`
        if ch == "/" and i + 1 < n and js_text[i + 1] == "*":
            end = js_text.find("*/", i + 2)
            if end < 0:
                break
            if "\n" in js_text[i + 2 : end] or "\r" in js_text[i + 2 : end]:
                emit_newline()
            else:
                had_space = True
            i = end + 2
            continue

        # Single- or double-quoted string literal
        if ch in ("'", '"'):
            quote = ch
            j = i + 1
            while j < n:
                c = js_text[j]
                if c == "\\":
                    j += 2
                    continue
                if c == quote:
                    j += 1
                    break
                j += 1
            emit(js_text[i:j], "value")
            i = j
            continue

        # Template literal start `` ` ``
        if ch == "`":
            if had_space and _js_needs_space(line_last, "`"):
                out.append(" ")
            out.append("`")
            had_space = False
            i = scan_template_chunk(i + 1)
            continue

        # Template expression braces `{` / `}`
        if ch == "{" and tpl_brace_stack:
            tpl_brace_stack[-1] += 1
            emit("{", "punct")
            i += 1
            continue
        if ch == "}" and tpl_brace_stack:
            if tpl_brace_stack[-1] == 0:
                tpl_brace_stack.pop()
                out.append("}")
                line_last = "}"
                had_space = False
                i = scan_template_chunk(i + 1)
                continue
            tpl_brace_stack[-1] -= 1
            emit("}", "punct")
            i += 1
            continue

        # Regex literal `/pattern/flags`
        if ch == "/" and _js_can_start_regex(prev_kind, prev_val):
            j = i + 1
            in_class = False
            valid_re = False
            while j < n:
                c = js_text[j]
                if c in ("\n", "\r"):
                    break
                if c == "\\":
                    j += 2
                    continue
                if c == "[" and not in_class:
                    in_class = True
                    j += 1
                    continue
                if c == "]" and in_class:
                    in_class = False
                    j += 1
                    continue
                if c == "/" and not in_class:
                    j += 1
                    while j < n and js_text[j].isalpha():
                        j += 1
                    valid_re = True
                    break
                j += 1
            if valid_re:
                emit(js_text[i:j], "value")
                i = j
                continue

        # Identifier or keyword
        if ch.isalpha() or ch in ("_", "$") or ord(ch) > 127:
            j = i + 1
            while j < n and _is_js_word_char(js_text[j]):
                j += 1
            emit(js_text[i:j], "ident")
            i = j
            continue

        # Numeric literal (decimal, hex, binary, octal, scientific)
        if ch.isdigit() or (ch == "." and i + 1 < n and js_text[i + 1].isdigit()):
            j = i + 1
            while j < n:
                c = js_text[j]
                if c in ("e", "E") and j + 1 < n and js_text[j + 1] in ("+", "-"):
                    j += 2
                    continue
                if c.isalnum() or c in (".", "_"):
                    j += 1
                    continue
                break
            emit(js_text[i:j], "value")
            i = j
            continue

        # Multi-character punctuators
        matched_multi = ""
        for mp in _JS_MULTI_PUNCT:
            if js_text.startswith(mp, i):
                matched_multi = mp
                break
        if matched_multi:
            emit(matched_multi, "punct")
            i += len(matched_multi)
            continue

        # Single-character punctuator
        emit(ch, "punct")
        i += 1

    return "".join(out).strip()


def _compact_style_blocks(html_text: str) -> str:
    """Safely strip CSS comments and redundant whitespace inside <style> blocks only (preserves calc() +/- spacing)."""
    def _shrink(m: re.Match[str]) -> str:
        css = m.group(1)
        css = re.sub(r"/\*[\s\S]*?\*/", "", css)
        css = re.sub(r"\s+", " ", css)
        css = re.sub(r"\s*([{}:;,>~])\s*", r"\1", css)
        css = css.replace(";}", "}")
        return f"<style>{css.strip()}</style>"
    return re.sub(r"<style>([\s\S]*?)</style>", _shrink, html_text, count=1)


def _compact_lines(text: str) -> str:
    """Trim leading/trailing line indentation and blank lines cleanly while preserving newline semantics."""
    return "\n".join(ln.strip() for ln in text.splitlines() if ln.strip())


def _compact_html_markup(html_part: str) -> str:
    """Strip HTML comments and redundant line indentation from non-script HTML markup."""
    stripped = re.sub(r"<!--[\s\S]*?-->", "", html_part)
    lines = _compact_lines(stripped)
    return re.sub(r">\n<", "><", lines)


def _compact_script_sections(fragment: str) -> str:
    """Apply safe lexical JS compaction to every inline `<script>` block inside an HTML fragment."""
    def _shrink_script(m: re.Match[str]) -> str:
        open_tag, body = m.group(1), m.group(2)
        return f"{open_tag}\n{_compact_js(body)}\n</script>"

    compacted = re.sub(
        r"(<script(?:\s+[^>]*)?>)([\s\S]*?)</script>",
        _shrink_script,
        fragment,
        flags=re.IGNORECASE,
    )
    return _compact_lines(compacted)


def _inject_demo_reset_pill(src: str, target_name: str) -> str:
    """Insert the calm Interactive Demo Reset pill directly after `#liveStatusPill`."""
    pill_re = re.compile(
        r'(<(?:button|span)\b[^>]*\bid="liveStatusPill"[^>]*>.*?</(?:button|span)>)',
        re.DOTALL,
    )
    m = pill_re.search(src)
    if not m:
        raise RuntimeError(f"Could not find #liveStatusPill in {target_name}")
    demo_pill_html = (
        m.group(1)
        + '\n    <button type="button" id="demoResetBtn" class="demo-reset-pill" '
        + 'title="Interactive browser sandbox (localStorage) — click to reset all demo progress">'
        + "Interactive Demo · Reset</button>"
    )
    return src[: m.start()] + demo_pill_html + src[m.end() :]


def _build_index_html(study_graph: dict[str, Any], study_hub: dict[str, Any]) -> str:
    src = (ROOT / "dashboard.html").read_text(encoding="utf-8")

    # 1. Inject subtle Demo Reset pill styling & compact <style> whitespace cleanly
    if "</style>" not in src:
        raise RuntimeError("dashboard.html missing </style>")
    src = src.replace("</style>", DEMO_RESET_CSS + "\n</style>", 1)
    src = _compact_style_blocks(src)

    # 2. Rewrite standalone Graph link for GitHub Pages relative routing
    src = src.replace('href="./knowledge-graph/viewer.html"', 'href="./viewer.html"')
    src = src.replace('href="/knowledge-graph/viewer.html"', 'href="./viewer.html"')
    src = src.replace('href="knowledge-graph/viewer.html"', 'href="./viewer.html"')

    # 3. Add calm "Interactive Demo · Reset" pill right beside #liveStatusPill in .topbar-right
    src = _inject_demo_reset_pill(src, "dashboard.html")

    # 4. Compact HTML comments/markup & JS blocks safely so docs/index.html stays comfortably <= 645 KB
    script_tag = '<script src="./knowledge-graph/graph-data.js"></script>'
    if script_tag not in src:
        raise RuntimeError("Could not find graph-data.js script tag in dashboard.html")
    pre_script, post_script = src.split(script_tag, 1)
    pre_script = _compact_html_markup(pre_script)
    post_script = _compact_script_sections(post_script)
    compact_shim = _compact_js(SHIM_JS)

    # 5. Inline window.__STUDY_GRAPH__, window.__STUDY_HUB__, and window.__SYNTOPIK_SHIM__
    inline_block = (
        "\n<script>\n"
        f"window.__STUDY_GRAPH__ = {_safe_script_json(study_graph)};\n"
        f"window.__STUDY_HUB__ = {_safe_script_json(study_hub)};\n"
        "</script>\n"
        f"<script>\n{compact_shim}\n</script>\n"
    )
    return pre_script + inline_block + post_script


def _build_viewer_html(study_graph: dict[str, Any], study_hub: dict[str, Any]) -> str:
    src = (ROOT / "knowledge-graph" / "viewer.html").read_text(encoding="utf-8")

    # 1. Inject subtle Demo Reset pill styling + <=1380px header/HUD compaction so viewer stays <=1280px
    if "</style>" not in src:
        raise RuntimeError("viewer.html missing </style>")
    src = src.replace("</style>", DEMO_RESET_CSS + VIEWER_DEMO_COMPACT_CSS + "\n</style>", 1)

    # 2. Rewrite back-link to ./index.html
    src = src.replace('href="../dashboard.html"', 'href="./index.html"')

    # 3. Add calm "Interactive Demo · Reset" pill next to #liveStatusPill
    src = _inject_demo_reset_pill(src, "viewer.html")

    # 4. Slim hub for viewer while keeping full Rosetta chords & localStorage parity
    viewer_hub = {
        "schemaVersion": 5,
        "generatedAt": DEMO_NOW_ISO,
        "today": DEMO_TODAY,
        "workspaceName": DEMO_WORKSPACE_NAME,
        "stats": study_hub.get("stats", {}),
        "leeches": study_hub.get("leeches", []),
        "evidenceSummary": study_hub.get("evidenceSummary", {}),
        "rootCauseHotspots": study_hub.get("rootCauseHotspots", {}),
        "rosetta": study_hub.get("rosetta", {}),
    }

    script_tag = '<script src="./graph-data.js"></script>'
    if script_tag not in src:
        raise RuntimeError("Could not find graph-data.js script tag in viewer.html")
    inline_block = (
        "<script>\n"
        f"window.__STUDY_GRAPH__ = {_safe_script_json(study_graph)};\n"
        f"window.__STUDY_HUB__ = {_safe_script_json(viewer_hub)};\n"
        "</script>\n"
        f"<script>\n{_compact_js(SHIM_JS)}\n</script>"
    )
    src = src.replace(script_tag, inline_block, 1)
    return src


def _build_pocket_html(ws: Path, kg: Path, out_path: Path) -> dict[str, Any]:
    res = export_pocket_html(
        workspace=ws,
        out_path=out_path,
        limit=20,
        kg_dir=kg,
        include_upcoming=True,
        title="Syntopik Pocket — Live Demo",
        api_base="",
    )
    txt = out_path.read_text(encoding="utf-8")
    # Deterministic generatedAt & deckId
    txt = re.sub(r'"generatedAt":\s*"[^"]+"', f'"generatedAt": "{DEMO_NOW_ISO}"', txt, count=1)
    # Add subtle relative link back to ./index.html and hide LAN sync button on static GitHub Pages
    txt = txt.replace(
        '<button class="ibtn" id="undoBtn" type="button" title="Undo last rating">Undo</button>',
        '<a class="ibtn" href="./index.html" style="text-decoration:none;">← Studio</a>\n    '
        '<button class="ibtn" id="undoBtn" type="button" title="Undo last rating">Undo</button>',
        1,
    )
    txt = txt.replace(
        'if (DECK.apiBase || (window.location && /^https?:/.test(window.location.protocol))) {\n  lanBtn.style.display = "inline-block";\n}',
        'if (DECK.apiBase) {\n  lanBtn.style.display = "inline-block";\n}',
        1,
    )
    out_path.write_text(txt, encoding="utf-8")
    res["bytes"] = out_path.stat().st_size
    return res


def build_demo_site(out_dir: Path) -> dict[str, Any]:
    out_dir = out_dir.expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="synt-demo-site-") as tmp:
        ws = Path(tmp) / "ws"
        kg = ws / "knowledge-graph"
        ch = ws / "chapters"
        _unpack_starter_packs(ws, kg, ch)
        _seed_45_day_history(ws, kg, ch)
        study_graph, study_hub = _build_demo_payloads(ws, kg, ch)

        index_path = out_dir / "index.html"
        viewer_path = out_dir / "viewer.html"
        pocket_path = out_dir / "pocket.html"
        nojekyll_path = out_dir / ".nojekyll"

        index_html = _build_index_html(study_graph, study_hub)
        viewer_html = _build_viewer_html(study_graph, study_hub)

        index_path.write_text(index_html, encoding="utf-8")
        viewer_path.write_text(viewer_html, encoding="utf-8")
        pocket_meta = _build_pocket_html(ws, kg, pocket_path)
        nojekyll_path.write_bytes(b"")

    return {
        "ok": True,
        "outDir": str(out_dir),
        "files": {
            "index.html": index_path.stat().st_size,
            "viewer.html": viewer_path.stat().st_size,
            "pocket.html": pocket_path.stat().st_size,
            ".nojekyll": nojekyll_path.stat().st_size,
        },
        "counts": {
            "propositions": len(study_graph.get("propositions") or []),
            "chains": len(study_graph.get("chains") or []),
            "chapters": len(study_hub.get("chapters") or []),
            "explorables": sum(len(v) for v in (study_hub.get("explorables") or {}).values()),
            "forks": len(study_hub.get("forks") or []),
            "arenas": len(study_hub.get("arenas") or []),
            "derivations": len(study_hub.get("derivations") or []),
            "whiteboardFrames": len((study_hub.get("whiteboard") or {}).get("frames") or []),
            "causalSuspects": sum(
                1 for v in ((study_hub.get("rootCauseHotspots") or {}).get("byProp") or {}).values() if float(v) >= 0.35
            ),
            "rosettaChords": len((study_hub.get("rosetta") or {}).get("chords") or []),
            "pocketCards": pocket_meta.get("cardCount", 0),
        },
    }


# ---------------------------------------------------------------------------
# Strict Static Verification (--check)
# ---------------------------------------------------------------------------


def _verify_inline_scripts_with_node(html_text: str, filename: str) -> list[str]:
    """Validate syntax of every inline `<script>` block via `node --check` when node is installed."""
    node_bin = shutil.which("node")
    if not node_bin:
        return []
    errs: list[str] = []
    blocks = re.findall(r"<script(?:\s+[^>]*)?>([\s\S]*?)</script>", html_text, flags=re.IGNORECASE)
    with tempfile.TemporaryDirectory(prefix="synt-js-chk-") as tmp:
        for idx, body in enumerate(blocks):
            if not body.strip():
                continue
            js_path = Path(tmp) / f"{filename}.script{idx}.js"
            js_path.write_text(body, encoding="utf-8")
            proc = subprocess.run([node_bin, "--check", str(js_path)], capture_output=True, text=True, check=False)
            if proc.returncode != 0:
                msg = (proc.stderr or proc.stdout or "syntax error").strip().splitlines()[0]
                errs.append(f"{filename}: <script>[{idx}] failed node --check: {msg}")
    return errs


def check_demo_site(out_dir: Path) -> dict[str, Any]:
    out_dir = out_dir.expanduser().resolve()
    errors: list[str] = []
    files_report: dict[str, Any] = {}

    required = ("index.html", "viewer.html", "pocket.html", ".nojekyll")
    for name in required:
        fp = out_dir / name
        if not fp.is_file():
            errors.append(f"Missing required artifact: {name}")
            continue
        size = fp.stat().st_size
        files_report[name] = {"bytes": size}
        if name == ".nojekyll":
            continue
        txt = fp.read_text(encoding="utf-8")
        if name == "index.html" and size > MAX_INDEX_BYTES:
            errors.append(f"index.html exceeds {MAX_INDEX_BYTES} B ceiling: {size} B")

        # 1. Zero external CDNs / external asset loads
        ext_hits = EXTERNAL_RESOURCE_RE.findall(txt)
        if ext_hits:
            errors.append(f"{name}: external CDN/resource reference(s) detected: {ext_hits[:5]}")

        # 2. Zero eval() / new Function()
        js_hits = DANGEROUS_JS_RE.findall(txt)
        if js_hits:
            errors.append(f"{name}: disallowed eval/new Function detected: {js_hits[:5]}")

        # 3. Zero personal strings or local user paths
        abs_hits = ABS_USER_PATH_RE.findall(txt)
        if abs_hits:
            errors.append(f"{name}: forbidden local user path leaked: {abs_hits[:5]}")
        for pstr in FORBIDDEN_PERSONAL_STRINGS:
            if pstr in txt:
                errors.append(f"{name}: forbidden personal string leaked: {pstr!r}")

        # 4. Zero sealed secret keys in HTML
        for stok in FORBIDDEN_HTML_SECRET_TOKENS:
            if stok in txt:
                errors.append(f"{name}: forbidden sealed secret token present: {stok!r}")

        # 5. Verify every inline <script> block passes `node --check`
        if name in ("index.html", "viewer.html"):
            errors.extend(_verify_inline_scripts_with_node(txt, name))

        # 6. Relative navigation & shim assertions
        if name == "index.html":
            if 'href="./viewer.html"' not in txt:
                errors.append("index.html: missing rewritten './viewer.html' link")
            if "knowledge-graph/viewer.html" in txt:
                errors.append("index.html: unrewritten 'knowledge-graph/viewer.html' link remains")
            if 'src="./knowledge-graph/graph-data.js"' in txt:
                errors.append("index.html: external graph-data.js script tag still present")
            if 'id="demoResetBtn"' not in txt:
                errors.append("index.html: missing 'Interactive Demo · Reset' button")
            if "window.__SYNTOPIK_SHIM__" not in txt:
                errors.append("index.html: missing window.__SYNTOPIK_SHIM__")
            if '"schemaVersion":5' not in txt:
                errors.append("index.html: missing schemaVersion: 5 payload")
            if 'esc(res.newlyKilled.join(", "))' not in txt:
                errors.append("index.html: missing Arena XSS fix esc(res.newlyKilled.join(\", \"))")
            if 'value="suspend-all"' not in txt or 'value="replace"' in txt:
                errors.append("index.html: pack import selector must use 'suspend-all' (not 'replace')")
            if "resolveSafeOcclusionHref" not in txt or "validateRpnArray" not in txt:
                errors.append("index.html: missing Wave 4B SVG/RPN hardening helpers")
            for v31_tok in ('"second_order_oscillator"', '"boltzmann_softmax"', "/api/rosetta/link", "/api/sync/merge", "/api/profile/replay", "/api/derivation/diff"):
                if v31_tok not in txt:
                    errors.append(f"index.html: missing v3.1 token {v31_tok!r}")
            for v32_tok in ("/api/readiness", "/api/interleave/build", "/api/interleave/check", "/api/interleave/complete", "mcpHttpEndpoint", "examReadinessCard", "agentCompatModal", "zeroStateLaunchpad"):
                if v32_tok not in txt:
                    errors.append(f"index.html: missing v3.2 token {v32_tok!r}")
        elif name == "viewer.html":
            if 'href="./index.html"' not in txt:
                errors.append("viewer.html: missing rewritten './index.html' back-link")
            if 'src="./graph-data.js"' in txt:
                errors.append("viewer.html: external graph-data.js script tag still present")
            if 'id="demoResetBtn"' not in txt:
                errors.append("viewer.html: missing 'Interactive Demo · Reset' button")
            if '"second_order_oscillator"' not in txt or '"boltzmann_softmax"' not in txt:
                errors.append("viewer.html: missing seeded Rosetta chords")
        elif name == "pocket.html":
            if 'href="./index.html"' not in txt:
                errors.append("pocket.html: missing './index.html' Studio back-link")

    return {
        "ok": len(errors) == 0,
        "outDir": str(out_dir),
        "maxIndexBytes": MAX_INDEX_BYTES,
        "files": files_report,
        "errors": errors,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="build_demo_site.py",
        description="Build & verify Syntopik v3.2 1-Click Zero-Install Live GitHub Pages Demo Site.",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=ROOT / "docs",
        help="Output directory (default: docs/)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Run strict verification checks after building (size, zero CDNs, zero eval, zero PII/secrets).",
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Verify existing output files without rebuilding.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON output.",
    )
    args = parser.parse_args(argv)

    build_res: dict[str, Any] | None = None
    check_res: dict[str, Any] | None = None

    if not args.check_only:
        build_res = build_demo_site(args.out_dir)
    if args.check or args.check_only:
        check_res = check_demo_site(args.out_dir)

    ok = (build_res["ok"] if build_res else True) and (check_res["ok"] if check_res else True)
    summary = {"ok": ok, "build": build_res, "check": check_res}

    if args.json:
        print(json.dumps(summary, indent=2, ensure_ascii=False))
    else:
        if build_res:
            f = build_res["files"]
            c = build_res["counts"]
            print(
                f"Built Syntopik v3.2 Live Demo Site → {build_res['outDir']}\n"
                f"  index.html  : {f['index.html']:,} B (ceiling {MAX_INDEX_BYTES:,} B)\n"
                f"  viewer.html : {f['viewer.html']:,} B\n"
                f"  pocket.html : {f['pocket.html']:,} B ({c['pocketCards']} commute cards)\n"
                f"  .nojekyll   : {f['.nojekyll']} B\n"
                f"  content     : {c['propositions']} props · {c['chains']} chains · {c['chapters']} chapters · "
                f"{c['rosettaChords']} Rosetta chords · {c['explorables']} :::explore labs · {c['forks']} forks · {c['arenas']} arenas · "
                f"{c['derivations']} derivations · {c['whiteboardFrames']} WB frames · {c['causalSuspects']} causal suspects"
            )
        if check_res:
            tag = "PASS" if check_res["ok"] else "FAIL"
            print(f"Static Check: {tag}")
            for err in check_res["errors"]:
                print(f"  ERROR: {err}", file=sys.stderr)

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
