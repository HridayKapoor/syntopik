"""Zero-Dependency 2-Way Live Studio Server for Syntopik v2.0.

Pure Python 3.12+ standard library (`http.server.ThreadingHTTPServer`).
- Default binds strictly to `127.0.0.1` (auto-incrementing port on EADDRINUSE when port != 0).
- Rejects non-loopback Host/Origin on default mode (`403`) and enforces per-process
  `X-Syntopik-Token` CSRF protection on ALL POST/PATCH routes (`403` when missing/wrong).
- Enforces strict canonical path jailing (`resolve().is_relative_to(...)`).
- Opt-in `--lan` prints an ASCII QR pairing URL with a one-time `pair=<token>` exchanged
  for an `HttpOnly` cookie scoped EXCLUSIVELY to `/pocket`, `/api/pocket/state`, and
  `POST /api/review/batch` (all other `/api/*` and static routes remain strictly loopback-only).
"""

from __future__ import annotations

import base64
import contextlib
import errno
import hmac
import importlib
import ipaddress
import json
import math
import mimetypes
import os
import re
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import webbrowser
import zipfile
from datetime import date, datetime, timedelta, timezone
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from syntopik import __version__
from syntopik.paths import (
    resolve_chapters_dir,
    resolve_demos_dir,
    resolve_exams_dir,
    resolve_kg_dir,
    resolve_package_root,
    resolve_sources_dir,
    resolve_workspace,
    sync_hub,
)
from syntopik.schemas import now_iso, slugify, today_iso
from syntopik.storage import (
    load_json,
    load_jsonl,
    save_json_atomic,
    save_jsonl_atomic,
    workspace_lock,
)

__all__ = [
    "SyntopikStudioServer",
    "SyntopikRequestHandler",
    "create_server",
    "serve_forever",
]

POCKET_COOKIE_NAME = "syntopik_pocket_session"
POCKET_LAN_PATHS: frozenset[str] = frozenset({"/pocket", "/api/pocket/state", "/api/review/batch"})
MAX_JSON_BODY_BYTES = 8 * 1024 * 1024  # 8 MiB ceiling for exam payloads / batch sync
MAX_MCP_HTTP_BODY_BYTES = 1 * 1024 * 1024  # 1 MiB ceiling for loopback POST /mcp (aligned with mcp_http.MAX_MCP_BODY_BYTES)
MAX_SYNC_DELTA_BODY_BYTES = 10 * 1024 * 1024  # 10 MiB hard cap for CRDT delta merge
MAX_UPLOAD_BODY_BYTES = 32 * 1024 * 1024  # 32 MiB ceiling for PDF upload
MAX_IMPORT_UPLOAD_DECODED_BYTES = 15 * 1024 * 1024  # 15 MiB decoded upload ceiling
MAX_IMPORT_JSON_BODY_BYTES = 22 * 1024 * 1024  # ~15 MiB base64 + JSON envelope
MAX_DIFF_STEPS = 40
MAX_DIFF_EXPR_LEN = 512

_ALLOWED_UPLOAD_EXTS: frozenset[str] = frozenset({
    ".apkg", ".csv", ".tsv", ".ipynb", ".zip", ".md", ".markdown", ".txt", ".pdf",
})
_SEALED_INTERLEAVE_FIELDS: frozenset[str] = frozenset({
    "sealedChapter",
    "sealedPrinciple",
    "sealedAnswer",
    "sealedTopic",
    "sealedSubject",
    "sealedPropId",
    "sealedPrincipleId",
    "sealedOptionChapters",
    "sealedStep2Prompt",
    "canonicalAnswer",
    "correctPrinciple",
    "trueChapter",
    "expectedAnswer",
    "modelSolution",
    "solution",
    "answer",
    "chapter",
    "chapterSlug",
})


def _reject_non_finite(token: str) -> Any:
    raise ValueError(f"Non-finite JSON constant rejected: {token!r}")

mimetypes.add_type("application/javascript", ".mjs")
mimetypes.add_type("image/svg+xml", ".svg")
mimetypes.add_type("text/markdown; charset=utf-8", ".md")


def _load_script_module(module_name: str, workspace: Path, pkg_root: Path) -> Any:
    """Import a Syntopik script module cleanly from workspace/scripts or package_root/scripts."""
    for base in (workspace / "scripts", pkg_root / "scripts"):
        if base.is_dir():
            s = str(base)
            if s not in sys.path:
                sys.path.insert(0, s)
    root_s = str(pkg_root)
    if root_s not in sys.path:
        sys.path.insert(0, root_s)
    return importlib.import_module(module_name)


def _is_loopback_ip(addr: str | None) -> bool:
    if not addr:
        return False
    clean = addr.strip().strip("[]").split("%", 1)[0]
    if clean.lower() == "localhost":
        return True
    try:
        ip = ipaddress.ip_address(clean)
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
            return ip.ipv4_mapped.is_loopback
        return ip.is_loopback
    except ValueError:
        return False


_HOST_IPV6_RE = re.compile(r"^\[([0-9a-fA-F:]+)\](?::(\d{1,5}))?$")
_HOST_NAME_RE = re.compile(r"^([A-Za-z0-9.-]+)(?::(\d{1,5}))?$")
_ARXIV_ID_RE = re.compile(r"^(?:arXiv:)?(?:\d{4}\.\d{4,5}|[a-z-]+(?:\.[A-Z]{2})?/\d{7})(?:v\d+)?$", re.IGNORECASE)
_SAFE_PACK_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

_SEALED_FORK_FIELDS = frozenset({
    "sealedWinningMove",
    "sealedBlunder",
    "refutationMsg",
    "verifySpec",
    "mutationPool",
})
_SEALED_DECISION_FIELDS = frozenset({
    "bestMove",
    "expertRationale",
    "expertLens",
    "killCue",
    "firstMove",
    "rejectedDecoy",
    "sealedAnswer",
    "canonicalAnswer",
})
_SEALED_GATE_FIELDS = frozenset({
    "canonicalAnswer",
    "expectedAnswer",
    "revealedBody",
    "sealedAnswer",
    "answer",
})


def _valid_port_str(port_str: str | None) -> bool:
    if port_str is None:
        return True
    try:
        port_num = int(port_str)
        return 1 <= port_num <= 65535
    except ValueError:
        return False


def _extract_host_only(host_header: str | None) -> str | None:
    """Strictly parse RFC 3986 Host header; return None on missing or malformed syntax."""
    if not host_header:
        return None
    raw = host_header.strip()
    if not raw or any(ch.isspace() for ch in raw):
        return None
    m6 = _HOST_IPV6_RE.match(raw)
    if m6:
        if not _valid_port_str(m6.group(2)):
            return None
        try:
            ipaddress.IPv6Address(m6.group(1))
        except ValueError:
            return None
        return m6.group(1).lower()
    mn = _HOST_NAME_RE.match(raw)
    if mn:
        if not _valid_port_str(mn.group(2)):
            return None
        return mn.group(1).lower()
    return None


def _is_loopback_host(host_header: str | None) -> bool:
    host_only = _extract_host_only(host_header)
    if not host_only:
        return False
    return _is_loopback_ip(host_only)


def _is_loopback_origin(origin_header: str | None) -> bool:
    if not origin_header:
        return True
    try:
        parsed = urllib.parse.urlparse(origin_header.strip())
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    if parsed.username or parsed.password:
        return False
    return _is_loopback_host(parsed.netloc)


def _origin_matches_host(origin_header: str, host_header: str | None) -> bool:
    if not host_header:
        return False
    try:
        parsed = urllib.parse.urlparse(origin_header.strip())
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    if parsed.username or parsed.password:
        return False
    if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        return False
    return parsed.netloc.lower() == host_header.strip().lower()


def _detect_lan_ip() -> str:
    """Detect local LAN IPv4 address without sending network traffic."""
    for target in (("192.0.2.1", 80), ("10.255.255.255", 1)):
        sock: socket.socket | None = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.connect(target)
            ip = sock.getsockname()[0]
            if ip and not ip.startswith("127."):
                return ip
        except OSError:
            continue
        finally:
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass
    return "127.0.0.1"


def _compute_workspace_signature(ws: Path, kg_dir: Path, chapters_dir: Path) -> str:
    """Fast mtime+size digest across watched learner files for SSE & state cache invalidation."""
    watched_files: list[Path] = [
        kg_dir / "propositions.jsonl",
        kg_dir / "chains.jsonl",
        kg_dir / "mistakes.jsonl",
        kg_dir / "exam-attempts.jsonl",
        kg_dir / "exam-config.json",
        kg_dir / "study-config.json",
        kg_dir / "revlog.jsonl",
        kg_dir / "reading-queue.jsonl",
        kg_dir / "forks.jsonl",
        kg_dir / "arenas.jsonl",
        kg_dir / "decisions.jsonl",
        kg_dir / "learner-profile.json",
        kg_dir / "derivations.jsonl",
        kg_dir / "viva-sessions.jsonl",
        kg_dir / "evidence-ledger.jsonl",
        kg_dir / "whiteboard-session.json",
        kg_dir / "sync-state.json",
        kg_dir / "mentor-config.json",
        kg_dir / "blitz-sessions.json",
        kg_dir / "interleave-sessions.jsonl",
        kg_dir / "review-queue.md",
    ]
    for d in (chapters_dir, ws / "exams", ws / "sources"):
        if d.is_dir():
            try:
                for entry in sorted(d.iterdir()):
                    if entry.name.startswith(".") or entry.name.endswith(".tmp"):
                        continue
                    watched_files.append(entry)
            except OSError:
                pass
    parts: list[str] = []
    for p in watched_files:
        try:
            st = p.stat()
            parts.append(f"{p.name}:{st.st_mtime_ns}:{st.st_size}")
        except OSError:
            continue
    return "|".join(parts)


def _parse_graph_data_js(path: Path) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """Extract `window.__STUDY_GRAPH__` and `window.__STUDY_HUB__` cleanly via JSON raw_decode."""
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    decoder = json.JSONDecoder()
    markers = ("window.__STUDY_GRAPH__ =", "window.__STUDY_HUB__ =")
    objs: list[dict[str, Any]] = []
    for marker in markers:
        idx = text.find(marker)
        if idx == -1:
            return None
        pos = idx + len(marker)
        while pos < len(text) and text[pos].isspace():
            pos += 1
        try:
            obj, _ = decoder.raw_decode(text, pos)
        except json.JSONDecodeError:
            return None
        if not isinstance(obj, dict):
            return None
        objs.append(obj)
    return objs[0], objs[1]


def _build_python_fallback_state(ws: Path, kg_dir: Path, chapters_dir: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """Pure-Python schemaVersion 4 state fallback when Node.js `sync-graph.mjs` is unavailable."""
    from syntopik.fsrs_core import compute_calibration, detect_leeches
    from syntopik.schemas import normalize_chain

    today = today_iso()
    props = load_jsonl(kg_dir / "propositions.jsonl")
    raw_chains = load_jsonl(kg_dir / "chains.jsonl")
    mistakes = load_jsonl(kg_dir / "mistakes.jsonl")
    exam_attempts = load_jsonl(kg_dir / "exam-attempts.jsonl")
    revlog = load_jsonl(kg_dir / "revlog.jsonl")
    reading_queue = load_jsonl(kg_dir / "reading-queue.jsonl")

    chains: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    hyperedge_count = 0
    for rc in raw_chains:
        nc = normalize_chain(rc)
        if not nc:
            continue
        chains.append(nc)
        if nc["isJoint"]:
            hyperedge_count += 1
        for prem in nc["premises"]:
            edges.append({"from": prem, "to": nc["conclusion"], "relation": nc["relation"], "chainKey": nc["chainKey"]})

    counts = {"solid": 0, "shaky": 0, "misconception": 0, "planned": 0, "other": 0}
    due_items: list[dict[str, Any]] = []
    subjects: set[str] = set()
    bkt_vals: list[float] = []
    for p in props:
        st = str(p.get("status") or "planned")
        counts[st if st in counts else "other"] += 1
        if p.get("subject"):
            subjects.add(str(p["subject"]))
        bkt = p.get("bktMastery")
        if isinstance(bkt, (int, float)):
            bkt_vals.append(float(bkt))
        nxt = str(p.get("nextReview") or (p.get("fsrs") or {}).get("due") or "")[:10]
        if st in ("misconception", "shaky") or (nxt and nxt <= today):
            due_items.append({
                "id": p.get("id"),
                "title": p.get("title") or p.get("id"),
                "subject": p.get("subject") or "General",
                "chapter": p.get("chapter") or "",
                "status": st,
                "bktMastery": bkt,
                "nextReview": nxt or None,
                "notes": p.get("notes") or "",
            })

    trackers: list[dict[str, Any]] = []
    if chapters_dir.is_dir():
        for tf in sorted(chapters_dir.glob("*-tracker.json")):
            obj = load_json(tf)
            if isinstance(obj, dict):
                topics = [t for t in (obj.get("topics") or []) if isinstance(t, dict)]
                questions = [q for q in (obj.get("questions") or []) if isinstance(q, dict)]
                topics_total = len(topics)
                topics_mastered = sum(1 for t in topics if t.get("status") == "mastered")
                questions_total = len(questions)
                questions_solved = sum(1 for q in questions if q.get("status") in ("solved", "mastered"))
                denom = topics_total + questions_total
                pct = round(((topics_mastered + questions_solved) / denom) * 100.0, 1) if denom > 0 else 0.0
                next_topic = next((t for t in topics if t.get("status") != "mastered"), None)
                next_hint = (
                    f"Teach {next_topic.get('title') or next_topic.get('id')}"
                    if next_topic
                    else ("All topics mastered" if topics_total else "Add topics to begin")
                )
                summary = dict(obj)
                summary.setdefault("pct", pct)
                summary.setdefault("topicsTotal", topics_total)
                summary.setdefault("topicsMastered", topics_mastered)
                summary.setdefault("questionsTotal", questions_total)
                summary.setdefault("questionsSolved", questions_solved)
                summary.setdefault("nextHint", next_hint)
                trackers.append(summary)
    active_tracker = next((t for t in trackers if t.get("active")), trackers[0] if trackers else None)

    by_cat: dict[str, int] = {}
    by_ch: dict[str, int] = {}
    by_subj: dict[str, int] = {}
    for m in mistakes:
        cat = str(m.get("category") or "other")
        by_cat[cat] = by_cat.get(cat, 0) + 1
        ch_name = str(m.get("chapter") or "General")
        by_ch[ch_name] = by_ch.get(ch_name, 0) + 1
        subj_name = str(m.get("subject") or "General")
        by_subj[subj_name] = by_subj.get(subj_name, 0) + 1

    cal = compute_calibration(revlog)
    leeches = detect_leeches(props, revlog)
    rq_path = kg_dir / "review-queue.md"
    rq_md = rq_path.read_text(encoding="utf-8") if rq_path.is_file() else ""

    graph = {
        "propositions": props,
        "chains": chains,
        "normalizedEdges": edges,
        "chainCount": len(chains),
    }
    hub = {
        "schemaVersion": 5,
        "generatedAt": now_iso(),
        "today": today,
        "workspaceName": ws.name,
        "stats": {
            "totalNodes": len(props),
            "totalEdges": len(edges),
            "chainCount": len(chains),
            "hyperedgeCount": hyperedge_count,
            "counts": counts,
            "avgBktMastery": round(sum(bkt_vals) / len(bkt_vals), 4) if bkt_vals else None,
            "bktTrackedCount": len(bkt_vals),
            "dueCount": len(due_items),
            "subjects": sorted(subjects),
            "chaptersCount": 0,
            "revisionSheetsCount": 0,
            "trackersCount": len(trackers),
            "activeChapterPct": active_tracker.get("pct") if isinstance(active_tracker, dict) else None,
            "audioBriefsCount": 0,
            "demosCount": 0,
            "animationsCount": 0,
            "sourcesCount": 0,
            "mistakesCount": len(mistakes),
            "examsCount": 0,
            "examAttemptsCount": len(exam_attempts),
            "harnessLogCount": 0,
            "revlogCount": len(revlog),
            "leechesCount": len(leeches),
            "readingQueueCount": len(reading_queue),
            "brierScore": cal.get("brierScore"),
        },
        "dueItems": due_items,
        "reviewQueueMarkdown": rq_md,
        "chapters": [],
        "chapterTrackers": trackers,
        "activeChapterTracker": active_tracker,
        "audioBriefs": [],
        "demos": [],
        "animations": [],
        "sources": [],
        "mistakes": mistakes,
        "mistakeStats": {
            "total": len(mistakes),
            "byCategory": [{"key": k, "count": v} for k, v in sorted(by_cat.items(), key=lambda kv: -kv[1])],
            "byChapter": [{"key": k, "count": v} for k, v in sorted(by_ch.items(), key=lambda kv: -kv[1])],
            "bySubject": by_subj,
        },
        "examAttempts": exam_attempts,
        "examFiles": [],
        "examStats": {"attemptsCount": len(exam_attempts), "avgPct": None, "latestAttempt": exam_attempts[-1] if exam_attempts else None},
        "harnessLog": [],
        "retentionHeatmap": {
            "windowDays": 365,
            "startDate": today,
            "endDate": today,
            "totalEvents": len(revlog) + len(exam_attempts),
            "activeDays": 0,
            "maxDailyCount": 0,
            "byDate": {},
            "days": [],
        },
        "calibration": cal,
        "leeches": leeches,
        "readingQueue": reading_queue,
        "rosetta": _build_rosetta_payload(kg_dir),
        "syncStatus": _build_sync_status_payload(ws),
        "readinessSummary": _build_readiness_summary(ws, kg_dir, chapters_dir),
        "interleaveStats": _build_interleave_stats_payload(ws, kg_dir),
    }
    return graph, hub


def _compute_triage_recommended(kg_dir: Path, hub: dict[str, Any]) -> bool:
    """Return True when an exam date is within 72 hours (or backlog is high within 7 days)."""
    today = date.today()
    exam_dates: list[date] = []
    for cfg_name in ("exam-config.json", "study-config.json"):
        cfg = load_json(kg_dir / cfg_name)
        if not isinstance(cfg, dict):
            continue
        for key in ("examDate", "exam_date", "targetExamDate", "nextExamDate"):
            raw = cfg.get(key)
            if isinstance(raw, str) and len(raw.strip()) >= 10:
                try:
                    exam_dates.append(date.fromisoformat(raw.strip()[:10]))
                except ValueError:
                    pass
    active = hub.get("activeChapterTracker")
    if isinstance(active, dict):
        for key in ("examDate", "targetDate"):
            raw = active.get(key)
            if isinstance(raw, str) and len(raw.strip()) >= 10:
                try:
                    exam_dates.append(date.fromisoformat(raw.strip()[:10]))
                except ValueError:
                    pass
    due_count = int((hub.get("stats") or {}).get("dueCount") or 0)
    for d in exam_dates:
        delta = (d - today).days
        if 0 <= delta <= 3:
            return True
        if 0 <= delta <= 7 and due_count >= 20:
            return True
    return False


# ----------------------------------------------------------------------
# Syntopik v3.0 sanitization, packs, root-cause & explorable helpers
# ----------------------------------------------------------------------


def _sanitize_fork(fork: dict[str, Any]) -> dict[str, Any]:
    """Return a public fork object guaranteed free of sealed winning/blunder moves."""
    out = {k: v for k, v in fork.items() if k not in _SEALED_FORK_FIELDS}
    if "mutationCount" not in out:
        mp = fork.get("mutationPool")
        out["mutationCount"] = len(mp) if isinstance(mp, list) else 0
    if "engine" not in out:
        vs = fork.get("verifySpec")
        out["engine"] = vs.get("engine", "exact") if isinstance(vs, dict) else "exact"
    return out


def _sanitize_arena(arena: dict[str, Any]) -> dict[str, Any]:
    """Return arena metadata WITHOUT leaking sealedReference or surviving mutant code/expr/trapTag."""
    mutants = arena.get("sealedMutants") or []
    killed = {str(k) for k in (arena.get("killedMutants") or [])}
    total = len(mutants) if isinstance(mutants, list) else int(arena.get("totalMutants") or 0)
    if isinstance(mutants, list) and mutants:
        killed_n = sum(1 for m in mutants if isinstance(m, dict) and str(m.get("id")) in killed)
    else:
        killed_n = int(arena.get("killedCount") or len(killed))
    survived_n = max(0, total - killed_n)

    raw_repairs = arena.get("lemmaRepairs") or []
    public_repairs: list[dict[str, Any]] = []
    if isinstance(raw_repairs, list):
        for r in raw_repairs:
            if isinstance(r, dict):
                public_repairs.append({
                    "id": r.get("id"),
                    "lemma": r.get("lemma"),
                    "ts": r.get("ts"),
                    "persistedToProp": bool(r.get("persistedToProp", False)),
                })

    raw_assumps = arena.get("assumptionPredicates")
    if isinstance(raw_assumps, list):
        assumptions = [
            (p.get("description") or p.get("expr") or str(p)) if isinstance(p, dict) else str(p)
            for p in raw_assumps
        ]
    elif isinstance(arena.get("assumptions"), list):
        assumptions = [str(x) for x in arena["assumptions"]]
    else:
        assumptions = []

    status = str(arena.get("status") or ("cleared" if total > 0 and killed_n >= total else "active")).lower()
    return {
        "id": arena.get("id"),
        "propId": arena.get("propId"),
        "domain": arena.get("domain"),
        "title": arena.get("title"),
        "claim": arena.get("claim"),
        "entry": arena.get("entry"),
        "lang": arena.get("lang"),
        "status": status,
        "assumptions": assumptions,
        "totalMutants": total,
        "killedCount": killed_n,
        "survivedCount": survived_n,
        "killedMutants": sorted(killed),
        "bestMinimalSize": arena.get("bestMinimalSize"),
        "lemmaRepairsCount": len(public_repairs),
        "lemmaRepairs": public_repairs,
        "updatedAt": arena.get("updatedAt"),
    }


def _sanitize_decision(dec: dict[str, Any]) -> dict[str, Any]:
    """Strip sealed expert move/rationale from ungraded decisions while keeping graded records."""
    status = str(dec.get("status") or "").strip().lower()
    has_grade_flags = any(
        isinstance(dec.get(k), bool) for k in ("killCueHit", "firstMoveHit", "decoyHit", "correct")
    )
    is_graded = (dec.get("graded") is True) or (
        dec.get("graded") is not False
        and status not in ("pending", "ungraded", "open", "draft")
        and has_grade_flags
    )
    if is_graded:
        return dict(dec)
    return {k: v for k, v in dec.items() if k not in _SEALED_DECISION_FIELDS}


def _sanitize_derivation_row(deriv: dict[str, Any], *, level: int = 3) -> dict[str, Any]:
    """Expose derivation DAG structure with blackout-hidden steps masked (zero secret leak)."""
    steps = [s for s in (deriv.get("steps") or []) if isinstance(s, dict)]
    non_axioms = [s for s in steps if str(s.get("role") or "step") != "axiom"]
    hidden_ids: set[str] = set()
    if non_axioms:
        lvl = max(1, min(3, int(level)))
        if lvl == 1:
            inter = [s for s in non_axioms if s.get("role") == "step"]
            pool = inter if inter else non_axioms
            target_n = max(1, round(len(pool) * 0.4))
            stride = max(1, len(pool) // target_n)
            for idx in range(len(pool) - 1, -1, -stride):
                hidden_ids.add(str(pool[idx].get("id") or ""))
                if len(hidden_ids) >= target_n:
                    break
        elif lvl == 2:
            target_n = max(1, round(len(non_axioms) * 0.65))
            for s in reversed(non_axioms):
                if len(hidden_ids) >= target_n:
                    break
                hidden_ids.add(str(s.get("id") or ""))
        else:
            hidden_ids = {str(s.get("id") or "") for s in non_axioms}

    public_steps: list[dict[str, Any]] = []
    for s in steps:
        sid = str(s.get("id") or "")
        is_hidden = sid in hidden_ids
        public_steps.append({
            "id": sid,
            "label": s.get("label", sid),
            "role": s.get("role", "step"),
            "dependsOn": list(s.get("dependsOn") or []),
            "hidden": is_hidden,
            "expr": None if is_hidden else s.get("expr"),
            "note": None if is_hidden else (s.get("note") or None),
        })

    return {
        "id": deriv.get("id"),
        "propId": deriv.get("propId"),
        "chapter": deriv.get("chapter"),
        "title": deriv.get("title"),
        "domain": deriv.get("domain"),
        "stepCount": len(steps),
        "axiomCount": sum(1 for s in steps if s.get("role") == "axiom"),
        "theoremCount": sum(1 for s in steps if s.get("role") == "theorem"),
        "hiddenCount": len(hidden_ids),
        "hiddenStepIds": [s["id"] for s in public_steps if s["hidden"]],
        "steps": public_steps,
        "updatedAt": deriv.get("updatedAt"),
    }


def _sanitize_viva_session(sess: dict[str, Any]) -> dict[str, Any]:
    """Return a clean Viva session summary without spoiling future attack prompts."""
    prompts = [p for p in (sess.get("prompts") or []) if isinstance(p, dict)]
    turns = [t for t in (sess.get("turns") or []) if isinstance(t, dict)]
    cur = int(sess.get("currentTurn") or (len(turns) + 1))
    status = str(sess.get("status") or "in_progress")
    cur_prompt = (
        prompts[cur - 1]
        if status == "in_progress" and 1 <= cur <= len(prompts)
        else None
    )
    visible_prompts = prompts if status == "completed" else prompts[: max(1, min(cur, len(prompts)))]
    return {
        "id": sess.get("id"),
        "propId": sess.get("propId"),
        "propTitle": sess.get("propTitle"),
        "subject": sess.get("subject"),
        "chapter": sess.get("chapter"),
        "status": status,
        "currentTurn": cur,
        "totalTurns": int(sess.get("totalTurns") or 5),
        "turnsCompleted": len(turns),
        "turns": turns,
        "currentPrompt": cur_prompt,
        "prompts": visible_prompts,
        "commitmentLedger": sess.get("commitmentLedger"),
        "scores": sess.get("scores"),
        "composite": (sess.get("scores") or {}).get("composite") if isinstance(sess.get("scores"), dict) else None,
        "outcome": sess.get("outcome"),
        "evidenceRecorded": bool(sess.get("evidenceRecorded")),
        "demotedToShaky": bool(sess.get("demotedToShaky")),
        "createdAt": sess.get("createdAt"),
        "updatedAt": sess.get("updatedAt"),
    }


def _sanitize_whiteboard_frame(frame: dict[str, Any]) -> dict[str, Any]:
    """Strip sealed canonical/expected answers and revealedBody from locked prediction_gate frames."""
    fr = dict(frame)
    kind = str(fr.get("kind") or "").lower()
    raw_gate = fr.get("gate")
    gate = dict(raw_gate) if isinstance(raw_gate, dict) else None
    raw_payload = fr.get("payload")
    payload = dict(raw_payload) if isinstance(raw_payload, dict) else {}

    is_locked_gate = kind == "prediction_gate" and (
        bool(fr.get("locked", True)) or not (gate and gate.get("unlockedAt"))
    )
    if is_locked_gate:
        for k in _SEALED_GATE_FIELDS:
            fr.pop(k, None)
            payload.pop(k, None)
            if gate is not None:
                gate.pop(k, None)
        fr["body"] = ""
        fr["locked"] = True
    elif kind == "prediction_gate" and gate is not None:
        if "canonicalAnswer" not in fr and gate.get("expectedAnswer") is not None:
            fr["canonicalAnswer"] = gate.get("expectedAnswer")

    if gate is not None:
        fr["gate"] = gate
    fr["payload"] = payload
    return fr


def _sanitize_whiteboard(wb: dict[str, Any]) -> dict[str, Any]:
    """Sanitize every frame in a whiteboard session so locked gates never leak answers."""
    out = dict(wb)
    raw_frames = wb.get("frames") or []
    out["frames"] = [_sanitize_whiteboard_frame(f) for f in raw_frames if isinstance(f, dict)]
    pins = [p for p in (wb.get("pins") or []) if isinstance(p, dict)]
    out["pins"] = pins
    pending = [p for p in pins if not p.get("resolved")]
    out["pendingPins"] = pending
    out["pendingPinCount"] = len(pending)
    out["frameCount"] = len(out["frames"])
    return out


def _find_packs_dirs(ws: Path, pkg: Path) -> list[Path]:
    dirs: list[Path] = []
    seen: set[Path] = set()
    for cand in (ws / "packs", pkg / "packs", pkg / "_bundle" / "packs"):
        try:
            res = cand.resolve()
        except OSError:
            continue
        if res.is_dir() and res not in seen:
            seen.add(res)
            dirs.append(res)
    return dirs


def _resolve_starter_pack_slug(pack_slug: str, ws: Path, pkg: Path) -> tuple[str, Path]:
    """Strictly validate a bundled starter pack slug against `packs/*.syntopik-pack.zip`."""
    slug = str(pack_slug or "").strip().lower()
    if not _SAFE_PACK_SLUG_RE.fullmatch(slug):
        raise ValueError(f"Invalid packSlug {pack_slug!r}: only lowercase letters, digits, '-' and '_' allowed.")
    filename = f"{slug}.syntopik-pack.zip"
    for pdir in _find_packs_dirs(ws, pkg):
        cand = (pdir / filename).resolve()
        if not cand.is_relative_to(pdir):
            continue
        if cand.is_file():
            return slug, cand
    raise FileNotFoundError(f"Bundled starter pack '{slug}' not found in packs/.")


def _scan_starter_packs(ws: Path, pkg: Path) -> list[dict[str, Any]]:
    """Scan bundled `packs/*.syntopik-pack.zip` manifests safely without extracting files."""
    packs_out: list[dict[str, Any]] = []
    seen_slugs: set[str] = set()
    max_manifest_bytes = 4 * 1024 * 1024
    for pdir in _find_packs_dirs(ws, pkg):
        try:
            entries = sorted(pdir.glob("*.syntopik-pack.zip"))
        except OSError:
            continue
        for zp in entries:
            try:
                resolved = zp.resolve()
                if not resolved.is_relative_to(pdir) or not resolved.is_file():
                    continue
            except OSError:
                continue
            slug = zp.name[:-len(".syntopik-pack.zip")].strip().lower()
            if not _SAFE_PACK_SLUG_RE.fullmatch(slug) or slug in seen_slugs:
                continue
            meta: dict[str, Any] = {
                "slug": slug,
                "id": slug,
                "title": slug.replace("-", " ").title(),
                "subject": "General",
                "author": "Syntopik",
                "license": "CC-BY-4.0",
                "filename": zp.name,
                "sizeBytes": zp.stat().st_size,
                "propositionCount": 0,
                "chainCount": 0,
                "chapterCount": 0,
                "forkCount": 0,
                "arenaCount": 0,
                "derivationCount": 0,
                "examCount": 0,
            }
            try:
                with zipfile.ZipFile(resolved, "r") as zf:
                    names = set(zf.namelist())
                    if "pack-manifest.json" in names:
                        with zf.open("pack-manifest.json", "r") as fh:
                            raw = fh.read(max_manifest_bytes + 1)
                        if len(raw) <= max_manifest_bytes:
                            pm = json.loads(raw.decode("utf-8"))
                            if isinstance(pm, dict):
                                meta["id"] = str(pm.get("id") or slug)
                                meta["title"] = str(pm.get("title") or meta["title"])
                                meta["subject"] = str(pm.get("subject") or meta["subject"])
                                meta["author"] = str(pm.get("author") or meta["author"])
                                meta["license"] = str(pm.get("license") or meta["license"])
                                if isinstance(pm.get("description"), str):
                                    meta["description"] = pm["description"]
                                meta["propositionCount"] = len(pm.get("propositions") or [])
                                meta["chainCount"] = len(pm.get("chains") or [])
                                comp_files = [str(x) for x in (pm.get("companionFiles") or [])]
                                meta["chapterCount"] = len(pm.get("chapters") or []) or sum(
                                    1 for cf in comp_files if cf.startswith("chapters/") and cf.endswith(".md")
                                )
                                meta["forkCount"] = len(pm.get("forks") or [])
                                meta["arenaCount"] = len(pm.get("arenas") or [])
                                meta["derivationCount"] = len(pm.get("derivations") or [])
                                meta["examCount"] = len(pm.get("exams") or [])
                    if meta["forkCount"] == 0 and "forks.jsonl" in names:
                        with zf.open("forks.jsonl", "r") as fh:
                            f_raw = fh.read(512 * 1024).decode("utf-8", errors="ignore")
                        meta["forkCount"] = sum(1 for ln in f_raw.splitlines() if ln.strip())
                    if meta["arenaCount"] == 0 and "arenas.jsonl" in names:
                        with zf.open("arenas.jsonl", "r") as fh:
                            a_raw = fh.read(512 * 1024).decode("utf-8", errors="ignore")
                        meta["arenaCount"] = sum(1 for ln in a_raw.splitlines() if ln.strip())
            except Exception:
                pass
            seen_slugs.add(slug)
            packs_out.append(meta)
    packs_out.sort(key=lambda p: p["slug"])
    return packs_out


def _compute_root_cause_hotspots(kg_dir: Path) -> dict[str, Any]:
    """Compute `{byProp, blastRadius}` using Bayesian Noisy-OR fault localization."""
    from syntopik.root_cause import _build_graph_maps, find_blast_radius, localize_fault

    props = load_jsonl(kg_dir / "propositions.jsonl")
    chains = load_jsonl(kg_dir / "chains.jsonl")
    mistakes = load_jsonl(kg_dir / "mistakes.jsonl")
    shared = {"propositions.jsonl": props, "chains.jsonl": chains, "mistakes.jsonl": mistakes}
    blast = find_blast_radius(kg_dir, limit=12, preloaded=shared)
    by_prop: dict[str, float] = {}

    for item in blast:
        pid = str(item.get("propId") or "")
        if not pid:
            continue
        weak_n = int(item.get("weakDownstreamCount") or 0)
        miss_n = int(item.get("downstreamMissCount") or 0)
        sev_base = 0.50 if item.get("status") == "misconception" else 0.38
        est = min(0.96, round(sev_base + 0.08 * weak_n + 0.05 * miss_n, 4))
        by_prop[pid] = max(by_prop.get(pid, 0.0), est)

    failed_leaves: list[str] = []
    seen_leaves: set[str] = set()
    for p in props:
        pid = str(p.get("id") or "")
        if not pid or pid in seen_leaves:
            continue
        if str(p.get("status") or "").lower() in ("misconception", "shaky"):
            seen_leaves.add(pid)
            failed_leaves.append(pid)
    for m in reversed(mistakes[-20:]):
        pid = str(m.get("propId") or "")
        if pid and pid not in seen_leaves:
            seen_leaves.add(pid)
            failed_leaves.append(pid)

    shared_loc = {"propositions.jsonl": props, "chains.jsonl": chains}
    if failed_leaves and chains:
        shared_loc["incomingMap"] = _build_graph_maps(chains)[0]
    for leaf_id in failed_leaves[:25]:
        try:
            loc = localize_fault(kg_dir, leaf_id, max_depth=4, preloaded=shared_loc)
            if loc.get("ok"):
                for s in loc.get("suspects") or []:
                    spid = str(s.get("propId") or "")
                    score = float(s.get("suspectRootScore") or 0.0)
                    if spid and score > by_prop.get(spid, 0.0):
                        by_prop[spid] = round(score, 4)
        except Exception:
            continue

    return {
        "byProp": by_prop,
        "blastRadius": blast,
    }


def _compile_chapter_explorables(chapters_dir: Path) -> dict[str, list[dict[str, Any]]]:
    """Compile all `:::explore` blocks across `chapters/*.md` into `{chapterSlug: [spec...]}`."""
    if not chapters_dir.is_dir():
        return {}
    from syntopik.explorable import parse_explorable_blocks

    out: dict[str, list[dict[str, Any]]] = {}
    for md_path in sorted(chapters_dir.rglob("*.md")):
        if md_path.name.startswith("."):
            continue
        try:
            text = md_path.read_text(encoding="utf-8")
        except OSError:
            continue
        if ":::explore" not in text:
            continue
        try:
            specs = parse_explorable_blocks(text, strict=False)
        except Exception:
            specs = []
        if specs:
            out[md_path.stem] = specs
    return out


def _build_rosetta_payload(kg_dir: Path, *, min_score: float = 0.35) -> dict[str, Any]:
    """Compute structural isomorphism chords & archetype count safely."""
    try:
        from syntopik.isomorphism import discover_isomorphisms, load_archetypes

        disc = discover_isomorphisms(kg_dir, min_score=min_score)
        archetypes = load_archetypes()
        return {
            **disc,
            "archetypesCount": len(archetypes),
        }
    except Exception:
        return {
            "ok": True,
            "kgDir": str(kg_dir),
            "chords": [],
            "archetypesMatched": [],
            "chordCount": 0,
            "persistedCount": 0,
            "discoveredCount": 0,
            "archetypesCount": 0,
        }


def _latest_iso_timestamp(*values: Any) -> str | None:
    cands = [str(v).strip() for v in values if isinstance(v, str) and str(v).strip()]
    return max(cands) if cands else None


def _build_sync_status_payload(workspace: Path) -> dict[str, Any]:
    """Read multi-device CRDT sync status safely."""
    try:
        from syntopik.sync_delta import get_sync_status

        st = get_sync_status(workspace)
        last_synced = _latest_iso_timestamp(st.get("lastMergedAt"), st.get("lastExportedAt"))
        return {
            **st,
            "lastSyncedAt": last_synced,
        }
    except Exception:
        return {
            "ok": True,
            "workspace": str(workspace),
            "initialized": False,
            "deviceId": None,
            "lamportClock": 0,
            "lastExportedAt": None,
            "lastMergedAt": None,
            "lastSyncedAt": None,
            "peerClocks": {},
            "mergedDeltaCount": 0,
            "trackerSlugs": [],
            "counts": {},
            "ledgerIntegrity": {"ok": True, "count": 0, "brokenAt": None},
        }


def _sanitize_interleave_item(obj: Any) -> Any:
    """Defense-in-depth sanitizer removing ALL sealed chapter/principle/answer keys from blind items."""
    if not isinstance(obj, dict):
        return obj
    clean: dict[str, Any] = {}
    for k, v in obj.items():
        if k in _SEALED_INTERLEAVE_FIELDS or str(k).lower().startswith("sealed"):
            continue
        if isinstance(v, dict):
            clean[k] = _sanitize_interleave_item(v)
        elif isinstance(v, list):
            clean[k] = [_sanitize_interleave_item(elem) for elem in v]
        else:
            clean[k] = v
    return clean


def _sanitize_interleave_payload(res: dict[str, Any]) -> dict[str, Any]:
    out = dict(res)
    for k in list(out.keys()):
        if k in _SEALED_INTERLEAVE_FIELDS or str(k).lower().startswith("sealed"):
            out.pop(k, None)
    if isinstance(out.get("items"), list):
        out["items"] = [_sanitize_interleave_item(it) for it in out["items"]]
    return out


def _normalize_shield_text(raw: Any) -> str:
    return re.sub(r"\s+", " ", str(raw or "")).strip().lower()


def _text_leaks_sealed_secret(raw_text: str, sealed_norms: list[str]) -> bool:
    """Return True if raw_text echoes any full, whitespace-collapsed, or clipped sealed secret."""
    norm_txt = _normalize_shield_text(raw_text)
    if not norm_txt or not sealed_norms:
        return False
    # Strip leading generic prefixes & trailing ellipsis so arbitrary word-boundary clips still match
    stripped = re.sub(r"^(?:if|then)\s+", "", norm_txt)
    stripped = re.sub(r"^stop and verify before committing:\s*", "", stripped)
    stripped_no_dots = re.sub(r"(?:\.\.\.|…)+$", "", stripped).strip()
    for sn in sealed_norms:
        if not sn:
            continue
        if sn in norm_txt:
            return True
        if len(sn) >= 16:
            for plen in (24, 48, 80, 100):
                if len(sn) >= plen and sn[:plen] in norm_txt:
                    return True
            if len(sn) >= 16 and sn[:16] in norm_txt:
                return True
        if len(stripped_no_dots) >= 12 and sn.startswith(stripped_no_dots):
            return True
        # Also catch suffix after ": " inside thenAction when clipped before 16 chars
        if ":" in norm_txt:
            tail = re.sub(r"(?:\.\.\.|…)+$", "", norm_txt.rsplit(":", 1)[1]).strip()
            if len(tail) >= 12 and sn.startswith(tail):
                return True
    return False


def _sanitize_premortem_shield(shield: Any, kg_dir: Path) -> dict[str, Any]:
    """Ensure Pre-Mortem Shield bullets never echo sealed Blunder Fork secrets."""
    if not isinstance(shield, dict):
        return {"ok": True, "sourceBasis": "cold_start_minimal", "count": 0, "bullets": []}
    bullets = shield.get("bullets")
    if not isinstance(bullets, list) or not bullets:
        return dict(shield)
    sealed_by_fork: dict[str, list[str]] = {}
    all_sealed_norms: list[str] = []
    unresolved_fork_ids: set[str] = set()
    try:
        for fk in load_jsonl(kg_dir / "forks.jsonl"):
            if not isinstance(fk, dict):
                continue
            if str(fk.get("status") or "").lower() in ("mastered", "cleared", "archived"):
                continue
            fid = str(fk.get("id") or "").strip()
            if fid:
                unresolved_fork_ids.add(fid)
            fk_norms: list[str] = []
            for sk in _SEALED_FORK_FIELDS:
                norm_val = _normalize_shield_text(fk.get(sk))
                if norm_val:
                    fk_norms.append(norm_val)
                    all_sealed_norms.append(norm_val)
            if fid and fk_norms:
                sealed_by_fork[fid] = fk_norms
    except Exception:
        all_sealed_norms = []
        sealed_by_fork = {}
        unresolved_fork_ids = set()
    if not all_sealed_norms and not unresolved_fork_ids:
        return dict(shield)
    clean_bullets: list[dict[str, Any]] = []
    for b in bullets:
        if not isinstance(b, dict):
            continue
        nb = dict(b)
        b_fid = str(nb.get("forkId") or "").strip()
        is_unresolved_fork_bullet = (b_fid and b_fid in unresolved_fork_ids) or (
            str(nb.get("sourceKind") or "").lower() == "blunder_fork" and bool(unresolved_fork_ids)
        )
        fork_specific = sealed_by_fork.get(b_fid, []) if b_fid else []
        check_pool = fork_specific + [s for s in all_sealed_norms if s not in fork_specific]
        then_txt = str(nb.get("thenAction") or "")
        if _text_leaks_sealed_secret(then_txt, check_pool) or (
            is_unresolved_fork_bullet
            and not _normalize_shield_text(then_txt).startswith(
                "then verify boundary conditions & invariant before executing your first move"
            )
        ):
            nb["thenAction"] = "THEN verify boundary conditions & invariant before executing your first move."
        if_txt = str(nb.get("ifCue") or "")
        if _text_leaks_sealed_secret(if_txt, check_pool):
            nb["ifCue"] = "IF you encounter this unresolved Blunder Fork setup"
        clean_bullets.append(nb)
    out = dict(shield)
    out["bullets"] = clean_bullets
    out["count"] = len(clean_bullets)
    return out


def _build_readiness_summary(
    workspace: Path,
    kg_dir: Path,
    chapters_dir: Path,
) -> dict[str, Any]:
    """Compute compact Exam-Day Readiness summary safely for `/api/state`."""
    try:
        from syntopik.readiness import compute_readiness

        rd = compute_readiness(workspace, kg_dir=kg_dir, chapters_dir=chapters_dir)
        if not isinstance(rd, dict):
            raise ValueError("Invalid readiness result")
        src = str(rd.get("examDateSource") or "default_14d")
        return {
            "ok": bool(rd.get("ok", True)),
            "readinessIndex": rd.get("readinessIndex", 0.0),
            "band": rd.get("band", "needs_foundation"),
            "predictedScorePct": rd.get("predictedScorePct") or {"low": 0.0, "expected": 0.0, "high": 0.0},
            "predictedMarks": rd.get("predictedMarks"),
            "examDate": rd.get("examDate"),
            "examDateSource": src,
            "daysToExam": rd.get("daysToExam"),
            "hasExamConfig": src != "default_14d",
            "taperMode": rd.get("taperMode") or {"active": False, "phase": "none", "allowNewConcepts": True},
            "preMortemShield": _sanitize_premortem_shield(rd.get("preMortemShield"), kg_dir),
            "topLevers": list(rd.get("topLevers") or [])[:3],
            "pillars": rd.get("pillars") or {},
            "executionConfidence": rd.get("executionConfidence", "untested"),
            "uncertaintyReasons": list(rd.get("uncertaintyReasons") or []),
        }
    except Exception:
        return {
            "ok": True,
            "readinessIndex": 0.0,
            "band": "needs_foundation",
            "predictedScorePct": {"low": 0.0, "expected": 0.0, "high": 0.0},
            "predictedMarks": None,
            "examDate": None,
            "examDateSource": "default_14d",
            "daysToExam": None,
            "hasExamConfig": False,
            "taperMode": {"active": False, "phase": "none", "allowNewConcepts": True},
            "preMortemShield": {"ok": True, "sourceBasis": "cold_start_minimal", "count": 0, "bullets": []},
            "topLevers": [],
            "pillars": {},
            "executionConfidence": "untested",
            "uncertaintyReasons": [],
        }


def _build_progress_report_payload(kg_dir: Path, days: int = 7) -> dict[str, Any]:
    """Weekly progress report (same numbers as `syntopik report`), never raising."""
    try:
        from syntopik.report import build_progress_report

        return build_progress_report(kg_dir, days=days)
    except Exception:
        return {"ok": False, "kind": "progress_report", "windowDays": days}


def _build_interleave_stats_payload(workspace: Path, kg_dir: Path) -> dict[str, Any]:
    """Compute sanitized blind-chapter interleave stats safely."""
    try:
        from syntopik.interleave import get_interleave_stats

        st = get_interleave_stats(workspace, kg_dir=kg_dir)
        return _sanitize_interleave_payload(st) if isinstance(st, dict) else {"ok": True, "totalSessions": 0, "totalItemsChecked": 0}
    except Exception:
        return {
            "ok": True,
            "totalSessions": 0,
            "completedSessions": 0,
            "totalItemsChecked": 0,
            "tier1Correct": 0,
            "tier1AccuracyPct": None,
            "principleAccuracyPct": None,
            "step2Correct": 0,
            "step2Total": 0,
            "step2AccuracyPct": None,
            "medianIdentifyMs": None,
            "medianIdentifySec": None,
            "lastSessionAt": None,
            "daysSinceLastInterleave": None,
            "distinctChaptersPracticed": 0,
            "confusionMatrix": {},
            "topConfusionPairs": [],
        }


def _build_agents_compat_payload(workspace: Path) -> dict[str, Any]:
    """Build 14-coding-agent compatibility matrix safely."""
    try:
        from syntopik.agents_compat import get_compat_matrix

        return get_compat_matrix(workspace)
    except Exception:
        return {
            "ok": True,
            "totalHarnesses": 0,
            "detectedCount": 0,
            "configuredCount": 0,
            "attachedCount": 0,
            "httpSupportedCount": 0,
            "activeHarness": "generic",
            "harnesses": [],
        }


def _build_compat_summary(workspace: Path, mcp_http_endpoint: str, *, mcp_enabled: bool) -> dict[str, Any]:
    mat = _build_agents_compat_payload(workspace)
    return {
        "totalHarnesses": int(mat.get("totalHarnesses") or 0),
        "detectedCount": int(mat.get("detectedCount") or 0),
        "configuredCount": int(mat.get("configuredCount") or 0),
        "attachedCount": int(mat.get("attachedCount") or 0),
        "httpSupportedCount": int(mat.get("httpSupportedCount") or 0),
        "activeHarness": str(mat.get("activeHarness") or "generic"),
        "mcpHttpEndpoint": mcp_http_endpoint,
        "mcpHttpEnabled": bool(mcp_enabled),
    }


def _sanitize_upload_filename(raw_name: Any) -> tuple[str, str]:
    """Validate strictly single-segment upload filename and return `(safe_filename, ext_lower)`."""
    if not isinstance(raw_name, str) or not raw_name.strip():
        raise ValueError("'filename' is required for file upload.")
    name = raw_name.strip()
    if "\x00" in name or "/" in name or "\\" in name or ".." in name or ":" in name:
        raise PermissionError("Invalid upload filename: path separators and traversal sequences are forbidden.")
    if name.startswith("."):
        raise PermissionError("Invalid upload filename: hidden dotfiles are forbidden.")
    if Path(name).name != name:
        raise PermissionError("Invalid upload filename: must be a single filename segment.")
    stem = Path(name).stem
    ext = Path(name).suffix.lower()
    if ext not in _ALLOWED_UPLOAD_EXTS:
        raise ValueError(
            f"Unsupported upload extension {ext!r}; allowed: {', '.join(sorted(_ALLOWED_UPLOAD_EXTS))}."
        )
    safe_stem = slugify(stem, default="upload", max_len=64)
    return f"{safe_stem}{ext}", ext


class SyntopikStudioServer(ThreadingHTTPServer):
    """Multi-threaded local Studio HTTP server with CSRF token, SSE notifications & LAN pairing."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        server_address: tuple[str, int],
        *,
        workspace: Path,
        package_root: Path,
        lan: bool = False,
        csrf_token: str | None = None,
    ) -> None:
        self.workspace = workspace.resolve()
        self.package_root = package_root.resolve()
        self.kg_dir = resolve_kg_dir(self.workspace)
        self.chapters_dir = resolve_chapters_dir(self.workspace)
        self.exams_dir = resolve_exams_dir(self.workspace)
        self.sources_dir = resolve_sources_dir(self.workspace)
        self.demos_dir = resolve_demos_dir(self.workspace)
        self.lan = bool(lan)
        self.csrf_token = csrf_token or secrets.token_urlsafe(24)

        self._auth_lock = threading.Lock()
        self._pair_token: str | None = secrets.token_urlsafe(18) if self.lan else None
        self._pair_consumed = False
        self._pocket_sessions: set[str] = set()
        self._mcp_sessions: set[str] = set()

        self._change_cond = threading.Condition()
        self._change_seq = 0
        self._last_change_reason = "init"
        self._event_history: list[tuple[int, str, str, dict[str, Any] | None]] = []
        self._shutdown_event = threading.Event()
        self._state_lock = threading.Lock()
        self._last_synced_sig = ""

        super().__init__(server_address, SyntopikRequestHandler)

        self.bound_host = str(self.server_address[0])
        self.bound_port = int(self.server_address[1])
        if self.address_family == socket.AF_INET6:
            self.local_url = f"http://[::1]:{self.bound_port}"
        else:
            self.local_url = f"http://127.0.0.1:{self.bound_port}"
        self.mcp_http_endpoint = f"{self.local_url}/mcp"
        self.lan_ip = _detect_lan_ip() if self.lan else None
        self.lan_url = (
            f"http://{self.lan_ip}:{self.bound_port}/pocket?pair={self._pair_token}"
            if self.lan and self._pair_token
            else None
        )

    @property
    def pair_token(self) -> str | None:
        return self._pair_token

    def ensure_active_pair_url(self) -> str | None:
        """Return an active one-time LAN pairing URL, rotating a fresh token if already consumed."""
        if not self.lan:
            return None
        with self._auth_lock:
            if self._pair_consumed or not self._pair_token:
                self._pair_token = secrets.token_urlsafe(18)
                self._pair_consumed = False
            if not self.lan_ip:
                self.lan_ip = _detect_lan_ip()
            self.lan_url = f"http://{self.lan_ip}:{self.bound_port}/pocket?pair={self._pair_token}"
            return self.lan_url

    def consume_pair_token(self, provided: str | None) -> str | None:
        """Atomically verify and consume the one-time LAN pairing token, returning a session id."""
        if not self.lan or not provided:
            return None
        with self._auth_lock:
            if self._pair_consumed or not self._pair_token:
                return None
            if not hmac.compare_digest(provided.strip(), self._pair_token):
                return None
            self._pair_consumed = True
            self._pair_token = None
            self.lan_url = None
            session_id = secrets.token_urlsafe(24)
            self._pocket_sessions.add(session_id)
            return session_id

    def is_valid_pocket_session(self, cookie_val: str | None) -> bool:
        if not self.lan or not cookie_val:
            return False
        with self._auth_lock:
            for valid in self._pocket_sessions:
                if hmac.compare_digest(cookie_val, valid):
                    return True
        return False

    def create_mcp_session(self) -> str:
        sid = secrets.token_urlsafe(18)
        with self._auth_lock:
            if len(self._mcp_sessions) >= 64:
                self._mcp_sessions.pop()
            self._mcp_sessions.add(sid)
        return sid

    def delete_mcp_session(self, sid: str | None) -> bool:
        if not sid:
            return False
        with self._auth_lock:
            if sid in self._mcp_sessions:
                self._mcp_sessions.discard(sid)
                return True
        return False

    def notify_change(
        self,
        reason: str = "mutation",
        *,
        event_type: str = "changed",
        payload: dict[str, Any] | None = None,
    ) -> None:
        with self._change_cond:
            self._change_seq += 1
            self._last_change_reason = reason
            self._event_history.append((self._change_seq, event_type, reason, payload))
            if len(self._event_history) > 64:
                del self._event_history[:-64]
            self._change_cond.notify_all()

    def ensure_synced_graph(self, *, force: bool = False) -> tuple[dict[str, Any], dict[str, Any]]:
        """Sync `graph-data.js` when workspace files changed and return `(graph, hub)`."""
        with self._state_lock:
            sig = _compute_workspace_signature(self.workspace, self.kg_dir, self.chapters_dir)
            graph_js = self.kg_dir / "graph-data.js"
            if force or not graph_js.is_file() or sig != self._last_synced_sig:
                sync_hub(
                    workspace=self.workspace,
                    kg_dir=self.kg_dir,
                    chapters_dir=self.chapters_dir,
                    quiet=True,
                )
                self._last_synced_sig = sig
            parsed = _parse_graph_data_js(graph_js)
            if parsed is not None:
                return parsed
            return _build_python_fallback_state(self.workspace, self.kg_dir, self.chapters_dir)

    def shutdown(self) -> None:
        self._shutdown_event.set()
        with self._change_cond:
            self._change_cond.notify_all()
        super().shutdown()

    def handle_error(self, request: Any, client_address: Any) -> None:
        _, exc, _ = sys.exc_info()
        if isinstance(exc, (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)):
            return
        super().handle_error(request, client_address)


class SyntopikRequestHandler(BaseHTTPRequestHandler):
    """HTTP/1.1 handler for static Studio assets, REST API, SSE stream, and LAN Pocket."""

    protocol_version = "HTTP/1.1"
    timeout = 15.0  # Slowloris hardening on standard requests (cleared inside SSE handler)
    server: SyntopikStudioServer

    def log_message(self, format: str, *args: Any) -> None:
        if os.environ.get("SYNT_SERVER_LOG", "").strip() in ("1", "true", "yes"):
            super().log_message(format, *args)

    # ------------------------------------------------------------------
    # Response helpers
    # ------------------------------------------------------------------

    def _send_json(
        self,
        payload: Any,
        status: int = HTTPStatus.OK,
        *,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        raw = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.send_response(int(status))
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if extra_headers:
            for k, v in extra_headers.items():
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(raw)

    def _send_bytes(
        self,
        data: bytes,
        content_type: str,
        status: int = HTTPStatus.OK,
        *,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self.send_response(int(status))
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if extra_headers:
            for k, v in extra_headers.items():
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _send_error_json(self, status: int, message: str) -> None:
        self._send_json({"ok": False, "error": message}, status=status)

    def _read_json_body(self, *, max_bytes: int = MAX_JSON_BODY_BYTES) -> dict[str, Any] | None:
        try:
            length = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            self._send_error_json(HTTPStatus.BAD_REQUEST, "Invalid Content-Length header")
            return None
        if length < 0 or length > max_bytes:
            self.close_connection = True
            self._send_error_json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "Request body exceeds limit")
            return None
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        try:
            parsed = json.loads(raw.decode("utf-8"), parse_constant=_reject_non_finite)
        except Exception as exc:
            self._send_error_json(HTTPStatus.BAD_REQUEST, f"Malformed JSON body: {exc}")
            return None
        if not isinstance(parsed, dict):
            self._send_error_json(HTTPStatus.BAD_REQUEST, "Expected JSON object body")
            return None
        return parsed

    def _get_cookie(self, name: str) -> str | None:
        raw = self.headers.get("Cookie")
        if not raw:
            return None
        jar = SimpleCookie()
        try:
            jar.load(raw)
        except Exception:
            return None
        morsel = jar.get(name)
        return morsel.value if morsel is not None else None

    # ------------------------------------------------------------------
    # Security gate (loopback, Host/Origin, CSRF, LAN scoped Pocket)
    # ------------------------------------------------------------------

    def _check_access(self, method: str, path: str, query: dict[str, list[str]]) -> tuple[bool, bool]:
        """Validate request security. Returns `(allowed, is_lan_pocket_client)`."""
        client_ip = self.client_address[0] if self.client_address else ""
        is_loopback_client = _is_loopback_ip(client_ip)
        host_hdr = self.headers.get("Host")
        origin_hdr = self.headers.get("Origin")

        # Require valid non-empty RFC 3986 Host header on all HTTP/1.1 requests
        if _extract_host_only(host_hdr) is None:
            self._send_error_json(HTTPStatus.FORBIDDEN, "Missing or malformed Host header")
            return False, False

        # 1. Default mode (`lan=False`): strictly loopback client + loopback Host/Origin
        if not self.server.lan:
            if not is_loopback_client:
                self._send_error_json(HTTPStatus.FORBIDDEN, "Loopback access only")
                return False, False
            if not _is_loopback_host(host_hdr):
                self._send_error_json(HTTPStatus.FORBIDDEN, "Forbidden Host header")
                return False, False
            if not _is_loopback_origin(origin_hdr):
                self._send_error_json(HTTPStatus.FORBIDDEN, "Forbidden Origin header")
                return False, False
            if method in ("POST", "PATCH"):
                tok = self.headers.get("X-Syntopik-Token") or ""
                if not hmac.compare_digest(tok, self.server.csrf_token):
                    self._send_error_json(HTTPStatus.FORBIDDEN, "Missing or invalid X-Syntopik-Token")
                    return False, False
            return True, False

        # 2. Opt-in `--lan` mode:
        #    Non-pocket routes remain STRICTLY loopback-only (even with a valid Pocket cookie!).
        if path not in POCKET_LAN_PATHS:
            if not is_loopback_client or not _is_loopback_host(host_hdr) or not _is_loopback_origin(origin_hdr):
                self._send_error_json(HTTPStatus.FORBIDDEN, "Route is loopback-only")
                return False, False
            if method in ("POST", "PATCH"):
                tok = self.headers.get("X-Syntopik-Token") or ""
                if not hmac.compare_digest(tok, self.server.csrf_token):
                    self._send_error_json(HTTPStatus.FORBIDDEN, "Missing or invalid X-Syntopik-Token")
                    return False, False
            return True, False

        # 3. `--lan` mode on the 3 Pocket routes: `/pocket`, `/api/pocket/state`, `POST /api/review/batch`
        cookie_val = self._get_cookie(POCKET_COOKIE_NAME)
        has_valid_cookie = self.server.is_valid_pocket_session(cookie_val)

        # Handle one-time pairing on `GET /pocket?pair=<token>` (from either LAN or loopback)
        if method == "GET" and path == "/pocket" and "pair" in query:
            pair_vals = query.get("pair") or []
            pair_tok = pair_vals[0] if pair_vals else ""
            sid = self.server.consume_pair_token(pair_tok)
            if not sid:
                self._send_error_json(HTTPStatus.FORBIDDEN, "Invalid or expired pairing token")
                return False, True
            self.send_response(HTTPStatus.FOUND)
            self.send_header("Location", "/pocket")
            self.send_header(
                "Set-Cookie",
                f"{POCKET_COOKIE_NAME}={sid}; HttpOnly; SameSite=Lax; Path=/",
            )
            self.send_header("Content-Length", "0")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return False, True  # Response already sent (302)

        if is_loopback_client and _is_loopback_host(host_hdr) and _is_loopback_origin(origin_hdr):
            if method in ("POST", "PATCH"):
                tok = self.headers.get("X-Syntopik-Token") or ""
                if hmac.compare_digest(tok, self.server.csrf_token):
                    return True, False
                if path == "/api/review/batch" and has_valid_cookie:
                    if origin_hdr is not None and not _origin_matches_host(origin_hdr, host_hdr):
                        self._send_error_json(HTTPStatus.FORBIDDEN, "Cross-origin Pocket request blocked")
                        return False, True
                    return True, True
                self._send_error_json(HTTPStatus.FORBIDDEN, "Missing or invalid X-Syntopik-Token")
                return False, False
            return True, False

        # Non-loopback client (or non-loopback Host) on the 3 Pocket LAN routes
        if not has_valid_cookie:
            self._send_error_json(HTTPStatus.FORBIDDEN, "Pocket LAN pairing required")
            return False, True
        if method in ("POST", "PATCH") and origin_hdr is not None:
            if not _origin_matches_host(origin_hdr, host_hdr):
                self._send_error_json(HTTPStatus.FORBIDDEN, "Cross-origin Pocket request blocked")
                return False, True
        return True, True

    # ------------------------------------------------------------------
    # Path jail helpers
    # ------------------------------------------------------------------

    def _safe_rel_path(self, raw_subpath: str) -> str | None:
        """Unquote and validate relative path segments, rejecting traversal, NUL, and dotfiles."""
        unquoted = urllib.parse.unquote(raw_subpath)
        if "\x00" in unquoted or "\\" in unquoted:
            return None
        if unquoted.startswith("/"):
            return None
        parts = [p for p in unquoted.split("/") if p not in ("", ".")]
        for seg in parts:
            if seg == ".." or seg.startswith("."):
                return None
        return "/".join(parts)

    def _resolve_jailed(self, rel_path: str, roots: list[Path]) -> Path | None:
        """Resolve `rel_path` strictly inside the first matching root directory."""
        for root in roots:
            try:
                root_res = root.resolve()
                cand = (root_res / rel_path).resolve()
            except OSError:
                continue
            if not cand.is_relative_to(root_res):
                continue
            if cand.is_file():
                return cand
        return None

    def _resolve_user_path_jailed(self, raw_path: str, *, allow_temp: bool = False) -> Path | None:
        """Resolve a JSON-supplied filesystem path strictly within workspace (and optionally tempdir)."""
        if not raw_path or "\x00" in raw_path:
            return None
        p = Path(raw_path).expanduser()
        if not p.is_absolute():
            p = self.server.workspace / p
        try:
            resolved = p.resolve()
        except OSError:
            return None
        if resolved.is_relative_to(self.server.workspace):
            return resolved
        if allow_temp:
            tmp_root = Path(tempfile.gettempdir()).resolve()
            if resolved.is_relative_to(tmp_root):
                return resolved
        return None

    # ------------------------------------------------------------------
    # HTTP method dispatchers
    # ------------------------------------------------------------------

    def do_OPTIONS(self) -> None:
        self._send_error_json(HTTPStatus.METHOD_NOT_ALLOWED, "CORS preflight disabled on local Studio")

    def _extract_mcp_token(self) -> str:
        tok = (self.headers.get("X-Syntopik-Token") or "").strip()
        if tok:
            return tok
        auth_hdr = (self.headers.get("Authorization") or "").strip()
        if auth_hdr:
            parts = auth_hdr.split(None, 1)
            if len(parts) == 2 and parts[0].lower() == "bearer":
                return parts[1].strip()
        return ""

    def _check_mcp_access(self, *, require_token: bool = True) -> bool:
        """Strict loopback + non-LAN + DNS-rebinding + timing-safe token gate for `/mcp`."""
        if self.server.lan:
            self._send_error_json(HTTPStatus.FORBIDDEN, "Loopback /mcp endpoint is disabled in --lan mode")
            return False
        client_ip = self.client_address[0] if self.client_address else ""
        host_hdr = self.headers.get("Host")
        origin_hdr = self.headers.get("Origin")
        if not _is_loopback_ip(client_ip):
            self._send_error_json(HTTPStatus.FORBIDDEN, "Loopback access only")
            return False
        if _extract_host_only(host_hdr) is None or not _is_loopback_host(host_hdr):
            self._send_error_json(HTTPStatus.FORBIDDEN, "Forbidden Host header")
            return False
        if origin_hdr is not None and not _is_loopback_origin(origin_hdr):
            self._send_error_json(HTTPStatus.FORBIDDEN, "Forbidden Origin header")
            return False
        if require_token:
            tok = self._extract_mcp_token()
            if not tok or not hmac.compare_digest(tok, self.server.csrf_token):
                self._send_error_json(
                    HTTPStatus.FORBIDDEN,
                    "Missing or invalid X-Syntopik-Token / Authorization Bearer token",
                )
                return False
        return True

    def _handle_mcp_get(self) -> None:
        if not self._check_mcp_access(require_token=False):
            return
        self._send_json(
            {"ok": False, "error": "Method Not Allowed: use POST /mcp for JSON-RPC 2.0 requests"},
            status=HTTPStatus.METHOD_NOT_ALLOWED,
            extra_headers={"Allow": "POST"},
        )

    def _handle_mcp_delete(self) -> None:
        if not self._check_mcp_access(require_token=True):
            return
        sid = (self.headers.get("Mcp-Session-Id") or "").strip()
        removed = self.server.delete_mcp_session(sid) if sid else False
        self._send_json({"ok": True, "sessionClosed": removed})

    def _handle_mcp_post(self) -> None:
        if not self._check_mcp_access(require_token=True):
            return

        ctype_hdr = (self.headers.get("Content-Type") or "").strip()
        if ctype_hdr and "json" not in ctype_hdr.split(";", 1)[0].strip().lower():
            self._send_error_json(
                HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                "Content-Type must be application/json",
            )
            return

        try:
            length = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            self._send_error_json(HTTPStatus.BAD_REQUEST, "Invalid Content-Length header")
            return
        if length < 0 or length > MAX_MCP_HTTP_BODY_BYTES:
            self.close_connection = True
            self._send_error_json(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                f"MCP request body exceeds {MAX_MCP_HTTP_BODY_BYTES} byte limit",
            )
            return

        raw = self.rfile.read(length) if length > 0 else b""
        try:
            parsed = json.loads(raw.decode("utf-8"), parse_constant=_reject_non_finite)
        except Exception as exc:
            self._send_json(
                {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32700, "message": f"Parse error: {exc}"},
                },
                status=HTTPStatus.BAD_REQUEST,
            )
            return

        from syntopik.mcp_http import MCP_PROTOCOL_VERSION, handle_mcp_http_request

        def _on_change(reason: str) -> None:
            self.server.ensure_synced_graph(force=True)
            self.server.notify_change(reason)

        def _on_wb_frame(safe_frame: dict[str, Any]) -> None:
            self.server.ensure_synced_graph(force=True)
            self.server.notify_change(
                "mcp:whiteboard_step",
                event_type="whiteboard_frame",
                payload={"action": "push", "frame": safe_frame},
            )

        http_status, resp_body = handle_mcp_http_request(
            self.server.workspace,
            parsed,
            version=__version__,
            on_change=_on_change,
            on_whiteboard_frame=_on_wb_frame,
        )

        extra_headers: dict[str, str] = {"MCP-Protocol-Version": MCP_PROTOCOL_VERSION}
        if isinstance(resp_body, dict) and isinstance(resp_body.get("result"), dict):
            pv = resp_body["result"].get("protocolVersion")
            if isinstance(pv, str) and pv.strip():
                extra_headers["MCP-Protocol-Version"] = pv.strip()
        if isinstance(parsed, dict) and str(parsed.get("method") or "").strip() == "initialize" and isinstance(resp_body, dict) and "result" in resp_body:
            extra_headers["Mcp-Session-Id"] = self.server.create_mcp_session()

        if resp_body is None:
            self._send_bytes(b"", "application/json; charset=utf-8", status=HTTPStatus.ACCEPTED, extra_headers=extra_headers)
            return

        self._send_json(resp_body, status=http_status, extra_headers=extra_headers)

    def do_DELETE(self) -> None:
        parsed = urllib.parse.urlsplit(self.path)
        path = parsed.path or "/"
        if path == "/mcp":
            self._handle_mcp_delete()
            return
        self._send_error_json(HTTPStatus.METHOD_NOT_ALLOWED, f"DELETE not supported for {path}")

    def do_GET(self) -> None:
        parsed = urllib.parse.urlsplit(self.path)
        path = parsed.path or "/"
        query = urllib.parse.parse_qs(parsed.query)

        if path == "/mcp":
            self._handle_mcp_get()
            return

        allowed, _is_lan_client = self._check_access("GET", path, query)
        if not allowed:
            return

        if path == "/api/capabilities":
            self._handle_capabilities()
            return
        if path == "/api/state":
            self._handle_state(query)
            return
        if path == "/api/pocket/state":
            self._handle_pocket_state(query)
            return
        if path == "/api/events":
            self._handle_sse()
            return
        if path == "/pocket":
            self._handle_pocket_html(query)
            return

        get_api_routes: dict[str, Any] = {
            "/api/profile": lambda: self._handle_profile_get(query),
            "/api/next-move": lambda: self._handle_next_move(query),
            "/api/root-cause": lambda: self._handle_root_cause(query),
            "/api/evidence": lambda: self._handle_evidence(),
            "/api/arenas": lambda: self._handle_arenas_get(query),
            "/api/derivations": lambda: self._handle_derivations_get(query),
            "/api/viva": lambda: self._handle_viva_get(query),
            "/api/whiteboard": lambda: self._handle_whiteboard_get(),
            "/api/rosetta": lambda: self._handle_rosetta_get(query),
            "/api/sync/status": lambda: self._handle_sync_status(),
            "/api/readiness": lambda: self._handle_readiness_get(query),
            "/api/interleave": lambda: self._handle_interleave_get(),
            "/api/report": lambda: self._handle_report_get(query),
        }
        get_handler = get_api_routes.get(path)
        if get_handler is not None:
            try:
                get_handler()
            except ValueError as exc:
                self._send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
            except (FileNotFoundError, KeyError) as exc:
                self._send_error_json(HTTPStatus.NOT_FOUND, str(exc))
            except PermissionError as exc:
                self._send_error_json(HTTPStatus.FORBIDDEN, str(exc))
            except Exception as exc:
                self._send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, f"{type(exc).__name__}: {exc}")
            return

        self._handle_static(path)

    def do_POST(self) -> None:
        parsed = urllib.parse.urlsplit(self.path)
        path = parsed.path or "/"
        query = urllib.parse.parse_qs(parsed.query)

        if path == "/mcp":
            self._handle_mcp_post()
            return

        allowed, _is_lan = self._check_access("POST", path, query)
        if not allowed:
            return

        routes = {
            "/api/review": self._handle_review,
            "/api/review/batch": self._handle_review_batch,
            "/api/recover": self._handle_recover,
            "/api/exam/submit": self._handle_exam_submit,
            "/api/tracker/topic": self._handle_tracker_topic,
            "/api/tracker/milestone": self._handle_tracker_milestone,
            "/api/import": self._handle_import,
            "/api/export/obsidian": self._handle_export_obsidian,
            "/api/export/pocket": self._handle_export_pocket,
            "/api/export/dossier": self._handle_export_dossier,
            "/api/pack/export": self._handle_pack_export,
            "/api/pack/import": self._handle_pack_import,
            "/api/demo/load": self._handle_demo_load,
            "/api/triage/plan": self._handle_triage_plan,
            "/api/triage/crib-sheet": self._handle_triage_crib_sheet,
            "/api/forks/check": self._handle_forks_check,
            "/api/arena/submit": self._handle_arena_submit,
            "/api/arena/repair": self._handle_arena_repair,
            "/api/blitz/build": self._handle_blitz_build,
            "/api/blitz/grade": self._handle_blitz_grade,
            "/api/interleave/build": self._handle_interleave_build,
            "/api/interleave/check": self._handle_interleave_check,
            "/api/interleave/complete": self._handle_interleave_complete,
            "/api/profile/calibrate": self._handle_profile_calibrate,
            "/api/profile/override": self._handle_profile_override,
            "/api/profile/replay": self._handle_profile_replay,
            "/api/derivation/check": self._handle_derivation_check,
            "/api/derivation/cut": self._handle_derivation_cut,
            "/api/derivation/diff": self._handle_derivation_diff,
            "/api/rosetta/link": self._handle_rosetta_link,
            "/api/sync/export": self._handle_sync_export,
            "/api/sync/merge": self._handle_sync_merge,
            "/api/viva/start": self._handle_viva_start,
            "/api/viva/turn": self._handle_viva_turn,
            "/api/whiteboard/push": self._handle_whiteboard_push,
            "/api/whiteboard/unlock": self._handle_whiteboard_unlock,
            "/api/whiteboard/pin": self._handle_whiteboard_pin,
            "/api/whiteboard/clear": self._handle_whiteboard_clear,
        }
        handler = routes.get(path)
        if handler is None:
            self._send_error_json(HTTPStatus.NOT_FOUND, f"Unknown API endpoint: {path}")
            return
        try:
            handler()
        except ValueError as exc:
            self._send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
        except (FileNotFoundError, KeyError) as exc:
            self._send_error_json(HTTPStatus.NOT_FOUND, str(exc))
        except PermissionError as exc:
            self._send_error_json(HTTPStatus.FORBIDDEN, str(exc))
        except Exception as exc:
            self._send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, f"{type(exc).__name__}: {exc}")

    def do_PATCH(self) -> None:
        path = urllib.parse.urlsplit(self.path).path or "/"
        if path == "/mcp":
            self._handle_mcp_get()
            return
        self.do_POST()

    # ------------------------------------------------------------------
    # Static file serving
    # ------------------------------------------------------------------

    def _serve_file(self, file_path: Path, *, inject_token_meta: bool = False) -> None:
        try:
            data = file_path.read_bytes()
        except OSError:
            self._send_error_json(HTTPStatus.NOT_FOUND, "File unreadable")
            return
        ctype, _ = mimetypes.guess_type(str(file_path))
        if not ctype:
            ctype = "application/octet-stream"
        elif ctype.startswith("text/") or ctype in ("application/javascript", "application/json", "image/svg+xml"):
            if "charset=" not in ctype:
                ctype += "; charset=utf-8"

        if inject_token_meta and file_path.suffix.lower() in (".html", ".htm"):
            try:
                html_txt = data.decode("utf-8")
                meta_tag = f'<meta name="syntopik-token" content="{self.server.csrf_token}">'
                if 'name="syntopik-token"' not in html_txt and "</head>" in html_txt:
                    html_txt = html_txt.replace("</head>", f"  {meta_tag}\n</head>", 1)
                    data = html_txt.encode("utf-8")
            except UnicodeDecodeError:
                pass
        self._send_bytes(data, ctype)

    def _handle_static(self, path: str) -> None:
        ws = self.server.workspace
        pkg = self.server.package_root

        # Explicit traversal check on raw path before mapping
        unquoted = urllib.parse.unquote(path)
        if "\x00" in unquoted or "\\" in unquoted or "/../" in unquoted or unquoted.endswith("/.."):
            self._send_error_json(HTTPStatus.FORBIDDEN, "Path traversal blocked")
            return

        if path == "/favicon.ico":
            self.send_response(HTTPStatus.NO_CONTENT)
            self.send_header("Cache-Control", "public, max-age=86400")
            self.end_headers()
            return

        if path in ("/", "/index.html", "/dashboard.html"):
            target = self._resolve_jailed("dashboard.html", [ws, pkg])
            if target is None:
                self._send_error_json(HTTPStatus.NOT_FOUND, "dashboard.html not found")
                return
            self._serve_file(target, inject_token_meta=True)
            return

        if path == "/knowledge-graph/viewer.html":
            target = self._resolve_jailed("knowledge-graph/viewer.html", [ws, pkg])
            if target is None:
                target = self._resolve_jailed("viewer.html", [pkg])
            if target is None:
                self._send_error_json(HTTPStatus.NOT_FOUND, "viewer.html not found")
                return
            self._serve_file(target, inject_token_meta=True)
            return

        if path == "/knowledge-graph/graph-data.js":
            self.server.ensure_synced_graph()
            gjs = self.server.kg_dir / "graph-data.js"
            if not gjs.is_file():
                self._send_error_json(HTTPStatus.NOT_FOUND, "graph-data.js not found")
                return
            self._serve_file(gjs)
            return

        if path == "/knowledge-graph/reagents.json":
            target = self._resolve_jailed("knowledge-graph/reagents.json", [ws, pkg])
            if target is None:
                target = self._resolve_jailed("reagents.json", [pkg])
            if target is None:
                self._send_error_json(HTTPStatus.NOT_FOUND, "reagents.json not found")
                return
            self._serve_file(target)
            return

        if path == "/demos/exam-cbt-template.html":
            target = self._resolve_jailed("demos/exam-cbt-template.html", [ws, pkg])
            if target is None:
                target = self._resolve_jailed("exam-cbt-template.html", [pkg])
            if target is None:
                self._send_error_json(HTTPStatus.NOT_FOUND, "exam-cbt-template.html not found")
                return
            self._serve_file(target, inject_token_meta=True)
            return

        prefix_map: list[tuple[str, list[Path], bool]] = [
            ("/demos/", [ws / "demos", pkg / "demos"], True),
            ("/exams/", [ws / "exams"], True),
            ("/chapters/", [ws / "chapters"], False),
            ("/sources/", [ws / "sources"], False),
            ("/animations/", [ws / "animations"], False),
            ("/docs/", [ws / "docs", pkg / "docs"], False),
        ]
        for prefix, roots, inject_meta in prefix_map:
            if path.startswith(prefix):
                rel = self._safe_rel_path(path[len(prefix):])
                if not rel:
                    self._send_error_json(HTTPStatus.FORBIDDEN, "Invalid path")
                    return
                target = self._resolve_jailed(rel, roots)
                if target is None:
                    self._send_error_json(HTTPStatus.NOT_FOUND, f"Not found: {path}")
                    return
                self._serve_file(target, inject_token_meta=inject_meta)
                return

        self._send_error_json(HTTPStatus.NOT_FOUND, f"Not found: {path}")

    # ------------------------------------------------------------------
    # GET API handlers
    # ------------------------------------------------------------------

    def _handle_capabilities(self) -> None:
        mcp_tools: list[str] = []
        try:
            from syntopik.mcp_http import list_http_mcp_tools

            mcp_tools = [str(t["name"]) for t in list_http_mcp_tools() if isinstance(t, dict) and t.get("name")]
        except Exception:
            mcp_tools = []
        agents_compat = _build_agents_compat_payload(self.server.workspace)
        self._send_json({
            "live": True,
            "token": self.server.csrf_token,
            "version": __version__,
            "workspaceName": self.server.workspace.name,
            "lan": self.server.lan,
            "mcpHttpEndpoint": self.server.mcp_http_endpoint,
            "mcpHttpEnabled": not self.server.lan,
            "mcpHttpTools": mcp_tools,
            "agentsCompat": agents_compat,
        })

    def _handle_pocket_html(self, query: dict[str, list[str]]) -> None:
        from syntopik.pocket import export_pocket_html

        raw_limit = (query.get("limit") or ["25"])[0]
        try:
            limit = max(1, min(200, int(raw_limit)))
        except ValueError:
            limit = 25
        chapter = (query.get("chapter") or [None])[0]

        with tempfile.TemporaryDirectory(prefix="synt-pocket-") as tmp:
            out_file = Path(tmp) / "pocket.html"
            export_pocket_html(
                workspace=self.server.workspace,
                out_path=out_file,
                limit=limit,
                chapter=chapter,
                kg_dir=self.server.kg_dir,
                api_base="",
            )
            html_txt = out_file.read_text(encoding="utf-8")

        self._send_bytes(html_txt.encode("utf-8"), "text/html; charset=utf-8")

    def _handle_pocket_state(self, query: dict[str, list[str]]) -> None:
        from syntopik.pocket import _select_pocket_cards

        raw_limit = (query.get("limit") or ["25"])[0]
        try:
            limit = max(1, min(200, int(raw_limit)))
        except ValueError:
            limit = 25
        chapter = (query.get("chapter") or [None])[0]
        props = load_jsonl(self.server.kg_dir / "propositions.jsonl")
        cards, due_count = _select_pocket_cards(
            props,
            limit=limit,
            chapter=chapter,
            include_upcoming=True,
            kg_dir=self.server.kg_dir,
        )
        self._send_json({
            "ok": True,
            "workspaceName": self.server.workspace.name,
            "cardCount": len(cards),
            "dueCount": due_count,
            "cards": cards,
        })

    def _handle_state(self, query: dict[str, list[str]]) -> None:
        force = (query.get("refresh") or ["0"])[0] in ("1", "true", "yes")
        energy = (query.get("energy") or ["normal"])[0].strip().lower()
        if energy not in ("low", "normal", "deep"):
            energy = "normal"
        resume = (query.get("resume") or ["0"])[0] in ("1", "true", "yes")

        graph, hub = self.server.ensure_synced_graph(force=force)

        # Enrich with v2.0 & v3.0 engines (strictly sanitized — zero sealed secret leaks!)
        forks_due_items: list[dict[str, Any]] = []
        forks_due_count = 0
        forks_all: list[dict[str, Any]] = []
        try:
            from syntopik.forks import list_due_forks

            f_due = list_due_forks(workspace=self.server.workspace, kg_dir=self.server.kg_dir, include_sealed=False)
            forks_due_items = [_sanitize_fork(x) for x in (f_due.get("items") or []) if isinstance(x, dict)]
            forks_due_count = int(f_due.get("dueCount") or len(forks_due_items))
            f_all = list_due_forks(
                workspace=self.server.workspace,
                kg_dir=self.server.kg_dir,
                limit=200,
                include_all=True,
                include_sealed=False,
            )
            forks_all = [_sanitize_fork(x) for x in (f_all.get("items") or []) if isinstance(x, dict)]
        except Exception:
            try:
                raw_forks = load_jsonl(self.server.kg_dir / "forks.jsonl")
                forks_all = [_sanitize_fork(x) for x in raw_forks if isinstance(x, dict)]
            except Exception:
                pass

        arenas_public: list[dict[str, Any]] = []
        try:
            arenas_rows = load_jsonl(self.server.kg_dir / "arenas.jsonl")
            arenas_public = [_sanitize_arena(a) for a in arenas_rows if isinstance(a, dict)]
        except Exception:
            arenas_public = []
        arenas_count = len(arenas_public)

        decisions_public: list[dict[str, Any]] = []
        try:
            dec_rows = load_jsonl(self.server.kg_dir / "decisions.jsonl")
            decisions_public = [_sanitize_decision(d) for d in dec_rows if isinstance(d, dict)]
        except Exception:
            decisions_public = []

        derivations_public: list[dict[str, Any]] = []
        try:
            deriv_rows = load_jsonl(self.server.kg_dir / "derivations.jsonl")
            derivations_public = [_sanitize_derivation_row(d, level=3) for d in deriv_rows if isinstance(d, dict)]
        except Exception:
            derivations_public = []

        viva_sessions_public: list[dict[str, Any]] = []
        try:
            viva_rows = load_jsonl(self.server.kg_dir / "viva-sessions.jsonl")
            viva_sessions_public = [_sanitize_viva_session(v) for v in viva_rows if isinstance(v, dict)]
        except Exception:
            viva_sessions_public = []

        learner_profile: dict[str, Any] = {}
        try:
            from syntopik.learner_model import compile_teaching_policy, load_profile

            learner_profile = load_profile(self.server.kg_dir)
            active_tr = hub.get("activeChapterTracker")
            active_dom = (
                str(active_tr.get("subject"))
                if isinstance(active_tr, dict) and active_tr.get("subject")
                else None
            )
            learner_profile["compiledPolicy"] = compile_teaching_policy(
                learner_profile,
                domain=active_dom,
                energy=energy,
            )
        except Exception:
            learner_profile = {}

        next_move: dict[str, Any] | None = None
        try:
            from syntopik.brainstem import plan_next_move

            next_move = plan_next_move(self.server.workspace, energy=energy)
        except Exception:
            next_move = None

        evidence_summary: dict[str, Any] = {
            "ok": True,
            "totalPropositions": 0,
            "tierCounts": {"T1": 0, "T2": 0, "T3": 0, "T4": 0},
            "verifiedTransferPct": 0.0,
            "propositions": {},
            "antiInflationAlerts": [],
            "ledgerIntegrity": {"ok": True, "count": 0, "brokenAt": None},
        }
        try:
            from syntopik.evidence import compute_evidence_summary

            evidence_summary = compute_evidence_summary(self.server.workspace)
        except Exception:
            pass

        root_cause_hotspots: dict[str, Any] = {"byProp": {}, "blastRadius": []}
        try:
            root_cause_hotspots = _compute_root_cause_hotspots(self.server.kg_dir)
        except Exception:
            pass

        whiteboard_public: dict[str, Any] = {
            "schemaVersion": 1,
            "sessionId": None,
            "revision": 0,
            "title": "",
            "activeFrameId": None,
            "frames": [],
            "pins": [],
            "pendingPins": [],
            "pendingPinCount": 0,
            "frameCount": 0,
            "updatedAt": None,
        }
        try:
            from syntopik.whiteboard import get_session

            whiteboard_public = _sanitize_whiteboard(get_session(self.server.kg_dir))
        except Exception:
            pass

        explorables_map: dict[str, list[dict[str, Any]]] = {}
        try:
            explorables_map = _compile_chapter_explorables(self.server.chapters_dir)
        except Exception:
            explorables_map = {}

        starter_packs = _scan_starter_packs(self.server.workspace, self.server.package_root)

        reentry: dict[str, Any] | None = None
        try:
            daily_plan = _load_script_module("daily_plan", self.server.workspace, self.server.package_root)
            plan = daily_plan.build_daily_plan(
                kg_dir=self.server.kg_dir,
                chapters_dir=self.server.chapters_dir,
                energy=energy,
                resume=resume,
            )
            reentry = plan.get("reentry")
        except Exception:
            reentry = None

        triage_recommended = _compute_triage_recommended(self.server.kg_dir, hub)
        rosetta_data = _build_rosetta_payload(self.server.kg_dir)
        sync_status = _build_sync_status_payload(self.server.workspace)
        readiness_summary = _build_readiness_summary(
            self.server.workspace,
            self.server.kg_dir,
            self.server.chapters_dir,
        )
        interleave_stats = _build_interleave_stats_payload(self.server.workspace, self.server.kg_dir)
        progress_report = _build_progress_report_payload(self.server.kg_dir)
        compat_summary = _build_compat_summary(
            self.server.workspace,
            self.server.mcp_http_endpoint,
            mcp_enabled=not self.server.lan,
        )

        # Mirror enriched v2, v3 & v3.2 fields inside hub as well as top-level for universal consumer compatibility
        hub_out = dict(hub)
        hub_out["schemaVersion"] = 5
        hub_out.setdefault("retentionHeatmap", {"windowDays": 365, "totalEvents": 0, "activeDays": 0, "byDate": {}, "days": []})
        hub_out.setdefault("calibration", {"sampleCount": 0, "totalReviews": 0, "brierScore": None})
        hub_out.setdefault("leeches", [])
        hub_out.setdefault("readingQueue", [])
        hub_out["forksDue"] = forks_due_items
        hub_out["forksDueCount"] = forks_due_count
        hub_out["arenasCount"] = arenas_count
        hub_out["reentry"] = reentry
        hub_out["triageRecommended"] = triage_recommended
        hub_out["learnerProfile"] = learner_profile
        hub_out["nextMove"] = next_move
        hub_out["forks"] = forks_all
        hub_out["arenas"] = arenas_public
        hub_out["decisions"] = decisions_public
        hub_out["derivations"] = derivations_public
        hub_out["vivaSessions"] = viva_sessions_public
        hub_out["evidenceSummary"] = evidence_summary
        hub_out["rootCauseHotspots"] = root_cause_hotspots
        hub_out["whiteboard"] = whiteboard_public
        hub_out["explorables"] = explorables_map
        hub_out["starterPacks"] = starter_packs
        hub_out["rosetta"] = rosetta_data
        hub_out["syncStatus"] = sync_status
        hub_out["readinessSummary"] = readiness_summary
        hub_out["interleaveStats"] = interleave_stats
        hub_out["compatSummary"] = compat_summary
        hub_out["progressReport"] = progress_report

        self._send_json({
            "ok": True,
            "schemaVersion": 5,
            "graph": graph,
            "hub": hub_out,
            "forksDue": forks_due_items,
            "forksDueCount": forks_due_count,
            "arenasCount": arenas_count,
            "reentry": reentry,
            "triageRecommended": triage_recommended,
            "energyMode": energy,
            "learnerProfile": learner_profile,
            "nextMove": next_move,
            "forks": forks_all,
            "arenas": arenas_public,
            "decisions": decisions_public,
            "derivations": derivations_public,
            "vivaSessions": viva_sessions_public,
            "evidenceSummary": evidence_summary,
            "rootCauseHotspots": root_cause_hotspots,
            "whiteboard": whiteboard_public,
            "explorables": explorables_map,
            "starterPacks": starter_packs,
            "rosetta": rosetta_data,
            "syncStatus": sync_status,
            "readinessSummary": readiness_summary,
            "interleaveStats": interleave_stats,
            "compatSummary": compat_summary,
            "progressReport": progress_report,
        })

    def _handle_sse(self) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        try:
            self.connection.settimeout(None)
        except OSError:
            pass

        def _write_sse(text: str) -> bool:
            try:
                self.wfile.write(text.encode("utf-8"))
                self.wfile.flush()
                return True
            except OSError:
                return False

        ready_payload = json.dumps({"type": "ready", "ts": now_iso()})
        if not _write_sse(f"data: {ready_payload}\n\n"):
            return

        last_sig = _compute_workspace_signature(
            self.server.workspace, self.server.kg_dir, self.server.chapters_dir
        )
        with self.server._change_cond:
            last_seq = self.server._change_seq
        last_ping = time.monotonic()

        while not self.server._shutdown_event.is_set():
            with self.server._change_cond:
                self.server._change_cond.wait(timeout=0.5)
                cur_seq = self.server._change_seq
                reason = self.server._last_change_reason
                queued = [e for e in self.server._event_history if e[0] > last_seq]

            if self.server._shutdown_event.is_set():
                break

            cur_sig = _compute_workspace_signature(
                self.server.workspace, self.server.kg_dir, self.server.chapters_dir
            )
            if queued:
                last_seq = cur_seq
                last_sig = cur_sig
                broken = False
                for _ev_seq, ev_type, ev_reason, ev_payload in queued:
                    body_obj: dict[str, Any] = {
                        "type": ev_type,
                        "reason": ev_reason,
                        "ts": now_iso(),
                    }
                    if isinstance(ev_payload, dict):
                        body_obj.update(ev_payload)
                    ev_json = json.dumps(body_obj, ensure_ascii=False)
                    if ev_type == "whiteboard_frame":
                        if not _write_sse(f"event: whiteboard_frame\ndata: {ev_json}\n\n"):
                            broken = True
                            break
                        if not _write_sse(f"data: {ev_json}\n\n"):
                            broken = True
                            break
                    else:
                        if not _write_sse(f"data: {ev_json}\n\n"):
                            broken = True
                            break
                if broken:
                    break
                last_ping = time.monotonic()
            elif cur_seq != last_seq or cur_sig != last_sig:
                last_seq = cur_seq
                last_sig = cur_sig
                ev = json.dumps({"type": "changed", "reason": reason if cur_seq else "fs", "ts": now_iso()})
                if not _write_sse(f"data: {ev}\n\n"):
                    break
                last_ping = time.monotonic()
            elif time.monotonic() - last_ping >= 15.0:
                if not _write_sse(": ping\n\n"):
                    break
                last_ping = time.monotonic()

    # ------------------------------------------------------------------
    # v3.0 GET API handlers (loopback-only)
    # ------------------------------------------------------------------

    def _handle_profile_get(self, query: dict[str, list[str]]) -> None:
        from syntopik.learner_model import (
            compile_teaching_policy,
            format_compact_policy_lines,
            load_profile,
        )

        domain = (query.get("domain") or [None])[0]
        energy = (query.get("energy") or ["normal"])[0].strip().lower()
        if energy not in ("low", "normal", "deep"):
            energy = "normal"
        profile = load_profile(self.server.kg_dir)
        policy = compile_teaching_policy(profile, domain=domain, energy=energy)
        profile["compiledPolicy"] = policy
        self._send_json({
            "ok": True,
            "profile": profile,
            "compiledPolicy": policy,
            "compactLines": format_compact_policy_lines(profile, domain=domain),
        })

    def _handle_next_move(self, query: dict[str, list[str]]) -> None:
        from syntopik.brainstem import plan_next_move

        energy = (query.get("energy") or [None])[0]
        domain = (query.get("domain") or [None])[0]
        event = (query.get("event") or [None])[0]
        res = plan_next_move(self.server.workspace, event=event, energy=energy, domain=domain)
        self._send_json(res)

    def _handle_root_cause(self, query: dict[str, list[str]]) -> None:
        from syntopik.root_cause import find_blast_radius, localize_fault, select_diagnostic_probe

        node_id = (query.get("nodeId") or query.get("propId") or [""])[0].strip()
        try:
            max_depth = max(1, min(8, int((query.get("maxDepth") or ["4"])[0])))
        except ValueError:
            max_depth = 4
        try:
            limit = max(1, min(25, int((query.get("limit") or ["10"])[0])))
        except ValueError:
            limit = 10

        if node_id:
            loc = localize_fault(self.server.kg_dir, node_id, max_depth=max_depth)
            probe = select_diagnostic_probe(self.server.kg_dir, node_id, max_depth=max_depth)
            blast = find_blast_radius(self.server.kg_dir, limit=limit)
            self._send_json({
                "ok": bool(loc.get("ok")),
                "nodeId": node_id,
                "localization": loc,
                "diagnosticProbe": probe,
                "blastRadius": blast,
            })
            return

        hotspots = _compute_root_cause_hotspots(self.server.kg_dir)
        self._send_json({
            "ok": True,
            "byProp": hotspots["byProp"],
            "blastRadius": hotspots["blastRadius"][:limit],
            "hotspots": hotspots,
        })

    def _handle_evidence(self) -> None:
        from syntopik.evidence import compute_evidence_summary

        res = compute_evidence_summary(self.server.workspace)
        self._send_json(res)

    def _handle_arenas_get(self, query: dict[str, list[str]]) -> None:
        dom_f = ((query.get("domain") or [""])[0]).strip().lower()
        prop_f = ((query.get("propId") or [""])[0]).strip()
        rows = load_jsonl(self.server.kg_dir / "arenas.jsonl")
        items: list[dict[str, Any]] = []
        for a in rows:
            if not isinstance(a, dict):
                continue
            if dom_f and str(a.get("domain") or "").lower() != dom_f:
                continue
            if prop_f and str(a.get("propId") or "") != prop_f:
                continue
            items.append(_sanitize_arena(a))
        self._send_json({"ok": True, "count": len(items), "arenas": items})

    def _handle_derivations_get(self, query: dict[str, list[str]]) -> None:
        deriv_id = ((query.get("id") or query.get("derivationId") or [""])[0]).strip()
        try:
            level = max(1, min(3, int((query.get("level") or query.get("blackoutLevel") or ["3"])[0])))
        except ValueError:
            level = 3

        df = _load_script_module("derivation_forge", self.server.workspace, self.server.package_root)
        if deriv_id:
            challenge = df.get_blackout_challenge(
                deriv_id,
                level=level,
                kg_dir=self.server.kg_dir,
                workspace=self.server.workspace,
            )
            self._send_json(challenge)
            return

        prop_f = ((query.get("propId") or [""])[0]).strip()
        chap_f = ((query.get("chapter") or [""])[0]).strip().lower()
        dom_f = ((query.get("domain") or [""])[0]).strip().lower()
        rows = load_jsonl(self.server.kg_dir / "derivations.jsonl")
        items: list[dict[str, Any]] = []
        for r in rows:
            if not isinstance(r, dict):
                continue
            if prop_f and str(r.get("propId") or "") != prop_f:
                continue
            if chap_f and chap_f not in str(r.get("chapter") or "").lower():
                continue
            if dom_f and str(r.get("domain") or "").lower() != dom_f:
                continue
            items.append(_sanitize_derivation_row(r, level=level))
        self._send_json({"ok": True, "count": len(items), "derivations": items})

    def _handle_viva_get(self, query: dict[str, list[str]]) -> None:
        sess_id = ((query.get("sessionId") or query.get("id") or [""])[0]).strip()
        if sess_id:
            vc = _load_script_module("viva_chamber", self.server.workspace, self.server.package_root)
            st = vc.get_viva_status(sess_id, kg_dir=self.server.kg_dir, workspace=self.server.workspace)
            sess_clean = _sanitize_viva_session(st["session"])
            self._send_json({
                "ok": True,
                "session": sess_clean,
                "currentPrompt": st.get("currentPrompt"),
            })
            return

        prop_f = ((query.get("propId") or [""])[0]).strip()
        status_f = ((query.get("status") or [""])[0]).strip().lower()
        rows = load_jsonl(self.server.kg_dir / "viva-sessions.jsonl")
        items: list[dict[str, Any]] = []
        for s in rows:
            if not isinstance(s, dict):
                continue
            if prop_f and str(s.get("propId") or "") != prop_f:
                continue
            if status_f and str(s.get("status") or "").lower() != status_f:
                continue
            items.append(_sanitize_viva_session(s))
        self._send_json({"ok": True, "count": len(items), "sessions": items})

    def _handle_whiteboard_get(self) -> None:
        from syntopik.whiteboard import get_session

        sess = _sanitize_whiteboard(get_session(self.server.kg_dir))
        self._send_json({"ok": True, **sess})

    # ------------------------------------------------------------------
    # POST API handlers
    # ------------------------------------------------------------------

    def _handle_review(self) -> None:
        body = self._read_json_body()
        if body is None:
            return
        prop_id = str(body.get("propId") or body.get("id") or "").strip()
        rating = str(body.get("rating") or "").strip().lower()
        if not prop_id or not rating:
            raise ValueError("Both 'propId' and 'rating' are required.")

        srs = _load_script_module("srs_engine", self.server.workspace, self.server.package_root)
        conf_raw = body.get("confidence")
        confidence = float(conf_raw) if isinstance(conf_raw, (int, float)) and not isinstance(conf_raw, bool) else None
        dur_raw = body.get("durationMs")
        duration_ms = int(dur_raw) if isinstance(dur_raw, (int, float)) and not isinstance(dur_raw, bool) else None

        res = srs.record_review(
            self.server.kg_dir,
            prop_id,
            rating,
            cue_id=body.get("cueId"),
            title=body.get("title"),
            subject=body.get("subject"),
            chapter=body.get("chapter"),
            notes=body.get("notes"),
            misconception=bool(body.get("misconception", False)),
            confidence=confidence,
            duration_ms=duration_ms,
            no_fire=bool(body.get("noFire", False)),
            no_sync=True,
        )
        self.server.ensure_synced_graph(force=True)
        self.server.notify_change("review")

        props = load_jsonl(self.server.kg_dir / "propositions.jsonl")
        updated_node = next((p for p in props if p.get("id") == prop_id), res)
        fire_credits = list(res.get("fireCredits") or [])
        fire_count = len(fire_credits)
        fire_summary = f"FIRe refreshed {fire_count} prerequisite(s)" if fire_count else ""

        self._send_json({
            "ok": True,
            "node": updated_node,
            "result": res,
            "fireCredits": fire_credits,
            "fireRefreshedCount": fire_count,
            "fireSummary": fire_summary,
        })

    def _handle_review_batch(self) -> None:
        body = self._read_json_body()
        if body is None:
            return
        if not isinstance(body.get("reviews"), list):
            raise ValueError("Payload must include 'reviews' array.")
        from syntopik.pocket import import_pocket_sync

        res = import_pocket_sync(
            workspace=self.server.workspace,
            sync_path=body,
            kg_dir=self.server.kg_dir,
            no_fire=bool(body.get("noFire", False)),
            no_sync=True,
            dry_run=bool(body.get("dryRun", False)),
        )
        if not body.get("dryRun"):
            self.server.ensure_synced_graph(force=True)
            self.server.notify_change("review_batch")
        self._send_json({"ok": True, **res})

    def _handle_recover(self) -> None:
        body = self._read_json_body()
        if body is None:
            return
        daily_cap = max(1, int(body.get("dailyCap") or 15))
        days = max(1, int(body.get("days") or 6))

        srs = _load_script_module("srs_engine", self.server.workspace, self.server.package_root)
        props_file = self.server.kg_dir / "propositions.jsonl"
        chains_file = self.server.kg_dir / "chains.jsonl"
        queue_file = self.server.kg_dir / "review-queue.md"

        with workspace_lock(props_file):
            props = load_jsonl(props_file)
            chains = load_jsonl(chains_file)
            params = srs.load_fsrs_parameters(self.server.kg_dir)
            plan = srs.recover_backlog(
                props,
                chains,
                daily_cap=daily_cap,
                days=days,
                parameters=params,
            )
            resched = plan.get("rescheduleMap") or {}
            if resched:
                for p in props:
                    pid = p.get("id")
                    if pid in resched:
                        new_date = resched[pid]
                        p["nextReview"] = new_date
                        if isinstance(p.get("fsrs"), dict):
                            p["fsrs"]["due"] = f"{new_date}T12:00:00+00:00"
                save_jsonl_atomic(props_file, props)
                srs.regenerate_review_queue(props, queue_file, kg_dir=self.server.kg_dir, chains=chains)

        self.server.ensure_synced_graph(force=True)
        self.server.notify_change("recover")
        self._send_json({
            "ok": True,
            "applied": True,
            "dailyCap": daily_cap,
            "rescheduledCount": len(resched),
            **plan,
        })

    def _handle_exam_submit(self) -> None:
        body = self._read_json_body()
        if body is None:
            return
        attempt = body.get("attempt")
        if not isinstance(attempt, dict):
            raise ValueError("'attempt' JSON object is required.")

        exam_runner = _load_script_module("exam_runner", self.server.workspace, self.server.package_root)
        raw_spec = body.get("spec")
        spec_dir: Path | None = None
        if raw_spec is None:
            spec_path_raw = body.get("specPath")
            if spec_path_raw:
                sp = self._resolve_user_path_jailed(str(spec_path_raw), allow_temp=False)
                if sp is None or not sp.is_file():
                    raise FileNotFoundError(f"Exam spec not found within workspace: {spec_path_raw}")
                raw_spec = json.loads(sp.read_text(encoding="utf-8"))
                spec_dir = sp.parent
            else:
                slug_hint = slugify(str(body.get("examSlug") or attempt.get("examSlug") or "exam"))
                for cand in (
                    self.server.exams_dir / f"{slug_hint}.json",
                    self.server.exams_dir / f"{slug_hint}-spec.json",
                ):
                    if cand.is_file():
                        raw_spec = json.loads(cand.read_text(encoding="utf-8"))
                        spec_dir = cand.parent
                        break
        if not isinstance(raw_spec, dict):
            raise ValueError("Provide exam 'spec' object or valid workspace 'specPath'.")

        norm_spec = exam_runner.normalize_spec(raw_spec, spec_dir=spec_dir, inline_media=False)
        exam_slug = slugify(str(body.get("examSlug") or norm_spec.get("examSlug") or "exam"))

        ts_tag = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self.server.exams_dir.mkdir(parents=True, exist_ok=True)
        attempt_file = self.server.exams_dir / f"{exam_slug}-attempt-{ts_tag}.json"
        save_json_atomic(attempt_file, attempt)

        graded = exam_runner.grade_attempt(
            norm_spec,
            attempt,
            kg_dir=self.server.kg_dir,
            no_sync=True,
        )
        self.server.ensure_synced_graph(force=True)
        self.server.notify_change("exam_submit")

        raw_autopsy = graded.get("autopsy") or {}
        buckets = raw_autopsy.get("buckets") or {}
        ev_calibration = graded.get("evCalibration")
        distractor_summary = graded.get("distractorSummary") or {
            "triggeredCount": 0,
            "topTrapCategories": [],
            "topConfusedWith": [],
            "items": [],
        }
        distractor_autopsy = list(distractor_summary.get("items") or [])
        vignette_breakdown = graded.get("vignetteBreakdown") or {}
        autopsy_out = {
            "knowledge": buckets.get("knowledge", []),
            "execution": buckets.get("execution", []),
            "time_sink": buckets.get("time_sink", []),
            "solid": buckets.get("solid", []),
            "counts": raw_autopsy.get("counts", {}),
            "medianTimeSec": raw_autopsy.get("medianTimeSec"),
            "buckets": buckets,
            "egoTraps": raw_autopsy.get("egoTraps", []),
            "escalations": graded.get("threeStrikeEscalations", []),
            "evCalibration": ev_calibration,
            "distractorSummary": distractor_summary,
            "distractorAutopsy": distractor_autopsy,
            "vignetteBreakdown": vignette_breakdown,
        }
        rel_attempt = (
            str(attempt_file.relative_to(self.server.workspace))
            if attempt_file.is_relative_to(self.server.workspace)
            else str(attempt_file)
        )
        self._send_json({
            "ok": True,
            "examSlug": exam_slug,
            "attemptPath": rel_attempt,
            "scored": graded,
            "autopsy": autopsy_out,
            "evCalibration": ev_calibration,
            "distractorSummary": distractor_summary,
            "distractorAutopsy": distractor_autopsy,
            "vignetteBreakdown": vignette_breakdown,
        })

    def _handle_tracker_topic(self) -> None:
        body = self._read_json_body()
        if body is None:
            return
        slug = str(body.get("slug") or "").strip()
        topic_id = str(body.get("topicId") or "").strip()
        status = str(body.get("status") or "").strip()
        if not slug or not topic_id or not status:
            raise ValueError("'slug', 'topicId', and 'status' are required.")

        ct = _load_script_module("chapter_tracker", self.server.workspace, self.server.package_root)
        valid_statuses = tuple(getattr(ct, "VALID_TOPIC_STATUSES", ("pending", "probed", "in_progress", "mastered")))
        if status not in valid_statuses:
            raise ValueError(f"Invalid topic status {status!r}; expected one of {valid_statuses}.")

        tpath = ct.tracker_path_for(slug, self.server.chapters_dir)
        with workspace_lock(tpath):
            tracker = ct.load_tracker(slug, self.server.chapters_dir)
            topic = next((t for t in tracker.get("topics") or [] if t.get("id") == topic_id), None)
            if topic is None:
                raise FileNotFoundError(f"Topic '{topic_id}' not found in tracker '{slug}'.")
            prev_status = topic.get("status")
            topic["status"] = status
            tracker.setdefault("sessionLog", []).append({
                "at": now_iso(),
                "action": "set_topic",
                "detail": {"topicId": topic_id, "from": prev_status, "to": status},
            })
            ct.save_tracker(tracker, self.server.chapters_dir, kg_dir=self.server.kg_dir, no_sync=True)

        self.server.ensure_synced_graph(force=True)
        self.server.notify_change("tracker_topic")
        self._send_json({
            "ok": True,
            "slug": tracker["slug"],
            "topicId": topic_id,
            "status": status,
            "topic": topic,
        })

    def _handle_tracker_milestone(self) -> None:
        body = self._read_json_body()
        if body is None:
            return
        slug = str(body.get("slug") or "").strip()
        key = str(body.get("key") or "").strip()
        if not slug or not key or "value" not in body:
            raise ValueError("'slug', 'key', and 'value' are required.")
        val = bool(body.get("value"))

        ct = _load_script_module("chapter_tracker", self.server.workspace, self.server.package_root)
        valid_keys = tuple(getattr(ct, "VALID_MILESTONES", ()))
        if valid_keys and key not in valid_keys:
            raise ValueError(f"Invalid milestone key {key!r}; expected one of {valid_keys}.")

        tpath = ct.tracker_path_for(slug, self.server.chapters_dir)
        with workspace_lock(tpath):
            tracker = ct.load_tracker(slug, self.server.chapters_dir)
            milestones = tracker.setdefault("milestones", ct.default_milestones())
            milestones[key] = val
            if key == "cbtMockPassed" and val:
                milestones["cbtMockBuilt"] = True
            tracker.setdefault("sessionLog", []).append({
                "at": now_iso(),
                "action": "set_milestone",
                "detail": {"key": key, "value": val},
            })
            ct.save_tracker(tracker, self.server.chapters_dir, kg_dir=self.server.kg_dir, no_sync=True)

        self.server.ensure_synced_graph(force=True)
        self.server.notify_change("tracker_milestone")
        self._send_json({
            "ok": True,
            "slug": tracker["slug"],
            "key": key,
            "value": val,
            "milestones": milestones,
        })

    def _handle_import(self) -> None:
        body = self._read_json_body(max_bytes=MAX_IMPORT_JSON_BODY_BYTES)
        if body is None:
            return

        from syntopik import ingest

        kind = str(body.get("kind") or "auto").strip().lower()
        slug = body.get("slug")
        subject = str(body.get("subject") or "General")
        chapter = body.get("chapter")
        delimiter = body.get("delimiter")
        register_tracker = bool(body.get("registerTracker", False))
        dry_run = bool(body.get("dryRun", body.get("dry_run", False)))
        raw_limit = body.get("limit")
        limit_val: int | None = None
        if raw_limit is not None:
            try:
                limit_val = max(1, min(2000, int(raw_limit)))
            except (TypeError, ValueError) as exc:
                raise ValueError("'limit' must be an integer.") from exc

        uploaded_rel_path: str | None = None
        with contextlib.ExitStack() as stack:
            if body.get("contentBase64") is not None:
                raw_b64 = body.get("contentBase64")
                if not isinstance(raw_b64, str) or not raw_b64.strip():
                    raise ValueError("'contentBase64' must be a non-empty base64 string.")
                safe_name, _ext = _sanitize_upload_filename(body.get("filename"))
                b64_payload = raw_b64.strip()
                if b64_payload.lower().startswith("data:") and ";base64," in b64_payload:
                    b64_payload = b64_payload.split(";base64,", 1)[1]
                # Fast upper-bound precheck before decoding
                if len(b64_payload) > ((MAX_IMPORT_UPLOAD_DECODED_BYTES * 4) // 3) + 128:
                    self._send_error_json(
                        HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                        f"Uploaded file exceeds {MAX_IMPORT_UPLOAD_DECODED_BYTES // (1024 * 1024)} MiB ceiling.",
                    )
                    return
                try:
                    decoded = base64.b64decode(b64_payload, validate=True)
                except Exception as exc:
                    raise ValueError(f"Invalid base64 upload payload: {exc}") from exc
                if len(decoded) > MAX_IMPORT_UPLOAD_DECODED_BYTES:
                    self._send_error_json(
                        HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                        f"Decoded file ({len(decoded)} bytes) exceeds {MAX_IMPORT_UPLOAD_DECODED_BYTES} byte limit.",
                    )
                    return

                if dry_run:
                    tmp_dir = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="synt-import-dryrun-"))).resolve()
                    dest_path = (tmp_dir / safe_name).resolve()
                    if not dest_path.is_relative_to(tmp_dir) or dest_path.parent != tmp_dir:
                        raise PermissionError("Dry-run upload destination escaped temp directory")
                    dest_path.write_bytes(decoded)
                    target = str(dest_path)
                    uploaded_rel_path = None
                else:
                    uploads_root = (self.server.workspace / "sources" / "_uploads").resolve()
                    if not uploads_root.is_relative_to(self.server.workspace):
                        raise PermissionError("Upload root escaped workspace")
                    uploads_root.mkdir(parents=True, exist_ok=True)
                    ts_tag = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                    dest_name = f"{ts_tag}-{secrets.token_hex(3)}-{safe_name}"
                    dest_path = (uploads_root / dest_name).resolve()
                    if not dest_path.is_relative_to(uploads_root) or dest_path.parent != uploads_root:
                        raise PermissionError("Upload destination escaped sources/_uploads/")
                    dest_path.write_bytes(decoded)
                    target = str(dest_path)
                    uploaded_rel_path = dest_path.relative_to(self.server.workspace).as_posix()
            else:
                target = str(body.get("target") or body.get("url") or "").strip()
                if not target:
                    raise ValueError("'target' or ('filename' + 'contentBase64') is required.")
                # Check remote scheme / arXiv ID purely syntactically BEFORE touching disk (prevents existence oracle)
                is_remote = (
                    kind in ("youtube", "arxiv", "url")
                    or target.lower().startswith(("http://", "https://"))
                    or bool(_ARXIV_ID_RE.match(target))
                )
                if not is_remote:
                    jailed = self._resolve_user_path_jailed(target, allow_temp=False)
                    if jailed is None:
                        raise PermissionError("Local import path must stay within workspace")
                    target = str(jailed)

            common_kwargs: dict[str, Any] = {
                "workspace": self.server.workspace,
                "slug": slug,
                "subject": subject,
                "register_tracker": register_tracker,
            }
            if kind == "youtube":
                if body.get("vttText") is not None:
                    common_kwargs["vtt_text"] = body["vttText"]
                res = ingest.ingest_youtube(target, **common_kwargs)
            elif kind == "arxiv":
                if body.get("atomXml") is not None:
                    common_kwargs["atom_xml"] = body["atomXml"]
                res = ingest.ingest_arxiv(target, **common_kwargs)
            elif kind == "repo":
                res = ingest.ingest_repo(target, **common_kwargs)
            elif kind == "url":
                if body.get("htmlText") is not None:
                    common_kwargs["html_text"] = body["htmlText"]
                res = ingest.ingest_url(target, **common_kwargs)
            elif kind == "notes":
                res = ingest.ingest_notes(target, **common_kwargs)
            elif kind == "anki":
                anki_kw: dict[str, Any] = {
                    "workspace": self.server.workspace,
                    "kg_dir": self.server.kg_dir,
                    "subject": subject,
                    "chapter": chapter,
                    "dry_run": dry_run,
                }
                if limit_val is not None:
                    anki_kw["limit"] = limit_val
                res = ingest.ingest_anki(target, **anki_kw)
            elif kind == "cards":
                cards_kw: dict[str, Any] = {
                    "workspace": self.server.workspace,
                    "kg_dir": self.server.kg_dir,
                    "delimiter": delimiter,
                    "subject": subject,
                    "chapter": chapter,
                    "dry_run": dry_run,
                }
                if limit_val is not None:
                    cards_kw["limit"] = limit_val
                res = ingest.ingest_cards(target, **cards_kw)
            elif kind == "notebook":
                nb_subj = subject if subject != "General" else "Computer Science"
                res = ingest.ingest_notebook(
                    target,
                    workspace=self.server.workspace,
                    kg_dir=self.server.kg_dir,
                    slug=slug,
                    subject=nb_subj,
                    chapter=chapter,
                    create_chapter=bool(body.get("createChapter", False)),
                    dry_run=dry_run,
                )
            elif kind == "notes_batch":
                nb_kw: dict[str, Any] = {
                    "workspace": self.server.workspace,
                    "kg_dir": self.server.kg_dir,
                    "slug": slug,
                    "subject": subject,
                    "extract_props": bool(body.get("extractProps", True)),
                    "dry_run": dry_run,
                }
                if limit_val is not None:
                    nb_kw["limit"] = limit_val
                res = ingest.ingest_notes_batch(target, **nb_kw)
            else:
                auto_kw: dict[str, Any] = {
                    **common_kwargs,
                    "kg_dir": self.server.kg_dir,
                    "dry_run": dry_run,
                }
                if chapter is not None:
                    auto_kw["chapter"] = chapter
                if delimiter is not None:
                    auto_kw["delimiter"] = delimiter
                if limit_val is not None:
                    auto_kw["limit"] = limit_val
                if body.get("createChapter") is not None:
                    auto_kw["create_chapter"] = bool(body["createChapter"])
                if body.get("htmlText") is not None:
                    auto_kw["html_text"] = body["htmlText"]
                if body.get("vttText") is not None:
                    auto_kw["vtt_text"] = body["vttText"]
                if body.get("atomXml") is not None:
                    auto_kw["atom_xml"] = body["atomXml"]
                res = ingest.ingest_auto(target, **auto_kw)

        if isinstance(res, dict) and res.get("ok") is False:
            self._send_json(res, status=HTTPStatus.BAD_REQUEST)
            return

        if not dry_run:
            self.server.ensure_synced_graph(force=True)
            self.server.notify_change("import")

        out = {"ok": True, **res}
        if uploaded_rel_path:
            out["uploadedPath"] = uploaded_rel_path
        if "summary" not in out:
            res_kind = str(out.get("kind") or out.get("detectedKind") or kind)
            if res_kind in ("anki", "cards", "notebook", "notes_batch"):
                imp_n = int(out.get("importedCount") or 0)
                dup_n = int(out.get("deduplicatedCount") or 0)
                dup_txt = f" ({dup_n} deduped)" if dup_n else ""
                out["summary"] = f"{imp_n} unseen cue{'s' if imp_n != 1 else ''} imported (0 fake mastery){dup_txt}"
        self._send_json(out)

    def _handle_export_obsidian(self) -> None:
        body = self._read_json_body()
        if body is None:
            return
        from syntopik.obsidian import export_vault

        raw_dir = body.get("vaultDir")
        if raw_dir:
            dest = self._resolve_user_path_jailed(str(raw_dir), allow_temp=True)
            if dest is None:
                raise PermissionError("vaultDir must be within workspace or temp directory")
        else:
            dest = (self.server.workspace / "exports" / "obsidian-vault").resolve()
        res = export_vault(dest, workspace=self.server.workspace, kg_dir=self.server.kg_dir)
        self._send_json({"ok": True, **res})

    def _handle_export_pocket(self) -> None:
        body = self._read_json_body()
        if body is None:
            return
        from syntopik.pocket import export_pocket_html, render_qr_svg

        limit = max(1, min(200, int(body.get("limit") or 25)))
        chapter = body.get("chapter")
        title = body.get("title")
        res = export_pocket_html(
            workspace=self.server.workspace,
            limit=limit,
            chapter=chapter,
            kg_dir=self.server.kg_dir,
            title=title,
            api_base="",
        )
        self.server.notify_change("export_pocket")
        extra: dict[str, Any] = {}
        if self.server.lan:
            active_lan_url = self.server.ensure_active_pair_url()
            if active_lan_url:
                extra["lanUrl"] = active_lan_url
                try:
                    extra["qrSvg"] = render_qr_svg(active_lan_url)
                except Exception:
                    pass
        self._send_json({"ok": True, "url": "/chapters/pocket-review.html", "pocketUrl": "/pocket", **extra, **res})

    def _handle_pack_export(self) -> None:
        body = self._read_json_body()
        if body is None:
            return
        target = str(body.get("target") or "").strip()
        if not target:
            raise ValueError("'target' (chapter slug or subject) is required.")
        from syntopik.packs import export_pack

        out_path_raw = body.get("outPath")
        if out_path_raw:
            out_path = self._resolve_user_path_jailed(str(out_path_raw), allow_temp=True)
            if out_path is None:
                raise PermissionError("outPath must be within workspace or temp directory")
        else:
            self.server.chapters_dir.mkdir(parents=True, exist_ok=True)
            out_path = (self.server.chapters_dir / f"{slugify(target)}.syntopik-pack.zip").resolve()

        res = export_pack(
            target,
            workspace=self.server.workspace,
            kg_dir=self.server.kg_dir,
            chapters_dir=self.server.chapters_dir,
            out_path=out_path,
            author=str(body.get("author") or ""),
            license_id=str(body.get("license") or "CC-BY-4.0"),
            title=body.get("title"),
            include_exams=bool(body.get("includeExams", False)),
        )
        final_out = Path(str(res.get("outPath") or out_path)).resolve()
        extra: dict[str, Any] = {}
        if final_out.is_relative_to(self.server.workspace):
            rel_posix = final_out.relative_to(self.server.workspace).as_posix()
            extra["relPath"] = rel_posix
            if rel_posix.startswith(("chapters/", "sources/", "exams/", "demos/")):
                extra["url"] = f"/{rel_posix}"
        self._send_json({"ok": True, **res, **extra})

    def _handle_pack_import(self) -> None:
        body = self._read_json_body()
        if body is None:
            return
        pack_slug_raw = str(body.get("packSlug") or "").strip()
        pack_raw = str(body.get("packPath") or "").strip()
        if not pack_slug_raw and not pack_raw:
            raise ValueError("Provide either 'packSlug' (bundled starter pack) or workspace-jailed 'packPath'.")

        resolved_slug: str | None = None
        if pack_slug_raw:
            resolved_slug, pack_path = _resolve_starter_pack_slug(
                pack_slug_raw,
                self.server.workspace,
                self.server.package_root,
            )
        else:
            pack_path = self._resolve_user_path_jailed(pack_raw, allow_temp=False)
            if pack_path is None:
                raise PermissionError("packPath must be within workspace")
        from syntopik.packs import import_pack

        res = import_pack(
            pack_path,
            workspace=self.server.workspace,
            kg_dir=self.server.kg_dir,
            chapters_dir=self.server.chapters_dir,
            mode=str(body.get("mode") or "merge"),
            probe=bool(body.get("probe", False)),
            dry_run=bool(body.get("dryRun", False)),
            no_sync=True,
            allow_code_arenas=bool(resolved_slug),
        )
        if not body.get("dryRun"):
            self.server.ensure_synced_graph(force=True)
            self.server.notify_change("pack_import")
        out: dict[str, Any] = {"ok": True, **res}
        if resolved_slug:
            out["packSlug"] = resolved_slug
        self._send_json(out)

    def _handle_demo_load(self) -> None:
        body = self._read_json_body()
        if body is None:
            return
        pack_slug_raw = str(body.get("packSlug") or body.get("slug") or "").strip()
        if not pack_slug_raw:
            raise ValueError("'packSlug' is required.")
        slug, pack_path = _resolve_starter_pack_slug(
            pack_slug_raw,
            self.server.workspace,
            self.server.package_root,
        )
        from syntopik.packs import import_pack

        res = import_pack(
            pack_path,
            workspace=self.server.workspace,
            kg_dir=self.server.kg_dir,
            chapters_dir=self.server.chapters_dir,
            mode=str(body.get("mode") or "merge"),
            probe=bool(body.get("probe", False)),
            dry_run=bool(body.get("dryRun", False)),
            no_sync=True,
            allow_code_arenas=True,
        )
        if not body.get("dryRun"):
            self.server.ensure_synced_graph(force=True)
            self.server.notify_change("demo_load")
        self._send_json({"ok": True, "packSlug": slug, **res})

    def _handle_triage_plan(self) -> None:
        body = self._read_json_body()
        if body is None:
            return
        hours = float(body.get("hours") or 48.0)
        sleep_hours = float(body.get("sleepHours") or 7.0)
        chapter = body.get("chapter")
        daily_plan = _load_script_module("daily_plan", self.server.workspace, self.server.package_root)
        plan = daily_plan.build_triage_plan(
            hours_left=hours,
            sleep_hours=sleep_hours,
            kg_dir=self.server.kg_dir,
            chapters_dir=self.server.chapters_dir,
            chapter=chapter,
        )
        self._send_json({"ok": True, **plan})

    def _handle_triage_crib_sheet(self) -> None:
        body = self._read_json_body()
        if body is None:
            return
        chapter = body.get("chapter")
        max_pages = max(1, min(2, int(body.get("pages") or 1)))
        spec_only = bool(body.get("specOnly", False))
        title = body.get("title")

        cs = _load_script_module("cheatsheet_pdf", self.server.workspace, self.server.package_root)
        spec = cs.build_anti_mastery_spec(
            self.server.kg_dir,
            chapter=chapter,
            anti_mastery=True,
            max_pages=max_pages,
            title=title,
        )
        slug = slugify(str(spec.get("slug") or chapter or "anti-mastery-crib"))
        self.server.chapters_dir.mkdir(parents=True, exist_ok=True)
        spec_path = self.server.chapters_dir / f"{slug}-anti-mastery-spec.json"
        save_json_atomic(spec_path, spec)

        typst_bin = None
        if not spec_only:
            try:
                typst_bin = cs.find_typst()
            except SystemExit:
                typst_bin = None

        if typst_bin and not spec_only:
            out_pdf = self.server.chapters_dir / f"{slug}-anti-mastery-sheet.pdf"
            with tempfile.TemporaryDirectory(prefix="synt-crib-") as tmp:
                stage_dir = Path(tmp)
                typ_src = cs.build_typst_source(
                    spec,
                    base_dir=self.server.workspace,
                    stage_dir=stage_dir,
                )
                typ_file = stage_dir / "sheet.typ"
                typ_file.write_text(typ_src, encoding="utf-8")
                try:
                    proc = subprocess.run(
                        [typst_bin, "compile", "--root", str(stage_dir), str(typ_file), str(out_pdf)],
                        capture_output=True,
                        text=True,
                        timeout=30,
                        check=False,
                    )
                except subprocess.TimeoutExpired:
                    proc = None
            if proc is not None and proc.returncode == 0 and out_pdf.is_file():
                pages = cs.count_pdf_pages(out_pdf)
                self.server.ensure_synced_graph(force=True)
                self.server.notify_change("triage_crib_sheet")
                self._send_json({
                    "ok": True,
                    "compiled": True,
                    "pdfPath": str(out_pdf),
                    "url": f"/chapters/{out_pdf.name}",
                    "pages": pages,
                    "bytes": out_pdf.stat().st_size,
                    "antiMasteryMeta": spec.get("antiMasteryMeta"),
                    "spec": spec,
                })
                return

        self._send_json({
            "ok": True,
            "compiled": False,
            "specPath": str(spec_path),
            "url": f"/chapters/{spec_path.name}",
            "antiMasteryMeta": spec.get("antiMasteryMeta"),
            "spec": spec,
        })

    def _handle_forks_check(self) -> None:
        body = self._read_json_body()
        if body is None:
            return
        fork_id = str(body.get("forkId") or "").strip()
        move = str(body.get("move") or "")
        if not fork_id or not move.strip():
            raise ValueError("'forkId' and 'move' are required.")
        from syntopik.forks import check_fork_move

        lat_raw = body.get("latencyMs")
        latency_ms = int(lat_raw) if isinstance(lat_raw, (int, float)) and not isinstance(lat_raw, bool) else None
        res = check_fork_move(
            fork_id,
            move,
            workspace=self.server.workspace,
            kg_dir=self.server.kg_dir,
            latency_ms=latency_ms,
            variant_id=body.get("variantId"),
            reveal_fix=bool(body.get("revealFix", False)),
        )
        self.server.ensure_synced_graph(force=True)
        self.server.notify_change("forks_check")
        self._send_json({"ok": True, **res})

    def _handle_arena_submit(self) -> None:
        body = self._read_json_body()
        if body is None:
            return
        arena_id = str(body.get("arenaId") or "").strip()
        raw_input = body.get("input")
        if not arena_id or raw_input is None:
            raise ValueError("'arenaId' and 'input' are required.")
        arena = _load_script_module("falsify_arena", self.server.workspace, self.server.package_root)
        raw_str = raw_input if isinstance(raw_input, str) else json.dumps(raw_input)
        timeout_sec = float(body.get("timeoutSec") or 3.0)
        res = arena.test_arena_input(
            arena_id,
            raw_str,
            workspace=self.server.workspace,
            kg_dir=self.server.kg_dir,
            timeout_sec=timeout_sec,
        )
        self.server.ensure_synced_graph(force=True)
        self.server.notify_change("arena_submit")
        self._send_json({"ok": True, **res})

    def _handle_arena_repair(self) -> None:
        body = self._read_json_body()
        if body is None:
            return
        arena_id = str(body.get("arenaId") or "").strip()
        lemma = str(body.get("lemma") or "").strip()
        if not arena_id or not lemma:
            raise ValueError("'arenaId' and 'lemma' are required.")
        arena = _load_script_module("falsify_arena", self.server.workspace, self.server.package_root)
        res = arena.repair_arena_lemma(
            arena_id,
            lemma,
            workspace=self.server.workspace,
            kg_dir=self.server.kg_dir,
        )
        self.server.ensure_synced_graph(force=True)
        self.server.notify_change("arena_repair")
        self._send_json({"ok": True, **res})

    def _handle_blitz_build(self) -> None:
        body = self._read_json_body()
        if body is None:
            return
        raw_n = body.get("count") if body.get("count") is not None else body.get("n")
        n = max(1, min(20, int(raw_n or 6)))
        seed_raw = body.get("seed")
        seed = int(seed_raw) if isinstance(seed_raw, (int, float)) and not isinstance(seed_raw, bool) else None
        blitz = _load_script_module("decision_blitz", self.server.workspace, self.server.package_root)
        res = blitz.build_blitz(
            slug=body.get("slug"),
            n=n,
            subject=body.get("subject"),
            seed=seed,
            include_key=False,
            workspace=self.server.workspace,
            kg_dir=self.server.kg_dir,
            chapters_dir=self.server.chapters_dir,
        )
        self.server.notify_change("blitz_build")
        self._send_json({"ok": True, **res})

    def _handle_blitz_grade(self) -> None:
        body = self._read_json_body()
        if body is None:
            return
        blitz = _load_script_module("decision_blitz", self.server.workspace, self.server.package_root)
        res = blitz.grade_decision(
            prop_id=body.get("propId"),
            question_ref=body.get("questionRef"),
            kill_cue_hit=body.get("killCueHit"),
            first_move_hit=body.get("firstMoveHit"),
            decoy_hit=body.get("decoyHit"),
            decision_ms=int(body.get("decisionMs") or 0),
            surface_tags=body.get("surfaceTags"),
            deep_principle=str(body.get("deepPrinciple") or ""),
            blitz_id=body.get("blitzId"),
            item_id=body.get("itemId"),
            kill_cue_answer=body.get("killCueAnswer"),
            first_move_answer=body.get("firstMoveAnswer"),
            rejected_decoy_answer=body.get("rejectedDecoyAnswer"),
            workspace=self.server.workspace,
            kg_dir=self.server.kg_dir,
        )
        self.server.ensure_synced_graph(force=True)
        self.server.notify_change("blitz_grade")
        self._send_json({"ok": True, **res})

    # ------------------------------------------------------------------
    # v3.0 POST / PATCH API handlers (loopback + CSRF)
    # ------------------------------------------------------------------

    def _handle_profile_calibrate(self) -> None:
        body = self._read_json_body()
        if body is None:
            return
        from syntopik.learner_model import (
            PROFILE_FILENAME,
            apply_tier1_calibration,
            compile_teaching_policy,
            load_profile,
            save_profile,
        )

        entry_ramp = body.get("entryRamp") or body.get("entry_ramp") or "balanced"
        challenge_appetite = body.get("challengeAppetite") or body.get("challenge_appetite") or "balanced"
        domain_fluency = body.get("domainFluency") if "domainFluency" in body else body.get("domain_fluency")
        session_minutes = body.get("sessionMinutes") or body.get("session_minutes") or body.get("sessionTargetMinutes") or 30
        prose_density = body.get("proseDensity") or body.get("prose_density") or "standard"
        micro_results = body.get("microTaskResults") if "microTaskResults" in body else body.get("micro_task_results")

        prof_path = self.server.kg_dir / PROFILE_FILENAME
        with workspace_lock(prof_path):
            profile = load_profile(self.server.kg_dir)
            updated = apply_tier1_calibration(
                profile,
                entry_ramp=str(entry_ramp),
                challenge_appetite=str(challenge_appetite),
                domain_fluency=domain_fluency if isinstance(domain_fluency, dict) else None,
                session_minutes=int(session_minutes),
                prose_density=str(prose_density),
                micro_task_results=micro_results if isinstance(micro_results, list) else None,
            )
            policy = compile_teaching_policy(updated)
            updated["compiledPolicy"] = policy
            save_profile(self.server.kg_dir, updated)

        self.server.ensure_synced_graph(force=True)
        self.server.notify_change("profile_calibrate")
        self._send_json({"ok": True, "profile": updated, "compiledPolicy": policy})

    def _handle_profile_override(self) -> None:
        body = self._read_json_body()
        if body is None:
            return
        from syntopik.learner_model import apply_user_overrides

        overrides = body.get("overrides") if isinstance(body.get("overrides"), dict) else body
        res = apply_user_overrides(self.server.kg_dir, overrides)
        self.server.ensure_synced_graph(force=True)
        self.server.notify_change("profile_override")
        self._send_json(res)

    def _handle_export_dossier(self) -> None:
        body = self._read_json_body()
        if body is None:
            return
        from syntopik.evidence import export_mastery_dossier

        out_raw = body.get("outPath")
        allowed_roots = (
            self.server.chapters_dir.resolve(),
            (self.server.workspace / "exports").resolve(),
        )
        if out_raw:
            out_path = self._resolve_user_path_jailed(str(out_raw), allow_temp=False)
            if out_path is None or not any(out_path.is_relative_to(r) for r in allowed_roots):
                raise PermissionError("outPath must stay within chapters/ or exports/")
        else:
            self.server.chapters_dir.mkdir(parents=True, exist_ok=True)
            out_path = (self.server.chapters_dir / "mastery-dossier.html").resolve()

        res = export_mastery_dossier(self.server.workspace, out_path)
        extra: dict[str, Any] = {}
        final_out = Path(str(res.get("outPath") or out_path)).resolve()
        if final_out.is_relative_to(self.server.workspace):
            rel_posix = final_out.relative_to(self.server.workspace).as_posix()
            extra["relPath"] = rel_posix
            if rel_posix.startswith(("chapters/", "sources/", "exams/", "demos/")):
                extra["url"] = f"/{rel_posix}"
        self.server.ensure_synced_graph(force=True)
        self.server.notify_change("export_dossier")
        self._send_json({"ok": True, **res, **extra})

    def _handle_derivation_check(self) -> None:
        body = self._read_json_body()
        if body is None:
            return
        deriv_id = str(body.get("derivationId") or body.get("id") or "").strip()
        step_id = str(body.get("stepId") or body.get("step") or "").strip()
        answer = str(body.get("answer") or "")
        if not deriv_id or not step_id or not answer.strip():
            raise ValueError("'derivationId', 'stepId', and 'answer' are required.")
        df = _load_script_module("derivation_forge", self.server.workspace, self.server.package_root)
        res = df.check_blackout_step(
            deriv_id,
            step_id,
            answer,
            kg_dir=self.server.kg_dir,
            workspace=self.server.workspace,
            reveal=bool(body.get("reveal", False)),
        )
        self.server.ensure_synced_graph(force=True)
        self.server.notify_change("derivation_check")
        self._send_json(res)

    def _handle_derivation_cut(self) -> None:
        body = self._read_json_body()
        if body is None:
            return
        deriv_id = str(body.get("derivationId") or body.get("id") or "").strip()
        axiom_id = str(body.get("axiomId") or body.get("axiomStepId") or body.get("axiom") or "").strip()
        guess_step_id = body.get("guessStepId") or body.get("guessStep")
        if not deriv_id or not axiom_id:
            raise ValueError("'derivationId' and 'axiomId' are required.")
        df = _load_script_module("derivation_forge", self.server.workspace, self.server.package_root)
        res = df.grade_axiom_cut(
            deriv_id,
            axiom_id,
            guess_step_id=str(guess_step_id).strip() if guess_step_id is not None and str(guess_step_id).strip() else None,
            kg_dir=self.server.kg_dir,
            workspace=self.server.workspace,
        )
        self.server.ensure_synced_graph(force=True)
        self.server.notify_change("derivation_cut")
        self._send_json(res)

    def _handle_viva_start(self) -> None:
        body = self._read_json_body()
        if body is None:
            return
        prop_id = str(body.get("propId") or body.get("id") or "").strip()
        if not prop_id:
            raise ValueError("'propId' is required.")
        vc = _load_script_module("viva_chamber", self.server.workspace, self.server.package_root)
        res = vc.start_viva(
            prop_id,
            kg_dir=self.server.kg_dir,
            workspace=self.server.workspace,
        )
        self.server.ensure_synced_graph(force=True)
        self.server.notify_change("viva_start")
        self._send_json(res)

    def _handle_viva_turn(self) -> None:
        body = self._read_json_body()
        if body is None:
            return
        session_id = str(body.get("sessionId") or body.get("id") or "").strip()
        response = str(body.get("response") or "")
        if not session_id or not response.strip():
            raise ValueError("'sessionId' and 'response' are required.")
        conf_raw = body.get("confidence")
        confidence = float(conf_raw) if isinstance(conf_raw, (int, float)) and not isinstance(conf_raw, bool) else None
        vc = _load_script_module("viva_chamber", self.server.workspace, self.server.package_root)
        res = vc.submit_viva_turn(
            session_id,
            response,
            confidence=confidence,
            kg_dir=self.server.kg_dir,
            workspace=self.server.workspace,
        )
        self.server.ensure_synced_graph(force=True)
        self.server.notify_change("viva_turn")
        self._send_json(res)

    def _handle_whiteboard_push(self) -> None:
        body = self._read_json_body()
        if body is None:
            return
        from syntopik.whiteboard import push_frame

        raw_frame = body.get("frame") if isinstance(body.get("frame"), dict) else body
        frame_in = dict(raw_frame)
        if str(frame_in.get("kind") or "").strip().lower() == "prediction_gate":
            if "canonicalAnswer" in frame_in and "expectedAnswer" not in frame_in:
                frame_in["expectedAnswer"] = frame_in["canonicalAnswer"]
            gate_in = dict(frame_in.get("gate")) if isinstance(frame_in.get("gate"), dict) else {}
            if "canonicalAnswer" in gate_in and "expectedAnswer" not in gate_in:
                gate_in["expectedAnswer"] = gate_in["canonicalAnswer"]
            if gate_in:
                frame_in["gate"] = gate_in

        res = push_frame(self.server.kg_dir, frame_in)
        safe_frame = _sanitize_whiteboard_frame(res["frame"])
        safe_session = _sanitize_whiteboard(res["session"])
        self.server.ensure_synced_graph(force=True)
        self.server.notify_change(
            "whiteboard_push",
            event_type="whiteboard_frame",
            payload={"action": "push", "revision": res["revision"], "frame": safe_frame, "whiteboard": safe_session},
        )
        self._send_json({
            "ok": True,
            "revision": res["revision"],
            "frame": safe_frame,
            "session": safe_session,
        })

    def _handle_whiteboard_unlock(self) -> None:
        body = self._read_json_body()
        if body is None:
            return
        from syntopik.whiteboard import get_session, unlock_gate

        frame_id = str(body.get("frameId") or "").strip()
        prediction = str(body.get("prediction") or "")
        conf_raw = body.get("confidence")
        confidence = float(conf_raw) if isinstance(conf_raw, (int, float)) and not isinstance(conf_raw, bool) else 0.5
        res = unlock_gate(self.server.kg_dir, frame_id, prediction, confidence=confidence)
        safe_frame = _sanitize_whiteboard_frame(res["frame"])
        safe_session = _sanitize_whiteboard(get_session(self.server.kg_dir))
        self.server.ensure_synced_graph(force=True)
        self.server.notify_change(
            "whiteboard_unlock",
            event_type="whiteboard_frame",
            payload={"action": "unlock", "revision": res["revision"], "frame": safe_frame, "whiteboard": safe_session},
        )
        self._send_json({
            "ok": True,
            "revision": res["revision"],
            "frameId": res["frameId"],
            "unlocked": True,
            "prediction": res["prediction"],
            "confidence": res["confidence"],
            "frame": safe_frame,
            "session": safe_session,
        })

    def _handle_whiteboard_pin(self) -> None:
        body = self._read_json_body()
        if body is None:
            return
        from syntopik.whiteboard import get_session, pin_step

        if body.get("stepIndex") is None:
            raise ValueError("'stepIndex' is required.")
        step_index = int(body["stepIndex"])
        note = str(body.get("note") or "")
        frame_id = body.get("frameId")
        res = pin_step(
            self.server.kg_dir,
            step_index,
            note,
            frame_id=str(frame_id).strip() if frame_id is not None and str(frame_id).strip() else None,
        )
        safe_session = _sanitize_whiteboard(get_session(self.server.kg_dir))
        self.server.ensure_synced_graph(force=True)
        self.server.notify_change(
            "whiteboard_pin",
            event_type="whiteboard_frame",
            payload={"action": "pin", "revision": res["revision"], "pin": res["pin"], "whiteboard": safe_session},
        )
        self._send_json({**res, "session": safe_session})

    def _handle_whiteboard_clear(self) -> None:
        from syntopik.whiteboard import clear_session

        res = clear_session(self.server.kg_dir)
        safe_session = _sanitize_whiteboard(res["session"])
        self.server.ensure_synced_graph(force=True)
        self.server.notify_change(
            "whiteboard_clear",
            event_type="whiteboard_frame",
            payload={"action": "clear", "revision": res["revision"], "whiteboard": safe_session},
        )
        self._send_json({
            "ok": True,
            "cleared": True,
            "revision": res["revision"],
            "session": safe_session,
        })

    # ------------------------------------------------------------------
    # v3.1 GET / POST API handlers (Rosetta, Multi-Device CRDT Sync,
    # Longitudinal Replay, Proof-Graph Diff — strictly loopback + CSRF)
    # ------------------------------------------------------------------

    def _handle_rosetta_get(self, query: dict[str, list[str]]) -> None:
        raw_min = (query.get("minScore") or ["0.35"])[0]
        try:
            min_score = max(0.0, min(1.0, float(raw_min)))
        except ValueError:
            min_score = 0.35

        from syntopik.isomorphism import (
            build_bijection_opener,
            discover_isomorphisms,
            get_isomorphic_neighbors,
            load_archetypes,
        )

        res = discover_isomorphisms(self.server.kg_dir, min_score=min_score)
        out: dict[str, Any] = {
            **res,
            "archetypesCount": len(load_archetypes()),
        }
        prop_id = ((query.get("propId") or query.get("nodeId") or [""])[0]).strip()
        if prop_id:
            out["propId"] = prop_id
            out["neighbors"] = get_isomorphic_neighbors(self.server.kg_dir, prop_id, min_score=min_score)
            out["opener"] = build_bijection_opener(self.server.kg_dir, prop_id, min_score=min_score)
        self._send_json(out)

    def _handle_rosetta_link(self) -> None:
        body = self._read_json_body()
        if body is None:
            return
        source_id = str(body.get("sourceId") or body.get("source_id") or "").strip()
        target_id = str(body.get("targetId") or body.get("target_id") or "").strip()
        archetype_id = str(body.get("archetypeId") or body.get("archetype_id") or "").strip()
        if not source_id or not target_id or not archetype_id:
            raise ValueError("'sourceId', 'targetId', and 'archetypeId' are required.")

        from syntopik.isomorphism import DEFAULT_ISOMORPHIC_WEIGHT, link_isomorphism_chord

        breaks_at = body.get("breaksAt") if "breaksAt" in body else body.get("breaks_at")
        raw_weight = body.get("encompassWeight", body.get("encompass_weight", DEFAULT_ISOMORPHIC_WEIGHT))
        try:
            weight = float(raw_weight)
        except (TypeError, ValueError):
            weight = DEFAULT_ISOMORPHIC_WEIGHT

        res = link_isomorphism_chord(
            self.server.kg_dir,
            source_id,
            target_id,
            archetype_id,
            breaks_at=breaks_at if isinstance(breaks_at, (list, str)) else None,
            encompass_weight=weight,
        )
        self.server.ensure_synced_graph(force=True)
        self.server.notify_change("rosetta_link")
        rosetta = _build_rosetta_payload(self.server.kg_dir)
        self._send_json({"ok": True, **res, "rosetta": rosetta})

    def _handle_sync_status(self) -> None:
        self._send_json(_build_sync_status_payload(self.server.workspace))

    def _handle_sync_export(self) -> None:
        body = self._read_json_body()
        if body is None:
            return
        from syntopik.sync_delta import export_sync_delta

        since_raw = body.get("since")
        since = str(since_raw).strip() if isinstance(since_raw, str) and since_raw.strip() else None
        save_to_disk = bool(body.get("saveToDisk", True))

        exports_root = (self.server.workspace / "exports").resolve()
        out_path: Path | None = None
        out_raw = body.get("outPath")
        if out_raw is not None and str(out_raw).strip():
            jailed = self._resolve_user_path_jailed(str(out_raw), allow_temp=False)
            if jailed is None or not jailed.is_relative_to(exports_root):
                raise PermissionError("outPath must stay strictly within exports/")
            if jailed == exports_root or jailed.is_dir():
                raise ValueError("outPath must name a .json file inside exports/, not a directory.")
            if jailed.suffix.lower() != ".json":
                raise ValueError("outPath must end with a .json suffix inside exports/.")
            out_path = jailed
        elif save_to_disk:
            ts_tag = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            out_path = (exports_root / f"syntopik-delta-{ts_tag}.json").resolve()
            if not out_path.is_relative_to(exports_root):
                raise PermissionError("Export path escaped exports/ jail")

        res = export_sync_delta(
            self.server.workspace,
            out_path=out_path,
            since=since,
        )
        extra: dict[str, Any] = {}
        if res.get("path"):
            p = Path(str(res["path"])).resolve()
            if p.is_relative_to(self.server.workspace):
                extra["relPath"] = p.relative_to(self.server.workspace).as_posix()
        if isinstance(res.get("delta"), dict):
            extra["deltaJson"] = json.dumps(res["delta"], indent=2, ensure_ascii=False, allow_nan=False) + "\n"
        self.server.notify_change("sync_export")
        self._send_json({"ok": True, **res, **extra})

    def _handle_sync_merge(self) -> None:
        body = self._read_json_body(max_bytes=MAX_SYNC_DELTA_BODY_BYTES)
        if body is None:
            return
        from syntopik.sync_delta import MAX_DELTA_BYTES, merge_sync_delta

        dry_run = bool(body.get("dryRun", body.get("dry_run", False)))
        delta_obj: dict[str, Any]
        if "delta" in body:
            raw_delta = body.get("delta")
            if isinstance(raw_delta, str):
                if len(raw_delta.encode("utf-8")) > MAX_DELTA_BYTES:
                    self._send_error_json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "Delta exceeds 10 MB limit")
                    return
                try:
                    parsed_delta = json.loads(raw_delta, parse_constant=_reject_non_finite)
                except Exception as exc:
                    raise ValueError(f"Malformed delta JSON: {exc}") from exc
                if not isinstance(parsed_delta, dict):
                    raise ValueError("'delta' JSON must be an object.")
                delta_obj = parsed_delta
            elif isinstance(raw_delta, dict):
                delta_obj = raw_delta
            else:
                raise ValueError("'delta' must be a JSON object or JSON string.")
        elif "payload" in body:
            delta_obj = body
        else:
            raise ValueError("'delta' object is required.")

        raw_bytes = json.dumps(delta_obj, ensure_ascii=False, allow_nan=False).encode("utf-8")
        if len(raw_bytes) > MAX_DELTA_BYTES:
            self._send_error_json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "Delta exceeds 10 MB limit")
            return

        res = merge_sync_delta(self.server.workspace, delta_obj, dry_run=dry_run)
        if not res.get("ok"):
            errs = res.get("errors") or ["Sync delta verification failed"]
            self._send_json(
                {**res, "error": "; ".join(str(e) for e in errs)},
                status=HTTPStatus.BAD_REQUEST,
            )
            return

        if not dry_run:
            self.server.ensure_synced_graph(force=True)
            self.server.notify_change("sync_merge")
        self._send_json(res)

    def _handle_profile_replay(self) -> None:
        body = self._read_json_body()
        if body is None:
            return
        from syntopik.sim_eval import replay_learner_history

        apply_flag = bool(body.get("apply") if "apply" in body else body.get("applyIfImproved", False))
        raw_min = body.get("minEvents", body.get("min_events", 12))
        try:
            min_events = max(2, min(100, int(raw_min)))
        except (TypeError, ValueError):
            min_events = 12

        res = replay_learner_history(
            self.server.workspace,
            min_events=min_events,
            apply_if_improved=apply_flag,
        )
        if res.get("applied"):
            self.server.ensure_synced_graph(force=True)
            self.server.notify_change("profile_replay")
        self._send_json(res)

    def _handle_derivation_diff(self) -> None:
        body = self._read_json_body()
        if body is None:
            return
        deriv_id = str(body.get("derivationId") or body.get("id") or "").strip()
        if not deriv_id:
            raise ValueError("'derivationId' is required.")

        raw_steps = body.get("studentSteps") if "studentSteps" in body else body.get("student_steps", body.get("steps"))
        if isinstance(raw_steps, str):
            raw_steps = [
                {"line": idx + 1, "expr": ln.strip()}
                for idx, ln in enumerate(raw_steps.splitlines())
                if ln.strip()
            ]
        if not isinstance(raw_steps, list) or len(raw_steps) == 0:
            raise ValueError("'studentSteps' must be a non-empty list of derivation steps.")
        if len(raw_steps) > MAX_DIFF_STEPS:
            raise ValueError(f"'studentSteps' exceeds maximum limit of {MAX_DIFF_STEPS} steps.")

        norm_steps: list[dict[str, Any]] = []
        for idx, item in enumerate(raw_steps, start=1):
            if isinstance(item, str):
                expr = item.strip()
                line_no = idx
                note = ""
            elif isinstance(item, dict):
                raw_line = item.get("line")
                line_no = int(raw_line) if isinstance(raw_line, int) and not isinstance(raw_line, bool) and raw_line > 0 else idx
                raw_expr = item.get("expr") if item.get("expr") is not None else item.get("expression")
                expr = str(raw_expr or "").strip()
                note = str(item.get("note") or "").strip()
            else:
                raise ValueError(f"studentSteps[{idx - 1}] must be an object or equation string.")
            if len(expr) > MAX_DIFF_EXPR_LEN:
                raise ValueError(f"Step line {line_no} exceeds {MAX_DIFF_EXPR_LEN} character limit.")
            if len(note) > MAX_DIFF_EXPR_LEN:
                note = note[:MAX_DIFF_EXPR_LEN]
            norm_steps.append({"line": line_no, "expr": expr, "note": note})

        auto_spawn = bool(body.get("autoSpawnFork", body.get("auto_spawn_fork", True)))
        df = _load_script_module("derivation_forge", self.server.workspace, self.server.package_root)
        res = df.diff_handwritten_steps(
            self.server.kg_dir,
            deriv_id,
            norm_steps,
            auto_spawn_fork=auto_spawn,
            workspace=self.server.workspace,
        )

        # Defense-in-depth zero-answer-leak guard on spawned fork & step entries
        if isinstance(res.get("spawnedFork"), dict) and isinstance(res["spawnedFork"].get("fork"), dict):
            res["spawnedFork"] = {
                **res["spawnedFork"],
                "fork": _sanitize_fork(res["spawnedFork"]["fork"]),
            }
        for st_entry in res.get("steps") or []:
            if isinstance(st_entry, dict):
                st_entry.pop("canonicalExpr", None)
                st_entry.pop("expectedExpr", None)

        self.server.ensure_synced_graph(force=True)
        self.server.notify_change("derivation_diff")
        self._send_json(res)

    # ------------------------------------------------------------------
    # v3.2 GET / POST API handlers (Exam Readiness & Blind Interleave)
    # ------------------------------------------------------------------

    def _handle_report_get(self, query: dict[str, list[str]]) -> None:
        from syntopik.report import build_progress_report

        raw_days = ((query.get("days") or ["7"])[0]).strip() or "7"
        try:
            days = int(raw_days)
        except ValueError as exc:
            raise ValueError("'days' must be a whole number between 1 and 365.") from exc
        if not 1 <= days <= 365:
            raise ValueError("'days' must be a whole number between 1 and 365.")
        self._send_json(build_progress_report(self.server.kg_dir, days=days))

    def _handle_readiness_get(self, query: dict[str, list[str]]) -> None:
        from syntopik.readiness import build_premortem_shield, compute_readiness

        exam_date = ((query.get("exam_date") or query.get("examDate") or [""])[0]).strip() or None
        raw_days = ((query.get("days") or [""])[0]).strip()
        if exam_date is None and raw_days:
            try:
                days_f = float(raw_days)
                if not math.isfinite(days_f) or days_f < 0 or days_f > 3650:
                    raise ValueError("'days' must be between 0 and 3650.")
            except ValueError as exc:
                raise ValueError("'days' must be a valid non-negative number.") from exc
            target_dt = (datetime.now(timezone.utc) + timedelta(days=days_f)).replace(microsecond=0)
            exam_date = target_dt.isoformat()

        subject = ((query.get("subject") or [""])[0]).strip() or None
        raw_max = ((query.get("max_marks") or query.get("maxMarks") or [""])[0]).strip()
        max_marks: float | None = None
        if raw_max:
            try:
                mm = float(raw_max)
                if not math.isfinite(mm) or mm <= 0:
                    raise ValueError("'max_marks' must be > 0.")
                max_marks = mm
            except ValueError as exc:
                raise ValueError("'max_marks' must be a positive number.") from exc

        premortem_only = ((query.get("premortem_only") or query.get("premortemOnly") or ["0"])[0]).strip().lower() in (
            "1", "true", "yes",
        )
        if premortem_only:
            chapter = ((query.get("chapter") or [""])[0]).strip() or None
            shield = build_premortem_shield(
                self.server.workspace,
                subject=subject,
                chapters=[chapter] if chapter else None,
                kg_dir=self.server.kg_dir,
            )
            self._send_json(_sanitize_premortem_shield(shield, self.server.kg_dir))
            return

        res = compute_readiness(
            self.server.workspace,
            exam_date=exam_date,
            subject=subject,
            max_marks=max_marks,
            kg_dir=self.server.kg_dir,
            chapters_dir=self.server.chapters_dir,
        )
        if isinstance(res, dict) and "preMortemShield" in res:
            res = dict(res)
            res["preMortemShield"] = _sanitize_premortem_shield(res.get("preMortemShield"), self.server.kg_dir)
        src = str(res.get("examDateSource") or "default_14d")
        res["hasExamConfig"] = src != "default_14d"
        self._send_json(res)

    def _handle_interleave_get(self) -> None:
        st = _build_interleave_stats_payload(self.server.workspace, self.server.kg_dir)
        self._send_json(st)

    def _handle_interleave_build(self) -> None:
        body = self._read_json_body()
        if body is None:
            return
        from syntopik.interleave import build_interleave_set

        raw_n = body.get("n") if body.get("n") is not None else body.get("count")
        try:
            n = max(1, min(20, int(raw_n or 6)))
        except (TypeError, ValueError) as exc:
            raise ValueError("'n' must be an integer between 1 and 20.") from exc

        seed_raw = body.get("seed")
        seed = int(seed_raw) if isinstance(seed_raw, int) and not isinstance(seed_raw, bool) else None
        subjects = body.get("subjects") if "subjects" in body else body.get("subject")
        chapters = body.get("chapters") if "chapters" in body else body.get("chapter")
        mode = str(body.get("mode") or "principle_first").strip() or "principle_first"

        res = build_interleave_set(
            self.server.workspace,
            n=n,
            subjects=subjects,
            chapters=chapters,
            mode=mode,
            seed=seed,
            kg_dir=self.server.kg_dir,
        )
        safe_res = _sanitize_interleave_payload(res)
        self.server.notify_change("interleave_build")
        self._send_json({"ok": bool(safe_res.get("ok", True)), **safe_res})

    def _handle_interleave_check(self) -> None:
        body = self._read_json_body()
        if body is None:
            return
        session_id = str(body.get("sessionId") or body.get("session_id") or "").strip()
        item_id = str(body.get("itemId") or body.get("item_id") or "").strip()
        selected = str(
            body.get("selectedPrinciple")
            or body.get("selected_principle")
            or body.get("chosenOptionId")
            or body.get("optionId")
            or ""
        ).strip()
        if not session_id or not item_id or not selected:
            raise ValueError("'sessionId', 'itemId', and 'selectedPrinciple' are required.")

        conf_raw = body.get("confidence", 0.5)
        try:
            conf_f = float(conf_raw) if conf_raw is not None else 0.5
        except (TypeError, ValueError):
            conf_f = 0.5
        dur_raw = body.get("durationMs") if body.get("durationMs") is not None else body.get("latencyMs")
        dur_ms = int(dur_raw) if isinstance(dur_raw, (int, float)) and not isinstance(dur_raw, bool) and dur_raw > 0 else None

        from syntopik.interleave import check_principle_step

        res = check_principle_step(
            self.server.workspace,
            session_id=session_id,
            item_id=item_id,
            selected_principle=selected,
            confidence=conf_f,
            duration_ms=dur_ms,
            kg_dir=self.server.kg_dir,
        )
        if not res.get("ok"):
            err_msg = str(res.get("error") or "Interleave check failed.")
            if "not found" in err_msg.lower():
                raise FileNotFoundError(err_msg)
            raise ValueError(err_msg)
        # Keep sealedAnswer hidden on Step-1 check
        res.pop("sealedAnswer", None)
        self.server.ensure_synced_graph(force=True)
        self.server.notify_change("interleave_check")
        self._send_json(res)

    def _handle_interleave_complete(self) -> None:
        body = self._read_json_body()
        if body is None:
            return
        session_id = str(body.get("sessionId") or body.get("session_id") or "").strip()
        if not session_id:
            raise ValueError("'sessionId' is required.")
        results = body.get("results")

        from syntopik.interleave import complete_interleave_session

        res = complete_interleave_session(
            self.server.workspace,
            session_id=session_id,
            results=results,
            kg_dir=self.server.kg_dir,
        )
        if not res.get("ok"):
            err_msg = str(res.get("error") or "Interleave completion failed.")
            if "not found" in err_msg.lower():
                raise FileNotFoundError(err_msg)
            raise ValueError(err_msg)
        self.server.ensure_synced_graph(force=True)
        self.server.notify_change("interleave_complete")
        self._send_json(res)


class _SyntopikStudioServerIPv6(SyntopikStudioServer):
    address_family = socket.AF_INET6


def create_server(
    workspace: Path | str | None = None,
    host: str = "127.0.0.1",
    port: int = 4173,
    lan: bool = False,
) -> SyntopikStudioServer:
    """Create and bind a `SyntopikStudioServer` (auto-incrementing port on EADDRINUSE if port != 0)."""
    ws = resolve_workspace(workspace)
    pkg_root = resolve_package_root()

    bind_host = str(host or "127.0.0.1").strip().strip("[]")
    if lan:
        if bind_host in ("127.0.0.1", "localhost", ""):
            bind_host = "0.0.0.0"
    else:
        if not _is_loopback_ip(bind_host):
            raise ValueError(f"Non-loopback host {bind_host!r} requires lan=True.")

    server_cls: type[SyntopikStudioServer] = SyntopikStudioServer
    try:
        if ipaddress.ip_address(bind_host).version == 6:
            server_cls = _SyntopikStudioServerIPv6
    except ValueError:
        pass

    # Ensure learner workspace is initialized non-destructively before serving
    try:
        setup = _load_script_module("setup_harness", ws, pkg_root)
        setup.ensure_learner_workspace(ws, run_sync=True, repair_skills=False)
    except Exception:
        (ws / "knowledge-graph").mkdir(parents=True, exist_ok=True)

    base_port = int(port)
    if base_port == 0:
        return server_cls((bind_host, 0), workspace=ws, package_root=pkg_root, lan=lan)

    last_err: OSError | None = None
    for offset in range(50):
        cand_port = base_port + offset
        try:
            return server_cls(
                (bind_host, cand_port),
                workspace=ws,
                package_root=pkg_root,
                lan=lan,
            )
        except OSError as exc:
            if exc.errno == errno.EADDRINUSE:
                last_err = exc
                continue
            raise
    raise OSError(errno.EADDRINUSE, f"No free port found in range {base_port}..{base_port + 49}") from last_err


def serve_forever(
    workspace: Path | str | None = None,
    host: str = "127.0.0.1",
    port: int = 4173,
    lan: bool = False,
    open_browser: bool = False,
) -> int:
    """Run the 2-way local Studio server until interrupted."""
    server = create_server(workspace=workspace, host=host, port=port, lan=lan)
    print(f"Syntopik Studio v{__version__} listening on {server.local_url}")
    print(f"  Workspace : {server.workspace}")
    print(f"  Pocket    : {server.local_url}/pocket")
    if not server.lan:
        print(f"  MCP HTTP  : {server.mcp_http_endpoint}  (loopback + token)")
    if server.lan and server.lan_url:
        print(f"  LAN Pair  : {server.lan_url}  (one-time token, scoped ONLY to Pocket)")
        try:
            from syntopik.pocket import render_qr_ascii

            print(render_qr_ascii(server.lan_url))
        except Exception:
            pass
    print("Press Ctrl+C to stop.")
    sys.stdout.flush()

    if open_browser:
        try:
            webbrowser.open(server.local_url)
        except Exception:
            pass

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        server.server_close()
    return 0
