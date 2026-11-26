"""Syntopik v3.1 — Offline-First Multi-Device CRDT Causal Delta Sync.

Pure Python 3.12+ standard library only (zero external dependencies).
Exports and merges portable, tamper-evident `syntopik-delta-v1` JSON snapshots
across offline devices using mathematically convergent CRDT join-semilattice
rules:
  - Append-only G-Set ledgers (`revlog.jsonl`, `mistakes.jsonl`,
    `viva-sessions.jsonl`, `forks.jsonl`, `arenas.jsonl`, `derivations.jsonl`):
    union deduplicated by stable content/event key, sorted chronologically.
  - `evidence-ledger.jsonl`: union deduplicated by `(propId, tier, source, ts)`
    and deterministically re-chained with valid SHA-256 `prevHash`/`entryHash`
    verified via `syntopik.evidence.verify_ledger_chain`.
  - `propositions.jsonl`: merge by `id` using LWW-with-max-progress tie-breaker
    `(reps, lastReviewed, updatedAt)` plus set-union of `tags`, `cues`
    (deduped by prompt), `boundaryConditions`, `antiPatterns`, and notes.
  - `chains.jsonl`: union deduplicated by normalized `(premises, conclusion, relation)`.
  - `chapters/*-tracker.json`: monotonic join-semilattice merge — topic status
    lattice `unseen < learning < practiced < mastered` takes max, question
    status lattice `unseen < attempted < retest < solved (< mastered)` takes
    max, milestones take logical OR, sources/slices union by key.
  - `learner-profile.json`: merges `recentEvents` (deduped FIFO 20 sorted by
    `ts`), merges per-domain Elo weighted by per-device `n` (never losing event
    counts while preserving exact idempotence and commutativity), preserves
    `calibration.completed = a or b`, and recompiles `compiledPolicy`.

Security guarantees:
  - `MAX_DELTA_BYTES = 10 * 1024 * 1024` (10 MiB hard ceiling)
  - Mandatory `contentSha256` verification (fail-closed if missing or tampered)
  - Strict tracker slug validation `^[a-z0-9][a-z0-9_-]{0,63}$` (zero path traversal)
  - Atomic writes via `syntopik.storage` + `workspace_lock`, restricted strictly
    to `knowledge-graph/` and `chapters/`.
"""

from __future__ import annotations

import copy
import hashlib
import hmac
import json
import math
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from syntopik.evidence import (
    GENESIS_HASH,
    LEDGER_FILENAME,
    TIER_LABELS,
    verify_ledger_chain,
)
from syntopik.learner_model import (
    MAX_RECENT_EVENTS,
    PROFILE_FILENAME,
    _compact_profile_for_disk,
    _compute_failure_dna,
    _compute_metacognition,
    _elo_to_band,
    _merge_defaults,
    _prior_weight,
    classify_affective_state,
    compile_teaching_policy,
    load_profile,
    resolve_calibrated_hyperparams,
)
from syntopik.paths import resolve_chapters_dir, resolve_kg_dir, resolve_workspace
from syntopik.schemas import normalize_chain, now_iso
from syntopik.storage import (
    load_json,
    load_jsonl,
    save_json_atomic,
    save_jsonl_atomic,
    workspace_lock,
    write_text_atomic,
)

__all__ = [
    "SCHEMA_VERSION",
    "DELTA_FORMAT",
    "MAX_DELTA_BYTES",
    "TRACKER_SLUG_RE",
    "SYNC_STATE_FILENAME",
    "TOPIC_STATUS_RANK",
    "QUESTION_STATUS_RANK",
    "SLICE_STAGE_RANK",
    "get_or_create_device_id",
    "export_sync_delta",
    "verify_sync_delta",
    "merge_sync_delta",
    "get_sync_status",
]

SCHEMA_VERSION = 1
DELTA_FORMAT = "syntopik-delta-v1"
MAX_DELTA_BYTES = 10 * 1024 * 1024  # 10 MiB hard cap
TRACKER_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
SYNC_STATE_FILENAME = "sync-state.json"
TRACKER_SUFFIX = "-tracker.json"

# Monotonic join-semilattice ranks supporting both v3.1 canonical & chapter_tracker vocabularies
TOPIC_STATUS_RANK: dict[str, int] = {
    "unseen": 0,
    "pending": 0,
    "learning": 10,
    "probed": 10,
    "in_progress": 15,
    "practiced": 20,
    "mastered": 30,
}

QUESTION_STATUS_RANK: dict[str, int] = {
    "unseen": 0,
    "attempted": 10,
    "in_progress": 10,
    "retest": 20,
    "skipped": 25,
    "solved": 30,
    "mastered": 40,
}

SLICE_STAGE_RANK: dict[str, int] = {
    "queued": 0,
    "extracted": 10,
    "clozed": 20,
    "crystallized": 30,
}

_REQUIRED_PAYLOAD_KEYS: tuple[str, ...] = (
    "propositions",
    "chains",
    "revlog",
    "mistakes",
    "evidenceLedger",
    "vivaSessions",
    "forks",
    "arenas",
    "derivations",
    "trackers",
    "learnerProfile",
)

_PAYLOAD_LIST_KEYS: tuple[str, ...] = (
    "propositions",
    "chains",
    "revlog",
    "mistakes",
    "evidenceLedger",
    "vivaSessions",
    "forks",
    "arenas",
    "derivations",
)


# ---------------------------------------------------------------------------
# Canonicalization, hashing & path safety
# ---------------------------------------------------------------------------


_NON_FINITE_STRINGS: frozenset[str] = frozenset(
    {"nan", "+nan", "-nan", "inf", "+inf", "-inf", "infinity", "+infinity", "-infinity"}
)

_NUMERIC_FIELD_NAMES: frozenset[str] = frozenset(
    {
        "elo",
        "n",
        "bktMastery",
        "confidence",
        "learnerConfidence",
        "stability",
        "stabilityDays",
        "difficulty",
        "reps",
        "lapses",
        "encompassWeight",
        "encompass_weight",
        "totalEvents",
        "priorWeight",
        "pfAttempts",
        "pfSurvivalRate",
        "analogyFormalismRatio",
        "latencyEwma",
        "pacingMultiplier",
        "sessionTargetMinutes",
        "baselineLatencyMs",
        "eloKBase",
        "erNoviceCutoff",
        "erChallengeCutoff",
        "fatigueMultiplier",
        "oosImprovement",
        "oosBaselineLoss",
        "oosCalibratedLoss",
        "trainEvents",
        "testEvents",
        "durationMs",
        "hintsUsed",
        "elapsedDays",
        "intervalDays",
        "ratingInt",
        "winCount",
        "attempts",
        "targetLatencyMs",
        "woodpeckerTargetMs",
        "killRate",
        "score",
        "composite",
        "completions",
        "order",
        "stepCounter",
        "lastStep",
        "totalPages",
        "step",
        "lamportClock",
        "tier",
        "seq",
        "probes",
    }
)

_STRICT_NUMERIC_FIELDS: frozenset[str] = frozenset(
    {
        "elo",
        "n",
        "bktMastery",
        "stability",
        "stabilityDays",
        "difficulty",
        "reps",
        "lapses",
        "encompassWeight",
        "encompass_weight",
        "totalEvents",
        "priorWeight",
        "pfAttempts",
        "pfSurvivalRate",
        "analogyFormalismRatio",
        "latencyEwma",
        "pacingMultiplier",
        "sessionTargetMinutes",
        "baselineLatencyMs",
        "eloKBase",
        "erNoviceCutoff",
        "erChallengeCutoff",
        "fatigueMultiplier",
        "oosImprovement",
        "oosBaselineLoss",
        "oosCalibratedLoss",
        "durationMs",
        "hintsUsed",
        "elapsedDays",
        "intervalDays",
        "winCount",
        "attempts",
        "targetLatencyMs",
        "woodpeckerTargetMs",
        "killRate",
        "completions",
        "stepCounter",
        "lamportClock",
    }
)

ELO_MIN = 800.0
ELO_MAX = 2600.0
FSRS_STABILITY_MIN = 0.01
FSRS_STABILITY_MAX = 36500.0
FSRS_DIFFICULTY_MIN = 1.0
FSRS_DIFFICULTY_MAX = 10.0


def _reject_non_finite(token: str) -> Any:
    raise ValueError(f"Non-finite JSON constant rejected: {token!r}")


def _safe_finite_float(
    val: Any,
    default: float,
    *,
    lo: float | None = None,
    hi: float | None = None,
) -> float:
    if isinstance(val, bool) or val is None:
        out = float(default)
    else:
        try:
            out = float(val)
        except (TypeError, ValueError):
            out = float(default)
        if not math.isfinite(out):
            out = float(default)
    if lo is not None and out < lo:
        out = lo
    if hi is not None and out > hi:
        out = hi
    return out


def _safe_nonneg_int(val: Any, default: int = 0) -> int:
    if isinstance(val, bool) or val is None:
        return max(0, int(default))
    try:
        fv = float(val)
    except (TypeError, ValueError):
        return max(0, int(default))
    if not math.isfinite(fv):
        return max(0, int(default))
    return max(0, int(fv))


def _find_non_finite_issues(
    obj: Any,
    path: str = "delta",
    field_name: str | None = None,
    errors: list[str] | None = None,
) -> list[str]:
    """Recursively detect any NaN/Inf float or non-finite numeric string in numeric fields."""
    if errors is None:
        errors = []
    if len(errors) >= 25:
        return errors

    if isinstance(obj, float):
        if not math.isfinite(obj):
            errors.append(f"Non-finite float rejected at {path}: {obj!r}.")
        return errors

    if isinstance(obj, str):
        if field_name in _NUMERIC_FIELD_NAMES:
            stripped = obj.strip()
            low = stripped.lower()
            if low in _NON_FINITE_STRINGS:
                errors.append(f"Non-finite numeric string rejected at {path}: {obj!r}.")
            elif stripped:
                try:
                    fv = float(stripped)
                except ValueError:
                    if field_name in _STRICT_NUMERIC_FIELDS:
                        errors.append(f"Non-numeric value rejected in numeric field {path}: {obj!r}.")
                else:
                    if not math.isfinite(fv):
                        errors.append(f"Non-finite numeric string rejected at {path}: {obj!r}.")
        return errors

    if isinstance(obj, dict):
        for k, v in obj.items():
            if len(errors) >= 25:
                break
            ks = str(k)
            _find_non_finite_issues(v, f"{path}.{ks}", ks, errors)
        return errors

    if isinstance(obj, list):
        item_field = "durationMs" if field_name == "latencyMsHistory" else None
        for idx, item in enumerate(obj):
            if len(errors) >= 25:
                break
            _find_non_finite_issues(item, f"{path}[{idx}]", item_field, errors)
        return errors

    return errors


def _canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _canonical_sha256(obj: Any) -> str:
    return hashlib.sha256(_canonical_json(obj).encode("utf-8")).hexdigest()


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
        if len(raw) == 10 and re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
            try:
                return datetime.fromisoformat(raw + "T00:00:00+00:00")
            except ValueError:
                return None
        return None


def _ts_sort_key(ts_raw: Any) -> str:
    dt = _parse_iso_dt(ts_raw)
    if dt is not None:
        return dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    return str(ts_raw or "").strip()


def _extract_row_ts(row: dict[str, Any]) -> str:
    for field in ("ts", "timestamp", "updatedAt", "lastReviewed", "lastPlayedAt", "createdAt", "date"):
        val = row.get(field)
        if isinstance(val, str) and val.strip():
            return val.strip()
    fsrs = row.get("fsrs")
    if isinstance(fsrs, dict):
        lr = fsrs.get("last_review")
        if isinstance(lr, str) and lr.strip():
            return lr.strip()
    return ""


def _resolve_ws_kg_ch(workspace: Path | str | None) -> tuple[Path, Path, Path]:
    ws = resolve_workspace(workspace)
    return ws, resolve_kg_dir(ws), resolve_chapters_dir(ws)


def _resolve_kg_only(kg_dir_or_ws: Path | str) -> Path:
    p = Path(kg_dir_or_ws).expanduser().resolve()
    if p.name == "knowledge-graph" and not (p / "knowledge-graph").is_dir():
        return p
    if (p / "knowledge-graph").is_dir():
        return (p / "knowledge-graph").resolve()
    if any(
        (p / fname).is_file()
        for fname in ("propositions.jsonl", SYNC_STATE_FILENAME, PROFILE_FILENAME, LEDGER_FILENAME)
    ):
        return p
    return p


def _assert_safe_child(base_dir: Path, filename: str) -> Path:
    """Resolve `base_dir / filename` and fail-closed if it escapes `base_dir`."""
    if not filename or "/" in filename or "\\" in filename or "\x00" in filename or ".." in filename:
        raise ValueError(f"Unsafe filename rejected: {filename!r}")
    base_resolved = base_dir.expanduser().resolve()
    candidate = (base_resolved / filename).resolve()
    if candidate.parent != base_resolved:
        raise ValueError(f"Path traversal blocked for {filename!r}")
    return candidate


def _validate_tracker_slug(slug: Any) -> str:
    if not isinstance(slug, str) or not TRACKER_SLUG_RE.fullmatch(slug):
        raise ValueError(
            f"Invalid tracker slug {slug!r}; must match {TRACKER_SLUG_RE.pattern}"
        )
    return slug


def _compute_delta_sha256(envelope: dict[str, Any]) -> str:
    body = {k: v for k, v in envelope.items() if k != "contentSha256"}
    return _canonical_sha256(body)


def _load_sync_state(kg_dir: Path) -> dict[str, Any]:
    state_path = _assert_safe_child(kg_dir, SYNC_STATE_FILENAME)
    raw = load_json(state_path, default=None)
    if not isinstance(raw, dict):
        return {}
    return dict(raw)


def _save_sync_state(kg_dir: Path, state: dict[str, Any]) -> Path:
    state_path = _assert_safe_child(kg_dir, SYNC_STATE_FILENAME)
    return save_json_atomic(state_path, state)


# ---------------------------------------------------------------------------
# Public API 1: Device Identity
# ---------------------------------------------------------------------------


def get_or_create_device_id(kg_dir: Path | str) -> str:
    """Return this device's persistent opaque ID from `sync-state.json`, creating it atomically if absent."""
    kdir = _resolve_kg_only(kg_dir)
    state_path = _assert_safe_child(kdir, SYNC_STATE_FILENAME)
    with workspace_lock(state_path):
        state = _load_sync_state(kdir)
        existing = state.get("deviceId")
        if isinstance(existing, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{3,63}", existing):
            return existing
        dev_id = f"dev-{uuid.uuid4().hex[:16]}"
        state.setdefault("schemaVersion", SCHEMA_VERSION)
        state["deviceId"] = dev_id
        state.setdefault("lamportClock", 0)
        state.setdefault("peerClocks", {})
        state.setdefault("mergedDeltaHashes", [])
        state["updatedAt"] = now_iso()
        _save_sync_state(kdir, state)
        return dev_id


# ---------------------------------------------------------------------------
# Per-Collection CRDT Merge Primitives (Commutative, Associative, Idempotent)
# ---------------------------------------------------------------------------


def _merge_notes(note_a: Any, note_b: Any) -> str:
    sa = str(note_a or "").strip()
    sb = str(note_b or "").strip()
    if not sa:
        return sb
    if not sb or sa == sb:
        return sa
    parts_set: set[str] = set()
    for raw in (sa, sb):
        for chunk in raw.split("\n---\n"):
            c = chunk.strip()
            if c:
                parts_set.add(c)
    return "\n---\n".join(sorted(parts_set))


def _dedupe_str_union(*lists: Any) -> list[str]:
    items: set[str] = set()
    for seq in lists:
        if isinstance(seq, list):
            for el in seq:
                s = str(el or "").strip()
                if s:
                    items.add(s)
    return sorted(items)


def _merge_covered_pages(a_pages: Any, b_pages: Any) -> list[Any]:
    ints: set[int] = set()
    strs: set[str] = set()
    for seq in (a_pages, b_pages):
        if not isinstance(seq, list):
            continue
        for v in seq:
            if isinstance(v, int) and not isinstance(v, bool):
                ints.add(v)
            elif isinstance(v, str) and v.strip():
                s = v.strip()
                if s.isdigit():
                    ints.add(int(s))
                else:
                    strs.add(s)
    return [*sorted(ints), *sorted(strs)]


# --- Propositions ---

_PROP_SET_FIELDS: frozenset[str] = frozenset({"tags", "cues", "boundaryConditions", "antiPatterns", "notes"})


def _prop_reps(prop: dict[str, Any]) -> int:
    reps = 0
    if prop.get("reps") is not None and not isinstance(prop.get("reps"), bool):
        reps = max(reps, _safe_nonneg_int(prop.get("reps"), 0))
    fsrs = prop.get("fsrs")
    if isinstance(fsrs, dict):
        freps = fsrs.get("reps")
        if freps is not None and not isinstance(freps, bool):
            reps = max(reps, _safe_nonneg_int(freps, 0))
    return reps


def _sanitize_proposition(prop: dict[str, Any]) -> dict[str, Any]:
    """Return a deep copy of `prop` with numeric mastery/FSRS fields strictly clamped to valid domains."""
    out = copy.deepcopy(prop)
    if "bktMastery" in out and out["bktMastery"] is not None:
        out["bktMastery"] = _safe_finite_float(out["bktMastery"], 0.0, lo=0.0, hi=1.0)
    if "reps" in out and out["reps"] is not None:
        out["reps"] = _safe_nonneg_int(out["reps"], 0)
    if "lapses" in out and out["lapses"] is not None:
        out["lapses"] = _safe_nonneg_int(out["lapses"], 0)
    if "stability" in out and out["stability"] is not None:
        out["stability"] = _safe_finite_float(out["stability"], 1.0, lo=FSRS_STABILITY_MIN, hi=FSRS_STABILITY_MAX)
    if "stabilityDays" in out and out["stabilityDays"] is not None:
        out["stabilityDays"] = _safe_finite_float(out["stabilityDays"], 1.0, lo=FSRS_STABILITY_MIN, hi=FSRS_STABILITY_MAX)
    if "difficulty" in out and out["difficulty"] is not None:
        out["difficulty"] = _safe_finite_float(out["difficulty"], 5.0, lo=FSRS_DIFFICULTY_MIN, hi=FSRS_DIFFICULTY_MAX)
    fsrs = out.get("fsrs")
    if isinstance(fsrs, dict):
        if "stability" in fsrs and fsrs["stability"] is not None:
            fsrs["stability"] = _safe_finite_float(fsrs["stability"], 1.0, lo=FSRS_STABILITY_MIN, hi=FSRS_STABILITY_MAX)
        if "difficulty" in fsrs and fsrs["difficulty"] is not None:
            fsrs["difficulty"] = _safe_finite_float(fsrs["difficulty"], 5.0, lo=FSRS_DIFFICULTY_MIN, hi=FSRS_DIFFICULTY_MAX)
        if "reps" in fsrs and fsrs["reps"] is not None:
            fsrs["reps"] = _safe_nonneg_int(fsrs["reps"], 0)
        if "lapses" in fsrs and fsrs["lapses"] is not None:
            fsrs["lapses"] = _safe_nonneg_int(fsrs["lapses"], 0)
    return out


def _prop_progress_key(prop: dict[str, Any]) -> tuple[int, str, str, str]:
    reps = _prop_reps(prop)
    fsrs = prop.get("fsrs") if isinstance(prop.get("fsrs"), dict) else {}
    last_rev = _ts_sort_key(
        prop.get("lastReviewed") or fsrs.get("last_review") or prop.get("lastTested") or ""
    )
    updated = _ts_sort_key(prop.get("updatedAt") or last_rev)
    scalars = {k: v for k, v in prop.items() if k not in _PROP_SET_FIELDS}
    return (reps, last_rev, updated, _canonical_json(scalars))


def _cue_prompt_key(cue: dict[str, Any]) -> str:
    raw = cue.get("prompt") or cue.get("q") or cue.get("front") or cue.get("id") or ""
    norm = re.sub(r"\s+", " ", str(raw).strip().lower())
    return norm or _canonical_sha256(cue)


def _cue_rank_key(cue: dict[str, Any]) -> tuple[int, str, str]:
    revs = int(cue.get("reviews") or 0) if isinstance(cue.get("reviews"), (int, float)) else 0
    lr = _ts_sort_key(cue.get("lastReviewed") or "")
    return (revs, lr, _canonical_json(cue))


def _merge_cues(cues_a: Any, cues_b: Any) -> list[dict[str, Any]]:
    by_prompt: dict[str, dict[str, Any]] = {}
    for seq in (cues_a, cues_b):
        if not isinstance(seq, list):
            continue
        for c in seq:
            if not isinstance(c, dict):
                continue
            key = _cue_prompt_key(c)
            prev = by_prompt.get(key)
            if prev is None or _cue_rank_key(c) > _cue_rank_key(prev):
                by_prompt[key] = copy.deepcopy(c)
    return [by_prompt[k] for k in sorted(by_prompt)]


def _merge_proposition(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    sa = _sanitize_proposition(a)
    sb = _sanitize_proposition(b)
    if _prop_progress_key(sa) >= _prop_progress_key(sb):
        winner, loser = sa, sb
    else:
        winner, loser = sb, sa

    merged = copy.deepcopy(winner)
    for k, v in loser.items():
        if k in _PROP_SET_FIELDS:
            continue
        if k not in merged or merged[k] is None or merged[k] == "":
            merged[k] = copy.deepcopy(v)

    if "reps" in sa or "reps" in sb:
        merged["reps"] = max(_prop_reps(sa), _prop_reps(sb))

    if "notes" in sa or "notes" in sb:
        merged["notes"] = _merge_notes(sa.get("notes"), sb.get("notes"))

    if "tags" in sa or "tags" in sb:
        merged["tags"] = _dedupe_str_union(sa.get("tags"), sb.get("tags"))

    if "boundaryConditions" in sa or "boundaryConditions" in sb:
        merged["boundaryConditions"] = _dedupe_str_union(
            sa.get("boundaryConditions"), sb.get("boundaryConditions")
        )

    if "antiPatterns" in sa or "antiPatterns" in sb:
        merged["antiPatterns"] = _dedupe_str_union(sa.get("antiPatterns"), sb.get("antiPatterns"))

    if "cues" in sa or "cues" in sb:
        merged["cues"] = _merge_cues(sa.get("cues"), sb.get("cues"))

    return _sanitize_proposition(merged)


def _merge_propositions(
    local_rows: list[dict[str, Any]], incoming_rows: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], int, int]:
    local_map: dict[str, dict[str, Any]] = {}
    for row in local_rows:
        if isinstance(row, dict) and str(row.get("id") or "").strip():
            pid = str(row["id"]).strip()
            srow = _sanitize_proposition(row)
            local_map[pid] = _merge_proposition(local_map[pid], srow) if pid in local_map else srow

    merged_map: dict[str, dict[str, Any]] = copy.deepcopy(local_map)
    added = 0
    updated = 0

    # Fold incoming deduplicated by id first so duplicate incoming entries don't skew counts
    inc_map: dict[str, dict[str, Any]] = {}
    for row in incoming_rows:
        if not isinstance(row, dict):
            continue
        pid = str(row.get("id") or "").strip()
        if not pid:
            continue
        srow = _sanitize_proposition(row)
        inc_map[pid] = _merge_proposition(inc_map[pid], srow) if pid in inc_map else srow

    for pid, inc in inc_map.items():
        if pid not in merged_map:
            merged_map[pid] = copy.deepcopy(inc)
            added += 1
        else:
            before = merged_map[pid]
            after = _merge_proposition(before, inc)
            if _canonical_json(after) != _canonical_json(before):
                merged_map[pid] = after
                updated += 1

    out = [merged_map[pid] for pid in sorted(merged_map)]
    return out, added, updated


# --- Chains ---


def _chain_key(nc: dict[str, Any]) -> tuple[tuple[str, ...], str, str, str]:
    rel = nc["relation"].strip().lower()
    if rel == "isomorphic_to":
        endpoints = tuple(sorted({*nc["premises"], nc["conclusion"]}))
        arch_id = str(nc.get("archetypeId") or "").strip().lower()
        return (endpoints, "", rel, arch_id)
    return (tuple(sorted(nc["premises"])), nc["conclusion"], rel, "")


def _canonicalize_chain(raw: dict[str, Any]) -> dict[str, Any] | None:
    nc = normalize_chain(raw)
    if nc is None:
        return None
    rel = nc["relation"].strip().lower()
    if rel == "isomorphic_to" and len(nc["premises"]) == 1:
        a, b = sorted((nc["premises"][0], nc["conclusion"]))
        nc["premises"] = [a]
        nc["conclusion"] = b
        nc["from"] = a
        nc["to"] = b
        nc["chainKey"] = f"{a}>{b}"
    else:
        nc["premises"] = sorted(nc["premises"])
        nc["chainKey"] = f"{'+'.join(nc['premises'])}>{nc['conclusion']}"

    arch_id = str(nc.get("archetypeId") or raw.get("archetypeId") or "").strip()
    if arch_id:
        nc["archetypeId"] = arch_id
    if "breaksAt" in nc or "breaksAt" in raw:
        raw_breaks = nc.get("breaksAt") if "breaksAt" in nc else raw.get("breaksAt")
        if isinstance(raw_breaks, list):
            nc["breaksAt"] = _dedupe_str_union(raw_breaks)
        elif isinstance(raw_breaks, str) and raw_breaks.strip():
            nc["breaksAt"] = [raw_breaks.strip()]
    created_at = str(nc.get("createdAt") or raw.get("createdAt") or "").strip()
    if created_at:
        nc["createdAt"] = created_at
    return nc


def _merge_chain_pair(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    def _rank(nc: dict[str, Any]) -> tuple[int, int, float, int, str]:
        return (
            1 if nc.get("archetypeId") else 0,
            len(nc.get("breaksAt") or []),
            float(nc.get("encompassWeight") or 0.0),
            len(str(nc.get("why") or "")),
            _canonical_json(nc),
        )

    winner, loser = (a, b) if _rank(a) >= _rank(b) else (b, a)
    merged = copy.deepcopy(winner)

    w_max = max(float(a.get("encompassWeight") or 0.0), float(b.get("encompassWeight") or 0.0))
    merged["encompassWeight"] = round(max(0.0, min(1.0, w_max)), 4)

    why_a = str(a.get("why") or "").strip()
    why_b = str(b.get("why") or "").strip()
    if (len(why_b), why_b) > (len(why_a), why_a):
        merged["why"] = why_b
    else:
        merged["why"] = why_a

    arch_a = str(a.get("archetypeId") or "").strip()
    arch_b = str(b.get("archetypeId") or "").strip()
    if arch_a or arch_b:
        merged["archetypeId"] = str(winner.get("archetypeId") or loser.get("archetypeId") or "").strip()

    if "breaksAt" in a or "breaksAt" in b:
        merged["breaksAt"] = _dedupe_str_union(a.get("breaksAt"), b.get("breaksAt"))

    created_vals = [
        str(x).strip()
        for x in (a.get("createdAt"), b.get("createdAt"))
        if isinstance(x, str) and x.strip()
    ]
    if created_vals:
        merged["createdAt"] = min(created_vals, key=_ts_sort_key)

    return merged


def _merge_chains(
    local_rows: list[dict[str, Any]], incoming_rows: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], int, int]:
    merged_map: dict[tuple[tuple[str, ...], str, str, str], dict[str, Any]] = {}
    for row in local_rows:
        nc = _canonicalize_chain(row)
        if nc is None:
            continue
        key = _chain_key(nc)
        merged_map[key] = _merge_chain_pair(merged_map[key], nc) if key in merged_map else nc

    inc_map: dict[tuple[tuple[str, ...], str, str, str], dict[str, Any]] = {}
    for row in incoming_rows:
        nc = _canonicalize_chain(row)
        if nc is None:
            continue
        key = _chain_key(nc)
        inc_map[key] = _merge_chain_pair(inc_map[key], nc) if key in inc_map else nc

    added = 0
    updated = 0
    for key, nc in inc_map.items():
        if key not in merged_map:
            merged_map[key] = nc
            added += 1
        else:
            before = merged_map[key]
            after = _merge_chain_pair(before, nc)
            if _canonical_json(after) != _canonical_json(before):
                merged_map[key] = after
                updated += 1

    out = [merged_map[k] for k in sorted(merged_map, key=lambda k: (k[1], k[0], k[2], k[3]))]
    return out, added, updated


# --- Append-only G-Set Ledgers (revlog, mistakes, viva, forks, arenas, derivations) ---


def _gset_key(kind: str, row: dict[str, Any]) -> str:
    if kind == "revlog":
        sig = (
            str(row.get("propId") or "").strip(),
            _ts_sort_key(row.get("ts") or row.get("timestamp") or ""),
            str(row.get("cueId") or ""),
            str(row.get("rating") or row.get("ratingInt") or "").strip().lower(),
            bool(row.get("correct")) if isinstance(row.get("correct"), bool) else None,
            row.get("learnerConfidence"),
            row.get("durationMs"),
            int(row.get("hintsUsed") or 0),
        )
        return f"rev:{_canonical_sha256(sig)}"

    rid = str(row.get("id") or "").strip()
    if rid:
        return f"id:{rid}"
    return f"hash:{_canonical_sha256(row)}"


def _entity_progress_tuple(kind: str, row: dict[str, Any]) -> tuple[Any, ...]:
    ts = _ts_sort_key(_extract_row_ts(row))
    if kind == "forks":
        wins = int(row.get("winCount") or 0)
        attempts = int(row.get("attempts") or 0)
        wp = 1 if row.get("woodpeckerPassed") else 0
        return (wp, wins, attempts, ts)
    if kind == "arenas":
        killed = len(row.get("killedMutants") or row.get("killedIds") or [])
        repaired = 1 if row.get("repairedLemma") else 0
        attempts = int(row.get("attempts") or 0)
        return (repaired, killed, attempts, ts)
    if kind == "vivaSessions":
        passed = 1 if row.get("passed") else 0
        turns = len(row.get("turns") or [])
        return (passed, turns, ts)
    if kind == "derivations":
        passed = 1 if (row.get("passed") or row.get("blackoutPassed") or row.get("axiomCutPassed")) else 0
        comps = int(row.get("completions") or 0)
        return (passed, comps, ts)
    return (ts,)


def _merge_gset_entity(kind: str, a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    if _canonical_json(a) == _canonical_json(b):
        return copy.deepcopy(a)

    # Compare using scalar progress + deterministic non-set canonical JSON
    set_fields = {
        "forks": frozenset({"latencyMsHistory", "woodpeckerPassed", "winCount", "attempts"}),
        "arenas": frozenset({"killedMutants", "killedIds", "attempts"}),
        "vivaSessions": frozenset({"passed"}),
        "derivations": frozenset({"passed", "blackoutPassed", "axiomCutPassed", "completions"}),
    }.get(kind, frozenset())

    sa = {k: v for k, v in a.items() if k not in set_fields}
    sb = {k: v for k, v in b.items() if k not in set_fields}
    ka = (*_entity_progress_tuple(kind, a), _canonical_json(sa))
    kb = (*_entity_progress_tuple(kind, b), _canonical_json(sb))
    winner, loser = (a, b) if ka >= kb else (b, a)
    merged = copy.deepcopy(winner)

    if kind == "forks":
        if "winCount" in a or "winCount" in b:
            merged["winCount"] = max(int(a.get("winCount") or 0), int(b.get("winCount") or 0))
        if "attempts" in a or "attempts" in b:
            merged["attempts"] = max(int(a.get("attempts") or 0), int(b.get("attempts") or 0))
        if "woodpeckerPassed" in a or "woodpeckerPassed" in b:
            merged["woodpeckerPassed"] = bool(a.get("woodpeckerPassed")) or bool(b.get("woodpeckerPassed"))
        if "latencyMsHistory" in a or "latencyMsHistory" in b:
            lats = sorted(
                {
                    float(x)
                    for seq in (a.get("latencyMsHistory"), b.get("latencyMsHistory"))
                    if isinstance(seq, list)
                    for x in seq
                    if isinstance(x, (int, float)) and not isinstance(x, bool)
                }
            )
            merged["latencyMsHistory"] = lats
    elif kind == "arenas":
        if "killedMutants" in a or "killedMutants" in b:
            merged["killedMutants"] = _dedupe_str_union(a.get("killedMutants"), b.get("killedMutants"))
        if "killedIds" in a or "killedIds" in b:
            merged["killedIds"] = _dedupe_str_union(a.get("killedIds"), b.get("killedIds"))
        if "attempts" in a or "attempts" in b:
            merged["attempts"] = max(int(a.get("attempts") or 0), int(b.get("attempts") or 0))
    elif kind == "vivaSessions":
        if "passed" in a or "passed" in b:
            merged["passed"] = bool(a.get("passed")) or bool(b.get("passed"))
    elif kind == "derivations":
        for flag in ("passed", "blackoutPassed", "axiomCutPassed"):
            if flag in a or flag in b:
                merged[flag] = bool(a.get(flag)) or bool(b.get(flag))
        if "completions" in a or "completions" in b:
            merged["completions"] = max(int(a.get("completions") or 0), int(b.get("completions") or 0))

    for k, v in loser.items():
        if k not in merged or merged[k] is None or merged[k] == "":
            merged[k] = copy.deepcopy(v)
    return merged


def _merge_gset_ledger(
    kind: str,
    local_rows: list[dict[str, Any]],
    incoming_rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int, int]:
    merged_map: dict[str, dict[str, Any]] = {}
    for row in local_rows:
        if not isinstance(row, dict):
            continue
        k = _gset_key(kind, row)
        merged_map[k] = _merge_gset_entity(kind, merged_map[k], row) if k in merged_map else copy.deepcopy(row)

    inc_map: dict[str, dict[str, Any]] = {}
    for row in incoming_rows:
        if not isinstance(row, dict):
            continue
        k = _gset_key(kind, row)
        inc_map[k] = _merge_gset_entity(kind, inc_map[k], row) if k in inc_map else copy.deepcopy(row)

    added = 0
    updated = 0
    for k, inc in inc_map.items():
        if k not in merged_map:
            merged_map[k] = copy.deepcopy(inc)
            added += 1
        else:
            before = merged_map[k]
            after = _merge_gset_entity(kind, before, inc)
            if _canonical_json(after) != _canonical_json(before):
                merged_map[k] = after
                updated += 1

    out = sorted(
        merged_map.values(),
        key=lambda r: (_ts_sort_key(_extract_row_ts(r)), str(r.get("id") or r.get("propId") or ""), _canonical_json(r)),
    )
    return out, added, updated


# --- Evidence Ledger (Deterministic Re-Chaining) ---


def _evidence_dedup_key(row: dict[str, Any]) -> tuple[str, int, str, str]:
    pid = str(row.get("propId") or "").strip()
    raw_tier = row.get("tier")
    tier = int(raw_tier) if isinstance(raw_tier, int) and not isinstance(raw_tier, bool) and raw_tier in TIER_LABELS else 1
    src = str(row.get("source") or "manual").strip() or "manual"
    ts = str(row.get("ts") or row.get("timestamp") or "").strip()
    return (pid, tier, src, ts)


def _strip_chain_fields(row: dict[str, Any]) -> dict[str, Any]:
    pid, tier, src, ts = _evidence_dedup_key(row)
    cleaned: dict[str, Any] = {
        "ts": ts,
        "propId": pid,
        "tier": tier,
        "tierLabel": TIER_LABELS.get(tier, str(row.get("tierLabel") or "T1 Exposure")),
        "source": src,
        "detail": row.get("detail"),
        "verified": bool(row.get("verified", True)),
    }
    for k, v in row.items():
        if k not in ("seq", "prevHash", "entryHash", "timestamp", *cleaned.keys()):
            cleaned[k] = copy.deepcopy(v)
    return cleaned


def _rechain_evidence_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ordered = sorted(
        entries,
        key=lambda r: (
            _ts_sort_key(r.get("ts") or ""),
            str(r.get("propId") or ""),
            int(r.get("tier") or 1),
            str(r.get("source") or ""),
            _canonical_json(r.get("detail")),
        ),
    )
    prev_hash = GENESIS_HASH
    chained: list[dict[str, Any]] = []
    for seq, base in enumerate(ordered, start=1):
        body = dict(base)
        body["seq"] = seq
        body["prevHash"] = prev_hash
        entry_hash = _canonical_sha256(body)
        record = {**body, "entryHash": entry_hash}
        chained.append(record)
        prev_hash = entry_hash
    return chained


def _merge_evidence_ledger(
    local_rows: list[dict[str, Any]], incoming_rows: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], int, int]:
    def _entry_rank(clean: dict[str, Any]) -> tuple[int, int, str]:
        return (1 if clean.get("verified") else 0, 1 if clean.get("detail") is not None else 0, _canonical_json(clean))

    merged_map: dict[tuple[str, int, str, str], dict[str, Any]] = {}
    for row in local_rows:
        if not isinstance(row, dict) or not str(row.get("propId") or "").strip():
            continue
        k = _evidence_dedup_key(row)
        clean = _strip_chain_fields(row)
        if k not in merged_map or _entry_rank(clean) > _entry_rank(merged_map[k]):
            merged_map[k] = clean

    inc_map: dict[tuple[str, int, str, str], dict[str, Any]] = {}
    for row in incoming_rows:
        if not isinstance(row, dict) or not str(row.get("propId") or "").strip():
            continue
        k = _evidence_dedup_key(row)
        clean = _strip_chain_fields(row)
        if k not in inc_map or _entry_rank(clean) > _entry_rank(inc_map[k]):
            inc_map[k] = clean

    added = 0
    updated = 0
    for k, clean in inc_map.items():
        if k not in merged_map:
            merged_map[k] = clean
            added += 1
        else:
            if _entry_rank(clean) > _entry_rank(merged_map[k]) and _canonical_json(clean) != _canonical_json(merged_map[k]):
                merged_map[k] = clean
                updated += 1

    rechained = _rechain_evidence_entries(list(merged_map.values()))
    if added == 0 and updated == 0 and _canonical_json(rechained) == _canonical_json(local_rows):
        return local_rows, 0, 0
    return rechained, added, updated


# --- Chapter Trackers (Monotonic Join-Semilattice) ---


def _join_status(raw_a: Any, raw_b: Any, rank_map: dict[str, int], default: str) -> str:
    sa = str(raw_a or default).strip().lower() or default
    sb = str(raw_b or default).strip().lower() or default
    ra = rank_map.get(sa, 0)
    rb = rank_map.get(sb, 0)
    if (ra, sa) >= (rb, sb):
        return sa
    return sb


def _merge_topic(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    sa = str(a.get("status") or "unseen").strip().lower()
    sb = str(b.get("status") or "unseen").strip().lower()
    ra = TOPIC_STATUS_RANK.get(sa, 0)
    rb = TOPIC_STATUS_RANK.get(sb, 0)

    scalar_skip = frozenset({"status", "coveredPages", "propIds", "prereqTopicIds", "sourceRefs", "notes"})
    ka = (ra, sa, _canonical_json({k: v for k, v in a.items() if k not in scalar_skip}))
    kb = (rb, sb, _canonical_json({k: v for k, v in b.items() if k not in scalar_skip}))
    winner, loser = (a, b) if ka >= kb else (b, a)

    merged = copy.deepcopy(winner)
    for k, v in loser.items():
        if k in scalar_skip:
            continue
        if k not in merged or merged[k] is None or merged[k] == "":
            merged[k] = copy.deepcopy(v)

    merged["status"] = _join_status(sa, sb, TOPIC_STATUS_RANK, "unseen")
    if "coveredPages" in a or "coveredPages" in b:
        merged["coveredPages"] = _merge_covered_pages(a.get("coveredPages"), b.get("coveredPages"))
    if "propIds" in a or "propIds" in b:
        merged["propIds"] = _dedupe_str_union(a.get("propIds"), b.get("propIds"))
    if "prereqTopicIds" in a or "prereqTopicIds" in b:
        merged["prereqTopicIds"] = _dedupe_str_union(a.get("prereqTopicIds"), b.get("prereqTopicIds"))
    if "notes" in a or "notes" in b:
        merged["notes"] = _merge_notes(a.get("notes"), b.get("notes"))
    if "sourceRefs" in a or "sourceRefs" in b:
        refs_map: dict[tuple[str, str, str], dict[str, Any]] = {}
        for seq in (a.get("sourceRefs"), b.get("sourceRefs")):
            if not isinstance(seq, list):
                continue
            for ref in seq:
                if not isinstance(ref, dict):
                    continue
                rk = (
                    str(ref.get("sourceId") or ""),
                    str(ref.get("pages") or ""),
                    str(ref.get("section") or ""),
                )
                if rk not in refs_map or _canonical_json(ref) > _canonical_json(refs_map[rk]):
                    refs_map[rk] = copy.deepcopy(ref)
        merged["sourceRefs"] = [refs_map[k] for k in sorted(refs_map)]
    return merged


def _merge_question(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    sa = str(a.get("status") or "unseen").strip().lower()
    sb = str(b.get("status") or "unseen").strip().lower()
    ra = QUESTION_STATUS_RANK.get(sa, 0)
    rb = QUESTION_STATUS_RANK.get(sb, 0)
    att_a = int(a.get("attempts") or 0) if isinstance(a.get("attempts"), (int, float)) else 0
    att_b = int(b.get("attempts") or 0) if isinstance(b.get("attempts"), (int, float)) else 0
    da = _ts_sort_key(a.get("lastDate") or "")
    db = _ts_sort_key(b.get("lastDate") or "")

    scalar_skip = frozenset({"status", "attempts", "lastDate", "lastStep"})
    ka = (ra, sa, att_a, da, _canonical_json({k: v for k, v in a.items() if k not in scalar_skip}))
    kb = (rb, sb, att_b, db, _canonical_json({k: v for k, v in b.items() if k not in scalar_skip}))
    winner, loser = (a, b) if ka >= kb else (b, a)

    merged = copy.deepcopy(winner)
    for k, v in loser.items():
        if k in scalar_skip:
            continue
        if k not in merged or merged[k] is None or merged[k] == "":
            merged[k] = copy.deepcopy(v)

    merged["status"] = _join_status(sa, sb, QUESTION_STATUS_RANK, "unseen")
    if "attempts" in a or "attempts" in b:
        merged["attempts"] = max(att_a, att_b)
    if "lastDate" in a or "lastDate" in b:
        dates = [str(x) for x in (a.get("lastDate"), b.get("lastDate")) if isinstance(x, str) and x.strip()]
        merged["lastDate"] = max(dates, key=_ts_sort_key) if dates else None
    if "lastStep" in a or "lastStep" in b:
        steps = [
            int(x)
            for x in (a.get("lastStep"), b.get("lastStep"))
            if isinstance(x, (int, float)) and not isinstance(x, bool)
        ]
        merged["lastStep"] = max(steps) if steps else None
    return merged


def _merge_source(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    pa = int(a.get("totalPages") or 0) if isinstance(a.get("totalPages"), (int, float)) else 0
    pb = int(b.get("totalPages") or 0) if isinstance(b.get("totalPages"), (int, float)) else 0
    skip = frozenset({"totalPages", "indexedRanges", "notes"})
    ka = (pa, _canonical_json({k: v for k, v in a.items() if k not in skip}))
    kb = (pb, _canonical_json({k: v for k, v in b.items() if k not in skip}))
    winner, loser = (a, b) if ka >= kb else (b, a)

    merged = copy.deepcopy(winner)
    for k, v in loser.items():
        if k in skip:
            continue
        if k not in merged or merged[k] is None or merged[k] == "":
            merged[k] = copy.deepcopy(v)
    if "totalPages" in a or "totalPages" in b:
        merged["totalPages"] = max(pa, pb)
    if "notes" in a or "notes" in b:
        merged["notes"] = _merge_notes(a.get("notes"), b.get("notes"))
    if "indexedRanges" in a or "indexedRanges" in b:
        ranges: set[tuple[int, int]] = set()
        for seq in (a.get("indexedRanges"), b.get("indexedRanges")):
            if isinstance(seq, list):
                for item in seq:
                    if isinstance(item, (list, tuple)) and len(item) == 2:
                        try:
                            lo, hi = int(item[0]), int(item[1])
                            ranges.add((min(lo, hi), max(lo, hi)))
                        except (TypeError, ValueError):
                            pass
        merged["indexedRanges"] = [[lo, hi] for lo, hi in sorted(ranges)]
    return merged


def _merge_slice(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    sa = str(a.get("stage") or "queued").strip().lower()
    sb = str(b.get("stage") or "queued").strip().lower()
    ra = SLICE_STAGE_RANK.get(sa, 0)
    rb = SLICE_STAGE_RANK.get(sb, 0)
    skip = frozenset({"stage", "propIds", "tags", "notes"})
    ka = (ra, sa, _canonical_json({k: v for k, v in a.items() if k not in skip}))
    kb = (rb, sb, _canonical_json({k: v for k, v in b.items() if k not in skip}))
    winner, loser = (a, b) if ka >= kb else (b, a)

    merged = copy.deepcopy(winner)
    for k, v in loser.items():
        if k in skip:
            continue
        if k not in merged or merged[k] is None or merged[k] == "":
            merged[k] = copy.deepcopy(v)
    merged["stage"] = _join_status(sa, sb, SLICE_STAGE_RANK, "queued")
    if "propIds" in a or "propIds" in b:
        merged["propIds"] = _dedupe_str_union(a.get("propIds"), b.get("propIds"))
    if "tags" in a or "tags" in b:
        merged["tags"] = _dedupe_str_union(a.get("tags"), b.get("tags"))
    if "notes" in a or "notes" in b:
        merged["notes"] = _merge_notes(a.get("notes"), b.get("notes"))
    return merged


def _merge_tracker(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    ua = _ts_sort_key(a.get("updatedAt") or "")
    ub = _ts_sort_key(b.get("updatedAt") or "")
    structured_keys = frozenset(
        {
            "sources",
            "topics",
            "questions",
            "slices",
            "milestones",
            "doubtStack",
            "stepCounter",
            "sessionLog",
            "updatedAt",
            "createdAt",
            "active",
        }
    )
    ka = (ua, _canonical_json({k: v for k, v in a.items() if k not in structured_keys}))
    kb = (ub, _canonical_json({k: v for k, v in b.items() if k not in structured_keys}))
    winner, loser = (a, b) if ka >= kb else (b, a)

    merged = copy.deepcopy(winner)
    for k, v in loser.items():
        if k in structured_keys:
            continue
        if k not in merged or merged[k] is None or merged[k] == "":
            merged[k] = copy.deepcopy(v)

    # Timestamps & active flag
    created_vals = [str(x) for x in (a.get("createdAt"), b.get("createdAt")) if isinstance(x, str) and x.strip()]
    if created_vals:
        merged["createdAt"] = min(created_vals, key=_ts_sort_key)
    updated_vals = [str(x) for x in (a.get("updatedAt"), b.get("updatedAt")) if isinstance(x, str) and x.strip()]
    if updated_vals:
        merged["updatedAt"] = max(updated_vals, key=_ts_sort_key)
    if "active" in a or "active" in b:
        merged["active"] = bool(a.get("active")) or bool(b.get("active"))

    # Sources (union by id/label/path)
    src_map: dict[str, dict[str, Any]] = {}
    for seq in (a.get("sources"), b.get("sources")):
        if not isinstance(seq, list):
            continue
        for s in seq:
            if not isinstance(s, dict):
                continue
            sk = str(s.get("id") or s.get("label") or s.get("path") or _canonical_sha256(s)).strip()
            src_map[sk] = _merge_source(src_map[sk], s) if sk in src_map else copy.deepcopy(s)
    if "sources" in a or "sources" in b or src_map:
        merged["sources"] = [src_map[k] for k in sorted(src_map)]

    # Topics (monotonic status lattice)
    topic_map: dict[str, dict[str, Any]] = {}
    for seq in (a.get("topics"), b.get("topics")):
        if not isinstance(seq, list):
            continue
        for t in seq:
            if not isinstance(t, dict):
                continue
            tk = str(t.get("id") or t.get("title") or _canonical_sha256(t)).strip()
            topic_map[tk] = _merge_topic(topic_map[tk], t) if tk in topic_map else copy.deepcopy(t)
    if "topics" in a or "topics" in b or topic_map:
        merged["topics"] = sorted(
            topic_map.values(),
            key=lambda t: (
                int(t.get("order") or 0) if isinstance(t.get("order"), (int, float)) else 0,
                str(t.get("id") or ""),
            ),
        )

    # Questions (monotonic status lattice)
    q_map: dict[str, dict[str, Any]] = {}
    for seq in (a.get("questions"), b.get("questions")):
        if not isinstance(seq, list):
            continue
        for q in seq:
            if not isinstance(q, dict):
                continue
            qk = str(q.get("qid") or q.get("id") or _canonical_sha256(q)).strip()
            q_map[qk] = _merge_question(q_map[qk], q) if qk in q_map else copy.deepcopy(q)
    if "questions" in a or "questions" in b or q_map:
        merged["questions"] = [q_map[k] for k in sorted(q_map)]

    # Slices (union by id + monotonic stage lattice)
    if "slices" in a or "slices" in b:
        slice_map: dict[str, dict[str, Any]] = {}
        for seq in (a.get("slices"), b.get("slices")):
            if not isinstance(seq, list):
                continue
            for sl in seq:
                if not isinstance(sl, dict):
                    continue
                slk = str(sl.get("id") or sl.get("key") or sl.get("title") or _canonical_sha256(sl)).strip()
                slice_map[slk] = _merge_slice(slice_map[slk], sl) if slk in slice_map else copy.deepcopy(sl)
        merged["slices"] = [slice_map[k] for k in sorted(slice_map)]

    # Milestones (logical OR — True always wins over False)
    ms_a = a.get("milestones") if isinstance(a.get("milestones"), dict) else {}
    ms_b = b.get("milestones") if isinstance(b.get("milestones"), dict) else {}
    if ms_a or ms_b or "milestones" in a or "milestones" in b:
        merged["milestones"] = {
            mk: bool(ms_a.get(mk)) or bool(ms_b.get(mk))
            for mk in sorted(set(ms_a.keys()) | set(ms_b.keys()))
        }

    # Step counter & session log
    if "stepCounter" in a or "stepCounter" in b:
        merged["stepCounter"] = max(int(a.get("stepCounter") or 0), int(b.get("stepCounter") or 0))

    if "doubtStack" in a or "doubtStack" in b:
        doubts: dict[str, Any] = {}
        for seq in (a.get("doubtStack"), b.get("doubtStack")):
            if isinstance(seq, list):
                for item in seq:
                    doubts[_canonical_sha256(item)] = copy.deepcopy(item)
        merged["doubtStack"] = [doubts[k] for k in sorted(doubts)]

    if "sessionLog" in a or "sessionLog" in b:
        log_map: dict[str, dict[str, Any]] = {}
        for seq in (a.get("sessionLog"), b.get("sessionLog")):
            if isinstance(seq, list):
                for entry in seq:
                    if isinstance(entry, dict):
                        log_map[_canonical_sha256(entry)] = copy.deepcopy(entry)
        merged["sessionLog"] = sorted(
            log_map.values(),
            key=lambda e: (
                _ts_sort_key(e.get("ts") or ""),
                int(e.get("step") or 0) if isinstance(e.get("step"), (int, float)) else 0,
                _canonical_json(e),
            ),
        )

    return merged


# --- Learner Profile (Per-Device CRDT Contributions for Weighted Elo + Commutative/Idempotent Merge) ---


def _materialize_domain_device_states(dom_rec: dict[str, Any], owner_device_id: str) -> dict[str, dict[str, Any]]:
    """Build or update per-device contribution map so weighted-by-`n` Elo is commutative AND idempotent."""
    raw_states = dom_rec.get("deviceStates")
    states: dict[str, dict[str, Any]] = {}
    if isinstance(raw_states, dict):
        for dev, entry in raw_states.items():
            if isinstance(dev, str) and dev.strip() and isinstance(entry, dict):
                states[dev.strip()] = {
                    "elo": round(_safe_finite_float(entry.get("elo"), 1450.0, lo=ELO_MIN, hi=ELO_MAX), 1),
                    "n": _safe_nonneg_int(entry.get("n"), 0),
                    "lastUpdated": str(entry.get("lastUpdated") or ""),
                }

    cur_elo = round(_safe_finite_float(dom_rec.get("elo"), 1450.0, lo=ELO_MIN, hi=ELO_MAX), 1)
    cur_n = _safe_nonneg_int(dom_rec.get("n"), 0)
    cur_lu = str(dom_rec.get("lastUpdated") or "")

    if not states:
        states[owner_device_id] = {"elo": cur_elo, "n": cur_n, "lastUpdated": cur_lu}
        return states

    # Reconcile any local progress made after a prior sync
    peer_n = sum(v["n"] for k, v in states.items() if k != owner_device_id)
    prev_local = states.get(owner_device_id)
    total_tracked_n = sum(v["n"] for v in states.values())

    if cur_n > total_tracked_n:
        new_local_n = max(0, cur_n - peer_n)
        peer_weighted = sum(v["elo"] * v["n"] for k, v in states.items() if k != owner_device_id)
        if new_local_n > 0:
            implied = (cur_elo * cur_n - peer_weighted) / new_local_n
            implied = max(ELO_MIN, min(ELO_MAX, implied))
        else:
            implied = cur_elo
        states[owner_device_id] = {
            "elo": round(implied, 1),
            "n": new_local_n,
            "lastUpdated": max(cur_lu, (prev_local or {}).get("lastUpdated", ""), key=_ts_sort_key),
        }
    elif owner_device_id not in states and cur_n == 0 and cur_elo != 1450.0:
        states[owner_device_id] = {"elo": cur_elo, "n": 0, "lastUpdated": cur_lu}
    return states


def _materialize_global_device_states(gs: dict[str, Any], owner_device_id: str) -> dict[str, dict[str, Any]]:
    raw_states = gs.get("deviceStates")
    states: dict[str, dict[str, Any]] = {}
    if isinstance(raw_states, dict):
        for dev, entry in raw_states.items():
            if isinstance(dev, str) and dev.strip() and isinstance(entry, dict):
                states[dev.strip()] = {
                    "elo": round(_safe_finite_float(entry.get("elo"), 1450.0, lo=ELO_MIN, hi=ELO_MAX), 1),
                    "n": _safe_nonneg_int(entry.get("n"), 0),
                    "pfAttempts": _safe_nonneg_int(entry.get("pfAttempts"), 0),
                    "pfSurvivalRate": round(_safe_finite_float(entry.get("pfSurvivalRate"), 0.65, lo=0.0, hi=1.0), 4),
                    "analogyFormalismRatio": round(
                        _safe_finite_float(entry.get("analogyFormalismRatio"), 0.50, lo=0.0, hi=1.0), 4
                    ),
                    "latencyEwma": round(_safe_finite_float(entry.get("latencyEwma"), 1.0, lo=0.1, hi=10.0), 3),
                }

    cur_elo = round(_safe_finite_float(gs.get("elo"), 1450.0, lo=ELO_MIN, hi=ELO_MAX), 1)
    cur_n = _safe_nonneg_int(gs.get("totalEvents"), 0)
    cur_pf_n = _safe_nonneg_int(gs.get("pfAttempts"), 0)
    cur_pf_r = round(_safe_finite_float(gs.get("pfSurvivalRate"), 0.65, lo=0.0, hi=1.0), 4)
    cur_af = round(_safe_finite_float(gs.get("analogyFormalismRatio"), 0.50, lo=0.0, hi=1.0), 4)
    cur_lat = round(_safe_finite_float(gs.get("latencyEwma"), 1.0, lo=0.1, hi=10.0), 3)

    if not states:
        states[owner_device_id] = {
            "elo": cur_elo,
            "n": cur_n,
            "pfAttempts": cur_pf_n,
            "pfSurvivalRate": cur_pf_r,
            "analogyFormalismRatio": cur_af,
            "latencyEwma": cur_lat,
        }
        return states

    total_tracked_n = sum(v["n"] for v in states.values())
    peer_n = sum(v["n"] for k, v in states.items() if k != owner_device_id)
    if cur_n > total_tracked_n:
        new_local_n = max(0, cur_n - peer_n)
        peer_weighted = sum(v["elo"] * v["n"] for k, v in states.items() if k != owner_device_id)
        implied = (cur_elo * cur_n - peer_weighted) / new_local_n if new_local_n > 0 else cur_elo
        implied = max(ELO_MIN, min(ELO_MAX, implied))
        peer_pf_n = sum(v["pfAttempts"] for k, v in states.items() if k != owner_device_id)
        states[owner_device_id] = {
            "elo": round(implied, 1),
            "n": new_local_n,
            "pfAttempts": max(0, cur_pf_n - peer_pf_n),
            "pfSurvivalRate": cur_pf_r,
            "analogyFormalismRatio": cur_af,
            "latencyEwma": cur_lat,
        }
    return states


def _prepare_profile_for_export(profile: dict[str, Any], device_id: str) -> dict[str, Any]:
    prof = _compact_profile_for_disk(profile)
    gs = prof.setdefault("globalStats", {})
    gs["deviceStates"] = _materialize_global_device_states(gs, device_id)
    for dom_rec in (prof.get("domains") or {}).values():
        if isinstance(dom_rec, dict):
            dom_rec["deviceStates"] = _materialize_domain_device_states(dom_rec, device_id)
    return prof


def _merge_device_state_maps(
    map_a: dict[str, dict[str, Any]], map_b: dict[str, dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for dev in sorted(set(map_a.keys()) | set(map_b.keys())):
        ea = map_a.get(dev)
        eb = map_b.get(dev)
        if ea is None:
            merged[dev] = copy.deepcopy(eb)  # type: ignore[arg-type]
        elif eb is None:
            merged[dev] = copy.deepcopy(ea)
        else:
            ka = (int(ea.get("n") or 0), _ts_sort_key(ea.get("lastUpdated") or ""), _canonical_json(ea))
            kb = (int(eb.get("n") or 0), _ts_sort_key(eb.get("lastUpdated") or ""), _canonical_json(eb))
            merged[dev] = copy.deepcopy(ea if ka >= kb else eb)
    return merged


def _recent_event_key(ev: dict[str, Any]) -> str:
    sig = (
        _ts_sort_key(ev.get("ts") or ""),
        str(ev.get("domain") or "General"),
        str(ev.get("propId") or ""),
        str(ev.get("kind") or "review"),
        bool(ev.get("correct")),
        bool(ev.get("skipped")),
        ev.get("confidence"),
        ev.get("durationMs"),
        int(ev.get("hintsUsed") or 0),
    )
    return _canonical_sha256(sig)


def _merge_learner_profiles(
    local_profile: dict[str, Any] | None,
    remote_profile: dict[str, Any] | None,
    *,
    local_device_id: str,
    remote_device_id: str,
    kg_dir: Path,
) -> dict[str, Any] | None:
    if not local_profile and not remote_profile:
        return None
    if not local_profile:
        base = _merge_defaults(remote_profile)
        gs = base["globalStats"]
        gst = _materialize_global_device_states(gs, remote_device_id)
        gs["deviceStates"] = gst
        gs["elo"] = round(_safe_finite_float(gs.get("elo"), 1450.0, lo=ELO_MIN, hi=ELO_MAX), 1)
        gs["totalEvents"] = _safe_nonneg_int(gs.get("totalEvents"), 0)
        gs["pfAttempts"] = _safe_nonneg_int(gs.get("pfAttempts"), 0)
        gs["pfSurvivalRate"] = round(_safe_finite_float(gs.get("pfSurvivalRate"), 0.65, lo=0.0, hi=1.0), 4)
        gs["analogyFormalismRatio"] = round(
            _safe_finite_float(gs.get("analogyFormalismRatio"), 0.50, lo=0.0, hi=1.0), 3
        )
        gs["latencyEwma"] = round(_safe_finite_float(gs.get("latencyEwma"), 1.0, lo=0.1, hi=10.0), 3)
        for dom_rec in base["domains"].values():
            if isinstance(dom_rec, dict):
                dom_rec["elo"] = round(_safe_finite_float(dom_rec.get("elo"), 1450.0, lo=ELO_MIN, hi=ELO_MAX), 1)
                dom_rec["n"] = _safe_nonneg_int(dom_rec.get("n"), 0)
                dom_rec["expertiseBand"] = _elo_to_band(dom_rec["elo"])
                dom_rec["deviceStates"] = _materialize_domain_device_states(dom_rec, remote_device_id)
        base["calibration"]["sessionTargetMinutes"] = _safe_finite_float(
            base["calibration"].get("sessionTargetMinutes"), 30.0, lo=5.0, hi=240.0
        )
        if "baselineLatencyMs" in base["calibration"] and base["calibration"]["baselineLatencyMs"] is not None:
            base["calibration"]["baselineLatencyMs"] = _safe_finite_float(
                base["calibration"].get("baselineLatencyMs"), 22000.0, lo=1000.0, hi=300000.0
            )
        base["affectiveState"] = classify_affective_state(
            base["recentEvents"],
            session_target_min=float(base["calibration"]["sessionTargetMinutes"]),
        )
        base["metacognition"] = _compute_metacognition(kg_dir, base["recentEvents"], base["calibration"])
        base["failureDna"] = _compute_failure_dna(kg_dir)
        base["compiledPolicy"] = compile_teaching_policy(base)
        return _compact_profile_for_disk(base)
    if not remote_profile:
        return _compact_profile_for_disk(local_profile)

    pa = _merge_defaults(local_profile)
    pb = _merge_defaults(remote_profile)

    # 1. Calibration: preserve `completed = a or b`
    cal_a = pa["calibration"]
    cal_b = pb["calibration"]
    comp_a = bool(cal_a.get("completed"))
    comp_b = bool(cal_b.get("completed"))
    if comp_a and not comp_b:
        merged_cal = copy.deepcopy(cal_a)
    elif comp_b and not comp_a:
        merged_cal = copy.deepcopy(cal_b)
    else:
        ka = (_ts_sort_key(cal_a.get("completedAt") or pa.get("updatedAt") or ""), _canonical_json(cal_a))
        kb = (_ts_sort_key(cal_b.get("completedAt") or pb.get("updatedAt") or ""), _canonical_json(cal_b))
        merged_cal = copy.deepcopy(cal_a if ka >= kb else cal_b)
    merged_cal["completed"] = comp_a or comp_b

    # 2. User overrides & calibratedHyperparams (deterministic LWW)
    uo_a = pa.get("userOverrides") or {}
    uo_b = pb.get("userOverrides") or {}
    has_uo_a = any(v is not None for v in uo_a.values()) if isinstance(uo_a, dict) else False
    has_uo_b = any(v is not None for v in uo_b.values()) if isinstance(uo_b, dict) else False
    uo_ka = (1 if has_uo_a else 0, _ts_sort_key(pa.get("updatedAt") or ""), _canonical_json(uo_a))
    uo_kb = (1 if has_uo_b else 0, _ts_sort_key(pb.get("updatedAt") or ""), _canonical_json(uo_b))
    merged_uo = copy.deepcopy(uo_a if uo_ka >= uo_kb else uo_b)

    ch_a = pa.get("calibratedHyperparams") if isinstance(pa.get("calibratedHyperparams"), dict) else None
    ch_b = pb.get("calibratedHyperparams") if isinstance(pb.get("calibratedHyperparams"), dict) else None
    merged_ch: dict[str, Any] | None = None
    if ch_a and not ch_b:
        merged_ch = copy.deepcopy(ch_a)
    elif ch_b and not ch_a:
        merged_ch = copy.deepcopy(ch_b)
    elif ch_a and ch_b:
        cka = (_ts_sort_key(ch_a.get("calibratedAt") or ""), float(ch_a.get("oosImprovement") or 0.0), _canonical_json(ch_a))
        ckb = (_ts_sort_key(ch_b.get("calibratedAt") or ""), float(ch_b.get("oosImprovement") or 0.0), _canonical_json(ch_b))
        merged_ch = copy.deepcopy(ch_a if cka >= ckb else ch_b)

    # 3. Per-domain Elo weighted by per-device `n` (never losing event counts, commutative & idempotent!)
    doms_a = pa.get("domains") or {}
    doms_b = pb.get("domains") or {}
    merged_domains: dict[str, dict[str, Any]] = {}
    for dom_name in sorted(set(doms_a.keys()) | set(doms_b.keys())):
        da = doms_a.get(dom_name) if isinstance(doms_a.get(dom_name), dict) else None
        db = doms_b.get(dom_name) if isinstance(doms_b.get(dom_name), dict) else None
        st_a = _materialize_domain_device_states(da, local_device_id) if da else {}
        st_b = _materialize_domain_device_states(db, remote_device_id) if db else {}
        merged_st = _merge_device_state_maps(st_a, st_b)

        total_n = max(0, sum(_safe_nonneg_int(v.get("n"), 0) for v in merged_st.values()))
        if total_n > 0:
            elo_val = round(
                _safe_finite_float(
                    sum(v["elo"] * v["n"] for v in merged_st.values()) / total_n,
                    1450.0,
                    lo=ELO_MIN,
                    hi=ELO_MAX,
                ),
                1,
            )
        else:
            # No scored events yet: average non-default priors if present, else average all
            non_def = [v["elo"] for v in merged_st.values() if abs(v["elo"] - 1450.0) > 1e-6]
            pool = non_def or [v["elo"] for v in merged_st.values()]
            elo_val = round(
                _safe_finite_float(sum(pool) / max(1, len(pool)), 1450.0, lo=ELO_MIN, hi=ELO_MAX),
                1,
            )

        lu_vals = [
            str(x)
            for x in [
                (da or {}).get("lastUpdated"),
                (db or {}).get("lastUpdated"),
                *(v.get("lastUpdated") for v in merged_st.values()),
            ]
            if isinstance(x, str) and x.strip()
        ]
        last_upd = max(lu_vals, key=_ts_sort_key) if lu_vals else ""

        # Merge chapterPlacements per domain
        pl_a = (da or {}).get("chapterPlacements") or {}
        pl_b = (db or {}).get("chapterPlacements") or {}
        merged_pl: dict[str, Any] = {}
        for slug in sorted(set(pl_a.keys()) | set(pl_b.keys())):
            ra_p = pl_a.get(slug) if isinstance(pl_a.get(slug), dict) else None
            rb_p = pl_b.get(slug) if isinstance(pl_b.get(slug), dict) else None
            if ra_p and not rb_p:
                merged_pl[slug] = copy.deepcopy(ra_p)
            elif rb_p and not ra_p:
                merged_pl[slug] = copy.deepcopy(rb_p)
            elif ra_p and rb_p:
                pka = (1 if ra_p.get("done") else 0, int(ra_p.get("probes") or 0), _ts_sort_key(ra_p.get("placedAt") or ""), _canonical_json(ra_p))
                pkb = (1 if rb_p.get("done") else 0, int(rb_p.get("probes") or 0), _ts_sort_key(rb_p.get("placedAt") or ""), _canonical_json(rb_p))
                merged_pl[slug] = copy.deepcopy(ra_p if pka >= pkb else rb_p)

        merged_domains[dom_name] = {
            "elo": elo_val,
            "n": total_n,
            "expertiseBand": _elo_to_band(elo_val),
            "lastUpdated": last_upd,
            "chapterPlacements": merged_pl,
            "deviceStates": merged_st,
        }

    # 4. Global stats weighted by per-device event counts
    gs_a = pa["globalStats"]
    gs_b = pb["globalStats"]
    gst_a = _materialize_global_device_states(gs_a, local_device_id)
    gst_b = _materialize_global_device_states(gs_b, remote_device_id)
    merged_gst = _merge_device_state_maps(gst_a, gst_b)

    total_events = max(
        sum(v["n"] for v in merged_gst.values()),
        sum(d["n"] for d in merged_domains.values()),
    )
    gst_n_sum = sum(v["n"] for v in merged_gst.values())
    if gst_n_sum > 0:
        global_elo = round(
            _safe_finite_float(
                sum(v["elo"] * v["n"] for v in merged_gst.values()) / gst_n_sum,
                1450.0,
                lo=ELO_MIN,
                hi=ELO_MAX,
            ),
            1,
        )
        af_ratio = round(
            _safe_finite_float(
                sum(v["analogyFormalismRatio"] * v["n"] for v in merged_gst.values()) / gst_n_sum,
                0.50,
                lo=0.0,
                hi=1.0,
            ),
            3,
        )
        lat_ewma = round(
            _safe_finite_float(
                sum(v["latencyEwma"] * v["n"] for v in merged_gst.values()) / gst_n_sum,
                1.0,
                lo=0.1,
                hi=10.0,
            ),
            3,
        )
    else:
        non_def_g = [v["elo"] for v in merged_gst.values() if abs(v["elo"] - 1450.0) > 1e-6]
        pool_g = non_def_g or [v["elo"] for v in merged_gst.values()]
        global_elo = round(
            _safe_finite_float(sum(pool_g) / max(1, len(pool_g)), 1450.0, lo=ELO_MIN, hi=ELO_MAX),
            1,
        )
        af_ratio = round(
            _safe_finite_float(
                sum(v["analogyFormalismRatio"] for v in merged_gst.values()) / max(1, len(merged_gst)),
                0.50,
                lo=0.0,
                hi=1.0,
            ),
            3,
        )
        lat_ewma = round(
            _safe_finite_float(
                sum(v["latencyEwma"] for v in merged_gst.values()) / max(1, len(merged_gst)),
                1.0,
                lo=0.1,
                hi=10.0,
            ),
            3,
        )

    pf_attempts = sum(v["pfAttempts"] for v in merged_gst.values())
    if pf_attempts > 0:
        pf_surv = round(sum(v["pfSurvivalRate"] * v["pfAttempts"] for v in merged_gst.values()) / pf_attempts, 4)
    else:
        pf_surv = round(sum(v["pfSurvivalRate"] for v in merged_gst.values()) / max(1, len(merged_gst)), 4)

    # 5. Recent events: deduped FIFO 20 sorted by `ts`
    ev_map: dict[str, dict[str, Any]] = {}
    for ev in list(pa.get("recentEvents") or []) + list(pb.get("recentEvents") or []):
        if not isinstance(ev, dict):
            continue
        ek = _recent_event_key(ev)
        if ek not in ev_map or _canonical_json(ev) > _canonical_json(ev_map[ek]):
            ev_map[ek] = copy.deepcopy(ev)
    merged_events = sorted(
        ev_map.values(),
        key=lambda e: (_ts_sort_key(e.get("ts") or ""), _canonical_json(e)),
    )[-MAX_RECENT_EVENTS:]

    # 6. Recompute affectiveState, pacingMultiplier, metacognition, failureDna & compiledPolicy cleanly
    merged_cal["sessionTargetMinutes"] = _safe_finite_float(
        merged_cal.get("sessionTargetMinutes"), 30.0, lo=5.0, hi=240.0
    )
    if "baselineLatencyMs" in merged_cal and merged_cal["baselineLatencyMs"] is not None:
        merged_cal["baselineLatencyMs"] = _safe_finite_float(
            merged_cal.get("baselineLatencyMs"), 22000.0, lo=1000.0, hi=300000.0
        )
    merged_hyper = resolve_calibrated_hyperparams({"calibratedHyperparams": merged_ch} if merged_ch else None)
    affect = classify_affective_state(
        merged_events,
        session_target_min=float(merged_cal["sessionTargetMinutes"]),
        fatigue_multiplier=merged_hyper["fatigueMultiplier"],
    )
    if affect["state"] == "bored_underchallenged":
        pacing = 1.25
    elif affect["state"] in ("overloaded_frustrated", "fatigued"):
        pacing = 0.80
    else:
        acc_w = affect.get("windowAccuracy")
        pacing = round(max(0.85, min(1.15, 0.85 + 0.30 * float(acc_w))), 2) if isinstance(acc_w, (int, float)) else 1.0

    updated_vals = [str(x) for x in (pa.get("updatedAt"), pb.get("updatedAt")) if isinstance(x, str) and x.strip()]
    merged_updated = max(updated_vals, key=_ts_sort_key) if updated_vals else now_iso()

    merged: dict[str, Any] = {
        "schemaVersion": pa["schemaVersion"],
        "updatedAt": merged_updated,
        "calibration": merged_cal,
        "userOverrides": merged_uo,
        "globalStats": {
            "elo": global_elo,
            "totalEvents": total_events,
            "priorWeight": _prior_weight(total_events),
            "analogyFormalismRatio": af_ratio,
            "pfAttempts": pf_attempts,
            "pfSurvivalRate": pf_surv,
            "latencyEwma": lat_ewma,
            "pacingMultiplier": pacing,
            "deviceStates": merged_gst,
        },
        "domains": merged_domains,
        "affectiveState": affect,
        "metacognition": _compute_metacognition(kg_dir, merged_events, merged_cal),
        "failureDna": _compute_failure_dna(kg_dir),
        "recentEvents": merged_events,
        "compiledPolicy": {},
    }
    if merged_ch is not None:
        merged["calibratedHyperparams"] = merged_ch
    merged["compiledPolicy"] = compile_teaching_policy(merged)
    return _compact_profile_for_disk(merged)


def _strip_profile_updated_at(prof: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(prof, dict):
        return {}
    return {k: v for k, v in prof.items() if k != "updatedAt"}


# ---------------------------------------------------------------------------
# Internal helpers for loading & verifying delta envelopes
# ---------------------------------------------------------------------------


def _load_delta_source(delta_source: Path | str | dict[str, Any]) -> tuple[dict[str, Any] | None, int, list[str]]:
    errors: list[str] = []
    if isinstance(delta_source, dict):
        envelope = delta_source["delta"] if (
            isinstance(delta_source.get("delta"), dict) and "payload" not in delta_source
        ) else delta_source
        try:
            raw_bytes = json.dumps(envelope, ensure_ascii=False, allow_nan=False).encode("utf-8")
        except Exception as exc:  # noqa: BLE001
            return None, 0, [f"Delta object contains non-finite numbers or is not JSON-serializable: {exc}"]
        if len(raw_bytes) > MAX_DELTA_BYTES:
            return None, len(raw_bytes), [f"Delta exceeds MAX_DELTA_BYTES ({len(raw_bytes)} > {MAX_DELTA_BYTES})."]
        return copy.deepcopy(envelope), len(raw_bytes), errors

    if isinstance(delta_source, (str, Path)):
        p = Path(delta_source).expanduser().resolve()
        if not p.exists() or not p.is_file():
            return None, 0, [f"Delta file not found: {p.name}"]
        try:
            size = p.stat().st_size
        except OSError as exc:
            return None, 0, [f"Cannot stat delta file: {exc}"]
        if size > MAX_DELTA_BYTES:
            return None, size, [f"Delta file exceeds MAX_DELTA_BYTES ({size} > {MAX_DELTA_BYTES})."]
        try:
            raw_bytes = p.read_bytes()
        except OSError as exc:
            return None, 0, [f"Cannot read delta file: {exc}"]
        if len(raw_bytes) > MAX_DELTA_BYTES:
            return None, len(raw_bytes), [f"Delta payload exceeds MAX_DELTA_BYTES ({len(raw_bytes)} > {MAX_DELTA_BYTES})."]
        try:
            parsed = json.loads(raw_bytes.decode("utf-8"), parse_constant=_reject_non_finite)
        except Exception as exc:  # noqa: BLE001
            return None, len(raw_bytes), [f"Malformed JSON in delta file: {exc}"]
        if not isinstance(parsed, dict):
            return None, len(raw_bytes), ["Delta root must be a JSON object."]
        return parsed, len(raw_bytes), errors

    return None, 0, [f"Unsupported delta_source type: {type(delta_source).__name__}"]


def _filter_since_rows(rows: list[dict[str, Any]], since_dt: datetime | None) -> list[dict[str, Any]]:
    if since_dt is None:
        return rows
    out: list[dict[str, Any]] = []
    for row in rows:
        ts_raw = _extract_row_ts(row)
        dt = _parse_iso_dt(ts_raw)
        if dt is None or dt >= since_dt:
            out.append(row)
    return out


# ---------------------------------------------------------------------------
# Public API 2: Export Causal Delta
# ---------------------------------------------------------------------------


def export_sync_delta(
    workspace: Path | str | None = None,
    out_path: Path | str | None = None,
    since: str | None = None,
    *,
    device_id: str | None = None,
) -> dict[str, Any]:
    """Export a tamper-evident `syntopik-delta-v1` JSON snapshot (full or filtered by `since`)."""
    ws, kg_dir, chapters_dir = _resolve_ws_kg_ch(workspace)
    since_dt: datetime | None = None
    since_norm: str | None = None
    if since is not None and str(since).strip():
        since_dt = _parse_iso_dt(since)
        if since_dt is None:
            raise ValueError(f"Invalid ISO-8601 timestamp for 'since': {since!r}")
        since_norm = since_dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")

    if device_id is not None and str(device_id).strip():
        dev_id = str(device_id).strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{3,63}", dev_id):
            raise ValueError(f"Invalid device_id {device_id!r}")
    else:
        dev_id = get_or_create_device_id(kg_dir)

    state_path = _assert_safe_child(kg_dir, SYNC_STATE_FILENAME)
    exported_at = now_iso()

    with workspace_lock(state_path):
        state = _load_sync_state(kg_dir)
        lamport = int(state.get("lamportClock") or 0) + 1

        propositions = _filter_since_rows(load_jsonl(kg_dir / "propositions.jsonl"), since_dt)
        chains = load_jsonl(kg_dir / "chains.jsonl")
        revlog = _filter_since_rows(load_jsonl(kg_dir / "revlog.jsonl"), since_dt)
        mistakes = _filter_since_rows(load_jsonl(kg_dir / "mistakes.jsonl"), since_dt)
        evidence_ledger = _filter_since_rows(load_jsonl(kg_dir / LEDGER_FILENAME), since_dt)
        viva_sessions = _filter_since_rows(load_jsonl(kg_dir / "viva-sessions.jsonl"), since_dt)
        forks = _filter_since_rows(load_jsonl(kg_dir / "forks.jsonl"), since_dt)
        arenas = _filter_since_rows(load_jsonl(kg_dir / "arenas.jsonl"), since_dt)
        derivations = _filter_since_rows(load_jsonl(kg_dir / "derivations.jsonl"), since_dt)

        trackers: dict[str, dict[str, Any]] = {}
        if chapters_dir.is_dir():
            for fp in sorted(chapters_dir.glob(f"*{TRACKER_SUFFIX}")):
                if not fp.is_file() or fp.is_symlink():
                    continue
                slug = fp.name[: -len(TRACKER_SUFFIX)]
                if not TRACKER_SLUG_RE.fullmatch(slug):
                    continue
                data = load_json(fp, default=None)
                if not isinstance(data, dict):
                    continue
                if since_dt is not None:
                    tdt = _parse_iso_dt(data.get("updatedAt"))
                    if tdt is not None and tdt < since_dt:
                        continue
                trackers[slug] = data

        prof_path = kg_dir / PROFILE_FILENAME
        learner_profile: dict[str, Any] | None = None
        if prof_path.is_file():
            raw_prof = load_profile(kg_dir)
            include_prof = True
            if since_dt is not None:
                pdt = _parse_iso_dt(raw_prof.get("updatedAt"))
                if pdt is not None and pdt < since_dt:
                    include_prof = False
            if include_prof:
                learner_profile = _prepare_profile_for_export(raw_prof, dev_id)

        payload: dict[str, Any] = {
            "propositions": propositions,
            "chains": chains,
            "revlog": revlog,
            "mistakes": mistakes,
            "evidenceLedger": evidence_ledger,
            "vivaSessions": viva_sessions,
            "forks": forks,
            "arenas": arenas,
            "derivations": derivations,
            "trackers": trackers,
            "learnerProfile": learner_profile,
        }

        counts: dict[str, int] = {
            "propositions": len(propositions),
            "chains": len(chains),
            "revlog": len(revlog),
            "mistakes": len(mistakes),
            "evidenceLedger": len(evidence_ledger),
            "vivaSessions": len(viva_sessions),
            "forks": len(forks),
            "arenas": len(arenas),
            "derivations": len(derivations),
            "trackers": len(trackers),
            "learnerProfile": 1 if learner_profile is not None else 0,
        }

        envelope: dict[str, Any] = {
            "schemaVersion": SCHEMA_VERSION,
            "format": DELTA_FORMAT,
            "deviceId": dev_id,
            "exportedAt": exported_at,
            "since": since_norm,
            "lamportClock": lamport,
            "counts": counts,
            "payload": payload,
        }
        content_sha = _compute_delta_sha256(envelope)
        envelope["contentSha256"] = content_sha

        serialized = json.dumps(envelope, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
        encoded_len = len(serialized.encode("utf-8"))
        if encoded_len > MAX_DELTA_BYTES:
            raise ValueError(f"Exported delta exceeds MAX_DELTA_BYTES ({encoded_len} > {MAX_DELTA_BYTES}).")

        saved_path: Path | None = None
        if out_path is not None and str(out_path).strip():
            saved_path = write_text_atomic(Path(out_path).expanduser().resolve(), serialized)

        # Persist updated Lamport clock & export timestamp
        state.setdefault("schemaVersion", SCHEMA_VERSION)
        state["deviceId"] = dev_id
        state["lamportClock"] = lamport
        state["lastExportedAt"] = exported_at
        state.setdefault("peerClocks", {})
        state.setdefault("mergedDeltaHashes", [])
        state["updatedAt"] = exported_at
        _save_sync_state(kg_dir, state)

    return {
        "ok": True,
        "workspace": str(ws),
        "path": str(saved_path) if saved_path else None,
        "outPath": str(saved_path) if saved_path else None,
        "bytes": encoded_len,
        "schemaVersion": SCHEMA_VERSION,
        "format": DELTA_FORMAT,
        "deviceId": dev_id,
        "exportedAt": exported_at,
        "since": since_norm,
        "lamportClock": lamport,
        "counts": counts,
        "contentSha256": content_sha,
        "delta": envelope,
    }


# ---------------------------------------------------------------------------
# Public API 3: Fail-Closed Integrity & Schema Verification
# ---------------------------------------------------------------------------


def verify_sync_delta(delta_source: Path | str | dict[str, Any]) -> dict[str, Any]:
    """Fail-closed verification of delta size limit, schema, tracker slugs, and mandatory `contentSha256`."""
    envelope, byte_len, load_errors = _load_delta_source(delta_source)
    if envelope is None or load_errors:
        return {
            "ok": False,
            "valid": False,
            "bytes": byte_len,
            "errors": load_errors or ["Unable to load delta source."],
        }

    errors: list[str] = []

    if envelope.get("schemaVersion") != SCHEMA_VERSION:
        errors.append(f"Unsupported schemaVersion: {envelope.get('schemaVersion')!r} (expected {SCHEMA_VERSION}).")
    if envelope.get("format") != DELTA_FORMAT:
        errors.append(f"Unsupported format: {envelope.get('format')!r} (expected {DELTA_FORMAT!r}).")

    dev_id = envelope.get("deviceId")
    if not isinstance(dev_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{3,63}", dev_id):
        errors.append(f"Invalid or missing deviceId: {dev_id!r}.")

    lamport = envelope.get("lamportClock")
    if not isinstance(lamport, int) or isinstance(lamport, bool) or lamport < 0:
        errors.append(f"Invalid lamportClock: {lamport!r}.")

    payload = envelope.get("payload")
    if not isinstance(payload, dict):
        errors.append("Missing or non-object 'payload'.")
    else:
        for key in _REQUIRED_PAYLOAD_KEYS:
            if key not in payload:
                errors.append(f"Missing required payload key: {key!r}.")
        for list_key in _PAYLOAD_LIST_KEYS:
            val = payload.get(list_key)
            if list_key in payload and not isinstance(val, list):
                errors.append(f"Payload key {list_key!r} must be a list.")
        trackers = payload.get("trackers")
        if "trackers" in payload:
            if not isinstance(trackers, dict):
                errors.append("Payload key 'trackers' must be an object mapping slug -> tracker.")
            else:
                for slug, tdata in trackers.items():
                    if not isinstance(slug, str) or not TRACKER_SLUG_RE.fullmatch(slug):
                        errors.append(f"Unsafe or invalid tracker slug rejected: {slug!r}.")
                    if not isinstance(tdata, dict):
                        errors.append(f"Tracker payload for slug {slug!r} must be an object.")
        lp = payload.get("learnerProfile")
        if "learnerProfile" in payload and lp is not None and not isinstance(lp, dict):
            errors.append("Payload key 'learnerProfile' must be an object or null.")

    non_finite_errs = _find_non_finite_issues(envelope)
    if non_finite_errs:
        errors.extend(non_finite_errs)

    actual_sha = envelope.get("contentSha256")
    try:
        computed_sha = _compute_delta_sha256(envelope)
    except ValueError as exc:
        computed_sha = ""
        errors.append(f"Delta contains non-finite JSON values: {exc}")
    if not isinstance(actual_sha, str) or not re.fullmatch(r"[0-9a-f]{64}", actual_sha):
        errors.append("Missing or malformed mandatory 'contentSha256'.")
    elif computed_sha and not hmac.compare_digest(actual_sha, computed_sha):
        errors.append(
            f"contentSha256 mismatch (tampered or corrupted delta: expected {computed_sha[:12]}..., got {actual_sha[:12]}...)."
        )

    ok = len(errors) == 0
    return {
        "ok": ok,
        "valid": ok,
        "schemaVersion": envelope.get("schemaVersion"),
        "format": envelope.get("format"),
        "deviceId": dev_id if isinstance(dev_id, str) else None,
        "exportedAt": envelope.get("exportedAt"),
        "since": envelope.get("since"),
        "lamportClock": lamport if isinstance(lamport, int) and not isinstance(lamport, bool) else None,
        "counts": envelope.get("counts") if isinstance(envelope.get("counts"), dict) else {},
        "contentSha256": actual_sha if isinstance(actual_sha, str) else None,
        "computedSha256": computed_sha,
        "bytes": byte_len,
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# Public API 4: Deterministic Conflict-Free CRDT Merge
# ---------------------------------------------------------------------------


def merge_sync_delta(
    workspace: Path | str,
    delta_source: Path | str | dict[str, Any],
    dry_run: bool = False,
) -> dict[str, Any]:
    """Verify and merge a `syntopik-delta-v1` envelope into `workspace` using convergent CRDT rules."""
    ver = verify_sync_delta(delta_source)
    if not ver["ok"]:
        return {
            "ok": False,
            "dryRun": bool(dry_run),
            "added": 0,
            "updated": 0,
            "errors": ver["errors"],
            "verification": ver,
        }

    envelope, _byte_len, _errs = _load_delta_source(delta_source)
    assert envelope is not None
    payload = envelope["payload"]
    remote_dev_id = str(envelope["deviceId"])
    remote_clock = int(envelope["lamportClock"])
    content_sha = str(envelope["contentSha256"])

    ws, kg_dir, chapters_dir = _resolve_ws_kg_ch(workspace)
    state_path = _assert_safe_child(kg_dir, SYNC_STATE_FILENAME)

    with workspace_lock(state_path):
        if dry_run:
            state = _load_sync_state(kg_dir)
            local_dev_id = str(state.get("deviceId") or f"dev-dryrun-{_canonical_sha256(str(ws))[:12]}")
        else:
            local_dev_id = get_or_create_device_id(kg_dir)
            state = _load_sync_state(kg_dir)

        collections_report: dict[str, dict[str, int]] = {}
        total_added = 0
        total_updated = 0

        # 1. Propositions
        props_path = _assert_safe_child(kg_dir, "propositions.jsonl")
        local_props = load_jsonl(props_path)
        inc_props = payload.get("propositions") or []
        merged_props, p_add, p_upd = _merge_propositions(local_props, inc_props)
        collections_report["propositions"] = {"added": p_add, "updated": p_upd, "total": len(merged_props)}
        total_added += p_add
        total_updated += p_upd
        if not dry_run and (p_add > 0 or p_upd > 0 or (inc_props and _canonical_json(merged_props) != _canonical_json(local_props))):
            save_jsonl_atomic(props_path, merged_props)

        # 2. Chains
        chains_path = _assert_safe_child(kg_dir, "chains.jsonl")
        local_chains = load_jsonl(chains_path)
        inc_chains = payload.get("chains") or []
        merged_chains, c_add, c_upd = _merge_chains(local_chains, inc_chains)
        collections_report["chains"] = {"added": c_add, "updated": c_upd, "total": len(merged_chains)}
        total_added += c_add
        total_updated += c_upd
        if not dry_run and (c_add > 0 or c_upd > 0 or (inc_chains and _canonical_json(merged_chains) != _canonical_json(local_chains))):
            save_jsonl_atomic(chains_path, merged_chains)

        # 3-8. Append-only G-Set Ledgers
        gset_specs = (
            ("revlog", "revlog.jsonl"),
            ("mistakes", "mistakes.jsonl"),
            ("vivaSessions", "viva-sessions.jsonl"),
            ("forks", "forks.jsonl"),
            ("arenas", "arenas.jsonl"),
            ("derivations", "derivations.jsonl"),
        )
        for pkey, fname in gset_specs:
            fpath = _assert_safe_child(kg_dir, fname)
            local_rows = load_jsonl(fpath)
            inc_rows = payload.get(pkey) or []
            merged_rows, g_add, g_upd = _merge_gset_ledger(pkey, local_rows, inc_rows)
            collections_report[pkey] = {"added": g_add, "updated": g_upd, "total": len(merged_rows)}
            total_added += g_add
            total_updated += g_upd
            if not dry_run and (g_add > 0 or g_upd > 0 or (inc_rows and _canonical_json(merged_rows) != _canonical_json(local_rows))):
                save_jsonl_atomic(fpath, merged_rows)

        # 9. Evidence Ledger (deterministic re-chaining + SHA-256 chain check)
        ev_path = _assert_safe_child(kg_dir, LEDGER_FILENAME)
        local_ev = load_jsonl(ev_path)
        merged_ev, e_add, e_upd = _merge_evidence_ledger(local_ev, payload.get("evidenceLedger") or [])
        collections_report["evidenceLedger"] = {"added": e_add, "updated": e_upd, "total": len(merged_ev)}
        total_added += e_add
        total_updated += e_upd
        if not dry_run and (e_add > 0 or e_upd > 0 or (merged_ev and _canonical_json(merged_ev) != _canonical_json(local_ev))):
            save_jsonl_atomic(ev_path, merged_ev)
        if not dry_run:
            ledger_integrity = verify_ledger_chain(kg_dir)
        else:
            ledger_integrity = {
                "ok": True,
                "count": len(merged_ev),
                "brokenAt": None,
                "headHash": str(merged_ev[-1]["entryHash"]) if merged_ev else GENESIS_HASH,
                "errors": [],
            }

        # 10. Chapter Trackers (strict slug validation + monotonic join-semilattice)
        incoming_trackers = payload.get("trackers") or {}
        t_add = 0
        t_upd = 0
        tracker_details: dict[str, str] = {}
        for raw_slug in sorted(incoming_trackers.keys()):
            slug = _validate_tracker_slug(raw_slug)
            inc_tracker = incoming_trackers[slug]
            tpath = _assert_safe_child(chapters_dir, f"{slug}{TRACKER_SUFFIX}")
            local_tracker = load_json(tpath, default=None)
            if not isinstance(local_tracker, dict):
                # Canonicalize new tracker via self-merge so ordering/milestones are deterministic
                merged_tracker = _merge_tracker(inc_tracker, inc_tracker)
                t_add += 1
                tracker_details[slug] = "added"
                if not dry_run:
                    save_json_atomic(tpath, merged_tracker)
            else:
                merged_tracker = _merge_tracker(local_tracker, inc_tracker)
                if _canonical_json(merged_tracker) != _canonical_json(local_tracker):
                    t_upd += 1
                    tracker_details[slug] = "updated"
                    if not dry_run:
                        save_json_atomic(tpath, merged_tracker)
                else:
                    tracker_details[slug] = "unchanged"

        existing_tracker_count = (
            len([p for p in chapters_dir.glob(f"*{TRACKER_SUFFIX}") if p.is_file()])
            if chapters_dir.is_dir()
            else 0
        )
        collections_report["trackers"] = {
            "added": t_add,
            "updated": t_upd,
            "total": existing_tracker_count if not dry_run else existing_tracker_count + t_add,
        }
        total_added += t_add
        total_updated += t_upd

        # 11. Learner Profile (after revlog & mistakes are on disk so metacognition/DNA reflect merged logs)
        prof_path = _assert_safe_child(kg_dir, PROFILE_FILENAME)
        has_local_prof = prof_path.is_file()
        local_prof = load_profile(kg_dir) if has_local_prof else None
        remote_prof = payload.get("learnerProfile")
        lp_add = 0
        lp_upd = 0
        if isinstance(remote_prof, dict):
            merged_prof = _merge_learner_profiles(
                local_prof,
                remote_prof,
                local_device_id=local_dev_id,
                remote_device_id=remote_dev_id,
                kg_dir=kg_dir,
            )
            if merged_prof is not None:
                if not has_local_prof:
                    lp_add = 1
                elif _canonical_json(_strip_profile_updated_at(merged_prof)) != _canonical_json(
                    _strip_profile_updated_at(_compact_profile_for_disk(local_prof))  # type: ignore[arg-type]
                ):
                    lp_upd = 1
                if not dry_run and (lp_add > 0 or lp_upd > 0):
                    pretty_size = len(json.dumps(merged_prof, indent=2, ensure_ascii=False, allow_nan=False).encode("utf-8"))
                    save_json_atomic(prof_path, merged_prof, indent=2 if pretty_size <= 7600 else None)

        collections_report["learnerProfile"] = {
            "added": lp_add,
            "updated": lp_upd,
            "total": 1 if (has_local_prof or lp_add > 0) else 0,
        }
        total_added += lp_add
        total_updated += lp_upd

        local_clock = int(state.get("lamportClock") or 0)
        new_clock = max(local_clock, remote_clock) + (1 if (total_added > 0 or total_updated > 0) else 0)
        if not dry_run:
            peer_clocks = dict(state.get("peerClocks") or {})
            peer_clocks[remote_dev_id] = max(int(peer_clocks.get(remote_dev_id) or 0), remote_clock)
            merged_hashes = list(state.get("mergedDeltaHashes") or [])
            if content_sha not in merged_hashes:
                merged_hashes.append(content_sha)
            merged_hashes = merged_hashes[-200:]
            merged_at = now_iso()
            state.setdefault("schemaVersion", SCHEMA_VERSION)
            state["deviceId"] = local_dev_id
            state["lamportClock"] = new_clock
            state["lastMergedAt"] = merged_at
            state["peerClocks"] = peer_clocks
            state["mergedDeltaHashes"] = merged_hashes
            state["updatedAt"] = merged_at
            _save_sync_state(kg_dir, state)

    return {
        "ok": True,
        "dryRun": bool(dry_run),
        "workspace": str(ws),
        "localDeviceId": local_dev_id,
        "sourceDeviceId": remote_dev_id,
        "lamportClock": new_clock,
        "contentSha256": content_sha,
        "added": total_added,
        "updated": total_updated,
        "idempotentNoop": (total_added == 0 and total_updated == 0),
        "collections": collections_report,
        "trackerDetails": tracker_details,
        "ledgerIntegrity": ledger_integrity,
    }


# ---------------------------------------------------------------------------
# Public API 5: Sync Status Inspection (Read-Only)
# ---------------------------------------------------------------------------


def get_sync_status(workspace: Path | str | None = None) -> dict[str, Any]:
    """Return read-only multi-device sync status, local counts, and evidence ledger integrity."""
    ws, kg_dir, chapters_dir = _resolve_ws_kg_ch(workspace)
    state_path = _assert_safe_child(kg_dir, SYNC_STATE_FILENAME)
    initialized = state_path.is_file()
    state = _load_sync_state(kg_dir) if initialized else {}

    tracker_slugs: list[str] = []
    if chapters_dir.is_dir():
        for fp in sorted(chapters_dir.glob(f"*{TRACKER_SUFFIX}")):
            if fp.is_file() and not fp.is_symlink():
                slug = fp.name[: -len(TRACKER_SUFFIX)]
                if TRACKER_SLUG_RE.fullmatch(slug):
                    tracker_slugs.append(slug)

    counts = {
        "propositions": len(load_jsonl(kg_dir / "propositions.jsonl")),
        "chains": len(load_jsonl(kg_dir / "chains.jsonl")),
        "revlog": len(load_jsonl(kg_dir / "revlog.jsonl")),
        "mistakes": len(load_jsonl(kg_dir / "mistakes.jsonl")),
        "evidenceLedger": len(load_jsonl(kg_dir / LEDGER_FILENAME)),
        "vivaSessions": len(load_jsonl(kg_dir / "viva-sessions.jsonl")),
        "forks": len(load_jsonl(kg_dir / "forks.jsonl")),
        "arenas": len(load_jsonl(kg_dir / "arenas.jsonl")),
        "derivations": len(load_jsonl(kg_dir / "derivations.jsonl")),
        "trackers": len(tracker_slugs),
        "learnerProfile": 1 if (kg_dir / PROFILE_FILENAME).is_file() else 0,
    }

    return {
        "ok": True,
        "workspace": str(ws),
        "format": DELTA_FORMAT,
        "schemaVersion": SCHEMA_VERSION,
        "initialized": initialized,
        "deviceId": state.get("deviceId"),
        "lamportClock": int(state.get("lamportClock") or 0),
        "lastExportedAt": state.get("lastExportedAt"),
        "lastMergedAt": state.get("lastMergedAt"),
        "peerClocks": dict(state.get("peerClocks") or {}),
        "mergedDeltaCount": len(state.get("mergedDeltaHashes") or []),
        "trackerSlugs": tracker_slugs,
        "counts": counts,
        "ledgerIntegrity": verify_ledger_chain(kg_dir),
    }
