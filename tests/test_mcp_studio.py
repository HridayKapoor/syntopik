#!/usr/bin/env python3
"""Syntopik regression suite: MCP Studio app, live local server, packaging and ambient MCP.

Shared fixtures live in tests/_helpers.py. Run via:
    python3 -B -m unittest discover -s tests -t tests -v
"""

from __future__ import annotations

import argparse
import contextlib
import http.client
import json
import re
import subprocess
import sys
import tempfile
import threading
import tomllib
import unittest
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from _helpers import (
    ROOT,
    _EncodedStringIO,
    load_script_module,
    run_cli,
)


class TestMcpAppStudio(unittest.TestCase):
    """12. LearnHarness Parity Tier 2: Official SEP-1865 mlh-studio MCP App & Installer."""

    def test_bundles_zero_cdn_stdio_protocol_and_installer_lifecycle(self) -> None:
        dist_dir = ROOT / "mcp-app" / "mlh-studio" / "dist"
        server_mjs = dist_dir / "server.mjs"
        self.assertTrue(server_mjs.is_file() and server_mjs.stat().st_size > 1000)

        for html_name in ("quiz-card.html", "practice-card.html", "studio-pane.html"):
            hp = dist_dir / html_name
            with self.subTest(bundle=html_name):
                self.assertTrue(hp.is_file() and hp.stat().st_size > 1000, f"Missing bundle: {hp}")
                txt = hp.read_text(encoding="utf-8")
                self.assertIsNone(
                    re.search(r"<(?:script|link)\b[^>]*\b(?:src|href)\s*=\s*['\"]https?://", txt, re.I),
                    f"External CDN tag forbidden in MCP App bundle {html_name}",
                )

        # Live stdio JSON-RPC test against bundled server.mjs in isolated /tmp workspace
        with tempfile.TemporaryDirectory(prefix="mlh-mcp-test-") as tmp:
            t = Path(tmp)
            ws_dir = t / "ws"
            (ws_dir / "knowledge-graph").mkdir(parents=True, exist_ok=True)
            (ws_dir / "knowledge-graph" / "propositions.jsonl").write_text("", encoding="utf-8")
            (ws_dir / "chapters").mkdir(parents=True, exist_ok=True)
            (ws_dir / "chapters" / "shm-tracker.json").write_text(
                json.dumps({
                    "schemaVersion": 1,
                    "slug": "shm",
                    "title": "Simple Harmonic Motion",
                    "subject": "Physics",
                    "active": True,
                    "topics": [{"id": "t1", "order": 1, "title": "Restoring Law", "level": "basics", "status": "mastered"}],
                    "questions": [{"qid": "q1", "label": "Ex-1 Q1", "topicId": "t1", "level": "basics", "status": "unseen"}],
                    "milestones": {"sourcesIndexed": True, "probed": True},
                }),
                encoding="utf-8",
            )

            secret_sol = "SECRET_MODEL_SOLUTION_NEVER_LEAK_4291"
            reqs = [
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {"name": "mlh-unittest", "version": "1.0"},
                    },
                },
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
                {"jsonrpc": "2.0", "id": 3, "method": "resources/list", "params": {}},
                {"jsonrpc": "2.0", "id": 4, "method": "resources/read", "params": {"uri": "ui://mlh/quiz-card.html"}},
                {
                    "jsonrpc": "2.0",
                    "id": 5,
                    "method": "tools/call",
                    "params": {
                        "name": "mlh_quiz_card",
                        "arguments": {"role": "check", "question": "Bad quiz missing options", "explanation": "Because"},
                    },
                },
                {
                    "jsonrpc": "2.0",
                    "id": 6,
                    "method": "tools/call",
                    "params": {
                        "name": "mlh_quiz_card",
                        "arguments": {
                            "role": "check",
                            "nodeId": "phy-work",
                            "question": "Does normal force do work on a fixed incline?",
                            "options": [{"id": "a", "label": "Zero work"}, {"id": "b", "label": "Positive work"}],
                            "explanation": "Normal force is perpendicular to instantaneous displacement on a fixed track.",
                            "correctAnswer": "a",
                        },
                    },
                },
                {
                    "jsonrpc": "2.0",
                    "id": 7,
                    "method": "tools/call",
                    "params": {
                        "name": "mlh_practice_card",
                        "arguments": {
                            "action": "pose",
                            "problemId": "prob-1",
                            "statement": "Find acceleration of a solid sphere rolling down angle $\\theta$.",
                            "mode": "recall",
                            "targets": ["phy-rot-rolling"],
                            "modelSolution": secret_sol,
                        },
                    },
                },
                {
                    "jsonrpc": "2.0",
                    "id": 8,
                    "method": "tools/call",
                    "params": {
                        "name": "mlh_practice_card",
                        "arguments": {
                            "action": "pose",
                            "problemId": "prob-2",
                            "statement": "Second problem while first active must fail",
                            "modelSolution": "Other",
                        },
                    },
                },
                {
                    "jsonrpc": "2.0",
                    "id": 9,
                    "method": "tools/call",
                    "params": {
                        "name": "mlh_practice_card",
                        "arguments": {
                            "action": "conclude",
                            "problemId": "prob-1",
                            "outcome": "solved",
                            "verdict": "Exact rolling constraint applied.",
                            "rung": None,
                        },
                    },
                },
                {
                    "jsonrpc": "2.0",
                    "id": 10,
                    "method": "tools/call",
                    "params": {"name": "mlh_lesson_studio", "arguments": {}},
                },
                {
                    "jsonrpc": "2.0",
                    "id": 11,
                    "method": "tools/call",
                    "params": {"name": "mlh_app_load_state", "arguments": {"section": "all"}},
                },
            ]

            stdin_payload = "\n".join(json.dumps(r) for r in reqs) + "\n"
            proc = run_cli(
                ["node", str(server_mjs), f"--workspace={ws_dir}"],
                stdin_text=stdin_payload,
                timeout=10.0,
            )
            self.assertEqual(proc.returncode, 0, f"MCP server exited {proc.returncode}: {proc.stderr}")

            by_id: dict[int, dict[str, Any]] = {}
            for line in proc.stdout.splitlines():
                line = line.strip()
                if not line:
                    continue
                msg = json.loads(line)
                if "id" in msg:
                    by_id[int(msg["id"])] = msg

            # 1. initialize
            self.assertIn("serverInfo", by_id[1]["result"])
            self.assertEqual(by_id[1]["result"]["serverInfo"]["version"], "3.2.0")

            # 2. tools/list: exact 23 tools (13 model-visible: 6 UI synt_*/mlh_* + 7 ambient headless; 10 visibility:["app"])
            tools = by_id[2]["result"]["tools"]
            tools_by_name = {tl["name"]: tl for tl in tools}
            self.assertEqual(len(tools), 23)
            model_tools = [tl for tl in tools if "app" not in tl.get("_meta", {}).get("ui", {}).get("visibility", [])]
            app_tools = [tl for tl in tools if tl.get("_meta", {}).get("ui", {}).get("visibility") == ["app"]]
            self.assertEqual(
                {tl["name"] for tl in model_tools},
                {
                    "synt_quiz_card",
                    "synt_practice_card",
                    "synt_lesson_studio",
                    "synt_next_move",
                    "synt_whiteboard_step",
                    "synt_capture_concept",
                    "synt_get_frontier",
                    "synt_log_review",
                    "synt_readiness",
                    "synt_interleave",
                    "mlh_quiz_card",
                    "mlh_practice_card",
                    "mlh_lesson_studio",
                },
            )
            self.assertEqual(
                {tl["name"] for tl in app_tools},
                {
                    "synt_app_load_state",
                    "synt_app_rate_flashcard",
                    "synt_app_save_sketch",
                    "synt_app_card_state",
                    "synt_app_update_profile",
                    "mlh_app_load_state",
                    "mlh_app_rate_flashcard",
                    "mlh_app_save_sketch",
                    "mlh_app_card_state",
                    "mlh_app_update_profile",
                },
            )

            # Check ui:// URIs, explanation-before-correctAnswer ordering, and aux_pane + global entrypoints
            self.assertEqual(tools_by_name["synt_quiz_card"]["_meta"]["ui"]["resourceUri"], "ui://syntopik/quiz-card.html")
            self.assertEqual(tools_by_name["mlh_quiz_card"]["_meta"]["ui"]["resourceUri"], "ui://mlh/quiz-card.html")
            for q_tool_name in ("synt_quiz_card", "mlh_quiz_card"):
                q_props = list(tools_by_name[q_tool_name]["inputSchema"]["properties"].keys())
                self.assertLess(q_props.index("explanation"), q_props.index("correctAnswer"))
            # Antigravity UI entrypoints MUST appear ONLY on synt_lesson_studio (never duplicated on mlh_lesson_studio)
            ep_tools = [
                tl["name"]
                for tl in tools
                if tl.get("_meta", {}).get("com.google.antigravity/ui", {}).get("entrypoints")
            ]
            self.assertEqual(ep_tools, ["synt_lesson_studio"], f"Duplicate or missing UI entrypoints: {ep_tools}")
            eps = tools_by_name["synt_lesson_studio"]["_meta"]["com.google.antigravity/ui"]["entrypoints"]
            self.assertEqual({e["type"] for e in eps}, {"aux_pane", "global"})

            # 3 & 4. resources/list & resources/read MIME type (6 dual-namespace UI resources)
            res_uris = {r["uri"] for r in by_id[3]["result"]["resources"]}
            self.assertEqual(
                res_uris,
                {
                    "ui://syntopik/quiz-card.html",
                    "ui://syntopik/practice-card.html",
                    "ui://syntopik/studio-pane.html",
                    "ui://mlh/quiz-card.html",
                    "ui://mlh/practice-card.html",
                    "ui://mlh/studio-pane.html",
                },
            )
            read_content = by_id[4]["result"]["contents"][0]
            self.assertEqual(read_content["mimeType"], "text/html;profile=mcp-app")
            self.assertIn("<!DOCTYPE html>", read_content["text"])

            # 5 & 6. mlh_quiz_card validation rejection + valid payload
            self.assertTrue(by_id[5]["result"].get("isError"))
            self.assertFalse(by_id[6]["result"].get("isError", False))
            self.assertEqual(by_id[6]["result"]["structuredContent"]["role"], "check")

            # 7, 8, 9. mlh_practice_card hidden modelSolution gate + 1-active gate + verbatim conclude
            pose_resp = by_id[7]["result"]
            self.assertFalse(pose_resp.get("isError", False))
            self.assertNotIn(secret_sol, json.dumps(pose_resp), "modelSolution leaked during pose!")
            self.assertTrue(by_id[8]["result"].get("isError"), "1-active-problem gate did not reject second pose")
            conc_resp = by_id[9]["result"]
            self.assertFalse(conc_resp.get("isError", False))
            self.assertEqual(conc_resp["structuredContent"]["modelSolution"], secret_sol)

            # 10 & 11. mlh_lesson_studio empty {} launch + mlh_app_load_state chapterTrackers/activeChapter
            studio_resp = by_id[10]["result"]
            self.assertFalse(studio_resp.get("isError", False))
            self.assertEqual(studio_resp["structuredContent"]["kind"], "mlh_lesson_studio")
            self.assertEqual(studio_resp["structuredContent"]["activeChapter"]["slug"], "shm")

            state_resp = by_id[11]["result"]
            self.assertFalse(state_resp.get("isError", False))
            state_sc = state_resp["structuredContent"]
            self.assertEqual(len(state_sc["chapterTrackers"]), 1)
            self.assertEqual(state_sc["activeChapter"]["slug"], "shm")
            self.assertEqual(state_sc["activeChapter"]["topicsMastered"], 1)

            # (a2) Cross-process disk persistence test (survives chat switches & server restarts!)
            data_dir = t / "plugin-data"
            persist_secret = "PERSISTED_ACROSS_RESTART_SOL_9812"
            p1_reqs = [
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {"name": "mlh-persist-1", "version": "1.0"},
                    },
                },
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {
                        "name": "mlh_app_card_state",
                        "arguments": {
                            "op": "save",
                            "kind": "quiz",
                            "id": "quiz-shm-probe-1",
                            "state": {
                                "revealed": True,
                                "selectedIds": ["c"],
                                "dontKnow": False,
                                "confidence": 85,
                                "thinking": "Pseudo-force shifts equilibrium only.",
                                "submitted": True,
                                "verdict": "correct",
                                "statusMsg": "Response sent to tutor.",
                                "statusKind": "success",
                            },
                        },
                    },
                },
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {
                        "name": "mlh_app_card_state",
                        "arguments": {
                            "op": "save",
                            "kind": "quiz",
                            "id": "quiz-shm-probe-1",
                            "state": {"submitted": False, "confidence": 50, "selectedIds": []},
                        },
                    },
                },
                {
                    "jsonrpc": "2.0",
                    "id": 4,
                    "method": "tools/call",
                    "params": {
                        "name": "mlh_practice_card",
                        "arguments": {
                            "action": "pose",
                            "problemId": "prob-persist-1",
                            "statement": "Find time period of SHM with $k$.",
                            "modelSolution": persist_secret,
                        },
                    },
                },
                {
                    "jsonrpc": "2.0",
                    "id": 5,
                    "method": "tools/call",
                    "params": {
                        "name": "mlh_lesson_studio",
                        "arguments": {
                            "planMermaid": "graph TD\n  shm_law[Restoring Law] --> shm_phase[Phase Circle]",
                            "activeNodeId": "shm_phase",
                        },
                    },
                },
            ]
            p1 = run_cli(
                ["node", str(server_mjs), f"--workspace={ws_dir}", f"--data-dir={data_dir}"],
                stdin_text="\n".join(json.dumps(r) for r in p1_reqs) + "\n",
                timeout=10.0,
            )
            self.assertEqual(p1.returncode, 0, p1.stderr)
            self.assertTrue((data_dir / "ui-state.json").is_file())

            # Launch a brand-new server process against the same --data-dir
            p2_reqs = [
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {"name": "mlh-persist-2", "version": "1.0"},
                    },
                },
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {
                        "name": "mlh_app_card_state",
                        "arguments": {"op": "get", "kind": "quiz", "id": "quiz-shm-probe-1"},
                    },
                },
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {
                        "name": "mlh_practice_card",
                        "arguments": {
                            "action": "conclude",
                            "problemId": "prob-persist-1",
                            "outcome": "solved",
                            "verdict": "Exact period derived.",
                        },
                    },
                },
                {
                    "jsonrpc": "2.0",
                    "id": 4,
                    "method": "tools/call",
                    "params": {"name": "mlh_lesson_studio", "arguments": {}},
                },
            ]
            p2 = run_cli(
                ["node", str(server_mjs), f"--workspace={ws_dir}", f"--data-dir={data_dir}"],
                stdin_text="\n".join(json.dumps(r) for r in p2_reqs) + "\n",
                timeout=10.0,
            )
            self.assertEqual(p2.returncode, 0, p2.stderr)
            p2_by_id = {
                int(m["id"]): m
                for m in (json.loads(ln) for ln in p2.stdout.splitlines() if ln.strip())
                if "id" in m
            }
            restored_quiz = p2_by_id[2]["result"]["structuredContent"]
            self.assertTrue(restored_quiz["found"])
            # Monotonic protection kept submitted=True & confidence=85 despite stale draft attempt
            self.assertTrue(restored_quiz["state"]["submitted"])
            self.assertEqual(restored_quiz["state"]["selectedIds"], ["c"])
            self.assertEqual(restored_quiz["state"]["confidence"], 85)
            self.assertEqual(restored_quiz["state"]["verdict"], "correct")
            # Practice conclude across restart revealed the exact sealed modelSolution
            self.assertEqual(p2_by_id[3]["result"]["structuredContent"]["modelSolution"], persist_secret)
            # Study Studio kept persisted Plan DAG across restart
            self.assertEqual(p2_by_id[4]["result"]["structuredContent"]["plan"]["activeNodeId"], "shm_phase")
            self.assertIn("shm_law", p2_by_id[4]["result"]["structuredContent"]["plan"]["planMermaid"])

            # (b) install_mcp_app.py install -> status --json -> uninstall strictly in /tmp
            inst_mod = load_script_module("install_mcp_app.py")
            plugin_target = t / "plugin-target"

            buf_i = _EncodedStringIO()
            with contextlib.redirect_stdout(buf_i):
                rc_i = inst_mod.cmd_install(argparse.Namespace(
                    target=str(plugin_target), workspace=str(ws_dir), node=None, json=True,
                ))
            self.assertEqual(rc_i, 0)
            i_meta = json.loads(buf_i.getvalue())
            self.assertEqual(i_meta["status"], "installed")
            self.assertFalse(i_meta["hasNodeModules"])

            buf_s = _EncodedStringIO()
            with contextlib.redirect_stdout(buf_s):
                rc_s = inst_mod.cmd_status(argparse.Namespace(target=str(plugin_target), json=True))
            self.assertEqual(rc_s, 0)
            s_meta = json.loads(buf_s.getvalue())
            self.assertTrue(s_meta["installed"])
            self.assertTrue(s_meta["healthy"])
            self.assertEqual(s_meta["missingFiles"], [])

            buf_u = _EncodedStringIO()
            with contextlib.redirect_stdout(buf_u):
                rc_u = inst_mod.cmd_uninstall(argparse.Namespace(target=str(plugin_target), json=True))
            self.assertEqual(rc_u, 0)
            self.assertFalse(plugin_target.exists())


class TestLocalStudioServerLive(unittest.TestCase):
    """26. Syntopik v2.0 2-Way Live Local Studio Server: Security, REST Routes, SSE & LAN Pocket Scoping."""

    def test_local_studio_server_security_routes_sse_and_lan_pocket_scoping(self) -> None:
        import syntopik.forks as fk
        import syntopik.server as srv_mod

        fa = load_script_module("falsify_arena.py")
        ct = load_script_module("chapter_tracker.py")

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
                raw_body: bytes | None = None
                if body is not None:
                    raw_body = json.dumps(body).encode("utf-8") if not isinstance(body, (bytes, str)) else (
                        body.encode("utf-8") if isinstance(body, str) else body
                    )
                    hdrs.setdefault("Content-Type", "application/json")
                conn.request(method, path, body=raw_body, headers=hdrs)
                resp = conn.getresponse()
                data = resp.read()
                resp_hdrs = {k.lower(): v for k, v in resp.getheaders()}
                return resp.status, resp_hdrs, data
            finally:
                conn.close()

        with tempfile.TemporaryDirectory(prefix="synt-v2-server-") as tmp:
            ws = Path(tmp) / "ws"
            kg = ws / "knowledge-graph"
            ch = ws / "chapters"
            kg.mkdir(parents=True)
            ch.mkdir(parents=True)

            now = datetime.now(timezone.utc)
            d_old = (now - timedelta(days=12)).isoformat()
            d_past = (now - timedelta(days=5)).date().isoformat()
            props = [
                {
                    "id": "p-anc",
                    "subject": "Physics",
                    "chapter": "SHM",
                    "title": "Restoring force law",
                    "kind": "axiom",
                    "status": "solid",
                    "bktMastery": 0.80,
                    "nextReview": d_past,
                    "fsrs": {"stability": 3.0, "difficulty": 4.0, "due": d_old, "last_review": d_old},
                },
                {
                    "id": "p-child",
                    "subject": "Physics",
                    "chapter": "SHM",
                    "title": "SHM angular frequency",
                    "kind": "derivation",
                    "status": "shaky",
                    "bktMastery": 0.45,
                    "nextReview": d_past,
                    "fsrs": {"stability": 2.0, "difficulty": 5.0, "due": d_old, "last_review": d_old},
                },
            ] + [
                {
                    "id": f"p-ov-{i}",
                    "subject": "Physics",
                    "chapter": "SHM",
                    "title": f"Overdue item {i}",
                    "kind": "formula",
                    "status": "solid",
                    "nextReview": d_past,
                    "fsrs": {"stability": 3.0, "difficulty": 5.0, "due": d_old, "last_review": d_old},
                }
                for i in range(12)
            ]
            (kg / "propositions.jsonl").write_text("\n".join(json.dumps(p) for p in props) + "\n", encoding="utf-8")
            (kg / "chains.jsonl").write_text(
                json.dumps({"from": "p-anc", "to": "p-child", "relation": "derives"}) + "\n",
                encoding="utf-8",
            )
            (kg / "mistakes.jsonl").write_text("", encoding="utf-8")

            # Seed tracker, fork, and arena inside /tmp workspace
            buf_ct = _EncodedStringIO()
            with contextlib.redirect_stdout(buf_ct):
                ct.main(["init", "shm", "--title", "SHM", "--subject", "Physics", "--set-active", "--chapters-dir", str(ch), "--kg-dir", str(kg), "--no-sync", "--json"])
                ct.main([
                    "add-topics", "shm",
                    "--json-data", json.dumps([{"id": "t1", "order": 1, "title": "Restoring Law", "level": "basics"}]),
                    "--chapters-dir", str(ch), "--kg-dir", str(kg), "--no-sync", "--json",
                ])

            fork_rec = fk.capture_fork(
                prop_id="p-child",
                prefix_steps=["F = -kx"],
                fork_prompt="Write omega",
                sealed_blunder="k/m",
                sealed_winning_move="sqrt(k/m)",
                refutation_msg="Dimensional check: k/m has units s^-2, not s^-1!",
                verify_spec={"engine": "math"},
                workspace=ws,
                kg_dir=kg,
            )
            arena_rec = fa.create_arena(
                prop_id="p-child",
                domain="code",
                claim="Return max of non-empty list",
                entry="my_max",
                sealed_reference={"code": "def my_max(xs):\n    return max(xs)\n"},
                sealed_mutants=[{"id": "m-zero", "trapTag": "neg_bug", "code": "def my_max(xs):\n    m = 0\n    for x in xs:\n        if x > m: m = x\n    return m\n"}],
                assumption_predicates=["len(args[0]) >= 1"],
                workspace=ws,
                kg_dir=kg,
            )

            # 1. Start default loopback Studio server on ephemeral port=0
            srv = srv_mod.create_server(workspace=ws, host="127.0.0.1", port=0, lan=False)
            t_srv = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
            t_srv.start()
            try:
                port = srv.bound_port

                # (a) GET /api/capabilities
                st, _h, raw = _req(port, "GET", "/api/capabilities")
                self.assertEqual(st, 200)
                cap = json.loads(raw)
                self.assertTrue(cap["live"])
                token = cap["token"]
                self.assertEqual(token, srv.csrf_token)

                # (b) Security gates: missing/wrong X-Syntopik-Token, bad Origin, path traversal -> 403
                st_no_tok, _, _ = _req(port, "POST", "/api/review", body={"propId": "p-child", "rating": "good"})
                self.assertEqual(st_no_tok, 403)
                st_bad_tok, _, _ = _req(
                    port, "POST", "/api/review",
                    headers={"X-Syntopik-Token": "wrong-token"},
                    body={"propId": "p-child", "rating": "good"},
                )
                self.assertEqual(st_bad_tok, 403)
                st_bad_org, _, _ = _req(port, "GET", "/api/state", headers={"Origin": "https://evil.example"})
                self.assertEqual(st_bad_org, 403)
                st_trav, _, _ = _req(port, "GET", "/chapters/../../etc/passwd")
                self.assertEqual(st_trav, 403)

                auth_hdr = {"X-Syntopik-Token": token}

                # (c) GET /api/state
                st_state, _, raw_state = _req(port, "GET", "/api/state")
                self.assertEqual(st_state, 200)
                state_obj = json.loads(raw_state)
                self.assertTrue(state_obj["ok"])
                self.assertEqual(state_obj["hub"]["schemaVersion"], 5)
                self.assertGreaterEqual(state_obj["arenasCount"], 1)

                # (d) SSE /api/events receives ready -> then changed upon POST /api/review
                sse_events: list[dict[str, Any]] = []
                sse_ready = threading.Event()
                sse_changed = threading.Event()

                def _sse_reader() -> None:
                    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5.0)
                    try:
                        c.request("GET", "/api/events")
                        r = c.getresponse()
                        while not sse_changed.is_set():
                            line = r.fp.readline()
                            if not line:
                                break
                            dec = line.decode("utf-8", errors="ignore").strip()
                            if dec.startswith("data:"):
                                ev = json.loads(dec[5:].strip())
                                sse_events.append(ev)
                                if ev.get("type") == "ready":
                                    sse_ready.set()
                                elif ev.get("type") == "changed":
                                    sse_changed.set()
                                    break
                    except Exception:
                        pass
                    finally:
                        c.close()

                t_sse = threading.Thread(target=_sse_reader, daemon=True)
                t_sse.start()
                self.assertTrue(sse_ready.wait(timeout=3.0), "SSE stream did not emit ready event")

                # (e) POST /api/review persists propositions.jsonl + revlog.jsonl + FIRe & wakes SSE!
                st_rev, _, raw_rev = _req(
                    port, "POST", "/api/review", headers=auth_hdr,
                    body={"propId": "p-child", "rating": "good", "confidence": 0.9},
                )
                self.assertEqual(st_rev, 200, raw_rev)
                rev_json = json.loads(raw_rev)
                self.assertTrue(rev_json["ok"])
                self.assertGreaterEqual(rev_json["fireRefreshedCount"], 1)
                self.assertTrue((kg / "revlog.jsonl").is_file())
                self.assertTrue(sse_changed.wait(timeout=3.0), "SSE stream did not receive changed event on POST /api/review")
                t_sse.join(timeout=2.0)

                # (f) POST /api/recover
                st_rec, _, raw_rec = _req(port, "POST", "/api/recover", headers=auth_hdr, body={"dailyCap": 4, "days": 5})
                self.assertEqual(st_rec, 200, raw_rec)
                rec_json = json.loads(raw_rec)
                self.assertTrue(rec_json["applied"])
                self.assertGreaterEqual(rec_json["rescheduledCount"], 1)

                # (g) POST /api/exam/submit -> 3-bucket autopsy & persists attempts/mistakes
                opts = [{"id": "A", "text": "A"}, {"id": "B", "text": "B"}]
                st_ex, _, raw_ex = _req(
                    port, "POST", "/api/exam/submit", headers=auth_hdr,
                    body={
                        "examSlug": "srv-mock",
                        "spec": {
                            "examSlug": "srv-mock",
                            "title": "Server Mock",
                            "durationMinutes": 10,
                            "questions": [
                                {"id": "Q1", "type": "single_correct", "subject": "Physics", "chapter": "SHM", "propId": "p-child", "stem": "Q1", "options": opts, "answer": "A"},
                                {"id": "Q2", "type": "single_correct", "subject": "Physics", "chapter": "SHM", "trapCategory": "misconception", "stem": "Q2", "options": opts, "answer": "B"},
                            ],
                        },
                        "attempt": {
                            "examSlug": "srv-mock",
                            "timeSpentSec": 90,
                            "responses": {
                                "Q1": {"answer": "A", "status": "answered", "timeSpentSec": 40},
                                "Q2": {"answer": "A", "status": "answered", "timeSpentSec": 50},
                            },
                        },
                    },
                )
                self.assertEqual(st_ex, 200, raw_ex)
                ex_json = json.loads(raw_ex)
                self.assertTrue(ex_json["ok"])
                self.assertIn("knowledge", ex_json["autopsy"])
                self.assertIn("execution", ex_json["autopsy"])
                self.assertIn("time_sink", ex_json["autopsy"])
                self.assertTrue((kg / "exam-attempts.jsonl").is_file())
                self.assertTrue((kg / "mistakes.jsonl").read_text(encoding="utf-8").strip())

                # (h) POST /api/tracker/topic & POST /api/tracker/milestone
                st_tp, _, raw_tp = _req(
                    port, "POST", "/api/tracker/topic", headers=auth_hdr,
                    body={"slug": "shm", "topicId": "t1", "status": "mastered"},
                )
                self.assertEqual(st_tp, 200, raw_tp)
                self.assertEqual(json.loads(raw_tp)["status"], "mastered")

                st_ms, _, raw_ms = _req(
                    port, "POST", "/api/tracker/milestone", headers=auth_hdr,
                    body={"slug": "shm", "key": "probed", "value": True},
                )
                self.assertEqual(st_ms, 200, raw_ms)
                self.assertTrue(json.loads(raw_ms)["milestones"]["probed"])

                # (i) POST /api/forks/check & POST /api/arena/submit
                st_fk, _, raw_fk = _req(
                    port, "POST", "/api/forks/check", headers=auth_hdr,
                    body={"forkId": fork_rec["id"], "move": "k/m", "latencyMs": 5000},
                )
                self.assertEqual(st_fk, 200, raw_fk)
                fk_json = json.loads(raw_fk)
                self.assertTrue(fk_json["repeatedBlunder"])
                self.assertIsNone(fk_json["winningMove"])

                st_ar, _, raw_ar = _req(
                    port, "POST", "/api/arena/submit", headers=auth_hdr,
                    body={"arenaId": arena_rec["id"], "input": [[-5, -2]]},
                )
                self.assertEqual(st_ar, 200, raw_ar)
                ar_json = json.loads(raw_ar)
                self.assertIn("m-zero", ar_json["killed"])
                self.assertEqual(ar_json["survivedCount"], 0)

                # (j) Wave 3A hardening (#172): /demos/ jail, forged Host, unknown ID 404, downloadable pack export
                st_leak_srv, _, _ = _req(port, "GET", "/demos/syntopik/server.py")
                self.assertIn(st_leak_srv, (403, 404))
                st_leak_kg, _, _ = _req(port, "GET", "/demos/knowledge-graph/propositions.jsonl")
                self.assertIn(st_leak_kg, (403, 404))
                st_cbt, _, raw_cbt = _req(port, "GET", "/demos/exam-cbt-template.html")
                self.assertEqual(st_cbt, 200)
                self.assertIn(b"<!DOCTYPE html>", raw_cbt)

                st_bad_host, _, _ = _req(port, "GET", "/api/capabilities", headers={"Host": "[::1]evil.com"})
                self.assertEqual(st_bad_host, 403)

                st_fk_404, _, _ = _req(
                    port, "POST", "/api/forks/check", headers=auth_hdr,
                    body={"forkId": "fork-does-not-exist", "move": "1"},
                )
                self.assertEqual(st_fk_404, 404)
                st_ar_404, _, _ = _req(
                    port, "POST", "/api/arena/submit", headers=auth_hdr,
                    body={"arenaId": "arena-does-not-exist", "input": [1]},
                )
                self.assertEqual(st_ar_404, 404)

                st_pk_exp, _, raw_pk_exp = _req(
                    port, "POST", "/api/pack/export", headers=auth_hdr,
                    body={"target": "SHM"},
                )
                self.assertEqual(st_pk_exp, 200, raw_pk_exp)
                pk_json = json.loads(raw_pk_exp)
                self.assertTrue(str(pk_json.get("url", "")).startswith("/chapters/"), pk_json)
                st_pk_dl, _, raw_pk_dl = _req(port, "GET", pk_json["url"])
                self.assertEqual(st_pk_dl, 200)
                self.assertTrue(raw_pk_dl.startswith(b"PK\x03\x04"))
            finally:
                srv.shutdown()
                srv.server_close()
                t_srv.join(timeout=3.0)

            # 2. Opt-in `--lan` server: one-time QR pair cookie strictly scoped to `/pocket`, `/api/pocket/state`, `POST /api/review/batch`
            srv_lan = srv_mod.create_server(workspace=ws, host="127.0.0.1", port=0, lan=True)
            t_lan = threading.Thread(target=srv_lan.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
            t_lan.start()
            try:
                lport = srv_lan.bound_port
                pair_tok = srv_lan.pair_token
                self.assertTrue(pair_tok)
                lan_host = f"192.168.1.99:{lport}"

                # Unpaired LAN client blocked on /api/pocket/state
                st_unp, _, _ = _req(lport, "GET", "/api/pocket/state", headers={"Host": lan_host})
                self.assertEqual(st_unp, 403)

                # One-time pair token -> 302 + HttpOnly SameSite=Lax cookie
                st_pair, hdrs_pair, _ = _req(lport, "GET", f"/pocket?pair={pair_tok}", headers={"Host": lan_host})
                self.assertEqual(st_pair, 302)
                set_cookie = hdrs_pair.get("set-cookie", "")
                self.assertIn("syntopik_pocket_session=", set_cookie)
                self.assertIn("HttpOnly", set_cookie)
                cookie_pair = set_cookie.split(";", 1)[0]

                # Second reuse of one-time pair token MUST be rejected (403)
                st_reuse, _, _ = _req(lport, "GET", f"/pocket?pair={pair_tok}", headers={"Host": lan_host})
                self.assertEqual(st_reuse, 403)

                # POST /api/export/pocket rotates a fresh valid pair token after the first is consumed!
                st_rot, _, raw_rot = _req(
                    lport, "POST", "/api/export/pocket",
                    headers={"X-Syntopik-Token": srv_lan.csrf_token},
                    body={"limit": 10},
                )
                self.assertEqual(st_rot, 200, raw_rot)
                rot_json = json.loads(raw_rot)
                rot_url = str(rot_json.get("lanUrl") or "")
                self.assertIn("pair=", rot_url)
                rot_tok = urllib.parse.parse_qs(urllib.parse.urlsplit(rot_url).query).get("pair", [""])[0]
                self.assertTrue(rot_tok and rot_tok != pair_tok)
                st_pair2, hdrs_pair2, _ = _req(lport, "GET", f"/pocket?pair={rot_tok}", headers={"Host": lan_host})
                self.assertEqual(st_pair2, 302)
                self.assertIn("syntopik_pocket_session=", hdrs_pair2.get("set-cookie", ""))

                # Valid LAN cookie succeeds ONLY on the 3 Pocket routes
                st_pkt, _, raw_pkt = _req(lport, "GET", "/pocket", headers={"Host": lan_host, "Cookie": cookie_pair})
                self.assertEqual(st_pkt, 200)
                self.assertIn(b"<!DOCTYPE html>", raw_pkt)

                st_ps, _, raw_ps = _req(lport, "GET", "/api/pocket/state", headers={"Host": lan_host, "Cookie": cookie_pair})
                self.assertEqual(st_ps, 200)
                self.assertTrue(json.loads(raw_ps)["ok"])

                st_rb, _, raw_rb = _req(
                    lport, "POST", "/api/review/batch",
                    headers={"Host": lan_host, "Origin": f"http://{lan_host}", "Cookie": cookie_pair},
                    body={"reviews": [{"reviewId": "lan-rev-1", "propId": "p-child", "rating": "good"}]},
                )
                self.assertEqual(st_rb, 200, raw_rb)
                self.assertTrue(json.loads(raw_rb)["ok"])

                # STRICT SCOPE GUARD: LAN cookie MUST get 403 on `/api/state` and `POST /api/import`!
                st_blk_state, _, _ = _req(lport, "GET", "/api/state", headers={"Host": lan_host, "Cookie": cookie_pair})
                self.assertEqual(st_blk_state, 403)
                st_blk_imp, _, _ = _req(
                    lport, "POST", "/api/import",
                    headers={"Host": lan_host, "Origin": f"http://{lan_host}", "Cookie": cookie_pair},
                    body={"target": "https://example.org"},
                )
                self.assertEqual(st_blk_imp, 403)
            finally:
                srv_lan.shutdown()
                srv_lan.server_close()
                t_lan.join(timeout=3.0)


class TestPackagingAttachAndAmbientMcp(unittest.TestCase):
    """27. Syntopik v2.0 Packaging Manifests, Non-Destructive `syntopik attach`, & 17-Tool Ambient/Parsons MCP."""

    def test_packaging_manifests_cli_and_non_destructive_git_attach(self) -> None:
        import syntopik.cli as cli_mod

        # 1. Validate pyproject.toml (zero required deps, Python >=3.12, hatchling force-include)
        pyp = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertEqual(pyp["project"]["name"], "syntopik")
        self.assertEqual(pyp["project"]["version"], "3.2.0")
        self.assertEqual(pyp["project"]["dependencies"], [])
        self.assertEqual(pyp["project"]["scripts"]["syntopik"], "syntopik.cli:main")
        force_inc = pyp["tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"]
        self.assertIn("scripts", force_inc)

        # 2. Validate package.json + bin/syntopik.mjs --help / --version
        pkg = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
        self.assertEqual(pkg["name"], "syntopik")
        self.assertEqual(pkg["version"], "3.2.0")
        self.assertEqual(pkg["dependencies"], {})
        self.assertEqual(pkg["bin"]["syntopik"], "./bin/syntopik.mjs")

        h_proc = run_cli(["node", str(ROOT / "bin" / "syntopik.mjs"), "--help"])
        self.assertEqual(h_proc.returncode, 0, h_proc.stderr)
        self.assertIn("syntopik", h_proc.stdout.lower())
        v_proc = run_cli(["node", str(ROOT / "bin" / "syntopik.mjs"), "--version"])
        self.assertEqual(v_proc.returncode, 0, v_proc.stderr)
        self.assertIn("3.2.0", v_proc.stdout)

        # 3. Validate .claude-plugin/{marketplace,plugin,hooks}.json & gemini-extension.json
        cp_mkt = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
        cp_plg = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        cp_hks = json.loads((ROOT / ".claude-plugin" / "hooks.json").read_text(encoding="utf-8"))
        gem_ext = json.loads((ROOT / "gemini-extension.json").read_text(encoding="utf-8"))

        self.assertEqual(cp_mkt["name"], "syntopik")
        self.assertEqual(cp_mkt["metadata"]["version"], "3.2.0")
        self.assertEqual(cp_plg["version"], "3.2.0")
        self.assertTrue(cp_plg["skills"].startswith("./"))
        self.assertIn("SessionStart", cp_hks["hooks"])
        self.assertIn("PostToolUse", cp_hks["hooks"])
        self.assertEqual(gem_ext["name"], "syntopik")
        self.assertEqual(gem_ext["version"], "3.2.0")
        self.assertEqual(gem_ext["contextFileName"], "GEMINI.md")

        # 4. Non-destructive `syntopik attach` on dummy /tmp git repos (Python CLI + Node CLI)
        host_agents = "# Existing Host Repo AGENTS.md — DO NOT MODIFY\n"
        host_claude = "# Existing Host Repo CLAUDE.md — DO NOT MODIFY\n"
        host_gitignore = "*.log\ndist/\n"

        with tempfile.TemporaryDirectory(prefix="synt-v2-attach-") as tmp:
            for mode_name in ("python", "node"):
                repo = Path(tmp) / f"host-{mode_name}"
                repo.mkdir(parents=True)
                self.assertEqual(subprocess.run(["git", "init", "-q"], cwd=str(repo), check=False).returncode, 0)
                subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=str(repo), check=True)
                subprocess.run(["git", "config", "user.name", "Syntopik Test"], cwd=str(repo), check=True)
                (repo / "AGENTS.md").write_text(host_agents, encoding="utf-8")
                (repo / "CLAUDE.md").write_text(host_claude, encoding="utf-8")
                (repo / ".gitignore").write_text(host_gitignore, encoding="utf-8")
                subprocess.run(["git", "add", "."], cwd=str(repo), check=True)
                subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=str(repo), check=True)

                if mode_name == "python":
                    att = cli_mod.attach_workspace(repo, harness="auto", vault="local")
                    self.assertTrue(att["ok"])
                else:
                    p_att = run_cli(["node", str(ROOT / "bin" / "syntopik.mjs"), "attach", str(repo), "--vault", "local", "--json"])
                    self.assertEqual(p_att.returncode, 0, p_att.stderr)
                    self.assertTrue(json.loads(p_att.stdout)["ok"])

                # Host files 100% preserved byte-for-byte & `git status --short` completely EMPTY!
                self.assertEqual((repo / "AGENTS.md").read_text(encoding="utf-8"), host_agents)
                self.assertEqual((repo / "CLAUDE.md").read_text(encoding="utf-8"), host_claude)
                self.assertEqual((repo / ".gitignore").read_text(encoding="utf-8"), host_gitignore)
                st_out = subprocess.check_output(["git", "status", "--short"], cwd=str(repo), text=True)
                self.assertEqual(st_out.strip(), "", f"{mode_name} attach polluted git status: {st_out!r}")
                excl = (repo / ".git" / "info" / "exclude").read_text(encoding="utf-8")
                self.assertIn(".syntopik/", excl)
                self.assertTrue((repo / ".syntopik" / "knowledge-graph" / "propositions.jsonl").is_file())

            # 5. Wave 3A CLI/Attach parity (#172 P0.5 & P1.9): `syntopik.cli sync --json` strict JSON & Node `attach --harness gemini`
            gem_repo = Path(tmp) / "host-gemini-node"
            gem_repo.mkdir(parents=True)
            self.assertEqual(subprocess.run(["git", "init", "-q"], cwd=str(gem_repo), check=False).returncode, 0)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=str(gem_repo), check=True)
            subprocess.run(["git", "config", "user.name", "Syntopik Test"], cwd=str(gem_repo), check=True)
            (gem_repo / "README.md").write_text("# Host\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=str(gem_repo), check=True)
            subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=str(gem_repo), check=True)

            p_gem = run_cli([
                "node", str(ROOT / "bin" / "syntopik.mjs"),
                "attach", str(gem_repo), "--harness", "gemini", "--vault", "local", "--json",
            ])
            self.assertEqual(p_gem.returncode, 0, p_gem.stderr)
            self.assertTrue(json.loads(p_gem.stdout)["ok"])
            gem_settings = gem_repo / ".gemini" / "settings.json"
            self.assertTrue(gem_settings.is_file(), "Node attach --harness gemini did not write .gemini/settings.json")
            self.assertIn("syntopik-studio", json.loads(gem_settings.read_text(encoding="utf-8")).get("mcpServers", {}))
            self.assertEqual(subprocess.check_output(["git", "status", "--short"], cwd=str(gem_repo), text=True).strip(), "")

            sync_ws = gem_repo / ".syntopik"
            p_sync = run_cli([
                sys.executable, "-B", "-m", "syntopik.cli",
                "sync", "--workspace", str(sync_ws), "--json",
            ])
            self.assertEqual(p_sync.returncode, 0, p_sync.stderr)
            sync_parsed = json.loads(p_sync.stdout)
            self.assertTrue(sync_parsed.get("ok"), sync_parsed)

    def test_mcp_stdio_v2_17_tools_ambient_and_parsons_zero_leak(self) -> None:
        server_mjs = ROOT / "mcp-app" / "mlh-studio" / "dist" / "server.mjs"
        self.assertTrue(server_mjs.is_file())

        with tempfile.TemporaryDirectory(prefix="synt-v2-mcp-") as tmp:
            t = Path(tmp)
            ws = t / "ws"  # Deliberately NO ws/scripts/ -> tests PACKAGE_ROOT fallback!
            kg = ws / "knowledge-graph"
            ch = ws / "chapters"
            kg.mkdir(parents=True)
            ch.mkdir(parents=True)
            (kg / "propositions.jsonl").write_text(
                json.dumps({
                    "id": "cs-base-heap",
                    "subject": "CS",
                    "chapter": "Graphs",
                    "title": "Binary min-heap extract-min invariant",
                    "kind": "axiom",
                    "status": "solid",
                    "bktMastery": 0.85,
                    "nextReview": "2020-01-01",
                }) + "\n",
                encoding="utf-8",
            )
            (kg / "chains.jsonl").write_text("", encoding="utf-8")
            (kg / "mistakes.jsonl").write_text("", encoding="utf-8")
            data_dir = t / "mcp-data"

            secret_sol = "SECRET_PARSONS_SOLUTION_9182"
            reqs = [
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},  # Terminal non-UI mode!
                        "clientInfo": {"name": "syntopik-v2-unittest", "version": "2.0"},
                    },
                },
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {
                        "name": "synt_capture_concept",
                        "arguments": {
                            "id": "cs-dijkstra-relax",
                            "title": "Dijkstra greedy edge relaxation invariant",
                            "statement": "With non-negative weights, popped vertex distance is final.",
                            "subject": "CS",
                            "chapter": "Graphs",
                            "cuePrompt": "Why do negative edges break Dijkstra?",
                            "cueAnswer": "A later negative edge can lower an already-popped vertex distance.",
                            "premises": ["cs-base-heap"],
                        },
                    },
                },
                {
                    "jsonrpc": "2.0",
                    "id": 4,
                    "method": "tools/call",
                    "params": {
                        "name": "synt_capture_concept",
                        "arguments": {
                            "title": "Dijkstra greedy edge relaxation invariant",
                            "statement": "Duplicate title capture should deduplicate.",
                            "subject": "CS",
                            "chapter": "Graphs",
                        },
                    },
                },
                {
                    "jsonrpc": "2.0",
                    "id": 5,
                    "method": "tools/call",
                    "params": {
                        "name": "synt_log_review",
                        "arguments": {
                            "propId": "cs-dijkstra-relax",
                            "rating": "good",
                            "confidence": 85,
                            "durationMs": 3200,
                        },
                    },
                },
                {
                    "jsonrpc": "2.0",
                    "id": 6,
                    "method": "tools/call",
                    "params": {
                        "name": "synt_get_frontier",
                        "arguments": {"limit": 10, "compress": True},
                    },
                },
                {
                    "jsonrpc": "2.0",
                    "id": 7,
                    "method": "tools/call",
                    "params": {
                        "name": "synt_practice_card",
                        "arguments": {
                            "action": "pose",
                            "problemId": "parsons-dijkstra-1",
                            "mode": "parsons",
                            "statement": "Order the valid Dijkstra relaxation steps (omit the distractor).",
                            "blocks": [
                                {"id": "blk-pop", "code": "d, u = heappop(pq)", "indent": 0},
                                {"id": "blk-skip", "code": "if d > dist[u]: continue", "indent": 0},
                                {"id": "blk-dist", "code": "if d < dist[u]: break", "indent": 0, "isDistractor": True},
                            ],
                            "canonicalOrder": ["blk-pop", "blk-skip"],
                            "modelSolution": secret_sol,
                        },
                    },
                },
                {
                    "jsonrpc": "2.0",
                    "id": 8,
                    "method": "tools/call",
                    "params": {
                        "name": "synt_practice_card",
                        "arguments": {
                            "action": "conclude",
                            "problemId": "parsons-dijkstra-1",
                            "outcome": "solved",
                            "verdict": "Exact stale-entry guard chosen.",
                            "rung": None,
                        },
                    },
                },
            ]

            proc = run_cli(
                ["node", str(server_mjs), f"--workspace={ws}", f"--data-dir={data_dir}"],
                stdin_text="\n".join(json.dumps(r) for r in reqs) + "\n",
                timeout=15.0,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            by_id = {
                int(m["id"]): m
                for m in (json.loads(ln) for ln in proc.stdout.splitlines() if ln.strip())
                if "id" in m
            }

            # 1. Exact 23 tools & single com.google.antigravity/ui entrypoint ONLY on synt_lesson_studio
            tools = by_id[2]["result"]["tools"]
            self.assertEqual(len(tools), 23)
            tool_names = {tl["name"] for tl in tools}
            for ambient in (
                "synt_capture_concept",
                "synt_get_frontier",
                "synt_log_review",
                "synt_next_move",
                "synt_whiteboard_step",
                "synt_readiness",
                "synt_interleave",
            ):
                self.assertIn(ambient, tool_names)
            ep_tools = [
                tl["name"]
                for tl in tools
                if tl.get("_meta", {}).get("com.google.antigravity/ui", {}).get("entrypoints")
            ]
            self.assertEqual(ep_tools, ["synt_lesson_studio"])

            # 2. synt_capture_concept creates + deduplicates
            cap1 = by_id[3]["result"]
            self.assertFalse(cap1.get("isError", False), cap1)
            self.assertEqual(cap1["structuredContent"]["id"], "cs-dijkstra-relax")
            self.assertTrue(cap1["structuredContent"]["cueAdded"])
            self.assertTrue(cap1["structuredContent"]["chainAdded"])

            cap2 = by_id[4]["result"]
            self.assertFalse(cap2.get("isError", False), cap2)
            self.assertTrue(cap2["structuredContent"]["deduplicated"])

            # 3. synt_log_review persists revlog.jsonl
            rev_r = by_id[5]["result"]
            self.assertFalse(rev_r.get("isError", False), rev_r)
            self.assertTrue((kg / "revlog.jsonl").is_file())

            # 4. synt_get_frontier returns due items & calibration summary
            fr_r = by_id[6]["result"]
            self.assertFalse(fr_r.get("isError", False), fr_r)
            self.assertEqual(fr_r["structuredContent"]["kind"], "synt_frontier")

            # 5. Parsons pose in terminal mode: ZERO leak of modelSolution, canonicalOrder, or isDistractor!
            pose_r = by_id[7]["result"]
            self.assertFalse(pose_r.get("isError", False), pose_r)
            pose_dump = json.dumps(pose_r)
            self.assertNotIn(secret_sol, pose_dump, "Parsons pose leaked modelSolution!")
            self.assertNotIn("canonicalOrder", pose_dump, "Parsons pose leaked canonicalOrder!")
            self.assertNotIn("isDistractor", pose_dump, "Parsons pose leaked isDistractor!")
            self.assertEqual(len(pose_r["structuredContent"]["blocks"]), 3)
            self.assertTrue(pose_r["structuredContent"]["terminalFallback"])

            # 6. Parsons conclude reveals canonicalOrder & modelSolution verbatim
            conc_r = by_id[8]["result"]
            self.assertFalse(conc_r.get("isError", False), conc_r)
            self.assertEqual(conc_r["structuredContent"]["modelSolution"], secret_sol)
            self.assertEqual(conc_r["structuredContent"]["canonicalOrder"], ["blk-pop", "blk-skip"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
