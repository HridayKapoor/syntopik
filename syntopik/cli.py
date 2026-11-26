"""Unified pure-stdlib CLI for Syntopik v2.0 (`syntopik`).

Verbs:
  init | attach | connect | serve | import | review | triage | pack | doctor | export | sync | run
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import shutil
import subprocess
import sys
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
from syntopik.storage import load_json, save_json_atomic, write_text_atomic

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


def _ensure_script_import(module_name: str, workspace: Path | None = None) -> Any:
    pkg_root = resolve_package_root()
    candidates = [pkg_root / "scripts"]
    if workspace is not None:
        candidates.insert(0, Path(workspace).resolve() / "scripts")
    for base in candidates:
        if base.is_dir() and str(base) not in sys.path:
            sys.path.insert(0, str(base))
    return importlib.import_module(module_name)


def _find_python_for_workspace(workspace: Path, pkg_root: Path) -> str:
    for base in (workspace, pkg_root):
        for rel in (Path(".venv") / "bin" / "python", Path(".venv") / "Scripts" / "python.exe"):
            cand = base / rel
            if cand.is_file():
                return str(cand)
    return sys.executable


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
        after = existing[end_idx + len(end_marker):].lstrip()
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
    if dest.exists() or dest.is_symlink():
        return "preserved", False
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not force_copy:
        try:
            os.symlink(str(src), str(dest), target_is_directory=True)
            return "symlinked", True
        except OSError:
            pass
    shutil.copytree(src, dest, dirs_exist_ok=True)
    return "copied", True


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

    selected = list(ALL_HARNESSES) if harness_low in ("auto", "all") else [harness_low]
    server_mjs = _resolve_bundled_path(pkg_root, "mcp-app/mlh-studio/dist/server.mjs", "mcp-dist/server.mjs")
    skills_src = _resolve_bundled_path(pkg_root, ".agents/skills", "skills")
    hooks_src = _resolve_bundled_path(pkg_root, ".agents/hooks", "hooks")

    exclude_entries: list[str] = [".syntopik/"]
    written: list[str] = []
    preserved: list[str] = []

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
            if not isinstance(hooks.get("SessionStart"), list):
                hooks["SessionStart"] = [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": f'SYNT_WORKSPACE="{vault_path}" node "{hooks_src / "session-start.mjs"}"',
                                "timeout": 10,
                            }
                        ]
                    }
                ]
            if not isinstance(hooks.get("PostToolUse"), list):
                hooks["PostToolUse"] = [
                    {
                        "matcher": "Write|Edit|MultiEdit|NotebookEdit|Bash",
                        "hooks": [
                            {
                                "type": "command",
                                "command": f'SYNT_WORKSPACE="{vault_path}" node "{hooks_src / "post-graph-sync.mjs"}"',
                                "timeout": 10,
                            }
                        ],
                    }
                ]

        existed_cs = _merge_json_file(claude_settings, _wire_claude_hooks)
        written.append(".claude/settings.json")
        if not existed_cs:
            exclude_entries.append(".claude/settings.json")

        claude_skills = target / ".claude" / "skills"
        dest_skills = target / ".agents" / "skills"
        if not claude_skills.exists() and not claude_skills.is_symlink() and dest_skills.exists():
            rel = os.path.relpath(dest_skills, claude_skills.parent)
            try:
                claude_skills.parent.mkdir(parents=True, exist_ok=True)
                os.symlink(rel, claude_skills, target_is_directory=True)
                written.append(".claude/skills (symlinked)")
                exclude_entries.append(".claude/skills")
            except OSError:
                pass

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
    return {
        "ok": True,
        "targetDir": str(target),
        "vaultMode": vault_low,
        "vaultPath": str(vault_path),
        "harnesses": selected,
        "written": written,
        "preserved": preserved,
        "gitExclude": git_exclude,
        "vaultInit": vault_init,
    }


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


def _cmd_review(args: argparse.Namespace) -> int:
    ws = resolve_workspace(getattr(args, "workspace", None))
    if args.web:
        from syntopik.server import serve_forever

        return serve_forever(workspace=ws, port=args.port, open_browser=True)

    pkg_root = resolve_package_root()
    srs_script = pkg_root / "scripts" / "srs_engine.py"
    py_bin = _find_python_for_workspace(ws, pkg_root)
    cmd = [py_bin, "-B", str(srs_script), "due", "--kg-dir", str(resolve_kg_dir(ws))]
    if args.compress:
        cmd.append("--compress")
    if args.bury_siblings:
        cmd.append("--bury-siblings")
    if args.limit:
        cmd.extend(["--limit", str(args.limit)])
    if args.json:
        cmd.append("--json")
    proc = subprocess.run(cmd, check=False)
    return int(proc.returncode)


def _cmd_triage(args: argparse.Namespace) -> int:
    ws = resolve_workspace(getattr(args, "workspace", None))
    kg_dir = resolve_kg_dir(ws)
    chapters_dir = resolve_chapters_dir(ws)
    daily_plan = _ensure_script_import("daily_plan", ws)
    plan = daily_plan.build_triage_plan(
        hours_left=float(args.hours),
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
    doctor = _ensure_script_import("study_doctor", ws)
    report = doctor.run_doctor(workspace=ws, core_only=bool(args.core_only))
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        s = report["summary"]
        banner = "HEALTHY" if report["healthy"] else "ISSUES DETECTED"
        print(f"Syntopik Doctor — {banner} ({s['ok']}/{s['total']} ok, {s['warn']} warn, {s['fail']} fail · {report['elapsedMs']} ms)")
        print("-" * 88)
        for c in report["checks"]:
            tag = {"ok": "[ OK ]", "warn": "[WARN]", "fail": "[FAIL]"}[c["status"]]
            print(f"{tag} {c['name']:<36} {c['detail']}")
        print("-" * 88)
    return 0 if report.get("healthy") else 1


def _cmd_export(args: argparse.Namespace) -> int:
    ws = resolve_workspace(getattr(args, "workspace", None))
    kg_dir = resolve_kg_dir(ws)
    chapters_dir = resolve_chapters_dir(ws)
    fmt = str(args.format).strip().lower()

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

    if fmt == "anki":
        pkg_root = resolve_package_root()
        srs_script = pkg_root / "scripts" / "srs_engine.py"
        py_bin = _find_python_for_workspace(ws, pkg_root)
        cmd = [py_bin, "-B", str(srs_script), "export-anki", "--kg-dir", str(kg_dir)]
        if args.out:
            cmd.extend(["--out", str(Path(args.out).expanduser().resolve())])
        if args.chapter:
            cmd.extend(["--chapter", args.chapter])
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
        help="Target AI harness adapter(s) to wire",
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
    p_imp.add_argument("--slug", default=None, help="Chapter/source slug override")
    p_imp.add_argument("--subject", default="General", help="Subject tag")
    p_imp.add_argument("--register-tracker", action="store_true", help="Register imported source in chapter tracker")
    p_imp.add_argument("--json", action="store_true", help="Emit JSON output")
    p_imp.set_defaults(func=_cmd_import)

    p_rev = sub.add_parser("review", help="Run FIRe-compressed FSRS-6 due review or open Studio Flashcards")
    p_rev.add_argument("--workspace", default=None, help="Syntopik workspace directory")
    p_rev.add_argument("--web", action="store_true", help="Launch live Studio in browser")
    p_rev.add_argument("--port", type=int, default=4173, help="Port when using --web")
    p_rev.add_argument("--limit", type=int, default=25, help="Max items")
    p_rev.add_argument("--compress", action="store_true", default=True, help="Apply FIRe SRC compression")
    p_rev.add_argument("--bury-siblings", action="store_true", default=True, help="Bury same-proposition sibling cues")
    p_rev.add_argument("--json", action="store_true", help="Emit JSON output")
    p_rev.set_defaults(func=_cmd_review)

    p_tri = sub.add_parser("triage", help="48-hour Pareto exam triage plan + Anti-Mastery 1-page crib sheet")
    p_tri.add_argument("--hours", type=float, default=48.0, help="Hours until exam (default: 48)")
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
    p_doc.add_argument("--json", action="store_true", help="Emit JSON output")
    p_doc.set_defaults(func=_cmd_doctor)

    p_exp = sub.add_parser("export", help="Export Knowledge Graph to Obsidian, Pocket HTML, Anki, or Study Pack")
    p_exp.add_argument("format", choices=["obsidian", "pocket", "anki", "pack"], help="Export target format")
    p_exp.add_argument("--out", default=None, help="Output file or directory")
    p_exp.add_argument("--target", default=None, help="Target chapter/subject (for pack export)")
    p_exp.add_argument("--chapter", default=None, help="Chapter filter")
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

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
