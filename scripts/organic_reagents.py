#!/usr/bin/env python3
"""Organic Chemistry Reagent & Named-Reaction Discrimination Matrix CLI.

Provides deterministic lookup, side-by-side comparison, chemoselectivity queries,
zero-spoiler LearnHarness discrimination drills, and RDKit 2D vector comparison
grids (PNG/SVG — zero ASCII chemical rings!) over the curated JEE Main & Advanced
reagent database (`knowledge-graph/reagents.json`).

Subcommands:
  - `list`        : List all curated reagents (filterable by category)
  - `lookup`      : Exact, alias, or fuzzy lookup of a reagent/reaction system
  - `compare`     : Side-by-side comparison of 2–4 reagents + optional RDKit 2D grid
  - `selectivity` : Show which reagents react with vs leave a functional group intact
  - `drill-pair`  : Emit a zero-spoiler LearnHarness discrimination drill for 2 reagents
"""

from __future__ import annotations

import argparse
import difflib
import html
import io
import json
import re
import sys
import textwrap
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont
from rdkit import Chem
from rdkit.Chem import AllChem, rdCIPLabeler, rdMolDescriptors
from rdkit.Chem.Draw import rdMolDraw2D

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "knowledge-graph" / "reagents.json"

VALID_CATEGORIES = (
    "reduction",
    "oxidation",
    "addition",
    "substitution_elimination",
    "carbonyl_cc",
)

VALID_STEREOCHEMISTRY = (
    "syn",
    "anti",
    "inversion",
    "retention",
    "racemization",
    "E-selective",
    "Z-selective",
    "none",
    "depends",
)

VALID_REARRANGEMENT_RISKS = ("none", "low", "high")

FUNCTIONAL_GROUP_ALIASES: dict[str, str] = {
    "aldehyde": "aldehyde",
    "aldehydes": "aldehyde",
    "cho": "aldehyde",
    "rcho": "aldehyde",
    "ketone": "ketone",
    "ketones": "ketone",
    "co": "ketone",
    "rcor": "ketone",
    "ester": "ester",
    "esters": "ester",
    "coor": "ester",
    "rcoor": "ester",
    "carboxylic_acid": "carboxylic_acid",
    "carboxylic acid": "carboxylic_acid",
    "carboxylic acids": "carboxylic_acid",
    "acid": "carboxylic_acid",
    "cooh": "carboxylic_acid",
    "rcooh": "carboxylic_acid",
    "acid_chloride": "acid_chloride",
    "acid chloride": "acid_chloride",
    "acyl chloride": "acid_chloride",
    "acyl_chloride": "acid_chloride",
    "cocl": "acid_chloride",
    "rcocl": "acid_chloride",
    "amide": "amide",
    "amides": "amide",
    "conh2": "amide",
    "nitrile": "nitrile",
    "nitriles": "nitrile",
    "cyanide": "nitrile",
    "cn": "nitrile",
    "rcn": "nitrile",
    "alkene": "alkene",
    "alkenes": "alkene",
    "double bond": "alkene",
    "c=c": "alkene",
    "alkyne": "alkyne",
    "alkynes": "alkyne",
    "triple bond": "alkyne",
    "c#c": "alkyne",
    "epoxide": "epoxide",
    "epoxides": "epoxide",
    "oxirane": "epoxide",
    "nitro": "nitro",
    "no2": "nitro",
    "rno2": "nitro",
    "alkyl_halide": "alkyl_halide",
    "alkyl halide": "alkyl_halide",
    "alkyl halides": "alkyl_halide",
    "halide": "alkyl_halide",
    "haloalkane": "alkyl_halide",
    "rx": "alkyl_halide",
    "r-x": "alkyl_halide",
    "alcohol": "alcohol",
    "alcohols": "alcohol",
    "oh": "alcohol",
    "roh": "alcohol",
}

FG_DISPLAY: dict[str, str] = {
    "aldehyde": "Aldehyde (-CHO)",
    "ketone": "Ketone (C=O)",
    "ester": "Ester (-COOR)",
    "carboxylic_acid": "Carboxylic Acid (-COOH)",
    "acid_chloride": "Acid Chloride (-COCl)",
    "amide": "Amide (-CONR2)",
    "nitrile": "Nitrile (-C≡N)",
    "alkene": "Alkene (C=C)",
    "alkyne": "Alkyne (C≡C)",
    "epoxide": "Epoxide",
    "nitro": "Nitro (-NO2)",
    "alkyl_halide": "Alkyl Halide (R-X)",
    "alcohol": "Alcohol (-OH)",
}


def _norm(token: str) -> str:
    """Normalize a reagent or functional-group token for resilient matching."""
    s = token.strip().lower()
    s = s.replace("°", "").replace("·", "").replace("−", "-").replace("–", "-")
    s = re.sub(r"[\s_\-/\\.,()+':;]+", "", s)
    return s


def load_reagents_db(db_path: str | Path = DEFAULT_DB) -> dict[str, Any]:
    path = Path(db_path).expanduser().resolve()
    if not path.exists():
        raise SystemExit(f"Reagents database not found: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid JSON in reagents database {path}: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("reagents"), list):
        raise SystemExit(f"Malformed reagents database schema in {path}")
    return data


def normalize_functional_group(raw: str, valid_groups: list[str]) -> str:
    cleaned = " ".join(raw.strip().lower().replace("-", " ").split())
    under = cleaned.replace(" ", "_")
    if under in valid_groups:
        return under
    if cleaned in FUNCTIONAL_GROUP_ALIASES and FUNCTIONAL_GROUP_ALIASES[cleaned] in valid_groups:
        return FUNCTIONAL_GROUP_ALIASES[cleaned]
    if raw.strip().lower() in FUNCTIONAL_GROUP_ALIASES:
        mapped = FUNCTIONAL_GROUP_ALIASES[raw.strip().lower()]
        if mapped in valid_groups:
            return mapped
    close = difflib.get_close_matches(under, valid_groups, n=3, cutoff=0.5)
    hint = f" Did you mean: {', '.join(close)}?" if close else ""
    raise SystemExit(
        f"Unknown functional group '{raw}'. Valid groups: {', '.join(valid_groups)}.{hint}"
    )


def resolve_reagent(db: dict[str, Any], query: str) -> tuple[dict[str, Any], str]:
    """Resolve query to (reagent_dict, match_reason) via exact ID, formula/name/alias, normalized token, or fuzzy fallback."""
    reagents: list[dict[str, Any]] = db["reagents"]
    q_raw = query.strip()
    q_low = q_raw.lower()
    q_norm = _norm(q_raw)

    if not q_raw:
        raise SystemExit("Reagent query cannot be empty.")

    # 1. Exact ID match
    for r in reagents:
        if r["id"].lower() == q_low:
            return r, "exact_id"

    # 2. Exact formula, name, or alias match (case-insensitive)
    for r in reagents:
        if r["formula"].lower() == q_low:
            return r, "exact_formula"
        if r["name"].lower() == q_low:
            return r, "exact_name"
        for a in r.get("aliases", []):
            if a.lower() == q_low:
                return r, f"exact_alias:{a}"

    # 3. Normalized punctuation-insensitive match
    norm_hits: list[tuple[dict[str, Any], str]] = []
    for r in reagents:
        candidates = [(r["id"], "norm_id"), (r["formula"], "norm_formula"), (r["name"], "norm_name")]
        candidates.extend((a, f"norm_alias:{a}") for a in r.get("aliases", []))
        for cand, reason in candidates:
            if _norm(cand) == q_norm and q_norm:
                norm_hits.append((r, reason))
                break
    if len(norm_hits) == 1:
        return norm_hits[0]
    if len(norm_hits) > 1:
        ids = ", ".join(h[0]["id"] for h in norm_hits)
        raise SystemExit(f"Ambiguous reagent query '{query}'. Matches: {ids}. Specify an exact ID.")

    # 4. Unambiguous substring match across id, formula, name, aliases
    sub_hits: list[tuple[dict[str, Any], str]] = []
    for r in reagents:
        haystack = [r["id"], r["formula"], r["name"], *r.get("aliases", [])]
        if any(q_low in h.lower() or (len(q_norm) >= 3 and q_norm in _norm(h)) for h in haystack):
            sub_hits.append((r, "substring"))
    if len(sub_hits) == 1:
        return sub_hits[0]

    # 5. Helpful suggestions on failure or ambiguity
    pool: dict[str, str] = {}
    for r in reagents:
        pool[r["id"].lower()] = f"{r['id']} ({r['formula']})"
        pool[r["formula"].lower()] = f"{r['id']} ({r['formula']})"
        pool[r["name"].lower()] = f"{r['id']} ({r['formula']})"
        for a in r.get("aliases", []):
            pool[a.lower()] = f"{r['id']} ({r['formula']})"

    if len(sub_hits) > 1:
        opts = ", ".join(f"{h[0]['id']} ({h[0]['formula']})" for h in sub_hits[:6])
        raise SystemExit(f"Ambiguous reagent query '{query}'. Matching entries: {opts}. Use an exact ID.")

    close_keys = difflib.get_close_matches(q_low, list(pool.keys()), n=5, cutoff=0.45)
    suggestions = list(dict.fromkeys(pool[k] for k in close_keys))[:4]
    if not suggestions:
        norm_pool = {_norm(k): v for k, v in pool.items()}
        close_norm = difflib.get_close_matches(q_norm, list(norm_pool.keys()), n=5, cutoff=0.5)
        suggestions = list(dict.fromkeys(norm_pool[k] for k in close_norm))[:4]
    sug_msg = f" Suggestions: {', '.join(suggestions)}" if suggestions else " Run `list` to see all available reagents."
    raise SystemExit(f"Reagent not found: '{query}'.{sug_msg}")


def _build_mol(smiles: str) -> Chem.Mol:
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        raise ValueError(f"RDKit failed to parse SMILES: {smiles}")
    Chem.AssignStereochemistry(mol, cleanIt=True, force=True, flagPossibleStereoCenters=True)
    try:
        rdCIPLabeler.AssignCIPLabels(mol)
    except Exception:
        pass
    AllChem.Compute2DCoords(mol)
    return mol


def _stereo_summary(mol: Chem.Mol) -> str:
    chiral = Chem.FindMolChiralCenters(mol, includeUnassigned=True, useLegacyImplementation=False)
    parts: list[str] = []
    for idx, cip in chiral:
        atom = mol.GetAtomWithIdx(idx)
        parts.append(f"{atom.GetSymbol()}{idx}:({cip})")
    for b in mol.GetBonds():
        if b.GetBondType() == Chem.BondType.DOUBLE:
            cip = b.GetProp("_CIPCode") if b.HasProp("_CIPCode") else None
            if cip:
                parts.append(f"C=C:({cip})")
    return ", ".join(parts) if parts else "achiral"


def validate_database_smiles(db: dict[str, Any]) -> dict[str, Any]:
    """Validate every reactantSmiles and productSmiles in the database using RDKit."""
    checked = 0
    errors: list[str] = []
    for r in db.get("reagents", []):
        ex = r.get("example", {})
        for field in ("reactantSmiles", "productSmiles"):
            smi = ex.get(field, "")
            checked += 1
            if not smi:
                errors.append(f"{r.get('id')}.{field}: missing")
                continue
            try:
                _build_mol(smi)
            except Exception as exc:
                errors.append(f"{r.get('id')}.{field}: {exc}")
    return {"checked": checked, "valid": len(errors) == 0, "errors": errors}


def _verify_example_rdkit(example: dict[str, Any]) -> dict[str, Any]:
    r_mol = _build_mol(example["reactantSmiles"])
    p_mol = _build_mol(example["productSmiles"])
    return {
        "reactantCanonicalSmiles": Chem.MolToSmiles(r_mol, isomericSmiles=True),
        "reactantFormula": rdMolDescriptors.CalcMolFormula(r_mol),
        "reactantStereo": _stereo_summary(r_mol),
        "productCanonicalSmiles": Chem.MolToSmiles(p_mol, isomericSmiles=True),
        "productFormula": rdMolDescriptors.CalcMolFormula(p_mol),
        "productStereo": _stereo_summary(p_mol),
    }


def _load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = (
        [
            "/System/Library/Fonts/HelveticaNeue.ttc",
            "/System/Library/Fonts/Helvetica.ttc",
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
            "/System/Library/Fonts/Supplemental/Arial.ttf",
        ]
        if bold
        else [
            "/System/Library/Fonts/HelveticaNeue.ttc",
            "/System/Library/Fonts/Helvetica.ttc",
            "/System/Library/Fonts/Supplemental/Arial.ttf",
        ]
    )
    for fp in candidates:
        p = Path(fp)
        if p.exists():
            try:
                index = 1 if (bold and fp.endswith(".ttc")) else 0
                return ImageFont.truetype(str(p), size=size, index=index)
            except Exception:
                try:
                    return ImageFont.truetype(str(p), size=size)
                except Exception:
                    continue
    return ImageFont.load_default()


def _render_mol_png_bytes(smiles: str, width: int, height: int, dark: bool) -> bytes:
    mol = _build_mol(smiles)
    drawer = rdMolDraw2D.MolDraw2DCairo(width, height)
    opts = drawer.drawOptions()
    opts.addStereoAnnotation = True
    opts.annotationFontScale = 0.85
    opts.bondLineWidth = 2.0
    opts.padding = 0.14
    if dark:
        rdMolDraw2D.SetDarkMode(opts)
        opts.setBackgroundColour((0.082, 0.102, 0.141, 1.0))
    else:
        opts.setBackgroundColour((1.0, 1.0, 1.0, 1.0))
    rdMolDraw2D.PrepareAndDrawMolecule(drawer, mol)
    drawer.FinishDrawing()
    return drawer.GetDrawingText()


def _render_mol_svg_inner(smiles: str, width: int, height: int, dark: bool, id_prefix: str) -> str:
    mol = _build_mol(smiles)
    drawer = rdMolDraw2D.MolDraw2DSVG(width, height)
    opts = drawer.drawOptions()
    opts.addStereoAnnotation = True
    opts.annotationFontScale = 0.85
    opts.bondLineWidth = 2.0
    opts.padding = 0.14
    if dark:
        rdMolDraw2D.SetDarkMode(opts)
        opts.setBackgroundColour((0.082, 0.102, 0.141, 1.0))
    else:
        opts.setBackgroundColour((1.0, 1.0, 1.0, 1.0))
    rdMolDraw2D.PrepareAndDrawMolecule(drawer, mol)
    drawer.FinishDrawing()
    raw = drawer.GetDrawingText()
    # Extract inner elements inside <svg ...>...</svg> and namespace IDs cleanly
    start = raw.find(">", raw.find("<svg"))
    end = raw.rfind("</svg>")
    inner = raw[start + 1 : end] if (start != -1 and end != -1) else raw
    inner = re.sub(r'\bid="([^"]+)"', rf'id="{id_prefix}_\1"', inner)
    inner = re.sub(r'url\(#([^)]+)\)', rf'url(#{id_prefix}_\1)', inner)
    inner = re.sub(r'href="#([^"]+)"', rf'href="#{id_prefix}_\1"', inner)
    return inner


def render_comparison_grid(
    reagents: list[dict[str, Any]],
    out_path: str | Path,
    differentiating_groups: list[str] | None = None,
    dark: bool = False,
) -> dict[str, Any]:
    """Render a crisp, publication-grade RDKit 2D reaction comparison grid (PNG or SVG)."""
    out = Path(out_path).expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    ext = out.suffix.lower()
    if ext not in (".png", ".svg"):
        raise SystemExit(f"--out-grid must end with .png or .svg (got '{out.suffix or 'none'}').")

    # Layout constants
    mol_w, mol_h = 370, 210
    arrow_w = 240
    pad_x = 24
    pad_y = 20
    title_h = 74
    card_header_h = 40
    caption_h = 38
    card_gap = 18
    card_w = mol_w + arrow_w + mol_w + 32
    card_h = card_header_h + mol_h + caption_h + 14
    total_w = card_w + 2 * pad_x
    total_h = title_h + len(reagents) * card_h + max(0, len(reagents) - 1) * card_gap + 2 * pad_y

    diff_list = differentiating_groups or []
    diff_str = ", ".join(diff_list[:6]) + (" ..." if len(diff_list) > 6 else "")
    title_text = "Reagent Comparison: " + "  vs  ".join(r["formula"] for r in reagents)
    subtitle_text = (
        f"Differentiating groups ({len(diff_list)}): {diff_str}"
        if diff_list
        else "Side-by-side verified RDKit 2D reaction outcomes"
    )

    if dark:
        bg_hex, card_hex, hdr_hex, border_hex = "#0b0f17", "#151a24", "#1c2433", "#2b3548"
        text_hex, muted_hex, accent_hex, arrow_hex = "#f1f5f9", "#94a3b8", "#38bdf8", "#cbd5e1"
        pill_stereo_bg, pill_stereo_fg = "#1e293b", "#fde68a"
        risk_colors = {
            "none": ("#064e3b", "#6ee7b7"),
            "low": ("#451a03", "#fcd34d"),
            "high": ("#4c0519", "#fda4af"),
        }
    else:
        bg_hex, card_hex, hdr_hex, border_hex = "#f8fafc", "#ffffff", "#f1f5f9", "#cbd5e1"
        text_hex, muted_hex, accent_hex, arrow_hex = "#0f172a", "#475569", "#1d4ed8", "#334155"
        pill_stereo_bg, pill_stereo_fg = "#eff6ff", "#1e40af"
        risk_colors = {
            "none": ("#ecfdf5", "#047857"),
            "low": ("#fffbeb", "#b45309"),
            "high": ("#fff1f2", "#be123c"),
        }

    if ext == ".png":
        img = Image.new("RGB", (total_w, total_h), bg_hex)
        draw = ImageDraw.Draw(img)
        f_title = _load_font(20, bold=True)
        f_sub = _load_font(13, bold=False)
        f_hdr = _load_font(15, bold=True)
        f_pill = _load_font(12, bold=True)
        f_reag = _load_font(14, bold=True)
        f_cond = _load_font(11, bold=False)
        f_cap = _load_font(12, bold=True)
        f_cap_sub = _load_font(11, bold=False)

        draw.text((pad_x, pad_y), title_text, fill=text_hex, font=f_title)
        draw.text((pad_x, pad_y + 30), subtitle_text, fill=muted_hex, font=f_sub)
        draw.line([(pad_x, title_h + pad_y - 10), (total_w - pad_x, title_h + pad_y - 10)], fill=border_hex, width=1)

        for idx, r in enumerate(reagents):
            y0 = pad_y + title_h + idx * (card_h + card_gap)
            x0 = pad_x
            x1 = x0 + card_w
            y1 = y0 + card_h

            # Card container + header band
            draw.rounded_rectangle([x0, y0, x1, y1], radius=10, fill=card_hex, outline=border_hex, width=2)
            draw.rounded_rectangle([x0 + 1, y0 + 1, x1 - 1, y0 + card_header_h], radius=9, fill=hdr_hex)
            draw.rectangle([x0 + 1, y0 + card_header_h - 10, x1 - 1, y0 + card_header_h], fill=hdr_hex)
            draw.line([(x0, y0 + card_header_h), (x1, y0 + card_header_h)], fill=border_hex, width=1)

            hdr_label = f"{idx + 1}. {r['formula']}  —  {r['name']}"
            draw.text((x0 + 14, y0 + 11), hdr_label, fill=text_hex, font=f_hdr)

            # Right-aligned badges for stereochemistry & rearrangement risk
            risk = r["rearrangementRisk"]
            r_bg, r_fg = risk_colors.get(risk, risk_colors["none"])
            risk_txt = f"Rearrangement: {risk.upper()}"
            stereo_txt = f"Stereo: {r['stereochemistry']}"
            rb = draw.textbbox((0, 0), risk_txt, font=f_pill)
            sb = draw.textbbox((0, 0), stereo_txt, font=f_pill)
            rw = (rb[2] - rb[0]) + 18
            sw = (sb[2] - sb[0]) + 18
            rx1 = x1 - 12
            rx0 = rx1 - rw
            sx1 = rx0 - 8
            sx0 = sx1 - sw
            draw.rounded_rectangle([sx0, y0 + 9, sx1, y0 + 31], radius=6, fill=pill_stereo_bg, outline=border_hex, width=1)
            draw.text((sx0 + 9, y0 + 13), stereo_txt, fill=pill_stereo_fg, font=f_pill)
            draw.rounded_rectangle([rx0, y0 + 9, rx1, y0 + 31], radius=6, fill=r_bg, outline=border_hex, width=1)
            draw.text((rx0 + 9, y0 + 13), risk_txt, fill=r_fg, font=f_pill)

            # Render RDKit 2D PNG panels
            ex = r["example"]
            r_png = _render_mol_png_bytes(ex["reactantSmiles"], mol_w, mol_h, dark)
            p_png = _render_mol_png_bytes(ex["productSmiles"], mol_w, mol_h, dark)
            r_im = Image.open(io.BytesIO(r_png)).convert("RGB")
            p_im = Image.open(io.BytesIO(p_png)).convert("RGB")

            mol_y = y0 + card_header_h + 6
            left_x = x0 + 12
            arrow_x0 = left_x + mol_w + 4
            arrow_x1 = arrow_x0 + arrow_w
            right_x = arrow_x1 + 4

            img.paste(r_im, (left_x, mol_y))
            img.paste(p_im, (right_x, mol_y))

            # Center reaction arrow + reagent formula + concise wrapped conditions
            ay = mol_y + mol_h // 2
            ax_start = arrow_x0 + 18
            ax_end = arrow_x1 - 22
            draw.line([(ax_start, ay), (ax_end, ay)], fill=arrow_hex, width=3)
            draw.polygon([(ax_end + 8, ay), (ax_end - 5, ay - 7), (ax_end - 5, ay + 7)], fill=arrow_hex)

            # Formula centered above arrow
            f_lines = textwrap.wrap(r["formula"], width=24)[:2]
            fy = ay - 14 - len(f_lines) * 16
            for line in f_lines:
                bb = draw.textbbox((0, 0), line, font=f_reag)
                tw = bb[2] - bb[0]
                draw.text((arrow_x0 + (arrow_w - tw) // 2, fy), line, fill=accent_hex, font=f_reag)
                fy += 16

            # Concise condition text centered below arrow (keep full 2-step sequences starting with '1)')
            cond_raw = r["conditions"]
            if cond_raw.startswith("1)"):
                c_lines = [s.strip() for s in cond_raw.split(";") if s.strip()][:3]
                wrapped: list[str] = []
                for step_line in c_lines:
                    wrapped.extend(textwrap.wrap(step_line, width=30))
                c_lines = wrapped[:4]
            else:
                c_lines = textwrap.wrap(cond_raw.split(";")[0], width=30)[:3]
            cy = ay + 12
            for line in c_lines:
                bb = draw.textbbox((0, 0), line, font=f_cond)
                tw = bb[2] - bb[0]
                draw.text((arrow_x0 + (arrow_w - tw) // 2, cy), line, fill=muted_hex, font=f_cond)
                cy += 14

            # Captions centered under reactant and product
            cap_y = mol_y + mol_h + 2
            r_cap = ex["reactantName"]
            p_cap = ex["productName"]
            r_bb = draw.textbbox((0, 0), r_cap, font=f_cap)
            p_bb = draw.textbbox((0, 0), p_cap, font=f_cap)
            draw.text((left_x + max(0, (mol_w - (r_bb[2] - r_bb[0])) // 2), cap_y), r_cap, fill=text_hex, font=f_cap)
            draw.text((right_x + max(0, (mol_w - (p_bb[2] - p_bb[0])) // 2), cap_y), p_cap, fill=text_hex, font=f_cap)

            lbl = ex.get("label", "")
            if lbl:
                l_bb = draw.textbbox((0, 0), lbl, font=f_cap_sub)
                draw.text(
                    (x0 + max(12, (card_w - (l_bb[2] - l_bb[0])) // 2), cap_y + 18),
                    lbl,
                    fill=muted_hex,
                    font=f_cap_sub,
                )

        img.save(str(out), format="PNG")
    else:
        # Pure vector SVG compositing RDKit MolDraw2DSVG panels
        parts: list[str] = [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{total_w}" height="{total_h}" viewBox="0 0 {total_w} {total_h}">',
            "<style>",
            '  text { font-family: -apple-system, BlinkMacSystemFont, "Helvetica Neue", Helvetica, Arial, sans-serif; }',
            "</style>",
            f'<rect x="0" y="0" width="{total_w}" height="{total_h}" fill="{bg_hex}"/>',
            f'<text x="{pad_x}" y="{pad_y + 20}" font-size="20" font-weight="700" fill="{text_hex}">{html.escape(title_text)}</text>',
            f'<text x="{pad_x}" y="{pad_y + 44}" font-size="13" fill="{muted_hex}">{html.escape(subtitle_text)}</text>',
            f'<line x1="{pad_x}" y1="{title_h + pad_y - 10}" x2="{total_w - pad_x}" y2="{title_h + pad_y - 10}" stroke="{border_hex}" stroke-width="1"/>',
        ]
        for idx, r in enumerate(reagents):
            y0 = pad_y + title_h + idx * (card_h + card_gap)
            x0 = pad_x
            x1 = x0 + card_w
            risk = r["rearrangementRisk"]
            r_bg, r_fg = risk_colors.get(risk, risk_colors["none"])
            hdr_label = f"{idx + 1}. {r['formula']}  —  {r['name']}"
            stereo_txt = f"Stereo: {r['stereochemistry']}"
            risk_txt = f"Rearrangement: {risk.upper()}"

            parts.append(
                f'<rect x="{x0}" y="{y0}" width="{card_w}" height="{card_h}" rx="10" fill="{card_hex}" stroke="{border_hex}" stroke-width="2"/>'
            )
            parts.append(
                f'<path d="M {x0+10},{y0+1} H {x1-10} Q {x1-1},{y0+1} {x1-1},{y0+10} V {y0+card_header_h} H {x0+1} V {y0+10} Q {x0+1},{y0+1} {x0+10},{y0+1} Z" fill="{hdr_hex}"/>'
            )
            parts.append(
                f'<line x1="{x0}" y1="{y0+card_header_h}" x2="{x1}" y2="{y0+card_header_h}" stroke="{border_hex}" stroke-width="1"/>'
            )
            parts.append(
                f'<text x="{x0+14}" y="{y0+25}" font-size="15" font-weight="700" fill="{text_hex}">{html.escape(hdr_label)}</text>'
            )

            rw = len(risk_txt) * 7 + 22
            sw = len(stereo_txt) * 7 + 22
            rx0 = x1 - 12 - rw
            sx0 = rx0 - 8 - sw
            parts.append(
                f'<rect x="{sx0}" y="{y0+9}" width="{sw}" height="22" rx="6" fill="{pill_stereo_bg}" stroke="{border_hex}" stroke-width="1"/>'
            )
            parts.append(
                f'<text x="{sx0 + sw/2}" y="{y0+24}" font-size="12" font-weight="700" text-anchor="middle" fill="{pill_stereo_fg}">{html.escape(stereo_txt)}</text>'
            )
            parts.append(
                f'<rect x="{rx0}" y="{y0+9}" width="{rw}" height="22" rx="6" fill="{r_bg}" stroke="{border_hex}" stroke-width="1"/>'
            )
            parts.append(
                f'<text x="{rx0 + rw/2}" y="{y0+24}" font-size="12" font-weight="700" text-anchor="middle" fill="{r_fg}">{html.escape(risk_txt)}</text>'
            )

            ex = r["example"]
            mol_y = y0 + card_header_h + 6
            left_x = x0 + 12
            arrow_x0 = left_x + mol_w + 4
            arrow_x1 = arrow_x0 + arrow_w
            right_x = arrow_x1 + 4

            r_inner = _render_mol_svg_inner(ex["reactantSmiles"], mol_w, mol_h, dark, f"r{idx}a")
            p_inner = _render_mol_svg_inner(ex["productSmiles"], mol_w, mol_h, dark, f"r{idx}b")
            parts.append(f'<g transform="translate({left_x},{mol_y})">{r_inner}</g>')
            parts.append(f'<g transform="translate({right_x},{mol_y})">{p_inner}</g>')

            ay = mol_y + mol_h // 2
            ax_start = arrow_x0 + 18
            ax_end = arrow_x1 - 22
            parts.append(
                f'<line x1="{ax_start}" y1="{ay}" x2="{ax_end}" y2="{ay}" stroke="{arrow_hex}" stroke-width="3"/>'
            )
            parts.append(
                f'<polygon points="{ax_end+8},{ay} {ax_end-5},{ay-7} {ax_end-5},{ay+7}" fill="{arrow_hex}"/>'
            )

            ax_mid = arrow_x0 + arrow_w / 2
            f_lines = textwrap.wrap(r["formula"], width=24)[:2]
            fy = ay - 10 - (len(f_lines) - 1) * 16
            for line in f_lines:
                parts.append(
                    f'<text x="{ax_mid}" y="{fy}" font-size="14" font-weight="700" text-anchor="middle" fill="{accent_hex}">{html.escape(line)}</text>'
                )
                fy += 16

            cond_raw = r["conditions"]
            if cond_raw.startswith("1)"):
                c_steps = [s.strip() for s in cond_raw.split(";") if s.strip()][:3]
                c_lines = []
                for step_line in c_steps:
                    c_lines.extend(textwrap.wrap(step_line, width=30))
                c_lines = c_lines[:4]
            else:
                c_lines = textwrap.wrap(cond_raw.split(";")[0], width=30)[:3]
            cy = ay + 20
            for line in c_lines:
                parts.append(
                    f'<text x="{ax_mid}" y="{cy}" font-size="11" text-anchor="middle" fill="{muted_hex}">{html.escape(line)}</text>'
                )
                cy += 14

            cap_y = mol_y + mol_h + 15
            parts.append(
                f'<text x="{left_x + mol_w/2}" y="{cap_y}" font-size="12" font-weight="700" text-anchor="middle" fill="{text_hex}">{html.escape(ex["reactantName"])}</text>'
            )
            parts.append(
                f'<text x="{right_x + mol_w/2}" y="{cap_y}" font-size="12" font-weight="700" text-anchor="middle" fill="{text_hex}">{html.escape(ex["productName"])}</text>'
            )
            lbl = ex.get("label", "")
            if lbl:
                parts.append(
                    f'<text x="{x0 + card_w/2}" y="{cap_y + 18}" font-size="11" text-anchor="middle" fill="{muted_hex}">{html.escape(lbl)}</text>'
                )

        parts.append("</svg>")
        out.write_text("\n".join(parts), encoding="utf-8")

    return {
        "path": str(out),
        "format": ext.lstrip("."),
        "width": total_w,
        "height": total_h,
        "bytes": out.stat().st_size,
        "reagentCount": len(reagents),
    }


def compare_reagents(
    db: dict[str, Any],
    queries: list[str],
    groups: list[str] | None = None,
) -> dict[str, Any]:
    if not (2 <= len(queries) <= 4):
        raise SystemExit(f"`compare` expects 2 to 4 reagents (got {len(queries)}).")
    resolved: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for q in queries:
        r, _ = resolve_reagent(db, q)
        if r["id"] in seen_ids:
            raise SystemExit(f"Duplicate reagent in comparison: '{r['id']}' ({r['formula']}).")
        seen_ids.add(r["id"])
        resolved.append(r)

    all_groups: list[str] = db["functionalGroups"]
    target_groups = (
        [normalize_functional_group(g, all_groups) for g in groups] if groups else list(all_groups)
    )

    matrix: dict[str, dict[str, str]] = {}
    selectivity_diffs: list[str] = []
    outcome_diffs: list[str] = []
    differentiating: list[str] = []

    for fg in target_groups:
        row = {r["id"]: r["reactivity"].get(fg, "no_reaction") for r in resolved}
        matrix[fg] = row
        vals = list(row.values())
        unique_vals = set(vals)
        if len(unique_vals) > 1:
            differentiating.append(fg)
            has_no_rxn = "no_reaction" in unique_vals
            has_rxn = any(v != "no_reaction" for v in unique_vals)
            if has_no_rxn and has_rxn:
                selectivity_diffs.append(fg)
            else:
                outcome_diffs.append(fg)

    stereo_set = {r["stereochemistry"] for r in resolved}
    rearr_set = {r["rearrangementRisk"] for r in resolved}

    return {
        "reagents": [
            {
                "id": r["id"],
                "name": r["name"],
                "formula": r["formula"],
                "category": r["category"],
                "conditions": r["conditions"],
                "mechanismClass": r["mechanismClass"],
                "stereochemistry": r["stereochemistry"],
                "stereochemistryDetail": r["stereochemistryDetail"],
                "rearrangementRisk": r["rearrangementRisk"],
                "rearrangementNote": r["rearrangementNote"],
                "jeeTraps": r["jeeTraps"],
                "example": r["example"],
            }
            for r in resolved
        ],
        "groups": target_groups,
        "matrix": matrix,
        "differentiatingGroups": differentiating,
        "selectivityDifferences": selectivity_diffs,
        "outcomeDifferences": outcome_diffs,
        "stereochemistryDiffers": len(stereo_set) > 1,
        "rearrangementDiffers": len(rearr_set) > 1,
    }


def query_selectivity(
    db: dict[str, Any],
    functional_group: str,
    reagent_queries: list[str] | None = None,
    category: str | None = None,
) -> dict[str, Any]:
    fg = normalize_functional_group(functional_group, db["functionalGroups"])
    if reagent_queries:
        pool = [resolve_reagent(db, q)[0] for q in reagent_queries]
    else:
        pool = list(db["reagents"])
    if category:
        pool = [r for r in pool if r["category"] == category]

    reacts: list[dict[str, Any]] = []
    leaves_intact: list[dict[str, Any]] = []
    for r in pool:
        outcome = r["reactivity"].get(fg, "no_reaction")
        if outcome != "no_reaction":
            reacts.append(
                {
                    "id": r["id"],
                    "name": r["name"],
                    "formula": r["formula"],
                    "category": r["category"],
                    "outcome": outcome,
                    "stereochemistry": r["stereochemistry"],
                    "rearrangementRisk": r["rearrangementRisk"],
                }
            )
        else:
            leaves_intact.append(
                {
                    "id": r["id"],
                    "name": r["name"],
                    "formula": r["formula"],
                    "category": r["category"],
                }
            )

    return {
        "functionalGroup": fg,
        "displayName": FG_DISPLAY.get(fg, fg),
        "categoryFilter": category,
        "totalEvaluated": len(pool),
        "reactsCount": len(reacts),
        "leavesIntactCount": len(leaves_intact),
        "reacts": reacts,
        "leavesIntact": leaves_intact,
    }


def build_drill_pair(db: dict[str, Any], q1: str, q2: str) -> dict[str, Any]:
    """Build a zero-spoiler LearnHarness discrimination drill between two confusable reagents."""
    r1, _ = resolve_reagent(db, q1)
    r2, _ = resolve_reagent(db, q2)
    if r1["id"] == r2["id"]:
        raise SystemExit(f"Provide two distinct reagents for `drill-pair` (both resolved to '{r1['id']}').")

    comp = compare_reagents(db, [r1["id"], r2["id"]])
    diff_groups: list[str] = comp["differentiatingGroups"]
    sel_diffs: list[str] = comp["selectivityDifferences"]
    out_diffs: list[str] = comp["outcomeDifferences"]

    ex1, ex2 = r1["example"], r2["example"]
    shared_substrate = ex1["reactantSmiles"] == ex2["reactantSmiles"]

    # Build ZERO-SPOILER student prompt (never leaks outcome, stereo, or rearrangement answer!)
    if shared_substrate:
        substrate_name = ex1["reactantName"]
        substrate_smiles = ex1["reactantSmiles"]
        stem = (
            f"You are given {substrate_name} and two reagent systems: "
            f"(A) {r1['formula']} and (B) {r2['formula']}. "
            f"Predict the major organic product obtained under (A) and under (B), "
            f"and state the exact mechanistic reason why they diverge."
        )
    elif diff_groups:
        top_fg = diff_groups[0]
        fg_label = FG_DISPLAY.get(top_fg, top_fg)
        substrate_name = ex1["reactantName"]
        substrate_smiles = ex1["reactantSmiles"]
        stem = (
            f"Compare reagent (A) {r1['formula']} against reagent (B) {r2['formula']}. "
            f"Consider a molecule containing a {fg_label} group (such as {substrate_name}, "
            f"or a bifunctional substrate containing {', '.join(FG_DISPLAY.get(g, g) for g in diff_groups[:2])}). "
            f"Predict how (A) and (B) behave toward each functional group, including stereochemistry and any skeletal rearrangement."
        )
    else:
        substrate_name = ex1["reactantName"]
        substrate_smiles = ex1["reactantSmiles"]
        stem = (
            f"Compare reagent (A) {r1['formula']} and reagent (B) {r2['formula']} on {substrate_name}. "
            f"Predict the major product, stereochemical outcome, and rearrangement risk for each."
        )

    probe_questions = [
        f"1. Which functional groups (if any) react differently under {r1['formula']} vs {r2['formula']}?",
        f"2. Predict the major product from {substrate_name} using (A) {r1['formula']} and (B) {r2['formula']}.",
        "3. State the stereochemical outcome and whether any carbocation/skeletal rearrangement occurs in either path.",
    ]

    # Progressive non-spoiling hints R0-R2
    hint_r0 = f"Identify the reactive functional group(s) in {substrate_name} and classify each reagent's mechanism family first."
    hint_r1 = (
        f"Check the functional groups where these two reagents disagree: {', '.join(diff_groups[:4])}."
        if diff_groups
        else f"Compare the mechanism classes ({r1['mechanismClass']} vs {r2['mechanismClass']}) and stereochemical pathway."
    )
    hint_r2 = (
        f"Compare intermediate type & geometry: check whether either path forms a free carbocation, cyclic bridged ion, or radical."
    )

    axes: list[str] = []
    if sel_diffs:
        axes.append(f"Chemoselectivity differs on: {', '.join(sel_diffs)}")
    if out_diffs:
        axes.append(f"Product outcome differs on: {', '.join(out_diffs)}")
    if comp["stereochemistryDiffers"]:
        axes.append(f"Stereochemistry differs: {r1['formula']} ({r1['stereochemistry']}) vs {r2['formula']} ({r2['stereochemistry']})")
    if comp["rearrangementDiffers"]:
        axes.append(f"Rearrangement risk differs: {r1['formula']} ({r1['rearrangementRisk']}) vs {r2['formula']} ({r2['rearrangementRisk']})")
    if not axes:
        axes.append(f"Compare mechanism conditions ({r1['conditions']} vs {r2['conditions']})")

    return {
        "pair": [r1["id"], r2["id"]],
        "sharedSubstrate": shared_substrate,
        "studentPrompt": {
            "stem": stem,
            "substrateName": substrate_name,
            "substrateSmiles": substrate_smiles,
            "reagents": [
                {"choice": "A", "formula": r1["formula"], "conditions": r1["conditions"]},
                {"choice": "B", "formula": r2["formula"], "conditions": r2["conditions"]},
            ],
            "questions": probe_questions,
        },
        "hints": {
            "R0": hint_r0,
            "R1": hint_r1,
            "R2": hint_r2,
        },
        "differentiationSummary": {
            "differentiatingGroups": diff_groups,
            "selectivityDifferences": sel_diffs,
            "outcomeDifferences": out_diffs,
            "stereochemistryDiffers": comp["stereochemistryDiffers"],
            "rearrangementDiffers": comp["rearrangementDiffers"],
            "keyAxes": axes,
        },
        "answerKey": {
            "reagentA": {
                "id": r1["id"],
                "formula": r1["formula"],
                "mechanismClass": r1["mechanismClass"],
                "stereochemistry": r1["stereochemistry"],
                "stereochemistryDetail": r1["stereochemistryDetail"],
                "rearrangementRisk": r1["rearrangementRisk"],
                "rearrangementNote": r1["rearrangementNote"],
                "exampleProductName": ex1["productName"],
                "exampleProductSmiles": ex1["productSmiles"],
                "traps": r1["jeeTraps"],
            },
            "reagentB": {
                "id": r2["id"],
                "formula": r2["formula"],
                "mechanismClass": r2["mechanismClass"],
                "stereochemistry": r2["stereochemistry"],
                "stereochemistryDetail": r2["stereochemistryDetail"],
                "rearrangementRisk": r2["rearrangementRisk"],
                "rearrangementNote": r2["rearrangementNote"],
                "exampleProductName": ex2["productName"],
                "exampleProductSmiles": ex2["productSmiles"],
                "traps": r2["jeeTraps"],
            },
            "differentiatingReactivity": {fg: comp["matrix"][fg] for fg in diff_groups},
        },
    }


# ---------------------------------------------------------------------------
# CLI Command Handlers
# ---------------------------------------------------------------------------


def cmd_list(args: argparse.Namespace) -> None:
    db = load_reagents_db(args.db)
    reagents = db["reagents"]
    if args.category:
        reagents = [r for r in reagents if r["category"] == args.category]

    if args.json:
        payload = {
            "count": len(reagents),
            "category": args.category,
            "functionalGroups": db["functionalGroups"],
            "reagents": [
                {
                    "id": r["id"],
                    "formula": r["formula"],
                    "name": r["name"],
                    "category": r["category"],
                    "stereochemistry": r["stereochemistry"],
                    "rearrangementRisk": r["rearrangementRisk"],
                }
                for r in reagents
            ],
        }
        print(json.dumps(payload, indent=2))
        return

    header = f"Curated JEE Organic Reagents ({len(reagents)}" + (f" in '{args.category}'" if args.category else " total") + ")"
    print(header)
    print("=" * len(header))
    print(f"{'ID':<18} {'Formula':<26} {'Category':<25} {'Stereo':<14} {'Rearr.'}")
    print("-" * 92)
    for r in reagents:
        print(
            f"{r['id']:<18} {r['formula']:<26} {r['category']:<25} "
            f"{r['stereochemistry']:<14} {r['rearrangementRisk']}"
        )


def cmd_lookup(args: argparse.Namespace) -> None:
    db = load_reagents_db(args.db)
    r, matched_by = resolve_reagent(db, args.query)
    fgs: list[str] = db["functionalGroups"]
    reacts_with = [fg for fg in fgs if r["reactivity"].get(fg, "no_reaction") != "no_reaction"]
    untouched = [fg for fg in fgs if r["reactivity"].get(fg, "no_reaction") == "no_reaction"]
    rdkit_info = _verify_example_rdkit(r["example"])

    if args.json:
        payload = {
            "matchedBy": matched_by,
            "reagent": r,
            "reactsWith": reacts_with,
            "untouchedGroups": untouched,
            "rdkitExample": rdkit_info,
        }
        print(json.dumps(payload, indent=2))
        return

    print(f"{r['formula']} — {r['name']}  [{r['id']}]")
    print("=" * 78)
    print(f"Category      : {r['category']}")
    print(f"Mechanism     : {r['mechanismClass']}")
    print(f"Conditions    : {r['conditions']}")
    print(f"Stereochem    : {r['stereochemistry']} — {r['stereochemistryDetail']}")
    print(f"Rearrangement : {r['rearrangementRisk']} — {r['rearrangementNote']}")
    print(f"Aliases       : {', '.join(r.get('aliases', []))}")
    print("\nFunctional Group Reactivity:")
    for fg in reacts_with:
        print(f"  [+] {fg:<17} -> {r['reactivity'][fg]}")
    if untouched:
        print(f"  [-] Leaves intact ({len(untouched)}): {', '.join(untouched)}")
    ex = r["example"]
    print("\nVerified Example (RDKit):")
    print(f"  {ex['label']}")
    print(f"  {ex['reactantName']} ({rdkit_info['reactantCanonicalSmiles']}; {rdkit_info['reactantStereo']})")
    print(f"    --> {ex['productName']} ({rdkit_info['productCanonicalSmiles']}; {rdkit_info['productStereo']})")
    print("\nJEE Traps:")
    for i, trap in enumerate(r["jeeTraps"], start=1):
        print(f"  {i}. {trap}")


def cmd_compare(args: argparse.Namespace) -> None:
    db = load_reagents_db(args.db)
    groups = [g.strip() for g in args.groups.split(",") if g.strip()] if args.groups else None
    result = compare_reagents(db, args.reagents, groups=groups)

    grid_info = None
    if args.out_grid:
        reagent_objs = [resolve_reagent(db, r["id"])[0] for r in result["reagents"]]
        grid_info = render_comparison_grid(
            reagent_objs,
            args.out_grid,
            differentiating_groups=result["differentiatingGroups"],
            dark=args.dark,
        )
        result["gridOutput"] = grid_info

    if args.json:
        print(json.dumps(result, indent=2))
        return

    reagents = result["reagents"]
    print("Reagent Comparison: " + "  vs  ".join(r["formula"] for r in reagents))
    print("=" * 96)
    print(f"{'Attribute':<20}" + "".join(f" | {r['formula']:<24}" for r in reagents))
    print("-" * 96)
    print(f"{'Category':<20}" + "".join(f" | {r['category']:<24}" for r in reagents))
    print(f"{'Stereochemistry':<20}" + "".join(f" | {r['stereochemistry']:<24}" for r in reagents))
    print(f"{'Rearrangement':<20}" + "".join(f" | {r['rearrangementRisk']:<24}" for r in reagents))
    print("-" * 96)

    diff_set = set(result["differentiatingGroups"])
    print("\nFunctional Group Matrix (* = differentiating group):")
    for fg in result["groups"]:
        marker = "*" if fg in diff_set else " "
        print(f" {marker} {fg}:")
        for r in reagents:
            outcome = result["matrix"][fg][r["id"]]
            print(f"      {r['formula']:<24} -> {outcome}")

    print("\nKey Differentiation:")
    print(f"  Differentiating groups ({len(result['differentiatingGroups'])}): {', '.join(result['differentiatingGroups']) or 'none'}")
    print(f"  Pure selectivity splits    : {', '.join(result['selectivityDifferences']) or 'none'}")
    print(f"  Product-outcome splits     : {', '.join(result['outcomeDifferences']) or 'none'}")
    print(f"  Stereochemistry differs    : {'YES' if result['stereochemistryDiffers'] else 'no'}")
    print(f"  Rearrangement risk differs : {'YES' if result['rearrangementDiffers'] else 'no'}")

    print("\nJEE Traps by Reagent:")
    for r in reagents:
        print(f"  [{r['formula']}]")
        for trap in r["jeeTraps"]:
            print(f"    - {trap}")

    if grid_info:
        print(
            f"\nRDKit 2D Comparison Grid saved: {grid_info['path']} "
            f"({grid_info['width']}x{grid_info['height']}, {grid_info['bytes']} B)"
        )


def cmd_selectivity(args: argparse.Namespace) -> None:
    db = load_reagents_db(args.db)
    req_list = [x.strip() for x in args.reagents.split(",") if x.strip()] if args.reagents else None
    result = query_selectivity(db, args.functional_group, reagent_queries=req_list, category=args.category)

    if args.json:
        print(json.dumps(result, indent=2))
        return

    print(f"Chemoselectivity for {result['displayName']} [{result['functionalGroup']}]")
    print("=" * 84)
    print(f"REACTS ({result['reactsCount']} / {result['totalEvaluated']}):")
    for item in result["reacts"]:
        print(
            f"  [+] {item['formula']:<24} [{item['id']:<15}] -> {item['outcome']} "
            f"(stereo: {item['stereochemistry']}, rearr: {item['rearrangementRisk']})"
        )
    print(f"\nLEAVES INTACT ({result['leavesIntactCount']} / {result['totalEvaluated']}):")
    if result["leavesIntact"]:
        intact_str = ", ".join(f"{x['formula']} ({x['id']})" for x in result["leavesIntact"])
        for line in textwrap.wrap(intact_str, width=82):
            print(f"  [-] {line}")
    else:
        print("  (none)")


def cmd_drill_pair(args: argparse.Namespace) -> None:
    db = load_reagents_db(args.db)
    drill = build_drill_pair(db, args.r1, args.r2)

    if args.json:
        print(json.dumps(drill, indent=2))
        return

    sp = drill["studentPrompt"]
    print("=== STUDENT DISCRIMINATION PROMPT (ZERO SPOILERS) ===")
    print(sp["stem"])
    print(f"\nSubstrate : {sp['substrateName']}  (SMILES: {sp['substrateSmiles']})")
    for ch in sp["reagents"]:
        print(f"  ({ch['choice']}) {ch['formula']}  [{ch['conditions']}]")
    print("\nQuestions:")
    for q in sp["questions"]:
        print(f"  {q}")
    print("\nProgressive Hints (reveal only if needed):")
    for lvl, txt in drill["hints"].items():
        print(f"  {lvl}: {txt}")

    print("\n=== TUTOR ANSWER KEY & DISCRIMINATION RUBRIC ===")
    ds = drill["differentiationSummary"]
    print("Key Discriminating Axes:")
    for ax in ds["keyAxes"]:
        print(f"  * {ax}")
    for label, key in (("Reagent A", "reagentA"), ("Reagent B", "reagentB")):
        ak = drill["answerKey"][key]
        print(f"\n{label}: {ak['formula']} ({ak['id']})")
        print(f"  Mechanism     : {ak['mechanismClass']}")
        print(f"  Stereochem    : {ak['stereochemistry']} — {ak['stereochemistryDetail']}")
        print(f"  Rearrangement : {ak['rearrangementRisk']} — {ak['rearrangementNote']}")
        print(f"  Product       : {ak['exampleProductName']} ({ak['exampleProductSmiles']})")
        for trap in ak["traps"]:
            print(f"  Trap          : {trap}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Organic Chemistry Reagent & Named-Reaction Discrimination Matrix (JEE Main + Advanced)"
    )
    parser.add_argument(
        "--db",
        default=str(DEFAULT_DB),
        help=f"Path to reagents.json database (default: {DEFAULT_DB})",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    # list
    p_list = sub.add_parser("list", help="List all curated reagents, optionally filtered by category")
    p_list.add_argument("--db", default=argparse.SUPPRESS, help="Override path to reagents.json")
    p_list.add_argument("--category", choices=VALID_CATEGORIES, help="Filter by reaction category")
    p_list.add_argument("--json", action="store_true", help="Output machine-readable JSON")
    p_list.set_defaults(func=cmd_list)

    # lookup
    p_look = sub.add_parser("lookup", help="Look up any reagent by ID, formula, name, or JEE alias (e.g. LAH, DIBAL)")
    p_look.add_argument("query", help="Reagent ID, formula, name, or alias")
    p_look.add_argument("--db", default=argparse.SUPPRESS, help="Override path to reagents.json")
    p_look.add_argument("--json", action="store_true", help="Output machine-readable JSON")
    p_look.set_defaults(func=cmd_lookup)

    # compare
    p_cmp = sub.add_parser(
        "compare",
        help="Compare 2–4 reagents side-by-side across functional groups, stereochemistry, rearrangements, and RDKit 2D grid",
    )
    p_cmp.add_argument("reagents", nargs="+", help="2 to 4 reagent queries (IDs, formulas, or aliases)")
    p_cmp.add_argument("--groups", help="Comma-separated functional groups to focus on (default: all 13)")
    p_cmp.add_argument("--out-grid", help="Output path (.png or .svg) for RDKit 2D reaction comparison grid")
    p_cmp.add_argument("--dark", action="store_true", help="Render comparison grid in dark mode")
    p_cmp.add_argument("--db", default=argparse.SUPPRESS, help="Override path to reagents.json")
    p_cmp.add_argument("--json", action="store_true", help="Output machine-readable JSON")
    p_cmp.set_defaults(func=cmd_compare)

    # selectivity
    p_sel = sub.add_parser(
        "selectivity",
        help="Show which reagents react with vs leave a functional group intact",
    )
    p_sel.add_argument("functional_group", help="Functional group (e.g. carboxylic_acid, ester, alkene, aldehyde)")
    p_sel.add_argument("--reagents", help="Optional comma-separated subset of reagents to evaluate")
    p_sel.add_argument("--category", choices=VALID_CATEGORIES, help="Optional category filter")
    p_sel.add_argument("--db", default=argparse.SUPPRESS, help="Override path to reagents.json")
    p_sel.add_argument("--json", action="store_true", help="Output machine-readable JSON")
    p_sel.set_defaults(func=cmd_selectivity)

    # drill-pair
    p_drill = sub.add_parser(
        "drill-pair",
        help="Generate a zero-spoiler LearnHarness discrimination drill comparing two confusable reagents",
    )
    p_drill.add_argument("r1", help="First reagent (ID, formula, or alias)")
    p_drill.add_argument("r2", help="Second reagent (ID, formula, or alias)")
    p_drill.add_argument("--db", default=argparse.SUPPRESS, help="Override path to reagents.json")
    p_drill.add_argument("--json", action="store_true", help="Output machine-readable JSON")
    p_drill.set_defaults(func=cmd_drill_pair)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
