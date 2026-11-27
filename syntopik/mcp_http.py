"""Syntopik v3.2 — Pure-Stdlib Loopback Streamable-HTTP MCP JSON-RPC 2.0 Dispatcher.

Zero Node.js, zero external dependencies (pure Python 3.12+ stdlib).
Implements MCP Streamable HTTP JSON-RPC 2.0 (`MCP_PROTOCOL_VERSION = "2025-03-26"`,
negotiating `"2025-11-25"`, `"2025-06-18"`, `"2025-03-26"`, `"2024-11-05"`) exposing
7 ambient headless tools directly against the Syntopik workspace:

  1. `synt_next_move`       — `syntopik.brainstem.plan_next_move`
  2. `synt_get_frontier`    — compact FIRe frontier + learnerProfile + teachingPolicy
                              + rosettaBridgesCount + readinessSummary
  3. `synt_capture_concept` — lexical dedup + atomic proposition/chain persistence
  4. `synt_log_review`      — FSRS-6 + True FIRe + revlog + Tier-3 telemetry
  5. `synt_whiteboard_step` — push frame / resolve pins (locked gates sanitized!)
  6. `synt_readiness`       — lazy `syntopik.readiness` forecast & pre-mortem shield
  7. `synt_interleave`      — lazy `syntopik.interleave` blind-chapter drills (zero sealed leak!)

Security Non-Negotiables:
  - Strictly rejects non-finite JSON values (`NaN`, `Infinity`, `-Infinity`) on input
    AND serializes all responses with `allow_nan=False`.
  - Strips all sealed answers from locked `prediction_gate` frames and blind
    interleave items before returning MCP tool payloads.
  - Designed to be mounted strictly on loopback (`127.0.0.1`) behind `X-Syntopik-Token`
    / `Authorization: Bearer` auth and disabled when `--lan` is active.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import importlib
import json
import math
from pathlib import Path
import re
import sys
from typing import Any, Callable
import unicodedata

from syntopik import __version__
from syntopik.fsrs_core import (
    card_retrievability,
    compress_due_queue,
    compute_calibration,
    detect_leeches,
    load_fsrs_parameters,
)
from syntopik.learner_model import compile_teaching_policy, load_profile
from syntopik.paths import (
    resolve_chapters_dir,
    resolve_kg_dir,
    resolve_package_root,
    resolve_workspace,
)
from syntopik.schemas import normalize_chain, now_iso, slugify, today_iso
from syntopik.storage import (
    load_json,
    load_jsonl,
    save_jsonl_atomic,
    workspace_lock,
)
from syntopik.whiteboard import VALID_FRAME_KINDS, get_session, push_frame, resolve_pin

__all__ = [
    "MCP_PROTOCOL_VERSION",
    "SUPPORTED_PROTOCOL_VERSIONS",
    "MAX_MCP_BODY_BYTES",
    "JSONRPC_PARSE_ERROR",
    "JSONRPC_INVALID_REQUEST",
    "JSONRPC_METHOD_NOT_FOUND",
    "JSONRPC_INVALID_PARAMS",
    "JSONRPC_INTERNAL_ERROR",
    "list_http_mcp_tools",
    "parse_mcp_json_bytes",
    "handle_mcp_http_request",
]

MCP_PROTOCOL_VERSION = "2025-03-26"
SUPPORTED_PROTOCOL_VERSIONS: tuple[str, ...] = (
    "2025-11-25",
    "2025-06-18",
    "2025-03-26",
    "2024-11-05",
)
MAX_MCP_BODY_BYTES = 1 * 1024 * 1024  # 1 MiB
MAX_BATCH_SIZE = 32

JSONRPC_PARSE_ERROR = -32700
JSONRPC_INVALID_REQUEST = -32600
JSONRPC_METHOD_NOT_FOUND = -32601
JSONRPC_INVALID_PARAMS = -32602
JSONRPC_INTERNAL_ERROR = -32603

_VALID_RATINGS: frozenset[str] = frozenset({"again", "hard", "good", "easy"})
_VALID_ENERGIES: frozenset[str] = frozenset({"low", "normal", "deep"})
_SEALED_GATE_FIELDS: frozenset[str] = frozenset(
    {"canonicalAnswer", "expectedAnswer", "revealedBody", "sealedAnswer", "answer"}
)
_SEALED_INTERLEAVE_FIELDS: frozenset[str] = frozenset(
    {
        "sealedChapter",
        "sealedPrinciple",
        "sealedAnswer",
        "canonicalAnswer",
        "correctPrinciple",
        "trueChapter",
        "expectedAnswer",
        "modelSolution",
        "solution",
        "answer",
        "chapter",
        "chapterSlug",
    }
)
_STOP_WORDS: frozenset[str] = frozenset(
    (
        "a an the and or of to in on at by for from with as is are was were be been being "
        "it its this that these those which who whose their them they we our you your "
        "can could may might must will would should has have had does do did"
    ).split()
)


# ---------------------------------------------------------------------------
# Strict JSON / Finite-Number Validation & JSON-RPC Envelope Builders
# ---------------------------------------------------------------------------


def _reject_non_finite(token: str) -> Any:
    raise ValueError(f"Non-finite JSON constant rejected: {token!r}")


def _contains_non_finite(obj: Any) -> bool:
    if isinstance(obj, float):
        return not math.isfinite(obj)
    if isinstance(obj, dict):
        return any(_contains_non_finite(k) or _contains_non_finite(v) for k, v in obj.items())
    if isinstance(obj, (list, tuple)):
        return any(_contains_non_finite(x) for x in obj)
    return False


def _strict_dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, allow_nan=False)


def parse_mcp_json_bytes(raw_bytes: bytes | bytearray | str) -> Any:
    """Parse JSON strictly rejecting non-finite constants (`NaN`, `Infinity`, `-Infinity`)."""
    if isinstance(raw_bytes, (bytes, bytearray)):
        if len(raw_bytes) > MAX_MCP_BODY_BYTES:
            raise ValueError(f"MCP request body exceeds {MAX_MCP_BODY_BYTES} byte limit.")
        text = raw_bytes.decode("utf-8")
    else:
        text = str(raw_bytes)
        if len(text.encode("utf-8")) > MAX_MCP_BODY_BYTES:
            raise ValueError(f"MCP request body exceeds {MAX_MCP_BODY_BYTES} byte limit.")
    return json.loads(text, parse_constant=_reject_non_finite)


def _rpc_result(req_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _rpc_error(req_id: Any, code: int, message: str, data: Any = None) -> dict[str, Any]:
    err: dict[str, Any] = {"code": int(code), "message": str(message)}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": req_id, "error": err}


def _tool_ok(structured: dict[str, Any]) -> dict[str, Any]:
    text = _strict_dumps(structured)
    return {
        "content": [{"type": "text", "text": text}],
        "structuredContent": structured,
        "isError": False,
    }


def _tool_err(message: str) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": str(message)}],
        "isError": True,
    }


class _InvalidParamsError(ValueError):
    """Raised when JSON-RPC tool arguments fail schema/type validation (-32602)."""


# ---------------------------------------------------------------------------
# Tool Definitions (Exact JSON Schemas matching `syntopik-studio` + v3.2)
# ---------------------------------------------------------------------------


_HTTP_MCP_TOOLS: tuple[dict[str, Any], ...] = (
    {
        "name": "synt_next_move",
        "title": "Syntopik Pedagogical Brainstem (Next Optimal Move)",
        "description": (
            "Return the compact <=180-token Action Envelope fusing FSRS-6/FIRe, "
            "Learner Psychology TeachingPolicy, causal root-cause probes, Blunder Forks, "
            "Rosetta isomorphicBridge openers, taper mode, and live whiteboard learner pins."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "energy": {
                    "type": "string",
                    "enum": ["low", "normal", "deep"],
                    "description": "Optional cognitive energy mode.",
                },
                "domain": {
                    "type": "string",
                    "description": "Optional subject/domain focus.",
                },
                "event": {
                    "oneOf": [{"type": "string"}, {"type": "object"}],
                    "description": "Optional recent event context (e.g. 'skip_calibration' or {kind:'miss', propId:'...'}).",
                },
            },
        },
    },
    {
        "name": "synt_get_frontier",
        "title": "Get Syntopik Study Frontier & Learner Policy",
        "description": (
            "Ambient headless query: return FIRe-compressed due items, active Autopilot, "
            "learnerProfile, Active TeachingPolicy, dueForks, arenasCount, coldStartDays, "
            "pendingPins, rosettaBridgesCount, syncDeviceId, readinessSummary, traps, and Brier calibration."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Max due items to return (default 10, max 50).",
                },
                "compress": {
                    "type": "boolean",
                    "description": "Enable FIRe Spaced Repetition Compression knockouts (default true).",
                },
            },
        },
    },
    {
        "name": "synt_capture_concept",
        "title": "Capture Concept to Syntopik Knowledge Graph",
        "description": (
            "Ambient headless capture: deduplicate via lexical similarity, save a verified "
            "proposition (plus optional recall cue and prerequisite hyperedge chain) to the Knowledge Graph."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Concise concept or invariant title."},
                "statement": {"type": "string", "description": "Caveat-free unconditional truth or mechanism statement."},
                "subject": {"type": "string", "description": "Subject area (e.g., CS, ML, Physics, Maths, Chemistry)."},
                "chapter": {"type": "string", "description": "Chapter, course, paper, or project context."},
                "cuePrompt": {"type": "string", "description": "Optional active recall question cue."},
                "cueAnswer": {"type": "string", "description": "Optional concise answer for cuePrompt."},
                "premises": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional prerequisite proposition IDs forming a hyperedge into this concept.",
                },
                "relation": {"type": "string", "description": "Optional chain relation (default: requires)."},
                "notes": {"type": "string", "description": "Optional concise notes."},
                "kind": {"type": "string", "description": "Optional proposition kind (default: concept)."},
                "id": {"type": "string", "description": "Optional explicit kebab-case proposition ID."},
            },
            "required": ["title", "statement", "subject", "chapter"],
        },
    },
    {
        "name": "synt_log_review",
        "title": "Log FSRS-6 + FIRe Concept Review",
        "description": (
            "Ambient headless review: run FSRS-6 review with True FIRe propagation, "
            "optional hintsUsed (0..5) & durationMs for silent Tier-3 learner psychology updates, "
            "and revlog.jsonl persistence."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "propId": {"type": "string", "description": "Proposition ID to review."},
                "rating": {"type": "string", "enum": ["again", "hard", "good", "easy"]},
                "confidence": {"type": "number", "description": "Optional learner confidence (0..1 or 0..100)."},
                "cueId": {"type": "string", "description": "Optional specific cue ID reviewed."},
                "misconception": {"type": "boolean", "description": "Flag as active misconception when rating is 'again'."},
                "durationMs": {"type": "number", "description": "Optional response duration in milliseconds."},
                "hintsUsed": {"type": "integer", "description": "Optional hints used (0..5) for silent Tier-3 adaptation."},
            },
            "required": ["propId", "rating"],
        },
    },
    {
        "name": "synt_whiteboard_step",
        "title": "Push Live AI Whiteboard Frame",
        "description": (
            "Push a progressive frame (derivation_step, prediction_gate, explore_widget, "
            "diagram_callout, or summary) to knowledge-graph/whiteboard-session.json and "
            "return any unresolved learner pendingPins (locked prediction gates strip sealed answers)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "enum": ["derivation_step", "prediction_gate", "explore_widget", "diagram_callout", "summary"],
                    "description": "Whiteboard frame kind (default: derivation_step).",
                },
                "title": {"type": "string", "description": "Concise frame title."},
                "body": {"type": "string", "description": "Concise Markdown body (locked when kind='prediction_gate')."},
                "latex": {"type": "string", "description": "Optional display LaTeX equation."},
                "propId": {"type": "string", "description": "Optional linked proposition ID."},
                "stepIndex": {"type": "integer", "description": "Optional 0-based step index."},
                "locked": {"type": "boolean", "description": "Lock frame behind prediction gate (default true for prediction_gate)."},
                "prompt": {"type": "string", "description": "Prompt shown on locked prediction_gate frames."},
                "revealedBody": {"type": "string", "description": "Body revealed once prediction_gate is unlocked."},
                "expectedAnswer": {"type": "string", "description": "Optional expected prediction for gate comparison."},
                "payload": {"type": "object", "description": "Optional structured payload (e.g., :::explore lab spec)."},
                "sessionTitle": {"type": "string", "description": "Optional whiteboard session heading."},
                "id": {"type": "string", "description": "Optional explicit frame ID (updates frame in-place if repeated)."},
                "resolvePinId": {"type": "string", "description": "Optional learner pin ID resolved by this step."},
                "resolution": {"type": "string", "description": "Optional short resolution note for resolvePinId."},
            },
            "required": ["title"],
        },
    },
    {
        "name": "synt_readiness",
        "title": "Exam-Day Readiness Forecaster & Pre-Mortem Shield",
        "description": (
            "Project deterministic FSRS-6 retention at exam date, fuse 4-tier Evidence coverage, "
            "unresolved Blunder Forks, EV-skip discipline, top-3 ROI levers, <=72h taper mode, "
            "and non-fabricated 3-bullet Pre-Mortem Shield."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "examDate": {"type": "string", "description": "Optional target exam date (YYYY-MM-DD)."},
                "subject": {"type": "string", "description": "Optional subject filter."},
                "chapter": {"type": "string", "description": "Optional chapter filter."},
                "maxMarks": {"type": "number", "description": "Optional maximum exam marks for score projection."},
                "premortemOnly": {"type": "boolean", "description": "Return only the 3-bullet Pre-Mortem Shield."},
            },
        },
    },
    {
        "name": "synt_interleave",
        "title": "Blind-Chapter Interleaved Drill Generator & Step-1 Grader",
        "description": (
            "Generate Rohrer blind-chapter interleaved drills (chapter labels & canonical answers sealed!) "
            "or grade Step-1 governing-principle identification cleanly without leaking sealed answers."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["build", "check", "stats"],
                    "description": "Operation to run (default: build).",
                },
                "n": {"type": "integer", "description": "Number of interleaved items to generate (default: 6)."},
                "count": {"type": "integer", "description": "Alias for n."},
                "subjects": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional list of subjects to interleave.",
                },
                "subject": {"type": "string", "description": "Optional single subject filter."},
                "mode": {"type": "string", "description": "Drill mode (default: principle_first)."},
                "seed": {"type": "integer", "description": "Optional deterministic RNG seed."},
                "sessionId": {"type": "string", "description": "Session ID (required for action='check')."},
                "itemId": {"type": "string", "description": "Item ID (required for action='check')."},
                "selectedPrinciple": {"type": "string", "description": "Learner's selected principle (required for action='check')."},
                "confidence": {"type": "number", "description": "Learner confidence (0..1 or 0..100)."},
                "durationMs": {"type": "number", "description": "Response duration in milliseconds."},
            },
        },
    },
)


def list_http_mcp_tools() -> list[dict[str, Any]]:
    """Return deep-copied JSON-schema definitions for all 7 headless HTTP MCP tools."""
    return json.loads(_strict_dumps(list(_HTTP_MCP_TOOLS)))


# ---------------------------------------------------------------------------
# Internal Helpers (Zero-Leak Sanitizers, Lexical Dedup, Frontier Builder)
# ---------------------------------------------------------------------------


def _load_script_module(module_name: str, workspace: Path) -> Any:
    pkg_root = resolve_package_root()
    for base in (workspace / "scripts", pkg_root / "scripts"):
        if base.is_dir():
            s = str(base)
            if s not in sys.path:
                sys.path.insert(0, s)
    root_s = str(pkg_root)
    if root_s not in sys.path:
        sys.path.insert(0, root_s)
    return importlib.import_module(module_name)


def _sanitize_whiteboard_frame(frame: dict[str, Any]) -> dict[str, Any]:
    """Strip sealed expectedAnswer / canonicalAnswer / revealedBody from locked prediction_gate frames."""
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
    if gate is not None:
        fr["gate"] = gate
    fr["payload"] = payload
    return fr


def _sanitize_interleave_item(item: Any) -> Any:
    """Defense-in-depth sanitizer removing ALL sealed chapter/principle/answer keys from blind items."""
    if not isinstance(item, dict):
        return item
    clean: dict[str, Any] = {}
    for k, v in item.items():
        if k in _SEALED_INTERLEAVE_FIELDS or str(k).lower().startswith("sealed"):
            continue
        if isinstance(v, dict):
            clean[k] = _sanitize_interleave_item(v)
        elif isinstance(v, list):
            clean[k] = [_sanitize_interleave_item(elem) for elem in v]
        else:
            clean[k] = v
    return clean


def _sanitize_interleave_build(res: dict[str, Any]) -> dict[str, Any]:
    out = dict(res)
    for key in list(out.keys()):
        if key in _SEALED_INTERLEAVE_FIELDS or str(key).lower().startswith("sealed"):
            out.pop(key, None)
    if isinstance(out.get("items"), list):
        out["items"] = [_sanitize_interleave_item(it) for it in out["items"]]
    return out


def _tokenize_lex(text: str) -> set[str]:
    norm = unicodedata.normalize("NFKC", str(text or "")).lower()
    raw = re.findall(r"[^\W_]+", norm, flags=re.UNICODE)
    out: set[str] = set()
    for tok in raw:
        if tok in _STOP_WORDS or (len(tok) == 1 and tok.isascii() and tok.isalpha()):
            continue
        stem = tok[:-1] if len(tok) > 4 and tok.endswith("s") else tok
        out.add(stem)
    return out


def _lexical_similarity(a: str, b: str) -> float:
    ta = _tokenize_lex(a)
    tb = _tokenize_lex(b)
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    if inter == 0:
        return 0.0
    return round((2.0 * inter) / (len(ta) + len(tb)), 4)


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


def _count_rosetta_bridges(chains: list[dict[str, Any]]) -> int:
    seen: set[str] = set()
    for raw in chains:
        norm = normalize_chain(raw)
        if not norm or str(norm.get("relation") or "").lower() != "isomorphic_to":
            continue
        concl = norm["conclusion"]
        arch = str(norm.get("archetypeId") or "").lower()
        for pr in norm["premises"]:
            a, b = (pr, concl) if pr < concl else (concl, pr)
            seen.add(f"{a}<->{b}:{arch}")
    return len(seen)


def _load_active_autopilot(chapters_dir: Path) -> dict[str, Any] | None:
    if not chapters_dir.is_dir():
        return None
    trackers: list[dict[str, Any]] = []
    for fp in sorted(chapters_dir.glob("*-tracker.json")):
        raw = load_json(fp, default=None)
        if isinstance(raw, dict) and raw.get("slug"):
            trackers.append(raw)
    if not trackers:
        return None
    active = next((t for t in trackers if t.get("active") is True), None)
    if active is None:
        return None
    topics = [t for t in (active.get("topics") or []) if isinstance(t, dict)]
    questions = [q for q in (active.get("questions") or []) if isinstance(q, dict)]
    t_mastered = sum(1 for t in topics if t.get("status") == "mastered")
    q_solved = sum(1 for q in questions if q.get("status") in ("solved", "mastered"))
    q_skipped = sum(1 for q in questions if q.get("status") == "skipped")
    denom = len(topics) + max(0, len(questions) - q_skipped)
    pct = max(0, min(100, round(((t_mastered + q_solved) / denom) * 100))) if denom > 0 else 0
    return {
        "slug": str(active.get("slug") or ""),
        "title": str(active.get("title") or active.get("slug") or ""),
        "subject": str(active.get("subject") or "General"),
        "pct": pct,
        "topicsMastered": t_mastered,
        "topicsTotal": len(topics),
        "questionsSolved": q_solved,
        "questionsTotal": len(questions),
    }


def _compute_cold_start_days(
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


# ---------------------------------------------------------------------------
# 7 Pure-Python Ambient Tool Handlers
# ---------------------------------------------------------------------------


def _require_str(args: dict[str, Any], key: str, *, allow_empty: bool = False) -> str:
    val = args.get(key)
    if not isinstance(val, str):
        raise _InvalidParamsError(f"Parameter '{key}' must be a string.")
    clean = val.strip()
    if not allow_empty and not clean:
        raise _InvalidParamsError(f"Parameter '{key}' must be a non-empty string.")
    return clean


def _opt_str(args: dict[str, Any], key: str) -> str | None:
    if key not in args or args[key] is None:
        return None
    val = args[key]
    if not isinstance(val, str):
        raise _InvalidParamsError(f"Parameter '{key}' must be a string.")
    clean = val.strip()
    return clean or None


def _opt_int(args: dict[str, Any], key: str, *, default: int | None = None) -> int | None:
    if key not in args or args[key] is None:
        return default
    val = args[key]
    if isinstance(val, bool) or not isinstance(val, int):
        raise _InvalidParamsError(f"Parameter '{key}' must be an integer.")
    return val


def _opt_num(args: dict[str, Any], key: str) -> float | None:
    if key not in args or args[key] is None:
        return None
    val = args[key]
    if isinstance(val, bool) or not isinstance(val, (int, float)):
        raise _InvalidParamsError(f"Parameter '{key}' must be a number.")
    fval = float(val)
    if not math.isfinite(fval):
        raise _InvalidParamsError(f"Parameter '{key}' must be a finite number.")
    return fval


def _opt_bool(args: dict[str, Any], key: str, *, default: bool | None = None) -> bool | None:
    if key not in args or args[key] is None:
        return default
    val = args[key]
    if not isinstance(val, bool):
        raise _InvalidParamsError(f"Parameter '{key}' must be a boolean.")
    return val


def _handle_next_move(ws: Path, args: dict[str, Any]) -> dict[str, Any]:
    energy = _opt_str(args, "energy")
    if energy is not None and energy.lower() not in _VALID_ENERGIES:
        raise _InvalidParamsError(f"Invalid 'energy' {energy!r}; expected one of: low, normal, deep.")
    domain = _opt_str(args, "domain")
    event = args.get("event")
    if event is not None and not isinstance(event, (str, dict)):
        raise _InvalidParamsError("Parameter 'event' must be a string or object.")

    from syntopik.brainstem import plan_next_move

    res = plan_next_move(
        ws,
        event=event,
        energy=energy.lower() if energy else None,
        domain=domain,
    )
    if not isinstance(res, dict) or not res.get("ok"):
        return _tool_err(str((res or {}).get("error") or "plan_next_move failed."))
    return _tool_ok({"kind": "synt_next_move", **res})


def _handle_get_frontier(ws: Path, args: dict[str, Any]) -> dict[str, Any]:
    raw_limit = _opt_int(args, "limit", default=10)
    clean_limit = max(1, min(50, int(raw_limit if raw_limit is not None else 10)))
    use_compress = bool(_opt_bool(args, "compress", default=True))

    kg_dir = resolve_kg_dir(ws)
    chapters_dir = resolve_chapters_dir(ws)
    now_dt = datetime.now(timezone.utc)
    today = today_iso()

    props = load_jsonl(kg_dir / "propositions.jsonl")
    chains = load_jsonl(kg_dir / "chains.jsonl")
    mistakes = load_jsonl(kg_dir / "mistakes.jsonl")
    revlog = load_jsonl(kg_dir / "revlog.jsonl")
    reading_queue = load_jsonl(kg_dir / "reading-queue.jsonl")
    forks = load_jsonl(kg_dir / "forks.jsonl")
    arenas = load_jsonl(kg_dir / "arenas.jsonl")

    params = load_fsrs_parameters(kg_dir)
    raw_due: list[dict[str, Any]] = []
    for p in props:
        pid = str(p.get("id") or "").strip()
        if not pid or bool(p.get("archived")) or bool(p.get("suspended")):
            continue
        st = str(p.get("status") or "planned").lower()
        nr = str(p.get("nextReview") or "")[:10] if isinstance(p.get("nextReview"), str) else None
        is_due = st in ("misconception", "shaky") or (nr is not None and nr <= today)
        if not is_due:
            continue
        retr = 0.5
        if isinstance(p.get("fsrs"), dict):
            try:
                retr = round(float(card_retrievability(p["fsrs"], now_dt, parameters=params)), 3)
            except Exception:
                retr = 0.5
        raw_due.append(
            {
                "id": pid,
                "title": str(p.get("title") or pid),
                "subject": str(p.get("subject") or "General"),
                "chapter": str(p.get("chapter") or ""),
                "status": st,
                "bktMastery": p.get("bktMastery"),
                "retrievability": retr,
                "nextReview": nr,
            }
        )

    status_rank = {"misconception": 0, "shaky": 1}
    raw_due.sort(key=lambda d: (status_rank.get(d["status"], 2), float(d.get("retrievability", 0.5)), str(d.get("nextReview") or ""), d["id"]))

    due_count = len(raw_due)
    knocked_out_ids: list[str] = []
    if use_compress and raw_due:
        comp = compress_due_queue(raw_due, chains)
        due_items = list(comp.get("selected") or [])[:clean_limit]
        knocked_out_ids = list(comp.get("knockedOutIds") or [])
    else:
        due_items = raw_due[:clean_limit]

    autopilot = _load_active_autopilot(chapters_dir)
    eff_domain = autopilot["subject"] if autopilot else None
    profile = load_profile(kg_dir)
    teaching_policy = compile_teaching_policy(profile, domain=eff_domain)

    cat_counts = Counter(str(m.get("category") or "concept_gap") for m in mistakes)
    top_categories = [{"category": c, "count": n} for c, n in cat_counts.most_common(3)]
    top_traps = [
        {
            "id": m.get("id"),
            "category": m.get("category") or "concept_gap",
            "subject": m.get("subject") or "General",
            "chapter": m.get("chapter") or "",
            "triggerCue": m.get("triggerCue") or "",
            "wrongMove": m.get("wrongMove") or "",
            "correctMove": m.get("correctMove") or "",
        }
        for m in reversed(mistakes[-3:])
    ]

    due_forks = [
        {
            "id": str(f.get("id") or ""),
            "propId": f.get("propId"),
            "domain": f.get("domain") or f.get("subject") or "General",
            "chapter": f.get("chapter") or "",
            "status": f.get("status") or "due",
            "nextReview": f.get("nextReview"),
        }
        for f in forks
        if str(f.get("id") or "")
        and str(f.get("status") or "due").lower() not in ("archived", "mastered", "retired")
        and (
            not isinstance(f.get("nextReview"), str)
            or str(f.get("nextReview"))[:10] <= today
            or str(f.get("status") or "").lower() == "due"
        )
    ]
    open_arenas_count = sum(
        1 for a in arenas if str(a.get("status") or "open").lower() not in ("cleared", "passed", "archived")
    )

    wb_sess = get_session(kg_dir)
    pending_pins = list(wb_sess.get("pendingPins") or [])
    rosetta_count = _count_rosetta_bridges(chains)
    sync_state = load_json(kg_dir / "sync-state.json", default=None)
    sync_device_id = str(sync_state.get("deviceId") or "").strip() if isinstance(sync_state, dict) else None
    sync_device_id = sync_device_id or None

    readiness_summary: dict[str, Any] | None = None
    try:
        from syntopik import readiness as readiness_mod

        rd = readiness_mod.compute_readiness(ws, kg_dir=kg_dir, chapters_dir=chapters_dir)
        if isinstance(rd, dict) and rd.get("ok"):
            taper = rd.get("taperMode") if isinstance(rd.get("taperMode"), dict) else {}
            readiness_summary = {
                "readinessIndex": rd.get("readinessIndex"),
                "band": rd.get("band"),
                "taperActive": bool(taper.get("active")),
                "hoursRemaining": taper.get("hoursRemaining"),
            }
    except Exception:
        readiness_summary = None

    calibration = compute_calibration(revlog)
    leeches = detect_leeches(props, revlog)
    reading_due = sum(1 for r in reading_queue if str(r.get("stage") or "queued") != "crystallized")
    cold_start_days = _compute_cold_start_days(profile, revlog, mistakes, now_dt)

    compact_profile = {
        "calibrated": bool((profile.get("calibration") or {}).get("completed")),
        "globalElo": round(float((profile.get("globalStats") or {}).get("elo", 1450.0))),
        "totalEvents": int((profile.get("globalStats") or {}).get("totalEvents", 0)),
        "affectState": str((profile.get("affectiveState") or {}).get("state", "flow")),
        "userOverrides": dict(profile.get("userOverrides") or {}),
    }

    payload: dict[str, Any] = {
        "kind": "synt_frontier",
        "ok": True,
        "today": today,
        "compressed": use_compress,
        "dueCount": due_count,
        "dueItems": due_items,
        "knockedOutCount": len(knocked_out_ids),
        "knockedOutIds": knocked_out_ids,
        "autopilot": autopilot,
        "topMistakeTraps": top_traps,
        "topCategories": top_categories,
        "calibration": calibration,
        "leechesCount": len(leeches),
        "readingQueueDueCount": reading_due,
        "learnerProfile": compact_profile,
        "teachingPolicy": teaching_policy,
        "dueForks": due_forks[:5],
        "dueForksCount": len(due_forks),
        "arenasCount": len(arenas),
        "openArenasCount": open_arenas_count,
        "coldStartDays": cold_start_days,
        "pendingPins": pending_pins,
        "rosettaBridgesCount": rosetta_count,
        "readinessSummary": readiness_summary,
        "stats": {
            "totalPropositions": len(props),
            "totalChains": len(chains),
            "totalMistakes": len(mistakes),
            "totalReviews": len(revlog),
            "rosettaBridgesCount": rosetta_count,
        },
    }
    if sync_device_id:
        payload["syncDeviceId"] = sync_device_id
    return _tool_ok(payload)


def _handle_capture_concept(
    ws: Path,
    args: dict[str, Any],
    *,
    on_change: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    title = _require_str(args, "title")
    statement = _require_str(args, "statement")
    subject = _require_str(args, "subject")
    chapter = _require_str(args, "chapter")
    cue_q = _opt_str(args, "cuePrompt") or ""
    cue_a = _opt_str(args, "cueAnswer") or ""
    relation = _opt_str(args, "relation") or "requires"
    notes = _opt_str(args, "notes") or ""
    kind = _opt_str(args, "kind") or "concept"
    explicit_id = _opt_str(args, "id")

    raw_premises = args.get("premises")
    premises: list[str] = []
    if raw_premises is not None:
        if not isinstance(raw_premises, list) or not all(isinstance(x, str) for x in raw_premises):
            raise _InvalidParamsError("Parameter 'premises' must be an array of strings.")
        premises = list(dict.fromkeys(x.strip() for x in raw_premises if x.strip()))

    kg_dir = resolve_kg_dir(ws)
    props_file = kg_dir / "propositions.jsonl"
    chains_file = kg_dir / "chains.jsonl"
    today = today_iso()
    ts = now_iso()

    deduplicated = False
    matched_score: float | None = None
    cue_added = False
    chain_added = False

    with workspace_lock(props_file):
        props = load_jsonl(props_file)

        dup_target: dict[str, Any] | None = None
        if explicit_id:
            dup_target = next((p for p in props if str(p.get("id") or "") == explicit_id), None)
            if dup_target is not None:
                matched_score = 1.0

        if dup_target is None:
            best_prop: dict[str, Any] | None = None
            best_sc = 0.0
            for p in props:
                etitle = str(p.get("title") or "").strip()
                estmt = str(p.get("statement") or "").strip()
                if etitle.lower() == title.lower():
                    best_prop, best_sc = p, 1.0
                    break
                sc = max(
                    _lexical_similarity(title, etitle),
                    _lexical_similarity(f"{title} {statement}", f"{etitle} {estmt}"),
                )
                if sc > best_sc:
                    best_prop, best_sc = p, sc
            if best_prop is not None and best_sc >= 0.78:
                dup_target = best_prop
                matched_score = best_sc

        if dup_target is not None:
            deduplicated = True
            prop_id = str(dup_target["id"])
            if cue_q:
                cues = dup_target.get("cues")
                if not isinstance(cues, list):
                    cues = []
                    dup_target["cues"] = cues
                if not any(str(c.get("q") or "").strip().lower() == cue_q.lower() for c in cues if isinstance(c, dict)):
                    cues.append(
                        {
                            "id": f"c{len(cues) + 1}",
                            "q": cue_q,
                            "a": cue_a or statement,
                            "lastRating": None,
                            "reviews": 0,
                        }
                    )
                    cue_added = True
            prev_notes = str(dup_target.get("notes") or "")
            if statement and statement not in prev_notes:
                dup_target["notes"] = f"{prev_notes} | {statement}" if prev_notes else statement
            dup_target["updatedAt"] = ts
            save_jsonl_atomic(props_file, props)
        else:
            existing_ids = {str(p.get("id") or "") for p in props}
            base_id = (
                slugify(explicit_id, default="prop", max_len=64)
                if explicit_id
                else slugify(f"{subject[:14]}-{chapter[:18]}-{title[:28]}", default="prop", max_len=64)
            )
            prop_id = base_id
            suffix = 2
            while prop_id in existing_ids:
                prop_id = f"{base_id[:56]}-{suffix}"
                suffix += 1

            cues_list: list[dict[str, Any]] = []
            if cue_q or cue_a:
                cues_list.append(
                    {
                        "id": "c1",
                        "q": cue_q or f"State and justify: {title}",
                        "a": cue_a or statement,
                        "lastRating": None,
                        "reviews": 0,
                    }
                )
                cue_added = True

            new_prop: dict[str, Any] = {
                "id": prop_id,
                "subject": subject,
                "chapter": chapter,
                "title": title,
                "statement": statement,
                "kind": kind,
                "status": "shaky",
                "bktMastery": 0.35,
                "confidence": 0.35,
                "notes": notes or statement,
                "createdAt": ts,
                "lastTested": today,
                "nextReview": today,
            }
            if cues_list:
                new_prop["cues"] = cues_list
            props.append(new_prop)
            save_jsonl_atomic(props_file, props)

    clean_premises = [p for p in premises if p != prop_id]
    if clean_premises:
        with workspace_lock(chains_file):
            chains = load_jsonl(chains_file)
            chain_key = f"{'+'.join(sorted(clean_premises))}>{prop_id}"
            exists = any((normalize_chain(c) or {}).get("chainKey") == chain_key for c in chains)
            if not exists:
                rec: dict[str, Any] = {
                    "premises": clean_premises,
                    "conclusion": prop_id,
                    "relation": relation,
                    "why": statement,
                    "to": prop_id,
                }
                if len(clean_premises) == 1:
                    rec["from"] = clean_premises[0]
                chains.append(rec)
                save_jsonl_atomic(chains_file, chains)
                chain_added = True

    if on_change is not None:
        try:
            on_change("mcp:capture_concept")
        except Exception:
            pass

    return _tool_ok(
        {
            "kind": "synt_capture_concept_result",
            "ok": True,
            "id": prop_id,
            "deduplicated": deduplicated,
            "matchedId": prop_id if deduplicated else None,
            "matchedScore": matched_score,
            "cueAdded": cue_added,
            "chainAdded": chain_added,
        }
    )


def _handle_log_review(
    ws: Path,
    args: dict[str, Any],
    *,
    on_change: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    prop_id = _require_str(args, "propId")
    rating = _require_str(args, "rating").lower()
    if rating not in _VALID_RATINGS:
        raise _InvalidParamsError(f"Invalid rating {rating!r}; expected one of: again, hard, good, easy.")
    confidence = _opt_num(args, "confidence")
    cue_id = _opt_str(args, "cueId")
    misconception = bool(_opt_bool(args, "misconception", default=False))
    duration_ms_raw = _opt_num(args, "durationMs")
    if duration_ms_raw is not None and duration_ms_raw < 0:
        raise _InvalidParamsError("Parameter 'durationMs' must be >= 0.")
    hints_used = _opt_int(args, "hintsUsed", default=0)
    if hints_used is not None and (hints_used < 0 or hints_used > 5):
        raise _InvalidParamsError("Parameter 'hintsUsed' must be an integer between 0 and 5.")

    kg_dir = resolve_kg_dir(ws)
    try:
        srs_mod = _load_script_module("srs_engine", ws)
        res = srs_mod.record_review(
            kg_dir,
            prop_id,
            rating,
            cue_id=cue_id,
            misconception=misconception,
            confidence=confidence,
            duration_ms=int(round(duration_ms_raw)) if duration_ms_raw is not None else None,
            hints_used=int(hints_used or 0),
            no_sync=True,
        )
    except ValueError as exc:
        return _tool_err(str(exc))

    if on_change is not None:
        try:
            on_change("mcp:log_review")
        except Exception:
            pass

    fire_credits = list(res.get("fireCredits") or [])
    fire_summary = (
        f"FIRe refreshed {len(fire_credits)} prerequisite{'s' if len(fire_credits) != 1 else ''}"
        if fire_credits
        else None
    )
    out = {
        "kind": "synt_log_review_result",
        "ok": True,
        **res,
        "fireRefreshedCount": len(fire_credits),
    }
    if fire_summary:
        out["fireSummary"] = fire_summary
    return _tool_ok(out)


def _handle_whiteboard_step(
    ws: Path,
    args: dict[str, Any],
    *,
    on_whiteboard_frame: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    kind = (_opt_str(args, "kind") or "derivation_step").lower()
    if kind not in VALID_FRAME_KINDS:
        raise _InvalidParamsError(
            f"Invalid frame kind {kind!r}; expected one of: {', '.join(sorted(VALID_FRAME_KINDS))}."
        )
    title = _require_str(args, "title")
    body = _opt_str(args, "body") or ""
    latex = _opt_str(args, "latex")
    prop_id = _opt_str(args, "propId")
    step_index = _opt_int(args, "stepIndex")
    locked = _opt_bool(args, "locked")
    prompt = _opt_str(args, "prompt")
    revealed_body = _opt_str(args, "revealedBody")
    expected_answer = args.get("expectedAnswer")
    if expected_answer is not None and not isinstance(expected_answer, (str, int, float, bool)):
        raise _InvalidParamsError("Parameter 'expectedAnswer' must be a scalar value.")
    payload = args.get("payload")
    if payload is not None and not isinstance(payload, dict):
        raise _InvalidParamsError("Parameter 'payload' must be an object.")
    session_title = _opt_str(args, "sessionTitle")
    frame_id = _opt_str(args, "id")
    resolve_pin_id = _opt_str(args, "resolvePinId")
    resolution = _opt_str(args, "resolution")

    kg_dir = resolve_kg_dir(ws)
    pin_resolved = False
    if resolve_pin_id:
        try:
            resolve_pin(kg_dir, resolve_pin_id, resolution=resolution)
            pin_resolved = True
        except KeyError:
            pin_resolved = False

    frame_spec: dict[str, Any] = {
        "kind": kind,
        "title": title,
        "body": body,
        "payload": dict(payload or {}),
    }
    if frame_id:
        frame_spec["id"] = frame_id
    if latex:
        frame_spec["latex"] = latex
    if prop_id:
        frame_spec["propId"] = prop_id
    if step_index is not None:
        frame_spec["stepIndex"] = step_index
    if locked is not None:
        frame_spec["locked"] = locked
    if prompt:
        frame_spec["prompt"] = prompt
    if revealed_body:
        frame_spec["revealedBody"] = revealed_body
    if expected_answer is not None:
        frame_spec["expectedAnswer"] = expected_answer
    if session_title:
        frame_spec["sessionTitle"] = session_title

    pushed = push_frame(kg_dir, frame_spec)
    sess = pushed.get("session") or get_session(kg_dir)
    safe_frame = _sanitize_whiteboard_frame(pushed.get("frame") or {})

    if on_whiteboard_frame is not None:
        try:
            on_whiteboard_frame(safe_frame)
        except Exception:
            pass

    return _tool_ok(
        {
            "kind": "synt_whiteboard_step",
            "ok": True,
            "revision": pushed.get("revision", sess.get("revision", 0)),
            "frameId": safe_frame.get("id"),
            "frameKind": safe_frame.get("kind"),
            "title": safe_frame.get("title"),
            "stepIndex": safe_frame.get("stepIndex"),
            "locked": bool(safe_frame.get("locked")),
            "frame": safe_frame,
            "pinResolved": pin_resolved,
            "frameCount": sess.get("frameCount", len(sess.get("frames") or [])),
            "pendingPins": sess.get("pendingPins") or [],
            "pendingPinCount": sess.get("pendingPinCount", 0),
        }
    )


def _handle_readiness(ws: Path, args: dict[str, Any]) -> dict[str, Any]:
    exam_date = _opt_str(args, "examDate")
    subject = _opt_str(args, "subject")
    chapter = _opt_str(args, "chapter")
    max_marks = _opt_num(args, "maxMarks")
    if max_marks is not None and max_marks <= 0:
        raise _InvalidParamsError("Parameter 'maxMarks' must be > 0.")
    premortem_only = bool(_opt_bool(args, "premortemOnly", default=False))

    try:
        from syntopik import readiness as readiness_mod
    except ImportError:
        return _tool_err("Readiness forecaster (syntopik.readiness) is not available in this installation.")

    kg_dir = resolve_kg_dir(ws)
    chapters_dir = resolve_chapters_dir(ws)

    if premortem_only:
        res = readiness_mod.build_premortem_shield(
            ws,
            subject=subject,
            chapters=[chapter] if chapter else None,
            kg_dir=kg_dir,
        )
    else:
        res = readiness_mod.compute_readiness(
            ws,
            exam_date=exam_date,
            subject=subject,
            max_marks=int(round(max_marks)) if max_marks is not None else None,
            kg_dir=kg_dir,
            chapters_dir=chapters_dir,
        )
    if not isinstance(res, dict):
        return _tool_err("Unexpected readiness response.")
    return _tool_ok({"kind": "synt_readiness", "ok": bool(res.get("ok", True)), **res})


def _handle_interleave(ws: Path, args: dict[str, Any]) -> dict[str, Any]:
    action = (_opt_str(args, "action") or "build").lower()
    if action not in ("build", "check", "stats"):
        raise _InvalidParamsError(f"Invalid action {action!r}; expected one of: build, check, stats.")

    try:
        from syntopik import interleave as interleave_mod
    except ImportError:
        return _tool_err("Blind-chapter interleave module (syntopik.interleave) is not available in this installation.")

    kg_dir = resolve_kg_dir(ws)

    if action == "stats":
        res = interleave_mod.get_interleave_stats(ws, kg_dir=kg_dir)
        return _tool_ok({"kind": "synt_interleave_stats", "ok": bool(res.get("ok", True)), **res})

    if action == "check":
        session_id = _require_str(args, "sessionId")
        item_id = _require_str(args, "itemId")
        selected = _require_str(args, "selectedPrinciple")
        conf = _opt_num(args, "confidence")
        dur = _opt_num(args, "durationMs")
        if dur is not None and dur < 0:
            raise _InvalidParamsError("Parameter 'durationMs' must be >= 0.")
        try:
            res = interleave_mod.check_principle_step(
                ws,
                session_id=session_id,
                item_id=item_id,
                selected_principle=selected,
                confidence=conf if conf is not None else 0.5,
                duration_ms=int(round(dur)) if dur is not None else None,
                kg_dir=kg_dir,
            )
        except (KeyError, ValueError) as exc:
            return _tool_err(str(exc))
        return _tool_ok({"kind": "synt_interleave_check", "ok": bool(res.get("ok", True)), **res})

    # action == "build"
    n_val = _opt_int(args, "n")
    if n_val is None:
        n_val = _opt_int(args, "count", default=6)
    n_clamped = max(1, min(20, int(n_val if n_val is not None else 6)))
    mode = _opt_str(args, "mode") or "principle_first"
    seed = _opt_int(args, "seed")

    raw_subjects = args.get("subjects")
    subjects: list[str] | None = None
    if raw_subjects is not None:
        if not isinstance(raw_subjects, list) or not all(isinstance(x, str) for x in raw_subjects):
            raise _InvalidParamsError("Parameter 'subjects' must be an array of strings.")
        subjects = [x.strip() for x in raw_subjects if x.strip()] or None
    else:
        single_subj = _opt_str(args, "subject")
        if single_subj:
            subjects = [single_subj]

    res = interleave_mod.build_interleave_set(
        ws,
        n=n_clamped,
        subjects=subjects,
        mode=mode,
        seed=seed,
        kg_dir=kg_dir,
    )
    if not isinstance(res, dict):
        return _tool_err("Unexpected interleave response.")
    # STRICT ZERO-LEAK SANITIZATION: strip all sealed answers/chapters before returning!
    safe_res = _sanitize_interleave_build(res)
    return _tool_ok({"kind": "synt_interleave", "ok": bool(safe_res.get("ok", True)), **safe_res})


# ---------------------------------------------------------------------------
# JSON-RPC 2.0 Request Dispatcher
# ---------------------------------------------------------------------------


def _dispatch_single(
    ws: Path,
    req: Any,
    *,
    version: str,
    on_change: Callable[[str], None] | None,
    on_whiteboard_frame: Callable[[dict[str, Any]], None] | None,
) -> tuple[int, dict[str, Any] | None]:
    if not isinstance(req, dict):
        return 200, _rpc_error(None, JSONRPC_INVALID_REQUEST, "Invalid Request: expected JSON object.")

    has_id = "id" in req
    req_id = req.get("id") if has_id else None
    if has_id and (isinstance(req_id, bool) or (req_id is not None and not isinstance(req_id, (str, int)))):
        return 200, _rpc_error(None, JSONRPC_INVALID_REQUEST, "Invalid Request: 'id' must be string, integer, or null.")

    if req.get("jsonrpc") != "2.0":
        return 200, _rpc_error(req_id, JSONRPC_INVALID_REQUEST, "Invalid Request: 'jsonrpc' must be '2.0'.")

    method = req.get("method")
    if not isinstance(method, str) or not method.strip():
        return 200, _rpc_error(req_id, JSONRPC_INVALID_REQUEST, "Invalid Request: 'method' must be a non-empty string.")
    method = method.strip()

    params = req.get("params")
    if params is not None and not isinstance(params, dict):
        if not has_id:
            return 202, None
        return 200, _rpc_error(req_id, JSONRPC_INVALID_PARAMS, "Invalid params: expected JSON object.")
    params_dict: dict[str, Any] = params if isinstance(params, dict) else {}

    # JSON-RPC 2.0 Notifications (no "id" field) -> 202 Accepted, empty body
    if not has_id:
        return 202, None

    if method == "ping":
        return 200, _rpc_result(req_id, {})

    if method == "initialize":
        client_pv = params_dict.get("protocolVersion")
        negotiated = (
            client_pv
            if isinstance(client_pv, str) and client_pv in SUPPORTED_PROTOCOL_VERSIONS
            else MCP_PROTOCOL_VERSION
        )
        return 200, _rpc_result(
            req_id,
            {
                "protocolVersion": negotiated,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "syntopik-http-mcp", "version": str(version)},
                "instructions": (
                    "Syntopik pure-Python loopback MCP server. Call synt_next_move first for the "
                    "compact <=180-token Action Envelope, synt_get_frontier for full study state, "
                    "synt_whiteboard_step for live frames, synt_capture_concept & synt_log_review "
                    "for Knowledge Graph persistence, and synt_readiness / synt_interleave for exam prep."
                ),
            },
        )

    if method == "tools/list":
        return 200, _rpc_result(req_id, {"tools": list_http_mcp_tools()})

    if method == "tools/call":
        tool_name = params_dict.get("name")
        if not isinstance(tool_name, str) or not tool_name.strip():
            return 200, _rpc_error(req_id, JSONRPC_INVALID_PARAMS, "Invalid params: tools/call requires non-empty 'name'.")
        tool_name = tool_name.strip()

        raw_args = params_dict.get("arguments")
        if raw_args is None:
            tool_args: dict[str, Any] = {}
        elif isinstance(raw_args, dict):
            tool_args = raw_args
        else:
            return 200, _rpc_error(req_id, JSONRPC_INVALID_PARAMS, "Invalid params: 'arguments' must be an object.")

        handlers: dict[str, Callable[[], dict[str, Any]]] = {
            "synt_next_move": lambda: _handle_next_move(ws, tool_args),
            "synt_get_frontier": lambda: _handle_get_frontier(ws, tool_args),
            "synt_capture_concept": lambda: _handle_capture_concept(ws, tool_args, on_change=on_change),
            "synt_log_review": lambda: _handle_log_review(ws, tool_args, on_change=on_change),
            "synt_whiteboard_step": lambda: _handle_whiteboard_step(ws, tool_args, on_whiteboard_frame=on_whiteboard_frame),
            "synt_readiness": lambda: _handle_readiness(ws, tool_args),
            "synt_interleave": lambda: _handle_interleave(ws, tool_args),
        }

        handler = handlers.get(tool_name)
        if handler is None:
            return 200, _rpc_error(req_id, JSONRPC_INVALID_PARAMS, f"Unknown tool: {tool_name}")

        try:
            tool_result = handler()
            # Final strict serialization check guarantees zero NaN/Infinity leaks
            _strict_dumps(tool_result)
            return 200, _rpc_result(req_id, tool_result)
        except _InvalidParamsError as exc:
            return 200, _rpc_error(req_id, JSONRPC_INVALID_PARAMS, str(exc))
        except Exception as exc:  # noqa: BLE001
            return 200, _rpc_error(req_id, JSONRPC_INTERNAL_ERROR, f"Tool '{tool_name}' failed: {exc}")

    return 200, _rpc_error(req_id, JSONRPC_METHOD_NOT_FOUND, f"Method not found: {method}")


def handle_mcp_http_request(
    workspace: Path | str | None,
    payload: dict[str, Any] | list[Any] | bytes | bytearray | str,
    *,
    version: str = __version__,
    on_change: Callable[[str], None] | None = None,
    on_whiteboard_frame: Callable[[dict[str, Any]], None] | None = None,
) -> tuple[int, dict[str, Any] | list[dict[str, Any]] | None]:
    """Dispatch a single or batched JSON-RPC 2.0 MCP request and return `(http_status, body_or_None)`."""
    ws = resolve_workspace(workspace)

    if isinstance(payload, (bytes, bytearray, str)):
        try:
            parsed = parse_mcp_json_bytes(payload)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            return 200, _rpc_error(None, JSONRPC_PARSE_ERROR, f"Parse error: {exc}")
    else:
        parsed = payload

    if _contains_non_finite(parsed):
        raw_id = parsed.get("id") if isinstance(parsed, dict) else None
        err_id = (
            raw_id
            if isinstance(raw_id, (str, int, float))
            and not isinstance(raw_id, bool)
            and (not isinstance(raw_id, float) or math.isfinite(raw_id))
            else None
        )
        return 200, _rpc_error(err_id, JSONRPC_INVALID_REQUEST, "Invalid Request: non-finite JSON number rejected.")

    if isinstance(parsed, list):
        if not parsed:
            return 200, _rpc_error(None, JSONRPC_INVALID_REQUEST, "Invalid Request: empty JSON-RPC batch.")
        if len(parsed) > MAX_BATCH_SIZE:
            return 200, _rpc_error(None, JSONRPC_INVALID_REQUEST, f"Invalid Request: batch exceeds {MAX_BATCH_SIZE} calls.")
        responses: list[dict[str, Any]] = []
        for item in parsed:
            _status, resp = _dispatch_single(
                ws,
                item,
                version=version,
                on_change=on_change,
                on_whiteboard_frame=on_whiteboard_frame,
            )
            if resp is not None:
                responses.append(resp)
        if not responses:
            return 202, None
        return 200, responses

    return _dispatch_single(
        ws,
        parsed,
        version=version,
        on_change=on_change,
        on_whiteboard_frame=on_whiteboard_frame,
    )
