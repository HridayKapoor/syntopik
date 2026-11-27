#!/usr/bin/env python3
"""Deterministic CS, Algorithms, ML & Systems Code Verification Engine for Syntopik.

Provides the same zero-hallucination deterministic verification rigor for coding,
algorithms, systems, and data-science practice/teaching that `math_verify.py` and
`chem_physical.py` provide for Physics, Maths, and Chemistry:

  - `run`        : Execute Python, Node/JS, C++, C, Rust, or POSIX sh in an isolated
                   `/tmp` sandbox with process-group timeouts, memory bounds, and
                   stdout/stderr byte caps.
  - `test-cases` : Verify a `modelSolution` and/or learner submission against
                   structured JSON test cases (both function-call `--entry` mode
                   and universal stdin/stdout mode, float tolerance, unordered sets).
  - `complexity` : Empirical doubling-ratio + basis-regression asymptotic runtime
                   classifier (`O(1)`, `O(log N)`, `O(N)`, `O(N log N)`, `O(N^2)`,
                   `O(N^3)`, `O(2^N)`).
  - `ast-check`  : Static Python AST verifier for forbidden imports/calls/constructs,
                   recursion requirements, and loop-nesting depth ceilings.

Pure Python 3.12 standard library only (zero external dependencies).
"""

from __future__ import annotations

import argparse
import ast
import json
import math
import os
import re
import secrets
import shutil
import signal
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

try:
    import resource  # POSIX only
except ImportError:  # pragma: no cover - Windows
    resource = None  # type: ignore[assignment]

LANG_ALIASES: dict[str, str] = {
    "py": "python",
    "python": "python",
    "python3": "python",
    "js": "node",
    "javascript": "node",
    "mjs": "node",
    "node": "node",
    "cpp": "cpp",
    "c++": "cpp",
    "cc": "cpp",
    "cxx": "cpp",
    "c": "c",
    "rs": "rust",
    "rust": "rust",
    "sh": "sh",
    "bash": "sh",
    "shell": "sh",
}

EXT_TO_LANG: dict[str, str] = {
    ".py": "python",
    ".js": "node",
    ".mjs": "node",
    ".cjs": "node",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".c": "c",
    ".rs": "rust",
    ".sh": "sh",
}

COMPLEXITY_ORDER: list[str] = [
    "O(1)",
    "O(log N)",
    "O(N)",
    "O(N log N)",
    "O(N^2)",
    "O(N^3)",
    "O(2^N)",
]

# Max score gap (log-RMSE + Occam penalty) within which an expected complexity class
# is treated as a match for the winning class. Absorbs runner timing jitter.
COMPLEXITY_NOISE_TOLERANCE = 0.02


def _find_tool(names: tuple[str, ...]) -> str | None:
    for name in names:
        hit = shutil.which(name)
        if hit:
            return hit
        for prefix in ("/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", str(Path.home() / ".cargo" / "bin")):
            cand = Path(prefix) / name
            if cand.is_file() and os.access(cand, os.X_OK):
                return str(cand)
    return None


def _normalize_lang(lang: str | None, file_path: Path | None) -> str:
    if lang and lang.strip() and lang.strip().lower() != "auto":
        key = lang.strip().lower()
        if key not in LANG_ALIASES:
            raise ValueError(f"Unsupported language {lang!r}. Choose from: python, node, cpp, c, rust, sh.")
        return LANG_ALIASES[key]
    if file_path is not None:
        ext = file_path.suffix.lower()
        if ext in EXT_TO_LANG:
            return EXT_TO_LANG[ext]
    return "python"


def _read_code_source(code: str | None, file_arg: str | None) -> tuple[str, Path | None]:
    if file_arg:
        p = Path(file_arg).expanduser().resolve()
        if not p.is_file():
            raise FileNotFoundError(f"Source file not found: {p}")
        return p.read_text(encoding="utf-8"), p
    if code == "-":
        return sys.stdin.read(), None
    if code is not None:
        return code, None
    raise ValueError("Provide either --code '<source>' or --file <path>.")


def _sandbox_env(work_dir: Path, memory_mb: int) -> dict[str, str]:
    safe_path = os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin")
    env: dict[str, str] = {
        "PATH": safe_path,
        "HOME": str(work_dir),
        "TMPDIR": str(work_dir),
        "TMP": str(work_dir),
        "TEMP": str(work_dir),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUNBUFFERED": "1",
        "PYTHONNOUSERSITE": "1",
        "LC_ALL": "C.UTF-8",
        "LANG": "C.UTF-8",
    }
    if os.name == "nt":
        # Windows binaries (especially Node.js/V8/libuv & Winsock DLLs) require
        # SYSTEMROOT/WINDIR/COMSPEC/PATHEXT to initialize inside a sandboxed env.
        env["USERPROFILE"] = str(work_dir)
        env["APPDATA"] = str(work_dir)
        env["LOCALAPPDATA"] = str(work_dir)
        for win_key in (
            "SYSTEMROOT",
            "WINDIR",
            "SYSTEMDRIVE",
            "COMSPEC",
            "PATHEXT",
            "PROCESSOR_ARCHITECTURE",
            "NUMBER_OF_PROCESSORS",
            "OS",
        ):
            val = os.environ.get(win_key)
            if val:
                env[win_key] = val
    # rustup's `rustc` proxy resolves its toolchain from RUSTUP_HOME (default ~/.rustup).
    # With HOME pointed at the sandbox dir it finds none and every Rust compile fails,
    # so point it (read-only) at the real toolchain directories.
    real_home = Path.home()
    for key, default_dir in (("RUSTUP_HOME", ".rustup"), ("CARGO_HOME", ".cargo")):
        val = os.environ.get(key)
        if not val and (real_home / default_dir).is_dir():
            val = str(real_home / default_dir)
        if val:
            env[key] = val
    if os.environ.get("RUSTUP_TOOLCHAIN"):
        env["RUSTUP_TOOLCHAIN"] = os.environ["RUSTUP_TOOLCHAIN"]
    if memory_mb > 0:
        env["NODE_OPTIONS"] = f"--max-old-space-size={max(64, int(memory_mb))}"
    return env


def _is_node_cmd(cmd: list[str]) -> bool:
    """Return True when `cmd` invokes Node.js (V8 reserves multi-GB virtual address space on Linux)."""
    if not cmd:
        return False
    exe = Path(cmd[0]).name.lower()
    if exe == "env" and len(cmd) > 1:
        exe = Path(cmd[1]).name.lower()
    return exe in ("node", "nodejs", "node.exe") or exe.startswith("node-")


SANDBOX_MAX_FILE_MB = 64  # largest file a learner program may write inside its temp dir


def _make_preexec_fn(memory_mb: int, *, apply_rlimit_as: bool = True, cpu_seconds: int | None = None):
    """Apply safe POSIX resource limits (skipping RLIMIT_AS on Darwin and Node/V8).

    - RLIMIT_CORE 0: no core dumps
    - RLIMIT_AS: address-space cap on Linux (memory)
    - RLIMIT_FSIZE: caps any single written file (disk-fill guard)
    - RLIMIT_CPU: CPU-time backstop behind the wall-clock timeout, generous
      enough that multi-threaded programs are never cut short before it
    """
    if resource is None or os.name == "nt":
        return None
    is_linux = sys.platform.startswith("linux")
    mem_bytes = max(64, int(memory_mb)) * 1024 * 1024
    fsize_bytes = SANDBOX_MAX_FILE_MB * 1024 * 1024

    def _set(kind: str, value: int) -> None:
        lim = getattr(resource, kind, None)
        if lim is None:
            return
        try:
            _soft, hard = resource.getrlimit(lim)
            if hard != resource.RLIM_INFINITY:
                value = min(value, hard)
            resource.setrlimit(lim, (value, value))
        except Exception:
            pass

    def _limits() -> None:
        _set("RLIMIT_CORE", 0)
        if apply_rlimit_as and is_linux and memory_mb > 0:
            _set("RLIMIT_AS", mem_bytes)
        _set("RLIMIT_FSIZE", fsize_bytes)
        if cpu_seconds and cpu_seconds > 0:
            _set("RLIMIT_CPU", int(cpu_seconds))

    return _limits


_NETWORK_ISOLATION_PROBE: dict[str, Any] = {}


def _network_isolation_prefix() -> list[str]:
    """Return a command prefix that runs the child without network access, or [] if unavailable.

    Linux: `unshare -rn` (unprivileged user + network namespace: only a down
    loopback interface exists). Probed once per process; disabled with
    SYNT_SANDBOX_NETWORK=allow. Other platforms: [] (reported in `sandbox`).
    """
    if os.environ.get("SYNT_SANDBOX_NETWORK", "").strip().lower() == "allow":
        return []
    if not sys.platform.startswith("linux"):
        return []
    if "prefix" not in _NETWORK_ISOLATION_PROBE:
        prefix: list[str] = []
        unshare = shutil.which("unshare")
        if unshare:
            try:
                probe = subprocess.run([unshare, "-rn", "--", "true"], capture_output=True, timeout=3, check=False)
                if probe.returncode == 0:
                    prefix = [unshare, "-rn", "--"]
            except (OSError, subprocess.SubprocessError):
                prefix = []
        _NETWORK_ISOLATION_PROBE["prefix"] = prefix
    return list(_NETWORK_ISOLATION_PROBE["prefix"])


def _windows_job_memory_limit(proc: subprocess.Popen, memory_mb: int) -> Any:
    """Cap a Windows child's committed memory via a Job Object. Returns the job handle or None."""
    if os.name != "nt" or memory_mb <= 0:
        return None
    try:
        import ctypes
        from ctypes import wintypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [(n, ctypes.c_ulonglong) for n in (
                "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

        class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x00000100
        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
        JobObjectExtendedLimitInformation = 9

        k32.CreateJobObjectW.restype = wintypes.HANDLE
        k32.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
        k32.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
        k32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
        k32.CloseHandle.argtypes = (wintypes.HANDLE,)

        job = k32.CreateJobObjectW(None, None)
        if not job:
            return None
        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_PROCESS_MEMORY | JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        info.ProcessMemoryLimit = max(64, int(memory_mb)) * 1024 * 1024
        ok = k32.SetInformationJobObject(job, JobObjectExtendedLimitInformation, ctypes.byref(info), ctypes.sizeof(info))
        if ok and k32.AssignProcessToJobObject(job, wintypes.HANDLE(int(proc._handle))):  # type: ignore[attr-defined]
            return (k32, job)
        k32.CloseHandle(job)
    except Exception:
        return None
    return None


def _truncate_bytes(raw: bytes, cap: int) -> tuple[str, bool]:
    if cap > 0 and len(raw) > cap:
        return raw[:cap].decode("utf-8", errors="replace"), True
    return raw.decode("utf-8", errors="replace"), False


def _drain_stream_capped(stream, cap: int, out_buf: bytearray, truncated_flag: list[bool]) -> None:
    """Read a child pipe in chunks, storing at most `cap` bytes while draining excess to avoid deadlock."""
    try:
        while True:
            chunk = stream.read(8192)
            if not chunk:
                break
            if cap <= 0:
                out_buf.extend(chunk)
            else:
                remaining = cap - len(out_buf)
                if remaining > 0:
                    out_buf.extend(chunk[:remaining])
                    if len(chunk) > remaining:
                        truncated_flag[0] = True
                else:
                    truncated_flag[0] = True
    except Exception:
        pass
    finally:
        try:
            stream.close()
        except Exception:
            pass


def _write_stdin(stream, data: bytes) -> None:
    try:
        if data:
            stream.write(data)
        stream.close()
    except Exception:
        pass


def _exec_process(
    cmd: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    stdin_bytes: bytes = b"",
    timeout_sec: float = 5.0,
    memory_mb: int = 256,
    max_output_bytes: int = 65536,
    apply_rlimit_as: bool | None = None,
) -> dict[str, Any]:
    t0 = time.perf_counter()
    is_node = _is_node_cmd(cmd)
    net_prefix = _network_isolation_prefix()
    if net_prefix:
        cmd = [*net_prefix, *cmd]
    cpu_seconds = int(math.ceil(max(2.0 * float(timeout_sec), float(timeout_sec) + 5.0)))
    sandbox: dict[str, Any] = {
        "networkIsolated": bool(net_prefix),
        "memoryLimit": "none",
        "memoryMb": int(memory_mb),
        "cpuSecondsBackstop": cpu_seconds if os.name != "nt" else None,
        "maxFileMb": SANDBOX_MAX_FILE_MB if os.name != "nt" else None,
    }
    if memory_mb > 0 and is_node:
        sandbox["memoryLimit"] = "node_heap"
    popen_kwargs: dict[str, Any] = {
        "cwd": str(cwd),
        "env": env,
        "stdin": subprocess.PIPE,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
    }
    if os.name == "nt":
        popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        popen_kwargs["start_new_session"] = True
        # Skip RLIMIT_AS for Node/V8 on Linux (V8 reserves multi-GB virtual memory for
        # pointer compression / CodeRange; NODE_OPTIONS=--max-old-space-size caps heap cleanly).
        use_rlimit_as = (not is_node) if apply_rlimit_as is None else bool(apply_rlimit_as)
        preexec = _make_preexec_fn(memory_mb, apply_rlimit_as=use_rlimit_as, cpu_seconds=cpu_seconds)
        if preexec is not None:
            popen_kwargs["preexec_fn"] = preexec
            if use_rlimit_as and memory_mb > 0 and sys.platform.startswith("linux"):
                sandbox["memoryLimit"] = "rlimit_as"

    proc = subprocess.Popen(cmd, **popen_kwargs)
    job = None
    if os.name == "nt" and not is_node:
        job = _windows_job_memory_limit(proc, memory_mb)
        if job is not None:
            sandbox["memoryLimit"] = "job_object"
    out_buf = bytearray()
    err_buf = bytearray()
    out_trunc = [False]
    err_trunc = [False]

    t_in = threading.Thread(target=_write_stdin, args=(proc.stdin, stdin_bytes), daemon=True)
    t_out = threading.Thread(target=_drain_stream_capped, args=(proc.stdout, max_output_bytes, out_buf, out_trunc), daemon=True)
    t_err = threading.Thread(target=_drain_stream_capped, args=(proc.stderr, max_output_bytes, err_buf, err_trunc), daemon=True)
    t_in.start()
    t_out.start()
    t_err.start()

    timed_out = False
    try:
        proc.wait(timeout=max(0.05, float(timeout_sec)))
    except subprocess.TimeoutExpired:
        timed_out = True
        if os.name != "nt":
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        else:
            proc.kill()
        try:
            proc.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            pass

    t_in.join(timeout=0.5)
    t_out.join(timeout=1.0)
    t_err.join(timeout=1.0)

    if job is not None:
        try:
            job[0].CloseHandle(job[1])
        except Exception:
            pass

    elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 2)
    return {
        "sandbox": sandbox,
        "exitCode": -1 if timed_out else int(proc.returncode or 0),
        "timedOut": timed_out,
        "runtimeMs": elapsed_ms,
        "stdout": out_buf.decode("utf-8", errors="replace"),
        "stderr": err_buf.decode("utf-8", errors="replace"),
        "stdoutTruncated": out_trunc[0],
        "stderrTruncated": err_trunc[0],
    }


# rustc -O plus the platform linker is far slower than gcc, and a cold first compile on a
# Windows runner (MSVC link.exe) routinely takes 10-20 s, so Rust gets a larger budget.
DEFAULT_COMPILE_TIMEOUT_SEC: dict[str, float] = {"c": 15.0, "cpp": 15.0, "rust": 45.0}


def _compile_failure(comp: dict[str, Any], compiler: str, timeout_sec: float) -> str:
    if comp["timedOut"]:
        return f"{compiler} compilation timed out after {timeout_sec:g}s."
    return comp["stderr"].strip() or f"{compiler} compilation failed (exit code {comp['exitCode']})."


def prepare_program(code: str, lang: str, work_dir: Path, compile_timeout_sec: float | None = None) -> dict[str, Any]:
    """Stage source into `work_dir` and compile if needed, returning `execCmd` or `error`."""
    env = _sandbox_env(work_dir, memory_mb=512)
    if compile_timeout_sec is None:
        compile_timeout_sec = DEFAULT_COMPILE_TIMEOUT_SEC.get(lang, 15.0)

    if lang == "python":
        src = work_dir / "solution.py"
        src.write_text(code, encoding="utf-8")
        return {"ok": True, "sourcePath": src, "execCmd": [sys.executable, "-B", "-I", str(src)], "compileMs": 0.0}

    if lang == "node":
        node_bin = _find_tool(("node",))
        if not node_bin:
            return {"ok": False, "status": "missing_toolchain", "error": "Node.js binary ('node') not found on PATH."}
        src = work_dir / "solution.js"
        src.write_text(code, encoding="utf-8")
        return {"ok": True, "sourcePath": src, "execCmd": [node_bin, str(src)], "compileMs": 0.0}

    if lang == "sh":
        sh_bin = _find_tool(("bash", "sh"))
        if not sh_bin:
            return {"ok": False, "status": "missing_toolchain", "error": "POSIX shell ('bash'/'sh') not found."}
        src = work_dir / "solution.sh"
        src.write_text(code, encoding="utf-8")
        return {"ok": True, "sourcePath": src, "execCmd": [sh_bin, str(src)], "compileMs": 0.0}

    if lang == "cpp":
        cxx = _find_tool(("g++", "clang++", "c++"))
        if not cxx:
            return {"ok": False, "status": "missing_toolchain", "error": "C++ compiler ('g++'/'clang++') not found."}
        src = work_dir / "solution.cpp"
        exe = work_dir / ("solution.exe" if os.name == "nt" else "solution")
        src.write_text(code, encoding="utf-8")
        comp = _exec_process(
            [cxx, "-O2", "-std=c++20", str(src), "-o", str(exe)],
            cwd=work_dir,
            env=env,
            timeout_sec=compile_timeout_sec,
            memory_mb=1024,
        )
        if comp["timedOut"] or comp["exitCode"] != 0:
            return {
                "ok": False,
                "status": "compile_error",
                "error": _compile_failure(comp, "C++", compile_timeout_sec),
                "compileMs": comp["runtimeMs"],
                "compileStderr": comp["stderr"],
            }
        return {"ok": True, "sourcePath": src, "execCmd": [str(exe)], "compileMs": comp["runtimeMs"]}

    if lang == "c":
        cc = _find_tool(("gcc", "clang", "cc"))
        if not cc:
            return {"ok": False, "status": "missing_toolchain", "error": "C compiler ('gcc'/'clang') not found."}
        src = work_dir / "solution.c"
        exe = work_dir / ("solution.exe" if os.name == "nt" else "solution")
        src.write_text(code, encoding="utf-8")
        comp = _exec_process(
            [cc, "-O2", "-std=c17", str(src), "-o", str(exe), "-lm"],
            cwd=work_dir,
            env=env,
            timeout_sec=compile_timeout_sec,
            memory_mb=1024,
        )
        if comp["timedOut"] or comp["exitCode"] != 0:
            return {
                "ok": False,
                "status": "compile_error",
                "error": _compile_failure(comp, "C", compile_timeout_sec),
                "compileMs": comp["runtimeMs"],
                "compileStderr": comp["stderr"],
            }
        return {"ok": True, "sourcePath": src, "execCmd": [str(exe)], "compileMs": comp["runtimeMs"]}

    if lang == "rust":
        rustc = _find_tool(("rustc",))
        if not rustc:
            return {"ok": False, "status": "missing_toolchain", "error": "Rust compiler ('rustc') not found."}
        src = work_dir / "solution.rs"
        exe = work_dir / ("solution.exe" if os.name == "nt" else "solution")
        src.write_text(code, encoding="utf-8")
        comp = _exec_process(
            [rustc, "-O", str(src), "-o", str(exe)],
            cwd=work_dir,
            env=env,
            timeout_sec=compile_timeout_sec,
            memory_mb=1024,
        )
        if comp["timedOut"] or comp["exitCode"] != 0:
            return {
                "ok": False,
                "status": "compile_error",
                "error": _compile_failure(comp, "Rust", compile_timeout_sec),
                "compileMs": comp["runtimeMs"],
                "compileStderr": comp["stderr"],
            }
        return {"ok": True, "sourcePath": src, "execCmd": [str(exe)], "compileMs": comp["runtimeMs"]}

    return {"ok": False, "status": "unsupported_language", "error": f"Unsupported language: {lang}"}


def run_sandboxed(
    code: str,
    *,
    lang: str = "python",
    stdin_text: str = "",
    prog_args: list[str] | None = None,
    timeout_sec: float = 5.0,
    memory_mb: int = 256,
    max_output_bytes: int = 65536,
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="synt-code-run-") as tmp:
        work_dir = Path(tmp)
        prep = prepare_program(code, lang, work_dir)
        if not prep["ok"]:
            return {
                "ok": False,
                "status": prep["status"],
                "language": lang,
                "exitCode": -1,
                "timedOut": False,
                "compileMs": prep.get("compileMs", 0.0),
                "runtimeMs": 0.0,
                "stdout": "",
                "stderr": prep.get("error", ""),
                "stdoutTruncated": False,
                "stderrTruncated": False,
            }

        env = _sandbox_env(work_dir, memory_mb)
        cmd = [*prep["execCmd"], *(prog_args or [])]
        res = _exec_process(
            cmd,
            cwd=work_dir,
            env=env,
            stdin_bytes=stdin_text.encode("utf-8"),
            timeout_sec=timeout_sec,
            memory_mb=memory_mb,
            max_output_bytes=max_output_bytes,
        )
        if res["timedOut"]:
            status = "timeout"
            ok = False
        elif res["exitCode"] != 0:
            status = "runtime_error"
            ok = False
        else:
            status = "ok"
            ok = True

        return {
            "ok": ok,
            "status": status,
            "language": lang,
            "compileMs": prep.get("compileMs", 0.0),
            **res,
        }


# ---------------------------------------------------------------------------
# Test-Case Comparison & Function-Call Harnesses
# ---------------------------------------------------------------------------


def _values_equal(actual: Any, expected: Any, *, float_tol: float = 1e-6, unordered: bool = False) -> bool:
    if isinstance(actual, bool) or isinstance(expected, bool):
        return type(actual) is type(expected) and actual == expected
    if isinstance(actual, (int, float)) and isinstance(expected, (int, float)):
        if math.isnan(float(actual)) or math.isnan(float(expected)):
            return False
        if math.isinf(float(actual)) or math.isinf(float(expected)):
            return float(actual) == float(expected)
        return abs(float(actual) - float(expected)) <= max(float_tol, float_tol * abs(float(expected)))
    if isinstance(actual, (list, tuple)) and isinstance(expected, (list, tuple)):
        if len(actual) != len(expected):
            return False
        if unordered:
            used = [False] * len(expected)
            for item_a in actual:
                matched = False
                for idx, item_e in enumerate(expected):
                    if not used[idx] and _values_equal(item_a, item_e, float_tol=float_tol, unordered=unordered):
                        used[idx] = True
                        matched = True
                        break
                if not matched:
                    return False
            return True
        return all(_values_equal(a, e, float_tol=float_tol, unordered=False) for a, e in zip(actual, expected))
    if isinstance(actual, dict) and isinstance(expected, dict):
        if set(actual.keys()) != set(expected.keys()):
            return False
        return all(_values_equal(actual[k], expected[k], float_tol=float_tol, unordered=unordered) for k in expected)
    return actual == expected


def _normalize_stdout(s: str, exact_whitespace: bool = False) -> str:
    if exact_whitespace:
        return s
    lines = [ln.rstrip() for ln in s.replace("\r\n", "\n").split("\n")]
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines)


def _stdout_matches(actual_out: str, expected_out: Any, *, float_tol: float, exact_whitespace: bool) -> bool:
    if isinstance(expected_out, (int, float)) and not isinstance(expected_out, bool):
        try:
            act_num = float(actual_out.strip())
            return _values_equal(act_num, expected_out, float_tol=float_tol)
        except ValueError:
            return False
    exp_str = str(expected_out)
    a_norm = _normalize_stdout(actual_out, exact_whitespace=exact_whitespace)
    e_norm = _normalize_stdout(exp_str, exact_whitespace=exact_whitespace)
    if a_norm == e_norm:
        return True
    if not exact_whitespace and float_tol > 0:
        try:
            return _values_equal(float(a_norm), float(e_norm), float_tol=float_tol)
        except ValueError:
            pass
    return False


_PY_FN_RUNNER = r"""
import importlib.util, json, sys, time, traceback

spec_file = sys.argv[1]
payload = json.loads(open(spec_file, "r", encoding="utf-8").read())
mod_spec = importlib.util.spec_from_file_location("user_solution", payload["sourcePath"])
mod = importlib.util.module_from_spec(mod_spec)
mod_spec.loader.exec_module(mod)

entry = payload["entry"]
target = mod
for part in entry.split("."):
    if not hasattr(target, part):
        raise AttributeError(f"Entry '{entry}' not found in solution")
    target = getattr(target, part)
if isinstance(target, type):
    target = target()

args = payload.get("args", [])
kwargs = payload.get("kwargs", {})
t0 = time.perf_counter()
res = target(*args, **kwargs)
dt = round((time.perf_counter() - t0) * 1000.0, 3)
if isinstance(res, tuple):
    res = list(res)
elif isinstance(res, set):
    res = sorted(list(res), key=repr)
print(payload["marker"] + json.dumps({"ok": True, "result": res, "callMs": dt}, default=repr))
"""

_NODE_FN_RUNNER = r"""
const fs = require("fs");
const { performance } = require("perf_hooks");

function resolveEntry(mod, entry) {
  if (!mod || (typeof mod !== "object" && typeof mod !== "function")) return null;
  if (typeof mod === "function" && (mod.name === entry || entry === "default")) return mod;
  const parts = entry.split(".");
  let cur = mod;
  for (const p of parts) {
    if (cur != null && Object.prototype.hasOwnProperty.call(cur, p)) {
      cur = cur[p];
    } else if (cur != null && cur[p] !== undefined) {
      cur = cur[p];
    } else {
      cur = undefined;
      break;
    }
  }
  if (typeof cur === "function") return cur;
  if (mod.default && typeof mod.default === "object" && typeof mod.default[entry] === "function") {
    return mod.default[entry];
  }
  if (typeof mod.default === "function" && (mod.default.name === entry || entry === "default")) {
    return mod.default;
  }
  return null;
}

(async () => {
  const payload = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
  const entry = String(payload.entry || "").trim();
  const rootIdent = entry.split(".")[0];
  let fn = null;

  try {
    const mod = require(payload.sourcePath);
    fn = resolveEntry(mod, entry);
  } catch (_err) {
    fn = null;
  }

  if (typeof fn !== "function") {
    if (!/^[A-Za-z_$][A-Za-z0-9_$]*$/.test(rootIdent)) {
      throw new Error(`Invalid JS entry identifier '${entry}'`);
    }
    const rawCode = fs.readFileSync(payload.sourcePath, "utf8");
    const stripped = rawCode.replace(/^\s*export\s+(?:default\s+)?/gm, "");
    const wrappedCode =
      stripped +
      `\n;module.exports = { ${rootIdent}: (typeof ${rootIdent} !== "undefined" ? ${rootIdent} : undefined) };\n`;
    const wrappedPath = payload.sourcePath + `.wrap_${process.pid}.cjs`;
    fs.writeFileSync(wrappedPath, wrappedCode, "utf8");
    const mod2 = require(wrappedPath);
    fn = resolveEntry(mod2, entry);
  }

  if (typeof fn !== "function") {
    throw new Error(`Entry function '${entry}' not found or not exported`);
  }
  const args = payload.args || [];
  const t0 = performance.now();
  const res = await fn(...args);
  const dt = Math.round((performance.now() - t0) * 1000) / 1000;
  console.log(payload.marker + JSON.stringify({ ok: true, result: res, callMs: dt }));
})().catch((e) => {
  console.error(e && e.stack ? e.stack : String(e));
  process.exit(1);
});
"""


def _load_cases_spec(raw_cases: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    p = Path(raw_cases).expanduser()
    if p.is_file():
        parsed = json.loads(p.read_text(encoding="utf-8"))
    else:
        parsed = json.loads(raw_cases)

    if isinstance(parsed, list):
        return {}, parsed
    if isinstance(parsed, dict) and isinstance(parsed.get("cases"), list):
        meta = {k: v for k, v in parsed.items() if k != "cases"}
        return meta, parsed["cases"]
    raise ValueError("Test cases must be a JSON array or an object with a 'cases' array.")


def run_test_cases(
    code: str,
    cases: list[dict[str, Any]],
    *,
    lang: str = "python",
    entry: str | None = None,
    reference_code: str | None = None,
    timeout_sec: float = 3.0,
    memory_mb: int = 256,
    float_tol: float = 1e-6,
    unordered: bool = False,
    exact_whitespace: bool = False,
    hide_secret: bool = False,
) -> dict[str, Any]:
    if not cases:
        raise ValueError("At least one test case is required.")

    with tempfile.TemporaryDirectory(prefix="synt-code-cases-") as tmp:
        work_dir = Path(tmp)
        prep = prepare_program(code, lang, work_dir)
        if not prep["ok"]:
            return {
                "ok": False,
                "allPassed": False,
                "status": prep["status"],
                "language": lang,
                "passedCount": 0,
                "totalCount": len(cases),
                "error": prep.get("error", "Compilation failed"),
                "results": [],
                "firstFailure": {"caseIndex": 0, "error": prep.get("error", "Compilation failed")},
            }

        ref_dir: Path | None = None
        ref_prep: dict[str, Any] | None = None
        if reference_code is not None:
            ref_dir = work_dir / "ref"
            ref_dir.mkdir()
            ref_prep = prepare_program(reference_code, lang, ref_dir)
            if not ref_prep["ok"]:
                return {
                    "ok": False,
                    "allPassed": False,
                    "status": "reference_compile_error",
                    "language": lang,
                    "passedCount": 0,
                    "totalCount": len(cases),
                    "error": f"Reference modelSolution failed to compile: {ref_prep.get('error')}",
                    "results": [],
                }

        env = _sandbox_env(work_dir, memory_mb)
        py_runner_path = work_dir / "_runner.py"
        node_runner_path = work_dir / "_runner.js"
        if lang == "python":
            py_runner_path.write_text(_PY_FN_RUNNER, encoding="utf-8")
        elif lang == "node":
            node_runner_path.write_text(_NODE_FN_RUNNER, encoding="utf-8")

        def _invoke_fn_case(prep_info: dict[str, Any], fn_name: str, c_args: list[Any], c_kwargs: dict[str, Any], case_idx: int, tag: str) -> dict[str, Any]:
            marker = f"__SYNT_{secrets.token_hex(6)}__:"
            spec_p = work_dir / f"_case_{tag}_{case_idx}.json"
            spec_p.write_text(
                json.dumps(
                    {
                        "sourcePath": str(prep_info["sourcePath"]),
                        "entry": fn_name,
                        "args": c_args,
                        "kwargs": c_kwargs,
                        "marker": marker,
                    }
                ),
                encoding="utf-8",
            )
            if lang == "python":
                cmd = [sys.executable, "-B", "-I", str(py_runner_path), str(spec_p)]
            elif lang == "node":
                node_bin = _find_tool(("node",))
                assert node_bin is not None
                cmd = [node_bin, str(node_runner_path), str(spec_p)]
            else:
                raise ValueError(f"Function-call --entry mode supports python and node (got {lang}); use stdin/expected cases for {lang}.")

            ex = _exec_process(cmd, cwd=work_dir, env=env, timeout_sec=timeout_sec, memory_mb=memory_mb)
            if ex["timedOut"]:
                return {"ok": False, "status": "timeout", "error": f"Time limit exceeded ({timeout_sec}s)", "runtimeMs": ex["runtimeMs"]}
            if ex["exitCode"] != 0:
                return {"ok": False, "status": "runtime_error", "error": ex["stderr"].strip() or f"Exited with {ex['exitCode']}", "runtimeMs": ex["runtimeMs"]}
            marker_line = next((ln for ln in reversed(ex["stdout"].splitlines()) if ln.startswith(marker)), None)
            if not marker_line:
                return {"ok": False, "status": "protocol_error", "error": "Runner did not return JSON result", "runtimeMs": ex["runtimeMs"]}
            parsed_out = json.loads(marker_line[len(marker) :])
            return {"ok": True, "status": "ok", "value": parsed_out["result"], "runtimeMs": parsed_out.get("callMs", ex["runtimeMs"])}

        results: list[dict[str, Any]] = []
        first_failure: dict[str, Any] | None = None
        passed_count = 0

        for idx, case in enumerate(cases):
            cid = str(case.get("id") or case.get("name") or f"case_{idx + 1}")
            hidden = bool(case.get("hidden", False))
            case_unordered = bool(case.get("unordered", unordered))
            case_tol = float(case.get("floatTol", float_tol))
            fn_entry = str(case.get("entry") or entry or "").strip() or None
            use_fn_mode = fn_entry is not None or "args" in case or "kwargs" in case

            if use_fn_mode:
                if not fn_entry:
                    raise ValueError(f"Case '{cid}' specifies 'args'/'kwargs' without an --entry function name.")
                raw_args = case.get("args", [])
                c_args = raw_args if isinstance(raw_args, list) else [raw_args]
                c_kwargs = case.get("kwargs", {}) if isinstance(case.get("kwargs"), dict) else {}

                if "expected" in case:
                    exp_val = case["expected"]
                elif ref_prep is not None:
                    ref_out = _invoke_fn_case(ref_prep, fn_entry, c_args, c_kwargs, idx, "ref")
                    if not ref_out["ok"]:
                        return {
                            "ok": False,
                            "allPassed": False,
                            "status": "reference_runtime_error",
                            "error": f"Reference solution failed on '{cid}': {ref_out.get('error')}",
                            "passedCount": passed_count,
                            "totalCount": len(cases),
                            "results": results,
                        }
                    exp_val = ref_out["value"]
                else:
                    raise ValueError(f"Case '{cid}' requires 'expected' or --reference-file/--reference-code.")

                act_out = _invoke_fn_case(prep, fn_entry, c_args, c_kwargs, idx, "sub")
                if not act_out["ok"]:
                    passed = False
                    row = {
                        "id": cid,
                        "index": idx,
                        "passed": False,
                        "hidden": hidden,
                        "status": act_out["status"],
                        "runtimeMs": act_out["runtimeMs"],
                        "error": act_out["error"],
                    }
                    if not (hidden and hide_secret):
                        row["args"] = c_args
                        row["expected"] = exp_val
                else:
                    passed = _values_equal(act_out["value"], exp_val, float_tol=case_tol, unordered=case_unordered)
                    row = {
                        "id": cid,
                        "index": idx,
                        "passed": passed,
                        "hidden": hidden,
                        "status": "ok" if passed else "wrong_answer",
                        "runtimeMs": act_out["runtimeMs"],
                    }
                    if not (hidden and hide_secret):
                        row["args"] = c_args
                        row["expected"] = exp_val
                        row["actual"] = act_out["value"]
            else:
                stdin_val = str(case.get("stdin") if "stdin" in case else case.get("input", ""))
                if "expected" in case or "stdout" in case:
                    exp_stdout = case["expected"] if "expected" in case else case["stdout"]
                elif ref_prep is not None:
                    ref_ex = _exec_process(
                        ref_prep["execCmd"],
                        cwd=ref_dir or work_dir,
                        env=env,
                        stdin_bytes=stdin_val.encode("utf-8"),
                        timeout_sec=timeout_sec,
                        memory_mb=memory_mb,
                    )
                    if ref_ex["timedOut"] or ref_ex["exitCode"] != 0:
                        return {
                            "ok": False,
                            "allPassed": False,
                            "status": "reference_runtime_error",
                            "error": f"Reference solution failed on '{cid}': {ref_ex['stderr'].strip()}",
                            "passedCount": passed_count,
                            "totalCount": len(cases),
                            "results": results,
                        }
                    exp_stdout = ref_ex["stdout"]
                else:
                    raise ValueError(f"Case '{cid}' requires 'expected'/'stdout' or --reference-file/--reference-code.")

                sub_ex = _exec_process(
                    prep["execCmd"],
                    cwd=work_dir,
                    env=env,
                    stdin_bytes=stdin_val.encode("utf-8"),
                    timeout_sec=timeout_sec,
                    memory_mb=memory_mb,
                )
                if sub_ex["timedOut"] or sub_ex["exitCode"] != 0:
                    passed = False
                    row = {
                        "id": cid,
                        "index": idx,
                        "passed": False,
                        "hidden": hidden,
                        "status": "timeout" if sub_ex["timedOut"] else "runtime_error",
                        "runtimeMs": sub_ex["runtimeMs"],
                        "error": f"Time limit exceeded ({timeout_sec}s)" if sub_ex["timedOut"] else (sub_ex["stderr"].strip() or f"Exited {sub_ex['exitCode']}"),
                    }
                    if not (hidden and hide_secret):
                        row["stdin"] = stdin_val
                        row["expected"] = _normalize_stdout(str(exp_stdout), exact_whitespace)
                else:
                    passed = _stdout_matches(sub_ex["stdout"], exp_stdout, float_tol=case_tol, exact_whitespace=exact_whitespace)
                    row = {
                        "id": cid,
                        "index": idx,
                        "passed": passed,
                        "hidden": hidden,
                        "status": "ok" if passed else "wrong_answer",
                        "runtimeMs": sub_ex["runtimeMs"],
                    }
                    if not (hidden and hide_secret):
                        row["stdin"] = stdin_val
                        row["expected"] = _normalize_stdout(str(exp_stdout), exact_whitespace)
                        row["actual"] = _normalize_stdout(sub_ex["stdout"], exact_whitespace)

            if passed:
                passed_count += 1
            elif first_failure is None:
                first_failure = dict(row)
            results.append(row)

        all_passed = passed_count == len(cases)
        return {
            "ok": all_passed,
            "allPassed": all_passed,
            "status": "passed" if all_passed else "failed",
            "language": lang,
            "compileMs": prep.get("compileMs", 0.0),
            "passedCount": passed_count,
            "totalCount": len(cases),
            "firstFailure": first_failure,
            "results": results,
        }


# ---------------------------------------------------------------------------
# Empirical Complexity Fitter (Doubling-Ratio + Basis Regression)
# ---------------------------------------------------------------------------

_PY_BENCH_HARNESS = r"""
import copy, gc, importlib.util, json, math, random, statistics, sys, time

cfg = json.loads(open(sys.argv[1], "r", encoding="utf-8").read())
mod_spec = importlib.util.spec_from_file_location("user_bench", cfg["sourcePath"])
mod = importlib.util.module_from_spec(mod_spec)
mod_spec.loader.exec_module(mod)

entry = cfg["entry"]
fn = getattr(mod, entry, None)
if fn is None:
    raise AttributeError(f"Function '{entry}' not found")

gen_kind = cfg["generator"]
gen_code = cfg.get("genCode")
custom_gen = eval(gen_code, {"random": random, "math": math}) if gen_code else None

def make_arg(n):
    rng = random.Random(42 + n)
    if custom_gen is not None:
        res = custom_gen(n)
        return res if isinstance(res, tuple) else (res,)
    if gen_kind in ("int", "scalar", "n"):
        return (n,)
    if gen_kind in ("sorted_array", "sorted_list"):
        return (list(range(n)),)
    if gen_kind == "string":
        alphabet = "abcdefghij"
        return ("".join(alphabet[rng.randrange(len(alphabet))] for _ in range(n)),)
    if gen_kind == "matrix":
        side = max(1, int(round(math.sqrt(n))))
        return ([[rng.randint(0, 100) for _ in range(side)] for _ in range(side)],)
    if gen_kind == "graph_sparse":
        edges = [[i, (i + 1) % max(1, n)] for i in range(max(0, n - 1))]
        return (n, edges)
    return ([rng.randint(-100000, 100000) for _ in range(n)],)

def clone_args(proto):
    return tuple(list(a) if isinstance(a, list) else (dict(a) if isinstance(a, dict) else copy.deepcopy(a)) for a in proto)

gc.disable()
measurements = []
repeats = max(3, int(cfg.get("repeats", 5)))
budget_per_size_s = float(cfg.get("perSizeBudgetSec", 0.35))

# Phase 1: Warmup, mutation check & batch calibration per size (respecting per-size budget)
prepared = []
for n in cfg["sizes"]:
    args_proto = make_arg(n)
    args_probe = clone_args(args_proto)
    t_cal0 = time.perf_counter_ns()
    fn(*args_probe)
    cal_ns = max(1, time.perf_counter_ns() - t_cal0)
    mutates = (args_probe != args_proto)

    if cal_ns > int(budget_per_size_s * 1e9):
        prepared.append({"n": n, "proto": args_proto, "mutates": mutates, "batch": 1, "samples": [float(cal_ns)], "overBudget": True})
        break

    batch = 1
    if cal_ns < 50_000:
        batch = min(4000, max(16, int(200_000 // cal_ns)))
        if mutates:
            batch = min(batch, max(1, 131_072 // max(1, n)))

    prepared.append({"n": n, "proto": args_proto, "mutates": mutates, "batch": batch, "samples": [], "overBudget": False})
    if cal_ns * repeats > budget_per_size_s * 1e9:
        break

# Phase 2: Interleave measurement rounds across sizes so transient VM/OS CPU jitter
# cannot systematically skew only the largest N; use lower-quartile (p25) to reject
# one-sided positive scheduling spikes.
for _ in range(repeats):
    for item in prepared:
        if item["overBudget"]:
            continue
        batch = item["batch"]
        args_proto = item["proto"]
        if item["mutates"]:
            inputs = [clone_args(args_proto) for _ in range(batch)]
            t0 = time.perf_counter_ns()
            for call_args in inputs:
                fn(*call_args)
            dt = (time.perf_counter_ns() - t0) / batch
        else:
            t0 = time.perf_counter_ns()
            for _ in range(batch):
                fn(*args_proto)
            dt = (time.perf_counter_ns() - t0) / batch
        item["samples"].append(dt)

for item in prepared:
    s = sorted(item["samples"])
    rep_ns = float(s[len(s) // 4]) if len(s) >= 4 else float(statistics.median(s))
    measurements.append({"n": item["n"], "medianNs": round(rep_ns, 2), "batch": item["batch"]})

print(cfg["marker"] + json.dumps({"measurements": measurements}))
"""

_NODE_BENCH_HARNESS = r"""
const fs = require("fs");

const cfg = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const entry = String(cfg.entry || "").trim();
const rootIdent = entry.split(".")[0];

function resolveEntry(mod, name) {
  if (!mod || (typeof mod !== "object" && typeof mod !== "function")) return null;
  if (typeof mod === "function" && (mod.name === name || name === "default")) return mod;
  let cur = mod;
  for (const part of name.split(".")) {
    if (cur != null && cur[part] !== undefined) cur = cur[part];
    else { cur = undefined; break; }
  }
  if (typeof cur === "function") return cur;
  if (mod.default && typeof mod.default === "object" && typeof mod.default[name] === "function") {
    return mod.default[name];
  }
  return null;
}

let fn = null;
try {
  fn = resolveEntry(require(cfg.sourcePath), entry);
} catch (_e) {
  fn = null;
}
if (typeof fn !== "function") {
  if (!/^[A-Za-z_$][A-Za-z0-9_$]*$/.test(rootIdent)) {
    throw new Error(`Invalid JS entry identifier '${entry}'`);
  }
  const raw = fs.readFileSync(cfg.sourcePath, "utf8").replace(/^\s*export\s+(?:default\s+)?/gm, "");
  const wrapped = cfg.sourcePath + `.bench_${process.pid}.cjs`;
  fs.writeFileSync(wrapped, raw + `\n;module.exports = { ${rootIdent}: (typeof ${rootIdent} !== "undefined" ? ${rootIdent} : undefined) };\n`, "utf8");
  fn = resolveEntry(require(wrapped), entry);
}
if (typeof fn !== "function") {
  throw new Error(`Entry function '${entry}' not found or not exported`);
}

function lcg(seed) {
  let s = (seed >>> 0) || 1;
  return () => {
    s = (Math.imul(1664525, s) + 1013904223) >>> 0;
    return s;
  };
}

function makeArg(n) {
  const gen = cfg.generator || "array";
  const nextRand = lcg(42 + n);
  if (gen === "int" || gen === "scalar" || gen === "n") return [n];
  if (gen === "sorted_array" || gen === "sorted_list") {
    const arr = new Array(n);
    for (let i = 0; i < n; i++) arr[i] = i;
    return [arr];
  }
  if (gen === "string") {
    const alpha = "abcdefghij";
    let s = "";
    for (let i = 0; i < n; i++) s += alpha[nextRand() % alpha.length];
    return [s];
  }
  if (gen === "matrix") {
    const side = Math.max(1, Math.round(Math.sqrt(n)));
    const grid = [];
    for (let r = 0; r < side; r++) {
      const row = new Array(side);
      for (let c = 0; c < side; c++) row[c] = nextRand() % 101;
      grid.push(row);
    }
    return [grid];
  }
  if (gen === "graph_sparse") {
    const edges = [];
    for (let i = 0; i < Math.max(0, n - 1); i++) edges.push([i, (i + 1) % Math.max(1, n)]);
    return [n, edges];
  }
  const arr = new Array(n);
  for (let i = 0; i < n; i++) arr[i] = (nextRand() % 200001) - 100000;
  return [arr];
}

function cloneArgs(proto) {
  return proto.map((a) => (Array.isArray(a) ? (Array.isArray(a[0]) ? a.map((r) => r.slice()) : a.slice()) : a));
}

function median(vals) {
  const s = vals.slice().sort((a, b) => a - b);
  const m = Math.floor(s.length / 2);
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
}

const repeats = Math.max(3, Number(cfg.repeats || 5));
const budgetNs = Number(cfg.perSizeBudgetSec || 0.35) * 1e9;
const measurements = [];

// Bounded JIT warmup on smallest size so V8 tiers up before measuring `cfg.sizes[0]`
if (Array.isArray(cfg.sizes) && cfg.sizes.length > 0) {
  const warmProto = makeArg(cfg.sizes[0]);
  const warmBudgetNs = Math.min(25e6, Math.max(5e6, budgetNs * 0.15));
  const tWarmStart = process.hrtime.bigint();
  for (let w = 0; w < 128; w++) {
    fn(...cloneArgs(warmProto));
    const elapsed = Number(process.hrtime.bigint() - tWarmStart);
    if (w >= 2 && elapsed >= warmBudgetNs) break;
    if (elapsed >= budgetNs * 0.35) break;
  }
}

for (const n of cfg.sizes) {
  const proto = makeArg(n);
  const probe = cloneArgs(proto);
  const tCal0 = process.hrtime.bigint();
  fn(...probe);
  const calNs = Math.max(1, Number(process.hrtime.bigint() - tCal0));
  const mutates = JSON.stringify(probe) !== JSON.stringify(proto);

  if (calNs > budgetNs) {
    measurements.push({ n, medianNs: calNs, batch: 1 });
    break;
  }

  let batch = 1;
  if (calNs < 200000) {
    batch = Math.min(4000, Math.max(4, Math.floor(500000 / calNs)));
    if (mutates) batch = Math.min(batch, Math.max(1, Math.floor(131072 / Math.max(1, n))));
  }

  const samples = [];
  for (let r = 0; r < repeats; r++) {
    if (mutates) {
      const inputs = [];
      for (let b = 0; b < batch; b++) inputs.push(cloneArgs(proto));
      const t0 = process.hrtime.bigint();
      for (let b = 0; b < batch; b++) fn(...inputs[b]);
      samples.push(Number(process.hrtime.bigint() - t0) / batch);
    } else {
      const t0 = process.hrtime.bigint();
      for (let b = 0; b < batch; b++) fn(...proto);
      samples.push(Number(process.hrtime.bigint() - t0) / batch);
    }
  }
  const med = median(samples);
  measurements.push({ n, medianNs: Math.round(med * 100) / 100, batch });
  if (med * repeats > budgetNs) break;
}

console.log(cfg.marker + JSON.stringify({ measurements }));
"""


def _basis_fn(label: str, n: float) -> float:
    if label == "O(1)":
        return 1.0
    if label == "O(log N)":
        return max(1.0, math.log2(max(2.0, n)))
    if label == "O(N)":
        return float(n)
    if label == "O(N log N)":
        return float(n) * max(1.0, math.log2(max(2.0, n)))
    if label == "O(N^2)":
        return float(n) ** 2
    if label == "O(N^3)":
        return float(n) ** 3
    if label == "O(2^N)":
        return float(2.0 ** min(60.0, n))
    return float(n)


def classify_measurements(points: list[dict[str, Any]]) -> dict[str, Any]:
    """Fit empirical `(n, medianNs)` pairs to asymptotic complexity classes."""
    if len(points) < 3:
        raise ValueError("Need at least 3 size measurements to classify asymptotic complexity.")

    ns = [float(p["n"]) for p in points]
    ts = [max(1.0, float(p["medianNs"])) for p in points]

    doubling_ratios: list[float] = []
    step_increments: list[float] = []
    for i in range(len(ns) - 1):
        if ns[i] > 0 and abs((ns[i + 1] / ns[i]) - 2.0) < 0.15:
            doubling_ratios.append(round(ts[i + 1] / ts[i], 3))
        if abs((ns[i + 1] - ns[i]) - 1.0) < 0.01 or abs((ns[i + 1] - ns[i]) - 2.0) < 0.01:
            dn = ns[i + 1] - ns[i]
            step_increments.append(round((ts[i + 1] / ts[i]) ** (1.0 / dn), 3))

    # Flat constant-time guard: if overall max/min across >=16x input span is < 1.55x, it's O(1).
    span_ratio = ns[-1] / max(1.0, ns[0])
    growth_ratio = ts[-1] / max(1.0, ts[0])
    if span_ratio >= 16.0 and growth_ratio < 1.45 and max(ts) / min(ts) < 1.65:
        return {
            "estimatedComplexity": "O(1)",
            "confidence": 0.92,
            "doublingRatios": doubling_ratios,
            "growthRatio": round(growth_ratio, 3),
            "candidates": [{"complexity": "O(1)", "logRmse": 0.05}],
        }

    # Evaluate log-space scale-fit RMSE: log(T_i) ≈ log(c) + log(f(N_i)) + constant overhead floor c0
    candidates: list[dict[str, Any]] = []
    candidates_pool = list(COMPLEXITY_ORDER)
    if max(ns) > 45:
        candidates_pool.remove("O(2^N)")

    # Complexity Occam simplicity penalty (prevents minor jitter from preferring higher-order curves)
    simplicity_penalty = {
        "O(1)": 0.0,
        "O(log N)": 0.03,
        "O(N)": 0.02,
        "O(N log N)": 0.045,
        "O(N^2)": 0.04,
        "O(N^3)": 0.05,
        "O(2^N)": 0.06,
    }

    # Estimate empirical log-log slope d(log T)/d(log N) over the upper half of inputs (where asymptotics dominate)
    mid = max(0, len(ns) // 2 - 1)
    log_ns = [math.log(n) for n in ns[mid:]]
    log_ts = [math.log(t) for t in ts[mid:]]
    mean_x = sum(log_ns) / len(log_ns)
    mean_y = sum(log_ts) / len(log_ts)
    denom = sum((x - mean_x) ** 2 for x in log_ns)
    empirical_exponent = (
        sum((x - mean_x) * (y - mean_y) for x, y in zip(log_ns, log_ts)) / denom
        if denom > 1e-12
        else 0.0
    )

    for label in candidates_pool:
        fs = [_basis_fn(label, n) for n in ns]
        log_ratios = [math.log(t) - math.log(f) for t, f in zip(ts, fs)]
        # Weight larger N higher because startup/constant overhead fades at larger N
        weights = [idx + 1.0 for idx in range(len(ns))]
        w_sum = sum(weights)
        log_c = sum(w * lr for w, lr in zip(weights, log_ratios)) / w_sum
        mse = sum(w * ((math.log(t) - (log_c + math.log(f))) ** 2) for w, t, f in zip(weights, ts, fs)) / w_sum
        rmse = math.sqrt(mse)
        score = rmse + simplicity_penalty.get(label, 0.03)
        candidates.append(
            {
                "complexity": label,
                "logRmse": round(rmse, 4),
                "score": round(score, 4),
            }
        )

    candidates.sort(key=lambda item: item["score"])
    best = candidates[0]["complexity"]
    ambiguous_linear = False

    # Disambiguate O(1) vs O(log N) and O(N) vs O(N log N) using empirical exponent & doubling ratios
    if best in ("O(1)", "O(log N)") and span_ratio >= 16.0:
        if growth_ratio >= 1.65 and ts[-1] > ts[0] * 1.55:
            best = "O(log N)"
        elif growth_ratio < 1.38:
            best = "O(1)"
    elif best in ("O(N)", "O(N log N)") and len(doubling_ratios) >= 3:
        tail_med = statistics.median(doubling_ratios[-3:])
        if (tail_med < 2.09 and empirical_exponent < 1.07) or tail_med < 2.04:
            best = "O(N)"
        elif tail_med >= 2.13 and empirical_exponent >= 1.08:
            best = "O(N log N)"
        else:
            # Over a small input span, O(N) and O(N log N) differ by only a few percent per
            # doubling, which is the same size as runner noise (cache, allocator, CPU clock).
            # Without decisive evidence for the extra log factor, keep the simpler class so a
            # linear solution is never reported as too slow.
            best = "O(N)"
            ambiguous_linear = True
    elif step_increments and statistics.median(step_increments[-2:]) >= 1.72 and max(ns) <= 35:
        best = "O(2^N)"

    gap = (candidates[1]["score"] - candidates[0]["score"]) if len(candidates) > 1 else 0.5
    conf = round(min(0.98, max(0.55, 0.65 + gap * 1.2)), 2)
    if ambiguous_linear:
        conf = 0.55

    return {
        "estimatedComplexity": best,
        "empiricalExponent": round(empirical_exponent, 3),
        "doublingRatios": doubling_ratios,
        "growthRatio": round(growth_ratio, 3),
        "confidence": conf,
        "candidates": candidates[:4],
    }


def analyze_complexity(
    code: str,
    *,
    lang: str = "python",
    entry: str | None = None,
    generator: str = "array",
    gen_code: str | None = None,
    sizes: list[int] | None = None,
    repeats: int = 5,
    timeout_sec: float = 10.0,
    expected: str | None = None,
) -> dict[str, Any]:
    if sizes is None:
        if generator in ("int", "scalar", "n") and re.search(r"\bdef\s+\w+.*:\s*[\s\S]*?\b\w+\s*\(\s*\w+\s*-\s*1\s*\)\s*\+\s*\w+\s*\(\s*\w+\s*-\s*2\s*\)", code):
            sizes = [12, 14, 16, 18, 20, 22]
        else:
            sizes = [128, 256, 512, 1024, 2048, 4096, 8192]

    if entry and lang not in ("python", "node"):
        return {
            "ok": False,
            "status": "unsupported_entry_lang",
            "exitCode": 2,
            "error": (
                f"Function-call --entry '{entry}' in complexity mode is supported for python and node (got '{lang}'). "
                "Omit --entry and read N from argv/stdin for compiled languages."
            ),
        }

    with tempfile.TemporaryDirectory(prefix="synt-code-cmplx-") as tmp:
        work_dir = Path(tmp)
        env = _sandbox_env(work_dir, memory_mb=512)

        if entry and lang in ("python", "node"):
            marker = f"__SYNT_{secrets.token_hex(6)}__:"
            src_path = work_dir / ("solution.py" if lang == "python" else "solution.js")
            src_path.write_text(code, encoding="utf-8")
            cfg_path = work_dir / "_cfg.json"
            cfg_path.write_text(
                json.dumps(
                    {
                        "sourcePath": str(src_path),
                        "entry": entry,
                        "generator": generator,
                        "genCode": gen_code,
                        "sizes": sizes,
                        "repeats": repeats,
                        "perSizeBudgetSec": max(0.15, timeout_sec / max(3, len(sizes))),
                        "marker": marker,
                    }
                ),
                encoding="utf-8",
            )
            if lang == "python":
                bench_path = work_dir / "_bench.py"
                bench_path.write_text(_PY_BENCH_HARNESS, encoding="utf-8")
                bench_cmd = [sys.executable, "-B", "-I", str(bench_path), str(cfg_path)]
            else:
                node_bin = _find_tool(("node",))
                if not node_bin:
                    return {"ok": False, "status": "missing_toolchain", "error": "Node.js binary ('node') not found on PATH."}
                bench_path = work_dir / "_bench.js"
                bench_path.write_text(_NODE_BENCH_HARNESS, encoding="utf-8")
                bench_cmd = [node_bin, str(bench_path), str(cfg_path)]

            ex = _exec_process(
                bench_cmd,
                cwd=work_dir,
                env=env,
                timeout_sec=timeout_sec,
                memory_mb=512,
            )
            if ex["timedOut"] or ex["exitCode"] != 0:
                return {
                    "ok": False,
                    "status": "timeout" if ex["timedOut"] else "runtime_error",
                    "error": ex["stderr"].strip() or f"Benchmark timed out ({timeout_sec}s)",
                }
            marker_line = next((ln for ln in reversed(ex["stdout"].splitlines()) if ln.startswith(marker)), None)
            if not marker_line:
                return {"ok": False, "status": "protocol_error", "error": "Benchmark harness produced no output"}
            points = json.loads(marker_line[len(marker) :])["measurements"]
        else:
            prep = prepare_program(code, lang, work_dir)
            if not prep["ok"]:
                return {"ok": False, "status": prep["status"], "error": prep.get("error", "Compile failed")}
            points = []
            for n in sizes:
                samples = []
                for _ in range(max(2, repeats)):
                    ex = _exec_process(
                        [*prep["execCmd"], str(n)],
                        cwd=work_dir,
                        env={**env, "SYNT_N": str(n)},
                        stdin_bytes=f"{n}\n".encode("utf-8"),
                        timeout_sec=max(0.5, timeout_sec / max(2, len(sizes))),
                    )
                    if ex["timedOut"] or ex["exitCode"] != 0:
                        return {
                            "ok": False,
                            "status": "timeout" if ex["timedOut"] else "runtime_error",
                            "error": ex["stderr"].strip() or f"Failed at N={n}",
                        }
                    samples.append(ex["runtimeMs"] * 1e6)
                points.append({"n": n, "medianNs": round(statistics.median(samples), 2), "batch": 1})

    fit = classify_measurements(points)
    meets_expected: bool | None = None
    within_tolerance = False
    if expected:
        exp_norm = expected.strip()
        if exp_norm in COMPLEXITY_ORDER and fit["estimatedComplexity"] in COMPLEXITY_ORDER:
            meets_expected = COMPLEXITY_ORDER.index(fit["estimatedComplexity"]) <= COMPLEXITY_ORDER.index(exp_norm)
        else:
            meets_expected = fit["estimatedComplexity"].lower() == exp_norm.lower()
        # Adjacent classes (O(N) vs O(N log N)) are often indistinguishable on a noisy
        # machine. If the expected class fits essentially as well as the winner, do not
        # tell the learner their solution is too slow.
        if meets_expected is False:
            scores = [c.get("score") for c in fit.get("candidates") or [] if isinstance(c.get("score"), (int, float))]
            exp_score = next(
                (c.get("score") for c in fit.get("candidates") or [] if str(c.get("complexity")).lower() == exp_norm.lower()),
                None,
            )
            if scores and isinstance(exp_score, (int, float)) and exp_score <= min(scores) + COMPLEXITY_NOISE_TOLERANCE:
                meets_expected = True
                within_tolerance = True

    return {
        "ok": True if meets_expected is None else meets_expected,
        "status": "ok" if (meets_expected is not False) else "complexity_exceeded",
        "language": lang,
        "entry": entry,
        "generator": generator,
        "measurements": points,
        **fit,
        "expectedComplexity": expected,
        "meetsExpected": meets_expected,
        "withinNoiseTolerance": within_tolerance,
    }


# ---------------------------------------------------------------------------
# Static Python AST Structural & Constraint Checker
# ---------------------------------------------------------------------------

_LOOP_NODES = (ast.For, ast.AsyncFor, ast.While, ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)


def check_python_ast(
    code: str,
    *,
    forbid_imports: list[str] | None = None,
    forbid_calls: list[str] | None = None,
    forbid_nodes: list[str] | None = None,
    forbid_loops: bool = False,
    require_loops: bool = False,
    require_recursion: bool = False,
    forbid_recursion: bool = False,
    max_loop_nesting: int | None = None,
) -> dict[str, Any]:
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return {
            "ok": False,
            "validSyntax": False,
            "violations": [
                {
                    "rule": "syntax",
                    "line": exc.lineno or 1,
                    "detail": f"SyntaxError: {exc.msg}",
                }
            ],
            "metrics": {},
        }

    bad_imports = {s.strip() for s in (forbid_imports or []) if s.strip()}
    bad_calls = {s.strip() for s in (forbid_calls or []) if s.strip()}
    bad_nodes = {s.strip() for s in (forbid_nodes or []) if s.strip()}

    violations: list[dict[str, Any]] = []
    imports_found: set[str] = set()
    calls_found: set[str] = set()
    functions_found: list[str] = []
    classes_found: list[str] = []
    loop_count = 0
    max_depth_seen = 0

    call_graph: dict[str, set[str]] = {}

    def _call_name(node: ast.AST) -> str | None:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            parent = _call_name(node.value)
            return f"{parent}.{node.attr}" if parent else node.attr
        return None

    def _walk(node: ast.AST, loop_depth: int, cur_fn: str | None) -> None:
        nonlocal loop_count, max_depth_seen
        ntype = type(node).__name__
        lineno = getattr(node, "lineno", None)

        if ntype in bad_nodes:
            violations.append({"rule": "forbid_node", "node": ntype, "line": lineno, "detail": f"Forbidden AST construct '{ntype}' at line {lineno}"})

        next_depth = loop_depth
        if isinstance(node, _LOOP_NODES):
            loop_count += 1
            next_depth = loop_depth + 1
            if next_depth > max_depth_seen:
                max_depth_seen = next_depth
            if forbid_loops:
                violations.append({"rule": "forbid_loops", "node": ntype, "line": lineno, "detail": f"Loop/comprehension '{ntype}' is forbidden at line {lineno}"})

        if isinstance(node, ast.Import):
            for alias in node.names:
                root_mod = alias.name.split(".")[0]
                imports_found.add(alias.name)
                if alias.name in bad_imports or root_mod in bad_imports:
                    violations.append({"rule": "forbid_import", "import": alias.name, "line": lineno, "detail": f"Forbidden import '{alias.name}' at line {lineno}"})
        elif isinstance(node, ast.ImportFrom):
            mod_name = node.module or ""
            root_mod = mod_name.split(".")[0] if mod_name else ""
            imports_found.add(mod_name)
            for alias in node.names:
                full = f"{mod_name}.{alias.name}" if mod_name else alias.name
                if mod_name in bad_imports or root_mod in bad_imports or alias.name in bad_imports or full in bad_imports:
                    violations.append({"rule": "forbid_import", "import": full, "line": lineno, "detail": f"Forbidden import '{full}' at line {lineno}"})

        if isinstance(node, ast.Call):
            cname = _call_name(node.func)
            if cname:
                short = cname.split(".")[-1]
                calls_found.add(cname)
                if cname in bad_calls or short in bad_calls:
                    violations.append({"rule": "forbid_call", "call": cname, "line": lineno, "detail": f"Forbidden call '{cname}()' at line {lineno}"})
                if cname == "__import__" and bad_imports:
                    violations.append({"rule": "forbid_import", "call": "__import__", "line": lineno, "detail": f"Dynamic __import__() forbidden when --forbid-imports is active (line {lineno})"})
                if cur_fn is not None:
                    call_graph.setdefault(cur_fn, set()).add(short if short in call_graph else cname)
                    if short != cname:
                        call_graph[cur_fn].add(short)

        next_fn = cur_fn
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions_found.append(node.name)
            call_graph.setdefault(node.name, set())
            next_fn = node.name
        elif isinstance(node, ast.ClassDef):
            classes_found.append(node.name)

        for child in ast.iter_child_nodes(node):
            _walk(child, next_depth, next_fn)

    _walk(tree, 0, None)

    # Detect direct and mutual recursion over defined functions
    defined_set = set(functions_found)
    recursive_fns: list[str] = []
    for fn in functions_found:
        visited: set[str] = set()
        stack = list(call_graph.get(fn, set()) & defined_set)
        while stack:
            nxt = stack.pop()
            if nxt == fn:
                recursive_fns.append(fn)
                break
            if nxt not in visited:
                visited.add(nxt)
                stack.extend((call_graph.get(nxt, set()) & defined_set) - visited)

    if require_loops and loop_count == 0:
        violations.append({"rule": "require_loops", "line": 1, "detail": "Expected an iterative loop construct, found none."})
    if require_recursion and not recursive_fns:
        violations.append({"rule": "require_recursion", "line": 1, "detail": "Expected a recursive function definition, found none."})
    if forbid_recursion and recursive_fns:
        violations.append({"rule": "forbid_recursion", "line": 1, "detail": f"Recursion is forbidden (found recursive function(s): {', '.join(recursive_fns)})."})
    if max_loop_nesting is not None and max_depth_seen > max_loop_nesting:
        violations.append(
            {
                "rule": "max_loop_nesting",
                "line": 1,
                "detail": f"Maximum loop nesting depth {max_depth_seen} exceeds ceiling {max_loop_nesting}.",
            }
        )

    return {
        "ok": len(violations) == 0,
        "validSyntax": True,
        "violations": violations,
        "metrics": {
            "functions": functions_found,
            "classes": classes_found,
            "imports": sorted(i for i in imports_found if i),
            "calls": sorted(calls_found),
            "loopCount": loop_count,
            "maxLoopNesting": max_depth_seen,
            "recursiveFunctions": recursive_fns,
            "lineCount": len(code.splitlines()),
        },
    }


def _split_csv(val: str | None) -> list[str]:
    if not val:
        return []
    return [part.strip() for part in val.split(",") if part.strip()]


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="Emit machine-readable JSON output")

    parser = argparse.ArgumentParser(
        prog="code_verify.py",
        description="Syntopik deterministic CS / Algorithms / ML / Systems code sandbox & verifier.",
        parents=[common],
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    # 1. run
    p_run = sub.add_parser("run", parents=[common], help="Run code in an isolated /tmp sandbox with timeout & caps")
    p_run.add_argument("--file", help="Path to source file (.py, .js, .cpp, .c, .rs, .sh)")
    p_run.add_argument("--code", help="Inline source code string (or '-' to read from stdin)")
    p_run.add_argument("--lang", default="auto", help="Language: auto|python|node|cpp|c|rust|sh (default: auto)")
    p_run.add_argument("--stdin", default="", help="Standard input text passed to the program")
    p_run.add_argument("--stdin-file", help="Path to file used as standard input")
    p_run.add_argument("--args", nargs="*", default=None, help="CLI arguments forwarded to the sandboxed program")
    p_run.add_argument("--timeout", type=float, default=5.0, help="Execution timeout in seconds (default: 5.0)")
    p_run.add_argument("--memory-mb", type=int, default=256, help="Memory cap in MiB (default: 256)")
    p_run.add_argument("--max-output-bytes", type=int, default=65536, help="Stdout/stderr cap in bytes (default: 65536)")

    # 2. test-cases
    p_tc = sub.add_parser("test-cases", parents=[common], help="Verify modelSolution or learner code against JSON test cases")
    p_tc.add_argument("--file", help="Path to solution file")
    p_tc.add_argument("--code", help="Inline solution code")
    p_tc.add_argument("--lang", default="auto", help="Language: auto|python|node|cpp|c|rust|sh")
    p_tc.add_argument("--cases", required=True, help="JSON string or path to .json file of test cases")
    p_tc.add_argument("--entry", default=None, help="Function/method name to invoke for function-call test cases")
    p_tc.add_argument("--reference-file", help="Optional verified reference solution file to compute expected outputs")
    p_tc.add_argument("--reference-code", help="Optional inline verified reference solution code")
    p_tc.add_argument("--timeout", type=float, default=3.0, help="Per-case timeout in seconds (default: 3.0)")
    p_tc.add_argument("--memory-mb", type=int, default=256, help="Memory cap in MiB (default: 256)")
    p_tc.add_argument("--float-tol", type=float, default=1e-6, help="Floating-point comparison tolerance (default: 1e-6)")
    p_tc.add_argument("--unordered", action="store_true", help="Compare list outputs ignoring element order")
    p_tc.add_argument("--exact-whitespace", action="store_true", help="Require exact trailing whitespace in stdout mode")
    p_tc.add_argument("--hide-secret", action="store_true", help="Omit args/expected/actual details on cases marked hidden:true")

    # 3. complexity
    p_cx = sub.add_parser("complexity", parents=[common], help="Empirical doubling-ratio asymptotic complexity classifier")
    p_cx.add_argument("--file", help="Path to source file")
    p_cx.add_argument("--code", help="Inline source code")
    p_cx.add_argument("--lang", default="auto", help="Language (default: python)")
    p_cx.add_argument("--entry", default=None, help="Python function name to benchmark (e.g., solve, sort_arr)")
    p_cx.add_argument(
        "--generator",
        choices=["array", "random_list", "sorted_array", "sorted_list", "int", "scalar", "string", "matrix", "graph_sparse"],
        default="array",
        help="Input generator scaling with N (default: array)",
    )
    p_cx.add_argument("--gen-code", default=None, help="Custom Python lambda `lambda n: (arg1, ...)` returning call tuple")
    p_cx.add_argument("--sizes", default=None, help="Comma-separated input sizes N (e.g., '128,256,512,1024,2048,4096')")
    p_cx.add_argument("--repeats", type=int, default=5, help="Timing repeats per input size (default: 5)")
    p_cx.add_argument("--timeout", type=float, default=10.0, help="Total benchmark timeout in seconds (default: 10.0)")
    p_cx.add_argument("--expect", default=None, help="Optional expected upper complexity bound (e.g., 'O(N log N)')")

    # 4. ast-check
    p_ast = sub.add_parser("ast-check", parents=[common], help="Static Python AST check for forbidden imports, loops, or recursion")
    p_ast.add_argument("--file", help="Path to Python source file")
    p_ast.add_argument("--code", help="Inline Python source code")
    p_ast.add_argument("--forbid-imports", default=None, help="Comma-separated forbidden modules/symbols")
    p_ast.add_argument("--forbid-calls", default=None, help="Comma-separated forbidden function calls (e.g., 'sorted,eval')")
    p_ast.add_argument("--forbid-nodes", default=None, help="Comma-separated forbidden AST node names (e.g., 'While,ListComp')")
    p_ast.add_argument("--forbid-loops", action="store_true", help="Disallow For/While/comprehension loops")
    p_ast.add_argument("--require-loops", action="store_true", help="Require at least one iterative loop")
    p_ast.add_argument("--require-recursion", action="store_true", help="Require at least one recursive function")
    p_ast.add_argument("--forbid-recursion", action="store_true", help="Disallow direct or mutual recursion")
    p_ast.add_argument("--max-loop-nesting", type=int, default=None, help="Maximum allowed static loop nesting depth")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    as_json = bool(getattr(args, "json", False))

    try:
        if args.cmd == "run":
            code, src_path = _read_code_source(args.code, args.file)
            lang = _normalize_lang(args.lang, src_path)
            stdin_text = Path(args.stdin_file).expanduser().read_text(encoding="utf-8") if args.stdin_file else args.stdin
            res = run_sandboxed(
                code,
                lang=lang,
                stdin_text=stdin_text,
                prog_args=args.args,
                timeout_sec=args.timeout,
                memory_mb=args.memory_mb,
                max_output_bytes=args.max_output_bytes,
            )
            if as_json:
                print(json.dumps(res, indent=2))
            else:
                print(f"[{res['status'].upper()}] {lang} · exit={res['exitCode']} · {res['runtimeMs']} ms")
                if res["stdout"]:
                    sys.stdout.write(res["stdout"] if res["stdout"].endswith("\n") else res["stdout"] + "\n")
                if res["stderr"]:
                    sys.stderr.write(res["stderr"] if res["stderr"].endswith("\n") else res["stderr"] + "\n")
            return 0 if res["ok"] else 1

        if args.cmd == "test-cases":
            code, src_path = _read_code_source(args.code, args.file)
            lang = _normalize_lang(args.lang, src_path)
            meta, cases = _load_cases_spec(args.cases)
            ref_code: str | None = None
            if args.reference_file or args.reference_code:
                ref_code, _ = _read_code_source(args.reference_code, args.reference_file)
            entry = args.entry or meta.get("entry")
            res = run_test_cases(
                code,
                cases,
                lang=lang,
                entry=entry,
                reference_code=ref_code,
                timeout_sec=float(meta.get("timeout", args.timeout)),
                memory_mb=args.memory_mb,
                float_tol=float(meta.get("floatTol", args.float_tol)),
                unordered=bool(meta.get("unordered", args.unordered)),
                exact_whitespace=bool(meta.get("exactWhitespace", args.exact_whitespace)),
                hide_secret=args.hide_secret,
            )
            if as_json:
                print(json.dumps(res, indent=2))
            else:
                print(f"Test Cases: {res['passedCount']}/{res['totalCount']} passed ({res['status']})")
                if res.get("firstFailure"):
                    ff = res["firstFailure"]
                    print(f"  First failure [{ff.get('id', ff.get('caseIndex'))}]: {ff.get('status')} {ff.get('error', '')}")
            return 0 if res["allPassed"] else 1

        if args.cmd == "complexity":
            code, src_path = _read_code_source(args.code, args.file)
            lang = _normalize_lang(args.lang, src_path)
            sizes = [int(x.strip()) for x in args.sizes.split(",") if x.strip()] if args.sizes else None
            res = analyze_complexity(
                code,
                lang=lang,
                entry=args.entry,
                generator=args.generator,
                gen_code=args.gen_code,
                sizes=sizes,
                repeats=args.repeats,
                timeout_sec=args.timeout,
                expected=args.expect,
            )
            if as_json:
                print(json.dumps(res, indent=2))
            else:
                if res.get("ok") or res.get("estimatedComplexity"):
                    print(
                        f"Complexity: {res.get('estimatedComplexity')} "
                        f"(confidence={res.get('confidence')}, doubling={res.get('doublingRatios')})"
                    )
                else:
                    print(f"Complexity check failed: {res.get('error')}", file=sys.stderr)
            return 0 if res.get("ok") else int(res.get("exitCode") or 1)

        if args.cmd == "ast-check":
            code, _ = _read_code_source(args.code, args.file)
            res = check_python_ast(
                code,
                forbid_imports=_split_csv(args.forbid_imports),
                forbid_calls=_split_csv(args.forbid_calls),
                forbid_nodes=_split_csv(args.forbid_nodes),
                forbid_loops=args.forbid_loops,
                require_loops=args.require_loops,
                require_recursion=args.require_recursion,
                forbid_recursion=args.forbid_recursion,
                max_loop_nesting=args.max_loop_nesting,
            )
            if as_json:
                print(json.dumps(res, indent=2))
            else:
                if res["ok"]:
                    m = res["metrics"]
                    print(f"AST Check OK: {len(m['functions'])} fn(s), loops={m['loopCount']} (maxDepth={m['maxLoopNesting']}), recursive={m['recursiveFunctions']}")
                else:
                    print(f"AST Check FAILED ({len(res['violations'])} violation(s)):")
                    for v in res["violations"]:
                        print(f"  - {v['detail']}")
            return 0 if res["ok"] else 1

    except Exception as exc:  # noqa: BLE001
        err_payload = {"ok": False, "status": "error", "command": args.cmd, "error": str(exc)}
        if as_json:
            print(json.dumps(err_payload, indent=2))
        else:
            print(f"Error: {exc}", file=sys.stderr)
        return 1

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
