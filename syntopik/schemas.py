"""Shared domain schemas, normalizers, and canonical constants for Syntopik v2.0.

Pure Python 3.12 standard library only (zero external dependencies).
Single source of truth for chains/hyperedges, mistake taxonomy, subjects,
mastery levels, JEE profile detection, slugs, and UTC timestamps.
"""

from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from datetime import datetime, timezone
from typing import Any

__all__ = [
    "now_iso",
    "today_iso",
    "slugify",
    "default_encompass_weight",
    "normalize_chain",
    "VALID_MISTAKE_CATEGORIES",
    "VALID_MISTAKE_CATEGORIES_SET",
    "MISTAKE_CATEGORY_ALIASES",
    "normalize_mistake_category",
    "VALID_SUBJECTS",
    "JEE_CORE_SUBJECTS",
    "SUBJECT_ALIASES",
    "normalize_subject",
    "VALID_LEVELS",
    "LEVEL_ALIASES",
    "LEVEL_RANK",
    "normalize_level",
    "normalize_level_soft",
    "VALID_GOAL_PROFILES",
    "VALID_TARGET_DEPTHS",
    "TARGET_DEPTH_ALIASES",
    "JEE_SOURCE_KINDS",
    "is_jee_profile",
]

# ---------------------------------------------------------------------------
# Time & slug helpers
# ---------------------------------------------------------------------------


def now_iso() -> str:
    """Return canonical UTC ISO-8601 timestamp (`YYYY-MM-DDTHH:MM:SSZ`)."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def today_iso() -> str:
    """Return canonical UTC ISO date (`YYYY-MM-DD`)."""
    return datetime.now(timezone.utc).date().isoformat()


def slugify(
    text: str,
    *,
    default: str = "item",
    max_len: int = 64,
    unicode_hash: bool = False,
) -> str:
    """Convert arbitrary text into a clean URL/filename-safe lowercase slug."""
    raw = str(text or "").strip()
    normalized = unicodedata.normalize("NFKD", raw)
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_only.lower()).strip("-")
    has_non_ascii = any(ord(ch) > 127 for ch in raw)
    if not slug:
        if unicode_hash and raw:
            digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8]
            return f"doc-{digest}"
        return default
    if unicode_hash and has_non_ascii:
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:6]
        return f"{slug[: max(8, max_len - 7)]}-{digest}"
    return slug[:max_len] if max_len > 0 else slug


# ---------------------------------------------------------------------------
# Knowledge hypergraph chains (`chains.jsonl`)
# ---------------------------------------------------------------------------

ZERO_ENCOMPASS_RELATIONS: frozenset[str] = frozenset(
    {"contrasts_with", "contrasts", "contrast", "analogous", "contradicts", "unrelated"}
)
BINARY_FULL_RELATIONS: frozenset[str] = frozenset(
    {"requires", "derives", "implies", "uses", "depends_on", "proves"}
)
PARTIAL_RELATIONS: frozenset[str] = frozenset(
    {"generalizes", "specializes", "extends", "refines"}
)


def default_encompass_weight(relation: str, is_joint: bool = False) -> float:
    """Return default FIRe encompassing weight in `[0.0, 1.0]` for a chain relation."""
    rel = str(relation or "requires").strip().lower()
    if rel in ZERO_ENCOMPASS_RELATIONS:
        return 0.0
    if is_joint:
        return 0.35
    if rel in BINARY_FULL_RELATIONS:
        return 0.50
    if rel in PARTIAL_RELATIONS:
        return 0.35
    return 0.50


def normalize_chain(raw: dict[str, Any]) -> dict[str, Any] | None:
    """Normalize both legacy binary `{from, to}` and multi-premise `{premises[], conclusion}` chains.

    Always populates `encompassWeight` (honoring explicit `encompassWeight` in `[0.0, 1.0]`,
    otherwise defaulting to `0.50` binary `requires`/`derives`, `0.35` per joint premise,
    and `0.0` for `contrasts_with`/`analogous`).
    """
    if not isinstance(raw, dict):
        return None
    conclusion = str(raw.get("conclusion") or raw.get("to") or "").strip()
    if not conclusion:
        return None

    raw_premises = raw.get("premises")
    premises: list[str] = []
    if isinstance(raw_premises, list):
        for p in raw_premises:
            ps = str(p or "").strip()
            if ps and ps != conclusion and ps not in premises:
                premises.append(ps)
    else:
        src = str(raw.get("from") or "").strip()
        if src and src != conclusion:
            premises = [src]

    if not premises:
        return None

    relation = str(raw.get("relation") or "requires").strip() or "requires"
    why = str(raw.get("why") or raw.get("note") or "").strip()
    is_joint = len(premises) >= 2
    chain_key = f"{'+'.join(sorted(premises))}>{conclusion}"

    raw_weight = raw.get("encompassWeight")
    if raw_weight is None:
        raw_weight = raw.get("encompass_weight")
    if isinstance(raw_weight, (int, float)) and not isinstance(raw_weight, bool) and math.isfinite(float(raw_weight)):
        encompass_weight = round(max(0.0, min(1.0, float(raw_weight))), 4)
    else:
        encompass_weight = default_encompass_weight(relation, is_joint=is_joint)

    return {
        "premises": premises,
        "conclusion": conclusion,
        "relation": relation,
        "why": why,
        "chainKey": chain_key,
        "isJoint": is_joint,
        "arity": len(premises),
        "encompassWeight": encompass_weight,
    }


# ---------------------------------------------------------------------------
# Mistake taxonomy (all 13 canonical categories + coding aliases)
# ---------------------------------------------------------------------------

VALID_MISTAKE_CATEGORIES: tuple[str, ...] = (
    "concept_gap",
    "misconception",
    "sign_error",
    "unit_trap",
    "condition_missed",
    "algebra_slip",
    "diagram_misread",
    "careless",
    "time_pressure",
    "edge_case_missed",
    "complexity_trap",
    "off_by_one",
    "state_mutation_bug",
)
VALID_MISTAKE_CATEGORIES_SET: frozenset[str] = frozenset(VALID_MISTAKE_CATEGORIES)

MISTAKE_CATEGORY_ALIASES: dict[str, str] = {
    "edge_case": "edge_case_missed",
    "boundary_missed": "edge_case_missed",
    "corner_case": "edge_case_missed",
    "tle": "complexity_trap",
    "complexity": "complexity_trap",
    "asymptotic_trap": "complexity_trap",
    "index_error": "off_by_one",
    "bounds_error": "off_by_one",
    "mutation_bug": "state_mutation_bug",
    "aliasing_bug": "state_mutation_bug",
    "concurrency_bug": "state_mutation_bug",
    "race_condition": "state_mutation_bug",
    "syntax_slip": "careless",
}


def normalize_mistake_category(raw: str) -> str:
    """Normalize a mistake category or alias; raises `ValueError` if unrecognized."""
    clean = str(raw or "").strip().lower().replace("-", "_").replace(" ", "_")
    mapped = MISTAKE_CATEGORY_ALIASES.get(clean, clean)
    if mapped not in VALID_MISTAKE_CATEGORIES_SET:
        raise ValueError(
            f"Invalid category {raw!r}. Must be one of: {', '.join(VALID_MISTAKE_CATEGORIES)}"
        )
    return mapped


# ---------------------------------------------------------------------------
# Subjects, levels, goal profiles & JEE profile detection
# ---------------------------------------------------------------------------

VALID_SUBJECTS: tuple[str, ...] = (
    "Physics",
    "Chemistry",
    "Maths",
    "Computer Science",
    "Machine Learning",
    "Biology",
    "Economics",
    "Systems",
    "General",
)
JEE_CORE_SUBJECTS: frozenset[str] = frozenset({"Physics", "Chemistry", "Maths"})
SUBJECT_ALIASES: dict[str, str] = {
    "physics": "Physics",
    "phy": "Physics",
    "chemistry": "Chemistry",
    "chem": "Chemistry",
    "maths": "Maths",
    "math": "Maths",
    "mathematics": "Maths",
    "cs": "Computer Science",
    "comp sci": "Computer Science",
    "compsci": "Computer Science",
    "computer science": "Computer Science",
    "coding": "Computer Science",
    "programming": "Computer Science",
    "software": "Computer Science",
    "software engineering": "Computer Science",
    "ml": "Machine Learning",
    "ai": "Machine Learning",
    "dl": "Machine Learning",
    "deep learning": "Machine Learning",
    "machine learning": "Machine Learning",
    "bio": "Biology",
    "biology": "Biology",
    "econ": "Economics",
    "economics": "Economics",
    "sys": "Systems",
    "systems": "Systems",
    "gen": "General",
    "general": "General",
}


def normalize_subject(value: str) -> str:
    """Normalize common STEM/CS/ML aliases or cleanly preserve/title-case any custom subject."""
    cleaned = re.sub(r"\s+", " ", str(value or "").strip())
    if not cleaned:
        raise ValueError("Subject cannot be empty.")
    key = re.sub(r"[-_]+", " ", cleaned.lower()).strip()
    key = re.sub(r"\s+", " ", key)
    if key in SUBJECT_ALIASES:
        return SUBJECT_ALIASES[key]
    if cleaned.lower() in SUBJECT_ALIASES:
        return SUBJECT_ALIASES[cleaned.lower()]
    if not cleaned.islower():
        return cleaned
    return cleaned.title()


VALID_LEVELS: tuple[str, ...] = ("basics", "main", "advanced")
LEVEL_ALIASES: dict[str, str] = {
    "basics": "basics",
    "basic": "basics",
    "foundations": "basics",
    "foundation": "basics",
    "intro": "basics",
    "introductory": "basics",
    "easy": "basics",
    "main": "main",
    "core": "main",
    "intermediate": "main",
    "medium": "main",
    "standard": "main",
    "advanced": "advanced",
    "adv": "advanced",
    "mastery": "advanced",
    "research": "advanced",
    "hard": "advanced",
}
LEVEL_RANK: dict[str, int] = {"basics": 0, "main": 1, "advanced": 2}


def normalize_level(raw: str | None, *, default: str = "basics") -> str:
    """Map canonical levels (`basics`, `main`, `advanced`) and aliases (`foundations`, `core`, `mastery`)."""
    val = str(raw or default).strip().lower()
    if val in LEVEL_ALIASES:
        return LEVEL_ALIASES[val]
    raise ValueError(
        f"Invalid level {raw!r}; expected {VALID_LEVELS} (or aliases foundations/core/mastery)."
    )


def normalize_level_soft(raw: Any, default: str = "basics") -> str:
    val = str(raw or default).strip().lower()
    return LEVEL_ALIASES.get(val, default)


VALID_GOAL_PROFILES: tuple[str, ...] = (
    "general",
    "course",
    "coding",
    "research",
    "jee",
    "exam",
)
VALID_TARGET_DEPTHS: tuple[str, ...] = (
    "foundations",
    "core",
    "mastery",
)
TARGET_DEPTH_ALIASES: dict[str, str] = {
    "foundations": "foundations",
    "foundation": "foundations",
    "basics": "foundations",
    "intro": "foundations",
    "easy": "foundations",
    "core": "core",
    "main": "core",
    "intermediate": "core",
    "medium": "core",
    "mastery": "mastery",
    "advanced": "mastery",
    "research": "mastery",
    "hard": "mastery",
}
JEE_SOURCE_KINDS: frozenset[str] = frozenset({"pyq_pdf", "pyq_bank", "module_pdf"})


def _has_jee_source_or_title_cues(title: str, sources: list[Any]) -> bool:
    title_low = str(title or "").lower()
    if "jee" in title_low or "nta" in title_low:
        return True
    for src in sources or []:
        if not isinstance(src, dict):
            continue
        skind = str(src.get("kind") or "").strip().lower()
        if skind in JEE_SOURCE_KINDS:
            return True
        s_blob = f"{src.get('label', '')} {src.get('notes', '')}".lower()
        if "jee" in s_blob or "pyq" in s_blob:
            return True
    return False


def is_jee_profile(tracker: dict[str, Any]) -> bool:
    """Return True only when tracker explicitly targets JEE or carries JEE title/source cues."""
    if not isinstance(tracker, dict):
        return False
    sources = list(tracker.get("sources") or [])
    title = str(tracker.get("title") or "")
    raw_sc = tracker.get("scopeCharter")
    gp = ""
    if isinstance(raw_sc, dict) and raw_sc.get("goalProfile"):
        gp = str(raw_sc["goalProfile"]).strip().lower()
    if gp == "jee":
        return True
    if gp in {"course", "coding", "research", "exam"}:
        return False
    return _has_jee_source_or_title_cues(title, sources)
