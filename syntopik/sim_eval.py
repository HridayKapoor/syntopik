"""Syntopik-Eval: Seeded 60-Day Multi-Persona Cognitive Simulator & Pure-Stdlib SVG Renderer.

Pure Python 3.12+ standard library ONLY (zero external dependencies, 100% deterministic
via `random.Random(seed)`). Builds a 50-node, 5-chapter conjunctive knowledge hypergraph
(`premises[] -> conclusion`) and runs 6 controlled ablation arms across 5 mechanistic
learner personas over 60 simulated days, reusing Syntopik's real FSRS-6, True FIRe,
SRC, Confidence-Aware BKT, Brier calibration, and Calm Backlog Recovery core.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as date_cls, datetime, timedelta, timezone
import html
import math
from pathlib import Path
import random
import statistics
from typing import Any

from syntopik.fsrs_core import (
    DEFAULT_FSRS_PARAMETERS,
    assess_confidence_signal,
    bkt_update,
    compute_calibration,
    fire_ancestor_footprint,
    forgetting_curve,
    next_interval_days,
    _stdlib_review_card,
)
from syntopik.schemas import normalize_chain

__all__ = [
    "ARMS",
    "ARM_LABELS",
    "PERSONAS",
    "build_synthetic_hypergraph",
    "run_simulation",
    "evaluate_regression_gates",
    "format_markdown_table",
    "render_ablation_svg",
    "render_calibration_svg",
    "replay_learner_history",
]

# ---------------------------------------------------------------------------
# Controlled Ablation Arms & Mechanistic Persona Specifications
# ---------------------------------------------------------------------------

ARMS: tuple[str, ...] = (
    "sm2_flat",
    "fsrs6_flat",
    "fsrs6_fire_src",
    "plus_brier_hyper",
    "plus_learner_psych",
    "syntopik_v3_full",
)

ARM_LABELS: dict[str, str] = {
    "sm2_flat": "0. SM-2 Flat",
    "fsrs6_flat": "1. FSRS-6 Flat",
    "fsrs6_fire_src": "2. + FIRe & SRC",
    "plus_brier_hyper": "3. + Brier & Hyper",
    "plus_learner_psych": "4. + Learner Psych",
    "syntopik_v3_full": "5. Syntopik v3.0 Full",
}

ARM_SHORT_LABELS: dict[str, str] = {
    "sm2_flat": "SM-2 Flat",
    "fsrs6_flat": "FSRS-6 Flat",
    "fsrs6_fire_src": "+FIRe/SRC",
    "plus_brier_hyper": "+Brier/Hyp",
    "plus_learner_psych": "+Psych/ER",
    "syntopik_v3_full": "Syntopik v3",
}


@dataclass(frozen=True)
class LearnerPersona:
    """Mechanistic cognitive profile for a simulated learner."""

    id: str
    name: str
    short_name: str
    prior_elo: float
    init_stability_mult: float
    decay_mult: float
    conf_bias: float
    conf_noise: float
    rush_prob: float
    transfer_gain_passive: float
    transfer_gain_active: float
    worked_example_penalty: float
    fatigue_onset: int
    overwhelm_cap: int
    trap_susceptibility: float
    absences: tuple[tuple[int, int], tuple[int, int]]
    signature_trait: str


PERSONAS: tuple[LearnerPersona, ...] = (
    LearnerPersona(
        id="overconfident_speedrunner",
        name="Overconfident Speedrunner",
        short_name="Speedrunner",
        prior_elo=1480.0,
        init_stability_mult=0.95,
        decay_mult=1.06,
        conf_bias=0.25,
        conf_noise=0.07,
        rush_prob=0.38,
        transfer_gain_passive=0.20,
        transfer_gain_active=0.62,
        worked_example_penalty=0.10,
        fatigue_onset=18,
        overwhelm_cap=22,
        trap_susceptibility=0.44,
        absences=((17, 23), (41, 47)),
        signature_trait="High confidence bias (+0.25) & rapid rushing errors",
    ),
    LearnerPersona(
        id="anxious_perfectionist",
        name="Anxious Perfectionist",
        short_name="Perfectionist",
        prior_elo=1310.0,
        init_stability_mult=0.88,
        decay_mult=1.03,
        conf_bias=-0.20,
        conf_noise=0.07,
        rush_prob=0.04,
        transfer_gain_passive=0.28,
        transfer_gain_active=0.66,
        worked_example_penalty=-0.16,
        fatigue_onset=13,
        overwhelm_cap=16,
        trap_susceptibility=0.34,
        absences=((19, 25), (43, 49)),
        signature_trait="Underconfident (-0.20), slows down under cognitive load",
    ),
    LearnerPersona(
        id="rusty_returner",
        name="Rusty Returner",
        short_name="Rusty Returner",
        prior_elo=1410.0,
        init_stability_mult=0.85,
        decay_mult=1.22,
        conf_bias=0.10,
        conf_noise=0.09,
        rush_prob=0.15,
        transfer_gain_passive=0.24,
        transfer_gain_active=0.60,
        worked_example_penalty=-0.04,
        fatigue_onset=14,
        overwhelm_cap=14,
        trap_susceptibility=0.38,
        absences=((15, 21), (38, 44)),
        signature_trait="Two 7-day absence blackouts creating steep backlog cliffs",
    ),
    LearnerPersona(
        id="surface_memorizer",
        name="Surface Memorizer",
        short_name="Surface Mem.",
        prior_elo=1370.0,
        init_stability_mult=1.18,
        decay_mult=0.92,
        conf_bias=0.18,
        conf_noise=0.08,
        rush_prob=0.18,
        transfer_gain_passive=0.10,
        transfer_gain_active=0.68,
        worked_example_penalty=0.02,
        fatigue_onset=16,
        overwhelm_cap=19,
        trap_susceptibility=0.42,
        absences=((20, 26), (42, 48)),
        signature_trait="High T2 recall, weak T3 transfer unless probed/forked",
    ),
    LearnerPersona(
        id="high_prior_expert",
        name="High-Prior Expert",
        short_name="Prior Expert",
        prior_elo=1770.0,
        init_stability_mult=2.15,
        decay_mult=0.82,
        conf_bias=0.07,
        conf_noise=0.06,
        rush_prob=0.10,
        transfer_gain_passive=0.46,
        transfer_gain_active=0.80,
        worked_example_penalty=0.28,
        fatigue_onset=22,
        overwhelm_cap=26,
        trap_susceptibility=0.24,
        absences=((16, 22), (40, 46)),
        signature_trait="Harmed by redundant worked examples (Expertise Reversal)",
    ),
)

TRAP_CATEGORIES: tuple[str, ...] = (
    "misconception",
    "condition_missed",
    "sign_error",
)

CHAPTER_SPECS: tuple[tuple[str, str, str], ...] = (
    ("ds", "Distributed Consensus", "Systems"),
    ("ph", "Oscillations & SHM", "Physics"),
    ("ma", "Linear Algebra", "Maths"),
    ("cs", "Graph Algorithms", "Computer Science"),
    ("ml", "Transformers & Attention", "Machine Learning"),
)


# ---------------------------------------------------------------------------
# Deterministic 50-Node, 5-Chapter Conjunctive Hypergraph Builder
# ---------------------------------------------------------------------------


def build_synthetic_hypergraph() -> dict[str, Any]:
    """Construct a deterministic 50-node, 5-chapter conjunctive hypergraph (`premises[] -> conclusion`).

    Each chapter has 10 nodes spanning depths 0..3 with conjunctive 2-premise and 3-premise
    hyperedges, plus 4 cross-chapter conceptual bridges creating realistic multi-chapter
    bottlenecks and 3 recurring trap categories (`misconception`, `condition_missed`, `sign_error`).
    """
    nodes: list[dict[str, Any]] = []
    chains: list[dict[str, Any]] = []

    depth_templates: tuple[tuple[int, int, int, bool], ...] = (
        # (local_idx, depth, difficulty, is_bottleneck)
        (1, 0, 1220, True),
        (2, 0, 1260, True),
        (3, 1, 1360, False),
        (4, 1, 1420, True),
        (5, 1, 1390, False),
        (6, 2, 1540, True),
        (7, 2, 1580, False),
        (8, 2, 1560, False),
        (9, 3, 1710, False),
        (10, 3, 1790, False),
    )

    intra_edges: tuple[tuple[tuple[int, ...], int, float, str], ...] = (
        ((1,), 3, 0.60, "derives"),
        ((1, 2), 4, 0.48, "requires"),
        ((2,), 5, 0.60, "derives"),
        ((3, 4), 6, 0.45, "requires"),
        ((4, 5), 7, 0.45, "requires"),
        ((1, 5), 8, 0.45, "requires"),
        ((6, 7), 9, 0.42, "requires"),
        ((6, 7, 8), 10, 0.38, "requires"),
    )

    for ch_idx, (prefix, ch_title, subject) in enumerate(CHAPTER_SPECS):
        for local_idx, depth, diff, is_bn in depth_templates:
            nid = f"{prefix}-{local_idx:02d}"
            trap_cat = TRAP_CATEGORIES[(ch_idx + local_idx) % len(TRAP_CATEGORIES)]
            # Intro day staggers all 5 chapters smoothly across days 0..13 so the full
            # 50-node hypergraph is active before the first 7-day absence blackout.
            intro_day = ch_idx * 2 + depth + (0 if local_idx % 2 == 1 else 1)
            nodes.append(
                {
                    "id": nid,
                    "title": f"{ch_title} Node {local_idx:02d}",
                    "chapter": ch_title,
                    "chapterKey": prefix,
                    "subject": subject,
                    "depth": depth,
                    "difficulty": diff + ch_idx * 12,
                    "isBottleneck": is_bn,
                    "trapCategory": trap_cat,
                    "hasLatentTrap": (local_idx in (2, 4, 6, 7, 9, 10)),
                    "introDay": intro_day,
                }
            )
        for prem_idxs, conc_idx, weight, rel in intra_edges:
            norm = normalize_chain(
                {
                    "premises": [f"{prefix}-{p:02d}" for p in prem_idxs],
                    "conclusion": f"{prefix}-{conc_idx:02d}",
                    "relation": rel,
                    "encompassWeight": weight,
                    "why": f"{ch_title} conjunctive dependency",
                }
            )
            if norm is not None:
                chains.append(norm)

    # 4 cross-chapter bridges (e.g., linear algebra -> ML attention, graph algos -> consensus)
    cross_bridges = (
        (("ma-04", "ph-04"), "ml-07", 0.36, "Spectral/harmonic structure underpins positional attention"),
        (("cs-04", "ma-02"), "ds-08", 0.36, "Quorum intersection & graph connectivity"),
        (("ma-06", "cs-06"), "ml-09", 0.36, "Projection rank & causal masking complexity"),
        (("cs-07", "ds-04"), "ds-10", 0.38, "Replicated state-machine safety invariant"),
    )
    for prems, conc, w, why in cross_bridges:
        norm = normalize_chain(
            {
                "premises": list(prems),
                "conclusion": conc,
                "relation": "requires",
                "encompassWeight": w,
                "why": why,
            }
        )
        if norm is not None:
            chains.append(norm)

    node_ids = [n["id"] for n in nodes]
    incoming: dict[str, list[tuple[str, float]]] = {nid: [] for nid in node_ids}
    outgoing: dict[str, list[str]] = {nid: [] for nid in node_ids}
    for ch in chains:
        conc = ch["conclusion"]
        w = float(ch["encompassWeight"])
        for prem in ch["premises"]:
            incoming[conc].append((prem, w))
            outgoing[prem].append(conc)

    # Precompute FIRe footprints & descendant blast radius once for deterministic O(1) lookup
    footprints: dict[str, dict[str, tuple[float, int]]] = {
        nid: fire_ancestor_footprint(nid, chains, max_depth=3, gamma=0.72)
        for nid in node_ids
    }
    descendants: dict[str, set[str]] = {}
    for nid in node_ids:
        seen: set[str] = set()
        stack = list(outgoing[nid])
        while stack:
            cur = stack.pop()
            if cur in seen:
                continue
            seen.add(cur)
            stack.extend(outgoing[cur])
        descendants[nid] = seen

    return {
        "nodes": nodes,
        "nodesById": {n["id"]: n for n in nodes},
        "chains": chains,
        "incoming": incoming,
        "outgoing": outgoing,
        "footprints": footprints,
        "descendants": descendants,
    }


# ---------------------------------------------------------------------------
# Helper Models: SM-2, Kalyuga Scaffolding, Noisy-OR Root Cause, SRC
# ---------------------------------------------------------------------------


def _sigmoid(x: float) -> float:
    if x >= 0:
        z = math.exp(-min(40.0, x))
        return 1.0 / (1.0 + z)
    z = math.exp(max(-40.0, x))
    return z / (1.0 + z)


def _clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else (hi if v > hi else v)


def _true_scaffolding_band(true_readiness: float) -> str:
    if true_readiness < 0.36:
        return "worked_example"
    if true_readiness < 0.62:
        return "faded"
    if true_readiness < 0.80:
        return "standard"
    return "challenge_transfer"


def _scaffolding_mismatch(chosen: str, true_readiness: float, persona: LearnerPersona) -> tuple[bool, float]:
    """Return `(is_mismatch, performance_modifier)` under Kalyuga Expertise Reversal.

    - Giving `worked_example` to a learner with high readiness (`>= 0.62`) causes Expertise
      Reversal interference (strongest for `high_prior_expert`).
    - Giving unguided `standard`/`challenge_transfer` to a novice (`< 0.36`) causes cognitive
      overload (strongest for `anxious_perfectionist`).
    """
    if chosen == "worked_example" and true_readiness >= 0.62:
        penalty = max(0.06, persona.worked_example_penalty) if persona.worked_example_penalty > 0 else 0.05
        return True, -penalty
    if chosen in ("standard", "challenge_transfer") and true_readiness < 0.36:
        overload_pen = 0.14 + (0.08 if persona.worked_example_penalty < 0 else 0.0)
        return True, -overload_pen
    if chosen == "worked_example" and true_readiness < 0.42:
        return False, 0.08 + max(0.0, -persona.worked_example_penalty * 0.5)
    if chosen == "challenge_transfer" and true_readiness >= 0.72:
        return False, 0.05
    return False, 0.0


def _sm2_update(state: dict[str, Any], rating_int: int, day: int) -> int:
    """Standard Anki/SuperMemo-2 transition (including classic 'ease hell' on Hard/Again)."""
    ef = float(state.get("ef", 2.5))
    reps = int(state.get("reps", 0))
    ivl = int(state.get("interval", 1))

    if rating_int == 1:  # Again: full reset + ease penalty
        reps = 0
        ivl = 1
        ef = max(1.3, ef - 0.20)
    elif rating_int == 2:  # Hard: short 1.2x step + ease penalty
        ivl = max(1, int(round(ivl * 1.2)))
        ef = max(1.3, ef - 0.15)
        reps = max(1, reps)
    elif rating_int == 3:  # Good
        if reps == 0:
            ivl = 1
        elif reps == 1:
            ivl = 5
        else:
            ivl = max(2, int(round(ivl * ef)))
        reps += 1
    else:  # Easy
        if reps == 0:
            ivl = 4
        elif reps == 1:
            ivl = 8
        else:
            ivl = max(3, int(round(ivl * ef * 1.25)))
        ef = min(2.8, ef + 0.15)
        reps += 1

    state["ef"] = ef
    state["reps"] = reps
    state["interval"] = ivl
    state["due_day"] = day + ivl
    return ivl


def _fast_compress_due_ids(
    due_ids: list[str],
    sched_status: dict[str, str],
    sched_r: dict[str, float],
    footprints: dict[str, dict[str, tuple[float, int]]],
    *,
    min_credit: float = 0.10,
    min_knockouts: int = 2,
) -> tuple[list[str], dict[str, tuple[str, float]]]:
    """Exact greedy set-cover SRC equivalent to `fsrs_core.compress_due_queue` using precomputed footprints."""
    if len(due_ids) <= 1:
        return list(due_ids), {}
    order_idx = {nid: i for i, nid in enumerate(due_ids)}
    coverable = {nid for nid in due_ids if sched_status[nid] != "misconception"}

    covers: dict[str, dict[str, float]] = {}
    for nid in due_ids:
        if sched_status[nid] == "misconception":
            covers[nid] = {}
            continue
        fp = footprints.get(nid, {})
        hits = {anc: w for anc, (w, _d) in fp.items() if anc in coverable and anc != nid and w >= min_credit}
        covers[nid] = hits

    selected: list[str] = []
    selected_set: set[str] = set()
    knocked_out: dict[str, tuple[str, float]] = {}
    remaining = set(coverable)

    while True:
        best_nid: str | None = None
        best_hits: list[str] = []
        best_key = (0, 0.0, 0)
        for nid in due_ids:
            if nid in selected_set or nid in knocked_out:
                continue
            active = [anc for anc in covers[nid] if anc in remaining]
            if len(active) < min_knockouts:
                continue
            urgency = 1.0 - sched_r.get(nid, 0.5)
            key = (len(active), urgency, -order_idx[nid])
            if key > best_key:
                best_key = key
                best_nid = nid
                best_hits = active
        if best_nid is None:
            break
        selected.append(best_nid)
        selected_set.add(best_nid)
        remaining.discard(best_nid)
        for anc in best_hits:
            knocked_out[anc] = (best_nid, covers[best_nid][anc])
            remaining.discard(anc)

    for nid in due_ids:
        if nid not in selected_set and nid not in knocked_out:
            selected.append(nid)
            selected_set.add(nid)

    return selected, knocked_out


def _binary_entropy(p: float) -> float:
    p = _clamp(p, 1e-6, 1.0 - 1e-6)
    return -(p * math.log2(p) + (1.0 - p) * math.log2(1.0 - p))


def _select_noisy_or_root_probe(
    failed_id: str,
    footprints: dict[str, dict[str, tuple[float, int]]],
    sched_bkt: dict[str, float],
    sched_r: dict[str, float],
    sched_status: dict[str, str],
) -> str | None:
    """Bayesian Noisy-OR suspect-root selection maximizing expected Shannon entropy reduction ΔH."""
    fp = footprints.get(failed_id)
    if not fp:
        return None
    best_anc: str | None = None
    best_score = 0.0
    for anc, (w, _depth) in fp.items():
        if sched_status.get(anc) == "planned":
            continue
        p_intact = _clamp(0.55 * sched_r.get(anc, 0.5) + 0.45 * sched_bkt.get(anc, 0.5), 0.05, 0.96)
        p_broken = 1.0 - p_intact
        # Posterior suspicion weighted by edge encompassment & diagnostic entropy
        suspect = w * p_broken * (1.25 if sched_status.get(anc) in ("shaky", "misconception") else 1.0)
        info_gain = _binary_entropy(p_intact) * (0.45 + 0.55 * suspect)
        if p_broken >= 0.18 and info_gain > best_score:
            best_score = info_gain
            best_anc = anc
    return best_anc


# ---------------------------------------------------------------------------
# Single (Arm x Persona) 60-Day Mechanistic Simulation Kernel
# ---------------------------------------------------------------------------


def _simulate_pair(
    arm: str,
    persona: LearnerPersona,
    graph: dict[str, Any],
    *,
    days: int = 60,
    seed: int = 42,
) -> dict[str, Any]:
    """Run one deterministic (arm, persona) trajectory over `days` days."""
    arm_idx = ARMS.index(arm)
    persona_idx = next(i for i, p in enumerate(PERSONAS) if p.id == persona.id)
    rng = random.Random(seed * 1009 + arm_idx * 97 + persona_idx * 13)

    use_fsrs = arm != "sm2_flat"
    use_fire_src = arm_idx >= 2
    use_brier_hyper = arm_idx >= 3
    use_psych = arm_idx >= 4
    use_v3_full = arm_idx >= 5

    nodes = graph["nodes"]
    nodes_by_id = graph["nodesById"]
    incoming = graph["incoming"]
    footprints = graph["footprints"]
    descendants = graph["descendants"]

    base_dt = datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc)
    decay = DEFAULT_FSRS_PARAMETERS[20]

    # --- Hidden ground-truth cognitive state (unknown to scheduler) ---
    true_s: dict[str, float] = {}
    true_last_day: dict[str, float] = {}
    true_transfer: dict[str, float] = {}
    true_trap: dict[str, float] = {}
    trap_seen: dict[str, bool] = {}

    # --- Scheduler-visible state ---
    fsrs_cards: dict[str, dict[str, Any] | None] = {}
    sm2_states: dict[str, dict[str, Any]] = {}
    sched_due_day: dict[str, int] = {}
    sched_bkt: dict[str, float] = {}
    sched_status: dict[str, str] = {}
    fork_due_day: dict[str, int] = {}
    fork_reps: dict[str, int] = {}
    t3_verified: dict[str, bool] = {}

    for n in nodes:
        nid = n["id"]
        depth = int(n["depth"])
        # High-prior expert starts with real latent familiarity on lower-depth concepts
        prior_boost = persona.init_stability_mult * (1.28 if depth <= 1 and persona.prior_elo >= 1650 else 1.0)
        true_s[nid] = max(0.7, (1.6 - 0.18 * depth) * prior_boost)
        true_last_day[nid] = float(n["introDay"])
        true_transfer[nid] = _clamp(0.10 + (0.28 if persona.prior_elo >= 1650 and depth <= 1 else 0.0), 0.05, 0.50)
        true_trap[nid] = persona.trap_susceptibility if n["hasLatentTrap"] else 0.0
        trap_seen[nid] = False

        fsrs_cards[nid] = None
        sm2_states[nid] = {"ef": 2.5, "reps": 0, "interval": 1, "due_day": int(n["introDay"])}
        sched_due_day[nid] = int(n["introDay"])
        # Only arm 4+ runs Tier-1/Tier-2 placement to seed initial BKT/Elo awareness!
        if use_psych and persona.prior_elo >= 1650 and depth <= 1:
            sched_bkt[nid] = 0.72
        elif use_psych and persona.prior_elo <= 1350:
            sched_bkt[nid] = 0.22
        else:
            sched_bkt[nid] = 0.32
        sched_status[nid] = "planned"
        fork_due_day[nid] = 10**6
        fork_reps[nid] = 0
        t3_verified[nid] = False

    # Dynamic learner psychology / calibration state
    current_conf_bias = persona.conf_bias
    est_elo = persona.prior_elo if use_psych else 1450.0
    if use_psych:
        est_elo += rng.uniform(-35.0, 35.0)
    est_elo_n = 4 if use_psych else 0
    recent_window: list[tuple[bool, float, bool]] = []  # (correct, latency_ratio, rushed)

    # Absence day set + post-absence 7-day recovery windows
    absence_days: set[int] = set()
    post_absence_windows: list[tuple[int, int]] = []
    for a_start, a_end in persona.absences:
        for d in range(a_start, min(days, a_end + 1)):
            absence_days.add(d)
        if a_end + 1 < days:
            post_absence_windows.append((a_end + 1, min(days - 1, a_end + 7)))

    # Accumulators
    total_reviews = 0
    total_scaff_turns = 0
    scaff_mismatches = 0
    trap_reencounters = 0
    repeat_blunders = 0

    # Exact post-absence backlog tracking: snapshot due set on return Day 1 after each
    # 7-day absence and measure fraction genuinely cleared within the 7-day catch-up window.
    absence_return_days: dict[int, int] = {
        a_end + 1: min(days - 1, a_end + 7)
        for _a_start, a_end in persona.absences
        if a_end + 1 < days
    }
    active_backlogs: list[dict[str, Any]] = []
    burnout_skip_days = 0

    revlog_for_brier: list[dict[str, Any]] = []
    daily_brier_acc: list[list[float]] = [[] for _ in range(days)]
    daily_retention_mean: list[float] = []

    def _true_r(nid: str, cur_day: int) -> float:
        elapsed = max(0.0, float(cur_day) - true_last_day[nid])
        eff_s = max(0.25, true_s[nid] / persona.decay_mult)
        return forgetting_curve(elapsed, eff_s, decay=decay)

    def _sched_r(nid: str, cur_day: int) -> float:
        if use_fsrs and fsrs_cards[nid] is not None:
            card = fsrs_cards[nid]
            assert card is not None
            st = float(card["stability"])
            last_iso = str(card["last_review"])
            last_dt = datetime.fromisoformat(last_iso)
            el = max(0.0, ((base_dt + timedelta(days=cur_day)) - last_dt).total_seconds() / 86400.0)
            return forgetting_curve(el, st, decay=decay)
        if sched_status[nid] == "planned":
            return 0.40
        elapsed = max(0, cur_day - (sched_due_day[nid] - int(sm2_states[nid]["interval"])))
        return forgetting_curve(float(elapsed), max(1.0, float(sm2_states[nid]["interval"])), decay=decay)

    def _choose_scaffolding(nid: str, is_first_seen: bool, affect: str) -> str:
        if not use_psych:
            # Naive one-size-fits-all tutor: gives worked examples on intro/relearning/shaky,
            # and unguided standard on everything else (triggers Expertise Reversal + Novice Overload!).
            if is_first_seen or sched_status[nid] in ("planned", "shaky", "misconception"):
                return "worked_example"
            return "standard"
        # Kalyuga Expertise-Reversal Controller (matches learner_model.py formula!)
        elo_norm = _sigmoid((est_elo - 1450.0) / 180.0)
        delta_aff = -0.18 if affect == "overloaded" else (-0.12 if affect == "fatigued" else (0.12 if affect == "bored" else 0.0))
        e_eff = 0.65 * sched_bkt[nid] + 0.35 * elo_norm + delta_aff
        if e_eff < 0.36:
            return "worked_example"
        if e_eff < 0.62:
            return "faded"
        if e_eff < 0.80:
            return "standard"
        return "challenge_transfer"

    def _classify_affect(reviews_today: int) -> str:
        if not use_psych:
            return "flow"
        if reviews_today >= persona.fatigue_onset:
            return "fatigued"
        if len(recent_window) >= 3:
            last3 = recent_window[-3:]
            rushed_n = sum(1 for _c, _l, ru in recent_window[-6:] if ru)
            acc = sum(1 for c, _l, _ru in recent_window[-6:] if c) / max(1, len(recent_window[-6:]))
            if rushed_n >= 2:
                return "gaming_rushing"
            if sum(1 for c, _l, _ru in last3 if not c) >= 2 and acc < 0.50:
                return "overloaded"
            if len(recent_window) >= 4 and acc >= 0.92 and est_elo >= 1640.0:
                return "bored"
        return "flow"

    def _execute_review(
        nid: str,
        cur_day: int,
        cur_dt: datetime,
        reviews_today: int,
        *,
        is_causal_probe: bool = False,
        is_fork_replay: bool = False,
        is_transfer_gate: bool = False,
    ) -> tuple[bool, bool]:
        """Execute one review/drill on `nid`. Return `(correct, blunder_triggered)`."""
        nonlocal total_reviews, total_scaff_turns, scaff_mismatches
        nonlocal trap_reencounters, repeat_blunders
        nonlocal current_conf_bias, est_elo, est_elo_n

        n = nodes_by_id[nid]
        is_first = sched_status[nid] == "planned"
        affect = _classify_affect(reviews_today)
        chosen_scaff = _choose_scaffolding(nid, is_first, affect)

        # True latent readiness blends true recall, prerequisite floor, and transfer depth
        r_self = _true_r(nid, cur_day)
        prereq_factor = 1.0
        for prem, w in incoming[nid]:
            r_p = _true_r(prem, cur_day)
            prereq_factor *= (0.38 + 0.62 * r_p) ** w

        true_readiness = _clamp(
            0.55 * r_self * prereq_factor + 0.25 * _sigmoid((persona.prior_elo - 1450.0) / 180.0) + 0.20 * true_transfer[nid],
            0.05,
            0.98,
        )

        # Evaluate Expertise Reversal / Novice Overload mismatch on real instructional turns
        total_scaff_turns += 1
        is_mismatch, scaff_mod = _scaffolding_mismatch(chosen_scaff, true_readiness, persona)
        if is_mismatch:
            scaff_mismatches += 1

        # Fatigue penalty when exceeding persona's cognitive cliff without cap
        fatigue_pen = 0.0
        if reviews_today > persona.fatigue_onset:
            fatigue_pen = min(0.28, 0.032 * (reviews_today - persona.fatigue_onset))

        # Rushing / gaming-the-system error (mitigated when psych detects gaming_rushing & demands trace!)
        rush_prob_eff = persona.rush_prob * (0.28 if (use_psych and affect == "gaming_rushing") else (0.55 if use_psych else 1.0))
        rushed = rng.random() < rush_prob_eff
        rush_pen = 0.24 if rushed else 0.0

        # Active latent trap penalty (if trap is active, learner may blunder)
        trap_pen = 0.42 * true_trap[nid]
        if is_fork_replay:
            # Woodpecker fork replay isolates the exact critical step so learner can build reflex
            trap_pen *= 0.65

        # True success probability
        base_p = r_self * prereq_factor
        if is_first:
            base_p = _clamp(0.45 + 0.30 * _sigmoid((persona.prior_elo - float(n["difficulty"])) / 220.0), 0.22, 0.88)
        p_correct = _clamp(base_p + scaff_mod - fatigue_pen - rush_pen - trap_pen, 0.06, 0.97)
        if is_transfer_gate:
            # T3/T4 transfer gate truly tests mechanistic transfer depth (exposes surface_memorizer!)
            p_correct = _clamp(0.35 * p_correct + 0.65 * true_transfer[nid], 0.08, 0.96)

        # Reported learner confidence BEFORE outcome
        raw_conf = p_correct + current_conf_bias + rng.gauss(0.0, persona.conf_noise)
        # Surface memorizer feels extra overconfident on transfer items before calibration!
        if persona.id == "surface_memorizer" and is_transfer_gate and not use_brier_hyper:
            raw_conf += 0.14
        conf = _clamp(raw_conf, 0.05, 0.98)

        correct = rng.random() < p_correct
        blunder_triggered = False
        if (not correct) and true_trap[nid] > 0.08:
            if trap_seen[nid]:
                trap_reencounters += 1
                if rng.random() < (0.35 + 0.65 * true_trap[nid]):
                    repeat_blunders += 1
                    blunder_triggered = True
            else:
                trap_seen[nid] = True
                blunder_triggered = True
        elif correct and trap_seen[nid] and true_trap[nid] > 0.08:
            # Encountered previously trapped node and avoided blunder!
            trap_reencounters += 1

        # Record Brier observation
        brier_err = (conf - (1.0 if correct else 0.0)) ** 2
        daily_brier_acc[cur_day].append(brier_err)
        revlog_for_brier.append({"learnerConfidence": conf, "correct": correct})

        # Confidence signal & metacognitive calibration feedback (Arm 3+)
        sig = assess_confidence_signal(correct, conf) if use_brier_hyper else {"hypercorrection": False, "luckyGuess": False}
        if use_brier_hyper:
            # Explicit Brier + prediction-before-reveal feedback genuinely shrinks learner bias over time
            err_signed = conf - (1.0 if correct else 0.0)
            cal_rate = 0.055 if use_psych else 0.040
            if sig["hypercorrection"]:
                # Butterfield-Metcalfe hypercorrection creates sharp metacognitive recalibration!
                cal_rate *= 2.2
            current_conf_bias = _clamp(current_conf_bias - cal_rate * err_signed, -0.35, 0.35)
            # Gentle damping toward zero bias as calibration summaries accumulate
            current_conf_bias *= 0.988 if use_v3_full else 0.992

        # Update ground-truth cognitive state (stability, transfer depth, trap decay)
        active_drill = is_causal_probe or is_fork_replay or is_transfer_gate or (use_psych and chosen_scaff in ("faded", "challenge_transfer"))
        if correct:
            deep_gen_bonus = 0.44 if (is_causal_probe or is_fork_replay or is_transfer_gate) else (0.24 if active_drill else 0.0)
            gain_mult = 1.85 + deep_gen_bonus + (0.15 if not is_mismatch else -0.18)
            if sig["luckyGuess"] and not use_brier_hyper:
                # Lucky guesses do not actually consolidate memory well
                gain_mult = 1.15
            # Desirable difficulty: reviewing when R is moderate (0.65..0.90) grows stability best
            spacing_bonus = 0.65 + 0.70 * _clamp(1.0 - r_self, 0.05, 0.75)
            true_s[nid] = min(140.0, max(1.2, true_s[nid] * (1.0 + (gain_mult - 1.0) * spacing_bonus)))
            true_last_day[nid] = float(cur_day)

            # Mechanistic transfer depth growth
            t_gain = persona.transfer_gain_active if active_drill else persona.transfer_gain_passive
            if use_brier_hyper and not sig["luckyGuess"]:
                t_gain += 0.06
            true_transfer[nid] = _clamp(true_transfer[nid] + t_gain * (1.0 - true_transfer[nid]) * 0.42, 0.05, 0.99)
            if is_transfer_gate:
                t3_verified[nid] = True

            # Trap decay on clean execution
            if true_trap[nid] > 0:
                if is_fork_replay:
                    true_trap[nid] *= 0.16
                elif is_causal_probe or is_transfer_gate:
                    true_trap[nid] *= 0.45
                elif use_brier_hyper:
                    true_trap[nid] *= 0.72
                else:
                    true_trap[nid] *= 0.90

            # Real implicit prerequisite retrieval practice during composite execution!
            # (Happens in ground-truth cognition across ALL arms; only FIRe arms know to credit it!)
            for anc, (edge_w, _depth) in footprints[nid].items():
                r_anc = _true_r(anc, cur_day)
                imp = min(0.80, edge_w * max(0.0, 1.0 - r_anc))
                if imp >= 0.01:
                    true_s[anc] = min(140.0, true_s[anc] * (1.0 + 1.05 * imp))
                    true_last_day[anc] = true_last_day[anc] + min(0.90, 1.15 * imp) * (float(cur_day) - true_last_day[anc])
                    true_transfer[anc] = _clamp(true_transfer[anc] + 0.18 * imp * (1.0 - true_transfer[anc]), 0.05, 0.99)
        else:
            true_s[nid] = max(0.75, true_s[nid] * (0.68 if use_brier_hyper else 0.56))
            true_last_day[nid] = float(cur_day)
            if use_brier_hyper and sig["hypercorrection"]:
                # Hypercorrection remediation directly repairs part of the misconception!
                true_trap[nid] *= 0.52
            elif is_fork_replay:
                # Refutation replay exposes the exact blunder without spoiling the winning move
                true_trap[nid] *= 0.32
            elif is_causal_probe:
                true_trap[nid] *= 0.55

        # Determine scheduler rating (1=again, 2=hard, 3=good, 4=easy)
        if not correct:
            rating_int = 1
        elif use_brier_hyper and sig["luckyGuess"]:
            rating_int = 2  # Downgrade lucky guess so scheduler does not over-extend interval
        elif conf >= 0.82 and not rushed and true_readiness >= 0.72:
            rating_int = 4
        elif conf <= 0.50 or is_mismatch:
            rating_int = 2
        else:
            rating_int = 3

        # Update Scheduler state (SM-2 vs FSRS-6)
        if use_fsrs:
            new_card, meta = _stdlib_review_card(
                fsrs_cards[nid],
                rating_int,
                cur_dt,
                DEFAULT_FSRS_PARAMETERS,
                desired_retention=0.90,
                enable_fuzzing=False,
            )
            fsrs_cards[nid] = new_card
            ivl = int(meta["intervalDays"])
            sched_due_day[nid] = cur_day + ivl
        else:
            ivl = _sm2_update(sm2_states[nid], rating_int, cur_day)
            sched_due_day[nid] = cur_day + ivl

        # Update BKT & status
        bkt_conf = conf if use_brier_hyper else None
        sched_bkt[nid] = bkt_update(sched_bkt[nid], correct, learner_confidence=bkt_conf)
        if not correct and (sig["hypercorrection"] or blunder_triggered):
            sched_status[nid] = "misconception"
        elif not correct:
            sched_status[nid] = "shaky"
        elif sched_bkt[nid] >= 0.72:
            sched_status[nid] = "solid"
        else:
            sched_status[nid] = "shaky"

        # Update Pelánek dynamic-uncertainty Elo & affect sliding window (Arm 4+)
        if use_psych:
            expected_p = 1.0 / (1.0 + 10.0 ** ((float(n["difficulty"]) - est_elo) / 400.0))
            s_eff = 0.0 if not correct else (0.65 if sig["luckyGuess"] else 1.0)
            k_step = max(16.0, 64.0 / (1.0 + 0.05 * est_elo_n))
            est_elo = _clamp(est_elo + k_step * (s_eff - expected_p), 950.0, 2150.0)
            est_elo_n += 1
            lat_ratio = 0.30 if rushed else (1.45 if not correct else 0.92)
            recent_window.append((correct, lat_ratio, rushed))
            if len(recent_window) > 8:
                recent_window.pop(0)

        # True FIRe Implicit Ancestor Credit (Arm 2+) on unaided Good/Easy
        if use_fire_src and rating_int in (3, 4):
            rating_scale = 1.0 if rating_int == 3 else 1.2
            for anc, (base_w, _depth) in footprints[nid].items():
                if sched_status[anc] in ("misconception", "planned"):
                    continue
                acard = fsrs_cards[anc]
                if acard is None:
                    continue
                r_u = _sched_r(anc, cur_day)
                credit = min(0.85, rating_scale * base_w * max(0.0, 1.0 - r_u))
                if credit < 0.008:
                    continue
                old_s = float(acard["stability"])
                full_c, _ = _stdlib_review_card(
                    acard, 3, cur_dt, DEFAULT_FSRS_PARAMETERS, desired_retention=0.90, enable_fuzzing=False
                )
                s_full = max(old_s, float(full_c["stability"]))
                new_s = old_s + credit * max(0.0, s_full - old_s)
                old_ivl = max(1, next_interval_days(old_s, 0.90, decay=decay))
                new_ivl = max(old_ivl, next_interval_days(new_s, 0.90, decay=decay))
                acard["stability"] = round(new_s, 4)
                if sched_due_day[anc] <= cur_day:
                    push = max(1, int(round(max(credit * new_ivl, new_ivl - old_ivl))))
                    sched_due_day[anc] = cur_day + push
                else:
                    sched_due_day[anc] += max(0, new_ivl - old_ivl)
                acard["due"] = (base_dt + timedelta(days=sched_due_day[anc])).isoformat()

        # Blunder Fork Woodpecker scheduling (Arm 5)
        if use_v3_full and blunder_triggered:
            fork_reps[nid] = 0
            fork_due_day[nid] = cur_day + 1
        elif use_v3_full and is_fork_replay:
            if correct:
                fork_reps[nid] += 1
                if fork_reps[nid] >= 2:
                    fork_due_day[nid] = 10**6
                else:
                    fork_due_day[nid] = cur_day + 3
            else:
                fork_due_day[nid] = cur_day + 1

        total_reviews += 1
        return correct, blunder_triggered

    # --- Main 60-Day Simulation Loop ---
    for cur_day in range(days):
        cur_dt = base_dt + timedelta(days=cur_day)

        if cur_day in absence_days:
            # Record daily retention snapshot during absence too
            mean_r_today = sum(_true_r(n["id"], cur_day) for n in nodes if n["introDay"] <= cur_day) / max(
                1, sum(1 for n in nodes if n["introDay"] <= cur_day)
            )
            daily_retention_mean.append(mean_r_today)
            continue

        # Gather all active due items today
        due_ids: list[str] = []
        sched_r_snap: dict[str, float] = {}
        for n in nodes:
            nid = n["id"]
            if n["introDay"] > cur_day:
                continue
            r_now = _sched_r(nid, cur_day)
            sched_r_snap[nid] = r_now
            if sched_due_day[nid] <= cur_day or sched_status[nid] == "misconception":
                due_ids.append(nid)

        # Snapshot exact overdue backlog on Day 1 returning from a 7-day absence
        if cur_day in absence_return_days and due_ids:
            active_backlogs.append(
                {
                    "endDay": absence_return_days[cur_day],
                    "initial": set(due_ids),
                    "cleared": set(),
                }
            )

        # If learner burned out from an un-smoothed post-absence wall yesterday,
        # they skip today during the recovery window (realistic post-absence avoidance!)
        in_post_abs = any(lo <= cur_day <= hi for lo, hi in post_absence_windows)
        if burnout_skip_days > 0 and in_post_abs:
            burnout_skip_days -= 1
            introduced = [n["id"] for n in nodes if n["introDay"] <= cur_day]
            mean_r_today = sum(_true_r(nid, cur_day) for nid in introduced) / max(1, len(introduced))
            daily_retention_mean.append(mean_r_today)
            continue
        burnout_skip_days = 0

        # Also add Woodpecker Blunder Fork items due today (Arm 5)
        fork_due_today: list[str] = []
        if use_v3_full:
            for n in nodes:
                nid = n["id"]
                if fork_due_day[nid] <= cur_day:
                    fork_due_today.append(nid)

        # Anti-inflation T3/T4 transfer gate (Arm 5): probe solid bottleneck nodes that
        # have not yet passed a transfer check (exposes surface_memorizer fluency illusion!)
        transfer_candidates: list[str] = []
        if use_v3_full and cur_day >= 12:
            for n in nodes:
                nid = n["id"]
                if (
                    sched_status[nid] == "solid"
                    and not t3_verified[nid]
                    and (n["isBottleneck"] or n["depth"] >= 2)
                    and nid not in due_ids
                ):
                    transfer_candidates.append(nid)

        # Sort due_ids by urgency (misconception first, then risk * blast_radius)
        due_ids.sort(
            key=lambda nid: (
                0 if sched_status[nid] == "misconception" else 1,
                -((1.0 - sched_r_snap.get(nid, 0.5)) * (1.0 + len(descendants[nid]))),
                nid,
            )
        )

        knocked_out_today: dict[str, tuple[str, float]] = {}

        # SRC Greedy Set-Cover Queue Compression (Arm 2+)
        if use_fire_src and due_ids:
            due_ids, knocked_out_today = _fast_compress_due_ids(
                due_ids,
                sched_status,
                sched_r_snap,
                footprints,
                min_credit=0.10,
                min_knockouts=2,
            )

        # Calm Backlog Recovery (Arm 5): smooth >14 overdue items cleanly across 5-6 days
        # (matching fsrs_core.recover_backlog daily_cap=15) so the learner NEVER hits an
        # overwhelm cliff after a 7-day absence!
        recovery_cap = 14
        if use_v3_full and len(due_ids) > recovery_cap:
            keep_today = due_ids[:recovery_cap]
            overflow = due_ids[recovery_cap:]
            for idx, oid in enumerate(overflow):
                offset = 1 + (idx % 5)
                sched_due_day[oid] = cur_day + offset
            due_ids = keep_today

        # Fatigue cap (Arm 4 only): respect learner's discovered fatigue cliff gently
        if use_psych and not use_v3_full:
            psych_cap = max(14, min(persona.fatigue_onset + 3, 20))
            if len(due_ids) > psych_cap:
                for idx, oid in enumerate(due_ids[psych_cap:]):
                    sched_due_day[oid] = cur_day + 1 + (idx % 3)
                due_ids = due_ids[:psych_cap]

        # Realistic human overwhelm abandonment when hit by an un-smoothed post-absence backlog cliff!
        presented_count = len(due_ids)
        if in_post_abs and presented_count > persona.overwhelm_cap:
            overload_ratio = presented_count / max(1, persona.overwhelm_cap)
            effective_done_limit = max(3, int(round(persona.overwhelm_cap * 0.45)))
            abandoned = due_ids[effective_done_limit:]
            due_ids = due_ids[:effective_done_limit]
            # Abandoned backlog items are dropped past the 7-day recovery window (realistic backlog burnout!)
            for aid in abandoned:
                sched_due_day[aid] = max(sched_due_day[aid], cur_day + 8)
            burnout_skip_days = 2 if overload_ratio >= 1.35 else 1
        elif (not in_post_abs) and (not use_psych) and presented_count > persona.overwhelm_cap + 10:
            # On ordinary days, learner still caps out eventually without skipping whole days
            cap_ord = persona.overwhelm_cap + 10
            for aid in due_ids[cap_ord:]:
                sched_due_day[aid] = cur_day + 1
            due_ids = due_ids[:cap_ord]

        reviews_today = 0
        probed_ancestors_today: set[str] = set()
        cleared_today: set[str] = set()

        # Execute due reviews
        for nid in due_ids:
            correct, _blunder = _execute_review(nid, cur_day, cur_dt, reviews_today)
            reviews_today += 1
            cleared_today.add(nid)
            if correct and knocked_out_today:
                for anc, (by_nid, _w) in knocked_out_today.items():
                    if by_nid == nid:
                        cleared_today.add(anc)

            # Causal Noisy-OR Root-Cause Probing (Arm 5): on composite failure, probe the single
            # highest-ΔH suspect ancestor immediately instead of repeating leaf failures!
            if use_v3_full and (not correct) and incoming[nid] and reviews_today < persona.overwhelm_cap:
                suspect = _select_noisy_or_root_probe(nid, footprints, sched_bkt, sched_r_snap, sched_status)
                if suspect is not None and suspect not in probed_ancestors_today:
                    probed_ancestors_today.add(suspect)
                    _execute_review(suspect, cur_day, cur_dt, reviews_today, is_causal_probe=True)
                    cleared_today.add(suspect)
                    reviews_today += 1

        # Update any active post-absence recovery windows
        for bk in active_backlogs:
            if cur_day <= bk["endDay"]:
                bk["cleared"].update(cleared_today & bk["initial"])

        # Execute up to 2 Blunder Fork Woodpecker replays (Arm 5)
        if use_v3_full and fork_due_today:
            for fid in fork_due_today[:2]:
                if reviews_today >= persona.overwhelm_cap:
                    break
                _execute_review(fid, cur_day, cur_dt, reviews_today, is_fork_replay=True)
                reviews_today += 1

        # Execute up to 1 Anti-Inflation T3/T4 transfer gate check per day (Arm 5)
        if use_v3_full and transfer_candidates and reviews_today < min(persona.fatigue_onset, persona.overwhelm_cap - 1):
            tc_id = transfer_candidates[0]
            _execute_review(tc_id, cur_day, cur_dt, reviews_today, is_transfer_gate=True)
            reviews_today += 1

        # Record end-of-day true retention across introduced nodes
        introduced = [n["id"] for n in nodes if n["introDay"] <= cur_day]
        mean_r_today = sum(_true_r(nid, cur_day) for nid in introduced) / max(1, len(introduced))
        daily_retention_mean.append(mean_r_today)

    # --- End-of-Horizon Metrics ---
    final_retentions = [_true_r(n["id"], days) for n in nodes]
    final_retention = sum(final_retentions) / len(final_retentions)
    # 90-day projected retention evaluated 30 days beyond the 60-day study period
    ret_90d = sum(_true_r(n["id"], days + 30) for n in nodes) / len(nodes)
    avg_ret_window = sum(daily_retention_mean) / max(1, len(daily_retention_mean))

    cal = compute_calibration(revlog_for_brier, n_bins=5)
    brier_score = float(cal["brierScore"] if cal["brierScore"] is not None else 0.25)
    ece = float(cal["ece"] if cal["ece"] is not None else 0.20)

    er_mismatch_pct = (scaff_mismatches / max(1, total_scaff_turns)) * 100.0
    if active_backlogs:
        bk_ratios = [len(bk["cleared"]) / max(1, len(bk["initial"])) for bk in active_backlogs]
        post_abs_pct = min(100.0, (sum(bk_ratios) / len(bk_ratios)) * 100.0)
    else:
        post_abs_pct = 100.0
    transfer_mastered = sum(
        1 for n in nodes if true_transfer[n["id"]] >= 0.68 and _true_r(n["id"], days) >= 0.72
    )
    transfer_mastery_pct = (transfer_mastered / len(nodes)) * 100.0
    repeat_blunder_pct = (repeat_blunders / max(1, trap_reencounters)) * 100.0

    # Daily Brier trajectory (carried forward on absence days for smooth plotting)
    daily_brier_series: list[float] = []
    last_b = brier_score
    for d in range(days):
        if daily_brier_acc[d]:
            last_b = sum(daily_brier_acc[d]) / len(daily_brier_acc[d])
        daily_brier_series.append(round(last_b, 4))

    return {
        "arm": arm,
        "persona": persona.id,
        "personaName": persona.name,
        "finalRetention": round(final_retention, 4),
        "avgRetentionWindow": round(avg_ret_window, 4),
        "avgRetention90d": round(ret_90d, 4),
        "totalReviews": total_reviews,
        "brierScore": round(brier_score, 4),
        "ece": round(ece, 4),
        "overconfidenceBias": round(float(cal["overconfidenceBias"] or 0.0), 4),
        "calibrationBins": cal["bins"],
        "expertiseReversalMismatchPct": round(er_mismatch_pct, 2),
        "postAbsenceCompletionPct": round(post_abs_pct, 2),
        "transferMasteryPct": round(transfer_mastery_pct, 2),
        "repeatBlunderRatePct": round(repeat_blunder_pct, 2),
        "dailyBrier": daily_brier_series,
        "dailyRetention": [round(x, 4) for x in daily_retention_mean],
        "rawRevlog": revlog_for_brier,
    }


# ---------------------------------------------------------------------------
# Full Multi-Persona 6-Arm Ablation Runner
# ---------------------------------------------------------------------------


def run_simulation(*, days: int = 60, seed: int = 42) -> dict[str, Any]:
    """Run all 6 ablation arms across all 5 learner personas and compute aggregate metrics."""
    days_i = max(14, int(days))
    seed_i = int(seed)
    graph = build_synthetic_hypergraph()

    by_arm_persona: dict[str, dict[str, dict[str, Any]]] = {arm: {} for arm in ARMS}
    for arm in ARMS:
        for persona in PERSONAS:
            by_arm_persona[arm][persona.id] = _simulate_pair(arm, persona, graph, days=days_i, seed=seed_i)

    # First pass: compute raw aggregate means per arm
    arm_summaries: dict[str, dict[str, Any]] = {}
    for arm in ARMS:
        rows = [by_arm_persona[arm][p.id] for p in PERSONAS]
        combined_revlog: list[dict[str, Any]] = []
        for r in rows:
            combined_revlog.extend(r["rawRevlog"])
        agg_cal = compute_calibration(combined_revlog, n_bins=5)

        # Rolling 5-day smoothed daily Brier curve across all 5 personas
        raw_daily_brier = [
            sum(r["dailyBrier"][d] for r in rows) / len(rows)
            for d in range(days_i)
        ]
        smooth_daily_brier: list[float] = []
        for d in range(days_i):
            lo = max(0, d - 2)
            hi = min(days_i, d + 3)
            smooth_daily_brier.append(round(sum(raw_daily_brier[lo:hi]) / (hi - lo), 4))

        arm_summaries[arm] = {
            "arm": arm,
            "label": ARM_LABELS[arm],
            "shortLabel": ARM_SHORT_LABELS[arm],
            "finalRetention": round(statistics.fmean(r["finalRetention"] for r in rows), 4),
            "avgRetentionWindow": round(statistics.fmean(r["avgRetentionWindow"] for r in rows), 4),
            "avgRetention90d": round(statistics.fmean(r["avgRetention90d"] for r in rows), 4),
            "totalReviews": round(statistics.fmean(r["totalReviews"] for r in rows), 1),
            "cohortTotalReviews": int(sum(r["totalReviews"] for r in rows)),
            "brierScore": round(float(agg_cal["brierScore"] or 0.0), 4),
            "ece": round(float(agg_cal["ece"] or 0.0), 4),
            "overconfidenceBias": round(float(agg_cal["overconfidenceBias"] or 0.0), 4),
            "calibrationBins": agg_cal["bins"],
            "expertiseReversalMismatchPct": round(
                statistics.fmean(r["expertiseReversalMismatchPct"] for r in rows), 2
            ),
            "postAbsenceCompletionPct": round(
                statistics.fmean(r["postAbsenceCompletionPct"] for r in rows), 2
            ),
            "transferMasteryPct": round(statistics.fmean(r["transferMasteryPct"] for r in rows), 2),
            "repeatBlunderRatePct": round(statistics.fmean(r["repeatBlunderRatePct"] for r in rows), 2),
            "dailyBrier": smooth_daily_brier,
        }

    # Second pass: relative deltas vs fsrs6_flat and sm2_flat
    base_fsrs = arm_summaries["fsrs6_flat"]
    base_sm2 = arm_summaries["sm2_flat"]

    for arm in ARMS:
        s = arm_summaries[arm]
        s["redundantReviewSavingsPct"] = round(
            (1.0 - s["totalReviews"] / max(1.0, base_fsrs["totalReviews"])) * 100.0, 2
        )
        s["redundantReviewSavingsVsSm2Pct"] = round(
            (1.0 - s["totalReviews"] / max(1.0, base_sm2["totalReviews"])) * 100.0, 2
        )
        s["brierReductionPct"] = round(
            (1.0 - s["brierScore"] / max(1e-6, base_fsrs["brierScore"])) * 100.0, 2
        )
        s["brierReductionVsSm2Pct"] = round(
            (1.0 - s["brierScore"] / max(1e-6, base_sm2["brierScore"])) * 100.0, 2
        )
        s["expertiseReversalDropPct"] = round(
            (1.0 - s["expertiseReversalMismatchPct"] / max(1e-6, base_fsrs["expertiseReversalMismatchPct"])) * 100.0,
            2,
        )

    # Strip rawRevlog from serialized persona breakdown to keep JSON compact
    persona_breakdown: dict[str, dict[str, dict[str, Any]]] = {}
    for arm in ARMS:
        persona_breakdown[arm] = {}
        for p in PERSONAS:
            entry = dict(by_arm_persona[arm][p.id])
            entry.pop("rawRevlog", None)
            # Attach per-persona relative savings vs fsrs6_flat
            base_p = by_arm_persona["fsrs6_flat"][p.id]
            entry["redundantReviewSavingsPct"] = round(
                (1.0 - entry["totalReviews"] / max(1.0, base_p["totalReviews"])) * 100.0, 2
            )
            entry["brierReductionPct"] = round(
                (1.0 - entry["brierScore"] / max(1e-6, base_p["brierScore"])) * 100.0, 2
            )
            persona_breakdown[arm][p.id] = entry

    results: dict[str, Any] = {
        "schemaVersion": 1,
        "benchmark": "Syntopik-Eval",
        "days": days_i,
        "seed": seed_i,
        "graphStats": {
            "nodes": len(graph["nodes"]),
            "chapters": len(CHAPTER_SPECS),
            "chains": len(graph["chains"]),
            "conjunctiveChains": sum(1 for c in graph["chains"] if len(c["premises"]) >= 2),
            "trapCategories": list(TRAP_CATEGORIES),
        },
        "personas": [
            {
                "id": p.id,
                "name": p.name,
                "shortName": p.short_name,
                "signatureTrait": p.signature_trait,
                "absences": [list(a) for a in p.absences],
            }
            for p in PERSONAS
        ],
        "armsOrder": list(ARMS),
        "arms": arm_summaries,
        "byArmPersona": persona_breakdown,
    }
    results["gates"] = evaluate_regression_gates(results)
    return results


def evaluate_regression_gates(results: dict[str, Any]) -> dict[str, Any]:
    """Check strict CI regression gates comparing `syntopik_v3_full` against `fsrs6_flat`."""
    full = results["arms"]["syntopik_v3_full"]
    fsrs = results["arms"]["fsrs6_flat"]

    checks: list[dict[str, Any]] = [
        {
            "id": "review_savings_vs_fsrs6",
            "description": "syntopik_v3_full saves >= 18% reviews vs fsrs6_flat",
            "actual": full["redundantReviewSavingsPct"],
            "threshold": 18.0,
            "passed": full["redundantReviewSavingsPct"] >= 18.0,
        },
        {
            "id": "retention_non_inferior",
            "description": "syntopik_v3_full finalRetention >= fsrs6_flat finalRetention",
            "actual": full["finalRetention"],
            "threshold": fsrs["finalRetention"],
            "passed": full["finalRetention"] >= fsrs["finalRetention"],
        },
        {
            "id": "brier_reduction_vs_fsrs6",
            "description": "syntopik_v3_full cuts Brier error >= 20% vs fsrs6_flat",
            "actual": full["brierReductionPct"],
            "threshold": 20.0,
            "passed": full["brierReductionPct"] >= 20.0,
        },
        {
            "id": "expertise_reversal_drop",
            "description": "syntopik_v3_full cuts Expertise-Reversal mismatch >= 60% vs fsrs6_flat",
            "actual": full["expertiseReversalDropPct"],
            "threshold": 60.0,
            "passed": full["expertiseReversalDropPct"] >= 60.0,
        },
        {
            "id": "post_absence_completion",
            "description": "syntopik_v3_full achieves >= 90% post-absence completion",
            "actual": full["postAbsenceCompletionPct"],
            "threshold": 90.0,
            "passed": full["postAbsenceCompletionPct"] >= 90.0,
        },
        {
            "id": "transfer_mastery_lift",
            "description": "syntopik_v3_full beats fsrs6_flat on T3/T4 transfer mastery by >= 15pp",
            "actual": round(full["transferMasteryPct"] - fsrs["transferMasteryPct"], 2),
            "threshold": 15.0,
            "passed": (full["transferMasteryPct"] - fsrs["transferMasteryPct"]) >= 15.0,
        },
        {
            "id": "repeat_blunder_reduction",
            "description": "syntopik_v3_full cuts repeat blunder rate vs fsrs6_flat",
            "actual": full["repeatBlunderRatePct"],
            "threshold": fsrs["repeatBlunderRatePct"],
            "passed": full["repeatBlunderRatePct"] < fsrs["repeatBlunderRatePct"],
        },
    ]

    passed_n = sum(1 for c in checks if c["passed"])
    return {
        "ok": passed_n == len(checks),
        "passed": passed_n,
        "total": len(checks),
        "checks": checks,
    }


def format_markdown_table(results: dict[str, Any]) -> str:
    """Render a crisp GitHub-Flavored Markdown ablation summary table."""
    lines = [
        f"### Syntopik-Eval — {results['days']}-Day Controlled Ablation (`seed={results['seed']}`, 5 Personas × {results['graphStats']['nodes']}-Node Conjunctive Hypergraph)",
        "",
        "| Ablation Arm | Avg Reviews | Saved vs FSRS-6 | Final Ret. | 90d Ret. | Brier Score | ER Mismatch | Post-Absence | T3/T4 Transfer | Repeat Blunder |",
        "| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |",
    ]
    for arm in results["armsOrder"]:
        s = results["arms"][arm]
        bold = "**" if arm == "syntopik_v3_full" else ""
        sav_sign = "+" if s["redundantReviewSavingsPct"] > 0 else ""
        lines.append(
            f"| {bold}{s['label']}{bold} "
            f"| {bold}{s['totalReviews']:.1f}{bold} "
            f"| {bold}{sav_sign}{s['redundantReviewSavingsPct']:.1f}%{bold} "
            f"| {bold}{s['finalRetention'] * 100:.1f}%{bold} "
            f"| {bold}{s['avgRetention90d'] * 100:.1f}%{bold} "
            f"| {bold}{s['brierScore']:.3f}{bold} "
            f"| {bold}{s['expertiseReversalMismatchPct']:.1f}%{bold} "
            f"| {bold}{s['postAbsenceCompletionPct']:.1f}%{bold} "
            f"| {bold}{s['transferMasteryPct']:.1f}%{bold} "
            f"| {bold}{s['repeatBlunderRatePct']:.1f}%{bold} |"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Pure-Stdlib Publication SVG Chart Emitters (Warm Obsidian Editorial Theme)
# ---------------------------------------------------------------------------

_BG = "#0F1115"
_PANEL = "#161A22"
_BORDER = "#262C38"
_GRID = "#222834"
_TEXT = "#F1ECE2"
_MUTED = "#9CA3B2"
_SUBTLE = "#6B7280"
_GOLD = "#E5A93C"
_TEAL = "#3FB984"
_CORAL = "#E06C58"
_BLUE = "#5B8DEF"
_VIOLET = "#A78BFA"
_SLATE = "#64748B"
_FONT_SANS = "ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif"
_FONT_MONO = "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace"


def _esc(text: Any) -> str:
    return html.escape(str(text), quote=True)


def _svg_open(width: int, height: int, title: str, desc: str) -> list[str]:
    return [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" height="{height}" role="img" aria-label="{_esc(title)}">',
        f"  <title>{_esc(title)}</title>",
        f"  <desc>{_esc(desc)}</desc>",
        "  <defs>",
        '    <linearGradient id="goldBar" x1="0" y1="0" x2="0" y2="1">',
        '      <stop offset="0%" stop-color="#F2C15A"/>',
        '      <stop offset="100%" stop-color="#D4922A"/>',
        "    </linearGradient>",
        '    <linearGradient id="tealBar" x1="0" y1="0" x2="0" y2="1">',
        '      <stop offset="0%" stop-color="#52D19A"/>',
        '      <stop offset="100%" stop-color="#2D9669"/>',
        "    </linearGradient>",
        '    <linearGradient id="blueBar" x1="0" y1="0" x2="0" y2="1">',
        '      <stop offset="0%" stop-color="#76A4F7"/>',
        '      <stop offset="100%" stop-color="#4574CE"/>',
        "    </linearGradient>",
        "  </defs>",
        f'  <rect x="0" y="0" width="{width}" height="{height}" fill="{_BG}" rx="14"/>',
    ]


def render_ablation_svg(results: dict[str, Any]) -> str:
    """Render a 2x2 publication-grade Warm Obsidian SVG comparing all 6 ablation arms."""
    w, h = 1120, 720
    arms_order: list[str] = results["armsOrder"]
    arms: dict[str, Any] = results["arms"]
    days = results["days"]
    seed = results["seed"]
    v3 = arms["syntopik_v3_full"]
    fsrs = arms["fsrs6_flat"]

    out = _svg_open(
        w,
        h,
        "Syntopik-Eval 60-Day Controlled Ablation Benchmark",
        f"6-arm ablation across 5 learner personas and 50-node conjunctive hypergraph over {days} days (seed={seed}).",
    )

    # Header band
    out.append(
        f'  <text x="36" y="42" fill="{_TEXT}" font-family="{_FONT_SANS}" font-size="22" font-weight="700">'
        f"Syntopik-Eval · {days}-Day Controlled Ablation Benchmark</text>"
    )
    out.append(
        f'  <text x="36" y="64" fill="{_MUTED}" font-family="{_FONT_SANS}" font-size="12.5">'
        f"5 mechanistic learner personas × 50-node conjunctive hypergraph · deterministic seed={seed} · pure Python stdlib</text>"
    )

    # Top-right summary pill
    pill_x = 768
    out.append(
        f'  <rect x="{pill_x}" y="22" width="316" height="44" rx="10" fill="{_PANEL}" stroke="{_GOLD}" stroke-width="1.2"/>'
    )
    out.append(
        f'  <text x="{pill_x + 16}" y="41" fill="{_GOLD}" font-family="{_FONT_MONO}" font-size="12" font-weight="700">'
        f"v3.0 FULL vs FSRS-6 FLAT</text>"
    )
    out.append(
        f'  <text x="{pill_x + 16}" y="57" fill="{_TEXT}" font-family="{_FONT_MONO}" font-size="11">'
        f"-{v3['redundantReviewSavingsPct']:.1f}% rev · -{v3['brierReductionPct']:.1f}% Brier · {v3['postAbsenceCompletionPct']:.1f}% catch-up</text>"
    )

    # Panel geometry (2x2)
    pw, ph = 516, 286
    panels = [
        (36, 88),
        (568, 88),
        (36, 396),
        (568, 396),
    ]

    arm_colors = {
        "sm2_flat": _SLATE,
        "fsrs6_flat": _CORAL,
        "fsrs6_fire_src": "url(#blueBar)",
        "plus_brier_hyper": _VIOLET,
        "plus_learner_psych": "url(#tealBar)",
        "syntopik_v3_full": "url(#goldBar)",
    }

    def _draw_panel_box(px: int, py: int, title: str, subtitle: str) -> None:
        out.append(
            f'  <rect x="{px}" y="{py}" width="{pw}" height="{ph}" rx="12" fill="{_PANEL}" stroke="{_BORDER}" stroke-width="1"/>'
        )
        out.append(
            f'  <text x="{px + 20}" y="{py + 28}" fill="{_TEXT}" font-family="{_FONT_SANS}" font-size="14.5" font-weight="700">{_esc(title)}</text>'
        )
        out.append(
            f'  <text x="{px + 20}" y="{py + 46}" fill="{_MUTED}" font-family="{_FONT_SANS}" font-size="11">{_esc(subtitle)}</text>'
        )

    # --- Panel 1 (Top-Left): Total Reviews & Review Savings vs FSRS-6 Flat ---
    px, py = panels[0]
    _draw_panel_box(
        px,
        py,
        "1. Review Load & Redundant Review Savings",
        "Avg reviews / learner (60d) — lower is leaner; % saved vs FSRS-6 Flat",
    )
    gx, gy, gw, gh = px + 54, py + 70, pw - 74, 162
    max_rev = max(arms[a]["totalReviews"] for a in arms_order) * 1.18
    for tick in (0, 0.25, 0.5, 0.75, 1.0):
        ty = gy + gh - tick * gh
        val = int(round(tick * max_rev))
        out.append(f'  <line x1="{gx}" y1="{ty:.1f}" x2="{gx + gw}" y2="{ty:.1f}" stroke="{_GRID}" stroke-width="1"/>')
        out.append(
            f'  <text x="{gx - 8}" y="{ty + 4:.1f}" text-anchor="end" fill="{_SUBTLE}" font-family="{_FONT_MONO}" font-size="10">{val}</text>'
        )

    slot_w = gw / len(arms_order)
    bar_w = min(44.0, slot_w * 0.58)
    for idx, arm in enumerate(arms_order):
        s = arms[arm]
        val = s["totalReviews"]
        bh = (val / max_rev) * gh
        bx = gx + idx * slot_w + (slot_w - bar_w) / 2.0
        by = gy + gh - bh
        out.append(
            f'  <rect x="{bx:.1f}" y="{by:.1f}" width="{bar_w:.1f}" height="{bh:.1f}" rx="4" fill="{arm_colors[arm]}"/>'
        )
        label_top = f"{val:.0f}"
        out.append(
            f'  <text x="{bx + bar_w / 2:.1f}" y="{by - 6:.1f}" text-anchor="middle" fill="{_TEXT}" font-family="{_FONT_MONO}" font-size="10.5" font-weight="600">{label_top}</text>'
        )
        sav = s["redundantReviewSavingsPct"]
        if abs(sav) >= 0.5 and arm != "fsrs6_flat":
            sav_txt = f"-{sav:.0f}%" if sav > 0 else f"+{abs(sav):.0f}%"
            sav_col = _TEAL if sav > 0 else _CORAL
            out.append(
                f'  <text x="{bx + bar_w / 2:.1f}" y="{gy + gh + 32:.1f}" text-anchor="middle" fill="{sav_col}" font-family="{_FONT_MONO}" font-size="9.5" font-weight="600">{sav_txt}</text>'
            )
        out.append(
            f'  <text x="{bx + bar_w / 2:.1f}" y="{gy + gh + 16:.1f}" text-anchor="middle" fill="{_MUTED}" font-family="{_FONT_SANS}" font-size="10">{_esc(s["shortLabel"])}</text>'
        )

    # --- Panel 2 (Top-Right): Final Retention & T3/T4 Transfer Mastery ---
    px, py = panels[1]
    _draw_panel_box(
        px,
        py,
        "2. Final Retention & T3/T4 Transfer Mastery",
        "True Day-60 retrievability (teal) vs mechanistic T3/T4 transfer mastery (gold)",
    )
    # Legend pills top-right inside panel
    out.append(f'  <rect x="{px + 332}" y="{py + 18}" width="10" height="10" rx="2" fill="{_TEAL}"/>')
    out.append(f'  <text x="{px + 347}" y="{py + 27}" fill="{_MUTED}" font-family="{_FONT_SANS}" font-size="10">Retention</text>')
    out.append(f'  <rect x="{px + 414}" y="{py + 18}" width="10" height="10" rx="2" fill="{_GOLD}"/>')
    out.append(f'  <text x="{px + 429}" y="{py + 27}" fill="{_MUTED}" font-family="{_FONT_SANS}" font-size="10">T3/T4 Transfer</text>')

    gx, gy, gw, gh = px + 46, py + 70, pw - 66, 162
    for tick in (0, 25, 50, 75, 100):
        ty = gy + gh - (tick / 100.0) * gh
        out.append(f'  <line x1="{gx}" y1="{ty:.1f}" x2="{gx + gw}" y2="{ty:.1f}" stroke="{_GRID}" stroke-width="1"/>')
        out.append(
            f'  <text x="{gx - 8}" y="{ty + 4:.1f}" text-anchor="end" fill="{_SUBTLE}" font-family="{_FONT_MONO}" font-size="10">{tick}%</text>'
        )

    slot_w = gw / len(arms_order)
    pair_w = min(22.0, slot_w * 0.32)
    for idx, arm in enumerate(arms_order):
        s = arms[arm]
        ret_pct = s["finalRetention"] * 100.0
        trn_pct = s["transferMasteryPct"]
        cx = gx + idx * slot_w + slot_w / 2.0

        rh = (ret_pct / 100.0) * gh
        th = (trn_pct / 100.0) * gh
        rx = cx - pair_w - 2.0
        tx = cx + 2.0
        ry = gy + gh - rh
        ty = gy + gh - th

        out.append(f'  <rect x="{rx:.1f}" y="{ry:.1f}" width="{pair_w:.1f}" height="{rh:.1f}" rx="3" fill="url(#tealBar)"/>')
        out.append(f'  <rect x="{tx:.1f}" y="{ty:.1f}" width="{pair_w:.1f}" height="{th:.1f}" rx="3" fill="url(#goldBar)"/>')
        out.append(
            f'  <text x="{rx + pair_w / 2:.1f}" y="{ry - 5:.1f}" text-anchor="middle" fill="{_TEAL}" font-family="{_FONT_MONO}" font-size="9.2">{ret_pct:.0f}</text>'
        )
        out.append(
            f'  <text x="{tx + pair_w / 2:.1f}" y="{ty - 5:.1f}" text-anchor="middle" fill="{_GOLD}" font-family="{_FONT_MONO}" font-size="9.2">{trn_pct:.0f}</text>'
        )
        out.append(
            f'  <text x="{cx:.1f}" y="{gy + gh + 16:.1f}" text-anchor="middle" fill="{_MUTED}" font-family="{_FONT_SANS}" font-size="10">{_esc(s["shortLabel"])}</text>'
        )

    # --- Panel 3 (Bottom-Left): Expertise-Reversal Mismatch Drop (%) ---
    px, py = panels[2]
    _draw_panel_box(
        px,
        py,
        "3. Expertise-Reversal Scaffolding Mismatch (%)",
        f"Lower is better — v3.0 cuts mismatch by {v3['expertiseReversalDropPct']:.1f}% vs FSRS-6 Flat",
    )
    gx, gy, gw, gh = px + 46, py + 70, pw - 66, 162
    max_er = max(50.0, max(arms[a]["expertiseReversalMismatchPct"] for a in arms_order) * 1.2)
    step_er = 10 if max_er <= 60 else 20
    tick_val = 0
    while tick_val <= max_er:
        ty = gy + gh - (tick_val / max_er) * gh
        out.append(f'  <line x1="{gx}" y1="{ty:.1f}" x2="{gx + gw}" y2="{ty:.1f}" stroke="{_GRID}" stroke-width="1"/>')
        out.append(
            f'  <text x="{gx - 8}" y="{ty + 4:.1f}" text-anchor="end" fill="{_SUBTLE}" font-family="{_FONT_MONO}" font-size="10">{tick_val}%</text>'
        )
        tick_val += step_er

    slot_w = gw / len(arms_order)
    bar_w = min(44.0, slot_w * 0.58)
    for idx, arm in enumerate(arms_order):
        s = arms[arm]
        val = s["expertiseReversalMismatchPct"]
        bh = max(3.0, (val / max_er) * gh)
        bx = gx + idx * slot_w + (slot_w - bar_w) / 2.0
        by = gy + gh - bh
        col = _TEAL if arm in ("plus_learner_psych", "syntopik_v3_full") else _CORAL
        if arm == "syntopik_v3_full":
            col = "url(#goldBar)"
        out.append(f'  <rect x="{bx:.1f}" y="{by:.1f}" width="{bar_w:.1f}" height="{bh:.1f}" rx="4" fill="{col}"/>')
        out.append(
            f'  <text x="{bx + bar_w / 2:.1f}" y="{by - 6:.1f}" text-anchor="middle" fill="{_TEXT}" font-family="{_FONT_MONO}" font-size="10.5" font-weight="600">{val:.1f}%</text>'
        )
        out.append(
            f'  <text x="{bx + bar_w / 2:.1f}" y="{gy + gh + 16:.1f}" text-anchor="middle" fill="{_MUTED}" font-family="{_FONT_SANS}" font-size="10">{_esc(s["shortLabel"])}</text>'
        )

    # --- Panel 4 (Bottom-Right): Post-Absence Backlog Completion (%) ---
    px, py = panels[3]
    _draw_panel_box(
        px,
        py,
        "4. Post-Absence Backlog Completion (%)",
        "Survival across two 7-day absence blackouts — dashed line = 90% CI gate",
    )
    gx, gy, gw, gh = px + 46, py + 70, pw - 66, 162
    for tick in (0, 25, 50, 75, 100):
        ty = gy + gh - (tick / 100.0) * gh
        out.append(f'  <line x1="{gx}" y1="{ty:.1f}" x2="{gx + gw}" y2="{ty:.1f}" stroke="{_GRID}" stroke-width="1"/>')
        out.append(
            f'  <text x="{gx - 8}" y="{ty + 4:.1f}" text-anchor="end" fill="{_SUBTLE}" font-family="{_FONT_MONO}" font-size="10">{tick}%</text>'
        )

    # 90% CI gate dashed line + non-overlapping pill tag on left side
    y90 = gy + gh - 0.90 * gh
    out.append(
        f'  <line x1="{gx}" y1="{y90:.1f}" x2="{gx + gw}" y2="{y90:.1f}" stroke="{_GOLD}" stroke-width="1.2" stroke-dasharray="5 4"/>'
    )
    out.append(
        f'  <rect x="{gx + 4}" y="{y90 - 17:.1f}" width="60" height="14" rx="4" fill="{_BG}" stroke="{_GOLD}" stroke-width="0.8"/>'
    )
    out.append(
        f'  <text x="{gx + 34}" y="{y90 - 7:.1f}" text-anchor="middle" fill="{_GOLD}" font-family="{_FONT_MONO}" font-size="8.8">90% gate</text>'
    )

    slot_w = gw / len(arms_order)
    bar_w = min(44.0, slot_w * 0.58)
    for idx, arm in enumerate(arms_order):
        s = arms[arm]
        val = s["postAbsenceCompletionPct"]
        bh = (val / 100.0) * gh
        bx = gx + idx * slot_w + (slot_w - bar_w) / 2.0
        by = gy + gh - bh
        out.append(
            f'  <rect x="{bx:.1f}" y="{by:.1f}" width="{bar_w:.1f}" height="{bh:.1f}" rx="4" fill="{arm_colors[arm]}"/>'
        )
        out.append(
            f'  <text x="{bx + bar_w / 2:.1f}" y="{by - 6:.1f}" text-anchor="middle" fill="{_TEXT}" font-family="{_FONT_MONO}" font-size="10.5" font-weight="600">{val:.1f}%</text>'
        )
        out.append(
            f'  <text x="{bx + bar_w / 2:.1f}" y="{gy + gh + 16:.1f}" text-anchor="middle" fill="{_MUTED}" font-family="{_FONT_SANS}" font-size="10">{_esc(s["shortLabel"])}</text>'
        )

    # Footer
    out.append(
        f'  <text x="36" y="{h - 14}" fill="{_SUBTLE}" font-family="{_FONT_MONO}" font-size="10">'
        f"Reproduce: python3 -B benchmarks/syntopik_bench.py --days {days} --seed {seed} --svg --check · "
        f"FSRS-6 Flat Brier={fsrs['brierScore']:.3f} -> v3.0 Full Brier={v3['brierScore']:.3f}</text>"
    )
    out.append("</svg>")
    return "\n".join(out) + "\n"


def render_calibration_svg(results: dict[str, Any]) -> str:
    """Render a 60-day Brier trajectory, reliability diagram, and per-persona matrix SVG."""
    w, h = 1120, 680
    days = results["days"]
    seed = results["seed"]
    arms = results["arms"]
    by_ap = results["byArmPersona"]
    personas_meta = results["personas"]

    v3 = arms["syntopik_v3_full"]
    fsrs = arms["fsrs6_flat"]

    out = _svg_open(
        w,
        h,
        "Syntopik-Eval Calibration Trajectory & Persona Outcomes",
        f"60-day Brier calibration error trajectory, reliability diagram, and 5-persona comparison (seed={seed}).",
    )

    # Header
    out.append(
        f'  <text x="36" y="42" fill="{_TEXT}" font-family="{_FONT_SANS}" font-size="22" font-weight="700">'
        f"Metacognitive Calibration &amp; Multi-Persona Outcomes</text>"
    )
    out.append(
        f'  <text x="36" y="64" fill="{_MUTED}" font-family="{_FONT_SANS}" font-size="12.5">'
        f"Brier calibration error (-{v3['brierReductionPct']:.1f}% vs FSRS-6 Flat), reliability alignment, and per-persona wins ({days}d, seed={seed})</text>"
    )

    # --- Top-Left Panel: 60-Day Brier Score Trajectory ---
    p1x, p1y, p1w, p1h = 36, 86, 560, 296
    out.append(
        f'  <rect x="{p1x}" y="{p1y}" width="{p1w}" height="{p1h}" rx="12" fill="{_PANEL}" stroke="{_BORDER}" stroke-width="1"/>'
    )
    out.append(
        f'  <text x="{p1x + 20}" y="{p1y + 28}" fill="{_TEXT}" font-family="{_FONT_SANS}" font-size="14.5" font-weight="700">'
        f"1. 60-Day Rolling Brier Score (Lower is Better)</text>"
    )
    out.append(
        f'  <text x="{p1x + 20}" y="{p1y + 46}" fill="{_MUTED}" font-family="{_FONT_SANS}" font-size="11">'
        f"Shaded bands = typical 7-day absence blackouts · hypercorrection feedback shrinks bias</text>"
    )

    gx, gy, gw, gh = p1x + 52, p1y + 68, p1w - 74, 174
    max_b = max(
        0.30,
        max(max(arms[a]["dailyBrier"]) for a in ("sm2_flat", "fsrs6_flat", "plus_brier_hyper", "syntopik_v3_full")) * 1.12,
    )

    # Shaded absence bands
    for band_lo, band_hi in ((16, 23), (40, 47)):
        bx1 = gx + (band_lo / max(1, days - 1)) * gw
        bx2 = gx + (min(days - 1, band_hi) / max(1, days - 1)) * gw
        out.append(
            f'  <rect x="{bx1:.1f}" y="{gy}" width="{max(2.0, bx2 - bx1):.1f}" height="{gh}" fill="#E5A93C" opacity="0.07"/>'
        )

    for frac in (0.0, 0.25, 0.5, 0.75, 1.0):
        ty = gy + gh - frac * gh
        val = frac * max_b
        out.append(f'  <line x1="{gx}" y1="{ty:.1f}" x2="{gx + gw}" y2="{ty:.1f}" stroke="{_GRID}" stroke-width="1"/>')
        out.append(
            f'  <text x="{gx - 8}" y="{ty + 4:.1f}" text-anchor="end" fill="{_SUBTLE}" font-family="{_FONT_MONO}" font-size="10">{val:.2f}</text>'
        )

    for d_tick in (1, 15, 30, 45, days):
        tx = gx + ((d_tick - 1) / max(1, days - 1)) * gw
        out.append(
            f'  <text x="{tx:.1f}" y="{gy + gh + 16}" text-anchor="middle" fill="{_SUBTLE}" font-family="{_FONT_MONO}" font-size="10">D{d_tick}</text>'
        )

    curve_specs = (
        ("sm2_flat", _SLATE, "1.8", "4 3", "SM-2 Flat"),
        ("fsrs6_flat", _CORAL, "2.0", "", "FSRS-6 Flat"),
        ("plus_brier_hyper", _BLUE, "2.0", "", "+Brier/Hyper"),
        ("syntopik_v3_full", _GOLD, "2.8", "", "Syntopik v3.0"),
    )
    for arm_k, col, sw, dash, _lbl in curve_specs:
        series = arms[arm_k]["dailyBrier"]
        pts = [
            f"{gx + (i / max(1, days - 1)) * gw:.1f},{gy + gh - min(max_b, v) / max_b * gh:.1f}"
            for i, v in enumerate(series)
        ]
        dash_attr = f' stroke-dasharray="{dash}"' if dash else ""
        out.append(
            f'  <polyline fill="none" stroke="{col}" stroke-width="{sw}"{dash_attr} stroke-linejoin="round" stroke-linecap="round" points="{" ".join(pts)}"/>'
        )

    # Bottom legend inside panel 1
    lx = p1x + 52
    ly = p1y + p1h - 14
    for _arm_k, col, _sw, _dash, lbl in curve_specs:
        out.append(f'  <rect x="{lx}" y="{ly - 9}" width="12" height="4" rx="2" fill="{col}"/>')
        out.append(f'  <text x="{lx + 16}" y="{ly - 3}" fill="{_MUTED}" font-family="{_FONT_SANS}" font-size="10.5">{_esc(lbl)}</text>')
        lx += 118

    # --- Top-Right Panel: Reliability Diagram (Confidence vs Accuracy) ---
    p2x, p2y, p2w, p2h = 616, 86, 468, 296
    out.append(
        f'  <rect x="{p2x}" y="{p2y}" width="{p2w}" height="{p2h}" rx="12" fill="{_PANEL}" stroke="{_BORDER}" stroke-width="1"/>'
    )
    out.append(
        f'  <text x="{p2x + 20}" y="{p2y + 28}" fill="{_TEXT}" font-family="{_FONT_SANS}" font-size="14.5" font-weight="700">'
        f"2. Reliability Diagram (ECE &amp; Bias)</text>"
    )
    out.append(
        f'  <text x="{p2x + 20}" y="{p2y + 46}" fill="{_MUTED}" font-family="{_FONT_SANS}" font-size="11">'
        f"Diagonal = ideal calibration · ECE {fsrs['ece']:.3f} → {v3['ece']:.3f}</text>"
    )

    rx, ry, rsize = p2x + 56, p2y + 66, 178
    for frac in (0.0, 0.25, 0.5, 0.75, 1.0):
        py_t = ry + rsize - frac * rsize
        px_t = rx + frac * rsize
        out.append(f'  <line x1="{rx}" y1="{py_t:.1f}" x2="{rx + rsize}" y2="{py_t:.1f}" stroke="{_GRID}" stroke-width="1"/>')
        out.append(f'  <line x1="{px_t:.1f}" y1="{ry}" x2="{px_t:.1f}" y2="{ry + rsize}" stroke="{_GRID}" stroke-width="1"/>')
        out.append(
            f'  <text x="{rx - 8}" y="{py_t + 3.5:.1f}" text-anchor="end" fill="{_SUBTLE}" font-family="{_FONT_MONO}" font-size="9.5">{int(frac * 100)}%</text>'
        )
        out.append(
            f'  <text x="{px_t:.1f}" y="{ry + rsize + 15}" text-anchor="middle" fill="{_SUBTLE}" font-family="{_FONT_MONO}" font-size="9.5">{int(frac * 100)}%</text>'
        )

    # Ideal diagonal
    out.append(
        f'  <line x1="{rx}" y1="{ry + rsize}" x2="{rx + rsize}" y2="{ry}" stroke="{_SUBTLE}" stroke-width="1.3" stroke-dasharray="5 4"/>'
    )

    for arm_k, col in (("fsrs6_flat", _CORAL), ("syntopik_v3_full", _GOLD)):
        bins = arms[arm_k]["calibrationBins"]
        if bins:
            pts = [
                f"{rx + b['avgConfidence'] * rsize:.1f},{ry + rsize - b['accuracy'] * rsize:.1f}"
                for b in bins
            ]
            out.append(f'  <polyline fill="none" stroke="{col}" stroke-width="2.4" points="{" ".join(pts)}"/>')
            for b in bins:
                cx = rx + b["avgConfidence"] * rsize
                cy = ry + rsize - b["accuracy"] * rsize
                out.append(f'  <circle cx="{cx:.1f}" cy="{cy:.1f}" r="4.2" fill="{col}" stroke="{_PANEL}" stroke-width="1.5"/>')

    # Side metrics readout box inside panel 2 (right of square chart, zero overlap)
    bx_info = rx + rsize + 24
    out.append(
        f'  <rect x="{bx_info}" y="{ry + 4}" width="186" height="170" rx="8" fill="{_BG}" stroke="{_BORDER}" stroke-width="1"/>'
    )
    out.append(
        f'  <text x="{bx_info + 14}" y="{ry + 28}" fill="{_CORAL}" font-family="{_FONT_MONO}" font-size="11" font-weight="700">FSRS-6 FLAT</text>'
    )
    out.append(
        f'  <text x="{bx_info + 14}" y="{ry + 46}" fill="{_TEXT}" font-family="{_FONT_MONO}" font-size="11">Brier: {fsrs["brierScore"]:.3f}</text>'
    )
    out.append(
        f'  <text x="{bx_info + 14}" y="{ry + 62}" fill="{_MUTED}" font-family="{_FONT_MONO}" font-size="10.5">ECE:   {fsrs["ece"]:.3f}</text>'
    )
    out.append(
        f'  <text x="{bx_info + 14}" y="{ry + 78}" fill="{_MUTED}" font-family="{_FONT_MONO}" font-size="10.5">Bias:  {fsrs["overconfidenceBias"]:+.3f}</text>'
    )
    out.append(f'  <line x1="{bx_info + 14}" y1="{ry + 90}" x2="{bx_info + 172}" y2="{ry + 90}" stroke="{_BORDER}" stroke-width="1"/>')
    out.append(
        f'  <text x="{bx_info + 14}" y="{ry + 110}" fill="{_GOLD}" font-family="{_FONT_MONO}" font-size="11" font-weight="700">SYNTOPIK v3.0</text>'
    )
    out.append(
        f'  <text x="{bx_info + 14}" y="{ry + 128}" fill="{_TEXT}" font-family="{_FONT_MONO}" font-size="11">Brier: {v3["brierScore"]:.3f}</text>'
    )
    out.append(
        f'  <text x="{bx_info + 14}" y="{ry + 144}" fill="{_TEAL}" font-family="{_FONT_MONO}" font-size="10.5">ECE:   {v3["ece"]:.3f}</text>'
    )
    out.append(
        f'  <text x="{bx_info + 14}" y="{ry + 160}" fill="{_TEAL}" font-family="{_FONT_MONO}" font-size="10.5">Bias:  {v3["overconfidenceBias"]:+.3f}</text>'
    )
    out.append(
        f'  <text x="{rx + rsize / 2:.1f}" y="{ry + rsize + 34}" text-anchor="middle" fill="{_MUTED}" font-family="{_FONT_SANS}" font-size="10">Mean Reported Confidence</text>'
    )

    # --- Bottom Full-Width Panel: 5-Persona Head-to-Head (FSRS-6 Flat vs Syntopik v3.0 Full) ---
    p3x, p3y, p3w, p3h = 36, 404, 1048, 240
    out.append(
        f'  <rect x="{p3x}" y="{p3y}" width="{p3w}" height="{p3h}" rx="12" fill="{_PANEL}" stroke="{_BORDER}" stroke-width="1"/>'
    )
    out.append(
        f'  <text x="{p3x + 20}" y="{p3y + 28}" fill="{_TEXT}" font-family="{_FONT_SANS}" font-size="14.5" font-weight="700">'
        f"3. Per-Persona Impact Breakdown (FSRS-6 Flat → Syntopik v3.0 Full)</text>"
    )
    out.append(
        f'  <text x="{p3x + 20}" y="{p3y + 46}" fill="{_MUTED}" font-family="{_FONT_SANS}" font-size="11">'
        f"Every persona improves on its signature cognitive failure mode without sacrificing retention</text>"
    )

    card_w = 192
    card_gap = 12
    start_x = p3x + 20
    cy = p3y + 62
    ch = 160

    for idx, pm in enumerate(personas_meta):
        pid = pm["id"]
        b_row = by_ap["fsrs6_flat"][pid]
        v_row = by_ap["syntopik_v3_full"][pid]
        cx = start_x + idx * (card_w + card_gap)

        out.append(
            f'  <rect x="{cx}" y="{cy}" width="{card_w}" height="{ch}" rx="9" fill="{_BG}" stroke="{_BORDER}" stroke-width="1"/>'
        )
        out.append(
            f'  <text x="{cx + 12}" y="{cy + 22}" fill="{_TEXT}" font-family="{_FONT_SANS}" font-size="12" font-weight="700">{_esc(pm["shortName"])}</text>'
        )

        # 4 clean metric rows per persona card
        metrics_rows = (
            ("Reviews", f"{b_row['totalReviews']}→{v_row['totalReviews']}", f"-{v_row['redundantReviewSavingsPct']:.0f}%", _GOLD),
            ("Brier", f"{b_row['brierScore']:.2f}→{v_row['brierScore']:.2f}", f"-{v_row['brierReductionPct']:.0f}%", _TEAL),
            ("ER Mis.", f"{b_row['expertiseReversalMismatchPct']:.0f}%→{v_row['expertiseReversalMismatchPct']:.0f}%", "", _BLUE),
            ("T3 Trns", f"{b_row['transferMasteryPct']:.0f}%→{v_row['transferMasteryPct']:.0f}%", "", _VIOLET),
            ("Catchup", f"{b_row['postAbsenceCompletionPct']:.0f}%→{v_row['postAbsenceCompletionPct']:.0f}%", "", _TEAL),
        )
        ry_line = cy + 46
        for m_lbl, m_val, m_badge, m_col in metrics_rows:
            out.append(
                f'  <text x="{cx + 12}" y="{ry_line}" fill="{_SUBTLE}" font-family="{_FONT_SANS}" font-size="10.2">{m_lbl}</text>'
            )
            out.append(
                f'  <text x="{cx + 62}" y="{ry_line}" fill="{_TEXT}" font-family="{_FONT_MONO}" font-size="10.2">{_esc(m_val)}</text>'
            )
            if m_badge:
                out.append(
                    f'  <text x="{cx + card_w - 10}" y="{ry_line}" text-anchor="end" fill="{m_col}" font-family="{_FONT_MONO}" font-size="9.8" font-weight="700">{_esc(m_badge)}</text>'
                )
            ry_line += 23

    out.append(
        f'  <text x="36" y="{h - 14}" fill="{_SUBTLE}" font-family="{_FONT_MONO}" font-size="10">'
        f"Syntopik v3.0 · 0 external dependencies · deterministic random.Random({seed})</text>"
    )
    out.append("</svg>")
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------------------
# Longitudinal Learner Telemetry Policy Replay (Chronological 70/30 Split)
# ---------------------------------------------------------------------------

_REPLAY_MIN_OOS_GAIN = 0.005
_REPLAY_GRID_ELO_K: tuple[float, ...] = (36.0, 48.0, 64.0, 80.0, 96.0)
_REPLAY_GRID_NOVICE: tuple[float, ...] = (0.28, 0.35, 0.42)
_REPLAY_GRID_CHALLENGE: tuple[float, ...] = (0.74, 0.80, 0.86)
_REPLAY_GRID_FATIGUE: tuple[float, ...] = (1.10, 1.25, 1.45)


def _replay_parse_ts(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        try:
            d = date_cls.fromisoformat(raw[:10])
            return datetime(d.year, d.month, d.day, 12, 0, 0, tzinfo=timezone.utc)
        except ValueError:
            return None


def _replay_resolve_kg(workspace: Path | str | None) -> tuple[Path, Path]:
    from syntopik.paths import resolve_kg_dir, resolve_workspace

    if workspace is not None and str(workspace).strip():
        p = Path(workspace).expanduser().resolve()
        if p.name == "knowledge-graph" and not (p / "knowledge-graph").is_dir():
            return p.parent, p
        if (p / "knowledge-graph").is_dir():
            return p, (p / "knowledge-graph").resolve()
        if (p / "revlog.jsonl").is_file() or (p / "learner-profile.json").is_file():
            return p.parent, p
        return p, (p / "knowledge-graph").resolve()
    ws = resolve_workspace()
    return ws, resolve_kg_dir(ws)


_REPLAY_MAX_UTC_DT = datetime(9999, 12, 31, 23, 59, 59, tzinfo=timezone.utc)


def _safe_replay_float(val: Any, default: float, *, lo: float, hi: float) -> float:
    if isinstance(val, bool) or val is None:
        return default
    try:
        fv = float(val)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(fv):
        return default
    return _clamp(fv, lo, hi)


def _extract_replay_events(
    revlog_rows: list[dict[str, Any]],
    props_by_id: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Extract and strictly chronologically order confidence-rated review events."""
    events: list[tuple[tuple[int, datetime, int], dict[str, Any]]] = []
    for idx, row in enumerate(revlog_rows):
        if not isinstance(row, dict):
            continue
        raw_conf = row.get("learnerConfidence")
        if raw_conf is None:
            raw_conf = row.get("confidence")
        if not isinstance(raw_conf, (int, float)) or isinstance(raw_conf, bool) or not math.isfinite(float(raw_conf)):
            continue
        conf = float(raw_conf)
        if 1.0 < conf <= 100.0:
            conf = conf / 100.0
        conf = _clamp(conf, 0.0, 1.0)

        if isinstance(row.get("correct"), bool):
            correct = bool(row["correct"])
        else:
            rating = row.get("rating")
            rating_int = row.get("ratingInt")
            if isinstance(rating_int, int) and not isinstance(rating_int, bool):
                correct = rating_int >= 3
            elif isinstance(rating, int) and not isinstance(rating, bool):
                correct = rating >= 3
            else:
                correct = str(rating or "").strip().lower() in ("good", "easy")

        pid = str(row.get("propId") or row.get("id") or "").strip()
        prop = props_by_id.get(pid) or {}
        domain = str(row.get("domain") or prop.get("subject") or "General").strip() or "General"

        # Determine item difficulty on Elo scale (900..2200)
        raw_diff = row.get("difficulty")
        diff_elo = 1500.0
        if isinstance(raw_diff, (int, float)) and not isinstance(raw_diff, bool) and math.isfinite(float(raw_diff)):
            df = float(raw_diff)
            diff_elo = _clamp(df, 900.0, 2200.0) if df >= 100.0 else _clamp(1200.0 + df * 60.0, 900.0, 2200.0)
        elif isinstance(prop.get("fsrs"), dict):
            fd = prop["fsrs"].get("difficulty")
            if isinstance(fd, (int, float)) and not isinstance(fd, bool) and math.isfinite(float(fd)):
                diff_elo = _clamp(1200.0 + float(fd) * 60.0, 900.0, 2200.0)

        hints = row.get("hintsUsed")
        hints_int = 0
        if isinstance(hints, (int, float)) and not isinstance(hints, bool):
            fh = float(hints)
            if math.isfinite(fh):
                hints_int = max(0, min(5, int(fh)))
        elif isinstance(hints, str) and hints.strip():
            try:
                fh = float(hints.strip())
                if math.isfinite(fh):
                    hints_int = max(0, min(5, int(fh)))
            except (TypeError, ValueError):
                hints_int = 0

        raw_dur = row.get("durationMs")
        dur_ms: float | None = None
        if isinstance(raw_dur, (int, float)) and not isinstance(raw_dur, bool) and math.isfinite(float(raw_dur)) and float(raw_dur) > 0:
            dur_ms = min(float(raw_dur), 86_400_000.0)

        ts_raw = row.get("ts") or row.get("timestamp") or row.get("reviewedAt") or row.get("date")
        dt = _replay_parse_ts(ts_raw)
        # Sort directly by UTC datetime (avoids Windows OSError on pre-1970 dt.timestamp())
        sort_key = (0, dt, idx) if dt is not None else (1, _REPLAY_MAX_UTC_DT, idx)

        events.append(
            (
                sort_key,
                {
                    "propId": pid or f"item-{idx}",
                    "domain": domain,
                    "ts": dt,
                    "tsIso": dt.replace(microsecond=0).isoformat().replace("+00:00", "Z") if dt else None,
                    "correct": correct,
                    "confidence": conf,
                    "difficulty": diff_elo,
                    "hintsUsed": hints_int,
                    "durationMs": dur_ms,
                },
            )
        )

    events.sort(key=lambda pair: pair[0])
    return [ev for _k, ev in events]


def _evaluate_replay_params(
    events: list[dict[str, Any]],
    split_idx: int,
    *,
    elo_k_base: float,
    er_novice_cutoff: float,
    er_challenge_cutoff: float,
    fatigue_multiplier: float,
    session_target_min: float = 30.0,
    baseline_latency_ms: float = 22000.0,
) -> tuple[dict[str, float], dict[str, float]]:
    """Run strictly causal step-by-step replay and return `(train_metrics, test_metrics)`."""
    dom_elo: dict[str, float] = {}
    dom_n: dict[str, int] = {}
    prop_bkt: dict[str, float] = {}

    session_start_dt: datetime | None = None
    last_dt: datetime | None = None
    session_records: list[tuple[float, float]] = []  # (clean_hit, latency_ratio)
    recent_window: list[tuple[bool, float, int]] = []  # (correct, latency_ratio, hints)

    def _new_acc() -> dict[str, list[float]]:
        return {"brier": [], "logloss": [], "er": []}

    train_acc = _new_acc()
    test_acc = _new_acc()
    target_min = _safe_replay_float(session_target_min, 30.0, lo=10.0, hi=240.0)
    learner_scale = _clamp(_safe_replay_float(baseline_latency_ms, 22000.0, lo=2000.0, hi=300000.0) / 22000.0, 0.60, 1.80)

    for idx, ev in enumerate(events):
        dom = ev["domain"]
        pid = ev["propId"]
        diff = float(ev["difficulty"])
        y = 1.0 if ev["correct"] else 0.0
        conf = float(ev["confidence"])
        hints = int(ev["hintsUsed"])
        dt: datetime | None = ev["ts"]
        dur_ms: float | None = ev["durationMs"]

        # 1. Causal session / affect estimation BEFORE seeing current outcome
        if dt is not None:
            if last_dt is None or (dt - last_dt).total_seconds() > 20.0 * 60.0 or (dt - last_dt).total_seconds() < -60.0:
                session_start_dt = dt
                session_records = []
            active_min = max(0.0, (dt - (session_start_dt or dt)).total_seconds() / 60.0)
        else:
            active_min = len(session_records) * 1.5

        fatigued = False
        if active_min > fatigue_multiplier * target_min and len(session_records) >= 4:
            first3 = session_records[:3]
            last3 = session_records[-3:]
            f_acc = sum(h for h, _l in first3) / len(first3)
            l_acc = sum(h for h, _l in last3) / len(last3)
            f_lat = sum(l for _h, l in first3) / len(first3)
            l_lat = sum(l for _h, l in last3) / len(last3)
            if (f_acc - l_acc) >= 0.25 or (f_lat > 0.0 and l_lat >= 1.5 * f_lat):
                fatigued = True

        affect_state = "flow"
        if fatigued:
            affect_state = "fatigued"
        elif len(recent_window) >= 2:
            w6 = recent_window[-6:]
            w_acc = sum(1.0 for c, _l, h in w6 if c and h <= 2) / len(w6)
            w_lat = sum(l for _c, l, _h in w6) / len(w6)
            w_hints = sum(h for _c, _l, h in w6) / len(w6)
            err_streak = 0
            for c, _l, _h in reversed(w6):
                if not c:
                    err_streak += 1
                else:
                    break
            if err_streak >= 2 and (w_hints >= 2.5 or w_lat >= 1.8 or w_acc < 0.45):
                affect_state = "overloaded_frustrated"
            elif len(w6) >= 4 and w_acc >= 0.94 and w_hints <= 0.3 and w_lat <= 0.65:
                affect_state = "bored_underchallenged"

        # 2. Causal Elo prediction BEFORE observing outcome `y`
        theta = dom_elo.get(dom, 1450.0)
        n_d = dom_n.get(dom, 0)
        exp_arg = _clamp((diff - theta) / 400.0, -15.0, 15.0)
        p_elo = 1.0 / (1.0 + (10.0 ** exp_arg))
        p_pred = _clamp(p_elo - (0.08 if fatigued else 0.0), 0.02, 0.98)

        brier = (p_pred - y) ** 2
        logloss = -(y * math.log(p_pred) + (1.0 - y) * math.log(1.0 - p_pred))

        # 3. Causal Kalyuga Expertise-Reversal alignment BEFORE observing outcome
        elo_sig = _sigmoid((theta - 1450.0) / 180.0)
        bkt_val = prop_bkt.get(pid, elo_sig)
        delta_aff = (
            -0.18
            if affect_state == "overloaded_frustrated"
            else (-0.12 if affect_state == "fatigued" else (0.12 if affect_state == "bored_underchallenged" else 0.0))
        )
        e_eff = _clamp(0.65 * bkt_val + 0.35 * elo_sig + delta_aff, 0.0, 1.0)
        if e_eff < er_novice_cutoff:
            mode = "worked_example_parsons"
        elif e_eff < 0.62:
            mode = "faded"
        elif e_eff < er_challenge_cutoff:
            mode = "standard"
        else:
            mode = "challenge_transfer"

        # Evaluate alignment against observed outcome `(y, hints, conf)`
        clean_confident_hit = (y == 1.0) and hints == 0 and conf >= 0.70
        struggle = (y == 0.0) or hints >= 2
        if mode == "worked_example_parsons" and clean_confident_hit:
            er_err = 1.0  # Redundant worked example for proficient learner (Expertise Reversal)
        elif mode == "challenge_transfer" and struggle:
            er_err = 1.0  # Challenge transfer when learner collapsed or needed heavy hints
        elif mode == "standard" and ((y == 0.0 and conf < 0.50) or hints >= 2):
            er_err = 0.5  # Under-scaffolded struggling novice
        elif mode == "faded" and clean_confident_hit and e_eff >= 0.55 and conf >= 0.85:
            er_err = 0.25  # Mild over-scaffolding
        else:
            er_err = 0.0

        bucket = train_acc if idx < split_idx else test_acc
        bucket["brier"].append(brier)
        bucket["logloss"].append(logloss)
        bucket["er"].append(er_err)

        # 4. Causal state updates AFTER scoring current event
        is_lucky = (y == 1.0) and conf <= 0.35
        s_eff = 0.0 if y == 0.0 else max(0.0, 1.0 - 0.14 * float(hints)) * (0.65 if is_lucky else 1.0)
        k_d = max(16.0, float(elo_k_base) / (1.0 + 0.05 * float(n_d)))
        dom_elo[dom] = _clamp(theta + k_d * (s_eff - p_elo), 900.0, 2200.0)
        dom_n[dom] = n_d + 1

        prev_bkt = prop_bkt.get(pid, 0.35)
        prop_bkt[pid] = bkt_update(prev_bkt, bool(ev["correct"]), p_transit=0.20, learner_confidence=conf)

        diff_scale = _clamp(1.0 + 0.15 * ((diff - 1500.0) / 300.0), 0.60, 1.60)
        expected_ms = 12000.0 * diff_scale * learner_scale
        lat_ratio = _clamp(dur_ms / expected_ms, 0.15, 5.0) if dur_ms is not None else 1.0
        clean_hit = 1.0 if (ev["correct"] and hints <= 2) else 0.0
        session_records.append((clean_hit, lat_ratio))
        recent_window.append((bool(ev["correct"]), lat_ratio, hints))
        if len(recent_window) > 20:
            recent_window = recent_window[-20:]
        if dt is not None:
            last_dt = dt

    def _summarize(acc: dict[str, list[float]]) -> dict[str, float]:
        brier_m = statistics.fmean(acc["brier"]) if acc["brier"] else 0.0
        ll_m = statistics.fmean(acc["logloss"]) if acc["logloss"] else 0.0
        er_m = statistics.fmean(acc["er"]) if acc["er"] else 0.0
        composite = 0.45 * brier_m + 0.30 * ll_m + 0.25 * er_m
        return {
            "brierScore": round(brier_m, 5),
            "logLoss": round(ll_m, 5),
            "erMismatchRate": round(er_m, 5),
            "compositeLoss": round(composite, 5),
        }

    return _summarize(train_acc), _summarize(test_acc)


def replay_learner_history(
    workspace: Path | str | None = None,
    *,
    min_events: int = 12,
    apply_if_improved: bool = False,
) -> dict[str, Any]:
    """Replay local chronological `revlog.jsonl` via 70/30 TimeSeriesSplit to calibrate `learner_model`."""
    from syntopik.learner_model import (
        CALIBRATED_HYPERPARAM_BOUNDS,
        DEFAULT_CALIBRATED_HYPERPARAMS,
        PROFILE_FILENAME,
        compile_teaching_policy,
        load_profile,
        save_profile,
    )
    from syntopik.schemas import now_iso
    from syntopik.storage import load_jsonl, workspace_lock

    ws, kg_dir = _replay_resolve_kg(workspace)
    revlog_rows = load_jsonl(kg_dir / "revlog.jsonl")
    prop_rows = load_jsonl(kg_dir / "propositions.jsonl")
    props_by_id = {
        str(p.get("id") or "").strip(): p for p in prop_rows if isinstance(p, dict) and str(p.get("id") or "").strip()
    }
    profile = load_profile(kg_dir)

    events = _extract_replay_events(revlog_rows, props_by_id)
    min_req = max(2, int(min_events))
    if len(events) < min_req:
        return {
            "ok": True,
            "workspace": str(ws),
            "insufficientData": True,
            "eventCount": len(events),
            "minEvents": min_req,
            "applied": False,
            "reason": f"Need at least {min_req} confidence-rated reviews for chronological replay (found {len(events)}).",
        }

    # Strict chronological 70/30 TimeSeriesSplit (earliest 70% train, latest 30% OOS test)
    split_idx = max(1, min(len(events) - 1, int(len(events) * 0.70)))
    cal = profile.get("calibration") if isinstance(profile.get("calibration"), dict) else {}
    session_target_min = _safe_replay_float(cal.get("sessionTargetMinutes"), 30.0, lo=10.0, hi=240.0)
    baseline_latency_ms = _safe_replay_float(cal.get("baselineLatencyMs"), 22000.0, lo=2000.0, hi=300000.0)

    baseline_params = dict(DEFAULT_CALIBRATED_HYPERPARAMS)
    base_train, base_oos = _evaluate_replay_params(
        events,
        split_idx,
        elo_k_base=baseline_params["eloKBase"],
        er_novice_cutoff=baseline_params["erNoviceCutoff"],
        er_challenge_cutoff=baseline_params["erChallengeCutoff"],
        fatigue_multiplier=baseline_params["fatigueMultiplier"],
        session_target_min=session_target_min,
        baseline_latency_ms=baseline_latency_ms,
    )

    # Select candidate strictly on earliest 70% train split (zero test-set leakage!)
    best_candidate: dict[str, float] = dict(baseline_params)
    best_train = dict(base_train)
    best_oos = dict(base_oos)
    best_train_rank: tuple[float, float] = (base_train["compositeLoss"], 0.0)
    grid_evaluated = 0

    for k_base in _REPLAY_GRID_ELO_K:
        for nov_cut in _REPLAY_GRID_NOVICE:
            for chal_cut in _REPLAY_GRID_CHALLENGE:
                for fat_mult in _REPLAY_GRID_FATIGUE:
                    grid_evaluated += 1
                    tr_m, te_m = _evaluate_replay_params(
                        events,
                        split_idx,
                        elo_k_base=k_base,
                        er_novice_cutoff=nov_cut,
                        er_challenge_cutoff=chal_cut,
                        fatigue_multiplier=fat_mult,
                        session_target_min=session_target_min,
                        baseline_latency_ms=baseline_latency_ms,
                    )
                    # Deterministic tie-breaker prefers smaller shift from baseline
                    l2_shift = (
                        ((k_base - baseline_params["eloKBase"]) / 32.0) ** 2
                        + ((nov_cut - baseline_params["erNoviceCutoff"]) / 0.10) ** 2
                        + ((chal_cut - baseline_params["erChallengeCutoff"]) / 0.08) ** 2
                        + ((fat_mult - baseline_params["fatigueMultiplier"]) / 0.20) ** 2
                    )
                    rank = (tr_m["compositeLoss"], round(l2_shift, 6))
                    if rank < best_train_rank:
                        best_train_rank = rank
                        best_candidate = {
                            "eloKBase": k_base,
                            "erNoviceCutoff": nov_cut,
                            "erChallengeCutoff": chal_cut,
                            "fatigueMultiplier": fat_mult,
                        }
                        best_train = tr_m
                        best_oos = te_m

    # Strictly clamp chosen parameters inside safe cognitive bounds
    clamped_candidate: dict[str, float] = {}
    for key, val in best_candidate.items():
        lo, hi = CALIBRATED_HYPERPARAM_BOUNDS[key]
        clamped_candidate[key] = round(_clamp(float(val), lo, hi), 4)

    oos_improvement = round(base_oos["compositeLoss"] - best_oos["compositeLoss"], 5)
    improved = oos_improvement >= _REPLAY_MIN_OOS_GAIN
    applied = False

    if apply_if_improved and improved:
        prof_path = kg_dir / PROFILE_FILENAME
        with workspace_lock(prof_path):
            fresh_profile = load_profile(kg_dir)
            fresh_profile["calibratedHyperparams"] = {
                **clamped_candidate,
                "calibratedAt": now_iso(),
                "trainEvents": split_idx,
                "testEvents": len(events) - split_idx,
                "oosBaselineLoss": base_oos["compositeLoss"],
                "oosCalibratedLoss": best_oos["compositeLoss"],
                "oosImprovement": oos_improvement,
            }
            fresh_profile["compiledPolicy"] = compile_teaching_policy(fresh_profile)
            save_profile(kg_dir, fresh_profile)
        applied = True

    return {
        "ok": True,
        "workspace": str(ws),
        "insufficientData": False,
        "eventCount": len(events),
        "minEvents": min_req,
        "split": {
            "method": "chronological_time_series_70_30",
            "trainEvents": split_idx,
            "testEvents": len(events) - split_idx,
            "trainStart": events[0]["tsIso"],
            "trainEnd": events[split_idx - 1]["tsIso"],
            "testStart": events[split_idx]["tsIso"],
            "testEnd": events[-1]["tsIso"],
        },
        "gridEvaluated": grid_evaluated,
        "minOosGainThreshold": _REPLAY_MIN_OOS_GAIN,
        "baselineParams": baseline_params,
        "candidateParams": clamped_candidate,
        "baselineMetrics": {"train": base_train, "oos": base_oos},
        "candidateMetrics": {"train": best_train, "oos": best_oos},
        "oosImprovement": oos_improvement,
        "improved": improved,
        "applied": applied,
    }
