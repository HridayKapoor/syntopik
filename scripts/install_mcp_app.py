#!/usr/bin/env python3
"""Install, inspect, or uninstall the Syntopik SEP-1865 MCP App or full Antigravity plugin.

Supports two bundles (zero `node_modules` copied in either mode!):
  - `--bundle studio` (default): `syntopik-studio` MCP App (`plugin.json`, `assets/logo.svg`,
    `mcp_config.json`, `dist/{server.mjs,quiz-card.html,practice-card.html,studio-pane.html}`).
  - `--bundle full`: global Antigravity `syntopik` plugin (`~/.gemini/config/plugins/syntopik/`)
    containing `skills/`, `rules/AGENTS.md`, `hooks.json`, `hooks/`, `scripts/`, `syntopik/` (if present),
    and the pre-bundled Studio MCP App.

Examples:
    python3 scripts/install_mcp_app.py status --json
    python3 scripts/install_mcp_app.py install --target /tmp/test-plugin --workspace "$PWD"
    python3 scripts/install_mcp_app.py install --bundle full --target /tmp/test-full-plugin --workspace "$PWD"
    python3 scripts/install_mcp_app.py uninstall --target /tmp/test-plugin
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

PLUGIN_NAME = "syntopik-studio"
FULL_PLUGIN_NAME = "syntopik"
LEGACY_PLUGIN_NAME = "mlh-studio"
ACCEPTED_PLUGIN_NAMES = {PLUGIN_NAME, FULL_PLUGIN_NAME, LEGACY_PLUGIN_NAME}
SERVER_KEY = "syntopik-studio"
SRC_DIR_NAME = "mlh-studio"
ROOT = Path(__file__).resolve().parent.parent
SRC_PLUGIN_DIR = ROOT / "mcp-app" / SRC_DIR_NAME


def _is_bundle_root(path: Path) -> bool:
    return bool({"_bundle", "site-packages", "dist-packages"}.intersection(path.parts))


def _resolve_default_workspace() -> Path:
    if not _is_bundle_root(ROOT):
        return ROOT
    try:
        from syntopik.paths import resolve_workspace

        return resolve_workspace()
    except Exception:
        return (Path.home() / ".syntopik").resolve()


def _resolve_plugin_file(rel: str) -> Path:
    repo_cand = SRC_PLUGIN_DIR / rel
    if repo_cand.is_file():
        return repo_cand
    if rel == "plugin.json":
        for cand in (ROOT / "mcp" / "plugin.json", ROOT / "mcp-plugin.json"):
            if cand.is_file():
                return cand
    elif rel == "assets/logo.svg":
        for cand in (ROOT / "mcp" / "assets" / "logo.svg", ROOT / "assets" / "logo.svg"):
            if cand.is_file():
                return cand
    elif rel.startswith("dist/"):
        cand = ROOT / "mcp-dist" / rel[len("dist/") :]
        if cand.is_file():
            return cand
    return repo_cand


def _resolve_skills_dir() -> Path:
    for cand in (ROOT / ".agents" / "skills", ROOT / "skills"):
        if cand.is_dir():
            return cand
    return ROOT / ".agents" / "skills"


def _resolve_hooks_dir() -> Path:
    for cand in (ROOT / ".agents" / "hooks", ROOT / "hooks"):
        if cand.is_dir():
            return cand
    return ROOT / ".agents" / "hooks"


def _resolve_agents_md() -> Path:
    return ROOT / "AGENTS.md"


def _resolve_syntopik_pkg_dir() -> Path | None:
    if (ROOT / "syntopik").is_dir():
        return ROOT / "syntopik"
    if ROOT.name == "_bundle" and ROOT.parent.name == "syntopik" and (ROOT.parent / "__init__.py").is_file():
        return ROOT.parent
    return None


def _default_plugin_target(bundle: str = "studio") -> Path:
    plugins_root = Path.home() / ".gemini" / "config" / "plugins"
    if bundle == "full":
        return plugins_root / FULL_PLUGIN_NAME
    new_dir = plugins_root / PLUGIN_NAME
    legacy_dir = plugins_root / LEGACY_PLUGIN_NAME
    if new_dir.exists():
        return new_dir
    if legacy_dir.exists():
        return legacy_dir
    return new_dir


DEFAULT_TARGET = _default_plugin_target("studio")

REQUIRED_DIST_FILES = [
    "dist/server.mjs",
    "dist/quiz-card.html",
    "dist/practice-card.html",
    "dist/studio-pane.html",
]
REQUIRED_RUNTIME_FILES = [
    "plugin.json",
    "mcp_config.json",
    "assets/logo.svg",
    *REQUIRED_DIST_FILES,
]
REQUIRED_FULL_EXTRA_FILES = [
    "rules/AGENTS.md",
    "hooks.json",
    "hooks/session-start.mjs",
    "hooks/post-graph-sync.mjs",
]


def resolve_node_command(explicit: str | None) -> str:
    if explicit and explicit.strip():
        return explicit.strip()
    found = shutil.which("node")
    if found:
        return found
    for candidate in (Path("/opt/homebrew/bin/node"), Path("/usr/local/bin/node")):
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return "node"


def build_mcp_config(node_cmd: str, workspace: Path) -> dict[str, Any]:
    return {
        "mcpServers": {
            SERVER_KEY: {
                "command": node_cmd,
                "args": [
                    "${PLUGIN_ROOT}/dist/server.mjs",
                    f"--workspace={workspace}",
                    "--data-dir=${PLUGIN_DATA}",
                ],
            }
        }
    }


def build_full_plugin_manifest() -> dict[str, Any]:
    return {
        "name": FULL_PLUGIN_NAME,
        "displayName": "Syntopik",
        "version": "2.0.0",
        "description": (
            "Universal AI Deep-Mastery OS: 17 mastery skills, calm tutoring rules, "
            "live Knowledge Graph hooks, and SEP-1865 Studio MCP App."
        ),
        "logo": "assets/logo.svg",
        "suggestedPrompts": [
            "Open Syntopik Studio and review my due FSRS-6 flashcards",
            "Start a guided deep-mastery chapter on Distributed Consensus",
            "Run a 5-minute interleaved tune-up from my Knowledge Graph",
        ],
    }


def build_full_hooks_config() -> dict[str, Any]:
    return {
        "study-session-context": {
            "SessionStart": [
                {
                    "type": "command",
                    "command": 'node "${PLUGIN_ROOT}/hooks/session-start.mjs"',
                    "timeout": 10,
                }
            ]
        },
        "knowledge-graph-autosync": {
            "PostToolUse": [
                {
                    "matcher": (
                        "write_file|edit_file|replace_file_content|"
                        "multi_replace_file_content|write_to_file|run_command"
                    ),
                    "hooks": [
                        {
                            "type": "command",
                            "command": 'node "${PLUGIN_ROOT}/hooks/post-graph-sync.mjs"',
                            "timeout": 10,
                        }
                    ],
                }
            ]
        },
    }


def _copy_tree_clean(src_dir: Path, dst_dir: Path, rel_root: Path) -> tuple[list[str], int]:
    copied: list[str] = []
    total_bytes = 0
    if not src_dir.is_dir():
        return copied, total_bytes
    for root_str, dirnames, filenames in os.walk(src_dir):
        dirnames[:] = sorted(
            d for d in dirnames if d not in ("__pycache__", "node_modules", ".git", ".pytest_cache", "_bundle")
        )
        cur_src = Path(root_str)
        rel_sub = cur_src.relative_to(src_dir)
        cur_dst = dst_dir / rel_sub
        cur_dst.mkdir(parents=True, exist_ok=True)
        for fname in sorted(filenames):
            if fname.startswith(".") or fname.endswith((".pyc", ".pyo")):
                continue
            s_file = cur_src / fname
            if not s_file.is_file():
                continue
            d_file = cur_dst / fname
            shutil.copy2(s_file, d_file)
            copied.append(d_file.relative_to(rel_root).as_posix())
            total_bytes += d_file.stat().st_size
    return copied, total_bytes


def emit(payload: dict[str, Any], as_json: bool, human_lines: list[str]) -> None:
    if as_json:
        print(json.dumps(payload, indent=2))
    else:
        for line in human_lines:
            print(line)


def cmd_install(args: argparse.Namespace) -> int:
    bundle = str(getattr(args, "bundle", "studio") or "studio").strip().lower()
    if bundle not in ("studio", "full"):
        msg = f"Invalid --bundle {bundle!r}; expected 'studio' or 'full'."
        if getattr(args, "json", False):
            print(json.dumps({"status": "error", "error": msg}, indent=2))
        else:
            print(f"ERROR: {msg}", file=sys.stderr)
        return 1

    raw_target = getattr(args, "target", None)
    target = (
        Path(raw_target).expanduser().resolve()
        if raw_target
        else _default_plugin_target(bundle).resolve()
    )
    workspace = Path(args.workspace).expanduser().resolve()
    node_cmd = resolve_node_command(getattr(args, "node", None))

    skills_src = _resolve_skills_dir()
    hooks_src = _resolve_hooks_dir()
    agents_md_src = _resolve_agents_md()

    # Verify source bundle exists (supports both direct repo checkout and wheel `_bundle/` layout)
    missing_src: list[str] = []
    for rel in ["plugin.json", "assets/logo.svg", *REQUIRED_DIST_FILES]:
        src_file = _resolve_plugin_file(rel)
        if not src_file.is_file() or src_file.stat().st_size == 0:
            missing_src.append(rel)
    if bundle == "full":
        for extra_p, label in (
            (agents_md_src, "AGENTS.md"),
            (skills_src, ".agents/skills"),
            (hooks_src / "session-start.mjs", ".agents/hooks/session-start.mjs"),
            (hooks_src / "post-graph-sync.mjs", ".agents/hooks/post-graph-sync.mjs"),
        ):
            if not extra_p.exists():
                missing_src.append(label)

    if missing_src:
        msg = (
            f"Missing pre-bundled plugin files: {', '.join(missing_src)}. "
            f"Run `cd mcp-app/{SRC_DIR_NAME} && npm run build` first."
        )
        if getattr(args, "json", False):
            print(json.dumps({"status": "error", "error": msg, "missing": missing_src}, indent=2))
        else:
            print(f"ERROR: {msg}", file=sys.stderr)
        return 1

    target.mkdir(parents=True, exist_ok=True)
    # Ensure zero node_modules remain in target plugin directory
    stale_nm = target / "node_modules"
    if stale_nm.exists():
        shutil.rmtree(stale_nm)

    copied_files: list[str] = []
    total_bytes = 0
    base_rels = ["assets/logo.svg", *REQUIRED_DIST_FILES]
    if bundle == "studio":
        base_rels.insert(0, "plugin.json")

    for rel in base_rels:
        src = _resolve_plugin_file(rel)
        dst = target / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        copied_files.append(rel)
        total_bytes += dst.stat().st_size

    plugin_name = FULL_PLUGIN_NAME if bundle == "full" else PLUGIN_NAME
    skills_count = 0

    if bundle == "full":
        pj_path = target / "plugin.json"
        pj_path.write_text(json.dumps(build_full_plugin_manifest(), indent=2) + "\n", encoding="utf-8")
        copied_files.append("plugin.json")
        total_bytes += pj_path.stat().st_size

        # rules/AGENTS.md (plain markdown, zero frontmatter)
        rules_dst = target / "rules" / "AGENTS.md"
        rules_dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(agents_md_src, rules_dst)
        copied_files.append("rules/AGENTS.md")
        total_bytes += rules_dst.stat().st_size

        # skills/
        skills_dst = target / "skills"
        if skills_dst.exists():
            shutil.rmtree(skills_dst)
        s_files, s_bytes = _copy_tree_clean(skills_src, skills_dst, target)
        copied_files.extend(s_files)
        total_bytes += s_bytes
        skills_count = sum(1 for f in s_files if f.endswith("/SKILL.md"))

        # hooks/ + hooks.json
        hooks_dst = target / "hooks"
        hooks_dst.mkdir(parents=True, exist_ok=True)
        for h_name in ("session-start.mjs", "post-graph-sync.mjs"):
            h_src = hooks_src / h_name
            h_dst = hooks_dst / h_name
            shutil.copy2(h_src, h_dst)
            copied_files.append(f"hooks/{h_name}")
            total_bytes += h_dst.stat().st_size

        hooks_cfg_path = target / "hooks.json"
        hooks_cfg_path.write_text(json.dumps(build_full_hooks_config(), indent=2) + "\n", encoding="utf-8")
        copied_files.append("hooks.json")
        total_bytes += hooks_cfg_path.stat().st_size

        # Bundled runtime scripts + pure-stdlib syntopik/ package (when present) for cross-repo execution
        scripts_dst = target / "scripts"
        if scripts_dst.exists():
            shutil.rmtree(scripts_dst)
        sc_files, sc_bytes = _copy_tree_clean(ROOT / "scripts", scripts_dst, target)
        copied_files.extend(sc_files)
        total_bytes += sc_bytes

        pkg_src = _resolve_syntopik_pkg_dir()
        if pkg_src is not None and pkg_src.is_dir():
            pkg_dst = target / "syntopik"
            if pkg_dst.exists():
                shutil.rmtree(pkg_dst)
            pk_files, pk_bytes = _copy_tree_clean(pkg_src, pkg_dst, target)
            copied_files.extend(pk_files)
            total_bytes += pk_bytes

    mcp_cfg = build_mcp_config(node_cmd, workspace)
    mcp_cfg_path = target / "mcp_config.json"
    mcp_cfg_path.write_text(json.dumps(mcp_cfg, indent=2) + "\n", encoding="utf-8")
    copied_files.append("mcp_config.json")
    total_bytes += mcp_cfg_path.stat().st_size

    payload: dict[str, Any] = {
        "status": "installed",
        "bundle": bundle,
        "pluginName": plugin_name,
        "serverKey": SERVER_KEY,
        "target": str(target),
        "workspace": str(workspace),
        "nodeCommand": node_cmd,
        "files": copied_files,
        "totalBytes": total_bytes,
        "hasNodeModules": (target / "node_modules").exists(),
    }
    if bundle == "full":
        payload["skillsCount"] = skills_count
        payload["hasHooks"] = (target / "hooks.json").is_file()
        payload["hasRules"] = (target / "rules" / "AGENTS.md").is_file()

    human = [
        f"Installed {plugin_name} plugin ({bundle} bundle) to: {target}",
        f"Workspace: {workspace}",
        f"Node command: {node_cmd}",
        f"Files copied: {len(copied_files)} ({total_bytes / 1024:.1f} KiB, zero node_modules)",
    ]
    if bundle == "full":
        human.append(f"Full bundle assets: {skills_count} skills, rules/AGENTS.md, hooks.json, Studio MCP")
    emit(payload, bool(getattr(args, "json", False)), human)
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    bundle_hint = getattr(args, "bundle", None)
    raw_target = getattr(args, "target", None)
    if raw_target:
        target = Path(raw_target).expanduser().resolve()
    elif bundle_hint == "full":
        target = _default_plugin_target("full").resolve()
    else:
        target = DEFAULT_TARGET.resolve()

    installed = target.is_dir()
    missing_files: list[str] = []
    bundle_bytes = 0
    plugin_name: str | None = None
    configured_workspace: str | None = None
    configured_command: str | None = None
    resolved_server_key = SERVER_KEY
    valid_json = True
    detected_bundle = "studio"
    skills_count = 0

    if installed:
        pj = target / "plugin.json"
        if pj.is_file():
            try:
                plugin_name = json.loads(pj.read_text(encoding="utf-8")).get("name")
            except Exception:
                valid_json = False

        if plugin_name == FULL_PLUGIN_NAME or (target / "skills").is_dir() or bundle_hint == "full":
            detected_bundle = "full"

        required = (
            [*REQUIRED_RUNTIME_FILES, *REQUIRED_FULL_EXTRA_FILES]
            if detected_bundle == "full"
            else list(REQUIRED_RUNTIME_FILES)
        )
        for rel in required:
            p = target / rel
            if not p.is_file() or p.stat().st_size == 0:
                missing_files.append(rel)
            else:
                bundle_bytes += p.stat().st_size

        if detected_bundle == "full":
            skills_dir = target / "skills"
            if skills_dir.is_dir():
                skills_count = sum(1 for p in skills_dir.glob("*/SKILL.md") if p.is_file())
            if skills_count == 0:
                missing_files.append("skills/*/SKILL.md")

        mc = target / "mcp_config.json"
        if mc.is_file():
            try:
                cfg = json.loads(mc.read_text(encoding="utf-8"))
                servers = cfg.get("mcpServers", {})
                if isinstance(servers, dict):
                    srv = servers.get(SERVER_KEY) or servers.get(LEGACY_PLUGIN_NAME) or {}
                    if SERVER_KEY not in servers and LEGACY_PLUGIN_NAME in servers:
                        resolved_server_key = LEGACY_PLUGIN_NAME
                    configured_command = srv.get("command")
                    for a in srv.get("args", []):
                        if isinstance(a, str) and a.startswith("--workspace="):
                            configured_workspace = a[len("--workspace=") :]
            except Exception:
                valid_json = False
    else:
        detected_bundle = "full" if bundle_hint == "full" else "studio"
        missing_files = (
            [*REQUIRED_RUNTIME_FILES, *REQUIRED_FULL_EXTRA_FILES]
            if detected_bundle == "full"
            else list(REQUIRED_RUNTIME_FILES)
        )

    has_node_modules = (target / "node_modules").exists() if installed else False
    healthy = bool(
        installed
        and valid_json
        and not missing_files
        and plugin_name in ACCEPTED_PLUGIN_NAMES
        and not has_node_modules
    )

    payload: dict[str, Any] = {
        "installed": installed,
        "healthy": healthy,
        "bundle": detected_bundle,
        "pluginName": plugin_name,
        "serverKey": resolved_server_key,
        "target": str(target),
        "workspace": configured_workspace,
        "nodeCommand": configured_command,
        "missingFiles": missing_files,
        "bundleBytes": bundle_bytes,
        "hasNodeModules": has_node_modules,
    }
    if detected_bundle == "full":
        payload["skillsCount"] = skills_count
        payload["hasHooks"] = (target / "hooks.json").is_file()
        payload["hasRules"] = (target / "rules" / "AGENTS.md").is_file()

    emit(
        payload,
        bool(getattr(args, "json", False)),
        [
            f"Plugin {plugin_name or PLUGIN_NAME} ({detected_bundle}): {'INSTALLED (healthy)' if healthy else ('INSTALLED (unhealthy)' if installed else 'NOT INSTALLED')}",
            f"Target: {target}",
            f"Workspace: {configured_workspace or '(auto-resolved)'}",
            f"Bundle size: {bundle_bytes / 1024:.1f} KiB" + (f" · missing: {', '.join(missing_files)}" if missing_files else ""),
        ],
    )
    return 0


def cmd_uninstall(args: argparse.Namespace) -> int:
    bundle_hint = getattr(args, "bundle", None)
    raw_target = getattr(args, "target", None)
    if raw_target:
        target = Path(raw_target).expanduser().resolve()
    elif bundle_hint == "full":
        target = _default_plugin_target("full").resolve()
    else:
        target = DEFAULT_TARGET.resolve()

    if not target.exists():
        payload = {"status": "not_installed", "target": str(target)}
        emit(payload, bool(getattr(args, "json", False)), [f"{PLUGIN_NAME} is not installed at {target}."])
        return 0

    # Guardrail: refuse to delete an unrelated directory unless plugin.json identifies Syntopik
    pj = target / "plugin.json"
    safe_to_remove = False
    detected_name = PLUGIN_NAME
    if pj.is_file():
        try:
            data = json.loads(pj.read_text(encoding="utf-8"))
            if data.get("name") in ACCEPTED_PLUGIN_NAMES:
                safe_to_remove = True
                detected_name = str(data.get("name"))
        except Exception:
            safe_to_remove = False
    elif target.is_dir() and not any(target.iterdir()):
        safe_to_remove = True

    if not safe_to_remove:
        msg = f"Refusing to remove {target}: missing plugin.json with name in {sorted(ACCEPTED_PLUGIN_NAMES)}."
        if getattr(args, "json", False):
            print(json.dumps({"status": "error", "error": msg, "target": str(target)}, indent=2))
        else:
            print(f"ERROR: {msg}", file=sys.stderr)
        return 1

    shutil.rmtree(target)
    payload = {"status": "uninstalled", "pluginName": detected_name, "target": str(target)}
    emit(payload, bool(getattr(args, "json", False)), [f"Uninstalled {detected_name} from {target}."])
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Install, inspect, or uninstall the Syntopik Studio SEP-1865 MCP App or full Antigravity plugin."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_inst = sub.add_parser("install", help="Install pre-bundled Syntopik plugin (zero node_modules)")
    p_inst.add_argument(
        "--bundle",
        choices=["studio", "full"],
        default="studio",
        help="Bundle flavor: 'studio' (syntopik-studio MCP App) or 'full' (syntopik global skills+rules+hooks+MCP)",
    )
    p_inst.add_argument("--target", default=None, help="Target plugin directory (defaults by --bundle)")
    p_inst.add_argument("--workspace", default=str(_resolve_default_workspace()), help="Syntopik workspace root")
    p_inst.add_argument("--node", default=None, help="Explicit path to node executable")
    p_inst.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    p_inst.set_defaults(func=cmd_install)

    p_stat = sub.add_parser("status", help="Inspect Syntopik plugin installation health")
    p_stat.add_argument("--bundle", choices=["studio", "full"], default=None, help="Bundle flavor to inspect")
    p_stat.add_argument("--target", default=None, help="Target plugin directory")
    p_stat.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    p_stat.set_defaults(func=cmd_status)

    p_un = sub.add_parser("uninstall", help="Uninstall Syntopik plugin safely")
    p_un.add_argument("--bundle", choices=["studio", "full"], default=None, help="Bundle flavor to uninstall")
    p_un.add_argument("--target", default=None, help="Target plugin directory")
    p_un.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    p_un.set_defaults(func=cmd_uninstall)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
