#!/usr/bin/env python3
"""Syntopik regression suite: v3.1 Rosetta bridges, CRDT sync, policy replay and proof-diff.

Shared fixtures live in tests/_helpers.py. Run via:
    python3 -B -m unittest discover -s tests -t tests -v
"""

from __future__ import annotations

import http.client
import json
import re
import shutil
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from _helpers import (
    ROOT,
    SCRIPTS_DIR,
    load_script_module,
    run_cli,
)


class TestV31RosettaSyncReplayAndProofDiff(unittest.TestCase):
    """33. Syntopik v3.1 Rosetta Isomorphism Engine, CRDT Delta Sync, Policy Replay & Proof-Graph Handwriting Diff."""

    def test_rosetta_isomorphism_archetypes_fire_credit_and_brainstem(self) -> None:
        import syntopik.brainstem as bs
        import syntopik.fsrs_core as fc
        import syntopik.isomorphism as iso
        import syntopik.learner_model as lm

        # 1. Archetype library schema (>= 10 archetypes with valid breaksAt, variableMap, bijectionQuestion)
        archetypes = iso.load_archetypes()
        self.assertGreaterEqual(len(archetypes), 10)
        for arch in archetypes:
            with self.subTest(archetype=arch.get("id")):
                self.assertTrue(str(arch.get("id") or "").strip())
                self.assertTrue(str(arch.get("name") or "").strip())
                breaks = arch.get("breaksAt")
                self.assertIsInstance(breaks, list)
                self.assertGreaterEqual(len(breaks), 1)
                self.assertTrue(all(isinstance(b, str) and b.strip() for b in breaks))
                self.assertTrue(str(arch.get("bijectionQuestion") or "").strip())
                domains = arch.get("domains")
                self.assertIsInstance(domains, list)
                self.assertGreaterEqual(len(domains), 2)
                for slot in domains:
                    vmap = slot.get("variableMap")
                    self.assertIsInstance(vmap, dict)
                    self.assertGreaterEqual(len(vmap), 2)

        with tempfile.TemporaryDirectory(prefix="synt-v31-rosetta-") as tmp:
            ws = Path(tmp) / "ws"
            kg = ws / "knowledge-graph"
            ch = ws / "chapters"
            kg.mkdir(parents=True)
            ch.mkdir(parents=True)

            seed_props = [
                {
                    "id": "phys-damped-shm",
                    "subject": "Physics",
                    "chapter": "SHM",
                    "title": "Damped Simple Harmonic Motion (SHM) Oscillator",
                    "statement": "Linear restoring force and viscous damping obey m x'' + b x' + k x = 0 with resonance and underdamped/overdamped regimes.",
                    "kind": "theorem",
                    "status": "solid",
                    "bktMastery": 0.92,
                    "nextReview": "2099-01-01",
                },
                {
                    "id": "ee-series-rlc",
                    "subject": "Electrical Engineering",
                    "chapter": "AC Circuits",
                    "title": "Series RLC Resonant Circuit",
                    "statement": "Inductor L, resistor R, and capacitor C obey L d^2q/dt^2 + R dq/dt + q/C = 0 with quality factor and resonant frequency 1/sqrt(LC).",
                    "kind": "theorem",
                    "status": "planned",
                    "bktMastery": 0.10,
                },
                {
                    "id": "ml-attn-softmax",
                    "subject": "Machine Learning",
                    "chapter": "Transformers",
                    "title": "Attention Softmax Temperature & Saturation",
                    "statement": "Scaled attention logits pass through softmax with vanishing Jacobian under saturation and temperature scaling.",
                    "kind": "theorem",
                    "status": "solid",
                    "bktMastery": 0.88,
                    "nextReview": "2099-01-01",
                },
                {
                    "id": "phys-boltzmann",
                    "subject": "Physics",
                    "chapter": "Statistical Mechanics",
                    "title": "Canonical Boltzmann Distribution & Partition Function",
                    "statement": "Thermal equilibrium microstate probability follows exp(-E_i / k_B T) / Z with Helmholtz free energy.",
                    "kind": "theorem",
                    "status": "planned",
                    "bktMastery": 0.20,
                },
                # Distractors that MUST yield zero false-positive chords
                {
                    "id": "sys-raft-rpc-framing",
                    "subject": "Systems",
                    "chapter": "Raft",
                    "title": "Raft Log Append RPC Byte Framing",
                    "statement": "AppendEntries RPC frames prevLogIndex and entries over length-prefixed TCP packets.",
                    "kind": "definition",
                    "status": "solid",
                    "bktMastery": 0.90,
                    "nextReview": "2099-01-01",
                },
                {
                    "id": "ml-kv-cache-bytes",
                    "subject": "Machine Learning",
                    "chapter": "Inference",
                    "title": "KV Cache Byte Footprint Accounting",
                    "statement": "Key-value tensor allocation scales linearly with batch size, context window, and fp16 width.",
                    "kind": "definition",
                    "status": "solid",
                    "bktMastery": 0.85,
                    "nextReview": "2099-01-01",
                },
            ]
            (kg / "propositions.jsonl").write_text(
                "\n".join(json.dumps(p) for p in seed_props) + "\n",
                encoding="utf-8",
            )
            (kg / "chains.jsonl").write_text("", encoding="utf-8")
            (kg / "mistakes.jsonl").write_text("", encoding="utf-8")

            # 2. discover_isomorphisms(): cross-domain chords & zero false positives
            disc = iso.discover_isomorphisms(kg)
            self.assertTrue(disc["ok"])
            by_pair = {frozenset((c["sourceId"], c["targetId"])): c for c in disc["chords"]}
            shm_rlc = by_pair.get(frozenset(("phys-damped-shm", "ee-series-rlc")))
            self.assertIsNotNone(shm_rlc, f"Expected Damped SHM <-> Series RLC chord, got {disc['chords']}")
            assert shm_rlc is not None
            self.assertEqual(shm_rlc["archetypeId"], "second_order_oscillator")
            self.assertTrue(shm_rlc["variableBijection"])
            self.assertTrue(shm_rlc["breaksAt"])

            sm_boltz = by_pair.get(frozenset(("ml-attn-softmax", "phys-boltzmann")))
            self.assertIsNotNone(sm_boltz, f"Expected Softmax <-> Boltzmann chord, got {disc['chords']}")
            assert sm_boltz is not None
            self.assertEqual(sm_boltz["archetypeId"], "boltzmann_softmax")

            distractors = {"sys-raft-rpc-framing", "ml-kv-cache-bytes"}
            for chord in disc["chords"]:
                self.assertNotIn(chord["sourceId"], distractors, f"False-positive chord on distractor: {chord}")
                self.assertNotIn(chord["targetId"], distractors, f"False-positive chord on distractor: {chord}")

            # 3. link_isomorphism_chord(): idempotent across both orientations & persists relation="isomorphic_to", weight=0.25
            link1 = iso.link_isomorphism_chord(kg, "phys-damped-shm", "ee-series-rlc", "second_order_oscillator")
            self.assertTrue(link1["ok"])
            self.assertTrue(link1["created"])
            self.assertFalse(link1["alreadyExists"])
            self.assertEqual(link1["chord"]["relation"], "isomorphic_to")
            self.assertAlmostEqual(link1["chord"]["encompassWeight"], 0.25, places=4)

            link2 = iso.link_isomorphism_chord(kg, "ee-series-rlc", "phys-damped-shm", "second_order_oscillator")
            self.assertTrue(link2["ok"])
            self.assertFalse(link2["created"])
            self.assertTrue(link2["alreadyExists"])

            persisted_chains = [
                json.loads(ln)
                for ln in (kg / "chains.jsonl").read_text(encoding="utf-8").splitlines()
                if ln.strip()
            ]
            self.assertEqual(len(persisted_chains), 1)
            self.assertEqual(persisted_chains[0]["relation"], "isomorphic_to")
            self.assertAlmostEqual(persisted_chains[0]["encompassWeight"], 0.25, places=4)

            # 4. Cross-domain FIRe credit <= 0.30, never clears misconception twin, SRC never knocks out isomorphic chords
            ref_now = datetime(2026, 11, 26, 12, 0, 0, tzinfo=timezone.utc)
            d_14_ago = (ref_now - timedelta(days=14)).date().isoformat()
            fire_props = [
                {
                    "id": "phys-damped-shm",
                    "status": "solid",
                    "fsrs": {"stability": 10.0, "difficulty": 4.0, "due": ref_now.date().isoformat(), "last_review": d_14_ago},
                },
                {
                    "id": "ee-series-rlc",
                    "status": "shaky",
                    "fsrs": {"stability": 3.0, "difficulty": 5.0, "due": d_14_ago, "last_review": d_14_ago},
                },
                {
                    "id": "ctrl-pid-misc",
                    "status": "misconception",
                    "fsrs": {"stability": 2.0, "difficulty": 6.0, "due": d_14_ago, "last_review": d_14_ago},
                },
            ]
            fire_chains = [
                {
                    "premises": ["phys-damped-shm"],
                    "conclusion": "ee-series-rlc",
                    "relation": "isomorphic_to",
                    "encompassWeight": 0.25,
                    "archetypeId": "second_order_oscillator",
                },
                {
                    "premises": ["phys-damped-shm"],
                    "conclusion": "ctrl-pid-misc",
                    "relation": "isomorphic_to",
                    "encompassWeight": 0.90,  # Must be capped <= 0.30 and blocked on misconception
                    "archetypeId": "second_order_oscillator",
                },
            ]
            credits = fc.apply_fire_credit("phys-damped-shm", "good", fire_props, fire_chains, now=ref_now)
            self.assertEqual(credits.isomorphicRefreshed, 1)
            self.assertEqual(credits.refreshed, 1)
            self.assertEqual(len(credits), 1)
            self.assertEqual(credits[0]["propId"], "ee-series-rlc")
            self.assertTrue(credits[0]["isomorphic"])
            self.assertEqual(credits[0]["relation"], "isomorphic_to")
            self.assertGreater(credits[0]["credit"], 0.0)
            self.assertLessEqual(credits[0]["credit"], 0.30)
            self.assertGreater(fire_props[1]["fsrs"]["stability"], 3.0)
            # Misconception twin MUST remain untouched
            self.assertEqual(fire_props[2]["status"], "misconception")
            self.assertEqual(fire_props[2]["fsrs"]["stability"], 2.0)

            # SRC compress_due_queue MUST NOT knock out isomorphic chords
            due_items = [
                {"id": "phys-damped-shm", "status": "solid"},
                {"id": "ee-series-rlc", "status": "shaky"},
                {"id": "ctrl-pid-misc", "status": "misconception"},
            ]
            comp = fc.compress_due_queue(due_items, fire_chains, min_knockouts=1)
            self.assertEqual(comp["knockedOutCount"], 0)
            self.assertEqual(comp["knockedOut"], [])
            self.assertEqual(len(comp["selected"]), 3)

            # 5. scripts/graph_intel.py --rosetta --json returns clusters, warnings & transfer opportunities
            gi_proc = run_cli([
                sys.executable, "-B", str(SCRIPTS_DIR / "graph_intel.py"),
                "--kg-dir", str(kg), "--rosetta", "--json",
            ])
            self.assertEqual(gi_proc.returncode, 0, f"graph_intel --rosetta failed: {gi_proc.stderr}")
            gi_data = json.loads(gi_proc.stdout)
            ros = gi_data["rosetta"]
            self.assertTrue(ros["ok"])
            cluster_ids = {c["archetypeId"] for c in ros["clusters"]}
            self.assertIn("second_order_oscillator", cluster_ids)
            self.assertIn("boltzmann_softmax", cluster_ids)
            self.assertGreaterEqual(len(ros["negativeTransferWarnings"]), 2)
            self.assertGreaterEqual(len(ros["transferOpportunities"]), 2)

            # 6. brainstem.plan_next_move() attaches isomorphicBridge & enforces <= 180-token envelope
            import syntopik.evidence as ev_mod

            ev_mod.record_evidence(ws, "phys-damped-shm", 3, "derivation_blackout", detail={"verified": True})
            prof_ros = lm.load_profile(kg)
            lm.apply_tier1_calibration(
                prof_ros,
                entry_ramp="balanced",
                challenge_appetite="balanced",
                prose_density="standard",
            )
            lm.save_profile(kg, prof_ros)
            lm.record_telemetry_event(
                kg,
                domain="Physics",
                prop_id="phys-damped-shm",
                correct=True,
                confidence=0.90,
                duration_ms=10000,
            )
            env = bs.plan_next_move(ws)
            self.assertTrue(env["ok"])
            self.assertEqual(env["recommendedMode"], "teach_frontier")
            self.assertEqual(env["targetNodeId"], "ee-series-rlc")
            bridge = env.get("isomorphicBridge")
            self.assertIsInstance(bridge, dict, f"Expected isomorphicBridge in envelope: {env}")
            assert isinstance(bridge, dict)
            self.assertEqual(bridge["knownPropId"], "phys-damped-shm")
            self.assertTrue(bridge.get("archetypeName"))
            self.assertTrue(bridge.get("breaksAt"))
            self.assertTrue(bridge.get("openerPrompt"))
            tok_count = len(re.findall(r"\S+", json.dumps(env, ensure_ascii=False)))
            self.assertLessEqual(tok_count, 180, f"Action Envelope exceeded 180 tokens ({tok_count}): {env}")

    def test_crdt_multi_device_delta_sync_monotonic_commutative_idempotent_and_tamper_proof(self) -> None:
        import copy
        import syntopik.evidence as ev_mod
        import syntopik.learner_model as lm
        import syntopik.sync_delta as sd

        with tempfile.TemporaryDirectory(prefix="synt-v31-sync-") as tmp:
            t = Path(tmp)
            dev_a = t / "device-a"
            dev_b = t / "device-b"
            for ws_root in (dev_a, dev_b):
                (ws_root / "knowledge-graph").mkdir(parents=True)
                (ws_root / "chapters").mkdir(parents=True)

            kg_a, ch_a = dev_a / "knowledge-graph", dev_a / "chapters"
            kg_b, ch_b = dev_b / "knowledge-graph", dev_b / "chapters"

            # Divergent + overlapping propositions
            (kg_a / "propositions.jsonl").write_text(
                "\n".join([
                    json.dumps({
                        "id": "p-shared",
                        "subject": "Physics",
                        "chapter": "SHM",
                        "title": "Restoring Force Law",
                        "status": "solid",
                        "reps": 4,
                        "lastReviewed": "2026-11-24",
                        "updatedAt": "2026-11-24T10:00:00Z",
                        "tags": ["shm", "core"],
                        "cues": [{"prompt": "Why negative sign?", "a": "Always opposes displacement"}],
                        "boundaryConditions": ["small amplitude"],
                        "antiPatterns": ["sign flip"],
                    }),
                    json.dumps({
                        "id": "p-only-a",
                        "subject": "Physics",
                        "chapter": "SHM",
                        "title": "Energy Conservation in SHM",
                        "status": "solid",
                        "reps": 2,
                        "updatedAt": "2026-11-24T11:00:00Z",
                    }),
                ]) + "\n",
                encoding="utf-8",
            )
            (kg_b / "propositions.jsonl").write_text(
                "\n".join([
                    json.dumps({
                        "id": "p-shared",
                        "subject": "Physics",
                        "chapter": "SHM",
                        "title": "Restoring Force Law",
                        "status": "shaky",
                        "reps": 2,
                        "lastReviewed": "2026-11-23",
                        "updatedAt": "2026-11-23T09:00:00Z",
                        "tags": ["physics", "core"],
                        "cues": [
                            {"prompt": "Why negative sign?", "a": "Always opposes displacement"},
                            {"prompt": "Equilibrium position?", "a": "Net force zero"},
                        ],
                        "boundaryConditions": ["linear spring"],
                        "antiPatterns": ["unit trap"],
                    }),
                    json.dumps({
                        "id": "p-only-b",
                        "subject": "Maths",
                        "chapter": "ODEs",
                        "title": "Characteristic Roots",
                        "status": "shaky",
                        "reps": 1,
                        "updatedAt": "2026-11-24T12:00:00Z",
                    }),
                ]) + "\n",
                encoding="utf-8",
            )

            # Overlapping + disjoint chains
            shared_chain = {"premises": ["p-shared"], "conclusion": "p-only-a", "relation": "derives", "encompassWeight": 0.6}
            (kg_a / "chains.jsonl").write_text(json.dumps(shared_chain) + "\n", encoding="utf-8")
            (kg_b / "chains.jsonl").write_text(
                "\n".join([
                    json.dumps(shared_chain),
                    json.dumps({"premises": ["p-only-b"], "conclusion": "p-shared", "relation": "supports", "encompassWeight": 0.4}),
                ]) + "\n",
                encoding="utf-8",
            )

            # Overlapping + disjoint revlog & mistakes
            shared_rev = {"propId": "p-shared", "ts": "2026-11-20T09:00:00Z", "rating": "good", "correct": True, "learnerConfidence": 0.85, "durationMs": 9000}
            (kg_a / "revlog.jsonl").write_text(
                "\n".join([
                    json.dumps(shared_rev),
                    json.dumps({"propId": "p-only-a", "ts": "2026-11-21T09:00:00Z", "rating": "good", "correct": True, "learnerConfidence": 0.90, "durationMs": 8000}),
                ]) + "\n",
                encoding="utf-8",
            )
            (kg_b / "revlog.jsonl").write_text(
                "\n".join([
                    json.dumps(shared_rev),
                    json.dumps({"propId": "p-only-b", "ts": "2026-11-22T09:00:00Z", "rating": "again", "correct": False, "learnerConfidence": 0.70, "durationMs": 14000}),
                ]) + "\n",
                encoding="utf-8",
            )

            shared_mist = {"id": "m-shared01", "date": "2026-11-20", "subject": "Physics", "chapter": "SHM", "category": "sign_error", "triggerCue": "spring", "wrongMove": "+kx", "correctMove": "-kx"}
            (kg_a / "mistakes.jsonl").write_text(
                "\n".join([
                    json.dumps(shared_mist),
                    json.dumps({"id": "m-onlya001", "date": "2026-11-21", "subject": "Physics", "chapter": "SHM", "category": "unit_trap", "triggerCue": "cm", "wrongMove": "cm", "correctMove": "m"}),
                ]) + "\n",
                encoding="utf-8",
            )
            (kg_b / "mistakes.jsonl").write_text(
                "\n".join([
                    json.dumps(shared_mist),
                    json.dumps({"id": "m-onlyb002", "date": "2026-11-22", "subject": "Maths", "chapter": "ODEs", "category": "algebra_slip", "triggerCue": "roots", "wrongMove": "real", "correctMove": "complex"}),
                ]) + "\n",
                encoding="utf-8",
            )

            # Evidence ledger chains (overlapping + disjoint)
            ev_mod.record_evidence(dev_a, "p-shared", 2, "review", detail={"ok": True})
            shutil.copy2(kg_a / "evidence-ledger.jsonl", kg_b / "evidence-ledger.jsonl")
            ev_mod.record_evidence(dev_a, "p-only-a", 3, "fork_replay", detail={"win": True})
            ev_mod.record_evidence(dev_b, "p-only-b", 2, "quiz", detail={"ok": True})

            # Monotonic join-semilattice trackers: A has t1=mastered/q1=solved, B has t1=unseen/q1=unseen!
            (ch_a / "shm-tracker.json").write_text(
                json.dumps({
                    "schemaVersion": 1,
                    "slug": "shm",
                    "title": "Simple Harmonic Motion",
                    "subject": "Physics",
                    "active": True,
                    "createdAt": "2026-11-18T08:00:00Z",
                    "updatedAt": "2026-11-24T10:00:00Z",
                    "topics": [
                        {"id": "t1", "order": 1, "title": "Restoring Law", "status": "mastered"},
                        {"id": "t2", "order": 2, "title": "Energy", "status": "unseen"},
                    ],
                    "questions": [
                        {"qid": "q1", "topicId": "t1", "status": "solved", "attempts": 2, "lastDate": "2026-11-24"},
                        {"qid": "q2", "topicId": "t2", "status": "unseen", "attempts": 0},
                    ],
                    "milestones": {"sourcesIndexed": True, "probed": False},
                }),
                encoding="utf-8",
            )
            (ch_b / "shm-tracker.json").write_text(
                json.dumps({
                    "schemaVersion": 1,
                    "slug": "shm",
                    "title": "Simple Harmonic Motion",
                    "subject": "Physics",
                    "active": True,
                    "createdAt": "2026-11-19T08:00:00Z",
                    "updatedAt": "2026-11-24T12:00:00Z",
                    "topics": [
                        {"id": "t1", "order": 1, "title": "Restoring Law", "status": "unseen"},
                        {"id": "t2", "order": 2, "title": "Energy", "status": "practiced"},
                    ],
                    "questions": [
                        {"qid": "q1", "topicId": "t1", "status": "unseen", "attempts": 0},
                        {"qid": "q2", "topicId": "t2", "status": "retest", "attempts": 1, "lastDate": "2026-11-23"},
                    ],
                    "milestones": {"sourcesIndexed": False, "probed": True},
                }),
                encoding="utf-8",
            )

            # Divergent learner profiles (A calibrated + Physics events; B uncalibrated + Physics & Maths events)
            prof_a = lm.load_profile(kg_a)
            lm.apply_tier1_calibration(prof_a, entry_ramp="formalism_first", challenge_appetite="balanced", prose_density="terse")
            lm.save_profile(kg_a, prof_a)
            lm.record_telemetry_event(kg_a, domain="Physics", prop_id="p-shared", correct=True, confidence=0.85, duration_ms=9000, now="2026-11-20T09:00:00Z")
            lm.record_telemetry_event(kg_a, domain="Physics", prop_id="p-only-a", correct=True, confidence=0.90, duration_ms=8000, now="2026-11-21T09:00:00Z")

            lm.record_telemetry_event(kg_b, domain="Physics", prop_id="p-shared", correct=True, confidence=0.80, duration_ms=11000, now="2026-11-22T08:00:00Z")
            lm.record_telemetry_event(kg_b, domain="Maths", prop_id="p-only-b", correct=False, confidence=0.70, duration_ms=14000, now="2026-11-22T09:00:00Z")

            # Export & verify both deltas
            delta_a_path = t / "delta-a.json"
            delta_b_path = t / "delta-b.json"
            exp_a = sd.export_sync_delta(dev_a, out_path=delta_a_path, device_id="device-alpha-01")
            exp_b = sd.export_sync_delta(dev_b, out_path=delta_b_path, device_id="device-beta-02")
            self.assertTrue(exp_a["ok"] and exp_b["ok"])
            self.assertTrue(sd.verify_sync_delta(delta_a_path)["ok"])
            self.assertTrue(sd.verify_sync_delta(delta_b_path)["ok"])

            # Fail-closed security checks: tampered hash, missing hash, ../escape tracker slug, oversized payload
            tampered = copy.deepcopy(exp_a["delta"])
            tampered["payload"]["propositions"][0]["title"] = "TAMPERED_TITLE"
            v_tamp = sd.verify_sync_delta(tampered)
            self.assertFalse(v_tamp["ok"])
            self.assertTrue(any("contentSha256" in e for e in v_tamp["errors"]))
            m_tamp = sd.merge_sync_delta(dev_b, tampered)
            self.assertFalse(m_tamp["ok"])

            missing_hash = copy.deepcopy(exp_a["delta"])
            missing_hash.pop("contentSha256", None)
            v_miss = sd.verify_sync_delta(missing_hash)
            self.assertFalse(v_miss["ok"])

            bad_slug = copy.deepcopy(exp_a["delta"])
            bad_slug["payload"]["trackers"]["../escape"] = {"slug": "../escape", "topics": []}
            bad_slug["contentSha256"] = sd._compute_delta_sha256(bad_slug)
            v_slug = sd.verify_sync_delta(bad_slug)
            self.assertFalse(v_slug["ok"])
            self.assertTrue(any("slug" in e.lower() for e in v_slug["errors"]))
            m_slug = sd.merge_sync_delta(dev_b, bad_slug)
            self.assertFalse(m_slug["ok"])
            self.assertFalse((dev_b / "escape-tracker.json").exists())

            oversized_path = t / "oversized-delta.json"
            oversized_path.write_bytes(b" " * (sd.MAX_DELTA_BYTES + 128))
            v_over = sd.verify_sync_delta(oversized_path)
            self.assertFalse(v_over["ok"])
            self.assertTrue(any("MAX_DELTA_BYTES" in e for e in v_over["errors"]))

            # Commutative merge: ws_ab = A (+) B, ws_ba = B (+) A
            ws_ab = t / "ws-ab"
            ws_ba = t / "ws-ba"
            shutil.copytree(dev_a, ws_ab)
            shutil.copytree(dev_b, ws_ba)

            m_ab = sd.merge_sync_delta(ws_ab, delta_b_path)
            m_ba = sd.merge_sync_delta(ws_ba, delta_a_path)
            self.assertTrue(m_ab["ok"], m_ab)
            self.assertTrue(m_ba["ok"], m_ba)
            self.assertGreater(m_ab["added"], 0)
            self.assertGreater(m_ba["added"], 0)

            # Monotonicity: mastered/solved NEVER regressed, milestones ORed
            for ws_m in (ws_ab, ws_ba):
                trk = json.loads((ws_m / "chapters" / "shm-tracker.json").read_text(encoding="utf-8"))
                topics_by_id = {x["id"]: x for x in trk["topics"]}
                qs_by_id = {x["qid"]: x for x in trk["questions"]}
                self.assertEqual(topics_by_id["t1"]["status"], "mastered")
                self.assertEqual(topics_by_id["t2"]["status"], "practiced")
                self.assertEqual(qs_by_id["q1"]["status"], "solved")
                self.assertEqual(qs_by_id["q2"]["status"], "retest")
                self.assertEqual(trk["milestones"], {"probed": True, "sourcesIndexed": True})

                props_m = {
                    r["id"]: r
                    for r in (json.loads(ln) for ln in (ws_m / "knowledge-graph" / "propositions.jsonl").read_text(encoding="utf-8").splitlines() if ln.strip())
                }
                self.assertEqual(set(props_m.keys()), {"p-shared", "p-only-a", "p-only-b"})
                self.assertEqual(props_m["p-shared"]["reps"], 4)
                self.assertEqual(set(props_m["p-shared"]["tags"]), {"core", "physics", "shm"})
                self.assertEqual(len(props_m["p-shared"]["cues"]), 2)
                self.assertEqual(set(props_m["p-shared"]["boundaryConditions"]), {"linear spring", "small amplitude"})
                self.assertEqual(set(props_m["p-shared"]["antiPatterns"]), {"sign flip", "unit trap"})

            # Exact Commutativity (A (+) B == B (+) A) across ledgers, propositions, chains, trackers & profile
            for rel_file in (
                "knowledge-graph/propositions.jsonl",
                "knowledge-graph/chains.jsonl",
                "knowledge-graph/revlog.jsonl",
                "knowledge-graph/mistakes.jsonl",
                "knowledge-graph/evidence-ledger.jsonl",
                "chapters/shm-tracker.json",
            ):
                with self.subTest(commutativity_file=rel_file):
                    self.assertEqual(
                        (ws_ab / rel_file).read_text(encoding="utf-8"),
                        (ws_ba / rel_file).read_text(encoding="utf-8"),
                        f"Commutativity mismatch in {rel_file}",
                    )
            prof_ab = json.loads((ws_ab / "knowledge-graph" / "learner-profile.json").read_text(encoding="utf-8"))
            prof_ba = json.loads((ws_ba / "knowledge-graph" / "learner-profile.json").read_text(encoding="utf-8"))
            prof_ab.pop("updatedAt", None)
            prof_ba.pop("updatedAt", None)
            self.assertEqual(prof_ab, prof_ba, "Merged learner-profile.json not commutative")
            self.assertTrue(prof_ab["calibration"]["completed"])
            self.assertEqual(prof_ab["domains"]["Physics"]["n"], 3)
            self.assertEqual(prof_ab["domains"]["Maths"]["n"], 1)

            # Exact Idempotence on second merge (added == 0, updated == 0)
            m_ab_2 = sd.merge_sync_delta(ws_ab, delta_b_path)
            self.assertTrue(m_ab_2["ok"])
            self.assertEqual(m_ab_2["added"], 0)
            self.assertEqual(m_ab_2["updated"], 0)
            self.assertTrue(m_ab_2["idempotentNoop"])

            # Merged evidence-ledger.jsonl passes SHA-256 chain verification
            ev_check_ab = ev_mod.verify_ledger_chain(ws_ab / "knowledge-graph")
            ev_check_ba = ev_mod.verify_ledger_chain(ws_ba / "knowledge-graph")
            self.assertTrue(ev_check_ab["ok"], ev_check_ab)
            self.assertTrue(ev_check_ba["ok"], ev_check_ba)
            self.assertEqual(ev_check_ab["count"], 3)
            self.assertEqual(ev_check_ab["headHash"], ev_check_ba["headHash"])

    def test_longitudinal_telemetry_replay_and_bounded_hyperparam_adoption(self) -> None:
        import syntopik.learner_model as lm
        import syntopik.sim_eval as se

        # 1. Strict cognitive bounds enforcement in learner_model.resolve_calibrated_hyperparams
        self.assertEqual(lm.resolve_calibrated_hyperparams(None), lm.DEFAULT_CALIBRATED_HYPERPARAMS)
        hi_clamped = lm.resolve_calibrated_hyperparams({
            "calibratedHyperparams": {
                "eloKBase": 999.0,
                "erNoviceCutoff": 0.95,
                "erChallengeCutoff": 0.99,
                "fatigueMultiplier": 5.0,
            }
        })
        self.assertEqual(hi_clamped["eloKBase"], 96.0)
        self.assertEqual(hi_clamped["erNoviceCutoff"], 0.45)
        self.assertEqual(hi_clamped["erChallengeCutoff"], 0.88)
        self.assertEqual(hi_clamped["fatigueMultiplier"], 1.50)

        lo_nan_clamped = lm.resolve_calibrated_hyperparams({
            "calibratedHyperparams": {
                "eloKBase": -10.0,
                "erNoviceCutoff": float("nan"),
                "erChallengeCutoff": 0.10,
                "fatigueMultiplier": float("inf"),
            }
        })
        self.assertEqual(lo_nan_clamped["eloKBase"], 32.0)
        self.assertEqual(lo_nan_clamped["erNoviceCutoff"], lm.DEFAULT_CALIBRATED_HYPERPARAMS["erNoviceCutoff"])
        self.assertEqual(lo_nan_clamped["erChallengeCutoff"], 0.72)
        self.assertEqual(lo_nan_clamped["fatigueMultiplier"], lm.DEFAULT_CALIBRATED_HYPERPARAMS["fatigueMultiplier"])

        bench_script = ROOT / "benchmarks" / "syntopik_bench.py"
        base_dt = datetime(2026, 10, 1, 9, 0, 0, tzinfo=timezone.utc)

        with tempfile.TemporaryDirectory(prefix="synt-v31-replay-") as tmp:
            t = Path(tmp)

            # 2. Insufficient data (5 events) -> {ok: True, insufficientData: True, applied: False}
            ws_small = t / "ws-small"
            kg_small = ws_small / "knowledge-graph"
            kg_small.mkdir(parents=True)
            small_rows = [
                {
                    "propId": f"p-{i}",
                    "domain": "Physics",
                    "ts": (base_dt + timedelta(days=i)).isoformat().replace("+00:00", "Z"),
                    "rating": "good",
                    "correct": True,
                    "learnerConfidence": 0.85,
                    "difficulty": 1500,
                    "hintsUsed": 0,
                    "durationMs": 10000,
                }
                for i in range(5)
            ]
            (kg_small / "revlog.jsonl").write_text("\n".join(json.dumps(r) for r in small_rows) + "\n", encoding="utf-8")
            res_small = se.replay_learner_history(ws_small, apply_if_improved=True)
            self.assertTrue(res_small["ok"])
            self.assertTrue(res_small["insufficientData"])
            self.assertEqual(res_small["eventCount"], 5)
            self.assertFalse(res_small["applied"])

            # 3. 30 chronological improving events via CLI -> 70/30 split, OOS gain >= 0.005, bounded adoption
            ws_imp = t / "ws-improving"
            kg_imp = ws_imp / "knowledge-graph"
            kg_imp.mkdir(parents=True)
            imp_rows = [
                {
                    "propId": f"p-imp-{i % 6}",
                    "domain": "Physics",
                    "ts": (base_dt + timedelta(days=i)).isoformat().replace("+00:00", "Z"),
                    "rating": "good",
                    "correct": True,
                    "learnerConfidence": 0.92,
                    "difficulty": 1680,
                    "hintsUsed": 0,
                    "durationMs": 9000,
                }
                for i in range(30)
            ]
            (kg_imp / "revlog.jsonl").write_text("\n".join(json.dumps(r) for r in imp_rows) + "\n", encoding="utf-8")
            p_imp = run_cli([
                sys.executable, "-B", str(bench_script),
                "--replay", "--workspace", str(ws_imp), "--apply", "--json",
            ])
            self.assertEqual(p_imp.returncode, 0, f"bench --replay improving failed: {p_imp.stderr}")
            r_imp = json.loads(p_imp.stdout)
            self.assertTrue(r_imp["ok"])
            self.assertFalse(r_imp["insufficientData"])
            self.assertEqual(r_imp["split"]["method"], "chronological_time_series_70_30")
            self.assertEqual(r_imp["split"]["trainEvents"], 21)
            self.assertEqual(r_imp["split"]["testEvents"], 9)
            self.assertTrue(r_imp["improved"], r_imp)
            self.assertGreaterEqual(r_imp["oosImprovement"], 0.005)
            self.assertTrue(r_imp["applied"])

            prof_imp = lm.load_profile(kg_imp)
            cal_hp = prof_imp.get("calibratedHyperparams")
            self.assertIsInstance(cal_hp, dict)
            resolved_hp = lm.resolve_calibrated_hyperparams(prof_imp)
            for key, (lo, hi) in lm.CALIBRATED_HYPERPARAM_BOUNDS.items():
                self.assertGreaterEqual(resolved_hp[key], lo)
                self.assertLessEqual(resolved_hp[key], hi)
                self.assertAlmostEqual(resolved_hp[key], r_imp["candidateParams"][key], places=4)

            # 4. 30 chronological regime-shift events -> non-improving OOS shift MUST be rejected (applied=False)
            ws_shift = t / "ws-shift"
            kg_shift = ws_shift / "knowledge-graph"
            kg_shift.mkdir(parents=True)
            shift_rows = []
            for i in range(30):
                if i < 21:
                    shift_rows.append({
                        "propId": f"p-sh-{i % 5}",
                        "domain": "Physics",
                        "ts": (base_dt + timedelta(days=i)).isoformat().replace("+00:00", "Z"),
                        "rating": "good",
                        "correct": True,
                        "learnerConfidence": 0.95,
                        "difficulty": 1680,
                        "hintsUsed": 0,
                        "durationMs": 8500,
                    })
                else:
                    shift_rows.append({
                        "propId": f"p-sh-{i % 5}",
                        "domain": "Physics",
                        "ts": (base_dt + timedelta(days=i)).isoformat().replace("+00:00", "Z"),
                        "rating": "again",
                        "correct": False,
                        "learnerConfidence": 0.85,
                        "difficulty": 1680,
                        "hintsUsed": 3,
                        "durationMs": 45000,
                    })
            (kg_shift / "revlog.jsonl").write_text("\n".join(json.dumps(r) for r in shift_rows) + "\n", encoding="utf-8")
            p_shift = run_cli([
                sys.executable, "-B", str(bench_script),
                "--replay", "--workspace", str(ws_shift), "--apply", "--json",
            ])
            self.assertEqual(p_shift.returncode, 0, f"bench --replay shift failed: {p_shift.stderr}")
            r_shift = json.loads(p_shift.stdout)
            self.assertTrue(r_shift["ok"])
            self.assertFalse(r_shift["insufficientData"])
            self.assertFalse(r_shift["improved"], r_shift)
            self.assertLess(r_shift["oosImprovement"], 0.005)
            self.assertFalse(r_shift["applied"])
            self.assertNotIn("calibratedHyperparams", lm.load_profile(kg_shift))

    def test_proof_graph_handwriting_diff_fork_spawn_and_v31_cli_and_server(self) -> None:
        import copy
        import syntopik.server as srv_mod
        import syntopik.sync_delta as sd

        df = load_script_module("derivation_forge.py")

        def _req(
            port: int,
            method: str,
            path: str,
            *,
            headers: dict[str, str] | None = None,
            body: Any = None,
        ) -> tuple[int, dict[str, str], bytes]:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=8.0)
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

        with tempfile.TemporaryDirectory(prefix="synt-v31-pdiff-") as tmp:
            t = Path(tmp)
            ws = t / "ws"
            kg = ws / "knowledge-graph"
            ch = ws / "chapters"
            kg.mkdir(parents=True)
            ch.mkdir(parents=True)

            (kg / "propositions.jsonl").write_text(
                "\n".join([
                    json.dumps({
                        "id": "p-hooke-sign",
                        "subject": "Physics",
                        "chapter": "SHM",
                        "title": "Hooke's Law Restoring Sign",
                        "kind": "axiom",
                        "status": "shaky",
                        "bktMastery": 0.35,
                    }),
                    json.dumps({
                        "id": "p-shm-osc",
                        "subject": "Physics",
                        "chapter": "SHM",
                        "title": "Damped Simple Harmonic Motion (SHM) Oscillator",
                        "statement": "Restoring force and viscous damping satisfy m x'' + b x' + k x = 0 with resonance.",
                        "kind": "theorem",
                        "status": "shaky",
                        "bktMastery": 0.40,
                    }),
                    json.dumps({
                        "id": "ee-series-rlc",
                        "subject": "Electrical Engineering",
                        "chapter": "AC Circuits",
                        "title": "Series RLC Resonant Circuit",
                        "statement": "Inductor L, resistor R, capacitor C obey L d^2q/dt^2 + R dq/dt + q/C = 0 with resonant frequency 1/sqrt(LC).",
                        "kind": "theorem",
                        "status": "solid",
                        "bktMastery": 0.90,
                    }),
                ]) + "\n",
                encoding="utf-8",
            )
            (kg / "chains.jsonl").write_text(
                json.dumps({"premises": ["p-hooke-sign"], "conclusion": "p-shm-osc", "relation": "derives"}) + "\n",
                encoding="utf-8",
            )
            (kg / "mistakes.jsonl").write_text("", encoding="utf-8")

            df.create_derivation(
                {
                    "id": "deriv-shm-diff",
                    "propId": "p-shm-osc",
                    "chapter": "SHM",
                    "title": "Spring-Block SHM Angular Frequency",
                    "domain": "Physics",
                    "steps": [
                        {"id": "a1", "role": "axiom", "label": "Hooke's Law", "expr": "F = -k*x", "dependsOn": []},
                        {"id": "a2", "role": "axiom", "label": "Newton Second Law", "expr": "F = m*a", "dependsOn": []},
                        {"id": "s1", "role": "step", "label": "Acceleration Law", "expr": "a = -(k/m)*x", "dependsOn": ["a1", "a2"]},
                        {"id": "s2", "role": "theorem", "label": "Angular Frequency", "expr": "omega = sqrt(k/m)", "dependsOn": ["s1"]},
                    ],
                },
                kg_dir=kg,
                workspace=ws,
            )

            # 1. Clean proof-diff pass
            clean_steps = [
                {"line": 1, "expr": "F = -k*x"},
                {"line": 2, "expr": "m*a = F"},
                {"line": 3, "expr": "a = -k*x/m"},
                {"line": 4, "expr": "omega = sqrt(k/m)"},
            ]
            ok_diff = df.diff_handwritten_steps(kg, "deriv-shm-diff", clean_steps, auto_spawn_fork=False, workspace=ws)
            self.assertTrue(ok_diff["ok"])
            self.assertTrue(ok_diff["allVerified"])
            self.assertIsNone(ok_diff["earliestBreakLine"])
            self.assertEqual(ok_diff["verifiedCount"], 4)

            # 2. Line-3 sign flip -> earliestBreakLine=3, errorKind="sign_flip", zero-leak R0-R2, Blunder Fork + diagnosticProbe
            flip_steps = [
                {"line": 1, "expr": "F = -k*x"},
                {"line": 2, "expr": "F = m*a"},
                {"line": 3, "expr": "a = (k/m)*x"},
                {"line": 4, "expr": "omega = sqrt(k/m)"},
            ]
            bad_diff = df.diff_handwritten_steps(kg, "deriv-shm-diff", flip_steps, auto_spawn_fork=True, workspace=ws)
            self.assertTrue(bad_diff["ok"])
            self.assertFalse(bad_diff["allVerified"])
            self.assertEqual(bad_diff["earliestBreakLine"], 3)
            self.assertEqual(bad_diff["earliestBreakStepId"], "s1")
            self.assertEqual(bad_diff["errorKind"], "sign_flip")
            self.assertEqual(bad_diff["mistakeCategory"], "sign_error")
            self.assertEqual([s["status"] for s in bad_diff["steps"]], ["verified", "verified", "broken", "skipped_dependency"])

            nudges = bad_diff.get("nudges") or {}
            for lvl in ("R0", "R1", "R2"):
                self.assertTrue(str(nudges.get(lvl) or "").strip(), f"Missing nudge {lvl}")
                self.assertNotIn("-(k/m)*x", nudges[lvl], f"Nudge {lvl} leaked canonical target expr!")
                self.assertNotIn("-k*x/m", nudges[lvl], f"Nudge {lvl} leaked canonical target expr!")

            fork_rec = bad_diff.get("spawnedFork")
            self.assertIsInstance(fork_rec, dict)
            assert isinstance(fork_rec, dict)
            self.assertTrue(fork_rec.get("id"))
            forks_disk = [
                json.loads(ln)
                for ln in (kg / "forks.jsonl").read_text(encoding="utf-8").splitlines()
                if ln.strip()
            ]
            self.assertIn(fork_rec["id"], {f.get("id") for f in forks_disk})
            self.assertEqual(bad_diff["verifiedPrefix"], ["F = -k*x", "F = m*a"])

            probe = bad_diff.get("diagnosticProbe")
            self.assertIsInstance(probe, dict)
            assert isinstance(probe, dict)
            self.assertEqual((probe.get("bestProbe") or {}).get("propId"), "p-hooke-sign")

            # 3. AST injection refusal & > 40 step bound
            canary_path = t / "pdiff-ast-pwned.txt"
            ast_res = df.diff_handwritten_steps(
                kg,
                "deriv-shm-diff",
                [{"line": 1, "expr": f"__import__('os').system('touch {canary_path}')"}],
                auto_spawn_fork=False,
                workspace=ws,
            )
            self.assertFalse(ast_res["allVerified"])
            self.assertEqual(ast_res["earliestBreakLine"], 1)
            self.assertFalse(canary_path.exists(), "AST injection executed in diff_handwritten_steps!")

            with self.assertRaises(ValueError):
                df.diff_handwritten_steps(
                    kg,
                    "deriv-shm-diff",
                    [{"line": i + 1, "expr": "x = 1"} for i in range(41)],
                    auto_spawn_fork=False,
                    workspace=ws,
                )

            # 4. v3.1+ Python & Node CLI verbs (--version == 3.2.0, rosetta, sync-delta, calibrate-policy, proof-diff)
            py_cli = [sys.executable, "-B", "-m", "syntopik.cli"]
            node_cli = ["node", str(ROOT / "bin" / "syntopik.mjs")]

            py_ver = run_cli([*py_cli, "--version"])
            self.assertEqual(py_ver.returncode, 0, py_ver.stderr)
            self.assertIn("3.2.0", py_ver.stdout)
            node_ver = run_cli([*node_cli, "--version"])
            self.assertEqual(node_ver.returncode, 0, node_ver.stderr)
            self.assertIn("3.2.0", node_ver.stdout)

            p_arch = run_cli([*py_cli, "rosetta", "archetypes", "--json"])
            self.assertEqual(p_arch.returncode, 0, p_arch.stderr)
            self.assertGreaterEqual(json.loads(p_arch.stdout)["count"], 10)

            p_scan = run_cli([*node_cli, "rosetta", "scan", "--workspace", str(ws), "--json"])
            self.assertEqual(p_scan.returncode, 0, p_scan.stderr)
            self.assertGreaterEqual(json.loads(p_scan.stdout)["chordCount"], 1)

            p_link = run_cli([
                *py_cli, "rosetta", "link",
                "--source", "p-shm-osc", "--target", "ee-series-rlc", "--archetype", "second_order_oscillator",
                "--workspace", str(ws), "--json",
            ])
            self.assertEqual(p_link.returncode, 0, p_link.stderr)
            self.assertTrue(json.loads(p_link.stdout)["ok"])

            p_br = run_cli([*py_cli, "rosetta", "bridge", "--prop-id", "p-shm-osc", "--workspace", str(ws), "--json"])
            self.assertEqual(p_br.returncode, 0, p_br.stderr)
            self.assertTrue(json.loads(p_br.stdout)["found"])

            cli_delta = t / "cli-delta.json"
            p_sd_st = run_cli([*node_cli, "sync-delta", "status", "--workspace", str(ws), "--json"])
            self.assertEqual(p_sd_st.returncode, 0, p_sd_st.stderr)
            self.assertTrue(json.loads(p_sd_st.stdout)["ok"])

            p_sd_ex = run_cli([*py_cli, "sync-delta", "export", "--workspace", str(ws), "--out", str(cli_delta), "--json"])
            self.assertEqual(p_sd_ex.returncode, 0, p_sd_ex.stderr)
            self.assertTrue(cli_delta.is_file())

            p_sd_vr = run_cli([*py_cli, "sync-delta", "verify", "--file", str(cli_delta), "--json"])
            self.assertEqual(p_sd_vr.returncode, 0, p_sd_vr.stderr)
            self.assertTrue(json.loads(p_sd_vr.stdout)["ok"])

            p_sd_mg = run_cli([*py_cli, "sync-delta", "merge", "--workspace", str(ws), "--file", str(cli_delta), "--dry-run", "--json"])
            self.assertEqual(p_sd_mg.returncode, 0, p_sd_mg.stderr)
            self.assertTrue(json.loads(p_sd_mg.stdout)["ok"])

            p_cal = run_cli([*node_cli, "calibrate-policy", "--workspace", str(ws), "--json"])
            self.assertEqual(p_cal.returncode, 0, p_cal.stderr)
            cal_j = json.loads(p_cal.stdout)
            self.assertTrue(cal_j["ok"])
            self.assertTrue(cal_j["insufficientData"])

            steps_file = t / "flip-steps.json"
            steps_file.write_text(json.dumps(flip_steps), encoding="utf-8")
            p_pd = run_cli([
                *node_cli, "proof-diff",
                "--id", "deriv-shm-diff", "--steps-json", str(steps_file),
                "--no-spawn-fork", "--workspace", str(ws), "--json",
            ])
            self.assertEqual(p_pd.returncode, 0, p_pd.stderr)
            pd_j = json.loads(p_pd.stdout)
            self.assertEqual(pd_j["earliestBreakLine"], 3)
            self.assertEqual(pd_j["errorKind"], "sign_flip")

            # 5. Live syntopik/server.py v3.1 endpoints: 200 OK, CSRF 403, tampered delta 400 & --lan cookie 403
            srv = srv_mod.create_server(workspace=ws, host="127.0.0.1", port=0, lan=False)
            t_srv = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
            t_srv.start()
            try:
                port = srv.bound_port
                auth = {"X-Syntopik-Token": srv.csrf_token}

                # GET /api/rosetta & POST /api/rosetta/link
                st_r, _, raw_r = _req(port, "GET", "/api/rosetta")
                self.assertEqual(st_r, 200, raw_r)
                r_j = json.loads(raw_r)
                self.assertTrue(r_j["ok"])
                self.assertGreaterEqual(len(r_j.get("chords") or []), 1)

                st_rl_csrf, _, _ = _req(
                    port, "POST", "/api/rosetta/link",
                    body={"sourceId": "p-shm-osc", "targetId": "ee-series-rlc", "archetypeId": "second_order_oscillator"},
                )
                self.assertEqual(st_rl_csrf, 403)
                st_rl, _, raw_rl = _req(
                    port, "POST", "/api/rosetta/link", headers=auth,
                    body={"sourceId": "p-shm-osc", "targetId": "ee-series-rlc", "archetypeId": "second_order_oscillator"},
                )
                self.assertEqual(st_rl, 200, raw_rl)
                self.assertTrue(json.loads(raw_rl)["ok"])

                # GET /api/sync/status, POST /api/sync/export, POST /api/sync/merge (+ tampered 400)
                st_ss, _, raw_ss = _req(port, "GET", "/api/sync/status")
                self.assertEqual(st_ss, 200, raw_ss)
                self.assertTrue(json.loads(raw_ss)["ok"])

                st_se_csrf, _, _ = _req(port, "POST", "/api/sync/export", body={})
                self.assertEqual(st_se_csrf, 403)
                st_se, _, raw_se = _req(port, "POST", "/api/sync/export", headers=auth, body={})
                self.assertEqual(st_se, 200, raw_se)
                se_j = json.loads(raw_se)
                self.assertTrue(se_j["ok"])
                exported_delta = se_j["delta"]
                self.assertTrue(sd.verify_sync_delta(exported_delta)["ok"])

                st_sm_csrf, _, _ = _req(port, "POST", "/api/sync/merge", body={"delta": exported_delta, "dryRun": True})
                self.assertEqual(st_sm_csrf, 403)

                bad_delta = copy.deepcopy(exported_delta)
                bad_delta["contentSha256"] = "0" * 64
                st_sm_bad, _, _ = _req(
                    port, "POST", "/api/sync/merge", headers=auth,
                    body={"delta": bad_delta, "dryRun": False},
                )
                self.assertEqual(st_sm_bad, 400)

                st_sm_ok, _, raw_sm_ok = _req(
                    port, "POST", "/api/sync/merge", headers=auth,
                    body={"delta": exported_delta, "dryRun": True},
                )
                self.assertEqual(st_sm_ok, 200, raw_sm_ok)
                self.assertTrue(json.loads(raw_sm_ok)["ok"])

                # POST /api/profile/replay
                st_pr_csrf, _, _ = _req(port, "POST", "/api/profile/replay", body={"apply": False})
                self.assertEqual(st_pr_csrf, 403)
                st_pr, _, raw_pr = _req(port, "POST", "/api/profile/replay", headers=auth, body={"apply": False})
                self.assertEqual(st_pr, 200, raw_pr)
                self.assertTrue(json.loads(raw_pr)["ok"])

                # POST /api/derivation/diff (CSRF 403 + 200 OK + zero leak)
                diff_body = {
                    "derivationId": "deriv-shm-diff",
                    "studentSteps": flip_steps,
                    "autoSpawnFork": False,
                }
                st_dd_csrf, _, _ = _req(port, "POST", "/api/derivation/diff", body=diff_body)
                self.assertEqual(st_dd_csrf, 403)
                st_dd, _, raw_dd = _req(port, "POST", "/api/derivation/diff", headers=auth, body=diff_body)
                self.assertEqual(st_dd, 200, raw_dd)
                dd_text = raw_dd.decode("utf-8")
                self.assertNotIn("-(k/m)*x", dd_text, "POST /api/derivation/diff leaked target step expr!")
                dd_j = json.loads(dd_text)
                self.assertTrue(dd_j["ok"])
                self.assertEqual(dd_j["earliestBreakLine"], 3)
                self.assertEqual(dd_j["errorKind"], "sign_flip")
            finally:
                srv.shutdown()
                srv.server_close()
                t_srv.join(timeout=3.0)

            # 6. --lan paired-cookie isolation: ALL 7 v3.1 routes MUST return 403
            srv_lan = srv_mod.create_server(workspace=ws, host="127.0.0.1", port=0, lan=True)
            t_lan = threading.Thread(target=srv_lan.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
            t_lan.start()
            try:
                lport = srv_lan.bound_port
                lan_host = f"192.168.1.88:{lport}"
                st_pair, hdrs_pair, _ = _req(lport, "GET", f"/pocket?pair={srv_lan.pair_token}", headers={"Host": lan_host})
                self.assertEqual(st_pair, 302)
                cookie = hdrs_pair["set-cookie"].split(";", 1)[0]

                lan_get_hdrs = {"Host": lan_host, "Cookie": cookie}
                lan_post_hdrs = {"Host": lan_host, "Origin": f"http://{lan_host}", "Cookie": cookie}
                for method, path, payload in (
                    ("GET", "/api/rosetta", None),
                    ("POST", "/api/rosetta/link", {"sourceId": "p-shm-osc", "targetId": "ee-series-rlc", "archetypeId": "second_order_oscillator"}),
                    ("GET", "/api/sync/status", None),
                    ("POST", "/api/sync/export", {}),
                    ("POST", "/api/sync/merge", {"delta": {}, "dryRun": True}),
                    ("POST", "/api/profile/replay", {"apply": False}),
                    ("POST", "/api/derivation/diff", {"derivationId": "deriv-shm-diff", "studentSteps": []}),
                ):
                    st_b, _, _ = _req(
                        lport,
                        method,
                        path,
                        headers=lan_get_hdrs if method == "GET" else lan_post_hdrs,
                        body=payload,
                    )
                    self.assertEqual(st_b, 403, f"--lan cookie MUST return 403 on {method} {path}, got {st_b}")
            finally:
                srv_lan.shutdown()
                srv_lan.server_close()
                t_lan.join(timeout=3.0)

    def test_v31_wave3b_adversarial_hardening_locks(self) -> None:
        """Wave 3B-Harden Lock: all 4 P0, 4 P1, and 3 P2 findings from #256."""
        import copy
        import syntopik.brainstem as bs
        import syntopik.evidence as ev_mod
        import syntopik.forks as forks_mod
        import syntopik.isomorphism as iso_mod
        import syntopik.learner_model as lm
        import syntopik.root_cause as rc_mod
        import syntopik.schemas as sch_mod
        import syntopik.server as srv_mod
        import syntopik.sim_eval as se_mod
        import syntopik.sync_delta as sd_mod

        df_mod = load_script_module("derivation_forge.py")
        fa_mod = load_script_module("falsify_arena.py")

        def _req(
            port: int,
            method: str,
            path: str,
            *,
            headers: dict[str, str] | None = None,
            body: Any = None,
        ) -> tuple[int, bytes]:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10.0)
            try:
                hdrs = dict(headers or {})
                raw: bytes | None = None
                if body is not None:
                    if isinstance(body, (bytes, bytearray)):
                        raw = bytes(body)
                    elif isinstance(body, str):
                        raw = body.encode("utf-8")
                    else:
                        raw = json.dumps(body).encode("utf-8")
                    hdrs.setdefault("Content-Type", "application/json")
                conn.request(method, path, body=raw, headers=hdrs)
                resp = conn.getresponse()
                return resp.status, resp.read()
            finally:
                conn.close()

        # --- P0.4: 6-term degree-8 rational SymPy DoS payload (<0.5s across all 3 evaluators) ---
        dos_expr = "((a+b+c+d+e+f)**8 + (-k*x)*(a+b+c+d+e+f)**8) / (1 + (a+b+c+d+e+f)**8)"
        for label, fn in (
            ("forks_deg8_dos", lambda: forks_mod._math_equivalent(dos_expr, "-k*x")),
            ("forge_deg8_dos", lambda: df_mod._math_equivalent(dos_expr, "-k*x")),
            ("arena_deg8_dos", lambda: fa_mod._try_sympy_eval(dos_expr, {})),
        ):
            with self.subTest(sympy_dos=label):
                t0 = time.monotonic()
                res_val = fn()
                dt = time.monotonic() - t0
                self.assertLess(dt, 0.5, f"{label} took {dt:.3f}s (exceeds 0.5s DoS ceiling)")
                if not label.startswith("arena_"):
                    self.assertFalse(res_val)
        # Equivalent high-expansion rational still passes fast via bounded AST numeric sampling
        t0_eq = time.monotonic()
        self.assertTrue(
            forks_mod._math_equivalent(
                "((-k*x) + (-k*x)*(a+b+c+d+e+f)**8) / (1 + (a+b+c+d+e+f)**8)",
                "-k*x",
            )
        )
        self.assertLess(time.monotonic() - t0_eq, 0.5)
        self.assertTrue(forks_mod._math_equivalent("(x+1)**2", "x**2 + 2*x + 1"))

        with tempfile.TemporaryDirectory(prefix="synt-v31-harden-") as tmp:
            t = Path(tmp)

            # --- P0.1: CRDT Sync NaN/Inf rejection & numeric bounds clamping ---
            ws_src = t / "ws-sync-src"
            ws_dst = t / "ws-sync-dst"
            for wr in (ws_src, ws_dst):
                (wr / "knowledge-graph").mkdir(parents=True)
                (wr / "chapters").mkdir(parents=True)
            (ws_src / "knowledge-graph" / "propositions.jsonl").write_text(
                json.dumps({
                    "id": "p-bounds",
                    "subject": "Physics",
                    "chapter": "SHM",
                    "title": "Restoring Force",
                    "status": "solid",
                    "bktMastery": 0.85,
                    "fsrs": {"stability": 5.0, "difficulty": 4.5},
                }) + "\n",
                encoding="utf-8",
            )
            lm.record_telemetry_event(
                ws_src / "knowledge-graph",
                domain="Physics",
                prop_id="p-bounds",
                correct=True,
                confidence=0.85,
                duration_ms=9000,
                now="2026-11-24T10:00:00Z",
            )
            base_delta = sd_mod.export_sync_delta(ws_src, device_id="dev-src-01")["delta"]

            # 1a. Non-finite string "NaN" / "Infinity" inside strict RFC-8259 JSON rejected by verify & merge
            for bad_field, mutator in (
                ("elo_nan_str", lambda d: d["payload"]["learnerProfile"]["domains"]["Physics"].__setitem__("elo", "NaN")),
                ("elo_inf_str", lambda d: d["payload"]["learnerProfile"]["globalStats"].__setitem__("elo", "Infinity")),
                ("bkt_ninf_str", lambda d: d["payload"]["propositions"][0].__setitem__("bktMastery", "-Infinity")),
                ("fsrs_stab_nan_str", lambda d: d["payload"]["propositions"][0]["fsrs"].__setitem__("stability", "NaN")),
            ):
                with self.subTest(bad_sync=bad_field):
                    bad = copy.deepcopy(base_delta)
                    mutator(bad)
                    bad["contentSha256"] = sd_mod._compute_delta_sha256(bad)
                    ver = sd_mod.verify_sync_delta(bad)
                    self.assertFalse(ver["ok"], f"{bad_field} MUST be rejected: {ver}")
                    mrg = sd_mod.merge_sync_delta(ws_dst, bad)
                    self.assertFalse(mrg["ok"], f"{bad_field} merge MUST fail closed")

            # 1b. Python float NaN dict & literal JSON NaN file rejected
            nan_dict = copy.deepcopy(base_delta)
            nan_dict["payload"]["propositions"][0]["bktMastery"] = float("nan")
            self.assertFalse(sd_mod.verify_sync_delta(nan_dict)["ok"])

            raw_nan_file = t / "literal-nan.json"
            raw_nan_file.write_text(
                json.dumps(base_delta).replace("0.85", "NaN", 1),
                encoding="utf-8",
            )
            self.assertFalse(sd_mod.verify_sync_delta(raw_nan_file)["ok"])

            # 1c. Out-of-bounds finite values clamped cleanly on merge
            oob = copy.deepcopy(base_delta)
            oob["payload"]["propositions"][0]["bktMastery"] = -999
            oob["payload"]["propositions"][0]["fsrs"]["stability"] = -5
            oob["payload"]["propositions"][0]["fsrs"]["difficulty"] = 999
            oob["payload"]["learnerProfile"]["domains"]["Physics"]["elo"] = 9999
            oob["payload"]["learnerProfile"]["domains"]["Physics"]["n"] = -7
            oob["payload"]["learnerProfile"]["domains"]["Physics"]["deviceStates"] = {
                "dev-src-01": {"elo": 9999, "n": -7, "lastUpdated": "2026-11-24T10:00:00Z"}
            }
            oob["payload"]["learnerProfile"]["globalStats"]["elo"] = 100
            oob["payload"]["learnerProfile"]["globalStats"]["totalEvents"] = -3
            oob["payload"]["learnerProfile"]["globalStats"]["deviceStates"] = {
                "dev-src-01": {"elo": 100, "n": -3, "pfAttempts": -2, "pfSurvivalRate": 5.0, "analogyFormalismRatio": -1.0, "latencyEwma": 0.0}
            }
            oob["contentSha256"] = sd_mod._compute_delta_sha256(oob)
            m_oob = sd_mod.merge_sync_delta(ws_dst, oob)
            self.assertTrue(m_oob["ok"], m_oob)
            dst_prop = json.loads((ws_dst / "knowledge-graph" / "propositions.jsonl").read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(dst_prop["bktMastery"], 0.0)
            self.assertEqual(dst_prop["fsrs"]["stability"], 0.01)
            self.assertEqual(dst_prop["fsrs"]["difficulty"], 10.0)
            dst_prof = json.loads((ws_dst / "knowledge-graph" / "learner-profile.json").read_text(encoding="utf-8"))
            self.assertEqual(dst_prof["domains"]["Physics"]["elo"], 2600.0)
            self.assertEqual(dst_prof["domains"]["Physics"]["n"], 0)
            self.assertGreaterEqual(dst_prof["globalStats"]["elo"], 800.0)
            self.assertLessEqual(dst_prof["globalStats"]["elo"], 2600.0)
            self.assertGreaterEqual(dst_prof["globalStats"]["totalEvents"], 0)

            # --- P0.2: Rosetta isomorphic_to metadata preserved & undirected dedup on CRDT merge ---
            nc_test = sch_mod.normalize_chain({
                "from": "shm-1",
                "to": "rlc-1",
                "relation": "isomorphic_to",
                "encompassWeight": 0.25,
                "archetypeId": "second_order_oscillator",
                "breaksAt": ["Non-linear spring breakdown"],
                "createdAt": "2026-11-20T08:00:00Z",
            })
            assert nc_test is not None
            self.assertEqual(nc_test["archetypeId"], "second_order_oscillator")
            self.assertEqual(nc_test["breaksAt"], ["Non-linear spring breakdown"])
            self.assertEqual(nc_test["createdAt"], "2026-11-20T08:00:00Z")

            iso_a = t / "iso-dev-a"
            iso_b = t / "iso-dev-b"
            for wr in (iso_a, iso_b):
                (wr / "knowledge-graph").mkdir(parents=True)
                (wr / "chapters").mkdir(parents=True)
            (iso_a / "knowledge-graph" / "chains.jsonl").write_text(
                json.dumps({
                    "from": "shm-1",
                    "to": "rlc-1",
                    "premises": ["shm-1"],
                    "conclusion": "rlc-1",
                    "relation": "isomorphic_to",
                    "encompassWeight": 0.25,
                    "archetypeId": "second_order_oscillator",
                    "breaksAt": ["Linearity fails at large amplitude"],
                    "why": "Harmonic oscillator duality",
                    "createdAt": "2026-11-21T09:00:00Z",
                }) + "\n",
                encoding="utf-8",
            )
            (iso_b / "knowledge-graph" / "chains.jsonl").write_text(
                json.dumps({
                    "from": "rlc-1",
                    "to": "shm-1",
                    "premises": ["rlc-1"],
                    "conclusion": "shm-1",
                    "relation": "isomorphic_to",
                    "encompassWeight": 0.25,
                    "archetypeId": "second_order_oscillator",
                    "breaksAt": ["Inductor core saturates at high current"],
                    "why": "Harmonic oscillator duality",
                    "createdAt": "2026-11-20T08:00:00Z",
                }) + "\n",
                encoding="utf-8",
            )
            exp_iso_a = sd_mod.export_sync_delta(iso_a, device_id="dev-iso-a")["delta"]
            exp_iso_b = sd_mod.export_sync_delta(iso_b, device_id="dev-iso-b")["delta"]
            iso_ab = t / "iso-ab"
            iso_ba = t / "iso-ba"
            shutil.copytree(iso_a, iso_ab)
            shutil.copytree(iso_b, iso_ba)
            self.assertTrue(sd_mod.merge_sync_delta(iso_ab, exp_iso_b)["ok"])
            self.assertTrue(sd_mod.merge_sync_delta(iso_ba, exp_iso_a)["ok"])
            chains_ab = [json.loads(ln) for ln in (iso_ab / "knowledge-graph" / "chains.jsonl").read_text(encoding="utf-8").splitlines() if ln.strip()]
            chains_ba = [json.loads(ln) for ln in (iso_ba / "knowledge-graph" / "chains.jsonl").read_text(encoding="utf-8").splitlines() if ln.strip()]
            self.assertEqual(len(chains_ab), 1, f"Expected 1 deduped undirected chord, got {chains_ab}")
            self.assertEqual(chains_ab, chains_ba, "Undirected isomorphic_to merge must be commutative")
            self.assertEqual(chains_ab[0]["archetypeId"], "second_order_oscillator")
            self.assertEqual(
                set(chains_ab[0]["breaksAt"]),
                {"Linearity fails at large amplitude", "Inductor core saturates at high current"},
            )
            self.assertEqual(chains_ab[0]["createdAt"], "2026-11-20T08:00:00Z")

            # --- P1.1: root_cause.py & evidence.py ignore isomorphic_to / contrasts_with / analogous ---
            ws_nc = t / "ws-noncausal"
            kg_nc = ws_nc / "knowledge-graph"
            kg_nc.mkdir(parents=True)
            (kg_nc / "propositions.jsonl").write_text(
                "\n".join([
                    json.dumps({"id": "shm-osc", "subject": "Physics", "chapter": "SHM", "title": "SHM", "status": "shaky"}),
                    json.dumps({"id": "rlc-osc", "subject": "EE", "chapter": "RLC", "title": "RLC", "status": "solid"}),
                    json.dumps({"id": "pid-ctrl", "subject": "Control", "chapter": "PID", "title": "PID", "status": "solid"}),
                ]) + "\n",
                encoding="utf-8",
            )
            (kg_nc / "chains.jsonl").write_text(
                "\n".join([
                    json.dumps({"premises": ["shm-osc"], "conclusion": "rlc-osc", "relation": "isomorphic_to", "encompassWeight": 0.25, "archetypeId": "second_order_oscillator"}),
                    json.dumps({"premises": ["shm-osc"], "conclusion": "pid-ctrl", "relation": "isomorphic_to", "encompassWeight": 0.25, "archetypeId": "second_order_oscillator"}),
                    json.dumps({"premises": ["rlc-osc"], "conclusion": "pid-ctrl", "relation": "contrasts_with", "encompassWeight": 0.5}),
                    json.dumps({"premises": ["rlc-osc"], "conclusion": "shm-osc", "relation": "analogous", "encompassWeight": 0.5}),
                ]) + "\n",
                encoding="utf-8",
            )
            loc = rc_mod.localize_fault(kg_nc, "rlc-osc")
            self.assertTrue(loc["ok"])
            self.assertEqual(loc["suspects"], [], f"isomorphic_to must not pollute localize_fault: {loc}")
            self.assertEqual(rc_mod.find_blast_radius(kg_nc), [])
            ev_nc = ev_mod.compute_evidence_summary(ws_nc)
            self.assertEqual(ev_nc["propositions"]["shm-osc"]["outDegree"], 0)
            self.assertEqual(ev_nc["propositions"]["shm-osc"]["descendantsCount"], 0)
            self.assertEqual(ev_nc["antiInflationAlerts"], [])

            # --- P1.2: Replay NaN/Inf hintsUsed, corrupt profile floats, & Windows pre-1970 dates ---
            ws_rep = t / "ws-replay-edge"
            kg_rep = ws_rep / "knowledge-graph"
            kg_rep.mkdir(parents=True)
            (kg_rep / "learner-profile.json").write_text(
                json.dumps({
                    "schemaVersion": 1,
                    "calibration": {
                        "completed": True,
                        "sessionTargetMinutes": "NaN",
                        "baselineLatencyMs": "Infinity",
                    },
                }),
                encoding="utf-8",
            )
            edge_revlog = []
            for i in range(14):
                ts_iso = "0001-01-01T00:00:00Z" if i == 0 else f"2026-10-{i + 1:02d}T10:00:00Z"
                edge_revlog.append({
                    "propId": f"p-e-{i % 4}",
                    "domain": "Physics",
                    "ts": ts_iso,
                    "rating": "good" if i % 3 != 0 else "again",
                    "correct": i % 3 != 0,
                    "learnerConfidence": 0.80,
                    "difficulty": 1520,
                    "hintsUsed": "NaN" if i == 1 else ("Infinity" if i == 2 else 0),
                    "durationMs": 12000,
                })
            (kg_rep / "revlog.jsonl").write_text("\n".join(json.dumps(r) for r in edge_revlog) + "\n", encoding="utf-8")
            rep_edge = se_mod.replay_learner_history(ws_rep, min_events=12, apply_if_improved=False)
            self.assertTrue(rep_edge["ok"], rep_edge)
            self.assertFalse(rep_edge["insufficientData"])
            self.assertEqual(rep_edge["eventCount"], 14)
            self.assertEqual(rep_edge["split"]["trainStart"], "0001-01-01T00:00:00Z")

            # --- P1.3: Pip-wheel archetypes.json lookup in scripts/sync-graph.mjs ---
            wheel_root = t / "wheel_pkg"
            (wheel_root / "data").mkdir(parents=True)
            (wheel_root / "_bundle" / "scripts").mkdir(parents=True)
            shutil.copy2(ROOT / "syntopik" / "data" / "archetypes.json", wheel_root / "data" / "archetypes.json")
            shutil.copy2(SCRIPTS_DIR / "sync-graph.mjs", wheel_root / "_bundle" / "scripts" / "sync-graph.mjs")
            wheel_ws = t / "wheel-ws"
            (wheel_ws / "knowledge-graph").mkdir(parents=True)
            (wheel_ws / "chapters").mkdir(parents=True)
            (wheel_ws / "knowledge-graph" / "propositions.jsonl").write_text("", encoding="utf-8")
            (wheel_ws / "knowledge-graph" / "chains.jsonl").write_text("", encoding="utf-8")
            wheel_out = wheel_ws / "knowledge-graph" / "graph-data.js"
            p_wh = run_cli([
                "node", str(wheel_root / "_bundle" / "scripts" / "sync-graph.mjs"),
                "--workspace", str(wheel_ws),
                "--kg-dir", str(wheel_ws / "knowledge-graph"),
                "--chapters-dir", str(wheel_ws / "chapters"),
                "--out", str(wheel_out),
            ])
            self.assertEqual(p_wh.returncode, 0, f"Wheel sync-graph.mjs failed: {p_wh.stderr}")
            p_wh_read = run_cli([
                "node", "-e",
                f"globalThis.window={{}}; require({json.dumps(str(wheel_out))}); console.log(window.__STUDY_HUB__.rosetta.archetypesCount);",
            ])
            self.assertEqual(p_wh_read.returncode, 0, p_wh_read.stderr)
            self.assertGreaterEqual(int(p_wh_read.stdout.strip()), 10)

            # --- P1.4: Brainstem worst-case envelope strictly <= 180 tokens ---
            ws_bs = t / "ws-brainstem-budget"
            kg_bs = ws_bs / "knowledge-graph"
            ch_bs = ws_bs / "chapters"
            kg_bs.mkdir(parents=True)
            ch_bs.mkdir(parents=True)
            long_known = "physics-very-long-second-order-damped-harmonic-oscillator-node-alpha-001"
            long_target = "electrical-engineering-very-long-series-rlc-resonant-circuit-node-beta-002"
            (kg_bs / "propositions.jsonl").write_text(
                "\n".join([
                    json.dumps({
                        "id": long_known,
                        "subject": "Physics",
                        "chapter": "SHM",
                        "title": "Damped Simple Harmonic Motion (SHM) Oscillator",
                        "statement": "Linear restoring force m x'' + b x' + k x = 0 with viscous damping and resonance.",
                        "status": "solid",
                        "bktMastery": 0.94,
                        "nextReview": "2099-01-01",
                    }),
                    json.dumps({
                        "id": long_target,
                        "subject": "Electrical Engineering",
                        "chapter": "AC Circuits",
                        "title": "Series RLC Resonant Circuit",
                        "statement": "Inductor L, resistor R, capacitor C obey L q'' + R q' + q/C = 0 with resonance 1/sqrt(LC).",
                        "status": "planned",
                        "bktMastery": 0.12,
                    }),
                ]) + "\n",
                encoding="utf-8",
            )
            (kg_bs / "chains.jsonl").write_text("", encoding="utf-8")
            iso_mod.link_isomorphism_chord(kg_bs, long_known, long_target, "second_order_oscillator")
            (kg_bs / "whiteboard-session.json").write_text(
                json.dumps({
                    "frames": [{"title": "Step 1", "kind": "derivation_step", "body": "Derive resonance"}],
                    "pins": [
                        {"id": f"pin-very-long-identifier-{i}", "stepIndex": i, "resolved": False, "note": " ".join([f"word{i}_{j}" for j in range(25)])}
                        for i in range(3)
                    ],
                }),
                encoding="utf-8",
            )
            prof_bs = lm.load_profile(kg_bs)
            lm.apply_tier1_calibration(prof_bs, entry_ramp="balanced", challenge_appetite="balanced", prose_density="standard")
            lm.save_profile(kg_bs, prof_bs)
            lm.record_telemetry_event(kg_bs, domain="Physics", prop_id=long_known, correct=True, confidence=0.90, duration_ms=9000)

            env_pins = bs.plan_next_move(ws_bs)
            self.assertLessEqual(bs._envelope_token_count(env_pins), 180, f"Pins envelope > 180 tokens: {env_pins}")

            # Also stress _enforce_envelope_budget directly with simultaneous bridge + pins + alerts + long rationale
            worst = {
                "ok": True,
                "recommendedMode": "teach_frontier",
                "targetNodeId": long_target,
                "chapterSlug": "super-long-chapter-slug-for-worst-case-token-budget-stress-test",
                "rationale": " ".join(["rationale"] * 40),
                "teachingPolicy": {
                    "domain": "Electrical Engineering",
                    "scaffoldingMode": "faded",
                    "allowWorkedExample": False,
                    "allowProductiveFailure": True,
                    "maxProseWords": 135,
                    "targetSuccessRate": 0.85,
                    "requirePredictionFirst": True,
                    "affectState": "flow",
                    "affectIntervention": "none",
                },
                "policyConstraints": [" ".join(["constraint"] * 12) for _ in range(3)],
                "exactCmd": "syntopik run chapter_tracker next --slug super-long-chapter-slug --extra-flag " + " ".join(["arg"] * 20),
                "skillHint": "teach-chapter",
                "whiteboardHint": " ".join(["whiteboard"] * 30),
                "pendingPins": [{"id": f"pin-{i}", "stepIndex": i, "note": " ".join(["pinword"] * 18)} for i in range(3)],
                "alerts": [" ".join(["alertword"] * 15) for _ in range(3)],
                "isomorphicBridge": {
                    "knownPropId": long_known,
                    "archetypeName": "Second-Order Damped Linear Oscillator Structural Archetype",
                    "breaksAt": [" ".join(["breakword"] * 25)],
                    "openerPrompt": " ".join(["openerword"] * 35),
                },
            }
            trimmed = bs._enforce_envelope_budget(worst, max_tokens=180)
            self.assertLessEqual(bs._envelope_token_count(trimmed), 180)

            # --- P0.3 (Proof-Graph Diff zero-leak via mistakes.jsonl & GET /api/state) + P0.1 & P2 HTTP locks ---
            ws_srv = t / "ws-srv-harden"
            kg_srv = ws_srv / "knowledge-graph"
            ch_srv = ws_srv / "chapters"
            kg_srv.mkdir(parents=True)
            ch_srv.mkdir(parents=True)
            secret_step_expr = "m*a = -k*x"
            (kg_srv / "propositions.jsonl").write_text(
                json.dumps({"id": "p-shm-seal", "subject": "Physics", "chapter": "SHM", "title": "SHM Seal", "status": "shaky"}) + "\n",
                encoding="utf-8",
            )
            (kg_srv / "chains.jsonl").write_text("", encoding="utf-8")
            (kg_srv / "mistakes.jsonl").write_text("", encoding="utf-8")
            df_mod.create_derivation(
                {
                    "id": "deriv-seal-check",
                    "propId": "p-shm-seal",
                    "chapter": "SHM",
                    "title": "Hooke Restoring Law",
                    "domain": "Physics",
                    "steps": [
                        {"id": "a1", "role": "axiom", "label": "Newton Second Law", "expr": "F = m*a", "dependsOn": []},
                        {"id": "s1", "role": "step", "label": "Restoring Equation", "expr": secret_step_expr, "dependsOn": ["a1"]},
                    ],
                },
                kg_dir=kg_srv,
                workspace=ws_srv,
            )

            srv = srv_mod.create_server(workspace=ws_srv, host="127.0.0.1", port=0, lan=False)
            t_srv = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
            t_srv.start()
            try:
                port = srv.bound_port
                auth = {"X-Syntopik-Token": srv.csrf_token}

                # P0.3: POST /api/derivation/diff with autoSpawnFork=True MUST NOT leak canonical step into mistakes.jsonl or GET /api/state
                st_dd, raw_dd = _req(
                    port,
                    "POST",
                    "/api/derivation/diff",
                    headers=auth,
                    body={
                        "derivationId": "deriv-seal-check",
                        "studentSteps": [{"line": 1, "expr": "F = m*a"}, {"line": 2, "expr": "m*a = +k*x"}],
                        "autoSpawnFork": True,
                    },
                )
                self.assertEqual(st_dd, 200, raw_dd)
                self.assertNotIn(secret_step_expr, raw_dd.decode("utf-8"))
                self.assertNotIn("-k*x", raw_dd.decode("utf-8"))

                mistakes_txt = (kg_srv / "mistakes.jsonl").read_text(encoding="utf-8")
                self.assertNotIn(secret_step_expr, mistakes_txt, "mistakes.jsonl leaked canonical expected_expr!")
                self.assertNotIn("-k*x", mistakes_txt, "mistakes.jsonl leaked canonical RHS!")
                st_state, raw_state = _req(port, "GET", "/api/state")
                self.assertEqual(st_state, 200, raw_state)
                state_txt = raw_state.decode("utf-8")
                self.assertNotIn(secret_step_expr, state_txt, "GET /api/state leaked canonical expected_expr!")
                self.assertNotIn("-k*x", state_txt, "GET /api/state leaked canonical RHS!")

                # P0.1 over HTTP: literal JSON NaN & string "NaN" delta rejected with 400; OOB finite clamped with 200
                st_lit_nan, _ = _req(port, "POST", "/api/sync/merge", headers=auth, body='{"delta": {"elo": NaN}}')
                self.assertEqual(st_lit_nan, 400)
                bad_http_delta = copy.deepcopy(base_delta)
                bad_http_delta["payload"]["learnerProfile"]["domains"]["Physics"]["elo"] = "NaN"
                bad_http_delta["contentSha256"] = sd_mod._compute_delta_sha256(bad_http_delta)
                st_str_nan, _ = _req(port, "POST", "/api/sync/merge", headers=auth, body={"delta": bad_http_delta, "dryRun": False})
                self.assertEqual(st_str_nan, 400)
                st_oob_http, raw_oob_http = _req(port, "POST", "/api/sync/merge", headers=auth, body={"delta": oob, "dryRun": False})
                self.assertEqual(st_oob_http, 200, raw_oob_http)

                # P2: POST /api/sync/export rejects outPath="exports" (directory) & non-.json with 400
                st_dir, _ = _req(port, "POST", "/api/sync/export", headers=auth, body={"outPath": "exports"})
                self.assertEqual(st_dir, 400, "POST /api/sync/export must reject exports directory with 400")
                st_ext, _ = _req(port, "POST", "/api/sync/export", headers=auth, body={"outPath": "exports/bad.txt"})
                self.assertEqual(st_ext, 400, "POST /api/sync/export must reject non-.json suffix with 400")
                st_ok_exp, raw_ok_exp = _req(port, "POST", "/api/sync/export", headers=auth, body={"outPath": "exports/clean.json"})
                self.assertEqual(st_ok_exp, 200, raw_ok_exp)
                self.assertTrue((ws_srv / "exports" / "clean.json").is_file())
            finally:
                srv.shutdown()
                srv.server_close()
                t_srv.join(timeout=3.0)

        # --- P2 UI Polish locks: dashboard.html (eloBarPct NaN + lift sign) & viewer.html esc(') ---
        dash_src = (ROOT / "dashboard.html").read_text(encoding="utf-8")
        viewer_src = (ROOT / "knowledge-graph" / "viewer.html").read_text(encoding="utf-8")
        self.assertIn("Number.isFinite(n)", dash_src)
        self.assertNotIn("<strong>+${lift.toFixed(4)} OOS lift</strong>", dash_src)
        self.assertIn('"&#39;"', viewer_src)
        p_ui = run_cli([
            "node", "-e",
            (
                "const eloBarPct = (elo) => { const n = Number(elo); return Number.isFinite(n) ? Math.max(6, Math.min(100, Math.round(((n - 1000) / 900) * 100))) : 0; };\n"
                "if (eloBarPct(NaN) !== 0 || eloBarPct('NaN') !== 0) throw new Error('eloBarPct NaN failed');\n"
                "const fmt = (lift) => `${lift >= 0 ? '+' : ''}${lift.toFixed(4)}`;\n"
                "if (fmt(-0.01) !== '-0.0100' || fmt(0.02) !== '+0.0200') throw new Error('lift fmt failed');\n"
                "console.log('UI_POLISH_OK');\n"
            ),
        ])
        self.assertEqual(p_ui.returncode, 0, p_ui.stderr)
        self.assertIn("UI_POLISH_OK", p_ui.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
