"""Atomic, concurrency-safe JSON / JSONL / text file storage primitives.

Pure Python 3.12 standard library only (zero external dependencies).
Locks are placed in the OS temp directory keyed by SHA-256 of the target path
so working directories and git workspaces stay 100% clean.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import tempfile
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

try:
    import fcntl  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - Windows fallback
    fcntl = None  # type: ignore[assignment]

try:
    import msvcrt  # type: ignore[import-not-found]
except ImportError:
    msvcrt = None  # type: ignore[assignment]

__all__ = [
    "workspace_lock",
    "load_json",
    "save_json_atomic",
    "load_jsonl",
    "save_jsonl_atomic",
    "append_jsonl_locked",
    "write_text_atomic",
]

_LOCK_REGISTRY_GUARD = threading.Lock()
_THREAD_LOCKS: dict[str, threading.RLock] = {}
_LOCAL_STATE = threading.local()


def _lock_key_and_file(path: Path | str) -> tuple[str, Path]:
    resolved = Path(path).expanduser().resolve()
    key = str(resolved)
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]
    lock_dir = Path(tempfile.gettempdir()) / "syntopik-locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    return key, lock_dir / f"{digest}.lock"


def _get_thread_lock(key: str) -> threading.RLock:
    with _LOCK_REGISTRY_GUARD:
        lk = _THREAD_LOCKS.get(key)
        if lk is None:
            lk = threading.RLock()
            _THREAD_LOCKS[key] = lk
        return lk


@contextlib.contextmanager
def workspace_lock(path: Path | str, timeout: float = 10.0) -> Iterator[None]:
    """Reentrant thread + cross-process exclusive lock for a workspace path or file."""
    key, lock_file = _lock_key_and_file(path)
    tlock = _get_thread_lock(key)
    held: dict[str, int] = getattr(_LOCAL_STATE, "held", None)  # type: ignore[assignment]
    if held is None:
        held = {}
        _LOCAL_STATE.held = held

    with tlock:
        depth = held.get(key, 0)
        if depth > 0:
            held[key] = depth + 1
            try:
                yield
            finally:
                held[key] -= 1
            return

        fh = None
        locked = False
        deadline = time.monotonic() + max(0.1, float(timeout))
        try:
            fh = lock_file.open("a+b")
            if fcntl is not None:
                while True:
                    try:
                        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        locked = True
                        break
                    except BlockingIOError:
                        if time.monotonic() >= deadline:
                            raise TimeoutError(f"Timed out waiting for lock on {path}")
                        time.sleep(0.01)
            elif msvcrt is not None:  # pragma: no cover - Windows
                fh.seek(0)
                if fh.read(1) == b"":
                    fh.write(b"0")
                    fh.flush()
                while True:
                    try:
                        fh.seek(0)
                        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                        locked = True
                        break
                    except OSError:
                        if time.monotonic() >= deadline:
                            raise TimeoutError(f"Timed out waiting for lock on {path}")
                        time.sleep(0.01)

            held[key] = 1
            yield
        finally:
            if held.get(key, 0) > 0:
                held[key] -= 1
                if held[key] == 0:
                    held.pop(key, None)
            if fh is not None:
                if locked:
                    try:
                        if fcntl is not None:
                            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
                        elif msvcrt is not None:  # pragma: no cover
                            fh.seek(0)
                            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
                    except OSError:
                        pass
                try:
                    fh.close()
                except OSError:
                    pass


def write_text_atomic(path: Path | str, content: str, *, encoding: str = "utf-8") -> Path:
    """Atomically write UTF-8 text via a same-directory temporary file + `os.replace()`."""
    target = Path(path).expanduser().resolve()
    with workspace_lock(target):
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            dir=str(target.parent),
            prefix=f".{target.name}.",
            suffix=".tmp",
        )
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding=encoding) as fh:
                fh.write(content)
                fh.flush()
                try:
                    os.fsync(fh.fileno())
                except OSError:
                    pass
            os.replace(tmp_path, target)
        except Exception:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise
    return target


def load_json(path: Path | str, default: Any = None, *, strict: bool = False) -> Any:
    """Load JSON from disk safely; return `default` when missing or malformed unless `strict=True`."""
    p = Path(path).expanduser()
    if not p.exists() or not p.is_file():
        if strict:
            raise FileNotFoundError(f"JSON file not found: {p}")
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        if strict:
            raise
        return default


def save_json_atomic(
    path: Path | str,
    data: Any,
    *,
    indent: int = 2,
    trailing_newline: bool = True,
) -> Path:
    """Serialize `data` as UTF-8 JSON and replace `path` atomically under lock."""
    text = json.dumps(data, indent=indent, ensure_ascii=False)
    if trailing_newline:
        text += "\n"
    return write_text_atomic(path, text)


def load_jsonl(path: Path | str) -> list[dict[str, Any]]:
    """Read newline-delimited JSON objects, skipping blank or corrupted lines."""
    p = Path(path).expanduser()
    if not p.exists() or not p.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for raw_line in p.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        if isinstance(obj, dict):
            rows.append(obj)
    return rows


def save_jsonl_atomic(path: Path | str, rows: list[dict[str, Any]]) -> Path:
    """Atomically write a list of JSON objects as newline-delimited JSON under lock."""
    body = "\n".join(json.dumps(r, ensure_ascii=False) for r in rows)
    text = (body + "\n") if body else ""
    return write_text_atomic(path, text)


def append_jsonl_locked(
    path: Path | str,
    rows: dict[str, Any] | list[dict[str, Any]],
) -> Path:
    """Append one or more JSON objects to a JSONL file safely under an exclusive lock."""
    target = Path(path).expanduser().resolve()
    items = [rows] if isinstance(rows, dict) else list(rows)
    if not items:
        return target

    payload = "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in items)
    with workspace_lock(target):
        target.parent.mkdir(parents=True, exist_ok=True)
        needs_leading_newline = False
        if target.exists() and target.stat().st_size > 0:
            with target.open("rb") as rfh:
                rfh.seek(-1, 2)
                needs_leading_newline = rfh.read(1) != b"\n"
        with target.open("a", encoding="utf-8") as fh:
            if needs_leading_newline:
                fh.write("\n")
            fh.write(payload)
            fh.flush()
            try:
                os.fsync(fh.fileno())
            except OSError:
                pass
    return target
