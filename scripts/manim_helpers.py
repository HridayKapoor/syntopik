"""Reusable helpers for 3Blue1Brown-style Manim explanation videos in Syntopik.

Importing this module automatically discovers and prepends the local lightweight
TinyTeX binary directory to ``os.environ["PATH"]`` so native Manim ``MathTex``,
``Tex``, and ``TransformMatchingTex`` work reliably in any subshell even when
shell rc files were not sourced.

Exports:
- ``TINYTEX_BIN`` / ``ensure_tinytex_in_path()`` / ``has_native_latex()``
- ``CHEM_TEX_TEMPLATE`` (``amsmath``, ``amssymb``, ``mathtools``, ``physics``,
  ``cancel``, ``xcolor``, ``dsfont``, and ``mhchem`` ``\\ce{}`` preloaded)
- ``STEM_TEX_TEMPLATE`` (STEM math/physics template without ``mhchem``)
- ``ChemMathTex`` convenience wrapper around ``MathTex(..., tex_template=CHEM_TEX_TEMPLATE)``
- ``PALETTE`` consistent dark-theme color palette
- ``LaTeXMath`` and ``TypstMath`` fast vector-SVG fallbacks (no TeX needed)
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Iterable

# ---------------------------------------------------------------------------
# Automatic TinyTeX PATH discovery (runs BEFORE importing Manim)
# ---------------------------------------------------------------------------


def _candidate_tex_bin_dirs() -> list[Path]:
    """Return ordered candidate directories that may hold TinyTeX/TeX binaries across macOS, Linux & Windows."""
    candidates: list[Path] = []
    home = Path.home()

    for env_var in ("TINYTEX_BIN", "TEXLIVE_BIN"):
        raw = os.environ.get(env_var)
        if raw:
            candidates.append(Path(raw).expanduser())

    roots = [
        home / "Library" / "TinyTeX" / "bin",
        home / ".TinyTeX" / "bin",
    ]
    appdata = os.environ.get("APPDATA")
    if appdata:
        roots.append(Path(appdata) / "TinyTeX" / "bin")

    known_archs = (
        "universal-darwin",
        "aarch64-darwin",
        "x86_64-darwin",
        "x86_64-linux",
        "aarch64-linux",
        "windows",
        "win32",
    )
    for root in roots:
        for arch in known_archs:
            candidates.append(root / arch)
        if root.is_dir():
            for child in sorted(root.iterdir()):
                if child.is_dir() and child not in candidates:
                    candidates.append(child)

    which_latex = shutil.which("latex")
    if which_latex:
        candidates.append(Path(which_latex).resolve().parent)

    candidates.extend([Path("/Library/TeX/texbin"), Path("/usr/texbin"), home / ".local" / "bin"])
    return candidates


def _is_executable(path: Path) -> bool:
    if path.is_file() and os.access(path, os.X_OK):
        return True
    if os.name == "nt":
        for ext in (".exe", ".cmd", ".bat"):
            cand = path.with_suffix(ext) if not path.suffix else Path(str(path) + ext)
            if cand.is_file():
                return True
    return False


def ensure_tinytex_in_path(extra_dirs: Iterable[Path] = ()) -> Path | None:
    """Prepend the discovered TinyTeX/TeX bin directory to ``os.environ["PATH"]``.

    Prioritizes a directory containing both ``latex`` and ``dvisvgm`` so Manim's
    native ``MathTex``/``Tex`` pipeline works even in stripped subshells.
    """
    candidates = [Path(d).expanduser() for d in extra_dirs] + _candidate_tex_bin_dirs()

    chosen: Path | None = None
    for d in candidates:
        if d.is_dir() and _is_executable(d / "latex") and _is_executable(d / "dvisvgm"):
            chosen = d.resolve()
            break

    if chosen is None:
        for d in candidates:
            if d.is_dir() and _is_executable(d / "latex"):
                chosen = d.resolve()
                break

    if chosen is not None:
        chosen_str = str(chosen)
        parts = [p for p in os.environ.get("PATH", "").split(os.pathsep) if p]
        filtered = [p for p in parts if p != chosen_str]
        os.environ["PATH"] = os.pathsep.join([chosen_str, *filtered])

    return chosen


TINYTEX_BIN: Path | None = ensure_tinytex_in_path()


def has_native_latex() -> bool:
    """Return True if both ``latex`` and ``dvisvgm`` are resolvable on PATH."""
    return bool(shutil.which("latex") and shutil.which("dvisvgm"))


def tinytex_status() -> dict[str, str | bool | None]:
    """Return diagnostic paths for TinyTeX binaries used by Manim."""
    return {
        "tinytex_bin": str(TINYTEX_BIN) if TINYTEX_BIN else None,
        "latex": shutil.which("latex"),
        "pdflatex": shutil.which("pdflatex"),
        "xelatex": shutil.which("xelatex"),
        "dvisvgm": shutil.which("dvisvgm"),
        "ready": has_native_latex(),
    }


# ---------------------------------------------------------------------------
# Manim imports & TeX templates
# ---------------------------------------------------------------------------

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from manim import MathTex, SVGMobject, TexTemplate, WHITE, config

CACHE_DIR = Path(__file__).resolve().parent.parent / "animations" / "_cache"

PALETTE = {
    "BG": "#0e1117",
    "TEXT": "#e6edf3",
    "MUTED": "#8b949e",
    "BLUE": "#58a6ff",
    "CYAN": "#39c5cf",
    "YELLOW": "#f2cc60",
    "GREEN": "#56d364",
    "RED": "#ff7b72",
    "PURPLE": "#d2a8ff",
    "ORANGE": "#ffa657",
}


def build_stem_tex_template() -> TexTemplate:
    """Build a Manim TexTemplate with full STEM math/physics packages preloaded."""
    template = TexTemplate()
    template.add_to_preamble(
        "\n".join(
            [
                r"\usepackage{mathtools}",
                r"\usepackage{dsfont}",
                r"\usepackage{xcolor}",
                r"\usepackage{physics}",
                r"\usepackage{cancel}",
            ]
        )
    )
    return template


def build_chem_tex_template() -> TexTemplate:
    """Build a Manim TexTemplate with STEM packages + ``mhchem`` (``\\ce{}``) preloaded."""
    template = build_stem_tex_template()
    template.add_to_preamble(r"\usepackage[version=4]{mhchem}")
    return template


STEM_TEX_TEMPLATE: TexTemplate = build_stem_tex_template()
CHEM_TEX_TEMPLATE: TexTemplate = build_chem_tex_template()


def use_chem_tex_template() -> TexTemplate:
    """Set ``CHEM_TEX_TEMPLATE`` as Manim's global default ``config.tex_template``."""
    config.tex_template = CHEM_TEX_TEMPLATE
    return CHEM_TEX_TEMPLATE


def use_stem_tex_template() -> TexTemplate:
    """Set ``STEM_TEX_TEMPLATE`` as Manim's global default ``config.tex_template``."""
    config.tex_template = STEM_TEX_TEMPLATE
    return STEM_TEX_TEMPLATE


def ChemMathTex(*tex_strings: str, **kwargs) -> MathTex:
    """Convenience ``MathTex`` wrapper preconfigured with ``CHEM_TEX_TEMPLATE``."""
    kwargs.setdefault("tex_template", CHEM_TEX_TEMPLATE)
    return MathTex(*tex_strings, **kwargs)


# ---------------------------------------------------------------------------
# Fast zero-TeX vector fallbacks (Matplotlib mathtext & Typst)
# ---------------------------------------------------------------------------


def _normalize_mathtext(latex: str) -> str:
    """Normalize common LaTeX macros matplotlib.mathtext does not implement."""
    s = latex.strip()
    if s.startswith("$") and s.endswith("$"):
        s = s[1:-1]
    s = re.sub(r"\\(?:Big|big|Bigg|bigg)[lr]?", "", s)
    s = re.sub(r"\\text\{([^}]*)\}", r"\\mathrm{\1}", s)
    s = s.replace(r"\implies", r"\Rightarrow").replace(r"\iff", r"\Leftrightarrow")
    return f"${s}$"


def LaTeXMath(
    latex: str,
    color=WHITE,
    height: float = 0.65,
    font_size: int = 36,
) -> SVGMobject:
    """Fast fallback: compile a LaTeX math string via Matplotlib into an SVGMobject."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    math_str = _normalize_mathtext(latex)

    key = hashlib.sha256(f"mpl:{font_size}:{math_str}".encode("utf-8")).hexdigest()[:16]
    svg_path = CACHE_DIR / f"math-{key}.svg"

    if not svg_path.exists():
        plt.rcParams["svg.fonttype"] = "path"
        plt.rcParams["mathtext.fontset"] = "cm"
        fig = plt.figure(figsize=(0.01, 0.01))
        fig.patch.set_alpha(0.0)
        fig.text(0.0, 0.0, math_str, fontsize=font_size, color="white")
        fig.savefig(
            svg_path,
            format="svg",
            transparent=True,
            bbox_inches="tight",
            pad_inches=0.04,
        )
        plt.close(fig)

    mob = SVGMobject(str(svg_path))
    mob.set_color(color)
    if height is not None:
        mob.scale_to_fit_height(height)
    return mob


def TypstMath(
    typst_expr: str,
    color=WHITE,
    height: float = 0.7,
) -> SVGMobject:
    """Fast fallback: compile a Typst expression into a vector Manim SVGMobject."""
    typst_bin = shutil.which("typst")
    if not typst_bin:
        raise RuntimeError("typst binary not found on PATH; use MathTex() or LaTeXMath() instead.")

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    src = (
        "#set page(width: auto, height: auto, margin: 4pt, fill: none)\n"
        "#set text(fill: white, size: 18pt)\n"
        f"{typst_expr.strip()}\n"
    )
    key = hashlib.sha256(f"typst:{src}".encode("utf-8")).hexdigest()[:16]
    typ_path = CACHE_DIR / f"typst-{key}.typ"
    svg_path = CACHE_DIR / f"typst-{key}.svg"

    if not svg_path.exists():
        typ_path.write_text(src, encoding="utf-8")
        subprocess.run(
            [typst_bin, "compile", str(typ_path), str(svg_path), "--format", "svg"],
            check=True,
            capture_output=True,
            text=True,
        )

    mob = SVGMobject(str(svg_path))
    mob.set_color(color)
    if height is not None:
        mob.scale_to_fit_height(height)
    return mob
