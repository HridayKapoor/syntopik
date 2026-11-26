"""Universal 14-Coding-Agent Compatibility Engine for Syntopik v3.2.

Single source of truth for cross-OS detection, ephemeral install-root guards,
compatibility matrices (`syntopik doctor --compat`), and host-safe non-destructive
configuration across all 14 coding-agent harnesses:

  antigravity, claude, codex, cursor, windsurf, copilot, gemini, opencode,
  cline, roo, zed, aider, continue, kiro

Pure Python 3.12+ standard library only (zero external dependencies, 100% Windows-safe).
"""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from syntopik.storage import load_json, save_json_atomic, write_text_atomic

__all__ = [
    "ALL_HARNESSES",
    "DEFAULT_FALLBACK_HARNESSES",
    "DEFAULT_HTTP_MCP_URL",
    "HARNESS_SPECS",
    "ZED_PRECEDENCE_RULE_FILES",
    "is_ephemeral_install_root",
    "detect_installed_agents",
    "get_compat_matrix",
    "format_compat_table",
    "attach_harness_config",
    "inspect_antigravity_plugin",
    "link_or_copy_dir",
]

ALL_HARNESSES: tuple[str, ...] = (
    "antigravity",
    "claude",
    "codex",
    "cursor",
    "windsurf",
    "copilot",
    "gemini",
    "opencode",
    "cline",
    "roo",
    "zed",
    "aider",
    "continue",
    "kiro",
)

DEFAULT_FALLBACK_HARNESSES: tuple[str, ...] = ("claude", "codex", "cursor")
DEFAULT_HTTP_MCP_URL = "http://127.0.0.1:4173/mcp"

# Zed evaluates rule files strictly first-match-only in this precedence order.
# Writing `.rules` when a host already has `AGENTS.md` or `CLAUDE.md` would shadow them!
ZED_PRECEDENCE_RULE_FILES: tuple[str, ...] = (
    ".rules",
    ".cursorrules",
    ".windsurfrules",
    ".clinerules",
    ".github/copilot-instructions.md",
    "AGENT.md",
    "AGENTS.md",
    "CLAUDE.md",
    "GEMINI.md",
)

HARNESS_SPECS: dict[str, dict[str, Any]] = {
    "antigravity": {
        "id": "antigravity",
        "name": "Google Antigravity",
        "rulesPath": "GEMINI.md (preserved) + .agents/hooks.json",
        "mcpPath": "~/.gemini/config/plugins/syntopik*/plugin.json",
        "mcpScope": "global",
        "mcpRootKey": "mcpServers",
        "httpMcpSupported": False,
        "transportSupport": ["stdio"],
        "httpSchemaNote": "SEP-1865 stdio plugin via `syntopik connect antigravity`",
        "attachCmd": "syntopik attach . --harness antigravity",
        "connectCmd": "syntopik connect antigravity",
    },
    "claude": {
        "id": "claude",
        "name": "Claude Code",
        "rulesPath": "CLAUDE.md (preserved) + .claude/settings.json",
        "mcpPath": ".mcp.json",
        "mcpScope": "project_or_global",
        "mcpRootKey": "mcpServers",
        "httpMcpSupported": True,
        "transportSupport": ["stdio", "http"],
        "httpSchemaNote": 'mcpServers: {type:"http", url, headers}',
        "attachCmd": "syntopik attach . --harness claude",
        "connectCmd": "/plugin marketplace add HridayKapoor/syntopik && /plugin install syntopik",
    },
    "codex": {
        "id": "codex",
        "name": "OpenAI Codex",
        "rulesPath": "AGENTS.md (preserved) + .agents/skills",
        "mcpPath": ".codex/config.toml",
        "mcpScope": "project_or_global",
        "mcpRootKey": "mcp_servers",
        "httpMcpSupported": True,
        "transportSupport": ["stdio", "http"],
        "httpSchemaNote": "TOML [mcp_servers.syntopik-studio]: url + bearer_token_env_var",
        "attachCmd": "syntopik attach . --harness codex",
        "connectCmd": "npx skills add HridayKapoor/syntopik && syntopik attach . --harness codex",
    },
    "cursor": {
        "id": "cursor",
        "name": "Cursor",
        "rulesPath": ".cursor/rules/syntopik.mdc",
        "mcpPath": ".cursor/mcp.json",
        "mcpScope": "project_or_global",
        "mcpRootKey": "mcpServers",
        "httpMcpSupported": True,
        "transportSupport": ["stdio", "http"],
        "httpSchemaNote": "mcpServers: {url, headers} (${env:SYNTOPIK_TOKEN})",
        "attachCmd": "syntopik attach . --harness cursor",
        "connectCmd": "npx skills add HridayKapoor/syntopik && syntopik attach . --harness cursor",
    },
    "windsurf": {
        "id": "windsurf",
        "name": "Windsurf",
        "rulesPath": ".windsurf/rules/syntopik.md",
        "mcpPath": "~/.codeium/windsurf/mcp_config.json",
        "mcpScope": "global",
        "mcpRootKey": "mcpServers",
        "httpMcpSupported": True,
        "transportSupport": ["stdio", "http"],
        "httpSchemaNote": "mcpServers: {serverUrl, headers} (serverUrl, NOT url!)",
        "attachCmd": "syntopik attach . --harness windsurf",
        "connectCmd": "npx skills add HridayKapoor/syntopik && syntopik attach . --harness windsurf",
    },
    "copilot": {
        "id": "copilot",
        "name": "GitHub Copilot (VS Code)",
        "rulesPath": ".github/instructions/syntopik.instructions.md",
        "mcpPath": ".vscode/mcp.json",
        "mcpScope": "project_or_global",
        "mcpRootKey": "servers",
        "httpMcpSupported": True,
        "transportSupport": ["stdio", "http"],
        "httpSchemaNote": 'servers (NOT mcpServers!): {type:"http", url, headers}',
        "attachCmd": "syntopik attach . --harness copilot",
        "connectCmd": "npx skills add HridayKapoor/syntopik && syntopik attach . --harness copilot",
    },
    "gemini": {
        "id": "gemini",
        "name": "Gemini CLI",
        "rulesPath": "GEMINI.md (preserved)",
        "mcpPath": ".gemini/settings.json",
        "mcpScope": "project_or_global",
        "mcpRootKey": "mcpServers",
        "httpMcpSupported": True,
        "transportSupport": ["stdio", "http"],
        "httpSchemaNote": "mcpServers: {httpUrl, headers} (httpUrl, NOT url!)",
        "attachCmd": "syntopik attach . --harness gemini",
        "connectCmd": "gemini extensions install https://github.com/HridayKapoor/syntopik",
    },
    "opencode": {
        "id": "opencode",
        "name": "OpenCode",
        "rulesPath": "AGENTS.md (preserved)",
        "mcpPath": "opencode.json",
        "mcpScope": "project_or_global",
        "mcpRootKey": "mcp",
        "httpMcpSupported": True,
        "transportSupport": ["stdio", "http"],
        "httpSchemaNote": 'mcp: {type:"remote", url, headers, enabled:true}',
        "attachCmd": "syntopik attach . --harness opencode",
        "connectCmd": "npx skills add HridayKapoor/syntopik && syntopik attach . --harness opencode",
    },
    "cline": {
        "id": "cline",
        "name": "Cline",
        "rulesPath": ".clinerules/syntopik.md",
        "mcpPath": "~/.cline/data/settings/cline_mcp_settings.json",
        "mcpScope": "global",
        "mcpRootKey": "mcpServers",
        "httpMcpSupported": True,
        "transportSupport": ["stdio", "http"],
        "httpSchemaNote": 'mcpServers: {type:"streamableHttp", url, headers} (camelCase!)',
        "attachCmd": "syntopik attach . --harness cline",
        "connectCmd": "syntopik attach . --harness cline",
    },
    "roo": {
        "id": "roo",
        "name": "Roo Code",
        "rulesPath": ".roo/rules/syntopik.md",
        "mcpPath": ".roo/mcp.json",
        "mcpScope": "project",
        "mcpRootKey": "mcpServers",
        "httpMcpSupported": True,
        "transportSupport": ["stdio", "http"],
        "httpSchemaNote": 'mcpServers: {type:"streamable-http", url, headers} (hyphenated!)',
        "attachCmd": "syntopik attach . --harness roo",
        "connectCmd": "syntopik attach . --harness roo",
    },
    "zed": {
        "id": "zed",
        "name": "Zed",
        "rulesPath": ".rules (if safe) or .agents/syntopik-rules.md",
        "mcpPath": ".zed/settings.json",
        "mcpScope": "project_or_global",
        "mcpRootKey": "context_servers",
        "httpMcpSupported": True,
        "transportSupport": ["stdio", "http"],
        "httpSchemaNote": "context_servers: {url, headers}",
        "attachCmd": "syntopik attach . --harness zed",
        "connectCmd": "syntopik attach . --harness zed",
    },
    "aider": {
        "id": "aider",
        "name": "Aider",
        "rulesPath": ".aider.conf.yml -> .agents/syntopik-rules.md",
        "mcpPath": None,
        "mcpScope": "none",
        "mcpRootKey": None,
        "httpMcpSupported": False,
        "transportSupport": [],
        "httpSchemaNote": "Rules-only CLI (YAML read: list; no MCP client)",
        "attachCmd": "syntopik attach . --harness aider",
        "connectCmd": "syntopik attach . --harness aider",
    },
    "continue": {
        "id": "continue",
        "name": "Continue",
        "rulesPath": ".continue/rules/syntopik.md",
        "mcpPath": ".continue/mcpServers/syntopik.yaml",
        "mcpScope": "project",
        "mcpRootKey": "mcpServers",
        "httpMcpSupported": True,
        "transportSupport": ["stdio", "http"],
        "httpSchemaNote": "YAML mcpServers: {type: streamable-http, url, requestOptions.headers}",
        "attachCmd": "syntopik attach . --harness continue",
        "connectCmd": "syntopik attach . --harness continue",
    },
    "kiro": {
        "id": "kiro",
        "name": "Kiro",
        "rulesPath": ".kiro/steering/syntopik.md",
        "mcpPath": ".kiro/settings/mcp.json",
        "mcpScope": "project_or_global",
        "mcpRootKey": "mcpServers",
        "httpMcpSupported": True,
        "transportSupport": ["stdio", "http"],
        "httpSchemaNote": "mcpServers: {url, headers, disabled:false, autoApprove:[]}",
        "attachCmd": "syntopik attach . --harness kiro",
        "connectCmd": "syntopik attach . --harness kiro",
    },
}


# ---------------------------------------------------------------------------
# Ephemeral Install-Root Guard (Zero Transient Cache Paths in Host Configs)
# ---------------------------------------------------------------------------


def is_ephemeral_install_root(path: str | Path | None) -> bool:
    """Return True if `path` resides in an ephemeral runner cache or OS temp directory.

    Detects:
      - npm/npx transient caches (`_npx`)
      - uv / uvx ephemeral archives (`uv/archive-v0`, `uv/builds-v0`, `uv-cache`)
      - pipx isolated environments (`pipx`, `.pipx`)
      - OS temporary directories (`/tmp`, `/private/tmp`, `/var/folders`, `AppData/Local/Temp`, `tempfile.gettempdir()`)
    """
    if path is None:
        return False
    raw = str(path).strip()
    if not raw:
        return False

    norm = raw.replace("\\", "/")
    norm_low = re.sub(r"/+", "/", norm).lower()
    parts = [p for p in norm_low.split("/") if p]
    part_set = set(parts)

    if "_npx" in part_set or "uv-cache" in part_set or "pipx" in part_set or ".pipx" in part_set:
        return True

    if re.search(r"(?:^|/)uv/(?:cache/)?(?:archive-v\d+|builds-v\d+|environments-v\d+)(?:/|$)", norm_low):
        return True
    if any(p.startswith("archive-v") and "uv" in part_set for p in parts):
        return True

    if (
        norm_low == "/tmp"
        or norm_low.startswith("/tmp/")
        or norm_low == "/private/tmp"
        or norm_low.startswith("/private/tmp/")
        or norm_low.startswith("/var/folders/")
        or norm_low.startswith("/private/var/folders/")
        or "/appdata/local/temp/" in norm_low
        or norm_low.endswith("/appdata/local/temp")
        or "/windows/temp/" in norm_low
        or norm_low.endswith("/windows/temp")
    ):
        return True

    # Also compare resolved path against the runtime OS temp directory when local.
    try:
        if not re.match(r"^[a-zA-Z]:[/\\]", raw):
            cand = Path(raw).expanduser().resolve()
            tmp_root = Path(tempfile.gettempdir()).resolve()
            if cand == tmp_root or cand.is_relative_to(tmp_root):
                return True
    except OSError:
        pass

    return False


# ---------------------------------------------------------------------------
# Internal Helpers (Cross-OS, Zero-Subprocess Detection & Safe File Merging)
# ---------------------------------------------------------------------------


def _normalize_env(env: Mapping[str, str] | None) -> Mapping[str, str]:
    return env if env is not None else os.environ


def _resolve_home(home: str | Path | None) -> tuple[Path, bool]:
    if home is not None:
        return Path(home).expanduser().resolve(), True
    return Path.home().resolve(), False


def _has_binary(name: str, env_map: Mapping[str, str], *, isolated: bool) -> bool:
    path_val = env_map.get("PATH")
    if isolated:
        if not path_val:
            return False
        return bool(shutil.which(name, path=path_val))
    if shutil.which(name, path=path_val):
        return True
    home = Path.home()
    for base in (home / ".local" / "bin", home / ".cargo" / "bin", Path("/opt/homebrew/bin"), Path("/usr/local/bin")):
        cand = base / name
        if cand.is_file() and os.access(cand, os.X_OK):
            return True
    return False


def _vscode_user_dirs(home_dir: Path, env_map: Mapping[str, str]) -> list[Path]:
    dirs: list[Path] = [
        home_dir / "Library" / "Application Support" / "Code" / "User",
        home_dir / ".config" / "Code" / "User",
        home_dir / "AppData" / "Roaming" / "Code" / "User",
    ]
    appdata = env_map.get("APPDATA")
    if appdata and appdata.strip():
        cand = Path(appdata).expanduser() / "Code" / "User"
        if cand not in dirs:
            dirs.insert(0, cand)
    return dirs


def _file_contains(path: Path, needle: str) -> bool:
    try:
        if not path.is_file():
            return False
        return needle in path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False


def _resolve_cline_mcp_settings_path(home_dir: Path, env_map: Mapping[str, str]) -> Path:
    explicit = (env_map.get("CLINE_MCP_SETTINGS_PATH") or "").strip()
    if explicit:
        return Path(explicit).expanduser().resolve()
    for vs_user in _vscode_user_dirs(home_dir, env_map):
        gs_dir = vs_user / "globalStorage" / "saoudrizwan.claude-dev"
        if gs_dir.is_dir():
            return (gs_dir / "settings" / "cline_mcp_settings.json").resolve()
    return (home_dir / ".cline" / "data" / "settings" / "cline_mcp_settings.json").resolve()


def _resolve_windsurf_mcp_path(home_dir: Path) -> Path:
    return (home_dir / ".codeium" / "windsurf" / "mcp_config.json").resolve()


def _strip_jsonc(text: str) -> str:
    """Strip // line comments, /* block comments */, and trailing commas outside JSON strings."""
    out: list[str] = []
    i = 0
    n = len(text)
    in_str = False
    esc = False
    while i < n:
        ch = text[i]
        if in_str:
            out.append(ch)
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            i += 1
            continue
        if ch == '"':
            in_str = True
            out.append(ch)
            i += 1
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "/":
            i += 2
            while i < n and text[i] not in ("\r", "\n"):
                i += 1
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "*":
            i += 2
            while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            i += 2
            continue
        out.append(ch)
        i += 1

    cleaned = "".join(out)
    # Remove trailing commas before } or ] (outside strings)
    res: list[str] = []
    in_str = False
    esc = False
    j = 0
    m = len(cleaned)
    while j < m:
        ch = cleaned[j]
        if in_str:
            res.append(ch)
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            j += 1
            continue
        if ch == '"':
            in_str = True
            res.append(ch)
            j += 1
            continue
        if ch == ",":
            k = j + 1
            while k < m and cleaned[k] in " \t\r\n":
                k += 1
            if k < m and cleaned[k] in "}]":
                j += 1
                continue
        res.append(ch)
        j += 1
    return "".join(res)


def _merge_json_file(path: Path, mutator: Any, *, dry_run: bool = False) -> tuple[bool, bool, dict[str, Any]]:
    """Safely merge into a JSON/JSONC file. Preserves existing file untouched on parse error."""
    return _merge_jsonc_file(path, mutator, dry_run=dry_run)


def _apply_json_merge(
    path: Path,
    mutator: Any,
    rel_label: str,
    *,
    dry_run: bool,
    written: list[str],
    preserved: list[str],
    exclude_entries: list[str],
    notes: list[str],
    track_exclude: bool = True,
) -> bool:
    """Merge a JSON/JSONC agent config safely; preserve untouched + emit warning on parse failure."""
    existed, merged_ok, _ = _merge_json_file(path, mutator, dry_run=dry_run)
    if merged_ok:
        written.append(rel_label)
        if track_exclude and not existed:
            exclude_entries.append(rel_label)
        return True
    preserved.append(rel_label)
    notes.append(f"Warning: preserved existing {rel_label} (could not parse safely as JSON/JSONC).")
    return False


def _merge_jsonc_file(path: Path, mutator: Any, *, dry_run: bool = False) -> tuple[bool, bool, dict[str, Any]]:
    """Merge into a JSON/JSONC file. Returns (existed, merged_ok, data). Preserves file on parse error."""
    existed = path.is_file()
    if existed:
        raw = path.read_text(encoding="utf-8", errors="replace")
        if raw.strip():
            try:
                parsed = json.loads(_strip_jsonc(raw))
                if not isinstance(parsed, dict):
                    return True, False, {}
                data = parsed
            except Exception:
                return True, False, {}
        else:
            data = {}
    else:
        data = {}
    mutator(data)
    if not dry_run:
        save_json_atomic(path, data)
    return existed, True, data


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


def _toml_escape(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"')


def _yaml_escape(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"')


def _upsert_codex_toml_section(prev_toml: str, snippet: str) -> str:
    header = "[mcp_servers.syntopik-studio]"
    if header not in prev_toml:
        return (prev_toml.rstrip() + "\n\n" + snippet.rstrip() + "\n") if prev_toml.strip() else (snippet.rstrip() + "\n")
    lines = prev_toml.splitlines()
    start_idx: int | None = None
    end_idx = len(lines)
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if start_idx is None:
            if stripped == header:
                start_idx = idx
        else:
            if stripped.startswith("[") and stripped.endswith("]"):
                end_idx = idx
                break
    if start_idx is None:
        return (prev_toml.rstrip() + "\n\n" + snippet.rstrip() + "\n") if prev_toml.strip() else (snippet.rstrip() + "\n")
    before = "\n".join(lines[:start_idx]).rstrip()
    after = "\n".join(lines[end_idx:]).lstrip()
    parts = [p for p in (before, snippet.rstrip(), after) if p]
    return "\n\n".join(parts) + "\n"


def _strip_yaml_scalar(raw: str) -> str:
    val = raw.strip()
    if len(val) >= 2 and ((val[0] == '"' and val[-1] == '"') or (val[0] == "'" and val[-1] == "'")):
        return val[1:-1]
    return val


def _merge_aider_yaml(prev_yaml: str, desired_reads: list[str]) -> str:
    """Non-destructively merge entries into top-level `read:` in `.aider.conf.yml`."""
    unique_desired = list(dict.fromkeys(r for r in desired_reads if r))
    if not prev_yaml.strip():
        body = "\n".join(f"  - {item}" for item in unique_desired)
        return f"read:\n{body}\n"

    lines = prev_yaml.splitlines()
    read_idx: int | None = None
    for idx, line in enumerate(lines):
        if re.match(r"^read\s*:", line):
            read_idx = idx
            break

    if read_idx is None:
        addition = "read:\n" + "\n".join(f"  - {item}" for item in unique_desired)
        return prev_yaml.rstrip() + "\n\n" + addition + "\n"

    head_line = lines[read_idx]
    m_head = re.match(r"^read\s*:\s*(.*)$", head_line)
    rest_raw = m_head.group(1) if m_head else ""
    # Preserve inline comment if present outside quotes
    rest_no_comment = rest_raw.split("#", 1)[0].strip() if not rest_raw.strip().startswith(('"', "'")) else rest_raw.strip()

    # Case 1: Inline flow list: `read: [AGENTS.md, foo.md]`
    if rest_no_comment.startswith("[") and rest_no_comment.endswith("]"):
        inner = rest_no_comment[1:-1].strip()
        existing = [_strip_yaml_scalar(tok) for tok in inner.split(",") if tok.strip()] if inner else []
        merged = list(existing)
        for item in unique_desired:
            if item not in merged:
                merged.append(item)
        formatted = ", ".join(merged)
        lines[read_idx] = f"read: [{formatted}]"
        return "\n".join(lines) + "\n"

    # Case 2: Single scalar: `read: AGENTS.md`
    if rest_no_comment:
        single = _strip_yaml_scalar(rest_no_comment)
        merged = [single] if single else []
        for item in unique_desired:
            if item not in merged:
                merged.append(item)
        replacement = ["read:", *[f"  - {item}" for item in merged]]
        lines[read_idx : read_idx + 1] = replacement
        return "\n".join(lines) + "\n"

    # Case 3: Block list under `read:`
    end_idx = read_idx + 1
    existing_items: list[str] = []
    indent = "  "
    last_item_idx = read_idx
    while end_idx < len(lines):
        ln = lines[end_idx]
        if not ln.strip() or ln.lstrip().startswith("#"):
            end_idx += 1
            continue
        if not ln.startswith((" ", "\t")):
            break
        m_item = re.match(r"^([ \t]+)-\s+(.+?)\s*$", ln)
        if m_item:
            indent = m_item.group(1)
            val = m_item.group(2).split(" #", 1)[0].strip()
            existing_items.append(_strip_yaml_scalar(val))
            last_item_idx = end_idx
        end_idx += 1

    to_add = [item for item in unique_desired if item not in existing_items]
    if to_add:
        insert_lines = [f"{indent}- {item}" for item in to_add]
        lines[last_item_idx + 1 : last_item_idx + 1] = insert_lines
    return "\n".join(lines) + "\n"


def _resolve_bundled_path(pkg_root: Path, repo_rel: str, bundle_rel: str) -> Path:
    repo_cand = pkg_root / repo_rel
    if repo_cand.exists():
        return repo_cand
    return pkg_root / bundle_rel


def link_or_copy_dir(src: Path, dest: Path, *, force_copy: bool = False) -> tuple[str, bool]:
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


def inspect_antigravity_plugin(home: str | Path | None = None) -> dict[str, Any]:
    home_dir, _ = _resolve_home(home)
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


def _generic_rules_markdown(vault_display: str) -> str:
    return (
        "# Syntopik Deep-Mastery Co-Pilot Rules\n\n"
        "For learning, deep-understanding, review, or exam-prep tasks, activate Syntopik skills in `.agents/skills/` "
        f"and persist study state to `{vault_display}`. Keep turns <= 160w prose, 1 active demand/turn, zero sycophancy, "
        "and verify math/code deterministically via `syntopik run <engine>`.\n"
    )


# ---------------------------------------------------------------------------
# Cross-OS Detection & Compatibility Matrix (14 Harnesses, Zero Subprocesses)
# ---------------------------------------------------------------------------


def detect_installed_agents(
    project_dir: str | Path | None = None,
    *,
    env: Mapping[str, str] | None = None,
    home: str | Path | None = None,
) -> list[dict[str, Any]]:
    """Detect all 14 coding-agent harnesses across macOS/Linux/Windows (<150ms, zero subprocesses)."""
    env_map = _normalize_env(env)
    home_dir, custom_home = _resolve_home(home)
    isolated = (env is not None) or custom_home
    proj = Path(project_dir).expanduser().resolve() if project_dir is not None else None

    appdata = (
        Path(env_map["APPDATA"]).expanduser()
        if (env_map.get("APPDATA") or "").strip()
        else home_dir / "AppData" / "Roaming"
    )
    xdg_config = (
        Path(env_map["XDG_CONFIG_HOME"]).expanduser()
        if (env_map.get("XDG_CONFIG_HOME") or "").strip()
        else home_dir / ".config"
    )
    vs_dirs = _vscode_user_dirs(home_dir, env_map)
    has_agy_home = (home_dir / ".gemini" / "antigravity").is_dir()
    is_macos_host = (sys.platform == "darwin") and (not isolated)

    def _app_exists(app_name: str) -> bool:
        if (home_dir / "Applications" / app_name).is_dir():
            return True
        return bool(is_macos_host and (Path("/Applications") / app_name).is_dir())

    results: list[dict[str, Any]] = []

    for hid in ALL_HARNESSES:
        spec = HARNESS_SPECS[hid]
        signals: list[str] = []
        configured = False

        if hid == "antigravity":
            if _has_binary("agy", env_map, isolated=isolated):
                signals.append("bin:agy")
            if _has_binary("antigravity", env_map, isolated=isolated):
                signals.append("bin:antigravity")
            for k in ("ANTIGRAVITY_CLI_ALIAS", "AGY_ACCOUNT", "ANTIGRAVITY_AGENT"):
                if env_map.get(k):
                    signals.append(f"env:{k}")
            if has_agy_home:
                signals.append("~/.gemini/antigravity")
            if proj and (proj / ".agents" / "hooks.json").is_file():
                signals.append(".agents/hooks.json")
            agy_plug = inspect_antigravity_plugin(home=home_dir)
            configured = bool(
                agy_plug["installed"]
                or (proj and (proj / ".agents" / "hooks.json").is_file())
            )

        elif hid == "claude":
            if _has_binary("claude", env_map, isolated=isolated):
                signals.append("bin:claude")
            for k in ("CLAUDECODE", "CLAUDE_PROJECT_DIR", "CLAUDE_CODE_ENTRYPOINT"):
                if env_map.get(k):
                    signals.append(f"env:{k}")
            if (home_dir / ".claude").is_dir():
                signals.append("~/.claude")
            if proj and ((proj / ".claude").exists() or (proj / ".mcp.json").is_file()):
                signals.append("project:.claude/.mcp.json")
            configured = bool(
                (proj and _file_contains(proj / ".mcp.json", "syntopik-studio"))
                or (proj and _file_contains(proj / ".claude" / "settings.json", "session-start"))
                or (proj and _file_contains(proj / ".claude" / "settings.json", "syntopik"))
            )

        elif hid == "codex":
            codex_home = (
                Path(env_map["CODEX_HOME"]).expanduser()
                if (env_map.get("CODEX_HOME") or "").strip()
                else home_dir / ".codex"
            )
            if _has_binary("codex", env_map, isolated=isolated):
                signals.append("bin:codex")
            for k in ("CODEX_HOME", "CODEX_THREAD_ID", "CODEX_SANDBOX", "CODEX_CI"):
                if env_map.get(k):
                    signals.append(f"env:{k}")
            if codex_home.is_dir():
                signals.append("~/.codex")
            if proj and (proj / ".codex").exists():
                signals.append("project:.codex")
            configured = bool(
                (proj and _file_contains(proj / ".codex" / "config.toml", "syntopik-studio"))
                or _file_contains(codex_home / "config.toml", "syntopik-studio")
            )

        elif hid == "cursor":
            if _has_binary("cursor", env_map, isolated=isolated):
                signals.append("bin:cursor")
            for k in ("CURSOR_TRACE_ID", "CURSOR_AGENT"):
                if env_map.get(k):
                    signals.append(f"env:{k}")
            if (home_dir / ".cursor").is_dir():
                signals.append("~/.cursor")
            if _app_exists("Cursor.app"):
                signals.append("app:Cursor.app")
            if proj and (proj / ".cursor").exists():
                signals.append("project:.cursor")
            configured = bool(
                (proj and (proj / ".cursor" / "rules" / "syntopik.mdc").is_file())
                or (proj and _file_contains(proj / ".cursor" / "mcp.json", "syntopik-studio"))
                or _file_contains(home_dir / ".cursor" / "mcp.json", "syntopik-studio")
            )

        elif hid == "windsurf":
            if _has_binary("windsurf", env_map, isolated=isolated):
                signals.append("bin:windsurf")
            for k in ("WINDSURF_PID", "CODEIUM_EDITOR_APP_ROOT"):
                if env_map.get(k):
                    signals.append(f"env:{k}")
            if (home_dir / ".codeium" / "windsurf").is_dir():
                signals.append("~/.codeium/windsurf")
            if (home_dir / ".windsurf").is_dir():
                signals.append("~/.windsurf")
            if _app_exists("Windsurf.app"):
                signals.append("app:Windsurf.app")
            if proj and (proj / ".windsurf").exists():
                signals.append("project:.windsurf")
            configured = bool(
                (proj and (proj / ".windsurf" / "rules" / "syntopik.md").is_file())
                or _file_contains(_resolve_windsurf_mcp_path(home_dir), "syntopik-studio")
            )

        elif hid == "copilot":
            if _has_binary("code", env_map, isolated=isolated):
                signals.append("bin:code")
            if _has_binary("code-insiders", env_map, isolated=isolated):
                signals.append("bin:code-insiders")
            if env_map.get("TERM_PROGRAM") == "vscode" or env_map.get("VSCODE_PID"):
                signals.append("env:vscode")
            if any(d.is_dir() for d in vs_dirs):
                signals.append("vscode:Code/User")
            if _app_exists("Visual Studio Code.app"):
                signals.append("app:Visual Studio Code.app")
            if proj and ((proj / ".vscode").exists() or (proj / ".github" / "instructions").exists()):
                signals.append("project:.vscode/.github/instructions")
            configured = bool(
                (proj and (proj / ".github" / "instructions" / "syntopik.instructions.md").is_file())
                or (proj and _file_contains(proj / ".vscode" / "mcp.json", "syntopik-studio"))
                or any(_file_contains(d / "mcp.json", "syntopik-studio") for d in vs_dirs)
            )

        elif hid == "gemini":
            # Exclude Antigravity-only `~/.gemini` false positive!
            if _has_binary("gemini", env_map, isolated=isolated):
                signals.append("bin:gemini")
            if env_map.get("GEMINI_CLI"):
                signals.append("env:GEMINI_CLI")
            if (home_dir / ".gemini" / "settings.json").is_file():
                signals.append("~/.gemini/settings.json")
            if (home_dir / ".gemini" / "extensions").is_dir():
                signals.append("~/.gemini/extensions")
            if (home_dir / ".gemini").is_dir() and not has_agy_home and not signals:
                signals.append("~/.gemini")
            if proj and (proj / ".gemini").exists():
                signals.append("project:.gemini")
            configured = bool(
                (proj and _file_contains(proj / ".gemini" / "settings.json", "syntopik-studio"))
                or (proj and (proj / "gemini-extension.json").is_file())
                or _file_contains(home_dir / ".gemini" / "settings.json", "syntopik-studio")
            )

        elif hid == "opencode":
            if _has_binary("opencode", env_map, isolated=isolated):
                signals.append("bin:opencode")
            if env_map.get("OPENCODE"):
                signals.append("env:OPENCODE")
            oc_dirs = [xdg_config / "opencode", home_dir / ".config" / "opencode", appdata / "opencode"]
            if any(d.is_dir() for d in oc_dirs):
                signals.append("config:opencode")
            if proj and (proj / "opencode.json").is_file():
                signals.append("project:opencode.json")
            configured = bool(
                (proj and _file_contains(proj / "opencode.json", "syntopik-studio"))
                or any(_file_contains(d / "opencode.json", "syntopik-studio") for d in oc_dirs)
            )

        elif hid == "cline":
            if _has_binary("cline", env_map, isolated=isolated):
                signals.append("bin:cline")
            if (env_map.get("CLINE_MCP_SETTINGS_PATH") or "").strip():
                signals.append("env:CLINE_MCP_SETTINGS_PATH")
            if (home_dir / ".cline").is_dir():
                signals.append("~/.cline")
            if any((d / "globalStorage" / "saoudrizwan.claude-dev").is_dir() for d in vs_dirs):
                signals.append("vscode:saoudrizwan.claude-dev")
            if proj and ((proj / ".clinerules").exists() or (proj / ".clinerules").is_symlink()):
                signals.append("project:.clinerules")
            cline_mcp = _resolve_cline_mcp_settings_path(home_dir, env_map)
            configured = bool(
                (proj and (proj / ".clinerules").is_dir() and (proj / ".clinerules" / "syntopik.md").is_file())
                or _file_contains(cline_mcp, "syntopik-studio")
            )

        elif hid == "roo":
            if (home_dir / ".roo").is_dir():
                signals.append("~/.roo")
            if any((d / "globalStorage" / "rooveterinaryinc.roo-cline").is_dir() for d in vs_dirs):
                signals.append("vscode:rooveterinaryinc.roo-cline")
            if proj and (proj / ".roo").exists():
                signals.append("project:.roo")
            configured = bool(
                (proj and (proj / ".roo" / "rules" / "syntopik.md").is_file())
                or (proj and _file_contains(proj / ".roo" / "mcp.json", "syntopik-studio"))
            )

        elif hid == "zed":
            if _has_binary("zed", env_map, isolated=isolated):
                signals.append("bin:zed")
            if env_map.get("ZED_TERM"):
                signals.append("env:ZED_TERM")
            zed_dirs = [xdg_config / "zed", home_dir / ".config" / "zed", appdata / "Zed"]
            if any(d.is_dir() for d in zed_dirs):
                signals.append("config:zed")
            if _app_exists("Zed.app"):
                signals.append("app:Zed.app")
            if proj and ((proj / ".zed").exists() or (proj / ".zed" / "settings.json").is_file()):
                signals.append("project:.zed")
            configured = bool(
                (proj and _file_contains(proj / ".zed" / "settings.json", "syntopik-studio"))
                or any(_file_contains(d / "settings.json", "syntopik-studio") for d in zed_dirs)
            )

        elif hid == "aider":
            if _has_binary("aider", env_map, isolated=isolated):
                signals.append("bin:aider")
            if (home_dir / ".aider.conf.yml").is_file():
                signals.append("~/.aider.conf.yml")
            if proj and (proj / ".aider.conf.yml").is_file():
                signals.append("project:.aider.conf.yml")
            configured = bool(
                proj
                and (
                    _file_contains(proj / ".aider.conf.yml", "syntopik-rules.md")
                    or _file_contains(proj / ".aider.conf.yml", "AGENTS.md")
                )
            )

        elif hid == "continue":
            if _has_binary("continue", env_map, isolated=isolated):
                signals.append("bin:continue")
            if (home_dir / ".continue").is_dir():
                signals.append("~/.continue")
            if any((d / "globalStorage" / "continue.continue").is_dir() for d in vs_dirs):
                signals.append("vscode:continue.continue")
            if proj and (proj / ".continue").exists():
                signals.append("project:.continue")
            configured = bool(
                (proj and (proj / ".continue" / "rules" / "syntopik.md").is_file())
                or (proj and (proj / ".continue" / "mcpServers" / "syntopik.yaml").is_file())
                or (proj and (proj / ".continue" / "mcpServers" / "syntopik.json").is_file())
            )

        elif hid == "kiro":
            if _has_binary("kiro", env_map, isolated=isolated):
                signals.append("bin:kiro")
            if _has_binary("kiro-cli", env_map, isolated=isolated):
                signals.append("bin:kiro-cli")
            if (home_dir / ".kiro").is_dir():
                signals.append("~/.kiro")
            if _app_exists("Kiro.app"):
                signals.append("app:Kiro.app")
            if proj and (proj / ".kiro").exists():
                signals.append("project:.kiro")
            configured = bool(
                (proj and (proj / ".kiro" / "steering" / "syntopik.md").is_file())
                or (proj and _file_contains(proj / ".kiro" / "settings" / "mcp.json", "syntopik-studio"))
                or _file_contains(home_dir / ".kiro" / "settings" / "mcp.json", "syntopik-studio")
            )

        results.append(
            {
                "id": hid,
                "name": spec["name"],
                "detected": bool(signals),
                "configured": configured,
                "attached": configured,
                "signals": signals,
                "rulesPath": spec["rulesPath"],
                "mcpPath": spec["mcpPath"],
                "mcpScope": spec["mcpScope"],
                "httpMcpSupported": bool(spec["httpMcpSupported"]),
                "transportSupport": list(spec["transportSupport"]),
                "attachCmd": spec["attachCmd"],
                "connectCmd": spec["connectCmd"],
            }
        )

    return results


def _detect_active_harness(env_map: Mapping[str, str], home_dir: Path) -> str:
    explicit = (env_map.get("SYNT_HARNESS") or env_map.get("MLH_HARNESS") or "").strip().lower()
    if explicit in (*ALL_HARNESSES, "generic"):
        return explicit
    if env_map.get("ANTIGRAVITY_CLI_ALIAS") or env_map.get("AGY_ACCOUNT") or env_map.get("ANTIGRAVITY_AGENT"):
        return "antigravity"
    if env_map.get("CLAUDECODE") or env_map.get("CLAUDE_PROJECT_DIR") or env_map.get("CLAUDE_CODE_ENTRYPOINT"):
        return "claude"
    if env_map.get("CODEX_THREAD_ID") or env_map.get("CODEX_SANDBOX") or env_map.get("CODEX_CI") or env_map.get("CODEX_HOME"):
        return "codex"
    if env_map.get("CURSOR_TRACE_ID") or env_map.get("CURSOR_AGENT"):
        return "cursor"
    if env_map.get("WINDSURF_PID") or env_map.get("CODEIUM_EDITOR_APP_ROOT"):
        return "windsurf"
    if env_map.get("OPENCODE"):
        return "opencode"
    if env_map.get("GEMINI_CLI"):
        return "gemini"
    if env_map.get("ZED_TERM"):
        return "zed"
    if env_map.get("CLINE_MCP_SETTINGS_PATH"):
        return "cline"
    if env_map.get("TERM_PROGRAM") == "vscode" or env_map.get("VSCODE_PID"):
        return "copilot"
    if (home_dir / ".gemini" / "antigravity").is_dir():
        return "antigravity"
    return "generic"


def get_compat_matrix(
    project_dir: str | Path | None = None,
    *,
    env: Mapping[str, str] | None = None,
    home: str | Path | None = None,
) -> dict[str, Any]:
    """Return full 14-coding-agent compatibility matrix for `syntopik doctor --compat` & Studio API."""
    env_map = _normalize_env(env)
    home_dir, _ = _resolve_home(home)
    proj = Path(project_dir).expanduser().resolve() if project_dir is not None else None
    agents = detect_installed_agents(proj, env=env, home=home)
    return {
        "ok": True,
        "platform": platform.system(),
        "projectDir": str(proj) if proj is not None else None,
        "totalHarnesses": len(agents),
        "detectedCount": sum(1 for a in agents if a["detected"]),
        "configuredCount": sum(1 for a in agents if a["configured"]),
        "attachedCount": sum(1 for a in agents if a["attached"]),
        "httpSupportedCount": sum(1 for a in agents if a["httpMcpSupported"]),
        "activeHarness": _detect_active_harness(env_map, home_dir),
        "harnesses": agents,
    }


def format_compat_table(matrix: dict[str, Any]) -> str:
    """Format the 14-harness compatibility matrix as a crisp aligned terminal table."""
    harnesses = list(matrix.get("harnesses") or [])
    header = (
        f"Syntopik Coding-Agent Compatibility Matrix ({matrix.get('platform', platform.system())}) — "
        f"{matrix.get('attachedCount', 0)}/{matrix.get('totalHarnesses', len(harnesses))} attached · "
        f"{matrix.get('detectedCount', 0)} detected · {matrix.get('httpSupportedCount', 0)} HTTP-MCP ready"
    )
    sep = "-" * 102
    lines = [
        header,
        sep,
        f"{'Harness':<14} {'Status':<10} {'Transports':<14} {'MCP Scope':<18} {'Next 1-Line Command'}",
        sep,
    ]
    for h in harnesses:
        status = "ATTACHED" if h.get("attached") else ("DETECTED" if h.get("detected") else "--")
        transports = "+".join(h.get("transportSupport") or []) or "rules-only"
        scope = str(h.get("mcpScope") or "none")
        next_cmd = str(h.get("connectCmd") if h.get("id") == "antigravity" else h.get("attachCmd") or "")
        lines.append(f"{h['id']:<14} {status:<10} {transports:<14} {scope:<18} {next_cmd}")
    lines.append(sep)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Host-Safe Per-Harness Config Writer (Stdio & Streamable HTTP)
# ---------------------------------------------------------------------------


def attach_harness_config(
    harness_id: str,
    project_dir: str | Path,
    *,
    vault_dir: str | Path,
    pkg_root: str | Path,
    transport: str = "stdio",
    http_url: str = DEFAULT_HTTP_MCP_URL,
    dry_run: bool = False,
    home: str | Path | None = None,
    write_global: bool = True,
    copy_skills: bool = False,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Idempotently wire a single coding-agent harness into `project_dir` (100% host-safe & Windows-safe).

    Guarantees:
      - NEVER overwrites existing host `AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, or regular-file `.clinerules`.
      - NEVER shadows Zed's 9 higher/peer rule files with a newly created `.rules`.
      - NEVER bakes ephemeral `_npx` / `uv/archive-v0` / `uv-cache` / `pipx` / temp install paths into configs.
      - Respects every harness's exact MCP root key & Streamable-HTTP schema quirk (`serverUrl`, `servers`,
        `httpUrl`, `mcp`+`remote`, `streamableHttp`, `streamable-http`, `context_servers`, `requestOptions.headers`).
    """
    hid = str(harness_id or "").strip().lower()
    if hid not in HARNESS_SPECS:
        raise ValueError(f"Unsupported harness {harness_id!r}; expected one of {ALL_HARNESSES}.")

    tr = str(transport or "stdio").strip().lower()
    if tr not in ("stdio", "http"):
        raise ValueError(f"Unsupported transport {transport!r}; expected 'stdio' or 'http'.")

    target = Path(project_dir).expanduser().resolve()
    vault_path = Path(vault_dir).expanduser().resolve()
    pkg_path = Path(pkg_root).expanduser().resolve()
    home_dir, _ = _resolve_home(home)
    env_map = _normalize_env(env)

    vault_display = ".syntopik/" if vault_path == (target / ".syntopik").resolve() else "~/.syntopik/"
    ephemeral = is_ephemeral_install_root(pkg_path)
    effective_copy_skills = bool(copy_skills or ephemeral)

    server_mjs = _resolve_bundled_path(pkg_path, "mcp-app/mlh-studio/dist/server.mjs", "mcp-dist/server.mjs").resolve()
    hooks_src = _resolve_bundled_path(pkg_path, ".agents/hooks", "hooks").resolve()

    if ephemeral:
        mcp_command = "npx"
        mcp_args = ["-y", "syntopik", "mcp", f"--workspace={vault_path}"]
        session_hook_cmd = f'npx -y syntopik hook session-start --workspace "{vault_path}"'
        post_hook_cmd = f'npx -y syntopik hook post-graph-sync --workspace "{vault_path}"'
    else:
        mcp_command = "node"
        mcp_args = [str(server_mjs), f"--workspace={vault_path}"]
        session_hook_cmd = f'node "{(hooks_src / "session-start.mjs").resolve()}" --workspace "{vault_path}"'
        post_hook_cmd = f'node "{(hooks_src / "post-graph-sync.mjs").resolve()}" --workspace "{vault_path}"'

    written: list[str] = []
    preserved: list[str] = []
    exclude_entries: list[str] = []
    notes: list[str] = []
    mcp_config_path: str | None = None
    antigravity_info: dict[str, Any] | None = None

    if not dry_run:
        target.mkdir(parents=True, exist_ok=True)

    # 1. Cursor
    if hid == "cursor":
        rule_path = target / ".cursor" / "rules" / "syntopik.mdc"
        existed_rule = rule_path.is_file()
        if not dry_run:
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
        mcp_config_path = str(cursor_mcp)
        entry = (
            {"url": http_url, "headers": {"Authorization": "Bearer ${env:SYNTOPIK_TOKEN}"}}
            if tr == "http"
            else {"command": mcp_command, "args": list(mcp_args)}
        )
        _apply_json_merge(
            cursor_mcp,
            lambda doc: doc.setdefault("mcpServers", {}).update({"syntopik-studio": entry}),
            ".cursor/mcp.json",
            dry_run=dry_run,
            written=written,
            preserved=preserved,
            exclude_entries=exclude_entries,
            notes=notes,
        )

    # 2. Windsurf (project rule + global ~/.codeium/windsurf/mcp_config.json with `serverUrl`!)
    elif hid == "windsurf":
        ws_rule = target / ".windsurf" / "rules" / "syntopik.md"
        existed_ws = ws_rule.is_file()
        if not dry_run:
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

        ws_mcp = _resolve_windsurf_mcp_path(home_dir)
        mcp_config_path = str(ws_mcp)
        # Windsurf Streamable-HTTP requires `serverUrl` (NOT `url`!).
        ws_entry = (
            {"serverUrl": http_url, "headers": {"Authorization": "Bearer ${env:SYNTOPIK_TOKEN}"}}
            if tr == "http"
            else {"command": mcp_command, "args": list(mcp_args)}
        )
        if write_global:
            _apply_json_merge(
                ws_mcp,
                lambda doc: doc.setdefault("mcpServers", {}).update({"syntopik-studio": ws_entry}),
                str(ws_mcp),
                dry_run=dry_run,
                written=written,
                preserved=preserved,
                exclude_entries=exclude_entries,
                notes=notes,
                track_exclude=False,
            )
        else:
            notes.append(f"Windsurf global MCP config skipped in local-sandbox auto mode ({ws_mcp}).")

    # 3. GitHub Copilot / VS Code (root key `servers`, NOT `mcpServers`!)
    elif hid == "copilot":
        gh_inst = target / ".github" / "instructions" / "syntopik.instructions.md"
        existed_gh = gh_inst.is_file()
        if not dry_run:
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
        mcp_config_path = str(vscode_mcp)
        vs_entry = (
            {"type": "http", "url": http_url, "headers": {"Authorization": "Bearer ${env:SYNTOPIK_TOKEN}"}}
            if tr == "http"
            else {"type": "stdio", "command": mcp_command, "args": list(mcp_args), "cwd": "${workspaceFolder}"}
        )
        _apply_json_merge(
            vscode_mcp,
            lambda doc: doc.setdefault("servers", {}).update({"syntopik-studio": vs_entry}),
            ".vscode/mcp.json",
            dry_run=dry_run,
            written=written,
            preserved=preserved,
            exclude_entries=exclude_entries,
            notes=notes,
        )

    # 4. OpenCode (root key `mcp`, `type: "local"` or `type: "remote"`)
    elif hid == "opencode":
        oc_json = target / "opencode.json"
        mcp_config_path = str(oc_json)
        oc_entry = (
            {
                "type": "remote",
                "url": http_url,
                "headers": {"Authorization": "Bearer {env:SYNTOPIK_TOKEN}"},
                "enabled": True,
            }
            if tr == "http"
            else {
                "type": "local",
                "command": [mcp_command, *mcp_args],
                "environment": {"SYNT_WORKSPACE": str(vault_path)},
                "enabled": True,
            }
        )
        _apply_json_merge(
            oc_json,
            lambda doc: doc.setdefault("mcp", {}).update({"syntopik-studio": oc_entry}),
            "opencode.json",
            dry_run=dry_run,
            written=written,
            preserved=preserved,
            exclude_entries=exclude_entries,
            notes=notes,
        )

    # 5. OpenAI Codex (TOML `[mcp_servers.syntopik-studio]`)
    elif hid == "codex":
        codex_toml = target / ".codex" / "config.toml"
        mcp_config_path = str(codex_toml)
        existed_codex = codex_toml.is_file()
        prev_toml = codex_toml.read_text(encoding="utf-8") if existed_codex else ""
        if tr == "http":
            snippet = (
                "[mcp_servers.syntopik-studio]\n"
                f'url = "{_toml_escape(http_url)}"\n'
                'bearer_token_env_var = "SYNTOPIK_TOKEN"\n'
                "startup_timeout_sec = 10\n"
                "tool_timeout_sec = 60\n"
            )
        else:
            args_toml = ", ".join(f'"{_toml_escape(a)}"' for a in mcp_args)
            snippet = (
                "[mcp_servers.syntopik-studio]\n"
                f'command = "{_toml_escape(mcp_command)}"\n'
                f"args = [{args_toml}]\n"
                "startup_timeout_sec = 10\n"
                "tool_timeout_sec = 60\n"
            )
        next_toml = _upsert_codex_toml_section(prev_toml, snippet)
        if not dry_run:
            write_text_atomic(codex_toml, next_toml)
        written.append(".codex/config.toml")
        if not existed_codex:
            exclude_entries.append(".codex/config.toml")

    # 6. Claude Code (`.mcp.json` + `.claude/settings.json` hooks + `.claude/skills`)
    elif hid == "claude":
        root_mcp = target / ".mcp.json"
        mcp_config_path = str(root_mcp)
        claude_entry = (
            {"type": "http", "url": http_url, "headers": {"Authorization": "Bearer ${SYNTOPIK_TOKEN}"}}
            if tr == "http"
            else {"type": "stdio", "command": mcp_command, "args": list(mcp_args)}
        )
        _apply_json_merge(
            root_mcp,
            lambda doc: doc.setdefault("mcpServers", {}).update({"syntopik-studio": claude_entry}),
            ".mcp.json",
            dry_run=dry_run,
            written=written,
            preserved=preserved,
            exclude_entries=exclude_entries,
            notes=notes,
        )

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
                {"hooks": [{"type": "command", "command": session_hook_cmd, "timeout": 10}]},
                session_hook_cmd,
            )
            _append_dedup_hook(
                hooks["PostToolUse"],
                {
                    "matcher": "Write|Edit|MultiEdit|NotebookEdit|Bash",
                    "hooks": [{"type": "command", "command": post_hook_cmd, "timeout": 10}],
                },
                post_hook_cmd,
            )

        _apply_json_merge(
            claude_settings,
            _wire_claude_hooks,
            ".claude/settings.json",
            dry_run=dry_run,
            written=written,
            preserved=preserved,
            exclude_entries=exclude_entries,
            notes=notes,
        )

        claude_skills = target / ".claude" / "skills"
        dest_skills = target / ".agents" / "skills"
        if not dry_run and dest_skills.exists():
            sk_action, sk_created = link_or_copy_dir(dest_skills.resolve(), claude_skills, force_copy=effective_copy_skills)
            if sk_created:
                written.append(f".claude/skills ({sk_action})")
                exclude_entries.extend([".claude/skills", ".claude/skills/"])
            else:
                preserved.append(".claude/skills")

    # 7. Google Antigravity (`.agents/hooks.json` + plugin inspection)
    elif hid == "antigravity":
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

        _apply_json_merge(
            agy_hooks,
            _wire_agy_hooks,
            ".agents/hooks.json",
            dry_run=dry_run,
            written=written,
            preserved=preserved,
            exclude_entries=exclude_entries,
            notes=notes,
        )

        antigravity_info = inspect_antigravity_plugin(home=home_dir)
        antigravity_info["workspaceHooks"] = ".agents/hooks.json"
        if antigravity_info["installed"]:
            notes.append(
                f"Antigravity plugin detected ({antigravity_info['pluginName']} at {antigravity_info['pluginDir']}); workspace hooks wired in .agents/hooks.json."
            )
        else:
            notes.append(
                "Antigravity workspace hooks wired in .agents/hooks.json (host AGENTS.md preserved). Run `syntopik connect antigravity` to install the global Studio MCP plugin."
            )

    # 8. Gemini CLI (`.gemini/settings.json`; HTTP uses `httpUrl`, NOT `url`!)
    elif hid == "gemini":
        gem_settings = target / ".gemini" / "settings.json"
        mcp_config_path = str(gem_settings)
        gem_entry = (
            {"httpUrl": http_url, "headers": {"Authorization": "Bearer ${SYNTOPIK_TOKEN}"}}
            if tr == "http"
            else {"command": mcp_command, "args": list(mcp_args)}
        )
        _apply_json_merge(
            gem_settings,
            lambda doc: doc.setdefault("mcpServers", {}).update({"syntopik-studio": gem_entry}),
            ".gemini/settings.json",
            dry_run=dry_run,
            written=written,
            preserved=preserved,
            exclude_entries=exclude_entries,
            notes=notes,
        )

    # 9. Cline (`.clinerules/syntopik.md` with regular-file GUARD + `streamableHttp` camelCase!)
    elif hid == "cline":
        clinerules_root = target / ".clinerules"
        is_regular_file = clinerules_root.is_file() or (
            clinerules_root.is_symlink() and not clinerules_root.is_dir()
        )
        if is_regular_file:
            preserved.append(".clinerules")
            notes.append("Preserved existing regular file .clinerules (skipped creating .clinerules/syntopik.md).")
        else:
            cline_rule = clinerules_root / "syntopik.md"
            existed_cr = cline_rule.is_file()
            if not dry_run:
                write_text_atomic(cline_rule, _generic_rules_markdown(vault_display))
            written.append(".clinerules/syntopik.md")
            if not existed_cr:
                exclude_entries.append(".clinerules/syntopik.md")

        cline_mcp = _resolve_cline_mcp_settings_path(home_dir, env_map)
        mcp_config_path = str(cline_mcp)
        # Cline HTTP transport MUST use camelCase `streamableHttp` (hyphenated breaks Cline!).
        cline_entry = (
            {
                "type": "streamableHttp",
                "url": http_url,
                "headers": {"Authorization": "Bearer ${SYNTOPIK_TOKEN}"},
                "disabled": False,
                "autoApprove": [],
            }
            if tr == "http"
            else {
                "command": mcp_command,
                "args": list(mcp_args),
                "disabled": False,
                "autoApprove": [],
            }
        )
        if write_global:
            _apply_json_merge(
                cline_mcp,
                lambda doc: doc.setdefault("mcpServers", {}).update({"syntopik-studio": cline_entry}),
                str(cline_mcp),
                dry_run=dry_run,
                written=written,
                preserved=preserved,
                exclude_entries=exclude_entries,
                notes=notes,
                track_exclude=False,
            )
        else:
            notes.append(f"Cline global MCP config skipped in local-sandbox auto mode ({cline_mcp}).")

    # 10. Roo Code (`.roo/rules/syntopik.md` + `.roo/mcp.json` with `streamable-http` hyphenated!)
    elif hid == "roo":
        roo_rule = target / ".roo" / "rules" / "syntopik.md"
        existed_rr = roo_rule.is_file()
        if not dry_run:
            write_text_atomic(roo_rule, _generic_rules_markdown(vault_display))
        written.append(".roo/rules/syntopik.md")
        if not existed_rr:
            exclude_entries.append(".roo/rules/syntopik.md")

        roo_mcp = target / ".roo" / "mcp.json"
        mcp_config_path = str(roo_mcp)
        roo_entry = (
            {
                "type": "streamable-http",
                "url": http_url,
                "headers": {"Authorization": "Bearer ${SYNTOPIK_TOKEN}"},
                "disabled": False,
                "alwaysAllow": [],
            }
            if tr == "http"
            else {
                "command": mcp_command,
                "args": list(mcp_args),
                "disabled": False,
                "alwaysAllow": [],
            }
        )
        _apply_json_merge(
            roo_mcp,
            lambda doc: doc.setdefault("mcpServers", {}).update({"syntopik-studio": roo_entry}),
            ".roo/mcp.json",
            dry_run=dry_run,
            written=written,
            preserved=preserved,
            exclude_entries=exclude_entries,
            notes=notes,
        )

    # 11. Zed (`.rules` ONLY if none of Zed's 9 first-match files exist; `.zed/settings.json` `context_servers`)
    elif hid == "zed":
        existing_precedence = [
            rel
            for rel in ZED_PRECEDENCE_RULE_FILES
            if (target / rel).is_file() or ((target / rel).is_symlink() and not (target / rel).is_dir())
        ]
        if existing_precedence:
            for rel in existing_precedence:
                if rel not in preserved:
                    preserved.append(rel)
            fallback_rule = target / ".agents" / "syntopik-rules.md"
            existed_fb = fallback_rule.is_file()
            if not dry_run:
                write_text_atomic(fallback_rule, _generic_rules_markdown(vault_display))
            written.append(".agents/syntopik-rules.md")
            if not existed_fb:
                exclude_entries.append(".agents/syntopik-rules.md")
            notes.append(
                f"Zed uses first-match rules; preserved {', '.join(existing_precedence)} instead of creating .rules (wrote .agents/syntopik-rules.md)."
            )
        else:
            zed_rules = target / ".rules"
            existed_zr = zed_rules.is_file()
            if not dry_run:
                write_text_atomic(zed_rules, _generic_rules_markdown(vault_display))
            written.append(".rules")
            if not existed_zr:
                exclude_entries.append(".rules")

        zed_settings = target / ".zed" / "settings.json"
        mcp_config_path = str(zed_settings)
        zed_entry = (
            {"url": http_url, "headers": {"Authorization": "Bearer ${SYNTOPIK_TOKEN}"}}
            if tr == "http"
            else {
                "command": mcp_command,
                "args": list(mcp_args),
                "env": {"SYNT_WORKSPACE": str(vault_path)},
            }
        )
        existed_zs, merged_ok, _ = _merge_jsonc_file(
            zed_settings,
            lambda doc: doc.setdefault("context_servers", {}).update({"syntopik-studio": zed_entry}),
            dry_run=dry_run,
        )
        if merged_ok:
            written.append(".zed/settings.json")
            if not existed_zs:
                exclude_entries.append(".zed/settings.json")
        else:
            preserved.append(".zed/settings.json")
            notes.append("Preserved existing .zed/settings.json (could not parse safely as JSON/JSONC).")

    # 12. Aider (rules-only CLI: `.agents/syntopik-rules.md` + non-destructive `.aider.conf.yml` merge)
    elif hid == "aider":
        aider_rule = target / ".agents" / "syntopik-rules.md"
        existed_ar = aider_rule.is_file()
        if not dry_run:
            write_text_atomic(aider_rule, _generic_rules_markdown(vault_display))
        written.append(".agents/syntopik-rules.md")
        if not existed_ar:
            exclude_entries.append(".agents/syntopik-rules.md")

        aider_cfg = target / ".aider.conf.yml"
        existed_ac = aider_cfg.is_file()
        prev_yaml = aider_cfg.read_text(encoding="utf-8") if existed_ac else ""
        desired_reads: list[str] = []
        if (target / "AGENTS.md").is_file():
            desired_reads.append("AGENTS.md")
        desired_reads.append(".agents/syntopik-rules.md")
        next_yaml = _merge_aider_yaml(prev_yaml, desired_reads)
        if not dry_run:
            write_text_atomic(aider_cfg, next_yaml)
        written.append(".aider.conf.yml")
        if not existed_ac:
            exclude_entries.append(".aider.conf.yml")
        if tr == "http":
            notes.append("Aider is a rules-only CLI (no MCP client); wired .aider.conf.yml read list.")

    # 13. Continue (`.continue/rules/syntopik.md` + `.continue/mcpServers/syntopik.yaml`)
    elif hid == "continue":
        cont_rule = target / ".continue" / "rules" / "syntopik.md"
        existed_cr = cont_rule.is_file()
        if not dry_run:
            write_text_atomic(
                cont_rule,
                (
                    "---\n"
                    "name: Syntopik Deep-Mastery Co-Pilot\n"
                    "description: Syntopik universal deep-mastery co-pilot rules and study vault pointer\n"
                    "alwaysApply: false\n"
                    "---\n\n"
                    f"For learning, deep-understanding, review, or exam-prep tasks, activate Syntopik skills in `.agents/skills/` "
                    f"and persist study state to `{vault_display}`. Keep turns <= 160w prose, 1 active demand/turn, zero sycophancy, "
                    "and verify math/code deterministically via `syntopik run <engine>`.\n"
                ),
            )
        written.append(".continue/rules/syntopik.md")
        if not existed_cr:
            exclude_entries.append(".continue/rules/syntopik.md")

        cont_mcp = target / ".continue" / "mcpServers" / "syntopik.yaml"
        mcp_config_path = str(cont_mcp)
        existed_cm = cont_mcp.is_file()
        if tr == "http":
            yaml_body = (
                "name: Syntopik Studio\n"
                "version: 0.0.1\n"
                "schema: v1\n"
                "mcpServers:\n"
                "  - name: syntopik-studio\n"
                "    type: streamable-http\n"
                f'    url: "{_yaml_escape(http_url)}"\n'
                "    requestOptions:\n"
                "      headers:\n"
                '        Authorization: "Bearer ${SYNTOPIK_TOKEN}"\n'
            )
        else:
            args_yaml = "\n".join(f'      - "{_yaml_escape(a)}"' for a in mcp_args)
            yaml_body = (
                "name: Syntopik Studio\n"
                "version: 0.0.1\n"
                "schema: v1\n"
                "mcpServers:\n"
                "  - name: syntopik-studio\n"
                f'    command: "{_yaml_escape(mcp_command)}"\n'
                "    args:\n"
                f"{args_yaml}\n"
            )
        if not dry_run:
            write_text_atomic(cont_mcp, yaml_body)
        written.append(".continue/mcpServers/syntopik.yaml")
        if not existed_cm:
            exclude_entries.append(".continue/mcpServers/syntopik.yaml")

    # 14. Kiro (`.kiro/steering/syntopik.md` + `.kiro/settings/mcp.json`)
    elif hid == "kiro":
        kiro_rule = target / ".kiro" / "steering" / "syntopik.md"
        existed_kr = kiro_rule.is_file()
        if not dry_run:
            write_text_atomic(
                kiro_rule,
                (
                    "---\n"
                    "title: Syntopik Deep-Mastery Co-Pilot\n"
                    "description: Syntopik universal deep-mastery co-pilot rules and study vault pointer\n"
                    "inclusion: manual\n"
                    "---\n\n"
                    f"For learning, deep-understanding, review, or exam-prep tasks, activate Syntopik skills in `.agents/skills/` "
                    f"and persist study state to `{vault_display}`. Keep turns <= 160w prose, 1 active demand/turn, zero sycophancy, "
                    "and verify math/code deterministically via `syntopik run <engine>`.\n"
                ),
            )
        written.append(".kiro/steering/syntopik.md")
        if not existed_kr:
            exclude_entries.append(".kiro/steering/syntopik.md")

        kiro_mcp = target / ".kiro" / "settings" / "mcp.json"
        mcp_config_path = str(kiro_mcp)
        kiro_entry = (
            {
                "url": http_url,
                "headers": {"Authorization": "Bearer ${SYNTOPIK_TOKEN}"},
                "disabled": False,
                "autoApprove": [],
            }
            if tr == "http"
            else {
                "command": mcp_command,
                "args": list(mcp_args),
                "disabled": False,
                "autoApprove": [],
            }
        )
        _apply_json_merge(
            kiro_mcp,
            lambda doc: doc.setdefault("mcpServers", {}).update({"syntopik-studio": kiro_entry}),
            ".kiro/settings/mcp.json",
            dry_run=dry_run,
            written=written,
            preserved=preserved,
            exclude_entries=exclude_entries,
            notes=notes,
        )

    result: dict[str, Any] = {
        "ok": True,
        "harness": hid,
        "transport": tr,
        "dryRun": bool(dry_run),
        "ephemeralInstallRoot": ephemeral,
        "mcpConfigPath": mcp_config_path,
        "written": written,
        "preserved": preserved,
        "excludeEntries": exclude_entries,
        "notes": notes,
    }
    if antigravity_info is not None:
        result["antigravity"] = antigravity_info
    return result
