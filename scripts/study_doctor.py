#!/usr/bin/env python3
"""Fast (<2s) Cross-Platform Workspace Health Doctor for Syntopik.

Audits every Python STEM & CS verification engine, TinyTeX / Typst / FFmpeg / Node
binary, multi-harness configuration (`Antigravity`, `Claude Code`, `OpenAI Codex`,
`BOOTSTRAP.md`), project skill YAML frontmatter (17 skills), 32 workspace scripts,
and auto-bootstraps missing gitignored learner files via `setup_harness.py` so a
fresh `git clone` passes with `0 warn, 0 fail`.
"""

from __future__ import annotations

import argparse
import importlib
import importlib.metadata as md
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import time
import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
HOME = Path.home()

SCRIPTS_DIR_STR = str(Path(__file__).resolve().parent)
if SCRIPTS_DIR_STR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR_STR)

from setup_harness import ensure_learner_workspace, repair_claude_skills_link  # noqa: E402

RULE_BUDGET_BYTES = 9500

EXPECTED_SCRIPTS: list[str] = [
    "pdf_studio.py",
    "math_verify.py",
    "chem_3d.py",
    "chem_physical.py",
    "circuit_draw.py",
    "physics_draw.py",
    "organic_reagents.py",
    "math_plot.py",
    "srs_engine.py",
    "cheatsheet_pdf.py",
    "narrate_video.py",
    "study_doctor.py",
    "manim_helpers.py",
    "mistake_analytics.py",
    "exam_runner.py",
    "install_mcp_app.py",
    "chapter_tracker.py",
    "graph_intel.py",
    "audio_brief.py",
    "daily_plan.py",
    "backup_harness.py",
    "eduniti_pyq.py",
    "setup_harness.py",
    "code_verify.py",
    "research_scout.py",
    "ingest_source.py",
    "vault_sync.py",
    "pedagogy_eval.py",
    "fork_replay.py",
    "falsify_arena.py",
    "decision_blitz.py",
    "sync-graph.mjs",
]

V3_SCRIPTS: list[str] = [
    "learner_profile.py",
    "derivation_forge.py",
    "viva_chamber.py",
    "build_starter_packs.py",
]

EXPECTED_SYNTOPIK_MODULES: list[str] = [
    "__init__.py",
    "paths.py",
    "storage.py",
    "schemas.py",
    "fsrs_core.py",
    "ingest.py",
    "obsidian.py",
    "forks.py",
    "packs.py",
    "pocket.py",
    "server.py",
    "cli.py",
]

V3_SYNTOPIK_MODULES: list[str] = [
    "learner_model.py",
    "root_cause.py",
    "evidence.py",
    "brainstem.py",
    "explorable.py",
    "work_mentor.py",
    "whiteboard.py",
    "sim_eval.py",
]

V31_SYNTOPIK_MODULES: list[str] = [
    "isomorphism.py",
    "sync_delta.py",
]

V32_SYNTOPIK_MODULES: list[str] = [
    "interleave.py",
    "readiness.py",
    "importers.py",
    "mcp_http.py",
    "agents_compat.py",
]

EXPECTED_SKILLS: list[str] = [
    "chapter-autopilot",
    "exam-simulator",
    "grade-handwriting",
    "harness-self-improve",
    "ingest-chapter",
    "knowledge-graph",
    "manim-animator",
    "organic-chemistry-tutor",
    "physical-chemistry",
    "physics-circuits-and-units",
    "practice-drill",
    "revision-sheets",
    "subagent-orchestrator",
    "teach-chapter",
    "topic-onboarding",
    "universal-pdf-study",
    "visual-and-demo",
]


def _is_executable(path: Path) -> bool:
    if path.is_file() and os.access(path, os.X_OK):
        return True
    if os.name == "nt":
        for ext in (".exe", ".cmd", ".bat"):
            cand = path.with_suffix(ext) if not path.suffix else Path(str(path) + ext)
            if cand.is_file():
                return True
    return False


def find_tinytex_bin() -> Path | None:
    candidates: list[Path] = []
    for env_var in ("TINYTEX_BIN", "TEXLIVE_BIN"):
        raw = os.environ.get(env_var)
        if raw and raw.strip():
            candidates.append(Path(raw).expanduser())

    roots = [
        HOME / "Library" / "TinyTeX" / "bin",
        HOME / ".TinyTeX" / "bin",
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


def find_binary(name: str, extra_dirs: list[Path] | None = None) -> str | None:
    for d in extra_dirs or []:
        cand = d / name
        if _is_executable(cand):
            return str(cand)
    which_hit = shutil.which(name)
    if which_hit:
        return which_hit
    prefixes: list[Path] = [
        HOME / ".local" / "bin",
        HOME / ".cargo" / "bin",
    ]
    if os.name == "nt":
        prog_files = os.environ.get("ProgramFiles")
        if prog_files:
            prefixes.append(Path(prog_files) / "nodejs")
        local_app = os.environ.get("LOCALAPPDATA")
        if local_app:
            prefixes.append(Path(local_app) / "Programs" / "nodejs")
    else:
        prefixes.extend([
            Path("/opt/homebrew/bin"),
            Path("/usr/local/bin"),
            Path("/usr/bin"),
        ])
    for prefix in prefixes:
        cand = prefix / name
        if _is_executable(cand):
            return str(cand)
    return None


def check_py_pkg(dist_name: str, import_name: str, label: str, required: bool = True, note: str = "") -> dict[str, Any]:
    try:
        ver = md.version(dist_name)
    except md.PackageNotFoundError:
        return {"name": label, "category": "python", "status": "fail" if required else "warn", "required": required, "detail": f"missing package '{dist_name}'"}
    try:
        spec = importlib.util.find_spec(import_name)
        if spec is None:
            return {"name": label, "category": "python", "status": "fail" if required else "warn", "required": required, "detail": f"installed {ver} but import '{import_name}' not found"}
        detail = f"{dist_name} {ver}" + (f" ({note})" if note else "")
        return {"name": label, "category": "python", "status": "ok", "required": required, "version": ver, "detail": detail}
    except Exception as exc:
        return {"name": label, "category": "python", "status": "fail" if required else "warn", "required": required, "detail": str(exc)}


def check_binary_tool(
    name: str,
    label: str,
    version_arg: str = "--version",
    extra_dirs: list[Path] | None = None,
    required: bool = True,
    optional_ok_when_missing: bool = False,
) -> dict[str, Any]:
    path = find_binary(name, extra_dirs=extra_dirs)
    if not path:
        if not required and optional_ok_when_missing:
            return {
                "name": label,
                "category": "binary",
                "status": "ok",
                "required": False,
                "installed": False,
                "detail": f"optional ('{name}' not installed)",
            }
        return {
            "name": label,
            "category": "binary",
            "status": "fail" if required else "warn",
            "required": required,
            "detail": f"'{name}' not found",
        }
    ver_line = ""
    if version_arg:
        try:
            proc = subprocess.run([path, version_arg], capture_output=True, text=True, timeout=3, check=False)
            out = (proc.stdout or proc.stderr or "").strip().splitlines()
            ver_line = out[0].strip()[:90] if out else ""
        except Exception:
            ver_line = ""
    return {
        "name": label,
        "category": "binary",
        "status": "ok",
        "required": required,
        "installed": True,
        "path": path,
        "detail": f"{path}" + (f" · {ver_line}" if ver_line else ""),
    }


def check_offline_tts() -> dict[str, Any]:
    if sys.platform == "darwin":
        return check_binary_tool("say", "Offline TTS (macOS say)", version_arg="", required=True)
    for alt in ("espeak-ng", "espeak"):
        alt_path = find_binary(alt)
        if alt_path:
            return {
                "name": "Offline TTS (System)",
                "category": "binary",
                "status": "ok",
                "required": False,
                "installed": True,
                "path": alt_path,
                "detail": f"{alt_path} (offline fallback alongside edge-tts)",
            }
    return {
        "name": "Offline TTS (System)",
        "category": "binary",
        "status": "ok",
        "required": False,
        "installed": False,
        "detail": "optional off macOS (using edge-tts neural TTS)",
    }


def _extract_yaml_description(fm: str) -> str:
    lines = fm.splitlines()
    for idx, line in enumerate(lines):
        m = re.match(r"^description:\s*(.*)$", line)
        if not m:
            continue
        val = m.group(1).strip()
        if val in (">", ">-", ">+", "|", "|-", "|+"):
            block: list[str] = []
            j = idx + 1
            while j < len(lines) and (lines[j].startswith((" ", "\t")) or not lines[j].strip()):
                if lines[j].strip():
                    block.append(lines[j].strip())
                j += 1
            return " ".join(block).strip()
        return val.strip("\"'")
    return ""


def _is_repo_checkout(path: Path) -> bool:
    """Return True only for a direct Syntopik repository checkout (never wheel/_bundle/vault)."""
    excluded = {"_bundle", "site-packages", "dist-packages", "node_modules", "_npx", ".claude", ".gemini"}
    if excluded.intersection(path.parts):
        return False
    return (
        (path / "scripts").is_dir()
        and (path / "syntopik").is_dir()
        and (path / "pyproject.toml").is_file()
    )


def _first_existing_dir(*candidates: Path) -> Path | None:
    for c in candidates:
        if c.is_dir():
            return c
    return None


def _first_existing_file(*candidates: Path) -> Path | None:
    for c in candidates:
        if c.is_file():
            return c
    return None


def check_skills(ws: Path, pkg_root: Path | None = None) -> dict[str, Any]:
    pr = pkg_root or ROOT
    skills_dir = _first_existing_dir(ws / ".agents" / "skills", pr / ".agents" / "skills", pr / "skills")
    if skills_dir is None:
        return {"name": "Project Skills (.agents/skills)", "category": "workspace", "status": "fail", "required": True, "detail": "directory missing"}
    found = sorted(skills_dir.glob("*/SKILL.md"))
    bad: list[str] = []
    names: list[str] = []
    for sk in found:
        txt = sk.read_text(encoding="utf-8", errors="replace")
        m = re.match(r"^---\s*\n(.*?)\n---\s*\n", txt, flags=re.DOTALL)
        if not m:
            bad.append(f"{sk.parent.name}: missing YAML frontmatter")
            continue
        fm = m.group(1)
        m_name = re.search(r"^name:\s*['\"]?([^'\"\n#]+?)['\"]?\s*$", fm, re.MULTILINE)
        desc_val = _extract_yaml_description(fm)
        if not (m_name and desc_val):
            bad.append(f"{sk.parent.name}: missing name/description")
        elif m_name.group(1).strip() != sk.parent.name:
            bad.append(f"{sk.parent.name}: frontmatter name '{m_name.group(1).strip()}' != folder")
        elif "<" in desc_val or ">" in desc_val:
            bad.append(f"{sk.parent.name}: YAML description contains literal angle brackets (< or >)")
        else:
            names.append(sk.parent.name)

    missing_expected = [s for s in EXPECTED_SKILLS if s not in names]
    if missing_expected:
        bad.append(f"missing expected skill(s): {', '.join(missing_expected)}")

    status = "ok" if found and not bad else "fail"
    detail = f"{len(names)} valid skills ({', '.join(names)})" if not bad else f"{len(bad)} issue(s): {'; '.join(bad)}"
    return {
        "name": "Project Skills (.agents/skills)",
        "category": "workspace",
        "status": status,
        "required": True,
        "count": len(names),
        "expectedCount": len(EXPECTED_SKILLS),
        "detail": detail,
    }


def check_hooks(ws: Path, pkg_root: Path | None = None, *, is_repo: bool = True) -> dict[str, Any]:
    pr = pkg_root or ROOT
    hooks_dir = _first_existing_dir(ws / ".agents" / "hooks", pr / ".agents" / "hooks", pr / "hooks")
    scripts = [hooks_dir / "session-start.mjs", hooks_dir / "post-graph-sync.mjs"] if hooks_dir else []
    missing_scripts = [s.name for s in scripts if not s.exists()] if scripts else ["session-start.mjs", "post-graph-sync.mjs"]
    if not is_repo:
        if missing_scripts:
            return {"name": "Lifecycle Hooks (.agents/hooks.json)", "category": "workspace", "status": "fail", "required": True, "detail": f"missing bundled hook scripts: {', '.join(missing_scripts)}"}
        return {"name": "Lifecycle Hooks (.agents/hooks.json)", "category": "workspace", "status": "ok", "required": True, "detail": "2 bundled tri-protocol hook scripts present (external vault mode)"}
    hooks_json = ws / ".agents" / "hooks.json"
    if not hooks_json.exists():
        return {"name": "Lifecycle Hooks (.agents/hooks.json)", "category": "workspace", "status": "fail", "required": True, "detail": "hooks.json missing"}
    try:
        data = json.loads(hooks_json.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"name": "Lifecycle Hooks (.agents/hooks.json)", "category": "workspace", "status": "fail", "required": True, "detail": f"invalid JSON: {exc}"}
    if missing_scripts:
        return {"name": "Lifecycle Hooks (.agents/hooks.json)", "category": "workspace", "status": "fail", "required": True, "detail": f"missing hook scripts: {', '.join(missing_scripts)}"}
    return {"name": "Lifecycle Hooks (.agents/hooks.json)", "category": "workspace", "status": "ok", "required": True, "detail": f"{len(data)} hook groups + 2 tri-protocol hook scripts present"}


def check_multi_harness_configs(ws: Path) -> dict[str, Any]:
    targets = [
        ("CLAUDE.md", ws / "CLAUDE.md", "claude_md"),
        ("BOOTSTRAP.md", ws / "BOOTSTRAP.md", "md"),
        (".claude/settings.json", ws / ".claude" / "settings.json", "json"),
        (".mcp.json", ws / ".mcp.json", "json"),
        (".codex/config.toml", ws / ".codex" / "config.toml", "toml"),
        (".codex/hooks.json", ws / ".codex" / "hooks.json", "json"),
    ]
    present: list[str] = []
    missing: list[str] = []
    errors: list[str] = []

    for label, path, kind in targets:
        if path.is_file():
            present.append(label)
            raw_txt = path.read_text(encoding="utf-8", errors="replace")
            if kind == "json":
                try:
                    json.loads(raw_txt)
                except Exception as exc:
                    errors.append(f"{label}: invalid JSON ({exc})")
                if "$(git rev-parse" in raw_txt or re.search(r"\$\{[A-Za-z0-9_]+:-", raw_txt):
                    errors.append(f"{label}: contains POSIX-only shell syntax (breaks Windows)")
            elif kind == "toml":
                try:
                    tomllib.loads(raw_txt)
                except Exception as exc:
                    errors.append(f"{label}: invalid TOML ({exc})")
            elif kind == "claude_md":
                first_line = next((ln.strip() for ln in raw_txt.splitlines() if ln.strip()), "")
                if first_line != "@AGENTS.md":
                    errors.append(f"CLAUDE.md line 1 must be '@AGENTS.md' to prevent masking AGENTS.md (got {first_line!r})")
        else:
            missing.append(label)

    claude_skills = ws / ".claude" / "skills"
    if claude_skills.exists() and not claude_skills.is_dir():
        errors.append(".claude/skills is a plain file stub instead of a symlink/directory")

    if errors:
        return {
            "name": "Multi-Harness Configs",
            "category": "workspace",
            "status": "fail",
            "required": True,
            "count": len(present),
            "detail": "; ".join(errors),
        }

    if len(present) == len(targets):
        detail = f"{len(present)}/{len(targets)} configs OK ({', '.join(present)})"
    else:
        detail = f"{len(present)}/{len(targets)} present ({', '.join(present) or 'Antigravity native'})"

    return {
        "name": "Multi-Harness Configs",
        "category": "workspace",
        "status": "ok",
        "required": False,
        "count": len(present),
        "allPresent": len(present) == len(targets),
        "missing": missing,
        "detail": detail,
    }


def check_google_chemistry_plugin() -> dict[str, Any]:
    plugin_root = HOME / ".gemini" / "config" / "plugins" / "chemistry"
    plugin_json = plugin_root / "plugin.json"
    mech_script = plugin_root / "skills" / "cheminformatics_reactions" / "scripts" / "render_mechanism.py"
    draw_script = plugin_root / "skills" / "rdkit_general" / "scripts" / "draw_molecules.py"
    if not plugin_json.exists():
        return {
            "name": "Google Chemistry Plugin",
            "category": "plugin",
            "status": "ok",
            "required": False,
            "installed": False,
            "detail": "optional (not installed — using built-in RDKit + chem_3d.py + organic_reagents.py)",
        }
    missing = [p.name for p in (mech_script, draw_script) if not p.exists()]
    if missing:
        return {
            "name": "Google Chemistry Plugin",
            "category": "plugin",
            "status": "ok",
            "required": False,
            "installed": False,
            "detail": f"optional (incomplete plugin, falling back to built-in RDKit; missing {', '.join(missing)})",
        }
    return {
        "name": "Google Chemistry Plugin",
        "category": "plugin",
        "status": "ok",
        "required": False,
        "installed": True,
        "detail": f"{plugin_root} (rdkit_general + cheminformatics_reactions)",
    }


def check_workspace_scripts(ws: Path, pkg_root: Path | None = None) -> dict[str, Any]:
    pr = pkg_root or ROOT
    scripts_dir = _first_existing_dir(ws / "scripts", pr / "scripts")
    if scripts_dir is None:
        return {
            "name": "Workspace Scripts (scripts/)",
            "category": "workspace",
            "status": "fail",
            "required": True,
            "count": 0,
            "expectedCount": len(EXPECTED_SCRIPTS),
            "detail": "scripts/ directory missing",
        }
    missing = [s for s in EXPECTED_SCRIPTS if not (scripts_dir / s).exists()]
    present_v3 = [s for s in V3_SCRIPTS if (scripts_dir / s).exists()]
    missing_v3 = [s for s in V3_SCRIPTS if s not in present_v3]
    if missing:
        return {
            "name": "Workspace Scripts (scripts/)",
            "category": "workspace",
            "status": "fail",
            "required": True,
            "count": len(EXPECTED_SCRIPTS) - len(missing) + len(present_v3),
            "expectedCount": len(EXPECTED_SCRIPTS) + len(V3_SCRIPTS),
            "v3Present": len(present_v3),
            "v3Missing": missing_v3,
            "detail": f"missing: {', '.join(missing)}",
        }
    total_present = len(EXPECTED_SCRIPTS) + len(present_v3)
    if not missing_v3:
        detail = f"all {total_present} scripts present (32 core + {len(present_v3)} v3.0)"
    else:
        detail = f"{len(EXPECTED_SCRIPTS)}/{len(EXPECTED_SCRIPTS)} core + {len(present_v3)}/{len(V3_SCRIPTS)} v3.0 scripts present"
    return {
        "name": "Workspace Scripts (scripts/)",
        "category": "workspace",
        "status": "ok",
        "required": True,
        "count": total_present,
        "expectedCount": len(EXPECTED_SCRIPTS) + len(V3_SCRIPTS),
        "v3Present": len(present_v3),
        "v3Missing": missing_v3,
        "detail": detail,
    }


def check_syntopik_package(ws: Path, pkg_root: Path | None = None) -> dict[str, Any]:
    pr = pkg_root or ROOT
    pkg_candidates: list[Path] = [ws / "syntopik", pr / "syntopik"]
    if pr.name == "_bundle" and pr.parent.name == "syntopik":
        pkg_candidates.append(pr.parent)
    pkg_dir = _first_existing_dir(*pkg_candidates)
    if pkg_dir is None:
        try:
            import syntopik as _synt_mod

            pkg_dir = Path(_synt_mod.__file__).resolve().parent
        except Exception:
            pkg_dir = ws / "syntopik"
    missing = [m for m in EXPECTED_SYNTOPIK_MODULES if not (pkg_dir / m).is_file()]
    present_v3 = [m for m in V3_SYNTOPIK_MODULES if (pkg_dir / m).is_file()]
    missing_v3 = [m for m in V3_SYNTOPIK_MODULES if m not in present_v3]
    present_v31 = [m for m in V31_SYNTOPIK_MODULES if (pkg_dir / m).is_file()]
    missing_v31 = [m for m in V31_SYNTOPIK_MODULES if m not in present_v31]
    present_v32 = [m for m in V32_SYNTOPIK_MODULES if (pkg_dir / m).is_file()]
    missing_v32 = [m for m in V32_SYNTOPIK_MODULES if m not in present_v32]
    expected_total = (
        len(EXPECTED_SYNTOPIK_MODULES)
        + len(V3_SYNTOPIK_MODULES)
        + len(V31_SYNTOPIK_MODULES)
        + len(V32_SYNTOPIK_MODULES)
    )
    if missing:
        return {
            "name": "Core Package (syntopik/)",
            "category": "workspace",
            "status": "fail",
            "required": True,
            "count": len(EXPECTED_SYNTOPIK_MODULES) - len(missing) + len(present_v3) + len(present_v31) + len(present_v32),
            "expectedCount": expected_total,
            "v3Present": len(present_v3),
            "v3Missing": missing_v3,
            "v31Present": len(present_v31),
            "v31Missing": missing_v31,
            "v32Present": len(present_v32),
            "v32Missing": missing_v32,
            "detail": f"missing: {', '.join(missing)}",
        }
    total_present = len(EXPECTED_SYNTOPIK_MODULES) + len(present_v3) + len(present_v31) + len(present_v32)
    if not missing_v3 and not missing_v31 and not missing_v32:
        detail = (
            f"all {total_present} pure-stdlib modules present "
            f"(12 core + {len(present_v3)} v3.0 + {len(present_v31)} v3.1 + {len(present_v32)} v3.2)"
        )
    else:
        detail = (
            f"{len(EXPECTED_SYNTOPIK_MODULES)}/{len(EXPECTED_SYNTOPIK_MODULES)} core + "
            f"{len(present_v3)}/{len(V3_SYNTOPIK_MODULES)} v3.0 + "
            f"{len(present_v31)}/{len(V31_SYNTOPIK_MODULES)} v3.1 + "
            f"{len(present_v32)}/{len(V32_SYNTOPIK_MODULES)} v3.2 pure-stdlib modules present"
        )
    return {
        "name": "Core Package (syntopik/)",
        "category": "workspace",
        "status": "ok",
        "required": True,
        "count": total_present,
        "expectedCount": expected_total,
        "v3Present": len(present_v3),
        "v3Missing": missing_v3,
        "v31Present": len(present_v31),
        "v31Missing": missing_v31,
        "v32Present": len(present_v32),
        "v32Missing": missing_v32,
        "detail": detail,
    }


def check_packaging_and_cli(ws: Path, *, is_repo: bool = True) -> dict[str, Any]:
    if not is_repo:
        return {
            "name": "Dual Packaging & CLI (Py/Node)",
            "category": "workspace",
            "status": "ok",
            "required": False,
            "detail": "external vault / wheel mode (repo packaging manifest check skipped)",
        }
    issues: list[str] = []
    pyproj = ws / "pyproject.toml"
    pkg_json = ws / "package.json"
    cli_bin = ws / "bin" / "syntopik.mjs"

    if not pyproj.is_file():
        issues.append("pyproject.toml missing")
    else:
        try:
            pt = tomllib.loads(pyproj.read_text(encoding="utf-8"))
            if ((pt.get("project") or {}).get("scripts") or {}).get("syntopik") != "syntopik.cli:main":
                issues.append("pyproject.toml missing [project.scripts] syntopik = 'syntopik.cli:main'")
        except Exception as exc:
            issues.append(f"pyproject.toml invalid TOML ({exc})")

    if not pkg_json.is_file():
        issues.append("package.json missing")
    else:
        try:
            pj = json.loads(pkg_json.read_text(encoding="utf-8"))
            if (pj.get("bin") or {}).get("syntopik") != "./bin/syntopik.mjs":
                issues.append("package.json missing bin.syntopik -> ./bin/syntopik.mjs")
        except Exception as exc:
            issues.append(f"package.json invalid JSON ({exc})")

    if not cli_bin.is_file():
        issues.append("bin/syntopik.mjs missing")
    elif os.name != "nt" and not os.access(cli_bin, os.X_OK):
        issues.append("bin/syntopik.mjs not executable")

    if issues:
        return {
            "name": "Dual Packaging & CLI (Py/Node)",
            "category": "workspace",
            "status": "fail",
            "required": True,
            "detail": "; ".join(issues),
        }
    return {
        "name": "Dual Packaging & CLI (Py/Node)",
        "category": "workspace",
        "status": "ok",
        "required": True,
        "detail": "pyproject.toml + package.json + executable bin/syntopik.mjs OK",
    }


def check_plugin_manifests(ws: Path, *, is_repo: bool = True) -> dict[str, Any]:
    if not is_repo:
        return {
            "name": "Plugin & Extension Manifests",
            "category": "plugin",
            "status": "ok",
            "required": False,
            "detail": "external vault / wheel mode (repo plugin manifest check skipped)",
        }
    manifest_rels = (
        ".claude-plugin/plugin.json",
        ".claude-plugin/marketplace.json",
        ".claude-plugin/hooks.json",
        "gemini-extension.json",
    )
    issues: list[str] = []
    for rel in manifest_rels:
        p = ws / rel
        if not p.is_file():
            issues.append(f"{rel} missing")
            continue
        try:
            json.loads(p.read_text(encoding="utf-8"))
        except Exception as exc:
            issues.append(f"{rel} invalid JSON ({exc})")

    if issues:
        return {
            "name": "Plugin & Extension Manifests",
            "category": "plugin",
            "status": "fail",
            "required": True,
            "detail": "; ".join(issues),
        }
    return {
        "name": "Plugin & Extension Manifests",
        "category": "plugin",
        "status": "ok",
        "required": True,
        "count": len(manifest_rels),
        "detail": "4/4 valid (.claude-plugin/{plugin,marketplace,hooks}.json + gemini-extension.json)",
    }


def check_context_footprint(ws: Path) -> dict[str, Any]:
    rule_bytes = sum(
        (ws / name).stat().st_size
        for name in ("GEMINI.md", "AGENTS.md")
        if (ws / name).exists()
    )
    rule_tokens = rule_bytes // 4
    skills_dir = ws / ".agents" / "skills"
    fm_bytes = 0
    skill_count = 0
    if skills_dir.exists():
        for sk in sorted(skills_dir.glob("*/SKILL.md")):
            txt = sk.read_text(encoding="utf-8", errors="replace")
            m = re.match(r"^---\s*\n(.*?)\n---\s*\n", txt, flags=re.DOTALL)
            if m:
                fm_bytes += len(m.group(1).encode("utf-8"))
                skill_count += 1
    skill_tokens = fm_bytes // 4

    optional_names = {"genomics", "structural-biology", "scientific-literature", "science-overview", "rea", "reverse-engineer-anything"}
    plugins_dir = HOME / ".gemini" / "config" / "plugins"
    unused_global: list[str] = []
    if plugins_dir.exists():
        for child in sorted(plugins_dir.iterdir()):
            if child.is_dir() and child.name in optional_names:
                unused_global.append(child.name)

    tip = (
        f" · tip: disable unused global plugins ({', '.join(unused_global)}) if unneeded"
        if unused_global
        else " · global plugins lean"
    )
    return {
        "name": "Context Footprint Audit",
        "category": "workspace",
        "status": "ok",
        "required": False,
        "ruleBytes": rule_bytes,
        "ruleTokens": rule_tokens,
        "skillCount": skill_count,
        "skillFrontmatterTokens": skill_tokens,
        "optionalGlobalPlugins": unused_global,
        "detail": f"rules {rule_bytes} B (~{rule_tokens} tok) + {skill_count} skills (~{skill_tokens} tok){tip}",
    }


def check_mcp_studio(ws: Path, pkg_root: Path | None = None) -> dict[str, Any]:
    pr = pkg_root or ROOT
    dist_dir = _first_existing_dir(
        ws / "mcp-app" / "mlh-studio" / "dist",
        pr / "mcp-app" / "mlh-studio" / "dist",
        pr / "mcp-dist",
    ) or (ws / "mcp-app" / "mlh-studio" / "dist")
    expected_bundle = ["server.mjs", "quiz-card.html", "practice-card.html", "studio-pane.html"]
    missing = [f for f in expected_bundle if not (dist_dir / f).exists()]

    synt_plugin = HOME / ".gemini" / "config" / "plugins" / "syntopik-studio" / "plugin.json"
    mlh_plugin = HOME / ".gemini" / "config" / "plugins" / "mlh-studio" / "plugin.json"
    installed_plugin = synt_plugin if synt_plugin.exists() else (mlh_plugin if mlh_plugin.exists() else None)

    install_note = (
        f"installed at {installed_plugin.parent}"
        if installed_plugin is not None
        else "optional plugin ready (run scripts/install_mcp_app.py install)"
    )
    if missing:
        return {
            "name": "MCP App Bundle (Syntopik Studio)",
            "category": "plugin",
            "status": "fail",
            "required": True,
            "detail": f"missing dist files: {', '.join(missing)}",
        }
    return {
        "name": "MCP App Bundle (Syntopik Studio)",
        "category": "plugin",
        "status": "ok",
        "required": True,
        "installed": installed_plugin is not None,
        "detail": f"4/4 dist bundles OK · {install_note}",
    }


def check_reagents_db(ws: Path, pkg_root: Path | None = None) -> dict[str, Any]:
    pr = pkg_root or ROOT
    db_path = _first_existing_file(
        ws / "knowledge-graph" / "reagents.json",
        pr / "knowledge-graph" / "reagents.json",
        pr / "reagents.json",
    )
    if db_path is None:
        return {"name": "Reagents DB (reagents.json)", "category": "workspace", "status": "fail", "required": True, "detail": "knowledge-graph/reagents.json missing"}
    try:
        data = json.loads(db_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"name": "Reagents DB (reagents.json)", "category": "workspace", "status": "fail", "required": True, "detail": f"invalid JSON: {exc}"}
    reagents = data.get("reagents") if isinstance(data, dict) else None
    if not isinstance(reagents, list) or len(reagents) < 25:
        count = len(reagents) if isinstance(reagents, list) else 0
        return {"name": "Reagents DB (reagents.json)", "category": "workspace", "status": "fail", "required": True, "detail": f"expected >= 25 reagents, found {count}"}
    missing_id = sum(1 for r in reagents if not isinstance(r, dict) or not r.get("id"))
    if missing_id:
        return {"name": "Reagents DB (reagents.json)", "category": "workspace", "status": "fail", "required": True, "detail": f"{missing_id} entries missing id"}
    fgs = data.get("functionalGroups") if isinstance(data.get("functionalGroups"), list) else []
    return {
        "name": "Reagents DB (reagents.json)",
        "category": "workspace",
        "status": "ok",
        "required": True,
        "count": len(reagents),
        "detail": f"reagents.json valid ({len(reagents)} reagents, {len(fgs)} functional groups)",
    }


def check_archetypes_db(ws: Path, pkg_root: Path | None = None) -> dict[str, Any]:
    pr = pkg_root or ROOT
    candidates: list[Path] = [
        ws / "syntopik" / "data" / "archetypes.json",
        pr / "syntopik" / "data" / "archetypes.json",
    ]
    if pr.name == "_bundle" and pr.parent.name == "syntopik":
        candidates.append(pr.parent / "data" / "archetypes.json")
    try:
        spec = importlib.util.find_spec("syntopik")
        if spec and spec.origin:
            candidates.append(Path(spec.origin).resolve().parent / "data" / "archetypes.json")
    except Exception:
        pass

    db_path = _first_existing_file(*candidates)
    if db_path is None:
        iso_present = _first_existing_file(
            ws / "syntopik" / "isomorphism.py",
            pr / "syntopik" / "isomorphism.py",
            *(
                [pr.parent / "isomorphism.py"]
                if pr.name == "_bundle" and pr.parent.name == "syntopik"
                else []
            ),
        ) is not None
        if iso_present:
            return {
                "name": "Rosetta Archetypes (archetypes.json)",
                "category": "workspace",
                "status": "fail",
                "required": True,
                "detail": "syntopik/data/archetypes.json missing",
            }
        return {
            "name": "Rosetta Archetypes (archetypes.json)",
            "category": "workspace",
            "status": "ok",
            "required": False,
            "detail": "v3.1 Rosetta archetypes pending (isomorphism.py not yet present)",
        }

    try:
        data = json.loads(db_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {
            "name": "Rosetta Archetypes (archetypes.json)",
            "category": "workspace",
            "status": "fail",
            "required": True,
            "detail": f"invalid JSON: {exc}",
        }

    archetypes = data if isinstance(data, list) else (data.get("archetypes") if isinstance(data, dict) else None)
    if not isinstance(archetypes, list) or len(archetypes) < 10:
        count = len(archetypes) if isinstance(archetypes, list) else 0
        return {
            "name": "Rosetta Archetypes (archetypes.json)",
            "category": "workspace",
            "status": "fail",
            "required": True,
            "detail": f"expected >= 10 archetypes, found {count}",
        }
    missing_id = sum(1 for a in archetypes if not isinstance(a, dict) or not a.get("id"))
    if missing_id:
        return {
            "name": "Rosetta Archetypes (archetypes.json)",
            "category": "workspace",
            "status": "fail",
            "required": True,
            "detail": f"{missing_id} archetype entries missing id",
        }
    return {
        "name": "Rosetta Archetypes (archetypes.json)",
        "category": "workspace",
        "status": "ok",
        "required": True,
        "count": len(archetypes),
        "detail": f"archetypes.json valid ({len(archetypes)} structural archetypes)",
    }


def check_knowledge_hub(ws: Path, kg_dir: Path, pkg_root: Path | None = None) -> dict[str, Any]:
    pr = pkg_root or ROOT
    required_files = [
        kg_dir / "propositions.jsonl",
        kg_dir / "chains.jsonl",
        kg_dir / "mistakes.jsonl",
        kg_dir / "exam-attempts.jsonl",
        kg_dir / "harness-log.jsonl",
        kg_dir / "review-queue.md",
        kg_dir / "graph-data.js",
    ]
    missing = [str(p.relative_to(ws)) if p.is_relative_to(ws) else str(p) for p in required_files if not p.exists()]
    viewer_file = _first_existing_file(
        ws / "knowledge-graph" / "viewer.html",
        pr / "knowledge-graph" / "viewer.html",
        pr / "viewer.html",
    )
    if viewer_file is None:
        missing.append("knowledge-graph/viewer.html")
    has_dashboard = _first_existing_file(ws / "dashboard.html", pr / "dashboard.html") is not None
    if missing:
        return {"name": "Knowledge Graph & Hub", "category": "workspace", "status": "fail", "required": True, "detail": f"missing: {', '.join(missing)}"}
    detail = "propositions/chains/mistakes/exams/harness-log/viewer/review-queue/graph-data.js OK" + (
        " + dashboard.html" if has_dashboard else " (dashboard.html absent)"
    )
    return {"name": "Knowledge Graph & Hub", "category": "workspace", "status": "ok" if has_dashboard else "warn", "required": True, "detail": detail}


def check_ledger_integrity(kg_dir: Path) -> dict[str, Any]:
    """Corrupted JSONL lines, duplicate proposition ids and dangling chains in learner ledgers."""
    name = "Learner Ledger Integrity"
    try:
        root_str = str(ROOT)
        if root_str not in sys.path:
            sys.path.insert(0, root_str)
        from syntopik.ledger_check import scan_ledgers, summarize
    except Exception as exc:  # pragma: no cover - packaging fallback
        return {"name": name, "category": "workspace", "status": "warn", "required": False, "detail": f"ledger check unavailable: {exc}"}
    rep = scan_ledgers(kg_dir)
    return {
        "name": name,
        "category": "workspace",
        "status": "ok" if rep["ok"] else "warn",
        "required": False,
        "issues": rep["issues"],
        "corruptLines": rep["corruptLines"],
        "duplicateIds": rep["duplicateIds"][:10],
        "danglingChains": rep["danglingChains"],
        "detail": summarize(rep),
    }


def check_rule_budget(ws: Path, *, is_repo: bool = True) -> dict[str, Any]:
    if not is_repo:
        return {
            "name": "Rule Budget (GEMINI.md + AGENTS.md)",
            "category": "workspace",
            "status": "ok",
            "required": False,
            "detail": "external vault / wheel mode (repo rule budget check skipped)",
        }
    rule_files = [ws / "GEMINI.md", ws / "AGENTS.md"]
    missing = [p.name for p in rule_files if not p.exists()]
    if missing:
        return {"name": "Rule Budget (GEMINI.md + AGENTS.md)", "category": "workspace", "status": "fail", "required": True, "detail": f"missing: {', '.join(missing)}"}
    sizes: dict[str, int] = {}
    issues: list[str] = []
    for p in rule_files:
        raw = p.read_bytes()
        sizes[p.name] = len(raw)
        if len(raw) >= 24576:
            issues.append(f"{p.name} >= 24 KB ({len(raw)} B)")
        text = raw.decode("utf-8", errors="replace")
        if re.match(r"^---\s*\r?\n", text):
            issues.append(f"{p.name} has YAML frontmatter")
    total_bytes = sum(sizes.values())
    est_tokens = total_bytes // 4
    if total_bytes > RULE_BUDGET_BYTES:
        issues.append(f"combined {total_bytes} B > {RULE_BUDGET_BYTES} B (~{RULE_BUDGET_BYTES // 4} tok ceiling)")
    if issues:
        return {
            "name": "Rule Budget (GEMINI.md + AGENTS.md)",
            "category": "workspace",
            "status": "fail",
            "required": True,
            "bytes": total_bytes,
            "budgetBytes": RULE_BUDGET_BYTES,
            "detail": "; ".join(issues),
        }
    return {
        "name": "Rule Budget (GEMINI.md + AGENTS.md)",
        "category": "workspace",
        "status": "ok",
        "required": True,
        "bytes": total_bytes,
        "budgetBytes": RULE_BUDGET_BYTES,
        "detail": f"{total_bytes} B (~{est_tokens} tok <= {RULE_BUDGET_BYTES // 4} tok / {RULE_BUDGET_BYTES} B; GEMINI={sizes['GEMINI.md']} B, AGENTS={sizes['AGENTS.md']} B, no frontmatter)",
    }


def check_regression_suite(ws: Path, *, is_repo: bool = True) -> dict[str, Any]:
    if not is_repo:
        return {
            "name": "Regression Suite (test_harness.py)",
            "category": "workspace",
            "status": "ok",
            "required": False,
            "detail": "external vault / wheel mode (repo test suite check skipped)",
        }
    test_file = ws / "tests" / "test_harness.py"
    if not test_file.exists():
        return {"name": "Regression Suite (test_harness.py)", "category": "workspace", "status": "fail", "required": True, "detail": "tests/test_harness.py missing"}
    size = test_file.stat().st_size
    if size == 0:
        return {"name": "Regression Suite (test_harness.py)", "category": "workspace", "status": "fail", "required": True, "detail": "tests/test_harness.py is empty"}
    return {"name": "Regression Suite (test_harness.py)", "category": "workspace", "status": "ok", "required": True, "detail": f"tests/test_harness.py present ({size} B)"}


def run_doctor(workspace: str | Path | None = None, *, core_only: bool = False) -> dict[str, Any]:
    t0 = time.perf_counter()
    pkg_root = ROOT
    if workspace is not None:
        ws = Path(workspace).expanduser().resolve()
    else:
        env_ws = os.environ.get("SYNT_WORKSPACE") or os.environ.get("MLH_WORKSPACE")
        if env_ws:
            ws = Path(env_ws).expanduser().resolve()
        elif _is_repo_checkout(ROOT):
            ws = ROOT
        else:
            try:
                from syntopik.paths import resolve_workspace as _resolve_ws

                ws = _resolve_ws()
            except Exception:
                ws = (HOME / ".syntopik").resolve()

    is_repo = _is_repo_checkout(ws)

    # Auto-bootstrap missing gitignored learner files in the resolved vault (NEVER in site-packages/_bundle!)
    boot_info = ensure_learner_workspace(ws)
    kg_dir = Path(boot_info["kgDir"])

    py_check = {
        "name": "Python Runtime (Core Stdlib)" if core_only else "Python Runtime (.venv)",
        "category": "python",
        "status": "ok" if sys.version_info >= (3, 12) else "warn",
        "required": True,
        "version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "detail": f"Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro} ({sys.executable})",
    }

    if core_only:
        checks: list[dict[str, Any]] = [
            py_check,
            check_binary_tool("node", "Node.js (Hooks & Sync)", required=True),
            check_workspace_scripts(ws, pkg_root),
            check_syntopik_package(ws, pkg_root),
            check_packaging_and_cli(ws, is_repo=is_repo),
            check_plugin_manifests(ws, is_repo=is_repo),
            check_hooks(ws, pkg_root, is_repo=is_repo),
            check_multi_harness_configs(ws),
            check_skills(ws, pkg_root),
            check_knowledge_hub(ws, kg_dir, pkg_root),
            check_ledger_integrity(kg_dir),
            check_reagents_db(ws, pkg_root),
            check_archetypes_db(ws, pkg_root),
            check_rule_budget(ws, is_repo=is_repo),
            check_context_footprint(ws),
            check_regression_suite(ws, is_repo=is_repo),
            check_mcp_studio(ws, pkg_root),
        ]
    else:
        tinytex_dir = find_tinytex_bin()
        tex_dirs = [tinytex_dir] if tinytex_dir else []
        checks = [
            py_check,
            # PDF & OCR
            check_py_pkg("pymupdf", "pymupdf", "PyMuPDF (fitz)"),
            check_py_pkg("pymupdf4llm", "pymupdf4llm", "PyMuPDF4LLM"),
            check_py_pkg("pdfplumber", "pdfplumber", "pdfplumber (Tables)"),
            check_py_pkg("rapidocr-onnxruntime", "rapidocr_onnxruntime", "RapidOCR"),
            # Chemistry
            check_py_pkg("rdkit", "rdkit", "RDKit (Organic 2D/3D)"),
            check_py_pkg("py2opsin", "py2opsin", "py2opsin (Offline IUPAC->SMILES)"),
            check_py_pkg("chempy", "chempy", "ChemPy (Stoichiometry/Reactions)"),
            check_py_pkg("molmass", "molmass", "molmass (Periodic Table & Formula)", note="replaces mendeleev cleanly without Pint conflict"),
            check_py_pkg("quantities", "quantities", "quantities (ChemPy units)"),
            # Maths, Physics & Units
            check_py_pkg("sympy", "sympy", "SymPy"),
            check_py_pkg("scipy", "scipy", "SciPy"),
            check_py_pkg("numpy", "numpy", "NumPy"),
            check_py_pkg("pint", "pint", "Pint (Units/Dimensions)"),
            check_py_pkg("latex2sympy2-extended", "latex2sympy2_extended", "latex2sympy2-extended"),
            check_py_pkg("lark", "lark", "Lark (SymPy LaTeX parser)"),
            check_py_pkg("uncertainties", "uncertainties", "uncertainties (Error Propagation)"),
            check_py_pkg("schemdraw", "schemdraw", "Schemdraw (Circuits)"),
            check_py_pkg("matplotlib", "matplotlib", "Matplotlib"),
            # Video, Narration & SRS
            check_py_pkg("manim", "manim", "Manim"),
            check_py_pkg("edge-tts", "edge_tts", "edge-tts (Neural Narration)"),
            check_py_pkg("srt", "srt", "srt (Captions)"),
            check_py_pkg("fsrs", "fsrs", "FSRS v6"),
            check_py_pkg("genanki", "genanki", "Genanki (Anki .apkg)"),
            # System Binaries
            check_binary_tool("latex", "TinyTeX latex", extra_dirs=tex_dirs, required=True),
            check_binary_tool("dvisvgm", "TinyTeX dvisvgm", extra_dirs=tex_dirs, required=True),
            check_binary_tool("typst", "Typst CLI", required=True),
            check_binary_tool("ffmpeg", "FFmpeg", version_arg="-version", required=True),
            check_binary_tool("ffprobe", "FFprobe", version_arg="-version", required=True),
            check_offline_tts(),
            check_binary_tool("node", "Node.js (Hooks & Sync)", required=True),
            check_binary_tool("java", "Java (py2opsin JVM)", version_arg="-version", required=False, optional_ok_when_missing=True),
            check_binary_tool("tesseract", "Tesseract (OCR fallback)", required=False, optional_ok_when_missing=True),
            # Workspace Integrity, Packaging, Multi-Harness & Plugins
            check_workspace_scripts(ws, pkg_root),
            check_syntopik_package(ws, pkg_root),
            check_packaging_and_cli(ws, is_repo=is_repo),
            check_plugin_manifests(ws, is_repo=is_repo),
            check_hooks(ws, pkg_root, is_repo=is_repo),
            check_multi_harness_configs(ws),
            check_skills(ws, pkg_root),
            check_knowledge_hub(ws, kg_dir, pkg_root),
            check_ledger_integrity(kg_dir),
            check_reagents_db(ws, pkg_root),
            check_archetypes_db(ws, pkg_root),
            check_rule_budget(ws, is_repo=is_repo),
            check_context_footprint(ws),
            check_regression_suite(ws, is_repo=is_repo),
            check_mcp_studio(ws, pkg_root),
            check_google_chemistry_plugin(),
        ]

        # Quick check for mhchem inside TinyTeX if kpsewhich exists
        kpse = find_binary("kpsewhich", extra_dirs=tex_dirs)
        if kpse:
            proc = subprocess.run([kpse, "mhchem.sty"], capture_output=True, text=True, timeout=3, check=False)
            ok_mh = proc.returncode == 0 and bool(proc.stdout.strip())
            checks.append({
                "name": "LaTeX mhchem.sty (Chem MathTex)",
                "category": "binary",
                "status": "ok" if ok_mh else "warn",
                "required": False,
                "detail": proc.stdout.strip() if ok_mh else "mhchem.sty not found via kpsewhich",
            })

    elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 1)
    ok_n = sum(1 for c in checks if c["status"] == "ok")
    warn_n = sum(1 for c in checks if c["status"] == "warn")
    fail_n = sum(1 for c in checks if c["status"] == "fail")
    required_fail_n = sum(1 for c in checks if c["status"] == "fail" and c.get("required", True))

    return {
        "workspace": str(ws),
        "coreOnly": core_only,
        "healthy": required_fail_n == 0,
        "elapsedMs": elapsed_ms,
        "bootstrap": boot_info,
        "summary": {"total": len(checks), "ok": ok_n, "warn": warn_n, "fail": fail_n},
        "checks": checks,
    }


def check_compat_matrix(ws: str | Path | None = None) -> dict[str, Any]:
    root_str = str(ROOT)
    if root_str not in sys.path:
        sys.path.insert(0, root_str)
    from syntopik.agents_compat import get_compat_matrix

    proj = Path(ws).expanduser().resolve() if ws is not None else Path.cwd().resolve()
    return get_compat_matrix(project_dir=proj)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Fast Cross-Platform Health Diagnostic for Syntopik")
    parser.add_argument("--workspace", default=None, help="Optional workspace root directory override")
    parser.add_argument("--core-only", action="store_true", help="Check bare-stdlib core & multi-harness integrity without requiring optional STEM .venv packages")
    parser.add_argument("--compat", action="store_true", help="Print cross-OS 14-coding-agent compatibility matrix")
    parser.add_argument("--fix", action="store_true", help="Non-destructively initialize missing ledgers, repair .claude/skills mirror, and sync graph-data.js before checking")
    parser.add_argument("--json", action="store_true", help="Output machine-readable JSON")
    args = parser.parse_args(argv)

    if args.compat:
        root_str = str(ROOT)
        if root_str not in sys.path:
            sys.path.insert(0, root_str)
        from syntopik.agents_compat import format_compat_table

        matrix = check_compat_matrix(args.workspace)
        if args.json:
            print(json.dumps(matrix, indent=2, ensure_ascii=False))
        else:
            print(format_compat_table(matrix))
        sys.exit(0)

    fix_actions: list[str] = []
    if args.fix:
        target_ws = Path(args.workspace).expanduser().resolve() if args.workspace else (
            ROOT if _is_repo_checkout(ROOT) else (HOME / ".syntopik").resolve()
        )
        boot = ensure_learner_workspace(target_ws, run_sync=True, repair_skills=True)
        try:
            root_str = str(ROOT)
            if root_str not in sys.path:
                sys.path.insert(0, root_str)
            from syntopik.ledger_check import repair_ledgers

            lrep = repair_ledgers(Path(boot.get("kgDir") or (target_ws / "knowledge-graph")))
            if lrep["quarantinedLines"]:
                fix_actions.append(f"quarantined {lrep['quarantinedLines']} corrupted ledger line(s) to knowledge-graph/.quarantine/")
        except Exception as exc:
            fix_actions.append(f"ledger repair skipped: {exc}")
        if boot.get("createdDirs"):
            fix_actions.append(f"created {len(boot['createdDirs'])} dir(s)")
        if boot.get("createdFiles"):
            fix_actions.append(f"created {len(boot['createdFiles'])} file(s)")
        if (target_ws / ".agents" / "skills").is_dir() and (target_ws / ".claude").is_dir():
            rep = repair_claude_skills_link(target_ws, create_if_missing=True)
            fix_actions.append(f".claude/skills: {rep.get('action', 'checked')}")
        try:
            from syntopik.paths import sync_hub

            if sync_hub(workspace=target_ws, quiet=True):
                fix_actions.append("synced knowledge-graph/graph-data.js")
        except Exception:
            pass

    report = run_doctor(workspace=args.workspace, core_only=bool(args.core_only))
    if args.fix:
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

    sys.exit(0 if report["healthy"] else 1)


if __name__ == "__main__":
    main()
