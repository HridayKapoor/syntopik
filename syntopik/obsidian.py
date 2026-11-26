"""Syntopik v2.0 — Bidirectional Obsidian Vault & Matuschak Mnemonic Medium Sync.

Pure Python 3.12+ standard library module.

Capabilities:
  1. `export --vault <dir>`:
     - Exports `knowledge-graph/propositions.jsonl` into clean Obsidian Markdown notes with
       minimal YAML frontmatter (`syntopik_id`, `status`, `mastery` — ZERO noisy FSRS
       stability/difficulty/due fields in prose or frontmatter!).
     - Preserves user-authored YAML keys and 100% of personal learner prose outside
       `<!-- syntopik:begin -->` ... `<!-- syntopik:end -->` fenced markers.
     - Emits valid `[[wikilinks]]` derived from `chains.jsonl` (supporting both legacy
       `{from, to}` and multi-premise `{premises[], conclusion}` chains).
     - Generates a `Trap-MOC.md` Map of Content from `mistakes.jsonl` (also marker-fenced).
  2. `import --vault <dir>` & `sync-notes [--chapters-dir <dir>]`:
     - Parses Matuschak Mnemonic Medium fenced `:::cue {propId, id, kind: recall|application|procedural|salience}`
       blocks AND inline `{{c1::...}}` / `{{c1::...::hint}}` clozes (plus `Q:: / A::` pairs)
       from Obsidian vault files or `chapters/*.md`.
     - Records `{file, heading, line}` context anchors on every synced cue/proposition.
     - Merges & deduplicates safely back into `propositions.jsonl` WITHOUT clobbering
       existing FSRS scheduling state, BKT mastery, or cue review history!
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Any
import unicodedata

ROOT = Path(__file__).resolve().parent.parent
SYNT_BEGIN = "<!-- syntopik:begin -->"
SYNT_END = "<!-- syntopik:end -->"

VALID_CUE_KINDS: tuple[str, ...] = ("recall", "application", "procedural", "salience")

# ---------------------------------------------------------------------------
# Optional shared imports from `syntopik.{paths,storage,schemas}` + stdlib fallbacks
# ---------------------------------------------------------------------------

try:
    from syntopik.paths import (  # type: ignore[import-not-found]
        resolve_chapters_dir as _shared_resolve_chapters_dir,
        resolve_kg_dir as _shared_resolve_kg_dir,
        resolve_workspace as _shared_resolve_workspace,
        sync_hub as _shared_sync_hub,
    )
except Exception:  # noqa: BLE001
    _shared_resolve_chapters_dir = None
    _shared_resolve_kg_dir = None
    _shared_resolve_workspace = None
    _shared_sync_hub = None

try:
    from syntopik.storage import (  # type: ignore[import-not-found]
        load_jsonl as _shared_load_jsonl,
        save_jsonl_atomic as _shared_save_jsonl_atomic,
    )
except Exception:  # noqa: BLE001
    _shared_load_jsonl = None
    _shared_save_jsonl_atomic = None

try:
    from syntopik.schemas import (  # type: ignore[import-not-found]
        normalize_chain as _shared_normalize_chain,
        now_iso as _shared_now_iso,
        slugify as _shared_slugify,
    )
except Exception:  # noqa: BLE001
    _shared_normalize_chain = None
    _shared_now_iso = None
    _shared_slugify = None


def now_iso() -> str:
    if _shared_now_iso is not None:
        return str(_shared_now_iso())
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def slugify(text: str, max_len: int = 80) -> str:
    if _shared_slugify is not None:
        val = str(_shared_slugify(text))
    else:
        val = re.sub(r"[^a-z0-9]+", "-", str(text or "").strip().lower()).strip("-")
    val = re.sub(r"[^a-z0-9-]+", "-", val).strip("-")[:max_len].strip("-")
    return val or "concept"


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
        return (resolve_workspace(workspace) / "knowledge-graph").resolve()
    if _shared_resolve_kg_dir is not None:
        return Path(_shared_resolve_kg_dir(None)).resolve()
    env_kg = os.environ.get("SYNT_KG_DIR") or os.environ.get("MLH_KG_DIR")
    if env_kg and env_kg.strip():
        return Path(env_kg).expanduser().resolve()
    return (resolve_workspace(None) / "knowledge-graph").resolve()


def resolve_chapters_dir(workspace: str | Path | None = None, explicit_chapters_dir: str | Path | None = None) -> Path:
    if explicit_chapters_dir is not None and str(explicit_chapters_dir).strip():
        return Path(explicit_chapters_dir).expanduser().resolve()
    if workspace is not None and str(workspace).strip():
        return (resolve_workspace(workspace) / "chapters").resolve()
    if _shared_resolve_chapters_dir is not None:
        return Path(_shared_resolve_chapters_dir(None)).resolve()
    env_ch = os.environ.get("SYNT_CHAPTERS_DIR") or os.environ.get("MLH_CHAPTERS_DIR")
    if env_ch and env_ch.strip():
        return Path(env_ch).expanduser().resolve()
    return (resolve_workspace(None) / "chapters").resolve()


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
    fd, tmp_name = tempfile.mkstemp(prefix=".tmp-", suffix=".md", dir=str(path.parent))
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


def normalize_chain(raw: dict[str, Any]) -> dict[str, Any] | None:
    if _shared_normalize_chain is not None:
        return _shared_normalize_chain(raw)
    if not isinstance(raw, dict):
        return None
    conclusion = str(raw.get("conclusion") or raw.get("to") or "").strip()
    if not conclusion:
        return None
    raw_premises = raw.get("premises")
    premises: list[str] = []
    if isinstance(raw_premises, list):
        for p in raw_premises:
            ps = str(p or "").strip()
            if ps and ps != conclusion and ps not in premises:
                premises.append(ps)
    else:
        src = str(raw.get("from") or "").strip()
        if src and src != conclusion:
            premises = [src]
    if not premises:
        return None
    why = str(raw.get("why") or raw.get("note") or "").strip()
    relation = str(raw.get("relation") or "requires").strip() or "requires"
    return {
        "premises": premises,
        "conclusion": conclusion,
        "relation": relation,
        "why": why,
        "chainKey": f"{'+'.join(sorted(premises))}>{conclusion}",
        "isJoint": len(premises) >= 2,
    }


def _sync_hub_quiet(workspace: Path, kg_dir: Path, chapters_dir: Path) -> None:
    if _shared_sync_hub is not None:
        try:
            _shared_sync_hub(workspace=workspace, kg_dir=kg_dir, chapters_dir=chapters_dir, quiet=True)
            return
        except Exception:  # noqa: BLE001
            pass
    sync_script = ROOT / "scripts" / "sync-graph.mjs"
    node_bin = shutil.which("node")
    if not sync_script.is_file() or not node_bin:
        return
    try:
        subprocess.run(
            [
                node_bin,
                str(sync_script),
                "--workspace",
                str(workspace),
                "--kg-dir",
                str(kg_dir),
                "--chapters-dir",
                str(chapters_dir),
            ],
            capture_output=True,
            timeout=15,
            check=False,
        )
    except Exception:  # noqa: BLE001
        pass


def _ensure_within_dir(base_dir: Path, candidate: Path) -> Path:
    base_res = base_dir.resolve()
    cand_res = candidate.resolve()
    if cand_res == base_res or cand_res.is_relative_to(base_res):
        return cand_res
    raise ValueError(f"Path jail violation: '{candidate}' escapes '{base_res}'.")


def _safe_note_stem(prop_id: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", str(prop_id or "").strip()).strip("-.")
    if not stem or stem in (".", ".."):
        stem = "concept-" + hashlib.sha1(str(prop_id).encode("utf-8")).hexdigest()[:8]
    return stem


# ---------------------------------------------------------------------------
# Minimal YAML Frontmatter Parser & Serializer (Zero External Deps)
# ---------------------------------------------------------------------------

# Fields that MUST NEVER pollute Obsidian YAML frontmatter or prose.
NOISY_FSRS_KEYS: frozenset[str] = frozenset(
    {"fsrs", "stability", "difficulty", "due", "last_review", "card_id", "nextReview", "lastTested", "state", "step"}
)


def _yaml_parse_scalar(val: str) -> Any:
    s = val.strip()
    if not s:
        return ""
    if (s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'")):
        inner = s[1:-1]
        if s.startswith('"'):
            inner = inner.replace('\\"', '"').replace("\\\\", "\\")
        return inner
    low = s.lower()
    if low == "true":
        return True
    if low == "false":
        return False
    if low in ("null", "none", "~"):
        return None
    if re.fullmatch(r"-?\d+", s):
        return int(s)
    if re.fullmatch(r"-?\d+\.\d+", s):
        return float(s)
    if s.startswith("[") and s.endswith("]"):
        inner = s[1:-1].strip()
        if not inner:
            return []
        return [_yaml_parse_scalar(part) for part in inner.split(",")]
    return s


def _yaml_format_scalar(val: Any) -> str:
    if val is None:
        return "null"
    if isinstance(val, bool):
        return "true" if val else "false"
    if isinstance(val, (int, float)):
        return str(val)
    s = str(val)
    needs_quote = (
        s == ""
        or s != s.strip()
        or any(ch in s for ch in (":", "#", "[", "]", "{", "}", ",", "\n", '"', "'", "?", "&", "*", "!"))
        or s.lower() in ("true", "false", "null", "none", "yes", "no")
    )
    if needs_quote:
        escaped = s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")
        return f'"{escaped}"'
    return s


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Parse leading YAML frontmatter (`--- ... ---`) into `(metadata_dict, body_text)`."""
    if not text.startswith("---"):
        return {}, text
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return {}, text
    end_idx = None
    for idx in range(1, len(lines)):
        if lines[idx].strip() in ("---", "..."):
            end_idx = idx
            break
    if end_idx is None:
        return {}, text

    meta: dict[str, Any] = {}
    current_list_key: str | None = None
    for raw_ln in lines[1:end_idx]:
        ln = raw_ln.rstrip("\r\n")
        if not ln.strip() or ln.lstrip().startswith("#"):
            continue
        m_item = re.match(r"^\s+-\s+(.*)$", ln)
        if m_item and current_list_key is not None:
            lst = meta.setdefault(current_list_key, [])
            if isinstance(lst, list):
                lst.append(_yaml_parse_scalar(m_item.group(1)))
            continue
        m_kv = re.match(r"^([A-Za-z0-9_-]+)\s*:\s*(.*)$", ln)
        if m_kv:
            k = m_kv.group(1).strip()
            v = m_kv.group(2).strip()
            if v == "":
                meta[k] = []
                current_list_key = k
            else:
                meta[k] = _yaml_parse_scalar(v)
                current_list_key = None
        else:
            current_list_key = None

    body = "".join(lines[end_idx + 1 :])
    # Preserve leading newline structure cleanly
    if body.startswith("\r\n"):
        body = body[2:]
    elif body.startswith("\n"):
        body = body[1:]
    return meta, body


def render_frontmatter(meta: dict[str, Any]) -> str:
    """Serialize a clean minimal YAML frontmatter block."""
    lines = ["---"]
    for k, v in meta.items():
        if k in NOISY_FSRS_KEYS:
            continue
        if isinstance(v, list):
            if not v:
                continue
            lines.append(f"{k}:")
            for item in v:
                lines.append(f"  - {_yaml_format_scalar(item)}")
        else:
            lines.append(f"{k}: {_yaml_format_scalar(v)}")
    lines.append("---")
    return "\n".join(lines) + "\n"


_MANAGED_MARKER_RE = re.compile(r"<!--(\s*syntopik\s*:\s*(?:begin|end)\s*)-->", re.IGNORECASE)
_ESCAPED_MARKER_RE = re.compile(r"&lt;!--(\s*syntopik\s*:\s*(?:begin|end)\s*)--&gt;", re.IGNORECASE)
_LEADING_FENCE_RE = re.compile(r"^([ \t]*)(:::)", re.MULTILINE)
_ESCAPED_FENCE_RE = re.compile(r"^([ \t]*)\\(:::)", re.MULTILINE)


def _escape_managed_markers(
    text: str,
    *,
    begin_marker: str = SYNT_BEGIN,
    end_marker: str = SYNT_END,
) -> str:
    """Neutralize literal syntopik fence markers inside managed content."""
    out = str(text or "")
    for marker in (begin_marker, end_marker):
        if marker and marker in out:
            out = out.replace(marker, marker.replace("<", "&lt;").replace(">", "&gt;"))
    out = _MANAGED_MARKER_RE.sub(r"&lt;!--\1--&gt;", out)
    return out


def _escape_managed_prose(text: str) -> str:
    """Escape both syntopik HTML markers and lines starting with `:::` in managed text."""
    out = _escape_managed_markers(text)
    out = _LEADING_FENCE_RE.sub(r"\1\\\2", out)
    return out


def _unescape_cue_text(text: str) -> str:
    """Reverse managed-block marker and `:::` escapes when importing `:::cue` bodies."""
    out = _ESCAPED_FENCE_RE.sub(r"\1\2", str(text or ""))
    out = _ESCAPED_MARKER_RE.sub(r"<!--\1-->", out)
    return out


def _escape_cue_attr(value: str) -> str:
    return str(value or "").replace("\\", "\\\\").replace('"', '\\"').replace("\r", " ").replace("\n", " ")


def splice_fenced_block(
    existing_body: str,
    managed_inner: str,
    *,
    begin_marker: str = SYNT_BEGIN,
    end_marker: str = SYNT_END,
) -> str:
    """Update ONLY the region between `begin_marker` and `end_marker`.

    Any learner prose before `begin_marker` or after `end_marker` is preserved byte-for-byte.
    If markers are absent in `existing_body`, appends the fenced block cleanly at the end.
    """
    safe_inner = _escape_managed_markers(
        managed_inner,
        begin_marker=begin_marker,
        end_marker=end_marker,
    ).rstrip()
    block = f"{begin_marker}\n{safe_inner}\n{end_marker}"
    start_idx = existing_body.find(begin_marker)
    end_idx = existing_body.find(end_marker, start_idx + len(begin_marker)) if start_idx != -1 else -1

    if start_idx != -1 and end_idx != -1:
        before = existing_body[:start_idx]
        after = existing_body[end_idx + len(end_marker) :]
        return before + block + after

    if not existing_body.strip():
        return block + "\n"

    sep = "\n\n"
    if existing_body.endswith("\n\n"):
        sep = ""
    elif existing_body.endswith("\n"):
        sep = "\n"
    return existing_body + sep + block + "\n"


# ---------------------------------------------------------------------------
# 1. Export: Knowledge Graph -> Obsidian Vault
# ---------------------------------------------------------------------------


def _scan_existing_vault_ids(vault_dir: Path) -> dict[str, Path]:
    """Find any existing Obsidian `.md` notes already tagged with `syntopik_id` (skipping symlinks)."""
    mapping: dict[str, Path] = {}
    if not vault_dir.is_dir():
        return mapping
    vdir_res = vault_dir.resolve()
    for dp, dns, fns in os.walk(vdir_res, topdown=True, followlinks=False):
        dns[:] = [
            d
            for d in sorted(dns)
            if not d.startswith(".") and not (Path(dp) / d).is_symlink()
        ]
        for fn in sorted(fns):
            if not fn.lower().endswith(".md"):
                continue
            fp = Path(dp) / fn
            if fp.is_symlink():
                continue
            try:
                fp_res = _ensure_within_dir(vdir_res, fp)
                text = fp_res.read_text(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                continue
            meta, _ = parse_frontmatter(text)
            sid = meta.get("syntopik_id")
            if isinstance(sid, str) and sid.strip():
                mapping[sid.strip()] = fp_res
    return mapping


def _format_wikilink(target_id: str, id_to_stem: dict[str, str], id_to_title: dict[str, str]) -> str:
    stem = id_to_stem.get(target_id) or _safe_note_stem(target_id)
    title = id_to_title.get(target_id)
    if title and title != stem:
        safe_title = _escape_managed_markers(title).replace("|", "/").replace("[[", "(").replace("]]", ")")
        return f"[[{stem}|{safe_title}]]"
    return f"[[{stem}]]"


def _render_proposition_managed_block(
    prop: dict[str, Any],
    incoming_chains: list[dict[str, Any]],
    outgoing_chains: list[dict[str, Any]],
    prop_mistakes: list[dict[str, Any]],
    id_to_stem: dict[str, str],
    id_to_title: dict[str, str],
) -> str:
    pid = str(prop.get("id") or "")
    title = _escape_managed_prose(str(prop.get("title") or pid))
    subject = _escape_managed_prose(str(prop.get("subject") or "General"))
    chapter = _escape_managed_prose(str(prop.get("chapter") or "General"))
    notes = _escape_managed_prose(str(prop.get("notes") or "").strip())

    lines: list[str] = [
        f"# {title}",
        "",
        f"*{subject} · {chapter}*",
        "",
    ]
    if notes:
        lines.extend([notes, ""])

    if incoming_chains:
        lines.extend(["## Prerequisites", ""])
        seen_in: set[str] = set()
        for ch in incoming_chains:
            if ch["chainKey"] in seen_in:
                continue
            seen_in.add(ch["chainKey"])
            prem_links = " ∧ ".join(_format_wikilink(p, id_to_stem, id_to_title) for p in ch["premises"])
            rel = _escape_managed_prose(str(ch.get("relation") or "requires"))
            why_txt = _escape_managed_prose(str(ch.get("why") or ""))
            why = f" — {why_txt}" if why_txt else ""
            lines.append(f"- {prem_links} (`{rel}`){why}")
        lines.append("")

    if outgoing_chains:
        lines.extend(["## Unlocks", ""])
        seen_out: set[str] = set()
        for ch in outgoing_chains:
            if ch["chainKey"] in seen_out:
                continue
            seen_out.add(ch["chainKey"])
            conc_link = _format_wikilink(ch["conclusion"], id_to_stem, id_to_title)
            rel = _escape_managed_prose(str(ch.get("relation") or "requires"))
            why_txt = _escape_managed_prose(str(ch.get("why") or ""))
            why = f" — {why_txt}" if why_txt else ""
            lines.append(f"- {conc_link} (`{rel}`){why}")
        lines.append("")

    cues = [c for c in (prop.get("cues") or []) if isinstance(c, dict) and (c.get("q") or c.get("a"))]
    if cues:
        lines.extend(["## Active Cues", ""])
        for idx, cue in enumerate(cues, start=1):
            cid = str(cue.get("id") or f"c{idx}")
            kind = str(cue.get("kind") or "recall")
            if kind not in VALID_CUE_KINDS:
                kind = "recall"
            q_raw = str(cue.get("q") or "").strip()
            a_raw = str(cue.get("a") or "").strip()
            lines.extend(
                [
                    f':::cue {{propId: "{_escape_cue_attr(pid)}", id: "{_escape_cue_attr(cid)}", kind: "{_escape_cue_attr(kind)}"}}',
                    _escape_managed_prose(f"Q: {q_raw}"),
                    _escape_managed_prose(f"A: {a_raw}"),
                    ":::",
                    "",
                ]
            )

    if prop_mistakes:
        lines.extend(["## Known Traps", ""])
        for m in prop_mistakes:
            cat = _escape_managed_prose(str(m.get("category") or "misconception"))
            wrong = _escape_managed_prose(str(m.get("wrongMove") or "").strip())
            correct = _escape_managed_prose(str(m.get("correctMove") or "").strip())
            lines.append(f"- **`{cat}`**: Avoid *{wrong}* → Instead: {correct}")
        lines.append("")

    return "\n".join(lines).rstrip()


def _render_trap_moc_managed_block(
    mistakes: list[dict[str, Any]],
    id_to_stem: dict[str, str],
    id_to_title: dict[str, str],
) -> str:
    lines: list[str] = [
        "# Syntopik Trap Map of Content (Trap MOC)",
        "",
        f"Recurring pitfalls aggregated from `mistakes.jsonl` ({len(mistakes)} logged).",
        "",
    ]
    if not mistakes:
        lines.append("_No mistakes logged yet._")
        return "\n".join(lines)

    by_cat: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for m in mistakes:
        cat = str(m.get("category") or "misconception")
        by_cat[cat].append(m)

    for cat in sorted(by_cat.keys()):
        items = by_cat[cat]
        lines.extend([f"## {_escape_managed_prose(cat)} ({len(items)})", ""])
        for m in items:
            pid = str(m.get("propId") or "").strip()
            link = _format_wikilink(pid, id_to_stem, id_to_title) if pid else "_unlinked_"
            chapter = _escape_managed_prose(str(m.get("chapter") or "").strip())
            trigger = _escape_managed_prose(str(m.get("triggerCue") or "").strip())
            wrong = _escape_managed_prose(str(m.get("wrongMove") or "").strip())
            correct = _escape_managed_prose(str(m.get("correctMove") or "").strip())
            chap_tag = f" ({chapter})" if chapter else ""
            lines.append(f"- {link}{chap_tag}")
            if trigger:
                lines.append(f"  - **Trigger**: {trigger}")
            if wrong:
                lines.append(f"  - **Wrong Move**: {wrong}")
            if correct:
                lines.append(f"  - **Correction**: {correct}")
        lines.append("")

    return "\n".join(lines).rstrip()


def export_vault(
    vault_dir: str | Path,
    *,
    workspace: str | Path | None = None,
    kg_dir: str | Path | None = None,
    include_trap_moc: bool = True,
) -> dict[str, Any]:
    """Export Syntopik propositions, chain wikilinks, and Trap MOC into an Obsidian vault.

    - Minimal clean YAML frontmatter (`syntopik_id`, `status`, `mastery` — zero noisy FSRS fields!).
    - Updates strictly inside `<!-- syntopik:begin -->` ... `<!-- syntopik:end -->` markers so
      personal learner notes outside markers are 100% preserved.
    """
    vdir = Path(vault_dir).expanduser().resolve()
    vdir.mkdir(parents=True, exist_ok=True)
    kg = resolve_kg_dir(workspace=workspace, explicit_kg_dir=kg_dir)

    props = load_jsonl(kg / "propositions.jsonl")
    raw_chains = load_jsonl(kg / "chains.jsonl")
    mistakes = load_jsonl(kg / "mistakes.jsonl")

    chains = [c for c in (normalize_chain(r) for r in raw_chains) if c is not None]
    incoming: dict[str, list[dict[str, Any]]] = defaultdict(list)
    outgoing: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for ch in chains:
        incoming[ch["conclusion"]].append(ch)
        for prem in ch["premises"]:
            outgoing[prem].append(ch)

    mistakes_by_prop: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for m in mistakes:
        pid = str(m.get("propId") or "").strip()
        if pid:
            mistakes_by_prop[pid].append(m)

    existing_by_id = _scan_existing_vault_ids(vdir)
    id_to_stem: dict[str, str] = {}
    id_to_title: dict[str, str] = {}
    for p in props:
        pid = str(p.get("id") or "").strip()
        if not pid:
            continue
        id_to_title[pid] = str(p.get("title") or pid)
        if pid in existing_by_id:
            id_to_stem[pid] = existing_by_id[pid].stem
        else:
            id_to_stem[pid] = _safe_note_stem(pid)

    exported_files: list[str] = []
    created_count = 0
    updated_count = 0

    for p in props:
        pid = str(p.get("id") or "").strip()
        if not pid:
            continue
        target_path = existing_by_id.get(pid) or _ensure_within_dir(vdir, vdir / f"{id_to_stem[pid]}.md")
        _ensure_within_dir(vdir, target_path)

        raw_mastery = p.get("bktMastery", p.get("confidence", 0.0))
        try:
            mastery_val = round(float(raw_mastery), 2)
        except (TypeError, ValueError):
            mastery_val = 0.0

        status_val = str(p.get("status") or "planned")
        subject_val = str(p.get("subject") or "General")
        chapter_val = str(p.get("chapter") or "General")
        title_val = str(p.get("title") or pid)

        existed = target_path.is_file()
        existing_meta: dict[str, Any] = {}
        existing_body = ""
        if existed:
            existing_meta, existing_body = parse_frontmatter(target_path.read_text(encoding="utf-8", errors="replace"))

        # Preserve user YAML keys, update minimal Syntopik keys only (NO noisy FSRS fields!)
        new_meta: dict[str, Any] = {}
        new_meta["syntopik_id"] = pid
        new_meta["status"] = status_val
        new_meta["mastery"] = mastery_val
        new_meta["title"] = existing_meta.get("title") or title_val
        new_meta["subject"] = existing_meta.get("subject") or subject_val
        new_meta["chapter"] = existing_meta.get("chapter") or chapter_val
        tags = existing_meta.get("tags")
        if isinstance(tags, list) and tags:
            clean_tags = list(tags)
        else:
            clean_tags = ["syntopik", slugify(subject_val, 30)]
        if "syntopik" not in clean_tags:
            clean_tags.insert(0, "syntopik")
        new_meta["tags"] = clean_tags

        for k, v in existing_meta.items():
            if k not in new_meta and k not in NOISY_FSRS_KEYS:
                new_meta[k] = v

        managed_inner = _render_proposition_managed_block(
            p,
            incoming.get(pid, []),
            outgoing.get(pid, []),
            mistakes_by_prop.get(pid, []),
            id_to_stem,
            id_to_title,
        )
        final_body = splice_fenced_block(existing_body, managed_inner)
        final_text = render_frontmatter(new_meta) + "\n" + final_body.lstrip("\n")
        write_text_atomic(target_path, final_text)

        exported_files.append(str(target_path))
        if existed:
            updated_count += 1
        else:
            created_count += 1

    trap_moc_path: str | None = None
    if include_trap_moc:
        moc_file = _ensure_within_dir(vdir, vdir / "Trap-MOC.md")
        moc_meta: dict[str, Any] = {}
        moc_body = ""
        if moc_file.is_file():
            moc_meta, moc_body = parse_frontmatter(moc_file.read_text(encoding="utf-8", errors="replace"))
        final_moc_meta = {
            "syntopik_moc": "traps",
            "title": moc_meta.get("title") or "Syntopik Trap MOC",
            "tags": moc_meta.get("tags") or ["syntopik", "moc", "traps"],
        }
        for k, v in moc_meta.items():
            if k not in final_moc_meta and k not in NOISY_FSRS_KEYS:
                final_moc_meta[k] = v
        moc_inner = _render_trap_moc_managed_block(mistakes, id_to_stem, id_to_title)
        moc_text = render_frontmatter(final_moc_meta) + "\n" + splice_fenced_block(moc_body, moc_inner).lstrip("\n")
        write_text_atomic(moc_file, moc_text)
        trap_moc_path = str(moc_file)

    return {
        "ok": True,
        "vaultDir": str(vdir),
        "kgDir": str(kg),
        "propositionsExported": len(exported_files),
        "created": created_count,
        "updated": updated_count,
        "chainsLinked": len(chains),
        "mistakesInTrapMoc": len(mistakes),
        "trapMocPath": trap_moc_path,
        "files": exported_files,
        "summary": f"Exported {len(exported_files)} proposition(s) to {vdir.name}",
    }


# ---------------------------------------------------------------------------
# 2. Import & Mnemonic Medium Sync (Vault or `chapters/*.md` -> `propositions.jsonl`)
# ---------------------------------------------------------------------------

CUE_OPEN_RE = re.compile(r"^\s*:::\s*cue(?:\s+(.*))?\s*$", re.IGNORECASE)
CUE_CLOSE_RE = re.compile(r"^\s*:::\s*$")
CLOZE_RE = re.compile(r"\{\{c(\d+)::([^}:]+?)(?:::([^}]+?))?\}\}")
PROP_COMMENT_RE = re.compile(r"<!--\s*prop(?:Id)?\s*:\s*([A-Za-z0-9._/-]+)\s*-->", re.IGNORECASE)


def _parse_cue_header_attrs(raw_attr: str) -> dict[str, str]:
    """Parse flexible `:::cue {propId: "...", id: "c1", kind: "application"}` or `k=v` headers."""
    attrs: dict[str, str] = {}
    s = (raw_attr or "").strip()
    if not s:
        return attrs
    # Try direct JSON first
    if s.startswith("{") and s.endswith("}"):
        try:
            parsed = json.loads(s)
            if isinstance(parsed, dict):
                return {str(k): str(v) for k, v in parsed.items() if v is not None}
        except json.JSONDecodeError:
            s = s[1:-1].strip()
    # Parse key: value or key=value pairs (quoted or unquoted values)
    pair_re = re.compile(r'([A-Za-z_][A-Za-z0-9_-]*)\s*[:=]\s*(?:"([^"]*)"|\'([^\']*)\'|([^,\s}]+))')
    for m in pair_re.finditer(s):
        key = m.group(1).strip()
        val = m.group(2) if m.group(2) is not None else (m.group(3) if m.group(3) is not None else m.group(4))
        attrs[key] = str(val).strip()
    return attrs


def _split_qa_body(body_lines: list[str]) -> tuple[str, str]:
    """Extract `(question, answer)` from a `:::cue` block body."""
    q_lines: list[str] = []
    a_lines: list[str] = []
    mode: str | None = None
    for raw_ln in body_lines:
        ln = _unescape_cue_text(raw_ln)
        stripped = ln.strip()
        m_q = re.match(r"^(?:Q|Prompt)\s*:\s*(.*)$", stripped, re.IGNORECASE)
        m_a = re.match(r"^(?:A|Answer)\s*:\s*(.*)$", stripped, re.IGNORECASE)
        if m_q:
            mode = "q"
            if m_q.group(1).strip():
                q_lines.append(m_q.group(1).strip())
        elif m_a:
            mode = "a"
            if m_a.group(1).strip():
                a_lines.append(m_a.group(1).strip())
        elif mode == "q":
            if stripped:
                q_lines.append(stripped)
        elif mode == "a":
            if stripped:
                a_lines.append(stripped)

    if q_lines or a_lines:
        return " ".join(q_lines).strip(), " ".join(a_lines).strip()

    # Fallback: split on first blank line
    unescaped_all = "\n".join(_unescape_cue_text(ln) for ln in body_lines).strip()
    non_empty_blocks = [b.strip() for b in re.split(r"\n\s*\n", unescaped_all) if b.strip()]
    if len(non_empty_blocks) >= 2:
        return " ".join(non_empty_blocks[0].split()), " ".join(" ".join(non_empty_blocks[1:]).split())
    if len(non_empty_blocks) == 1:
        return " ".join(non_empty_blocks[0].split()), ""
    return "", ""


def _render_cloze_cards(line_text: str) -> list[dict[str, Any]]:
    """Expand `{{c1::...}}` clozes on a line into one cue per distinct cloze index (`c1`, `c2`, ...)."""
    matches = list(CLOZE_RE.finditer(line_text))
    if not matches:
        return []
    indices = sorted({int(m.group(1)) for m in matches})
    cards: list[dict[str, Any]] = []
    for idx in indices:
        answers: list[str] = []

        def _replace(m: re.Match[str], target_idx: int = idx, ans_list: list[str] = answers) -> str:
            cnum = int(m.group(1))
            ans = m.group(2).strip()
            hint = (m.group(3) or "").strip()
            if cnum == target_idx:
                ans_list.append(ans)
                return f"[{hint}]" if hint else "[...]"
            return ans

        question = CLOZE_RE.sub(_replace, line_text).strip()
        # Strip leading markdown list bullet or blockquote prefix for crisp card front
        question = re.sub(r"^(?:[-*+]\s+|\d+\.\s+|>\s+)", "", question).strip()
        answer = "; ".join(answers)
        cards.append(
            {
                "clozeIndex": idx,
                "q": question,
                "a": answer,
                "rawCloze": line_text.strip(),
            }
        )
    return cards


def parse_mnemonic_markdown(
    text: str,
    *,
    rel_file: str = "",
    default_prop_id: str | None = None,
) -> dict[str, Any]:
    """Parse `:::cue` blocks, inline `{{c1::...}}` clozes, and `Q:: / A::` pairs with `{file, heading, line}` anchors."""
    meta, _body = parse_frontmatter(text)
    file_prop_id = str(meta.get("syntopik_id") or default_prop_id or "").strip() or None
    file_title = str(meta.get("title") or "").strip()
    file_subject = str(meta.get("subject") or "").strip()
    file_chapter = str(meta.get("chapter") or "").strip()

    lines = text.splitlines()
    # Skip frontmatter lines when scanning, but preserve exact 1-based line numbers!
    start_line_idx = 0
    if lines and lines[0].strip() == "---":
        for idx in range(1, len(lines)):
            if lines[idx].strip() in ("---", "..."):
                start_line_idx = idx + 1
                break

    current_heading = file_title or (Path(rel_file).stem if rel_file else "")
    active_prop_override: str | None = None
    in_code_fence = False
    cues: list[dict[str, Any]] = []
    clozes: list[dict[str, Any]] = []

    i = start_line_idx
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        line_no = i + 1

        # Track fenced code blocks (never parse clozes/cues inside ``` code)
        if re.match(r"^(?:```|~~~)", stripped):
            in_code_fence = not in_code_fence
            i += 1
            continue
        if in_code_fence:
            i += 1
            continue

        # Track headings
        m_head = re.match(r"^#{1,6}\s+(.+)$", stripped)
        if m_head:
            current_heading = m_head.group(1).strip()
            active_prop_override = None
            i += 1
            continue

        # Optional HTML prop anchor comment: <!-- prop: my-prop-id -->
        m_prop = PROP_COMMENT_RE.search(line)
        if m_prop:
            active_prop_override = m_prop.group(1).strip()
            i += 1
            continue

        # 1. Fenced Matuschak `:::cue {propId, id, kind}` block
        m_open = CUE_OPEN_RE.match(line)
        if m_open:
            attrs = _parse_cue_header_attrs(m_open.group(1) or "")
            cue_start_line = line_no
            body_lines: list[str] = []
            i += 1
            while i < len(lines) and not CUE_CLOSE_RE.match(lines[i]):
                body_lines.append(lines[i])
                i += 1
            if i < len(lines) and CUE_CLOSE_RE.match(lines[i]):
                i += 1
            q_text, a_text = _split_qa_body(body_lines)
            if q_text or a_text:
                pid = (
                    attrs.get("propId")
                    or attrs.get("prop_id")
                    or attrs.get("prop")
                    or active_prop_override
                    or file_prop_id
                    or slugify(f"{Path(rel_file).stem}-{current_heading}" if rel_file else current_heading, 64)
                )
                kind = str(attrs.get("kind") or "recall").strip().lower()
                if kind not in VALID_CUE_KINDS:
                    kind = "recall"
                cues.append(
                    {
                        "propId": pid,
                        "id": attrs.get("id") or None,
                        "kind": kind,
                        "q": q_text,
                        "a": a_text,
                        "source": "mnemonic_cue",
                        "titleHint": current_heading or file_title or pid,
                        "subjectHint": file_subject,
                        "chapterHint": file_chapter,
                        "anchor": {
                            "file": rel_file,
                            "heading": current_heading,
                            "line": cue_start_line,
                        },
                    }
                )
            continue

        # 2. Inline `{{c1::...}}` Cloze deletions
        if CLOZE_RE.search(line):
            pid = (
                active_prop_override
                or file_prop_id
                or slugify(f"{Path(rel_file).stem}-{current_heading}" if rel_file else current_heading, 64)
            )
            for card in _render_cloze_cards(line):
                fp = hashlib.sha1(f"{pid}|c{card['clozeIndex']}|{card['q']}".encode("utf-8")).hexdigest()[:6]
                clozes.append(
                    {
                        "propId": pid,
                        "id": f"cloze-c{card['clozeIndex']}-{fp}",
                        "kind": "recall",
                        "cloze": True,
                        "clozeIndex": card["clozeIndex"],
                        "q": card["q"],
                        "a": card["a"],
                        "rawCloze": card["rawCloze"],
                        "source": "inline_cloze",
                        "titleHint": current_heading or file_title or pid,
                        "subjectHint": file_subject,
                        "chapterHint": file_chapter,
                        "anchor": {
                            "file": rel_file,
                            "heading": current_heading,
                            "line": line_no,
                        },
                    }
                )
            i += 1
            continue

        # 3. Obsidian inline `Q:: ...` followed by `A:: ...` (same line via `;;` or next line)
        m_qa_same = re.match(r"^(?:[-*+]\s+)?Q::\s*(.+?)\s*;;\s*A::\s*(.+)$", stripped)
        if m_qa_same:
            pid = (
                active_prop_override
                or file_prop_id
                or slugify(f"{Path(rel_file).stem}-{current_heading}" if rel_file else current_heading, 64)
            )
            cues.append(
                {
                    "propId": pid,
                    "id": None,
                    "kind": "recall",
                    "q": m_qa_same.group(1).strip(),
                    "a": m_qa_same.group(2).strip(),
                    "source": "obsidian_qa",
                    "titleHint": current_heading or file_title or pid,
                    "subjectHint": file_subject,
                    "chapterHint": file_chapter,
                    "anchor": {"file": rel_file, "heading": current_heading, "line": line_no},
                }
            )
            i += 1
            continue

        m_q_only = re.match(r"^(?:[-*+]\s+)?Q::\s*(.+)$", stripped)
        if m_q_only and i + 1 < len(lines):
            m_a_next = re.match(r"^(?:[-*+]\s+)?A::\s*(.+)$", lines[i + 1].strip())
            if m_a_next:
                pid = (
                    active_prop_override
                    or file_prop_id
                    or slugify(f"{Path(rel_file).stem}-{current_heading}" if rel_file else current_heading, 64)
                )
                cues.append(
                    {
                        "propId": pid,
                        "id": None,
                        "kind": "recall",
                        "q": m_q_only.group(1).strip(),
                        "a": m_a_next.group(1).strip(),
                        "source": "obsidian_qa",
                        "titleHint": current_heading or file_title or pid,
                        "subjectHint": file_subject,
                        "chapterHint": file_chapter,
                        "anchor": {"file": rel_file, "heading": current_heading, "line": line_no},
                    }
                )
                i += 2
                continue

        i += 1

    return {
        "frontmatter": meta,
        "cues": cues,
        "clozes": clozes,
    }


def _norm_text(s: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", str(s or "")).strip().lower())


def _token_dice(a: str, b: str) -> float:
    ta = set(re.findall(r"[a-z0-9]{2,}", _norm_text(a)))
    tb = set(re.findall(r"[a-z0-9]{2,}", _norm_text(b)))
    if not ta or not tb:
        return 0.0
    return (2.0 * len(ta & tb)) / (len(ta) + len(tb))


def _find_matching_proposition(
    props: list[dict[str, Any]],
    prop_id: str,
    title_hint: str,
) -> dict[str, Any] | None:
    for p in props:
        if str(p.get("id") or "").strip() == prop_id:
            return p
    # Fuzzy dedup by title similarity (high threshold >= 0.78 to prevent false merges)
    if title_hint and len(title_hint.split()) >= 3:
        best_p: dict[str, Any] | None = None
        best_score = 0.0
        for p in props:
            sc = _token_dice(title_hint, str(p.get("title") or ""))
            if sc > best_score:
                best_score = sc
                best_p = p
        if best_p is not None and best_score >= 0.78:
            return best_p
    return None


def _merge_parsed_items_into_kg(
    parsed_items: list[dict[str, Any]],
    *,
    props_file: Path,
    default_subject: str = "General",
    default_chapter: str = "Synced Notes",
) -> dict[str, Any]:
    """Merge extracted cues & clozes into `propositions.jsonl` without clobbering FSRS state."""
    props = load_jsonl(props_file)
    props_created = 0
    props_updated = 0
    cues_added = 0
    cues_updated = 0
    cues_unchanged = 0
    touched_prop_ids: set[str] = set()

    for item in parsed_items:
        pid = str(item["propId"]).strip()
        title_hint = str(item.get("titleHint") or pid).strip()
        target = _find_matching_proposition(props, pid, title_hint)

        if target is None:
            target = {
                "id": pid,
                "subject": item.get("subjectHint") or default_subject,
                "chapter": item.get("chapterHint") or default_chapter,
                "title": title_hint,
                "kind": "concept",
                "status": "planned",
                "sourceAnchor": item["anchor"],
                "cues": [],
            }
            props.append(target)
            props_created += 1
            touched_prop_ids.add(pid)
        else:
            # Never overwrite existing fsrs, bktMastery, confidence, status, lastTested, nextReview!
            if "sourceAnchor" not in target and item.get("anchor"):
                target["sourceAnchor"] = item["anchor"]

        cues = target.get("cues")
        if not isinstance(cues, list):
            cues = []
            target["cues"] = cues

        req_id = item.get("id")
        norm_q = _norm_text(item["q"])
        norm_a = _norm_text(item["a"])

        existing_cue: dict[str, Any] | None = None
        if req_id:
            existing_cue = next((c for c in cues if isinstance(c, dict) and str(c.get("id")) == str(req_id)), None)
        if existing_cue is None:
            existing_cue = next(
                (
                    c
                    for c in cues
                    if isinstance(c, dict)
                    and _norm_text(str(c.get("q") or "")) == norm_q
                    and _norm_text(str(c.get("a") or "")) == norm_a
                ),
                None,
            )

        if existing_cue is not None:
            changed = False
            if existing_cue.get("q") != item["q"]:
                existing_cue["q"] = item["q"]
                changed = True
            if existing_cue.get("a") != item["a"]:
                existing_cue["a"] = item["a"]
                changed = True
            if existing_cue.get("kind") != item["kind"]:
                existing_cue["kind"] = item["kind"]
                changed = True
            if existing_cue.get("anchor") != item["anchor"]:
                existing_cue["anchor"] = item["anchor"]
                changed = True
            if item.get("cloze") and not existing_cue.get("cloze"):
                existing_cue["cloze"] = True
                changed = True
            # Preserve existing lastRating & reviews untouched!
            existing_cue.setdefault("lastRating", None)
            existing_cue.setdefault("reviews", 0)
            if changed:
                cues_updated += 1
                if str(target.get("id")) not in touched_prop_ids:
                    props_updated += 1
                    touched_prop_ids.add(str(target.get("id")))
            else:
                cues_unchanged += 1
        else:
            existing_ids = {str(c.get("id")) for c in cues if isinstance(c, dict) and c.get("id")}
            if req_id and str(req_id) not in existing_ids:
                cid = str(req_id)
            else:
                idx = 1
                while f"c{idx}" in existing_ids:
                    idx += 1
                cid = f"c{idx}"

            new_cue: dict[str, Any] = {
                "id": cid,
                "kind": item["kind"],
                "q": item["q"],
                "a": item["a"],
                "anchor": item["anchor"],
                "lastRating": None,
                "reviews": 0,
            }
            if item.get("cloze"):
                new_cue["cloze"] = True
                new_cue["clozeIndex"] = item.get("clozeIndex", 1)
            cues.append(new_cue)
            cues_added += 1
            if str(target.get("id")) not in touched_prop_ids:
                props_updated += 1
                touched_prop_ids.add(str(target.get("id")))

    if props_created > 0 or cues_added > 0 or cues_updated > 0:
        save_jsonl_atomic(props_file, props)

    return {
        "propositionsCreated": props_created,
        "propositionsUpdated": props_updated,
        "cuesAdded": cues_added,
        "cuesUpdated": cues_updated,
        "cuesUnchanged": cues_unchanged,
        "totalPropositions": len(props),
    }


def _collect_markdown_files(target_dir: Path) -> list[Path]:
    if target_dir.is_symlink():
        return []
    if target_dir.is_file():
        return [target_dir] if target_dir.suffix.lower() in (".md", ".markdown") else []
    if not target_dir.is_dir():
        return []
    files: list[Path] = []
    for dp, dns, fns in os.walk(target_dir, topdown=True, followlinks=False):
        dns[:] = [
            d
            for d in sorted(dns)
            if not d.startswith(".")
            and d not in ("node_modules", ".venv", "__pycache__")
            and not (Path(dp) / d).is_symlink()
        ]
        for fn in sorted(fns):
            fp = Path(dp) / fn
            if fp.is_symlink():
                continue
            if fp.suffix.lower() in (".md", ".markdown") and fp.name != "Trap-MOC.md":
                files.append(fp)
    return files


def import_vault(
    vault_dir: str | Path,
    *,
    workspace: str | Path | None = None,
    kg_dir: str | Path | None = None,
    no_sync: bool = False,
) -> dict[str, Any]:
    """Import `:::cue` blocks, `{{c1::...}}` clozes, and `Q:: / A::` cues from an Obsidian vault."""
    vdir = Path(vault_dir).expanduser().resolve()
    if not vdir.exists():
        raise FileNotFoundError(f"Obsidian vault path not found: {vdir}")
    ws = resolve_workspace(workspace)
    kg = resolve_kg_dir(workspace=workspace, explicit_kg_dir=kg_dir)
    chapters_dir = resolve_chapters_dir(workspace=workspace)
    props_file = kg / "propositions.jsonl"

    md_files = _collect_markdown_files(vdir)
    all_items: list[dict[str, Any]] = []
    cues_parsed = 0
    clozes_parsed = 0

    for fp in md_files:
        rel_file = fp.name if vdir.is_file() else fp.relative_to(vdir).as_posix()
        text = fp.read_text(encoding="utf-8", errors="replace")
        parsed = parse_mnemonic_markdown(text, rel_file=rel_file)
        cues_parsed += len(parsed["cues"])
        clozes_parsed += len(parsed["clozes"])
        all_items.extend(parsed["cues"])
        all_items.extend(parsed["clozes"])

    merge_stats = _merge_parsed_items_into_kg(
        all_items,
        props_file=props_file,
        default_chapter="Obsidian Vault",
    )
    if not no_sync:
        _sync_hub_quiet(ws, kg, chapters_dir)

    return {
        "ok": True,
        "mode": "import",
        "vaultDir": str(vdir),
        "propsFile": str(props_file),
        "filesScanned": len(md_files),
        "cuesParsed": cues_parsed,
        "clozesParsed": clozes_parsed,
        "anchorsRecorded": len(all_items),
        **merge_stats,
    }


def sync_notes(
    chapters_dir: str | Path | None = None,
    *,
    workspace: str | Path | None = None,
    kg_dir: str | Path | None = None,
    no_sync: bool = False,
) -> dict[str, Any]:
    """Sync Matuschak `:::cue` blocks & inline `{{c1::...}}` clozes from `chapters/*.md` into `propositions.jsonl`."""
    ws = resolve_workspace(workspace)
    cdir = resolve_chapters_dir(workspace=workspace, explicit_chapters_dir=chapters_dir)
    kg = resolve_kg_dir(workspace=workspace, explicit_kg_dir=kg_dir)
    props_file = kg / "propositions.jsonl"

    md_files = _collect_markdown_files(cdir)
    all_items: list[dict[str, Any]] = []
    cues_parsed = 0
    clozes_parsed = 0

    for fp in md_files:
        try:
            rel_file = fp.resolve().relative_to(ws.resolve()).as_posix()
        except ValueError:
            rel_file = fp.name if cdir.is_file() else fp.relative_to(cdir).as_posix()
        text = fp.read_text(encoding="utf-8", errors="replace")
        parsed = parse_mnemonic_markdown(text, rel_file=rel_file)
        cues_parsed += len(parsed["cues"])
        clozes_parsed += len(parsed["clozes"])
        all_items.extend(parsed["cues"])
        all_items.extend(parsed["clozes"])

    merge_stats = _merge_parsed_items_into_kg(
        all_items,
        props_file=props_file,
        default_chapter="Chapter Notes",
    )
    if not no_sync:
        _sync_hub_quiet(ws, kg, cdir if cdir.is_dir() else resolve_chapters_dir(workspace=workspace))

    return {
        "ok": True,
        "mode": "sync-notes",
        "chaptersDir": str(cdir),
        "propsFile": str(props_file),
        "filesScanned": len(md_files),
        "cuesParsed": cues_parsed,
        "clozesParsed": clozes_parsed,
        "anchorsRecorded": len(all_items),
        **merge_stats,
    }


# ---------------------------------------------------------------------------
# CLI Parser & Entrypoint
# ---------------------------------------------------------------------------


def _emit(payload: dict[str, Any], as_json: bool, summary_lines: list[str]) -> None:
    if as_json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        for ln in summary_lines:
            print(ln)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vault_sync.py",
        description="Syntopik v2.0 — Bidirectional Obsidian Vault & Matuschak Mnemonic Medium Sync",
    )
    parser.add_argument("--workspace", default=None, help="Workspace root override (or SYNT_WORKSPACE)")
    parser.add_argument("--kg-dir", default=None, help="Explicit knowledge-graph directory override")
    parser.add_argument("--no-sync", action="store_true", default=False, help="Skip refreshing graph-data.js")
    parser.add_argument("--json", action="store_true", default=False, help="Emit structured JSON output")
    sub = parser.add_subparsers(dest="cmd", required=True)

    # export
    p_exp = sub.add_parser("export", help="Export KG propositions, chain wikilinks, and Trap MOC to Obsidian vault")
    p_exp.add_argument("--vault", required=True, help="Path to target Obsidian vault directory")
    p_exp.add_argument("--no-trap-moc", action="store_true", help="Skip generating Trap-MOC.md")
    p_exp.add_argument("--workspace", default=argparse.SUPPRESS)
    p_exp.add_argument("--kg-dir", default=argparse.SUPPRESS)
    p_exp.add_argument("--json", action="store_true", default=argparse.SUPPRESS)

    # import
    p_imp = sub.add_parser("import", help="Import :::cue blocks & {{c1::...}} clozes from Obsidian vault into KG")
    p_imp.add_argument("--vault", required=True, help="Path to source Obsidian vault directory or .md file")
    p_imp.add_argument("--workspace", default=argparse.SUPPRESS)
    p_imp.add_argument("--kg-dir", default=argparse.SUPPRESS)
    p_imp.add_argument("--no-sync", action="store_true", default=argparse.SUPPRESS)
    p_imp.add_argument("--json", action="store_true", default=argparse.SUPPRESS)

    # sync-notes
    p_sn = sub.add_parser("sync-notes", help="Sync :::cue blocks & {{c1::...}} clozes from chapters/*.md into KG")
    p_sn.add_argument("--chapters-dir", default=None, help="Chapters directory or single Markdown file (default: <ws>/chapters)")
    p_sn.add_argument("--workspace", default=argparse.SUPPRESS)
    p_sn.add_argument("--kg-dir", default=argparse.SUPPRESS)
    p_sn.add_argument("--no-sync", action="store_true", default=argparse.SUPPRESS)
    p_sn.add_argument("--json", action="store_true", default=argparse.SUPPRESS)

    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    ws = getattr(args, "workspace", None)
    kg_dir = getattr(args, "kg_dir", None)
    no_sync = bool(getattr(args, "no_sync", False))
    as_json = bool(getattr(args, "json", False))

    try:
        if args.cmd == "export":
            res = export_vault(
                args.vault,
                workspace=ws,
                kg_dir=kg_dir,
                include_trap_moc=not args.no_trap_moc,
            )
            _emit(
                res,
                as_json,
                [
                    f"Exported {res['propositionsExported']} proposition(s) "
                    f"({res['created']} created, {res['updated']} updated) + "
                    f"{res['chainsLinked']} chain links to {res['vaultDir']}"
                ],
            )
        elif args.cmd == "import":
            res = import_vault(
                args.vault,
                workspace=ws,
                kg_dir=kg_dir,
                no_sync=no_sync,
            )
            _emit(
                res,
                as_json,
                [
                    f"Imported from {res['vaultDir']}: {res['filesScanned']} files, "
                    f"{res['cuesParsed']} :::cue + {res['clozesParsed']} clozes -> "
                    f"+{res['cuesAdded']} added, {res['cuesUpdated']} updated, {res['cuesUnchanged']} unchanged"
                ],
            )
        elif args.cmd == "sync-notes":
            res = sync_notes(
                args.chapters_dir,
                workspace=ws,
                kg_dir=kg_dir,
                no_sync=no_sync,
            )
            _emit(
                res,
                as_json,
                [
                    f"Synced notes from {res['chaptersDir']}: {res['filesScanned']} files, "
                    f"{res['cuesParsed']} :::cue + {res['clozesParsed']} clozes -> "
                    f"+{res['cuesAdded']} added, {res['cuesUpdated']} updated, {res['cuesUnchanged']} unchanged"
                ],
            )
    except (ValueError, FileNotFoundError, OSError) as exc:
        if as_json:
            print(json.dumps({"ok": False, "error": str(exc)}, indent=2, ensure_ascii=False))
        else:
            print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
