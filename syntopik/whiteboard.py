"""Live AI-to-Whiteboard SSE Frame Bus for Syntopik v3.0.

Maintains atomic whiteboard session state in `knowledge-graph/whiteboard-session.json`
for streaming derivation steps, prediction gates (locked until the learner commits
a prediction + confidence), explorable widgets, diagram callouts, summaries, and
bidirectional learner step pins.

Pure Python 3.12+ standard library only (zero external dependencies).
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from syntopik.paths import resolve_kg_dir
from syntopik.schemas import now_iso
from syntopik.storage import load_json, save_json_atomic, workspace_lock

__all__ = [
    "WHITEBOARD_FILENAME",
    "VALID_FRAME_KINDS",
    "resolve_whiteboard_path",
    "get_session",
    "push_frame",
    "unlock_gate",
    "pin_step",
    "resolve_pin",
    "clear_session",
]

WHITEBOARD_FILENAME = "whiteboard-session.json"
VALID_FRAME_KINDS: frozenset[str] = frozenset(
    {
        "derivation_step",
        "prediction_gate",
        "explore_widget",
        "diagram_callout",
        "summary",
    }
)
_MAX_FRAMES = 200
_MAX_PINS = 200


def resolve_whiteboard_path(kg_dir: Path | str | None = None) -> Path:
    """Resolve `whiteboard-session.json` inside `kg_dir`."""
    if kg_dir is not None and str(kg_dir).strip():
        kdir = Path(kg_dir).expanduser().resolve()
    else:
        kdir = resolve_kg_dir()
    return kdir / WHITEBOARD_FILENAME


def _blank_session(*, revision: int = 0, title: str = "") -> dict[str, Any]:
    ts = now_iso()
    return {
        "schemaVersion": 1,
        "sessionId": f"wb-{uuid.uuid4().hex[:8]}",
        "revision": int(revision),
        "title": str(title or "").strip(),
        "activeFrameId": None,
        "frames": [],
        "pins": [],
        "updatedAt": ts,
    }


def _normalize_session(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return _blank_session()
    sess = {
        "schemaVersion": 1,
        "sessionId": str(raw.get("sessionId") or f"wb-{uuid.uuid4().hex[:8]}"),
        "revision": int(raw.get("revision") or 0),
        "title": str(raw.get("title") or "").strip(),
        "activeFrameId": raw.get("activeFrameId"),
        "frames": [f for f in (raw.get("frames") or []) if isinstance(f, dict)],
        "pins": [p for p in (raw.get("pins") or []) if isinstance(p, dict)],
        "updatedAt": str(raw.get("updatedAt") or now_iso()),
    }
    return sess


def _attach_views(session: dict[str, Any]) -> dict[str, Any]:
    out = dict(session)
    pending = [p for p in out["pins"] if not p.get("resolved")]
    out["pendingPins"] = pending
    out["pendingPinCount"] = len(pending)
    out["frameCount"] = len(out["frames"])
    return out


def _clamp_confidence(confidence: float | int | None) -> float:
    if confidence is None:
        return 0.5
    val = float(confidence)
    if 1.0 < val <= 100.0:
        val = val / 100.0
    return round(max(0.0, min(1.0, val)), 4)


def get_session(kg_dir: Path | str | None = None) -> dict[str, Any]:
    """Read the current whiteboard session state cleanly (returns empty skeleton if absent)."""
    path = resolve_whiteboard_path(kg_dir)
    raw = load_json(path, default=None)
    session = _normalize_session(raw)
    return _attach_views(session)


def push_frame(kg_dir: Path | str | None, frame: dict[str, Any]) -> dict[str, Any]:
    """Atomically append a validated whiteboard frame and bump `revision` for SSE subscribers."""
    if not isinstance(frame, dict):
        raise ValueError("frame must be a dict.")

    kind = str(frame.get("kind") or "").strip().lower()
    if kind not in VALID_FRAME_KINDS:
        raise ValueError(
            f"Unsupported frame kind {kind!r}; expected one of: {', '.join(sorted(VALID_FRAME_KINDS))}."
        )

    path = resolve_whiteboard_path(kg_dir)
    with workspace_lock(path):
        session = _normalize_session(load_json(path, default=None))
        next_rev = int(session["revision"]) + 1
        ts = now_iso()

        fid = str(frame.get("id") or "").strip() or f"fr-{next_rev:04d}-{uuid.uuid4().hex[:4]}"
        is_gate = kind == "prediction_gate"
        locked = bool(frame.get("locked", True)) if is_gate else bool(frame.get("locked", False))

        gate_obj: dict[str, Any] | None = None
        if is_gate:
            raw_gate = frame.get("gate") if isinstance(frame.get("gate"), dict) else {}
            gate_obj = {
                "prompt": str(raw_gate.get("prompt") or frame.get("prompt") or frame.get("title") or "Commit your prediction before revealing.").strip(),
                "revealedBody": str(raw_gate.get("revealedBody") or frame.get("revealedBody") or frame.get("body") or "").strip(),
                "expectedAnswer": raw_gate.get("expectedAnswer", frame.get("expectedAnswer")),
                "prediction": raw_gate.get("prediction"),
                "confidence": raw_gate.get("confidence"),
                "unlockedAt": raw_gate.get("unlockedAt"),
            }

        entry: dict[str, Any] = {
            "id": fid,
            "seq": next_rev,
            "kind": kind,
            "title": str(frame.get("title") or kind.replace("_", " ").title()).strip(),
            "body": "" if (is_gate and locked) else str(frame.get("body") or "").strip(),
            "latex": str(frame.get("latex") or "").strip() or None,
            "propId": str(frame.get("propId") or "").strip() or None,
            "stepIndex": int(frame["stepIndex"]) if frame.get("stepIndex") is not None else len(session["frames"]),
            "locked": locked,
            "gate": gate_obj,
            "payload": dict(frame.get("payload") or {}),
            "createdAt": ts,
            "updatedAt": ts,
        }

        if frame.get("sessionTitle"):
            session["title"] = str(frame["sessionTitle"]).strip()

        frames = session["frames"]
        replaced = False
        for idx, existing in enumerate(frames):
            if str(existing.get("id") or "") == fid:
                entry["createdAt"] = existing.get("createdAt") or ts
                frames[idx] = entry
                replaced = True
                break
        if not replaced:
            frames.append(entry)
        if len(frames) > _MAX_FRAMES:
            del frames[:-_MAX_FRAMES]

        session["revision"] = next_rev
        session["activeFrameId"] = fid
        session["updatedAt"] = ts
        save_json_atomic(path, session)

    return {
        "ok": True,
        "revision": session["revision"],
        "path": str(path),
        "frame": entry,
        "session": _attach_views(session),
    }


def unlock_gate(
    kg_dir: Path | str | None,
    frame_id: str,
    prediction: str,
    confidence: float = 0.5,
) -> dict[str, Any]:
    """Unlock a `prediction_gate` frame after recording the learner's committed prediction + confidence."""
    target_id = str(frame_id or "").strip()
    pred_clean = str(prediction or "").strip()
    if not target_id:
        raise ValueError("frame_id is required.")
    if not pred_clean:
        raise ValueError("prediction cannot be empty when unlocking a prediction gate.")
    conf_norm = _clamp_confidence(confidence)

    path = resolve_whiteboard_path(kg_dir)
    with workspace_lock(path):
        session = _normalize_session(load_json(path, default=None))
        target: dict[str, Any] | None = None
        for fr in session["frames"]:
            if str(fr.get("id") or "") == target_id:
                target = fr
                break
        if target is None:
            raise KeyError(f"Whiteboard frame '{target_id}' not found.")

        ts = now_iso()
        gate = dict(target.get("gate") or {})
        gate["prediction"] = pred_clean
        gate["confidence"] = conf_norm
        gate["unlockedAt"] = ts
        target["gate"] = gate
        target["locked"] = False
        if gate.get("revealedBody") and not target.get("body"):
            target["body"] = str(gate["revealedBody"])
        target["updatedAt"] = ts

        session["revision"] = int(session["revision"]) + 1
        session["activeFrameId"] = target_id
        session["updatedAt"] = ts
        save_json_atomic(path, session)

    return {
        "ok": True,
        "revision": session["revision"],
        "frameId": target_id,
        "unlocked": True,
        "prediction": pred_clean,
        "confidence": conf_norm,
        "frame": target,
    }


def pin_step(
    kg_dir: Path | str | None,
    step_index: int,
    note: str,
    frame_id: str | None = None,
) -> dict[str, Any]:
    """Record a learner confusion/question pin on a whiteboard step for the teacher brainstem."""
    note_clean = str(note or "").strip()
    if not note_clean:
        raise ValueError("Pin note cannot be empty.")
    idx_int = int(step_index)
    if idx_int < 0:
        raise ValueError("step_index must be >= 0.")

    path = resolve_whiteboard_path(kg_dir)
    with workspace_lock(path):
        session = _normalize_session(load_json(path, default=None))
        ts = now_iso()

        eff_frame_id = str(frame_id or "").strip() or None
        if eff_frame_id is None:
            if 0 <= idx_int < len(session["frames"]):
                eff_frame_id = str(session["frames"][idx_int].get("id") or "") or None
            else:
                eff_frame_id = session.get("activeFrameId")

        pin_id = f"pin-{uuid.uuid4().hex[:8]}"
        pin_obj: dict[str, Any] = {
            "id": pin_id,
            "frameId": eff_frame_id,
            "stepIndex": idx_int,
            "note": note_clean,
            "resolved": False,
            "resolution": None,
            "createdAt": ts,
            "resolvedAt": None,
        }
        session["pins"].append(pin_obj)
        if len(session["pins"]) > _MAX_PINS:
            del session["pins"][:-_MAX_PINS]

        session["revision"] = int(session["revision"]) + 1
        session["updatedAt"] = ts
        save_json_atomic(path, session)
        pending_count = sum(1 for p in session["pins"] if not p.get("resolved"))

    return {
        "ok": True,
        "revision": session["revision"],
        "pin": pin_obj,
        "pendingPinCount": pending_count,
    }


def resolve_pin(
    kg_dir: Path | str | None,
    pin_id: str,
    *,
    resolution: str | None = None,
) -> dict[str, Any]:
    """Mark a learner whiteboard pin as resolved."""
    pid = str(pin_id or "").strip()
    if not pid:
        raise ValueError("pin_id is required.")

    path = resolve_whiteboard_path(kg_dir)
    with workspace_lock(path):
        session = _normalize_session(load_json(path, default=None))
        target: dict[str, Any] | None = None
        for p in session["pins"]:
            if str(p.get("id") or "") == pid:
                target = p
                break
        if target is None:
            raise KeyError(f"Whiteboard pin '{pid}' not found.")

        ts = now_iso()
        target["resolved"] = True
        target["resolvedAt"] = ts
        if resolution is not None:
            target["resolution"] = str(resolution).strip()

        session["revision"] = int(session["revision"]) + 1
        session["updatedAt"] = ts
        save_json_atomic(path, session)
        pending_count = sum(1 for p in session["pins"] if not p.get("resolved"))

    return {
        "ok": True,
        "revision": session["revision"],
        "pin": target,
        "pendingPinCount": pending_count,
    }


def clear_session(kg_dir: Path | str | None = None) -> dict[str, Any]:
    """Reset the whiteboard session while monotonically bumping `revision` for SSE listeners."""
    path = resolve_whiteboard_path(kg_dir)
    with workspace_lock(path):
        prev = _normalize_session(load_json(path, default=None))
        fresh = _blank_session(revision=int(prev["revision"]) + 1)
        save_json_atomic(path, fresh)

    return {
        "ok": True,
        "cleared": True,
        "revision": fresh["revision"],
        "path": str(path),
        "session": _attach_views(fresh),
    }
