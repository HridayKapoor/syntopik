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
from datetime import date as date_cls, datetime, timezone
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
from syntopik.storage import load_json as _load_json_safe, load_jsonl as _load_jsonl  # noqa: E402

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

    exam_date_str = str(cfg.get("examDate") or "").strip() or None
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

    target_label = _default_target_label(cfg, active_tracker)

    out: dict[str, Any] = {
        "brand": "Syntopik",
        "mode": "daily",
        "energyMode": energy_norm,
        "date": today_iso,
        "totalMinutes": total_minutes,
        "allocatedMinutes": sum(b["minutes"] for b in blocks),
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


def _render_human_plan(plan: dict[str, Any]) -> str:
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
    parser.add_argument("--kg-dir", help="Override knowledge-graph directory (or SYNT_KG_DIR / MLH_KG_DIR)")
    parser.add_argument("--chapters-dir", help="Override chapters directory containing *-tracker.json (or SYNT_CHAPTERS_DIR / MLH_CHAPTERS_DIR)")
    parser.add_argument("--config", help="Optional path to study-config.json / exam-config.json")
    parser.add_argument("--today", help="Optional ISO date YYYY-MM-DD override for deterministic planning")
    parser.add_argument("--json", action="store_true", help="Emit structured JSON output")
    args = parser.parse_args(argv)

    if args.triage_hours is not None:
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
