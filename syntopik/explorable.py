"""Bret Victor Tangle + Mahajan Limiting-Case Explorable Lab Compiler.

Parses `:::explore ... :::` fenced blocks from chapter Markdown into validated
JSON specs and compiles math expressions via a strict Python `ast` whitelist
into RPN opcode arrays so both Python and browser widgets evaluate live sliders,
exact-vs-approximation regime-break curves, and limiting-case predictions with
ZERO dynamic code evaluation.

Pure Python 3.12+ standard library only (zero external dependencies).
"""

from __future__ import annotations

import ast
import json
import math
import re
from typing import Any

from syntopik.schemas import slugify

__all__ = [
    "MAX_EXPONENT",
    "ALLOWED_FUNCTIONS",
    "ALLOWED_CONSTANTS",
    "compile_expr_to_rpn",
    "evaluate_rpn",
    "compile_explorable_spec",
    "parse_explorable_blocks",
    "sample_explorable_curve",
    "evaluate_limit_check",
]

MAX_EXPONENT = 12.0
_MAX_EXPR_LEN = 512
_MAX_AST_NODES = 128
_MAX_AST_DEPTH = 24
_MAX_ABS_VALUE = 1e100
_IDENT_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")

ALLOWED_CONSTANTS: dict[str, float] = {
    "pi": math.pi,
    "e": math.e,
    "tau": math.tau,
}

# Whitelisted math functions: name -> (callable, allowed_arities)
ALLOWED_FUNCTIONS: dict[str, tuple[Any, tuple[int, ...]]] = {
    "sin": (math.sin, (1,)),
    "cos": (math.cos, (1,)),
    "tan": (math.tan, (1,)),
    "asin": (math.asin, (1,)),
    "acos": (math.acos, (1,)),
    "atan": (math.atan, (1,)),
    "sinh": (math.sinh, (1,)),
    "cosh": (math.cosh, (1,)),
    "tanh": (math.tanh, (1,)),
    "sqrt": (math.sqrt, (1,)),
    "abs": (abs, (1,)),
    "exp": (math.exp, (1,)),
    "log": (math.log, (1, 2)),
    "ln": (math.log, (1,)),
    "floor": (math.floor, (1,)),
    "ceil": (math.ceil, (1,)),
    "min": (min, (2,)),
    "max": (max, (2,)),
}

_ALLOWED_AST_TYPES = (
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
    ast.Pow,
    ast.UAdd,
    ast.USub,
)

_BINOP_SYMBOLS: dict[type[ast.operator], str] = {
    ast.Add: "+",
    ast.Sub: "-",
    ast.Mult: "*",
    ast.Div: "/",
    ast.Pow: "**",
}

_EXPLORE_OPEN_RE = re.compile(r"^:::explore(?:\s+(.*))?$")
_EXPLORE_CLOSE_RE = re.compile(r"^:::\s*$")
_CODE_FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})")


# ---------------------------------------------------------------------------
# Strict AST validation & RPN compiler
# ---------------------------------------------------------------------------


_RESERVED_JS_IDENTS: frozenset[str] = frozenset({"constructor", "prototype", "__proto__"})


def _is_clean_ident(name: str) -> bool:
    return bool(
        isinstance(name, str)
        and _IDENT_RE.match(name)
        and "__" not in name
        and not name.startswith("_")
        and name not in _RESERVED_JS_IDENTS
    )


def _preprocess_expr(expr: str) -> str:
    raw = str(expr or "").strip()
    if raw.startswith("$") and raw.endswith("$") and len(raw) >= 2:
        raw = raw.strip("$").strip()
    raw = raw.replace("−", "-").replace("^", "**")
    return raw


def _check_ast_depth(node: ast.AST, depth: int = 0) -> None:
    if depth > _MAX_AST_DEPTH:
        raise ValueError(f"Expression nesting depth exceeds safe limit ({_MAX_AST_DEPTH}).")
    for child in ast.iter_child_nodes(node):
        _check_ast_depth(child, depth + 1)


def _contains_pow(node: ast.AST) -> bool:
    return any(isinstance(n, ast.BinOp) and isinstance(n.op, ast.Pow) for n in ast.walk(node))


def _safe_pow(base: float, exp: float) -> float:
    if not math.isfinite(base) or not math.isfinite(exp):
        raise ValueError("Non-finite operand in exponentiation.")
    if abs(exp) > MAX_EXPONENT:
        raise ValueError(f"Exponent exceeds safe limit (|exp| <= {int(MAX_EXPONENT)}).")
    if base < 0.0 and not float(exp).is_integer():
        raise ValueError("Fractional power of negative base is not real.")
    if base == 0.0 and exp < 0.0:
        raise ValueError("Zero cannot be raised to a negative power.")
    if abs(base) > 1.0 and exp > 0.0:
        if exp * math.log10(abs(base)) > 100.0:
            raise ValueError("Exponentiation magnitude exceeds safe limit.")
    res = float(base ** exp)
    if not math.isfinite(res) or abs(res) > _MAX_ABS_VALUE:
        raise ValueError("Exponentiation result exceeds finite bounds.")
    return res


def _try_const_fold(node: ast.AST) -> float | None:
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            try:
                val = float(node.value)
            except (OverflowError, ValueError) as exc:
                raise ValueError("Numeric literal out of safe finite range.") from exc
            if not math.isfinite(val) or abs(val) > _MAX_ABS_VALUE:
                raise ValueError("Numeric literal out of safe finite range.")
            return val
        return None
    if isinstance(node, ast.Name) and node.id in ALLOWED_CONSTANTS:
        return ALLOWED_CONSTANTS[node.id]
    if isinstance(node, ast.UnaryOp):
        v = _try_const_fold(node.operand)
        if v is None:
            return None
        if isinstance(node.op, ast.UAdd):
            return +v
        if isinstance(node.op, ast.USub):
            return -v
        return None
    if isinstance(node, ast.BinOp):
        lv = _try_const_fold(node.left)
        rv = _try_const_fold(node.right)
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
            if rv == 0.0:
                return None
            res = lv / rv
        else:
            return None
        if not math.isfinite(res) or abs(res) > _MAX_ABS_VALUE:
            raise ValueError("Constant expression overflows finite safe limit.")
        return float(res)
    return None


def _validate_ast(parsed: ast.Expression, allowed_vars: set[str] | None = None) -> None:
    _check_ast_depth(parsed)
    node_count = 0
    for node in ast.walk(parsed):
        node_count += 1
        if node_count > _MAX_AST_NODES:
            raise ValueError(f"Expression exceeds safe AST node limit ({_MAX_AST_NODES}).")
        if not isinstance(node, _ALLOWED_AST_TYPES):
            raise ValueError(f"Disallowed AST node in explorable expression: {type(node).__name__}")
        if isinstance(node, ast.Constant):
            if not isinstance(node.value, (int, float)) or isinstance(node.value, bool):
                raise ValueError(f"Disallowed literal type: {type(node.value).__name__}")
            try:
                val = float(node.value)
            except (OverflowError, ValueError) as exc:
                raise ValueError("Numeric literal out of safe finite range.") from exc
            if not math.isfinite(val) or abs(val) > _MAX_ABS_VALUE:
                raise ValueError("Numeric literal out of safe finite range.")
        elif isinstance(node, ast.Name):
            if not _is_clean_ident(node.id):
                raise ValueError(f"Disallowed identifier: {node.id!r}")
        elif isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name):
                raise ValueError("Disallowed callable target (only bare whitelisted math functions allowed).")
            fname = node.func.id
            if not _is_clean_ident(fname) or fname not in ALLOWED_FUNCTIONS:
                raise ValueError(f"Disallowed function call: {fname!r}")
            if node.keywords:
                raise ValueError("Keyword arguments are disallowed in explorable expressions.")
            if any(isinstance(arg, ast.Starred) for arg in node.args):
                raise ValueError("Starred arguments are disallowed in explorable expressions.")
            _, valid_arities = ALLOWED_FUNCTIONS[fname]
            if len(node.args) not in valid_arities:
                raise ValueError(
                    f"Function {fname!r} expects {valid_arities} argument(s), got {len(node.args)}."
                )
        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Pow):
            if _contains_pow(node.right):
                raise ValueError("Nested exponentiation in exponent is disallowed.")
            exp_const = _try_const_fold(node.right)
            if exp_const is not None:
                if abs(exp_const) > MAX_EXPONENT:
                    raise ValueError(f"Exponent exceeds safe limit (|exp| <= {int(MAX_EXPONENT)}).")
                base_const = _try_const_fold(node.left)
                if base_const is not None:
                    _safe_pow(base_const, exp_const)

    # Check variable references (skipping Call.func Name nodes)
    if allowed_vars is not None:
        func_name_nodes = {
            id(n.func)
            for n in ast.walk(parsed)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        }
        for node in ast.walk(parsed):
            if isinstance(node, ast.Name) and id(node) not in func_name_nodes:
                if node.id not in ALLOWED_CONSTANTS and node.id not in allowed_vars:
                    raise ValueError(f"Unknown variable {node.id!r}; allowed variables: {sorted(allowed_vars)}")


def _emit_rpn(node: ast.AST, out: list[dict[str, Any]]) -> None:
    if isinstance(node, ast.Expression):
        _emit_rpn(node.body, out)
        return
    if isinstance(node, ast.Constant):
        try:
            fval = float(node.value)
        except (OverflowError, ValueError) as exc:
            raise ValueError("Numeric literal out of safe finite range.") from exc
        if not math.isfinite(fval) or abs(fval) > _MAX_ABS_VALUE:
            raise ValueError("Numeric literal out of safe finite range.")
        out.append({"op": "num", "val": fval})
        return
    if isinstance(node, ast.Name):
        if node.id in ALLOWED_CONSTANTS:
            out.append({"op": "const", "name": node.id, "val": ALLOWED_CONSTANTS[node.id]})
        else:
            out.append({"op": "var", "name": node.id})
        return
    if isinstance(node, ast.UnaryOp):
        _emit_rpn(node.operand, out)
        if isinstance(node.op, ast.USub):
            out.append({"op": "unary", "fn": "neg"})
        elif isinstance(node.op, ast.UAdd):
            out.append({"op": "unary", "fn": "pos"})
        else:
            raise ValueError(f"Disallowed unary op: {type(node.op).__name__}")
        return
    if isinstance(node, ast.BinOp):
        _emit_rpn(node.left, out)
        _emit_rpn(node.right, out)
        sym = _BINOP_SYMBOLS.get(type(node.op))
        if sym is None:
            raise ValueError(f"Disallowed binary op: {type(node.op).__name__}")
        out.append({"op": "bin", "fn": sym})
        return
    if isinstance(node, ast.Call):
        assert isinstance(node.func, ast.Name)
        for arg in node.args:
            _emit_rpn(arg, out)
        out.append({"op": "call", "fn": node.func.id, "arity": len(node.args)})
        return
    raise ValueError(f"Disallowed AST node: {type(node).__name__}")


def compile_expr_to_rpn(
    expr: str,
    *,
    allowed_vars: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Compile a safe mathematical expression into a JSON-serializable RPN opcode list."""
    code = _preprocess_expr(expr)
    if not code:
        raise ValueError("Math expression cannot be empty.")
    if len(code) > _MAX_EXPR_LEN:
        raise ValueError(f"Math expression exceeds {_MAX_EXPR_LEN} character limit.")
    try:
        parsed = ast.parse(code, mode="eval")
    except (SyntaxError, ValueError, OverflowError) as exc:
        msg = exc.msg if isinstance(exc, SyntaxError) else str(exc)
        raise ValueError(f"Invalid math expression syntax: {msg}") from exc

    try:
        _validate_ast(parsed, allowed_vars=allowed_vars)
        ops: list[dict[str, Any]] = []
        _emit_rpn(parsed, ops)
    except OverflowError as exc:
        raise ValueError("Numeric literal out of safe finite range.") from exc
    return ops


def evaluate_rpn(
    rpn_ops: list[dict[str, Any]],
    env: dict[str, float] | None = None,
) -> float:
    """Evaluate a compiled RPN opcode array safely with ZERO `eval()`."""
    if not isinstance(rpn_ops, list) or not rpn_ops:
        raise ValueError("RPN opcode array cannot be empty.")
    vars_env = {str(k): float(v) for k, v in (env or {}).items()}
    stack: list[float] = []

    for tok in rpn_ops:
        if not isinstance(tok, dict):
            raise ValueError("Each RPN opcode must be a dict.")
        op = tok.get("op")
        if op == "num":
            val = float(tok["val"])
            if not math.isfinite(val):
                raise ValueError("Non-finite numeric opcode.")
            stack.append(val)
        elif op == "const":
            cname = str(tok.get("name") or "")
            if cname not in ALLOWED_CONSTANTS:
                raise ValueError(f"Disallowed constant opcode: {cname!r}")
            stack.append(ALLOWED_CONSTANTS[cname])
        elif op == "var":
            vname = str(tok.get("name") or "")
            if vname in vars_env:
                val = vars_env[vname]
            elif vname in ALLOWED_CONSTANTS:
                val = ALLOWED_CONSTANTS[vname]
            else:
                raise ValueError(f"Missing variable in evaluation environment: {vname!r}")
            if not math.isfinite(val):
                raise ValueError(f"Non-finite variable value for {vname!r}.")
            stack.append(val)
        elif op == "unary":
            if not stack:
                raise ValueError("RPN stack underflow on unary op.")
            a = stack.pop()
            fn_name = str(tok.get("fn") or "")
            if fn_name == "neg":
                stack.append(-a)
            elif fn_name == "pos":
                stack.append(+a)
            else:
                raise ValueError(f"Unknown unary opcode: {fn_name!r}")
        elif op == "bin":
            if len(stack) < 2:
                raise ValueError("RPN stack underflow on binary op.")
            b = stack.pop()
            a = stack.pop()
            fn_name = str(tok.get("fn") or "")
            if fn_name == "+":
                res = a + b
            elif fn_name == "-":
                res = a - b
            elif fn_name == "*":
                res = a * b
            elif fn_name == "/":
                if b == 0.0:
                    raise ZeroDivisionError("Division by zero in RPN evaluation.")
                res = a / b
            elif fn_name == "**":
                res = _safe_pow(a, b)
            else:
                raise ValueError(f"Unknown binary opcode: {fn_name!r}")
            if not math.isfinite(res) or abs(res) > _MAX_ABS_VALUE:
                raise ValueError("RPN binary operation overflowed finite bounds.")
            stack.append(float(res))
        elif op == "call":
            fn_name = str(tok.get("fn") or "")
            if fn_name not in ALLOWED_FUNCTIONS:
                raise ValueError(f"Disallowed function opcode: {fn_name!r}")
            fn_impl, valid_arities = ALLOWED_FUNCTIONS[fn_name]
            arity = int(tok.get("arity", 1))
            if arity not in valid_arities:
                raise ValueError(f"Invalid arity {arity} for function {fn_name!r}.")
            if len(stack) < arity:
                raise ValueError(f"RPN stack underflow calling {fn_name!r}.")
            args = stack[-arity:]
            del stack[-arity:]
            if fn_name == "exp" and args[0] > 230.0:
                raise ValueError("exp() argument exceeds safe finite bound.")
            res = float(fn_impl(*args))
            if not math.isfinite(res) or abs(res) > _MAX_ABS_VALUE:
                raise ValueError(f"Function {fn_name!r} returned non-finite result.")
            stack.append(res)
        else:
            raise ValueError(f"Unknown RPN opcode type: {op!r}")

    if len(stack) != 1:
        raise ValueError(f"Malformed RPN stack at end of evaluation (depth={len(stack)}).")
    return float(stack[0])


# ---------------------------------------------------------------------------
# Spec compilation & curve/limit evaluation
# ---------------------------------------------------------------------------


def _as_finite_float(value: Any, field_name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"Field '{field_name}' must be numeric, not bool.")
    try:
        val = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"Field '{field_name}' must be numeric, got {value!r}.") from exc
    if not math.isfinite(val):
        raise ValueError(f"Field '{field_name}' must be finite.")
    return val


def compile_explorable_spec(spec: dict[str, Any]) -> dict[str, Any]:
    """Validate an explorable lab spec dict and compile all math expressions to RPN."""
    if not isinstance(spec, dict):
        raise ValueError("Explorable spec must be a JSON object.")

    title = str(spec.get("title") or "").strip()
    raw_id = str(spec.get("id") or "").strip()
    lab_id = slugify(raw_id or title or "explorable-lab", default="explorable-lab")
    if not title:
        title = lab_id.replace("-", " ").title()
    prompt = str(spec.get("prompt") or "").strip()

    raw_params = spec.get("params") or []
    if not isinstance(raw_params, list):
        raise ValueError("'params' must be a list.")

    params: list[dict[str, Any]] = []
    param_names: set[str] = set()
    default_env: dict[str, float] = {}
    for idx, p in enumerate(raw_params):
        if not isinstance(p, dict):
            raise ValueError(f"Param #{idx + 1} must be an object.")
        name = str(p.get("name") or "").strip()
        if not _is_clean_ident(name) or name in ALLOWED_CONSTANTS or name in ALLOWED_FUNCTIONS:
            raise ValueError(f"Invalid parameter name: {name!r}")
        if name in param_names:
            raise ValueError(f"Duplicate parameter name: {name!r}")
        pmin = _as_finite_float(p.get("min", 0.0), f"params[{name}].min")
        pmax = _as_finite_float(p.get("max", 1.0), f"params[{name}].max")
        if pmax <= pmin:
            raise ValueError(f"Parameter {name!r} requires max > min (got {pmin}..{pmax}).")
        step = _as_finite_float(p.get("step", (pmax - pmin) / 50.0), f"params[{name}].step")
        if step <= 0.0:
            raise ValueError(f"Parameter {name!r} requires step > 0.")
        default_val = _as_finite_float(
            p.get("default", (pmin + pmax) / 2.0),
            f"params[{name}].default",
        )
        default_val = max(pmin, min(pmax, default_val))
        param_names.add(name)
        default_env[name] = default_val
        params.append(
            {
                "name": name,
                "label": str(p.get("label") or name).strip(),
                "min": pmin,
                "max": pmax,
                "step": step,
                "default": default_val,
                "unit": str(p.get("unit") or "").strip(),
            }
        )

    raw_outputs = spec.get("outputs") or []
    if not isinstance(raw_outputs, list):
        raise ValueError("'outputs' must be a list.")

    outputs: list[dict[str, Any]] = []
    out_allowed_vars = set(param_names)
    for idx, o in enumerate(raw_outputs):
        if not isinstance(o, dict):
            raise ValueError(f"Output #{idx + 1} must be an object.")
        expr = str(o.get("expr") or "").strip()
        rpn = compile_expr_to_rpn(expr, allowed_vars=out_allowed_vars)
        out_entry: dict[str, Any] = {
            "label": str(o.get("label") or f"Output {idx + 1}").strip(),
            "expr": expr,
            "rpn": rpn,
            "unit": str(o.get("unit") or "").strip(),
        }
        try:
            out_entry["defaultValue"] = round(evaluate_rpn(rpn, default_env), 6)
        except Exception:
            pass
        outputs.append(out_entry)

    curve_out: dict[str, Any] | None = None
    raw_curve = spec.get("curve")
    if isinstance(raw_curve, dict) and raw_curve:
        x_var = str(raw_curve.get("xVar") or "x").strip()
        if not _is_clean_ident(x_var) or x_var in ALLOWED_CONSTANTS or x_var in ALLOWED_FUNCTIONS:
            raise ValueError(f"Invalid curve.xVar: {x_var!r}")
        x_min = _as_finite_float(raw_curve.get("xMin", 0.0), "curve.xMin")
        x_max = _as_finite_float(raw_curve.get("xMax", 1.0), "curve.xMax")
        if x_max <= x_min:
            raise ValueError(f"curve requires xMax > xMin (got {x_min}..{x_max}).")
        samples = max(2, min(200, int(_as_finite_float(raw_curve.get("samples", 64), "curve.samples"))))
        curve_vars = param_names | {x_var}

        exact_expr = str(raw_curve.get("exactExpr") or "").strip()
        if not exact_expr:
            raise ValueError("curve.exactExpr is required when curve is specified.")
        exact_rpn = compile_expr_to_rpn(exact_expr, allowed_vars=curve_vars)

        approx_expr = str(raw_curve.get("approxExpr") or "").strip()
        approx_rpn = compile_expr_to_rpn(approx_expr, allowed_vars=curve_vars) if approx_expr else None

        raw_thresh = raw_curve.get("regimeThreshold")
        regime_threshold = (
            _as_finite_float(raw_thresh, "curve.regimeThreshold")
            if raw_thresh is not None and str(raw_thresh).strip() != ""
            else None
        )
        curve_out = {
            "xVar": x_var,
            "xMin": x_min,
            "xMax": x_max,
            "samples": samples,
            "exactExpr": exact_expr,
            "exactRpn": exact_rpn,
            "approxExpr": approx_expr or None,
            "approxRpn": approx_rpn,
            "regimeThreshold": regime_threshold,
            "yLabel": str(raw_curve.get("yLabel") or "y").strip(),
        }

    limit_out: dict[str, Any] | None = None
    raw_limit = spec.get("limitCheck")
    if isinstance(raw_limit, dict) and raw_limit:
        l_var = str(raw_limit.get("var") or (params[0]["name"] if params else "x")).strip()
        if not _is_clean_ident(l_var):
            raise ValueError(f"Invalid limitCheck.var: {l_var!r}")
        raw_target = raw_limit.get("target")
        target_val: float | str
        if isinstance(raw_target, (int, float)) and not isinstance(raw_target, bool):
            target_val = float(raw_target)
        else:
            t_str = str(raw_target if raw_target is not None else "0").strip()
            try:
                f_t = float(t_str)
                target_val = f_t if math.isfinite(f_t) else t_str.lower()
            except ValueError:
                target_val = t_str

        expected_expr = str(raw_limit.get("expectedExpr") or "").strip()
        expected_rpn: list[dict[str, Any]] | None = None
        if expected_expr:
            lim_vars = param_names | {l_var}
            expected_rpn = compile_expr_to_rpn(expected_expr, allowed_vars=lim_vars)

        expected_val: float | None = None
        if raw_limit.get("expectedVal") is not None and str(raw_limit.get("expectedVal")).strip() != "":
            expected_val = _as_finite_float(raw_limit["expectedVal"], "limitCheck.expectedVal")
        elif expected_rpn is not None:
            eval_env = dict(default_env)
            if isinstance(target_val, (int, float)) and math.isfinite(float(target_val)):
                eval_env[l_var] = float(target_val)
            elif str(target_val).lower() in ("inf", "+inf", "infinity", "oo"):
                eval_env[l_var] = 1e6
            elif str(target_val).lower() in ("-inf", "-infinity", "-oo"):
                eval_env[l_var] = -1e6
            try:
                expected_val = evaluate_rpn(expected_rpn, eval_env)
            except Exception:
                expected_val = None

        limit_out = {
            "question": str(raw_limit.get("question") or f"Predict the limiting value as {l_var} -> {target_val}.").strip(),
            "var": l_var,
            "target": target_val,
            "expectedExpr": expected_expr or None,
            "expectedRpn": expected_rpn,
            "expectedVal": expected_val,
            "hint": str(raw_limit.get("hint") or "Check the dominant term in the limiting regime.").strip(),
        }

    return {
        "id": lab_id,
        "title": title,
        "prompt": prompt,
        "params": params,
        "outputs": outputs,
        "curve": curve_out,
        "limitCheck": limit_out,
    }


def _parse_kv_pairs(text: str) -> dict[str, Any]:
    """Parse lightweight `k=v, k2="val 2"` attribute lists inside DSL lines."""
    out: dict[str, Any] = {}
    for m in re.finditer(r'([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?:"([^"]*)"|\'([^\']*)\'|([^,\s]+))', text):
        key = m.group(1)
        raw_val = m.group(2) if m.group(2) is not None else (m.group(3) if m.group(3) is not None else m.group(4))
        if m.group(2) is not None or m.group(3) is not None:
            out[key] = raw_val
        else:
            try:
                num = float(raw_val)
                out[key] = int(num) if num.is_integer() and "." not in raw_val else num
            except ValueError:
                out[key] = raw_val
    return out


def _parse_block_payload(body: str, fence_arg: str = "") -> dict[str, Any]:
    """Parse JSON or lightweight key-value DSL inside a `:::explore` block."""
    cleaned = body.strip()
    # Unwrap optional fenced ```json ... ``` inside :::explore
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if len(lines) >= 2 and lines[-1].strip().startswith("```"):
            cleaned = "\n".join(lines[1:-1]).strip()

    if cleaned.startswith("{"):
        parsed = json.loads(cleaned)
        if not isinstance(parsed, dict):
            raise ValueError(":::explore JSON block must be an object.")
        if fence_arg and not parsed.get("id"):
            parsed["id"] = fence_arg.strip()
        return parsed

    # Lightweight line DSL fallback
    spec: dict[str, Any] = {"params": [], "outputs": []}
    if fence_arg.strip():
        spec["id"] = fence_arg.strip()
    curve: dict[str, Any] = {}
    limit_check: dict[str, Any] = {}

    for raw_line in cleaned.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, val = line.split(":", 1)
        k_low = key.strip().lower()
        val_s = val.strip()

        if k_low in ("id", "title", "prompt"):
            spec[k_low] = val_s.strip('"').strip("'")
        elif k_low.startswith("param "):
            pname = key.strip().split(None, 1)[1].strip()
            pobj = _parse_kv_pairs(val_s)
            pobj["name"] = pname
            spec["params"].append(pobj)
        elif k_low.startswith("output"):
            oobj = _parse_kv_pairs(val_s)
            if "label" not in oobj and " " in key.strip():
                oobj["label"] = key.strip().split(None, 1)[1].strip()
            spec["outputs"].append(oobj)
        elif k_low == "curve":
            curve.update(_parse_kv_pairs(val_s))
        elif k_low in ("limitcheck", "limit_check", "limit"):
            limit_check.update(_parse_kv_pairs(val_s))

    if curve:
        spec["curve"] = curve
    if limit_check:
        spec["limitCheck"] = limit_check
    if not spec.get("title") and not spec.get("params") and not spec.get("outputs") and not curve:
        raise ValueError("Could not parse :::explore block body as JSON or DSL.")
    return spec


def parse_explorable_blocks(
    markdown_text: str,
    *,
    strict: bool = True,
) -> list[dict[str, Any]]:
    """Extract and compile all `:::explore ... :::` blocks from chapter Markdown."""
    if not markdown_text:
        return []
    lines = str(markdown_text).splitlines()
    specs: list[dict[str, Any]] = []

    in_code_fence = False
    code_fence_char = ""
    in_explore = False
    fence_arg = ""
    buf: list[str] = []

    for line in lines:
        stripped = line.strip()
        if not in_explore:
            cf = _CODE_FENCE_RE.match(line)
            if cf:
                marker = cf.group(1)[0]
                if not in_code_fence:
                    in_code_fence = True
                    code_fence_char = marker
                elif marker == code_fence_char:
                    in_code_fence = False
                    code_fence_char = ""
                continue
            if in_code_fence:
                continue
            om = _EXPLORE_OPEN_RE.match(stripped)
            if om:
                in_explore = True
                fence_arg = (om.group(1) or "").strip()
                buf = []
                continue
        else:
            if _EXPLORE_CLOSE_RE.match(stripped):
                raw_body = "\n".join(buf)
                in_explore = False
                buf = []
                try:
                    raw_spec = _parse_block_payload(raw_body, fence_arg=fence_arg)
                    compiled = compile_explorable_spec(raw_spec)
                    specs.append(compiled)
                except Exception:
                    if strict:
                        raise
            else:
                buf.append(line)

    if in_explore and strict:
        raise ValueError("Unclosed :::explore fenced block in Markdown.")
    return specs


def sample_explorable_curve(
    compiled_spec: dict[str, Any],
    param_values: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Sample exact and approximation curves using the compiled RPN arrays."""
    curve = compiled_spec.get("curve")
    if not isinstance(curve, dict):
        return {"ok": False, "error": "Explorable spec has no curve definition."}

    env = {p["name"]: float(p["default"]) for p in (compiled_spec.get("params") or [])}
    if param_values:
        for k, v in param_values.items():
            env[str(k)] = float(v)

    x_var = curve["xVar"]
    x_min = _as_finite_float(curve["xMin"], "curve.xMin")
    x_max = _as_finite_float(curve["xMax"], "curve.xMax")
    raw_samples = curve.get("samples", 64)
    samples = max(2, min(200, int(_as_finite_float(raw_samples, "curve.samples"))))
    exact_rpn = curve["exactRpn"]
    approx_rpn = curve.get("approxRpn")

    points: list[dict[str, float | None]] = []
    max_abs_err = 0.0
    max_rel_err = 0.0
    for i in range(samples):
        frac = i / max(1, samples - 1)
        xv = x_min + (x_max - x_min) * frac
        env[x_var] = xv
        try:
            ey: float | None = evaluate_rpn(exact_rpn, env)
        except Exception:
            ey = None
        ay: float | None = None
        if approx_rpn is not None:
            try:
                ay = evaluate_rpn(approx_rpn, env)
            except Exception:
                ay = None
        if ey is not None and ay is not None:
            err = abs(ey - ay)
            max_abs_err = max(max_abs_err, err)
            rel = err / max(1e-9, abs(ey))
            max_rel_err = max(max_rel_err, rel)
        points.append({"x": round(xv, 6), "exact": round(ey, 6) if ey is not None else None, "approx": round(ay, 6) if ay is not None else None})

    return {
        "ok": True,
        "xVar": x_var,
        "yLabel": curve.get("yLabel", "y"),
        "regimeThreshold": curve.get("regimeThreshold"),
        "maxAbsError": round(max_abs_err, 6),
        "maxRelError": round(max_rel_err, 6),
        "points": points,
    }


def evaluate_limit_check(
    spec: dict[str, Any],
    user_val: float | int | str,
    *,
    param_values: dict[str, float] | None = None,
    tol: float = 0.05,
) -> dict[str, Any]:
    """Grade a learner's Mahajan limiting-case prediction against `spec['limitCheck']`."""
    lc = spec.get("limitCheck")
    if not isinstance(lc, dict):
        raise ValueError("Explorable spec does not define a limitCheck.")

    env = {p["name"]: float(p["default"]) for p in (spec.get("params") or [])}
    if param_values:
        for k, v in param_values.items():
            env[str(k)] = float(v)

    l_var = str(lc.get("var") or "x")
    target = lc.get("target", 0.0)
    if isinstance(target, (int, float)) and math.isfinite(float(target)):
        env[l_var] = float(target)
    elif str(target).lower() in ("inf", "+inf", "infinity", "oo"):
        env[l_var] = 1e6
    elif str(target).lower() in ("-inf", "-infinity", "-oo"):
        env[l_var] = -1e6

    # Parse learner's value safely (numeric literal or safe RPN math expression — NEVER eval!)
    if isinstance(user_val, (int, float)) and not isinstance(user_val, bool):
        u_num = float(user_val)
    else:
        raw_str = str(user_val or "").strip()
        if not raw_str:
            raise ValueError("Learner limit prediction cannot be empty.")
        try:
            u_num = float(raw_str)
        except ValueError:
            u_rpn = compile_expr_to_rpn(raw_str, allowed_vars=set(env.keys()))
            u_num = evaluate_rpn(u_rpn, env)

    if not math.isfinite(u_num):
        raise ValueError("Learner prediction must evaluate to a finite real number.")

    expected_val = lc.get("expectedVal")
    if expected_val is None or param_values is not None:
        exp_rpn = lc.get("expectedRpn")
        if exp_rpn is None and lc.get("expectedExpr"):
            exp_rpn = compile_expr_to_rpn(str(lc["expectedExpr"]), allowed_vars=set(env.keys()))
        if exp_rpn is not None:
            expected_val = evaluate_rpn(exp_rpn, env)
    if expected_val is None:
        raise ValueError("limitCheck has neither expectedVal nor evaluable expectedExpr.")

    exp_num = float(expected_val)
    abs_err = abs(u_num - exp_num)
    denom = max(1.0, abs(exp_num))
    rel_err = abs_err / denom
    eff_tol = max(1e-9, float(tol))
    passed = abs_err <= eff_tol * denom

    return {
        "ok": True,
        "passed": bool(passed),
        "var": l_var,
        "target": target,
        "userVal": round(u_num, 6),
        "expectedVal": round(exp_num, 6),
        "absoluteError": round(abs_err, 6),
        "relativeError": round(rel_err, 6),
        "tolerance": eff_tol,
        "hint": None if passed else lc.get("hint"),
        "feedback": (
            f"Limit check passed ({u_num:g} matches {exp_num:g} within {eff_tol * 100:.1f}%)."
            if passed
            else f"Limit check missed: got {u_num:g}, expected {exp_num:g} as {l_var} -> {target}."
        ),
    }
