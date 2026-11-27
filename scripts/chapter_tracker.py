#!/usr/bin/env python3
"""Syntopik — Universal Topic & Chapter Autopilot Tracker & State-Machine CLI.

Tracks end-to-end mastery across any subject or domain (CS, ML/AI, Systems,
Physics, Chemistry, Maths, Biology, Economics, university courses, papers, or
competitive exams) with multi-source indexing (whiteboard/lecture PDFs, slides,
papers, textbooks, code repos, problem sets, official docs, bounded web
research), Scope Charters, deduplicated question ledgers, spaced fresh-surface
retests, profile-aware milestones, and nested doubt breadcrumbs — 100% offline
using pure Python stdlib.

Importable helpers for other scripts (e.g., `scripts/daily_plan.py`):
- `resolve_chapters_dir(chapters_dir=None)`
- `resolve_kg_dir(kg_dir=None)`
- `normalize_subject(value)`
- `normalize_level(value)`
- `is_jee_profile(tracker)`
- `list_trackers(chapters_dir=None)`
- `find_active_tracker(chapters_dir=None)`
- `load_tracker(slug_or_path, chapters_dir=None)`
- `save_tracker(tracker, chapters_dir=None, *, sync=False)`
- `compute_stats(tracker)`
- `compute_next_action(tracker, kg_dir=None, include_warmup=False)`
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
import os
from pathlib import Path
import re
import sys
from typing import Any

SCHEMA_VERSION = 1

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from syntopik.paths import (  # noqa: E402
    resolve_chapters_dir as _resolve_chapters_dir_core,
    resolve_kg_dir as _resolve_kg_dir_core,
    resolve_package_root,
    sync_hub,
)
from syntopik.schemas import (  # noqa: E402
    LEVEL_RANK,
    TARGET_DEPTH_ALIASES,
    VALID_GOAL_PROFILES,
    VALID_LEVELS,
    VALID_MISTAKE_CATEGORIES,
    VALID_TARGET_DEPTHS,
    _has_jee_source_or_title_cues,
    is_jee_profile,
    normalize_level,
    normalize_level_soft,
    normalize_mistake_category as _normalize_mistake_category_core,
    normalize_subject as _normalize_subject_core,
    now_iso,
    today_iso,
)
from syntopik.storage import (  # noqa: E402
    load_json as _load_json_core,
    load_jsonl as _load_jsonl,
    save_json_atomic as _write_json_atomic,
)

DEFAULT_CHAPTERS_DIR = ROOT / "chapters"
DEFAULT_KG_DIR = ROOT / "knowledge-graph"
SYNC_SCRIPT = resolve_package_root() / "scripts" / "sync-graph.mjs"
TRACKER_SUFFIX = "-tracker.json"

VALID_SLICE_STAGES: tuple[str, ...] = ("queued", "extracted", "clozed", "crystallized")

VALID_SOURCE_KINDS: tuple[str, ...] = (
    "whiteboard_pdf",
    "textbook_pdf",
    "module_pdf",
    "pyq_pdf",
    "pyq_bank",
    "notes",
    "slides_pdf",
    "paper_pdf",
    "code_repo",
    "problem_set",
    "web_research",
    "syllabus",
    "official_docs",
)
CODE_FILE_EXTS: frozenset[str] = frozenset(
    {".py", ".js", ".ts", ".tsx", ".rs", ".cpp", ".c", ".h", ".java", ".go", ".ipynb", ".sql"}
)

VALID_TOPIC_STATUSES: tuple[str, ...] = ("pending", "probed", "in_progress", "mastered")
VALID_QUESTION_KINDS: tuple[str, ...] = (
    "mcq",
    "msq",
    "numerical",
    "subjective",
    "matrix",
    "coding",
    "proof",
    "debug",
    "short_answer",
)
VALID_QUESTION_STATUSES: tuple[str, ...] = (
    "unseen",
    "in_progress",
    "solved",
    "retest",
    "mastered",
    "skipped",
)
DONE_QUESTION_STATUSES: frozenset[str] = frozenset({"solved", "mastered", "skipped"})
OPEN_QUESTION_STATUSES: frozenset[str] = frozenset({"unseen", "in_progress", "retest"})
PROTECTED_QUESTION_STATUSES: frozenset[str] = frozenset(
    {"in_progress", "solved", "retest", "mastered", "skipped"}
)

VALID_MILESTONES: tuple[str, ...] = (
    "sourcesIndexed",
    "probed",
    "theoryComplete",
    "questionsComplete",
    "revisionSheetBuilt",
    "cbtMockBuilt",
    "cbtMockPassed",
    "audioBriefBuilt",
)

IGNORED_DIR_NAMES: frozenset[str] = frozenset(
    {".git", ".venv", "__pycache__", "_extracted", "node_modules", "target"}
)
STUDY_FILE_EXTS: frozenset[str] = frozenset(
    {
        ".pdf",
        ".md",
        ".txt",
        ".png",
        ".jpg",
        ".jpeg",
        ".webp",
        ".heic",
        ".tex",
        ".rst",
        ".py",
        ".js",
        ".ts",
        ".tsx",
        ".rs",
        ".cpp",
        ".c",
        ".h",
        ".java",
        ".go",
        ".ipynb",
        ".sql",
        ".html",
    }
)

DEFAULT_CHUNK_WINDOW = 16
DEFAULT_CHUNK_OVERLAP = 1
DEFAULT_CHUNK_TAIL_MERGE = 4
MAX_SESSION_LOG = 200


# ---------------------------------------------------------------------------
# Path, time, and validation helpers
# ---------------------------------------------------------------------------


def normalize_subject(value: str) -> str:
    """Normalize common STEM/CS/ML aliases or cleanly preserve/title-case any custom subject."""
    try:
        return _normalize_subject_core(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def normalize_goal_profile(raw: str) -> str:
    val = str(raw or "").strip().lower()
    if val in VALID_GOAL_PROFILES:
        return val
    raise argparse.ArgumentTypeError(
        f"Invalid --goal-profile {raw!r}; expected one of: {', '.join(VALID_GOAL_PROFILES)}"
    )


def normalize_target_depth(raw: str) -> str:
    val = str(raw or "").strip().lower()
    if val in TARGET_DEPTH_ALIASES:
        return TARGET_DEPTH_ALIASES[val]
    raise argparse.ArgumentTypeError(
        f"Invalid --target-depth {raw!r}; expected one of: {', '.join(VALID_TARGET_DEPTHS)}"
    )


def _parse_scope_items(raw_items: list[str] | None) -> list[str]:
    if not raw_items:
        return []
    items: list[str] = []
    for raw in raw_items:
        for part in str(raw or "").split(","):
            tok = part.strip()
            if tok:
                items.append(tok)
    return list(dict.fromkeys(items))


def normalize_slug(raw: str) -> str:
    s = re.sub(r"[^a-z0-9_-]+", "-", str(raw or "").strip().lower()).strip("-_")
    if not s or ".." in raw or "/" in raw or "\\" in raw:
        raise ValueError(f"Invalid chapter slug {raw!r}; use lowercase letters, digits, '-' or '_'.")
    return s


def parse_bool_str(raw: str) -> bool:
    low = str(raw or "").strip().lower()
    if low in {"true", "1", "yes", "y", "on"}:
        return True
    if low in {"false", "0", "no", "n", "off"}:
        return False
    raise argparse.ArgumentTypeError(f"Invalid boolean value {raw!r}; expected true or false.")


def resolve_chapters_dir(chapters_dir: Path | str | None = None) -> tuple[Path, bool]:
    """Return `(resolved_chapters_dir, is_default)`, honoring `SYNT_CHAPTERS_DIR || MLH_CHAPTERS_DIR || SYNT_WORKSPACE`."""
    p = _resolve_chapters_dir_core(explicit=chapters_dir)
    return p, (p == DEFAULT_CHAPTERS_DIR.resolve())


def resolve_kg_dir(kg_dir: Path | str | None = None) -> tuple[Path, bool]:
    """Return `(resolved_kg_dir, is_default)`, honoring `SYNT_KG_DIR || MLH_KG_DIR || SYNT_WORKSPACE`."""
    p = _resolve_kg_dir_core(explicit=kg_dir)
    return p, (p == DEFAULT_KG_DIR.resolve())


def tracker_path_for(slug: str, chapters_dir: Path | str | None = None) -> Path:
    cdir, _ = resolve_chapters_dir(chapters_dir)
    return cdir / f"{normalize_slug(slug)}{TRACKER_SUFFIX}"


def normalize_mistake_category(raw: str) -> str:
    """Normalize canonical mistake categories and coding aliases matching `mistake_analytics.py`."""
    return _normalize_mistake_category_core(raw)


def sync_viewer(
    chapters_dir: Path | str | None = None,
    kg_dir: Path | str | None = None,
) -> None:
    """Refresh `knowledge-graph/graph-data.js` quietly via `scripts/sync-graph.mjs`."""
    sync_hub(kg_dir=kg_dir, chapters_dir=chapters_dir, quiet=True)


def _should_sync(chapters_dir: Path | str | None, kg_dir: Path | str | None, no_sync: bool) -> bool:
    return not no_sync


# ---------------------------------------------------------------------------
# Page range & PDF probing helpers
# ---------------------------------------------------------------------------


def parse_page_range_spec(spec: str) -> list[list[int]]:
    """Parse '1-20', '1-10,15,20-25' into sorted [start, end] integer pairs."""
    ranges: list[list[int]] = []
    for part in str(spec or "").split(","):
        tok = part.strip()
        if not tok:
            continue
        if "-" in tok:
            a_s, b_s = tok.split("-", 1)
            a, b = int(a_s.strip()), int(b_s.strip())
            lo, hi = (a, b) if a <= b else (b, a)
            if lo >= 1 and hi >= 1:
                ranges.append([lo, hi])
        else:
            p = int(tok)
            if p >= 1:
                ranges.append([p, p])
    return merge_ranges(ranges)


def merge_ranges(ranges: list[Any]) -> list[list[int]]:
    """Merge overlapping or adjacent [start, end] page ranges into canonical form."""
    cleaned: list[tuple[int, int]] = []
    for item in ranges or []:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            try:
                a, b = int(item[0]), int(item[1])
            except (TypeError, ValueError):
                continue
            lo, hi = (a, b) if a <= b else (b, a)
            if hi >= 1:
                cleaned.append((max(1, lo), hi))
        elif isinstance(item, int) and item >= 1:
            cleaned.append((item, item))
    if not cleaned:
        return []
    cleaned.sort()
    merged: list[list[int]] = [[cleaned[0][0], cleaned[0][1]]]
    for lo, hi in cleaned[1:]:
        if lo <= merged[-1][1] + 1:
            merged[-1][1] = max(merged[-1][1], hi)
        else:
            merged.append([lo, hi])
    return merged


def compute_uncovered_ranges(total_pages: int | None, indexed_ranges: list[Any]) -> list[list[int]]:
    """Return sorted uncovered [start, end] spans inside [1, total_pages]."""
    if not isinstance(total_pages, int) or total_pages <= 0:
        return []
    covered = merge_ranges(indexed_ranges)
    uncovered: list[list[int]] = []
    cursor = 1
    for lo, hi in covered:
        if hi < 1 or lo > total_pages:
            continue
        lo_c = max(1, lo)
        hi_c = min(total_pages, hi)
        if cursor < lo_c:
            uncovered.append([cursor, lo_c - 1])
        cursor = max(cursor, hi_c + 1)
    if cursor <= total_pages:
        uncovered.append([cursor, total_pages])
    return uncovered


def build_chunk_windows(
    uncovered: list[list[int]],
    window: int = DEFAULT_CHUNK_WINDOW,
    overlap: int = DEFAULT_CHUNK_OVERLAP,
    max_tail_merge: int = DEFAULT_CHUNK_TAIL_MERGE,
) -> list[list[int]]:
    """Split uncovered ranges into bounded 15-20p chunks with 1-page boundary overlap."""
    chunks: list[list[int]] = []
    step_span = max(1, window - 1)
    for lo, hi in uncovered:
        if hi < lo:
            continue
        cur = lo
        while cur <= hi:
            end = min(hi, cur + step_span)
            remaining = hi - end
            if 0 < remaining <= max_tail_merge:
                end = hi
            chunks.append([cur, end])
            if end >= hi:
                break
            cur = max(cur + 1, end - overlap + 1)
    return chunks


def _parse_covered_pages_arg(specs: list[str]) -> list[int | str]:
    """Expand numeric specs like '1-9,12' into ints while preserving prefixed tokens like 'wb:1-9'."""
    out: list[int | str] = []
    for spec in specs:
        for raw_part in str(spec or "").split(","):
            tok = raw_part.strip()
            if not tok:
                continue
            m_range = re.fullmatch(r"(\d+)\s*-\s*(\d+)", tok)
            if m_range:
                a, b = int(m_range.group(1)), int(m_range.group(2))
                lo, hi = (a, b) if a <= b else (b, a)
                if lo >= 1 and hi - lo <= 2000:
                    out.extend(range(lo, hi + 1))
                else:
                    out.append(tok)
            elif tok.isdigit():
                out.append(int(tok))
            else:
                out.append(tok)
    return out


def _merge_covered_pages(existing: list[Any], additions: list[Any]) -> list[Any]:
    ints: set[int] = set()
    strs: list[str] = []
    seen_strs: set[str] = set()
    for val in list(existing or []) + list(additions or []):
        if isinstance(val, int) and not isinstance(val, bool):
            ints.add(val)
        elif isinstance(val, str) and val.strip():
            s = val.strip()
            if s.isdigit():
                ints.add(int(s))
            elif s not in seen_strs:
                seen_strs.add(s)
                strs.append(s)
    return [*sorted(ints), *strs]


def _probe_pdf_pages(file_path: Path) -> int | None:
    """Fast metadata-only PDF page count via pymupdf if available (never extracts text)."""
    if file_path.suffix.lower() != ".pdf" or not file_path.exists():
        return None
    try:
        import pymupdf as fitz  # type: ignore

        with fitz.open(str(file_path)) as doc:
            return int(len(doc))
    except Exception:
        return None


def classify_source_kind(file_path: Path) -> str:
    """Auto-classify a study source file from filename and extension hints."""
    ext = file_path.suffix.lower()
    haystack = f"{file_path.parent.name} {file_path.stem}".lower()
    if ext in CODE_FILE_EXTS:
        return "code_repo"
    if ext != ".pdf":
        if "syllabus" in haystack:
            return "syllabus"
        if any(kw in haystack for kw in ("official-doc", "official_doc", "api-doc", "reference", "manual")):
            return "official_docs"
        if any(kw in haystack for kw in ("pset", "problem-set", "problem_set", "homework")):
            return "problem_set"
        return "notes"

    if "syllabus" in haystack:
        return "syllabus"
    if any(
        kw in haystack
        for kw in (
            "pyq",
            "previous",
            "past-year",
            "past_year",
            "archive",
            "jee-main",
            "jee-adv",
            "question-paper",
            "past_paper",
        )
    ):
        return "pyq_pdf"
    if any(
        kw in haystack
        for kw in ("arxiv", "et-al", "proceedings", "neurips", "icml", "iclr", "ieee", "acm")
    ) or re.search(r"\b\d{4}\.\d{4,5}\b", haystack):
        return "paper_pdf"
    if any(kw in haystack for kw in ("slides", "deck", "presentation")) and "whiteboard" not in haystack:
        return "slides_pdf"
    if any(
        kw in haystack
        for kw in (
            "whiteboard",
            "board",
            "class-notes",
            "class_notes",
            "classnotes",
            "handwritten",
            "lecture",
            "lect",
            "sir",
        )
    ) or re.search(r"(^|[\s_-])(wb|lec\d*|l\d+)([\s_-]|$)", haystack):
        return "whiteboard_pdf"
    if any(kw in haystack for kw in ("pset", "problem-set", "problem_set", "homework")):
        return "problem_set"
    if any(
        kw in haystack
        for kw in (
            "module",
            "sheet",
            "dpp",
            "exercise",
            "allen",
            "resonance",
            "fiitjee",
            "bansal",
            "workbook",
            "assignment",
            "problem",
            "question",
            "bank",
            "paper",
        )
    ) or re.search(r"(^|[\s_-])(mod|ex\d*)([\s_-]|$)", haystack):
        return "module_pdf"
    if any(
        kw in haystack
        for kw in (
            "textbook",
            "book",
            "ncert",
            "cengage",
            "sardana",
            "irodov",
            "krotov",
            "verma",
            "hcv",
            "chouhan",
            "morrison",
            "solomons",
            "clayden",
            "atkins",
            "loney",
            "theory",
            "chapter",
        )
    ):
        return "textbook_pdf"
    return "textbook_pdf"


def _prettify_label(stem: str) -> str:
    cleaned = re.sub(r"[-_]+", " ", stem).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.title() if cleaned.islower() else (cleaned or stem)


def discover_sources(source_dir: Path) -> list[dict[str, Any]]:
    """Discover visible study files inside source_dir with metadata-only PDF page counts."""
    root_dir = source_dir.expanduser().resolve()
    if not root_dir.exists() or not root_dir.is_dir():
        raise ValueError(f"Source directory not found: {root_dir}")

    discovered_paths: list[Path] = []
    for cur_root, dirs, files in os.walk(root_dir):
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d not in IGNORED_DIR_NAMES)
        for fname in sorted(files):
            if fname.startswith(".") or fname.endswith(TRACKER_SUFFIX):
                continue
            fpath = Path(cur_root) / fname
            if fpath.suffix.lower() in STUDY_FILE_EXTS:
                discovered_paths.append(fpath)

    sources: list[dict[str, Any]] = []
    used_ids: set[str] = set()
    for fpath in discovered_paths:
        base_id = re.sub(r"[^a-z0-9]+", "-", fpath.stem.lower()).strip("-")[:24] or "src"
        sid = base_id
        suffix = 2
        while sid in used_ids:
            sid = f"{base_id[:20]}-{suffix}"
            suffix += 1
        used_ids.add(sid)

        kind = classify_source_kind(fpath)
        total_pages = _probe_pdf_pages(fpath) if fpath.suffix.lower() == ".pdf" else 1
        sources.append(
            {
                "id": sid,
                "label": _prettify_label(fpath.stem),
                "path": str(fpath),
                "kind": kind,
                "totalPages": total_pages,
                "indexedRanges": [],
                "notes": "",
            }
        )
    return sources


# ---------------------------------------------------------------------------
# Tracker schema normalization, loading & saving
# ---------------------------------------------------------------------------


def default_milestones() -> dict[str, bool]:
    return {k: False for k in VALID_MILESTONES}


def default_config() -> dict[str, int]:
    return {"retestGap": 3, "dailyWarmupLimit": 3}


def default_scope_charter(
    subject: str = "General",
    sources: list[Any] | None = None,
    title: str = "",
) -> dict[str, Any]:
    inferred_profile = "jee" if _has_jee_source_or_title_cues(title, sources or []) else "general"
    return {
        "goalProfile": inferred_profile,
        "targetDepth": "mastery",
        "allowWebResearch": False,
        "inScope": [],
        "outOfScope": [],
    }


def _normalize_scope_charter(
    raw: Any,
    *,
    subject: str = "General",
    sources: list[Any] | None = None,
    title: str = "",
) -> dict[str, Any]:
    sc = default_scope_charter(subject=subject, sources=sources, title=title)
    if not isinstance(raw, dict):
        return sc
    if raw.get("goalProfile"):
        gp = str(raw["goalProfile"]).strip().lower()
        if gp in VALID_GOAL_PROFILES:
            sc["goalProfile"] = gp
    if raw.get("targetDepth"):
        td = str(raw["targetDepth"]).strip().lower()
        if td in TARGET_DEPTH_ALIASES:
            sc["targetDepth"] = TARGET_DEPTH_ALIASES[td]
    if "allowWebResearch" in raw and raw.get("allowWebResearch") is not None:
        sc["allowWebResearch"] = bool(raw.get("allowWebResearch"))
    if isinstance(raw.get("inScope"), list):
        sc["inScope"] = list(
            dict.fromkeys(str(x).strip() for x in raw["inScope"] if str(x).strip())
        )
    if isinstance(raw.get("outOfScope"), list):
        sc["outOfScope"] = list(
            dict.fromkeys(str(x).strip() for x in raw["outOfScope"] if str(x).strip())
        )
    return sc


def _normalize_slice(raw: dict[str, Any], idx: int) -> dict[str, Any]:
    stage = str(raw.get("stage") or "queued").strip().lower()
    if stage not in VALID_SLICE_STAGES:
        stage = "queued"
    try:
        prio = max(0, min(100, int(raw.get("priority", 50))))
    except (TypeError, ValueError):
        prio = 50
    return {
        "id": str(raw.get("id") or f"slice-{idx + 1}").strip(),
        "sourceId": str(raw.get("sourceId") or "").strip(),
        "title": str(raw.get("title") or raw.get("id") or f"Slice {idx + 1}").strip(),
        "pages": str(raw.get("pages") or raw.get("range") or "").strip(),
        "priority": prio,
        "stage": stage,
        "notes": str(raw.get("notes") or "").strip(),
        "updatedAt": str(raw.get("updatedAt") or now_iso()),
    }


def _normalize_tracker(data: dict[str, Any]) -> dict[str, Any]:
    ms = default_milestones()
    if isinstance(data.get("milestones"), dict):
        for k in VALID_MILESTONES:
            if k in data["milestones"]:
                ms[k] = bool(data["milestones"][k])

    cfg = default_config()
    if isinstance(data.get("config"), dict):
        if "retestGap" in data["config"]:
            try:
                cfg["retestGap"] = max(1, int(data["config"]["retestGap"]))
            except (TypeError, ValueError):
                pass
        if "dailyWarmupLimit" in data["config"]:
            try:
                cfg["dailyWarmupLimit"] = max(0, int(data["config"]["dailyWarmupLimit"]))
            except (TypeError, ValueError):
                pass

    norm_topics: list[dict[str, Any]] = []
    for t_item in data.get("topics") or []:
        if isinstance(t_item, dict):
            tc = dict(t_item)
            tc["level"] = normalize_level_soft(tc.get("level"), "basics")
            norm_topics.append(tc)

    norm_questions: list[dict[str, Any]] = []
    for q_item in data.get("questions") or []:
        if isinstance(q_item, dict):
            qc = dict(q_item)
            qc["level"] = normalize_level_soft(qc.get("level"), "basics")
            norm_questions.append(qc)

    subj = str(data.get("subject") or "General")
    title = str(data.get("title") or data.get("slug") or "")
    sources = list(data.get("sources") or [])
    sc = _normalize_scope_charter(
        data.get("scopeCharter"),
        subject=subj,
        sources=sources,
        title=title,
    )

    out: dict[str, Any] = {
        "schemaVersion": int(data.get("schemaVersion") or SCHEMA_VERSION),
        "slug": str(data.get("slug") or ""),
        "title": title,
        "subject": subj,
        "active": bool(data.get("active", False)),
        "scopeCharter": sc,
        "createdAt": str(data.get("createdAt") or now_iso()),
        "updatedAt": str(data.get("updatedAt") or now_iso()),
        "sources": sources,
        "topics": norm_topics,
        "questions": norm_questions,
        "milestones": ms,
        "doubtStack": list(data.get("doubtStack") or []),
        "stepCounter": max(0, int(data.get("stepCounter") or 0)),
        "config": cfg,
        "sessionLog": list(data.get("sessionLog") or [])[-MAX_SESSION_LOG:],
    }
    if "slices" in data and isinstance(data.get("slices"), list):
        out["slices"] = [
            _normalize_slice(s, i)
            for i, s in enumerate(data["slices"])
            if isinstance(s, dict)
        ]
    raw_bc = data.get("nextBreadcrumb")
    if isinstance(raw_bc, dict) and str(raw_bc.get("summary") or "").strip():
        out["nextBreadcrumb"] = {
            "at": str(raw_bc.get("at") or now_iso()),
            "summary": str(raw_bc.get("summary") or "").strip(),
            "firstMicroAction": str(raw_bc.get("firstMicroAction") or "").strip(),
        }
    return out


def _append_log(tracker: dict[str, Any], event: str, detail: dict[str, Any] | None = None) -> None:
    log = list(tracker.get("sessionLog") or [])
    entry: dict[str, Any] = {
        "ts": now_iso(),
        "step": int(tracker.get("stepCounter") or 0),
        "event": event,
    }
    if detail:
        entry.update(detail)
    log.append(entry)
    tracker["sessionLog"] = log[-MAX_SESSION_LOG:]


def list_trackers(chapters_dir: Path | str | None = None) -> list[dict[str, Any]]:
    """Load all valid `*-tracker.json` files in chapters_dir, sorted active-first."""
    cdir, _ = resolve_chapters_dir(chapters_dir)
    if not cdir.exists() or not cdir.is_dir():
        return []
    items: list[dict[str, Any]] = []
    for fpath in sorted(cdir.iterdir()):
        if not fpath.is_file() or not fpath.name.endswith(TRACKER_SUFFIX):
            continue
        raw = _load_json_core(fpath, default=None)
        if isinstance(raw, dict) and raw.get("slug"):
            items.append(_normalize_tracker(raw))
    items.sort(key=lambda t: str(t.get("updatedAt") or ""), reverse=True)
    items.sort(key=lambda t: 0 if t.get("active") else 1)
    return items


def find_active_tracker(chapters_dir: Path | str | None = None) -> dict[str, Any] | None:
    """Return the currently active chapter tracker dict, or None if none is active."""
    for tracker in list_trackers(chapters_dir):
        if tracker.get("active"):
            return tracker
    return None


def load_tracker(slug_or_path: str | Path, chapters_dir: Path | str | None = None) -> dict[str, Any]:
    """Load a chapter tracker by slug (within chapters_dir) or direct JSON file path."""
    candidate = Path(slug_or_path).expanduser()
    if candidate.suffix.lower() == ".json" or candidate.exists():
        path = candidate.resolve()
    else:
        path = tracker_path_for(str(slug_or_path), chapters_dir)
    if not path.exists():
        raise FileNotFoundError(f"Chapter tracker not found: {path}")
    raw = _load_json_core(path, strict=True)
    if not isinstance(raw, dict):
        raise ValueError(f"Invalid tracker JSON in {path}")
    return _normalize_tracker(raw)


def save_tracker(
    tracker: dict[str, Any],
    chapters_dir: Path | str | None = None,
    *,
    kg_dir: Path | str | None = None,
    no_sync: bool = True,
) -> Path:
    """Atomically write a chapter tracker JSON and optionally trigger sync-graph.mjs."""
    norm = _normalize_tracker(tracker)
    norm["updatedAt"] = now_iso()
    path = tracker_path_for(norm["slug"], chapters_dir)
    _write_json_atomic(path, norm)
    if _should_sync(chapters_dir, kg_dir, no_sync):
        sync_viewer(chapters_dir=chapters_dir, kg_dir=kg_dir)
    return path


def _deactivate_other_trackers(active_slug: str, chapters_dir: Path | str | None) -> list[str]:
    cdir, _ = resolve_chapters_dir(chapters_dir)
    if not cdir.exists():
        return []
    deactivated: list[str] = []
    for fpath in sorted(cdir.iterdir()):
        if not fpath.is_file() or not fpath.name.endswith(TRACKER_SUFFIX):
            continue
        try:
            raw = json.loads(fpath.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(raw, dict):
            continue
        other_slug = str(raw.get("slug") or "")
        if other_slug and other_slug != active_slug and raw.get("active"):
            raw["active"] = False
            raw["updatedAt"] = now_iso()
            _write_json_atomic(fpath, _normalize_tracker(raw))
            deactivated.append(other_slug)
    return deactivated


def _recompute_auto_milestones(tracker: dict[str, Any]) -> None:
    topics = tracker.get("topics") or []
    questions = tracker.get("questions") or []
    ms = tracker["milestones"]
    if topics:
        ms["theoryComplete"] = all(t.get("status") == "mastered" for t in topics)
    if questions:
        ms["questionsComplete"] = all(q.get("status") not in OPEN_QUESTION_STATUSES for q in questions)


# ---------------------------------------------------------------------------
# Stats & Deterministic Autopilot Brain
# ---------------------------------------------------------------------------


def compute_stats(tracker: dict[str, Any]) -> dict[str, Any]:
    """Compute progress counts, level breakdowns, and completion percentages."""
    t = _normalize_tracker(tracker)
    sources = t["sources"]
    topics = t["topics"]
    questions = t["questions"]
    milestones = t["milestones"]

    sources_indexed = 0
    for s in sources:
        tp = s.get("totalPages")
        if isinstance(tp, int) and tp > 0:
            if not compute_uncovered_ranges(tp, s.get("indexedRanges") or []):
                sources_indexed += 1
        elif s.get("indexedRanges"):
            sources_indexed += 1

    topic_counts = Counter(str(item.get("status") or "pending") for item in topics)
    q_counts = Counter(str(item.get("status") or "unseen") for item in questions)

    topics_total = len(topics)
    topics_mastered = topic_counts["mastered"]
    topics_pct = round((topics_mastered / topics_total) * 100.0, 1) if topics_total else 0.0

    q_total = len(questions)
    q_solved = q_counts["solved"] + q_counts["mastered"]
    q_done = q_counts["solved"] + q_counts["mastered"] + q_counts["skipped"]
    q_pct = round((q_done / q_total) * 100.0, 1) if q_total else 0.0

    ms_done = sum(1 for k in VALID_MILESTONES if milestones.get(k))
    ms_total = len(VALID_MILESTONES)

    denom = topics_total + q_total
    if denom > 0:
        overall_pct = round(((topics_mastered + q_done) / denom) * 100.0, 1)
    else:
        overall_pct = round((ms_done / ms_total) * 100.0, 1) if ms_total else 0.0

    by_level: dict[str, dict[str, int]] = {}
    for lvl in VALID_LEVELS:
        lvl_topics = [x for x in topics if x.get("level") == lvl]
        lvl_qs = [x for x in questions if x.get("level") == lvl]
        by_level[lvl] = {
            "topicsTotal": len(lvl_topics),
            "topicsMastered": sum(1 for x in lvl_topics if x.get("status") == "mastered"),
            "questionsTotal": len(lvl_qs),
            "questionsDone": sum(1 for x in lvl_qs if x.get("status") in DONE_QUESTION_STATUSES),
            "questionsRetest": sum(1 for x in lvl_qs if x.get("status") == "retest"),
            "questionsUnseen": sum(1 for x in lvl_qs if x.get("status") == "unseen"),
        }

    scope_charter = _normalize_scope_charter(t.get("scopeCharter"))
    slices = t.get("slices") or []
    slices_crystallized = sum(1 for s in slices if isinstance(s, dict) and s.get("stage") == "crystallized")
    return {
        "slug": t["slug"],
        "title": t["title"],
        "subject": t["subject"],
        "active": t["active"],
        "scopeCharter": scope_charter,
        "stepCounter": t["stepCounter"],
        "sourcesTotal": len(sources),
        "sourcesIndexedCount": sources_indexed,
        "topicsTotal": topics_total,
        "topicsMastered": topics_mastered,
        "topicsInProgress": topic_counts["in_progress"],
        "topicsProbed": topic_counts["probed"],
        "topicsPending": topic_counts["pending"],
        "topicsPct": topics_pct,
        "questionsTotal": q_total,
        "questionsSolved": q_solved,
        "questionsMastered": q_counts["mastered"],
        "questionsSkipped": q_counts["skipped"],
        "questionsDone": q_done,
        "questionsRetest": q_counts["retest"],
        "questionsInProgress": q_counts["in_progress"],
        "questionsUnseen": q_counts["unseen"],
        "questionsPct": q_pct,
        "slicesTotal": len(slices),
        "slicesCrystallized": slices_crystallized,
        "overallPct": overall_pct,
        "byLevel": by_level,
        "milestones": dict(milestones),
        "milestonesDone": ms_done,
        "milestonesTotal": ms_total,
        "doubtDepth": len(t["doubtStack"]),
    }


def _collect_kg_signals(
    tracker: dict[str, Any],
    kg_dir: Path | str | None,
    include_warmup: bool,
) -> tuple[list[dict[str, Any]], int, dict[str, Any] | None]:
    """Read propositions.jsonl and mistakes.jsonl for warmup items and top recurring trap."""
    resolved_kg, _ = resolve_kg_dir(kg_dir)
    props = _load_jsonl(resolved_kg / "propositions.jsonl")
    mistakes = _load_jsonl(resolved_kg / "mistakes.jsonl")
    today = today_iso()

    due_candidates: list[tuple[int, str, float, dict[str, Any]]] = []
    for p in props:
        pid = str(p.get("id") or "").strip()
        if not pid:
            continue
        status = str(p.get("status") or "planned")
        next_rev = str(p.get("nextReview") or "")
        try:
            bkt = float(p.get("bktMastery", 0.5))
        except (TypeError, ValueError):
            bkt = 0.5

        if status == "misconception":
            prio, reason = 0, "misconception"
        elif status == "shaky":
            prio, reason = 1, "shaky"
        elif next_rev and next_rev <= today:
            prio, reason = 2, f"due_{next_rev}"
        else:
            continue

        due_candidates.append(
            (
                prio,
                next_rev or "0000-00-00",
                bkt,
                {
                    "id": pid,
                    "title": str(p.get("title") or pid),
                    "subject": str(p.get("subject") or ""),
                    "chapter": str(p.get("chapter") or ""),
                    "status": status,
                    "nextReview": next_rev or None,
                    "bktMastery": bkt,
                    "reason": reason,
                },
            )
        )

    due_candidates.sort(key=lambda item: (item[0], item[1], item[2], item[3]["id"]))
    due_count = len(due_candidates)
    limit = max(0, int(tracker.get("config", {}).get("dailyWarmupLimit", 3)))
    warmup_items = [item[3] for item in due_candidates[:limit]] if include_warmup else []

    top_trap: dict[str, Any] | None = None
    if mistakes:
        ch_slug = str(tracker.get("slug") or "").lower()
        ch_title = str(tracker.get("title") or "").lower()
        subj_low = str(tracker.get("subject") or "").lower()

        overall_counts: Counter[str] = Counter()
        chapter_counts: Counter[str] = Counter()
        for m in mistakes:
            cat = str(m.get("category") or "").strip()
            if not cat:
                continue
            overall_counts[cat] += 1
            m_ch = str(m.get("chapter") or "").strip().lower()
            m_subj = str(m.get("subject") or "").strip().lower()
            if (m_ch and (m_ch == ch_slug or m_ch == ch_title or ch_title in m_ch or ch_slug in m_ch)) or (
                not m_ch and m_subj and m_subj == subj_low
            ):
                chapter_counts[cat] += 1

        if chapter_counts:
            cat, ch_cnt = sorted(chapter_counts.items(), key=lambda kv: (-kv[1], kv[0]))[0]
            top_trap = {
                "category": cat,
                "chapterCount": ch_cnt,
                "totalCount": overall_counts[cat],
                "scope": "chapter",
            }
        elif overall_counts:
            cat, tot_cnt = sorted(overall_counts.items(), key=lambda kv: (-kv[1], kv[0]))[0]
            top_trap = {
                "category": cat,
                "chapterCount": 0,
                "totalCount": tot_cnt,
                "scope": "global",
            }

    return warmup_items, due_count, top_trap


def _enrich_topic(topic: dict[str, Any], sources_by_id: dict[str, dict[str, Any]]) -> dict[str, Any]:
    refs_out: list[dict[str, Any]] = []
    ref_labels: list[str] = []
    for ref in topic.get("sourceRefs") or []:
        if not isinstance(ref, dict):
            continue
        sid = str(ref.get("sourceId") or "")
        src = sources_by_id.get(sid)
        pages = str(ref.get("pages") or "")
        enriched = {
            "sourceId": sid,
            "pages": pages,
            "section": str(ref.get("section") or ""),
            "label": str(src.get("label") or sid) if src else sid,
            "path": str(src.get("path") or "") if src else "",
            "kind": str(src.get("kind") or "") if src else "",
        }
        refs_out.append(enriched)
        if sid and pages:
            ref_labels.append(f"{sid.upper()} p.{pages}")
        elif sid:
            ref_labels.append(sid.upper())

    return {
        "id": topic["id"],
        "order": int(topic.get("order") or 0),
        "title": topic["title"],
        "level": str(topic.get("level") or "basics"),
        "status": str(topic.get("status") or "pending"),
        "prereqTopicIds": list(topic.get("prereqTopicIds") or []),
        "propIds": list(topic.get("propIds") or []),
        "coveredPages": list(topic.get("coveredPages") or []),
        "sourceRefs": refs_out,
        "sourceHint": ", ".join(ref_labels[:2]),
        "notes": str(topic.get("notes") or ""),
    }


def _enrich_question(
    q: dict[str, Any],
    sources_by_id: dict[str, dict[str, Any]],
    step_counter: int,
    retest_gap: int,
) -> dict[str, Any]:
    sid = str(q.get("sourceId") or "")
    src = sources_by_id.get(sid)
    last_step = q.get("lastStep")
    gap_elapsed = (step_counter - int(last_step)) if isinstance(last_step, int) else None
    return {
        "qid": q["qid"],
        "sourceId": sid,
        "sourceLabel": str(src.get("label") or sid) if src else sid,
        "path": str(src.get("path") or "") if src else "",
        "sourceKind": str(src.get("kind") or "") if src else "",
        "label": str(q.get("label") or q["qid"]),
        "page": q.get("page"),
        "topicId": q.get("topicId"),
        "level": str(q.get("level") or "basics"),
        "kind": str(q.get("kind") or "mcq"),
        "status": str(q.get("status") or "unseen"),
        "attempts": int(q.get("attempts") or 0),
        "lastMistakeCategory": q.get("lastMistakeCategory"),
        "lastDate": q.get("lastDate"),
        "lastStep": last_step,
        "gapElapsed": gap_elapsed,
        "retestGap": retest_gap,
    }


def _recent_question_topic_state(tracker: dict[str, Any]) -> tuple[str | None, int, Counter[tuple[str, str]]]:
    """Inspect recent question history for Rohrer interleaving across mastered topics."""
    q_by_id = {q["qid"]: q for q in tracker.get("questions") or [] if isinstance(q, dict) and q.get("qid")}
    done_by_topic_level: Counter[tuple[str, str]] = Counter()
    step_history: list[tuple[int, str]] = []

    for q in tracker.get("questions") or []:
        tid = str(q.get("topicId") or "")
        lvl = str(q.get("level") or "basics")
        if tid and q.get("status") in DONE_QUESTION_STATUSES:
            done_by_topic_level[(tid, lvl)] += 1
        ls = q.get("lastStep")
        if tid and isinstance(ls, int):
            step_history.append((ls, tid))

    # Also incorporate sessionLog mark_question entries so retests/solves preserve exact tail sequence.
    log_history: list[tuple[int, str]] = []
    for entry in tracker.get("sessionLog") or []:
        if entry.get("event") == "mark_question" and entry.get("qid") in q_by_id:
            tid = str(q_by_id[entry["qid"]].get("topicId") or "")
            if tid:
                log_history.append((int(entry.get("step") or 0), tid))

    history = log_history if log_history else sorted(step_history, key=lambda x: x[0])
    if not history:
        return None, 0, done_by_topic_level

    last_topic = history[-1][1]
    tail_len = 0
    for _, tid in reversed(history):
        if tid == last_topic:
            tail_len += 1
        else:
            break
    return last_topic, tail_len, done_by_topic_level


def _select_interleaved_question(
    candidates: list[tuple[int, dict[str, Any]]],
    topic_order_map: dict[str, int],
    last_topic: str | None,
    tail_len: int,
    done_by_topic_level: Counter[tuple[str, str]],
) -> tuple[dict[str, Any], bool]:
    """Select next unseen/in_progress question respecting basics->main->advanced + Rohrer round-robin."""
    # Group first by (in_progress before unseen, LEVEL_RANK)
    def primary_bucket(q: dict[str, Any]) -> tuple[int, int]:
        st_rank = 0 if q.get("status") == "in_progress" else 1
        lvl_rank = LEVEL_RANK.get(str(q.get("level") or "basics"), 1)
        return (st_rank, lvl_rank)

    min_bucket = min(primary_bucket(q) for _, q in candidates)
    pool = [(idx, q) for idx, q in candidates if primary_bucket(q) == min_bucket]
    distinct_topics = {str(q.get("topicId") or "") for _, q in pool if q.get("topicId")}

    if len(distinct_topics) >= 2 and min_bucket[0] == 1:
        # Rohrer interleaving across distinct mastered topics at the same level:
        # avoid repeating last_topic (especially >2 consecutive) and balance done counts.
        def interleave_key(item: tuple[int, dict[str, Any]]) -> tuple[int, int, int, int, int]:
            idx, q = item
            tid = str(q.get("topicId") or "")
            lvl = str(q.get("level") or "basics")
            avoid_streak = 1 if (last_topic and tid == last_topic and tail_len >= 2) else 0
            avoid_repeat = 1 if (last_topic and tid == last_topic) else 0
            done_cnt = done_by_topic_level.get((tid, lvl), 0)
            t_ord = topic_order_map.get(tid, 9999)
            return (avoid_streak, avoid_repeat, done_cnt, t_ord, idx)

        _, chosen = min(pool, key=interleave_key)
        interleaved = bool(last_topic and chosen.get("topicId") and chosen.get("topicId") != last_topic)
        return chosen, interleaved

    _, chosen = min(
        pool,
        key=lambda item: (topic_order_map.get(str(item[1].get("topicId") or ""), 9999), item[0]),
    )
    return chosen, False


def _compute_base_action(tracker: dict[str, Any]) -> dict[str, Any]:
    """Evaluate deterministic rules 2..11 of the Chapter Autopilot state machine."""
    slug = tracker["slug"]
    title = tracker["title"]
    sources = tracker["sources"]
    topics = tracker["topics"]
    questions = tracker["questions"]
    milestones = tracker["milestones"]
    step_counter = int(tracker["stepCounter"])
    retest_gap = max(1, int(tracker["config"].get("retestGap", 3)))

    sources_by_id = {str(s.get("id") or ""): s for s in sources if isinstance(s, dict)}
    topics_by_id = {str(t.get("id") or ""): t for t in topics if isinstance(t, dict)}
    topic_order_map = {str(t.get("id") or ""): int(t.get("order") or i + 1) for i, t in enumerate(topics)}

    # Rule 2: Index sources first when sources exist and !milestones.sourcesIndexed.
    if sources and not milestones.get("sourcesIndexed", False):
        unindexed: list[dict[str, Any]] = []
        for s in sources:
            tp = s.get("totalPages")
            idx_ranges = merge_ranges(s.get("indexedRanges") or [])
            if isinstance(tp, int) and tp > 0:
                uncovered = compute_uncovered_ranges(tp, idx_ranges)
                chunks = build_chunk_windows(uncovered)
                needs = bool(uncovered)
            else:
                uncovered = []
                chunks = []
                needs = not bool(idx_ranges)
            if needs:
                unindexed.append(
                    {
                        "id": s["id"],
                        "label": s.get("label") or s["id"],
                        "path": s.get("path") or "",
                        "kind": s.get("kind") or "notes",
                        "totalPages": tp,
                        "indexedRanges": idx_ranges,
                        "uncoveredRanges": uncovered,
                        "recommendedChunks": chunks,
                    }
                )
        if not unindexed:
            # All source ranges already recorded; prompt finalizing sourcesIndexed milestone.
            unindexed = [
                {
                    "id": s["id"],
                    "label": s.get("label") or s["id"],
                    "path": s.get("path") or "",
                    "kind": s.get("kind") or "notes",
                    "totalPages": s.get("totalPages"),
                    "indexedRanges": merge_ranges(s.get("indexedRanges") or []),
                    "uncoveredRanges": [],
                    "recommendedChunks": [],
                }
                for s in sources
            ]
        labels = ", ".join(str(u["label"]) for u in unindexed[:3])
        return {
            "action": "index_sources",
            "reason": "sources_not_indexed",
            "summary": f"Index {len(unindexed)} source file(s): {labels}",
            "unindexedSources": unindexed,
            "chunkPolicy": {
                "windowPages": DEFAULT_CHUNK_WINDOW,
                "overlapPages": DEFAULT_CHUNK_OVERLAP,
                "maxPagesPerVlmStep": 5,
            },
        }

    # Rule 3: Phase 1 binary-search frontier probe before diving into topics.
    if not milestones.get("probed", False):
        preview = [
            {
                "id": t["id"],
                "order": int(t.get("order") or i + 1),
                "title": t["title"],
                "level": str(t.get("level") or "basics"),
            }
            for i, t in enumerate(
                sorted(
                    topics,
                    key=lambda x: (int(x.get("order") or 9999), LEVEL_RANK.get(str(x.get("level") or "basics"), 1)),
                )[:8]
            )
        ]
        return {
            "action": "probe_frontier",
            "reason": "chapter_not_probed",
            "summary": f'Probe frontier for "{title}" ({len(topics)} topics mapped)',
            "topicsPreview": preview,
        }

    # Rule 5: Finish any in_progress topic first.
    in_prog_topics = [
        (i, t) for i, t in enumerate(topics) if str(t.get("status") or "pending") == "in_progress"
    ]
    if in_prog_topics:
        _, chosen_t = min(
            in_prog_topics,
            key=lambda item: (
                int(item[1].get("order") or 9999),
                LEVEL_RANK.get(str(item[1].get("level") or "basics"), 1),
                item[0],
            ),
        )
        et = _enrich_topic(chosen_t, sources_by_id)
        hint = f" ({et['sourceHint']})" if et["sourceHint"] else ""
        return {
            "action": "teach_topic",
            "reason": "continue_in_progress_topic",
            "summary": f'Teach Topic "{et["title"]}"{hint}',
            "topic": et,
        }

    # Rule 6: Practice questions belonging to already-mastered topics.
    mastered_topic_ids = {t["id"] for t in topics if t.get("status") == "mastered"}
    has_remaining_topics = any(t.get("status") != "mastered" for t in topics)

    mastered_retest_due: list[tuple[int, dict[str, Any]]] = []
    mastered_retest_cooling: list[tuple[int, dict[str, Any]]] = []
    mastered_unseen: list[tuple[int, dict[str, Any]]] = []
    other_unseen_exists = False

    for idx, q in enumerate(questions):
        st = str(q.get("status") or "unseen")
        if st in DONE_QUESTION_STATUSES:
            continue
        tid = q.get("topicId")
        if tid in mastered_topic_ids:
            if st == "retest":
                ls = q.get("lastStep")
                if ls is None or (step_counter - int(ls)) >= retest_gap:
                    mastered_retest_due.append((idx, q))
                else:
                    mastered_retest_cooling.append((idx, q))
            elif st in {"unseen", "in_progress"}:
                mastered_unseen.append((idx, q))
        elif st in {"unseen", "in_progress"}:
            other_unseen_exists = True

    # Rule 6a: Spaced fresh-surface retest ready (stepCounter - lastStep >= retestGap).
    if mastered_retest_due:
        _, chosen_q = min(
            mastered_retest_due,
            key=lambda item: (
                LEVEL_RANK.get(str(item[1].get("level") or "basics"), 1),
                -1 if item[1].get("lastStep") is None else int(item[1]["lastStep"]),
                item[0],
            ),
        )
        eq = _enrich_question(chosen_q, sources_by_id, step_counter, retest_gap)
        pg = f" (p.{eq['page']})" if eq.get("page") is not None else ""
        return {
            "action": "retest_question",
            "reason": "spaced_retest_due",
            "summary": f"Retest {eq['label']}{pg}",
            "question": eq,
        }

    # Rule 6b: Unseen or in-progress question on already-mastered topics (basics->main->advanced + Rohrer interleave).
    if mastered_unseen:
        last_tid, tail_len, done_map = _recent_question_topic_state(tracker)
        chosen_q, interleaved = _select_interleaved_question(
            mastered_unseen, topic_order_map, last_tid, tail_len, done_map
        )
        eq = _enrich_question(chosen_q, sources_by_id, step_counter, retest_gap)
        pg = f" (p.{eq['page']})" if eq.get("page") is not None else ""
        return {
            "action": "solve_question",
            "reason": "interleaved_mastered_topic_question" if interleaved else "mastered_topic_question",
            "interleaved": interleaved,
            "summary": f"Solve {eq['label']}{pg}",
            "question": eq,
        }

    # Rule 6c: Flush cooling retest on mastered topics only when no pending topics & no unseen mixed questions remain.
    if mastered_retest_cooling and not has_remaining_topics and not other_unseen_exists:
        _, chosen_q = min(
            mastered_retest_cooling,
            key=lambda item: (
                LEVEL_RANK.get(str(item[1].get("level") or "basics"), 1),
                int(item[1].get("lastStep") or 0),
                item[0],
            ),
        )
        eq = _enrich_question(chosen_q, sources_by_id, step_counter, retest_gap)
        pg = f" (p.{eq['page']})" if eq.get("page") is not None else ""
        return {
            "action": "retest_question",
            "reason": "remaining_retest_flush",
            "summary": f"Retest {eq['label']}{pg}",
            "question": eq,
        }

    # Rule 7: Next pending/probed topic whose prerequisites are all mastered.
    pending_topics = [
        (i, t) for i, t in enumerate(topics) if str(t.get("status") or "pending") in {"pending", "probed"}
    ]
    if pending_topics:
        unlocked = [
            (i, t)
            for i, t in pending_topics
            if all(
                (pid not in topics_by_id) or (pid in mastered_topic_ids)
                for pid in (t.get("prereqTopicIds") or [])
            )
        ]
        cycle_fallback = False
        pool_topics = unlocked if unlocked else pending_topics
        if not unlocked:
            cycle_fallback = True
        _, chosen_t = min(
            pool_topics,
            key=lambda item: (
                int(item[1].get("order") or 9999),
                LEVEL_RANK.get(str(item[1].get("level") or "basics"), 1),
                item[0],
            ),
        )
        et = _enrich_topic(chosen_t, sources_by_id)
        hint = f" ({et['sourceHint']})" if et["sourceHint"] else ""
        out: dict[str, Any] = {
            "action": "teach_topic",
            "reason": "prereq_cycle_fallback" if cycle_fallback else "next_unlocked_topic",
            "summary": f'Teach Topic "{et["title"]}"{hint}',
            "topic": et,
        }
        if cycle_fallback:
            out["prereqCycleFallback"] = True
        return out

    # Rule 8: Any remaining unseen / in_progress / retest questions (e.g., mixed chapter-end problems).
    rem_retest_due: list[tuple[int, dict[str, Any]]] = []
    rem_unseen: list[tuple[int, dict[str, Any]]] = []
    rem_retest_cooling: list[tuple[int, dict[str, Any]]] = []
    for idx, q in enumerate(questions):
        st = str(q.get("status") or "unseen")
        if st in DONE_QUESTION_STATUSES:
            continue
        if st == "retest":
            ls = q.get("lastStep")
            if ls is None or (step_counter - int(ls)) >= retest_gap:
                rem_retest_due.append((idx, q))
            else:
                rem_retest_cooling.append((idx, q))
        elif st in {"unseen", "in_progress"}:
            rem_unseen.append((idx, q))

    if rem_retest_due:
        _, chosen_q = min(
            rem_retest_due,
            key=lambda item: (
                LEVEL_RANK.get(str(item[1].get("level") or "basics"), 1),
                -1 if item[1].get("lastStep") is None else int(item[1]["lastStep"]),
                item[0],
            ),
        )
        eq = _enrich_question(chosen_q, sources_by_id, step_counter, retest_gap)
        pg = f" (p.{eq['page']})" if eq.get("page") is not None else ""
        return {
            "action": "retest_question",
            "reason": "spaced_retest_due",
            "summary": f"Retest {eq['label']}{pg}",
            "question": eq,
        }

    if rem_unseen:
        last_tid, tail_len, done_map = _recent_question_topic_state(tracker)
        chosen_q, interleaved = _select_interleaved_question(
            rem_unseen, topic_order_map, last_tid, tail_len, done_map
        )
        eq = _enrich_question(chosen_q, sources_by_id, step_counter, retest_gap)
        pg = f" (p.{eq['page']})" if eq.get("page") is not None else ""
        return {
            "action": "solve_question",
            "reason": "remaining_chapter_question",
            "interleaved": interleaved,
            "summary": f"Solve {eq['label']}{pg}",
            "question": eq,
        }

    if rem_retest_cooling:
        _, chosen_q = min(
            rem_retest_cooling,
            key=lambda item: (
                LEVEL_RANK.get(str(item[1].get("level") or "basics"), 1),
                int(item[1].get("lastStep") or 0),
                item[0],
            ),
        )
        eq = _enrich_question(chosen_q, sources_by_id, step_counter, retest_gap)
        pg = f" (p.{eq['page']})" if eq.get("page") is not None else ""
        return {
            "action": "retest_question",
            "reason": "remaining_retest_flush",
            "summary": f"Retest {eq['label']}{pg}",
            "question": eq,
        }

    jee_mode = is_jee_profile(tracker)

    # Rule 9: Revision Sheet milestone.
    if not milestones.get("revisionSheetBuilt", False):
        sheet_label = (
            f"Build 1-2 page Formula & Trap Revision Sheet PDF (chapters/{slug}-sheet.pdf)"
            if jee_mode
            else f"Build 1-2 page Mastery & Pitfall Revision Sheet PDF (chapters/{slug}-sheet.pdf)"
        )
        return {
            "action": "build_revision_sheet",
            "reason": "revision_sheet_pending",
            "summary": sheet_label,
            "targetPath": f"chapters/{slug}-sheet.pdf",
        }

    # Rule 10: Timed assessment / NTA JEE CBT chapter mock milestone.
    if not milestones.get("cbtMockPassed", False):
        built = bool(milestones.get("cbtMockBuilt", False))
        if jee_mode:
            mock_summary = (
                f"Complete and grade timed NTA JEE CBT mock (exams/{slug}-mock.html)"
                if built
                else f"Build and take timed NTA JEE CBT chapter mock (exams/{slug}-mock.html)"
            )
        else:
            mock_summary = (
                f"Complete and debrief timed mastery assessment (exams/{slug}-mock.html)"
                if built
                else f"Build and take timed mastery assessment (exams/{slug}-mock.html)"
            )
        return {
            "action": "run_cbt_mock",
            "reason": "cbt_mock_not_passed",
            "cbtMockBuilt": built,
            "assessmentKind": "jee_cbt" if jee_mode else "mastery_assessment",
            "summary": mock_summary,
            "targetPath": f"exams/{slug}-mock.html",
        }

    # Rule 11: Chapter Complete!
    return {
        "action": "chapter_complete",
        "reason": "all_milestones_complete",
        "audioBriefBuilt": bool(milestones.get("audioBriefBuilt", False)),
        "summary": f'Chapter "{title}" 100% mastered',
    }


def compute_next_action(
    tracker: dict[str, Any],
    kg_dir: Path | str | None = None,
    include_warmup: bool = False,
) -> dict[str, Any]:
    """Compute the deterministic next action for a chapter tracker (including doubts & KG context)."""
    t = _normalize_tracker(tracker)
    base = _compute_base_action(t)
    warmup_items, due_count, top_trap = _collect_kg_signals(t, kg_dir, include_warmup=include_warmup)
    doubt_stack = t["doubtStack"]

    result: dict[str, Any]
    if doubt_stack:
        active_doubt = doubt_stack[-1]
        q_text = str(active_doubt.get("query") or "Paused doubt")
        result = {
            "slug": t["slug"],
            "title": t["title"],
            "subject": t["subject"],
            "scopeCharter": dict(t["scopeCharter"]),
            "stepCounter": t["stepCounter"],
            "action": "resolve_doubt",
            "reason": "active_doubt_on_stack",
            "summary": f'Resolve doubt: "{q_text}" (resume: {base["summary"]})',
            "activeDoubt": active_doubt,
            "doubtDepth": len(doubt_stack),
            "resumeAction": base,
            "warmupItems": warmup_items,
            "dueWarmupCount": due_count,
            "topMistakeTrap": top_trap,
        }
    else:
        result = {
            "slug": t["slug"],
            "title": t["title"],
            "subject": t["subject"],
            "scopeCharter": dict(t["scopeCharter"]),
            "stepCounter": t["stepCounter"],
            **base,
            "activeDoubt": None,
            "doubtDepth": 0,
            "resumeAction": None,
            "warmupItems": warmup_items,
            "dueWarmupCount": due_count,
            "topMistakeTrap": top_trap,
        }
    return result


# ---------------------------------------------------------------------------
# Subcommand handlers
# ---------------------------------------------------------------------------


def _emit(payload: dict[str, Any], as_json: bool, human_lines: list[str]) -> None:
    if as_json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print("\n".join(human_lines))


def _load_json_payload(file_arg: str | None, data_arg: str | None) -> Any:
    if file_arg:
        p = Path(file_arg).expanduser().resolve()
        if not p.exists():
            raise SystemExit(f"JSON file not found: {p}")
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception as exc:
            raise SystemExit(f"Failed to parse JSON file '{p}': {exc}") from exc
    if data_arg is not None:
        try:
            return json.loads(data_arg)
        except Exception as exc:
            raise SystemExit(f"Failed to parse --json-data: {exc}") from exc
    raise SystemExit("Provide either --file <json> or --json-data '<json>'.")


def cmd_init(args: argparse.Namespace) -> None:
    try:
        slug = normalize_slug(args.slug)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    chapters_dir, _ = resolve_chapters_dir(args.chapters_dir)
    target = tracker_path_for(slug, chapters_dir)
    if target.exists() and not args.overwrite:
        raise SystemExit(f"Tracker already exists: {target} (pass --overwrite to replace).")

    sources: list[dict[str, Any]] = []
    if args.source_dir:
        try:
            sources = discover_sources(Path(args.source_dir))
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc

    ts = now_iso()
    scope_input: dict[str, Any] = {}
    if getattr(args, "goal_profile", None) is not None:
        scope_input["goalProfile"] = args.goal_profile
    if getattr(args, "target_depth", None) is not None:
        scope_input["targetDepth"] = args.target_depth
    if getattr(args, "allow_web_research", None) is not None:
        scope_input["allowWebResearch"] = bool(args.allow_web_research)
    if getattr(args, "in_scope", None) is not None:
        scope_input["inScope"] = _parse_scope_items(args.in_scope)
    if getattr(args, "out_of_scope", None) is not None:
        scope_input["outOfScope"] = _parse_scope_items(args.out_of_scope)

    init_dict: dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "slug": slug,
        "title": args.title.strip(),
        "subject": args.subject,
        "active": bool(args.set_active),
        "scopeCharter": scope_input,
        "createdAt": ts,
        "updatedAt": ts,
        "sources": sources,
        "topics": [],
        "questions": [],
        "milestones": default_milestones(),
        "doubtStack": [],
        "stepCounter": 0,
        "config": {
            "retestGap": max(1, int(args.retest_gap)),
            "dailyWarmupLimit": max(0, int(args.daily_warmup_limit)),
        },
        "sessionLog": [{"ts": ts, "step": 0, "event": "init", "sourcesFound": len(sources)}],
    }
    tracker = _normalize_tracker(init_dict)

    deactivated: list[str] = []
    if tracker["active"]:
        deactivated = _deactivate_other_trackers(slug, chapters_dir)

    saved_path = save_tracker(tracker, chapters_dir, kg_dir=args.kg_dir, no_sync=args.no_sync)
    payload = {
        "ok": True,
        "slug": slug,
        "path": str(saved_path),
        "active": tracker["active"],
        "deactivated": deactivated,
        "sourcesDiscovered": len(sources),
        "tracker": tracker,
    }
    lines = [
        f"Initialized chapter tracker '{tracker['title']}' ({tracker['subject']}, slug={slug})",
        f"  File: {saved_path}",
        f"  Active: {tracker['active']} · Sources discovered: {len(sources)}",
    ]
    if sources:
        for s in sources:
            pg = f"{s['totalPages']}p" if s.get("totalPages") else "?p"
            lines.append(f"  - [{s['id']}] {s['label']} ({s['kind']}, {pg})")
    _emit(payload, args.json, lines)


def cmd_set_scope(args: argparse.Namespace) -> None:
    try:
        tracker = load_tracker(args.slug, args.chapters_dir)
    except (FileNotFoundError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc

    sc = _normalize_scope_charter(tracker.get("scopeCharter"))
    if args.goal_profile is not None:
        sc["goalProfile"] = args.goal_profile
    if args.target_depth is not None:
        sc["targetDepth"] = args.target_depth
    if args.allow_web_research is not None:
        sc["allowWebResearch"] = bool(args.allow_web_research)
    if args.in_scope is not None:
        sc["inScope"] = _parse_scope_items(args.in_scope)
    if args.out_of_scope is not None:
        sc["outOfScope"] = _parse_scope_items(args.out_of_scope)

    tracker["scopeCharter"] = sc
    _append_log(
        tracker,
        "set_scope",
        {
            "goalProfile": sc["goalProfile"],
            "targetDepth": sc["targetDepth"],
            "allowWebResearch": sc["allowWebResearch"],
        },
    )
    save_tracker(tracker, args.chapters_dir, kg_dir=args.kg_dir, no_sync=args.no_sync)

    payload = {
        "ok": True,
        "slug": tracker["slug"],
        "scopeCharter": sc,
    }
    lines = [
        f"Updated Scope Charter for '{tracker['title']}' ({tracker['slug']}):",
        f"  profile={sc['goalProfile']} · depth={sc['targetDepth']} · allowWebResearch={sc['allowWebResearch']}",
        f"  inScope ({len(sc['inScope'])}): {', '.join(sc['inScope']) or '(unrestricted)'}",
        f"  outOfScope ({len(sc['outOfScope'])}): {', '.join(sc['outOfScope']) or '(none)'}",
    ]
    _emit(payload, args.json, lines)


def cmd_add_source(args: argparse.Namespace) -> None:
    try:
        tracker = load_tracker(args.slug, args.chapters_dir)
    except (FileNotFoundError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc

    sid = str(args.id).strip()
    if not sid:
        raise SystemExit("--id cannot be empty.")

    raw_path_str = str(args.path).strip()
    is_url = bool(re.match(r"^https?://", raw_path_str, flags=re.IGNORECASE))
    src_path: Path | None = None if is_url else Path(raw_path_str).expanduser().resolve()
    resolved_path_str = raw_path_str if is_url else str(src_path)

    new_ranges: list[list[int]] = []
    for rspec in args.indexed_range or []:
        try:
            new_ranges.extend(parse_page_range_spec(rspec))
        except ValueError as exc:
            raise SystemExit(f"Invalid --indexed-range {rspec!r}: {exc}") from exc

    total_pages = args.total_pages
    if total_pages is None and src_path is not None and src_path.suffix.lower() == ".pdf" and src_path.exists():
        total_pages = _probe_pdf_pages(src_path)

    existing = next((s for s in tracker["sources"] if s.get("id") == sid), None)
    action_kind = "updated" if existing else "added"
    if existing is not None:
        existing["label"] = args.label.strip()
        existing["path"] = resolved_path_str
        existing["kind"] = args.kind
        if total_pages is not None:
            existing["totalPages"] = int(total_pages)
        if new_ranges:
            existing["indexedRanges"] = merge_ranges((existing.get("indexedRanges") or []) + new_ranges)
        if args.notes is not None:
            existing["notes"] = args.notes
        source_obj = existing
    else:
        source_obj = {
            "id": sid,
            "label": args.label.strip(),
            "path": resolved_path_str,
            "kind": args.kind,
            "totalPages": int(total_pages) if total_pages is not None else None,
            "indexedRanges": merge_ranges(new_ranges),
            "notes": args.notes or "",
        }
        tracker["sources"].append(source_obj)

    _append_log(tracker, "add_source", {"sourceId": sid, "op": action_kind})
    save_tracker(tracker, args.chapters_dir, kg_dir=args.kg_dir, no_sync=args.no_sync)
    payload = {"ok": True, "slug": tracker["slug"], "operation": action_kind, "source": source_obj}
    _emit(
        payload,
        args.json,
        [f"{action_kind.title()} source [{sid}] {source_obj['label']} ({source_obj['kind']}, pages={source_obj['totalPages']})"],
    )


def cmd_add_topics(args: argparse.Namespace) -> None:
    try:
        tracker = load_tracker(args.slug, args.chapters_dir)
    except (FileNotFoundError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc

    raw = _load_json_payload(args.file, args.json_data)
    if isinstance(raw, dict) and isinstance(raw.get("topics"), list):
        incoming = raw["topics"]
    elif isinstance(raw, list):
        incoming = raw
    elif isinstance(raw, dict) and raw.get("id"):
        incoming = [raw]
    else:
        raise SystemExit("Topics JSON must be a list of topic objects or {'topics': [...]}.")

    by_id = {t["id"]: t for t in tracker["topics"] if isinstance(t, dict) and t.get("id")}
    max_order = max((int(t.get("order") or 0) for t in tracker["topics"]), default=0)

    added = 0
    updated = 0
    preserved_status = 0

    for item in incoming:
        if not isinstance(item, dict) or not str(item.get("id") or "").strip():
            raise SystemExit("Every topic object must include a non-empty 'id'.")
        tid = str(item["id"]).strip()
        try:
            lvl = normalize_level(item.get("level"), default="basics")
        except ValueError as exc:
            raise SystemExit(f"Topic '{tid}': {exc}") from exc
        inc_status = str(item.get("status") or "pending").strip().lower()
        if inc_status not in VALID_TOPIC_STATUSES:
            raise SystemExit(f"Topic '{tid}' has invalid status {inc_status!r}; expected {VALID_TOPIC_STATUSES}.")

        src_refs = []
        for ref in item.get("sourceRefs") or []:
            if isinstance(ref, dict):
                src_refs.append(
                    {
                        "sourceId": str(ref.get("sourceId") or ""),
                        "pages": str(ref.get("pages") or ""),
                        "section": str(ref.get("section") or ""),
                    }
                )
        prereqs = [str(p).strip() for p in (item.get("prereqTopicIds") or []) if str(p).strip()]
        prop_ids = [str(p).strip() for p in (item.get("propIds") or []) if str(p).strip()]
        covered = list(item.get("coveredPages") or [])

        if tid in by_id:
            existing = by_id[tid]
            if args.overwrite:
                existing["order"] = int(item.get("order") or existing.get("order") or 1)
                existing["title"] = str(item.get("title") or existing["title"]).strip()
                existing["level"] = lvl
                existing["prereqTopicIds"] = prereqs
                existing["sourceRefs"] = src_refs
                existing["propIds"] = list(dict.fromkeys(prop_ids))
                existing["status"] = inc_status
                existing["coveredPages"] = _merge_covered_pages([], covered)
                existing["notes"] = str(item.get("notes") or "")
            else:
                if item.get("order") is not None:
                    existing["order"] = int(item["order"])
                if item.get("title"):
                    existing["title"] = str(item["title"]).strip()
                if item.get("level"):
                    existing["level"] = lvl
                if "prereqTopicIds" in item:
                    existing["prereqTopicIds"] = prereqs
                if src_refs:
                    existing["sourceRefs"] = src_refs
                if prop_ids:
                    existing["propIds"] = list(dict.fromkeys((existing.get("propIds") or []) + prop_ids))
                if covered:
                    existing["coveredPages"] = _merge_covered_pages(existing.get("coveredPages") or [], covered)
                if item.get("notes"):
                    existing["notes"] = str(item["notes"])
                # Preserve progressed status (never silently reset probed/in_progress/mastered -> pending).
                if existing.get("status") != "pending":
                    preserved_status += 1
                elif inc_status != "pending":
                    existing["status"] = inc_status
            updated += 1
        else:
            max_order += 1
            order_val = int(item["order"]) if item.get("order") is not None else max_order
            max_order = max(max_order, order_val)
            new_topic = {
                "id": tid,
                "order": order_val,
                "title": str(item.get("title") or tid).strip(),
                "level": lvl,
                "prereqTopicIds": prereqs,
                "sourceRefs": src_refs,
                "propIds": list(dict.fromkeys(prop_ids)),
                "status": inc_status,
                "coveredPages": _merge_covered_pages([], covered),
                "notes": str(item.get("notes") or ""),
            }
            tracker["topics"].append(new_topic)
            by_id[tid] = new_topic
            added += 1

    tracker["topics"].sort(
        key=lambda t: (int(t.get("order") or 9999), LEVEL_RANK.get(str(t.get("level") or "basics"), 1))
    )
    _recompute_auto_milestones(tracker)
    _append_log(tracker, "add_topics", {"added": added, "updated": updated})
    save_tracker(tracker, args.chapters_dir, kg_dir=args.kg_dir, no_sync=args.no_sync)

    payload = {
        "ok": True,
        "slug": tracker["slug"],
        "added": added,
        "updated": updated,
        "preservedStatus": preserved_status,
        "totalTopics": len(tracker["topics"]),
        "theoryComplete": tracker["milestones"]["theoryComplete"],
    }
    _emit(
        payload,
        args.json,
        [
            f"Topics synced for '{tracker['slug']}': +{added} added, {updated} updated "
            f"(total={len(tracker['topics'])}, preservedStatus={preserved_status})"
        ],
    )


def cmd_set_topic(args: argparse.Namespace) -> None:
    try:
        tracker = load_tracker(args.slug, args.chapters_dir)
    except (FileNotFoundError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc

    topic = next((t for t in tracker["topics"] if t.get("id") == args.topic_id), None)
    if topic is None:
        raise SystemExit(f"Topic '{args.topic_id}' not found in chapter '{tracker['slug']}'.")

    prev_status = topic.get("status")
    topic["status"] = args.status

    if args.prop_id:
        new_props: list[str] = []
        for raw_p in args.prop_id:
            for p in str(raw_p).split(","):
                p = p.strip()
                if p:
                    new_props.append(p)
        topic["propIds"] = list(dict.fromkeys((topic.get("propIds") or []) + new_props))

    if args.covered_pages:
        expanded = _parse_covered_pages_arg(args.covered_pages)
        topic["coveredPages"] = _merge_covered_pages(topic.get("coveredPages") or [], expanded)

    if args.notes is not None:
        topic["notes"] = args.notes

    tracker["stepCounter"] = int(tracker.get("stepCounter") or 0) + 1
    _recompute_auto_milestones(tracker)
    _append_log(
        tracker,
        "set_topic",
        {"topicId": args.topic_id, "from": prev_status, "to": args.status},
    )
    save_tracker(tracker, args.chapters_dir, kg_dir=args.kg_dir, no_sync=args.no_sync)

    payload = {
        "ok": True,
        "slug": tracker["slug"],
        "stepCounter": tracker["stepCounter"],
        "topic": topic,
        "theoryComplete": tracker["milestones"]["theoryComplete"],
    }
    _emit(
        payload,
        args.json,
        [
            f"Topic '{topic['id']}' ({topic['title']}): {prev_status} -> {topic['status']} "
            f"[step={tracker['stepCounter']}, theoryComplete={tracker['milestones']['theoryComplete']}]"
        ],
    )


def cmd_add_questions(args: argparse.Namespace) -> None:
    try:
        tracker = load_tracker(args.slug, args.chapters_dir)
    except (FileNotFoundError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc

    raw = _load_json_payload(args.file, args.json_data)
    if isinstance(raw, dict) and isinstance(raw.get("questions"), list):
        incoming = raw["questions"]
    elif isinstance(raw, list):
        incoming = raw
    elif isinstance(raw, dict) and raw.get("qid"):
        incoming = [raw]
    else:
        raise SystemExit("Questions JSON must be a list of question objects or {'questions': [...]}.")

    by_qid = {q["qid"]: q for q in tracker["questions"] if isinstance(q, dict) and q.get("qid")}
    added = 0
    updated = 0
    preserved_progress = 0

    for item in incoming:
        if not isinstance(item, dict) or not str(item.get("qid") or "").strip():
            raise SystemExit("Every question object must include a non-empty 'qid'.")
        qid = str(item["qid"]).strip()
        try:
            lvl = normalize_level(item.get("level"), default="basics")
        except ValueError as exc:
            raise SystemExit(f"Question '{qid}': {exc}") from exc
        qkind = str(item.get("kind") or "mcq").strip().lower()
        if qkind not in VALID_QUESTION_KINDS:
            raise SystemExit(f"Question '{qid}' has invalid kind {qkind!r}; expected {VALID_QUESTION_KINDS}.")
        inc_status = str(item.get("status") or "unseen").strip().lower()
        if inc_status not in VALID_QUESTION_STATUSES:
            raise SystemExit(
                f"Question '{qid}' has invalid status {inc_status!r}; expected {VALID_QUESTION_STATUSES}."
            )
        page_val = item.get("page")
        if page_val is not None:
            try:
                page_val = int(page_val)
            except (TypeError, ValueError):
                page_val = None

        if qid in by_qid:
            existing = by_qid[qid]
            # Update descriptive metadata cleanly.
            if item.get("sourceId") is not None:
                existing["sourceId"] = str(item["sourceId"]).strip()
            if item.get("label"):
                existing["label"] = str(item["label"]).strip()
            if page_val is not None:
                existing["page"] = page_val
            if "topicId" in item:
                existing["topicId"] = str(item["topicId"]).strip() if item["topicId"] else None
            if item.get("level"):
                existing["level"] = lvl
            if item.get("kind"):
                existing["kind"] = qkind

            # ZERO-REPETITION INVARIANT: NEVER downgrade progressed status or wipe attempt history!
            if existing.get("status") in PROTECTED_QUESTION_STATUSES:
                preserved_progress += 1
            elif inc_status != "unseen":
                existing["status"] = inc_status
            updated += 1
        else:
            new_q = {
                "qid": qid,
                "sourceId": str(item.get("sourceId") or "").strip(),
                "label": str(item.get("label") or qid).strip(),
                "page": page_val,
                "topicId": str(item["topicId"]).strip() if item.get("topicId") else None,
                "level": lvl,
                "kind": qkind,
                "status": inc_status,
                "attempts": max(0, int(item.get("attempts") or 0)),
                "lastMistakeCategory": item.get("lastMistakeCategory") or None,
                "lastDate": item.get("lastDate") or None,
                "lastStep": item.get("lastStep") if isinstance(item.get("lastStep"), int) else None,
            }
            tracker["questions"].append(new_q)
            by_qid[qid] = new_q
            added += 1

    _recompute_auto_milestones(tracker)
    _append_log(
        tracker,
        "add_questions",
        {"added": added, "updated": updated, "preservedProgress": preserved_progress},
    )
    save_tracker(tracker, args.chapters_dir, kg_dir=args.kg_dir, no_sync=args.no_sync)

    payload = {
        "ok": True,
        "slug": tracker["slug"],
        "added": added,
        "updated": updated,
        "preservedProgress": preserved_progress,
        "totalQuestions": len(tracker["questions"]),
        "questionsComplete": tracker["milestones"]["questionsComplete"],
    }
    _emit(
        payload,
        args.json,
        [
            f"Questions synced for '{tracker['slug']}': +{added} added, {updated} updated "
            f"(total={len(tracker['questions'])}, preservedProgress={preserved_progress})"
        ],
    )


def cmd_mark_question(args: argparse.Namespace) -> None:
    try:
        tracker = load_tracker(args.slug, args.chapters_dir)
    except (FileNotFoundError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc

    q = next((item for item in tracker["questions"] if item.get("qid") == args.qid), None)
    if q is None:
        raise SystemExit(f"Question '{args.qid}' not found in chapter '{tracker['slug']}'.")

    prev_status = q.get("status")
    tracker["stepCounter"] = int(tracker.get("stepCounter") or 0) + 1
    q["status"] = args.status
    q["attempts"] = int(q.get("attempts") or 0) + 1
    q["lastStep"] = tracker["stepCounter"]
    q["lastDate"] = today_iso()

    if args.mistake_category is not None:
        cat = str(args.mistake_category).strip()
        if cat == "" or cat.lower() == "none":
            q["lastMistakeCategory"] = None
        else:
            try:
                q["lastMistakeCategory"] = normalize_mistake_category(cat)
            except ValueError as exc:
                raise SystemExit(str(exc)) from exc

    _recompute_auto_milestones(tracker)
    _append_log(
        tracker,
        "mark_question",
        {
            "qid": args.qid,
            "from": prev_status,
            "to": args.status,
            "mistakeCategory": q.get("lastMistakeCategory"),
        },
    )
    save_tracker(tracker, args.chapters_dir, kg_dir=args.kg_dir, no_sync=args.no_sync)

    payload = {
        "ok": True,
        "slug": tracker["slug"],
        "stepCounter": tracker["stepCounter"],
        "question": q,
        "questionsComplete": tracker["milestones"]["questionsComplete"],
    }
    _emit(
        payload,
        args.json,
        [
            f"Question '{q['qid']}' ({q['label']}): {prev_status} -> {q['status']} "
            f"[attempts={q['attempts']}, step={tracker['stepCounter']}, "
            f"questionsComplete={tracker['milestones']['questionsComplete']}]"
        ],
    )


def cmd_set_milestone(args: argparse.Namespace) -> None:
    try:
        tracker = load_tracker(args.slug, args.chapters_dir)
    except (FileNotFoundError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc

    key = args.key
    val = bool(args.value)
    tracker["milestones"][key] = val
    if key == "cbtMockPassed" and val:
        tracker["milestones"]["cbtMockBuilt"] = True

    _append_log(tracker, "set_milestone", {"key": key, "value": val})
    save_tracker(tracker, args.chapters_dir, kg_dir=args.kg_dir, no_sync=args.no_sync)

    payload = {
        "ok": True,
        "slug": tracker["slug"],
        "key": key,
        "value": val,
        "milestones": tracker["milestones"],
    }
    _emit(payload, args.json, [f"Milestone '{key}' for '{tracker['slug']}' set to {val}"])


def cmd_set_active(args: argparse.Namespace) -> None:
    try:
        tracker = load_tracker(args.slug, args.chapters_dir)
    except (FileNotFoundError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc

    deactivated = _deactivate_other_trackers(tracker["slug"], args.chapters_dir)
    tracker["active"] = True
    _append_log(tracker, "set_active", {"deactivated": deactivated})
    save_tracker(tracker, args.chapters_dir, kg_dir=args.kg_dir, no_sync=args.no_sync)

    payload = {
        "ok": True,
        "slug": tracker["slug"],
        "active": True,
        "deactivated": deactivated,
    }
    _emit(
        payload,
        args.json,
        [f"Active chapter switched to '{tracker['title']}' ({tracker['slug']}); deactivated: {deactivated or 'none'}"],
    )


def cmd_pause_doubt(args: argparse.Namespace) -> None:
    try:
        tracker = load_tracker(args.slug, args.chapters_dir)
    except (FileNotFoundError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc

    return_action = args.return_action
    if not return_action:
        base = _compute_base_action(tracker)
        return_action = base["summary"]

    stack = list(tracker.get("doubtStack") or [])
    doubt_entry = {
        "id": f"d-{len(stack) + 1}",
        "query": args.query.strip(),
        "returnAction": return_action,
        "createdAt": now_iso(),
        "step": int(tracker.get("stepCounter") or 0),
    }
    stack.append(doubt_entry)
    tracker["doubtStack"] = stack
    _append_log(tracker, "pause_doubt", {"doubtId": doubt_entry["id"], "query": doubt_entry["query"]})
    save_tracker(tracker, args.chapters_dir, kg_dir=args.kg_dir, no_sync=args.no_sync)

    payload = {
        "ok": True,
        "slug": tracker["slug"],
        "pushed": doubt_entry,
        "doubtDepth": len(stack),
    }
    _emit(
        payload,
        args.json,
        [f"Paused doubt [{doubt_entry['id']}]: \"{doubt_entry['query']}\" (return: {return_action})"],
    )


def cmd_clear_doubt(args: argparse.Namespace) -> None:
    try:
        tracker = load_tracker(args.slug, args.chapters_dir)
    except (FileNotFoundError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc

    stack = list(tracker.get("doubtStack") or [])
    cleared: list[dict[str, Any]] = []
    if args.all:
        cleared = stack
        stack = []
    elif stack:
        cleared = [stack.pop()]

    tracker["doubtStack"] = stack
    _append_log(tracker, "clear_doubt", {"clearedCount": len(cleared), "remaining": len(stack)})
    save_tracker(tracker, args.chapters_dir, kg_dir=args.kg_dir, no_sync=args.no_sync)

    resume = compute_next_action(tracker, kg_dir=args.kg_dir, include_warmup=False)
    payload = {
        "ok": True,
        "slug": tracker["slug"],
        "cleared": cleared,
        "remainingDoubts": len(stack),
        "resumeAction": resume,
    }
    lines = [
        f"Cleared {len(cleared)} doubt(s) for '{tracker['slug']}' (remaining: {len(stack)})",
        f"  Resume -> [{resume['action']}] {resume['summary']}",
    ]
    _emit(payload, args.json, lines)


def cmd_next(args: argparse.Namespace) -> None:
    if args.slug:
        try:
            tracker = load_tracker(args.slug, args.chapters_dir)
        except (FileNotFoundError, ValueError) as exc:
            raise SystemExit(str(exc)) from exc
    else:
        tracker = find_active_tracker(args.chapters_dir)
        if tracker is None:
            payload = {
                "slug": None,
                "active": False,
                "action": "no_active_chapter",
                "summary": "No active chapter tracker found. Initialize one with `chapter_tracker.py init <slug> --title ... --subject ...`.",
                "warmupItems": [],
                "dueWarmupCount": 0,
            }
            _emit(payload, args.json, [payload["summary"]])
            return

    nxt = compute_next_action(tracker, kg_dir=args.kg_dir, include_warmup=bool(args.include_warmup))
    lines = [
        f"Autopilot [{nxt['slug']}] -> {nxt['action']}: {nxt['summary']}",
    ]
    if nxt.get("warmupItems"):
        ids = ", ".join(item["id"] for item in nxt["warmupItems"])
        lines.append(f"  Warmup ({len(nxt['warmupItems'])}/{nxt['dueWarmupCount']} due): {ids}")
    if nxt.get("topMistakeTrap"):
        trap = nxt["topMistakeTrap"]
        lines.append(f"  Watch trap: {trap['category']} ({trap['totalCount']} total)")
    if nxt.get("activeDoubt"):
        lines.append(f"  Active doubt: {nxt['activeDoubt']['query']}")
        lines.append(f"  Return point: {nxt['resumeAction']['action']} -> {nxt['resumeAction']['summary']}")
    _emit(nxt, args.json, lines)


def cmd_add_slices(args: argparse.Namespace) -> None:
    """Idempotent upsert of incremental reading slices into tracker['slices']."""
    try:
        tracker = load_tracker(args.slug, args.chapters_dir)
    except (FileNotFoundError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc

    raw = _load_json_payload(args.file, args.json_data)
    if isinstance(raw, dict) and isinstance(raw.get("slices"), list):
        incoming = raw["slices"]
    elif isinstance(raw, list):
        incoming = raw
    elif isinstance(raw, dict) and raw.get("id"):
        incoming = [raw]
    else:
        raise SystemExit("Slices JSON must be a list of slice objects or {'slices': [...]}.")

    slices = list(tracker.get("slices") or [])
    by_id = {s["id"]: s for s in slices if isinstance(s, dict) and s.get("id")}
    added = 0
    updated = 0
    for idx, item in enumerate(incoming):
        if not isinstance(item, dict):
            continue
        norm_s = _normalize_slice(item, len(slices) + idx)
        sid = norm_s["id"]
        if sid in by_id:
            ex = by_id[sid]
            for k in ("sourceId", "title", "pages", "notes"):
                if item.get(k):
                    ex[k] = norm_s[k]
            if "priority" in item:
                ex["priority"] = norm_s["priority"]
            if "stage" in item:
                ex["stage"] = norm_s["stage"]
            ex["updatedAt"] = now_iso()
            updated += 1
        else:
            slices.append(norm_s)
            by_id[sid] = norm_s
            added += 1

    tracker["slices"] = slices
    _append_log(tracker, "add_slices", {"added": added, "updated": updated})
    save_tracker(tracker, args.chapters_dir, kg_dir=args.kg_dir, no_sync=args.no_sync)
    payload = {"ok": True, "slug": tracker["slug"], "added": added, "updated": updated, "totalSlices": len(slices)}
    _emit(payload, args.json, [f"Slices synced for '{tracker['slug']}': +{added} added, {updated} updated (total={len(slices)})"])


def cmd_set_slice(args: argparse.Namespace) -> None:
    """Update incremental reading stage/priority/notes for an existing slice."""
    try:
        tracker = load_tracker(args.slug, args.chapters_dir)
    except (FileNotFoundError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc

    slices = list(tracker.get("slices") or [])
    target = next((s for s in slices if isinstance(s, dict) and s.get("id") == args.slice_id), None)
    if target is None:
        raise SystemExit(f"Slice '{args.slice_id}' not found in chapter '{tracker['slug']}'.")

    prev_stage = target.get("stage")
    if args.stage is not None:
        target["stage"] = args.stage
    if args.priority is not None:
        target["priority"] = max(0, min(100, int(args.priority)))
    if args.notes is not None:
        target["notes"] = args.notes
    target["updatedAt"] = now_iso()
    tracker["slices"] = slices

    _append_log(tracker, "set_slice", {"sliceId": args.slice_id, "from": prev_stage, "to": target["stage"]})
    save_tracker(tracker, args.chapters_dir, kg_dir=args.kg_dir, no_sync=args.no_sync)
    payload = {"ok": True, "slug": tracker["slug"], "slice": target}
    _emit(payload, args.json, [f"Slice '{target['id']}': {prev_stage} -> {target['stage']} (priority={target['priority']})"])


def cmd_checkpoint(args: argparse.Namespace) -> None:
    """Save a calm session-exit breadcrumb (`nextBreadcrumb`) for zero-guilt cold-start re-entry."""
    try:
        tracker = load_tracker(args.slug, args.chapters_dir)
    except (FileNotFoundError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc

    summary = str(args.breadcrumb or "").strip()
    if not summary:
        raise SystemExit("--breadcrumb cannot be empty.")
    first_action = str(getattr(args, "first_action", None) or "").strip()
    if not first_action:
        base = _compute_base_action(tracker)
        first_action = str(base.get("summary") or "")

    breadcrumb = {
        "at": now_iso(),
        "summary": summary,
        "firstMicroAction": first_action,
    }
    tracker["nextBreadcrumb"] = breadcrumb
    _append_log(tracker, "checkpoint", {"summary": summary, "firstMicroAction": first_action})
    save_tracker(tracker, args.chapters_dir, kg_dir=args.kg_dir, no_sync=args.no_sync)

    payload = {
        "ok": True,
        "slug": tracker["slug"],
        "nextBreadcrumb": breadcrumb,
    }
    lines = [
        f"Saved re-entry breadcrumb for '{tracker['title']}' ({tracker['slug']}):",
        f"  Where we stopped: {summary}",
        f"  First micro-action: {first_action or '(auto)'}",
    ]
    _emit(payload, args.json, lines)


def cmd_status(args: argparse.Namespace) -> None:
    if args.all:
        trackers = list_trackers(args.chapters_dir)
        items = []
        active_slug = None
        for tr in trackers:
            st = compute_stats(tr)
            if tr.get("active") and active_slug is None:
                active_slug = tr["slug"]
            st["nextAction"] = compute_next_action(tr, kg_dir=args.kg_dir, include_warmup=False)
            items.append(st)
        payload = {"activeSlug": active_slug, "count": len(items), "trackers": items}
        lines = [f"Chapter Trackers ({len(items)} total, active={active_slug or 'none'}):"]
        for st in items:
            mark = "*" if st["active"] else " "
            lines.append(
                f"  {mark} {st['slug']} ({st['title']}, {st['subject']}) · "
                f"Topics {st['topicsMastered']}/{st['topicsTotal']} · "
                f"Questions {st['questionsDone']}/{st['questionsTotal']} ({st['questionsRetest']} retest) · "
                f"{st['overallPct']}% · Next: {st['nextAction']['summary']}"
            )
        if not items:
            lines.append("  (none)")
        _emit(payload, args.json, lines)
        return

    if args.slug:
        try:
            tracker = load_tracker(args.slug, args.chapters_dir)
        except (FileNotFoundError, ValueError) as exc:
            raise SystemExit(str(exc)) from exc
    else:
        tracker = find_active_tracker(args.chapters_dir)
        if tracker is None:
            all_t = list_trackers(args.chapters_dir)
            if all_t:
                tracker = all_t[0]
            else:
                payload = {"active": False, "count": 0, "summary": "No chapter trackers found."}
                _emit(payload, args.json, ["No chapter trackers found."])
                return

    stats = compute_stats(tracker)
    nxt = compute_next_action(tracker, kg_dir=args.kg_dir, include_warmup=False)
    payload = {**stats, "nextAction": nxt}
    ms_on = [k for k in VALID_MILESTONES if stats["milestones"].get(k)]
    lines = [
        f"Chapter: {stats['title']} ({stats['subject']}, slug={stats['slug']}, active={stats['active']})",
        f"  Progress: {stats['overallPct']}% · Step {stats['stepCounter']}",
        f"  Topics: {stats['topicsMastered']}/{stats['topicsTotal']} mastered "
        f"({stats['topicsInProgress']} in_progress, {stats['topicsPending']} pending)",
        f"  Questions: {stats['questionsDone']}/{stats['questionsTotal']} done "
        f"({stats['questionsSolved']} solved/mastered, {stats['questionsRetest']} retest, {stats['questionsUnseen']} unseen)",
        "  By level: "
        + " | ".join(
            f"{lvl}: T {stats['byLevel'][lvl]['topicsMastered']}/{stats['byLevel'][lvl]['topicsTotal']}, "
            f"Q {stats['byLevel'][lvl]['questionsDone']}/{stats['byLevel'][lvl]['questionsTotal']}"
            for lvl in VALID_LEVELS
        ),
        f"  Milestones ({stats['milestonesDone']}/{stats['milestonesTotal']}): {', '.join(ms_on) or 'none'}",
        f"  Next: [{nxt['action']}] {nxt['summary']}",
    ]
    _emit(payload, args.json, lines)


# ---------------------------------------------------------------------------
# Argument parser (shared flags accepted both before and after subcommand)
# ---------------------------------------------------------------------------


def _add_shared_flags(subparser: argparse.ArgumentParser) -> None:
    subparser.add_argument(
        "--chapters-dir",
        default=argparse.SUPPRESS,
        help="Override chapters directory (default: <workspace>/chapters)",
    )
    subparser.add_argument(
        "--kg-dir",
        default=argparse.SUPPRESS,
        help="Override knowledge-graph directory (default: <workspace>/knowledge-graph)",
    )
    subparser.add_argument(
        "--no-sync",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Skip refreshing knowledge-graph/graph-data.js via sync-graph.mjs",
    )
    subparser.add_argument(
        "--json",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Emit structured JSON output",
    )


def _add_scope_flags(subparser: argparse.ArgumentParser) -> None:
    subparser.add_argument(
        "--goal-profile",
        default=None,
        type=normalize_goal_profile,
        help=f"Goal profile: {'|'.join(VALID_GOAL_PROFILES)}",
    )
    subparser.add_argument(
        "--target-depth",
        default=None,
        type=normalize_target_depth,
        help=f"Target depth: {'|'.join(VALID_TARGET_DEPTHS)}",
    )
    subparser.add_argument(
        "--allow-web-research",
        nargs="?",
        const=True,
        default=None,
        type=parse_bool_str,
        help="Allow bounded syllabus-scoped web research (true|false)",
    )
    subparser.add_argument(
        "--in-scope",
        action="append",
        default=None,
        help="Explicit in-scope subtopics/goals (repeatable or comma-separated)",
    )
    subparser.add_argument(
        "--out-of-scope",
        action="append",
        default=None,
        help="Explicit out-of-scope rabbit-hole fences (repeatable or comma-separated)",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Syntopik — Universal Topic & Chapter Autopilot Tracker & Deterministic State-Machine CLI"
    )
    parser.add_argument("--chapters-dir", default=None, help="Override chapters directory (or SYNT_CHAPTERS_DIR)")
    parser.add_argument("--kg-dir", default=None, help="Override knowledge-graph directory (or SYNT_KG_DIR)")
    parser.add_argument("--no-sync", action="store_true", default=False, help="Skip sync-graph.mjs refresh")
    parser.add_argument("--json", action="store_true", default=False, help="Emit structured JSON output")

    sub = parser.add_subparsers(dest="cmd", required=True)

    # 1. init
    p_init = sub.add_parser("init", help="Initialize chapters/<slug>-tracker.json and optionally scan --source-dir")
    p_init.add_argument("slug", help="Topic/chapter slug (e.g., raft-consensus, transformers, shm)")
    p_init.add_argument("--title", required=True, help="Human-readable topic or chapter title")
    p_init.add_argument(
        "--subject",
        required=True,
        type=normalize_subject,
        help="Any subject (e.g., CS, ML, Systems, Rust, Physics, Chemistry, Maths, Biology, Economics, or custom)",
    )
    p_init.add_argument("--source-dir", help="Optional directory to auto-discover study files (zero-OCR metadata probe)")
    p_init.add_argument(
        "--set-active",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Mark this chapter active and deactivate others (default: true)",
    )
    p_init.add_argument("--retest-gap", type=int, default=3, help="Steps before a retest question resurfaces (default: 3)")
    p_init.add_argument("--daily-warmup-limit", type=int, default=3, help="Max warmup items per session (default: 3)")
    p_init.add_argument("--overwrite", action="store_true", help="Overwrite an existing tracker file if present")
    _add_scope_flags(p_init)
    _add_shared_flags(p_init)
    p_init.set_defaults(func=cmd_init)

    # 1b. set-scope
    p_scope = sub.add_parser("set-scope", help="Configure or update the chapter's bounded Scope Charter")
    p_scope.add_argument("slug", help="Topic/chapter slug")
    _add_scope_flags(p_scope)
    _add_shared_flags(p_scope)
    p_scope.set_defaults(func=cmd_set_scope)

    # 2. add-source
    p_src = sub.add_parser("add-source", help="Idempotent upsert of a chapter source file by id")
    p_src.add_argument("slug")
    p_src.add_argument("--id", required=True, help="Short source ID (e.g., wb, mod, book, pyq)")
    p_src.add_argument("--label", required=True, help="Human-readable source label")
    p_src.add_argument("--path", required=True, help="Absolute or relative path to the source file")
    p_src.add_argument("--kind", required=True, choices=VALID_SOURCE_KINDS)
    p_src.add_argument("--total-pages", type=int, default=None, help="Total page count (auto-probed for PDFs if omitted)")
    p_src.add_argument(
        "--indexed-range",
        action="append",
        default=[],
        help="Indexed page range such as '1-20' (repeatable)",
    )
    p_src.add_argument("--notes", default=None, help="Optional source notes")
    _add_shared_flags(p_src)
    p_src.set_defaults(func=cmd_add_source)

    # 3. add-topics
    p_top = sub.add_parser("add-topics", help="Idempotent upsert of topic objects by id (preserves progressed status)")
    p_top.add_argument("slug")
    g_top = p_top.add_mutually_exclusive_group(required=True)
    g_top.add_argument("--file", help="Path to JSON file containing topic array or {'topics': [...]}")
    g_top.add_argument("--json-data", help="Inline JSON string containing topic array or {'topics': [...]}")
    p_top.add_argument("--overwrite", action="store_true", help="Overwrite existing topic status/coveredPages as well")
    _add_shared_flags(p_top)
    p_top.set_defaults(func=cmd_add_topics)

    # 4. set-topic
    p_st = sub.add_parser("set-topic", help="Update topic status, linked propIds, covered pages, and auto-milestones")
    p_st.add_argument("slug")
    p_st.add_argument("topic_id")
    p_st.add_argument("--status", required=True, choices=VALID_TOPIC_STATUSES)
    p_st.add_argument("--prop-id", action="append", default=[], help="Linked KG proposition ID (repeatable/comma-separated)")
    p_st.add_argument("--covered-pages", action="append", default=[], help="Covered pages like '1-9' (repeatable)")
    p_st.add_argument("--notes", default=None, help="Optional topic notes")
    _add_shared_flags(p_st)
    p_st.set_defaults(func=cmd_set_topic)

    # 5. add-questions
    p_aq = sub.add_parser("add-questions", help="Idempotent upsert of questions by qid (NEVER resets solved/mastered)")
    p_aq.add_argument("slug")
    g_aq = p_aq.add_mutually_exclusive_group(required=True)
    g_aq.add_argument("--file", help="Path to JSON file containing question array or {'questions': [...]}")
    g_aq.add_argument("--json-data", help="Inline JSON string containing question array or {'questions': [...]}")
    _add_shared_flags(p_aq)
    p_aq.set_defaults(func=cmd_add_questions)

    # 6. mark-question
    p_mq = sub.add_parser("mark-question", help="Record question outcome, stepCounter, mistake category, and auto-milestones")
    p_mq.add_argument("slug")
    p_mq.add_argument("qid")
    p_mq.add_argument("--status", required=True, choices=VALID_QUESTION_STATUSES)
    p_mq.add_argument(
        "--mistake-category",
        default=None,
        help=f"Mistake taxonomy category ({', '.join(VALID_MISTAKE_CATEGORIES)}) or 'none'",
    )
    _add_shared_flags(p_mq)
    p_mq.set_defaults(func=cmd_mark_question)

    # 7. set-milestone & set-active
    p_sm = sub.add_parser("set-milestone", help="Set a chapter milestone boolean flag")
    p_sm.add_argument("slug")
    p_sm.add_argument("key", choices=VALID_MILESTONES)
    p_sm.add_argument("--value", required=True, type=parse_bool_str, help="true or false")
    _add_shared_flags(p_sm)
    p_sm.set_defaults(func=cmd_set_milestone)

    p_sa = sub.add_parser("set-active", help="Switch the active chapter cleanly")
    p_sa.add_argument("slug")
    _add_shared_flags(p_sa)
    p_sa.set_defaults(func=cmd_set_active)

    # 8. pause-doubt & clear-doubt
    p_pd = sub.add_parser("pause-doubt", help="Push a student tangent/doubt breadcrumb onto doubtStack")
    p_pd.add_argument("slug")
    p_pd.add_argument("--query", required=True, help="The student's tangential question or doubt")
    p_pd.add_argument("--return-action", default=None, help="Explicit resume note (auto-captured from next if omitted)")
    _add_shared_flags(p_pd)
    p_pd.set_defaults(func=cmd_pause_doubt)

    p_cd = sub.add_parser("clear-doubt", help="Pop the top doubt (or --all) and return exact resumeAction")
    p_cd.add_argument("slug")
    p_cd.add_argument("--all", action="store_true", help="Clear the entire doubtStack")
    _add_shared_flags(p_cd)
    p_cd.set_defaults(func=cmd_clear_doubt)

    # 9. next
    p_nx = sub.add_parser("next", help="Deterministic Autopilot Brain — compute exact next learning action")
    p_nx.add_argument("slug", nargs="?", default=None, help="Chapter slug (auto-picks active chapter if omitted)")
    p_nx.add_argument(
        "--include-warmup",
        action="store_true",
        help="Include up to dailyWarmupLimit due/shaky KG warmup items without derailing",
    )
    _add_shared_flags(p_nx)
    p_nx.set_defaults(func=cmd_next)

    # 10. status
    p_stat = sub.add_parser("status", help="Show chapter progress stats, level breakdown, milestones, and nextAction")
    p_stat.add_argument("slug", nargs="?", default=None, help="Chapter slug (defaults to active chapter)")
    p_stat.add_argument("--all", action="store_true", help="List all chapter trackers in chapters-dir")
    _add_shared_flags(p_stat)
    p_stat.set_defaults(func=cmd_status)

    # 11. add-slices & set-slice (optional incremental reading progress)
    p_asl = sub.add_parser("add-slices", help="Idempotent upsert of incremental reading slices")
    p_asl.add_argument("slug")
    g_asl = p_asl.add_mutually_exclusive_group(required=True)
    g_asl.add_argument("--file", help="Path to JSON file containing slice array or {'slices': [...]}")
    g_asl.add_argument("--json-data", help="Inline JSON string containing slice array or {'slices': [...]}")
    _add_shared_flags(p_asl)
    p_asl.set_defaults(func=cmd_add_slices)

    p_ssl = sub.add_parser("set-slice", help="Update incremental reading stage/priority/notes for a slice")
    p_ssl.add_argument("slug")
    p_ssl.add_argument("slice_id")
    p_ssl.add_argument("--stage", choices=VALID_SLICE_STAGES, default=None)
    p_ssl.add_argument("--priority", type=int, default=None)
    p_ssl.add_argument("--notes", default=None)
    _add_shared_flags(p_ssl)
    p_ssl.set_defaults(func=cmd_set_slice)

    # 12. checkpoint (zero-guilt cold-start re-entry breadcrumb)
    p_cp = sub.add_parser("checkpoint", help="Record a session-exit breadcrumb (`nextBreadcrumb`) for zero-guilt re-entry")
    p_cp.add_argument("slug")
    p_cp.add_argument("--breadcrumb", required=True, help="1-sentence note on where you stopped and what comes next")
    p_cp.add_argument("--first-action", default=None, help="Optional tiny first micro-action for next session")
    _add_shared_flags(p_cp)
    p_cp.set_defaults(func=cmd_checkpoint)

    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
