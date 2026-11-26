"""Syntopik v3.2 — Exam-Day Readiness Forecaster, Pre-Exam Taper Mode & Pre-Mortem Shield.

Pure Python 3.12+ standard library only (zero external imports).
Provides:
  1. `detect_taper_mode(kg_dir, *, now=None, exam_date=None)`: O(1) check reading
     `study-config.json` (`examDate` / `exams[]`) to auto-activate Pre-Exam Taper
     Mode inside `<= 72h` (`taper_72h` and `final_24h` with `allowNewConcepts=False`).
  2. `build_premortem_shield(workspace=None, *, subject=None, chapters=None, limit=3, kg_dir=None)`:
     STRICT NON-FABRICATION Gollwitzer If-Then Failure Shield (max 3 bullets) built
     exclusively from real `forks.jsonl`, `mistakes.jsonl`, `learner-profile.json`
     (`failureDna`), and `exam-attempts.jsonl` (returns `bullets: []`,
     `sourceBasis: "cold_start_minimal"` when 0 logs exist).
  3. `compute_readiness(...)`: deterministic 5-pillar readiness index (`0..100`)
     fusing FSRS-6 exam-day retention projection `R(t_exam, S)`, 4-tier Evidence
     depth, Blunder Fork & trap clearance, timed EV-skip discipline, and syllabus
     completion, plus Bayesian-shrunk score band and top-3 ROI study levers.
"""

from __future__ import annotations

import math
import re
from collections import Counter, deque
from datetime import date as date_cls, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from syntopik.evidence import compute_evidence_summary
from syntopik.forks import list_due_forks
from syntopik.fsrs_core import (
    card_retrievability,
    compute_calibration,
    forgetting_curve,
    load_fsrs_parameters,
)
from syntopik.interleave import get_interleave_stats
from syntopik.learner_model import load_profile
from syntopik.paths import resolve_chapters_dir, resolve_kg_dir, resolve_workspace
from syntopik.schemas import normalize_chain, normalize_subject
from syntopik.storage import load_json, load_jsonl

__all__ = [
    "detect_taper_mode",
    "build_premortem_shield",
    "compute_readiness",
]

_NON_CAUSAL_RELATIONS: frozenset[str] = frozenset(
    {"isomorphic_to", "contrasts_with", "contrasts", "contrast", "analogous", "contradicts", "unrelated"}
)

_TIER_WEIGHT: dict[int, float] = {
    1: 0.25,
    2: 0.60,
    3: 0.88,
    4: 1.00,
}


# ---------------------------------------------------------------------------
# Internal Helpers
# ---------------------------------------------------------------------------


def _clip_str(text: str, max_chars: int = 120) -> str:
    s = re.sub(r"\s+", " ", str(text or "").strip())
    if len(s) <= max_chars:
        return s
    cut = s[: max(8, max_chars - 3)].rstrip()
    if " " in cut and len(cut) > 16:
        cut = cut.rsplit(" ", 1)[0].rstrip(" ,;:-")
    return cut + "..."


def _clamp(val: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(val)))


def _resolve_kg_only(kg_dir_or_ws: Path | str | None) -> Path:
    if kg_dir_or_ws is None or not str(kg_dir_or_ws).strip():
        return resolve_kg_dir()
    p = Path(kg_dir_or_ws).expanduser().resolve()
    if p.name == "knowledge-graph":
        return p
    if (p / "knowledge-graph").is_dir():
        return (p / "knowledge-graph").resolve()
    if any((p / fn).is_file() for fn in ("study-config.json", "exam-config.json", "propositions.jsonl", "mistakes.jsonl")):
        return p
    return (p / "knowledge-graph").resolve()


def _resolve_paths(
    workspace: Path | str | None = None,
    kg_dir: Path | str | None = None,
    chapters_dir: Path | str | None = None,
) -> tuple[Path, Path, Path]:
    if workspace is not None and str(workspace).strip():
        ws = resolve_workspace(workspace)
        kg = resolve_kg_dir(ws, explicit=kg_dir)
        ch = resolve_chapters_dir(ws, explicit=chapters_dir)
        return ws, kg, ch
    if kg_dir is not None and str(kg_dir).strip():
        kg = Path(kg_dir).expanduser().resolve()
        ws = kg.parent if kg.name == "knowledge-graph" else kg.parent
        ch = Path(chapters_dir).expanduser().resolve() if chapters_dir else (ws / "chapters").resolve()
        return ws, kg, ch
    ws = resolve_workspace(None)
    kg = resolve_kg_dir(ws)
    ch = resolve_chapters_dir(ws, explicit=chapters_dir)
    return ws, kg, ch


def _parse_dt(value: Any, *, end_of_day_if_date_only: bool = False, default_hour: int = 9) -> tuple[datetime | None, bool]:
    """Parse ISO datetime or `YYYY-MM-DD` date; returns `(utc_dt, is_date_only)`."""
    if isinstance(value, datetime):
        dt = value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return dt, False
    if isinstance(value, date_cls):
        hr = 23 if end_of_day_if_date_only else default_hour
        mn = 59 if end_of_day_if_date_only else 0
        return datetime(value.year, value.month, value.day, hr, mn, 0, tzinfo=timezone.utc), True
    if not isinstance(value, str) or not value.strip():
        return None, False
    raw = value.strip()
    is_date_only = len(raw) == 10 and bool(re.match(r"^\d{4}-\d{2}-\d{2}$", raw))
    if is_date_only:
        try:
            d = date_cls.fromisoformat(raw)
            hr = 23 if end_of_day_if_date_only else default_hour
            mn = 59 if end_of_day_if_date_only else 0
            return datetime(d.year, d.month, d.day, hr, mn, 0, tzinfo=timezone.utc), True
        except ValueError:
            return None, False
    norm = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    try:
        dt = datetime.fromisoformat(norm)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc), False
    except ValueError:
        try:
            d = date_cls.fromisoformat(raw[:10])
            hr = 23 if end_of_day_if_date_only else default_hour
            mn = 59 if end_of_day_if_date_only else 0
            return datetime(d.year, d.month, d.day, hr, mn, 0, tzinfo=timezone.utc), True
        except ValueError:
            return None, False


def _load_study_config(kg_dir: Path) -> tuple[dict[str, Any], str | None]:
    for fname in ("study-config.json", "exam-config.json"):
        p = kg_dir / fname
        if p.is_file():
            data = load_json(p, default=None)
            if isinstance(data, dict):
                return data, fname
    return {}, None


def _norm_subject_low(subj: str | None) -> str | None:
    if not subj or not str(subj).strip():
        return None
    try:
        return normalize_subject(str(subj)).lower()
    except ValueError:
        return str(subj).strip().lower()


def _subject_matches(item_subj: str | None, filter_low: str | None) -> bool:
    if not filter_low:
        return True
    norm = _norm_subject_low(item_subj)
    return norm == filter_low or str(item_subj or "").strip().lower() == filter_low


def _extract_candidate_exams(
    cfg: dict[str, Any],
    cfg_source: str | None,
    *,
    explicit_exam_date: Any = None,
    subject_low: str | None = None,
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []

    if explicit_exam_date is not None and str(explicit_exam_date).strip():
        candidates.append(
            {
                "rawDate": explicit_exam_date,
                "name": str(cfg.get("targetExam") or "Target Exam"),
                "subject": subject_low,
                "source": "explicit_arg",
            }
        )
        return candidates

    raw_exams = cfg.get("exams")
    if isinstance(raw_exams, list):
        for ex in raw_exams:
            if not isinstance(ex, dict):
                continue
            raw_d = ex.get("examDate") or ex.get("date") or ex.get("targetDate")
            if not raw_d:
                continue
            ex_subj = ex.get("subject")
            if subject_low and ex_subj and not _subject_matches(str(ex_subj), subject_low):
                continue
            candidates.append(
                {
                    "rawDate": raw_d,
                    "name": str(ex.get("name") or ex.get("title") or cfg.get("targetExam") or "Exam"),
                    "subject": ex_subj,
                    "source": f"{cfg_source or 'study-config.json'}:exams[]",
                }
            )

    for key in ("examDate", "exam_date", "targetExamDate", "nextExamDate"):
        val = cfg.get(key)
        if val:
            candidates.append(
                {
                    "rawDate": val,
                    "name": str(cfg.get("targetExam") or cfg.get("examName") or "Target Exam"),
                    "subject": cfg.get("subject"),
                    "source": f"{cfg_source or 'study-config.json'}:{key}",
                }
            )
            break

    return candidates


def _compute_hours_until(raw_date: Any, now_dt: datetime) -> tuple[datetime | None, float | None]:
    exam_dt, is_date_only = _parse_dt(raw_date, default_hour=9)
    if exam_dt is None:
        return None, None
    if is_date_only:
        if exam_dt.date() < now_dt.date():
            return exam_dt, -1.0
        if exam_dt.date() == now_dt.date():
            eod = datetime(exam_dt.year, exam_dt.month, exam_dt.day, 23, 59, 0, tzinfo=timezone.utc)
            hrs = _clamp((eod - now_dt).total_seconds() / 3600.0, 0.0, 24.0)
            return eod, hrs
        # Future date-only exam: compute clean hours from now_dt to 09:00 UTC on exam date
        hrs = (exam_dt - now_dt).total_seconds() / 3600.0
        return exam_dt, hrs
    hrs = (exam_dt - now_dt).total_seconds() / 3600.0
    return exam_dt, hrs


# ---------------------------------------------------------------------------
# Public API 1: Fast O(1) Pre-Exam Taper Mode Detector (`<= 72h`)
# ---------------------------------------------------------------------------


def detect_taper_mode(
    kg_dir: Path | str | None,
    *,
    now: datetime | str | None = None,
    exam_date: str | datetime | date_cls | None = None,
    subject: str | None = None,
) -> dict[str, Any]:
    """O(1) Pre-Exam Taper Mode check reading `study-config.json` (`examDate` / `exams[]`)."""
    kg = _resolve_kg_only(kg_dir)
    now_dt, _ = _parse_dt(now) if now is not None else (datetime.now(timezone.utc), False)
    if now_dt is None:
        now_dt = datetime.now(timezone.utc)

    cfg, cfg_source = _load_study_config(kg)
    subj_low = _norm_subject_low(subject)
    candidates = _extract_candidate_exams(cfg, cfg_source, explicit_exam_date=exam_date, subject_low=subj_low)

    best_exam: dict[str, Any] | None = None
    best_hours: float | None = None
    best_dt: datetime | None = None

    for cand in candidates:
        ex_dt, hrs = _compute_hours_until(cand["rawDate"], now_dt)
        if ex_dt is None or hrs is None or hrs < 0.0:
            continue
        if best_hours is None or hrs < best_hours:
            best_hours = hrs
            best_dt = ex_dt
            best_exam = cand

    raw_sleep = cfg.get("sleepHours")
    sleep_floor = max(7.0, float(raw_sleep)) if isinstance(raw_sleep, (int, float)) and not isinstance(raw_sleep, bool) else 7.5
    default_mins = int(cfg.get("dailyMinutes") or 120) if isinstance(cfg.get("dailyMinutes"), (int, float)) else 120

    if best_hours is not None and 0.0 <= best_hours <= 72.0:
        hrs_round = round(best_hours, 1)
        if best_hours <= 24.0:
            phase = "final_24h"
            window = "final_24h"
            max_mins = 60
        elif best_hours <= 48.0:
            phase = "taper_72h"
            window = "48h_to_24h"
            max_mins = 150
        else:
            phase = "taper_72h"
            window = "72h_to_48h"
            max_mins = 240

        return {
            "active": True,
            "hoursRemaining": hrs_round,
            "phase": phase,
            "window": window,
            "allowNewConcepts": False,
            "sleepFloorHours": round(sleep_floor, 1),
            "maxSessionMinutes": max_mins,
            "examDate": best_dt.date().isoformat() if best_dt else None,
            "examName": best_exam["name"] if best_exam else None,
            "examDateSource": best_exam["source"] if best_exam else None,
        }

    return {
        "active": False,
        "hoursRemaining": round(best_hours, 1) if best_hours is not None else None,
        "phase": "none",
        "window": "none",
        "allowNewConcepts": True,
        "sleepFloorHours": round(sleep_floor, 1),
        "maxSessionMinutes": max(30, default_mins),
        "examDate": best_dt.date().isoformat() if best_dt else None,
        "examName": best_exam["name"] if best_exam else None,
        "examDateSource": best_exam["source"] if best_exam else None,
    }


# ---------------------------------------------------------------------------
# Public API 2: Strictly Non-Fabricated 3-Bullet Pre-Mortem Failure Shield
# ---------------------------------------------------------------------------


def build_premortem_shield(
    workspace: Path | str | None = None,
    *,
    subject: str | None = None,
    chapters: list[str] | str | None = None,
    limit: int = 3,
    kg_dir: Path | str | None = None,
) -> dict[str, Any]:
    """Build up to 3 Gollwitzer If-Then Pre-Mortem bullets strictly from observed learner logs.

    NEVER fabricates generic filler when logs are empty (`bullets: []`,
    `sourceBasis: "cold_start_minimal"`).
    """
    ws, kg, _ch = _resolve_paths(workspace, kg_dir)
    max_bullets = max(1, min(3, int(limit or 3)))
    subj_low = _norm_subject_low(subject)
    chap_filter: set[str] = set()
    if chapters:
        raw_c = [chapters] if isinstance(chapters, str) else list(chapters)
        for item in raw_c:
            for part in str(item or "").split(","):
                if part.strip():
                    chap_filter.add(part.strip().lower())

    def _in_scope(item_subj: Any, item_chap: Any) -> bool:
        if subj_low and item_subj and not _subject_matches(str(item_subj), subj_low):
            return False
        if chap_filter:
            cl = str(item_chap or "").strip().lower()
            if not cl or (cl not in chap_filter and not any(cf in cl for cf in chap_filter)):
                return False
        return True

    forks = load_jsonl(kg / "forks.jsonl")
    mistakes = load_jsonl(kg / "mistakes.jsonl")
    attempts = load_jsonl(kg / "exam-attempts.jsonl")
    profile = load_profile(kg)

    scoped_forks = [
        f for f in forks
        if str(f.get("status") or "").lower() not in ("mastered", "cleared", "archived")
        and _in_scope(f.get("subject"), f.get("chapter"))
    ]
    scoped_mistakes = [m for m in mistakes if _in_scope(m.get("subject"), m.get("chapter"))]

    # Filter exam attempts by subject if subjectBreakdown present
    scoped_attempts: list[dict[str, Any]] = []
    for a in attempts:
        if subj_low:
            sb = a.get("subjectBreakdown")
            if isinstance(sb, dict) and sb:
                if not any(_subject_matches(k, subj_low) for k in sb):
                    continue
        scoped_attempts.append(a)

    dna = profile.get("failureDna") if isinstance(profile.get("failureDna"), dict) else {}
    dna_count = int(dna.get("windowCount") or 0)

    bullets: list[dict[str, Any]] = []
    seen_keys: set[tuple[str, str]] = set()

    def _add_bullet(
        source_kind: str,
        dedup_key: str,
        evidence_badge: str,
        if_cue: str,
        then_action: str,
        *,
        prop_id: str | None = None,
        fork_id: str | None = None,
    ) -> None:
        if len(bullets) >= max_bullets:
            return
        k = (source_kind, dedup_key.lower())
        if k in seen_keys:
            return
        seen_keys.add(k)
        b: dict[str, Any] = {
            "rank": len(bullets) + 1,
            "sourceKind": source_kind,
            "evidenceBadge": _clip_str(evidence_badge, 72),
            "ifCue": _clip_str(if_cue, 150),
            "thenAction": _clip_str(then_action, 160),
        }
        if prop_id:
            b["propId"] = str(prop_id)
        if fork_id:
            b["forkId"] = str(fork_id)
        bullets.append(b)

    # Priority 1: Unresolved Blunder Forks (highest lapses / lowest wins first)
    if scoped_forks:
        ranked_forks = sorted(
            scoped_forks,
            key=lambda f: (-int(f.get("lapses") or 0), int(f.get("winCount") or 0), str(f.get("id") or "")),
        )
        f0 = ranked_forks[0]
        fid = str(f0.get("id") or "")
        pid = str(f0.get("propId") or "")
        lapses = int(f0.get("lapses") or 0)
        chap = str(f0.get("chapter") or f0.get("subject") or "active chapter")
        prompt_cue = str(f0.get("forkPrompt") or "").strip()
        if not prompt_cue and isinstance(f0.get("prefixSteps"), list) and f0["prefixSteps"]:
            prompt_cue = str(f0["prefixSteps"][-1])
        if_txt = f"IF you hit the fork setup ({_clip_str(prompt_cue, 85)}) in {chap}"
        then_txt = f"THEN verify boundary conditions & invariant before executing your first move in {chap}."
        badge = f"Blunder Fork {fid} · {lapses} lapse(s)" if lapses > 0 else f"Unresolved Fork {fid} · {chap}"
        _add_bullet("blunder_fork", pid or fid, badge, if_txt, then_txt, prop_id=pid or None, fork_id=fid or None)

    # Priority 2: Top Recurring Mistake Cluster (or observed Failure DNA when no mistakes match scope)
    mistake_clusters: list[tuple[tuple[str, str], list[dict[str, Any]]]] = []
    if scoped_mistakes:
        grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for m in scoped_mistakes:
            cat = str(m.get("category") or "concept_gap").strip()
            chap = str(m.get("chapter") or "General").strip()
            grouped.setdefault((cat, chap), []).append(m)
        mistake_clusters = sorted(
            grouped.items(),
            key=lambda kv: (-len(kv[1]), kv[0][0], kv[0][1]),
        )
        (top_cat, top_ch), rows_m = mistake_clusters[0]
        cnt = len(rows_m)
        sample = rows_m[-1]
        pid = str(sample.get("propId") or "").strip() or None
        trig = str(sample.get("triggerCue") or f"{top_cat.replace('_', ' ')} setup in {top_ch}").strip()
        corr = str(sample.get("correctMove") or "Verify the governing condition, sign, and unit before locking your answer.").strip()
        badge_prefix = "3-Strike Escalation · " if cnt >= 3 else ""
        badge = f"{badge_prefix}{cnt}x {top_cat} in {top_ch}"
        _add_bullet(
            "recurring_mistake",
            f"{top_cat}:{pid or top_ch}",
            badge,
            f"IF you see {_clip_str(trig, 95)}",
            f"THEN {_clip_str(corr, 120)}",
            prop_id=pid,
        )
    elif dna_count > 0 and not chap_filter:
        dom_tier = str(dna.get("dominantTier") or "comprehension_model")
        shares = dna.get("tierShares") or {}
        pct = int(round(float(shares.get(dom_tier) or 0.0) * 100))
        top_cats = ", ".join(str(c) for c in (dna.get("topCategories") or [])[:2])
        if dom_tier == "process_encoding":
            if_c = f"IF you finish a numerical/algebra step ({top_cats or 'encoding slip'})"
            th_a = "THEN run a 5-second sign, SI-unit, and limiting-case sanity check before submitting."
        elif dom_tier == "executive_pacing":
            if_c = "IF a single question stalls beyond 180 seconds on Pass 1"
            th_a = "THEN mark for review and move on to harvest high-confidence marks first."
        else:
            if_c = f"IF a problem looks familiar on the surface ({top_cats or 'model mismatch'})"
            th_a = "THEN name the governing law and check its validity assumptions before computing."
        _add_bullet(
            "failure_dna",
            dom_tier,
            f"Failure DNA: {dom_tier} ({pct}%, n={dna_count})",
            if_c,
            th_a,
        )

    # Priority 3: Real Timed Exam Trap (-EV gambling or ego-trap time sinks)
    if scoped_attempts and len(bullets) < max_bullets:
        total_neg_lost = sum(float(a.get("negativeMarksLost") or 0.0) for a in scoped_attempts)
        total_neg_gambles = 0
        total_slow = 0
        for a in scoped_attempts:
            evc = a.get("evCalibration")
            if isinstance(evc, dict):
                total_neg_gambles += int(evc.get("negEvGamblesCount") or 0)
            sq = a.get("slowQuestions")
            if isinstance(sq, list):
                total_slow += len(sq)

        if total_neg_gambles > 0 or total_neg_lost > 0.0:
            badge = (
                f"Exam EV Leak · {total_neg_gambles} -EV gamble(s), -{round(total_neg_lost, 1):g}m"
                if total_neg_gambles > 0
                else f"Exam Negative Marks · -{round(total_neg_lost, 1):g}m across {len(scoped_attempts)} mock(s)"
            )
            _add_bullet(
                "exam_trap",
                "neg_ev_gambling",
                badge,
                "IF your confidence on a negative-marked question is below break-even p* = (P + U) / (B + P)",
                "THEN skip it cleanly on Pass 1 instead of taking a negative-EV guess.",
            )
        elif total_slow > 0:
            _add_bullet(
                "exam_trap",
                "ego_trap_pacing",
                f"Exam Time-Sink · {total_slow} slow question(s) (>3 min) in {len(scoped_attempts)} mock(s)",
                "IF a single problem stalls past 180 seconds on Pass 1",
                "THEN mark it for review and move on to secure faster positive-EV marks.",
            )

    # Fill remaining slots (up to max_bullets) ONLY from additional real observed mistake clusters / forks
    for (cat, chap), rows_m in mistake_clusters[1:]:
        if len(bullets) >= max_bullets:
            break
        sample = rows_m[-1]
        pid = str(sample.get("propId") or "").strip() or None
        trig = str(sample.get("triggerCue") or f"{cat.replace('_', ' ')} in {chap}").strip()
        corr = str(sample.get("correctMove") or "Check invariants before locking in.").strip()
        _add_bullet(
            "recurring_mistake",
            f"{cat}:{pid or chap}",
            f"{len(rows_m)}x {cat} in {chap}",
            f"IF you encounter {_clip_str(trig, 95)}",
            f"THEN {_clip_str(corr, 120)}",
            prop_id=pid,
        )

    if len(bullets) < max_bullets and len(scoped_forks) > 1:
        for fk in scoped_forks[1:]:
            if len(bullets) >= max_bullets:
                break
            fid = str(fk.get("id") or "")
            pid = str(fk.get("propId") or "")
            chap = str(fk.get("chapter") or fk.get("subject") or "active chapter")
            pcue = str(fk.get("forkPrompt") or "").strip() or chap
            _add_bullet(
                "blunder_fork",
                pid or fid,
                f"Unresolved Fork {fid} · {chap}",
                f"IF you reach {_clip_str(pcue, 90)}",
                f"THEN verify boundary conditions & invariant before executing your first move in {chap}.",
                prop_id=pid or None,
                fork_id=fid or None,
            )

    if not bullets:
        return {
            "ok": True,
            "sourceBasis": "cold_start_minimal",
            "count": 0,
            "bullets": [],
            "note": "Zero mistake, fork, failure-DNA, or exam-trap logs in scope; Pre-Mortem Shield stays empty (no generic filler).",
        }

    return {
        "ok": True,
        "sourceBasis": "observed_logs",
        "count": len(bullets),
        "bullets": bullets,
    }


# ---------------------------------------------------------------------------
# Public API 3: 5-Pillar Exam-Day Readiness Forecaster & Top-3 ROI Levers
# ---------------------------------------------------------------------------


def _build_descendants(chains: list[dict[str, Any]]) -> dict[str, set[str]]:
    adj: dict[str, set[str]] = {}
    for raw in chains:
        nc = normalize_chain(raw)
        if not nc or float(nc.get("encompassWeight") or 0.0) <= 0.0:
            continue
        if str(nc.get("relation") or "").strip().lower() in _NON_CAUSAL_RELATIONS:
            continue
        conc = nc["conclusion"]
        for prem in nc["premises"]:
            adj.setdefault(prem, set()).add(conc)

    desc: dict[str, set[str]] = {}
    for u in adj:
        visited: set[str] = set()
        q: deque[str] = deque(adj[u])
        while q:
            cur = q.popleft()
            if cur in visited or cur == u:
                continue
            visited.add(cur)
            for nxt in adj.get(cur, ()):
                if nxt not in visited and nxt != u:
                    q.append(nxt)
        desc[u] = visited
    return desc


def _project_prop_retrievability(
    prop: dict[str, Any],
    target_dt: datetime,
    now_dt: datetime,
    params: tuple[float, ...],
) -> float:
    status = str(prop.get("status") or "planned").strip().lower()
    if status == "planned":
        return 0.0

    fsrs_raw = prop.get("fsrs")
    if isinstance(fsrs_raw, dict):
        # Support both canonical `last_review` and camelCase `lastReview`
        card_norm = dict(fsrs_raw)
        if not card_norm.get("last_review") and card_norm.get("lastReview"):
            card_norm["last_review"] = card_norm["lastReview"]
        if not card_norm.get("last_review") and prop.get("lastTested"):
            card_norm["last_review"] = prop["lastTested"]
        if isinstance(card_norm.get("stability"), (int, float)) and card_norm.get("last_review"):
            try:
                r_val = float(
                    card_retrievability(
                        card_norm,
                        target_dt,
                        parameters=params,
                        fractional_days=True,
                    )
                )
                if math.isfinite(r_val) and r_val > 0.0:
                    return _clamp(r_val, 0.0, 1.0)
            except Exception:
                pass

    if status == "misconception":
        return 0.12
    if status == "shaky":
        return 0.35

    # `solid` without complete FSRS metadata: conservative S=7.0d decay
    last_tested_dt, _ = _parse_dt(prop.get("lastTested") or prop.get("updatedAt"))
    ref_start = last_tested_dt if last_tested_dt is not None else now_dt
    elapsed_days = max(0.0, (target_dt - ref_start).total_seconds() / 86400.0)
    return _clamp(forgetting_curve(elapsed_days, 7.0, decay=params[20]), 0.0, 1.0)


def _readiness_band(score: float) -> str:
    if score < 40.0:
        return "needs_foundation"
    if score < 65.0:
        return "building"
    if score < 85.0:
        return "exam_ready"
    return "peak"


def compute_readiness(
    workspace: Path | str | None = None,
    *,
    exam_date: str | datetime | date_cls | None = None,
    subject: str | None = None,
    max_marks: float | None = None,
    today: str | datetime | date_cls | None = None,
    kg_dir: Path | str | None = None,
    chapters_dir: Path | str | None = None,
) -> dict[str, Any]:
    """Deterministically project exam-day readiness, score band, chapters, top-3 ROI levers, taper & shield."""
    ws, kg, ch_dir = _resolve_paths(workspace, kg_dir, chapters_dir)
    now_dt, _ = _parse_dt(today, default_hour=12) if today is not None else (datetime.now(timezone.utc), False)
    if now_dt is None:
        now_dt = datetime.now(timezone.utc)

    cfg, _cfg_src = _load_study_config(kg)
    subj_low = _norm_subject_low(subject)
    taper = detect_taper_mode(kg, now=now_dt, exam_date=exam_date, subject=subject)

    uncertainty_reasons: list[str] = []
    if taper.get("examDate"):
        exam_dt, _ = _parse_dt(taper["examDate"], default_hour=9)
        exam_date_str = str(taper["examDate"])
        exam_date_source = str(taper.get("examDateSource") or "study-config.json")
    else:
        exam_dt = now_dt + timedelta(days=14)
        exam_date_str = exam_dt.date().isoformat()
        exam_date_source = "default_14d"
        uncertainty_reasons.append("No examDate configured; defaulted projection horizon to +14 days.")

    assert exam_dt is not None
    days_to_exam = max(0.0, round((exam_dt - now_dt).total_seconds() / 86400.0, 1))

    # Load workspace data
    all_props = load_jsonl(kg / "propositions.jsonl")
    chains = load_jsonl(kg / "chains.jsonl")
    revlog = load_jsonl(kg / "revlog.jsonl")
    mistakes = load_jsonl(kg / "mistakes.jsonl")
    attempts = load_jsonl(kg / "exam-attempts.jsonl")
    params = load_fsrs_parameters(kg)
    ev_summary = compute_evidence_summary(ws)
    ev_props = ev_summary.get("propositions") or {}
    anti_alerts_all = ev_summary.get("antiInflationAlerts") or []
    due_forks_resp = list_due_forks(workspace=ws, kg_dir=kg, limit=50, today=now_dt.date().isoformat())
    due_forks_all = due_forks_resp.get("items") or due_forks_resp.get("forks") or []
    ilv_stats = get_interleave_stats(ws, kg_dir=kg)
    shield = build_premortem_shield(ws, subject=subject, limit=3, kg_dir=kg)

    # Filter propositions by scope (non-archived)
    props = [
        p for p in all_props
        if isinstance(p, dict)
        and str(p.get("id") or "").strip()
        and not bool(p.get("archived"))
        and _subject_matches(p.get("subject"), subj_low)
    ]
    desc_map = _build_descendants(chains)

    # Filter due forks & anti-inflation alerts & mistakes & attempts to scope
    due_forks = [f for f in due_forks_all if _subject_matches(f.get("subject"), subj_low)]
    anti_alerts = [a for a in anti_alerts_all if _subject_matches(a.get("subject"), subj_low)]
    scoped_mistakes = [m for m in mistakes if _subject_matches(m.get("subject"), subj_low)]
    scoped_attempts: list[dict[str, Any]] = []
    for a in attempts:
        if subj_low:
            sb = a.get("subjectBreakdown")
            if isinstance(sb, dict) and sb and not any(_subject_matches(k, subj_low) for k in sb):
                continue
        scoped_attempts.append(a)

    # --- Pillar 1 & 2: Projected Retention (C_ret) & Evidence Depth (C_evid) ---
    total_weight = 0.0
    weighted_r_exam = 0.0
    weighted_r_today = 0.0
    weighted_evid = 0.0
    learned_r_vals: list[float] = []
    prop_r_exam: dict[str, float] = {}
    prop_weight: dict[str, float] = {}
    chapters_agg: dict[str, dict[str, Any]] = {}

    m_active = 0
    planned_count = 0
    decay_candidates: list[dict[str, Any]] = []

    for p in props:
        pid = str(p["id"]).strip()
        status = str(p.get("status") or "planned").strip().lower()
        chap = str(p.get("chapter") or "General").strip() or "General"
        subj = str(p.get("subject") or "General").strip() or "General"
        desc_n = len(desc_map.get(pid, set()))
        w_i = 1.0 + 0.35 * math.log1p(desc_n)

        r_ex = _project_prop_retrievability(p, exam_dt, now_dt, params)
        r_td = _project_prop_retrievability(p, now_dt, now_dt, params)
        prop_r_exam[pid] = r_ex
        prop_weight[pid] = w_i

        if status == "planned":
            planned_count += 1
            tau_i = 0.0
        else:
            learned_r_vals.append(r_ex)
            if status == "misconception":
                m_active += 1
            if r_ex < 0.70 and status in ("solid", "shaky"):
                decay_candidates.append({"propId": pid, "chapter": chap, "rExam": r_ex, "weight": w_i})
            ev_rec = ev_props.get(pid)
            tier = int(ev_rec.get("tier") or 1) if isinstance(ev_rec, dict) else 1
            tau_i = _TIER_WEIGHT.get(tier, 0.25)

        total_weight += w_i
        weighted_r_exam += w_i * r_ex
        weighted_r_today += w_i * r_td
        weighted_evid += w_i * tau_i

        ca = chapters_agg.setdefault(
            chap,
            {
                "chapter": chap,
                "subject": subj,
                "totalProps": 0,
                "solidCount": 0,
                "shakyCount": 0,
                "misconceptionCount": 0,
                "plannedCount": 0,
                "weightSum": 0.0,
                "rExamWeighted": 0.0,
                "evidWeighted": 0.0,
                "dueForks": 0,
            },
        )
        ca["totalProps"] += 1
        if status == "solid":
            ca["solidCount"] += 1
        elif status == "shaky":
            ca["shakyCount"] += 1
        elif status == "misconception":
            ca["misconceptionCount"] += 1
        else:
            ca["plannedCount"] += 1
        ca["weightSum"] += w_i
        ca["rExamWeighted"] += w_i * r_ex
        ca["evidWeighted"] += w_i * tau_i

    for fk in due_forks:
        f_ch = str(fk.get("chapter") or "").strip()
        if f_ch in chapters_agg:
            chapters_agg[f_ch]["dueForks"] += 1

    if total_weight > 0.0:
        c_ret = _clamp(weighted_r_exam / total_weight, 0.0, 1.0)
        today_ret = _clamp(weighted_r_today / total_weight, 0.0, 1.0)
        raw_evid = _clamp(weighted_evid / total_weight, 0.0, 1.0)
        anti_penalty = max(0.75, 1.0 - 0.04 * min(6, len(anti_alerts)))
        c_evid = _clamp(raw_evid * anti_penalty, 0.0, 1.0)
    else:
        c_ret = 0.0
        today_ret = 0.0
        c_evid = 0.0
        uncertainty_reasons.append("Knowledge graph has 0 propositions in scope.")

    learned_mean_r = (sum(learned_r_vals) / len(learned_r_vals)) if learned_r_vals else 0.0

    # --- Pillar 3: Syllabus Coverage (C_syl) ---
    tracker_topics_total = 0
    tracker_topics_done = 0
    tracker_qs_total = 0
    tracker_qs_done = 0
    if ch_dir.is_dir():
        for tp in sorted(ch_dir.glob("*-tracker.json")):
            td = load_json(tp, default=None)
            if not isinstance(td, dict) or not td.get("slug"):
                continue
            if not _subject_matches(td.get("subject"), subj_low):
                continue
            topics = [t for t in (td.get("topics") or []) if isinstance(t, dict)]
            tracker_topics_total += len(topics)
            tracker_topics_done += sum(1 for t in topics if str(t.get("status") or "").lower() == "mastered")
            qs = [q for q in (td.get("questions") or []) if isinstance(q, dict)]
            tracker_qs_total += len(qs)
            tracker_qs_done += sum(1 for q in qs if str(q.get("status") or "").lower() in ("done", "solved", "mastered"))

    if tracker_topics_total > 0:
        t_ratio = tracker_topics_done / tracker_topics_total
        if tracker_qs_total > 0:
            q_ratio = tracker_qs_done / tracker_qs_total
            c_syl = _clamp(0.6 * t_ratio + 0.4 * q_ratio, 0.0, 1.0)
        else:
            c_syl = _clamp(t_ratio, 0.0, 1.0)
    elif props:
        c_syl = _clamp((len(props) - planned_count) / len(props), 0.0, 1.0)
    else:
        c_syl = 0.0

    # --- Pillar 4: Fork & Trap Clearance (C_trap) ---
    cluster_counts: Counter[tuple[str, str]] = Counter()
    for m in scoped_mistakes:
        cluster_counts[(str(m.get("category") or "concept_gap"), str(m.get("chapter") or "General"))] += 1
    e_3strike = sum(1 for c in cluster_counts.values() if c >= 3)
    f_due = len(due_forks)
    n_props = len(props)

    if n_props == 0 and m_active == 0 and f_due == 0 and e_3strike == 0:
        c_trap = 0.0
    else:
        denom = max(5.0, 0.35 * n_props)
        c_trap = _clamp(math.exp(-(1.5 * m_active + 1.0 * f_due + 1.2 * e_3strike) / denom), 0.0, 1.0)

    # --- Pillar 5: Timed Execution & EV Discipline (C_exec) ---
    exec_signals: list[float] = []
    mock_pcts: list[float] = []
    ev_scores_raw: list[float] = []
    total_neg_lost = 0.0
    total_mock_max = 0.0

    for a in scoped_attempts:
        pct = a.get("percentage")
        if subj_low and isinstance(a.get("subjectBreakdown"), dict):
            for sk, sbd in a["subjectBreakdown"].items():
                if _subject_matches(sk, subj_low) and isinstance(sbd, dict) and sbd.get("percentage") is not None:
                    pct = sbd["percentage"]
                    break
        if isinstance(pct, (int, float)) and not isinstance(pct, bool) and math.isfinite(float(pct)):
            mock_pcts.append(_clamp(float(pct), 0.0, 100.0))

        neg_l = float(a.get("negativeMarksLost") or 0.0)
        mx = float(a.get("maxScore") or 0.0)
        total_neg_lost += max(0.0, neg_l)
        total_mock_max += max(0.0, mx)

        evc = a.get("evCalibration")
        if isinstance(evc, dict) and isinstance(evc.get("evDisciplineScore"), (int, float)):
            ev_scores_raw.append(_clamp(float(evc["evDisciplineScore"]) / 100.0, 0.0, 1.0))
        elif isinstance(a.get("evDisciplineScore"), (int, float)):
            ev_scores_raw.append(_clamp(float(a["evDisciplineScore"]) / 100.0, 0.0, 1.0))
        elif mx > 0:
            ev_scores_raw.append(_clamp(1.0 - neg_l / max(1.0, 0.20 * mx), 0.0, 1.0))

    if ev_scores_raw:
        # Recency-weighted mean of recent mocks
        weights = [0.65 ** (len(ev_scores_raw) - 1 - i) for i in range(len(ev_scores_raw))]
        exec_signals.append(sum(w * v for w, v in zip(weights, ev_scores_raw)) / sum(weights))

    cal = compute_calibration(revlog[-60:])
    overconf_bias = float(cal.get("overconfidenceBias") or 0.0) if cal.get("count", 0) >= 3 else 0.0
    if int(cal.get("count") or 0) >= 5 and isinstance(cal.get("brierScore"), (int, float)):
        exec_signals.append(_clamp(1.0 - min(1.0, 2.0 * float(cal["brierScore"])), 0.0, 1.0))

    if int(ilv_stats.get("totalItemsChecked") or 0) >= 5 and isinstance(ilv_stats.get("tier1AccuracyPct"), (int, float)):
        exec_signals.append(_clamp(float(ilv_stats["tier1AccuracyPct"]) / 100.0, 0.0, 1.0))

    if exec_signals:
        c_exec = _clamp(sum(exec_signals) / len(exec_signals), 0.0, 1.0)
        exec_conf = "observed"
    elif props:
        c_exec = 0.55
        exec_conf = "untested"
        uncertainty_reasons.append("No graded timed mock exams yet; execution pillar uses conservative 55% prior.")
    else:
        c_exec = 0.0
        exec_conf = "untested"
        uncertainty_reasons.append("No graded timed mock exams yet.")

    if overconf_bias >= 0.15:
        uncertainty_reasons.append(f"Overconfidence bias detected (+{overconf_bias:.2f}) in confidence-scored reviews.")
    if props and c_evid < 0.45:
        uncertainty_reasons.append("Low T3/T4 mechanistic transfer evidence coverage widens score uncertainty.")

    # --- Composite Readiness Index & Bayesian-Shrunk Score Band ---
    readiness_index = round(
        100.0 * (0.32 * c_ret + 0.22 * c_evid + 0.20 * c_syl + 0.14 * c_trap + 0.12 * c_exec),
        1,
    )
    band = _readiness_band(readiness_index)

    m_mocks = len(mock_pcts)
    if m_mocks > 0:
        mw = [0.7 ** (m_mocks - 1 - i) for i in range(m_mocks)]
        s_mock = sum(w * v for w, v in zip(mw, mock_pcts)) / sum(mw)
        w_mock = m_mocks / (m_mocks + 2.5)
        expected_pct = w_mock * s_mock + (1.0 - w_mock) * readiness_index
    else:
        expected_pct = readiness_index

    sigma = _clamp(
        6.0 + (9.0 / math.sqrt(1.0 + m_mocks)) + 8.0 * max(0.0, overconf_bias) + 5.0 * (1.0 - c_evid),
        4.5,
        18.0,
    )
    low_pct = round(_clamp(expected_pct - sigma, 0.0, 100.0), 1)
    exp_pct = round(_clamp(expected_pct, 0.0, 100.0), 1)
    high_pct = round(_clamp(expected_pct + sigma, 0.0, 100.0), 1)

    eff_max_marks: float | None = None
    if isinstance(max_marks, (int, float)) and not isinstance(max_marks, bool) and float(max_marks) > 0:
        eff_max_marks = float(max_marks)
    elif isinstance(cfg.get("maxMarks"), (int, float)) and not isinstance(cfg.get("maxMarks"), bool) and float(cfg["maxMarks"]) > 0:
        eff_max_marks = float(cfg["maxMarks"])

    predicted_marks: dict[str, Any] | None = None
    if eff_max_marks is not None:
        predicted_marks = {
            "maxMarks": round(eff_max_marks, 1),
            "low": round(eff_max_marks * low_pct / 100.0, 1),
            "expected": round(eff_max_marks * exp_pct / 100.0, 1),
            "high": round(eff_max_marks * high_pct / 100.0, 1),
        }

    # --- Per-Chapter Breakdown ---
    chapters_out: list[dict[str, Any]] = []
    for ch_name, ca in sorted(chapters_agg.items(), key=lambda kv: kv[0].lower()):
        ws_sum = ca["weightSum"] or 1.0
        ch_ret = (ca["rExamWeighted"] / ws_sum) * 100.0
        ch_evid = (ca["evidWeighted"] / ws_sum) * 100.0
        ch_cov = ((ca["totalProps"] - ca["plannedCount"]) / ca["totalProps"]) * 100.0 if ca["totalProps"] > 0 else 0.0
        ch_ready = round(0.45 * ch_ret + 0.30 * ch_evid + 0.25 * ch_cov, 1)
        chapters_out.append(
            {
                "chapter": ch_name,
                "subject": ca["subject"],
                "totalProps": ca["totalProps"],
                "solidCount": ca["solidCount"],
                "shakyCount": ca["shakyCount"],
                "misconceptionCount": ca["misconceptionCount"],
                "plannedCount": ca["plannedCount"],
                "dueForks": ca["dueForks"],
                "projectedRetentionPct": round(ch_ret, 1),
                "evidenceDepthPct": round(ch_evid, 1),
                "readinessPct": ch_ready,
            }
        )
    chapters_out.sort(key=lambda c: (c["readinessPct"], c["chapter"]))

    # --- Top-3 Highest-ROI Study Levers (`expectedMarksGain / hoursEffort`) ---
    marks_scale = eff_max_marks if eff_max_marks is not None else 100.0
    levers: list[dict[str, Any]] = []

    def _add_lever(
        kind: str,
        title: str,
        why: str,
        hours_effort: float,
        gain_readiness_pct: float,
        exact_cmd: str,
        skill_hint: str,
    ) -> None:
        if gain_readiness_pct <= 0.1 or hours_effort <= 0.0:
            return
        marks_gain = round(marks_scale * (gain_readiness_pct / 100.0), 2)
        roi = round(marks_gain / hours_effort, 2)
        levers.append(
            {
                "kind": kind,
                "title": title,
                "why": why,
                "effortMinutes": int(round(hours_effort * 60)),
                "hoursEffort": round(hours_effort, 2),
                "expectedReadinessGainPct": round(gain_readiness_pct, 2),
                "expectedMarksGain": marks_gain,
                "roiMarksPerHour": roi,
                "exactCmd": exact_cmd,
                "skillHint": skill_hint,
            }
        )

    # 1. Repair active misconception / high-blast root
    misc_props = [p for p in props if str(p.get("status") or "").lower() == "misconception"]
    if misc_props:
        misc_props.sort(key=lambda p: (-len(desc_map.get(str(p["id"]), set())), str(p["id"])))
        m0 = misc_props[0]
        pid = str(m0["id"])
        w0 = prop_weight.get(pid, 1.0)
        delta_ret = (w0 * (0.85 - prop_r_exam.get(pid, 0.12))) / max(1.0, total_weight)
        delta_trap = 1.0 - c_trap
        gain_pct = 100.0 * (0.32 * delta_ret + 0.14 * min(0.35, delta_trap * 0.45))
        _add_lever(
            "repair_misconception",
            f"Repair misconception: {_clip_str(str(m0.get('title') or pid), 52)}",
            f"Active misconception on '{pid}' ({len(desc_map.get(pid, set()))} downstream) drags both retention and trap clearance.",
            0.35,
            max(1.5, gain_pct),
            f"syntopik run srs_engine review {pid}",
            "teach-chapter",
        )

    # 2. Clear due Blunder Forks / 3-strike traps
    if f_due > 0 or e_3strike > 0:
        trap_gap = max(0.05, 1.0 - c_trap)
        gain_pct = 100.0 * (0.14 * min(0.50, trap_gap * 0.65))
        _add_lever(
            "clear_forks",
            f"Clear {f_due} due Blunder Fork(s)" if f_due > 0 else f"Drill {e_3strike} 3-strike trap cluster(s)",
            f"Eliminates recurring exam blunders ({f_due} due forks, {e_3strike} 3-strike clusters).",
            0.25,
            max(1.2, gain_pct),
            "syntopik run fork_replay due --json",
            "practice-drill",
        )

    # 3. Upgrade anti-inflation bottleneck T1/T2 -> T3
    if anti_alerts:
        a0 = anti_alerts[0]
        pid = str(a0["propId"])
        w0 = prop_weight.get(pid, 1.5)
        cur_tau = _TIER_WEIGHT.get(int(a0.get("currentTier") or 1), 0.25)
        delta_ev = (w0 * (0.88 - cur_tau)) / max(1.0, total_weight) + 0.04 * c_evid
        gain_pct = 100.0 * (0.22 * delta_ev)
        suggested = str(a0.get("suggestedAction") or "falsify_arena")
        _add_lever(
            "upgrade_evidence",
            f"Verify bottleneck '{pid}' to T3 transfer",
            f"High-leverage node ({a0.get('descendantsCount', 0)} downstream) stuck at {a0.get('currentTierLabel', 'T1/T2')}.",
            0.30,
            max(1.0, gain_pct),
            f"syntopik run {suggested} list --json",
            "practice-drill",
        )

    # 4. Plug negative-EV gambling leak
    if total_neg_lost > 0.0 and total_mock_max > 0.0 and (total_neg_lost / total_mock_max) >= 0.04:
        ev_gap = max(0.05, 1.0 - c_exec)
        gain_pct = 100.0 * (0.12 * min(0.55, ev_gap * 0.70) + min(0.08, total_neg_lost / total_mock_max))
        _add_lever(
            "plug_ev_leak",
            f"Plug negative-EV skip leak (-{round(total_neg_lost, 1):g} marks lost)",
            "Enforcing exact break-even skip threshold p*=(P+U)/(B+P) recovers negative marks immediately.",
            0.50,
            max(1.2, gain_pct),
            "syntopik readiness --premortem-only --json",
            "exam-simulator",
        )

    # 5. FIRe-compressed refresh of decaying cards (R(t_exam) < 0.70)
    if decay_candidates:
        decay_candidates.sort(key=lambda d: (-d["weight"] * (0.90 - d["rExam"]), d["propId"]))
        batch = decay_candidates[:5]
        delta_ret = sum(d["weight"] * (0.90 - d["rExam"]) for d in batch) / max(1.0, total_weight)
        gain_pct = 100.0 * (0.32 * delta_ret)
        _add_lever(
            "refresh_decay",
            f"Refresh {len(batch)} decaying card(s) before exam",
            f"Projected exam-day retention drops below 70% on {len(decay_candidates)} learned proposition(s).",
            0.25,
            max(1.0, gain_pct),
            "syntopik review --json",
            "knowledge-graph",
        )

    # 6. Blind-Chapter Interleave when >= 2 chapters exist and Tier-1 accuracy < 80% or unpracticed
    if len(chapters_agg) >= 2 and (ilv_stats.get("tier1AccuracyPct") is None or float(ilv_stats["tier1AccuracyPct"]) < 80.0):
        if taper["phase"] != "final_24h":
            _add_lever(
                "interleave_drill",
                f"Run blind-chapter interleave across {len(chapters_agg)} chapters",
                "Mixed-chapter principle discrimination prevents surface-feature confusion on unseen exam problems.",
                0.30,
                1.8,
                "syntopik interleave build --n 6 --json",
                "practice-drill",
            )

    # 7. First timed CBT baseline (skip in final_24h taper!)
    if m_mocks == 0 and len(learned_r_vals) > 0 and taper["phase"] != "final_24h":
        _add_lever(
            "timed_baseline",
            "Take 1 calibrated timed CBT baseline mock",
            "Replaces the unverified 55% execution prior with real pacing, EV-skip, and autopsy telemetry.",
            0.75,
            2.2,
            "syntopik run exam_runner schema",
            "exam-simulator",
        )

    # Sort strictly by ROI (`expectedMarksGain / hoursEffort`) descending
    levers.sort(key=lambda lv: (-lv["roiMarksPerHour"], -lv["expectedMarksGain"], lv["effortMinutes"], lv["kind"]))
    top_levers: list[dict[str, Any]] = []
    for idx, lv in enumerate(levers[:3], start=1):
        top_levers.append({"rank": idx, **lv})

    return {
        "ok": True,
        "asOfDate": now_dt.date().isoformat(),
        "examDate": exam_date_str,
        "examDateSource": exam_date_source,
        "daysToExam": days_to_exam,
        "subject": subject,
        "readinessIndex": readiness_index,
        "band": band,
        "predictedScorePct": {
            "low": low_pct,
            "expected": exp_pct,
            "high": high_pct,
        },
        "predictedMarks": predicted_marks,
        "pillars": {
            "projectedRetention": round(c_ret * 100.0, 1),
            "evidenceDepth": round(c_evid * 100.0, 1),
            "forkClearance": round(c_trap * 100.0, 1),
            "evDiscipline": round(c_exec * 100.0, 1),
            "syllabusCoverage": round(c_syl * 100.0, 1),
        },
        "learnedMeanRetentionPct": round(learned_mean_r * 100.0, 1),
        "decayDeltaPct": round((c_ret - today_ret) * 100.0, 1),
        "executionConfidence": exec_conf,
        "chapters": chapters_out,
        "topLevers": top_levers,
        "taperMode": taper,
        "preMortemShield": shield,
        "uncertaintyReasons": uncertainty_reasons,
    }
