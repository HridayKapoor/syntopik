#!/usr/bin/env python3
"""Ultra-Dense Printable Mastery, Formula & Pitfall Revision Sheet Compiler (Typst) for Syntopik.

Compiles a structured JSON spec or Markdown chapter file into a crisp, 1-2 page
printable A4 revision sheet PDF under `chapters/<slug>-sheet.pdf` using local
Typst with zero network dependencies (<150ms).
Supports equations (Typst math & automatic LaTeX -> Typst conversion), crimson
pitfall/trap callout boxes, compact formula/complexity tables, and embedded SVG/PNG figures.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from syntopik.fsrs_core import card_retrievability, load_fsrs_parameters  # noqa: E402
from syntopik.paths import find_binary, resolve_kg_dir  # noqa: E402
from syntopik.schemas import slugify as _slugify_core, today_iso  # noqa: E402
from syntopik.storage import load_jsonl, save_json_atomic  # noqa: E402

DEFAULT_EXAM_TAG = "Mastery & Pitfall Sheet"

ACCENT_THEMES: dict[str, dict[str, str]] = {
    "blue": {"primary": "#1d4ed8", "soft": "#eff6ff", "border": "#93c5fd", "dark": "#0f172a"},
    "emerald": {"primary": "#047857", "soft": "#ecfdf5", "border": "#6ee7b7", "dark": "#064e3b"},
    "violet": {"primary": "#6d28d9", "soft": "#f5f3ff", "border": "#c4b5fd", "dark": "#2e1065"},
    "amber": {"primary": "#b45309", "soft": "#fffbeb", "border": "#fcd34d", "dark": "#451a03"},
    "crimson": {"primary": "#b91c1c", "soft": "#fef2f2", "border": "#fca5a5", "dark": "#450a0a"},
    "slate": {"primary": "#334155", "soft": "#f8fafc", "border": "#cbd5e1", "dark": "#0f172a"},
}


def find_typst() -> str:
    env_bin = os.environ.get("TYPST_BIN")
    if env_bin and Path(env_bin).is_file() and os.access(env_bin, os.X_OK):
        return str(env_bin)
    hit = find_binary("typst")
    if hit:
        return hit
    raise SystemExit("Typst CLI not found on PATH. Install via `brew install typst`, `winget install Typst.Typst`, or `cargo install typst-cli`.")


def _slugify(text: str) -> str:
    return _slugify_core(text, default="revision-sheet", max_len=64)


def escape_typst_text(text: str) -> str:
    """Escape plain prose for Typst markup while preserving inline $...$ math and **bold** / `code`."""
    # Split out inline code and math so we only escape prose segments.
    tokens = re.split(r"(\$\$.*?\$\$|\$[^$\n]+?\$|`[^`\n]+?`|\*\*[^*\n]+?\*\*)", text, flags=re.DOTALL)
    out: list[str] = []
    for tok in tokens:
        if not tok:
            continue
        if tok.startswith("$$") and tok.endswith("$$") and len(tok) > 4:
            out.append(f"$ {latex_to_typst_math(tok[2:-2].strip())} $")
        elif tok.startswith("$") and tok.endswith("$") and len(tok) > 2:
            out.append(f"${latex_to_typst_math(tok[1:-1].strip())}$")
        elif tok.startswith("`") and tok.endswith("`") and len(tok) > 2:
            inner = tok[1:-1].replace("\\", "\\\\").replace('"', '\\"')
            out.append(f'#raw("{inner}")')
        elif tok.startswith("**") and tok.endswith("**") and len(tok) > 4:
            inner = _escape_plain_chars(tok[2:-2])
            out.append(f"*{inner}*")
        else:
            out.append(_escape_plain_chars(tok))
    return "".join(out)


def _escape_plain_chars(s: str) -> str:
    for ch in ("\\", "#", "$", "*", "_", "@", "<", ">", "[", "]", "~"):
        s = s.replace(ch, "\\" + ch)
    # Prevent Typst from interpreting leading "1. Heading" inside inline headings/callouts as an ordered list
    s = re.sub(r"^(\d+)\.(\s)", r"\1\\.\2", s)
    return s


def _replace_brace_command(expr: str, cmd: str, nargs: int, replacer) -> str:
    """Safely replace LaTeX commands like \\frac{a}{b} or \\sqrt{x} handling nested braces."""
    token = "\\" + cmd
    while True:
        idx = expr.find(token)
        if idx == -1:
            break
        # Ensure exact command boundary (next char is not ASCII letter)
        pos = idx + len(token)
        if pos < len(expr) and expr[pos].isalpha():
            # Skip longer command with same prefix by guarding search
            prefix = expr[:pos]
            rest = _replace_brace_command(expr[pos:], cmd, nargs, replacer)
            return prefix + rest
        args: list[str] = []
        ok = True
        cur = pos
        for _ in range(nargs):
            while cur < len(expr) and expr[cur].isspace():
                cur += 1
            if cur >= len(expr) or expr[cur] != "{":
                ok = False
                break
            depth = 0
            start = cur + 1
            end = start
            while cur < len(expr):
                if expr[cur] == "{":
                    depth += 1
                elif expr[cur] == "}":
                    depth -= 1
                    if depth == 0:
                        end = cur
                        cur += 1
                        break
                cur += 1
            else:
                ok = False
                break
            args.append(expr[start:end])
        if not ok:
            break
        expr = expr[:idx] + replacer(*args) + expr[cur:]
    return expr


_TYPST_MATH_IDENTS = {
    # Greek
    "alpha", "beta", "gamma", "delta", "epsilon", "zeta", "eta", "theta", "iota",
    "kappa", "lambda", "mu", "nu", "xi", "pi", "rho", "sigma", "tau", "upsilon",
    "phi", "chi", "psi", "omega", "Gamma", "Delta", "Theta", "Lambda", "Xi", "Pi",
    "Sigma", "Phi", "Psi", "Omega",
    # Functions & operators
    "sin", "cos", "tan", "cot", "sec", "csc", "arcsin", "arccos", "arctan",
    "sinh", "cosh", "tanh", "ln", "log", "exp", "lim", "max", "min", "sup", "inf",
    "det", "gcd", "lcm", "mod", "deg", "arg", "dim", "ker", "Pr", "hom",
    "sqrt", "root", "abs", "norm", "floor", "ceil", "dif",
    # Accents, styles, relations & symbols
    "arrow", "vec", "hat", "tilde", "overline", "underline", "dot", "ddot", "double",
    "upright", "italic", "bold", "cal", "bb", "frak", "mono", "sans", "serif",
    "plus", "minus", "times", "div", "approx", "equiv", "prop", "oo", "diff",
    "nabla", "integral", "cont", "sum", "product", "harpoons", "rtlb", "quad",
    "wide", "thin", "med", "degree", "c", "cancel", "underbrace", "overbrace",
    "cases", "mat", "binom", "op", "limits", "scripts", "display", "inline",
    "lr", "mid", "class", "accent", "text", "rgb", "red", "blue", "green",
}

_TEXT_WORDS_IN_MATH = {
    "net", "eff", "rms", "avg", "max", "min", "eq", "cell", "tot", "total",
    "sys", "surr", "rev", "irrev", "atm", "mol", "kJ", "int", "ext", "rel",
    "init", "final", "const", "std", "aq", "sol", "obs", "calc", "true",
    "in", "out", "on", "off", "up", "down", "left", "right", "top", "bot",
}


def _space_typst_math_identifiers(expr: str) -> str:
    """Ensure multi-letter variable runs like `mg`, `RC`, `pH` never trigger Typst `unknown variable` errors."""
    # Preserve quoted strings ("...") untouched.
    parts = re.split(r'("[^"]*")', expr)
    out: list[str] = []
    for i, part in enumerate(parts):
        if i % 2 == 1:
            out.append(part)
            continue

        def _fix_ident(m: re.Match) -> str:
            tok = m.group(0)
            if len(tok) <= 1:
                return tok
            if "." in tok:
                segs = tok.split(".")
                if all(seg in _TYPST_MATH_IDENTS for seg in segs):
                    return tok
            if tok in _TYPST_MATH_IDENTS:
                return tok
            if tok in _TEXT_WORDS_IN_MATH:
                return f'"{tok}"'
            return " ".join(tok)

        out.append(re.sub(r"[A-Za-z]+(?:\.[A-Za-z]+)*", _fix_ident, part))
    return "".join(out)


def latex_to_typst_math(raw: str) -> str:
    """Convert common JEE LaTeX math syntax into native Typst 0.15 math (passes native Typst through safely)."""
    s = raw.strip()
    if not s:
        return ""

    if "\\" in s or "{" in s:
        # Handle \left( ... \right)
        s = re.sub(r"\\(?:left|right|big|Big|bigg|Bigg)\s*([()\[\]|.])", lambda m: "" if m.group(1) == "." else m.group(1), s)
        s = s.replace(r"\left\{", "{").replace(r"\right\}", "}")

        # Brace commands (padded with spaces so e.g. C\mathcal{E} -> C cal(E) and 2\pi\sqrt{m/k} -> 2 pi sqrt(m/k))
        s = _replace_brace_command(s, "frac", 2, lambda a, b: f" ({a})/({b}) ")
        s = _replace_brace_command(s, "dfrac", 2, lambda a, b: f" ({a})/({b}) ")
        s = _replace_brace_command(s, "tfrac", 2, lambda a, b: f" ({a})/({b}) ")
        s = _replace_brace_command(s, "sqrt", 1, lambda a: f" sqrt({a}) ")
        s = _replace_brace_command(s, "vec", 1, lambda a: f" arrow({a}) ")
        s = _replace_brace_command(s, "hat", 1, lambda a: f" hat({a}) ")
        s = _replace_brace_command(s, "bar", 1, lambda a: f" overline({a}) ")
        s = _replace_brace_command(s, "overline", 1, lambda a: f" overline({a}) ")
        s = _replace_brace_command(s, "dot", 1, lambda a: f" dot({a}) ")
        s = _replace_brace_command(s, "ddot", 1, lambda a: f" dot.double({a}) ")
        s = _replace_brace_command(s, "text", 1, lambda a: f' "{a}" ')
        s = _replace_brace_command(s, "mathrm", 1, lambda a: f' upright("{a}") ')
        s = _replace_brace_command(s, "mathbf", 1, lambda a: f" bold({a}) ")
        s = _replace_brace_command(s, "mathcal", 1, lambda a: f" cal({a}) ")
        s = _replace_brace_command(s, "ce", 1, lambda a: f" {_ce_to_typst(a)} ")

        # Common LaTeX symbols -> Typst math identifiers
        replacements = [
            (r"\Leftrightarrow", " <==> "),
            (r"\Rightarrow", " ==> "),
            (r"\rightarrow", " -> "),
            (r"\leftarrow", " <- "),
            (r"\rightleftharpoons", " harpoons.rtlb "),
            (r"\to", " -> "),
            (r"\implies", " ==> "),
            (r"\iff", " <==> "),
            (r"\propto", " prop "),
            (r"\approx", " approx "),
            (r"\equiv", " equiv "),
            (r"\neq", " != "),
            (r"\ne", " != "),
            (r"\leq", " <= "),
            (r"\le", " <= "),
            (r"\geq", " >= "),
            (r"\ge", " >= "),
            (r"\pm", " plus.minus "),
            (r"\mp", " minus.plus "),
            (r"\times", " times "),
            (r"\cdot", " dot.c "),
            (r"\infty", " oo "),
            (r"\partial", " diff "),
            (r"\nabla", " nabla "),
            (r"\int", " integral "),
            (r"\oint", " integral.cont "),
            (r"\sum", " sum "),
            (r"\prod", " product "),
            (r"\varepsilon", " epsilon "),
            (r"\varphi", " phi "),
            (r"\vartheta", " theta "),
            (r"\degree", " degree "),
            (r"\circ", " degree "),
            (r"\quad", " quad "),
            (r"\qquad", " wide "),
            (r"\,", " thin "),
            (r"\;", " med "),
            (r"\%", ' "%" '),
        ]
        for old, new in replacements:
            s = s.replace(old, new)

        # Greek letters & standard functions (\alpha -> alpha, \sin -> sin, \Delta -> Delta)
        s = re.sub(
            r"\\(alpha|beta|gamma|delta|epsilon|zeta|eta|theta|iota|kappa|lambda|mu|nu|xi|pi|rho|sigma|tau|upsilon|phi|chi|psi|omega|"
            r"Gamma|Delta|Theta|Lambda|Xi|Pi|Sigma|Phi|Psi|Omega|sin|cos|tan|cot|sec|csc|ln|log|exp|lim|max|min|det)",
            r" \1 ",
            s,
        )

        # Convert remaining _{...} and ^{...} braces into Typst parens _(...) and ^(...)
        s = _convert_sub_sup_braces(s)
        # Remove any leftover stray backslashes before spaces
        s = re.sub(r"\\(?=\s)", " ", s)

    s = _space_typst_math_identifiers(s)
    return re.sub(r"\s{2,}", " ", s).strip()


def _ce_to_typst(ce_str: str) -> str:
    """Lightweight offline converter for simple \\ce{...} chemical formulas in Typst math."""
    s = ce_str.strip()
    s = s.replace("<=>", " harpoons.rtlb ").replace("<->", " harpoons.rtlb ").replace("->", " -> ")
    # Convert trailing numbers after element symbols/parens into subscripts
    s = re.sub(r"([A-Za-z\)])(\d+)", r"\1_\2", s)
    # Wrap element runs in upright quotes so Typst doesn't italicize Fe or Na as F*e
    tokens = re.split(r"(\s+|->|harpoons\.rtlb|\+)", s)
    out = []
    for t in tokens:
        if not t or t.isspace() or t in ("->", "harpoons.rtlb", "+"):
            out.append(t)
        else:
            # Split element chunks & subscripts/charges
            parts = re.sub(r"([A-Z][a-z]?)", r'upright("\1")', t)
            out.append(parts)
    return "".join(out)


def _convert_sub_sup_braces(s: str) -> str:
    out: list[str] = []
    i = 0
    n = len(s)
    while i < n:
        if i + 1 < n and s[i] in ("_", "^") and s[i + 1] == "{":
            op = s[i]
            cur = i + 1
            depth = 0
            start = cur + 1
            while cur < n:
                if s[cur] == "{":
                    depth += 1
                elif s[cur] == "}":
                    depth -= 1
                    if depth == 0:
                        inner = _convert_sub_sup_braces(s[start:cur])
                        out.append(f"{op}({inner})")
                        i = cur + 1
                        break
                cur += 1
            else:
                out.append(s[i])
                i += 1
        else:
            out.append(s[i])
            i += 1
    return "".join(out)


def parse_markdown_to_spec(md_path: Path) -> dict[str, Any]:
    """Convert a chapters/<slug>.md document into the structured revision-sheet spec."""
    text = md_path.read_text(encoding="utf-8")
    lines = text.splitlines()

    title = md_path.stem.replace("-", " ").title()
    subject = "STEM"
    subtitle = "Core Invariants, Derivations & Pitfall Revision Sheet"
    sections: list[dict[str, Any]] = []
    global_traps: list[str] = []

    current_sec: dict[str, Any] | None = None
    i = 0
    while i < len(lines):
        line = lines[i].rstrip()
        stripped = line.strip()

        if stripped.startswith("# ") and not sections and current_sec is None:
            title = stripped[2:].strip()
            i += 1
            continue
        if stripped.startswith("## "):
            heading = stripped[3:].strip()
            current_sec = {"heading": heading, "items": [], "bullets": [], "traps": []}
            sections.append(current_sec)
            i += 1
            continue
        if stripped.startswith("### "):
            if current_sec is None:
                current_sec = {"heading": "Core Concepts", "items": [], "bullets": [], "traps": []}
                sections.append(current_sec)
            current_sec["bullets"].append(f"**{stripped[4:].strip()}**")
            i += 1
            continue

        # Blockquote / Trap callout
        if stripped.startswith(">"):
            q_lines = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                q_lines.append(lines[i].strip().lstrip(">").strip())
                i += 1
            joined = " ".join(q for q in q_lines if q and not re.match(r"^\[!(WARNING|CAUTION|IMPORTANT|TIP|NOTE)\]$", q, re.I))
            if joined:
                is_trap = bool(re.search(r"\b(trap|warning|caution|mistake|never|watch out|pitfall)\b", " ".join(q_lines), re.I))
                if current_sec is not None and is_trap:
                    current_sec["traps"].append(joined)
                elif is_trap:
                    global_traps.append(joined)
                elif current_sec is not None:
                    current_sec["bullets"].append(joined)
            continue

        # Display math block $$...$$
        if stripped.startswith("$$"):
            if current_sec is None:
                current_sec = {"heading": "Key Formulas", "items": [], "bullets": [], "traps": []}
                sections.append(current_sec)
            if stripped.endswith("$$") and len(stripped) > 4:
                math_expr = stripped[2:-2].strip()
                i += 1
            else:
                math_lines = [stripped[2:]]
                i += 1
                while i < len(lines) and "$$" not in lines[i]:
                    math_lines.append(lines[i])
                    i += 1
                if i < len(lines):
                    math_lines.append(lines[i].split("$$", 1)[0])
                    i += 1
                math_expr = " ".join(m.strip() for m in math_lines if m.strip())
            if math_expr:
                current_sec["items"].append({"label": "", "math": math_expr, "note": ""})
            continue

        # Markdown image ![caption](path)
        m_img = re.match(r"^!\[(.*?)\]\((.+?)\)$", stripped)
        if m_img:
            if current_sec is None:
                current_sec = {"heading": "Figures", "items": [], "bullets": [], "traps": []}
                sections.append(current_sec)
            cap, raw_p = m_img.group(1).strip(), m_img.group(2).strip()
            resolved = (md_path.parent / raw_p).resolve() if not Path(raw_p).is_absolute() else Path(raw_p)
            if resolved.exists():
                current_sec["figure"] = {"path": str(resolved), "caption": cap, "width": "85%"}
            i += 1
            continue

        # Markdown table
        if stripped.startswith("|") and "|" in stripped[1:] and i + 1 < len(lines) and re.match(r"^\|[\s:|-]+\|$", lines[i + 1].strip()):
            if current_sec is None:
                current_sec = {"heading": "Summary Table", "items": [], "bullets": [], "traps": []}
                sections.append(current_sec)
            headers = [c.strip() for c in stripped.strip("|").split("|")]
            i += 2
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                row = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                rows.append(row)
                i += 1
            current_sec["table"] = {"headers": headers, "rows": rows}
            continue

        # Bullet items (detect formula-style bullets: "- **Label**: $math$ — note")
        m_bul = re.match(r"^[-*+]\s+(.+)$", stripped)
        if m_bul:
            if current_sec is None:
                current_sec = {"heading": "Key Points", "items": [], "bullets": [], "traps": []}
                sections.append(current_sec)
            content = m_bul.group(1).strip()
            m_form = re.match(r"^\*\*(.+?)\*\*[:\s—–-]+\$([^$]+)\$(?:\s*[—–-]\s*(.+))?$", content)
            if m_form:
                current_sec["items"].append({
                    "label": m_form.group(1).strip(),
                    "math": m_form.group(2).strip(),
                    "note": (m_form.group(3) or "").strip(),
                })
            elif re.match(r"^(?:trap|pitfall|caution)\s*[:—-]", content, re.I):
                current_sec["traps"].append(re.sub(r"^(?:trap|pitfall|caution)\s*[:—-]\s*", "", content, flags=re.I))
            else:
                current_sec["bullets"].append(content)
            i += 1
            continue

        # Metadata lines like "- Subject: Physics" or "Subject: Physics"
        if not sections and re.match(r"^(?:\*\*)?subject(?:\*\*)?\s*:", stripped, re.I):
            subject = re.sub(r"^(?:\*\*)?subject(?:\*\*)?\s*:\s*", "", stripped, flags=re.I).strip("* ")
            i += 1
            continue

        # Plain paragraph
        if stripped and not stripped.startswith("---"):
            if current_sec is None:
                subtitle = stripped[:120]
            else:
                current_sec["bullets"].append(stripped)
        i += 1

    return {
        "title": title,
        "subject": subject,
        "subtitle": subtitle,
        "examTag": DEFAULT_EXAM_TAG,
        "columns": 2,
        "sections": sections,
        "traps": global_traps,
    }


def _stage_figure(fig_path_str: str, base_dir: Path, stage_dir: Path, idx: int) -> str | None:
    p = Path(fig_path_str).expanduser()
    if not p.is_absolute():
        cand = (base_dir / p).resolve()
        if not cand.exists():
            cand = (ROOT / p).resolve()
        p = cand
    if not p.exists():
        return None
    dest_name = f"fig_{idx:02d}{p.suffix.lower()}"
    shutil.copy2(p, stage_dir / dest_name)
    return dest_name


def build_typst_source(
    spec: dict[str, Any],
    base_dir: Path,
    stage_dir: Path,
    columns_override: int | None = None,
    paper: str = "a4",
    accent_name: str = "blue",
    exam_tag_override: str | None = None,
) -> str:
    theme = ACCENT_THEMES.get(accent_name, ACCENT_THEMES["blue"])
    title = str(spec.get("title", "Syntopik Revision Sheet"))
    subject = str(spec.get("subject", "General"))
    exam_tag = str(exam_tag_override or spec.get("examTag") or DEFAULT_EXAM_TAG)
    subtitle = str(spec.get("subtitle", ""))
    columns = int(columns_override or spec.get("columns", 2))
    margin = str(spec.get("margin", "8.5mm"))
    font_size = str(spec.get("fontSize", "8.3pt"))

    lines: list[str] = [
        f'#set page(paper: "{paper}", margin: (x: {margin}, y: {margin}), footer: context [',
        '  #set text(size: 7pt, fill: rgb("#64748b"))',
        f'  #grid(columns: (1fr, auto), align: (left, right), [Syntopik · {escape_typst_text(subject)} — {escape_typst_text(title)}], [Page #counter(page).display() of #counter(page).final().first()])',
        '])',
        f'#set text(font: ("Helvetica Neue", "Arial"), size: {font_size}, fill: rgb("#0f172a"))',
        '#set par(leading: 0.52em, spacing: 0.45em)',
        '#set list(indent: 0.4em, body-indent: 0.35em, spacing: 0.32em)',
        '',
        # Header banner (full width across columns)
        f'#block(width: 100%, fill: rgb("{theme["dark"]}"), inset: (x: 10pt, y: 7pt), radius: 5pt)[',
        '  #grid(',
        '    columns: (1fr, auto),',
        '    align: (left + horizon, right + horizon),',
        '    [',
        f'      #text(size: 13pt, weight: "bold", fill: white)[{escape_typst_text(title)}]',
        *(
            [f'      #linebreak() #text(size: 8pt, fill: rgb("#cbd5e1"))[{escape_typst_text(subtitle)}]']
            if subtitle
            else []
        ),
        '    ],',
        '    [',
        f'      #box(fill: rgb("{theme["primary"]}"), inset: (x: 6pt, y: 3pt), radius: 99pt)[#text(size: 7.5pt, weight: "bold", fill: white)[{escape_typst_text(subject)} · {escape_typst_text(exam_tag)}]]',
        '    ]',
        '  )',
        ']',
        '#v(4pt)',
        '',
        # Helper macros
        f'#let sec_box(title, body) = block(width: 100%, stroke: (paint: rgb("{theme["border"]}"), thickness: 0.7pt), radius: 4pt, clip: true, breakable: true)[',
        f'  #block(width: 100%, fill: rgb("{theme["soft"]}"), inset: (x: 6pt, y: 3.5pt), below: 0pt)[',
        f'    #text(size: 8.8pt, weight: "bold", fill: rgb("{theme["primary"]}"))[#title]',
        '  ]',
        '  #block(width: 100%, inset: (x: 6pt, y: 4.5pt))[#body]',
        ']',
        '',
        '#let trap_box(title, body) = block(width: 100%, fill: rgb("#fef2f2"), stroke: (left: 2.5pt + rgb("#dc2626"), rest: 0.5pt + rgb("#fecaca")), inset: (x: 6pt, y: 4pt), radius: 3pt, breakable: false)[',
        '  #text(size: 7.8pt, weight: "bold", fill: rgb("#b91c1c"))[TRAP · #title]',
        '  #v(1.5pt)',
        '  #text(size: 7.8pt, fill: rgb("#7f1d1d"))[#body]',
        ']',
        '',
        '#let formula_row(lbl, eq, note) = block(width: 100%, fill: rgb("#f8fafc"), stroke: 0.4pt + rgb("#e2e8f0"), inset: (x: 5pt, y: 3pt), radius: 2.5pt, below: 3pt)[',
        '  #if lbl != "" [#text(size: 7.6pt, weight: "bold", fill: rgb("#334155"))[#lbl] #h(4pt)]',
        '  #align(center)[#eq]',
        '  #if note != "" [#v(1pt) #text(size: 7.3pt, style: "italic", fill: rgb("#475569"))[#note]]',
        ']',
        '',
        f'#columns({columns}, gutter: 7pt)[',
    ]

    fig_counter = 0
    for sec in spec.get("sections", []):
        heading = str(sec.get("heading", "Section"))
        body_parts: list[str] = []

        for item in sec.get("items", []):
            lbl = escape_typst_text(str(item.get("label", "")))
            math_raw = str(item.get("math", "")).strip()
            note = escape_typst_text(str(item.get("note", "")))
            typ_math = latex_to_typst_math(math_raw)
            eq_block = f"$ {typ_math} $" if typ_math else "[]"
            body_parts.append(f'  #formula_row([{lbl}], [{eq_block}], [{note}])')

        for b in sec.get("bullets", []):
            body_parts.append(f'  - {escape_typst_text(str(b))}')

        tbl = sec.get("table")
        if isinstance(tbl, dict) and tbl.get("headers"):
            headers = tbl["headers"]
            rows = tbl.get("rows", [])
            ncols = len(headers)
            col_spec = ", ".join(["1fr"] * ncols)
            cells = [f'[*#text(size: 7.5pt)[{escape_typst_text(str(h))}]*]' for h in headers]
            for r in rows:
                padded = list(r) + [""] * max(0, ncols - len(r))
                for c in padded[:ncols]:
                    cells.append(f'[#text(size: 7.5pt)[{escape_typst_text(str(c))}]]')
            body_parts.append(
                f'  #v(2pt)\n  #table(columns: ({col_spec}), inset: 3.2pt, stroke: 0.4pt + rgb("#cbd5e1"), '
                f'fill: (_, row) => if row == 0 {{ rgb("{theme["soft"]}") }} else {{ white }}, {", ".join(cells)})'
            )

        figs = []
        if sec.get("figure"):
            figs.append(sec["figure"])
        if isinstance(sec.get("figures"), list):
            figs.extend(sec["figures"])
        for fig in figs:
            if not isinstance(fig, dict) or not fig.get("path"):
                continue
            fig_counter += 1
            staged = _stage_figure(str(fig["path"]), base_dir, stage_dir, fig_counter)
            if staged:
                width = str(fig.get("width", "85%"))
                cap = str(fig.get("caption", ""))
                body_parts.append(f'  #v(2pt)\n  #align(center)[#image("{staged}", width: {width})]')
                if cap:
                    body_parts.append(f'  #align(center)[#text(size: 7.2pt, fill: rgb("#475569"))[{escape_typst_text(cap)}]]')

        sec_traps = sec.get("traps", [])
        if sec_traps:
            trap_items = "\n".join(f"    - {escape_typst_text(str(t))}" for t in sec_traps)
            body_parts.append(f'  #v(2pt)\n  #trap_box([{escape_typst_text(heading)}], [\n{trap_items}\n  ])')

        joined_body = "\n".join(body_parts) if body_parts else "  _No items._"
        lines.append(f'#sec_box([{escape_typst_text(heading)}], [\n{joined_body}\n])')
        lines.append('#v(4pt)')

    global_traps = spec.get("traps", [])
    if global_traps:
        trap_items = "\n".join(f"  - {escape_typst_text(str(t))}" for t in global_traps)
        lines.append(f'#trap_box([Top Pitfalls, Edge Cases & Misconceptions], [\n{trap_items}\n])')
        lines.append('#v(4pt)')

    quick_checks = spec.get("quickChecks", [])
    if quick_checks:
        qc_items = "\n".join(f"  - {escape_typst_text(str(q))}" for q in quick_checks)
        lines.append(f'#sec_box([Rapid Dimensional & Limiting-Case Sanity Checks], [\n{qc_items}\n])')

    lines.append(']')
    return "\n".join(lines) + "\n"


def count_pdf_pages(pdf_path: Path) -> int | None:
    try:
        import pymupdf as fitz

        with fitz.open(str(pdf_path)) as doc:
            return len(doc)
    except Exception:
        return None


def build_anti_mastery_spec(
    kg_dir: Path | str | None = None,
    *,
    chapter: str | None = None,
    anti_mastery: bool = True,
    max_pages: int = 1,
    title: str | None = None,
) -> dict[str, Any]:
    """Build a 1-page Anti-Mastery crib sheet spec directly from `knowledge-graph/`.

    Pulls weak/misconception/trapped propositions and `mistakes.jsonl` decision cues while
    auto-excluding already-solid nodes (`R >= 0.90` & `lapses == 0` & 0 mistake hits) when
    `anti_mastery=True`, so 100% of A4 real estate goes to personal traps & weak invariants.
    """
    from datetime import datetime, timezone

    resolved_kg = resolve_kg_dir(explicit=kg_dir)
    props = load_jsonl(resolved_kg / "propositions.jsonl")
    mistakes = load_jsonl(resolved_kg / "mistakes.jsonl")
    revlog = load_jsonl(resolved_kg / "revlog.jsonl")
    params = load_fsrs_parameters(resolved_kg)
    now = datetime.now(timezone.utc)
    today = today_iso()

    ch_query = str(chapter or "").strip().lower()

    def _matches_chapter(ch_val: Any, pid_val: Any = "", subj_val: Any = "") -> bool:
        if not ch_query:
            return True
        ch_s = str(ch_val or "").strip().lower()
        ch_slug = _slugify(ch_s) if ch_s else ""
        pid_s = str(pid_val or "").strip().lower()
        subj_s = str(subj_val or "").strip().lower()
        return (
            ch_query == ch_s
            or ch_query == ch_slug
            or ch_query in ch_s
            or ch_query in ch_slug
            or ch_query in pid_s
            or ch_query == subj_s
        )

    revlog_lapses: dict[str, int] = {}
    for ev in revlog:
        pid = str(ev.get("propId") or "").strip()
        r = ev.get("rating")
        if pid and (r == 1 or str(r or "").lower() == "again"):
            revlog_lapses[pid] = revlog_lapses.get(pid, 0) + 1

    mistakes_filtered = [
        m for m in mistakes if _matches_chapter(m.get("chapter"), m.get("propId"), m.get("subject"))
    ]
    mistake_hits_by_prop: dict[str, int] = {}
    for m in mistakes_filtered:
        pid = str(m.get("propId") or "").strip()
        if pid:
            mistake_hits_by_prop[pid] = mistake_hits_by_prop.get(pid, 0) + 1

    included_props: list[dict[str, Any]] = []
    excluded_solid_ids: list[str] = []
    subjects_seen: list[str] = []

    for p in props:
        pid = str(p.get("id") or "").strip()
        if not pid:
            continue
        if not _matches_chapter(p.get("chapter"), pid, p.get("subject")):
            continue
        subj = str(p.get("subject") or "General").strip()
        if subj and subj not in subjects_seen:
            subjects_seen.append(subj)

        status = str(p.get("status") or "planned").strip().lower()
        lapses = max(int(p.get("lapses") or 0), revlog_lapses.get(pid, 0))
        m_hits = mistake_hits_by_prop.get(pid, 0)
        due_str = str(p.get("nextReview") or "")[:10]

        if isinstance(p.get("fsrs"), dict) and isinstance(p["fsrs"].get("stability"), (int, float)):
            r_val = float(card_retrievability(p["fsrs"], now, parameters=params, fractional_days=True))
        elif status == "solid":
            bkt_v = float(p.get("bktMastery")) if isinstance(p.get("bktMastery"), (int, float)) else 0.92
            r_val = 0.95 if (bkt_v >= 0.85 and (not due_str or due_str > today)) else 0.78
        elif status == "misconception":
            r_val = 0.15
        elif status == "shaky":
            r_val = 0.40
        else:
            r_val = 0.25

        # Auto-exclude mastered solid nodes (R >= 0.90 & lapses == 0 & no personal mistake hits).
        if anti_mastery and status == "solid" and r_val >= 0.90 and lapses == 0 and m_hits == 0:
            excluded_solid_ids.append(pid)
            continue

        included_props.append(
            {
                "id": pid,
                "title": str(p.get("title") or pid).strip(),
                "chapter": str(p.get("chapter") or "General").strip(),
                "status": status,
                "notes": str(p.get("notes") or "").strip(),
                "retrievability": round(r_val, 3),
                "lapses": lapses,
                "mistakeHits": m_hits,
            }
        )

    prio_rank = {"misconception": 0, "shaky": 1, "planned": 2, "solid": 3}
    included_props.sort(
        key=lambda x: (
            prio_rank.get(x["status"], 4),
            -x["mistakeHits"],
            -x["lapses"],
            x["retrievability"],
            x["id"],
        )
    )

    cap_props = 8 if max_pages == 1 else 16
    cap_traps = 6 if max_pages == 1 else 12
    chosen_props = included_props[:cap_props]

    sections: list[dict[str, Any]] = []
    if chosen_props:
        inv_bullets: list[str] = []
        for item in chosen_props:
            tag = item["status"].upper()
            note = f" — {item['notes']}" if item["notes"] else ""
            inv_bullets.append(
                f"**[{tag} · R≈{round(item['retrievability'] * 100)}%]** `{item['id']}`: {item['title']}{note}"
            )
        sections.append(
            {
                "heading": "1. Weak & Unstable Invariants (Mastered Solid Nodes Excluded)",
                "bullets": inv_bullets,
            }
        )

    # Personal mistake decision rules ("If condition X -> Do Y, Avoid Z")
    decision_bullets: list[str] = []
    global_traps: list[str] = []
    seen_cues: set[str] = set()
    for m in mistakes_filtered:
        cue = " ".join(str(m.get("triggerCue") or "").split())
        wrong = " ".join(str(m.get("wrongMove") or "").split())
        fix = " ".join(str(m.get("correctMove") or "").split())
        cat = str(m.get("category") or "trap").replace("_", " ").title()
        key = f"{cue.lower()}|{fix.lower()}"
        if not cue or key in seen_cues:
            continue
        seen_cues.add(key)
        if len(decision_bullets) < cap_traps:
            decision_bullets.append(f"**[{cat}] If:** {cue} -> **Do:** {fix}")
        if wrong and len(global_traps) < (5 if max_pages == 1 else 10):
            global_traps.append(f"**[{cat}]** Avoid: {wrong} — **Instead:** {fix}")

    if decision_bullets:
        sections.append(
            {
                "heading": "2. Personal Decision Rules & Brain-Freeze Guardrails",
                "bullets": decision_bullets,
            }
        )

    if not sections:
        sections.append(
            {
                "heading": "1. Clean Slate — Zero Weak Invariants",
                "bullets": [
                    f"Excluded {len(excluded_solid_ids)} already-mastered solid node(s) (R >= 90%, 0 lapses).",
                    "No active misconceptions, shaky edges, or logged mistake traps match this filter.",
                ],
            }
        )

    subject_label = " / ".join(subjects_seen[:2]) if subjects_seen else "General"
    sheet_title = (
        title.strip()
        if title and title.strip()
        else (f"{chapter} — Anti-Mastery 1-Page Crib Sheet" if chapter else "Personal Anti-Mastery 1-Page Crib Sheet")
    )
    subtitle = (
        f"Auto-excluded {len(excluded_solid_ids)} solid node(s) (R>=90%, 0 lapses) · "
        f"Kept {len(chosen_props)} weak invariant(s) & {len(decision_bullets)} trap rule(s)"
    )

    return {
        "slug": _slugify(chapter or "anti-mastery-crib"),
        "title": sheet_title,
        "subject": subject_label,
        "examTag": "Anti-Mastery 1p Crib" if anti_mastery else DEFAULT_EXAM_TAG,
        "subtitle": subtitle,
        "columns": 2,
        "margin": "7.5mm" if max_pages == 1 else "8.5mm",
        "fontSize": "7.8pt" if max_pages == 1 else "8.3pt",
        "sections": sections,
        "traps": global_traps,
        "quickChecks": [
            "Check sign conventions & positive axis orientation before substituting numbers.",
            "Verify validity assumptions (ideal, small-angle, boundary/edge cases) before selecting a law.",
            "Run dimensional & limiting-case sanity check ($x \\to 0, \\infty$) on final expressions.",
        ],
        "antiMasteryMeta": {
            "antiMastery": bool(anti_mastery),
            "chapterFilter": chapter or None,
            "maxPages": int(max_pages),
            "includedPropIds": [x["id"] for x in chosen_props],
            "excludedSolidIds": sorted(excluded_solid_ids),
            "excludedSolidCount": len(excluded_solid_ids),
            "trapRulesCount": len(decision_bullets),
        },
    }


def cmd_build(args: argparse.Namespace) -> None:
    from_kg = bool(getattr(args, "from_kg", False))
    anti_mastery = bool(getattr(args, "anti_mastery", False))
    max_pages = int(getattr(args, "pages", None) or 2)
    spec_only = bool(getattr(args, "spec_only", False))
    out_spec_arg = getattr(args, "out_spec", None)

    if from_kg:
        spec = build_anti_mastery_spec(
            getattr(args, "kg_dir", None),
            chapter=getattr(args, "chapter", None),
            anti_mastery=anti_mastery,
            max_pages=max_pages,
        )
        base_dir = resolve_kg_dir(explicit=getattr(args, "kg_dir", None)).parent
        slug = _slugify(str(spec.get("slug") or getattr(args, "chapter", None) or "anti-mastery-crib"))
        input_label = f"kg:{resolve_kg_dir(explicit=getattr(args, 'kg_dir', None))}"
    else:
        if not args.input:
            raise SystemExit("Provide an input .json/.md file OR pass --from-kg [--anti-mastery].")
        in_path = Path(args.input).expanduser().resolve()
        if not in_path.exists():
            raise SystemExit(f"Input file not found: {in_path}")
        if in_path.suffix.lower() == ".json":
            spec = json.loads(in_path.read_text(encoding="utf-8"))
        else:
            spec = parse_markdown_to_spec(in_path)
        if max_pages == 1:
            spec = dict(spec)
            spec.setdefault("margin", "7.5mm")
            spec.setdefault("fontSize", "7.8pt")
        base_dir = in_path.parent
        slug = _slugify(str(spec.get("slug") or in_path.stem))
        input_label = str(in_path)

    if out_spec_arg:
        save_json_atomic(Path(out_spec_arg).expanduser().resolve(), spec)

    if spec_only:
        print(json.dumps(spec, indent=2, ensure_ascii=False))
        return

    typst_bin = find_typst()
    out_pdf = (
        Path(args.out).expanduser().resolve()
        if args.out
        else (ROOT / "chapters" / f"{slug}-sheet.pdf").resolve()
    )
    out_pdf.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="typst-sheet-") as tmp:
        stage_dir = Path(tmp)
        typ_src = build_typst_source(
            spec,
            base_dir=base_dir,
            stage_dir=stage_dir,
            columns_override=args.columns,
            paper=args.paper,
            accent_name=args.accent,
            exam_tag_override=getattr(args, "exam_tag", None),
        )
        typ_file = stage_dir / "sheet.typ"
        typ_file.write_text(typ_src, encoding="utf-8")

        if args.keep_typ:
            saved_typ = out_pdf.with_suffix(".typ")
            saved_typ.write_text(typ_src, encoding="utf-8")

        proc = subprocess.run(
            [typst_bin, "compile", "--root", str(stage_dir), str(typ_file), str(out_pdf)],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            raise SystemExit(f"Typst compilation failed:\n{proc.stderr}\n--- Generated .typ ---\n{typ_src}")

    pages = count_pdf_pages(out_pdf)
    payload: dict[str, Any] = {
        "input": input_label,
        "output": str(out_pdf),
        "pages": pages,
        "bytes": out_pdf.stat().st_size,
        "typst": typst_bin,
        "keepTyp": str(out_pdf.with_suffix(".typ")) if args.keep_typ else None,
        "withinTwoPages": (pages is not None and pages <= 2),
        "withinOnePage": (pages is not None and pages <= 1),
    }
    if "antiMasteryMeta" in spec:
        payload["antiMasteryMeta"] = spec["antiMasteryMeta"]
    print(json.dumps(payload, indent=2, ensure_ascii=False))


def cmd_schema(_args: argparse.Namespace) -> None:
    sample = {
        "title": "Simple Harmonic Motion & Oscillations",
        "subject": "Physics",
        "examTag": DEFAULT_EXAM_TAG,
        "subtitle": "Unconditional Roots · Key Derivations · Critical Pitfalls",
        "columns": 2,
        "sections": [
            {
                "heading": "1. Defining Equation & Kinematics",
                "items": [
                    {
                        "label": "Restoring Law",
                        "math": r"a = -\omega^2 x \Leftrightarrow \ddot{x} + \omega^2 x = 0",
                        "note": "Unconditional iff F_net = -k(x - x_0)",
                    },
                    {
                        "label": "Velocity-Position",
                        "math": r"v = \pm \omega \sqrt{A^2 - x^2}",
                    },
                ],
                "bullets": ["Phase advances by $\\pi/2$ at each derivative ($x \\to v \\to a$)."],
                "table": {
                    "headers": ["Quantity", "Mean (x=0)", "Extreme (x=A)"],
                    "rows": [["|v|", "$\\omega A$", "0"], ["|a|", "0", "$\\omega^2 A$"]],
                },
                "figure": {"path": "demos/rc.svg", "caption": "Optional SVG/PNG diagram", "width": "80%"},
                "traps": ["Always measure displacement from equilibrium $x_0$, NOT natural spring length."],
            }
        ],
        "traps": [
            "Time period of a spring-mass system is independent of $g$ (gravity only shifts equilibrium $x_0$).",
        ],
        "quickChecks": [
            "Dimensional check: $\\sqrt{k/m}$ has units $\\text{s}^{-1}$.",
        ],
    }
    print(json.dumps(sample, indent=2))


def main(argv: list[str] | None = None) -> None:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    # Allow shorthand `cheatsheet_pdf.py input.json --out ...` without requiring `build`.
    if raw_argv and raw_argv[0] not in ("build", "schema", "-h", "--help"):
        raw_argv.insert(0, "build")

    parser = argparse.ArgumentParser(description="Compile 1-2 page Syntopik Mastery, Anti-Mastery & Pitfall Revision Sheet PDFs with Typst")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_b = sub.add_parser("build", help="Compile JSON, Markdown, or live Knowledge Graph (--from-kg --anti-mastery) to PDF")
    p_b.add_argument("input", nargs="?", default=None, help="Path to structured .json spec or .md chapter file (omit when using --from-kg)")
    p_b.add_argument("--from-kg", action="store_true", help="Build spec directly from knowledge-graph/propositions.jsonl + mistakes.jsonl")
    p_b.add_argument("--kg-dir", default=None, help="Override knowledge-graph directory for --from-kg")
    p_b.add_argument("--chapter", default=None, help="Filter --from-kg to a chapter name or slug")
    p_b.add_argument("--anti-mastery", action="store_true", help="Auto-exclude solid mastered nodes (R >= 0.90 & lapses == 0) so 100%% of page holds weak traps")
    p_b.add_argument("--pages", type=int, choices=[1, 2], default=None, help="Target page budget (1 = ultra-dense 1-page crib layout)")
    p_b.add_argument("--out-spec", default=None, help="Optional path to save generated JSON spec")
    p_b.add_argument("--spec-only", action="store_true", help="Emit generated JSON spec to stdout without running Typst")
    p_b.add_argument("--out", help="Output PDF path (default: chapters/<slug>-sheet.pdf)")
    p_b.add_argument("--columns", type=int, choices=[1, 2, 3], help="Column count (default: 2)")
    p_b.add_argument("--paper", default="a4", choices=["a4", "us-letter"])
    p_b.add_argument("--accent", default="blue", choices=list(ACCENT_THEMES.keys()))
    p_b.add_argument("--exam-tag", default=None, help=f"Override header badge tag (default: spec examTag or '{DEFAULT_EXAM_TAG}')")
    p_b.add_argument("--keep-typ", action="store_true", help="Also save generated .typ source alongside PDF")
    p_b.set_defaults(func=cmd_build)

    p_s = sub.add_parser("schema", help="Print an example structured JSON spec for cheatsheet_pdf.py")
    p_s.set_defaults(func=cmd_schema)

    args = parser.parse_args(raw_argv)
    args.func(args)


if __name__ == "__main__":
    main()
