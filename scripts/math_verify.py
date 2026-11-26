#!/usr/bin/env python3
"""Deterministic Physics & Maths Verifier (SymPy + Pint).

Prevents sign slips, algebra errors, wrong limits, ODE slips, matrix/vector
mistakes, and dimensional errors during JEE/STEM teaching and practice drills.
"""

from __future__ import annotations

import argparse
import ast
import json
import math
import operator
import re
from typing import Any

import sympy as sp
from sympy.parsing.sympy_parser import (
    convert_xor,
    implicit_multiplication_application,
    parse_expr,
    standard_transformations,
)

TRANSFORMS = standard_transformations + (implicit_multiplication_application, convert_xor)


def _vec(*args: Any) -> sp.Matrix:
    if len(args) == 1 and isinstance(args[0], (list, tuple)):
        return sp.Matrix(list(args[0]))
    return sp.Matrix(list(args))


def _dot(u: Any, v: Any) -> sp.Expr:
    mu = u if isinstance(u, sp.MatrixBase) else sp.Matrix(u)
    mv = v if isinstance(v, sp.MatrixBase) else sp.Matrix(v)
    return sp.simplify(mu.dot(mv))


def _cross(u: Any, v: Any) -> sp.Matrix:
    mu = u if isinstance(u, sp.MatrixBase) else sp.Matrix(u)
    mv = v if isinstance(v, sp.MatrixBase) else sp.Matrix(v)
    return sp.simplify(mu.cross(mv))


def _norm(v: Any) -> sp.Expr:
    mv = v if isinstance(v, sp.MatrixBase) else sp.Matrix(v)
    return sp.simplify(mv.norm())


def parse_sym(text: str, extra_syms: dict[str, Any] | None = None) -> Any:
    local: dict[str, Any] = {
        "pi": sp.pi,
        "e": sp.E,
        "E": sp.E,
        "I": sp.I,
        "oo": sp.oo,
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
        "re": sp.re,
        "im": sp.im,
        "arg": sp.arg,
        "conjugate": sp.conjugate,
        "diff": sp.diff,
        "integrate": sp.integrate,
        "limit": sp.limit,
        "series": sp.series,
        "simplify": sp.simplify,
        "factor": sp.factor,
        "expand": sp.expand,
        "Sum": sp.Sum,
        "Product": sp.Product,
        "binomial": sp.binomial,
        "factorial": sp.factorial,
        "gamma": sp.gamma,
        "floor": sp.floor,
        "ceiling": sp.ceiling,
        "Mod": sp.Mod,
        "gcd": sp.gcd,
        "lcm": sp.lcm,
        "Piecewise": sp.Piecewise,
        "Eq": sp.Eq,
        "Function": sp.Function,
        "Derivative": sp.Derivative,
        "dsolve": sp.dsolve,
        "Matrix": sp.Matrix,
        "eye": sp.eye,
        "zeros": sp.zeros,
        "ones": sp.ones,
        "diag": sp.diag,
        "det": lambda m: sp.Matrix(m).det(),
        "trace": lambda m: sp.Matrix(m).trace(),
        "vec": _vec,
        "dot": _dot,
        "cross": _cross,
        "norm": _norm,
    }
    if extra_syms:
        local.update(extra_syms)
    return parse_expr(text, local_dict=local, transformations=TRANSFORMS)


def make_symbols(var_csv: str | None, real: bool = True) -> dict[str, sp.Symbol]:
    if not var_csv:
        return {}
    kwargs = {"real": True} if real else {}
    return {name.strip(): sp.Symbol(name.strip(), **kwargs) for name in var_csv.split(",") if name.strip()}


def _safe_simplify(obj: Any) -> Any:
    if isinstance(obj, sp.MatrixBase):
        return obj.applyfunc(sp.simplify)
    if isinstance(obj, (list, tuple)):
        return type(obj)(_safe_simplify(x) for x in obj)
    if isinstance(obj, dict):
        return {k: _safe_simplify(v) for k, v in obj.items()}
    try:
        return sp.simplify(obj)
    except Exception:
        return obj


def _safe_numeric(obj: Any) -> Any:
    if isinstance(obj, sp.MatrixBase):
        if obj.free_symbols:
            return None
        try:
            return [[complex(sp.N(el, 12)).real if abs(complex(sp.N(el, 12)).imag) < 1e-12 else str(sp.N(el, 12)) for el in obj.row(r)] for r in range(obj.rows)]
        except Exception:
            return None
    if hasattr(obj, "free_symbols") and not obj.free_symbols:
        try:
            val = sp.N(obj, 12)
            if getattr(val, "is_real", False):
                return float(val)
            c = complex(val)
            if abs(c.imag) < 1e-12:
                return float(c.real)
            return str(val)
        except Exception:
            try:
                return str(sp.N(obj, 12))
            except Exception:
                return None
    return None


def parse_latex_sym(text: str) -> Any:
    """Parse LaTeX math into SymPy using latex2sympy2_extended (with SymPy Lark fallback)."""
    raw = text.strip().strip("$").strip()
    try:
        from latex2sympy2_extended import latex2sympy  # type: ignore

        return latex2sympy(raw)
    except Exception:
        from sympy.parsing.latex import parse_latex

        return parse_latex(raw, backend="lark")


def parse_input(text: str, extra_syms: dict[str, Any] | None = None, is_latex: bool = False) -> Any:
    if is_latex:
        expr = parse_latex_sym(text)
        if extra_syms and hasattr(expr, "free_symbols"):
            subs = {s: extra_syms[s.name] for s in expr.free_symbols if s.name in extra_syms}
            if subs:
                expr = expr.subs(subs)
        return expr
    return parse_sym(text, extra_syms)


def cmd_eval(args: argparse.Namespace) -> None:
    syms = make_symbols(args.vars, real=not args.complex)
    expr = parse_input(args.expr, syms, is_latex=args.latex)
    if hasattr(expr, "doit") and not isinstance(expr, sp.MatrixBase):
        try:
            expr = expr.doit()
        except Exception:
            pass
    simp = _safe_simplify(expr)
    print(json.dumps({
        "input": args.expr,
        "simplified": str(simp),
        "latex": sp.latex(simp),
        "numeric": _safe_numeric(simp),
    }, indent=2))


def cmd_check_eq(args: argparse.Namespace) -> None:
    syms = make_symbols(args.vars, real=not args.complex)
    lhs = parse_input(args.lhs, syms, is_latex=args.latex)
    rhs = parse_input(args.rhs, syms, is_latex=args.latex)
    diff_expr = _safe_simplify(lhs - rhs)
    if isinstance(diff_expr, sp.MatrixBase):
        equal = all(el == 0 for el in diff_expr)
    else:
        equal = diff_expr == 0
        if not equal:
            for fn in (sp.trigsimp, sp.expand_complex, sp.powsimp, sp.radsimp, sp.cancel, sp.factor):
                try:
                    cand = sp.simplify(fn(lhs - rhs))
                    if cand == 0:
                        diff_expr = sp.Integer(0)
                        equal = True
                        break
                except Exception:
                    continue
    print(json.dumps({
        "lhs": str(lhs),
        "rhs": str(rhs),
        "equivalent": bool(equal),
        "difference": str(diff_expr),
        "differenceLatex": sp.latex(diff_expr),
    }, indent=2))


def cmd_solve(args: argparse.Namespace) -> None:
    target_names = [v.strip() for v in args.var.split(",") if v.strip()]
    # Keep background parameters real, but allow solve targets to be complex unless --real is explicitly requested.
    syms = make_symbols(args.vars, real=True)
    target_syms = []
    for name in target_names:
        sym = sp.Symbol(name, real=True) if args.real else sp.Symbol(name)
        syms[name] = sym
        target_syms.append(sym)

    eq_parts = [p.strip() for p in args.equation.split(";") if p.strip()]
    eqs = []
    for part in eq_parts:
        if "=" in part:
            lhs_s, rhs_s = part.split("=", 1)
            eqs.append(sp.Eq(parse_sym(lhs_s, syms), parse_sym(rhs_s, syms)))
        else:
            eqs.append(sp.Eq(parse_sym(part, syms), 0))

    if len(eqs) == 1 and len(target_syms) == 1:
        sols = sp.solve(eqs[0], target_syms[0])
        sols_str = [str(sp.simplify(s)) for s in sols]
        sols_tex = [sp.latex(sp.simplify(s)) for s in sols]
        print(json.dumps({
            "equation": str(eqs[0]),
            "variable": target_names[0],
            "solutions": sols_str,
            "solutionsLatex": sols_tex,
        }, indent=2))
    else:
        raw = sp.solve(eqs, target_syms, dict=True)
        formatted = [{str(k): str(sp.simplify(v)) for k, v in sol.items()} for sol in raw]
        formatted_tex = [{str(k): sp.latex(sp.simplify(v)) for k, v in sol.items()} for sol in raw]
        print(json.dumps({
            "equations": [str(e) for e in eqs],
            "variables": target_names,
            "solutions": formatted,
            "solutionsLatex": formatted_tex,
        }, indent=2))


def cmd_limit(args: argparse.Namespace) -> None:
    syms = make_symbols(args.vars, real=True)
    x = syms.get(args.var, sp.Symbol(args.var, real=True))
    syms[args.var] = x
    expr = parse_sym(args.expr, syms)
    point = parse_sym(args.to, syms)

    def _lim(d: str) -> str:
        try:
            return str(sp.simplify(sp.limit(expr, x, point, dir=d)))
        except Exception as exc:
            return f"error: {exc}"

    out: dict[str, Any] = {
        "expr": str(expr),
        "var": args.var,
        "to": str(point),
        "dir": args.dir,
    }
    if args.dir == "+-" and point not in (sp.oo, -sp.oo):
        left = sp.simplify(sp.limit(expr, x, point, dir="-"))
        right = sp.simplify(sp.limit(expr, x, point, dir="+"))
        exists = sp.simplify(left - right) == 0
        out["leftLimit"] = str(left)
        out["rightLimit"] = str(right)
        out["exists"] = bool(exists)
        out["limit"] = str(right) if exists else "DNE (left != right)"
        out["latex"] = sp.latex(right) if exists else r"\text{DNE}"
    else:
        d = "-" if point == sp.oo else ("+" if point == -sp.oo else args.dir)
        val = sp.simplify(sp.limit(expr, x, point, dir=d))
        out["limit"] = str(val)
        out["latex"] = sp.latex(val)
    print(json.dumps(out, indent=2))


def cmd_series(args: argparse.Namespace) -> None:
    syms = make_symbols(args.vars, real=True)
    x = syms.get(args.var, sp.Symbol(args.var, real=True))
    syms[args.var] = x
    expr = parse_sym(args.expr, syms)
    x0 = parse_sym(args.at, syms)
    ser = sp.series(expr, x, x0, args.order)
    poly = sp.simplify(ser.removeO())
    print(json.dumps({
        "expr": str(expr),
        "var": args.var,
        "around": str(x0),
        "order": args.order,
        "series": str(ser),
        "polynomial": str(poly),
        "latex": sp.latex(ser),
    }, indent=2))


def _normalize_ode_text(text: str, func_name: str, var_name: str) -> str:
    """Convert prime notation like y'' + w^2*y = 0 into SymPy Derivative(y(t), t, k)."""
    out = text
    # Replace highest derivatives first: y'''', y''', y'', y'
    for order in (4, 3, 2, 1):
        primes = "'" * order
        pattern = rf"\b{re.escape(func_name)}{re.escape(primes)}(?!['\w])"
        repl = f"Derivative({func_name}({var_name}), {var_name}, {order})" if order > 1 else f"Derivative({func_name}({var_name}), {var_name})"
        out = re.sub(pattern, repl, out)
    # Replace bare function symbol `y` not already followed by `(` with `y(t)`.
    out = re.sub(rf"\b{re.escape(func_name)}\b(?!\s*\()", f"{func_name}({var_name})", out)
    return out


def cmd_ode(args: argparse.Namespace) -> None:
    syms = make_symbols(args.vars, real=True)
    t = syms.get(args.var, sp.Symbol(args.var, real=True))
    syms[args.var] = t
    fname = args.func_name
    f = sp.Function(fname)
    syms[fname] = f

    eq_text = _normalize_ode_text(args.equation, fname, args.var)
    if "=" in eq_text:
        lhs_s, rhs_s = eq_text.split("=", 1)
        ode_eq = sp.Eq(parse_sym(lhs_s, syms), parse_sym(rhs_s, syms))
    else:
        ode_eq = sp.Eq(parse_sym(eq_text, syms), 0)

    ics_dict: dict[Any, Any] | None = None
    if args.ics:
        ics_dict = {}
        for item in args.ics.split(","):
            item = item.strip()
            if not item or "=" not in item:
                continue
            lhs_i, rhs_i = item.split("=", 1)
            lhs_i = lhs_i.strip()
            # Handle y'(0)=0 or y(0)=1
            m = re.match(rf"^{re.escape(fname)}('+)?\((.+)\)$", lhs_i)
            if m:
                primes = m.group(1) or ""
                at_val = parse_sym(m.group(2), syms)
                if primes:
                    key = sp.Derivative(f(t), t, len(primes)).subs(t, at_val)
                else:
                    key = f(at_val)
            else:
                key = parse_sym(_normalize_ode_text(lhs_i, fname, args.var), syms)
            ics_dict[key] = parse_sym(rhs_i.strip(), syms)

    sol = sp.dsolve(ode_eq, f(t), ics=ics_dict) if ics_dict else sp.dsolve(ode_eq, f(t))
    if isinstance(sol, list):
        sol_str = [str(sp.simplify(s)) for s in sol]
        sol_tex = [sp.latex(sp.simplify(s)) for s in sol]
    else:
        sol = sp.simplify(sol)
        sol_str = str(sol)
        sol_tex = sp.latex(sol)
    print(json.dumps({
        "ode": str(ode_eq),
        "ics": args.ics,
        "solution": sol_str,
        "latex": sol_tex,
    }, indent=2))


def _parse_vector_arg(raw: str, syms: dict[str, sp.Symbol]) -> sp.Matrix:
    s = raw.strip()
    if s.startswith("[") or s.startswith("("):
        parsed = parse_sym(s, syms)
        if isinstance(parsed, sp.MatrixBase):
            return sp.Matrix(list(parsed))
        if isinstance(parsed, (list, tuple)):
            return sp.Matrix(list(parsed))
    parts = [p.strip() for p in s.split(",") if p.strip()]
    return sp.Matrix([parse_sym(p, syms) for p in parts])


def cmd_vector(args: argparse.Namespace) -> None:
    syms = make_symbols(args.vars, real=True)
    u = _parse_vector_arg(args.u, syms)
    u_norm = sp.simplify(u.norm())
    out: dict[str, Any] = {
        "u": [str(x) for x in u],
        "normU": str(u_norm),
        "unitU": [str(sp.simplify(x / u_norm)) for x in u] if u_norm != 0 else None,
    }
    if args.v:
        v = _parse_vector_arg(args.v, syms)
        if len(u) != len(v):
            raise SystemExit(f"Vector dimension mismatch: len(u)={len(u)} vs len(v)={len(v)}")
        v_norm = sp.simplify(v.norm())
        dot_uv = sp.simplify(u.dot(v))
        out["v"] = [str(x) for x in v]
        out["normV"] = str(v_norm)
        out["dot"] = str(dot_uv)
        out["dotLatex"] = sp.latex(dot_uv)
        if u_norm != 0 and v_norm != 0:
            cos_th = sp.simplify(dot_uv / (u_norm * v_norm))
            angle_rad = sp.simplify(sp.acos(cos_th))
            out["cosTheta"] = str(cos_th)
            out["angleRad"] = str(angle_rad)
            if not angle_rad.free_symbols:
                out["angleDeg"] = round(math.degrees(float(sp.N(angle_rad, 12))), 4)
            scalar_proj = sp.simplify(dot_uv / v_norm)
            vec_proj = [str(sp.simplify(dot_uv / (v_norm ** 2) * x)) for x in v]
            out["scalarProjectionUonV"] = str(scalar_proj)
            out["vectorProjectionUonV"] = vec_proj
        if len(u) == 3:
            cr = sp.simplify(u.cross(v))
            out["cross"] = [str(x) for x in cr]
            out["crossNorm"] = str(sp.simplify(cr.norm()))
            if args.w:
                w = _parse_vector_arg(args.w, syms)
                if len(w) != 3:
                    raise SystemExit("Third vector w must be 3D for scalar triple product")
                out["w"] = [str(x) for x in w]
                out["scalarTripleProduct"] = str(sp.simplify(u.dot(v.cross(w))))
    print(json.dumps(out, indent=2))


def _parse_matrix_arg(raw: str, syms: dict[str, sp.Symbol]) -> sp.Matrix:
    s = raw.strip()
    if not s.startswith("Matrix"):
        parsed = parse_sym(f"Matrix({s})", syms)
    else:
        parsed = parse_sym(s, syms)
    if not isinstance(parsed, sp.MatrixBase):
        raise SystemExit(f"Could not parse matrix: {raw}")
    return sp.Matrix(parsed)


def _mat_to_list(m: sp.MatrixBase) -> list[list[str]]:
    return [[str(sp.simplify(m[r, c])) for c in range(m.cols)] for r in range(m.rows)]


def cmd_matrix(args: argparse.Namespace) -> None:
    syms = make_symbols(args.vars, real=not args.complex)
    A = _parse_matrix_arg(args.matrix, syms)
    op = args.op
    out: dict[str, Any] = {"shape": [A.rows, A.cols], "A": _mat_to_list(A)}

    if op in ("info", "det"):
        if A.rows == A.cols:
            det_a = sp.simplify(A.det())
            out["det"] = str(det_a)
            out["detLatex"] = sp.latex(det_a)
            out["trace"] = str(sp.simplify(A.trace()))
        out["rank"] = int(A.rank())
    if op == "info":
        out["transpose"] = _mat_to_list(A.T)
        if A.rows == A.cols and sp.simplify(A.det()) != 0:
            inv_a = A.inv().applyfunc(sp.simplify)
            out["inverse"] = _mat_to_list(inv_a)
        if A.rows == A.cols and A.rows <= 4:
            lam = sp.Symbol("lambda")
            out["charpoly"] = str(sp.factor(A.charpoly(lam).as_expr()))
            try:
                out["eigenvals"] = {str(sp.simplify(k)): int(v) for k, v in A.eigenvals().items()}
            except Exception:
                pass
    elif op == "inv":
        if A.rows != A.cols:
            raise SystemExit("Matrix must be square for inverse")
        inv_a = A.inv().applyfunc(sp.simplify)
        out["inverse"] = _mat_to_list(inv_a)
        out["inverseLatex"] = sp.latex(inv_a)
    elif op == "rref":
        rref_m, pivots = A.rref()
        out["rref"] = _mat_to_list(rref_m)
        out["pivots"] = list(pivots)
    elif op == "eigen":
        if A.rows != A.cols:
            raise SystemExit("Matrix must be square for eigenvalues/eigenvectors")
        lam = sp.Symbol("lambda")
        out["charpoly"] = str(sp.factor(A.charpoly(lam).as_expr()))
        out["eigenvects"] = [
            {
                "eigenvalue": str(sp.simplify(ev)),
                "multiplicity": int(mult),
                "vectors": [[str(sp.simplify(el)) for el in v] for v in vecs],
            }
            for ev, mult, vecs in A.eigenvects()
        ]
    elif op == "solve":
        if not args.rhs:
            raise SystemExit("--rhs is required for --op solve (e.g. --rhs '[5,1]')")
        b = _parse_matrix_arg(args.rhs, syms)
        if b.rows == 1 and b.cols == A.rows:
            b = b.T
        sol = A.LUsolve(b).applyfunc(sp.simplify)
        out["rhs"] = _mat_to_list(b)
        out["solution"] = _mat_to_list(sol)
        out["solutionLatex"] = sp.latex(sol)
    elif op == "mul":
        if not args.rhs:
            raise SystemExit("--rhs is required for --op mul")
        B = _parse_matrix_arg(args.rhs, syms)
        prod = (A * B).applyfunc(sp.simplify)
        out["B"] = _mat_to_list(B)
        out["product"] = _mat_to_list(prod)
        out["productLatex"] = sp.latex(prod)
    print(json.dumps(out, indent=2))


def cmd_complex(args: argparse.Namespace) -> None:
    syms = make_symbols(args.vars, real=True)
    z = sp.expand_complex(parse_sym(args.expr, syms))
    re_z = sp.simplify(sp.re(z))
    im_z = sp.simplify(sp.im(z))
    mod = sp.simplify(sp.Abs(z))
    argument = sp.simplify(sp.arg(z))
    conj = sp.simplify(sp.conjugate(z))
    out: dict[str, Any] = {
        "input": args.expr,
        "rectangular": str(sp.simplify(re_z + sp.I * im_z)),
        "latex": sp.latex(sp.simplify(re_z + sp.I * im_z)),
        "real": str(re_z),
        "imag": str(im_z),
        "modulus": str(mod),
        "argumentRad": str(argument),
        "conjugate": str(conj),
    }
    if not argument.free_symbols:
        out["argumentDeg"] = round(math.degrees(float(sp.N(argument, 12))), 4)
    if args.roots and args.roots >= 2:
        n = int(args.roots)
        r_root = sp.simplify(mod ** sp.Rational(1, n))
        roots = []
        for k in range(n):
            ang = sp.simplify((argument + 2 * sp.pi * k) / n)
            wk = sp.simplify(sp.expand_complex(r_root * sp.exp(sp.I * ang)))
            roots.append({"k": k, "angleRad": str(ang), "value": str(wk), "latex": sp.latex(wk)})
        out["nthRoots"] = roots
    print(json.dumps(out, indent=2))


def cmd_small_osc(args: argparse.Namespace) -> None:
    syms = make_symbols(args.vars, real=True)
    x = syms.get(args.var, sp.Symbol(args.var, real=True))
    m = syms.get(args.mass, sp.Symbol(args.mass, positive=True))
    syms[args.var] = x
    syms[args.mass] = m
    U = parse_sym(args.potential, syms)
    dU = sp.simplify(sp.diff(U, x))
    d2U = sp.simplify(sp.diff(dU, x))

    if args.x0 is not None:
        x0_expr = parse_sym(args.x0, syms)
        k_eff = sp.simplify(d2U.subs(x, x0_expr))
        omega = sp.simplify(sp.sqrt(k_eff / m))
        period = sp.simplify(2 * sp.pi / omega)
        equilibria = [{
            "x0": str(x0_expr),
            "k_eff": str(k_eff),
            "k_eff_latex": sp.latex(k_eff),
            "omega": str(omega),
            "omega_latex": sp.latex(omega),
            "period": str(period),
            "period_latex": sp.latex(period),
        }]
    else:
        roots = sp.solve(sp.Eq(dU, 0), x)
        equilibria = []
        for r in roots[:6]:
            k_eff = sp.simplify(d2U.subs(x, r))
            omega = sp.simplify(sp.sqrt(k_eff / m))
            equilibria.append({
                "x0": str(r),
                "k_eff": str(k_eff),
                "omega": str(omega),
                "omega_latex": sp.latex(omega),
            })

    print(json.dumps({
        "U": str(U),
        "dU_dx": str(dU),
        "d2U_dx2": str(d2U),
        "equilibria": equilibria,
    }, indent=2))


def _build_physical_constants(ureg: Any) -> dict[str, Any]:
    """CODATA 2018/2022 exact SI physical constants for Pint dimensional & unit checks."""
    return {
        "pi": math.pi,
        "h": ureg.Quantity(6.62607015e-34, "J * s"),
        "hbar": ureg.Quantity(1.0545718176461565e-34, "J * s"),
        "c": ureg.Quantity(299792458.0, "m / s"),
        "k_B": ureg.Quantity(1.380649e-23, "J / K"),
        "kB": ureg.Quantity(1.380649e-23, "J / K"),
        "R": ureg.Quantity(8.31446261815324, "J / (mol * K)"),
        "N_A": ureg.Quantity(6.02214076e23, "1 / mol"),
        "NA": ureg.Quantity(6.02214076e23, "1 / mol"),
        "e": ureg.Quantity(1.602176634e-19, "C"),
        "q_e": ureg.Quantity(1.602176634e-19, "C"),
        "epsilon_0": ureg.Quantity(8.8541878128e-12, "F / m"),
        "eps0": ureg.Quantity(8.8541878128e-12, "F / m"),
        "mu_0": ureg.Quantity(1.25663706212e-6, "N / A**2"),
        "mu0": ureg.Quantity(1.25663706212e-6, "N / A**2"),
        "G": ureg.Quantity(6.67430e-11, "m**3 / (kg * s**2)"),
        "g_0": ureg.Quantity(9.80665, "m / s**2"),
        "m_e": ureg.Quantity(9.1093837015e-31, "kg"),
        "m_p": ureg.Quantity(1.67262192369e-27, "kg"),
        "m_n": ureg.Quantity(1.67492749804e-27, "kg"),
        "sigma_SB": ureg.Quantity(5.670374419e-8, "W / (m**2 * K**4)"),
        "F_const": ureg.Quantity(96485.33212331001, "C / mol"),
        "a_0": ureg.Quantity(5.29177210903e-11, "m"),
        "R_inf": ureg.Quantity(10973731.568160, "1 / m"),
    }


def _require_dimensionless(val: Any, fn_name: str) -> float:
    if hasattr(val, "dimensionless"):
        if not val.dimensionless:
            raise ValueError(f"Function '{fn_name}' requires a dimensionless argument, got {val.dimensionality}")
        return float(val.to_base_units().magnitude)
    return float(val)


def _safe_eval_dim_expr(expr: str, env: dict[str, Any]) -> Any:
    """Safely evaluate a physics expression over Pint Quantities using an AST whitelist (zero eval())."""
    tree = ast.parse(expr.replace("^", "**"), mode="eval")
    bin_ops = {
        ast.Add: operator.add,
        ast.Sub: operator.sub,
        ast.Mult: operator.mul,
        ast.Div: operator.truediv,
        ast.Pow: operator.pow,
    }
    un_ops = {
        ast.UAdd: operator.pos,
        ast.USub: operator.neg,
    }
    funcs = {
        "sqrt": lambda v: v ** 0.5,
        "cbrt": lambda v: v ** (1.0 / 3.0),
        "abs": abs,
        "sin": lambda v: math.sin(_require_dimensionless(v, "sin")),
        "cos": lambda v: math.cos(_require_dimensionless(v, "cos")),
        "tan": lambda v: math.tan(_require_dimensionless(v, "tan")),
        "exp": lambda v: math.exp(_require_dimensionless(v, "exp")),
        "log": lambda v: math.log(_require_dimensionless(v, "log")),
        "ln": lambda v: math.log(_require_dimensionless(v, "ln")),
        "log10": lambda v: math.log10(_require_dimensionless(v, "log10")),
    }

    def _visit(node: ast.AST) -> Any:
        if isinstance(node, ast.Expression):
            return _visit(node.body)
        if isinstance(node, ast.Constant):
            if isinstance(node.value, (int, float)):
                return float(node.value)
            raise ValueError(f"Unsupported constant in formula: {node.value!r}")
        if isinstance(node, ast.Name):
            if node.id in env:
                return env[node.id]
            raise ValueError(
                f"Unknown symbol '{node.id}'. Provide its unit in --defs (e.g. --defs \"{node.id}=...\") "
                "or use a built-in constant (h, hbar, c, k_B, R, N_A, e, epsilon_0, mu_0, G, m_e, m_p)."
            )
        if isinstance(node, ast.UnaryOp) and type(node.op) in un_ops:
            return un_ops[type(node.op)](_visit(node.operand))
        if isinstance(node, ast.BinOp) and type(node.op) in bin_ops:
            left = _visit(node.left)
            right = _visit(node.right)
            if isinstance(node.op, ast.Pow) and hasattr(right, "dimensionless"):
                right = _require_dimensionless(right, "power exponent")
            return bin_ops[type(node.op)](left, right)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            fname = node.func.id
            if fname not in funcs or node.keywords or len(node.args) != 1:
                raise ValueError(f"Unsupported function call: {fname}")
            return funcs[fname](_visit(node.args[0]))
        raise ValueError(f"Unsupported syntax node in dimensional expression: {type(node).__name__}")

    return _visit(tree)


def cmd_units(args: argparse.Namespace) -> None:
    import pint

    ureg = pint.UnitRegistry()
    raw = args.quantity.strip()
    consts = _build_physical_constants(ureg)

    def _try_parse_plain() -> Any:
        q0 = ureg.Quantity(raw)
        return q0.to(args.to) if args.to else q0.to_base_units()

    def _try_parse_consts() -> Any:
        q0 = ureg.parse_expression(raw, **consts)
        return q0.to(args.to) if args.to else q0.to_base_units()

    # If the input starts with a known physics constant identifier (e.g. "h*c/(500 nm)"),
    # prefer CODATA constants so "h" isn't misread as "hour".
    m_lead = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\b", raw)
    prefer_consts = bool(m_lead and m_lead.group(1) in consts)

    target = None
    last_err: Exception | None = None
    order = (_try_parse_consts, _try_parse_plain) if prefer_consts else (_try_parse_plain, _try_parse_consts)
    for fn in order:
        try:
            target = fn()
            break
        except Exception as exc:
            last_err = exc
            continue
    if target is None:
        raise SystemExit(f"Could not parse or convert quantity '{raw}': {last_err}")

    print(json.dumps({
        "input": args.quantity,
        "result": str(target),
        "magnitude": float(target.magnitude),
        "units": str(target.units),
        "dimensionality": str(target.dimensionality),
    }, indent=2))


def cmd_check_dim(args: argparse.Namespace) -> None:
    import pint

    ureg = pint.UnitRegistry()
    env: dict[str, Any] = _build_physical_constants(ureg)
    user_defs: dict[str, str] = {}
    if args.defs:
        for pair in args.defs.split(","):
            pair = pair.strip()
            if not pair or "=" not in pair:
                continue
            name, unit_str = pair.split("=", 1)
            k = name.strip()
            v = unit_str.strip()
            env[k] = ureg.Quantity(1.0, v)
            user_defs[k] = v

    try:
        result = _safe_eval_dim_expr(args.expr, env)
    except Exception as exc:
        raise SystemExit(f"Dimensional evaluation error: {exc}")

    if hasattr(result, "dimensionality"):
        dim = str(result.dimensionality)
        base = result.to_base_units()
        base_units = str(base.units)
        si_val = float(base.magnitude)
    else:
        dim = "dimensionless"
        base_units = "dimensionless"
        si_val = float(result)

    expected_dim = str(ureg.Quantity(1.0, args.expect).dimensionality) if args.expect else None
    print(json.dumps({
        "expr": args.expr,
        "userDefs": user_defs,
        "dimensionality": dim,
        "expected": expected_dim,
        "consistent": (dim == expected_dim) if expected_dim is not None else None,
        "baseUnits": base_units,
        "siValue": si_val,
    }, indent=2))


def cmd_error_prop(args: argparse.Namespace) -> None:
    """Propagate experimental measurement uncertainties using uncertainties.ufloat (JEE Practical Physics)."""
    from uncertainties import ufloat, umath

    env: dict[str, Any] = {"pi": math.pi}
    parsed_vars: dict[str, dict[str, float]] = {}
    for pair in args.vars_unc.split(","):
        pair = pair.strip()
        if not pair or "=" not in pair:
            continue
        name, spec = pair.split("=", 1)
        name = name.strip()
        spec = spec.strip()
        if "+/-" in spec:
            val_s, err_s = spec.split("+/-", 1)
            v, e = float(val_s.strip()), float(err_s.strip())
        elif "±" in spec:
            val_s, err_s = spec.split("±", 1)
            v, e = float(val_s.strip()), float(err_s.strip())
        else:
            v, e = float(spec), 0.0
        env[name] = ufloat(v, e)
        parsed_vars[name] = {"value": v, "absError": e, "relPercent": round((e / abs(v)) * 100.0, 4) if v != 0 else 0.0}

    tree = ast.parse(args.expr.replace("^", "**"), mode="eval")
    bin_ops = {
        ast.Add: operator.add,
        ast.Sub: operator.sub,
        ast.Mult: operator.mul,
        ast.Div: operator.truediv,
        ast.Pow: operator.pow,
    }
    un_ops = {ast.UAdd: operator.pos, ast.USub: operator.neg}
    ufuncs = {
        "sqrt": umath.sqrt,
        "sin": umath.sin,
        "cos": umath.cos,
        "tan": umath.tan,
        "exp": umath.exp,
        "log": umath.log,
        "ln": umath.log,
        "log10": umath.log10,
        "abs": abs,
    }

    def _visit(node: ast.AST) -> Any:
        if isinstance(node, ast.Expression):
            return _visit(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return float(node.value)
        if isinstance(node, ast.Name) and node.id in env:
            return env[node.id]
        if isinstance(node, ast.UnaryOp) and type(node.op) in un_ops:
            return un_ops[type(node.op)](_visit(node.operand))
        if isinstance(node, ast.BinOp) and type(node.op) in bin_ops:
            return bin_ops[type(node.op)](_visit(node.left), _visit(node.right))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ufuncs and len(node.args) == 1:
            return ufuncs[node.func.id](_visit(node.args[0]))
        raise ValueError(f"Unsupported syntax in error-prop expression: {ast.dump(node)}")

    res = _visit(tree)
    nom = float(res.nominal_value) if hasattr(res, "nominal_value") else float(res)
    std = float(res.std_dev) if hasattr(res, "std_dev") else 0.0
    # Also compute max-possible (JEE linear) worst-case bound sum(|df/dx_i| * dx_i)
    max_err = sum(abs(deriv) * var.std_dev for var, deriv in res.derivatives.items()) if hasattr(res, "derivatives") else 0.0
    print(json.dumps({
        "expr": args.expr,
        "inputs": parsed_vars,
        "nominal": nom,
        "rssStdDev": std,
        "rssRelPercent": round((std / abs(nom)) * 100.0, 4) if nom != 0 else None,
        "maxPossibleError_JEE": max_err,
        "maxRelPercent_JEE": round((max_err / abs(nom)) * 100.0, 4) if nom != 0 else None,
        "formattedRSS": f"{nom:.6g} +/- {std:.4g}",
        "formattedMaxJEE": f"{nom:.6g} +/- {max_err:.4g}",
    }, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description="Deterministic SymPy & Pint Physics/Maths Verifier")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p1 = sub.add_parser("eval", help="Evaluate / simplify a symbolic expression (diff, integrate, limit, Matrix, vectors, etc.)")
    p1.add_argument("expr")
    p1.add_argument("--vars", default="x,t,a,b,k,m,g,w,alpha,beta")
    p1.add_argument("--complex", action="store_true", help="Allow symbols to be complex instead of real")
    p1.add_argument("--latex", action="store_true", help="Parse input expression as LaTeX")
    p1.set_defaults(func=cmd_eval)

    p2 = sub.add_parser("check-eq", help="Check whether two symbolic expressions are algebraically/trig/complex equivalent")
    p2.add_argument("lhs")
    p2.add_argument("rhs")
    p2.add_argument("--vars", default="x,t,a,b,k,m,g,w,theta,alpha,beta")
    p2.add_argument("--complex", action="store_true")
    p2.add_argument("--latex", action="store_true", help="Parse lhs/rhs as LaTeX")
    p2.set_defaults(func=cmd_check_eq)

    p3 = sub.add_parser("solve", help="Solve an equation or ';' separated system (supports complex roots by default)")
    p3.add_argument("equation")
    p3.add_argument("--var", default="x", help="Variable or comma-separated variables, e.g. 'x' or 'x,y'")
    p3.add_argument("--vars", default="x,y,z,t,a,b,k,m,g,w")
    p3.add_argument("--real", action="store_true", help="Restrict solve target variable(s) to real numbers only")
    p3.set_defaults(func=cmd_solve)

    p4 = sub.add_parser("small-osc", help="Compute equilibrium, k_eff = U''(x0), and small-oscillation omega & T")
    p4.add_argument("potential", help="Potential energy U(x), e.g. 'U0*(1 - cos(alpha*x))'")
    p4.add_argument("--var", default="x")
    p4.add_argument("--mass", default="m")
    p4.add_argument("--x0", help="Specific equilibrium point (e.g. '0')")
    p4.add_argument("--vars", default="x,m,U0,alpha,a,b,k,g,L")
    p4.set_defaults(func=cmd_small_osc)

    p5 = sub.add_parser("units", help="Convert physical quantities and constants cleanly with Pint")
    p5.add_argument("quantity", help="e.g. '1 atm * L', '25 degC', or 'h*c/(500 nm)'")
    p5.add_argument("--to", help="Target unit, e.g. 'J', 'eV', or 'K' (default: SI base units)")
    p5.set_defaults(func=cmd_units)

    p6 = sub.add_parser("check-dim", help="Safely verify dimensional consistency (auto-supports h, c, k_B, R, N_A, e, epsilon_0, mu_0, G)")
    p6.add_argument("expr", help="e.g. 'sqrt(k/m)' or '1/sqrt(mu_0*epsilon_0)'")
    p6.add_argument("--defs", default="", help="Optional comma-separated var=unit pairs, e.g. 'k=N/m,m=kg'")
    p6.add_argument("--expect", help="Expected target unit, e.g. '1/s' or 'm/s'")
    p6.set_defaults(func=cmd_check_dim)

    p7 = sub.add_parser("limit", help="Compute left, right, and two-sided limits symbolically")
    p7.add_argument("expr")
    p7.add_argument("--var", default="x")
    p7.add_argument("--to", default="0", help="Limit point, e.g. '0', 'oo', '-oo', 'pi/2'")
    p7.add_argument("--dir", choices=["+-", "+", "-"], default="+-")
    p7.add_argument("--vars", default="x,t,a,b,n,k")
    p7.set_defaults(func=cmd_limit)

    p8 = sub.add_parser("series", help="Compute Taylor/Maclaurin/Laurent series expansion")
    p8.add_argument("expr")
    p8.add_argument("--var", default="x")
    p8.add_argument("--at", default="0")
    p8.add_argument("--order", type=int, default=6)
    p8.add_argument("--vars", default="x,t,a,b,k")
    p8.set_defaults(func=cmd_series)

    p9 = sub.add_parser("ode", help="Solve an ODE symbolically (supports prime notation like \"y'' + w^2*y = 0\")")
    p9.add_argument("equation")
    p9.add_argument("--func", dest="func_name", default="y")
    p9.add_argument("--var", default="t")
    p9.add_argument("--ics", help="Optional initial conditions, e.g. \"y(0)=A, y'(0)=0\"")
    p9.add_argument("--vars", default="t,x,A,B,k,w,m,g,R,L,C,alpha,beta")
    p9.set_defaults(func=cmd_ode)

    p10 = sub.add_parser("vector", help="Vector algebra: norm, unit vector, dot, cross, angle, projections, triple product")
    p10.add_argument("u", help="Vector u, e.g. '[1,2,3]' or '1,2,3'")
    p10.add_argument("v", nargs="?", help="Optional second vector v")
    p10.add_argument("w", nargs="?", help="Optional third 3D vector w for scalar triple product")
    p10.add_argument("--vars", default="x,y,z,a,b,c,k,t")
    p10.set_defaults(func=cmd_vector)

    p11 = sub.add_parser("matrix", help="Matrix algebra: det, trace, rank, inverse, RREF, eigen, Ax=b, multiplication")
    p11.add_argument("matrix", help="e.g. '[[1,2],[3,4]]'")
    p11.add_argument("--op", choices=["info", "det", "inv", "rref", "eigen", "solve", "mul"], default="info")
    p11.add_argument("--rhs", help="Right-hand vector/matrix for solve or mul")
    p11.add_argument("--vars", default="x,y,z,a,b,c,k,lambda,theta")
    p11.add_argument("--complex", action="store_true")
    p11.set_defaults(func=cmd_matrix)

    p12 = sub.add_parser("complex", help="Complex number form: rectangular, modulus, argument, conjugate, n-th roots")
    p12.add_argument("expr", help="e.g. '(1 + I)^4 / (1 - sqrt(3)*I)'")
    p12.add_argument("--roots", type=int, help="Compute n-th roots of the complex number")
    p12.add_argument("--vars", default="x,y,r,theta,a,b")
    p12.set_defaults(func=cmd_complex)

    p13 = sub.add_parser("error-prop", help="Propagate experimental measurement errors (both RSS std-dev and JEE max-error bound)")
    p13.add_argument("expr", help="Formula, e.g. '4*pi^2*L/T^2'")
    p13.add_argument("--vars-unc", required=True, help="Comma-separated name=val+/-err pairs, e.g. 'L=1.00+/-0.01, T=2.00+/-0.02'")
    p13.set_defaults(func=cmd_error_prop)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
