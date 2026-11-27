#!/usr/bin/env python3
"""Syntopik regression suite: Moonshot engines, pocket packs, Syntopik eval and starter packs.

Shared fixtures live in tests/_helpers.py. Run via:
    python3 -B -m unittest discover -s tests -t tests -v
"""

from __future__ import annotations

import http.client
import json
import re
import sys
import tempfile
import threading
import time
import unittest
import unittest.mock
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from _helpers import (
    ROOT,
    SCRIPTS_DIR,
    load_script_module,
    run_cli,
)


class TestMoonshotEnginesAndPocketPacks(unittest.TestCase):
    """25. Syntopik v2.0 Moonshot Engines (Fork Replay, Falsify Arena, Decision Blitz) + Portable Study Packs & Pocket Deck."""

    def test_fork_replay_falsify_arena_and_decision_blitz(self) -> None:
        import syntopik.forks as fk

        fa = load_script_module("falsify_arena.py")
        db = load_script_module("decision_blitz.py")

        with tempfile.TemporaryDirectory(prefix="synt-v2-moonshots-") as tmp:
            ws = Path(tmp) / "ws"
            kg = ws / "knowledge-graph"
            ch = ws / "chapters"
            kg.mkdir(parents=True)
            ch.mkdir(parents=True)

            # Seed propositions & mistakes for arena lemma persistence + blitz build
            seed_props = [
                {"id": "cs-binsearch", "subject": "CS", "chapter": "Search", "title": "Binary search inclusive loop invariant lo <= hi", "status": "shaky", "bktMastery": 0.4},
                {"id": "phys-work-spring", "subject": "Physics", "chapter": "Work-Energy", "title": "Spring restoring work equals minus delta U", "status": "shaky", "bktMastery": 0.4},
                {"id": "phys-work-friction", "subject": "Physics", "chapter": "Work-Energy", "title": "Kinetic friction work sign depends on ground displacement", "status": "shaky", "bktMastery": 0.4},
                {"id": "phys-shm-spring", "subject": "Physics", "chapter": "SHM", "title": "Spring block time period invariant under constant force", "status": "solid", "bktMastery": 0.85},
            ]
            (kg / "propositions.jsonl").write_text("\n".join(json.dumps(p) for p in seed_props) + "\n", encoding="utf-8")
            (kg / "chains.jsonl").write_text("", encoding="utf-8")
            (kg / "mistakes.jsonl").write_text(
                "\n".join(
                    json.dumps(m)
                    for m in [
                        {
                            "id": "m-f1111111", "date": "2026-11-25", "subject": "Physics", "chapter": "Work-Energy",
                            "propId": "phys-work-spring", "category": "sign_error",
                            "triggerCue": "Spring stretched x1 to x2", "wrongMove": "Used +1/2 k (x2^2 - x1^2)",
                            "correctMove": "Use -delta U = 1/2 k (x1^2 - x2^2)", "sourceRef": "test",
                        },
                        {
                            "id": "m-f2222222", "date": "2026-11-25", "subject": "Physics", "chapter": "Work-Energy",
                            "propId": "phys-work-friction", "category": "condition_missed",
                            "triggerCue": "Two stacked blocks with friction", "wrongMove": "Assumed friction work always negative",
                            "correctMove": "Check block displacement relative to ground", "sourceRef": "test",
                        },
                    ]
                ) + "\n",
                encoding="utf-8",
            )

            # 1. Blunder FEN Fork Replay: repeat-blunder refutation WITHOUT leaking winning move + mutation + latency
            win_secret = "1/2*k*(x1**2 - x2**2)"
            cap_f = fk.capture_fork(
                prop_id="phys-work-spring",
                prefix_steps=["F_s(x) = -k*x", "W_s = integral_{x1}^{x2} F_s dx"],
                fork_prompt="Evaluate work done by spring from x1 to x2",
                sealed_blunder="1/2*k*(x2**2 - x1**2)",
                sealed_winning_move=win_secret,
                refutation_msg="Stretching x2 > x1 opposes restoring force, so W_s must be negative!",
                verify_spec={"engine": "math"},
                workspace=ws,
                kg_dir=kg,
            )
            fid = cap_f["id"]
            due_f = fk.list_due_forks(workspace=ws, kg_dir=kg, include_sealed=False)
            self.assertNotIn(win_secret, json.dumps(due_f), "list_due_forks leaked sealedWinningMove!")

            # Repeat exact blunder -> plays refutation WITHOUT leaking winning move!
            chk_blunder = fk.check_fork_move(fid, "0.5*k*(x2^2 - x1^2)", latency_ms=11000, workspace=ws, kg_dir=kg)
            self.assertTrue(chk_blunder["repeatedBlunder"])
            self.assertFalse(chk_blunder["correct"])
            self.assertIn("must be negative", chk_blunder["refutation"])
            self.assertIsNone(chk_blunder["winningMove"])
            self.assertNotIn(win_secret, json.dumps(chk_blunder), "Repeat blunder leaked winning move!")

            # Submit algebraically equivalent winning move faster -> win + Woodpecker latency compression!
            chk_win = fk.check_fork_move(fid, "k*(x1**2 - x2**2)/2", latency_ms=3500, workspace=ws, kg_dir=kg)
            self.assertTrue(chk_win["correct"])
            self.assertEqual(chk_win["winCount"], 1)
            self.assertGreater(chk_win["latencyTrend"]["compressionRatio"], 2.0)

            mut = fk.mutate_fork(fid, seed=7, workspace=ws, kg_dir=kg, include_sealed=False)
            self.assertTrue(mut["ok"])
            self.assertNotIn("sealedWinningMove", mut["variant"])

            # 2. Adversarial Counterexample Arena: kill mutant with ZERO survivor leak + Lakatos lemma repair
            ar = fa.create_arena(
                prop_id="cs-binsearch",
                domain="code",
                claim="Return index of target in sorted list or -1",
                entry="bsearch",
                sealed_reference={
                    "code": (
                        "def bsearch(a, t):\n"
                        "    lo, hi = 0, len(a) - 1\n"
                        "    while lo <= hi:\n"
                        "        m = (lo + hi) // 2\n"
                        "        if a[m] == t: return m\n"
                        "        if a[m] < t: lo = m + 1\n"
                        "        else: hi = m - 1\n"
                        "    return -1\n"
                    )
                },
                sealed_mutants=[
                    {
                        "id": "m-offbyone",
                        "trapTag": "strict_lt_loop",
                        "code": (
                            "def bsearch(a, t):\n"
                            "    lo, hi = 0, len(a) - 1\n"
                            "    while lo < hi:\n"
                            "        m = (lo + hi) // 2\n"
                            "        if a[m] == t: return m\n"
                            "        if a[m] < t: lo = m + 1\n"
                            "        else: hi = m - 1\n"
                            "    return -1\n"
                        ),
                    },
                    {
                        "id": "m-secret-survivor",
                        "trapTag": "SECRET_EMPTY_CRASH_TAG",
                        "code": (
                            "def bsearch(a, t):\n"
                            "    if a[0] == t: return 0\n"
                            "    return -1\n"
                        ),
                    },
                ],
                assumption_predicates=["len(args) == 2"],
                workspace=ws,
                kg_dir=kg,
            )
            aid = ar["id"]
            t1 = fa.test_arena_input(aid, json.dumps({"args": [[5], 5]}), workspace=ws, kg_dir=kg)
            self.assertTrue(t1["validAssumption"], t1)
            self.assertIn("m-offbyone", t1["killed"])
            self.assertEqual(t1["survivedCount"], 1)
            t1_dump = json.dumps(t1)
            self.assertNotIn("m-secret-survivor", t1_dump, "Surviving mutant ID leaked!")
            self.assertNotIn("SECRET_EMPTY_CRASH_TAG", t1_dump, "Surviving mutant trapTag leaked!")
            self.assertNotIn("a[0]", t1_dump, "Surviving mutant code leaked!")

            t2 = fa.test_arena_input(aid, json.dumps({"args": [[], 5]}), workspace=ws, kg_dir=kg)
            self.assertIn("m-secret-survivor", t2["killed"])
            self.assertEqual(t2["survivedCount"], 0)
            self.assertTrue(t2["allKilled"])

            lemma = "Invariant requires inclusive lo <= hi and empty-list guard."
            rep = fa.repair_arena_lemma(aid, lemma, workspace=ws, kg_dir=kg)
            self.assertTrue(rep["ok"])
            self.assertTrue(rep["persistedToProp"])
            reloaded_props = {
                r["id"]: r
                for r in (json.loads(ln) for ln in (kg / "propositions.jsonl").read_text(encoding="utf-8").splitlines() if ln.strip())
            }
            self.assertIn(lemma, reloaded_props["cs-binsearch"].get("boundaryConditions", []))

            # 3. ShadowBox Decision Blitz: sealed build + grade + Newman Tier-1 stats
            blitz = db.build_blitz(n=3, seed=42, workspace=ws, kg_dir=kg, chapters_dir=ch)
            self.assertTrue(blitz["ok"])
            self.assertGreaterEqual(len(blitz["items"]), 2)
            # Sealed key must NOT leak in public blitz payload
            self.assertNotIn("answerKey", json.dumps(blitz))
            first_item = blitz["items"][0]
            self.assertIn("I don't know", json.dumps(first_item))

            # Grade 2 decisions (1 via sealed blitz session, 1 explicit) and verify Newman Tier-1 stats
            sessions = json.loads((kg / "blitz-sessions.json").read_text(encoding="utf-8"))
            sealed_key = sessions[blitz["blitzId"]]["items"][first_item["itemId"]]
            g1 = db.grade_decision(
                blitz_id=blitz["blitzId"],
                item_id=first_item["itemId"],
                kill_cue_answer=sealed_key["killCue"],
                first_move_answer=sealed_key["firstMove"],
                rejected_decoy_answer=sealed_key["rejectedDecoy"],
                decision_ms=11000,
                workspace=ws,
                kg_dir=kg,
            )
            self.assertTrue(g1["ok"])
            self.assertEqual(g1["fieldsHit"], 3)
            self.assertEqual(g1["itemAccuracy"], 1.0)
            self.assertIn("expertLensDiff", g1)

            g2 = db.grade_decision(
                prop_id="phys-work-friction",
                kill_cue_hit=True,
                first_move_hit=True,
                decoy_hit=True,
                decision_ms=15000,
                workspace=ws,
                kg_dir=kg,
            )
            self.assertTrue(g2["ok"])
            stats = db.compute_decision_stats(workspace=ws, kg_dir=kg)
            self.assertEqual(stats["totalDecisions"], 2)
            self.assertAlmostEqual(stats["medianDecisionSec"], 13.0, places=2)
            self.assertEqual(stats["speedAccuracyZone"], "expert_fluent")

            # 4. Security hardening (#172 P0.1): falsify_arena & forks reject RCE + 9**9**8 DoS in < 0.5s with zero execution
            canary = Path(tmp) / "rce_canary.txt"
            math_ar = fa.create_arena(
                prop_id="phys-work-spring",
                domain="math",
                claim="Check polynomial identity",
                sealed_reference={"expr": "x + 1"},
                sealed_mutants=[{"id": "m-bad", "trapTag": "wrong_const", "expr": "x + 2"}],
                workspace=ws,
                kg_dir=kg,
            )
            rce_payload = f"__import__('os').system('touch {canary}')"
            open_payload = f"open({str(canary)!r}, 'w').write('pwn')"
            t0 = time.monotonic()
            for payload in (rce_payload, open_payload, "9**9**8"):
                # Arena math input: must reject cleanly without executing or hanging
                try:
                    ar_res = fa.test_arena_input(math_ar["id"], json.dumps({"x": payload}), workspace=ws, kg_dir=kg)
                    self.assertFalse(ar_res.get("allKilled", False))
                except ValueError:
                    pass
                # Fork math move: must reject cleanly without executing or hanging
                fk_res = fk.check_fork_move(fid, payload, workspace=ws, kg_dir=kg)
                self.assertFalse(fk_res["correct"])
            elapsed_sec = time.monotonic() - t0
            self.assertLess(elapsed_sec, 0.5, f"RCE/DoS guard took too long ({elapsed_sec:.3f}s)")
            self.assertFalse(canary.exists(), "RCE payload executed in falsify_arena or forks!")

    def test_portable_study_packs_security_and_pocket_deck_qr(self) -> None:
        import syntopik.packs as pk
        import syntopik.pocket as po

        with tempfile.TemporaryDirectory(prefix="synt-v2-pack-pocket-") as tmp:
            t = Path(tmp)
            ws_src = t / "ws-src"
            kg_src = ws_src / "knowledge-graph"
            ch_src = ws_src / "chapters"
            kg_src.mkdir(parents=True)
            ch_src.mkdir(parents=True)

            src_props = [
                {
                    "id": "shm-root",
                    "subject": "Physics",
                    "chapter": "SHM",
                    "title": "Linear restoring force F = -kx",
                    "kind": "axiom",
                    "status": "solid",
                    "bktMastery": 0.95,
                    "lapses": 0,
                    "nextReview": "2026-12-20",
                    "lastReviewed": "2026-11-25T10:00:00Z",
                    "notes": "PRIVATE_PERSONAL_NOTE_NEVER_EXPORT",
                    "fsrs": {"stability": 30.0, "difficulty": 2.0, "due": "2026-12-20T10:00:00+00:00"},
                },
                {
                    "id": "shm-energy",
                    "subject": "Physics",
                    "chapter": "SHM",
                    "title": "Total SHM mechanical energy E = 1/2 k A^2",
                    "kind": "derivation",
                    "status": "shaky",
                    "bktMastery": 0.45,
                    "nextReview": "2026-11-20",
                    "fsrs": {"stability": 2.0, "difficulty": 5.5, "due": "2026-11-20T10:00:00+00:00"},
                },
            ]
            (kg_src / "propositions.jsonl").write_text("\n".join(json.dumps(p) for p in src_props) + "\n", encoding="utf-8")
            (kg_src / "chains.jsonl").write_text(
                json.dumps({"from": "shm-root", "to": "shm-energy", "relation": "derives"}) + "\n",
                encoding="utf-8",
            )
            (kg_src / "mistakes.jsonl").write_text("", encoding="utf-8")

            # 1. Export Study Pack -> zero PII fields + valid SHA-256 checksums
            pack_zip = t / "shm.syntopik-pack.zip"
            exp = pk.export_pack("SHM", workspace=ws_src, kg_dir=kg_src, chapters_dir=ch_src, out_path=pack_zip, author="Test")
            self.assertTrue(exp["ok"])
            self.assertTrue(pack_zip.is_file())
            ver = pk.verify_pack(pack_zip)
            self.assertTrue(ver["ok"], ver)

            with zipfile.ZipFile(pack_zip, "r") as zf:
                pack_data_txt = zf.read(pk.DATA_NAME).decode("utf-8")
            self.assertNotIn("PRIVATE_PERSONAL_NOTE_NEVER_EXPORT", pack_data_txt)
            pack_data = json.loads(pack_data_txt)
            for p_item in pack_data["propositions"]:
                for pii_field in ("fsrs", "bktMastery", "nextReview", "lapses", "notes", "lastReviewed"):
                    self.assertNotIn(pii_field, p_item, f"PII field {pii_field!r} leaked into exported pack!")

            # 2. Tamper & stripped-checksum rejection (SHA-256 mismatch AND missing checksums both fail closed!)
            tampered_zip = t / "tampered.syntopik-pack.zip"
            with zipfile.ZipFile(pack_zip, "r") as zin, zipfile.ZipFile(tampered_zip, "w") as zout:
                for info in zin.infolist():
                    data = zin.read(info.filename)
                    if info.filename == pk.DATA_NAME:
                        data = data + b" "
                    zout.writestr(info, data)
            ver_tamper = pk.verify_pack(tampered_zip)
            self.assertFalse(ver_tamper["ok"])

            stripped_zip = t / "stripped-checksums.syntopik-pack.zip"
            with zipfile.ZipFile(pack_zip, "r") as zin, zipfile.ZipFile(stripped_zip, "w") as zout:
                for info in zin.infolist():
                    if info.filename == pk.CHECKSUMS_NAME:
                        continue
                    data = zin.read(info.filename)
                    if info.filename == pk.MANIFEST_NAME:
                        mf = json.loads(data.decode("utf-8"))
                        mf.pop("sha256", None)
                        mf.pop("contentSha256", None)
                        data = json.dumps(mf).encode("utf-8")
                    zout.writestr(info, data)
            ver_stripped = pk.verify_pack(stripped_zip)
            self.assertFalse(ver_stripped["ok"], f"Stripped-checksum ZIP must fail verify_pack: {ver_stripped}")

            # 2b. `export_pack(..., include_exams=True)` excludes personal `*-attempt-*.json` while keeping exam specs
            ex_src_dir = ws_src / "exams"
            ex_src_dir.mkdir(parents=True, exist_ok=True)
            (ex_src_dir / "shm-mock.json").write_text(
                json.dumps({
                    "examSlug": "shm-mock",
                    "title": "SHM Mock",
                    "subject": "Physics",
                    "chapter": "SHM",
                    "questions": [{"id": "Q1", "subject": "Physics", "chapter": "SHM", "stem": "Check T", "answer": "A"}],
                }),
                encoding="utf-8",
            )
            (ex_src_dir / "shm-attempt-20261126T120000Z.json").write_text(
                json.dumps({
                    "examSlug": "shm-mock",
                    "startedAt": "2026-11-26T12:00:00Z",
                    "submittedAt": "2026-11-26T12:10:00Z",
                    "timeSpentSec": 600,
                    "responses": {"Q1": {"answer": "LEARNER_SECRET_RESPONSE"}},
                }),
                encoding="utf-8",
            )
            pack_with_exams = t / "shm-exams.syntopik-pack.zip"
            exp_ex = pk.export_pack(
                "SHM", workspace=ws_src, kg_dir=kg_src, chapters_dir=ch_src,
                out_path=pack_with_exams, include_exams=True,
            )
            self.assertTrue(exp_ex["ok"])
            with zipfile.ZipFile(pack_with_exams, "r") as zf:
                ex_data_txt = zf.read(pk.DATA_NAME).decode("utf-8")
            self.assertNotIn("LEARNER_SECRET_RESPONSE", ex_data_txt)
            self.assertNotIn("shm-attempt-", ex_data_txt)
            self.assertIn("shm-mock", ex_data_txt)

            # 3. Zip-slip rejection
            zipslip_zip = t / "zipslip.syntopik-pack.zip"
            with zipfile.ZipFile(zipslip_zip, "w") as zf:
                zf.writestr("../escape.txt", "pwn")
            self.assertFalse(pk.verify_pack(zipslip_zip)["ok"])

            # 4. POSIX symlink rejection
            symlink_zip = t / "symlink.syntopik-pack.zip"
            with zipfile.ZipFile(symlink_zip, "w") as zf:
                zi = zipfile.ZipInfo("evil-link")
                zi.create_system = 3
                zi.external_attr = (0o120777 << 16)
                zf.writestr(zi, "/etc/passwd")
            self.assertFalse(pk.verify_pack(symlink_zip)["ok"])

            # 5. Non-destructive `suspend-all` import preserves existing `solid` FSRS!
            ws_dst = t / "ws-dst"
            kg_dst = ws_dst / "knowledge-graph"
            ch_dst = ws_dst / "chapters"
            kg_dst.mkdir(parents=True)
            ch_dst.mkdir(parents=True)
            existing_solid = {
                "id": "shm-root",
                "subject": "Physics",
                "chapter": "SHM",
                "title": "Linear restoring force F = -kx",
                "kind": "axiom",
                "status": "solid",
                "bktMastery": 0.96,
                "fsrs": {"stability": 42.0, "difficulty": 1.8, "due": "2027-01-10T00:00:00+00:00"},
            }
            (kg_dst / "propositions.jsonl").write_text(json.dumps(existing_solid) + "\n", encoding="utf-8")
            (kg_dst / "chains.jsonl").write_text("", encoding="utf-8")

            imp = pk.import_pack(pack_zip, workspace=ws_dst, kg_dir=kg_dst, chapters_dir=ch_dst, mode="suspend-all", no_sync=True)
            self.assertTrue(imp["ok"])
            dst_props = {
                r["id"]: r
                for r in (json.loads(ln) for ln in (kg_dst / "propositions.jsonl").read_text(encoding="utf-8").splitlines() if ln.strip())
            }
            self.assertEqual(dst_props["shm-root"]["status"], "solid")
            self.assertEqual(dst_props["shm-root"]["fsrs"]["stability"], 42.0)
            self.assertIn("shm-energy", dst_props)
            self.assertEqual(dst_props["shm-energy"]["status"], "planned")

            # 6. Zero-CDN Offline Pocket HTML + XSS SVG Sanitization + Idempotent Sync (FSRS + FIRe) + Pure-Stdlib QR
            xss_prop = dict(src_props[1])
            xss_prop["svg"] = (
                '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
                '<script>alert("xss")</script>'
                '<image href="x" onerror="window.__PWN__=1"/>'
                '<rect width="50" height="50" fill="#4f46e5"/></svg>'
            )
            (kg_src / "propositions.jsonl").write_text(
                "\n".join(json.dumps(p) for p in [src_props[0], xss_prop]) + "\n",
                encoding="utf-8",
            )
            pocket_html = t / "pocket-review.html"
            p_exp = po.export_pocket_html(workspace=ws_src, out_path=pocket_html, limit=10, kg_dir=kg_src)
            self.assertTrue(p_exp["ok"])
            self.assertTrue(pocket_html.is_file())
            html_txt = pocket_html.read_text(encoding="utf-8")
            self.assertIsNone(re.search(r"<(?:script|link)\b[^>]*\b(?:src|href)\s*=\s*['\"]https?://", html_txt, re.I))
            self.assertIn("shm-energy", html_txt)
            self.assertNotIn("onerror=", html_txt.lower(), "Pocket HTML failed to strip SVG onerror handler!")
            self.assertNotIn('alert("xss")', html_txt, "Pocket HTML failed to strip SVG <script> payload!")
            self.assertIn("data:image/svg+xml", html_txt, "Pocket HTML must render SVG via <img src='data:image/svg+xml,...'>")
            self.assertIn("<img", html_txt)

            sync_payload = {
                "schemaVersion": 1,
                "reviews": [
                    {
                        "reviewId": "pkt-rev-001",
                        "propId": "shm-energy",
                        "rating": "good",
                        "confidence": 0.85,
                        "durationMs": 4000,
                        "ts": datetime.now(timezone.utc).isoformat(),
                    }
                ],
            }
            s1 = po.import_pocket_sync(workspace=ws_src, sync_path=sync_payload, kg_dir=kg_src, no_sync=True)
            self.assertTrue(s1["ok"])
            self.assertEqual(s1["importedCount"], 1)
            self.assertEqual(s1["skippedDuplicateCount"], 0)
            rev_count_1 = len([ln for ln in (kg_src / "revlog.jsonl").read_text(encoding="utf-8").splitlines() if ln.strip()])

            # Re-importing exact same reviewId MUST be idempotent (0 imported, 1 duplicate skipped)
            s2 = po.import_pocket_sync(workspace=ws_src, sync_path=sync_payload, kg_dir=kg_src, no_sync=True)
            self.assertTrue(s2["ok"])
            self.assertEqual(s2["importedCount"], 0)
            self.assertEqual(s2["skippedDuplicateCount"], 1)
            rev_count_2 = len([ln for ln in (kg_src / "revlog.jsonl").read_text(encoding="utf-8").splitlines() if ln.strip()])
            self.assertEqual(rev_count_1, rev_count_2)

            # Pure-stdlib QR encoder (matrix, SVG, ASCII)
            qr_url = "http://192.168.1.42:4173/pocket?pair=demo-token-123"
            mat = po.encode_qr_matrix(qr_url)
            self.assertGreaterEqual(len(mat), 21)
            self.assertEqual(len(mat), len(mat[0]))
            svg = po.render_qr_svg(qr_url)
            self.assertIn("<svg", svg)
            self.assertIn("<rect", svg)
            ascii_qr = po.render_qr_ascii(qr_url)
            self.assertGreaterEqual(len(ascii_qr.splitlines()), 10)


class TestSyntopikEvalAndStarterPacks(unittest.TestCase):
    """32. Syntopik v3.0 Deterministic 60-Day Cognitive Benchmark (7/7 Gates) & 3 Curated Zero-PII Starter Packs."""

    def test_syntopik_eval_deterministic_reproducible_and_all_7_gates_green(self) -> None:
        bench_script = ROOT / "benchmarks" / "syntopik_bench.py"
        self.assertTrue(bench_script.is_file())

        cmd = [sys.executable, "-B", str(bench_script), "--days", "60", "--seed", "42", "--check", "--json"]
        p1 = run_cli(cmd, timeout=20.0)
        self.assertEqual(p1.returncode, 0, f"syntopik_bench run 1 failed: {p1.stderr}")
        p2 = run_cli(cmd, timeout=20.0)
        self.assertEqual(p2.returncode, 0, f"syntopik_bench run 2 failed: {p2.stderr}")

        r1 = json.loads(p1.stdout)
        r2 = json.loads(p2.stdout)
        self.assertTrue(r1["gates"]["ok"], r1["gates"])
        self.assertEqual(r1["gates"]["total"], 7)
        self.assertEqual(r1["gates"]["passed"], 7)

        # Strip wall-clock timing and assert exact determinism across runs
        r1.pop("elapsedMs", None)
        r2.pop("elapsedMs", None)
        self.assertEqual(r1, r2, "Syntopik-Eval benchmark output is not deterministic across repeated runs!")

        # Also verify SVG generation into /tmp produces valid XML SVGs
        with tempfile.TemporaryDirectory(prefix="synt-v3-svg-") as tmp:
            p_svg = run_cli(
                [sys.executable, "-B", str(bench_script), "--days", "60", "--seed", "42", "--svg", "--svg-dir", tmp, "--check", "--json"],
                timeout=20.0,
            )
            self.assertEqual(p_svg.returncode, 0, p_svg.stderr)
            svg_meta = json.loads(p_svg.stdout)["svgOutputs"]
            self.assertTrue(svg_meta["ablation"]["validXml"])
            self.assertTrue(svg_meta["calibration"]["validXml"])
            self.assertTrue((Path(tmp) / "benchmark-ablation.svg").is_file())
            self.assertTrue((Path(tmp) / "benchmark-calibration.svg").is_file())

    def test_all_three_starter_packs_verify_zero_pii_and_roundtrip_import(self) -> None:
        import syntopik.packs as pk

        expected_packs = [
            "raft-consensus.syntopik-pack.zip",
            "shm-physics.syntopik-pack.zip",
            "transformers-attention.syntopik-pack.zip",
        ]
        packs_dir = ROOT / "packs"
        self.assertTrue(packs_dir.is_dir())

        for pack_name in expected_packs:
            pack_path = packs_dir / pack_name
            with self.subTest(pack=pack_name):
                self.assertTrue(pack_path.is_file(), f"Missing bundled starter pack: {pack_path}")

                ver = pk.verify_pack(pack_path)
                self.assertTrue(ver["ok"], f"verify_pack failed for {pack_name}: {ver}")
                self.assertEqual(ver.get("errors") or [], [])
                self.assertEqual(ver.get("unsafePaths") or [], [])
                self.assertEqual(ver.get("mismatches") or [], [])

                ins = pk.inspect_pack(pack_path)
                self.assertTrue(ins["ok"], ins)
                self.assertTrue(ins.get("zeroPiiVerified"), f"PII leaks found in {pack_name}: {ins.get('piiLeaks')}")
                self.assertEqual(ins.get("piiLeaks") or [], [])
                self.assertGreaterEqual(ins["counts"]["propositions"], 8)
                self.assertGreaterEqual(ins["counts"]["chains"], 5)
                self.assertGreaterEqual(ins["counts"]["derivations"], 1)

                with tempfile.TemporaryDirectory(prefix=f"synt-pack-{pack_path.stem}-") as tmp:
                    ws = Path(tmp) / "ws"
                    kg = ws / "knowledge-graph"
                    ch = ws / "chapters"
                    kg.mkdir(parents=True)
                    ch.mkdir(parents=True)

                    imp1 = pk.import_pack(pack_path, workspace=ws, kg_dir=kg, chapters_dir=ch, no_sync=True)
                    self.assertTrue(imp1["ok"], imp1)
                    self.assertGreaterEqual(imp1["addedPropositions"], 8)
                    self.assertGreaterEqual(imp1["addedChains"], 5)
                    self.assertGreaterEqual(imp1["addedDerivations"], 1)
                    self.assertTrue((kg / "propositions.jsonl").is_file())
                    self.assertTrue((kg / "chains.jsonl").is_file())
                    self.assertTrue((kg / "derivations.jsonl").is_file())
                    self.assertTrue(any(ch.glob("*-tracker.json")))

                    # Idempotent re-import skips duplicates cleanly
                    imp2 = pk.import_pack(pack_path, workspace=ws, kg_dir=kg, chapters_dir=ch, no_sync=True)
                    self.assertTrue(imp2["ok"], imp2)
                    self.assertEqual(imp2["addedPropositions"], 0)
                    self.assertGreaterEqual(imp2["skippedExisting"], 8)

    def test_wave4_security_pack_import_sympy_dos_evidence_cli_and_paths(self) -> None:
        """Wave 4C Lock: P0.1, P0.3, P1.1, P1.2, P1.5, P1.6, P2.1."""
        import syntopik.evidence as ev_mod
        import syntopik.forks as forks_mod
        import syntopik.learner_model as lm_mod
        import syntopik.packs as pk_mod
        import syntopik.paths as paths_mod
        import syntopik.work_mentor as wm_mod

        fa_mod = load_script_module("falsify_arena.py")
        df_mod = load_script_module("derivation_forge.py")
        sh_mod = load_script_module("setup_harness.py")

        # --- P0.1: import_pack populates chapters/*.md, forks.jsonl, exams/* & gates code arenas ---
        raft_pack = ROOT / "packs" / "raft-consensus.syntopik-pack.zip"
        shm_pack = ROOT / "packs" / "shm-physics.syntopik-pack.zip"
        with tempfile.TemporaryDirectory(prefix="synt-w4-p01-") as tmp:
            ws = Path(tmp) / "ws"
            kg = ws / "knowledge-graph"
            ch = ws / "chapters"
            kg.mkdir(parents=True)
            ch.mkdir(parents=True)

            res_default = pk_mod.import_pack(raft_pack, workspace=ws, kg_dir=kg, chapters_dir=ch, no_sync=True)
            self.assertTrue(res_default["ok"], res_default)
            self.assertEqual(res_default["addedChapters"], 1)
            self.assertTrue((ch / "raft-consensus.md").is_file())
            self.assertGreaterEqual(res_default["addedForks"], 2)
            self.assertTrue((kg / "forks.jsonl").is_file())
            self.assertGreaterEqual(res_default["addedExams"], 1)
            self.assertTrue(any((ws / "exams").glob("raft-consensus*")))
            # Code-domain arena MUST be skipped by default (allow_code_arenas=False)
            self.assertEqual(res_default["addedArenas"], 0)
            self.assertEqual(res_default["skippedCodeArenas"], 1)
            self.assertIn("arena-raft-uptodate-check", res_default.get("skippedCodeArenaIds") or [])

            # Math-domain arena imports even when allow_code_arenas=False
            res_shm = pk_mod.import_pack(shm_pack, workspace=ws, kg_dir=kg, chapters_dir=ch, no_sync=True)
            self.assertTrue(res_shm["ok"], res_shm)
            self.assertEqual(res_shm["addedArenas"], 1)
            self.assertEqual(res_shm["skippedCodeArenas"], 0)

            # Explicit allow_code_arenas=True imports the Raft code arena
            res_allow = pk_mod.import_pack(
                raft_pack, workspace=ws, kg_dir=kg, chapters_dir=ch, allow_code_arenas=True, no_sync=True
            )
            self.assertTrue(res_allow["ok"], res_allow)
            self.assertEqual(res_allow["addedArenas"], 1)
            self.assertEqual(res_allow["skippedCodeArenas"], 0)
            arena_rows = [json.loads(ln) for ln in (kg / "arenas.jsonl").read_text(encoding="utf-8").splitlines() if ln.strip()]
            self.assertIn("arena-raft-uptodate-check", {r.get("id") for r in arena_rows})

        # --- P0.3: SymPy symbolic-exponent CPU DoS guard across all 3 math evaluators (<0.5s) ---
        dos_cases = [
            ("forks_poly80", lambda: forks_mod._math_equivalent("(x+y+z)**80", "x**80")),
            ("forks_pow1000", lambda: forks_mod._math_equivalent("(x+1)**1000 - x**1000", "1")),
            ("forge_poly80", lambda: df_mod._math_equivalent("(x+y+z)**80", "x**80")),
            ("forge_pow1000", lambda: df_mod._math_equivalent("(x+1)**1000 - x**1000", "1")),
            ("arena_poly80", lambda: fa_mod._try_sympy_eval("x", {"x": "(a+b+c)**80"})),
            ("arena_pow1000", lambda: fa_mod._try_sympy_eval("(x+1)**1000 - x**1000", {"x": 2})),
        ]
        for label, fn in dos_cases:
            with self.subTest(dos_case=label):
                t0 = time.monotonic()
                res_val = fn()
                dt = time.monotonic() - t0
                self.assertLess(dt, 0.5, f"{label} took {dt:.3f}s (exceeds 0.5s DoS ceiling)")
                if label.startswith("arena_"):
                    self.assertFalse(res_val[0], f"{label} should reject/skip DoS payload: {res_val}")
                else:
                    self.assertFalse(res_val, f"{label} should return False for non-equivalent DoS expr")
        # Legitimate low-degree algebraic equivalence still passes
        self.assertTrue(forks_mod._math_equivalent("(x+1)**2", "x**2 + 2*x + 1"))
        self.assertTrue(df_mod._math_equivalent("2*pi*sqrt(m/k)", "sqrt(m/k)*2*pi"))

        # --- P1.1: evidence.py T3 from sealedMutants kill ratio & T4 from Viva outcome/scores.passed ---
        with tempfile.TemporaryDirectory(prefix="synt-w4-p11-") as tmp:
            ws = Path(tmp) / "ws"
            kg = ws / "knowledge-graph"
            kg.mkdir(parents=True)
            (kg / "propositions.jsonl").write_text(
                "\n".join([
                    json.dumps({"id": "p-t3-arena", "subject": "CS", "chapter": "Raft", "title": "Arena Prop", "status": "solid"}),
                    json.dumps({"id": "p-t4-viva", "subject": "CS", "chapter": "Raft", "title": "Viva Prop", "status": "solid"}),
                ]) + "\n",
                encoding="utf-8",
            )
            (kg / "chains.jsonl").write_text("", encoding="utf-8")
            (kg / "arenas.jsonl").write_text(
                json.dumps({
                    "id": "a-p11",
                    "propId": "p-t3-arena",
                    "sealedMutants": [{"id": "m1"}, {"id": "m2"}, {"id": "m3"}, {"id": "m4"}],
                    "killedMutants": ["m1", "m2", "m3"],
                }) + "\n",
                encoding="utf-8",
            )
            (kg / "viva-sessions.jsonl").write_text(
                json.dumps({
                    "id": "v-p11",
                    "propId": "p-t4-viva",
                    "status": "completed",
                    "outcome": "passed",
                    "scores": {"composite": 0.85, "passed": True},
                }) + "\n",
                encoding="utf-8",
            )
            ev_sum = ev_mod.compute_evidence_summary(ws)
            self.assertEqual(ev_sum["propositions"]["p-t3-arena"]["tier"], 3, ev_sum["propositions"]["p-t3-arena"])
            self.assertEqual(ev_sum["propositions"]["p-t4-viva"]["tier"], 4, ev_sum["propositions"]["p-t4-viva"])

        # --- P1.2: CLI board push --content writes frame["body"] & profile override --prose-density terse ---
        with tempfile.TemporaryDirectory(prefix="synt-w4-p12-") as tmp:
            ws = Path(tmp) / "ws"
            kg = ws / "knowledge-graph"
            kg.mkdir(parents=True)
            (kg / "propositions.jsonl").write_text("", encoding="utf-8")
            (kg / "chains.jsonl").write_text("", encoding="utf-8")

            p_wb = run_cli([
                sys.executable, "-B", "-m", "syntopik.cli",
                "board", "push", "--kind", "derivation_step", "--title", "Sign Check",
                "--content", "Keep restoring minus sign", "--workspace", str(ws), "--json",
            ])
            self.assertEqual(p_wb.returncode, 0, p_wb.stderr)
            wb_json = json.loads(p_wb.stdout)
            self.assertEqual(wb_json["frame"]["body"], "Keep restoring minus sign")
            wb_disk = json.loads((kg / "whiteboard-session.json").read_text(encoding="utf-8"))
            self.assertEqual(wb_disk["frames"][-1]["body"], "Keep restoring minus sign")

            p_cal = run_cli([
                sys.executable, "-B", "-m", "syntopik.cli",
                "profile", "calibrate", "--domain", "Physics", "--entry-ramp", "formalism_first",
                "--workspace", str(ws), "--json",
            ])
            self.assertEqual(p_cal.returncode, 0, p_cal.stderr)

            p_ov_txt = run_cli([
                sys.executable, "-B", "-m", "syntopik.cli",
                "profile", "override", "--prose-density", "terse", "--domain", "Physics", "--workspace", str(ws),
            ])
            self.assertEqual(p_ov_txt.returncode, 0, p_ov_txt.stderr)
            self.assertNotIn("Uncalibrated", p_ov_txt.stdout)
            self.assertIn("<= 105w", p_ov_txt.stdout)
            prof_disk = lm_mod.load_profile(kg)
            self.assertEqual(prof_disk["userOverrides"].get("maxProseWords"), 105)
            self.assertEqual(lm_mod.compile_teaching_policy(prof_disk, domain="Physics")["maxProseWords"], 105)

        # --- P1.5: learner_model NaN/Inf guards & ValueError on malformed now ---
        self.assertEqual(lm_mod._clamp(float("nan"), 0.0, 1.0), 0.0)
        self.assertEqual(lm_mod._clamp(float("inf"), 0.0, 1.0), 1.0)
        self.assertEqual(lm_mod._clamp(float("-inf"), 0.0, 1.0), 0.0)
        with tempfile.TemporaryDirectory(prefix="synt-w4-p15-") as tmp:
            kg = Path(tmp) / "kg"
            kg.mkdir(parents=True)
            tel = lm_mod.record_telemetry_event(
                kg,
                domain="Physics",
                prop_id="p-nan",
                correct=True,
                confidence=float("nan"),
                duration_ms=float("inf"),  # type: ignore[arg-type]
                now="2026-11-26T12:00:00Z",
            )
            self.assertTrue(tel["ok"])
            last_ev = tel["profile"]["recentEvents"][-1]
            self.assertIsNone(last_ev["confidence"])
            self.assertIsNone(last_ev["durationMs"])

            with self.assertRaises(ValueError):
                lm_mod.record_telemetry_event(kg, domain="Physics", prop_id="p-bad", correct=True, now="not-a-timestamp")
            with self.assertRaises(ValueError):
                lm_mod.classify_affective_state([], now="not-a-timestamp")

        # --- P1.6: Windows executable lookup returns full suffixed path (.cmd/.exe) ---
        with tempfile.TemporaryDirectory(prefix="synt-w4-p16-") as tmp:
            bin_dir = Path(tmp) / "winbin"
            bin_dir.mkdir(parents=True)
            cmd_path = bin_dir / "synt_fake_cmd.cmd"
            exe_path = bin_dir / "synt_fake_exe.exe"
            cmd_path.write_text("@echo off\n", encoding="utf-8")
            exe_path.write_text("MZ", encoding="utf-8")

            host_path_cls = type(Path())
            for mod_name, mod in (("syntopik.paths", paths_mod), ("setup_harness", sh_mod)):
                with self.subTest(module=mod_name):
                    with unittest.mock.patch.object(mod, "Path", host_path_cls), unittest.mock.patch.object(
                        mod.os, "name", "nt"
                    ):
                        hit_cmd = mod.find_binary("synt_fake_cmd", extra_dirs=[bin_dir])
                        hit_exe = mod.find_binary("synt_fake_exe", extra_dirs=[bin_dir])
                        self.assertEqual(hit_cmd, str(cmd_path))
                        self.assertEqual(hit_exe, str(exe_path))

        # --- P2.1: work_mentor validate_git_ref rejects HEAD:../etc/passwd while allowing HEAD~1..HEAD ---
        for bad_ref in ("HEAD:../etc/passwd", "../etc/passwd", "main:secret.txt", "HEAD --exec=evil"):
            with self.subTest(bad_ref=bad_ref):
                with self.assertRaises(ValueError):
                    wm_mod.validate_git_ref(bad_ref)
        for ok_ref in ("HEAD~1..HEAD", "main...feature", "origin/main..feature/v2.1"):
            with self.subTest(ok_ref=ok_ref):
                self.assertEqual(wm_mod.validate_git_ref(ok_ref), ok_ref)

    def test_wave4_level3_blackout_dossier_jail_and_explorable_sandbox(self) -> None:
        """Wave 4C Lock: P0.4 (server + sync-graph.mjs), P1.4 (explorable.py + sync-graph.mjs + dashboard.html RPN), P2.2."""
        import syntopik.explorable as expl_mod
        import syntopik.server as srv_mod

        def _req(
            port: int,
            method: str,
            path: str,
            *,
            headers: dict[str, str] | None = None,
            body: Any = None,
        ) -> tuple[int, bytes]:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10.0)
            hdrs = dict(headers or {})
            payload: bytes | None = None
            if body is not None:
                payload = json.dumps(body).encode("utf-8")
                hdrs.setdefault("Content-Type", "application/json")
            try:
                conn.request(method, path, body=payload, headers=hdrs)
                resp = conn.getresponse()
                return resp.status, resp.read()
            finally:
                conn.close()

        # --- P1.4 (Python explorable.py) ---
        with self.assertRaises(ValueError):
            expl_mod.compile_expr_to_rpn("9" * 400)
        with self.assertRaises(ValueError):
            expl_mod.compile_explorable_spec({
                "id": "bad-empty-params",
                "title": "Proto Probe",
                "params": [],
                "outputs": [{"label": "Leak", "expr": "constructor + toString"}],
            })
        exact_rpn = expl_mod.compile_expr_to_rpn("x * 2", allowed_vars={"x"})
        hi_curve = expl_mod.sample_explorable_curve({
            "params": [{"name": "x", "default": 1.0}],
            "curve": {"xVar": "x", "xMin": 0.0, "xMax": 1.0, "samples": 100000, "exactRpn": exact_rpn},
        })
        self.assertEqual(len(hi_curve["points"]), 200)
        lo_curve = expl_mod.sample_explorable_curve({
            "params": [{"name": "x", "default": 1.0}],
            "curve": {"xVar": "x", "xMin": 0.0, "xMax": 1.0, "samples": -5, "exactRpn": exact_rpn},
        })
        self.assertEqual(len(lo_curve["points"]), 2)

        # --- P0.4 (server + sync-graph.mjs Level-3 blackout) & P2.2 (dossier out-of-jail 403) ---
        canary_step = "SECRET_STEP_EXPR_4C_9101"
        canary_step_note = "SECRET_STEP_NOTE_4C_9102"
        canary_thm = "SECRET_THEOREM_EXPR_4C_9103"
        canary_thm_note = "SECRET_THEOREM_NOTE_4C_9104"

        with tempfile.TemporaryDirectory(prefix="synt-w4-srv-") as tmp:
            ws = Path(tmp) / "ws"
            kg = ws / "knowledge-graph"
            ch = ws / "chapters"
            kg.mkdir(parents=True)
            ch.mkdir(parents=True)
            (kg / "propositions.jsonl").write_text(
                json.dumps({"id": "p-w4", "subject": "Physics", "chapter": "SHM", "title": "Pendulum", "status": "solid"}) + "\n",
                encoding="utf-8",
            )
            (kg / "chains.jsonl").write_text("", encoding="utf-8")
            (kg / "mistakes.jsonl").write_text("", encoding="utf-8")
            (kg / "derivations.jsonl").write_text(
                json.dumps({
                    "id": "deriv-w4-l3",
                    "propId": "p-w4",
                    "title": "Level-3 Masking Test",
                    "steps": [
                        {"id": "a1", "role": "axiom", "label": "Axiom", "expr": "F = -k*x", "note": "Visible axiom", "dependsOn": []},
                        {"id": "s1", "role": "step", "label": "Mid Step", "expr": canary_step, "note": canary_step_note, "dependsOn": ["a1"]},
                        {"id": "s2", "role": "theorem", "label": "Theorem", "expr": canary_thm, "note": canary_thm_note, "dependsOn": ["s1"]},
                    ],
                }) + "\n",
                encoding="utf-8",
            )
            # Chapter with both malicious prototype :::explore blocks AND valid over-sampled block
            (ch / "sandbox.md").write_text(
                "# Sandbox\n\n"
                ":::explore bad-proto-1\n"
                '{"title":"Bad 1","params":[],"outputs":[{"label":"X","expr":"constructor(1)"}]}\n'
                ":::\n\n"
                ":::explore bad-proto-2\n"
                '{"title":"Bad 2","params":[{"name":"__proto__","min":0,"max":1,"default":0}],"outputs":[{"label":"X","expr":"__proto__ + 1"}]}\n'
                ":::\n\n"
                ":::explore good-lab\n"
                '{"title":"Good Lab","params":[{"name":"x","min":0,"max":10,"default":2}],"outputs":[{"label":"Sq","expr":"x**2"}],"curve":{"xVar":"x","xMin":0,"xMax":10,"samples":9999,"exactExpr":"x**2"}}\n'
                ":::\n",
                encoding="utf-8",
            )

            # (a) sync-graph.mjs Level-3 blackout masking & prototype-safe RPN parser
            gd_out = kg / "graph-data-w4.js"
            sg = run_cli([
                "node", str(SCRIPTS_DIR / "sync-graph.mjs"),
                "--workspace", str(ws), "--kg-dir", str(kg), "--chapters-dir", str(ch), "--out", str(gd_out),
            ])
            self.assertEqual(sg.returncode, 0, sg.stderr)
            gd_text = gd_out.read_text(encoding="utf-8")
            for secret in (canary_step, canary_step_note, canary_thm, canary_thm_note):
                self.assertNotIn(secret, gd_text, f"sync-graph.mjs leaked derivation secret: {secret}")
            hub_proc = run_cli([
                "node", "-e",
                f"globalThis.window={{}}; require({json.dumps(str(gd_out))}); console.log(JSON.stringify(window.__STUDY_HUB__));",
            ])
            self.assertEqual(hub_proc.returncode, 0, hub_proc.stderr)
            hub = json.loads(hub_proc.stdout)
            d_row = hub["derivations"][0]
            self.assertEqual(d_row["blackoutLevel"], 3)
            steps_by_id = {s["id"]: s for s in d_row["steps"]}
            self.assertFalse(steps_by_id["a1"]["hidden"])
            self.assertEqual(steps_by_id["a1"]["expr"], "F = -k*x")
            for hid in ("s1", "s2"):
                self.assertTrue(steps_by_id[hid]["hidden"])
                self.assertIsNone(steps_by_id[hid]["expr"])
                self.assertIsNone(steps_by_id[hid]["note"])

            # Malicious :::explore blocks rejected; valid block compiled & samples clamped to 200
            sb_expl = (hub.get("explorables") or {}).get("sandbox") or []
            self.assertEqual([e.get("id") for e in sb_expl], ["good-lab"])
            self.assertEqual(sb_expl[0]["curve"]["samples"], 200)

            # (b) Live server GET /api/state, GET /api/derivations (Level-3), POST /api/export/dossier jail
            srv = srv_mod.create_server(workspace=ws, host="127.0.0.1", port=0)
            t_srv = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
            t_srv.start()
            try:
                port = srv.bound_port
                auth = {"X-Syntopik-Token": srv.csrf_token}

                for ep in ("/api/state", "/api/derivations"):
                    st_code, raw_b = _req(port, "GET", ep)
                    self.assertEqual(st_code, 200, f"{ep} failed: {raw_b}")
                    raw_s = raw_b.decode("utf-8")
                    for secret in (canary_step, canary_step_note, canary_thm, canary_thm_note):
                        self.assertNotIn(secret, raw_s, f"{ep} leaked derivation secret: {secret}")
                    body_j = json.loads(raw_s)
                    derivs = body_j["derivations"]
                    self.assertEqual(derivs[0]["hiddenCount"], 2)
                    self.assertEqual(derivs[0]["hiddenStepIds"], ["s1", "s2"])
                    st_map = {s["id"]: s for s in derivs[0]["steps"]}
                    self.assertFalse(st_map["a1"]["hidden"])
                    self.assertEqual(st_map["a1"]["expr"], "F = -k*x")
                    self.assertTrue(st_map["s1"]["hidden"] and st_map["s1"]["expr"] is None and st_map["s1"]["note"] is None)
                    self.assertTrue(st_map["s2"]["hidden"] and st_map["s2"]["expr"] is None and st_map["s2"]["note"] is None)

                # P2.2: out-of-jail knowledge-graph/pwn.html MUST return 403 and NOT create file
                st_jail, _ = _req(
                    port, "POST", "/api/export/dossier", headers=auth, body={"outPath": "knowledge-graph/pwn.html"}
                )
                self.assertEqual(st_jail, 403)
                self.assertFalse((kg / "pwn.html").exists())

                # In-jail chapters/ok-dossier.html MUST succeed (200)
                st_ok, raw_ok = _req(
                    port, "POST", "/api/export/dossier", headers=auth, body={"outPath": "chapters/ok-dossier.html"}
                )
                self.assertEqual(st_ok, 200, raw_ok)
                self.assertTrue((ch / "ok-dossier.html").is_file())
            finally:
                srv.shutdown()
                srv.server_close()
                t_srv.join(timeout=3.0)

        # --- P1.4 (Node runtime verification of dashboard.html null-prototype RPN sandbox) ---
        dash_src = (ROOT / "dashboard.html").read_text(encoding="utf-8")
        rpn_start = dash_src.index("const RPN_MAX_OPS = 256;")
        rpn_end = dash_src.index("function ensureCompiledExploreSpec(", rpn_start)
        rpn_snippet = dash_src[rpn_start:rpn_end]
        node_rpn_check = (
            rpn_snippet
            + "\n"
            + "if (Object.getPrototypeOf(RPN_FUNCS) !== null) throw new Error('RPN_FUNCS has prototype');\n"
            + "if (Object.getPrototypeOf(RPN_CONSTS) !== null) throw new Error('RPN_CONSTS has prototype');\n"
            + "if (compileExprToRpnJs('constructor(1)') !== null) throw new Error('constructor call allowed');\n"
            + "if (compileExprToRpnJs('__proto__ + 1') !== null) throw new Error('__proto__ var allowed');\n"
            + "if (validateRpnArray([{op:'call', fn:'constructor', argc:0}]) !== null) throw new Error('raw constructor op allowed');\n"
            + "if (validateRpnArray([{op:'var', name:'__proto__'}]) !== null) throw new Error('raw __proto__ op allowed');\n"
            + "const okOps = compileExprToRpnJs('sin(x)**2 + cos(x)**2');\n"
            + "const val = evaluateRpn(okOps, {x: 1.23});\n"
            + "if (!Number.isFinite(val) || Math.abs(val - 1.0) > 1e-9) throw new Error('valid RPN failed: ' + val);\n"
            + "console.log('RPN_OK');\n"
        )
        rpn_proc = run_cli(["node", "-e", node_rpn_check])
        self.assertEqual(rpn_proc.returncode, 0, f"dashboard.html RPN check failed: {rpn_proc.stderr}")
        self.assertIn("RPN_OK", rpn_proc.stdout)

    def test_wave4_ui_xss_pack_mode_svg_uri_and_static_demo_site_locks(self) -> None:
        """Wave 4C Lock: P0.2, P1.3, P1.7, and docs/{index,viewer,pocket}.html static sandbox gates."""
        dash_src = (ROOT / "dashboard.html").read_text(encoding="utf-8")
        pane_src = (ROOT / "mcp-app" / "mlh-studio" / "src" / "ui" / "studio-pane.mjs").read_text(encoding="utf-8")
        docs_dir = ROOT / "docs"
        index_path = docs_dir / "index.html"
        viewer_path = docs_dir / "viewer.html"
        pocket_path = docs_dir / "pocket.html"
        nojekyll_path = docs_dir / ".nojekyll"

        for p in (index_path, viewer_path, pocket_path, nojekyll_path):
            self.assertTrue(p.is_file(), f"Missing static demo artifact: {p}")

        index_src = index_path.read_text(encoding="utf-8")

        # --- P0.2: Arena res.newlyKilled XSS escape & studio-pane maxWords escape ---
        for label, html_txt in (("dashboard.html", dash_src), ("docs/index.html", index_src)):
            with self.subTest(xss_target=label):
                self.assertIn('esc(res.newlyKilled.join(", "))', html_txt)
                self.assertNotIn('${res.newlyKilled.join(', html_txt)
        self.assertIn("escapeHtml(String(maxWords))", pane_src)

        # --- P1.3: Pack import mode uses 'suspend-all' (never broken 'replace') ---
        for label, html_txt in (("dashboard.html", dash_src), ("docs/index.html", index_src)):
            with self.subTest(pack_mode_target=label):
                self.assertIn('value="suspend-all"', html_txt)
                self.assertNotIn('value="replace"', html_txt)

        # --- P1.7: SVG <image href> URI scheme hardening in dashboard.html & studio-pane.mjs ---
        dash_occ_start = dash_src.index("function isSafeOcclusionDataUri(")
        dash_occ_end = dash_src.index("function renderOcclusionStageHtml(", dash_occ_start)
        dash_occ_js = (
            "function relHref(p) { return './' + p; }\n"
            + dash_src[dash_occ_start:dash_occ_end]
            + "\n"
            + "const png = 'data:image/png;base64,iVBORw0KGgo=';\n"
            + "const badSvg = 'data:image/svg+xml;base64,' + Buffer.from('<svg><script>alert(1)</script></svg>').toString('base64');\n"
            + "const extSvg = 'data:image/svg+xml;base64,' + Buffer.from('<svg><image href=\"https://evil.example/x.png\"/></svg>').toString('base64');\n"
            + "for (const spec of [\n"
            + "  {src: 'https://evil.example/a.png'}, {src: 'http://evil.example/a.png'},\n"
            + "  {src: 'javascript:alert(1)'}, {dataUri: 'javascript:alert(1)'},\n"
            + "  {dataUri: 'https://evil.example/a.png'}, {dataUri: 'data:text/html;base64,PGgxPg=='},\n"
            + "  {dataUri: badSvg}, {dataUri: extSvg}, {src: '../secret.png'}\n"
            + "]) {\n"
            + "  const r = resolveSafeOcclusionHref(spec);\n"
            + "  if (r !== '') throw new Error('dashboard allowed unsafe href: ' + JSON.stringify(spec) + ' -> ' + r);\n"
            + "}\n"
            + "if (resolveSafeOcclusionHref({dataUri: png}) !== png) throw new Error('dashboard rejected valid png dataUri');\n"
            + "if (resolveSafeOcclusionHref({src: 'assets/fig1.png'}) !== './assets/fig1.png') throw new Error('dashboard rejected local asset');\n"
            + "console.log('DASH_SVG_OK');\n"
        )
        p_dash_svg = run_cli(["node", "-e", dash_occ_js])
        self.assertEqual(p_dash_svg.returncode, 0, f"dashboard.html SVG URI check failed: {p_dash_svg.stderr}")
        self.assertIn("DASH_SVG_OK", p_dash_svg.stdout)

        pane_occ_start = pane_src.index("function isSafeOcclusionDataUri(")
        pane_occ_end = pane_src.index("function renderOcclusionWidget(", pane_occ_start)
        pane_occ_js = (
            pane_src[pane_occ_start:pane_occ_end]
            + "\n"
            + "const png = 'data:image/png;base64,iVBORw0KGgo=';\n"
            + "const badSvg = 'data:image/svg+xml;base64,' + Buffer.from('<svg onload=\"alert(1)\"></svg>').toString('base64');\n"
            + "for (const spec of [\n"
            + "  {src: 'https://evil.example/a.png'}, {src: 'http://evil.example/a.png'},\n"
            + "  {src: 'javascript:alert(1)'}, {dataUri: 'javascript:alert(1)'},\n"
            + "  {dataUri: 'https://evil.example/a.png'}, {dataUri: badSvg}\n"
            + "]) {\n"
            + "  const r = resolveSafeOcclusionImageHref(spec);\n"
            + "  if (r !== '') throw new Error('studio-pane allowed unsafe href: ' + JSON.stringify(spec) + ' -> ' + r);\n"
            + "}\n"
            + "if (resolveSafeOcclusionImageHref({dataUri: png}) !== png) throw new Error('studio-pane rejected valid png dataUri');\n"
            + "console.log('PANE_SVG_OK');\n"
        )
        p_pane_svg = run_cli(["node", "-e", pane_occ_js])
        self.assertEqual(p_pane_svg.returncode, 0, f"studio-pane.mjs SVG URI check failed: {p_pane_svg.stderr}")
        self.assertIn("PANE_SVG_OK", p_pane_svg.stdout)

        # --- Static GitHub Pages Demo Site Gates (<= 650 KB, zero CDN, zero user paths, zero eval, Level-3 masking) ---
        max_bytes = 650 * 1024
        ext_cdn_re = re.compile(
            r"""(?:<script\b[^>]*\bsrc\s*=\s*["']https?://|<link\b[^>]*\bhref\s*=\s*["']https?://"""
            r"""|<img\b[^>]*\bsrc\s*=\s*["']https?://|@import\s+(?:url\s*\(\s*)?["']?https?://"""
            r"""|fonts\.googleapis\.com|cdn\.jsdelivr\.net|unpkg\.com|cdnjs\.cloudflare\.com)""",
            re.IGNORECASE,
        )
        user_path_re = re.compile(
            r"(?:/Users/[A-Za-z0-9._-]+|/home/[A-Za-z0-9._-]+|[A-Za-z]:\\Users\\[A-Za-z0-9._-]+)"
        )
        dangerous_js_re = re.compile(r"\b(?:eval\s*\(|new\s+Function\s*\()")
        for doc_file in (index_path, viewer_path, pocket_path):
            with self.subTest(demo_file=doc_file.name):
                size = doc_file.stat().st_size
                self.assertGreater(size, 1000)
                self.assertLessEqual(size, max_bytes, f"{doc_file.name} ({size} B) exceeds 650 KB ceiling ({max_bytes} B)")
                txt = doc_file.read_text(encoding="utf-8")
                self.assertEqual(ext_cdn_re.findall(txt), [], f"{doc_file.name} has external CDN/resource links")
                self.assertEqual(user_path_re.findall(txt), [], f"{doc_file.name} leaks personal home path")
                self.assertEqual(dangerous_js_re.findall(txt), [], f"{doc_file.name} contains eval/new Function")
                for stok in ("sealedWinningMove", "sealedBlunder", "sealedMutants", "sealedReference"):
                    self.assertNotIn(stok, txt, f"{doc_file.name} leaks sealed token {stok!r}")

        # Verify docs/index.html embeds Level-3 masked derivations
        hub_match = re.search(r"window\.__STUDY_HUB__\s*=\s*(\{.*?\});\n</script>", index_src)
        self.assertIsNotNone(hub_match, "docs/index.html missing inline window.__STUDY_HUB__")
        assert hub_match is not None
        demo_hub = json.loads(hub_match.group(1))
        self.assertGreaterEqual(len(demo_hub.get("derivations") or []), 3)
        for dv in demo_hub["derivations"]:
            self.assertEqual(dv.get("blackoutLevel"), 3, f"docs/index.html derivation {dv.get('id')} not Level-3 masked")
            for st in dv.get("steps") or []:
                if st.get("role") != "axiom":
                    self.assertTrue(st.get("hidden"), f"Non-axiom step {st.get('id')} exposed in docs/index.html")
                    self.assertIsNone(st.get("expr"), "Non-axiom expr exposed in docs/index.html")


if __name__ == "__main__":
    unittest.main(verbosity=2)
