#!/usr/bin/env python3
"""Tests for passphrase-encrypted sync deltas (syntopik/sync_crypto.py + sync_delta integration)."""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from syntopik import sync_crypto as sc  # noqa: E402
from syntopik import sync_delta as sd  # noqa: E402
from syntopik.storage import load_jsonl  # noqa: E402

PASS = "correct horse battery staple"
SECRET = "my-private-misconception-about-entropy"
needs_crypto = unittest.skipUnless(sc.crypto_available(), 'needs the optional "cryptography" package (syntopik[sync])')


def _workspace(root: Path, name: str) -> Path:
    ws = root / name
    kg = ws / "knowledge-graph"
    kg.mkdir(parents=True)
    (ws / "chapters").mkdir()
    (kg / "propositions.jsonl").write_text(
        json.dumps({"id": "entropy", "title": "Entropy", "statement": SECRET, "status": "shaky", "reps": 2}) + "\n",
        encoding="utf-8",
    )
    (kg / "revlog.jsonl").write_text(
        json.dumps({"propId": "entropy", "ts": "2026-11-20T10:00:00Z", "rating": "again"}) + "\n", encoding="utf-8"
    )
    return ws


class TestSyncEncryption(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="synt-enc-")
        self.root = Path(self._tmp.name)
        self.src = _workspace(self.root, "laptop")
        self.dst = self.root / "phone"
        (self.dst / "knowledge-graph").mkdir(parents=True)
        (self.dst / "chapters").mkdir()
        self.out = self.root / "delta.enc.json"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    @needs_crypto
    def test_round_trip_hides_content_and_merges(self) -> None:
        res = sd.export_sync_delta(self.src, out_path=self.out, passphrase=PASS, device_id="laptop-01")
        self.assertTrue(res["encrypted"])
        raw = self.out.read_text(encoding="utf-8")
        self.assertNotIn(SECRET, raw)
        self.assertNotIn("entropy", raw)
        env = json.loads(raw)
        self.assertEqual(env["format"], sc.ENCRYPTED_FORMAT)
        self.assertEqual(env["cipher"], "AES-256-GCM")

        ver = sd.verify_sync_delta(self.out, passphrase=PASS)
        self.assertTrue(ver["ok"], ver)
        merged = sd.merge_sync_delta(self.dst, self.out, passphrase=PASS)
        self.assertTrue(merged["ok"], merged)
        props = load_jsonl(self.dst / "knowledge-graph" / "propositions.jsonl")
        self.assertEqual([p["statement"] for p in props], [SECRET])
        # Idempotent like plain deltas
        again = sd.merge_sync_delta(self.dst, self.out, passphrase=PASS)
        self.assertTrue(again["ok"])
        self.assertEqual(len(load_jsonl(self.dst / "knowledge-graph" / "propositions.jsonl")), 1)

    @needs_crypto
    def test_fail_closed_on_wrong_or_missing_passphrase_and_tampering(self) -> None:
        sd.export_sync_delta(self.src, out_path=self.out, passphrase=PASS)
        old_env = os.environ.pop(sc.PASSPHRASE_ENV, None)
        try:
            for bad in (None, "wrong passphrase!"):
                res = sd.merge_sync_delta(self.dst, self.out, passphrase=bad)
                self.assertFalse(res["ok"])
            self.assertFalse((self.dst / "knowledge-graph" / "propositions.jsonl").exists())

            env = json.loads(self.out.read_text(encoding="utf-8"))
            ct = bytearray(base64.b64decode(env["ciphertext"]))
            ct[5] ^= 0x01
            flipped = dict(env, ciphertext=base64.b64encode(bytes(ct)).decode())
            self.assertIn("modified", sd.verify_sync_delta(flipped, passphrase=PASS)["errors"][0])

            # Header is bound as associated data: weakening the KDF breaks decryption
            weaker = json.loads(json.dumps(env))
            weaker["kdf"]["n"] = 2**14
            self.assertFalse(sd.verify_sync_delta(weaker, passphrase=PASS)["ok"])
            # A crafted header cannot demand huge scrypt memory
            greedy = json.loads(json.dumps(env))
            greedy["kdf"]["n"] = 2**24
            self.assertIn("safe bounds", sd.verify_sync_delta(greedy, passphrase=PASS)["errors"][0])
        finally:
            if old_env is not None:
                os.environ[sc.PASSPHRASE_ENV] = old_env

    @needs_crypto
    def test_env_passphrase_and_short_passphrase(self) -> None:
        with self.assertRaises(sc.DeltaCryptoError):
            sd.export_sync_delta(self.src, out_path=self.out, passphrase="short")
        sd.export_sync_delta(self.src, out_path=self.out, passphrase=PASS)
        old_env = os.environ.get(sc.PASSPHRASE_ENV)
        os.environ[sc.PASSPHRASE_ENV] = PASS
        try:
            self.assertTrue(sd.verify_sync_delta(self.out)["ok"])
        finally:
            if old_env is None:
                os.environ.pop(sc.PASSPHRASE_ENV, None)
            else:
                os.environ[sc.PASSPHRASE_ENV] = old_env

    @needs_crypto
    def test_cli_encrypt_export_verify_merge(self) -> None:
        env = dict(os.environ, **{sc.PASSPHRASE_ENV: PASS})

        def cli(*args: str, ws: Path) -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                [sys.executable, "-B", "-m", "syntopik.cli", "sync-delta", *args, "--workspace", str(ws), "--json"],
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                timeout=60,
                env=env,
                check=False,
            )

        exp = cli("export", "--encrypt", "--out", str(self.out), ws=self.src)
        self.assertEqual(exp.returncode, 0, exp.stderr)
        exp_json = json.loads(exp.stdout)
        self.assertTrue(exp_json["encrypted"])
        self.assertNotIn(SECRET, exp.stdout)  # plaintext never echoed next to the encrypted file
        self.assertEqual(cli("verify", "--file", str(self.out), ws=self.dst).returncode, 0)
        merged = cli("merge", "--file", str(self.out), ws=self.dst)
        self.assertEqual(merged.returncode, 0, merged.stderr)
        self.assertTrue(json.loads(merged.stdout)["ok"])

    def test_plain_deltas_unchanged_and_missing_extra_is_explained(self) -> None:
        plain = self.root / "delta.json"
        sd.export_sync_delta(self.src, out_path=plain)
        self.assertEqual(json.loads(plain.read_text(encoding="utf-8"))["format"], sd.DELTA_FORMAT)
        self.assertTrue(sd.verify_sync_delta(plain)["ok"])
        if not sc.crypto_available():
            with self.assertRaises(sc.DeltaCryptoError) as ctx:
                sd.export_sync_delta(self.src, out_path=self.out, passphrase=PASS)
            self.assertIn("syntopik[sync]", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
