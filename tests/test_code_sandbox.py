#!/usr/bin/env python3
"""Sandbox limit tests for scripts/code_verify.py (network isolation, file-size cap, toolchains)."""

from __future__ import annotations

import importlib.util
import os
import shutil
import sys
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("_synt_code_verify_sandbox", ROOT / "scripts" / "code_verify.py")
cv = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
_spec.loader.exec_module(cv)  # type: ignore[union-attr]

NET_PROBE = """
import socket
try:
    socket.create_connection(("1.1.1.1", 80), timeout=2)
    print("NET_OPEN")
except OSError:
    print("NET_BLOCKED")
"""


class TestCodeSandboxLimits(unittest.TestCase):
    def test_result_reports_applied_sandbox(self) -> None:
        res = cv.run_sandboxed("print(6 * 7)", lang="python")
        self.assertEqual(res["status"], "ok")
        self.assertEqual(res["stdout"].strip(), "42")
        sb = res["sandbox"]
        self.assertIn(sb["memoryLimit"], {"rlimit_as", "job_object", "none"})
        self.assertIsInstance(sb["networkIsolated"], bool)
        if sys.platform.startswith("linux"):
            self.assertEqual(sb["memoryLimit"], "rlimit_as")

    @unittest.skipUnless(sys.platform.startswith("linux"), "network namespaces are Linux-only")
    def test_network_is_blocked_when_namespaces_are_available(self) -> None:
        if not cv._network_isolation_prefix():
            self.skipTest("unprivileged user namespaces unavailable on this host")
        res = cv.run_sandboxed(NET_PROBE, lang="python")
        self.assertTrue(res["sandbox"]["networkIsolated"])
        self.assertIn("NET_BLOCKED", res["stdout"])

    @unittest.skipUnless(sys.platform.startswith("linux"), "network namespaces are Linux-only")
    def test_network_opt_out(self) -> None:
        old = os.environ.get("SYNT_SANDBOX_NETWORK")
        os.environ["SYNT_SANDBOX_NETWORK"] = "allow"
        try:
            self.assertEqual(cv._network_isolation_prefix(), [])
        finally:
            if old is None:
                os.environ.pop("SYNT_SANDBOX_NETWORK", None)
            else:
                os.environ["SYNT_SANDBOX_NETWORK"] = old

    @unittest.skipIf(os.name == "nt", "RLIMIT_FSIZE is POSIX-only")
    def test_file_size_cap_stops_disk_fill(self) -> None:
        mb = cv.SANDBOX_MAX_FILE_MB + 8
        res = cv.run_sandboxed(
            f"with open('big.bin', 'wb') as fh:\n    for _ in range({mb}):\n        fh.write(b'x' * 1048576)\nprint('WROTE')",
            lang="python",
            memory_mb=256,
        )
        self.assertNotIn("WROTE", res["stdout"])
        self.assertNotEqual(res["exitCode"], 0)

    def test_compile_timeout_is_reported_not_hidden(self) -> None:
        self.assertGreater(cv.DEFAULT_COMPILE_TIMEOUT_SEC["rust"], cv.DEFAULT_COMPILE_TIMEOUT_SEC["c"])
        timed_out = {"timedOut": True, "exitCode": -9, "stderr": ""}
        self.assertEqual(cv._compile_failure(timed_out, "Rust", 45.0), "Rust compilation timed out after 45s.")
        crashed = {"timedOut": False, "exitCode": 3, "stderr": "  "}
        self.assertIn("exit code 3", cv._compile_failure(crashed, "C", 15.0))

    @unittest.skipUnless(shutil.which("rustc"), "rustc not installed")
    def test_rust_compiles_with_rustup_toolchain(self) -> None:
        res = cv.run_sandboxed('fn main() { println!("{}", 6 * 7); }', lang="rust", timeout_sec=10)
        self.assertEqual(res["status"], "ok", res.get("stderr"))
        self.assertEqual(res["stdout"].strip(), "42")


if __name__ == "__main__":
    unittest.main()
