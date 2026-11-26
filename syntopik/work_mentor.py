"""Anti-Vibe-Coding Work Mentor & Git-Diff Mastery Crystallizer for Syntopik v3.0.

Inspects recent git diffs (or supplied unified diff text) using Python `ast` and
deterministic structural patterns to detect real bug/invariant classes
(`off_by_one`, `null_or_edge_guard`, `aliasing_or_mutation`, `concurrency_lock`,
`complexity_loop`, `error_handling`), then crystallizes:
  (a) a Blunder FEN Fork (`knowledge-graph/forks.jsonl`),
  (b) a candidate Counterexample Arena (`knowledge-graph/arenas.jsonl`), and
  (c) a linked invariant proposition (`knowledge-graph/propositions.jsonl`, deduped).

Also manages Mentor Mode (`set_mentor_mode`) and non-annoying mastery gating
(`evaluate_mentor_gate`: silent passthrough for `solid` T3+ concepts; 20s
micro-prediction prompt ONLY when touching `shaky` / `misconception` concepts).

Pure Python 3.12+ standard library only (zero external dependencies).
"""

from __future__ import annotations

import ast
import hashlib
import re
import subprocess
from pathlib import Path
from typing import Any

from syntopik.forks import capture_fork
from syntopik.paths import resolve_kg_dir, resolve_workspace
from syntopik.schemas import now_iso, slugify
from syntopik.storage import (
    load_json,
    load_jsonl,
    save_json_atomic,
    save_jsonl_atomic,
    workspace_lock,
)

__all__ = [
    "BUG_CLASSES",
    "MENTOR_CONFIG_FILENAME",
    "validate_git_ref",
    "debrief_git_diff",
    "set_mentor_mode",
    "evaluate_mentor_gate",
]

MENTOR_CONFIG_FILENAME = "mentor-config.json"

BUG_CLASSES: tuple[str, ...] = (
    "off_by_one",
    "null_or_edge_guard",
    "aliasing_or_mutation",
    "concurrency_lock",
    "complexity_loop",
    "error_handling",
)

# Strict safe git revision regex: never starts with '-', no ':', backslash, or whitespace/control chars.
_SAFE_GIT_REF_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._/~^@{}\-]*$")

_CLASS_META: dict[str, dict[str, str]] = {
    "off_by_one": {
        "label": "Off-by-One / Loop & Slice Bounds",
        "category": "off_by_one",
        "invariant": "State half-open [lo, hi) vs closed [lo, hi] loop/index bounds explicitly and check 0/1/2-element boundaries.",
        "refutation": "An off-by-one boundary either drops the terminal element, overruns index len(a), or stalls binary search on 2-element slices.",
        "arenaClaim": "Boundary condition must hold on empty, singleton, and endpoint inputs without index drift.",
    },
    "null_or_edge_guard": {
        "label": "Null / Empty / Zero-Edge Guard",
        "category": "edge_case_missed",
        "invariant": "Guard None/null/undefined, empty collections, and zero denominators at entry before dereferencing or dividing.",
        "refutation": "Omitting the empty/null/zero guard triggers a runtime exception or silent NaN on degenerate boundary inputs.",
        "arenaClaim": "Function handles empty, null, or zero boundary inputs safely without throwing or corrupting output.",
    },
    "aliasing_or_mutation": {
        "label": "Aliasing & Shared Mutable State",
        "category": "state_mutation_bug",
        "invariant": "Clone shared mutable containers before storing or mutating, and never use mutable literals as default arguments.",
        "refutation": "Mutating an aliased reference or mutable default silently corrupts callers and prior snapshots across invocations.",
        "arenaClaim": "Mutating returned state or invoking repeatedly must not leak across aliased references.",
    },
    "concurrency_lock": {
        "label": "Concurrency / Critical-Section Locking",
        "category": "state_mutation_bug",
        "invariant": "Protect shared read-modify-write state transitions with an explicit lock/atomic boundary to prevent race windows.",
        "refutation": "Unprotected check-then-act sequences interleave under concurrent threads/tasks and lose updates.",
        "arenaClaim": "Concurrent updates must preserve linearizability without lost increments or torn state.",
    },
    "complexity_loop": {
        "label": "Asymptotic Complexity / Hidden Linear Scan",
        "category": "complexity_trap",
        "invariant": "Avoid hidden O(N) membership scans, list.pop(0), or quadratic string concatenation inside hot loops; use hash sets/deques/joins.",
        "refutation": "Linear scans or copies inside a loop degrade O(N) algorithms into O(N^2) blowups under realistic input sizes.",
        "arenaClaim": "Lookup/pop operations inside the main loop stay O(1) amortized rather than O(N).",
    },
    "error_handling": {
        "label": "Error Handling & Failure Isolation",
        "category": "condition_missed",
        "invariant": "Catch narrow, expected exception types, enforce timeouts/resource cleanup via context managers, and never swallow failures silently.",
        "refutation": "Bare except or unguarded I/O/subprocess calls mask real bugs or leak open descriptors on failure paths.",
        "arenaClaim": "Failure paths raise/handle typed errors cleanly without masking unrelated faults.",
    },
}


# ---------------------------------------------------------------------------
# Safe git ref validation & diff parser
# ---------------------------------------------------------------------------


def validate_git_ref(ref: str) -> str:
    """Validate a git revision/range string, rejecting flag injection, `:`, and path traversal."""
    if not isinstance(ref, str):
        raise ValueError("Git ref must be a string.")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in ref):
        raise ValueError(f"Rejected git ref {ref!r}: control characters are disallowed.")
    cleaned = ref.strip()
    if not cleaned:
        raise ValueError("Git ref cannot be empty.")
    if cleaned.startswith("-"):
        raise ValueError(f"Rejected git ref {cleaned!r}: leading '-' flag injection is disallowed.")
    if any(ch.isspace() for ch in cleaned):
        raise ValueError(f"Rejected git ref {cleaned!r}: whitespace is disallowed.")
    if ":" in cleaned or "\\" in cleaned:
        raise ValueError(f"Rejected git ref {cleaned!r}: ':' and backslashes are disallowed.")
    if (
        cleaned.startswith("..")
        or cleaned.endswith("..")
        or "../" in cleaned
        or "/.." in cleaned
        or "...." in cleaned
    ):
        raise ValueError(f"Rejected git ref {cleaned!r}: path traversal or malformed '..' sequence is disallowed.")
    if not _SAFE_GIT_REF_RE.fullmatch(cleaned):
        raise ValueError(f"Rejected git ref {cleaned!r}: contains disallowed characters.")
    if ".." in cleaned:
        parts = re.split(r"\.\.\.?", cleaned)
        if len(parts) != 2 or any(
            not p or p.startswith(("-", ".", "/")) or p.endswith((".", "/")) or ".." in p
            for p in parts
        ):
            raise ValueError(f"Rejected git ref range {cleaned!r}.")
    return cleaned


def _parse_unified_diff(diff_text: str) -> list[dict[str, Any]]:
    """Parse a unified git diff into file-level hunk records."""
    files: list[dict[str, Any]] = []
    current_file: dict[str, Any] | None = None
    current_hunk: dict[str, Any] | None = None

    for line in str(diff_text or "").splitlines():
        if line.startswith("diff --git "):
            current_hunk = None
            m = re.search(r" b/(.+)$", line)
            fpath = m.group(1).strip() if m else "unknown"
            current_file = {"path": fpath, "hunks": []}
            files.append(current_file)
            continue
        if line.startswith("+++ "):
            raw_p = line[4:].strip()
            fpath = raw_p[2:] if raw_p.startswith("b/") else raw_p
            if current_file is None:
                current_file = {"path": fpath, "hunks": []}
                files.append(current_file)
            elif fpath and fpath != "/dev/null":
                current_file["path"] = fpath
            continue
        if line.startswith("--- "):
            continue
        if line.startswith("@@ "):
            if current_file is None:
                current_file = {"path": "patch.py", "hunks": []}
                files.append(current_file)
            current_hunk = {
                "header": line,
                "context": [],
                "removed": [],
                "added": [],
            }
            current_file["hunks"].append(current_hunk)
            continue
        if current_hunk is None:
            continue
        if line.startswith("-") and not line.startswith("---"):
            current_hunk["removed"].append(line[1:])
        elif line.startswith("+") and not line.startswith("+++"):
            current_hunk["added"].append(line[1:])
        elif line.startswith(" "):
            current_hunk["context"].append(line[1:])
        elif line.startswith("\\ No newline"):
            continue

    return files


# ---------------------------------------------------------------------------
# Python AST + deterministic structural bug/invariant detector
# ---------------------------------------------------------------------------


def _try_parse_python_snippet(lines: list[str]) -> ast.AST | None:
    if not lines:
        return None
    non_empty = [ln for ln in lines if ln.strip()]
    if not non_empty:
        return None
    min_indent = min(len(ln) - len(ln.lstrip(" ")) for ln in non_empty)
    dedented = "\n".join(ln[min_indent:] if len(ln) >= min_indent else ln for ln in lines)
    try:
        return ast.parse(dedented)
    except SyntaxError:
        # Wrap bare block statements inside a dummy function body
        indented = "\n".join("    " + ln for ln in dedented.splitlines())
        try:
            return ast.parse(f"def _synt_hunk():\n{indented}\n")
        except SyntaxError:
            return None


def _ast_signals(tree: ast.AST | None) -> dict[str, Any]:
    if tree is None:
        return {
            "comp_ops": set(),
            "has_none_check": False,
            "has_len_zero": False,
            "has_mutable_default": False,
            "has_copy_call": False,
            "has_bare_except": False,
            "has_typed_except": False,
            "has_with_lock": False,
            "has_set_or_deque": False,
            "func_names": [],
        }
    comp_ops: set[str] = set()
    has_none_check = False
    has_len_zero = False
    has_mutable_default = False
    has_copy_call = False
    has_bare_except = False
    has_typed_except = False
    has_with_lock = False
    has_set_or_deque = False
    func_names: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name != "_synt_hunk":
            func_names.append(node.name)
            for d in list(node.args.defaults) + [x for x in node.args.kw_defaults if x is not None]:
                if isinstance(d, (ast.List, ast.Dict, ast.Set)):
                    has_mutable_default = True
        elif isinstance(node, ast.Compare):
            for op in node.ops:
                comp_ops.add(type(op).__name__)
            if any(isinstance(c, ast.Constant) and c.value is None for c in node.comparators):
                has_none_check = True
            if isinstance(node.left, ast.Call) and isinstance(node.left.func, ast.Name) and node.left.func.id == "len":
                if any(isinstance(c, ast.Constant) and c.value == 0 for c in node.comparators):
                    has_len_zero = True
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Attribute) and node.func.attr in ("copy", "deepcopy"):
                has_copy_call = True
            if isinstance(node.func, ast.Name) and node.func.id in ("set", "deque", "frozenset"):
                has_set_or_deque = True
            if isinstance(node.func, ast.Name) and node.func.id in ("list", "dict", "copy", "deepcopy"):
                has_copy_call = True
        elif isinstance(node, ast.ExceptHandler):
            if node.type is None:
                has_bare_except = True
            else:
                has_typed_except = True
        elif isinstance(node, ast.With):
            for item in node.items:
                src = ast.unparse(item.context_expr).lower() if hasattr(ast, "unparse") else ""
                if any(tok in src for tok in ("lock", "mutex", "semaphore", "rlock")):
                    has_with_lock = True

    return {
        "comp_ops": comp_ops,
        "has_none_check": has_none_check,
        "has_len_zero": has_len_zero,
        "has_mutable_default": has_mutable_default,
        "has_copy_call": has_copy_call,
        "has_bare_except": has_bare_except,
        "has_typed_except": has_typed_except,
        "has_with_lock": has_with_lock,
        "has_set_or_deque": has_set_or_deque,
        "func_names": func_names,
    }


def _detect_hunk_bug_classes(file_path: str, hunk: dict[str, Any]) -> list[tuple[str, str]]:
    """Return list of `(bug_class, evidence_note)` detected in one diff hunk."""
    removed = hunk.get("removed") or []
    added = hunk.get("added") or []
    if not removed and not added:
        return []

    rem_text = "\n".join(removed)
    add_text = "\n".join(added)
    is_py = file_path.endswith(".py")
    rem_ast = _ast_signals(_try_parse_python_snippet(removed)) if is_py else None
    add_ast = _ast_signals(_try_parse_python_snippet(added)) if is_py else None

    hits: list[tuple[str, str]] = []

    # 1. off_by_one
    obo_reason: str | None = None
    if rem_ast and add_ast:
        if (("Lt" in rem_ast["comp_ops"] and "LtE" in add_ast["comp_ops"])
            or ("LtE" in rem_ast["comp_ops"] and "Lt" in add_ast["comp_ops"])
            or ("Gt" in rem_ast["comp_ops"] and "GtE" in add_ast["comp_ops"])
            or ("GtE" in rem_ast["comp_ops"] and "Gt" in add_ast["comp_ops"])):
            obo_reason = "Strict vs inclusive comparison operator changed (< <-> <= or > <-> >=)."
    if obo_reason is None:
        if (re.search(r"(<=|>=|<|>)", rem_text) and re.search(r"(<=|>=|<|>)", add_text)
            and (("<=" in add_text) != ("<=" in rem_text) or (">=" in add_text) != (">=" in rem_text))):
            obo_reason = "Loop or boundary comparison operator shifted between strict and inclusive."
        elif (re.search(r"(\blen\([^)]+\)\s*[-+]\s*1\b|\.length\s*[-+]\s*1\b|range\([^)]*[-+]\s*1\)|\bmid\s*[-+]\s*1\b|\bhigh\s*[-+]\s*1\b|\bhi\s*[-+]\s*1\b)", add_text)
              and not re.search(r"(\blen\([^)]+\)\s*[-+]\s*1\b|\.length\s*[-+]\s*1\b|range\([^)]*[-+]\s*1\)|\bmid\s*[-+]\s*1\b)", rem_text)):
            obo_reason = "Index/range endpoint adjusted by +-1."
        elif re.search(r"\[[^\]]*:\s*[^\]]*\+\s*1\]", add_text) and not re.search(r"\+\s*1\]", rem_text):
            obo_reason = "Slice upper bound adjusted by +1."
    if obo_reason:
        hits.append(("off_by_one", obo_reason))

    # 2. null_or_edge_guard
    null_reason: str | None = None
    if add_ast and ((add_ast["has_none_check"] and not (rem_ast and rem_ast["has_none_check"]))
                    or (add_ast["has_len_zero"] and not (rem_ast and rem_ast["has_len_zero"]))):
        null_reason = "Added explicit None or empty-collection boundary guard."
    elif (re.search(r"(\bis\s+None\b|\bis\s+not\s+None\b|\bif\s+not\s+[A-Za-z_]|===\s*null\b|!==\s*null\b|undefined\b|\?\.\w|\?\?|\b==\s*0\b|\blen\([^)]+\)\s*==\s*0)", add_text)
          and not re.search(r"(\bis\s+None\b|\bis\s+not\s+None\b|\bif\s+not\s+[A-Za-z_]|===\s*null\b|!==\s*null\b|\?\.\w|\?\?|\blen\([^)]+\)\s*==\s*0)", rem_text)):
        null_reason = "Added null/empty/zero guard on edge input."
    if null_reason:
        hits.append(("null_or_edge_guard", null_reason))

    # 3. aliasing_or_mutation
    alias_reason: str | None = None
    if rem_ast and add_ast and rem_ast["has_mutable_default"] and not add_ast["has_mutable_default"]:
        alias_reason = "Replaced mutable default argument with safe None sentinel."
    elif add_ast and add_ast["has_copy_call"] and not (rem_ast and rem_ast["has_copy_call"]):
        alias_reason = "Added defensive copy/clone to prevent shared-state aliasing."
    elif (re.search(r"(\.copy\(\)|deepcopy\(|\[:\]|\[\.\.\.|\{\.\.\.|structuredClone\(|Object\.assign\(\s*\{\s*\})", add_text)
          and not re.search(r"(\.copy\(\)|deepcopy\(|\[:\]|\[\.\.\.|\{\.\.\.|structuredClone\()", rem_text)):
        alias_reason = "Introduced shallow/deep copy to break mutable aliasing."
    if alias_reason:
        hits.append(("aliasing_or_mutation", alias_reason))

    # 4. concurrency_lock
    conc_reason: str | None = None
    if add_ast and add_ast["has_with_lock"] and not (rem_ast and rem_ast["has_with_lock"]):
        conc_reason = "Wrapped critical section in a lock context manager."
    elif (re.search(r"\b(Lock|RLock|Mutex|Semaphore|acquire|release|synchronized|atomic|flock|asyncio\.Lock)\b", add_text)
          and not re.search(r"\b(Lock|RLock|Mutex|Semaphore|acquire|release|synchronized|atomic|flock|asyncio\.Lock)\b", rem_text)):
        conc_reason = "Added synchronization primitive around concurrent shared state."
    if conc_reason:
        hits.append(("concurrency_lock", conc_reason))

    # 5. complexity_loop
    comp_reason: str | None = None
    if add_ast and add_ast["has_set_or_deque"] and not (rem_ast and rem_ast["has_set_or_deque"]):
        comp_reason = "Replaced linear list scan/pop with O(1) set or deque lookup."
    elif (re.search(r"(\bset\(|\bdeque\(|\bpopleft\(\)|\.join\(|@lru_cache|@cache|\bnew\s+Set\b|\bnew\s+Map\b)", add_text)
          and not re.search(r"(\bset\(|\bdeque\(|\bpopleft\(\)|\.join\(|@lru_cache|@cache|\bnew\s+Set\b|\bnew\s+Map\b)", rem_text)):
        comp_reason = "Eliminated hidden linear work inside loop using O(1) structure or join/cache."
    if comp_reason:
        hits.append(("complexity_loop", comp_reason))

    # 6. error_handling
    err_reason: str | None = None
    if rem_ast and add_ast and rem_ast["has_bare_except"] and add_ast["has_typed_except"] and not add_ast["has_bare_except"]:
        err_reason = "Narrowed bare `except:` to explicit typed exception handling."
    elif add_ast and add_ast["has_typed_except"] and not (rem_ast and rem_ast["has_typed_except"]):
        err_reason = "Added explicit typed exception handling around fallible operation."
    elif (re.search(r"(\bexcept\s+[A-Z]\w*|\btry\s*:|\btry\s*\{|\bcatch\s*\(|\btimeout\s*=|\braise\s+[A-Z]\w*)", add_text)
          and not re.search(r"(\bexcept\s+[A-Z]\w*|\btry\s*:|\btry\s*\{|\bcatch\s*\(|\btimeout\s*=)", rem_text)):
        err_reason = "Hardened error handling, timeout, or explicit failure propagation."
    if err_reason:
        hits.append(("error_handling", err_reason))

    return hits


def _build_candidate_arena_record(
    *,
    arena_id: str,
    prop_id: str,
    bug_class: str,
    file_path: str,
    before_line: str,
    after_line: str,
) -> dict[str, Any]:
    """Build a valid `arenas.jsonl` record matching `scripts/falsify_arena.py` schema."""
    meta = _CLASS_META[bug_class]
    ts = now_iso()

    # Provide runnable domain-specific reference + conceptual mutant for immediate Counterexample Arena practice
    templates: dict[str, tuple[str, str, str, list[dict[str, str]]]] = {
        "off_by_one": (
            "binary_search",
            (
                "def binary_search(nums, target):\n"
                "    lo, hi = 0, len(nums) - 1\n"
                "    while lo <= hi:\n"
                "        mid = (lo + hi) // 2\n"
                "        if nums[mid] == target:\n"
                "            return mid\n"
                "        if nums[mid] < target:\n"
                "            lo = mid + 1\n"
                "        else:\n"
                "            hi = mid - 1\n"
                "    return -1\n"
            ),
            (
                "def binary_search(nums, target):\n"
                "    lo, hi = 0, len(nums) - 1\n"
                "    while lo < hi:\n"
                "        mid = (lo + hi) // 2\n"
                "        if nums[mid] == target:\n"
                "            return mid\n"
                "        if nums[mid] < target:\n"
                "            lo = mid + 1\n"
                "        else:\n"
                "            hi = mid - 1\n"
                "    return -1\n"
            ),
            [{"description": "nums is sorted ascending", "expr": "args[0] == sorted(args[0])"}],
        ),
        "null_or_edge_guard": (
            "safe_mean",
            "def safe_mean(xs):\n    return 0.0 if not xs else sum(xs) / len(xs)\n",
            "def safe_mean(xs):\n    return sum(xs) / len(xs)\n",
            [{"description": "xs is a list of numbers", "expr": "isinstance(args[0], list)"}],
        ),
        "aliasing_or_mutation": (
            "append_tag",
            "def append_tag(tag, bucket=None):\n    out = list(bucket) if bucket is not None else []\n    out.append(tag)\n    return out\n",
            "def append_tag(tag, bucket=[]):\n    bucket.append(tag)\n    return bucket\n",
            [{"description": "tag is a string", "expr": "isinstance(args[0], str)"}],
        ),
        "concurrency_lock": (
            "bounded_withdraw",
            "def bounded_withdraw(balance, amount):\n    return balance - amount if 0 <= amount <= balance else balance\n",
            "def bounded_withdraw(balance, amount):\n    return balance - amount\n",
            [{"description": "balance >= 0", "expr": "args[0] >= 0"}],
        ),
        "complexity_loop": (
            "has_duplicate",
            "def has_duplicate(xs):\n    return len(xs) != len(set(xs))\n",
            "def has_duplicate(xs):\n    return len(xs) > 1 and xs[0] == xs[-1]\n",
            [{"description": "xs is a list", "expr": "isinstance(args[0], list)"}],
        ),
        "error_handling": (
            "parse_pos_int",
            "def parse_pos_int(raw):\n    try:\n        v = int(raw)\n    except (TypeError, ValueError):\n        return None\n    return v if v > 0 else None\n",
            "def parse_pos_int(raw):\n    return int(raw)\n",
            [{"description": "raw is string or int", "expr": "isinstance(args[0], (str, int))"}],
        ),
    }

    entry, ref_code, mut_code, assumptions = templates[bug_class]
    return {
        "id": arena_id,
        "propId": prop_id,
        "domain": "code",
        "title": f"{meta['label']} ({Path(file_path).name})",
        "claim": f"{meta['arenaClaim']} [Source diff: `{before_line[:60]}` -> `{after_line[:60]}`]",
        "entry": entry,
        "lang": "python",
        "sealedReference": {"code": ref_code, "entry": entry},
        "sealedMutants": [{"id": "m1", "trapTag": bug_class, "code": mut_code}],
        "assumptionPredicates": assumptions,
        "killedMutants": [],
        "bestMinimalSize": None,
        "attempts": 0,
        "lemmaRepairs": [],
        "createdAt": ts,
        "updatedAt": ts,
    }


def debrief_git_diff(
    workspace: Path | str | None,
    *,
    ref: str = "HEAD~1..HEAD",
    diff_text: str | None = None,
    dry_run: bool = False,
    kg_dir: Path | str | None = None,
    max_findings: int = 5,
) -> dict[str, Any]:
    """Inspect a git diff (or supplied unified diff text) and crystallize forks, arenas, and invariants."""
    ws = resolve_workspace(workspace)
    kdir = resolve_kg_dir(workspace=ws, explicit=kg_dir)

    if diff_text is not None:
        raw_diff = str(diff_text)
        eff_ref = "supplied_diff"
    else:
        eff_ref = validate_git_ref(ref)
        proc = subprocess.run(
            ["git", "diff", "--no-ext-diff", "--unified=3", eff_ref],
            cwd=str(ws),
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"git diff failed ({proc.returncode}): {proc.stderr.strip() or 'unknown error'}")
        raw_diff = proc.stdout

    parsed_files = _parse_unified_diff(raw_diff)
    findings: list[dict[str, Any]] = []
    seen_keys: set[tuple[str, str]] = set()

    for fobj in parsed_files:
        fpath = str(fobj.get("path") or "unknown")
        for hunk in fobj.get("hunks") or []:
            detected = _detect_hunk_bug_classes(fpath, hunk)
            for bug_class, evidence in detected:
                key = (bug_class, fpath)
                if key in seen_keys:
                    continue
                seen_keys.add(key)

                rem_lines = [ln for ln in (hunk.get("removed") or []) if ln.strip()]
                add_lines = [ln for ln in (hunk.get("added") or []) if ln.strip()]
                ctx_lines = [ln for ln in (hunk.get("context") or []) if ln.strip()][:4]
                before_line = rem_lines[0].strip() if rem_lines else "(missing guard/check)"
                after_line = add_lines[0].strip() if add_lines else "(removed faulty line)"

                meta = _CLASS_META[bug_class]
                sig_hash = hashlib.sha256(
                    f"{fpath}|{bug_class}|{before_line}|{after_line}".encode("utf-8")
                ).hexdigest()[:8]
                prop_id = f"work-{slugify(bug_class, max_len=24)}-{sig_hash}"
                fork_id = f"fork-work-{sig_hash}"
                arena_id = f"arena-work-{sig_hash}"

                findings.append(
                    {
                        "bugClass": bug_class,
                        "label": meta["label"],
                        "filePath": fpath,
                        "evidence": evidence,
                        "beforeLine": before_line,
                        "afterLine": after_line,
                        "contextLines": ctx_lines,
                        "invariant": meta["invariant"],
                        "refutation": meta["refutation"],
                        "propId": prop_id,
                        "forkId": fork_id,
                        "arenaId": arena_id,
                    }
                )
                if len(findings) >= max(1, int(max_findings)):
                    break
            if len(findings) >= max(1, int(max_findings)):
                break
        if len(findings) >= max(1, int(max_findings)):
            break

    crystallized: dict[str, list[str]] = {"propositions": [], "forks": [], "arenas": []}
    if not dry_run and findings:
        props_path = kdir / "propositions.jsonl"
        arenas_path = kdir / "arenas.jsonl"

        with workspace_lock(props_path):
            props = load_jsonl(props_path)
            existing_ids = {str(p.get("id") or "") for p in props}
            existing_titles = {str(p.get("title") or "").strip().lower() for p in props}

            for fnd in findings:
                pid = fnd["propId"]
                title = f"{fnd['label']} in {Path(fnd['filePath']).name}: {fnd['invariant']}"
                if pid not in existing_ids and title.lower() not in existing_titles:
                    props.append(
                        {
                            "id": pid,
                            "subject": "Computer Science",
                            "chapter": "Work Debrief",
                            "title": title,
                            "kind": "invariant",
                            "status": "shaky",
                            "notes": f"{fnd['evidence']} ({fnd['beforeLine']} -> {fnd['afterLine']})",
                            "boundaryConditions": [fnd["invariant"]],
                            "antiPatterns": [fnd["beforeLine"]],
                            "tags": ["work-mentor", fnd["bugClass"]],
                        }
                    )
                    existing_ids.add(pid)
                    existing_titles.add(title.lower())
                crystallized["propositions"].append(pid)
            save_jsonl_atomic(props_path, props)

        # Crystallize Blunder FEN Forks
        for fnd in findings:
            is_code = Path(fnd["filePath"]).suffix.lower() in {".py", ".js", ".ts", ".tsx", ".go", ".rs", ".java", ".c", ".cpp"}
            capture_fork(
                prop_id=fnd["propId"],
                prefix_steps=fnd["contextLines"] or [f"# File: {fnd['filePath']}"],
                fork_prompt=f"Work debrief ({fnd['label']} in {Path(fnd['filePath']).name}): write the exact fixed line.",
                sealed_blunder=fnd["beforeLine"],
                sealed_winning_move=fnd["afterLine"],
                refutation_msg=fnd["refutation"],
                subject="Computer Science",
                chapter="Work Debrief",
                verify_spec={"engine": "code" if is_code else "exact"},
                fork_id=fnd["forkId"],
                kg_dir=kdir,
            )
            crystallized["forks"].append(fnd["forkId"])

        # Crystallize Counterexample Arenas (deduped by id)
        with workspace_lock(arenas_path):
            arenas = load_jsonl(arenas_path)
            arena_idx_by_id = {str(a.get("id") or ""): i for i, a in enumerate(arenas)}
            for fnd in findings:
                rec = _build_candidate_arena_record(
                    arena_id=fnd["arenaId"],
                    prop_id=fnd["propId"],
                    bug_class=fnd["bugClass"],
                    file_path=fnd["filePath"],
                    before_line=fnd["beforeLine"],
                    after_line=fnd["afterLine"],
                )
                if fnd["arenaId"] in arena_idx_by_id:
                    arenas[arena_idx_by_id[fnd["arenaId"]]] = rec
                else:
                    arena_idx_by_id[fnd["arenaId"]] = len(arenas)
                    arenas.append(rec)
                crystallized["arenas"].append(fnd["arenaId"])
            save_jsonl_atomic(arenas_path, arenas)

    return {
        "ok": True,
        "ref": eff_ref,
        "filesAnalyzed": len(parsed_files),
        "findingsCount": len(findings),
        "findings": findings,
        "dryRun": bool(dry_run),
        "crystallized": crystallized,
    }


# ---------------------------------------------------------------------------
# Mentor Mode & Non-Annoying Mastery Gate
# ---------------------------------------------------------------------------


def set_mentor_mode(
    workspace: Path | str | None,
    enabled: bool = True,
    *,
    kg_dir: Path | str | None = None,
) -> dict[str, Any]:
    """Enable or disable Anti-Vibe-Coding Work Mentor mode in `mentor-config.json`."""
    ws = resolve_workspace(workspace)
    kdir = resolve_kg_dir(workspace=ws, explicit=kg_dir)
    cfg_path = kdir / MENTOR_CONFIG_FILENAME
    with workspace_lock(cfg_path):
        prev = load_json(cfg_path, default={})
        cfg = dict(prev) if isinstance(prev, dict) else {}
        cfg["schemaVersion"] = 1
        cfg["mentorMode"] = bool(enabled)
        cfg["mode"] = "mentor" if enabled else "study"
        cfg["updatedAt"] = now_iso()
        save_json_atomic(cfg_path, cfg)
    return {
        "ok": True,
        "mentorMode": bool(enabled),
        "mode": cfg["mode"],
        "path": str(cfg_path),
    }


def _highest_verified_tier(kdir: Path, prop: dict[str, Any]) -> int:
    pid = str(prop.get("id") or "")
    best = int(prop.get("evidenceTier") or 0)
    for row in load_jsonl(kdir / "evidence-ledger.jsonl"):
        if str(row.get("propId") or "") == pid and row.get("verified", True):
            try:
                best = max(best, int(row.get("tier") or 0))
            except (TypeError, ValueError):
                pass
    for fork in load_jsonl(kdir / "forks.jsonl"):
        if str(fork.get("propId") or "") == pid and int(fork.get("winCount") or 0) >= 1:
            best = max(best, 3)
    for arena in load_jsonl(kdir / "arenas.jsonl"):
        if str(arena.get("propId") or "") == pid and len(arena.get("killedMutants") or []) >= 1:
            best = max(best, 3)
    if best == 0 and str(prop.get("status") or "").lower() == "solid":
        # Solid nodes with established stability/BKT >= 0.80 qualify as verified unless explicitly weak
        bkt = float(prop.get("bktMastery") or 0.0)
        if bkt >= 0.80:
            best = 3
        else:
            best = 2
    return best


def evaluate_mentor_gate(
    workspace: Path | str | None,
    touched_concepts: list[str],
    *,
    kg_dir: Path | str | None = None,
) -> dict[str, Any]:
    """Silent passthrough for `solid` T3+ concepts; 20s micro-prediction gate ONLY for `shaky`/`misconception`."""
    ws = resolve_workspace(workspace)
    kdir = resolve_kg_dir(workspace=ws, explicit=kg_dir)
    cfg = load_json(kdir / MENTOR_CONFIG_FILENAME, default={})
    mentor_enabled = bool(cfg.get("mentorMode", True)) if isinstance(cfg, dict) else True

    props = load_jsonl(kdir / "propositions.jsonl")
    by_id = {str(p.get("id") or "").lower(): p for p in props if p.get("id")}

    matched_weak: list[dict[str, Any]] = []
    matched_solid: list[dict[str, Any]] = []
    unmatched: list[str] = []

    for raw_c in touched_concepts or []:
        q = str(raw_c or "").strip()
        if not q:
            continue
        q_low = q.lower()
        prop = by_id.get(q_low)
        if prop is None:
            for p in props:
                title_low = str(p.get("title") or "").lower()
                tags = [str(t).lower() for t in (p.get("tags") or [])]
                if q_low in title_low or q_low in tags or q_low in str(p.get("id") or "").lower():
                    prop = p
                    break
        if prop is None:
            unmatched.append(q)
            continue

        status = str(prop.get("status") or "planned").lower()
        tier = _highest_verified_tier(kdir, prop)
        entry = {
            "id": str(prop["id"]),
            "title": str(prop.get("title") or prop["id"]),
            "status": status,
            "evidenceTier": tier,
            "boundaryConditions": list(prop.get("boundaryConditions") or []),
            "notes": str(prop.get("notes") or "").strip(),
        }
        if status in ("shaky", "misconception"):
            matched_weak.append(entry)
        else:
            matched_solid.append(entry)

    # Silent passthrough when mentor mode disabled or no shaky/misconception concepts touched
    if not mentor_enabled or not matched_weak:
        return {
            "ok": True,
            "mentorMode": mentor_enabled,
            "gated": False,
            "action": "passthrough",
            "durationSec": 0,
            "prompt": None,
            "targetConcept": None,
            "solidConcepts": matched_solid,
            "weakConcepts": matched_weak,
            "unmatchedConcepts": unmatched,
        }

    # Prioritize misconception over shaky
    matched_weak.sort(key=lambda x: (0 if x["status"] == "misconception" else 1, x["id"]))
    target = matched_weak[0]
    bc_hint = (
        f" Check boundary: {target['boundaryConditions'][0]}"
        if target["boundaryConditions"]
        else ""
    )
    prompt = (
        f"20s micro-prediction before editing (`{target['id']}` is {target['status']}): "
        f"What exact failure or edge case breaks `{target['title']}` here?{bc_hint}"
    )
    return {
        "ok": True,
        "mentorMode": mentor_enabled,
        "gated": True,
        "action": "micro_prediction",
        "durationSec": 20,
        "prompt": prompt,
        "targetConcept": target,
        "solidConcepts": matched_solid,
        "weakConcepts": matched_weak,
        "unmatchedConcepts": unmatched,
    }
