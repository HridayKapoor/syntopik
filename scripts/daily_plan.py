#!/usr/bin/env python3
"""Syntopik — Profile-Aware Calm Daily Study Planner & 5-Minute Micro-Sprint Engine.

Combines live signals from:
  - `knowledge-graph/propositions.jsonl` & `chains.jsonl` (FSRS-6 + FIRe SRC knockouts + BKT)
  - `knowledge-graph/mistakes.jsonl` (top recurring cognitive & coding mistake categories)
  - `scripts/graph_intel.py` (bottlenecks, memorized islands, synthesis combos, lookalike confusables)
  - `chapters/*-tracker.json` (active Topic/Chapter Autopilot ledger & Scope Charter, if present)
  - Optional `knowledge-graph/study-config.json` or `exam-config.json` (`{examDate, dailyMinutes, targetExam}`)

Runs 100% cleanly on bare Python 3.12+ with zero third-party dependencies!
Outputs either a calm time-boxed daily schedule (summing strictly to `--minutes`,
including FIRe-compressed warmups and `[A, B, A']` interleaved contrast triplets)
or a focused `--micro 5` commute/micro-review sprint.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date as date_cls, datetime, timedelta, timezone
import hashlib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = ROOT / "scripts"
DEFAULT_KG_DIR = ROOT / "knowledge-graph"
DEFAULT_CHAPTERS_DIR = ROOT / "chapters"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from syntopik.fsrs_core import (  # noqa: E402
    card_retrievability,
    compress_due_queue,
    load_fsrs_parameters,
)
from syntopik.storage import (  # noqa: E402
    load_json as _load_json_safe,
    load_jsonl as _load_jsonl,
    write_text_atomic as _write_text_atomic,
)

import chapter_tracker  # noqa: E402
import graph_intel  # noqa: E402
import mistake_analytics  # noqa: E402

LEVEL_RANK = {"basics": 0, "main": 1, "advanced": 2}


def resolve_kg_dir(kg_dir: Path | str | None = None) -> Path:
    resolved, _ = chapter_tracker.resolve_kg_dir(kg_dir)
    return resolved


def resolve_chapters_dir(chapters_dir: Path | str | None = None) -> Path:
    resolved, _ = chapter_tracker.resolve_chapters_dir(chapters_dir)
    return resolved


def _parse_today(today_arg: str | None) -> tuple[date_cls, datetime]:
    if today_arg and today_arg.strip():
        d = date_cls.fromisoformat(today_arg.strip()[:10])
        dt = datetime(d.year, d.month, d.day, 12, 0, 0, tzinfo=timezone.utc)
        return d, dt
    now = datetime.now(timezone.utc)
    return now.date(), now


def _compute_due_items(
    props: list[dict[str, Any]],
    today_iso: str,
    now_dt: datetime,
    kg_dir: Path | None = None,
) -> list[dict[str, Any]]:
    params = load_fsrs_parameters(kg_dir)
    items: list[dict[str, Any]] = []
    for p in props:
        if p.get("archived") is True:
            continue
        pid = str(p.get("id") or "").strip()
        if not pid:
            continue
        status = str(p.get("status") or "planned").lower()
        due_date = p.get("nextReview")
        is_due = status in ("misconception", "shaky") or (isinstance(due_date, str) and due_date <= today_iso)
        if not is_due:
            continue
        retr: float | None = None
        if isinstance(p.get("fsrs"), dict):
            try:
                retr = round(float(card_retrievability(p["fsrs"], now_dt, parameters=params)), 3)
            except Exception:
                retr = None
        items.append(
            {
                "id": pid,
                "title": str(p.get("title") or pid),
                "subject": str(p.get("subject") or "General"),
                "chapter": str(p.get("chapter") or "General"),
                "status": status,
                "bktMastery": p.get("bktMastery"),
                "nextReview": due_date,
                "retrievability": retr,
                "notes": str(p.get("notes") or "").strip(),
            }
        )

    items.sort(
        key=lambda x: (
            0 if x["status"] == "misconception" else 1 if x["status"] == "shaky" else 2,
            x["retrievability"] if x["retrievability"] is not None else 0.5,
            str(x["id"]),
        )
    )
    return items


def _inspect_active_tracker(chapters_dir: Path) -> dict[str, Any] | None:
    """Read active `chapters/*-tracker.json` following the shared Chapter Autopilot schema."""
    if not chapters_dir.exists() or not chapters_dir.is_dir():
        return None

    trackers: list[tuple[Path, dict[str, Any]]] = []
    for path in sorted(chapters_dir.glob("*-tracker.json")):
        data = _load_json_safe(path)
        if isinstance(data, dict) and data.get("slug"):
            trackers.append((path, data))
    if not trackers:
        return None

    chosen = next((t for t in trackers if t[1].get("active") is True), None)
    if chosen is None:
        trackers.sort(key=lambda pair: str(pair[1].get("updatedAt") or ""), reverse=True)
        chosen = trackers[0]

    path, tr = chosen
    topics = [t for t in (tr.get("topics") or []) if isinstance(t, dict)]
    questions = [q for q in (tr.get("questions") or []) if isinstance(q, dict)]
    sources = [s for s in (tr.get("sources") or []) if isinstance(s, dict)]
    milestones = tr.get("milestones") if isinstance(tr.get("milestones"), dict) else {}
    config = tr.get("config") if isinstance(tr.get("config"), dict) else {}
    step_counter = int(tr.get("stepCounter") or 0)
    retest_gap = int(config.get("retestGap") or 3)

    mastered_topic_ids = {str(t.get("id")) for t in topics if t.get("status") == "mastered"}
    topics_mastered = len(mastered_topic_ids)
    q_done = sum(1 for q in questions if q.get("status") in ("solved", "mastered", "skipped"))
    q_retest = sum(1 for q in questions if q.get("status") == "retest")

    next_action = "chapter_complete"
    next_detail = f"All topics and questions complete for {tr.get('title', tr['slug'])}."
    next_ref: dict[str, Any] = {}

    def _topic_sort_key(t: dict[str, Any]) -> tuple[int, int, str]:
        lvl_norm = chapter_tracker.normalize_level_soft(t.get("level"), "basics")
        return (
            LEVEL_RANK.get(lvl_norm, 1),
            int(t.get("order") or 999),
            str(t.get("id") or ""),
        )

    def _q_sort_key(q: dict[str, Any]) -> tuple[int, str]:
        lvl_norm = chapter_tracker.normalize_level_soft(q.get("level"), "basics")
        return (
            LEVEL_RANK.get(lvl_norm, 1),
            str(q.get("qid") or ""),
        )

    in_prog_topics = sorted((t for t in topics if t.get("status") == "in_progress"), key=_topic_sort_key)
    ready_pending_topics = sorted(
        (
            t
            for t in topics
            if t.get("status") in ("pending", "probed")
            and all(pr in mastered_topic_ids for pr in (t.get("prereqTopicIds") or []))
        ),
        key=_topic_sort_key,
    )
    due_retests = sorted(
        (
            q
            for q in questions
            if q.get("status") == "retest"
            and (q.get("topicId") in mastered_topic_ids or not topics)
            and (step_counter - int(q.get("lastStep") or 0) >= retest_gap)
        ),
        key=_q_sort_key,
    )
    unseen_mastered = sorted(
        (
            q
            for q in questions
            if q.get("status") in ("unseen", "in_progress")
            and (q.get("topicId") in mastered_topic_ids or not topics)
        ),
        key=_q_sort_key,
    )
    any_retest = sorted((q for q in questions if q.get("status") == "retest"), key=_q_sort_key)
    any_unseen = sorted((q for q in questions if q.get("status") in ("unseen", "in_progress")), key=_q_sort_key)

    jee_mode = chapter_tracker.is_jee_profile(tr)
    norm_tr = chapter_tracker._normalize_tracker(tr)
    scope_charter = norm_tr["scopeCharter"]

    if sources and not milestones.get("sourcesIndexed"):
        next_action = "index_sources"
        next_detail = f"Index {len(sources)} source file(s) for {tr.get('title', tr['slug'])} in 15–20p chunks."
    elif not milestones.get("probed"):
        next_action = "probe_frontier"
        next_detail = f"Run Phase 1 binary-search frontier probe for {tr.get('title', tr['slug'])}."
    elif in_prog_topics:
        t = in_prog_topics[0]
        next_action = "teach_topic"
        refs = ", ".join(f"{r.get('sourceId', 'src')} p.{r.get('pages', '?')}" for r in (t.get("sourceRefs") or [])[:2])
        next_detail = f"Continue topic '{t.get('title', t.get('id'))}'" + (f" ({refs})" if refs else "")
        next_ref = {"topicId": t.get("id"), "level": t.get("level"), "sourceRefs": t.get("sourceRefs") or []}
    elif due_retests:
        q = due_retests[0]
        next_action = "retest_question"
        pg_note = f" (p.{q['page']})" if q.get("page") is not None else ""
        next_detail = f"Retest {q.get('label') or q.get('qid')}{pg_note}"
        next_ref = {"qid": q.get("qid"), "label": q.get("label"), "page": q.get("page"), "topicId": q.get("topicId")}
    elif unseen_mastered:
        q = unseen_mastered[0]
        next_action = "solve_question"
        pg_note = f" (p.{q['page']})" if q.get("page") is not None else ""
        next_detail = f"Solve {q.get('label') or q.get('qid')}{pg_note}"
        next_ref = {"qid": q.get("qid"), "label": q.get("label"), "page": q.get("page"), "topicId": q.get("topicId")}
    elif ready_pending_topics:
        t = ready_pending_topics[0]
        next_action = "teach_topic"
        refs = ", ".join(f"{r.get('sourceId', 'src')} p.{r.get('pages', '?')}" for r in (t.get("sourceRefs") or [])[:2])
        next_detail = f"Teach next topic '{t.get('title', t.get('id'))}'" + (f" ({refs})" if refs else "")
        next_ref = {"topicId": t.get("id"), "level": t.get("level"), "sourceRefs": t.get("sourceRefs") or []}
    elif any_retest:
        q = any_retest[0]
        next_action = "retest_question"
        pg_note = f" (p.{q['page']})" if q.get("page") is not None else ""
        next_detail = f"Clear remaining retest {q.get('label') or q.get('qid')}{pg_note}"
        next_ref = {"qid": q.get("qid"), "label": q.get("label"), "page": q.get("page")}
    elif any_unseen:
        q = any_unseen[0]
        next_action = "solve_question"
        pg_note = f" (p.{q['page']})" if q.get("page") is not None else ""
        next_detail = f"Solve remaining question {q.get('label') or q.get('qid')}{pg_note}"
        next_ref = {"qid": q.get("qid"), "label": q.get("label"), "page": q.get("page")}
    elif not milestones.get("revisionSheetBuilt"):
        next_action = "build_revision_sheet"
        if jee_mode:
            next_detail = f"Compile 1–2p Typst formula & trap sheet for {tr.get('title', tr['slug'])}."
        else:
            next_detail = f"Compile 1–2p Typst mastery & pitfall sheet for {tr.get('title', tr['slug'])}."
    elif not milestones.get("cbtMockPassed"):
        next_action = "run_cbt_mock"
        if jee_mode:
            next_detail = f"Take and debrief timed NTA JEE CBT mock for {tr.get('title', tr['slug'])}."
        else:
            next_detail = f"Take and debrief timed mastery assessment for {tr.get('title', tr['slug'])}."

    return {
        "slug": str(tr.get("slug")),
        "title": str(tr.get("title") or tr.get("slug")),
        "subject": str(tr.get("subject") or "General"),
        "active": bool(tr.get("active")),
        "jeeProfile": jee_mode,
        "scopeCharter": scope_charter,
        "trackerPath": str(path),
        "topicsMastered": topics_mastered,
        "topicsTotal": len(topics),
        "questionsDone": q_done,
        "questionsTotal": len(questions),
        "questionsRetest": q_retest,
        "dailyWarmupLimit": int(config.get("dailyWarmupLimit") or 3),
        "openDoubts": len(tr.get("doubtStack") or []),
        "updatedAt": tr.get("updatedAt"),
        "nextBreadcrumb": norm_tr.get("nextBreadcrumb"),
        "nextAction": next_action,
        "nextDetail": next_detail,
        "nextRef": next_ref,
    }


def allocate_time_blocks(total_minutes: int, days_to_exam: int | None = None) -> tuple[int, int, int]:
    """Allocate integer minutes across (Block 1 Warmup, Block 2 Core Deep Work, Block 3 Interleaved Drill).

    Guarantees `b1 + b2 + b3 == total_minutes` and stays strictly within cognitive-load
    ratios: Block 1 = 15-20%, Block 2 = 50-60%, Block 3 = 25-30% (for total >= 15).
    """
    total = max(3, int(total_minutes))
    if total < 15:
        b1 = max(1, round(total * 0.20))
        b3 = max(1, round(total * 0.27))
        b2 = total - b1 - b3
        return b1, b2, b3

    if days_to_exam is not None and 0 <= days_to_exam <= 30:
        r1, r3 = 0.20, 0.28
    else:
        r1, r3 = 0.17, 0.27

    b1 = max(1, int(round(total * r1)))
    b3 = max(1, int(round(total * r3)))
    b2 = total - b1 - b3

    min_b2 = int(total * 0.50)
    if b2 < min_b2 and b3 > 1:
        shift = min_b2 - b2
        b3 = max(1, b3 - shift)
        b2 = total - b1 - b3

    return b1, b2, b3


def _default_target_label(cfg: dict[str, Any], active_tracker: dict[str, Any] | None) -> str:
    explicit = str(cfg.get("targetExam") or cfg.get("targetGoal") or "").strip()
    if explicit:
        return explicit
    if active_tracker:
        if active_tracker.get("jeeProfile"):
            return "JEE Main + Advanced"
        gp = str((active_tracker.get("scopeCharter") or {}).get("goalProfile") or "general")
        subj = str(active_tracker.get("subject") or "General")
        if gp == "coding":
            return f"{subj} · Technical & Systems Mastery" if subj != "General" else "Technical & Systems Mastery"
        if gp == "course":
            return f"{subj} · Course Mastery" if subj != "General" else "Course Mastery"
        if gp == "research":
            return f"{subj} · Research Mastery" if subj != "General" else "Research & First-Principles Mastery"
        if gp == "exam":
            return f"{subj} · Timed Exam Mastery" if subj != "General" else "Timed Exam Mastery"
        return f"{subj} · Deep Mastery" if subj != "General" else "Deep Mastery"
    return "Deep Mastery"


def _build_contrast_triplet(
    confusables: list[dict[str, Any]],
    combos: list[dict[str, Any]],
    due_items: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Build a deterministic `[A, B, A']` interleaved contrast triplet (strictly prohibiting `A, A, A`)."""
    if confusables:
        top_cf = confusables[0]
        a = top_cf["nodeA"]
        b = top_cf["nodeB"]
        return {
            "type": "interleaved_contrast_triplet",
            "pattern": "A-B-A'",
            "source": "confusables",
            "triplet": [
                {"slot": "A", "propId": a["id"], "title": a["title"], "chapter": a["chapter"]},
                {"slot": "B", "propId": b["id"], "title": b["title"], "chapter": b["chapter"]},
                {"slot": "A'", "propId": a["id"], "title": f"{a['title']} (fresh surface variant)", "chapter": a["chapter"]},
            ],
            "confusableScore": top_cf["confusableScore"],
            "description": (
                f"Interleaved [A, B, A'] contrast triplet: `{a['id']}` -> `{b['id']}` -> fresh variant of `{a['id']}` "
                f"(disguise rule selection; verify governing validity premise before solving)."
            ),
        }

    if combos:
        top_cb = combos[0]
        a = top_cb["nodeA"]
        b = top_cb["nodeB"]
        return {
            "type": "interleaved_contrast_triplet",
            "pattern": "A-B-A'",
            "source": "combos",
            "triplet": [
                {"slot": "A", "propId": a["id"], "title": a["title"], "chapter": a["chapter"]},
                {"slot": "B", "propId": b["id"], "title": b["title"], "chapter": b["chapter"]},
                {"slot": "A'", "propId": a["id"], "title": f"{a['title']} (fresh surface variant)", "chapter": a["chapter"]},
            ],
            "description": (
                f"Interleaved [A, B, A'] transfer triplet: `{a['id']}` ({a['chapter']}) -> `{b['id']}` ({b['chapter']}) "
                f"-> fresh variant of `{a['id']}`."
            ),
        }

    if len(due_items) >= 2:
        a = due_items[0]
        b = next((it for it in due_items[1:] if it["id"] != a["id"] and it["chapter"] != a["chapter"]), due_items[1])
        return {
            "type": "interleaved_contrast_triplet",
            "pattern": "A-B-A'",
            "source": "due_items",
            "triplet": [
                {"slot": "A", "propId": a["id"], "title": a["title"], "chapter": a["chapter"]},
                {"slot": "B", "propId": b["id"], "title": b["title"], "chapter": b["chapter"]},
                {"slot": "A'", "propId": a["id"], "title": f"{a['title']} (fresh surface variant)", "chapter": a["chapter"]},
            ],
            "description": (
                f"Interleaved [A, B, A'] recall triplet: `{a['id']}` -> `{b['id']}` -> fresh variant of `{a['id']}`."
            ),
        }

    return None


def build_micro_plan(
    *,
    minutes: int = 5,
    kg_dir: Path | str | None = None,
    chapters_dir: Path | str | None = None,
    today: str | None = None,
) -> dict[str, Any]:
    """Build a calm 3-5 item micro-review sprint (default 5 min) for commute or quick tune-ups."""
    resolved_kg = resolve_kg_dir(kg_dir)
    resolved_chapters = resolve_chapters_dir(chapters_dir)
    today_date, now_dt = _parse_today(today)
    today_iso = today_date.isoformat()
    micro_min = max(3, min(15, int(minutes)))
    max_items = min(5, max(3, micro_min))

    props = _load_jsonl(resolved_kg / "propositions.jsonl")
    chains = _load_jsonl(resolved_kg / "chains.jsonl")
    due_items = _compute_due_items(props, today_iso, now_dt, kg_dir=resolved_kg)
    comp = compress_due_queue(due_items, chains)
    compressed_due = comp["selected"]

    G, _gp, _gc = graph_intel.load_knowledge_graph(resolved_kg)
    bottlenecks = graph_intel.analyze_bottlenecks(G, top=3)
    confusables = graph_intel.find_confusables(G, kg_dir=resolved_kg, top=3)

    mistakes_rows = mistake_analytics.load_mistakes(resolved_kg)
    mistake_summary = mistake_analytics.summarize_mistakes(mistakes_rows, top=2, today=today_iso)
    active_tracker = _inspect_active_tracker(resolved_chapters)

    sprint_items: list[dict[str, Any]] = []
    seen_props: set[str] = set()

    # Slot 1: Highest-leverage bottleneck or FIRe-compressed recall
    if compressed_due:
        top_due = compressed_due[0]
        seen_props.add(top_due["id"])
        ko_note = f" · knocks out {top_due['knocksOutCount']} prereq(s)" if top_due.get("knocksOutCount") else ""
        sprint_items.append(
            {
                "slot": len(sprint_items) + 1,
                "type": "fire_compressed_recall" if top_due.get("knocksOutCount") else "bottleneck_recall",
                "propId": top_due["id"],
                "chapter": top_due["chapter"],
                "status": top_due["status"],
                "knocksOut": top_due.get("knocksOut", []),
                "description": f"Recall `{top_due['id']}` ({top_due['chapter']} · {top_due['status']}{ko_note}): {top_due['title']}",
            }
        )
    elif bottlenecks:
        bn = bottlenecks[0]
        seen_props.add(bn["id"])
        sprint_items.append(
            {
                "slot": len(sprint_items) + 1,
                "type": "bottleneck_recall",
                "propId": bn["id"],
                "chapter": bn["chapter"],
                "status": bn["status"],
                "description": f"Bottleneck check `{bn['id']}` (blocks {bn['downstreamDescendants']} nodes): {bn['title']}",
            }
        )

    # Slot 2: Confusable or repeat-trap discrimination check
    if confusables and len(sprint_items) < max_items:
        cf = confusables[0]
        sprint_items.append(
            {
                "slot": len(sprint_items) + 1,
                "type": "trap_discrimination",
                "nodes": [cf["nodeA"]["id"], cf["nodeB"]["id"]],
                "pattern": "A-B-A'",
                "description": cf["contrastPrompt"],
            }
        )
    elif mistake_summary.get("repeatTraps") and len(sprint_items) < max_items:
        rep = mistake_summary["repeatTraps"][0]
        sprint_items.append(
            {
                "slot": len(sprint_items) + 1,
                "type": "trap_discrimination",
                "chapter": rep["chapter"],
                "category": rep["category"],
                "description": f"Trap check ({rep['count']}x in {rep['chapter']}): {rep['actionCue']}",
            }
        )
    elif mistake_summary.get("topCategories") and len(sprint_items) < max_items:
        cat = mistake_summary["topCategories"][0]
        rem = mistake_analytics.CATEGORY_META.get(cat["category"], {}).get("remediation", "")
        sprint_items.append(
            {
                "slot": len(sprint_items) + 1,
                "type": "trap_discrimination",
                "category": cat["category"],
                "description": f"Quick trap check [{cat['label']}]: {rem}",
            }
        )

    # Remaining slots: next highest-urgency compressed due items
    for due in compressed_due:
        if len(sprint_items) >= max_items:
            break
        if due["id"] in seen_props:
            continue
        seen_props.add(due["id"])
        sprint_items.append(
            {
                "slot": len(sprint_items) + 1,
                "type": "quick_recall",
                "propId": due["id"],
                "chapter": due["chapter"],
                "status": due["status"],
                "description": f"Rapid recall `{due['id']}` ({due['chapter']} · {due['status']}): {due['title']}",
            }
        )

    if not sprint_items and active_tracker:
        sprint_items.append(
            {
                "slot": 1,
                "type": "active_chapter_micro",
                "slug": active_tracker["slug"],
                "description": f"5-min check on `{active_tracker['title']}`: {active_tracker['nextDetail']}",
            }
        )
    elif not sprint_items:
        sprint_items.append(
            {
                "slot": 1,
                "type": "baseline_micro",
                "description": "Zero overdue cues — pick 1 recent concept and state its governing invariant from memory.",
            }
        )

    blocks = [
        {
            "id": "micro_sprint",
            "order": 1,
            "title": f"{micro_min}-Minute Micro-Review Sprint",
            "minutes": micro_min,
            "sharePct": 100.0,
            "focus": "High-ROI recall + trap discrimination in under 5 minutes (FIRe-compressed).",
            "tasks": sprint_items,
        }
    ]

    return {
        "brand": "Syntopik",
        "mode": "micro",
        "date": today_iso,
        "totalMinutes": micro_min,
        "allocatedMinutes": micro_min,
        "exam": {
            "targetExam": _default_target_label({}, active_tracker),
            "examDate": None,
            "daysToExam": None,
            "phase": "micro_sprint",
            "configFound": False,
        },
        "activeChapter": active_tracker,
        "signals": {
            "dueCount": len(due_items),
            "compressedDueCount": comp["compressedCount"],
            "fireKnockouts": comp["knockedOutCount"],
            "bottleneckCount": len(bottlenecks),
            "topBottleneckId": bottlenecks[0]["id"] if bottlenecks else None,
            "confusableCount": len(confusables),
            "mistakeTotal": mistake_summary["total"],
            "topMistakeCategory": mistake_summary["topCategories"][0]["category"] if mistake_summary["topCategories"] else None,
        },
        "items": sprint_items,
        "blocks": blocks,
        "suggestedPrompt": f"Run my {micro_min}-minute micro-review sprint ({len(sprint_items)} high-ROI cues).",
    }


VALID_ENERGY_MODES: tuple[str, ...] = ("low", "normal", "deep")


def _extract_iso_date(raw: Any) -> date_cls | None:
    if not isinstance(raw, str) or len(raw.strip()) < 10:
        return None
    try:
        return date_cls.fromisoformat(raw.strip()[:10])
    except ValueError:
        return None


def _compute_days_absent(
    props: list[dict[str, Any]],
    revlog_rows: list[dict[str, Any]],
    active_tracker: dict[str, Any] | None,
    today_date: date_cls,
) -> tuple[int, str | None]:
    """Compute calendar days absent from latest revlog.jsonl, propositions.jsonl, or active tracker."""
    dates: list[date_cls] = []
    for r in revlog_rows:
        if isinstance(r, dict):
            d = _extract_iso_date(r.get("ts"))
            if d is not None and d <= today_date:
                dates.append(d)
    for p in props:
        if not isinstance(p, dict):
            continue
        for k in ("lastReviewed", "lastTested", "lastImplicitReview"):
            d = _extract_iso_date(p.get(k))
            if d is not None and d <= today_date:
                dates.append(d)
        fsrs_d = p.get("fsrs")
        if isinstance(fsrs_d, dict):
            d = _extract_iso_date(fsrs_d.get("last_review"))
            if d is not None and d <= today_date:
                dates.append(d)
    if active_tracker:
        d = _extract_iso_date(active_tracker.get("updatedAt"))
        if d is not None and d <= today_date:
            dates.append(d)
    if not dates:
        return 0, None
    latest = max(dates)
    return max(0, (today_date - latest).days), latest.isoformat()


def _select_warm_start_prop(
    props: list[dict[str, Any]],
    now_dt: datetime,
    kg_dir: Path | None = None,
) -> dict[str, Any] | None:
    """Select 1 warm-start cue with `0.60 <= R <= 0.85` (closest to 0.75) for a genuine first win."""
    params = load_fsrs_parameters(kg_dir)
    in_band: list[tuple[float, int, str, dict[str, Any]]] = []
    fallback: list[tuple[float, int, str, dict[str, Any]]] = []

    for p in props:
        if p.get("archived") is True:
            continue
        pid = str(p.get("id") or "").strip()
        if not pid:
            continue
        status = str(p.get("status") or "planned").lower()
        if status in ("misconception", "planned"):
            continue
        if isinstance(p.get("fsrs"), dict) and isinstance(p["fsrs"].get("stability"), (int, float)):
            r_val = float(card_retrievability(p["fsrs"], now_dt, parameters=params, fractional_days=True))
        elif status == "solid":
            r_val = 0.78
        else:
            r_val = 0.55

        entry = {
            "id": pid,
            "title": str(p.get("title") or pid),
            "chapter": str(p.get("chapter") or "General"),
            "status": status,
            "retrievability": round(r_val, 3),
        }
        status_pref = 0 if status == "solid" else 1
        dist = abs(r_val - 0.75)
        if 0.60 <= r_val <= 0.85:
            in_band.append((dist, status_pref, pid, entry))
        else:
            fallback.append((dist, status_pref, pid, entry))

    if in_band:
        in_band.sort(key=lambda item: (item[0], item[1], item[2]))
        return in_band[0][3]
    if fallback:
        fallback.sort(key=lambda item: (item[0], item[1], item[2]))
        return fallback[0][3]
    return None


def _build_reentry_brief(
    props: list[dict[str, Any]],
    revlog_rows: list[dict[str, Any]],
    active_tracker: dict[str, Any] | None,
    today_date: date_cls,
    now_dt: datetime,
    kg_dir: Path | None,
    *,
    force_resume: bool = False,
) -> dict[str, Any] | None:
    days_absent, last_active = _compute_days_absent(props, revlog_rows, active_tracker, today_date)
    if days_absent < 4 and not force_resume:
        return None

    # Identify the most recently touched solid proposition (last mastered unconditional truth).
    solid_dated: list[tuple[str, str, dict[str, Any]]] = []
    for p in props:
        if not isinstance(p, dict) or str(p.get("status") or "").lower() != "solid":
            continue
        dt_str = str(
            p.get("lastReviewed")
            or p.get("lastTested")
            or (p.get("fsrs", {}).get("last_review") if isinstance(p.get("fsrs"), dict) else "")
            or ""
        )[:10]
        solid_dated.append((dt_str, str(p.get("id") or ""), p))
    solid_dated.sort(key=lambda item: (item[0], item[1]), reverse=True)
    last_mastered_truth: str | None = None
    if solid_dated:
        top_p = solid_dated[0][2]
        last_mastered_truth = f"{top_p.get('title') or top_p.get('id')} ({top_p.get('id')})"
    elif active_tracker:
        last_mastered_truth = f"{active_tracker['title']} ({active_tracker['topicsMastered']}/{active_tracker['topicsTotal']} topics mastered)"

    breadcrumb = active_tracker.get("nextBreadcrumb") if active_tracker else None
    warm = _select_warm_start_prop(props, now_dt, kg_dir=kg_dir)

    reload_bullets: list[str] = []
    if last_mastered_truth:
        reload_bullets.append(f"Last mastered truth: {last_mastered_truth}")
    if isinstance(breadcrumb, dict) and breadcrumb.get("summary"):
        bc_line = f"Where you stopped: {breadcrumb['summary']}"
        if breadcrumb.get("firstMicroAction"):
            bc_line += f" -> First step: {breadcrumb['firstMicroAction']}"
        reload_bullets.append(bc_line)
    elif active_tracker:
        reload_bullets.append(f"Resume point: {active_tracker['nextDetail']}")
    if warm:
        reload_bullets.append(
            f"Warm-start first win (R≈{round(warm['retrievability'] * 100)}%): `{warm['id']}` — {warm['title']}"
        )

    return {
        "daysAbsent": days_absent,
        "lastActiveDate": last_active,
        "suppressOverdueCount": True,
        "lastMasteredTruth": last_mastered_truth,
        "breadcrumb": breadcrumb,
        "warmStartPropId": warm["id"] if warm else None,
        "warmStartTitle": warm["title"] if warm else None,
        "warmStartRetrievability": warm["retrievability"] if warm else None,
        "reloadBullets": reload_bullets,
    }


def build_triage_plan(
    *,
    hours_left: float = 48.0,
    sleep_hours: float = 7.0,
    kg_dir: Path | str | None = None,
    chapters_dir: Path | str | None = None,
    chapter: str | None = None,
    today: str | None = None,
) -> dict[str, Any]:
    """48-Hour Pareto Exam Triage: protect sleep, allocate 70% to high-leverage salvage,
    20% to protecting solid marks, 10% to 1-page Anti-Mastery crib sheet, and explicitly cut cold leaves.
    """
    resolved_kg = resolve_kg_dir(kg_dir)
    resolved_chapters = resolve_chapters_dir(chapters_dir)
    today_date, now_dt = _parse_today(today)
    today_iso = today_date.isoformat()

    h_left = max(1.0, float(hours_left))
    sleep_per_night = max(0.0, min(12.0, float(sleep_hours)))
    nights = int(h_left // 24.0) if h_left >= 24.0 else (1 if h_left >= 14.0 else 0)
    sleep_protected = round(min(h_left * 0.45, nights * sleep_per_night), 1)
    awake_hours = max(1.0, h_left - sleep_protected)
    usable_hours = round(max(1.0, min(awake_hours * 0.65, (h_left / 24.0) * 11.0)), 1)
    usable_minutes = max(30, int(round(usable_hours * 60.0)))

    props = _load_jsonl(resolved_kg / "propositions.jsonl")
    mistakes = mistake_analytics.load_mistakes(resolved_kg)
    params = load_fsrs_parameters(resolved_kg)
    G, _gp, _gc = graph_intel.load_knowledge_graph(resolved_kg)
    active_tracker = _inspect_active_tracker(resolved_chapters)

    ch_filter = str(chapter or "").strip().lower()
    if ch_filter:
        props = [
            p
            for p in props
            if ch_filter in str(p.get("chapter") or "").lower()
            or ch_filter in str(p.get("id") or "").lower()
            or ch_filter in str(p.get("subject") or "").lower()
        ]

    trap_hits_by_prop: dict[str, int] = {}
    for m in mistakes:
        pid = str(m.get("propId") or "").strip()
        if pid:
            trap_hits_by_prop[pid] = trap_hits_by_prop.get(pid, 0) + 1

    salvage_raw: list[dict[str, Any]] = []
    protect_raw: list[dict[str, Any]] = []
    cut_raw: list[dict[str, Any]] = []

    for p in props:
        if p.get("archived") is True:
            continue
        pid = str(p.get("id") or "").strip()
        if not pid:
            continue
        status = str(p.get("status") or "planned").lower()
        lapses = int(p.get("lapses") or 0)
        trap_hits = trap_hits_by_prop.get(pid, 0)
        bkt_raw = p.get("bktMastery")
        eff_bkt = float(bkt_raw) if isinstance(bkt_raw, (int, float)) else (
            0.85 if status == "solid" else (0.15 if status == "misconception" else (0.35 if status == "shaky" else 0.20))
        )
        if isinstance(p.get("fsrs"), dict) and isinstance(p["fsrs"].get("stability"), (int, float)):
            r_val = float(card_retrievability(p["fsrs"], now_dt, parameters=params, fractional_days=True))
        elif status == "solid":
            r_val = 0.85
        elif status == "misconception":
            r_val = 0.15
        elif status == "shaky":
            r_val = 0.40
        else:
            r_val = 0.0

        leverage = len(graph_intel._graph_descendants(G, pid)) if pid in G else 0

        if status == "planned" and leverage == 0 and trap_hits == 0:
            cut_raw.append(
                {
                    "propId": pid,
                    "title": str(p.get("title") or pid),
                    "chapter": str(p.get("chapter") or "General"),
                    "leverage": 0,
                    "reason": f"cold leaf (0 downstream deps), low ROI in {int(round(h_left))}h",
                }
            )
        elif status in ("misconception", "shaky") or eff_bkt < 0.65 or trap_hits >= 1 or (status == "planned" and leverage >= 1):
            roi_score = (1.0 + leverage) * max(0.15, 1.0 - eff_bkt) + 1.5 * trap_hits + (1.2 if status == "misconception" else 0.0)
            salvage_raw.append(
                {
                    "propId": pid,
                    "title": str(p.get("title") or pid),
                    "chapter": str(p.get("chapter") or "General"),
                    "status": status,
                    "leverage": leverage,
                    "bktMastery": round(eff_bkt, 3),
                    "trapHits": trap_hits,
                    "roiScore": round(roi_score, 3),
                }
            )
        elif status == "solid" and r_val >= 0.92 and lapses == 0 and trap_hits == 0 and leverage == 0:
            cut_raw.append(
                {
                    "propId": pid,
                    "title": str(p.get("title") or pid),
                    "chapter": str(p.get("chapter") or "General"),
                    "leverage": leverage,
                    "retrievability": round(r_val, 3),
                    "reason": "already locked in (R>=92%, 0 lapses, leaf) — skip during triage",
                }
            )
        else:
            protect_raw.append(
                {
                    "propId": pid,
                    "title": str(p.get("title") or pid),
                    "chapter": str(p.get("chapter") or "General"),
                    "status": status,
                    "leverage": leverage,
                    "retrievability": round(r_val, 3),
                }
            )

    salvage_raw.sort(key=lambda x: (-x["roiScore"], -x["leverage"], -x["trapHits"], x["propId"]))
    protect_raw.sort(key=lambda x: (x["retrievability"], -x["leverage"], x["propId"]))
    cut_raw.sort(key=lambda x: (x["leverage"], x["propId"]))

    salvage_budget = max(20, int(round(usable_minutes * 0.70)))
    protect_budget = max(10, int(round(usable_minutes * 0.20)))
    crib_budget = max(10, usable_minutes - salvage_budget - protect_budget)

    # Cap salvage list to realistic high-yield slots (>= 15m each) and move overflow leaves to cut.
    max_salvage = max(1, min(len(salvage_raw), salvage_budget // 15))
    salvage_keep = salvage_raw[:max_salvage]
    for overflow in salvage_raw[max_salvage:]:
        cut_raw.append(
            {
                "propId": overflow["propId"],
                "title": overflow["title"],
                "chapter": overflow["chapter"],
                "leverage": overflow["leverage"],
                "reason": f"lower Pareto ROI than top {max_salvage} salvage targets in {int(round(h_left))}h",
            }
        )

    total_roi = sum(item["roiScore"] for item in salvage_keep) or 1.0
    assigned = 0
    for idx, item in enumerate(salvage_keep):
        if idx == len(salvage_keep) - 1:
            alloc = max(10, salvage_budget - assigned)
        else:
            alloc = max(15, int(round(salvage_budget * (item["roiScore"] / total_roi))))
            assigned += alloc
        item["allocatedMin"] = alloc

    # Build hard-cutoff 90m blocks + sleep protection + final Anti-Mastery 1-page crib sheet slot.
    blocks: list[dict[str, Any]] = []
    block_num = 1
    rem_salvage = salvage_budget
    s_cursor = 0
    while rem_salvage > 0 and block_num <= 12:
        slot_min = min(90, rem_salvage)
        target_slice = salvage_keep[s_cursor : s_cursor + 2]
        s_cursor = (s_cursor + 2) % len(salvage_keep) if salvage_keep else 0
        goal_ids = ", ".join(x["propId"] for x in target_slice) if target_slice else "top bottleneck invariants"
        blocks.append(
            {
                "order": block_num,
                "slot": f"Block {block_num} ({slot_min}m)",
                "minutes": slot_min,
                "bucket": "salvage_70pct",
                "hardCutoff": True,
                "goal": f"Salvage high-yield gaps & traps: {goal_ids} (hard cutoff at {slot_min}m — no rabbit holes).",
            }
        )
        rem_salvage -= slot_min
        block_num += 1

    if protect_budget > 0:
        prot_ids = ", ".join(x["propId"] for x in protect_raw[:4]) if protect_raw else "solid core formulas"
        blocks.append(
            {
                "order": block_num,
                "slot": f"Block {block_num} ({min(90, protect_budget)}m)",
                "minutes": protect_budget,
                "bucket": "protect_20pct",
                "hardCutoff": True,
                "goal": f"Rapid active-recall refresh on solid marks: {prot_ids}.",
            }
        )
        block_num += 1

    blocks.append(
        {
            "order": block_num,
            "slot": f"Block {block_num} ({crib_budget}m)",
            "minutes": crib_budget,
            "bucket": "anti_mastery_crib",
            "hardCutoff": True,
            "goal": "Compile 1-page Anti-Mastery Crib Sheet (`cheatsheet_pdf.py build --from-kg --anti-mastery --pages 1`) & do final trap walk-through.",
        }
    )

    return {
        "brand": "Syntopik",
        "mode": "exam_triage",
        "date": today_iso,
        "chapterFilter": chapter or None,
        "hoursLeft": round(h_left, 1),
        "usableStudyHours": usable_hours,
        "sleepProtectedHours": sleep_protected,
        "sleepHoursPerNight": sleep_per_night,
        "buckets": {
            "salvage_70pct": salvage_keep,
            "protect_20pct": protect_raw[:15],
            "explicitly_cut": cut_raw[:25],
        },
        "blocks": blocks,
        "activeChapter": active_tracker,
        "signals": {
            "salvageCount": len(salvage_keep),
            "protectCount": len(protect_raw),
            "cutCount": len(cut_raw),
            "mistakeTotal": len(mistakes),
        },
        "suggestedPrompt": (
            f"Run my {int(round(h_left))}h Pareto exam triage ({usable_hours}h usable, {sleep_protected}h sleep protected): "
            f"start Block 1 salvage and build my 1-page Anti-Mastery crib sheet."
        ),
    }


def _load_fatigue_cliff(kg_dir: Path) -> dict[str, Any] | None:
    """Read `sessionTargetMinutes` and fatigue affect state from `learner-profile.json` if present."""
    profile = _load_json_safe(kg_dir / "learner-profile.json")
    if not isinstance(profile, dict):
        return None
    target_min: int | None = None
    candidates = [
        profile.get("sessionTargetMinutes"),
        (profile.get("compiledPolicy") or {}).get("sessionTargetMinutes") if isinstance(profile.get("compiledPolicy"), dict) else None,
        (profile.get("calibration") or {}).get("sessionTargetMinutes") if isinstance(profile.get("calibration"), dict) else None,
        (profile.get("calibration") or {}).get("sessionMinutes") if isinstance(profile.get("calibration"), dict) else None,
        (profile.get("preferences") or {}).get("sessionTargetMinutes") if isinstance(profile.get("preferences"), dict) else None,
        (profile.get("userOverrides") or {}).get("sessionTargetMinutes") if isinstance(profile.get("userOverrides"), dict) else None,
    ]
    for cand in candidates:
        if isinstance(cand, (int, float)) and not isinstance(cand, bool) and int(cand) >= 5:
            target_min = int(cand)
            break

    affect_state = ""
    if isinstance(profile.get("affect"), dict):
        affect_state = str(profile["affect"].get("state") or "").strip().lower()
    elif isinstance(profile.get("affectiveState"), dict):
        affect_state = str(profile["affectiveState"].get("state") or "").strip().lower()
    elif isinstance(profile.get("compiledPolicy"), dict):
        affect_state = str(profile["compiledPolicy"].get("affectState") or "").strip().lower()

    if target_min is None and not affect_state:
        return None
    return {
        "sessionTargetMinutes": target_min,
        "affectState": affect_state or None,
        "isFatigued": affect_state == "fatigued",
    }


def _cap_blocks_for_fatigue(
    blocks: list[dict[str, Any]],
    max_block_min: int,
) -> tuple[list[dict[str, Any]], bool]:
    """Split any continuous study block exceeding `max_block_min` so every block respects the fatigue cliff."""
    cap = max(5, int(max_block_min))
    capped_any = False
    out: list[dict[str, Any]] = []
    total_all = sum(int(b.get("minutes") or 0) for b in blocks) or 1

    for blk in blocks:
        mins = int(blk.get("minutes") or 0)
        if mins <= cap:
            out.append(dict(blk))
            continue
        capped_any = True
        rem = mins
        part = 1
        total_parts = (mins + cap - 1) // cap
        tasks = list(blk.get("tasks") or [])
        while rem > 0:
            chunk = min(cap, rem)
            rem -= chunk
            sub = dict(blk)
            sub["id"] = f"{blk.get('id', 'block')}_p{part}"
            sub["title"] = f"{blk.get('title', 'Study Block')} (Part {part}/{total_parts} · fatigue cap {cap}m)"
            sub["minutes"] = chunk
            sub["sharePct"] = round((chunk / total_all) * 100.0, 1)
            sub["fatigueCapped"] = True
            if part == 1:
                sub["tasks"] = tasks
            else:
                sub["tasks"] = [
                    {
                        "type": "fatigue_break_continue",
                        "description": f"Take a 5-min eyes-off-screen reset, then resume {blk.get('title', 'block')} for {chunk}m.",
                    }
                ]
            out.append(sub)
            part += 1

    for idx, b in enumerate(out, start=1):
        b["order"] = idx
    return out, capped_any


def build_reentry_only(
    kg_dir: Path | str | None = None,
    chapters_dir: Path | str | None = None,
    *,
    today: str | None = None,
    resume: bool = False,
) -> dict[str, Any] | None:
    """Just the re-entry brief from `build_daily_plan`, without the graph analysis.

    The Studio's `/api/state` only needs this part; the full plan's bottleneck,
    combo and confusable scans take seconds on large vaults.
    """
    resolved_kg = resolve_kg_dir(kg_dir)
    resolved_chapters = resolve_chapters_dir(chapters_dir)
    today_date, now_dt = _parse_today(today)
    return _build_reentry_brief(
        _load_jsonl(resolved_kg / "propositions.jsonl"),
        _load_jsonl(resolved_kg / "revlog.jsonl"),
        _inspect_active_tracker(resolved_chapters),
        today_date,
        now_dt,
        resolved_kg,
        force_resume=bool(resume),
    )


def _nearest_listed_exam(cfg: dict[str, Any], today_date: date_cls) -> tuple[str, str] | None:
    """Earliest not-yet-past `(date, name)` in `exams[]` (the format `syntopik pace --add-exam` writes)."""
    best: date_cls | None = None
    best_name = ""
    for ex in cfg.get("exams") or []:
        if not isinstance(ex, dict):
            continue
        raw = str(ex.get("examDate") or ex.get("date") or "").strip()
        try:
            d = date_cls.fromisoformat(raw[:10])
        except ValueError:
            continue
        if d >= today_date and (best is None or d < best):
            best = d
            best_name = str(ex.get("name") or ex.get("title") or ex.get("subject") or "").strip()
    return (best.isoformat(), best_name) if best else None


def build_daily_plan(
    *,
    minutes: int | None = None,
    kg_dir: Path | str | None = None,
    chapters_dir: Path | str | None = None,
    config_path: Path | str | None = None,
    today: str | None = None,
    energy: str = "normal",
    resume: bool = False,
) -> dict[str, Any]:
    resolved_kg = resolve_kg_dir(kg_dir)
    resolved_chapters = resolve_chapters_dir(chapters_dir)
    today_date, now_dt = _parse_today(today)
    today_iso = today_date.isoformat()

    energy_norm = str(energy or "normal").strip().lower()
    if energy_norm not in VALID_ENERGY_MODES:
        energy_norm = "normal"

    if config_path:
        cfg_file = Path(config_path).expanduser().resolve()
    elif (resolved_kg / "study-config.json").is_file():
        cfg_file = resolved_kg / "study-config.json"
    else:
        cfg_file = resolved_kg / "exam-config.json"
    cfg = _load_json_safe(cfg_file)
    if not isinstance(cfg, dict):
        cfg = {}

    fatigue_info = _load_fatigue_cliff(resolved_kg)
    fatigue_target_min = fatigue_info["sessionTargetMinutes"] if fatigue_info else None
    fatigue_capped_total = False

    exam_date_str = str(cfg.get("examDate") or "").strip() or None
    listed_exam_name = ""
    if not exam_date_str:
        nearest = _nearest_listed_exam(cfg, today_date)
        if nearest:
            exam_date_str, listed_exam_name = nearest
    days_to_exam: int | None = None
    exam_phase = "standard"
    if exam_date_str:
        try:
            ex_date = date_cls.fromisoformat(exam_date_str[:10])
            days_to_exam = (ex_date - today_date).days
            if days_to_exam < 0:
                exam_phase = "post_exam"
            elif days_to_exam <= 30:
                exam_phase = "final_sprint"
            elif days_to_exam <= 90:
                exam_phase = "consolidation"
            else:
                exam_phase = "foundation_building"
        except ValueError:
            days_to_exam = None

    if minutes is not None:
        total_minutes = max(5, int(minutes))
    elif isinstance(cfg.get("dailyMinutes"), (int, float)) and int(cfg["dailyMinutes"]) > 0:
        total_minutes = max(5, int(cfg["dailyMinutes"]))
    else:
        total_minutes = 60

    if minutes is None and fatigue_target_min is not None and total_minutes > fatigue_target_min:
        total_minutes = fatigue_target_min
        fatigue_capped_total = True
    if fatigue_info and fatigue_info.get("isFatigued") and energy_norm == "normal":
        energy_norm = "low"

    props = _load_jsonl(resolved_kg / "propositions.jsonl")
    chains = _load_jsonl(resolved_kg / "chains.jsonl")
    revlog_rows = _load_jsonl(resolved_kg / "revlog.jsonl")
    due_items = _compute_due_items(props, today_iso, now_dt, kg_dir=resolved_kg)
    comp = compress_due_queue(due_items, chains)
    compressed_due = comp["selected"]

    G, _g_props, _g_chains = graph_intel.load_knowledge_graph(resolved_kg)
    bottlenecks = graph_intel.analyze_bottlenecks(G, top=5)
    islands = graph_intel.analyze_islands(G)
    combos = graph_intel.find_combos(G, top=4)
    confusables = graph_intel.find_confusables(G, kg_dir=resolved_kg, top=4)

    mistakes_rows = mistake_analytics.load_mistakes(resolved_kg)
    mistake_summary = mistake_analytics.summarize_mistakes(mistakes_rows, top=3, today=today_iso)

    active_tracker = _inspect_active_tracker(resolved_chapters)
    reentry = _build_reentry_brief(
        props,
        revlog_rows,
        active_tracker,
        today_date,
        now_dt,
        resolved_kg,
        force_resume=bool(resume),
    )

    warmup_cap = active_tracker["dailyWarmupLimit"] if active_tracker else 3
    b1_min, b2_min, b3_min = allocate_time_blocks(total_minutes, days_to_exam=days_to_exam)

    if energy_norm == "low":
        # Low-energy mode: 0 new theory/proofs; max 3 warm-start / faded cues + guilt-free stop.
        warm = _select_warm_start_prop(props, now_dt, kg_dir=resolved_kg)
        b1_tasks = []
        used_low: set[str] = set()
        if warm:
            used_low.add(warm["id"])
            b1_tasks.append(
                {
                    "type": "warm_start_cue",
                    "propId": warm["id"],
                    "chapter": warm["chapter"],
                    "retrievability": warm["retrievability"],
                    "description": (
                        f"Warm-start first win `{warm['id']}` ({warm['chapter']} · R≈{round(warm['retrievability'] * 100)}%): "
                        f"{warm['title']}"
                    ),
                }
            )
        elif compressed_due:
            first_d = compressed_due[0]
            used_low.add(first_d["id"])
            b1_tasks.append(
                {
                    "type": "warm_start_cue",
                    "propId": first_d["id"],
                    "chapter": first_d["chapter"],
                    "description": f"Gentle single recall `{first_d['id']}` ({first_d['chapter']}): {first_d['title']}",
                }
            )
        else:
            b1_tasks.append(
                {
                    "type": "warm_start_cue",
                    "description": "One 60-second retrieval of your last mastered concept — motion creates emotion.",
                }
            )

        b2_tasks = []
        for item in compressed_due:
            if len(b2_tasks) >= 2:
                break
            if item["id"] in used_low:
                continue
            used_low.add(item["id"])
            b2_tasks.append(
                {
                    "type": "faded_parsons_cue",
                    "propId": item["id"],
                    "chapter": item["chapter"],
                    "description": f"Low-friction faded/Parsons cue `{item['id']}` ({item['chapter']}): {item['title']} (zero new theory).",
                }
            )
        if not b2_tasks:
            b2_tasks.append(
                {
                    "type": "faded_parsons_cue",
                    "description": "Zero new theory or proofs today — review 1–2 faded worked steps or listen to a 5-min audio brief.",
                }
            )

        b3_tasks = [
            {
                "type": "guilt_free_stop",
                "description": "Optional 5-min audio brief or save a 1-line `checkpoint` breadcrumb and stop guilt-free.",
            }
        ]
        blocks = [
            {
                "id": "warmup",
                "order": 1,
                "title": "Warm-Start First Win",
                "minutes": b1_min,
                "sharePct": round((b1_min / total_minutes) * 100.0, 1),
                "focus": "Low-energy activation: 1 genuine confidence-building recall (0.60 <= R <= 0.85).",
                "tasks": b1_tasks,
            },
            {
                "id": "deep_work",
                "order": 2,
                "title": "Low-Friction Faded Maintenance (Zero New Theory)",
                "minutes": b2_min,
                "sharePct": round((b2_min / total_minutes) * 100.0, 1),
                "focus": "Strictly 0 new theory/proofs — at most 2 faded/Parsons cues to keep continuity alive.",
                "tasks": b2_tasks,
            },
            {
                "id": "interleaved_drill",
                "order": 3,
                "title": "Guilt-Free Wind-Down & Checkpoint",
                "minutes": b3_min,
                "sharePct": round((b3_min / total_minutes) * 100.0, 1),
                "focus": "Drop a 1-sentence breadcrumb for tomorrow and stop cleanly.",
                "tasks": b3_tasks,
            },
        ]
    else:
        # --- Block 1: FIRe-Compressed Spaced Recall Warmup (15-20%) ---
        b1_tasks = []
        if reentry and reentry.get("warmStartPropId"):
            b1_tasks.append(
                {
                    "type": "warm_start_cue",
                    "propId": reentry["warmStartPropId"],
                    "retrievability": reentry.get("warmStartRetrievability"),
                    "description": (
                        f"Cold-start warm win `{reentry['warmStartPropId']}` "
                        f"(R≈{round((reentry.get('warmStartRetrievability') or 0.75) * 100)}%): "
                        f"{reentry.get('warmStartTitle') or reentry['warmStartPropId']}"
                    ),
                }
            )
        warmup_picks = [
            it for it in compressed_due if not (reentry and it["id"] == reentry.get("warmStartPropId"))
        ][: max(1, warmup_cap - len(b1_tasks))]
        if warmup_picks:
            for item in warmup_picks:
                retr_str = f" (R≈{round(item['retrievability'] * 100)}%)" if item["retrievability"] is not None else ""
                ko_str = f" · FIRe knocks out {item['knocksOutCount']} prereq(s)" if item.get("knocksOutCount") else ""
                task_entry: dict[str, Any] = {
                    "type": "fsrs_recall",
                    "propId": item["id"],
                    "chapter": item["chapter"],
                    "status": item["status"],
                    "description": f"Active-recall check `{item['id']}` ({item['chapter']} · {item['status']}{retr_str}{ko_str}): {item['title']}",
                }
                if item.get("knocksOut"):
                    task_entry["knocksOut"] = item["knocksOut"]
                b1_tasks.append(task_entry)
        elif not b1_tasks:
            b1_tasks.append(
                {
                    "type": "baseline_warmup",
                    "description": "No overdue FSRS nodes — run a 3-question rapid recall from your latest chapter notes or start a new probe.",
                }
            )

        # --- Block 2: Core Active Chapter Autopilot / Bottleneck Deep Work (50-60%) ---
        b2_tasks = []
        if energy_norm == "deep" and bottlenecks:
            top_bn = bottlenecks[0]
            b2_tasks.append(
                {
                    "type": "productive_failure_challenge",
                    "propId": top_bn["id"],
                    "chapter": top_bn["chapter"],
                    "downstreamDescendants": top_bn["downstreamDescendants"],
                    "description": (
                        f"Productive Failure opener on `{top_bn['id']}` ({top_bn['chapter']}, blocks "
                        f"{top_bn['downstreamDescendants']} nodes): attempt rough derivation/RSM before contrasting canonical invariant."
                    ),
                }
            )
        if active_tracker and active_tracker["nextAction"] != "chapter_complete":
            b2_tasks.append(
                {
                    "type": "chapter_autopilot",
                    "slug": active_tracker["slug"],
                    "action": active_tracker["nextAction"],
                    "ref": active_tracker["nextRef"],
                    "description": (
                        f"[{active_tracker['title']} · Topics {active_tracker['topicsMastered']}/{active_tracker['topicsTotal']} · "
                        f"Qs {active_tracker['questionsDone']}/{active_tracker['questionsTotal']}] {active_tracker['nextDetail']}"
                    ),
                }
            )
            if bottlenecks and energy_norm != "deep":
                top_bn = bottlenecks[0]
                b2_tasks.append(
                    {
                        "type": "bottleneck_detour",
                        "propId": top_bn["id"],
                        "chapter": top_bn["chapter"],
                        "downstreamDescendants": top_bn["downstreamDescendants"],
                        "description": (
                            f"Prerequisite check/repair `{top_bn['id']}` ({top_bn['chapter']}, {top_bn['status']}, "
                            f"blocks {top_bn['downstreamDescendants']} downstream node(s)): {top_bn['title']}"
                        ),
                    }
                )
        elif bottlenecks:
            for bn in bottlenecks[:2]:
                if energy_norm == "deep" and b2_tasks and bn["id"] == b2_tasks[0].get("propId"):
                    continue
                b2_tasks.append(
                    {
                        "type": "bottleneck_repair",
                        "propId": bn["id"],
                        "chapter": bn["chapter"],
                        "downstreamDescendants": bn["downstreamDescendants"],
                        "crossChapterReach": bn["crossChapterReach"],
                        "impactScore": bn["impactScore"],
                        "description": (
                            f"Repair high-leverage bottleneck `{bn['id']}` ({bn['chapter']} · {bn['status']} · "
                            f"blocks {bn['downstreamDescendants']} descendants across {bn['crossChapterReach']} other chapter(s)): {bn['title']}"
                        ),
                    }
                )
        elif not b2_tasks:
            b2_tasks.append(
                {
                    "type": "start_chapter",
                    "description": "No active topic tracker or broken bottlenecks — attach your course/paper files or say 'Start <topic>' to launch Syntopik Autopilot.",
                }
            )

        if energy_norm == "deep" and islands:
            top_isl = islands[0]
            b2_tasks.append(
                {
                    "type": "deep_island_derivation",
                    "propId": top_isl["id"],
                    "chapter": top_isl["chapter"],
                    "description": f"First-principles derivation of memorized island `{top_isl['id']}` ({top_isl['chapter']}) from axioms.",
                }
            )

        # --- Block 3: Interleaved Contrast Triplets [A, B, A'], Trap Repair & Combos (25-30%) ---
        jee_context = bool(
            (active_tracker and active_tracker.get("jeeProfile"))
            or ("jee" in str(cfg.get("targetExam") or "").lower())
        )
        coding_context = bool(
            active_tracker
            and (
                str((active_tracker.get("scopeCharter") or {}).get("goalProfile") or "") == "coding"
                or str(active_tracker.get("subject") or "") in {"Computer Science", "Machine Learning", "Systems"}
            )
        )
        b3_tasks = []

        if energy_norm == "deep" and combos:
            for combo in combos[:2]:
                b3_tasks.append(
                    {
                        "type": "cross_chapter_combo",
                        "nodes": [combo["nodeA"]["id"], combo["nodeB"]["id"]],
                        "chapters": combo["chapters"],
                        "synergyScore": combo["synergyScore"],
                        "description": combo["drillPrompt"],
                    }
                )

        triplet_task = _build_contrast_triplet(confusables, combos, due_items)
        if triplet_task is not None:
            b3_tasks.append(triplet_task)

        for cat in mistake_summary.get("topCategories", [])[:2]:
            rem = mistake_analytics.CATEGORY_META.get(cat["category"], {}).get("remediation", "")
            b3_tasks.append(
                {
                    "type": "mistake_category_drill",
                    "category": cat["category"],
                    "count": cat["count"],
                    "description": f"Target top trap `{cat['label']}` ({cat['count']} hits, {cat['pct']}%): {rem}",
                }
            )
        if mistake_summary.get("repeatTraps"):
            rep = mistake_summary["repeatTraps"][0]
            esc_tag = " [3-STRIKE ESCALATION]" if rep.get("escalated") else ""
            b3_tasks.append(
                {
                    "type": "repeat_trap_drill",
                    "key": rep["key"],
                    "chapter": rep["chapter"],
                    "count": rep["count"],
                    "escalated": bool(rep.get("escalated")),
                    "description": f"Discrimination drill on repeat trap ({rep['count']}x in {rep['chapter']}{esc_tag}): {rep['actionCue']}",
                }
            )
        if combos and energy_norm != "deep":
            top_combo = combos[0]
            b3_tasks.append(
                {
                    "type": "cross_chapter_combo",
                    "nodes": [top_combo["nodeA"]["id"], top_combo["nodeB"]["id"]],
                    "chapters": top_combo["chapters"],
                    "synergyScore": top_combo["synergyScore"],
                    "description": top_combo["drillPrompt"],
                }
            )
        if islands and energy_norm != "deep":
            top_isl = islands[0]
            b3_tasks.append(
                {
                    "type": "derive_island",
                    "propId": top_isl["id"],
                    "chapter": top_isl["chapter"],
                    "description": f"Derive memorized island `{top_isl['id']}` ({top_isl['chapter']}) from first principles and link its premise chain.",
                }
            )
        if not b3_tasks:
            interleave_desc = (
                "Run a 3-problem interleaved [A, B, A'] discrimination drill mixing your 2 most recent topics (check invariants, edge cases, and complexity bounds)."
                if coding_context
                else "Run a 3-problem interleaved [A, B, A'] discrimination drill mixing your 2 most recent chapters (check signs, invariants, and validity bounds)."
            )
            b3_tasks.append(
                {
                    "type": "interleaved_practice",
                    "pattern": "A-B-A'",
                    "description": interleave_desc,
                }
            )

        b3_focus = (
            "Deep synthesis: cross-chapter combos + [A, B, A'] discriminative contrast under high freshness."
            if energy_norm == "deep"
            else (
                "Consolidate transfer via [A, B, A'] interleaved contrast triplets, personal mistake traps, and cross-chapter JEE synthesis pairs."
                if jee_context
                else "Consolidate transfer via [A, B, A'] interleaved contrast triplets, personal mistake traps, and multi-concept synthesis pairs."
            )
        )

        blocks = [
            {
                "id": "warmup",
                "order": 1,
                "title": "Spaced Recall Warmup",
                "minutes": b1_min,
                "sharePct": round((b1_min / total_minutes) * 100.0, 1),
                "focus": "Activate FIRe-compressed due/shaky FSRS traces without exhausting peak working memory.",
                "tasks": b1_tasks,
            },
            {
                "id": "deep_work",
                "order": 2,
                "title": (
                    "Deep Focus: Productive Failure & First-Principles Proofs"
                    if energy_norm == "deep"
                    else "Active Autopilot & Bottleneck Deep Work"
                ),
                "minutes": b2_min,
                "sharePct": round((b2_min / total_minutes) * 100.0, 1),
                "focus": (
                    "Peak energy window: Productive Failure openers, island derivations, and high-leverage proofs."
                    if energy_norm == "deep"
                    else "Spend peak freshness on the active topic's exact next concept/unseen problem or highest-leverage bottleneck root."
                ),
                "tasks": b2_tasks,
            },
            {
                "id": "interleaved_drill",
                "order": 3,
                "title": "Interleaved Trap & Synthesis Drill",
                "minutes": b3_min,
                "sharePct": round((b3_min / total_minutes) * 100.0, 1),
                "focus": b3_focus,
                "tasks": b3_tasks,
            },
        ]

    fatigue_blocks_capped = False
    if fatigue_target_min is not None:
        blocks, fatigue_blocks_capped = _cap_blocks_for_fatigue(blocks, fatigue_target_min)

    if energy_norm == "low":
        suggested_prompt = f"Low-energy session ({total_minutes}m): start with my warm-start cue and stop guilt-free after 3 steps."
    elif reentry:
        suggested_prompt = (
            f"Welcome back ({reentry['daysAbsent']}d away) — run my 90s mental-model reload and warm-start cue `{reentry.get('warmStartPropId') or 'next'}`."
        )
    elif active_tracker and active_tracker["nextAction"] != "chapter_complete":
        suggested_prompt = f"Continue {active_tracker['title']} — run today's {total_minutes}-minute {energy_norm}-energy plan."
    elif bottlenecks:
        suggested_prompt = f"Repair bottleneck {bottlenecks[0]['id']} ({bottlenecks[0]['chapter']}) and run today's {total_minutes}-minute plan."
    else:
        suggested_prompt = f"Run today's {total_minutes}-minute study plan."

    target_label = (
        listed_exam_name
        if listed_exam_name and not str(cfg.get("targetExam") or cfg.get("targetGoal") or "").strip()
        else _default_target_label(cfg, active_tracker)
    )

    out: dict[str, Any] = {
        "brand": "Syntopik",
        "mode": "daily",
        "energyMode": energy_norm,
        "date": today_iso,
        "totalMinutes": total_minutes,
        "allocatedMinutes": sum(b["minutes"] for b in blocks),
        "fatigueCliff": {
            "sessionTargetMinutes": fatigue_target_min,
            "cappedTotal": fatigue_capped_total,
            "cappedBlocks": fatigue_blocks_capped,
            "affectState": fatigue_info.get("affectState") if fatigue_info else None,
        },
        "exam": {
            "targetExam": target_label,
            "examDate": exam_date_str,
            "daysToExam": days_to_exam,
            "phase": exam_phase,
            "configFound": bool(cfg),
        },
        "activeChapter": active_tracker,
        "reentry": reentry,
        "signals": {
            "dueCount": len(due_items),
            "compressedDueCount": comp["compressedCount"],
            "fireKnockouts": comp["knockedOutCount"],
            "bottleneckCount": len(bottlenecks),
            "topBottleneckId": bottlenecks[0]["id"] if bottlenecks else None,
            "islandCount": len(islands),
            "comboCount": len(combos),
            "confusableCount": len(confusables),
            "mistakeTotal": mistake_summary["total"],
            "topMistakeCategory": mistake_summary["topCategories"][0]["category"] if mistake_summary["topCategories"] else None,
        },
        "blocks": blocks,
        "suggestedPrompt": suggested_prompt,
    }
    return out


# ---------------------------------------------------------------------------
# Multi-Course Buffer-Protected Auto-Pacer & Pure-Stdlib RFC-5545 .ics Export
# ---------------------------------------------------------------------------


def _ics_escape(text: str) -> str:
    return (
        str(text or "")
        .replace("\\", "\\\\")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
        .replace(";", "\\;")
        .replace(",", "\\,")
    )


def _ics_fold(line: str) -> str:
    """Fold RFC-5545 content lines to <= 75 UTF-8 octets using CRLF + single space."""
    raw = str(line)
    if len(raw.encode("utf-8")) <= 75:
        return raw
    chunks: list[str] = []
    cur = ""
    limit = 75
    for ch in raw:
        cand = cur + ch
        if len(cand.encode("utf-8")) > limit and cur:
            chunks.append(cur)
            cur = " " + ch
            limit = 75
        else:
            cur = cand
    if cur:
        chunks.append(cur)
    return "\r\n".join(chunks)


def build_pace_plan(
    *,
    kg_dir: Path | str | None = None,
    chapters_dir: Path | str | None = None,
    config_path: Path | str | None = None,
    today: str | None = None,
    rebalance: bool = False,
    horizon_days: int = 14,
    daily_minutes: int | None = None,
    slack_buffer_pct: float | None = None,
) -> dict[str, Any]:
    """Multi-Course Buffer-Protected Auto-Pacer reading `exams[]` in `study-config.json`
    with a 20% guilt-free slack buffer and `learner-profile.json` fatigue cliff capping.
    """
    resolved_kg = resolve_kg_dir(kg_dir)
    resolved_chapters = resolve_chapters_dir(chapters_dir)
    today_date, _now_dt = _parse_today(today)
    today_iso = today_date.isoformat()

    if config_path:
        cfg_file = Path(config_path).expanduser().resolve()
    elif (resolved_kg / "study-config.json").is_file():
        cfg_file = resolved_kg / "study-config.json"
    else:
        cfg_file = resolved_kg / "exam-config.json"
    cfg = _load_json_safe(cfg_file)
    if not isinstance(cfg, dict):
        cfg = {}

    fatigue_info = _load_fatigue_cliff(resolved_kg)
    fatigue_cap = fatigue_info["sessionTargetMinutes"] if fatigue_info else None

    if daily_minutes is not None:
        eff_daily = max(15, int(daily_minutes))
    elif isinstance(cfg.get("dailyMinutes"), (int, float)) and int(cfg["dailyMinutes"]) > 0:
        eff_daily = max(15, int(cfg["dailyMinutes"]))
    else:
        eff_daily = 90

    if slack_buffer_pct is not None:
        buf_pct = max(0.05, min(0.40, float(slack_buffer_pct) / 100.0 if float(slack_buffer_pct) > 1.0 else float(slack_buffer_pct)))
    elif isinstance(cfg.get("slackBufferPct"), (int, float)):
        raw_b = float(cfg["slackBufferPct"])
        buf_pct = max(0.05, min(0.40, raw_b / 100.0 if raw_b > 1.0 else raw_b))
    else:
        buf_pct = 0.20

    props = _load_jsonl(resolved_kg / "propositions.jsonl")
    active_tracker = _inspect_active_tracker(resolved_chapters)

    raw_exams = cfg.get("exams")
    exam_specs: list[dict[str, Any]] = []
    if isinstance(raw_exams, list) and raw_exams:
        for idx, ex in enumerate(raw_exams):
            if not isinstance(ex, dict):
                continue
            name = str(ex.get("name") or ex.get("title") or ex.get("subject") or f"Course {idx + 1}").strip()
            d_str = str(ex.get("examDate") or ex.get("date") or "").strip()
            if not d_str:
                continue
            try:
                ex_dt = date_cls.fromisoformat(d_str[:10])
            except ValueError:
                continue
            exam_specs.append(
                {
                    "name": name,
                    "subject": str(ex.get("subject") or name).strip(),
                    "chapterSlug": str(ex.get("chapterSlug") or ex.get("slug") or "").strip() or None,
                    "examDate": ex_dt.isoformat(),
                    "examDateObj": ex_dt,
                    "weight": max(0.25, float(ex.get("weight") or 1.0)),
                    "remainingTopics": ex.get("remainingTopics"),
                    "missedDays": max(0, int(ex.get("missedDays") or 0)),
                }
            )
    elif cfg.get("examDate"):
        try:
            ex_dt = date_cls.fromisoformat(str(cfg["examDate"])[:10])
            exam_specs.append(
                {
                    "name": _default_target_label(cfg, active_tracker),
                    "subject": (active_tracker or {}).get("subject") or "General",
                    "chapterSlug": (active_tracker or {}).get("slug"),
                    "examDate": ex_dt.isoformat(),
                    "examDateObj": ex_dt,
                    "weight": 1.0,
                    "remainingTopics": None,
                    "missedDays": 0,
                }
            )
        except ValueError:
            pass

    courses: list[dict[str, Any]] = []
    for ex in exam_specs:
        days_left = (ex["examDateObj"] - today_date).days
        if days_left <= 0:
            continue
        slug = ex["chapterSlug"]
        rem_units = ex["remainingTopics"]
        if rem_units is None:
            # Estimate remaining units from tracker or non-archived non-solid propositions
            tr_rem = 0
            if slug and (resolved_chapters / f"{slug}-tracker.json").is_file():
                tr_obj = _load_json_safe(resolved_chapters / f"{slug}-tracker.json")
                if isinstance(tr_obj, dict):
                    tr_rem = sum(
                        1
                        for t in (tr_obj.get("topics") or [])
                        if isinstance(t, dict) and t.get("status") != "mastered"
                    )
            subj_low = ex["subject"].lower()
            kg_rem = sum(
                1
                for p in props
                if p.get("archived") is not True
                and str(p.get("status") or "planned").lower() != "solid"
                and (
                    subj_low in str(p.get("subject") or "").lower()
                    or (slug and slug.lower() in str(p.get("chapter") or "").lower())
                )
            )
            rem_units = max(1, tr_rem or kg_rem or 4)
        rem_units = max(1, int(rem_units))

        raw_buffer_days = max(1, int(round(days_left * buf_pct)))
        if rebalance and ex["missedDays"] > 0:
            # Absorb missed days cleanly into the guilt-free slack buffer first
            absorbed = min(raw_buffer_days, ex["missedDays"])
            remaining_buffer = max(0, raw_buffer_days - absorbed)
        else:
            absorbed = 0
            remaining_buffer = raw_buffer_days

        effective_study_days = max(1, days_left - remaining_buffer)
        urgency = (rem_units * ex["weight"]) / effective_study_days
        courses.append(
            {
                "name": ex["name"],
                "subject": ex["subject"],
                "chapterSlug": slug,
                "examDate": ex["examDate"],
                "daysUntilExam": days_left,
                "bufferDaysReserved": remaining_buffer,
                "bufferDaysAbsorbed": absorbed,
                "effectiveStudyDays": effective_study_days,
                "remainingUnits": rem_units,
                "weight": round(ex["weight"], 2),
                "urgencyScore": round(urgency, 4),
                "triageRecommended": effective_study_days < rem_units and remaining_buffer == 0,
            }
        )

    courses.sort(key=lambda c: (-c["urgencyScore"], c["daysUntilExam"], c["name"]))

    # Allocate daily study minutes proportionally across active courses
    total_urgency = sum(c["urgencyScore"] for c in courses) or 1.0
    for c in courses:
        share = c["urgencyScore"] / total_urgency
        c["dailyMinutesAllocated"] = max(10, int(round(eff_daily * share))) if courses else 0
        c["sharePct"] = round(share * 100.0, 1)

    # Normalize exact sum to eff_daily when courses exist
    if courses:
        diff_min = eff_daily - sum(c["dailyMinutesAllocated"] for c in courses)
        if diff_min != 0:
            courses[0]["dailyMinutesAllocated"] = max(10, courses[0]["dailyMinutesAllocated"] + diff_min)

    # Build multi-day calendar schedule with 1-in-5 (20%) guilt-free buffer days
    max_days_span = max((c["daysUntilExam"] for c in courses), default=horizon_days)
    sched_days = max(1, min(30, int(horizon_days), max_days_span))
    buffer_stride = max(2, int(round(1.0 / buf_pct)))  # 20% -> every 5th day is a guilt-free buffer day
    max_chunk = fatigue_cap if (fatigue_cap is not None and fatigue_cap >= 15) else 45

    schedule: list[dict[str, Any]] = []
    buffer_day_count = 0
    for offset in range(sched_days):
        day_dt = today_date + timedelta(days=offset)
        day_iso = day_dt.isoformat()
        active_today = [c for c in courses if c["examDate"] > day_iso]
        exam_today = [c for c in courses if c["examDate"] == day_iso]

        is_buffer = ((offset + 1) % buffer_stride == 0) and not exam_today
        day_blocks: list[dict[str, Any]] = []

        if exam_today:
            for ex_c in exam_today:
                day_blocks.append(
                    {
                        "course": ex_c["name"],
                        "subject": ex_c["subject"],
                        "type": "exam_day",
                        "minutes": 20,
                        "focus": f"Exam Day: {ex_c['name']} — light 20m calm confidence warmup only.",
                    }
                )
        elif is_buffer:
            buffer_day_count += 1
            day_blocks.append(
                {
                    "course": "Guilt-Free Slack Buffer",
                    "subject": "Recovery",
                    "type": "guilt_free_buffer",
                    "minutes": min(25, eff_daily),
                    "focus": "Protected 20% slack buffer: zero new material — rest, light catch-up, or 5-min micro-sprint only.",
                }
            )
        elif active_today:
            sub_urg = sum(c["urgencyScore"] for c in active_today) or 1.0
            for c in active_today:
                alloc = max(10, int(round(eff_daily * (c["urgencyScore"] / sub_urg))))
                # Respect learner-profile fatigue cliff by splitting long course blocks
                rem_m = alloc
                part = 1
                while rem_m > 0:
                    chunk = min(max_chunk, rem_m)
                    rem_m -= chunk
                    part_tag = f" (Part {part})" if alloc > max_chunk else ""
                    day_blocks.append(
                        {
                            "course": c["name"],
                            "subject": c["subject"],
                            "chapterSlug": c["chapterSlug"],
                            "type": "paced_study",
                            "minutes": chunk,
                            "focus": (
                                f"{c['name']}{part_tag}: active recall + next core topic "
                                f"({c['daysUntilExam'] - offset}d to exam, {c['bufferDaysReserved']}d buffer protected)."
                            ),
                        }
                    )
                    part += 1

        schedule.append(
            {
                "date": day_iso,
                "isBufferDay": is_buffer,
                "totalMinutes": sum(b["minutes"] for b in day_blocks),
                "blocks": day_blocks,
            }
        )

    return {
        "brand": "Syntopik",
        "mode": "pace",
        "date": today_iso,
        "rebalanced": bool(rebalance),
        "dailyMinutesBudget": eff_daily,
        "slackBufferPct": round(buf_pct * 100.0, 1),
        "fatigueBlockCapMinutes": max_chunk,
        "fatigueCliffApplied": fatigue_cap is not None,
        "courseCount": len(courses),
        "bufferDaysScheduled": buffer_day_count,
        "courses": courses,
        "schedule": schedule,
        "suggestedPrompt": (
            f"Run my paced multi-course plan ({len(courses)} course(s), {eff_daily}m/day, "
            f"{round(buf_pct * 100)}% guilt-free slack buffer)."
        ),
    }


def render_ics_calendar(
    pace_plan: dict[str, Any],
    *,
    start_hour_utc: int = 9,
) -> str:
    """Serialize a `build_pace_plan()` schedule into a pure-stdlib RFC-5545 `.ics` calendar."""
    dtstamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines: list[str] = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Syntopik//Multi-Course Auto-Pacer 3.0//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:Syntopik Study Pace",
    ]

    for day in pace_plan.get("schedule") or []:
        d_str = str(day.get("date") or "")[:10]
        if not d_str:
            continue
        try:
            d_obj = date_cls.fromisoformat(d_str)
        except ValueError:
            continue
        cursor = datetime(d_obj.year, d_obj.month, d_obj.day, int(start_hour_utc) % 24, 0, 0, tzinfo=timezone.utc)

        for idx, blk in enumerate(day.get("blocks") or [], start=1):
            mins = max(5, int(blk.get("minutes") or 20))
            end_dt = cursor + timedelta(minutes=mins)
            course = str(blk.get("course") or "Study Block")
            btype = str(blk.get("type") or "paced_study")
            focus = str(blk.get("focus") or "")
            summary = f"Syntopik: {course} ({mins}m)"
            uid_seed = f"{d_str}|{idx}|{course}|{btype}|{mins}"
            uid = f"syntopik-{hashlib.sha256(uid_seed.encode('utf-8')).hexdigest()[:16]}@syntopik.local"

            lines.append("BEGIN:VEVENT")
            lines.append(_ics_fold(f"UID:{uid}"))
            lines.append(f"DTSTAMP:{dtstamp}")
            lines.append(f"DTSTART:{cursor.strftime('%Y%m%dT%H%M%SZ')}")
            lines.append(f"DTEND:{end_dt.strftime('%Y%m%dT%H%M%SZ')}")
            lines.append(_ics_fold(f"SUMMARY:{_ics_escape(summary)}"))
            lines.append(_ics_fold(f"DESCRIPTION:{_ics_escape(focus)}"))
            lines.append(_ics_fold(f"CATEGORIES:Syntopik,{_ics_escape(str(blk.get('subject') or 'Study'))}"))
            lines.append("END:VEVENT")

            # Add 10-minute cognitive recovery break between blocks on the same day
            cursor = end_dt + timedelta(minutes=10)

    # Also emit explicit exam deadline reminders
    for c in pace_plan.get("courses") or []:
        ex_str = str(c.get("examDate") or "")[:10]
        if not ex_str:
            continue
        try:
            ex_d = date_cls.fromisoformat(ex_str)
        except ValueError:
            continue
        start_dt = datetime(ex_d.year, ex_d.month, ex_d.day, 8, 0, 0, tzinfo=timezone.utc)
        end_dt = start_dt + timedelta(hours=1)
        uid_seed = f"exam|{ex_str}|{c.get('name')}"
        uid = f"syntopik-exam-{hashlib.sha256(uid_seed.encode('utf-8')).hexdigest()[:16]}@syntopik.local"
        lines.append("BEGIN:VEVENT")
        lines.append(_ics_fold(f"UID:{uid}"))
        lines.append(f"DTSTAMP:{dtstamp}")
        lines.append(f"DTSTART:{start_dt.strftime('%Y%m%dT%H%M%SZ')}")
        lines.append(f"DTEND:{end_dt.strftime('%Y%m%dT%H%M%SZ')}")
        lines.append(_ics_fold(f"SUMMARY:{_ics_escape('Exam: ' + str(c.get('name') or 'Course'))}"))
        lines.append(_ics_fold(f"DESCRIPTION:{_ics_escape('Syntopik target exam date.')}"))
        lines.append("CATEGORIES:Syntopik,Exam")
        lines.append("END:VEVENT")

    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


def export_pace_ics(pace_plan: dict[str, Any], out_path: Path | str) -> Path:
    """Write a pure-stdlib RFC-5545 `.ics` file atomically."""
    ics_text = render_ics_calendar(pace_plan)
    return _write_text_atomic(Path(out_path).expanduser().resolve(), ics_text)


def _render_human_plan(plan: dict[str, Any]) -> str:
    if plan.get("mode") == "pace":
        lines = [
            f"# Syntopik Multi-Course Buffer-Protected Auto-Pacer — {plan['date']}",
            (
                f"**Budget:** {plan['dailyMinutesBudget']}m/day · "
                f"**Guilt-Free Slack Buffer:** {plan['slackBufferPct']}% "
                f"({plan['bufferDaysScheduled']} buffer day(s) scheduled) · "
                f"**Block Fatigue Cap:** {plan['fatigueBlockCapMinutes']}m"
            ),
            "",
        ]
        if plan.get("courses"):
            lines.append(f"## Active Courses ({len(plan['courses'])})")
            for c in plan["courses"]:
                lines.append(
                    f"- **{c['name']}** ({c['subject']}): exam `{c['examDate']}` ({c['daysUntilExam']}d left · "
                    f"{c['bufferDaysReserved']}d slack protected) · {c['dailyMinutesAllocated']}m/day ({c['sharePct']}%)"
                )
            lines.append("")
        else:
            lines.append("_No upcoming exams set yet, so there is nothing to pace toward._")
            lines.append("")
            lines.append('Add one with `syntopik pace --add-exam "Biology final" --date YYYY-MM-DD`,')
            lines.append("or ask your AI assistant to set your exam dates.")
            if plan.get("icsExportPath"):
                lines.append("")
                lines.append(f"**iCalendar Export:** `{plan['icsExportPath']}`")
            return "\n".join(lines)
        lines.append("## Upcoming Paced Schedule")
        for day in (plan.get("schedule") or [])[:7]:
            tag = " [GUILT-FREE BUFFER]" if day.get("isBufferDay") else ""
            lines.append(f"- **`{day['date']}`** ({day['totalMinutes']}m){tag}:")
            for blk in day.get("blocks") or []:
                lines.append(f"  - {blk['course']} ({blk['minutes']}m): {blk['focus']}")
        if plan.get("icsExportPath"):
            lines.append("")
            lines.append(f"**iCalendar Export:** `{plan['icsExportPath']}`")
        return "\n".join(lines)

    if plan.get("mode") == "exam_triage":
        lines = [
            f"# Syntopik {int(round(plan['hoursLeft']))}-Hour Pareto Exam Triage — {plan['date']}",
            f"**Budget:** {plan['usableStudyHours']}h usable study · {plan['sleepProtectedHours']}h sleep protected ({plan['sleepHoursPerNight']}h/night)",
            "",
        ]
        buckets = plan["buckets"]
        lines.append(f"## Salvage 70% ({len(buckets['salvage_70pct'])} high-yield targets)")
        for it in buckets["salvage_70pct"][:8]:
            lines.append(f"- `{it['propId']}` ({it['chapter']} · {it['allocatedMin']}m · lev={it['leverage']}, traps={it['trapHits']}): {it['title']}")
        lines.append("")
        lines.append(f"## Protect 20% ({len(buckets['protect_20pct'])} solid marks)")
        for it in buckets["protect_20pct"][:6]:
            lines.append(f"- `{it['propId']}` ({it['chapter']} · R≈{round(it['retrievability'] * 100)}%): {it['title']}")
        lines.append("")
        lines.append(f"## Explicitly Cut ({len(buckets['explicitly_cut'])} low-ROI items — guilt-free)")
        for it in buckets["explicitly_cut"][:6]:
            lines.append(f"- `{it['propId']}`: {it['reason']}")
        lines.append("")
        lines.append("## Hard-Cutoff Study Blocks")
        for blk in plan["blocks"]:
            lines.append(f"- **{blk['slot']}** [{blk['bucket']}]: {blk['goal']}")
        lines.append("")
        lines.append(f"**One-Line Kickoff:** `{plan['suggestedPrompt']}`")
        return "\n".join(lines)

    ex = plan["exam"]
    if plan.get("mode") == "micro":
        lines = [
            f"# Syntopik {plan['totalMinutes']}-Minute Micro-Review Sprint — {plan['date']}",
            "",
        ]
    else:
        countdown = (
            f" · {ex['daysToExam']} days to {ex['targetExam']} ({ex['examDate']}, {ex['phase']})"
            if ex["daysToExam"] is not None
            else f" · {ex['targetExam']}"
        )
        en = plan.get("energyMode", "normal")
        lines = [
            f"# Syntopik Daily Study Plan — {plan['date']} ({plan['totalMinutes']} min · energy={en}{countdown})",
            "",
        ]

    reentry = plan.get("reentry")
    if isinstance(reentry, dict):
        lines.append(f"## Zero-Guilt Re-Entry Brief ({reentry['daysAbsent']} days away — overdue counter hidden)")
        for b in reentry.get("reloadBullets") or []:
            lines.append(f"- {b}")
        lines.append("")

    sig = plan["signals"]
    ac = plan["activeChapter"]
    if ac:
        lines.append(
            f"**Active Topic:** {ac['title']} ({ac['subject']}, `{ac['slug']}`) · "
            f"Topics {ac['topicsMastered']}/{ac['topicsTotal']} · "
            f"Questions {ac['questionsDone']}/{ac['questionsTotal']} ({ac['questionsRetest']} retest)"
        )
    if not (isinstance(reentry, dict) and reentry.get("suppressOverdueCount")):
        ko_str = f" ({sig['fireKnockouts']} FIRe knockouts)" if sig.get("fireKnockouts") else ""
        lines.append(
            f"**Signals:** {sig['dueCount']} FSRS due/weak{ko_str} · {sig['bottleneckCount']} bottlenecks · "
            f"{sig['mistakeTotal']} logged mistakes · {sig.get('confusableCount', 0)} confusables"
        )
    lines.append("")

    for blk in plan["blocks"]:
        lines.append(f"## Block {blk['order']}: {blk['title']} — {blk['minutes']} min ({blk['sharePct']}%)")
        lines.append(f"_{blk['focus']}_")
        for task in blk["tasks"]:
            lines.append(f"- {task['description']}")
        lines.append("")

    lines.append(f"**One-Line Kickoff:** `{plan['suggestedPrompt']}`")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Syntopik Calm Study Planner: daily plan (with FIRe, [A,B,A'] contrast, Re-Entry Brief & energy modes), --micro 5 sprint, or --triage-hours 48 Pareto exam triage."
    )
    parser.add_argument("--minutes", type=int, default=None, help="Total daily study minutes (overrides study/exam-config.json dailyMinutes; default 60)")
    parser.add_argument("--micro", type=int, default=None, metavar="MIN", help="Generate a focused 3-5 item micro-review sprint (e.g., --micro 5)")
    parser.add_argument("--energy", choices=list(VALID_ENERGY_MODES), default="normal", help="Cognitive energy state: low (0 new theory, max 3 warm/faded cues), normal, or deep (Productive Failure + combos)")
    parser.add_argument("--resume", action="store_true", help="Force zero-guilt Cold-Start Re-Entry Brief (hides overdue counts, picks 1 warm-start win)")
    parser.add_argument("--triage-hours", type=float, default=None, metavar="HOURS", help="Launch Pareto Exam Triage mode for an exam in H hours (e.g., 48)")
    parser.add_argument("--sleep-hours", type=float, default=7.0, metavar="HOURS", help="Protected sleep hours per night during exam triage (default: 7)")
    parser.add_argument("--chapter", default=None, help="Optional chapter/subject filter for exam triage")
    parser.add_argument("--pace", action="store_true", help="Run Multi-Course Buffer-Protected Auto-Pacer reading exams[] from study-config.json (20%% guilt-free slack buffer)")
    parser.add_argument("--rebalance", action="store_true", help="Rebalance multi-course pace plan absorbing missed days into the 20%% slack buffer first")
    parser.add_argument("--ics", metavar="OUT_PATH", default=None, help="Export paced multi-course calendar as pure-stdlib RFC-5545 .ics file")
    parser.add_argument("--kg-dir", help="Override knowledge-graph directory (or SYNT_KG_DIR / MLH_KG_DIR)")
    parser.add_argument("--chapters-dir", help="Override chapters directory containing *-tracker.json (or SYNT_CHAPTERS_DIR / MLH_CHAPTERS_DIR)")
    parser.add_argument("--config", help="Optional path to study-config.json / exam-config.json")
    parser.add_argument("--today", help="Optional ISO date YYYY-MM-DD override for deterministic planning")
    parser.add_argument("--json", action="store_true", help="Emit structured JSON output")
    args = parser.parse_args(argv)

    if args.pace or args.rebalance or args.ics:
        plan = build_pace_plan(
            kg_dir=args.kg_dir,
            chapters_dir=args.chapters_dir,
            config_path=args.config,
            today=args.today,
            rebalance=bool(args.rebalance),
            daily_minutes=args.minutes,
        )
        if args.ics:
            ics_path = export_pace_ics(plan, args.ics)
            plan["icsExportPath"] = str(ics_path)
    elif args.triage_hours is not None:
        plan = build_triage_plan(
            hours_left=args.triage_hours,
            sleep_hours=args.sleep_hours,
            kg_dir=args.kg_dir,
            chapters_dir=args.chapters_dir,
            chapter=args.chapter,
            today=args.today,
        )
    elif args.micro is not None:
        plan = build_micro_plan(
            minutes=args.micro,
            kg_dir=args.kg_dir,
            chapters_dir=args.chapters_dir,
            today=args.today,
        )
    else:
        plan = build_daily_plan(
            minutes=args.minutes,
            kg_dir=args.kg_dir,
            chapters_dir=args.chapters_dir,
            config_path=args.config,
            today=args.today,
            energy=args.energy,
            resume=args.resume,
        )

    if args.json:
        print(json.dumps(plan, indent=2, ensure_ascii=False))
    else:
        print(_render_human_plan(plan))


if __name__ == "__main__":
    main()
