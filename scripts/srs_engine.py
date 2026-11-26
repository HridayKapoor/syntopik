#!/usr/bin/env python3
"""Deterministic FSRS v6 Spaced Repetition, BKT Mastery & Anki (.apkg) Exporter.

Reads and updates knowledge-graph/propositions.jsonl, regenerates
knowledge-graph/review-queue.md, syncs viewer.html / dashboard.html, and exports
MathJax-enabled Anki decks (.apkg) for mobile/desktop long-term retention.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import html
import json
import math
import os
import random
import re
import shutil
import subprocess
import tempfile
import unicodedata
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import fsrs
import genanki

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_KG_DIR = ROOT / "knowledge-graph"
SYNC_SCRIPT = ROOT / "scripts" / "sync-graph.mjs"

RATINGS = {
    "again": fsrs.Rating.Again,
    "hard": fsrs.Rating.Hard,
    "good": fsrs.Rating.Good,
    "easy": fsrs.Rating.Easy,
}

# Transit probability scaled by review quality so a failed recall never inflates BKT mastery.
RATING_TRANSIT = {
    "again": 0.05,
    "hard": 0.10,
    "good": 0.25,
    "easy": 0.35,
}

# LearnHarness multi-cue weighted selection scheme (proposition shares 1 FSRS-6 schedule).
CUE_WEIGHTS: dict[str | None, int] = {
    "again": 4,
    "hard": 3,
    None: 3,
    "good": 2,
    "easy": 1,
}

# Common English function words excluded from lexical similarity (keeps negation & STEM tokens).
LEXICAL_STOP_WORDS = frozenset(
    (
        "a an the and or of to in on at by for from with as is are was were be been being "
        "it its this that these those which who whose their them they we our you your "
        "can could may might must will would should has have had does do did "
        "such than then when where each every any all also into through"
    ).split()
)


def resolve_kg_paths(kg_dir_arg: str | None) -> tuple[Path, Path, Path, Path, bool]:
    if kg_dir_arg:
        kg_dir = Path(kg_dir_arg).expanduser().resolve()
        is_default = kg_dir == DEFAULT_KG_DIR.resolve()
    else:
        env_kg = os.environ.get("SYNT_KG_DIR") or os.environ.get("MLH_KG_DIR")
        if env_kg and env_kg.strip():
            kg_dir = Path(env_kg).expanduser().resolve()
            is_default = kg_dir == DEFAULT_KG_DIR.resolve()
        else:
            kg_dir = DEFAULT_KG_DIR
            is_default = True
    return kg_dir, kg_dir / "propositions.jsonl", kg_dir / "chains.jsonl", kg_dir / "review-queue.md", is_default


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


def save_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(json.dumps(r, ensure_ascii=False) for r in rows)
    path.write_text((text + "\n") if text else "", encoding="utf-8")


def make_scheduler() -> fsrs.Scheduler:
    # Day-level spacing (no 10-minute intraday steps) tailored for chapter/concept mastery.
    return fsrs.Scheduler(learning_steps=(), relearning_steps=())


def _find_node() -> str | None:
    hit = shutil.which("node")
    if hit:
        return hit
    for cand in (Path.home() / ".local" / "bin" / "node", Path("/opt/homebrew/bin/node"), Path("/usr/local/bin/node"), Path("/usr/bin/node")):
        if cand.is_file() and os.access(cand, os.X_OK):
            return str(cand)
    return None


def sync_viewer() -> None:
    node_bin = _find_node()
    if SYNC_SCRIPT.exists() and node_bin:
        subprocess.run(
            [node_bin, str(SYNC_SCRIPT)],
            cwd=str(ROOT),
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


def regenerate_review_queue(props: list[dict[str, Any]], queue_file: Path) -> None:
    today = datetime.now(timezone.utc).date().isoformat()
    sched = make_scheduler()
    now = datetime.now(timezone.utc)

    due_weak: list[tuple[float, dict[str, Any]]] = []
    upcoming: list[tuple[str, dict[str, Any]]] = []
    misconceptions: list[dict[str, Any]] = []

    for p in props:
        status = p.get("status", "planned")
        retr = 0.0
        if isinstance(p.get("fsrs"), dict):
            try:
                card = fsrs.Card.from_dict(p["fsrs"])
                retr = float(sched.get_card_retrievability(card, now))
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

    lines = [
        "# Spaced Review & Weak-Edge Queue",
        "",
        f"> Automatically maintained via FSRS v6 (`scripts/srs_engine.py`) · Updated `{today}`.",
        '> Ask Syntopik: *"Check my review-queue.md and run a 10-minute interleaved tune-up."*',
        "",
        "## Due / Weak Edges (`misconception`, `shaky`, or Due Today)",
    ]
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

    lines.append("")
    queue_file.parent.mkdir(parents=True, exist_ok=True)
    queue_file.write_text("\n".join(lines), encoding="utf-8")


def bkt_update(prior: float, correct: bool, p_transit: float = 0.25, p_slip: float = 0.10, p_guess: float = 0.20) -> float:
    """Corbett & Anderson Bayesian Knowledge Tracing (BKT) mastery update."""
    p = max(0.01, min(0.99, prior))
    if correct:
        num = p * (1.0 - p_slip)
        den = num + (1.0 - p) * p_guess
    else:
        num = p * p_slip
        den = num + (1.0 - p) * (1.0 - p_guess)
    posterior = num / den if den > 0 else p
    updated = posterior + (1.0 - posterior) * p_transit
    return round(max(0.01, min(0.995, updated)), 3)


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
    # Replace LaTeX commands like \vec, \frac, \text with spaces while keeping inner text.
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

    # Pre-tokenize each proposition and compute corpus document frequency per node (never inflated by aliases).
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
            # Context view (subject + chapter + notes + title + aliases) scaled so primary matches rank higher.
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


def normalize_chain(raw: dict[str, Any]) -> dict[str, Any] | None:
    """Normalize both legacy binary {from, to} and multi-premise {premises, conclusion} chains."""
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
    why = str(raw.get("why") or raw.get("note") or "").strip()
    relation = str(raw.get("relation") or "requires").strip() or "requires"
    chain_key = f"{'+'.join(sorted(premises))}>{conclusion}"
    return {
        "premises": premises,
        "conclusion": conclusion,
        "relation": relation,
        "why": why,
        "chainKey": chain_key,
        "isJoint": len(premises) >= 2,
    }


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
    _kg_dir, props_file, _chains_file, _queue_file, is_default = resolve_kg_paths(getattr(args, "kg_dir", None))
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

    existing = next((c for c in cues if isinstance(c, dict) and str(c.get("id")) == cid), None)
    if existing is not None:
        existing["q"] = args.q
        existing["a"] = args.a
        existing.setdefault("lastRating", None)
        existing.setdefault("reviews", 0)
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
        cues.append(cue_obj)
        action = "added"

    save_jsonl(props_file, props)
    if is_default and not getattr(args, "no_sync", False):
        sync_viewer()

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
    _kg_dir, props_file, _chains_file, queue_file, is_default = resolve_kg_paths(getattr(args, "kg_dir", None))
    props = load_jsonl(props_file)
    target = next((p for p in props if p.get("id") == args.prop_id), None)
    if target is None:
        if not args.title:
            raise SystemExit(f"Proposition '{args.prop_id}' not found. Pass --title (and --subject/--chapter) to create it.")
        target = {
            "id": args.prop_id,
            "subject": args.subject or "General",
            "chapter": args.chapter or "General",
            "title": args.title,
            "kind": args.kind or "derivation",
            "status": "planned",
        }
        props.append(target)

    cue_id = getattr(args, "cue_id", None)
    updated_cue: dict[str, Any] | None = None
    if cue_id:
        cues = target.get("cues")
        if not isinstance(cues, list):
            raise SystemExit(f"Cue '{cue_id}' not found on proposition '{args.prop_id}'. Add cues first with add-cue.")
        updated_cue = next((c for c in cues if isinstance(c, dict) and str(c.get("id")) == str(cue_id)), None)
        if updated_cue is None:
            raise SystemExit(f"Cue '{cue_id}' not found on proposition '{args.prop_id}'.")

    sched = make_scheduler()
    card = fsrs.Card.from_dict(target["fsrs"]) if isinstance(target.get("fsrs"), dict) else fsrs.Card()
    now = datetime.now(timezone.utc)
    rating = RATINGS[args.rating]
    new_card, _log = sched.review_card(card, rating, now)
    card_dict = new_card.to_dict()

    due_date = str(card_dict["due"])[:10]
    today = now.date().isoformat()
    prior_bkt = float(target.get("bktMastery", 0.30))
    bkt_correct = args.rating in ("good", "easy")
    new_bkt = bkt_update(prior_bkt, bkt_correct, p_transit=RATING_TRANSIT[args.rating])

    target["fsrs"] = card_dict
    target["bktMastery"] = new_bkt
    target["confidence"] = new_bkt
    target["lastTested"] = today
    target["nextReview"] = due_date
    if args.rating == "again":
        target["status"] = "misconception" if args.misconception else "shaky"
    elif args.rating == "hard":
        target["status"] = "shaky"
    else:
        target["status"] = "solid"
    if args.notes:
        target["notes"] = args.notes

    if updated_cue is not None:
        updated_cue["lastRating"] = args.rating
        updated_cue["reviews"] = int(updated_cue.get("reviews") or 0) + 1

    save_jsonl(props_file, props)
    regenerate_review_queue(props, queue_file)
    if is_default and not args.no_sync:
        sync_viewer()

    out: dict[str, Any] = {
        "id": target["id"],
        "rating": args.rating,
        "status": target["status"],
        "bktMastery": new_bkt,
        "stabilityDays": round(float(card_dict.get("stability") or 0), 2),
        "difficulty": round(float(card_dict.get("difficulty") or 0), 2),
        "nextReview": due_date,
    }
    if updated_cue is not None:
        out["cueId"] = str(updated_cue["id"])
        out["cueReviews"] = int(updated_cue["reviews"])
    print(json.dumps(out, indent=2))


def cmd_due(args: argparse.Namespace) -> None:
    _kg_dir, props_file, _chains_file, _queue_file, _is_default = resolve_kg_paths(getattr(args, "kg_dir", None))
    props = load_jsonl(props_file)
    today = datetime.now(timezone.utc).date().isoformat()
    sched = make_scheduler()
    now = datetime.now(timezone.utc)
    items = []
    for p in props:
        status = p.get("status", "planned")
        due_date = p.get("nextReview")
        is_due = status in ("misconception", "shaky") or (isinstance(due_date, str) and due_date <= today)
        if not is_due:
            continue
        retr = None
        if isinstance(p.get("fsrs"), dict):
            try:
                retr = round(float(sched.get_card_retrievability(fsrs.Card.from_dict(p["fsrs"]), now)), 3)
            except Exception:
                pass
        items.append({
            "id": p["id"],
            "subject": p.get("subject"),
            "chapter": p.get("chapter"),
            "title": p.get("title"),
            "status": status,
            "bktMastery": p.get("bktMastery"),
            "nextReview": due_date,
            "retrievability": retr,
            "notes": p.get("notes", ""),
        })
    items.sort(key=lambda x: (0 if x["status"] == "misconception" else 1 if x["status"] == "shaky" else 2, x["retrievability"] if x["retrievability"] is not None else 0.5))
    print(json.dumps({"today": today, "dueCount": len(items), "items": items[: args.limit]}, indent=2))


def cmd_stats(args: argparse.Namespace) -> None:
    _kg_dir, props_file, chains_file, _queue_file, _is_default = resolve_kg_paths(getattr(args, "kg_dir", None))
    props = load_jsonl(props_file)
    chains = load_jsonl(chains_file)
    today = datetime.now(timezone.utc).date().isoformat()
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

    print(json.dumps({
        "totalNodes": len(props),
        "totalChains": len(chains),
        "uniqueChains": len(seen_chain_keys),
        "hyperedgeCount": hyperedge_count,
        "totalCues": total_cues,
        "statusCounts": counts,
        "avgBktMastery": round(sum(bkt_vals) / len(bkt_vals), 3) if bkt_vals else None,
        "dueCount": due_count,
    }, indent=2))


def stable_id(seed: str) -> int:
    return int(hashlib.sha256(seed.encode("utf-8")).hexdigest()[:8], 16)


def _extract_media_sides(media_field: Any) -> tuple[list[str], list[str]]:
    """Extract `(front_paths, back_paths)` from a proposition or cue `media` field.

    Accepts:
      - Flat list: `["demos/sn2.png"]` (rendered on Front, and shown on Back via `{{FrontSide}}`)
      - Dict: `{"front": ["..."], "back": ["..."]}` (values may be list or single string)
      - Single string: `"demos/sn2.png"`
      - Dict entries inside lists: `{"path": "demos/sn2.png", "side": "front"|"back"}`
    """
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
    """Resolve absolute or relative media paths against `kg_dir.parent`, `kg_dir`, and `ROOT`."""
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


def cmd_export_anki(args: argparse.Namespace) -> None:
    kg_dir, props_file, chains_file, _queue_file, _is_default = resolve_kg_paths(getattr(args, "kg_dir", None))
    props = load_jsonl(props_file)
    chains = load_jsonl(chains_file)
    if args.subject:
        props = [p for p in props if str(p.get("subject", "")).lower() == args.subject.lower()]

    deck_name = f"Syntopik::{args.subject}" if args.subject else "Syntopik"
    deck = genanki.Deck(stable_id(deck_name), deck_name)
    model = genanki.Model(
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
        css="""
        .card { font-family: -apple-system, BlinkMacSystemFont, "Inter", sans-serif; font-size: 17px; line-height: 1.55; color: #e8edf5; background: #10141c; padding: 20px; }
        .meta { font-size: 12px; color: #94a3b8; margin-bottom: 10px; text-transform: uppercase; letter-spacing: 0.04em; }
        .prompt { font-size: 19px; font-weight: 600; }
        .answer { margin-top: 12px; color: #bbf7d0; }
        .why { margin-top: 14px; padding: 10px 12px; border-left: 3px solid #60a5fa; background: #171e2b; font-size: 14px; color: #cbd5e1; }
        .mlh-media img { max-width: 100%; border-radius: 8px; margin-top: 8px; }
        """,
    )

    collector = _MediaCollector()
    exported = 0
    for p in props:
        pid = p.get("id", "")
        if not pid:
            continue
        why_lines = _format_why_lines(chains, pid)
        base_meta = " · ".join(filter(None, [p.get("subject"), p.get("chapter"), p.get("kind"), p.get("status")]))
        base_tags = [_sanitize_tag(t) for t in [p.get("subject", ""), p.get("chapter", ""), p.get("status", "")] if str(t).strip()]
        valid_cues = [c for c in (p.get("cues") or []) if isinstance(c, dict) and (c.get("q") or c.get("a"))]
        prop_front_raw, prop_back_raw = _extract_media_sides(p.get("media"))

        if valid_cues:
            for idx, cue in enumerate(valid_cues):
                cid = str(cue.get("id") or f"c{idx + 1}")
                cue_front_raw, cue_back_raw = _extract_media_sides(cue.get("media"))
                front_files = collector.collect_side(prop_front_raw + cue_front_raw, kg_dir)
                back_files = collector.collect_side(prop_back_raw + cue_back_raw, kg_dir)

                prompt = str(cue.get("q") or p.get("prompt") or f"State and justify: {p.get('title', pid)}")
                prompt_html = md_math_to_anki_mathjax(prompt) + collector.render_html(front_files)
                answer_parts = [md_math_to_anki_mathjax(cue.get("a") or p.get("title", ""))]
                if back_files:
                    answer_parts.append(collector.render_html(back_files))
                if p.get("notes"):
                    answer_parts.append(f"<br><br><em>Notes / Traps:</em> {md_math_to_anki_mathjax(p['notes'])}")
                cue_meta = f"{base_meta} · cue:{cid}" if base_meta else f"cue:{cid}"
                tags = [*base_tags, f"cue::{_sanitize_tag(cid)}"]
                note = genanki.Note(
                    model=model,
                    fields=[prompt_html, "".join(answer_parts), "<br>".join(why_lines), html.escape(cue_meta)],
                    tags=tags,
                    guid=genanki.guid_for(f"{pid}:{cid}"),
                )
                deck.add_note(note)
                exported += 1
        else:
            front_files = collector.collect_side(prop_front_raw, kg_dir)
            back_files = collector.collect_side(prop_back_raw, kg_dir)

            prompt = p.get("prompt") or f"State and justify: {p.get('title', pid)}"
            prompt_html = md_math_to_anki_mathjax(prompt) + collector.render_html(front_files)
            answer_parts = [md_math_to_anki_mathjax(p.get("title", ""))]
            if back_files:
                answer_parts.append(collector.render_html(back_files))
            if p.get("notes"):
                answer_parts.append(f"<br><br><em>Notes / Traps:</em> {md_math_to_anki_mathjax(p['notes'])}")
            note = genanki.Note(
                model=model,
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

    # Upload all resolved media files to AnkiConnect BEFORE addNote / updateNoteFields.
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
    parser = argparse.ArgumentParser(description="FSRS v6 + BKT Spaced Repetition, Lexical Dedup & Multi-Cue Anki Exporter")
    parser.add_argument("--kg-dir", help="Override knowledge-graph directory (for sandboxed testing)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p1 = sub.add_parser("review", help="Record an FSRS + BKT review outcome (and optional cue rating) and update review-queue.md")
    p1.add_argument("prop_id")
    p1.add_argument("--rating", required=True, choices=["again", "hard", "good", "easy"])
    p1.add_argument("--cue-id", help="Optional cue ID on the proposition whose lastRating & reviews count will be updated")
    p1.add_argument("--title", help="Required only if creating a brand-new proposition")
    p1.add_argument("--subject")
    p1.add_argument("--chapter")
    p1.add_argument("--kind", default="derivation")
    p1.add_argument("--notes")
    p1.add_argument("--misconception", action="store_true")
    p1.add_argument("--no-sync", action="store_true", help="Skip regenerating viewer/dashboard")
    _add_kg_dir_flag(p1)
    p1.set_defaults(func=cmd_review)

    p2 = sub.add_parser("due", help="List due, shaky, and misconception concepts ranked by urgency")
    p2.add_argument("--limit", type=int, default=20)
    _add_kg_dir_flag(p2)
    p2.set_defaults(func=cmd_due)

    p3 = sub.add_parser("export-anki", help="Export Knowledge Graph concepts & chains to an offline MathJax Anki .apkg deck")
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

    p5 = sub.add_parser("stats", help="Show mastery counts, average BKT mastery, cue counts, and due count")
    _add_kg_dir_flag(p5)
    p5.set_defaults(func=cmd_stats)

    p6 = sub.add_parser("find-similar", help="Lexical token + bigram/stem similarity search over propositions.jsonl before adding new nodes")
    p6.add_argument("query", help="Proposed concept title, statement, or keywords to check for duplicates")
    p6.add_argument("--top", type=int, default=5, help="Maximum matches to return (default: 5)")
    p6.add_argument("--threshold", type=float, default=0.25, help="Minimum similarity score in [0, 1] (default: 0.25)")
    p6.add_argument("--json", action="store_true", help="Emit structured JSON output")
    _add_kg_dir_flag(p6)
    p6.set_defaults(func=cmd_find_similar)

    p7 = sub.add_parser("add-cue", help="Append or update a retrieval cue on an existing proposition (sharing its single FSRS-6 schedule)")
    p7.add_argument("prop_id", help="Target proposition ID")
    p7.add_argument("--q", required=True, help="Cue prompt / question")
    p7.add_argument("--a", required=True, help="Cue expected answer / justification")
    p7.add_argument("--cue-id", help="Optional cue ID (defaults to next sequential c1, c2, ...)")
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
