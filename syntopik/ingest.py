"""Syntopik v2.0 — 1-Command Universal Source Importer & Incremental Reading Queue.

Pure Python 3.12+ standard library module (optional lazy subprocess to `yt-dlp`,
`git`, `scripts/pdf_studio.py`, and `scripts/chapter_tracker.py` when present).

Supports:
  - `auto`    : Automatically detect YouTube, arXiv, GitHub/local repo, web URL, or notes/PDF
  - `youtube` : Caption-only lecture ingestion (NEVER downloads audio/video!) into 5-8 min
                sections with `[mm:ss]` links and Active Pause-Point checks
  - `arxiv`   : Normalize ID/URL, fetch Atom metadata, optional PDF download, scaffold Keshav 3-Pass guide
  - `repo`    : Local directory or shallow `--clone` HTTPS GitHub scan -> token-budgeted
                Architecture & Golden-Path Manifest
  - `url`     : Strict http/https URL validation, stdlib HTML->Markdown extraction, incremental slices
  - `notes`   : Local Markdown / Obsidian folder / PDF ingestion into `sources/_ingested/<slug>/`
  - `queue`   : Incremental Reading Queue (`knowledge-graph/reading-queue.jsonl`) with
                `queued -> extracted -> clozed -> crystallized` stages and `priority * urgency` ranking
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import date, datetime, timedelta, timezone
import hashlib
import html as html_lib
from html.parser import HTMLParser
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import tempfile
from typing import Any
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

# ---------------------------------------------------------------------------
# Optional shared primitives from `syntopik.{paths,storage,schemas}` with
# 100% pure-stdlib fallbacks for standalone execution.
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent.parent
USER_AGENT = "Syntopik/2.0 (Local-First Study OS; +https://github.com/HridayKapoor/syntopik)"

try:
    from syntopik.paths import (  # type: ignore[import-not-found]
        find_binary as _shared_find_binary,
        resolve_chapters_dir as _shared_resolve_chapters_dir,
        resolve_kg_dir as _shared_resolve_kg_dir,
        resolve_package_root as _shared_resolve_package_root,
        resolve_workspace as _shared_resolve_workspace,
        sync_hub as _shared_sync_hub,
    )
except Exception:  # noqa: BLE001
    _shared_find_binary = None
    _shared_resolve_chapters_dir = None
    _shared_resolve_kg_dir = None
    _shared_resolve_package_root = None
    _shared_resolve_workspace = None
    _shared_sync_hub = None

try:
    from syntopik.storage import (  # type: ignore[import-not-found]
        load_json as _shared_load_json,
        load_jsonl as _shared_load_jsonl,
        save_json_atomic as _shared_save_json_atomic,
        save_jsonl_atomic as _shared_save_jsonl_atomic,
    )
except Exception:  # noqa: BLE001
    _shared_load_json = None
    _shared_load_jsonl = None
    _shared_save_json_atomic = None
    _shared_save_jsonl_atomic = None

try:
    from syntopik.schemas import (  # type: ignore[import-not-found]
        now_iso as _shared_now_iso,
        slugify as _shared_slugify,
        today_iso as _shared_today_iso,
    )
except Exception:  # noqa: BLE001
    _shared_now_iso = None
    _shared_slugify = None
    _shared_today_iso = None


def now_iso() -> str:
    if _shared_now_iso is not None:
        return str(_shared_now_iso())
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def today_iso() -> str:
    if _shared_today_iso is not None:
        return str(_shared_today_iso())
    return datetime.now(timezone.utc).date().isoformat()


def slugify(text: str, max_len: int = 80) -> str:
    if _shared_slugify is not None:
        val = str(_shared_slugify(text))
    else:
        val = re.sub(r"[^a-z0-9]+", "-", str(text or "").strip().lower()).strip("-")
    val = re.sub(r"[^a-z0-9-]+", "-", val).strip("-")[:max_len].strip("-")
    return val or "source"


def resolve_package_root() -> Path:
    if _shared_resolve_package_root is not None:
        return Path(_shared_resolve_package_root()).resolve()
    return ROOT


def resolve_workspace(explicit: str | Path | None = None) -> Path:
    if _shared_resolve_workspace is not None:
        return Path(_shared_resolve_workspace(explicit)).resolve()
    if explicit is not None and str(explicit).strip():
        return Path(explicit).expanduser().resolve()
    env_ws = os.environ.get("SYNT_WORKSPACE") or os.environ.get("MLH_WORKSPACE")
    if env_ws and env_ws.strip():
        return Path(env_ws).expanduser().resolve()
    cwd = Path.cwd().resolve()
    if (cwd / "knowledge-graph").is_dir() or (cwd / ".syntopik").is_dir():
        return cwd
    return ROOT


def resolve_kg_dir(workspace: str | Path | None = None, explicit_kg_dir: str | Path | None = None) -> Path:
    if explicit_kg_dir is not None and str(explicit_kg_dir).strip():
        return Path(explicit_kg_dir).expanduser().resolve()
    if workspace is not None and str(workspace).strip():
        ws = resolve_workspace(workspace)
        return (ws / "knowledge-graph").resolve()
    if _shared_resolve_kg_dir is not None:
        return Path(_shared_resolve_kg_dir(None)).resolve()
    env_kg = os.environ.get("SYNT_KG_DIR") or os.environ.get("MLH_KG_DIR")
    if env_kg and env_kg.strip():
        return Path(env_kg).expanduser().resolve()
    return (resolve_workspace(None) / "knowledge-graph").resolve()


def resolve_chapters_dir(workspace: str | Path | None = None) -> Path:
    if workspace is not None and str(workspace).strip():
        ws = resolve_workspace(workspace)
        return (ws / "chapters").resolve()
    if _shared_resolve_chapters_dir is not None:
        return Path(_shared_resolve_chapters_dir(None)).resolve()
    env_ch = os.environ.get("SYNT_CHAPTERS_DIR") or os.environ.get("MLH_CHAPTERS_DIR")
    if env_ch and env_ch.strip():
        return Path(env_ch).expanduser().resolve()
    return (resolve_workspace(None) / "chapters").resolve()


def find_binary(name: str) -> str | None:
    if _shared_find_binary is not None:
        return _shared_find_binary(name)
    hit = shutil.which(name)
    if hit:
        return hit
    for cand in (
        Path.home() / ".local" / "bin" / name,
        Path("/usr/local/bin") / name,
        Path("/usr/bin") / name,
    ):
        if cand.is_file() and os.access(cand, os.X_OK):
            return str(cand)
    return None


def load_json(path: Path, default: Any = None) -> Any:
    if _shared_load_json is not None:
        return _shared_load_json(path, default)
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def save_json_atomic(path: Path, data: Any) -> None:
    if _shared_save_json_atomic is not None:
        _shared_save_json_atomic(path, data)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".tmp-", suffix=".json", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if _shared_load_jsonl is not None:
        return list(_shared_load_jsonl(path))
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            if isinstance(obj, dict):
                rows.append(obj)
        except json.JSONDecodeError:
            continue
    return rows


def save_jsonl_atomic(path: Path, rows: list[dict[str, Any]]) -> None:
    if _shared_save_jsonl_atomic is not None:
        _shared_save_jsonl_atomic(path, rows)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".tmp-", suffix=".jsonl", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def write_text_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".tmp-", suffix=".txt", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Security: Path-Jail & Strict URL Validation
# ---------------------------------------------------------------------------


def ensure_within_dir(base_dir: Path, candidate: Path) -> Path:
    """Resolve `candidate` and guarantee it stays strictly inside `base_dir`."""
    base_res = base_dir.resolve()
    cand_res = candidate.resolve()
    if cand_res == base_res or cand_res.is_relative_to(base_res):
        return cand_res
    raise ValueError(f"Path jail violation: '{candidate}' escapes allowed directory '{base_res}'.")


def ingested_dir_for(workspace: Path, slug: str) -> Path:
    """Return path-jailed `sources/_ingested/<safe_slug>` inside `workspace`."""
    raw = str(slug or "").strip()
    if ".." in raw or "/" in raw or "\\" in raw:
        raise ValueError(f"Invalid slug {slug!r}: path separators and '..' are forbidden.")
    clean = slugify(raw)
    root_ingested = (workspace / "sources" / "_ingested").resolve()
    target = ensure_within_dir(root_ingested, root_ingested / clean)
    if target == root_ingested:
        raise ValueError(f"Invalid slug {slug!r}: resolves to base directory.")
    return target


_BLOCKED_SSRF_HOSTS: frozenset[str] = frozenset(
    {
        "localhost",
        "ip6-localhost",
        "ip6-loopback",
        "localhost.localdomain",
        "metadata.google.internal",
        "metadata.goog",
        "metadata",
    }
)


def _parse_ip_literal(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    clean = host.strip().strip("[]")
    if "%" in clean:
        clean = clean.split("%", 1)[0]
    try:
        return ipaddress.ip_address(clean)
    except ValueError:
        pass
    try:
        packed = socket.inet_aton(clean)
        return ipaddress.IPv4Address(packed)
    except OSError:
        return None


def _is_non_public_ip(ip_obj: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip_obj, ipaddress.IPv6Address):
        if ip_obj.ipv4_mapped is not None:
            return _is_non_public_ip(ip_obj.ipv4_mapped)
        if ip_obj.sixtofour is not None and _is_non_public_ip(ip_obj.sixtofour):
            return True
        if ip_obj.teredo is not None:
            server_ip, client_ip = ip_obj.teredo
            if _is_non_public_ip(server_ip) or _is_non_public_ip(client_ip):
                return True
    return bool(
        ip_obj.is_loopback
        or ip_obj.is_link_local
        or ip_obj.is_private
        or ip_obj.is_unspecified
        or ip_obj.is_reserved
        or ip_obj.is_multicast
        or not ip_obj.is_global
    )


def _check_ssrf_host(host: str, port: int | None, *, allow_private: bool = False, require_dns: bool = False) -> None:
    if allow_private:
        return
    h = host.strip().rstrip(".").lower()
    if (
        h in _BLOCKED_SSRF_HOSTS
        or h.endswith(".localhost")
        or h.endswith(".local")
        or h.endswith(".internal")
    ):
        raise ValueError(f"Blocked SSRF target host (private/loopback/internal): {host!r}")

    ip_lit = _parse_ip_literal(h)
    if ip_lit is not None:
        if _is_non_public_ip(ip_lit):
            raise ValueError(f"Blocked SSRF target IP (loopback/link-local/private): {host!r}")
        return

    try:
        infos = socket.getaddrinfo(h, port or 80, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        if require_dns:
            raise ValueError(f"Unable to resolve host {host!r}: {exc}") from exc
        return
    except OSError as exc:
        if require_dns:
            raise ValueError(f"Host resolution failed for {host!r}: {exc}") from exc
        return

    for _family, _socktype, _proto, _canon, sockaddr in infos:
        addr_str = str(sockaddr[0])
        resolved_ip = _parse_ip_literal(addr_str)
        if resolved_ip is not None and _is_non_public_ip(resolved_ip):
            raise ValueError(f"Blocked SSRF target: host {host!r} resolved to non-public IP {addr_str}")


def validate_http_url(
    url: str,
    *,
    allow_private: bool = False,
    require_dns: bool = False,
) -> urllib.parse.ParseResult:
    """Strictly validate an http/https URL with a non-empty public host.

    Immediately rejects `file://`, `javascript:`, `data:`, `ftp:`, control chars,
    missing host, and (when `allow_private=False`) loopback, link-local, and RFC1918
    private IPs or hostnames resolving to them.
    """
    raw = str(url or "").strip()
    if not raw:
        raise ValueError("URL cannot be empty.")
    if any(ord(ch) < 32 or ch.isspace() for ch in raw):
        raise ValueError(f"URL contains illegal whitespace or control characters: {raw!r}")
    parsed = urllib.parse.urlparse(raw)
    scheme = (parsed.scheme or "").lower()
    if scheme not in ("http", "https"):
        raise ValueError(
            f"Unsupported URL scheme '{scheme or '(none)'}': only http:// and https:// URLs are allowed."
        )
    host = (parsed.hostname or "").strip()
    if not host or not parsed.netloc.strip():
        raise ValueError(f"Invalid URL (empty host): {raw!r}")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError(f"Invalid URL port: {raw!r}") from exc
    _check_ssrf_host(host, port, allow_private=allow_private, require_dns=require_dns)
    return parsed


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Validate EVERY HTTP redirect target before opening a connection (prevents redirect SSRF)."""

    max_redirections = 5

    def __init__(self, *, allow_private: bool = False) -> None:
        super().__init__()
        self.allow_private = allow_private

    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> Any:
        target_url = urllib.parse.urljoin(req.full_url, newurl)
        validate_http_url(target_url, allow_private=self.allow_private, require_dns=True)
        return super().redirect_request(req, fp, code, msg, headers, target_url)


def _http_get(
    url: str,
    *,
    timeout: float = 15.0,
    accept: str = "*/*",
    max_bytes: int = 5 * 1024 * 1024,
    allow_private: bool = False,
) -> bytes:
    validate_http_url(url, allow_private=allow_private, require_dns=True)
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": accept,
        },
    )
    opener = urllib.request.build_opener(_SafeRedirectHandler(allow_private=allow_private))
    with opener.open(req, timeout=timeout) as resp:
        # Defense-in-depth: validate final redirected URL too
        final_url = resp.geturl()
        if final_url:
            validate_http_url(final_url, allow_private=allow_private, require_dns=False)
        data = resp.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise ValueError(f"Response from {url} exceeded {max_bytes} byte safety limit.")
        return data


# ---------------------------------------------------------------------------
# Constants for Repo Scanning, Notes, and Incremental Reading Queue
# ---------------------------------------------------------------------------

READING_STAGES: tuple[str, ...] = ("queued", "extracted", "clozed", "crystallized")
STAGE_INTERVAL_DAYS: dict[str, int | None] = {
    "queued": 1,
    "extracted": 3,
    "clozed": 7,
    "crystallized": None,
}

REPO_IGNORED_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "node_modules",
        ".venv",
        "venv",
        "env",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        "dist",
        "build",
        "out",
        "target",
        ".next",
        ".nuxt",
        ".cache",
        "vendor",
        ".idea",
        ".vscode",
        "coverage",
        "htmlcov",
        ".eggs",
        "eggs",
    }
)

BINARY_EXTS: frozenset[str] = frozenset(
    {
        ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico", ".svgz",
        ".pdf", ".zip", ".tar", ".gz", ".bz2", ".xz", ".7z", ".rar", ".whl",
        ".pyc", ".pyo", ".so", ".dylib", ".dll", ".exe", ".o", ".a", ".obj",
        ".class", ".jar", ".war", ".wasm", ".bin", ".dat", ".db", ".sqlite",
        ".sqlite3", ".mp3", ".mp4", ".wav", ".flac", ".avi", ".mov", ".mkv",
        ".ttf", ".otf", ".woff", ".woff2", ".eot", ".apkg", ".DS_Store",
    }
)

EXT_TO_LANG: dict[str, str] = {
    ".py": "Python",
    ".pyi": "Python",
    ".js": "JavaScript",
    ".mjs": "JavaScript",
    ".cjs": "JavaScript",
    ".jsx": "JavaScript",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".rs": "Rust",
    ".go": "Go",
    ".java": "Java",
    ".kt": "Kotlin",
    ".c": "C",
    ".h": "C/C++ Header",
    ".cpp": "C++",
    ".cc": "C++",
    ".hpp": "C++",
    ".cs": "C#",
    ".rb": "Ruby",
    ".php": "PHP",
    ".swift": "Swift",
    ".scala": "Scala",
    ".sh": "Shell",
    ".zsh": "Shell",
    ".bash": "Shell",
    ".sql": "SQL",
    ".html": "HTML",
    ".css": "CSS",
    ".scss": "SCSS",
    ".json": "JSON",
    ".yaml": "YAML",
    ".yml": "YAML",
    ".toml": "TOML",
    ".md": "Markdown",
    ".tex": "LaTeX",
    ".ipynb": "Jupyter",
}

ENTRY_POINT_NAMES: frozenset[str] = frozenset(
    {
        "main.py", "__main__.py", "cli.py", "app.py", "server.py", "manage.py",
        "index.js", "index.mjs", "index.ts", "main.ts", "main.js", "server.js", "server.mjs",
        "main.rs", "lib.rs", "main.go", "Makefile", "Dockerfile",
        "pyproject.toml", "package.json", "Cargo.toml", "go.mod", "README.md",
    }
)

NOTE_FILE_EXTS: frozenset[str] = frozenset({".md", ".markdown", ".txt", ".rst", ".tex", ".pdf"})

STOP_WORDS: frozenset[str] = frozenset(
    (
        "the and for that with this from have are was were will would could should "
        "into about there their they them these those what when where which while "
        "your you our we can may just like also very more most some such than then "
        "here how why all any each other over under only been being does did done "
        "going gonna wanna really actually basically right okay yeah well let lets "
        "know think look see want need make take come give work time way thing"
    ).split()
)

ARXIV_ID_RE = re.compile(
    r"^(?:arxiv:)?(?P<id>(?:\d{4}\.\d{4,5}|[a-z-]+(?:\.[A-Z]{2})?/\d{7})(?:v\d+)?)$",
    re.IGNORECASE,
)
YOUTUBE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


# ---------------------------------------------------------------------------
# Auto-Detection
# ---------------------------------------------------------------------------


def detect_source_type(target: str) -> str:
    """Detect source kind (`youtube`, `arxiv`, `repo`, `url`, `notes`, `anki`, `cards`, `notebook`, `notes_batch`)."""
    raw = str(target or "").strip()
    if not raw:
        raise ValueError("Target cannot be empty.")

    # Explicit non-http dangerous schemes should route to `url` so strict validation rejects them.
    scheme_match = re.match(r"^([a-zA-Z][a-zA-Z0-9+.-]*):", raw)
    if scheme_match and scheme_match.group(1).lower() not in ("http", "https", "arxiv"):
        # Avoid mistaking Windows drive letters like C:\path as schemes
        if not (len(scheme_match.group(1)) == 1 and len(raw) > 2 and raw[2] in ("\\", "/")):
            return "url"

    # Check bare arXiv ID before filesystem checks
    if ARXIV_ID_RE.match(raw):
        return "arxiv"

    # Check URL forms
    if re.match(r"^https?://", raw, re.IGNORECASE):
        parsed = urllib.parse.urlparse(raw)
        host = (parsed.hostname or "").lower()
        path = parsed.path or ""
        if host in ("youtu.be", "youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"):
            return "youtube"
        if host in ("arxiv.org", "www.arxiv.org", "export.arxiv.org"):
            return "arxiv"
        if host in ("github.com", "www.github.com", "gitlab.com", "www.gitlab.com"):
            parts = [p for p in path.strip("/").split("/") if p]
            if len(parts) >= 2 and parts[1] not in ("issues", "pulls", "discussions", "blob"):
                return "repo"
            if raw.endswith(".git"):
                return "repo"
        return "url"

    # Local filesystem path
    p = Path(raw).expanduser()
    if p.exists():
        if p.is_file():
            ext = p.suffix.lower()
            if ext == ".apkg":
                return "anki"
            if ext in (".csv", ".tsv"):
                return "cards"
            if ext == ".ipynb":
                return "notebook"
            if ext == ".zip" and not p.name.lower().endswith(".syntopik-pack.zip"):
                return "notes_batch"
            return "notes"
        if p.is_dir():
            if (p / ".obsidian").is_dir():
                return "notes_batch"
            repo_markers = (".git", "pyproject.toml", "package.json", "Cargo.toml", "go.mod", "Makefile", "src")
            if any((p / marker).exists() for marker in repo_markers):
                return "repo"
            code_count = 0
            note_count = 0
            md_count = 0
            for child in list(p.iterdir())[:40]:
                if child.is_file():
                    ext = child.suffix.lower()
                    if ext in (".md", ".markdown"):
                        md_count += 1
                        note_count += 1
                    elif ext in NOTE_FILE_EXTS:
                        note_count += 1
                    elif ext in EXT_TO_LANG:
                        code_count += 1
                elif child.is_dir() and not child.name.startswith(".") and child.name not in REPO_IGNORED_DIRS:
                    try:
                        for sub in list(child.iterdir())[:10]:
                            if sub.is_file() and sub.suffix.lower() in (".md", ".markdown"):
                                md_count += 1
                                note_count += 1
                    except OSError:
                        pass
            if code_count > note_count:
                return "repo"
            return "notes_batch" if md_count > 0 else "notes"

    raise ValueError(
        f"Could not detect source type for {target!r}: not an existing local path, arXiv ID, or http(s) URL."
    )


# ---------------------------------------------------------------------------
# Optional Tracker Registration Helper
# ---------------------------------------------------------------------------


def _register_with_tracker(
    *,
    workspace: Path,
    slug: str,
    title: str,
    subject: str,
    source_id: str,
    source_label: str,
    source_path: Path,
    source_kind: str,
    notes: str | None = None,
) -> dict[str, Any]:
    tracker_script = resolve_package_root() / "scripts" / "chapter_tracker.py"
    if not tracker_script.is_file():
        return {"registered": False, "reason": "scripts/chapter_tracker.py not found"}

    chapters_dir = resolve_chapters_dir(workspace)
    kg_dir = resolve_kg_dir(workspace)
    tracker_file = chapters_dir / f"{slug}-tracker.json"
    rel_path = str(source_path)
    try:
        rel_path = str(source_path.resolve().relative_to(workspace.resolve()))
    except ValueError:
        pass

    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"

    try:
        if not tracker_file.is_file():
            init_cmd = [
                sys.executable,
                "-B",
                str(tracker_script),
                "init",
                slug,
                "--title",
                title,
                "--subject",
                subject or "General",
                "--chapters-dir",
                str(chapters_dir),
                "--kg-dir",
                str(kg_dir),
                "--no-sync",
                "--json",
            ]
            res_init = subprocess.run(init_cmd, capture_output=True, text=True, env=env, timeout=20, check=False)
            if res_init.returncode != 0:
                return {"registered": False, "stage": "init", "error": (res_init.stderr or res_init.stdout).strip()}

        add_cmd = [
            sys.executable,
            "-B",
            str(tracker_script),
            "add-source",
            slug,
            "--id",
            source_id,
            "--label",
            source_label,
            "--path",
            rel_path,
            "--kind",
            source_kind,
            "--chapters-dir",
            str(chapters_dir),
            "--kg-dir",
            str(kg_dir),
            "--no-sync",
            "--json",
        ]
        if notes:
            add_cmd.extend(["--notes", notes])
        res_add = subprocess.run(add_cmd, capture_output=True, text=True, env=env, timeout=20, check=False)
        if res_add.returncode != 0:
            return {"registered": False, "stage": "add-source", "error": (res_add.stderr or res_add.stdout).strip()}
        return {"registered": True, "trackerFile": str(tracker_file), "sourceId": source_id}
    except Exception as exc:  # noqa: BLE001
        return {"registered": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# 1. YouTube Caption-Only Ingestion (NEVER downloads audio/video!)
# ---------------------------------------------------------------------------


def extract_youtube_video_id(url_or_id: str) -> str:
    """Extract an 11-character YouTube video ID from any standard YouTube URL or bare ID."""
    raw = str(url_or_id or "").strip()
    if YOUTUBE_ID_RE.match(raw):
        return raw
    parsed = validate_http_url(raw)
    host = (parsed.hostname or "").lower()
    if host not in ("youtu.be", "youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"):
        raise ValueError(f"Not a valid YouTube host: {host!r}")
    if host == "youtu.be":
        cand = parsed.path.strip("/").split("/")[0]
        if YOUTUBE_ID_RE.match(cand):
            return cand
    qs = urllib.parse.parse_qs(parsed.query)
    if "v" in qs and qs["v"] and YOUTUBE_ID_RE.match(qs["v"][0]):
        return qs["v"][0]
    m = re.search(r"/(?:embed|shorts|live|v)/([A-Za-z0-9_-]{11})(?:[/?#]|$)", parsed.path)
    if m:
        return m.group(1)
    raise ValueError(f"Could not extract YouTube video ID from {url_or_id!r}.")


def _parse_vtt_ts(ts: str) -> float:
    parts = ts.strip().replace(",", ".").split(":")
    if len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    if len(parts) == 2:
        return int(parts[0]) * 60 + float(parts[1])
    return float(parts[0])


def _format_mmss(seconds: float) -> str:
    sec = max(0, int(round(seconds)))
    hrs, rem = divmod(sec, 3600)
    mins, secs = divmod(rem, 60)
    if hrs > 0:
        return f"{hrs}:{mins:02d}:{secs:02d}"
    return f"{mins:02d}:{secs:02d}"


def parse_vtt(vtt_text: str) -> list[dict[str, Any]]:
    """Parse WebVTT captions into clean deduplicated segments `{startSec, endSec, text}`."""
    segments: list[dict[str, Any]] = []
    lines = vtt_text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    ts_re = re.compile(
        r"^(?P<start>(?:\d{1,2}:)?\d{2}:\d{2}[.,]\d{3})\s*-->\s*(?P<end>(?:\d{1,2}:)?\d{2}:\d{2}[.,]\d{3})"
    )
    i = 0
    last_clean = ""
    while i < len(lines):
        line = lines[i].strip()
        m = ts_re.match(line)
        if not m:
            i += 1
            continue
        start_sec = _parse_vtt_ts(m.group("start"))
        end_sec = _parse_vtt_ts(m.group("end"))
        i += 1
        text_lines: list[str] = []
        while i < len(lines) and lines[i].strip() != "":
            raw_ln = lines[i].strip()
            # Strip WebVTT timing/style tags like <00:01:02.120><c>word</c>
            cleaned = re.sub(r"<[^>]+>", "", raw_ln)
            cleaned = html_lib.unescape(cleaned).strip()
            if cleaned:
                text_lines.append(cleaned)
            i += 1
        joined = " ".join(" ".join(text_lines).split())
        if not joined:
            continue
        # Handle YouTube rolling auto-caption overlap (where cue N+1 repeats cue N prefix)
        if last_clean and joined == last_clean:
            continue
        if last_clean and joined.startswith(last_clean + " "):
            delta = joined[len(last_clean) :].strip()
            last_clean = joined
            if not delta:
                continue
            joined = delta
        else:
            last_clean = joined
        segments.append(
            {
                "startSec": round(start_sec, 2),
                "endSec": round(max(end_sec, start_sec), 2),
                "text": joined,
            }
        )
    return segments


def parse_timedtext_xml(xml_text: str) -> list[dict[str, Any]]:
    """Parse YouTube timedtext XML (`<text start dur>` or `<p t d>`) into segments."""
    segments: list[dict[str, Any]] = []
    root = ET.fromstring(xml_text.strip())
    for el in root.iter():
        tag = el.tag.split("}")[-1]
        if tag == "text":
            start = float(el.attrib.get("start", "0") or 0.0)
            dur = float(el.attrib.get("dur", "0") or 0.0)
            txt = " ".join(html_lib.unescape("".join(el.itertext())).split())
            if txt:
                segments.append({"startSec": round(start, 2), "endSec": round(start + dur, 2), "text": txt})
        elif tag == "p":
            t_ms = float(el.attrib.get("t", "0") or 0.0)
            d_ms = float(el.attrib.get("d", "0") or 0.0)
            txt = " ".join(html_lib.unescape("".join(el.itertext())).split())
            if txt:
                segments.append(
                    {"startSec": round(t_ms / 1000.0, 2), "endSec": round((t_ms + d_ms) / 1000.0, 2), "text": txt}
                )
    return segments


def _extract_key_terms(text: str, limit: int = 4) -> list[str]:
    tokens = re.findall(r"[A-Za-z][A-Za-z0-9_-]{3,}", text.lower())
    counts = Counter(t for t in tokens if t not in STOP_WORDS)
    return [tok for tok, _ in counts.most_common(limit)]


def _build_pause_prompt(section_idx: int, text: str, key_terms: list[str]) -> str:
    if len(key_terms) >= 2:
        return (
            f"Pause before continuing: without re-reading Section {section_idx}, explain the causal link between "
            f"`{key_terms[0]}` and `{key_terms[1]}` in 1-2 sentences, and name one condition where it breaks."
        )
    if key_terms:
        return (
            f"Pause before continuing: state the core invariant behind `{key_terms[0]}` from Section {section_idx} "
            f"from memory and predict what the next section must resolve."
        )
    return (
        f"Pause before continuing: summarize the single governing claim of Section {section_idx} "
        f"from memory before advancing."
    )


def chunk_transcript(
    segments: list[dict[str, Any]],
    *,
    min_sec: float = 300.0,
    target_sec: float = 360.0,
    max_sec: float = 480.0,
) -> list[dict[str, Any]]:
    """Chunk timestamped segments into 5-8 minute sections with auto-placed Active Pause-Points."""
    if not segments:
        return []

    sections: list[dict[str, Any]] = []
    bucket: list[dict[str, Any]] = []
    sec_start = float(segments[0]["startSec"])

    def _flush() -> None:
        if not bucket:
            return
        idx = len(sections) + 1
        start_s = float(bucket[0]["startSec"])
        end_s = float(bucket[-1]["endSec"])
        body = " ".join(s["text"] for s in bucket).strip()
        terms = _extract_key_terms(body, limit=4)
        sections.append(
            {
                "index": idx,
                "startSec": round(start_s, 2),
                "endSec": round(end_s, 2),
                "startTimestamp": _format_mmss(start_s),
                "endTimestamp": _format_mmss(end_s),
                "durationSec": round(max(0.0, end_s - start_s), 2),
                "wordCount": len(body.split()),
                "keyTerms": terms,
                "pausePrompt": _build_pause_prompt(idx, body, terms),
                "text": body,
                "segments": list(bucket),
            }
        )

    for seg in segments:
        if not bucket:
            sec_start = float(seg["startSec"])
        bucket.append(seg)
        elapsed = float(seg["endSec"]) - sec_start
        ends_sentence = bool(re.search(r"[.!?][\"'\)]?$", seg["text"].strip()))
        if elapsed >= max_sec or (elapsed >= target_sec and ends_sentence) or (elapsed >= min_sec and ends_sentence and len(bucket) >= 12):
            _flush()
            bucket = []

    if bucket:
        # Merge tiny trailing tail (< 90s) into previous section if within reasonable bound
        if sections and (float(bucket[-1]["endSec"]) - float(bucket[0]["startSec"])) < 90.0:
            prev = sections[-1]
            if (float(bucket[-1]["endSec"]) - prev["startSec"]) <= max_sec + 90.0:
                merged_segs = prev["segments"] + bucket
                body = " ".join(s["text"] for s in merged_segs).strip()
                terms = _extract_key_terms(body, limit=4)
                prev["endSec"] = round(float(merged_segs[-1]["endSec"]), 2)
                prev["endTimestamp"] = _format_mmss(prev["endSec"])
                prev["durationSec"] = round(max(0.0, prev["endSec"] - prev["startSec"]), 2)
                prev["wordCount"] = len(body.split())
                prev["keyTerms"] = terms
                prev["pausePrompt"] = _build_pause_prompt(prev["index"], body, terms)
                prev["text"] = body
                prev["segments"] = merged_segs
                bucket = []
        if bucket:
            _flush()

    return sections


def _fetch_youtube_captions_stdlib(video_id: str, *, lang: str = "en", timeout: float = 15.0) -> tuple[str | None, list[dict[str, Any]], str]:
    """Fetch captions using stdlib only (watch-page captionTracks or timedtext API)."""
    title: str | None = None
    watch_url = f"https://www.youtube.com/watch?v={video_id}"
    try:
        html_bytes = _http_get(watch_url, timeout=timeout, accept="text/html")
        page = html_bytes.decode("utf-8", errors="replace")
        m_title = re.search(r"<title>(.*?)</title>", page, re.IGNORECASE | re.DOTALL)
        if m_title:
            title = html_lib.unescape(m_title.group(1)).replace(" - YouTube", "").strip()
        m_tracks = re.search(r'"captionTracks"\s*:\s*(\[.*?\])', page)
        if m_tracks:
            tracks = json.loads(m_tracks.group(1))
            # Prefer exact lang (manual before ASR), then lang prefix, then first available
            def _track_rank(tr: dict[str, Any]) -> tuple[int, int]:
                lc = str(tr.get("languageCode") or "").lower()
                is_asr = 1 if tr.get("kind") == "asr" else 0
                if lc == lang.lower():
                    return (0, is_asr)
                if lc.startswith(lang.lower().split("-")[0]):
                    return (1, is_asr)
                return (2, is_asr)

            tracks_sorted = sorted([t for t in tracks if isinstance(t, dict) and t.get("baseUrl")], key=_track_rank)
            if tracks_sorted:
                base_url = str(tracks_sorted[0]["baseUrl"]).replace("\\u0026", "&")
                raw = _http_get(base_url, timeout=timeout).decode("utf-8", errors="replace")
                segs = parse_vtt(raw) if "WEBVTT" in raw[:40] else parse_timedtext_xml(raw)
                if segs:
                    return title, segs, "stdlib-watch-captionTracks"
    except Exception:  # noqa: BLE001
        pass

    # Direct timedtext endpoint fallback
    try:
        tt_url = f"https://video.google.com/timedtext?lang={urllib.parse.quote(lang)}&v={video_id}"
        raw = _http_get(tt_url, timeout=timeout).decode("utf-8", errors="replace")
        if raw.strip().startswith("<"):
            segs = parse_timedtext_xml(raw)
            if segs:
                return title, segs, "stdlib-timedtext"
    except Exception:  # noqa: BLE001
        pass

    return title, [], "none"


def _fetch_youtube_captions_ytdlp(video_id: str, *, lang: str = "en", timeout: float = 30.0) -> tuple[str | None, list[dict[str, Any]], str]:
    """Optional caption-only fallback via `yt-dlp --skip-download` (NEVER downloads audio/video)."""
    ytdlp = find_binary("yt-dlp")
    if not ytdlp:
        return None, [], "none"
    url = f"https://www.youtube.com/watch?v={video_id}"
    with tempfile.TemporaryDirectory(prefix="synt-yt-") as tmpdir:
        out_tpl = str(Path(tmpdir) / "%(id)s.%(ext)s")
        cmd = [
            ytdlp,
            "--skip-download",
            "--no-playlist",
            "--write-subs",
            "--write-auto-subs",
            "--sub-lang",
            f"{lang}.*,{lang}",
            "--sub-format",
            "vtt",
            "--print",
            "title",
            "-o",
            out_tpl,
            url,
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
        title = res.stdout.strip().splitlines()[0] if res.stdout.strip() else None
        vtt_files = sorted(Path(tmpdir).glob("*.vtt"))
        if vtt_files:
            vtt_content = vtt_files[0].read_text(encoding="utf-8", errors="replace")
            return title, parse_vtt(vtt_content), "yt-dlp-vtt"
    return None, [], "none"


def ingest_youtube(
    url: str,
    *,
    workspace: str | Path | None = None,
    slug: str | None = None,
    lang: str = "en",
    vtt_text: str | None = None,
    vtt_file: str | Path | None = None,
    register_tracker: bool = False,
    enqueue: bool = True,
    subject: str = "General",
    title: str | None = None,
    priority: int = 65,
    timeout: float = 15.0,
) -> dict[str, Any]:
    """Caption-only YouTube lecture ingestion into 5-8 min sections + Active Pause-Points."""
    ws = resolve_workspace(workspace)
    video_id = extract_youtube_video_id(url)
    canonical_url = f"https://www.youtube.com/watch?v={video_id}"

    fetched_title: str | None = None
    segments: list[dict[str, Any]] = []
    caption_backend = "injected"

    if vtt_text is not None:
        segments = parse_vtt(vtt_text)
        caption_backend = "vtt-text"
    elif vtt_file is not None:
        vpath = Path(vtt_file).expanduser().resolve()
        if not vpath.is_file():
            raise FileNotFoundError(f"VTT caption file not found: {vpath}")
        raw_vtt = vpath.read_text(encoding="utf-8", errors="replace")
        segments = parse_vtt(raw_vtt) if "WEBVTT" in raw_vtt[:80] or "-->" in raw_vtt[:400] else parse_timedtext_xml(raw_vtt)
        caption_backend = "vtt-file"
    else:
        fetched_title, segments, caption_backend = _fetch_youtube_captions_stdlib(video_id, lang=lang, timeout=timeout)
        if not segments:
            yt_title, yt_segs, yt_backend = _fetch_youtube_captions_ytdlp(video_id, lang=lang, timeout=max(20.0, timeout))
            if yt_segs:
                fetched_title = fetched_title or yt_title
                segments = yt_segs
                caption_backend = yt_backend

    if not segments:
        raise RuntimeError(
            f"No captions retrieved for YouTube video '{video_id}' (caption-only mode; never downloads audio/video). "
            "Pass --vtt-file <path.vtt> for offline captions or verify captions exist."
        )

    final_title = title or fetched_title or f"YouTube Lecture ({video_id})"
    final_slug = slug if slug else slugify(f"yt-{final_title}" if fetched_title or title else f"yt-{video_id}")
    out_dir = ingested_dir_for(ws, final_slug)
    out_dir.mkdir(parents=True, exist_ok=True)

    sections = chunk_transcript(segments)
    total_duration = round(max((float(s["endSec"]) for s in segments), default=0.0), 2)

    md_lines: list[str] = [
        f"# {final_title}",
        "",
        f"- **Source**: [{canonical_url}]({canonical_url})",
        f"- **Video ID**: `{video_id}`",
        f"- **Caption Backend**: `{caption_backend}` (caption-only; zero media downloaded)",
        f"- **Duration**: `{_format_mmss(total_duration)}` · **Sections**: {len(sections)}",
        f"- **Ingested**: {now_iso()}",
        "",
        "---",
        "",
    ]
    manifest_sections: list[dict[str, Any]] = []
    queued_items: list[dict[str, Any]] = []

    for sec in sections:
        ts_link = f"[{sec['startTimestamp']}]({canonical_url}&t={int(sec['startSec'])}s)"
        end_link = f"[{sec['endTimestamp']}]({canonical_url}&t={int(sec['endSec'])}s)"
        heading = f"Section {sec['index']} ({sec['startTimestamp']}–{sec['endTimestamp']})"
        md_lines.extend(
            [
                f"## {heading}",
                "",
                f"Jump: {ts_link} → {end_link}" + (f" · Key terms: {', '.join(f'`{t}`' for t in sec['keyTerms'])}" if sec["keyTerms"] else ""),
                "",
                sec["text"],
                "",
                f"> [!IMPORTANT] Active Pause-Point #{sec['index']}",
                f"> {sec['pausePrompt']}",
                "",
            ]
        )
        manifest_sections.append(
            {
                "index": sec["index"],
                "heading": heading,
                "startSec": sec["startSec"],
                "endSec": sec["endSec"],
                "startTimestamp": sec["startTimestamp"],
                "endTimestamp": sec["endTimestamp"],
                "durationSec": sec["durationSec"],
                "wordCount": sec["wordCount"],
                "keyTerms": sec["keyTerms"],
                "pausePrompt": sec["pausePrompt"],
                "timestampUrl": f"{canonical_url}&t={int(sec['startSec'])}s",
            }
        )

    lecture_path = ensure_within_dir(out_dir, out_dir / "lecture.md")
    write_text_atomic(lecture_path, "\n".join(md_lines).rstrip() + "\n")

    if enqueue:
        for sec in manifest_sections:
            q_item = queue_add(
                title=f"{final_title} — {sec['heading']}",
                source_path=str(lecture_path.relative_to(ws)),
                workspace=ws,
                slug=final_slug,
                priority=priority,
                stage="queued",
                heading=sec["heading"],
                excerpt=sec["pausePrompt"],
            )
            queued_items.append(q_item)

    manifest = {
        "schemaVersion": 2,
        "kind": "youtube",
        "slug": final_slug,
        "title": final_title,
        "subject": subject,
        "url": canonical_url,
        "videoId": video_id,
        "captionOnly": True,
        "captionBackend": caption_backend,
        "durationSec": total_duration,
        "durationFormatted": _format_mmss(total_duration),
        "segmentCount": len(segments),
        "sectionCount": len(manifest_sections),
        "lectureFile": str(lecture_path.relative_to(ws)),
        "sections": manifest_sections,
        "queuedCount": len(queued_items),
        "ingestedAt": now_iso(),
    }
    manifest_path = ensure_within_dir(out_dir, out_dir / "manifest.json")
    save_json_atomic(manifest_path, manifest)

    tracker_info: dict[str, Any] | None = None
    if register_tracker:
        tracker_info = _register_with_tracker(
            workspace=ws,
            slug=final_slug,
            title=final_title,
            subject=subject,
            source_id=f"yt-{video_id[:6].lower()}",
            source_label=f"YouTube Lecture: {final_title}",
            source_path=lecture_path,
            source_kind="notes",
            notes=f"Caption-only YouTube ingest ({len(manifest_sections)} pause-point sections)",
        )

    return {
        "ok": True,
        "kind": "youtube",
        "slug": final_slug,
        "title": final_title,
        "videoId": video_id,
        "captionOnly": True,
        "captionBackend": caption_backend,
        "durationSec": total_duration,
        "sectionCount": len(manifest_sections),
        "outputDir": str(out_dir),
        "lecturePath": str(lecture_path),
        "manifestPath": str(manifest_path),
        "queuedCount": len(queued_items),
        "tracker": tracker_info,
    }


# ---------------------------------------------------------------------------
# 2. arXiv 1-Shot Paper Ingestion & Keshav 3-Pass Guide
# ---------------------------------------------------------------------------


def normalize_arxiv_id(raw: str) -> str:
    """Normalize a bare arXiv ID (`1706.03762`) or arXiv URL to canonical ID."""
    s = str(raw or "").strip()
    if not s:
        raise ValueError("arXiv identifier cannot be empty.")
    m = ARXIV_ID_RE.match(s)
    if m:
        return m.group("id")
    parsed = validate_http_url(s)
    host = (parsed.hostname or "").lower()
    if host not in ("arxiv.org", "www.arxiv.org", "export.arxiv.org"):
        raise ValueError(f"Not an arXiv URL: {s!r}")
    path = parsed.path.strip("/")
    m_url = re.match(r"^(?:abs|pdf|html|e-print)/(.+?)(?:\.pdf)?$", path)
    if m_url:
        cand = m_url.group(1).strip("/")
        m2 = ARXIV_ID_RE.match(cand)
        if m2:
            return m2.group("id")
    raise ValueError(f"Could not normalize arXiv ID from {raw!r}.")


def parse_arxiv_atom(xml_data: str | bytes, fallback_id: str = "") -> dict[str, Any]:
    """Parse arXiv Atom API XML into structured metadata."""
    xml_str = xml_data.decode("utf-8", errors="replace") if isinstance(xml_data, bytes) else str(xml_data)
    root = ET.fromstring(xml_str.strip())
    ns = {
        "atom": "http://www.w3.org/2005/Atom",
        "arxiv": "http://arxiv.org/schemas/atom",
    }
    entry = root.find("atom:entry", ns)
    if entry is None:
        raise ValueError("No <entry> element found in arXiv Atom XML response.")

    raw_id = (entry.findtext("atom:id", default="", namespaces=ns) or "").strip()
    title = " ".join((entry.findtext("atom:title", default="", namespaces=ns) or "").split())
    summary = " ".join((entry.findtext("atom:summary", default="", namespaces=ns) or "").split())
    published = (entry.findtext("atom:published", default="", namespaces=ns) or "").strip()
    updated = (entry.findtext("atom:updated", default="", namespaces=ns) or "").strip()
    comment = " ".join((entry.findtext("arxiv:comment", default="", namespaces=ns) or "").split())
    doi = (entry.findtext("arxiv:doi", default="", namespaces=ns) or "").strip() or None

    authors = [
        " ".join((a.findtext("atom:name", default="", namespaces=ns) or "").split())
        for a in entry.findall("atom:author", ns)
        if (a.findtext("atom:name", default="", namespaces=ns) or "").strip()
    ]
    categories = [
        str(c.attrib.get("term") or "").strip()
        for c in entry.findall("atom:category", ns)
        if str(c.attrib.get("term") or "").strip()
    ]
    prim_el = entry.find("arxiv:primary_category", ns)
    primary_category = str(prim_el.attrib.get("term") or "").strip() if prim_el is not None else (categories[0] if categories else "")

    arxiv_id = fallback_id
    if raw_id:
        try:
            arxiv_id = normalize_arxiv_id(raw_id)
        except ValueError:
            pass

    pdf_url = f"https://arxiv.org/pdf/{arxiv_id}.pdf" if arxiv_id else None
    for link in entry.findall("atom:link", ns):
        if link.attrib.get("title") == "pdf" or link.attrib.get("type") == "application/pdf":
            href = (link.attrib.get("href") or "").strip()
            if href:
                pdf_url = href
                break

    year = int(published[:4]) if len(published) >= 4 and published[:4].isdigit() else None
    return {
        "arxivId": arxiv_id,
        "title": title or f"arXiv:{arxiv_id}",
        "authors": authors,
        "year": year,
        "published": published,
        "updated": updated,
        "summary": summary,
        "categories": categories,
        "primaryCategory": primary_category,
        "comment": comment or None,
        "doi": doi,
        "absUrl": f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else raw_id,
        "pdfUrl": pdf_url,
    }


def _render_keshav_guide(meta: dict[str, Any], pdf_rel: str | None) -> str:
    authors_str = ", ".join(meta["authors"][:8]) + (" et al." if len(meta["authors"]) > 8 else "")
    cats_str = ", ".join(meta.get("categories") or []) or "unspecified"
    key_terms = _extract_key_terms(f"{meta['title']} {meta['summary']}", limit=6)
    lines = [
        f"# Keshav 3-Pass Study Guide: {meta['title']}",
        "",
        f"- **arXiv ID**: `{meta['arxivId']}`",
        f"- **Authors**: {authors_str or 'Unknown'}",
        f"- **Published**: {meta.get('published') or 'Unknown'} · **Categories**: `{cats_str}`",
        f"- **Abstract URL**: [{meta['absUrl']}]({meta['absUrl']})",
        f"- **PDF**: {f'`{pdf_rel}`' if pdf_rel else meta.get('pdfUrl') or 'Not downloaded (pass `--download-pdf`)'}",
        "",
        "## Abstract",
        "",
        meta["summary"] or "_No abstract available._",
        "",
        "---",
        "",
        "## Pass 1: The 5 Cs (5–10 min Bird's-Eye Triage)",
        "",
        "Read ONLY the title, abstract, introduction, section headings, and conclusions. Answer from memory:",
        "",
        f"- [ ] **Category**: What kind of paper is this? (primary tag: `{meta.get('primaryCategory') or 'TBD'}` — measurement, architecture, proof, systems, survey?)",
        "- [ ] **Context**: Which prior papers/baselines does this directly respond to, and what bottleneck did they hit?",
        "- [ ] **Correctness**: Do the core assumptions and evaluation setup appear valid on first glance?",
        "- [ ] **Contributions**: State the 1–3 concrete claims/contributions in your own words.",
        "- [ ] **Clarity**: Is the notation and structure clear enough to warrant Pass 2?",
        "",
        f"> [!IMPORTANT] Pass-1 Gate",
        f"> Before starting Pass 2, summarize the paper's central claim in <= 2 sentences using your own words (watch terms: {', '.join(f'`{t}`' for t in key_terms) or 'key claims'}).",
        "",
        "---",
        "",
        "## Pass 2: Figures, Diagrams & Key Equations (30–60 min)",
        "",
        "Read with greater care, ignoring full proofs. Inspect every figure, plot, and equation carefully.",
        "",
        "| Figure / Eq | What It Claims | Axes / Units / Variables | Unresolved Question |",
        "| :--- | :--- | :--- | :--- |",
        "| Fig 1 / Eq (1) | _Fill after reading_ | _Check units & domains_ | _Open doubt_ |",
        "",
        "- [ ] **Architecture / Mechanism Trace**: Trace one concrete input end-to-end through the main diagram or algorithm.",
        "- [ ] **Key References**: List 2–3 cited papers you must understand to read Pass 3 without hand-waving.",
        "",
        "---",
        "",
        "## Pass 3: Assumptions, Re-Derivation & Virtual Re-Implementation",
        "",
        "Virtually re-implement the paper: starting from the same assumptions, re-derive the core theorem/algorithm and compare with the authors.",
        "",
        "- [ ] **Hidden Assumptions**: Which unstated assumptions (i.i.d., convexity, bounded latency, memory budget, dataset split) carry the result?",
        "- [ ] **Re-Derivation**: Re-derive the load-bearing equation or write a minimal toy implementation without looking at the paper.",
        "- [ ] **Failure Mode / Stress-Test**: Construct one concrete boundary input or ablation where the paper's method fails or degrades.",
        "- [ ] **Crystallize to Knowledge Graph**: Capture 2–4 unconditional propositions + prerequisite chains in `knowledge-graph/propositions.jsonl`.",
        "",
    ]
    return "\n".join(lines)


def ingest_arxiv(
    id_or_url: str,
    *,
    workspace: str | Path | None = None,
    slug: str | None = None,
    download_pdf: bool = False,
    atom_xml: str | bytes | None = None,
    atom_file: str | Path | None = None,
    register_tracker: bool = False,
    enqueue: bool = True,
    subject: str = "General",
    priority: int = 75,
    timeout: float = 15.0,
) -> dict[str, Any]:
    """Ingest an arXiv paper via Atom API, optional official PDF, and Keshav 3-Pass guide."""
    ws = resolve_workspace(workspace)
    arxiv_id = normalize_arxiv_id(id_or_url)

    if atom_xml is not None:
        raw_atom: str | bytes = atom_xml
    elif atom_file is not None:
        apath = Path(atom_file).expanduser().resolve()
        if not apath.is_file():
            raise FileNotFoundError(f"Atom XML file not found: {apath}")
        raw_atom = apath.read_bytes()
    else:
        api_url = f"https://export.arxiv.org/api/query?id_list={urllib.parse.quote(arxiv_id)}"
        raw_atom = _http_get(api_url, timeout=timeout, accept="application/atom+xml")

    meta = parse_arxiv_atom(raw_atom, fallback_id=arxiv_id)
    final_slug = slug if slug else slugify(f"arxiv-{arxiv_id}-{meta['title'][:40]}")
    out_dir = ingested_dir_for(ws, final_slug)
    out_dir.mkdir(parents=True, exist_ok=True)

    pdf_path: Path | None = None
    pdf_rel: str | None = None
    pdf_inspect: dict[str, Any] | None = None
    if download_pdf and meta.get("pdfUrl"):
        pdf_bytes = _http_get(
            str(meta["pdfUrl"]),
            timeout=max(30.0, timeout),
            accept="application/pdf",
            max_bytes=50 * 1024 * 1024,
        )
        safe_pdf_name = re.sub(r"[^A-Za-z0-9._-]+", "_", arxiv_id) + ".pdf"
        pdf_path = ensure_within_dir(out_dir, out_dir / safe_pdf_name)
        pdf_path.write_bytes(pdf_bytes)
        pdf_rel = str(pdf_path.relative_to(ws))
        pdf_studio = resolve_package_root() / "scripts" / "pdf_studio.py"
        if pdf_studio.is_file():
            try:
                env = dict(os.environ)
                env["PYTHONDONTWRITEBYTECODE"] = "1"
                res = subprocess.run(
                    [sys.executable, "-B", str(pdf_studio), "inspect", str(pdf_path), "--json"],
                    capture_output=True,
                    text=True,
                    env=env,
                    timeout=20,
                    check=False,
                )
                if res.returncode == 0 and res.stdout.strip():
                    pdf_inspect = json.loads(res.stdout)
            except Exception:  # noqa: BLE001
                pdf_inspect = None

    guide_md = _render_keshav_guide(meta, pdf_rel)
    guide_path = ensure_within_dir(out_dir, out_dir / "keshav-3-pass.md")
    write_text_atomic(guide_path, guide_md)

    passes = [
        {"pass": 1, "heading": "Pass 1: The 5 Cs (5–10 min Bird's-Eye Triage)", "priority": priority},
        {"pass": 2, "heading": "Pass 2: Figures, Diagrams & Key Equations (30–60 min)", "priority": max(10, priority - 5)},
        {"pass": 3, "heading": "Pass 3: Assumptions, Re-Derivation & Virtual Re-Implementation", "priority": max(10, priority - 10)},
    ]
    queued_items: list[dict[str, Any]] = []
    if enqueue:
        for pinfo in passes:
            queued_items.append(
                queue_add(
                    title=f"{meta['title']} — {pinfo['heading']}",
                    source_path=str(guide_path.relative_to(ws)),
                    workspace=ws,
                    slug=final_slug,
                    priority=int(pinfo["priority"]),
                    stage="queued",
                    heading=str(pinfo["heading"]),
                    excerpt=meta["summary"][:220],
                )
            )

    manifest = {
        "schemaVersion": 2,
        "kind": "arxiv",
        "slug": final_slug,
        "subject": subject,
        **meta,
        "guideFile": str(guide_path.relative_to(ws)),
        "pdfFile": pdf_rel,
        "pdfInspect": pdf_inspect,
        "keshavPasses": passes,
        "queuedCount": len(queued_items),
        "ingestedAt": now_iso(),
    }
    manifest_path = ensure_within_dir(out_dir, out_dir / "manifest.json")
    save_json_atomic(manifest_path, manifest)

    tracker_info: dict[str, Any] | None = None
    if register_tracker:
        tracker_info = _register_with_tracker(
            workspace=ws,
            slug=final_slug,
            title=meta["title"],
            subject=subject,
            source_id=f"arxiv-{slugify(arxiv_id, 16)}",
            source_label=f"arXiv:{arxiv_id} — {meta['title'][:60]}",
            source_path=pdf_path or guide_path,
            source_kind="paper_pdf" if pdf_path else "notes",
            notes="Keshav 3-Pass arXiv ingestion",
        )

    return {
        "ok": True,
        "kind": "arxiv",
        "slug": final_slug,
        "arxivId": meta["arxivId"],
        "title": meta["title"],
        "authors": meta["authors"],
        "outputDir": str(out_dir),
        "guidePath": str(guide_path),
        "pdfPath": str(pdf_path) if pdf_path else None,
        "manifestPath": str(manifest_path),
        "queuedCount": len(queued_items),
        "tracker": tracker_info,
    }


# ---------------------------------------------------------------------------
# 3. GitHub / Local Repository Scanner -> Architecture & Golden-Path Manifest
# ---------------------------------------------------------------------------


def _is_probably_binary(path: Path) -> bool:
    if path.suffix.lower() in BINARY_EXTS:
        return True
    try:
        with path.open("rb") as fh:
            chunk = fh.read(1024)
        return b"\x00" in chunk
    except OSError:
        return True


def _extract_symbols(path: Path, text: str, max_symbols: int = 8) -> list[str]:
    ext = path.suffix.lower()
    symbols: list[str] = []
    if ext == ".py":
        for m in re.finditer(r"^(?:async\s+)?(?:def|class)\s+([A-Za-z_][A-Za-z0-9_]*)", text, re.MULTILINE):
            name = m.group(1)
            if not name.startswith("_"):
                symbols.append(name)
    elif ext in (".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx"):
        for m in re.finditer(
            r"^(?:export\s+(?:default\s+)?)?(?:async\s+)?(?:function|class|const)\s+([A-Za-z_$][A-Za-z0-9_$]*)",
            text,
            re.MULTILINE,
        ):
            symbols.append(m.group(1))
    elif ext == ".rs":
        for m in re.finditer(r"^\s*pub\s+(?:async\s+)?(?:fn|struct|enum|trait)\s+([A-Za-z_][A-Za-z0-9_]*)", text, re.MULTILINE):
            symbols.append(m.group(1))
    elif ext == ".go":
        for m in re.finditer(r"^func\s+(?:\([^)]+\)\s+)?([A-Z][A-Za-z0-9_]*)", text, re.MULTILINE):
            symbols.append(m.group(1))
    elif ext in (".md", ".markdown"):
        for m in re.finditer(r"^#{1,2}\s+(.+)$", text, re.MULTILINE):
            symbols.append(m.group(1).strip()[:50])
    # Deduplicate preserving order
    seen: set[str] = set()
    uniq: list[str] = []
    for sym in symbols:
        if sym not in seen:
            seen.add(sym)
            uniq.append(sym)
        if len(uniq) >= max_symbols:
            break
    return uniq


def _is_test_file(rel_posix: str) -> bool:
    low = rel_posix.lower()
    parts = low.split("/")
    name = parts[-1]
    if any(p in ("test", "tests", "__tests__", "spec", "specs") for p in parts[:-1]):
        return True
    return (
        name.startswith("test_")
        or name.endswith("_test.py")
        or name.endswith("_test.go")
        or ".test." in name
        or ".spec." in name
    )


def _scan_local_repo(repo_root: Path, *, max_files: int = 2000) -> dict[str, Any]:
    repo_root = repo_root.resolve()
    lang_files: Counter[str] = Counter()
    lang_lines: Counter[str] = Counter()
    entry_points: list[str] = []
    test_files: list[str] = []
    modules: list[dict[str, Any]] = []
    total_files = 0
    total_lines = 0
    skipped_binary = 0

    for dirpath, dirnames, filenames in os.walk(repo_root, topdown=True, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if d not in REPO_IGNORED_DIRS and not d.startswith(".git"))
        for fname in sorted(filenames):
            if total_files >= max_files:
                break
            fpath = Path(dirpath) / fname
            if fpath.is_symlink():
                continue
            rel = fpath.relative_to(repo_root).as_posix()
            if _is_probably_binary(fpath):
                skipped_binary += 1
                continue
            try:
                size = fpath.stat().st_size
                if size > 512 * 1024:
                    skipped_binary += 1
                    continue
                text = fpath.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue

            total_files += 1
            loc = text.count("\n") + (1 if text and not text.endswith("\n") else 0)
            total_lines += loc
            ext = fpath.suffix.lower()
            lang = EXT_TO_LANG.get(ext, ext.lstrip(".").upper() if ext else "Text")
            lang_files[lang] += 1
            lang_lines[lang] += loc

            is_entry = fname in ENTRY_POINT_NAMES or rel in ENTRY_POINT_NAMES
            is_test = _is_test_file(rel)
            if is_entry:
                entry_points.append(rel)
            if is_test:
                test_files.append(rel)

            symbols = _extract_symbols(fpath, text)
            modules.append(
                {
                    "path": rel,
                    "language": lang,
                    "lines": loc,
                    "bytes": size,
                    "isEntryPoint": is_entry,
                    "isTest": is_test,
                    "symbols": symbols,
                }
            )
        if total_files >= max_files:
            break

    # Rank key modules: non-test code modules prioritizing entry points, symbol richness, and moderate depth
    def _mod_rank(m: dict[str, Any]) -> tuple[int, int, int, str]:
        depth = m["path"].count("/")
        is_code = 1 if m["language"] not in ("Markdown", "JSON", "YAML", "TOML", "Text") else 0
        score = (
            (40 if m["isEntryPoint"] else 0)
            + (20 if is_code and not m["isTest"] else 0)
            + min(30, len(m["symbols"]) * 4)
            + min(20, m["lines"] // 25)
            - depth * 3
        )
        return (-score, depth, -m["lines"], m["path"])

    key_modules = sorted([m for m in modules if not m["isTest"]], key=_mod_rank)[:40]
    languages = [
        {"language": lang, "files": lang_files[lang], "lines": lang_lines[lang]}
        for lang, _ in lang_lines.most_common()
    ]
    return {
        "repoRoot": str(repo_root),
        "totalFiles": total_files,
        "totalLines": total_lines,
        "skippedBinaryOrLarge": skipped_binary,
        "languages": languages,
        "entryPoints": entry_points[:25],
        "keyModules": key_modules,
        "testFiles": test_files[:40],
    }


def _render_repo_map(title: str, source_ref: str, scan: dict[str, Any], token_budget: int = 4000) -> str:
    char_budget = max(1200, int(token_budget) * 4)
    lines: list[str] = [
        f"# Architecture & Golden-Path Manifest: {title}",
        "",
        f"- **Source**: `{source_ref}`",
        f"- **Files Scanned**: {scan['totalFiles']} · **Lines**: {scan['totalLines']} · **Skipped Binaries/Large**: {scan['skippedBinaryOrLarge']}",
        f"- **Token Budget**: ~{token_budget} tokens · **Generated**: {now_iso()}",
        "",
        "## Language Breakdown",
        "",
        "| Language | Files | Lines |",
        "| :--- | ---: | ---: |",
    ]
    for lang in scan["languages"][:10]:
        lines.append(f"| {lang['language']} | {lang['files']} | {lang['lines']} |")

    lines.extend(["", "## Entry Points", ""])
    if scan["entryPoints"]:
        for ep in scan["entryPoints"][:12]:
            lines.append(f"- `{ep}`")
    else:
        lines.append("- _(No conventional entry-point filename detected; start from the top key module below.)_")

    # Golden-Path 3-step trace
    primary_ep = next((p for p in scan["entryPoints"] if not p.lower().endswith((".md", ".toml", ".json"))), None) or (
        scan["entryPoints"][0] if scan["entryPoints"] else (scan["keyModules"][0]["path"] if scan["keyModules"] else "N/A")
    )
    core_mod = next((m["path"] for m in scan["keyModules"] if m["path"] != primary_ep), primary_ep)
    first_test = scan["testFiles"][0] if scan["testFiles"] else "(no test files found)"
    lines.extend(
        [
            "",
            "## Golden-Path Trace (Read 1 Real Execution Flow First)",
            "",
            f"1. **Entry**: Open `{primary_ep}` and trace how inputs/config enter the system.",
            f"2. **Core Engine**: Follow the primary call into `{core_mod}` and map its key data invariants.",
            f"3. **Verification**: Inspect `{first_test}` to see the concrete contract and edge cases.",
            "",
            "## Key Modules",
            "",
        ]
    )

    current_chars = sum(len(ln) + 1 for ln in lines)
    for mod in scan["keyModules"]:
        syms = f" — symbols: {', '.join(f'`{s}`' for s in mod['symbols'][:6])}" if mod["symbols"] else ""
        entry = f"- `{mod['path']}` ({mod['language']}, {mod['lines']}L){syms}"
        if current_chars + len(entry) + 300 > char_budget:
            lines.append("- _(Remaining modules truncated to respect token budget; see `manifest.json`.)_")
            break
        lines.append(entry)
        current_chars += len(entry) + 1

    lines.extend(["", "## Test Suite", ""])
    if scan["testFiles"]:
        for tf in scan["testFiles"][:15]:
            entry = f"- `{tf}`"
            if current_chars + len(entry) + 50 > char_budget:
                lines.append(f"- _(+{len(scan['testFiles']) - 15} more test files in manifest.json)_")
                break
            lines.append(entry)
            current_chars += len(entry) + 1
    else:
        lines.append("- _(No automated test files detected.)_")

    return "\n".join(lines).rstrip() + "\n"


def ingest_repo(
    path_or_url: str,
    *,
    workspace: str | Path | None = None,
    slug: str | None = None,
    clone: bool = False,
    token_budget: int = 4000,
    register_tracker: bool = False,
    enqueue: bool = False,
    subject: str = "Computer Science",
    priority: int = 70,
    timeout: float = 45.0,
) -> dict[str, Any]:
    """Scan a local repository or shallow-clone an HTTPS GitHub URL into a Golden-Path Manifest."""
    ws = resolve_workspace(workspace)
    raw = str(path_or_url or "").strip()
    is_remote = bool(re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", raw))

    if is_remote:
        parsed = validate_http_url(raw)
        if parsed.scheme.lower() != "https":
            raise ValueError("Remote repository URLs must use https:// for safety.")
        host = (parsed.hostname or "").lower()
        parts = [p for p in parsed.path.strip("/").split("/") if p]
        if len(parts) < 2:
            raise ValueError(f"Expected repository URL like https://github.com/<owner>/<repo>, got {raw!r}.")
        repo_name = parts[1].removesuffix(".git")
        final_slug = slug if slug else slugify(f"repo-{repo_name}")
        out_dir = ingested_dir_for(ws, final_slug)
        out_dir.mkdir(parents=True, exist_ok=True)
        title = f"{parts[0]}/{repo_name}"

        if clone:
            git_bin = find_binary("git")
            if not git_bin:
                raise RuntimeError("`git` binary not found; cannot shallow-clone remote repository.")
            clone_dir = ensure_within_dir(out_dir, out_dir / "repo")
            if clone_dir.exists():
                shutil.rmtree(clone_dir)
            cmd = [
                git_bin,
                "-c",
                "core.hooksPath=/dev/null",
                "clone",
                "--depth",
                "1",
                "--single-branch",
                "--",
                raw,
                str(clone_dir),
            ]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
            if res.returncode != 0:
                raise RuntimeError(f"git clone failed: {(res.stderr or res.stdout).strip()}")
            scan = _scan_local_repo(clone_dir)
            source_ref = raw
        elif host in ("github.com", "www.github.com"):
            # Keyless GitHub tree API scan (zero clone)
            api_url = f"https://api.github.com/repos/{urllib.parse.quote(parts[0])}/{urllib.parse.quote(repo_name)}/git/trees/HEAD?recursive=1"
            tree_data = json.loads(_http_get(api_url, timeout=timeout, accept="application/vnd.github+json").decode("utf-8", errors="replace"))
            tree = tree_data.get("tree") or []
            lang_files: Counter[str] = Counter()
            entry_points: list[str] = []
            test_files: list[str] = []
            key_modules: list[dict[str, Any]] = []
            total_files = 0
            for item in tree:
                if not isinstance(item, dict) or item.get("type") != "blob":
                    continue
                rel = str(item.get("path") or "")
                if any(seg in REPO_IGNORED_DIRS for seg in rel.split("/")):
                    continue
                ext = Path(rel).suffix.lower()
                if ext in BINARY_EXTS:
                    continue
                total_files += 1
                lang = EXT_TO_LANG.get(ext, ext.lstrip(".").upper() if ext else "Text")
                lang_files[lang] += 1
                fname = Path(rel).name
                is_ep = fname in ENTRY_POINT_NAMES
                is_t = _is_test_file(rel)
                if is_ep:
                    entry_points.append(rel)
                if is_t:
                    test_files.append(rel)
                if not is_t and len(key_modules) < 40:
                    key_modules.append(
                        {
                            "path": rel,
                            "language": lang,
                            "lines": max(1, int(item.get("size") or 0) // 40),
                            "bytes": int(item.get("size") or 0),
                            "isEntryPoint": is_ep,
                            "isTest": False,
                            "symbols": [],
                        }
                    )
            scan = {
                "repoRoot": raw,
                "totalFiles": total_files,
                "totalLines": sum(m["lines"] for m in key_modules),
                "skippedBinaryOrLarge": 0,
                "languages": [{"language": k, "files": v, "lines": 0} for k, v in lang_files.most_common()],
                "entryPoints": entry_points[:25],
                "keyModules": key_modules,
                "testFiles": test_files[:40],
            }
            source_ref = raw
        else:
            raise ValueError("Pass --clone to shallow-clone non-GitHub HTTPS repositories.")
    else:
        local_path = Path(raw).expanduser().resolve()
        if not local_path.is_dir():
            raise FileNotFoundError(f"Repository directory not found: {local_path}")
        title = local_path.name
        final_slug = slug if slug else slugify(f"repo-{local_path.name}")
        out_dir = ingested_dir_for(ws, final_slug)
        out_dir.mkdir(parents=True, exist_ok=True)
        scan = _scan_local_repo(local_path)
        source_ref = str(local_path)

    repo_map_md = _render_repo_map(title, source_ref, scan, token_budget=token_budget)
    repo_map_path = ensure_within_dir(out_dir, out_dir / "repo-map.md")
    write_text_atomic(repo_map_path, repo_map_md)

    queued_items: list[dict[str, Any]] = []
    if enqueue:
        queued_items.append(
            queue_add(
                title=f"Repo Golden Path: {title}",
                source_path=str(repo_map_path.relative_to(ws)),
                workspace=ws,
                slug=final_slug,
                priority=priority,
                stage="queued",
                heading="Golden-Path Trace",
                excerpt=f"{scan['totalFiles']} files, entry points: {', '.join(scan['entryPoints'][:3])}",
            )
        )

    manifest = {
        "schemaVersion": 2,
        "kind": "repo",
        "slug": final_slug,
        "title": title,
        "subject": subject,
        "source": source_ref,
        "cloned": bool(is_remote and clone),
        "tokenBudget": token_budget,
        "repoMapFile": str(repo_map_path.relative_to(ws)),
        "totalFiles": scan["totalFiles"],
        "totalLines": scan["totalLines"],
        "languages": scan["languages"],
        "entryPoints": scan["entryPoints"],
        "keyModules": scan["keyModules"],
        "testFiles": scan["testFiles"],
        "queuedCount": len(queued_items),
        "ingestedAt": now_iso(),
    }
    manifest_path = ensure_within_dir(out_dir, out_dir / "manifest.json")
    save_json_atomic(manifest_path, manifest)

    tracker_info: dict[str, Any] | None = None
    if register_tracker:
        tracker_info = _register_with_tracker(
            workspace=ws,
            slug=final_slug,
            title=title,
            subject=subject,
            source_id=f"repo-{slugify(final_slug, 14)}",
            source_label=f"Repo Map: {title}",
            source_path=repo_map_path,
            source_kind="code_repo",
            notes=f"{scan['totalFiles']} files, {len(scan['entryPoints'])} entry points",
        )

    return {
        "ok": True,
        "kind": "repo",
        "slug": final_slug,
        "title": title,
        "totalFiles": scan["totalFiles"],
        "totalLines": scan["totalLines"],
        "entryPoints": scan["entryPoints"],
        "testFiles": scan["testFiles"],
        "outputDir": str(out_dir),
        "repoMapPath": str(repo_map_path),
        "manifestPath": str(manifest_path),
        "queuedCount": len(queued_items),
        "tracker": tracker_info,
    }


# ---------------------------------------------------------------------------
# 4. Web Article / Docs Ingestion (Strict http/https + HTML->Markdown + Slices)
# ---------------------------------------------------------------------------


class _HTMLToMarkdownParser(HTMLParser):
    """Lightweight pure-stdlib HTML -> clean Markdown extractor."""

    SKIP_TAGS = frozenset({"script", "style", "noscript", "svg", "iframe", "nav", "footer", "aside", "form"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._in_title = False
        self._in_pre = False
        self._in_code = False
        self._in_blockquote = False
        self._link_href: str | None = None
        self._link_text: list[str] = []
        self._list_stack: list[tuple[str, int]] = []
        self.title_parts: list[str] = []
        self.out: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        t = tag.lower()
        attr_map = dict(attrs)
        if t in self.SKIP_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth > 0:
            return
        if t == "title":
            self._in_title = True
            return
        if t in ("h1", "h2", "h3", "h4", "h5", "h6"):
            level = int(t[1])
            self.out.append(f"\n\n{'#' * level} ")
        elif t == "p":
            self.out.append("\n\n")
        elif t == "br":
            self.out.append("\n")
        elif t == "hr":
            self.out.append("\n\n---\n\n")
        elif t == "pre":
            self._in_pre = True
            self.out.append("\n\n```\n")
        elif t == "code":
            if not self._in_pre:
                self._in_code = True
                self.out.append("`")
        elif t == "blockquote":
            self._in_blockquote = True
            self.out.append("\n\n> ")
        elif t in ("strong", "b"):
            self.out.append("**")
        elif t in ("em", "i"):
            self.out.append("*")
        elif t == "ul":
            self._list_stack.append(("ul", 0))
            self.out.append("\n")
        elif t == "ol":
            self._list_stack.append(("ol", 0))
            self.out.append("\n")
        elif t == "li":
            indent = "  " * max(0, len(self._list_stack) - 1)
            if self._list_stack and self._list_stack[-1][0] == "ol":
                kind, idx = self._list_stack[-1]
                idx += 1
                self._list_stack[-1] = (kind, idx)
                self.out.append(f"\n{indent}{idx}. ")
            else:
                self.out.append(f"\n{indent}- ")
        elif t == "a":
            href = (attr_map.get("href") or "").strip()
            if href and not href.lower().startswith("javascript:"):
                self._link_href = href
                self._link_text = []

    def handle_endtag(self, tag: str) -> None:
        t = tag.lower()
        if t in self.SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if self._skip_depth > 0:
            return
        if t == "title":
            self._in_title = False
            return
        if t in ("h1", "h2", "h3", "h4", "h5", "h6", "p"):
            self.out.append("\n\n")
        elif t == "pre":
            self._in_pre = False
            self.out.append("\n```\n\n")
        elif t == "code":
            if not self._in_pre and self._in_code:
                self._in_code = False
                self.out.append("`")
        elif t == "blockquote":
            self._in_blockquote = False
            self.out.append("\n\n")
        elif t in ("strong", "b"):
            self.out.append("**")
        elif t in ("em", "i"):
            self.out.append("*")
        elif t in ("ul", "ol"):
            if self._list_stack:
                self._list_stack.pop()
            self.out.append("\n\n")
        elif t == "a" and self._link_href is not None:
            label = " ".join("".join(self._link_text).split())
            if label:
                self.out.append(f"[{label}]({self._link_href})")
            self._link_href = None
            self._link_text = []

    def handle_data(self, data: str) -> None:
        if self._skip_depth > 0:
            return
        if self._in_title:
            self.title_parts.append(data)
            return
        if self._link_href is not None:
            self._link_text.append(data)
            return
        if self._in_pre:
            self.out.append(data)
            return
        collapsed = re.sub(r"[ \t\r\n]+", " ", data)
        if self._in_blockquote:
            collapsed = collapsed.replace("\n", "\n> ")
        self.out.append(collapsed)


def html_to_markdown(raw_html: str) -> tuple[str, str]:
    """Convert raw HTML to `(title, clean_markdown)` using stdlib `html.parser`."""
    parser = _HTMLToMarkdownParser()
    parser.feed(raw_html)
    parser.close()
    title = " ".join("".join(parser.title_parts).split()).strip()
    md = "".join(parser.out)
    # Normalize blank lines while preserving fenced code blocks
    md = re.sub(r"[ \t]+\n", "\n", md)
    md = re.sub(r"\n{3,}", "\n\n", md).strip()
    if not title:
        m = re.search(r"^#\s+(.+)$", md, re.MULTILINE)
        if m:
            title = m.group(1).strip()
    return title, md


def chunk_markdown_slices(
    md_text: str,
    *,
    target_words: int = 600,
    max_words: int = 900,
) -> list[dict[str, Any]]:
    """Split a Markdown document into incremental reading slices along headings/paragraphs."""
    blocks = [b.strip() for b in re.split(r"\n{2,}", md_text.strip()) if b.strip()]
    if not blocks:
        return []

    slices: list[dict[str, Any]] = []
    cur_blocks: list[str] = []
    cur_words = 0
    cur_heading = "Introduction"

    def _flush() -> None:
        nonlocal cur_blocks, cur_words
        if not cur_blocks:
            return
        idx = len(slices) + 1
        text = "\n\n".join(cur_blocks).strip()
        slices.append(
            {
                "index": idx,
                "sliceId": f"slice-{idx:02d}",
                "heading": cur_heading,
                "wordCount": len(text.split()),
                "text": text,
                "excerpt": " ".join(text.split())[:220],
            }
        )
        cur_blocks = []
        cur_words = 0

    for blk in blocks:
        is_heading = bool(re.match(r"^#{1,6}\s+", blk))
        blk_words = len(blk.split())
        if is_heading and cur_blocks and cur_words >= max(120, target_words // 3):
            _flush()
        if is_heading:
            cur_heading = re.sub(r"^#{1,6}\s+", "", blk).strip()[:100]
        if cur_blocks and cur_words + blk_words > max_words:
            _flush()
        cur_blocks.append(blk)
        cur_words += blk_words
        if cur_words >= target_words and not is_heading:
            _flush()

    _flush()
    return slices


def ingest_url(
    url: str,
    *,
    workspace: str | Path | None = None,
    slug: str | None = None,
    html_text: str | None = None,
    html_file: str | Path | None = None,
    priority: int = 60,
    register_tracker: bool = False,
    enqueue: bool = True,
    subject: str = "General",
    timeout: float = 15.0,
) -> dict[str, Any]:
    """Ingest a web article/doc after strict http/https URL validation into slices + reading queue."""
    parsed = validate_http_url(url)
    ws = resolve_workspace(workspace)

    if html_text is not None:
        raw_html = html_text
    elif html_file is not None:
        hpath = Path(html_file).expanduser().resolve()
        if not hpath.is_file():
            raise FileNotFoundError(f"HTML file not found: {hpath}")
        raw_html = hpath.read_text(encoding="utf-8", errors="replace")
    else:
        raw_html = _http_get(
            url,
            timeout=timeout,
            accept="text/html,application/xhtml+xml,text/markdown,text/plain;q=0.9,*/*;q=0.5",
        ).decode("utf-8", errors="replace")

    is_html = bool(re.search(r"<(?:html|body|article|main|p|h1|div)\b", raw_html[:2000], re.IGNORECASE))
    if is_html:
        title, md_body = html_to_markdown(raw_html)
    else:
        md_body = raw_html.strip()
        m_h = re.search(r"^#\s+(.+)$", md_body, re.MULTILINE)
        title = m_h.group(1).strip() if m_h else ""

    if not title:
        tail = Path(parsed.path.strip("/")).name or parsed.hostname or "web-article"
        title = tail.replace("-", " ").replace("_", " ").strip() or "Web Article"

    final_slug = slug if slug else slugify(f"web-{title[:48]}")
    out_dir = ingested_dir_for(ws, final_slug)
    out_dir.mkdir(parents=True, exist_ok=True)

    header = "\n".join(
        [
            f"# {title}",
            "",
            f"- **Source URL**: [{url}]({url})",
            f"- **Host**: `{parsed.hostname}`",
            f"- **Ingested**: {now_iso()}",
            "",
            "---",
            "",
        ]
    )
    article_path = ensure_within_dir(out_dir, out_dir / "article.md")
    write_text_atomic(article_path, header + md_body + "\n")

    slices = chunk_markdown_slices(md_body)
    slices_dir = ensure_within_dir(out_dir, out_dir / "slices")
    slices_dir.mkdir(parents=True, exist_ok=True)
    manifest_slices: list[dict[str, Any]] = []
    queued_items: list[dict[str, Any]] = []

    for sl in slices:
        sl_path = ensure_within_dir(slices_dir, slices_dir / f"{sl['sliceId']}.md")
        sl_content = f"# {title} — {sl['heading']}\n\n_Source: {url}_\n\n{sl['text']}\n"
        write_text_atomic(sl_path, sl_content)
        rel_sl = str(sl_path.relative_to(ws))
        manifest_slices.append(
            {
                "sliceId": sl["sliceId"],
                "heading": sl["heading"],
                "wordCount": sl["wordCount"],
                "file": rel_sl,
            }
        )
        if enqueue:
            queued_items.append(
                queue_add(
                    title=f"{title} [{sl['sliceId']}: {sl['heading']}]",
                    source_path=rel_sl,
                    workspace=ws,
                    slug=final_slug,
                    priority=priority,
                    stage="queued",
                    heading=sl["heading"],
                    excerpt=sl["excerpt"],
                )
            )

    manifest = {
        "schemaVersion": 2,
        "kind": "url",
        "slug": final_slug,
        "title": title,
        "subject": subject,
        "url": url,
        "host": parsed.hostname,
        "articleFile": str(article_path.relative_to(ws)),
        "wordCount": len(md_body.split()),
        "sliceCount": len(manifest_slices),
        "slices": manifest_slices,
        "queuedCount": len(queued_items),
        "ingestedAt": now_iso(),
    }
    manifest_path = ensure_within_dir(out_dir, out_dir / "manifest.json")
    save_json_atomic(manifest_path, manifest)

    tracker_info: dict[str, Any] | None = None
    if register_tracker:
        tracker_info = _register_with_tracker(
            workspace=ws,
            slug=final_slug,
            title=title,
            subject=subject,
            source_id=f"web-{slugify(final_slug, 14)}",
            source_label=title[:70],
            source_path=article_path,
            source_kind="official_docs",
            notes=f"{len(manifest_slices)} incremental reading slices from {parsed.hostname}",
        )

    return {
        "ok": True,
        "kind": "url",
        "slug": final_slug,
        "title": title,
        "url": url,
        "wordCount": len(md_body.split()),
        "sliceCount": len(manifest_slices),
        "outputDir": str(out_dir),
        "articlePath": str(article_path),
        "manifestPath": str(manifest_path),
        "queuedCount": len(queued_items),
        "tracker": tracker_info,
    }


# ---------------------------------------------------------------------------
# 5. Notes / Obsidian Folder / PDF Ingestion
# ---------------------------------------------------------------------------


def ingest_notes(
    path: str | Path,
    *,
    workspace: str | Path | None = None,
    slug: str | None = None,
    priority: int = 60,
    register_tracker: bool = False,
    enqueue: bool = True,
    subject: str = "General",
) -> dict[str, Any]:
    """Copy and index local Markdown, Obsidian notes, or PDFs into `sources/_ingested/<slug>/`."""
    ws = resolve_workspace(workspace)
    src = Path(path).expanduser().resolve()
    if not src.exists():
        raise FileNotFoundError(f"Notes path not found: {src}")

    title = src.stem if src.is_file() else src.name
    final_slug = slug if slug else slugify(f"notes-{title}")
    out_dir = ingested_dir_for(ws, final_slug)
    out_dir.mkdir(parents=True, exist_ok=True)
    files_dir = ensure_within_dir(out_dir, out_dir / "files")
    files_dir.mkdir(parents=True, exist_ok=True)

    sources_list: list[Path] = []
    if src.is_file():
        sources_list = [src]
    else:
        for dp, dns, fns in os.walk(src, topdown=True, followlinks=False):
            dns[:] = sorted(d for d in dns if d not in REPO_IGNORED_DIRS and not d.startswith("."))
            for fn in sorted(fns):
                fp = Path(dp) / fn
                if fp.is_symlink():
                    continue
                if fp.suffix.lower() in NOTE_FILE_EXTS:
                    sources_list.append(fp)

    indexed_files: list[dict[str, Any]] = []
    queued_items: list[dict[str, Any]] = []
    pdf_studio = resolve_package_root() / "scripts" / "pdf_studio.py"

    for fp in sources_list:
        rel_src = fp.name if src.is_file() else fp.relative_to(src).as_posix()
        dest = ensure_within_dir(files_dir, files_dir / rel_src)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(fp, dest)
        ext = fp.suffix.lower()
        rel_ws = str(dest.relative_to(ws))
        entry: dict[str, Any] = {
            "name": fp.name,
            "file": rel_ws,
            "ext": ext,
            "bytes": dest.stat().st_size,
        }
        if ext == ".pdf":
            entry["type"] = "pdf"
            if pdf_studio.is_file():
                try:
                    env = dict(os.environ)
                    env["PYTHONDONTWRITEBYTECODE"] = "1"
                    res = subprocess.run(
                        [sys.executable, "-B", str(pdf_studio), "inspect", str(dest), "--json"],
                        capture_output=True,
                        text=True,
                        env=env,
                        timeout=15,
                        check=False,
                    )
                    if res.returncode == 0 and res.stdout.strip():
                        info = json.loads(res.stdout)
                        entry["pages"] = info.get("pages") or info.get("totalPages")
                except Exception:  # noqa: BLE001
                    pass
            if enqueue:
                queued_items.append(
                    queue_add(
                        title=f"PDF: {fp.stem}",
                        source_path=rel_ws,
                        workspace=ws,
                        slug=final_slug,
                        priority=priority,
                        stage="queued",
                        heading=fp.name,
                        excerpt=f"Ingested PDF ({entry.get('pages', '?')} pages)",
                    )
                )
        else:
            text = dest.read_text(encoding="utf-8", errors="replace")
            headings = [m.group(1).strip() for m in re.finditer(r"^#{1,3}\s+(.+)$", text, re.MULTILINE)]
            wikilinks = sorted(set(re.findall(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]", text)))
            entry["type"] = "markdown" if ext in (".md", ".markdown") else "text"
            entry["wordCount"] = len(text.split())
            entry["headings"] = headings[:20]
            entry["wikilinks"] = wikilinks[:30]
            if enqueue:
                queued_items.append(
                    queue_add(
                        title=headings[0] if headings else fp.stem,
                        source_path=rel_ws,
                        workspace=ws,
                        slug=final_slug,
                        priority=priority,
                        stage="queued",
                        heading=headings[0] if headings else fp.stem,
                        excerpt=" ".join(text.split())[:220],
                    )
                )
        indexed_files.append(entry)

    index_lines = [
        f"# Ingested Notes: {title}",
        "",
        f"- **Origin**: `{src}`",
        f"- **Files Indexed**: {len(indexed_files)} · **Ingested**: {now_iso()}",
        "",
        "## Files",
        "",
    ]
    for item in indexed_files:
        detail = f"{item.get('wordCount', 0)}w" if item.get("type") != "pdf" else f"PDF, {item['bytes']}B"
        index_lines.append(f"- `{item['file']}` ({detail})")

    index_path = ensure_within_dir(out_dir, out_dir / "index.md")
    write_text_atomic(index_path, "\n".join(index_lines).rstrip() + "\n")

    manifest = {
        "schemaVersion": 2,
        "kind": "notes",
        "slug": final_slug,
        "title": title,
        "subject": subject,
        "origin": str(src),
        "indexFile": str(index_path.relative_to(ws)),
        "fileCount": len(indexed_files),
        "files": indexed_files,
        "queuedCount": len(queued_items),
        "ingestedAt": now_iso(),
    }
    manifest_path = ensure_within_dir(out_dir, out_dir / "manifest.json")
    save_json_atomic(manifest_path, manifest)

    tracker_info: dict[str, Any] | None = None
    if register_tracker:
        primary_file = Path(ws / indexed_files[0]["file"]) if len(indexed_files) == 1 else index_path
        src_kind = "textbook_pdf" if primary_file.suffix.lower() == ".pdf" else "notes"
        tracker_info = _register_with_tracker(
            workspace=ws,
            slug=final_slug,
            title=title,
            subject=subject,
            source_id=f"notes-{slugify(final_slug, 14)}",
            source_label=f"Notes: {title}",
            source_path=primary_file,
            source_kind=src_kind,
            notes=f"{len(indexed_files)} file(s) ingested",
        )

    return {
        "ok": True,
        "kind": "notes",
        "slug": final_slug,
        "title": title,
        "fileCount": len(indexed_files),
        "outputDir": str(out_dir),
        "indexPath": str(index_path),
        "manifestPath": str(manifest_path),
        "queuedCount": len(queued_items),
        "tracker": tracker_info,
    }


def ingest_anki(apkg_path: str | Path, **kwargs: Any) -> dict[str, Any]:
    """Convenience wrapper routing `.apkg` ingestion to `syntopik.importers.import_anki_apkg`."""
    from syntopik.importers import import_anki_apkg

    return import_anki_apkg(apkg_path, **{k: v for k, v in kwargs.items() if k in _ANKI_KWARGS})


def ingest_cards(path: str | Path, **kwargs: Any) -> dict[str, Any]:
    """Convenience wrapper routing CSV/TSV flashcard decks to `syntopik.importers.import_delimited_cards`."""
    from syntopik.importers import import_delimited_cards

    return import_delimited_cards(path, **{k: v for k, v in kwargs.items() if k in _CARDS_KWARGS})


def ingest_notebook(ipynb_path: str | Path, **kwargs: Any) -> dict[str, Any]:
    """Convenience wrapper routing `.ipynb` notebooks to `syntopik.importers.import_jupyter_notebook`."""
    from syntopik.importers import import_jupyter_notebook

    return import_jupyter_notebook(ipynb_path, **{k: v for k, v in kwargs.items() if k in _NOTEBOOK_KWARGS})


def ingest_notes_batch(path_or_zip: str | Path, **kwargs: Any) -> dict[str, Any]:
    """Convenience wrapper routing Markdown folders / Notion `.zip` exports to `syntopik.importers.import_markdown_folder`."""
    from syntopik.importers import import_markdown_folder

    return import_markdown_folder(path_or_zip, **{k: v for k, v in kwargs.items() if k in _NOTES_BATCH_KWARGS})


def ingest_auto(target: str, **kwargs: Any) -> dict[str, Any]:
    """Auto-detect source type for `target` and dispatch to the matching importer."""
    detected = detect_source_type(target)
    if detected == "youtube":
        res = ingest_youtube(target, **{k: v for k, v in kwargs.items() if k in _YOUTUBE_KWARGS})
    elif detected == "arxiv":
        res = ingest_arxiv(target, **{k: v for k, v in kwargs.items() if k in _ARXIV_KWARGS})
    elif detected == "repo":
        res = ingest_repo(target, **{k: v for k, v in kwargs.items() if k in _REPO_KWARGS})
    elif detected == "url":
        res = ingest_url(target, **{k: v for k, v in kwargs.items() if k in _URL_KWARGS})
    elif detected == "notes":
        res = ingest_notes(target, **{k: v for k, v in kwargs.items() if k in _NOTES_KWARGS})
    elif detected == "anki":
        res = ingest_anki(target, **kwargs)
    elif detected == "cards":
        res = ingest_cards(target, **kwargs)
    elif detected == "notebook":
        res = ingest_notebook(target, **kwargs)
    elif detected == "notes_batch":
        res = ingest_notes_batch(target, **kwargs)
    else:
        raise ValueError(f"Unsupported detected source type: {detected!r}")
    res["detectedKind"] = detected
    return res


_YOUTUBE_KWARGS = frozenset({"workspace", "slug", "lang", "vtt_text", "vtt_file", "register_tracker", "enqueue", "subject", "title", "priority", "timeout"})
_ARXIV_KWARGS = frozenset({"workspace", "slug", "download_pdf", "atom_xml", "atom_file", "register_tracker", "enqueue", "subject", "priority", "timeout"})
_REPO_KWARGS = frozenset({"workspace", "slug", "clone", "token_budget", "register_tracker", "enqueue", "subject", "priority", "timeout"})
_URL_KWARGS = frozenset({"workspace", "slug", "html_text", "html_file", "priority", "register_tracker", "enqueue", "subject", "timeout"})
_NOTES_KWARGS = frozenset({"workspace", "slug", "priority", "register_tracker", "enqueue", "subject"})
_ANKI_KWARGS = frozenset({"workspace", "kg_dir", "subject", "chapter", "limit", "dry_run"})
_CARDS_KWARGS = frozenset({"workspace", "kg_dir", "delimiter", "subject", "chapter", "limit", "dry_run"})
_NOTEBOOK_KWARGS = frozenset({"workspace", "kg_dir", "slug", "subject", "chapter", "create_chapter", "enqueue", "dry_run"})
_NOTES_BATCH_KWARGS = frozenset({"workspace", "kg_dir", "slug", "subject", "extract_props", "enqueue", "limit", "dry_run"})


# ---------------------------------------------------------------------------
# 6. Incremental Reading Queue (`knowledge-graph/reading-queue.jsonl`)
# ---------------------------------------------------------------------------


def _reading_queue_path(workspace: str | Path | None = None, kg_dir: str | Path | None = None) -> Path:
    return resolve_kg_dir(workspace=workspace, explicit_kg_dir=kg_dir) / "reading-queue.jsonl"


def load_reading_queue(workspace: str | Path | None = None, kg_dir: str | Path | None = None) -> list[dict[str, Any]]:
    return load_jsonl(_reading_queue_path(workspace, kg_dir))


def _parse_iso_date(val: str | None, default_date: date) -> date:
    if not val:
        return default_date
    try:
        return date.fromisoformat(str(val)[:10])
    except ValueError:
        return default_date


def compute_queue_rank(item: dict[str, Any], today_date: date | None = None) -> float:
    """Compute hybrid `priority * urgency` score for an incremental reading item."""
    ref_today = today_date or date.fromisoformat(today_iso())
    priority = max(0.0, min(100.0, float(item.get("priority", 50))))
    stage = str(item.get("stage") or "queued")
    if stage == "crystallized":
        return 0.0
    due_d = _parse_iso_date(item.get("nextReview"), ref_today)
    delta_days = (ref_today - due_d).days
    if delta_days >= 0:
        urgency = 1.0 + delta_days * 0.25
    else:
        urgency = 1.0 / (1.0 + abs(delta_days))
    return round(priority * urgency, 3)


def queue_add(
    title: str,
    source_path: str,
    *,
    workspace: str | Path | None = None,
    kg_dir: str | Path | None = None,
    slug: str = "general",
    priority: int = 50,
    stage: str = "queued",
    heading: str = "",
    excerpt: str = "",
    item_id: str | None = None,
) -> dict[str, Any]:
    """Idempotently add or refresh an extract in `knowledge-graph/reading-queue.jsonl`."""
    clean_stage = str(stage or "queued").strip().lower()
    if clean_stage not in READING_STAGES:
        raise ValueError(f"Invalid stage {stage!r}; expected one of {READING_STAGES}.")
    clamped_priority = max(0, min(100, int(priority)))
    qpath = _reading_queue_path(workspace, kg_dir)
    rows = load_jsonl(qpath)

    fingerprint = hashlib.sha1(f"{slug}|{source_path}|{heading}|{title}".encode("utf-8")).hexdigest()[:8]
    cid = item_id.strip() if item_id and item_id.strip() else f"ir-{slugify(slug, 24)}-{fingerprint}"

    existing = next((r for r in rows if r.get("id") == cid), None)
    now = now_iso()
    today = today_iso()
    if existing is not None:
        existing["title"] = title
        existing["priority"] = clamped_priority
        existing["updatedAt"] = now
        if heading:
            existing["heading"] = heading
        if excerpt:
            existing["excerpt"] = excerpt[:320]
        save_jsonl_atomic(qpath, rows)
        return existing

    entry = {
        "id": cid,
        "slug": slugify(slug, 40),
        "title": title,
        "sourcePath": str(source_path),
        "heading": heading,
        "excerpt": excerpt[:320],
        "priority": clamped_priority,
        "stage": clean_stage,
        "createdAt": now,
        "updatedAt": now,
        "nextReview": today if clean_stage != "crystallized" else None,
        "intervalDays": STAGE_INTERVAL_DAYS[clean_stage],
        "touches": 0,
        "linkedPropIds": [],
    }
    rows.append(entry)
    save_jsonl_atomic(qpath, rows)
    return entry


def queue_due(
    *,
    workspace: str | Path | None = None,
    kg_dir: str | Path | None = None,
    limit: int = 10,
    include_future: bool = False,
    stage: str | None = None,
    today: str | None = None,
) -> dict[str, Any]:
    """Return reading queue extracts ranked by hybrid `priority * urgency`."""
    qpath = _reading_queue_path(workspace, kg_dir)
    rows = load_jsonl(qpath)
    ref_today = date.fromisoformat(today[:10]) if today else date.fromisoformat(today_iso())

    scored: list[dict[str, Any]] = []
    for row in rows:
        row_stage = str(row.get("stage") or "queued")
        if row_stage == "crystallized" and stage != "crystallized":
            continue
        if stage and row_stage != stage:
            continue
        due_d = _parse_iso_date(row.get("nextReview"), ref_today)
        overdue_days = (ref_today - due_d).days
        if not include_future and overdue_days < 0:
            continue
        rank = compute_queue_rank(row, today_date=ref_today)
        enriched = dict(row)
        enriched["overdueDays"] = max(0, overdue_days)
        enriched["urgency"] = round(1.0 + overdue_days * 0.25 if overdue_days >= 0 else 1.0 / (1.0 + abs(overdue_days)), 3)
        enriched["rankScore"] = rank
        scored.append(enriched)

    scored.sort(key=lambda r: (-float(r["rankScore"]), -int(r.get("priority", 0)), str(r.get("id", ""))))
    items = scored[: max(1, int(limit))]
    return {
        "ok": True,
        "queuePath": str(qpath),
        "today": ref_today.isoformat(),
        "totalQueued": len(rows),
        "dueCount": len(scored),
        "items": items,
    }


def queue_advance(
    item_id: str,
    *,
    workspace: str | Path | None = None,
    kg_dir: str | Path | None = None,
    to_stage: str | None = None,
    priority: int | None = None,
    linked_prop_id: str | None = None,
    today: str | None = None,
) -> dict[str, Any]:
    """Advance an incremental reading queue item (`queued -> extracted -> clozed -> crystallized`)."""
    qpath = _reading_queue_path(workspace, kg_dir)
    rows = load_jsonl(qpath)
    target = next((r for r in rows if r.get("id") == item_id), None)
    if target is None:
        raise KeyError(f"Reading queue item '{item_id}' not found in {qpath}.")

    prev_stage = str(target.get("stage") or "queued")
    if to_stage is not None:
        clean_next = str(to_stage).strip().lower()
        if clean_next not in READING_STAGES:
            raise ValueError(f"Invalid target stage {to_stage!r}; expected one of {READING_STAGES}.")
    else:
        idx = READING_STAGES.index(prev_stage) if prev_stage in READING_STAGES else 0
        clean_next = READING_STAGES[min(len(READING_STAGES) - 1, idx + 1)]

    ref_today = date.fromisoformat(today[:10]) if today else date.fromisoformat(today_iso())
    interval = STAGE_INTERVAL_DAYS[clean_next]
    next_due = (ref_today + timedelta(days=interval)).isoformat() if interval is not None else None

    target["stage"] = clean_next
    target["intervalDays"] = interval
    target["nextReview"] = next_due
    target["touches"] = int(target.get("touches") or 0) + 1
    target["updatedAt"] = now_iso()
    if priority is not None:
        target["priority"] = max(0, min(100, int(priority)))
    if linked_prop_id:
        linked = list(target.get("linkedPropIds") or [])
        if linked_prop_id not in linked:
            linked.append(linked_prop_id)
        target["linkedPropIds"] = linked

    save_jsonl_atomic(qpath, rows)
    return {
        "ok": True,
        "id": item_id,
        "previousStage": prev_stage,
        "stage": clean_next,
        "nextReview": next_due,
        "item": target,
    }


# ---------------------------------------------------------------------------
# CLI Parser & Entrypoint
# ---------------------------------------------------------------------------


def _add_common_flags(p: argparse.ArgumentParser) -> None:
    p.add_argument("--workspace", default=argparse.SUPPRESS, help="Workspace root override (or SYNT_WORKSPACE)")
    p.add_argument("--slug", default=None, help="Explicit output slug under sources/_ingested/<slug>/")
    p.add_argument("--subject", default="General", help="Subject label (e.g., Physics, ML, Computer Science)")
    p.add_argument("--register-tracker", action="store_true", help="Register ingested source in chapters/<slug>-tracker.json")
    p.add_argument("--no-enqueue", action="store_true", help="Do not add sections/slices to reading-queue.jsonl")
    p.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="Emit structured JSON output")


def _emit(payload: dict[str, Any], as_json: bool, summary_lines: list[str]) -> None:
    if as_json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        for ln in summary_lines:
            print(ln)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ingest_source.py",
        description="Syntopik v2.0 — 1-Command Universal Source Importer & Incremental Reading Queue",
    )
    parser.add_argument("--workspace", default=None, help="Workspace root override (or SYNT_WORKSPACE)")
    parser.add_argument("--json", action="store_true", default=False, help="Emit structured JSON output")
    sub = parser.add_subparsers(dest="cmd", required=True)

    # auto
    p_auto = sub.add_parser("auto", help="Auto-detect source type (YouTube, arXiv, repo, URL, notes) and ingest")
    p_auto.add_argument("target", help="URL, arXiv ID, GitHub URL, or local file/folder path")
    p_auto.add_argument("--lang", default="en", help="Preferred YouTube caption language (default: en)")
    p_auto.add_argument("--vtt-file", default=None, help="Offline WebVTT file for YouTube ingestion")
    p_auto.add_argument("--atom-file", default=None, help="Offline Atom XML file for arXiv ingestion")
    p_auto.add_argument("--html-file", default=None, help="Offline HTML file for URL ingestion")
    p_auto.add_argument("--download-pdf", action="store_true", help="Download official arXiv PDF")
    p_auto.add_argument("--clone", action="store_true", help="Shallow-clone remote HTTPS GitHub repository")
    p_auto.add_argument("--token-budget", type=int, default=4000, help="Token budget for repo-map.md")
    p_auto.add_argument("--priority", type=int, default=65, help="Initial reading queue priority (0-100)")
    _add_common_flags(p_auto)

    # youtube
    p_yt = sub.add_parser("youtube", help="Caption-only YouTube lecture ingestion (5-8 min sections + pause-points)")
    p_yt.add_argument("url", help="YouTube video URL or 11-char video ID")
    p_yt.add_argument("--lang", default="en", help="Preferred caption language (default: en)")
    p_yt.add_argument("--title", default=None, help="Override lecture title")
    p_yt.add_argument("--vtt-file", default=None, help="Optional local .vtt/.xml caption file (offline mode)")
    p_yt.add_argument("--priority", type=int, default=65, help="Initial reading queue priority (0-100)")
    _add_common_flags(p_yt)

    # arxiv
    p_ax = sub.add_parser("arxiv", help="arXiv paper metadata, optional PDF, and Keshav 3-Pass guide")
    p_ax.add_argument("id_or_url", help="Bare arXiv ID (e.g., 1706.03762) or arxiv.org URL")
    p_ax.add_argument("--download-pdf", action="store_true", help="Download official arXiv PDF")
    p_ax.add_argument("--atom-file", default=None, help="Optional local Atom XML file (offline mode)")
    p_ax.add_argument("--priority", type=int, default=75, help="Initial reading queue priority (0-100)")
    _add_common_flags(p_ax)

    # repo
    p_repo = sub.add_parser("repo", help="Local or GitHub repository -> Architecture & Golden-Path Manifest")
    p_repo.add_argument("path_or_url", help="Local directory path or HTTPS GitHub URL")
    p_repo.add_argument("--clone", action="store_true", help="Shallow depth-1 clone for remote HTTPS GitHub URL")
    p_repo.add_argument("--token-budget", type=int, default=4000, help="Token budget for repo-map.md (default: 4000)")
    p_repo.add_argument("--enqueue", action="store_true", help="Add Golden-Path trace to reading-queue.jsonl")
    p_repo.add_argument("--priority", type=int, default=70, help="Reading queue priority (0-100)")
    _add_common_flags(p_repo)

    # url
    p_url = sub.add_parser("url", help="Web article/docs (strict http/https) -> Markdown + incremental slices")
    p_url.add_argument("url", help="http:// or https:// URL")
    p_url.add_argument("--html-file", default=None, help="Optional local HTML file (offline mode)")
    p_url.add_argument("--priority", type=int, default=60, help="Initial reading queue priority (0-100)")
    _add_common_flags(p_url)

    # notes
    p_notes = sub.add_parser("notes", help="Local Markdown, Obsidian folder, or PDF -> sources/_ingested/<slug>/")
    p_notes.add_argument("path", help="Path to Markdown file, PDF, or notes folder")
    p_notes.add_argument("--priority", type=int, default=60, help="Initial reading queue priority (0-100)")
    _add_common_flags(p_notes)

    # anki
    p_anki = sub.add_parser("anki", help="Import Anki .apkg deck as unseen planned propositions")
    p_anki.add_argument("path", help="Path to .apkg file")
    p_anki.add_argument("--chapter", default=None, help="Chapter label override")
    p_anki.add_argument("--limit", type=int, default=500, help="Max cards to import (default: 500)")
    p_anki.add_argument("--dry-run", action="store_true", help="Parse and preview without writing")
    _add_common_flags(p_anki)

    # cards
    p_cards = sub.add_parser("cards", help="Import CSV/TSV flashcard deck (Anki/Quizlet) as planned propositions")
    p_cards.add_argument("path", help="Path to .csv or .tsv file")
    p_cards.add_argument("--delimiter", default=None, help="Explicit delimiter override")
    p_cards.add_argument("--chapter", default=None, help="Chapter label override")
    p_cards.add_argument("--limit", type=int, default=500, help="Max cards to import (default: 500)")
    p_cards.add_argument("--dry-run", action="store_true", help="Parse and preview without writing")
    _add_common_flags(p_cards)

    # notebook
    p_nb = sub.add_parser("notebook", help="Statically import Jupyter .ipynb notebook (zero code execution)")
    p_nb.add_argument("path", help="Path to .ipynb file")
    p_nb.add_argument("--chapter", default=None, help="Chapter label override")
    p_nb.add_argument("--create-chapter", action="store_true", help="Also emit chapters/<slug>.md")
    p_nb.add_argument("--dry-run", action="store_true", help="Parse and preview without writing")
    _add_common_flags(p_nb)

    # notes_batch
    p_nbatch = sub.add_parser("notes_batch", help="Import Markdown/Obsidian folder or Notion .zip export")
    p_nbatch.add_argument("path", help="Path to Markdown folder or Notion .zip archive")
    p_nbatch.add_argument("--limit", type=int, default=500, help="Max propositions to extract (default: 500)")
    p_nbatch.add_argument("--no-extract-props", action="store_true", help="Skip proposition extraction")
    p_nbatch.add_argument("--dry-run", action="store_true", help="Parse and preview without writing")
    _add_common_flags(p_nbatch)

    # queue
    p_q = sub.add_parser("queue", help="Manage Incremental Reading Queue (knowledge-graph/reading-queue.jsonl)")
    p_q.add_argument("--workspace", default=argparse.SUPPRESS, help="Workspace root override")
    p_q.add_argument("--kg-dir", default=None, help="Explicit knowledge-graph directory override")
    p_q.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="Emit JSON output")
    q_sub = p_q.add_subparsers(dest="queue_cmd", required=True)

    p_qa = q_sub.add_parser("add", help="Add an extract to reading-queue.jsonl")
    p_qa.add_argument("--title", required=True, help="Extract title")
    p_qa.add_argument("--source-path", required=True, help="Source file path")
    p_qa.add_argument("--slug", default="general", help="Topic/source slug")
    p_qa.add_argument("--priority", type=int, default=50, help="Priority 0-100 (default: 50)")
    p_qa.add_argument("--stage", default="queued", choices=READING_STAGES, help="Initial stage (default: queued)")
    p_qa.add_argument("--heading", default="", help="Section heading anchor")
    p_qa.add_argument("--excerpt", default="", help="Short extract snippet")
    p_qa.add_argument("--id", dest="item_id", default=None, help="Optional explicit item ID")
    p_qa.add_argument("--workspace", default=argparse.SUPPRESS)
    p_qa.add_argument("--kg-dir", default=argparse.SUPPRESS)
    p_qa.add_argument("--json", action="store_true", default=argparse.SUPPRESS)

    p_qd = q_sub.add_parser("due", help="List due extracts ranked by priority * urgency")
    p_qd.add_argument("--limit", type=int, default=10, help="Max items to return (default: 10)")
    p_qd.add_argument("--include-future", action="store_true", help="Include not-yet-due items")
    p_qd.add_argument("--stage", default=None, choices=READING_STAGES, help="Filter by stage")
    p_qd.add_argument("--today", default=None, help="Override today YYYY-MM-DD (for deterministic testing)")
    p_qd.add_argument("--workspace", default=argparse.SUPPRESS)
    p_qd.add_argument("--kg-dir", default=argparse.SUPPRESS)
    p_qd.add_argument("--json", action="store_true", default=argparse.SUPPRESS)

    p_qadv = q_sub.add_parser("advance", help="Advance an item's stage (queued -> extracted -> clozed -> crystallized)")
    p_qadv.add_argument("item_id", help="Reading queue item ID")
    p_qadv.add_argument("--to-stage", default=None, choices=READING_STAGES, help="Explicit destination stage")
    p_qadv.add_argument("--priority", type=int, default=None, help="Optional updated priority (0-100)")
    p_qadv.add_argument("--linked-prop-id", default=None, help="Link crystallized KG proposition ID")
    p_qadv.add_argument("--today", default=None, help="Override today YYYY-MM-DD")
    p_qadv.add_argument("--workspace", default=argparse.SUPPRESS)
    p_qadv.add_argument("--kg-dir", default=argparse.SUPPRESS)
    p_qadv.add_argument("--json", action="store_true", default=argparse.SUPPRESS)

    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    ws = getattr(args, "workspace", None)
    as_json = bool(getattr(args, "json", False))

    try:
        if args.cmd == "auto":
            res = ingest_auto(
                args.target,
                workspace=ws,
                slug=args.slug,
                lang=args.lang,
                vtt_file=args.vtt_file,
                atom_file=args.atom_file,
                html_file=args.html_file,
                download_pdf=args.download_pdf,
                clone=args.clone,
                token_budget=args.token_budget,
                priority=args.priority,
                register_tracker=args.register_tracker,
                enqueue=not args.no_enqueue,
                subject=args.subject,
            )
            dest_label = res.get("outputDir") or res.get("propsFile") or ws
            _emit(res, as_json, [f"Ingested ({res['detectedKind']}) -> {dest_label} [slug={res.get('slug', '')}]"])
        elif args.cmd == "anki":
            res = ingest_anki(
                args.path,
                workspace=ws,
                subject=args.subject,
                chapter=args.chapter,
                limit=args.limit,
                dry_run=args.dry_run,
            )
            _emit(res, as_json, [f"Anki imported ({res.get('importedCount', 0)} planned, {res.get('deduplicatedCount', 0)} dupes)"])
        elif args.cmd == "cards":
            res = ingest_cards(
                args.path,
                workspace=ws,
                delimiter=args.delimiter,
                subject=args.subject,
                chapter=args.chapter,
                limit=args.limit,
                dry_run=args.dry_run,
            )
            _emit(res, as_json, [f"Cards imported ({res.get('importedCount', 0)} planned, {res.get('deduplicatedCount', 0)} dupes)"])
        elif args.cmd == "notebook":
            res = ingest_notebook(
                args.path,
                workspace=ws,
                slug=args.slug,
                subject=args.subject if args.subject != "General" else "Computer Science",
                chapter=args.chapter,
                create_chapter=args.create_chapter,
                enqueue=not args.no_enqueue,
                dry_run=args.dry_run,
            )
            _emit(res, as_json, [f"Notebook imported ({res.get('importedCount', 0)} props, {res.get('extractedCasesCount', 0)} test cases) -> {res.get('notebookPath')}"])
        elif args.cmd == "notes_batch":
            res = ingest_notes_batch(
                args.path,
                workspace=ws,
                slug=args.slug,
                subject=args.subject,
                extract_props=not args.no_extract_props,
                enqueue=not args.no_enqueue,
                limit=args.limit,
                dry_run=args.dry_run,
            )
            _emit(res, as_json, [f"Notes batch imported ({res.get('fileCount', 0)} files, {res.get('importedCount', 0)} props) -> {res.get('indexPath')}"])
        elif args.cmd == "youtube":
            res = ingest_youtube(
                args.url,
                workspace=ws,
                slug=args.slug,
                lang=args.lang,
                title=args.title,
                vtt_file=args.vtt_file,
                priority=args.priority,
                register_tracker=args.register_tracker,
                enqueue=not args.no_enqueue,
                subject=args.subject,
            )
            _emit(
                res,
                as_json,
                [f"YouTube captions ingested ({res['sectionCount']} pause-point sections) -> {res['lecturePath']}"],
            )
        elif args.cmd == "arxiv":
            res = ingest_arxiv(
                args.id_or_url,
                workspace=ws,
                slug=args.slug,
                download_pdf=args.download_pdf,
                atom_file=args.atom_file,
                priority=args.priority,
                register_tracker=args.register_tracker,
                enqueue=not args.no_enqueue,
                subject=args.subject,
            )
            _emit(res, as_json, [f"arXiv:{res['arxivId']} Keshav 3-Pass guide -> {res['guidePath']}"])
        elif args.cmd == "repo":
            res = ingest_repo(
                args.path_or_url,
                workspace=ws,
                slug=args.slug,
                clone=args.clone,
                token_budget=args.token_budget,
                register_tracker=args.register_tracker,
                enqueue=bool(args.enqueue and not args.no_enqueue),
                subject=args.subject if args.subject != "General" else "Computer Science",
                priority=args.priority,
            )
            _emit(
                res,
                as_json,
                [f"Repo manifest ({res['totalFiles']} files, {res['totalLines']}L) -> {res['repoMapPath']}"],
            )
        elif args.cmd == "url":
            res = ingest_url(
                args.url,
                workspace=ws,
                slug=args.slug,
                html_file=args.html_file,
                priority=args.priority,
                register_tracker=args.register_tracker,
                enqueue=not args.no_enqueue,
                subject=args.subject,
            )
            _emit(
                res,
                as_json,
                [f"Web article ({res['sliceCount']} slices, {res['wordCount']}w) -> {res['articlePath']}"],
            )
        elif args.cmd == "notes":
            res = ingest_notes(
                args.path,
                workspace=ws,
                slug=args.slug,
                priority=args.priority,
                register_tracker=args.register_tracker,
                enqueue=not args.no_enqueue,
                subject=args.subject,
            )
            _emit(res, as_json, [f"Notes indexed ({res['fileCount']} files) -> {res['indexPath']}"])
        elif args.cmd == "queue":
            kg_dir = getattr(args, "kg_dir", None)
            if args.queue_cmd == "add":
                item = queue_add(
                    title=args.title,
                    source_path=args.source_path,
                    workspace=ws,
                    kg_dir=kg_dir,
                    slug=args.slug,
                    priority=args.priority,
                    stage=args.stage,
                    heading=args.heading,
                    excerpt=args.excerpt,
                    item_id=args.item_id,
                )
                _emit({"ok": True, "item": item}, as_json, [f"Queued [{item['id']}] p={item['priority']} ({item['stage']}): {item['title']}"])
            elif args.queue_cmd == "due":
                res = queue_due(
                    workspace=ws,
                    kg_dir=kg_dir,
                    limit=args.limit,
                    include_future=args.include_future,
                    stage=args.stage,
                    today=args.today,
                )
                lines = [f"Reading Queue Due ({res['dueCount']}/{res['totalQueued']} total):"]
                for it in res["items"]:
                    lines.append(
                        f"  [{it['id']}] score={it['rankScore']} (p={it['priority']} x u={it['urgency']}) "
                        f"stage={it['stage']} — {it['title']}"
                    )
                if not res["items"]:
                    lines.append("  (none due)")
                _emit(res, as_json, lines)
            elif args.queue_cmd == "advance":
                res = queue_advance(
                    args.item_id,
                    workspace=ws,
                    kg_dir=kg_dir,
                    to_stage=args.to_stage,
                    priority=args.priority,
                    linked_prop_id=args.linked_prop_id,
                    today=args.today,
                )
                _emit(
                    res,
                    as_json,
                    [f"Advanced [{res['id']}]: {res['previousStage']} -> {res['stage']} (nextReview={res['nextReview']})"],
                )
    except (ValueError, FileNotFoundError, KeyError, RuntimeError, urllib.error.URLError) as exc:
        if as_json:
            print(json.dumps({"ok": False, "error": str(exc)}, indent=2, ensure_ascii=False))
        else:
            print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
