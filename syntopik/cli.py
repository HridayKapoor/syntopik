"""Unified pure-stdlib CLI for Syntopik v3.2 (`syntopik`).

Verbs:
  init | attach | connect | serve | import | review | triage | pack | doctor | export | sync | run
  next | profile | drop | demo | debrief | mode | sunset | pace | board | bench
  rosetta | sync-delta | calibrate-policy | proof-diff
  readiness | interleave
"""

from __future__ import annotations

import argparse
import difflib
import importlib
import inspect
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from syntopik import __version__
from syntopik.agents_compat import (
    ALL_HARNESSES,
    DEFAULT_FALLBACK_HARNESSES,
    DEFAULT_HTTP_MCP_URL,
    attach_harness_config,
    detect_installed_agents,
    format_compat_table,
    get_compat_matrix,
    inspect_antigravity_plugin,
    is_ephemeral_install_root,
    link_or_copy_dir,
)
from syntopik.paths import (
    find_node,
    resolve_chapters_dir,
    resolve_kg_dir,
    resolve_package_root,
    resolve_workspace,
    sync_hub,
)
from syntopik.storage import (
    load_json,
    load_jsonl,
    save_json_atomic,
    save_jsonl_atomic,
    write_text_atomic,
)

__all__ = ["ALL_HARNESSES", "attach_workspace", "build_parser", "main"]

STARTER_PACK_SLUGS: tuple[str, ...] = (
    "raft-consensus",
    "shm-physics",
    "transformers-attention",
)

_is_ephemeral_install_root = is_ephemeral_install_root


def _ensure_script_import(module_name: str, workspace: Path | None = None) -> Any:
    pkg_root = resolve_package_root()
    candidates = [pkg_root / "scripts"]
    if workspace is not None:
        candidates.insert(0, Path(workspace).resolve() / "scripts")
    for base in candidates:
        if base.is_dir() and str(base) not in sys.path:
            sys.path.insert(0, str(base))
    return importlib.import_module(module_name)


def _lazy_import(module_name: str) -> Any:
    try:
        return importlib.import_module(module_name)
    except ImportError as exc:
        raise SystemExit(f"Required Syntopik module '{module_name}' is not available: {exc}") from exc


def _find_python_for_workspace(workspace: Path, pkg_root: Path) -> str:
    for base in (workspace, pkg_root):
        for rel in (Path(".venv") / "bin" / "python", Path(".venv") / "Scripts" / "python.exe"):
            cand = base / rel
            if cand.is_file():
                return str(cand)
    return sys.executable


def _parse_duration_hours(raw: str | float | int | None, *, default: float = 48.0) -> float:
    """Parse duration strings like '48h', '2d', '1.5d', '90m', or numeric hours into float hours."""
    if raw is None:
        return float(default)
    if isinstance(raw, (int, float)):
        val = float(raw)
        if val <= 0:
            raise ValueError(f"Duration must be positive, got {raw!r}.")
        return val
    text = str(raw).strip().lower()
    if not text:
        return float(default)
    match = re.fullmatch(
        r"([0-9]+(?:\.[0-9]+)?)\s*(h|hr|hrs|hour|hours|d|day|days|m|min|mins|minute|minutes)?",
        text,
    )
    if not match:
        raise ValueError(f"Invalid duration {raw!r}; expected e.g. '48h', '2d', '90m', or '48'.")
    amount = float(match.group(1))
    unit = match.group(2) or "h"
    if amount <= 0:
        raise ValueError(f"Duration must be positive, got {raw!r}.")
    if unit in ("d", "day", "days"):
        return amount * 24.0
    if unit in ("m", "min", "mins", "minute", "minutes"):
        return amount / 60.0
    return amount


def _resolve_git_info_exclude(repo_dir: Path) -> Path | None:
    dot_git = repo_dir / ".git"
    if not dot_git.exists():
        return None
    if dot_git.is_dir():
        return dot_git / "info" / "exclude"
    if dot_git.is_file():
        raw = dot_git.read_text(encoding="utf-8", errors="ignore").strip()
        if raw.lower().startswith("gitdir:"):
            target = raw.split(":", 1)[1].strip()
            git_dir = (repo_dir / target).resolve()
            return git_dir / "info" / "exclude"
    return None


def _update_git_exclude(repo_dir: Path, entries: list[str]) -> dict[str, Any]:
    exclude_path = _resolve_git_info_exclude(repo_dir)
    if exclude_path is None:
        return {"updated": False, "path": None, "entries": []}

    exclude_path.parent.mkdir(parents=True, exist_ok=True)
    existing = exclude_path.read_text(encoding="utf-8") if exclude_path.is_file() else ""
    begin_marker = "# >>> syntopik >>>"
    end_marker = "# <<< syntopik <<<"
    start_idx = existing.find(begin_marker)
    end_idx = existing.find(end_marker)
    prior_block_entries: list[str] = []
    if start_idx != -1 and end_idx != -1 and end_idx >= start_idx:
        inner_raw = existing[start_idx + len(begin_marker) : end_idx]
        prior_block_entries = [ln.strip() for ln in inner_raw.splitlines() if ln.strip()]

    unique_entries = list(dict.fromkeys([*prior_block_entries, *(e for e in entries if e)]))
    block = "\n".join([begin_marker, *unique_entries, end_marker])

    if start_idx != -1 and end_idx != -1 and end_idx >= start_idx:
        before = existing[:start_idx].rstrip()
        after = existing[end_idx + len(end_marker) :].lstrip()
        parts = [p for p in (before, block, after) if p]
        next_text = "\n\n".join(parts) + "\n"
    else:
        trimmed = existing.rstrip()
        next_text = f"{trimmed}\n\n{block}\n" if trimmed else f"{block}\n"

    write_text_atomic(exclude_path, next_text)
    return {"updated": True, "path": str(exclude_path), "entries": unique_entries}


def _merge_json_file(path: Path, mutator: Any) -> bool:
    """Merge updates into a JSON object file; returns True if the file already existed."""
    existed = path.is_file()
    data = load_json(path, default={})
    if not isinstance(data, dict):
        data = {}
    mutator(data)
    save_json_atomic(path, data)
    return existed


def _resolve_bundled_path(pkg_root: Path, repo_rel: str, bundle_rel: str) -> Path:
    repo_cand = pkg_root / repo_rel
    if repo_cand.exists():
        return repo_cand
    return pkg_root / bundle_rel


def _link_or_copy_dir(src: Path, dest: Path, *, force_copy: bool = False) -> tuple[str, bool]:
    """Create a cross-platform directory link (absolute symlink / Windows junction) or copy fallback."""
    return link_or_copy_dir(src, dest, force_copy=force_copy)


def _detect_installed_harnesses(target: Path, *, home: Path | None = None) -> list[str]:
    """Detect harnesses installed globally or present in `target` across all 14 coding agents."""
    agents = detect_installed_agents(target, home=home)
    detected = [a["id"] for a in agents if a.get("detected")]
    return detected if detected else list(DEFAULT_FALLBACK_HARNESSES)


def _inspect_antigravity_plugin(home: Path | None = None) -> dict[str, Any]:
    return inspect_antigravity_plugin(home=home)


def attach_workspace(
    target_dir: str | Path,
    *,
    harness: str = "auto",
    vault: str = "global",
    copy_skills: bool = False,
    transport: str = "stdio",
    http_url: str = DEFAULT_HTTP_MCP_URL,
    home: str | Path | None = None,
    pkg_root: str | Path | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Non-destructively wire Syntopik into any existing repository without touching git status.

    Contract:
      - Supports all 14 coding-agent harnesses in both `stdio` and loopback Streamable-HTTP (`http`) modes.
      - NEVER overwrites an existing host `AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, or regular-file `.clinerules`.
      - NEVER bakes ephemeral `_npx`, `uv/archive-v0`, `uv-cache`, `pipx`, or temp install paths into configs.
      - Writes local exclusions strictly to `.git/info/exclude` (zero diff to host `.gitignore`).
      - Initializes `~/.syntopik` (`vault="global"`) or `<dir>/.syntopik` (`vault="local"`).
      - Uses 100% cross-platform Windows-safe commands (zero inline POSIX `VAR=... cmd`).
    """
    target = Path(target_dir).expanduser().resolve()
    if not dry_run:
        target.mkdir(parents=True, exist_ok=True)

    harness_low = str(harness or "auto").strip().lower()
    if harness_low not in ("auto", "all", *ALL_HARNESSES):
        raise ValueError(f"Unsupported harness {harness!r}; expected auto, all, or one of {ALL_HARNESSES}.")
    vault_low = str(vault or "global").strip().lower()
    if vault_low not in ("global", "local"):
        raise ValueError(f"Unsupported vault {vault!r}; expected 'global' or 'local'.")
    transport_low = str(transport or "stdio").strip().lower()
    if transport_low not in ("stdio", "http"):
        raise ValueError(f"Unsupported transport {transport!r}; expected 'stdio' or 'http'.")

    resolved_pkg_root = Path(pkg_root).expanduser().resolve() if pkg_root is not None else resolve_package_root()
    home_dir = Path(home).expanduser().resolve() if home is not None else Path.home().resolve()
    vault_path = (target / ".syntopik").resolve() if vault_low == "local" else (home_dir / ".syntopik").resolve()

    vault_init: dict[str, Any] = {"skipped": True, "dryRun": True}
    if not dry_run:
        setup = _ensure_script_import("setup_harness", resolve_package_root())
        vault_init = setup.ensure_learner_workspace(vault_path, run_sync=True, repair_skills=False)

    if harness_low == "all":
        selected = list(ALL_HARNESSES)
    elif harness_low == "auto":
        selected = _detect_installed_harnesses(target, home=home_dir)
    else:
        selected = [harness_low]

    ephemeral = is_ephemeral_install_root(resolved_pkg_root)
    effective_copy_skills = bool(copy_skills or ephemeral)
    skills_src = _resolve_bundled_path(resolved_pkg_root, ".agents/skills", "skills").resolve()

    exclude_entries: list[str] = [".syntopik/"]
    written: list[str] = []
    preserved: list[str] = []
    notes: list[str] = []

    # Strictly preserve any host AGENTS.md / CLAUDE.md / GEMINI.md
    for host_doc in ("AGENTS.md", "CLAUDE.md", "GEMINI.md"):
        if (target / host_doc).exists():
            preserved.append(host_doc)

    # Link or copy canonical `.agents/skills` (always copy when running from ephemeral cache!)
    if skills_src.is_dir():
        dest_skills = target / ".agents" / "skills"
        if dry_run:
            if dest_skills.exists() or dest_skills.is_symlink():
                preserved.append(".agents/skills")
            else:
                written.append(".agents/skills (dry-run)")
                exclude_entries.extend([".agents/skills", ".agents/skills/"])
        else:
            action, created = _link_or_copy_dir(skills_src, dest_skills, force_copy=effective_copy_skills)
            if created:
                written.append(f".agents/skills ({action})")
                exclude_entries.extend([".agents/skills", ".agents/skills/"])
            else:
                preserved.append(".agents/skills")

    # Avoid mutating real host global configs when auto-attaching a `/tmp` local-vault sandbox.
    write_global = (harness_low != "auto") or not (
        vault_low == "local" and is_ephemeral_install_root(target) and not is_ephemeral_install_root(home_dir)
    )

    antigravity_info: dict[str, Any] | None = None
    for hid in selected:
        h_res = attach_harness_config(
            hid,
            target,
            vault_dir=vault_path,
            pkg_root=resolved_pkg_root,
            transport=transport_low,
            http_url=http_url,
            dry_run=dry_run,
            home=home_dir,
            write_global=write_global,
            copy_skills=effective_copy_skills,
        )
        for item in h_res.get("written") or []:
            if item not in written:
                written.append(item)
        for item in h_res.get("preserved") or []:
            if item not in preserved:
                preserved.append(item)
        for item in h_res.get("excludeEntries") or []:
            if item not in exclude_entries:
                exclude_entries.append(item)
        for note in h_res.get("notes") or []:
            if note not in notes:
                notes.append(note)
        if hid == "antigravity" and isinstance(h_res.get("antigravity"), dict):
            antigravity_info = h_res["antigravity"]

    git_exclude = (
        {"updated": False, "path": None, "entries": exclude_entries}
        if dry_run
        else _update_git_exclude(target, exclude_entries)
    )
    res: dict[str, Any] = {
        "ok": True,
        "targetDir": str(target),
        "vaultMode": vault_low,
        "vaultPath": str(vault_path),
        "transport": transport_low,
        "httpUrl": http_url if transport_low == "http" else None,
        "ephemeralInstallRoot": ephemeral,
        "harnesses": selected,
        "written": written,
        "preserved": preserved,
        "notes": notes,
        "gitExclude": git_exclude,
        "vaultInit": vault_init,
    }
    if antigravity_info is not None:
        res["antigravity"] = antigravity_info
    return res


def _emit(payload: dict[str, Any], as_json: bool, lines: list[str]) -> None:
    if as_json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        for ln in lines:
            print(ln)


EMPTY_VAULT_HINT = (
    "  Your knowledge graph is empty, so there is nothing to work with yet.\n"
    "  Add your notes with `syntopik drop <file-or-url>`, or explore sample courses with `syntopik demo`."
)


def _vault_is_empty(kg_dir: Path) -> bool:
    """True when `propositions.jsonl` is missing or has no non-blank lines."""
    path = Path(kg_dir) / "propositions.jsonl"
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            return not any(line.strip() for line in fh)
    except OSError:
        return True


STARTED_VAULT_HINT = (
    "  You have added material but have not studied any of it yet.\n"
    "  Ask your AI agent to start the chapter, or run `syntopik next` to see the first step."
)


def _has_chapters(kg_dir: Path) -> bool:
    try:
        chapters = resolve_chapters_dir(Path(kg_dir).parent)
        return any(chapters.glob("*-tracker.json"))
    except OSError:
        return False


def _nothing_due_lines(kg_dir: Path, today: str) -> list[str]:
    """When the review queue is empty, say what is coming instead of stopping at a bare zero."""
    try:
        props = load_jsonl(kg_dir / "propositions.jsonl")
    except (OSError, ValueError):
        return []
    unstarted = 0
    upcoming: dict[str, int] = {}
    for p in props:
        if not isinstance(p, dict) or p.get("archived") is True:
            continue
        nxt = p.get("nextReview")
        if isinstance(nxt, str) and nxt[:10] > today:
            upcoming[nxt[:10]] = upcoming.get(nxt[:10], 0) + 1
        elif not isinstance(nxt, str) or not nxt.strip():
            unstarted += 1
    out: list[str] = []
    if upcoming:
        first = min(upcoming)
        out.append(f"  Next review: {first} ({upcoming[first]} item(s)).")
    if unstarted:
        out.append(f"  {unstarted} concept(s) not started yet. `syntopik review --tui` starts them as flashcards, or `syntopik next` picks a lesson.")
    return out


def _with_empty_hint(lines: list[str], kg_dir: Path) -> list[str]:
    if _vault_is_empty(kg_dir):
        hint = STARTED_VAULT_HINT if _has_chapters(kg_dir) else EMPTY_VAULT_HINT
        return [*lines, *hint.split("\n")]
    return lines


# ---------------------------------------------------------------------------
# Subcommand handlers
# ---------------------------------------------------------------------------


def _cmd_init(args: argparse.Namespace) -> int:
    vault_mode = getattr(args, "vault", None)
    if args.dir:
        ws = Path(args.dir).expanduser().resolve()
    elif getattr(args, "workspace", None):
        ws = Path(args.workspace).expanduser().resolve()
    elif vault_mode == "global":
        ws = (Path.home() / ".syntopik").resolve()
    elif vault_mode == "local":
        ws = (Path.cwd() / ".syntopik").resolve()
    else:
        ws = resolve_workspace()

    setup = _ensure_script_import("setup_harness", ws)
    boot = setup.ensure_learner_workspace(ws, kg_dir=getattr(args, "kg_dir", None), run_sync=True, repair_skills=True)
    payload = {"ok": True, "command": "init", "workspace": str(ws), "bootstrap": boot}
    _emit(
        payload,
        bool(args.json),
        [
            f"Syntopik initialized: {ws}",
            f"  Created {len(boot.get('createdDirs', []))} dir(s), {len(boot.get('createdFiles', []))} starter file(s) "
            f"(preserved {boot.get('existingPreservedCount', 0)} existing file(s)).",
        ],
    )
    return 0


def _cmd_attach(args: argparse.Namespace) -> int:
    res = attach_workspace(
        args.dir,
        harness=args.harness,
        vault=args.vault,
        copy_skills=bool(args.copy_skills),
        transport=getattr(args, "transport", "stdio"),
        http_url=getattr(args, "http_url", DEFAULT_HTTP_MCP_URL),
    )
    lines = [
        f"Syntopik attached to {res['targetDir']}",
        f"  Vault      : {res['vaultPath']} ({res['vaultMode']})",
        f"  Transport  : {res['transport']}" + (f" ({res['httpUrl']})" if res.get("httpUrl") else ""),
        f"  Harnesses  : {', '.join(res['harnesses'])}",
        f"  Written    : {len(res['written'])} adapter(s)",
    ]
    if res["preserved"]:
        lines.append(f"  Preserved  : {', '.join(res['preserved'])}")
    for note in res.get("notes") or []:
        lines.append(f"  Note       : {note}")
    if res["gitExclude"]["updated"]:
        lines.append(f"  Git Ignore : updated {res['gitExclude']['path']} (zero diff to .gitignore)")
    _emit(res, bool(args.json), lines)
    return 0


def _cmd_connect(args: argparse.Namespace) -> int:
    harness = str(args.harness).strip().lower()
    ws = resolve_workspace(getattr(args, "workspace", None))
    pkg_root = resolve_package_root()

    if harness == "antigravity":
        installer = pkg_root / "scripts" / "install_mcp_app.py"
        if not installer.is_file():
            raise SystemExit(f"Installer not found: {installer}")
        cmd = [
            sys.executable,
            "-B",
            str(installer),
            "install",
            "--bundle",
            args.bundle,
            "--workspace",
            str(ws),
        ]
        if args.target:
            cmd.extend(["--target", args.target])
        if args.json:
            cmd.append("--json")
        proc = subprocess.run(cmd, check=False)
        return int(proc.returncode)

    guides: dict[str, dict[str, Any]] = {
        "claude": {
            "harness": "claude",
            "command": "/plugin marketplace add HridayKapoor/syntopik && /plugin install syntopik",
            "note": "Installs all 17 skills, SEP-1865 Studio MCP App, and cache-safe hooks globally in Claude Code.",
        },
        "gemini": {
            "harness": "gemini",
            "command": "gemini extensions install https://github.com/HridayKapoor/syntopik",
            "note": "Installs gemini-extension.json with GEMINI.md context and syntopik-studio MCP.",
        },
        "cursor": {
            "harness": "cursor",
            "command": "npx skills add HridayKapoor/syntopik && syntopik attach . --harness cursor",
            "note": "Installs 17 skills + .cursor/rules/syntopik.mdc + .cursor/mcp.json.",
        },
        "windsurf": {
            "harness": "windsurf",
            "command": "npx skills add HridayKapoor/syntopik && syntopik attach . --harness windsurf",
            "note": "Installs 17 skills + .windsurf/rules/syntopik.md + global ~/.codeium/windsurf/mcp_config.json (HTTP uses serverUrl).",
        },
        "copilot": {
            "harness": "copilot",
            "command": "npx skills add HridayKapoor/syntopik && syntopik attach . --harness copilot",
            "note": "Installs 17 skills + .github/instructions/syntopik.instructions.md + .vscode/mcp.json (servers).",
        },
        "codex": {
            "harness": "codex",
            "command": "npx skills add HridayKapoor/syntopik && syntopik attach . --harness codex",
            "note": "Installs 17 skills + .codex/config.toml.",
        },
        "opencode": {
            "harness": "opencode",
            "command": "npx skills add HridayKapoor/syntopik && syntopik attach . --harness opencode",
            "note": "Installs 17 skills + opencode.json (mcp local/remote).",
        },
        "cline": {
            "harness": "cline",
            "command": "syntopik attach . --harness cline",
            "note": "Writes .clinerules/syntopik.md (preserves regular .clinerules file) + Cline MCP settings (HTTP uses type: streamableHttp).",
        },
        "roo": {
            "harness": "roo",
            "command": "syntopik attach . --harness roo",
            "note": "Writes .roo/rules/syntopik.md + .roo/mcp.json (HTTP uses type: streamable-http).",
        },
        "zed": {
            "harness": "zed",
            "command": "syntopik attach . --harness zed",
            "note": "Writes .zed/settings.json (context_servers) + .rules only when safe (never shadows existing host rules).",
        },
        "aider": {
            "harness": "aider",
            "command": "syntopik attach . --harness aider",
            "note": "Writes .agents/syntopik-rules.md and safely merges read: list in .aider.conf.yml.",
        },
        "continue": {
            "harness": "continue",
            "command": "syntopik attach . --harness continue",
            "note": "Writes .continue/rules/syntopik.md + .continue/mcpServers/syntopik.yaml.",
        },
        "kiro": {
            "harness": "kiro",
            "command": "syntopik attach . --harness kiro",
            "note": "Writes .kiro/steering/syntopik.md + .kiro/settings/mcp.json.",
        },
    }
    info = guides.get(harness)
    if info is None:
        raise SystemExit(f"Unsupported harness: {harness}")
    payload = {"ok": True, **info}
    _emit(payload, bool(args.json), [f"Connect {harness}:", f"  {info['command']}", f"  {info['note']}"])
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    from syntopik.server import serve_forever

    return serve_forever(
        workspace=getattr(args, "workspace", None),
        host=args.host,
        port=args.port,
        lan=bool(args.lan),
        open_browser=bool(args.open),
    )


def _cmd_import(args: argparse.Namespace) -> int:
    try:
        return _cmd_import_impl(args)
    except (ValueError, FileNotFoundError) as exc:
        msg = str(exc).splitlines()[0] if str(exc) else exc.__class__.__name__
        _emit({"ok": False, "error": msg}, bool(getattr(args, "json", False)), [f"Import failed: {msg}"])
        return 1


def _cmd_import_impl(args: argparse.Namespace) -> int:
    ws = resolve_workspace(getattr(args, "workspace", None))
    pocket_sync = getattr(args, "pocket_sync", None)
    if pocket_sync:
        from syntopik.pocket import import_pocket_sync

        res = import_pocket_sync(
            workspace=ws,
            sync_path=pocket_sync,
            kg_dir=resolve_kg_dir(ws),
            no_fire=bool(getattr(args, "no_fire", False)),
            no_sync=False,
            dry_run=bool(getattr(args, "dry_run", False)),
        )
        _emit(
            res,
            bool(args.json),
            [
                f"Pocket sync imported ({res.get('importedCount', 0)} new, {res.get('skippedDuplicates', 0)} duplicate(s))",
                f"  Workspace : {ws}",
            ],
        )
        return 0

    if getattr(args, "list_importers", False):
        from syntopik.importer_api import list_importers

        listing = list_importers(ws)
        lines = [f"Importer plugins (API v{listing['apiVersion']}):"]
        lines += [f"  {p['name']:<14} {p['description']}  [{p['source']}]" for p in listing["plugins"]] or ["  (none)"]
        lines.append(f"  Built-in kinds: {', '.join(listing['builtinKinds'])}")
        state = "on" if listing["localImportersEnabled"] else "off (set allowLocalImporters: true in study-config.json)"
        lines.append(f"  Local plugins in {listing['localImportersDir']}: {state}")
        lines += [f"  ! {e['source']}: {e['error']}" for e in listing["errors"]]
        _emit(listing, bool(args.json), lines)
        return 0

    if not args.target:
        raise SystemExit("Provide a source target OR pass --pocket-sync <pocket-sync.json> (or --list-importers).")

    kind = str(args.kind or "auto").strip().lower()
    dry_run = bool(getattr(args, "dry_run", False))
    kg_dir = resolve_kg_dir(ws)
    builtin_kinds = ("auto", "youtube", "arxiv", "repo", "url", "notes", "anki", "cards", "notebook", "notes_batch")

    if kind not in builtin_kinds:
        from syntopik.importer_api import run_plugin_import

        res = run_plugin_import(
            kind,
            args.target,
            workspace=ws,
            kg_dir=kg_dir,
            subject=args.subject,
            chapter=args.slug,
            limit=getattr(args, "limit", None),
            dry_run=dry_run,
        )
        if res.get("ok") and not dry_run:
            sync_hub(workspace=ws, quiet=True)
        _emit(
            res,
            bool(args.json),
            [
                f"Imported ({kind}): {res.get('importedCount', 0)} new, {res.get('deduplicatedCount', 0)} merged into existing"
                if res.get("ok")
                else f"Import failed ({kind}): {res.get('error')}",
                f"  Output : {res.get('propsFile') or ws}",
            ],
        )
        return 0 if res.get("ok") else 1

    if kind in ("anki", "cards", "notebook", "notes_batch"):
        importers = _lazy_import("syntopik.importers")
        imp_dispatch = {
            "anki": importers.import_anki_apkg,
            "cards": importers.import_delimited_cards,
            "notebook": importers.import_jupyter_notebook,
            "notes_batch": importers.import_markdown_folder,
        }
        imp_fn = imp_dispatch[kind]
        sig = inspect.signature(imp_fn)
        imp_kwargs: dict[str, Any] = {}
        candidate_kwargs: dict[str, Any] = {
            "workspace": ws,
            "kg_dir": kg_dir,
            "subject": args.subject,
            "chapter": args.slug,
            "slug": args.slug,
            "dry_run": dry_run,
        }
        for k, v in candidate_kwargs.items():
            if k in sig.parameters:
                imp_kwargs[k] = v
        res = imp_fn(args.target, **imp_kwargs)
        if not dry_run and (not isinstance(res, dict) or res.get("ok", True)):
            sync_hub(workspace=ws, quiet=True)
        ok_flag = bool(res.get("ok", True)) if isinstance(res, dict) else True
        out_ref = (
            res.get("outputDir")
            or res.get("propsFile")
            or res.get("notebookPath")
            or res.get("indexPath")
            or res.get("outputPath")
            or res.get("outDir")
            or res.get("error")
            or ws
        ) if isinstance(res, dict) else ws
        _emit(
            res if isinstance(res, dict) else {"ok": ok_flag, "kind": kind, "result": res},
            bool(args.json),
            [
                f"Imported ({(res.get('kind') if isinstance(res, dict) else None) or kind}): {(res.get('title') if isinstance(res, dict) else None) or args.target}",
                *_import_count_lines(res),
                f"  Output : {out_ref}",
            ],
        )
        return 0 if ok_flag else 1

    from syntopik import ingest

    kwargs: dict[str, Any] = {
        "workspace": ws,
        "slug": args.slug,
        "subject": args.subject,
        "register_tracker": bool(args.register_tracker),
    }
    dispatch = {
        "auto": ingest.ingest_auto,
        "youtube": ingest.ingest_youtube,
        "arxiv": ingest.ingest_arxiv,
        "repo": ingest.ingest_repo,
        "url": ingest.ingest_url,
        "notes": ingest.ingest_notes,
    }
    fn = dispatch[kind]
    sig = inspect.signature(fn)
    if "dry_run" in sig.parameters:
        kwargs["dry_run"] = dry_run
    res = fn(args.target, **kwargs)
    if not dry_run and (not isinstance(res, dict) or res.get("ok", True)):
        sync_hub(workspace=ws, quiet=True)
    ok_flag = bool(res.get("ok", True)) if isinstance(res, dict) else True
    _emit(
        res if isinstance(res, dict) else {"ok": ok_flag, "kind": kind, "result": res},
        bool(args.json),
        [
            f"Imported ({(res.get('kind') if isinstance(res, dict) else None) or kind}): {(res.get('title') if isinstance(res, dict) else None) or args.target}",
            *_import_count_lines(res),
            f"  Output : {(res.get('outputPath') or res.get('outDir') or ws) if isinstance(res, dict) else ws}",
        ],
    )
    return 0 if ok_flag else 1


def _import_count_lines(res: Any) -> list[str]:
    """Say how many items an import added, so a re-import of the same file is not a mystery."""
    if not isinstance(res, dict) or "importedCount" not in res:
        return []
    new = int(res.get("importedCount") or 0)
    merged = int(res.get("deduplicatedCount") or 0)
    skipped = int(res.get("skippedCount") or 0)
    parts = [f"{new} new"]
    if merged:
        parts.append(f"{merged} already there (merged)")
    if skipped:
        parts.append(f"{skipped} skipped")
    lines = [f"  Items  : {', '.join(parts)}"]
    if res.get("dryRun"):
        lines.append("  Dry run: nothing was saved.")
    return lines


def _run_srs_json(ws: Path, pkg_root: Path, srs_args: list[str]) -> tuple[int, dict[str, Any] | None, str]:
    srs_script = pkg_root / "scripts" / "srs_engine.py"
    py_bin = _find_python_for_workspace(ws, pkg_root)
    cmd = [py_bin, "-B", str(srs_script), *srs_args]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        return int(proc.returncode), None, (proc.stderr or proc.stdout).strip()
    raw = proc.stdout.strip()
    if not raw:
        return 0, {}, ""
    try:
        return 0, json.loads(raw), ""
    except json.JSONDecodeError:
        return 0, {"raw": raw}, ""


def _unstarted_items(kg_dir: Path, limit: int, exclude: set[str]) -> list[dict[str, Any]]:
    """Concepts never reviewed yet (no `nextReview`), in the order they were added, like new cards in Anki."""
    if limit <= 0:
        return []
    out: list[dict[str, Any]] = []
    for p in load_jsonl(kg_dir / "propositions.jsonl"):
        if not isinstance(p, dict) or p.get("archived") is True or p.get("suspended"):
            continue
        pid = str(p.get("id") or "").strip()
        nxt = p.get("nextReview")
        if not pid or pid in exclude or (isinstance(nxt, str) and nxt.strip()):
            continue
        if not (p.get("cues") or p.get("statement") or p.get("prompt")):
            continue
        out.append({"id": pid, "title": p.get("title"), "chapter": p.get("chapter"), "status": "new"})
        if len(out) >= limit:
            break
    return out


def _run_review_tui(
    ws: Path, kg_dir: Path, payload: dict[str, Any], *, energy: str | None = None, new_limit: int = 0
) -> int:
    items = list(payload.get("items") or [])
    if energy == "low":
        items = items[:3]
        new_limit = min(new_limit, 3)
    new_items = _unstarted_items(kg_dir, new_limit, {str(it.get("id")) for it in items})
    items.extend(new_items)
    if not items:
        print(f"Syntopik Review TUI ({ws}): 0 items due.")
        return 0

    props_by_id = {
        str(p.get("id")): p
        for p in load_jsonl(kg_dir / "propositions.jsonl")
        if isinstance(p, dict) and p.get("id")
    }
    srs_mod = _ensure_script_import("srs_engine", ws)

    head = f"{len(items) - len(new_items)} due"
    if new_items:
        head += f" + {len(new_items)} new"
    print(f"Syntopik Review TUI — {head} (Ctrl-C or 'q' to stop)")
    reviewed = 0
    rating_map = {
        "1": "again",
        "a": "again",
        "again": "again",
        "2": "hard",
        "h": "hard",
        "hard": "hard",
        "3": "good",
        "g": "good",
        "good": "good",
        "4": "easy",
        "e": "easy",
        "easy": "easy",
    }

    tally: dict[str, int] = {}
    # Cards rated "again" come back once at the end of the session, like Anki's learning steps:
    # a second retrieval a few minutes later is what makes a missed card stick.
    queue = list(items)
    retried = 0
    pos = 0
    while pos < len(queue):
        item = queue[pos]
        pos += 1
        is_retry = bool(item.get("_retry"))
        idx = pos
        pid = str(item.get("id") or "")
        prop = props_by_id.get(pid) or {}
        cues = [c for c in (prop.get("cues") or []) if isinstance(c, dict) and (c.get("q") or c.get("a"))]
        cue = cues[0] if cues else None
        prompt_text = str((cue or {}).get("q") or prop.get("prompt") or item.get("title") or pid)
        answer_text = str((cue or {}).get("a") or prop.get("statement") or item.get("title") or "")
        notes_text = str(item.get("notes") or prop.get("notes") or "")

        print("")
        if is_retry:
            print(f"[second look] {pid} (missed earlier this session)")
        else:
            print(f"[{idx}/{len(items)}] {pid} ({item.get('chapter') or 'General'} · {item.get('status') or 'due'})")
        print(f"  Prompt: {prompt_text}")
        shown_at = time.monotonic()
        try:
            reveal = input("  Press Enter to reveal (or 'q' to quit): ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("")
            break
        if reveal in ("q", "quit"):
            break

        print(f"  Answer: {answer_text}")
        if notes_text:
            print(f"  Notes : {notes_text}")

        rating: str | None = None
        parts: list[str] = []
        stop = False
        for _attempt in range(3):
            try:
                raw_rate = input("  Rate [1=again, 2=hard, 3=good, 4=easy, s=skip, q=quit] (+ optional confidence %): ").strip()
            except (EOFError, KeyboardInterrupt):
                print("")
                stop = True
                break
            if not raw_rate or raw_rate.lower() in ("s", "skip"):
                break
            if raw_rate.lower() in ("q", "quit"):
                stop = True
                break
            parts = raw_rate.split()
            rating = rating_map.get(parts[0].lower())
            if rating:
                break
            print(f"  {parts[0]!r} is not a rating. Type 1, 2, 3 or 4 (or s to skip).")
        if stop:
            break
        if not rating:
            continue
        conf: float | None = None
        if len(parts) >= 2:
            try:
                # The prompt asks for a percentage, so "3 1" means 1%, not 100%.
                conf = max(0.0, min(100.0, float(parts[1].rstrip("%")))) / 100.0
            except ValueError:
                conf = None
        # Time from showing the prompt to rating it, capped so a card left open over lunch
        # does not count as hours of study.
        duration_ms = int(min(time.monotonic() - shown_at, 600.0) * 1000)

        rec = srs_mod.record_review(
            kg_dir,
            pid,
            rating,
            cue_id=(cue or {}).get("id"),
            confidence=conf,
            duration_ms=duration_ms,
            no_sync=True,
        )
        reviewed += 1
        if not is_retry:
            tally[rating] = tally.get(rating, 0) + 1
        days = (rec or {}).get("intervalDays") if isinstance(rec, dict) else None
        when = ""
        if isinstance(days, int) and days > 0:
            when = " Next review tomorrow." if days == 1 else f" Next review in {days} days."
        print(f"  Recorded '{rating}' for {pid}.{when}")
        if is_retry:
            retried += 1
            reviewed -= 1  # the summary counts distinct cards
        elif rating == "again":
            queue.append({**item, "_retry": True})
            print("  It comes back once more at the end of this session.")

    if reviewed > 0:
        sync_hub(workspace=ws, quiet=True)
    print(f"Review TUI complete: {reviewed}/{len(items)} card(s) recorded.")
    if retried:
        print(f"  {retried} missed card(s) got a second look.")
    if tally:
        order = ("again", "hard", "good", "easy")
        print("  " + " · ".join(f"{tally[k]} {k}" for k in order if tally.get(k)))
    left = len(items) - reviewed
    if left > 0:
        print(f"  {left} card(s) left for now. Run `syntopik review --tui` again whenever you have a few minutes.")
    return 0


def _cmd_review(args: argparse.Namespace) -> int:
    ws = resolve_workspace(getattr(args, "workspace", None))
    kg_dir = resolve_kg_dir(ws)
    chapters_dir = resolve_chapters_dir(ws)
    pkg_root = resolve_package_root()

    if args.web:
        from syntopik.server import serve_forever

        return serve_forever(workspace=ws, port=args.port, open_browser=True)

    if args.recover:
        rec_args = [
            "recover",
            "--kg-dir",
            str(kg_dir),
            "--daily-cap",
            str(max(1, int(args.daily_cap))),
            "--days",
            str(max(1, int(args.days))),
        ]
        if getattr(args, "apply", False):
            rec_args.append("--apply")
        rc, data, err = _run_srs_json(ws, pkg_root, rec_args)
        if rc != 0 or data is None:
            print(err or "Backlog recovery failed.", file=sys.stderr)
            return rc or 1
        payload = {"ok": True, "mode": "recover", "workspace": str(ws), **data}
        lines = [
            f"Calm Backlog Recovery ({ws})",
            f"  Overdue       : {data.get('overdueCount', data.get('totalOverdue', 0))} item(s)",
            f"  Daily cap     : {args.daily_cap}/day over {args.days} day(s)",
            f"  Rescheduled   : {len(data.get('rescheduleMap') or {})} item(s) (applied={bool(data.get('applied'))})",
        ]
        _emit(payload, bool(args.json), lines)
        return 0

    if (args.resume or args.energy is not None) and not args.tui:
        daily_plan = _ensure_script_import("daily_plan", ws)
        plan = daily_plan.build_daily_plan(
            kg_dir=kg_dir,
            chapters_dir=chapters_dir,
            energy=args.energy or "normal",
            resume=bool(args.resume),
        )
        if args.json:
            print(json.dumps({"ok": True, "workspace": str(ws), **plan}, indent=2, ensure_ascii=False))
        else:
            print(daily_plan._render_human_plan(plan))
        return 0

    due_args = ["due", "--kg-dir", str(kg_dir)]
    if args.compress:
        due_args.append("--compress")
    if args.bury_siblings:
        due_args.append("--bury-siblings")
    effective_limit = 3 if args.energy == "low" else int(args.limit)
    if effective_limit:
        due_args.extend(["--limit", str(effective_limit)])

    rc, data, err = _run_srs_json(ws, pkg_root, due_args)
    if rc != 0 or data is None:
        print(err or "Failed to load due queue.", file=sys.stderr)
        return rc or 1

    if args.tui:
        if args.json:
            print(json.dumps({"ok": True, "mode": "tui", "workspace": str(ws), **data}, indent=2, ensure_ascii=False))
            return 0
        return _run_review_tui(ws, kg_dir, data, energy=args.energy, new_limit=int(args.new))

    if args.json:
        print(json.dumps({"ok": True, "workspace": str(ws), **data}, indent=2, ensure_ascii=False))
        return 0

    items = data.get("items") or []
    lines = [
        f"Syntopik Due Review ({data.get('today', '')}) — {data.get('dueCount', len(items))} item(s)"
        + (f" ({data.get('knockedOutCount')} FIRe knockout(s))" if data.get("knockedOutCount") else ""),
    ]
    for it in items[:25]:
        retr = f" · R≈{round(float(it['retrievability']) * 100)}%" if it.get("retrievability") is not None else ""
        lines.append(f"  - {it.get('id')} [{it.get('status')}{retr}]: {it.get('title') or it.get('id')}")
    if not items:
        lines.append("  Nothing due right now.")
        lines.extend(_nothing_due_lines(kg_dir, str(data.get("today") or "")[:10] or datetime.now(timezone.utc).date().isoformat()))
        lines = _with_empty_hint(lines, kg_dir)
    else:
        due_total = int(data.get("dueCount") or len(items))
        if due_total > len(items[:25]):
            lines.append(f"  ... and {due_total - len(items[:25])} more.")
        lines.append("  Start with `syntopik review --tui`, or ask your AI assistant to quiz you on these.")
        if due_total > 60:
            lines.append("  Fell behind? `syntopik review --recover` spreads the backlog calmly over the next days.")
    for ln in lines:
        print(ln)
    return 0


def _short(text: str, limit: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: max(1, limit - 1)].rstrip() + "…"


def _triage_plan_lines(plan: dict[str, Any], max_blocks: int = 14) -> list[str]:
    """Plain-language block-by-block plan: what to study in each block, and what to skip."""
    buckets = plan.get("buckets") or {}
    if not buckets.get("salvage_70pct") and not buckets.get("protect_20pct"):
        return []
    labels = {"salvage_70pct": "Fix", "protect_20pct": "Refresh", "anti_mastery_crib": "Crib sheet"}
    out = ["", "Plan (one block at a time, stop when the time is up):"]
    blocks = list(plan.get("blocks") or [])
    for blk in blocks[:max_blocks]:
        goal = str(blk.get("goal") or "")
        label = labels.get(str(blk.get("bucket")), "Study")
        if goal.startswith("Closed-book re-test"):
            label = "Re-test"
        titles = [_short(str(t.get("title") or t.get("propId") or ""), 48) for t in blk.get("targets") or []]
        if titles:
            what = "; ".join(titles[:2]) + (f" (+{len(titles) - 2} more)" if len(titles) > 2 else "")
        elif blk.get("bucket") == "anti_mastery_crib":
            what = "write a 1-page sheet of your traps" if goal.startswith("Compile") else "walk through your traps, then rest"
        else:
            what = _short(goal, 80)
        out.append(f"  {int(blk.get('order') or 0):>2}. {int(blk.get('minutes') or 0):>3}m  {label}: {what}")
    if len(blocks) > max_blocks:
        out.append(f"  ... and {len(blocks) - max_blocks} more block(s).")
    cut = list(buckets.get("explicitly_cut") or [])
    if cut:
        names = ", ".join(_short(str(c.get("title") or c.get("propId")), 40) for c in cut[:3])
        more = f" (+{len(cut) - 3} more)" if len(cut) > 3 else ""
        out.append(f"  Skip for now, guilt-free: {names}{more}.")
    return out


def _cmd_triage(args: argparse.Namespace) -> int:
    ws = resolve_workspace(getattr(args, "workspace", None))
    kg_dir = resolve_kg_dir(ws)
    chapters_dir = resolve_chapters_dir(ws)

    raw_duration = args.exam_in if args.exam_in is not None else args.hours
    try:
        hours_left = _parse_duration_hours(raw_duration, default=48.0)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    daily_plan = _ensure_script_import("daily_plan", ws)
    plan = daily_plan.build_triage_plan(
        hours_left=hours_left,
        sleep_hours=float(args.sleep_hours),
        kg_dir=kg_dir,
        chapters_dir=chapters_dir,
        chapter=args.chapter,
    )
    payload: dict[str, Any] = {"ok": True, "triage": plan}

    if args.crib_sheet:
        cs = _ensure_script_import("cheatsheet_pdf", ws)
        spec = cs.build_anti_mastery_spec(
            kg_dir=kg_dir,
            chapter=args.chapter,
            anti_mastery=True,
            max_pages=1,
        )
        payload["antiMasterySpec"] = spec
        if args.out:
            out_path = Path(args.out).expanduser().resolve()
            save_json_atomic(out_path.with_suffix(".spec.json"), spec)
            payload["specOut"] = str(out_path.with_suffix(".spec.json"))

    lines = [
        f"Exam Triage ({plan.get('hoursLeft')}h left · usable {plan.get('usableStudyHours')}h · sleep protected {plan.get('sleepProtectedHours')}h)",
        f"  Salvage (70%) : {len((plan.get('buckets') or {}).get('salvage_70pct', []))} node(s)",
        f"  Protect (20%) : {len((plan.get('buckets') or {}).get('protect_20pct', []))} node(s)",
        f"  Explicitly Cut: {len((plan.get('buckets') or {}).get('explicitly_cut', []))} node(s)",
    ]
    lines.extend(_triage_plan_lines(plan))
    _emit(payload, bool(args.json), _with_empty_hint(lines, kg_dir))
    return 0


def _cmd_pack(args: argparse.Namespace) -> int:
    from syntopik import packs

    rest = list(args.pack_args or [])
    if rest and rest[0] == "--":
        rest = rest[1:]
    try:
        rc = packs.main(rest)
        return int(rc) if isinstance(rc, int) else 0
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else 0


def _cmd_doctor(args: argparse.Namespace) -> int:
    ws = resolve_workspace(getattr(args, "workspace", None)) if getattr(args, "workspace", None) else None

    if getattr(args, "compat", False):
        proj_dir = ws if ws is not None else Path.cwd()
        matrix = get_compat_matrix(project_dir=proj_dir)
        if args.json:
            print(json.dumps(matrix, indent=2, ensure_ascii=False))
        else:
            print(format_compat_table(matrix))
        return 0

    fix_actions: list[str] = []

    if getattr(args, "fix", False):
        target_ws = ws if ws is not None else resolve_workspace()
        setup = _ensure_script_import("setup_harness", target_ws)
        boot = setup.ensure_learner_workspace(target_ws, run_sync=True, repair_skills=True)
        from syntopik.ledger_check import repair_ledgers

        lrep = repair_ledgers(Path(boot.get("kgDir") or resolve_kg_dir(target_ws)))
        if lrep["quarantinedLines"]:
            fix_actions.append(f"quarantined {lrep['quarantinedLines']} corrupted ledger line(s) to knowledge-graph/.quarantine/")
        if boot.get("createdDirs"):
            fix_actions.append(f"created {len(boot['createdDirs'])} dir(s)")
        if boot.get("createdFiles"):
            fix_actions.append(f"created {len(boot['createdFiles'])} missing ledger/starter file(s)")
        if (target_ws / ".agents" / "skills").is_dir() and (target_ws / ".claude").is_dir():
            rep = setup.repair_claude_skills_link(target_ws, create_if_missing=True)
            fix_actions.append(f".claude/skills: {rep.get('action', 'checked')}")
        if sync_hub(workspace=target_ws, quiet=True):
            fix_actions.append("synced knowledge-graph/graph-data.js")
        ws = target_ws

    doctor = _ensure_script_import("study_doctor", ws)
    core_only = bool(args.core_only)
    auto_core = False
    if not core_only and not getattr(args, "full", False) and not _stem_extras_installed():
        # Without the optional maths/PDF/chemistry/video extras, the full check would list
        # ~24 missing packages as failures; the core install is what matters here.
        core_only = auto_core = True
    report = doctor.run_doctor(workspace=ws, core_only=core_only)
    if auto_core:
        report["autoCoreOnly"] = True
    if getattr(args, "fix", False):
        report["fixApplied"] = True
        report["fixActions"] = fix_actions

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        if fix_actions:
            print(f"Syntopik Doctor --fix: {', '.join(fix_actions)}")
        s = report["summary"]
        banner = "HEALTHY" if report["healthy"] else "ISSUES DETECTED"
        print(f"Syntopik Doctor — {banner} ({s['ok']}/{s['total']} ok, {s['warn']} warn, {s['fail']} fail · {report['elapsedMs']} ms)")
        print("-" * 88)
        for c in report["checks"]:
            tag = {"ok": "[ OK ]", "warn": "[WARN]", "fail": "[FAIL]"}[c["status"]]
            print(f"{tag} {c['name']:<36} {c['detail']}")
        print("-" * 88)
        if auto_core:
            print("Checked the core install. The optional maths, PDF, chemistry and video extras are not installed,")
            print("which is fine unless you need them. `syntopik doctor --full` lists every optional extra.")
    return 0 if report.get("healthy") else 1


STEM_EXTRA_SENTINELS = ("sympy", "pymupdf", "matplotlib", "rdkit", "manim", "scipy")


def _stem_extras_installed() -> bool:
    """True when any of the optional STEM extras is installed in this interpreter."""
    import importlib.metadata as md

    for dist in STEM_EXTRA_SENTINELS:
        try:
            md.version(dist)
            return True
        except md.PackageNotFoundError:
            continue
    return False


def _build_json_export(ws: Path, kg_dir: Path, chapters_dir: Path, chapter: str | None = None) -> dict[str, Any]:
    ch_filter = str(chapter or "").strip().lower()

    def _matches_ch(row: dict[str, Any]) -> bool:
        if not ch_filter:
            return True
        return (
            ch_filter in str(row.get("chapter") or "").lower()
            or ch_filter in str(row.get("id") or "").lower()
            or ch_filter in str(row.get("subject") or "").lower()
        )

    props = [p for p in load_jsonl(kg_dir / "propositions.jsonl") if _matches_ch(p)]
    prop_ids = {str(p.get("id")) for p in props if p.get("id")}
    all_chains = load_jsonl(kg_dir / "chains.jsonl")
    if ch_filter:
        chains = [
            c
            for c in all_chains
            if str(c.get("conclusion") or c.get("to") or "") in prop_ids
            or any(str(pr) in prop_ids for pr in (c.get("premises") or []))
        ]
    else:
        chains = all_chains

    mistakes = [m for m in load_jsonl(kg_dir / "mistakes.jsonl") if _matches_ch(m)]
    revlog = load_jsonl(kg_dir / "revlog.jsonl")
    if ch_filter:
        revlog = [r for r in revlog if str(r.get("propId") or "") in prop_ids]
    reading_queue = load_jsonl(kg_dir / "reading-queue.jsonl")
    forks = load_jsonl(kg_dir / "forks.jsonl")
    arenas = load_jsonl(kg_dir / "arenas.jsonl")
    decisions = load_jsonl(kg_dir / "decisions.jsonl")
    derivations = load_jsonl(kg_dir / "derivations.jsonl")

    trackers: list[dict[str, Any]] = []
    if chapters_dir.is_dir():
        for tf in sorted(chapters_dir.glob("*-tracker.json")):
            obj = load_json(tf, default=None)
            if isinstance(obj, dict):
                if not ch_filter or ch_filter in str(obj.get("slug") or "").lower() or ch_filter in str(obj.get("title") or "").lower():
                    trackers.append(obj)

    return {
        "ok": True,
        "format": "json",
        "schemaVersion": 3,
        "syntopikVersion": __version__,
        "exportedAt": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "workspace": str(ws),
        "chapterFilter": chapter or None,
        "counts": {
            "propositions": len(props),
            "chains": len(chains),
            "mistakes": len(mistakes),
            "revlog": len(revlog),
            "readingQueue": len(reading_queue),
            "forks": len(forks),
            "arenas": len(arenas),
            "decisions": len(decisions),
            "derivations": len(derivations),
            "trackers": len(trackers),
        },
        "propositions": props,
        "chains": chains,
        "mistakes": mistakes,
        "revlog": revlog,
        "readingQueue": reading_queue,
        "forks": forks,
        "arenas": arenas,
        "decisions": decisions,
        "derivations": derivations,
        "chapterTrackers": trackers,
    }


def _cmd_export(args: argparse.Namespace) -> int:
    ws = resolve_workspace(getattr(args, "workspace", None))
    kg_dir = resolve_kg_dir(ws)
    chapters_dir = resolve_chapters_dir(ws)

    raw_fmt = args.format
    if getattr(args, "pocket", False) and not raw_fmt:
        fmt = "pocket"
    elif raw_fmt:
        fmt = str(raw_fmt).strip().lower()
    else:
        raise SystemExit("Specify an export format (obsidian|pocket|anki|anki-text|pack|json|dossier) or pass --pocket.")

    if fmt == "obsidian":
        from syntopik.obsidian import export_vault

        out_dir = Path(args.out).expanduser().resolve() if args.out else (ws / "exports" / "obsidian-vault").resolve()
        res = export_vault(out_dir, workspace=ws, kg_dir=kg_dir)
        _emit(res, bool(args.json), [f"Exported Obsidian vault -> {out_dir}"])
        return 0

    if fmt == "pocket":
        from syntopik.pocket import export_pocket_html

        res = export_pocket_html(
            workspace=ws,
            out_path=args.out,
            limit=int(args.limit),
            chapter=args.chapter,
            kg_dir=kg_dir,
        )
        _emit(res, bool(args.json), [f"Exported Pocket deck ({res.get('cardCount')} cards) -> {res.get('outPath')}"])
        return 0

    if fmt == "pack":
        from syntopik.packs import export_pack

        target = args.target or args.chapter or "all"
        res = export_pack(
            target,
            workspace=ws,
            kg_dir=kg_dir,
            chapters_dir=chapters_dir,
            out_path=args.out,
        )
        _emit(res, bool(args.json), [f"Exported Study Pack -> {res.get('outPath')}"])
        return 0

    if fmt == "json":
        snapshot = _build_json_export(ws, kg_dir, chapters_dir, chapter=args.chapter)
        if args.out:
            out_path = Path(args.out).expanduser().resolve()
            save_json_atomic(out_path, snapshot)
            res = {
                "ok": True,
                "format": "json",
                "outPath": str(out_path),
                "counts": snapshot["counts"],
            }
            _emit(res, bool(args.json), [f"Exported Knowledge Graph JSON -> {out_path}"])
        else:
            print(json.dumps(snapshot, indent=2, ensure_ascii=False))
        return 0

    if fmt == "dossier":
        evidence = _lazy_import("syntopik.evidence")
        out_path = Path(args.out).expanduser().resolve() if args.out else (ws / "exports" / "mastery-dossier.html").resolve()
        res = evidence.export_mastery_dossier(ws, out_path)
        _emit(res, bool(args.json), [f"Exported Mastery Dossier -> {res.get('outPath', out_path)}"])
        return 0

    if fmt == "anki":
        pkg_root = resolve_package_root()
        srs_script = pkg_root / "scripts" / "srs_engine.py"
        py_bin = _find_python_for_workspace(ws, pkg_root)
        has_genanki = subprocess.run(
            [py_bin, "-c", "import genanki"], capture_output=True, check=False
        ).returncode == 0
        if not has_genanki:
            fmt = "anki-text"
            if not args.json:
                print("The optional Anki package builder (genanki) is not installed, so this writes a text file")
                print("that Anki imports directly instead (Anki: File > Import). Picture-hiding cards need the full .apkg.")

    if fmt == "anki-text":
        srs = _ensure_script_import("srs_engine", ws)
        if args.out:
            out_path = Path(args.out).expanduser().resolve()
            if out_path.suffix.lower() == ".apkg":
                out_path = out_path.with_suffix(".txt")
        else:
            out_path = (ws / "exports" / "syntopik-anki.txt").resolve()
        res = srs.export_anki_text(kg_dir, out_path, subject=getattr(args, "subject", None))
        _emit(
            {"ok": True, **res},
            bool(args.json),
            [f"Exported {res['exportedCards']} Anki card(s) -> {res['output']}", "  In Anki: File > Import, pick this file, then Import."],
        )
        return 0

    if fmt == "anki":
        cmd = [py_bin, "-B", str(srs_script), "export-anki", "--kg-dir", str(kg_dir)]
        if args.out:
            cmd.extend(["--out", str(Path(args.out).expanduser().resolve())])
        if getattr(args, "subject", None):
            cmd.extend(["--subject", args.subject])
        proc = subprocess.run(cmd, check=False)
        return int(proc.returncode)

    raise SystemExit(f"Unknown export format: {fmt}")


def _cmd_sync(args: argparse.Namespace) -> int:
    ws = resolve_workspace(getattr(args, "workspace", None))
    ok = sync_hub(workspace=ws, quiet=True)
    payload = {"ok": ok, "workspace": str(ws), "out": str(resolve_kg_dir(ws) / "graph-data.js")}
    if args.json:
        print(json.dumps(payload, indent=2))
    elif ok:
        print(f"Synced knowledge-graph/graph-data.js for {ws}")
    else:
        print("Failed to sync graph-data.js (Node.js or scripts/sync-graph.mjs missing)", file=sys.stderr)
    return 0 if ok else 1


def _cmd_run(args: argparse.Namespace) -> int:
    ws = resolve_workspace(getattr(args, "workspace", None))
    pkg_root = resolve_package_root()
    bases = (ws / "scripts", pkg_root / "scripts")
    available = sorted(
        {
            p.stem
            for base in bases
            if base.is_dir()
            for p in base.iterdir()
            if p.is_file() and p.suffix in (".py", ".mjs") and not p.name.startswith("_")
        }
    )
    engine = str(args.engine or "").strip()
    if not engine:
        print("Usage: syntopik run <engine> [args...]   (add --help after the engine for its options)")
        print("Available engines:")
        for name in available:
            print(f"  {name}")
        return 0
    if "/" in engine or "\\" in engine or ".." in engine:
        raise SystemExit(f"Invalid engine name: {engine!r}")

    candidates_names = [engine]
    if not engine.endswith((".py", ".mjs")):
        candidates_names.extend([f"{engine}.py", f"{engine}.mjs"])

    script_path: Path | None = None
    for base in bases:
        for name in candidates_names:
            cand = base / name
            if cand.is_file():
                script_path = cand
                break
        if script_path is not None:
            break

    if script_path is None:
        close = difflib.get_close_matches(engine.rsplit(".", 1)[0], available, n=3, cutoff=0.6)
        hint = f" Did you mean: {', '.join(close)}?" if close else " Run `syntopik run` to list engines."
        raise SystemExit(f"Engine script '{engine}' not found in workspace or bundled scripts/.{hint}")

    rest = list(args.engine_args or [])
    if rest and rest[0] == "--":
        rest = rest[1:]

    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.setdefault("SYNT_WORKSPACE", str(ws))

    if script_path.suffix == ".mjs":
        node_bin = find_node()
        if not node_bin:
            raise SystemExit("Node.js not found on PATH.")
        proc = subprocess.run([node_bin, str(script_path), *rest], env=env, check=False)
        return int(proc.returncode)

    py_bin = _find_python_for_workspace(ws, pkg_root)
    proc = subprocess.run([py_bin, "-B", str(script_path), *rest], env=env, check=False)
    return int(proc.returncode)


# ---------------------------------------------------------------------------
# v3.0 First-Class Verbs (lazy imports for parallel Wave 1A/1B/1D engines)
# ---------------------------------------------------------------------------


# What each Brainstem move means for the learner, and how they start it. `--json` keeps the
# machine fields (recommendedMode, exactCmd, skillHint) for AI assistants.
_NEXT_MOVE_PLAIN: dict[str, tuple[str, str]] = {
    "whiteboard_pin_answer": ("Get an answer to the question you pinned on the whiteboard", 'Ask your AI assistant: "answer my pinned question".'),
    "onboarding_calibration": ("Answer 4 quick questions so lessons fit how you like to learn (optional)", "`syntopik profile calibrate`"),
    "cold_start_reentry": ("Welcome back: a short, easy catch-up review", "`syntopik review --recover --daily-cap 15`"),
    "calm_wrapup": ("You seem tired: one light review, then stop for today", "`syntopik review --energy low --tui`"),
    "bridge_worked_example": ("Step back to a fully worked example of {title}", 'Ask your AI assistant: "show me a worked example of {title}".'),
    "causal_probe": ("Check the idea underneath first: {title}", 'Ask your AI assistant: "quiz me on {title}".'),
    "fork_replay": ("Retry a mistake you made before, on {title}", 'Ask your AI assistant: "replay my due mistakes".'),
    "fire_review": ("Review what is due, starting with {title}", "`syntopik review --tui`"),
    "transfer_verify": ("Use {title} on a new kind of problem", 'Ask your AI assistant: "give me a new problem on {title}".'),
    "pre_exam_taper": ("Exam soon: light review only, no new topics", "`syntopik readiness`"),
    "blind_interleave": ("Practise mixed questions from all your chapters", "`syntopik interleave --tui`"),
    "teach_frontier": ("Learn something new: {title}", 'Open your AI assistant in this folder and say "Start".'),
}


def _next_move_lines(res: dict[str, Any], titles: dict[str, str]) -> list[str]:
    mode = str(res.get("recommendedMode") or "")
    target = str(res.get("targetNodeId") or "")
    title = _short(titles.get(target, target), 60) if target else "the next topic"
    what, how = _NEXT_MOVE_PLAIN.get(mode, ("", ""))
    if mode == "teach_frontier" and not target:
        what, how = ("Add something to study", "`syntopik drop <file-or-url>`, or try `syntopik demo`.")
    if not what:
        # Unknown move: fall back to the raw fields rather than hiding them.
        human_cmd = re.sub(r"\s+--json\b", "", str(res.get("exactCmd") or "")).strip()
        lines = [f"Next: {mode or 'unknown'}", f"  Why  : {res.get('rationale', '')}"]
        if human_cmd:
            lines.append(f"  Start: {human_cmd}")
        return lines
    lines = [f"Next: {what.format(title=title)}."]
    if mode == "cold_start_reentry" or mode == "pre_exam_taper":
        lines.append(f"  Why  : {res.get('rationale', '')}")
    elif mode == "causal_probe":
        m = re.search(r"Leaf '([^']+)' failed", str(res.get("rationale") or ""))
        if m:
            missed = _short(titles.get(m.group(1), m.group(1)), 60)
            lines.append(f"  Why  : you missed {missed}, which builds on it.")
    lines.append(f"  Start: {how.format(title=title)}")
    return lines


def _cmd_next(args: argparse.Namespace) -> int:
    ws = resolve_workspace(getattr(args, "workspace", None))
    brainstem = _lazy_import("syntopik.brainstem")
    res = brainstem.plan_next_move(
        ws,
        event=getattr(args, "event", None),
        energy=getattr(args, "energy", None),
        domain=getattr(args, "domain", None),
    )
    titles = {
        str(p.get("id")): str(p.get("title") or p.get("id"))
        for p in load_jsonl(resolve_kg_dir(ws) / "propositions.jsonl")
        if isinstance(p, dict) and p.get("id")
    }
    _emit(res, bool(args.json), _next_move_lines(res, titles))
    return 0


def _parse_kv_overrides(pairs: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for raw in pairs or []:
        if "=" not in raw:
            raise SystemExit(f"Invalid --set entry {raw!r}; expected KEY=VALUE.")
        k, v = raw.split("=", 1)
        k = k.strip()
        vs = v.strip()
        if vs.lower() in ("true", "false"):
            val: Any = vs.lower() == "true"
        elif re.fullmatch(r"-?[0-9]+", vs):
            val = int(vs)
        elif re.fullmatch(r"-?[0-9]+\.[0-9]+", vs):
            val = float(vs)
        else:
            val = vs
        out[k] = val
    return out


def _int_between(lo: int, hi: int) -> Any:
    """argparse `type=` that accepts whole numbers in [lo, hi] with a plain error otherwise."""

    def _parse(raw: str) -> int:
        try:
            val = int(str(raw).strip())
        except ValueError:
            raise argparse.ArgumentTypeError(f"expected a whole number from {lo} to {hi}, got {raw!r}") from None
        if not lo <= val <= hi:
            raise argparse.ArgumentTypeError(f"must be from {lo} to {hi}, got {val}")
        return val

    return _parse


def _iso_date_arg(raw: str) -> str:
    text = str(raw).strip()
    try:
        datetime.fromisoformat(text[:10])
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected a date like 2026-12-20, got {raw!r}") from None
    return text


CALIBRATION_QUESTIONS: list[tuple[str, str, list[tuple[str, Any]]]] = [
    (
        "entry_ramp",
        "When you meet a new idea, what helps first?",
        [("An example or picture, then the formal rule", "intuition_first"),
         ("A bit of both", "balanced"),
         ("The definition or formula, then examples", "formalism_first")],
    ),
    (
        "challenge_appetite",
        "How hard should practice push you?",
        [("Gently, with plenty of hints", "guided"),
         ("Steady, a few stretches", "balanced"),
         ("Hard, try to trip me up", "adversarial")],
    ),
    (
        "session_minutes",
        "How long is a typical study session for you?",
        [("About 15 minutes", 15), ("About 30 minutes", 30), ("About 45 minutes", 45), ("An hour or more", 60)],
    ),
    (
        "prose_density",
        "How much explanation per step?",
        [("Short and to the point", "terse"), ("Normal", "standard"), ("More detail", "rich")],
    ),
]


def _ask_calibration_questions() -> dict[str, Any]:
    """Ask the 4 Tier-1 questions in the terminal. Enter keeps the middle/default answer."""
    defaults = {"entry_ramp": "balanced", "challenge_appetite": "balanced", "session_minutes": 30, "prose_density": "standard"}
    answers = dict(defaults)
    print("Four quick questions so lessons fit how you like to learn. Press Enter to skip any.\n")
    for idx, (key, question, options) in enumerate(CALIBRATION_QUESTIONS, start=1):
        print(f"{idx}. {question}")
        default_no = next(i for i, (_label, val) in enumerate(options, start=1) if val == defaults[key])
        for i, (label, _val) in enumerate(options, start=1):
            print(f"   {i}) {label}")
        for _attempt in range(3):
            try:
                raw = input(f"   Your choice [{default_no}]: ").strip()
            except EOFError:
                raw = ""
            if not raw:
                break
            if raw.isdigit() and 1 <= int(raw) <= len(options):
                answers[key] = options[int(raw) - 1][1]
                break
            print(f"   Please type a number from 1 to {len(options)}, or press Enter to skip.")
        print()
    return answers


_SCAFFOLD_PLAIN = {
    "worked_example_parsons": "fully worked examples first; you put the steps in order",
    "faded": "worked examples with some steps left for you to fill in",
    "standard": "problems you solve yourself, with hints when you need them",
    "challenge_transfer": "harder problems that take the idea to new cases",
}


def _profile_plain_lines(profile: dict[str, Any], policy: dict[str, Any]) -> list[str]:
    """How Syntopik is currently teaching this learner, in plain words."""
    cal = profile.get("calibration") if isinstance(profile.get("calibration"), dict) else {}
    out = ["How Syntopik teaches you right now:"]
    mode = str(policy.get("scaffoldingMode") or "")
    if mode in _SCAFFOLD_PLAIN:
        out.append(f"  Lessons      : {_SCAFFOLD_PLAIN[mode]}.")
    words = policy.get("maxProseWords")
    if isinstance(words, (int, float)) and words > 0:
        out.append(f"  Explanations : short, about {int(words)} words at a time.")
    habits = []
    if policy.get("requirePredictionFirst"):
        habits.append("you guess the answer before it is shown")
    if policy.get("allowProductiveFailure"):
        habits.append("sometimes you try a problem before it is taught")
    if habits:
        out.append(f"  Habits       : {'; '.join(habits)}.")
    minutes = cal.get("sessionTargetMinutes")
    if isinstance(minutes, (int, float)) and minutes > 0:
        out.append(f"  Session      : about {int(minutes)} minutes.")
    domains = profile.get("domains") if isinstance(profile.get("domains"), dict) else {}
    levels = [
        f"{name}: {d.get('expertiseBand')}"
        for name, d in sorted(domains.items())
        if isinstance(d, dict) and d.get("expertiseBand") and int(d.get("n") or 0) > 0
    ]
    if levels:
        out.append(f"  Level        : {' · '.join(levels)}.")
    meta = profile.get("metacognition") if isinstance(profile.get("metacognition"), dict) else {}
    if int(meta.get("windowCount") or 0) >= 2:
        label = str(meta.get("label") or "")
        conf = {
            "overconfident": "you tend to feel surer than your answers turn out to be.",
            "underconfident": "you tend to know more than you think.",
            "calibrated": "how sure you feel matches your results well.",
        }.get(label)
        if conf:
            out.append(f"  Confidence   : {conf}")
    if not cal.get("completed"):
        out.append("  Not tuned to you yet: `syntopik profile calibrate` asks 4 quick questions (optional).")
    out.append("  Change any of this: `syntopik profile override --help`, or tell your AI assistant.")
    return out


def _cmd_profile(args: argparse.Namespace) -> int:
    ws = resolve_workspace(getattr(args, "workspace", None))
    kg_dir = resolve_kg_dir(ws)
    lm = _lazy_import("syntopik.learner_model")
    action = str(args.action or "status").strip().lower()

    if action == "status":
        profile = lm.load_profile(kg_dir)
        policy_lines = lm.format_compact_policy_lines(profile, domain=args.domain)
        payload = {"ok": True, "action": "status", "workspace": str(ws), "profile": profile, "policyLines": policy_lines}
        try:
            policy = lm.compile_teaching_policy(profile, domain=args.domain, energy="normal")
        except Exception:
            policy = {}
        lines = _profile_plain_lines(profile, policy if isinstance(policy, dict) else {})
        lines += ["", "Summary for your AI assistant:", *[f"  {ln}" for ln in policy_lines]]
        _emit(payload, bool(args.json), lines)
        return 0

    if action == "calibrate":
        answers = {
            "entry_ramp": args.entry_ramp,
            "challenge_appetite": args.challenge_appetite,
            "session_minutes": args.session_minutes,
            "prose_density": args.prose_density,
        }
        asked = False
        if all(v is None for v in answers.values()) and not args.json and sys.stdin.isatty() and sys.stdout.isatty():
            answers = _ask_calibration_questions()
            asked = True
        profile = lm.load_profile(kg_dir)
        updated = lm.apply_tier1_calibration(
            profile,
            entry_ramp=answers["entry_ramp"] or "balanced",
            challenge_appetite=answers["challenge_appetite"] or "balanced",
            session_minutes=answers["session_minutes"] if answers["session_minutes"] is not None else 30,
            prose_density=answers["prose_density"] or "standard",
        )
        lm.save_profile(kg_dir, updated)
        policy_lines = lm.format_compact_policy_lines(updated, domain=args.domain)
        payload = {"ok": True, "action": "calibrate", "workspace": str(ws), "profile": updated, "policyLines": policy_lines}
        lines = ["Learner profile calibrated:", *[f"  {ln}" for ln in policy_lines]]
        if not asked and all(v is None for v in (args.entry_ramp, args.challenge_appetite, args.session_minutes, args.prose_density)):
            lines.append("  Saved with the default answers. Run this in a terminal to answer 4 quick questions instead.")
        _emit(payload, bool(args.json), lines)
        return 0

    if action == "policy":
        profile = lm.load_profile(kg_dir)
        policy = lm.compile_teaching_policy(profile, domain=args.domain, energy=args.energy or "normal")
        policy_lines = lm.format_compact_policy_lines(profile, domain=args.domain)
        payload = {"ok": True, "action": "policy", "workspace": str(ws), "policy": policy, "policyLines": policy_lines}
        _emit(payload, bool(args.json), ["Compiled Teaching Policy:", *[f"  {ln}" for ln in policy_lines]])
        return 0

    if action == "override":
        overrides = _parse_kv_overrides(args.set_kv)
        density_map = getattr(lm, "PROSE_DENSITY_WORDS", {"terse": 105, "standard": 135, "rich": 155})
        if args.prose_density:
            overrides["maxProseWords"] = int(density_map[args.prose_density])
        elif "proseDensity" in overrides and "maxProseWords" not in overrides:
            raw_pd = str(overrides.pop("proseDensity")).strip().lower()
            if raw_pd in density_map:
                overrides["maxProseWords"] = int(density_map[raw_pd])
        if args.session_minutes is not None:
            overrides["sessionTargetMinutes"] = int(args.session_minutes)
        updated = lm.apply_user_overrides(kg_dir, overrides)
        profile_obj = updated.get("profile", updated) if isinstance(updated, dict) else updated
        policy_lines = lm.format_compact_policy_lines(profile_obj, domain=args.domain)
        payload = {
            "ok": True,
            "action": "override",
            "workspace": str(ws),
            "overrides": overrides,
            "userOverrides": updated.get("userOverrides") if isinstance(updated, dict) else None,
            "compiledPolicy": updated.get("compiledPolicy") if isinstance(updated, dict) else None,
            "policyLines": policy_lines,
            "profile": profile_obj,
        }
        _emit(payload, bool(args.json), ["Applied learner overrides:", *[f"  {ln}" for ln in policy_lines]])
        return 0

    if action == "reset":
        prof_file = kg_dir / "learner-profile.json"
        existed = prof_file.is_file()
        if existed:
            prof_file.unlink()
        fresh = lm.load_profile(kg_dir)
        payload = {"ok": True, "action": "reset", "workspace": str(ws), "removedExisting": existed, "profile": fresh}
        _emit(payload, bool(args.json), [f"Learner profile reset ({'removed existing' if existed else 'already default'})."])
        return 0

    raise SystemExit(f"Unknown profile action: {action}")


def _cmd_drop(args: argparse.Namespace) -> int:
    ws = resolve_workspace(getattr(args, "workspace", None))
    brainstem = _lazy_import("syntopik.brainstem")
    res = brainstem.drop_sources(ws, list(args.inputs), slug=args.slug, title=args.title)
    failed = [f for f in (res.get("failed") or []) if isinstance(f, dict)]
    ingested = list(res.get("ingested") or [])
    fail_lines = [f"  Skipped {f.get('input')}: {str(f.get('error') or 'unreadable').splitlines()[0]}" for f in failed]
    if not ingested:
        _emit(res, bool(args.json), [
            "Nothing was added: none of the sources could be read.",
            *fail_lines,
            "  Check the file path, or pass a full http(s) URL or an arXiv ID.",
        ])
        return 1
    sync_hub(workspace=ws, quiet=True)
    lines = [
        f"Dropped {len(ingested)} source(s) into {ws}",
        f"  Slug : {res.get('slug') or args.slug or 'auto'}",
        *fail_lines,
    ]
    card_rows = [r for r in ingested if isinstance(r, dict) and "importedCount" in r]
    if card_rows:
        new = sum(int(r.get("importedCount") or 0) for r in card_rows)
        merged = sum(int(r.get("deduplicatedCount") or 0) for r in card_rows)
        lines.append(f"  Items: {new} new" + (f", {merged} already there (merged)" if merged else ""))
    if card_rows and len(card_rows) == len(ingested):
        # Flashcard decks are ready to study as they are; a "what do you expect" probe does not fit.
        lines.append("  Next : `syntopik review --tui` to start learning them as flashcards.")
    else:
        # Lecture notes, PDFs, videos and links are taught by the AI assistant from this spine.
        lines.append("  Next : open your AI assistant in this folder and say \"Start\"; it teaches from this source first.")
        lines.append("         Or browse it in the study dashboard: `syntopik serve --open`.")
        if res.get("probeOneHint"):
            lines.append(f"  Warm-up question: {res['probeOneHint']}")
    _emit(res, bool(args.json), lines)
    return 0


def _find_packs_dir(ws: Path, pkg_root: Path) -> Path | None:
    for cand in (ws / "packs", pkg_root / "packs", pkg_root / "_bundle" / "packs"):
        if cand.is_dir():
            return cand
    return None


def _cmd_demo(args: argparse.Namespace) -> int:
    pkg_root = resolve_package_root()
    if getattr(args, "workspace", None):
        demo_ws = Path(args.workspace).expanduser().resolve()
    else:
        demo_ws = (Path.home() / ".syntopik-demo").resolve()

    packs_dir = _find_packs_dir(demo_ws, pkg_root)
    if packs_dir is None:
        raise SystemExit("Starter packs directory (packs/) not found in workspace or package bundle.")

    selected_slugs = list(STARTER_PACK_SLUGS) if args.pack == "all" else [str(args.pack)]
    pack_files: list[Path] = []
    for slug in selected_slugs:
        pf = packs_dir / f"{slug}.syntopik-pack.zip"
        if not pf.is_file():
            raise SystemExit(f"Starter pack not found: {pf}")
        pack_files.append(pf)

    setup = _ensure_script_import("setup_harness", pkg_root)
    setup.ensure_learner_workspace(demo_ws, run_sync=False, repair_skills=False)

    from syntopik.packs import import_pack

    imported: list[dict[str, Any]] = []
    for pf in pack_files:
        res = import_pack(
            pf,
            workspace=demo_ws,
            kg_dir=demo_ws / "knowledge-graph",
            chapters_dir=demo_ws / "chapters",
            allow_code_arenas=True,
            no_sync=True,
        )
        imported.append({"pack": pf.name, "ok": bool(res.get("ok", True)), "summary": res})

    synced = sync_hub(workspace=demo_ws, quiet=True)
    payload = {
        "ok": True,
        "workspace": str(demo_ws),
        "packSelection": args.pack,
        "importedPacks": [it["pack"] for it in imported],
        "synced": synced,
        "results": imported,
    }
    _emit(
        payload,
        bool(args.json),
        [
            f"Syntopik demo workspace ready: {demo_ws}",
            f"  Imported : {', '.join(payload['importedPacks'])}",
            f"  Hub sync : {'ok' if synced else 'skipped (Node unavailable)'}",
            f"  Launch   : syntopik serve --workspace \"{demo_ws}\" --open",
        ],
    )

    if args.open:
        from syntopik.server import serve_forever

        return serve_forever(workspace=demo_ws, port=int(args.port), open_browser=True)
    return 0


def _cmd_debrief(args: argparse.Namespace) -> int:
    ws = resolve_workspace(getattr(args, "workspace", None))
    wm = _lazy_import("syntopik.work_mentor")
    diff_text: str | None = None
    if getattr(args, "diff_file", None):
        diff_text = Path(args.diff_file).expanduser().read_text(encoding="utf-8")

    sig = inspect.signature(wm.debrief_git_diff)
    kwargs: dict[str, Any] = {"ref": args.ref, "diff_text": diff_text}
    if "dry_run" in sig.parameters:
        kwargs["dry_run"] = bool(args.dry_run)
    try:
        res = wm.debrief_git_diff(ws, **kwargs)
    except (RuntimeError, ValueError, OSError) as exc:
        msg = str(exc).splitlines()[0] if str(exc) else exc.__class__.__name__
        _emit({"ok": False, "error": msg}, bool(args.json), [
            f"Work Mentor Debrief could not read the diff: {msg}",
            "  Run it inside a git project with at least two commits, or pass --diff-file <path>.",
        ])
        return 1
    if not args.dry_run:
        sync_hub(workspace=ws, quiet=True)

    lines = [
        f"Work Mentor Debrief ({args.ref})",
        f"  Findings : {len(res.get('findings') or [])}",
    ]
    _emit(res, bool(args.json), lines)
    return 0


def _cmd_mode(args: argparse.Namespace) -> int:
    ws = resolve_workspace(getattr(args, "workspace", None))
    wm = _lazy_import("syntopik.work_mentor")
    if args.mode is None and not bool(args.off):
        # Bare `syntopik mode` only reports; it never flips the setting.
        cfg = load_json(resolve_kg_dir(ws) / wm.MENTOR_CONFIG_FILENAME, default={})
        on = bool(cfg.get("mentorMode")) if isinstance(cfg, dict) else False
        label = "mentor (active)" if on else "study (mentor off)"
        res = {"ok": True, "mentorMode": on, "mode": "mentor" if on else "study", "changed": False}
        _emit(res, bool(args.json), [
            f"Syntopik mode: {label} ({ws})",
            "  Switch with `syntopik mode mentor` or `syntopik mode study`.",
        ])
        return 0
    mode_arg = str(args.mode or "mentor").strip().lower()
    enabled = (mode_arg == "mentor") and (not bool(args.off))
    res = wm.set_mentor_mode(ws, enabled=enabled)
    state_label = "mentor (active)" if enabled else "study (mentor off)"
    _emit(res, bool(args.json), [f"Syntopik mode updated: {state_label} ({ws})"])
    return 0


def _extract_id_set(items: Any) -> set[str]:
    ids: set[str] = set()
    if not isinstance(items, list):
        return ids
    for item in items:
        if isinstance(item, str) and item.strip():
            ids.add(item.strip())
        elif isinstance(item, dict):
            val = item.get("id") or item.get("propId")
            if isinstance(val, str) and val.strip():
                ids.add(val.strip())
    return ids


def _cmd_sunset(args: argparse.Namespace) -> int:
    ws = resolve_workspace(getattr(args, "workspace", None))
    kg_dir = resolve_kg_dir(ws)
    chapters_dir = resolve_chapters_dir(ws)
    slug = str(args.slug).strip()
    if not slug:
        raise SystemExit("Provide a chapter slug to sunset.")

    gi = _ensure_script_import("graph_intel", ws)
    if not hasattr(gi, "compute_sunset_plan"):
        raise SystemExit("scripts/graph_intel.py does not expose compute_sunset_plan().")
    plan = gi.compute_sunset_plan(kg_dir, slug)

    safe_slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", slug).strip("-") or "chapter"
    archive_path = (chapters_dir / "_archived" / f"{safe_slug}.syntopik-pack.zip").resolve()

    keep_ids = _extract_id_set(plan.get("keepAxioms"))
    archive_ids = _extract_id_set(plan.get("archiveLeaves"))

    if not keep_ids and not archive_ids:
        known = sorted(
            {p.name[: -len("-tracker.json")] for p in chapters_dir.glob("*-tracker.json")}
            | {
                str(r.get("chapter")).strip()
                for r in load_jsonl(kg_dir / "propositions.jsonl")
                if isinstance(r, dict) and r.get("chapter")
            }
        )
        if slug not in known:
            hint = ", ".join(known[:12]) if known else "none yet"
            _emit(
                {"ok": False, "error": f"unknown chapter '{slug}'", "knownChapters": known},
                bool(args.json),
                [f"No chapter named '{slug}'. Known chapters: {hint}"],
            )
            return 1

    applied = False
    pack_snapshot: dict[str, Any] | None = None
    archived_count = 0
    kept_axiom_count = 0

    if args.apply:
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            from syntopik.packs import export_pack

            pack_snapshot = export_pack(
                slug,
                workspace=ws,
                kg_dir=kg_dir,
                chapters_dir=chapters_dir,
                out_path=archive_path,
            )
        except Exception as exc:
            pack_snapshot = {"ok": False, "error": str(exc)}

        props_file = kg_dir / "propositions.jsonl"
        props = load_jsonl(props_file)
        mutated = False
        for p in props:
            pid = str(p.get("id") or "")
            if pid in archive_ids:
                p["archived"] = True
                archived_count += 1
                mutated = True
            elif pid in keep_ids:
                p["archived"] = False
                p["targetRetention"] = 0.80
                kept_axiom_count += 1
                mutated = True
        if mutated:
            save_jsonl_atomic(props_file, props)
        sync_hub(workspace=ws, quiet=True)
        applied = True

    payload = {
        "ok": True,
        "slug": slug,
        "applied": applied,
        "archivePath": str(archive_path),
        "packSnapshot": pack_snapshot,
        "archivedCount": archived_count if applied else len(archive_ids),
        "keptAxiomCount": kept_axiom_count if applied else len(keep_ids),
        "plan": plan,
    }
    lines = [
        f"Semester Sunset ({slug}) — {'APPLIED' if applied else 'PREVIEW (pass --apply to archive)'}",
        f"  Keep axioms (R=0.80): {len(keep_ids)}",
        f"  Archive leaves      : {len(archive_ids)}",
        f"  Archive pack        : {archive_path}",
    ]
    _emit(payload, bool(args.json), lines)
    return 0


def _edit_exams(args: argparse.Namespace, kg_dir: Path) -> tuple[int, list[str]] | None:
    """Handle `pace --add-exam/--remove-exam/--minutes`. Returns None when no edit was asked for."""
    ex_mod = _lazy_import("syntopik.exams")
    add_raw = getattr(args, "add_exam", None)
    remove_name = (getattr(args, "remove_exam", None) or "").strip()
    minutes = getattr(args, "minutes", None)
    if add_raw is None and not remove_name and minutes is None:
        return None
    add_name = (add_raw or "").strip()
    if add_raw is not None and not add_name:
        return 1, ['Give the exam a name, for example: syntopik pace --add-exam "Biology final" --date 2026-12-20']
    lines: list[str] = []
    try:
        if add_raw is not None:
            if not (getattr(args, "date", None) or "").strip():
                return 1, [f'Add a date in YYYY-MM-DD form, for example: syntopik pace --add-exam "{add_name}" --date 2026-12-20']
            exam_date = ex_mod.parse_exam_date(args.date)
        if minutes is not None and not 15 <= int(minutes) <= 720:
            raise ValueError("Daily study time must be between 15 and 720 minutes.")
        if remove_name:
            ex_mod.remove_exam(kg_dir, remove_name)
            lines.append(f"Removed exam '{remove_name}'.")
        if add_raw is not None:
            res = ex_mod.set_exam(kg_dir, add_name, exam_date, chapter=getattr(args, "chapter", None))
            verb = "Updated" if res["replaced"] else "Added"
            lines.append(f"{verb} exam '{res['name']}' on {res['examDate']} ({res['daysAway']} day(s) away).")
        if minutes is not None:
            ex_mod.set_daily_minutes(kg_dir, int(minutes))
            lines.append(f"Daily study time set to {int(minutes)} minutes.")
    except ValueError as exc:
        return 1, [*lines, str(exc)]
    return 0, lines


def _cmd_pace(args: argparse.Namespace) -> int:
    ws = resolve_workspace(getattr(args, "workspace", None))
    edit = _edit_exams(args, resolve_kg_dir(ws))
    if edit is not None:
        rc, msg = edit
        if rc != 0:
            _emit({"ok": False, "messages": msg}, bool(args.json), msg)
            return rc
        if not args.json:
            print("\n".join(msg) + "\n", flush=True)
    pkg_root = resolve_package_root()
    daily_script = pkg_root / "scripts" / "daily_plan.py"
    py_bin = _find_python_for_workspace(ws, pkg_root)
    cmd = [
        py_bin,
        "-B",
        str(daily_script),
        "--pace",
        "--kg-dir",
        str(resolve_kg_dir(ws)),
        "--chapters-dir",
        str(resolve_chapters_dir(ws)),
    ]
    if args.rebalance:
        cmd.append("--rebalance")
    if args.ics:
        cmd.extend(["--ics", str(Path(args.ics).expanduser().resolve())])
    if args.json:
        cmd.append("--json")
    proc = subprocess.run(cmd, check=False)
    return int(proc.returncode)


def _cmd_board(args: argparse.Namespace) -> int:
    ws = resolve_workspace(getattr(args, "workspace", None))
    kg_dir = resolve_kg_dir(ws)
    wb = _lazy_import("syntopik.whiteboard")
    action = str(args.action or "status").strip().lower()

    if action == "status":
        res = wb.get_session(kg_dir)
        frames = res.get("frames") or []
        pins = res.get("pins") or []
        _emit(res, bool(args.json), [f"Whiteboard session ({ws}): {len(frames)} frame(s), {len(pins)} pin(s)"])
        return 0

    if action == "push":
        if args.frame_json:
            raw = args.frame_json.strip()
            if raw.startswith("@"):
                frame = json.loads(Path(raw[1:]).expanduser().read_text(encoding="utf-8"))
            else:
                frame = json.loads(raw)
            if isinstance(frame, dict) and "content" in frame and "body" not in frame:
                frame["body"] = frame.pop("content")
        else:
            frame = {
                "kind": args.kind or "summary",
                "title": args.title or "",
                "body": args.content or "",
            }
        res = wb.push_frame(kg_dir, frame)
        _emit(res, bool(args.json), [f"Pushed whiteboard frame ({frame.get('kind', 'summary')})."])
        return 0

    if action == "pin":
        res = wb.pin_step(kg_dir, int(args.step_index), args.note or "")
        _emit(res, bool(args.json), [f"Pinned whiteboard step {args.step_index}."])
        return 0

    if action == "clear":
        res = wb.clear_session(kg_dir)
        _emit(res, bool(args.json), [f"Cleared whiteboard session ({ws})."])
        return 0

    raise SystemExit(f"Unknown board action: {action}")


def _cmd_bench(args: argparse.Namespace) -> int:
    ws = resolve_workspace(getattr(args, "workspace", None))
    pkg_root = resolve_package_root()
    bench_script: Path | None = None
    for cand in (
        ws / "benchmarks" / "syntopik_bench.py",
        pkg_root / "benchmarks" / "syntopik_bench.py",
        pkg_root / "_bundle" / "benchmarks" / "syntopik_bench.py",
    ):
        if cand.is_file():
            bench_script = cand
            break
    if bench_script is None:
        raise SystemExit("Benchmark runner benchmarks/syntopik_bench.py not found.")

    py_bin = _find_python_for_workspace(ws, pkg_root)
    cmd = [
        py_bin,
        "-B",
        str(bench_script),
        "--days",
        str(args.days),
        "--seed",
        str(args.seed),
    ]
    if args.svg:
        cmd.append("--svg")
    if args.markdown:
        cmd.append("--markdown")
    if args.check:
        cmd.append("--check")
    if args.json:
        cmd.append("--json")

    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    existing_pp = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{pkg_root}{os.pathsep}{existing_pp}" if existing_pp else str(pkg_root)
    proc = subprocess.run(cmd, env=env, check=False)
    return int(proc.returncode)


# ---------------------------------------------------------------------------
# v3.1 First-Class Verbs (lazy imports for Rosetta, Sync-Delta, Replay, Diff)
# ---------------------------------------------------------------------------


def _cmd_rosetta(args: argparse.Namespace) -> int:
    ws = resolve_workspace(getattr(args, "workspace", None))
    kg_dir = resolve_kg_dir(ws)
    iso = _lazy_import("syntopik.isomorphism")
    action = str(args.action or "scan").strip().lower()

    if action == "archetypes":
        archetypes = list(iso.load_archetypes() or [])
        payload = {
            "ok": True,
            "action": "archetypes",
            "count": len(archetypes),
            "archetypes": archetypes,
        }
        lines = [
            f"Syntopik Rosetta Structural Archetypes ({len(archetypes)}):",
            *[f"  - {a.get('id')}: {a.get('name', a.get('id'))}" for a in archetypes[:20]],
        ]
        _emit(payload, bool(args.json), lines)
        return 0

    if action == "scan":
        sig = inspect.signature(iso.discover_isomorphisms)
        kwargs: dict[str, Any] = {}
        if getattr(args, "min_score", None) is not None and "min_score" in sig.parameters:
            kwargs["min_score"] = float(args.min_score)
        res = iso.discover_isomorphisms(kg_dir, **kwargs)
        chords = list((res or {}).get("chords") or [])
        payload = {"ok": True, "action": "scan", "workspace": str(ws), **(res if isinstance(res, dict) else {"result": res})}
        lines = [
            f"Rosetta Isomorphism Scan ({ws}) — {len(chords)} cross-domain chord(s)",
            *[
                f"  - [{c.get('archetypeId')}] {c.get('sourceId')} <-> {c.get('targetId')} (score={c.get('score')})"
                for c in chords[:15]
            ],
        ]
        if not chords:
            lines.append("  No cross-domain structural twins matched yet.")
        _emit(payload, bool(args.json), lines)
        return 0

    if action == "bridge":
        prop_id = str(args.prop_id or "").strip()
        if not prop_id:
            raise SystemExit("Provide --prop-id <id> for 'syntopik rosetta bridge'.")
        bridge = iso.build_bijection_opener(kg_dir, prop_id)
        payload = {
            "ok": True,
            "action": "bridge",
            "workspace": str(ws),
            "propId": prop_id,
            "found": bridge is not None,
            "bridge": bridge,
        }
        if isinstance(bridge, dict):
            lines = [
                f"Rosetta Bijection Bridge for {prop_id}:",
                f"  Known twin : {bridge.get('knownPropId')} ({bridge.get('knownTitle') or ''})",
                f"  Archetype  : {bridge.get('archetypeName') or ''}",
                f"  Opener     : {bridge.get('openerPrompt') or ''}",
            ]
        else:
            lines = [f"No mastered structural twin bridge found for {prop_id}."]
        _emit(payload, bool(args.json), lines)
        return 0

    if action == "link":
        source_id = str(args.source or "").strip()
        target_id = str(args.target or "").strip()
        archetype_id = str(args.archetype or "").strip()
        if not (source_id and target_id and archetype_id):
            raise SystemExit("Provide --source <id> --target <id> --archetype <id> for 'syntopik rosetta link'.")
        sig = inspect.signature(iso.link_isomorphism_chord)
        kwargs = {}
        if getattr(args, "breaks_at", None) and "breaks_at" in sig.parameters:
            kwargs["breaks_at"] = list(args.breaks_at)
        res = iso.link_isomorphism_chord(kg_dir, source_id, target_id, archetype_id, **kwargs)
        sync_hub(workspace=ws, quiet=True)
        payload = {"ok": True, "action": "link", "workspace": str(ws), **(res if isinstance(res, dict) else {"result": res})}
        _emit(payload, bool(args.json), [f"Linked Rosetta chord [{archetype_id}]: {source_id} <-> {target_id}"])
        return 0

    raise SystemExit(f"Unknown rosetta action: {action}")


def _sync_passphrase(*, confirm: bool) -> str:
    """Passphrase from SYNTOPIK_SYNC_PASSPHRASE, else an interactive no-echo prompt (never a CLI arg)."""
    from syntopik.sync_crypto import PASSPHRASE_ENV, passphrase_from_env

    env_val = passphrase_from_env()
    if env_val:
        return env_val
    if not sys.stdin.isatty():
        raise SystemExit(f"Encrypted sync needs a passphrase: set {PASSPHRASE_ENV} or run in a terminal.")
    import getpass

    first = getpass.getpass("Sync passphrase: ")
    if confirm and getpass.getpass("Repeat passphrase: ") != first:
        raise SystemExit("Passphrases did not match.")
    return first


def _delta_file_is_encrypted(path: str) -> bool:
    try:
        with open(path, "rb") as fh:
            head = fh.read(256)
    except OSError:
        return False
    return b"syntopik-delta-enc-v1" in head


def _cmd_sync_delta(args: argparse.Namespace) -> int:
    ws = resolve_workspace(getattr(args, "workspace", None))
    sd = _lazy_import("syntopik.sync_delta")
    action = str(args.action or "status").strip().lower()

    if action == "status":
        res = sd.get_sync_status(ws)
        payload = {"ok": True, "action": "status", "workspace": str(ws), **(res if isinstance(res, dict) else {"status": res})}
        lines = [
            f"Sync-Delta Status ({ws}):",
            f"  Device ID : {payload.get('deviceId') or 'uninitialized'}",
            f"  Last Sync : {payload.get('lastSyncAt') or 'never'}",
        ]
        _emit(payload, bool(args.json), lines)
        return 0

    if action == "export":
        passphrase = _sync_passphrase(confirm=True) if getattr(args, "encrypt", False) else None
        if passphrase is not None and not args.out:
            raise SystemExit("--encrypt needs --out <file>.")
        try:
            res = sd.export_sync_delta(ws, out_path=args.out, since=args.since, passphrase=passphrase)
        except ValueError as exc:
            _emit({"ok": False, "action": "export", "error": str(exc)}, bool(args.json), [f"Export failed: {exc}"])
            return 1
        payload = {"ok": bool(res.get("ok", True)), "action": "export", "workspace": str(ws), **(res if isinstance(res, dict) else {})}
        if payload.get("encrypted"):
            payload.pop("delta", None)  # never echo plaintext study data next to an encrypted file
        lines = [
            f"Exported {'encrypted ' if payload.get('encrypted') else ''}CRDT sync delta ({ws})",
            f"  Output : {payload.get('outPath') or payload.get('path') or '(in-memory)'}",
        ]
        _emit(payload, bool(args.json), lines)
        return 0 if payload.get("ok", True) else 1

    if action == "verify":
        target_file = args.file or args.out
        if not target_file:
            raise SystemExit("Provide --file <delta_path> for 'syntopik sync-delta verify'.")
        passphrase = _sync_passphrase(confirm=False) if _delta_file_is_encrypted(target_file) else None
        res = sd.verify_sync_delta(target_file, passphrase=passphrase)
        ok = bool(res.get("ok", False)) if isinstance(res, dict) else False
        payload = {"ok": ok, "action": "verify", **(res if isinstance(res, dict) else {})}
        _emit(payload, bool(args.json), [f"Sync delta verify ({target_file}): {'VALID' if ok else 'INVALID'}"])
        return 0 if ok else 1

    if action == "merge":
        target_file = args.file or args.out
        if not target_file:
            raise SystemExit("Provide --file <delta_path> for 'syntopik sync-delta merge'.")
        passphrase = _sync_passphrase(confirm=False) if _delta_file_is_encrypted(target_file) else None
        res = sd.merge_sync_delta(ws, target_file, dry_run=bool(args.dry_run), passphrase=passphrase)
        ok = bool(res.get("ok", True)) if isinstance(res, dict) else False
        if ok and not args.dry_run:
            sync_hub(workspace=ws, quiet=True)
        payload = {"ok": ok, "action": "merge", "workspace": str(ws), "dryRun": bool(args.dry_run), **(res if isinstance(res, dict) else {})}
        lines = [
            f"Sync delta merge ({target_file}) — {'DRY-RUN' if args.dry_run else ('OK' if ok else 'FAILED')}",
            f"  Added: {payload.get('added', 0)} · Updated: {payload.get('updated', 0)}",
        ]
        _emit(payload, bool(args.json), lines)
        return 0 if ok else 1

    raise SystemExit(f"Unknown sync-delta action: {action}")


def _cmd_calibrate_policy(args: argparse.Namespace) -> int:
    ws = resolve_workspace(getattr(args, "workspace", None))
    se = _lazy_import("syntopik.sim_eval")
    if not hasattr(se, "replay_learner_history"):
        raise SystemExit("syntopik.sim_eval does not expose replay_learner_history().")
    sig = inspect.signature(se.replay_learner_history)
    kwargs: dict[str, Any] = {"apply_if_improved": bool(args.apply)}
    if getattr(args, "min_events", None) is not None and "min_events" in sig.parameters:
        kwargs["min_events"] = int(args.min_events)
    res = se.replay_learner_history(ws, **kwargs)
    ok = bool(res.get("ok", True)) if isinstance(res, dict) else False
    payload = {"ok": ok, "workspace": str(ws), **(res if isinstance(res, dict) else {})}
    if payload.get("insufficientData"):
        lines = [
            f"Policy Calibration ({ws}): insufficient confidence-rated reviews "
            f"({payload.get('eventCount', 0)}/{payload.get('minEvents', 12)}).",
        ]
    else:
        lines = [
            f"Policy Calibration ({ws}): applied={bool(payload.get('applied'))}",
            f"  OOS improvement : {payload.get('improvement', payload.get('oosImprovement', 0))}",
        ]
    _emit(payload, bool(args.json), lines)
    return 0 if ok else 1


def _cmd_proof_diff(args: argparse.Namespace) -> int:
    ws = resolve_workspace(getattr(args, "workspace", None))
    kg_dir = Path(args.kg_dir).expanduser().resolve() if getattr(args, "kg_dir", None) else resolve_kg_dir(ws)
    df = _ensure_script_import("derivation_forge", ws)
    try:
        student_steps = df._load_json_arg(args.steps_json)
        res = df.diff_handwritten_steps(
            kg_dir,
            args.id,
            student_steps,
            auto_spawn_fork=not bool(args.no_spawn_fork),
            workspace=ws,
        )
    except Exception as exc:  # noqa: BLE001
        err_payload = {"ok": False, "command": "proof-diff", "derivationId": args.id, "error": str(exc)}
        if args.json:
            print(json.dumps(err_payload, indent=2, ensure_ascii=False))
        else:
            print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if res.get("spawnedFork") and not args.no_spawn_fork:
        sync_hub(workspace=ws, quiet=True)

    lines: list[str]
    if res.get("allVerified"):
        lines = [f"[VERIFIED] {res['derivationId']}: {res['verifiedCount']}/{res['totalSteps']} steps verified."]
    elif res.get("earliestBreakLine") is not None:
        lines = [
            f"[BREAK line {res['earliestBreakLine']}] {res['derivationId']}::{res['earliestBreakStepId']} "
            f"({res['errorKind']}) · {res['verifiedCount']}/{res['totalSteps']} verified",
        ]
        nudges = res.get("nudges") or {}
        for lvl in ("R0", "R1", "R2"):
            if nudges.get(lvl):
                lines.append(f"  {lvl}: {nudges[lvl]}")
        if isinstance(res.get("spawnedFork"), dict) and res["spawnedFork"].get("id"):
            lines.append(f"  Fork: {res['spawnedFork']['id']}")
        probe = (res.get("diagnosticProbe") or {}).get("bestProbe")
        if isinstance(probe, dict) and probe.get("propId"):
            lines.append(f"  Diagnostic Probe: {probe['propId']} (deltaH={probe.get('deltaHBits', 0)} bits)")
    else:
        lines = [f"[INCOMPLETE] {res['derivationId']}: {res['verifiedCount']}/{res['totalSteps']} steps verified."]
    _emit(res, bool(args.json), lines)
    return 0 if res.get("ok") else 1


# ---------------------------------------------------------------------------
# v3.2 First-Class Verbs (lazy imports for Readiness & Blind-Chapter Interleave)
# ---------------------------------------------------------------------------


def _call_with_supported_kwargs(fn: Any, *pos_args: Any, **kwargs: Any) -> Any:
    sig = inspect.signature(fn)
    accepts_var_kw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
    filtered = kwargs if accepts_var_kw else {k: v for k, v in kwargs.items() if k in sig.parameters}
    return fn(*pos_args, **filtered)


def _cmd_report(args: argparse.Namespace) -> int:
    ws = resolve_workspace(getattr(args, "workspace", None))
    kg_dir = resolve_kg_dir(ws)
    rp = _lazy_import("syntopik.report")
    rep = rp.build_progress_report(kg_dir, days=max(1, int(args.days)))
    md_path = getattr(args, "md", None)
    if md_path:
        out = Path(md_path).expanduser()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(rp.render_report_markdown(rep), encoding="utf-8")
        rep["markdownPath"] = str(out.resolve())
    lines = rp.render_report_lines(rep)
    if md_path:
        lines.append(f"  Markdown       : {rep['markdownPath']}")
    _emit({"workspace": str(ws), **rep}, bool(args.json), _with_empty_hint(lines, kg_dir))
    return 0


def _cmd_readiness(args: argparse.Namespace) -> int:
    ws = resolve_workspace(getattr(args, "workspace", None))
    kg_dir = resolve_kg_dir(ws)
    chapters_dir = resolve_chapters_dir(ws)
    rd = _lazy_import("syntopik.readiness")

    if bool(getattr(args, "premortem_only", False)):
        res = _call_with_supported_kwargs(
            rd.build_premortem_shield,
            workspace=ws,
            subject=getattr(args, "subject", None),
            limit=3,
            kg_dir=kg_dir,
        )
        bullets = list((res or {}).get("bullets") or [])
        payload = {"ok": True, "mode": "premortem_only", "workspace": str(ws), **(res if isinstance(res, dict) else {"result": res})}
        lines = [
            f"Pre-Mortem Shield ({ws}) — {len(bullets)} evidence-backed bullet(s) [{payload.get('sourceBasis', 'logs')}]",
            *[
                f"  {b.get('rank', idx)}. IF {b.get('ifCue', '')} -> THEN {b.get('thenAction', '')} [{b.get('evidenceBadge', '')}]"
                for idx, b in enumerate(bullets, 1)
            ],
        ]
        if not bullets:
            lines.append("  No logged forks/mistakes/exam errors yet (zero generic filler).")
        _emit(payload, bool(args.json), lines)
        return 0

    res = _call_with_supported_kwargs(
        rd.compute_readiness,
        workspace=ws,
        exam_date=getattr(args, "exam_date", None),
        subject=getattr(args, "subject", None),
        max_marks=getattr(args, "max_marks", None),
        kg_dir=kg_dir,
        chapters_dir=chapters_dir,
    )
    payload = {"ok": bool((res or {}).get("ok", True)), "workspace": str(ws), **(res if isinstance(res, dict) else {"result": res})}
    pred = payload.get("predictedScorePct") or {}
    taper = payload.get("taperMode") or {}
    lines = [
        f"Exam Readiness ({ws}) — Index {payload.get('readinessIndex', 0)}/100 ({str(payload.get('band', 'unknown')).replace('_', ' ')})",
    ]
    if isinstance(pred, dict) and pred:
        lines.append(
            f"  Predicted Score : {pred.get('expected', '?')}% (range {pred.get('low', '?')}%–{pred.get('high', '?')}%)"
        )
    if taper.get("active"):
        lines.append(f"  Taper Mode      : ACTIVE ({taper.get('phase')} · {taper.get('hoursRemaining')}h remaining)")
    levers = [lv for lv in list(payload.get("topLevers") or [])[:3] if isinstance(lv, dict)]
    if levers:
        titles = {
            str(p.get("id")): str(p.get("title") or p.get("id"))
            for p in load_jsonl(kg_dir / "propositions.jsonl")
            if isinstance(p, dict) and p.get("id")
        }
        lines.append("  Best next steps (biggest gain for the time):")
        lines.extend(_readiness_lever_lines(levers, titles))
    _emit(payload, bool(args.json), _with_empty_hint(lines, kg_dir))
    return 0 if payload.get("ok", True) else 1


_LEVER_PLAIN: dict[str, tuple[str, str]] = {
    "repair_misconception": ("Fix a mix-up: {title}", 'Ask your AI assistant: "re-teach {title}".'),
    "clear_forks": ("Retry the past mistakes that are due again", 'Ask your AI assistant: "replay my due mistakes".'),
    "upgrade_evidence": ("Show you can use {title} on a new problem", 'Ask your AI assistant: "give me a new problem on {title}".'),
    "plug_ev_leak": ("Stop losing marks to wrong guesses", "See where: `syntopik readiness --premortem-only`."),
    "refresh_decay": ("Review the cards you are starting to forget", "`syntopik review --tui`"),
    "interleave_drill": ("Practise mixed questions from all your chapters", "`syntopik interleave --tui`"),
    "timed_baseline": ("Take one timed practice exam", 'Ask your AI assistant: "give me a timed mock exam".'),
}


def _readiness_lever_lines(levers: list[dict[str, Any]], titles: dict[str, str]) -> list[str]:
    """Readiness levers in plain words, each with how long it takes and how to start it."""
    out: list[str] = []
    for idx, lv in enumerate(levers, 1):
        kind = str(lv.get("kind") or "")
        raw_title = str(lv.get("title") or lv.get("label") or lv.get("chapter") or lv.get("propId") or "")
        name = ""
        m = re.search(r"'([^']+)'", raw_title)
        if m:
            name = titles.get(m.group(1), m.group(1))
        elif ":" in raw_title:
            name = raw_title.split(":", 1)[1].strip()
        name = _short(name, 60) if name else "this topic"
        what, how = _LEVER_PLAIN.get(kind, (raw_title, str(lv.get("exactCmd") or "")))
        count = re.search(r"\b(\d+)\b", raw_title)
        if kind == "clear_forks" and count and "Fork" in raw_title:
            what = f"Retry {count.group(1)} past mistake(s) that are due again"
        elif kind == "refresh_decay" and count:
            what = f"Review {count.group(1)} card(s) you are starting to forget"
        mins = lv.get("effortMinutes")
        when = f" (~{int(mins)} min)" if isinstance(mins, (int, float)) and mins > 0 else ""
        out.append(f"    {idx}. {what.format(title=name)}{when}")
        if how:
            out.append(f"       {how.format(title=name)}")
    return out


def _flatten_subjects(raw_subjects: Any) -> list[str] | None:
    if not raw_subjects:
        return None
    out: list[str] = []
    for entry in raw_subjects:
        chunks = entry if isinstance(entry, list) else [entry]
        for ch in chunks:
            for tok in str(ch).split(","):
                cleaned = tok.strip()
                if cleaned and cleaned not in out:
                    out.append(cleaned)
    return out or None


def _run_interleave_tui(ws: Path, kg_dir: Path, il: Any, payload: dict[str, Any]) -> int:
    """Terminal drill for a blind interleave set: name the idea behind each mixed-up question,
    then try it, see the answer, and say whether you got it. Results go to the same ledgers."""
    session_id = str(payload.get("sessionId"))
    items = list(payload.get("items") or [])
    sealed = {}
    for row in load_jsonl(kg_dir / il.INTERLEAVE_SESSIONS_FILENAME):
        if str(row.get("sessionId") or "") == session_id:
            sealed = {str(it.get("itemId")): it for it in row.get("items") or [] if isinstance(it, dict)}
    print(f"Mixed drill: {len(items)} question(s) from different chapters, shuffled. ('q' to stop)")
    print("For each one, first pick the idea it needs. Then work it out and check the answer.")
    results: dict[str, dict[str, Any]] = {}
    answered = 0
    for idx, it in enumerate(items, start=1):
        iid = str(it.get("itemId") or "")
        choices = [c for c in it.get("principleChoices") or [] if isinstance(c, dict)]
        print("")
        print(f"[{idx}/{len(items)}]")
        for line in str(it.get("prompt") or "").splitlines():
            print(f"  {line}" if line.strip() else "")
        print("  Which idea does this need?")
        for c in choices:
            print(f"    {c.get('id')}) {c.get('text')}")
        ids = {str(c.get("id")).lower(): str(c.get("id")) for c in choices}
        shown_at = time.monotonic()
        pick: str | None = None
        conf = 0.5
        stop = False
        for _attempt in range(3):
            try:
                raw = input("  Your pick (letter, + optional confidence %): ").strip()
            except (EOFError, KeyboardInterrupt):
                print("")
                stop = True
                break
            if raw.lower() in ("q", "quit"):
                stop = True
                break
            parts = raw.split()
            if parts and parts[0].lower() in ids:
                pick = ids[parts[0].lower()]
                if len(parts) >= 2:
                    try:
                        conf = max(0.0, min(100.0, float(parts[1].rstrip("%")))) / 100.0
                    except ValueError:
                        conf = 0.5
                break
            print(f"  Type one of {', '.join(sorted(ids.values()))}.")
        if stop:
            break
        if pick is None:
            continue
        duration_ms = int(min(time.monotonic() - shown_at, 600.0) * 1000)
        chk = il.check_principle_step(
            ws,
            session_id=session_id,
            item_id=iid,
            selected_principle=pick,
            confidence=conf,
            duration_ms=duration_ms,
            kg_dir=kg_dir,
        )
        if not chk.get("ok"):
            print(f"  Could not check that one: {chk.get('error')}")
            continue
        answered += 1
        chapter = str(chk.get("revealedChapter") or "")
        where = f" (from {chapter})" if chapter else ""
        if chk.get("correct"):
            print(f"  Right: {chk.get('expectedPrinciple')}{where}.")
        else:
            print(f"  It needs: {chk.get('expectedPrinciple')}{where}.")
            if chk.get("confusedChapter"):
                print(f"  The one you picked belongs to {chk.get('confusedChapter')}, so those two are easy to mix up.")
        answer = str((sealed.get(iid) or {}).get("sealedAnswer") or "").strip()
        if not answer:
            continue
        try:
            go = input("  Now work it out. Press Enter to see the answer (or 's' to skip): ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("")
            break
        if go in ("q", "quit"):
            break
        if go in ("s", "skip"):
            continue
        print(f"  Answer: {answer}")
        try:
            got = input("  Did you get it? [y/n]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("")
            break
        if got in ("y", "yes", "n", "no"):
            results[iid] = {"solved": got.startswith("y")}
    if not answered:
        print("Stopped before any answers. Nothing was recorded.")
        return 0
    done = il.complete_interleave_session(ws, session_id=session_id, results=results, kg_dir=kg_dir)
    sync_hub(workspace=ws, quiet=True)
    print("")
    print(f"Drill done: picked the right idea {done.get('tier1Correct', 0)}/{done.get('tier1Total', 0)} time(s).")
    if done.get("step2Total"):
        print(f"  Solved {done.get('step2Correct', 0)}/{done.get('step2Total', 0)} of the ones you worked out.")
    pairs = done.get("topConfusionPairs") or []
    if pairs:
        top = pairs[0]
        print(f"  Most mixed up: {top.get('trueChapter')} with {top.get('confusedWithChapter')}.")
    print("  Misses come back in your next review (`syntopik review --tui`).")
    return 0


def _cmd_interleave(args: argparse.Namespace) -> int:
    ws = resolve_workspace(getattr(args, "workspace", None))
    kg_dir = resolve_kg_dir(ws)
    il = _lazy_import("syntopik.interleave")
    action = str(getattr(args, "action", None) or "build").strip().lower()
    subjects = _flatten_subjects(getattr(args, "subjects", None))

    if action == "build":
        res = _call_with_supported_kwargs(
            il.build_interleave_set,
            workspace=ws,
            n=int(args.n),
            subjects=subjects,
            mode=str(args.mode or "principle_first"),
            seed=args.seed,
            kg_dir=kg_dir,
        )
        payload = {"ok": bool((res or {}).get("ok", True)), "action": "build", "workspace": str(ws), **(res if isinstance(res, dict) else {"result": res})}
        items = list(payload.get("items") or [])
        if getattr(args, "tui", False) and not args.json and items and payload.get("sessionId"):
            return _run_interleave_tui(ws, kg_dir, il, payload)
        lines = [
            f"Blind-Chapter Interleave Set ({payload.get('sessionId') or 'not started'}) — {len(items)} item(s) [mode={payload.get('mode', args.mode)}]",
            *[f"  - {it.get('itemId') or it.get('id')}: {' '.join(str(it.get('prompt') or '').split())[:96]}" for it in items[:10]],
        ]
        if items:
            lines.append("  Drill it here one question at a time: `syntopik interleave --tui`, or ask your AI assistant for a mixed drill.")
        _emit(payload, bool(args.json), lines if items else _with_empty_hint(lines, kg_dir))
        return 0 if payload.get("ok", True) else 1

    if action == "check":
        if not (args.session_id and args.item_id and args.principle):
            raise SystemExit("Provide --session-id, --item-id, and --principle for 'syntopik interleave check'.")
        res = _call_with_supported_kwargs(
            il.check_principle_step,
            workspace=ws,
            session_id=str(args.session_id),
            item_id=str(args.item_id),
            selected_principle=str(args.principle),
            confidence=float(args.confidence),
            duration_ms=args.duration_ms,
            kg_dir=kg_dir,
        )
        payload = {"ok": bool((res or {}).get("ok", True)), "action": "check", "workspace": str(ws), **(res if isinstance(res, dict) else {"result": res})}
        lines = [
            f"Principle Check ({args.session_id} / {args.item_id}): {'CORRECT' if payload.get('correct') else 'MISS'}",
        ]
        _emit(payload, bool(args.json), lines)
        return 0 if payload.get("ok", True) else 1

    if action == "complete":
        if not args.session_id:
            raise SystemExit("Provide --session-id for 'syntopik interleave complete'.")
        results_data: Any = None
        if getattr(args, "results_json", None):
            raw = str(args.results_json).strip()
            results_data = json.loads(Path(raw[1:]).expanduser().read_text(encoding="utf-8")) if raw.startswith("@") else json.loads(raw)
        res = _call_with_supported_kwargs(
            il.complete_interleave_session,
            workspace=ws,
            session_id=str(args.session_id),
            results=results_data,
            kg_dir=kg_dir,
        )
        sync_hub(workspace=ws, quiet=True)
        payload = {"ok": bool((res or {}).get("ok", True)), "action": "complete", "workspace": str(ws), **(res if isinstance(res, dict) else {"result": res})}
        _emit(payload, bool(args.json), [f"Completed interleave session {args.session_id}."])
        return 0 if payload.get("ok", True) else 1

    if action == "stats":
        res = _call_with_supported_kwargs(il.get_interleave_stats, workspace=ws, kg_dir=kg_dir)
        payload = {"ok": bool((res or {}).get("ok", True)), "action": "stats", "workspace": str(ws), **(res if isinstance(res, dict) else {"result": res})}
        acc = payload.get("tier1AccuracyPct")
        if acc is None:
            acc = payload.get("tier1Accuracy")
        if acc is None:
            acc = payload.get("principleAccuracy")
        acc_str = f"{acc}%" if isinstance(acc, (int, float)) and not isinstance(acc, bool) else ("n/a" if acc is None else str(acc))
        lines = [
            f"Blind-Chapter Interleave Stats ({ws}): {payload.get('totalSessions', 0)} session(s), "
            f"Tier-1 accuracy={acc_str}",
        ]
        _emit(payload, bool(args.json), lines)
        return 0 if payload.get("ok", True) else 1

    if action == "export-cbt":
        res = _call_with_supported_kwargs(
            il.export_interleave_cbt_spec,
            workspace=ws,
            n=int(args.n),
            subjects=subjects,
            kg_dir=kg_dir,
        )
        if getattr(args, "out", None) and isinstance(res, dict):
            out_path = Path(args.out).expanduser().resolve()
            save_json_atomic(out_path, res)
            res = {**res, "outPath": str(out_path)}
        payload = {"ok": bool((res or {}).get("ok", True)), "action": "export-cbt", "workspace": str(ws), **(res if isinstance(res, dict) else {"spec": res})}
        _emit(payload, bool(args.json), [f"Exported interleave CBT spec ({len((payload.get('questions') or []))} question(s))."])
        return 0 if payload.get("ok", True) else 1

    raise SystemExit(f"Unknown interleave action: {action}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="syntopik",
        description=f"Syntopik v{__version__} — Universal AI Deep-Mastery Operating System",
    )
    parser.add_argument("-v", "--version", action="version", version=f"syntopik {__version__}")

    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="Initialize a Syntopik workspace or ~/.syntopik vault (non-destructive)")
    p_init.add_argument("dir", nargs="?", default=None, help="Target workspace directory")
    p_init.add_argument("--workspace", default=None, help="Explicit workspace directory override")
    p_init.add_argument("--kg-dir", default=None, help="Explicit knowledge-graph directory override")
    p_init.add_argument("--vault", choices=["global", "local"], default=None, help="Vault shortcut (~/.syntopik or ./.syntopik)")
    p_init.add_argument("--json", action="store_true", help="Emit JSON output")
    p_init.set_defaults(func=_cmd_init)

    p_att = sub.add_parser("attach", help="Non-destructively attach Syntopik to any repository (zero git diff)")
    p_att.add_argument("dir", nargs="?", default=".", help="Target repository directory")
    p_att.add_argument(
        "--harness",
        choices=["auto", "all", *ALL_HARNESSES],
        default="auto",
        help="Target AI harness adapter(s) to wire (auto detects installed harnesses across 14 coding agents)",
    )
    p_att.add_argument("--vault", choices=["global", "local"], default="global", help="Study vault location")
    p_att.add_argument(
        "--transport",
        choices=["stdio", "http"],
        default="stdio",
        help="MCP transport mode: stdio (default) or loopback Streamable HTTP (http://127.0.0.1:4173/mcp)",
    )
    p_att.add_argument(
        "--http-url",
        dest="http_url",
        default=DEFAULT_HTTP_MCP_URL,
        help="Loopback Streamable-HTTP MCP URL when --transport http is selected",
    )
    p_att.add_argument("--copy-skills", action="store_true", help="Copy .agents/skills instead of symlinking")
    p_att.add_argument("--json", action="store_true", help="Emit JSON output")
    p_att.set_defaults(func=_cmd_attach)

    p_conn = sub.add_parser("connect", help="Install or show global plugin/extension setup for a harness")
    p_conn.add_argument("harness", choices=list(ALL_HARNESSES), help="Target harness")
    p_conn.add_argument("--bundle", choices=["studio", "full"], default="full", help="Antigravity plugin bundle flavor")
    p_conn.add_argument("--workspace", default=None, help="Syntopik workspace root")
    p_conn.add_argument("--target", default=None, help="Custom installation target directory")
    p_conn.add_argument("--json", action="store_true", help="Emit JSON output")
    p_conn.set_defaults(func=_cmd_connect)

    p_srv = sub.add_parser("serve", help="Start the zero-dependency 2-way local Studio web server")
    p_srv.add_argument("--workspace", default=None, help="Syntopik workspace directory")
    p_srv.add_argument("--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1)")
    p_srv.add_argument("--port", type=int, default=4173, help="Port (auto-increments if in use; 0 for ephemeral)")
    p_srv.add_argument("--lan", action="store_true", help="Opt-in LAN Pocket QR pairing (scoped strictly to Pocket)")
    p_srv.add_argument("--open", action="store_true", help="Open local Studio in default web browser")
    p_srv.set_defaults(func=_cmd_serve)

    p_imp = sub.add_parser(
        "import",
        help="Universal source importer (Anki .apkg, CSV/TSV cards, Jupyter .ipynb, Notion/MD folders, YouTube, arXiv, repo, URL, notes, Pocket sync)",
    )
    p_imp.add_argument("target", nargs="?", default=None, help="URL, arXiv ID, repository URL/path, or local file/directory")
    p_imp.add_argument("--pocket-sync", dest="pocket_sync", default=None, help="Replay an offline pocket-sync.json file through FSRS-6 + FIRe")
    p_imp.add_argument("--no-fire", action="store_true", help="Disable FIRe ancestor credit when replaying --pocket-sync")
    p_imp.add_argument("--dry-run", action="store_true", help="Preview import without mutating ledgers")
    p_imp.add_argument(
        "--kind",
        default="auto",
        help="auto, youtube, arxiv, repo, url, notes, anki, cards, notebook, notes_batch, or an importer plugin "
        "name such as kindle (see --list-importers)",
    )
    p_imp.add_argument("--list-importers", action="store_true", help="List importer plugins (built-in, installed, local)")
    p_imp.add_argument("--limit", type=int, default=None, help="Max cards a plugin importer may add (default 500, cap 2000)")
    p_imp.add_argument("--workspace", default=None, help="Syntopik workspace directory")
    p_imp.add_argument("--slug", "--chapter", dest="slug", default=None, help="Chapter/source slug override (alias: --chapter)")
    p_imp.add_argument("--subject", default="General", help="Subject tag")
    p_imp.add_argument("--register-tracker", action="store_true", help="Register imported source in chapter tracker")
    p_imp.add_argument("--json", action="store_true", help="Emit JSON output")
    p_imp.set_defaults(func=_cmd_import)

    p_rev = sub.add_parser("review", help="Run FIRe-compressed FSRS-6 review, TUI loop, recovery, re-entry, or Studio Flashcards")
    p_rev.add_argument("--workspace", default=None, help="Syntopik workspace directory")
    p_rev.add_argument("--web", action="store_true", help="Launch live Studio in browser")
    p_rev.add_argument("--tui", action="store_true", help="Interactive terminal flashcard loop")
    p_rev.add_argument("--recover", action="store_true", help="Calm backlog recovery plan (smooth overdue tail)")
    p_rev.add_argument("--daily-cap", type=_int_between(1, 500), default=15, help="Max reviews/day during --recover (default: 15)")
    p_rev.add_argument("--days", type=_int_between(1, 90), default=6, help="Recovery window in days during --recover (default: 6)")
    p_rev.add_argument("--apply", action="store_true", help="Persist smoothed schedule when using --recover")
    p_rev.add_argument("--resume", action="store_true", help="Zero-guilt Cold-Start Re-Entry Brief")
    p_rev.add_argument("--energy", choices=["low", "normal", "deep"], default=None, help="Cognitive energy mode")
    p_rev.add_argument("--port", type=int, default=4173, help="Port when using --web")
    p_rev.add_argument("--limit", type=int, default=25, help="Max items")
    p_rev.add_argument(
        "--new",
        type=_int_between(0, 200),
        default=10,
        help="With --tui: also introduce up to N never-reviewed concepts after the due ones (default: 10, 0 = off)",
    )
    p_rev.add_argument("--compress", action="store_true", default=True, help="Apply FIRe SRC compression")
    p_rev.add_argument("--bury-siblings", action="store_true", default=True, help="Bury same-proposition sibling cues")
    p_rev.add_argument("--json", action="store_true", help="Emit JSON output")
    p_rev.set_defaults(func=_cmd_review)

    p_tri = sub.add_parser("triage", help="48-hour Pareto exam triage plan + Anti-Mastery 1-page crib sheet")
    p_tri.add_argument("--hours", type=float, default=None, help="Hours until exam (default: 48)")
    p_tri.add_argument("--exam-in", dest="exam_in", default=None, help="Time until exam, e.g. '48h', '2d', '36' (aliases --hours)")
    p_tri.add_argument("--sleep-hours", type=float, default=7.0, help="Protected sleep hours per night (default: 7)")
    p_tri.add_argument("--chapter", default=None, help="Optional chapter filter")
    p_tri.add_argument("--crib-sheet", action="store_true", help="Also generate Anti-Mastery crib sheet spec")
    p_tri.add_argument("--out", default=None, help="Optional output path for crib sheet spec")
    p_tri.add_argument("--workspace", default=None, help="Syntopik workspace directory")
    p_tri.add_argument("--json", action="store_true", help="Emit JSON output")
    p_tri.set_defaults(func=_cmd_triage)

    p_pack = sub.add_parser("pack", help="Export, inspect, verify, or import zero-PII Study Packs")
    p_pack.add_argument("pack_args", nargs=argparse.REMAINDER, help="Arguments passed to syntopik.packs")
    p_pack.set_defaults(func=_cmd_pack)

    p_doc = sub.add_parser("doctor", help="Run workspace, runtime, and packaging health diagnostics")
    p_doc.add_argument("--workspace", default=None, help="Workspace directory override")
    p_doc.add_argument("--core-only", action="store_true", help="Check pure-stdlib core without optional STEM .venv")
    p_doc.add_argument("--full", action="store_true", help="Also check every optional STEM extra, even when none are installed")
    p_doc.add_argument("--compat", action="store_true", help="Show cross-OS 14-coding-agent compatibility matrix")
    p_doc.add_argument("--fix", action="store_true", help="Non-destructively initialize missing ledgers, repair .claude/skills, and sync graph-data.js before checking")
    p_doc.add_argument("--json", action="store_true", help="Emit JSON output")
    p_doc.set_defaults(func=_cmd_doctor)

    p_exp = sub.add_parser("export", help="Export Knowledge Graph to Obsidian, Pocket HTML, Anki, Study Pack, JSON, or Mastery Dossier")
    p_exp.add_argument(
        "format",
        nargs="?",
        choices=["obsidian", "pocket", "anki", "anki-text", "pack", "json", "dossier"],
        default=None,
        help="Export target format",
    )
    p_exp.add_argument("--pocket", action="store_true", help="Shortcut flag for 'syntopik export pocket'")
    p_exp.add_argument("--out", "--vault", dest="out", default=None, help="Output file or directory (alias: --vault)")
    p_exp.add_argument("--target", default=None, help="Target chapter/subject (for pack export)")
    p_exp.add_argument("--chapter", default=None, help="Chapter filter")
    p_exp.add_argument("--subject", default=None, help="Subject filter (for Anki export)")
    p_exp.add_argument("--limit", type=int, default=25, help="Card limit (for pocket export)")
    p_exp.add_argument("--workspace", default=None, help="Syntopik workspace directory")
    p_exp.add_argument("--json", action="store_true", help="Emit JSON output")
    p_exp.set_defaults(func=_cmd_export)

    p_sync = sub.add_parser("sync", help="Rebuild knowledge-graph/graph-data.js (schemaVersion 4)")
    p_sync.add_argument("--workspace", default=None, help="Syntopik workspace directory")
    p_sync.add_argument("--json", action="store_true", help="Emit JSON output")
    p_sync.set_defaults(func=_cmd_sync)

    p_run = sub.add_parser("run", help="Run any bundled Syntopik engine script from any workspace")
    p_run.add_argument("--workspace", default=None, help="Syntopik workspace directory")
    p_run.add_argument("engine", nargs="?", default=None, help="Engine script name (e.g. srs_engine, math_verify, sync-graph.mjs); omit to list engines")
    p_run.add_argument("engine_args", nargs=argparse.REMAINDER, help="Arguments forwarded to the engine script")
    p_run.set_defaults(func=_cmd_run)

    # --- v3.0 First-Class Verbs ---

    p_next = sub.add_parser("next", help="Plan the single optimal next pedagogical move (Brainstem)")
    p_next.add_argument("--energy", choices=["low", "normal", "deep"], default=None, help="Current cognitive energy")
    p_next.add_argument("--domain", default=None, help="Optional subject/domain scope")
    p_next.add_argument("--event", default=None, help="Optional trigger event context")
    p_next.add_argument("--workspace", default=None, help="Syntopik workspace directory")
    p_next.add_argument("--json", action="store_true", help="Emit JSON output")
    p_next.set_defaults(func=_cmd_next)

    p_prof = sub.add_parser("profile", help="Inspect, calibrate, compile policy, override, or reset Adaptive Learner Profile")
    p_prof.add_argument("action", nargs="?", choices=["status", "calibrate", "policy", "override", "reset"], default="status")
    p_prof.add_argument("--domain", default=None, help="Optional domain scope")
    p_prof.add_argument("--energy", choices=["low", "normal", "deep"], default=None, help="Cognitive energy override")
    p_prof.add_argument("--entry-ramp", dest="entry_ramp", default=None, choices=["intuition_first", "balanced", "formalism_first"], help="Calibration entry ramp preference")
    p_prof.add_argument("--challenge-appetite", dest="challenge_appetite", default=None, choices=["guided", "balanced", "adversarial"], help="Challenge appetite preference")
    p_prof.add_argument("--session-minutes", dest="session_minutes", type=int, default=None, help="Target session length in minutes")
    p_prof.add_argument("--prose-density", dest="prose_density", choices=["terse", "standard", "rich"], default=None)
    p_prof.add_argument("--set", dest="set_kv", action="append", default=[], help="Override KEY=VALUE (repeatable)")
    p_prof.add_argument("--workspace", default=None, help="Syntopik workspace directory")
    p_prof.add_argument("--json", action="store_true", help="Emit JSON output")
    p_prof.set_defaults(func=_cmd_profile)

    p_drop = sub.add_parser("drop", help="Drop mixed sources (PDF, URL, YouTube, repo, notes) with Teacher-First spine classification")
    p_drop.add_argument("inputs", nargs="+", help="One or more file paths, directories, or URLs")
    p_drop.add_argument("--slug", default=None, help="Chapter slug override")
    p_drop.add_argument("--title", default=None, help="Chapter title override")
    p_drop.add_argument("--workspace", default=None, help="Syntopik workspace directory")
    p_drop.add_argument("--json", action="store_true", help="Emit JSON output")
    p_drop.set_defaults(func=_cmd_drop)

    p_demo = sub.add_parser("demo", help="Load bundled zero-PII starter pack(s) into a clean demo workspace")
    p_demo.add_argument("--pack", choices=[*STARTER_PACK_SLUGS, "all"], default="all", help="Starter pack to load (default: all)")
    p_demo.add_argument("--workspace", default=None, help="Target demo workspace (default: ~/.syntopik-demo)")
    p_demo.add_argument("--open", action="store_true", help="Launch local Studio server in browser after loading")
    p_demo.add_argument("--port", type=int, default=4173, help="Port when using --open")
    p_demo.add_argument("--json", action="store_true", help="Emit JSON output")
    p_demo.set_defaults(func=_cmd_demo)

    p_deb = sub.add_parser("debrief", help="Crystallize git diff bugs/invariants into Forks, Arenas, and KG propositions")
    p_deb.add_argument("--ref", default="HEAD~1..HEAD", help="Git diff ref range (default: HEAD~1..HEAD)")
    p_deb.add_argument("--diff-file", dest="diff_file", default=None, help="Optional unified diff file instead of git diff")
    p_deb.add_argument("--dry-run", action="store_true", help="Analyze diff without writing to ledgers")
    p_deb.add_argument("--workspace", default=None, help="Syntopik workspace directory")
    p_deb.add_argument("--json", action="store_true", help="Emit JSON output")
    p_deb.set_defaults(func=_cmd_debrief)

    p_mode = sub.add_parser("mode", help="Toggle Anti-Vibe-Coding Work Mentor mode vs Study mode")
    p_mode.add_argument("mode", nargs="?", choices=["mentor", "study"], default=None, help="Mode to activate (omit to show the current mode)")
    p_mode.add_argument("--off", action="store_true", help="Disable mentor mode")
    p_mode.add_argument("--workspace", default=None, help="Syntopik workspace directory")
    p_mode.add_argument("--json", action="store_true", help="Emit JSON output")
    p_mode.set_defaults(func=_cmd_mode)

    p_sun = sub.add_parser("sunset", help="Semester Sunset: snapshot chapter pack, keep core axioms at R=0.80, archive leaves")
    p_sun.add_argument("slug", help="Chapter slug to sunset")
    p_sun.add_argument("--apply", action="store_true", help="Write archive snapshot and mark archived leaves / axiom retention")
    p_sun.add_argument("--workspace", default=None, help="Syntopik workspace directory")
    p_sun.add_argument("--json", action="store_true", help="Emit JSON output")
    p_sun.set_defaults(func=_cmd_sunset)

    p_pace = sub.add_parser("pace", help="Multi-course buffer-protected Auto-Pacer with RFC-5545 .ics export")
    p_pace.add_argument("--rebalance", action="store_true", help="Rebalance schedule across remaining buffer days")
    p_pace.add_argument("--ics", default=None, help="Export calendar schedule to an .ics file")
    p_pace.add_argument("--add-exam", dest="add_exam", default=None, metavar="NAME", help="Add (or update) an exam to pace toward; needs --date")
    p_pace.add_argument("--date", default=None, help="Exam date for --add-exam (YYYY-MM-DD)")
    p_pace.add_argument("--chapter", default=None, help="Optional chapter slug the exam covers (with --add-exam)")
    p_pace.add_argument("--remove-exam", dest="remove_exam", default=None, metavar="NAME", help="Remove an exam by name")
    p_pace.add_argument("--minutes", type=_int_between(15, 720), default=None, help="Set daily study time in minutes (15-720)")
    p_pace.add_argument("--workspace", default=None, help="Syntopik workspace directory")
    p_pace.add_argument("--json", action="store_true", help="Emit JSON output")
    p_pace.set_defaults(func=_cmd_pace)

    p_brd = sub.add_parser("board", help="Inspect or push frames/pins to the live AI-to-Whiteboard SSE bus")
    p_brd.add_argument("action", nargs="?", choices=["status", "push", "pin", "clear"], default="status")
    p_brd.add_argument("--kind", default="summary", help="Frame kind for push")
    p_brd.add_argument("--title", default="", help="Frame title for push")
    p_brd.add_argument("--content", default="", help="Frame content for push")
    p_brd.add_argument("--frame-json", dest="frame_json", default=None, help="Raw JSON object or @path for push")
    p_brd.add_argument("--step-index", dest="step_index", type=int, default=0, help="Step index for pin")
    p_brd.add_argument("--note", default="", help="Pin note text")
    p_brd.add_argument("--workspace", default=None, help="Syntopik workspace directory")
    p_brd.add_argument("--json", action="store_true", help="Emit JSON output")
    p_brd.set_defaults(func=_cmd_board)

    p_bench = sub.add_parser("bench", help="Run the seeded 60-day multi-persona Syntopik-Eval cognitive benchmark")
    p_bench.add_argument("--days", type=int, default=60, help="Simulation horizon in days (default: 60)")
    p_bench.add_argument("--seed", type=int, default=42, help="Deterministic RNG seed (default: 42)")
    p_bench.add_argument("--svg", action="store_true", help="Write ablation & calibration SVG charts")
    p_bench.add_argument("--markdown", action="store_true", help="Print GitHub Markdown benchmark table")
    p_bench.add_argument("--check", action="store_true", help="Assert strict CI regression invariants")
    p_bench.add_argument("--workspace", default=None, help="Syntopik workspace directory")
    p_bench.add_argument("--json", action="store_true", help="Emit JSON output")
    p_bench.set_defaults(func=_cmd_bench)

    # --- v3.1 First-Class Verbs ---

    p_ros = sub.add_parser("rosetta", help="Structural Isomorphism Rosetta Engine (scan, bridge, link, archetypes)")
    p_ros.add_argument("action", nargs="?", choices=["scan", "bridge", "link", "archetypes"], default="scan")
    p_ros.add_argument("--prop-id", dest="prop_id", default=None, help="Target proposition ID (for bridge)")
    p_ros.add_argument("--source", default=None, help="Source proposition ID (for link)")
    p_ros.add_argument("--target", default=None, help="Target proposition ID (for link)")
    p_ros.add_argument("--archetype", default=None, help="Archetype ID (for link)")
    p_ros.add_argument("--breaks-at", dest="breaks_at", action="append", default=[], help="Optional negative-transfer boundary note (repeatable)")
    p_ros.add_argument("--min-score", dest="min_score", type=float, default=None, help="Minimum archetype match score (for scan)")
    p_ros.add_argument("--workspace", default=None, help="Syntopik workspace directory")
    p_ros.add_argument("--json", action="store_true", help="Emit JSON output")
    p_ros.set_defaults(func=_cmd_rosetta)

    p_sd = sub.add_parser("sync-delta", help="CRDT Multi-Device Sync Deltas (export, merge, verify, status)")
    p_sd.add_argument("action", nargs="?", choices=["export", "merge", "verify", "status"], default="status")
    p_sd.add_argument("--out", default=None, help="Output delta file path (for export)")
    p_sd.add_argument("--file", default=None, help="Input delta file path (for verify/merge)")
    p_sd.add_argument("--since", default=None, help="Optional ISO-8601 cutoff timestamp (for export)")
    p_sd.add_argument("--dry-run", dest="dry_run", action="store_true", help="Preview merge without mutating workspace files")
    p_sd.add_argument(
        "--encrypt",
        action="store_true",
        help="Encrypt the exported file with a passphrase (AES-256-GCM; needs the 'sync' extra). "
        "Passphrase comes from SYNTOPIK_SYNC_PASSPHRASE or a prompt; verify/merge detect encrypted files",
    )
    p_sd.add_argument("--workspace", default=None, help="Syntopik workspace directory")
    p_sd.add_argument("--json", action="store_true", help="Emit JSON output")
    p_sd.set_defaults(func=_cmd_sync_delta)

    p_cal = sub.add_parser("calibrate-policy", help="Replay longitudinal learner history (70/30 OOS split) to calibrate hyperparameters")
    p_cal.add_argument("--apply", action="store_true", help="Persist calibratedHyperparams if OOS composite loss improves")
    p_cal.add_argument("--min-events", dest="min_events", type=int, default=None, help="Minimum confidence-rated reviews required")
    p_cal.add_argument("--workspace", default=None, help="Syntopik workspace directory")
    p_cal.add_argument("--json", action="store_true", help="Emit JSON output")
    p_cal.set_defaults(func=_cmd_calibrate_policy)

    p_pdiff = sub.add_parser("proof-diff", help="Structured Proof-Graph Handwriting Diff against a Causal Step DAG")
    p_pdiff.add_argument("--id", required=True, help="Derivation ID in derivations.jsonl")
    p_pdiff.add_argument("--steps-json", dest="steps_json", required=True, help="JSON array, file path, or @path of student steps")
    p_pdiff.add_argument("--no-spawn-fork", dest="no_spawn_fork", action="store_true", help="Do not auto-spawn Blunder FEN fork or log mistake on break")
    p_pdiff.add_argument("--workspace", default=None, help="Syntopik workspace directory")
    p_pdiff.add_argument("--kg-dir", dest="kg_dir", default=None, help="Explicit knowledge-graph directory override")
    p_pdiff.add_argument("--json", action="store_true", help="Emit JSON output")
    p_pdiff.set_defaults(func=_cmd_proof_diff)

    # --- v3.2 First-Class Verbs ---

    p_rdy = sub.add_parser("readiness", help="Exam-Day Readiness Forecaster, <=72h Pre-Exam Taper Mode & Pre-Mortem Shield")
    p_rdy.add_argument("--exam-date", dest="exam_date", default=None, type=_iso_date_arg, help="Target exam date (YYYY-MM-DD)")
    p_rdy.add_argument("--subject", default=None, help="Optional subject filter")
    p_rdy.add_argument("--max-marks", dest="max_marks", type=float, default=None, help="Optional exam maximum marks")
    p_rdy.add_argument("--premortem-only", dest="premortem_only", action="store_true", help="Emit only the evidence-backed Pre-Mortem Shield bullets")
    p_rdy.add_argument("--workspace", default=None, help="Syntopik workspace directory")
    p_rdy.add_argument("--json", action="store_true", help="Emit JSON output")
    p_rdy.set_defaults(func=_cmd_readiness)

    p_rep = sub.add_parser("report", help="Weekly progress report: reviews, streak, true retention, mastery, due load and focus")
    p_rep.add_argument("--days", type=_int_between(1, 365), default=7, help="Window length in days, compared with the window before (default: 7)")
    p_rep.add_argument("--md", default=None, help="Also write the report as Markdown to this path")
    p_rep.add_argument("--workspace", default=None, help="Syntopik workspace directory")
    p_rep.add_argument("--json", action="store_true", help="Emit JSON output")
    p_rep.set_defaults(func=_cmd_report)

    p_ilv = sub.add_parser("interleave", help="Blind-Chapter Interleaved Drill Generator (Rohrer interleaving + Newman Tier-1)")
    p_ilv.add_argument("action", nargs="?", choices=["build", "check", "complete", "stats", "export-cbt"], default="build")
    p_ilv.add_argument("--n", type=int, default=6, help="Number of interleaved problems (default: 6)")
    p_ilv.add_argument("--subjects", nargs="+", action="append", default=None, help="Subject filter(s) (space or comma separated)")
    p_ilv.add_argument("--mode", default="principle_first", help="Interleave mode (default: principle_first)")
    p_ilv.add_argument("--seed", type=int, default=None, help="Deterministic RNG seed")
    p_ilv.add_argument("--session-id", dest="session_id", default=None, help="Interleave session ID (for check/complete)")
    p_ilv.add_argument("--item-id", dest="item_id", default=None, help="Item ID within session (for check)")
    p_ilv.add_argument("--principle", default=None, help="Selected governing principle (for check)")
    p_ilv.add_argument("--confidence", type=float, default=0.5, help="Confidence 0..1 (for check, default: 0.5)")
    p_ilv.add_argument("--duration-ms", dest="duration_ms", type=int, default=None, help="Optional response duration in ms (for check)")
    p_ilv.add_argument("--results-json", dest="results_json", default=None, help="Optional JSON or @path of step results (for complete)")
    p_ilv.add_argument("--out", default=None, help="Optional output file path (for export-cbt)")
    p_ilv.add_argument("--workspace", default=None, help="Syntopik workspace directory")
    p_ilv.add_argument("--json", action="store_true", help="Emit JSON output")
    p_ilv.add_argument("--tui", action="store_true", help="Build a set and drill it right here in the terminal, one question at a time")
    p_ilv.set_defaults(func=_cmd_interleave)

    return parser


QUICKSTART = """Syntopik v{version} — study anything until it clicks.

Start here:
  syntopik demo              Try it with a ready-made sample course
  syntopik drop <file|url>   Add your own notes, PDF, slides, video or website
  syntopik next              See the one best thing to do right now
  syntopik review            Go through what is due for review today
  syntopik serve --open      Open the study dashboard in your browser

Getting ready for a test:
  syntopik pace --add-exam NAME --date YYYY-MM-DD   Set an exam date and get a study pace
  syntopik readiness         How ready you are, and what to fix first
  syntopik triage --exam-in 48h   Short plan when the exam is close
  syntopik interleave --tui  Mixed questions from all chapters, like a real exam

Something wrong?  syntopik doctor --fix
All commands:     syntopik --help
"""


def _command_names(parser: argparse.ArgumentParser) -> list[str]:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return list(action.choices)
    return []


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    raw = list(sys.argv[1:] if argv is None else argv)
    if not raw:
        print(QUICKSTART.format(version=__version__), end="")
        return 0
    first = raw[0]
    if not first.startswith("-"):
        names = _command_names(parser)
        if names and first not in names:
            close = difflib.get_close_matches(first, names, n=3, cutoff=0.6)
            hint = f" Did you mean: {', '.join(close)}?" if close else ""
            print(f"syntopik: unknown command '{first}'.{hint}", file=sys.stderr)
            print("Run 'syntopik' on its own for a short guide, or 'syntopik --help' for every command.", file=sys.stderr)
            return 2
    args = parser.parse_args(raw)
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        print("\nStopped.", file=sys.stderr)
        return 130
    except BrokenPipeError:
        # Output piped into something that stopped reading (e.g. `| head`): exit quietly.
        try:
            devnull = os.open(os.devnull, os.O_WRONLY)
            os.dup2(devnull, sys.stdout.fileno())
        except (OSError, ValueError):
            pass
        return 141
    except (ValueError, FileNotFoundError, PermissionError, TimeoutError) as exc:
        # Bad input or a missing/locked file: a plain one-line message, not a traceback.
        if os.environ.get("SYNTOPIK_DEBUG", "").strip() in ("1", "true", "yes"):
            raise
        cmd = getattr(args, "command", None) or (first if not first.startswith("-") else "")
        prefix = f"syntopik {cmd}" if cmd else "syntopik"
        print(f"{prefix}: {exc}", file=sys.stderr)
        print("(Set SYNTOPIK_DEBUG=1 to see the full error.)", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
