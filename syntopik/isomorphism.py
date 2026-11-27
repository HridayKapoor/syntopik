"""Syntopik v3.1 — Structural Isomorphism Rosetta Engine.

Pure Python 3.12+ standard library only (zero external dependencies).
Discovers cross-domain structural twins grounded in Gentner Structure-Mapping
and Paynter Bond Graphs, persists idempotent `isomorphic_to` chords into
`chains.jsonl`, surfaces explicit negative-transfer `breaksAt` warnings, and
generates crisp discovery openers (`build_bijection_opener`) when a learner
encounters a new concept whose structural twin is already mastered.
"""

from __future__ import annotations

import copy
import functools
import json
import math
import re
from pathlib import Path
from typing import Any

from syntopik.paths import resolve_kg_dir
from syntopik.schemas import normalize_chain, now_iso
from syntopik.storage import append_jsonl_locked, load_jsonl, save_jsonl_atomic

__all__ = [
    "ISOMORPHIC_RELATION",
    "DEFAULT_ISOMORPHIC_WEIGHT",
    "load_archetypes",
    "match_proposition_archetypes",
    "discover_isomorphisms",
    "link_isomorphism_chord",
    "get_isomorphic_neighbors",
    "build_bijection_opener",
]

ISOMORPHIC_RELATION = "isomorphic_to"
DEFAULT_ISOMORPHIC_WEIGHT = 0.25

_ARCHETYPES_CACHE: list[dict[str, Any]] | None = None
_KW_REGEX_CACHE: dict[str, re.Pattern[str]] = {}


def _archetypes_path() -> Path:
    here = Path(__file__).resolve().parent
    primary = here / "data" / "archetypes.json"
    if primary.is_file():
        return primary
    bundled = here / "_bundle" / "data" / "archetypes.json"
    if bundled.is_file():
        return bundled
    return primary


def load_archetypes() -> list[dict[str, Any]]:
    """Load the curated cross-domain structural archetype library (works from repo or wheel)."""
    global _ARCHETYPES_CACHE
    if _ARCHETYPES_CACHE is None:
        path = _archetypes_path()
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict) and isinstance(raw.get("archetypes"), list):
            items = [a for a in raw["archetypes"] if isinstance(a, dict) and a.get("id")]
        elif isinstance(raw, list):
            items = [a for a in raw if isinstance(a, dict) and a.get("id")]
        else:
            items = []
        _ARCHETYPES_CACHE = items
    return copy.deepcopy(_ARCHETYPES_CACHE)


def _norm_space(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip().lower())


_SYMBOL_RE = re.compile(r"[^a-z0-9\s\-]")
_WS_RE = re.compile(r"\s+")
_KW_SPLIT_RE = re.compile(r"[\s\-]+")


@functools.lru_cache(maxsize=4096)
def _keyword_spec(k: str) -> tuple[bool, str, re.Pattern[str] | None]:
    """Classify a normalized keyword once: (has_symbol, compact form, word-boundary regex)."""
    if _SYMBOL_RE.search(k):
        return True, _WS_RE.sub("", k), None
    pat = _KW_REGEX_CACHE.get(k)
    if pat is None:
        parts = [re.escape(p) for p in _KW_SPLIT_RE.split(k) if p]
        if not parts:
            return False, "", None
        expr = r"(?<![a-z0-9])" + r"[\s\-_]+".join(parts) + r"(?![a-z0-9])"
        pat = re.compile(expr)
        _KW_REGEX_CACHE[k] = pat
    return False, "", pat


@functools.lru_cache(maxsize=256)
def _compact_blob(raw_blob: str) -> str:
    return _WS_RE.sub("", raw_blob)


def _keyword_matches(kw: str, raw_blob: str, word_blob: str) -> bool:
    """Match a keyword deterministically using word boundaries for alphanumeric terms
    and literal substring matching for equation/symbolic tokens."""
    k = str(kw or "").strip().lower()
    if not k:
        return False
    has_symbol, compact_k, pat = _keyword_spec(k)
    if has_symbol:
        if k in raw_blob:
            return True
        return len(compact_k) >= 4 and compact_k in _compact_blob(raw_blob)
    if pat is None:
        return False
    return bool(pat.search(word_blob))


@functools.lru_cache(maxsize=4096)
def _keyword_weight(kw: str) -> float:
    k = str(kw or "").strip().lower()
    has_symbol = bool(_SYMBOL_RE.search(k))
    is_phrase = (" " in k) or ("-" in k)
    if has_symbol or is_phrase or len(k) >= 9:
        return 1.45
    if len(k) >= 5:
        return 1.15
    return 0.95


def _domain_context_alignment(prop: dict[str, Any], slot: dict[str, Any]) -> float:
    subj_chap = _norm_space(f"{prop.get('subject', '')} {prop.get('chapter', '')}")
    slot_meta = _norm_space(f"{slot.get('domain', '')} {slot.get('title', '')}")
    if not subj_chap or not slot_meta:
        return 0.0
    stop = {"and", "the", "for", "with", "from", "general", "system", "systems", "theory", "linear"}
    p_toks = {t for t in re.findall(r"[a-z]{3,}", subj_chap) if t not in stop}
    s_toks = {t for t in re.findall(r"[a-z]{3,}", slot_meta) if t not in stop}
    if not p_toks or not s_toks:
        return 0.0
    overlap = p_toks & s_toks
    return 0.08 if overlap else 0.0


def _prop_text_parts(prop: dict[str, Any]) -> tuple[str, str, str, str, set[str]]:
    title = str(prop.get("title") or "")
    stmt = str(prop.get("statement") or "")
    notes = str(prop.get("notes") or "")
    raw_tags = prop.get("tags") or []
    tags = [str(t).strip().lower() for t in raw_tags if isinstance(t, str) and t.strip()]
    aliases = [str(a).strip() for a in (prop.get("aliases") or []) if isinstance(a, str) and a.strip()]
    cue_parts: list[str] = []
    for c in prop.get("cues") or []:
        if isinstance(c, dict):
            if c.get("q"):
                cue_parts.append(str(c["q"]))
            if c.get("a"):
                cue_parts.append(str(c["a"]))

    headline_raw = _norm_space(" ".join([title, " ".join(aliases), " ".join(tags)]))
    headline_word = re.sub(r"[_/]+", " ", headline_raw)
    full_raw = _norm_space(" ".join([title, stmt, " ".join(aliases), " ".join(tags), notes, " ".join(cue_parts)]))
    full_word = re.sub(r"[_/]+", " ", full_raw)
    return headline_raw, headline_word, full_raw, full_word, set(tags)


def match_proposition_archetypes(
    prop: dict[str, Any],
    archetypes: list[dict[str, Any]] | None = None,
    *,
    min_score: float = 0.35,
) -> list[dict[str, Any]]:
    """Deterministically score a proposition against all structural archetypes.

    Returns matches with `score >= min_score` (default `0.35`), including
    `archetypeId`, `domainSlot`, `variableMap`, and `breaksAt`.
    """
    if not isinstance(prop, dict):
        return []
    lib = archetypes if archetypes is not None else load_archetypes()
    headline_raw, headline_word, full_raw, full_word, tag_set = _prop_text_parts(prop)
    if not full_raw:
        return []

    matches: list[dict[str, Any]] = []
    for arch in lib:
        arch_id = str(arch.get("id") or "").strip()
        if not arch_id:
            continue
        explicit_tag = arch_id.lower() in tag_set
        best_slot: dict[str, Any] | None = None
        best_score = 0.0
        best_kws: list[str] = []

        for slot in arch.get("domains") or []:
            if not isinstance(slot, dict):
                continue
            kws = [str(k) for k in (slot.get("keywords") or []) if str(k).strip()]
            matched_kws: list[str] = []
            weighted_hits = 0.0
            headline_hits = 0

            for kw in kws:
                if _keyword_matches(kw, full_raw, full_word):
                    matched_kws.append(kw)
                    weighted_hits += _keyword_weight(kw)
                    if _keyword_matches(kw, headline_raw, headline_word):
                        headline_hits += 1

            if not matched_kws and not explicit_tag:
                continue

            base = 1.0 - math.exp(-0.42 * weighted_hits) if weighted_hits > 0 else 0.0
            head_boost = min(0.22, 0.11 * headline_hits)
            ctx_boost = _domain_context_alignment(prop, slot)
            score = min(0.99, base + head_boost + ctx_boost)

            # Guard against single weak body-only keyword false positives
            if not explicit_tag and len(matched_kws) == 1 and weighted_hits < 1.35 and headline_hits == 0:
                score = min(score, 0.32)

            if explicit_tag:
                score = max(score, 0.88 + ctx_boost)
                score = min(0.99, score)

            if score > best_score:
                best_score = score
                best_slot = slot
                best_kws = matched_kws

        if best_slot is None and explicit_tag and (arch.get("domains") or []):
            best_slot = arch["domains"][0]
            best_score = 0.88

        if best_slot is not None and best_score >= float(min_score):
            matches.append(
                {
                    "archetypeId": arch_id,
                    "archetypeName": str(arch.get("name") or arch_id),
                    "canonicalEquation": str(arch.get("canonicalEquation") or ""),
                    "invariantStructure": str(arch.get("invariantStructure") or ""),
                    "domainSlot": str(best_slot.get("domain") or "General"),
                    "domainTitle": str(best_slot.get("title") or ""),
                    "variableMap": dict(best_slot.get("variableMap") or {}),
                    "breaksAt": list(arch.get("breaksAt") or []),
                    "bijectionQuestion": str(arch.get("bijectionQuestion") or ""),
                    "matchedKeywords": best_kws[:8],
                    "score": round(best_score, 4),
                }
            )

    matches.sort(key=lambda m: (-m["score"], m["archetypeId"]))
    return matches


def _build_variable_bijection(
    src_map: dict[str, str],
    tgt_map: dict[str, str],
    *,
    arrow: str = "<->",
) -> dict[str, str]:
    out: dict[str, str] = {}
    for role, src_sym in src_map.items():
        tgt_sym = tgt_map.get(role)
        if tgt_sym is not None:
            out[role] = f"{src_sym} {arrow} {tgt_sym}"
    return out


def _is_cross_domain_or_chapter(
    prop_a: dict[str, Any],
    prop_b: dict[str, Any],
    domain_a: str,
    domain_b: str,
) -> bool:
    chap_a = _norm_space(str(prop_a.get("chapter") or ""))
    chap_b = _norm_space(str(prop_b.get("chapter") or ""))
    subj_a = _norm_space(str(prop_a.get("subject") or ""))
    subj_b = _norm_space(str(prop_b.get("subject") or ""))
    dom_a = _norm_space(domain_a)
    dom_b = _norm_space(domain_b)
    if dom_a and dom_b and dom_a != dom_b:
        return True
    if subj_a and subj_b and subj_a != subj_b:
        return True
    if chap_a and chap_b and chap_a != chap_b:
        return True
    return False


def _best_slot_for_prop(prop: dict[str, Any] | None, arch: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(arch, dict):
        return None
    domains = [d for d in (arch.get("domains") or []) if isinstance(d, dict)]
    if not domains:
        return None
    if prop is None:
        return domains[0]
    m = match_proposition_archetypes(prop, [arch], min_score=0.0)
    if m:
        target_dom = m[0]["domainSlot"]
        for d in domains:
            if str(d.get("domain") or "") == target_dom:
                return d
    # Fallback by subject/chapter overlap
    best_d = domains[0]
    best_align = -1.0
    for d in domains:
        al = _domain_context_alignment(prop, d)
        if al > best_align:
            best_align = al
            best_d = d
    return best_d


def _extract_persisted_iso_chords(chains: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    persisted: dict[tuple[str, str], dict[str, Any]] = {}
    for raw in chains:
        if not isinstance(raw, dict):
            continue
        rel = str(raw.get("relation") or "").strip().lower()
        if rel != ISOMORPHIC_RELATION:
            continue
        norm = normalize_chain(raw)
        if not norm:
            continue
        conc = norm["conclusion"]
        for prem in norm["premises"]:
            if not prem or prem == conc:
                continue
            pair_key = (prem, conc) if prem <= conc else (conc, prem)
            raw_breaks = raw.get("breaksAt")
            breaks = [str(b).strip() for b in raw_breaks if str(b).strip()] if isinstance(raw_breaks, list) else []
            persisted[pair_key] = {
                "sourceId": prem,
                "targetId": conc,
                "archetypeId": str(raw.get("archetypeId") or "").strip(),
                "encompassWeight": float(norm["encompassWeight"]),
                "breaksAt": breaks,
                "why": norm["why"],
                "raw": raw,
            }
    return persisted


def _resolve_kg_path(kg_dir: Path | str | None) -> Path:
    if kg_dir is None:
        return resolve_kg_dir()
    return resolve_kg_dir(explicit=kg_dir)


def discover_isomorphisms(
    kg_dir: Path | str | None,
    *,
    min_score: float = 0.35,
) -> dict[str, Any]:
    """Scan `propositions.jsonl` + persisted `isomorphic_to` chords in `chains.jsonl`
    and return cross-chapter / cross-domain structural twins sharing an archetype."""
    resolved_kg = _resolve_kg_path(kg_dir)
    props = load_jsonl(resolved_kg / "propositions.jsonl")
    chains = load_jsonl(resolved_kg / "chains.jsonl")
    archetypes = load_archetypes()
    arch_by_id = {str(a["id"]): a for a in archetypes}

    props_by_id: dict[str, dict[str, Any]] = {}
    active_props: list[dict[str, Any]] = []
    for p in props:
        pid = str(p.get("id") or "").strip()
        if not pid:
            continue
        props_by_id[pid] = p
        if not bool(p.get("archived")):
            active_props.append(p)

    persisted_map = _extract_persisted_iso_chords(chains)

    # Match every active proposition against all archetypes
    by_archetype: dict[str, list[tuple[dict[str, Any], dict[str, Any]]]] = {}
    for p in active_props:
        matches = match_proposition_archetypes(p, archetypes, min_score=min_score)
        for m in matches:
            by_archetype.setdefault(m["archetypeId"], []).append((p, m))

    # Build discovered chords, deduplicating by (pair_key) keeping best archetype unless persisted
    chords_by_pair: dict[tuple[str, str], dict[str, Any]] = {}

    for arch_id, entries in sorted(by_archetype.items()):
        arch = arch_by_id[arch_id]
        entries_sorted = sorted(entries, key=lambda x: str(x[0].get("id") or ""))
        for i in range(len(entries_sorted)):
            p_a, m_a = entries_sorted[i]
            id_a = str(p_a["id"])
            for j in range(i + 1, len(entries_sorted)):
                p_b, m_b = entries_sorted[j]
                id_b = str(p_b["id"])
                if not _is_cross_domain_or_chapter(p_a, p_b, m_a["domainSlot"], m_b["domainSlot"]):
                    continue

                pair_key = (id_a, id_b) if id_a <= id_b else (id_b, id_a)
                src_p, src_m, tgt_p, tgt_m = (p_a, m_a, p_b, m_b) if id_a <= id_b else (p_b, m_b, p_a, m_a)
                pers = persisted_map.get(pair_key)
                is_persisted = bool(pers and (not pers["archetypeId"] or pers["archetypeId"] == arch_id))

                cross_domain_bonus = 0.08 if _norm_space(src_m["domainSlot"]) != _norm_space(tgt_m["domainSlot"]) else 0.0
                pair_score = round(min(0.99, math.sqrt(src_m["score"] * tgt_m["score"]) + cross_domain_bonus), 4)
                breaks_at = (pers["breaksAt"] if (is_persisted and pers and pers["breaksAt"]) else list(arch.get("breaksAt") or []))

                chord = {
                    "id": f"iso-{arch_id}-{src_p['id']}--{tgt_p['id']}",
                    "sourceId": str(src_p["id"]),
                    "targetId": str(tgt_p["id"]),
                    "sourceTitle": str(src_p.get("title") or src_p["id"]),
                    "targetTitle": str(tgt_p.get("title") or tgt_p["id"]),
                    "sourceDomain": src_m["domainSlot"],
                    "targetDomain": tgt_m["domainSlot"],
                    "sourceChapter": str(src_p.get("chapter") or "General"),
                    "targetChapter": str(tgt_p.get("chapter") or "General"),
                    "sourceSubject": str(src_p.get("subject") or "General"),
                    "targetSubject": str(tgt_p.get("subject") or "General"),
                    "archetypeId": arch_id,
                    "archetypeName": str(arch.get("name") or arch_id),
                    "canonicalEquation": str(arch.get("canonicalEquation") or ""),
                    "variableBijection": _build_variable_bijection(src_m["variableMap"], tgt_m["variableMap"]),
                    "breaksAt": breaks_at,
                    "bijectionQuestion": str(arch.get("bijectionQuestion") or ""),
                    "score": pair_score,
                    "persisted": is_persisted,
                }

                prev = chords_by_pair.get(pair_key)
                if prev is None or (chord["persisted"] and not prev["persisted"]) or (
                    chord["persisted"] == prev["persisted"] and chord["score"] > prev["score"]
                ):
                    chords_by_pair[pair_key] = chord

    # Always include persisted `isomorphic_to` chords even if lexical score was below threshold
    for pair_key, pers in sorted(persisted_map.items()):
        if pair_key in chords_by_pair:
            existing = chords_by_pair[pair_key]
            existing["persisted"] = True
            if pers["breaksAt"]:
                existing["breaksAt"] = pers["breaksAt"]
            if pers["archetypeId"] and pers["archetypeId"] != existing["archetypeId"] and pers["archetypeId"] in arch_by_id:
                arch = arch_by_id[pers["archetypeId"]]
                src_p = props_by_id.get(pair_key[0])
                tgt_p = props_by_id.get(pair_key[1])
                slot_a = _best_slot_for_prop(src_p, arch) or {}
                slot_b = _best_slot_for_prop(tgt_p, arch) or {}
                existing["archetypeId"] = pers["archetypeId"]
                existing["archetypeName"] = str(arch.get("name") or pers["archetypeId"])
                existing["canonicalEquation"] = str(arch.get("canonicalEquation") or "")
                existing["sourceDomain"] = str(slot_a.get("domain") or existing["sourceDomain"])
                existing["targetDomain"] = str(slot_b.get("domain") or existing["targetDomain"])
                existing["variableBijection"] = _build_variable_bijection(
                    dict(slot_a.get("variableMap") or {}),
                    dict(slot_b.get("variableMap") or {}),
                )
                existing["bijectionQuestion"] = str(arch.get("bijectionQuestion") or "")
                if not pers["breaksAt"]:
                    existing["breaksAt"] = list(arch.get("breaksAt") or [])
            continue

        src_id, tgt_id = pair_key
        src_p = props_by_id.get(src_id) or {"id": src_id, "title": src_id, "subject": "General", "chapter": "General"}
        tgt_p = props_by_id.get(tgt_id) or {"id": tgt_id, "title": tgt_id, "subject": "General", "chapter": "General"}
        arch_id = pers["archetypeId"] or "custom_isomorphism"
        arch = arch_by_id.get(arch_id)
        slot_a = _best_slot_for_prop(src_p, arch) or {}
        slot_b = _best_slot_for_prop(tgt_p, arch) or {}
        breaks_at = pers["breaksAt"] or (list(arch.get("breaksAt") or []) if arch else [])
        chords_by_pair[pair_key] = {
            "id": f"iso-{arch_id}-{src_id}--{tgt_id}",
            "sourceId": src_id,
            "targetId": tgt_id,
            "sourceTitle": str(src_p.get("title") or src_id),
            "targetTitle": str(tgt_p.get("title") or tgt_id),
            "sourceDomain": str(slot_a.get("domain") or src_p.get("subject") or "General"),
            "targetDomain": str(slot_b.get("domain") or tgt_p.get("subject") or "General"),
            "sourceChapter": str(src_p.get("chapter") or "General"),
            "targetChapter": str(tgt_p.get("chapter") or "General"),
            "sourceSubject": str(src_p.get("subject") or "General"),
            "targetSubject": str(tgt_p.get("subject") or "General"),
            "archetypeId": arch_id,
            "archetypeName": str(arch.get("name")) if arch else arch_id.replace("_", " ").title(),
            "canonicalEquation": str(arch.get("canonicalEquation") or "") if arch else "",
            "variableBijection": _build_variable_bijection(
                dict(slot_a.get("variableMap") or {}),
                dict(slot_b.get("variableMap") or {}),
            ),
            "breaksAt": breaks_at,
            "bijectionQuestion": str(arch.get("bijectionQuestion") or "") if arch else (
                "Map the core invariant roles across both domains and state where the analogy breaks."
            ),
            "score": 1.0,
            "persisted": True,
        }

    chords = sorted(
        chords_by_pair.values(),
        key=lambda c: (0 if c["persisted"] else 1, -c["score"], c["archetypeId"], c["sourceId"], c["targetId"]),
    )

    # Summarize matched archetypes
    chord_counts: dict[str, int] = {}
    chord_props: dict[str, set[str]] = {}
    chord_domains: dict[str, set[str]] = {}
    for ch in chords:
        aid = ch["archetypeId"]
        chord_counts[aid] = chord_counts.get(aid, 0) + 1
        chord_props.setdefault(aid, set()).update([ch["sourceId"], ch["targetId"]])
        chord_domains.setdefault(aid, set()).update([ch["sourceDomain"], ch["targetDomain"]])

    matched_arch_ids = sorted(set(by_archetype.keys()) | set(chord_counts.keys()))
    archetypes_matched: list[dict[str, Any]] = []
    for aid in matched_arch_ids:
        arch = arch_by_id.get(aid)
        entries = by_archetype.get(aid, [])
        pids = sorted({str(p["id"]) for p, _ in entries} | chord_props.get(aid, set()))
        doms = sorted({m["domainSlot"] for _, m in entries} | chord_domains.get(aid, set()))
        archetypes_matched.append(
            {
                "archetypeId": aid,
                "archetypeName": str(arch.get("name")) if arch else aid.replace("_", " ").title(),
                "canonicalEquation": str(arch.get("canonicalEquation") or "") if arch else "",
                "propositionCount": len(pids),
                "chordCount": chord_counts.get(aid, 0),
                "propositionIds": pids,
                "domainsCovered": doms,
                "breaksAt": list(arch.get("breaksAt") or []) if arch else [],
            }
        )
    archetypes_matched.sort(key=lambda a: (-a["chordCount"], -a["propositionCount"], a["archetypeId"]))

    return {
        "ok": True,
        "kgDir": str(resolved_kg),
        "chords": chords,
        "archetypesMatched": archetypes_matched,
        "chordCount": len(chords),
        "persistedCount": sum(1 for c in chords if c["persisted"]),
        "discoveredCount": sum(1 for c in chords if not c["persisted"]),
    }


def link_isomorphism_chord(
    kg_dir: Path | str | None,
    source_id: str,
    target_id: str,
    archetype_id: str,
    *,
    breaks_at: list[str] | str | None = None,
    encompass_weight: float = DEFAULT_ISOMORPHIC_WEIGHT,
) -> dict[str, Any]:
    """Persist an idempotent `isomorphic_to` chord into `chains.jsonl`."""
    src = str(source_id or "").strip()
    tgt = str(target_id or "").strip()
    aid = str(archetype_id or "").strip()
    if not src or not tgt:
        raise ValueError("source_id and target_id are required.")
    if src == tgt:
        raise ValueError("source_id and target_id must be distinct.")
    if not aid:
        raise ValueError("archetype_id is required.")

    resolved_kg = _resolve_kg_path(kg_dir)
    resolved_kg.mkdir(parents=True, exist_ok=True)
    chains_path = resolved_kg / "chains.jsonl"
    existing_rows = load_jsonl(chains_path)

    arch_by_id = {str(a["id"]): a for a in load_archetypes()}
    arch = arch_by_id.get(aid)

    if isinstance(breaks_at, str):
        clean_breaks = [breaks_at.strip()] if breaks_at.strip() else []
    elif isinstance(breaks_at, list):
        clean_breaks = [str(b).strip() for b in breaks_at if str(b).strip()]
    else:
        clean_breaks = list(arch.get("breaksAt") or []) if arch else []

    weight = round(max(0.05, min(0.30, float(encompass_weight))), 4)
    pair_set = {src, tgt}

    # Check idempotency across either orientation
    for idx, row in enumerate(existing_rows):
        if str(row.get("relation") or "").strip().lower() != ISOMORPHIC_RELATION:
            continue
        norm = normalize_chain(row)
        if not norm:
            continue
        if len(norm["premises"]) == 1 and {norm["premises"][0], norm["conclusion"]} == pair_set:
            existing_aid = str(row.get("archetypeId") or "").strip()
            if not existing_aid or existing_aid == aid:
                updated = False
                if breaks_at is not None and clean_breaks != list(row.get("breaksAt") or []):
                    row["breaksAt"] = clean_breaks
                    updated = True
                if not existing_aid:
                    row["archetypeId"] = aid
                    updated = True
                if updated:
                    existing_rows[idx] = row
                    save_jsonl_atomic(chains_path, existing_rows)
                return {
                    "ok": True,
                    "created": False,
                    "alreadyExists": True,
                    "updated": updated,
                    "chainsPath": str(chains_path),
                    "chord": row,
                }

    why_text = (
        f"Structural isomorphism via {arch['name']} ({arch['canonicalEquation']})"
        if arch
        else f"Structural isomorphism via {aid}"
    )
    new_row: dict[str, Any] = {
        "from": src,
        "to": tgt,
        "premises": [src],
        "conclusion": tgt,
        "relation": ISOMORPHIC_RELATION,
        "encompassWeight": weight,
        "archetypeId": aid,
        "breaksAt": clean_breaks,
        "why": why_text,
        "createdAt": now_iso(),
    }
    append_jsonl_locked(chains_path, new_row)
    return {
        "ok": True,
        "created": True,
        "alreadyExists": False,
        "updated": False,
        "chainsPath": str(chains_path),
        "chord": new_row,
    }


def get_isomorphic_neighbors(
    kg_dir: Path | str | None,
    prop_id: str,
    *,
    min_score: float = 0.35,
) -> list[dict[str, Any]]:
    """Return persisted `isomorphic_to` chords AND high-confidence discovered structural twins for `prop_id`."""
    target = str(prop_id or "").strip()
    if not target:
        return []
    resolved_kg = _resolve_kg_path(kg_dir)
    props = load_jsonl(resolved_kg / "propositions.jsonl")
    props_by_id = {str(p.get("id") or ""): p for p in props if isinstance(p, dict) and p.get("id")}
    disc = discover_isomorphisms(resolved_kg, min_score=min_score)

    neighbors: list[dict[str, Any]] = []
    for ch in disc.get("chords") or []:
        src_id = str(ch["sourceId"])
        tgt_id = str(ch["targetId"])
        if target not in (src_id, tgt_id):
            continue
        is_src = target == src_id
        other_id = tgt_id if is_src else src_id
        other_p = props_by_id.get(other_id) or {}
        bkt = other_p.get("bktMastery")
        bkt_val = float(bkt) if isinstance(bkt, (int, float)) else None
        status = str(other_p.get("status") or "planned").strip().lower()

        raw_bij = ch.get("variableBijection") or {}
        oriented_bij: dict[str, str] = {}
        for role, expr in raw_bij.items():
            if "<->" in expr:
                left, right = [s.strip() for s in expr.split("<->", 1)]
                oriented_bij[role] = f"{left} -> {right}" if is_src else f"{right} -> {left}"
            else:
                oriented_bij[role] = str(expr)

        neighbors.append(
            {
                "neighborId": other_id,
                "neighborTitle": ch["targetTitle"] if is_src else ch["sourceTitle"],
                "neighborSubject": ch["targetSubject"] if is_src else ch["sourceSubject"],
                "neighborChapter": ch["targetChapter"] if is_src else ch["sourceChapter"],
                "neighborStatus": status,
                "neighborBktMastery": bkt_val,
                "neighborDomain": ch["targetDomain"] if is_src else ch["sourceDomain"],
                "targetDomain": ch["sourceDomain"] if is_src else ch["targetDomain"],
                "archetypeId": ch["archetypeId"],
                "archetypeName": ch["archetypeName"],
                "canonicalEquation": ch["canonicalEquation"],
                "variableBijection": oriented_bij,
                "breaksAt": list(ch.get("breaksAt") or []),
                "bijectionQuestion": ch["bijectionQuestion"],
                "score": float(ch["score"]),
                "persisted": bool(ch["persisted"]),
            }
        )

    neighbors.sort(
        key=lambda n: (
            0 if n["persisted"] else 1,
            0 if (n["neighborStatus"] == "solid" or (n["neighborBktMastery"] is not None and n["neighborBktMastery"] >= 0.70)) else 1,
            -n["score"],
            n["neighborId"],
        )
    )
    return neighbors


def build_bijection_opener(
    kg_dir: Path | str | None,
    target_prop_id: str,
    *,
    min_score: float = 0.35,
) -> dict[str, Any] | None:
    """If `target_prop_id` has a `solid` (or `bktMastery >= 0.70`) structural twin in another
    chapter/subject, return a crisp Rosetta discovery opener for the AI teacher."""
    target = str(target_prop_id or "").strip()
    if not target:
        return None
    resolved_kg = _resolve_kg_path(kg_dir)
    props = load_jsonl(resolved_kg / "propositions.jsonl")
    props_by_id = {str(p.get("id") or ""): p for p in props if isinstance(p, dict) and p.get("id")}
    target_p = props_by_id.get(target)
    if target_p is None:
        return None

    target_chap = _norm_space(str(target_p.get("chapter") or ""))
    target_subj = _norm_space(str(target_p.get("subject") or ""))
    neighbors = get_isomorphic_neighbors(resolved_kg, target, min_score=min_score)

    candidates: list[tuple[tuple[int, int, float, float, str], dict[str, Any]]] = []
    for nbr in neighbors:
        nid = nbr["neighborId"]
        np = props_by_id.get(nid) or {}
        if bool(np.get("archived")):
            continue
        status = nbr["neighborStatus"]
        bkt = nbr["neighborBktMastery"]
        if status == "misconception":
            continue
        is_mastered = (status == "solid") or (bkt is not None and bkt >= 0.70)
        if not is_mastered:
            continue

        n_chap = _norm_space(nbr["neighborChapter"])
        n_subj = _norm_space(nbr["neighborSubject"])
        n_dom = _norm_space(nbr["neighborDomain"])
        t_dom = _norm_space(nbr["targetDomain"])
        is_other_scope = (n_chap != target_chap) or (n_subj != target_subj) or (bool(n_dom and t_dom and n_dom != t_dom))
        if not is_other_scope:
            continue

        rank_key = (
            0 if nbr["persisted"] else 1,
            0 if n_subj != target_subj else 1,
            -float(nbr["score"]),
            -(float(bkt) if bkt is not None else (0.85 if status == "solid" else 0.0)),
            nid,
        )
        candidates.append((rank_key, nbr))

    if not candidates:
        return None

    candidates.sort(key=lambda item: item[0])
    best = candidates[0][1]

    # Build known -> target bijection direction
    known_to_target: dict[str, str] = {}
    for role, expr in (best.get("variableBijection") or {}).items():
        if "->" in expr:
            tgt_sym, known_sym = [s.strip() for s in expr.split("->", 1)]
            known_to_target[role] = f"{known_sym} -> {tgt_sym}"
        else:
            known_to_target[role] = str(expr)

    first_role_hint = ""
    for role, mapping in known_to_target.items():
        known_sym_raw = mapping.split("->", 1)[0].strip()
        known_sym = known_sym_raw.split("(", 1)[0].strip() or known_sym_raw
        first_role_hint = f"{known_sym} ({role})"
        if role not in ("state",):
            break

    known_title = str(best["neighborTitle"])
    target_title = str(target_p.get("title") or target)
    arch_name = str(best["archetypeName"])

    if first_role_hint:
        opener_prompt = (
            f"You mastered {known_title} — predict what replaces {first_role_hint} "
            f"in {target_title}, and where the analogy breaks."
        )
    else:
        opener_prompt = (
            f"You mastered {known_title} — predict the matching roles "
            f"in {target_title} ({arch_name}), and where the analogy breaks."
        )

    return {
        "knownPropId": best["neighborId"],
        "knownTitle": known_title,
        "knownChapter": best["neighborChapter"],
        "knownSubject": best["neighborSubject"],
        "targetPropId": target,
        "targetTitle": target_title,
        "archetypeId": best["archetypeId"],
        "archetypeName": arch_name,
        "canonicalEquation": best["canonicalEquation"],
        "variableBijection": known_to_target,
        "breaksAt": list(best.get("breaksAt") or []),
        "openerPrompt": opener_prompt,
    }
