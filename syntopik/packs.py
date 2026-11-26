"""Zero-PII Shareable Portable Study Packs (`.syntopik-pack.zip` / `.json`).

Exports a chapter or subject into a SHA-256-verified portable pack strictly
stripping ALL personal mastery, scheduling, review history, and local path PII,
while preserving DAG propositions, multi-premise chains, ordered topics, and
anonymized canonical traps.

Imports with strict zip-slip, symlink, special-file, and checksum rejection
(matching `scripts/backup_harness.py` security standards) and lexical+ID
deduplication so existing `solid` mastery and FSRS schedules are NEVER overwritten.

Pure Python 3.12+ standard library only (zero external dependencies).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import posixpath
import re
import stat
import sys
import tempfile
import unicodedata
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Safe syntopik.* imports with pure-stdlib fallbacks
# ---------------------------------------------------------------------------

try:
    from syntopik.paths import resolve_chapters_dir as _resolve_chapters_dir
    from syntopik.paths import resolve_exams_dir as _resolve_exams_dir
    from syntopik.paths import resolve_kg_dir as _resolve_kg_dir
    from syntopik.paths import resolve_workspace as _resolve_workspace
    from syntopik.paths import sync_hub as _sync_hub
except Exception:  # pragma: no cover - standalone fallback
    def _resolve_workspace(explicit: Path | str | None = None) -> Path:
        if explicit is not None and str(explicit).strip():
            return Path(explicit).expanduser().resolve()
        env_ws = os.environ.get("SYNT_WORKSPACE", "").strip() or os.environ.get("MLH_WORKSPACE", "").strip()
        if env_ws:
            return Path(env_ws).expanduser().resolve()
        return Path.cwd().resolve()

    def _resolve_kg_dir(workspace: Path | str | None = None, *, explicit: Path | str | None = None) -> Path:
        if explicit is not None and str(explicit).strip():
            return Path(explicit).expanduser().resolve()
        return (_resolve_workspace(workspace) / "knowledge-graph").resolve()

    def _resolve_chapters_dir(workspace: Path | str | None = None, *, explicit: Path | str | None = None) -> Path:
        if explicit is not None and str(explicit).strip():
            return Path(explicit).expanduser().resolve()
        return (_resolve_workspace(workspace) / "chapters").resolve()

    def _resolve_exams_dir(workspace: Path | str | None = None, *, explicit: Path | str | None = None) -> Path:
        if explicit is not None and str(explicit).strip():
            return Path(explicit).expanduser().resolve()
        return (_resolve_workspace(workspace) / "exams").resolve()

    def _sync_hub(**_: Any) -> bool:
        return False


try:
    from syntopik.storage import load_json as _load_json
    from syntopik.storage import load_jsonl as _load_jsonl
    from syntopik.storage import save_json_atomic as _save_json_atomic
    from syntopik.storage import save_jsonl_atomic as _save_jsonl_atomic
except Exception:  # pragma: no cover - standalone fallback
    def _load_json(path: Path | str, default: Any = None, *, strict: bool = False) -> Any:
        p = Path(path).expanduser()
        if not p.is_file():
            if strict:
                raise FileNotFoundError(f"File not found: {p}")
            return default
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            if strict:
                raise
            return default

    def _save_json_atomic(path: Path | str, data: Any, *, indent: int = 2) -> Path:
        target = Path(path).expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(target.parent), prefix=f".{target.name}.", suffix=".tmp")
        tmp_path = Path(tmp)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(data, indent=indent, ensure_ascii=False) + "\n")
            os.replace(tmp_path, target)
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise
        return target

    def _load_jsonl(path: Path | str) -> list[dict[str, Any]]:
        p = Path(path).expanduser()
        if not p.is_file():
            return []
        rows: list[dict[str, Any]] = []
        for ln in p.read_text(encoding="utf-8").splitlines():
            s = ln.strip()
            if not s:
                continue
            try:
                obj = json.loads(s)
            except Exception:
                continue
            if isinstance(obj, dict):
                rows.append(obj)
        return rows

    def _save_jsonl_atomic(path: Path | str, rows: list[dict[str, Any]]) -> Path:
        target = Path(path).expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        body = "\n".join(json.dumps(r, ensure_ascii=False) for r in rows)
        fd, tmp = tempfile.mkstemp(dir=str(target.parent), prefix=f".{target.name}.", suffix=".tmp")
        tmp_path = Path(tmp)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write((body + "\n") if body else "")
            os.replace(tmp_path, target)
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise
        return target


try:
    from syntopik.schemas import normalize_chain as _normalize_chain
    from syntopik.schemas import now_iso as _now_iso
    from syntopik.schemas import slugify as _slugify
except Exception:  # pragma: no cover - standalone fallback
    def _now_iso() -> str:
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    def _slugify(text: str, *, default: str = "pack", max_len: int = 64) -> str:
        norm = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode("ascii")
        slug = re.sub(r"[^a-z0-9]+", "-", norm.lower()).strip("-")
        return (slug[:max_len] if max_len > 0 else slug) or default

    def _normalize_chain(raw: dict[str, Any]) -> dict[str, Any] | None:
        if not isinstance(raw, dict):
            return None
        conc = str(raw.get("conclusion") or raw.get("to") or "").strip()
        if not conc:
            return None
        prems: list[str] = []
        if isinstance(raw.get("premises"), list):
            for p in raw["premises"]:
                ps = str(p or "").strip()
                if ps and ps != conc and ps not in prems:
                    prems.append(ps)
        else:
            src = str(raw.get("from") or "").strip()
            if src and src != conc:
                prems = [src]
        if not prems:
            return None
        rel = str(raw.get("relation") or "requires").strip() or "requires"
        ew = raw.get("encompassWeight", 0.35 if len(prems) >= 2 else 0.50)
        return {
            "premises": prems,
            "conclusion": conc,
            "relation": rel,
            "why": str(raw.get("why") or raw.get("note") or "").strip(),
            "chainKey": f"{'+'.join(sorted(prems))}>{conc}",
            "encompassWeight": float(ew) if isinstance(ew, (int, float)) else 0.50,
        }


__all__ = [
    "PACK_SCHEMA_VERSION",
    "MANIFEST_NAME",
    "DATA_NAME",
    "CHECKSUMS_NAME",
    "STRIPPED_PERSONAL_FIELDS",
    "EXCLUDED_PERSONAL_FILES",
    "is_safe_archive_path",
    "verify_pack",
    "export_pack",
    "inspect_pack",
    "import_pack",
    "main",
]

PACK_SCHEMA_VERSION = 1
MANIFEST_NAME = "pack-manifest.json"
DATA_NAME = "pack-data.json"
CHECKSUMS_NAME = "SHA256SUMS.json"

MAX_PACK_UNCOMPRESSED_BYTES = 50 * 1024 * 1024  # 50 MiB zip-bomb ceiling
MAX_PACK_MEMBERS = 512

# Learner-private files that are NEVER exported into or accepted inside portable study packs (zero-PII guarantee)
EXCLUDED_PERSONAL_FILES: tuple[str, ...] = (
    "learner-profile.json",
    "whiteboard-session.json",
    "evidence-ledger.jsonl",
    "viva-sessions.jsonl",
    "blitz-sessions.json",
    "exam-attempts.jsonl",
    "revlog.jsonl",
    "mistakes.jsonl",
    "fsrs-params.json",
    "study-config.json",
)
_EXCLUDED_FILES_SET: frozenset[str] = frozenset(EXCLUDED_PERSONAL_FILES)

# Canonical list of personal / learner-private & unsafe fields ALWAYS stripped from exported/imported packs
STRIPPED_PERSONAL_FIELDS: tuple[str, ...] = (
    "fsrs",
    "bktMastery",
    "nextReview",
    "lapses",
    "revlog",
    "personalNotes",
    "lastReviewed",
    "lastImplicitReview",
    "lastTested",
    "confidence",
    "implicitCredit",
    "lastRating",
    "reviews",
    "notes",
    "coveredPages",
    "nextBreadcrumb",
    "retestQueue",
    "wrongCount",
    "latencyMsHistory",
    "blunderRepeatCount",
    "winCount",
    "responses",
    "startedAt",
    "submittedAt",
    "timeSpentSec",
    "svg",
    "learnerProfile",
    "recentEvents",
    "affectiveState",
    "failureDna",
    "userOverrides",
    "pins",
    "attemptHistory",
    "attempts",
    "completions",
    "lastAttemptAt",
    "passed",
    "blackoutPassed",
    "axiomCutPassed",
    "vivaHistory",
    "entryHash",
    "prevHash",
    "durationMs",
    "hintsUsed",
    "learnerConfidence",
    "hypercorrection",
    "luckyGuess",
)
_STRIPPED_SET: frozenset[str] = frozenset(STRIPPED_PERSONAL_FIELDS)

_DRIVE_PREFIX_RE = re.compile(r"^[A-Za-z]:")
_ABS_LOCAL_PATH_RE = re.compile(
    r"(?:"
    r"(?:/Users|/home)/[^\s/\\:\"'<>|]+"
    r"(?:/[^\s/\\:\"'<>|,;()\[\]{}]+(?: [^\s/\\:\"'<>|,;()\[\]{}]+){0,5}(?=/))*"
    r"(?:/(?:[^\s/\\:\"'<>|,;()\[\]{}]+(?: [^\s/\\:\"'<>|,;()\[\]{}]+){0,4}\.[A-Za-z0-9]{1,10}"
    r"|[^\s/\\:\"'<>|.,;()\[\]{}]+(?: [A-Z0-9][^\s/\\:\"'<>|.,;()\[\]{}]*){0,4}"
    r"|[^\s/\\:\"'<>|]+))?/?"
    r"|"
    r"[A-Za-z]:\\[^\s/\\:\"'<>|]+"
    r"(?:\\[^\s/\\:\"'<>|,;()\[\]{}]+(?: [^\s/\\:\"'<>|,;()\[\]{}]+){0,5}(?=\\))*"
    r"(?:\\(?:[^\s/\\:\"'<>|,;()\[\]{}]+(?: [^\s/\\:\"'<>|,;()\[\]{}]+){0,4}\.[A-Za-z0-9]{1,10}"
    r"|[^\s/\\:\"'<>|.,;()\[\]{}]+(?: [A-Z0-9][^\s/\\:\"'<>|.,;()\[\]{}]*){0,4}"
    r"|[^\s/\\:\"'<>|]+))?\\?"
    r")"
)
_SAFE_CHAPTER_MD_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}\.md$")
_SAFE_EXAM_FILE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}\.(?:json|html)$", re.IGNORECASE)

_STOP_WORDS = frozenset(
    (
        "a an the and or of to in on at by for from with as is are was were be been being "
        "it its this that these those which who whose their them they we our you your "
        "can could may might must will would should has have had does do did"
    ).split()
)


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_json_bytes(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _redact_local_paths_raw(text: str) -> str:
    return _ABS_LOCAL_PATH_RE.sub("<redacted-path>", str(text or ""))


def _strip_local_paths(text: str) -> str:
    return _redact_local_paths_raw(text).strip()


def _deep_strip_pii(obj: Any) -> Any:
    """Recursively strip any forbidden personal/private keys as defense-in-depth."""
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for k, v in obj.items():
            if k in _STRIPPED_SET:
                continue
            out[k] = _deep_strip_pii(v)
        return out
    if isinstance(obj, list):
        return [_deep_strip_pii(item) for item in obj]
    if isinstance(obj, str):
        return _redact_local_paths_raw(obj)
    return obj


# ---------------------------------------------------------------------------
# Security: zip-slip, symlink & integrity guards (backup_harness.py standard)
# ---------------------------------------------------------------------------


def is_safe_archive_path(raw_name: str) -> tuple[bool, str]:
    """Return `(is_safe, normalized_posix_path_or_reason)` blocking zip-slip attacks."""
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
            return True, "symlink entry disallowed in study pack"
        if stat.S_ISCHR(mode) or stat.S_ISBLK(mode) or stat.S_ISFIFO(mode) or stat.S_ISSOCK(mode):
            return True, "special device/FIFO entry disallowed in study pack"
    return False, ""


# ---------------------------------------------------------------------------
# Pure-stdlib lexical deduplication (matches srs_engine.py find-similar)
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


def _find_best_similar_existing(
    cand: dict[str, Any],
    existing_props: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, float]:
    cand_id = str(cand.get("id") or "").strip().lower()
    cand_title = str(cand.get("title") or "").strip()
    cand_stmt = str(cand.get("statement") or "").strip()
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
        sc = max(
            _lexical_similarity(cand_title, etitle),
            _lexical_similarity(cand_blob, f"{etitle} {estmt}".strip()),
        )
        if sc > best_score:
            best_score = sc
            best_prop = ep
    return best_prop, best_score


# ---------------------------------------------------------------------------
# Clean Zero-PII sanitation for propositions, chains, topics, canonicalTraps
# ---------------------------------------------------------------------------


def _sanitize_cue(cue: dict[str, Any], idx: int) -> dict[str, Any] | None:
    if not isinstance(cue, dict):
        return None
    q = _strip_local_paths(str(cue.get("q") or ""))
    a = _strip_local_paths(str(cue.get("a") or ""))
    if not q and not a:
        return None
    out: dict[str, Any] = {
        "id": str(cue.get("id") or f"c{idx + 1}").strip(),
        "q": q,
        "a": a,
    }
    if isinstance(cue.get("occlusion"), list) and cue["occlusion"]:
        out["occlusion"] = _deep_strip_pii(cue["occlusion"])
    return out


def _sanitize_proposition(prop: dict[str, Any]) -> dict[str, Any]:
    clean_cues: list[dict[str, Any]] = []
    for idx, c in enumerate(prop.get("cues") or []):
        sc = _sanitize_cue(c, idx)
        if sc is not None:
            clean_cues.append(sc)

    out: dict[str, Any] = {
        "id": str(prop.get("id") or "").strip(),
        "title": _strip_local_paths(str(prop.get("title") or "")),
        "subject": str(prop.get("subject") or "General").strip(),
        "chapter": str(prop.get("chapter") or "").strip(),
        "kind": str(prop.get("kind") or "derivation").strip(),
        "statement": _strip_local_paths(str(prop.get("statement") or prop.get("title") or "")),
        "cues": clean_cues,
        "occlusion": _deep_strip_pii(prop.get("occlusion") or []),
    }
    if isinstance(prop.get("aliases"), list) and prop["aliases"]:
        out["aliases"] = [_strip_local_paths(str(a)) for a in prop["aliases"] if str(a).strip()]
    if isinstance(prop.get("boundaryConditions"), list) and prop["boundaryConditions"]:
        out["boundaryConditions"] = [_strip_local_paths(str(b)) for b in prop["boundaryConditions"] if str(b).strip()]
    return _deep_strip_pii(out)


def _sanitize_topic(topic: dict[str, Any], idx: int) -> dict[str, Any]:
    return {
        "id": str(topic.get("id") or f"topic-{idx + 1}").strip(),
        "title": _strip_local_paths(str(topic.get("title") or f"Topic {idx + 1}")),
        "order": int(topic.get("order") or (idx + 1)),
        "level": str(topic.get("level") or "basics").strip(),
        "prereqTopicIds": [str(x).strip() for x in (topic.get("prereqTopicIds") or []) if str(x).strip()],
        "propIds": [str(x).strip() for x in (topic.get("propIds") or []) if str(x).strip()],
    }


def _sanitize_trap(mistake: dict[str, Any]) -> dict[str, Any] | None:
    trap_text = _strip_local_paths(str(mistake.get("wrongMove") or mistake.get("triggerCue") or ""))
    cue_text = _strip_local_paths(str(mistake.get("correctMove") or ""))
    if not trap_text and not cue_text:
        return None
    return {
        "propId": str(mistake.get("propId") or "").strip() or None,
        "category": str(mistake.get("category") or "misconception").strip(),
        "trap": trap_text,
        "actionCue": cue_text,
    }


def _sanitize_derivation(raw: dict[str, Any]) -> dict[str, Any] | None:
    """Sanitize a derivation DAG preserving structure only (stripping all personal attempt history)."""
    if not isinstance(raw, dict):
        return None
    did = str(raw.get("id") or "").strip()
    title = _strip_local_paths(str(raw.get("title") or ""))
    raw_steps = raw.get("steps")
    if not did or not isinstance(raw_steps, list) or not raw_steps:
        return None

    clean_steps: list[dict[str, Any]] = []
    for idx, st in enumerate(raw_steps):
        if not isinstance(st, dict):
            continue
        sid = str(st.get("id") or f"s{idx + 1}").strip()
        role = str(st.get("role") or "step").strip().lower()
        if role not in ("axiom", "step", "theorem"):
            role = "step"
        deps = [str(d).strip() for d in (st.get("dependsOn") or []) if str(d).strip()]
        clean_steps.append(
            {
                "id": sid,
                "label": _strip_local_paths(str(st.get("label") or sid)),
                "expr": _strip_local_paths(str(st.get("expr") or "")),
                "role": role,
                "dependsOn": deps,
                "note": _strip_local_paths(str(st.get("note") or "")),
            }
        )
    if not clean_steps:
        return None

    out: dict[str, Any] = {
        "id": did,
        "propId": str(raw.get("propId") or "").strip() or None,
        "chapter": _strip_local_paths(str(raw.get("chapter") or "")),
        "title": title or did,
        "domain": str(raw.get("domain") or raw.get("subject") or "General").strip(),
        "steps": clean_steps,
    }
    return _deep_strip_pii(out)


def _find_matching_tracker(chapters_dir: Path, target: str) -> tuple[dict[str, Any] | None, str | None]:
    if not chapters_dir.is_dir():
        return None, None
    t_low = target.strip().lower()
    t_slug = _slugify(target)
    direct = chapters_dir / f"{t_slug}-tracker.json"
    if direct.is_file():
        data = _load_json(direct, default=None)
        if isinstance(data, dict):
            return data, str(data.get("slug") or t_slug)
    for path in sorted(chapters_dir.glob("*-tracker.json")):
        data = _load_json(path, default=None)
        if not isinstance(data, dict):
            continue
        slug = str(data.get("slug") or path.name.replace("-tracker.json", "")).lower()
        title = str(data.get("title") or "").lower()
        subj = str(data.get("subject") or "").lower()
        if t_low in (slug, title, subj) or t_slug == slug or (t_low and t_low in title):
            return data, str(data.get("slug") or slug)
    return None, None


# ---------------------------------------------------------------------------
# Public API: export_pack, verify_pack, inspect_pack, import_pack
# ---------------------------------------------------------------------------


def export_pack(
    target: str,
    *,
    workspace: Path | str | None = None,
    kg_dir: Path | str | None = None,
    chapters_dir: Path | str | None = None,
    out_path: Path | str | None = None,
    author: str = "",
    license_id: str = "CC-BY-4.0",
    title: str | None = None,
    include_exams: bool = False,
) -> dict[str, Any]:
    """Export a chapter slug or subject into a zero-PII SHA-256-verified `.syntopik-pack.zip` or `.json`."""
    ws = _resolve_workspace(workspace)
    kdir = _resolve_kg_dir(workspace=ws, explicit=kg_dir)
    cdir = _resolve_chapters_dir(workspace=ws, explicit=chapters_dir)

    target_str = str(target or "").strip()
    if not target_str:
        raise ValueError("Target chapter slug or subject is required.")
    t_low = target_str.lower()
    t_slug = _slugify(target_str)

    tracker, matched_slug = _find_matching_tracker(cdir, target_str)
    all_props = _load_jsonl(kdir / "propositions.jsonl")
    all_chains = _load_jsonl(kdir / "chains.jsonl")
    all_mistakes = _load_jsonl(kdir / "mistakes.jsonl")

    tracker_prop_ids: set[str] = set()
    clean_topics: list[dict[str, Any]] = []
    if isinstance(tracker, dict):
        for idx, t in enumerate(tracker.get("topics") or []):
            if not isinstance(t, dict):
                continue
            ct = _sanitize_topic(t, idx)
            clean_topics.append(ct)
            tracker_prop_ids.update(ct["propIds"])

    tracker_title_low = str((tracker or {}).get("title") or "").lower()
    tracker_subj = str((tracker or {}).get("subject") or "").strip()

    matched_props: list[dict[str, Any]] = []
    for p in all_props:
        pid = str(p.get("id") or "").strip()
        p_subj = str(p.get("subject") or "").strip().lower()
        p_chap = str(p.get("chapter") or "").strip().lower()
        p_chap_slug = _slugify(p_chap, default="") if p_chap else ""
        if (
            t_low in ("all", "*")
            or (pid and pid in tracker_prop_ids)
            or p_subj == t_low
            or p_chap == t_low
            or (t_slug and p_chap_slug == t_slug)
            or (t_low and t_low in p_chap)
            or (matched_slug and matched_slug.lower() in p_chap)
            or (tracker_title_low and p_chap and (p_chap in tracker_title_low or tracker_title_low in p_chap))
        ):
            matched_props.append(_sanitize_proposition(p))

    prop_id_set = {p["id"] for p in matched_props if p.get("id")}

    # Collect chains whose endpoints belong to exported propositions
    clean_chains: list[dict[str, Any]] = []
    seen_chain_keys: set[str] = set()
    for raw_c in all_chains:
        norm = _normalize_chain(raw_c)
        if not norm:
            continue
        if norm["conclusion"] in prop_id_set and all(pr in prop_id_set for pr in norm["premises"]):
            if norm["chainKey"] in seen_chain_keys:
                continue
            seen_chain_keys.add(norm["chainKey"])
            clean_chains.append(
                {
                    "premises": norm["premises"],
                    "conclusion": norm["conclusion"],
                    "relation": norm["relation"],
                    "why": _strip_local_paths(norm.get("why") or ""),
                    "encompassWeight": norm["encompassWeight"],
                }
            )

    # Anonymized canonical traps from mistakes matching exported propositions/chapter/subject
    canonical_traps: list[dict[str, Any]] = []
    seen_traps: set[tuple[str, str, str]] = set()
    for m in all_mistakes:
        m_pid = str(m.get("propId") or "").strip()
        m_subj = str(m.get("subject") or "").strip().lower()
        m_chap = str(m.get("chapter") or "").strip().lower()
        if (
            t_low in ("all", "*")
            or (m_pid and m_pid in prop_id_set)
            or m_subj == t_low
            or (t_low and t_low in m_chap)
        ):
            ct = _sanitize_trap(m)
            if ct is None:
                continue
            dedup_key = (str(ct.get("category")), str(ct.get("trap")), str(ct.get("actionCue")))
            if dedup_key not in seen_traps:
                seen_traps.add(dedup_key)
                canonical_traps.append(ct)

    # Clean optional derivation DAGs (`derivations.jsonl`, strictly sanitized: zero personal attempt history)
    # Note: EXCLUDED_PERSONAL_FILES (learner-profile.json, whiteboard-session.json, evidence-ledger.jsonl,
    # viva-sessions.jsonl, blitz-sessions.json, exam-attempts.jsonl, revlog.jsonl, etc.) are NEVER read or exported.
    all_derivations = _load_jsonl(kdir / "derivations.jsonl")
    clean_derivations: list[dict[str, Any]] = []
    seen_deriv_ids: set[str] = set()
    for raw_d in all_derivations:
        if not isinstance(raw_d, dict):
            continue
        d_pid = str(raw_d.get("propId") or "").strip()
        d_dom = str(raw_d.get("domain") or raw_d.get("subject") or "").strip().lower()
        d_chap = str(raw_d.get("chapter") or "").strip().lower()
        d_chap_slug = _slugify(d_chap, default="") if d_chap else ""
        if (
            t_low in ("all", "*")
            or (d_pid and d_pid in prop_id_set)
            or d_dom == t_low
            or d_chap == t_low
            or (t_slug and d_chap_slug == t_slug)
            or (t_low and t_low in d_chap)
            or (matched_slug and matched_slug.lower() in d_chap)
        ):
            sd = _sanitize_derivation(raw_d)
            if sd is not None and sd["id"] not in seen_deriv_ids:
                seen_deriv_ids.add(sd["id"])
                clean_derivations.append(sd)

    clean_exams: list[dict[str, Any]] = []
    if include_exams:
        exams_dir = _resolve_exams_dir(workspace=ws)
        if exams_dir.is_dir():
            for ep in sorted(exams_dir.glob("*.json")):
                if ep.name.lower() in _EXCLUDED_FILES_SET:
                    continue
                stem_low = ep.stem.lower()
                if "-attempt-" in stem_low or stem_low.endswith("-attempt") or "attempt" in stem_low.split("-"):
                    continue
                if t_slug in _slugify(ep.stem) or t_low in ("all", "*"):
                    ed = _load_json(ep, default=None)
                    if isinstance(ed, dict) and isinstance(ed.get("questions"), list):
                        clean_exams.append(_deep_strip_pii(ed))

    pack_slug = matched_slug or t_slug
    pack_title = str(
        title
        or (tracker or {}).get("title")
        or (matched_props[0].get("chapter") if matched_props and matched_props[0].get("chapter") else target_str)
    ).strip()
    pack_subject = tracker_subj or (matched_props[0].get("subject") if matched_props else "General")

    data_payload: dict[str, Any] = {
        "propositions": matched_props,
        "chains": clean_chains,
        "topics": clean_topics,
        "canonicalTraps": canonical_traps,
    }
    if clean_derivations:
        data_payload["derivations"] = clean_derivations
    if include_exams:
        data_payload["exams"] = clean_exams

    data_bytes = (json.dumps(data_payload, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    data_sha = _sha256_bytes(data_bytes)
    content_sha = _sha256_bytes(_canonical_json_bytes(data_payload))

    manifest: dict[str, Any] = {
        "packSchema": PACK_SCHEMA_VERSION,
        "id": pack_slug,
        "slug": pack_slug,
        "title": pack_title,
        "subject": pack_subject,
        "author": str(author or "").strip(),
        "license": str(license_id or "CC-BY-4.0").strip(),
        "createdAt": _now_iso(),
        "strippedFields": list(STRIPPED_PERSONAL_FIELDS),
        "excludedPersonalFiles": list(EXCLUDED_PERSONAL_FILES),
        "propositions": matched_props,
        "chains": clean_chains,
        "topics": clean_topics,
        "canonicalTraps": canonical_traps,
        "contentSha256": content_sha,
        "sha256": {
            DATA_NAME: data_sha,
        },
    }
    if clean_derivations:
        manifest["derivations"] = clean_derivations
    if include_exams:
        manifest["exams"] = clean_exams

    if out_path is not None and str(out_path).strip():
        dest = Path(out_path).expanduser().resolve()
    else:
        dest = (ws / f"{pack_slug}.syntopik-pack.zip").resolve()
    dest.parent.mkdir(parents=True, exist_ok=True)

    is_json_out = dest.suffix.lower() == ".json"
    if is_json_out:
        _save_json_atomic(dest, manifest, indent=2)
        pack_bytes = dest.stat().st_size
        fmt = "json"
    else:
        manifest_bytes = (json.dumps(manifest, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
        manifest_sha = _sha256_bytes(manifest_bytes)
        checksums_obj = {
            "packSchema": PACK_SCHEMA_VERSION,
            "sha256": {
                MANIFEST_NAME: manifest_sha,
                DATA_NAME: data_sha,
            },
        }
        checksums_bytes = (json.dumps(checksums_obj, indent=2, ensure_ascii=False) + "\n").encode("utf-8")

        fd, tmp_name = tempfile.mkstemp(dir=str(dest.parent), prefix=f".{dest.name}.", suffix=".tmp")
        os.close(fd)
        tmp_path = Path(tmp_name)
        try:
            with zipfile.ZipFile(tmp_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                for member_name, payload in (
                    (DATA_NAME, data_bytes),
                    (MANIFEST_NAME, manifest_bytes),
                    (CHECKSUMS_NAME, checksums_bytes),
                ):
                    zi = zipfile.ZipInfo(member_name)
                    zi.compress_type = zipfile.ZIP_DEFLATED
                    zi.external_attr = 0o100644 << 16
                    zf.writestr(zi, payload)
            os.replace(tmp_path, dest)
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise
        pack_bytes = dest.stat().st_size
        fmt = "zip"

    return {
        "ok": True,
        "format": fmt,
        "outPath": str(dest),
        "bytes": pack_bytes,
        "id": pack_slug,
        "title": pack_title,
        "subject": pack_subject,
        "propositionCount": len(matched_props),
        "chainCount": len(clean_chains),
        "topicCount": len(clean_topics),
        "canonicalTrapCount": len(canonical_traps),
        "derivationCount": len(clean_derivations),
        "contentSha256": content_sha,
        "strippedFields": list(STRIPPED_PERSONAL_FIELDS),
        "excludedPersonalFiles": list(EXCLUDED_PERSONAL_FILES),
    }


def _is_attempt_filename(name: str) -> bool:
    stem_low = Path(name).stem.lower()
    return (
        name.lower() in _EXCLUDED_FILES_SET
        or "-attempt-" in stem_low
        or stem_low.endswith("-attempt")
        or "attempt" in re.split(r"[-_]", stem_low)
    )


def _is_code_arena(arena: dict[str, Any]) -> bool:
    """Return True if an arena executes Python/code (must require explicit allow_code_arenas=True)."""
    dom = str(arena.get("domain") or "code").strip().lower()
    if dom == "code":
        return True
    ref = arena.get("sealedReference")
    if isinstance(ref, dict) and isinstance(ref.get("code"), str) and ref["code"].strip():
        return True
    for mut in arena.get("sealedMutants") or []:
        if isinstance(mut, dict) and isinstance(mut.get("code"), str) and mut["code"].strip():
            return True
    for pred in arena.get("assumptionPredicates") or []:
        if isinstance(pred, dict) and isinstance(pred.get("code"), str) and pred["code"].strip():
            return True
    return False


def _sanitize_fork_for_pack(
    raw: dict[str, Any],
    *,
    pack_id: Any,
    id_remap: dict[str, str],
    suspend: bool,
) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    fid = str(raw.get("id") or "").strip()
    if not fid:
        return None
    clean = _deep_strip_pii(raw)
    if not isinstance(clean, dict):
        return None
    if not str(clean.get("sealedWinningMove") or "").strip() or not str(clean.get("sealedBlunder") or "").strip():
        return None
    prop_id = str(clean.get("propId") or "").strip()
    if prop_id:
        clean["propId"] = id_remap.get(prop_id, prop_id)
    clean["id"] = fid
    clean["status"] = "active"
    clean["nextReview"] = _now_iso()[:10]
    clean["lastTested"] = None
    clean["blunderRepeatCount"] = 0
    clean["winCount"] = 0
    clean["latencyMsHistory"] = []
    clean["importedFromPack"] = pack_id
    if suspend:
        clean["suspended"] = True
    return clean


def _sanitize_arena_for_pack(
    raw: dict[str, Any],
    *,
    pack_id: Any,
    id_remap: dict[str, str],
) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    aid = str(raw.get("id") or "").strip()
    if not aid:
        return None
    clean = _deep_strip_pii(raw)
    if not isinstance(clean, dict):
        return None
    prop_id = str(clean.get("propId") or "").strip()
    if prop_id:
        clean["propId"] = id_remap.get(prop_id, prop_id)
    clean["id"] = aid
    clean["killedMutants"] = []
    clean["bestMinimalSize"] = None
    clean["attempts"] = 0
    clean["lemmaRepairs"] = []
    clean["importedFromPack"] = pack_id
    return clean


def _save_text_atomic(path: Path | str, text: str) -> Path:
    target = Path(path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(target.parent), prefix=f".{target.name}.", suffix=".tmp")
    tmp_path = Path(tmp)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp_path, target)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise
    return target


def _parse_jsonl_bytes(raw_bytes: bytes) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for ln in raw_bytes.decode("utf-8", errors="replace").splitlines():
        s = ln.strip()
        if not s:
            continue
        try:
            obj = json.loads(s)
        except Exception:
            continue
        if isinstance(obj, dict):
            rows.append(obj)
    return rows


def _read_and_verify_pack_with_files(
    pack_path: Path | str,
) -> tuple[dict[str, Any], dict[str, Any] | None, dict[str, bytes]]:
    """Verify zip-slip/symlink safety and SHA-256 checksums; return `(report, manifest, raw_files)`."""
    p = Path(pack_path).expanduser().resolve()
    errors: list[str] = []
    unsafe_paths: list[str] = []
    mismatches: list[dict[str, Any]] = []

    if not p.is_file():
        return {
            "ok": False,
            "path": str(p),
            "format": "unknown",
            "unsafePaths": [],
            "mismatches": [],
            "errors": [f"Pack file not found: {p}"],
        }, None, {}

    # Standalone JSON pack
    if p.suffix.lower() == ".json" and not zipfile.is_zipfile(p):
        try:
            if p.stat().st_size > MAX_PACK_UNCOMPRESSED_BYTES:
                return {
                    "ok": False,
                    "path": str(p),
                    "format": "json",
                    "unsafePaths": [],
                    "mismatches": [],
                    "errors": [f"JSON pack exceeds max size ({MAX_PACK_UNCOMPRESSED_BYTES} B)."],
                }, None, {}
            manifest = json.loads(p.read_text(encoding="utf-8"))
        except Exception as exc:
            return {
                "ok": False,
                "path": str(p),
                "format": "json",
                "unsafePaths": [],
                "mismatches": [],
                "errors": [f"Malformed JSON pack: {exc}"],
            }, None, {}
        if not isinstance(manifest, dict) or not isinstance(manifest.get("propositions"), list):
            errors.append("Malformed JSON pack: expected object with 'propositions' list.")
            return {
                "ok": False,
                "path": str(p),
                "format": "json",
                "unsafePaths": [],
                "mismatches": [],
                "errors": errors,
            }, None, {}

        exp_content_sha = manifest.get("contentSha256")
        if not isinstance(exp_content_sha, str) or not exp_content_sha.strip():
            errors.append("Missing required contentSha256 in JSON study pack.")
        else:
            payload: dict[str, Any] = {
                "propositions": manifest.get("propositions", []),
                "chains": manifest.get("chains", []),
                "topics": manifest.get("topics", []),
                "canonicalTraps": manifest.get("canonicalTraps", []),
            }
            if "derivations" in manifest:
                payload["derivations"] = manifest["derivations"]
            if "exams" in manifest:
                payload["exams"] = manifest["exams"]
            actual_sha = _sha256_bytes(_canonical_json_bytes(payload))
            if actual_sha != exp_content_sha:
                mismatches.append({"path": "contentSha256", "expected": exp_content_sha, "actual": actual_sha})
                errors.append("Content SHA-256 mismatch in JSON study pack.")

        return {
            "ok": len(errors) == 0,
            "path": str(p),
            "format": "json",
            "unsafePaths": unsafe_paths,
            "mismatches": mismatches,
            "errors": errors,
        }, (manifest if not errors else None), {}

    # ZIP pack verification
    if not zipfile.is_zipfile(p):
        return {
            "ok": False,
            "path": str(p),
            "format": "unknown",
            "unsafePaths": [],
            "mismatches": [],
            "errors": ["File is neither a valid ZIP archive nor a .json pack."],
        }, None, {}

    raw_files: dict[str, bytes] = {}
    seen_names: set[str] = set()
    total_uncompressed = 0

    try:
        with zipfile.ZipFile(p, "r") as zf:
            infos = zf.infolist()
            if len(infos) > MAX_PACK_MEMBERS:
                errors.append(f"Archive member count ({len(infos)}) exceeds limit ({MAX_PACK_MEMBERS}).")
                infos = []
            for info in infos:
                ok_path, norm_or_reason = is_safe_archive_path(info.filename)
                if not ok_path:
                    unsafe_paths.append(info.filename)
                    errors.append(f"Unsafe archive path ({norm_or_reason})")
                    continue

                is_bad_type, type_reason = _zip_info_is_symlink_or_special(info)
                if is_bad_type:
                    unsafe_paths.append(info.filename)
                    errors.append(f"{type_reason}: {info.filename!r}")
                    continue

                if info.is_dir():
                    continue

                if posixpath.basename(norm_or_reason).lower() in _EXCLUDED_FILES_SET:
                    unsafe_paths.append(info.filename)
                    errors.append(f"Excluded personal/PII file disallowed in study pack: {norm_or_reason!r}")
                    continue

                if norm_or_reason in seen_names:
                    errors.append(f"Duplicate archive entry: {norm_or_reason!r}")
                    continue
                seen_names.add(norm_or_reason)

                remaining = MAX_PACK_UNCOMPRESSED_BYTES - total_uncompressed
                if remaining < 0 or max(0, int(info.file_size)) > remaining:
                    errors.append(f"Archive exceeds max uncompressed size ({MAX_PACK_UNCOMPRESSED_BYTES} B)")
                    break

                # Bounded streaming read defeats fake-header zip bombs (info.file_size lies)
                with zf.open(info, "r") as member_fh:
                    data = member_fh.read(remaining + 1)
                if len(data) > remaining:
                    errors.append(
                        f"Archive member {norm_or_reason!r} exceeded max uncompressed size ({MAX_PACK_UNCOMPRESSED_BYTES} B)"
                    )
                    break
                total_uncompressed += len(data)
                raw_files[norm_or_reason] = data
    except (zipfile.BadZipFile, OSError) as exc:
        return {
            "ok": False,
            "path": str(p),
            "format": "zip",
            "unsafePaths": unsafe_paths,
            "mismatches": mismatches,
            "errors": [f"Failed to read zip archive: {exc}"],
        }, None, {}

    if unsafe_paths or errors:
        return {
            "ok": False,
            "path": str(p),
            "format": "zip",
            "unsafePaths": unsafe_paths,
            "mismatches": mismatches,
            "errors": errors,
        }, None, {}

    if MANIFEST_NAME not in raw_files:
        errors.append(f"Missing {MANIFEST_NAME} in pack archive.")
        return {
            "ok": False,
            "path": str(p),
            "format": "zip",
            "unsafePaths": unsafe_paths,
            "mismatches": mismatches,
            "errors": errors,
        }, None, {}

    try:
        manifest = json.loads(raw_files[MANIFEST_NAME].decode("utf-8"))
        if not isinstance(manifest, dict):
            raise ValueError("Manifest root must be a JSON object.")
    except Exception as exc:
        errors.append(f"Invalid {MANIFEST_NAME}: {exc}")
        return {
            "ok": False,
            "path": str(p),
            "format": "zip",
            "unsafePaths": unsafe_paths,
            "mismatches": mismatches,
            "errors": errors,
        }, None, {}

    # Require pack-data.json and SHA256SUMS.json on ZIP packs (fail closed if stripped!)
    if DATA_NAME not in raw_files:
        errors.append(f"Missing required {DATA_NAME} in pack archive.")
    if CHECKSUMS_NAME not in raw_files:
        errors.append(f"Missing required {CHECKSUMS_NAME} in pack archive.")
    else:
        try:
            sums_obj = json.loads(raw_files[CHECKSUMS_NAME].decode("utf-8"))
            sha_map = sums_obj.get("sha256") if isinstance(sums_obj, dict) else None
            if not isinstance(sha_map, dict):
                errors.append(f"Malformed {CHECKSUMS_NAME}: missing 'sha256' object.")
            else:
                for req_member in (MANIFEST_NAME, DATA_NAME):
                    if req_member not in sha_map:
                        errors.append(f"{CHECKSUMS_NAME} missing required checksum for {req_member}")
                for rel_name, exp_sha in sha_map.items():
                    ok_r, norm_r = is_safe_archive_path(str(rel_name))
                    if not ok_r:
                        unsafe_paths.append(str(rel_name))
                        errors.append(f"Unsafe checksum path ({norm_r})")
                        continue
                    if norm_r not in raw_files:
                        errors.append(f"Missing file listed in {CHECKSUMS_NAME}: {norm_r}")
                        continue
                    act_sha = _sha256_bytes(raw_files[norm_r])
                    if not isinstance(exp_sha, str) or act_sha != exp_sha:
                        mismatches.append({"path": norm_r, "expected": exp_sha, "actual": act_sha})
                        errors.append(f"SHA-256 mismatch for {norm_r}")
                for extra_name in sorted(raw_files.keys()):
                    if extra_name != CHECKSUMS_NAME and extra_name not in sha_map:
                        errors.append(f"Unexpected unverified file in pack: {extra_name}")
        except Exception as exc:
            errors.append(f"Invalid {CHECKSUMS_NAME}: {exc}")

    # Require & verify manifest's own sha256 map (must cover pack-data.json)
    m_sha_map = manifest.get("sha256")
    if not isinstance(m_sha_map, dict) or DATA_NAME not in m_sha_map:
        errors.append(f"Manifest missing required 'sha256' entry for {DATA_NAME}.")
    else:
        for rel_name, exp_sha in m_sha_map.items():
            ok_r, norm_r = is_safe_archive_path(str(rel_name))
            if not ok_r:
                unsafe_paths.append(str(rel_name))
                errors.append(f"Unsafe manifest sha256 path ({norm_r})")
                continue
            if norm_r == MANIFEST_NAME:
                continue
            if norm_r not in raw_files:
                errors.append(f"Missing file referenced by manifest sha256: {norm_r}")
                continue
            act_sha = _sha256_bytes(raw_files[norm_r])
            if not isinstance(exp_sha, str) or act_sha != exp_sha:
                mismatches.append({"path": norm_r, "expected": exp_sha, "actual": act_sha})
                errors.append(f"Manifest SHA-256 mismatch for {norm_r}")

    # Require & verify contentSha256 consistency AND pack-data.json == manifest content
    exp_content_sha = manifest.get("contentSha256")
    manifest_payload: dict[str, Any] = {
        "propositions": manifest.get("propositions", []),
        "chains": manifest.get("chains", []),
        "topics": manifest.get("topics", []),
        "canonicalTraps": manifest.get("canonicalTraps", []),
    }
    if "derivations" in manifest:
        manifest_payload["derivations"] = manifest["derivations"]
    if "exams" in manifest:
        manifest_payload["exams"] = manifest["exams"]

    if not isinstance(exp_content_sha, str) or not exp_content_sha.strip():
        errors.append("Manifest missing required 'contentSha256'.")
    else:
        actual_csha = _sha256_bytes(_canonical_json_bytes(manifest_payload))
        if actual_csha != exp_content_sha:
            mismatches.append({"path": "contentSha256", "expected": exp_content_sha, "actual": actual_csha})
            errors.append("Manifest contentSha256 mismatch.")

    if DATA_NAME in raw_files:
        try:
            pack_data_obj = json.loads(raw_files[DATA_NAME].decode("utf-8"))
            if not isinstance(pack_data_obj, dict):
                errors.append(f"Invalid {DATA_NAME}: root must be a JSON object.")
            else:
                data_csha = _sha256_bytes(_canonical_json_bytes(pack_data_obj))
                if pack_data_obj != manifest_payload or (
                    isinstance(exp_content_sha, str) and exp_content_sha.strip() and data_csha != exp_content_sha
                ):
                    mismatches.append({"path": DATA_NAME, "expected": exp_content_sha, "actual": data_csha})
                    errors.append(f"{DATA_NAME} content does not match {MANIFEST_NAME}.")
        except Exception as exc:
            errors.append(f"Invalid {DATA_NAME}: {exc}")

    ok = len(errors) == 0
    return {
        "ok": ok,
        "path": str(p),
        "format": "zip",
        "verifiedFiles": len(raw_files) if ok else 0,
        "unsafePaths": unsafe_paths,
        "mismatches": mismatches,
        "errors": errors,
    }, (manifest if ok else None), (raw_files if ok else {})


def _read_and_verify_pack(pack_path: Path | str) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Verify zip-slip/symlink safety and SHA-256 checksums; return `(report, manifest_or_None)`."""
    report, manifest, _raw_files = _read_and_verify_pack_with_files(pack_path)
    return report, manifest


def verify_pack(pack_path: Path | str) -> dict[str, Any]:
    """Verify pack path safety (no zip-slip/symlinks) and SHA-256 checksums."""
    report, _ = _read_and_verify_pack(pack_path)
    return report


def inspect_pack(pack_path: Path | str) -> dict[str, Any]:
    """Verify and summarize a portable study pack without mutating the workspace."""
    report, manifest = _read_and_verify_pack(pack_path)
    if not report["ok"] or manifest is None:
        return {
            "ok": False,
            "path": report["path"],
            "format": report["format"],
            "unsafePaths": report["unsafePaths"],
            "mismatches": report["mismatches"],
            "errors": report["errors"],
        }

    props = manifest.get("propositions") or []
    chains = manifest.get("chains") or []
    topics = manifest.get("topics") or []
    traps = manifest.get("canonicalTraps") or []

    # Audit PII-cleanliness
    pii_leaks: list[str] = []
    def _scan_keys(obj: Any, prefix: str = "") -> None:
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k in _STRIPPED_SET:
                    pii_leaks.append(f"{prefix}{k}")
                _scan_keys(v, f"{prefix}{k}.")
        elif isinstance(obj, list):
            for i, it in enumerate(obj):
                _scan_keys(it, f"{prefix}[{i}].")
    _scan_keys({
        "propositions": props,
        "chains": chains,
        "topics": topics,
        "canonicalTraps": traps,
        "derivations": manifest.get("derivations") or [],
        "exams": manifest.get("exams") or [],
    })

    return {
        "ok": True,
        "path": report["path"],
        "format": report["format"],
        "packSchema": manifest.get("packSchema", 1),
        "id": manifest.get("id"),
        "title": manifest.get("title"),
        "subject": manifest.get("subject"),
        "author": manifest.get("author", ""),
        "license": manifest.get("license", ""),
        "createdAt": manifest.get("createdAt"),
        "contentSha256": manifest.get("contentSha256"),
        "strippedFields": manifest.get("strippedFields", []),
        "excludedPersonalFiles": manifest.get("excludedPersonalFiles", list(EXCLUDED_PERSONAL_FILES)),
        "zeroPiiVerified": len(pii_leaks) == 0,
        "piiLeaks": pii_leaks,
        "counts": {
            "propositions": len(props),
            "chains": len(chains),
            "topics": len(topics),
            "canonicalTraps": len(traps),
            "derivations": len(manifest.get("derivations") or []),
            "exams": len(manifest.get("exams") or []),
        },
        "samplePropositionIds": [p.get("id") for p in props[:8] if isinstance(p, dict)],
    }


def import_pack(
    pack_path: Path | str,
    *,
    workspace: Path | str | None = None,
    kg_dir: Path | str | None = None,
    chapters_dir: Path | str | None = None,
    exams_dir: Path | str | None = None,
    mode: str = "merge",
    probe: bool = False,
    dry_run: bool = False,
    similarity_threshold: float = 0.78,
    no_sync: bool = False,
    allow_code_arenas: bool = False,
) -> dict[str, Any]:
    """Safely verify and import a study pack without overwriting existing mastery or FSRS schedules."""
    mode_norm = str(mode or "merge").strip().lower()
    if mode_norm not in ("merge", "suspend-all"):
        raise ValueError(f"Invalid import mode {mode!r}; expected 'merge' or 'suspend-all'.")

    report, manifest, raw_files = _read_and_verify_pack_with_files(pack_path)
    if not report["ok"] or manifest is None:
        return {
            "ok": False,
            "path": report["path"],
            "mode": mode_norm,
            "unsafePaths": report["unsafePaths"],
            "mismatches": report["mismatches"],
            "errors": report["errors"],
        }

    ws = _resolve_workspace(workspace)
    kdir = _resolve_kg_dir(workspace=ws, explicit=kg_dir)
    cdir = _resolve_chapters_dir(workspace=ws, explicit=chapters_dir)
    edir = _resolve_exams_dir(workspace=ws, explicit=exams_dir)

    props_path = kdir / "propositions.jsonl"
    chains_path = kdir / "chains.jsonl"

    existing_props = _load_jsonl(props_path)
    existing_chains = _load_jsonl(chains_path)

    added_props: list[dict[str, Any]] = []
    skipped_existing: list[dict[str, Any]] = []
    id_remap: dict[str, str] = {}

    incoming_props = manifest.get("propositions") or []
    for raw_p in incoming_props:
        if not isinstance(raw_p, dict):
            continue
        clean_p = _sanitize_proposition(raw_p)
        pid = clean_p.get("id") or _slugify(clean_p.get("title") or "prop")
        clean_p["id"] = pid

        match, score = _find_best_similar_existing(clean_p, existing_props)
        if match is not None and score >= float(similarity_threshold):
            # NEVER overwrite existing mastery, status, or FSRS schedules!
            id_remap[pid] = str(match.get("id") or pid)
            skipped_existing.append(
                {
                    "incomingId": pid,
                    "existingId": match.get("id"),
                    "existingStatus": match.get("status", "planned"),
                    "similarity": score,
                    "reason": "exact_id" if score >= 1.0 and str(match.get("id")) == pid else "similar_concept",
                }
            )
            continue

        # Brand-new proposition enters cleanly with zero fabricated FSRS history
        new_node: dict[str, Any] = {
            "id": pid,
            "subject": clean_p.get("subject") or manifest.get("subject") or "General",
            "chapter": clean_p.get("chapter") or manifest.get("title") or "",
            "title": clean_p.get("title") or pid,
            "kind": clean_p.get("kind") or "derivation",
            "status": "planned",
            "statement": clean_p.get("statement") or clean_p.get("title") or pid,
            "cues": [
                {
                    "id": str(c.get("id") or f"c{i + 1}"),
                    "q": str(c.get("q") or ""),
                    "a": str(c.get("a") or ""),
                    "lastRating": None,
                    "reviews": 0,
                    **({"occlusion": c["occlusion"]} if c.get("occlusion") else {}),
                }
                for i, c in enumerate(clean_p.get("cues") or [])
                if isinstance(c, dict)
            ],
            "importedFromPack": manifest.get("id"),
        }
        if mode_norm == "suspend-all":
            new_node["suspended"] = True
        if clean_p.get("aliases"):
            new_node["aliases"] = clean_p["aliases"]
        if clean_p.get("boundaryConditions"):
            new_node["boundaryConditions"] = clean_p["boundaryConditions"]
        if clean_p.get("occlusion"):
            new_node["occlusion"] = clean_p["occlusion"]

        existing_props.append(new_node)
        added_props.append(new_node)
        id_remap[pid] = pid

    # Merge chains with deduplication by canonical chainKey
    existing_chain_keys: set[str] = set()
    for c in existing_chains:
        nc = _normalize_chain(c)
        if nc:
            existing_chain_keys.add(nc["chainKey"])

    added_chains = 0
    for raw_c in manifest.get("chains") or []:
        nc = _normalize_chain(raw_c)
        if not nc:
            continue
        remapped_prems = [id_remap.get(pr, pr) for pr in nc["premises"]]
        remapped_conc = id_remap.get(nc["conclusion"], nc["conclusion"])
        if remapped_conc in remapped_prems:
            continue
        dedup_key = f"{'+'.join(sorted(set(remapped_prems)))}>{remapped_conc}"
        if dedup_key in existing_chain_keys:
            continue
        existing_chain_keys.add(dedup_key)
        existing_chains.append(
            {
                "premises": sorted(set(remapped_prems)),
                "conclusion": remapped_conc,
                "relation": nc["relation"],
                "why": nc["why"],
                "encompassWeight": nc["encompassWeight"],
                "importedFromPack": manifest.get("id"),
            }
        )
        added_chains += 1

    # Optionally create chapter tracker if pack includes topics and tracker does not exist yet
    tracker_action = "none"
    tracker_path: Path | None = None
    pack_slug = _slugify(str(manifest.get("slug") or manifest.get("id") or "pack"))
    pack_topics = [t for t in (manifest.get("topics") or []) if isinstance(t, dict)]
    if pack_topics:
        tracker_path = cdir / f"{pack_slug}-tracker.json"
        if not tracker_path.exists():
            tracker_action = "created"
            if not dry_run:
                tracker_obj = {
                    "schemaVersion": 1,
                    "slug": pack_slug,
                    "title": manifest.get("title") or pack_slug,
                    "subject": manifest.get("subject") or "General",
                    "active": mode_norm != "suspend-all",
                    "createdAt": _now_iso(),
                    "updatedAt": _now_iso(),
                    "importedFromPack": manifest.get("id"),
                    "sources": [],
                    "topics": [
                        {
                            "id": str(t.get("id") or f"{pack_slug}-t{i + 1}"),
                            "order": int(t.get("order") or (i + 1)),
                            "title": str(t.get("title") or f"Topic {i + 1}"),
                            "level": str(t.get("level") or "basics"),
                            "prereqTopicIds": list(t.get("prereqTopicIds") or []),
                            "propIds": [id_remap.get(pid, pid) for pid in (t.get("propIds") or [])],
                            "status": "pending",
                            "coveredPages": [],
                        }
                        for i, t in enumerate(pack_topics)
                    ],
                }
                _save_json_atomic(tracker_path, tracker_obj)
        else:
            tracker_action = "preserved_existing"

    # Optionally import companion chapters/*.md (safe single-segment basename; NEVER overwrite existing)
    added_chapter_files: list[str] = []
    skipped_chapter_files: list[str] = []
    cdir_resolved = cdir.resolve()
    for member_name in sorted(raw_files.keys()):
        if not member_name.startswith("chapters/"):
            continue
        rel_sub = member_name[len("chapters/"):]
        if "/" in rel_sub or "\\" in rel_sub or not _SAFE_CHAPTER_MD_RE.fullmatch(rel_sub):
            continue
        dest_md = (cdir_resolved / rel_sub).resolve()
        if not dest_md.is_relative_to(cdir_resolved):
            continue
        if dest_md.exists():
            skipped_chapter_files.append(rel_sub)
            continue
        if not dry_run:
            md_text = _redact_local_paths_raw(raw_files[member_name].decode("utf-8", errors="replace"))
            _save_text_atomic(dest_md, md_text)
        added_chapter_files.append(rel_sub)

    # Optionally merge sanitized derivation DAGs (never overwriting existing derivation IDs)
    added_derivations = 0
    incoming_derivs = [d for d in (manifest.get("derivations") or []) if isinstance(d, dict)]
    if incoming_derivs:
        derivs_path = kdir / "derivations.jsonl"
        existing_derivs = _load_jsonl(derivs_path)
        existing_deriv_ids = {str(d.get("id") or "").strip() for d in existing_derivs if d.get("id")}
        for raw_d in incoming_derivs:
            sd = _sanitize_derivation(raw_d)
            if sd is None or sd["id"] in existing_deriv_ids:
                continue
            if sd.get("propId"):
                sd["propId"] = id_remap.get(sd["propId"], sd["propId"])
            sd["importedFromPack"] = manifest.get("id")
            existing_derivs.append(sd)
            existing_deriv_ids.add(sd["id"])
            added_derivations += 1
        if not dry_run and added_derivations > 0:
            _save_jsonl_atomic(derivs_path, existing_derivs)

    # Optionally merge sanitized Blunder FEN forks (`forks.jsonl` + manifest.forks)
    added_forks = 0
    incoming_forks: list[dict[str, Any]] = []
    if "forks.jsonl" in raw_files:
        incoming_forks.extend(_parse_jsonl_bytes(raw_files["forks.jsonl"]))
    if isinstance(manifest.get("forks"), list):
        incoming_forks.extend(f for f in manifest["forks"] if isinstance(f, dict))
    if incoming_forks:
        forks_path = kdir / "forks.jsonl"
        existing_forks = _load_jsonl(forks_path)
        existing_fork_ids = {str(f.get("id") or "").strip() for f in existing_forks if f.get("id")}
        for raw_f in incoming_forks:
            sf = _sanitize_fork_for_pack(
                raw_f,
                pack_id=manifest.get("id"),
                id_remap=id_remap,
                suspend=(mode_norm == "suspend-all"),
            )
            if sf is None or sf["id"] in existing_fork_ids:
                continue
            existing_forks.append(sf)
            existing_fork_ids.add(sf["id"])
            added_forks += 1
        if not dry_run and added_forks > 0:
            _save_jsonl_atomic(forks_path, existing_forks)

    # Optionally merge Counterexample Arenas (`arenas.jsonl` + manifest.arenas)
    # Security: import math/non-code arenas always; import code-domain arenas ONLY when allow_code_arenas=True!
    added_arenas = 0
    skipped_code_arena_ids: list[str] = []
    incoming_arenas: list[dict[str, Any]] = []
    if "arenas.jsonl" in raw_files:
        incoming_arenas.extend(_parse_jsonl_bytes(raw_files["arenas.jsonl"]))
    if isinstance(manifest.get("arenas"), list):
        incoming_arenas.extend(a for a in manifest["arenas"] if isinstance(a, dict))
    if incoming_arenas:
        arenas_path = kdir / "arenas.jsonl"
        existing_arenas = _load_jsonl(arenas_path)
        existing_arena_ids = {str(a.get("id") or "").strip() for a in existing_arenas if a.get("id")}
        seen_skipped_code: set[str] = set()
        for raw_a in incoming_arenas:
            sa = _sanitize_arena_for_pack(
                raw_a,
                pack_id=manifest.get("id"),
                id_remap=id_remap,
            )
            if sa is None or sa["id"] in existing_arena_ids:
                continue
            if _is_code_arena(sa) and not allow_code_arenas:
                if sa["id"] not in seen_skipped_code:
                    seen_skipped_code.add(sa["id"])
                    skipped_code_arena_ids.append(sa["id"])
                continue
            existing_arenas.append(sa)
            existing_arena_ids.add(sa["id"])
            added_arenas += 1
        if not dry_run and added_arenas > 0:
            _save_jsonl_atomic(arenas_path, existing_arenas)

    # Optionally import companion exams (`exams/*.json|html` + manifest.exams), excluding `*-attempt-*`
    added_exam_files: list[str] = []
    skipped_exam_files: list[str] = []
    edir_resolved = edir.resolve()
    planned_exam_names: set[str] = set()
    for member_name in sorted(raw_files.keys()):
        if not member_name.startswith("exams/"):
            continue
        rel_sub = member_name[len("exams/"):]
        if "/" in rel_sub or "\\" in rel_sub or not _SAFE_EXAM_FILE_RE.fullmatch(rel_sub):
            continue
        if _is_attempt_filename(rel_sub):
            continue
        dest_ex = (edir_resolved / rel_sub).resolve()
        if not dest_ex.is_relative_to(edir_resolved):
            continue
        if dest_ex.exists() or rel_sub in planned_exam_names:
            skipped_exam_files.append(rel_sub)
            continue
        if rel_sub.lower().endswith(".json"):
            try:
                ex_obj = json.loads(raw_files[member_name].decode("utf-8"))
            except Exception:
                continue
            if not isinstance(ex_obj, dict):
                continue
            clean_ex = _deep_strip_pii(ex_obj)
            if not dry_run:
                _save_json_atomic(dest_ex, clean_ex)
        else:
            if not dry_run:
                ex_html = _redact_local_paths_raw(raw_files[member_name].decode("utf-8", errors="replace"))
                _save_text_atomic(dest_ex, ex_html)
        planned_exam_names.add(rel_sub)
        added_exam_files.append(rel_sub)

    for idx_ex, raw_ex in enumerate(manifest.get("exams") or []):
        if not isinstance(raw_ex, dict) or not isinstance(raw_ex.get("questions"), list):
            continue
        raw_slug = str(
            raw_ex.get("examSlug")
            or raw_ex.get("slug")
            or raw_ex.get("id")
            or raw_ex.get("title")
            or f"{pack_slug}-exam-{idx_ex + 1}"
        )
        ex_slug = _slugify(raw_slug, default=f"{pack_slug}-exam-{idx_ex + 1}")
        ex_fname = f"{ex_slug}.json"
        if _is_attempt_filename(ex_fname) or not _SAFE_EXAM_FILE_RE.fullmatch(ex_fname):
            continue
        dest_ex = (edir_resolved / ex_fname).resolve()
        if not dest_ex.is_relative_to(edir_resolved):
            continue
        if dest_ex.exists() or ex_fname in planned_exam_names:
            skipped_exam_files.append(ex_fname)
            continue
        clean_ex = _deep_strip_pii(raw_ex)
        if isinstance(clean_ex, dict):
            clean_ex.setdefault("examSlug", ex_slug)
            clean_ex["importedFromPack"] = manifest.get("id")
            if not dry_run:
                _save_json_atomic(dest_ex, clean_ex)
            planned_exam_names.add(ex_fname)
            added_exam_files.append(ex_fname)

    if not dry_run:
        _save_jsonl_atomic(props_path, existing_props)
        _save_jsonl_atomic(chains_path, existing_chains)
        if not no_sync:
            _sync_hub(workspace=ws, kg_dir=kdir, chapters_dir=cdir, quiet=True)

    # Optional 3-question placement skip probe
    probe_items: list[dict[str, Any]] = []
    if probe:
        for p in added_props[:3]:
            cues = p.get("cues") or []
            prompt = cues[0]["q"] if cues and cues[0].get("q") else f"State and justify: {p.get('title')}"
            probe_items.append(
                {
                    "propId": p["id"],
                    "title": p.get("title"),
                    "prompt": prompt,
                }
            )

    return {
        "ok": True,
        "path": report["path"],
        "packId": manifest.get("id"),
        "title": manifest.get("title"),
        "mode": mode_norm,
        "dryRun": bool(dry_run),
        "addedPropositions": len(added_props),
        "addedPropositionIds": [p["id"] for p in added_props],
        "skippedExisting": len(skipped_existing),
        "skippedDetails": skipped_existing,
        "addedChains": added_chains,
        "addedDerivations": added_derivations,
        "addedChapters": len(added_chapter_files),
        "addedChapterFiles": added_chapter_files,
        "skippedChapters": len(skipped_chapter_files),
        "addedForks": added_forks,
        "addedArenas": added_arenas,
        "skippedCodeArenas": len(skipped_code_arena_ids),
        "skippedCodeArenaIds": skipped_code_arena_ids,
        "addedExams": len(added_exam_files),
        "addedExamFiles": added_exam_files,
        "canonicalTrapsAvailable": len(manifest.get("canonicalTraps") or []),
        "trackerAction": tracker_action,
        "trackerPath": str(tracker_path) if tracker_path else None,
        "probeItems": probe_items,
    }


# ---------------------------------------------------------------------------
# CLI (`python3 -m syntopik.packs export|inspect|import|verify`)
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--workspace", type=Path, default=argparse.SUPPRESS, help="Workspace root directory.")
    common.add_argument("--kg-dir", type=Path, default=argparse.SUPPRESS, help="Override knowledge-graph/ directory.")
    common.add_argument("--chapters-dir", type=Path, default=argparse.SUPPRESS, help="Override chapters/ directory.")
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="Emit JSON output.")

    parser = argparse.ArgumentParser(
        prog="syntopik.packs",
        description="Export, inspect, verify, and safely import zero-PII Syntopik Study Packs.",
        parents=[common],
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_exp = sub.add_parser("export", parents=[common], help="Export a chapter/subject into a zero-PII study pack.")
    p_exp.add_argument("target", help="Chapter slug, subject name, or 'all'.")
    p_exp.add_argument("--out", type=Path, default=None, help="Output .syntopik-pack.zip or .json path.")
    p_exp.add_argument("--author", default="", help="Pack author attribution.")
    p_exp.add_argument("--license", default="CC-BY-4.0", help="Pack license identifier (default: CC-BY-4.0).")
    p_exp.add_argument("--title", default=None, help="Override pack title.")
    p_exp.add_argument("--include-exams", action="store_true", help="Include sanitized exam specs when matching.")

    p_ins = sub.add_parser("inspect", parents=[common], help="Verify and inspect a study pack without importing.")
    p_ins.add_argument("pack", type=Path, help="Path to .syntopik-pack.zip or .json file.")

    p_ver = sub.add_parser("verify", parents=[common], help="Verify zip-slip/symlink safety and SHA-256 checksums.")
    p_ver.add_argument("pack", type=Path, help="Path to .syntopik-pack.zip or .json file.")

    p_imp = sub.add_parser("import", parents=[common], help="Verify and import a study pack safely.")
    p_imp.add_argument("pack", type=Path, help="Path to .syntopik-pack.zip or .json file.")
    p_imp.add_argument("--mode", choices=("merge", "suspend-all"), default="merge", help="Import mode.")
    p_imp.add_argument("--probe", action="store_true", help="Emit a 3-question placement skip probe.")
    p_imp.add_argument("--dry-run", action="store_true", help="Preview deduplication without writing files.")
    p_imp.add_argument("--no-sync", action="store_true", help="Skip hub sync after import.")
    p_imp.add_argument(
        "--allow-code-arenas",
        action="store_true",
        help="Opt in to importing executable code-domain Counterexample Arenas from trusted packs.",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    ws = getattr(args, "workspace", None)
    kg_dir = getattr(args, "kg_dir", None)
    ch_dir = getattr(args, "chapters_dir", None)
    as_json = bool(getattr(args, "json", False))

    try:
        if args.command == "export":
            res = export_pack(
                args.target,
                workspace=ws,
                kg_dir=kg_dir,
                chapters_dir=ch_dir,
                out_path=args.out,
                author=args.author,
                license_id=args.license,
                title=args.title,
                include_exams=args.include_exams,
            )
        elif args.command == "inspect":
            res = inspect_pack(args.pack)
        elif args.command == "verify":
            res = verify_pack(args.pack)
        elif args.command == "import":
            res = import_pack(
                args.pack,
                workspace=ws,
                kg_dir=kg_dir,
                chapters_dir=ch_dir,
                mode=args.mode,
                probe=args.probe,
                dry_run=args.dry_run,
                no_sync=args.no_sync,
                allow_code_arenas=bool(args.allow_code_arenas),
            )
        else:
            parser.error(f"Unknown command: {args.command}")
            return 2
    except Exception as exc:  # noqa: BLE001
        res = {"ok": False, "command": args.command, "errors": [str(exc)]}

    if as_json:
        print(json.dumps(res, indent=2, ensure_ascii=False))
    else:
        if res.get("ok"):
            if args.command == "export":
                print(
                    f"Exported zero-PII study pack: {res['outPath']}\n"
                    f"  {res['propositionCount']} propositions · {res['chainCount']} chains · "
                    f"{res['topicCount']} topics · {res['canonicalTrapCount']} canonical traps ({res['bytes']:,} B)"
                )
            elif args.command == "inspect":
                c = res["counts"]
                print(
                    f"Pack OK: {res['title']} ({res['id']})\n"
                    f"  {c['propositions']} props · {c['chains']} chains · {c['topics']} topics · "
                    f"{c['canonicalTraps']} traps · zeroPII={res['zeroPiiVerified']}"
                )
            elif args.command == "verify":
                print(f"Pack verified OK: {res['path']} (0 unsafe paths, 0 checksum mismatches)")
            elif args.command == "import":
                print(
                    f"Imported pack '{res['packId']}' ({res['mode']}): "
                    f"+{res['addedPropositions']} props, {res['skippedExisting']} preserved existing, +{res['addedChains']} chains"
                )
        else:
            for err in res.get("errors", ["Operation failed"]):
                print(f"ERROR: {err}", file=sys.stderr)
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
