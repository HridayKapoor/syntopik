#!/usr/bin/env python3
"""Automated Mistake Taxonomy & Personal Trap Analytics CLI.

Appends structured error records to `knowledge-graph/mistakes.jsonl`, computes
recurring category/chapter/proposition analytics, and exports chapter or global
trap sheets directly into `scripts/cheatsheet_pdf.py`-compatible JSON specs.

Importable helpers for other scripts (e.g., `scripts/exam_runner.py`):
- `VALID_CATEGORIES`
- `CATEGORY_META`
- `load_mistakes(kg_dir)`
- `append_mistake(kg_dir, ...)`
- `summarize_mistakes(rows, ...)`
- `build_cheatsheet_spec(rows, ...)`
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import date as date_cls, datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any
import uuid

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from syntopik.paths import resolve_kg_dir as _resolve_kg_dir_core, resolve_package_root, sync_hub  # noqa: E402
from syntopik.schemas import (  # noqa: E402
    MISTAKE_CATEGORY_ALIASES as CATEGORY_ALIASES,
    VALID_MISTAKE_CATEGORIES,
    VALID_MISTAKE_CATEGORIES_SET as VALID_CATEGORIES_SET,
    normalize_mistake_category as _normalize_cat_core,
)
from syntopik.storage import append_jsonl_locked, load_jsonl as _load_jsonl_core, write_text_atomic  # noqa: E402

DEFAULT_KG_DIR = ROOT / "knowledge-graph"
SYNC_SCRIPT = resolve_package_root() / "scripts" / "sync-graph.mjs"
MISTAKES_FILENAME = "mistakes.jsonl"
DEFAULT_EXAM_TAG = "Mastery & Pitfall Sheet"

VALID_CATEGORIES: list[str] = list(VALID_MISTAKE_CATEGORIES)


def normalize_category(raw: str) -> str:
    try:
        return _normalize_cat_core(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc

# Grounded in Newman's Error Analysis (NEA), Rohrer interleaving, and Kornell & Bjork errorful learning.
TIER_META: dict[str, dict[str, str]] = {
    "comprehension_model": {
        "label": "Comprehension & Model Selection",
        "strategy": "Run interleaved discrimination drills mixing lookalike surface problems requiring different laws, plus setup-only checks.",
    },
    "process_encoding": {
        "label": "Process & Encoding",
        "strategy": "Enforce pre-submit sign, SI-unit/dimensional, and limiting-case sanity checks instead of theory re-lectures.",
    },
    "executive_pacing": {
        "label": "Executive & Pacing",
        "strategy": "Apply 2-pass CBT triage: cap first pass at 180s, mark heavy derivations for review, and lock high-confidence marks first.",
    },
}

CATEGORY_META: dict[str, dict[str, str]] = {
    "concept_gap": {
        "label": "Concept Gap",
        "stage": "Comprehension",
        "tier": "comprehension_model",
        "remediation": "Rebuild the governing law from its unconditional root and test on interleaved variants.",
    },
    "misconception": {
        "label": "Misconception",
        "stage": "Mental Model",
        "tier": "comprehension_model",
        "remediation": "Contrast the intuitive false rule against a concrete counterexample and interleaved discrimination pair.",
    },
    "condition_missed": {
        "label": "Condition Missed",
        "stage": "Transformation / Validity",
        "tier": "comprehension_model",
        "remediation": "Check validity assumptions (ideal, reversible, small-angle, dilute $K_w$, rearrangement) before picking a formula.",
    },
    "diagram_misread": {
        "label": "Diagram Misread",
        "stage": "Reading / Visual",
        "tier": "comprehension_model",
        "remediation": "Redraw the FBD, circuit, optical axis, or stereocenter cleanly and label every constraint.",
    },
    "sign_error": {
        "label": "Sign Error",
        "stage": "Process / Convention",
        "tier": "process_encoding",
        "remediation": "Declare positive axes and work/heat/current sign conventions explicitly before substituting.",
    },
    "unit_trap": {
        "label": "Unit Trap",
        "stage": "Encoding / Dimensions",
        "tier": "process_encoding",
        "remediation": "Convert every given quantity to coherent SI units first and verify final dimensions.",
    },
    "algebra_slip": {
        "label": "Algebra Slip",
        "stage": "Process Execution",
        "tier": "process_encoding",
        "remediation": "Sanity-check intermediate expressions with boundary limits ($x \\to 0, \\infty$) and dimensional homogeneity.",
    },
    "careless": {
        "label": "Careless Slip",
        "stage": "Encoding / Verification",
        "tier": "process_encoding",
        "remediation": "Re-read what the prompt actually asks (NOT/incorrect, radius vs diameter, rounding, units) before locking in.",
    },
    "time_pressure": {
        "label": "Time Pressure",
        "stage": "Executive / Pacing",
        "tier": "executive_pacing",
        "remediation": "Cap first pass at 3 min; mark heavy derivations for review and secure high-confidence marks first.",
    },
    "edge_case_missed": {
        "label": "Edge Case Missed",
        "stage": "Comprehension / Boundary",
        "tier": "comprehension_model",
        "remediation": "Test empty/singleton inputs (n=0,1), duplicates, negative/zero values, and graph cycles/disconnects before submitting.",
    },
    "complexity_trap": {
        "label": "Complexity / TLE Trap",
        "stage": "Algorithm / Scaling",
        "tier": "comprehension_model",
        "remediation": "Check input constraints N against worst-case big-O and eliminate hidden O(N) slices/copies inside loops.",
    },
    "off_by_one": {
        "label": "Off-by-One / Bounds Slip",
        "stage": "Process / Invariant",
        "tier": "process_encoding",
        "remediation": "State loop/window invariants explicitly (half-open [lo, hi) vs closed [lo, hi]) and trace 2-element termination.",
    },
    "state_mutation_bug": {
        "label": "State / Aliasing Bug",
        "stage": "Process Execution",
        "tier": "process_encoding",
        "remediation": "Clone shared mutable collections before storing results, undo state on backtrack, and isolate memoization keys.",
    },
}

DEFAULT_QUICK_CHECKS: list[str] = [
    "Sign & axis check: declare positive direction and thermodynamic/circuit sign conventions before writing equations.",
    "Dimension & unit check: convert all inputs to coherent SI units and verify output dimensions.",
    "Validity boundary check: confirm ideal/reversible/small-angle/dilute-$K_w$/non-rearranging assumptions hold.",
    "Prompt re-read check: verify whether the question asks for correct vs incorrect, magnitude vs vector, or radius vs diameter.",
]


def resolve_kg_paths(kg_dir_arg: Path | str | None = None) -> tuple[Path, Path, bool]:
    """Resolve `(kg_dir, mistakes_file, is_default)` honoring SYNT_KG_DIR / MLH_KG_DIR / SYNT_WORKSPACE."""
    kg_dir = _resolve_kg_dir_core(explicit=kg_dir_arg)
    is_default = kg_dir == DEFAULT_KG_DIR.resolve()
    return kg_dir, kg_dir / MISTAKES_FILENAME, is_default


def load_mistakes(path_or_dir: Path | str | None = None) -> list[dict[str, Any]]:
    """Load mistake records from a knowledge-graph dir or JSONL file path."""
    if path_or_dir is None:
        _, file_path, _ = resolve_kg_paths(None)
    else:
        p = Path(path_or_dir).expanduser().resolve()
        file_path = p if p.suffix.lower() == ".jsonl" else (p / MISTAKES_FILENAME)
    return _load_jsonl_core(file_path)


def sync_viewer(kg_dir: Path | str | None = None) -> None:
    """Refresh `knowledge-graph/graph-data.js` quietly via `scripts/sync-graph.mjs`."""
    sync_hub(kg_dir=kg_dir, quiet=True)


def validate_date(date_str: str | None = None) -> str:
    """Return canonical `YYYY-MM-DD` date string (defaults to today UTC)."""
    if date_str is None or not str(date_str).strip():
        return datetime.now(timezone.utc).date().isoformat()
    raw = str(date_str).strip()
    try:
        parsed = datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError(f"Invalid date {raw!r}; expected YYYY-MM-DD.") from exc
    return parsed.isoformat()


def generate_mistake_id() -> str:
    """Generate a unique mistake ID matching `m-<8hex>`."""
    return f"m-{uuid.uuid4().hex[:8]}"


def _pick_str(
    explicit: str | None,
    alt_explicit: str | None,
    entry: dict[str, Any] | None,
    *entry_keys: str,
    default: str = "",
) -> str:
    for val in (explicit, alt_explicit):
        if val is not None:
            return str(val).strip()
    if entry is not None:
        for key in entry_keys:
            if key in entry and entry[key] is not None:
                return str(entry[key]).strip()
    return default


def append_mistake(
    kg_dir: Path | str | None = None,
    entry: dict[str, Any] | None = None,
    *,
    subject: str | None = None,
    chapter: str | None = None,
    category: str | None = None,
    trigger_cue: str | None = None,
    wrong_move: str | None = None,
    correct_move: str | None = None,
    prop_id: str | None = None,
    confused_with: str | None = None,
    source_ref: str | None = None,
    date: str | None = None,
    mistake_id: str | None = None,
    id: str | None = None,  # noqa: A002 - matches JSON schema key for kwargs callers
    propId: str | None = None,
    confusedWith: str | None = None,
    triggerCue: str | None = None,
    wrongMove: str | None = None,
    correctMove: str | None = None,
    sourceRef: str | None = None,
    no_sync: bool = False,
    sync: bool | None = None,
) -> dict[str, Any]:
    """Validate and append one mistake entry to `<kg_dir>/mistakes.jsonl`.

    Supports both dict style (`append_mistake(kg_dir, {...})`) and keyword style
    (`append_mistake(kg_dir, subject=..., chapter=..., category=..., ...)`).
    """
    resolved_dir, mistakes_file, _is_default = resolve_kg_paths(kg_dir)

    raw_category = _pick_str(category, None, entry, "category")
    rec_category = _normalize_cat_core(raw_category)

    rec_subject = _pick_str(subject, None, entry, "subject", default="General") or "General"
    rec_chapter = _pick_str(chapter, None, entry, "chapter")
    rec_trigger_cue = _pick_str(trigger_cue, triggerCue, entry, "triggerCue", "trigger_cue")
    rec_wrong_move = _pick_str(wrong_move, wrongMove, entry, "wrongMove", "wrong_move")
    rec_correct_move = _pick_str(correct_move, correctMove, entry, "correctMove", "correct_move")
    rec_prop_id = _pick_str(prop_id, propId, entry, "propId", "prop_id", default="")
    rec_confused_with = _pick_str(confused_with, confusedWith, entry, "confusedWith", "confused_with", default="")
    rec_source_ref = _pick_str(source_ref, sourceRef, entry, "sourceRef", "source_ref", default="")
    rec_date = validate_date(_pick_str(date, None, entry, "date", default="") or None)
    rec_id = _pick_str(id, mistake_id, entry, "id", "mistake_id", default="") or generate_mistake_id()

    missing = [
        name
        for name, val in (
            ("chapter", rec_chapter),
            ("triggerCue", rec_trigger_cue),
            ("wrongMove", rec_wrong_move),
            ("correctMove", rec_correct_move),
        )
        if not val
    ]
    if missing:
        raise ValueError(f"Missing required mistake field(s): {', '.join(missing)}")

    record: dict[str, Any] = {
        "id": rec_id,
        "date": rec_date,
        "subject": rec_subject,
        "chapter": rec_chapter,
        "propId": rec_prop_id,
        "category": rec_category,
        "triggerCue": rec_trigger_cue,
        "wrongMove": rec_wrong_move,
        "correctMove": rec_correct_move,
        "sourceRef": rec_source_ref,
    }
    if rec_confused_with:
        record["confusedWith"] = rec_confused_with

    append_jsonl_locked(mistakes_file, record)

    try:
        from syntopik.learner_model import record_telemetry_event

        record_telemetry_event(
            resolved_dir,
            domain=rec_subject,
            prop_id=rec_prop_id or None,
            kind="mistake",
            correct=False,
            difficulty=1500,
            confidence=None,
            duration_ms=None,
            hints_used=0,
            mistake_category=rec_category,
            pf_outcome=None,
            skipped=False,
        )
    except Exception:
        pass

    should_sync = (not no_sync) and (sync is not False)
    if should_sync:
        sync_viewer(resolved_dir)

    return record


def filter_mistakes(
    rows: list[dict[str, Any]],
    *,
    subject: str | None = None,
    chapter: str | None = None,
    days: int | None = None,
    today: str | None = None,
) -> list[dict[str, Any]]:
    """Filter mistake rows by optional subject, chapter, and rolling day window."""
    subj_norm = subject.strip().lower() if subject and subject.strip() else None
    chap_norm = chapter.strip().lower() if chapter and chapter.strip() else None

    cutoff: str | None = None
    if days is not None:
        if days < 0:
            raise ValueError("--days must be >= 0")
        ref_date = date_cls.fromisoformat(validate_date(today))
        cutoff = (ref_date - timedelta(days=days)).isoformat()

    filtered: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if subj_norm is not None and str(row.get("subject", "")).strip().lower() != subj_norm:
            continue
        if chap_norm is not None and str(row.get("chapter", "")).strip().lower() != chap_norm:
            continue
        if cutoff is not None:
            raw_date = str(row.get("date", "")).strip()[:10]
            if len(raw_date) != 10:
                continue
            try:
                datetime.strptime(raw_date, "%Y-%m-%d")
            except ValueError:
                continue
            if raw_date < cutoff:
                continue
        filtered.append(row)
    return filtered


def _dedupe_preserve(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in values:
        clean = " ".join(str(raw).split())
        if not clean:
            continue
        key = clean.lower()
        if key not in seen:
            seen.add(key)
            out.append(clean)
    return out


def summarize_mistakes(
    rows: list[dict[str, Any]],
    *,
    subject: str | None = None,
    chapter: str | None = None,
    days: int | None = None,
    top: int = 3,
    today: str | None = None,
) -> dict[str, Any]:
    """Compute structured personal mistake analytics from raw mistake rows."""
    filtered = filter_mistakes(rows, subject=subject, chapter=chapter, days=days, today=today)
    total = len(filtered)
    top_n = max(0, int(top))

    cat_counts: Counter[str] = Counter()
    subj_display: dict[str, str] = {}
    subj_counts: Counter[str] = Counter()
    chap_display: dict[tuple[str, str], tuple[str, str]] = {}
    chap_counts: Counter[tuple[str, str]] = Counter()
    chap_cat_counts: dict[tuple[str, str], Counter[str]] = {}

    prop_groups: dict[str, list[tuple[int, dict[str, Any]]]] = {}
    cc_groups: dict[tuple[str, str], list[tuple[int, dict[str, Any]]]] = {}
    confused_counter: Counter[tuple[str, str]] = Counter()

    for idx, row in enumerate(filtered):
        cat = str(row.get("category", "")).strip() or "unknown"
        subj = str(row.get("subject", "")).strip() or "General"
        chap = str(row.get("chapter", "")).strip() or "General"
        prop = str(row.get("propId", "")).strip()
        confused = str(row.get("confusedWith", "")).strip()

        cat_counts[cat] += 1

        subj_key = subj.lower()
        subj_display.setdefault(subj_key, subj)
        subj_counts[subj_key] += 1

        chap_key = (chap.lower(), subj_key)
        chap_display.setdefault(chap_key, (chap, subj))
        chap_counts[chap_key] += 1
        chap_cat_counts.setdefault(chap_key, Counter())[cat] += 1

        if prop:
            prop_groups.setdefault(prop.lower(), []).append((idx, row))
            if confused and confused.lower() != prop.lower():
                pair = (prop, confused) if prop <= confused else (confused, prop)
                confused_counter[pair] += 1
        cc_groups.setdefault((chap.lower(), cat), []).append((idx, row))

    by_category: list[dict[str, Any]] = []
    tier_counts: Counter[str] = Counter()
    for cat, cnt in sorted(cat_counts.items(), key=lambda kv: (-kv[1], kv[0])):
        meta = CATEGORY_META.get(cat, {})
        tier = meta.get("tier", "process_encoding")
        tier_counts[tier] += cnt
        by_category.append(
            {
                "category": cat,
                "label": meta.get("label", cat.replace("_", " ").title()),
                "stage": meta.get("stage", "General"),
                "tier": tier,
                "count": cnt,
                "pct": round((cnt / total) * 100.0, 1) if total > 0 else 0.0,
            }
        )

    by_tier: list[dict[str, Any]] = [
        {
            "tier": tier,
            "label": TIER_META.get(tier, {}).get("label", tier),
            "count": cnt,
            "pct": round((cnt / total) * 100.0, 1) if total > 0 else 0.0,
            "strategy": TIER_META.get(tier, {}).get("strategy", ""),
        }
        for tier, cnt in sorted(tier_counts.items(), key=lambda kv: (-kv[1], kv[0]))
    ]

    top_categories = by_category[:top_n]

    hottest_chapters: list[dict[str, Any]] = []
    for chap_key, cnt in sorted(
        chap_counts.items(),
        key=lambda kv: (-kv[1], chap_display[kv[0]][0].lower(), chap_display[kv[0]][1].lower()),
    ):
        ch_name, sb_name = chap_display[chap_key]
        top_cat = sorted(
            chap_cat_counts[chap_key].items(),
            key=lambda kv: (-kv[1], kv[0]),
        )[0][0]
        hottest_chapters.append(
            {
                "chapter": ch_name,
                "subject": sb_name,
                "count": cnt,
                "topCategory": top_cat,
            }
        )

    by_subject: dict[str, int] = {
        subj_display[k]: cnt
        for k, cnt in sorted(subj_counts.items(), key=lambda kv: (-kv[1], subj_display[kv[0]].lower()))
    }

    # Build recurring trap list (`count >= 2`) by propId first, then (chapter, category).
    repeat_traps: list[dict[str, Any]] = []
    covered_row_sets: set[frozenset[int]] = set()

    def _make_repeat_trap(kind: str, key: str, items: list[tuple[int, dict[str, Any]]], prop_val: str) -> dict[str, Any]:
        group_rows = [r for _, r in items]
        group_cats = Counter(str(r.get("category", "")).strip() or "unknown" for r in group_rows)
        primary_cat = sorted(group_cats.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
        primary_ch = str(group_rows[-1].get("chapter", "")).strip() or "General"
        primary_subj = str(group_rows[-1].get("subject", "")).strip() or "General"
        cues = _dedupe_preserve([str(r.get("triggerCue", "")) for r in group_rows])
        wrongs = _dedupe_preserve([str(r.get("wrongMove", "")) for r in group_rows])
        corrects = _dedupe_preserve([str(r.get("correctMove", "")) for r in group_rows])
        action_cue = (
            f"When you see: {cues[0]} -> {corrects[0]}"
            if cues and corrects
            else (corrects[0] if corrects else CATEGORY_META.get(primary_cat, {}).get("remediation", ""))
        )
        is_escalated = len(group_rows) >= 3
        trap_obj: dict[str, Any] = {
            "kind": kind,
            "key": key,
            "propId": prop_val,
            "chapter": primary_ch,
            "subject": primary_subj,
            "category": primary_cat,
            "count": len(group_rows),
            "escalated": is_escalated,
            "triggerCues": cues[:4],
            "wrongMoves": wrongs[:3],
            "correctMoves": corrects[:3],
            "actionCue": action_cue,
            "remediation": CATEGORY_META.get(primary_cat, {}).get("remediation", ""),
        }
        if is_escalated:
            trap_obj["escalationAction"] = (
                "3-strike escalation: queue targeted Bug-Hunt + unaided Blank-Sheet Delayed Retest in 4 days."
            )
        return trap_obj

    for _prop_key, items in prop_groups.items():
        if len(items) < 2:
            continue
        prop_val = str(items[0][1].get("propId", "")).strip()
        covered_row_sets.add(frozenset(i for i, _ in items))
        repeat_traps.append(_make_repeat_trap("propId", prop_val, items, prop_val))

    for (_ch_key, cat_val), items in cc_groups.items():
        if len(items) < 2:
            continue
        idx_set = frozenset(i for i, _ in items)
        if idx_set in covered_row_sets:
            continue
        ch_display_name = str(items[0][1].get("chapter", "")).strip() or "General"
        repeat_traps.append(
            _make_repeat_trap("chapter_category", f"{ch_display_name}::{cat_val}", items, "")
        )

    repeat_traps.sort(key=lambda t: (-t["count"], t["chapter"].lower(), t["category"], t["key"].lower()))
    escalations = [t for t in repeat_traps if t.get("escalated")]
    confused_pairs = [
        {"nodeA": a, "nodeB": b, "count": cnt}
        for (a, b), cnt in sorted(confused_counter.items(), key=lambda kv: (-kv[1], kv[0][0], kv[0][1]))
    ]

    remediation_focus = [
        {
            "category": item["category"],
            "label": item["label"],
            "stage": item["stage"],
            "remediation": CATEGORY_META.get(item["category"], {}).get("remediation", ""),
        }
        for item in top_categories
    ]

    recent_indexed = sorted(
        enumerate(filtered),
        key=lambda pair: (str(pair[1].get("date", "")), pair[0]),
        reverse=True,
    )
    recent = [row for _, row in recent_indexed[:8]]

    return {
        "total": total,
        "byCategory": by_category,
        "topCategories": top_categories,
        "byTier": by_tier,
        "hottestChapters": hottest_chapters,
        "bySubject": by_subject,
        "repeatTraps": repeat_traps,
        "escalations": escalations,
        "confusedPairs": confused_pairs,
        "remediationFocus": remediation_focus,
        "recent": recent,
        "filters": {
            "subject": subject,
            "chapter": chapter,
            "days": days,
            "top": top_n,
        },
    }


def _sanitize_typst_prose(text: str) -> str:
    """Preserve inline `$...$` / `$$...$$` / `` `...` `` while avoiding Typst comment hazards."""
    collapsed = " ".join(str(text).split())
    if not collapsed:
        return ""
    tokens = re.split(r"(\$\$.*?\$\$|\$[^$\n]+?\$|`[^`\n]+?`)", collapsed, flags=re.DOTALL)
    out: list[str] = []
    for tok in tokens:
        if not tok:
            continue
        if (
            (tok.startswith("$$") and tok.endswith("$$") and len(tok) > 4)
            or (tok.startswith("$") and tok.endswith("$") and len(tok) > 2)
            or (tok.startswith("`") and tok.endswith("`") and len(tok) > 2)
        ):
            out.append(tok)
        else:
            # Neutralize `//` in plain prose so Typst never treats URLs or double slashes as line comments.
            out.append(tok.replace("//", "/ /"))
    return "".join(out)


def _aggregate_unique_traps(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse duplicate `(chapter, category, triggerCue, wrongMove, correctMove)` traps with counts."""
    buckets: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
    for idx, row in enumerate(rows):
        ch = str(row.get("chapter", "")).strip() or "General"
        subj = str(row.get("subject", "")).strip() or "General"
        cat = str(row.get("category", "")).strip() or "unknown"
        cue = " ".join(str(row.get("triggerCue", "")).split())
        wrong = " ".join(str(row.get("wrongMove", "")).split())
        correct = " ".join(str(row.get("correctMove", "")).split())
        prop = str(row.get("propId", "")).strip()
        src = str(row.get("sourceRef", "")).strip()
        dt = str(row.get("date", "")).strip()
        key = (ch.lower(), cat, cue.lower(), wrong.lower(), correct.lower())
        if key not in buckets:
            buckets[key] = {
                "id": str(row.get("id", "")),
                "date": dt,
                "subject": subj,
                "chapter": ch,
                "propId": prop,
                "category": cat,
                "label": CATEGORY_META.get(cat, {}).get("label", cat.replace("_", " ").title()),
                "triggerCue": cue,
                "wrongMove": wrong,
                "correctMove": correct,
                "sourceRef": src,
                "occurrences": 1,
                "_lastIdx": idx,
            }
        else:
            b = buckets[key]
            b["occurrences"] += 1
            if dt >= b["date"]:
                b["date"] = dt
            if not b["propId"] and prop:
                b["propId"] = prop
            if not b["sourceRef"] and src:
                b["sourceRef"] = src
            b["_lastIdx"] = idx

    items = list(buckets.values())
    # Sort descending by occurrences, then descending by date & recency.
    items.sort(key=lambda x: (x["occurrences"], x["date"], x["_lastIdx"]), reverse=True)
    for item in items:
        item.pop("_lastIdx", None)
    return items


def build_cheatsheet_spec(
    rows: list[dict[str, Any]],
    *,
    chapter: str | None = None,
    subject: str | None = None,
    title: str | None = None,
    exam_tag: str | None = None,
) -> dict[str, Any]:
    """Build a 1–2 page `scripts/cheatsheet_pdf.py build` JSON spec from mistakes."""
    matched = filter_mistakes(rows, subject=subject, chapter=chapter)
    summary = summarize_mistakes(matched, top=3)
    total = summary["total"]
    resolved_exam_tag = (exam_tag or "").strip() or DEFAULT_EXAM_TAG

    subjects_present = list(summary["bySubject"].keys())
    chapters_present = [c["chapter"] for c in summary["hottestChapters"]]

    if subject and subject.strip():
        resolved_subject = subject.strip()
    elif len(subjects_present) == 1:
        resolved_subject = subjects_present[0]
    elif len(subjects_present) > 1:
        resolved_subject = " / ".join(subjects_present[:3])
    else:
        resolved_subject = "General"

    if title and title.strip():
        resolved_title = title.strip()
    elif chapter and chapter.strip():
        display_ch = chapters_present[0] if chapters_present else chapter.strip()
        resolved_title = f"{display_ch} — Personal Trap Sheet"
    elif subject and subject.strip():
        resolved_title = f"{resolved_subject} — Personal Trap Sheet"
    else:
        resolved_title = "Personal Mistake & Pitfall Revision Sheet"

    if total == 0:
        scope_desc = chapter.strip() if (chapter and chapter.strip()) else (resolved_subject if subject else "all chapters")
        return {
            "title": _sanitize_typst_prose(resolved_title),
            "subject": _sanitize_typst_prose(resolved_subject),
            "examTag": _sanitize_typst_prose(resolved_exam_tag),
            "subtitle": _sanitize_typst_prose(f"0 logged mistakes for {scope_desc} · Ready to populate"),
            "columns": 2,
            "sections": [
                {
                    "heading": "1. No Logged Traps Yet",
                    "bullets": [
                        "No mistakes match this filter yet.",
                        "Log a personal mistake via `mistake_analytics.py log` after practice or let `exam_runner.py grade` log timed-CBT misses automatically.",
                        "Re-run `mistake_analytics.py export-traps` once traps are recorded to compile a targeted 1-2 page revision sheet.",
                    ],
                }
            ],
            "traps": [],
            "quickChecks": list(DEFAULT_QUICK_CHECKS),
        }

    top_str = ", ".join(f"{c['label']} ({c['count']})" for c in summary["topCategories"])
    subtitle = f"{total} logged mistake{'s' if total != 1 else ''} · Top patterns: {top_str}"

    sections: list[dict[str, Any]] = []

    # Section 1: Compact Mistake Profile table.
    profile_rows = [
        [c["label"], str(c["count"]), f"{c['pct']}%", c["stage"]]
        for c in summary["byCategory"][:6]
    ]
    profile_bullets: list[str] = []
    if len(summary["hottestChapters"]) > 1:
        hot_str = ", ".join(f"{h['chapter']} ({h['count']})" for h in summary["hottestChapters"][:5])
        profile_bullets.append(f"**Hottest Chapters:** {_sanitize_typst_prose(hot_str)}")
    if summary["repeatTraps"]:
        rep_str = ", ".join(f"{r['key']} ({r['count']}x)" for r in summary["repeatTraps"][:4])
        profile_bullets.append(f"**Repeat Triggers:** {_sanitize_typst_prose(rep_str)}")

    sections.append(
        {
            "heading": "1. Personal Mistake Profile",
            "table": {
                "headers": ["Category", "Hits", "Share", "Cognitive Stage"],
                "rows": profile_rows,
            },
            "bullets": profile_bullets,
        }
    )

    unique_traps = _aggregate_unique_traps(matched)
    single_chapter_mode = bool(chapter and chapter.strip()) or len(chapters_present) <= 1

    sec_num = 2
    if single_chapter_mode:
        # Group by error category (highest frequency first, cap at 5 category boxes for 1-2 page fit).
        for cat_entry in summary["byCategory"][:5]:
            cat_code = cat_entry["category"]
            cat_label = cat_entry["label"]
            cat_count = cat_entry["count"]
            cat_traps = [t for t in unique_traps if t["category"] == cat_code]
            bullets: list[str] = []
            sec_traps: list[str] = []
            for t in cat_traps[:5]:
                occ = f" ({t['occurrences']}x)" if t["occurrences"] > 1 else ""
                prop_tag = f" `{t['propId']}`" if t["propId"] else ""
                cue = _sanitize_typst_prose(t["triggerCue"])
                fix = _sanitize_typst_prose(t["correctMove"])
                wrong = _sanitize_typst_prose(t["wrongMove"])
                bullets.append(f"**Cue{occ}:** {cue}{prop_tag} — **Fix:** {fix}")
                if len(sec_traps) < 3:
                    sec_traps.append(f"**Avoid:** {wrong} — **Instead:** {fix}")
            sections.append(
                {
                    "heading": f"{sec_num}. {_sanitize_typst_prose(cat_label)} ({cat_count}x)",
                    "bullets": bullets,
                    "traps": sec_traps,
                }
            )
            sec_num += 1
    else:
        # Multi-chapter export: group by hottest chapters (top 5 chapters).
        for hot in summary["hottestChapters"][:5]:
            ch_name = hot["chapter"]
            ch_subj = hot["subject"]
            ch_traps = [
                t
                for t in unique_traps
                if t["chapter"].lower() == ch_name.lower() and t["subject"].lower() == ch_subj.lower()
            ]
            bullets = []
            sec_traps = []
            for t in ch_traps[:4]:
                occ = f" {t['occurrences']}x" if t["occurrences"] > 1 else ""
                cue = _sanitize_typst_prose(t["triggerCue"])
                fix = _sanitize_typst_prose(t["correctMove"])
                wrong = _sanitize_typst_prose(t["wrongMove"])
                bullets.append(f"**[{_sanitize_typst_prose(t['label'])}{occ}]** {cue} — **Fix:** {fix}")
                if len(sec_traps) < 2:
                    sec_traps.append(f"**Avoid:** {wrong} — **Instead:** {fix}")
            sections.append(
                {
                    "heading": f"{sec_num}. {_sanitize_typst_prose(ch_name)} ({hot['count']}x)",
                    "bullets": bullets,
                    "traps": sec_traps,
                }
            )
            sec_num += 1

    # Global crimson trap callouts: highlight repeat traps first (deduped by cue+wrongMove), capped cleanly for balanced 2-column fit.
    global_traps: list[str] = []
    seen_pairs: set[tuple[str, str]] = set()
    for rep in summary["repeatTraps"][:4]:
        lbl = CATEGORY_META.get(rep["category"], {}).get("label", rep["category"])
        raw_cue = rep["triggerCues"][0] if rep["triggerCues"] else rep["key"]
        raw_wrong = rep["wrongMoves"][0] if rep["wrongMoves"] else ""
        raw_fix = rep["correctMoves"][0] if rep["correctMoves"] else rep["remediation"]
        pair_key = (raw_cue.lower(), raw_wrong.lower())
        if pair_key in seen_pairs:
            continue
        seen_pairs.add(pair_key)
        cue = _sanitize_typst_prose(raw_cue)
        wrong = _sanitize_typst_prose(raw_wrong)
        fix = _sanitize_typst_prose(raw_fix)
        line = (
            f"**[REPEAT {rep['count']}x · {_sanitize_typst_prose(rep['chapter'])} · {_sanitize_typst_prose(lbl)}]** "
            f"{cue} — **Avoid:** {wrong}. **Fix:** {fix}"
            if wrong
            else f"**[REPEAT {rep['count']}x · {_sanitize_typst_prose(rep['chapter'])} · {_sanitize_typst_prose(lbl)}]** {cue} — **Fix:** {fix}"
        )
        global_traps.append(line)

    target_global_cap = 3 if len(sections) >= 3 else 5
    for t in unique_traps:
        if len(global_traps) >= target_global_cap:
            break
        pair_key = (t["triggerCue"].lower(), t["wrongMove"].lower())
        if pair_key in seen_pairs:
            continue
        seen_pairs.add(pair_key)
        cue = _sanitize_typst_prose(t["triggerCue"])
        wrong = _sanitize_typst_prose(t["wrongMove"])
        fix = _sanitize_typst_prose(t["correctMove"])
        global_traps.append(
            f"**[{_sanitize_typst_prose(t['label'])}]** {cue} — **Avoid:** {wrong}. **Fix:** {fix}"
        )

    # Tailored quickChecks from the learner's top recurring categories.
    quick_checks: list[str] = []
    seen_qc: set[str] = set()
    for item in summary["byCategory"][:5]:
        meta = CATEGORY_META.get(item["category"])
        if meta:
            qc = f"**{meta['label']}:** {meta['remediation']}"
            if qc not in seen_qc:
                seen_qc.add(qc)
                quick_checks.append(qc)
    for fallback_qc in DEFAULT_QUICK_CHECKS:
        if len(quick_checks) >= 4:
            break
        if fallback_qc not in seen_qc:
            seen_qc.add(fallback_qc)
            quick_checks.append(fallback_qc)

    return {
        "title": _sanitize_typst_prose(resolved_title),
        "subject": _sanitize_typst_prose(resolved_subject),
        "examTag": _sanitize_typst_prose(resolved_exam_tag),
        "subtitle": _sanitize_typst_prose(subtitle),
        "columns": 2,
        "sections": sections,
        "traps": global_traps,
        "quickChecks": quick_checks,
    }


def _format_summary_text(summary: dict[str, Any]) -> str:
    total = summary["total"]
    if total == 0:
        return "Mistake Analytics Summary: 0 mistakes logged matching filter."

    lines = [f"Mistake Analytics Summary ({total} logged):"]
    top_cats = summary["topCategories"]
    if top_cats:
        lines.append("  Top Categories:")
        for c in top_cats:
            lines.append(f"    - {c['category']} ({c['label']}): {c['count']} ({c['pct']}%) [{c['stage']}]")
    if summary["hottestChapters"]:
        lines.append("  Hottest Chapters:")
        for ch in summary["hottestChapters"][:5]:
            lines.append(
                f"    - {ch['chapter']} ({ch['subject']}): {ch['count']} (top: {ch['topCategory']})"
            )
    if summary["bySubject"]:
        subj_str = ", ".join(f"{k}: {v}" for k, v in summary["bySubject"].items())
        lines.append(f"  By Subject: {subj_str}")
    if summary["repeatTraps"]:
        lines.append("  Repeat Traps (2+ occurrences):")
        for r in summary["repeatTraps"][:5]:
            lines.append(f"    - [{r['count']}x {r['category']}] {r['key']}: {r['actionCue']}")
    if summary["remediationFocus"]:
        lines.append("  Recommended Focus:")
        for rf in summary["remediationFocus"]:
            lines.append(f"    - {rf['label']}: {rf['remediation']}")
    return "\n".join(lines)


def cmd_log(args: argparse.Namespace) -> None:
    try:
        record = append_mistake(
            args.kg_dir,
            subject=args.subject,
            chapter=args.chapter,
            category=args.category,
            trigger_cue=args.trigger_cue,
            wrong_move=args.wrong_move,
            correct_move=args.correct_move,
            prop_id=args.prop_id,
            confused_with=getattr(args, "confused_with", ""),
            source_ref=args.source_ref,
            date=args.date,
            mistake_id=args.id,
            no_sync=args.no_sync,
        )
    except ValueError as exc:
        raise SystemExit(f"Error: {exc}") from exc

    if args.json:
        print(json.dumps(record, indent=2, ensure_ascii=False))
    else:
        print(
            f"Logged {record['id']} [{record['category']}] ({record['subject']} · {record['chapter']}): "
            f"{record['triggerCue']} -> {record['correctMove']}"
        )


def cmd_summary(args: argparse.Namespace) -> None:
    _kg_dir, mistakes_file, _is_default = resolve_kg_paths(args.kg_dir)
    rows = load_mistakes(mistakes_file)
    try:
        summary = summarize_mistakes(
            rows,
            subject=args.subject,
            chapter=args.chapter,
            days=args.days,
            top=args.top,
        )
    except ValueError as exc:
        raise SystemExit(f"Error: {exc}") from exc

    if args.json:
        print(json.dumps(summary, indent=2, ensure_ascii=False))
    else:
        print(_format_summary_text(summary))


def cmd_export_traps(args: argparse.Namespace) -> None:
    if args.chapter and args.all:
        raise SystemExit("Error: pass either --chapter <name> or --all, not both.")

    _kg_dir, mistakes_file, _is_default = resolve_kg_paths(args.kg_dir)
    rows = load_mistakes(mistakes_file)
    chapter_filter = None if args.all else args.chapter
    matched = filter_mistakes(rows, subject=args.subject, chapter=chapter_filter)

    if args.format == "cheatsheet":
        payload: dict[str, Any] = build_cheatsheet_spec(
            rows,
            chapter=chapter_filter,
            subject=args.subject,
            title=args.title,
            exam_tag=getattr(args, "exam_tag", None),
        )
    else:
        traps = _aggregate_unique_traps(matched)
        payload = {
            "title": args.title or (f"{chapter_filter} Traps" if chapter_filter else "All Logged Traps"),
            "filter": {
                "chapter": chapter_filter,
                "subject": args.subject,
                "all": bool(args.all or not chapter_filter),
            },
            "total": len(matched),
            "uniqueCount": len(traps),
            "traps": traps,
        }

    encoded = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    if args.out:
        out_path = Path(args.out).expanduser().resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(encoded, encoding="utf-8")
        print(
            json.dumps(
                {
                    "format": args.format,
                    "matchedMistakes": len(matched),
                    "title": payload.get("title", ""),
                    "output": str(out_path),
                },
                indent=2,
                ensure_ascii=False,
            )
        )
    else:
        sys.stdout.write(encoded)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Automated Mistake Taxonomy & Personal Trap Analytics CLI"
    )
    parser.add_argument(
        "--kg-dir",
        default=None,
        help="Override knowledge-graph directory (for sandboxed testing)",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    # log
    p_log = sub.add_parser("log", help="Append a structured mistake entry to mistakes.jsonl")
    p_log.add_argument("--subject", required=True, help="Subject (e.g., Computer Science, Physics, Chemistry, Maths, General)")
    p_log.add_argument("--chapter", required=True, help="Chapter/topic name (e.g., Dynamic Programming, Work-Energy)")
    p_log.add_argument(
        "--category",
        required=True,
        type=normalize_category,
        metavar="{" + ",".join(VALID_CATEGORIES) + "}",
        help="Mistake taxonomy category (Newman + CS coding categories/aliases)",
    )
    p_log.add_argument("--trigger-cue", required=True, help="Problem cue or condition that triggered the trap")
    p_log.add_argument("--wrong-move", required=True, help="What went wrong / erroneous step taken")
    p_log.add_argument("--correct-move", required=True, help="Correct invariant, check, or procedure")
    p_log.add_argument("--prop-id", default="", help="Optional Knowledge Graph proposition ID")
    p_log.add_argument("--confused-with", default="", help="Optional confusable lookalike proposition ID")
    p_log.add_argument("--source-ref", default="", help="Optional source/PYQ/exam reference")
    p_log.add_argument("--date", default=None, help="Override date (YYYY-MM-DD, default: today UTC)")
    p_log.add_argument("--id", default=None, help="Override mistake ID (default: m-<8hex>)")
    p_log.add_argument("--kg-dir", default=argparse.SUPPRESS, help="Override knowledge-graph directory")
    p_log.add_argument("--no-sync", action="store_true", help="Skip syncing dashboard/viewer graph-data.js")
    p_log.add_argument("--json", action="store_true", help="Emit logged record as JSON")
    p_log.set_defaults(func=cmd_log)

    # summary
    p_sum = sub.add_parser("summary", help="Summarize recurring mistake categories, hottest chapters, and repeat traps")
    p_sum.add_argument("--subject", default=None, help="Filter by subject (case-insensitive)")
    p_sum.add_argument("--chapter", default=None, help="Filter by chapter (case-insensitive)")
    p_sum.add_argument("--days", type=int, default=None, help="Include only mistakes from the last N days")
    p_sum.add_argument("--top", type=int, default=3, help="Number of top categories to highlight (default: 3)")
    p_sum.add_argument("--kg-dir", default=argparse.SUPPRESS, help="Override knowledge-graph directory")
    p_sum.add_argument("--json", action="store_true", help="Emit analytics summary as JSON")
    p_sum.set_defaults(func=cmd_summary)

    # export-traps
    p_exp = sub.add_parser(
        "export-traps",
        help="Export personal traps as a cheatsheet_pdf.py JSON spec or structured trap list",
    )
    p_exp.add_argument("--chapter", default=None, help="Filter traps to a specific chapter")
    p_exp.add_argument("--all", action="store_true", help="Export traps across all chapters")
    p_exp.add_argument("--subject", default=None, help="Optional subject filter")
    p_exp.add_argument("--out", default=None, help="Write JSON output to this file path (default: stdout)")
    p_exp.add_argument(
        "--format",
        choices=["cheatsheet", "list"],
        default="cheatsheet",
        help="Output format: cheatsheet (cheatsheet_pdf.py JSON spec) or list (default: cheatsheet)",
    )
    p_exp.add_argument("--kg-dir", default=argparse.SUPPRESS, help="Override knowledge-graph directory")
    p_exp.add_argument("--title", default=None, help="Custom sheet title")
    p_exp.add_argument("--exam-tag", default=None, help=f"Custom sheet header badge tag (default: '{DEFAULT_EXAM_TAG}')")
    p_exp.set_defaults(func=cmd_export_traps)

    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
