"""Syntopik v3.0 — Pedagogical Brainstem (`syntopik next` + `syntopik drop`).

Pure Python 3.12+ standard library only (zero external imports).
Provides a single deterministic control loop (`plan_next_move`) fusing:
  - FSRS-6 & True FIRe / SRC queue compression (`syntopik.fsrs_core`)
  - 3-Cadence Adaptive Learner Psychology (`syntopik.learner_model`)
  - Bayesian Noisy-OR Root-Cause Fault Localization (`syntopik.root_cause`)
  - 4-Tier Epistemic Evidence & Anti-Inflation Gate (`syntopik.evidence`)
  - Blunder FEN Forks, Counterexample Arenas, ShadowBox Decisions, Active Trackers,
    Live Whiteboard Learner Pins (`whiteboard-session.json`), and `study-config.json`

Emits a strict `<= 180`-token Action Envelope so any LLM across any harness
executes the optimal pedagogical move with zero Turn-0 rule bloat.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from syntopik.evidence import compute_evidence_summary
from syntopik.forks import list_due_forks
from syntopik.fsrs_core import card_retrievability, compress_due_queue, load_fsrs_parameters
from syntopik.ingest import detect_source_type, ingest_auto
from syntopik.interleave import get_interleave_stats
from syntopik.isomorphism import build_bijection_opener
from syntopik.learner_model import compile_teaching_policy, load_profile
from syntopik.paths import resolve_chapters_dir, resolve_kg_dir, resolve_workspace
from syntopik.readiness import detect_taper_mode
from syntopik.root_cause import find_blast_radius, select_diagnostic_probe
from syntopik.schemas import LEVEL_RANK, normalize_level_soft, now_iso, slugify, today_iso
from syntopik.storage import load_json, load_jsonl, save_json_atomic

__all__ = [
    "plan_next_move",
    "drop_sources",
]

_SPINE_KEYWORDS = (
    "lecture",
    "notes",
    "slides",
    "class",
    "whiteboard",
    "handout",
    "syllabus",
    "outline",
    "transcript",
    "chapter",
    "lesson",
)
_BANK_KEYWORDS = (
    "pyq",
    "problem",
    "problems",
    "exercise",
    "exercises",
    "sheet",
    "dpp",
    "bank",
    "question",
    "questions",
    "homework",
    "hw",
    "assignment",
    "quiz",
    "exam",
    "mock",
    "practice",
    "solutions",
)


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


def _clip(text: str, max_chars: int) -> str:
    s = re.sub(r"\s+", " ", str(text or "").strip())
    if len(s) <= max_chars:
        return s
    cut = s[: max(4, max_chars - 3)].rstrip()
    if " " in cut and len(cut) > 16:
        cut = cut.rsplit(" ", 1)[0].rstrip(" ,;:-")
    return cut + "..."


def _compute_days_absent(
    profile: dict[str, Any],
    revlog: list[dict[str, Any]],
    mistakes: list[dict[str, Any]],
    now_dt: datetime,
) -> int:
    latest: datetime | None = None

    def _consider(ts_val: Any) -> None:
        nonlocal latest
        dt = _parse_iso_dt(ts_val)
        if dt is not None and (latest is None or dt > latest):
            latest = dt

    affect = profile.get("affectiveState") or {}
    _consider(affect.get("lastEventAt"))
    for ev in (profile.get("recentEvents") or [])[-5:]:
        if isinstance(ev, dict):
            _consider(ev.get("ts"))
    for r in revlog[-10:]:
        _consider(r.get("ts") or r.get("reviewedAt") or r.get("date"))
    for m in mistakes[-5:]:
        _consider(m.get("ts") or m.get("date"))

    if latest is None:
        return 0
    return max(0, int((now_dt - latest).total_seconds() // 86400))


def _load_active_tracker(chapters_dir: Path) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Return `(tracker_dict, next_frontier_topic_dict)` from `chapters/*-tracker.json`."""
    if not chapters_dir.is_dir():
        return None, None
    trackers: list[dict[str, Any]] = []
    for p in sorted(chapters_dir.glob("*-tracker.json")):
        d = load_json(p, default=None)
        if isinstance(d, dict) and d.get("slug"):
            trackers.append(d)
    if not trackers:
        return None, None

    active = next((t for t in trackers if t.get("active") is True), None)
    if active is None:
        trackers.sort(key=lambda t: str(t.get("updatedAt") or ""), reverse=True)
        active = trackers[0]

    topics = [t for t in (active.get("topics") or []) if isinstance(t, dict)]
    mastered = {str(t.get("id")) for t in topics if str(t.get("status") or "").lower() == "mastered"}
    pending = [t for t in topics if str(t.get("status") or "").lower() not in ("mastered", "skipped")]
    pending.sort(
        key=lambda t: (
            0 if all(pr in mastered for pr in (t.get("prereqTopicIds") or [])) else 1,
            LEVEL_RANK.get(normalize_level_soft(t.get("level"), "basics"), 1),
            int(t.get("order") or 999),
            str(t.get("id") or ""),
        )
    )
    return active, (pending[0] if pending else None)


def _extract_pending_pins(kg_dir: Path) -> list[dict[str, Any]]:
    wb = load_json(kg_dir / "whiteboard-session.json", default=None)
    if not isinstance(wb, dict):
        return []
    raw_pins = wb.get("pins") or []
    out: list[dict[str, Any]] = []
    for pin in raw_pins:
        if not isinstance(pin, dict):
            continue
        if bool(pin.get("resolved")):
            continue
        out.append(
            {
                "id": str(pin.get("id") or f"pin-{len(out) + 1}"),
                "stepIndex": pin.get("stepIndex"),
                "note": _clip(str(pin.get("note") or ""), 80),
            }
        )
    return out[:3]


def _extract_failed_node_from_event(
    event: Any,
    revlog: list[dict[str, Any]],
    mistakes: list[dict[str, Any]],
    props_by_id: dict[str, dict[str, Any]],
) -> str | None:
    if isinstance(event, dict):
        kind = str(event.get("kind") or event.get("type") or "").lower()
        pid = str(event.get("propId") or event.get("nodeId") or event.get("failedNodeId") or "").strip()
        if pid and (event.get("correct") is False or kind in ("miss", "fail", "again", "wrong", "mistake")):
            return pid if pid in props_by_id else None
    elif isinstance(event, str):
        s = event.strip()
        for prefix in ("miss:", "fail:", "again:", "wrong:"):
            if s.lower().startswith(prefix):
                cand = s[len(prefix) :].strip()
                if cand in props_by_id:
                    return cand

    # Check latest revlog entry if it was a recent fail
    if revlog:
        last_r = revlog[-1]
        pid = str(last_r.get("propId") or last_r.get("id") or "").strip()
        rating = last_r.get("rating")
        failed = (last_r.get("correct") is False) or (isinstance(rating, int) and rating == 1) or str(rating or "").lower() == "again"
        if failed and pid in props_by_id:
            return pid

    # Or latest mistake entry
    if mistakes:
        last_m = mistakes[-1]
        pid = str(last_m.get("propId") or "").strip()
        if pid in props_by_id:
            return pid
    return None


def _compute_compressed_due(
    props: list[dict[str, Any]],
    chains: list[dict[str, Any]],
    kg_dir: Path,
    now_dt: datetime,
) -> dict[str, Any]:
    today_str = now_dt.date().isoformat()
    params = load_fsrs_parameters(kg_dir)
    due_items: list[dict[str, Any]] = []
    for p in props:
        pid = str(p.get("id") or "").strip()
        if not pid or bool(p.get("archived")) or bool(p.get("suspended")):
            continue
        status = str(p.get("status") or "planned").lower()
        due_date = p.get("nextReview")
        is_due = status in ("misconception", "shaky") or (isinstance(due_date, str) and due_date <= today_str)
        if not is_due:
            continue
        retr = 0.5
        if isinstance(p.get("fsrs"), dict):
            try:
                retr = round(float(card_retrievability(p["fsrs"], now_dt, parameters=params)), 3)
            except Exception:
                retr = 0.5
        due_items.append(
            {
                "id": pid,
                "title": str(p.get("title") or pid),
                "subject": str(p.get("subject") or "General"),
                "chapter": str(p.get("chapter") or ""),
                "status": status,
                "nextReview": due_date,
                "retrievability": retr,
            }
        )
    due_items.sort(
        key=lambda x: (
            0 if x["status"] == "misconception" else 1 if x["status"] == "shaky" else 2,
            x["retrievability"],
            x["id"],
        )
    )
    return compress_due_queue(due_items, chains)


def _compact_policy(policy: dict[str, Any]) -> dict[str, Any]:
    return {
        "domain": policy.get("domain"),
        "scaffoldingMode": policy.get("scaffoldingMode"),
        "allowWorkedExample": bool(policy.get("allowWorkedExample")),
        "allowProductiveFailure": bool(policy.get("allowProductiveFailure")),
        "maxProseWords": int(policy.get("maxProseWords") or 135),
        "targetSuccessRate": float(policy.get("targetSuccessRate") or 0.85),
        "requirePredictionFirst": bool(policy.get("requirePredictionFirst")),
        "affectState": policy.get("affectState") or "flow",
        "affectIntervention": policy.get("affectIntervention") or "none",
    }


def _envelope_token_count(envelope: dict[str, Any]) -> int:
    return len(re.findall(r"\S+", json.dumps(envelope, ensure_ascii=False)))


def _enforce_envelope_budget(envelope: dict[str, Any], max_tokens: int = 180) -> dict[str, Any]:
    """Guarantee the emitted Action Envelope stays strictly `<= max_tokens`."""
    budget = max(48, int(max_tokens))
    if _envelope_token_count(envelope) <= budget:
        return envelope

    bridge = envelope.get("isomorphicBridge")
    if isinstance(bridge, dict):
        bridge["openerPrompt"] = _clip(str(bridge.get("openerPrompt") or ""), 84)
        if isinstance(bridge.get("breaksAt"), list) and bridge["breaksAt"]:
            bridge["breaksAt"] = [_clip(str(bridge["breaksAt"][0]), 54)]
        bridge["archetypeName"] = _clip(str(bridge.get("archetypeName") or ""), 36)
    if _envelope_token_count(envelope) <= budget:
        return envelope

    envelope["rationale"] = _clip(str(envelope.get("rationale") or ""), 84)
    envelope["whiteboardHint"] = _clip(str(envelope.get("whiteboardHint") or ""), 64)
    if isinstance(envelope.get("policyConstraints"), list):
        envelope["policyConstraints"] = [_clip(str(c), 40) for c in envelope["policyConstraints"][:2]]
    if _envelope_token_count(envelope) <= budget:
        return envelope

    if isinstance(bridge, dict):
        bridge["openerPrompt"] = _clip(str(bridge.get("openerPrompt") or ""), 60)
        if isinstance(bridge.get("breaksAt"), list) and bridge["breaksAt"]:
            bridge["breaksAt"] = [_clip(str(bridge["breaksAt"][0]), 38)]
    if isinstance(envelope.get("alerts"), list):
        envelope["alerts"] = [_clip(str(a), 36) for a in envelope["alerts"][:2]]
    if isinstance(envelope.get("pendingPins"), list):
        envelope["pendingPins"] = [
            {**p, "note": _clip(str(p.get("note") or ""), 44)} if isinstance(p, dict) else p
            for p in envelope["pendingPins"][:2]
        ]
    if _envelope_token_count(envelope) <= budget:
        return envelope

    # Hard-trim loop: progressively trim optional/verbose fields until strictly <= budget
    if isinstance(envelope.get("alerts"), list):
        while envelope["alerts"] and _envelope_token_count(envelope) > budget:
            envelope["alerts"].pop()
    if _envelope_token_count(envelope) <= budget:
        return envelope

    if isinstance(envelope.get("pendingPins"), list):
        for p in envelope["pendingPins"]:
            if isinstance(p, dict):
                p["note"] = _clip(str(p.get("note") or ""), 20)
        while envelope["pendingPins"] and _envelope_token_count(envelope) > budget:
            envelope["pendingPins"].pop()
    if _envelope_token_count(envelope) <= budget:
        return envelope

    if isinstance(envelope.get("policyConstraints"), list):
        envelope["policyConstraints"] = [_clip(str(c), 28) for c in envelope["policyConstraints"][:1]]
        while envelope["policyConstraints"] and _envelope_token_count(envelope) > budget:
            envelope["policyConstraints"].pop()
    if _envelope_token_count(envelope) <= budget:
        return envelope

    if isinstance(envelope.get("isomorphicBridge"), dict):
        br = envelope["isomorphicBridge"]
        if isinstance(br.get("breaksAt"), list) and br["breaksAt"]:
            br["breaksAt"] = []
        if _envelope_token_count(envelope) > budget:
            br["openerPrompt"] = _clip(str(br.get("openerPrompt") or ""), 32)
            br["archetypeName"] = _clip(str(br.get("archetypeName") or ""), 20)
        if _envelope_token_count(envelope) > budget:
            br["openerPrompt"] = ""
        if _envelope_token_count(envelope) > budget:
            envelope.pop("isomorphicBridge", None)
    if _envelope_token_count(envelope) <= budget:
        return envelope

    for field, limits in (
        ("whiteboardHint", (36, 16, 0)),
        ("rationale", (56, 28, 12, 0)),
        ("exactCmd", (48, 24, 0)),
    ):
        for lim in limits:
            if _envelope_token_count(envelope) <= budget:
                return envelope
            val = str(envelope.get(field) or "")
            envelope[field] = "" if lim == 0 else _clip(val, lim)

    for _ in range(16):
        if _envelope_token_count(envelope) <= budget:
            return envelope
        trimmed = False
        for field in ("rationale", "whiteboardHint", "exactCmd", "targetNodeId", "chapterSlug"):
            val = envelope.get(field)
            if isinstance(val, str) and len(val) > 8:
                envelope[field] = val[: max(4, len(val) // 2)]
                trimmed = True
                if _envelope_token_count(envelope) <= budget:
                    return envelope
        if not trimmed:
            break
    return envelope


# ---------------------------------------------------------------------------
# Public API 1: Single Deterministic Teacher Control Loop (`plan_next_move`)
# ---------------------------------------------------------------------------


def plan_next_move(
    workspace: Path | str | None = None,
    *,
    event: Any = None,
    energy: str | None = None,
    domain: str | None = None,
) -> dict[str, Any]:
    """Fuse all local cognitive/memory/graph signals into a <=180-token Action Envelope."""
    ws = resolve_workspace(workspace)
    kg_dir = resolve_kg_dir(ws)
    ch_dir = resolve_chapters_dir(ws)
    now_dt = datetime.now(timezone.utc)

    study_cfg = load_json(kg_dir / "study-config.json", default={})
    if not isinstance(study_cfg, dict):
        study_cfg = {}
    eff_energy = str(energy or study_cfg.get("energyMode") or "normal").strip().lower()
    if eff_energy not in ("low", "normal", "deep"):
        eff_energy = "normal"

    profile = load_profile(kg_dir)
    props = load_jsonl(kg_dir / "propositions.jsonl")
    chains = load_jsonl(kg_dir / "chains.jsonl")
    revlog = load_jsonl(kg_dir / "revlog.jsonl")
    mistakes = load_jsonl(kg_dir / "mistakes.jsonl")
    arenas = load_jsonl(kg_dir / "arenas.jsonl")
    decisions = load_jsonl(kg_dir / "decisions.jsonl")
    props_by_id = {str(p.get("id") or ""): p for p in props if isinstance(p, dict) and p.get("id")}

    active_tracker, next_topic = _load_active_tracker(ch_dir)
    active_slug = str(active_tracker.get("slug")) if active_tracker else None
    active_dom = domain or (str(active_tracker.get("subject")) if active_tracker and active_tracker.get("subject") else None)

    policy_full = compile_teaching_policy(profile, domain=active_dom, energy=eff_energy)
    compact_pol = _compact_policy(policy_full)
    constraints = [_clip(g, 58) for g in (policy_full.get("activeGuardrails") or [])[:3]]
    if not constraints:
        constraints = [f"<= {compact_pol['maxProseWords']}w prose; 1 demand/turn"]

    pending_pins = _extract_pending_pins(kg_dir)
    days_absent = _compute_days_absent(profile, revlog, mistakes, now_dt)
    affect_state = str((profile.get("affectiveState") or {}).get("state") or "flow")
    total_events = int((profile.get("globalStats") or {}).get("totalEvents") or 0)
    cal_done = bool((profile.get("calibration") or {}).get("completed"))

    due_forks_resp = list_due_forks(workspace=ws, kg_dir=kg_dir, limit=5, today=now_dt.date().isoformat())
    due_forks = due_forks_resp.get("items") or due_forks_resp.get("forks") or []
    comp_due = _compute_compressed_due(props, chains, kg_dir, now_dt)
    due_selected = comp_due.get("selected") or []
    ev_summary = compute_evidence_summary(ws)
    anti_alerts = ev_summary.get("antiInflationAlerts") or []
    blast_list = find_blast_radius(kg_dir, limit=3)

    # Fast O(1) Pre-Exam Taper Mode check (<= 72h)
    try:
        taper = detect_taper_mode(kg_dir, now=now_dt)
    except Exception:
        taper = {"active": False, "hoursRemaining": None, "phase": "none"}

    # Assemble compact global alerts (<= 3 short strings)
    alerts: list[str] = []
    if taper.get("active"):
        hrs_rem = int(round(float(taper.get("hoursRemaining") or 0.0)))
        alerts.append(f"Exam in {hrs_rem}h: taper (0 new)")
    if days_absent >= 4:
        alerts.append(f"{days_absent}d away: calm re-entry")
    if blast_list:
        alerts.append(f"Blast root: {blast_list[0]['propId']} ({blast_list[0]['downstreamCount']} downstream)")
    if anti_alerts:
        alerts.append(f"T2 bottleneck: {anti_alerts[0]['propId']}")
    if due_forks:
        alerts.append(f"{len(due_forks)} blunder fork(s) due")
    if arenas:
        open_arenas = sum(1 for a in arenas if str(a.get("status") or "").lower() not in ("cleared", "passed"))
        if open_arenas > 0:
            alerts.append(f"{open_arenas} open arena(s)")
    if decisions:
        recent_dec = decisions[-5:]
        dec_acc = sum(1 for d in recent_dec if d.get("correct")) / len(recent_dec)
        if dec_acc < 0.60:
            alerts.append("Blitz cue accuracy <60%")
    alerts = alerts[:3]

    # Candidate causal probe from explicit/recent failed leaf
    failed_leaf = _extract_failed_node_from_event(event, revlog, mistakes, props_by_id)
    causal_probe: dict[str, Any] | None = None
    if failed_leaf:
        probe_res = select_diagnostic_probe(kg_dir, failed_leaf)
        bp = probe_res.get("bestProbe")
        if isinstance(bp, dict) and float(bp.get("expectedEntropyReductionBits") or 0.0) >= 0.15:
            causal_probe = bp

    skip_cal = (event == "skip_calibration") or (isinstance(event, dict) and bool(event.get("skipCalibration")))

    # --- Exact 10-step Priority Hierarchy ---
    mode: str
    target_id: str | None = None
    rationale: str
    exact_cmd: str
    skill_hint: str
    wb_hint: str

    if pending_pins:
        p0 = pending_pins[0]
        mode = "whiteboard_pin_answer"
        rationale = f"Learner pinned whiteboard step {p0.get('stepIndex')}: {p0.get('note')}"
        exact_cmd = f"syntopik board status --json"
        skill_hint = "teach-chapter"
        wb_hint = f"Resolve pin {p0['id']} directly on step {p0.get('stepIndex')} before advancing."
    elif total_events == 0 and not cal_done and not skip_cal:
        mode = "onboarding_calibration"
        rationale = "First-time uncalibrated learner; offer optional 2-min non-VARK calibration (skippable)."
        exact_cmd = "syntopik profile calibrate --json"
        skill_hint = "topic-onboarding"
        wb_hint = "Optional onboarding card (entryRamp, challengeAppetite, domainFluency)."
    elif days_absent >= 4:
        mode = "cold_start_reentry"
        target_id = due_selected[0]["id"] if due_selected else (blast_list[0]["propId"] if blast_list else None)
        rationale = f"{days_absent} days away; serve guilt-free re-entry brief & 1 anchor recall."
        exact_cmd = "syntopik review --recover --daily-cap 15"
        skill_hint = "knowledge-graph"
        wb_hint = "Push 1 calm anchor summary frame; zero backlog guilt."
    elif affect_state == "fatigued":
        mode = "calm_wrapup"
        target_id = due_selected[0]["id"] if due_selected else None
        rationale = "Session fatigue cliff detected; run 1 low-energy consolidation & offer guilt-free stop."
        exact_cmd = "syntopik review --energy low"
        skill_hint = "chapter-autopilot"
        wb_hint = "Push concise summary frame and close session cleanly."
    elif affect_state == "overloaded_frustrated":
        mode = "bridge_worked_example"
        target_id = failed_leaf or (due_selected[0]["id"] if due_selected else None)
        rationale = "Overload streak detected; step down scaffolding to <=5-step worked bridge (p*=0.90)."
        exact_cmd = "syntopik profile policy --json"
        skill_hint = "teach-chapter"
        wb_hint = "Push step-by-step worked micro-example frame (<=110w prose)."
    elif causal_probe is not None:
        mode = "causal_probe"
        target_id = causal_probe["propId"]
        rationale = (
            f"Leaf '{failed_leaf}' failed; probe ancestor '{target_id}' first "
            f"(cuts {causal_probe['expectedEntropyReductionBits']:.2f} bits)."
        )
        exact_cmd = f"syntopik run srs_engine review {target_id}"
        skill_hint = "teach-chapter"
        wb_hint = f"Push prediction_gate on prerequisite '{target_id}' before re-teaching leaf."
    elif due_forks:
        f0 = due_forks[0]
        mode = "fork_replay"
        target_id = f0.get("propId") or f0.get("id")
        rationale = f"Due Blunder Fork '{f0.get('id')}' ready for mutated Woodpecker replay."
        exact_cmd = f"syntopik run fork_replay due --json"
        skill_hint = "practice-drill"
        wb_hint = f"Load Blunder Fork '{f0.get('id')}' pre-blunder FEN frame."
    elif due_selected:
        d0 = due_selected[0]
        mode = "fire_review"
        target_id = d0["id"]
        ko_n = int(comp_due.get("knockedOutCount") or 0)
        ko_note = f" (knocks out {ko_n} prereqs)" if ko_n > 0 else ""
        rationale = f"Due FIRe/SRC review '{target_id}' ({d0['status']}, R={d0['retrievability']}){ko_note}."
        exact_cmd = f"syntopik review --json"
        skill_hint = "knowledge-graph"
        wb_hint = f"Push cold-recall prediction_gate for '{target_id}'."
    elif anti_alerts:
        a0 = anti_alerts[0]
        mode = "transfer_verify"
        target_id = a0["propId"]
        suggested = a0["suggestedAction"]
        rationale = f"Bottleneck '{target_id}' stuck at {a0['currentTierLabel']}; verify T3/T4 transfer."
        exact_cmd = f"syntopik run {suggested} list --json"
        skill_hint = "practice-drill"
        wb_hint = f"Open {suggested} transfer challenge for '{target_id}'."
    else:
        # Check v3.2 Pre-Exam Taper Mode (<=72h) and Blind-Chapter Interleave before teach_frontier
        active_props = [
            p for p in props
            if isinstance(p, dict) and str(p.get("id") or "").strip() and not bool(p.get("archived"))
        ]
        chapters_with_props = {
            str(p.get("chapter") or "").strip().lower()
            for p in active_props
            if str(p.get("chapter") or "").strip()
        }
        planned_node = next((p for p in active_props if str(p.get("status") or "").lower() == "planned"), None)

        dna = profile.get("failureDna") if isinstance(profile.get("failureDna"), dict) else {}
        nea_window = int(dna.get("windowCount") or 0)
        nea_cm = float(((dna.get("tierShares") or {}).get("comprehension_model")) or 0.0)

        should_blind_interleave = False
        interleave_reason = ""
        if (not taper.get("active")) and len(chapters_with_props) >= 2:
            explicit_ilv = (event == "interleave_due") or (
                isinstance(event, dict) and bool(event.get("interleaveDue"))
            )
            try:
                ilv_stats = get_interleave_stats(ws, kg_dir=kg_dir)
                days_since_ilv = ilv_stats.get("daysSinceLastInterleave")
            except Exception:
                days_since_ilv = None

            if explicit_ilv:
                should_blind_interleave = True
                interleave_reason = f"Blind interleave requested across {len(chapters_with_props)} chapters."
            elif nea_window >= 1 and nea_cm >= 0.45:
                should_blind_interleave = True
                interleave_reason = (
                    f"NEA comprehension_model={int(round(nea_cm * 100))}% across "
                    f"{len(chapters_with_props)} chapters; run blind-chapter principle drill."
                )
            elif (next_topic is None and planned_node is None) and (days_since_ilv is None or days_since_ilv >= 3):
                should_blind_interleave = True
                interleave_reason = (
                    f"Frontier clear across {len(chapters_with_props)} chapters; run blind-chapter interleave."
                )
            elif (days_since_ilv is not None and days_since_ilv >= 3) or (
                days_since_ilv is None and total_events >= 6
            ):
                solid_chapters = {
                    str(p.get("chapter") or "").strip().lower()
                    for p in active_props
                    if str(p.get("status") or "").lower() == "solid" and str(p.get("chapter") or "").strip()
                }
                if len(solid_chapters) >= 2:
                    should_blind_interleave = True
                    interleave_reason = (
                        f"Blind-chapter interleave due across {len(solid_chapters)} solid chapters."
                    )

        if taper.get("active"):
            hrs_rem = int(round(float(taper.get("hoursRemaining") or 0.0)))
            mode = "pre_exam_taper"
            target_id = None
            rationale = (
                f"Exam in {hrs_rem}h ({taper.get('phase')}); consolidate solid/forks & Pre-Mortem Shield (0 new concepts)."
            )
            exact_cmd = "syntopik readiness --json"
            skill_hint = "chapter-autopilot"
            wb_hint = "Display Pre-Mortem Shield & light consolidation only; zero new topics."
        elif should_blind_interleave:
            mode = "blind_interleave"
            target_id = None
            rationale = interleave_reason
            exact_cmd = "syntopik interleave build --n 6 --json"
            skill_hint = "practice-drill"
            wb_hint = "Run 2-stage blind principle-first drill (strip chapter cues)."
        else:
            mode = "teach_frontier"
            if next_topic is not None:
                t_props = next_topic.get("propIds") or []
                target_id = str(t_props[0]) if t_props else str(next_topic.get("id") or "")
                rationale = (
                    f"Teach frontier topic '{_clip(str(next_topic.get('title') or target_id), 44)}' "
                    f"via {compact_pol['scaffoldingMode']}."
                )
                exact_cmd = f"syntopik run chapter_tracker next --slug {active_slug}"
                skill_hint = "teach-chapter"
                wb_hint = f"Push derivation_step for '{target_id}' ({compact_pol['scaffoldingMode']})."
            else:
                target_id = str(planned_node["id"]) if planned_node else None
                rationale = (
                    f"Teach planned node '{target_id}' via {compact_pol['scaffoldingMode']}."
                    if target_id
                    else "Workspace clear; drop sources or start a new chapter."
                )
                exact_cmd = "syntopik drop <source>" if target_id is None else f"syntopik run srs_engine list --json"
                skill_hint = "topic-onboarding" if target_id is None else "teach-chapter"
                wb_hint = "Ready for new topic scope or derivation frame."

    envelope: dict[str, Any] = {
        "ok": True,
        "recommendedMode": mode,
        "targetNodeId": target_id,
        "chapterSlug": active_slug,
        "rationale": _clip(rationale, 130),
        "teachingPolicy": compact_pol,
        "policyConstraints": constraints,
        "exactCmd": exact_cmd,
        "skillHint": skill_hint,
        "whiteboardHint": _clip(wb_hint, 96),
        "pendingPins": pending_pins,
        "alerts": alerts,
    }

    if mode == "teach_frontier" and target_id:
        try:
            opener = build_bijection_opener(kg_dir, target_id)
        except Exception:
            opener = None
        if isinstance(opener, dict):
            raw_breaks = opener.get("breaksAt") or []
            compact_breaks = [_clip(str(b), 120) for b in raw_breaks[:1] if str(b).strip()]
            envelope["isomorphicBridge"] = {
                "knownPropId": str(opener["knownPropId"]),
                "archetypeName": _clip(str(opener["archetypeName"]), 48),
                "breaksAt": compact_breaks,
                "openerPrompt": _clip(str(opener["openerPrompt"]), 150),
            }

    return _enforce_envelope_budget(envelope, max_tokens=180)


# ---------------------------------------------------------------------------
# Public API 2: 10-Second Mixed-Input Drop (`drop_sources`)
# ---------------------------------------------------------------------------


def _classify_input_role(raw_input: str, detected_kind: str) -> tuple[str, int, str]:
    """Classify an input into `(role, priority_rank, reason)` obeying Teacher-First Law."""
    low = str(raw_input or "").strip().lower()
    name_part = Path(raw_input).name.lower() if not low.startswith(("http://", "https://")) else low

    # Problem bank check first when explicit problem/PYQ/exercise markers exist
    if any(re.search(rf"(?:^|[^a-z0-9]){kw}(?:$|[^a-z0-9])", name_part) for kw in _BANK_KEYWORDS):
        return "problem_bank", 2, "Matches supplementary problem-bank/PYQ naming"

    # Explicit Teacher-First spine signals (lecture notes, slides, whiteboard, YouTube lecture)
    if any(re.search(rf"(?:^|[^a-z0-9]){kw}(?:$|[^a-z0-9])", name_part) for kw in _SPINE_KEYWORDS):
        return "teacher_spine", 1, "Matches instructor lecture/notes spine"
    if re.search(r"(?:^|[^a-z0-9])(?:l|lec|ch)\d+", name_part):
        return "teacher_spine", 1, "Numbered lecture/chapter sequence"
    if detected_kind == "youtube":
        return "teacher_spine", 1, "Lecture video transcript spine"
    if detected_kind == "repo":
        return "teacher_spine", 1, "Primary codebase walkthrough spine"
    if detected_kind == "arxiv":
        return "reference", 3, "Research paper reference (Keshav 3-Pass)"
    if detected_kind == "url":
        return "reference", 3, "Web documentation reference"

    # Default local notes/PDF without bank cues: candidate primary spine
    return "teacher_spine", 1, "Primary study document"


def _extract_first_heading_or_hint(ingested_items: list[dict[str, Any]], fallback_title: str) -> str:
    candidate_files: list[Path] = []
    for item in ingested_items:
        ws_root = item.get("_workspace")
        for f_rec in item.get("files") or []:
            rel = f_rec.get("file") if isinstance(f_rec, dict) else None
            if rel and ws_root:
                candidate_files.append(Path(ws_root) / str(rel))
        out_dir = item.get("outputDir")
        if out_dir:
            files_sub = Path(str(out_dir)) / "files"
            if files_sub.is_dir():
                for fp in sorted(files_sub.rglob("*")):
                    if fp.is_file():
                        candidate_files.append(fp)

    for fp in candidate_files:
        if fp.is_file() and fp.suffix.lower() in (".md", ".markdown", ".txt"):
            try:
                for line in fp.read_text(encoding="utf-8", errors="ignore").splitlines()[:40]:
                    m = re.match(r"^\s*#{1,3}\s+(.+?)\s*$", line)
                    if m and len(m.group(1).strip()) >= 4:
                        heading = _clip(m.group(1).strip(), 72)
                        return f"Before we open '{heading}', what core invariant or first principle do you expect governs it?"
            except OSError:
                pass
    return f"Before walking through '{fallback_title}', what do you already expect the governing principle or main failure mode to be?"


def drop_sources(
    workspace: Path | str | None,
    inputs: str | Path | list[str | Path],
    *,
    slug: str | None = None,
    title: str | None = None,
) -> dict[str, Any]:
    """Inspect mixed inputs, lock Teacher-First spine, ingest safely, init tracker, return Probe #1."""
    ws = resolve_workspace(workspace)
    ch_dir = resolve_chapters_dir(ws)
    ch_dir.mkdir(parents=True, exist_ok=True)

    raw_list = [inputs] if isinstance(inputs, (str, Path)) else list(inputs or [])
    cleaned_inputs = [str(x).strip() for x in raw_list if str(x).strip()]
    if not cleaned_inputs:
        return {
            "ok": False,
            "error": "At least one source file, folder, or URL is required.",
            "classifiedSources": [],
            "ingested": [],
            "failed": [],
        }

    # 1. Classify every input & enforce Teacher-First Law ordering
    classified: list[dict[str, Any]] = []
    for raw in cleaned_inputs:
        try:
            kind = detect_source_type(raw)
        except Exception:  # noqa: BLE001
            kind = "notes"
        role, rank, reason = _classify_input_role(raw, kind)
        classified.append(
            {
                "input": raw,
                "detectedKind": kind,
                "role": role,
                "priorityRank": rank,
                "reason": reason,
            }
        )

    # Guarantee at least one spine if all inputs looked like references/banks
    if not any(c["role"] == "teacher_spine" for c in classified):
        classified[0]["role"] = "teacher_spine"
        classified[0]["priorityRank"] = 1
        classified[0]["reason"] = "Promoted to primary study spine"

    classified.sort(key=lambda c: (c["priorityRank"], c["input"]))
    spine = classified[0]

    # Derive clean slug and title
    if title and str(title).strip():
        final_title = str(title).strip()
    else:
        spine_raw = spine["input"]
        if re.match(r"^https?://", spine_raw, re.I):
            final_title = slugify(spine_raw.rstrip("/").split("/")[-1] or "study-topic").replace("-", " ").title()
        else:
            final_title = Path(spine_raw).stem.replace("_", " ").replace("-", " ").strip().title() or "Study Topic"
    final_slug = slugify(slug if slug else final_title, default="chapter")

    # 2. Safely ingest each source in Teacher-First order
    ingested: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    for idx, item in enumerate(classified):
        sub_slug = final_slug if idx == 0 else slugify(f"{final_slug}-{Path(item['input']).stem or idx}", max_len=64)
        try:
            res = ingest_auto(
                item["input"],
                workspace=ws,
                slug=sub_slug,
                register_tracker=False,
                enqueue=True,
                priority=85 if item["role"] == "teacher_spine" else 55,
            )
            res["_workspace"] = str(ws)
            res["role"] = item["role"]
            ingested.append(res)
        except Exception as exc:  # noqa: BLE001
            failed.append({"input": item["input"], "role": item["role"], "error": str(exc)})

    # 3. Initialize or non-destructively update chapter tracker
    tracker_path = ch_dir / f"{final_slug}-tracker.json"
    existing_tracker = load_json(tracker_path, default=None)
    ts = now_iso()

    source_entries = [
        {
            "id": f"src-{i + 1}",
            "label": Path(c["input"]).name if not re.match(r"^https?://", c["input"], re.I) else c["input"],
            "kind": c["detectedKind"],
            "role": c["role"],
            "order": i + 1,
            "notes": c["reason"],
        }
        for i, c in enumerate(classified)
    ]

    if isinstance(existing_tracker, dict) and existing_tracker.get("slug"):
        tracker_action = "updated"
        existing_labels = {str(s.get("label") or "") for s in (existing_tracker.get("sources") or []) if isinstance(s, dict)}
        srcs = list(existing_tracker.get("sources") or [])
        for se in source_entries:
            if se["label"] not in existing_labels:
                srcs.append(se)
        existing_tracker["sources"] = srcs
        existing_tracker["active"] = True
        existing_tracker["updatedAt"] = ts
        save_json_atomic(tracker_path, existing_tracker)
    else:
        tracker_action = "created"
        new_tracker = {
            "schemaVersion": 1,
            "slug": final_slug,
            "title": final_title,
            "subject": "General",
            "active": True,
            "createdAt": ts,
            "updatedAt": ts,
            "stepCounter": 0,
            "sources": source_entries,
            "topics": [
                {
                    "id": f"{final_slug}-t1",
                    "order": 1,
                    "title": f"Slice 1: {final_title} Foundations",
                    "level": "basics",
                    "prereqTopicIds": [],
                    "propIds": [],
                    "status": "pending",
                    "coveredPages": [],
                }
            ],
            "questions": [],
        }
        save_json_atomic(tracker_path, new_tracker)

    probe_hint = _extract_first_heading_or_hint(ingested, final_title)
    for item in ingested:
        item.pop("_workspace", None)

    next_move = plan_next_move(ws)

    return {
        "ok": len(ingested) > 0 or len(failed) == 0,
        "slug": final_slug,
        "title": final_title,
        "workspace": str(ws),
        "spineSource": spine,
        "classifiedSources": classified,
        "ingested": ingested,
        "failed": failed,
        "trackerPath": str(tracker_path),
        "trackerAction": tracker_action,
        "probeOneHint": probe_hint,
        "nextMove": next_move,
    }
