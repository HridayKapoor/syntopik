#!/usr/bin/env python3
"""Syntopik — Profile-Aware Calm Daily Study Planner.

Combines live signals from:
  - `knowledge-graph/propositions.jsonl` (FSRS v6 due / retrievability + BKT mastery)
  - `knowledge-graph/mistakes.jsonl` (top recurring cognitive & coding mistake categories)
  - `scripts/graph_intel.py` (highest-leverage bottlenecks, memorized islands, synthesis combos)
  - `chapters/*-tracker.json` (active Topic/Chapter Autopilot ledger & Scope Charter, if present)
  - Optional `knowledge-graph/study-config.json` or `exam-config.json` (`{examDate, dailyMinutes, targetExam}`)

Outputs a calm, cognitive-load-balanced, time-boxed daily schedule (summing
strictly to `--minutes`) ready for interactive study across any AI harness or
morning `/schedule` automations.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date as date_cls, datetime, timezone
from pathlib import Path
from typing import Any

import fsrs

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = ROOT / "scripts"
DEFAULT_KG_DIR = ROOT / "knowledge-graph"
DEFAULT_CHAPTERS_DIR = ROOT / "chapters"

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

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


def _load_json_safe(path: Path) -> Any:
    if not path.exists() or not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        if isinstance(obj, dict):
            rows.append(obj)
    return rows


def _parse_today(today_arg: str | None) -> tuple[date_cls, datetime]:
    if today_arg and today_arg.strip():
        d = date_cls.fromisoformat(today_arg.strip())
        dt = datetime(d.year, d.month, d.day, 12, 0, 0, tzinfo=timezone.utc)
        return d, dt
    now = datetime.now(timezone.utc)
    return now.date(), now


def _compute_due_items(props: list[dict[str, Any]], today_iso: str, now_dt: datetime) -> list[dict[str, Any]]:
    sched = fsrs.Scheduler(learning_steps=(), relearning_steps=())
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
                card = fsrs.Card.from_dict(p["fsrs"])
                retr = round(float(sched.get_card_retrievability(card, now_dt)), 3)
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
        # Fallback to most recently updated tracker if none explicitly active.
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

    # Compute concise deterministic next action matching chapter_tracker.py priority.
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

    # Slightly tilt toward spaced retrieval & trap repair in final 30-day exam sprint while staying inside bands.
    if days_to_exam is not None and 0 <= days_to_exam <= 30:
        r1, r3 = 0.20, 0.28
    else:
        r1, r3 = 0.17, 0.27

    b1 = max(1, int(round(total * r1)))
    b3 = max(1, int(round(total * r3)))
    b2 = total - b1 - b3

    # Guard exact ratio bounds whenever feasible.
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


def build_daily_plan(
    *,
    minutes: int | None = None,
    kg_dir: Path | str | None = None,
    chapters_dir: Path | str | None = None,
    config_path: Path | str | None = None,
    today: str | None = None,
) -> dict[str, Any]:
    resolved_kg = resolve_kg_dir(kg_dir)
    resolved_chapters = resolve_chapters_dir(chapters_dir)
    today_date, now_dt = _parse_today(today)
    today_iso = today_date.isoformat()

    # Load optional study-config.json or exam-config.json
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

    # Gather live workspace signals.
    props = _load_jsonl(resolved_kg / "propositions.jsonl")
    due_items = _compute_due_items(props, today_iso, now_dt)

    G, _g_props, _g_chains = graph_intel.load_knowledge_graph(resolved_kg)
    bottlenecks = graph_intel.analyze_bottlenecks(G, top=5)
    islands = graph_intel.analyze_islands(G)
    combos = graph_intel.find_combos(G, top=4)

    mistakes_rows = mistake_analytics.load_mistakes(resolved_kg)
    mistake_summary = mistake_analytics.summarize_mistakes(mistakes_rows, top=3, today=today_iso)

    active_tracker = _inspect_active_tracker(resolved_chapters)
    warmup_cap = active_tracker["dailyWarmupLimit"] if active_tracker else 3

    b1_min, b2_min, b3_min = allocate_time_blocks(total_minutes, days_to_exam=days_to_exam)

    # --- Block 1: Spaced Recall Warmup (15-20%) ---
    warmup_picks = due_items[:warmup_cap]
    b1_tasks: list[dict[str, Any]] = []
    if warmup_picks:
        for item in warmup_picks:
            retr_str = f" (R≈{round(item['retrievability'] * 100)}%)" if item["retrievability"] is not None else ""
            b1_tasks.append(
                {
                    "type": "fsrs_recall",
                    "propId": item["id"],
                    "chapter": item["chapter"],
                    "status": item["status"],
                    "description": f"Active-recall check `{item['id']}` ({item['chapter']} · {item['status']}{retr_str}): {item['title']}",
                }
            )
    else:
        b1_tasks.append(
            {
                "type": "baseline_warmup",
                "description": "No overdue FSRS nodes — run a 3-question rapid recall from your latest chapter notes or start a new probe.",
            }
        )

    # --- Block 2: Core Active Chapter Autopilot / Bottleneck Deep Work (50-60%) ---
    b2_tasks: list[dict[str, Any]] = []
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
        if bottlenecks:
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
    else:
        b2_tasks.append(
            {
                "type": "start_chapter",
                "description": "No active topic tracker or broken bottlenecks — attach your course/paper files or say 'Start <topic>' to launch Syntopik Autopilot.",
            }
        )

    # --- Block 3: Interleaved Trap Repair & Cross-Chapter Combo Drill (25-30%) ---
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
    b3_tasks: list[dict[str, Any]] = []
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
        b3_tasks.append(
            {
                "type": "repeat_trap_drill",
                "key": rep["key"],
                "chapter": rep["chapter"],
                "count": rep["count"],
                "description": f"Discrimination drill on repeat trap ({rep['count']}x in {rep['chapter']}): {rep['actionCue']}",
            }
        )
    if combos:
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
    if islands:
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
            "Run a 3-problem interleaved discrimination drill mixing your 2 most recent topics (check invariants, edge cases, and complexity bounds)."
            if coding_context
            else "Run a 3-problem interleaved discrimination drill mixing your 2 most recent chapters (check signs, invariants, and validity bounds)."
        )
        b3_tasks.append(
            {
                "type": "interleaved_practice",
                "description": interleave_desc,
            }
        )

    b3_focus = (
        "Consolidate transfer by targeting top personal mistake categories and cross-chapter JEE synthesis pairs."
        if jee_context
        else "Consolidate transfer by targeting top personal mistake categories and integrative multi-concept synthesis pairs."
    )

    blocks = [
        {
            "id": "warmup",
            "order": 1,
            "title": "Spaced Recall Warmup",
            "minutes": b1_min,
            "sharePct": round((b1_min / total_minutes) * 100.0, 1),
            "focus": "Activate due/shaky FSRS traces without exhausting peak working memory.",
            "tasks": b1_tasks,
        },
        {
            "id": "deep_work",
            "order": 2,
            "title": "Active Autopilot & Bottleneck Deep Work",
            "minutes": b2_min,
            "sharePct": round((b2_min / total_minutes) * 100.0, 1),
            "focus": "Spend peak freshness on the active topic's exact next concept/unseen problem or highest-leverage bottleneck root.",
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

    if active_tracker and active_tracker["nextAction"] != "chapter_complete":
        suggested_prompt = f"Continue {active_tracker['title']} — run today's {total_minutes}-minute plan."
    elif bottlenecks:
        suggested_prompt = f"Repair bottleneck {bottlenecks[0]['id']} ({bottlenecks[0]['chapter']}) and run today's {total_minutes}-minute plan."
    else:
        suggested_prompt = f"Run today's {total_minutes}-minute study plan."

    target_label = _default_target_label(cfg, active_tracker)

    return {
        "brand": "Syntopik",
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
        "signals": {
            "dueCount": len(due_items),
            "bottleneckCount": len(bottlenecks),
            "topBottleneckId": bottlenecks[0]["id"] if bottlenecks else None,
            "islandCount": len(islands),
            "comboCount": len(combos),
            "mistakeTotal": mistake_summary["total"],
            "topMistakeCategory": mistake_summary["topCategories"][0]["category"] if mistake_summary["topCategories"] else None,
        },
        "blocks": blocks,
        "suggestedPrompt": suggested_prompt,
    }


def _render_human_plan(plan: dict[str, Any]) -> str:
    ex = plan["exam"]
    countdown = (
        f" · {ex['daysToExam']} days to {ex['targetExam']} ({ex['examDate']}, {ex['phase']})"
        if ex["daysToExam"] is not None
        else f" · {ex['targetExam']}"
    )
    lines = [
        f"# Syntopik Daily Study Plan — {plan['date']} ({plan['totalMinutes']} min{countdown})",
        "",
    ]

    sig = plan["signals"]
    ac = plan["activeChapter"]
    if ac:
        lines.append(
            f"**Active Topic:** {ac['title']} ({ac['subject']}, `{ac['slug']}`) · "
            f"Topics {ac['topicsMastered']}/{ac['topicsTotal']} · "
            f"Questions {ac['questionsDone']}/{ac['questionsTotal']} ({ac['questionsRetest']} retest)"
        )
    lines.append(
        f"**Signals:** {sig['dueCount']} FSRS due/weak · {sig['bottleneckCount']} bottlenecks · "
        f"{sig['mistakeTotal']} logged mistakes · {sig['comboCount']} synthesis combos · {sig['islandCount']} memorized islands"
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
        description="Syntopik Calm Study Planner: generate a time-boxed, cognitive-load-balanced daily plan combining FSRS due items, bottlenecks, mistakes, synthesis combos, and active Autopilot progress."
    )
    parser.add_argument("--minutes", type=int, default=None, help="Total daily study minutes (overrides study/exam-config.json dailyMinutes; default 60)")
    parser.add_argument("--kg-dir", help="Override knowledge-graph directory (or SYNT_KG_DIR / MLH_KG_DIR)")
    parser.add_argument("--chapters-dir", help="Override chapters directory containing *-tracker.json (or SYNT_CHAPTERS_DIR / MLH_CHAPTERS_DIR)")
    parser.add_argument("--config", help="Optional path to study-config.json / exam-config.json")
    parser.add_argument("--today", help="Optional ISO date YYYY-MM-DD override for deterministic planning")
    parser.add_argument("--json", action="store_true", help="Emit structured JSON output")
    args = parser.parse_args(argv)

    plan = build_daily_plan(
        minutes=args.minutes,
        kg_dir=args.kg_dir,
        chapters_dir=args.chapters_dir,
        config_path=args.config,
        today=args.today,
    )

    if args.json:
        print(json.dumps(plan, indent=2, ensure_ascii=False))
    else:
        print(_render_human_plan(plan))


if __name__ == "__main__":
    main()
