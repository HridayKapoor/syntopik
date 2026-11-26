#!/usr/bin/env python3
"""Adversarial Counterexample Arena ("Examplar + Lakatos Monster-Hunting").

Flips tutor-learner polarity: presents a theorem, physical law, or algorithm
spec alongside server-sealed conceptual mutants in `knowledge-graph/arenas.jsonl`.
The learner authors minimal adversarial inputs or symbolic counterexamples to
kill mutants deterministically (via `code_verify.py` / isolated Python sandbox
or SymPy / safe AST math evaluator) WITHOUT ever leaking surviving mutants'
code, formulas, or trap tags, then persists a 1-line Lakatos Lemma Repair into
the arena and the linked proposition's `boundaryConditions[]`.

Pure Python 3.12+ standard library only (optional lazy SymPy / code_verify.py).
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.util
import json
import math
import os
import re
import secrets
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# ---------------------------------------------------------------------------
# Safe syntopik.* imports with pure-stdlib fallbacks
# ---------------------------------------------------------------------------

try:
    from syntopik.paths import resolve_kg_dir as _resolve_kg_dir
    from syntopik.paths import resolve_package_root as _resolve_package_root
except Exception:  # pragma: no cover - standalone fallback
    def _resolve_package_root() -> Path:
        return ROOT

    def _resolve_kg_dir(workspace: Path | str | None = None, *, explicit: Path | str | None = None) -> Path:
        if explicit is not None and str(explicit).strip():
            return Path(explicit).expanduser().resolve()
        if workspace is not None and str(workspace).strip():
            return (Path(workspace).expanduser().resolve() / "knowledge-graph").resolve()
        env_kg = os.environ.get("SYNT_KG_DIR", "").strip() or os.environ.get("MLH_KG_DIR", "").strip()
        if env_kg:
            return Path(env_kg).expanduser().resolve()
        return (Path.cwd().resolve() / "knowledge-graph").resolve()


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
        fd, tmp = tempfile.mkstemp(dir=str(target.parent), prefix=f".{target.name}.", suffix=".tmp")
        tmp_path = Path(tmp)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write((body + "\n") if body else "")
            os.replace(tmp_path, target)
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise
        return target


try:
    from syntopik.schemas import now_iso as _now_iso
except Exception:  # pragma: no cover - standalone fallback
    def _now_iso() -> str:
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


__all__ = [
    "ARENAS_FILENAME",
    "resolve_arenas_path",
    "load_arenas",
    "save_arenas",
    "create_arena",
    "list_arenas",
    "test_arena_input",
    "repair_arena_lemma",
    "main",
]

ARENAS_FILENAME = "arenas.jsonl"

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

_STRICT_MATH_CONSTANTS: frozenset[str] = frozenset({"pi", "e", "E", "oo", "tau"})

_SYMBOLIC_ARENA_OPS: frozenset[str] = frozenset({"diff", "integrate", "simplify", "Matrix", "det", "trace"})

_MAX_EXPR_LEN = 512
_MAX_AST_NODES = 200
_MAX_AST_DEPTH = 32
_MAX_EXPONENT = 32
_MAX_COMPOUND_EXPONENT = 16
_MAX_SYMPY_COMPOUND_EXPONENT = 8
_MAX_POWER_LOG10 = 300.0
_MAX_INT_BITS = 1024
_IDENT_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")

_STRICT_MATH_NS: dict[str, Any] = {
    "pi": math.pi,
    "e": math.e,
    "E": math.e,
    "tau": math.tau,
    "oo": math.inf,
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

_SAFE_MATH_NS: dict[str, Any] = {
    **_STRICT_MATH_NS,
    "inf": math.inf,
    "atan2": math.atan2,
    "log10": math.log10,
    "min": min,
    "max": max,
    "sum": sum,
    "len": len,
    "round": round,
    "all": all,
    "any": any,
    "True": True,
    "False": False,
}

_STRICT_MATH_AST_TYPES = (
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

_SAFE_AST_TYPES = (
    *_STRICT_MATH_AST_TYPES,
    ast.BoolOp,
    ast.Compare,
    ast.IfExp,
    ast.List,
    ast.Tuple,
    ast.Dict,
    ast.Set,
    ast.Subscript,
    ast.Slice,
    ast.Not,
    ast.And,
    ast.Or,
    ast.Eq,
    ast.NotEq,
    ast.Lt,
    ast.LtE,
    ast.Gt,
    ast.GtE,
    ast.In,
    ast.NotIn,
)


def resolve_arenas_path(
    workspace: Path | str | None = None,
    kg_dir: Path | str | None = None,
) -> Path:
    return _resolve_kg_dir(workspace=workspace, explicit=kg_dir) / ARENAS_FILENAME


def load_arenas(
    workspace: Path | str | None = None,
    kg_dir: Path | str | None = None,
) -> list[dict[str, Any]]:
    return _load_jsonl(resolve_arenas_path(workspace=workspace, kg_dir=kg_dir))


def save_arenas(
    arenas: list[dict[str, Any]],
    workspace: Path | str | None = None,
    kg_dir: Path | str | None = None,
) -> Path:
    return _save_jsonl_atomic(resolve_arenas_path(workspace=workspace, kg_dir=kg_dir), arenas)


# ---------------------------------------------------------------------------
# Minimality scoring & structural comparison
# ---------------------------------------------------------------------------


def _structural_size(val: Any) -> int:
    if val is None or isinstance(val, bool):
        return 1
    if isinstance(val, (int, float)):
        mag = abs(val)
        return 1 if mag <= 10 else 1 + int(math.log10(max(1.0, mag)))
    if isinstance(val, str):
        return max(1, len(val.strip()))
    if isinstance(val, (list, tuple, set)):
        return 1 + sum(_structural_size(x) for x in val)
    if isinstance(val, dict):
        return 1 + sum(_structural_size(k) + _structural_size(v) for k, v in val.items())
    return max(1, len(str(val)))


def _minimality_score(input_size: int) -> float:
    sz = max(1, int(input_size))
    if sz <= 3:
        return 1.0
    return round(max(0.05, min(1.0, 1.0 / (1.0 + math.log1p(sz - 3) / 2.2))), 3)


def _values_equal(a: Any, b: Any, *, tol: float = 1e-6) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        fa, fb = float(a), float(b)
        if math.isnan(fa) or math.isnan(fb):
            return False
        if math.isinf(fa) or math.isinf(fb):
            return fa == fb
        return abs(fa - fb) <= max(tol, tol * max(abs(fa), abs(fb)))
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        if len(a) != len(b):
            return False
        return all(_values_equal(x, y, tol=tol) for x, y in zip(a, b))
    if isinstance(a, dict) and isinstance(b, dict):
        if set(a.keys()) != set(b.keys()):
            return False
        return all(_values_equal(a[k], b[k], tol=tol) for k in a)
    return a == b


# ---------------------------------------------------------------------------
# Safe AST math/physics evaluator (with optional lazy SymPy)
# ---------------------------------------------------------------------------


def _prep_expr(expr: str) -> str:
    s = str(expr or "").strip().strip("$").strip()
    s = s.replace("^", "**").replace("−", "-")
    # Safe implicit multiplication (preserves scientific notation like 1e-6)
    s = re.sub(r"(\d)\s*(?![eE][+-]?\d)([A-Za-z_(])", r"\1*\2", s)
    s = re.sub(r"(\))\s*([A-Za-z0-9_(])", r"\1*\2", s)
    return s


def _is_clean_ident(name: str) -> bool:
    return bool(isinstance(name, str) and _IDENT_RE.match(name) and "__" not in name and not name.startswith("_"))


def _safe_pow(base: Any, exp: Any) -> Any:
    if isinstance(base, bool) or isinstance(exp, bool):
        raise ValueError("Boolean operands are disallowed in exponentiation.")
    if not isinstance(base, (int, float)) or not isinstance(exp, (int, float)):
        raise ValueError("Exponentiation requires numeric operands.")
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
    """Safely constant-fold a purely numeric AST subtree, raising ValueError on huge powers."""
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


def _is_atomic_base(node: ast.AST) -> bool:
    if isinstance(node, (ast.Name, ast.Constant)):
        return True
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        return isinstance(node.operand, (ast.Name, ast.Constant))
    return False


def _contains_add_sub(node: ast.AST) -> bool:
    return any(isinstance(n, ast.BinOp) and isinstance(n.op, (ast.Add, ast.Sub)) for n in ast.walk(node))


def _estimate_poly_degree(node: ast.AST) -> int:
    if isinstance(node, ast.Expression):
        return _estimate_poly_degree(node.body)
    if isinstance(node, ast.Constant):
        return 0
    if isinstance(node, ast.Name):
        return 0 if node.id in _STRICT_MATH_NS else 1
    if isinstance(node, ast.UnaryOp):
        return _estimate_poly_degree(node.operand)
    if isinstance(node, ast.Call):
        return max((_estimate_poly_degree(a) for a in node.args), default=0)
    if isinstance(node, ast.BinOp):
        ld = _estimate_poly_degree(node.left)
        rd = _estimate_poly_degree(node.right)
        if isinstance(node.op, (ast.Add, ast.Sub)):
            return max(ld, rd)
        if isinstance(node.op, ast.Mult):
            return min(1000, ld + rd)
        if isinstance(node.op, (ast.Div, ast.FloorDiv, ast.Mod)):
            return max(ld, rd)
        if isinstance(node.op, ast.Pow):
            exp_val = _try_const_numeric(node.right)
            if exp_val is not None:
                return min(1000, int(math.ceil(abs(float(exp_val)))) * max(1, ld))
            return 1000
    return 0


_MAX_SYMPY_EXPANDED_TERMS = 64


def _count_additive_terms(node: ast.AST) -> int:
    """Upper-bound estimate of expanded additive monomials for an AST subtree."""
    if isinstance(node, ast.Expression):
        return _count_additive_terms(node.body)
    if isinstance(node, ast.UnaryOp):
        return _count_additive_terms(node.operand)
    if isinstance(node, ast.BinOp):
        if isinstance(node.op, (ast.Add, ast.Sub)):
            return min(100_000, _count_additive_terms(node.left) + _count_additive_terms(node.right))
        if isinstance(node.op, ast.Mult):
            return min(100_000, _count_additive_terms(node.left) * _count_additive_terms(node.right))
        if isinstance(node.op, ast.Pow):
            base_terms = _count_additive_terms(node.left)
            if base_terms <= 1:
                return 1
            exp_val = _try_const_numeric(node.right)
            if exp_val is None or not math.isfinite(float(exp_val)):
                return 100_000
            exp_int = int(math.ceil(abs(float(exp_val))))
            if exp_int <= 0:
                return 1
            if base_terms > 32 or exp_int > 32:
                return 100_000
            return min(100_000, math.comb(base_terms + exp_int - 1, exp_int))
    return 1


def _has_high_symbolic_expansion_risk(parsed: ast.AST) -> bool:
    """Return True if SymPy simplify()/expand() could experience combinatorial polynomial blowup."""
    has_add_sub = _contains_add_sub(parsed)
    for node in ast.walk(parsed):
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Pow):
            exp_val = _try_const_numeric(node.right)
            if not _is_atomic_base(node.left):
                if exp_val is None or not math.isfinite(float(exp_val)):
                    return True
                exp_abs = abs(float(exp_val))
                if exp_abs > _MAX_SYMPY_COMPOUND_EXPONENT:
                    return True
                if _contains_pow(node.left) and exp_abs > 2:
                    return True
                terms = _count_additive_terms(node.left)
                exp_int = int(math.ceil(exp_abs))
                if terms >= 3 and exp_abs > 3:
                    return True
                if terms >= 2 and exp_int >= 2:
                    est = 100_000 if (terms > 32 or exp_int > 32) else math.comb(terms + exp_int - 1, exp_int)
                    if est > _MAX_SYMPY_EXPANDED_TERMS:
                        return True
            elif exp_val is None or not math.isfinite(float(exp_val)) or abs(float(exp_val)) > _MAX_COMPOUND_EXPONENT:
                return True
    if _count_additive_terms(parsed) > _MAX_SYMPY_EXPANDED_TERMS:
        return True
    if has_add_sub and _estimate_poly_degree(parsed) > _MAX_COMPOUND_EXPONENT:
        return True
    return False


def _check_ast_depth(node: ast.AST, depth: int = 0) -> None:
    if depth > _MAX_AST_DEPTH:
        raise ValueError("Expression nesting depth exceeds safe limit.")
    for child in ast.iter_child_nodes(node):
        _check_ast_depth(child, depth + 1)


def validate_math_expr_ast(expr: str, *, allow_symbolic_ops: bool = False) -> ast.Expression:
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
    allowed_calls = _STRICT_MATH_CALLABLES | (_SYMBOLIC_ARENA_OPS if allow_symbolic_ops else frozenset())

    node_count = 0
    for node in ast.walk(parsed):
        node_count += 1
        if node_count > _MAX_AST_NODES:
            raise ValueError("Math expression exceeds safe AST complexity limit.")
        if not isinstance(node, _STRICT_MATH_AST_TYPES):
            raise ValueError(f"Disallowed expression construct: {type(node).__name__}")
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
                raise ValueError("Disallowed callable target.")
            if not _is_clean_ident(node.func.id) or node.func.id not in allowed_calls:
                raise ValueError(f"Disallowed function call: {node.func.id!r}")
            if node.keywords:
                raise ValueError("Keyword arguments are disallowed in math expressions.")
            if any(isinstance(arg, ast.Starred) for arg in node.args):
                raise ValueError("Starred arguments are disallowed in math expressions.")
        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Pow):
            if _contains_pow(node.right):
                raise ValueError("Nested exponentiation in exponent is disallowed.")
            exp_val = _try_const_numeric(node.right)
            atomic_base = _is_atomic_base(node.left)
            if exp_val is not None:
                max_exp = _MAX_EXPONENT if atomic_base else _MAX_COMPOUND_EXPONENT
                if not math.isfinite(float(exp_val)) or abs(float(exp_val)) > max_exp:
                    raise ValueError(f"Exponent exceeds safe limit (|exp| <= {max_exp}).")
                if not atomic_base and _contains_pow(node.left) and abs(float(exp_val)) > 4:
                    raise ValueError("Nested compound exponentiation exceeds safe limit.")
                base_val = _try_const_numeric(node.left)
                if base_val is not None:
                    _safe_pow(base_val, exp_val)
            elif not atomic_base and _contains_pow(node.left):
                raise ValueError("Nested compound exponentiation with symbolic exponent is disallowed.")
    return parsed


def _eval_ast_node(node: ast.AST, ns: dict[str, Any], *, allow_predicates: bool = False) -> Any:
    if isinstance(node, ast.Expression):
        return _eval_ast_node(node.body, ns, allow_predicates=allow_predicates)
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        if node.id not in ns:
            raise ValueError(f"Unknown variable: {node.id}")
        return ns[node.id]
    if isinstance(node, ast.UnaryOp):
        val = _eval_ast_node(node.operand, ns, allow_predicates=allow_predicates)
        if isinstance(node.op, ast.UAdd):
            return +val
        if isinstance(node.op, ast.USub):
            return -val
        if allow_predicates and isinstance(node.op, ast.Not):
            return not val
        raise ValueError(f"Disallowed unary operator: {type(node.op).__name__}")
    if isinstance(node, ast.BinOp):
        left = _eval_ast_node(node.left, ns, allow_predicates=allow_predicates)
        right = _eval_ast_node(node.right, ns, allow_predicates=allow_predicates)
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
            raise ValueError(f"Disallowed binary operator: {type(node.op).__name__}")
        if isinstance(res, int) and res.bit_length() > _MAX_INT_BITS:
            raise ValueError("Integer magnitude exceeds safe limit.")
        return res
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in ns:
            raise ValueError("Disallowed function call")
        fn = ns[node.func.id]
        if not callable(fn):
            raise ValueError(f"Target is not callable: {node.func.id}")
        args = [_eval_ast_node(a, ns, allow_predicates=allow_predicates) for a in node.args]
        return fn(*args)
    if allow_predicates:
        if isinstance(node, ast.BoolOp):
            if isinstance(node.op, ast.And):
                out: Any = True
                for v in node.values:
                    out = _eval_ast_node(v, ns, allow_predicates=True)
                    if not out:
                        return out
                return out
            if isinstance(node.op, ast.Or):
                out = False
                for v in node.values:
                    out = _eval_ast_node(v, ns, allow_predicates=True)
                    if out:
                        return out
                return out
        if isinstance(node, ast.Compare):
            left = _eval_ast_node(node.left, ns, allow_predicates=True)
            for op, comp in zip(node.ops, node.comparators):
                right = _eval_ast_node(comp, ns, allow_predicates=True)
                if isinstance(op, ast.Eq):
                    ok = left == right
                elif isinstance(op, ast.NotEq):
                    ok = left != right
                elif isinstance(op, ast.Lt):
                    ok = left < right
                elif isinstance(op, ast.LtE):
                    ok = left <= right
                elif isinstance(op, ast.Gt):
                    ok = left > right
                elif isinstance(op, ast.GtE):
                    ok = left >= right
                elif isinstance(op, ast.In):
                    ok = left in right
                elif isinstance(op, ast.NotIn):
                    ok = left not in right
                else:
                    raise ValueError(f"Disallowed comparator: {type(op).__name__}")
                if not ok:
                    return False
                left = right
            return True
        if isinstance(node, ast.IfExp):
            cond = _eval_ast_node(node.test, ns, allow_predicates=True)
            return _eval_ast_node(node.body if cond else node.orelse, ns, allow_predicates=True)
        if isinstance(node, ast.List):
            return [_eval_ast_node(e, ns, allow_predicates=True) for e in node.elts]
        if isinstance(node, ast.Tuple):
            return tuple(_eval_ast_node(e, ns, allow_predicates=True) for e in node.elts)
        if isinstance(node, ast.Set):
            return {_eval_ast_node(e, ns, allow_predicates=True) for e in node.elts}
        if isinstance(node, ast.Dict):
            return {
                _eval_ast_node(k, ns, allow_predicates=True): _eval_ast_node(v, ns, allow_predicates=True)
                for k, v in zip(node.keys, node.values)
                if k is not None
            }
        if isinstance(node, ast.Subscript):
            container = _eval_ast_node(node.value, ns, allow_predicates=True)
            if isinstance(node.slice, ast.Slice):
                lo = _eval_ast_node(node.slice.lower, ns, allow_predicates=True) if node.slice.lower is not None else None
                hi = _eval_ast_node(node.slice.upper, ns, allow_predicates=True) if node.slice.upper is not None else None
                st = _eval_ast_node(node.slice.step, ns, allow_predicates=True) if node.slice.step is not None else None
                return container[slice(lo, hi, st)]
            idx = _eval_ast_node(node.slice, ns, allow_predicates=True)
            return container[idx]
    raise ValueError(f"Disallowed expression construct: {type(node).__name__}")


def _safe_eval_expr(expr: str, env: dict[str, Any], *, allow_predicates: bool = False) -> Any:
    code = _prep_expr(expr)
    if not allow_predicates:
        parsed = validate_math_expr_ast(code, allow_symbolic_ops=False)
        for node in ast.walk(parsed):
            if isinstance(node, ast.Name) and node.id not in env and node.id not in _STRICT_MATH_NS:
                raise ValueError(f"Unknown variable: {node.id}")
        ns = dict(_STRICT_MATH_NS)
        ns.update(env)
        return _eval_ast_node(parsed, ns, allow_predicates=False)

    if len(code) > _MAX_EXPR_LEN:
        raise ValueError(f"Predicate expression exceeds {_MAX_EXPR_LEN} character limit.")
    parsed = ast.parse(code, mode="eval")
    _check_ast_depth(parsed)
    node_count = 0
    for node in ast.walk(parsed):
        node_count += 1
        if node_count > _MAX_AST_NODES:
            raise ValueError("Predicate expression exceeds safe AST complexity limit.")
        if not isinstance(node, _SAFE_AST_TYPES):
            raise ValueError(f"Disallowed expression construct: {type(node).__name__}")
        if isinstance(node, ast.Call):
            if (
                not isinstance(node.func, ast.Name)
                or not _is_clean_ident(node.func.id)
                or node.func.id not in _SAFE_MATH_NS
                or node.keywords
                or any(isinstance(a, ast.Starred) for a in node.args)
            ):
                raise ValueError("Disallowed function call")
        if isinstance(node, ast.Name):
            if not _is_clean_ident(node.id):
                raise ValueError(f"Disallowed identifier: {node.id!r}")
            if node.id not in env and node.id not in _SAFE_MATH_NS:
                raise ValueError(f"Unknown variable: {node.id}")
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Pow):
            if _contains_pow(node.right):
                raise ValueError("Nested exponentiation in exponent is disallowed.")
            exp_val = _try_const_numeric(node.right)
            atomic_base = _is_atomic_base(node.left)
            if exp_val is not None:
                max_exp = _MAX_EXPONENT if atomic_base else _MAX_COMPOUND_EXPONENT
                if not math.isfinite(float(exp_val)) or abs(float(exp_val)) > max_exp:
                    raise ValueError(f"Exponent exceeds safe limit (|exp| <= {max_exp}).")
                if not atomic_base and _contains_pow(node.left) and abs(float(exp_val)) > 4:
                    raise ValueError("Nested compound exponentiation exceeds safe limit.")
            elif not atomic_base and _contains_pow(node.left):
                raise ValueError("Nested compound exponentiation with symbolic exponent is disallowed.")
    ns = dict(_SAFE_MATH_NS)
    ns.update(env)
    return _eval_ast_node(parsed, ns, allow_predicates=True)


def _validate_math_value(val: Any, *, depth: int = 0) -> Any:
    if isinstance(val, bool):
        raise ValueError("Boolean values are disallowed in math inputs.")
    if isinstance(val, (int, float)):
        if isinstance(val, float) and not math.isfinite(val):
            raise ValueError("Non-finite numeric input.")
        if isinstance(val, int) and val.bit_length() > _MAX_INT_BITS:
            raise ValueError("Integer input exceeds safe size limit.")
        return val
    if isinstance(val, str):
        validate_math_expr_ast(_prep_expr(val), allow_symbolic_ops=False)
        return val
    if isinstance(val, list):
        if depth >= 2 or len(val) > 64:
            raise ValueError("Matrix/list math input exceeds safe dimensions.")
        return [_validate_math_value(item, depth=depth + 1) for item in val]
    raise ValueError(f"Disallowed math input type: {type(val).__name__}")


def _validate_math_env(env: dict[str, Any]) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    for k, v in env.items():
        if not isinstance(k, str) or not _is_clean_ident(k):
            raise ValueError(f"Disallowed variable name: {k!r}")
        clean[k] = _validate_math_value(v)
    return clean


def _env_has_expansion_risk(val: Any) -> bool:
    if isinstance(val, str):
        parsed = validate_math_expr_ast(_prep_expr(val), allow_symbolic_ops=False)
        return _has_high_symbolic_expansion_risk(parsed)
    if isinstance(val, list):
        return any(_env_has_expansion_risk(item) for item in val)
    return False


def _try_sympy_eval(expr: str, raw_env: dict[str, Any]) -> tuple[bool, Any]:
    """Evaluate `expr` with SymPy ONLY after strict math-only AST validation and zero-builtins global_dict."""
    try:
        clean_env = _validate_math_env(raw_env)
        prep_main = _prep_expr(expr)
        parsed_main = validate_math_expr_ast(prep_main, allow_symbolic_ops=True)
        high_expansion_risk = _has_high_symbolic_expansion_risk(parsed_main) or any(
            _env_has_expansion_risk(v) for v in clean_env.values()
        )

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

        def _parse_validated(raw_s: str, loc: dict[str, Any], *, allow_sym_ops: bool) -> Any:
            prepped = _prep_expr(raw_s)
            validate_math_expr_ast(prepped, allow_symbolic_ops=allow_sym_ops)
            return parse_expr(prepped, local_dict=loc, global_dict=safe_global, transformations=tr)

        def _to_sympy_item(v: Any, loc: dict[str, Any]) -> Any:
            if isinstance(v, int):
                return sp.Integer(v)
            if isinstance(v, float):
                return sp.Float(v)
            if isinstance(v, str):
                return _parse_validated(v, loc, allow_sym_ops=False)
            if isinstance(v, list):
                return [_to_sympy_item(item, loc) for item in v]
            raise ValueError(f"Unsupported value type: {type(v).__name__}")

        def _to_sympy_val(v: Any, loc: dict[str, Any]) -> Any:
            if isinstance(v, list):
                return sp.Matrix(_to_sympy_item(v, loc))
            return _to_sympy_item(v, loc)

        learner_local: dict[str, Any] = {
            k: safe_global[k]
            for k in (
                "pi", "e", "E", "oo", "tau",
                "sin", "cos", "tan", "asin", "acos", "atan",
                "sinh", "cosh", "tanh", "exp", "log", "ln", "sqrt",
                "abs", "Abs", "floor", "ceil", "ceiling",
            )
        }
        for k, v in clean_env.items():
            learner_local[k] = _to_sympy_val(v, learner_local)

        def _guarded_simplify(obj: Any) -> Any:
            if high_expansion_risk:
                return obj
            if hasattr(obj, "count_ops") and int(obj.count_ops()) > 120:
                return obj
            if hasattr(obj, "atoms"):
                for pw in obj.atoms(sp.Pow):
                    base_e, exp_e = pw.as_base_exp()
                    if not base_e.is_Atom:
                        if not exp_e.is_Number or abs(float(exp_e)) > _MAX_SYMPY_COMPOUND_EXPONENT:
                            return obj
                        if isinstance(base_e, sp.Add):
                            n_terms = len(base_e.args)
                            exp_abs = abs(float(exp_e))
                            exp_int = int(math.ceil(exp_abs))
                            if (
                                (n_terms >= 3 and exp_abs > 3)
                                or n_terms > 32
                                or exp_int > 32
                                or (n_terms >= 2 and exp_int >= 2 and math.comb(n_terms + exp_int - 1, exp_int) > _MAX_SYMPY_EXPANDED_TERMS)
                            ):
                                return obj
            return sp.simplify(obj)

        arena_local: dict[str, Any] = {
            **learner_local,
            "diff": sp.diff,
            "integrate": sp.integrate,
            "simplify": _guarded_simplify,
            "Matrix": sp.Matrix,
            "det": lambda m: sp.Matrix(m).det(),
            "trace": lambda m: sp.Matrix(m).trace(),
        }

        res = _parse_validated(prep_main, arena_local, allow_sym_ops=True)
        res = _guarded_simplify(res)
        if isinstance(res, sp.MatrixBase):
            return True, [[float(sp.N(el, 12)) for el in res.row(r)] for r in range(res.rows)]
        if hasattr(res, "free_symbols") and not res.free_symbols:
            c = complex(sp.N(res, 12))
            if abs(c.imag) < 1e-10:
                return True, float(c.real)
            return True, str(res)
        return True, str(res)
    except Exception:
        return False, None


def _parse_math_input(raw_input: str, expected_vars: list[str]) -> tuple[dict[str, Any], int]:
    text = str(raw_input or "").strip()
    if not text:
        raise ValueError("Input cannot be empty.")

    # 1. JSON dict / list / number
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return _validate_math_env(parsed), _structural_size(parsed)
        primary = expected_vars[0] if expected_vars else "x"
        return _validate_math_env({primary: parsed}), _structural_size(parsed)
    except json.JSONDecodeError:
        pass

    # 2. `k=v, k2=v2` assignments
    if "=" in text and not any(op in text for op in ("==", "<=", ">=", "!=")):
        env: dict[str, Any] = {}
        for part in text.split(","):
            if "=" not in part:
                continue
            k, v = part.split("=", 1)
            key = k.strip()
            if not _is_clean_ident(key):
                raise ValueError(f"Disallowed variable name: {key!r}")
            v_str = v.strip()
            try:
                val: Any = json.loads(v_str)
                val = _validate_math_value(val)
            except json.JSONDecodeError:
                validate_math_expr_ast(_prep_expr(v_str), allow_symbolic_ops=False)
                try:
                    val = float(_safe_eval_expr(v_str, env))
                except ValueError as exc:
                    if "Unknown variable:" in str(exc):
                        val = v_str
                    else:
                        raise
            env[key] = val
        if env:
            return _validate_math_env(env), _structural_size(env)

    # 3. Single scalar/expression mapped to primary var
    primary = expected_vars[0] if expected_vars else "x"
    validate_math_expr_ast(_prep_expr(text), allow_symbolic_ops=False)
    try:
        val = float(_safe_eval_expr(text, {}))
        return _validate_math_env({primary: val}), _structural_size(val)
    except ValueError as exc:
        if "Unknown variable:" in str(exc):
            return _validate_math_env({primary: text}), _structural_size(text)
        raise


# ---------------------------------------------------------------------------
# Isolated Code Sandbox Execution (uses code_verify.py or -B -I subprocess)
# ---------------------------------------------------------------------------

_PY_SANDBOX_BATCH_RUNNER = r"""
import importlib.util, json, sys, traceback

cfg = json.loads(open(sys.argv[1], "r", encoding="utf-8").read())
marker = cfg["marker"]
entry = cfg["entry"]
args = cfg["args"]
kwargs = cfg["kwargs"]

def _run_source(src_path, fn_name, a, kw):
    spec = importlib.util.spec_from_file_location("arena_mod", src_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    target = mod
    for part in fn_name.split("."):
        target = getattr(target, part)
    res = target(*a, **kw)
    if isinstance(res, tuple):
        res = list(res)
    elif isinstance(res, set):
        res = sorted(list(res), key=repr)
    return res

try:
    val = _run_source(cfg["sourcePath"], entry, args, kwargs)
    print(marker + json.dumps({"ok": True, "value": val}, default=repr))
except Exception as exc:
    print(marker + json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}))
"""


def _sandbox_env(work_dir: Path) -> dict[str, str]:
    env = {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "HOME": str(work_dir),
        "TMPDIR": str(work_dir),
        "TMP": str(work_dir),
        "TEMP": str(work_dir),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUNBUFFERED": "1",
        "PYTHONNOUSERSITE": "1",
    }
    if os.name == "nt":
        env["USERPROFILE"] = str(work_dir)
        for win_key in ("SYSTEMROOT", "WINDIR", "SYSTEMDRIVE", "COMSPEC", "PATHEXT", "OS"):
            val = os.environ.get(win_key)
            if val:
                env[win_key] = val
    return env


def _load_code_verify() -> Any | None:
    cv_path = _resolve_package_root() / "scripts" / "code_verify.py"
    if not cv_path.is_file():
        return None
    try:
        spec = importlib.util.spec_from_file_location("_syntopik_arena_cv", str(cv_path))
        if spec is None or spec.loader is None:
            return None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception:
        return None


def _run_python_entry_isolated(
    code: str,
    entry: str,
    call_args: list[Any],
    call_kwargs: dict[str, Any],
    *,
    timeout_sec: float = 3.0,
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="synt-arena-") as tmp:
        wdir = Path(tmp)
        src_p = wdir / "program.py"
        runner_p = wdir / "_runner.py"
        cfg_p = wdir / "_cfg.json"
        marker = f"__ARENA_{secrets.token_hex(6)}__:"

        src_p.write_text(code, encoding="utf-8")
        runner_p.write_text(_PY_SANDBOX_BATCH_RUNNER, encoding="utf-8")
        cfg_p.write_text(
            json.dumps(
                {
                    "sourcePath": str(src_p),
                    "entry": entry,
                    "args": call_args,
                    "kwargs": call_kwargs,
                    "marker": marker,
                }
            ),
            encoding="utf-8",
        )
        try:
            proc = subprocess.run(
                [sys.executable, "-B", "-I", str(runner_p), str(cfg_p)],
                cwd=str(wdir),
                env=_sandbox_env(wdir),
                capture_output=True,
                text=True,
                timeout=timeout_sec,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return {"ok": False, "status": "timeout", "error": f"Timed out after {timeout_sec}s"}

        marker_line = next((ln for ln in reversed(proc.stdout.splitlines()) if ln.startswith(marker)), None)
        if marker_line:
            try:
                parsed = json.loads(marker_line[len(marker) :])
                parsed["status"] = "ok" if parsed.get("ok") else "runtime_error"
                return parsed
            except json.JSONDecodeError:
                pass
        return {
            "ok": False,
            "status": "runtime_error",
            "error": (proc.stderr or proc.stdout).strip() or f"Exited with code {proc.returncode}",
        }


def _check_code_assumptions(
    predicates: list[Any],
    raw_val: Any,
    call_args: list[Any],
    call_kwargs: dict[str, Any],
    timeout_sec: float = 2.0,
) -> tuple[bool, list[str]]:
    failed: list[str] = []
    for idx, pred in enumerate(predicates or []):
        if isinstance(pred, str):
            pid = f"assumption-{idx + 1}"
            desc = pred
            expr_str = pred
            code_str = None
        elif isinstance(pred, dict):
            pid = str(pred.get("id") or f"assumption-{idx + 1}")
            desc = str(pred.get("description") or pred.get("expr") or pid)
            expr_str = pred.get("expr")
            code_str = pred.get("code")
        else:
            continue

        if isinstance(code_str, str) and code_str.strip():
            res = _run_python_entry_isolated(
                code_str,
                "check",
                [raw_val],
                {},
                timeout_sec=timeout_sec,
            )
            if not res.get("ok") or not bool(res.get("value")):
                failed.append(desc)
        elif isinstance(expr_str, str) and expr_str.strip():
            try:
                env = {"inp": raw_val, "args": call_args, "kwargs": call_kwargs}
                if isinstance(raw_val, dict):
                    env.update(raw_val)
                ok = bool(_safe_eval_expr(expr_str, env, allow_predicates=True))
                if not ok:
                    failed.append(desc)
            except Exception:
                failed.append(desc)
    return len(failed) == 0, failed


def _check_math_assumptions(predicates: list[Any], env: dict[str, Any]) -> tuple[bool, list[str]]:
    failed: list[str] = []
    for idx, pred in enumerate(predicates or []):
        if isinstance(pred, str):
            desc = pred
            expr_str = pred
        elif isinstance(pred, dict):
            desc = str(pred.get("description") or pred.get("expr") or f"assumption-{idx + 1}")
            expr_str = str(pred.get("expr") or "")
        else:
            continue
        if not expr_str.strip():
            continue
        try:
            ok = bool(_safe_eval_expr(expr_str, env, allow_predicates=True))
            if not ok:
                failed.append(desc)
        except Exception:
            failed.append(desc)
    return len(failed) == 0, failed


def _public_arena_summary(arena: dict[str, Any]) -> dict[str, Any]:
    """Return arena metadata WITHOUT leaking sealedReference or sealedMutants code/expr/tags."""
    mutants = arena.get("sealedMutants") or []
    killed = set(str(k) for k in (arena.get("killedMutants") or []))
    total = len(mutants)
    killed_n = sum(1 for m in mutants if isinstance(m, dict) and str(m.get("id")) in killed)
    return {
        "id": arena.get("id"),
        "propId": arena.get("propId"),
        "domain": arena.get("domain"),
        "title": arena.get("title"),
        "claim": arena.get("claim"),
        "entry": arena.get("entry"),
        "lang": arena.get("lang"),
        "assumptions": [
            (p.get("description") or p.get("expr") or str(p)) if isinstance(p, dict) else str(p)
            for p in (arena.get("assumptionPredicates") or [])
        ],
        "totalMutants": total,
        "killedCount": killed_n,
        "survivedCount": max(0, total - killed_n),
        "killedMutants": sorted(killed),
        "bestMinimalSize": arena.get("bestMinimalSize"),
        "lemmaRepairsCount": len(arena.get("lemmaRepairs") or []),
        "updatedAt": arena.get("updatedAt"),
    }


# ---------------------------------------------------------------------------
# Public API: create_arena, list_arenas, test_arena_input, repair_arena_lemma
# ---------------------------------------------------------------------------


def create_arena(
    *,
    prop_id: str = "",
    domain: str = "code",
    claim: str = "",
    sealed_reference: dict[str, Any] | str,
    sealed_mutants: list[dict[str, Any]],
    assumption_predicates: list[Any] | None = None,
    title: str | None = None,
    entry: str | None = None,
    lang: str = "python",
    arena_id: str | None = None,
    workspace: Path | str | None = None,
    kg_dir: Path | str | None = None,
) -> dict[str, Any]:
    """Create (or update) a server-sealed Adversarial Counterexample Arena."""
    dom = str(domain or "code").strip().lower()
    if dom not in ("code", "math", "physics"):
        raise ValueError(f"Unsupported domain {domain!r}; expected code|math|physics.")
    if not str(claim or "").strip():
        raise ValueError("claim is required.")
    if not isinstance(sealed_mutants, list) or not sealed_mutants:
        raise ValueError("At least one sealed mutant is required.")

    ref_obj: dict[str, Any]
    if isinstance(sealed_reference, str):
        ref_obj = {"code": sealed_reference} if dom == "code" else {"expr": sealed_reference}
    elif isinstance(sealed_reference, dict):
        ref_obj = dict(sealed_reference)
    else:
        raise ValueError("sealed_reference must be a string or dict.")

    norm_mutants: list[dict[str, Any]] = []
    for idx, m in enumerate(sealed_mutants):
        if not isinstance(m, dict):
            raise ValueError("Each mutant must be a JSON object.")
        mid = str(m.get("id") or f"m{idx + 1}").strip()
        trap = str(m.get("trapTag") or f"trap_{idx + 1}").strip()
        entry_obj: dict[str, Any] = {"id": mid, "trapTag": trap}
        if "code" in m:
            entry_obj["code"] = str(m["code"])
        if "expr" in m:
            entry_obj["expr"] = str(m["expr"])
        if "code" not in entry_obj and "expr" not in entry_obj:
            raise ValueError(f"Mutant '{mid}' must contain 'code' or 'expr'.")
        norm_mutants.append(entry_obj)

    aid = str(arena_id or "").strip()
    if not aid:
        digest = hashlib.sha256(f"{prop_id}|{claim}|{uuid.uuid4().hex[:8]}".encode("utf-8")).hexdigest()[:8]
        aid = f"arena-{digest}"

    now_str = _now_iso()
    record: dict[str, Any] = {
        "id": aid,
        "propId": str(prop_id or "").strip() or None,
        "domain": dom,
        "title": str(title or claim[:72]).strip(),
        "claim": str(claim).strip(),
        "entry": str(entry or ref_obj.get("entry") or "solve").strip() if dom == "code" else None,
        "lang": str(lang or "python").strip().lower() if dom == "code" else None,
        "sealedReference": ref_obj,
        "sealedMutants": norm_mutants,
        "assumptionPredicates": list(assumption_predicates or []),
        "killedMutants": [],
        "bestMinimalSize": None,
        "attempts": 0,
        "lemmaRepairs": [],
        "createdAt": now_str,
        "updatedAt": now_str,
    }

    arenas = load_arenas(workspace=workspace, kg_dir=kg_dir)
    replaced = False
    for idx, existing in enumerate(arenas):
        if str(existing.get("id") or "") == aid:
            record["createdAt"] = existing.get("createdAt") or now_str
            arenas[idx] = record
            replaced = True
            break
    if not replaced:
        arenas.append(record)

    out_path = save_arenas(arenas, workspace=workspace, kg_dir=kg_dir)
    return {
        "ok": True,
        "action": "updated" if replaced else "created",
        "path": str(out_path),
        "id": aid,
        "arena": _public_arena_summary(record),
    }


def list_arenas(
    *,
    workspace: Path | str | None = None,
    kg_dir: Path | str | None = None,
    domain: str | None = None,
    prop_id: str | None = None,
) -> dict[str, Any]:
    """List arenas safely (zero leakage of sealedReference or sealedMutants)."""
    arenas = load_arenas(workspace=workspace, kg_dir=kg_dir)
    dom_f = str(domain or "").strip().lower()
    prop_f = str(prop_id or "").strip()

    items: list[dict[str, Any]] = []
    for a in arenas:
        if dom_f and str(a.get("domain") or "").lower() != dom_f:
            continue
        if prop_f and str(a.get("propId") or "") != prop_f:
            continue
        items.append(_public_arena_summary(a))

    return {
        "ok": True,
        "count": len(items),
        "arenas": items,
    }


def test_arena_input(
    arena_id: str,
    raw_input: str,
    *,
    workspace: Path | str | None = None,
    kg_dir: Path | str | None = None,
    timeout_sec: float = 3.0,
) -> dict[str, Any]:
    """Test a learner's adversarial counterexample against sealedReference and all sealedMutants.

    NEVER leaks surviving mutants' code, formulas, trap tags, IDs, or outputs.
    """
    arenas = load_arenas(workspace=workspace, kg_dir=kg_dir)
    target_idx = next((i for i, a in enumerate(arenas) if str(a.get("id") or "") == str(arena_id).strip()), None)
    if target_idx is None:
        raise KeyError(f"Arena '{arena_id}' not found.")

    arena = arenas[target_idx]
    dom = str(arena.get("domain") or "code").lower()
    ref_obj = dict(arena.get("sealedReference") or {})
    mutants = [m for m in (arena.get("sealedMutants") or []) if isinstance(m, dict)]
    assumptions = list(arena.get("assumptionPredicates") or [])
    tol = float(ref_obj.get("tolerance") or 1e-6)

    killed_now: list[str] = []
    killed_tags: dict[str, str] = {}
    valid_assumption = True
    failed_assumptions: list[str] = []
    input_size = _structural_size(raw_input)
    reference_ok = True
    reference_error: str | None = None

    if dom == "code":
        try:
            parsed_in = json.loads(raw_input)
        except json.JSONDecodeError:
            parsed_in = ast.literal_eval(raw_input)

        if isinstance(parsed_in, dict) and ("args" in parsed_in or "kwargs" in parsed_in):
            call_args = list(parsed_in.get("args") or [])
            call_kwargs = dict(parsed_in.get("kwargs") or {})
            input_size = _structural_size(call_args) + _structural_size(call_kwargs)
        else:
            call_args = [parsed_in]
            call_kwargs = {}
            input_size = _structural_size(parsed_in)

        valid_assumption, failed_assumptions = _check_code_assumptions(
            assumptions, parsed_in, call_args, call_kwargs, timeout_sec=min(2.0, timeout_sec)
        )
        if valid_assumption:
            entry = str(arena.get("entry") or ref_obj.get("entry") or "solve")
            ref_code = str(ref_obj.get("code") or "")
            ref_res = _run_python_entry_isolated(ref_code, entry, call_args, call_kwargs, timeout_sec=timeout_sec)
            if not ref_res.get("ok"):
                reference_ok = False
                reference_error = str(ref_res.get("error") or "Reference oracle rejected input.")
            else:
                ref_val = ref_res.get("value")
                # Optional cross-check path via code_verify.py if available (already isolated above)
                for m in mutants:
                    mid = str(m.get("id") or "")
                    m_code = str(m.get("code") or "")
                    m_res = _run_python_entry_isolated(m_code, entry, call_args, call_kwargs, timeout_sec=timeout_sec)
                    if (not m_res.get("ok")) or (not _values_equal(m_res.get("value"), ref_val, tol=tol)):
                        killed_now.append(mid)
                        if m.get("trapTag"):
                            killed_tags[mid] = str(m["trapTag"])
    else:
        # Math or Physics domain
        expected_vars = [str(v) for v in (ref_obj.get("vars") or []) if str(v).strip()]
        env, input_size = _parse_math_input(raw_input, expected_vars)
        valid_assumption, failed_assumptions = _check_math_assumptions(assumptions, env)
        if valid_assumption:
            ref_expr = str(ref_obj.get("expr") or "")
            use_sympy = any(isinstance(v, str) for v in env.values())
            ref_val: Any = None

            if use_sympy:
                ok_sp, sp_val = _try_sympy_eval(ref_expr, env)
                if ok_sp:
                    ref_val = sp_val
                else:
                    reference_ok = False
                    reference_error = "Symbolic evaluation unavailable for this expression."
            else:
                try:
                    ref_val = _safe_eval_expr(ref_expr, env)
                    if isinstance(ref_val, float) and not math.isfinite(ref_val):
                        raise ValueError("Non-finite reference result")
                except Exception as exc:
                    ok_sp, sp_val = _try_sympy_eval(ref_expr, env)
                    if ok_sp:
                        ref_val = sp_val
                    else:
                        reference_ok = False
                        reference_error = f"Reference evaluation failed on input: {exc}"

            if reference_ok:
                for m in mutants:
                    mid = str(m.get("id") or "")
                    m_expr = str(m.get("expr") or "")
                    m_ok = True
                    m_val: Any = None
                    if use_sympy:
                        m_ok, m_val = _try_sympy_eval(m_expr, env)
                    else:
                        try:
                            m_val = _safe_eval_expr(m_expr, env)
                            if isinstance(m_val, float) and not math.isfinite(m_val):
                                m_ok = False
                        except Exception:
                            m_ok, m_val = _try_sympy_eval(m_expr, env)

                    if (not m_ok) or (not _values_equal(m_val, ref_val, tol=tol)):
                        killed_now.append(mid)
                        if m.get("trapTag"):
                            killed_tags[mid] = str(m["trapTag"])

    min_score = _minimality_score(input_size) if (valid_assumption and killed_now) else 0.0

    prev_killed = set(str(k) for k in (arena.get("killedMutants") or []))
    newly_killed = [mid for mid in killed_now if mid not in prev_killed]
    updated_killed = sorted(prev_killed | set(killed_now))

    arena["attempts"] = int(arena.get("attempts") or 0) + 1
    arena["killedMutants"] = updated_killed
    if valid_assumption and killed_now:
        prev_best = arena.get("bestMinimalSize")
        if prev_best is None or input_size < int(prev_best):
            arena["bestMinimalSize"] = int(input_size)
    arena["updatedAt"] = _now_iso()
    arenas[target_idx] = arena
    save_arenas(arenas, workspace=workspace, kg_dir=kg_dir)

    total_mutants = len(mutants)
    survived_count = max(0, total_mutants - len(updated_killed))

    # Strict zero-spoiler payload: NEVER emit surviving mutant IDs, code, expr, or trapTags!
    out: dict[str, Any] = {
        "ok": True,
        "arenaId": arena["id"],
        "domain": dom,
        "validAssumption": bool(valid_assumption),
        "failedAssumptions": failed_assumptions,
        "referenceOk": bool(reference_ok),
        "killed": killed_now,
        "newlyKilled": newly_killed,
        "killedTrapTags": killed_tags,
        "totalMutants": total_mutants,
        "killedTotalCount": len(updated_killed),
        "survivedCount": survived_count,
        "allKilled": survived_count == 0 and total_mutants > 0,
        "inputSize": int(input_size),
        "minimalityScore": min_score,
        "bestMinimalSize": arena.get("bestMinimalSize"),
    }
    if reference_error:
        out["referenceError"] = reference_error
    return out


def repair_arena_lemma(
    arena_id: str,
    lemma: str,
    *,
    workspace: Path | str | None = None,
    kg_dir: Path | str | None = None,
) -> dict[str, Any]:
    """Persist a 1-line Lakatos Lemma Repair into the arena and linked proposition's boundaryConditions[]."""
    lemma_clean = str(lemma or "").strip()
    if not lemma_clean:
        raise ValueError("Lemma repair text cannot be empty.")

    arenas = load_arenas(workspace=workspace, kg_dir=kg_dir)
    target_idx = next((i for i, a in enumerate(arenas) if str(a.get("id") or "") == str(arena_id).strip()), None)
    if target_idx is None:
        raise KeyError(f"Arena '{arena_id}' not found.")

    arena = arenas[target_idx]
    repairs = list(arena.get("lemmaRepairs") or [])
    rep_id = f"rep-{len(repairs) + 1}"
    ts = _now_iso()

    # Persist into linked proposition's boundaryConditions[] if present
    persisted_to_prop = False
    prop_id = str(arena.get("propId") or "").strip()
    if prop_id:
        props_path = _resolve_kg_dir(workspace=workspace, explicit=kg_dir) / "propositions.jsonl"
        props = _load_jsonl(props_path)
        for p in props:
            if str(p.get("id") or "") == prop_id:
                bcs = list(p.get("boundaryConditions") or [])
                if lemma_clean not in bcs:
                    bcs.append(lemma_clean)
                    p["boundaryConditions"] = bcs
                    _save_jsonl_atomic(props_path, props)
                persisted_to_prop = True
                break

    repair_entry = {
        "id": rep_id,
        "lemma": lemma_clean,
        "ts": ts,
        "persistedToProp": persisted_to_prop,
    }
    repairs.append(repair_entry)
    arena["lemmaRepairs"] = repairs
    arena["updatedAt"] = ts
    arenas[target_idx] = arena
    save_arenas(arenas, workspace=workspace, kg_dir=kg_dir)

    return {
        "ok": True,
        "arenaId": arena["id"],
        "propId": prop_id or None,
        "repair": repair_entry,
        "persistedToProp": persisted_to_prop,
        "totalRepairs": len(repairs),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_json_or_file(raw: str | None, label: str) -> Any:
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    p = Path(s).expanduser()
    if p.is_file():
        s = p.read_text(encoding="utf-8")
    try:
        return json.loads(s)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON for {label}: {exc}") from exc


def _build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--workspace", type=Path, default=argparse.SUPPRESS, help="Workspace root directory.")
    common.add_argument("--kg-dir", type=Path, default=argparse.SUPPRESS, help="Override knowledge-graph/ directory.")
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="Emit JSON output.")

    parser = argparse.ArgumentParser(
        prog="falsify_arena.py",
        description="Adversarial Counterexample Arena (Examplar + Lakatos Monster-Hunting) CLI.",
        parents=[common],
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_create = sub.add_parser("create", parents=[common], help="Create a server-sealed counterexample arena.")
    p_create.add_argument("--id", dest="arena_id", default=None, help="Optional explicit arena ID.")
    p_create.add_argument("--prop-id", default="", help="Linked proposition ID.")
    p_create.add_argument("--domain", choices=("code", "math", "physics"), default="code", help="Arena domain.")
    p_create.add_argument("--title", default=None, help="Short arena title.")
    p_create.add_argument("--claim", required=True, help="Plausible claim/spec to stress-test.")
    p_create.add_argument("--entry", default="solve", help="Entry function name (for code domain).")
    p_create.add_argument("--lang", default="python", help="Language (for code domain, default: python).")
    p_create.add_argument("--reference", default=None, help="Reference code or math expression string.")
    p_create.add_argument("--reference-json", default=None, help="Reference JSON object or file.")
    p_create.add_argument("--mutants-json", required=True, help="JSON array (or file) of sealed mutants.")
    p_create.add_argument("--assumptions-json", default=None, help="JSON array (or file) of assumptionPredicates.")

    p_list = sub.add_parser("list", parents=[common], help="List arenas without revealing sealed mutants.")
    p_list.add_argument("--domain", choices=("code", "math", "physics"), default=None, help="Filter by domain.")
    p_list.add_argument("--prop-id", default=None, help="Filter by proposition ID.")

    p_test = sub.add_parser("test", parents=[common], help="Submit an adversarial counterexample input.")
    p_test.add_argument("arena_id", help="Arena ID to attack.")
    p_test.add_argument("--input", dest="raw_input", required=True, help="Counterexample JSON or math expression.")
    p_test.add_argument("--timeout", type=float, default=3.0, help="Per-mutant execution timeout in seconds.")

    p_rep = sub.add_parser("repair", parents=[common], help="Persist a 1-line Lakatos Lemma Repair.")
    p_rep.add_argument("arena_id", help="Arena ID to repair.")
    p_rep.add_argument("--lemma", required=True, help="1-line hidden assumption barring the counterexample.")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    ws = getattr(args, "workspace", None)
    kg_dir = getattr(args, "kg_dir", None)
    as_json = bool(getattr(args, "json", False))

    try:
        if args.command == "create":
            ref_val = _parse_json_or_file(args.reference_json, "--reference-json") if args.reference_json else args.reference
            if ref_val is None:
                raise ValueError("Provide --reference or --reference-json.")
            muts = _parse_json_or_file(args.mutants_json, "--mutants-json")
            assumps = _parse_json_or_file(args.assumptions_json, "--assumptions-json") or []
            res = create_arena(
                prop_id=args.prop_id,
                domain=args.domain,
                claim=args.claim,
                sealed_reference=ref_val,
                sealed_mutants=muts,
                assumption_predicates=assumps,
                title=args.title,
                entry=args.entry,
                lang=args.lang,
                arena_id=args.arena_id,
                workspace=ws,
                kg_dir=kg_dir,
            )
        elif args.command == "list":
            res = list_arenas(workspace=ws, kg_dir=kg_dir, domain=args.domain, prop_id=args.prop_id)
        elif args.command == "test":
            res = test_arena_input(
                args.arena_id,
                args.raw_input,
                workspace=ws,
                kg_dir=kg_dir,
                timeout_sec=args.timeout,
            )
        elif args.command == "repair":
            res = repair_arena_lemma(
                args.arena_id,
                args.lemma,
                workspace=ws,
                kg_dir=kg_dir,
            )
        else:
            parser.error(f"Unknown command: {args.command}")
            return 2
    except Exception as exc:  # noqa: BLE001
        res = {"ok": False, "command": args.command, "error": str(exc)}

    if as_json:
        print(json.dumps(res, indent=2, ensure_ascii=False))
    else:
        if not res.get("ok"):
            print(f"ERROR: {res.get('error', 'Failed')}", file=sys.stderr)
        elif args.command == "create":
            a = res["arena"]
            print(f"Arena {res['action']}: {res['id']} ({a['domain']}, {a['totalMutants']} sealed mutants)")
        elif args.command == "list":
            print(f"Arenas ({res['count']}):")
            for a in res["arenas"]:
                print(
                    f"  - {a['id']} [{a['domain']}] killed {a['killedCount']}/{a['totalMutants']} · {a['title']}"
                )
        elif args.command == "test":
            if not res["validAssumption"]:
                print(f"INVALID ASSUMPTION on {res['arenaId']}: {', '.join(res['failedAssumptions'])}")
            else:
                print(
                    f"Arena {res['arenaId']}: killed={res['killed']} · survivedCount={res['survivedCount']} · "
                    f"minimalityScore={res['minimalityScore']}"
                )
        elif args.command == "repair":
            print(
                f"Lakatos lemma persisted on {res['arenaId']} "
                f"(linkedProp={res['propId']}, persistedToProp={res['persistedToProp']})"
            )
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
