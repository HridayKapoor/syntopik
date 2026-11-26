#!/usr/bin/env python3
"""Deterministic FSRS v6 Spaced Repetition, True FIRe, SRC Knockouts,
Backlog Recovery, Calibration/Optimizer, BKT Mastery & Anki (.apkg) Exporter.

Reads and updates knowledge-graph/propositions.jsonl, chains.jsonl, and
revlog.jsonl; regenerates knowledge-graph/review-queue.md; syncs viewer.html /
dashboard.html; and exports MathJax/Cloze/Image-Occlusion Anki decks (.apkg).

Runs 100% cleanly on bare Python 3.12+ (stdlib fallback when `fsrs` is absent;
`genanki` lazy-imported inside `export-anki`; `scipy` lazy-imported inside `optimize`).
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import html
import json
import math
import random
import re
import shutil
import sys
import tempfile
import unicodedata
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from syntopik.fsrs_core import (  # noqa: E402
    RATING_TO_INT,
    RATING_TRANSIT,
    apply_fire_credit,
    apply_sibling_burying,
    assess_confidence_signal,
    bkt_update,
    card_retrievability,
    compress_due_queue,
    compute_calibration,
    detect_leeches,
    forecast_retention,
    load_fsrs_parameters,
    optimize_fsrs_params,
    recover_backlog,
    review_card_dict,
)
from syntopik.paths import (  # noqa: E402
    find_node as _find_node,
    resolve_kg_dir as _resolve_kg_dir_core,
    resolve_package_root,
    sync_hub,
)
from syntopik.schemas import normalize_chain, now_iso, today_iso  # noqa: E402
from syntopik.storage import (  # noqa: E402
    append_jsonl_locked,
    load_jsonl,
    save_json_atomic,
    save_jsonl_atomic,
    write_text_atomic,
)

DEFAULT_KG_DIR = ROOT / "knowledge-graph"
SYNC_SCRIPT = resolve_package_root() / "scripts" / "sync-graph.mjs"

RATINGS: dict[str, int] = dict(RATING_TO_INT)

# LearnHarness multi-cue weighted selection scheme (proposition shares 1 FSRS-6 schedule).
CUE_WEIGHTS: dict[str | None, int] = {
    "again": 4,
    "hard": 3,
    None: 3,
    "good": 2,
    "easy": 1,
}

VALID_CUE_KINDS: tuple[str, ...] = (
    "recall",
    "application",
    "procedural",
    "salience",
    "cloze",
    "occlusion",
)

CLOZE_PATTERN = re.compile(r"\{\{c\d+::.+?\}\}", flags=re.DOTALL)

# Common English function words excluded from lexical similarity (keeps negation & STEM tokens).
LEXICAL_STOP_WORDS = frozenset(
    (
        "a an the and or of to in on at by for from with as is are was were be been being "
        "it its this that these those which who whose their them they we our you your "
        "can could may might must will would should has have had does do did "
        "such than then when where each every any all also into through"
    ).split()
)


def resolve_kg_paths(kg_dir_arg: str | Path | None) -> tuple[Path, Path, Path, Path, bool]:
    kg_dir = _resolve_kg_dir_core(explicit=kg_dir_arg)
    is_default = kg_dir == DEFAULT_KG_DIR.resolve()
    return (
        kg_dir,
        kg_dir / "propositions.jsonl",
        kg_dir / "chains.jsonl",
        kg_dir / "review-queue.md",
        is_default,
    )


def save_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    save_jsonl_atomic(path, rows)


def sync_viewer(kg_dir: Path | str | None = None) -> None:
    sync_hub(kg_dir=kg_dir, quiet=True)


def _normalize_confidence(raw: float | None) -> float | None:
    if raw is None:
        return None
    val = float(raw)
    if not math.isfinite(val):
        return None
    if 1.0 < val <= 100.0:
        val = val / 100.0
    return max(0.0, min(1.0, val))


def regenerate_review_queue(
    props: list[dict[str, Any]],
    queue_file: Path,
    *,
    kg_dir: Path | None = None,
    chains: list[dict[str, Any]] | None = None,
    revlog_rows: list[dict[str, Any]] | None = None,
    fire_summary: str | None = None,
) -> None:
    today = today_iso()
    now = datetime.now(timezone.utc)
    params = load_fsrs_parameters(kg_dir or queue_file.parent)

    due_weak: list[tuple[float, dict[str, Any]]] = []
    upcoming: list[tuple[str, dict[str, Any]]] = []
    misconceptions: list[dict[str, Any]] = []

    for p in props:
        status = p.get("status", "planned")
        retr = 0.0
        if isinstance(p.get("fsrs"), dict):
            try:
                retr = float(card_retrievability(p["fsrs"], now, parameters=params))
            except Exception:
                retr = 0.5
        if status == "misconception":
            misconceptions.append(p)
            due_weak.append((0.0, p))
        elif status == "shaky":
            due_weak.append((min(retr, 0.4), p))
        elif isinstance(p.get("nextReview"), str) and p["nextReview"] <= today:
            due_weak.append((retr, p))
        elif status == "solid":
            upcoming.append((str(p.get("nextReview", "9999-99-99")), p))

    due_weak.sort(key=lambda item: item[0])
    upcoming.sort(key=lambda item: item[0])

    leeches = detect_leeches(props, revlog_rows)
    compression_note = ""
    if chains and due_weak:
        due_dicts = [
            {"id": p["id"], "title": p.get("title", p["id"]), "status": p.get("status", "planned"), "retrievability": r}
            for r, p in due_weak
            if p.get("id")
        ]
        comp = compress_due_queue(due_dicts, chains)
        if comp["knockedOutCount"] > 0:
            compression_note = f" · SRC: {comp['summary']}"

    lines = [
        "# Spaced Review & Weak-Edge Queue",
        "",
        f"> Automatically maintained via FSRS v6 + FIRe (`scripts/srs_engine.py`) · Updated `{today}`{compression_note}.",
        '> Ask Syntopik: *"Check my review-queue.md and run a 10-minute interleaved tune-up."*',
    ]
    if fire_summary:
        lines.append(f"> {fire_summary}")
    lines.extend([
        "",
        "## Due / Weak Edges (`misconception`, `shaky`, or Due Today)",
    ])
    if due_weak:
        for retr, p in due_weak[:30]:
            note = f" — {p['notes']}" if p.get("notes") else ""
            lines.append(
                f"- **`{p['id']}`** ({p.get('subject', '?')} · {p.get('chapter', '?')} · `{p.get('status')}` · next: `{p.get('nextReview', 'now')}` · R≈{round(retr * 100)}%): {p.get('title', '')}{note}"
            )
    else:
        lines.append("_Nothing due today — keep building new chapters!_")

    lines.append("")
    lines.append("## Upcoming Spaced Recall (`solid`)")
    if upcoming:
        for due_date, p in upcoming[:30]:
            lines.append(
                f"- `{due_date}` · **`{p['id']}`** ({p.get('chapter', p.get('subject', ''))}): {p.get('title', '')}"
            )
    else:
        lines.append("_None scheduled yet._")

    lines.append("")
    lines.append("## Active Misconception Watchlist")
    if misconceptions:
        for p in misconceptions:
            lines.append(f"- **`{p['id']}`**: {p.get('notes') or p.get('title', '')}")
    else:
        lines.append("_None active._")

    if leeches:
        lines.append("")
        lines.append("## Leech Watchlist (Refactor / Prerequisite Check / Teach-Back)")
        for lc in leeches[:15]:
            lines.append(
                f"- **`{lc['id']}`** ({', '.join(lc['reasons'])}): {lc['title']} — {lc['recommendation']}"
            )

    lines.append("")
    write_text_atomic(queue_file, "\n".join(lines))


def md_math_to_anki_mathjax(text: str) -> str:
    """HTML-escape text and convert Markdown $$...$$ and $...$ math into Anki MathJax \\[...\\] / \\(...\\)."""
    escaped = html.escape(str(text))
    escaped = re.sub(r"\$\$(.+?)\$\$", lambda m: r"\[" + m.group(1) + r"\]", escaped, flags=re.DOTALL)
    escaped = re.sub(r"(?<!\$)\$(?!\$)(.+?)(?<!\$)\$(?!\$)", lambda m: r"\(" + m.group(1) + r"\)", escaped)
    return escaped


def _sanitize_tag(tag: str) -> str:
    return re.sub(r"\s+", "_", str(tag).strip())


def _stem_token(tok: str) -> str:
    """Lightweight deterministic suffix stemmer for STEM English tokens (stdlib-only)."""
    if len(tok) <= 3 or not tok.isascii() or not tok.isalpha():
        return tok
    if tok.endswith("ies") and len(tok) > 4:
        return tok[:-3] + "y"
    for suffix, repl, min_stem in (
        ("ational", "at", 3),
        ("ization", "iz", 3),
        ("ation", "at", 3),
        ("ition", "it", 3),
        ("sion", "s", 3),
        ("tion", "t", 3),
        ("ment", "", 3),
        ("ness", "", 3),
        ("able", "", 3),
        ("ible", "", 3),
        ("ing", "", 3),
        ("ied", "y", 3),
        ("ed", "", 3),
        ("es", "", 3),
        ("ly", "", 3),
        ("al", "", 4),
        ("ic", "", 4),
        ("s", "", 3),
    ):
        if tok.endswith(suffix):
            stem = tok[: -len(suffix)] + repl
            if len(stem) >= min_stem:
                return stem
    return tok


def _tokenize_lexical(text: str) -> list[str]:
    """Normalize text and extract lowercase searchable tokens (preserving STEM terms & Greek)."""
    norm = unicodedata.normalize("NFKC", str(text or "")).lower()
    norm = re.sub(r"\\(?:text|mathrm|mathbf|vec|hat|bar|frac|sqrt|left|right)\b", " ", norm)
    raw = re.findall(r"[^\W_]+", norm, flags=re.UNICODE)
    tokens: list[str] = []
    for tok in raw:
        if tok in LEXICAL_STOP_WORDS:
            continue
        if len(tok) == 1 and tok.isascii() and tok.isalpha():
            continue
        tokens.append(tok)
    if not tokens and raw:
        tokens = [t for t in raw if t not in LEXICAL_STOP_WORDS] or raw
    return tokens


def _word_bigrams(tokens: list[str]) -> set[tuple[str, str]]:
    return {(tokens[i], tokens[i + 1]) for i in range(len(tokens) - 1)}


def _token_char_ngrams(tokens: list[str], n: int = 3) -> set[str]:
    grams: set[str] = set()
    for tok in tokens:
        if len(tok) < n:
            if len(tok) >= 2:
                grams.add(tok)
        else:
            for i in range(len(tok) - n + 1):
                grams.add(tok[i : i + n])
    return grams


def _char_dice(a: str, b: str) -> float:
    if a == b:
        return 1.0
    n = 2 if min(len(a), len(b)) <= 4 else 3
    ga = {a[i : i + n] for i in range(max(1, len(a) - n + 1))} if len(a) >= n else {a}
    gb = {b[i : i + n] for i in range(max(1, len(b) - n + 1))} if len(b) >= n else {b}
    inter = len(ga & gb)
    return (2.0 * inter / (len(ga) + len(gb))) if (ga and gb) else 0.0


def _score_view(
    q_tokens: list[str],
    q_stems: list[str],
    q_stem_set: set[str],
    q_bigrams: set[tuple[str, str]],
    q_char_grams: set[str],
    cand_tokens: list[str],
    idf_map: dict[str, float],
    default_idf: float,
) -> tuple[float, set[str]]:
    if not q_tokens or not cand_tokens:
        return 0.0, set()

    cand_stems = [_stem_token(t) for t in cand_tokens]
    cand_stem_set = set(cand_stems)
    cand_tok_set = set(cand_tokens)

    matched_q_stems: set[str] = set()
    matched_tokens: set[str] = set()
    fuzzy_weight = 0.0
    total_q_idf = sum(idf_map.get(s, default_idf) for s in q_stem_set) or 1.0

    for qt, qs in zip(q_tokens, q_stems):
        if qt in cand_tok_set or qs in cand_stem_set:
            matched_q_stems.add(qs)
            matched_tokens.add(qt)
        elif len(qt) >= 4:
            best_sim = 0.0
            for ct, cs in zip(cand_tokens, cand_stems):
                if len(ct) < 4:
                    continue
                sim = max(_char_dice(qt, ct), _char_dice(qs, cs))
                if sim > best_sim:
                    best_sim = sim
            if best_sim >= 0.58:
                fuzzy_weight += idf_map.get(qs, default_idf) * best_sim * 0.85
                matched_tokens.add(qt)

    exact_idf = sum(idf_map.get(s, default_idf) for s in matched_q_stems)
    query_cov = min(1.0, (exact_idf + fuzzy_weight) / total_q_idf)

    if not matched_tokens:
        return 0.0, set()

    dice_tok = (2.0 * len(q_stem_set & cand_stem_set)) / (len(q_stem_set) + len(cand_stem_set))

    cand_bigrams = _word_bigrams(cand_stems)
    if q_bigrams and cand_bigrams:
        bg_inter = len(q_bigrams & cand_bigrams)
        bg_score = 0.5 * (bg_inter / len(q_bigrams)) + 0.5 * (2.0 * bg_inter / (len(q_bigrams) + len(cand_bigrams)))
    else:
        bg_score = 0.0

    cand_char_grams = _token_char_ngrams(cand_stems)
    if q_char_grams and cand_char_grams:
        cg_inter = len(q_char_grams & cand_char_grams)
        char_score = 0.55 * (cg_inter / len(q_char_grams)) + 0.45 * (2.0 * cg_inter / (len(q_char_grams) + len(cand_char_grams)))
    else:
        char_score = 0.0

    if q_bigrams:
        score = 0.48 * query_cov + 0.24 * dice_tok + 0.14 * bg_score + 0.14 * char_score
    else:
        score = 0.58 * query_cov + 0.24 * dice_tok + 0.18 * char_score
    return min(1.0, score), matched_tokens


def find_similar_propositions(
    props: list[dict[str, Any]],
    query: str,
    top: int = 5,
    threshold: float = 0.25,
) -> list[dict[str, Any]]:
    """Fast lexical token + bigram + stem similarity search over propositions (zero LLM calls)."""
    q_raw = str(query or "").strip()
    if not q_raw or not props:
        return []

    q_norm_exact = re.sub(r"\s+", " ", unicodedata.normalize("NFKC", q_raw).lower()).strip()
    q_tokens = _tokenize_lexical(q_raw)
    if not q_tokens:
        return []
    q_stems = [_stem_token(t) for t in q_tokens]
    q_stem_set = set(q_stems)
    q_bigrams = _word_bigrams(q_stems)
    q_char_grams = _token_char_ngrams(q_stems)

    prepared: list[tuple[dict[str, Any], list[list[str]], list[str]]] = []
    doc_freq: dict[str, int] = {}
    for p in props:
        pid_text = str(p.get("id") or "").replace("/", " ").replace("-", " ").replace("_", " ")
        title_text = str(p.get("title") or "")
        aliases = [str(a) for a in (p.get("aliases") or []) if isinstance(a, str) and a.strip()]
        chapter_text = str(p.get("chapter") or "")
        subject_text = str(p.get("subject") or "")
        notes_text = str(p.get("notes") or "")

        primary_views = [_tokenize_lexical(f"{pid_text} {title_text}")]
        for alias in aliases:
            primary_views.append(_tokenize_lexical(f"{pid_text} {alias}"))
        full_tokens = _tokenize_lexical(f"{pid_text} {title_text} {' '.join(aliases)} {chapter_text} {subject_text} {notes_text}")

        node_stems = {_stem_token(t) for t in full_tokens}
        for s in node_stems:
            doc_freq[s] = doc_freq.get(s, 0) + 1
        prepared.append((p, primary_views, full_tokens))

    n_docs = len(props)
    idf_map = {s: math.log(1.0 + (n_docs - df + 0.5) / (df + 0.5)) for s, df in doc_freq.items()}
    default_idf = math.log(1.0 + (n_docs + 0.5) / 0.5)

    matches: list[dict[str, Any]] = []
    for p, primary_views, full_tokens in prepared:
        pid_norm = str(p.get("id") or "").strip().lower()
        title_norm = re.sub(r"\s+", " ", unicodedata.normalize("NFKC", str(p.get("title") or "")).lower()).strip()
        aliases_norm = [
            re.sub(r"\s+", " ", unicodedata.normalize("NFKC", a).lower()).strip()
            for a in (p.get("aliases") or [])
            if isinstance(a, str) and a.strip()
        ]

        best_score = 0.0
        best_matched: set[str] = set()
        if q_norm_exact and (q_norm_exact == pid_norm or q_norm_exact == title_norm or q_norm_exact in aliases_norm):
            best_score = 1.0
            best_matched = set(q_tokens)
        else:
            for view_tokens in primary_views:
                sc, mt = _score_view(
                    q_tokens, q_stems, q_stem_set, q_bigrams, q_char_grams,
                    view_tokens, idf_map, default_idf,
                )
                if sc > best_score:
                    best_score = sc
                    best_matched = mt
            full_sc, full_mt = _score_view(
                q_tokens, q_stems, q_stem_set, q_bigrams, q_char_grams,
                full_tokens, idf_map, default_idf,
            )
            scaled_full = full_sc * 0.78
            if scaled_full > best_score:
                best_score = scaled_full
                best_matched = full_mt
            elif full_mt:
                best_matched = best_matched | full_mt

        rounded = round(min(1.0, best_score), 4)
        if rounded > 0.0 and rounded >= threshold:
            matches.append({
                "id": p.get("id", ""),
                "title": p.get("title", ""),
                "subject": p.get("subject", ""),
                "chapter": p.get("chapter", ""),
                "status": p.get("status", "planned"),
                "score": rounded,
                "matchedTokens": sorted(best_matched),
            })

    matches.sort(key=lambda item: (-item["score"], str(item["id"])))
    return matches[: max(0, int(top))]


def cue_weight(last_rating: str | None) -> int:
    key = str(last_rating).strip().lower() if isinstance(last_rating, str) and last_rating.strip() else None
    return CUE_WEIGHTS.get(key, 3)


def synthesize_default_cue(prop: dict[str, Any]) -> dict[str, Any]:
    pid = str(prop.get("id") or "")
    title = str(prop.get("title") or pid)
    q = str(prop.get("prompt") or f"State and justify: {title}")
    notes = str(prop.get("notes") or "").strip()
    a = f"{title} — {notes}" if notes else title
    return {
        "id": "default",
        "q": q,
        "a": a,
        "lastRating": None,
        "reviews": 0,
        "synthetic": True,
    }


def select_weighted_cue(cues: list[dict[str, Any]], seed: int | None = None) -> tuple[dict[str, Any], dict[str, int]]:
    weights: dict[str, int] = {}
    weighted_pairs: list[tuple[dict[str, Any], int]] = []
    for idx, cue in enumerate(cues):
        cid = str(cue.get("id") or f"c{idx + 1}")
        w = cue_weight(cue.get("lastRating"))
        weights[cid] = w
        weighted_pairs.append((cue, w))

    rng = random.Random(seed) if seed is not None else random.Random()
    total = sum(w for _, w in weighted_pairs)
    target = rng.random() * total
    selected = weighted_pairs[-1][0]
    for cue, w in weighted_pairs:
        target -= w
        if target < 0:
            selected = cue
            break
    return selected, weights


def _format_why_lines(chains: list[dict[str, Any]], pid: str) -> list[str]:
    lines: list[str] = []
    seen_keys: set[str] = set()
    for raw in chains:
        norm = normalize_chain(raw)
        if not norm or norm["conclusion"] != pid:
            continue
        if norm["chainKey"] in seen_keys:
            continue
        seen_keys.add(norm["chainKey"])
        rel = html.escape(norm["relation"])
        why_html = md_math_to_anki_mathjax(norm["why"])
        if norm["isJoint"]:
            prem_html = " ∧ ".join(f"<code>{html.escape(pr)}</code>" for pr in norm["premises"])
            suffix = f": {why_html}" if why_html else ""
            lines.append(f"• {prem_html} (joint {rel}){suffix}")
        else:
            prem_html = f"<code>{html.escape(norm['premises'][0])}</code>"
            lines.append(f"• {prem_html} ({rel}): {why_html}")
    return lines


def _parse_occlusion_arg(raw_occ: str | None, default_src: str | None = None) -> dict[str, Any] | None:
    if not raw_occ or not str(raw_occ).strip():
        return None
    raw = str(raw_occ).strip()
    if raw.startswith("@"):
        p = Path(raw[1:]).expanduser().resolve()
        if not p.is_file():
            raise SystemExit(f"Occlusion JSON file not found: {p}")
        raw = p.read_text(encoding="utf-8")
    elif not raw.startswith(("{", "[")):
        p = Path(raw).expanduser().resolve()
        if p.is_file():
            raw = p.read_text(encoding="utf-8")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid --occlusion JSON: {exc}") from exc

    if isinstance(parsed, list):
        masks_raw = parsed
        src = str(default_src or "").strip()
        mode = "hide_one"
    elif isinstance(parsed, dict):
        masks_raw = parsed.get("masks") or []
        src = str(parsed.get("src") or default_src or "").strip()
        mode = str(parsed.get("mode") or "hide_one").strip().lower()
    else:
        raise SystemExit("--occlusion must decode to a JSON object or masks array.")

    if mode not in ("hide_one", "hide_all"):
        mode = "hide_one"

    norm_masks: list[dict[str, Any]] = []
    for idx, m in enumerate(masks_raw, start=1):
        if not isinstance(m, dict):
            continue
        bbox = m.get("bbox")
        if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
            continue
        norm_masks.append(
            {
                "id": str(m.get("id") or f"m{idx}"),
                "bbox": [round(float(v), 4) for v in bbox],
                "label": str(m.get("label") or ""),
                "propId": str(m.get("propId") or ""),
            }
        )
    if not norm_masks:
        raise SystemExit("--occlusion requires at least one valid mask with bbox: [x, y, w, h].")
    return {"src": src, "mode": mode, "masks": norm_masks}


# ---------------------------------------------------------------------------
# Importable programmatic review & add helpers (used by exam_runner & server)
# ---------------------------------------------------------------------------


def record_review(
    kg_dir: Path | str | None,
    prop_id: str,
    rating: str,
    *,
    cue_id: str | None = None,
    title: str | None = None,
    subject: str | None = None,
    chapter: str | None = None,
    kind: str = "derivation",
    notes: str | None = None,
    misconception: bool = False,
    confidence: float | None = None,
    duration_ms: int | None = None,
    no_fire: bool = False,
    no_sync: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Record an FSRS-6 + confidence-aware BKT + True FIRe review in-process."""
    resolved_kg, props_file, chains_file, queue_file, _is_default = resolve_kg_paths(kg_dir)
    props = load_jsonl(props_file)
    chains = load_jsonl(chains_file)

    r_low = str(rating).strip().lower()
    if r_low not in RATINGS:
        raise ValueError(f"Invalid rating {rating!r}; expected one of: again, hard, good, easy.")

    target = next((p for p in props if p.get("id") == prop_id), None)
    if target is None:
        if not title:
            raise ValueError(
                f"Proposition '{prop_id}' not found. Pass --title (and --subject/--chapter) to create it."
            )
        target = {
            "id": prop_id,
            "subject": subject or "General",
            "chapter": chapter or "General",
            "title": title,
            "kind": kind or "derivation",
            "status": "planned",
        }
        props.append(target)

    updated_cue: dict[str, Any] | None = None
    cue_kind = "recall"
    if cue_id:
        cues = target.get("cues")
        if not isinstance(cues, list):
            raise ValueError(f"Cue '{cue_id}' not found on proposition '{prop_id}'. Add cues first with add-cue.")
        updated_cue = next((c for c in cues if isinstance(c, dict) and str(c.get("id")) == str(cue_id)), None)
        if updated_cue is None:
            raise ValueError(f"Cue '{cue_id}' not found on proposition '{prop_id}'.")
        cue_kind = str(updated_cue.get("kind") or ("occlusion" if updated_cue.get("occlusion") else "recall")).lower()

    ref_now = now if now is not None else datetime.now(timezone.utc)
    params = load_fsrs_parameters(resolved_kg)
    card_dict, rev_meta = review_card_dict(
        target.get("fsrs") if isinstance(target.get("fsrs"), dict) else None,
        r_low,
        ref_now,
        parameters=params,
        review_duration_ms=duration_ms,
    )

    conf_norm = _normalize_confidence(confidence)
    bkt_correct = r_low in ("good", "easy")
    conf_sig = assess_confidence_signal(bkt_correct, conf_norm)

    # Butterfield & Metcalfe hypercorrection: high-confidence miss gets a crisp 1-2d retest window.
    if conf_sig["hypercorrection"]:
        short_due = ref_now + timedelta(days=1)
        cur_due = datetime.fromisoformat(str(card_dict["due"]).replace("Z", "+00:00"))
        if cur_due > short_due:
            card_dict["due"] = short_due.isoformat()

    due_date = str(card_dict["due"])[:10]
    today = ref_now.date().isoformat()
    prior_bkt = float(target.get("bktMastery", 0.30))
    new_bkt = bkt_update(
        prior_bkt,
        bkt_correct,
        p_transit=RATING_TRANSIT[r_low],
        learner_confidence=conf_norm,
    )

    target["fsrs"] = card_dict
    target["bktMastery"] = new_bkt
    target["confidence"] = new_bkt
    target["lastTested"] = today
    target["nextReview"] = due_date
    if r_low == "again":
        target["lapses"] = int(target.get("lapses") or 0) + 1
        target["status"] = "misconception" if misconception else "shaky"
    elif r_low == "hard":
        target["status"] = "shaky"
    else:
        target["status"] = "shaky" if conf_sig["luckyGuess"] else "solid"
    if notes:
        target["notes"] = notes

    if updated_cue is not None:
        updated_cue["lastRating"] = r_low
        updated_cue["reviews"] = int(updated_cue.get("reviews") or 0) + 1

    # True FIRe: propagate implicit prerequisite credit on unaided good/easy (skip on lucky guess or --no-fire).
    fire_credits: list[dict[str, Any]] = []
    if not no_fire and r_low in ("good", "easy") and not conf_sig["luckyGuess"]:
        fire_credits = apply_fire_credit(
            target["id"],
            r_low,
            props,
            chains,
            now=ref_now,
            parameters=params,
        )

    save_jsonl_atomic(props_file, props)

    revlog_entry: dict[str, Any] = {
        "propId": target["id"],
        "cueId": str(updated_cue["id"]) if updated_cue is not None else None,
        "cueKind": cue_kind,
        "ts": ref_now.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "rating": r_low,
        "ratingInt": RATINGS[r_low],
        "elapsedDays": rev_meta["elapsedDays"],
        "state": rev_meta["prevState"],
        "predictedR": rev_meta["predictedR"],
        "learnerConfidence": conf_sig["learnerConfidence"],
        "correct": bkt_correct,
        "durationMs": int(duration_ms) if duration_ms is not None else None,
        "hypercorrection": conf_sig["hypercorrection"],
        "luckyGuess": conf_sig["luckyGuess"],
        "fireCreditsCount": len(fire_credits),
    }
    revlog_file = resolved_kg / "revlog.jsonl"
    append_jsonl_locked(revlog_file, revlog_entry)
    revlog_rows = load_jsonl(revlog_file)

    fire_line = (
        f"FIRe implicitly refreshed {len(fire_credits)} prerequisite(s) from `{target['id']}`."
        if fire_credits
        else None
    )
    regenerate_review_queue(
        props,
        queue_file,
        kg_dir=resolved_kg,
        chains=chains,
        revlog_rows=revlog_rows,
        fire_summary=fire_line,
    )
    if not no_sync:
        sync_viewer(resolved_kg)

    out: dict[str, Any] = {
        "id": target["id"],
        "rating": r_low,
        "status": target["status"],
        "bktMastery": new_bkt,
        "stabilityDays": round(float(card_dict.get("stability") or 0), 2),
        "difficulty": round(float(card_dict.get("difficulty") or 0), 2),
        "nextReview": due_date,
        "lapses": int(target.get("lapses") or 0),
        "fireCredits": fire_credits,
        "hypercorrection": conf_sig["hypercorrection"],
        "luckyGuess": conf_sig["luckyGuess"],
        "backend": rev_meta["backend"],
    }
    if conf_sig["learnerConfidence"] is not None:
        out["learnerConfidence"] = conf_sig["learnerConfidence"]
        out["brierError"] = conf_sig["brierError"]
    if updated_cue is not None:
        out["cueId"] = str(updated_cue["id"])
        out["cueReviews"] = int(updated_cue["reviews"])
    return out


# ---------------------------------------------------------------------------
# CLI subcommand handlers
# ---------------------------------------------------------------------------


def cmd_add(args: argparse.Namespace) -> None:
    kg_dir, props_file, chains_file, queue_file, _is_default = resolve_kg_paths(getattr(args, "kg_dir", None))
    props = load_jsonl(props_file)
    pid = str(args.prop_id).strip()
    if not pid:
        raise SystemExit("prop_id cannot be empty.")

    aliases = [a.strip() for raw in (getattr(args, "aliases", None) or []) for a in str(raw).split(",") if a.strip()]
    tags = [t.strip().lower() for raw in (getattr(args, "tags", None) or []) for t in str(raw).split(",") if t.strip()]

    existing = next((p for p in props if p.get("id") == pid), None)
    if existing is not None:
        action = "updated"
        if args.title:
            existing["title"] = args.title.strip()
        if args.subject:
            existing["subject"] = args.subject.strip()
        if args.chapter:
            existing["chapter"] = args.chapter.strip()
        if args.kind:
            existing["kind"] = args.kind.strip()
        if args.status:
            existing["status"] = args.status.strip().lower()
        if args.notes is not None:
            existing["notes"] = args.notes
        if getattr(args, "prompt", None):
            existing["prompt"] = args.prompt
        if aliases:
            existing["aliases"] = list(dict.fromkeys(list(existing.get("aliases") or []) + aliases))
        if tags:
            existing["tags"] = list(dict.fromkeys(list(existing.get("tags") or []) + tags))
        prop_obj = existing
    else:
        action = "added"
        prop_obj = {
            "id": pid,
            "subject": (args.subject or "General").strip(),
            "chapter": (args.chapter or "General").strip(),
            "title": (args.title or pid).strip(),
            "kind": (args.kind or "derivation").strip(),
            "status": (args.status or "planned").strip().lower(),
        }
        if args.notes:
            prop_obj["notes"] = args.notes
        if getattr(args, "prompt", None):
            prop_obj["prompt"] = args.prompt
        if aliases:
            prop_obj["aliases"] = aliases
        if tags:
            prop_obj["tags"] = tags
        props.append(prop_obj)

    save_jsonl_atomic(props_file, props)
    regenerate_review_queue(props, queue_file, kg_dir=kg_dir, chains=load_jsonl(chains_file))
    if not getattr(args, "no_sync", False):
        sync_viewer(kg_dir)

    print(json.dumps({"action": action, "proposition": prop_obj, "totalNodes": len(props)}, indent=2, ensure_ascii=False))


def cmd_find_similar(args: argparse.Namespace) -> None:
    _kg_dir, props_file, _chains_file, _queue_file, _is_default = resolve_kg_paths(getattr(args, "kg_dir", None))
    props = load_jsonl(props_file)
    matches = find_similar_propositions(
        props,
        args.query,
        top=getattr(args, "top", 5),
        threshold=getattr(args, "threshold", 0.25),
    )
    print(json.dumps({
        "query": args.query,
        "threshold": getattr(args, "threshold", 0.25),
        "totalPropositions": len(props),
        "matches": matches,
    }, indent=2, ensure_ascii=False))


def cmd_add_cue(args: argparse.Namespace) -> None:
    kg_dir, props_file, _chains_file, _queue_file, _is_default = resolve_kg_paths(getattr(args, "kg_dir", None))
    props = load_jsonl(props_file)
    target = next((p for p in props if p.get("id") == args.prop_id), None)
    if target is None:
        raise SystemExit(f"Proposition '{args.prop_id}' not found.")

    cues = target.get("cues")
    if not isinstance(cues, list):
        cues = []
        target["cues"] = cues

    existing_ids = {str(c.get("id")) for c in cues if isinstance(c, dict) and c.get("id")}
    req_cue_id = getattr(args, "cue_id", None)
    if req_cue_id:
        cid = str(req_cue_id).strip()
    else:
        idx = 1
        while f"c{idx}" in existing_ids:
            idx += 1
        cid = f"c{idx}"

    media_arg = getattr(args, "media", None)
    occ_spec = _parse_occlusion_arg(getattr(args, "occlusion", None), default_src=media_arg)
    explicit_kind = getattr(args, "kind", None)
    inferred_kind: str | None = None
    if explicit_kind:
        inferred_kind = str(explicit_kind).strip().lower()
    elif occ_spec is not None:
        inferred_kind = "occlusion"
    elif CLOZE_PATTERN.search(str(args.q or "")):
        inferred_kind = "cloze"

    existing = next((c for c in cues if isinstance(c, dict) and str(c.get("id")) == cid), None)
    if existing is not None:
        existing["q"] = args.q
        existing["a"] = args.a
        existing.setdefault("lastRating", None)
        existing.setdefault("reviews", 0)
        if inferred_kind:
            existing["kind"] = inferred_kind
        if media_arg:
            existing["media"] = [media_arg]
        if occ_spec is not None:
            existing["occlusion"] = occ_spec
        cue_obj = existing
        action = "updated"
    else:
        cue_obj = {
            "id": cid,
            "q": args.q,
            "a": args.a,
            "lastRating": None,
            "reviews": 0,
        }
        if inferred_kind:
            cue_obj["kind"] = inferred_kind
        if media_arg:
            cue_obj["media"] = [media_arg]
        if occ_spec is not None:
            cue_obj["occlusion"] = occ_spec
        cues.append(cue_obj)
        action = "added"

    save_jsonl_atomic(props_file, props)
    if not getattr(args, "no_sync", False):
        sync_viewer(kg_dir)

    print(json.dumps({
        "propId": target["id"],
        "action": action,
        "cue": cue_obj,
        "totalCues": len(cues),
    }, indent=2, ensure_ascii=False))


def cmd_next_cue(args: argparse.Namespace) -> None:
    _kg_dir, props_file, _chains_file, _queue_file, _is_default = resolve_kg_paths(getattr(args, "kg_dir", None))
    props = load_jsonl(props_file)
    target = next((p for p in props if p.get("id") == args.prop_id), None)
    if target is None:
        raise SystemExit(f"Proposition '{args.prop_id}' not found.")

    raw_cues = [c for c in (target.get("cues") or []) if isinstance(c, dict) and (c.get("q") or c.get("a"))]
    synthetic = False
    if raw_cues:
        candidates = raw_cues
    else:
        synthetic = True
        candidates = [synthesize_default_cue(target)]

    selected, weights = select_weighted_cue(candidates, seed=getattr(args, "seed", None))
    print(json.dumps({
        "propId": target["id"],
        "cue": selected,
        "weights": weights,
        "totalCues": len(candidates),
        "synthetic": synthetic,
    }, indent=2, ensure_ascii=False))


def cmd_review(args: argparse.Namespace) -> None:
    try:
        out = record_review(
            getattr(args, "kg_dir", None),
            args.prop_id,
            args.rating,
            cue_id=getattr(args, "cue_id", None),
            title=getattr(args, "title", None),
            subject=getattr(args, "subject", None),
            chapter=getattr(args, "chapter", None),
            kind=getattr(args, "kind", "derivation"),
            notes=getattr(args, "notes", None),
            misconception=bool(getattr(args, "misconception", False)),
            confidence=getattr(args, "confidence", None),
            duration_ms=getattr(args, "duration_ms", None),
            no_fire=bool(getattr(args, "no_fire", False)),
            no_sync=bool(getattr(args, "no_sync", False)),
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps(out, indent=2, ensure_ascii=False))


def cmd_due(args: argparse.Namespace) -> None:
    kg_dir, props_file, chains_file, _queue_file, _is_default = resolve_kg_paths(getattr(args, "kg_dir", None))
    props = load_jsonl(props_file)
    today_arg = getattr(args, "today", None)
    if today_arg and str(today_arg).strip():
        today = str(today_arg).strip()[:10]
        now = datetime.fromisoformat(f"{today}T12:00:00+00:00")
    else:
        now = datetime.now(timezone.utc)
        today = now.date().isoformat()
    params = load_fsrs_parameters(kg_dir)

    items: list[dict[str, Any]] = []
    for p in props:
        status = p.get("status", "planned")
        due_date = p.get("nextReview")
        is_due = status in ("misconception", "shaky") or (isinstance(due_date, str) and due_date <= today)
        if not is_due:
            continue
        retr: float | None = None
        if isinstance(p.get("fsrs"), dict):
            try:
                retr = round(float(card_retrievability(p["fsrs"], now, parameters=params)), 3)
            except Exception:
                pass
        cues = [c for c in (p.get("cues") or []) if isinstance(c, dict)]
        has_occ = bool(p.get("occlusion")) or any(c.get("occlusion") or c.get("kind") == "occlusion" for c in cues)
        items.append({
            "id": p["id"],
            "subject": p.get("subject"),
            "chapter": p.get("chapter"),
            "title": p.get("title"),
            "status": status,
            "bktMastery": p.get("bktMastery"),
            "nextReview": due_date,
            "retrievability": retr,
            "cueKind": "occlusion" if has_occ and not any(c.get("kind", "recall") != "occlusion" for c in cues) else "recall",
            "notes": p.get("notes", ""),
        })

    items.sort(
        key=lambda x: (
            0 if x["status"] == "misconception" else 1 if x["status"] == "shaky" else 2,
            x["retrievability"] if x["retrievability"] is not None else 0.5,
            str(x["id"]),
        )
    )

    buried: list[dict[str, Any]] = []
    if getattr(args, "bury_siblings", False):
        revlog_rows = load_jsonl(kg_dir / "revlog.jsonl")
        items, buried = apply_sibling_burying(items, revlog_rows, now=now)

    payload: dict[str, Any] = {
        "today": today,
        "dueCount": len(items),
        "items": items[: args.limit],
    }

    if getattr(args, "compress", False):
        chains = load_jsonl(chains_file)
        comp = compress_due_queue(items, chains)
        payload.update({
            "compressed": True,
            "originalDueCount": comp["originalCount"],
            "dueCount": comp["compressedCount"],
            "knockedOutCount": comp["knockedOutCount"],
            "savingsPct": comp["savingsPct"],
            "compressionSummary": comp["summary"],
            "knockedOut": comp["knockedOut"],
            "items": comp["selected"][: args.limit],
        })

    if buried:
        payload["buriedCount"] = len(buried)
        payload["buried"] = buried

    print(json.dumps(payload, indent=2, ensure_ascii=False))


def cmd_recover(args: argparse.Namespace) -> None:
    kg_dir, props_file, chains_file, queue_file, _is_default = resolve_kg_paths(getattr(args, "kg_dir", None))
    props = load_jsonl(props_file)
    chains = load_jsonl(chains_file)
    params = load_fsrs_parameters(kg_dir)

    plan = recover_backlog(
        props,
        chains,
        daily_cap=getattr(args, "daily_cap", 15),
        days=getattr(args, "days", 6),
        today=getattr(args, "today", None),
        parameters=params,
    )

    applied = False
    if getattr(args, "apply", False) and plan["rescheduleMap"]:
        resched = plan["rescheduleMap"]
        for p in props:
            pid = p.get("id")
            if pid in resched:
                new_date = resched[pid]
                p["nextReview"] = new_date
                if isinstance(p.get("fsrs"), dict):
                    p["fsrs"]["due"] = f"{new_date}T12:00:00+00:00"
        save_jsonl_atomic(props_file, props)
        regenerate_review_queue(props, queue_file, kg_dir=kg_dir, chains=chains)
        if not getattr(args, "no_sync", False):
            sync_viewer(kg_dir)
        applied = True

    plan["applied"] = applied
    print(json.dumps(plan, indent=2, ensure_ascii=False))


def cmd_forecast(args: argparse.Namespace) -> None:
    kg_dir, props_file, _chains_file, _queue_file, _is_default = resolve_kg_paths(getattr(args, "kg_dir", None))
    props = load_jsonl(props_file)
    revlog_rows = load_jsonl(kg_dir / "revlog.jsonl")
    params = load_fsrs_parameters(kg_dir)

    report = forecast_retention(
        props,
        exam_date=getattr(args, "exam_date", None),
        horizon_days=getattr(args, "days", 7),
        today=getattr(args, "today", None),
        parameters=params,
        revlog_rows=revlog_rows,
    )
    print(json.dumps(report, indent=2, ensure_ascii=False))


def cmd_optimize(args: argparse.Namespace) -> None:
    kg_dir, _props_file, _chains_file, _queue_file, _is_default = resolve_kg_paths(getattr(args, "kg_dir", None))
    revlog_rows = load_jsonl(kg_dir / "revlog.jsonl")
    result = optimize_fsrs_params(
        revlog_rows,
        min_reviews=getattr(args, "min_reviews", 100),
    )

    saved_path: str | None = None
    if result.get("accepted") and not getattr(args, "dry_run", False):
        params_path = kg_dir / "fsrs-params.json"
        save_json_atomic(
            params_path,
            {
                "updatedAt": now_iso(),
                "accepted": True,
                "reviewCount": result["reviewCount"],
                "defaultOosLogLoss": result["defaultOosLogLoss"],
                "optimizedOosLogLoss": result["optimizedOosLogLoss"],
                "improvement": result["improvement"],
                "parameters": result["parameters"],
            },
        )
        saved_path = str(params_path)

    result["savedPath"] = saved_path
    print(json.dumps(result, indent=2, ensure_ascii=False))


def cmd_leeches(args: argparse.Namespace) -> None:
    kg_dir, props_file, _chains_file, _queue_file, _is_default = resolve_kg_paths(getattr(args, "kg_dir", None))
    props = load_jsonl(props_file)
    revlog_rows = load_jsonl(kg_dir / "revlog.jsonl")
    leeches = detect_leeches(
        props,
        revlog_rows,
        lapse_threshold=getattr(args, "lapse_threshold", 4),
        difficulty_threshold=getattr(args, "difficulty_threshold", 8.5),
    )
    print(json.dumps({
        "leechCount": len(leeches),
        "lapseThreshold": getattr(args, "lapse_threshold", 4),
        "difficultyThreshold": getattr(args, "difficulty_threshold", 8.5),
        "leeches": leeches,
    }, indent=2, ensure_ascii=False))


def cmd_stats(args: argparse.Namespace) -> None:
    kg_dir, props_file, chains_file, _queue_file, _is_default = resolve_kg_paths(getattr(args, "kg_dir", None))
    props = load_jsonl(props_file)
    chains = load_jsonl(chains_file)
    revlog_rows = load_jsonl(kg_dir / "revlog.jsonl")
    today = today_iso()
    counts = {"solid": 0, "shaky": 0, "misconception": 0, "planned": 0}
    bkt_vals = []
    due_count = 0
    total_cues = 0
    for p in props:
        st = p.get("status", "planned")
        counts[st] = counts.get(st, 0) + 1
        if isinstance(p.get("bktMastery"), (int, float)):
            bkt_vals.append(float(p["bktMastery"]))
        due_date = p.get("nextReview")
        if st in ("misconception", "shaky") or (isinstance(due_date, str) and due_date <= today):
            due_count += 1
        if isinstance(p.get("cues"), list):
            total_cues += sum(1 for c in p["cues"] if isinstance(c, dict))

    seen_chain_keys: set[str] = set()
    hyperedge_count = 0
    for c in chains:
        norm = normalize_chain(c)
        if not norm or norm["chainKey"] in seen_chain_keys:
            continue
        seen_chain_keys.add(norm["chainKey"])
        if norm["isJoint"]:
            hyperedge_count += 1

    leeches = detect_leeches(props, revlog_rows)
    calibration = compute_calibration(revlog_rows)

    print(json.dumps({
        "totalNodes": len(props),
        "totalChains": len(chains),
        "uniqueChains": len(seen_chain_keys),
        "hyperedgeCount": hyperedge_count,
        "totalCues": total_cues,
        "statusCounts": counts,
        "avgBktMastery": round(sum(bkt_vals) / len(bkt_vals), 3) if bkt_vals else None,
        "dueCount": due_count,
        "leechCount": len(leeches),
        "revlogCount": len(revlog_rows),
        "calibration": calibration,
    }, indent=2, ensure_ascii=False))


# ---------------------------------------------------------------------------
# Visual Anki (.apkg) + Cloze + Declarative SVG Image Occlusion & AnkiConnect
# ---------------------------------------------------------------------------


def stable_id(seed: str) -> int:
    return int(hashlib.sha256(seed.encode("utf-8")).hexdigest()[:8], 16)


def _extract_media_sides(media_field: Any) -> tuple[list[str], list[str]]:
    front: list[str] = []
    back: list[str] = []

    def _add_entries(raw_val: Any, default_side: str) -> None:
        if not raw_val:
            return
        items = raw_val if isinstance(raw_val, list) else [raw_val]
        for item in items:
            if isinstance(item, str):
                p = item.strip()
                if p:
                    (back if default_side == "back" else front).append(p)
            elif isinstance(item, dict):
                p = str(item.get("path") or item.get("src") or item.get("file") or "").strip()
                side = str(item.get("side") or default_side).strip().lower()
                if p:
                    (back if side == "back" else front).append(p)

    if isinstance(media_field, dict):
        _add_entries(media_field.get("front"), "front")
        _add_entries(media_field.get("both"), "front")
        _add_entries(media_field.get("back"), "back")
    else:
        _add_entries(media_field, "front")
    return front, back


def _resolve_media_path(raw_path: str, kg_dir: Path) -> Path | None:
    cand_raw = Path(raw_path).expanduser()
    if cand_raw.is_absolute():
        return cand_raw.resolve() if cand_raw.is_file() else None
    for base in (kg_dir.parent, kg_dir, ROOT):
        cand = (base / cand_raw).resolve()
        if cand.is_file():
            return cand
    return None


class _MediaCollector:
    """Resolve, deduplicate, collision-proof, and render Visual Anki media attachments."""

    def __init__(self) -> None:
        self.path_to_name: dict[Path, str] = {}
        self.name_to_path: dict[str, Path] = {}
        self.missing: list[str] = []

    def register(self, raw_path: str, kg_dir: Path) -> str | None:
        resolved = _resolve_media_path(raw_path, kg_dir)
        if resolved is None:
            if raw_path not in self.missing:
                self.missing.append(raw_path)
            return None
        if resolved in self.path_to_name:
            return self.path_to_name[resolved]

        safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", resolved.name).lstrip(".")
        if not safe_name:
            safe_name = f"media_{hashlib.sha256(str(resolved).encode('utf-8')).hexdigest()[:8]}{resolved.suffix}"
        if safe_name in self.name_to_path and self.name_to_path[safe_name] != resolved:
            stem = Path(safe_name).stem
            suffix = Path(safe_name).suffix
            digest = hashlib.sha256(str(resolved).encode("utf-8")).hexdigest()[:8]
            safe_name = f"{stem}_{digest}{suffix}"

        self.path_to_name[resolved] = safe_name
        self.name_to_path[safe_name] = resolved
        return safe_name

    def collect_side(self, raw_paths: list[str], kg_dir: Path) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()
        for raw in raw_paths:
            name = self.register(raw, kg_dir)
            if name and name not in seen:
                seen.add(name)
                out.append(name)
        return out

    @staticmethod
    def render_html(filenames: list[str]) -> str:
        if not filenames:
            return ""
        imgs = "".join(
            f'<img src="{html.escape(fn, quote=True)}" style="max-width:100%;border-radius:8px;margin-top:8px;">'
            for fn in filenames
        )
        return f'<div class="mlh-media">{imgs}</div>'

    def stage_for_genanki(self, stage_dir: Path) -> list[str]:
        staged: list[str] = []
        for resolved, anki_name in self.path_to_name.items():
            if resolved.name == anki_name:
                staged.append(str(resolved))
            else:
                dst = stage_dir / anki_name
                shutil.copy2(resolved, dst)
                staged.append(str(dst))
        return staged


def _render_occlusion_svg_html(
    img_filename: str,
    masks: list[dict[str, Any]],
    active_idx: int,
    *,
    mode: str = "hide_one",
    revealed: bool = False,
) -> str:
    """Render pure-CSS + inline SVG mask overlay for Image Occlusion cards."""
    rects: list[str] = []
    for idx, m in enumerate(masks):
        bbox = m.get("bbox")
        if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
            continue
        x, y, w, h = (float(v) * 1000.0 for v in bbox)
        is_active = idx == active_idx
        if revealed:
            if is_active:
                rects.append(
                    f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" '
                    f'rx="8" fill="rgba(34,197,94,0.18)" stroke="#22c55e" stroke-width="6"/>'
                )
            elif mode == "hide_all":
                rects.append(
                    f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" '
                    f'rx="8" fill="#475569" fill-opacity="0.92" stroke="#cbd5e1" stroke-width="4"/>'
                )
        else:
            if is_active:
                rects.append(
                    f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" '
                    f'rx="8" fill="#c2410c" stroke="#fed7aa" stroke-width="6"/>'
                )
            elif mode == "hide_all":
                rects.append(
                    f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" '
                    f'rx="8" fill="#64748b" fill-opacity="0.94" stroke="#cbd5e1" stroke-width="4"/>'
                )

    svg_overlay = (
        '<svg viewBox="0 0 1000 1000" preserveAspectRatio="none" '
        'style="position:absolute;inset:0;width:100%;height:100%;pointer-events:none;">'
        + "".join(rects)
        + "</svg>"
    )
    return (
        '<div class="mlh-occlusion" style="position:relative;display:inline-block;max-width:100%;margin-top:8px;">'
        f'<img src="{html.escape(img_filename, quote=True)}" style="display:block;max-width:100%;border-radius:8px;">'
        f"{svg_overlay}</div>"
    )


def _iter_occlusion_specs(obj: dict[str, Any]) -> list[dict[str, Any]]:
    raw = obj.get("occlusion")
    if isinstance(raw, dict) and isinstance(raw.get("masks"), list):
        return [raw]
    if isinstance(raw, list):
        return [x for x in raw if isinstance(x, dict) and isinstance(x.get("masks"), list)]
    return []


def cmd_export_anki(args: argparse.Namespace) -> None:
    try:
        import genanki  # type: ignore[import-not-found]
    except ImportError as exc:
        raise SystemExit(
            "genanki is required for `export-anki`. Install it in your venv (`pip install genanki`) "
            "or use `syntopik serve` / `review` / `due` which run on bare Python 3.12+."
        ) from exc

    kg_dir, props_file, chains_file, _queue_file, _is_default = resolve_kg_paths(getattr(args, "kg_dir", None))
    props = load_jsonl(props_file)
    chains = load_jsonl(chains_file)
    if args.subject:
        props = [p for p in props if str(p.get("subject", "")).lower() == args.subject.lower()]

    deck_name = f"Syntopik::{args.subject}" if args.subject else "Syntopik"
    deck = genanki.Deck(stable_id(deck_name), deck_name)
    base_css = """
        .card { font-family: -apple-system, BlinkMacSystemFont, "Inter", sans-serif; font-size: 17px; line-height: 1.55; color: #e8edf5; background: #10141c; padding: 20px; }
        .meta { font-size: 12px; color: #94a3b8; margin-bottom: 10px; text-transform: uppercase; letter-spacing: 0.04em; }
        .prompt { font-size: 19px; font-weight: 600; }
        .answer { margin-top: 12px; color: #bbf7d0; }
        .cloze { font-weight: 700; color: #f59e0b; }
        .why { margin-top: 14px; padding: 10px 12px; border-left: 3px solid #60a5fa; background: #171e2b; font-size: 14px; color: #cbd5e1; }
        .mlh-media img { max-width: 100%; border-radius: 8px; margin-top: 8px; }
    """

    basic_model = genanki.Model(
        stable_id("syntopik-concept-v1"),
        "Syntopik Concept",
        fields=[
            {"name": "Prompt"},
            {"name": "Answer"},
            {"name": "WhyChain"},
            {"name": "Meta"},
        ],
        templates=[{
            "name": "Concept Recall",
            "qfmt": '<div class="meta">{{Meta}}</div><div class="prompt">{{Prompt}}</div>',
            "afmt": '{{FrontSide}}<hr id="answer"><div class="answer">{{Answer}}</div>{{#WhyChain}}<div class="why"><strong>Why / Dependencies:</strong><br>{{WhyChain}}</div>{{/WhyChain}}',
        }],
        css=base_css,
    )

    cloze_model = genanki.Model(
        stable_id("syntopik-cloze-v2"),
        "Syntopik Cloze",
        model_type=genanki.Model.CLOZE,
        fields=[
            {"name": "Text"},
            {"name": "Back Extra"},
            {"name": "WhyChain"},
            {"name": "Meta"},
        ],
        templates=[{
            "name": "Cloze Recall",
            "qfmt": '<div class="meta">{{Meta}}</div><div class="prompt">{{cloze:Text}}</div>',
            "afmt": '<div class="meta">{{Meta}}</div><div class="prompt">{{cloze:Text}}</div><hr id="answer">{{#Back Extra}}<div class="answer">{{Back Extra}}</div>{{/Back Extra}}{{#WhyChain}}<div class="why"><strong>Why / Dependencies:</strong><br>{{WhyChain}}</div>{{/WhyChain}}',
        }],
        css=base_css,
    )

    occ_model = genanki.Model(
        stable_id("syntopik-occlusion-v2"),
        "Syntopik Image Occlusion",
        fields=[
            {"name": "Front"},
            {"name": "Back"},
            {"name": "WhyChain"},
            {"name": "Meta"},
        ],
        templates=[{
            "name": "Image Occlusion",
            "qfmt": '<div class="meta">{{Meta}}</div><div class="prompt">{{Front}}</div>',
            "afmt": '<div class="meta">{{Meta}}</div><div class="answer">{{Back}}</div>{{#WhyChain}}<div class="why"><strong>Why / Dependencies:</strong><br>{{WhyChain}}</div>{{/WhyChain}}',
        }],
        css=base_css,
    )

    collector = _MediaCollector()
    exported = 0
    cloze_cards = 0
    occlusion_cards = 0

    def _emit_occlusion_notes(
        occ_spec: dict[str, Any],
        pid: str,
        suffix_id: str,
        prompt_text: str,
        extra_notes: str,
        why_lines: list[str],
        meta_label: str,
        tags: list[str],
    ) -> int:
        nonlocal exported, occlusion_cards
        src_raw = str(occ_spec.get("src") or "").strip()
        masks = [m for m in (occ_spec.get("masks") or []) if isinstance(m, dict) and m.get("bbox")]
        if not src_raw or not masks:
            return 0
        img_fn = collector.register(src_raw, kg_dir)
        if not img_fn:
            return 0
        mode = str(occ_spec.get("mode") or "hide_one").strip().lower()
        count = 0
        for m_idx, mask in enumerate(masks):
            mid = str(mask.get("id") or f"m{m_idx + 1}")
            lbl = str(mask.get("label") or f"Region {mid}")
            front_html = (
                md_math_to_anki_mathjax(prompt_text)
                + _render_occlusion_svg_html(img_fn, masks, m_idx, mode=mode, revealed=False)
            )
            back_parts = [
                f"<strong>{md_math_to_anki_mathjax(lbl)}</strong>",
                _render_occlusion_svg_html(img_fn, masks, m_idx, mode=mode, revealed=True),
            ]
            if extra_notes:
                back_parts.append(f"<br><em>Notes / Traps:</em> {md_math_to_anki_mathjax(extra_notes)}")
            note = genanki.Note(
                model=occ_model,
                fields=[front_html, "".join(back_parts), "<br>".join(why_lines), html.escape(f"{meta_label} · io:{mid}")],
                tags=[*tags, "occlusion", f"io::{_sanitize_tag(mid)}"],
                guid=genanki.guid_for(f"{pid}:{suffix_id}:io:{mid}"),
            )
            deck.add_note(note)
            exported += 1
            occlusion_cards += 1
            count += 1
        return count

    for p in props:
        pid = p.get("id", "")
        if not pid:
            continue
        why_lines = _format_why_lines(chains, pid)
        base_meta = " · ".join(filter(None, [p.get("subject"), p.get("chapter"), p.get("kind"), p.get("status")]))
        base_tags = [_sanitize_tag(t) for t in [p.get("subject", ""), p.get("chapter", ""), p.get("status", "")] if str(t).strip()]
        valid_cues = [c for c in (p.get("cues") or []) if isinstance(c, dict) and (c.get("q") or c.get("a") or c.get("occlusion"))]
        prop_front_raw, prop_back_raw = _extract_media_sides(p.get("media"))

        for o_idx, prop_occ in enumerate(_iter_occlusion_specs(p)):
            _emit_occlusion_notes(
                prop_occ,
                pid,
                f"prop-occ-{o_idx}",
                str(p.get("prompt") or f"Identify the occluded region: {p.get('title', pid)}"),
                str(p.get("notes") or ""),
                why_lines,
                base_meta,
                base_tags,
            )

        if valid_cues:
            for idx, cue in enumerate(valid_cues):
                cid = str(cue.get("id") or f"c{idx + 1}")
                cue_meta = f"{base_meta} · cue:{cid}" if base_meta else f"cue:{cid}"
                tags = [*base_tags, f"cue::{_sanitize_tag(cid)}"]

                occ_specs = _iter_occlusion_specs(cue)
                if occ_specs:
                    for o_idx, occ in enumerate(occ_specs):
                        _emit_occlusion_notes(
                            occ,
                            pid,
                            f"{cid}-{o_idx}",
                            str(cue.get("q") or p.get("title", pid)),
                            str(cue.get("a") or p.get("notes") or ""),
                            why_lines,
                            cue_meta,
                            tags,
                        )
                    continue

                cue_front_raw, cue_back_raw = _extract_media_sides(cue.get("media"))
                front_files = collector.collect_side(prop_front_raw + cue_front_raw, kg_dir)
                back_files = collector.collect_side(prop_back_raw + cue_back_raw, kg_dir)

                prompt = str(cue.get("q") or p.get("prompt") or f"State and justify: {p.get('title', pid)}")
                is_cloze = bool(CLOZE_PATTERN.search(prompt)) or str(cue.get("kind") or "").lower() == "cloze"

                if is_cloze and CLOZE_PATTERN.search(prompt):
                    text_html = md_math_to_anki_mathjax(prompt) + collector.render_html(front_files)
                    extra_parts = [md_math_to_anki_mathjax(cue.get("a") or "")]
                    if back_files:
                        extra_parts.append(collector.render_html(back_files))
                    if p.get("notes"):
                        extra_parts.append(f"<br><br><em>Notes / Traps:</em> {md_math_to_anki_mathjax(p['notes'])}")
                    note = genanki.Note(
                        model=cloze_model,
                        fields=[text_html, "".join(extra_parts), "<br>".join(why_lines), html.escape(cue_meta)],
                        tags=[*tags, "cloze"],
                        guid=genanki.guid_for(f"{pid}:{cid}:cloze"),
                    )
                    deck.add_note(note)
                    exported += 1
                    cloze_cards += 1
                    continue

                prompt_html = md_math_to_anki_mathjax(prompt) + collector.render_html(front_files)
                answer_parts = [md_math_to_anki_mathjax(cue.get("a") or p.get("title", ""))]
                if back_files:
                    answer_parts.append(collector.render_html(back_files))
                if p.get("notes"):
                    answer_parts.append(f"<br><br><em>Notes / Traps:</em> {md_math_to_anki_mathjax(p['notes'])}")
                note = genanki.Note(
                    model=basic_model,
                    fields=[prompt_html, "".join(answer_parts), "<br>".join(why_lines), html.escape(cue_meta)],
                    tags=tags,
                    guid=genanki.guid_for(f"{pid}:{cid}"),
                )
                deck.add_note(note)
                exported += 1
        elif not _iter_occlusion_specs(p):
            front_files = collector.collect_side(prop_front_raw, kg_dir)
            back_files = collector.collect_side(prop_back_raw, kg_dir)

            prompt = p.get("prompt") or f"State and justify: {p.get('title', pid)}"
            if CLOZE_PATTERN.search(str(prompt)):
                text_html = md_math_to_anki_mathjax(str(prompt)) + collector.render_html(front_files)
                extra_parts = [md_math_to_anki_mathjax(p.get("title", ""))]
                if back_files:
                    extra_parts.append(collector.render_html(back_files))
                if p.get("notes"):
                    extra_parts.append(f"<br><br><em>Notes / Traps:</em> {md_math_to_anki_mathjax(p['notes'])}")
                note = genanki.Note(
                    model=cloze_model,
                    fields=[text_html, "".join(extra_parts), "<br>".join(why_lines), html.escape(base_meta)],
                    tags=[*base_tags, "cloze"],
                    guid=genanki.guid_for(f"{pid}:cloze"),
                )
                deck.add_note(note)
                exported += 1
                cloze_cards += 1
            else:
                prompt_html = md_math_to_anki_mathjax(prompt) + collector.render_html(front_files)
                answer_parts = [md_math_to_anki_mathjax(p.get("title", ""))]
                if back_files:
                    answer_parts.append(collector.render_html(back_files))
                if p.get("notes"):
                    answer_parts.append(f"<br><br><em>Notes / Traps:</em> {md_math_to_anki_mathjax(p['notes'])}")
                note = genanki.Note(
                    model=basic_model,
                    fields=[prompt_html, "".join(answer_parts), "<br>".join(why_lines), html.escape(base_meta)],
                    tags=base_tags,
                    guid=genanki.guid_for(pid),
                )
                deck.add_note(note)
                exported += 1

    out_path = Path(args.out).expanduser().resolve() if args.out else (kg_dir / "syntopik.apkg")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="mlh-anki-media-") as stage_tmp:
        pkg = genanki.Package(deck)
        pkg.media_files = collector.stage_for_genanki(Path(stage_tmp))
        pkg.write_to_file(str(out_path))

    print(json.dumps({
        "deck": deck_name,
        "exportedCards": exported,
        "clozeCards": cloze_cards,
        "occlusionCards": occlusion_cards,
        "mediaCount": len(collector.path_to_name),
        "mediaFiles": sorted(collector.name_to_path.keys()),
        "missingMediaCount": len(collector.missing),
        "missingMedia": collector.missing,
        "output": str(out_path),
    }, indent=2))


def anki_connect(action: str, params: dict[str, Any] | None = None, url: str = "http://127.0.0.1:8765") -> Any:
    body = json.dumps({"action": action, "version": 6, "params": params or {}}).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if data.get("error"):
        raise RuntimeError(str(data["error"]))
    return data.get("result")


def cmd_push_anki(args: argparse.Namespace) -> None:
    """Push Knowledge Graph concepts (with embedded Visual Anki media) live via AnkiConnect (code 2055492159)."""
    kg_dir, props_file, chains_file, _queue_file, _is_default = resolve_kg_paths(getattr(args, "kg_dir", None))
    props = load_jsonl(props_file)
    chains = load_jsonl(chains_file)
    if args.subject:
        props = [p for p in props if str(p.get("subject", "")).lower() == args.subject.lower()]

    deck_name = f"Syntopik::{args.subject}" if args.subject else "Syntopik"
    try:
        anki_connect("createDeck", {"deck": deck_name}, url=args.url)
    except Exception as exc:
        raise SystemExit(
            f"Could not reach AnkiConnect at {args.url} ({exc}). "
            "Open Anki Desktop with AnkiConnect add-on (code 2055492159) installed, or run `export-anki` to generate an offline .apkg file."
        )

    collector = _MediaCollector()
    prepared_notes: list[dict[str, Any]] = []

    for p in props:
        pid = p.get("id", "")
        if not pid:
            continue
        why_lines = _format_why_lines(chains, pid)
        base_tags = ["syntopik", f"id::{_sanitize_tag(pid)}"]
        if p.get("subject"):
            base_tags.append(_sanitize_tag(p["subject"]))
        if p.get("chapter"):
            base_tags.append(_sanitize_tag(p["chapter"]))

        valid_cues = [c for c in (p.get("cues") or []) if isinstance(c, dict) and (c.get("q") or c.get("a"))]
        prop_front_raw, prop_back_raw = _extract_media_sides(p.get("media"))

        card_specs: list[tuple[str, str, list[str], list[str], list[str], str | None]] = []
        if valid_cues:
            for idx, cue in enumerate(valid_cues):
                cid = str(cue.get("id") or f"c{idx + 1}")
                cue_front_raw, cue_back_raw = _extract_media_sides(cue.get("media"))
                front_files = collector.collect_side(prop_front_raw + cue_front_raw, kg_dir)
                back_files = collector.collect_side(prop_back_raw + cue_back_raw, kg_dir)
                front_text = str(cue.get("q") or p.get("prompt") or f"State and justify: {p.get('title', pid)}")
                back_text = str(cue.get("a") or p.get("title", ""))
                card_specs.append((front_text, back_text, [*base_tags, f"cue::{_sanitize_tag(cid)}"], front_files, back_files, cid))
        else:
            front_files = collector.collect_side(prop_front_raw, kg_dir)
            back_files = collector.collect_side(prop_back_raw, kg_dir)
            front_text = str(p.get("prompt") or f"State and justify: {p.get('title', pid)}")
            back_text = str(p.get("title", ""))
            card_specs.append((front_text, back_text, base_tags, front_files, back_files, None))

        for front_text, back_text, tags, front_files, back_files, cid in card_specs:
            front_html = md_math_to_anki_mathjax(front_text) + collector.render_html(front_files)
            answer_parts = [md_math_to_anki_mathjax(back_text)]
            if back_files:
                answer_parts.append(collector.render_html(back_files))
            if p.get("notes"):
                answer_parts.append(f"<br><br><em>Notes / Traps:</em> {md_math_to_anki_mathjax(p['notes'])}")
            if why_lines:
                answer_parts.append("<br><br><strong>Why / Dependencies:</strong><br>" + "<br>".join(why_lines))
            prepared_notes.append({
                "pid": pid,
                "cid": cid,
                "front": front_html,
                "back": "".join(answer_parts),
                "tags": tags,
            })

    media_uploaded = 0
    for resolved_path, anki_name in collector.path_to_name.items():
        b64_data = base64.b64encode(resolved_path.read_bytes()).decode("ascii")
        anki_connect("storeMediaFile", {"filename": anki_name, "data": b64_data}, url=args.url)
        media_uploaded += 1

    added = 0
    updated = 0
    skipped = 0
    allow_update = bool(getattr(args, "update", False))
    for item in prepared_notes:
        fields = {"Front": item["front"], "Back": item["back"]}
        try:
            anki_connect("addNote", {
                "note": {
                    "deckName": deck_name,
                    "modelName": "Basic",
                    "fields": fields,
                    "options": {"allowDuplicate": False, "duplicateScope": "deck"},
                    "tags": item["tags"],
                }
            }, url=args.url)
            added += 1
        except Exception:
            if allow_update:
                try:
                    query = f'deck:"{deck_name}" tag:id::{_sanitize_tag(item["pid"])}'
                    if item["cid"]:
                        query += f' tag:cue::{_sanitize_tag(item["cid"])}'
                    note_ids = anki_connect("findNotes", {"query": query}, url=args.url) or []
                    if note_ids:
                        anki_connect("updateNoteFields", {"note": {"id": note_ids[0], "fields": fields}}, url=args.url)
                        updated += 1
                        continue
                except Exception:
                    pass
            skipped += 1

    print(json.dumps({
        "deck": deck_name,
        "added": added,
        "updated": updated,
        "skippedDuplicates": skipped,
        "mediaUploaded": media_uploaded,
        "missingMediaCount": len(collector.missing),
        "missingMedia": collector.missing,
    }, indent=2))


def _add_kg_dir_flag(subparser: argparse.ArgumentParser) -> None:
    subparser.add_argument("--kg-dir", default=argparse.SUPPRESS, help="Override knowledge-graph directory (for sandboxed testing)")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Syntopik FSRS-6 + True FIRe + SRC + Calibration + Multi-Cue/Cloze/Occlusion Anki Engine"
    )
    parser.add_argument("--kg-dir", help="Override knowledge-graph directory (for sandboxed testing)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_add = sub.add_parser("add", help="Add or update a proposition in propositions.jsonl")
    p_add.add_argument("prop_id")
    p_add.add_argument("--title", required=True)
    p_add.add_argument("--subject", default="General")
    p_add.add_argument("--chapter", default="General")
    p_add.add_argument("--kind", default="derivation")
    p_add.add_argument("--status", default="planned", choices=["planned", "shaky", "solid", "misconception"])
    p_add.add_argument("--notes")
    p_add.add_argument("--prompt")
    p_add.add_argument("--aliases", action="append", default=[])
    p_add.add_argument("--tags", action="append", default=[])
    p_add.add_argument("--no-sync", action="store_true", help="Skip regenerating viewer/dashboard")
    _add_kg_dir_flag(p_add)
    p_add.set_defaults(func=cmd_add)

    p1 = sub.add_parser("review", help="Record an FSRS-6 + FIRe + BKT review outcome and update review-queue.md & revlog.jsonl")
    p1.add_argument("prop_id")
    p1.add_argument("--rating", required=True, choices=["again", "hard", "good", "easy"])
    p1.add_argument("--cue-id", help="Optional cue ID on the proposition whose lastRating & reviews count will be updated")
    p1.add_argument("--title", help="Required only if creating a brand-new proposition")
    p1.add_argument("--subject")
    p1.add_argument("--chapter")
    p1.add_argument("--kind", default="derivation")
    p1.add_argument("--notes")
    p1.add_argument("--misconception", action="store_true")
    p1.add_argument("--confidence", type=float, default=None, help="Learner confidence 0..1 (or 0..100)")
    p1.add_argument("--duration-ms", type=int, default=None, help="Review response duration in milliseconds")
    p1.add_argument("--no-fire", action="store_true", help="Disable FIRe implicit prerequisite credit")
    p1.add_argument("--no-sync", action="store_true", help="Skip regenerating viewer/dashboard")
    _add_kg_dir_flag(p1)
    p1.set_defaults(func=cmd_review)

    p2 = sub.add_parser("due", help="List due, shaky, and misconception concepts ranked by urgency (optional FIRe compression)")
    p2.add_argument("--limit", type=int, default=20)
    p2.add_argument("--compress", action="store_true", help="Apply Spaced Repetition Compression (greedy FIRe knockout set-cover)")
    p2.add_argument("--bury-siblings", action="store_true", help="Apply 24h per-type sibling burying (exempts image occlusion)")
    p2.add_argument("--today", help="Optional ISO date YYYY-MM-DD override")
    _add_kg_dir_flag(p2)
    p2.set_defaults(func=cmd_due)

    p_rec = sub.add_parser("recover", help="Calm backlog recovery: smooth overdue tail across 5-7 days within a daily cap")
    p_rec.add_argument("--daily-cap", type=int, default=15, help="Maximum calm reviews per day (default: 15)")
    p_rec.add_argument("--days", type=int, default=6, help="Target recovery window in days (5-7, default: 6)")
    p_rec.add_argument("--today", help="Optional ISO date YYYY-MM-DD override")
    p_rec.add_argument("--apply", action="store_true", help="Persist smoothed nextReview dates into propositions.jsonl")
    p_rec.add_argument("--no-sync", action="store_true", help="Skip regenerating viewer/dashboard")
    _add_kg_dir_flag(p_rec)
    p_rec.set_defaults(func=cmd_recover)

    p_fc = sub.add_parser("forecast", help="Forecast 7-day load, chapter readiness R(t_exam), and Brier calibration")
    p_fc.add_argument("--exam-date", help="Optional target exam date YYYY-MM-DD")
    p_fc.add_argument("--days", type=int, default=7, help="Forecast load horizon in days (default: 7)")
    p_fc.add_argument("--today", help="Optional ISO date YYYY-MM-DD override")
    _add_kg_dir_flag(p_fc)
    p_fc.set_defaults(func=cmd_forecast)

    p_opt = sub.add_parser("optimize", help="Fit personal 21-weight FSRS-6 parameters via SciPy L-BFGS-B (requires >=100 revlogs)")
    p_opt.add_argument("--min-reviews", type=int, default=100, help="Minimum revlog rows required (default: 100)")
    p_opt.add_argument("--dry-run", action="store_true", help="Evaluate OOS log-loss without writing fsrs-params.json")
    _add_kg_dir_flag(p_opt)
    p_opt.set_defaults(func=cmd_optimize)

    p_lee = sub.add_parser("leeches", help="Flag chronic leech propositions (lapses >= 4 or difficulty >= 8.5) for refactoring")
    p_lee.add_argument("--lapse-threshold", type=int, default=4)
    p_lee.add_argument("--difficulty-threshold", type=float, default=8.5)
    _add_kg_dir_flag(p_lee)
    p_lee.set_defaults(func=cmd_leeches)

    p3 = sub.add_parser("export-anki", help="Export Knowledge Graph concepts, Cloze, and Image Occlusion cards to Anki .apkg")
    p3.add_argument("--subject", help="Optional subject filter (e.g., Physics, Organic Chemistry)")
    p3.add_argument("--out", help="Output .apkg path (default: knowledge-graph/syntopik.apkg)")
    _add_kg_dir_flag(p3)
    p3.set_defaults(func=cmd_export_anki)

    p4 = sub.add_parser("push-anki", help="Push cards (and embedded media) live into running Anki Desktop via AnkiConnect (code 2055492159)")
    p4.add_argument("--subject", help="Optional subject filter")
    p4.add_argument("--url", default="http://127.0.0.1:8765")
    p4.add_argument("--update", action="store_true", help="Update existing matching notes via updateNoteFields instead of skipping duplicates")
    _add_kg_dir_flag(p4)
    p4.set_defaults(func=cmd_push_anki)

    p5 = sub.add_parser("stats", help="Show mastery counts, average BKT mastery, cue counts, leeches, and calibration")
    _add_kg_dir_flag(p5)
    p5.set_defaults(func=cmd_stats)

    p6 = sub.add_parser("find-similar", help="Lexical token + bigram/stem similarity search over propositions.jsonl before adding new nodes")
    p6.add_argument("query", help="Proposed concept title, statement, or keywords to check for duplicates")
    p6.add_argument("--top", type=int, default=5, help="Maximum matches to return (default: 5)")
    p6.add_argument("--threshold", type=float, default=0.25, help="Minimum similarity score in [0, 1] (default: 0.25)")
    p6.add_argument("--json", action="store_true", help="Emit structured JSON output")
    _add_kg_dir_flag(p6)
    p6.set_defaults(func=cmd_find_similar)

    p7 = sub.add_parser("add-cue", help="Append or update a retrieval cue (supports Cloze {{c1::...}} & SVG occlusion) on an existing proposition")
    p7.add_argument("prop_id", help="Target proposition ID")
    p7.add_argument("--q", required=True, help="Cue prompt / question (supports {{c1::...}} Cloze)")
    p7.add_argument("--a", required=True, help="Cue expected answer / justification")
    p7.add_argument("--cue-id", help="Optional cue ID (defaults to next sequential c1, c2, ...)")
    p7.add_argument("--kind", choices=VALID_CUE_KINDS, default=None, help="Optional prompt kind (recall/application/procedural/salience/cloze/occlusion)")
    p7.add_argument("--occlusion", help="Optional declarative SVG occlusion JSON string or @path")
    p7.add_argument("--media", help="Optional image/SVG media path for this cue")
    p7.add_argument("--no-sync", action="store_true", help="Skip regenerating viewer/dashboard")
    p7.add_argument("--json", action="store_true", help="Emit structured JSON output")
    _add_kg_dir_flag(p7)
    p7.set_defaults(func=cmd_add_cue)

    p8 = sub.add_parser("next-cue", help="Select the next retrieval cue using LearnHarness weighted selection (again:4, hard:3, untried:3, good:2, easy:1)")
    p8.add_argument("prop_id", help="Target proposition ID")
    p8.add_argument("--seed", type=int, help="Optional integer RNG seed for deterministic testing")
    p8.add_argument("--json", action="store_true", help="Emit structured JSON output")
    _add_kg_dir_flag(p8)
    p8.set_defaults(func=cmd_next_cue)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
