"""Unified workspace, package-bundle, binary, and hub-sync path resolution.

Pure Python 3.12 standard library only (zero external dependencies).
"""

from __future__ import annotations

import importlib.resources
import os
import shutil
import subprocess
from pathlib import Path

__all__ = [
    "resolve_package_root",
    "resolve_workspace",
    "resolve_kg_dir",
    "resolve_chapters_dir",
    "resolve_exams_dir",
    "resolve_sources_dir",
    "resolve_demos_dir",
    "find_binary",
    "find_node",
    "find_tinytex_bin",
    "sync_hub",
]


def _is_writable_dir(path: Path) -> bool:
    try:
        return path.is_dir() and os.access(path, os.W_OK)
    except OSError:
        return False


def _is_repo_checkout(path: Path) -> bool:
    """Return True when `path` looks like a direct source checkout rather than a wheel/npx/plugin install."""
    excluded_parts = {
        "_bundle",
        "site-packages",
        "dist-packages",
        "node_modules",
        "_npx",
        ".claude",
        ".gemini",
    }
    if excluded_parts.intersection(path.parts):
        return False
    if not (path / "scripts").is_dir():
        return False
    return (
        (path / ".git").exists()
        or (path / "AGENTS.md").is_file()
        or (path / "knowledge-graph").is_dir()
    )


def resolve_package_root() -> Path:
    """Resolve the Syntopik repository root OR packaged `_bundle` root cleanly."""
    repo_candidate = Path(__file__).resolve().parent.parent
    if (repo_candidate / "scripts" / "sync-graph.mjs").is_file() or (
        (repo_candidate / "scripts").is_dir() and (repo_candidate / ".agents").is_dir()
    ):
        return repo_candidate

    try:
        bundle_ref = importlib.resources.files("syntopik").joinpath("_bundle")
        bundle_path = Path(str(bundle_ref)).resolve()
        if bundle_path.is_dir():
            return bundle_path
    except Exception:
        pass

    return repo_candidate


def _has_workspace_marker(path: Path) -> bool:
    return (path / "knowledge-graph").is_dir() or (path / ".syntopik").is_dir()


def resolve_workspace(explicit: Path | str | None = None) -> Path:
    """Resolve active Syntopik workspace root with strict precedence:

    1. `explicit` argument
    2. `SYNT_WORKSPACE` || `MLH_WORKSPACE`
    3. `$CLAUDE_PROJECT_DIR` or `Path.cwd()` if `knowledge-graph/` or `.syntopik/` exists
    4. Direct repo checkout root if writable
    5. `~/.syntopik`
    """
    if explicit is not None and str(explicit).strip():
        return Path(explicit).expanduser().resolve()

    env_ws = (
        os.environ.get("SYNT_WORKSPACE", "").strip()
        or os.environ.get("MLH_WORKSPACE", "").strip()
    )
    if env_ws:
        return Path(env_ws).expanduser().resolve()

    claude_dir = os.environ.get("CLAUDE_PROJECT_DIR", "").strip()
    if claude_dir:
        cand = Path(claude_dir).expanduser().resolve()
        if _has_workspace_marker(cand):
            return cand

    try:
        cwd = Path.cwd().resolve()
        if _has_workspace_marker(cwd):
            return cwd
    except OSError:
        pass

    pkg_root = resolve_package_root()
    if _is_repo_checkout(pkg_root) and _is_writable_dir(pkg_root):
        return pkg_root

    return (Path.home() / ".syntopik").resolve()


def resolve_kg_dir(
    workspace: Path | str | None = None,
    *,
    explicit: Path | str | None = None,
) -> Path:
    """Resolve `knowledge-graph/` directory honoring explicit arg, env overrides, and workspace."""
    if explicit is not None and str(explicit).strip():
        return Path(explicit).expanduser().resolve()
    if workspace is not None and str(workspace).strip():
        return (Path(workspace).expanduser().resolve() / "knowledge-graph").resolve()

    env_kg = (
        os.environ.get("SYNT_KG_DIR", "").strip()
        or os.environ.get("MLH_KG_DIR", "").strip()
    )
    if env_kg:
        return Path(env_kg).expanduser().resolve()

    return (resolve_workspace() / "knowledge-graph").resolve()


def resolve_chapters_dir(
    workspace: Path | str | None = None,
    *,
    explicit: Path | str | None = None,
) -> Path:
    """Resolve `chapters/` directory honoring explicit arg, env overrides, and workspace."""
    if explicit is not None and str(explicit).strip():
        return Path(explicit).expanduser().resolve()
    if workspace is not None and str(workspace).strip():
        return (Path(workspace).expanduser().resolve() / "chapters").resolve()

    env_ch = (
        os.environ.get("SYNT_CHAPTERS_DIR", "").strip()
        or os.environ.get("MLH_CHAPTERS_DIR", "").strip()
    )
    if env_ch:
        return Path(env_ch).expanduser().resolve()

    return (resolve_workspace() / "chapters").resolve()


def resolve_exams_dir(
    workspace: Path | str | None = None,
    *,
    explicit: Path | str | None = None,
) -> Path:
    if explicit is not None and str(explicit).strip():
        return Path(explicit).expanduser().resolve()
    return (resolve_workspace(workspace) / "exams").resolve()


def resolve_sources_dir(
    workspace: Path | str | None = None,
    *,
    explicit: Path | str | None = None,
) -> Path:
    if explicit is not None and str(explicit).strip():
        return Path(explicit).expanduser().resolve()
    return (resolve_workspace(workspace) / "sources").resolve()


def resolve_demos_dir(
    workspace: Path | str | None = None,
    *,
    explicit: Path | str | None = None,
) -> Path:
    if explicit is not None and str(explicit).strip():
        return Path(explicit).expanduser().resolve()
    return (resolve_workspace(workspace) / "demos").resolve()


def _is_executable(path: Path) -> bool:
    if path.is_file() and os.access(path, os.X_OK):
        return True
    if os.name == "nt":
        for ext in (".exe", ".cmd", ".bat"):
            cand = path.with_suffix(ext) if not path.suffix else Path(str(path) + ext)
            if cand.is_file():
                return True
    return False


def find_binary(name: str, extra_dirs: list[Path] | None = None) -> str | None:
    """Locate an executable portably across PATH and common macOS/Linux/Windows directories."""
    for d in extra_dirs or []:
        cand = Path(d) / name
        if _is_executable(cand):
            return str(cand)

    which_hit = shutil.which(name)
    if which_hit:
        return which_hit

    home = Path.home()
    fallback_dirs = [
        home / ".local" / "bin",
        home / ".cargo" / "bin",
        Path("/opt/homebrew/bin"),
        Path("/usr/local/bin"),
        Path("/usr/bin"),
    ]
    for prefix in fallback_dirs:
        cand = prefix / name
        if _is_executable(cand):
            return str(cand)
    return None


def find_node() -> str | None:
    """Locate Node.js portably across PATH, Homebrew, Volta, nvm, and standard Unix paths."""
    home = Path.home()
    extra: list[Path] = [home / ".volta" / "bin"]
    nvm_dir = home / ".nvm" / "versions" / "node"
    if nvm_dir.is_dir():
        try:
            for vdir in sorted(nvm_dir.iterdir(), reverse=True):
                extra.append(vdir / "bin")
        except OSError:
            pass
    hit = find_binary("node")
    if hit:
        return hit
    return find_binary("node", extra_dirs=extra)


def find_tinytex_bin() -> Path | None:
    """Locate a directory containing both `latex` and `dvisvgm` (TinyTeX or TeX Live)."""
    home = Path.home()
    candidates: list[Path] = []
    for env_var in ("TINYTEX_BIN", "TEXLIVE_BIN"):
        raw = os.environ.get(env_var)
        if raw and raw.strip():
            candidates.append(Path(raw).expanduser())

    roots = [
        home / "Library" / "TinyTeX" / "bin",
        home / ".TinyTeX" / "bin",
    ]
    appdata = os.environ.get("APPDATA")
    if appdata:
        roots.append(Path(appdata) / "TinyTeX" / "bin")

    known_archs = (
        "universal-darwin",
        "aarch64-darwin",
        "x86_64-darwin",
        "x86_64-linux",
        "aarch64-linux",
        "windows",
        "win32",
    )
    for base in roots:
        for arch in known_archs:
            candidates.append(base / arch)
        if base.is_dir():
            try:
                for child in sorted(base.iterdir()):
                    if child.is_dir() and child not in candidates:
                        candidates.append(child)
            except OSError:
                pass

    candidates.extend([Path("/Library/TeX/texbin"), Path("/usr/texbin")])
    which_latex = shutil.which("latex")
    if which_latex:
        candidates.append(Path(which_latex).resolve().parent)

    for c in candidates:
        if c.is_dir() and _is_executable(c / "latex") and _is_executable(c / "dvisvgm"):
            return c
    return None


def sync_hub(
    workspace: Path | str | None = None,
    kg_dir: Path | str | None = None,
    chapters_dir: Path | str | None = None,
    quiet: bool = True,
    *,
    out: Path | str | None = None,
) -> bool:
    """Refresh `knowledge-graph/graph-data.js` cleanly for any workspace via `sync-graph.mjs`."""
    pkg_root = resolve_package_root()
    sync_script = pkg_root / "scripts" / "sync-graph.mjs"
    if not sync_script.is_file():
        return False
    node_bin = find_node()
    if not node_bin:
        return False

    ws_path: Path
    if workspace is not None and str(workspace).strip():
        ws_path = Path(workspace).expanduser().resolve()
    elif kg_dir is not None and str(kg_dir).strip():
        kd = Path(kg_dir).expanduser().resolve()
        ws_path = kd.parent if kd.name == "knowledge-graph" else kd
    elif chapters_dir is not None and str(chapters_dir).strip():
        cd = Path(chapters_dir).expanduser().resolve()
        ws_path = cd.parent if cd.name == "chapters" else cd
    else:
        ws_path = resolve_workspace()

    cmd = [node_bin, str(sync_script), "--workspace", str(ws_path)]
    if kg_dir is not None and str(kg_dir).strip():
        cmd.extend(["--kg-dir", str(Path(kg_dir).expanduser().resolve())])
    if chapters_dir is not None and str(chapters_dir).strip():
        cmd.extend(["--chapters-dir", str(Path(chapters_dir).expanduser().resolve())])
    if out is not None and str(out).strip():
        cmd.extend(["--out", str(Path(out).expanduser().resolve())])
    if quiet:
        cmd.append("--quiet")

    try:
        proc = subprocess.run(
            cmd,
            cwd=str(ws_path if ws_path.is_dir() else pkg_root),
            check=False,
            stdout=subprocess.DEVNULL if quiet else None,
            stderr=subprocess.DEVNULL if quiet else None,
        )
        return proc.returncode == 0
    except OSError:
        return False
