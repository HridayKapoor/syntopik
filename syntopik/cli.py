"""Unified pure-stdlib CLI for Syntopik v3.1 (`syntopik`).

Verbs:
  init | attach | connect | serve | import | review | triage | pack | doctor | export | sync | run
  next | profile | drop | demo | debrief | mode | sunset | pace | board | bench
  rosetta | sync-delta | calibrate-policy | proof-diff
"""

from __future__ import annotations

import argparse
import importlib
import inspect
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from syntopik import __version__
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

ALL_HARNESSES: tuple[str, ...] = (
    "cursor",
    "windsurf",
    "copilot",
    "opencode",
    "codex",
    "claude",
    "antigravity",
    "gemini",
)

DEFAULT_FALLBACK_HARNESSES: tuple[str, ...] = ("claude", "codex", "cursor")

STARTER_PACK_SLUGS: tuple[str, ...] = (
    "raft-consensus",
    "shm-physics",
    "transformers-attention",
)


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
    unique_entries = list(dict.fromkeys(e for e in entries if e))
    block = "\n".join([begin_marker, *unique_entries, end_marker])

    start_idx = existing.find(begin_marker)
    end_idx = existing.find(end_marker)
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
    if dest.exists() or dest.is_symlink():
        return "preserved", False
    abs_src = src.resolve()
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not force_copy:
        try:
            os.symlink(str(abs_src), str(dest), target_is_directory=True)
            if dest.is_dir():
                return "symlinked", True
        except OSError:
            pass
        if os.name == "nt":
            try:
                proc = subprocess.run(
                    ["cmd", "/c", "mklink", "/J", str(dest), str(abs_src)],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if proc.returncode == 0 and dest.is_dir():
                    return "junction", True
            except OSError:
                pass
    shutil.copytree(abs_src, dest, dirs_exist_ok=True)
    return "copied", True


def _detect_installed_harnesses(target: Path, *, home: Path | None = None) -> list[str]:
    """Detect harnesses installed globally or present in `target` without dumping all 8 blindly."""
    home_dir = Path(home).expanduser().resolve() if home is not None else Path.home()
    detected: list[str] = []

    has_agy_home = (home_dir / ".gemini" / "antigravity").is_dir()
    checks: list[tuple[str, bool]] = [
        ("claude", (target / ".claude").exists() or (home_dir / ".claude").is_dir()),
        ("codex", (target / ".codex").exists() or (home_dir / ".codex").is_dir()),
        ("cursor", (target / ".cursor").exists() or (home_dir / ".cursor").is_dir()),
        (
            "windsurf",
            (target / ".windsurf").exists()
            or (home_dir / ".windsurf").is_dir()
            or (home_dir / ".codeium" / "windsurf").is_dir(),
        ),
        (
            "antigravity",
            (target / ".agents" / "hooks.json").is_file()
            or (target / "GEMINI.md").is_file()
            or has_agy_home,
        ),
        (
            "gemini",
            (target / ".gemini").exists()
            or (home_dir / ".gemini" / "settings.json").is_file()
            or (home_dir / ".gemini" / "extensions").is_dir()
            or ((home_dir / ".gemini").is_dir() and not has_agy_home),
        ),
        (
            "copilot",
            (target / ".vscode").exists() or (target / ".github" / "instructions").exists(),
        ),
        (
            "opencode",
            (target / "opencode.json").is_file() or (home_dir / ".config" / "opencode").is_dir(),
        ),
    ]
    for name, present in checks:
        if present:
            detected.append(name)

    return detected if detected else list(DEFAULT_FALLBACK_HARNESSES)


def _hook_list_has_command(hook_list: list[Any], command_str: str) -> bool:
    for item in hook_list:
        if not isinstance(item, dict):
            continue
        if item.get("command") == command_str:
            return True
        inner = item.get("hooks")
        if isinstance(inner, list):
            for h in inner:
                if isinstance(h, dict) and h.get("command") == command_str:
                    return True
    return False


def _append_dedup_hook(hook_list: list[Any], entry: dict[str, Any], command_str: str) -> None:
    if not _hook_list_has_command(hook_list, command_str):
        hook_list.append(entry)


def _inspect_antigravity_plugin(home: Path | None = None) -> dict[str, Any]:
    home_dir = Path(home).expanduser().resolve() if home is not None else Path.home()
    plugins_root = home_dir / ".gemini" / "config" / "plugins"
    for name in ("syntopik", "syntopik-studio", "mlh-studio"):
        pdir = plugins_root / name
        if (pdir / "plugin.json").is_file():
            return {
                "installed": True,
                "pluginName": name,
                "pluginDir": str(pdir),
                "hasDistServer": (pdir / "dist" / "server.mjs").is_file(),
            }
    return {
        "installed": False,
        "pluginName": None,
        "pluginDir": str(plugins_root / "syntopik"),
        "hasDistServer": False,
    }


def attach_workspace(
    target_dir: str | Path,
    *,
    harness: str = "auto",
    vault: str = "global",
    copy_skills: bool = False,
) -> dict[str, Any]:
    """Non-destructively wire Syntopik into any existing repository without touching git status.

    Contract:
      - NEVER overwrites an existing host `AGENTS.md` or `CLAUDE.md`.
      - Writes local exclusions strictly to `.git/info/exclude` (zero diff to host `.gitignore`).
      - Initializes `~/.syntopik` (`vault="global"`) or `<dir>/.syntopik` (`vault="local"`).
      - Uses 100% cross-platform Windows-safe commands (zero inline POSIX `VAR=... cmd`).
    """
    target = Path(target_dir).expanduser().resolve()
    target.mkdir(parents=True, exist_ok=True)

    harness_low = str(harness or "auto").strip().lower()
    if harness_low not in ("auto", "all", *ALL_HARNESSES):
        raise ValueError(f"Unsupported harness {harness!r}; expected auto, all, or one of {ALL_HARNESSES}.")
    vault_low = str(vault or "global").strip().lower()
    if vault_low not in ("global", "local"):
        raise ValueError(f"Unsupported vault {vault!r}; expected 'global' or 'local'.")

    pkg_root = resolve_package_root()
    vault_path = (target / ".syntopik").resolve() if vault_low == "local" else (Path.home() / ".syntopik").resolve()
    vault_display = ".syntopik/" if vault_low == "local" else "~/.syntopik/"

    setup = _ensure_script_import("setup_harness", pkg_root)
    vault_init = setup.ensure_learner_workspace(vault_path, run_sync=True, repair_skills=False)

    if harness_low == "all":
        selected = list(ALL_HARNESSES)
    elif harness_low == "auto":
        selected = _detect_installed_harnesses(target)
    else:
        selected = [harness_low]

    server_mjs = _resolve_bundled_path(pkg_root, "mcp-app/mlh-studio/dist/server.mjs", "mcp-dist/server.mjs").resolve()
    skills_src = _resolve_bundled_path(pkg_root, ".agents/skills", "skills").resolve()
    hooks_src = _resolve_bundled_path(pkg_root, ".agents/hooks", "hooks").resolve()

    session_hook_cmd = f'node "{(hooks_src / "session-start.mjs").resolve()}" --workspace "{vault_path}"'
    post_hook_cmd = f'node "{(hooks_src / "post-graph-sync.mjs").resolve()}" --workspace "{vault_path}"'

    exclude_entries: list[str] = [".syntopik/"]
    written: list[str] = []
    preserved: list[str] = []
    notes: list[str] = []

    # Strictly preserve any host AGENTS.md / CLAUDE.md
    for host_doc in ("AGENTS.md", "CLAUDE.md"):
        if (target / host_doc).exists():
            preserved.append(host_doc)

    # Link or copy canonical `.agents/skills`
    if skills_src.is_dir():
        dest_skills = target / ".agents" / "skills"
        action, created = _link_or_copy_dir(skills_src, dest_skills, force_copy=copy_skills)
        if created:
            written.append(f".agents/skills ({action})")
            exclude_entries.extend([".agents/skills", ".agents/skills/"])
        else:
            preserved.append(".agents/skills")

    if "cursor" in selected:
        rule_path = target / ".cursor" / "rules" / "syntopik.mdc"
        existed_rule = rule_path.is_file()
        write_text_atomic(
            rule_path,
            (
                "---\n"
                "description: Syntopik universal deep-mastery co-pilot rules and study vault pointer\n"
                "alwaysApply: false\n"
                "---\n\n"
                f"For learning, deep-understanding, review, or exam-prep tasks, activate Syntopik skills in `.agents/skills/` "
                f"and persist study state to `{vault_display}`. Keep turns <= 160w prose, 1 active demand/turn, zero sycophancy, "
                "and verify math/code deterministically via `syntopik run <engine>`.\n"
            ),
        )
        written.append(".cursor/rules/syntopik.mdc")
        if not existed_rule:
            exclude_entries.append(".cursor/rules/syntopik.mdc")

        cursor_mcp = target / ".cursor" / "mcp.json"
        existed_mcp = _merge_json_file(
            cursor_mcp,
            lambda doc: doc.setdefault("mcpServers", {}).update({
                "syntopik-studio": {
                    "command": "node",
                    "args": [str(server_mjs), f"--workspace={vault_path}"],
                }
            }),
        )
        written.append(".cursor/mcp.json")
        if not existed_mcp:
            exclude_entries.append(".cursor/mcp.json")

    if "windsurf" in selected:
        ws_rule = target / ".windsurf" / "rules" / "syntopik.md"
        existed_ws = ws_rule.is_file()
        write_text_atomic(
            ws_rule,
            (
                "---\n"
                "trigger: model_decision\n"
                "description: Syntopik universal deep-mastery co-pilot rules and study vault pointer\n"
                "---\n\n"
                f"For learning, study, revision, or concept-mastery requests, follow Syntopik skills in `.agents/skills/` "
                f"and persist study state in `{vault_display}`. Keep turns <= 160w prose, 1 demand/turn, zero sycophancy, "
                "and run deterministic verifiers via `syntopik run <engine>`.\n"
            ),
        )
        written.append(".windsurf/rules/syntopik.md")
        if not existed_ws:
            exclude_entries.append(".windsurf/rules/syntopik.md")

    if "copilot" in selected:
        gh_inst = target / ".github" / "instructions" / "syntopik.instructions.md"
        existed_gh = gh_inst.is_file()
        write_text_atomic(
            gh_inst,
            (
                "---\n"
                'applyTo: "**"\n'
                "---\n\n"
                f"When asked to teach, probe, review, or verify STEM/CS concepts, follow Syntopik skills in `.agents/skills/` "
                f"(vault: `{vault_display}`): <= 160w calm turns, 1 active demand/turn, zero sycophancy, and deterministic verification "
                "via `syntopik run <engine>`.\n"
            ),
        )
        written.append(".github/instructions/syntopik.instructions.md")
        if not existed_gh:
            exclude_entries.append(".github/instructions/syntopik.instructions.md")

        vscode_mcp = target / ".vscode" / "mcp.json"
        existed_vs = _merge_json_file(
            vscode_mcp,
            lambda doc: doc.setdefault("servers", {}).update({
                "syntopik-studio": {
                    "type": "stdio",
                    "command": "node",
                    "args": [str(server_mjs), f"--workspace={vault_path}"],
                    "cwd": "${workspaceFolder}",
                }
            }),
        )
        written.append(".vscode/mcp.json")
        if not existed_vs:
            exclude_entries.append(".vscode/mcp.json")

    if "opencode" in selected:
        oc_json = target / "opencode.json"
        existed_oc = _merge_json_file(
            oc_json,
            lambda doc: doc.setdefault("mcp", {}).update({
                "syntopik-studio": {
                    "type": "local",
                    "command": ["node", str(server_mjs), f"--workspace={vault_path}"],
                    "environment": {"SYNT_WORKSPACE": str(vault_path)},
                    "enabled": True,
                }
            }),
        )
        written.append("opencode.json")
        if not existed_oc:
            exclude_entries.append("opencode.json")

    if "codex" in selected:
        codex_toml = target / ".codex" / "config.toml"
        existed_codex = codex_toml.is_file()
        prev_toml = codex_toml.read_text(encoding="utf-8") if existed_codex else ""
        if "[mcp_servers.syntopik-studio]" not in prev_toml:
            esc_server = str(server_mjs).replace("\\", "\\\\").replace('"', '\\"')
            esc_vault = str(vault_path).replace("\\", "\\\\").replace('"', '\\"')
            snippet = (
                "[mcp_servers.syntopik-studio]\n"
                'command = "node"\n'
                f'args = ["{esc_server}", "--workspace={esc_vault}"]\n'
                "startup_timeout_sec = 10\n"
                "tool_timeout_sec = 60\n"
            )
            next_toml = (prev_toml.rstrip() + "\n\n" + snippet) if prev_toml.strip() else snippet
            write_text_atomic(codex_toml, next_toml)
        written.append(".codex/config.toml")
        if not existed_codex:
            exclude_entries.append(".codex/config.toml")

    if "claude" in selected:
        root_mcp = target / ".mcp.json"
        existed_root_mcp = _merge_json_file(
            root_mcp,
            lambda doc: doc.setdefault("mcpServers", {}).update({
                "syntopik-studio": {
                    "type": "stdio",
                    "command": "node",
                    "args": [str(server_mjs), f"--workspace={vault_path}"],
                }
            }),
        )
        written.append(".mcp.json")
        if not existed_root_mcp:
            exclude_entries.append(".mcp.json")

        claude_settings = target / ".claude" / "settings.json"

        def _wire_claude_hooks(doc: dict[str, Any]) -> None:
            hooks = doc.setdefault("hooks", {})
            if not isinstance(hooks, dict):
                hooks = {}
                doc["hooks"] = hooks
            if not isinstance(hooks.get("SessionStart"), list):
                hooks["SessionStart"] = []
            if not isinstance(hooks.get("PostToolUse"), list):
                hooks["PostToolUse"] = []

            _append_dedup_hook(
                hooks["SessionStart"],
                {
                    "hooks": [
                        {
                            "type": "command",
                            "command": session_hook_cmd,
                            "timeout": 10,
                        }
                    ]
                },
                session_hook_cmd,
            )
            _append_dedup_hook(
                hooks["PostToolUse"],
                {
                    "matcher": "Write|Edit|MultiEdit|NotebookEdit|Bash",
                    "hooks": [
                        {
                            "type": "command",
                            "command": post_hook_cmd,
                            "timeout": 10,
                        }
                    ],
                },
                post_hook_cmd,
            )

        existed_cs = _merge_json_file(claude_settings, _wire_claude_hooks)
        written.append(".claude/settings.json")
        if not existed_cs:
            exclude_entries.append(".claude/settings.json")

        claude_skills = target / ".claude" / "skills"
        dest_skills = target / ".agents" / "skills"
        if dest_skills.exists():
            sk_action, sk_created = _link_or_copy_dir(dest_skills.resolve(), claude_skills, force_copy=copy_skills)
            if sk_created:
                written.append(f".claude/skills ({sk_action})")
                exclude_entries.extend([".claude/skills", ".claude/skills/"])
            else:
                preserved.append(".claude/skills")

    antigravity_info: dict[str, Any] | None = None
    if "antigravity" in selected:
        agy_hooks = target / ".agents" / "hooks.json"

        def _wire_agy_hooks(doc: dict[str, Any]) -> None:
            ss_group = doc.setdefault("study-session-context", {})
            if not isinstance(ss_group, dict):
                ss_group = {}
                doc["study-session-context"] = ss_group
            if not isinstance(ss_group.get("SessionStart"), list):
                ss_group["SessionStart"] = []
            _append_dedup_hook(
                ss_group["SessionStart"],
                {"type": "command", "command": session_hook_cmd, "timeout": 10},
                session_hook_cmd,
            )

            pt_group = doc.setdefault("knowledge-graph-autosync", {})
            if not isinstance(pt_group, dict):
                pt_group = {}
                doc["knowledge-graph-autosync"] = pt_group
            if not isinstance(pt_group.get("PostToolUse"), list):
                pt_group["PostToolUse"] = []
            _append_dedup_hook(
                pt_group["PostToolUse"],
                {
                    "matcher": "write_file|edit_file|replace_file_content|multi_replace_file_content|write_to_file|run_command",
                    "type": "command",
                    "command": post_hook_cmd,
                    "timeout": 10,
                },
                post_hook_cmd,
            )

        existed_agy_hooks = _merge_json_file(agy_hooks, _wire_agy_hooks)
        written.append(".agents/hooks.json")
        if not existed_agy_hooks:
            exclude_entries.append(".agents/hooks.json")

        antigravity_info = _inspect_antigravity_plugin()
        antigravity_info["workspaceHooks"] = ".agents/hooks.json"
        if antigravity_info["installed"]:
            notes.append(
                f"Antigravity plugin detected ({antigravity_info['pluginName']} at {antigravity_info['pluginDir']}); workspace hooks wired in .agents/hooks.json."
            )
        else:
            notes.append(
                "Antigravity workspace hooks wired in .agents/hooks.json (host AGENTS.md preserved). Run `syntopik connect antigravity` to install the global Studio MCP plugin."
            )

    if "gemini" in selected:
        gem_settings = target / ".gemini" / "settings.json"
        existed_gem = _merge_json_file(
            gem_settings,
            lambda doc: doc.setdefault("mcpServers", {}).update({
                "syntopik-studio": {
                    "command": "node",
                    "args": [str(server_mjs), f"--workspace={vault_path}"],
                }
            }),
        )
        written.append(".gemini/settings.json")
        if not existed_gem:
            exclude_entries.append(".gemini/settings.json")

    git_exclude = _update_git_exclude(target, exclude_entries)
    res: dict[str, Any] = {
        "ok": True,
        "targetDir": str(target),
        "vaultMode": vault_low,
        "vaultPath": str(vault_path),
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
    )
    lines = [
        f"Syntopik attached to {res['targetDir']}",
        f"  Vault      : {res['vaultPath']} ({res['vaultMode']})",
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
            "note": "Installs 17 skills + .windsurf/rules/syntopik.md.",
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
            "note": "Installs 17 skills + opencode.json (mcp local).",
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

    if not args.target:
        raise SystemExit("Provide a source target OR pass --pocket-sync <pocket-sync.json>.")

    from syntopik import ingest

    kind = str(args.kind or "auto").strip().lower()
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
    res = fn(args.target, **kwargs)
    sync_hub(workspace=ws, quiet=True)
    _emit(
        res,
        bool(args.json),
        [
            f"Imported ({res.get('kind', kind)}): {res.get('title') or args.target}",
            f"  Output : {res.get('outputPath') or res.get('outDir') or ws}",
        ],
    )
    return 0


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


def _run_review_tui(ws: Path, kg_dir: Path, payload: dict[str, Any], *, energy: str | None = None) -> int:
    items = list(payload.get("items") or [])
    if energy == "low":
        items = items[:3]
    if not items:
        print(f"Syntopik Review TUI ({ws}): 0 items due.")
        return 0

    props_by_id = {
        str(p.get("id")): p
        for p in load_jsonl(kg_dir / "propositions.jsonl")
        if isinstance(p, dict) and p.get("id")
    }
    srs_mod = _ensure_script_import("srs_engine", ws)

    print(f"Syntopik Review TUI — {len(items)} due item(s) (Ctrl-C or 'q' to stop)")
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

    for idx, item in enumerate(items, 1):
        pid = str(item.get("id") or "")
        prop = props_by_id.get(pid) or {}
        cues = [c for c in (prop.get("cues") or []) if isinstance(c, dict) and (c.get("q") or c.get("a"))]
        cue = cues[0] if cues else None
        prompt_text = str((cue or {}).get("q") or prop.get("prompt") or item.get("title") or pid)
        answer_text = str((cue or {}).get("a") or prop.get("statement") or item.get("title") or "")
        notes_text = str(item.get("notes") or prop.get("notes") or "")

        print("")
        print(f"[{idx}/{len(items)}] {pid} ({item.get('chapter') or 'General'} · {item.get('status') or 'due'})")
        print(f"  Prompt: {prompt_text}")
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

        try:
            raw_rate = input("  Rate [1=again, 2=hard, 3=good, 4=easy, s=skip, q=quit] (+ optional confidence %): ").strip()
        except (EOFError, KeyboardInterrupt):
            print("")
            break
        if not raw_rate or raw_rate.lower() in ("s", "skip"):
            continue
        if raw_rate.lower() in ("q", "quit"):
            break

        parts = raw_rate.split()
        rating = rating_map.get(parts[0].lower())
        if not rating:
            print(f"  Unrecognized rating {parts[0]!r}; skipped.")
            continue
        conf: float | None = None
        if len(parts) >= 2:
            try:
                conf = float(parts[1].rstrip("%"))
            except ValueError:
                conf = None

        srs_mod.record_review(
            kg_dir,
            pid,
            rating,
            cue_id=(cue or {}).get("id"),
            confidence=conf,
            no_sync=True,
        )
        reviewed += 1
        print(f"  Recorded '{rating}' for {pid}.")

    if reviewed > 0:
        sync_hub(workspace=ws, quiet=True)
    print(f"Review TUI complete: {reviewed}/{len(items)} card(s) recorded.")
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
        return _run_review_tui(ws, kg_dir, data, energy=args.energy)

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
    for ln in lines:
        print(ln)
    return 0


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
    _emit(payload, bool(args.json), lines)
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
    fix_actions: list[str] = []

    if getattr(args, "fix", False):
        target_ws = ws if ws is not None else resolve_workspace()
        setup = _ensure_script_import("setup_harness", target_ws)
        boot = setup.ensure_learner_workspace(target_ws, run_sync=True, repair_skills=True)
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
    report = doctor.run_doctor(workspace=ws, core_only=bool(args.core_only))
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
    return 0 if report.get("healthy") else 1


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
        raise SystemExit("Specify an export format (obsidian|pocket|anki|pack|json|dossier) or pass --pocket.")

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
    engine = str(args.engine).strip()
    if "/" in engine or "\\" in engine or ".." in engine:
        raise SystemExit(f"Invalid engine name: {engine!r}")

    candidates_names = [engine]
    if not engine.endswith((".py", ".mjs")):
        candidates_names.extend([f"{engine}.py", f"{engine}.mjs"])

    script_path: Path | None = None
    for base in (ws / "scripts", pkg_root / "scripts"):
        for name in candidates_names:
            cand = base / name
            if cand.is_file():
                script_path = cand
                break
        if script_path is not None:
            break

    if script_path is None:
        raise SystemExit(f"Engine script '{engine}' not found in workspace or bundled scripts/.")

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


def _cmd_next(args: argparse.Namespace) -> int:
    ws = resolve_workspace(getattr(args, "workspace", None))
    brainstem = _lazy_import("syntopik.brainstem")
    res = brainstem.plan_next_move(
        ws,
        event=getattr(args, "event", None),
        energy=getattr(args, "energy", None),
        domain=getattr(args, "domain", None),
    )
    lines = [
        f"Next Move: {res.get('recommendedMode', 'unknown')}",
        f"  Target   : {res.get('targetNodeId') or res.get('chapterSlug') or 'frontier'}",
        f"  Rationale: {res.get('rationale', '')}",
    ]
    if res.get("exactCmd"):
        lines.append(f"  Command  : {res['exactCmd']}")
    _emit(res, bool(args.json), lines)
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


def _cmd_profile(args: argparse.Namespace) -> int:
    ws = resolve_workspace(getattr(args, "workspace", None))
    kg_dir = resolve_kg_dir(ws)
    lm = _lazy_import("syntopik.learner_model")
    action = str(args.action or "status").strip().lower()

    if action == "status":
        profile = lm.load_profile(kg_dir)
        policy_lines = lm.format_compact_policy_lines(profile, domain=args.domain)
        payload = {"ok": True, "action": "status", "workspace": str(ws), "profile": profile, "policyLines": policy_lines}
        _emit(payload, bool(args.json), ["Syntopik Learner Profile:", *[f"  {ln}" for ln in policy_lines]])
        return 0

    if action == "calibrate":
        profile = lm.load_profile(kg_dir)
        updated = lm.apply_tier1_calibration(
            profile,
            entry_ramp=args.entry_ramp,
            challenge_appetite=args.challenge_appetite,
            session_minutes=args.session_minutes if args.session_minutes is not None else 30,
            prose_density=args.prose_density or "standard",
        )
        lm.save_profile(kg_dir, updated)
        policy_lines = lm.format_compact_policy_lines(updated, domain=args.domain)
        payload = {"ok": True, "action": "calibrate", "workspace": str(ws), "profile": updated, "policyLines": policy_lines}
        _emit(payload, bool(args.json), ["Learner profile calibrated:", *[f"  {ln}" for ln in policy_lines]])
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
    sync_hub(workspace=ws, quiet=True)
    lines = [
        f"Dropped {len(args.inputs)} source(s) into {ws}",
        f"  Slug : {res.get('slug') or args.slug or 'auto'}",
    ]
    if res.get("probeOneHint"):
        lines.append(f"  Next : {res['probeOneHint']}")
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
    res = wm.debrief_git_diff(ws, **kwargs)
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


def _cmd_pace(args: argparse.Namespace) -> int:
    ws = resolve_workspace(getattr(args, "workspace", None))
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
        res = sd.export_sync_delta(ws, out_path=args.out, since=args.since)
        payload = {"ok": bool(res.get("ok", True)), "action": "export", "workspace": str(ws), **(res if isinstance(res, dict) else {})}
        lines = [
            f"Exported CRDT sync delta ({ws})",
            f"  Output : {payload.get('outPath') or payload.get('path') or '(in-memory)'}",
        ]
        _emit(payload, bool(args.json), lines)
        return 0 if payload.get("ok", True) else 1

    if action == "verify":
        target_file = args.file or args.out
        if not target_file:
            raise SystemExit("Provide --file <delta_path> for 'syntopik sync-delta verify'.")
        res = sd.verify_sync_delta(target_file)
        ok = bool(res.get("ok", False)) if isinstance(res, dict) else False
        payload = {"ok": ok, "action": "verify", **(res if isinstance(res, dict) else {})}
        _emit(payload, bool(args.json), [f"Sync delta verify ({target_file}): {'VALID' if ok else 'INVALID'}"])
        return 0 if ok else 1

    if action == "merge":
        target_file = args.file or args.out
        if not target_file:
            raise SystemExit("Provide --file <delta_path> for 'syntopik sync-delta merge'.")
        res = sd.merge_sync_delta(ws, target_file, dry_run=bool(args.dry_run))
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
        help="Target AI harness adapter(s) to wire (auto detects installed harnesses)",
    )
    p_att.add_argument("--vault", choices=["global", "local"], default="global", help="Study vault location")
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

    p_imp = sub.add_parser("import", help="Universal source importer (YouTube, arXiv, GitHub repo, URL, PDF/notes, Pocket sync)")
    p_imp.add_argument("target", nargs="?", default=None, help="URL, arXiv ID, repository URL/path, or local file/directory")
    p_imp.add_argument("--pocket-sync", dest="pocket_sync", default=None, help="Replay an offline pocket-sync.json file through FSRS-6 + FIRe")
    p_imp.add_argument("--no-fire", action="store_true", help="Disable FIRe ancestor credit when replaying --pocket-sync")
    p_imp.add_argument("--dry-run", action="store_true", help="Preview --pocket-sync without mutating ledgers")
    p_imp.add_argument("--kind", choices=["auto", "youtube", "arxiv", "repo", "url", "notes"], default="auto")
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
    p_rev.add_argument("--daily-cap", type=int, default=15, help="Max reviews/day during --recover (default: 15)")
    p_rev.add_argument("--days", type=int, default=6, help="Recovery window in days during --recover (default: 6)")
    p_rev.add_argument("--apply", action="store_true", help="Persist smoothed schedule when using --recover")
    p_rev.add_argument("--resume", action="store_true", help="Zero-guilt Cold-Start Re-Entry Brief")
    p_rev.add_argument("--energy", choices=["low", "normal", "deep"], default=None, help="Cognitive energy mode")
    p_rev.add_argument("--port", type=int, default=4173, help="Port when using --web")
    p_rev.add_argument("--limit", type=int, default=25, help="Max items")
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
    p_doc.add_argument("--fix", action="store_true", help="Non-destructively initialize missing ledgers, repair .claude/skills, and sync graph-data.js before checking")
    p_doc.add_argument("--json", action="store_true", help="Emit JSON output")
    p_doc.set_defaults(func=_cmd_doctor)

    p_exp = sub.add_parser("export", help="Export Knowledge Graph to Obsidian, Pocket HTML, Anki, Study Pack, JSON, or Mastery Dossier")
    p_exp.add_argument(
        "format",
        nargs="?",
        choices=["obsidian", "pocket", "anki", "pack", "json", "dossier"],
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
    p_run.add_argument("engine", help="Engine script name (e.g. srs_engine, math_verify, sync-graph.mjs)")
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
    p_prof.add_argument("--entry-ramp", dest="entry_ramp", default="balanced", help="Calibration entry ramp preference")
    p_prof.add_argument("--challenge-appetite", dest="challenge_appetite", default="balanced", help="Challenge appetite preference")
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
    p_mode.add_argument("mode", nargs="?", choices=["mentor", "study"], default="mentor", help="Mode to activate")
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

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
