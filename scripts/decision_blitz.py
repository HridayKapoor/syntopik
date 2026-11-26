#!/usr/bin/env python3
"""ShadowBox First-Move & Cue-Recognition Blitz CLI for Syntopik v2.0.

Trains Newman Tier-1 comprehension & principle-selection speed (Chi-Feltovich-
Glaser deep-structure sorting + Klein ShadowBox Cognitive Lens): selects
interleaved lookalike pairs from chapter trackers, knowledge-graph propositions,
and historical mistakes, scores 3 bare ShadowBox fields (Kill-Cue, First Move,
Rejected Decoy — zero full algebra/boilerplate solving), logs decisions to
`knowledge-graph/decisions.jsonl`, and tracks `medianDecisionSec` vs accuracy.

Pure Python 3.12+ standard library only (zero external dependencies).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import statistics
import sys
import tempfile
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# ---------------------------------------------------------------------------
# Safe syntopik.* imports with pure-stdlib fallbacks
# ---------------------------------------------------------------------------

try:
    from syntopik.paths import resolve_chapters_dir as _resolve_chapters_dir
    from syntopik.paths import resolve_kg_dir as _resolve_kg_dir
    from syntopik.paths import resolve_workspace as _resolve_workspace
except Exception:  # pragma: no cover - standalone fallback
    def _resolve_workspace(explicit: Path | str | None = None) -> Path:
        if explicit is not None and str(explicit).strip():
            return Path(explicit).expanduser().resolve()
        env_ws = os.environ.get("SYNT_WORKSPACE", "").strip() or os.environ.get("MLH_WORKSPACE", "").strip()
        if env_ws:
            return Path(env_ws).expanduser().resolve()
        return Path.cwd().resolve()

    def _resolve_kg_dir(workspace: Path | str | None = None, *, explicit: Path | str | None = None) -> Path:
        if explicit is not None and str(explicit).strip():
            return Path(explicit).expanduser().resolve()
        return (_resolve_workspace(workspace) / "knowledge-graph").resolve()

    def _resolve_chapters_dir(workspace: Path | str | None = None, *, explicit: Path | str | None = None) -> Path:
        if explicit is not None and str(explicit).strip():
            return Path(explicit).expanduser().resolve()
        return (_resolve_workspace(workspace) / "chapters").resolve()


try:
    from syntopik.storage import append_jsonl_locked as _append_jsonl_locked
    from syntopik.storage import load_json as _load_json
    from syntopik.storage import load_jsonl as _load_jsonl
    from syntopik.storage import save_json_atomic as _save_json_atomic
except Exception:  # pragma: no cover - standalone fallback
    def _load_json(path: Path | str, default: Any = None) -> Any:
        p = Path(path).expanduser()
        if not p.is_file():
            return default
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return default

    def _save_json_atomic(path: Path | str, data: Any) -> Path:
        target = Path(path).expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(target.parent), prefix=f".{target.name}.", suffix=".tmp")
        tmp_path = Path(tmp)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
            os.replace(tmp_path, target)
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise
        return target

    def _load_jsonl(path: Path | str) -> list[dict[str, Any]]:
        p = Path(path).expanduser()
        if not p.is_file():
            return []
        rows: list[dict[str, Any]] = []
        for ln in p.read_text(encoding="utf-8").splitlines():
            s = ln.strip()
            if not s:
                continue
            try:
                obj = json.loads(s)
            except Exception:
                continue
            if isinstance(obj, dict):
                rows.append(obj)
        return rows

    def _append_jsonl_locked(path: Path | str, rows: dict[str, Any] | list[dict[str, Any]]) -> Path:
        target = Path(path).expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        items = [rows] if isinstance(rows, dict) else list(rows)
        with target.open("a", encoding="utf-8") as fh:
            for it in items:
                fh.write(json.dumps(it, ensure_ascii=False) + "\n")
        return target


try:
    from syntopik.schemas import normalize_chain as _normalize_chain
    from syntopik.schemas import now_iso as _now_iso
    from syntopik.schemas import slugify as _slugify
except Exception:  # pragma: no cover - standalone fallback
    def _now_iso() -> str:
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    def _slugify(text: str, *, default: str = "chapter", max_len: int = 64) -> str:
        norm = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode("ascii")
        slug = re.sub(r"[^a-z0-9]+", "-", norm.lower()).strip("-")
        return (slug[:max_len] if max_len > 0 else slug) or default

    def _normalize_chain(raw: dict[str, Any]) -> dict[str, Any] | None:
        if not isinstance(raw, dict):
            return None
        conc = str(raw.get("conclusion") or raw.get("to") or "").strip()
        if not conc:
            return None
        prems = [str(x).strip() for x in (raw.get("premises") or []) if str(x).strip()] if isinstance(raw.get("premises"), list) else (
            [str(raw.get("from")).strip()] if raw.get("from") else []
        )
        return {"premises": prems, "conclusion": conc} if prems else None


__all__ = [
    "DECISIONS_FILENAME",
    "SESSIONS_FILENAME",
    "resolve_decisions_path",
    "load_decisions",
    "build_blitz",
    "grade_decision",
    "compute_decision_stats",
    "main",
]

DECISIONS_FILENAME = "decisions.jsonl"
SESSIONS_FILENAME = "blitz-sessions.json"

_STOP_WORDS = frozenset(
    (
        "a an the and or of to in on at by for from with as is are was were be been being "
        "it its this that these those which who what when where how does do did can could "
        "should would will may might must not no into over under between through about"
    ).split()
)

# Common surface-feature keywords (Chi-Feltovich-Glaser surface features)
_SURFACE_LEXICON = frozenset(
    (
        "spring block pulley pendulum elevator lift inclined plane wedge collision wall "
        "graph tree grid array matrix window heap stack queue cache lock thread stream "
        "circuit capacitor inductor resistor battery lens mirror gas piston reaction buffer "
        "acid base ring benzene alkene gradient loss layer attention token"
    ).split()
)


def resolve_decisions_path(
    workspace: Path | str | None = None,
    kg_dir: Path | str | None = None,
) -> Path:
    return _resolve_kg_dir(workspace=workspace, explicit=kg_dir) / DECISIONS_FILENAME


def load_decisions(
    workspace: Path | str | None = None,
    kg_dir: Path | str | None = None,
) -> list[dict[str, Any]]:
    return _load_jsonl(resolve_decisions_path(workspace=workspace, kg_dir=kg_dir))


def _tokenize(text: str) -> list[str]:
    norm = unicodedata.normalize("NFKC", str(text or "")).lower()
    raw = re.findall(r"[a-z][a-z0-9_-]{1,}", norm)
    return [t for t in raw if t not in _STOP_WORDS]


def _extract_surface_tags(text: str, *, fallback_subject: str = "") -> list[str]:
    toks = _tokenize(text)
    hits: list[str] = []
    for t in toks:
        if t in _SURFACE_LEXICON and t not in hits:
            hits.append(t)
    if not hits:
        for t in toks:
            if len(t) >= 4 and t not in hits:
                hits.append(t)
            if len(hits) >= 3:
                break
    if not hits and fallback_subject:
        hits.append(fallback_subject.lower())
    return hits[:4]


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _normalize_choice(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip().lower())


def _parse_bool(raw: Any) -> bool:
    if isinstance(raw, bool):
        return raw
    s = str(raw or "").strip().lower()
    if s in ("1", "true", "yes", "y", "hit", "pass", "ok"):
        return True
    if s in ("0", "false", "no", "n", "miss", "fail"):
        return False
    raise ValueError(f"Expected boolean value, got {raw!r}")


def _classify_speed_accuracy_zone(median_sec: float | None, accuracy: float | None) -> str:
    if median_sec is None or accuracy is None:
        return "insufficient_data"
    fast = median_sec <= 25.0
    accurate = accuracy >= 0.80
    if fast and accurate:
        return "expert_fluent"
    if (not fast) and accurate:
        return "deliberate_accurate"
    if fast and (not accurate):
        return "rushed_surface_sort"
    return "building_recognition"


def _shorten_claim(text: str, max_len: int = 96) -> str:
    clean = re.sub(r"\s+", " ", str(text or "").strip())
    if len(clean) <= max_len:
        return clean
    return clean[: max_len - 1].rstrip(" ,;:-") + "…"


# ---------------------------------------------------------------------------
# Public API: build_blitz, grade_decision, compute_decision_stats
# ---------------------------------------------------------------------------


def build_blitz(
    *,
    slug: str | None = None,
    n: int = 6,
    subject: str | None = None,
    seed: int | None = None,
    include_key: bool = False,
    workspace: Path | str | None = None,
    kg_dir: Path | str | None = None,
    chapters_dir: Path | str | None = None,
) -> dict[str, Any]:
    """Build an interleaved lookalike ShadowBox First-Move Blitz (default 6 items)."""
    ws = _resolve_workspace(workspace)
    kdir = _resolve_kg_dir(workspace=ws, explicit=kg_dir)
    cdir = _resolve_chapters_dir(workspace=ws, explicit=chapters_dir)

    props = _load_jsonl(kdir / "propositions.jsonl")
    chains = _load_jsonl(kdir / "chains.jsonl")
    mistakes = _load_jsonl(kdir / "mistakes.jsonl")

    tracker: dict[str, Any] | None = None
    if slug:
        t_path = cdir / f"{_slugify(slug)}-tracker.json"
        if t_path.is_file():
            tracker = _load_json(t_path, default=None)

    # Index prerequisites per proposition so confusable pairs prefer divergent premises
    prereqs_by_prop: dict[str, set[str]] = {}
    for c in chains:
        nc = _normalize_chain(c)
        if nc:
            prereqs_by_prop.setdefault(nc["conclusion"], set()).update(nc["premises"])

    mistakes_by_prop: dict[str, list[dict[str, Any]]] = {}
    for m in mistakes:
        pid = str(m.get("propId") or "").strip()
        if pid:
            mistakes_by_prop.setdefault(pid, []).append(m)

    # Filter candidate propositions matching slug / tracker / subject
    tracker_prop_ids: set[str] = set()
    if isinstance(tracker, dict):
        for t in tracker.get("topics") or []:
            if isinstance(t, dict):
                tracker_prop_ids.update(str(pid) for pid in (t.get("propIds") or []) if str(pid).strip())

    slug_low = str(slug or "").strip().lower()
    subj_low = str(subject or (tracker or {}).get("subject") or "").strip().lower()

    candidates: list[dict[str, Any]] = []
    for p in props:
        if not isinstance(p, dict) or not p.get("id"):
            continue
        pid = str(p["id"])
        p_chap = str(p.get("chapter") or "").lower()
        p_subj = str(p.get("subject") or "").lower()
        if slug_low:
            if (
                pid in tracker_prop_ids
                or slug_low in p_chap
                or slug_low in pid.lower()
                or (tracker and str(tracker.get("title") or "").lower()[:12] in p_chap)
            ):
                candidates.append(p)
        elif subj_low:
            if p_subj == subj_low:
                candidates.append(p)
        else:
            candidates.append(p)

    # If chapter filter yielded fewer than requested, top up from same subject / full KG
    if len(candidates) < max(2, int(n)):
        seen_ids = {str(p["id"]) for p in candidates}
        for p in props:
            if not isinstance(p, dict) or not p.get("id") or str(p["id"]) in seen_ids:
                continue
            if subj_low and str(p.get("subject") or "").lower() != subj_low:
                continue
            candidates.append(p)
            seen_ids.add(str(p["id"]))
        if len(candidates) < max(2, int(n)):
            for p in props:
                if isinstance(p, dict) and p.get("id") and str(p["id"]) not in seen_ids:
                    candidates.append(p)
                    seen_ids.add(str(p["id"]))

    # Also mine candidate prompts from tracker questions / topics if KG is sparse
    pool_entries: list[dict[str, Any]] = []
    for p in candidates:
        pid = str(p["id"])
        title = str(p.get("title") or pid)
        cues = [c for c in (p.get("cues") or []) if isinstance(c, dict) and c.get("q")]
        m_list = mistakes_by_prop.get(pid, [])
        m_top = m_list[0] if m_list else None

        prompt = (
            str(cues[0]["q"])
            if cues
            else (
                f"Scenario involving {m_top.get('triggerCue')}: identify the decisive cue, governing first move, and trap decoy."
                if m_top and m_top.get("triggerCue")
                else f"Problem testing: {title}. Select the decisive cue, first move, and decoy to reject."
            )
        )
        kill_cue = (
            _shorten_claim(str(m_top.get("triggerCue")))
            if m_top and m_top.get("triggerCue")
            else _shorten_claim(f"Constraint distinguishing {title}")
        )
        first_move = (
            _shorten_claim(str(m_top.get("correctMove")))
            if m_top and m_top.get("correctMove")
            else _shorten_claim(str(cues[0]["a"]) if cues and cues[0].get("a") else title)
        )
        decoy = (
            _shorten_claim(str(m_top.get("wrongMove")))
            if m_top and m_top.get("wrongMove")
            else _shorten_claim(f"Apply surface shortcut ignoring boundary conditions of {title}")
        )
        surface_tags = _extract_surface_tags(
            f"{prompt} {title} {p.get('chapter', '')}",
            fallback_subject=str(p.get("subject") or "general"),
        )
        pool_entries.append(
            {
                "propId": pid,
                "questionRef": str((m_top or {}).get("sourceRef") or f"{slug or 'kg'}:{pid}"),
                "prompt": prompt,
                "surfaceTags": surface_tags,
                "deepPrinciple": _shorten_claim(title, 88),
                "killCue": kill_cue,
                "firstMove": first_move,
                "rejectedDecoy": decoy,
                "status": str(p.get("status") or "planned"),
                "hasMistake": bool(m_list),
                "tokens": set(_tokenize(f"{prompt} {title} {p.get('chapter', '')}")),
                "prereqs": prereqs_by_prop.get(pid, set()),
            }
        )

    # Supplement with tracker topics when fewer than `n` entries exist
    if isinstance(tracker, dict) and len(pool_entries) < int(n):
        existing_refs = {e["questionRef"] for e in pool_entries}
        for t in tracker.get("topics") or []:
            if not isinstance(t, dict):
                continue
            tid = str(t.get("id") or "")
            ref = f"{tracker.get('slug', slug)}:{tid}"
            if ref in existing_refs:
                continue
            t_title = str(t.get("title") or tid)
            t_notes = str(t.get("notes") or "")
            pool_entries.append(
                {
                    "propId": (t.get("propIds") or [f"topic-{tid}"])[0],
                    "questionRef": ref,
                    "prompt": f"Identify governing model for: {t_title}",
                    "surfaceTags": _extract_surface_tags(f"{t_title} {t_notes}", fallback_subject=str(tracker.get("subject") or "general")),
                    "deepPrinciple": _shorten_claim(t_title, 88),
                    "killCue": _shorten_claim(f"Primary invariant in {t_title}"),
                    "firstMove": _shorten_claim(t_notes.split("|")[0].strip() if t_notes else t_title),
                    "rejectedDecoy": _shorten_claim(f"Sort by surface diagram instead of {t_title} invariant"),
                    "status": "planned",
                    "hasMistake": False,
                    "tokens": set(_tokenize(f"{t_title} {t_notes}")),
                    "prereqs": set(),
                }
            )
            existing_refs.add(ref)
            if len(pool_entries) >= int(n) * 2:
                break

    if not pool_entries:
        raise ValueError("No propositions or tracker topics found to construct a Decision Blitz.")

    # Pair confusable lookalikes (high surface/lexical overlap + distinct propId / divergent premises)
    rng = random.Random(seed if seed is not None else int(hashlib.sha256(f"{slug}|{len(pool_entries)}".encode()).hexdigest()[:8], 16))
    # Prioritize shaky/misconception & mistake-backed nodes as pair anchors
    pool_entries.sort(
        key=lambda e: (
            0 if e["hasMistake"] else 1,
            0 if e["status"] in ("misconception", "shaky") else 1,
            e["propId"],
        )
    )

    target_n = max(1, int(n))
    ordered_items: list[tuple[int, dict[str, Any]]] = []
    used_indices: set[int] = set()
    pair_group = 0

    for i, anchor in enumerate(pool_entries):
        if len(ordered_items) >= target_n:
            break
        if i in used_indices:
            continue
        used_indices.add(i)
        pair_group += 1
        ordered_items.append((pair_group, anchor))
        if len(ordered_items) >= target_n:
            break

        # Find best confusable partner (high surface overlap, different deep principle / premises)
        best_j: int | None = None
        best_conf = -1.0
        for j, cand in enumerate(pool_entries):
            if j in used_indices:
                continue
            surf_sim = _jaccard(set(anchor["surfaceTags"]), set(cand["surfaceTags"]))
            lex_sim = _jaccard(anchor["tokens"], cand["tokens"])
            prem_div = 1.0 - _jaccard(anchor["prereqs"], cand["prereqs"]) if (anchor["prereqs"] or cand["prereqs"]) else 0.7
            diff_prop = 1.0 if cand["propId"] != anchor["propId"] else 0.1
            conf_score = (0.5 * surf_sim + 0.3 * lex_sim + 0.2 * prem_div) * diff_prop
            if conf_score > best_conf:
                best_conf = conf_score
                best_j = j
        if best_j is not None:
            used_indices.add(best_j)
            ordered_items.append((pair_group, pool_entries[best_j]))

    # Build distractors from peer items so every ShadowBox row has bare matched options + "I don't know"
    all_kill_cues = [e["killCue"] for _, e in ordered_items]
    all_first_moves = [e["firstMove"] for _, e in ordered_items]
    all_decoys = [e["rejectedDecoy"] for _, e in ordered_items]

    def _bare_choices(correct_val: str, pool_vals: list[str], fallback_a: str, fallback_b: str) -> list[str]:
        others = [v for v in dict.fromkeys(pool_vals) if _normalize_choice(v) != _normalize_choice(correct_val)]
        rng.shuffle(others)
        picks = [correct_val] + others[:2]
        for fb in (fallback_a, fallback_b):
            if len(picks) >= 3:
                break
            if _normalize_choice(fb) not in {_normalize_choice(x) for x in picks}:
                picks.append(fb)
        rng.shuffle(picks)
        picks.append("I don't know")
        return picks

    blitz_id = f"blitz-{hashlib.sha256(f'{slug}|{_now_iso()}|{target_n}'.encode()).hexdigest()[:8]}"
    public_items: list[dict[str, Any]] = []
    sealed_key: dict[str, dict[str, Any]] = {}

    for idx, (pg, entry) in enumerate(ordered_items, start=1):
        item_id = f"b{idx}"
        kc_opts = _bare_choices(
            entry["killCue"],
            all_kill_cues,
            "No boundary constraint changes the default formula",
            "Only initial magnitude matters; sign/direction is irrelevant",
        )
        fm_opts = _bare_choices(
            entry["firstMove"],
            all_first_moves,
            "Apply direct linear scaling from the surface diagram",
            "Substitute equilibrium values directly without checking regime",
        )
        dc_opts = _bare_choices(
            entry["rejectedDecoy"],
            all_decoys,
            "Assume symmetry halves the interval automatically",
            "Treat local constraint as globally invariant",
        )
        item_public: dict[str, Any] = {
            "itemId": item_id,
            "pairGroup": pg,
            "propId": entry["propId"],
            "questionRef": entry["questionRef"],
            "prompt": entry["prompt"],
            "surfaceTags": entry["surfaceTags"],
            "choices": {
                "killCue": kc_opts,
                "firstMove": fm_opts,
                "rejectedDecoy": dc_opts,
            },
        }
        lens = {
            "propId": entry["propId"],
            "questionRef": entry["questionRef"],
            "surfaceTags": entry["surfaceTags"],
            "deepPrinciple": entry["deepPrinciple"],
            "killCue": entry["killCue"],
            "firstMove": entry["firstMove"],
            "rejectedDecoy": entry["rejectedDecoy"],
        }
        if include_key:
            item_public["expertLens"] = lens
        public_items.append(item_public)
        sealed_key[item_id] = lens

    # Persist sealed session key for leak-free grading by blitz_id + item_id
    sessions_path = kdir / SESSIONS_FILENAME
    sessions = _load_json(sessions_path, default={})
    if not isinstance(sessions, dict):
        sessions = {}
    sessions[blitz_id] = {
        "blitzId": blitz_id,
        "slug": slug,
        "createdAt": _now_iso(),
        "items": sealed_key,
    }
    # Keep last 50 sessions bounded
    if len(sessions) > 50:
        oldest_keys = sorted(sessions.keys(), key=lambda k: str(sessions[k].get("createdAt") or ""))[:-50]
        for k in oldest_keys:
            sessions.pop(k, None)
    _save_json_atomic(sessions_path, sessions)

    return {
        "ok": True,
        "blitzId": blitz_id,
        "slug": slug,
        "targetSeconds": len(public_items) * 40,
        "itemCount": len(public_items),
        "rule": "Zero solving allowed. Pick Kill-Cue, First Move, and Rejected Decoy in <= 25s per item.",
        "items": public_items,
    }


def grade_decision(
    *,
    prop_id: str | None = None,
    question_ref: str | None = None,
    kill_cue_hit: bool | None = None,
    first_move_hit: bool | None = None,
    decoy_hit: bool | None = None,
    decision_ms: int = 0,
    surface_tags: list[str] | None = None,
    deep_principle: str = "",
    blitz_id: str | None = None,
    item_id: str | None = None,
    kill_cue_answer: str | None = None,
    first_move_answer: str | None = None,
    rejected_decoy_answer: str | None = None,
    workspace: Path | str | None = None,
    kg_dir: Path | str | None = None,
) -> dict[str, Any]:
    """Score a ShadowBox decision (3 fields), append to `decisions.jsonl`, and emit Newman Tier-1 metrics."""
    kdir = _resolve_kg_dir(workspace=workspace, explicit=kg_dir)
    lens: dict[str, Any] | None = None

    if blitz_id and item_id:
        sessions = _load_json(kdir / SESSIONS_FILENAME, default={})
        sess = sessions.get(str(blitz_id)) if isinstance(sessions, dict) else None
        if isinstance(sess, dict) and isinstance(sess.get("items"), dict):
            lens = sess["items"].get(str(item_id))
        if lens is None:
            raise KeyError(f"Blitz item '{blitz_id}/{item_id}' not found in {SESSIONS_FILENAME}.")

    eff_prop_id = str(prop_id or (lens or {}).get("propId") or "").strip()
    eff_qref = str(question_ref or (lens or {}).get("questionRef") or eff_prop_id or "blitz-item").strip()
    eff_tags = list(surface_tags) if surface_tags is not None else list((lens or {}).get("surfaceTags") or [])
    eff_principle = str(deep_principle or (lens or {}).get("deepPrinciple") or "").strip()

    if lens is not None:
        kc_hit = (
            bool(kill_cue_hit)
            if kill_cue_hit is not None
            else _normalize_choice(kill_cue_answer or "") == _normalize_choice(lens.get("killCue") or "")
        )
        fm_hit = (
            bool(first_move_hit)
            if first_move_hit is not None
            else _normalize_choice(first_move_answer or "") == _normalize_choice(lens.get("firstMove") or "")
        )
        dc_hit = (
            bool(decoy_hit)
            if decoy_hit is not None
            else _normalize_choice(rejected_decoy_answer or "") == _normalize_choice(lens.get("rejectedDecoy") or "")
        )
    else:
        if kill_cue_hit is None or first_move_hit is None or decoy_hit is None:
            raise ValueError(
                "Provide either (--blitz-id and --item-id with learner answers) "
                "or explicit --kill-cue-hit, --first-move-hit, and --decoy-hit."
            )
        kc_hit = bool(kill_cue_hit)
        fm_hit = bool(first_move_hit)
        dc_hit = bool(decoy_hit)

    eff_ms = max(0, int(decision_ms))
    fields_hit = int(kc_hit) + int(fm_hit) + int(dc_hit)
    item_acc = round(fields_hit / 3.0, 4)

    record: dict[str, Any] = {
        "ts": _now_iso(),
        "blitzId": str(blitz_id) if blitz_id else None,
        "itemId": str(item_id) if item_id else None,
        "propId": eff_prop_id or None,
        "questionRef": eff_qref,
        "killCueHit": kc_hit,
        "firstMoveHit": fm_hit,
        "decoyHit": dc_hit,
        "decisionMs": eff_ms,
        "surfaceTags": eff_tags,
        "deepPrinciple": eff_principle,
    }
    decisions_path = resolve_decisions_path(workspace=workspace, kg_dir=kg_dir)
    _append_jsonl_locked(decisions_path, record)

    try:
        from syntopik.learner_model import record_telemetry_event

        dom = "General"
        if eff_prop_id:
            for p_row in _load_jsonl(kdir / "propositions.jsonl"):
                if str(p_row.get("id") or "") == eff_prop_id:
                    dom = str(p_row.get("subject") or "General")
                    break
        record_telemetry_event(
            kdir,
            domain=dom,
            prop_id=eff_prop_id or None,
            kind="decision_blitz",
            correct=(fields_hit == 3),
            difficulty=1480,
            confidence=None,
            duration_ms=eff_ms if eff_ms > 0 else None,
            hints_used=0,
            mistake_category="condition_missed" if not dc_hit else (None if fields_hit == 3 else "concept_gap"),
            pf_outcome=None,
            skipped=False,
        )
    except Exception:
        pass

    live_stats = compute_decision_stats(workspace=workspace, kg_dir=kg_dir)

    expert_diff: dict[str, Any] | None = None
    if lens is not None:
        expert_diff = {
            "deepPrinciple": lens.get("deepPrinciple"),
            "killCue": {"learner": kill_cue_answer, "expert": lens.get("killCue"), "hit": kc_hit},
            "firstMove": {"learner": first_move_answer, "expert": lens.get("firstMove"), "hit": fm_hit},
            "rejectedDecoy": {"learner": rejected_decoy_answer, "expert": lens.get("rejectedDecoy"), "hit": dc_hit},
        }

    return {
        "ok": True,
        "path": str(decisions_path),
        "record": record,
        "fieldsHit": fields_hit,
        "itemAccuracy": item_acc,
        "decisionSec": round(eff_ms / 1000.0, 2),
        "rejectedDecoyCorrect": dc_hit,
        "fellForDecoy": not dc_hit,
        "expertLensDiff": expert_diff,
        "newmanTier1": {
            "totalDecisions": live_stats["totalDecisions"],
            "medianDecisionSec": live_stats["medianDecisionSec"],
            "overallAccuracy": live_stats["overallAccuracy"],
            "speedAccuracyZone": live_stats["speedAccuracyZone"],
        },
    }


def compute_decision_stats(
    *,
    workspace: Path | str | None = None,
    kg_dir: Path | str | None = None,
    prop_id: str | None = None,
    days: int | None = None,
) -> dict[str, Any]:
    """Aggregate Newman Tier-1 recognition latency (`medianDecisionSec`) vs accuracy."""
    rows = load_decisions(workspace=workspace, kg_dir=kg_dir)
    prop_f = str(prop_id or "").strip()
    cutoff_iso: str | None = None
    if days is not None and int(days) > 0:
        cutoff_iso = (datetime.now(timezone.utc) - timedelta(days=int(days))).isoformat()

    filtered: list[dict[str, Any]] = []
    for r in rows:
        if prop_f and str(r.get("propId") or "") != prop_f:
            continue
        if cutoff_iso and str(r.get("ts") or "") < cutoff_iso:
            continue
        filtered.append(r)

    n = len(filtered)
    if n == 0:
        return {
            "ok": True,
            "totalDecisions": 0,
            "medianDecisionSec": None,
            "meanDecisionSec": None,
            "overallAccuracy": None,
            "killCueAccuracy": None,
            "firstMoveAccuracy": None,
            "decoyAccuracy": None,
            "speedAccuracyZone": "insufficient_data",
            "bySurfaceTag": {},
            "weakestPrinciples": [],
        }

    lat_secs = [max(0.0, float(r.get("decisionMs") or 0) / 1000.0) for r in filtered]
    kc_hits = [1.0 if r.get("killCueHit") else 0.0 for r in filtered]
    fm_hits = [1.0 if r.get("firstMoveHit") else 0.0 for r in filtered]
    dc_hits = [1.0 if r.get("decoyHit") else 0.0 for r in filtered]
    item_accs = [(kc + fm + dc) / 3.0 for kc, fm, dc in zip(kc_hits, fm_hits, dc_hits)]

    med_sec = round(float(statistics.median(lat_secs)), 2)
    mean_sec = round(sum(lat_secs) / n, 2)
    overall_acc = round(sum(item_accs) / n, 4)

    tag_buckets: dict[str, list[tuple[float, float]]] = {}
    prin_buckets: dict[str, list[float]] = {}
    for r, acc, sec in zip(filtered, item_accs, lat_secs):
        for tag in (r.get("surfaceTags") or []):
            t = str(tag).strip().lower()
            if t:
                tag_buckets.setdefault(t, []).append((acc, sec))
        prin = str(r.get("deepPrinciple") or r.get("propId") or "").strip()
        if prin:
            prin_buckets.setdefault(prin, []).append(acc)

    by_surface = {
        tag: {
            "count": len(vals),
            "accuracy": round(sum(a for a, _ in vals) / len(vals), 4),
            "medianDecisionSec": round(float(statistics.median(s for _, s in vals)), 2),
        }
        for tag, vals in sorted(tag_buckets.items())
    }
    weakest = sorted(
        (
            {"deepPrinciple": p, "count": len(accs), "accuracy": round(sum(accs) / len(accs), 4)}
            for p, accs in prin_buckets.items()
        ),
        key=lambda x: (x["accuracy"], -x["count"], x["deepPrinciple"]),
    )[:5]

    return {
        "ok": True,
        "totalDecisions": n,
        "medianDecisionSec": med_sec,
        "meanDecisionSec": mean_sec,
        "overallAccuracy": overall_acc,
        "killCueAccuracy": round(sum(kc_hits) / n, 4),
        "firstMoveAccuracy": round(sum(fm_hits) / n, 4),
        "decoyAccuracy": round(sum(dc_hits) / n, 4),
        "speedAccuracyZone": _classify_speed_accuracy_zone(med_sec, overall_acc),
        "bySurfaceTag": by_surface,
        "weakestPrinciples": weakest,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--workspace", type=Path, default=argparse.SUPPRESS, help="Workspace root directory.")
    common.add_argument("--kg-dir", type=Path, default=argparse.SUPPRESS, help="Override knowledge-graph/ directory.")
    common.add_argument("--chapters-dir", type=Path, default=argparse.SUPPRESS, help="Override chapters/ directory.")
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="Emit JSON output.")

    parser = argparse.ArgumentParser(
        prog="decision_blitz.py",
        description="ShadowBox First-Move & Cue-Recognition Blitz CLI (Newman Tier-1).",
        parents=[common],
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_bld = sub.add_parser("build", parents=[common], help="Build an interleaved lookalike First-Move Blitz.")
    p_bld.add_argument("--slug", default=None, help="Chapter slug (e.g. shm, raft).")
    p_bld.add_argument("--subject", default=None, help="Filter by subject.")
    p_bld.add_argument("--n", type=int, default=6, help="Number of interleaved blitz items (default: 6).")
    p_bld.add_argument("--seed", type=int, default=None, help="Deterministic shuffle seed.")
    p_bld.add_argument("--include-key", action="store_true", help="Include expertLens answer key in output.")

    p_grd = sub.add_parser("grade", parents=[common], help="Grade ShadowBox answers and log to decisions.jsonl.")
    p_grd.add_argument("--blitz-id", default=None, help="Session blitzId returned by `build`.")
    p_grd.add_argument("--item-id", default=None, help="Item ID within blitz (e.g. b1).")
    p_grd.add_argument("--kill-cue", default=None, help="Learner's selected Kill-Cue choice text.")
    p_grd.add_argument("--first-move", default=None, help="Learner's selected First Move choice text.")
    p_grd.add_argument("--rejected-decoy", default=None, help="Learner's selected Rejected Decoy choice text.")
    p_grd.add_argument("--prop-id", default=None, help="Proposition ID (for direct grading).")
    p_grd.add_argument("--question-ref", default=None, help="Question reference ID.")
    p_grd.add_argument("--kill-cue-hit", default=None, help="Explicit boolean hit for Kill-Cue (true/false).")
    p_grd.add_argument("--first-move-hit", default=None, help="Explicit boolean hit for First Move (true/false).")
    p_grd.add_argument("--decoy-hit", default=None, help="Explicit boolean hit for Rejected Decoy (true/false).")
    p_grd.add_argument("--decision-ms", type=int, default=0, help="Decision latency in milliseconds.")
    p_grd.add_argument("--surface-tags", default=None, help="Comma-separated surface tags.")
    p_grd.add_argument("--deep-principle", default="", help="Deep governing principle label.")

    p_st = sub.add_parser("stats", parents=[common], help="Report Newman Tier-1 latency vs accuracy analytics.")
    p_st.add_argument("--prop-id", default=None, help="Filter by proposition ID.")
    p_st.add_argument("--days", type=int, default=None, help="Filter to last N days.")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    ws = getattr(args, "workspace", None)
    kg_dir = getattr(args, "kg_dir", None)
    ch_dir = getattr(args, "chapters_dir", None)
    as_json = bool(getattr(args, "json", False))

    try:
        if args.command == "build":
            res = build_blitz(
                slug=args.slug,
                n=args.n,
                subject=args.subject,
                seed=args.seed,
                include_key=args.include_key,
                workspace=ws,
                kg_dir=kg_dir,
                chapters_dir=ch_dir,
            )
        elif args.command == "grade":
            tags = [t.strip() for t in args.surface_tags.split(",") if t.strip()] if args.surface_tags else None
            res = grade_decision(
                prop_id=args.prop_id,
                question_ref=args.question_ref,
                kill_cue_hit=_parse_bool(args.kill_cue_hit) if args.kill_cue_hit is not None else None,
                first_move_hit=_parse_bool(args.first_move_hit) if args.first_move_hit is not None else None,
                decoy_hit=_parse_bool(args.decoy_hit) if args.decoy_hit is not None else None,
                decision_ms=args.decision_ms,
                surface_tags=tags,
                deep_principle=args.deep_principle,
                blitz_id=args.blitz_id,
                item_id=args.item_id,
                kill_cue_answer=args.kill_cue,
                first_move_answer=args.first_move,
                rejected_decoy_answer=args.rejected_decoy,
                workspace=ws,
                kg_dir=kg_dir,
            )
        elif args.command == "stats":
            res = compute_decision_stats(
                workspace=ws,
                kg_dir=kg_dir,
                prop_id=args.prop_id,
                days=args.days,
            )
        else:
            parser.error(f"Unknown command: {args.command}")
            return 2
    except Exception as exc:  # noqa: BLE001
        res = {"ok": False, "command": args.command, "error": str(exc)}

    if as_json:
        print(json.dumps(res, indent=2, ensure_ascii=False))
    else:
        if not res.get("ok"):
            print(f"ERROR: {res.get('error', 'Failed')}", file=sys.stderr)
        elif args.command == "build":
            print(f"Decision Blitz {res['blitzId']} ({res['itemCount']} items · target {res['targetSeconds']}s):")
            for it in res["items"]:
                print(f"  [{it['itemId']} · pair {it['pairGroup']}] {it['propId']} — {it['prompt']}")
        elif args.command == "grade":
            nt = res["newmanTier1"]
            print(
                f"Graded {res['record']['questionRef']}: {res['fieldsHit']}/3 ({round(res['itemAccuracy']*100)}%) "
                f"in {res['decisionSec']}s · median={nt['medianDecisionSec']}s · zone={nt['speedAccuracyZone']}"
            )
        elif args.command == "stats":
            print(
                f"Newman Tier-1 ({res['totalDecisions']} decisions): median={res['medianDecisionSec']}s · "
                f"acc={res['overallAccuracy']} · zone={res['speedAccuracyZone']}"
            )
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
