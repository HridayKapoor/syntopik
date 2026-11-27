#!/usr/bin/env python3
"""Coordinate Geometry, 3D Vectors & Calculus Interactive Plotter (P1.4).

Generates publication-grade static vector/raster plots (SVG/PNG via Matplotlib)
and 100% offline, zero-CDN interactive HTML5 Canvas labs with SymPy-verified
exact geometry, calculus, complex-plane loci, and 3D skew-line analysis.

Subcommands:
  conic     - Parabola, ellipse, hyperbola (foci, directrix, tangent, normal,
              latus rectum, asymptotes, director circle with JEE a>b guard)
  argand    - Complex plane loci (circle, perp-bisector, ray, ellipse-foci,
              apollonius, line, segment, hyperbola-foci) & exact intersections
  integral  - Definite signed integral vs geometric area with root detection
              and teal/rose sign shading (SymPy exact + SciPy quad fallback)
  vector3d  - 3D lines & planes: skew/parallel/intersecting/coincident, exact
              shortest distance, feet of perpendicular, dihedral angles,
              and 100% offline drag-to-rotate 3D Canvas lab
  func      - Multi-function calculus plotter with exact SymPy roots,
              extrema (min/max/saddle), verified inflection points & asymptotes
"""

from __future__ import annotations

import argparse
import ast
import html as html_lib
import json
import math
import re
import sys
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")

import matplotlib.gridspec as gridspec  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import sympy as sp  # noqa: E402
from matplotlib.patches import Arc, Circle  # noqa: E402
from mpl_toolkits.mplot3d.art3d import Poly3DCollection  # noqa: E402
from scipy import integrate as sp_integrate  # noqa: E402
from scipy import optimize as sp_optimize  # noqa: E402
from sympy.parsing.sympy_parser import (  # noqa: E402
    convert_xor,
    implicit_multiplication_application,
    parse_expr,
    standard_transformations,
    stringify_expr,
)

# ---------------------------------------------------------------------------
# Safe SymPy Parsing (Defense-in-Depth: Pre-filter + AST Whitelist + Sandbox)
# ---------------------------------------------------------------------------

TRANSFORMS = standard_transformations + (
    implicit_multiplication_application,
    convert_xor,
)

_FORBIDDEN_SUBSTRINGS = (
    "__",
    ";",
    "`",
    '"',
    "'",
    "{",
    "}",
    "[",
    "]",
    "\\",
    ":",
    "=",
    "@",
    "$",
    "~",
    "!",
    "&",
    "|",
)

_FORBIDDEN_WORDS = frozenset(
    {
        "import",
        "from",
        "lambda",
        "eval",
        "exec",
        "open",
        "compile",
        "getattr",
        "setattr",
        "delattr",
        "globals",
        "locals",
        "vars",
        "dir",
        "type",
        "class",
        "os",
        "sys",
        "subprocess",
        "builtins",
        "breakpoint",
        "input",
        "print",
        "def",
        "return",
        "yield",
        "raise",
        "try",
        "except",
        "for",
        "while",
        "if",
        "else",
        "with",
        "as",
        "del",
        "pass",
        "assert",
    }
)

_SAFE_MATH_FUNCS: dict[str, Any] = {
    "pi": sp.pi,
    "e": sp.E,
    "E": sp.E,
    "sin": sp.sin,
    "cos": sp.cos,
    "tan": sp.tan,
    "cot": sp.cot,
    "sec": sp.sec,
    "csc": sp.csc,
    "asin": sp.asin,
    "acos": sp.acos,
    "atan": sp.atan,
    "atan2": sp.atan2,
    "sinh": sp.sinh,
    "cosh": sp.cosh,
    "tanh": sp.tanh,
    "asinh": sp.asinh,
    "acosh": sp.acosh,
    "atanh": sp.atanh,
    "exp": sp.exp,
    "log": sp.log,
    "ln": sp.log,
    "sqrt": sp.sqrt,
    "cbrt": sp.cbrt,
    "Abs": sp.Abs,
    "abs": sp.Abs,
    "sign": sp.sign,
    "floor": sp.floor,
    "ceiling": sp.ceiling,
    "ceil": sp.ceiling,
    "Mod": sp.Mod,
    "Max": sp.Max,
    "Min": sp.Min,
    "Heaviside": sp.Heaviside,
    "erf": sp.erf,
}

_SAFE_GLOBALS: dict[str, Any] = {
    "__builtins__": {},
    "Symbol": sp.Symbol,
    "Integer": sp.Integer,
    "Float": sp.Float,
    "Rational": sp.Rational,
    "I": sp.I,
}

_ALLOWED_AST_CALLS = frozenset(_SAFE_MATH_FUNCS.keys()) | frozenset(
    {"Symbol", "Integer", "Float", "Rational"}
)

_ALLOWED_BINOPS = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow)
_ALLOWED_UNOPS = (ast.UAdd, ast.USub)
_IDENT_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
_FLOAT_STR_RE = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$")


def _validate_ast_tree(
    node: ast.AST, allowed_names: set[str], allowed_calls: set[str]
) -> None:
    """Walk transformed Python AST and reject any non-mathematical construct."""
    if isinstance(node, ast.Expression):
        _validate_ast_tree(node.body, allowed_names, allowed_calls)
        return
    if isinstance(node, ast.BinOp) and isinstance(node.op, _ALLOWED_BINOPS):
        _validate_ast_tree(node.left, allowed_names, allowed_calls)
        _validate_ast_tree(node.right, allowed_names, allowed_calls)
        return
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, _ALLOWED_UNOPS):
        _validate_ast_tree(node.operand, allowed_names, allowed_calls)
        return
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            return
        raise ValueError(f"Disallowed literal in expression: {node.value!r}")
    if isinstance(node, ast.Name):
        if node.id in allowed_names:
            return
        raise ValueError(f"Unknown or disallowed symbol: {node.id!r}")
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.keywords:
            raise ValueError("Disallowed function call syntax in expression")
        fname = node.func.id
        if fname not in allowed_calls:
            raise ValueError(f"Disallowed function call: {fname!r}")
        if fname == "Symbol":
            if (
                len(node.args) == 1
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
                and _IDENT_RE.match(node.args[0].value)
                and node.args[0].value in allowed_names
            ):
                return
            raise ValueError("Disallowed Symbol() argument")
        if fname == "Float":
            if (
                len(node.args) == 1
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
                and _FLOAT_STR_RE.match(node.args[0].value)
            ):
                return
            raise ValueError("Disallowed Float() argument")
        for arg in node.args:
            _validate_ast_tree(arg, allowed_names, allowed_calls)
        return
    raise ValueError(f"Disallowed syntax construct: {type(node).__name__}")


def safe_parse_expr(
    raw: str,
    allowed_symbols: dict[str, sp.Symbol] | None = None,
    allow_complex: bool = False,
) -> sp.Expr:
    """Safely parse a mathematical expression via pre-filter + AST whitelist + SymPy."""
    if not isinstance(raw, str):
        raise ValueError("Expression must be a string")
    text = raw.strip()
    if not text:
        raise ValueError("Expression cannot be empty")
    if len(text) > 512:
        raise ValueError("Expression exceeds maximum allowed length (512 chars)")

    for bad in _FORBIDDEN_SUBSTRINGS:
        if bad in text:
            raise ValueError(f"Disallowed token {bad!r} in expression: {raw!r}")

    # Block attribute access like `x.real` or `(1).bit_length`
    if re.search(r"\.[A-Za-z_]", text):
        raise ValueError(f"Attribute access is not permitted in expression: {raw!r}")

    # Block dunder / leading-underscore identifiers and forbidden keywords
    for ident in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", text):
        if ident.startswith("_") or ident in _FORBIDDEN_WORDS:
            raise ValueError(f"Disallowed identifier {ident!r} in expression: {raw!r}")

    local_dict: dict[str, Any] = dict(_SAFE_MATH_FUNCS)
    if allow_complex:
        local_dict["I"] = sp.I
        local_dict["i"] = sp.I
        local_dict["j"] = sp.I
    if allowed_symbols:
        local_dict.update(allowed_symbols)

    allowed_names = set(local_dict.keys()) | set(_SAFE_GLOBALS.keys())
    allowed_calls = set(_ALLOWED_AST_CALLS)

    try:
        code_str = stringify_expr(text, local_dict, _SAFE_GLOBALS, TRANSFORMS)
        tree = ast.parse(code_str, mode="eval")
    except Exception as exc:
        raise ValueError(f"Could not parse expression {raw!r}: {exc}") from exc

    _validate_ast_tree(tree, allowed_names, allowed_calls)

    try:
        expr = parse_expr(
            text,
            local_dict=local_dict,
            global_dict=dict(_SAFE_GLOBALS),
            transformations=TRANSFORMS,
            evaluate=True,
        )
    except Exception as exc:
        raise ValueError(f"SymPy evaluation failed for {raw!r}: {exc}") from exc

    if not isinstance(expr, sp.Basic):
        raise ValueError(f"Parsed result is not a mathematical expression: {raw!r}")

    expected_syms = set(allowed_symbols.values()) if allowed_symbols else set()
    unknown = expr.free_symbols - expected_syms
    if unknown:
        names = ", ".join(sorted(str(s) for s in unknown))
        raise ValueError(f"Unexpected free symbol(s) {names} in expression: {raw!r}")

    return sp.sympify(expr)


def parse_scalar_expr(raw: str | float | int) -> tuple[sp.Expr, float]:
    """Parse a real scalar expression (supports pi, sqrt(2), 1/3, etc.)."""
    if isinstance(raw, (int, float)):
        val_f = float(raw)
        if not math.isfinite(val_f):
            raise ValueError(f"Non-finite scalar value: {raw!r}")
        if isinstance(raw, int):
            return sp.Integer(raw), val_f
        return sp.nsimplify(val_f, tolerance=1e-10, rational=True), val_f
    expr = safe_parse_expr(str(raw), allowed_symbols=None, allow_complex=False)
    expr_s = sp.simplify(expr)
    num = complex(sp.N(expr_s, 15))
    if abs(num.imag) > 1e-11:
        raise ValueError(f"Expected real scalar, got complex value for {raw!r}: {expr_s}")
    val_f = float(num.real)
    if not math.isfinite(val_f):
        raise ValueError(f"Non-finite scalar expression: {raw!r}")
    return expr_s, val_f


def parse_complex_expr(raw: str) -> tuple[sp.Expr, complex]:
    """Parse a complex number expression (accepts i, j, I, sqrt, pi, exp)."""
    expr = safe_parse_expr(str(raw), allowed_symbols=None, allow_complex=True)
    expr_s = sp.simplify(sp.expand_complex(expr))
    cval = complex(sp.N(expr_s, 15))
    if not (math.isfinite(cval.real) and math.isfinite(cval.imag)):
        raise ValueError(f"Non-finite complex expression: {raw!r}")
    return expr_s, cval


def _clean_float(val: float, tol: float = 1e-12) -> float:
    if not math.isfinite(val):
        return val
    return 0.0 if abs(val) < tol else float(val)


def _sym_to_float(expr: sp.Expr) -> float:
    c = complex(sp.N(expr, 15))
    if abs(c.imag) > 1e-9:
        raise ValueError(f"Expression is not real: {expr}")
    return _clean_float(float(c.real))


def _fmt_num(val: float, digits: int = 4) -> str:
    v = _clean_float(val)
    if abs(v - round(v)) < 1e-9:
        return str(int(round(v)))
    return f"{v:.{digits}g}"


def _fmt_deg(val: float) -> str:
    v = _clean_float(val)
    if abs(v - round(v)) < 1e-6:
        return str(int(round(v)))
    return f"{v:.2f}".rstrip("0").rstrip(".")


def _fmt_complex(re_v: float, im_v: float, digits: int = 3) -> str:
    r = _clean_float(re_v)
    im = _clean_float(im_v)
    if abs(im) < 1e-9:
        return _fmt_num(r, digits)
    if abs(r) < 1e-9:
        if abs(abs(im) - 1.0) < 1e-9:
            return "i" if im > 0 else "-i"
        return f"{_fmt_num(im, digits)}i"
    sign = "+" if im >= 0 else "-"
    aim = abs(im)
    im_s = "i" if abs(aim - 1.0) < 1e-9 else f"{_fmt_num(aim, digits)}i"
    return f"{_fmt_num(r, digits)} {sign} {im_s}"


def _format_line_eq(A: sp.Expr, B: sp.Expr, C: sp.Expr) -> str:
    """Format exact line A*x + B*y + C = 0 cleanly using SymPy."""
    x_s, y_s = sp.symbols("x y", real=True)
    expr = sp.simplify(A * x_s + B * y_s + C)
    # Clear simple numeric denominators when possible
    try:
        num, den = sp.fraction(sp.together(expr))
        if den.is_number and den != 0:
            expr = sp.expand(num)
    except Exception:
        pass
    # Prefer y = m*x + c if B != 0 and simple, or standard Ax + By = D
    if sp.simplify(B) == 0 and sp.simplify(A) != 0:
        x_val = sp.simplify(-C / A)
        return f"x = {x_val}"
    if sp.simplify(A) == 0 and sp.simplify(B) != 0:
        y_val = sp.simplify(-C / B)
        return f"y = {y_val}"
    # Try clean y = ... form
    y_rhs = sp.simplify((-A * x_s - C) / B)
    return f"y = {y_rhs}"


# ---------------------------------------------------------------------------
# SymPy -> Safe JavaScript Emitter (for Zero-CDN Interactive Calculus HTML)
# ---------------------------------------------------------------------------

_JS_UNARY_FUNCS: dict[type, str] = {
    sp.sin: "Math.sin",
    sp.cos: "Math.cos",
    sp.tan: "Math.tan",
    sp.asin: "Math.asin",
    sp.acos: "Math.acos",
    sp.atan: "Math.atan",
    sp.sinh: "Math.sinh",
    sp.cosh: "Math.cosh",
    sp.tanh: "Math.tanh",
    sp.asinh: "Math.asinh",
    sp.acosh: "Math.acosh",
    sp.atanh: "Math.atanh",
    sp.exp: "Math.exp",
    sp.log: "Math.log",
    sp.Abs: "Math.abs",
    sp.sign: "Math.sign",
    sp.floor: "Math.floor",
    sp.ceiling: "Math.ceil",
}


def sympy_to_js(expr: sp.Expr, var_name: str = "x") -> str | None:
    """Convert a validated SymPy expression in one real variable into safe JS code.

    Returns None if an unsupported special function is encountered (caller then
    falls back to dense pre-sampled cubic/linear interpolation in JSON).
    """
    if isinstance(expr, sp.Symbol):
        return var_name if expr.name == var_name else None
    if expr is sp.pi:
        return "Math.PI"
    if expr is sp.E:
        return "Math.E"
    if isinstance(expr, sp.Integer):
        return f"({int(expr)})"
    if isinstance(expr, sp.Rational):
        return f"({int(expr.p)}/{int(expr.q)})"
    if isinstance(expr, sp.Float):
        fv = float(expr)
        if not math.isfinite(fv):
            return None
        return f"({fv!r})"
    if expr.is_Number:
        try:
            fv = float(sp.N(expr, 15))
            if math.isfinite(fv):
                return f"({fv!r})"
        except Exception:
            return None
        return None
    if isinstance(expr, sp.Add):
        parts = [sympy_to_js(a, var_name) for a in expr.args]
        if any(p is None for p in parts):
            return None
        return "(" + " + ".join(p for p in parts if p is not None) + ")"
    if isinstance(expr, sp.Mul):
        parts = [sympy_to_js(a, var_name) for a in expr.args]
        if any(p is None for p in parts):
            return None
        return "(" + " * ".join(p for p in parts if p is not None) + ")"
    if isinstance(expr, sp.Pow):
        base, expo = expr.args
        b_js = sympy_to_js(base, var_name)
        if b_js is None:
            return None
        if expo == sp.Rational(1, 2):
            return f"Math.sqrt({b_js})"
        if expo == sp.Rational(1, 3):
            return f"Math.cbrt({b_js})"
        if expo == -1:
            return f"(1 / ({b_js}))"
        if isinstance(expo, sp.Rational) and expo.q % 2 == 1 and expo.q > 1:
            return f"(Math.sign({b_js}) * Math.pow(Math.abs({b_js}), {int(expo.p)}/{int(expo.q)}))"
        e_js = sympy_to_js(expo, var_name)
        if e_js is None:
            return None
        return f"Math.pow({b_js}, {e_js})"
    if type(expr) in _JS_UNARY_FUNCS and len(expr.args) == 1:
        arg_js = sympy_to_js(expr.args[0], var_name)
        if arg_js is None:
            return None
        return f"{_JS_UNARY_FUNCS[type(expr)]}({arg_js})"
    if isinstance(expr, sp.cot) and len(expr.args) == 1:
        u = sympy_to_js(expr.args[0], var_name)
        return f"(1 / Math.tan({u}))" if u else None
    if isinstance(expr, sp.sec) and len(expr.args) == 1:
        u = sympy_to_js(expr.args[0], var_name)
        return f"(1 / Math.cos({u}))" if u else None
    if isinstance(expr, sp.csc) and len(expr.args) == 1:
        u = sympy_to_js(expr.args[0], var_name)
        return f"(1 / Math.sin({u}))" if u else None
    if isinstance(expr, sp.Heaviside) and len(expr.args) >= 1:
        u = sympy_to_js(expr.args[0], var_name)
        return f"(({u}) < 0 ? 0 : (({u}) > 0 ? 1 : 0.5))" if u else None
    if isinstance(expr, sp.erf) and len(expr.args) == 1:
        u = sympy_to_js(expr.args[0], var_name)
        return f"__erf({u})" if u else None
    if isinstance(expr, sp.Max):
        parts = [sympy_to_js(a, var_name) for a in expr.args]
        if any(p is None for p in parts):
            return None
        return "Math.max(" + ", ".join(p for p in parts if p is not None) + ")"
    if isinstance(expr, sp.Min):
        parts = [sympy_to_js(a, var_name) for a in expr.args]
        if any(p is None for p in parts):
            return None
        return "Math.min(" + ", ".join(p for p in parts if p is not None) + ")"
    if isinstance(expr, sp.Mod) and len(expr.args) == 2:
        a_js = sympy_to_js(expr.args[0], var_name)
        b_js = sympy_to_js(expr.args[1], var_name)
        if a_js and b_js:
            return f"((({a_js}) % ({b_js})) + ({b_js})) % ({b_js})"
    return None


def safe_json_for_script(obj: Any) -> str:
    """Serialize JSON safely for inline <script> tags (escapes </script, U+2028, U+2029)."""
    raw = json.dumps(obj, ensure_ascii=True)
    return (
        raw.replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


# ---------------------------------------------------------------------------
# Theme & Static Matplotlib Helpers (Zero-Overlap Annotations + Info Panel)
# ---------------------------------------------------------------------------

THEMES: dict[str, dict[str, str]] = {
    "light": {
        "fig_bg": "#ffffff",
        "ax_bg": "#f8fafc",
        "panel_bg": "#f1f5f9",
        "panel_border": "#cbd5e1",
        "text": "#0f172a",
        "muted": "#475569",
        "grid": "#e2e8f0",
        "axis": "#334155",
        "primary": "#2563eb",
        "secondary": "#7c3aed",
        "teal": "#0d9488",
        "amber": "#d97706",
        "crimson": "#e11d48",
        "emerald": "#059669",
        "pill_bg": "#ffffff",
        "pill_edge": "#94a3b8",
        "pane": "#f1f5f9",
    },
    "dark": {
        "fig_bg": "#0b1120",
        "ax_bg": "#0f172a",
        "panel_bg": "#111c33",
        "panel_border": "#1e293b",
        "text": "#f1f5f9",
        "muted": "#94a3b8",
        "grid": "#1e293b",
        "axis": "#94a3b8",
        "primary": "#38bdf8",
        "secondary": "#a78bfa",
        "teal": "#2dd4bf",
        "amber": "#fbbf24",
        "crimson": "#fb7185",
        "emerald": "#34d399",
        "pill_bg": "#1e293b",
        "pill_edge": "#475569",
        "pane": "#131c31",
    },
}

SERIES_PALETTE = {
    "light": ["#2563eb", "#7c3aed", "#0d9488", "#d97706", "#e11d48", "#059669"],
    "dark": ["#38bdf8", "#a78bfa", "#2dd4bf", "#fbbf24", "#fb7185", "#34d399"],
}


def _apply_mpl_rc(theme_name: str) -> dict[str, str]:
    t = THEMES[theme_name]
    plt.rcParams.update(
        {
            "font.family": "sans-serif",
            "font.sans-serif": ["DejaVu Sans", "Helvetica", "Arial"],
            "mathtext.fontset": "stixsans",
            "axes.edgecolor": t["panel_border"],
            "axes.labelcolor": t["text"],
            "xtick.color": t["muted"],
            "ytick.color": t["muted"],
            "text.color": t["text"],
            "figure.facecolor": t["fig_bg"],
            "axes.facecolor": t["ax_bg"],
            "savefig.facecolor": t["fig_bg"],
        }
    )
    return t


def _style_2d_axes(ax: Any, t: dict[str, str], xlabel: str = "x", ylabel: str = "y") -> None:
    ax.set_facecolor(t["ax_bg"])
    ax.grid(True, which="major", color=t["grid"], linewidth=0.8, alpha=0.9, zorder=0)
    ax.axhline(0, color=t["axis"], linewidth=1.1, alpha=0.85, zorder=1)
    ax.axvline(0, color=t["axis"], linewidth=1.1, alpha=0.85, zorder=1)
    for spine in ("top", "right"):
        ax.spines[spine].set_color(t["panel_border"])
        ax.spines[spine].set_linewidth(0.8)
    for spine in ("bottom", "left"):
        ax.spines[spine].set_color(t["panel_border"])
        ax.spines[spine].set_linewidth(1.0)
    ax.set_xlabel(xlabel, fontsize=10, fontweight="bold", color=t["text"], labelpad=6)
    ax.set_ylabel(ylabel, fontsize=10, fontweight="bold", color=t["text"], labelpad=6)
    ax.tick_params(labelsize=8.5, colors=t["muted"])


def _render_info_panel(
    ax_info: Any,
    t: dict[str, str],
    header: str,
    subtitle: str,
    rows: list[tuple[str, str, str | None]],
) -> None:
    """Render a crisp, collision-free SymPy Exact Readout card in a dedicated side axis."""
    ax_info.set_facecolor(t["panel_bg"])
    ax_info.set_xlim(0, 1)
    ax_info.set_ylim(0, 1)
    ax_info.set_xticks([])
    ax_info.set_yticks([])
    for sp_name in ("top", "right", "bottom", "left"):
        ax_info.spines[sp_name].set_color(t["panel_border"])
        ax_info.spines[sp_name].set_linewidth(1.1)

    ax_info.text(
        0.06,
        0.95,
        header,
        fontsize=11.5,
        fontweight="bold",
        color=t["text"],
        va="top",
        ha="left",
        transform=ax_info.transAxes,
    )
    if subtitle:
        ax_info.text(
            0.06,
            0.895,
            subtitle,
            fontsize=8.5,
            color=t["muted"],
            va="top",
            ha="left",
            transform=ax_info.transAxes,
        )
    ax_info.plot(
        [0.05, 0.95], [0.855, 0.855], color=t["panel_border"], linewidth=1.0, transform=ax_info.transAxes
    )

    y_cur = 0.825
    max_rows = min(len(rows), 12)
    step = min(0.068, 0.78 / max(1, max_rows))
    for label, value, color_key in rows[:max_rows]:
        val_color = t.get(color_key, color_key) if color_key else t["text"]
        # Wrap long values cleanly at ~34 chars
        val_clean = str(value)
        if len(val_clean) > 36:
            val_clean = val_clean[:34] + "..."
        ax_info.text(
            0.06,
            y_cur,
            label,
            fontsize=7.6,
            fontweight="bold",
            color=t["muted"],
            va="top",
            ha="left",
            transform=ax_info.transAxes,
        )
        ax_info.text(
            0.06,
            y_cur - 0.025,
            val_clean,
            fontsize=8.8,
            fontfamily="monospace",
            fontweight="bold",
            color=val_color,
            va="top",
            ha="left",
            transform=ax_info.transAxes,
        )
        y_cur -= step


def _place_non_overlapping_annotations(
    ax: Any,
    items: list[dict[str, Any]],
    t: dict[str, str],
) -> None:
    """Greedily place pill annotations around data points with zero box-box overlap."""
    if not items:
        return
    xlim = ax.get_xlim()
    ylim = ax.get_ylim()
    dx_span = max(1e-9, xlim[1] - xlim[0])
    dy_span = max(1e-9, ylim[1] - ylim[0])

    # Reserve small point boxes around markers so labels do not cover dots
    placed: list[tuple[float, float, float, float]] = []
    for it in items:
        u = (it["x"] - xlim[0]) / dx_span
        v = (it["y"] - ylim[0]) / dy_span
        if 0.0 <= u <= 1.0 and 0.0 <= v <= 1.0:
            placed.append((u - 0.018, v - 0.018, u + 0.018, v + 0.018))

    # Candidate offsets in axes-fraction space (dx, dy)
    dirs = [
        (0.055, 0.065),
        (-0.055, 0.065),
        (0.055, -0.075),
        (-0.055, -0.075),
        (0.095, 0.01),
        (-0.095, 0.01),
        (0.0, 0.105),
        (0.0, -0.105),
        (0.115, 0.095),
        (-0.115, 0.095),
        (0.115, -0.105),
        (-0.115, -0.105),
        (0.16, 0.045),
        (-0.16, 0.045),
        (0.06, 0.155),
        (-0.06, -0.155),
        (0.19, 0.13),
        (-0.19, 0.13),
    ]

    def _overlaps(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> bool:
        return not (a[2] < b[0] or a[0] > b[2] or a[3] < b[1] or a[1] > b[3])

    for it in items:
        px, py = float(it["x"]), float(it["y"])
        u0 = (px - xlim[0]) / dx_span
        v0 = (py - ylim[0]) / dy_span
        if not (-0.02 <= u0 <= 1.02 and -0.02 <= v0 <= 1.02):
            continue
        label = str(it["label"])
        color = it.get("color", t["text"])
        lines = label.split("\n")
        max_len = max(len(ln) for ln in lines)
        bw = min(0.32, max(0.08, 0.0125 * max_len + 0.028))
        bh = 0.042 * len(lines) + 0.016

        best_slot: tuple[float, float, tuple[float, float, float, float]] | None = None
        best_score = float("inf")

        for du, dv in dirs:
            cu = u0 + du
            cv = v0 + dv
            # Shift box center slightly along direction so arrow points to edge
            cx = cu + (bw * 0.35 if du > 0.01 else (-bw * 0.35 if du < -0.01 else 0.0))
            cy = cv + (bh * 0.25 if dv > 0.01 else (-bh * 0.25 if dv < -0.01 else 0.0))
            box = (cx - bw / 2, cy - bh / 2, cx + bw / 2, cy + bh / 2)
            # Keep strictly inside axes
            if box[0] < 0.02 or box[2] > 0.98 or box[1] < 0.02 or box[3] > 0.98:
                continue
            ov_count = sum(1 for p in placed if _overlaps(box, p))
            dist = math.hypot(cx - u0, cy - v0)
            score = ov_count * 100.0 + dist
            if score < best_score:
                best_score = score
                best_slot = (cx, cy, box)
                if ov_count == 0:
                    break

        if best_slot is None:
            cx = min(max(u0 + 0.06, 0.02 + bw / 2), 0.98 - bw / 2)
            cy = min(max(v0 + 0.06, 0.02 + bh / 2), 0.98 - bh / 2)
            best_slot = (cx, cy, (cx - bw / 2, cy - bh / 2, cx + bw / 2, cy + bh / 2))

        cx, cy, box = best_slot
        placed.append(box)
        tx = xlim[0] + cx * dx_span
        ty = ylim[0] + cy * dy_span
        ax.annotate(
            label,
            xy=(px, py),
            xytext=(tx, ty),
            fontsize=8.0,
            fontweight="bold",
            color=color,
            ha="center",
            va="center",
            bbox={
                "boxstyle": "round,pad=0.26",
                "facecolor": t["pill_bg"],
                "edgecolor": color,
                "linewidth": 0.9,
                "alpha": 0.94,
            },
            arrowprops={
                "arrowstyle": "-",
                "color": color,
                "lw": 0.9,
                "alpha": 0.8,
                "shrinkA": 4,
                "shrinkB": 3,
            },
            zorder=9,
        )


def _save_figure(fig: Any, out_path: Path, dpi: int) -> int:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    ext = out_path.suffix.lower()
    if ext == ".png":
        fig.savefig(str(out_path), dpi=dpi, bbox_inches="tight", pad_inches=0.18)
    elif ext == ".svg":
        fig.savefig(str(out_path), format="svg", bbox_inches="tight", pad_inches=0.18)
    else:
        raise SystemExit(f"Unsupported static output format: {ext} (expected .svg, .png, or .html)")
    plt.close(fig)
    return out_path.stat().st_size


# ---------------------------------------------------------------------------
# Shared Zero-CDN Interactive HTML Shell
# ---------------------------------------------------------------------------

_HTML_SHELL_CSS = """
:root {
  --bg: #f8fafc;
  --panel: #ffffff;
  --panel-alt: #f1f5f9;
  --border: #e2e8f0;
  --text: #0f172a;
  --muted: #475569;
  --accent: #2563eb;
  --accent-soft: #eff6ff;
  --teal: #0d9488;
  --amber: #d97706;
  --crimson: #e11d48;
  --violet: #7c3aed;
  --emerald: #059669;
  --canvas-bg: #f8fafc;
  --grid: #e2e8f0;
  --axis: #475569;
  --mono: "SF Mono", ui-monospace, Menlo, Consolas, monospace;
  --sans: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
}
html[data-theme="dark"] {
  --bg: #0b1120;
  --panel: #111827;
  --panel-alt: #172033;
  --border: #1e293b;
  --text: #f1f5f9;
  --muted: #94a3b8;
  --accent: #38bdf8;
  --accent-soft: #0c233a;
  --teal: #2dd4bf;
  --amber: #fbbf24;
  --crimson: #fb7185;
  --violet: #a78bfa;
  --emerald: #34d399;
  --canvas-bg: #0f172a;
  --grid: #1e293b;
  --axis: #94a3b8;
}
* { box-sizing: border-box; }
html, body {
  margin: 0; padding: 0; height: 100%; width: 100%;
  background: var(--bg); color: var(--text);
  font-family: var(--sans); overflow: hidden;
}
.app {
  display: grid;
  grid-template-rows: 52px 1fr;
  height: 100vh; width: 100vw;
}
header {
  display: flex; align-items: center; gap: 12px;
  padding: 0 18px; background: var(--panel);
  border-bottom: 1px solid var(--border);
}
header h1 {
  margin: 0; font-size: 15px; font-weight: 700; letter-spacing: -0.01em;
}
.badge {
  font-size: 11px; font-weight: 600; padding: 3px 9px;
  border-radius: 999px; background: var(--accent-soft);
  color: var(--accent); border: 1px solid var(--border);
}
.spacer { flex: 1; }
button.btn {
  background: var(--panel-alt); color: var(--text);
  border: 1px solid var(--border); border-radius: 7px;
  padding: 5px 11px; font-size: 12px; font-weight: 600;
  cursor: pointer; transition: background 0.15s, border-color 0.15s;
}
button.btn:hover { border-color: var(--accent); }
button.btn.active { background: var(--accent); color: #ffffff; border-color: var(--accent); }
main {
  display: grid; grid-template-columns: 1fr 340px;
  min-height: 0; height: 100%;
}
@media (max-width: 860px) {
  main { grid-template-columns: 1fr; grid-template-rows: 1fr 290px; }
}
.stage {
  position: relative; background: var(--canvas-bg);
  min-height: 0; min-width: 0; overflow: hidden;
}
.stage canvas {
  display: block; width: 100%; height: 100%;
  cursor: grab; touch-action: none;
}
.stage canvas:active { cursor: grabbing; }
.hud-pill {
  position: absolute; left: 12px; bottom: 12px;
  padding: 5px 10px; border-radius: 8px;
  background: var(--panel); border: 1px solid var(--border);
  font-size: 11.5px; color: var(--muted);
  font-family: var(--mono); pointer-events: none;
  opacity: 0.92;
}
aside {
  background: var(--panel); border-left: 1px solid var(--border);
  overflow-y: auto; padding: 14px 15px; display: flex;
  flex-direction: column; gap: 12px;
}
.card {
  background: var(--panel-alt); border: 1px solid var(--border);
  border-radius: 10px; padding: 11px 12px;
}
.card-title {
  font-size: 11px; font-weight: 700; text-transform: uppercase;
  letter-spacing: 0.06em; color: var(--muted); margin: 0 0 8px 0;
}
.control-row {
  display: flex; flex-direction: column; gap: 4px; margin-bottom: 9px;
}
.control-row:last-child { margin-bottom: 0; }
.control-head {
  display: flex; justify-content: space-between; align-items: center;
  font-size: 12px; font-weight: 600;
}
.control-val {
  font-family: var(--mono); font-size: 12px; color: var(--accent);
}
input[type="range"] {
  width: 100%; accent-color: var(--accent); cursor: pointer; margin: 0;
}
.toggles {
  display: flex; flex-wrap: wrap; gap: 8px 12px;
}
.toggle-item {
  display: inline-flex; align-items: center; gap: 5px;
  font-size: 12px; color: var(--text); cursor: pointer; user-select: none;
}
.kv-list { display: flex; flex-direction: column; gap: 6px; }
.kv {
  display: flex; flex-direction: column; gap: 1px;
  padding-bottom: 5px; border-bottom: 1px dashed var(--border);
}
.kv:last-child { border-bottom: none; padding-bottom: 0; }
.kv-k { font-size: 10.5px; color: var(--muted); font-weight: 600; }
.kv-v {
  font-family: var(--mono); font-size: 12px; font-weight: 600;
  color: var(--text); word-break: break-word;
}
.kv-v.teal { color: var(--teal); }
.kv-v.crimson { color: var(--crimson); }
.kv-v.amber { color: var(--amber); }
.kv-v.accent { color: var(--accent); }
.kv-v.violet { color: var(--violet); }
.btn-bar { display: flex; flex-wrap: wrap; gap: 6px; }
"""


def _build_offline_html(
    title: str,
    badge: str,
    initial_theme: str,
    sidebar_html: str,
    hud_hint: str,
    js_body: str,
) -> str:
    safe_title = html_lib.escape(title)
    safe_badge = html_lib.escape(badge)
    safe_hint = html_lib.escape(hud_hint)
    init_t = "dark" if initial_theme == "dark" else "light"
    return f"""<!DOCTYPE html>
<html lang="en" data-theme="{init_t}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{safe_title}</title>
<style>{_HTML_SHELL_CSS}</style>
</head>
<body>
<div class="app">
  <header>
    <h1>{safe_title}</h1>
    <span class="badge">{safe_badge}</span>
    <span class="spacer"></span>
    <button class="btn" id="resetViewBtn" type="button">Reset View</button>
    <button class="btn" id="themeBtn" type="button">Theme: {init_t.title()}</button>
  </header>
  <main>
    <div class="stage" id="stage">
      <canvas id="canvas"></canvas>
      <div class="hud-pill" id="hud">{safe_hint}</div>
    </div>
    <aside id="sidebar">{sidebar_html}</aside>
  </main>
</div>
<script>
(function() {{
  "use strict";
  function cssVar(name) {{
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }}
  function fmt(v, d) {{
    if (typeof v !== "number" || !Number.isFinite(v)) return "—";
    if (Math.abs(v) < 1e-10) v = 0;
    const dec = d === undefined ? 3 : d;
    return Number(v.toFixed(dec)).toString();
  }}
  const themeBtn = document.getElementById("themeBtn");
  let onThemeChange = null;
  if (themeBtn) {{
    themeBtn.addEventListener("click", function() {{
      const cur = document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";
      document.documentElement.setAttribute("data-theme", cur);
      themeBtn.textContent = "Theme: " + (cur === "dark" ? "Dark" : "Light");
      if (typeof onThemeChange === "function") onThemeChange();
    }});
  }}
{js_body}
}})();
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Subcommand 1: CONIC (Parabola, Ellipse, Hyperbola)
# ---------------------------------------------------------------------------


def compute_conic_data(args: argparse.Namespace) -> dict[str, Any]:
    ctype = args.type
    h_sym, h_val = parse_scalar_expr(args.h)
    k_sym, k_val = parse_scalar_expr(args.k)

    if ctype == "parabola":
        p_raw = args.p if args.p is not None else (args.a if args.a is not None else "2")
        p_sym, p_val = parse_scalar_expr(p_raw)
        if abs(p_val) < 1e-12:
            raise SystemExit("Parabola focal parameter --p must be non-zero.")
        a_sym, a_val = p_sym, p_val
        b_sym, b_val = sp.Integer(0), 0.0
    else:
        a_raw = args.a if args.a is not None else "4"
        b_raw = args.b if args.b is not None else "2.5"
        a_sym, a_val = parse_scalar_expr(a_raw)
        b_sym, b_val = parse_scalar_expr(b_raw)
        if a_val <= 0 or b_val <= 0:
            raise SystemExit(f"Semi-axes --a ({a_val}) and --b ({b_val}) must be strictly positive.")
        p_sym, p_val = sp.Integer(0), 0.0

    x_s, y_s = sp.symbols("x y", real=True)
    has_tangent = args.tangent_t is not None or bool(args.show_normal)
    if args.tangent_t is not None:
        t_sym, t_val = parse_scalar_expr(args.tangent_t)
    elif args.show_normal:
        default_t = "1" if ctype == "parabola" else ("45" if ctype == "ellipse" else "35")
        t_sym, t_val = parse_scalar_expr(default_t)
    else:
        t_sym, t_val = None, None

    foci: list[dict[str, Any]] = []
    directrices: list[dict[str, Any]] = []
    asymptotes: list[dict[str, Any]] = []
    lr_endpoints: list[dict[str, Any]] = []
    vertices: list[dict[str, Any]] = []

    tangent_point: dict[str, Any] | None = None
    tangent_eq: str | None = None
    normal_eq: str | None = None
    tangent_line_abc: tuple[float, float, float] | None = None
    normal_line_abc: tuple[float, float, float] | None = None

    director_status = "none"
    director_radius: float | None = None
    director_radius_exact: str | None = None
    director_eq: str | None = None
    director_locus: str | None = None

    if ctype == "parabola":
        eq_expr = sp.Eq((y_s - k_sym) ** 2, 4 * p_sym * (x_s - h_sym))
        eq_str = f"(y - {k_sym})^2 = {sp.simplify(4*p_sym)}*(x - {h_sym})" if (h_val or k_val) else f"y^2 = {sp.simplify(4*p_sym)}*x"
        eq_latex = sp.latex(eq_expr)
        ecc_sym = sp.Integer(1)
        lr_sym = sp.simplify(sp.Abs(4 * p_sym))
        fx_sym = sp.simplify(h_sym + p_sym)
        foci.append(
            {
                "label": "F",
                "x": _sym_to_float(fx_sym),
                "y": _clean_float(k_val),
                "xExact": str(fx_sym),
                "yExact": str(k_sym),
            }
        )
        vertices.append({"label": "V", "x": _clean_float(h_val), "y": _clean_float(k_val)})
        dir_x_sym = sp.simplify(h_sym - p_sym)
        dir_x_val = _sym_to_float(dir_x_sym)
        directrices.append(
            {
                "axis": "x",
                "value": dir_x_val,
                "valueExact": str(dir_x_sym),
                "equation": f"x = {dir_x_sym}",
            }
        )
        director_status = "directrix"
        director_locus = f"x = {dir_x_sym}"
        director_eq = f"x = {dir_x_sym} (directrix)"

        for sgn, lbl in ((1, "L1"), (-1, "L2")):
            ly_sym = sp.simplify(k_sym + sgn * 2 * p_sym)
            lr_endpoints.append(
                {
                    "label": lbl,
                    "x": _sym_to_float(fx_sym),
                    "y": _sym_to_float(ly_sym),
                    "xExact": str(fx_sym),
                    "yExact": str(ly_sym),
                }
            )

        if has_tangent and t_sym is not None and t_val is not None:
            px_sym = sp.simplify(h_sym + p_sym * t_sym ** 2)
            py_sym = sp.simplify(k_sym + 2 * p_sym * t_sym)
            tangent_point = {
                "x": _sym_to_float(px_sym),
                "y": _sym_to_float(py_sym),
                "xExact": str(px_sym),
                "yExact": str(py_sym),
            }
            # Tangent: (x - h) - t*(y - k) + p*t^2 = 0
            tA, tB, tC = sp.Integer(1), -t_sym, sp.simplify(-h_sym + t_sym * k_sym + p_sym * t_sym ** 2)
            tangent_eq = _format_line_eq(tA, tB, tC)
            tangent_line_abc = (_sym_to_float(tA), _sym_to_float(tB), _sym_to_float(tC))
            # Normal: t*(x - h) + (y - k) - 2*p*t - p*t^3 = 0
            nA, nB, nC = t_sym, sp.Integer(1), sp.simplify(-t_sym * h_sym - k_sym - 2 * p_sym * t_sym - p_sym * t_sym ** 3)
            normal_eq = _format_line_eq(nA, nB, nC)
            normal_line_abc = (_sym_to_float(nA), _sym_to_float(nB), _sym_to_float(nC))

    elif ctype == "ellipse":
        eq_expr = sp.Eq((x_s - h_sym) ** 2 / a_sym ** 2 + (y_s - k_sym) ** 2 / b_sym ** 2, 1)
        eq_str = (
            f"(x - {h_sym})^2/{sp.simplify(a_sym**2)} + (y - {k_sym})^2/{sp.simplify(b_sym**2)} = 1"
            if (h_val or k_val)
            else f"x^2/{sp.simplify(a_sym**2)} + y^2/{sp.simplify(b_sym**2)} = 1"
        )
        eq_latex = sp.latex(eq_expr)
        major_horizontal = a_val >= b_val - 1e-12
        if major_horizontal:
            c_sym = sp.simplify(sp.sqrt(sp.Max(0, a_sym ** 2 - b_sym ** 2)))
            ecc_sym = sp.simplify(c_sym / a_sym)
            lr_sym = sp.simplify(2 * b_sym ** 2 / a_sym)
            c_val = _sym_to_float(c_sym)
            for sgn, lbl in ((1, "F1"), (-1, "F2")):
                fx_sym = sp.simplify(h_sym + sgn * c_sym)
                foci.append(
                    {
                        "label": lbl,
                        "x": _sym_to_float(fx_sym),
                        "y": _clean_float(k_val),
                        "xExact": str(fx_sym),
                        "yExact": str(k_sym),
                    }
                )
            vertices = [
                {"label": "A", "x": _clean_float(h_val + a_val), "y": _clean_float(k_val)},
                {"label": "A'", "x": _clean_float(h_val - a_val), "y": _clean_float(k_val)},
                {"label": "B", "x": _clean_float(h_val), "y": _clean_float(k_val + b_val)},
                {"label": "B'", "x": _clean_float(h_val), "y": _clean_float(k_val - b_val)},
            ]
            if c_val > 1e-12:
                for sgn in (1, -1):
                    dx_sym = sp.simplify(h_sym + sgn * a_sym ** 2 / c_sym)
                    directrices.append(
                        {
                            "axis": "x",
                            "value": _sym_to_float(dx_sym),
                            "valueExact": str(dx_sym),
                            "equation": f"x = {dx_sym}",
                        }
                    )
            fx1_sym = sp.simplify(h_sym + c_sym)
            for sgn, lbl in ((1, "L1"), (-1, "L2")):
                ly_sym = sp.simplify(k_sym + sgn * b_sym ** 2 / a_sym)
                lr_endpoints.append(
                    {
                        "label": lbl,
                        "x": _sym_to_float(fx1_sym),
                        "y": _sym_to_float(ly_sym),
                        "xExact": str(fx1_sym),
                        "yExact": str(ly_sym),
                    }
                )
        else:
            # Vertical major axis (b > a)
            c_sym = sp.simplify(sp.sqrt(b_sym ** 2 - a_sym ** 2))
            ecc_sym = sp.simplify(c_sym / b_sym)
            lr_sym = sp.simplify(2 * a_sym ** 2 / b_sym)
            for sgn, lbl in ((1, "F1"), (-1, "F2")):
                fy_sym = sp.simplify(k_sym + sgn * c_sym)
                foci.append(
                    {
                        "label": lbl,
                        "x": _clean_float(h_val),
                        "y": _sym_to_float(fy_sym),
                        "xExact": str(h_sym),
                        "yExact": str(fy_sym),
                    }
                )
            vertices = [
                {"label": "B", "x": _clean_float(h_val), "y": _clean_float(k_val + b_val)},
                {"label": "B'", "x": _clean_float(h_val), "y": _clean_float(k_val - b_val)},
                {"label": "A", "x": _clean_float(h_val + a_val), "y": _clean_float(k_val)},
                {"label": "A'", "x": _clean_float(h_val - a_val), "y": _clean_float(k_val)},
            ]
            for sgn in (1, -1):
                dy_sym = sp.simplify(k_sym + sgn * b_sym ** 2 / c_sym)
                directrices.append(
                    {
                        "axis": "y",
                        "value": _sym_to_float(dy_sym),
                        "valueExact": str(dy_sym),
                        "equation": f"y = {dy_sym}",
                    }
                )
            fy1_sym = sp.simplify(k_sym + c_sym)
            for sgn, lbl in ((1, "L1"), (-1, "L2")):
                lx_sym = sp.simplify(h_sym + sgn * a_sym ** 2 / b_sym)
                lr_endpoints.append(
                    {
                        "label": lbl,
                        "x": _sym_to_float(lx_sym),
                        "y": _sym_to_float(fy1_sym),
                        "xExact": str(lx_sym),
                        "yExact": str(fy1_sym),
                    }
                )

        dr_sym = sp.simplify(sp.sqrt(a_sym ** 2 + b_sym ** 2))
        director_status = "real"
        director_radius = _sym_to_float(dr_sym)
        director_radius_exact = str(dr_sym)
        r2_sym = sp.simplify(a_sym ** 2 + b_sym ** 2)
        director_eq = (
            f"(x - {h_sym})^2 + (y - {k_sym})^2 = {r2_sym}"
            if (h_val or k_val)
            else f"x^2 + y^2 = {r2_sym}"
        )
        director_locus = director_eq

        if has_tangent and t_sym is not None and t_val is not None:
            th_rad = sp.simplify(t_sym * sp.pi / 180)
            cth, sth = sp.simplify(sp.cos(th_rad)), sp.simplify(sp.sin(th_rad))
            px_sym = sp.simplify(h_sym + a_sym * cth)
            py_sym = sp.simplify(k_sym + b_sym * sth)
            tangent_point = {
                "x": _sym_to_float(px_sym),
                "y": _sym_to_float(py_sym),
                "xExact": str(px_sym),
                "yExact": str(py_sym),
            }
            # Tangent: b*cos(th)*(x-h) + a*sin(th)*(y-k) - a*b = 0
            tA = sp.simplify(b_sym * cth)
            tB = sp.simplify(a_sym * sth)
            tC = sp.simplify(-tA * h_sym - tB * k_sym - a_sym * b_sym)
            tangent_eq = _format_line_eq(tA, tB, tC)
            tangent_line_abc = (_sym_to_float(tA), _sym_to_float(tB), _sym_to_float(tC))
            # Normal: a*sin(th)*(x - x0) - b*cos(th)*(y - y0) = 0
            nA = sp.simplify(a_sym * sth)
            nB = sp.simplify(-b_sym * cth)
            nC = sp.simplify(-nA * px_sym - nB * py_sym)
            normal_eq = _format_line_eq(nA, nB, nC)
            normal_line_abc = (_sym_to_float(nA), _sym_to_float(nB), _sym_to_float(nC))

    elif ctype == "hyperbola":
        eq_expr = sp.Eq((x_s - h_sym) ** 2 / a_sym ** 2 - (y_s - k_sym) ** 2 / b_sym ** 2, 1)
        eq_str = (
            f"(x - {h_sym})^2/{sp.simplify(a_sym**2)} - (y - {k_sym})^2/{sp.simplify(b_sym**2)} = 1"
            if (h_val or k_val)
            else f"x^2/{sp.simplify(a_sym**2)} - y^2/{sp.simplify(b_sym**2)} = 1"
        )
        eq_latex = sp.latex(eq_expr)
        c_sym = sp.simplify(sp.sqrt(a_sym ** 2 + b_sym ** 2))
        ecc_sym = sp.simplify(c_sym / a_sym)
        lr_sym = sp.simplify(2 * b_sym ** 2 / a_sym)
        for sgn, lbl in ((1, "F1"), (-1, "F2")):
            fx_sym = sp.simplify(h_sym + sgn * c_sym)
            foci.append(
                {
                    "label": lbl,
                    "x": _sym_to_float(fx_sym),
                    "y": _clean_float(k_val),
                    "xExact": str(fx_sym),
                    "yExact": str(k_sym),
                }
            )
        vertices = [
            {"label": "A", "x": _clean_float(h_val + a_val), "y": _clean_float(k_val)},
            {"label": "A'", "x": _clean_float(h_val - a_val), "y": _clean_float(k_val)},
        ]
        for sgn in (1, -1):
            dx_sym = sp.simplify(h_sym + sgn * a_sym ** 2 / c_sym)
            directrices.append(
                {
                    "axis": "x",
                    "value": _sym_to_float(dx_sym),
                    "valueExact": str(dx_sym),
                    "equation": f"x = {dx_sym}",
                }
            )
        fx1_sym = sp.simplify(h_sym + c_sym)
        for sgn, lbl in ((1, "L1"), (-1, "L2")):
            ly_sym = sp.simplify(k_sym + sgn * b_sym ** 2 / a_sym)
            lr_endpoints.append(
                {
                    "label": lbl,
                    "x": _sym_to_float(fx1_sym),
                    "y": _sym_to_float(ly_sym),
                    "xExact": str(fx1_sym),
                    "yExact": str(ly_sym),
                }
            )
        for sgn in (1, -1):
            slope_sym = sp.simplify(sgn * b_sym / a_sym)
            rhs = sp.simplify(k_sym + slope_sym * (x_s - h_sym))
            asymptotes.append(
                {
                    "slope": _sym_to_float(slope_sym),
                    "slopeExact": str(slope_sym),
                    "equation": f"y = {rhs}",
                }
            )

        diff_sq = a_val ** 2 - b_val ** 2
        if diff_sq > 1e-12:
            dr_sym = sp.simplify(sp.sqrt(a_sym ** 2 - b_sym ** 2))
            r2_sym = sp.simplify(a_sym ** 2 - b_sym ** 2)
            director_status = "real"
            director_radius = _sym_to_float(dr_sym)
            director_radius_exact = str(dr_sym)
            director_eq = (
                f"(x - {h_sym})^2 + (y - {k_sym})^2 = {r2_sym}"
                if (h_val or k_val)
                else f"x^2 + y^2 = {r2_sym}"
            )
            director_locus = director_eq
        elif abs(diff_sq) <= 1e-12:
            director_status = "point"
            director_radius = 0.0
            director_radius_exact = "0"
            director_eq = f"Point ({h_sym}, {k_sym}) [a = b rectangular]"
            director_locus = director_eq
        else:
            director_status = "none"
            director_radius = None
            director_radius_exact = None
            director_eq = "None real (a < b, e > sqrt(2))"
            director_locus = None

        if has_tangent and t_sym is not None and t_val is not None:
            th_rad = sp.simplify(t_sym * sp.pi / 180)
            cth_f = math.cos(math.radians(t_val))
            if abs(cth_f) < 1e-7:
                raise SystemExit(
                    f"Hyperbola parametric angle --tangent-t={t_val} deg hits sec(theta) singularity (+/-90 deg)."
                )
            sec_s = sp.simplify(sp.sec(th_rad))
            tan_s = sp.simplify(sp.tan(th_rad))
            sin_s = sp.simplify(sp.sin(th_rad))
            px_sym = sp.simplify(h_sym + a_sym * sec_s)
            py_sym = sp.simplify(k_sym + b_sym * tan_s)
            tangent_point = {
                "x": _sym_to_float(px_sym),
                "y": _sym_to_float(py_sym),
                "xExact": str(px_sym),
                "yExact": str(py_sym),
            }
            # Tangent: b*sec(th)*(x-h) - a*tan(th)*(y-k) - a*b = 0
            tA = sp.simplify(b_sym * sec_s)
            tB = sp.simplify(-a_sym * tan_s)
            tC = sp.simplify(-tA * h_sym - tB * k_sym - a_sym * b_sym)
            tangent_eq = _format_line_eq(tA, tB, tC)
            tangent_line_abc = (_sym_to_float(tA), _sym_to_float(tB), _sym_to_float(tC))
            # Normal: a*sin(th)*(x - x0) + b*(y - y0) = 0 (always finite when cos(th) != 0)
            nA = sp.simplify(a_sym * sin_s)
            nB = b_sym
            nC = sp.simplify(-nA * px_sym - nB * py_sym)
            normal_eq = _format_line_eq(nA, nB, nC)
            normal_line_abc = (_sym_to_float(nA), _sym_to_float(nB), _sym_to_float(nC))
    else:
        raise SystemExit(f"Unsupported conic type: {ctype}")

    return {
        "subcommand": "conic",
        "type": ctype,
        "equation": eq_str,
        "equationLatex": eq_latex,
        "params": {
            "h": _clean_float(h_val),
            "k": _clean_float(k_val),
            "hExact": str(h_sym),
            "kExact": str(k_sym),
            "a": _clean_float(a_val) if ctype != "parabola" else None,
            "b": _clean_float(b_val) if ctype != "parabola" else None,
            "p": _clean_float(p_val) if ctype == "parabola" else None,
        },
        "center": {"x": _clean_float(h_val), "y": _clean_float(k_val)},
        "vertices": vertices,
        "foci": foci,
        "directrices": directrices,
        "eccentricity": _sym_to_float(ecc_sym),
        "eccentricityExact": str(ecc_sym),
        "latusRectum": _sym_to_float(lr_sym),
        "latusRectumExact": str(lr_sym),
        "latusRectumEndpoints": lr_endpoints,
        "asymptotes": asymptotes,
        "directorCircleStatus": director_status,
        "directorCircleRadius": director_radius,
        "directorCircleRadiusExact": director_radius_exact,
        "directorCircleEquation": director_eq,
        "directorLocus": director_locus,
        "tangentParam": _clean_float(t_val) if t_val is not None else None,
        "tangentPoint": tangent_point,
        "tangentEquation": tangent_eq,
        "normalEquation": normal_eq,
        "_flags": {
            "showDirector": bool(args.show_director),
            "showLatusRectum": bool(args.show_latus_rectum),
            "showNormal": bool(args.show_normal),
            "showAsymptotes": bool(args.show_asymptotes),
        },
        "_internal": {
            "tangentABC": tangent_line_abc,
            "normalABC": normal_line_abc,
        },
    }


def _draw_infinite_line_2d(
    ax: Any,
    A: float,
    B: float,
    C: float,
    xlim: tuple[float, float],
    ylim: tuple[float, float],
    **plot_kwargs: Any,
) -> None:
    """Clip A*x + B*y + C = 0 cleanly to the visible bounding box."""
    pts: list[tuple[float, float]] = []
    x0, x1 = xlim
    y0, y1 = ylim
    eps = 1e-12
    if abs(B) > eps:
        for xv in (x0, x1):
            yv = -(A * xv + C) / B
            if y0 - 1e-6 <= yv <= y1 + 1e-6:
                pts.append((xv, yv))
    if abs(A) > eps:
        for yv in (y0, y1):
            xv = -(B * yv + C) / A
            if x0 - 1e-6 <= xv <= x1 + 1e-6:
                pts.append((xv, yv))
    # Select the farthest pair of box intersections (immune to corner duplicates)
    best_pair: tuple[tuple[float, float], tuple[float, float]] | None = None
    best_d2 = 1e-12
    for i in range(len(pts)):
        for j in range(i + 1, len(pts)):
            dx = pts[j][0] - pts[i][0]
            dy = pts[j][1] - pts[i][1]
            d2 = dx * dx + dy * dy
            if d2 > best_d2:
                best_d2 = d2
                best_pair = (pts[i], pts[j])
    if best_pair is not None:
        ax.plot([best_pair[0][0], best_pair[1][0]], [best_pair[0][1], best_pair[1][1]], **plot_kwargs)


def render_conic_static(data: dict[str, Any], out_path: Path, theme: str, dpi: int, title: str | None) -> int:
    t = _apply_mpl_rc(theme)
    ctype = data["type"]
    h = data["params"]["h"]
    k = data["params"]["k"]
    flags = data["_flags"]

    fig = plt.figure(figsize=(11.2, 6.4))
    gs = gridspec.GridSpec(1, 2, width_ratios=[2.55, 1.35], wspace=0.14)
    ax = fig.add_subplot(gs[0, 0])
    ax_info = fig.add_subplot(gs[0, 1])
    _style_2d_axes(ax, t)

    # Determine balanced viewing window
    if ctype == "parabola":
        p = data["params"]["p"]
        span = max(abs(p) * 4.5, 4.0)
        if data["tangentPoint"]:
            span = max(span, abs(data["tangentPoint"]["x"] - h) * 1.35, abs(data["tangentPoint"]["y"] - k) * 1.35)
        if p > 0:
            xlim = (h - max(2.2 * p, span * 0.45), h + span * 1.15)
        else:
            xlim = (h - span * 1.15, h + max(2.2 * abs(p), span * 0.45))
        ylim = (k - span * 0.9, k + span * 0.9)
    elif ctype == "ellipse":
        a, b = data["params"]["a"], data["params"]["b"]
        r_max = max(a, b)
        if flags["showDirector"] and data["directorCircleRadius"]:
            r_max = max(r_max, data["directorCircleRadius"])
        for d in data["directrices"]:
            if abs(d["value"] - (h if d["axis"] == "x" else k)) < r_max * 2.3:
                r_max = max(r_max, abs(d["value"] - (h if d["axis"] == "x" else k)) * 1.08)
        span = r_max * 1.28
        xlim = (h - span, h + span)
        ylim = (k - span, k + span)
    else:
        a, b = data["params"]["a"], data["params"]["b"]
        c_val = math.hypot(a, b)
        span = max(a * 2.7, b * 2.7, c_val * 1.65)
        if data["tangentPoint"]:
            span = max(span, abs(data["tangentPoint"]["x"] - h) * 1.35, abs(data["tangentPoint"]["y"] - k) * 1.35)
        xlim = (h - span, h + span)
        ylim = (k - span, k + span)

    ax.set_xlim(*xlim)
    ax.set_ylim(*ylim)
    ax.set_aspect("equal", "box")

    # Plot main conic curve
    if ctype == "parabola":
        p = data["params"]["p"]
        tmax = math.sqrt(max(1.0, (xlim[1] - h) / p if p > 0 else (xlim[0] - h) / p)) * 1.1
        ts = np.linspace(-tmax, tmax, 700)
        xs = h + p * ts ** 2
        ys = k + 2 * p * ts
        ax.plot(xs, ys, color=t["primary"], linewidth=2.4, label="Parabola", zorder=4)
    elif ctype == "ellipse":
        a, b = data["params"]["a"], data["params"]["b"]
        th = np.linspace(0, 2 * math.pi, 720)
        ax.plot(h + a * np.cos(th), k + b * np.sin(th), color=t["primary"], linewidth=2.4, label="Ellipse", zorder=4)
    else:
        a, b = data["params"]["a"], data["params"]["b"]
        umax = math.acosh(max(1.2, (xlim[1] - h) / a)) * 1.05
        us = np.linspace(-umax, umax, 500)
        ax.plot(h + a * np.cosh(us), k + b * np.sinh(us), color=t["primary"], linewidth=2.4, label="Hyperbola", zorder=4)
        ax.plot(h - a * np.cosh(us), k + b * np.sinh(us), color=t["primary"], linewidth=2.4, zorder=4)

    # Directrices
    for idx, d in enumerate(data["directrices"]):
        lbl = "Directrix" if idx == 0 else None
        if d["axis"] == "x":
            if xlim[0] <= d["value"] <= xlim[1]:
                ax.axvline(d["value"], color=t["emerald"], linestyle="--", linewidth=1.4, label=lbl, zorder=2)
        else:
            if ylim[0] <= d["value"] <= ylim[1]:
                ax.axhline(d["value"], color=t["emerald"], linestyle="--", linewidth=1.4, label=lbl, zorder=2)

    # Asymptotes (hyperbola)
    if ctype == "hyperbola" and flags["showAsymptotes"]:
        for idx, asym in enumerate(data["asymptotes"]):
            m = asym["slope"]
            _draw_infinite_line_2d(
                ax,
                m,
                -1.0,
                k - m * h,
                xlim,
                ylim,
                color=t["muted"],
                linestyle=":",
                linewidth=1.35,
                label="Asymptotes" if idx == 0 else None,
                zorder=2,
            )

    # Director circle
    if flags["showDirector"]:
        if data["directorCircleStatus"] == "real" and data["directorCircleRadius"]:
            circ = Circle(
                (h, k),
                data["directorCircleRadius"],
                fill=False,
                edgecolor=t["amber"],
                linestyle="-.",
                linewidth=1.6,
                label=f"Director Circle (R={_fmt_num(data['directorCircleRadius'])})",
                zorder=3,
            )
            ax.add_patch(circ)
        elif data["directorCircleStatus"] == "point":
            ax.scatter([h], [k], color=t["amber"], s=70, marker="o", zorder=6, label="Director Point (a=b)")

    # Latus rectum
    if flags["showLatusRectum"] and len(data["latusRectumEndpoints"]) == 2:
        l1, l2 = data["latusRectumEndpoints"]
        ax.plot(
            [l1["x"], l2["x"]],
            [l1["y"], l2["y"]],
            color=t["secondary"],
            linestyle="--",
            linewidth=1.55,
            label=f"Latus Rectum ({_fmt_num(data['latusRectum'])})",
            zorder=5,
        )
        ax.scatter([l1["x"], l2["x"]], [l1["y"], l2["y"]], color=t["secondary"], s=28, zorder=6)

    # Tangent and Normal
    annotations: list[dict[str, Any]] = []
    if data["tangentPoint"]:
        tp = data["tangentPoint"]
        t_abc = data["_internal"]["tangentABC"]
        if t_abc is not None:
            _draw_infinite_line_2d(
                ax, t_abc[0], t_abc[1], t_abc[2], xlim, ylim, color=t["teal"], linewidth=1.9, label="Tangent", zorder=6
            )
        if flags["showNormal"] and data["_internal"]["normalABC"] is not None:
            n_abc = data["_internal"]["normalABC"]
            _draw_infinite_line_2d(
                ax, n_abc[0], n_abc[1], n_abc[2], xlim, ylim, color=t["amber"], linestyle="--", linewidth=1.7, label="Normal", zorder=6
            )
        ax.scatter([tp["x"]], [tp["y"]], color=t["teal"], edgecolor=t["fig_bg"], s=65, linewidth=1.2, zorder=8)
        annotations.append(
            {
                "x": tp["x"],
                "y": tp["y"],
                "label": f"P({_fmt_num(tp['x'], 3)}, {_fmt_num(tp['y'], 3)})",
                "color": t["teal"],
            }
        )

    # Foci & Center/Vertex markers
    for f in data["foci"]:
        ax.scatter([f["x"]], [f["y"]], color=t["crimson"], edgecolor=t["fig_bg"], s=60, linewidth=1.1, zorder=7)
        annotations.append(
            {
                "x": f["x"],
                "y": f["y"],
                "label": f"{f['label']}({_fmt_num(f['x'], 3)}, {_fmt_num(f['y'], 3)})",
                "color": t["crimson"],
            }
        )
    if ctype == "parabola":
        ax.scatter([h], [k], color=t["primary"], edgecolor=t["fig_bg"], s=45, zorder=7)
        annotations.append({"x": h, "y": k, "label": f"V({_fmt_num(h)}, {_fmt_num(k)})", "color": t["primary"]})
    else:
        ax.scatter([h], [k], color=t["muted"], marker="+", s=55, zorder=6)

    _place_non_overlapping_annotations(ax, annotations, t)

    plot_title = title or f"{ctype.title()}: {data['equation']}"
    ax.set_title(plot_title, fontsize=11.5, fontweight="bold", color=t["text"], pad=10)
    leg = ax.legend(loc="upper left", fontsize=7.8, frameon=True, framealpha=0.94, facecolor=t["pill_bg"], edgecolor=t["panel_border"])
    leg.set_zorder(15)
    for text in leg.get_texts():
        text.set_color(t["text"])

    # Side SymPy Info Panel
    foci_str = ", ".join(f"({f['xExact']}, {f['yExact']})" for f in data["foci"])
    dir_str = "; ".join(d["equation"] for d in data["directrices"]) if data["directrices"] else "None (circle)"
    rows: list[tuple[str, str, str | None]] = [
        ("EQUATION", data["equation"], "primary"),
        ("ECCENTRICITY (e)", f"{data['eccentricityExact']}  (~{_fmt_num(data['eccentricity'], 4)})", "text"),
        ("FOCI", foci_str, "crimson"),
        ("DIRECTRIX", dir_str, "emerald"),
        ("LATUS RECTUM", f"{data['latusRectumExact']}  (~{_fmt_num(data['latusRectum'], 4)})", "secondary"),
        ("DIRECTOR LOCUS", data["directorCircleEquation"] or "None", "amber"),
    ]
    if data["asymptotes"]:
        rows.append(("ASYMPTOTES", "; ".join(a["equation"] for a in data["asymptotes"]), "muted"))
    if data["tangentPoint"]:
        tp = data["tangentPoint"]
        rows.append(("TANGENT POINT P", f"({tp['xExact']}, {tp['yExact']})", "teal"))
        rows.append(("TANGENT EQ", data["tangentEquation"] or "", "teal"))
        if flags["showNormal"] and data["normalEquation"]:
            rows.append(("NORMAL EQ", data["normalEquation"], "amber"))

    _render_info_panel(ax_info, t, "SymPy Exact Properties", f"JEE Conic · {ctype.title()}", rows)
    return _save_figure(fig, out_path, dpi)


def render_conic_html(data: dict[str, Any], out_path: Path, theme: str, title: str | None) -> int:
    ctype = data["type"]
    p_init = data["params"]["p"] if ctype == "parabola" else 2.0
    a_init = data["params"]["a"] if ctype != "parabola" else 4.0
    b_init = data["params"]["b"] if ctype != "parabola" else 2.5
    t_init = data["tangentParam"] if data["tangentParam"] is not None else (1.0 if ctype == "parabola" else 40.0)
    flags = data["_flags"]

    init_payload = {
        "type": ctype,
        "h": data["params"]["h"],
        "k": data["params"]["k"],
        "a": a_init,
        "b": b_init,
        "p": p_init,
        "t": t_init,
        "showDirector": flags["showDirector"],
        "showLR": flags["showLatusRectum"],
        "showNormal": flags["showNormal"] or True,
        "showAsymptotes": flags["showAsymptotes"],
    }

    if ctype == "parabola":
        p_max = max(8.0, abs(p_init) * 2.5)
        sliders_html = f"""
        <div class="control-row">
          <div class="control-head"><span>Focal parameter p (y&sup2; = 4px)</span><span class="control-val" id="valP">{_fmt_num(p_init)}</span></div>
          <input type="range" id="slP" min="{-p_max}" max="{p_max}" step="0.1" value="{p_init}">
        </div>
        <div class="control-row">
          <div class="control-head"><span>Tangent parameter t</span><span class="control-val" id="valT">{_fmt_num(t_init)}</span></div>
          <input type="range" id="slT" min="-3" max="3" step="0.05" value="{max(-3.0, min(3.0, t_init))}">
        </div>"""
    else:
        a_max = max(10.0, a_init * 2.2)
        b_max = max(10.0, b_init * 2.2)
        t_min, t_max = (-72, 72) if ctype == "hyperbola" else (0, 360)
        t_clamped = max(t_min, min(t_max, t_init)) if ctype == "hyperbola" else (t_init % 360)
        sliders_html = f"""
        <div class="control-row">
          <div class="control-head"><span>Semi-axis a</span><span class="control-val" id="valA">{_fmt_num(a_init)}</span></div>
          <input type="range" id="slA" min="0.5" max="{a_max}" step="0.1" value="{a_init}">
        </div>
        <div class="control-row">
          <div class="control-head"><span>Semi-axis b</span><span class="control-val" id="valB">{_fmt_num(b_init)}</span></div>
          <input type="range" id="slB" min="0.5" max="{b_max}" step="0.1" value="{b_init}">
        </div>
        <div class="control-row">
          <div class="control-head"><span>Parametric angle &theta; (deg)</span><span class="control-val" id="valT">{_fmt_num(t_clamped)}&deg;</span></div>
          <input type="range" id="slT" min="{t_min}" max="{t_max}" step="1" value="{t_clamped}">
        </div>"""

    sidebar = f"""
    <div class="card">
      <div class="card-title">Live Parameters</div>
      {sliders_html}
    </div>
    <div class="card">
      <div class="card-title">Overlays</div>
      <div class="toggles">
        <label class="toggle-item"><input type="checkbox" id="chkTangent" checked> Tangent</label>
        <label class="toggle-item"><input type="checkbox" id="chkNormal" {"checked" if init_payload["showNormal"] else ""}> Normal</label>
        <label class="toggle-item"><input type="checkbox" id="chkDirector" {"checked" if init_payload["showDirector"] else ""}> Director Locus</label>
        <label class="toggle-item"><input type="checkbox" id="chkLR" {"checked" if init_payload["showLR"] else ""}> Latus Rectum</label>
        {"<label class='toggle-item'><input type='checkbox' id='chkAsym' " + ("checked" if init_payload["showAsymptotes"] else "") + "> Asymptotes</label>" if ctype == "hyperbola" else ""}
      </div>
    </div>
    <div class="card">
      <div class="card-title">Exact Live Readout</div>
      <div class="kv-list" id="readout"></div>
    </div>
    """

    js_body = f"""
  const INIT = {safe_json_for_script(init_payload)};
  const state = Object.assign({{}}, INIT, {{
    showTangent: true,
    zoom: 1.0,
    panX: INIT.h,
    panY: INIT.k
  }});
  const canvas = document.getElementById("canvas");
  const ctx = canvas.getContext("2d");
  const readout = document.getElementById("readout");
  const hud = document.getElementById("hud");

  function fmtLine(A, B, C) {{
    if (Math.abs(B) < 1e-8 && Math.abs(A) > 1e-8) return "x = " + fmt(-C / A, 3);
    if (Math.abs(A) < 1e-8 && Math.abs(B) > 1e-8) return "y = " + fmt(-C / B, 3);
    const m = -A / B, c = -C / B;
    const sign = c >= 0 ? " + " : " - ";
    return "y = " + fmt(m, 3) + "x" + (Math.abs(c) > 1e-8 ? sign + fmt(Math.abs(c), 3) : "");
  }}

  function worldRange() {{
    let base = 10;
    if (state.type === "parabola") {{
      base = Math.max(8, Math.abs(state.p) * 5.2);
    }} else {{
      base = Math.max(state.a, state.b) * 2.6 + 2.0;
    }}
    return base / state.zoom;
  }}

  function draw() {{
    const rect = canvas.getBoundingClientRect();
    const w = Math.max(10, rect.width), hpx = Math.max(10, rect.height);
    const dpr = window.devicePixelRatio || 1;
    if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(hpx * dpr)) {{
      canvas.width = Math.round(w * dpr);
      canvas.height = Math.round(hpx * dpr);
    }}
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const bg = cssVar("--canvas-bg"), gridC = cssVar("--grid"), axisC = cssVar("--axis");
    const cPrim = cssVar("--accent"), cTeal = cssVar("--teal"), cAmb = cssVar("--amber");
    const cCrim = cssVar("--crimson"), cEm = cssVar("--emerald"), cVio = cssVar("--violet"), cTxt = cssVar("--text");

    ctx.fillStyle = bg;
    ctx.fillRect(0, 0, w, hpx);

    const halfW = worldRange();
    const scale = Math.min(w, hpx) / (2 * halfW);
    const toSX = (x) => w / 2 + (x - state.panX) * scale;
    const toSY = (y) => hpx / 2 - (y - state.panY) * scale;
    const xMin = state.panX - w / (2 * scale), xMax = state.panX + w / (2 * scale);
    const yMin = state.panY - hpx / (2 * scale), yMax = state.panY + hpx / (2 * scale);

    // Grid
    const rawStep = (2 * halfW) / 10;
    const pow10 = Math.pow(10, Math.floor(Math.log10(Math.max(1e-6, rawStep))));
    const step = rawStep / pow10 < 2 ? pow10 : (rawStep / pow10 < 5 ? 2 * pow10 : 5 * pow10);
    ctx.strokeStyle = gridC; ctx.lineWidth = 1;
    ctx.beginPath();
    for (let x = Math.ceil(xMin / step) * step; x <= xMax; x += step) {{
      const sx = toSX(x); ctx.moveTo(sx, 0); ctx.lineTo(sx, hpx);
    }}
    for (let y = Math.ceil(yMin / step) * step; y <= yMax; y += step) {{
      const sy = toSY(y); ctx.moveTo(0, sy); ctx.lineTo(w, sy);
    }}
    ctx.stroke();

    // Axes
    ctx.strokeStyle = axisC; ctx.lineWidth = 1.4;
    ctx.beginPath();
    ctx.moveTo(0, toSY(0)); ctx.lineTo(w, toSY(0));
    ctx.moveTo(toSX(0), 0); ctx.lineTo(toSX(0), hpx);
    ctx.stroke();
    ctx.fillStyle = axisC; ctx.font = "11px " + cssVar("--mono");
    for (let x = Math.ceil(xMin / step) * step; x <= xMax; x += step) {{
      if (Math.abs(x) < 1e-8) continue;
      ctx.fillText(fmt(x, 2), toSX(x) + 3, Math.min(hpx - 6, Math.max(14, toSY(0) + 13)));
    }}
    for (let y = Math.ceil(yMin / step) * step; y <= yMax; y += step) {{
      if (Math.abs(y) < 1e-8) continue;
      ctx.fillText(fmt(y, 2), Math.min(w - 30, Math.max(6, toSX(0) + 5)), toSY(y) - 3);
    }}

    function drawLineABC(A, B, C, color, dash, lw) {{
      const pts = [];
      const padX = (xMax - xMin) * 0.02 + 1e-6;
      const padY = (yMax - yMin) * 0.02 + 1e-6;
      if (Math.abs(B) > 1e-9) {{
        for (const xv of [xMin, xMax]) {{
          const yv = -(A * xv + C) / B;
          if (yv >= yMin - padY && yv <= yMax + padY) pts.push([xv, yv]);
        }}
      }}
      if (Math.abs(A) > 1e-9) {{
        for (const yv of [yMin, yMax]) {{
          const xv = -(B * yv + C) / A;
          if (xv >= xMin - padX && xv <= xMax + padX) pts.push([xv, yv]);
        }}
      }}
      let bestA = null, bestB = null, bestD2 = 1e-12;
      for (let i = 0; i < pts.length; i++) {{
        for (let j = i + 1; j < pts.length; j++) {{
          const dx = pts[j][0] - pts[i][0], dy = pts[j][1] - pts[i][1];
          const d2 = dx * dx + dy * dy;
          if (d2 > bestD2) {{ bestD2 = d2; bestA = pts[i]; bestB = pts[j]; }}
        }}
      }}
      if (bestA && bestB) {{
        ctx.save(); ctx.strokeStyle = color; ctx.lineWidth = lw || 1.8;
        if (dash) ctx.setLineDash(dash);
        ctx.beginPath(); ctx.moveTo(toSX(bestA[0]), toSY(bestA[1]));
        ctx.lineTo(toSX(bestB[0]), toSY(bestB[1]));
        ctx.stroke(); ctx.restore();
      }}
    }}

    function drawDot(x, y, color, label) {{
      const sx = toSX(x), sy = toSY(y);
      ctx.beginPath(); ctx.arc(sx, sy, 5, 0, Math.PI * 2);
      ctx.fillStyle = color; ctx.fill();
      ctx.strokeStyle = bg; ctx.lineWidth = 1.5; ctx.stroke();
      if (label) {{
        ctx.font = "600 11.5px " + cssVar("--sans");
        ctx.fillStyle = color;
        ctx.fillText(label, sx + 8, sy - 7);
      }}
    }}

    const h = state.h, k = state.k;
    const rows = [];

    if (state.type === "parabola") {{
      const p = Math.abs(state.p) < 0.05 ? (state.p >= 0 ? 0.05 : -0.05) : state.p;
      const tSpan = Math.sqrt(Math.max(4, Math.abs((p > 0 ? xMax - h : xMin - h) / p))) * 1.15;
      ctx.strokeStyle = cPrim; ctx.lineWidth = 2.5;
      ctx.beginPath();
      for (let i = 0; i <= 400; i++) {{
        const tv = -tSpan + (2 * tSpan * i) / 400;
        const px = h + p * tv * tv, py = k + 2 * p * tv;
        if (i === 0) ctx.moveTo(toSX(px), toSY(py)); else ctx.lineTo(toSX(px), toSY(py));
      }}
      ctx.stroke();

      // Directrix
      drawLineABC(1, 0, -(h - p), state.showDirector ? cAmb : cEm, [6, 5], state.showDirector ? 2.2 : 1.5);
      // Latus rectum
      if (state.showLR) {{
        ctx.save(); ctx.strokeStyle = cVio; ctx.lineWidth = 1.7; ctx.setLineDash([5, 4]);
        ctx.beginPath(); ctx.moveTo(toSX(h + p), toSY(k - 2 * p)); ctx.lineTo(toSX(h + p), toSY(k + 2 * p));
        ctx.stroke(); ctx.restore();
      }}
      const t = state.t;
      const px = h + p * t * t, py = k + 2 * p * t;
      const tA = 1, tB = -t, tC = -h + t * k + p * t * t;
      const nA = t, nB = 1, nC = -t * h - k - 2 * p * t - p * t * t * t;
      if (state.showTangent) drawLineABC(tA, tB, tC, cTeal, null, 2.0);
      if (state.showNormal) drawLineABC(nA, nB, nC, cAmb, [6, 4], 1.7);
      drawDot(h + p, k, cCrim, "F(" + fmt(h + p, 2) + ", " + fmt(k, 2) + ")");
      drawDot(h, k, cPrim, "V");
      if (state.showTangent || state.showNormal) drawDot(px, py, cTeal, "P(" + fmt(px, 2) + ", " + fmt(py, 2) + ")");

      rows.push(["Equation", "(y - " + fmt(k, 2) + ")² = " + fmt(4 * p, 2) + "(x - " + fmt(h, 2) + ")", "accent"]);
      rows.push(["Focus F", "(" + fmt(h + p, 3) + ", " + fmt(k, 3) + ")", "crimson"]);
      rows.push(["Directrix / Director Locus", "x = " + fmt(h - p, 3), "amber"]);
      rows.push(["Eccentricity e", "1", ""]);
      rows.push(["Latus Rectum", fmt(Math.abs(4 * p), 3), "violet"]);
      rows.push(["Point P(t=" + fmt(t, 2) + ")", "(" + fmt(px, 3) + ", " + fmt(py, 3) + ")", "teal"]);
      rows.push(["Tangent", fmtLine(tA, tB, tC), "teal"]);
      rows.push(["Normal", fmtLine(nA, nB, nC), "amber"]);
    }} else if (state.type === "ellipse") {{
      const a = state.a, b = state.b;
      ctx.strokeStyle = cPrim; ctx.lineWidth = 2.5;
      ctx.beginPath();
      for (let i = 0; i <= 360; i++) {{
        const rad = (i * Math.PI) / 180;
        const px = h + a * Math.cos(rad), py = k + b * Math.sin(rad);
        if (i === 0) ctx.moveTo(toSX(px), toSY(py)); else ctx.lineTo(toSX(px), toSY(py));
      }}
      ctx.stroke();

      const horiz = a >= b - 1e-9;
      const c = Math.sqrt(Math.abs(a * a - b * b));
      const e = horiz ? c / a : c / b;
      const lr = horiz ? (2 * b * b) / a : (2 * a * a) / b;
      const rDir = Math.hypot(a, b);

      if (c > 1e-6) {{
        if (horiz) {{
          drawLineABC(1, 0, -(h + a * a / c), cEm, [6, 5], 1.4);
          drawLineABC(1, 0, -(h - a * a / c), cEm, [6, 5], 1.4);
        }} else {{
          drawLineABC(0, 1, -(k + b * b / c), cEm, [6, 5], 1.4);
          drawLineABC(0, 1, -(k - b * b / c), cEm, [6, 5], 1.4);
        }}
      }}
      if (state.showDirector) {{
        ctx.save(); ctx.strokeStyle = cAmb; ctx.lineWidth = 1.7; ctx.setLineDash([8, 4, 2, 4]);
        ctx.beginPath(); ctx.arc(toSX(h), toSY(k), rDir * scale, 0, Math.PI * 2);
        ctx.stroke(); ctx.restore();
      }}
      if (state.showLR) {{
        ctx.save(); ctx.strokeStyle = cVio; ctx.lineWidth = 1.7; ctx.setLineDash([5, 4]);
        ctx.beginPath();
        if (horiz) {{
          ctx.moveTo(toSX(h + c), toSY(k - b * b / a)); ctx.lineTo(toSX(h + c), toSY(k + b * b / a));
        }} else {{
          ctx.moveTo(toSX(h - a * a / b), toSY(k + c)); ctx.lineTo(toSX(h + a * a / b), toSY(k + c));
        }}
        ctx.stroke(); ctx.restore();
      }}
      const rad = (state.t * Math.PI) / 180;
      const cth = Math.cos(rad), sth = Math.sin(rad);
      const px = h + a * cth, py = k + b * sth;
      const tA = b * cth, tB = a * sth, tC = -tA * h - tB * k - a * b;
      const nA = a * sth, nB = -b * cth, nC = -nA * px - nB * py;
      if (state.showTangent) drawLineABC(tA, tB, tC, cTeal, null, 2.0);
      if (state.showNormal) drawLineABC(nA, nB, nC, cAmb, [6, 4], 1.7);

      if (horiz) {{
        drawDot(h + c, k, cCrim, "F1"); drawDot(h - c, k, cCrim, "F2");
      }} else {{
        drawDot(h, k + c, cCrim, "F1"); drawDot(h, k - c, cCrim, "F2");
      }}
      if (state.showTangent || state.showNormal) drawDot(px, py, cTeal, "P(" + fmt(px, 2) + ", " + fmt(py, 2) + ")");

      rows.push(["Equation", "x²/" + fmt(a * a, 2) + " + y²/" + fmt(b * b, 2) + " = 1", "accent"]);
      rows.push(["Eccentricity e", fmt(e, 4), ""]);
      rows.push(["Foci", horiz ? "(" + fmt(h, 2) + " ± " + fmt(c, 3) + ", " + fmt(k, 2) + ")" : "(" + fmt(h, 2) + ", " + fmt(k, 2) + " ± " + fmt(c, 3) + ")", "crimson"]);
      rows.push(["Latus Rectum", fmt(lr, 3), "violet"]);
      rows.push(["Director Circle", "R = √(a²+b²) = " + fmt(rDir, 3), "amber"]);
      rows.push(["Point P(θ=" + fmt(state.t, 1) + "°)", "(" + fmt(px, 3) + ", " + fmt(py, 3) + ")", "teal"]);
      rows.push(["Tangent", fmtLine(tA, tB, tC), "teal"]);
      rows.push(["Normal", fmtLine(nA, nB, nC), "amber"]);
    }} else {{
      // Hyperbola
      const a = state.a, b = state.b;
      const c = Math.hypot(a, b);
      const e = c / a;
      const lr = (2 * b * b) / a;
      const uMax = Math.acosh(Math.max(1.5, Math.abs((xMax - h) / a) + 1.5, Math.abs((xMin - h) / a) + 1.5));
      ctx.strokeStyle = cPrim; ctx.lineWidth = 2.5;
      for (const sgn of [1, -1]) {{
        ctx.beginPath();
        for (let i = 0; i <= 260; i++) {{
          const u = -uMax + (2 * uMax * i) / 260;
          const px = h + sgn * a * Math.cosh(u), py = k + b * Math.sinh(u);
          if (i === 0) ctx.moveTo(toSX(px), toSY(py)); else ctx.lineTo(toSX(px), toSY(py));
        }}
        ctx.stroke();
      }}
      drawLineABC(1, 0, -(h + a * a / c), cEm, [6, 5], 1.4);
      drawLineABC(1, 0, -(h - a * a / c), cEm, [6, 5], 1.4);
      if (state.showAsymptotes) {{
        drawLineABC(b / a, -1, k - (b / a) * h, axisC, [3, 4], 1.3);
        drawLineABC(-b / a, -1, k + (b / a) * h, axisC, [3, 4], 1.3);
      }}
      const d2 = a * a - b * b;
      let dirText = "None real (a < b)";
      if (d2 > 1e-8) {{
        const rDir = Math.sqrt(d2);
        dirText = "R = √(a²-b²) = " + fmt(rDir, 3);
        if (state.showDirector) {{
          ctx.save(); ctx.strokeStyle = cAmb; ctx.lineWidth = 1.7; ctx.setLineDash([8, 4, 2, 4]);
          ctx.beginPath(); ctx.arc(toSX(h), toSY(k), rDir * scale, 0, Math.PI * 2);
          ctx.stroke(); ctx.restore();
        }}
      }} else if (Math.abs(d2) <= 1e-8) {{
        dirText = "Point (" + fmt(h, 2) + ", " + fmt(k, 2) + ") [a = b]";
        if (state.showDirector) drawDot(h, k, cAmb, "Dir Pt");
      }}
      if (state.showLR) {{
        ctx.save(); ctx.strokeStyle = cVio; ctx.lineWidth = 1.7; ctx.setLineDash([5, 4]);
        ctx.beginPath();
        ctx.moveTo(toSX(h + c), toSY(k - b * b / a)); ctx.lineTo(toSX(h + c), toSY(k + b * b / a));
        ctx.stroke(); ctx.restore();
      }}
      const rad = (state.t * Math.PI) / 180;
      const cth = Math.cos(rad);
      let px = h + a, py = k, tA = 1, tB = 0, tC = -(h + a), nA = 0, nB = 1, nC = -k;
      if (Math.abs(cth) > 1e-5) {{
        const secv = 1 / cth, tanv = Math.tan(rad), sinv = Math.sin(rad);
        px = h + a * secv; py = k + b * tanv;
        tA = b * secv; tB = -a * tanv; tC = -tA * h - tB * k - a * b;
        nA = a * sinv; nB = b; nC = -nA * px - nB * py;
      }}
      if (state.showTangent) drawLineABC(tA, tB, tC, cTeal, null, 2.0);
      if (state.showNormal) drawLineABC(nA, nB, nC, cAmb, [6, 4], 1.7);
      drawDot(h + c, k, cCrim, "F1"); drawDot(h - c, k, cCrim, "F2");
      if (state.showTangent || state.showNormal) drawDot(px, py, cTeal, "P(" + fmt(px, 2) + ", " + fmt(py, 2) + ")");

      rows.push(["Equation", "x²/" + fmt(a * a, 2) + " - y²/" + fmt(b * b, 2) + " = 1", "accent"]);
      rows.push(["Eccentricity e", fmt(e, 4), ""]);
      rows.push(["Foci", "(" + fmt(h, 2) + " ± " + fmt(c, 3) + ", " + fmt(k, 2) + ")", "crimson"]);
      rows.push(["Asymptotes", "y = ±" + fmt(b / a, 3) + "x", ""]);
      rows.push(["Latus Rectum", fmt(lr, 3), "violet"]);
      rows.push(["Director Circle", dirText, d2 > 1e-8 ? "amber" : "crimson"]);
      rows.push(["Point P(θ=" + fmt(state.t, 1) + "°)", "(" + fmt(px, 3) + ", " + fmt(py, 3) + ")", "teal"]);
      rows.push(["Tangent", fmtLine(tA, tB, tC), "teal"]);
      rows.push(["Normal", fmtLine(nA, nB, nC), "amber"]);
    }}

    readout.innerHTML = rows.map(r =>
      '<div class="kv"><span class="kv-k">' + r[0] + '</span><span class="kv-v ' + r[2] + '">' + r[1] + '</span></div>'
    ).join("");
  }}

  function bindSlider(id, valId, key, suffix) {{
    const el = document.getElementById(id), vEl = document.getElementById(valId);
    if (!el) return;
    el.addEventListener("input", function() {{
      state[key] = parseFloat(el.value);
      if (vEl) vEl.textContent = fmt(state[key], 2) + (suffix || "");
      draw();
    }});
  }}
  bindSlider("slP", "valP", "p", "");
  bindSlider("slA", "valA", "a", "");
  bindSlider("slB", "valB", "b", "");
  bindSlider("slT", "valT", "t", state.type === "parabola" ? "" : "°");

  function bindCheck(id, key) {{
    const el = document.getElementById(id);
    if (!el) return;
    el.addEventListener("change", function() {{ state[key] = el.checked; draw(); }});
  }}
  bindCheck("chkTangent", "showTangent");
  bindCheck("chkNormal", "showNormal");
  bindCheck("chkDirector", "showDirector");
  bindCheck("chkLR", "showLR");
  bindCheck("chkAsym", "showAsymptotes");

  // Pan & zoom
  let dragging = false, lx = 0, ly = 0;
  canvas.addEventListener("pointerdown", function(e) {{ dragging = true; lx = e.clientX; ly = e.clientY; }});
  window.addEventListener("pointerup", function() {{ dragging = false; }});
  window.addEventListener("pointermove", function(e) {{
    const rect = canvas.getBoundingClientRect();
    const scale = Math.min(rect.width, rect.height) / (2 * worldRange());
    if (dragging) {{
      state.panX -= (e.clientX - lx) / scale;
      state.panY += (e.clientY - ly) / scale;
      lx = e.clientX; ly = e.clientY;
      draw();
    }} else if (e.target === canvas) {{
      const wx = state.panX + (e.clientX - rect.left - rect.width / 2) / scale;
      const wy = state.panY - (e.clientY - rect.top - rect.height / 2) / scale;
      hud.textContent = "Cursor: (" + fmt(wx, 2) + ", " + fmt(wy, 2) + ") · Drag to pan, scroll to zoom";
    }}
  }});
  canvas.addEventListener("wheel", function(e) {{
    e.preventDefault();
    state.zoom = Math.max(0.25, Math.min(6.0, state.zoom * (e.deltaY > 0 ? 0.9 : 1.11)));
    draw();
  }}, {{ passive: false }});

  document.getElementById("resetViewBtn").addEventListener("click", function() {{
    state.zoom = 1.0; state.panX = state.h; state.panY = state.k; draw();
  }});
  onThemeChange = draw;
  window.addEventListener("resize", draw);
  draw();
"""
    html_doc = _build_offline_html(
        title=title or f"Conic Explorer · {ctype.title()}",
        badge=f"100% Offline · {ctype.title()}",
        initial_theme=theme,
        sidebar_html=sidebar,
        hud_hint="Drag sliders to update curve, foci, tangent & director locus",
        js_body=js_body,
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html_doc, encoding="utf-8")
    return out_path.stat().st_size


# ---------------------------------------------------------------------------
# Subcommand 2: ARGAND (Complex Plane Loci & Intersections)
# ---------------------------------------------------------------------------


def _parse_argand_loci(loci_spec: str) -> list[dict[str, Any]]:
    loci: list[dict[str, Any]] = []
    if not loci_spec or not loci_spec.strip():
        return loci
    for idx, chunk in enumerate(loci_spec.split(";")):
        item = chunk.strip()
        if not item:
            continue
        if ":" not in item:
            raise SystemExit(f"Invalid locus spec {item!r}; expected kind:args (e.g. circle:1+2j,3)")
        kind, raw_args = item.split(":", 1)
        kind = kind.strip().lower()
        parts = [p.strip() for p in raw_args.split(",") if p.strip()]
        if kind == "circle":
            if len(parts) != 2:
                raise SystemExit(f"circle requires 2 args <center>,<radius>, got: {item!r}")
            c_sym, c_val = parse_complex_expr(parts[0])
            r_sym, r_val = parse_scalar_expr(parts[1])
            if r_val <= 0:
                raise SystemExit(f"circle radius must be > 0, got: {r_val}")
            loci.append(
                {
                    "id": f"L{idx+1}",
                    "kind": "circle",
                    "center": [c_val.real, c_val.imag],
                    "centerExact": str(c_sym),
                    "radius": _clean_float(r_val),
                    "radiusExact": str(r_sym),
                    "equation": f"|z - ({_fmt_complex(c_val.real, c_val.imag)})| = {_fmt_num(r_val)}",
                }
            )
        elif kind == "perp-bisector":
            if len(parts) != 2:
                raise SystemExit(f"perp-bisector requires 2 args <z1>,<z2>, got: {item!r}")
            z1_sym, z1 = parse_complex_expr(parts[0])
            z2_sym, z2 = parse_complex_expr(parts[1])
            if abs(z2 - z1) < 1e-12:
                raise SystemExit("perp-bisector requires two distinct points")
            mx, my = (z1.real + z2.real) / 2.0, (z1.imag + z2.imag) / 2.0
            nx, ny = z2.real - z1.real, z2.imag - z1.imag
            # Line: nx*(x - mx) + ny*(y - my) = 0 -> A*x + B*y + C = 0
            A, B, C = nx, ny, -(nx * mx + ny * my)
            loci.append(
                {
                    "id": f"L{idx+1}",
                    "kind": "perp-bisector",
                    "z1": [z1.real, z1.imag],
                    "z2": [z2.real, z2.imag],
                    "midpoint": [mx, my],
                    "lineABC": [A, B, C],
                    "equation": f"|z - ({_fmt_complex(z1.real, z1.imag)})| = |z - ({_fmt_complex(z2.real, z2.imag)})|",
                }
            )
        elif kind == "ray":
            if len(parts) != 2:
                raise SystemExit(f"ray requires 2 args <origin>,<angle_deg>, got: {item!r}")
            z0_sym, z0 = parse_complex_expr(parts[0])
            ang_sym, ang_deg = parse_scalar_expr(parts[1])
            rad = math.radians(ang_deg)
            dx, dy = math.cos(rad), math.sin(rad)
            # Line normal (-dy, dx): -dy*(x - x0) + dx*(y - y0) = 0
            A, B, C = -dy, dx, dy * z0.real - dx * z0.imag
            loci.append(
                {
                    "id": f"L{idx+1}",
                    "kind": "ray",
                    "origin": [z0.real, z0.imag],
                    "originExact": str(z0_sym),
                    "angleDeg": _clean_float(ang_deg),
                    "dir": [dx, dy],
                    "lineABC": [A, B, C],
                    "equation": f"arg(z - ({_fmt_complex(z0.real, z0.imag)})) = {_fmt_num(ang_deg)} deg",
                }
            )
        elif kind == "ellipse-foci":
            if len(parts) != 3:
                raise SystemExit(f"ellipse-foci requires 3 args <z1>,<z2>,<major_sum>, got: {item!r}")
            _, z1 = parse_complex_expr(parts[0])
            _, z2 = parse_complex_expr(parts[1])
            _, major_sum = parse_scalar_expr(parts[2])
            d = abs(z2 - z1)
            if major_sum <= d + 1e-9:
                raise SystemExit(
                    f"ellipse-foci requires major sum ({major_sum}) > |z1 - z2| ({d:.4g})"
                )
            cx, cy = (z1.real + z2.real) / 2.0, (z1.imag + z2.imag) / 2.0
            a = major_sum / 2.0
            c = d / 2.0
            b = math.sqrt(max(0.0, a * a - c * c))
            rot = math.atan2(z2.imag - z1.imag, z2.real - z1.real)
            loci.append(
                {
                    "id": f"L{idx+1}",
                    "kind": "ellipse-foci",
                    "z1": [z1.real, z1.imag],
                    "z2": [z2.real, z2.imag],
                    "center": [cx, cy],
                    "a": _clean_float(a),
                    "b": _clean_float(b),
                    "c": _clean_float(c),
                    "eccentricity": _clean_float(c / a),
                    "angleRad": rot,
                    "majorSum": _clean_float(major_sum),
                    "equation": f"|z - ({_fmt_complex(z1.real, z1.imag)})| + |z - ({_fmt_complex(z2.real, z2.imag)})| = {_fmt_num(major_sum)}",
                }
            )
        elif kind == "apollonius":
            if len(parts) != 3:
                raise SystemExit(f"apollonius requires 3 args <z1>,<z2>,<k>, got: {item!r}")
            _, z1 = parse_complex_expr(parts[0])
            _, z2 = parse_complex_expr(parts[1])
            _, kval = parse_scalar_expr(parts[2])
            if kval <= 0:
                raise SystemExit("apollonius ratio k must be > 0")
            if abs(kval - 1.0) < 1e-9:
                mx, my = (z1.real + z2.real) / 2.0, (z1.imag + z2.imag) / 2.0
                nx, ny = z2.real - z1.real, z2.imag - z1.imag
                loci.append(
                    {
                        "id": f"L{idx+1}",
                        "kind": "perp-bisector",
                        "z1": [z1.real, z1.imag],
                        "z2": [z2.real, z2.imag],
                        "midpoint": [mx, my],
                        "lineABC": [nx, ny, -(nx * mx + ny * my)],
                        "equation": f"|z - ({_fmt_complex(z1.real, z1.imag)})| / |z - ({_fmt_complex(z2.real, z2.imag)})| = 1",
                    }
                )
            else:
                denom = 1.0 - kval * kval
                cz = (z1 - (kval * kval) * z2) / denom
                rad = abs(kval * abs(z1 - z2) / denom)
                loci.append(
                    {
                        "id": f"L{idx+1}",
                        "kind": "circle",
                        "subtype": "apollonius",
                        "z1": [z1.real, z1.imag],
                        "z2": [z2.real, z2.imag],
                        "k": kval,
                        "center": [cz.real, cz.imag],
                        "radius": _clean_float(rad),
                        "equation": f"|z - ({_fmt_complex(z1.real, z1.imag)})| / |z - ({_fmt_complex(z2.real, z2.imag)})| = {_fmt_num(kval)}",
                    }
                )
        elif kind in ("line", "segment"):
            if len(parts) != 2:
                raise SystemExit(f"{kind} requires 2 args <z1>,<z2>, got: {item!r}")
            _, z1 = parse_complex_expr(parts[0])
            _, z2 = parse_complex_expr(parts[1])
            dx, dy = z2.real - z1.real, z2.imag - z1.imag
            A, B, C = -dy, dx, dy * z1.real - dx * z1.imag
            loci.append(
                {
                    "id": f"L{idx+1}",
                    "kind": kind,
                    "z1": [z1.real, z1.imag],
                    "z2": [z2.real, z2.imag],
                    "lineABC": [A, B, C],
                    "equation": f"{kind.title()} ({_fmt_complex(z1.real, z1.imag)}) — ({_fmt_complex(z2.real, z2.imag)})",
                }
            )
        else:
            raise SystemExit(
                f"Unknown argand locus kind {kind!r}. Supported: circle, perp-bisector, ray, ellipse-foci, apollonius, line, segment."
            )
    return loci


def _point_satisfies_ray_or_seg(pt: tuple[float, float], loc: dict[str, Any]) -> bool:
    if loc["kind"] == "ray":
        ox, oy = loc["origin"]
        dx, dy = loc["dir"]
        t_param = (pt[0] - ox) * dx + (pt[1] - oy) * dy
        return t_param > 1e-7  # Open origin on ray arg(z-z0)=theta
    if loc["kind"] == "segment":
        x1, y1 = loc["z1"]
        x2, y2 = loc["z2"]
        dx, dy = x2 - x1, y2 - y1
        l2 = dx * dx + dy * dy
        if l2 < 1e-12:
            return False
        t_param = ((pt[0] - x1) * dx + (pt[1] - y1) * dy) / l2
        return -1e-7 <= t_param <= 1.0 + 1e-7
    return True


def _intersect_two_loci(l1: dict[str, Any], l2: dict[str, Any]) -> list[tuple[float, float]]:
    k1, k2 = l1["kind"], l2["kind"]
    line_kinds = {"perp-bisector", "ray", "line", "segment"}
    pts: list[tuple[float, float]] = []

    if k1 == "circle" and k2 == "circle":
        x1, y1 = l1["center"]
        r1 = l1["radius"]
        x2, y2 = l2["center"]
        r2 = l2["radius"]
        d = math.hypot(x2 - x1, y2 - y1)
        if d > 1e-11 and abs(r1 - r2) - 1e-9 <= d <= r1 + r2 + 1e-9:
            a = (r1 * r1 - r2 * r2 + d * d) / (2 * d)
            h_sq = max(0.0, r1 * r1 - a * a)
            h = math.sqrt(h_sq)
            xm = x1 + a * (x2 - x1) / d
            ym = y1 + a * (y2 - y1) / d
            rx = -(y2 - y1) * (h / d)
            ry = (x2 - x1) * (h / d)
            pts.append((xm + rx, ym + ry))
            if h > 1e-8:
                pts.append((xm - rx, ym - ry))
    elif (k1 == "circle" and k2 in line_kinds) or (k2 == "circle" and k1 in line_kinds):
        circ, lin = (l1, l2) if k1 == "circle" else (l2, l1)
        cx, cy = circ["center"]
        r = circ["radius"]
        A, B, C = lin["lineABC"]
        norm2 = A * A + B * B
        if norm2 > 1e-12:
            signed_d = (A * cx + B * cy + C) / norm2
            px = cx - A * signed_d
            py = cy - B * signed_d
            dist2 = (px - cx) ** 2 + (py - cy) ** 2
            if dist2 <= r * r + 1e-9:
                h = math.sqrt(max(0.0, r * r - dist2))
                norm = math.sqrt(norm2)
                ux, uy = -B / norm, A / norm
                cand = [(px + h * ux, py + h * uy)]
                if h > 1e-8:
                    cand.append((px - h * ux, py - h * uy))
                pts = [p for p in cand if _point_satisfies_ray_or_seg(p, lin)]
    elif k1 in line_kinds and k2 in line_kinds:
        A1, B1, C1 = l1["lineABC"]
        A2, B2, C2 = l2["lineABC"]
        det = A1 * B2 - A2 * B1
        if abs(det) > 1e-11:
            x = (B1 * C2 - B2 * C1) / det
            y = (C1 * A2 - C2 * A1) / det
            if _point_satisfies_ray_or_seg((x, y), l1) and _point_satisfies_ray_or_seg((x, y), l2):
                pts.append((x, y))
    elif (k1 == "ellipse-foci" and k2 in line_kinds) or (k2 == "ellipse-foci" and k1 in line_kinds):
        ell, lin = (l1, l2) if k1 == "ellipse-foci" else (l2, l1)
        cx, cy = ell["center"]
        a, b, rot = ell["a"], ell["b"], ell["angleRad"]
        cosr, sinr = math.cos(rot), math.sin(rot)
        A, B, C = lin["lineABC"]
        # Transform line into ellipse local coords (u, v):
        # x = cx + u*cosr - v*sinr, y = cy + u*sinr + v*cosr
        Au = A * cosr + B * sinr
        Bv = -A * sinr + B * cosr
        Cl = A * cx + B * cy + C
        # Parameterize line (u, v) = (u0, v0) + s*(-Bv, Au)
        n2 = Au * Au + Bv * Bv
        if n2 > 1e-12:
            u0, v0 = -Au * Cl / n2, -Bv * Cl / n2
            du, dv = -Bv, Au
            qa = (du * du) / (a * a) + (dv * dv) / (b * b)
            qb = 2.0 * ((u0 * du) / (a * a) + (v0 * dv) / (b * b))
            qc = (u0 * u0) / (a * a) + (v0 * v0) / (b * b) - 1.0
            disc = qb * qb - 4 * qa * qc
            if disc >= -1e-9 and qa > 1e-12:
                sd = math.sqrt(max(0.0, disc))
                s_vals = [(-qb + sd) / (2 * qa)]
                if sd > 1e-8:
                    s_vals.append((-qb - sd) / (2 * qa))
                for sv in s_vals:
                    u, v = u0 + sv * du, v0 + sv * dv
                    gx = cx + u * cosr - v * sinr
                    gy = cy + u * sinr + v * cosr
                    if _point_satisfies_ray_or_seg((gx, gy), lin):
                        pts.append((gx, gy))
    return [(_clean_float(x), _clean_float(y)) for x, y in pts]


def compute_argand_data(args: argparse.Namespace) -> dict[str, Any]:
    loci = _parse_argand_loci(args.loci or "")
    points: list[dict[str, Any]] = []
    if args.points:
        for idx, chunk in enumerate(args.points.split(";")):
            raw = chunk.strip()
            if not raw:
                continue
            z_sym, z = parse_complex_expr(raw)
            mod_sym = sp.simplify(sp.Abs(z_sym))
            arg_rad = math.atan2(z.imag, z.real)
            points.append(
                {
                    "label": f"z{idx+1}",
                    "expr": raw,
                    "re": _clean_float(z.real),
                    "im": _clean_float(z.imag),
                    "modulus": _clean_float(abs(z)),
                    "modulusExact": str(mod_sym),
                    "argRad": _clean_float(arg_rad),
                    "argDeg": _clean_float(math.degrees(arg_rad)),
                    "complex": _fmt_complex(z.real, z.imag, 4),
                }
            )
    if not loci and not points:
        raise SystemExit("Provide at least one locus (--loci) or point (--points) for argand.")

    intersections: list[dict[str, Any]] = []
    for i in range(len(loci)):
        for j in range(i + 1, len(loci)):
            pts = _intersect_two_loci(loci[i], loci[j])
            for rx, ry in pts:
                intersections.append(
                    {
                        "lociPair": [loci[i]["id"], loci[j]["id"]],
                        "re": rx,
                        "im": ry,
                        "complex": _fmt_complex(rx, ry, 4),
                    }
                )

    return {
        "subcommand": "argand",
        "loci": loci,
        "points": points,
        "intersections": intersections,
    }


def render_argand_static(data: dict[str, Any], out_path: Path, theme: str, dpi: int, title: str | None) -> int:
    t = _apply_mpl_rc(theme)
    palette = SERIES_PALETTE[theme]

    fig = plt.figure(figsize=(11.2, 6.4))
    gs = gridspec.GridSpec(1, 2, width_ratios=[2.55, 1.35], wspace=0.14)
    ax = fig.add_subplot(gs[0, 0])
    ax_info = fig.add_subplot(gs[0, 1])
    _style_2d_axes(ax, t, xlabel="Re(z)", ylabel="Im(z)")

    # Collect bounding box coordinates
    xs, ys = [0.0], [0.0]
    for p in data["points"]:
        xs.append(p["re"])
        ys.append(p["im"])
    for it in data["intersections"]:
        xs.append(it["re"])
        ys.append(it["im"])
    for loc in data["loci"]:
        k = loc["kind"]
        if k == "circle":
            cx, cy = loc["center"]
            r = loc["radius"]
            xs.extend([cx - r, cx + r])
            ys.extend([cy - r, cy + r])
        elif k == "ellipse-foci":
            cx, cy = loc["center"]
            a = loc["a"]
            xs.extend([cx - a, cx + a])
            ys.extend([cy - a, cy + a])
        elif k in ("perp-bisector", "line", "segment"):
            xs.extend([loc["z1"][0], loc["z2"][0]])
            ys.extend([loc["z1"][1], loc["z2"][1]])
        elif k == "ray":
            ox, oy = loc["origin"]
            dx, dy = loc["dir"]
            xs.extend([ox, ox + 3.0 * dx])
            ys.extend([oy, oy + 3.0 * dy])

    cx_mid = (min(xs) + max(xs)) / 2.0
    cy_mid = (min(ys) + max(ys)) / 2.0
    half_span = max(3.5, (max(xs) - min(xs)) * 0.65, (max(ys) - min(ys)) * 0.65)
    xlim = (cx_mid - half_span, cx_mid + half_span)
    ylim = (cy_mid - half_span, cy_mid + half_span)
    ax.set_xlim(*xlim)
    ax.set_ylim(*ylim)
    ax.set_aspect("equal", "box")

    annotations: list[dict[str, Any]] = []
    for idx, loc in enumerate(data["loci"]):
        col = palette[idx % len(palette)]
        k = loc["kind"]
        if k == "circle":
            cx, cy = loc["center"]
            r = loc["radius"]
            ax.add_patch(Circle((cx, cy), r, fill=False, edgecolor=col, linewidth=2.2, label=f"{loc['id']}: {loc['equation']}", zorder=4))
            ax.scatter([cx], [cy], color=col, s=36, zorder=6)
            # Draw radius guide segment at 145 deg (away from standard first-quadrant rays)
            ang = math.radians(145 + idx * 35)
            rx, ry = cx + r * math.cos(ang), cy + r * math.sin(ang)
            ax.plot([cx, rx], [cy, ry], color=col, linestyle=":", linewidth=1.2, zorder=3)
            ax.text((cx + rx) / 2, (cy + ry) / 2, f"r={_fmt_num(r)}", fontsize=7.8, color=col, fontweight="bold", ha="right", va="bottom", zorder=7)
        elif k == "perp-bisector":
            A, B, C = loc["lineABC"]
            _draw_infinite_line_2d(ax, A, B, C, xlim, ylim, color=col, linewidth=2.2, label=f"{loc['id']}: {loc['equation']}", zorder=4)
            z1, z2 = loc["z1"], loc["z2"]
            ax.plot([z1[0], z2[0]], [z1[1], z2[1]], color=col, linestyle=":", linewidth=1.1, alpha=0.7, zorder=2)
            ax.scatter([z1[0], z2[0]], [z1[1], z2[1]], color=col, s=30, zorder=5)
        elif k == "ray":
            ox, oy = loc["origin"]
            dx, dy = loc["dir"]
            ray_len = half_span * 2.8
            ax.plot([ox, ox + ray_len * dx], [oy, oy + ray_len * dy], color=col, linewidth=2.2, label=f"{loc['id']}: {loc['equation']}", zorder=4)
            # Open circle at origin (arg undefined at z0)
            ax.scatter([ox], [oy], facecolor=t["ax_bg"], edgecolor=col, s=55, linewidth=1.8, zorder=7)
            # Horizontal reference + angle arc
            arc_r = half_span * 0.22
            ax.plot([ox, ox + arc_r * 1.4], [oy, oy], color=t["muted"], linestyle="--", linewidth=0.9, zorder=2)
            ang = loc["angleDeg"] % 360
            ax.add_patch(Arc((ox, oy), arc_r * 2, arc_r * 2, angle=0, theta1=0, theta2=ang, color=col, linewidth=1.2, zorder=5))
        elif k == "ellipse-foci":
            cx, cy = loc["center"]
            a, b, rot = loc["a"], loc["b"], loc["angleRad"]
            th = np.linspace(0, 2 * math.pi, 400)
            ex = cx + a * np.cos(th) * math.cos(rot) - b * np.sin(th) * math.sin(rot)
            ey = cy + a * np.cos(th) * math.sin(rot) + b * np.sin(th) * math.cos(rot)
            ax.plot(ex, ey, color=col, linewidth=2.2, label=f"{loc['id']}: {loc['equation']}", zorder=4)
            z1, z2 = loc["z1"], loc["z2"]
            ax.scatter([z1[0], z2[0]], [z1[1], z2[1]], color=t["crimson"], s=45, zorder=6)
        elif k == "line":
            A, B, C = loc["lineABC"]
            _draw_infinite_line_2d(ax, A, B, C, xlim, ylim, color=col, linewidth=2.1, label=f"{loc['id']}: {loc['equation']}", zorder=4)
        elif k == "segment":
            z1, z2 = loc["z1"], loc["z2"]
            ax.plot([z1[0], z2[0]], [z1[1], z2[1]], color=col, linewidth=2.3, label=f"{loc['id']}: {loc['equation']}", zorder=4)
            ax.scatter([z1[0], z2[0]], [z1[1], z2[1]], color=col, s=42, zorder=6)

    for p in data["points"]:
        ax.scatter([p["re"]], [p["im"]], color=t["amber"], edgecolor=t["fig_bg"], s=65, linewidth=1.2, zorder=8)
        annotations.append({"x": p["re"], "y": p["im"], "label": f"{p['label']}: {p['complex']}", "color": t["amber"]})

    for idx, it in enumerate(data["intersections"]):
        ax.scatter([it["re"]], [it["im"]], color=t["crimson"], edgecolor=t["fig_bg"], s=75, marker="D", linewidth=1.1, zorder=9)
        annotations.append({"x": it["re"], "y": it["im"], "label": f"I{idx+1}: {it['complex']}", "color": t["crimson"]})

    _place_non_overlapping_annotations(ax, annotations, t)
    ax.set_title(title or "Argand Complex Plane Loci", fontsize=11.5, fontweight="bold", color=t["text"], pad=10)
    if data["loci"]:
        leg = ax.legend(loc="upper left", fontsize=7.4, frameon=True, framealpha=0.94, facecolor=t["pill_bg"], edgecolor=t["panel_border"])
        leg.set_zorder(15)
        for text in leg.get_texts():
            text.set_color(t["text"])

    rows: list[tuple[str, str, str | None]] = []
    for loc in data["loci"][:4]:
        rows.append((f"LOCUS {loc['id']} ({loc['kind']})", loc["equation"], "primary"))
    for p in data["points"][:3]:
        rows.append((f"POINT {p['label']}", f"{p['complex']}  (|z|={_fmt_num(p['modulus'],3)}, {_fmt_deg(p['argDeg'])} deg)", "amber"))
    if data["intersections"]:
        for idx, it in enumerate(data["intersections"][:4]):
            rows.append((f"INTERSECT {it['lociPair'][0]} n {it['lociPair'][1]}", it["complex"], "crimson"))
    else:
        rows.append(("INTERSECTIONS", "None detected", "muted"))

    _render_info_panel(ax_info, t, "Argand Plane Summary", "Exact Complex Loci & Roots", rows)
    return _save_figure(fig, out_path, dpi)


def render_argand_html(data: dict[str, Any], out_path: Path, theme: str, title: str | None) -> int:
    sidebar = """
    <div class="card">
      <div class="card-title">Live Complex Probe z = x + iy</div>
      <div class="kv-list" id="probeInfo"></div>
    </div>
    <div class="card">
      <div class="card-title">Loci &amp; Exact Intersections</div>
      <div class="kv-list" id="staticInfo"></div>
    </div>
    """
    js_body = f"""
  const DATA = {safe_json_for_script(data)};
  const canvas = document.getElementById("canvas");
  const ctx = canvas.getContext("2d");
  const probeInfo = document.getElementById("probeInfo");
  const staticInfo = document.getElementById("staticInfo");
  const hud = document.getElementById("hud");

  // Compute initial bounds
  const xs = [0], ys = [0];
  DATA.points.forEach(p => {{ xs.push(p.re); ys.push(p.im); }});
  DATA.intersections.forEach(p => {{ xs.push(p.re); ys.push(p.im); }});
  DATA.loci.forEach(l => {{
    if (l.kind === "circle") {{ xs.push(l.center[0] - l.radius, l.center[0] + l.radius); ys.push(l.center[1] - l.radius, l.center[1] + l.radius); }}
    else if (l.kind === "ellipse-foci") {{ xs.push(l.center[0] - l.a, l.center[0] + l.a); ys.push(l.center[1] - l.a, l.center[1] + l.a); }}
    else if (l.z1 && l.z2) {{ xs.push(l.z1[0], l.z2[0]); ys.push(l.z1[1], l.z2[1]); }}
    else if (l.origin) {{ xs.push(l.origin[0], l.origin[0] + 3 * l.dir[0]); ys.push(l.origin[1], l.origin[1] + 3 * l.dir[1]); }}
  }});
  const initPanX = (Math.min(...xs) + Math.max(...xs)) / 2;
  const initPanY = (Math.min(...ys) + Math.max(...ys)) / 2;
  const initHalf = Math.max(4, (Math.max(...xs) - Math.min(...xs)) * 0.7, (Math.max(...ys) - Math.min(...ys)) * 0.7);
  const view = {{ panX: initPanX, panY: initPanY, zoom: 1.0 }};
  const probe = {{ x: initPanX + 1.0, y: initPanY + 1.0, pinned: false }};

  function renderStaticPanel() {{
    const rows = [];
    DATA.loci.forEach(l => rows.push([l.id + " (" + l.kind + ")", l.equation, "accent"]));
    DATA.intersections.forEach((it, i) => rows.push(["Intersection I" + (i + 1), it.complex, "crimson"]));
    DATA.points.forEach(p => rows.push(["Point " + p.label, p.complex + " (|z|=" + fmt(p.modulus, 3) + ")", "amber"]));
    staticInfo.innerHTML = rows.map(r =>
      '<div class="kv"><span class="kv-k">' + r[0] + '</span><span class="kv-v ' + r[2] + '">' + r[1] + '</span></div>'
    ).join("");
  }}

  function renderProbePanel() {{
    const mod = Math.hypot(probe.x, probe.y);
    const argRad = Math.atan2(probe.y, probe.x);
    const argDeg = (argRad * 180) / Math.PI;
    const sign = probe.y >= 0 ? " + " : " - ";
    const rows = [
      ["z (click canvas to " + (probe.pinned ? "unpin" : "pin") + ")", fmt(probe.x, 3) + sign + fmt(Math.abs(probe.y), 3) + "i", "teal"],
      ["Modulus |z|", fmt(mod, 4), "accent"],
      ["Principal arg(z)", fmt(argDeg, 2) + "° (" + fmt(argRad, 3) + " rad)", "violet"]
    ];
    DATA.points.slice(0, 4).forEach(p => {{
      const d = Math.hypot(probe.x - p.re, probe.y - p.im);
      rows.push(["|z - " + p.label + "|", fmt(d, 4), "amber"]);
    }});
    DATA.loci.slice(0, 3).forEach(l => {{
      if (l.kind === "circle") {{
        const d = Math.hypot(probe.x - l.center[0], probe.y - l.center[1]);
        rows.push([l.id + " |z-c| - r", fmt(d - l.radius, 4), ""]);
      }} else if (l.kind === "ellipse-foci") {{
        const d1 = Math.hypot(probe.x - l.z1[0], probe.y - l.z1[1]);
        const d2 = Math.hypot(probe.x - l.z2[0], probe.y - l.z2[1]);
        rows.push([l.id + " |z-F1|+|z-F2|", fmt(d1 + d2, 4) + " (target " + fmt(l.majorSum, 2) + ")", ""]);
      }}
    }});
    probeInfo.innerHTML = rows.map(r =>
      '<div class="kv"><span class="kv-k">' + r[0] + '</span><span class="kv-v ' + r[2] + '">' + r[1] + '</span></div>'
    ).join("");
  }}

  function draw() {{
    const rect = canvas.getBoundingClientRect();
    const w = Math.max(10, rect.width), hpx = Math.max(10, rect.height);
    const dpr = window.devicePixelRatio || 1;
    if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(hpx * dpr)) {{
      canvas.width = Math.round(w * dpr); canvas.height = Math.round(hpx * dpr);
    }}
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const bg = cssVar("--canvas-bg"), gridC = cssVar("--grid"), axisC = cssVar("--axis");
    const pal = [cssVar("--accent"), cssVar("--violet"), cssVar("--teal"), cssVar("--emerald"), cssVar("--amber")];
    const cCrim = cssVar("--crimson"), cAmb = cssVar("--amber"), cTeal = cssVar("--teal");

    ctx.fillStyle = bg; ctx.fillRect(0, 0, w, hpx);
    const halfW = initHalf / view.zoom;
    const scale = Math.min(w, hpx) / (2 * halfW);
    const toSX = x => w / 2 + (x - view.panX) * scale;
    const toSY = y => hpx / 2 - (y - view.panY) * scale;
    const xMin = view.panX - w / (2 * scale), xMax = view.panX + w / (2 * scale);
    const yMin = view.panY - hpx / (2 * scale), yMax = view.panY + hpx / (2 * scale);

    const rawStep = (2 * halfW) / 10;
    const p10 = Math.pow(10, Math.floor(Math.log10(Math.max(1e-6, rawStep))));
    const step = rawStep / p10 < 2 ? p10 : (rawStep / p10 < 5 ? 2 * p10 : 5 * p10);
    ctx.strokeStyle = gridC; ctx.lineWidth = 1; ctx.beginPath();
    for (let x = Math.ceil(xMin / step) * step; x <= xMax; x += step) {{ ctx.moveTo(toSX(x), 0); ctx.lineTo(toSX(x), hpx); }}
    for (let y = Math.ceil(yMin / step) * step; y <= yMax; y += step) {{ ctx.moveTo(0, toSY(y)); ctx.lineTo(w, toSY(y)); }}
    ctx.stroke();

    ctx.strokeStyle = axisC; ctx.lineWidth = 1.4; ctx.beginPath();
    ctx.moveTo(0, toSY(0)); ctx.lineTo(w, toSY(0));
    ctx.moveTo(toSX(0), 0); ctx.lineTo(toSX(0), hpx); ctx.stroke();
    ctx.fillStyle = axisC; ctx.font = "600 11px " + cssVar("--mono");
    ctx.fillText("Re(z)", w - 42, Math.min(hpx - 8, Math.max(16, toSY(0) - 6)));
    ctx.fillText("Im(z)", Math.min(w - 42, Math.max(8, toSX(0) + 6)), 16);

    function drawLineABC(A, B, C, color) {{
      const pts = [];
      const padX = (xMax - xMin) * 0.02 + 1e-6;
      const padY = (yMax - yMin) * 0.02 + 1e-6;
      if (Math.abs(B) > 1e-9) [xMin, xMax].forEach(xv => {{ const yv = -(A * xv + C) / B; if (yv >= yMin - padY && yv <= yMax + padY) pts.push([xv, yv]); }});
      if (Math.abs(A) > 1e-9) [yMin, yMax].forEach(yv => {{ const xv = -(B * yv + C) / A; if (xv >= xMin - padX && xv <= xMax + padX) pts.push([xv, yv]); }});
      let bestA = null, bestB = null, bestD2 = 1e-12;
      for (let i = 0; i < pts.length; i++) {{
        for (let j = i + 1; j < pts.length; j++) {{
          const dx = pts[j][0] - pts[i][0], dy = pts[j][1] - pts[i][1];
          const d2 = dx * dx + dy * dy;
          if (d2 > bestD2) {{ bestD2 = d2; bestA = pts[i]; bestB = pts[j]; }}
        }}
      }}
      if (bestA && bestB) {{
        ctx.strokeStyle = color; ctx.lineWidth = 2.2; ctx.beginPath();
        ctx.moveTo(toSX(bestA[0]), toSY(bestA[1])); ctx.lineTo(toSX(bestB[0]), toSY(bestB[1]));
        ctx.stroke();
      }}
    }}

    DATA.loci.forEach((l, i) => {{
      const col = pal[i % pal.length];
      if (l.kind === "circle") {{
        ctx.strokeStyle = col; ctx.lineWidth = 2.3;
        ctx.beginPath(); ctx.arc(toSX(l.center[0]), toSY(l.center[1]), l.radius * scale, 0, Math.PI * 2); ctx.stroke();
      }} else if (l.kind === "perp-bisector" || l.kind === "line") {{
        drawLineABC(l.lineABC[0], l.lineABC[1], l.lineABC[2], col);
      }} else if (l.kind === "segment") {{
        ctx.strokeStyle = col; ctx.lineWidth = 2.4; ctx.beginPath();
        ctx.moveTo(toSX(l.z1[0]), toSY(l.z1[1])); ctx.lineTo(toSX(l.z2[0]), toSY(l.z2[1])); ctx.stroke();
      }} else if (l.kind === "ray") {{
        const rLen = halfW * 3;
        ctx.strokeStyle = col; ctx.lineWidth = 2.3; ctx.beginPath();
        ctx.moveTo(toSX(l.origin[0]), toSY(l.origin[1]));
        ctx.lineTo(toSX(l.origin[0] + rLen * l.dir[0]), toSY(l.origin[1] + rLen * l.dir[1]));
        ctx.stroke();
        ctx.beginPath(); ctx.arc(toSX(l.origin[0]), toSY(l.origin[1]), 4.5, 0, Math.PI * 2);
        ctx.fillStyle = bg; ctx.fill(); ctx.strokeStyle = col; ctx.lineWidth = 2; ctx.stroke();
      }} else if (l.kind === "ellipse-foci") {{
        ctx.strokeStyle = col; ctx.lineWidth = 2.3; ctx.beginPath();
        const cr = Math.cos(l.angleRad), sr = Math.sin(l.angleRad);
        for (let k = 0; k <= 240; k++) {{
          const th = (k / 240) * Math.PI * 2;
          const gx = l.center[0] + l.a * Math.cos(th) * cr - l.b * Math.sin(th) * sr;
          const gy = l.center[1] + l.a * Math.cos(th) * sr + l.b * Math.sin(th) * cr;
          if (k === 0) ctx.moveTo(toSX(gx), toSY(gy)); else ctx.lineTo(toSX(gx), toSY(gy));
        }}
        ctx.stroke();
      }}
    }});

    // Probe vector from origin
    ctx.save(); ctx.strokeStyle = cTeal; ctx.lineWidth = 1.5; ctx.setLineDash([4, 4]);
    ctx.beginPath(); ctx.moveTo(toSX(0), toSY(0)); ctx.lineTo(toSX(probe.x), toSY(probe.y)); ctx.stroke();
    ctx.restore();
    ctx.beginPath(); ctx.arc(toSX(probe.x), toSY(probe.y), 6, 0, Math.PI * 2);
    ctx.fillStyle = cTeal; ctx.fill(); ctx.strokeStyle = bg; ctx.lineWidth = 1.8; ctx.stroke();

    DATA.points.forEach(p => {{
      ctx.beginPath(); ctx.arc(toSX(p.re), toSY(p.im), 5, 0, Math.PI * 2);
      ctx.fillStyle = cAmb; ctx.fill();
      ctx.fillStyle = cAmb; ctx.font = "600 11px " + cssVar("--sans");
      ctx.fillText(p.label, toSX(p.re) + 7, toSY(p.im) - 6);
    }});
    DATA.intersections.forEach((it, idx) => {{
      ctx.beginPath(); ctx.arc(toSX(it.re), toSY(it.im), 5.5, 0, Math.PI * 2);
      ctx.fillStyle = cCrim; ctx.fill();
      ctx.fillStyle = cCrim; ctx.font = "600 11px " + cssVar("--sans");
      ctx.fillText("I" + (idx + 1), toSX(it.re) + 7, toSY(it.im) - 6);
    }});
    renderProbePanel();
  }}

  let dragging = false, moved = false, lx = 0, ly = 0;
  canvas.addEventListener("pointerdown", e => {{ dragging = true; moved = false; lx = e.clientX; ly = e.clientY; }});
  window.addEventListener("pointerup", () => {{ dragging = false; }});
  canvas.addEventListener("click", e => {{
    if (moved) return;
    const rect = canvas.getBoundingClientRect();
    const scale = Math.min(rect.width, rect.height) / (2 * (initHalf / view.zoom));
    probe.x = view.panX + (e.clientX - rect.left - rect.width / 2) / scale;
    probe.y = view.panY - (e.clientY - rect.top - rect.height / 2) / scale;
    probe.pinned = !probe.pinned;
    draw();
  }});
  window.addEventListener("pointermove", e => {{
    const rect = canvas.getBoundingClientRect();
    const scale = Math.min(rect.width, rect.height) / (2 * (initHalf / view.zoom));
    if (dragging) {{
      const dx = e.clientX - lx, dy = e.clientY - ly;
      if (Math.hypot(dx, dy) > 3) moved = true;
      view.panX -= dx / scale; view.panY += dy / scale;
      lx = e.clientX; ly = e.clientY; draw();
    }} else if (!probe.pinned && e.target === canvas) {{
      probe.x = view.panX + (e.clientX - rect.left - rect.width / 2) / scale;
      probe.y = view.panY - (e.clientY - rect.top - rect.height / 2) / scale;
      hud.textContent = "Probe z = " + fmt(probe.x, 2) + (probe.y >= 0 ? " + " : " - ") + fmt(Math.abs(probe.y), 2) + "i · Click to pin";
      draw();
    }}
  }});
  canvas.addEventListener("wheel", e => {{
    e.preventDefault();
    view.zoom = Math.max(0.25, Math.min(6.0, view.zoom * (e.deltaY > 0 ? 0.9 : 1.11)));
    draw();
  }}, {{ passive: false }});
  document.getElementById("resetViewBtn").addEventListener("click", () => {{
    view.panX = initPanX; view.panY = initPanY; view.zoom = 1.0; probe.pinned = false; draw();
  }});
  onThemeChange = draw;
  window.addEventListener("resize", draw);
  renderStaticPanel();
  draw();
"""
    html_doc = _build_offline_html(
        title=title or "Argand Complex Plane Explorer",
        badge="100% Offline · Complex Plane",
        initial_theme=theme,
        sidebar_html=sidebar,
        hud_hint="Hover to probe z = x + iy · Click to pin · Drag to pan",
        js_body=js_body,
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html_doc, encoding="utf-8")
    return out_path.stat().st_size


# ---------------------------------------------------------------------------
# Subcommand 3: INTEGRAL (Signed Definite Integral vs Geometric Area)
# ---------------------------------------------------------------------------


def _eval_expr_array(expr: sp.Expr, x_sym: sp.Symbol, xs: np.ndarray) -> np.ndarray:
    """Safely evaluate a SymPy expression on a NumPy array, returning NaN at singularities."""
    fn = sp.lambdify(x_sym, expr, modules=["numpy"])
    with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
        try:
            raw = fn(xs)
            arr = np.asarray(raw, dtype=np.complex128)
            if arr.ndim == 0:
                arr = np.full_like(xs, arr.item(), dtype=np.complex128)
            out = np.where(np.abs(arr.imag) < 1e-8, arr.real, np.nan)
            out = np.where(np.isfinite(out), out, np.nan)
            return out.astype(np.float64)
        except Exception:
            out = np.full(len(xs), np.nan, dtype=np.float64)
            for i, xv in enumerate(xs):
                try:
                    c = complex(sp.N(expr.subs(x_sym, float(xv)), 12))
                    if abs(c.imag) < 1e-8 and math.isfinite(c.real):
                        out[i] = float(c.real)
                except Exception:
                    pass
            return out


def _eval_expr_scalar(expr: sp.Expr, x_sym: sp.Symbol, x_val: float, fn: Any = None) -> float:
    with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
        try:
            res = fn(x_val) if fn is not None else float(sp.N(expr.subs(x_sym, x_val), 12))
            c = complex(res)
            if abs(c.imag) < 1e-8 and math.isfinite(c.real):
                return float(c.real)
        except Exception:
            pass
    return float("nan")


def _find_real_roots_in_interval(
    expr: sp.Expr, x_sym: sp.Symbol, lo_sym: sp.Expr, hi_sym: sp.Expr, lo: float, hi: float, open_ends: bool = False
) -> list[tuple[float, sp.Expr | None]]:
    """Combine exact SymPy solveset with dense SciPy brentq scan to find all real roots."""
    candidates: list[tuple[float, sp.Expr | None]] = []

    # 1. Exact symbolic roots via solveset
    try:
        dom = sp.Interval.open(lo_sym, hi_sym) if open_ends else sp.Interval(lo_sym, hi_sym)
        ss = sp.solveset(expr, x_sym, domain=dom)
        if isinstance(ss, sp.FiniteSet):
            for r in ss:
                try:
                    rv = _sym_to_float(r)
                    if (lo + 1e-9 < rv < hi - 1e-9) if open_ends else (lo - 1e-9 <= rv <= hi + 1e-9):
                        candidates.append((_clean_float(rv), sp.simplify(r)))
                except Exception:
                    pass
    except Exception:
        pass

    # 2. Dense numerical scan + brentq refinement
    n_grid = 1800
    xs = np.linspace(lo, hi, n_grid)
    ys = _eval_expr_array(expr, x_sym, xs)
    fn = sp.lambdify(x_sym, expr, modules=["numpy"])

    def _f_scalar(v: float) -> float:
        return _eval_expr_scalar(expr, x_sym, v, fn)

    for i in range(n_grid - 1):
        y0, y1 = ys[i], ys[i + 1]
        if not (math.isfinite(y0) and math.isfinite(y1)):
            continue
        if abs(y0) < 1e-10:
            rv = float(xs[i])
            if (lo + 1e-8 < rv < hi - 1e-8) if open_ends else True:
                candidates.append((_clean_float(rv), None))
        elif y0 * y1 < 0 and abs(y1 - y0) < 1e4:
            try:
                rv = float(sp_optimize.brentq(_f_scalar, float(xs[i]), float(xs[i + 1]), xtol=1e-11))
                if abs(_f_scalar(rv)) < 1e-5:
                    if (lo + 1e-8 < rv < hi - 1e-8) if open_ends else (lo - 1e-8 <= rv <= hi + 1e-8):
                        candidates.append((_clean_float(rv), None))
            except Exception:
                pass
        # Catch even-multiplicity tangent zeros (e.g. x^2 = 0)
        if 0 < i < n_grid - 1 and math.isfinite(ys[i - 1]):
            if abs(y0) < 1e-3 and abs(y0) <= abs(ys[i - 1]) and abs(y0) <= abs(y1):
                try:
                    res = sp_optimize.minimize_scalar(
                        lambda v: abs(_f_scalar(v)), bounds=(float(xs[i - 1]), float(xs[i + 1])), method="bounded"
                    )
                    if res.success and abs(float(res.fun)) < 1e-6:
                        rv = float(res.x)
                        if (lo + 1e-8 < rv < hi - 1e-8) if open_ends else (lo - 1e-8 <= rv <= hi + 1e-8):
                            candidates.append((_clean_float(rv), None))
                except Exception:
                    pass

    # Deduplicate (prefer symbolic exact entry when within 1e-5)
    candidates.sort(key=lambda item: (item[0], 0 if item[1] is not None else 1))
    merged: list[tuple[float, sp.Expr | None]] = []
    for rv, sym_r in candidates:
        if merged and abs(rv - merged[-1][0]) < 1e-5:
            if merged[-1][1] is None and sym_r is not None:
                merged[-1] = (rv, sym_r)
        else:
            merged.append((rv, sym_r))
    return merged


def compute_integral_data(args: argparse.Namespace) -> dict[str, Any]:
    x = sp.Symbol("x", real=True)
    f_expr = safe_parse_expr(args.expr, allowed_symbols={"x": x})
    g_expr = safe_parse_expr(args.expr2, allowed_symbols={"x": x}) if args.expr2 else sp.Integer(0)
    h_expr = sp.simplify(f_expr - g_expr)

    a_sym, a_val = parse_scalar_expr(args.a)
    b_sym, b_val = parse_scalar_expr(args.b)
    lo_val, hi_val = (a_val, b_val) if a_val <= b_val else (b_val, a_val)
    lo_sym, hi_sym = (a_sym, b_sym) if a_val <= b_val else (b_sym, a_sym)

    fn_h = sp.lambdify(x, h_expr, modules=["numpy"])

    def _h_num(v: float) -> float:
        val = _eval_expr_scalar(h_expr, x, v, fn_h)
        return 0.0 if not math.isfinite(val) else val

    # Symbolic antiderivative & signed definite integral
    antideriv: sp.Expr | None = None
    signed_exact: sp.Expr | None = None
    signed_method = "sympy_exact"
    try:
        anti_cand = sp.integrate(h_expr, x)
        if anti_cand is not None and not anti_cand.has(sp.Integral):
            antideriv = sp.simplify(anti_cand)
        res_sym = sp.integrate(h_expr, (x, a_sym, b_sym))
        if res_sym is not None and not res_sym.has(sp.Integral) and not res_sym.has(sp.oo, -sp.oo, sp.zoo, sp.nan):
            num_check = complex(sp.N(res_sym, 15))
            if abs(num_check.imag) < 1e-10 and math.isfinite(num_check.real):
                signed_exact = sp.simplify(res_sym)
                signed_val = _clean_float(float(num_check.real))
            else:
                raise ValueError("Non-real symbolic integral")
        else:
            raise ValueError("Unevaluated symbolic integral")
    except Exception:
        signed_method = "scipy_quad"
        q_val, _ = sp_integrate.quad(_h_num, a_val, b_val, limit=250)
        signed_val = _clean_float(float(q_val))

    # Sign-change roots strictly inside (lo, hi)
    interior_roots = _find_real_roots_in_interval(h_expr, x, lo_sym, hi_sym, lo_val, hi_val, open_ends=True) if hi_val > lo_val + 1e-12 else []

    # Partition [lo, hi] by interior roots for exact geometric area
    pts_num = [lo_val] + [r[0] for r in interior_roots] + [hi_val]
    pts_sym: list[sp.Expr | None] = [lo_sym] + [r[1] for r in interior_roots] + [hi_sym]
    all_sym_pts = all(p is not None for p in pts_sym) and signed_exact is not None

    pos_area = 0.0
    neg_area = 0.0
    geom_sym_acc: sp.Expr | None = sp.Integer(0) if all_sym_pts else None

    for i in range(len(pts_num) - 1):
        p0, p1 = pts_num[i], pts_num[i + 1]
        if p1 - p0 < 1e-12:
            continue
        sub_val_f: float | None = None
        if geom_sym_acc is not None and pts_sym[i] is not None and pts_sym[i + 1] is not None:
            try:
                sub_sym = sp.simplify(sp.integrate(h_expr, (x, pts_sym[i], pts_sym[i + 1])))
                if not sub_sym.has(sp.Integral):
                    sub_val_f = _sym_to_float(sub_sym)
                    geom_sym_acc = sp.simplify(geom_sym_acc + (sub_sym if sub_val_f >= 0 else -sub_sym))
                else:
                    geom_sym_acc = None
            except Exception:
                geom_sym_acc = None
        if sub_val_f is None:
            qv, _ = sp_integrate.quad(_h_num, p0, p1, limit=200)
            sub_val_f = _clean_float(float(qv))
        if sub_val_f >= 0:
            pos_area += sub_val_f
        else:
            neg_area += abs(sub_val_f)

    geom_val = _clean_float(pos_area + neg_area)
    if geom_sym_acc is not None:
        try:
            geom_val = _sym_to_float(geom_sym_acc)
        except Exception:
            geom_sym_acc = None

    fn_f = sp.lambdify(x, f_expr, modules=["numpy"])
    roots_out = []
    for rv, r_sym in interior_roots:
        roots_out.append(
            {
                "x": rv,
                "xExact": str(r_sym) if r_sym is not None else None,
                "y": _clean_float(_eval_expr_scalar(f_expr, x, rv, fn_f)),
            }
        )

    return {
        "subcommand": "integral",
        "expr": str(f_expr),
        "expr2": str(g_expr),
        "diffExpr": str(h_expr),
        "a": _clean_float(a_val),
        "aExact": str(a_sym),
        "b": _clean_float(b_val),
        "bExact": str(b_sym),
        "signedIntegral": _clean_float(signed_val),
        "signedIntegralExact": str(signed_exact) if signed_exact is not None else None,
        "signedMethod": signed_method,
        "geometricArea": _clean_float(geom_val),
        "geometricAreaExact": str(geom_sym_acc) if geom_sym_acc is not None else None,
        "positiveArea": _clean_float(pos_area),
        "negativeArea": _clean_float(neg_area),
        "signChangeRoots": roots_out,
        "antiderivative": str(antideriv) if antideriv is not None else None,
        "_sym": {"x": x, "f": f_expr, "g": g_expr, "h": h_expr},
    }


def render_integral_static(data: dict[str, Any], out_path: Path, theme: str, dpi: int, title: str | None) -> int:
    t = _apply_mpl_rc(theme)
    x = data["_sym"]["x"]
    f_expr, g_expr = data["_sym"]["f"], data["_sym"]["g"]
    lo = min(data["a"], data["b"])
    hi = max(data["a"], data["b"])
    span = max(2.0, hi - lo)
    pad = span * 0.24
    x_min, x_max = lo - pad, hi + pad

    fig = plt.figure(figsize=(11.2, 6.2))
    gs = gridspec.GridSpec(1, 2, width_ratios=[2.55, 1.35], wspace=0.14)
    ax = fig.add_subplot(gs[0, 0])
    ax_info = fig.add_subplot(gs[0, 1])
    _style_2d_axes(ax, t)

    xs_all = np.linspace(x_min, x_max, 900)
    yf_all = _eval_expr_array(f_expr, x, xs_all)
    yg_all = _eval_expr_array(g_expr, x, xs_all)

    xs_in = np.linspace(lo, hi, 700)
    yf_in = _eval_expr_array(f_expr, x, xs_in)
    yg_in = _eval_expr_array(g_expr, x, xs_in)

    # Shade positive (f >= g) in teal and negative (f < g) in crimson
    ax.fill_between(
        xs_in,
        yg_in,
        yf_in,
        where=(yf_in >= yg_in) & np.isfinite(yf_in) & np.isfinite(yg_in),
        interpolate=True,
        color=t["teal"],
        alpha=0.30,
        label=f"Positive (+{_fmt_num(data['positiveArea'], 4)})",
        zorder=2,
    )
    ax.fill_between(
        xs_in,
        yg_in,
        yf_in,
        where=(yf_in < yg_in) & np.isfinite(yf_in) & np.isfinite(yg_in),
        interpolate=True,
        color=t["crimson"],
        alpha=0.28,
        label=f"Negative (-{_fmt_num(data['negativeArea'], 4)})",
        zorder=2,
    )

    ax.plot(xs_all, yf_all, color=t["primary"], linewidth=2.4, label=f"f(x) = {data['expr']}", zorder=4)
    if data["expr2"] != "0":
        ax.plot(xs_all, yg_all, color=t["secondary"], linewidth=2.0, linestyle="--", label=f"g(x) = {data['expr2']}", zorder=4)

    ax.axvline(data["a"], color=t["muted"], linestyle="--", linewidth=1.2, zorder=3)
    ax.axvline(data["b"], color=t["muted"], linestyle="--", linewidth=1.2, zorder=3)

    # Robust y-limits
    finite_y = np.concatenate([yf_all[np.isfinite(yf_all)], yg_all[np.isfinite(yg_all)]])
    if len(finite_y) > 0:
        q_lo, q_hi = float(np.percentile(finite_y, 2)), float(np.percentile(finite_y, 98))
        y_lo = min(q_lo, 0.0)
        y_hi = max(q_hi, 0.0)
        ypad = max(0.5, (y_hi - y_lo) * 0.18)
        ax.set_ylim(y_lo - ypad, y_hi + ypad)
    ax.set_xlim(x_min, x_max)

    annotations: list[dict[str, Any]] = []
    for r in data["signChangeRoots"]:
        ax.scatter([r["x"]], [r["y"]], color=t["amber"], edgecolor=t["fig_bg"], s=60, zorder=7)
        lbl = f"root x={r['xExact']}" if r["xExact"] else f"root x={_fmt_num(r['x'], 3)}"
        annotations.append({"x": r["x"], "y": r["y"], "label": lbl, "color": t["amber"]})
    _place_non_overlapping_annotations(ax, annotations, t)

    t_str = title or f"Definite Integral on [{data['aExact']}, {data['bExact']}]"
    ax.set_title(t_str, fontsize=11.5, fontweight="bold", color=t["text"], pad=10)
    leg = ax.legend(loc="upper right", fontsize=7.8, frameon=True, framealpha=0.94, facecolor=t["pill_bg"], edgecolor=t["panel_border"])
    leg.set_zorder(15)
    for text in leg.get_texts():
        text.set_color(t["text"])

    signed_disp = (
        f"{data['signedIntegralExact']}  (~{_fmt_num(data['signedIntegral'], 5)})"
        if data["signedIntegralExact"] is not None
        else f"{_fmt_num(data['signedIntegral'], 6)} (SciPy)"
    )
    geom_disp = (
        f"{data['geometricAreaExact']}  (~{_fmt_num(data['geometricArea'], 5)})"
        if data["geometricAreaExact"] is not None
        else f"{_fmt_num(data['geometricArea'], 6)}"
    )
    rows: list[tuple[str, str, str | None]] = [
        ("f(x)", data["expr"], "primary"),
        ("g(x)", data["expr2"], "secondary"),
        ("BOUNDS [a, b]", f"[{data['aExact']}, {data['bExact']}]", "text"),
        ("SIGNED INTEGRAL", signed_disp, "teal" if data["signedIntegral"] >= 0 else "crimson"),
        ("GEOMETRIC AREA", geom_disp, "amber"),
        ("POSITIVE / NEGATIVE", f"+{_fmt_num(data['positiveArea'], 4)} / -{_fmt_num(data['negativeArea'], 4)}", "muted"),
        ("CROSSING ROOTS", ", ".join(r["xExact"] or _fmt_num(r["x"], 3) for r in data["signChangeRoots"]) or "None inside (a,b)", "amber"),
    ]
    if data["antiderivative"]:
        rows.append(("ANTIDERIVATIVE", data["antiderivative"], "muted"))
    _render_info_panel(ax_info, t, "SymPy Integral Analysis", "Signed Integral vs Geometric Area", rows)
    return _save_figure(fig, out_path, dpi)


def render_integral_html(data: dict[str, Any], out_path: Path, theme: str, title: str | None) -> int:
    x = data["_sym"]["x"]
    f_js = sympy_to_js(data["_sym"]["f"], "x")
    g_js = sympy_to_js(data["_sym"]["g"], "x")

    lo = min(data["a"], data["b"])
    hi = max(data["a"], data["b"])
    span = max(4.0, hi - lo)
    sl_min = lo - span * 0.8
    sl_max = hi + span * 0.8

    # Fallback dense sample table if sympy_to_js cannot express a special function
    fallback_samples = None
    if f_js is None or g_js is None:
        xs_s = np.linspace(sl_min, sl_max, 1200)
        yf_s = _eval_expr_array(data["_sym"]["f"], x, xs_s)
        yg_s = _eval_expr_array(data["_sym"]["g"], x, xs_s)
        fallback_samples = {
            "xmin": float(sl_min),
            "xmax": float(sl_max),
            "f": [None if not math.isfinite(v) else float(v) for v in yf_s],
            "g": [None if not math.isfinite(v) else float(v) for v in yg_s],
        }

    meta = {
        "expr": data["expr"],
        "expr2": data["expr2"],
        "a": data["a"],
        "b": data["b"],
        "aExact": data["aExact"],
        "bExact": data["bExact"],
        "signedExact": data["signedIntegralExact"],
        "geomExact": data["geometricAreaExact"],
        "slMin": sl_min,
        "slMax": sl_max,
        "fallback": fallback_samples,
    }

    sidebar = f"""
    <div class="card">
      <div class="card-title">Integration Bounds [a, b]</div>
      <div class="control-row">
        <div class="control-head"><span>Lower bound a</span><span class="control-val" id="valA">{_fmt_num(data["a"], 3)}</span></div>
        <input type="range" id="slA" min="{sl_min}" max="{sl_max}" step="{(sl_max - sl_min) / 400:.5f}" value="{data["a"]}">
      </div>
      <div class="control-row">
        <div class="control-head"><span>Upper bound b</span><span class="control-val" id="valB">{_fmt_num(data["b"], 3)}</span></div>
        <input type="range" id="slB" min="{sl_min}" max="{sl_max}" step="{(sl_max - sl_min) / 400:.5f}" value="{data["b"]}">
      </div>
    </div>
    <div class="card">
      <div class="card-title">Live Integral Readout</div>
      <div class="kv-list" id="readout"></div>
    </div>
    """

    f_body = f"return {f_js};" if f_js is not None else "return sampleInterp(META.fallback.f, x);"
    g_body = f"return {g_js};" if g_js is not None else "return sampleInterp(META.fallback.g, x);"

    js_body = f"""
  const META = {safe_json_for_script(meta)};
  function __erf(x) {{
    const s = x >= 0 ? 1 : -1;
    const ax = Math.abs(x);
    const t = 1 / (1 + 0.3275911 * ax);
    const y = 1 - (((((1.061405429 * t - 1.453152027) * t) + 1.421413741) * t - 0.284496736) * t + 0.254829592) * t * Math.exp(-ax * ax);
    return s * y;
  }}
  function sampleInterp(arr, x) {{
    if (!META.fallback) return NaN;
    const u = ((x - META.fallback.xmin) / (META.fallback.xmax - META.fallback.xmin)) * (arr.length - 1);
    const i = Math.floor(u);
    if (i < 0 || i >= arr.length - 1) return NaN;
    const a = arr[i], b = arr[i + 1];
    if (a === null || b === null) return NaN;
    return a + (b - a) * (u - i);
  }}
  function evalF(x) {{ {f_body} }}
  function evalG(x) {{ {g_body} }}

  const state = {{ a: META.a, b: META.b }};
  const canvas = document.getElementById("canvas");
  const ctx = canvas.getContext("2d");
  const readout = document.getElementById("readout");
  const hud = document.getElementById("hud");

  function computeNumeric(a, b) {{
    const signDir = b >= a ? 1 : -1;
    const lo = Math.min(a, b), hi = Math.max(a, b);
    if (hi - lo < 1e-9) return {{ signed: 0, geom: 0, pos: 0, neg: 0, roots: [] }};
    const N = 600;
    const dx = (hi - lo) / N;
    let pos = 0, neg = 0;
    const roots = [];
    let prevX = lo, prevH = evalF(lo) - evalG(lo);
    for (let i = 1; i <= N; i++) {{
      const x1 = lo + i * dx;
      const xm = 0.5 * (prevX + x1);
      const h1 = evalF(x1) - evalG(x1);
      const hm = evalF(xm) - evalG(xm);
      if (Number.isFinite(prevH) && Number.isFinite(hm) && Number.isFinite(h1)) {{
        if (prevH * h1 < 0) {{
          const r = prevX + (x1 - prevX) * (Math.abs(prevH) / (Math.abs(prevH) + Math.abs(h1)));
          roots.push(r);
          const a1 = 0.5 * prevH * (r - prevX);
          const a2 = 0.5 * h1 * (x1 - r);
          if (a1 >= 0) pos += a1; else neg += Math.abs(a1);
          if (a2 >= 0) pos += a2; else neg += Math.abs(a2);
        }} else {{
          const seg = (dx / 6) * (prevH + 4 * hm + h1);
          if (seg >= 0) pos += seg; else neg += Math.abs(seg);
        }}
      }}
      prevX = x1; prevH = h1;
    }}
    return {{ signed: signDir * (pos - neg), geom: pos + neg, pos: pos, neg: neg, roots: roots }};
  }}

  function draw() {{
    const rect = canvas.getBoundingClientRect();
    const w = Math.max(10, rect.width), hpx = Math.max(10, rect.height);
    const dpr = window.devicePixelRatio || 1;
    if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(hpx * dpr)) {{
      canvas.width = Math.round(w * dpr); canvas.height = Math.round(hpx * dpr);
    }}
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const bg = cssVar("--canvas-bg"), gridC = cssVar("--grid"), axisC = cssVar("--axis");
    const cPrim = cssVar("--accent"), cVio = cssVar("--violet"), cTeal = cssVar("--teal");
    const cCrim = cssVar("--crimson"), cAmb = cssVar("--amber");

    ctx.fillStyle = bg; ctx.fillRect(0, 0, w, hpx);
    const xMin = META.slMin, xMax = META.slMax;
    const ys = [];
    for (let i = 0; i <= 300; i++) {{
      const xv = xMin + (xMax - xMin) * (i / 300);
      const fv = evalF(xv), gv = evalG(xv);
      if (Number.isFinite(fv) && Math.abs(fv) < 1e4) ys.push(fv);
      if (Number.isFinite(gv) && Math.abs(gv) < 1e4) ys.push(gv);
    }}
    ys.sort((u, v) => u - v);
    const yLoRaw = ys.length ? Math.min(0, ys[Math.floor(ys.length * 0.02)]) : -2;
    const yHiRaw = ys.length ? Math.max(0, ys[Math.floor(ys.length * 0.98)]) : 2;
    const yPad = Math.max(0.6, (yHiRaw - yLoRaw) * 0.22);
    const yMin = yLoRaw - yPad, yMax = yHiRaw + yPad;

    const padL = 44, padR = 20, padT = 22, padB = 34;
    const toSX = x => padL + ((x - xMin) / (xMax - xMin)) * (w - padL - padR);
    const toSY = y => hpx - padB - ((y - yMin) / (yMax - yMin)) * (hpx - padT - padB);

    // Grid
    ctx.strokeStyle = gridC; ctx.lineWidth = 1; ctx.beginPath();
    for (let i = 0; i <= 8; i++) {{
      const gx = xMin + (xMax - xMin) * (i / 8);
      ctx.moveTo(toSX(gx), padT); ctx.lineTo(toSX(gx), hpx - padB);
    }}
    for (let j = 0; j <= 6; j++) {{
      const gy = yMin + (yMax - yMin) * (j / 6);
      ctx.moveTo(padL, toSY(gy)); ctx.lineTo(w - padR, toSY(gy));
    }}
    ctx.stroke();

    // Axes
    ctx.strokeStyle = axisC; ctx.lineWidth = 1.3; ctx.beginPath();
    if (yMin <= 0 && yMax >= 0) {{ ctx.moveTo(padL, toSY(0)); ctx.lineTo(w - padR, toSY(0)); }}
    if (xMin <= 0 && xMax >= 0) {{ ctx.moveTo(toSX(0), padT); ctx.lineTo(toSX(0), hpx - padB); }}
    ctx.stroke();

    // Shaded area strips
    const lo = Math.min(state.a, state.b), hi = Math.max(state.a, state.b);
    const strips = 360;
    if (hi > lo) {{
      const dx = (hi - lo) / strips;
      for (let i = 0; i < strips; i++) {{
        const x0 = lo + i * dx, x1 = x0 + dx;
        const f0 = evalF(x0), f1 = evalF(x1), g0 = evalG(x0), g1 = evalG(x1);
        if (!Number.isFinite(f0) || !Number.isFinite(f1) || !Number.isFinite(g0) || !Number.isFinite(g1)) continue;
        const pos = ((f0 - g0) + (f1 - g1)) >= 0;
        ctx.fillStyle = pos ? cTeal : cCrim;
        ctx.globalAlpha = 0.28;
        ctx.beginPath();
        ctx.moveTo(toSX(x0), toSY(g0)); ctx.lineTo(toSX(x0), toSY(f0));
        ctx.lineTo(toSX(x1), toSY(f1)); ctx.lineTo(toSX(x1), toSY(g1));
        ctx.closePath(); ctx.fill();
      }}
      ctx.globalAlpha = 1.0;
    }}

    // Curves
    function strokeCurve(fn, col, dash) {{
      ctx.save(); ctx.strokeStyle = col; ctx.lineWidth = 2.4;
      if (dash) ctx.setLineDash(dash);
      ctx.beginPath(); let open = false;
      for (let i = 0; i <= 500; i++) {{
        const xv = xMin + (xMax - xMin) * (i / 500);
        const yv = fn(xv);
        if (!Number.isFinite(yv) || yv < yMin - 10 || yv > yMax + 10) {{ open = false; continue; }}
        if (!open) {{ ctx.moveTo(toSX(xv), toSY(yv)); open = true; }} else ctx.lineTo(toSX(xv), toSY(yv));
      }}
      ctx.stroke(); ctx.restore();
    }}
    strokeCurve(evalF, cPrim, null);
    if (META.expr2 !== "0") strokeCurve(evalG, cVio, [6, 4]);

    // Bound markers
    ctx.save(); ctx.strokeStyle = axisC; ctx.setLineDash([5, 4]); ctx.lineWidth = 1.3;
    [state.a, state.b].forEach(bx => {{
      ctx.beginPath(); ctx.moveTo(toSX(bx), padT); ctx.lineTo(toSX(bx), hpx - padB); ctx.stroke();
    }});
    ctx.restore();

    const calc = computeNumeric(state.a, state.b);
    calc.roots.forEach(rx => {{
      const ry = evalF(rx);
      if (Number.isFinite(ry)) {{
        ctx.beginPath(); ctx.arc(toSX(rx), toSY(ry), 4.5, 0, Math.PI * 2);
        ctx.fillStyle = cAmb; ctx.fill();
      }}
    }});

    const atInit = Math.abs(state.a - META.a) < 1e-4 && Math.abs(state.b - META.b) < 1e-4;
    const rows = [
      ["f(x)", META.expr, "accent"],
      ["g(x)", META.expr2, "violet"],
      ["Bounds [a, b]", "[" + fmt(state.a, 3) + ", " + fmt(state.b, 3) + "]", ""],
      ["Signed ∫ (f - g) dx", (atInit && META.signedExact !== null ? META.signedExact + " (~" + fmt(calc.signed, 4) + ")" : fmt(calc.signed, 5)), calc.signed >= 0 ? "teal" : "crimson"],
      ["Geometric Area ∫|f - g|", (atInit && META.geomExact !== null ? META.geomExact + " (~" + fmt(calc.geom, 4) + ")" : fmt(calc.geom, 5)), "amber"],
      ["Positive / Negative", "+" + fmt(calc.pos, 4) + " / -" + fmt(calc.neg, 4), ""],
      ["Crossing Roots", calc.roots.length ? calc.roots.map(r => fmt(r, 3)).join(", ") : "None inside (a, b)", "amber"]
    ];
    readout.innerHTML = rows.map(r =>
      '<div class="kv"><span class="kv-k">' + r[0] + '</span><span class="kv-v ' + r[2] + '">' + r[1] + '</span></div>'
    ).join("");
  }}

  const slA = document.getElementById("slA"), slB = document.getElementById("slB");
  const valA = document.getElementById("valA"), valB = document.getElementById("valB");
  slA.addEventListener("input", () => {{ state.a = parseFloat(slA.value); valA.textContent = fmt(state.a, 3); draw(); }});
  slB.addEventListener("input", () => {{ state.b = parseFloat(slB.value); valB.textContent = fmt(state.b, 3); draw(); }});
  document.getElementById("resetViewBtn").addEventListener("click", () => {{
    state.a = META.a; state.b = META.b; slA.value = META.a; slB.value = META.b;
    valA.textContent = fmt(META.a, 3); valB.textContent = fmt(META.b, 3); draw();
  }});
  canvas.addEventListener("pointermove", e => {{
    const rect = canvas.getBoundingClientRect();
    const padL = 44, padR = 20;
    const x = META.slMin + ((e.clientX - rect.left - padL) / Math.max(1, rect.width - padL - padR)) * (META.slMax - META.slMin);
    if (x >= META.slMin && x <= META.slMax) {{
      hud.textContent = "x = " + fmt(x, 3) + " · f(x) = " + fmt(evalF(x), 3) + (META.expr2 !== "0" ? " · g(x) = " + fmt(evalG(x), 3) : "");
    }}
  }});
  onThemeChange = draw;
  window.addEventListener("resize", draw);
  draw();
"""
    html_doc = _build_offline_html(
        title=title or f"Definite Integral Explorer · {data['expr']}",
        badge="100% Offline · Calculus Integral",
        initial_theme=theme,
        sidebar_html=sidebar,
        hud_hint="Slide bounds a & b to compare Signed Integral vs Geometric Area",
        js_body=js_body,
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html_doc, encoding="utf-8")
    return out_path.stat().st_size


# ---------------------------------------------------------------------------
# Subcommand 4: VECTOR3D (3D Lines, Planes, Skew Shortest Distance, Offline 3D)
# ---------------------------------------------------------------------------


def _parse_vec3_exact(csv: str) -> tuple[sp.Matrix, list[float]]:
    parts = [p.strip() for p in csv.split(",") if p.strip()]
    if len(parts) != 3:
        raise SystemExit(f"Expected 3 coordinates x,y,z, got: {csv!r}")
    syms: list[sp.Expr] = []
    floats: list[float] = []
    for p in parts:
        es, ef = parse_scalar_expr(p)
        syms.append(es)
        floats.append(_clean_float(ef))
    return sp.Matrix(syms), floats


def _parse_plane_exact(csv: str) -> tuple[sp.Matrix, sp.Expr, list[float], float]:
    parts = [p.strip() for p in csv.split(",") if p.strip()]
    if len(parts) != 4:
        raise SystemExit(f"Expected 4 plane coefficients a,b,c,d for ax+by+cz=d, got: {csv!r}")
    syms, floats = [], []
    for p in parts:
        es, ef = parse_scalar_expr(p)
        syms.append(es)
        floats.append(_clean_float(ef))
    n_sym = sp.Matrix(syms[:3])
    if n_sym.dot(n_sym) == 0:
        raise SystemExit(f"Plane normal vector cannot be zero: {csv!r}")
    return n_sym, syms[3], floats[:3], floats[3]


def _mat_to_float_list(m: sp.Matrix) -> list[float]:
    return [_sym_to_float(el) for el in m]


def _mat_to_exact_list(m: sp.Matrix) -> list[str]:
    return [str(sp.simplify(el)) for el in m]


def compute_vector3d_data(args: argparse.Namespace) -> dict[str, Any]:
    lines: list[dict[str, Any]] = []
    lines_sym: list[tuple[sp.Matrix, sp.Matrix]] = []
    if args.lines:
        for idx, chunk in enumerate(args.lines.split(";")):
            raw = chunk.strip()
            if not raw:
                continue
            if "|" not in raw:
                raise SystemExit(f"Invalid line spec {raw!r}; expected px,py,pz|dx,dy,dz")
            p_str, d_str = raw.split("|", 1)
            p_sym, p_f = _parse_vec3_exact(p_str)
            d_sym, d_f = _parse_vec3_exact(d_str)
            if d_sym.dot(d_sym) == 0:
                raise SystemExit(f"Line direction vector cannot be zero: {raw!r}")
            lines_sym.append((p_sym, d_sym))
            lines.append(
                {
                    "id": f"L{idx+1}",
                    "point": p_f,
                    "direction": d_f,
                    "pointExact": _mat_to_exact_list(p_sym),
                    "directionExact": _mat_to_exact_list(d_sym),
                }
            )

    planes: list[dict[str, Any]] = []
    planes_sym: list[tuple[sp.Matrix, sp.Expr]] = []
    x_s, y_s, z_s = sp.symbols("x y z", real=True)
    if args.planes:
        for idx, chunk in enumerate(args.planes.split(";")):
            raw = chunk.strip()
            if not raw:
                continue
            n_sym, d_sym, n_f, d_f = _parse_plane_exact(raw)
            planes_sym.append((n_sym, d_sym))
            lhs_expr = sp.simplify(n_sym[0] * x_s + n_sym[1] * y_s + n_sym[2] * z_s)
            planes.append(
                {
                    "id": f"Pi{idx+1}",
                    "normal": n_f,
                    "d": d_f,
                    "normalExact": _mat_to_exact_list(n_sym),
                    "dExact": str(sp.simplify(d_sym)),
                    "equation": f"{lhs_expr} = {sp.simplify(d_sym)}",
                }
            )

    points: list[dict[str, Any]] = []
    if args.points:
        for idx, chunk in enumerate(args.points.split(";")):
            raw = chunk.strip()
            if not raw:
                continue
            pt_sym, pt_f = _parse_vec3_exact(raw)
            points.append({"id": f"Q{idx+1}", "coords": pt_f, "exact": _mat_to_exact_list(pt_sym)})

    if not lines and not planes and not points:
        raise SystemExit("Provide at least one --lines, --planes, or --points specification for vector3d.")

    # Pairwise Line-Line analysis (exact SymPy)
    line_pairs: list[dict[str, Any]] = []
    for i in range(len(lines_sym)):
        for j in range(i + 1, len(lines_sym)):
            p1, d1 = lines_sym[i]
            p2, d2 = lines_sym[j]
            n = d1.cross(d2)
            w = p2 - p1
            cos_th = sp.simplify(sp.Abs(d1.dot(d2)) / (d1.norm() * d2.norm()))
            ang_rad = sp.simplify(sp.acos(sp.Min(1, cos_th)))
            ang_deg = _clean_float(math.degrees(_sym_to_float(ang_rad)))

            if n.dot(n) == 0:
                cross_wd = w.cross(d1)
                dist_sym = sp.simplify(cross_wd.norm() / d1.norm())
                if dist_sym == 0:
                    rel = "coincident"
                    feet = None
                else:
                    rel = "parallel"
                    foot1 = p1
                    t_proj = sp.simplify(w.dot(d1) / d1.dot(d1))
                    foot2 = sp.simplify(p2 - t_proj * d1)
                    feet = {
                        "P1": _mat_to_float_list(foot1),
                        "P2": _mat_to_float_list(foot2),
                        "P1Exact": _mat_to_exact_list(foot1),
                        "P2Exact": _mat_to_exact_list(foot2),
                    }
                line_pairs.append(
                    {
                        "pair": [lines[i]["id"], lines[j]["id"]],
                        "relation": rel,
                        "shortestDistance": _sym_to_float(dist_sym),
                        "shortestDistanceExact": str(dist_sym),
                        "feetOfPerpendicular": feet,
                        "angleDeg": ang_deg,
                        "angleExact": str(ang_rad),
                    }
                )
            else:
                dist_sym = sp.simplify(sp.Abs(w.dot(n)) / n.norm())
                # Exact 2x2 normal equations for common perpendicular feet:
                # s*(d1.d1) - t*(d1.d2) = w.d1
                # s*(d1.d2) - t*(d2.d2) = w.d2
                M = sp.Matrix([[d1.dot(d1), -d1.dot(d2)], [d1.dot(d2), -d2.dot(d2)]])
                rhs = sp.Matrix([w.dot(d1), w.dot(d2)])
                st = M.LUsolve(rhs)
                foot1 = sp.simplify(p1 + st[0] * d1)
                foot2 = sp.simplify(p2 + st[1] * d2)
                rel = "intersecting" if dist_sym == 0 else "skew"
                line_pairs.append(
                    {
                        "pair": [lines[i]["id"], lines[j]["id"]],
                        "relation": rel,
                        "shortestDistance": _sym_to_float(dist_sym),
                        "shortestDistanceExact": str(dist_sym),
                        "feetOfPerpendicular": {
                            "P1": _mat_to_float_list(foot1),
                            "P2": _mat_to_float_list(foot2),
                            "P1Exact": _mat_to_exact_list(foot1),
                            "P2Exact": _mat_to_exact_list(foot2),
                        },
                        "intersectionPoint": _mat_to_float_list(foot1) if rel == "intersecting" else None,
                        "angleDeg": ang_deg,
                        "angleExact": str(ang_rad),
                    }
                )

    # Line-Plane analysis (exact SymPy)
    line_planes: list[dict[str, Any]] = []
    for i, (p, d) in enumerate(lines_sym):
        for j, (n, dv) in enumerate(planes_sym):
            nd = sp.simplify(n.dot(d))
            sin_phi = sp.simplify(sp.Abs(nd) / (n.norm() * d.norm()))
            ang_rad = sp.simplify(sp.asin(sp.Min(1, sin_phi)))
            ang_deg = _clean_float(math.degrees(_sym_to_float(ang_rad)))
            if nd != 0:
                lam = sp.simplify((dv - n.dot(p)) / nd)
                inter = sp.simplify(p + lam * d)
                line_planes.append(
                    {
                        "line": lines[i]["id"],
                        "plane": planes[j]["id"],
                        "relation": "intersecting",
                        "intersection": _mat_to_float_list(inter),
                        "intersectionExact": _mat_to_exact_list(inter),
                        "angleDeg": ang_deg,
                        "distance": 0.0,
                        "distanceExact": "0",
                    }
                )
            else:
                dist_sym = sp.simplify(sp.Abs(n.dot(p) - dv) / n.norm())
                rel = "line_in_plane" if dist_sym == 0 else "parallel"
                line_planes.append(
                    {
                        "line": lines[i]["id"],
                        "plane": planes[j]["id"],
                        "relation": rel,
                        "intersection": None,
                        "intersectionExact": None,
                        "angleDeg": 0.0,
                        "distance": _sym_to_float(dist_sym),
                        "distanceExact": str(dist_sym),
                    }
                )

    # Plane-Plane analysis (exact SymPy)
    plane_pairs: list[dict[str, Any]] = []
    for i in range(len(planes_sym)):
        for j in range(i + 1, len(planes_sym)):
            n1, d1 = planes_sym[i]
            n2, d2 = planes_sym[j]
            cos_th = sp.simplify(sp.Abs(n1.dot(n2)) / (n1.norm() * n2.norm()))
            ang_rad = sp.simplify(sp.acos(sp.Min(1, cos_th)))
            ang_deg = _clean_float(math.degrees(_sym_to_float(ang_rad)))
            d_int = sp.simplify(n1.cross(n2))
            if d_int.dot(d_int) != 0:
                # Pick coordinate with max |d_int[k]| and set it to 0 to solve exact 2x2
                d_abs = [abs(_sym_to_float(el)) for el in d_int]
                drop = int(np.argmax(d_abs))
                keep = [c for c in range(3) if c != drop]
                M2 = sp.Matrix([[n1[keep[0]], n1[keep[1]]], [n2[keep[0]], n2[keep[1]]]])
                sol2 = M2.LUsolve(sp.Matrix([d1, d2]))
                p0_list = [sp.Integer(0), sp.Integer(0), sp.Integer(0)]
                p0_list[keep[0]] = sp.simplify(sol2[0])
                p0_list[keep[1]] = sp.simplify(sol2[1])
                p0 = sp.Matrix(p0_list)
                plane_pairs.append(
                    {
                        "pair": [planes[i]["id"], planes[j]["id"]],
                        "relation": "intersecting",
                        "angleDeg": ang_deg,
                        "intersectionLine": {
                            "point": _mat_to_float_list(p0),
                            "direction": _mat_to_float_list(d_int),
                            "pointExact": _mat_to_exact_list(p0),
                            "directionExact": _mat_to_exact_list(d_int),
                        },
                        "distance": 0.0,
                        "distanceExact": "0",
                    }
                )
            else:
                # Parallel or coincident: find non-zero component k so n2 = ratio * n1
                ratio = sp.Integer(1)
                for c in range(3):
                    if n1[c] != 0:
                        ratio = sp.simplify(n2[c] / n1[c])
                        break
                dist_sym = sp.simplify(sp.Abs(d1 - d2 / ratio) / n1.norm())
                rel = "coincident" if dist_sym == 0 else "parallel"
                plane_pairs.append(
                    {
                        "pair": [planes[i]["id"], planes[j]["id"]],
                        "relation": rel,
                        "angleDeg": 0.0,
                        "intersectionLine": None,
                        "distance": _sym_to_float(dist_sym),
                        "distanceExact": str(dist_sym),
                    }
                )

    out: dict[str, Any] = {
        "subcommand": "vector3d",
        "lines": lines,
        "planes": planes,
        "points": points,
        "linePairs": line_pairs,
        "linePlaneRelations": line_planes,
        "planePairs": plane_pairs,
    }
    # Top-level primary convenience properties
    if line_pairs:
        lp0 = line_pairs[0]
        out["relation"] = lp0["relation"]
        out["shortestDistance"] = lp0["shortestDistance"]
        out["shortestDistanceExact"] = lp0["shortestDistanceExact"]
        out["feetOfPerpendicular"] = lp0["feetOfPerpendicular"]
        out["angleDeg"] = lp0["angleDeg"]
    elif line_planes:
        lpl0 = line_planes[0]
        out["relation"] = lpl0["relation"]
        out["intersection"] = lpl0["intersection"]
        out["angleDeg"] = lpl0["angleDeg"]
        out["distance"] = lpl0["distance"]
    elif plane_pairs:
        pp0 = plane_pairs[0]
        out["relation"] = pp0["relation"]
        out["angleDeg"] = pp0["angleDeg"]
        out["intersectionLine"] = pp0["intersectionLine"]
        out["distance"] = pp0["distance"]
    return out


def _plane_basis(normal: list[float], d: float, anchor: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return (center_on_plane, u_unit, v_unit) centered near anchor."""
    n = np.array(normal, dtype=float)
    n_norm2 = float(np.dot(n, n))
    # Project anchor onto plane n.r = d
    center = anchor + ((d - float(np.dot(n, anchor))) / n_norm2) * n
    n_u = n / math.sqrt(n_norm2)
    ref = np.array([0.0, 0.0, 1.0]) if abs(n_u[2]) < 0.85 else np.array([1.0, 0.0, 0.0])
    u = np.cross(n_u, ref)
    u = u / np.linalg.norm(u)
    v = np.cross(n_u, u)
    return center, u, v


def _compute_3d_bounds(data: dict[str, Any]) -> tuple[np.ndarray, float]:
    pts: list[list[float]] = [[0.0, 0.0, 0.0]]
    for ln in data["lines"]:
        pts.append(ln["point"])
    for pt in data["points"]:
        pts.append(pt["coords"])
    for lp in data["linePairs"]:
        if lp.get("feetOfPerpendicular"):
            pts.append(lp["feetOfPerpendicular"]["P1"])
            pts.append(lp["feetOfPerpendicular"]["P2"])
    for lpl in data["linePlaneRelations"]:
        if lpl.get("intersection"):
            pts.append(lpl["intersection"])
    for pp in data["planePairs"]:
        if pp.get("intersectionLine"):
            pts.append(pp["intersectionLine"]["point"])
    arr = np.array(pts, dtype=float)
    center = 0.5 * (arr.min(axis=0) + arr.max(axis=0))
    radius = max(3.2, float(np.max(np.linalg.norm(arr - center, axis=1))) * 1.45)
    return center, radius


def render_vector3d_static(data: dict[str, Any], out_path: Path, theme: str, dpi: int, title: str | None, elev: float, azim: float) -> int:
    t = _apply_mpl_rc(theme)
    palette = SERIES_PALETTE[theme]
    center, radius = _compute_3d_bounds(data)

    fig = plt.figure(figsize=(11.5, 6.6))
    gs = gridspec.GridSpec(1, 2, width_ratios=[2.5, 1.4], wspace=0.10)
    ax = fig.add_subplot(gs[0, 0], projection="3d")
    ax_info = fig.add_subplot(gs[0, 1])

    ax.set_facecolor(t["ax_bg"])
    ax.view_init(elev=elev, azim=azim)
    pane_rgba = matplotlib.colors.to_rgba(t["pane"], alpha=0.85)
    for axis_obj in (ax.xaxis, ax.yaxis, ax.zaxis):
        axis_obj.set_pane_color(pane_rgba)
        axis_obj._axinfo["grid"]["color"] = t["grid"]
        axis_obj._axinfo["grid"]["linewidth"] = 0.6

    ax.set_xlim(center[0] - radius, center[0] + radius)
    ax.set_ylim(center[1] - radius, center[1] + radius)
    ax.set_zlim(center[2] - radius, center[2] + radius)
    ax.set_xlabel("X", fontsize=9.5, fontweight="bold", color=t["text"], labelpad=6)
    ax.set_ylabel("Y", fontsize=9.5, fontweight="bold", color=t["text"], labelpad=6)
    ax.set_zlabel("Z", fontsize=9.5, fontweight="bold", color=t["text"], labelpad=6)
    ax.tick_params(labelsize=7.8, colors=t["muted"])

    # Planes as translucent patches
    plane_colors = [t["teal"], t["secondary"], t["amber"]]
    for idx, pl in enumerate(data["planes"]):
        pc, u, v = _plane_basis(pl["normal"], pl["d"], center)
        s = radius * 0.72
        corners = [pc - s * u - s * v, pc + s * u - s * v, pc + s * u + s * v, pc - s * u + s * v]
        col = plane_colors[idx % len(plane_colors)]
        poly = Poly3DCollection([corners], facecolors=col, edgecolors=col, linewidths=1.2, alpha=0.22)
        ax.add_collection3d(poly)
        ax.text(corners[2][0], corners[2][1], corners[2][2], pl["id"], color=col, fontsize=8.5, fontweight="bold")

    # Lines
    for idx, ln in enumerate(data["lines"]):
        col = palette[idx % len(palette)]
        p = np.array(ln["point"], dtype=float)
        d = np.array(ln["direction"], dtype=float)
        du = d / np.linalg.norm(d)
        # Center segment near bounding box center projection
        t0 = float(np.dot(center - p, du))
        a_pt = p + (t0 - radius * 0.92) * du
        b_pt = p + (t0 + radius * 0.92) * du
        ax.plot([a_pt[0], b_pt[0]], [a_pt[1], b_pt[1]], [a_pt[2], b_pt[2]], color=col, linewidth=2.5, label=ln["id"])
        ax.scatter([p[0]], [p[1]], [p[2]], color=col, s=36)
        lbl_pt = a_pt if idx % 2 == 1 else b_pt
        ax.text(lbl_pt[0], lbl_pt[1], lbl_pt[2], f" {ln['id']}", color=col, fontsize=9, fontweight="bold")

    # Plane-plane intersection line
    for pp in data["planePairs"]:
        il = pp.get("intersectionLine")
        if il:
            p = np.array(il["point"], dtype=float)
            du = np.array(il["direction"], dtype=float)
            du = du / np.linalg.norm(du)
            t0 = float(np.dot(center - p, du))
            a_pt = p + (t0 - radius * 0.85) * du
            b_pt = p + (t0 + radius * 0.85) * du
            ax.plot([a_pt[0], b_pt[0]], [a_pt[1], b_pt[1]], [a_pt[2], b_pt[2]], color=t["crimson"], linewidth=2.4, linestyle="--", label="Plane Intersection")

    # Shortest distance segment & feet
    for lp in data["linePairs"]:
        feet = lp.get("feetOfPerpendicular")
        if feet and lp["relation"] in ("skew", "parallel"):
            p1, p2 = feet["P1"], feet["P2"]
            ax.plot([p1[0], p2[0]], [p1[1], p2[1]], [p1[2], p2[2]], color=t["crimson"], linewidth=3.0, label=f"SD = {lp['shortestDistanceExact']}")
            ax.scatter([p1[0], p2[0]], [p1[1], p2[1]], [p1[2], p2[2]], color=t["crimson"], edgecolor=t["fig_bg"], s=65, depthshade=False)
            ax.text(p1[0], p1[1], p1[2] - radius * 0.12, "P1 ", color=t["crimson"], fontsize=8.5, fontweight="bold", ha="right")
            ax.text(p2[0], p2[1], p2[2] + radius * 0.08, " P2", color=t["crimson"], fontsize=8.5, fontweight="bold", ha="left")
        elif lp["relation"] == "intersecting" and lp.get("intersectionPoint"):
            ip = lp["intersectionPoint"]
            ax.scatter([ip[0]], [ip[1]], [ip[2]], color=t["crimson"], s=75, depthshade=False, label="Intersection")

    for lpl in data["linePlaneRelations"]:
        if lpl.get("intersection"):
            ip = lpl["intersection"]
            ax.scatter([ip[0]], [ip[1]], [ip[2]], color=t["crimson"], s=70, depthshade=False)
            ax.text(ip[0], ip[1], ip[2], "  Int", color=t["crimson"], fontsize=8.5, fontweight="bold")

    ax.set_title(title or "3D Vector Geometry (Lines & Planes)", fontsize=11.5, fontweight="bold", color=t["text"], pad=8)
    leg = ax.legend(loc="upper left", fontsize=7.8, frameon=True, framealpha=0.94, facecolor=t["pill_bg"], edgecolor=t["panel_border"])
    leg.set_zorder(15)
    for text in leg.get_texts():
        text.set_color(t["text"])

    rows: list[tuple[str, str, str | None]] = []
    for ln in data["lines"][:3]:
        rows.append((f"LINE {ln['id']}", f"({','.join(ln['pointExact'])}) + t({','.join(ln['directionExact'])})", "primary"))
    for pl in data["planes"][:2]:
        rows.append((f"PLANE {pl['id']}", pl["equation"], "teal"))
    for lp in data["linePairs"][:2]:
        rows.append((f"{lp['pair'][0]} vs {lp['pair'][1]}", f"{lp['relation'].upper()} · {_fmt_deg(lp['angleDeg'])} deg", "amber"))
        rows.append(("SHORTEST DIST", f"{lp['shortestDistanceExact']}  (~{_fmt_num(lp['shortestDistance'], 4)})", "crimson"))
        if lp.get("feetOfPerpendicular"):
            f = lp["feetOfPerpendicular"]
            rows.append(("FOOT P1 / P2", f"({','.join(f['P1Exact'])}) -> ({','.join(f['P2Exact'])})", "crimson"))
    for lpl in data["linePlaneRelations"][:2]:
        val = f"({','.join(lpl['intersectionExact'])})" if lpl["intersectionExact"] else f"{lpl['relation']} (d={lpl['distanceExact']})"
        rows.append((f"{lpl['line']} n {lpl['plane']}", f"{val} · {_fmt_deg(lpl['angleDeg'])} deg", "crimson"))
    for pp in data["planePairs"][:2]:
        rows.append((f"{pp['pair'][0]} vs {pp['pair'][1]}", f"{pp['relation']} · {_fmt_deg(pp['angleDeg'])} deg", "secondary"))

    _render_info_panel(ax_info, t, "SymPy Exact 3D Geometry", "Skew Distance, Feet & Angles", rows)
    return _save_figure(fig, out_path, dpi)


def render_vector3d_html(data: dict[str, Any], out_path: Path, theme: str, title: str | None) -> int:
    center, radius = _compute_3d_bounds(data)
    scene = {
        "center": [float(c) for c in center],
        "radius": float(radius),
        "lines": data["lines"],
        "planes": [],
        "points": data["points"],
        "linePairs": data["linePairs"],
        "linePlaneRelations": data["linePlaneRelations"],
        "planePairs": data["planePairs"],
    }
    for pl in data["planes"]:
        pc, u, v = _plane_basis(pl["normal"], pl["d"], center)
        scene["planes"].append(
            {
                "id": pl["id"],
                "equation": pl["equation"],
                "center": [float(x) for x in pc],
                "u": [float(x) for x in u],
                "v": [float(x) for x in v],
                "halfSize": float(radius * 0.72),
            }
        )

    sidebar = """
    <div class="card">
      <div class="card-title">Camera Views</div>
      <div class="btn-bar">
        <button class="btn" type="button" data-cam="iso">Isometric</button>
        <button class="btn" type="button" data-cam="xy">XY Top</button>
        <button class="btn" type="button" data-cam="xz">XZ Front</button>
        <button class="btn" type="button" data-cam="yz">YZ Side</button>
        <button class="btn" type="button" id="spinBtn">Auto-Spin</button>
      </div>
    </div>
    <div class="card">
      <div class="card-title">Display Layers</div>
      <div class="toggles">
        <label class="toggle-item"><input type="checkbox" id="chkAxes" checked> Axes</label>
        <label class="toggle-item"><input type="checkbox" id="chkGrid" checked> Floor Grid</label>
        <label class="toggle-item"><input type="checkbox" id="chkPlanes" checked> Planes</label>
        <label class="toggle-item"><input type="checkbox" id="chkLabels" checked> Labels</label>
      </div>
    </div>
    <div class="card">
      <div class="card-title">Exact SymPy 3D Readout</div>
      <div class="kv-list" id="readout"></div>
    </div>
    """

    js_body = f"""
  const SCENE = {safe_json_for_script(scene)};
  const canvas = document.getElementById("canvas");
  const ctx = canvas.getContext("2d");
  const readout = document.getElementById("readout");
  const hud = document.getElementById("hud");

  const cam = {{ yaw: -0.78, pitch: 0.42, zoom: 1.0, spin: false }};
  const layers = {{ axes: true, grid: true, planes: true, labels: true }};
  const MAX_PITCH = (85 * Math.PI) / 180;

  function renderReadout() {{
    const rows = [];
    SCENE.lines.forEach(l => rows.push(["Line " + l.id, "(" + l.pointExact.join(",") + ") + t(" + l.directionExact.join(",") + ")", "accent"]));
    SCENE.planes.forEach(p => rows.push(["Plane " + p.id, p.equation, "teal"]));
    SCENE.linePairs.forEach(lp => {{
      rows.push([lp.pair[0] + " vs " + lp.pair[1], lp.relation.toUpperCase() + " · " + fmt(lp.angleDeg, 2) + "°", "amber"]);
      rows.push(["Shortest Distance", lp.shortestDistanceExact + " (~" + fmt(lp.shortestDistance, 4) + ")", "crimson"]);
      if (lp.feetOfPerpendicular) {{
        rows.push(["Foot P1", "(" + lp.feetOfPerpendicular.P1Exact.join(", ") + ")", "crimson"]);
        rows.push(["Foot P2", "(" + lp.feetOfPerpendicular.P2Exact.join(", ") + ")", "crimson"]);
      }}
    }});
    SCENE.linePlaneRelations.forEach(lp => {{
      const v = lp.intersectionExact ? "(" + lp.intersectionExact.join(", ") + ")" : (lp.relation + ", d=" + lp.distanceExact);
      rows.push([lp.line + " ∩ " + lp.plane, v + " · " + fmt(lp.angleDeg, 2) + "°", "crimson"]);
    }});
    SCENE.planePairs.forEach(pp => {{
      rows.push([pp.pair[0] + " vs " + pp.pair[1], pp.relation + " · " + fmt(pp.angleDeg, 2) + "°", "violet"]);
      if (pp.intersectionLine) {{
        rows.push(["Intersection Line", "(" + pp.intersectionLine.pointExact.join(",") + ") + t(" + pp.intersectionLine.directionExact.join(",") + ")", "crimson"]);
      }}
    }});
    readout.innerHTML = rows.map(r =>
      '<div class="kv"><span class="kv-k">' + r[0] + '</span><span class="kv-v ' + r[2] + '">' + r[1] + '</span></div>'
    ).join("");
  }}

  function projectPoint(pt, w, hpx) {{
    const cx = SCENE.center[0], cy = SCENE.center[1], cz = SCENE.center[2];
    const x = pt[0] - cx, y = pt[1] - cy, z = pt[2] - cz;
    // Z-up turntable: yaw around Z, then pitch around X
    const cyaw = Math.cos(cam.yaw), syaw = Math.sin(cam.yaw);
    const x1 = x * cyaw - y * syaw;
    const y1 = x * syaw + y * cyaw;
    const cp = Math.cos(cam.pitch), sp = Math.sin(cam.pitch);
    const zRot = y1 * cp - z * sp; // depth away from viewer (smaller/negative closer)
    const yScreen = y1 * sp + z * cp;
    const camDist = SCENE.radius * 3.8;
    const baseScale = Math.min(w, hpx) * 0.42 * cam.zoom / Math.max(1e-6, SCENE.radius);
    const persp = camDist / Math.max(0.2, camDist - zRot);
    return {{
      sx: w / 2 + x1 * baseScale * persp,
      sy: hpx / 2 - yScreen * baseScale * persp,
      depth: zRot,
      persp: persp
    }};
  }}

  function draw() {{
    const rect = canvas.getBoundingClientRect();
    const w = Math.max(10, rect.width), hpx = Math.max(10, rect.height);
    const dpr = window.devicePixelRatio || 1;
    if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(hpx * dpr)) {{
      canvas.width = Math.round(w * dpr); canvas.height = Math.round(hpx * dpr);
    }}
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const bg = cssVar("--canvas-bg"), gridC = cssVar("--grid"), axisC = cssVar("--axis");
    const pal = [cssVar("--accent"), cssVar("--violet"), cssVar("--emerald"), cssVar("--amber")];
    const planePal = [cssVar("--teal"), cssVar("--violet"), cssVar("--amber")];
    const cCrim = cssVar("--crimson"), cTxt = cssVar("--text");

    ctx.fillStyle = bg; ctx.fillRect(0, 0, w, hpx);
    const R = SCENE.radius;
    const prims = [];

    // Floor grid on z = center.z - R*0.45 (or z = 0 if inside view)
    if (layers.grid) {{
      const gz = Math.abs(SCENE.center[2]) < R ? 0 : SCENE.center[2] - R * 0.4;
      const gSpan = R * 0.85;
      const nCells = 6;
      for (let i = -nCells; i <= nCells; i++) {{
        const off = (i / nCells) * gSpan;
        const a1 = projectPoint([SCENE.center[0] + off, SCENE.center[1] - gSpan, gz], w, hpx);
        const b1 = projectPoint([SCENE.center[0] + off, SCENE.center[1] + gSpan, gz], w, hpx);
        prims.push({{ kind: "seg", depth: (a1.depth + b1.depth) / 2 - 2, a: a1, b: b1, color: gridC, lw: 1, dash: null }});
        const a2 = projectPoint([SCENE.center[0] - gSpan, SCENE.center[1] + off, gz], w, hpx);
        const b2 = projectPoint([SCENE.center[0] + gSpan, SCENE.center[1] + off, gz], w, hpx);
        prims.push({{ kind: "seg", depth: (a2.depth + b2.depth) / 2 - 2, a: a2, b: b2, color: gridC, lw: 1, dash: null }});
      }}
    }}

    // Axes triad through origin
    if (layers.axes) {{
      const axLen = R * 0.95;
      const o = projectPoint([0, 0, 0], w, hpx);
      const axSpecs = [
        [[axLen, 0, 0], "#ef4444", "X"],
        [[0, axLen, 0], "#10b981", "Y"],
        [[0, 0, axLen], "#3b82f6", "Z"]
      ];
      axSpecs.forEach(sp => {{
        const end = projectPoint(sp[0], w, hpx);
        prims.push({{ kind: "seg", depth: (o.depth + end.depth) / 2, a: o, b: end, color: sp[1], lw: 1.8, dash: null }});
        if (layers.labels) prims.push({{ kind: "label", depth: end.depth + 0.1, p: end, text: sp[2], color: sp[1] }});
      }});
    }}

    // Subdivided plane quads for clean back-to-front painter's sort
    if (layers.planes) {{
      const subN = 5;
      SCENE.planes.forEach((pl, pi) => {{
        const col = planePal[pi % planePal.length];
        const hs = pl.halfSize;
        for (let ix = 0; ix < subN; ix++) {{
          for (let iy = 0; iy < subN; iy++) {{
            const u0 = -hs + (2 * hs * ix) / subN, u1 = -hs + (2 * hs * (ix + 1)) / subN;
            const v0 = -hs + (2 * hs * iy) / subN, v1 = -hs + (2 * hs * (iy + 1)) / subN;
            const corners3 = [
              [u0, v0], [u1, v0], [u1, v1], [u0, v1]
            ].map(uv => [
              pl.center[0] + uv[0] * pl.u[0] + uv[1] * pl.v[0],
              pl.center[1] + uv[0] * pl.u[1] + uv[1] * pl.v[1],
              pl.center[2] + uv[0] * pl.u[2] + uv[1] * pl.v[2]
            ]);
            const pts2 = corners3.map(c => projectPoint(c, w, hpx));
            const avgD = (pts2[0].depth + pts2[1].depth + pts2[2].depth + pts2[3].depth) / 4;
            prims.push({{ kind: "quad", depth: avgD, pts: pts2, color: col }});
          }}
        }}
      }});
    }}

    // Lines split into segments for accurate depth sorting around planes
    const nSegs = 12;
    SCENE.lines.forEach((ln, li) => {{
      const col = pal[li % pal.length];
      const d = ln.direction;
      const dn = Math.hypot(d[0], d[1], d[2]) || 1;
      const du = [d[0] / dn, d[1] / dn, d[2] / dn];
      const t0 = (SCENE.center[0] - ln.point[0]) * du[0] + (SCENE.center[1] - ln.point[1]) * du[1] + (SCENE.center[2] - ln.point[2]) * du[2];
      const tA = t0 - R * 0.95, tB = t0 + R * 0.95;
      for (let k = 0; k < nSegs; k++) {{
        const s0 = tA + ((tB - tA) * k) / nSegs;
        const s1 = tA + ((tB - tA) * (k + 1)) / nSegs;
        const pA = projectPoint([ln.point[0] + s0 * du[0], ln.point[1] + s0 * du[1], ln.point[2] + s0 * du[2]], w, hpx);
        const pB = projectPoint([ln.point[0] + s1 * du[0], ln.point[1] + s1 * du[1], ln.point[2] + s1 * du[2]], w, hpx);
        prims.push({{ kind: "seg", depth: (pA.depth + pB.depth) / 2, a: pA, b: pB, color: col, lw: 2.8, dash: null }});
      }}
      const endP = projectPoint([ln.point[0] + tB * du[0], ln.point[1] + tB * du[1], ln.point[2] + tB * du[2]], w, hpx);
      if (layers.labels) prims.push({{ kind: "label", depth: endP.depth + 0.15, p: endP, text: ln.id, color: col }});
    }});

    // Shortest distance segments & intersection dots
    SCENE.linePairs.forEach(lp => {{
      const f = lp.feetOfPerpendicular;
      if (f && (lp.relation === "skew" || lp.relation === "parallel")) {{
        const a = projectPoint(f.P1, w, hpx), b = projectPoint(f.P2, w, hpx);
        prims.push({{ kind: "seg", depth: (a.depth + b.depth) / 2 + 0.05, a: a, b: b, color: cCrim, lw: 3.4, dash: null }});
        prims.push({{ kind: "dot", depth: a.depth + 0.1, p: a, color: cCrim, r: 5.5 }});
        prims.push({{ kind: "dot", depth: b.depth + 0.1, p: b, color: cCrim, r: 5.5 }});
        if (layers.labels) {{
          prims.push({{ kind: "label", depth: a.depth + 0.2, p: a, text: "P1", color: cCrim }});
          prims.push({{ kind: "label", depth: b.depth + 0.2, p: b, text: "P2", color: cCrim }});
          const mid = projectPoint([(f.P1[0]+f.P2[0])/2, (f.P1[1]+f.P2[1])/2, (f.P1[2]+f.P2[2])/2], w, hpx);
          prims.push({{ kind: "label", depth: mid.depth + 0.2, p: mid, text: "SD=" + lp.shortestDistanceExact, color: cCrim }});
        }}
      }} else if (lp.relation === "intersecting" && lp.intersectionPoint) {{
        const ip = projectPoint(lp.intersectionPoint, w, hpx);
        prims.push({{ kind: "dot", depth: ip.depth + 0.1, p: ip, color: cCrim, r: 6 }});
        if (layers.labels) prims.push({{ kind: "label", depth: ip.depth + 0.2, p: ip, text: "Intersection", color: cCrim }});
      }}
    }});

    SCENE.linePlaneRelations.forEach(lp => {{
      if (lp.intersection) {{
        const ip = projectPoint(lp.intersection, w, hpx);
        prims.push({{ kind: "dot", depth: ip.depth + 0.1, p: ip, color: cCrim, r: 5.5 }});
        if (layers.labels) prims.push({{ kind: "label", depth: ip.depth + 0.2, p: ip, text: "Int", color: cCrim }});
      }}
    }});

    // Back-to-front painter's sort (smaller depth is farther back)
    prims.sort((u, v) => u.depth - v.depth);

    for (const pr of prims) {{
      if (pr.kind === "quad") {{
        ctx.save();
        ctx.beginPath();
        ctx.moveTo(pr.pts[0].sx, pr.pts[0].sy);
        ctx.lineTo(pr.pts[1].sx, pr.pts[1].sy);
        ctx.lineTo(pr.pts[2].sx, pr.pts[2].sy);
        ctx.lineTo(pr.pts[3].sx, pr.pts[3].sy);
        ctx.closePath();
        ctx.fillStyle = pr.color; ctx.globalAlpha = 0.20; ctx.fill();
        ctx.strokeStyle = pr.color; ctx.globalAlpha = 0.36; ctx.lineWidth = 0.8; ctx.stroke();
        ctx.restore();
      }} else if (pr.kind === "seg") {{
        ctx.save();
        ctx.strokeStyle = pr.color; ctx.lineWidth = pr.lw; ctx.lineCap = "round";
        if (pr.dash) ctx.setLineDash(pr.dash);
        ctx.beginPath(); ctx.moveTo(pr.a.sx, pr.a.sy); ctx.lineTo(pr.b.sx, pr.b.sy); ctx.stroke();
        ctx.restore();
      }} else if (pr.kind === "dot") {{
        ctx.beginPath(); ctx.arc(pr.p.sx, pr.p.sy, pr.r, 0, Math.PI * 2);
        ctx.fillStyle = pr.color; ctx.fill();
        ctx.strokeStyle = bg; ctx.lineWidth = 1.6; ctx.stroke();
      }} else if (pr.kind === "label") {{
        ctx.font = "700 12px " + cssVar("--sans");
        ctx.fillStyle = pr.color || cTxt;
        ctx.fillText(pr.text, pr.p.sx + 7, pr.p.sy - 5);
      }}
    }}
  }}

  // Drag orbit & zoom
  let dragging = false, lx = 0, ly = 0;
  canvas.addEventListener("pointerdown", e => {{ dragging = true; lx = e.clientX; ly = e.clientY; }});
  window.addEventListener("pointerup", () => {{ dragging = false; }});
  window.addEventListener("pointermove", e => {{
    if (!dragging) return;
    cam.yaw += (e.clientX - lx) * 0.009;
    cam.pitch = Math.max(-MAX_PITCH, Math.min(MAX_PITCH, cam.pitch + (e.clientY - ly) * 0.009));
    lx = e.clientX; ly = e.clientY;
    hud.textContent = "Yaw " + fmt((cam.yaw * 180) / Math.PI, 0) + "° · Pitch " + fmt((cam.pitch * 180) / Math.PI, 0) + "° · Drag to rotate, scroll to zoom";
    draw();
  }});
  canvas.addEventListener("wheel", e => {{
    e.preventDefault();
    cam.zoom = Math.max(0.4, Math.min(4.0, cam.zoom * (e.deltaY > 0 ? 0.92 : 1.09)));
    draw();
  }}, {{ passive: false }});

  document.querySelectorAll("button[data-cam]").forEach(btn => {{
    btn.addEventListener("click", () => {{
      const m = btn.getAttribute("data-cam");
      if (m === "iso") {{ cam.yaw = -0.78; cam.pitch = 0.42; }}
      else if (m === "xy") {{ cam.yaw = 0; cam.pitch = -MAX_PITCH; }}
      else if (m === "xz") {{ cam.yaw = 0; cam.pitch = 0; }}
      else if (m === "yz") {{ cam.yaw = Math.PI / 2; cam.pitch = 0; }}
      draw();
    }});
  }});
  const spinBtn = document.getElementById("spinBtn");
  spinBtn.addEventListener("click", () => {{
    cam.spin = !cam.spin; spinBtn.classList.toggle("active", cam.spin);
  }});
  function bindChk(id, key) {{
    const el = document.getElementById(id);
    if (el) el.addEventListener("change", () => {{ layers[key] = el.checked; draw(); }});
  }}
  bindChk("chkAxes", "axes");
  bindChk("chkGrid", "grid");
  bindChk("chkPlanes", "planes");
  bindChk("chkLabels", "labels");

  document.getElementById("resetViewBtn").addEventListener("click", () => {{
    cam.yaw = -0.78; cam.pitch = 0.42; cam.zoom = 1.0; cam.spin = false;
    spinBtn.classList.remove("active"); draw();
  }});
  onThemeChange = draw;
  window.addEventListener("resize", draw);
  renderReadout();
  draw();
  (function tick() {{
    if (cam.spin && !dragging) {{ cam.yaw += 0.008; draw(); }}
    requestAnimationFrame(tick);
  }})();
"""
    html_doc = _build_offline_html(
        title=title or "3D Vector Geometry Interactive Lab",
        badge="100% Offline · Canvas 3D",
        initial_theme=theme,
        sidebar_html=sidebar,
        hud_hint="Drag to rotate in 3D · Scroll to zoom · Exact SymPy skew/plane geometry",
        js_body=js_body,
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html_doc, encoding="utf-8")
    return out_path.stat().st_size


# ---------------------------------------------------------------------------
# Subcommand 5: FUNC (Multi-Function Calculus Analyzer & Plotter)
# ---------------------------------------------------------------------------


def compute_func_data(args: argparse.Namespace) -> dict[str, Any]:
    x = sp.Symbol("x", real=True)
    xmin_sym, xmin_val = parse_scalar_expr(args.xmin)
    xmax_sym, xmax_val = parse_scalar_expr(args.xmax)
    if xmax_val <= xmin_val:
        raise SystemExit(f"--xmax ({xmax_val}) must be strictly greater than --xmin ({xmin_val}).")

    ymin_val = parse_scalar_expr(args.ymin)[1] if args.ymin is not None else None
    ymax_val = parse_scalar_expr(args.ymax)[1] if args.ymax is not None else None

    raw_exprs = [s.strip() for s in args.exprs.split(";") if s.strip()]
    if not raw_exprs:
        raise SystemExit("Provide at least one expression in --exprs.")

    funcs_out: list[dict[str, Any]] = []
    sym_objs: list[dict[str, Any]] = []

    for raw in raw_exprs:
        f = safe_parse_expr(raw, allowed_symbols={"x": x})
        f_simp = sp.simplify(f)
        df = sp.simplify(sp.diff(f_simp, x))
        d2f = sp.simplify(sp.diff(df, x))

        fn_f = sp.lambdify(x, f_simp, modules=["numpy"])
        fn_df = sp.lambdify(x, df, modules=["numpy"])
        fn_d2f = sp.lambdify(x, d2f, modules=["numpy"])

        # Detect vertical asymptotes / denominator zeros
        disconts: list[float] = []
        try:
            _, den = sp.fraction(sp.together(f_simp))
            if den != 1:
                for rv, _ in _find_real_roots_in_interval(den, x, xmin_sym, xmax_sym, xmin_val, xmax_val, open_ends=False):
                    disconts.append(rv)
        except Exception:
            pass

        # Roots f(x) = 0
        roots_list: list[dict[str, Any]] = []
        if args.mark_roots:
            for rv, r_sym in _find_real_roots_in_interval(f_simp, x, xmin_sym, xmax_sym, xmin_val, xmax_val):
                if any(abs(rv - d) < 1e-4 for d in disconts):
                    continue
                roots_list.append(
                    {
                        "x": rv,
                        "y": 0.0,
                        "xExact": str(r_sym) if r_sym is not None else None,
                    }
                )

        # Extrema f'(x) = 0
        extrema_list: list[dict[str, Any]] = []
        if args.mark_extrema and df != 0:
            eps_h = max(1e-4, (xmax_val - xmin_val) * 1e-4)
            for rv, r_sym in _find_real_roots_in_interval(df, x, xmin_sym, xmax_sym, xmin_val, xmax_val):
                if any(abs(rv - d) < 1e-4 for d in disconts):
                    continue
                yv = _eval_expr_scalar(f_simp, x, rv, fn_f)
                if not math.isfinite(yv):
                    continue
                y_sym = sp.simplify(f_simp.subs(x, r_sym)) if r_sym is not None else None
                d2v = _eval_expr_scalar(d2f, x, rv, fn_d2f)
                if math.isfinite(d2v) and abs(d2v) > 1e-7:
                    kind = "local_min" if d2v > 0 else "local_max"
                else:
                    # First-derivative sign change test (catches x^4 at 0 as local_min, x^3 as saddle)
                    dl = _eval_expr_scalar(df, x, rv - eps_h, fn_df)
                    dr = _eval_expr_scalar(df, x, rv + eps_h, fn_df)
                    if math.isfinite(dl) and math.isfinite(dr):
                        if dl < 0 and dr > 0:
                            kind = "local_min"
                        elif dl > 0 and dr < 0:
                            kind = "local_max"
                        else:
                            kind = "saddle"
                    else:
                        kind = "stationary"
                extrema_list.append(
                    {
                        "x": rv,
                        "y": _clean_float(yv),
                        "xExact": str(r_sym) if r_sym is not None else None,
                        "yExact": str(y_sym) if y_sym is not None else None,
                        "kind": kind,
                    }
                )

        # Inflection points f''(x) = 0 with verified sign change of f''
        infl_list: list[dict[str, Any]] = []
        if args.mark_inflection and d2f != 0:
            eps_h = max(1e-4, (xmax_val - xmin_val) * 1e-4)
            for rv, r_sym in _find_real_roots_in_interval(d2f, x, xmin_sym, xmax_sym, xmin_val, xmax_val):
                if any(abs(rv - d) < 1e-4 for d in disconts):
                    continue
                # Verify concavity actually changes sign across rv (prevents false inflection on x^4!)
                c_left = _eval_expr_scalar(d2f, x, rv - eps_h, fn_d2f)
                c_right = _eval_expr_scalar(d2f, x, rv + eps_h, fn_d2f)
                if not (math.isfinite(c_left) and math.isfinite(c_right) and c_left * c_right < 0):
                    continue
                yv = _eval_expr_scalar(f_simp, x, rv, fn_f)
                if not math.isfinite(yv):
                    continue
                y_sym = sp.simplify(f_simp.subs(x, r_sym)) if r_sym is not None else None
                infl_list.append(
                    {
                        "x": rv,
                        "y": _clean_float(yv),
                        "xExact": str(r_sym) if r_sym is not None else None,
                        "yExact": str(y_sym) if y_sym is not None else None,
                    }
                )

        funcs_out.append(
            {
                "expr": raw,
                "simplified": str(f_simp),
                "derivative": str(df),
                "secondDerivative": str(d2f),
                "roots": roots_list,
                "extrema": extrema_list,
                "inflectionPoints": infl_list,
                "discontinuities": disconts,
            }
        )
        sym_objs.append({"f": f_simp, "df": df, "d2f": d2f})

    out: dict[str, Any] = {
        "subcommand": "func",
        "xmin": _clean_float(xmin_val),
        "xmax": _clean_float(xmax_val),
        "ymin": ymin_val,
        "ymax": ymax_val,
        "functions": funcs_out,
        "_sym": {"x": x, "items": sym_objs},
        "_flags": {
            "markRoots": bool(args.mark_roots),
            "markExtrema": bool(args.mark_extrema),
            "markInflection": bool(args.mark_inflection),
        },
    }
    if len(funcs_out) == 1:
        out["roots"] = funcs_out[0]["roots"]
        out["extrema"] = funcs_out[0]["extrema"]
        out["inflectionPoints"] = funcs_out[0]["inflectionPoints"]
    return out


def render_func_static(data: dict[str, Any], out_path: Path, theme: str, dpi: int, title: str | None) -> int:
    t = _apply_mpl_rc(theme)
    palette = SERIES_PALETTE[theme]
    x = data["_sym"]["x"]
    xmin, xmax = data["xmin"], data["xmax"]

    fig = plt.figure(figsize=(11.4, 6.4))
    gs = gridspec.GridSpec(1, 2, width_ratios=[2.55, 1.38], wspace=0.14)
    ax = fig.add_subplot(gs[0, 0])
    ax_info = fig.add_subplot(gs[0, 1])
    _style_2d_axes(ax, t)

    xs = np.linspace(xmin, xmax, 1200)
    all_finite_y: list[np.ndarray] = []

    for idx, (fmeta, sym_it) in enumerate(zip(data["functions"], data["_sym"]["items"])):
        col = palette[idx % len(palette)]
        ys = _eval_expr_array(sym_it["f"], x, xs)
        # Break plot at steep vertical-asymptote jumps
        finite_mask = np.isfinite(ys)
        if np.any(finite_mask):
            p5, p95 = np.percentile(ys[finite_mask], [3, 97])
            spread = max(5.0, float(p95 - p5))
            ys_plot = ys.copy()
            jumps = np.abs(np.diff(ys_plot)) > spread * 3.5
            ys_plot[:-1][jumps] = np.nan
            ys_plot[1:][jumps] = np.nan
            all_finite_y.append(ys_plot[np.isfinite(ys_plot)])
        else:
            ys_plot = ys
        ax.plot(xs, ys_plot, color=col, linewidth=2.4, label=f"f{idx+1}(x) = {fmeta['simplified']}", zorder=4)
        for dv in fmeta["discontinuities"]:
            if xmin < dv < xmax:
                ax.axvline(dv, color=t["crimson"], linestyle=":", linewidth=1.2, alpha=0.8, zorder=2)

    ax.set_xlim(xmin, xmax)
    if data["ymin"] is not None and data["ymax"] is not None:
        ax.set_ylim(data["ymin"], data["ymax"])
    elif all_finite_y:
        cat = np.concatenate(all_finite_y)
        if len(cat) > 0:
            lo_q, hi_q = float(np.percentile(cat, 2)), float(np.percentile(cat, 98))
            # Include all extrema & roots cleanly in y-range
            for fm in data["functions"]:
                for pt in fm["extrema"] + fm["inflectionPoints"] + fm["roots"]:
                    if math.isfinite(pt["y"]):
                        lo_q = min(lo_q, pt["y"])
                        hi_q = max(hi_q, pt["y"])
            ypad = max(0.8, (hi_q - lo_q) * 0.18)
            ax.set_ylim(lo_q - ypad, hi_q + ypad)

    annotations: list[dict[str, Any]] = []
    seen_pts: list[tuple[float, float]] = []

    def _add_pt_ann(px: float, py: float, label: str, color: str) -> None:
        # Merge labels if root & inflection coincide (e.g. x=0 for x^3 - 3x!)
        for i, (sx, sy) in enumerate(seen_pts):
            if math.hypot(px - sx, py - sy) < 1e-4:
                annotations[i]["label"] = annotations[i]["label"] + "\n" + label
                return
        seen_pts.append((px, py))
        annotations.append({"x": px, "y": py, "label": label, "color": color})

    for fm in data["functions"]:
        for r in fm["roots"]:
            ax.scatter([r["x"]], [0.0], color=t["teal"], edgecolor=t["fig_bg"], s=60, marker="o", linewidth=1.1, zorder=7)
            lbl = f"Root ({r['xExact'] or _fmt_num(r['x'], 3)}, 0)"
            _add_pt_ann(r["x"], 0.0, lbl, t["teal"])
        for ex in fm["extrema"]:
            mk = "^" if ex["kind"] == "local_max" else ("v" if ex["kind"] == "local_min" else "s")
            col_e = t["crimson"] if ex["kind"] == "local_max" else t["amber"]
            tag = "Max" if ex["kind"] == "local_max" else ("Min" if ex["kind"] == "local_min" else "Saddle")
            ax.scatter([ex["x"]], [ex["y"]], color=col_e, edgecolor=t["fig_bg"], s=72, marker=mk, linewidth=1.1, zorder=8)
            lbl = f"{tag} ({ex['xExact'] or _fmt_num(ex['x'], 3)}, {ex['yExact'] or _fmt_num(ex['y'], 3)})"
            _add_pt_ann(ex["x"], ex["y"], lbl, col_e)
        for inf in fm["inflectionPoints"]:
            ax.scatter([inf["x"]], [inf["y"]], color=t["secondary"], edgecolor=t["fig_bg"], s=62, marker="D", linewidth=1.1, zorder=7)
            lbl = f"Infl ({inf['xExact'] or _fmt_num(inf['x'], 3)}, {inf['yExact'] or _fmt_num(inf['y'], 3)})"
            _add_pt_ann(inf["x"], inf["y"], lbl, t["secondary"])

    _place_non_overlapping_annotations(ax, annotations, t)
    ax.set_title(title or f"Calculus Curve Analysis · {data['functions'][0]['simplified']}", fontsize=11.5, fontweight="bold", color=t["text"], pad=10)
    leg = ax.legend(loc="upper left", fontsize=7.8, frameon=True, framealpha=0.94, facecolor=t["pill_bg"], edgecolor=t["panel_border"])
    leg.set_zorder(15)
    for text in leg.get_texts():
        text.set_color(t["text"])

    rows: list[tuple[str, str, str | None]] = []
    for idx, fm in enumerate(data["functions"][:2]):
        pref = f"f{idx+1}" if len(data["functions"]) > 1 else "f"
        rows.append((f"{pref}(x)", fm["simplified"], "primary"))
        rows.append((f"{pref}'(x)", fm["derivative"], "muted"))
        rows.append((f"{pref}''(x)", fm["secondDerivative"], "muted"))
        r_str = ", ".join(r["xExact"] or _fmt_num(r["x"], 3) for r in fm["roots"]) or "None"
        rows.append(("REAL ROOTS", r_str, "teal"))
        ex_str = "; ".join(f"{e['kind']}({e['xExact'] or _fmt_num(e['x'],2)},{e['yExact'] or _fmt_num(e['y'],2)})" for e in fm["extrema"]) or "None"
        rows.append(("EXTREMA", ex_str, "amber"))
        in_str = "; ".join(f"({i['xExact'] or _fmt_num(i['x'],2)},{i['yExact'] or _fmt_num(i['y'],2)})" for i in fm["inflectionPoints"]) or "None"
        rows.append(("INFLECTION", in_str, "secondary"))

    _render_info_panel(ax_info, t, "SymPy Calculus Summary", f"Domain [{_fmt_num(xmin)}, {_fmt_num(xmax)}]", rows)
    return _save_figure(fig, out_path, dpi)


def render_func_html(data: dict[str, Any], out_path: Path, theme: str, title: str | None) -> int:
    x = data["_sym"]["x"]
    js_funcs: list[str] = []
    js_derivs: list[str] = []
    xs_fb = np.linspace(data["xmin"], data["xmax"], 1000)
    fb_tables: list[dict[str, Any] | None] = []

    for idx, sym_it in enumerate(data["_sym"]["items"]):
        fj = sympy_to_js(sym_it["f"], "x")
        dfj = sympy_to_js(sym_it["df"], "x")
        if fj is not None and dfj is not None:
            js_funcs.append(f"(x) => ({fj})")
            js_derivs.append(f"(x) => ({dfj})")
            fb_tables.append(None)
        else:
            yf = _eval_expr_array(sym_it["f"], x, xs_fb)
            ydf = _eval_expr_array(sym_it["df"], x, xs_fb)
            fb_tables.append(
                {
                    "f": [None if not math.isfinite(v) else float(v) for v in yf],
                    "df": [None if not math.isfinite(v) else float(v) for v in ydf],
                }
            )
            js_funcs.append(f"(x) => interpTable({idx}, 'f', x)")
            js_derivs.append(f"(x) => interpTable({idx}, 'df', x)")

    payload = {
        "xmin": data["xmin"],
        "xmax": data["xmax"],
        "ymin": data["ymin"],
        "ymax": data["ymax"],
        "functions": data["functions"],
        "flags": data["_flags"],
        "fallback": fb_tables,
    }

    sidebar = """
    <div class="card">
      <div class="card-title">Calculus Overlays</div>
      <div class="toggles">
        <label class="toggle-item"><input type="checkbox" id="chkRoots" checked> Roots</label>
        <label class="toggle-item"><input type="checkbox" id="chkExtrema" checked> Extrema</label>
        <label class="toggle-item"><input type="checkbox" id="chkInfl" checked> Inflection</label>
        <label class="toggle-item"><input type="checkbox" id="chkDeriv"> Show f'(x)</label>
      </div>
    </div>
    <div class="card">
      <div class="card-title">Live Cursor Probe</div>
      <div class="kv-list" id="probeCard"></div>
    </div>
    <div class="card">
      <div class="card-title">SymPy Exact Features</div>
      <div class="kv-list" id="readout"></div>
    </div>
    """

    js_body = f"""
  const DATA = {safe_json_for_script(payload)};
  function __erf(x) {{
    const s = x >= 0 ? 1 : -1, ax = Math.abs(x), t = 1 / (1 + 0.3275911 * ax);
    return s * (1 - (((((1.061405429 * t - 1.453152027) * t) + 1.421413741) * t - 0.284496736) * t + 0.254829592) * t * Math.exp(-ax * ax));
  }}
  function interpTable(idx, key, x) {{
    const tbl = DATA.fallback[idx]; if (!tbl) return NaN;
    const arr = tbl[key];
    const u = ((x - DATA.xmin) / (DATA.xmax - DATA.xmin)) * (arr.length - 1);
    const i = Math.floor(u);
    if (i < 0 || i >= arr.length - 1) return NaN;
    const a = arr[i], b = arr[i + 1];
    return (a === null || b === null) ? NaN : a + (b - a) * (u - i);
  }}
  const FUNCS = [{", ".join(js_funcs)}];
  const DERIVS = [{", ".join(js_derivs)}];

  const canvas = document.getElementById("canvas");
  const ctx = canvas.getContext("2d");
  const readout = document.getElementById("readout");
  const probeCard = document.getElementById("probeCard");
  const hud = document.getElementById("hud");

  // Compute initial y-range
  let yLo = -4, yHi = 4;
  if (DATA.ymin !== null && DATA.ymax !== null) {{ yLo = DATA.ymin; yHi = DATA.ymax; }}
  else {{
    const ys = [];
    for (let i = 0; i <= 300; i++) {{
      const xv = DATA.xmin + (DATA.xmax - DATA.xmin) * (i / 300);
      FUNCS.forEach(fn => {{ const v = fn(xv); if (Number.isFinite(v) && Math.abs(v) < 1e4) ys.push(v); }});
    }}
    ys.sort((a, b) => a - b);
    if (ys.length) {{
      yLo = ys[Math.floor(ys.length * 0.02)];
      yHi = ys[Math.floor(ys.length * 0.98)];
      const p = Math.max(0.8, (yHi - yLo) * 0.2);
      yLo -= p; yHi += p;
    }}
  }}
  const view = {{ xMin: DATA.xmin, xMax: DATA.xmax, yMin: yLo, yMax: yHi }};
  const initView = Object.assign({{}}, view);
  const opts = {{ roots: true, extrema: true, infl: true, deriv: false }};
  let probeX = (DATA.xmin + DATA.xmax) / 2;

  function renderReadout() {{
    const rows = [];
    DATA.functions.forEach((fm, i) => {{
      rows.push(["f" + (i + 1) + "(x)", fm.simplified, "accent"]);
      rows.push(["f" + (i + 1) + "'(x)", fm.derivative, ""]);
      rows.push(["Roots", fm.roots.map(r => r.xExact || fmt(r.x, 3)).join(", ") || "None", "teal"]);
      rows.push(["Extrema", fm.extrema.map(e => e.kind + "(" + (e.xExact || fmt(e.x, 2)) + ", " + (e.yExact || fmt(e.y, 2)) + ")").join("; ") || "None", "amber"]);
      rows.push(["Inflection", fm.inflectionPoints.map(p => "(" + (p.xExact || fmt(p.x, 2)) + ", " + (p.yExact || fmt(p.y, 2)) + ")").join("; ") || "None", "violet"]);
    }});
    readout.innerHTML = rows.map(r =>
      '<div class="kv"><span class="kv-k">' + r[0] + '</span><span class="kv-v ' + r[2] + '">' + r[1] + '</span></div>'
    ).join("");
  }}

  function renderProbe() {{
    const rows = [["x", fmt(probeX, 4), "accent"]];
    DATA.functions.forEach((fm, i) => {{
      rows.push(["f" + (i + 1) + "(x)", fmt(FUNCS[i](probeX), 4), "teal"]);
      rows.push(["f" + (i + 1) + "'(x)", fmt(DERIVS[i](probeX), 4), "amber"]);
    }});
    probeCard.innerHTML = rows.map(r =>
      '<div class="kv"><span class="kv-k">' + r[0] + '</span><span class="kv-v ' + r[2] + '">' + r[1] + '</span></div>'
    ).join("");
  }}

  function draw() {{
    const rect = canvas.getBoundingClientRect();
    const w = Math.max(10, rect.width), hpx = Math.max(10, rect.height);
    const dpr = window.devicePixelRatio || 1;
    if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(hpx * dpr)) {{
      canvas.width = Math.round(w * dpr); canvas.height = Math.round(hpx * dpr);
    }}
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const bg = cssVar("--canvas-bg"), gridC = cssVar("--grid"), axisC = cssVar("--axis");
    const pal = [cssVar("--accent"), cssVar("--violet"), cssVar("--teal"), cssVar("--amber")];
    const cTeal = cssVar("--teal"), cCrim = cssVar("--crimson"), cAmb = cssVar("--amber"), cVio = cssVar("--violet");

    ctx.fillStyle = bg; ctx.fillRect(0, 0, w, hpx);
    const toSX = x => ((x - view.xMin) / (view.xMax - view.xMin)) * w;
    const toSY = y => hpx - ((y - view.yMin) / (view.yMax - view.yMin)) * hpx;

    // Grid
    ctx.strokeStyle = gridC; ctx.lineWidth = 1; ctx.beginPath();
    for (let i = 1; i < 10; i++) {{
      const gx = view.xMin + (view.xMax - view.xMin) * (i / 10);
      ctx.moveTo(toSX(gx), 0); ctx.lineTo(toSX(gx), hpx);
      const gy = view.yMin + (view.yMax - view.yMin) * (i / 10);
      ctx.moveTo(0, toSY(gy)); ctx.lineTo(w, toSY(gy));
    }}
    ctx.stroke();

    // Axes
    ctx.strokeStyle = axisC; ctx.lineWidth = 1.4; ctx.beginPath();
    if (view.yMin <= 0 && view.yMax >= 0) {{ ctx.moveTo(0, toSY(0)); ctx.lineTo(w, toSY(0)); }}
    if (view.xMin <= 0 && view.xMax >= 0) {{ ctx.moveTo(toSX(0), 0); ctx.lineTo(toSX(0), hpx); }}
    ctx.stroke();

    // Plot curves & optional derivative
    const ySpan = view.yMax - view.yMin;
    FUNCS.forEach((fn, idx) => {{
      const col = pal[idx % pal.length];
      function strokeFn(f, c, lw, dash) {{
        ctx.save(); ctx.strokeStyle = c; ctx.lineWidth = lw;
        if (dash) ctx.setLineDash(dash);
        ctx.beginPath(); let open = false, prevY = NaN;
        for (let i = 0; i <= 600; i++) {{
          const xv = view.xMin + (view.xMax - view.xMin) * (i / 600);
          const yv = f(xv);
          if (!Number.isFinite(yv) || Math.abs(yv - prevY) > ySpan * 2.5 || yv < view.yMin - ySpan * 4 || yv > view.yMax + ySpan * 4) {{
            open = false; prevY = yv; continue;
          }}
          if (!open) {{ ctx.moveTo(toSX(xv), toSY(yv)); open = true; }} else ctx.lineTo(toSX(xv), toSY(yv));
          prevY = yv;
        }}
        ctx.stroke(); ctx.restore();
      }}
      strokeFn(fn, col, 2.5, null);
      if (opts.deriv) strokeFn(DERIVS[idx], col, 1.5, [5, 4]);
    }});

    // Feature markers
    DATA.functions.forEach(fm => {{
      if (opts.roots) fm.roots.forEach(r => {{
        ctx.beginPath(); ctx.arc(toSX(r.x), toSY(0), 5, 0, Math.PI * 2);
        ctx.fillStyle = cTeal; ctx.fill();
      }});
      if (opts.extrema) fm.extrema.forEach(e => {{
        ctx.beginPath(); ctx.arc(toSX(e.x), toSY(e.y), 5.5, 0, Math.PI * 2);
        ctx.fillStyle = e.kind === "local_max" ? cCrim : cAmb; ctx.fill();
      }});
      if (opts.infl) fm.inflectionPoints.forEach(p => {{
        ctx.beginPath(); ctx.arc(toSX(p.x), toSY(p.y), 5, 0, Math.PI * 2);
        ctx.fillStyle = cVio; ctx.fill();
      }});
    }});

    // Crosshair
    if (probeX >= view.xMin && probeX <= view.xMax) {{
      ctx.save(); ctx.strokeStyle = axisC; ctx.setLineDash([4, 4]); ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(toSX(probeX), 0); ctx.lineTo(toSX(probeX), hpx); ctx.stroke();
      ctx.restore();
      FUNCS.forEach((fn, i) => {{
        const yv = fn(probeX);
        if (Number.isFinite(yv)) {{
          ctx.beginPath(); ctx.arc(toSX(probeX), toSY(yv), 5, 0, Math.PI * 2);
          ctx.fillStyle = pal[i % pal.length]; ctx.fill();
          ctx.strokeStyle = bg; ctx.lineWidth = 1.6; ctx.stroke();
        }}
      }});
    }}
    renderProbe();
  }}

  let dragging = false, lx = 0, ly = 0;
  canvas.addEventListener("pointerdown", e => {{ dragging = true; lx = e.clientX; ly = e.clientY; }});
  window.addEventListener("pointerup", () => {{ dragging = false; }});
  window.addEventListener("pointermove", e => {{
    const rect = canvas.getBoundingClientRect();
    if (dragging) {{
      const dx = ((e.clientX - lx) / Math.max(1, rect.width)) * (view.xMax - view.xMin);
      const dy = ((e.clientY - ly) / Math.max(1, rect.height)) * (view.yMax - view.yMin);
      view.xMin -= dx; view.xMax -= dx; view.yMin += dy; view.yMax += dy;
      lx = e.clientX; ly = e.clientY; draw();
    }} else if (e.target === canvas) {{
      probeX = view.xMin + ((e.clientX - rect.left) / Math.max(1, rect.width)) * (view.xMax - view.xMin);
      hud.textContent = "x = " + fmt(probeX, 3) + " · f(x) = " + fmt(FUNCS[0](probeX), 3) + " · f'(x) = " + fmt(DERIVS[0](probeX), 3);
      draw();
    }}
  }});
  canvas.addEventListener("wheel", e => {{
    e.preventDefault();
    const f = e.deltaY > 0 ? 1.12 : 0.89;
    const cx = (view.xMin + view.xMax) / 2, cy = (view.yMin + view.yMax) / 2;
    const hx = ((view.xMax - view.xMin) / 2) * f, hy = ((view.yMax - view.yMin) / 2) * f;
    view.xMin = cx - hx; view.xMax = cx + hx; view.yMin = cy - hy; view.yMax = cy + hy;
    draw();
  }}, {{ passive: false }});

  [["chkRoots", "roots"], ["chkExtrema", "extrema"], ["chkInfl", "infl"], ["chkDeriv", "deriv"]].forEach(pair => {{
    const el = document.getElementById(pair[0]);
    if (el) el.addEventListener("change", () => {{ opts[pair[1]] = el.checked; draw(); }});
  }});
  document.getElementById("resetViewBtn").addEventListener("click", () => {{ Object.assign(view, initView); draw(); }});
  onThemeChange = draw;
  window.addEventListener("resize", draw);
  renderReadout();
  draw();
"""
    html_doc = _build_offline_html(
        title=title or f"Calculus Curve Explorer · {data['functions'][0]['simplified']}",
        badge="100% Offline · Calculus",
        initial_theme=theme,
        sidebar_html=sidebar,
        hud_hint="Hover for (x, f(x), f'(x)) · Drag to pan · Scroll to zoom",
        js_body=js_body,
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html_doc, encoding="utf-8")
    return out_path.stat().st_size


# ---------------------------------------------------------------------------
# CLI Entry Point & Output Dispatch
# ---------------------------------------------------------------------------


def _strip_private(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _strip_private(v) for k, v in obj.items() if not str(k).startswith("_")}
    if isinstance(obj, list):
        return [_strip_private(v) for v in obj]
    return obj


def _emit_output(payload: dict[str, Any], as_json: bool) -> None:
    clean = _strip_private(payload)
    if as_json:
        print(json.dumps(clean, indent=2))
        return
    sub = clean.get("subcommand", "plot")
    print(f"[math_plot:{sub}] Wrote {clean['output']} ({clean['bytes']} bytes, {clean['format']}, theme={clean['theme']})")
    if sub == "conic":
        foci_s = ", ".join(f"({f['xExact']}, {f['yExact']})" for f in clean["foci"])
        print(f"  Equation: {clean['equation']} | e = {clean['eccentricityExact']} | Foci: {foci_s} | LR = {clean['latusRectumExact']}")
        print(f"  Director: {clean['directorCircleEquation']}")
        if clean.get("tangentEquation"):
            print(f"  Tangent: {clean['tangentEquation']} | Normal: {clean['normalEquation']}")
    elif sub == "argand":
        print(f"  Loci: {len(clean['loci'])} | Points: {len(clean['points'])} | Intersections: {len(clean['intersections'])}")
    elif sub == "integral":
        print(f"  Signed Integral: {clean['signedIntegralExact'] or clean['signedIntegral']} | Geometric Area: {clean['geometricAreaExact'] or clean['geometricArea']}")
    elif sub == "vector3d":
        if "relation" in clean:
            sd = clean.get("shortestDistanceExact", clean.get("distance", ""))
            print(f"  Relation: {clean['relation']} | Distance: {sd} | Angle: {clean.get('angleDeg')} deg")
    elif sub == "func":
        for fm in clean["functions"]:
            print(f"  f(x) = {fm['simplified']} | roots={len(fm['roots'])} extrema={len(fm['extrema'])} inflection={len(fm['inflectionPoints'])}")


def _add_common_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--out", required=True, help="Output file path (.svg, .png, or .html)")
    p.add_argument("--theme", choices=["light", "dark"], default="light", help="Color theme (default: light)")
    p.add_argument("--dpi", type=int, default=220, help="Raster DPI for PNG output (default: 220)")
    p.add_argument("--title", default=None, help="Optional plot/lab title override")
    p.add_argument("--json", action="store_true", help="Print exact SymPy properties and output metadata as JSON")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="math_plot.py",
        description="Coordinate Geometry, 3D Vectors & Calculus Interactive Plotter (SVG/PNG + 100% Offline HTML)",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    # 1. conic
    p_conic = sub.add_parser("conic", help="Plot parabola, ellipse, or hyperbola with exact SymPy foci, tangent, normal & director locus")
    p_conic.add_argument("--type", choices=["parabola", "ellipse", "hyperbola"], default="ellipse", help="Conic type (default: ellipse)")
    p_conic.add_argument("--a", default=None, help="Semi-axis a (default: 4 for ellipse/hyperbola; fallback focal param p for parabola)")
    p_conic.add_argument("--b", default="2.5", help="Semi-axis b for ellipse/hyperbola (default: 2.5)")
    p_conic.add_argument("--p", default=None, help="Focal parameter p for parabola (y-k)^2 = 4p(x-h) (default: 2.0)")
    p_conic.add_argument("--h", default="0", help="Center/vertex x-coordinate h (default: 0)")
    p_conic.add_argument("--k", default="0", help="Center/vertex y-coordinate k (default: 0)")
    p_conic.add_argument("--tangent-t", dest="tangent_t", default=None, help="Parametric angle theta (deg) for ellipse/hyperbola or parameter t for parabola")
    p_conic.add_argument("--show-director", action=argparse.BooleanOptionalAction, default=False, help="Draw director circle / directrix locus")
    p_conic.add_argument("--show-latus-rectum", action=argparse.BooleanOptionalAction, default=True, help="Draw latus rectum chord (default: true)")
    p_conic.add_argument("--show-normal", action=argparse.BooleanOptionalAction, default=False, help="Draw normal line at tangent point")
    p_conic.add_argument("--show-asymptotes", action=argparse.BooleanOptionalAction, default=True, help="Draw hyperbola asymptotes (default: true)")
    _add_common_args(p_conic)

    # 2. argand
    p_arg = sub.add_parser("argand", help="Plot complex plane loci (circle, perp-bisector, ray, ellipse-foci, apollonius) & intersections")
    p_arg.add_argument("--loci", default="", help='Semicolon-separated loci, e.g. "circle:1+2j,3;perp-bisector:0,4;ray:1+1j,45;ellipse-foci:-2,2,6"')
    p_arg.add_argument("--points", default="", help='Semicolon-separated complex points, e.g. "0;1+2j;4"')
    _add_common_args(p_arg)

    # 3. integral
    p_int = sub.add_parser("integral", help="Compute & plot exact signed definite integral vs geometric area between curves")
    p_int.add_argument("--expr", required=True, help='Integrand f(x), e.g. "sin(x)"')
    p_int.add_argument("--expr2", default="0", help='Optional second curve g(x) for area between curves (default: "0")')
    p_int.add_argument("--a", default="0", help='Lower limit a (accepts SymPy expressions like "0", "-pi/2")')
    p_int.add_argument("--b", default="pi", help='Upper limit b (accepts SymPy expressions like "pi", "2*pi")')
    _add_common_args(p_int)

    # 4. vector3d
    p_v3 = sub.add_parser("vector3d", help="Compute & plot 3D lines/planes, skew shortest distance, feet of perpendicular, and offline 3D HTML")
    p_v3.add_argument("--lines", default="", help='Semicolon-separated "px,py,pz|dx,dy,dz" specs')
    p_v3.add_argument("--planes", default="", help='Semicolon-separated "a,b,c,d" specs for ax+by+cz=d')
    p_v3.add_argument("--points", default="", help='Optional semicolon-separated "x,y,z" points')
    p_v3.add_argument("--elev", type=float, default=24.0, help="Static 3D elevation angle in deg (default: 24)")
    p_v3.add_argument("--azim", type=float, default=-52.0, help="Static 3D azimuth angle in deg (default: -52)")
    _add_common_args(p_v3)

    # 5. func
    p_fn = sub.add_parser("func", help="Plot expressions in x with exact SymPy roots, extrema, inflection points & asymptotes")
    p_fn.add_argument("--exprs", required=True, help='Semicolon-separated expressions in x, e.g. "x**3 - 3*x"')
    p_fn.add_argument("--xmin", default="-4", help="Domain minimum x (default: -4)")
    p_fn.add_argument("--xmax", default="4", help="Domain maximum x (default: 4)")
    p_fn.add_argument("--ymin", default=None, help="Optional y-axis minimum")
    p_fn.add_argument("--ymax", default=None, help="Optional y-axis maximum")
    p_fn.add_argument("--mark-roots", action=argparse.BooleanOptionalAction, default=True, help="Find & annotate real roots (default: true)")
    p_fn.add_argument("--mark-extrema", action=argparse.BooleanOptionalAction, default=True, help="Find & annotate extrema f'=0 (default: true)")
    p_fn.add_argument("--mark-inflection", action=argparse.BooleanOptionalAction, default=True, help="Find & annotate verified inflection points (default: true)")
    _add_common_args(p_fn)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    out_path = Path(args.out).expanduser().resolve()
    ext = out_path.suffix.lower()
    if ext not in (".svg", ".png", ".html"):
        raise SystemExit(f"Unsupported output extension {ext!r}. Use .svg, .png, or .html.")

    try:
        if args.cmd == "conic":
            data = compute_conic_data(args)
            nbytes = (
                render_conic_html(data, out_path, args.theme, args.title)
                if ext == ".html"
                else render_conic_static(data, out_path, args.theme, args.dpi, args.title)
            )
        elif args.cmd == "argand":
            data = compute_argand_data(args)
            nbytes = (
                render_argand_html(data, out_path, args.theme, args.title)
                if ext == ".html"
                else render_argand_static(data, out_path, args.theme, args.dpi, args.title)
            )
        elif args.cmd == "integral":
            data = compute_integral_data(args)
            nbytes = (
                render_integral_html(data, out_path, args.theme, args.title)
                if ext == ".html"
                else render_integral_static(data, out_path, args.theme, args.dpi, args.title)
            )
        elif args.cmd == "vector3d":
            data = compute_vector3d_data(args)
            nbytes = (
                render_vector3d_html(data, out_path, args.theme, args.title)
                if ext == ".html"
                else render_vector3d_static(data, out_path, args.theme, args.dpi, args.title, args.elev, args.azim)
            )
        elif args.cmd == "func":
            data = compute_func_data(args)
            nbytes = (
                render_func_html(data, out_path, args.theme, args.title)
                if ext == ".html"
                else render_func_static(data, out_path, args.theme, args.dpi, args.title)
            )
        else:
            raise SystemExit(f"Unknown command: {args.cmd}")
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    data["output"] = str(out_path)
    data["format"] = ext.lstrip(".")
    data["bytes"] = int(nbytes)
    data["theme"] = args.theme
    _emit_output(data, args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
