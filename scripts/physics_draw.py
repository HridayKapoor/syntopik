#!/usr/bin/env python3
"""Precision JEE Physics Diagram Engine (Matplotlib + NumPy).

Generates publication-grade, mathematically exact vector SVG and high-DPI PNG
diagrams across 7 parametric JEE Physics presets with zero hand-eyeballed
geometry, automatic light/dark theming, pixel-level label collision avoidance,
and deterministic JSON physics readouts:

  1. fbd-incline    — Free-body diagram on an inclined plane with exact vector proportions
  2. pulley         — Atwood, table-pulley, and incline-pulley with exact tangent ropes
  3. optics-lens    — Thin converging/diverging lens (NCERT New Cartesian, 3 principal rays)
  4. optics-mirror  — Concave/convex spherical mirror (NCERT New Cartesian, 3 principal rays)
  5. optics-prism   — Exact Snell's law refraction, minimum deviation, and TIR ray tracing
  6. field-charges  — RK4 electric field lines, equipotential contours, dipole & null points
  7. projectile     — Level, elevated, up-incline, and down-incline projectile kinematics
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import Arc, Circle, FancyArrowPatch, Polygon, Rectangle


# ---------------------------------------------------------------------------
# Themes & Typography
# ---------------------------------------------------------------------------

THEMES: dict[str, dict[str, str]] = {
    "light": {
        "bg": "#FFFFFF",
        "panel_bg": "#F8FAFC",
        "panel_edge": "#CBD5E1",
        "ink": "#0F172A",
        "muted": "#475569",
        "grid": "#E2E8F0",
        "surface": "#E2E8F0",
        "surface_edge": "#334155",
        "block_fill": "#DBEAFE",
        "block_edge": "#1E3A8A",
        "block2_fill": "#FEF3C7",
        "block2_edge": "#92400E",
        "pulley_fill": "#E2E8F0",
        "rope": "#1E293B",
        "glass_fill": "#E0F2FE",
        "glass_edge": "#0284C7",
        "primary": "#2563EB",
        "danger": "#DC2626",
        "accent": "#D97706",
        "success": "#059669",
        "purple": "#7C3AED",
        "teal": "#0891B2",
        "label_bg": "#FFFFFF",
    },
    "dark": {
        "bg": "#0B1120",
        "panel_bg": "#111827",
        "panel_edge": "#334155",
        "ink": "#F1F5F9",
        "muted": "#94A3B8",
        "grid": "#1E293B",
        "surface": "#1E293B",
        "surface_edge": "#94A3B8",
        "block_fill": "#1E3A5F",
        "block_edge": "#60A5FA",
        "block2_fill": "#451A03",
        "block2_edge": "#FBBF24",
        "pulley_fill": "#334155",
        "rope": "#E2E8F0",
        "glass_fill": "#0C4A6E",
        "glass_edge": "#38BDF8",
        "primary": "#60A5FA",
        "danger": "#F87171",
        "accent": "#FBBF24",
        "success": "#34D399",
        "purple": "#C084FC",
        "teal": "#22D3EE",
        "label_bg": "#0B1120",
    },
}


def configure_rc(theme: dict[str, str]) -> None:
    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "mathtext.fontset": "stix",
            "axes.edgecolor": theme["muted"],
            "axes.labelcolor": theme["ink"],
            "xtick.color": theme["muted"],
            "ytick.color": theme["muted"],
            "text.color": theme["ink"],
            "svg.fonttype": "path",
        }
    )


def _round_opt(val: float | None, ndigits: int = 6) -> float | None:
    if val is None or not math.isfinite(val):
        return None
    if abs(val) < 1e-12:
        return 0.0
    return round(float(val), ndigits)


def _fmt(val: float | None, digits: int = 2, suffix: str = "") -> str:
    if val is None:
        return r"$\infty$"
    if abs(val) < 1e-9:
        val = 0.0
    return f"{val:.{digits}f}{suffix}"


# ---------------------------------------------------------------------------
# Drawing Primitives & Pixel-Level Label De-Overlapping
# ---------------------------------------------------------------------------


def make_canvas(
    theme_name: str,
    figsize: tuple[float, float] = (9.2, 5.8),
    title: str = "",
    subtitle: str = "",
    ax_rect: tuple[float, float, float, float] = (0.06, 0.14, 0.88, 0.74),
) -> tuple[plt.Figure, plt.Axes, dict[str, str]]:
    theme = THEMES[theme_name]
    configure_rc(theme)
    fig = plt.figure(figsize=figsize, facecolor=theme["bg"])
    ax = fig.add_axes(list(ax_rect))
    ax.set_facecolor(theme["bg"])
    if title:
        fig.text(
            0.5,
            0.945 if subtitle else 0.935,
            title,
            ha="center",
            va="center",
            fontsize=13.0,
            fontweight="bold",
            color=theme["ink"],
        )
    if subtitle:
        fig.text(
            0.5,
            0.903,
            subtitle,
            ha="center",
            va="center",
            fontsize=9.2,
            color=theme["muted"],
        )
    return fig, ax, theme


def add_readout_bar(fig: plt.Figure, text: str, theme: dict[str, str], y: float = 0.048) -> None:
    fig.text(
        0.5,
        y,
        text,
        ha="center",
        va="center",
        fontsize=9.0,
        color=theme["ink"],
        bbox=dict(
            boxstyle="round,pad=0.45",
            facecolor=theme["panel_bg"],
            edgecolor=theme["panel_edge"],
            linewidth=1.0,
        ),
    )


def draw_vector(
    ax: plt.Axes,
    start: tuple[float, float],
    end: tuple[float, float],
    color: str,
    lw: float = 2.2,
    ls: str = "-",
    head_len: float = 8.5,
    head_w: float = 4.6,
    zorder: int = 8,
    alpha: float = 1.0,
) -> FancyArrowPatch | None:
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    if math.hypot(dx, dy) < 1e-9:
        return None
    arrow = FancyArrowPatch(
        start,
        end,
        arrowstyle=f"-|>,head_length={head_len},head_width={head_w}",
        mutation_scale=1.0,
        linewidth=lw,
        linestyle=ls,
        color=color,
        alpha=alpha,
        shrinkA=0.0,
        shrinkB=0.0,
        zorder=zorder,
    )
    ax.add_patch(arrow)
    return arrow


def draw_mid_arrow(
    ax: plt.Axes,
    p0: tuple[float, float],
    p1: tuple[float, float],
    color: str,
    frac: float = 0.52,
    lw: float = 1.6,
    head_len: float = 7.5,
    head_w: float = 4.2,
    zorder: int = 8,
) -> None:
    dx = p1[0] - p0[0]
    dy = p1[1] - p0[1]
    dist = math.hypot(dx, dy)
    if dist < 1e-6:
        return
    ux, uy = dx / dist, dy / dist
    step = min(dist * 0.08, 0.25)
    cx = p0[0] + frac * dx
    cy = p0[1] + frac * dy
    a0 = (cx - 0.5 * step * ux, cy - 0.5 * step * uy)
    a1 = (cx + 0.5 * step * ux, cy + 0.5 * step * uy)
    draw_vector(ax, a0, a1, color=color, lw=lw, head_len=head_len, head_w=head_w, zorder=zorder)


def draw_hatched_line(
    ax: plt.Axes,
    p0: tuple[float, float],
    p1: tuple[float, float],
    theme: dict[str, str],
    side: int = -1,
    spacing: float = 0.32,
    tick_len: float = 0.22,
    lw: float = 1.8,
    zorder: int = 3,
) -> None:
    """Draw a rigid ground/wall line with crisp angled hatching ticks on one side."""
    x0, y0 = p0
    x1, y1 = p1
    ax.plot([x0, x1], [y0, y1], color=theme["surface_edge"], lw=lw, zorder=zorder)
    dx, dy = x1 - x0, y1 - y0
    length = math.hypot(dx, dy)
    if length < 1e-6:
        return
    tx, ty = dx / length, dy / length
    nx, ny = -ty * side, tx * side
    # Hatch angle: tilted 45 deg opposite tangent into the solid side
    hx = (nx - 0.85 * tx)
    hy = (ny - 0.85 * ty)
    hnorm = math.hypot(hx, hy)
    hx, hy = hx / hnorm * tick_len, hy / hnorm * tick_len
    n_ticks = max(2, int(length / spacing))
    for i in range(n_ticks + 1):
        s = (i / n_ticks) * length
        bx = x0 + tx * s
        by = y0 + ty * s
        ax.plot([bx, bx + hx], [by, by + hy], color=theme["muted"], lw=0.85, alpha=0.85, zorder=zorder - 1)


def draw_angle_arc(
    ax: plt.Axes,
    center: tuple[float, float],
    radius: float,
    theta1_deg: float,
    theta2_deg: float,
    color: str,
    lw: float = 1.3,
    zorder: int = 6,
) -> tuple[float, float]:
    """Draw an exact circular arc from theta1_deg to theta2_deg (CCW) and return midpoint direction."""
    t1 = theta1_deg % 360.0
    t2 = theta2_deg % 360.0
    span = (t2 - t1) % 360.0
    if span > 180.0:
        t1, t2 = t2, t1
        span = 360.0 - span
    arc = Arc(
        center,
        width=2.0 * radius,
        height=2.0 * radius,
        angle=0.0,
        theta1=t1,
        theta2=t1 + span,
        color=color,
        lw=lw,
        zorder=zorder,
    )
    ax.add_patch(arc)
    mid = math.radians(t1 + 0.5 * span)
    return (math.cos(mid), math.sin(mid))


class LabelManager:
    """Places crisp pill-backed labels and eliminates pixel-level overlaps before saving."""

    def __init__(self, ax: plt.Axes, theme: dict[str, str]) -> None:
        self.ax = ax
        self.theme = theme
        self.items: list[dict[str, Any]] = []

    def add(
        self,
        x: float,
        y: float,
        text: str,
        color: str | None = None,
        fontsize: float = 9.5,
        ha: str = "center",
        va: str = "center",
        weight: str = "bold",
        pill: bool = True,
        movable: bool = True,
        zorder: int = 12,
        anchor: tuple[float, float] | None = None,
    ) -> plt.Text:
        bbox = (
            dict(
                boxstyle="round,pad=0.18",
                facecolor=self.theme["label_bg"],
                edgecolor="none",
                alpha=0.86,
            )
            if pill
            else None
        )
        txt = self.ax.text(
            x,
            y,
            text,
            color=color or self.theme["ink"],
            fontsize=fontsize,
            ha=ha,
            va=va,
            fontweight=weight,
            bbox=bbox,
            zorder=zorder,
        )
        self.items.append({"artist": txt, "movable": movable, "anchor": anchor})
        return txt

    def resolve(self, fig: plt.Figure, pad_px: float = 4.5, max_iter: int = 45) -> None:
        if len(self.items) < 2:
            return
        fig.canvas.draw()
        renderer = fig.canvas.get_renderer()
        ax_bbox = self.ax.get_window_extent(renderer)
        inv = self.ax.transData.inverted()

        for _ in range(max_iter):
            moved = False
            bboxes = [item["artist"].get_window_extent(renderer) for item in self.items]
            for i in range(len(self.items)):
                for j in range(i + 1, len(self.items)):
                    if not (self.items[i]["movable"] or self.items[j]["movable"]):
                        continue
                    bi, bj = bboxes[i], bboxes[j]
                    ox = min(bi.x1, bj.x1) - max(bi.x0, bj.x0) + pad_px
                    oy = min(bi.y1, bj.y1) - max(bi.y0, bj.y0) + pad_px
                    if ox > 0 and oy > 0:
                        moved = True
                        # Push along axis of smaller penetration
                        cix, ciy = 0.5 * (bi.x0 + bi.x1), 0.5 * (bi.y0 + bi.y1)
                        cjx, cjy = 0.5 * (bj.x0 + bj.x1), 0.5 * (bj.y0 + bj.y1)
                        if oy <= ox:
                            sgn = -1.0 if ciy <= cjy else 1.0
                            shift = 0.55 * oy
                            dx_px, dy_px = 0.0, sgn * shift
                        else:
                            sgn = -1.0 if cix <= cjx else 1.0
                            shift = 0.55 * ox
                            dx_px, dy_px = sgn * shift, 0.0

                        mi, mj = self.items[i]["movable"], self.items[j]["movable"]
                        for idx, factor in ((i, 1.0 if mj else 1.8), (j, -1.0 if mi else -1.8)):
                            if not self.items[idx]["movable"]:
                                continue
                            art = self.items[idx]["artist"]
                            px, py = self.ax.transData.transform(art.get_position())
                            nx_px = px + dx_px * factor
                            ny_px = py + dy_px * factor
                            # Keep comfortably inside axes bounds
                            margin_x = 0.5 * bboxes[idx].width + 4.0
                            margin_y = 0.5 * bboxes[idx].height + 4.0
                            nx_px = min(max(nx_px, ax_bbox.x0 + margin_x), ax_bbox.x1 - margin_x)
                            ny_px = min(max(ny_px, ax_bbox.y0 + margin_y), ax_bbox.y1 - margin_y)
                            new_data = inv.transform((nx_px, ny_px))
                            art.set_position((float(new_data[0]), float(new_data[1])))
            if not moved:
                break
            fig.canvas.draw()


def save_figure(fig: plt.Figure, out_path: Path, dpi: int) -> dict[str, Any]:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    ext = out_path.suffix.lower().lstrip(".")
    if ext not in ("svg", "png", "pdf"):
        raise SystemExit(f"Unsupported output extension '.{ext}'. Use .svg or .png.")
    fig.savefig(str(out_path), format=ext, dpi=dpi, facecolor=fig.get_facecolor())
    plt.close(fig)
    return {
        "output": str(out_path),
        "format": ext,
        "bytes": out_path.stat().st_size,
    }


# ---------------------------------------------------------------------------
# 1. FBD on an Inclined Plane
# ---------------------------------------------------------------------------


def compute_fbd_incline(
    angle_deg: float,
    mass: float,
    g: float,
    mu: float,
    applied_force: float,
    applied_angle_deg: float,
    mode: str,
) -> dict[str, Any]:
    if not (0.0 < angle_deg <= 85.0):
        raise SystemExit("--angle must be in (0, 85] degrees.")
    if mass <= 0.0 or g <= 0.0 or mu < 0.0:
        raise SystemExit("--mass and --g must be > 0; --mu must be >= 0.")

    theta = math.radians(angle_deg)
    phi = math.radians(applied_angle_deg)
    weight = mass * g
    mg_sin = weight * math.sin(theta)
    mg_cos = weight * math.cos(theta)
    f_app_par = applied_force * math.cos(phi)
    f_app_perp = applied_force * math.sin(phi)

    raw_normal = mg_cos - f_app_perp
    liftoff = raw_normal < -1e-9
    normal = max(0.0, raw_normal)
    f_max = mu * normal
    f_drive = f_app_par - mg_sin  # + up incline, - down incline

    static_possible = abs(f_drive) <= f_max + 1e-12
    if liftoff:
        f_signed = 0.0
        f_net_signed = f_drive
        regime = "liftoff"
    elif mode == "auto":
        if static_possible:
            f_signed = -f_drive
            f_net_signed = 0.0
            regime = "static"
        elif f_drive < -f_max:
            f_signed = +f_max
            f_net_signed = f_drive + f_max
            regime = "sliding_down"
        else:
            f_signed = -f_max
            f_net_signed = f_drive - f_max
            regime = "sliding_up"
    elif mode == "static":
        if static_possible:
            f_signed = -f_drive
            f_net_signed = 0.0
            regime = "static"
        else:
            f_signed = +f_max if f_drive < 0 else -f_max
            f_net_signed = f_drive + f_signed
            regime = "sliding_down" if f_net_signed < 0 else "sliding_up"
    elif mode == "sliding-down":
        f_signed = +f_max
        f_net_signed = f_drive + f_max
        regime = "sliding_down"
    elif mode == "sliding-up":
        f_signed = -f_max
        f_net_signed = f_drive - f_max
        regime = "sliding_up"
    else:
        raise SystemExit(f"Unknown mode: {mode}")

    a_signed = f_net_signed / mass
    if abs(f_signed) < 1e-12:
        fric_dir = "none"
    elif f_signed > 0:
        fric_dir = "up_incline"
    else:
        fric_dir = "down_incline"

    if abs(a_signed) < 1e-12:
        accel_dir = "none"
    elif a_signed > 0:
        accel_dir = "up_incline"
    else:
        accel_dir = "down_incline"

    return {
        "preset": "fbd-incline",
        "angleDeg": _round_opt(angle_deg),
        "massKg": _round_opt(mass),
        "g": _round_opt(g),
        "mu": _round_opt(mu),
        "appliedForceN": _round_opt(applied_force),
        "appliedAngleDeg": _round_opt(applied_angle_deg),
        "modeRequested": mode,
        "regime": regime,
        "staticPossible": static_possible,
        "liftoff": liftoff,
        "weightN": _round_opt(weight),
        "mgSinThetaN": _round_opt(mg_sin),
        "mgCosThetaN": _round_opt(mg_cos),
        "normalN": _round_opt(normal),
        "frictionMaxN": _round_opt(f_max),
        "frictionN": _round_opt(abs(f_signed)),
        "frictionSignedN": _round_opt(f_signed),
        "frictionDirection": fric_dir,
        "netForceN": _round_opt(abs(f_net_signed)),
        "netForceSignedN": _round_opt(f_net_signed),
        "acceleration": _round_opt(abs(a_signed)),
        "accelerationSigned": _round_opt(a_signed),
        "accelerationDirection": accel_dir,
    }


def render_fbd_incline(args: argparse.Namespace) -> dict[str, Any]:
    data = compute_fbd_incline(
        angle_deg=args.angle,
        mass=args.mass,
        g=args.g,
        mu=args.mu,
        applied_force=args.applied_force,
        applied_angle_deg=args.applied_angle,
        mode=args.mode,
    )
    theta = math.radians(args.angle)
    ct, st = math.cos(theta), math.sin(theta)
    t_hat = np.array([ct, st])      # up incline
    n_hat = np.array([-st, ct])     # outward normal

    regime_label = data["regime"].replace("_", " ").title()
    fig, ax, theme = make_canvas(
        args.theme,
        figsize=(9.4, 6.2),
        title=args.title or f"Free-Body Diagram on Inclined Plane ($\\theta = {args.angle:g}^\\circ$)",
        subtitle=f"$m = {args.mass:g}\\,\\mathrm{{kg}},\\; \\mu = {args.mu:g},\\; g = {args.g:g}\\,\\mathrm{{m/s^2}}$  |  Regime: {regime_label}",
        ax_rect=(0.05, 0.14, 0.90, 0.73),
    )
    labels = LabelManager(ax, theme)

    # Wedge geometry (slope rises left->right from origin)
    slope_len = 7.4
    base_len = slope_len * ct
    height = slope_len * st
    p_orig = np.array([0.0, 0.0])
    p_base = np.array([base_len, 0.0])
    p_apex = np.array([base_len, height])

    wedge = Polygon(
        [p_orig, p_base, p_apex],
        closed=True,
        facecolor=theme["surface"],
        edgecolor=theme["surface_edge"],
        linewidth=1.8,
        zorder=2,
    )
    ax.add_patch(wedge)
    draw_hatched_line(ax, (-1.1, 0.0), (base_len + 1.1, 0.0), theme, side=-1, spacing=0.35, tick_len=0.22)

    # Right-angle marker at bottom-right vertex of wedge
    sq = min(0.32, 0.18 * height, 0.18 * base_len)
    ax.plot(
        [base_len - sq, base_len - sq, base_len],
        [0.0, sq, sq],
        color=theme["muted"],
        lw=1.0,
        zorder=3,
    )

    # Wedge angle arc theta at origin
    arc_r = min(1.35, 0.34 * slope_len)
    mid_dir = draw_angle_arc(ax, (0.0, 0.0), arc_r, 0.0, args.angle, theme["accent"], lw=1.6)
    labels.add(
        (arc_r + 0.38) * mid_dir[0],
        max(0.18, (arc_r + 0.34) * mid_dir[1]),
        rf"$\theta = {args.angle:g}^\circ$",
        color=theme["accent"],
        fontsize=9.5,
        movable=True,
    )

    # Block sitting cleanly at midpoint along the incline
    bw, bh = 1.25, 0.78
    s_mid = 0.52 * slope_len
    m_contact = s_mid * t_hat
    com = m_contact + 0.5 * bh * n_hat
    corners = [
        m_contact - 0.5 * bw * t_hat,
        m_contact + 0.5 * bw * t_hat,
        m_contact + 0.5 * bw * t_hat + bh * n_hat,
        m_contact - 0.5 * bw * t_hat + bh * n_hat,
    ]
    block_patch = Polygon(
        corners,
        closed=True,
        facecolor=theme["block_fill"],
        edgecolor=theme["block_edge"],
        linewidth=2.0,
        zorder=5,
    )
    ax.add_patch(block_patch)
    ax.plot(com[0], com[1], "o", color=theme["ink"], ms=3.5, zorder=9)
    m_lbl_pos = com - 0.30 * bw * t_hat + 0.22 * bh * n_hat
    labels.add(
        float(m_lbl_pos[0]),
        float(m_lbl_pos[1]),
        r"$m$",
        color=theme["block_edge"],
        fontsize=10.5,
        pill=False,
        movable=False,
        zorder=7,
    )

    # Exact proportional scaling for all force vectors
    max_force = max(
        data["weightN"],
        data["normalN"],
        abs(data["frictionSignedN"]),
        abs(args.applied_force),
        1e-6,
    )
    vec_scale = 2.55 / max_force

    pts_for_bounds: list[np.ndarray] = [
        np.array([-1.2, -0.45]),
        np.array([base_len + 1.2, height + 0.8]),
        com,
    ]

    # Weight mg (straight down)
    mg_len = data["weightN"] * vec_scale
    mg_tip = com + np.array([0.0, -mg_len])
    draw_vector(ax, tuple(com), tuple(mg_tip), color=theme["danger"], lw=2.4, zorder=9)
    pts_for_bounds.append(mg_tip)
    # Place mg label slightly to the right of tip (away from mg sin theta which goes down-left)
    labels.add(
        mg_tip[0] + 0.55,
        mg_tip[1] - 0.08,
        rf"$mg = {data['weightN']:.2f}\,\mathrm{{N}}$",
        color=theme["danger"],
        fontsize=9.3,
    )

    # Dashed exact components mg sin(theta) and mg cos(theta)
    if args.show_components:
        mgsin_tip = com - (data["mgSinThetaN"] * vec_scale) * t_hat
        mgcos_tip = com - (data["mgCosThetaN"] * vec_scale) * n_hat
        draw_vector(ax, tuple(com), tuple(mgsin_tip), color=theme["danger"], lw=1.7, ls="--", head_len=7.0, head_w=3.8, alpha=0.85, zorder=7)
        draw_vector(ax, tuple(com), tuple(mgcos_tip), color=theme["danger"], lw=1.7, ls="--", head_len=7.0, head_w=3.8, alpha=0.85, zorder=7)
        # Exact rectangle projection closing lines
        ax.plot([mgsin_tip[0], mg_tip[0]], [mgsin_tip[1], mg_tip[1]], color=theme["muted"], lw=0.95, ls=":", alpha=0.8, zorder=4)
        ax.plot([mgcos_tip[0], mg_tip[0]], [mgcos_tip[1], mg_tip[1]], color=theme["muted"], lw=0.95, ls=":", alpha=0.8, zorder=4)
        pts_for_bounds.extend([mgsin_tip, mgcos_tip])

        # Angle theta between mg (-90 deg) and -n_hat (-90 + angle deg)
        comp_arc_r = min(0.72, 0.35 * data["mgCosThetaN"] * vec_scale)
        if comp_arc_r > 0.2:
            c_dir = draw_angle_arc(ax, tuple(com), comp_arc_r, -90.0, -90.0 + args.angle, theme["muted"], lw=1.1, zorder=6)
            labels.add(
                com[0] + (comp_arc_r + 0.24) * c_dir[0],
                com[1] + (comp_arc_r + 0.24) * c_dir[1],
                r"$\theta$",
                color=theme["muted"],
                fontsize=8.6,
            )

        labels.add(
            mgsin_tip[0] - 0.55 * ct - 0.22 * st,
            mgsin_tip[1] - 0.55 * st + 0.22 * ct,
            rf"$mg\sin\theta = {data['mgSinThetaN']:.2f}\,\mathrm{{N}}$",
            color=theme["danger"],
            fontsize=8.6,
        )
        labels.add(
            mgcos_tip[0] + 0.65 * ct + 0.25 * st,
            mgcos_tip[1] + 0.30 * st - 0.28 * ct,
            rf"$mg\cos\theta = {data['mgCosThetaN']:.2f}\,\mathrm{{N}}$",
            color=theme["danger"],
            fontsize=8.6,
        )

    # Normal force N along +n_hat
    if data["normalN"] > 1e-6:
        n_tip = com + (data["normalN"] * vec_scale) * n_hat
        draw_vector(ax, tuple(com), tuple(n_tip), color=theme["primary"], lw=2.4, zorder=9)
        pts_for_bounds.append(n_tip)
        labels.add(
            n_tip[0] - 0.35 * st - 0.15 * ct,
            n_tip[1] + 0.35 * ct,
            rf"$N = {data['normalN']:.2f}\,\mathrm{{N}}$",
            color=theme["primary"],
            fontsize=9.5,
        )

    # Friction force f along the contact interface (so it never overlaps mg sin(theta) or F_app)
    if abs(data["frictionSignedN"]) > 1e-6:
        f_dir_vec = t_hat if data["frictionSignedN"] > 0 else -t_hat
        f_len = max(abs(data["frictionSignedN"]) * vec_scale, 0.55)
        f_start = m_contact + 0.05 * n_hat
        f_tip = f_start + f_len * f_dir_vec
        draw_vector(ax, tuple(f_start), tuple(f_tip), color=theme["accent"], lw=2.3, zorder=10)
        pts_for_bounds.append(f_tip)
        lbl_f = rf"$f_s = {data['frictionN']:.2f}\,\mathrm{{N}}$" if data["regime"] == "static" else rf"$f_k = {data['frictionN']:.2f}\,\mathrm{{N}}$"
        off_sign = 1.0 if data["frictionSignedN"] > 0 else -1.0
        labels.add(
            f_tip[0] + 0.42 * f_dir_vec[0] + 0.32 * n_hat[0] * off_sign,
            f_tip[1] + 0.42 * f_dir_vec[1] + 0.32 * n_hat[1] * off_sign,
            lbl_f,
            color=theme["accent"],
            fontsize=9.2,
        )

    # Applied force F_app (if non-zero)
    if abs(args.applied_force) > 1e-6:
        phi = math.radians(args.applied_angle)
        app_dir = (math.cos(phi) * t_hat + math.sin(phi) * n_hat) * (1.0 if args.applied_force > 0 else -1.0)
        app_start = com + 0.45 * bw * (t_hat if args.applied_force > 0 else -t_hat)
        app_len = max(abs(args.applied_force) * vec_scale, 0.65)
        app_tip = app_start + app_len * app_dir
        draw_vector(ax, tuple(app_start), tuple(app_tip), color=theme["success"], lw=2.4, zorder=10)
        pts_for_bounds.append(app_tip)
        labels.add(
            app_tip[0] + 0.45 * app_dir[0],
            app_tip[1] + 0.35 * app_dir[1] + 0.25,
            rf"$F_{{\mathrm{{app}}}} = {abs(args.applied_force):.2f}\,\mathrm{{N}}$",
            color=theme["success"],
            fontsize=9.2,
        )

    # Acceleration indicator arrow parallel to incline above the block
    if data["acceleration"] > 1e-6:
        a_dir = t_hat if data["accelerationSigned"] > 0 else -t_hat
        a_center = com + (0.5 * bh + 0.72) * n_hat + 0.95 * a_dir
        a_p0 = a_center - 0.55 * a_dir
        a_p1 = a_center + 0.55 * a_dir
        draw_vector(ax, tuple(a_p0), tuple(a_p1), color=theme["purple"], lw=2.4, head_len=9.0, head_w=5.0, zorder=10)
        pts_for_bounds.extend([a_p0, a_p1])
        labels.add(
            a_center[0] + 0.36 * n_hat[0],
            a_center[1] + 0.36 * n_hat[1],
            rf"$\vec{{a}} = {data['acceleration']:.2f}\,\mathrm{{m/s^2}}$",
            color=theme["purple"],
            fontsize=9.3,
        )

    # Coordinate frame inset in upper-left corner
    arr_pts = np.vstack(pts_for_bounds)
    xmin, ymin = arr_pts.min(axis=0)
    xmax, ymax = arr_pts.max(axis=0)
    pad_x = max(1.1, 0.16 * (xmax - xmin))
    pad_y = max(0.95, 0.16 * (ymax - ymin))
    ax.set_xlim(xmin - pad_x, xmax + pad_x)
    ax.set_ylim(ymin - pad_y, ymax + pad_y)
    ax.set_aspect("equal", adjustable="box")
    ax.axis("off")

    # Small rotated (x, y) triad in top-left open space
    tri_o = np.array([xmin - 0.35 * pad_x, ymax + 0.12 * pad_y])
    tri_l = 0.65
    draw_vector(ax, tuple(tri_o), tuple(tri_o + tri_l * t_hat), color=theme["muted"], lw=1.3, head_len=6.0, head_w=3.2)
    draw_vector(ax, tuple(tri_o), tuple(tri_o + tri_l * n_hat), color=theme["muted"], lw=1.3, head_len=6.0, head_w=3.2)
    labels.add(tri_o[0] + (tri_l + 0.22) * t_hat[0], tri_o[1] + (tri_l + 0.22) * t_hat[1], r"$+x$", color=theme["muted"], fontsize=8.4, pill=False)
    labels.add(tri_o[0] + (tri_l + 0.22) * n_hat[0], tri_o[1] + (tri_l + 0.22) * n_hat[1], r"$+y$", color=theme["muted"], fontsize=8.4, pill=False)

    summary = (
        f"N = {data['normalN']:.2f} N   |   f = {data['frictionN']:.2f} N ({data['frictionDirection'].replace('_', ' ')})   |   "
        f"F_net = {data['netForceN']:.2f} N   |   a = {data['acceleration']:.2f} m/s²   |   Regime: {regime_label}"
    )
    add_readout_bar(fig, summary, theme)
    labels.resolve(fig)

    file_info = save_figure(fig, Path(args.out).expanduser().resolve(), args.dpi)
    data.update(file_info)
    data["theme"] = args.theme
    return data


# ---------------------------------------------------------------------------
# 2. Pulley Systems (Atwood, Table-Pulley, Incline-Pulley)
# ---------------------------------------------------------------------------


def compute_pulley(preset: str, m1: float, m2: float, angle_deg: float, mu: float, g: float) -> dict[str, Any]:
    if m1 <= 0.0 or m2 <= 0.0 or g <= 0.0 or mu < 0.0:
        raise SystemExit("--m1, --m2, --g must be > 0 and --mu must be >= 0.")
    if preset == "incline-pulley" and not (0.0 < angle_deg <= 85.0):
        raise SystemExit("--angle must be in (0, 85] degrees for incline-pulley.")

    res: dict[str, Any] = {
        "preset": "pulley",
        "pulleyPreset": preset,
        "m1Kg": _round_opt(m1),
        "m2Kg": _round_opt(m2),
        "g": _round_opt(g),
        "mu": _round_opt(mu),
    }

    if preset == "atwood":
        a_signed = g * (m2 - m1) / (m1 + m2)  # + => m2 down, m1 up
        tension = 2.0 * m1 * m2 * g / (m1 + m2)
        if abs(a_signed) < 1e-12:
            mdir = "equilibrium"
        elif a_signed > 0:
            mdir = "m2_down_m1_up"
        else:
            mdir = "m1_down_m2_up"
        res.update(
            {
                "tensionN": _round_opt(tension),
                "acceleration": _round_opt(abs(a_signed)),
                "accelerationSigned": _round_opt(a_signed),
                "motionDirection": mdir,
                "normal1N": 0.0,
                "frictionN": 0.0,
            }
        )
    elif preset == "table-pulley":
        n1 = m1 * g
        f_max = mu * n1
        f_drive = m2 * g
        if f_drive <= f_max + 1e-12:
            a_signed = 0.0
            friction = f_drive
            tension = m2 * g
            mdir = "static"
        else:
            a_signed = (m2 * g - f_max) / (m1 + m2)
            friction = f_max
            tension = m2 * (g - a_signed)
            mdir = "m2_down_m1_right"
        res.update(
            {
                "tensionN": _round_opt(tension),
                "acceleration": _round_opt(abs(a_signed)),
                "accelerationSigned": _round_opt(a_signed),
                "motionDirection": mdir,
                "normal1N": _round_opt(n1),
                "frictionN": _round_opt(friction),
                "frictionMaxN": _round_opt(f_max),
            }
        )
    elif preset == "incline-pulley":
        theta = math.radians(angle_deg)
        n1 = m1 * g * math.cos(theta)
        f_max = mu * n1
        f_drive = m2 * g - m1 * g * math.sin(theta)  # + => m2 down, m1 up incline
        if abs(f_drive) <= f_max + 1e-12:
            a_signed = 0.0
            friction = abs(f_drive)
            tension = m2 * g
            mdir = "static"
            f_dir = "down_incline" if f_drive > 1e-12 else ("up_incline" if f_drive < -1e-12 else "none")
        elif f_drive > f_max:
            a_signed = (f_drive - f_max) / (m1 + m2)
            friction = f_max
            tension = m2 * (g - a_signed)
            mdir = "m2_down_m1_up_incline"
            f_dir = "down_incline"
        else:
            a_signed = (f_drive + f_max) / (m1 + m2)  # negative
            friction = f_max
            tension = m2 * (g + abs(a_signed))
            mdir = "m1_down_incline_m2_up"
            f_dir = "up_incline"
        res.update(
            {
                "angleDeg": _round_opt(angle_deg),
                "tensionN": _round_opt(tension),
                "acceleration": _round_opt(abs(a_signed)),
                "accelerationSigned": _round_opt(a_signed),
                "motionDirection": mdir,
                "normal1N": _round_opt(n1),
                "frictionN": _round_opt(friction),
                "frictionMaxN": _round_opt(f_max),
                "frictionDirection": f_dir,
            }
        )
    else:
        raise SystemExit(f"Unknown pulley preset: {preset}")

    return res


def _draw_pulley_wheel(
    ax: plt.Axes,
    center: tuple[float, float],
    radius: float,
    theme: dict[str, str],
    rope_arc_deg: tuple[float, float],
) -> None:
    wheel = Circle(
        center,
        radius,
        facecolor=theme["pulley_fill"],
        edgecolor=theme["surface_edge"],
        linewidth=2.0,
        zorder=6,
    )
    ax.add_patch(wheel)
    inner = Circle(center, radius * 0.22, facecolor=theme["ink"], edgecolor="none", zorder=8)
    ax.add_patch(inner)
    # Exact circular rope groove arc
    t1, t2 = rope_arc_deg
    angles = np.linspace(math.radians(t1), math.radians(t2), 48)
    rx = center[0] + radius * np.cos(angles)
    ry = center[1] + radius * np.sin(angles)
    ax.plot(rx, ry, color=theme["rope"], lw=2.3, zorder=7)


def render_pulley(args: argparse.Namespace) -> dict[str, Any]:
    data = compute_pulley(args.preset, args.m1, args.m2, args.angle, args.mu, args.g)
    preset_titles = {
        "atwood": "Atwood Machine Pulley System",
        "table-pulley": "Horizontal Table–Pulley System",
        "incline-pulley": f"Inclined Plane–Pulley System ($\\theta = {args.angle:g}^\\circ$)",
    }
    sub_extra = f",\\; \\mu = {args.mu:g}" if args.preset != "atwood" else ""
    fig, ax, theme = make_canvas(
        args.theme,
        figsize=(9.2, 6.2),
        title=args.title or preset_titles[args.preset],
        subtitle=f"$m_1 = {args.m1:g}\\,\\mathrm{{kg}},\\; m_2 = {args.m2:g}\\,\\mathrm{{kg}},\\; g = {args.g:g}\\,\\mathrm{{m/s^2}}{sub_extra}$",
        ax_rect=(0.05, 0.14, 0.90, 0.73),
    )
    labels = LabelManager(ax, theme)

    rp = 0.55
    if args.preset == "atwood":
        cp = (0.0, 3.6)
        draw_hatched_line(ax, (-2.6, 4.6), (2.6, 4.6), theme, side=+1, spacing=0.32, tick_len=0.22)
        ax.plot([0.0, 0.0], [4.6, cp[1]], color=theme["surface_edge"], lw=2.4, zorder=5)
        _draw_pulley_wheel(ax, cp, rp, theme, (0.0, 180.0))

        # Hanging blocks at exact vertical tangent lines x = -rp and x = +rp
        bw, bh = 0.96, 0.86
        y1_top = 1.85
        y2_top = 1.25 if args.m2 >= args.m1 else 2.25
        ax.plot([-rp, -rp], [cp[1], y1_top], color=theme["rope"], lw=2.2, zorder=5)
        ax.plot([+rp, +rp], [cp[1], y2_top], color=theme["rope"], lw=2.2, zorder=5)

        b1 = Rectangle((-rp - bw / 2, y1_top - bh), bw, bh, facecolor=theme["block_fill"], edgecolor=theme["block_edge"], lw=2.0, zorder=6)
        b2 = Rectangle((+rp - bw / 2, y2_top - bh), bw, bh, facecolor=theme["block2_fill"], edgecolor=theme["block2_edge"], lw=2.0, zorder=6)
        ax.add_patch(b1)
        ax.add_patch(b2)
        labels.add(-rp, y1_top - bh / 2, r"$m_1$", color=theme["block_edge"], fontsize=10.5, pill=False, movable=False)
        labels.add(+rp, y2_top - bh / 2, r"$m_2$", color=theme["block2_edge"], fontsize=10.5, pill=False, movable=False)

        # Force arrows: T upward, m1 g and m2 g downward (proportional)
        fmax = max(args.m1 * args.g, args.m2 * args.g, data["tensionN"], 1e-6)
        sc = 1.35 / fmax
        t_len = data["tensionN"] * sc
        w1_len = args.m1 * args.g * sc
        w2_len = args.m2 * args.g * sc

        draw_vector(ax, (-rp, y1_top), (-rp, y1_top + t_len), color=theme["primary"], lw=2.3, zorder=9)
        labels.add(-rp - 0.55, y1_top + 0.55 * t_len, r"$T$", color=theme["primary"], fontsize=10.0)
        draw_vector(ax, (+rp, y2_top), (+rp, y2_top + t_len), color=theme["primary"], lw=2.3, zorder=9)
        labels.add(+rp + 0.55, y2_top + 0.55 * t_len, r"$T$", color=theme["primary"], fontsize=10.0)

        draw_vector(ax, (-rp, y1_top - bh), (-rp, y1_top - bh - w1_len), color=theme["danger"], lw=2.3, zorder=9)
        labels.add(-rp - 0.75, y1_top - bh - 0.65 * w1_len, rf"$m_1 g = {args.m1 * args.g:.1f}\,\mathrm{{N}}$", color=theme["danger"], fontsize=9.2)
        draw_vector(ax, (+rp, y2_top - bh), (+rp, y2_top - bh - w2_len), color=theme["danger"], lw=2.3, zorder=9)
        labels.add(+rp + 0.85, y2_top - bh - 0.65 * w2_len, rf"$m_2 g = {args.m2 * args.g:.1f}\,\mathrm{{N}}$", color=theme["danger"], fontsize=9.2)

        # Acceleration arrows
        if data["acceleration"] > 1e-6:
            sgn = 1.0 if data["accelerationSigned"] > 0 else -1.0  # + => m1 up, m2 down
            a1_y0 = y1_top - bh / 2 - 0.45 * sgn
            a1_y1 = y1_top - bh / 2 + 0.45 * sgn
            draw_vector(ax, (-rp - bw / 2 - 0.52, a1_y0), (-rp - bw / 2 - 0.52, a1_y1), color=theme["purple"], lw=2.4, zorder=10)
            labels.add(-rp - bw / 2 - 0.92, y1_top - bh / 2, r"$\vec{a}$", color=theme["purple"], fontsize=10.2)

            a2_y0 = y2_top - bh / 2 + 0.45 * sgn
            a2_y1 = y2_top - bh / 2 - 0.45 * sgn
            draw_vector(ax, (+rp + bw / 2 + 0.52, a2_y0), (+rp + bw / 2 + 0.52, a2_y1), color=theme["purple"], lw=2.4, zorder=10)
            labels.add(+rp + bw / 2 + 0.92, y2_top - bh / 2, r"$\vec{a}$", color=theme["purple"], fontsize=10.2)

        ymin = min(y1_top - bh - w1_len, y2_top - bh - w2_len) - 0.65
        ax.set_xlim(-3.8, 3.8)
        ax.set_ylim(ymin, 5.15)

    elif args.preset == "table-pulley":
        # Table top y = 0 from x = -4.6 to x = 0, vertical drop at x = 0
        table = Polygon(
            [(-4.6, -2.8), (0.0, -2.8), (0.0, 0.0), (-4.6, 0.0)],
            closed=True,
            facecolor=theme["surface"],
            edgecolor="none",
            zorder=1,
        )
        ax.add_patch(table)
        draw_hatched_line(ax, (-4.6, 0.0), (0.0, 0.0), theme, side=-1, spacing=0.35, tick_len=0.20)
        draw_hatched_line(ax, (0.0, 0.0), (0.0, -2.8), theme, side=-1, spacing=0.35, tick_len=0.20)

        bw, bh = 1.20, 0.78
        m1_cx = -2.4
        b1 = Rectangle((m1_cx - bw / 2, 0.0), bw, bh, facecolor=theme["block_fill"], edgecolor=theme["block_edge"], lw=2.0, zorder=6)
        ax.add_patch(b1)
        labels.add(m1_cx, bh / 2, r"$m_1$", color=theme["block_edge"], fontsize=10.5, pill=False, movable=False)

        # Pulley center chosen so top tangent (x_p, y_p + rp) is EXACTLY at height bh/2!
        rp = 0.48
        cp = (0.48, bh / 2 - rp)
        ax.plot([0.0, cp[0]], [0.0, cp[1]], color=theme["surface_edge"], lw=2.5, zorder=5)
        _draw_pulley_wheel(ax, cp, rp, theme, (0.0, 90.0))

        # Exact tangent ropes
        ax.plot([m1_cx + bw / 2, cp[0]], [bh / 2, bh / 2], color=theme["rope"], lw=2.2, zorder=5)
        m2_x = cp[0] + rp
        m2_top = -1.25
        bw2, bh2 = 0.90, 0.82
        ax.plot([m2_x, m2_x], [cp[1], m2_top], color=theme["rope"], lw=2.2, zorder=5)
        b2 = Rectangle((m2_x - bw2 / 2, m2_top - bh2), bw2, bh2, facecolor=theme["block2_fill"], edgecolor=theme["block2_edge"], lw=2.0, zorder=6)
        ax.add_patch(b2)
        labels.add(m2_x, m2_top - bh2 / 2, r"$m_2$", color=theme["block2_edge"], fontsize=10.5, pill=False, movable=False)

        # Force arrows (proportional)
        fmax = max(args.m1 * args.g, args.m2 * args.g, data["tensionN"], data["frictionN"], 1e-6)
        sc = 1.30 / fmax
        n1_len = data["normal1N"] * sc
        w1_len = args.m1 * args.g * sc
        w2_len = args.m2 * args.g * sc
        t_len = max(data["tensionN"] * sc, 0.55)

        draw_vector(ax, (m1_cx, bh), (m1_cx, bh + n1_len), color=theme["primary"], lw=2.2, zorder=9)
        labels.add(m1_cx - 0.52, bh + 0.65 * n1_len, rf"$N_1 = {data['normal1N']:.1f}\,\mathrm{{N}}$", color=theme["primary"], fontsize=9.0)

        draw_vector(ax, (m1_cx, 0.0), (m1_cx, -w1_len), color=theme["danger"], lw=2.2, zorder=9)
        labels.add(m1_cx - 0.65, -0.68 * w1_len, rf"$m_1 g = {args.m1 * args.g:.1f}\,\mathrm{{N}}$", color=theme["danger"], fontsize=9.0)

        draw_vector(ax, (m1_cx + bw / 2, bh / 2), (m1_cx + bw / 2 + t_len, bh / 2), color=theme["primary"], lw=2.2, zorder=9)
        labels.add(m1_cx + bw / 2 + 0.55 * t_len, bh / 2 + 0.36, r"$T$", color=theme["primary"], fontsize=9.8)

        if data["frictionN"] > 1e-6:
            f_len = max(data["frictionN"] * sc, 0.55)
            draw_vector(ax, (m1_cx - bw / 2, 0.08), (m1_cx - bw / 2 - f_len, 0.08), color=theme["accent"], lw=2.2, zorder=9)
            labels.add(m1_cx - bw / 2 - 0.55 * f_len, 0.42, rf"$f = {data['frictionN']:.1f}\,\mathrm{{N}}$", color=theme["accent"], fontsize=9.0)

        draw_vector(ax, (m2_x, m2_top), (m2_x, m2_top + t_len), color=theme["primary"], lw=2.2, zorder=9)
        labels.add(m2_x + 0.45, m2_top + 0.55 * t_len, r"$T$", color=theme["primary"], fontsize=9.8)

        draw_vector(ax, (m2_x, m2_top - bh2), (m2_x, m2_top - bh2 - w2_len), color=theme["danger"], lw=2.2, zorder=9)
        labels.add(m2_x + 0.78, m2_top - bh2 - 0.65 * w2_len, rf"$m_2 g = {args.m2 * args.g:.1f}\,\mathrm{{N}}$", color=theme["danger"], fontsize=9.0)

        if data["acceleration"] > 1e-6:
            draw_vector(ax, (m1_cx - 0.45, bh + n1_len + 0.35), (m1_cx + 0.55, bh + n1_len + 0.35), color=theme["purple"], lw=2.3, zorder=10)
            labels.add(m1_cx + 0.05, bh + n1_len + 0.68, rf"$\vec{{a}} = {data['acceleration']:.2f}\,\mathrm{{m/s^2}}$", color=theme["purple"], fontsize=9.2)
            draw_vector(ax, (m2_x + bw2 / 2 + 0.52, m2_top - 0.05), (m2_x + bw2 / 2 + 0.52, m2_top - bh2 + 0.05), color=theme["purple"], lw=2.3, zorder=10)
            labels.add(m2_x + bw2 / 2 + 0.90, m2_top - bh2 / 2, r"$\vec{a}$", color=theme["purple"], fontsize=10.0)

        ax.set_xlim(-5.1, m2_x + 2.3)
        ax.set_ylim(min(-3.2, m2_top - bh2 - w2_len - 0.55), bh + n1_len + 1.2)

    else:  # incline-pulley
        theta = math.radians(args.angle)
        ct, st = math.cos(theta), math.sin(theta)
        t_hat = np.array([ct, st])
        n_hat = np.array([-st, ct])

        slope_len = 6.2
        base_len = slope_len * ct
        height = slope_len * st
        p_orig = np.array([0.0, 0.0])
        p_base = np.array([base_len, 0.0])
        p_apex = np.array([base_len, height])

        wedge = Polygon([p_orig, p_base, p_apex], closed=True, facecolor=theme["surface"], edgecolor=theme["surface_edge"], lw=1.8, zorder=2)
        ax.add_patch(wedge)
        draw_hatched_line(ax, (-0.8, 0.0), (base_len + 2.4, 0.0), theme, side=-1, spacing=0.34, tick_len=0.20)

        arc_r = min(1.15, 0.32 * slope_len)
        mid_d = draw_angle_arc(ax, (0.0, 0.0), arc_r, 0.0, args.angle, theme["accent"], lw=1.5)
        labels.add((arc_r + 0.35) * mid_d[0], max(0.18, (arc_r + 0.30) * mid_d[1]), rf"$\theta = {args.angle:g}^\circ$", color=theme["accent"], fontsize=9.2)

        bw, bh = 1.05, 0.68
        m1_contact = (0.48 * slope_len) * t_hat
        m1_com = m1_contact + 0.5 * bh * n_hat
        corners = [
            m1_contact - 0.5 * bw * t_hat,
            m1_contact + 0.5 * bw * t_hat,
            m1_contact + 0.5 * bw * t_hat + bh * n_hat,
            m1_contact - 0.5 * bw * t_hat + bh * n_hat,
        ]
        ax.add_patch(Polygon(corners, closed=True, facecolor=theme["block_fill"], edgecolor=theme["block_edge"], lw=2.0, zorder=6))
        labels.add(m1_com[0], m1_com[1], r"$m_1$", color=theme["block_edge"], fontsize=10.2, pill=False, movable=False)

        # Pulley positioned so upper-incline tangent point lies EXACTLY on the line y_n = bh/2 above the incline!
        rp = 0.44
        # Line of rope: p_apex + (bh/2) * n_hat + s * t_hat.
        # Pulley center at distance (bh/2 - rp) along n_hat from apex, offset slightly along t_hat by d_off:
        d_off = 0.48
        cp_arr = p_apex + d_off * t_hat + (0.5 * bh - rp) * n_hat
        cp = (float(cp_arr[0]), float(cp_arr[1]))
        ax.plot([p_apex[0], cp[0]], [p_apex[1], cp[1]], color=theme["surface_edge"], lw=2.4, zorder=5)
        _draw_pulley_wheel(ax, cp, rp, theme, (0.0, 90.0 + args.angle))

        # Exact tangent point P1 on incline side & P2 on vertical side
        p1_tan = cp_arr + rp * n_hat
        rope_attach = m1_com + 0.5 * bw * t_hat
        ax.plot([rope_attach[0], p1_tan[0]], [rope_attach[1], p1_tan[1]], color=theme["rope"], lw=2.2, zorder=5)

        p2_tan = np.array([cp[0] + rp, cp[1]])
        m2_x = float(p2_tan[0])
        bw2, bh2 = 0.85, 0.78

        # Proportional forces (computed before m2_top so m2 g stays cleanly above ground)
        fmax = max(args.m1 * args.g, args.m2 * args.g, data["tensionN"], 1e-6)
        sc = 1.35 / fmax
        t_len = max(data["tensionN"] * sc, 0.55)
        n1_len = data["normal1N"] * sc
        w1_len = args.m1 * args.g * sc
        w2_len = args.m2 * args.g * sc

        m2_top = max(bh2 + w2_len + 0.25, cp[1] - 1.35)
        if m2_top > cp[1] - rp - 0.35:
            m2_top = cp[1] - rp - 0.35
        ax.plot([m2_x, m2_x], [cp[1], m2_top], color=theme["rope"], lw=2.2, zorder=5)
        ax.add_patch(Rectangle((m2_x - bw2 / 2, m2_top - bh2), bw2, bh2, facecolor=theme["block2_fill"], edgecolor=theme["block2_edge"], lw=2.0, zorder=6))
        labels.add(m2_x, m2_top - bh2 / 2, r"$m_2$", color=theme["block2_edge"], fontsize=10.2, pill=False, movable=False)

        # T on m1 along +t_hat
        draw_vector(ax, tuple(rope_attach), tuple(rope_attach + t_len * t_hat), color=theme["primary"], lw=2.2, zorder=9)
        t1_mid = rope_attach + 0.55 * t_len * t_hat + 0.30 * n_hat
        labels.add(t1_mid[0], t1_mid[1], r"$T$", color=theme["primary"], fontsize=9.6)

        # N1 starting from top face of m1 (keeps m1 center label unobstructed!)
        n1_start = m1_com + 0.5 * bh * n_hat
        n1_tip = n1_start + n1_len * n_hat
        draw_vector(ax, tuple(n1_start), tuple(n1_tip), color=theme["primary"], lw=2.2, zorder=9)
        labels.add(n1_tip[0] - 0.25 * st, n1_tip[1] + 0.28 * ct, r"$N_1$", color=theme["primary"], fontsize=9.6)

        # m1 g downward from bottom contact face of m1
        w1_tip = m1_contact + np.array([0.0, -w1_len])
        draw_vector(ax, tuple(m1_contact), tuple(w1_tip), color=theme["danger"], lw=2.2, zorder=9)
        labels.add(w1_tip[0] + 0.42, w1_tip[1] - 0.14, r"$m_1 g$", color=theme["danger"], fontsize=9.6)

        # Friction on m1
        if data["frictionN"] > 1e-6:
            f_dir_v = t_hat if data.get("frictionDirection") == "up_incline" else -t_hat
            f_len = max(data["frictionN"] * sc, 0.50)
            f_s = m1_contact + 0.04 * n_hat
            f_tip = f_s + f_len * f_dir_v
            draw_vector(ax, tuple(f_s), tuple(f_tip), color=theme["accent"], lw=2.2, zorder=9)
            labels.add(f_tip[0] + 0.35 * f_dir_v[0], f_tip[1] + 0.35 * f_dir_v[1] + 0.22, r"$f$", color=theme["accent"], fontsize=9.5)

        # T and m2 g on m2
        draw_vector(ax, (m2_x, m2_top), (m2_x, m2_top + t_len), color=theme["primary"], lw=2.2, zorder=9)
        labels.add(m2_x + 0.42, m2_top + 0.52 * t_len, r"$T$", color=theme["primary"], fontsize=9.6)

        draw_vector(ax, (m2_x, m2_top - bh2), (m2_x, m2_top - bh2 - w2_len), color=theme["danger"], lw=2.2, zorder=9)
        labels.add(m2_x + 0.52, m2_top - bh2 - 0.65 * w2_len, r"$m_2 g$", color=theme["danger"], fontsize=9.6)

        # Acceleration arrows
        if data["acceleration"] > 1e-6:
            sgn = 1.0 if data["accelerationSigned"] > 0 else -1.0  # + => m1 up incline, m2 down
            a1_c = m1_com + (0.5 * bh + 0.82) * n_hat + 0.55 * sgn * t_hat
            draw_vector(ax, tuple(a1_c - 0.45 * sgn * t_hat), tuple(a1_c + 0.45 * sgn * t_hat), color=theme["purple"], lw=2.3, zorder=10)
            labels.add(a1_c[0] + 0.32 * n_hat[0], a1_c[1] + 0.32 * n_hat[1], r"$\vec{a}$", color=theme["purple"], fontsize=9.8)

            ax2 = m2_x + bw2 / 2 + 0.48
            draw_vector(ax, (ax2, m2_top - bh2 / 2 + 0.40 * sgn), (ax2, m2_top - bh2 / 2 - 0.40 * sgn), color=theme["purple"], lw=2.3, zorder=10)
            labels.add(ax2 + 0.36, m2_top - bh2 / 2, r"$\vec{a}$", color=theme["purple"], fontsize=9.8)

        ymin = min(-0.55, w1_tip[1] - 0.5, m2_top - bh2 - w2_len - 0.5)
        ymax = max(cp[1] + rp + 0.85, n1_tip[1] + 0.95)
        ax.set_xlim(-1.2, m2_x + 2.0)
        ax.set_ylim(ymin, ymax)

    ax.set_aspect("equal", adjustable="box")
    ax.axis("off")

    mdir_clean = data["motionDirection"].replace("_", " ")
    fric_str = f"   |   f = {data['frictionN']:.2f} N" if args.preset != "atwood" else ""
    summary = f"T = {data['tensionN']:.2f} N   |   a = {data['acceleration']:.2f} m/s²{fric_str}   |   Motion: {mdir_clean}"
    add_readout_bar(fig, summary, theme)
    labels.resolve(fig)

    file_info = save_figure(fig, Path(args.out).expanduser().resolve(), args.dpi)
    data.update(file_info)
    data["theme"] = args.theme
    return data


# ---------------------------------------------------------------------------
# 3. Thin Lens Ray Optics (NCERT New Cartesian Sign Convention)
# ---------------------------------------------------------------------------


def compute_optics_lens(f: float, u_raw: float, h: float, virtual_object: bool = False) -> dict[str, Any]:
    if abs(f) < 1e-9:
        raise SystemExit("--f must be non-zero (+ converging / - diverging).")
    if abs(u_raw) < 1e-9:
        raise SystemExit("--u must be non-zero.")
    if abs(h) < 1e-9:
        raise SystemExit("--h must be non-zero.")

    u_norm = False
    u = u_raw
    if u > 0 and not virtual_object:
        u = -abs(u)
        u_norm = True

    denom = u + f
    at_infinity = abs(denom) < 1e-9 * max(abs(f), abs(u), 1.0)
    if at_infinity:
        v = None
        m = None
        h_img = None
        nature = "at_infinity"
    else:
        v = (u * f) / denom
        m = v / u
        h_img = m * h
        if v > 0:
            nature = "real_inverted" if m < 0 else "real_erect"
        else:
            nature = "virtual_erect" if m > 0 else "virtual_inverted"

    return {
        "preset": "optics-lens",
        "lensType": "converging" if f > 0 else "diverging",
        "signConvention": "NCERT_New_Cartesian (light left->right +x, real object u<0, 1/v - 1/u = 1/f, m = v/u)",
        "f": _round_opt(f),
        "u": _round_opt(u),
        "uNormalizedFromPositive": u_norm,
        "h": _round_opt(h),
        "v": _round_opt(v),
        "magnification": _round_opt(m),
        "imageHeight": _round_opt(h_img),
        "nature": nature,
        "atInfinity": at_infinity,
    }


def render_optics_lens(args: argparse.Namespace) -> dict[str, Any]:
    data = compute_optics_lens(args.f, args.u, args.h, getattr(args, "virtual_object", False))
    f = float(data["f"])
    u = float(data["u"])
    h = float(data["h"])
    v = data["v"]
    h_img = data["imageHeight"]
    at_inf = data["atInfinity"]
    af = abs(f)

    lens_name = "Converging (Convex) Thin Lens" if f > 0 else "Diverging (Concave) Thin Lens"
    fig, ax, theme = make_canvas(
        args.theme,
        figsize=(10.0, 5.8),
        title=args.title or f"{lens_name} Ray Diagram",
        subtitle=rf"NCERT Cartesian: $\frac{{1}}{{v}} - \frac{{1}}{{u}} = \frac{{1}}{{f}}$  |  $f = {f:+g}\,\mathrm{{cm}},\; u = {u:+g}\,\mathrm{{cm}},\; h = {h:g}\,\mathrm{{cm}}$",
        ax_rect=(0.04, 0.14, 0.92, 0.73),
    )
    labels = LabelManager(ax, theme)

    # Compute horizontal span cleanly containing object, foci, 2F, and image (clamped gracefully if huge)
    x_candidates = [-2.35 * af, 2.35 * af, u * 1.25]
    if v is not None:
        v_vis = float(np.clip(v, -6.5 * af, 6.5 * af))
        x_candidates.append(v_vis * 1.18)
    else:
        v_vis = None
    xmin = min(x_candidates) - 0.55 * af
    xmax = max(x_candidates) + 0.55 * af
    xspan = xmax - xmin

    # Vertical scale & thin lens half-height
    # Ray 3 hits lens at y_L3 = h_img (exact thin lens property when object not at F)
    max_h = abs(h)
    if h_img is not None and abs(h_img) <= 3.6 * abs(h):
        max_h = max(max_h, abs(h_img))
    elif h_img is not None:
        max_h = max(max_h, 3.2 * abs(h))
    lens_H = max(1.45 * abs(h), 1.18 * max_h)
    lens_H = min(lens_H, 0.34 * xspan)  # Keep pleasing aspect
    y_lim = max(lens_H * 1.32, (abs(h_img) * 1.22 if (h_img is not None and abs(h_img) <= 3.8 * abs(h)) else lens_H * 1.32))

    # Optical axis
    ax.plot([xmin, xmax], [0.0, 0.0], color=theme["ink"], lw=1.2, ls="-.", alpha=0.85, zorder=2)

    # Thin lens silhouette (subtle glass + textbook arrows)
    thick = 0.018 * xspan
    ys = np.linspace(-lens_H, lens_H, 80)
    if f > 0:
        curve = thick * (1.0 - (ys / lens_H) ** 2)
        poly_x = np.concatenate([curve, -curve[::-1]])
        poly_y = np.concatenate([ys, ys[::-1]])
        ax.add_patch(Polygon(np.column_stack([poly_x, poly_y]), closed=True, facecolor=theme["glass_fill"], edgecolor=theme["glass_edge"], lw=1.7, alpha=0.65, zorder=4))
        # Converging end arrowheads
        ah = 0.055 * lens_H
        aw = 0.016 * xspan
        ax.plot([-aw, 0.0, aw], [lens_H - ah, lens_H, lens_H - ah], color=theme["glass_edge"], lw=2.0, zorder=6)
        ax.plot([-aw, 0.0, aw], [-lens_H + ah, -lens_H, -lens_H + ah], color=theme["glass_edge"], lw=2.0, zorder=6)
    else:
        edge_w = thick * 1.25
        waist = thick * 0.25
        curve = waist + (edge_w - waist) * (ys / lens_H) ** 2
        poly_x = np.concatenate([curve, -curve[::-1]])
        poly_y = np.concatenate([ys, ys[::-1]])
        ax.add_patch(Polygon(np.column_stack([poly_x, poly_y]), closed=True, facecolor=theme["glass_fill"], edgecolor=theme["glass_edge"], lw=1.7, alpha=0.65, zorder=4))
        ah = 0.055 * lens_H
        aw = 0.018 * xspan
        ax.plot([-aw, 0.0, aw], [lens_H + ah, lens_H, lens_H + ah], color=theme["glass_edge"], lw=2.0, zorder=6)
        ax.plot([-aw, 0.0, aw], [-lens_H - ah, -lens_H, -lens_H - ah], color=theme["glass_edge"], lw=2.0, zorder=6)

    # Principal plane vertical center line
    ax.plot([0.0, 0.0], [-lens_H * 1.05, lens_H * 1.05], color=theme["glass_edge"], lw=1.0, ls=":", alpha=0.7, zorder=3)

    # Mark 2F1, F1, O, F2, 2F2 (NCERT: F1/2F1 left, F2/2F2 right)
    axis_marks = [
        (-2 * af, r"$2F_1$"),
        (-af, r"$F_1$"),
        (0.0, r"$O$"),
        (+af, r"$F_2$"),
        (+2 * af, r"$2F_2$"),
    ]
    tick_h = 0.032 * y_lim
    for mx, mlbl in axis_marks:
        if xmin + 0.02 * xspan <= mx <= xmax - 0.02 * xspan:
            ax.plot([mx, mx], [-tick_h, tick_h], color=theme["ink"], lw=1.3, zorder=5)
            ax.plot(mx, 0.0, "o", color=theme["ink"], ms=3.2, zorder=6)
            y_off = -0.11 * y_lim
            x_off = 0.018 * xspan if mx == 0.0 else 0.0
            labels.add(mx + x_off, y_off, mlbl, color=theme["ink"], fontsize=9.2, movable=True)

    # Object arrow
    draw_vector(ax, (u, 0.0), (u, h), color=theme["ink"], lw=2.6, head_len=8.5, head_w=4.8, zorder=10)
    labels.add(u, h + (0.11 * y_lim if h > 0 else -0.11 * y_lim), r"$\mathrm{Object}$", color=theme["ink"], fontsize=9.2)

    # Exact 3 Principal Rays via universal thin-lens paraxial refraction:
    # Ray hitting lens at (0, yL) enters from (u, h) with s_in = (yL - h)/(0 - u),
    # emerges on x >= 0 with s_out = s_in - yL / f.
    ray_colors = [theme["primary"], theme["danger"], theme["accent"]]

    def trace_lens_ray(y_lens: float, color: str, draw_pre_focus_guide: tuple[float, float] | None = None) -> None:
        s_in = (y_lens - h) / (0.0 - u)
        s_out = s_in - y_lens / f

        # Incident ray segment (u, h) -> (0, y_lens)
        ax.plot([u, 0.0], [h, y_lens], color=color, lw=1.85, zorder=7)
        draw_mid_arrow(ax, (u, h), (0.0, y_lens), color=color, frac=0.52)

        # Optional pre-lens dashed alignment guide (e.g., from F1 behind/ahead)
        if draw_pre_focus_guide is not None:
            ax.plot([draw_pre_focus_guide[0], u if draw_pre_focus_guide[0] < u else 0.0],
                    [draw_pre_focus_guide[1], h if draw_pre_focus_guide[0] < u else y_lens],
                    color=color, lw=1.1, ls=":", alpha=0.65, zorder=5)

        # Emergent real ray on x >= 0 clipped cleanly to plot box
        x_end = xmax - 0.03 * xspan
        if v is not None and v > 0:
            x_end = max(min(xmax - 0.03 * xspan, v + 0.14 * xspan), min(xmax - 0.03 * xspan, max(v, 1.4 * af)))
        y_end = y_lens + s_out * x_end
        if abs(y_end) > y_lim * 0.96 and abs(s_out) > 1e-9:
            y_clip = math.copysign(y_lim * 0.96, y_end)
            x_end = (y_clip - y_lens) / s_out
            y_end = y_clip
        ax.plot([0.0, x_end], [y_lens, y_end], color=color, lw=1.85, zorder=7)
        if v is not None and v > 0 and v <= x_end:
            draw_mid_arrow(ax, (0.0, y_lens), (v, h_img), color=color, frac=0.46)
        else:
            draw_mid_arrow(ax, (0.0, y_lens), (x_end, y_end), color=color, frac=0.45)

        # Dashed backward virtual extension on x <= 0 for virtual images or infinity
        if at_inf:
            x_back = max(xmin + 0.04 * xspan, u - 0.35 * af)
            y_back = y_lens + s_out * x_back
            if abs(y_back) <= y_lim * 0.98:
                ax.plot([0.0, x_back], [y_lens, y_back], color=theme["purple"], lw=1.25, ls="--", alpha=0.72, zorder=5)
        elif v is not None and v < 0:
            x_target = v
            if f < 0 and abs(y_lens - h) < 1e-9:
                # Extend all the way through left focus F_1 = (f, 0) as well as virtual image (v, h')
                x_target = min(v, f)
            y_target = y_lens + s_out * x_target
            ax.plot([0.0, x_target], [y_lens, y_target], color=theme["purple"], lw=1.35, ls="--", alpha=0.82, zorder=6)

    # Ray 1: Parallel to principal axis -> through focus
    trace_lens_ray(h, ray_colors[0])
    # Ray 2: Through optical center O -> undeviated
    trace_lens_ray(0.0, ray_colors[1])
    # Ray 3: Through/aimed at primary focus (-f, 0) -> emerges parallel (or auxiliary ray if u == -f or huge)
    if at_inf:
        trace_lens_ray(-0.55 * h, ray_colors[2])
    else:
        y_l3 = h_img
        if y_l3 is not None and abs(y_l3) <= lens_H * 0.96:
            guide = (-f, 0.0) if (f > 0 and u > -f) or (f < 0) else None
            trace_lens_ray(y_l3, ray_colors[2], draw_pre_focus_guide=guide)
        else:
            # Auxiliary exact thin-lens ray strictly inside lens rim (still meets exact (v, h')!)
            trace_lens_ray(-0.65 * h, ray_colors[2])

    # Image arrow (solid emerald for real, dashed purple for virtual)
    if at_inf:
        labels.add(
            0.55 * xmax,
            0.68 * y_lim,
            r"$\mathrm{Parallel\ emergent\ rays} \Rightarrow \mathrm{Image\ at}\ \infty$",
            color=theme["purple"],
            fontsize=9.6,
        )
    elif v is not None and h_img is not None:
        is_virtual = v < 0
        img_color = theme["purple"] if is_virtual else theme["success"]
        img_ls = "--" if is_virtual else "-"
        if abs(v) <= xmax - xmin and abs(h_img) <= y_lim * 0.96:
            draw_vector(ax, (v, 0.0), (v, h_img), color=img_color, lw=2.6, ls=img_ls, head_len=8.5, head_w=4.8, zorder=10)
            img_lbl = r"$\mathrm{Virtual\ Image}$" if is_virtual else r"$\mathrm{Real\ Image}$"
            y_lbl = h_img + (0.12 * y_lim if h_img >= 0 else -0.12 * y_lim)
            labels.add(v, y_lbl, img_lbl, color=img_color, fontsize=9.2)

    ax.set_xlim(xmin, xmax)
    ax.set_ylim(-y_lim, y_lim)
    ax.axis("off")

    nature_map = {
        "real_inverted": "Real & Inverted",
        "real_erect": "Real & Erect",
        "virtual_erect": "Virtual & Erect",
        "virtual_inverted": "Virtual & Inverted",
        "at_infinity": "At Infinity",
    }
    nature_str = nature_map.get(data["nature"], data["nature"].replace("_", " ").title())
    summary = (
        f"f = {f:+g} cm   |   u = {u:+g} cm   |   v = {_fmt(v, 2, ' cm')}   |   "
        f"m = {_fmt(data['magnification'], 2)}   |   h' = {_fmt(h_img, 2, ' cm')}   |   Nature: {nature_str}"
    )
    add_readout_bar(fig, summary, theme)
    labels.resolve(fig)

    file_info = save_figure(fig, Path(args.out).expanduser().resolve(), args.dpi)
    data.update(file_info)
    data["theme"] = args.theme
    return data


# ---------------------------------------------------------------------------
# 4. Spherical Mirror Ray Optics (NCERT New Cartesian Sign Convention)
# ---------------------------------------------------------------------------


def compute_optics_mirror(
    mirror_type: str,
    f_raw: float | None,
    r_raw: float | None,
    u_raw: float,
    h: float,
    virtual_object: bool = False,
) -> dict[str, Any]:
    if mirror_type not in ("concave", "convex"):
        raise SystemExit("--type must be 'concave' or 'convex'.")
    if f_raw is None and r_raw is None:
        f_mag = 15.0
    elif r_raw is not None:
        if abs(r_raw) < 1e-9:
            raise SystemExit("--R must be non-zero.")
        f_mag = abs(r_raw) / 2.0
    else:
        assert f_raw is not None
        if abs(f_raw) < 1e-9:
            raise SystemExit("--f must be non-zero.")
        f_mag = abs(f_raw)

    if abs(u_raw) < 1e-9 or abs(h) < 1e-9:
        raise SystemExit("--u and --h must be non-zero.")

    # NCERT New Cartesian sign convention: concave f < 0, convex f > 0
    f = -f_mag if mirror_type == "concave" else +f_mag
    R = 2.0 * f

    u_norm = False
    u = u_raw
    if u > 0 and not virtual_object:
        u = -abs(u)
        u_norm = True

    denom = u - f
    at_infinity = abs(denom) < 1e-9 * max(abs(f), abs(u), 1.0)
    if at_infinity:
        v = None
        m = None
        h_img = None
        nature = "at_infinity"
    else:
        v = (u * f) / denom
        m = -v / u
        h_img = m * h
        if v < 0:
            nature = "real_inverted" if m < 0 else "real_erect"
        else:
            nature = "virtual_erect" if m > 0 else "virtual_inverted"

    return {
        "preset": "optics-mirror",
        "mirrorType": mirror_type,
        "signConvention": "NCERT_New_Cartesian (reflecting face left, concave f<0, convex f>0, 1/v + 1/u = 1/f, m = -v/u)",
        "f": _round_opt(f),
        "R": _round_opt(R),
        "u": _round_opt(u),
        "uNormalizedFromPositive": u_norm,
        "h": _round_opt(h),
        "v": _round_opt(v),
        "magnification": _round_opt(m),
        "imageHeight": _round_opt(h_img),
        "nature": nature,
        "atInfinity": at_infinity,
    }


def render_optics_mirror(args: argparse.Namespace) -> dict[str, Any]:
    data = compute_optics_mirror(args.type, args.f, args.R, args.u, args.h, getattr(args, "virtual_object", False))
    f = float(data["f"])
    R = float(data["R"])
    u = float(data["u"])
    h = float(data["h"])
    v = data["v"]
    h_img = data["imageHeight"]
    at_inf = data["atInfinity"]
    af = abs(f)

    m_name = "Concave Spherical Mirror" if args.type == "concave" else "Convex Spherical Mirror"
    fig, ax, theme = make_canvas(
        args.theme,
        figsize=(10.0, 5.8),
        title=args.title or f"{m_name} Ray Diagram",
        subtitle=rf"NCERT Cartesian: $\frac{{1}}{{v}} + \frac{{1}}{{u}} = \frac{{1}}{{f}}$  |  $f = {f:+g}\,\mathrm{{cm}},\; R = {R:+g}\,\mathrm{{cm}},\; u = {u:+g}\,\mathrm{{cm}}$",
        ax_rect=(0.04, 0.14, 0.92, 0.73),
    )
    labels = LabelManager(ax, theme)

    x_pts = [u * 1.25, f * 1.2, R * 1.2, -2.3 * af if args.type == "concave" else -1.4 * af, 1.35 * af if args.type == "concave" else 2.4 * af]
    if v is not None:
        x_pts.append(float(np.clip(v, -6.5 * af, 6.5 * af)) * 1.18)
    xmin = min(x_pts) - 0.45 * af
    xmax = max(x_pts) + 0.45 * af
    xspan = xmax - xmin

    max_h = abs(h)
    if h_img is not None and abs(h_img) <= 3.5 * abs(h):
        max_h = max(max_h, abs(h_img))
    mirror_H = min(max(1.5 * abs(h), 1.22 * max_h), 0.34 * xspan)
    y_lim = max(mirror_H * 1.30, (abs(h_img) * 1.22 if (h_img is not None and abs(h_img) <= 3.6 * abs(h)) else mirror_H * 1.30))

    # Principal axis
    ax.plot([xmin, xmax], [0.0, 0.0], color=theme["ink"], lw=1.2, ls="-.", alpha=0.85, zorder=2)

    # Subtle paraxial tangent reference line at Pole P (x = 0)
    ax.plot([0.0, 0.0], [-mirror_H * 1.02, mirror_H * 1.02], color=theme["muted"], lw=0.85, ls=":", alpha=0.55, zorder=3)

    # Shallow curved spherical mirror arc with hatched silvered non-reflecting back on right (+x)
    # Use a gentle paraxial curvature sagitta so rays stay visually coincident with mirror while obeying exact paraxial optics!
    sagitta_max = 0.016 * xspan
    ys = np.linspace(-mirror_H, mirror_H, 100)
    sgn_curve = -1.0 if args.type == "concave" else +1.0
    xs_arc = sgn_curve * sagitta_max * (ys / mirror_H) ** 2
    ax.plot(xs_arc, ys, color=theme["glass_edge"], lw=3.0, zorder=6)

    # Silvered back hatches on the right side (+x) of the mirror curve
    hatch_n = 22
    hatch_len = 0.018 * xspan
    for yi in np.linspace(-mirror_H * 0.96, mirror_H * 0.96, hatch_n):
        xi = sgn_curve * sagitta_max * (yi / mirror_H) ** 2
        ax.plot([xi, xi + hatch_len], [yi, yi - 0.035 * mirror_H], color=theme["muted"], lw=0.95, zorder=5)

    # Key points P, F, C
    for mx, mlbl in [(0.0, r"$P$"), (f, r"$F$"), (R, r"$C$")]:
        if xmin + 0.02 * xspan <= mx <= xmax - 0.02 * xspan:
            ax.plot(mx, 0.0, "o", color=theme["ink"], ms=4.0, zorder=8)
            x_off = -0.022 * xspan if mx == 0.0 else 0.0
            labels.add(mx + x_off, -0.11 * y_lim, mlbl, color=theme["ink"], fontsize=9.8)

    # Object arrow
    draw_vector(ax, (u, 0.0), (u, h), color=theme["ink"], lw=2.6, head_len=8.5, head_w=4.8, zorder=10)
    labels.add(u, h + (0.11 * y_lim if h > 0 else -0.11 * y_lim), r"$\mathrm{Object}$", color=theme["ink"], fontsize=9.2)

    # Exact paraxial reflection law at x = 0:
    # Incident ray from (u, h) hits (0, yM) with slope s_in = (yM - h) / (-u).
    # Reflected ray travels back into x <= 0 with slope s_ref = -s_in + yM / f?
    # Verify: line through (0, yM) with slope s_ref evaluated at x = v:
    #   y(v) = yM + s_ref * v. We need y(v) = h' = -v*h/u for ALL yM!
    #   Since 1/v = 1/f - 1/u => v/f - v/u = 1 => 1 + v*(1/u - 1/f) = 0:
    #   If s_ref = (yM / u - yM / f) - h / u = (yM - h)/u - yM/f = -s_in - yM/f?
    #   WAIT: check with s_in = (yM - h)/(0 - u) = -(yM - h)/u!
    #   Check Ray 1 (parallel, yM = h): s_in = 0, must pass through F = (f, 0) => slope = (0 - h)/(f - 0) = -h/f!
    #   Check s_ref = -s_in - yM/f = 0 - h/f = -h/f! EXACT!
    #   Check Ray 2 (Pole, yM = 0): s_in = h/u, must reflect symmetrically => s_ref = -h/u = -s_in - 0! EXACT!
    #   Check y(v) = yM + (-s_in - yM/f)*v = yM + ((yM - h)/u - yM/f)*v = yM*(1 + v/u - v/f) - (v/u)*h = -v*h/u = h'!
    #   IDENTICALLY EXACT FOR EVERY POSSIBLE yM, CONCAVE OR CONVEX, REAL OR VIRTUAL!
    ray_colors = [theme["primary"], theme["danger"], theme["accent"]]

    def trace_mirror_ray(y_m: float, color: str) -> None:
        s_in = (y_m - h) / (0.0 - u)
        s_ref = -s_in - y_m / f

        # Incident ray (u, h) -> (0, y_m)
        ax.plot([u, 0.0], [h, y_m], color=color, lw=1.85, zorder=7)
        retracing = abs(s_ref - s_in) < 1e-6
        draw_mid_arrow(ax, (u, h), (0.0, y_m), color=color, frac=0.34 if retracing else 0.48)

        # Reflected real ray going left into x <= 0
        x_end = xmin + 0.03 * xspan
        if v is not None and v < 0:
            x_end = min(max(xmin + 0.03 * xspan, v - 0.14 * xspan), min(v, u) - 0.10 * xspan)
        y_end = y_m + s_ref * x_end
        if abs(y_end) > y_lim * 0.96 and abs(s_ref) > 1e-9:
            y_clip = math.copysign(y_lim * 0.96, y_end)
            x_end = (y_clip - y_m) / s_ref
            y_end = y_clip
        ax.plot([0.0, x_end], [y_m, y_end], color=color, lw=1.85, zorder=7)
        if v is not None and v < 0 and v >= x_end:
            draw_mid_arrow(ax, (0.0, y_m), (v, h_img), color=color, frac=0.68 if retracing else 0.52)
        else:
            draw_mid_arrow(ax, (0.0, y_m), (x_end, y_end), color=color, frac=0.72 if retracing else 0.45)

        # Dashed virtual extensions behind the mirror (x >= 0)
        if v is not None and v > 0:
            x_ext = v
            if args.type == "convex":
                if abs(y_m - h) < 1e-9:
                    x_ext = max(v, f)
                elif abs(s_ref - s_in) < 1e-6:
                    x_ext = max(v, R)
            y_ext = y_m + s_ref * x_ext
            ax.plot([0.0, x_ext], [y_m, y_ext], color=theme["purple"], lw=1.35, ls="--", alpha=0.82, zorder=6)
        elif at_inf:
            x_ext = min(xmax - 0.04 * xspan, 0.85 * af)
            y_ext = y_m + s_ref * x_ext
            if abs(y_ext) <= y_lim * 0.98:
                ax.plot([0.0, x_ext], [y_m, y_ext], color=theme["purple"], lw=1.2, ls="--", alpha=0.65, zorder=5)

    # Ray 1: Parallel to axis -> reflects through (or appears from) F
    trace_mirror_ray(h, ray_colors[0])

    # Ray 2: Through Center of Curvature C (retraces itself!) — fall back to Pole ray if object near C or y_C too large
    use_c_ray = False
    if abs(u - R) > 0.18 * af:
        y_c = (-R * h) / (u - R)
        if abs(y_c) <= mirror_H * 0.95:
            use_c_ray = True
            # Draw line connecting C, object, and mirror point cleanly
            trace_mirror_ray(y_c, ray_colors[1])
            if args.type == "concave" and R < u:
                ax.plot([R, u], [0.0, h], color=ray_colors[1], lw=1.2, ls=":", alpha=0.65, zorder=5)
    if not use_c_ray:
        # Exact Pole P reflection ray
        trace_mirror_ray(0.0, ray_colors[1])

    # Ray 3: Through/aimed at F -> reflects parallel (y_m = h_img!), or Pole ray if at_inf or out of mirror rim
    if not at_inf and h_img is not None and abs(h_img) <= mirror_H * 0.95:
        trace_mirror_ray(h_img, ray_colors[2])
        if args.type == "convex":
            # Dotted aiming guide from mirror bounce point to F behind mirror
            ax.plot([0.0, f], [h_img, 0.0], color=ray_colors[2], lw=1.1, ls=":", alpha=0.6, zorder=5)
    else:
        trace_mirror_ray(0.0 if use_c_ray else -0.55 * h, ray_colors[2])

    # Image arrow
    if at_inf:
        labels.add(
            0.55 * xmin,
            0.70 * y_lim,
            r"$\mathrm{Parallel\ reflected\ rays} \Rightarrow \mathrm{Image\ at}\ \infty$",
            color=theme["purple"],
            fontsize=9.6,
        )
    elif v is not None and h_img is not None:
        is_virtual = v > 0
        img_color = theme["purple"] if is_virtual else theme["success"]
        img_ls = "--" if is_virtual else "-"
        if xmin <= v <= xmax and abs(h_img) <= y_lim * 0.96:
            draw_vector(ax, (v, 0.0), (v, h_img), color=img_color, lw=2.6, ls=img_ls, head_len=8.5, head_w=4.8, zorder=10)
            img_lbl = r"$\mathrm{Virtual\ Image}$" if is_virtual else r"$\mathrm{Real\ Image}$"
            y_lbl = h_img + (0.12 * y_lim if h_img >= 0 else -0.12 * y_lim)
            labels.add(v, y_lbl, img_lbl, color=img_color, fontsize=9.2)

    ax.set_xlim(xmin, xmax)
    ax.set_ylim(-y_lim, y_lim)
    ax.axis("off")

    nature_map = {
        "real_inverted": "Real & Inverted",
        "real_erect": "Real & Erect",
        "virtual_erect": "Virtual & Erect",
        "virtual_inverted": "Virtual & Inverted",
        "at_infinity": "At Infinity",
    }
    nature_str = nature_map.get(data["nature"], data["nature"].replace("_", " ").title())
    summary = (
        f"f = {f:+g} cm   |   R = {R:+g} cm   |   u = {u:+g} cm   |   v = {_fmt(v, 2, ' cm')}   |   "
        f"m = {_fmt(data['magnification'], 2)}   |   Nature: {nature_str}"
    )
    add_readout_bar(fig, summary, theme)
    labels.resolve(fig)

    file_info = save_figure(fig, Path(args.out).expanduser().resolve(), args.dpi)
    data.update(file_info)
    data["theme"] = args.theme
    return data


# ---------------------------------------------------------------------------
# 5. Prism Refraction, Minimum Deviation & Exact TIR Ray Tracing
# ---------------------------------------------------------------------------


def _ray_segment_hit(
    origin: np.ndarray,
    direction: np.ndarray,
    seg_a: np.ndarray,
    seg_b: np.ndarray,
) -> tuple[float, np.ndarray] | None:
    """Return (t > 1e-6, hit_point) where ray origin + t*direction intersects segment [seg_a, seg_b]."""
    v = seg_b - seg_a
    denom = direction[0] * v[1] - direction[1] * v[0]
    if abs(denom) < 1e-12:
        return None
    diff = seg_a - origin
    t = (diff[0] * v[1] - diff[1] * v[0]) / denom
    s = (diff[0] * direction[1] - diff[1] * direction[0]) / denom
    if t > 1e-6 and -1e-6 <= s <= 1.0 + 1e-6:
        return (float(t), origin + t * direction)
    return None


def compute_optics_prism(A_deg: float, n: float, i1_deg: float | None, min_dev: bool) -> dict[str, Any]:
    if not (5.0 <= A_deg <= 150.0):
        raise SystemExit("--A (apex angle) must be in [5, 150] degrees.")
    if n <= 1.0:
        raise SystemExit("--n (refractive index) must be > 1.0.")

    theta_c_deg = math.degrees(math.asin(1.0 / n))
    no_emergence_prism = A_deg > 2.0 * theta_c_deg + 1e-9

    sin_imin = n * math.sin(math.radians(0.5 * A_deg))
    if sin_imin <= 1.0 + 1e-12:
        i_min_deg: float | None = math.degrees(math.asin(min(1.0, sin_imin)))
        delta_min_deg: float | None = 2.0 * i_min_deg - A_deg
    else:
        i_min_deg = None
        delta_min_deg = None

    # Grazing emergence minimum incidence angle (when A <= 2*theta_c)
    r1_grazing = A_deg - theta_c_deg
    if r1_grazing <= 0.0:
        i1_grazing_deg: float | None = 0.0
    elif n * math.sin(math.radians(r1_grazing)) <= 1.0:
        i1_grazing_deg = math.degrees(math.asin(n * math.sin(math.radians(r1_grazing))))
    else:
        i1_grazing_deg = None

    if min_dev:
        if i_min_deg is None:
            raise SystemExit(
                f"Minimum deviation does not exist for A={A_deg:g}°, n={n:g} because A > 2*theta_c ({2*theta_c_deg:.2f}°): no ray can emerge from face 2."
            )
        i1 = i_min_deg
    else:
        i1 = 45.0 if i1_deg is None else i1_deg
        if not (0.0 <= i1 < 90.0):
            raise SystemExit("--i1 must be in [0, 90) degrees.")

    r1 = math.degrees(math.asin(math.sin(math.radians(i1)) / n))
    r2 = A_deg - r1
    sin_i2_raw = n * math.sin(math.radians(r2))
    tir = abs(sin_i2_raw) > 1.0 + 1e-12

    if not tir:
        i2: float | None = math.degrees(math.asin(max(-1.0, min(1.0, sin_i2_raw))))
        delta: float | None = i1 + i2 - A_deg
    else:
        i2 = None
        delta = None  # Will be populated from exact exit ray after TIR trace!

    return {
        "preset": "optics-prism",
        "A": _round_opt(A_deg),
        "n": _round_opt(n),
        "i1": _round_opt(i1),
        "r1": _round_opt(r1),
        "r2": _round_opt(r2),
        "i2": _round_opt(i2),
        "delta": _round_opt(delta),
        "criticalAngle": _round_opt(theta_c_deg),
        "tir": tir,
        "noEmergencePrism": no_emergence_prism,
        "minDevUsed": min_dev,
        "iMin": _round_opt(i_min_deg),
        "deltaMin": _round_opt(delta_min_deg),
        "grazingIncidenceMinI1": _round_opt(i1_grazing_deg),
    }


def render_optics_prism(args: argparse.Namespace) -> dict[str, Any]:
    data = compute_optics_prism(args.A, args.n, args.i1, args.min_dev)
    A = float(data["A"])
    n = float(data["n"])
    i1 = float(data["i1"])
    r1 = float(data["r1"])
    r2 = float(data["r2"])
    tir = bool(data["tir"])
    alpha = math.radians(0.5 * A)

    # Isosceles prism triangle geometry
    H = 3.6
    half_base = H * math.tan(alpha)
    # Keep extremely wide apex angles bounded gracefully
    if half_base > 4.2:
        scale_p = 4.2 / half_base
        H *= scale_p
        half_base *= scale_p
    p_apex = np.array([0.0, H])
    p_left = np.array([-half_base, 0.0])
    p_right = np.array([+half_base, 0.0])

    # Outward unit normals of all 3 faces
    n_face1 = np.array([-math.cos(alpha), math.sin(alpha)])   # Left face
    n_face2 = np.array([+math.cos(alpha), math.sin(alpha)])   # Right face
    n_base = np.array([0.0, -1.0])                            # Bottom base

    # Exact entry point E1 along Face 1 (positioned so internal ray hits Face 2 cleanly)
    e1 = p_left + 0.56 * (p_apex - p_left)

    # Exact unit vectors of incident ray d0 and internal ray d1
    th0 = math.radians(i1) - alpha
    th1 = math.radians(r1) - alpha
    d0 = np.array([math.cos(th0), math.sin(th0)])
    d1 = np.array([math.cos(th1), math.sin(th1)])

    # Intersect d1 with Face 2 and Base to find exact E2
    hit_f2 = _ray_segment_hit(e1, d1, p_apex, p_right)
    hit_base = _ray_segment_hit(e1, d1, p_left, p_right)

    # Choose E1 higher if d1 hits base before Face 2
    if hit_f2 is None and hit_base is not None:
        e1 = p_left + 0.78 * (p_apex - p_left)
        hit_f2 = _ray_segment_hit(e1, d1, p_apex, p_right)
        hit_base = _ray_segment_hit(e1, d1, p_left, p_right)

    hits_first = [(h[0], "face2", h[1]) for h in [hit_f2] if h is not None] + [
        (h[0], "base", h[1]) for h in [hit_base] if h is not None
    ]
    hits_first.sort(key=lambda item: item[0])
    if not hits_first:
        raise SystemExit("Internal ray failed to intersect prism boundary.")
    _, hit_name, e2 = hits_first[0]

    subtitle_mode = "Minimum Deviation" if args.min_dev else ("Total Internal Reflection (TIR)" if tir else "Refraction")
    fig, ax, theme = make_canvas(
        args.theme,
        figsize=(9.6, 6.2),
        title=args.title or f"Glass Prism Ray Tracing ($A = {A:g}^\\circ,\\; n = {n:g}$)",
        subtitle=f"{subtitle_mode}  |  $\\theta_c = {data['criticalAngle']:.2f}^\\circ,\\; i_1 = {i1:.2f}^\\circ,\\; r_1 = {r1:.2f}^\\circ,\\; r_2 = {r2:.2f}^\\circ$",
        ax_rect=(0.05, 0.14, 0.90, 0.73),
    )
    labels = LabelManager(ax, theme)

    # Draw prism polygon
    prism_poly = Polygon(
        [p_left, p_right, p_apex],
        closed=True,
        facecolor=theme["glass_fill"],
        edgecolor=theme["glass_edge"],
        linewidth=2.3,
        alpha=0.62,
        zorder=3,
    )
    ax.add_patch(prism_poly)

    # Apex angle A arc
    a_arc_r = min(0.68, 0.25 * H)
    mid_a = draw_angle_arc(ax, tuple(p_apex), a_arc_r, 270.0 - 0.5 * A, 270.0 + 0.5 * A, theme["glass_edge"], lw=1.4, zorder=6)
    labels.add(p_apex[0] + (a_arc_r + 0.26) * mid_a[0], p_apex[1] + (a_arc_r + 0.26) * mid_a[1], rf"$A={A:g}^\circ$", color=theme["glass_edge"], fontsize=9.0)
    labels.add(0.0, 0.26, rf"$n = {n:g}$", color=theme["glass_edge"], fontsize=9.5, pill=False)

    # Draw surface normals at E1 and E2
    norm_len = 1.05
    ax.plot(
        [e1[0] - norm_len * n_face1[0], e1[0] + norm_len * n_face1[0]],
        [e1[1] - norm_len * n_face1[1], e1[1] + norm_len * n_face1[1]],
        color=theme["muted"],
        lw=1.15,
        ls="--",
        zorder=4,
    )
    n2_draw = n_face2 if hit_name == "face2" else n_base
    ax.plot(
        [e2[0] - norm_len * n2_draw[0], e2[0] + norm_len * n2_draw[0]],
        [e2[1] - norm_len * n2_draw[1], e2[1] + norm_len * n2_draw[1]],
        color=theme["muted"],
        lw=1.15,
        ls="--",
        zorder=4,
    )

    # Incident ray segment
    ray_ext = 2.2
    p_inc_start = e1 - ray_ext * d0
    ax.plot([p_inc_start[0], e1[0]], [p_inc_start[1], e1[1]], color=theme["primary"], lw=2.3, zorder=8)
    draw_mid_arrow(ax, tuple(p_inc_start), tuple(e1), color=theme["primary"], frac=0.52)

    # Angle i1 arc (between outward normal n_face1 and -d0)
    ang_n1 = math.degrees(math.atan2(n_face1[1], n_face1[0]))
    ang_neg_d0 = math.degrees(math.atan2(-d0[1], -d0[0]))
    if i1 > 2.0:
        mi1 = draw_angle_arc(ax, tuple(e1), 0.58, ang_n1, ang_neg_d0, theme["primary"], lw=1.3)
        if i1 < 22.0:
            # Place cleanly above the outward normal for narrow acute incidence angles
            n1_perp = np.array([-n_face1[1], n_face1[0]])
            if n1_perp[1] < 0:
                n1_perp = -n1_perp
            p_lbl_i1 = e1 + 0.85 * n_face1 + 0.34 * n1_perp
            labels.add(float(p_lbl_i1[0]), float(p_lbl_i1[1]), rf"$i_1={i1:.1f}^\circ$", color=theme["primary"], fontsize=8.8)
        else:
            labels.add(e1[0] + 0.92 * mi1[0], e1[1] + 0.92 * mi1[1], rf"$i_1={i1:.1f}^\circ$", color=theme["primary"], fontsize=8.8)

    # Internal ray segment E1 -> E2
    ax.plot([e1[0], e2[0]], [e1[1], e2[1]], color=theme["accent"], lw=2.3, zorder=8)
    draw_mid_arrow(ax, tuple(e1), tuple(e2), color=theme["accent"], frac=0.50)

    # Angle r1 arc (between inward normal -n_face1 and d1)
    ang_in1 = math.degrees(math.atan2(-n_face1[1], -n_face1[0]))
    ang_d1 = math.degrees(math.atan2(d1[1], d1[0]))
    if r1 > 2.0:
        mr1 = draw_angle_arc(ax, tuple(e1), 0.50, ang_in1, ang_d1, theme["accent"], lw=1.2)
        if r1 < 18.0:
            in1_perp = np.array([n_face1[1], -n_face1[0]])
            if in1_perp[1] > 0:
                in1_perp = -in1_perp
            p_lbl_r1 = e1 - 0.65 * n_face1 + 0.28 * in1_perp
            labels.add(float(p_lbl_r1[0]), float(p_lbl_r1[1]), r"$r_1$", color=theme["accent"], fontsize=8.8)
        else:
            labels.add(e1[0] + 0.82 * mr1[0], e1[1] + 0.82 * mr1[1], r"$r_1$", color=theme["accent"], fontsize=8.8)

    pts_bounds = [p_left, p_right, p_apex, p_inc_start, e1 + norm_len * n_face1, e2 + norm_len * n2_draw]

    if not tir and hit_name == "face2":
        i2 = float(data["i2"])
        delta = float(data["delta"])
        th2 = alpha - math.radians(i2)
        d2 = np.array([math.cos(th2), math.sin(th2)])
        p_em_end = e2 + ray_ext * d2
        ax.plot([e2[0], p_em_end[0]], [e2[1], p_em_end[1]], color=theme["success"], lw=2.3, zorder=8)
        draw_mid_arrow(ax, tuple(e2), tuple(p_em_end), color=theme["success"], frac=0.52)
        pts_bounds.append(p_em_end)

        # Angle r2 & i2 arcs
        ang_n2 = math.degrees(math.atan2(n_face2[1], n_face2[0]))
        ang_in2 = math.degrees(math.atan2(-n_face2[1], -n_face2[0]))
        ang_neg_d1 = math.degrees(math.atan2(-d1[1], -d1[0]))
        ang_d2 = math.degrees(math.atan2(d2[1], d2[0]))
        if abs(r2) > 2.0:
            mr2 = draw_angle_arc(ax, tuple(e2), 0.48, ang_in2, ang_neg_d1, theme["accent"], lw=1.2)
            labels.add(e2[0] + 0.78 * mr2[0], e2[1] + 0.78 * mr2[1], r"$r_2$", color=theme["accent"], fontsize=8.8)
        if abs(i2) > 2.0:
            mi2 = draw_angle_arc(ax, tuple(e2), 0.58, ang_n2, ang_d2, theme["success"], lw=1.3)
            labels.add(e2[0] + 0.90 * mi2[0], e2[1] + 0.90 * mi2[1], rf"$i_2={i2:.1f}^\circ$", color=theme["success"], fontsize=8.8)

        # Exact intersection of extended incident ray and backward emergent ray for deviation angle delta
        denom_d = d0[0] * d2[1] - d0[1] * d2[0]
        if abs(denom_d) > 1e-6:
            diff_e = e2 - e1
            t_int = (diff_e[0] * d2[1] - diff_e[1] * d2[0]) / denom_d
            p_dev = e1 + t_int * d0
            p_fwd = p_dev + 1.05 * d0
            ax.plot([e1[0], p_fwd[0]], [e1[1], p_fwd[1]], color=theme["purple"], lw=1.15, ls="--", alpha=0.8, zorder=5)
            ax.plot([e2[0], p_dev[0]], [e2[1], p_dev[1]], color=theme["purple"], lw=1.15, ls="--", alpha=0.8, zorder=5)
            pts_bounds.extend([p_dev, p_fwd])
            ang_d0_deg = math.degrees(math.atan2(d0[1], d0[0]))
            md = draw_angle_arc(ax, tuple(p_dev), 0.55, ang_d2, ang_d0_deg, theme["purple"], lw=1.5, zorder=7)
            labels.add(p_dev[0] + 0.98 * md[0], p_dev[1] + 0.98 * md[1], rf"$\delta={delta:.1f}^\circ$", color=theme["purple"], fontsize=9.4)
    else:
        # Exact Total Internal Reflection (TIR) ray tracing!
        # Reflect d1 off Face 2 (or hit face) using exact vector reflection: d_refl = d1 - 2 (d1 . n) n
        n_hit = n_face2 if hit_name == "face2" else n_base
        d_cur = d1 - 2.0 * float(np.dot(d1, n_hit)) * n_hit
        d_cur = d_cur / np.linalg.norm(d_cur)
        cur_pt = e2
        cur_face = hit_name
        tir_bounces = 1
        exit_dir = None

        labels.add(
            e2[0] + 0.95 * n_hit[0],
            e2[1] + 0.95 * n_hit[1],
            rf"$\mathrm{{TIR}}\ (r_2={r2:.1f}^\circ > \theta_c)$",
            color=theme["danger"],
            fontsize=9.1,
        )

        # Trace up to 3 internal bounces until clean exit into air
        faces_geom = {
            "face1": (p_left, p_apex, n_face1),
            "face2": (p_apex, p_right, n_face2),
            "base": (p_left, p_right, n_base),
        }
        for _ in range(3):
            next_hits = []
            for fname, (fa, fb, fn) in faces_geom.items():
                if fname == cur_face:
                    continue
                res_h = _ray_segment_hit(cur_pt, d_cur, fa, fb)
                if res_h is not None:
                    next_hits.append((res_h[0], fname, res_h[1], fn))
            if not next_hits:
                break
            next_hits.sort(key=lambda item: item[0])
            _, next_face, next_pt, next_n = next_hits[0]
            ax.plot([cur_pt[0], next_pt[0]], [cur_pt[1], next_pt[1]], color=theme["danger"], lw=2.3, zorder=8)
            draw_mid_arrow(ax, tuple(cur_pt), tuple(next_pt), color=theme["danger"], frac=0.52)
            pts_bounds.append(next_pt)

            # Check Snell's law at next_pt (glass n -> air 1)
            cos_inc = float(np.dot(d_cur, next_n))  # > 0 since leaving prism
            cos_inc = max(0.0, min(1.0, cos_inc))
            sin_inc = math.sqrt(max(0.0, 1.0 - cos_inc * cos_inc))
            if n * sin_inc <= 1.0 + 1e-12:
                # Emerges cleanly into air!
                t_vec = d_cur - cos_inc * next_n
                t_norm = float(np.linalg.norm(t_vec))
                t_unit = t_vec / t_norm if t_norm > 1e-12 else np.array([0.0, 0.0])
                sin_out = min(1.0, n * sin_inc)
                cos_out = math.sqrt(max(0.0, 1.0 - sin_out * sin_out))
                exit_dir = cos_out * next_n + sin_out * t_unit
                exit_dir = exit_dir / np.linalg.norm(exit_dir)
                p_exit = next_pt + 1.85 * exit_dir
                # Normal at exit face
                ax.plot(
                    [next_pt[0] - 0.7 * next_n[0], next_pt[0] + 0.7 * next_n[0]],
                    [next_pt[1] - 0.7 * next_n[1], next_pt[1] + 0.7 * next_n[1]],
                    color=theme["muted"],
                    lw=1.05,
                    ls="--",
                    zorder=4,
                )
                ax.plot([next_pt[0], p_exit[0]], [next_pt[1], p_exit[1]], color=theme["success"], lw=2.3, zorder=8)
                draw_mid_arrow(ax, tuple(next_pt), tuple(p_exit), color=theme["success"], frac=0.52)
                pts_bounds.append(p_exit)
                exit_ang = math.degrees(math.asin(sin_out))
                labels.add(
                    p_exit[0] + 0.35 * exit_dir[0],
                    p_exit[1] + 0.25 * exit_dir[1] - 0.12,
                    rf"$\mathrm{{Exit}}\ (\mathrm{{{next_face}}},\; i_e={exit_ang:.1f}^\circ)$",
                    color=theme["success"],
                    fontsize=8.9,
                )
                data["tirExitFace"] = next_face
                data["tirExitAngleDeg"] = _round_opt(exit_ang)
                break
            else:
                # Secondary TIR bounce
                tir_bounces += 1
                d_cur = d_cur - 2.0 * float(np.dot(d_cur, next_n)) * next_n
                d_cur = d_cur / np.linalg.norm(d_cur)
                cur_pt = next_pt
                cur_face = next_face

        data["tirBounces"] = tir_bounces
        if exit_dir is not None:
            cos_dev = max(-1.0, min(1.0, float(np.dot(d0, exit_dir))))
            data["delta"] = _round_opt(math.degrees(math.acos(cos_dev)))

    arr_b = np.vstack(pts_bounds)
    xmin, ymin = arr_b.min(axis=0)
    xmax, ymax = arr_b.max(axis=0)
    px = max(0.85, 0.14 * (xmax - xmin))
    py = max(0.65, 0.16 * (ymax - ymin))
    ax.set_xlim(xmin - px, xmax + px)
    ax.set_ylim(ymin - py, ymax + py)
    ax.set_aspect("equal", adjustable="box")
    ax.axis("off")

    i2_txt = "TIR" if tir else f"{data['i2']:.2f}°"
    dmin_txt = "None (A > 2θc)" if data["deltaMin"] is None else f"{data['deltaMin']:.2f}°"
    delta_txt = _fmt(data["delta"], 2, "°")
    summary = (
        f"i₁ = {i1:.2f}°   |   r₁ = {r1:.2f}°   |   r₂ = {r2:.2f}°   |   i₂ = {i2_txt}   |   "
        f"θc = {data['criticalAngle']:.2f}°   |   δ = {delta_txt}   |   δ_min = {dmin_txt}"
    )
    add_readout_bar(fig, summary, theme)
    labels.resolve(fig)

    file_info = save_figure(fig, Path(args.out).expanduser().resolve(), args.dpi)
    data.update(file_info)
    data["theme"] = args.theme
    return data


# ---------------------------------------------------------------------------
# 6. 2D Electrostatic Field Lines & Equipotentials
# ---------------------------------------------------------------------------


def parse_charges(spec: str) -> list[tuple[float, float, float]]:
    charges: list[tuple[float, float, float]] = []
    for chunk in spec.split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        parts = [p.strip() for p in chunk.split(",")]
        if len(parts) != 3:
            raise SystemExit(f"Invalid charge entry {chunk!r}; expected 'q,x,y' separated by ';'.")
        q, x, y = float(parts[0]), float(parts[1]), float(parts[2])
        if abs(q) < 1e-12:
            raise SystemExit("Charge magnitude q must be non-zero.")
        charges.append((q, x, y))
    if not charges:
        raise SystemExit("--charges must specify at least one charge.")
    return charges


def parse_lim(spec: str | None) -> tuple[float, float] | None:
    if spec is None:
        return None
    parts = [p.strip() for p in spec.split(",")]
    if len(parts) != 2:
        raise SystemExit(f"Invalid axis limit {spec!r}; expected 'min,max'.")
    lo, hi = float(parts[0]), float(parts[1])
    if hi <= lo:
        raise SystemExit(f"Axis limit max must be > min, got {spec!r}.")
    return (lo, hi)


def _efield_at(x: float, y: float, charges: list[tuple[float, float, float]]) -> tuple[float, float]:
    ex, ey = 0.0, 0.0
    for q, cx, cy in charges:
        dx = x - cx
        dy = y - cy
        r2 = dx * dx + dy * dy
        if r2 < 1e-12:
            return (0.0, 0.0)
        inv_r3 = q / (r2 * math.sqrt(r2))
        ex += inv_r3 * dx
        ey += inv_r3 * dy
    return (ex, ey)


def _find_null_points(
    charges: list[tuple[float, float, float]],
    xlim: tuple[float, float],
    ylim: tuple[float, float],
) -> list[list[float]]:
    found: list[tuple[float, float]] = []

    # Exact closed-form for 2 point charges
    if len(charges) == 2:
        (q1, x1, y1), (q2, x2, y2) = charges
        d = math.hypot(x2 - x1, y2 - y1)
        if d > 1e-9:
            ux, uy = (x2 - x1) / d, (y2 - y1) / d
            s1, s2 = math.sqrt(abs(q1)), math.sqrt(abs(q2))
            if q1 * q2 > 0:
                # Same sign: unique null point strictly between them
                r1 = d * s1 / (s1 + s2)
                found.append((x1 + r1 * ux, y1 + r1 * uy))
            elif abs(s1 - s2) > 1e-9:
                # Opposite sign, unequal magnitude: outside nearer the smaller charge
                if abs(q1) < abs(q2):
                    r1 = d * s1 / (s2 - s1)
                    found.append((x1 - r1 * ux, y1 - r1 * uy))
                else:
                    r2 = d * s2 / (s1 - s2)
                    found.append((x2 + r2 * ux, y2 + r2 * uy))
    elif len(charges) > 2:
        # Coarse grid scan + 2D Newton refinement inside viewport
        gx = np.linspace(xlim[0], xlim[1], 80)
        gy = np.linspace(ylim[0], ylim[1], 80)
        X, Y = np.meshgrid(gx, gy)
        Ex = np.zeros_like(X)
        Ey = np.zeros_like(Y)
        for q, cx, cy in charges:
            dx, dy = X - cx, Y - cy
            r2 = dx * dx + dy * dy + 1e-9
            inv3 = q / (r2 * np.sqrt(r2))
            Ex += inv3 * dx
            Ey += inv3 * dy
        Emag2 = Ex * Ex + Ey * Ey
        # Find local minima in interior
        for iy in range(1, Emag2.shape[0] - 1):
            for ix in range(1, Emag2.shape[1] - 1):
                val = Emag2[iy, ix]
                patch = Emag2[iy - 1 : iy + 2, ix - 1 : ix + 2]
                if val <= patch.min() and val < 0.08:
                    # Refine with damped 2D Newton using finite-difference Jacobian
                    x_c, y_c = float(X[iy, ix]), float(Y[iy, ix])
                    eps = 1e-5
                    for _ in range(18):
                        fx, fy = _efield_at(x_c, y_c, charges)
                        if math.hypot(fx, fy) < 1e-8:
                            break
                        fx_dx, fy_dx = _efield_at(x_c + eps, y_c, charges)
                        fx_dy, fy_dy = _efield_at(x_c, y_c + eps, charges)
                        j11, j21 = (fx_dx - fx) / eps, (fy_dx - fy) / eps
                        j12, j22 = (fx_dy - fx) / eps, (fy_dy - fy) / eps
                        det = j11 * j22 - j12 * j21
                        if abs(det) < 1e-14:
                            break
                        step_x = (j22 * fx - j12 * fy) / det
                        step_y = (-j21 * fx + j11 * fy) / det
                        x_c -= max(-0.2, min(0.2, step_x))
                        y_c -= max(-0.2, min(0.2, step_y))
                    ex_f, ey_f = _efield_at(x_c, y_c, charges)
                    near_charge = any(math.hypot(x_c - cx, y_c - cy) < 0.25 for _, cx, cy in charges)
                    if math.hypot(ex_f, ey_f) < 1e-4 and not near_charge:
                        if all(math.hypot(x_c - ox, y_c - oy) > 0.15 for ox, oy in found):
                            found.append((x_c, y_c))

    return [[_round_opt(px, 5), _round_opt(py, 5)] for px, py in found]


def _trace_field_line(
    x0: float,
    y0: float,
    charges: list[tuple[float, float, float]],
    direction: float,
    xlim: tuple[float, float],
    ylim: tuple[float, float],
    capture_r: float,
    ds: float,
    max_steps: int = 900,
) -> tuple[np.ndarray, int | None]:
    """RK4 streamline tracer for unit electric field vector +/- E/|E|."""

    def unit_e(x: float, y: float) -> tuple[float, float] | None:
        ex, ey = _efield_at(x, y, charges)
        mag = math.hypot(ex, ey)
        if mag < 1e-10:
            return None
        return (direction * ex / mag, direction * ey / mag)

    pts = [(x0, y0)]
    x, y = x0, y0
    hit_idx: int | None = None
    x_pad = 0.08 * (xlim[1] - xlim[0])
    y_pad = 0.08 * (ylim[1] - ylim[0])

    for step in range(max_steps):
        k1 = unit_e(x, y)
        if k1 is None:
            break
        k2 = unit_e(x + 0.5 * ds * k1[0], y + 0.5 * ds * k1[1])
        if k2 is None:
            break
        k3 = unit_e(x + 0.5 * ds * k2[0], y + 0.5 * ds * k2[1])
        if k3 is None:
            break
        k4 = unit_e(x + ds * k3[0], y + ds * k3[1])
        if k4 is None:
            break

        xn = x + (ds / 6.0) * (k1[0] + 2 * k2[0] + 2 * k3[0] + k4[0])
        yn = y + (ds / 6.0) * (k1[1] + 2 * k2[1] + 2 * k3[1] + k4[1])
        pts.append((xn, yn))
        x, y = xn, yn

        if step > 2:
            for idx, (q, cx, cy) in enumerate(charges):
                if (direction > 0 and q < 0) or (direction < 0 and q > 0):
                    if math.hypot(x - cx, y - cy) <= capture_r:
                        hit_idx = idx
                        break
            if hit_idx is not None:
                break

        if not (xlim[0] - x_pad <= x <= xlim[1] + x_pad and ylim[0] - y_pad <= y <= ylim[1] + y_pad):
            break

    return np.array(pts, dtype=float), hit_idx


def render_field_charges(args: argparse.Namespace) -> dict[str, Any]:
    charges = parse_charges(args.charges)
    xs = [c[1] for c in charges]
    ys = [c[2] for c in charges]
    span = max(max(xs) - min(xs), max(ys) - min(ys), 2.5)
    cx_mid = 0.5 * (min(xs) + max(xs))
    cy_mid = 0.5 * (min(ys) + max(ys))

    xlim = parse_lim(args.xlim) or (cx_mid - 0.95 * span, cx_mid + 0.95 * span)
    ylim = parse_lim(args.ylim) or (cy_mid - 0.72 * span, cy_mid + 0.72 * span)

    net_q = sum(c[0] for c in charges)
    px = sum(c[0] * c[1] for c in charges)
    py = sum(c[0] * c[2] for c in charges)
    p_mag = math.hypot(px, py)
    null_pts = _find_null_points(charges, xlim, ylim)

    net_q_str = "0" if abs(net_q) < 1e-12 else f"{net_q:+g}"
    fig, ax, theme = make_canvas(
        args.theme,
        figsize=(9.6, 6.2),
        title=args.title or "2D Electric Field Lines & Equipotential Contours",
        subtitle=f"{len(charges)} point charge(s)  |  $Q_{{\\mathrm{{net}}}} = {net_q_str}$  |  Field lines $\\perp$ equipotentials everywhere",
        ax_rect=(0.09, 0.17, 0.85, 0.70),
    )
    labels = LabelManager(ax, theme)

    # Subtle grid
    ax.grid(True, color=theme["grid"], lw=0.75, alpha=0.8, zorder=0)
    ax.set_axisbelow(True)

    # Equipotential contours
    if args.equipotentials:
        gx = np.linspace(xlim[0], xlim[1], 240)
        gy = np.linspace(ylim[0], ylim[1], 200)
        X, Y = np.meshgrid(gx, gy)
        V = np.zeros_like(X)
        mask = np.zeros_like(X, dtype=bool)
        r_mask = 0.055 * min(xlim[1] - xlim[0], ylim[1] - ylim[0])
        for q, cx, cy in charges:
            r = np.hypot(X - cx, Y - cy)
            mask |= r < r_mask
            V += q / np.maximum(r, 1e-6)
        V_masked = np.ma.array(V, mask=mask)
        # Symmetric arcsinh-spaced contour levels so near & far field look balanced
        v_scale = max(abs(c[0]) for c in charges) / max(0.35 * span, 0.6)
        u_levels = np.linspace(-2.5, 2.5, 19)
        levels = np.sinh(u_levels) * v_scale * 0.45
        ax.contour(
            X,
            Y,
            V_masked,
            levels=levels,
            colors=theme["purple"],
            linestyles="--",
            linewidths=0.95,
            alpha=0.58,
            zorder=2,
        )

    # RK4 Exact Symmetric Field Lines
    view_min = min(xlim[1] - xlim[0], ylim[1] - ylim[0])
    badge_r = 0.044 * view_min
    seed_r = badge_r * 1.15
    ds = 0.012 * view_min
    min_q = min(abs(c[0]) for c in charges)

    # Track incoming angles hitting negative charges to prevent duplicate lines
    neg_captured_angles: dict[int, list[float]] = {i: [] for i, c in enumerate(charges) if c[0] < 0}
    drawn_lines: list[tuple[np.ndarray, float]] = []

    # 1) Trace outward from every positive charge
    for idx, (q, cx, cy) in enumerate(charges):
        if q <= 0:
            continue
        n_lines = max(8, min(20, int(round(12 * abs(q) / min_q))))
        # Offset half-step when only like charges exist so lines cleanly showcase null-point repulsion
        has_neg = any(c[0] < 0 for c in charges)
        offset = 0.0 if has_neg else (math.pi / n_lines)
        for k in range(n_lines):
            ang = offset + 2.0 * math.pi * k / n_lines
            pts, hit_idx = _trace_field_line(
                cx + seed_r * math.cos(ang),
                cy + seed_r * math.sin(ang),
                charges,
                direction=+1.0,
                xlim=xlim,
                ylim=ylim,
                capture_r=seed_r,
                ds=ds,
            )
            drawn_lines.append((pts, +1.0))
            if hit_idx is not None:
                _, nx, ny = charges[hit_idx]
                inc_ang = math.atan2(pts[-1, 1] - ny, pts[-1, 0] - nx)
                neg_captured_angles[hit_idx].append(inc_ang)

    # 2) Trace backward from negative charges for uncaptured incoming directions
    for idx, (q, cx, cy) in enumerate(charges):
        if q >= 0:
            continue
        n_lines = max(8, min(20, int(round(12 * abs(q) / min_q))))
        ang_tol = 0.65 * (2.0 * math.pi / n_lines)
        for k in range(n_lines):
            ang = 2.0 * math.pi * k / n_lines
            already = False
            for c_ang in neg_captured_angles[idx]:
                diff = abs((ang - c_ang + math.pi) % (2.0 * math.pi) - math.pi)
                if diff < ang_tol:
                    already = True
                    break
            if already:
                continue
            pts, hit_pos = _trace_field_line(
                cx + seed_r * math.cos(ang),
                cy + seed_r * math.sin(ang),
                charges,
                direction=-1.0,
                xlim=xlim,
                ylim=ylim,
                capture_r=seed_r,
                ds=ds,
            )
            if hit_pos is not None:
                continue
            # Reverse so points run strictly along +E (from infinity into -q)
            drawn_lines.append((pts[::-1], +1.0))

    # Plot field lines + directional arrowheads
    for pts, _ in drawn_lines:
        if len(pts) < 4:
            continue
        ax.plot(pts[:, 0], pts[:, 1], color=theme["teal"], lw=1.45, alpha=0.92, zorder=4)
        # Find an interior point well inside viewport and away from charges for the arrowhead
        seg_lens = np.hypot(np.diff(pts[:, 0]), np.diff(pts[:, 1]))
        cum = np.concatenate([[0.0], np.cumsum(seg_lens)])
        total_len = cum[-1]
        if total_len < 3.0 * badge_r:
            continue
        # Place 1 arrow at midpoint (or 2 for long lines)
        fracs = [0.5] if total_len < 0.7 * view_min else [0.35, 0.72]
        for fr in fracs:
            target = fr * total_len
            j = int(np.searchsorted(cum, target))
            j = max(1, min(len(pts) - 2, j))
            px_a, py_a = pts[j, 0], pts[j, 1]
            in_box = (xlim[0] + 0.04 * view_min < px_a < xlim[1] - 0.04 * view_min) and (
                ylim[0] + 0.04 * view_min < py_a < ylim[1] - 0.04 * view_min
            )
            far_q = all(math.hypot(px_a - cx, py_a - cy) > 2.0 * badge_r for _, cx, cy in charges)
            if in_box and far_q:
                draw_vector(
                    ax,
                    (float(pts[j - 1, 0]), float(pts[j - 1, 1])),
                    (float(pts[j + 1, 0]), float(pts[j + 1, 1])),
                    color=theme["teal"],
                    lw=1.35,
                    head_len=6.8,
                    head_w=3.8,
                    zorder=6,
                )

    # Mark detected null points inside viewport
    for np_xy in null_pts:
        nx, ny = np_xy
        if xlim[0] <= nx <= xlim[1] and ylim[0] <= ny <= ylim[1]:
            ax.plot(nx, ny, marker="D", ms=7.2, mfc=theme["bg"], mec=theme["accent"], mew=2.0, zorder=9)
            labels.add(
                nx,
                ny + 0.075 * view_min,
                rf"$\mathrm{{Null}}\ ({nx:.2f}, {ny:.2f})$",
                color=theme["accent"],
                fontsize=8.6,
            )

    # Draw crisp charge badges
    for q, cx, cy in charges:
        c_color = theme["danger"] if q > 0 else theme["primary"]
        ax.add_patch(Circle((cx, cy), badge_r, facecolor=c_color, edgecolor=theme["bg"], lw=2.0, zorder=10))
        q_lbl = f"+{q:g}" if q > 0 else f"{q:g}"
        ax.text(cx, cy, q_lbl, color="#FFFFFF", fontsize=9.2, fontweight="bold", ha="center", va="center", zorder=11)

    ax.set_xlim(xlim[0], xlim[1])
    ax.set_ylim(ylim[0], ylim[1])
    ax.set_aspect("equal", adjustable="box")
    ax.set_xlabel(r"$x$", fontsize=10.0)
    ax.set_ylabel(r"$y$", fontsize=10.0)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)

    null_txt = ", ".join(f"({p[0]:.2f}, {p[1]:.2f})" for p in null_pts) if null_pts else "None"
    eq_txt = "   |   Dashed: Equipotentials" if args.equipotentials else ""
    summary = f"Q_net = {net_q_str}   |   Dipole p = ({px:.2f}, {py:.2f}) (|p| = {p_mag:.2f})   |   Null E=0: {null_txt}{eq_txt}"
    add_readout_bar(fig, summary, theme)
    labels.resolve(fig)

    file_info = save_figure(fig, Path(args.out).expanduser().resolve(), args.dpi)
    return {
        "preset": "field-charges",
        "charges": [{"q": _round_opt(q), "x": _round_opt(x), "y": _round_opt(y)} for q, x, y in charges],
        "netCharge": _round_opt(net_q),
        "dipoleMoment": [_round_opt(px), _round_opt(py)],
        "dipoleMagnitude": _round_opt(p_mag),
        "nullPoints": null_pts,
        "equipotentials": bool(args.equipotentials),
        "xlim": [_round_opt(xlim[0]), _round_opt(xlim[1])],
        "ylim": [_round_opt(ylim[0]), _round_opt(ylim[1])],
        "theme": args.theme,
        **file_info,
    }


# ---------------------------------------------------------------------------
# 7. Projectile Trajectory (Level, Elevated, Up-Incline & Down-Incline)
# ---------------------------------------------------------------------------


def compute_projectile(v0: float, angle_deg: float, h0: float, g: float, incline_deg: float) -> dict[str, Any]:
    if v0 <= 0.0 or g <= 0.0:
        raise SystemExit("--v0 and --g must be > 0.")
    if h0 < 0.0:
        raise SystemExit("--h0 must be >= 0.")
    if not (-75.0 <= incline_deg <= 75.0):
        raise SystemExit("--incline must be in [-75, 75] degrees.")
    if not (-85.0 <= angle_deg <= 89.5):
        raise SystemExit("--angle must be in [-85, 89.5] degrees.")

    alpha = math.radians(angle_deg)
    beta = math.radians(incline_deg)
    vx0 = v0 * math.cos(alpha)
    vy0 = v0 * math.sin(alpha)
    tan_b = math.tan(beta)

    # Solve y(T) = h0 + vy0*T - 0.5*g*T^2 = (vx0*T)*tan(beta)
    # => 0.5*g*T^2 - (vy0 - vx0*tan_b)*T - h0 = 0
    b_eff = vy0 - vx0 * tan_b  # == v0 * sin(alpha - beta) / cos(beta)
    disc = b_eff * b_eff + 2.0 * g * h0
    if h0 == 0.0 and b_eff <= 1e-12:
        raise SystemExit(
            f"Launch angle ({angle_deg:g}°) must be strictly greater than incline angle ({incline_deg:g}°) when h0 = 0."
        )
    if disc < 0.0:
        raise SystemExit("Projectile does not intersect the target plane.")
    T = (b_eff + math.sqrt(disc)) / g
    if T <= 1e-12:
        raise SystemExit("Computed time of flight is non-positive; check launch/incline angles.")

    x_land = vx0 * T
    y_land = x_land * tan_b
    range_along = x_land / math.cos(beta) if abs(incline_deg) > 1e-12 else x_land

    # Vertical world apex
    t_apex_raw = vy0 / g
    has_interior_apex = 0.0 < t_apex_raw < T
    t_apex = max(0.0, min(T, t_apex_raw))
    x_apex = vx0 * t_apex
    y_apex = h0 + vy0 * t_apex - 0.5 * g * t_apex * t_apex

    # Perpendicular max height above incline (when beta != 0)
    # Distance perpendicular to plane y = x tan(beta): d_perp(t) = (y(t) - x(t) tan(beta)) * cos(beta)
    t_perp = max(0.0, min(T, b_eff / g))
    d_perp_max = (h0 + b_eff * t_perp - 0.5 * g * t_perp * t_perp) * math.cos(beta)

    vx_land = vx0
    vy_land = vy0 - g * T
    v_land = math.hypot(vx_land, vy_land)
    impact_angle_deg = math.degrees(math.atan2(vy_land, vx_land))

    incline_mode = "level" if abs(incline_deg) < 1e-12 else ("up_incline" if incline_deg > 0 else "down_incline")

    return {
        "preset": "projectile",
        "angleConvention": "angle (alpha) and incline (beta) both measured from horizontal (+beta up-incline, -beta down-incline)",
        "v0": _round_opt(v0),
        "angleDeg": _round_opt(angle_deg),
        "h0": _round_opt(h0),
        "g": _round_opt(g),
        "inclineDeg": _round_opt(incline_deg),
        "inclineMode": incline_mode,
        "timeOfFlight": _round_opt(T),
        "range": _round_opt(range_along),
        "horizontalRange": _round_opt(x_land),
        "landingPoint": [_round_opt(x_land), _round_opt(y_land)],
        "maxHeight": _round_opt(y_apex),
        "maxHeightPerpIncline": _round_opt(d_perp_max),
        "apexPoint": [_round_opt(x_apex), _round_opt(y_apex)],
        "apexTime": _round_opt(t_apex),
        "hasInteriorApex": has_interior_apex,
        "launchVelocity": [_round_opt(vx0), _round_opt(vy0)],
        "apexVelocity": [_round_opt(vx0), _round_opt(vy0 - g * t_apex)],
        "landingVelocity": [_round_opt(vx_land), _round_opt(vy_land)],
        "landingSpeed": _round_opt(v_land),
        "impactAngleDeg": _round_opt(impact_angle_deg),
    }


def render_projectile(args: argparse.Namespace) -> dict[str, Any]:
    data = compute_projectile(args.v0, args.angle, args.h0, args.g, args.incline)
    T = float(data["timeOfFlight"])
    x_land, y_land = data["landingPoint"]
    x_apex, y_apex = data["apexPoint"]
    beta = math.radians(args.incline)
    vx0, vy0 = data["launchVelocity"]
    vx_land, vy_land = data["landingVelocity"]

    mode_str = {
        "level": "Level Ground" if args.h0 == 0 else f"Elevated Launch ($h_0 = {args.h0:g}\\,\\mathrm{{m}}$)",
        "up_incline": f"Up Inclined Plane ($\\beta = +{args.incline:g}^\\circ$)",
        "down_incline": f"Down Inclined Plane ($\\beta = {args.incline:g}^\\circ$)",
    }[data["inclineMode"]]

    fig, ax, theme = make_canvas(
        args.theme,
        figsize=(9.8, 6.0),
        title=args.title or f"Projectile Trajectory — {mode_str}",
        subtitle=f"$v_0 = {args.v0:g}\\,\\mathrm{{m/s}},\\; \\alpha = {args.angle:g}^\\circ$ (from horizontal)$,\\; g = {args.g:g}\\,\\mathrm{{m/s^2}}$",
        ax_rect=(0.09, 0.17, 0.85, 0.70),
    )
    labels = LabelManager(ax, theme)

    ts = np.linspace(0.0, T, 260)
    xs = vx0 * ts
    ys = args.h0 + vy0 * ts - 0.5 * args.g * ts * ts

    ax.grid(True, color=theme["grid"], lw=0.75, alpha=0.8, zorder=0)
    ax.set_axisbelow(True)

    # Trajectory curve
    ax.plot(xs, ys, color=theme["primary"], lw=2.6, zorder=6)

    # Ground / Incline plane surface
    x_span = max(abs(x_land), 1.0)
    y_min_data = min(0.0, y_land, float(ys.min()))
    y_max_data = max(args.h0, y_apex, y_land, 1.0)
    y_span = max(y_max_data - y_min_data, 0.45 * x_span)

    x_left_ground = -0.10 * x_span
    x_right_ground = x_land + 0.16 * x_span
    if abs(args.incline) < 1e-12:
        draw_hatched_line(
            ax,
            (x_left_ground, 0.0),
            (x_right_ground, 0.0),
            theme,
            side=-1,
            spacing=0.045 * x_span,
            tick_len=0.032 * y_span,
        )
        if args.h0 > 0:
            # Draw launch cliff/platform cleanly
            cliff = Polygon(
                [(x_left_ground, 0.0), (0.0, 0.0), (0.0, args.h0), (x_left_ground, args.h0)],
                closed=True,
                facecolor=theme["surface"],
                edgecolor=theme["surface_edge"],
                lw=1.5,
                zorder=3,
            )
            ax.add_patch(cliff)
            labels.add(-0.05 * x_span, 0.5 * args.h0, rf"$h_0={args.h0:g}$", color=theme["muted"], fontsize=8.8)
    else:
        # Draw inclined wedge cleanly
        y_right_plane = x_right_ground * math.tan(beta)
        y_left_plane = x_left_ground * math.tan(beta)
        y_base = min(0.0, y_right_plane, y_left_plane) - 0.10 * y_span
        plane_poly = Polygon(
            [(x_left_ground, y_base), (x_right_ground, y_base), (x_right_ground, y_right_plane), (x_left_ground, y_left_plane)],
            closed=True,
            facecolor=theme["surface"],
            edgecolor="none",
            alpha=0.85,
            zorder=2,
        )
        ax.add_patch(plane_poly)
        ax.plot([x_left_ground, x_right_ground], [y_left_plane, y_right_plane], color=theme["surface_edge"], lw=2.1, zorder=4)
        # Horizontal dashed reference through launch base (0, 0)
        ax.plot([0.0, x_right_ground * 0.6], [0.0, 0.0], color=theme["muted"], lw=1.0, ls=":", zorder=3)
        # Incline angle beta arc
        b_r = 0.14 * x_span
        if args.incline > 0:
            mb = draw_angle_arc(ax, (0.0, 0.0), b_r, 0.0, args.incline, theme["accent"], lw=1.4, zorder=7)
        else:
            mb = draw_angle_arc(ax, (0.0, 0.0), b_r, args.incline, 0.0, theme["accent"], lw=1.4, zorder=7)
        labels.add((b_r + 0.055 * x_span) * mb[0], (b_r + 0.04 * x_span) * mb[1], rf"$\beta={args.incline:g}^\circ$", color=theme["accent"], fontsize=8.8)
        if args.h0 > 0:
            ax.plot([0.0, 0.0], [0.0, args.h0], color=theme["surface_edge"], lw=2.2, zorder=5)

    # Apex dashed guide & marker
    if data["hasInteriorApex"]:
        y_base_apex = x_apex * math.tan(beta)
        ax.plot([x_apex, x_apex], [y_base_apex if abs(args.incline) > 1e-12 else 0.0, y_apex], color=theme["muted"], lw=1.2, ls="--", zorder=4)
        ax.plot(x_apex, y_apex, "o", color=theme["accent"], ms=6.0, zorder=9)
        labels.add(
            x_apex,
            y_apex + 0.095 * y_span,
            rf"$\mathrm{{Apex}}\ H = {y_apex:.2f}\,\mathrm{{m}}$",
            color=theme["accent"],
            fontsize=9.0,
        )

    # Velocity vectors (scaled proportionally to visual span)
    v_scale = 0.18 * max(x_span, y_span) / max(args.v0, data["landingSpeed"], 1e-6)
    # Launch velocity v0
    p0 = (0.0, args.h0)
    v0_end = (vx0 * v_scale, args.h0 + vy0 * v_scale)
    draw_vector(ax, p0, v0_end, color=theme["success"], lw=2.3, zorder=9)
    ax.plot(0.0, args.h0, "o", color=theme["ink"], ms=5.5, zorder=10)
    labels.add(v0_end[0] + 0.02 * x_span, v0_end[1] + 0.055 * y_span, r"$\vec{v}_0$", color=theme["success"], fontsize=9.8)

    # Launch angle alpha arc from horizontal through launch point
    if abs(args.angle) > 2.5:
        ax.plot([0.0, 0.18 * x_span], [args.h0, args.h0], color=theme["muted"], lw=0.95, ls=":", zorder=4)
        a_r = 0.10 * x_span
        t_lo, t_hi = (0.0, args.angle) if args.angle >= 0 else (args.angle, 0.0)
        ma = draw_angle_arc(ax, p0, a_r, t_lo, t_hi, theme["success"], lw=1.3, zorder=7)
        labels.add(p0[0] + (a_r + 0.045 * x_span) * ma[0], p0[1] + (a_r + 0.045 * y_span) * ma[1], rf"$\alpha={args.angle:g}^\circ$", color=theme["success"], fontsize=8.8)

    # Apex velocity vector
    if data["hasInteriorApex"]:
        va_end = (x_apex + vx0 * v_scale, y_apex)
        draw_vector(ax, (x_apex, y_apex), va_end, color=theme["success"], lw=2.0, zorder=9)
        labels.add(va_end[0] + 0.04 * x_span, y_apex + 0.03 * y_span, rf"$v_x = {vx0:.1f}$", color=theme["success"], fontsize=8.5)

    # Landing velocity vector
    ax.plot(x_land, y_land, "o", color=theme["danger"], ms=6.2, zorder=10)
    vl_end = (x_land + vx_land * v_scale, y_land + vy_land * v_scale)
    draw_vector(ax, (x_land, y_land), vl_end, color=theme["danger"], lw=2.2, zorder=9)
    labels.add(
        vl_end[0] + 0.04 * x_span,
        vl_end[1] - 0.02 * y_span,
        rf"$\vec{{v}}_f\ ({data['landingSpeed']:.1f}\,\mathrm{{m/s}})$",
        color=theme["danger"],
        fontsize=8.8,
    )

    # Range dimension callout along level ground or along incline
    if abs(args.incline) < 1e-12:
        r_y = -0.11 * y_span
        ax.annotate(
            "",
            xy=(x_land, r_y),
            xytext=(0.0, r_y),
            arrowprops=dict(arrowstyle="<->", color=theme["purple"], lw=1.6),
            zorder=7,
        )
        labels.add(0.5 * x_land, r_y - 0.055 * y_span, rf"$R = {data['range']:.2f}\,\mathrm{{m}}$", color=theme["purple"], fontsize=9.4)
    else:
        # Parallel range callout slightly beneath the incline segment (0,0) -> (x_land, y_land)
        n_plane = np.array([math.sin(beta), -math.cos(beta)])  # outward into wedge
        off = 0.11 * y_span * n_plane
        p_r0 = off
        p_r1 = np.array([x_land, y_land]) + off
        ax.annotate(
            "",
            xy=(float(p_r1[0]), float(p_r1[1])),
            xytext=(float(p_r0[0]), float(p_r0[1])),
            arrowprops=dict(arrowstyle="<->", color=theme["purple"], lw=1.6),
            zorder=7,
        )
        mid_r = 0.5 * (p_r0 + p_r1) + 0.095 * y_span * n_plane
        labels.add(float(mid_r[0]), float(mid_r[1]), rf"$R_{{\mathrm{{incline}}}} = {data['range']:.2f}\,\mathrm{{m}}$", color=theme["purple"], fontsize=9.2)

    all_x = [x_left_ground, x_right_ground, vl_end[0], v0_end[0]]
    all_y = [y_min_data - 0.24 * y_span, y_max_data + 0.24 * y_span, vl_end[1] - 0.08 * y_span, v0_end[1] + 0.10 * y_span]
    ax.set_xlim(min(all_x) - 0.04 * x_span, max(all_x) + 0.08 * x_span)
    ax.set_ylim(min(all_y), max(all_y))
    # Use equal aspect with adjustable="box" (zero Matplotlib limit warnings) when ratio is balanced
    if 0.25 <= (max(all_y) - min(all_y)) / max(max(all_x) - min(all_x), 1e-6) <= 2.2:
        ax.set_aspect("equal", adjustable="box")
    ax.set_xlabel(r"$x\ (\mathrm{m})$", fontsize=10.0)
    ax.set_ylabel(r"$y\ (\mathrm{m})$", fontsize=10.0)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)

    perp_txt = f"   |   H_⊥ = {data['maxHeightPerpIncline']:.2f} m" if abs(args.incline) > 1e-12 else ""
    summary = (
        f"T = {T:.2f} s   |   R = {data['range']:.2f} m   |   H_max = {y_apex:.2f} m{perp_txt}   |   "
        f"|v_f| = {data['landingSpeed']:.2f} m/s ({data['impactAngleDeg']:.1f}°)"
    )
    add_readout_bar(fig, summary, theme)
    labels.resolve(fig)

    file_info = save_figure(fig, Path(args.out).expanduser().resolve(), args.dpi)
    data.update(file_info)
    data["theme"] = args.theme
    return data


# ---------------------------------------------------------------------------
# CLI Argument Normalization & Parser
# ---------------------------------------------------------------------------

_NEG_VALUE_FLAGS = {
    "--xlim",
    "--ylim",
    "--charges",
    "--u",
    "--f",
    "--R",
    "--incline",
    "--angle",
    "--applied-force",
    "--applied-angle",
    "--h",
    "--h0",
    "--i1",
}
_NEG_VAL_RE = re.compile(r"^-(?:\d|\.)")


def normalize_argv(argv: list[str]) -> list[str]:
    """Bind negative numeric/range tokens like `--xlim -4,4` into `--xlim=-4,4` so argparse never misparses them."""
    out: list[str] = []
    i = 0
    while i < len(argv):
        tok = argv[i]
        if tok in _NEG_VALUE_FLAGS and i + 1 < len(argv) and _NEG_VAL_RE.match(argv[i + 1]):
            out.append(f"{tok}={argv[i + 1]}")
            i += 2
        else:
            out.append(tok)
            i += 1
    return out


def add_common_args(subparser: argparse.ArgumentParser) -> None:
    subparser.add_argument("--out", required=True, help="Output file path (.svg or .png; format auto-detected)")
    subparser.add_argument("--theme", choices=["light", "dark"], default="light", help="Color theme (default: light)")
    subparser.add_argument("--dpi", type=int, default=220, help="PNG output resolution in DPI (default: 220)")
    subparser.add_argument("--title", default=None, help="Optional custom diagram title")
    subparser.add_argument("--json", action="store_true", help="Print full JSON metadata and computed physics quantities to stdout")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="physics_draw.py",
        description="Precision JEE Physics Diagram Engine (Matplotlib/SVG/PNG) with exact geometry and computed physics outputs.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    # 1. fbd-incline
    p1 = sub.add_parser(
        "fbd-incline",
        help="Free-body diagram on an inclined plane with exact proportional vectors and friction regime solver",
    )
    p1.add_argument("--angle", type=float, default=30.0, help="Incline angle theta in degrees (0, 85], default 30")
    p1.add_argument("--mass", type=float, default=2.0, help="Block mass in kg, default 2.0")
    p1.add_argument("--g", type=float, default=9.8, help="Gravitational acceleration in m/s^2, default 9.8")
    p1.add_argument("--mu", type=float, default=0.2, help="Coefficient of friction mu >= 0, default 0.2")
    p1.add_argument("--applied-force", type=float, default=0.0, help="External force F_app in N (+ up incline), default 0")
    p1.add_argument("--applied-angle", type=float, default=0.0, help="Angle of F_app relative to up-incline tangent in deg, default 0")
    p1.add_argument("--mode", choices=["auto", "static", "sliding-down", "sliding-up"], default="auto", help="Friction regime mode (default: auto)")
    p1.add_argument(
        "--show-components",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Draw dashed mg*sin(theta) and mg*cos(theta) components (default: true; pass --no-show-components to hide)",
    )
    add_common_args(p1)

    # 2. pulley
    p2 = sub.add_parser(
        "pulley",
        help="Pulley system diagram (atwood, table-pulley, incline-pulley) with exact tangent ropes, tension, and acceleration",
    )
    p2.add_argument("--preset", choices=["atwood", "incline-pulley", "table-pulley"], default="atwood", help="Pulley configuration preset")
    p2.add_argument("--m1", type=float, default=3.0, help="Mass m1 in kg (left / on surface), default 3.0")
    p2.add_argument("--m2", type=float, default=5.0, help="Mass m2 in kg (right / hanging), default 5.0")
    p2.add_argument("--angle", type=float, default=30.0, help="Incline angle in degrees for incline-pulley, default 30")
    p2.add_argument("--mu", type=float, default=0.0, help="Friction coefficient on m1 surface, default 0.0")
    p2.add_argument("--g", type=float, default=9.8, help="Gravitational acceleration in m/s^2, default 9.8")
    add_common_args(p2)

    # 3. optics-lens
    p3 = sub.add_parser(
        "optics-lens",
        help="Thin lens ray diagram using NCERT New Cartesian Sign Convention (1/v - 1/u = 1/f, m = v/u, real object u < 0)",
    )
    p3.add_argument("--f", type=float, default=15.0, help="Focal length in cm (+ converging, - diverging), default +15")
    p3.add_argument(
        "--u",
        type=float,
        default=-30.0,
        help="Object coordinate in cm (NCERT Cartesian: real object on left has u < 0; positive values auto-normalized to -|u| unless --virtual-object)",
    )
    p3.add_argument("--h", type=float, default=4.0, help="Object height in cm, default 4.0")
    p3.add_argument("--virtual-object", action="store_true", help="Allow positive u as a virtual object on the right")
    add_common_args(p3)

    # 4. optics-mirror
    p4 = sub.add_parser(
        "optics-mirror",
        help="Spherical mirror ray diagram using NCERT New Cartesian Sign Convention (1/v + 1/u = 1/f, m = -v/u; concave f < 0, convex f > 0)",
    )
    p4.add_argument("--type", choices=["concave", "convex"], default="concave", help="Mirror type (default: concave)")
    p4.add_argument("--f", type=float, default=None, help="Focal length in cm (sign normalized by mirror type: concave < 0, convex > 0)")
    p4.add_argument("--R", type=float, default=None, help="Radius of curvature in cm (overrides --f; f = R/2 with mirror-type sign)")
    p4.add_argument(
        "--u",
        type=float,
        default=-30.0,
        help="Object coordinate in cm (NCERT Cartesian: real object on left has u < 0; positive values auto-normalized to -|u| unless --virtual-object)",
    )
    p4.add_argument("--h", type=float, default=4.0, help="Object height in cm, default 4.0")
    p4.add_argument("--virtual-object", action="store_true", help="Allow positive u as a virtual object")
    add_common_args(p4)

    # 5. optics-prism
    p5 = sub.add_parser(
        "optics-prism",
        help="Prism Snell's law ray tracing with exact minimum deviation and Total Internal Reflection (TIR) handling",
    )
    p5.add_argument("--A", type=float, default=60.0, help="Prism apex angle A in degrees, default 60")
    p5.add_argument("--n", type=float, default=1.5, help="Prism refractive index n > 1, default 1.5")
    p5.add_argument("--i1", type=float, default=None, help="Angle of incidence at face 1 in degrees [0, 90), default 45")
    p5.add_argument("--min-dev", action="store_true", help="Use exact minimum-deviation incidence i1 = arcsin(n*sin(A/2))")
    add_common_args(p5)

    # 6. field-charges
    p6 = sub.add_parser(
        "field-charges",
        help="2D electric field lines (RK4 symmetric seeding), equipotential contours, dipole moment & null points",
    )
    p6.add_argument("--charges", default="1,-1.5,0;-1,1.5,0", help='Semicolon-separated "q,x,y" list, e.g. "1,-1.5,0;-1,1.5,0"')
    p6.add_argument(
        "--equipotentials",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Draw dashed equipotential contours (default: true; pass --no-equipotentials to hide)",
    )
    p6.add_argument("--xlim", default=None, help='X-axis limits as "xmin,xmax" (auto-fit if omitted)')
    p6.add_argument("--ylim", default=None, help='Y-axis limits as "ymin,ymax" (auto-fit if omitted)')
    add_common_args(p6)

    # 7. projectile
    p7 = sub.add_parser(
        "projectile",
        help="Projectile motion trajectory (level, elevated, up-incline, or down-incline); --angle & --incline measured from horizontal",
    )
    p7.add_argument("--v0", type=float, default=20.0, help="Launch speed in m/s, default 20.0")
    p7.add_argument("--angle", type=float, default=45.0, help="Launch angle alpha measured from horizontal in degrees, default 45")
    p7.add_argument("--h0", type=float, default=0.0, help="Initial launch height above origin in m, default 0.0")
    p7.add_argument("--g", type=float, default=9.8, help="Gravitational acceleration in m/s^2, default 9.8")
    p7.add_argument(
        "--incline",
        type=float,
        default=0.0,
        help="Inclined plane angle beta from horizontal in degrees (+ up-incline, - down-incline, 0 level), default 0",
    )
    add_common_args(p7)

    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    raw_argv = sys.argv[1:] if argv is None else argv
    args = parser.parse_args(normalize_argv(raw_argv))

    handlers = {
        "fbd-incline": render_fbd_incline,
        "pulley": render_pulley,
        "optics-lens": render_optics_lens,
        "optics-mirror": render_optics_mirror,
        "optics-prism": render_optics_prism,
        "field-charges": render_field_charges,
        "projectile": render_projectile,
    }
    result = handlers[args.cmd](args)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"Saved {result['preset']} ({result['theme']}, {result['format'].upper()}, {result['bytes']} B) -> {result['output']}")


if __name__ == "__main__":
    main()
