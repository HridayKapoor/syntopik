"""Syntopik — Importer Plugin API (pure stdlib).

Lets anyone teach `syntopik import` a new source format without touching core
code. A plugin only *parses*: it turns a file (or folder) into plain card dicts.
Core owns everything that touches the learner's data: size caps, text cleanup,
building `status: "planned"` propositions (zero fake mastery), de-duplication,
and the locked, atomic write to `propositions.jsonl`.

Writing a plugin
----------------
A plugin is a module that defines `SYNTOPIK_IMPORTER`::

    def parse(path, options):
        for line in path.read_text(encoding="utf-8").splitlines():
            term, _, meaning = line.partition(" = ")
            if meaning:
                yield {"q": f"What does '{term}' mean?", "a": meaning, "tags": ["glossary"]}

    SYNTOPIK_IMPORTER = {
        "name": "glossary",                 # used as `syntopik import --kind glossary`
        "description": "term = meaning lines",
        "extensions": [".txt"],             # informational, shown in --list-importers
        "accepts": "file",                  # "file" (default), "dir", or "any"
        "apiVersion": 1,
        "parse": parse,
    }

Each yielded card is a dict with `q` (required unless `statement` is given) and
optional `a`, `title`, `statement`, `chapter`, `subject`, `tags`, `notes`, and
`kind` ("recall" or "cloze"). Unknown keys are ignored.

Where plugins are loaded from
-----------------------------
1. Built-ins shipped with Syntopik (e.g. `kindle` for Kindle "My Clippings.txt").
2. Installed Python packages exposing the `syntopik.importers` entry-point group
   (same trust as any `pip install`).
3. `<workspace>/importers/*.py`, **only** when `knowledge-graph/study-config.json`
   sets `"allowLocalImporters": true`. Off by default, so a shared pack or a
   cloned vault can never run code just by being opened.

Plugins cannot shadow built-in kinds (`anki`, `cards`, `url`, ...).
"""

from __future__ import annotations

import importlib.util
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from syntopik.paths import resolve_kg_dir, resolve_workspace
from syntopik.storage import load_json

__all__ = [
    "IMPORTER_API_VERSION",
    "ENTRY_POINT_GROUP",
    "RESERVED_KINDS",
    "ImporterSpec",
    "discover_importers",
    "list_importers",
    "run_plugin_import",
]

IMPORTER_API_VERSION = 1
ENTRY_POINT_GROUP = "syntopik.importers"
LOCAL_IMPORTERS_DIRNAME = "importers"
LOCAL_OPT_IN_KEY = "allowLocalImporters"

# Kinds handled by core `syntopik import`; plugins may not take these names.
RESERVED_KINDS = frozenset(
    {"auto", "youtube", "arxiv", "repo", "url", "notes", "anki", "cards", "notebook", "notes_batch", "pocket"}
)
_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{1,31}$")
_MAX_PLUGIN_INPUT_BYTES = 10 * 1024 * 1024
_MAX_FIELD_CHARS = {"q": 2000, "a": 4000, "title": 160, "statement": 4000, "notes": 4000, "chapter": 120, "subject": 60}
_MAX_TAGS = 12
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


@dataclass(frozen=True)
class ImporterSpec:
    name: str
    description: str
    parse: Callable[[Path, dict[str, Any]], Iterable[dict[str, Any]]]
    extensions: tuple[str, ...] = ()
    accepts: str = "file"
    source: str = "builtin"
    api_version: int = IMPORTER_API_VERSION

    def public(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "extensions": list(self.extensions),
            "accepts": self.accepts,
            "source": self.source,
            "apiVersion": self.api_version,
        }


@dataclass
class _Discovery:
    importers: dict[str, ImporterSpec] = field(default_factory=dict)
    errors: list[dict[str, str]] = field(default_factory=list)
    local_dir: str | None = None
    local_enabled: bool = False


def _spec_from_object(obj: Any, *, source: str) -> ImporterSpec:
    """Validate a plugin's `SYNTOPIK_IMPORTER` (dict, or a module exposing it)."""
    raw = getattr(obj, "SYNTOPIK_IMPORTER", obj)
    if callable(raw) and not isinstance(raw, dict):
        raw = raw()
    if not isinstance(raw, dict):
        raise TypeError("SYNTOPIK_IMPORTER must be a dict")
    name = str(raw.get("name") or "").strip().lower()
    if not _NAME_RE.match(name):
        raise ValueError(f"invalid importer name {name!r} (use 2-32 chars: a-z, 0-9, '-', '_')")
    if name in RESERVED_KINDS:
        raise ValueError(f"importer name {name!r} is reserved by a built-in kind")
    parse = raw.get("parse")
    if not callable(parse):
        raise TypeError(f"importer {name!r} has no callable 'parse'")
    api_version = int(raw.get("apiVersion") or IMPORTER_API_VERSION)
    if api_version > IMPORTER_API_VERSION:
        raise ValueError(f"importer {name!r} needs API v{api_version}; this Syntopik supports v{IMPORTER_API_VERSION}")
    accepts = str(raw.get("accepts") or "file").strip().lower()
    if accepts not in ("file", "dir", "any"):
        raise ValueError(f"importer {name!r}: 'accepts' must be file, dir or any")
    exts = tuple(
        e if e.startswith(".") else f".{e}"
        for e in (str(x).strip().lower() for x in (raw.get("extensions") or []))
        if e
    )
    return ImporterSpec(
        name=name,
        description=str(raw.get("description") or "").strip()[:200],
        parse=parse,
        extensions=exts,
        accepts=accepts,
        source=source,
        api_version=api_version,
    )


def _builtin_importers() -> list[tuple[Any, str]]:
    from syntopik import kindle_clippings

    return [(kindle_clippings, "builtin")]


def _entry_point_importers() -> list[tuple[Any, str]]:
    from importlib import metadata

    out: list[tuple[Any, str]] = []
    try:
        eps = metadata.entry_points(group=ENTRY_POINT_GROUP)
    except Exception:
        return out
    for ep in eps:
        dist = getattr(getattr(ep, "dist", None), "name", None) or ep.value.split(":")[0]
        out.append((ep, f"package:{dist}"))
    return out


def _local_dir(ws: Path) -> Path:
    return ws / LOCAL_IMPORTERS_DIRNAME


def _local_opt_in(kg_dir: Path) -> bool:
    cfg = load_json(kg_dir / "study-config.json", default={})
    return isinstance(cfg, dict) and cfg.get(LOCAL_OPT_IN_KEY) is True


def _load_local_module(path: Path, base: Path) -> Any:
    real = path.resolve()
    if path.is_symlink() or not real.is_file() or base.resolve() not in real.parents:
        raise ValueError("local importer must be a regular file inside the importers/ folder")
    mod_name = f"_syntopik_local_importer_{re.sub(r'[^A-Za-z0-9_]', '_', path.stem)}"
    spec = importlib.util.spec_from_file_location(mod_name, str(real))
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path.name}")
    mod = importlib.util.module_from_spec(spec)
    prev = sys.dont_write_bytecode
    sys.dont_write_bytecode = True  # never drop __pycache__ into the learner's vault
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.dont_write_bytecode = prev
    return mod


def discover_importers(
    workspace: Path | str | None = None, *, kg_dir: Path | str | None = None
) -> _Discovery:
    """Find every usable importer. Broken plugins are reported, never raised."""
    ws = resolve_workspace(workspace)
    kdir = Path(kg_dir).expanduser().resolve() if kg_dir else resolve_kg_dir(ws)
    found = _Discovery()

    def _add(obj: Any, source: str) -> None:
        label = source
        try:
            if hasattr(obj, "load") and hasattr(obj, "group"):  # importlib.metadata.EntryPoint
                label = f"{source} ({obj.name})"
                obj = obj.load()
            spec = _spec_from_object(obj, source=source)
        except Exception as exc:  # a broken plugin must never break `syntopik import`
            found.errors.append({"source": label, "error": f"{type(exc).__name__}: {exc}"})
            return
        if spec.name in found.importers:
            found.errors.append(
                {"source": label, "error": f"name {spec.name!r} already provided by {found.importers[spec.name].source}"}
            )
            return
        found.importers[spec.name] = spec

    for obj, source in _builtin_importers():
        _add(obj, source)
    for obj, source in _entry_point_importers():
        _add(obj, source)

    local = _local_dir(ws)
    found.local_dir = str(local)
    found.local_enabled = _local_opt_in(kdir)
    if found.local_enabled and local.is_dir():
        for path in sorted(local.glob("*.py")):
            if path.name.startswith("_"):
                continue
            try:
                mod = _load_local_module(path, local)
            except Exception as exc:
                found.errors.append({"source": f"local:{path.name}", "error": f"{type(exc).__name__}: {exc}"})
                continue
            _add(mod, f"local:{path.name}")
    return found


def list_importers(workspace: Path | str | None = None) -> dict[str, Any]:
    d = discover_importers(workspace)
    return {
        "ok": True,
        "kind": "importer_list",
        "apiVersion": IMPORTER_API_VERSION,
        "builtinKinds": sorted(RESERVED_KINDS - {"pocket"}),
        "plugins": [spec.public() for spec in sorted(d.importers.values(), key=lambda s: s.name)],
        "errors": d.errors,
        "localImportersDir": d.local_dir,
        "localImportersEnabled": d.local_enabled,
    }


def _clean_text(value: Any, key: str) -> str:
    if value is None:
        return ""
    text = unicodedata.normalize("NFC", str(value))
    text = _CONTROL_RE.sub("", text).replace("\r\n", "\n").strip()
    limit = _MAX_FIELD_CHARS.get(key, 2000)
    return text if len(text) <= limit else text[: limit - 3].rstrip() + "..."


def _clean_card(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    card = {k: _clean_text(raw.get(k), k) for k in ("q", "a", "title", "statement", "chapter", "subject", "notes")}
    if not card["q"] and not card["statement"]:
        return None
    tags_raw = raw.get("tags") or []
    if isinstance(tags_raw, str):
        tags_raw = [tags_raw]
    card["tags"] = [_clean_text(t, "tag")[:40] for t in list(tags_raw)[:_MAX_TAGS] if str(t or "").strip()]
    kind = str(raw.get("kind") or "").strip().lower()
    card["kind"] = kind if kind in ("recall", "cloze") else ""
    return card


def _check_target(target: str | Path, accepts: str) -> Path:
    p = Path(target).expanduser()
    if not p.exists():
        raise FileNotFoundError(f"Import source not found: {p}")
    if p.is_dir():
        if accepts == "file":
            raise ValueError(f"This importer expects a file, got a folder: {p}")
        return p.resolve()
    if accepts == "dir":
        raise ValueError(f"This importer expects a folder, got a file: {p}")
    if not p.is_file():
        raise ValueError(f"Import source must be a regular file: {p}")
    size = p.stat().st_size
    if size > _MAX_PLUGIN_INPUT_BYTES:
        raise ValueError(f"Import source ({size} bytes) exceeds safety limit ({_MAX_PLUGIN_INPUT_BYTES} bytes).")
    return p.resolve()


def run_plugin_import(
    kind: str,
    target: str | Path,
    *,
    workspace: Path | str | None = None,
    kg_dir: Path | str | None = None,
    subject: str = "General",
    chapter: str | None = None,
    limit: int | None = None,
    dry_run: bool = False,
    options: dict[str, Any] | None = None,
    discovery: _Discovery | None = None,
) -> dict[str, Any]:
    """Run plugin `kind` on `target` and merge its cards as `planned` propositions."""
    from syntopik import importers as core

    ws = resolve_workspace(workspace)
    kdir = Path(kg_dir).expanduser().resolve() if kg_dir else resolve_kg_dir(ws)
    disc = discovery or discover_importers(ws, kg_dir=kdir)
    name = str(kind or "").strip().lower()
    spec = disc.importers.get(name)
    if spec is None:
        hint = ", ".join(sorted(disc.importers)) or "none installed"
        return {"ok": False, "kind": name, "error": f"Unknown importer {name!r} (plugins: {hint})", "pluginErrors": disc.errors}

    try:
        src = _check_target(target, spec.accepts)
    except (OSError, ValueError) as exc:
        return {"ok": False, "kind": name, "source": spec.source, "error": str(exc)}
    eff_limit = max(1, min(core.MAX_IMPORTED_ITEMS, int(limit or core.MAX_CARDS)))
    clean_subject = core._safe_subject(subject, default="General")
    default_chapter = str(chapter or "").strip() or src.stem.replace("_", " ").strip() or name

    cards: list[dict[str, Any]] = []
    skipped = 0
    truncated = False
    try:
        for raw in spec.parse(src, dict(options or {})):
            if len(cards) >= eff_limit:
                truncated = True
                break
            card = _clean_card(raw)
            if card is None:
                skipped += 1
                continue
            cards.append(card)
    except Exception as exc:
        return {
            "ok": False,
            "kind": name,
            "source": spec.source,
            "error": f"Importer {name!r} failed: {type(exc).__name__}: {exc}",
        }

    candidates: list[dict[str, Any]] = []
    for idx, card in enumerate(cards, 1):
        q = card["q"] or card["statement"]
        statement = card["statement"] or card["a"] or q
        title = card["title"] or core._derive_title(statement or q, fallback=f"{name} item {idx}")
        cue_kind = card["kind"] or ("cloze" if core.CLOZE_PATTERN.search(q) else "recall")
        candidates.append(
            core._build_planned_proposition(
                prop_id=f"{name}-{title}",
                title=title,
                statement=statement,
                subject=core._safe_subject(card["subject"], default=clean_subject) if card["subject"] else clean_subject,
                chapter=card["chapter"] or default_chapter,
                kind=cue_kind,
                cues=[{"id": "c1", "q": q, "a": card["a"], "kind": cue_kind}],
                tags=[name, *card["tags"]],
                notes=card["notes"],
                source_meta={"kind": name, "plugin": spec.source, "file": src.name, "item": idx},
            )
        )

    merge = core._merge_imported_propositions(
        candidates, props_file=kdir / "propositions.jsonl", dry_run=bool(dry_run)
    )
    return {
        "ok": True,
        "kind": name,
        "plugin": spec.public(),
        "title": default_chapter,
        "subject": clean_subject,
        "chapter": default_chapter,
        "parsedCount": len(cards),
        "importedCount": merge["importedCount"],
        "deduplicatedCount": merge["deduplicatedCount"],
        "cuesMerged": merge["cuesMerged"],
        "skippedCount": skipped,
        "truncated": truncated,
        "propsFile": merge["propsFile"],
        "dryRun": bool(dry_run),
        "propositions": merge["imported"],
    }
