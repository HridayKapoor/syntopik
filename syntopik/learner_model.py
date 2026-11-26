"""Syntopik v3.0 — 3-Cadence Adaptive Learner Psychology & TeachingPolicy Engine.

Pure Python 3.12+ standard library only (zero external imports, zero ML weights).
Implements the evidence-backed 3-Cadence profiling architecture (zero VARK):
  - Tier 1: One-time 2-min onboarding calibration with Bayesian prior decay
    `w_prior(N) = 6 / (6 + N)` and prose density strictly capped `<= 155w`.
  - Tier 2: Per-chapter 2-3 probe Rapid Dynamic Assessment (Kalyuga RDA + KG
    prerequisite bypass for `solid` nodes with `R >= 0.80` + Pelánek 1PL/Elo
    max-information binary search + 2-probe early stop).
  - Tier 3: Continuous silent O(1) behavioral adaptation from every review,
    mistake, fork, blitz, and exam event -> Pelánek dynamic-uncertainty Elo,
    normalized latency & session clock, 6-state sensor-free affect classifier,
    3-tier NEA Failure DNA, FSRS/Brier calibration, PF survival EWMA, and
    Kalyuga Expertise-Reversal `TeachingPolicy` compiler.

Persisted atomically at `knowledge-graph/learner-profile.json` (`< 8 KB` via
20-event FIFO window).
"""

from __future__ import annotations

import copy
import math
import statistics
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from syntopik.fsrs_core import card_retrievability, compute_calibration, load_fsrs_parameters
from syntopik.schemas import (
    normalize_chain,
    normalize_level_soft,
    normalize_mistake_category,
    normalize_subject,
    now_iso,
)
from syntopik.storage import load_json, load_jsonl, save_json_atomic, workspace_lock

__all__ = [
    "PROFILE_SCHEMA_VERSION",
    "PROFILE_FILENAME",
    "MAX_RECENT_EVENTS",
    "PRIOR_DECAY_N0",
    "ENTRY_RAMP_RATIOS",
    "CHALLENGE_TARGET_SUCCESS",
    "PROSE_DENSITY_WORDS",
    "DOMAIN_FLUENCY_ELO",
    "SCAFFOLDING_MODES",
    "AFFECT_STATES",
    "NEA_CATEGORY_TIER",
    "DEFAULT_CALIBRATED_HYPERPARAMS",
    "CALIBRATED_HYPERPARAM_BOUNDS",
    "resolve_calibrated_hyperparams",
    "load_profile",
    "save_profile",
    "apply_tier1_calibration",
    "select_next_placement_probe",
    "score_placement_probe",
    "classify_affective_state",
    "compile_teaching_policy",
    "record_telemetry_event",
    "apply_user_overrides",
    "format_compact_policy_lines",
]

PROFILE_SCHEMA_VERSION = 1
PROFILE_FILENAME = "learner-profile.json"
MAX_RECENT_EVENTS = 20
PRIOR_DECAY_N0 = 6.0
SESSION_GAP_MINUTES = 20.0

ENTRY_RAMP_RATIOS: dict[str, float] = {
    "intuition_first": 0.70,
    "balanced": 0.50,
    "formalism_first": 0.25,
}

CHALLENGE_TARGET_SUCCESS: dict[str, float] = {
    "guided": 0.88,
    "balanced": 0.85,
    "adversarial": 0.78,
}

CHALLENGE_INITIAL_PF_SURVIVAL: dict[str, float] = {
    "guided": 0.50,
    "balanced": 0.65,
    "adversarial": 0.78,
}

# Strictly <= 155 words (below Syntopik's hard 160w cognitive-load ceiling)
PROSE_DENSITY_WORDS: dict[str, int] = {
    "terse": 105,
    "standard": 135,
    "rich": 155,
}

DOMAIN_FLUENCY_ELO: dict[int, float] = {
    0: 1150.0,
    1: 1350.0,
    2: 1550.0,
    3: 1750.0,
}

DOMAIN_FAMILY_ALIASES: dict[str, str] = {
    "math_proof": "Maths",
    "algebra_calc": "Maths",
    "coding_algo": "Computer Science",
    "physics_chem": "Physics",
    "systems_ml": "Systems",
}

LEVEL_DIFFICULTY: dict[str, float] = {
    "basics": 1200.0,
    "main": 1500.0,
    "advanced": 1800.0,
}

KIND_BASELINE_MS: dict[str, float] = {
    "flashcard": 12000.0,
    "review": 12000.0,
    "probe": 25000.0,
    "quiz": 25000.0,
    "placement": 25000.0,
    "blitz": 30000.0,
    "decision": 30000.0,
    "fork": 40000.0,
    "arena": 60000.0,
    "practice": 90000.0,
    "derivation": 90000.0,
    "viva": 90000.0,
    "exam": 90000.0,
}

SCAFFOLDING_MODES: tuple[str, ...] = (
    "worked_example_parsons",
    "faded",
    "standard",
    "challenge_transfer",
)

AFFECT_STATES: tuple[str, ...] = (
    "flow",
    "productive_confusion",
    "overloaded_frustrated",
    "bored_underchallenged",
    "gaming_rushing",
    "fatigued",
)

AFFECT_READINESS_DELTA: dict[str, float] = {
    "overloaded_frustrated": -0.18,
    "fatigued": -0.12,
    "bored_underchallenged": 0.12,
    "gaming_rushing": 0.0,
    "productive_confusion": 0.0,
    "flow": 0.0,
}

# Canonical 13-category -> 3-tier Newman Error Analysis (NEA) map
NEA_CATEGORY_TIER: dict[str, str] = {
    "concept_gap": "comprehension_model",
    "misconception": "comprehension_model",
    "condition_missed": "comprehension_model",
    "diagram_misread": "comprehension_model",
    "edge_case_missed": "comprehension_model",
    "complexity_trap": "comprehension_model",
    "sign_error": "process_encoding",
    "unit_trap": "process_encoding",
    "algebra_slip": "process_encoding",
    "careless": "process_encoding",
    "off_by_one": "process_encoding",
    "state_mutation_bug": "process_encoding",
    "time_pressure": "executive_pacing",
}

# Safe cognitive bounds for optional longitudinal replay-calibrated hyperparameters
DEFAULT_CALIBRATED_HYPERPARAMS: dict[str, float] = {
    "eloKBase": 64.0,
    "erNoviceCutoff": 0.35,
    "erChallengeCutoff": 0.80,
    "fatigueMultiplier": 1.25,
}

CALIBRATED_HYPERPARAM_BOUNDS: dict[str, tuple[float, float]] = {
    "eloKBase": (32.0, 96.0),
    "erNoviceCutoff": (0.25, 0.45),
    "erChallengeCutoff": (0.72, 0.88),
    "fatigueMultiplier": (1.05, 1.50),
}


def resolve_calibrated_hyperparams(profile: dict[str, Any] | None = None) -> dict[str, float]:
    """Read optional `profile['calibratedHyperparams']`, strictly clamped to safe cognitive bounds."""
    raw = profile.get("calibratedHyperparams") if isinstance(profile, dict) else None
    out: dict[str, float] = {}
    for key, default_val in DEFAULT_CALIBRATED_HYPERPARAMS.items():
        lo, hi = CALIBRATED_HYPERPARAM_BOUNDS[key]
        val = raw.get(key) if isinstance(raw, dict) else None
        if isinstance(val, (int, float)) and not isinstance(val, bool) and math.isfinite(float(val)):
            out[key] = round(_clamp(float(val), lo, hi), 4)
        else:
            out[key] = default_val
    return out


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _clamp(val: float, lo: float, hi: float) -> float:
    try:
        fval = float(val)
    except (TypeError, ValueError, OverflowError):
        return float(lo)
    if math.isnan(fval):
        return float(lo)
    if math.isinf(fval):
        return float(hi) if fval > 0.0 else float(lo)
    return max(float(lo), min(float(hi), fval))


def _sigmoid(x: float) -> float:
    if x >= 0:
        z = math.exp(-min(x, 60.0))
        return 1.0 / (1.0 + z)
    z = math.exp(max(x, -60.0))
    return z / (1.0 + z)


def _elo_expected(theta: float, difficulty: float) -> float:
    exponent = _clamp((float(difficulty) - float(theta)) / 400.0, -15.0, 15.0)
    return 1.0 / (1.0 + (10.0 ** exponent))


def _elo_to_band(elo: float) -> str:
    val = float(elo)
    if val < 1250.0:
        return "novice"
    if val < 1450.0:
        return "developing"
    if val < 1700.0:
        return "intermediate"
    return "advanced"


def _parse_iso_dt(value: Any) -> datetime | None:
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
        return None


def _fmt_iso_dt(dt: datetime | None) -> str:
    ref = dt.astimezone(timezone.utc) if dt is not None else datetime.now(timezone.utc)
    return ref.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _normalize_domain(domain: str | None) -> str:
    raw = str(domain or "General").strip()
    if not raw:
        return "General"
    low = raw.lower().replace("-", "_").replace(" ", "_")
    if low in DOMAIN_FAMILY_ALIASES:
        return DOMAIN_FAMILY_ALIASES[low]
    try:
        return normalize_subject(raw)
    except ValueError:
        return "General"


def _resolve_kg_path(kg_dir: Path | str) -> Path:
    p = Path(kg_dir).expanduser().resolve()
    if (p / "knowledge-graph").is_dir() and not (p / "propositions.jsonl").is_file() and p.name != "knowledge-graph":
        return (p / "knowledge-graph").resolve()
    return p


def _profile_path(kg_dir: Path | str) -> Path:
    return _resolve_kg_path(kg_dir) / PROFILE_FILENAME


def _default_profile() -> dict[str, Any]:
    ts = now_iso()
    prof: dict[str, Any] = {
        "schemaVersion": PROFILE_SCHEMA_VERSION,
        "updatedAt": ts,
        "calibration": {
            "completed": False,
            "completedAt": None,
            "entryRamp": "balanced",
            "challengeAppetite": "balanced",
            "targetSuccessRate": 0.85,
            "sessionTargetMinutes": 30,
            "proseDensity": "standard",
            "baselineLatencyMs": 22000,
            "microTaskBias": 0.0,
            "calibrationPrior": "calibrated",
        },
        "userOverrides": {
            "scaffoldingMode": None,
            "allowProductiveFailure": None,
            "maxProseWords": None,
        },
        "globalStats": {
            "elo": 1450.0,
            "totalEvents": 0,
            "priorWeight": 1.0,
            "analogyFormalismRatio": 0.50,
            "pfAttempts": 0,
            "pfSurvivalRate": 0.65,
            "latencyEwma": 1.0,
            "pacingMultiplier": 1.0,
        },
        "domains": {},
        "affectiveState": {
            "state": "flow",
            "confidence": 0.5,
            "activeSessionMinutes": 0.0,
            "windowAccuracy": None,
            "windowMeanHints": 0.0,
            "windowMeanLatencyRatio": 1.0,
            "errorStreak": 0,
            "gamingSignals": 0,
            "lastEventAt": None,
        },
        "metacognition": {
            "windowCount": 0,
            "brierScore": None,
            "ece": None,
            "overconfidenceBias": None,
            "label": "uncalibrated",
            "recentHypercorrections": 0,
            "recentLuckyGuesses": 0,
        },
        "failureDna": {
            "windowCount": 0,
            "tierShares": {
                "comprehension_model": 0.0,
                "process_encoding": 0.0,
                "executive_pacing": 0.0,
            },
            "dominantTier": None,
            "topCategories": [],
        },
        "recentEvents": [],
        "compiledPolicy": {},
    }
    prof["compiledPolicy"] = compile_teaching_policy(prof)
    return prof


def _merge_defaults(raw: dict[str, Any] | None) -> dict[str, Any]:
    base = _default_profile()
    if not isinstance(raw, dict):
        return base
    for key, val in raw.items():
        if key in base and isinstance(base[key], dict) and isinstance(val, dict):
            merged = dict(base[key])
            merged.update(val)
            base[key] = merged
        else:
            base[key] = copy.deepcopy(val)
    base["schemaVersion"] = PROFILE_SCHEMA_VERSION
    if not isinstance(base.get("domains"), dict):
        base["domains"] = {}
    if not isinstance(base.get("recentEvents"), list):
        base["recentEvents"] = []
    base["recentEvents"] = base["recentEvents"][-MAX_RECENT_EVENTS:]
    if not isinstance(base.get("compiledPolicy"), dict) or not base["compiledPolicy"]:
        base["compiledPolicy"] = compile_teaching_policy(base)
    return base


def _ensure_domain_entry(profile: dict[str, Any], domain: str) -> dict[str, Any]:
    domains = profile.setdefault("domains", {})
    if not isinstance(domains, dict):
        domains = {}
        profile["domains"] = domains
    dom = _normalize_domain(domain)
    entry = domains.get(dom)
    if not isinstance(entry, dict):
        default_elo = float((profile.get("globalStats") or {}).get("elo") or 1450.0)
        entry = {
            "elo": round(default_elo, 1),
            "n": 0,
            "expertiseBand": _elo_to_band(default_elo),
            "lastUpdated": now_iso(),
            "chapterPlacements": {},
        }
        domains[dom] = entry
    if not isinstance(entry.get("chapterPlacements"), dict):
        entry["chapterPlacements"] = {}
    return entry


def _prior_weight(total_events: int) -> float:
    n = max(0, int(total_events))
    return round(PRIOR_DECAY_N0 / (PRIOR_DECAY_N0 + float(n)), 4)


# ---------------------------------------------------------------------------
# Public API: Persistence
# ---------------------------------------------------------------------------


def load_profile(kg_dir: Path | str) -> dict[str, Any]:
    """Load `learner-profile.json` from `kg_dir`, returning a safe default skeleton if absent."""
    path = _profile_path(kg_dir)
    raw = load_json(path, default=None)
    return _merge_defaults(raw if isinstance(raw, dict) else None)


def _compact_profile_for_disk(profile: dict[str, Any]) -> dict[str, Any]:
    """Keep `learner-profile.json` cleanly bounded (< 8 KB) via 20-event FIFO and compact records."""
    clean = _merge_defaults(profile)
    compact_events: list[dict[str, Any]] = []
    for raw_ev in list(clean.get("recentEvents") or [])[-MAX_RECENT_EVENTS:]:
        if not isinstance(raw_ev, dict):
            continue
        ev: dict[str, Any] = {
            "ts": raw_ev.get("ts"),
            "domain": raw_ev.get("domain", "General"),
            "kind": raw_ev.get("kind", "review"),
            "difficulty": raw_ev.get("difficulty", 1500.0),
            "correct": bool(raw_ev.get("correct")),
            "latencyRatio": raw_ev.get("latencyRatio", 1.0),
            "hintsUsed": int(raw_ev.get("hintsUsed") or 0),
        }
        if raw_ev.get("propId"):
            ev["propId"] = raw_ev["propId"]
        if raw_ev.get("confidence") is not None:
            ev["confidence"] = raw_ev["confidence"]
        if raw_ev.get("durationMs") is not None:
            ev["durationMs"] = raw_ev["durationMs"]
        if raw_ev.get("skipped"):
            ev["skipped"] = True
        if raw_ev.get("pfContext"):
            ev["pfContext"] = True
        if raw_ev.get("mistakeCategory"):
            ev["mistakeCategory"] = raw_ev["mistakeCategory"]
        compact_events.append(ev)
    clean["recentEvents"] = compact_events

    # Trim completed chapter placement scratch fields (keep schema summary)
    for dom_rec in (clean.get("domains") or {}).values():
        if not isinstance(dom_rec, dict):
            continue
        placements = dom_rec.get("chapterPlacements")
        if not isinstance(placements, dict):
            continue
        for slug, rec in list(placements.items()):
            if isinstance(rec, dict) and rec.get("done"):
                placements[slug] = {
                    "theta": rec.get("theta"),
                    "probes": rec.get("probes"),
                    "band": rec.get("band"),
                    "placedAt": rec.get("placedAt"),
                    "done": True,
                    **({"earlyStop": True, "earlyStopReason": rec.get("earlyStopReason")} if rec.get("earlyStop") else {}),
                }
    return clean


def save_profile(kg_dir: Path | str, profile: dict[str, Any]) -> Path:
    """Atomically persist `learner-profile.json` under `workspace_lock`, staying `< 8 KB`."""
    path = _profile_path(kg_dir)
    clean = _compact_profile_for_disk(profile)
    clean["updatedAt"] = now_iso()
    # Stay strictly under 8 KB even with many domains by switching to indent=None if pretty JSON exceeds 7.5 KB
    import json as _json
    pretty_size = len(_json.dumps(clean, indent=2, ensure_ascii=False).encode("utf-8"))
    chosen_indent = 2 if pretty_size <= 7600 else None
    with workspace_lock(path):
        return save_json_atomic(path, clean, indent=chosen_indent)


# ---------------------------------------------------------------------------
# Tier 1: One-Time 2-Minute Onboarding Calibration (Zero VARK!)
# ---------------------------------------------------------------------------


def apply_tier1_calibration(
    profile: dict[str, Any],
    *,
    entry_ramp: str = "balanced",
    challenge_appetite: str = "balanced",
    domain_fluency: dict[str, int] | None = None,
    session_minutes: int = 30,
    prose_density: str = "standard",
    micro_task_results: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Apply Tier-1 non-VARK onboarding calibration priors with Bayesian decay `6 / (6 + N)`."""
    ramp = str(entry_ramp or "balanced").strip().lower()
    if ramp not in ENTRY_RAMP_RATIOS:
        raise ValueError(f"Invalid entry_ramp {entry_ramp!r}; expected {tuple(ENTRY_RAMP_RATIOS)}")

    appetite = str(challenge_appetite or "balanced").strip().lower()
    if appetite not in CHALLENGE_TARGET_SUCCESS:
        raise ValueError(
            f"Invalid challenge_appetite {challenge_appetite!r}; expected {tuple(CHALLENGE_TARGET_SUCCESS)}"
        )

    density = str(prose_density or "standard").strip().lower()
    if density not in PROSE_DENSITY_WORDS:
        raise ValueError(f"Invalid prose_density {prose_density!r}; expected {tuple(PROSE_DENSITY_WORDS)}")

    target_min = int(_clamp(int(session_minutes or 30), 10, 90))
    ts = now_iso()

    cal = profile.setdefault("calibration", {})
    gs = profile.setdefault("globalStats", {})
    total_n = int(gs.get("totalEvents") or 0)
    w_prior = _prior_weight(total_n)

    # 2-item metacognitive calibration micro-task
    micro_biases: list[float] = []
    micro_latencies: list[float] = []
    if isinstance(micro_task_results, list):
        for item in micro_task_results:
            if not isinstance(item, dict):
                continue
            dur = item.get("duration_ms")
            if dur is None:
                dur = item.get("durationMs")
            if isinstance(dur, (int, float)) and not isinstance(dur, bool) and math.isfinite(float(dur)) and dur > 0:
                micro_latencies.append(float(dur))
            dont_know = bool(item.get("dont_know") or item.get("dontKnow"))
            if dont_know:
                micro_biases.append(0.0)
                continue
            conf_raw = item.get("confidence")
            if isinstance(conf_raw, (int, float)) and not isinstance(conf_raw, bool) and math.isfinite(float(conf_raw)):
                c = _clamp(float(conf_raw), 0.0, 1.0)
                y = 1.0 if bool(item.get("correct")) else 0.0
                micro_biases.append(c - y)

    micro_bias = round(sum(micro_biases) / len(micro_biases), 4) if micro_biases else 0.0
    if micro_bias >= 0.15:
        cal_prior = "overconfident"
    elif micro_bias <= -0.15:
        cal_prior = "underconfident"
    else:
        cal_prior = "calibrated"

    baseline_ms = (
        int(round(_clamp(statistics.median(micro_latencies), 4000.0, 60000.0)))
        if micro_latencies
        else int(cal.get("baselineLatencyMs") or 22000)
    )

    cal.update(
        {
            "completed": True,
            "completedAt": ts,
            "entryRamp": ramp,
            "challengeAppetite": appetite,
            "targetSuccessRate": CHALLENGE_TARGET_SUCCESS[appetite],
            "sessionTargetMinutes": target_min,
            "proseDensity": density,
            "maxProseWordsPrior": PROSE_DENSITY_WORDS[density],
            "baselineLatencyMs": baseline_ms,
            "microTaskBias": micro_bias,
            "calibrationPrior": cal_prior,
        }
    )

    # Blend self-reported priors with empirical history via w_prior = 6 / (6 + N)
    prior_ratio = ENTRY_RAMP_RATIOS[ramp]
    emp_ratio = float(gs.get("analogyFormalismRatio", prior_ratio))
    blended_ratio = round(w_prior * prior_ratio + (1.0 - w_prior) * emp_ratio, 4)

    prior_pf = CHALLENGE_INITIAL_PF_SURVIVAL[appetite]
    emp_pf = float(gs.get("pfSurvivalRate", prior_pf))
    blended_pf = round(w_prior * prior_pf + (1.0 - w_prior) * emp_pf, 4) if int(gs.get("pfAttempts") or 0) > 0 else prior_pf

    gs["priorWeight"] = w_prior
    gs["analogyFormalismRatio"] = _clamp(blended_ratio, 0.15, 0.85)
    gs["pfSurvivalRate"] = _clamp(blended_pf, 0.05, 0.99)

    seeded_elos: list[float] = []
    if isinstance(domain_fluency, dict):
        for raw_dom, raw_rating in domain_fluency.items():
            try:
                rating_int = int(_clamp(int(raw_rating), 0, 3))
            except (TypeError, ValueError):
                continue
            prior_elo = DOMAIN_FLUENCY_ELO[rating_int]
            dom_key = _normalize_domain(str(raw_dom))
            entry = _ensure_domain_entry(profile, dom_key)
            n_d = int(entry.get("n") or 0)
            w_d = _prior_weight(n_d)
            emp_elo = float(entry.get("elo") or prior_elo)
            new_elo = round(w_d * prior_elo + (1.0 - w_d) * emp_elo, 1) if n_d > 0 else prior_elo
            entry["elo"] = new_elo
            entry["expertiseBand"] = _elo_to_band(new_elo)
            entry["lastUpdated"] = ts
            seeded_elos.append(new_elo)

    if seeded_elos:
        mean_seeded = sum(seeded_elos) / len(seeded_elos)
        emp_global = float(gs.get("elo") or 1450.0)
        gs["elo"] = round(w_prior * mean_seeded + (1.0 - w_prior) * emp_global, 1)

    # Seed metacognition prior if no empirical reviews exist yet
    meta = profile.setdefault("metacognition", {})
    if int(meta.get("windowCount") or 0) == 0 and micro_biases:
        meta["overconfidenceBias"] = micro_bias
        meta["label"] = cal_prior

    profile["updatedAt"] = ts
    profile["compiledPolicy"] = compile_teaching_policy(profile)
    return profile


# ---------------------------------------------------------------------------
# Tier 2: Per-Chapter 2-3 Probe Adaptive Placement (Kalyuga RDA + KG Bypass)
# ---------------------------------------------------------------------------


def _candidate_id(cand: dict[str, Any], idx: int) -> str:
    return str(cand.get("id") or cand.get("propId") or f"probe-{idx + 1}").strip()


def _candidate_difficulty(cand: dict[str, Any]) -> float:
    raw_diff = cand.get("difficulty")
    if isinstance(raw_diff, (int, float)) and not isinstance(raw_diff, bool):
        return _clamp(float(raw_diff), 900.0, 2200.0)
    lvl = normalize_level_soft(cand.get("level"), default="main")
    return LEVEL_DIFFICULTY.get(lvl, 1500.0)


def _prop_retrievability(prop: dict[str, Any], now_dt: datetime) -> float:
    fsrs_obj = prop.get("fsrs")
    if isinstance(fsrs_obj, dict) and isinstance(fsrs_obj.get("stability"), (int, float)):
        try:
            return float(card_retrievability(fsrs_obj, now_dt))
        except Exception:
            pass
    explicit_r = prop.get("retrievability")
    if isinstance(explicit_r, (int, float)) and not isinstance(explicit_r, bool):
        return _clamp(float(explicit_r), 0.0, 1.0)
    status = str(prop.get("status") or "planned").strip().lower()
    bkt = prop.get("bktMastery")
    if status == "solid" and isinstance(bkt, (int, float)) and float(bkt) >= 0.85:
        return 0.85
    return 0.0


def _initial_chapter_theta(
    domain_elo: float,
    candidates: list[dict[str, Any]],
    kg_props: list[dict[str, Any]] | None,
    chains: list[dict[str, Any]] | None,
) -> float:
    if not kg_props:
        return float(domain_elo)
    props_by_id = {str(p.get("id") or ""): p for p in kg_props if isinstance(p, dict) and p.get("id")}
    cand_ids = {_candidate_id(c, i) for i, c in enumerate(candidates) if isinstance(c, dict)}

    prereq_ids: set[str] = set()
    for raw_c in chains or []:
        nc = normalize_chain(raw_c)
        if not nc:
            continue
        if nc["conclusion"] in cand_ids:
            prereq_ids.update(nc["premises"])
    if not prereq_ids:
        prereq_ids = cand_ids & set(props_by_id.keys())

    masteries: list[float] = []
    for pid in prereq_ids:
        p = props_by_id.get(pid)
        if not p:
            continue
        bkt = p.get("bktMastery")
        st = str(p.get("status") or "planned").lower()
        if isinstance(bkt, (int, float)) and not isinstance(bkt, bool):
            masteries.append(_clamp(float(bkt), 0.0, 1.0))
        elif st in ("solid", "shaky", "misconception"):
            masteries.append({"solid": 0.88, "shaky": 0.45, "misconception": 0.15}[st])

    if not masteries:
        return float(domain_elo)
    mean_m = sum(masteries) / len(masteries)
    return _clamp(0.6 * float(domain_elo) + 0.4 * (1000.0 + 1000.0 * mean_m), 900.0, 2200.0)


def select_next_placement_probe(
    profile: dict[str, Any],
    domain: str,
    chapter_slug: str,
    candidates: list[dict[str, Any]],
    kg_props: list[dict[str, Any]] | None = None,
    chains: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Select the max-information placement probe after bypassing `solid` KG nodes with `R >= 0.80`."""
    dom = _normalize_domain(domain)
    slug = str(chapter_slug or "chapter").strip() or "chapter"
    dom_entry = _ensure_domain_entry(profile, dom)
    placements = dom_entry["chapterPlacements"]
    existing = placements.get(slug) if isinstance(placements.get(slug), dict) else None

    now_dt = datetime.now(timezone.utc)
    props_by_id = {
        str(p.get("id") or ""): p for p in (kg_props or []) if isinstance(p, dict) and p.get("id")
    }

    if existing is not None and isinstance(existing.get("theta"), (int, float)):
        theta_c = float(existing["theta"])
        probes_done = int(existing.get("probes") or 0)
        asked_ids = set(str(x) for x in (existing.get("askedIds") or []))
        already_done = bool(existing.get("done"))
        early_stop = bool(existing.get("earlyStop"))
    else:
        theta_c = _initial_chapter_theta(float(dom_entry["elo"]), candidates, kg_props, chains)
        probes_done = 0
        asked_ids = set()
        already_done = False
        early_stop = False

    bypassed_ids: list[str] = []
    eligible: list[tuple[dict[str, Any], str, float]] = []
    for idx, cand in enumerate(candidates or []):
        if not isinstance(cand, dict):
            continue
        cid = _candidate_id(cand, idx)
        if cid in asked_ids:
            continue
        prop = props_by_id.get(cid)
        if prop is not None:
            status = str(prop.get("status") or "").strip().lower()
            retr = _prop_retrievability(prop, now_dt)
            if status == "solid" and retr >= 0.80:
                bypassed_ids.append(cid)
                continue
        eligible.append((cand, cid, _candidate_difficulty(cand)))

    if already_done or probes_done >= 3 or not eligible:
        band = _elo_to_band(theta_c)
        return {
            "done": True,
            "earlyStop": early_stop,
            "reason": "early_stop" if early_stop else ("max_probes" if probes_done >= 3 else "all_bypassed_or_asked"),
            "domain": dom,
            "chapterSlug": slug,
            "probe": None,
            "probeIndex": probes_done,
            "currentTheta": round(theta_c, 1),
            "expertiseBand": band,
            "bypassedIds": bypassed_ids,
            "remainingCount": len(eligible),
        }

    # Maximize 1PL Fisher information I(theta) = P * (1 - P) <=> minimize |b - theta|
    best_item: tuple[dict[str, Any], str, float] | None = None
    best_key: tuple[float, float, str] | None = None
    for cand, cid, diff in eligible:
        p_hat = _elo_expected(theta_c, diff)
        fisher = p_hat * (1.0 - p_hat)
        key = (-fisher, abs(diff - theta_c), cid)
        if best_key is None or key < best_key:
            best_key = key
            best_item = (cand, cid, diff)

    assert best_item is not None
    chosen_cand, chosen_id, chosen_diff = best_item
    p_exp = _elo_expected(theta_c, chosen_diff)

    # Record initial placement scaffold & mark chosen id so repeated calls don't re-ask it
    if existing is None:
        placements[slug] = {
            "theta": round(theta_c, 1),
            "probes": 0,
            "band": _elo_to_band(theta_c),
            "placedAt": None,
            "done": False,
            "earlyStop": False,
            "askedIds": [chosen_id],
            "history": [],
        }
    else:
        asked_list = list(existing.get("askedIds") or [])
        if chosen_id not in asked_list:
            asked_list.append(chosen_id)
        existing["askedIds"] = asked_list[-6:]

    probe_payload = dict(chosen_cand)
    probe_payload.setdefault("id", chosen_id)
    probe_payload["difficulty"] = round(chosen_diff, 1)

    return {
        "done": False,
        "earlyStop": False,
        "domain": dom,
        "chapterSlug": slug,
        "probe": probe_payload,
        "probeIndex": probes_done + 1,
        "expectedSuccess": round(p_exp, 4),
        "fisherInfo": round(p_exp * (1.0 - p_exp), 4),
        "currentTheta": round(theta_c, 1),
        "expertiseBand": _elo_to_band(theta_c),
        "bypassedIds": bypassed_ids,
        "remainingCount": len(eligible),
    }


def score_placement_probe(
    profile: dict[str, Any],
    domain: str,
    chapter_slug: str,
    *,
    difficulty: float = 1500.0,
    correct: bool = True,
    confidence: float = 0.5,
    dont_know: bool = False,
    duration_ms: int | None = None,
    probe_index: int = 1,
) -> dict[str, Any]:
    """Update per-chapter placement theta via Pelánek fast 1PL/Elo with 2-probe early stop."""
    dom = _normalize_domain(domain)
    slug = str(chapter_slug or "chapter").strip() or "chapter"
    dom_entry = _ensure_domain_entry(profile, dom)
    placements = dom_entry["chapterPlacements"]

    rec = placements.get(slug)
    if not isinstance(rec, dict):
        rec = {
            "theta": float(dom_entry["elo"]),
            "probes": 0,
            "band": dom_entry["expertiseBand"],
            "placedAt": None,
            "done": False,
            "earlyStop": False,
            "askedIds": [],
            "history": [],
        }
        placements[slug] = rec

    old_theta = float(rec.get("theta") or dom_entry["elo"])
    b_k = (
        _clamp(float(difficulty), 900.0, 2200.0)
        if isinstance(difficulty, (int, float)) and not isinstance(difficulty, bool) and math.isfinite(float(difficulty))
        else 1500.0
    )
    c = (
        _clamp(float(confidence), 0.0, 1.0)
        if isinstance(confidence, (int, float)) and not isinstance(confidence, bool) and math.isfinite(float(confidence))
        else 0.5
    )
    k_idx = max(1, int(probe_index or (int(rec.get("probes") or 0) + 1)))

    honest_gap = bool(dont_know)
    lucky_guess = False
    hypercorrection = False
    misconception_tag = False
    step_mult = 1.0

    if honest_gap:
        s_k = 0.0
    elif correct and c <= 0.35:
        s_k = 0.55
        lucky_guess = True
    elif (not correct) and c >= 0.75:
        s_k = 0.0
        hypercorrection = True
        misconception_tag = True
        step_mult = 1.35
    else:
        s_k = 1.0 if correct else 0.0

    p_k = _elo_expected(old_theta, b_k)
    k_step = 240.0 / (1.0 + 0.35 * float(k_idx))
    delta = k_step * (s_k - p_k) * step_mult
    new_theta = round(_clamp(old_theta + delta, 900.0, 2200.0), 1)
    band = _elo_to_band(new_theta)

    dur_probe_ms: int | None = None
    if isinstance(duration_ms, (int, float)) and not isinstance(duration_ms, bool):
        df = float(duration_ms)
        if math.isfinite(df) and df > 0:
            dur_probe_ms = int(round(min(df, 86_400_000.0)))

    hist = list(rec.get("history") or [])
    hist.append(
        {
            "probeIndex": k_idx,
            "difficulty": round(b_k, 1),
            "correct": bool(correct) and not honest_gap,
            "confidence": round(c, 3),
            "dontKnow": honest_gap,
            "durationMs": dur_probe_ms,
        }
    )
    hist = hist[-5:]
    probes_count = max(int(rec.get("probes") or 0) + 1, len(hist))

    # 2-probe early stop rule
    early_stop = False
    early_reason: str | None = None
    if len(hist) >= 2:
        h2 = hist[-2:]
        if all((not h["correct"]) and float(h["difficulty"]) <= 1250.0 for h in h2):
            early_stop = True
            early_reason = "both_basics_failed"
        elif all(
            h["correct"]
            and float(h["difficulty"]) >= 1700.0
            and float(h["confidence"]) >= 0.80
            and (h["durationMs"] is None or int(h["durationMs"]) <= 90000)
            for h in h2
        ):
            early_stop = True
            early_reason = "both_advanced_aced"

    done = bool(early_stop or probes_count >= 3)
    ts = now_iso()

    rec.update(
        {
            "theta": new_theta,
            "probes": probes_count,
            "band": band,
            "placedAt": ts,
            "done": done,
            "earlyStop": early_stop,
            "earlyStopReason": early_reason,
            "history": hist,
        }
    )

    # Gently sync domain Elo when placement concludes (stronger if domain has low n)
    if done:
        n_d = int(dom_entry.get("n") or 0)
        blend = 0.65 if n_d == 0 else _clamp(3.0 / (3.0 + n_d), 0.15, 0.50)
        blended_dom = round(_clamp((1.0 - blend) * float(dom_entry["elo"]) + blend * new_theta, 900.0, 2200.0), 1)
        dom_entry["elo"] = blended_dom
        dom_entry["expertiseBand"] = _elo_to_band(blended_dom)
        dom_entry["lastUpdated"] = ts

    profile["updatedAt"] = ts
    profile["compiledPolicy"] = compile_teaching_policy(profile, domain=dom)

    return {
        "domain": dom,
        "chapterSlug": slug,
        "probeIndex": k_idx,
        "oldTheta": round(old_theta, 1),
        "newTheta": new_theta,
        "delta": round(new_theta - old_theta, 2),
        "expectedP": round(p_k, 4),
        "effectiveScore": round(s_k, 3),
        "expertiseBand": band,
        "done": done,
        "earlyStop": early_stop,
        "earlyStopReason": early_reason,
        "honestGap": honest_gap,
        "luckyGuess": lucky_guess,
        "hypercorrection": hypercorrection,
        "misconceptionTag": misconception_tag,
        "placement": copy.deepcopy(rec),
    }


# ---------------------------------------------------------------------------
# Tier 3: Sensor-Free 6-State Affective & Cognitive-Load Classifier
# ---------------------------------------------------------------------------


def _compute_session_events(
    recent_events: list[dict[str, Any]],
    now: datetime | str | None = None,
) -> tuple[list[dict[str, Any]], float, str | None]:
    """Return `(contiguous_session_events, active_session_minutes, last_event_iso)`."""
    if not recent_events:
        return [], 0.0, None

    parsed: list[tuple[dict[str, Any], datetime | None]] = [
        (ev, _parse_iso_dt(ev.get("ts"))) for ev in recent_events if isinstance(ev, dict)
    ]
    if not parsed:
        return [], 0.0, None

    last_ev, last_dt = parsed[-1]
    last_iso = str(last_ev.get("ts") or "") or (_fmt_iso_dt(last_dt) if last_dt else None)

    if last_dt is None:
        total_ms = sum(float(ev.get("durationMs") or 20000.0) for ev, _ in parsed)
        return [ev for ev, _ in parsed], round(total_ms / 60000.0, 2), last_iso

    if now is not None:
        ref_now = _parse_iso_dt(now)
        if ref_now is None:
            raise ValueError(f"Malformed ISO timestamp for 'now': {now!r}")
    else:
        ref_now = last_dt
    if now is not None and (ref_now - last_dt).total_seconds() > SESSION_GAP_MINUTES * 60.0:
        return [], 0.0, last_iso

    session_rev: list[tuple[dict[str, Any], datetime]] = [(last_ev, last_dt)]
    prev_dt = last_dt
    for ev, dt in reversed(parsed[:-1]):
        if dt is None:
            continue
        gap_min = (prev_dt - dt).total_seconds() / 60.0
        if gap_min < -1.0 or gap_min > SESSION_GAP_MINUTES:
            break
        session_rev.append((ev, dt))
        prev_dt = dt

    session_list = list(reversed(session_rev))
    start_dt = session_list[0][1]
    end_dt = session_list[-1][1]
    span_min = max(0.0, (end_dt - start_dt).total_seconds() / 60.0)
    # Add last event's duration so single/short sessions reflect real effort
    last_dur_min = float(session_list[-1][0].get("durationMs") or 0.0) / 60000.0
    active_min = max(span_min + min(last_dur_min, 5.0), min(last_dur_min, 5.0) if len(session_list) == 1 else span_min)
    return [ev for ev, _ in session_list], round(active_min, 2), last_iso


def _event_clean_hit(ev: dict[str, Any]) -> float:
    if bool(ev.get("skipped")) or not bool(ev.get("correct")):
        return 0.0
    hints = int(ev.get("hintsUsed") or 0)
    return 1.0 if hints <= 2 else 0.0


def classify_affective_state(
    recent_events: list[dict[str, Any]],
    session_target_min: float = 30.0,
    now: datetime | str | None = None,
    *,
    fatigue_multiplier: float = 1.25,
) -> dict[str, Any]:
    """Classify 6 sensor-free affective states over the sliding window W=6 of `recentEvents`."""
    if now is not None and _parse_iso_dt(now) is None:
        raise ValueError(f"Malformed ISO timestamp for 'now': {now!r}")
    events = [e for e in (recent_events or []) if isinstance(e, dict)]
    if not events:
        return {
            "state": "flow",
            "confidence": 0.5,
            "activeSessionMinutes": 0.0,
            "windowAccuracy": None,
            "windowMeanHints": 0.0,
            "windowMeanLatencyRatio": 1.0,
            "errorStreak": 0,
            "gamingSignals": 0,
            "lastEventAt": None,
        }

    session_events, active_min, last_iso = _compute_session_events(events, now=now)
    window = events[-6:]
    w_len = len(window)

    hits = [_event_clean_hit(e) for e in window]
    hints_list = [max(0, int(e.get("hintsUsed") or 0)) for e in window]
    lat_list = [_clamp(float(e.get("latencyRatio") or 1.0), 0.15, 5.0) for e in window]

    acc = sum(hits) / w_len
    mean_hints = sum(hints_list) / w_len
    mean_lat = sum(lat_list) / w_len

    # Consecutive misses or surrenders at tail of window
    error_streak = 0
    for ev in reversed(window):
        if bool(ev.get("skipped")) or not bool(ev.get("correct")):
            error_streak += 1
        else:
            break

    # Gaming / rushing signals
    gaming_signals = 0
    for ev in window:
        lat = float(ev.get("latencyRatio") or 1.0)
        h = int(ev.get("hintsUsed") or 0)
        clean_solve = bool(ev.get("correct")) and not bool(ev.get("skipped")) and h == 0
        if (lat < 0.35 and not clean_solve) or (h >= 3 and lat < 0.50):
            gaming_signals += 1

    # Fatigue degradation check across active session (first 3 vs last 3)
    fatigue_degraded = False
    target_min = max(10.0, float(session_target_min or 30.0))
    fat_lo, fat_hi = CALIBRATED_HYPERPARAM_BOUNDS["fatigueMultiplier"]
    fat_mult = _clamp(fatigue_multiplier, fat_lo, fat_hi)
    if active_min > fat_mult * target_min and len(session_events) >= 4:
        first3 = session_events[:3]
        last3 = session_events[-3:]
        f_acc = sum(_event_clean_hit(e) for e in first3) / len(first3)
        l_acc = sum(_event_clean_hit(e) for e in last3) / len(last3)
        f_lat = sum(float(e.get("latencyRatio") or 1.0) for e in first3) / len(first3)
        l_lat = sum(float(e.get("latencyRatio") or 1.0) for e in last3) / len(last3)
        if (f_acc - l_acc) >= 0.25 or (f_lat > 0 and l_lat >= 1.5 * f_lat):
            fatigue_degraded = True

    # Strict priority evaluation
    state = "flow"
    conf = round(_clamp(0.55 + 0.06 * w_len, 0.55, 0.90), 2)

    if gaming_signals >= 2:
        state = "gaming_rushing"
        conf = round(_clamp(0.70 + 0.08 * gaming_signals, 0.70, 0.95), 2)
    elif fatigue_degraded:
        state = "fatigued"
        conf = round(_clamp(0.72 + 0.05 * min(4.0, (active_min / target_min) - fat_mult), 0.72, 0.94), 2)
    elif error_streak >= 2 and (mean_hints >= 2.5 or mean_lat >= 1.8 or acc < 0.45):
        state = "overloaded_frustrated"
        conf = round(_clamp(0.72 + 0.06 * error_streak, 0.72, 0.95), 2)
    elif w_len >= 4 and acc >= 0.94 and mean_hints <= 0.3 and mean_lat <= 0.65:
        state = "bored_underchallenged"
        conf = round(_clamp(0.74 + 0.04 * w_len, 0.74, 0.94), 2)
    elif w_len >= 2 and 0.50 <= acc < 0.78 and 1.1 <= mean_lat <= 2.2 and mean_hints <= 2.0:
        state = "productive_confusion"
        conf = round(_clamp(0.65 + 0.04 * w_len, 0.65, 0.88), 2)
    else:
        state = "flow"
        conf = round(_clamp(0.60 + 0.05 * w_len, 0.60, 0.90), 2)

    return {
        "state": state,
        "confidence": conf,
        "activeSessionMinutes": active_min,
        "windowAccuracy": round(acc, 3),
        "windowMeanHints": round(mean_hints, 2),
        "windowMeanLatencyRatio": round(mean_lat, 2),
        "errorStreak": error_streak,
        "gamingSignals": gaming_signals,
        "lastEventAt": last_iso,
    }


# ---------------------------------------------------------------------------
# Tier 3: Kalyuga Expertise-Reversal TeachingPolicy Compiler
# ---------------------------------------------------------------------------


def _pick_active_domain(profile: dict[str, Any], domain: str | None) -> str:
    if domain and str(domain).strip():
        return _normalize_domain(domain)
    domains = profile.get("domains")
    if isinstance(domains, dict) and domains:
        best = max(
            domains.items(),
            key=lambda kv: (
                str((kv[1] or {}).get("lastUpdated") or ""),
                int((kv[1] or {}).get("n") or 0),
                kv[0],
            ),
        )
        return best[0]
    prev_dom = (profile.get("compiledPolicy") or {}).get("domain")
    if prev_dom:
        return _normalize_domain(str(prev_dom))
    return "General"


def _step_down_scaffolding(mode: str) -> str:
    idx = SCAFFOLDING_MODES.index(mode) if mode in SCAFFOLDING_MODES else 1
    return SCAFFOLDING_MODES[max(0, idx - 1)]


def compile_teaching_policy(
    profile: dict[str, Any],
    *,
    domain: str | None = None,
    node_bkt: float | None = None,
    energy: str = "normal",
) -> dict[str, Any]:
    """Compile Kalyuga Expertise-Reversal TeachingPolicy honoring `userOverrides`."""
    dom = _pick_active_domain(profile, domain)
    cal = profile.get("calibration") or {}
    gs = profile.get("globalStats") or {}
    domains = profile.get("domains") or {}
    dom_rec = domains.get(dom) if isinstance(domains.get(dom), dict) else None
    theta_d = float((dom_rec or {}).get("elo") or gs.get("elo") or 1450.0)

    affect_obj = profile.get("affectiveState") or {}
    affect_state = str(affect_obj.get("state") or "flow")
    if affect_state not in AFFECT_READINESS_DELTA:
        affect_state = "flow"

    elo_sig = _sigmoid((theta_d - 1450.0) / 180.0)
    bkt_val = _clamp(float(node_bkt), 0.0, 1.0) if isinstance(node_bkt, (int, float)) else elo_sig
    delta_affect = AFFECT_READINESS_DELTA[affect_state]

    raw_eff = 0.65 * bkt_val + 0.35 * elo_sig + delta_affect
    e_eff = _clamp(raw_eff, 0.0, 1.0)

    hyper = resolve_calibrated_hyperparams(profile)
    novice_cutoff = hyper["erNoviceCutoff"]
    challenge_cutoff = hyper["erChallengeCutoff"]

    if e_eff < novice_cutoff:
        mode = "worked_example_parsons"
    elif e_eff < 0.62:
        mode = "faded"
    elif e_eff < challenge_cutoff:
        mode = "standard"
    else:
        mode = "challenge_transfer"

    density = str(cal.get("proseDensity") or "standard").lower()
    max_words = int(PROSE_DENSITY_WORDS.get(density, 135))
    target_p = float(cal.get("targetSuccessRate") or 0.85)
    pf_survival = float(gs.get("pfSurvivalRate") or 0.65)
    r_af = round(_clamp(float(gs.get("analogyFormalismRatio") or 0.50), 0.15, 0.85), 3)
    pacing = round(_clamp(float(gs.get("pacingMultiplier") or 1.0), 0.70, 1.40), 2)

    allow_pf = bool(theta_d >= 1350.0 and affect_state in ("flow", "bored_underchallenged") and pf_survival >= 0.45)
    require_pred = False
    energy_override: str | None = None
    intervention = "none"
    guardrails: list[str] = []

    energy_norm = str(energy or "normal").strip().lower()
    if energy_norm == "low":
        max_words = min(max_words, 115)
        allow_pf = False

    # Affect-driven interventions
    if affect_state == "gaming_rushing":
        require_pred = True
        intervention = "require_reasoning_trace"
        guardrails.append("Require 1-line reasoning trace before grading; block hint-skipping")
    elif affect_state == "fatigued":
        energy_override = "low"
        allow_pf = False
        max_words = min(max_words, 110)
        pacing = min(pacing, 0.80)
        intervention = "calm_consolidation_wrapup"
        guardrails.append("Fatigue cliff: switch to low-energy wrap-up & guilt-free stop")
    elif affect_state == "overloaded_frustrated":
        mode = _step_down_scaffolding(mode)
        allow_pf = False
        max_words = min(max_words, 110)
        target_p = max(target_p, 0.90)
        pacing = min(pacing, 0.80)
        intervention = "step_down_bridge_worked_example"
        guardrails.append("Overload guard: step down scaffolding, <=110w prose, 90% bridge")
    elif affect_state == "bored_underchallenged":
        if mode in ("worked_example_parsons", "faded"):
            mode = "standard"
        if e_eff >= 0.72:
            mode = "challenge_transfer"
        pacing = max(pacing, 1.25)
        intervention = "expertise_reversal_strip_examples"
        guardrails.append("Expertise Reversal: zero worked examples; escalate to transfer/bug-hunt")
    elif affect_state == "productive_confusion":
        intervention = "hold_steady_r0_r1_socratic"
        guardrails.append("Productive confusion: hold steady with 1 Socratic R0-R1 nudge")

    allow_worked = mode == "worked_example_parsons" and affect_state != "bored_underchallenged"

    # Metacognition calibration guardrails
    meta = profile.get("metacognition") or {}
    bias = meta.get("overconfidenceBias")
    hyper = int(meta.get("recentHypercorrections") or 0)
    if (isinstance(bias, (int, float)) and float(bias) >= 0.15) or hyper >= 2:
        require_pred = True
        guardrails.append("Overconfidence guard: force prediction + confidence before reveal")
    elif isinstance(bias, (int, float)) and float(bias) <= -0.15:
        guardrails.append("Underconfidence calibration: surface objective recall evidence calmly")

    # Failure DNA (NEA 3-tier) guardrails
    dna = profile.get("failureDna") or {}
    shares = dna.get("tierShares") or {}
    if int(dna.get("windowCount") or 0) > 0:
        pe = float(shares.get("process_encoding") or 0.0)
        cm = float(shares.get("comprehension_model") or 0.0)
        ep = float(shares.get("executive_pacing") or 0.0)
        if pe >= 0.50:
            guardrails.append(f"Pre-submit sign/limit/unit check (process_encoding {int(round(pe * 100))}%)")
        elif cm >= 0.50:
            guardrails.append(f"Interleaved [A,B,A'] discrimination (comprehension_model {int(round(cm * 100))}%)")
        if ep >= 0.40:
            guardrails.append(f"2-pass <=180s time cap (executive_pacing {int(round(ep * 100))}%)")

    # Honor explicit userOverrides (agency & transparency)
    overrides = profile.get("userOverrides") or {}
    if isinstance(overrides, dict):
        ov_mode = overrides.get("scaffoldingMode")
        if isinstance(ov_mode, str) and ov_mode in SCAFFOLDING_MODES:
            mode = ov_mode
            allow_worked = mode == "worked_example_parsons"
        ov_pf = overrides.get("allowProductiveFailure")
        if isinstance(ov_pf, bool):
            allow_pf = ov_pf
        ov_words = overrides.get("maxProseWords")
        if isinstance(ov_words, (int, float)) and not isinstance(ov_words, bool):
            max_words = int(_clamp(int(ov_words), 60, 160))

    return {
        "domain": dom,
        "domainElo": round(theta_d, 1),
        "expertiseBand": _elo_to_band(theta_d),
        "scaffoldingMode": mode,
        "effectiveReadiness": round(e_eff, 3),
        "allowWorkedExample": bool(allow_worked),
        "allowProductiveFailure": bool(allow_pf),
        "maxProseWords": min(160, int(max_words)),
        "analogyFormalismRatio": r_af,
        "targetSuccessRate": round(_clamp(target_p, 0.70, 0.95), 2),
        "pacingMultiplier": pacing,
        "requirePredictionFirst": bool(require_pred),
        "energyOverride": energy_override,
        "affectState": affect_state,
        "affectIntervention": intervention,
        "activeGuardrails": guardrails[:4],
    }


# ---------------------------------------------------------------------------
# Tier 3: Continuous Silent Telemetry Update
# ---------------------------------------------------------------------------


def _compute_failure_dna(kg_path: Path, extra_category: str | None = None) -> dict[str, Any]:
    rows = load_jsonl(kg_path / "mistakes.jsonl")
    recent_cats: list[str] = []
    for row in rows[-20:]:
        raw_cat = row.get("category")
        if not raw_cat:
            continue
        try:
            recent_cats.append(normalize_mistake_category(str(raw_cat)))
        except ValueError:
            continue

    if extra_category:
        try:
            norm_extra = normalize_mistake_category(extra_category)
            # Avoid double-counting if caller already appended to mistakes.jsonl right before calling us
            if not recent_cats or recent_cats[-1] != norm_extra or len(rows) == 0:
                recent_cats.append(norm_extra)
                recent_cats = recent_cats[-20:]
        except ValueError:
            pass

    total = len(recent_cats)
    if total == 0:
        return {
            "windowCount": 0,
            "tierShares": {
                "comprehension_model": 0.0,
                "process_encoding": 0.0,
                "executive_pacing": 0.0,
            },
            "dominantTier": None,
            "topCategories": [],
        }

    tier_counts: Counter[str] = Counter()
    cat_counts: Counter[str] = Counter(recent_cats)
    for cat in recent_cats:
        tier = NEA_CATEGORY_TIER.get(cat, "comprehension_model")
        tier_counts[tier] += 1

    shares = {
        "comprehension_model": round(tier_counts["comprehension_model"] / total, 3),
        "process_encoding": round(tier_counts["process_encoding"] / total, 3),
        "executive_pacing": round(tier_counts["executive_pacing"] / total, 3),
    }
    dominant = max(shares.items(), key=lambda kv: (kv[1], kv[0]))[0]
    top_cats = [c for c, _ in cat_counts.most_common(3)]
    return {
        "windowCount": total,
        "tierShares": shares,
        "dominantTier": dominant,
        "topCategories": top_cats,
    }


def _compute_metacognition(kg_path: Path, recent_events: list[dict[str, Any]], cal_prior: dict[str, Any]) -> dict[str, Any]:
    revlog = load_jsonl(kg_path / "revlog.jsonl")
    rev_window = revlog[-30:]

    # Also build synthetic calibration rows from recentEvents when revlog lacks confidence entries
    rev_scored = [
        r for r in rev_window if isinstance(r.get("learnerConfidence"), (int, float)) and not isinstance(r.get("learnerConfidence"), bool)
    ]
    if rev_scored:
        cal = compute_calibration(rev_window)
    else:
        ev_rows = [
            {
                "learnerConfidence": e["confidence"],
                "correct": bool(e.get("correct")) and not bool(e.get("skipped")),
            }
            for e in recent_events
            if isinstance(e.get("confidence"), (int, float)) and not isinstance(e.get("confidence"), bool)
        ]
        cal = compute_calibration(ev_rows)

    n_count = int(cal.get("count") or 0)
    bias = cal.get("overconfidenceBias")
    if n_count == 0:
        prior_bias = cal_prior.get("microTaskBias")
        prior_label = str(cal_prior.get("calibrationPrior") or "uncalibrated")
        return {
            "windowCount": 0,
            "brierScore": None,
            "ece": None,
            "overconfidenceBias": prior_bias if cal_prior.get("completed") else None,
            "label": prior_label if cal_prior.get("completed") else "uncalibrated",
            "recentHypercorrections": 0,
            "recentLuckyGuesses": 0,
        }

    if isinstance(bias, (int, float)):
        if float(bias) >= 0.15:
            label = "overconfident"
        elif float(bias) <= -0.15:
            label = "underconfident"
        else:
            label = "calibrated"
    else:
        label = "calibrated"

    return {
        "windowCount": n_count,
        "brierScore": cal.get("brierScore"),
        "ece": cal.get("ece"),
        "overconfidenceBias": bias,
        "label": label,
        "recentHypercorrections": int(cal.get("hypercorrectionCount") or 0),
        "recentLuckyGuesses": int(cal.get("luckyGuessCount") or 0),
    }


def _parse_pf_outcome(pf_outcome: Any) -> float | None:
    if pf_outcome is None:
        return None
    if isinstance(pf_outcome, bool):
        return 1.0 if pf_outcome else 0.0
    if isinstance(pf_outcome, (int, float)):
        fval = float(pf_outcome)
        return _clamp(fval, 0.0, 1.0) if math.isfinite(fval) else None
    s = str(pf_outcome).strip().lower()
    if s in ("survived", "pass", "passed", "solved", "good", "easy", "true", "1"):
        return 1.0
    if s in ("partial", "hard", "recovered"):
        return 0.5
    if s in ("collapsed", "fail", "failed", "again", "surrendered", "false", "0"):
        return 0.0
    return None


def record_telemetry_event(
    kg_dir: Path | str,
    *,
    domain: str = "General",
    prop_id: str | None = None,
    kind: str = "review",
    correct: bool = True,
    difficulty: float = 1500.0,
    confidence: float | None = None,
    duration_ms: int | None = None,
    hints_used: int = 0,
    mistake_category: str | None = None,
    pf_outcome: Any = None,
    skipped: bool = False,
    now: datetime | str | None = None,
) -> dict[str, Any]:
    """Record a silent Tier-3 telemetry event, update Elo/affect/DNA/policy, and save atomically."""
    kg_path = _resolve_kg_path(kg_dir)
    path = kg_path / PROFILE_FILENAME
    if now is not None:
        ref_dt = _parse_iso_dt(now)
        if ref_dt is None:
            raise ValueError(f"Malformed ISO timestamp for 'now': {now!r}")
    else:
        ref_dt = datetime.now(timezone.utc)
    ts = _fmt_iso_dt(ref_dt)

    dom = _normalize_domain(domain)
    kind_norm = str(kind or "review").strip().lower() or "review"
    if isinstance(difficulty, (int, float)) and not isinstance(difficulty, bool) and math.isfinite(float(difficulty)):
        b = _clamp(float(difficulty), 900.0, 2200.0)
    else:
        b = 1500.0
    h = max(0, min(5, int(hints_used or 0)))
    conf_val: float | None = None
    if isinstance(confidence, (int, float)) and not isinstance(confidence, bool):
        cf = float(confidence)
        if math.isfinite(cf):
            conf_val = round(_clamp(cf, 0.0, 1.0), 4)

    norm_cat: str | None = None
    if mistake_category:
        try:
            norm_cat = normalize_mistake_category(str(mistake_category))
        except ValueError:
            norm_cat = None

    with workspace_lock(path):
        profile = load_profile(kg_path)
        cal = profile["calibration"]
        gs = profile["globalStats"]
        dom_entry = _ensure_domain_entry(profile, dom)

        # 1. Normalized latency ratio L_t
        base_kind_ms = KIND_BASELINE_MS.get(kind_norm, 25000.0)
        diff_scale = _clamp(1.0 + 0.15 * ((b - 1500.0) / 300.0), 0.60, 1.60)
        learner_scale = _clamp(float(cal.get("baselineLatencyMs") or 22000.0) / 22000.0, 0.60, 1.80)
        expected_ms = base_kind_ms * diff_scale * learner_scale

        dur_int: int | None = None
        if isinstance(duration_ms, (int, float)) and not isinstance(duration_ms, bool):
            df = float(duration_ms)
            if math.isfinite(df) and df > 0:
                dur_int = int(round(min(df, 86_400_000.0)))
                l_t = _clamp(dur_int / expected_ms, 0.15, 5.0)
                gs["latencyEwma"] = round(0.75 * float(gs.get("latencyEwma") or 1.0) + 0.25 * l_t, 3)
            else:
                l_t = 1.0
        else:
            l_t = 1.0

        # 2. Hint & lucky-guess discounted effective score -> Pelánek dynamic-uncertainty Elo
        is_hit = bool(correct) and not bool(skipped)
        is_lucky = is_hit and (conf_val is not None) and (conf_val <= 0.35)
        if not is_hit:
            s_eff = 0.0
        else:
            hint_factor = max(0.0, 1.0 - 0.14 * float(h))
            lucky_factor = 0.65 if is_lucky else 1.0
            s_eff = round(hint_factor * lucky_factor, 4)

        hyper = resolve_calibrated_hyperparams(profile)
        elo_k_base = hyper["eloKBase"]

        n_d = int(dom_entry.get("n") or 0)
        theta_d = float(dom_entry.get("elo") or 1450.0)
        k_d = max(16.0, elo_k_base / (1.0 + 0.05 * float(n_d)))
        p_d = _elo_expected(theta_d, b)
        new_theta_d = round(_clamp(theta_d + k_d * (s_eff - p_d), 900.0, 2200.0), 1)
        dom_entry["elo"] = new_theta_d
        dom_entry["n"] = n_d + 1
        dom_entry["expertiseBand"] = _elo_to_band(new_theta_d)
        dom_entry["lastUpdated"] = ts

        n_global = int(gs.get("totalEvents") or 0)
        theta_g = float(gs.get("elo") or 1450.0)
        k_g = max(16.0, elo_k_base / (1.0 + 0.05 * float(n_global)))
        p_g = _elo_expected(theta_g, b)
        gs["elo"] = round(_clamp(theta_g + k_g * (s_eff - p_g), 900.0, 2200.0), 1)
        gs["totalEvents"] = n_global + 1
        gs["priorWeight"] = _prior_weight(n_global + 1)

        # 3. Productive Failure survival EWMA (alpha = 0.3)
        pf_val = _parse_pf_outcome(pf_outcome)
        if pf_val is not None:
            gs["pfAttempts"] = int(gs.get("pfAttempts") or 0) + 1
            old_pf = float(gs.get("pfSurvivalRate") or 0.65)
            gs["pfSurvivalRate"] = round(_clamp(0.7 * old_pf + 0.3 * pf_val, 0.05, 0.99), 4)

        # 4. Analogy/formalism ratio drift
        r_af = float(gs.get("analogyFormalismRatio") or 0.50)
        if is_hit and h == 0 and l_t <= 1.15:
            r_af -= 0.05
        elif (not is_hit and norm_cat in ("concept_gap", "misconception")) or h >= 2:
            r_af += 0.05
        gs["analogyFormalismRatio"] = round(_clamp(r_af, 0.15, 0.85), 3)

        # 5. Append compact FIFO event (cap 20 so file stays < 8 KB)
        ev: dict[str, Any] = {
            "ts": ts,
            "domain": dom,
            "propId": str(prop_id).strip() if prop_id else None,
            "kind": kind_norm,
            "difficulty": round(b, 1),
            "correct": bool(is_hit),
            "skipped": bool(skipped),
            "confidence": conf_val,
            "durationMs": dur_int,
            "latencyRatio": round(l_t, 3),
            "hintsUsed": h,
            "pfContext": pf_val is not None,
        }
        if norm_cat:
            ev["mistakeCategory"] = norm_cat

        recent = list(profile.get("recentEvents") or [])
        recent.append(ev)
        recent = recent[-MAX_RECENT_EVENTS:]
        profile["recentEvents"] = recent

        # 6. Affect, Metacognition (FSRS Brier/ECE), Failure DNA (NEA 3-tier), Pacing & Compiled Policy
        affect = classify_affective_state(
            recent,
            session_target_min=float(cal.get("sessionTargetMinutes") or 30.0),
            now=ref_dt,
            fatigue_multiplier=hyper["fatigueMultiplier"],
        )
        profile["affectiveState"] = affect

        if affect["state"] == "bored_underchallenged":
            gs["pacingMultiplier"] = 1.25
        elif affect["state"] in ("overloaded_frustrated", "fatigued"):
            gs["pacingMultiplier"] = 0.80
        else:
            acc_w = affect.get("windowAccuracy")
            if isinstance(acc_w, (int, float)):
                gs["pacingMultiplier"] = round(_clamp(0.85 + 0.30 * float(acc_w), 0.85, 1.15), 2)

        profile["metacognition"] = _compute_metacognition(kg_path, recent, cal)
        profile["failureDna"] = _compute_failure_dna(kg_path, extra_category=norm_cat)

        # Optional node BKT lookup for compiled policy
        node_bkt: float | None = None
        if prop_id:
            for p in load_jsonl(kg_path / "propositions.jsonl"):
                if str(p.get("id") or "") == str(prop_id).strip():
                    raw_bkt = p.get("bktMastery")
                    if isinstance(raw_bkt, (int, float)) and not isinstance(raw_bkt, bool):
                        node_bkt = float(raw_bkt)
                    break

        policy = compile_teaching_policy(profile, domain=dom, node_bkt=node_bkt)
        profile["compiledPolicy"] = policy
        profile["updatedAt"] = ts
        save_profile(kg_path, profile)

    return {
        "ok": True,
        "domain": dom,
        "domainElo": new_theta_d,
        "expertiseBand": dom_entry["expertiseBand"],
        "effectiveScore": s_eff,
        "event": ev,
        "affectiveState": affect,
        "compiledPolicy": policy,
        "profile": profile,
    }


# ---------------------------------------------------------------------------
# User Overrides & Compact Policy Formatter (< 55 tokens, strictly 2 lines)
# ---------------------------------------------------------------------------


def apply_user_overrides(kg_dir: Path | str, overrides: dict[str, Any]) -> dict[str, Any]:
    """Atomically apply transparent user overrides to `learner-profile.json` and recompile policy."""
    if not isinstance(overrides, dict):
        raise ValueError("overrides must be a dict.")
    kg_path = _resolve_kg_path(kg_dir)
    path = kg_path / PROFILE_FILENAME

    with workspace_lock(path):
        profile = load_profile(kg_path)
        uo = profile.setdefault("userOverrides", {})

        if "scaffoldingMode" in overrides:
            val = overrides["scaffoldingMode"]
            if val is None or val == "":
                uo["scaffoldingMode"] = None
            elif str(val) in SCAFFOLDING_MODES:
                uo["scaffoldingMode"] = str(val)
            else:
                raise ValueError(f"Invalid scaffoldingMode {val!r}; expected {SCAFFOLDING_MODES} or None.")

        if "allowProductiveFailure" in overrides:
            val = overrides["allowProductiveFailure"]
            if val is None:
                uo["allowProductiveFailure"] = None
            elif isinstance(val, bool):
                uo["allowProductiveFailure"] = val
            else:
                raise ValueError("allowProductiveFailure must be bool or None.")

        if "maxProseWords" in overrides:
            val = overrides["maxProseWords"]
            if val is None:
                uo["maxProseWords"] = None
            elif isinstance(val, (int, float)) and not isinstance(val, bool) and math.isfinite(float(val)):
                uo["maxProseWords"] = int(round(_clamp(float(val), 60.0, 160.0)))
            else:
                raise ValueError("maxProseWords must be a finite int (60..160) or None.")

        if "sessionTargetMinutes" in overrides and overrides["sessionTargetMinutes"] is not None:
            raw_sm = overrides["sessionTargetMinutes"]
            if not isinstance(raw_sm, (int, float)) or isinstance(raw_sm, bool) or not math.isfinite(float(raw_sm)):
                raise ValueError("sessionTargetMinutes must be a finite int (10..90) or None.")
            profile["calibration"]["sessionTargetMinutes"] = int(
                round(_clamp(float(raw_sm), 10.0, 90.0))
            )

        policy = compile_teaching_policy(profile)
        profile["compiledPolicy"] = policy
        profile["updatedAt"] = now_iso()
        save_profile(kg_path, profile)

    return {
        "ok": True,
        "userOverrides": dict(uo),
        "compiledPolicy": policy,
        "profile": profile,
    }


def format_compact_policy_lines(profile: dict[str, Any], domain: str | None = None) -> list[str]:
    """Return strictly 2 concise lines (< 55 tokens total) for Turn-0 hook / MCP injection."""
    prof = _merge_defaults(profile if isinstance(profile, dict) else None)
    policy = compile_teaching_policy(prof, domain=domain)
    dom = policy["domain"]
    elo = int(round(float(policy["domainElo"])))
    band = policy["expertiseBand"]
    affect = policy["affectState"]

    meta = prof.get("metacognition") or {}
    brier = meta.get("brierScore")
    cal_lbl = str(meta.get("label") or "uncalibrated")
    brier_part = f"Brier {brier:.2f} ({cal_lbl})" if isinstance(brier, (int, float)) else f"Cal:{cal_lbl}"

    dna = prof.get("failureDna") or {}
    dom_tier = dna.get("dominantTier")
    dna_part = f" · DNA:{dom_tier}" if dom_tier else ""

    cal = prof.get("calibration") or {}
    total_ev = int((prof.get("globalStats") or {}).get("totalEvents") or 0)
    if not cal.get("completed") and total_ev == 0:
        line1 = f"Learner Profile: Uncalibrated ({dom} Elo {elo}, optional 2-min onboarding available)"
    else:
        line1 = f"Learner Profile: {dom} Elo {elo} ({band}) · Affect:{affect} · {brier_part}{dna_part}"

    pf_flag = "on" if policy["allowProductiveFailure"] else "off"
    ex_flag = "on" if policy["allowWorkedExample"] else "off"
    pred_flag = " · predFirst" if policy["requirePredictionFirst"] else ""
    guard_txt = ""
    if policy.get("activeGuardrails"):
        first_g = str(policy["activeGuardrails"][0])
        if len(first_g) > 48:
            first_g = first_g[:45].rstrip() + "..."
        guard_txt = f" · {first_g}"

    line2 = (
        f"TeachingPolicy: mode={policy['scaffoldingMode']} · ex={ex_flag} · PF={pf_flag} · "
        f"<= {policy['maxProseWords']}w · p*={policy['targetSuccessRate']:.2f}{pred_flag}{guard_txt}"
    )
    return [line1, line2]
