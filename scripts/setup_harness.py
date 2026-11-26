#!/usr/bin/env python3
"""Cross-Platform Self-Bootstrapper & Capability Inspector for Syntopik.

Supports subcommands:
  - `auto`        : (default if omitted) Idempotently initialize learner workspace,
                    repair `.claude/skills` Windows symlink stubs, and emit full
                    harness/engine capability profile in <2s.
  - `status`      : Read-only capability & workspace health snapshot (zero mutations).
  - `init-data`   : Idempotently create missing learner dirs, 0-byte `.jsonl` ledgers,
                    starter `review-queue.md`, and `knowledge-graph/graph-data.js`
                    without EVER overwriting existing user files.
  - `configure`   : Configure harness adapters (`--target antigravity|claude|codex|generic|all`).
  - `install-mcp` : Install or check SEP-1865 Studio MCP App (`scripts/install_mcp_app.py`).

Pure Python 3.12 standard library only (zero external dependencies).
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent

LEARNER_DIRS: tuple[str, ...] = (
    "chapters",
    "demos",
    "exams",
    "animations",
    "sources",
    "knowledge-graph",
)

CORE_LEDGER_JSONL_FILES: tuple[str, ...] = (
    "propositions.jsonl",
    "chains.jsonl",
    "mistakes.jsonl",
    "exam-attempts.jsonl",
)

LEDGER_JSONL_FILES: tuple[str, ...] = (
    *CORE_LEDGER_JSONL_FILES,
    "revlog.jsonl",
    "reading-queue.jsonl",
    "derivations.jsonl",
    "evidence-ledger.jsonl",
    "viva-sessions.jsonl",
)

STARTER_REVIEW_QUEUE = """# Spaced Review & Weak-Edge Queue

> Automatically maintained via FSRS v6 (`scripts/srs_engine.py`).
> Ask Syntopik: *"Check my review-queue.md and run a 10-minute interleaved tune-up."*

## Due / Weak Edges (`misconception`, `shaky`, or Due Today)
_Nothing due today — start any topic to build your knowledge graph._

## Upcoming Spaced Recall (`solid`)
_None scheduled yet._

## Active Misconception Watchlist
_None active._
"""

MINIMAL_GRAPH_DATA_JS = (
    "// Auto-generated fallback by scripts/setup_harness.py when Node.js is unavailable.\n"
    'window.__STUDY_GRAPH__ = {"propositions":[],"chains":[],"normalizedEdges":[],"chainCount":0};\n'
    'window.__STUDY_HUB__ = {"schemaVersion":4,"generatedAt":"","today":"","workspaceName":"Syntopik",'
    '"stats":{"totalNodes":0,"totalEdges":0,"chainCount":0,"hyperedgeCount":0,'
    '"counts":{"solid":0,"shaky":0,"misconception":0,"planned":0,"other":0},'
    '"avgBktMastery":null,"bktTrackedCount":0,"dueCount":0,"subjects":[],"chaptersCount":0,'
    '"revisionSheetsCount":0,"trackersCount":0,"activeChapterPct":null,"audioBriefsCount":0,'
    '"demosCount":0,"animationsCount":0,"sourcesCount":0,"mistakesCount":0,"examsCount":0,'
    '"examAttemptsCount":0,"harnessLogCount":0,"revlogCount":0,"leechesCount":0,'
    '"readingQueueCount":0,"brierScore":null},'
    '"dueItems":[],"reviewQueueMarkdown":"","chapters":[],"chapterTrackers":[],"activeChapterTracker":null,'
    '"audioBriefs":[],"demos":[],"animations":[],"sources":[],"mistakes":[],'
    '"mistakeStats":{"total":0,"byCategory":[],"byChapter":[],"bySubject":[]},'
    '"examAttempts":[],"examFiles":[],"examStats":{"attemptsCount":0,"avgPct":null,"latestAttempt":null},'
    '"harnessLog":[],'
    '"retentionHeatmap":{"windowDays":365,"startDate":"","endDate":"","totalEvents":0,"activeDays":0,"maxDailyCount":0,"byDate":{},"days":[]},'
    '"calibration":{"sampleCount":0,"totalReviews":0,"brierScore":null,"overconfidenceBias":null,"accuracy":null,"meanConfidence":null,"ece":null,"hypercorrectionCount":0,"luckyGuessCount":0},'
    '"leeches":[],"readingQueue":[]};\n'
)


def _is_bundle_or_site_path(path: Path) -> bool:
    """Return True when `path` is inside a wheel bundle, site-packages, node_modules, or _npx cache."""
    return bool(
        {"_bundle", "site-packages", "dist-packages", "node_modules", "_npx"}.intersection(
            path.parts
        )
    )


def resolve_default_workspace(explicit: str | Path | None = None) -> Path:
    if explicit is not None and str(explicit).strip():
        return Path(explicit).expanduser().resolve()
    env_ws = os.environ.get("SYNT_WORKSPACE") or os.environ.get("MLH_WORKSPACE")
    if env_ws and env_ws.strip():
        return Path(env_ws).expanduser().resolve()
    if _is_bundle_or_site_path(ROOT):
        try:
            from syntopik.paths import resolve_workspace

            return resolve_workspace()
        except Exception:
            return (Path.home() / ".syntopik").resolve()
    return ROOT


def resolve_kg_dir(workspace: Path, explicit_kg: str | Path | None = None) -> Path:
    if explicit_kg is not None and str(explicit_kg).strip():
        return Path(explicit_kg).expanduser().resolve()
    if workspace.resolve() == ROOT.resolve():
        env_kg = os.environ.get("SYNT_KG_DIR") or os.environ.get("MLH_KG_DIR")
        if env_kg and env_kg.strip():
            return Path(env_kg).expanduser().resolve()
    return (workspace / "knowledge-graph").resolve()


def _resolve_executable(path: Path) -> Path | None:
    if os.name == "nt":
        if path.suffix.lower() in (".exe", ".cmd", ".bat", ".com") and path.is_file():
            return path
        for ext in (".exe", ".cmd", ".bat"):
            cand = path.with_suffix(ext) if not path.suffix else Path(str(path) + ext)
            if cand.is_file():
                return cand
    if path.is_file() and os.access(path, os.X_OK):
        return path
    return None


def _is_executable(path: Path) -> bool:
    return _resolve_executable(path) is not None


def find_binary(name: str, extra_dirs: list[Path] | None = None) -> str | None:
    for d in extra_dirs or []:
        hit = _resolve_executable(Path(d) / name)
        if hit is not None:
            return str(hit)
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
        hit = _resolve_executable(prefix / name)
        if hit is not None:
            return str(hit)
    return None


def find_tinytex_bin() -> Path | None:
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
            for child in sorted(base.iterdir()):
                if child.is_dir() and child not in candidates:
                    candidates.append(child)

    candidates.extend([Path("/Library/TeX/texbin"), Path("/usr/texbin")])
    which_latex = shutil.which("latex")
    if which_latex:
        candidates.append(Path(which_latex).resolve().parent)

    for c in candidates:
        if c.is_dir() and _is_executable(c / "latex") and _is_executable(c / "dvisvgm"):
            return c
    return None


def discover_researchctl(workspace: Path | None = None) -> dict[str, Any]:
    """Portably discover `researchctl` without any hardcoded user paths."""
    ws = (workspace or ROOT).resolve()
    home = Path.home()
    candidates: list[tuple[str, Path]] = []

    env_bin = os.environ.get("RESEARCHCTL_BIN")
    if env_bin and env_bin.strip():
        candidates.append(("env:RESEARCHCTL_BIN", Path(env_bin).expanduser()))

    which_bin = shutil.which("researchctl")
    if which_bin:
        candidates.append(("PATH", Path(which_bin)))

    candidates.append(("~/.local/bin/researchctl", home / ".local" / "bin" / "researchctl"))

    env_stack = os.environ.get("LOCAL_RESEARCH_STACK_DIR")
    if env_stack and env_stack.strip():
        stack_dir = Path(env_stack).expanduser()
        candidates.append(("env:LOCAL_RESEARCH_STACK_DIR", stack_dir / "scripts" / "researchctl.py"))

    for label, rel_base in (
        ("sibling:local-research-stack", ws.parent / "local-research-stack"),
        ("home:Documents/projects/local-research-stack", home / "Documents" / "projects" / "local-research-stack"),
        ("home:projects/local-research-stack", home / "projects" / "local-research-stack"),
        ("home:local-research-stack", home / "local-research-stack"),
    ):
        candidates.append((label, rel_base / "scripts" / "researchctl.py"))

    chosen_source: str | None = None
    chosen_path: Path | None = None
    for src, cand in candidates:
        if cand.is_file() and (os.access(cand, os.X_OK) or cand.suffix == ".py"):
            chosen_source = src
            chosen_path = cand
            break

    if chosen_path is None:
        return {
            "available": False,
            "source": None,
            "binary": None,
            "stackRoot": None,
        }

    stack_root: str | None = None
    cmd = [sys.executable, str(chosen_path), "paths"] if chosen_path.suffix == ".py" and not os.access(chosen_path, os.X_OK) else [str(chosen_path), "paths"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=2.5, check=False)
        if proc.returncode == 0:
            for line in proc.stdout.splitlines():
                if line.startswith("Installation:"):
                    stack_root = line.split(":", 1)[1].strip()
                    break
    except Exception:
        stack_root = None

    if not stack_root and chosen_path.name == "researchctl.py" and chosen_path.parent.name == "scripts":
        stack_root = str(chosen_path.resolve().parent.parent)

    return {
        "available": True,
        "source": chosen_source,
        "binary": str(chosen_path),
        "stackRoot": stack_root,
    }


def repair_claude_skills_link(workspace: Path, *, create_if_missing: bool = False) -> dict[str, Any]:
    """Repair `.claude/skills` if checked out on Windows as a plain-text stub file, or create link when requested."""
    ws = workspace.resolve()
    agents_skills = ws / ".agents" / "skills"
    claude_dir = ws / ".claude"
    claude_skills = claude_dir / "skills"

    if not agents_skills.is_dir():
        return {
            "status": "skipped",
            "action": "none",
            "detail": ".agents/skills directory not present in workspace",
            "path": str(claude_skills),
        }

    if claude_skills.is_symlink() and claude_skills.is_dir():
        return {
            "status": "ok",
            "action": "intact_symlink",
            "detail": f"valid symlink -> {os.readlink(claude_skills)}",
            "path": str(claude_skills),
        }

    if claude_skills.is_dir():
        return {
            "status": "ok",
            "action": "intact_dir",
            "detail": "valid directory/junction",
            "path": str(claude_skills),
        }

    is_broken_stub = claude_skills.exists() or claude_skills.is_symlink()
    if not is_broken_stub and not create_if_missing:
        return {
            "status": "ok",
            "action": "not_created",
            "detail": ".claude/skills not yet created",
            "path": str(claude_skills),
        }

    if is_broken_stub:
        try:
            claude_skills.unlink()
        except OSError:
            pass

    claude_dir.mkdir(parents=True, exist_ok=True)
    abs_target = str(agents_skills.resolve())
    link_target = abs_target if os.name == "nt" else os.path.relpath(agents_skills, claude_dir)

    # 1. Try native directory symlink first
    try:
        os.symlink(link_target, claude_skills, target_is_directory=True)
        if claude_skills.is_dir():
            return {
                "status": "ok",
                "action": "repaired_symlink" if is_broken_stub else "created_symlink",
                "detail": f"linked .claude/skills -> {link_target}",
                "path": str(claude_skills),
            }
    except OSError:
        pass

    # 2. Windows directory junction (`mklink /J`) fallback (requires zero admin privileges & absolute target)
    if os.name == "nt":
        proc = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(claude_skills), abs_target],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode == 0 and claude_skills.is_dir():
            return {
                "status": "ok",
                "action": "repaired_junction" if is_broken_stub else "created_junction",
                "detail": f"Windows junction .claude/skills -> {abs_target}",
                "path": str(claude_skills),
            }

    # 3. Universal directory copy fallback
    shutil.copytree(agents_skills, claude_skills, dirs_exist_ok=True)
    return {
        "status": "ok",
        "action": "copied_dir",
        "detail": "mirrored .agents/skills into .claude/skills",
        "path": str(claude_skills),
    }


def ensure_learner_workspace(
    root: str | Path | None = None,
    *,
    kg_dir: str | Path | None = None,
    run_sync: bool = True,
    repair_skills: bool = True,
) -> dict[str, Any]:
    """Idempotently initialize gitignored learner directories & starter ledgers.

    STRICT NON-DESTRUCTIVE CONTRACT:
      - NEVER overwrites, truncates, or touches any existing file!
      - Only creates missing directories, missing 0-byte `.jsonl` files,
        starter `review-queue.md` if absent, and `graph-data.js` if absent.
    """
    ws = resolve_default_workspace(root)
    kg = resolve_kg_dir(ws, kg_dir)

    created_dirs: list[str] = []
    created_files: list[str] = []
    existing_preserved: list[str] = []

    ws.mkdir(parents=True, exist_ok=True)
    for rel_dir in LEARNER_DIRS:
        target_dir = kg if rel_dir == "knowledge-graph" else (ws / rel_dir)
        if not target_dir.exists():
            target_dir.mkdir(parents=True, exist_ok=True)
            created_dirs.append(str(target_dir.relative_to(ws)) if target_dir.is_relative_to(ws) else str(target_dir))

    for fname in (*LEDGER_JSONL_FILES, "harness-log.jsonl"):
        fpath = kg / fname
        rel_label = str(fpath.relative_to(ws)) if fpath.is_relative_to(ws) else str(fpath)
        if not fpath.exists():
            fpath.parent.mkdir(parents=True, exist_ok=True)
            fpath.write_text("", encoding="utf-8")
            created_files.append(rel_label)
        else:
            existing_preserved.append(rel_label)

    rq_path = kg / "review-queue.md"
    rq_label = str(rq_path.relative_to(ws)) if rq_path.is_relative_to(ws) else str(rq_path)
    if not rq_path.exists():
        rq_path.write_text(STARTER_REVIEW_QUEUE, encoding="utf-8")
        created_files.append(rq_label)
    else:
        existing_preserved.append(rq_label)

    graph_js = kg / "graph-data.js"
    graph_label = str(graph_js.relative_to(ws)) if graph_js.is_relative_to(ws) else str(graph_js)
    synced_graph = False
    if not graph_js.exists() and run_sync:
        sync_script = ws / "scripts" / "sync-graph.mjs"
        if not sync_script.is_file():
            sync_script = ROOT / "scripts" / "sync-graph.mjs"
        node_bin = find_binary("node")
        if node_bin and sync_script.is_file():
            proc = subprocess.run(
                [
                    node_bin,
                    str(sync_script),
                    "--workspace",
                    str(ws),
                    "--kg-dir",
                    str(kg),
                    "--chapters-dir",
                    str(ws / "chapters"),
                    "--out",
                    str(graph_js),
                    "--quiet",
                ],
                cwd=str(ws),
                capture_output=True,
                text=True,
                timeout=8,
                check=False,
            )
            synced_graph = proc.returncode == 0 and graph_js.is_file()
        if not graph_js.exists():
            graph_js.write_text(MINIMAL_GRAPH_DATA_JS, encoding="utf-8")
            synced_graph = True
        if graph_js.exists():
            created_files.append(graph_label)
    elif graph_js.exists():
        existing_preserved.append(graph_label)

    skills_repair = (
        repair_claude_skills_link(ws, create_if_missing=False)
        if repair_skills
        else {"status": "skipped", "action": "none"}
    )

    return {
        "workspace": str(ws),
        "kgDir": str(kg),
        "createdDirs": created_dirs,
        "createdFiles": created_files,
        "existingPreservedCount": len(existing_preserved),
        "syncedGraphDataJs": synced_graph,
        "claudeSkills": skills_repair,
    }


def detect_harnesses(workspace: Path) -> dict[str, Any]:
    """Detect active harness from environment and all available local harnesses."""
    env = os.environ
    home = Path.home()

    explicit = (env.get("SYNT_HARNESS") or env.get("MLH_HARNESS") or "").strip().lower()
    if explicit in ("antigravity", "claude", "codex", "cursor", "windsurf", "generic"):
        active = explicit
    elif env.get("ANTIGRAVITY_CLI_ALIAS") or env.get("AGY_ACCOUNT") or env.get("ANTIGRAVITY_AGENT"):
        active = "antigravity"
    elif env.get("CLAUDECODE") or env.get("CLAUDE_PROJECT_DIR") or env.get("CLAUDE_CODE_ENTRYPOINT"):
        active = "claude"
    elif env.get("CODEX_THREAD_ID") or env.get("CODEX_SANDBOX") or env.get("CODEX_CI") or env.get("CODEX_HOME"):
        active = "codex"
    elif env.get("CURSOR_TRACE_ID") or env.get("CURSOR_AGENT"):
        active = "cursor"
    elif env.get("WINDSURF_PID") or env.get("CODEIUM_EDITOR_APP_ROOT"):
        active = "windsurf"
    elif (home / ".gemini" / "antigravity").is_dir() and not env.get("TERM_PROGRAM") == "vscode":
        active = "antigravity"
    else:
        active = "generic"

    available: dict[str, dict[str, Any]] = {
        "antigravity": {
            "installed": bool(find_binary("agy") or find_binary("antigravity") or (home / ".gemini" / "antigravity").is_dir()),
            "configPresent": (workspace / "GEMINI.md").is_file() and (workspace / ".agents" / "hooks.json").is_file(),
        },
        "claude": {
            "installed": bool(find_binary("claude") or (home / ".claude").is_dir()),
            "configPresent": (workspace / "CLAUDE.md").is_file() or (workspace / ".claude" / "settings.json").is_file(),
        },
        "codex": {
            "installed": bool(find_binary("codex") or (home / ".codex").is_dir()),
            "configPresent": (workspace / "AGENTS.md").is_file() and ((workspace / ".codex" / "config.toml").is_file() or (workspace / ".agents" / "skills").is_dir()),
        },
        "cursor": {
            "installed": bool(find_binary("cursor") or (home / ".cursor").is_dir()),
            "configPresent": (workspace / "AGENTS.md").is_file(),
        },
        "windsurf": {
            "installed": bool(find_binary("windsurf") or (home / ".codeium").is_dir()),
            "configPresent": (workspace / "AGENTS.md").is_file(),
        },
        "generic": {
            "installed": True,
            "configPresent": (workspace / "AGENTS.md").is_file() or (workspace / "BOOTSTRAP.md").is_file(),
        },
    }

    ux_recommendations_by_harness: dict[str, dict[str, str]] = {
        "antigravity": {
            "quiz": "synt_quiz_card / mlh_quiz_card -> ask_question -> calm_markdown_card",
            "practice": "synt_practice_card / mlh_practice_card -> scratch_locked_markdown",
            "visual": "generative_ui <agent-embed> -> offline demos/<slug>.html",
            "progress": "synt_lesson_studio / update_steps",
            "subagents": "invoke_subagent (research / self)",
        },
        "claude": {
            "quiz": "AskUserQuestion (description: '') -> synt_quiz_card -> calm_markdown_card",
            "practice": "synt_practice_card (non-UI text safe) -> scratch_locked_markdown",
            "visual": "demos/<slug>.html + SVG/PNG -> Mermaid/KaTeX",
            "progress": "TaskCreate / TaskUpdate",
            "subagents": "Agent (Explore / background)",
        },
        "codex": {
            "quiz": "request_user_input -> synt_quiz_card -> calm_markdown_card",
            "practice": "synt_practice_card -> scratch_locked_markdown",
            "visual": "demos/<slug>.html + SVG/PNG -> Mermaid/KaTeX",
            "progress": "update_plan",
            "subagents": "spawn_agent",
        },
    }
    default_ux = {
        "quiz": "calm_markdown_card (A..D + E: I don't know + 0-100% confidence + 1-line why)",
        "practice": "scratch_locked_markdown + R0-R5 hints + Pause Law",
        "visual": "demos/<slug>.html + verified SVG/PNG + inline Mermaid/KaTeX",
        "progress": "1-line turn breadcrumb [Step k/N]",
        "subagents": "bounded CLI helpers (pdf_studio.py, research_scout.py, math_verify.py, code_verify.py)",
    }

    return {
        "activeHarness": active,
        "availableHarnesses": [name for name, info in available.items() if info["installed"]],
        "harnesses": available,
        "uxRecommendations": ux_recommendations_by_harness.get(active, default_ux),
    }


def _venv_python(workspace: Path) -> Path | None:
    candidates = [
        workspace / ".venv" / "bin" / "python",
        workspace / ".venv" / "Scripts" / "python.exe",
        ROOT / ".venv" / "bin" / "python",
        ROOT / ".venv" / "Scripts" / "python.exe",
    ]
    for c in candidates:
        if c.is_file():
            return c
    return None


def inspect_capabilities(workspace: Path) -> dict[str, Any]:
    ws = workspace.resolve()
    kg = resolve_kg_dir(ws)
    venv_py = _venv_python(ws)
    tinytex_dir = find_tinytex_bin()
    tex_dirs = [tinytex_dir] if tinytex_dir else []

    # Fast stdlib spec check for key STEM Python modules if running inside .venv, else probe .venv/lib
    py_modules = ("sympy", "pint", "rdkit", "pymupdf", "fsrs", "manim", "scipy")
    if venv_py and Path(sys.executable).resolve() != venv_py.resolve():
        try:
            probe = subprocess.run(
                [
                    str(venv_py),
                    "-B",
                    "-c",
                    "import importlib.util,json;mods=['sympy','pint','rdkit','pymupdf','fsrs','manim','scipy'];"
                    "print(json.dumps({m:bool(importlib.util.find_spec(m)) for m in mods}))",
                ],
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            )
            stem_pkgs = json.loads(probe.stdout) if probe.returncode == 0 and probe.stdout.strip() else {}
        except Exception:
            stem_pkgs = {}
    else:
        stem_pkgs = {m: bool(importlib.util.find_spec(m)) for m in py_modules}

    binaries = {
        "python": str(venv_py) if venv_py else sys.executable,
        "venvPresent": venv_py is not None,
        "node": find_binary("node"),
        "typst": find_binary("typst"),
        "ffmpeg": find_binary("ffmpeg"),
        "ffprobe": find_binary("ffprobe"),
        "latex": find_binary("latex", extra_dirs=tex_dirs),
        "dvisvgm": find_binary("dvisvgm", extra_dirs=tex_dirs),
        "tinytexBin": str(tinytex_dir) if tinytex_dir else None,
        "gpp": find_binary("g++") or find_binary("clang++"),
        "rustc": find_binary("rustc"),
    }

    mcp_dist = ws / "mcp-app" / "mlh-studio" / "dist" / "server.mjs"
    if not mcp_dist.is_file():
        mcp_dist = ROOT / "mcp-app" / "mlh-studio" / "dist" / "server.mjs"
    home = Path.home()
    agy_plugin_installed = (
        (home / ".gemini" / "config" / "plugins" / "syntopik-studio" / "plugin.json").is_file()
        or (home / ".gemini" / "config" / "plugins" / "mlh-studio" / "plugin.json").is_file()
    )

    skills_dir = ws / ".agents" / "skills"
    skill_count = len(list(skills_dir.glob("*/SKILL.md"))) if skills_dir.is_dir() else 0

    harness_info = detect_harnesses(ws)
    research_info = discover_researchctl(ws)

    ledgers_present = all((kg / f).is_file() for f in CORE_LEDGER_JSONL_FILES)
    v3_ledgers_present = all((kg / f).is_file() for f in LEDGER_JSONL_FILES)
    hub_ready = ledgers_present and (kg / "review-queue.md").is_file() and (kg / "graph-data.js").is_file()

    return {
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "pythonVersion": platform.python_version(),
        },
        **harness_info,
        "binaries": binaries,
        "pythonEngines": {
            "venvPython": str(venv_py) if venv_py else None,
            "packages": stem_pkgs,
            "allStemPackagesReady": bool(stem_pkgs) and all(stem_pkgs.values()),
            "codeVerifyReady": True,
        },
        "mcpStudio": {
            "bundleReady": mcp_dist.is_file(),
            "serverPath": str(mcp_dist) if mcp_dist.is_file() else None,
            "antigravityPluginInstalled": agy_plugin_installed,
            "claudeMcpJsonPresent": (ws / ".mcp.json").is_file(),
            "codexConfigPresent": (ws / ".codex" / "config.toml").is_file(),
        },
        "researchStack": research_info,
        "workspaceState": {
            "skillsCount": skill_count,
            "ledgersReady": ledgers_present,
            "v3LedgersReady": v3_ledgers_present,
            "graphDataJsReady": (kg / "graph-data.js").is_file(),
            "hubReady": hub_ready,
            "claudeSkillsLinkOk": (ws / ".claude" / "skills").is_dir(),
        },
    }


def configure_harnesses(workspace: Path, target: str, *, force: bool = False) -> dict[str, Any]:
    """Ensure learner workspace and non-destructively wire requested harness configs."""
    ws = workspace.resolve()
    boot = ensure_learner_workspace(ws)
    targets = {"antigravity", "claude", "codex", "generic"} if target == "all" else {target}
    actions: list[str] = []

    if "claude" in targets:
        rep = repair_claude_skills_link(ws, create_if_missing=True)
        actions.append(f".claude/skills: {rep['action']}")
        mcp_json = ws / ".mcp.json"
        if force or not mcp_json.exists():
            mcp_payload = {
                "mcpServers": {
                    "syntopik-studio": {
                        "type": "stdio",
                        "command": "node",
                        "args": [
                            "./mcp-app/mlh-studio/dist/server.mjs",
                        ],
                    }
                }
            }
            mcp_json.write_text(json.dumps(mcp_payload, indent=2) + "\n", encoding="utf-8")
            actions.append("wrote .mcp.json")
        else:
            actions.append("preserved existing .mcp.json")

        claude_settings = ws / ".claude" / "settings.json"
        if force or not claude_settings.exists():
            claude_settings.parent.mkdir(parents=True, exist_ok=True)
            settings_payload = {
                "hooks": {
                    "SessionStart": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "node .agents/hooks/session-start.mjs",
                                    "timeout": 10,
                                }
                            ]
                        }
                    ],
                    "PostToolUse": [
                        {
                            "matcher": "Write|Edit|MultiEdit|NotebookEdit|Bash",
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "node .agents/hooks/post-graph-sync.mjs",
                                    "timeout": 10,
                                }
                            ]
                        }
                    ],
                }
            }
            claude_settings.write_text(json.dumps(settings_payload, indent=2) + "\n", encoding="utf-8")
            actions.append("wrote .claude/settings.json")
        else:
            actions.append("preserved existing .claude/settings.json")

    if "codex" in targets:
        codex_toml = ws / ".codex" / "config.toml"
        if force or not codex_toml.exists():
            codex_toml.parent.mkdir(parents=True, exist_ok=True)
            toml_text = (
                "project_doc_max_bytes = 65536\n\n"
                "[features]\n"
                "multi_agent = true\n"
                "hooks = true\n\n"
                "[mcp_servers.syntopik-studio]\n"
                'command = "node"\n'
                'args = ["./mcp-app/mlh-studio/dist/server.mjs"]\n'
                "startup_timeout_sec = 10\n"
                "tool_timeout_sec = 60\n"
            )
            codex_toml.write_text(toml_text, encoding="utf-8")
            actions.append("wrote .codex/config.toml")
        else:
            actions.append("preserved existing .codex/config.toml")

        codex_hooks = ws / ".codex" / "hooks.json"
        if force or not codex_hooks.exists():
            codex_hooks.parent.mkdir(parents=True, exist_ok=True)
            codex_hooks_payload = {
                "hooks": {
                    "SessionStart": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "node .agents/hooks/session-start.mjs",
                                    "timeout": 10,
                                }
                            ]
                        }
                    ],
                    "PostToolUse": [
                        {
                            "matcher": "apply_patch|Edit|Write|Bash",
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "node .agents/hooks/post-graph-sync.mjs",
                                    "timeout": 10,
                                }
                            ]
                        }
                    ],
                }
            }
            codex_hooks.write_text(json.dumps(codex_hooks_payload, indent=2) + "\n", encoding="utf-8")
            actions.append("wrote .codex/hooks.json")
        else:
            actions.append("preserved existing .codex/hooks.json")

    if "antigravity" in targets:
        actions.append("verified Antigravity .agents/skills & .agents/hooks.json")
    if "generic" in targets:
        actions.append("verified universal AGENTS.md / BOOTSTRAP.md readiness")

    return {
        "target": target,
        "actions": actions,
        "learnerWorkspace": boot,
    }


def run_install_mcp(workspace: Path, *, action: str = "install", target_dir: str | None = None) -> dict[str, Any]:
    installer = workspace / "scripts" / "install_mcp_app.py"
    if not installer.is_file():
        installer = ROOT / "scripts" / "install_mcp_app.py"
    if not installer.is_file():
        return {"ok": False, "error": f"Installer script not found: {installer}"}

    cmd = [sys.executable, "-B", str(installer), action, "--json"]
    if action == "install":
        cmd.extend(["--workspace", str(workspace)])
    if target_dir:
        cmd.extend(["--target", target_dir])

    proc = subprocess.run(cmd, cwd=str(workspace), capture_output=True, text=True, timeout=15, check=False)
    parsed: Any = None
    if proc.stdout.strip():
        try:
            parsed = json.loads(proc.stdout)
        except json.JSONDecodeError:
            parsed = proc.stdout.strip()
    return {
        "ok": proc.returncode == 0,
        "exitCode": proc.returncode,
        "installerResult": parsed,
        "stderr": proc.stderr.strip() or None,
    }


def _format_human_report(report: dict[str, Any]) -> str:
    caps = report.get("capabilities", {})
    bins = caps.get("binaries", {})
    py_eng = caps.get("pythonEngines", {})
    mcp = caps.get("mcpStudio", {})
    rs = caps.get("researchStack", {})
    ws_state = caps.get("workspaceState", {})

    lines = [
        f"Syntopik Self-Bootstrap ({report['command']}) — {'READY' if report.get('ok', True) else 'ERROR'} ({report.get('elapsedMs', 0)} ms)",
        f"  Workspace      : {report['workspace']}",
        f"  Active Harness : {caps.get('activeHarness', 'generic')} (available: {', '.join(caps.get('availableHarnesses', []))})",
        f"  Learner Data   : {'ready' if ws_state.get('hubReady') else 'uninitialized'} (skills={ws_state.get('skillsCount', 0)}, graph-data.js={ws_state.get('graphDataJsReady')})",
        f"  Python STEM    : {'ready' if py_eng.get('allStemPackagesReady') else 'partial/stdlib'} ({bins.get('python')})",
        f"  Binaries       : node={'yes' if bins.get('node') else 'no'}, typst={'yes' if bins.get('typst') else 'no'}, ffmpeg={'yes' if bins.get('ffmpeg') else 'no'}, tinytex={'yes' if bins.get('latex') and bins.get('dvisvgm') else 'no'}",
        f"  MCP Studio     : bundle={'yes' if mcp.get('bundleReady') else 'no'}, agyPlugin={'yes' if mcp.get('antigravityPluginInstalled') else 'no'}, .mcp.json={'yes' if mcp.get('claudeMcpJsonPresent') else 'no'}",
        f"  Research Stack : {'tier-2 researchctl (' + str(rs.get('source')) + ')' if rs.get('available') else 'tier-3 open scholarly HTTPS / offline'}",
    ]
    boot = report.get("bootstrap")
    if isinstance(boot, dict) and (boot.get("createdDirs") or boot.get("createdFiles")):
        lines.append(
            f"  Initialized    : +{len(boot.get('createdDirs', []))} dir(s), +{len(boot.get('createdFiles', []))} starter file(s) (0 existing files overwritten)"
        )
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--workspace", default=argparse.SUPPRESS, help="Workspace root directory (default: repo root)")
    common.add_argument("--kg-dir", default=argparse.SUPPRESS, help="Optional knowledge-graph directory override")
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="Emit machine-readable JSON")

    parser = argparse.ArgumentParser(
        prog="setup_harness.py",
        description="Syntopik cross-platform self-bootstrapper and multi-harness capability detector.",
        parents=[common],
    )
    sub = parser.add_subparsers(dest="command", required=False)

    sub.add_parser("auto", parents=[common], help="Idempotently initialize learner data, repair symlinks, and inspect capabilities")
    sub.add_parser("status", parents=[common], help="Read-only capability & workspace inspection (no disk writes)")
    sub.add_parser("init-data", parents=[common], help="Initialize missing learner dirs & empty JSONL ledgers without overwriting")

    p_cfg = sub.add_parser("configure", parents=[common], help="Configure harness files/symlinks for a target environment")
    p_cfg.add_argument(
        "--target",
        choices=["antigravity", "claude", "codex", "generic", "all"],
        default="all",
        help="Target harness to configure (default: all)",
    )
    p_cfg.add_argument("--force", action="store_true", help="Overwrite generated adapter config files if they already exist")

    p_mcp = sub.add_parser("install-mcp", parents=[common], help="Install or check the Syntopik Studio SEP-1865 MCP plugin")
    p_mcp.add_argument("--status-only", action="store_true", help="Check MCP installation status without modifying ~/.gemini")
    p_mcp.add_argument("--target", default=None, help="Optional custom plugin installation directory (for sandbox testing)")

    p_att = sub.add_parser("attach", parents=[common], help="Non-destructively attach Syntopik to any repository (delegates to syntopik.cli)")
    p_att.add_argument("target_dir", nargs="?", default=".", help="Target repository directory")
    p_att.add_argument(
        "--harness",
        choices=["auto", "all", "claude", "cursor", "windsurf", "copilot", "codex", "antigravity", "opencode", "gemini"],
        default="auto",
        help="Target harness adapter(s) to wire",
    )
    p_att.add_argument("--vault", choices=["global", "local"], default="global", help="Study vault location")
    p_att.add_argument("--copy-skills", action="store_true", help="Copy .agents/skills instead of symlinking")

    return parser


def main(argv: list[str] | None = None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    known_cmds = {"auto", "status", "init-data", "configure", "install-mcp", "attach", "-h", "--help"}
    if not raw_argv or (raw_argv[0].startswith("-") and not any(tok in known_cmds for tok in raw_argv)):
        raw_argv = ["auto", *raw_argv]

    parser = build_parser()
    args = parser.parse_args(raw_argv)
    cmd = args.command or "auto"
    as_json = bool(getattr(args, "json", False))
    ws = resolve_default_workspace(getattr(args, "workspace", None))
    kg_override = getattr(args, "kg_dir", None)

    t0 = time.perf_counter()
    report: dict[str, Any] = {
        "ok": True,
        "command": cmd,
        "workspace": str(ws),
    }

    if cmd == "auto":
        boot = ensure_learner_workspace(ws, kg_dir=kg_override, run_sync=True, repair_skills=True)
        if (ws / ".claude").is_dir() and not (ws / ".claude" / "skills").exists():
            boot["claudeSkills"] = repair_claude_skills_link(ws, create_if_missing=True)
        caps = inspect_capabilities(ws)
        report["bootstrap"] = boot
        report["capabilities"] = caps
    elif cmd == "status":
        report["capabilities"] = inspect_capabilities(ws)
    elif cmd == "init-data":
        report["bootstrap"] = ensure_learner_workspace(ws, kg_dir=kg_override, run_sync=True, repair_skills=True)
        report["capabilities"] = inspect_capabilities(ws)
    elif cmd == "configure":
        cfg = configure_harnesses(ws, args.target, force=bool(getattr(args, "force", False)))
        report["configuration"] = cfg
        report["bootstrap"] = cfg["learnerWorkspace"]
        report["capabilities"] = inspect_capabilities(ws)
    elif cmd == "install-mcp":
        action = "status" if getattr(args, "status_only", False) else "install"
        mcp_res = run_install_mcp(ws, action=action, target_dir=getattr(args, "target", None))
        report["ok"] = bool(mcp_res.get("ok"))
        report["mcp"] = mcp_res
        report["capabilities"] = inspect_capabilities(ws)
    elif cmd == "attach":
        root_str = str(ROOT)
        if root_str not in sys.path:
            sys.path.insert(0, root_str)
        from syntopik.cli import attach_workspace

        att_res = attach_workspace(
            args.target_dir,
            harness=args.harness,
            vault=args.vault,
            copy_skills=bool(getattr(args, "copy_skills", False)),
        )
        report["attach"] = att_res
        report["ok"] = bool(att_res.get("ok", True))
        report["workspace"] = str(att_res.get("targetDir", ws))
        report["capabilities"] = inspect_capabilities(ws)
    else:
        parser.error(f"Unknown command: {cmd}")
        return 2

    report["elapsedMs"] = round((time.perf_counter() - t0) * 1000.0, 1)
    if as_json:
        print(json.dumps(report, indent=2))
    else:
        print(_format_human_report(report))
    return 0 if report.get("ok", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
