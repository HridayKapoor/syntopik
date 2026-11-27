"""Syntopik — Weekly Progress Report (pure stdlib).

Summarises what a learner actually did over a window (default: the last 7 days)
and compares it with the window before, using only the existing ledgers:

- `revlog.jsonl`        reviews, ratings, durations, hints, true retention
- `propositions.jsonl`  current mastery distribution, due load, per-chapter state
- `mistakes.jsonl`      logged mistakes by category

Everything is computed from logged evidence. Nothing is estimated or invented:
a metric with no data is reported as `None`, never as zero or a guess.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any

from syntopik.storage import load_jsonl

__all__ = ["build_progress_report", "render_report_lines", "render_report_markdown"]

MASTERY_ORDER = ("solid", "learning", "shaky", "misconception", "planned")
_LAPSE_RATINGS = {"again", "1"}


def _parse_ts(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        try:
            dt = datetime.fromisoformat(raw[:10])
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _row_ts(row: dict[str, Any]) -> datetime | None:
    return _parse_ts(row.get("ts") or row.get("reviewedAt") or row.get("date"))


def _is_lapse(row: dict[str, Any]) -> bool:
    rating = str(row.get("rating") if row.get("rating") is not None else row.get("ratingInt") or "").strip().lower()
    if rating in _LAPSE_RATINGS:
        return True
    return row.get("correct") is False and not rating


def _review_window_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(rows)
    days = sorted({ts.date().isoformat() for r in rows if (ts := _row_ts(r))})
    durations = [int(r["durationMs"]) for r in rows if isinstance(r.get("durationMs"), (int, float)) and r["durationMs"] > 0]
    hints = [int(r["hintsUsed"]) for r in rows if isinstance(r.get("hintsUsed"), (int, float))]
    lapses = sum(1 for r in rows if _is_lapse(r))
    # True retention: recall success on cards that were actually due for recall
    # (seen at least a day earlier), the quantity FSRS's target retention predicts.
    mature = [r for r in rows if isinstance(r.get("elapsedDays"), (int, float)) and r["elapsedDays"] >= 1]
    mature_ok = sum(1 for r in mature if not _is_lapse(r))
    targets = [float(r["targetRetention"]) for r in mature if isinstance(r.get("targetRetention"), (int, float))]
    return {
        "reviews": n,
        "activeDays": len(days),
        "activeDates": days,
        "studyMinutes": round(sum(durations) / 60000.0, 1) if durations else None,
        "accuracy": round((n - lapses) / n, 3) if n else None,
        "lapses": lapses,
        "avgHints": round(sum(hints) / len(hints), 2) if hints else None,
        "trueRetention": round(mature_ok / len(mature), 3) if mature else None,
        "matureReviews": len(mature),
        "targetRetention": round(sum(targets) / len(targets), 3) if targets else None,
        "uniqueConcepts": len({str(r.get("propId")) for r in rows if r.get("propId")}),
    }


def _streak(all_review_dates: set[str], today: date) -> int:
    """Consecutive days with >= 1 review, ending today (or yesterday if today is empty)."""
    cursor = today if today.isoformat() in all_review_dates else today - timedelta(days=1)
    streak = 0
    while cursor.isoformat() in all_review_dates:
        streak += 1
        cursor -= timedelta(days=1)
    return streak


def _delta(cur: float | int | None, prev: float | int | None) -> float | None:
    if cur is None or prev is None:
        return None
    return round(float(cur) - float(prev), 3)


def build_progress_report(
    kg_dir: Path | str,
    *,
    days: int = 7,
    now: datetime | None = None,
    top_n: int = 5,
) -> dict[str, Any]:
    """Build a JSON-serialisable progress report for the last `days` days."""
    kg = Path(kg_dir)
    days = max(1, int(days))
    now_dt = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    today = now_dt.date()
    # Calendar-day window ending today (UTC): `days=7` covers today and the 6 days before.
    win_start = datetime.combine(today - timedelta(days=days - 1), time.min, tzinfo=timezone.utc)
    prev_start = win_start - timedelta(days=days)

    revlog = [r for r in load_jsonl(kg / "revlog.jsonl") if isinstance(r, dict)]
    props = [p for p in load_jsonl(kg / "propositions.jsonl") if isinstance(p, dict) and p.get("id")]
    mistakes = [m for m in load_jsonl(kg / "mistakes.jsonl") if isinstance(m, dict)]

    cur_rows: list[dict[str, Any]] = []
    prev_rows: list[dict[str, Any]] = []
    all_dates: set[str] = set()
    for r in revlog:
        ts = _row_ts(r)
        if ts is None or ts > now_dt:
            continue
        all_dates.add(ts.date().isoformat())
        if ts >= win_start:
            cur_rows.append(r)
        elif ts >= prev_start:
            prev_rows.append(r)

    cur = _review_window_stats(cur_rows)
    prev = _review_window_stats(prev_rows)

    # Mastery & due load (current state of the graph)
    props_by_id = {str(p["id"]): p for p in props}
    live = [p for p in props if not p.get("archived") and not p.get("suspended")]
    status_counts = Counter(str(p.get("status") or "planned").lower() for p in live)
    today_s = today.isoformat()
    week_s = (today + timedelta(days=7)).isoformat()
    due_now = 0
    due_week = 0
    for p in live:
        st = str(p.get("status") or "planned").lower()
        nr = p.get("nextReview")
        if st in ("misconception", "shaky") or (isinstance(nr, str) and nr[:10] <= today_s):
            due_now += 1
        elif isinstance(nr, str) and nr[:10] <= week_s:
            due_week += 1

    chapters: dict[str, Counter[str]] = defaultdict(Counter)
    for p in live:
        chapters[str(p.get("chapter") or p.get("subject") or "Unsorted")][str(p.get("status") or "planned").lower()] += 1
    chapter_rows = []
    for name, cnt in chapters.items():
        total = sum(cnt.values())
        chapter_rows.append(
            {
                "chapter": name,
                "total": total,
                "solid": cnt.get("solid", 0),
                "solidPct": round(cnt.get("solid", 0) / total, 3) if total else 0.0,
                "needsWork": cnt.get("shaky", 0) + cnt.get("misconception", 0),
            }
        )
    chapter_rows.sort(key=lambda c: (-c["needsWork"], c["solidPct"], c["chapter"]))

    # Concepts that lapsed most in the window
    lapse_counts = Counter(str(r.get("propId")) for r in cur_rows if r.get("propId") and _is_lapse(r))
    struggling = [
        {
            "id": pid,
            "title": str(props_by_id.get(pid, {}).get("title") or pid),
            "chapter": str(props_by_id.get(pid, {}).get("chapter") or ""),
            "lapses": n,
        }
        for pid, n in lapse_counts.most_common(top_n)
    ]

    # Mistakes in the window
    win_mistakes = [m for m in mistakes if (ts := _row_ts(m)) is not None and win_start <= ts <= now_dt]
    mistake_cats = Counter(str(m.get("category") or "uncategorised") for m in win_mistakes)

    focus = None
    if struggling:
        focus = {"kind": "concept", "id": struggling[0]["id"], "title": struggling[0]["title"],
                 "reason": f"lapsed {struggling[0]['lapses']}x this window"}
    elif chapter_rows and chapter_rows[0]["needsWork"] > 0:
        c0 = chapter_rows[0]
        focus = {"kind": "chapter", "id": c0["chapter"], "title": c0["chapter"],
                 "reason": f"{c0['needsWork']} shaky/misconception node(s)"}
    elif due_now:
        focus = {"kind": "review", "id": None, "title": "Due reviews", "reason": f"{due_now} item(s) due now"}

    return {
        "ok": True,
        "kind": "progress_report",
        "windowDays": days,
        "windowStart": win_start.replace(microsecond=0).isoformat(),
        "windowEnd": now_dt.replace(microsecond=0).isoformat(),
        "current": cur,
        "previous": prev,
        "deltas": {
            "reviews": _delta(cur["reviews"], prev["reviews"]),
            "activeDays": _delta(cur["activeDays"], prev["activeDays"]),
            "studyMinutes": _delta(cur["studyMinutes"], prev["studyMinutes"]),
            "accuracy": _delta(cur["accuracy"], prev["accuracy"]),
            "trueRetention": _delta(cur["trueRetention"], prev["trueRetention"]),
        },
        "streakDays": _streak(all_dates, today),
        "mastery": {
            "total": len(live),
            "byStatus": {k: status_counts.get(k, 0) for k in MASTERY_ORDER} | {
                k: v for k, v in status_counts.items() if k not in MASTERY_ORDER
            },
            "solidPct": round(status_counts.get("solid", 0) / len(live), 3) if live else None,
        },
        "due": {"now": due_now, "next7Days": due_week},
        "chapters": chapter_rows[: max(top_n, 1)],
        "struggling": struggling,
        "mistakes": {"count": len(win_mistakes), "byCategory": dict(mistake_cats.most_common())},
        "focus": focus,
    }


def _pct(v: float | None) -> str:
    return "n/a" if v is None else f"{v * 100:.0f}%"


def _signed(v: float | None, *, pct: bool = False, unit: str = "") -> str:
    if v is None:
        return ""
    if pct:
        return f" ({v * 100:+.0f} pts)"
    return f" ({v:+g}{unit})"


def render_report_lines(rep: dict[str, Any]) -> list[str]:
    cur, d = rep["current"], rep["deltas"]
    m = rep["mastery"]
    lines = [
        f"Progress — last {rep['windowDays']} day(s), vs the {rep['windowDays']} before",
        f"  Reviews        : {cur['reviews']}{_signed(d['reviews'])} across {cur['uniqueConcepts']} concept(s)",
        f"  Active days    : {cur['activeDays']}/{rep['windowDays']}{_signed(d['activeDays'])} · streak {rep['streakDays']} day(s)",
        f"  Study time     : {'n/a' if cur['studyMinutes'] is None else str(cur['studyMinutes']) + ' min'}{_signed(d['studyMinutes'], unit=' min')}",
        f"  Accuracy       : {_pct(cur['accuracy'])}{_signed(d['accuracy'], pct=True)} · {cur['lapses']} lapse(s)",
        f"  True retention : {_pct(cur['trueRetention'])}{_signed(d['trueRetention'], pct=True)}"
        + (f" vs target {_pct(cur['targetRetention'])}" if cur["targetRetention"] is not None else "")
        + f" ({cur['matureReviews']} mature review(s))",
        f"  Mastery        : {m['byStatus'].get('solid', 0)}/{m['total']} solid ({_pct(m['solidPct'])}), "
        f"{m['byStatus'].get('shaky', 0)} shaky, {m['byStatus'].get('misconception', 0)} misconception",
        f"  Due            : {rep['due']['now']} now, {rep['due']['next7Days']} more in 7 days",
        f"  Mistakes       : {rep['mistakes']['count']} logged"
        + (
            " (" + ", ".join(f"{k} {v}" for k, v in list(rep["mistakes"]["byCategory"].items())[:3]) + ")"
            if rep["mistakes"]["count"]
            else ""
        ),
    ]
    if rep["struggling"]:
        lines.append("  Most lapsed    : " + "; ".join(f"{s['title']} ({s['lapses']}x)" for s in rep["struggling"][:3]))
    if rep["focus"]:
        lines.append(f"  Focus next     : {rep['focus']['title']} — {rep['focus']['reason']}")
    return lines


def render_report_markdown(rep: dict[str, Any]) -> str:
    cur, prev = rep["current"], rep["previous"]
    rows = [
        ("Reviews", cur["reviews"], prev["reviews"]),
        ("Active days", cur["activeDays"], prev["activeDays"]),
        ("Study minutes", cur["studyMinutes"], prev["studyMinutes"]),
        ("Accuracy", _pct(cur["accuracy"]), _pct(prev["accuracy"])),
        ("True retention", _pct(cur["trueRetention"]), _pct(prev["trueRetention"])),
        ("Concepts touched", cur["uniqueConcepts"], prev["uniqueConcepts"]),
    ]
    out = [
        f"# Syntopik progress — {rep['windowStart'][:10]} to {rep['windowEnd'][:10]}",
        "",
        "| Metric | This window | Previous window |",
        "| --- | --- | --- |",
        *[f"| {name} | {'n/a' if a is None else a} | {'n/a' if b is None else b} |" for name, a, b in rows],
        "",
        f"Streak: {rep['streakDays']} day(s). Due now: {rep['due']['now']}; next 7 days: {rep['due']['next7Days']}.",
        "",
        "## Chapters needing work",
        "",
        "| Chapter | Solid | Needs work | Total |",
        "| --- | --- | --- | --- |",
        *[f"| {c['chapter']} | {_pct(c['solidPct'])} | {c['needsWork']} | {c['total']} |" for c in rep["chapters"]],
    ]
    if rep["struggling"]:
        out += ["", "## Most lapsed concepts", ""]
        out += [f"- {s['title']} ({s['chapter'] or 'no chapter'}): {s['lapses']} lapse(s)" for s in rep["struggling"]]
    if rep["focus"]:
        out += ["", f"**Focus next:** {rep['focus']['title']} ({rep['focus']['reason']})."]
    return "\n".join(out) + "\n"
