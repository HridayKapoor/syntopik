#!/usr/bin/env python3
"""Deterministic Physical & Inorganic Chemistry Solver for Syntopik.

Uses `chempy`, `molmass`, `scipy.optimize`, and `sympy` for exact JEE Main/Advanced
verification with zero hallucinated stoichiometry, pH approximations, or periodic trends:
  - `balance`  : Chemical & ionic redox equation balancing (acidic/basic medium)
  - `stoich`   : Limiting reagent, theoretical & % yield, excess remaining
  - `formula`  : Molar mass, empirical formula, elemental mass % composition
  - `equil`    : Kp <-> Kc conversion, reaction quotient Q, exact ICE solver
  - `ph`       : Exact charge-balance pH (strong/weak/dilute/buffer/salt) & Ksp
  - `nernst`   : Nernst equation, E_cell, Delta G, K_eq, standard E0 table & Faraday
  - `thermo`   : Delta G = Delta H - T*Delta S, van 't Hoff, Arrhenius & rate laws
  - `element`  : Exact periodic table lookup & multi-element trend comparison
"""

from __future__ import annotations

import argparse
import json
import math
import re
from typing import Any

import chempy
import molmass
import sympy as sp
from scipy.optimize import brentq

R_GAS = 8.31446261815324  # J / (mol * K)
R_LATM = 0.082057366080960  # L * atm / (mol * K)
FARADAY = 96485.33212331001  # C / mol
KW_298 = 1.0e-14
EV_TO_KJMOL = 96.48533212

# Standard reduction potentials E° (V vs SHE at 298.15 K) for common JEE half-cells.
STANDARD_E0: dict[str, tuple[float, int, str]] = {
    "li+/li": (-3.04, 1, "Li+ + e- -> Li"),
    "k+/k": (-2.93, 1, "K+ + e- -> K"),
    "ba2+/ba": (-2.91, 2, "Ba2+ + 2e- -> Ba"),
    "ca2+/ca": (-2.87, 2, "Ca2+ + 2e- -> Ca"),
    "na+/na": (-2.71, 1, "Na+ + e- -> Na"),
    "mg2+/mg": (-2.37, 2, "Mg2+ + 2e- -> Mg"),
    "al3+/al": (-1.66, 3, "Al3+ + 3e- -> Al"),
    "mn2+/mn": (-1.18, 2, "Mn2+ + 2e- -> Mn"),
    "zn2+/zn": (-0.76, 2, "Zn2+ + 2e- -> Zn"),
    "cr3+/cr": (-0.74, 3, "Cr3+ + 3e- -> Cr"),
    "fe2+/fe": (-0.44, 2, "Fe2+ + 2e- -> Fe"),
    "cd2+/cd": (-0.40, 2, "Cd2+ + 2e- -> Cd"),
    "ni2+/ni": (-0.25, 2, "Ni2+ + 2e- -> Ni"),
    "sn2+/sn": (-0.14, 2, "Sn2+ + 2e- -> Sn"),
    "pb2+/pb": (-0.13, 2, "Pb2+ + 2e- -> Pb"),
    "h+/h2": (0.00, 2, "2H+ + 2e- -> H2"),
    "cu2+/cu+": (0.15, 1, "Cu2+ + e- -> Cu+"),
    "sn4+/sn2+": (0.15, 2, "Sn4+ + 2e- -> Sn2+"),
    "cu2+/cu": (0.34, 2, "Cu2+ + 2e- -> Cu"),
    "cu+/cu": (0.52, 1, "Cu+ + e- -> Cu"),
    "i2/i-": (0.54, 2, "I2 + 2e- -> 2I-"),
    "fe3+/fe2+": (0.77, 1, "Fe3+ + e- -> Fe2+"),
    "ag+/ag": (0.80, 1, "Ag+ + e- -> Ag"),
    "hg2+/hg": (0.85, 2, "Hg2+ + 2e- -> Hg"),
    "br2/br-": (1.07, 2, "Br2 + 2e- -> 2Br-"),
    "o2/h2o": (1.23, 4, "O2 + 4H+ + 4e- -> 2H2O"),
    "cr2o7^2-/cr3+": (1.33, 6, "Cr2O7^2- + 14H+ + 6e- -> 2Cr3+ + 7H2O"),
    "cl2/cl-": (1.36, 2, "Cl2 + 2e- -> 2Cl-"),
    "au3+/au": (1.50, 3, "Au3+ + 3e- -> Au"),
    "mno4-/mn2+": (1.51, 5, "MnO4- + 8H+ + 5e- -> Mn2+ + 4H2O"),
    "f2/f-": (2.87, 2, "F2 + 2e- -> 2F-"),
}


_COMMON_POLYATOMIC_SHORTHAND = {
    "SO42-": "SO4-2",
    "SO32-": "SO3-2",
    "S2O32-": "S2O3-2",
    "S4O62-": "S4O6-2",
    "CO32-": "CO3-2",
    "C2O42-": "C2O4-2",
    "CrO42-": "CrO4-2",
    "Cr2O72-": "Cr2O7-2",
    "PO43-": "PO4-3",
    "HPO42-": "HPO4-2",
    "MnO42-": "MnO4-2",
    "O22-": "O2-2",
}


def _normalize_chempy_species(token: str) -> str:
    """Normalize species like 'Fe2+', 'MnO4-', 'Cr2O7^2-', 'CrO4^2-' into chempy syntax ('Fe+2', 'MnO4-', 'Cr2O7-2')."""
    s = token.strip()
    # Remove leading stoichiometric coefficient if user included one
    s = re.sub(r"^\d+\s+", "", s)
    # Strip phase annotations like (aq), (s), (l), (g)
    s = re.sub(r"\((?:aq|s|l|g)\)$", "", s, flags=re.IGNORECASE).strip()

    # 1. Caret charge notation: CrO4^2- -> CrO4-2, Fe^3+ -> Fe+3, MnO4^- -> MnO4-
    s = re.sub(r"\^(\d+)([+-])$", r"\2\1", s)
    s = re.sub(r"\^([+-])$", r"\1", s)

    # 2. Common caret-less polyatomic anions (e.g. SO42- -> SO4-2)
    if s in _COMMON_POLYATOMIC_SHORTHAND:
        return _COMMON_POLYATOMIC_SHORTHAND[s]

    # 3. Monoatomic cations like Fe2+, Fe3+, Mn2+, Al3+ -> Fe+2, Fe+3, Mn+2, Al+3
    #    (Leaves polyatomic ions like NH4+, H3O+, MnO4-, IO3-, NO3- completely intact!)
    m_cat = re.match(r"^([A-Z][a-z]?)(\d+)\+$", s)
    if m_cat:
        return f"{m_cat.group(1)}+{m_cat.group(2)}"

    return s


def _parse_species_composition(species: str) -> tuple[dict[str, int], int]:
    """Parse chemical species into ({element: count}, integer_charge) using chempy.Substance."""
    norm = _normalize_chempy_species(species)
    sub = chempy.Substance.from_formula(norm)
    comp: dict[str, int] = {}
    charge = 0
    for k, v in sub.composition.items():
        if k == 0:
            charge = int(v)
        else:
            el = molmass.ELEMENTS[int(k)].symbol
            comp[el] = int(v)
    return comp, charge


def _split_reaction_sides(reaction: str) -> tuple[list[str], list[str]]:
    arrow = re.search(r"(<=>|<->|⇌|->|→|=)", reaction)
    if not arrow:
        raise SystemExit(f"Reaction must contain '->', '=', or '<=>': {reaction}")
    lhs_raw = reaction[: arrow.start()]
    rhs_raw = reaction[arrow.end() :]

    def _split_side(side: str) -> list[str]:
        # Split on ' + ' (whitespace-padded +) or '+' followed by uppercase/digit, NOT charges like 'Fe+2' or 'H+ + '
        parts = re.split(r"\s+\+\s+", side.strip())
        if len(parts) == 1 and "+" in side:
            # Handle compact form like H2+O2 without breaking H+ or Fe2+
            parts = re.split(r"(?<![+-])\+(?=[A-Z(])", side.strip())
        return [p.strip() for p in parts if p.strip()]

    return _split_side(lhs_raw), _split_side(rhs_raw)


def _balance_with_nullspace(reactants: list[str], products: list[str]) -> tuple[dict[str, int], dict[str, int]]:
    """Exact SymPy rational nullspace stoichiometry balancer (supports ionic charges & redox)."""
    r_list = list(dict.fromkeys(reactants))
    p_list = list(dict.fromkeys(products))
    all_species = [(s, 1) for s in r_list] + [(s, -1) for s in p_list]

    parsed = []
    elements: list[str] = []
    has_charge = False
    for sp_str, sign in all_species:
        comp, chg = _parse_species_composition(sp_str)
        parsed.append((sp_str, sign, comp, chg))
        if chg != 0:
            has_charge = True
        for el in comp:
            if el not in elements:
                elements.append(el)

    rows = []
    for el in elements:
        rows.append([sign * comp.get(el, 0) for _, sign, comp, _ in parsed])
    if has_charge:
        rows.append([sign * chg for _, sign, _, chg in parsed])

    M = sp.Matrix(rows)
    ns = M.nullspace()
    if not ns:
        raise ValueError("No valid stoichiometric solution (check missing species or medium H+/OH-/H2O).")
    if len(ns) > 1:
        raise ValueError(
            f"Reaction is underdetermined ({len(ns)} independent solutions) — split into independent reactions."
        )

    vec = ns[0]
    denoms = [sp.fraction(sp.nsimplify(v))[1] for v in vec]
    lcm = int(sp.ilcm(*denoms))
    ints = [int(sp.nsimplify(v) * lcm) for v in vec]
    if all(x < 0 for x in ints):
        ints = [-x for x in ints]
    gcd = math.gcd(*[abs(x) for x in ints if x != 0])
    ints = [x // gcd for x in ints]

    reac_out: dict[str, int] = {}
    prod_out: dict[str, int] = {}
    for (sp_str, sign, _, _), coeff in zip(parsed, ints):
        if coeff == 0:
            continue
        if (sign == 1 and coeff > 0) or (sign == -1 and coeff < 0):
            reac_out[sp_str] = abs(coeff)
        else:
            prod_out[sp_str] = abs(coeff)
    return reac_out, prod_out


def balance_reaction_smart(reaction: str, medium: str = "neutral") -> dict[str, Any]:
    raw_r, raw_p = _split_reaction_sides(reaction)
    reac = [_normalize_chempy_species(x) for x in raw_r]
    prod = [_normalize_chempy_species(x) for x in raw_p]

    # Try chempy first on neutral/complete equations, then nullspace with medium augmentation.
    attempts: list[tuple[list[str], list[str], str]] = [(reac, prod, "direct")]
    if medium == "acidic":
        for h_side, w_side in (("r", "p"), ("p", "r"), ("r", "r"), ("p", "p")):
            rr = list(reac) + (["H+"] if h_side == "r" and "H+" not in reac else []) + (["H2O"] if w_side == "r" and "H2O" not in reac else [])
            pp = list(prod) + (["H+"] if h_side == "p" and "H+" not in prod else []) + (["H2O"] if w_side == "p" and "H2O" not in prod else [])
            attempts.append((rr, pp, "acidic-medium"))
    elif medium == "basic":
        for oh_side, w_side in (("r", "p"), ("p", "r"), ("r", "r"), ("p", "p")):
            rr = list(reac) + (["OH-"] if oh_side == "r" and "OH-" not in reac else []) + (["H2O"] if w_side == "r" and "H2O" not in reac else [])
            pp = list(prod) + (["OH-"] if oh_side == "p" and "OH-" not in prod else []) + (["H2O"] if w_side == "p" and "H2O" not in prod else [])
            attempts.append((rr, pp, "basic-medium"))

    reac_bal: dict[str, int] | None = None
    prod_bal: dict[str, int] | None = None
    method_used = ""
    last_err = ""

    # First try chempy directly if neutral
    if medium == "neutral":
        try:
            r_c, p_c = chempy.balance_stoichiometry(set(reac), set(prod))
            reac_bal = {k: int(v) for k, v in r_c.items()}
            prod_bal = {k: int(v) for k, v in p_c.items()}
            method_used = "chempy"
        except Exception as exc:
            last_err = str(exc)

    if reac_bal is None:
        for rr, pp, label in attempts:
            try:
                rb, pb = _balance_with_nullspace(rr, pp)
                # Ensure all original species are present with positive coefficients
                if all(s in rb for s in reac) and all(s in pb for s in prod):
                    reac_bal, prod_bal = rb, pb
                    method_used = f"sympy-nullspace:{label}"
                    break
            except Exception as exc:
                last_err = str(exc)
                continue

    if reac_bal is None or prod_bal is None:
        raise SystemExit(f"Could not balance reaction '{reaction}' (medium={medium}): {last_err}")

    # Verify exact atom & charge conservation
    atom_lhs: dict[str, int] = {}
    atom_rhs: dict[str, int] = {}
    chg_lhs = 0
    chg_rhs = 0
    for s, c in reac_bal.items():
        comp, chg = _parse_species_composition(s)
        chg_lhs += c * chg
        for el, cnt in comp.items():
            atom_lhs[el] = atom_lhs.get(el, 0) + c * cnt
    for s, c in prod_bal.items():
        comp, chg = _parse_species_composition(s)
        chg_rhs += c * chg
        for el, cnt in comp.items():
            atom_rhs[el] = atom_rhs.get(el, 0) + c * cnt

    def _fmt_side(d: dict[str, int]) -> str:
        return " + ".join(f"{c} {s}" if c != 1 else s for s, c in d.items())

    eq_str = f"{_fmt_side(reac_bal)} -> {_fmt_side(prod_bal)}"
    return {
        "input": reaction,
        "medium": medium,
        "method": method_used,
        "balanced": eq_str,
        "latex": rf"\ce{{{eq_str}}}",
        "reactants": reac_bal,
        "products": prod_bal,
        "atomBalance": atom_lhs,
        "netCharge": chg_lhs,
        "verified": (atom_lhs == atom_rhs and chg_lhs == chg_rhs),
    }


def cmd_balance(args: argparse.Namespace) -> None:
    res = balance_reaction_smart(args.reaction, medium=args.medium)
    print(json.dumps(res, indent=2))


def _molar_mass(species: str) -> float:
    norm = _normalize_chempy_species(species)
    # Strip charge for molmass.Formula
    clean = re.sub(r"[+-]\d*$", "", norm)
    return float(molmass.Formula(clean).mass)


def _parse_amount_to_moles(species: str, amt_str: str) -> dict[str, Any]:
    s = amt_str.strip()
    mm = _molar_mass(species)
    # Format: "50mL@0.2M" or "0.05L@0.2M"
    m_sol = re.match(r"^([0-9.eE+-]+)\s*(mL|ml|L|l)\s*@\s*([0-9.eE+-]+)\s*M$", s)
    if m_sol:
        vol = float(m_sol.group(1))
        unit = m_sol.group(2).lower()
        molarity = float(m_sol.group(3))
        vol_l = vol / 1000.0 if unit == "ml" else vol
        moles = vol_l * molarity
        return {"moles": moles, "mass_g": moles * mm, "molarMass": mm, "input": s}

    m = re.match(r"^([0-9.eE+-]+)\s*(kg|g|mg|mol|mmol)?$", s, re.IGNORECASE)
    if not m:
        raise SystemExit(f"Could not parse amount '{amt_str}' for {species}. Use e.g. '10g', '0.5mol', '50mL@0.2M'.")
    val = float(m.group(1))
    unit = (m.group(2) or "mol").lower()
    if unit == "mol":
        moles = val
    elif unit == "mmol":
        moles = val / 1000.0
    elif unit == "g":
        moles = val / mm
    elif unit == "mg":
        moles = (val / 1000.0) / mm
    elif unit == "kg":
        moles = (val * 1000.0) / mm
    else:
        raise SystemExit(f"Unsupported unit: {unit}")
    return {"moles": moles, "mass_g": moles * mm, "molarMass": mm, "input": s}


def cmd_stoich(args: argparse.Namespace) -> None:
    bal = balance_reaction_smart(args.reaction, medium=args.medium)
    reac_bal: dict[str, int] = bal["reactants"]
    prod_bal: dict[str, int] = bal["products"]

    given: dict[str, dict[str, Any]] = {}
    for pair in args.given.split(","):
        pair = pair.strip()
        if not pair or "=" not in pair:
            continue
        sp_raw, amt = pair.split("=", 1)
        sp_norm = _normalize_chempy_species(sp_raw)
        if sp_norm not in reac_bal:
            raise SystemExit(f"Given species '{sp_norm}' is not a reactant in {list(reac_bal.keys())}")
        given[sp_norm] = _parse_amount_to_moles(sp_norm, amt)

    if not given:
        raise SystemExit("Provide at least one reactant amount via --given, e.g. --given 'Fe=10g, O2=5g'")

    extents = {sp_k: info["moles"] / reac_bal[sp_k] for sp_k, info in given.items()}
    min_extent = min(extents.values())
    limiting = [sp_k for sp_k, xi in extents.items() if abs(xi - min_extent) <= 1e-12 * max(1.0, min_extent)]

    reactants_report = {}
    for sp_k, coeff in reac_bal.items():
        mm = _molar_mass(sp_k)
        consumed_mol = coeff * min_extent
        if sp_k in given:
            init_mol = given[sp_k]["moles"]
            rem_mol = max(0.0, init_mol - consumed_mol)
            reactants_report[sp_k] = {
                "coeff": coeff,
                "molarMass": round(mm, 4),
                "initialMoles": round(init_mol, 6),
                "consumedMoles": round(consumed_mol, 6),
                "remainingMoles": round(rem_mol, 6),
                "remainingGrams": round(rem_mol * mm, 4),
                "isLimiting": sp_k in limiting,
            }
        else:
            reactants_report[sp_k] = {
                "coeff": coeff,
                "molarMass": round(mm, 4),
                "requiredMoles": round(consumed_mol, 6),
                "requiredGrams": round(consumed_mol * mm, 4),
                "note": "assumed in excess",
            }

    products_report = {}
    for sp_k, coeff in prod_bal.items():
        mm = _molar_mass(sp_k)
        theo_mol = coeff * min_extent
        theo_g = theo_mol * mm
        entry: dict[str, Any] = {
            "coeff": coeff,
            "molarMass": round(mm, 4),
            "theoreticalMoles": round(theo_mol, 6),
            "theoreticalGrams": round(theo_g, 4),
        }
        if args.yield_pct is not None:
            factor = float(args.yield_pct) / 100.0
            entry["actualMoles"] = round(theo_mol * factor, 6)
            entry["actualGrams"] = round(theo_g * factor, 4)
        products_report[sp_k] = entry

    print(json.dumps({
        "balanced": bal["balanced"],
        "latex": bal["latex"],
        "limitingReagent": limiting,
        "reactionExtentMoles": round(min_extent, 6),
        "reactants": reactants_report,
        "products": products_report,
    }, indent=2))


def cmd_formula(args: argparse.Namespace) -> None:
    f = molmass.Formula(args.formula)
    comp = [
        {
            "element": sym,
            "count": item.count,
            "mass": round(item.mass, 4),
            "massPercent": round(item.fraction * 100.0, 3),
        }
        for sym, item in f.composition().items()
    ]
    print(json.dumps({
        "input": args.formula,
        "formula": f.formula,
        "empirical": f.empirical,
        "molarMass_g_mol": round(f.mass, 4),
        "monoisotopicMass": round(f.isotope.mass, 5),
        "atoms": f.atoms,
        "composition": comp,
    }, indent=2))


def cmd_equil(args: argparse.Namespace) -> None:
    T = float(args.temp)
    out: dict[str, Any] = {"temperatureK": T}

    if args.kc is not None and args.delta_ng is not None:
        kc = float(args.kc)
        dng = float(args.delta_ng)
        kp = kc * ((R_LATM * T) ** dng)
        dg0_kj = -R_GAS * T * math.log(kc) / 1000.0
        out.update({
            "Kc": kc,
            "delta_ng": dng,
            "Kp_atm": kp,
            "deltaG0_kJ_mol_from_Kc": round(dg0_kj, 4),
        })
    elif args.kp is not None and args.delta_ng is not None:
        kp = float(args.kp)
        dng = float(args.delta_ng)
        kc = kp / ((R_LATM * T) ** dng)
        dg0_kj = -R_GAS * T * math.log(kp) / 1000.0
        out.update({
            "Kp_atm": kp,
            "delta_ng": dng,
            "Kc": kc,
            "deltaG0_kJ_mol_from_Kp": round(dg0_kj, 4),
        })

    if args.reaction and args.init and args.kc is not None:
        bal = balance_reaction_smart(args.reaction)
        reac: dict[str, int] = bal["reactants"]
        prod: dict[str, int] = bal["products"]
        vol = float(args.volume)
        init_moles: dict[str, float] = {k: 0.0 for k in list(reac) + list(prod)}
        for pair in args.init.split(","):
            pair = pair.strip()
            if not pair or "=" not in pair:
                continue
            k, v = pair.split("=", 1)
            init_moles[_normalize_chempy_species(k)] = float(v.strip())

        kc = float(args.kc)
        # Reaction quotient Q at initial state
        q_num = math.prod(((init_moles[p] / vol) ** c) for p, c in prod.items())
        q_den = math.prod(((init_moles[r] / vol) ** c) for r, c in reac.items())
        q_val = float("inf") if q_den == 0 else q_num / q_den

        # Extent bounds: every species concentration must remain > 0
        xi_min = max(-init_moles[p] / c for p, c in prod.items())
        xi_max = min(init_moles[r] / c for r, c in reac.items())
        eps = 1e-12 * max(1.0, abs(xi_max - xi_min))

        def residual(xi: float) -> float:
            num = math.prod((((init_moles[p] + c * xi) / vol) ** c) for p, c in prod.items())
            den = math.prod((((init_moles[r] - c * xi) / vol) ** c) for r, c in reac.items())
            return math.log(max(num, 1e-300)) - math.log(max(den, 1e-300)) - math.log(kc)

        lo = xi_min + eps
        hi = xi_max - eps
        xi_eq = float(brentq(residual, lo, hi, xtol=1e-12, rtol=1e-12))

        eq_moles = {}
        eq_conc = {}
        for r, c in reac.items():
            m = init_moles[r] - c * xi_eq
            eq_moles[r] = round(m, 6)
            eq_conc[r] = round(m / vol, 6)
        for p, c in prod.items():
            m = init_moles[p] + c * xi_eq
            eq_moles[p] = round(m, 6)
            eq_conc[p] = round(m / vol, 6)

        direction = "forward (Q < Kc)" if q_val < kc else ("reverse (Q > Kc)" if q_val > kc else "at equilibrium (Q = Kc)")
        out.update({
            "balanced": bal["balanced"],
            "volumeL": vol,
            "initialMoles": init_moles,
            "initialQ": None if math.isinf(q_val) else round(q_val, 6),
            "shiftDirection": direction,
            "equilibriumExtentMoles": round(xi_eq, 6),
            "equilibriumMoles": eq_moles,
            "equilibriumConc_M": eq_conc,
        })

    if len(out) == 1:
        raise SystemExit("Provide --kc/--kp with --delta-ng, and/or --reaction + --init + --kc for ICE table solving.")
    print(json.dumps(out, indent=2))


def _solve_exact_acid_h(ca: float, ka: float, kw: float = KW_298) -> float:
    """Exact charge balance for monoprotic weak acid: [H+] = Kw/[H+] + Ca*Ka/([H+] + Ka)."""
    def f(h: float) -> float:
        return h - kw / h - (ca * ka) / (h + ka)
    return float(brentq(f, 1e-14, max(ca + 1.0, 10.0), xtol=1e-15, rtol=1e-13))


def _solve_exact_buffer_h(ca: float, cb: float, ka: float, kw: float = KW_298) -> float:
    """Exact charge balance for weak acid (Ca) + conjugate base salt (Cb): [H+] + Cb = Kw/[H+] + (Ca+Cb)*Ka/([H+]+Ka)."""
    c_tot = ca + cb
    def f(h: float) -> float:
        return h + cb - kw / h - (c_tot * ka) / (h + ka)
    return float(brentq(f, 1e-15, max(c_tot + 1.0, 10.0), xtol=1e-15, rtol=1e-13))


def cmd_ph(args: argparse.Namespace) -> None:
    kw = float(args.kw)
    pkw = -math.log10(kw)
    mode = args.mode
    out: dict[str, Any] = {"mode": mode, "Kw": kw}

    if mode == "strong-acid":
        c = float(args.conc)
        # Exact quadratic: [H+]^2 - c[H+] - Kw = 0 (avoids the classic 10^-8 M HCl -> pH 8 JEE trap!)
        h = 0.5 * (c + math.sqrt(c * c + 4.0 * kw))
        ph = -math.log10(h)
        out.update({
            "conc_M": c,
            "H_plus_M": h,
            "OH_minus_M": kw / h,
            "pH": round(ph, 4),
            "pOH": round(pkw - ph, 4),
            "waterAutoionizationSignificant": c < 1e-6,
        })
    elif mode == "strong-base":
        c = float(args.conc)
        oh = 0.5 * (c + math.sqrt(c * c + 4.0 * kw))
        poh = -math.log10(oh)
        ph = pkw - poh
        out.update({
            "conc_M": c,
            "OH_minus_M": oh,
            "H_plus_M": kw / oh,
            "pOH": round(poh, 4),
            "pH": round(ph, 4),
            "waterAutoionizationSignificant": c < 1e-6,
        })
    elif mode == "weak-acid":
        c = float(args.conc)
        ka = float(args.ka) if args.ka is not None else 10.0 ** (-float(args.pka))
        h_exact = _solve_exact_acid_h(c, ka, kw)
        alpha = (ka / (h_exact + ka))
        h_approx = math.sqrt(ka * c)
        ph_exact = -math.log10(h_exact)
        ph_approx = -math.log10(h_approx)
        out.update({
            "conc_M": c,
            "Ka": ka,
            "pKa": round(-math.log10(ka), 4),
            "H_plus_exact_M": h_exact,
            "pH_exact": round(ph_exact, 4),
            "pOH_exact": round(pkw - ph_exact, 4),
            "degreeOfDissociation_alpha": round(alpha, 5),
            "naiveSqrtKaC_pH": round(ph_approx, 4),
            "fivePercentRuleValid": alpha < 0.05 and c > 1e-6,
        })
    elif mode == "weak-base":
        c = float(args.conc)
        kb = float(args.kb) if args.kb is not None else 10.0 ** (-float(args.pkb))
        oh_exact = _solve_exact_acid_h(c, kb, kw)
        alpha = kb / (oh_exact + kb)
        poh_exact = -math.log10(oh_exact)
        ph_exact = pkw - poh_exact
        out.update({
            "conc_M": c,
            "Kb": kb,
            "pKb": round(-math.log10(kb), 4),
            "OH_minus_exact_M": oh_exact,
            "pOH_exact": round(poh_exact, 4),
            "pH_exact": round(ph_exact, 4),
            "degreeOfDissociation_alpha": round(alpha, 5),
            "fivePercentRuleValid": alpha < 0.05 and c > 1e-6,
        })
    elif mode == "buffer":
        ca = float(args.acid_conc)
        cb = float(args.base_conc)
        if args.ka is not None or args.pka is not None:
            ka = float(args.ka) if args.ka is not None else 10.0 ** (-float(args.pka))
            pka = -math.log10(ka)
            ph_hh = pka + math.log10(cb / ca)
            h_exact = _solve_exact_buffer_h(ca, cb, ka, kw)
            ph_exact = -math.log10(h_exact)
            c_tot = ca + cb
            beta = 2.303 * (kw / h_exact + h_exact + (c_tot * ka * h_exact) / ((ka + h_exact) ** 2))
            out.update({
                "bufferType": "acidic (HA / A-)",
                "acidConc_M": ca,
                "conjugateBaseConc_M": cb,
                "pKa": round(pka, 4),
                "pH_HendersonHasselbalch": round(ph_hh, 4),
                "pH_exactChargeBalance": round(ph_exact, 4),
                "bufferCapacity_mol_L_pH": round(beta, 5),
            })
        elif args.kb is not None or args.pkb is not None:
            kb = float(args.kb) if args.kb is not None else 10.0 ** (-float(args.pkb))
            pkb = -math.log10(kb)
            poh_hh = pkb + math.log10(ca / cb)  # ca = conjugate acid BH+, cb = weak base B
            oh_exact = _solve_exact_buffer_h(cb, ca, kb, kw)
            poh_exact = -math.log10(oh_exact)
            out.update({
                "bufferType": "basic (B / BH+)",
                "baseConc_M": cb,
                "conjugateAcidConc_M": ca,
                "pKb": round(pkb, 4),
                "pH_HendersonHasselbalch": round(pkw - poh_hh, 4),
                "pH_exactChargeBalance": round(pkw - poh_exact, 4),
            })
        else:
            raise SystemExit("Buffer mode requires --ka/--pka (acidic buffer) or --kb/--pkb (basic buffer).")
    elif mode == "ksp":
        ksp = float(args.ksp)
        m = int(args.m)
        n = int(args.n)
        common = float(args.common_ion)
        # Salt A_m B_n -> m A + n B, optional common ion on B
        def f_sol(s: float) -> float:
            return ((m * s) ** m) * ((n * s + common) ** n) - ksp
        hi = max(1.0, (ksp / ((m ** m) * (n ** n))) ** (1.0 / (m + n)) * 10.0 + 1.0)
        sol = float(brentq(f_sol, 0.0, hi, xtol=1e-18, rtol=1e-13))
        pure_sol = (ksp / ((m ** m) * (n ** n))) ** (1.0 / (m + n))
        out.update({
            "saltStoichiometry": f"A_{m}B_{n}",
            "Ksp": ksp,
            "commonIonConc_M": common,
            "molarSolubility_M": sol,
            "pureWaterSolubility_M": pure_sol,
            "suppressionFactor": round(pure_sol / sol, 3) if sol > 0 else None,
        })
    else:
        raise SystemExit(f"Unknown pH mode: {mode}")

    print(json.dumps(out, indent=2))


def cmd_nernst(args: argparse.Namespace) -> None:
    T = float(args.temp)
    out: dict[str, Any] = {"temperatureK": T}

    if args.list_potentials:
        print(json.dumps({
            k: {"E0_V": v[0], "n": v[1], "halfReaction": v[2]}
            for k, v in STANDARD_E0.items()
        }, indent=2))
        return

    e0 = args.e0
    n = args.n
    if args.cathode and args.anode:
        ck = args.cathode.strip().lower()
        ak = args.anode.strip().lower()
        if ck not in STANDARD_E0 or ak not in STANDARD_E0:
            raise SystemExit(
                "Unknown half-cell. Run `nernst --list-potentials` to see built-in JEE half-cells."
            )
        ec, nc, rc = STANDARD_E0[ck]
        ea, na, ra = STANDARD_E0[ak]
        e0 = round(ec - ea, 4)
        n = n or math.lcm(nc, na)
        out["cathode"] = {"couple": args.cathode, "E0_V": ec, "reaction": rc}
        out["anode"] = {"couple": args.anode, "E0_V": ea, "reaction": ra}

    if e0 is not None and n is not None:
        e0 = float(e0)
        n = int(n)
        q = float(args.q)
        slope_log10 = 2.302585092994046 * R_GAS * T / FARADAY
        e_cell = e0 - (R_GAS * T / (n * FARADAY)) * math.log(q)
        dg0_kj = -n * FARADAY * e0 / 1000.0
        dg_kj = -n * FARADAY * e_cell / 1000.0
        log10_k = (n * e0) / slope_log10
        out.update({
            "E0_cell_V": round(e0, 5),
            "n_electrons": n,
            "Q": q,
            "nernstFactor_2.303RT_over_F_V": round(slope_log10, 5),
            "E_cell_V": round(e_cell, 5),
            "deltaG0_kJ_mol": round(dg0_kj, 4),
            "deltaG_kJ_mol": round(dg_kj, 4),
            "log10_Keq": round(log10_k, 4),
            "Keq": (10.0 ** log10_k) if abs(log10_k) < 280 else f"10^{log10_k:.2f}",
            "spontaneous": e_cell > 0,
        })

    if args.current is not None and args.time is not None:
        I = float(args.current)
        t = float(args.time)
        charge_c = I * t
        faradays = charge_c / FARADAY
        faraday_out: dict[str, Any] = {
            "current_A": I,
            "time_s": t,
            "charge_C": round(charge_c, 4),
            "molesElectrons_F": round(faradays, 6),
        }
        if args.n and args.molar_mass:
            moles_dep = faradays / int(args.n)
            mass_g = moles_dep * float(args.molar_mass)
            faraday_out["molesDeposited"] = round(moles_dep, 6)
            faraday_out["massDeposited_g"] = round(mass_g, 5)
        out["faradayElectrolysis"] = faraday_out

    if len(out) == 1:
        raise SystemExit("Provide --e0 & --n (or --cathode & --anode), or --current & --time for Faraday electrolysis.")
    print(json.dumps(out, indent=2))


def cmd_thermo(args: argparse.Namespace) -> None:
    mode = args.mode
    out: dict[str, Any] = {"mode": mode}

    if mode == "gibbs":
        dh = float(args.dh)  # kJ/mol
        ds = float(args.ds)  # J/(mol*K)
        T = float(args.temp)
        dg = dh - T * (ds / 1000.0)
        ln_k = -(dg * 1000.0) / (R_GAS * T)
        t_cross = (dh * 1000.0) / ds if abs(ds) > 1e-12 else None
        out.update({
            "deltaH_kJ_mol": dh,
            "deltaS_J_mol_K": ds,
            "temperatureK": T,
            "deltaG_kJ_mol": round(dg, 4),
            "spontaneous": dg < 0,
            "crossoverTempK": round(t_cross, 2) if (t_cross is not None and t_cross > 0) else None,
            "lnK": round(ln_k, 4),
            "Keq": math.exp(ln_k) if abs(ln_k) < 600 else f"exp({ln_k:.2f})",
        })
    elif mode == "arrhenius":
        # Two-temperature Arrhenius: ln(k2/k1) = (Ea/R)*(1/T1 - 1/T2)
        if args.t1 is not None and args.t2 is not None:
            t1, t2 = float(args.t1), float(args.t2)
            inv_diff = (1.0 / t1) - (1.0 / t2)
            if args.ea is not None and args.k1 is not None:
                ea_j = float(args.ea) * 1000.0
                k1 = float(args.k1)
                ratio = math.exp((ea_j / R_GAS) * inv_diff)
                k2 = k1 * ratio
                out.update({"Ea_kJ_mol": float(args.ea), "T1_K": t1, "T2_K": t2, "k1": k1, "k2": k2, "ratio_k2_k1": round(ratio, 5)})
            elif args.k1 is not None and args.k2 is not None:
                k1, k2 = float(args.k1), float(args.k2)
                ea_kj = (R_GAS * math.log(k2 / k1) / inv_diff) / 1000.0
                out.update({"T1_K": t1, "T2_K": t2, "k1": k1, "k2": k2, "Ea_kJ_mol": round(ea_kj, 4)})
            else:
                raise SystemExit("Two-T Arrhenius needs (--t1, --t2, --k1, --ea) OR (--t1, --t2, --k1, --k2).")
        elif args.a_factor is not None and args.ea is not None:
            A = float(args.a_factor)
            ea_j = float(args.ea) * 1000.0
            T = float(args.temp)
            k = A * math.exp(-ea_j / (R_GAS * T))
            out.update({"A": A, "Ea_kJ_mol": float(args.ea), "temperatureK": T, "k": k})
        else:
            raise SystemExit("Provide (--a-factor, --ea, --temp) OR two-temperature (--t1, --t2, --k1, --ea/--k2).")
    elif mode == "vant-hoff":
        t1, t2 = float(args.t1), float(args.t2)
        inv_diff = (1.0 / t1) - (1.0 / t2)
        if args.dh is not None and args.k1 is not None:
            dh_j = float(args.dh) * 1000.0
            k1 = float(args.k1)
            k2 = k1 * math.exp((dh_j / R_GAS) * inv_diff)
            out.update({"deltaH_kJ_mol": float(args.dh), "T1_K": t1, "T2_K": t2, "K1": k1, "K2": k2})
        elif args.k1 is not None and args.k2 is not None:
            k1, k2 = float(args.k1), float(args.k2)
            dh_kj = (R_GAS * math.log(k2 / k1) / inv_diff) / 1000.0
            out.update({"T1_K": t1, "T2_K": t2, "K1": k1, "K2": k2, "deltaH_kJ_mol": round(dh_kj, 4)})
        else:
            raise SystemExit("van 't Hoff needs --t1, --t2, and (--k1 + --dh) OR (--k1 + --k2).")
    elif mode == "kinetics":
        order = int(args.order)
        k = float(args.k1)
        a0 = float(args.a0)
        t = float(args.time)
        if order == 0:
            at = max(0.0, a0 - k * t)
            t_half = a0 / (2.0 * k)
        elif order == 1:
            at = a0 * math.exp(-k * t)
            t_half = math.log(2.0) / k
        elif order == 2:
            at = a0 / (1.0 + k * a0 * t)
            t_half = 1.0 / (k * a0)
        else:
            raise SystemExit("Supported reaction orders: 0, 1, 2.")
        out.update({
            "order": order,
            "k": k,
            "A0": a0,
            "time": t,
            "A_t": round(at, 6),
            "fractionRemaining": round(at / a0, 6) if a0 > 0 else 0.0,
            "halfLife": round(t_half, 6),
        })
    print(json.dumps(out, indent=2))


def _lookup_element(query: str) -> dict[str, Any]:
    q = query.strip()
    el = None
    if q.isdigit():
        num = int(q)
        if 1 <= num <= len(molmass.ELEMENTS):
            el = molmass.ELEMENTS[num]
    else:
        for cand in (q, q.capitalize()):
            try:
                el = molmass.ELEMENTS[cand]
                break
            except KeyError:
                pass
        if el is None:
            ql = q.lower()
            for item in molmass.ELEMENTS:
                if item.name.lower() == ql or item.symbol.lower() == ql:
                    el = item
                    break
    if el is None:
        raise SystemExit(f"Element not found: '{query}'")

    ies_ev = [round(x, 4) for x in el.ionenergy[:4]] if el.ionenergy else []
    ies_kj = [round(x * EV_TO_KJMOL, 1) for x in el.ionenergy[:4]] if el.ionenergy else []
    ea_ev = round(el.eleaffin, 4) if el.eleaffin is not None else None
    return {
        "atomicNumber": el.number,
        "symbol": el.symbol,
        "name": el.name,
        "atomicMass": round(el.mass, 4),
        "group": el.group,
        "period": el.period,
        "block": el.block,
        "electronConfig": el.eleconfig,
        "electronegativityPauling": el.eleneg if el.eleneg else None,
        "electronAffinity_eV": ea_ev,
        "electronAffinity_kJ_mol": round(ea_ev * EV_TO_KJMOL, 1) if ea_ev is not None else None,
        "ionizationEnergies_eV": ies_ev,
        "ionizationEnergies_kJ_mol": ies_kj,
        "oxidationStates": el.oxistates,
        "atomicRadius_A": el.atmrad if el.atmrad else None,
        "covalentRadius_A": el.covrad if el.covrad else None,
        "vdwRadius_A": el.vdwrad if el.vdwrad else None,
        "meltingPoint_K": el.tmelt if el.tmelt else None,
        "boilingPoint_K": el.tboil if el.tboil else None,
        "density_g_cm3": el.density if el.density else None,
    }


def cmd_element(args: argparse.Namespace) -> None:
    queries: list[str] = []
    for raw in args.elements:
        queries.extend(p.strip() for p in raw.split(",") if p.strip())
    records = [_lookup_element(q) for q in queries]
    if len(records) == 1:
        print(json.dumps(records[0], indent=2))
    else:
        print(json.dumps({
            "count": len(records),
            "elements": records,
            "trends": {
                "byElectronegativity": [r["symbol"] for r in sorted(records, key=lambda x: x["electronegativityPauling"] or -1)],
                "byFirstIE": [r["symbol"] for r in sorted(records, key=lambda x: (x["ionizationEnergies_eV"][0] if x["ionizationEnergies_eV"] else -1))],
                "byAtomicRadius": [r["symbol"] for r in sorted(records, key=lambda x: x["atomicRadius_A"] or -1)],
            },
        }, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description="Deterministic Physical & Inorganic Chemistry Solver (chempy + molmass + SciPy)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p1 = sub.add_parser("balance", help="Balance chemical or ionic redox equations (neutral/acidic/basic)")
    p1.add_argument("reaction", help="e.g. 'KMnO4 + FeSO4 + H2SO4 -> K2SO4 + MnSO4 + Fe2(SO4)3 + H2O' or 'MnO4- + Fe2+ -> Mn2+ + Fe3+'")
    p1.add_argument("--medium", choices=["neutral", "acidic", "basic"], default="neutral")
    p1.set_defaults(func=cmd_balance)

    p2 = sub.add_parser("stoich", help="Limiting reagent, theoretical/actual yield & remaining excess")
    p2.add_argument("reaction", help="Reaction (auto-balanced if unbalanced)")
    p2.add_argument("--given", required=True, help="Comma-separated amounts, e.g. 'Fe=10g, O2=5g' or 'HCl=50mL@0.2M, NaOH=0.02mol'")
    p2.add_argument("--medium", choices=["neutral", "acidic", "basic"], default="neutral")
    p2.add_argument("--yield-pct", type=float, help="Optional percent yield (e.g. 85)")
    p2.set_defaults(func=cmd_stoich)

    p3 = sub.add_parser("formula", help="Molar mass, empirical formula, and elemental mass percent composition")
    p3.add_argument("formula", help="e.g. 'K2Cr2O7' or 'CuSO4.5H2O'")
    p3.set_defaults(func=cmd_formula)

    p4 = sub.add_parser("equil", help="Equilibrium Kc <-> Kp, Delta G0, Q vs K, and exact ICE table solver")
    p4.add_argument("--kc", type=float)
    p4.add_argument("--kp", type=float)
    p4.add_argument("--delta-ng", type=float, help="Change in gaseous moles (products - reactants)")
    p4.add_argument("--temp", type=float, default=298.15, help="Temperature in K (default 298.15)")
    p4.add_argument("--reaction", help="Optional reaction for ICE table solver")
    p4.add_argument("--init", help="Initial moles for ICE table, e.g. 'N2=1, H2=3, NH3=0'")
    p4.add_argument("--volume", type=float, default=1.0, help="Container volume in L (default 1.0)")
    p4.set_defaults(func=cmd_equil)

    p5 = sub.add_parser("ph", help="Exact charge-balance pH (strong/weak/dilute/buffer) & Ksp solubility")
    p5.add_argument("mode", choices=["strong-acid", "strong-base", "weak-acid", "weak-base", "buffer", "ksp"])
    p5.add_argument("--conc", type=float, help="Molar concentration (M)")
    p5.add_argument("--ka", type=float)
    p5.add_argument("--pka", type=float)
    p5.add_argument("--kb", type=float)
    p5.add_argument("--pkb", type=float)
    p5.add_argument("--acid-conc", type=float, help="Acid (or conjugate acid) concentration for buffer (M)")
    p5.add_argument("--base-conc", type=float, help="Base (or conjugate base) concentration for buffer (M)")
    p5.add_argument("--ksp", type=float, help="Solubility product Ksp")
    p5.add_argument("--m", type=int, default=1, help="Cation stoichiometry m in A_m B_n")
    p5.add_argument("--n", type=int, default=1, help="Anion stoichiometry n in A_m B_n")
    p5.add_argument("--common-ion", type=float, default=0.0, help="Common anion concentration (M)")
    p5.add_argument("--kw", type=float, default=KW_298, help="Water autoionization constant Kw (default 1e-14)")
    p5.set_defaults(func=cmd_ph)

    p6 = sub.add_parser("nernst", help="Nernst equation, E0_cell lookup, Delta G, Keq, and Faraday electrolysis")
    p6.add_argument("--e0", type=float, help="Standard cell potential E0_cell (V)")
    p6.add_argument("--n", type=int, help="Electrons transferred (n)")
    p6.add_argument("--q", type=float, default=1.0, help="Reaction quotient Q (default 1.0)")
    p6.add_argument("--temp", type=float, default=298.15, help="Temperature in K (default 298.15)")
    p6.add_argument("--cathode", help="Built-in cathode reduction couple, e.g. 'Cu2+/Cu'")
    p6.add_argument("--anode", help="Built-in anode reduction couple, e.g. 'Zn2+/Zn'")
    p6.add_argument("--list-potentials", action="store_true", help="List built-in standard reduction potentials")
    p6.add_argument("--current", type=float, help="Electrolysis current in Amperes (A)")
    p6.add_argument("--time", type=float, help="Electrolysis duration in seconds (s)")
    p6.add_argument("--molar-mass", type=float, help="Molar mass (g/mol) of deposited species")
    p6.set_defaults(func=cmd_nernst)

    p7 = sub.add_parser("thermo", help="Thermodynamics (Delta G = Delta H - T*Delta S, van 't Hoff) & Arrhenius kinetics")
    p7.add_argument("mode", choices=["gibbs", "arrhenius", "vant-hoff", "kinetics"])
    p7.add_argument("--dh", type=float, help="Delta H in kJ/mol")
    p7.add_argument("--ds", type=float, help="Delta S in J/(mol*K)")
    p7.add_argument("--temp", type=float, default=298.15, help="Temperature in K")
    p7.add_argument("--t1", type=float, help="Initial temperature T1 (K)")
    p7.add_argument("--t2", type=float, help="Final temperature T2 (K)")
    p7.add_argument("--k1", type=float, help="Rate constant k1 or equilibrium constant K1")
    p7.add_argument("--k2", type=float, help="Rate constant k2 or equilibrium constant K2")
    p7.add_argument("--ea", type=float, help="Activation energy Ea in kJ/mol")
    p7.add_argument("--a-factor", type=float, help="Pre-exponential factor A")
    p7.add_argument("--order", type=int, default=1, choices=[0, 1, 2])
    p7.add_argument("--a0", type=float, default=1.0, help="Initial concentration [A]0")
    p7.add_argument("--time", type=float, default=1.0, help="Elapsed time t")
    p7.set_defaults(func=cmd_thermo)

    p8 = sub.add_parser("element", help="Exact periodic table lookup & multi-element trend comparison")
    p8.add_argument("elements", nargs="+", help="Symbol(s), atomic number(s), or name(s), e.g. 'Fe' or 'N O F'")
    p8.set_defaults(func=cmd_element)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
