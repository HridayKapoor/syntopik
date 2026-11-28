#!/usr/bin/env python3
"""Syntopik regression suite: CLI contracts, hooks, context budget, repo hygiene and portability.

Shared fixtures live in tests/_helpers.py. Run via:
    python3 -B -m unittest discover -s tests -t tests -v
"""

from __future__ import annotations

import concurrent.futures
import http.client
import json
import os
import py_compile
import re
import subprocess
import sys
import tempfile
import threading
import tomllib
import unittest
import warnings
from pathlib import Path
from typing import Any

from _helpers import (
    HOOKS_JSON,
    ROOT,
    SCRIPTS_DIR,
    SKILLS_DIR,
    _MISSING_MODULE_RE,
    _module_available,
    load_script_module,
    parse_yaml_frontmatter,
    run_cli,
)


class TestSyntaxAndCLI(unittest.TestCase):
    """1. Syntax compilation (zero SyntaxWarning) & CLI --help contracts."""

    def test_all_python_scripts_compile_without_syntax_warnings(self) -> None:
        py_files = (
            sorted((ROOT / "syntopik").glob("*.py"))
            + sorted(SCRIPTS_DIR.glob("*.py"))
            + sorted((ROOT / "benchmarks").glob("*.py"))
            + sorted((ROOT / "tests").glob("*.py"))
        )
        self.assertGreaterEqual(len(py_files), 50, "Expected at least 50 Python files across syntopik/, scripts/, benchmarks/, tests/")
        with tempfile.TemporaryDirectory(prefix="mlh-pycompile-") as tmp:
            tmp_dir = Path(tmp)
            for idx, py_path in enumerate(py_files):
                with self.subTest(script=py_path.name):
                    with warnings.catch_warnings():
                        warnings.simplefilter("error", SyntaxWarning)
                        py_compile.compile(
                            str(py_path),
                            cfile=str(tmp_dir / f"{idx}_{py_path.stem}.pyc"),
                            doraise=True,
                        )

    def test_all_node_scripts_syntax_check(self) -> None:
        node_files = [
            ROOT / "bin" / "syntopik.mjs",
            SCRIPTS_DIR / "sync-graph.mjs",
            ROOT / ".agents" / "hooks" / "session-start.mjs",
            ROOT / ".agents" / "hooks" / "post-graph-sync.mjs",
            ROOT / "mcp-app" / "mlh-studio" / "dist" / "server.mjs",
        ]
        for nf in node_files:
            with self.subTest(script=nf.name):
                self.assertTrue(nf.exists(), f"Missing Node script: {nf}")
                proc = run_cli(["node", "--check", str(nf)])
                self.assertEqual(proc.returncode, 0, f"node --check failed for {nf}: {proc.stderr}")

    def test_bare_cli_prints_quickstart_and_typos_get_suggestions(self) -> None:
        from syntopik import cli as synt_cli

        def run(*args: str) -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                [sys.executable, "-B", "-m", "syntopik.cli", *args],
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )

        bare = run()
        self.assertEqual(bare.returncode, 0, bare.stderr)
        self.assertIn("Start here", bare.stdout)
        # Every command the guide mentions must really exist.
        names = set(synt_cli._command_names(synt_cli.build_parser()))
        for line in bare.stdout.splitlines():
            m = re.match(r"\s*syntopik ([a-z-]+)", line)
            if m:
                self.assertIn(m.group(1), names, line)
        typo = run("reveiw")
        self.assertEqual(typo.returncode, 2)
        self.assertIn("Did you mean: review", typo.stderr)
        self.assertEqual(run("--version").returncode, 0)

    def test_python_dash_m_and_empty_vault_hint(self) -> None:
        import tempfile

        from syntopik import cli as synt_cli

        with tempfile.TemporaryDirectory() as tmp:
            env = {**os.environ, "SYNT_WORKSPACE": tmp, "PYTHONPATH": str(ROOT)}

            def run(*args: str) -> subprocess.CompletedProcess[str]:
                return subprocess.run(
                    [sys.executable, "-B", "-m", "syntopik", *args],
                    cwd=tmp,
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=60,
                    check=False,
                )

            self.assertEqual(run("init").returncode, 0)
            for cmd in (["review"], ["readiness"], ["interleave"], ["report"]):
                proc = run(*cmd)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertIn("syntopik drop", proc.stdout, cmd)
                self.assertIn("syntopik demo", proc.stdout, cmd)
            # The hint names commands that really exist.
            names = set(synt_cli._command_names(synt_cli.build_parser()))
            self.assertTrue({"drop", "demo"} <= names)
            # JSON output stays machine-clean.
            proc = run("review", "--json")
            self.assertNotIn("syntopik drop", proc.stdout)

    def test_bare_mode_reports_and_debrief_outside_git_is_friendly(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            env = {**os.environ, "SYNT_WORKSPACE": tmp, "PYTHONPATH": str(ROOT),
                   "GIT_CEILING_DIRECTORIES": str(Path(tmp).resolve().parent)}

            def run(*args: str) -> subprocess.CompletedProcess[str]:
                return subprocess.run(
                    [sys.executable, "-B", "-m", "syntopik", *args],
                    cwd=tmp, env=env, capture_output=True, text=True, timeout=60, check=False,
                )

            self.assertEqual(run("init").returncode, 0)
            cfg = Path(tmp) / "knowledge-graph" / "mentor-config.json"
            bare = run("mode")
            self.assertEqual(bare.returncode, 0, bare.stderr)
            self.assertIn("study (mentor off)", bare.stdout)
            self.assertFalse(cfg.exists(), "bare `mode` must not change the setting")
            self.assertEqual(run("mode", "mentor").returncode, 0)
            self.assertIn("mentor (active)", run("mode").stdout)
            deb = run("debrief")
            self.assertEqual(deb.returncode, 1)
            self.assertIn("not inside a git repository", deb.stdout)
            self.assertNotIn("usage: git diff", deb.stdout + deb.stderr)

    def test_hint_after_drop_and_sunset_unknown_chapter(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            env = {**os.environ, "SYNT_WORKSPACE": tmp, "PYTHONPATH": str(ROOT)}

            def run(*args: str) -> subprocess.CompletedProcess[str]:
                return subprocess.run(
                    [sys.executable, "-B", "-m", "syntopik", *args],
                    cwd=tmp, env=env, capture_output=True, text=True, timeout=60, check=False,
                )

            self.assertEqual(run("init").returncode, 0)
            chapters = Path(tmp) / "chapters"
            chapters.mkdir(exist_ok=True)
            (chapters / "optics-tracker.json").write_text('{"slug": "optics"}', encoding="utf-8")
            rev = run("review")
            self.assertIn("not studied any of it yet", rev.stdout)
            self.assertNotIn("syntopik drop", rev.stdout)
            bad = run("sunset", "optcs")
            self.assertEqual(bad.returncode, 1)
            self.assertIn("Known chapters: optics", bad.stdout)
            self.assertEqual(run("sunset", "optics").returncode, 0)

    def test_run_lists_engines_and_suggests_typos(self) -> None:
        def run(*args: str) -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                [sys.executable, "-B", "-m", "syntopik", *args],
                cwd=str(ROOT), capture_output=True, text=True, timeout=60, check=False,
            )

        bare = run("run")
        self.assertEqual(bare.returncode, 0, bare.stderr)
        self.assertIn("  math_verify", bare.stdout)
        typo = run("run", "math_verfy")
        self.assertEqual(typo.returncode, 1)
        self.assertIn("Did you mean: math_verify", typo.stderr)

    def test_drop_and_import_reject_missing_sources_cleanly(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            env = {**os.environ, "SYNT_WORKSPACE": tmp, "PYTHONPATH": str(ROOT)}

            def run(*args: str) -> subprocess.CompletedProcess[str]:
                return subprocess.run(
                    [sys.executable, "-B", "-m", "syntopik", *args],
                    cwd=tmp, env=env, capture_output=True, text=True, timeout=60, check=False,
                )

            self.assertEqual(run("init").returncode, 0)
            chapters = Path(tmp) / "chapters"
            bad = run("drop", "missing-file.pdf")
            self.assertEqual(bad.returncode, 1)
            self.assertIn("Nothing was added", bad.stdout)
            self.assertEqual(list(chapters.glob("*-tracker.json")), [])
            good = Path(tmp) / "optics.md"
            good.write_text("# Optics\n\nLight bends at an interface (Snell's law).\n", encoding="utf-8")
            mixed = run("drop", str(good), "missing-file.pdf")
            self.assertEqual(mixed.returncode, 0, mixed.stderr)
            self.assertIn("Skipped missing-file.pdf", mixed.stdout)
            self.assertEqual([p.name for p in chapters.glob("*-tracker.json")], ["optics-tracker.json"])
            imp = run("import", "missing-deck.apkg")
            self.assertEqual(imp.returncode, 1)
            self.assertIn("Import failed", imp.stdout)
            self.assertNotIn("Traceback", imp.stderr)

    def test_review_tui_reprompts_typos_and_summarises(self) -> None:
        import json as _json
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            env = {**os.environ, "SYNT_WORKSPACE": tmp, "PYTHONPATH": str(ROOT)}
            kg = Path(tmp) / "knowledge-graph"
            kg.mkdir()
            (Path(tmp) / "chapters").mkdir()
            with (kg / "propositions.jsonl").open("w", encoding="utf-8") as fh:
                for i in range(3):
                    fh.write(_json.dumps({
                        "id": f"t{i}", "title": f"Idea {i}", "statement": f"Answer {i}",
                        "chapter": "demo", "status": "shaky", "nextReview": "2020-01-01",
                    }) + "\n")
            proc = subprocess.run(
                [sys.executable, "-B", "-m", "syntopik", "review", "--tui", "--limit", "3"],
                cwd=tmp, env={**env, "PYTHONIOENCODING": "utf-8"}, input="\nzz\n3\n\n1\n\nq\n",
                capture_output=True, text=True, encoding="utf-8", timeout=60, check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            out = proc.stdout
            self.assertIn("'zz' is not a rating", out)
            self.assertIn("Recorded 'good'", out)
            self.assertIn("Next review", out)
            self.assertIn("2/3 card(s) recorded", out)
            self.assertIn("1 again · 1 good", out)
            self.assertIn("1 card(s) still due", out)
            listing = subprocess.run(
                [sys.executable, "-B", "-m", "syntopik", "review"],
                cwd=tmp, env={**env, "PYTHONIOENCODING": "utf-8"},
                capture_output=True, text=True, encoding="utf-8", timeout=60, check=False,
            )
            self.assertIn("syntopik review --tui", listing.stdout)
            nxt = subprocess.run(
                [sys.executable, "-B", "-m", "syntopik", "next"],
                cwd=tmp, env={**env, "PYTHONIOENCODING": "utf-8"},
                capture_output=True, text=True, encoding="utf-8", timeout=60, check=False,
            )
            self.assertEqual(nxt.returncode, 0, nxt.stderr)
            self.assertNotIn("--json", nxt.stdout)

    def test_all_cli_scripts_help_exit_zero(self) -> None:
        cli_scripts = [
            "pdf_studio.py",
            "math_verify.py",
            "chem_3d.py",
            "chem_physical.py",
            "circuit_draw.py",
            "srs_engine.py",
            "cheatsheet_pdf.py",
            "narrate_video.py",
            "study_doctor.py",
            "mistake_analytics.py",
            "exam_runner.py",
            "physics_draw.py",
            "organic_reagents.py",
            "math_plot.py",
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
            "learner_profile.py",
            "derivation_forge.py",
            "viva_chamber.py",
            "build_starter_packs.py",
        ]

        def _check_help(name: str) -> tuple[str, int, str, str]:
            path = SCRIPTS_DIR / name
            if not path.exists():
                return (name, -1, "", f"Missing script: {path}")
            proc = run_cli([sys.executable, "-B", str(path), "--help"])
            return (name, proc.returncode, proc.stdout, proc.stderr)

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
            results = list(pool.map(_check_help, cli_scripts))

        for name, rc, out, err in results:
            with self.subTest(script=name):
                if rc != 0:
                    m = _MISSING_MODULE_RE.search(err or "")
                    if m and not _module_available(m.group(1).split(".")[0]):
                        self.skipTest(f"{name}: optional dependency not installed: {m.group(1)}")
                self.assertEqual(rc, 0, f"{name} --help failed (rc={rc}): {err}")
                self.assertTrue(out.strip(), f"{name} --help produced empty stdout")
                self.assertNotIn("Traceback", err)
                if name == "narrate_video.py":
                    # Guard against unescaped '%' argparse formatting regression
                    self.assertIn("+5%", out)


class TestHooksSkillsBudget(unittest.TestCase):
    """3. Hooks JSON/stdout contracts, Skill YAML frontmatter, & Token-Budget hygiene."""

    def test_hooks_json_valid_and_scripts_exist(self) -> None:
        self.assertTrue(HOOKS_JSON.exists(), "Missing .agents/hooks.json")
        data = json.loads(HOOKS_JSON.read_text(encoding="utf-8"))
        self.assertIsInstance(data, dict)

        commands: list[str] = []

        def _collect(obj: Any) -> None:
            if isinstance(obj, dict):
                if isinstance(obj.get("command"), str):
                    commands.append(obj["command"])
                for v in obj.values():
                    _collect(v)
            elif isinstance(obj, list):
                for item in obj:
                    _collect(item)

        _collect(data)
        self.assertGreaterEqual(len(commands), 2, "Expected at least SessionStart and PostToolUse commands")
        for cmd in commands:
            parts = cmd.strip().split()
            self.assertGreaterEqual(len(parts), 2, f"Malformed hook command: {cmd}")
            script_rel = parts[1]
            resolved = (ROOT / ".agents" / script_rel).resolve()
            self.assertTrue(resolved.exists(), f"Hook script referenced in hooks.json does not exist: {script_rel}")

    def test_session_start_and_post_graph_sync_contracts(self) -> None:
        ss = run_cli(["node", str(ROOT / ".agents" / "hooks" / "session-start.mjs")], stdin_text="{}")
        self.assertEqual(ss.returncode, 0, f"session-start.mjs failed: {ss.stderr}")
        ss_json = json.loads(ss.stdout)
        msg = ss_json["injectSteps"][0]["systemMessage"]["systemMessage"]
        self.assertIsInstance(msg, str)
        self.assertIn("# Syntopik — Live Study Snapshot", msg)
        # Guard against exam-cbt-template.html leaking into Interactive demos count
        self.assertNotIn("exam-cbt-template.html", msg)

        ps = run_cli(["node", str(ROOT / ".agents" / "hooks" / "post-graph-sync.mjs")], stdin_text="{}")
        self.assertEqual(ps.returncode, 0, f"post-graph-sync.mjs failed: {ps.stderr}")
        self.assertEqual(ps.stdout.strip(), "{}")

    def test_all_skills_valid_frontmatter(self) -> None:
        skill_files = sorted(SKILLS_DIR.glob("*/SKILL.md"))
        self.assertEqual(len(skill_files), 17, f"Expected exact 17 skills, found {len(skill_files)}")
        for sk in skill_files:
            with self.subTest(skill=sk.parent.name):
                text = sk.read_text(encoding="utf-8")
                fm = parse_yaml_frontmatter(text)
                self.assertIsNotNone(fm, f"{sk} is missing YAML frontmatter")
                assert fm is not None
                name_val = fm.get("name", "")
                desc_val = fm.get("description", "")
                self.assertEqual(
                    name_val,
                    sk.parent.name,
                    f"{sk} frontmatter name '{name_val}' != folder '{sk.parent.name}'",
                )
                self.assertGreaterEqual(len(desc_val), 15, f"{sk} description too short: {desc_val!r}")
                self.assertNotIn("<", desc_val, f"{sk} YAML description contains '<'")
                self.assertNotIn(">", desc_val, f"{sk} YAML description contains '>'")

    def test_always_on_rules_token_budget_and_no_frontmatter(self) -> None:
        gemini = ROOT / "GEMINI.md"
        agents = ROOT / "AGENTS.md"
        total_bytes = 0
        for rule_file in (gemini, agents):
            with self.subTest(file=rule_file.name):
                self.assertTrue(rule_file.exists(), f"Missing {rule_file.name}")
                raw = rule_file.read_bytes()
                self.assertLess(len(raw), 24576, f"{rule_file.name} exceeds 24 KB limit ({len(raw)} B)")
                text = raw.decode("utf-8")
                self.assertFalse(
                    text.lstrip().startswith("---"),
                    f"{rule_file.name} must NOT contain YAML frontmatter",
                )
                total_bytes += len(raw)
        self.assertLessEqual(
            total_bytes,
            9500,
            f"Combined GEMINI.md + AGENTS.md ({total_bytes} B) exceeds v3.0 9,500 B unified ceiling",
        )


class TestTriProtocolHooksAndMcp(unittest.TestCase):
    """21. Tri-protocol hooks (AGY/Claude/Codex), multi-harness configs & Terminal non-UI MCP safeguard."""

    def test_tri_protocol_hooks_configs_and_terminal_non_ui_mcp_safeguard(self) -> None:
        # 1. Validate all multi-harness configuration files
        claude_settings = json.loads((ROOT / ".claude" / "settings.json").read_text(encoding="utf-8"))
        self.assertIn("SessionStart", claude_settings["hooks"])
        self.assertIn("PostToolUse", claude_settings["hooks"])
        self.assertIn("session-start.mjs", json.dumps(claude_settings["hooks"]["SessionStart"]))
        self.assertIn("post-graph-sync.mjs", json.dumps(claude_settings["hooks"]["PostToolUse"]))

        mcp_json = json.loads((ROOT / ".mcp.json").read_text(encoding="utf-8"))
        srv_cfg = mcp_json["mcpServers"]["syntopik-studio"]
        self.assertEqual(srv_cfg["command"], "node")
        self.assertTrue(any("dist/server.mjs" in a for a in srv_cfg["args"]))

        codex_toml = tomllib.loads((ROOT / ".codex" / "config.toml").read_text(encoding="utf-8"))
        self.assertGreaterEqual(codex_toml.get("project_doc_max_bytes", 0), 32768)
        self.assertTrue(codex_toml.get("features", {}).get("hooks"))
        self.assertEqual(codex_toml["mcp_servers"]["syntopik-studio"]["command"], "node")

        codex_hooks = json.loads((ROOT / ".codex" / "hooks.json").read_text(encoding="utf-8"))
        self.assertIn("SessionStart", codex_hooks["hooks"])
        self.assertIn("PostToolUse", codex_hooks["hooks"])
        self.assertIn("apply_patch", json.dumps(codex_hooks["hooks"]["PostToolUse"]))

        with tempfile.TemporaryDirectory(prefix="synt-tri-hooks-") as tmp:
            ws = Path(tmp) / "ws"
            kg = ws / "knowledge-graph"
            ch = ws / "chapters"
            kg.mkdir(parents=True)
            ch.mkdir(parents=True)
            (kg / "propositions.jsonl").write_text(
                json.dumps({"id": "cs-raft-1", "subject": "CS", "chapter": "Raft", "statement": "At most one leader per term.", "status": "solid"}) + "\n",
                encoding="utf-8",
            )
            (kg / "chains.jsonl").write_text("", encoding="utf-8")
            (kg / "mistakes.jsonl").write_text("", encoding="utf-8")

            base_env = {
                k: v
                for k, v in os.environ.items()
                if k not in ("CLAUDECODE", "CLAUDE_PROJECT_DIR", "CODEX_HOME", "MLH_WORKSPACE", "MLH_KG_DIR", "MLH_CHAPTERS_DIR")
            }
            base_env["SYNT_WORKSPACE"] = str(ws)
            base_env["SYNT_KG_DIR"] = str(kg)
            base_env["SYNT_CHAPTERS_DIR"] = str(ch)

            ss_script = str(ROOT / ".agents" / "hooks" / "session-start.mjs")
            ps_script = str(ROOT / ".agents" / "hooks" / "post-graph-sync.mjs")

            # 2a. session-start.mjs in Antigravity mode -> injectSteps
            ag = run_cli(["node", ss_script], stdin_text="{}", env=base_env)
            self.assertEqual(ag.returncode, 0, ag.stderr)
            ag_out = json.loads(ag.stdout)
            self.assertIn("injectSteps", ag_out)
            self.assertIn("# Syntopik — Live Study Snapshot", ag_out["injectSteps"][0]["systemMessage"]["systemMessage"])

            # 2b. session-start.mjs in Claude Code mode -> hookSpecificOutput.additionalContext
            cc_env = dict(base_env, CLAUDECODE="1")
            cc = run_cli(
                ["node", ss_script],
                stdin_text=json.dumps({"hook_event_name": "SessionStart", "session_id": "cc-test"}),
                env=cc_env,
            )
            self.assertEqual(cc.returncode, 0, cc.stderr)
            cc_out = json.loads(cc.stdout)
            self.assertNotIn("injectSteps", cc_out)
            self.assertEqual(cc_out["hookSpecificOutput"]["hookEventName"], "SessionStart")
            self.assertIn("# Syntopik — Live Study Snapshot", cc_out["hookSpecificOutput"]["additionalContext"])

            # 2c. session-start.mjs in Codex mode -> hookSpecificOutput.additionalContext
            cx_env = dict(base_env, CODEX_HOME=str(Path(tmp) / "codex-home"))
            cx = run_cli(
                ["node", ss_script],
                stdin_text=json.dumps({"hook_event_name": "SessionStart"}),
                env=cx_env,
            )
            self.assertEqual(cx.returncode, 0, cx.stderr)
            cx_out = json.loads(cx.stdout)
            self.assertIn("# Syntopik — Live Study Snapshot", cx_out["hookSpecificOutput"]["additionalContext"])

            # 3. post-graph-sync.mjs with Claude `tool_input.file_path` AND Codex `apply_patch` (strictly in /tmp ws)
            gdata = kg / "graph-data.js"
            if gdata.exists():
                gdata.unlink()
            ps_cc = run_cli(
                ["node", ps_script],
                stdin_text=json.dumps({
                    "tool_name": "Write",
                    "tool_input": {"file_path": str(kg / "propositions.jsonl")},
                }),
                env=base_env,
            )
            self.assertEqual(ps_cc.returncode, 0, ps_cc.stderr)
            self.assertEqual(ps_cc.stdout.strip(), "{}")
            self.assertTrue(gdata.is_file(), "post-graph-sync.mjs did not sync graph-data.js on Claude tool_input.file_path")
            self.assertIn("cs-raft-1", gdata.read_text(encoding="utf-8"))

            gdata.unlink()
            ps_cx = run_cli(
                ["node", ps_script],
                stdin_text=json.dumps({
                    "tool_name": "apply_patch",
                    "tool_input": {"patch": "*** Begin Patch\n*** Update File: chapters/raft-tracker.json\n*** End Patch\n"},
                }),
                env=base_env,
            )
            self.assertEqual(ps_cx.returncode, 0, ps_cx.stderr)
            self.assertEqual(ps_cx.stdout.strip(), "{}")
            self.assertTrue(gdata.is_file(), "post-graph-sync.mjs did not sync graph-data.js on Codex apply_patch")

            # 4. MCP Studio Server: BOTH synt_* and mlh_* tools + Terminal non-UI anti-leak safeguard
            server_mjs = ROOT / "mcp-app" / "mlh-studio" / "dist" / "server.mjs"
            data_dir = Path(tmp) / "mcp-data"
            secret_expl = "SECRET_QUIZ_EXPLANATION_NEVER_LEAK_7731"
            secret_sol = "SECRET_PRACTICE_SOLUTION_NEVER_LEAK_9914"
            reqs = [
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2024-11-05",
                        # Deliberately NO extensions["io.modelcontextprotocol/ui"] -> Terminal non-UI client!
                        "capabilities": {},
                        "clientInfo": {"name": "terminal-cli-harness", "version": "1.0"},
                    },
                },
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {
                        "name": "synt_quiz_card",
                        "arguments": {
                            "role": "probe",
                            "question": "What is the worst-case time complexity of binary search?",
                            "options": [
                                {"id": "a", "label": "O(log n)"},
                                {"id": "b", "label": "O(n)"},
                                {"id": "c", "label": "O(1)"},
                            ],
                            "explanation": secret_expl,
                            "correctAnswer": "a",
                        },
                    },
                },
                {
                    "jsonrpc": "2.0",
                    "id": 4,
                    "method": "tools/call",
                    "params": {
                        "name": "synt_practice_card",
                        "arguments": {
                            "action": "pose",
                            "problemId": "cs-dijkstra-1",
                            "statement": "Construct a 3-vertex directed graph where Dijkstra fails due to a negative edge.",
                            "mode": "recall",
                            "modelSolution": secret_sol,
                        },
                    },
                },
                {
                    "jsonrpc": "2.0",
                    "id": 5,
                    "method": "tools/call",
                    "params": {
                        "name": "synt_practice_card",
                        "arguments": {
                            "action": "conclude",
                            "problemId": "cs-dijkstra-1",
                            "outcome": "solved",
                            "verdict": "Exact counterexample verified.",
                            "rung": None,
                        },
                    },
                },
            ]
            proc = run_cli(
                ["node", str(server_mjs), f"--workspace={ws}", f"--data-dir={data_dir}"],
                stdin_text="\n".join(json.dumps(r) for r in reqs) + "\n",
                timeout=10.0,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            by_id = {
                int(m["id"]): m
                for m in (json.loads(ln) for ln in proc.stdout.splitlines() if ln.strip())
                if "id" in m
            }

            tool_names = {tl["name"] for tl in by_id[2]["result"]["tools"]}
            for required_name in (
                "synt_quiz_card", "synt_practice_card", "synt_lesson_studio",
                "mlh_quiz_card", "mlh_practice_card", "mlh_lesson_studio",
            ):
                self.assertIn(required_name, tool_names)

            # Terminal non-UI quiz response: bare question + options + "I don't know", ZERO answer/explanation leak!
            q_res = by_id[3]["result"]
            self.assertFalse(q_res.get("isError", False))
            self.assertTrue(q_res["structuredContent"].get("terminalFallback"))
            q_text = q_res["content"][0]["text"]
            self.assertIn("binary search", q_text)
            self.assertIn("O(log n)", q_text)
            self.assertIn("I don't know", q_text)
            q_dump = json.dumps(q_res)
            self.assertNotIn(secret_expl, q_dump, "Quiz explanation leaked to terminal client!")
            self.assertNotIn('"correctAnswer"', q_dump, "Quiz correctAnswer field leaked to terminal client!")

            # Terminal non-UI practice pose: statement present, modelSolution strictly hidden server-side!
            p_pose = by_id[4]["result"]
            self.assertFalse(p_pose.get("isError", False))
            self.assertTrue(p_pose["structuredContent"].get("terminalFallback"))
            self.assertTrue(p_pose["structuredContent"].get("hasHiddenSolution"))
            self.assertIn("3-vertex directed graph", p_pose["content"][0]["text"])
            self.assertNotIn(secret_sol, json.dumps(p_pose), "Practice modelSolution leaked during terminal pose!")

            # Practice conclude reveals committed modelSolution verbatim
            p_conc = by_id[5]["result"]
            self.assertFalse(p_conc.get("isError", False))
            self.assertEqual(p_conc["structuredContent"]["modelSolution"], secret_sol)
            self.assertIn(secret_sol, p_conc["content"][0]["text"])


class TestGitHubHygieneAndPortability(unittest.TestCase):
    """22. Public-repo hygiene, zero machine/user leaks, rule budgets, & cross-harness portability."""

    def test_zero_personal_path_leaks_and_github_release_hygiene(self) -> None:
        # Build forbidden search patterns generically so zero real usernames appear in repo tests.
        personal_home_re = re.compile(
            r"(?:/Users/[A-Za-z0-9._-]+(?:/|[\"'\s]|$)|/home/[A-Za-z0-9._-]+(?:/|[\"'\s]|$)|C:\\Users\\[A-Za-z0-9._-]+\\)"
        )
        hardcoded_brew_node = "/opt/homebrew/" + "bin/node"

        ls_tracked = run_cli(["git", "ls-files"])
        self.assertEqual(ls_tracked.returncode, 0, ls_tracked.stderr)
        ls_untracked = run_cli(["git", "ls-files", "--others", "--exclude-standard"])
        self.assertEqual(ls_untracked.returncode, 0, ls_untracked.stderr)

        candidates = sorted(
            set(ls_tracked.stdout.splitlines() + ls_untracked.stdout.splitlines())
        )
        self.assertGreater(len(candidates), 40)

        personal_leaks: list[str] = []
        config_node_leaks: list[str] = []
        config_suffixes = {".json", ".toml", ".md", ".mjs", ".js", ".ts", ".html"}

        for rel in candidates:
            if not rel or rel.startswith((".venv/", ".git/", "node_modules/", "mcp-app/mlh-studio/node_modules/")):
                continue
            p = ROOT / rel
            if not p.is_file() or p.is_symlink():
                continue
            try:
                raw = p.read_bytes()
            except OSError:
                continue
            if b"\x00" in raw[:4096]:
                continue
            text = raw.decode("utf-8", errors="ignore")
            if personal_home_re.search(text):
                personal_leaks.append(rel)
            if p.suffix.lower() in config_suffixes and hardcoded_brew_node in text:
                config_node_leaks.append(rel)

        self.assertEqual(personal_leaks, [], f"Personal path leak found in tracked/non-ignored files: {personal_leaks}")
        self.assertEqual(config_node_leaks, [], f"Hardcoded Homebrew node path found in config/doc/JS files: {config_node_leaks}")

        # Always-on rule budget: GEMINI.md + AGENTS.md <= 9,500 B (v3.0 unified ceiling)
        agents_bytes = (ROOT / "AGENTS.md").read_bytes()
        gemini_bytes = (ROOT / "GEMINI.md").read_bytes()
        combined_rules = len(agents_bytes) + len(gemini_bytes)
        self.assertLessEqual(
            combined_rules,
            9500,
            f"Combined GEMINI.md + AGENTS.md ({combined_rules} B) exceeds v3.0 9,500 B unified ceiling",
        )

        # CLAUDE.md starts with @AGENTS.md and has no YAML frontmatter
        claude_md = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
        self.assertTrue(claude_md.startswith("@AGENTS.md"), "CLAUDE.md must begin with '@AGENTS.md'")
        self.assertFalse(claude_md.lstrip().startswith("---"))

        # Exact 17/17 valid SKILL.md frontmatters with 0 '<' or '>' in YAML description
        skill_files = sorted(SKILLS_DIR.glob("*/SKILL.md"))
        self.assertEqual(len(skill_files), 17, f"Expected 17 skills, found {len(skill_files)}")
        for sk in skill_files:
            fm = parse_yaml_frontmatter(sk.read_text(encoding="utf-8"))
            self.assertIsNotNone(fm, f"{sk} missing frontmatter")
            assert fm is not None
            self.assertEqual(fm.get("name"), sk.parent.name)
            desc = fm.get("description", "")
            self.assertGreaterEqual(len(desc), 15)
            self.assertNotIn("<", desc, f"{sk} description has '<'")
            self.assertNotIn(">", desc, f"{sk} description has '>'")

        # .claude/skills resolves to .agents/skills and exposes all 17 skills
        claude_skills = ROOT / ".claude" / "skills"
        self.assertTrue(claude_skills.exists() and claude_skills.is_dir(), ".claude/skills must resolve to a directory")
        self.assertEqual(claude_skills.resolve(), SKILLS_DIR.resolve())
        self.assertEqual(len(list(claude_skills.glob("*/SKILL.md"))), 17)

        # Open-source & onboarding top-level docs exist and are non-empty
        for doc_name in ("LICENSE", "BOOTSTRAP.md", "CONTRIBUTING.md", "README.md"):
            doc_path = ROOT / doc_name
            self.assertTrue(doc_path.is_file(), f"Missing top-level doc: {doc_name}")
            self.assertGreater(doc_path.stat().st_size, 200, f"Top-level doc unexpectedly small: {doc_name}")


class TestV2ContextDietAndHygiene(unittest.TestCase):
    """28. Syntopik v2.0 Context-Budget Diet, 17-Skill YAML Budget, Zero Leaks/Pycache & Live SHM Integrity."""

    def test_v2_context_diet_skills_hygiene_no_pycache_and_shm_intact(self) -> None:
        # 1. Combined AGENTS.md + GEMINI.md <= 9,500 B (v3.0 unified ceiling)
        agents_raw = (ROOT / "AGENTS.md").read_bytes()
        gemini_raw = (ROOT / "GEMINI.md").read_bytes()
        combined = len(agents_raw) + len(gemini_raw)
        self.assertLessEqual(combined, 9500, f"AGENTS.md + GEMINI.md ({combined} B) exceeds 9,500 B v3.0 unified ceiling")
        self.assertFalse(agents_raw.decode("utf-8").lstrip().startswith("---"))
        self.assertFalse(gemini_raw.decode("utf-8").lstrip().startswith("---"))

        # 2. Exact 17/17 valid skills with total YAML description bytes < 3,600 B
        skill_files = sorted(SKILLS_DIR.glob("*/SKILL.md"))
        self.assertEqual(len(skill_files), 17)
        total_desc_bytes = 0
        for sk in skill_files:
            fm = parse_yaml_frontmatter(sk.read_text(encoding="utf-8"))
            self.assertIsNotNone(fm, f"{sk} missing YAML frontmatter")
            assert fm is not None
            self.assertEqual(fm.get("name"), sk.parent.name)
            desc = fm.get("description", "")
            self.assertGreaterEqual(len(desc), 15)
            self.assertNotIn("<", desc)
            self.assertNotIn(">", desc)
            total_desc_bytes += len(desc.encode("utf-8"))
        self.assertLess(total_desc_bytes, 3600, f"17-skill YAML descriptions ({total_desc_bytes} B) >= 3,600 B ceiling")

        # 3. Zero /Users/ (or personal home) leaks across tracked + non-ignored repo files
        personal_re = re.compile(
            r"(?:/Users/[A-Za-z0-9._-]+(?:/|[\"'\s]|$)|/home/[A-Za-z0-9._-]+(?:/|[\"'\s]|$)|C:\\Users\\[A-Za-z0-9._-]+\\)"
        )
        tracked = run_cli(["git", "ls-files"]).stdout.splitlines()
        untracked = run_cli(["git", "ls-files", "--others", "--exclude-standard"]).stdout.splitlines()
        leaks: list[str] = []
        for rel in sorted(set(tracked + untracked)):
            if not rel or rel.startswith((".venv/", ".git/", "node_modules/", "mcp-app/mlh-studio/node_modules/")):
                continue
            p = ROOT / rel
            if not p.is_file() or p.is_symlink():
                continue
            raw = p.read_bytes()
            if b"\x00" in raw[:4096]:
                continue
            if personal_re.search(raw.decode("utf-8", errors="ignore")):
                leaks.append(rel)
        self.assertEqual(leaks, [], f"Personal path leaks detected: {leaks}")

        # 4. Zero __pycache__ directories anywhere outside .venv / node_modules
        stray_pycache: list[str] = []
        for dirpath, dirnames, _ in os.walk(ROOT):
            rel_dir = Path(dirpath).relative_to(ROOT)
            parts = rel_dir.parts
            if parts and parts[0] in (".venv", ".git", "node_modules"):
                dirnames[:] = []
                continue
            if "node_modules" in parts:
                dirnames[:] = []
                continue
            if "__pycache__" in dirnames:
                stray_pycache.append(str(rel_dir / "__pycache__"))
        self.assertEqual(stray_pycache, [], f"Stray __pycache__ directories found outside .venv: {stray_pycache}")

        # 5. Live SHM files never tracked in git; when present locally, verify 4/4 live SHM files + 4/4 extracted dirs intact
        live_shm_files = [
            ROOT / "chapters" / "shm-tracker.json",
            ROOT / "knowledge-graph" / "propositions.jsonl",
            ROOT / "knowledge-graph" / "mistakes.jsonl",
            ROOT / "knowledge-graph" / "review-queue.md",
        ]
        tracked_set = set(tracked)
        for lf in live_shm_files:
            self.assertNotIn(str(lf.relative_to(ROOT)), tracked_set)

        if live_shm_files[0].is_file():
            for lf in live_shm_files:
                self.assertTrue(lf.is_file() and lf.stat().st_size > 0, f"Live SHM file missing or empty: {lf}")
            shm_tracker = json.loads(live_shm_files[0].read_text(encoding="utf-8"))
            self.assertEqual(shm_tracker.get("slug"), "shm")
            self.assertEqual(len(shm_tracker.get("topics", [])), 10)
            self.assertEqual(len(shm_tracker.get("questions", [])), 172)
            shm_props = [
                json.loads(ln)
                for ln in live_shm_files[1].read_text(encoding="utf-8").splitlines()
                if ln.strip()
            ]
            self.assertGreaterEqual(len(shm_props), 3)
            shm_extracted = ROOT / "sources" / "_extracted"
            if shm_extracted.is_dir():
                for dname in (
                    "426-simple-harmonic-motion-shm-simple-harmonic-motion-module",
                    "eduniti-pyqs",
                    "shm",
                    "shm-final",
                ):
                    dp_dir = shm_extracted / dname
                    self.assertTrue(dp_dir.is_dir() and any(dp_dir.iterdir()), f"Live SHM extracted dir missing: {dp_dir}")


class TestV3ServerHooksMcpAndWindowsPortability(unittest.TestCase):
    """31. Syntopik v3.0 Studio Server Endpoints, Hooks/sync-graph v5, Fixed CLI Flags, --vault local, Windows Configs & MCP v3."""

    def test_v3_studio_server_endpoints_zero_leak_csrf_and_lan_isolation(self) -> None:
        import syntopik.forks as fk
        import syntopik.server as srv_mod
        import syntopik.whiteboard as wb

        df = load_script_module("derivation_forge.py")
        fa = load_script_module("falsify_arena.py")

        def _req(
            port: int,
            method: str,
            path: str,
            *,
            headers: dict[str, str] | None = None,
            body: Any = None,
        ) -> tuple[int, dict[str, str], bytes]:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=6.0)
            try:
                hdrs = dict(headers or {})
                raw = None
                if body is not None:
                    raw = json.dumps(body).encode("utf-8") if isinstance(body, (dict, list)) else body
                    hdrs.setdefault("Content-Type", "application/json")
                conn.request(method, path, body=raw, headers=hdrs)
                resp = conn.getresponse()
                data = resp.read()
                return resp.status, {k.lower(): v for k, v in resp.getheaders()}, data
            finally:
                conn.close()

        with tempfile.TemporaryDirectory(prefix="synt-v3-srv-") as tmp:
            ws = Path(tmp) / "ws"
            kg = ws / "knowledge-graph"
            ch = ws / "chapters"
            kg.mkdir(parents=True)
            ch.mkdir(parents=True)

            (kg / "propositions.jsonl").write_text(
                "\n".join([
                    json.dumps({
                        "id": "p-root", "subject": "Physics", "chapter": "SHM",
                        "title": "Restoring force", "kind": "axiom", "status": "shaky", "bktMastery": 0.4,
                        "boundaryConditions": ["linear spring"], "antiPatterns": ["ignores minus sign"],
                    }),
                    json.dumps({
                        "id": "p-leaf", "subject": "Physics", "chapter": "SHM",
                        "title": "SHM period", "kind": "theorem", "status": "shaky", "bktMastery": 0.45,
                        "boundaryConditions": ["small amplitude"], "antiPatterns": ["uses g instead of k/m"],
                    }),
                ]) + "\n",
                encoding="utf-8",
            )
            (kg / "chains.jsonl").write_text(
                json.dumps({"premises": ["p-root"], "conclusion": "p-leaf", "relation": "derives"}) + "\n",
                encoding="utf-8",
            )

            # Seed secrets across forks, arenas, decisions, derivations, and locked whiteboard gate
            canary_fork_win = "SECRET_WIN_MOVE_7741"
            canary_fork_blunder = "SECRET_BLUNDER_7742"
            canary_mutant = "SECRET_MUTANT_CODE_7743"
            canary_wb_ans = "SECRET_GATE_ANS_7744"
            canary_wb_body = "SECRET_GATE_BODY_7745"
            canary_deriv_expr = "2*pi*sqrt(m/k)"

            fk.capture_fork(
                prop_id="p-leaf",
                prefix_steps=["Spring mass m, stiffness k"],
                fork_prompt="Write angular frequency squared.",
                sealed_blunder=canary_fork_blunder,
                sealed_winning_move=canary_fork_win,
                refutation_msg="Check units",
                kg_dir=kg,
                workspace=ws,
            )
            fa.create_arena(
                prop_id="p-leaf",
                domain="code",
                claim="Return max of list",
                entry="my_max",
                sealed_reference={"code": "def my_max(xs):\n    return max(xs)\n"},
                sealed_mutants=[{"id": "m1", "trapTag": "neg", "code": f"# {canary_mutant}\ndef my_max(xs):\n    return 0\n"}],
                assumption_predicates=["len(args[0]) >= 1"],
                workspace=ws,
                kg_dir=kg,
            )
            (kg / "decisions.jsonl").write_text(
                json.dumps({
                    "id": "dec-1", "propId": "p-leaf", "prompt": "Pick first move",
                    "killCue": "SECRET_KILL_CUE_7746", "sealedAnswer": "SECRET_DEC_ANS_7747",
                }) + "\n",
                encoding="utf-8",
            )
            df.create_derivation(
                {
                    "id": "d-srv-1", "propId": "p-leaf", "chapter": "SHM", "title": "SHM T", "domain": "Physics",
                    "steps": [
                        {"id": "a1", "label": "Axiom", "expr": "a = -w**2*x", "role": "axiom", "dependsOn": []},
                        {"id": "s1", "label": "Period", "expr": canary_deriv_expr, "role": "theorem", "dependsOn": ["a1"]},
                    ],
                },
                kg_dir=kg,
                workspace=ws,
            )
            wb.push_frame(
                kg,
                {
                    "id": "wb-gate-1",
                    "kind": "prediction_gate",
                    "title": "Locked Gate",
                    "prompt": "Predict before unlock",
                    "expectedAnswer": canary_wb_ans,
                    "revealedBody": canary_wb_body,
                },
            )

            srv = srv_mod.create_server(workspace=ws, host="127.0.0.1", port=0, lan=False)
            t_srv = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
            t_srv.start()
            try:
                port = srv.bound_port
                auth = {"X-Syntopik-Token": srv.csrf_token}

                # 1. GET /api/state -> schemaVersion == 5 & ZERO leaked secrets!
                st_st, _, raw_st = _req(port, "GET", "/api/state")
                self.assertEqual(st_st, 200)
                st_text = raw_st.decode("utf-8")
                state_obj = json.loads(st_text)
                self.assertEqual(state_obj["hub"]["schemaVersion"], 5)
                for secret in (canary_fork_win, canary_fork_blunder, canary_mutant, canary_wb_ans, canary_wb_body, "SECRET_DEC_ANS_7747"):
                    self.assertNotIn(secret, st_text, f"GET /api/state leaked secret: {secret}")

                # 2. CSRF 403 enforcement on v3 POST/PATCH routes
                st_csrf1, _, _ = _req(port, "POST", "/api/profile/calibrate", body={"entryRamp": "balanced"})
                self.assertEqual(st_csrf1, 403)
                st_csrf2, _, _ = _req(port, "PATCH", "/api/profile/override", body={"maxProseWords": 110})
                self.assertEqual(st_csrf2, 403)
                st_csrf3, _, _ = _req(port, "POST", "/api/whiteboard/push", body={"kind": "summary", "body": "x"})
                self.assertEqual(st_csrf3, 403)

                # 3. Profile GET / calibrate / PATCH override
                st_p0, _, raw_p0 = _req(port, "GET", "/api/profile")
                self.assertEqual(st_p0, 200)
                self.assertTrue(json.loads(raw_p0)["ok"])

                st_cal, _, raw_cal = _req(
                    port, "POST", "/api/profile/calibrate", headers=auth,
                    body={"entryRamp": "formalism_first", "challengeAppetite": "balanced", "proseDensity": "terse"},
                )
                self.assertEqual(st_cal, 200, raw_cal)
                self.assertTrue(json.loads(raw_cal)["profile"]["calibration"]["completed"])

                st_ov, _, raw_ov = _req(
                    port, "PATCH", "/api/profile/override", headers=auth,
                    body={"scaffoldingMode": "standard", "maxProseWords": 120},
                )
                self.assertEqual(st_ov, 200, raw_ov)
                self.assertEqual(json.loads(raw_ov)["compiledPolicy"]["maxProseWords"], 120)

                # 4. /api/next-move, /api/root-cause, /api/evidence
                st_nm, _, raw_nm = _req(port, "GET", "/api/next-move")
                self.assertEqual(st_nm, 200)
                self.assertIn("recommendedMode", json.loads(raw_nm))

                st_rc, _, raw_rc = _req(port, "GET", "/api/root-cause?nodeId=p-leaf")
                self.assertEqual(st_rc, 200)
                rc_j = json.loads(raw_rc)
                self.assertTrue(rc_j["ok"])
                self.assertEqual(rc_j["localization"]["topSuspect"]["propId"], "p-root")

                st_ev, _, raw_ev = _req(port, "GET", "/api/evidence")
                self.assertEqual(st_ev, 200)
                self.assertIn("tierCounts", json.loads(raw_ev))

                # 5. /api/whiteboard*
                st_wbg, _, raw_wbg = _req(port, "GET", "/api/whiteboard")
                self.assertEqual(st_wbg, 200)
                self.assertNotIn(canary_wb_ans, raw_wbg.decode("utf-8"))
                self.assertNotIn(canary_wb_body, raw_wbg.decode("utf-8"))

                st_wbp, _, raw_wbp = _req(
                    port, "POST", "/api/whiteboard/push", headers=auth,
                    body={"kind": "derivation_step", "title": "Step 2", "body": "Integrate"},
                )
                self.assertEqual(st_wbp, 200, raw_wbp)
                st_wbpin, _, raw_wbpin = _req(
                    port, "POST", "/api/whiteboard/pin", headers=auth,
                    body={"stepIndex": 0, "note": "Check sign"},
                )
                self.assertEqual(st_wbpin, 200, raw_wbpin)
                st_wbu, _, raw_wbu = _req(
                    port, "POST", "/api/whiteboard/unlock", headers=auth,
                    body={"frameId": "wb-gate-1", "prediction": "negative", "confidence": 0.8},
                )
                self.assertEqual(st_wbu, 200, raw_wbu)
                self.assertTrue(json.loads(raw_wbu)["unlocked"])
                st_wbc, _, raw_wbc = _req(port, "POST", "/api/whiteboard/clear", headers=auth, body={})
                self.assertEqual(st_wbc, 200, raw_wbc)
                self.assertTrue(json.loads(raw_wbc)["cleared"])

                # 6. /api/derivation/* & /api/viva/*
                st_dg, _, raw_dg = _req(port, "GET", "/api/derivations")
                self.assertEqual(st_dg, 200)
                self.assertEqual(json.loads(raw_dg)["count"], 1)

                st_dc, _, raw_dc = _req(
                    port, "POST", "/api/derivation/check", headers=auth,
                    body={"derivationId": "d-srv-1", "stepId": "s1", "answer": "sqrt(m/k)*2*pi"},
                )
                self.assertEqual(st_dc, 200, raw_dc)
                self.assertTrue(json.loads(raw_dc)["passed"])

                st_dcut, _, raw_dcut = _req(
                    port, "POST", "/api/derivation/cut", headers=auth,
                    body={"derivationId": "d-srv-1", "axiomId": "a1", "guessStepId": "s1"},
                )
                self.assertEqual(st_dcut, 200, raw_dcut)
                self.assertTrue(json.loads(raw_dcut)["passed"])

                st_vs, _, raw_vs = _req(port, "POST", "/api/viva/start", headers=auth, body={"propId": "p-leaf"})
                self.assertEqual(st_vs, 200, raw_vs)
                v_sid = json.loads(raw_vs)["sessionId"]
                st_vt, _, raw_vt = _req(
                    port, "POST", "/api/viva/turn", headers=auth,
                    body={"sessionId": v_sid, "response": "Because SHM requires small amplitude linear restoring force.", "confidence": 0.8},
                )
                self.assertEqual(st_vt, 200, raw_vt)
                self.assertEqual(json.loads(raw_vt)["turnCompleted"], 1)

                # 7. /api/demo/load (valid starter pack + traversal 400)
                st_bad_demo, _, _ = _req(port, "POST", "/api/demo/load", headers=auth, body={"packSlug": "../evil"})
                self.assertEqual(st_bad_demo, 400)
                st_ok_demo, _, raw_ok_demo = _req(port, "POST", "/api/demo/load", headers=auth, body={"packSlug": "raft-consensus"})
                self.assertEqual(st_ok_demo, 200, raw_ok_demo)
                self.assertTrue(json.loads(raw_ok_demo)["ok"])
            finally:
                srv.shutdown()
                srv.server_close()
                t_srv.join(timeout=3.0)

            # 8. --lan paired-cookie isolation: MUST block /api/profile & /api/whiteboard (403)
            srv_lan = srv_mod.create_server(workspace=ws, host="127.0.0.1", port=0, lan=True)
            t_lan = threading.Thread(target=srv_lan.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
            t_lan.start()
            try:
                lport = srv_lan.bound_port
                lan_host = f"192.168.1.77:{lport}"
                st_p, hdrs_p, _ = _req(lport, "GET", f"/pocket?pair={srv_lan.pair_token}", headers={"Host": lan_host})
                self.assertEqual(st_p, 302)
                cookie = hdrs_p["set-cookie"].split(";", 1)[0]

                st_pkt, _, _ = _req(lport, "GET", "/api/pocket/state", headers={"Host": lan_host, "Cookie": cookie})
                self.assertEqual(st_pkt, 200)
                for blocked_path in ("/api/profile", "/api/whiteboard", "/api/next-move", "/api/root-cause", "/api/evidence"):
                    st_b, _, _ = _req(lport, "GET", blocked_path, headers={"Host": lan_host, "Cookie": cookie})
                    self.assertEqual(st_b, 403, f"--lan cookie MUST be blocked (403) on {blocked_path}")
                st_bpost, _, _ = _req(
                    lport, "POST", "/api/whiteboard/push",
                    headers={"Host": lan_host, "Origin": f"http://{lan_host}", "Cookie": cookie},
                    body={"kind": "summary", "body": "blocked"},
                )
                self.assertEqual(st_bpost, 403)
            finally:
                srv_lan.shutdown()
                srv_lan.server_close()
                t_lan.join(timeout=3.0)

    def test_v3_hooks_sync_graph_cli_flags_vault_local_attach_and_windows_configs(self) -> None:
        import syntopik.cli as cli_mod
        import syntopik.paths as paths_mod
        import syntopik.whiteboard as wb

        with tempfile.TemporaryDirectory(prefix="synt-v3-cli-") as tmp:
            t = Path(tmp)
            ws = t / "ws"
            kg = ws / "knowledge-graph"
            ch = ws / "chapters"
            kg.mkdir(parents=True)
            ch.mkdir(parents=True)

            (kg / "propositions.jsonl").write_text(
                json.dumps({
                    "id": "cs-q1", "subject": "CS", "chapter": "Consensus", "title": "Quorum intersection",
                    "kind": "axiom", "status": "shaky", "nextReview": "2020-01-01",
                }) + "\n",
                encoding="utf-8",
            )
            (kg / "chains.jsonl").write_text("", encoding="utf-8")
            (kg / "mistakes.jsonl").write_text("", encoding="utf-8")
            (kg / "forks.jsonl").write_text(
                json.dumps({
                    "id": "fk-1", "propId": "cs-q1", "prompt": "Min quorum?",
                    "sealedBlunder": "SECRET_SYNC_BLUNDER", "sealedWinningMove": "SECRET_SYNC_WIN",
                    "nextReview": "2020-01-01",
                }) + "\n",
                encoding="utf-8",
            )
            wb.push_frame(
                kg,
                {
                    "id": "g-sync", "kind": "prediction_gate", "title": "Gate",
                    "prompt": "Predict?", "expectedAnswer": "SECRET_SYNC_GATE", "revealedBody": "SECRET_SYNC_BODY",
                },
            )
            wb.pin_step(kg, 0, "Need intuition on quorum")

            (ch / "consensus.md").write_text(
                "# Consensus\n\n:::explore quorum-lab\n"
                '{"title":"Quorum Size","params":[{"name":"N","min":3,"max":11,"step":2,"default":5}],"outputs":[{"label":"Quorum","expr":"floor(N/2)+1"}]}\n'
                ":::\n",
                encoding="utf-8",
            )

            # 1. session-start.mjs --workspace <tmp>, post-graph-sync.mjs --workspace <tmp>, sync-graph.mjs v5
            ss = run_cli(["node", str(ROOT / ".agents" / "hooks" / "session-start.mjs"), "--workspace", str(ws)], stdin_text="{}")
            self.assertEqual(ss.returncode, 0, ss.stderr)
            msg = json.loads(ss.stdout)["injectSteps"][0]["systemMessage"]["systemMessage"]
            self.assertIn("Learner Profile:", msg)
            self.assertIn("Active TeachingPolicy:", msg)
            self.assertIn("Due Forks/Arenas/Re-Entry:", msg)
            self.assertIn("Pending Whiteboard Pins (1):", msg)
            self.assertIn("Need intuition on quorum", msg)

            ps = run_cli(
                ["node", str(ROOT / ".agents" / "hooks" / "post-graph-sync.mjs"), "--workspace", str(ws)],
                stdin_text=json.dumps({"tool_name": "Write", "tool_input": {"file_path": str(kg / "learner-profile.json")}}),
            )
            self.assertEqual(ps.returncode, 0, ps.stderr)
            self.assertTrue((kg / "graph-data.js").is_file())

            gd_out = kg / "graph-data-v5.js"
            sg = run_cli([
                "node", str(SCRIPTS_DIR / "sync-graph.mjs"),
                "--kg-dir", str(kg), "--chapters-dir", str(ch), "--out", str(gd_out),
            ])
            self.assertEqual(sg.returncode, 0, sg.stderr)
            gd_txt = gd_out.read_text(encoding="utf-8")
            for secret in ("SECRET_SYNC_BLUNDER", "SECRET_SYNC_WIN", "SECRET_SYNC_GATE", "SECRET_SYNC_BODY"):
                self.assertNotIn(secret, gd_txt, f"sync-graph.mjs leaked secret: {secret}")
            # Parse window.__STUDY_HUB__ via Node cleanly
            hub_eval = run_cli(
                ["node", "-e", f"globalThis.window={{}}; require({json.dumps(str(gd_out))}); console.log(JSON.stringify(window.__STUDY_HUB__));"]
            )
            self.assertEqual(hub_eval.returncode, 0, hub_eval.stderr)
            hub = json.loads(hub_eval.stdout)
            self.assertEqual(hub["schemaVersion"], 5)
            self.assertGreaterEqual(len(hub.get("starterPacks") or []), 3)
            expl_map = hub.get("explorables") or {}
            self.assertIsInstance(expl_map, dict)
            all_expls = [item for arr in expl_map.values() if isinstance(arr, list) for item in arr]
            self.assertGreaterEqual(len(all_expls), 1)
            self.assertTrue(all_expls[0]["outputs"][0]["rpn"])

            # 2. All 5 fixed CLI flags (triage --exam-in 48h, review --recover, doctor --fix, import --chapter, export json|dossier|--pocket|--vault)
            def _run_py_cli(args: list[str]) -> dict[str, Any]:
                p = run_cli([sys.executable, "-B", "-m", "syntopik.cli", *args])
                self.assertEqual(p.returncode, 0, f"CLI {args} failed (rc={p.returncode}):\nSTDOUT: {p.stdout}\nSTDERR: {p.stderr}")
                return json.loads(p.stdout)

            tr_res = _run_py_cli(["triage", "--exam-in", "48h", "--workspace", str(ws), "--json"])
            self.assertTrue(tr_res["ok"])
            self.assertAlmostEqual(tr_res["triage"]["hoursLeft"], 48.0, places=1)

            rev_res = _run_py_cli(["review", "--recover", "--daily-cap", "10", "--workspace", str(ws), "--json"])
            self.assertTrue(rev_res["ok"])
            self.assertEqual(rev_res["mode"], "recover")

            ws_fix = t / "ws-fix"
            doc_res = _run_py_cli(["doctor", "--fix", "--core-only", "--workspace", str(ws_fix), "--json"])
            self.assertTrue(doc_res["fixApplied"])
            self.assertTrue((ws_fix / "knowledge-graph" / "propositions.jsonl").is_file())

            note_file = t / "my_notes.md"
            note_file.write_text("# Cache Coherence\nMESI protocol states.\n", encoding="utf-8")
            imp_res = _run_py_cli(["import", str(note_file), "--chapter", "mesi-cache", "--workspace", str(ws), "--json"])
            self.assertEqual(imp_res["slug"], "mesi-cache")

            exp_json_path = t / "export.json"
            ej = _run_py_cli(["export", "json", "--workspace", str(ws), "--out", str(exp_json_path), "--json"])
            self.assertTrue(ej["ok"] and exp_json_path.is_file())
            self.assertEqual(json.loads(exp_json_path.read_text(encoding="utf-8"))["format"], "json")

            exp_dos_path = t / "dossier.html"
            ed = _run_py_cli(["export", "dossier", "--workspace", str(ws), "--out", str(exp_dos_path), "--json"])
            self.assertTrue(ed["ok"] and exp_dos_path.is_file())

            exp_pkt_path = t / "pocket.html"
            ep = _run_py_cli(["export", "--pocket", "--workspace", str(ws), "--out", str(exp_pkt_path), "--json"])
            self.assertTrue(ep["ok"] and exp_pkt_path.is_file())

            exp_vault_dir = t / "obs-vault"
            eo = _run_py_cli(["export", "obsidian", "--vault", str(exp_vault_dir), "--workspace", str(ws), "--json"])
            self.assertTrue(eo["ok"] and exp_vault_dir.is_dir())

            # 3. --vault local split-brain parity + attach --harness auto|antigravity + Claude settings hook merge preservation
            repo = t / "host-repo"
            repo.mkdir(parents=True)
            subprocess.run(["git", "init", "-q"], cwd=str(repo), check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=str(repo), check=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=str(repo), check=True)
            host_agents = "# Custom Host AGENTS\n"
            (repo / "AGENTS.md").write_text(host_agents, encoding="utf-8")
            (repo / ".claude").mkdir(parents=True)
            custom_hook = {"hooks": [{"type": "command", "command": "echo existing-host-hook"}]}
            (repo / ".claude" / "settings.json").write_text(
                json.dumps({"hooks": {"SessionStart": [custom_hook]}}),
                encoding="utf-8",
            )
            subprocess.run(["git", "add", "."], cwd=str(repo), check=True)
            subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=str(repo), check=True)

            att_auto = cli_mod.attach_workspace(repo, harness="auto", vault="local")
            self.assertTrue(att_auto["ok"])
            self.assertIn("claude", att_auto["harnesses"])
            # Split-brain parity: resolve_workspace(repo) MUST equal <repo>/.syntopik!
            self.assertEqual(paths_mod.resolve_workspace(repo), (repo / ".syntopik").resolve())
            self.assertEqual(paths_mod.resolve_kg_dir(repo), (repo / ".syntopik" / "knowledge-graph").resolve())

            # Existing host hook MUST be preserved alongside Syntopik hook (and deduped on rerun)
            merged_1 = json.loads((repo / ".claude" / "settings.json").read_text(encoding="utf-8"))
            ss_entries_1 = merged_1["hooks"]["SessionStart"]
            cmds_1 = [h.get("command", "") for entry in ss_entries_1 for h in entry.get("hooks", [])]
            self.assertIn("echo existing-host-hook", cmds_1)
            self.assertTrue(any("session-start.mjs" in c for c in cmds_1))
            cli_mod.attach_workspace(repo, harness="claude", vault="local")
            merged_2 = json.loads((repo / ".claude" / "settings.json").read_text(encoding="utf-8"))
            self.assertEqual(len(merged_2["hooks"]["SessionStart"]), len(ss_entries_1))

            att_ag = cli_mod.attach_workspace(repo, harness="antigravity", vault="local")
            self.assertTrue(att_ag["ok"])
            self.assertTrue((repo / ".agents" / "hooks.json").is_file())
            self.assertEqual((repo / "AGENTS.md").read_text(encoding="utf-8"), host_agents)

            # 4. Zero Windows-breaking POSIX shell patterns in tracked hook & MCP configs!
            posix_forbidden = ("SYNT_WORKSPACE=", "$(git rev-parse", "${VAR:-", "MLH_WORKSPACE=")
            for rel_cfg in (
                ".claude/settings.json",
                ".codex/hooks.json",
                ".claude-plugin/hooks.json",
                ".agents/hooks.json",
                ".mcp.json",
            ):
                cfg_p = ROOT / rel_cfg
                self.assertTrue(cfg_p.is_file(), f"Missing tracked config: {rel_cfg}")
                txt = cfg_p.read_text(encoding="utf-8")
                for bad_tok in posix_forbidden:
                    self.assertNotIn(bad_tok, txt, f"Windows-breaking POSIX token {bad_tok!r} found in {rel_cfg}")
                self.assertIsNone(re.search(r"\$\{[A-Za-z0-9_]+:-", txt), f"POSIX default expansion found in {rel_cfg}")

    def test_mcp_v3_stdio_brainstem_whiteboard_frontier_and_blackout_zero_leak(self) -> None:
        import syntopik.whiteboard as wb

        server_mjs = ROOT / "mcp-app" / "mlh-studio" / "dist" / "server.mjs"
        self.assertTrue(server_mjs.is_file())

        with tempfile.TemporaryDirectory(prefix="synt-v3-mcp-") as tmp:
            t = Path(tmp)
            ws = t / "ws"
            kg = ws / "knowledge-graph"
            ch = ws / "chapters"
            kg.mkdir(parents=True)
            ch.mkdir(parents=True)
            (kg / "propositions.jsonl").write_text(
                json.dumps({
                    "id": "p-mcp-1", "subject": "Math", "chapter": "Linear Algebra",
                    "title": "Rank-nullity theorem", "kind": "theorem", "status": "shaky", "nextReview": "2020-01-01",
                }) + "\n",
                encoding="utf-8",
            )
            (kg / "chains.jsonl").write_text("", encoding="utf-8")
            (kg / "mistakes.jsonl").write_text("", encoding="utf-8")
            wb.push_frame(kg, {"kind": "derivation_step", "title": "Basis", "body": "Pick kernel basis"})
            wb.pin_step(kg, 0, "Why extend basis?")

            secret_expr = "SECRET_BLACKOUT_EXPR_8821"
            secret_sol = "SECRET_BLACKOUT_MODEL_8822"
            secret_gate = "SECRET_WB_GATE_ANS_8823"

            reqs = [
                {
                    "jsonrpc": "2.0", "id": 1, "method": "initialize",
                    "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "syntopik-v3-test", "version": "3.0"}},
                },
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "synt_next_move", "arguments": {"domain": "Math"}}},
                {
                    "jsonrpc": "2.0", "id": 3, "method": "tools/call",
                    "params": {
                        "name": "synt_whiteboard_step",
                        "arguments": {
                            "kind": "prediction_gate",
                            "title": "Kernel Dimension Gate",
                            "prompt": "What is dim(ker T) + dim(im T)?",
                            "expectedAnswer": secret_gate,
                            "revealedBody": "Equals dim(V).",
                        },
                    },
                },
                {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "synt_get_frontier", "arguments": {"limit": 5}}},
                {
                    "jsonrpc": "2.0", "id": 5, "method": "tools/call",
                    "params": {
                        "name": "synt_practice_card",
                        "arguments": {
                            "action": "pose",
                            "problemId": "bo-rank-nullity",
                            "mode": "blackout",
                            "derivationId": "deriv-rn",
                            "blackoutLevel": 2,
                            "statement": "Reconstruct the hidden rank-nullity proof step.",
                            "steps": [
                                {"id": "a1", "label": "Kernel basis", "role": "axiom", "expr": "span(u_1..u_k) = ker(T)", "hidden": False},
                                {"id": "s1", "label": "Extend & map", "role": "step", "expr": secret_expr, "expectedExpr": secret_expr, "hidden": True, "dependsOn": ["a1"]},
                            ],
                            "modelSolution": secret_sol,
                        },
                    },
                },
                {
                    "jsonrpc": "2.0", "id": 6, "method": "tools/call",
                    "params": {
                        "name": "synt_practice_card",
                        "arguments": {
                            "action": "conclude",
                            "problemId": "bo-rank-nullity",
                            "outcome": "solved",
                            "verdict": "Exact linear independence proof.",
                            "rung": None,
                        },
                    },
                },
            ]

            proc = run_cli(
                ["node", str(server_mjs), f"--workspace={ws}", f"--data-dir={t / 'mcp-data'}"],
                stdin_text="\n".join(json.dumps(r) for r in reqs) + "\n",
                timeout=15.0,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            by_id = {
                int(m["id"]): m
                for m in (json.loads(ln) for ln in proc.stdout.splitlines() if ln.strip())
                if "id" in m
            }

            self.assertEqual(by_id[1]["result"]["serverInfo"]["version"], "3.2.0")

            # 1. synt_next_move returns Action Envelope honoring pending pin
            nm = by_id[2]["result"]
            self.assertFalse(nm.get("isError", False), nm)
            self.assertEqual(nm["structuredContent"]["recommendedMode"], "whiteboard_pin_answer")
            self.assertIn("teachingPolicy", nm["structuredContent"])

            # 2. synt_whiteboard_step pushes frame, returns pendingPins, and seals expectedAnswer
            wb_r = by_id[3]["result"]
            self.assertFalse(wb_r.get("isError", False), wb_r)
            self.assertNotIn(secret_gate, json.dumps(wb_r), "synt_whiteboard_step leaked expectedAnswer!")
            self.assertGreaterEqual(len(wb_r["structuredContent"].get("pendingPins") or []), 1)

            # 3. synt_get_frontier enriched with v3 psychology & gym telemetry
            fr = by_id[4]["result"]
            self.assertFalse(fr.get("isError", False), fr)
            fr_sc = fr["structuredContent"]
            for key in ("learnerProfile", "teachingPolicy", "dueForks", "arenasCount", "coldStartDays", "pendingPins", "rosettaBridgesCount", "readinessSummary", "taperActive"):
                self.assertIn(key, fr_sc, f"synt_get_frontier missing v3.2 key: {key}")

            # 4. synt_practice_card(mode="blackout") ZERO leak on pose, verbatim reveal on conclude
            bo_pose = by_id[5]["result"]
            self.assertFalse(bo_pose.get("isError", False), bo_pose)
            bo_dump = json.dumps(bo_pose)
            self.assertNotIn(secret_expr, bo_dump, "Blackout pose leaked hidden step expr!")
            self.assertNotIn(secret_sol, bo_dump, "Blackout pose leaked modelSolution!")
            self.assertNotIn("expectedExpr", bo_dump, "Blackout pose leaked expectedExpr key!")
            self.assertEqual(bo_pose["structuredContent"]["cardMode"], "blackout")
            self.assertEqual(bo_pose["structuredContent"]["hiddenStepIds"], ["s1"])

            bo_conc = by_id[6]["result"]
            self.assertFalse(bo_conc.get("isError", False), bo_conc)
            self.assertEqual(bo_conc["structuredContent"]["modelSolution"], secret_sol)


if __name__ == "__main__":
    unittest.main(verbosity=2)
