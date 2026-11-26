#!/usr/bin/env python3
"""Install, inspect, or uninstall the official SEP-1865 Syntopik Studio MCP App plugin.

Copies ONLY zero-runtime-dependency pre-bundled artifacts (`plugin.json`, `assets/logo.svg`,
generated `mcp_config.json`, and `dist/{server.mjs,quiz-card.html,practice-card.html,studio-pane.html}`)
into the target Antigravity plugins directory — ZERO `node_modules` copied!

Examples:
    python3 scripts/install_mcp_app.py status --json
    python3 scripts/install_mcp_app.py install --target /tmp/test-plugin --workspace "$PWD"
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
LEGACY_PLUGIN_NAME = "mlh-studio"
ACCEPTED_PLUGIN_NAMES = {PLUGIN_NAME, LEGACY_PLUGIN_NAME}
SERVER_KEY = "syntopik-studio"
SRC_DIR_NAME = "mlh-studio"
ROOT = Path(__file__).resolve().parent.parent
SRC_PLUGIN_DIR = ROOT / "mcp-app" / SRC_DIR_NAME


def _default_plugin_target() -> Path:
    plugins_root = Path.home() / ".gemini" / "config" / "plugins"
    new_dir = plugins_root / PLUGIN_NAME
    legacy_dir = plugins_root / LEGACY_PLUGIN_NAME
    if new_dir.exists():
        return new_dir
    if legacy_dir.exists():
        return legacy_dir
    return new_dir


DEFAULT_TARGET = _default_plugin_target()

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


def emit(payload: dict[str, Any], as_json: bool, human_lines: list[str]) -> None:
    if as_json:
        print(json.dumps(payload, indent=2))
    else:
        for line in human_lines:
            print(line)


def cmd_install(args: argparse.Namespace) -> int:
    target = Path(args.target).expanduser().resolve()
    workspace = Path(args.workspace).expanduser().resolve()
    node_cmd = resolve_node_command(getattr(args, "node", None))

    # Verify source bundle exists
    missing_src: list[str] = []
    for rel in ["plugin.json", "assets/logo.svg", *REQUIRED_DIST_FILES]:
        src_file = SRC_PLUGIN_DIR / rel
        if not src_file.is_file() or src_file.stat().st_size == 0:
            missing_src.append(rel)
    if missing_src:
        msg = (
            f"Missing pre-bundled plugin files in {SRC_PLUGIN_DIR}: {', '.join(missing_src)}. "
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
    for rel in ["plugin.json", "assets/logo.svg", *REQUIRED_DIST_FILES]:
        src = SRC_PLUGIN_DIR / rel
        dst = target / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        copied_files.append(rel)
        total_bytes += dst.stat().st_size

    mcp_cfg = build_mcp_config(node_cmd, workspace)
    mcp_cfg_path = target / "mcp_config.json"
    mcp_cfg_path.write_text(json.dumps(mcp_cfg, indent=2) + "\n", encoding="utf-8")
    copied_files.append("mcp_config.json")
    total_bytes += mcp_cfg_path.stat().st_size

    payload = {
        "status": "installed",
        "pluginName": PLUGIN_NAME,
        "serverKey": SERVER_KEY,
        "target": str(target),
        "workspace": str(workspace),
        "nodeCommand": node_cmd,
        "files": copied_files,
        "totalBytes": total_bytes,
        "hasNodeModules": (target / "node_modules").exists(),
    }
    emit(
        payload,
        bool(getattr(args, "json", False)),
        [
            f"Installed {PLUGIN_NAME} plugin to: {target}",
            f"Workspace: {workspace}",
            f"Node command: {node_cmd}",
            f"Files copied: {len(copied_files)} ({total_bytes / 1024:.1f} KiB, zero node_modules)",
        ],
    )
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    target = Path(args.target).expanduser().resolve()
    installed = target.is_dir()
    missing_files: list[str] = []
    bundle_bytes = 0
    plugin_name: str | None = None
    configured_workspace: str | None = None
    configured_command: str | None = None
    resolved_server_key = SERVER_KEY
    valid_json = True

    if installed:
        for rel in REQUIRED_RUNTIME_FILES:
            p = target / rel
            if not p.is_file() or p.stat().st_size == 0:
                missing_files.append(rel)
            else:
                bundle_bytes += p.stat().st_size

        pj = target / "plugin.json"
        if pj.is_file():
            try:
                plugin_name = json.loads(pj.read_text(encoding="utf-8")).get("name")
            except Exception:
                valid_json = False

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
        missing_files = list(REQUIRED_RUNTIME_FILES)

    has_node_modules = (target / "node_modules").exists() if installed else False
    healthy = bool(
        installed
        and valid_json
        and not missing_files
        and plugin_name in ACCEPTED_PLUGIN_NAMES
        and not has_node_modules
    )

    payload = {
        "installed": installed,
        "healthy": healthy,
        "pluginName": plugin_name,
        "serverKey": resolved_server_key,
        "target": str(target),
        "workspace": configured_workspace,
        "nodeCommand": configured_command,
        "missingFiles": missing_files,
        "bundleBytes": bundle_bytes,
        "hasNodeModules": has_node_modules,
    }
    emit(
        payload,
        bool(getattr(args, "json", False)),
        [
            f"Plugin {plugin_name or PLUGIN_NAME}: {'INSTALLED (healthy)' if healthy else ('INSTALLED (unhealthy)' if installed else 'NOT INSTALLED')}",
            f"Target: {target}",
            f"Workspace: {configured_workspace or '(auto-resolved)'}",
            f"Bundle size: {bundle_bytes / 1024:.1f} KiB" + (f" · missing: {', '.join(missing_files)}" if missing_files else ""),
        ],
    )
    return 0


def cmd_uninstall(args: argparse.Namespace) -> int:
    target = Path(args.target).expanduser().resolve()
    if not target.exists():
        payload = {"status": "not_installed", "target": str(target)}
        emit(payload, bool(getattr(args, "json", False)), [f"{PLUGIN_NAME} is not installed at {target}."])
        return 0

    # Guardrail: refuse to delete an unrelated directory unless plugin.json identifies Syntopik Studio
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
        description="Install, inspect, or uninstall the Syntopik Studio SEP-1865 MCP App plugin."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_inst = sub.add_parser("install", help="Install pre-bundled Syntopik Studio plugin (zero node_modules)")
    p_inst.add_argument("--target", default=str(Path.home() / ".gemini" / "config" / "plugins" / PLUGIN_NAME), help="Target plugin directory")
    p_inst.add_argument("--workspace", default=str(ROOT), help="Syntopik workspace root")
    p_inst.add_argument("--node", default=None, help="Explicit path to node executable")
    p_inst.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    p_inst.set_defaults(func=cmd_install)

    p_stat = sub.add_parser("status", help="Inspect Syntopik Studio plugin installation health")
    p_stat.add_argument("--target", default=str(DEFAULT_TARGET), help="Target plugin directory")
    p_stat.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    p_stat.set_defaults(func=cmd_status)

    p_un = sub.add_parser("uninstall", help="Uninstall Syntopik Studio plugin safely")
    p_un.add_argument("--target", default=str(DEFAULT_TARGET), help="Target plugin directory")
    p_un.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    p_un.set_defaults(func=cmd_uninstall)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
