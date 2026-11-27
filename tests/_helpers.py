#!/usr/bin/env python3
"""Shared fixtures for the Syntopik regression suite (imported by tests/test_*.py).

Standard-library `unittest` only. Every test is sandboxed in `tempfile.TemporaryDirectory()`
and NEVER mutates the real `knowledge-graph/`, `chapters/`, `demos/`, `animations/` or `exams/`.

Run the whole suite via:
    python3 -B -m unittest discover -s tests -t tests -v

Tests for optional engines skip (not fail) when their extras are missing; see
`requires()` below. Set SYNTOPIK_TEST_FULL_STACK=1 to also run the full-stack doctor.

Importing this module scrubs host-agent detection variables (CLAUDECODE, ...) so results
do not depend on which agent launched the run.
"""

from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

# Never pollute scripts/ or tests/ with __pycache__ bytecode during test runs.
sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
SCRIPTS_DIR = ROOT / "scripts"
SKILLS_DIR = ROOT / ".agents" / "skills"
HOOKS_JSON = ROOT / ".agents" / "hooks.json"

_MODULE_CACHE: dict[str, Any] = {}

# Hooks and CLIs change their output format when they detect a host agent
# (Claude Code, Codex, a Claude plugin). Running the suite from inside one of
# those agents must not change results, so scrub the detection variables for
# this process and every subprocess it spawns. Tests that need them pass an
# explicit `env=`.
_HOST_AGENT_ENV_VARS = (
    "CLAUDECODE",
    "CLAUDE_PROJECT_DIR",
    "CLAUDE_PLUGIN_ROOT",
    "CODEX_HOME",
)
for _var in _HOST_AGENT_ENV_VARS:
    os.environ.pop(_var, None)

_MISSING_MODULE_RE = re.compile(r"No module named '([A-Za-z0-9_.]+)'")


def _module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def requires(*modules: str, binaries: tuple[str, ...] = ()):
    """Skip a test (instead of failing it) when optional extras are not installed.

    Core tests run on the bare stdlib; STEM/PDF/Anki engines need the optional
    extras from pyproject (`pip install .[all]`) and some need CLI tools such as
    Typst. A missing module discovered at run time (in-process import) is also
    converted to a skip so the stdlib-only suite stays green on a fresh clone.
    """
    import functools

    def deco(fn):
        @functools.wraps(fn)
        def wrapper(self, *args, **kwargs):
            missing = [m for m in modules if not _module_available(m)]
            missing += [b for b in binaries if shutil.which(b) is None]
            if missing:
                self.skipTest(f"optional dependency not installed: {', '.join(missing)}")
            try:
                return fn(self, *args, **kwargs)
            except ModuleNotFoundError as exc:
                if exc.name and not _module_available(exc.name):
                    self.skipTest(f"optional dependency not installed: {exc.name}")
                raise

        return wrapper

    return deco


def load_script_module(script_name: str) -> Any:
    """Import a script from scripts/ cleanly without leaving bytecode behind."""
    if script_name in _MODULE_CACHE:
        return _MODULE_CACHE[script_name]
    path = SCRIPTS_DIR / script_name
    if not path.exists():
        raise FileNotFoundError(f"Expected script not found: {path}")
    mod_name = f"_mlh_test_{path.stem}"
    spec = importlib.util.spec_from_file_location(mod_name, str(path))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not create module spec for {path}")
    mod = importlib.util.module_from_spec(spec)
    # Allow cross-script imports (e.g. exam_runner importing mistake_analytics)
    scripts_str = str(SCRIPTS_DIR)
    if scripts_str not in sys.path:
        sys.path.insert(0, scripts_str)
    spec.loader.exec_module(mod)
    _MODULE_CACHE[script_name] = mod
    return mod


class _EncodedStringIO(io.StringIO):
    """StringIO with a utf-8 .encoding attribute (required by libraries like py2opsin)."""

    @property
    def encoding(self) -> str:
        return "utf-8"


def call_json_cmd(fn, args: argparse.Namespace) -> Any:
    """Invoke a script's cmd_* function in-process and parse its JSON stdout."""
    buf = _EncodedStringIO()
    with contextlib.redirect_stdout(buf):
        fn(args)
    raw = buf.getvalue().strip()
    return json.loads(raw)


def parse_yaml_frontmatter(text: str) -> dict[str, str] | None:
    """Minimal stdlib YAML frontmatter parser supporting single-line and >-/| multiline values."""
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, flags=re.DOTALL)
    if not m:
        return None
    fields: dict[str, str] = {}
    lines = m.group(1).splitlines()
    idx = 0
    while idx < len(lines):
        line = lines[idx]
        km = re.match(r"^([A-Za-z0-9_-]+)\s*:\s*(.*)$", line)
        if not km:
            idx += 1
            continue
        key = km.group(1).strip()
        val = km.group(2).strip()
        if val in (">", ">-", ">+", "|", "|-", "|+"):
            block_lines: list[str] = []
            idx += 1
            while idx < len(lines) and (lines[idx].startswith((" ", "\t")) or not lines[idx].strip()):
                if lines[idx].strip():
                    block_lines.append(lines[idx].strip())
                idx += 1
            fields[key] = " ".join(block_lines).strip()
        else:
            fields[key] = val.strip("\"'")
            idx += 1
    return fields


def run_cli(
    cmd: list[str],
    *,
    stdin_text: str | None = None,
    timeout: float = 15.0,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=str(ROOT),
        input=stdin_text,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
        check=False,
    )


__all__ = [
    "HOOKS_JSON",
    "ROOT",
    "SCRIPTS_DIR",
    "SKILLS_DIR",
    "_EncodedStringIO",
    "_HOST_AGENT_ENV_VARS",
    "_MISSING_MODULE_RE",
    "_MODULE_CACHE",
    "_module_available",
    "call_json_cmd",
    "load_script_module",
    "parse_yaml_frontmatter",
    "requires",
    "run_cli",
]
