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

import errno
import hmac
import importlib
import ipaddress
import json
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
from datetime import date, datetime, timezone
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
    write_text_atomic,
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
MAX_UPLOAD_BODY_BYTES = 32 * 1024 * 1024  # 32 MiB ceiling for PDF upload

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
        "schemaVersion": 4,
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

        self._change_cond = threading.Condition()
        self._change_seq = 0
        self._last_change_reason = "init"
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

    def notify_change(self, reason: str = "mutation") -> None:
        with self._change_cond:
            self._change_seq += 1
            self._last_change_reason = reason
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
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
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

    def _read_json_body(self) -> dict[str, Any] | None:
        try:
            length = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            self._send_error_json(HTTPStatus.BAD_REQUEST, "Invalid Content-Length header")
            return None
        if length < 0 or length > MAX_JSON_BODY_BYTES:
            self._send_error_json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "Request body exceeds limit")
            return None
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        try:
            parsed = json.loads(raw.decode("utf-8"))
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
                    return True, True
                self._send_error_json(HTTPStatus.FORBIDDEN, "Missing or invalid X-Syntopik-Token")
                return False, False
            return True, False

        # Non-loopback client (or non-loopback Host) on the 3 Pocket LAN routes
        if not has_valid_cookie:
            self._send_error_json(HTTPStatus.FORBIDDEN, "Pocket LAN pairing required")
            return False, True
        if method in ("POST", "PATCH") and origin_hdr:
            try:
                origin_netloc = urllib.parse.urlparse(origin_hdr.strip()).netloc.lower()
            except ValueError:
                origin_netloc = ""
            if not host_hdr or origin_netloc != host_hdr.strip().lower():
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

    def do_GET(self) -> None:
        parsed = urllib.parse.urlsplit(self.path)
        path = parsed.path or "/"
        query = urllib.parse.parse_qs(parsed.query)

        allowed, is_lan_client = self._check_access("GET", path, query)
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
            self._handle_pocket_html(query, inject_csrf=not is_lan_client)
            return

        self._handle_static(path)

    def do_POST(self) -> None:
        parsed = urllib.parse.urlsplit(self.path)
        path = parsed.path or "/"
        query = urllib.parse.parse_qs(parsed.query)

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
            "/api/pack/export": self._handle_pack_export,
            "/api/pack/import": self._handle_pack_import,
            "/api/triage/plan": self._handle_triage_plan,
            "/api/triage/crib-sheet": self._handle_triage_crib_sheet,
            "/api/forks/check": self._handle_forks_check,
            "/api/arena/submit": self._handle_arena_submit,
            "/api/blitz/grade": self._handle_blitz_grade,
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
        self._send_json({
            "live": True,
            "token": self.server.csrf_token,
            "version": __version__,
            "workspaceName": self.server.workspace.name,
            "lan": self.server.lan,
        })

    def _handle_pocket_html(self, query: dict[str, list[str]], *, inject_csrf: bool) -> None:
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

        # Enrich with v2.0 engines (forksDue, arenasCount, reentry, triageRecommended)
        forks_due_items: list[dict[str, Any]] = []
        forks_due_count = 0
        try:
            from syntopik.forks import list_due_forks

            f_res = list_due_forks(workspace=self.server.workspace, kg_dir=self.server.kg_dir)
            forks_due_items = list(f_res.get("items") or [])
            forks_due_count = int(f_res.get("dueCount") or len(forks_due_items))
        except Exception:
            pass

        arenas_count = 0
        try:
            arenas_rows = load_jsonl(self.server.kg_dir / "arenas.jsonl")
            arenas_count = len(arenas_rows)
        except Exception:
            pass

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

        # Mirror enriched v2 fields inside hub as well as top-level for universal consumer compatibility
        hub_out = dict(hub)
        hub_out.setdefault("retentionHeatmap", {"windowDays": 365, "totalEvents": 0, "activeDays": 0, "byDate": {}, "days": []})
        hub_out.setdefault("calibration", {"sampleCount": 0, "totalReviews": 0, "brierScore": None})
        hub_out.setdefault("leeches", [])
        hub_out.setdefault("readingQueue", [])
        hub_out["forksDue"] = forks_due_items
        hub_out["forksDueCount"] = forks_due_count
        hub_out["arenasCount"] = arenas_count
        hub_out["reentry"] = reentry
        hub_out["triageRecommended"] = triage_recommended

        self._send_json({
            "ok": True,
            "graph": graph,
            "hub": hub_out,
            "forksDue": forks_due_items,
            "forksDueCount": forks_due_count,
            "arenasCount": arenas_count,
            "reentry": reentry,
            "triageRecommended": triage_recommended,
            "energyMode": energy,
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

            if self.server._shutdown_event.is_set():
                break

            cur_sig = _compute_workspace_signature(
                self.server.workspace, self.server.kg_dir, self.server.chapters_dir
            )
            if cur_seq != last_seq or cur_sig != last_sig:
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
        body = self._read_json_body()
        if body is None:
            return
        target = str(body.get("target") or body.get("url") or "").strip()
        if not target:
            raise ValueError("'target' is required.")

        from syntopik import ingest

        kind = str(body.get("kind") or "auto").strip().lower()
        slug = body.get("slug")
        subject = str(body.get("subject") or "General")
        register_tracker = bool(body.get("registerTracker", False))

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
        else:
            if body.get("htmlText") is not None:
                common_kwargs["html_text"] = body["htmlText"]
            if body.get("vttText") is not None:
                common_kwargs["vtt_text"] = body["vttText"]
            if body.get("atomXml") is not None:
                common_kwargs["atom_xml"] = body["atomXml"]
            res = ingest.ingest_auto(target, **common_kwargs)

        self.server.ensure_synced_graph(force=True)
        self.server.notify_change("import")
        self._send_json({"ok": True, **res})

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
        pack_raw = str(body.get("packPath") or "").strip()
        if not pack_raw:
            raise ValueError("'packPath' is required.")
        pack_path = self._resolve_user_path_jailed(pack_raw, allow_temp=True)
        if pack_path is None:
            raise PermissionError("packPath must be within workspace or temp directory")
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
        )
        if not body.get("dryRun"):
            self.server.ensure_synced_graph(force=True)
            self.server.notify_change("pack_import")
        self._send_json({"ok": True, **res})

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
                proc = subprocess.run(
                    [typst_bin, "compile", "--root", str(stage_dir), str(typ_file), str(out_pdf)],
                    capture_output=True,
                    text=True,
                    check=False,
                )
            if proc.returncode == 0 and out_pdf.is_file():
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
        self.server.notify_change("arena_submit")
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
        self.server.notify_change("blitz_grade")
        self._send_json({"ok": True, **res})


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
