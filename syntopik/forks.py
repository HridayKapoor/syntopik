"""Blunder FEN & Critical-Moment Fork Replay engine for Syntopik v2.0.

Captures the exact pre-blunder state (a STEM/CS "FEN") one step before a
learner's earliest broken line, replays that mid-flight fork under FSRS-6
scheduling with deterministic symbol/number mutations, and plays a
deterministic refutation if the learner repeats their historical blunder
BEFORE revealing the winning move.

Pure Python 3.12+ standard library only (optional lazy SymPy / `code_verify.py`
support when present; clean fallbacks when `syntopik.{paths,storage,schemas,fsrs_core}`
or third-party libraries are absent).
"""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import math
import os
import random
import re
import statistics
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Safe syntopik.* imports with pure-stdlib fallbacks
# ---------------------------------------------------------------------------

try:
    from syntopik.paths import resolve_kg_dir as _resolve_kg_dir
    from syntopik.paths import resolve_package_root as _resolve_package_root
    from syntopik.paths import resolve_workspace as _resolve_workspace
except Exception:  # pragma: no cover - standalone fallback
    def _resolve_package_root() -> Path:
        return Path(__file__).resolve().parent.parent

    def _resolve_workspace(explicit: Path | str | None = None) -> Path:
        if explicit is not None and str(explicit).strip():
            return Path(explicit).expanduser().resolve()
        env_ws = os.environ.get("SYNT_WORKSPACE", "").strip() or os.environ.get("MLH_WORKSPACE", "").strip()
        if env_ws:
            return Path(env_ws).expanduser().resolve()
        return Path.cwd().resolve()

    def _resolve_kg_dir(workspace: Path | str | None = None, *, explicit: Path | str | None = None) -> Path:
        if explicit is not None and str(explicit).strip():
            return Path(explicit).expanduser().resolve()
        if workspace is not None and str(workspace).strip():
            return (Path(workspace).expanduser().resolve() / "knowledge-graph").resolve()
        env_kg = os.environ.get("SYNT_KG_DIR", "").strip() or os.environ.get("MLH_KG_DIR", "").strip()
        if env_kg:
            return Path(env_kg).expanduser().resolve()
        return (_resolve_workspace() / "knowledge-graph").resolve()


try:
    from syntopik.storage import load_jsonl as _load_jsonl
    from syntopik.storage import save_jsonl_atomic as _save_jsonl_atomic
except Exception:  # pragma: no cover - standalone fallback
    def _load_jsonl(path: Path | str) -> list[dict[str, Any]]:
        p = Path(path).expanduser()
        if not p.is_file():
            return []
        rows: list[dict[str, Any]] = []
        for ln in p.read_text(encoding="utf-8").splitlines():
            s = ln.strip()
            if not s:
                continue
            try:
                obj = json.loads(s)
            except Exception:
                continue
            if isinstance(obj, dict):
                rows.append(obj)
        return rows

    def _save_jsonl_atomic(path: Path | str, rows: list[dict[str, Any]]) -> Path:
        target = Path(path).expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        body = "\n".join(json.dumps(r, ensure_ascii=False) for r in rows)
        text = (body + "\n") if body else ""
        fd, tmp = tempfile.mkstemp(dir=str(target.parent), prefix=f".{target.name}.", suffix=".tmp")
        tmp_path = Path(tmp)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(text)
            os.replace(tmp_path, target)
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise
        return target


try:
    from syntopik.schemas import normalize_subject as _normalize_subject
    from syntopik.schemas import now_iso as _now_iso
    from syntopik.schemas import today_iso as _today_iso
except Exception:  # pragma: no cover - standalone fallback
    def _now_iso() -> str:
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    def _today_iso() -> str:
        return datetime.now(timezone.utc).date().isoformat()

    def _normalize_subject(value: str) -> str:
        s = re.sub(r"\s+", " ", str(value or "General").strip())
        return s or "General"


try:
    from syntopik.fsrs_core import card_retrievability as _card_retrievability
    from syntopik.fsrs_core import load_fsrs_parameters as _load_fsrs_parameters
    from syntopik.fsrs_core import review_card_dict as _review_card_dict
except Exception:  # pragma: no cover - standalone fallback
    def _load_fsrs_parameters(kg_dir: Path | str | None = None) -> tuple[float, ...]:
        return ()

    def _card_retrievability(card_dict: dict[str, Any] | None, now: datetime | None = None, **_: Any) -> float:
        if not isinstance(card_dict, dict):
            return 0.5
        s = max(0.1, float(card_dict.get("stability") or 1.0))
        ref_now = now or datetime.now(timezone.utc)
        last_raw = card_dict.get("last_review")
        if not isinstance(last_raw, str):
            return 0.5
        try:
            dt = datetime.fromisoformat(last_raw.replace("Z", "+00:00"))
            elapsed = max(0.0, (ref_now - dt).total_seconds() / 86400.0)
        except ValueError:
            return 0.5
        return float((1.0 + (elapsed / (9.0 * s))) ** -1.0)

    def _review_card_dict(
        card_dict: dict[str, Any] | None,
        rating: str | int,
        now: datetime | None = None,
        **_: Any,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        ref_now = now or datetime.now(timezone.utc)
        r = str(rating).strip().lower()
        prev_s = float((card_dict or {}).get("stability") or 1.0)
        prev_d = float((card_dict or {}).get("difficulty") or 5.0)
        if r in ("1", "again"):
            new_s, new_d, ivl = max(0.25, prev_s * 0.4), min(10.0, prev_d + 1.2), 1
        elif r in ("2", "hard"):
            new_s, new_d, ivl = max(0.8, prev_s * 1.15), min(10.0, prev_d + 0.4), max(1, int(round(prev_s * 1.15)))
        elif r in ("4", "easy"):
            new_s, new_d, ivl = max(3.0, prev_s * 2.6), max(1.0, prev_d - 0.8), max(2, int(round(prev_s * 2.6)))
        else:
            new_s, new_d, ivl = max(1.5, prev_s * 1.9), max(1.0, prev_d - 0.2), max(1, int(round(prev_s * 1.9)))
        due_dt = ref_now + timedelta(days=ivl)
        out = {
            "card_id": int((card_dict or {}).get("card_id") or int(ref_now.timestamp() * 1000)),
            "state": 2,
            "step": None,
            "stability": round(new_s, 4),
            "difficulty": round(new_d, 4),
            "due": due_dt.isoformat(),
            "last_review": ref_now.isoformat(),
        }
        return out, {"intervalDays": ivl, "backend": "fallback"}


__all__ = [
    "FORKS_FILENAME",
    "resolve_forks_path",
    "load_forks",
    "save_forks",
    "get_fork",
    "capture_fork",
    "list_due_forks",
    "mutate_fork",
    "check_fork_move",
    "fork_stats",
]

FORKS_FILENAME = "forks.jsonl"

# Safe physics/math symbol swap candidates for deterministic mutation
_SYMBOL_SWAPS: dict[str, tuple[str, ...]] = {
    "A": ("R", "X_0", "D"),
    "T": ("P", "tau", "T_0"),
    "x": ("q", "s", "u"),
    "m": ("M", "m_0"),
    "k": ("K", "k_s"),
    "v": ("u", "v_0"),
    "L": ("d", "l_0"),
    "omega": ("w", "Omega"),
    "alpha": ("beta", "gamma"),
    "theta": ("phi", "psi"),
}

# Safe math namespace & strict AST validator limits for bare-stdlib / SymPy equivalence checking
_STRICT_MATH_CALLABLES: frozenset[str] = frozenset(
    {
        "sin",
        "cos",
        "tan",
        "asin",
        "acos",
        "atan",
        "sinh",
        "cosh",
        "tanh",
        "exp",
        "log",
        "ln",
        "sqrt",
        "abs",
        "Abs",
        "floor",
        "ceil",
        "ceiling",
    }
)

_SAFE_MATH_FUNCS: dict[str, Any] = {
    "pi": math.pi,
    "e": math.e,
    "E": math.e,
    "oo": math.inf,
    "tau": math.tau,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "asin": math.asin,
    "acos": math.acos,
    "atan": math.atan,
    "sinh": math.sinh,
    "cosh": math.cosh,
    "tanh": math.tanh,
    "exp": math.exp,
    "log": math.log,
    "ln": math.log,
    "sqrt": math.sqrt,
    "abs": abs,
    "Abs": abs,
    "floor": math.floor,
    "ceil": math.ceil,
    "ceiling": math.ceil,
}

_MAX_EXPR_LEN = 512
_MAX_AST_NODES = 200
_MAX_AST_DEPTH = 32
_MAX_EXPONENT = 1000
_MAX_POWER_LOG10 = 300.0
_MAX_INT_BITS = 1024
_IDENT_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")

_ALLOWED_AST_NODES = (
    ast.Expression,
    ast.BinOp,
    ast.UnaryOp,
    ast.Constant,
    ast.Name,
    ast.Load,
    ast.Call,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.FloorDiv,
    ast.Mod,
    ast.Pow,
    ast.UAdd,
    ast.USub,
)


def resolve_forks_path(
    workspace: Path | str | None = None,
    kg_dir: Path | str | None = None,
) -> Path:
    """Resolve `knowledge-graph/forks.jsonl` for the active or explicit workspace."""
    kdir = _resolve_kg_dir(workspace=workspace, explicit=kg_dir)
    return kdir / FORKS_FILENAME


def load_forks(
    workspace: Path | str | None = None,
    kg_dir: Path | str | None = None,
) -> list[dict[str, Any]]:
    """Load all Blunder FEN fork records from `knowledge-graph/forks.jsonl`."""
    return _load_jsonl(resolve_forks_path(workspace=workspace, kg_dir=kg_dir))


def save_forks(
    forks: list[dict[str, Any]],
    workspace: Path | str | None = None,
    kg_dir: Path | str | None = None,
) -> Path:
    """Atomically write all fork records to `knowledge-graph/forks.jsonl`."""
    return _save_jsonl_atomic(resolve_forks_path(workspace=workspace, kg_dir=kg_dir), forks)


def get_fork(
    fork_id: str,
    workspace: Path | str | None = None,
    kg_dir: Path | str | None = None,
) -> dict[str, Any] | None:
    """Fetch a single fork record by ID, or None if absent."""
    target = str(fork_id or "").strip()
    if not target:
        return None
    for row in load_forks(workspace=workspace, kg_dir=kg_dir):
        if str(row.get("id") or "") == target:
            return row
    return None


# ---------------------------------------------------------------------------
# Normalization & safe math/code equivalence helpers
# ---------------------------------------------------------------------------


def _normalize_text_move(text: str) -> str:
    raw = str(text or "").strip()
    raw = raw.strip("$").strip()
    raw = re.sub(r"\\left|\\right|\\,|\\;|\\!", "", raw)
    raw = re.sub(r"\s+", " ", raw).strip().lower()
    return raw


def _preprocess_math_text(expr: str) -> str:
    """Convert light LaTeX/caret/implicit-mul math into Python expression syntax."""
    s = str(expr or "").strip()
    s = s.strip("$").strip()
    # Handle simple \frac{a}{b} -> ((a)/(b))
    for _ in range(6):
        nxt = re.sub(r"\\frac\s*\{([^{}]+)\}\s*\{([^{}]+)\}", r"((\1)/(\2))", s)
        if nxt == s:
            break
        s = nxt
    s = re.sub(r"\\sqrt\s*\{([^{}]+)\}", r"sqrt(\1)", s)
    s = re.sub(r"\\(sin|cos|tan|log|ln|exp|pi|omega|alpha|beta|theta|phi|tau)\b", r"\1", s)
    s = s.replace("\\cdot", "*").replace("\\times", "*").replace("×", "*").replace("·", "*")
    s = s.replace("−", "-").replace("^", "**")
    s = re.sub(r"\\left|\\right|\\,|\\;|\\!", "", s)
    # Implicit multiplication: 5T -> 5*T, 2pi -> 2*pi, 3(x+1) -> 3*(x+1), )( -> )*( (preserves 1e-6)
    s = re.sub(r"(\d)\s*(?![eE][+-]?\d)([A-Za-z_(])", r"\1*\2", s)
    s = re.sub(r"(\))\s*([A-Za-z0-9_(])", r"\1*\2", s)
    return s.strip()


def _is_clean_ident(name: str) -> bool:
    return bool(isinstance(name, str) and _IDENT_RE.match(name) and "__" not in name and not name.startswith("_"))


def _safe_pow(base: float | int, exp: float | int) -> float | int:
    if isinstance(base, bool) or isinstance(exp, bool):
        raise ValueError("Boolean operands are disallowed in exponentiation.")
    f_exp = float(exp)
    if not math.isfinite(f_exp) or abs(f_exp) > _MAX_EXPONENT:
        raise ValueError(f"Exponent exceeds safe limit (|exp| <= {_MAX_EXPONENT}).")
    f_base = float(base)
    if not math.isfinite(f_base):
        raise ValueError("Non-finite base in exponentiation.")
    if abs(f_base) > 1.0 and f_exp > 0.0:
        if f_exp * math.log10(abs(f_base)) > _MAX_POWER_LOG10:
            raise ValueError("Exponentiation magnitude exceeds safe limit.")
    res = base ** exp
    if isinstance(res, int) and res.bit_length() > _MAX_INT_BITS:
        raise ValueError("Integer magnitude exceeds safe limit.")
    if isinstance(res, complex):
        raise ValueError("Complex result not supported in real numeric evaluator.")
    return res


def _try_const_numeric(node: ast.AST) -> float | int | None:
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            return node.value
        return None
    if isinstance(node, ast.UnaryOp):
        v = _try_const_numeric(node.operand)
        if v is None:
            return None
        if isinstance(node.op, ast.UAdd):
            return +v
        if isinstance(node.op, ast.USub):
            return -v
        return None
    if isinstance(node, ast.BinOp):
        lv = _try_const_numeric(node.left)
        rv = _try_const_numeric(node.right)
        if lv is None or rv is None:
            return None
        if isinstance(node.op, ast.Pow):
            return _safe_pow(lv, rv)
        if isinstance(node.op, ast.Add):
            res = lv + rv
        elif isinstance(node.op, ast.Sub):
            res = lv - rv
        elif isinstance(node.op, ast.Mult):
            res = lv * rv
        elif isinstance(node.op, ast.Div):
            if rv == 0:
                return None
            res = lv / rv
        elif isinstance(node.op, ast.FloorDiv):
            if rv == 0:
                return None
            res = lv // rv
        elif isinstance(node.op, ast.Mod):
            if rv == 0:
                return None
            res = lv % rv
        else:
            return None
        if isinstance(res, int) and res.bit_length() > _MAX_INT_BITS:
            raise ValueError("Integer magnitude exceeds safe limit.")
        return res
    return None


def _contains_pow(node: ast.AST) -> bool:
    return any(isinstance(n, ast.BinOp) and isinstance(n.op, ast.Pow) for n in ast.walk(node))


def _check_ast_depth(node: ast.AST, depth: int = 0) -> None:
    if depth > _MAX_AST_DEPTH:
        raise ValueError("Math expression nesting depth exceeds safe limit.")
    for child in ast.iter_child_nodes(node):
        _check_ast_depth(child, depth + 1)


def validate_math_expr_ast(expr: str) -> ast.Expression:
    """Strict math-only AST validator run BEFORE any evaluation or SymPy parse_expr()."""
    code = str(expr or "").strip()
    if not code:
        raise ValueError("Math expression cannot be empty.")
    if len(code) > _MAX_EXPR_LEN:
        raise ValueError(f"Math expression exceeds {_MAX_EXPR_LEN} character limit.")
    try:
        parsed = ast.parse(code, mode="eval")
    except SyntaxError as exc:
        raise ValueError(f"Invalid math expression syntax: {exc.msg}") from exc

    _check_ast_depth(parsed)
    node_count = 0
    for node in ast.walk(parsed):
        node_count += 1
        if node_count > _MAX_AST_NODES:
            raise ValueError("Math expression exceeds safe AST complexity limit.")
        if not isinstance(node, _ALLOWED_AST_NODES):
            raise ValueError(f"Disallowed AST construct: {type(node).__name__}")
        if isinstance(node, ast.Constant):
            if not isinstance(node.value, (int, float)) or isinstance(node.value, bool):
                raise ValueError(f"Disallowed literal type in math expression: {type(node.value).__name__}")
            if isinstance(node.value, float) and not math.isfinite(node.value):
                raise ValueError("Non-finite numeric literal.")
            if isinstance(node.value, int) and node.value.bit_length() > _MAX_INT_BITS:
                raise ValueError("Integer literal exceeds safe size limit.")
        elif isinstance(node, ast.Name):
            if not _is_clean_ident(node.id):
                raise ValueError(f"Disallowed identifier: {node.id!r}")
        elif isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name):
                raise ValueError("Disallowed callable target in math expression")
            if not _is_clean_ident(node.func.id) or node.func.id not in _STRICT_MATH_CALLABLES:
                raise ValueError(f"Disallowed function call in math expression: {node.func.id!r}")
            if node.keywords:
                raise ValueError("Keyword arguments are disallowed in math expressions")
            if any(isinstance(arg, ast.Starred) for arg in node.args):
                raise ValueError("Starred arguments are disallowed in math expressions")
        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Pow):
            exp_val = _try_const_numeric(node.right)
            if exp_val is not None:
                if not math.isfinite(float(exp_val)) or abs(float(exp_val)) > _MAX_EXPONENT:
                    raise ValueError(f"Exponent exceeds safe limit (|exp| <= {_MAX_EXPONENT}).")
                base_val = _try_const_numeric(node.left)
                if base_val is not None:
                    _safe_pow(base_val, exp_val)
            elif _contains_pow(node.right):
                raise ValueError("Nested exponentiation in exponent is disallowed.")
    return parsed


def _eval_math_ast_node(node: ast.AST, ns: dict[str, Any]) -> Any:
    if isinstance(node, ast.Expression):
        return _eval_math_ast_node(node.body, ns)
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        if node.id not in ns:
            raise ValueError(f"Unknown symbol: {node.id}")
        return ns[node.id]
    if isinstance(node, ast.UnaryOp):
        v = _eval_math_ast_node(node.operand, ns)
        if isinstance(node.op, ast.UAdd):
            return +v
        if isinstance(node.op, ast.USub):
            return -v
        raise ValueError(f"Disallowed unary op: {type(node.op).__name__}")
    if isinstance(node, ast.BinOp):
        left = _eval_math_ast_node(node.left, ns)
        right = _eval_math_ast_node(node.right, ns)
        if isinstance(node.op, ast.Pow):
            return _safe_pow(left, right)
        if isinstance(node.op, ast.Add):
            res = left + right
        elif isinstance(node.op, ast.Sub):
            res = left - right
        elif isinstance(node.op, ast.Mult):
            res = left * right
        elif isinstance(node.op, ast.Div):
            res = left / right
        elif isinstance(node.op, ast.FloorDiv):
            res = left // right
        elif isinstance(node.op, ast.Mod):
            res = left % right
        else:
            raise ValueError(f"Disallowed binary op: {type(node.op).__name__}")
        if isinstance(res, int) and res.bit_length() > _MAX_INT_BITS:
            raise ValueError("Integer magnitude exceeds safe limit.")
        return res
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in _STRICT_MATH_CALLABLES:
            raise ValueError("Disallowed function call in math expression")
        fn = ns[node.func.id]
        args = [_eval_math_ast_node(a, ns) for a in node.args]
        return fn(*args)
    raise ValueError(f"Disallowed AST construct: {type(node).__name__}")


def _safe_eval_math_ast(expr: str, env: dict[str, float]) -> float:
    parsed = validate_math_expr_ast(expr)
    for node in ast.walk(parsed):
        if isinstance(node, ast.Name):
            if node.id not in env and node.id not in _SAFE_MATH_FUNCS:
                raise ValueError(f"Unknown symbol: {node.id}")
    ns = dict(_SAFE_MATH_FUNCS)
    ns.update(env)
    val = _eval_math_ast_node(parsed, ns)
    fval = float(val)
    if not math.isfinite(fval):
        raise ValueError("Non-finite result")
    return fval


def _extract_math_symbols(expr: str) -> set[str]:
    try:
        parsed = validate_math_expr_ast(expr)
    except ValueError:
        return set()
    syms: set[str] = set()
    for node in ast.walk(parsed):
        if isinstance(node, ast.Name) and node.id not in _SAFE_MATH_FUNCS:
            syms.add(node.id)
    return syms


def _math_equivalent(expr_a: str, expr_b: str, *, tol: float = 1e-6, _depth: int = 0) -> bool:
    """Compare two mathematical expressions via strict AST validation + optional SymPy or stdlib probing."""
    if _depth > 2:
        return False
    if _normalize_text_move(expr_a) == _normalize_text_move(expr_b):
        return True

    # Handle equations `lhs = rhs` by comparing `(lhs) - (rhs)` (up to sign scaling)
    def _split_eq(raw: str) -> tuple[str, str] | None:
        cleaned = raw.strip().strip("$").strip()
        if "==" in cleaned:
            parts = cleaned.split("==", 1)
            return parts[0].strip(), parts[1].strip()
        if "=" in cleaned and not any(op in cleaned for op in ("<=", ">=", "!=")):
            parts = cleaned.split("=", 1)
            return parts[0].strip(), parts[1].strip()
        return None

    eq_a = _split_eq(expr_a)
    eq_b = _split_eq(expr_b)
    if eq_a and eq_b:
        lhs_a, rhs_a = eq_a
        lhs_b, rhs_b = eq_b
        if _math_equivalent(lhs_a, lhs_b, tol=tol, _depth=_depth + 1) and _math_equivalent(rhs_a, rhs_b, tol=tol, _depth=_depth + 1):
            return True
        if _math_equivalent(f"({lhs_a})-({rhs_a})", f"({lhs_b})-({rhs_b})", tol=tol, _depth=_depth + 1):
            return True
        if _math_equivalent(f"({lhs_a})-({rhs_a})", f"({rhs_b})-({lhs_b})", tol=tol, _depth=_depth + 1):
            return True
        return False
    if eq_a and not eq_b:
        # Compare RHS if user wrote only the value (e.g., `5T/6` vs `t = 5T/6`)
        if _math_equivalent(eq_a[1], expr_b, tol=tol, _depth=_depth + 1):
            return True
    if eq_b and not eq_a:
        if _math_equivalent(expr_a, eq_b[1], tol=tol, _depth=_depth + 1):
            return True

    prep_a = _preprocess_math_text(expr_a)
    prep_b = _preprocess_math_text(expr_b)
    if not prep_a or not prep_b:
        return False

    # Mandatory strict math-only AST validation BEFORE touching SymPy or numeric eval
    try:
        validate_math_expr_ast(prep_a)
        validate_math_expr_ast(prep_b)
    except ValueError:
        return False

    # Try lazy SymPy if available (with zero __builtins__ in global_dict)
    try:
        import sympy as sp  # type: ignore[import-not-found]
        from sympy.parsing.sympy_parser import (  # type: ignore[import-not-found]
            auto_number,
            auto_symbol,
            parse_expr,
        )

        tr = (auto_symbol, auto_number)
        safe_global: dict[str, Any] = {
            "__builtins__": {},
            "Symbol": sp.Symbol,
            "Integer": sp.Integer,
            "Float": sp.Float,
            "Rational": sp.Rational,
            "Number": sp.Number,
            "pi": sp.pi,
            "e": sp.E,
            "E": sp.E,
            "oo": sp.oo,
            "tau": 2 * sp.pi,
            "sin": sp.sin,
            "cos": sp.cos,
            "tan": sp.tan,
            "asin": sp.asin,
            "acos": sp.acos,
            "atan": sp.atan,
            "sinh": sp.sinh,
            "cosh": sp.cosh,
            "tanh": sp.tanh,
            "exp": sp.exp,
            "log": sp.log,
            "ln": sp.log,
            "sqrt": sp.sqrt,
            "abs": sp.Abs,
            "Abs": sp.Abs,
            "floor": sp.floor,
            "ceil": sp.ceiling,
            "ceiling": sp.ceiling,
        }
        safe_local: dict[str, Any] = {
            k: safe_global[k]
            for k in (
                "pi", "e", "E", "oo", "tau",
                "sin", "cos", "tan", "asin", "acos", "atan",
                "sinh", "cosh", "tanh", "exp", "log", "ln", "sqrt",
                "abs", "Abs", "floor", "ceil", "ceiling",
            )
        }
        sa = parse_expr(prep_a, local_dict=safe_local, global_dict=safe_global, transformations=tr)
        sb = parse_expr(prep_b, local_dict=safe_local, global_dict=safe_global, transformations=tr)
        diff = sp.simplify(sa - sb)
        if diff == 0:
            return True
        if not diff.free_symbols:
            try:
                return abs(complex(sp.N(diff, 12))) <= tol
            except Exception:
                return False
        return False
    except Exception:
        pass

    # Pure-stdlib randomized multi-point numeric equivalence check
    syms_a = _extract_math_symbols(prep_a)
    syms_b = _extract_math_symbols(prep_b)
    all_syms = sorted(syms_a | syms_b)
    rng = random.Random(20261126)
    ok_points = 0
    for _ in range(12):
        env = {name: rng.uniform(1.17, 4.83) for name in all_syms}
        try:
            va = _safe_eval_math_ast(prep_a, env)
            vb = _safe_eval_math_ast(prep_b, env)
        except Exception:
            continue
        ok_points += 1
        scale = max(1.0, abs(va), abs(vb))
        if abs(va - vb) > tol * scale:
            return False
    return ok_points >= 5


def _load_code_verify_module() -> Any | None:
    cv_path = _resolve_package_root() / "scripts" / "code_verify.py"
    if not cv_path.is_file():
        return None
    try:
        spec = importlib.util.spec_from_file_location("_syntopik_code_verify", str(cv_path))
        if spec is None or spec.loader is None:
            return None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception:
        return None


def _verify_code_move(
    prefix_steps: list[str],
    move: str,
    verify_spec: dict[str, Any],
) -> dict[str, Any]:
    """Run learner's code move against `verifySpec.tests` in an isolated sandbox."""
    tests = verify_spec.get("tests")
    if not isinstance(tests, list) or not tests:
        return {"ran": False, "passed": False}

    template = verify_spec.get("template")
    if isinstance(template, str) and "{{MOVE}}" in template:
        source = template.replace("{{MOVE}}", move)
    else:
        suffix = str(verify_spec.get("suffixCode") or "")
        lines = list(prefix_steps)
        lines.append(move)
        if suffix:
            lines.append(suffix)
        source = "\n".join(lines) + "\n"

    entry = verify_spec.get("entry")
    lang = str(verify_spec.get("lang") or "python")
    timeout_sec = float(verify_spec.get("timeoutSec") or 3.0)

    cv = _load_code_verify_module()
    if cv is not None and hasattr(cv, "run_test_cases"):
        try:
            res = cv.run_test_cases(
                source,
                tests,
                lang=lang,
                entry=entry,
                timeout_sec=timeout_sec,
            )
            return {
                "ran": True,
                "passed": bool(res.get("allPassed")),
                "passedCount": int(res.get("passedCount") or 0),
                "totalCount": int(res.get("totalCount") or len(tests)),
                "firstFailure": res.get("firstFailure"),
                "error": res.get("error"),
            }
        except Exception as exc:
            return {"ran": True, "passed": False, "error": str(exc)}

    # Minimal isolated Python subprocess fallback
    if lang != "python" or not entry:
        return {"ran": False, "passed": False}
    with tempfile.TemporaryDirectory(prefix="synt-fork-code-") as tmp:
        src_p = Path(tmp) / "solution.py"
        src_p.write_text(source, encoding="utf-8")
        runner = (
            "import importlib.util, json, sys\n"
            "spec = importlib.util.spec_from_file_location('sol', sys.argv[1])\n"
            "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n"
            "fn = getattr(m, sys.argv[2])\n"
            "cases = json.loads(sys.argv[3])\n"
            "passed = 0; fail = None\n"
            "for i, c in enumerate(cases):\n"
            "  got = fn(*(c.get('args', [])), **(c.get('kwargs', {})))\n"
            "  if got == c.get('expected'): passed += 1\n"
            "  elif fail is None: fail = {'caseIndex': i, 'got': got, 'expected': c.get('expected')}\n"
            "print(json.dumps({'passed': passed == len(cases), 'passedCount': passed, 'totalCount': len(cases), 'firstFailure': fail}))\n"
        )
        try:
            proc = subprocess.run(
                [sys.executable, "-B", "-I", "-c", runner, str(src_p), str(entry), json.dumps(tests)],
                capture_output=True,
                text=True,
                timeout=timeout_sec,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return {"ran": True, "passed": False, "error": "Execution timed out"}
        if proc.returncode != 0:
            return {"ran": True, "passed": False, "error": proc.stderr.strip() or f"Exit {proc.returncode}"}
        try:
            parsed = json.loads(proc.stdout.strip().splitlines()[-1])
            parsed["ran"] = True
            return parsed
        except Exception:
            return {"ran": True, "passed": False, "error": "Malformed runner output"}


# ---------------------------------------------------------------------------
# Token-safe simultaneous substitution for deterministic mutations
# ---------------------------------------------------------------------------


def _apply_substitutions(text: str, mapping: dict[str, str]) -> str:
    if not text or not mapping:
        return text
    keys = sorted((str(k) for k in mapping.keys() if str(k)), key=len, reverse=True)
    if not keys:
        return text
    pattern = re.compile(r"\b(" + "|".join(re.escape(k) for k in keys) + r")\b")
    return pattern.sub(lambda m: str(mapping[m.group(0)]), text)


def _auto_substitutions_for_fork(fork: dict[str, Any], seed: int) -> dict[str, str]:
    blob = " ".join(
        [
            " ".join(str(s) for s in (fork.get("prefixSteps") or [])),
            str(fork.get("forkPrompt") or ""),
            str(fork.get("sealedBlunder") or ""),
            str(fork.get("sealedWinningMove") or ""),
        ]
    )
    rng = random.Random(seed)
    subs: dict[str, str] = {}
    used_targets: set[str] = set()
    for sym, cands in _SYMBOL_SWAPS.items():
        if re.search(rf"\b{re.escape(sym)}\b", blob):
            avail = [c for c in cands if c not in used_targets and not re.search(rf"\b{re.escape(c)}\b", blob)]
            if avail:
                chosen = rng.choice(avail)
                subs[sym] = chosen
                used_targets.add(chosen)
        if len(subs) >= 2:
            break
    if not subs:
        # Fallback: mutate small standalone integers (2..9) deterministically
        nums = sorted({m.group(0) for m in re.finditer(r"\b([2-9])\b", blob)})
        if nums:
            src = rng.choice(nums)
            pool = [str(n) for n in range(2, 10) if str(n) != src and str(n) not in nums]
            if pool:
                subs[src] = rng.choice(pool)
    return subs


def _build_variant_dict(
    fork: dict[str, Any],
    subs: dict[str, str],
    *,
    variant_id: str,
    label: str = "",
) -> dict[str, Any]:
    return {
        "id": variant_id,
        "label": label or (", ".join(f"{k}->{v}" for k, v in sorted(subs.items())) if subs else "canonical"),
        "substitutions": dict(subs),
        "prefixSteps": [_apply_substitutions(str(s), subs) for s in (fork.get("prefixSteps") or [])],
        "forkPrompt": _apply_substitutions(str(fork.get("forkPrompt") or ""), subs),
        "sealedBlunder": _apply_substitutions(str(fork.get("sealedBlunder") or ""), subs),
        "sealedWinningMove": _apply_substitutions(str(fork.get("sealedWinningMove") or ""), subs),
        "refutationMsg": _apply_substitutions(str(fork.get("refutationMsg") or ""), subs),
    }


def _public_fork_view(fork: dict[str, Any], *, include_sealed: bool = False) -> dict[str, Any]:
    view = {
        "id": fork.get("id"),
        "mistakeId": fork.get("mistakeId"),
        "propId": fork.get("propId"),
        "subject": fork.get("subject"),
        "chapter": fork.get("chapter"),
        "prefixSteps": list(fork.get("prefixSteps") or []),
        "forkPrompt": fork.get("forkPrompt"),
        "engine": (fork.get("verifySpec") or {}).get("engine", "exact"),
        "mutationCount": len(fork.get("mutationPool") or []),
        "status": fork.get("status", "active"),
        "nextReview": fork.get("nextReview"),
        "lastTested": fork.get("lastTested"),
        "blunderRepeatCount": int(fork.get("blunderRepeatCount") or 0),
        "winCount": int(fork.get("winCount") or 0),
        "latencyMsHistory": list(fork.get("latencyMsHistory") or []),
    }
    if include_sealed:
        view["sealedBlunder"] = fork.get("sealedBlunder")
        view["sealedWinningMove"] = fork.get("sealedWinningMove")
        view["refutationMsg"] = fork.get("refutationMsg")
        view["verifySpec"] = fork.get("verifySpec")
        view["mutationPool"] = fork.get("mutationPool")
    return view


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def capture_fork(
    *,
    prop_id: str = "",
    prefix_steps: list[str] | None = None,
    fork_prompt: str = "",
    sealed_blunder: str = "",
    sealed_winning_move: str = "",
    refutation_msg: str = "",
    mistake_id: str | None = None,
    subject: str | None = None,
    chapter: str | None = None,
    verify_spec: dict[str, Any] | None = None,
    mutation_pool: list[dict[str, Any]] | None = None,
    fork_id: str | None = None,
    workspace: Path | str | None = None,
    kg_dir: Path | str | None = None,
) -> dict[str, Any]:
    """Capture a Blunder FEN checkpoint into `knowledge-graph/forks.jsonl`."""
    kdir = _resolve_kg_dir(workspace=workspace, explicit=kg_dir)

    # Hydrate defaults from mistakes.jsonl when mistake_id is supplied
    mistake_row: dict[str, Any] | None = None
    if mistake_id:
        for row in _load_jsonl(kdir / "mistakes.jsonl"):
            if str(row.get("id") or "") == str(mistake_id).strip():
                mistake_row = row
                break

    spec = dict(verify_spec or {})
    engine = str(spec.get("engine") or "exact").strip().lower()
    if engine not in ("math", "code", "choice", "exact"):
        raise ValueError(f"Unsupported verifySpec.engine {engine!r}; expected math|code|choice|exact.")
    spec["engine"] = engine

    eff_prop_id = str(prop_id or (mistake_row or {}).get("propId") or "").strip()
    eff_subject = _normalize_subject(str(subject or (mistake_row or {}).get("subject") or "General"))
    eff_chapter = str(chapter or (mistake_row or {}).get("chapter") or "").strip()
    raw_blunder = str(sealed_blunder or (mistake_row or {}).get("wrongMove") or "")
    raw_winning = str(sealed_winning_move or (mistake_row or {}).get("correctMove") or "")
    eff_blunder = raw_blunder.strip("\r\n") if engine == "code" else raw_blunder.strip()
    eff_winning = raw_winning.strip("\r\n") if engine == "code" else raw_winning.strip()
    eff_prompt = str(
        fork_prompt
        or (
            f"Critical fork ({mistake_row.get('triggerCue')}): write the exact next valid step."
            if mistake_row and mistake_row.get("triggerCue")
            else "Write the exact next valid step from this checkpoint."
        )
    ).strip()
    eff_prefix = [str(s).rstrip("\r\n") for s in (prefix_steps or []) if str(s).strip()]

    if not eff_winning.strip():
        raise ValueError("sealed_winning_move is required (or supply a mistake_id with correctMove).")
    if not eff_blunder.strip():
        raise ValueError("sealed_blunder is required (or supply a mistake_id with wrongMove).")

    eff_refutation = str(refutation_msg or "").strip() or (
        f"Repeating '{eff_blunder.strip()}' hits the same trap. Check the governing condition before committing."
    )

    now_dt = datetime.now(timezone.utc)
    now_str = _now_iso()
    today_str = _today_iso()

    fid = str(fork_id or "").strip()
    if not fid:
        seed_str = f"{eff_prop_id}|{eff_blunder}|{eff_winning}|{uuid.uuid4().hex[:8]}"
        fid = f"fork-{hashlib.sha256(seed_str.encode('utf-8')).hexdigest()[:8]}"

    base_record: dict[str, Any] = {
        "id": fid,
        "mistakeId": str(mistake_id).strip() if mistake_id else None,
        "propId": eff_prop_id or None,
        "subject": eff_subject,
        "chapter": eff_chapter or None,
        "prefixSteps": eff_prefix,
        "forkPrompt": eff_prompt,
        "sealedBlunder": eff_blunder,
        "sealedWinningMove": eff_winning,
        "refutationMsg": eff_refutation,
        "verifySpec": spec,
        "mutationPool": list(mutation_pool or []),
        "fsrs": {
            "card_id": int(now_dt.timestamp() * 1000),
            "state": 1,
            "step": 0,
            "stability": 0.5,
            "difficulty": 5.5,
            "due": now_dt.isoformat(),
            "last_review": None,
        },
        "nextReview": today_str,
        "lastTested": None,
        "status": "active",
        "blunderRepeatCount": 0,
        "winCount": 0,
        "latencyMsHistory": [],
        "createdAt": now_str,
        "updatedAt": now_str,
    }

    # Auto-seed 1 deterministic symbol/number mutation if pool is empty and safe swap exists
    if not base_record["mutationPool"] and engine in ("math", "exact", "choice"):
        auto_seed = int(hashlib.sha256(fid.encode("utf-8")).hexdigest()[:8], 16)
        subs = _auto_substitutions_for_fork(base_record, auto_seed)
        if subs:
            base_record["mutationPool"].append(
                _build_variant_dict(base_record, subs, variant_id="mut-1")
            )

    forks = load_forks(workspace=workspace, kg_dir=kg_dir)
    replaced = False
    for idx, existing in enumerate(forks):
        if str(existing.get("id") or "") == fid:
            base_record["createdAt"] = existing.get("createdAt") or now_str
            forks[idx] = base_record
            replaced = True
            break
    if not replaced:
        forks.append(base_record)

    out_path = save_forks(forks, workspace=workspace, kg_dir=kg_dir)
    return {
        "ok": True,
        "action": "updated" if replaced else "captured",
        "path": str(out_path),
        "fork": _public_fork_view(base_record, include_sealed=False),
        "id": fid,
    }


def list_due_forks(
    *,
    workspace: Path | str | None = None,
    kg_dir: Path | str | None = None,
    limit: int = 20,
    subject: str | None = None,
    prop_id: str | None = None,
    include_all: bool = False,
    include_sealed: bool = False,
    today: str | None = None,
) -> dict[str, Any]:
    """List due Blunder FEN forks ranked by urgency without leaking sealed moves by default."""
    forks = load_forks(workspace=workspace, kg_dir=kg_dir)
    kdir = _resolve_kg_dir(workspace=workspace, explicit=kg_dir)
    params = _load_fsrs_parameters(kdir)

    today_str = (today or _today_iso())[:10]
    ref_now = datetime.fromisoformat(f"{today_str}T23:59:59+00:00")
    subj_filter = str(subject or "").strip().lower()
    prop_filter = str(prop_id or "").strip()

    items: list[tuple[tuple[int, float, int, str], dict[str, Any]]] = []
    for row in forks:
        if subj_filter and str(row.get("subject") or "").strip().lower() != subj_filter:
            continue
        if prop_filter and str(row.get("propId") or "").strip() != prop_filter:
            continue

        due_str = str(row.get("nextReview") or "")[:10]
        status = str(row.get("status") or "active").lower()
        is_due = include_all or status in ("active", "shaky") or (not due_str) or (due_str <= today_str)
        if not is_due:
            continue

        retr = round(_card_retrievability(row.get("fsrs"), ref_now, parameters=params), 3) if params else round(
            _card_retrievability(row.get("fsrs"), ref_now), 3
        )
        view = _public_fork_view(row, include_sealed=include_sealed)
        view["retrievability"] = retr
        view["isDue"] = (not due_str) or (due_str <= today_str) or status in ("active", "shaky")

        urgency = (
            0 if status == "shaky" else (1 if status == "active" else 2),
            retr,
            -int(row.get("blunderRepeatCount") or 0),
            str(row.get("id") or ""),
        )
        items.append((urgency, view))

    items.sort(key=lambda pair: pair[0])
    selected = [v for _, v in items[: max(1, int(limit))]]
    return {
        "ok": True,
        "today": today_str,
        "totalForks": len(forks),
        "dueCount": len(items),
        "items": selected,
    }


def mutate_fork(
    fork_id: str,
    *,
    workspace: Path | str | None = None,
    kg_dir: Path | str | None = None,
    seed: int | None = None,
    variant_index: int | None = None,
    substitutions: dict[str, str] | None = None,
    persist: bool = False,
    include_sealed: bool = False,
) -> dict[str, Any]:
    """Produce a deterministically mutated symbol/number FEN view for a fork."""
    forks = load_forks(workspace=workspace, kg_dir=kg_dir)
    target_idx = next((i for i, r in enumerate(forks) if str(r.get("id") or "") == str(fork_id).strip()), None)
    if target_idx is None:
        raise KeyError(f"Fork '{fork_id}' not found.")

    fork = forks[target_idx]
    pool: list[dict[str, Any]] = list(fork.get("mutationPool") or [])
    eff_seed = int(seed) if seed is not None else int(hashlib.sha256(f"{fork_id}:{len(pool)}".encode()).hexdigest()[:8], 16)

    variant: dict[str, Any]
    created_new = False
    if substitutions:
        vid = f"mut-{len(pool) + 1}"
        variant = _build_variant_dict(fork, substitutions, variant_id=vid)
        created_new = True
    elif variant_index is not None and pool:
        idx = int(variant_index) % len(pool)
        variant = dict(pool[idx])
    elif pool and seed is None:
        variant = dict(pool[0])
    else:
        subs = _auto_substitutions_for_fork(fork, eff_seed)
        vid = f"mut-s{eff_seed % 100000}"
        variant = _build_variant_dict(fork, subs, variant_id=vid)
        created_new = True

    if persist and created_new:
        pool = [v for v in pool if str(v.get("id")) != str(variant["id"])]
        pool.append(variant)
        fork["mutationPool"] = pool
        fork["updatedAt"] = _now_iso()
        forks[target_idx] = fork
        save_forks(forks, workspace=workspace, kg_dir=kg_dir)

    out_variant: dict[str, Any] = {
        "id": variant.get("id"),
        "label": variant.get("label"),
        "substitutions": variant.get("substitutions", {}),
        "prefixSteps": variant.get("prefixSteps", []),
        "forkPrompt": variant.get("forkPrompt", ""),
    }
    if include_sealed:
        out_variant["sealedBlunder"] = variant.get("sealedBlunder")
        out_variant["sealedWinningMove"] = variant.get("sealedWinningMove")
        out_variant["refutationMsg"] = variant.get("refutationMsg")

    return {
        "ok": True,
        "forkId": fork["id"],
        "seed": eff_seed,
        "persisted": bool(persist and created_new),
        "variant": out_variant,
    }


def check_fork_move(
    fork_id: str,
    move: str,
    *,
    workspace: Path | str | None = None,
    kg_dir: Path | str | None = None,
    latency_ms: int | None = None,
    variant_id: str | None = None,
    reveal_fix: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Grade a learner's move at a Blunder FEN fork, triggering refutation on blunder repeat."""
    forks = load_forks(workspace=workspace, kg_dir=kg_dir)
    target_idx = next((i for i, r in enumerate(forks) if str(r.get("id") or "") == str(fork_id).strip()), None)
    if target_idx is None:
        raise KeyError(f"Fork '{fork_id}' not found.")

    fork = forks[target_idx]
    active: dict[str, Any] = fork
    if variant_id:
        matched_var = next(
            (v for v in (fork.get("mutationPool") or []) if isinstance(v, dict) and str(v.get("id")) == str(variant_id)),
            None,
        )
        if matched_var is None:
            raise KeyError(f"Mutation variant '{variant_id}' not found on fork '{fork_id}'.")
        active = matched_var

    verify_spec = dict(fork.get("verifySpec") or {"engine": "exact"})
    engine = str(verify_spec.get("engine") or "exact").lower()
    raw_move = str(move or "")
    learner_move = raw_move.strip("\r\n") if engine == "code" else raw_move.strip()
    raw_win = str(active.get("sealedWinningMove") or fork.get("sealedWinningMove") or "")
    raw_blund = str(active.get("sealedBlunder") or fork.get("sealedBlunder") or "")
    winning_move = raw_win.strip("\r\n") if engine == "code" else raw_win.strip()
    blunder_move = raw_blund.strip("\r\n") if engine == "code" else raw_blund.strip()
    refutation_msg = str(active.get("refutationMsg") or fork.get("refutationMsg") or "").strip()
    prefix_steps = list(active.get("prefixSteps") or fork.get("prefixSteps") or [])

    is_win = False
    is_blunder = False
    verify_details: dict[str, Any] = {"engine": engine}

    if engine == "math":
        is_win = _math_equivalent(learner_move, winning_move)
        if not is_win:
            is_blunder = _math_equivalent(learner_move, blunder_move)
    elif engine == "code":
        norm_in = _normalize_text_move(learner_move)
        norm_win = _normalize_text_move(winning_move)
        norm_blunder = _normalize_text_move(blunder_move)
        if norm_in == norm_blunder:
            is_blunder = True
        code_res = _verify_code_move(prefix_steps, learner_move, verify_spec)
        verify_details["codeVerify"] = code_res
        if code_res.get("ran"):
            is_win = bool(code_res.get("passed"))
            if is_win:
                is_blunder = False
        else:
            is_win = norm_in == norm_win
    else:
        norm_in = _normalize_text_move(learner_move)
        is_win = norm_in == _normalize_text_move(winning_move)
        if not is_win:
            is_blunder = norm_in == _normalize_text_move(blunder_move)

    # Record latency history (Woodpecker latency compression)
    history = [int(x) for x in (fork.get("latencyMsHistory") or []) if isinstance(x, (int, float)) and x >= 0]
    if latency_ms is not None and int(latency_ms) >= 0:
        history.append(int(latency_ms))
    fork["latencyMsHistory"] = history

    ref_now = now or datetime.now(timezone.utc)
    kdir = _resolve_kg_dir(workspace=workspace, explicit=kg_dir)
    params = _load_fsrs_parameters(kdir)

    if is_win:
        outcome = "win"
        prev_fast = bool(latency_ms is not None and int(latency_ms) <= 8000 and int(fork.get("blunderRepeatCount") or 0) == 0)
        rating = "easy" if prev_fast else "good"
        fork["winCount"] = int(fork.get("winCount") or 0) + 1
        fork["status"] = "solid"
    elif is_blunder:
        outcome = "blunder_repeat"
        rating = "again"
        fork["blunderRepeatCount"] = int(fork.get("blunderRepeatCount") or 0) + 1
        fork["status"] = "shaky"
    else:
        outcome = "miss"
        rating = "hard"
        fork["status"] = "shaky"

    if params:
        new_card, meta = _review_card_dict(
            fork.get("fsrs"),
            rating,
            now=ref_now,
            parameters=params,
            review_duration_ms=int(latency_ms) if latency_ms is not None else None,
        )
    else:
        new_card, meta = _review_card_dict(fork.get("fsrs"), rating, now=ref_now)

    fork["fsrs"] = new_card
    fork["nextReview"] = str(new_card.get("due") or "")[:10] or _today_iso()
    fork["lastTested"] = ref_now.date().isoformat()
    fork["updatedAt"] = _now_iso()
    forks[target_idx] = fork
    save_forks(forks, workspace=workspace, kg_dir=kg_dir)

    latency_trend: dict[str, Any] = {
        "samples": len(history),
        "latestMs": history[-1] if history else None,
        "medianMs": int(round(statistics.median(history))) if history else None,
        "bestMs": min(history) if history else None,
        "compressionRatio": round(history[0] / history[-1], 2) if len(history) >= 2 and history[-1] > 0 else None,
    }

    result: dict[str, Any] = {
        "ok": True,
        "forkId": fork["id"],
        "variantId": variant_id,
        "outcome": outcome,
        "correct": bool(is_win),
        "repeatedBlunder": bool(is_blunder),
        "rating": rating,
        "status": fork["status"],
        "nextReview": fork["nextReview"],
        "intervalDays": int(meta.get("intervalDays") or 1),
        "stabilityDays": round(float(new_card.get("stability") or 0.0), 2),
        "blunderRepeatCount": int(fork["blunderRepeatCount"]),
        "winCount": int(fork["winCount"]),
        "latencyTrend": latency_trend,
        "verify": verify_details,
    }

    # Pedagogical core: on repeat blunder, play refutation BEFORE revealing the fix!
    if is_blunder:
        result["refutation"] = refutation_msg
        result["fixRevealed"] = bool(reveal_fix)
        if reveal_fix:
            result["winningMove"] = winning_move
        else:
            result["winningMove"] = None
            result["nextPrompt"] = "Re-read the refutation above and retry this fork (pass --reveal-fix only if stuck)."
    elif is_win:
        result["fixRevealed"] = True
        result["winningMove"] = winning_move
    else:
        result["refutation"] = None
        result["fixRevealed"] = bool(reveal_fix)
        result["winningMove"] = winning_move if reveal_fix else None
        if not reveal_fix:
            result["nextPrompt"] = "Not equivalent to the winning move. Retry from the frozen prefix (or pass --reveal-fix)."

    return result


def fork_stats(
    *,
    workspace: Path | str | None = None,
    kg_dir: Path | str | None = None,
) -> dict[str, Any]:
    """Summarize Blunder FEN fork mastery, blunder repeat rate, and latency compression."""
    forks = load_forks(workspace=workspace, kg_dir=kg_dir)
    today_str = _today_iso()
    due_n = 0
    solid_n = 0
    shaky_n = 0
    repeat_total = 0
    win_total = 0
    all_latencies: list[int] = []
    compressions: list[float] = []

    for row in forks:
        st = str(row.get("status") or "active").lower()
        due_str = str(row.get("nextReview") or "")[:10]
        if st in ("active", "shaky") or not due_str or due_str <= today_str:
            due_n += 1
        if st == "solid":
            solid_n += 1
        elif st == "shaky":
            shaky_n += 1
        repeat_total += int(row.get("blunderRepeatCount") or 0)
        win_total += int(row.get("winCount") or 0)
        hist = [int(x) for x in (row.get("latencyMsHistory") or []) if isinstance(x, (int, float)) and x > 0]
        all_latencies.extend(hist)
        if len(hist) >= 2 and hist[-1] > 0:
            compressions.append(hist[0] / hist[-1])

    return {
        "ok": True,
        "totalForks": len(forks),
        "dueCount": due_n,
        "solidCount": solid_n,
        "shakyCount": shaky_n,
        "totalWins": win_total,
        "totalBlunderRepeats": repeat_total,
        "medianLatencyMs": int(round(statistics.median(all_latencies))) if all_latencies else None,
        "meanCompressionRatio": round(sum(compressions) / len(compressions), 2) if compressions else None,
    }
