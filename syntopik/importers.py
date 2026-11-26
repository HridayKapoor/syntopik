"""Syntopik v3.2 — Pure-Stdlib Universal Student Content Importers.

Pure Python 3.12+ standard library ONLY (zero external dependencies).
Supports 4 universal student knowledge formats with strict security &
pedagogical invariants:
  1. `import_anki_apkg`       — Anki `.apkg` (`collection.anki21` / `.anki2`,
                                 graceful `.anki21b` zstd detection, Basic & Cloze)
  2. `import_delimited_cards` — Anki `#separator:` TSV/CSV, Quizlet TSV/CSV,
                                 BOM-safe auto-detected delimited decks
  3. `import_jupyter_notebook`— `.ipynb` pure `json.loads` + `ast.parse` (NEVER
                                 executes code!) -> Markdown, planned props, and
                                 `scripts/code_verify.py` `code-tests.json`
  4. `import_markdown_folder` — Local Markdown/Obsidian folders & Notion `.zip`
                                 exports (32/36-char UUID stripping, link rewriting,
                                 YAML frontmatter, `:::cue`, `{{cN::...}}`, `Q::/A::`)

Security & Pedagogical Non-Negotiables:
  - Strict zip-slip, POSIX symlink/special-file, and bounded 64 KiB streaming
    anti-zip-bomb guards (`MAX_ZIP_TOTAL = 50 MiB`, `MAX_FILE = 10 MiB`,
    `MAX_ENTRIES = 500`, `MAX_CARDS = 500` default cap).
  - NEVER executes notebook code cells (`ast.parse` + `ast.literal_eval` ONLY).
  - Strips all HTML/script/style/iframe/object/embed tags via stdlib `HTMLParser`,
    converts `\\(...\\)` / `\\[...\\]` to `$...$` / `$$...$$`, removes `[sound:...]`.
  - ZERO fake mastery: every imported proposition is created strictly with
    `status: "planned"`, `reps: 0`, `lapses: 0`, `bktMastery: 0.1`, and NO
    `fsrs` / `nextReview` / `lastTested` / `revlog` entries.
  - Non-destructive deduplication: never overwrites existing mastery or schedules.
"""

from __future__ import annotations

import ast
import csv
import hashlib
import html as html_lib
from html.parser import HTMLParser
import io
import json
import math
import os
from pathlib import Path
import posixpath
import re
import sqlite3
import stat
import tempfile
from typing import Any
import unicodedata
import urllib.parse
import zipfile

from syntopik.paths import resolve_chapters_dir, resolve_kg_dir, resolve_workspace
from syntopik.schemas import normalize_subject, now_iso, slugify
from syntopik.storage import (
    load_jsonl,
    save_json_atomic,
    save_jsonl_atomic,
    workspace_lock,
    write_text_atomic,
)

try:
    from syntopik.packs import is_safe_archive_path as _packs_is_safe_archive_path
except Exception:  # pragma: no cover - standalone fallback
    _packs_is_safe_archive_path = None

__all__ = [
    "MAX_ZIP_TOTAL",
    "MAX_ARCHIVE_BYTES",
    "MAX_FILE",
    "MAX_ENTRIES",
    "MAX_CARDS",
    "MAX_IMPORTED_ITEMS",
    "MAX_COMPRESSION_RATIO",
    "CLOZE_PATTERN",
    "NOTION_UUID_RE",
    "is_safe_archive_path",
    "sanitize_anki_html",
    "strip_notion_uuid",
    "import_anki_apkg",
    "import_delimited_cards",
    "import_jupyter_notebook",
    "import_markdown_folder",
]

# ---------------------------------------------------------------------------
# Hard Security & Resource Ceilings
# ---------------------------------------------------------------------------

MAX_ZIP_TOTAL = 50 * 1024 * 1024       # 50 MiB total uncompressed streaming read cap
MAX_ARCHIVE_BYTES = 50 * 1024 * 1024   # 50 MiB input archive size cap
MAX_FILE = 10 * 1024 * 1024            # 10 MiB single-file cap
MAX_ENTRIES = 500                      # Max entries in a zip archive or batch folder
MAX_CARDS = 500                        # Default imported item cap
MAX_IMPORTED_ITEMS = 2000              # Hard ceiling even if caller passes larger limit
MAX_COMPRESSION_RATIO = 100.0          # Enforced once a member exceeds 1 MiB uncompressed
_RATIO_CHECK_MIN_BYTES = 1024 * 1024
_STREAM_CHUNK_BYTES = 65536

CLOZE_PATTERN = re.compile(r"\{\{c(\d+)::((?:(?!\{\{|::|\}\}).)*)(?:::((?:(?!\{\{|\}\}).)*))?\}\}", flags=re.DOTALL)
NOTION_UUID_RE = re.compile(
    r"(?:[\s_-]+)(?:[0-9a-fA-F]{32}|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})$"
)
_DRIVE_PREFIX_RE = re.compile(r"^[A-Za-z]:")
_ANKI_STUB_MARKER = "please update to the latest anki version"
_HTML_TAG_HINT_RE = re.compile(r"<(?:!doctype|[a-zA-Z][a-zA-Z0-9:-]*(?:\s+[^<>]*)?/?|/[a-zA-Z][a-zA-Z0-9:-]*)>")

_IGNORED_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".obsidian",
        ".trash",
        "node_modules",
        ".venv",
        "venv",
        "env",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "dist",
        "build",
        "target",
        ".idea",
        ".vscode",
        "__MACOSX",
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
# Path-Jail, Zip-Slip & Streaming Anti-Zip-Bomb Security Guards
# ---------------------------------------------------------------------------


def _ensure_within_dir(base_dir: Path, candidate: Path) -> Path:
    base_res = base_dir.resolve()
    cand_res = candidate.resolve()
    if cand_res == base_res or cand_res.is_relative_to(base_res):
        return cand_res
    raise ValueError(f"Path jail violation: '{candidate}' escapes allowed directory '{base_res}'.")


def _ingested_dir_for(workspace: Path, slug: str) -> Path:
    raw = str(slug or "").strip()
    if ".." in raw or "/" in raw or "\\" in raw:
        raise ValueError(f"Invalid slug {slug!r}: path separators and '..' are forbidden.")
    clean = slugify(raw, default="import", max_len=64)
    root_ingested = (workspace / "sources" / "_ingested").resolve()
    target = _ensure_within_dir(root_ingested, root_ingested / clean)
    if target == root_ingested:
        raise ValueError(f"Invalid slug {slug!r}: resolves to base directory.")
    return target


def is_safe_archive_path(raw_name: str) -> tuple[bool, str]:
    """Return `(is_safe, normalized_posix_path_or_reason)` blocking zip-slip attacks."""
    if _packs_is_safe_archive_path is not None:
        return _packs_is_safe_archive_path(raw_name)
    if not isinstance(raw_name, str) or not raw_name or not raw_name.strip():
        return False, "empty archive member path"
    if "\x00" in raw_name:
        return False, f"null byte in path: {raw_name!r}"
    if "\\" in raw_name:
        return False, f"backslash disallowed in path: {raw_name!r}"
    if raw_name.startswith("/"):
        return False, f"absolute path disallowed: {raw_name!r}"
    if _DRIVE_PREFIX_RE.match(raw_name):
        return False, f"drive-letter path disallowed: {raw_name!r}"

    raw_parts = raw_name.split("/")
    for part in raw_parts:
        if part == "..":
            return False, f"parent directory traversal disallowed: {raw_name!r}"

    cleaned = [p for p in raw_parts if p not in ("", ".")]
    if not cleaned:
        return False, f"degenerate path: {raw_name!r}"

    norm = posixpath.normpath("/".join(cleaned))
    if norm in (".", "..") or norm.startswith("/") or norm.startswith("../") or "/../" in norm:
        return False, f"unsafe normalized path: {raw_name!r} -> {norm!r}"
    return True, norm


def _zip_info_is_symlink_or_special(info: zipfile.ZipInfo) -> tuple[bool, str]:
    """Detect POSIX symlinks or special device/FIFO entries encoded in zip external_attr."""
    mode = (info.external_attr >> 16) & 0xFFFF
    if mode:
        if stat.S_ISLNK(mode):
            return True, "symlink entry disallowed in archive"
        if stat.S_ISCHR(mode) or stat.S_ISBLK(mode) or stat.S_ISFIFO(mode) or stat.S_ISSOCK(mode):
            return True, "special device/FIFO entry disallowed in archive"
    return False, ""


def _validate_zip_members(zf: zipfile.ZipFile, *, max_entries: int = MAX_ENTRIES) -> list[tuple[zipfile.ZipInfo, str]]:
    """Validate EVERY entry in a zip archive before reading any bytes."""
    infos = zf.infolist()
    if len(infos) > max_entries:
        raise ValueError(f"Zip archive contains {len(infos)} entries, exceeding safety cap ({max_entries}).")

    validated: list[tuple[zipfile.ZipInfo, str]] = []
    for info in infos:
        raw_name = info.filename
        bad_special, special_reason = _zip_info_is_symlink_or_special(info)
        if bad_special:
            raise ValueError(f"Unsafe zip member {raw_name!r}: {special_reason}.")
        ok, norm_or_reason = is_safe_archive_path(raw_name)
        if not ok:
            raise ValueError(f"Zip-slip rejected for member {raw_name!r}: {norm_or_reason}.")
        validated.append((info, norm_or_reason))
    return validated


def _stream_read_zip_member(
    zf: zipfile.ZipFile,
    info: zipfile.ZipInfo,
    *,
    max_file_bytes: int,
    remaining_total_bytes: int,
) -> bytes:
    """Read a zip member in 64 KiB chunks enforcing real byte & compression-ratio caps."""
    buf = bytearray()
    member_bytes = 0
    compress_size = max(1, int(info.compress_size or 0))
    with zf.open(info, "r") as stream:
        while True:
            chunk = stream.read(_STREAM_CHUNK_BYTES)
            if not chunk:
                break
            member_bytes += len(chunk)
            if member_bytes > max_file_bytes:
                raise ValueError(
                    f"Zip member {info.filename!r} exceeded single-file limit ({max_file_bytes} bytes)."
                )
            if member_bytes > remaining_total_bytes:
                raise ValueError(
                    f"Zip archive exceeded cumulative uncompressed safety cap ({MAX_ZIP_TOTAL} bytes)."
                )
            if (
                member_bytes > _RATIO_CHECK_MIN_BYTES
                and info.compress_type != zipfile.ZIP_STORED
                and (member_bytes / compress_size) > MAX_COMPRESSION_RATIO
            ):
                raise ValueError(
                    f"Zip member {info.filename!r} triggered anti-zip-bomb ratio guard (>{MAX_COMPRESSION_RATIO:.0f}x)."
                )
            buf.extend(chunk)
    return bytes(buf)


def _stream_extract_zip_member_to_file(
    zf: zipfile.ZipFile,
    info: zipfile.ZipInfo,
    dest_path: Path,
    *,
    max_file_bytes: int,
    remaining_total_bytes: int,
) -> int:
    """Stream a single zip member to disk in 64 KiB chunks with strict size & ratio guards."""
    member_bytes = 0
    compress_size = max(1, int(info.compress_size or 0))
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    with zf.open(info, "r") as src, dest_path.open("wb") as dst:
        while True:
            chunk = src.read(_STREAM_CHUNK_BYTES)
            if not chunk:
                break
            member_bytes += len(chunk)
            if member_bytes > max_file_bytes:
                raise ValueError(
                    f"Zip member {info.filename!r} exceeded single-file limit ({max_file_bytes} bytes)."
                )
            if member_bytes > remaining_total_bytes:
                raise ValueError(
                    f"Zip archive exceeded cumulative uncompressed safety cap ({MAX_ZIP_TOTAL} bytes)."
                )
            if (
                member_bytes > _RATIO_CHECK_MIN_BYTES
                and info.compress_type != zipfile.ZIP_STORED
                and (member_bytes / compress_size) > MAX_COMPRESSION_RATIO
            ):
                raise ValueError(
                    f"Zip member {info.filename!r} triggered anti-zip-bomb ratio guard (>{MAX_COMPRESSION_RATIO:.0f}x)."
                )
            dst.write(chunk)
    return member_bytes


def _check_regular_file(path: str | Path, *, max_bytes: int, label: str = "Input file") -> Path:
    p = Path(path).expanduser()
    if not p.exists():
        raise FileNotFoundError(f"{label} not found: {p}")
    if not p.is_file():
        raise ValueError(f"{label} must be a regular file: {p}")
    size = p.stat().st_size
    if size > max_bytes:
        raise ValueError(f"{label} ({size} bytes) exceeds safety limit ({max_bytes} bytes).")
    return p.resolve()


# ---------------------------------------------------------------------------
# Safe HTML Sanitizer & MathJax Normalizer (Zero Script/Tag Injection)
# ---------------------------------------------------------------------------


class _HtmlToCleanText(HTMLParser):
    """Pure-stdlib HTML-to-plain-text sanitizer stripping dangerous subtrees & all tags."""

    _SKIP_TAGS: frozenset[str] = frozenset(
        {"script", "style", "iframe", "object", "embed", "noscript", "template", "svg", "head"}
    )
    _BLOCK_TAGS: frozenset[str] = frozenset(
        {"p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "blockquote", "section", "article", "pre", "hr"}
    )

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip_depth: int = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        t = tag.lower()
        if t in self._SKIP_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth > 0:
            return
        if t == "br":
            self._parts.append("\n")
        elif t == "li":
            self._parts.append("\n- ")
        elif t in self._BLOCK_TAGS:
            self._parts.append("\n")
        elif t == "img":
            attr_map = {k.lower(): (v or "") for k, v in attrs}
            raw_src = attr_map.get("src", "").strip()
            alt = attr_map.get("alt", "").strip()
            label = ""
            if raw_src and not raw_src.lower().startswith("data:"):
                clean_src = urllib.parse.unquote(raw_src.split("?", 1)[0].split("#", 1)[0])
                label = posixpath.basename(clean_src.replace("\\", "/")).strip()
            if not label:
                label = alt or "image"
            label = re.sub(r"[\r\n\[\]]+", " ", label).strip()[:80] or "image"
            self._parts.append(f" [image: {label}] ")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        t = tag.lower()
        if t in self._SKIP_TAGS:
            return
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        t = tag.lower()
        if t in self._SKIP_TAGS:
            if self._skip_depth > 0:
                self._skip_depth -= 1
            return
        if self._skip_depth > 0:
            return
        if t in self._BLOCK_TAGS or t in ("li", "ul", "ol", "table"):
            self._parts.append("\n")
        elif t in ("td", "th"):
            self._parts.append(" | ")

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0 and data:
            self._parts.append(data)

    def get_text(self) -> str:
        return "".join(self._parts)


def _normalize_math_and_sound(text: str) -> str:
    """Convert Anki MathJax `\\(...\\)` / `\\[...\\]` to `$...$` / `$$...$$` and strip `[sound:...]`."""
    out = re.sub(r"\[sound:[^\]]*\]", "", text, flags=re.IGNORECASE)
    # Convert Anki MathJax display & inline delimiters
    out = re.sub(r"\\\[(.+?)\\\]", lambda m: f"$${m.group(1).strip()}$$", out, flags=re.DOTALL)
    out = re.sub(r"\\\((.+?)\\\)", lambda m: f"${m.group(1).strip()}$", out, flags=re.DOTALL)
    # Convert legacy Anki [$$]...[/$$] and [$]...[$] tags
    out = re.sub(r"\[\$\$\](.+?)\[/\$\$\]", lambda m: f"$${m.group(1).strip()}$$", out, flags=re.DOTALL)
    out = re.sub(r"\[\$\](.+?)\[/\$\]", lambda m: f"${m.group(1).strip()}$", out, flags=re.DOTALL)
    out = out.replace("\r\n", "\n").replace("\r", "\n").replace("\xa0", " ")
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in out.split("\n")]
    cleaned_lines: list[str] = []
    blank_run = 0
    for ln in lines:
        if not ln:
            blank_run += 1
            if blank_run <= 1 and cleaned_lines:
                cleaned_lines.append("")
        else:
            blank_run = 0
            cleaned_lines.append(ln)
    return "\n".join(cleaned_lines).strip()


def sanitize_anki_html(raw: str) -> str:
    """Strip all HTML/script/style tags cleanly via stdlib HTMLParser and normalize MathJax."""
    text = str(raw or "")
    if not text.strip():
        return ""
    parser = _HtmlToCleanText()
    try:
        parser.feed(text)
        parser.close()
        extracted = parser.get_text()
    except Exception:
        extracted = re.sub(r"<[^>]+>", " ", html_lib.unescape(text))
    # Defense-in-depth: remove any malformed residual HTML/script tags while preserving math `a < b`
    extracted = re.sub(r"</?(?:script|style|iframe|object|embed|svg|img|div|span|p|br|a|b|i|u|strong|em|table|tr|td|th|ul|ol|li|font|center)\b[^>]*>", "", extracted, flags=re.IGNORECASE)
    return _normalize_math_and_sound(extracted)


# ---------------------------------------------------------------------------
# Lexical Deduplication & Zero-Fake-Mastery Persistence
# ---------------------------------------------------------------------------


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


def _derive_title(text: str, *, max_len: int = 96, fallback: str = "Imported Concept") -> str:
    one_line = re.sub(r"\s+", " ", str(text or "").strip())
    if not one_line:
        return fallback
    if len(one_line) <= max_len:
        return one_line
    cut = one_line[: max(12, max_len - 3)].rstrip()
    if " " in cut and len(cut) > 20:
        cut = cut.rsplit(" ", 1)[0].rstrip(" ,;:-")
    return cut + "..."


def _unwrap_cloze(text: str) -> tuple[str, list[str]]:
    """Replace `{{cN::ans::hint}}` with `ans` and collect extracted answers."""
    answers: list[str] = []

    def _repl(match: re.Match[str]) -> str:
        ans = match.group(2).strip()
        if ans:
            answers.append(ans)
        return ans

    unwrapped = CLOZE_PATTERN.sub(_repl, text)
    return unwrapped.strip(), answers


def _normalize_tags(raw_tags: Any) -> list[str]:
    if isinstance(raw_tags, str):
        parts = re.split(r"[\s,;]+", raw_tags.strip())
    elif isinstance(raw_tags, (list, tuple, set)):
        parts = [str(x).strip() for x in raw_tags]
    else:
        parts = []
    out: list[str] = []
    seen: set[str] = set()
    for p in parts:
        clean = p.strip().strip("#").lower()
        if clean and clean not in seen:
            seen.add(clean)
            out.append(clean)
    return out


def _build_planned_proposition(
    *,
    prop_id: str,
    title: str,
    statement: str,
    subject: str,
    chapter: str,
    kind: str,
    cues: list[dict[str, Any]],
    tags: list[str] | None = None,
    notes: str = "",
    aliases: list[str] | None = None,
    source_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a strictly unseen `status: "planned"` proposition (ZERO fake mastery)."""
    clean_cues: list[dict[str, Any]] = []
    for idx, cue in enumerate(cues):
        q = str(cue.get("q") or "").strip()
        a = str(cue.get("a") or "").strip()
        if not q and not a:
            continue
        ckind = str(cue.get("kind") or ("cloze" if CLOZE_PATTERN.search(q) else "recall")).strip().lower()
        clean_cues.append(
            {
                "id": str(cue.get("id") or f"c{idx + 1}"),
                "q": q,
                "a": a,
                "kind": ckind,
                "lastRating": None,
                "reviews": 0,
            }
        )

    prop: dict[str, Any] = {
        "id": prop_id,
        "subject": subject,
        "chapter": chapter,
        "title": title,
        "statement": statement,
        "kind": kind,
        "status": "planned",
        "reps": 0,
        "lapses": 0,
        "bktMastery": 0.1,
        "createdAt": now_iso(),
        "cues": clean_cues,
    }
    norm_tags = _normalize_tags(tags or [])
    if norm_tags:
        prop["tags"] = norm_tags
    if notes.strip():
        prop["notes"] = notes.strip()
    if aliases:
        clean_aliases = [str(a).strip() for a in aliases if str(a).strip()]
        if clean_aliases:
            prop["aliases"] = list(dict.fromkeys(clean_aliases))
    if source_meta:
        prop["source"] = source_meta
    return prop


def _find_duplicate_prop(
    cand: dict[str, Any],
    existing_props: list[dict[str, Any]],
    *,
    threshold: float = 0.80,
) -> tuple[dict[str, Any] | None, float]:
    cand_id = str(cand.get("id") or "").strip().lower()
    cand_title = str(cand.get("title") or "").strip()
    cand_stmt = str(cand.get("statement") or "").strip()
    cand_q = ""
    if isinstance(cand.get("cues"), list) and cand["cues"]:
        cand_q = str(cand["cues"][0].get("q") or "").strip()
    cand_blob = f"{cand_title} {cand_stmt}".strip()

    best_prop: dict[str, Any] | None = None
    best_score = 0.0
    for ep in existing_props:
        eid = str(ep.get("id") or "").strip().lower()
        if cand_id and eid == cand_id:
            return ep, 1.0
        etitle = str(ep.get("title") or "").strip()
        estmt = str(ep.get("statement") or "").strip()
        if cand_title and etitle and cand_title.lower() == etitle.lower():
            return ep, 1.0
        if cand_q and isinstance(ep.get("cues"), list):
            for ec in ep["cues"]:
                if isinstance(ec, dict) and str(ec.get("q") or "").strip().lower() == cand_q.lower():
                    return ep, 1.0
        sc = max(
            _lexical_similarity(cand_title, etitle),
            _lexical_similarity(cand_blob, f"{etitle} {estmt}".strip()),
        )
        if sc > best_score:
            best_score = sc
            best_prop = ep

    if best_prop is not None and best_score >= threshold:
        return best_prop, best_score
    return None, best_score


def _merge_imported_propositions(
    candidates: list[dict[str, Any]],
    *,
    props_file: Path,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Non-destructively dedupe & persist imported `planned` propositions under lock."""
    with workspace_lock(props_file):
        existing = load_jsonl(props_file)
        existing_ids: set[str] = {str(p.get("id") or "") for p in existing if p.get("id")}
        working = list(existing)

        imported: list[dict[str, Any]] = []
        deduplicated_ids: list[str] = []
        cues_merged = 0
        changed = False

        for cand in candidates:
            dup, _score = _find_duplicate_prop(cand, working, threshold=0.80)
            if dup is not None:
                deduplicated_ids.append(str(dup.get("id") or ""))
                # Non-destructive merge: NEVER touch status, fsrs, reps, lapses, bktMastery!
                existing_cues = dup.get("cues")
                if not isinstance(existing_cues, list):
                    existing_cues = []
                    dup["cues"] = existing_cues
                seen_qs = {
                    str(c.get("q") or "").strip().lower()
                    for c in existing_cues
                    if isinstance(c, dict)
                }
                for new_cue in cand.get("cues") or []:
                    nq = str(new_cue.get("q") or "").strip()
                    if nq and nq.lower() not in seen_qs:
                        seen_qs.add(nq.lower())
                        existing_cues.append(
                            {
                                "id": f"c{len(existing_cues) + 1}",
                                "q": nq,
                                "a": str(new_cue.get("a") or "").strip(),
                                "kind": str(new_cue.get("kind") or "recall"),
                                "lastRating": None,
                                "reviews": 0,
                            }
                        )
                        cues_merged += 1
                        changed = True
                if cand.get("tags"):
                    merged_tags = _normalize_tags(list(dup.get("tags") or []) + list(cand["tags"]))
                    if merged_tags != list(dup.get("tags") or []):
                        dup["tags"] = merged_tags
                        changed = True
                continue

            # Assign deterministic unique ID
            base_id = slugify(str(cand.get("id") or cand.get("title") or "card"), default="card", max_len=60)
            final_id = base_id
            suffix = 2
            while final_id in existing_ids:
                final_id = f"{base_id[:52]}-{suffix}"
                suffix += 1
            existing_ids.add(final_id)
            fresh = dict(cand)
            fresh["id"] = final_id
            # Enforce zero-fake-mastery invariants right before persistence
            fresh["status"] = "planned"
            fresh["reps"] = 0
            fresh["lapses"] = 0
            fresh["bktMastery"] = 0.1
            for forbidden in ("fsrs", "nextReview", "lastTested", "confidence", "revlog"):
                fresh.pop(forbidden, None)

            working.append(fresh)
            imported.append(fresh)
            changed = True

        if not dry_run and changed:
            save_jsonl_atomic(props_file, working)

    return {
        "imported": imported,
        "importedCount": len(imported),
        "deduplicatedCount": len(deduplicated_ids),
        "deduplicatedIds": deduplicated_ids,
        "cuesMerged": cues_merged,
        "propsFile": str(props_file),
    }


def _safe_subject(raw_subject: str | None, default: str = "General") -> str:
    txt = str(raw_subject or "").strip()
    if not txt:
        return default
    try:
        return normalize_subject(txt)
    except ValueError:
        return default


# ---------------------------------------------------------------------------
# 1. Anki `.apkg` Pure-Stdlib Importer
# ---------------------------------------------------------------------------


def _read_anki_sqlite_notes(db_path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Read notes, notetypes, and decks strictly read-only from an Anki SQLite collection."""
    uri = f"{db_path.resolve().as_uri()}?mode=ro&immutable=1"
    conn: sqlite3.Connection | None = None
    try:
        conn = sqlite3.connect(uri, uri=True)
        cur = conn.cursor()
        tables = {
            str(row[0]).lower()
            for row in cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        if "notes" not in tables:
            raise ValueError("Anki SQLite database does not contain a 'notes' table.")

        models: dict[int, dict[str, Any]] = {}
        decks: dict[int, str] = {}

        # Modern Anki 2.1.28+ normalized schema (`notetypes`, `fields`, `decks`)
        if "notetypes" in tables:
            try:
                for row in cur.execute("SELECT id, name FROM notetypes").fetchall():
                    ntid = int(row[0])
                    name = str(row[1] or "")
                    models[ntid] = {
                        "name": name,
                        "isCloze": "cloze" in name.lower(),
                        "fields": [],
                    }
            except sqlite3.DatabaseError:
                pass
            if "fields" in tables:
                try:
                    for row in cur.execute("SELECT ntid, ord, name FROM fields ORDER BY ntid, ord").fetchall():
                        ntid = int(row[0])
                        if ntid in models:
                            models[ntid]["fields"].append(str(row[2] or ""))
                except sqlite3.DatabaseError:
                    pass

        if "decks" in tables:
            try:
                for row in cur.execute("SELECT id, name FROM decks").fetchall():
                    did = int(row[0])
                    dname = str(row[1] or "").replace("\x1f", "::").strip()
                    if dname:
                        decks[did] = dname
            except sqlite3.DatabaseError:
                pass

        # Classic Anki schema (`col.models`, `col.decks` JSON columns)
        if "col" in tables and (not models or not decks):
            try:
                col_cols = {str(r[1]).lower() for r in cur.execute("PRAGMA table_info(col)").fetchall()}
                if "models" in col_cols and not models:
                    row = cur.execute("SELECT models FROM col LIMIT 1").fetchone()
                    if row and row[0]:
                        parsed_models = json.loads(row[0])
                        if isinstance(parsed_models, dict):
                            for mid_str, mobj in parsed_models.items():
                                if not isinstance(mobj, dict):
                                    continue
                                try:
                                    mid_int = int(mid_str)
                                except ValueError:
                                    continue
                                mname = str(mobj.get("name") or "")
                                mtype = int(mobj.get("type") or 0)
                                fld_names = [
                                    str(f.get("name") or "")
                                    for f in (mobj.get("flds") or [])
                                    if isinstance(f, dict)
                                ]
                                models[mid_int] = {
                                    "name": mname,
                                    "isCloze": mtype == 1 or "cloze" in mname.lower(),
                                    "fields": fld_names,
                                }
                if "decks" in col_cols and not decks:
                    row = cur.execute("SELECT decks FROM col LIMIT 1").fetchone()
                    if row and row[0]:
                        parsed_decks = json.loads(row[0])
                        if isinstance(parsed_decks, dict):
                            for did_str, dobj in parsed_decks.items():
                                if not isinstance(dobj, dict):
                                    continue
                                try:
                                    did_int = int(did_str)
                                except ValueError:
                                    continue
                                dname = str(dobj.get("name") or "").replace("\x1f", "::").strip()
                                if dname:
                                    decks[did_int] = dname
            except (sqlite3.DatabaseError, json.JSONDecodeError):
                pass

        # Map note id -> deck name via `cards` table (NEVER imports review reps/ivl/ease!)
        note_deck: dict[int, str] = {}
        if "cards" in tables:
            try:
                for nid, did in cur.execute("SELECT nid, did FROM cards ORDER BY id ASC").fetchall():
                    nid_int = int(nid)
                    dname = decks.get(int(did), "")
                    if not dname:
                        continue
                    if nid_int not in note_deck or (note_deck[nid_int].lower() == "default" and dname.lower() != "default"):
                        note_deck[nid_int] = dname
            except sqlite3.DatabaseError:
                pass

        raw_notes: list[dict[str, Any]] = []
        for row in cur.execute("SELECT id, guid, mid, tags, flds FROM notes ORDER BY id ASC").fetchall():
            nid_int = int(row[0])
            mid_int = int(row[2])
            model_info = models.get(mid_int) or {"name": "", "isCloze": False, "fields": []}
            raw_notes.append(
                {
                    "nid": nid_int,
                    "guid": str(row[1] or ""),
                    "mid": mid_int,
                    "modelName": model_info["name"],
                    "isClozeModel": bool(model_info["isCloze"]),
                    "deckName": note_deck.get(nid_int, ""),
                    "tags": str(row[3] or ""),
                    "flds": str(row[4] or ""),
                }
            )

        schema_meta = {
            "schemaStyle": "modern" if "notetypes" in tables else "classic",
            "deckCount": len(decks),
            "notetypeCount": len(models),
        }
        return raw_notes, schema_meta
    except sqlite3.Error as exc:
        raise ValueError(f"Corrupted Anki SQLite database: {exc}") from exc
    finally:
        if conn is not None:
            try:
                conn.close()
            except sqlite3.Error:
                pass


def _decompress_zstd_bounded(raw_bytes: bytes, *, max_bytes: int) -> bytes | None:
    """Attempt bounded zstd decompression via Python 3.14+ stdlib `compression.zstd` if available."""
    try:
        from compression import zstd  # type: ignore[import-not-found]
    except Exception:
        return None
    try:
        if hasattr(zstd, "ZstdDecompressor"):
            decomp = zstd.ZstdDecompressor()
            out = decomp.decompress(raw_bytes, max_length=max_bytes + 1)
        else:
            out = zstd.decompress(raw_bytes)
    except Exception:
        return None
    if len(out) > max_bytes:
        raise ValueError(f"Decompressed anki21b database exceeded safety limit ({max_bytes} bytes).")
    if not out.startswith(b"SQLite format 3\x00"):
        return None
    return out


def import_anki_apkg(
    apkg_path: str | Path,
    *,
    workspace: str | Path | None = None,
    kg_dir: str | Path | None = None,
    subject: str = "General",
    chapter: str | None = None,
    limit: int = MAX_CARDS,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Import an Anki `.apkg` deck safely as unseen `status: "planned"` propositions."""
    ws = resolve_workspace(workspace)
    resolved_kg = Path(kg_dir).expanduser().resolve() if kg_dir else resolve_kg_dir(ws)
    src = _check_regular_file(apkg_path, max_bytes=MAX_ARCHIVE_BYTES, label="Anki .apkg archive")
    clean_subject = _safe_subject(subject, default="General")
    eff_limit = max(1, min(MAX_IMPORTED_ITEMS, int(limit or MAX_CARDS)))

    try:
        zf_ctx = zipfile.ZipFile(src, "r")
    except zipfile.BadZipFile as exc:
        raise ValueError(f"Invalid .apkg zip archive {src.name!r}: {exc}") from exc

    with zf_ctx as zf:
        validated = _validate_zip_members(zf, max_entries=MAX_ENTRIES)
        by_name: dict[str, zipfile.ZipInfo] = {norm: info for info, norm in validated}

        db_format = ""
        db_info: zipfile.ZipInfo | None = None
        is_zstd = False

        if "collection.anki21" in by_name:
            db_format = "anki21"
            db_info = by_name["collection.anki21"]
        elif "collection.anki21b" in by_name:
            db_format = "anki21b_zstd"
            db_info = by_name["collection.anki21b"]
            is_zstd = True
        elif "collection.anki2" in by_name:
            db_format = "anki2"
            db_info = by_name["collection.anki2"]
        else:
            raise ValueError(
                f"No Anki collection database (collection.anki21 or collection.anki2) found inside {src.name!r}."
            )

        with tempfile.TemporaryDirectory(prefix="synt-anki-") as tmp:
            db_path = Path(tmp) / "collection.sqlite"
            if is_zstd:
                compressed_bytes = _stream_read_zip_member(
                    zf,
                    db_info,
                    max_file_bytes=MAX_FILE,
                    remaining_total_bytes=MAX_ZIP_TOTAL,
                )
                decompressed = _decompress_zstd_bounded(compressed_bytes, max_bytes=MAX_FILE)
                if decompressed is None:
                    return {
                        "ok": False,
                        "kind": "anki",
                        "format": "anki21b_zstd",
                        "error": (
                            "Re-export in Anki with 'Support older Anki versions' checked "
                            "(creates collection.anki21)."
                        ),
                        "importedCount": 0,
                        "deduplicatedCount": 0,
                        "skippedStubCount": 0,
                        "truncatedCount": 0,
                        "dryRun": bool(dry_run),
                        "propositions": [],
                    }
                db_path.write_bytes(decompressed)
            else:
                _stream_extract_zip_member_to_file(
                    zf,
                    db_info,
                    db_path,
                    max_file_bytes=MAX_FILE,
                    remaining_total_bytes=MAX_ZIP_TOTAL,
                )

            raw_notes, schema_meta = _read_anki_sqlite_notes(db_path)

    candidates: list[dict[str, Any]] = []
    skipped_stub = 0
    skipped_empty = 0
    truncated = 0
    default_chapter = chapter.strip() if isinstance(chapter, str) and chapter.strip() else src.stem

    for note in raw_notes:
        raw_fields = note["flds"].split("\x1f")
        clean_fields = [sanitize_anki_html(f) for f in raw_fields]
        joined_low = " ".join(clean_fields).lower()
        if _ANKI_STUB_MARKER in joined_low:
            skipped_stub += 1
            continue
        if not any(f.strip() for f in clean_fields):
            skipped_empty += 1
            continue

        if len(candidates) >= eff_limit:
            truncated += 1
            continue

        deck_name = str(note.get("deckName") or "").strip()
        if isinstance(chapter, str) and chapter.strip():
            note_chapter = chapter.strip()
        elif deck_name and deck_name.lower() != "default":
            note_chapter = deck_name
        else:
            note_chapter = default_chapter

        primary_text = clean_fields[0] if clean_fields else ""
        has_cloze = bool(CLOZE_PATTERN.search(primary_text)) or (
            bool(note.get("isClozeModel")) and any(CLOZE_PATTERN.search(f) for f in clean_fields)
        )

        if has_cloze:
            cloze_src = primary_text if CLOZE_PATTERN.search(primary_text) else next(
                (f for f in clean_fields if CLOZE_PATTERN.search(f)), primary_text
            )
            unwrapped, answers = _unwrap_cloze(cloze_src)
            back_extra = "\n".join(f for f in clean_fields[1:] if f.strip()).strip()
            answer_summary = "; ".join(answers) if answers else unwrapped
            cue_a = f"{answer_summary}\n\n{back_extra}".strip() if back_extra else answer_summary
            cue_q = cloze_src.strip()
            cue_kind = "cloze"
            title = _derive_title(unwrapped or cloze_src, max_len=92, fallback="Anki Cloze Card")
            statement = unwrapped or answer_summary
            extra_notes = back_extra
        else:
            front = primary_text.strip()
            back = clean_fields[1].strip() if len(clean_fields) > 1 else ""
            if not front and back:
                front, back = back, ""
            extra_notes = "\n".join(f for f in clean_fields[2:] if f.strip()).strip()
            cue_q = front
            cue_a = back or front
            cue_kind = "recall"
            title = _derive_title(front, max_len=92, fallback="Anki Recall Card")
            statement = back or front

        tags = _normalize_tags(note.get("tags") or "")
        guid_hash = hashlib.sha1(f"{src.name}|{note['guid']}|{note['nid']}".encode("utf-8")).hexdigest()[:8]
        prop_id = slugify(f"anki-{slugify(note_chapter, default='deck', max_len=16)}-{title[:28]}-{guid_hash}", default=f"anki-{guid_hash}", max_len=60)

        candidates.append(
            _build_planned_proposition(
                prop_id=prop_id,
                title=title,
                statement=statement,
                subject=clean_subject,
                chapter=note_chapter,
                kind=cue_kind,
                cues=[{"id": "c1", "q": cue_q, "a": cue_a, "kind": cue_kind}],
                tags=tags,
                notes=extra_notes,
                source_meta={"kind": "anki", "file": src.name, "guid": note["guid"]},
            )
        )

    merge = _merge_imported_propositions(
        candidates,
        props_file=resolved_kg / "propositions.jsonl",
        dry_run=bool(dry_run),
    )

    return {
        "ok": True,
        "kind": "anki",
        "format": db_format,
        "schemaStyle": schema_meta["schemaStyle"],
        "title": default_chapter,
        "slug": slugify(f"anki-{default_chapter}", default="anki-deck", max_len=64),
        "subject": clean_subject,
        "chapter": default_chapter,
        "totalNotesInDeck": len(raw_notes),
        "importedCount": merge["importedCount"],
        "deduplicatedCount": merge["deduplicatedCount"],
        "cuesMerged": merge["cuesMerged"],
        "skippedStubCount": skipped_stub,
        "skippedEmptyCount": skipped_empty,
        "truncatedCount": truncated,
        "propsFile": merge["propsFile"],
        "dryRun": bool(dry_run),
        "propositions": merge["imported"],
    }


# ---------------------------------------------------------------------------
# 2. Delimited Cards Importer (Anki TSV/CSV + Quizlet TSV/CSV)
# ---------------------------------------------------------------------------

_SEPARATOR_MAP: dict[str, str] = {
    "tab": "\t",
    "\\t": "\t",
    "\t": "\t",
    "comma": ",",
    ",": ",",
    "semicolon": ";",
    ";": ";",
    "pipe": "|",
    "|": "|",
    "colon": ":",
    ":": ":",
    "space": " ",
}

_HEADER_ALIASES: dict[str, str] = {
    "front": "front",
    "term": "front",
    "question": "front",
    "prompt": "front",
    "q": "front",
    "cue": "front",
    "word": "front",
    "concept": "front",
    "back": "back",
    "definition": "back",
    "answer": "back",
    "a": "back",
    "response": "back",
    "meaning": "back",
    "explanation": "back",
    "tags": "tags",
    "tag": "tags",
    "labels": "tags",
    "chapter": "chapter",
    "deck": "chapter",
    "topic": "chapter",
    "unit": "chapter",
    "module": "chapter",
    "section": "chapter",
    "subject": "subject",
    "course": "subject",
    "domain": "subject",
    "category": "subject",
    "notes": "notes",
    "note": "notes",
    "hint": "notes",
    "extra": "notes",
    "context": "notes",
    "example": "notes",
}


def _parse_anki_text_directives(lines: list[str]) -> tuple[dict[str, Any], list[str]]:
    """Parse leading Anki `#separator:`, `#html:`, `#tags column:`, `#deck:` directives."""
    directives: dict[str, Any] = {}
    idx = 0
    while idx < len(lines):
        raw = lines[idx]
        stripped = raw.strip()
        if not stripped:
            idx += 1
            continue
        if not stripped.startswith("#"):
            break
        m = re.match(r"^#([a-zA-Z0-9 _-]+?)\s*:\s*(.*)$", stripped)
        if m:
            key = re.sub(r"\s+", " ", m.group(1).strip().lower())
            val = m.group(2).strip()
            if key == "separator":
                sep_key = val.lower()
                if sep_key in _SEPARATOR_MAP:
                    directives["delimiter"] = _SEPARATOR_MAP[sep_key]
                elif len(val) == 1:
                    directives["delimiter"] = val
            elif key == "html":
                directives["html"] = val.lower() in ("true", "1", "yes", "on")
            elif key in ("tags column", "tag column"):
                try:
                    col_1based = int(val)
                    if col_1based >= 1:
                        directives["tagsColumn"] = col_1based - 1
                except ValueError:
                    pass
            elif key == "deck":
                if val:
                    directives["deck"] = val
            elif key == "columns":
                directives["columns"] = val
        idx += 1
    return directives, lines[idx:]


def _detect_delimiter(body_text: str, ext: str) -> str:
    """Auto-detect delimiter (`\\t`, `,`, `;`, `|`) via csv.Sniffer + column-consistency scoring."""
    sample_lines = [ln for ln in body_text.splitlines() if ln.strip()][:25]
    if not sample_lines:
        return "\t" if ext == ".tsv" else ","
    sample = "\n".join(sample_lines)

    sniffer_pick: str | None = None
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters="\t,;|")
        if dialect.delimiter in ("\t", ",", ";", "|"):
            sniffer_pick = dialect.delimiter
    except csv.Error:
        pass

    ext_hint = "\t" if ext == ".tsv" else ","
    best_delim = ext_hint
    best_score = -1.0

    for cand in ("\t", ",", ";", "|"):
        try:
            rows = [r for r in csv.reader(io.StringIO(sample), delimiter=cand) if any(c.strip() for c in r)]
        except csv.Error:
            continue
        if not rows:
            continue
        widths = [len(r) for r in rows]
        multi = [w for w in widths if w >= 2]
        if not multi:
            continue
        modal_w = max(set(multi), key=lambda w: (multi.count(w), -abs(w - 2)))
        consistency = sum(1 for w in widths if w == modal_w) / len(widths)
        coverage = len(multi) / len(widths)
        bonus = (0.12 if cand == sniffer_pick else 0.0) + (0.06 if cand == ext_hint else 0.0)
        score = consistency * 2.0 + coverage + bonus
        if score > best_score:
            best_score = score
            best_delim = cand

    return best_delim


def _map_header_row(row: list[str]) -> dict[str, int] | None:
    """Map header aliases to column indices, returning None if row is data rather than a header."""
    mapped: dict[str, int] = {}
    for idx, cell in enumerate(row):
        norm = re.sub(r"[^a-z0-9]+", " ", str(cell or "").strip().lower()).strip()
        canonical = _HEADER_ALIASES.get(norm)
        if canonical and canonical not in mapped:
            mapped[canonical] = idx
    if "front" in mapped and "back" in mapped:
        return mapped
    return None


def import_delimited_cards(
    path: str | Path,
    *,
    workspace: str | Path | None = None,
    kg_dir: str | Path | None = None,
    delimiter: str | None = None,
    subject: str = "General",
    chapter: str | None = None,
    limit: int = MAX_CARDS,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Import CSV/TSV flashcard decks (Anki `#separator:` exports & Quizlet TSV/CSV)."""
    ws = resolve_workspace(workspace)
    resolved_kg = Path(kg_dir).expanduser().resolve() if kg_dir else resolve_kg_dir(ws)
    src = _check_regular_file(path, max_bytes=MAX_FILE, label="Delimited cards file")
    clean_subject = _safe_subject(subject, default="General")
    eff_limit = max(1, min(MAX_IMPORTED_ITEMS, int(limit or MAX_CARDS)))

    raw_text = src.read_text(encoding="utf-8-sig", errors="replace")
    all_lines = raw_text.splitlines()
    directives, body_lines = _parse_anki_text_directives(all_lines)
    body_text = "\n".join(body_lines)

    if delimiter is not None and str(delimiter) != "":
        eff_delim = _SEPARATOR_MAP.get(str(delimiter).lower(), str(delimiter)[0])
    elif "delimiter" in directives:
        eff_delim = str(directives["delimiter"])
    else:
        eff_delim = _detect_delimiter(body_text, src.suffix.lower())

    rows = [r for r in csv.reader(io.StringIO(body_text), delimiter=eff_delim) if any(cell.strip() for cell in r)]
    if not rows:
        return {
            "ok": True,
            "kind": "cards",
            "title": chapter or src.stem,
            "slug": slugify(f"cards-{chapter or src.stem}", default="cards", max_len=64),
            "delimiter": eff_delim,
            "hasHeader": False,
            "directives": directives,
            "importedCount": 0,
            "deduplicatedCount": 0,
            "skippedCount": 0,
            "truncatedCount": 0,
            "propsFile": str(resolved_kg / "propositions.jsonl"),
            "dryRun": bool(dry_run),
            "propositions": [],
        }

    col_map = _map_header_row(rows[0])
    has_header = col_map is not None
    data_rows = rows[1:] if has_header else rows
    if col_map is None:
        col_map = {"front": 0, "back": 1}
        if "tagsColumn" in directives:
            col_map["tags"] = int(directives["tagsColumn"])
        else:
            col_map["tags"] = 2
    elif "tagsColumn" in directives and "tags" not in col_map:
        col_map["tags"] = int(directives["tagsColumn"])

    use_html_strip = directives.get("html")
    default_chapter = (
        chapter.strip()
        if isinstance(chapter, str) and chapter.strip()
        else str(directives.get("deck") or src.stem).strip()
    )

    def _clean_cell(val: str) -> str:
        raw_val = str(val or "").strip()
        if not raw_val:
            return ""
        if use_html_strip is True or (use_html_strip is None and _HTML_TAG_HINT_RE.search(raw_val)):
            return sanitize_anki_html(raw_val)
        return _normalize_math_and_sound(raw_val)

    candidates: list[dict[str, Any]] = []
    skipped = 0
    truncated = 0

    for row_idx, row in enumerate(data_rows, start=1):
        f_idx = col_map.get("front", 0)
        b_idx = col_map.get("back", 1)
        front_raw = row[f_idx] if f_idx < len(row) else ""
        back_raw = row[b_idx] if b_idx < len(row) else ""
        front = _clean_cell(front_raw)
        back = _clean_cell(back_raw)

        if not front and not back:
            skipped += 1
            continue
        if not front:
            front, back = back, ""

        if len(candidates) >= eff_limit:
            truncated += 1
            continue

        tags_idx = col_map.get("tags")
        tags_raw = row[tags_idx] if (tags_idx is not None and tags_idx < len(row)) else ""
        chap_idx = col_map.get("chapter")
        row_chap = _clean_cell(row[chap_idx]) if (chap_idx is not None and chap_idx < len(row)) else ""
        subj_idx = col_map.get("subject")
        row_subj_raw = _clean_cell(row[subj_idx]) if (subj_idx is not None and subj_idx < len(row)) else ""
        notes_idx = col_map.get("notes")
        row_notes = _clean_cell(row[notes_idx]) if (notes_idx is not None and notes_idx < len(row)) else ""

        eff_chap = row_chap or default_chapter
        eff_subj = _safe_subject(row_subj_raw, default=clean_subject) if row_subj_raw else clean_subject
        tags = _normalize_tags(tags_raw)

        if CLOZE_PATTERN.search(front):
            unwrapped, answers = _unwrap_cloze(front)
            ans_str = "; ".join(answers) if answers else unwrapped
            cue_q = front
            cue_a = f"{ans_str}\n\n{back}".strip() if back else ans_str
            cue_kind = "cloze"
            title = _derive_title(unwrapped or front, max_len=92, fallback=f"Card {row_idx}")
            statement = unwrapped or ans_str
        else:
            cue_q = front
            cue_a = back or front
            cue_kind = "recall"
            title = _derive_title(front, max_len=92, fallback=f"Card {row_idx}")
            statement = back or front

        h8 = hashlib.sha1(f"{src.name}|{row_idx}|{cue_q}".encode("utf-8")).hexdigest()[:8]
        prop_id = slugify(f"card-{slugify(eff_chap, default='deck', max_len=16)}-{title[:28]}-{h8}", default=f"card-{h8}", max_len=60)

        candidates.append(
            _build_planned_proposition(
                prop_id=prop_id,
                title=title,
                statement=statement,
                subject=eff_subj,
                chapter=eff_chap,
                kind=cue_kind,
                cues=[{"id": "c1", "q": cue_q, "a": cue_a, "kind": cue_kind}],
                tags=tags,
                notes=row_notes,
                source_meta={"kind": "cards", "file": src.name, "row": row_idx},
            )
        )

    merge = _merge_imported_propositions(
        candidates,
        props_file=resolved_kg / "propositions.jsonl",
        dry_run=bool(dry_run),
    )

    return {
        "ok": True,
        "kind": "cards",
        "title": default_chapter,
        "slug": slugify(f"cards-{default_chapter}", default="cards", max_len=64),
        "subject": clean_subject,
        "chapter": default_chapter,
        "delimiter": eff_delim,
        "hasHeader": has_header,
        "directives": directives,
        "totalRowsParsed": len(data_rows),
        "importedCount": merge["importedCount"],
        "deduplicatedCount": merge["deduplicatedCount"],
        "cuesMerged": merge["cuesMerged"],
        "skippedCount": skipped,
        "truncatedCount": truncated,
        "propsFile": merge["propsFile"],
        "dryRun": bool(dry_run),
        "propositions": merge["imported"],
    }


# ---------------------------------------------------------------------------
# 3. Jupyter Notebook (`.ipynb`) Static AST Importer (ZERO Code Execution!)
# ---------------------------------------------------------------------------

_DATA_URI_RE = re.compile(r"data:[a-zA-Z0-9.+/-]+;base64,[A-Za-z0-9+/=\s]{64,}")
_BOLD_DEF_RE = re.compile(r"^\s*(?:[-*]\s+)?\*\*([^*]{3,90})\*\*\s*[:—–-]\s+(.{8,})$")


def _cell_source_text(cell: dict[str, Any]) -> str:
    raw = cell.get("source", "")
    if isinstance(raw, list):
        return "".join(str(x) for x in raw)
    return str(raw or "")


def _strip_ipython_magics(src: str) -> str:
    """Comment out IPython line/cell magics (`%...`, `!...`) so `ast.parse` succeeds cleanly."""
    lines = src.splitlines()
    if lines and lines[0].lstrip().startswith("%%"):
        return "\n".join(f"# {ln}" for ln in lines)
    out: list[str] = []
    for ln in lines:
        stripped = ln.lstrip()
        if stripped.startswith(("%", "!")):
            indent = ln[: len(ln) - len(stripped)]
            out.append(f"{indent}# {stripped}")
        else:
            out.append(ln)
    return "\n".join(out)


def _json_safe_literal(value: Any) -> tuple[bool, Any]:
    """Ensure a Python literal from `ast.literal_eval` is finite & JSON-safe."""
    if value is None or isinstance(value, (bool, str, int)):
        return True, value
    if isinstance(value, float):
        if not math.isfinite(value):
            return False, None
        return True, value
    if isinstance(value, (list, tuple)):
        items: list[Any] = []
        for elem in value:
            ok, safe_elem = _json_safe_literal(elem)
            if not ok:
                return False, None
            items.append(safe_elem)
        return True, items
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            if not isinstance(k, str):
                return False, None
            ok, safe_v = _json_safe_literal(v)
            if not ok:
                return False, None
            out[k] = safe_v
        return True, out
    return False, None


def _safe_literal_node(node: ast.AST) -> tuple[bool, Any]:
    """Evaluate strictly literal AST expressions via `ast.literal_eval` ONLY (NEVER executes calls!)."""
    try:
        val = ast.literal_eval(node)
    except Exception:
        return False, None
    return _json_safe_literal(val)


def _extract_safe_callee_name(node: ast.AST) -> str | None:
    """Return dotted callable name if strictly plain non-dunder identifiers (blocks `__import__` etc.)."""
    parts: list[str] = []
    cur = node
    while isinstance(cur, ast.Attribute):
        attr = str(cur.attr or "")
        if not attr or attr.startswith("__"):
            return None
        parts.append(attr)
        cur = cur.value
    if not isinstance(cur, ast.Name):
        return None
    root = str(cur.id or "")
    if not root or root.startswith("__"):
        return None
    parts.append(root)
    parts.reverse()
    if len(parts) > 3:
        return None
    return ".".join(parts)


def _extract_call_args_literals(call: ast.Call) -> tuple[bool, str, list[Any], dict[str, Any]]:
    """Parse `fn(lit_args, kw=lit_val)` strictly via `ast.literal_eval`."""
    callee = _extract_safe_callee_name(call.func)
    if not callee:
        return False, "", [], {}
    args_out: list[Any] = []
    for arg in call.args:
        if isinstance(arg, ast.Starred):
            return False, "", [], {}
        ok, val = _safe_literal_node(arg)
        if not ok:
            return False, "", [], {}
        args_out.append(val)
    kwargs_out: dict[str, Any] = {}
    for kw in call.keywords:
        if kw.arg is None or kw.arg.startswith("__"):
            return False, "", [], {}
        ok, val = _safe_literal_node(kw.value)
        if not ok:
            return False, "", [], {}
        kwargs_out[kw.arg] = val
    return True, callee, args_out, kwargs_out


_BLOCKED_NOTEBOOK_CALLEES: frozenset[str] = frozenset(
    {
        "eval",
        "exec",
        "compile",
        "__import__",
        "open",
        "input",
        "breakpoint",
        "getattr",
        "setattr",
        "delattr",
        "globals",
        "locals",
        "vars",
    }
)


def _is_safe_defined_notebook_fn(fn_name: str, defined_fns: set[str]) -> bool:
    """Strictly require a single-identifier function/class defined in the notebook (no dots/builtins)."""
    if not defined_fns or not fn_name or "." in fn_name:
        return False
    if fn_name in _BLOCKED_NOTEBOOK_CALLEES:
        return False
    return fn_name in defined_fns


def _extract_assert_test_case(
    stmt: ast.Assert,
    *,
    cell_idx: int,
    assert_idx: int,
    defined_fns: set[str],
) -> dict[str, Any] | None:
    """Safely extract a `scripts/code_verify.py test-cases` entry from a static `ast.Assert`."""
    if not defined_fns:
        return None
    test = stmt.test
    case_id = f"cell{cell_idx}_assert{assert_idx}"

    # Pattern 1: `assert fn(lit_args) == expected_lit` or `assert expected_lit == fn(lit_args)`
    if isinstance(test, ast.Compare) and len(test.ops) == 1 and isinstance(test.ops[0], ast.Eq) and len(test.comparators) == 1:
        left, right = test.left, test.comparators[0]
        call_node: ast.Call | None = None
        exp_node: ast.AST | None = None
        if isinstance(left, ast.Call):
            call_node, exp_node = left, right
        elif isinstance(right, ast.Call):
            call_node, exp_node = right, left
        if call_node is not None and exp_node is not None:
            ok_call, fn_name, args_val, kwargs_val = _extract_call_args_literals(call_node)
            ok_exp, exp_val = _safe_literal_node(exp_node)
            if ok_call and ok_exp and _is_safe_defined_notebook_fn(fn_name, defined_fns):
                case: dict[str, Any] = {
                    "id": case_id,
                    "entry": fn_name,
                    "args": args_val,
                    "expected": exp_val,
                    "sourceCell": cell_idx,
                }
                if kwargs_val:
                    case["kwargs"] = kwargs_val
                return case
        return None

    # Pattern 2: `assert math.isclose(fn(...), expected, ...)` or `assert isclose(fn(...), expected, ...)`
    if isinstance(test, ast.Call):
        outer_name = _extract_safe_callee_name(test.func)
        if outer_name in ("math.isclose", "isclose") and len(test.args) >= 2:
            a0, a1 = test.args[0], test.args[1]
            call_node = a0 if isinstance(a0, ast.Call) else (a1 if isinstance(a1, ast.Call) else None)
            exp_node = a1 if call_node is a0 else (a0 if call_node is a1 else None)
            if call_node is not None and exp_node is not None:
                ok_call, fn_name, args_val, kwargs_val = _extract_call_args_literals(call_node)
                ok_exp, exp_val = _safe_literal_node(exp_node)
                if (
                    ok_call
                    and ok_exp
                    and _is_safe_defined_notebook_fn(fn_name, defined_fns)
                    and isinstance(exp_val, (int, float))
                    and not isinstance(exp_val, bool)
                ):
                    tol = 1e-6
                    for kw in test.keywords:
                        if kw.arg in ("abs_tol", "rel_tol"):
                            ok_t, tv = _safe_literal_node(kw.value)
                            if ok_t and isinstance(tv, (int, float)) and not isinstance(tv, bool) and tv > 0:
                                tol = float(tv)
                    case = {
                        "id": case_id,
                        "entry": fn_name,
                        "args": args_val,
                        "expected": exp_val,
                        "floatTol": tol,
                        "sourceCell": cell_idx,
                    }
                    if kwargs_val:
                        case["kwargs"] = kwargs_val
                    return case
            return None

        # Pattern 3: `assert fn(lit_args)` where fn is strictly a defined single-identifier notebook function
        ok_call, fn_name, args_val, kwargs_val = _extract_call_args_literals(test)
        if ok_call and _is_safe_defined_notebook_fn(fn_name, defined_fns):
            case = {
                "id": case_id,
                "entry": fn_name,
                "args": args_val,
                "expected": True,
                "sourceCell": cell_idx,
            }
            if kwargs_val:
                case["kwargs"] = kwargs_val
            return case
        return None

    # Pattern 4: `assert not fn(lit_args)` where fn is strictly a defined single-identifier notebook function
    if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not) and isinstance(test.operand, ast.Call):
        ok_call, fn_name, args_val, kwargs_val = _extract_call_args_literals(test.operand)
        if ok_call and _is_safe_defined_notebook_fn(fn_name, defined_fns):
            case = {
                "id": case_id,
                "entry": fn_name,
                "args": args_val,
                "expected": False,
                "sourceCell": cell_idx,
            }
            if kwargs_val:
                case["kwargs"] = kwargs_val
            return case

    return None


def _summarize_notebook_outputs(outputs: Any) -> list[str]:
    """Keep short plain-text output snippets while stripping large base64/media blobs."""
    if not isinstance(outputs, list):
        return []
    snippets: list[str] = []
    for out in outputs[:3]:
        if not isinstance(out, dict):
            continue
        otype = str(out.get("output_type") or "")
        if otype == "stream":
            raw = out.get("text", "")
            txt = "".join(str(x) for x in raw) if isinstance(raw, list) else str(raw or "")
            txt = _DATA_URI_RE.sub("[base64-stripped]", txt).strip()
            if txt:
                snippets.append(txt[:300])
        elif otype in ("execute_result", "display_data"):
            data = out.get("data")
            if isinstance(data, dict) and "text/plain" in data:
                raw = data["text/plain"]
                txt = "".join(str(x) for x in raw) if isinstance(raw, list) else str(raw or "")
                txt = _DATA_URI_RE.sub("[base64-stripped]", txt).strip()
                if txt and not txt.startswith("<Figure"):
                    snippets.append(txt[:300])
    return snippets


def import_jupyter_notebook(
    ipynb_path: str | Path,
    *,
    workspace: str | Path | None = None,
    kg_dir: str | Path | None = None,
    slug: str | None = None,
    subject: str = "Computer Science",
    chapter: str | None = None,
    create_chapter: bool = False,
    enqueue: bool = True,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Statically parse a Jupyter `.ipynb` via `json.loads` + `ast.parse` (ZERO execution)."""
    ws = resolve_workspace(workspace)
    resolved_kg = Path(kg_dir).expanduser().resolve() if kg_dir else resolve_kg_dir(ws)
    src = _check_regular_file(ipynb_path, max_bytes=MAX_FILE, label="Jupyter notebook")
    clean_subject = _safe_subject(subject, default="Computer Science")

    try:
        nb = json.loads(src.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid Jupyter notebook JSON in {src.name!r}: {exc}") from exc
    if not isinstance(nb, dict) or not isinstance(nb.get("cells"), list):
        raise ValueError(f"Invalid .ipynb structure in {src.name!r}: expected top-level object with 'cells' list.")

    meta = nb.get("metadata") if isinstance(nb.get("metadata"), dict) else {}
    kernelspec = meta.get("kernelspec") if isinstance(meta.get("kernelspec"), dict) else {}
    lang_info = meta.get("language_info") if isinstance(meta.get("language_info"), dict) else {}
    code_lang = str(kernelspec.get("language") or lang_info.get("name") or "python").strip().lower() or "python"

    cells = [c for c in nb["cells"] if isinstance(c, dict)]
    nb_title = ""
    for c in cells:
        if c.get("cell_type") == "markdown":
            m = re.search(r"^\s*#\s+(.+?)\s*$", _cell_source_text(c), re.MULTILINE)
            if m:
                nb_title = m.group(1).strip()
                break
    if not nb_title:
        nb_title = str(meta.get("title") or src.stem.replace("_", " ").replace("-", " ").title()).strip()

    final_slug = slugify(slug if slug else f"nb-{nb_title}", default="notebook", max_len=64)
    eff_chapter = chapter.strip() if isinstance(chapter, str) and chapter.strip() else nb_title

    md_parts: list[str] = [
        f"# {nb_title}",
        "",
        f"- **Source Notebook**: `{src.name}`",
        f"- **Language**: `{code_lang}` · **Cells**: {len(cells)} · **Ingested**: {now_iso()}",
        "",
        "---",
        "",
    ]

    candidates: list[dict[str, Any]] = []
    all_cases: list[dict[str, Any]] = []
    unparsed_asserts: list[dict[str, Any]] = []
    defined_fns: set[str] = set()
    stripped_blobs = 0

    # First pass on code cells to record defined function/class names cleanly
    for cell in cells:
        if cell.get("cell_type") == "code" and code_lang.startswith("py"):
            cleaned_code = _strip_ipython_magics(_cell_source_text(cell))
            try:
                tree = ast.parse(cleaned_code)
            except SyntaxError:
                continue
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    defined_fns.add(node.name)

    for cell_idx, cell in enumerate(cells, start=1):
        ctype = str(cell.get("cell_type") or "")
        if cell.get("attachments"):
            stripped_blobs += len(cell["attachments"]) if isinstance(cell["attachments"], dict) else 1
        raw_src = _cell_source_text(cell)
        if _DATA_URI_RE.search(raw_src):
            stripped_blobs += 1
            raw_src = _DATA_URI_RE.sub("[base64-image-stripped]", raw_src)

        if ctype == "markdown":
            clean_md = raw_src.strip()
            if clean_md:
                md_parts.append(clean_md)
                md_parts.append("")
                # Extract propositions from ## / ### sections
                sec_matches = list(re.finditer(r"^(#{2,3})\s+(.+?)\s*$", clean_md, re.MULTILINE))
                for s_idx, sm in enumerate(sec_matches):
                    heading = sm.group(2).strip()
                    body_start = sm.end()
                    body_end = sec_matches[s_idx + 1].start() if s_idx + 1 < len(sec_matches) else len(clean_md)
                    sec_body = re.sub(r"\s+", " ", clean_md[body_start:body_end].strip())
                    if len(heading) >= 3 and len(sec_body) >= 12 and len(candidates) < MAX_CARDS:
                        stmt = sec_body[:320]
                        h8 = hashlib.sha1(f"{final_slug}|sec|{heading}".encode("utf-8")).hexdigest()[:8]
                        candidates.append(
                            _build_planned_proposition(
                                prop_id=slugify(f"{final_slug}-{heading[:28]}-{h8}", default=f"nb-{h8}", max_len=60),
                                title=_derive_title(heading, max_len=92),
                                statement=stmt,
                                subject=clean_subject,
                                chapter=eff_chapter,
                                kind="concept",
                                cues=[{"id": "c1", "q": f"Explain the core mechanism/invariant of: {heading}", "a": stmt, "kind": "recall"}],
                                tags=["notebook", code_lang],
                                source_meta={"kind": "notebook", "file": src.name, "cell": cell_idx},
                            )
                        )
                # Extract bold definitions/theorems
                for ln in clean_md.splitlines():
                    dm = _BOLD_DEF_RE.match(ln)
                    if dm and len(candidates) < MAX_CARDS:
                        term = dm.group(1).strip()
                        defn = dm.group(2).strip()
                        h8 = hashlib.sha1(f"{final_slug}|def|{term}".encode("utf-8")).hexdigest()[:8]
                        candidates.append(
                            _build_planned_proposition(
                                prop_id=slugify(f"{final_slug}-{term[:28]}-{h8}", default=f"nb-{h8}", max_len=60),
                                title=_derive_title(term, max_len=92),
                                statement=defn[:320],
                                subject=clean_subject,
                                chapter=eff_chapter,
                                kind="definition",
                                cues=[{"id": "c1", "q": f"Define or state: {term}", "a": defn[:320], "kind": "recall"}],
                                tags=["notebook", code_lang],
                                source_meta={"kind": "notebook", "file": src.name, "cell": cell_idx},
                            )
                        )

        elif ctype == "code":
            code_txt = raw_src.strip()
            if not code_txt:
                continue
            md_parts.append(f"```{code_lang}\n{code_txt}\n```")
            outputs = cell.get("outputs")
            if isinstance(outputs, list):
                for out_item in outputs:
                    if isinstance(out_item, dict) and isinstance(out_item.get("data"), dict):
                        for mime in ("image/png", "image/jpeg", "image/svg+xml", "application/pdf"):
                            if mime in out_item["data"]:
                                stripped_blobs += 1
            out_snips = _summarize_notebook_outputs(outputs)
            if out_snips:
                md_parts.append("\n_Cell output preview:_\n```text\n" + "\n".join(out_snips) + "\n```")
            md_parts.append("")

            if code_lang.startswith("py"):
                cleaned_code = _strip_ipython_magics(code_txt)
                try:
                    tree = ast.parse(cleaned_code)
                except SyntaxError:
                    continue

                # Extract function/method docstrings as planned propositions
                for node in tree.body:
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        doc = (ast.get_docstring(node) or "").strip()
                        if doc and len(candidates) < MAX_CARDS:
                            arg_names = [a.arg for a in node.args.args]
                            sig = f"def {node.name}({', '.join(arg_names)})"
                            first_doc = re.sub(r"\s+", " ", doc)[:300]
                            h8 = hashlib.sha1(f"{final_slug}|fn|{node.name}".encode("utf-8")).hexdigest()[:8]
                            candidates.append(
                                _build_planned_proposition(
                                    prop_id=slugify(f"{final_slug}-fn-{node.name}-{h8}", default=f"nb-fn-{h8}", max_len=60),
                                    title=_derive_title(f"{sig}: {first_doc.splitlines()[0]}", max_len=92),
                                    statement=first_doc,
                                    subject=clean_subject,
                                    chapter=eff_chapter,
                                    kind="procedure",
                                    cues=[{"id": "c1", "q": f"What contract and invariants must `{sig}` satisfy?", "a": first_doc, "kind": "procedural"}],
                                    tags=["notebook", "python", "function"],
                                    source_meta={"kind": "notebook", "file": src.name, "cell": cell_idx, "symbol": node.name},
                                )
                            )

                # Extract literal assert test cases strictly via ast.literal_eval
                assert_num = 0
                for node in ast.walk(tree):
                    if isinstance(node, ast.Assert):
                        assert_num += 1
                        extracted = _extract_assert_test_case(
                            node,
                            cell_idx=cell_idx,
                            assert_idx=assert_num,
                            defined_fns=defined_fns,
                        )
                        if extracted is not None:
                            all_cases.append(extracted)
                        else:
                            unparsed_asserts.append(
                                {
                                    "cell": cell_idx,
                                    "source": ast.unparse(node) if hasattr(ast, "unparse") else "<complex-assert>",
                                    "reason": "non-literal or unsafe expression skipped",
                                }
                            )

    # Group extracted test cases into suites + flat `cases` compatible with `scripts/code_verify.py test-cases`
    suites_map: dict[str, list[dict[str, Any]]] = {}
    for c in all_cases:
        suites_map.setdefault(str(c["entry"]), []).append(c)
    suites = [
        {
            "entry": fn_name,
            "lang": "python" if code_lang.startswith("py") else code_lang,
            "sourceCell": suite_cases[0].get("sourceCell"),
            "cases": suite_cases,
        }
        for fn_name, suite_cases in suites_map.items()
    ]
    primary_entry = suites[0]["entry"] if len(suites) == 1 else (suites[0]["entry"] if suites else None)

    code_tests_payload: dict[str, Any] = {
        "schemaVersion": 1,
        "lang": "python" if code_lang.startswith("py") else code_lang,
        "entry": primary_entry,
        "notebook": src.name,
        "suites": suites,
        "cases": all_cases,
        "unparsedAsserts": unparsed_asserts,
    }

    out_dir = _ingested_dir_for(ws, final_slug)
    notebook_md_path = _ensure_within_dir(out_dir, out_dir / "notebook.md")
    code_tests_path = _ensure_within_dir(out_dir, out_dir / "code-tests.json")
    manifest_path = _ensure_within_dir(out_dir, out_dir / "manifest.json")
    chapter_md_path: Path | None = None
    markdown_text = "\n".join(md_parts).rstrip() + "\n"

    queued_count = 0
    if not dry_run:
        out_dir.mkdir(parents=True, exist_ok=True)
        write_text_atomic(notebook_md_path, markdown_text)
        save_json_atomic(code_tests_path, code_tests_payload)
        if create_chapter:
            ch_dir = resolve_chapters_dir(ws)
            ch_dir.mkdir(parents=True, exist_ok=True)
            chapter_md_path = _ensure_within_dir(ch_dir, ch_dir / f"{final_slug}.md")
            write_text_atomic(chapter_md_path, markdown_text)
        if enqueue:
            from syntopik.ingest import queue_add

            rel_nb = str(notebook_md_path.relative_to(ws))
            queue_add(
                title=f"Notebook: {nb_title}",
                source_path=rel_nb,
                workspace=ws,
                kg_dir=resolved_kg,
                slug=final_slug,
                priority=70,
                stage="queued",
                heading=nb_title,
                excerpt=f"{len(cells)} cells, {len(all_cases)} extracted test cases",
            )
            queued_count = 1

    merge = _merge_imported_propositions(
        candidates,
        props_file=resolved_kg / "propositions.jsonl",
        dry_run=bool(dry_run),
    )

    manifest = {
        "schemaVersion": 2,
        "kind": "notebook",
        "slug": final_slug,
        "title": nb_title,
        "subject": clean_subject,
        "chapter": eff_chapter,
        "language": code_lang,
        "cellCount": len(cells),
        "strippedBase64Count": stripped_blobs,
        "extractedCasesCount": len(all_cases),
        "unparsedAssertsCount": len(unparsed_asserts),
        "importedCount": merge["importedCount"],
        "deduplicatedCount": merge["deduplicatedCount"],
        "notebookFile": str(notebook_md_path.relative_to(ws)),
        "codeTestsFile": str(code_tests_path.relative_to(ws)),
        "ingestedAt": now_iso(),
    }
    if not dry_run:
        save_json_atomic(manifest_path, manifest)

    return {
        "ok": True,
        "kind": "notebook",
        "slug": final_slug,
        "title": nb_title,
        "subject": clean_subject,
        "chapter": eff_chapter,
        "language": code_lang,
        "cellCount": len(cells),
        "strippedBase64Count": stripped_blobs,
        "outputDir": str(out_dir),
        "notebookPath": str(notebook_md_path),
        "codeTestsPath": str(code_tests_path),
        "manifestPath": str(manifest_path),
        "chapterPath": str(chapter_md_path) if chapter_md_path else None,
        "extractedCasesCount": len(all_cases),
        "unparsedAssertsCount": len(unparsed_asserts),
        "codeTests": code_tests_payload,
        "importedCount": merge["importedCount"],
        "deduplicatedCount": merge["deduplicatedCount"],
        "queuedCount": queued_count,
        "propsFile": merge["propsFile"],
        "dryRun": bool(dry_run),
        "propositions": merge["imported"],
    }


# ---------------------------------------------------------------------------
# 4. Markdown / Obsidian Folder & Notion `.zip` Importer
# ---------------------------------------------------------------------------

_MD_LINK_RE = re.compile(r"(!?\[[^\]]*\])\(([^)]+)\)")
_CUE_BLOCK_RE = re.compile(r"^:::cue(?:\s+([^\n]*))?\n(.*?)^:::\s*$", re.MULTILINE | re.DOTALL)
_QA_PAIR_RE = re.compile(
    r"^\s*(?:[-*]\s+)?Q::\s*(.+?)\s*(?:\n\s*(?:[-*]\s+)?A::\s*(.+?))\s*$",
    re.MULTILINE,
)


def strip_notion_uuid(name: str) -> str:
    """Strip Notion 32-hex or 36-char hyphenated UUID suffixes from a folder or file stem."""
    cleaned = NOTION_UUID_RE.sub("", str(name or "").strip()).strip()
    return cleaned or str(name or "").strip()


def _clean_notion_rel_path(rel_posix: str, used_paths: set[str]) -> str:
    """Strip Notion UUIDs from every segment, sanitize names, and resolve collisions deterministically."""
    parts = [p for p in rel_posix.split("/") if p not in ("", ".")]
    cleaned_parts: list[str] = []
    for idx, part in enumerate(parts):
        is_last = idx == len(parts) - 1
        if is_last:
            stem, ext = posixpath.splitext(part)
            stripped_stem = strip_notion_uuid(stem)
            clean_stem = slugify(stripped_stem, default="note", max_len=64)
            cleaned_parts.append(f"{clean_stem}{ext.lower()}")
        else:
            stripped_dir = strip_notion_uuid(part)
            cleaned_parts.append(slugify(stripped_dir, default="folder", max_len=48))

    candidate = "/".join(cleaned_parts)
    if candidate not in used_paths:
        used_paths.add(candidate)
        return candidate

    parent = posixpath.dirname(candidate)
    base = posixpath.basename(candidate)
    stem, ext = posixpath.splitext(base)
    suffix = 2
    while True:
        alt_base = f"{stem}-{suffix}{ext}"
        alt = f"{parent}/{alt_base}" if parent else alt_base
        if alt not in used_paths:
            used_paths.add(alt)
            return alt
        suffix += 1


def _rewrite_notion_links(
    md_text: str,
    *,
    orig_rel: str,
    dest_rel: str,
    path_map: dict[str, str],
) -> str:
    """Rewrite internal Notion URL-encoded UUID Markdown links cleanly to stripped relative paths."""
    orig_dir = posixpath.dirname(orig_rel)
    dest_dir = posixpath.dirname(dest_rel)

    def _repl(m: re.Match[str]) -> str:
        label = m.group(1)
        raw_target = m.group(2).strip()
        if not raw_target or raw_target.startswith(("#", "http://", "https://", "mailto:", "data:")):
            return m.group(0)
        # Preserve optional title after whitespace if quotes present
        target_part = raw_target
        title_suffix = ""
        tm = re.match(r'^(\S+)(\s+"[^"]*")$', raw_target)
        if tm:
            target_part, title_suffix = tm.group(1), tm.group(2)

        href, frag = (target_part.split("#", 1) + [""])[:2]
        decoded = urllib.parse.unquote(href).replace("\\", "/")
        if not decoded:
            return m.group(0)

        joined_orig = posixpath.normpath(posixpath.join(orig_dir, decoded)) if orig_dir else posixpath.normpath(decoded)
        mapped_dest = path_map.get(joined_orig) or path_map.get(decoded)
        if mapped_dest is not None:
            new_rel = posixpath.relpath(mapped_dest, dest_dir) if dest_dir else mapped_dest
        else:
            # Strip Notion UUIDs from link path segments cleanly even if target is external to batch
            segs = [s for s in decoded.split("/") if s not in ("", ".")]
            new_segs: list[str] = []
            for idx, s in enumerate(segs):
                if s == "..":
                    new_segs.append(s)
                elif idx == len(segs) - 1:
                    st, ex = posixpath.splitext(s)
                    if NOTION_UUID_RE.search(st):
                        new_segs.append(f"{slugify(strip_notion_uuid(st), default='note', max_len=64)}{ex.lower()}")
                    else:
                        new_segs.append(s)
                else:
                    if NOTION_UUID_RE.search(s):
                        new_segs.append(slugify(strip_notion_uuid(s), default="folder", max_len=48))
                    else:
                        new_segs.append(s)
            new_rel = "/".join(new_segs)

        frag_str = f"#{frag}" if frag else ""
        return f"{label}({new_rel}{frag_str}{title_suffix})"

    return _MD_LINK_RE.sub(_repl, md_text)


def _parse_yaml_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Pure-stdlib YAML frontmatter parser for `title`, `tags`, `aliases`, `subject`, `chapter`."""
    if not text.startswith("---"):
        return {}, text
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text
    end_idx = None
    for i in range(1, min(len(lines), 80)):
        if lines[i].strip() in ("---", "..."):
            end_idx = i
            break
    if end_idx is None:
        return {}, text

    fm: dict[str, Any] = {}
    cur_list_key: str | None = None
    for ln in lines[1:end_idx]:
        stripped = ln.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if cur_list_key and stripped.startswith("- "):
            item = stripped[2:].strip().strip("'\"")
            if item:
                fm.setdefault(cur_list_key, []).append(item)
            continue
        cur_list_key = None
        if ":" not in stripped:
            continue
        k, v = stripped.split(":", 1)
        key = k.strip().lower()
        val = v.strip()
        if not val:
            fm[key] = []
            cur_list_key = key
        elif val.startswith("[") and val.endswith("]"):
            inner = val[1:-1].strip()
            items = [x.strip().strip("'\"") for x in inner.split(",") if x.strip().strip("'\"")]
            fm[key] = items
        else:
            fm[key] = val.strip("'\"")

    body = "\n".join(lines[end_idx + 1 :]).lstrip("\n")
    return fm, body


def _extract_markdown_propositions(
    body: str,
    *,
    fm: dict[str, Any],
    doc_title: str,
    rel_dest: str,
    subject: str,
    default_chapter: str,
    remaining: int,
) -> list[dict[str, Any]]:
    """Extract `status: "planned"` props from `:::cue` blocks, `{{cN::...}}` clozes, and `Q::/A::`."""
    out: list[dict[str, Any]] = []
    if remaining <= 0:
        return out

    eff_subj = _safe_subject(str(fm.get("subject") or ""), default=subject) if fm.get("subject") else subject
    eff_chap = str(fm.get("chapter") or default_chapter or doc_title).strip()
    base_tags = _normalize_tags(fm.get("tags") or [])
    aliases = fm.get("aliases") if isinstance(fm.get("aliases"), list) else []

    # 1. Matuschak `:::cue` blocks
    for idx, cm in enumerate(_CUE_BLOCK_RE.finditer(body), start=1):
        if len(out) >= remaining:
            break
        header_hint = (cm.group(1) or "").strip()
        block = cm.group(2).strip()
        if not block:
            continue
        q_match = re.search(r"(?im)^Q\s*::?\s*(.+)$", block)
        a_match = re.search(r"(?ims)^A\s*::?\s*(.+)$", block)
        if q_match and a_match:
            q_txt = q_match.group(1).strip()
            a_txt = re.sub(r"\s+", " ", a_match.group(1).strip())
        elif header_hint:
            q_txt = header_hint
            a_txt = re.sub(r"\s+", " ", block)
        else:
            block_lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
            q_txt = block_lines[0]
            a_txt = " ".join(block_lines[1:]) if len(block_lines) > 1 else block_lines[0]

        cue_kind = "cloze" if CLOZE_PATTERN.search(q_txt) else "recall"
        title = _derive_title(_unwrap_cloze(q_txt)[0] if cue_kind == "cloze" else q_txt, max_len=92)
        h8 = hashlib.sha1(f"{rel_dest}|cue|{idx}|{q_txt}".encode("utf-8")).hexdigest()[:8]
        out.append(
            _build_planned_proposition(
                prop_id=slugify(f"note-{slugify(eff_chap, default='notes', max_len=14)}-{title[:26]}-{h8}", default=f"note-{h8}", max_len=60),
                title=title,
                statement=a_txt[:360],
                subject=eff_subj,
                chapter=eff_chap,
                kind=cue_kind,
                cues=[{"id": "c1", "q": q_txt, "a": a_txt[:360], "kind": cue_kind}],
                tags=base_tags + ["cue"],
                aliases=aliases,
                source_meta={"kind": "notes_batch", "file": rel_dest, "extractor": "cue_block"},
            )
        )

    # Remove `:::cue` blocks before scanning standalone clozes & Q::/A:: so we don't double-extract
    body_no_cues = _CUE_BLOCK_RE.sub("", body)

    # 2. `Q:: ... / A:: ...` pairs
    for idx, qm in enumerate(_QA_PAIR_RE.finditer(body_no_cues), start=1):
        if len(out) >= remaining:
            break
        q_txt = qm.group(1).strip()
        a_txt = qm.group(2).strip()
        if not q_txt or not a_txt:
            continue
        cue_kind = "cloze" if CLOZE_PATTERN.search(q_txt) else "recall"
        title = _derive_title(q_txt, max_len=92)
        h8 = hashlib.sha1(f"{rel_dest}|qa|{idx}|{q_txt}".encode("utf-8")).hexdigest()[:8]
        out.append(
            _build_planned_proposition(
                prop_id=slugify(f"note-{slugify(eff_chap, default='notes', max_len=14)}-{title[:26]}-{h8}", default=f"note-{h8}", max_len=60),
                title=title,
                statement=a_txt[:360],
                subject=eff_subj,
                chapter=eff_chap,
                kind=cue_kind,
                cues=[{"id": "c1", "q": q_txt, "a": a_txt[:360], "kind": cue_kind}],
                tags=base_tags + ["qa"],
                aliases=aliases,
                source_meta={"kind": "notes_batch", "file": rel_dest, "extractor": "qa_pair"},
            )
        )

    # 3. Inline `{{c1::...}}` clozes
    for idx, para in enumerate(re.split(r"\n\s*\n", body_no_cues), start=1):
        if len(out) >= remaining:
            break
        clean_p = para.strip()
        if not clean_p or not CLOZE_PATTERN.search(clean_p):
            continue
        unwrapped, answers = _unwrap_cloze(clean_p)
        ans_str = "; ".join(answers) if answers else unwrapped
        title = _derive_title(unwrapped or clean_p, max_len=92)
        h8 = hashlib.sha1(f"{rel_dest}|cloze|{idx}|{clean_p}".encode("utf-8")).hexdigest()[:8]
        out.append(
            _build_planned_proposition(
                prop_id=slugify(f"note-{slugify(eff_chap, default='notes', max_len=14)}-{title[:26]}-{h8}", default=f"note-{h8}", max_len=60),
                title=title,
                statement=unwrapped[:360],
                subject=eff_subj,
                chapter=eff_chap,
                kind="cloze",
                cues=[{"id": "c1", "q": clean_p, "a": ans_str[:360], "kind": "cloze"}],
                tags=base_tags + ["cloze"],
                aliases=aliases,
                source_meta={"kind": "notes_batch", "file": rel_dest, "extractor": "cloze"},
            )
        )

    return out


def import_markdown_folder(
    path_or_zip: str | Path,
    *,
    workspace: str | Path | None = None,
    kg_dir: str | Path | None = None,
    slug: str | None = None,
    subject: str = "General",
    extract_props: bool = True,
    enqueue: bool = True,
    limit: int = MAX_CARDS,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Import a local Markdown/Obsidian folder OR Notion `.zip` export cleanly & safely."""
    ws = resolve_workspace(workspace)
    resolved_kg = Path(kg_dir).expanduser().resolve() if kg_dir else resolve_kg_dir(ws)
    clean_subject = _safe_subject(subject, default="General")
    eff_limit = max(1, min(MAX_IMPORTED_ITEMS, int(limit or MAX_CARDS)))

    raw_src = Path(path_or_zip).expanduser()
    if not raw_src.exists():
        raise FileNotFoundError(f"Markdown folder or zip not found: {raw_src}")

    is_zip = raw_src.is_file() and raw_src.suffix.lower() == ".zip"
    if not is_zip and not raw_src.is_dir():
        raise ValueError(f"Expected a directory or Notion .zip archive, got: {raw_src}")

    src_res = raw_src.resolve()
    raw_docs: list[tuple[str, str]] = []  # list of (orig_rel_posix, text_content)
    skipped_symlinks = 0
    total_bytes = 0

    if is_zip:
        _check_regular_file(src_res, max_bytes=MAX_ARCHIVE_BYTES, label="Notion zip archive")
        try:
            zf_ctx = zipfile.ZipFile(src_res, "r")
        except zipfile.BadZipFile as exc:
            raise ValueError(f"Invalid zip archive {src_res.name!r}: {exc}") from exc

        with zf_ctx as zf:
            validated = _validate_zip_members(zf, max_entries=MAX_ENTRIES)
            for info, norm_path in validated:
                if info.is_dir():
                    continue
                parts = norm_path.split("/")
                if any(p in _IGNORED_DIRS or p.startswith(".") for p in parts):
                    continue
                ext = posixpath.splitext(norm_path)[1].lower()
                if ext not in (".md", ".markdown", ".txt"):
                    continue
                data = _stream_read_zip_member(
                    zf,
                    info,
                    max_file_bytes=MAX_FILE,
                    remaining_total_bytes=MAX_ZIP_TOTAL - total_bytes,
                )
                total_bytes += len(data)
                raw_docs.append((norm_path, data.decode("utf-8-sig", errors="replace")))
    else:
        file_count = 0
        for dp, dns, fns in os.walk(src_res, topdown=True, followlinks=False):
            dp_path = Path(dp)
            kept_dirs: list[str] = []
            for d in sorted(dns):
                d_path = dp_path / d
                if d_path.is_symlink():
                    skipped_symlinks += 1
                    continue
                if d.startswith(".") or d in _IGNORED_DIRS:
                    continue
                kept_dirs.append(d)
            dns[:] = kept_dirs

            for fn in sorted(fns):
                fp = dp_path / fn
                if fp.is_symlink():
                    skipped_symlinks += 1
                    continue
                if fn.startswith("."):
                    continue
                ext = fp.suffix.lower()
                if ext not in (".md", ".markdown", ".txt"):
                    continue
                file_count += 1
                if file_count > MAX_ENTRIES:
                    raise ValueError(f"Folder contains more than {MAX_ENTRIES} markdown/text files.")
                fsize = fp.stat().st_size
                if fsize > MAX_FILE:
                    raise ValueError(f"File {fp.name!r} ({fsize} bytes) exceeds safety limit ({MAX_FILE} bytes).")
                total_bytes += fsize
                if total_bytes > MAX_ZIP_TOTAL:
                    raise ValueError(f"Folder batch exceeded cumulative size cap ({MAX_ZIP_TOTAL} bytes).")
                rel = fp.relative_to(src_res).as_posix()
                raw_docs.append((rel, fp.read_text(encoding="utf-8-sig", errors="replace")))

    raw_title = strip_notion_uuid(src_res.stem if is_zip else src_res.name)
    batch_title = raw_title.replace("_", " ").replace("-", " ").strip() or "Imported Notes"
    final_slug = slugify(slug if slug else f"notes-{batch_title}", default="notes-batch", max_len=64)

    # Build deterministic orig_rel -> cleaned_dest_rel map (UUID-stripped)
    used_dest_paths: set[str] = set()
    path_map: dict[str, str] = {}
    for orig_rel, _text in raw_docs:
        path_map[orig_rel] = _clean_notion_rel_path(orig_rel, used_dest_paths)

    out_dir = _ingested_dir_for(ws, final_slug)
    files_dir = _ensure_within_dir(out_dir, out_dir / "files")

    indexed_files: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    queued_count = 0

    for orig_rel, text in raw_docs:
        dest_rel = path_map[orig_rel]
        cleaned_text = _rewrite_notion_links(text, orig_rel=orig_rel, dest_rel=dest_rel, path_map=path_map)
        fm, body = _parse_yaml_frontmatter(cleaned_text)
        headings = [m.group(1).strip() for m in re.finditer(r"^#{1,3}\s+(.+)$", body, re.MULTILINE)]
        wikilinks = sorted(set(re.findall(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]", body)))
        doc_title = str(fm.get("title") or (headings[0] if headings else strip_notion_uuid(posixpath.splitext(posixpath.basename(orig_rel))[0]))).strip()

        dest_path = _ensure_within_dir(files_dir, files_dir / dest_rel)
        rel_ws = str(dest_path.relative_to(ws))

        if not dry_run:
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            write_text_atomic(dest_path, cleaned_text)
            if enqueue:
                from syntopik.ingest import queue_add

                queue_add(
                    title=doc_title,
                    source_path=rel_ws,
                    workspace=ws,
                    kg_dir=resolved_kg,
                    slug=final_slug,
                    priority=60,
                    stage="queued",
                    heading=headings[0] if headings else doc_title,
                    excerpt=" ".join(body.split())[:220],
                )
                queued_count += 1

        if extract_props and len(candidates) < eff_limit:
            extracted = _extract_markdown_propositions(
                body,
                fm=fm,
                doc_title=doc_title,
                rel_dest=dest_rel,
                subject=clean_subject,
                default_chapter=batch_title,
                remaining=eff_limit - len(candidates),
            )
            candidates.extend(extracted)

        indexed_files.append(
            {
                "originalPath": orig_rel,
                "cleanPath": dest_rel,
                "file": rel_ws,
                "title": doc_title,
                "wordCount": len(body.split()),
                "headings": headings[:20],
                "wikilinks": wikilinks[:30],
                "tags": _normalize_tags(fm.get("tags") or []),
            }
        )

    merge = _merge_imported_propositions(
        candidates,
        props_file=resolved_kg / "propositions.jsonl",
        dry_run=bool(dry_run),
    )

    index_path = _ensure_within_dir(out_dir, out_dir / "index.md")
    manifest_path = _ensure_within_dir(out_dir, out_dir / "manifest.json")

    if not dry_run:
        out_dir.mkdir(parents=True, exist_ok=True)
        index_lines = [
            f"# Imported Notes Batch: {batch_title}",
            "",
            f"- **Source**: `{src_res.name}` ({'Notion/Markdown ZIP' if is_zip else 'Local Folder'})",
            f"- **Files Cleaned**: {len(indexed_files)} · **Planned Propositions**: {merge['importedCount']} · **Ingested**: {now_iso()}",
            "",
            "## Files",
            "",
        ]
        for item in indexed_files:
            index_lines.append(f"- `{item['cleanPath']}` — {item['title']} ({item['wordCount']}w)")
        write_text_atomic(index_path, "\n".join(index_lines).rstrip() + "\n")

        manifest = {
            "schemaVersion": 2,
            "kind": "notes_batch",
            "sourceType": "zip" if is_zip else "folder",
            "slug": final_slug,
            "title": batch_title,
            "subject": clean_subject,
            "fileCount": len(indexed_files),
            "files": indexed_files,
            "importedCount": merge["importedCount"],
            "deduplicatedCount": merge["deduplicatedCount"],
            "queuedCount": queued_count,
            "skippedSymlinks": skipped_symlinks,
            "ingestedAt": now_iso(),
        }
        save_json_atomic(manifest_path, manifest)

    return {
        "ok": True,
        "kind": "notes_batch",
        "sourceType": "zip" if is_zip else "folder",
        "slug": final_slug,
        "title": batch_title,
        "subject": clean_subject,
        "fileCount": len(indexed_files),
        "files": indexed_files,
        "outputDir": str(out_dir),
        "filesDir": str(files_dir),
        "indexPath": str(index_path),
        "manifestPath": str(manifest_path),
        "importedCount": merge["importedCount"],
        "deduplicatedCount": merge["deduplicatedCount"],
        "cuesMerged": merge["cuesMerged"],
        "queuedCount": queued_count,
        "skippedSymlinks": skipped_symlinks,
        "propsFile": merge["propsFile"],
        "dryRun": bool(dry_run),
        "propositions": merge["imported"],
    }
