"""Showcase scene verifying native TinyTeX MathTex, TransformMatchingTex, \\cancel{}, and \\ce{}.

Run from the workspace root (even in a stripped shell without TinyTeX on PATH):
    .venv/bin/manim -qm --media_dir media animations/sample_shm_mathtex.py NativeLaTeXShowcase
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

# Import manim_helpers FIRST so TinyTeX is auto-prepended to os.environ["PATH"]
from manim_helpers import CHEM_TEX_TEMPLATE, PALETTE
from manim import *


class NativeLaTeXShowcase(Scene):
    """Demonstrates native MathTex, TransformMatchingTex, \\cancel{}, \\underbrace, and \\ce{}."""

    def construct(self) -> None:
        self.camera.background_color = PALETTE["BG"]

        title = Text(
            "Native LaTeX · Manim STEM & Chemistry",
            font_size=34,
            color=PALETTE["TEXT"],
            weight=BOLD,
        ).to_edge(UP, buff=0.4)
        underline = Line(
            LEFT * 5.8, RIGHT * 5.8, stroke_width=1.5, color=PALETTE["MUTED"]
        ).next_to(title, DOWN, buff=0.18)

        self.play(FadeIn(title, shift=DOWN * 0.15), Create(underline), run_time=0.6)

        # 1. Native MathTex + 3Blue1Brown TransformMatchingTex with \underbrace
        shm_label = Text(
            "1. SHM Equation (TransformMatchingTex + \\underbrace)",
            font_size=20,
            color=PALETTE["CYAN"],
        ).next_to(underline, DOWN, buff=0.35).to_edge(LEFT, buff=0.75)

        eq1 = MathTex(
            r"m",
            r"\frac{d^2 x}{dt^2}",
            r"=",
            r"-",
            r"k",
            r"x",
            tex_template=CHEM_TEX_TEMPLATE,
            font_size=46,
        ).next_to(shm_label, DOWN, buff=0.48).set_x(0)
        eq1.set_color_by_tex("x", PALETTE["YELLOW"])
        eq1.set_color_by_tex("k", PALETTE["CYAN"])

        eq2 = MathTex(
            r"\frac{d^2 x}{dt^2}",
            r"=",
            r"-",
            r"\underbrace{\frac{k}{m}}_{\omega^2}",
            r"x",
            tex_template=CHEM_TEX_TEMPLATE,
            font_size=46,
        ).move_to(eq1, aligned_edge=UP)
        eq2.set_color_by_tex("x", PALETTE["YELLOW"])
        eq2[3].set_color(PALETTE["CYAN"])

        self.play(FadeIn(shm_label), Write(eq1), run_time=0.9)
        self.wait(0.4)
        self.play(TransformMatchingTex(eq1, eq2), run_time=1.0)
        self.wait(0.4)

        # 2. Term cancellation with \cancel{} inside \begin{aligned} (bottom-left)
        cancel_label = Text(
            "2. Term Cancellation (\\cancel + aligned)",
            font_size=20,
            color=PALETTE["ORANGE"],
        )
        cancel_eq = MathTex(
            r"\begin{aligned}"
            r"\cancel{m}\, a &= -\cancel{m}\, g \sin\theta \\"
            r"a &= -g \sin\theta"
            r"\end{aligned}",
            tex_template=CHEM_TEX_TEMPLATE,
            font_size=38,
            color=PALETTE["TEXT"],
        )
        left_group = VGroup(cancel_label, cancel_eq).arrange(DOWN, buff=0.3, aligned_edge=LEFT)
        left_box = SurroundingRectangle(
            left_group,
            buff=0.32,
            corner_radius=0.14,
            color=PALETTE["ORANGE"],
            stroke_width=1.6,
            fill_color="#161b22",
            fill_opacity=0.85,
        )
        left_card = VGroup(left_box, left_group)

        # 3. Native Chemistry reaction with mhchem \ce{} (bottom-right)
        chem_label = Text(
            "3. SN2 Reaction (mhchem \\ce{})",
            font_size=20,
            color=PALETTE["GREEN"],
        )
        chem_eq = MathTex(
            r"\ce{CH3Br + OH- -> CH3OH + Br-}",
            tex_template=CHEM_TEX_TEMPLATE,
            font_size=38,
            color=PALETTE["GREEN"],
        )
        chem_note = MathTex(
            r"\text{Rate} = k\,[\ce{CH3Br}][\ce{OH-}]",
            tex_template=CHEM_TEX_TEMPLATE,
            font_size=32,
            color=PALETTE["BLUE"],
        )
        right_group = VGroup(chem_label, chem_eq, chem_note).arrange(
            DOWN, buff=0.28, aligned_edge=LEFT
        )
        right_box = SurroundingRectangle(
            right_group,
            buff=0.32,
            corner_radius=0.14,
            color=PALETTE["GREEN"],
            stroke_width=1.6,
            fill_color="#161b22",
            fill_opacity=0.85,
        )
        right_card = VGroup(right_box, right_group)

        # Match card heights and position cleanly side-by-side in lower half
        bottom_row = VGroup(left_card, right_card).arrange(RIGHT, buff=0.55)
        if bottom_row.width > config.frame_width - 1.0:
            bottom_row.scale_to_fit_width(config.frame_width - 1.0)
        bottom_row.to_edge(DOWN, buff=0.55)

        self.play(FadeIn(left_box), FadeIn(cancel_label), Write(cancel_eq), run_time=1.0)
        self.play(
            FadeIn(right_box),
            FadeIn(chem_label),
            Write(chem_eq),
            Write(chem_note),
            run_time=1.1,
        )
        self.wait(0.8)
