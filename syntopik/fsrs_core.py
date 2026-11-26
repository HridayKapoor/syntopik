"""Pure-stdlib FSRS-6 core, True FIRe, Spaced Repetition Compression,
Calm Backlog Recovery, Confidence-Aware BKT/Hypercorrection/Calibration,
Leech Triage, Per-Type Sibling Burying, and Zero-PyTorch SciPy L-BFGS-B Optimizer.

Runs cleanly on bare Python 3.12+ with zero external packages installed.
Delegates FSRS scheduling to `fsrs` when installed, and uses `scipy.optimize`
lazily ONLY inside `optimize_fsrs_params()`.
"""

from __future__ import annotations

import math
import random
import time
from collections import deque
from collections.abc import Sequence
from datetime import date as date_cls, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from syntopik.schemas import normalize_chain
from syntopik.storage import load_json

__all__ = [
    "DEFAULT_FSRS_PARAMETERS",
    "LOWER_BOUNDS_PARAMETERS",
    "UPPER_BOUNDS_PARAMETERS",
    "RATING_TO_INT",
    "INT_TO_RATING",
    "RATING_TRANSIT",
    "FSRS_DEFAULT_DECAY",
    "forgetting_curve",
    "next_interval_days",
    "load_fsrs_parameters",
    "card_retrievability",
    "review_card_dict",
    "bkt_update",
    "assess_confidence_signal",
    "compute_calibration",
    "ISOMORPHIC_FIRE_DEFAULT_WEIGHT",
    "ISOMORPHIC_FIRE_MAX_WEIGHT",
    "FireCreditList",
    "fire_ancestor_footprint",
    "fire_isomorphic_neighbors",
    "apply_fire_credit",
    "compress_due_queue",
    "recover_backlog",
    "forecast_retention",
    "detect_leeches",
    "apply_sibling_burying",
    "optimize_fsrs_params",
]

# Exact FSRS v6 21-parameter defaults & bounds (matching `fsrs` 6.x)
FSRS_DEFAULT_DECAY = 0.1542
DEFAULT_FSRS_PARAMETERS: tuple[float, ...] = (
    0.212,
    1.2931,
    2.3065,
    8.2956,
    6.4133,
    0.8334,
    3.0194,
    0.001,
    1.8722,
    0.1666,
    0.796,
    1.4835,
    0.0614,
    0.2629,
    1.6483,
    0.6014,
    1.8729,
    0.5425,
    0.0912,
    0.0658,
    FSRS_DEFAULT_DECAY,
)

STABILITY_MIN = 0.001
INITIAL_STABILITY_MAX = 100.0
MIN_DIFFICULTY = 1.0
MAX_DIFFICULTY = 10.0

LOWER_BOUNDS_PARAMETERS: tuple[float, ...] = (
    STABILITY_MIN,
    STABILITY_MIN,
    STABILITY_MIN,
    STABILITY_MIN,
    1.0,
    0.001,
    0.001,
    0.001,
    0.0,
    0.0,
    0.001,
    0.001,
    0.001,
    0.001,
    0.0,
    0.0,
    1.0,
    0.0,
    0.0,
    0.0,
    0.1,
)

UPPER_BOUNDS_PARAMETERS: tuple[float, ...] = (
    INITIAL_STABILITY_MAX,
    INITIAL_STABILITY_MAX,
    INITIAL_STABILITY_MAX,
    INITIAL_STABILITY_MAX,
    10.0,
    4.0,
    4.0,
    0.75,
    4.5,
    0.8,
    3.5,
    5.0,
    0.25,
    0.9,
    4.0,
    1.0,
    6.0,
    2.0,
    2.0,
    0.8,
    0.8,
)

FUZZ_RANGES: tuple[tuple[float, float, float], ...] = (
    (2.5, 7.0, 0.15),
    (7.0, 20.0, 0.10),
    (20.0, math.inf, 0.05),
)

RATING_TO_INT: dict[str, int] = {
    "again": 1,
    "hard": 2,
    "good": 3,
    "easy": 4,
}
INT_TO_RATING: dict[int, str] = {v: k for k, v in RATING_TO_INT.items()}

# Transit probability scaled by review quality so a failed recall never inflates BKT mastery.
RATING_TRANSIT: dict[str, float] = {
    "again": 0.05,
    "hard": 0.10,
    "good": 0.25,
    "easy": 0.35,
}

STATE_LEARNING = 1
STATE_REVIEW = 2
STATE_RELEARNING = 3


# ---------------------------------------------------------------------------
# Datetime & parameter utilities
# ---------------------------------------------------------------------------


def _parse_utc_dt(value: Any) -> datetime | None:
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


def _validate_parameters(params: Sequence[float] | None) -> tuple[float, ...]:
    if params is None:
        return DEFAULT_FSRS_PARAMETERS
    if len(params) != len(DEFAULT_FSRS_PARAMETERS):
        return DEFAULT_FSRS_PARAMETERS
    out: list[float] = []
    for val, lo, hi in zip(params, LOWER_BOUNDS_PARAMETERS, UPPER_BOUNDS_PARAMETERS):
        try:
            fv = float(val)
        except (TypeError, ValueError):
            return DEFAULT_FSRS_PARAMETERS
        if not math.isfinite(fv) or not (lo <= fv <= hi):
            return DEFAULT_FSRS_PARAMETERS
        out.append(fv)
    return tuple(out)


def load_fsrs_parameters(kg_dir: Path | str | None = None) -> tuple[float, ...]:
    """Load accepted personal 21-weight FSRS-6 parameters from `fsrs-params.json` if valid."""
    if kg_dir is None:
        return DEFAULT_FSRS_PARAMETERS
    cfg = load_json(Path(kg_dir).expanduser() / "fsrs-params.json", default=None)
    if not isinstance(cfg, dict):
        return DEFAULT_FSRS_PARAMETERS
    if cfg.get("accepted") is False:
        return DEFAULT_FSRS_PARAMETERS
    raw_params = cfg.get("parameters")
    if isinstance(raw_params, list):
        return _validate_parameters(raw_params)
    return DEFAULT_FSRS_PARAMETERS


# ---------------------------------------------------------------------------
# Pure-stdlib FSRS-6 mathematics (matching `fsrs` 6.x)
# ---------------------------------------------------------------------------


def forgetting_curve(elapsed_days: float, stability: float, decay: float = FSRS_DEFAULT_DECAY) -> float:
    """Power-law FSRS-6 forgetting curve `R(t, S) = (1 + factor * t / S)^(-w20)`."""
    s = max(STABILITY_MIN, float(stability))
    t = max(0.0, float(elapsed_days))
    d = -max(0.01, abs(float(decay)))
    factor = (0.9 ** (1.0 / d)) - 1.0
    return float((1.0 + factor * t / s) ** d)


def next_interval_days(
    stability: float,
    desired_retention: float = 0.9,
    decay: float = FSRS_DEFAULT_DECAY,
    maximum_interval: int = 36500,
) -> int:
    """Compute integer day interval for a target retrievability under FSRS-6."""
    s = max(STABILITY_MIN, float(stability))
    r_req = max(0.5, min(0.99, float(desired_retention)))
    d = -max(0.01, abs(float(decay)))
    factor = (0.9 ** (1.0 / d)) - 1.0
    ivl = (s / factor) * ((r_req ** (1.0 / d)) - 1.0)
    return max(1, min(int(maximum_interval), int(round(ivl))))


def _clamp_difficulty(d: float) -> float:
    return min(max(d, MIN_DIFFICULTY), MAX_DIFFICULTY)


def _clamp_stability(s: float) -> float:
    return max(s, STABILITY_MIN)


def _initial_stability(params: Sequence[float], rating_int: int) -> float:
    return _clamp_stability(float(params[rating_int - 1]))


def _initial_difficulty(params: Sequence[float], rating_int: int, *, clamp: bool = True) -> float:
    d = float(params[4]) - math.exp(float(params[5]) * (rating_int - 1)) + 1.0
    return _clamp_difficulty(d) if clamp else d


def _next_difficulty(params: Sequence[float], difficulty: float, rating_int: int) -> float:
    arg_1 = _initial_difficulty(params, 4, clamp=False)
    delta_d = -(float(params[6]) * (rating_int - 3))
    damped = (10.0 - difficulty) * delta_d / 9.0
    arg_2 = difficulty + damped
    next_d = float(params[7]) * arg_1 + (1.0 - float(params[7])) * arg_2
    return _clamp_difficulty(next_d)


def _short_term_stability(params: Sequence[float], stability: float, rating_int: int) -> float:
    inc = math.exp(float(params[17]) * (rating_int - 3 + float(params[18]))) * (
        max(STABILITY_MIN, stability) ** (-float(params[19]))
    )
    if rating_int in (2, 3, 4):
        inc = max(inc, 1.0)
    return _clamp_stability(stability * inc)


def _next_stability(
    params: Sequence[float],
    difficulty: float,
    stability: float,
    retrievability: float,
    rating_int: int,
) -> float:
    s = max(STABILITY_MIN, stability)
    if rating_int == 1:
        long_term = (
            float(params[11])
            * (difficulty ** (-float(params[12])))
            * (((s + 1.0) ** float(params[13])) - 1.0)
            * math.exp((1.0 - retrievability) * float(params[14]))
        )
        short_term = s / math.exp(float(params[17]) * float(params[18]))
        return _clamp_stability(min(long_term, short_term))

    hard_pen = float(params[15]) if rating_int == 2 else 1.0
    easy_bon = float(params[16]) if rating_int == 4 else 1.0
    next_s = s * (
        1.0
        + math.exp(float(params[8]))
        * (11.0 - difficulty)
        * (s ** (-float(params[9])))
        * (math.exp((1.0 - retrievability) * float(params[10])) - 1.0)
        * hard_pen
        * easy_bon
    )
    return _clamp_stability(next_s)


def _fuzz_interval_days(interval_days: int, maximum_interval: int = 36500) -> int:
    if interval_days < 2.5:
        return interval_days
    delta = 1.0
    for start, end, factor in FUZZ_RANGES:
        delta += factor * max(min(float(interval_days), end) - start, 0.0)
    min_ivl = max(2, round(interval_days - delta))
    max_ivl = min(round(interval_days + delta), maximum_interval)
    min_ivl = min(min_ivl, max_ivl)
    chosen = math.floor(random.random() * (max_ivl - min_ivl + 1) + min_ivl)
    return max(1, min(int(chosen), maximum_interval))


def _stdlib_review_card(
    card_dict: dict[str, Any] | None,
    rating_int: int,
    now: datetime,
    params: tuple[float, ...],
    desired_retention: float = 0.9,
    enable_fuzzing: bool = True,
    maximum_interval: int = 36500,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Pure-stdlib FSRS-6 review transition (day-level spacing, empty intraday steps)."""
    card = dict(card_dict) if isinstance(card_dict, dict) else {}
    card_id = card.get("card_id")
    if not isinstance(card_id, int):
        card_id = int(time.time() * 1000)

    prev_state = int(card.get("state") or STATE_LEARNING)
    stability = float(card["stability"]) if isinstance(card.get("stability"), (int, float)) else None
    difficulty = float(card["difficulty"]) if isinstance(card.get("difficulty"), (int, float)) else None
    last_rev = _parse_utc_dt(card.get("last_review"))

    elapsed_days: int | None = None
    if last_rev is not None:
        elapsed_days = max(0, (now - last_rev).days)

    predicted_r: float | None = None
    if stability is not None and last_rev is not None:
        predicted_r = forgetting_curve(elapsed_days or 0, stability, decay=params[20])

    if stability is None or difficulty is None:
        new_s = _initial_stability(params, rating_int)
        new_d = _initial_difficulty(params, rating_int, clamp=True)
    elif elapsed_days is not None and elapsed_days < 1:
        new_s = _short_term_stability(params, stability, rating_int)
        new_d = _next_difficulty(params, difficulty, rating_int)
    else:
        r_now = predicted_r if predicted_r is not None else 0.9
        new_s = _next_stability(params, difficulty, stability, r_now, rating_int)
        new_d = _next_difficulty(params, difficulty, rating_int)

    ivl_days = next_interval_days(
        new_s,
        desired_retention=desired_retention,
        decay=params[20],
        maximum_interval=maximum_interval,
    )
    if enable_fuzzing:
        ivl_days = _fuzz_interval_days(ivl_days, maximum_interval=maximum_interval)

    due_dt = now + timedelta(days=ivl_days)
    new_card_dict = {
        "card_id": card_id,
        "state": STATE_REVIEW,
        "step": None,
        "stability": float(new_s),
        "difficulty": float(new_d),
        "due": due_dt.isoformat(),
        "last_review": now.isoformat(),
    }
    meta = {
        "elapsedDays": elapsed_days if elapsed_days is not None else 0,
        "prevState": prev_state,
        "predictedR": round(predicted_r, 4) if predicted_r is not None else None,
        "intervalDays": ivl_days,
        "backend": "stdlib",
    }
    return new_card_dict, meta


def card_retrievability(
    card_dict: dict[str, Any] | None,
    now: datetime | None = None,
    *,
    parameters: Sequence[float] | None = None,
    fractional_days: bool = False,
) -> float:
    """Compute FSRS-6 retrievability `R(t, S)` with optional `fsrs` delegation or stdlib fallback."""
    if not isinstance(card_dict, dict):
        return 0.0
    stability = card_dict.get("stability")
    last_rev = _parse_utc_dt(card_dict.get("last_review"))
    if not isinstance(stability, (int, float)) or last_rev is None:
        return 0.0

    params = _validate_parameters(parameters)
    ref_now = _parse_utc_dt(now) if now is not None else datetime.now(timezone.utc)
    assert ref_now is not None

    if fractional_days:
        elapsed = max(0.0, (ref_now - last_rev).total_seconds() / 86400.0)
        return float(forgetting_curve(elapsed, float(stability), decay=params[20]))

    try:
        import fsrs  # type: ignore[import-not-found]

        sched = fsrs.Scheduler(parameters=params, learning_steps=(), relearning_steps=())
        card = fsrs.Card.from_dict(card_dict)
        return float(sched.get_card_retrievability(card, ref_now))
    except Exception:
        elapsed_days = max(0, (ref_now - last_rev).days)
        return float(forgetting_curve(elapsed_days, float(stability), decay=params[20]))


def review_card_dict(
    card_dict: dict[str, Any] | None,
    rating: str | int,
    now: datetime | None = None,
    *,
    parameters: Sequence[float] | None = None,
    desired_retention: float = 0.9,
    enable_fuzzing: bool = True,
    review_duration_ms: int | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Review a serialized FSRS card dict, delegating to `fsrs` when installed else stdlib FSRS-6."""
    if isinstance(rating, int):
        rating_int = rating
    else:
        rating_int = RATING_TO_INT[str(rating).strip().lower()]
    if rating_int not in (1, 2, 3, 4):
        raise ValueError(f"Invalid rating {rating!r}; expected again/hard/good/easy or 1..4.")

    params = _validate_parameters(parameters)
    ref_now = _parse_utc_dt(now) if now is not None else datetime.now(timezone.utc)
    assert ref_now is not None

    prev_state = int((card_dict or {}).get("state") or STATE_LEARNING)
    prev_last_rev = _parse_utc_dt((card_dict or {}).get("last_review"))
    elapsed_days = max(0, (ref_now - prev_last_rev).days) if prev_last_rev is not None else 0
    predicted_r: float | None = None
    if isinstance(card_dict, dict) and isinstance(card_dict.get("stability"), (int, float)) and prev_last_rev:
        predicted_r = round(
            forgetting_curve(elapsed_days, float(card_dict["stability"]), decay=params[20]),
            4,
        )

    try:
        import fsrs  # type: ignore[import-not-found]

        rating_map = {
            1: fsrs.Rating.Again,
            2: fsrs.Rating.Hard,
            3: fsrs.Rating.Good,
            4: fsrs.Rating.Easy,
        }
        sched = fsrs.Scheduler(
            parameters=params,
            desired_retention=desired_retention,
            learning_steps=(),
            relearning_steps=(),
            enable_fuzzing=enable_fuzzing,
        )
        card = fsrs.Card.from_dict(card_dict) if isinstance(card_dict, dict) else fsrs.Card()
        new_card, _log = sched.review_card(
            card,
            rating_map[rating_int],
            ref_now,
            review_duration=review_duration_ms,
        )
        out_dict = new_card.to_dict()
        due_dt = _parse_utc_dt(out_dict.get("due")) or (ref_now + timedelta(days=1))
        ivl_days = max(1, int(round((due_dt - ref_now).total_seconds() / 86400.0)))
        meta = {
            "elapsedDays": elapsed_days,
            "prevState": prev_state,
            "predictedR": predicted_r,
            "intervalDays": ivl_days,
            "backend": "fsrs",
        }
        return out_dict, meta
    except Exception:
        return _stdlib_review_card(
            card_dict,
            rating_int,
            ref_now,
            params,
            desired_retention=desired_retention,
            enable_fuzzing=enable_fuzzing,
        )


# ---------------------------------------------------------------------------
# Confidence-Aware BKT, Hypercorrection & Brier Calibration
# ---------------------------------------------------------------------------


def bkt_update(
    prior: float,
    correct: bool,
    p_transit: float = 0.25,
    p_slip: float = 0.10,
    p_guess: float = 0.20,
    *,
    learner_confidence: float | None = None,
) -> float:
    """Corbett & Anderson Bayesian Knowledge Tracing with optional confidence-aware damping.

    When `learner_confidence` is omitted, produces the exact classic BKT update.
    - Low-confidence hit (`c <= 0.35`, `correct=True`): raises `p_guess` & halves `p_transit`
      to damp lucky guesses.
    - High-confidence miss (`c >= 0.75`, `correct=False`): lowers `p_slip` & caps `p_transit`
      so strong misconceptions pull mastery down decisively.
    """
    p = max(0.01, min(0.99, float(prior)))
    slip = float(p_slip)
    guess = float(p_guess)
    transit = float(p_transit)

    if learner_confidence is not None:
        c = max(0.0, min(1.0, float(learner_confidence)))
        if correct and c <= 0.35:
            guess = max(guess, 0.45)
            transit = transit * 0.5
        elif (not correct) and c >= 0.75:
            slip = min(slip, 0.05)
            transit = min(transit, 0.05)

    if correct:
        num = p * (1.0 - slip)
        den = num + (1.0 - p) * guess
    else:
        num = p * slip
        den = num + (1.0 - p) * (1.0 - guess)
    posterior = num / den if den > 0 else p
    updated = posterior + (1.0 - posterior) * transit
    return round(max(0.01, min(0.995, updated)), 3)


def assess_confidence_signal(
    correct: bool,
    learner_confidence: float | None,
) -> dict[str, Any]:
    """Classify Butterfield-Metcalfe hypercorrection, lucky guesses, and Brier error."""
    if learner_confidence is None:
        return {
            "learnerConfidence": None,
            "hypercorrection": False,
            "luckyGuess": False,
            "brierError": None,
            "calibrationBias": None,
        }
    c = max(0.0, min(1.0, float(learner_confidence)))
    y = 1.0 if correct else 0.0
    return {
        "learnerConfidence": round(c, 4),
        "hypercorrection": bool((not correct) and c >= 0.75),
        "luckyGuess": bool(correct and c <= 0.35),
        "brierError": round((c - y) ** 2, 4),
        "calibrationBias": round(c - y, 4),
    }


def compute_calibration(
    revlog_rows: list[dict[str, Any]],
    *,
    n_bins: int = 5,
) -> dict[str, Any]:
    """Compute Brier score, Expected Calibration Error (ECE), overconfidence bias,
    and hypercorrection / lucky-guess counts from `revlog.jsonl` records.
    """
    scored: list[tuple[float, float]] = []
    hyper_count = 0
    lucky_count = 0

    for row in revlog_rows:
        if not isinstance(row, dict):
            continue
        conf_raw = row.get("learnerConfidence")
        if not isinstance(conf_raw, (int, float)) or isinstance(conf_raw, bool):
            continue
        c = max(0.0, min(1.0, float(conf_raw)))
        corr_raw = row.get("correct")
        if isinstance(corr_raw, bool):
            y = 1.0 if corr_raw else 0.0
        elif isinstance(corr_raw, (int, float)):
            y = 1.0 if float(corr_raw) >= 0.5 else 0.0
        else:
            r = row.get("rating")
            if isinstance(r, int):
                y = 1.0 if r >= 3 else 0.0
            else:
                y = 1.0 if str(r or "").lower() in ("good", "easy") else 0.0

        scored.append((c, y))
        if y < 0.5 and c >= 0.75:
            hyper_count += 1
        elif y >= 0.5 and c <= 0.35:
            lucky_count += 1

    n = len(scored)
    if n == 0:
        return {
            "count": 0,
            "brierScore": None,
            "ece": None,
            "overconfidenceBias": None,
            "meanConfidence": None,
            "accuracy": None,
            "hypercorrectionCount": 0,
            "luckyGuessCount": 0,
            "bins": [],
            "summary": "No confidence-scored reviews yet.",
        }

    brier = sum((c - y) ** 2 for c, y in scored) / n
    mean_c = sum(c for c, _ in scored) / n
    acc = sum(y for _, y in scored) / n
    bias = mean_c - acc

    bins_n = max(2, int(n_bins))
    bucket_rows: list[list[tuple[float, float]]] = [[] for _ in range(bins_n)]
    for c, y in scored:
        idx = min(bins_n - 1, int(c * bins_n))
        bucket_rows[idx].append((c, y))

    ece = 0.0
    bins_out: list[dict[str, Any]] = []
    for i, b_items in enumerate(bucket_rows):
        lo = round(i / bins_n, 2)
        hi = round((i + 1) / bins_n, 2)
        if not b_items:
            continue
        b_conf = sum(c for c, _ in b_items) / len(b_items)
        b_acc = sum(y for _, y in b_items) / len(b_items)
        ece += (len(b_items) / n) * abs(b_acc - b_conf)
        bins_out.append(
            {
                "binLow": lo,
                "binHigh": hi,
                "count": len(b_items),
                "avgConfidence": round(b_conf, 4),
                "accuracy": round(b_acc, 4),
            }
        )

    bias_pct = round(bias * 100.0, 1)
    bias_sign = "+" if bias_pct >= 0 else ""
    return {
        "count": n,
        "brierScore": round(brier, 4),
        "ece": round(ece, 4),
        "overconfidenceBias": round(bias, 4),
        "meanConfidence": round(mean_c, 4),
        "accuracy": round(acc, 4),
        "hypercorrectionCount": hyper_count,
        "luckyGuessCount": lucky_count,
        "bins": bins_out,
        "summary": f"Brier {brier:.2f} · ECE {ece:.2f} · Overconfidence {bias_sign}{bias_pct}%",
    }


# ---------------------------------------------------------------------------
# True FIRe (Fractional Implicit Repetition) & Ancestor Traversal
# ---------------------------------------------------------------------------


ISOMORPHIC_FIRE_DEFAULT_WEIGHT = 0.25
ISOMORPHIC_FIRE_MAX_WEIGHT = 0.30


class FireCreditList(list):
    """Backwards-compatible `list` of FIRe credit records carrying `refreshed` and `isomorphicRefreshed` counts."""

    def __init__(
        self,
        items: Sequence[dict[str, Any]] = (),
        *,
        prerequisite_refreshed: int = 0,
        isomorphic_refreshed: int = 0,
    ) -> None:
        super().__init__(items)
        self.prerequisiteRefreshed: int = int(prerequisite_refreshed)
        self.isomorphicRefreshed: int = int(isomorphic_refreshed)
        self.refreshed: int = int(prerequisite_refreshed) + int(isomorphic_refreshed)

    def to_summary(self) -> dict[str, Any]:
        return {
            "credits": list(self),
            "refreshed": self.refreshed,
            "prerequisiteRefreshed": self.prerequisiteRefreshed,
            "isomorphicRefreshed": self.isomorphicRefreshed,
        }


def _build_incoming_prereq_map(chains: list[dict[str, Any]]) -> dict[str, list[tuple[str, float]]]:
    """Map `conclusion -> [(premise, encompassWeight), ...]` keeping max weight per pair.

    Excludes symmetric `isomorphic_to` chords so they never trigger prerequisite knockouts
    or multi-hop ancestor amplification; those are handled via gentle cross-domain FIRe.
    """
    best_edge: dict[tuple[str, str], float] = {}
    for raw in chains:
        norm = normalize_chain(raw)
        if not norm:
            continue
        if str(norm["relation"]).strip().lower() == "isomorphic_to":
            continue
        w = float(norm["encompassWeight"])
        if w <= 0.0:
            continue
        conc = norm["conclusion"]
        for prem in norm["premises"]:
            key = (conc, prem)
            if w > best_edge.get(key, 0.0):
                best_edge[key] = w

    incoming: dict[str, list[tuple[str, float]]] = {}
    for (conc, prem), w in best_edge.items():
        incoming.setdefault(conc, []).append((prem, w))
    for conc in incoming:
        incoming[conc].sort(key=lambda pair: (-pair[1], pair[0]))
    return incoming


def _build_outgoing_descendants_map(chains: list[dict[str, Any]]) -> dict[str, set[str]]:
    """Compute transitive downstream descendant sets per node via pure-stdlib BFS."""
    adj: dict[str, set[str]] = {}
    nodes: set[str] = set()
    for raw in chains:
        norm = normalize_chain(raw)
        if not norm:
            continue
        if str(norm["relation"]).strip().lower() == "isomorphic_to":
            continue
        conc = norm["conclusion"]
        nodes.add(conc)
        for prem in norm["premises"]:
            nodes.add(prem)
            adj.setdefault(prem, set()).add(conc)

    desc_map: dict[str, set[str]] = {}
    for u in nodes:
        visited: set[str] = set()
        q: deque[str] = deque(adj.get(u, ()))
        while q:
            cur = q.popleft()
            if cur in visited or cur == u:
                continue
            visited.add(cur)
            for nxt in adj.get(cur, ()):
                if nxt not in visited and nxt != u:
                    q.append(nxt)
        desc_map[u] = visited
    return desc_map


def fire_isomorphic_neighbors(
    node_id: str,
    chains: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Collect bidirectional persisted `isomorphic_to` neighbors with weight capped `<= 0.30`."""
    target = str(node_id or "").strip()
    if not target:
        return {}
    neighbors: dict[str, dict[str, Any]] = {}
    for raw in chains:
        if not isinstance(raw, dict):
            continue
        if str(raw.get("relation") or "").strip().lower() != "isomorphic_to":
            continue
        norm = normalize_chain(raw)
        if not norm:
            continue
        raw_w = raw.get("encompassWeight")
        if raw_w is None:
            raw_w = raw.get("encompass_weight")
        if isinstance(raw_w, (int, float)) and not isinstance(raw_w, bool) and math.isfinite(float(raw_w)):
            eff_w = min(ISOMORPHIC_FIRE_MAX_WEIGHT, max(0.0, float(raw_w)))
        else:
            eff_w = ISOMORPHIC_FIRE_DEFAULT_WEIGHT
        if eff_w <= 0.0:
            continue

        conc = norm["conclusion"]
        prems = norm["premises"]
        arch_id = str(raw.get("archetypeId") or "").strip() or None
        other_ids: list[str] = []
        if target == conc:
            other_ids = [p for p in prems if p != target]
        elif target in prems:
            other_ids = [conc] if conc != target else []

        for oid in other_ids:
            prev = neighbors.get(oid)
            if prev is None or eff_w > prev["weight"] + 1e-9:
                neighbors[oid] = {
                    "weight": round(eff_w, 4),
                    "archetypeId": arch_id,
                }
    return neighbors


def _apply_credit_to_target(
    target_id: str,
    anc: dict[str, Any],
    *,
    base_weight: float,
    depth: int,
    rating_scale: float,
    max_credit: float,
    ref_now: datetime,
    today_str: str,
    params: tuple[float, ...],
    desired_retention: float,
    is_isomorphic: bool = False,
    archetype_id: str | None = None,
) -> dict[str, Any] | None:
    """Apply discounted FIRe stability/due refresh to a single eligible proposition in-place."""
    if bool(anc.get("archived")) or bool(anc.get("suspended")):
        return None
    status = str(anc.get("status") or "planned").strip().lower()
    # Hard safety invariant: NEVER auto-clear misconception or unlearned planned nodes.
    if status in ("misconception", "planned"):
        return None

    fsrs_dict = anc.get("fsrs") if isinstance(anc.get("fsrs"), dict) else None
    old_next_rev = str(anc.get("nextReview") or "") or None

    if fsrs_dict is not None and isinstance(fsrs_dict.get("stability"), (int, float)):
        old_s = max(STABILITY_MIN, float(fsrs_dict["stability"]))
        last_rev_dt = _parse_utc_dt(fsrs_dict.get("last_review"))
        old_due_dt = _parse_utc_dt(fsrs_dict.get("due"))
        if last_rev_dt is not None:
            elapsed_frac = max(0.0, (ref_now - last_rev_dt).total_seconds() / 86400.0)
            r_u = forgetting_curve(elapsed_frac, old_s, decay=params[20])
        else:
            r_u = 0.5
        if old_due_dt is not None and last_rev_dt is not None:
            old_ivl = max(1, int(round((old_due_dt - last_rev_dt).total_seconds() / 86400.0)))
        else:
            old_ivl = next_interval_days(old_s, desired_retention=desired_retention, decay=params[20])
        working_card = dict(fsrs_dict)
    else:
        # Solid/shaky node created before FSRS tracking: infer conservative baseline stability.
        last_tested_dt = _parse_utc_dt(anc.get("lastTested"))
        old_due_dt = _parse_utc_dt(old_next_rev)
        if last_tested_dt is not None and old_due_dt is not None and old_due_dt > last_tested_dt:
            old_ivl = max(1, (old_due_dt.date() - last_tested_dt.date()).days)
            old_s = float(old_ivl)
        else:
            old_ivl = 3 if status == "solid" else 1
            old_s = float(params[2] if status == "solid" else params[1])
        if last_tested_dt is not None:
            elapsed_frac = max(0.0, (ref_now - last_tested_dt).total_seconds() / 86400.0)
            r_u = forgetting_curve(elapsed_frac, old_s, decay=params[20])
        elif old_due_dt is not None and old_due_dt.date() <= ref_now.date():
            overdue_days = max(1, (ref_now.date() - old_due_dt.date()).days + old_ivl)
            r_u = forgetting_curve(float(overdue_days), old_s, decay=params[20])
        else:
            r_u = 0.70 if status == "solid" else 0.45
        working_card = {
            "card_id": int(time.time() * 1000) + hash(target_id) % 10000,
            "state": STATE_REVIEW,
            "step": None,
            "stability": old_s,
            "difficulty": _initial_difficulty(params, 3, clamp=True),
            "due": (old_due_dt or ref_now).isoformat(),
            "last_review": (last_tested_dt or (ref_now - timedelta(days=max(1, old_ivl)))).isoformat(),
        }

    # Early-review memory discount: (1 - R_u) prevents spurious inflation on freshly solid cards.
    credit = min(max_credit, rating_scale * base_weight * max(0.0, 1.0 - r_u))
    if credit < 0.005:
        return None

    # Hypothetical full unaided "good" review stability to interpolate toward smoothly.
    full_card, _ = _stdlib_review_card(
        working_card,
        3,
        ref_now,
        params,
        desired_retention=desired_retention,
        enable_fuzzing=False,
    )
    s_full = max(old_s, float(full_card["stability"]))
    new_s = round(old_s + credit * max(0.0, s_full - old_s), 4)
    new_ivl = max(old_ivl, next_interval_days(new_s, desired_retention=desired_retention, decay=params[20]))

    old_due_dt_eff = _parse_utc_dt(working_card.get("due")) or ref_now
    delta_days = max(0, new_ivl - old_ivl)
    if old_due_dt_eff <= ref_now:
        # Overdue or due today: smoothly push forward proportionally to earned implicit credit.
        push_days = max(1, int(round(max(credit * new_ivl, delta_days))))
        new_due_dt = ref_now + timedelta(days=push_days)
    else:
        # Upcoming card: extend existing due date by stability interval gain.
        extra_days = delta_days if delta_days > 0 else (1 if credit >= 0.25 else 0)
        new_due_dt = old_due_dt_eff + timedelta(days=extra_days)

    working_card["stability"] = new_s
    working_card["due"] = new_due_dt.isoformat()
    anc["fsrs"] = working_card
    anc["nextReview"] = new_due_dt.date().isoformat()
    anc["implicitCredit"] = round(float(anc.get("implicitCredit") or 0.0) + credit, 4)
    anc["lastImplicitReview"] = today_str

    rec: dict[str, Any] = {
        "propId": target_id,
        "credit": round(credit, 4),
        "depth": depth,
        "kind": "isomorphic" if is_isomorphic else "prerequisite",
        "isomorphic": bool(is_isomorphic),
        "retrievability": round(r_u, 4),
        "oldStability": round(old_s, 3),
        "newStability": round(new_s, 3),
        "oldInterval": int(old_ivl),
        "newInterval": int(new_ivl),
        "oldNextReview": old_next_rev,
        "newNextReview": anc["nextReview"],
    }
    if is_isomorphic:
        rec["relation"] = "isomorphic_to"
        rec["encompassWeight"] = round(base_weight, 4)
        if archetype_id:
            rec["archetypeId"] = archetype_id
    return rec


def fire_ancestor_footprint(
    node_id: str,
    chains: list[dict[str, Any]],
    *,
    max_depth: int = 3,
    gamma: float = 0.7,
) -> dict[str, tuple[float, int]]:
    """Return `{ancestor_id: (max_decayed_weight, min_depth)}` for prerequisites up to `max_depth`.

    At depth `d >= 1`, path weight = `(prod edge weights) * (gamma ** (d - 1))`.
    Cycles are safely prevented via per-path visited sets.
    """
    incoming = _build_incoming_prereq_map(chains)
    best: dict[str, tuple[float, int]] = {}
    depth_cap = max(1, int(max_depth))

    def _dfs(cur: str, depth: int, prod_weight: float, path_seen: set[str]) -> None:
        if depth > depth_cap:
            return
        for prem, edge_w in incoming.get(cur, ()):
            if prem in path_seen or edge_w <= 0.0:
                continue
            new_prod = prod_weight * edge_w
            decayed = new_prod * (gamma ** (depth - 1))
            prev = best.get(prem)
            if prev is None or decayed > prev[0] + 1e-9 or (abs(decayed - prev[0]) <= 1e-9 and depth < prev[1]):
                best[prem] = (decayed, depth)
            if depth < depth_cap:
                path_seen.add(prem)
                _dfs(prem, depth + 1, new_prod, path_seen)
                path_seen.remove(prem)

    _dfs(node_id, 1, 1.0, {node_id})
    return best


def apply_fire_credit(
    reviewed_prop_id: str,
    rating: str,
    props: list[dict[str, Any]],
    chains: list[dict[str, Any]],
    *,
    now: datetime | None = None,
    parameters: Sequence[float] | None = None,
    max_depth: int = 3,
    gamma: float = 0.7,
    desired_retention: float = 0.9,
    include_isomorphic: bool = True,
    return_summary: bool = False,
) -> FireCreditList | dict[str, Any]:
    """Apply True FIRe (Fractional Implicit Repetition) credit to prerequisite ancestors
    and gentle (`<= 0.30`, default `0.25`) cross-domain credit across persisted `isomorphic_to` chords.

    - Runs ONLY on unaided `good` or `easy` ratings.
    - Walks `chains.jsonl` prerequisite ancestors up to `d <= 3` with `encompassWeight` & depth decay `gamma=0.7`.
    - Walks direct bidirectional persisted `isomorphic_to` chords with weight capped `<= 0.30` (default `0.25`).
    - Applies early-review memory discount `(1 - R_u)` so freshly reviewed nodes receive near-zero inflation.
    - NEVER modifies or auto-clears `misconception` (or unlearned `planned`) nodes.
    - Returns a backwards-compatible `FireCreditList` (`list` subclass exposing `.refreshed` and
      `.isomorphicRefreshed`), or a summary dict when `return_summary=True`.
    """
    r_low = str(rating or "").strip().lower()
    if r_low not in ("good", "easy"):
        empty = FireCreditList()
        return empty.to_summary() if return_summary else empty

    rating_scale = 1.0 if r_low == "good" else 1.2
    params = _validate_parameters(parameters)
    ref_now = _parse_utc_dt(now) if now is not None else datetime.now(timezone.utc)
    assert ref_now is not None
    today_str = ref_now.date().isoformat()

    footprint = fire_ancestor_footprint(reviewed_prop_id, chains, max_depth=max_depth, gamma=gamma)
    iso_neighbors = fire_isomorphic_neighbors(reviewed_prop_id, chains) if include_isomorphic else {}
    if not footprint and not iso_neighbors:
        empty = FireCreditList()
        return empty.to_summary() if return_summary else empty

    props_by_id = {str(p.get("id") or ""): p for p in props if isinstance(p, dict) and p.get("id")}
    credits_raw: list[dict[str, Any]] = []
    credited_ids: set[str] = set()
    prereq_refreshed = 0
    iso_refreshed = 0

    for anc_id, (base_weight, depth) in sorted(footprint.items(), key=lambda kv: (kv[1][1], -kv[1][0], kv[0])):
        anc = props_by_id.get(anc_id)
        if anc is None:
            continue
        rec = _apply_credit_to_target(
            anc_id,
            anc,
            base_weight=base_weight,
            depth=depth,
            rating_scale=rating_scale,
            max_credit=0.85,
            ref_now=ref_now,
            today_str=today_str,
            params=params,
            desired_retention=desired_retention,
            is_isomorphic=False,
        )
        if rec is not None:
            credits_raw.append(rec)
            credited_ids.add(anc_id)
            prereq_refreshed += 1

    for twin_id, info in sorted(iso_neighbors.items(), key=lambda kv: (-kv[1]["weight"], kv[0])):
        if twin_id in credited_ids:
            continue
        twin = props_by_id.get(twin_id)
        if twin is None:
            continue
        rec = _apply_credit_to_target(
            twin_id,
            twin,
            base_weight=float(info["weight"]),
            depth=1,
            rating_scale=rating_scale,
            max_credit=ISOMORPHIC_FIRE_MAX_WEIGHT,
            ref_now=ref_now,
            today_str=today_str,
            params=params,
            desired_retention=desired_retention,
            is_isomorphic=True,
            archetype_id=info.get("archetypeId"),
        )
        if rec is not None:
            credits_raw.append(rec)
            credited_ids.add(twin_id)
            iso_refreshed += 1

    result = FireCreditList(
        credits_raw,
        prerequisite_refreshed=prereq_refreshed,
        isomorphic_refreshed=iso_refreshed,
    )
    return result.to_summary() if return_summary else result


# ---------------------------------------------------------------------------
# Spaced Repetition Compression (Greedy Set-Cover Graph Knockouts)
# ---------------------------------------------------------------------------


def compress_due_queue(
    due_items: list[dict[str, Any]],
    chains: list[dict[str, Any]],
    props_by_id: dict[str, dict[str, Any]] | None = None,
    *,
    max_depth: int = 3,
    gamma: float = 0.7,
    min_credit: float = 0.10,
    min_knockouts: int = 2,
) -> dict[str, Any]:
    """Greedy set-cover over due nodes selecting composite reviews that knock out `>= 2`
    due prerequisites at once via FIRe. Never knocks out `misconception` nodes.
    """
    if not due_items:
        return {
            "selected": [],
            "knockedOut": [],
            "originalCount": 0,
            "compressedCount": 0,
            "knockedOutCount": 0,
            "savingsPct": 0.0,
            "summary": "0 due items.",
        }

    item_by_id = {str(it["id"]): dict(it) for it in due_items if it.get("id")}
    due_ids = list(item_by_id.keys())
    due_set = set(due_ids)
    order_index = {pid: idx for idx, pid in enumerate(due_ids)}

    # Misconception nodes are never knocked out implicitly and never trusted to knock out others.
    coverable: set[str] = {
        pid for pid in due_ids if str(item_by_id[pid].get("status") or "").lower() != "misconception"
    }

    # Precompute each candidate's knock-out set among coverable due prerequisites.
    covers_map: dict[str, dict[str, float]] = {}
    for pid in due_ids:
        if str(item_by_id[pid].get("status") or "").lower() == "misconception":
            covers_map[pid] = {}
            continue
        fp = fire_ancestor_footprint(pid, chains, max_depth=max_depth, gamma=gamma)
        hits = {
            anc: round(w, 4)
            for anc, (w, _d) in fp.items()
            if anc in coverable and anc != pid and w >= min_credit
        }
        covers_map[pid] = hits

    selected_ids: list[str] = []
    selected_set: set[str] = set()
    knocked_out_by: dict[str, tuple[str, float]] = {}
    remaining_coverable = set(coverable)

    # Greedy set-cover selecting high-leverage composites covering >= min_knockouts due prereqs.
    while True:
        best_pid: str | None = None
        best_hits: list[str] = []
        best_key: tuple[int, float, int] = (0, 0.0, 0)

        for pid in due_ids:
            if pid in selected_set or pid in knocked_out_by:
                continue
            active_hits = [anc for anc in covers_map.get(pid, {}) if anc in remaining_coverable]
            if len(active_hits) < min_knockouts:
                continue
            retr = item_by_id[pid].get("retrievability")
            urgency = 1.0 - float(retr) if isinstance(retr, (int, float)) else 0.5
            key = (len(active_hits), urgency, -order_index[pid])
            if key > best_key:
                best_key = key
                best_pid = pid
                best_hits = sorted(active_hits)

        if best_pid is None:
            break

        selected_ids.append(best_pid)
        selected_set.add(best_pid)
        remaining_coverable.discard(best_pid)
        for anc in best_hits:
            knocked_out_by[anc] = (best_pid, covers_map[best_pid][anc])
            remaining_coverable.discard(anc)

    # Retain all remaining non-knocked-out due items in their original urgency order.
    for pid in due_ids:
        if pid not in selected_set and pid not in knocked_out_by:
            selected_ids.append(pid)
            selected_set.add(pid)

    # Keep misconceptions first, then compressed composites / urgency order.
    def _final_sort(pid: str) -> tuple[int, int, int]:
        st = str(item_by_id[pid].get("status") or "").lower()
        knocks = [anc for anc, (by_id, _) in knocked_out_by.items() if by_id == pid]
        return (
            0 if st == "misconception" else (1 if knocks else 2),
            -len(knocks),
            order_index[pid],
        )

    selected_ids.sort(key=_final_sort)

    selected_out: list[dict[str, Any]] = []
    for pid in selected_ids:
        entry = dict(item_by_id[pid])
        knocks = sorted(anc for anc, (by_id, _) in knocked_out_by.items() if by_id == pid)
        if knocks:
            entry["knocksOut"] = knocks
            entry["knocksOutCount"] = len(knocks)
        selected_out.append(entry)

    knocked_out_list: list[dict[str, Any]] = []
    for anc, (by_id, credit_w) in sorted(knocked_out_by.items(), key=lambda kv: order_index[kv[0]]):
        anc_item = item_by_id[anc]
        knocked_out_list.append(
            {
                "id": anc,
                "title": anc_item.get("title", anc),
                "knockedOutBy": by_id,
                "encompassWeight": credit_w,
            }
        )

    orig_n = len(due_items)
    ko_n = len(knocked_out_list)
    savings = round((ko_n / orig_n) * 100.0, 1) if orig_n > 0 else 0.0
    summary = (
        f"Knocked out {ko_n} prerequisite review(s) ({orig_n} -> {len(selected_out)}, -{savings}%)."
        if ko_n > 0
        else f"No multi-prerequisite knockouts available ({orig_n} due)."
    )
    return {
        "selected": selected_out,
        "knockedOut": knocked_out_list,
        "originalCount": orig_n,
        "compressedCount": len(selected_out),
        "knockedOutCount": ko_n,
        "savingsPct": savings,
        "summary": summary,
    }


# ---------------------------------------------------------------------------
# Calm Backlog Recovery (Zero-Guilt Workload Smoother)
# ---------------------------------------------------------------------------


def recover_backlog(
    props: list[dict[str, Any]],
    chains: list[dict[str, Any]],
    *,
    daily_cap: int = 15,
    days: int = 6,
    today: str | None = None,
    now: datetime | None = None,
    parameters: Sequence[float] | None = None,
) -> dict[str, Any]:
    """Smooth an overdue backlog across 5-7 calm days capped at `<= daily_cap` (default 15),
    ranked by `retrievability_at_risk * (1 + blast_radius)` with FIRe compression applied.
    """
    cap = max(1, int(daily_cap))
    target_days = max(5, min(7, int(days)))
    if today and str(today).strip():
        today_d = date_cls.fromisoformat(str(today).strip()[:10])
        ref_now = datetime(today_d.year, today_d.month, today_d.day, 12, 0, 0, tzinfo=timezone.utc)
    else:
        ref_now = _parse_utc_dt(now) if now is not None else datetime.now(timezone.utc)
        assert ref_now is not None
        today_d = ref_now.date()
    today_str = today_d.isoformat()

    params = _validate_parameters(parameters)
    desc_map = _build_outgoing_descendants_map(chains)

    due_raw: list[dict[str, Any]] = []
    for p in props:
        pid = str(p.get("id") or "").strip()
        if not pid:
            continue
        status = str(p.get("status") or "planned").lower()
        due_date = p.get("nextReview")
        is_due = status in ("misconception", "shaky") or (isinstance(due_date, str) and due_date <= today_str)
        if not is_due:
            continue

        if isinstance(p.get("fsrs"), dict):
            r_val = card_retrievability(p["fsrs"], ref_now, parameters=params)
        elif status == "misconception":
            r_val = 0.15
        elif status == "shaky":
            r_val = 0.35
        else:
            r_val = 0.50

        r_at_risk = max(0.05, 1.0 - r_val)
        blast = len(desc_map.get(pid, set()))
        sev = 1.35 if status == "misconception" else (1.10 if status == "shaky" else 1.0)
        priority = round(sev * r_at_risk * (1.0 + blast), 4)

        due_raw.append(
            {
                "id": pid,
                "title": str(p.get("title") or pid),
                "subject": str(p.get("subject") or "General"),
                "chapter": str(p.get("chapter") or "General"),
                "status": status,
                "nextReview": due_date,
                "retrievability": round(r_val, 3),
                "retrievabilityAtRisk": round(r_at_risk, 3),
                "blastRadius": blast,
                "priorityScore": priority,
            }
        )

    due_raw.sort(
        key=lambda x: (
            -x["priorityScore"],
            0 if x["status"] == "misconception" else 1,
            x["retrievability"],
            x["id"],
        )
    )

    comp = compress_due_queue(due_raw, chains)
    active_queue = comp["selected"]
    # Re-sort compressed queue by priorityScore so Day 0 gets highest ROI.
    active_queue.sort(
        key=lambda x: (
            -float(x.get("priorityScore") or 0.0),
            0 if x.get("status") == "misconception" else 1,
            str(x.get("id") or ""),
        )
    )

    total_due = len(due_raw)
    total_active = len(active_queue)
    needs_recovery = total_active > cap

    if total_active == 0:
        return {
            "today": today_str,
            "dailyCap": cap,
            "targetDays": target_days,
            "recoveryDays": 0,
            "totalDue": 0,
            "compressedDue": 0,
            "knockedOutCount": 0,
            "needsRecovery": False,
            "schedule": [],
            "rescheduleMap": {},
            "summary": "Zero overdue cards — no backlog recovery needed.",
        }

    if total_active <= cap:
        day_slots = [active_queue]
    else:
        # Spread evenly across 5..7 days (expanding only if total_active > cap * target_days).
        num_days = max(target_days, math.ceil(total_active / cap))
        # Always give Day 0 up to min(cap, max(1, ceil(total_active / num_days))) highest-priority items,
        # and smooth the remainder evenly across days 1..num_days-1 never exceeding cap.
        day_slots = [[] for _ in range(num_days)]
        day0_size = min(cap, max(1, math.ceil(total_active / num_days)))
        # Fill Day 0 up to cap if backlog is large, or balanced per-day share for smooth 5-7d recovery.
        day_slots[0] = active_queue[:day0_size]
        tail = active_queue[day0_size:]
        rem_days = max(1, num_days - 1)
        idx = 0
        for item in tail:
            # Fill earliest day that keeps load balanced and under cap.
            target_d = 1 + (idx % rem_days)
            while len(day_slots[target_d]) >= cap:
                target_d += 1
                if target_d >= len(day_slots):
                    day_slots.append([])
            day_slots[target_d].append(item)
            idx += 1
        day_slots = [slot for slot in day_slots if slot]

    schedule: list[dict[str, Any]] = []
    reschedule_map: dict[str, str] = {}
    for offset, slot in enumerate(day_slots):
        d_str = (today_d + timedelta(days=offset)).isoformat()
        if offset > 0:
            for item in slot:
                reschedule_map[item["id"]] = d_str
        schedule.append(
            {
                "dayOffset": offset,
                "date": d_str,
                "count": len(slot),
                "items": slot,
            }
        )

    summary = (
        f"Calm catch-up: {total_due} due -> {total_active} after FIRe compression, "
        f"smoothed across {len(schedule)} day(s) (<= {cap}/day)."
        if needs_recovery
        else f"Within daily cap ({total_active} <= {cap} reviews today)."
    )
    return {
        "today": today_str,
        "dailyCap": cap,
        "targetDays": target_days,
        "recoveryDays": len(schedule),
        "totalDue": total_due,
        "compressedDue": total_active,
        "knockedOutCount": comp["knockedOutCount"],
        "knockedOut": comp["knockedOut"],
        "needsRecovery": needs_recovery,
        "schedule": schedule,
        "rescheduleMap": reschedule_map,
        "summary": summary,
    }


# ---------------------------------------------------------------------------
# Retention Forecast, Leech Triage & Per-Type Sibling Burying
# ---------------------------------------------------------------------------


def forecast_retention(
    props: list[dict[str, Any]],
    *,
    exam_date: str | None = None,
    horizon_days: int = 7,
    today: str | None = None,
    now: datetime | None = None,
    parameters: Sequence[float] | None = None,
    revlog_rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Forecast 7-day review load, projected chapter retrievability `R(t_target)`, and calibration."""
    if today and str(today).strip():
        today_d = date_cls.fromisoformat(str(today).strip()[:10])
        ref_now = datetime(today_d.year, today_d.month, today_d.day, 12, 0, 0, tzinfo=timezone.utc)
    else:
        ref_now = _parse_utc_dt(now) if now is not None else datetime.now(timezone.utc)
        assert ref_now is not None
        today_d = ref_now.date()
    today_str = today_d.isoformat()

    h_days = max(1, int(horizon_days))
    if exam_date and str(exam_date).strip():
        target_d = date_cls.fromisoformat(str(exam_date).strip()[:10])
    else:
        target_d = today_d + timedelta(days=h_days)
    target_dt = datetime(target_d.year, target_d.month, target_d.day, 12, 0, 0, tzinfo=timezone.utc)
    days_to_target = (target_d - today_d).days

    params = _validate_parameters(parameters)

    # 7-day daily review load histogram
    daily_counts: dict[str, int] = {(today_d + timedelta(days=i)).isoformat(): 0 for i in range(h_days)}
    chapter_r: dict[str, list[float]] = {}
    all_r: list[float] = []
    at_risk: list[dict[str, Any]] = []

    for p in props:
        pid = str(p.get("id") or "").strip()
        if not pid:
            continue
        status = str(p.get("status") or "planned").lower()
        chapter = str(p.get("chapter") or "General").strip() or "General"
        due_str = str(p.get("nextReview") or "")[:10]

        if status in ("misconception", "shaky") or (due_str and due_str <= today_str):
            daily_counts[today_str] = daily_counts.get(today_str, 0) + 1
        elif due_str in daily_counts:
            daily_counts[due_str] += 1

        if status == "planned" and not isinstance(p.get("fsrs"), dict):
            continue

        if isinstance(p.get("fsrs"), dict) and isinstance(p["fsrs"].get("stability"), (int, float)):
            proj_r = card_retrievability(p["fsrs"], target_dt, parameters=params, fractional_days=True)
        elif status == "misconception":
            proj_r = 0.15
        elif status == "shaky":
            proj_r = 0.35
        else:
            proj_r = 0.85

        proj_r_round = round(proj_r, 4)
        all_r.append(proj_r)
        chapter_r.setdefault(chapter, []).append(proj_r)
        if proj_r < 0.70:
            at_risk.append(
                {
                    "id": pid,
                    "title": str(p.get("title") or pid),
                    "chapter": chapter,
                    "status": status,
                    "projectedRetrievability": proj_r_round,
                    "nextReview": due_str or None,
                }
            )

    at_risk.sort(key=lambda x: (x["projectedRetrievability"], x["id"]))
    chapters_out = [
        {
            "chapter": ch,
            "trackedNodes": len(vals),
            "meanRetrievability": round(sum(vals) / len(vals), 4),
            "readinessPct": round((sum(vals) / len(vals)) * 100.0, 1),
        }
        for ch, vals in chapter_r.items()
    ]
    chapters_out.sort(key=lambda x: (x["meanRetrievability"], x["chapter"].lower()))

    daily_load = [{"date": d, "dueCount": daily_counts[d]} for d in sorted(daily_counts.keys())]
    mean_r = round(sum(all_r) / len(all_r), 4) if all_r else None

    out: dict[str, Any] = {
        "today": today_str,
        "targetDate": target_d.isoformat(),
        "daysToTarget": days_to_target,
        "trackedNodes": len(all_r),
        "meanRetrievability": mean_r,
        "readinessPct": round(mean_r * 100.0, 1) if mean_r is not None else None,
        "dailyLoad": daily_load,
        "chapters": chapters_out,
        "atRiskCount": len(at_risk),
        "atRiskProps": at_risk[:20],
    }
    if revlog_rows is not None:
        out["calibration"] = compute_calibration(revlog_rows)
    return out


def detect_leeches(
    props: list[dict[str, Any]],
    revlog_rows: list[dict[str, Any]] | None = None,
    *,
    lapse_threshold: int = 4,
    difficulty_threshold: float = 8.5,
) -> list[dict[str, Any]]:
    """Detect chronic leech propositions (`lapses >= 4` or `difficulty >= 8.5`) for refactoring."""
    revlog_lapses: dict[str, int] = {}
    for row in revlog_rows or []:
        if not isinstance(row, dict):
            continue
        pid = str(row.get("propId") or "").strip()
        r = row.get("rating")
        is_again = (r == 1) or (isinstance(r, str) and r.strip().lower() == "again")
        if pid and is_again:
            revlog_lapses[pid] = revlog_lapses.get(pid, 0) + 1

    leeches: list[dict[str, Any]] = []
    for p in props:
        pid = str(p.get("id") or "").strip()
        if not pid:
            continue
        stored_lapses = int(p.get("lapses") or 0)
        lapses = max(stored_lapses, revlog_lapses.get(pid, 0))
        fsrs_d = p.get("fsrs") if isinstance(p.get("fsrs"), dict) else {}
        diff_raw = fsrs_d.get("difficulty")
        difficulty = float(diff_raw) if isinstance(diff_raw, (int, float)) else 0.0

        reasons: list[str] = []
        if lapses >= lapse_threshold:
            reasons.append(f"lapses={lapses} (>={lapse_threshold})")
        if difficulty >= difficulty_threshold:
            reasons.append(f"difficulty={difficulty:.2f} (>={difficulty_threshold})")
        if not reasons:
            continue

        leeches.append(
            {
                "id": pid,
                "title": str(p.get("title") or pid),
                "subject": str(p.get("subject") or "General"),
                "chapter": str(p.get("chapter") or "General"),
                "status": str(p.get("status") or "planned"),
                "lapses": lapses,
                "difficulty": round(difficulty, 2),
                "reasons": reasons,
                "recommendation": (
                    "Rewrite or split this proposition into 2 atomic concepts, audit prerequisites "
                    "with `graph_intel.py --path-to`, or run a 3-turn Protégé Teach-Back."
                ),
            }
        )

    leeches.sort(key=lambda x: (-x["lapses"], -x["difficulty"], x["id"]))
    return leeches


def apply_sibling_burying(
    items: list[dict[str, Any]],
    revlog_rows: list[dict[str, Any]],
    *,
    now: datetime | None = None,
    bury_hours: float = 24.0,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Per-type sibling burying: bury same-day text/cloze sibling cues for a proposition
    reviewed within `bury_hours`, while allowing grouped spatial `occlusion` sweeps.
    """
    ref_now = _parse_utc_dt(now) if now is not None else datetime.now(timezone.utc)
    assert ref_now is not None
    cutoff = ref_now - timedelta(hours=max(1.0, float(bury_hours)))

    recently_reviewed_text_props: set[str] = set()
    for row in revlog_rows:
        if not isinstance(row, dict):
            continue
        ts = _parse_utc_dt(row.get("ts"))
        if ts is None or ts < cutoff:
            continue
        pid = str(row.get("propId") or "").strip()
        cue_kind = str(row.get("cueKind") or "recall").strip().lower()
        if pid and cue_kind != "occlusion":
            recently_reviewed_text_props.add(pid)

    kept: list[dict[str, Any]] = []
    buried: list[dict[str, Any]] = []
    seen_text_props: set[str] = set()

    for it in items:
        pid = str(it.get("propId") or it.get("id") or "").strip()
        cue_kind = str(it.get("cueKind") or it.get("kind") or "recall").strip().lower()
        # Spatial Image Occlusion cards are intentionally exempt from sibling burying.
        if cue_kind == "occlusion":
            kept.append(it)
            continue
        if pid and (pid in recently_reviewed_text_props or pid in seen_text_props):
            buried.append({**it, "buriedReason": "sibling_text_24h"})
        else:
            if pid:
                seen_text_props.add(pid)
            kept.append(it)

    return kept, buried


# ---------------------------------------------------------------------------
# Zero-PyTorch SciPy L-BFGS-B Personal FSRS-6 Parameter Optimizer
# ---------------------------------------------------------------------------


def _eval_fsrs_logloss(
    params: Sequence[float],
    histories: dict[str, list[tuple[datetime, int, bool]]],
    *,
    use_val: bool,
) -> tuple[float, int]:
    """Replay chronological card histories under `params` and compute mean binary log-loss."""
    eps = 1e-6
    total_loss = 0.0
    total_weight = 0.0
    count = 0

    for events in histories.values():
        s: float | None = None
        d: float | None = None
        last_dt: datetime | None = None
        n_ev = len(events)

        for idx, (ev_dt, rating_int, is_val) in enumerate(events):
            if s is not None and d is not None and last_dt is not None:
                elapsed = max(0.0, (ev_dt - last_dt).total_seconds() / 86400.0)
                r_pred = forgetting_curve(elapsed, s, decay=params[20])
                r_clip = min(1.0 - eps, max(eps, r_pred))
                y = 1.0 if rating_int >= 2 else 0.0
                if is_val == use_val:
                    # Mild recency weighting on train split; unweighted exact OOS loss on val split.
                    w = 1.0 if use_val else (0.5 + 0.5 * ((idx + 1) / max(1, n_ev)))
                    bce = -(y * math.log(r_clip) + (1.0 - y) * math.log(1.0 - r_clip))
                    total_loss += w * bce
                    total_weight += w
                    count += 1

                if elapsed < 1.0:
                    s = _short_term_stability(params, s, rating_int)
                else:
                    s = _next_stability(params, d, s, r_pred, rating_int)
                d = _next_difficulty(params, d, rating_int)
            else:
                s = _initial_stability(params, rating_int)
                d = _initial_difficulty(params, rating_int, clamp=True)
            last_dt = ev_dt

    if count == 0 or total_weight <= 0.0:
        return math.inf, 0
    return total_loss / total_weight, count


def optimize_fsrs_params(
    revlog_rows: list[dict[str, Any]],
    *,
    min_reviews: int = 100,
    default_params: Sequence[float] = DEFAULT_FSRS_PARAMETERS,
    prior_strength: float = 0.015,
    max_iter: int = 50,
) -> dict[str, Any]:
    """Fit personal 21-weight FSRS-6 parameters via SciPy L-BFGS-B (zero PyTorch!).

    Only accepts fitted weights when `>= min_reviews` (default 100) valid revlog events
    exist AND chronological 80/20 out-of-sample Log-Loss strictly improves over defaults.
    """
    base_params = _validate_parameters(default_params)
    parsed: list[tuple[datetime, str, int]] = []
    for row in revlog_rows:
        if not isinstance(row, dict):
            continue
        pid = str(row.get("propId") or "").strip()
        dt = _parse_utc_dt(row.get("ts"))
        r_raw = row.get("rating")
        if isinstance(r_raw, int) and r_raw in (1, 2, 3, 4):
            r_int = r_raw
        elif isinstance(r_raw, str) and r_raw.strip().lower() in RATING_TO_INT:
            r_int = RATING_TO_INT[r_raw.strip().lower()]
        else:
            continue
        if pid and dt is not None:
            parsed.append((dt, pid, r_int))

    parsed.sort(key=lambda item: (item[0], item[1]))
    n_total = len(parsed)
    if n_total < min_reviews:
        return {
            "optimized": False,
            "accepted": False,
            "reviewCount": n_total,
            "minReviews": min_reviews,
            "parameters": list(base_params),
            "reason": f"Need at least {min_reviews} review events (found {n_total}).",
        }

    # Chronological 80/20 time split to guarantee zero leakage in OOS validation.
    split_idx = max(1, int(n_total * 0.80))
    histories: dict[str, list[tuple[datetime, int, bool]]] = {}
    for idx, (ev_dt, pid, r_int) in enumerate(parsed):
        histories.setdefault(pid, []).append((ev_dt, r_int, idx >= split_idx))

    default_val_loss, val_n = _eval_fsrs_logloss(base_params, histories, use_val=True)
    default_train_loss, train_n = _eval_fsrs_logloss(base_params, histories, use_val=False)
    if train_n < 10 or val_n < 5 or not math.isfinite(default_val_loss):
        return {
            "optimized": False,
            "accepted": False,
            "reviewCount": n_total,
            "trainCount": train_n,
            "valCount": val_n,
            "parameters": list(base_params),
            "reason": "Not enough repeat review transitions across train/validation split.",
        }

    try:
        from scipy.optimize import minimize  # type: ignore[import-not-found]
    except ImportError:
        return {
            "optimized": False,
            "accepted": False,
            "reviewCount": n_total,
            "trainCount": train_n,
            "valCount": val_n,
            "defaultOosLogLoss": round(default_val_loss, 5),
            "parameters": list(base_params),
            "reason": "scipy is not installed; keeping default FSRS-6 parameters.",
        }

    scales = [max(0.25, abs(w0)) for w0 in base_params]
    bounds = list(zip(LOWER_BOUNDS_PARAMETERS, UPPER_BOUNDS_PARAMETERS))

    def _objective(vec: Any) -> float:
        p_list = [float(x) for x in vec]
        loss, _ = _eval_fsrs_logloss(p_list, histories, use_val=False)
        if not math.isfinite(loss):
            return 1e6
        reg = prior_strength * sum(((p_list[i] - base_params[i]) / scales[i]) ** 2 for i in range(len(base_params)))
        return float(loss + reg)

    res = minimize(
        _objective,
        x0=list(base_params),
        method="L-BFGS-B",
        bounds=bounds,
        options={"maxiter": int(max_iter)},
    )
    cand_params = _validate_parameters([round(float(x), 6) for x in res.x])
    opt_val_loss, _ = _eval_fsrs_logloss(cand_params, histories, use_val=True)
    opt_train_loss, _ = _eval_fsrs_logloss(cand_params, histories, use_val=False)

    improvement = default_val_loss - opt_val_loss
    accepted = bool(math.isfinite(opt_val_loss) and improvement > 1e-4)

    return {
        "optimized": True,
        "accepted": accepted,
        "reviewCount": n_total,
        "trainCount": train_n,
        "valCount": val_n,
        "defaultTrainLogLoss": round(default_train_loss, 5),
        "optimizedTrainLogLoss": round(opt_train_loss, 5),
        "defaultOosLogLoss": round(default_val_loss, 5),
        "optimizedOosLogLoss": round(opt_val_loss, 5),
        "improvement": round(improvement, 5),
        "parameters": list(cand_params if accepted else base_params),
        "candidateParameters": list(cand_params),
        "reason": (
            f"Accepted personal FSRS-6 parameters (OOS Log-Loss improved by {improvement:.5f})."
            if accepted
            else f"Kept defaults (OOS Log-Loss did not improve: {default_val_loss:.5f} -> {opt_val_loss:.5f})."
        ),
    }
