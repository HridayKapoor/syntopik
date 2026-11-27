"""Syntopik v3.2 — Blind-Chapter Interleaved Drill Generator (Rohrer + Newman Tier-1).

Pure Python 3.12+ standard library only (zero external imports).
Trains cross-chapter principle discrimination before solving:
  1. Pools items across >= 2 distinct chapters from `propositions.jsonl`,
     `forks.jsonl`, and `decisions.jsonl` (enriched with `mistakes.jsonl`
     `confusedWith` pairs and Chi-Feltovich-Glaser surface-feature overlap).
  2. Enforces zero-blocked-run scheduling (`chapter[i] != chapter[i-1]` when
     >= 2 chapters exist) and strips explicit chapter/topic/lecture cues from
     learner-facing prompts (`strip_chapter_cues`).
  3. Seals canonical answers (`sealedChapter`, `sealedPrinciple`, `sealedAnswer`)
     server-side inside `knowledge-graph/interleave-sessions.jsonl` under lock
     so public payloads never leak chapter labels or solutions.
  4. Grades Step-1 governing-principle selection BEFORE revealing Step-2
     solutions, tracks Newman Tier-1 accuracy & cross-chapter confusion matrix,
     logs silent telemetry to `learner_model`, and exports blind CBT specs.
"""

from __future__ import annotations

import functools
import math
import random
import re
import statistics
import sys
import unicodedata
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from syntopik.paths import resolve_kg_dir, resolve_package_root, resolve_workspace
from syntopik.schemas import normalize_subject, now_iso, slugify, today_iso
from syntopik.storage import (
    append_jsonl_locked,
    load_jsonl,
    save_jsonl_atomic,
    workspace_lock,
)

__all__ = [
    "INTERLEAVE_SESSIONS_FILENAME",
    "strip_chapter_cues",
    "build_interleave_set",
    "check_principle_step",
    "complete_interleave_session",
    "get_interleave_stats",
    "export_interleave_cbt_spec",
]

INTERLEAVE_SESSIONS_FILENAME = "interleave-sessions.jsonl"

_STOP_WORDS: frozenset[str] = frozenset(
    (
        "a an the and or of to in on at by for from with as is are was were be been being "
        "it its this that these those which who what when where how does do did can could "
        "should would will may might must not no into over under between through about "
        "given find show prove compute calculate determine explain using using"
    ).split()
)

_SURFACE_LEXICON: frozenset[str] = frozenset(
    (
        "spring block pulley pendulum elevator lift incline wedge collision wall wheel "
        "graph tree grid array matrix window heap stack queue cache lock thread stream "
        "circuit capacitor inductor resistor battery lens mirror gas piston reaction buffer "
        "acid base ring benzene alkene gradient loss layer attention token wave charge field "
        "force torque energy momentum entropy potential current flux"
    ).split()
)

_FALLBACK_DECOY_PRINCIPLES: tuple[tuple[str, str], ...] = (
    ("Conservation of Energy & Work-Energy Balance", "General Decoy"),
    ("Equilibrium / Steady-State Force & Flux Balance", "General Decoy"),
    ("Linear Superposition & Boundary-Condition Matching", "General Decoy"),
    ("Asymptotic / Limiting-Case Invariant Check", "General Decoy"),
    ("Dimensional & Monotonicity Constraint Analysis", "General Decoy"),
)

_LABEL_PREFIX_RE = re.compile(
    r"^\s*(?:"
    r"\[[^\]]{1,60}\]\s*"
    r"|\([^)]{1,60}\)\s*"
    r"|(?:chapter|chap\.?|ch\.?|unit|module|lecture|lec\.?|lesson|topic|section|sec\.?|l)\s*#?\s*\d+[a-z]?\s*[:\-–—.]\s*"
    r")+",
    re.IGNORECASE,
)

_INLINE_TAG_RE = re.compile(
    r"(?:\b(?:chapter|chap\.?|ch\.?|unit|module|lecture|lec\.?|topic)\s*#?\s*\d+[a-z]?\b\s*[:\-–—]?)",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Internal Utilities
# ---------------------------------------------------------------------------


def _resolve_kg(workspace: Path | str | None = None, kg_dir: Path | str | None = None) -> tuple[Path, Path]:
    ws = resolve_workspace(workspace)
    kg = resolve_kg_dir(ws, explicit=kg_dir)
    return ws, kg


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
        try:
            return datetime.fromisoformat(raw[:10] + "T12:00:00+00:00")
        except ValueError:
            return None


def _tokenize(text: str) -> list[str]:
    norm = unicodedata.normalize("NFKC", str(text or "")).lower()
    raw = re.findall(r"[a-z][a-z0-9_-]{1,}", norm)
    return [t for t in raw if t not in _STOP_WORDS]


def _surface_tokens(text: str) -> set[str]:
    toks = _tokenize(text)
    surf = {t for t in toks if t in _SURFACE_LEXICON}
    if not surf:
        surf = {t for t in toks if len(t) >= 4}
    return surf


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _normalize_text_key(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip().lower())


def _clip(text: str, max_chars: int = 120) -> str:
    s = re.sub(r"\s+", " ", str(text or "").strip())
    if len(s) <= max_chars:
        return s
    cut = s[: max(8, max_chars - 3)].rstrip()
    if " " in cut and len(cut) > 16:
        cut = cut.rsplit(" ", 1)[0].rstrip(" ,;:-")
    return cut + "..."


def _normalize_filter_set(raw: str | list[str] | tuple[str, ...] | set[str] | None, *, is_subject: bool = False) -> set[str]:
    if raw is None:
        return set()
    items = [raw] if isinstance(raw, str) else list(raw)
    out: set[str] = set()
    for it in items:
        s = str(it or "").strip()
        if not s:
            continue
        for part in s.split(","):
            p = part.strip()
            if not p:
                continue
            if is_subject:
                try:
                    out.add(normalize_subject(p).lower())
                except ValueError:
                    out.add(p.lower())
            else:
                out.add(p.lower())
    return out


def _may_contain(text: str, needle: str) -> bool:
    """Cheap pre-check before a case-insensitive regex: False only when a match is impossible.

    Only trusted for ASCII text, where re.IGNORECASE and str.lower() agree exactly.
    """
    if not (text.isascii() and needle.isascii()):
        return True
    return needle.lower() in text.lower()


@functools.lru_cache(maxsize=4096)
def _phrase_patterns(phrase: str) -> tuple[re.Pattern[str], ...]:
    """Compiled chapter/topic cue patterns, cached: a vault reuses a few dozen labels thousands of times."""
    esc = re.escape(phrase)
    return (
        # Bracketed/parenthesized forms like "(SHM)" or "[Electrostatics]"
        re.compile(rf"[\[\(]\s*{esc}\s*[\]\)]", re.IGNORECASE),
        # Prefix form "In SHM, ..." or "SHM: ..."
        re.compile(rf"\b(?:in|for|from|under|of)\s+{esc}\b\s*[,:\-–—]?", re.IGNORECASE),
        re.compile(rf"(?<![A-Za-z0-9]){esc}(?![A-Za-z0-9])\s*[:\-–—]?", re.IGNORECASE),
    )


def strip_chapter_cues(
    text: str,
    *,
    chapter: str = "",
    topic: str = "",
    subject: str = "",
    prop_id: str = "",
) -> str:
    """Strip explicit chapter, topic, lecture-number, and propId labels from learner-facing prompts."""
    s = str(text or "").strip()
    if not s:
        return ""

    # Remove leading bracketed / lecture / chapter prefixes
    s = _LABEL_PREFIX_RE.sub("", s)
    s = _INLINE_TAG_RE.sub("", s)

    # Remove exact propId if embedded
    pid = str(prop_id or "").strip()
    if pid and len(pid) >= 3 and _may_contain(s, pid):
        s = re.sub(rf"(?<![A-Za-z0-9_-]){re.escape(pid)}(?![A-Za-z0-9_-])", "", s, flags=re.IGNORECASE)

    # Remove explicit chapter / topic strings (longest first, skip ultra-short generic words)
    phrases: list[str] = []
    for raw_label in (chapter, topic):
        lbl = str(raw_label or "").strip()
        if not lbl:
            continue
        phrases.append(lbl)
        # Also strip parenthetical sub-labels inside chapter name e.g. "Simple Harmonic Motion (SHM)"
        paren_parts = re.findall(r"\(([^)]+)\)", lbl)
        for pp in paren_parts:
            if len(pp.strip()) >= 2:
                phrases.append(pp.strip())
        base_lbl = re.sub(r"\s*\([^)]*\)\s*", " ", lbl).strip()
        if base_lbl and base_lbl != lbl:
            phrases.append(base_lbl)
        # Also strip "Ch1 Kinematics" -> "Kinematics"
        no_prefix = re.sub(r"^(?:ch(?:apter)?\.?\s*\d+\s*[:\-–—]?\s*|l\d+\s*[:\-–—]?\s*)", "", base_lbl, flags=re.I).strip()
        if no_prefix and len(no_prefix) >= 4:
            phrases.append(no_prefix)

    # Deduplicate longest first so full phrase matches before fragments
    seen_p: set[str] = set()
    uniq_phrases: list[str] = []
    for p in sorted(phrases, key=len, reverse=True):
        pl = p.lower()
        if pl in seen_p or len(p) < 2:
            continue
        seen_p.add(pl)
        uniq_phrases.append(p)

    for phrase in uniq_phrases:
        if not _may_contain(s, phrase):
            continue
        for pattern in _phrase_patterns(phrase):
            s = pattern.sub("", s)

    # Clean leftover empty punctuation / double spaces
    s = re.sub(r"\(\s*\)|\[\s*\]", "", s)
    s = re.sub(r"^\s*[,;:\-–—.]+\s*", "", s)
    s = re.sub(r"\s+([,.;:?!])", r"\1", s)
    s = re.sub(r"\s{2,}", " ", s).strip()

    if s and s[0].islower():
        s = s[0].upper() + s[1:]
    return s


def _clean_principle_label(title: str, *, chapter: str = "", topic: str = "", prop_id: str = "") -> str:
    cleaned = strip_chapter_cues(title, chapter=chapter, topic=topic, prop_id=prop_id)
    if not cleaned or len(cleaned) < 4:
        cleaned = re.sub(r"\s+", " ", str(title or "").strip())
    return _clip(cleaned, 88)


# ---------------------------------------------------------------------------
# Candidate Pool Construction
# ---------------------------------------------------------------------------


def _build_candidate_pool(
    kg_dir: Path,
    *,
    subject_filter: set[str],
    chapter_filter: set[str],
) -> tuple[list[dict[str, Any]], dict[str, set[str]]]:
    props = load_jsonl(kg_dir / "propositions.jsonl")
    forks = load_jsonl(kg_dir / "forks.jsonl")
    decisions = load_jsonl(kg_dir / "decisions.jsonl")
    mistakes = load_jsonl(kg_dir / "mistakes.jsonl")

    props_by_id: dict[str, dict[str, Any]] = {}
    for p in props:
        pid = str(p.get("id") or "").strip()
        if pid:
            props_by_id[pid] = p

    # Historical confusion map: chapter_low / propId -> set of confused labels
    confusion_map: dict[str, set[str]] = {}
    for m in mistakes:
        cw = str(m.get("confusedWith") or "").strip()
        if not cw:
            continue
        ch_low = str(m.get("chapter") or "").strip().lower()
        pid = str(m.get("propId") or "").strip()
        if ch_low:
            confusion_map.setdefault(ch_low, set()).add(cw.lower())
        if pid:
            confusion_map.setdefault(pid.lower(), set()).add(cw.lower())

    candidates: list[dict[str, Any]] = []
    seen_keys: set[str] = set()

    def _matches_scope(subj: str, chap: str) -> bool:
        if subject_filter:
            try:
                snorm = normalize_subject(subj).lower()
            except ValueError:
                snorm = subj.lower()
            if snorm not in subject_filter and subj.lower() not in subject_filter:
                return False
        if chapter_filter:
            cl = chap.lower()
            if cl not in chapter_filter and not any(cf in cl for cf in chapter_filter):
                return False
        return True

    # 1. Blunder Forks (high-value realistic pre-blunder decision points)
    for f in forks:
        fid = str(f.get("id") or "").strip()
        pid = str(f.get("propId") or "").strip()
        linked = props_by_id.get(pid) or {}
        subj = str(f.get("subject") or linked.get("subject") or "General").strip() or "General"
        chap = str(f.get("chapter") or linked.get("chapter") or subj).strip() or subj
        if not _matches_scope(subj, chap):
            continue
        prefix = f.get("prefixSteps") or []
        prefix_str = " ".join(str(x).strip() for x in prefix if str(x).strip()) if isinstance(prefix, list) else str(prefix or "")
        fork_prompt = str(f.get("forkPrompt") or "").strip()
        raw_prompt = f"{prefix_str} — {fork_prompt}".strip(" —") if prefix_str and fork_prompt else (fork_prompt or prefix_str)
        if not raw_prompt:
            continue
        raw_principle = str(linked.get("title") or f.get("principle") or f.get("title") or f"Governing rule for {pid or fid}").strip()
        sealed_answer = str(f.get("sealedWinningMove") or linked.get("statement") or "").strip()
        blind_prompt = strip_chapter_cues(raw_prompt, chapter=chap, topic=str(f.get("topic") or ""), subject=subj, prop_id=pid)
        if not blind_prompt:
            blind_prompt = _clip(raw_prompt, 220)
        key = f"fork:{fid or pid}:{_normalize_text_key(blind_prompt)[:48]}"
        if key in seen_keys:
            continue
        seen_keys.add(key)
        candidates.append(
            {
                "sourceKind": "fork",
                "sourceId": fid or pid,
                "propId": pid,
                "subject": subj,
                "chapter": chap,
                "topic": str(f.get("topic") or ""),
                "rawPrompt": raw_prompt,
                "blindPrompt": _clip(blind_prompt, 260),
                "step2Prompt": _clip(blind_prompt, 260),
                "principle": _clean_principle_label(raw_principle, chapter=chap, prop_id=pid),
                "rawPrinciple": raw_principle,
                "answer": sealed_answer or raw_principle,
                "surfaceTokens": _surface_tokens(f"{raw_prompt} {raw_principle}"),
                "priority": 3 if str(f.get("status") or "").lower() != "mastered" else 2,
            }
        )

    # 2. Propositions (with cues or statement)
    for p in props:
        pid = str(p.get("id") or "").strip()
        if not pid or bool(p.get("archived")) or bool(p.get("suspended")):
            continue
        subj = str(p.get("subject") or "General").strip() or "General"
        chap = str(p.get("chapter") or subj).strip() or subj
        if not _matches_scope(subj, chap):
            continue
        title = str(p.get("title") or pid).strip()
        stmt = str(p.get("statement") or "").strip()
        cues = [c for c in (p.get("cues") or []) if isinstance(c, dict) and str(c.get("q") or "").strip()]

        if cues:
            c0 = cues[0]
            raw_prompt = str(c0.get("q") or "").strip()
            answer = str(c0.get("a") or stmt or title).strip()
        elif stmt:
            # Turn statement into an active principle-identification scenario without giving away the chapter
            raw_prompt = f"Which governing principle applies to this situation: {stmt}"
            answer = stmt
        else:
            raw_prompt = f"Identify the governing principle and core invariant for: {title}"
            answer = title

        blind_prompt = strip_chapter_cues(raw_prompt, chapter=chap, topic=str(p.get("topic") or ""), subject=subj, prop_id=pid)
        if not blind_prompt or len(blind_prompt) < 12:
            blind_prompt = f"Determine the governing principle for the physical/mathematical relation: {_clip(stmt or title, 180)}"
            blind_prompt = strip_chapter_cues(blind_prompt, chapter=chap, subject=subj, prop_id=pid) or blind_prompt

        status = str(p.get("status") or "planned").lower()
        prio = 3 if status in ("shaky", "misconception") else (2 if status == "solid" else 1)
        key = f"prop:{pid}"
        if key in seen_keys:
            continue
        seen_keys.add(key)
        candidates.append(
            {
                "sourceKind": "proposition",
                "sourceId": pid,
                "propId": pid,
                "subject": subj,
                "chapter": chap,
                "topic": str(p.get("topic") or ""),
                "rawPrompt": raw_prompt,
                "blindPrompt": _clip(blind_prompt, 260),
                "step2Prompt": _clip(
                    strip_chapter_cues(cues[0]["q"], chapter=chap, prop_id=pid)
                    if cues
                    else f"Apply the selected principle to derive or state the key result: {blind_prompt}",
                    260,
                ),
                "principle": _clean_principle_label(title, chapter=chap, prop_id=pid),
                "rawPrinciple": title,
                "answer": _clip(answer, 280),
                "surfaceTokens": _surface_tokens(f"{raw_prompt} {stmt} {title}"),
                "priority": prio,
            }
        )

    # 3. ShadowBox Decisions (`decisions.jsonl`)
    for idx, d in enumerate(decisions):
        pid = str(d.get("propId") or "").strip()
        linked = props_by_id.get(pid) or {}
        subj = str(d.get("subject") or linked.get("subject") or "General").strip() or "General"
        chap = str(d.get("chapter") or linked.get("chapter") or subj).strip() or subj
        if not _matches_scope(subj, chap):
            continue
        raw_prompt = str(d.get("prompt") or d.get("stem") or d.get("problemCue") or "").strip()
        first_move = str(d.get("firstMove") or d.get("expectedFirstMove") or d.get("principle") or linked.get("title") or "").strip()
        if not raw_prompt or not first_move:
            continue
        blind_prompt = strip_chapter_cues(raw_prompt, chapter=chap, subject=subj, prop_id=pid)
        if not blind_prompt:
            continue
        key = f"dec:{d.get('id') or idx}:{_normalize_text_key(blind_prompt)[:48]}"
        if key in seen_keys:
            continue
        seen_keys.add(key)
        candidates.append(
            {
                "sourceKind": "decision",
                "sourceId": str(d.get("id") or f"dec-{idx + 1}"),
                "propId": pid,
                "subject": subj,
                "chapter": chap,
                "topic": "",
                "rawPrompt": raw_prompt,
                "blindPrompt": _clip(blind_prompt, 260),
                "step2Prompt": _clip(f"State the kill-cue and exact first move: {blind_prompt}", 260),
                "principle": _clean_principle_label(first_move, chapter=chap, prop_id=pid),
                "rawPrinciple": first_move,
                "answer": _clip(str(d.get("killCue") or first_move), 240),
                "surfaceTokens": _surface_tokens(f"{raw_prompt} {first_move}"),
                "priority": 3 if d.get("correct") is False else 1,
            }
        )

    return candidates, confusion_map


def _select_zero_blocked_run(
    candidates: list[dict[str, Any]],
    *,
    n: int,
    confusion_map: dict[str, set[str]],
    rng: random.Random,
) -> list[dict[str, Any]]:
    """Select up to `n` items spanning >= 2 chapters (when available) with zero consecutive same-chapter items."""
    if not candidates:
        return []

    by_chapter: dict[str, list[dict[str, Any]]] = {}
    for c in candidates:
        by_chapter.setdefault(c["chapter"], []).append(c)

    # Sort each chapter's bucket by priority desc + deterministic tie-break
    for ch, items in by_chapter.items():
        rng.shuffle(items)
        items.sort(key=lambda x: (-int(x.get("priority") or 0), x["sourceId"]))

    selected: list[dict[str, Any]] = []
    used_ids: set[tuple[str, str]] = set()
    target = min(max(1, int(n)), len(candidates))

    # Pick initial item from chapter with highest priority & cross-chapter confusion/overlap
    all_chapters = sorted(by_chapter.keys())
    rng.shuffle(all_chapters)

    prev_chapter: str | None = None
    prev_tokens: set[str] = set()

    while len(selected) < target:
        available_chapters = [
            ch for ch in by_chapter
            if any((it["sourceKind"], it["sourceId"]) not in used_ids for it in by_chapter[ch])
        ]
        if not available_chapters:
            break

        # Enforce zero-blocked-run: exclude prev_chapter whenever another chapter is available
        non_prev = [ch for ch in available_chapters if ch != prev_chapter]
        pool_chapters = non_prev if non_prev else available_chapters

        # Score candidate chapters: favor underrepresented chapters, confused pairs, and high surface overlap
        ch_counts = Counter(x["chapter"] for x in selected)
        best_choice: tuple[float, dict[str, Any]] | None = None
        for ch in pool_chapters:
            next_item = next(
                (it for it in by_chapter[ch] if (it["sourceKind"], it["sourceId"]) not in used_ids),
                None,
            )
            if next_item is None:
                continue
            diversity_bonus = 2.0 / (1.0 + ch_counts[ch])
            overlap = _jaccard(prev_tokens, next_item["surfaceTokens"]) if prev_tokens else 0.0
            conf_bonus = 0.0
            if prev_chapter:
                prev_confs = confusion_map.get(prev_chapter.lower(), set())
                cur_confs = confusion_map.get(ch.lower(), set())
                if ch.lower() in prev_confs or prev_chapter.lower() in cur_confs:
                    conf_bonus = 1.0
            score = diversity_bonus + 0.8 * overlap + conf_bonus + 0.15 * float(next_item.get("priority") or 0) + rng.uniform(0.0, 0.05)
            if best_choice is None or score > best_choice[0]:
                best_choice = (score, next_item)

        if best_choice is None:
            break
        chosen = best_choice[1]
        selected.append(chosen)
        used_ids.add((chosen["sourceKind"], chosen["sourceId"]))
        prev_chapter = chosen["chapter"]
        prev_tokens = chosen["surfaceTokens"]

    return selected


def _build_principle_choices(
    item: dict[str, Any],
    all_candidates: list[dict[str, Any]],
    *,
    confusion_map: dict[str, set[str]],
    rng: random.Random,
    include_dont_know: bool = True,
) -> tuple[list[dict[str, Any]], str, dict[str, str]]:
    """Build 4 plausible principle choices (+ optional 'I don't know' as 5th) and return `(choices, correct_id, opt_chapter_map)`."""
    true_principle = item["principle"]
    true_norm = _normalize_text_key(true_principle)
    true_chap = item["chapter"]
    true_surf = item["surfaceTokens"]
    confs = confusion_map.get(true_chap.lower(), set()) | confusion_map.get(item["propId"].lower(), set())

    # Collect cross-chapter decoy principles first (sorted by confusion & surface-feature similarity)
    decoy_pool: list[tuple[float, str, str]] = []
    seen_norms: set[str] = {true_norm}

    for other in all_candidates:
        p_text = other["principle"]
        p_norm = _normalize_text_key(p_text)
        if not p_text or p_norm in seen_norms:
            continue
        is_cross_ch = other["chapter"].lower() != true_chap.lower()
        sim = _jaccard(true_surf, other["surfaceTokens"])
        conf_hit = 1.0 if (other["chapter"].lower() in confs or p_norm in confs) else 0.0
        cross_bonus = 2.0 if is_cross_ch else 0.2
        score = cross_bonus + conf_hit + sim + rng.uniform(0.0, 0.05)
        decoy_pool.append((score, p_text, other["chapter"]))
        seen_norms.add(p_norm)

    decoy_pool.sort(key=lambda t: -t[0])
    chosen_decoys: list[tuple[str, str]] = [(p, ch) for _, p, ch in decoy_pool[:3]]

    # Pad to 3 decoys (4 principle options total) using domain-neutral conceptual decoys if vault is tiny
    for fallback_text, fallback_ch in _FALLBACK_DECOY_PRINCIPLES:
        if len(chosen_decoys) >= 3:
            break
        fn = _normalize_text_key(fallback_text)
        if fn not in seen_norms:
            chosen_decoys.append((fallback_text, fallback_ch))
            seen_norms.add(fn)

    # Assemble 4 principle entries and shuffle deterministically
    raw_entries: list[tuple[str, str, bool]] = [(true_principle, true_chap, True)]
    for d_text, d_chap in chosen_decoys[:3]:
        raw_entries.append((d_text, d_chap, False))
    rng.shuffle(raw_entries)

    labels = ["A", "B", "C", "D", "E"]
    choices: list[dict[str, Any]] = []
    correct_id = "A"
    opt_chapter_map: dict[str, str] = {}

    for idx, (text, ch, is_true) in enumerate(raw_entries):
        oid = labels[idx]
        choices.append({"id": oid, "text": text})
        opt_chapter_map[oid] = ch
        if is_true:
            correct_id = oid

    if include_dont_know:
        dk_id = labels[len(choices)]
        choices.append({"id": dk_id, "text": "I don't know", "isDontKnow": True})
        opt_chapter_map[dk_id] = ""

    return choices, correct_id, opt_chapter_map


# ---------------------------------------------------------------------------
# Public API 1: Build Blind-Chapter Interleaved Set
# ---------------------------------------------------------------------------


def build_interleave_set(
    workspace: Path | str | None = None,
    *,
    n: int = 6,
    subjects: str | list[str] | None = None,
    mode: str = "principle_first",
    seed: int | None = None,
    kg_dir: Path | str | None = None,
    chapters: str | list[str] | None = None,
) -> dict[str, Any]:
    """Build a blind-chapter interleaved drill, sealing true chapters/principles/answers on disk."""
    _ws, kg = _resolve_kg(workspace, kg_dir)
    subj_filter = _normalize_filter_set(subjects, is_subject=True)
    chap_filter = _normalize_filter_set(chapters, is_subject=False)
    rng = random.Random(seed) if seed is not None else random.Random()

    candidates, confusion_map = _build_candidate_pool(
        kg,
        subject_filter=subj_filter,
        chapter_filter=chap_filter,
    )
    if not candidates:
        return {
            "ok": True,
            "sessionId": None,
            "mode": str(mode or "principle_first"),
            "count": 0,
            "distinctChaptersCount": 0,
            "blinded": True,
            "items": [],
            "emptyReason": "No propositions, blunder forks, or decision items available in scope.",
        }

    selected = _select_zero_blocked_run(candidates, n=max(1, int(n)), confusion_map=confusion_map, rng=rng)
    distinct_chapters = {it["chapter"] for it in selected}
    session_id = f"ilv-{uuid.uuid4().hex[:8]}"
    created_at = now_iso()

    sealed_items: list[dict[str, Any]] = []
    public_items: list[dict[str, Any]] = []

    for idx, cand in enumerate(selected, start=1):
        item_id = f"I{idx}"
        choices, correct_id, opt_chapter_map = _build_principle_choices(
            cand,
            candidates,
            confusion_map=confusion_map,
            rng=rng,
            include_dont_know=True,
        )
        sealed_items.append(
            {
                "itemId": item_id,
                "order": idx,
                "sourceKind": cand["sourceKind"],
                "sourceId": cand["sourceId"],
                "prompt": cand["blindPrompt"],
                "principleChoices": choices,
                "sealedChapter": cand["chapter"],
                "sealedTopic": cand["topic"],
                "sealedSubject": cand["subject"],
                "sealedPropId": cand["propId"],
                "sealedPrinciple": cand["principle"],
                "sealedPrincipleId": correct_id,
                "sealedOptionChapters": opt_chapter_map,
                "sealedStep2Prompt": cand["step2Prompt"],
                "sealedAnswer": cand["answer"],
                "step1Result": None,
                "step2Result": None,
            }
        )
        # Strict zero-leak public item: NO chapter, NO propId, NO canonical principle/answer!
        public_items.append(
            {
                "itemId": item_id,
                "order": idx,
                "prompt": cand["blindPrompt"],
                "principleChoices": choices,
                "stage": "principle_select",
                "blinded": True,
            }
        )

    session_row: dict[str, Any] = {
        "schemaVersion": 1,
        "sessionId": session_id,
        "createdAt": created_at,
        "completedAt": None,
        "status": "active",
        "mode": str(mode or "principle_first"),
        "seed": seed,
        "distinctChaptersCount": len(distinct_chapters),
        "items": sealed_items,
        "summary": None,
    }

    sessions_path = kg / INTERLEAVE_SESSIONS_FILENAME
    append_jsonl_locked(sessions_path, session_row)

    out: dict[str, Any] = {
        "ok": True,
        "sessionId": session_id,
        "createdAt": created_at,
        "mode": session_row["mode"],
        "count": len(public_items),
        "distinctChaptersCount": len(distinct_chapters),
        "blinded": True,
        "items": public_items,
    }
    if len(distinct_chapters) < 2:
        out["note"] = "Only 1 chapter available in scope; add propositions from >= 2 chapters for full Rohrer interleaving."
    return out


# ---------------------------------------------------------------------------
# Public API 2: Grade Step-1 Principle Selection (Before Step-2 Reveal)
# ---------------------------------------------------------------------------


def _match_selected_principle(
    choices: list[dict[str, Any]],
    selected_principle: Any,
) -> tuple[str | None, str, bool]:
    """Return `(matched_opt_id, matched_text, is_dont_know)`."""
    raw = str(selected_principle or "").strip()
    low = _normalize_text_key(raw)
    if not low or low in ("i don't know", "i dont know", "idk", "skip", "unknown", "none"):
        dk = next((c for c in choices if c.get("isDontKnow")), None)
        return (dk["id"] if dk else None), "I don't know", True

    # 1. Exact option ID match (e.g. "A", "b")
    for c in choices:
        if str(c.get("id") or "").strip().lower() == low:
            return str(c["id"]), str(c.get("text") or ""), bool(c.get("isDontKnow"))

    # 2. Exact normalized principle text match
    for c in choices:
        if _normalize_text_key(str(c.get("text") or "")) == low:
            return str(c["id"]), str(c.get("text") or ""), bool(c.get("isDontKnow"))

    # 3. Substring fallback if unambiguous
    subs = [c for c in choices if not c.get("isDontKnow") and low in _normalize_text_key(str(c.get("text") or ""))]
    if len(subs) == 1:
        return str(subs[0]["id"]), str(subs[0].get("text") or ""), False

    return None, raw, False


def _parse_confidence(raw: Any, *, default: float | None = 0.5) -> float | None:
    """Parse confidence in [0.0, 1.0] (or 0..100%), preserving 0.0 and rejecting bool/NaN/Inf."""
    if raw is None or isinstance(raw, bool):
        return default
    if isinstance(raw, (int, float)):
        val = float(raw)
    elif isinstance(raw, str):
        s = raw.strip()
        if not s:
            return default
        try:
            val = float(s)
        except ValueError:
            return default
    else:
        return default
    if not math.isfinite(val):
        return default
    if 1.0 < val <= 100.0:
        val /= 100.0
    return round(max(0.0, min(1.0, val)), 4)


def check_principle_step(
    workspace: Path | str | None = None,
    *,
    session_id: str,
    item_id: str,
    selected_principle: str,
    confidence: float = 0.5,
    duration_ms: int | None = None,
    kg_dir: Path | str | None = None,
) -> dict[str, Any]:
    """Grade Step-1 governing-principle identification BEFORE revealing Step-2 solution."""
    _ws, kg = _resolve_kg(workspace, kg_dir)
    sessions_path = kg / INTERLEAVE_SESSIONS_FILENAME

    parsed_conf = _parse_confidence(confidence, default=0.5)
    conf_clamped = 0.5 if parsed_conf is None else parsed_conf

    dur_int: int | None = None
    if isinstance(duration_ms, (int, float)) and not isinstance(duration_ms, bool) and math.isfinite(float(duration_ms)) and float(duration_ms) > 0:
        dur_int = int(round(float(duration_ms)))

    with workspace_lock(sessions_path):
        rows = load_jsonl(sessions_path)
        sess_idx = next((i for i, r in enumerate(rows) if str(r.get("sessionId") or "") == str(session_id)), None)
        if sess_idx is None:
            return {"ok": False, "error": f"Interleave session '{session_id}' not found."}

        session = rows[sess_idx]
        items = session.get("items") or []
        item = next((it for it in items if str(it.get("itemId") or "") == str(item_id)), None)
        if item is None:
            return {"ok": False, "error": f"Item '{item_id}' not found in session '{session_id}'."}

        choices = item.get("principleChoices") or []
        opt_id, opt_text, is_dk = _match_selected_principle(choices, selected_principle)
        expected_id = str(item.get("sealedPrincipleId") or "")
        expected_principle = str(item.get("sealedPrinciple") or "")
        is_correct = (not is_dk) and (
            (opt_id is not None and opt_id == expected_id)
            or (_normalize_text_key(opt_text) == _normalize_text_key(expected_principle))
        )

        opt_ch_map = item.get("sealedOptionChapters") or {}
        guessed_chapter = str(opt_ch_map.get(opt_id or "", "") or "").strip() if not is_correct else ""
        if guessed_chapter == item.get("sealedChapter"):
            guessed_chapter = ""

        step1_rec = {
            "checkedAt": now_iso(),
            "selectedOptionId": opt_id,
            "selectedPrinciple": opt_text,
            "correct": bool(is_correct),
            "dontKnow": bool(is_dk),
            "confidence": conf_clamped,
            "durationMs": dur_int,
            "confusedChapter": guessed_chapter or None,
        }
        item["step1Result"] = step1_rec
        rows[sess_idx] = session
        save_jsonl_atomic(sessions_path, rows)

        checked_items = [it for it in items if isinstance(it.get("step1Result"), dict)]
        t1_hits = sum(1 for it in checked_items if it["step1Result"].get("correct"))
        t1_acc = round((t1_hits / len(checked_items)) * 100.0, 1) if checked_items else 0.0

    # Silent non-fatal Tier-3 telemetry into learner_model
    try:
        from syntopik.learner_model import record_telemetry_event

        record_telemetry_event(
            kg,
            domain=str(item.get("sealedSubject") or "General"),
            prop_id=str(item.get("sealedPropId") or "") or None,
            kind="interleave",
            correct=bool(is_correct),
            difficulty=1520.0,
            confidence=conf_clamped,
            duration_ms=dur_int,
            hints_used=0,
            mistake_category=None if is_correct else "condition_missed",
            skipped=bool(is_dk),
        )
    except Exception:
        pass

    # Reveal Step-1 diagnosis and unlock Step-2 prompt, keeping sealedAnswer hidden until Step-2/completion
    return {
        "ok": True,
        "sessionId": session_id,
        "itemId": item_id,
        "stage": "principle_checked",
        "correct": bool(is_correct),
        "dontKnow": bool(is_dk),
        "selectedOptionId": opt_id,
        "selectedPrinciple": opt_text,
        "expectedPrincipleId": expected_id,
        "expectedPrinciple": expected_principle,
        "revealedChapter": str(item.get("sealedChapter") or ""),
        "confusedChapter": guessed_chapter or None,
        "confidence": conf_clamped,
        "durationMs": dur_int,
        "step2Unlocked": True,
        "step2Prompt": str(item.get("sealedStep2Prompt") or item.get("prompt") or ""),
        "tier1CheckedCount": len(checked_items),
        "tier1AccuracyPct": t1_acc,
    }


# ---------------------------------------------------------------------------
# Public API 3: Complete Session, Confusion Matrix, Reviews & Mistakes
# ---------------------------------------------------------------------------


def _normalize_results_map(results: Any) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    if isinstance(results, dict):
        for k, v in results.items():
            if isinstance(v, dict):
                out[str(k)] = v
            elif isinstance(v, bool):
                out[str(k)] = {"solved": v}
    elif isinstance(results, list):
        for entry in results:
            if not isinstance(entry, dict):
                continue
            iid = str(entry.get("itemId") or entry.get("id") or "").strip()
            if iid:
                out[iid] = entry
    return out


def _count_prior_blind_wins(rows: list[dict[str, Any]], exclude_session_id: str) -> Counter[str]:
    counts: Counter[str] = Counter()
    for r in rows:
        if str(r.get("sessionId") or "") == exclude_session_id:
            continue
        for it in r.get("items") or []:
            pid = str(it.get("sealedPropId") or "").strip()
            s1 = it.get("step1Result")
            if pid and isinstance(s1, dict) and s1.get("correct"):
                counts[pid] += 1
    return counts


def _build_revealed_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    revealed: list[dict[str, Any]] = []
    for it in items:
        s1 = it.get("step1Result") if isinstance(it.get("step1Result"), dict) else None
        s2 = it.get("step2Result") if isinstance(it.get("step2Result"), dict) else None
        revealed.append(
            {
                "itemId": str(it.get("itemId") or ""),
                "chapter": str(it.get("sealedChapter") or "General"),
                "subject": str(it.get("sealedSubject") or "General"),
                "propId": str(it.get("sealedPropId") or ""),
                "principle": str(it.get("sealedPrinciple") or ""),
                "expectedAnswer": str(it.get("sealedAnswer") or ""),
                "step1Correct": s1.get("correct") if s1 is not None else None,
                "step2Correct": s2.get("correct") if s2 is not None else None,
                "confusedChapter": s1.get("confusedChapter") if s1 is not None else None,
            }
        )
    return revealed


def complete_interleave_session(
    workspace: Path | str | None = None,
    *,
    session_id: str,
    results: dict[str, Any] | list[dict[str, Any]] | None = None,
    kg_dir: Path | str | None = None,
) -> dict[str, Any]:
    """Finalize an interleave session, log calibrated reviews/mistakes, and return confusion matrix."""
    ws, kg = _resolve_kg(workspace, kg_dir)
    sessions_path = kg / INTERLEAVE_SESSIONS_FILENAME
    res_map = _normalize_results_map(results)

    with workspace_lock(sessions_path):
        rows = load_jsonl(sessions_path)
        sess_idx = next((i for i, r in enumerate(rows) if str(r.get("sessionId") or "") == str(session_id)), None)
        if sess_idx is None:
            return {"ok": False, "error": f"Interleave session '{session_id}' not found."}

        session = rows[sess_idx]
        items = session.get("items") or []

        # Idempotent short-circuit: never duplicate FSRS revlog, mistakes.jsonl, or T3 evidence writes
        if str(session.get("status") or "").lower() == "completed":
            summary = session.get("summary") if isinstance(session.get("summary"), dict) else {}
            return {
                "ok": True,
                "alreadyCompleted": True,
                "sessionId": session_id,
                "status": "completed",
                "completedAt": session.get("completedAt"),
                "tier1Correct": summary.get("tier1Correct", 0),
                "tier1Total": summary.get("tier1Total", 0),
                "tier1AccuracyPct": summary.get("tier1AccuracyPct"),
                "step2Correct": summary.get("step2Correct", 0),
                "step2Total": summary.get("step2Total", 0),
                "step2AccuracyPct": summary.get("step2AccuracyPct"),
                "confusionMatrix": summary.get("confusionMatrix") or {},
                "topConfusionPairs": summary.get("topConfusionPairs") or [],
                "revealedItems": _build_revealed_items(items),
                "mistakesLogged": 0,
                "srsUpdated": 0,
                "evidenceRecorded": 0,
            }

        prior_wins = _count_prior_blind_wins(rows, str(session_id))

        tier1_correct = 0
        tier1_total = 0
        step2_correct = 0
        step2_total = 0
        confusion_matrix: dict[str, dict[str, int]] = {}
        revealed_items: list[dict[str, Any]] = []

        for it in items:
            iid = str(it.get("itemId") or "")
            res_in = res_map.get(iid) or {}

            # If caller supplied selectedPrinciple inside results without prior check_principle_step
            if it.get("step1Result") is None and "selectedPrinciple" in res_in:
                choices = it.get("principleChoices") or []
                opt_id, opt_text, is_dk = _match_selected_principle(choices, res_in.get("selectedPrinciple"))
                exp_id = str(it.get("sealedPrincipleId") or "")
                exp_p = str(it.get("sealedPrinciple") or "")
                ok1 = (not is_dk) and (
                    (opt_id is not None and opt_id == exp_id)
                    or (_normalize_text_key(opt_text) == _normalize_text_key(exp_p))
                )
                g_ch = str((it.get("sealedOptionChapters") or {}).get(opt_id or "", "") or "").strip() if not ok1 else ""
                if g_ch == it.get("sealedChapter"):
                    g_ch = ""
                parsed_s1_conf = _parse_confidence(res_in.get("confidence"), default=0.5)
                it["step1Result"] = {
                    "checkedAt": now_iso(),
                    "selectedOptionId": opt_id,
                    "selectedPrinciple": opt_text,
                    "correct": bool(ok1),
                    "dontKnow": bool(is_dk),
                    "confidence": 0.5 if parsed_s1_conf is None else parsed_s1_conf,
                    "durationMs": res_in.get("durationMs"),
                    "confusedChapter": g_ch or None,
                }

            # Record optional Step-2 result
            if res_in and any(k in res_in for k in ("solved", "solved_correctly", "correct", "step2Correct")):
                raw_s2 = res_in.get("solved")
                if raw_s2 is None:
                    raw_s2 = res_in.get("solved_correctly")
                if raw_s2 is None:
                    raw_s2 = res_in.get("step2Correct")
                if raw_s2 is None:
                    raw_s2 = res_in.get("correct")
                s2_ok = bool(raw_s2)
                it["step2Result"] = {
                    "completedAt": now_iso(),
                    "correct": s2_ok,
                    "confidence": _parse_confidence(res_in.get("confidence"), default=None),
                    "durationMs": res_in.get("durationMs"),
                    "response": str(res_in.get("response") or res_in.get("answer") or "").strip()[:240],
                }

            s1 = it.get("step1Result") if isinstance(it.get("step1Result"), dict) else None
            s2 = it.get("step2Result") if isinstance(it.get("step2Result"), dict) else None
            true_ch = str(it.get("sealedChapter") or "General")

            if s1 is not None:
                tier1_total += 1
                if s1.get("correct"):
                    tier1_correct += 1
                    confusion_matrix.setdefault(true_ch, {})[true_ch] = confusion_matrix.get(true_ch, {}).get(true_ch, 0) + 1
                else:
                    confused_ch = str(s1.get("confusedChapter") or ("I don't know" if s1.get("dontKnow") else "Other"))
                    confusion_matrix.setdefault(true_ch, {})[confused_ch] = confusion_matrix.get(true_ch, {}).get(confused_ch, 0) + 1

            if s2 is not None:
                step2_total += 1
                if s2.get("correct"):
                    step2_correct += 1

            revealed_items.append(
                {
                    "itemId": iid,
                    "chapter": true_ch,
                    "subject": str(it.get("sealedSubject") or "General"),
                    "propId": str(it.get("sealedPropId") or ""),
                    "principle": str(it.get("sealedPrinciple") or ""),
                    "expectedAnswer": str(it.get("sealedAnswer") or ""),
                    "step1Correct": s1.get("correct") if s1 is not None else None,
                    "step2Correct": s2.get("correct") if s2 is not None else None,
                    "confusedChapter": s1.get("confusedChapter") if s1 is not None else None,
                }
            )

        tier1_acc = round((tier1_correct / tier1_total) * 100.0, 1) if tier1_total > 0 else None
        step2_acc = round((step2_correct / step2_total) * 100.0, 1) if step2_total > 0 else None

        pair_counts: list[dict[str, Any]] = []
        for true_ch, guesses in confusion_matrix.items():
            for g_ch, cnt in guesses.items():
                if g_ch != true_ch:
                    pair_counts.append({"trueChapter": true_ch, "confusedWithChapter": g_ch, "count": cnt})
        pair_counts.sort(key=lambda x: (-x["count"], x["trueChapter"], x["confusedWithChapter"]))

        session["status"] = "completed"
        session["completedAt"] = now_iso()
        session["summary"] = {
            "tier1Correct": tier1_correct,
            "tier1Total": tier1_total,
            "tier1AccuracyPct": tier1_acc,
            "step2Correct": step2_correct,
            "step2Total": step2_total,
            "step2AccuracyPct": step2_acc,
            "confusionMatrix": confusion_matrix,
            "topConfusionPairs": pair_counts[:5],
        }
        rows[sess_idx] = session
        save_jsonl_atomic(sessions_path, rows)

    # Non-fatal side effects: log mistakes on Step-1 misses, update FSRS reviews & T3 evidence
    mistakes_logged = 0
    srs_updated = 0
    evidence_recorded = 0

    scripts_dir = str(resolve_package_root() / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    try:
        import srs_engine  # type: ignore
    except Exception:
        srs_engine = None
    try:
        from syntopik.evidence import record_evidence
    except Exception:
        record_evidence = None

    today = today_iso()
    for it in items:
        s1 = it.get("step1Result") if isinstance(it.get("step1Result"), dict) else None
        s2 = it.get("step2Result") if isinstance(it.get("step2Result"), dict) else None
        if s1 is None and s2 is None:
            continue
        pid = str(it.get("sealedPropId") or "").strip()
        subj = str(it.get("sealedSubject") or "General")
        chap = str(it.get("sealedChapter") or "General")
        princ = str(it.get("sealedPrinciple") or "")
        raw_conf = (
            s1.get("confidence")
            if isinstance(s1, dict) and s1.get("confidence") is not None
            else (s2.get("confidence") if isinstance(s2, dict) else None)
        )
        conf_val = _parse_confidence(raw_conf, default=None)
        dur = s1.get("durationMs") if isinstance(s1, dict) and s1.get("durationMs") is not None else (
            s2.get("durationMs") if isinstance(s2, dict) else None
        )
        dur_int = (
            int(round(float(dur)))
            if isinstance(dur, (int, float))
            and not isinstance(dur, bool)
            and math.isfinite(float(dur))
            and float(dur) > 0
            else None
        )

        s1_ok = bool(s1.get("correct")) if s1 is not None else True
        s2_ok = bool(s2.get("correct")) if s2 is not None else s1_ok
        overall_ok = s1_ok and s2_ok

        if not s1_ok:
            confused_ch = str(s1.get("confusedChapter") or "").strip() if s1 else ""
            m_entry: dict[str, Any] = {
                "id": f"m-{uuid.uuid4().hex[:8]}",
                "date": today,
                "subject": subj,
                "chapter": chap,
                "propId": pid,
                "category": "condition_missed",
                "triggerCue": _clip(str(it.get("prompt") or princ), 160),
                "wrongMove": f"Blind interleave misidentified principle as '{s1.get('selectedPrinciple', '')}'" if s1 else "Misidentified principle",
                "correctMove": f"Apply '{princ}' ({chap}).",
                "sourceRef": f"interleave:{session_id}:{it.get('itemId')}",
            }
            if confused_ch:
                m_entry["confusedWith"] = confused_ch
            try:
                append_jsonl_locked(kg / "mistakes.jsonl", m_entry)
                mistakes_logged += 1
            except Exception:
                pass

        if pid and srs_engine is not None:
            try:
                rating = "good" if overall_ok else "again"
                srs_engine.record_review(
                    kg,
                    pid,
                    rating,
                    title=princ or pid,
                    subject=subj,
                    chapter=chap,
                    confidence=conf_val,
                    duration_ms=dur_int,
                    no_sync=True,
                )
                srs_updated += 1
            except Exception:
                pass

        if pid and overall_ok and record_evidence is not None:
            if prior_wins[pid] + 1 >= 2:
                try:
                    record_evidence(
                        ws,
                        pid,
                        3,
                        "interleave_blind",
                        detail={"sessionId": session_id, "itemId": it.get("itemId"), "chapter": chap},
                        verified=True,
                    )
                    evidence_recorded += 1
                except Exception:
                    pass

    return {
        "ok": True,
        "sessionId": session_id,
        "status": "completed",
        "completedAt": session["completedAt"],
        "tier1Correct": tier1_correct,
        "tier1Total": tier1_total,
        "tier1AccuracyPct": tier1_acc,
        "step2Correct": step2_correct,
        "step2Total": step2_total,
        "step2AccuracyPct": step2_acc,
        "confusionMatrix": confusion_matrix,
        "topConfusionPairs": pair_counts[:5],
        "revealedItems": revealed_items,
        "mistakesLogged": mistakes_logged,
        "srsUpdated": srs_updated,
        "evidenceRecorded": evidence_recorded,
    }


# ---------------------------------------------------------------------------
# Public API 4: Interleave Stats
# ---------------------------------------------------------------------------


def get_interleave_stats(
    workspace: Path | str | None = None,
    *,
    kg_dir: Path | str | None = None,
) -> dict[str, Any]:
    """Compute aggregate Newman Tier-1 blind-interleave metrics & confusion pairs."""
    _ws, kg = _resolve_kg(workspace, kg_dir)
    rows = load_jsonl(kg / INTERLEAVE_SESSIONS_FILENAME)

    total_sessions = len(rows)
    completed_sessions = sum(1 for r in rows if str(r.get("status") or "").lower() == "completed")
    t1_total = 0
    t1_correct = 0
    s2_total = 0
    s2_correct = 0
    latencies_ms: list[int] = []
    confusion_matrix: dict[str, dict[str, int]] = {}
    chapters_seen: set[str] = set()
    latest_dt: datetime | None = None
    latest_iso: str | None = None

    for r in rows:
        for ts_key in ("completedAt", "createdAt"):
            dt = _parse_iso_dt(r.get(ts_key))
            if dt is not None and (latest_dt is None or dt > latest_dt):
                latest_dt = dt
                latest_iso = str(r.get(ts_key))

        for it in r.get("items") or []:
            true_ch = str(it.get("sealedChapter") or "").strip()
            s1 = it.get("step1Result") if isinstance(it.get("step1Result"), dict) else None
            s2 = it.get("step2Result") if isinstance(it.get("step2Result"), dict) else None
            if s1 is not None:
                t1_total += 1
                if true_ch:
                    chapters_seen.add(true_ch)
                dur = s1.get("durationMs")
                if isinstance(dur, (int, float)) and not isinstance(dur, bool) and dur > 0:
                    latencies_ms.append(int(round(dur)))
                if s1.get("correct"):
                    t1_correct += 1
                    if true_ch:
                        confusion_matrix.setdefault(true_ch, {})[true_ch] = confusion_matrix.get(true_ch, {}).get(true_ch, 0) + 1
                else:
                    g_ch = str(s1.get("confusedChapter") or ("I don't know" if s1.get("dontKnow") else "Other"))
                    if true_ch:
                        confusion_matrix.setdefault(true_ch, {})[g_ch] = confusion_matrix.get(true_ch, {}).get(g_ch, 0) + 1
            if s2 is not None:
                s2_total += 1
                if s2.get("correct"):
                    s2_correct += 1

    t1_acc = round((t1_correct / t1_total) * 100.0, 1) if t1_total > 0 else None
    s2_acc = round((s2_correct / s2_total) * 100.0, 1) if s2_total > 0 else None
    med_ms = int(round(statistics.median(latencies_ms))) if latencies_ms else None
    med_sec = round(med_ms / 1000.0, 1) if med_ms is not None else None
    days_since = (
        max(0, int((datetime.now(timezone.utc) - latest_dt).total_seconds() // 86400))
        if latest_dt is not None
        else None
    )

    top_pairs: list[dict[str, Any]] = []
    for tc, guesses in confusion_matrix.items():
        for gc, cnt in guesses.items():
            if gc != tc:
                top_pairs.append({"trueChapter": tc, "confusedWithChapter": gc, "count": cnt})
    top_pairs.sort(key=lambda x: (-x["count"], x["trueChapter"], x["confusedWithChapter"]))

    return {
        "ok": True,
        "totalSessions": total_sessions,
        "completedSessions": completed_sessions,
        "totalItemsChecked": t1_total,
        "tier1Correct": t1_correct,
        "tier1AccuracyPct": t1_acc,
        "principleAccuracyPct": t1_acc,
        "step2Correct": s2_correct,
        "step2Total": s2_total,
        "step2AccuracyPct": s2_acc,
        "medianIdentifyMs": med_ms,
        "medianIdentifySec": med_sec,
        "lastSessionAt": latest_iso,
        "daysSinceLastInterleave": days_since,
        "distinctChaptersPracticed": len(chapters_seen),
        "confusionMatrix": confusion_matrix,
        "topConfusionPairs": top_pairs[:5],
    }


# ---------------------------------------------------------------------------
# Public API 5: Export Valid Blind-Chapter CBT Spec (`exam_runner.py` ready)
# ---------------------------------------------------------------------------


def export_interleave_cbt_spec(
    workspace: Path | str | None = None,
    *,
    n: int = 10,
    subjects: str | list[str] | None = None,
    kg_dir: Path | str | None = None,
    seed: int | None = None,
    chapters: str | list[str] | None = None,
) -> dict[str, Any]:
    """Export a valid `scripts/exam_runner.py` spec with `blindChapterLabels: True` and decoy autopsies."""
    _ws, kg = _resolve_kg(workspace, kg_dir)
    subj_filter = _normalize_filter_set(subjects, is_subject=True)
    chap_filter = _normalize_filter_set(chapters, is_subject=False)
    rng = random.Random(seed) if seed is not None else random.Random()

    candidates, confusion_map = _build_candidate_pool(
        kg,
        subject_filter=subj_filter,
        chapter_filter=chap_filter,
    )
    selected = _select_zero_blocked_run(candidates, n=max(1, int(n)), confusion_map=confusion_map, rng=rng)

    questions: list[dict[str, Any]] = []
    for idx, cand in enumerate(selected, start=1):
        choices, correct_id, opt_chapter_map = _build_principle_choices(
            cand,
            candidates,
            confusion_map=confusion_map,
            rng=rng,
            include_dont_know=False,
        )
        rich_options: list[dict[str, Any]] = []
        for ch_opt in choices:
            oid = ch_opt["id"]
            opt_row: dict[str, Any] = {"id": oid, "text": ch_opt["text"]}
            if oid != correct_id:
                decoy_ch = opt_chapter_map.get(oid, "")
                opt_row["whyWrong"] = (
                    f"Belongs to {decoy_ch}, not the governing invariant for this problem."
                    if decoy_ch and decoy_ch != "General Decoy"
                    else "Surface decoy principle that does not satisfy this problem's governing conditions."
                )
                opt_row["trapCategory"] = "condition_missed"
                if decoy_ch and decoy_ch != "General Decoy":
                    opt_row["confusedWith"] = decoy_ch
            rich_options.append(opt_row)

        questions.append(
            {
                "id": f"BLIND-{idx}",
                "type": "single_correct",
                "subject": cand["subject"],
                "chapter": cand["chapter"],
                "propId": cand["propId"],
                "stem": f"{cand['blindPrompt']}\n\nWhich governing principle applies?",
                "options": rich_options,
                "answer": correct_id,
                "marksCorrect": 4,
                "marksWrong": -1,
                "solution": (
                    f"Chapter: {cand['chapter']}. Governing principle: {cand['principle']}. "
                    f"{cand['answer']}"
                ).strip(),
                "trapNote": f"Do not sort by surface features; confirm the conditions for {cand['principle']} ({cand['chapter']}).",
                "trapCategory": "condition_missed",
            }
        )

    slug_suffix = slugify("-".join(sorted(subj_filter)), default="mixed", max_len=24) if subj_filter else "mixed"
    return {
        "schemaVersion": 1,
        "examSlug": f"blind-interleave-{slug_suffix}",
        "title": "Blind-Chapter Interleaved Principle Drill",
        "examType": "Blind Interleave",
        "blindChapterLabels": True,
        "durationMinutes": max(10, len(questions) * 2),
        "instructions": [
            "Chapter labels are hidden until submission to train Rohrer cross-chapter principle discrimination.",
            "Single Correct: +4 / -1 (break-even skip threshold p* = 20%).",
            "Identify the governing principle before relying on surface cues.",
        ],
        "questions": questions,
    }
