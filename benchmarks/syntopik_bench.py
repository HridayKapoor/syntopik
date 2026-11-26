#!/usr/bin/env python3
"""Syntopik-Eval Reproducible CLI & CI Regression Gate.

Runs Syntopik's deterministic 60-day multi-persona cognitive ablation simulator
(`syntopik.sim_eval`), prints GitHub-ready Markdown tables or JSON, writes
pure-stdlib publication SVG charts to `docs/assets/`, and enforces strict
non-regression CI gates (`--check`).

Pure Python 3.12+ standard library only (zero external dependencies).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time
import xml.etree.ElementTree as ET
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from syntopik.sim_eval import (  # noqa: E402
    format_markdown_table,
    render_ablation_svg,
    render_calibration_svg,
    run_simulation,
)


def _validate_svg_xml(svg_text: str, label: str) -> dict[str, Any]:
    """Parse SVG as XML and assert valid root/viewBox and in-bounds text anchors."""
    root = ET.fromstring(svg_text)
    if not root.tag.endswith("svg"):
        raise ValueError(f"{label}: root tag is {root.tag!r}, expected <svg>")
    vb = root.attrib.get("viewBox", "")
    parts = [float(x) for x in vb.split()]
    if len(parts) != 4:
        raise ValueError(f"{label}: invalid viewBox {vb!r}")
    _x0, _y0, vw, vh = parts
    out_of_bounds = 0
    text_nodes = 0
    for elem in root.iter():
        if elem.tag.endswith("text"):
            text_nodes += 1
            tx = float(elem.attrib.get("x", "0"))
            ty = float(elem.attrib.get("y", "0"))
            if tx < 0 or tx > vw or ty < 0 or ty > vh:
                out_of_bounds += 1
    if out_of_bounds > 0:
        raise ValueError(f"{label}: {out_of_bounds} text element(s) outside viewBox {vb!r}")
    return {
        "validXml": True,
        "viewBox": vb,
        "bytes": len(svg_text.encode("utf-8")),
        "textElements": text_nodes,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="syntopik_bench.py",
        description="Syntopik-Eval: Deterministic 60-Day Multi-Persona Cognitive Benchmark & CI Gate",
    )
    parser.add_argument("--days", type=int, default=60, help="Simulated horizon in days (default: 60)")
    parser.add_argument("--seed", type=int, default=42, help="Deterministic RNG seed (default: 42)")
    parser.add_argument(
        "--svg",
        action="store_true",
        help="Write publication SVGs to docs/assets/benchmark-{ablation,calibration}.svg",
    )
    parser.add_argument(
        "--svg-dir",
        type=Path,
        default=None,
        help="Override output directory for SVG charts (default: docs/assets)",
    )
    parser.add_argument("--markdown", action="store_true", help="Print GitHub-flavored Markdown ablation table")
    parser.add_argument("--json", action="store_true", help="Emit full structured JSON output")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Assert strict deterministic CI regression gates (exit non-zero on failure)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    t0 = time.perf_counter()
    results = run_simulation(days=args.days, seed=args.seed)
    elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 1)
    results["elapsedMs"] = elapsed_ms

    svg_info: dict[str, Any] = {}
    if args.svg:
        out_dir = (args.svg_dir or (ROOT / "docs" / "assets")).expanduser().resolve()
        out_dir.mkdir(parents=True, exist_ok=True)
        ablation_path = out_dir / "benchmark-ablation.svg"
        calibration_path = out_dir / "benchmark-calibration.svg"

        ablation_svg = render_ablation_svg(results)
        calibration_svg = render_calibration_svg(results)

        ab_meta = _validate_svg_xml(ablation_svg, ablation_path.name)
        cal_meta = _validate_svg_xml(calibration_svg, calibration_path.name)

        ablation_path.write_text(ablation_svg, encoding="utf-8")
        calibration_path.write_text(calibration_svg, encoding="utf-8")
        svg_info = {
            "ablation": {"path": str(ablation_path), **ab_meta},
            "calibration": {"path": str(calibration_path), **cal_meta},
        }
        results["svgOutputs"] = svg_info

    gates = results["gates"]

    if args.json:
        print(json.dumps(results, indent=2, ensure_ascii=False))
    elif args.markdown:
        print(format_markdown_table(results))
    else:
        print(format_markdown_table(results))
        print(f"\nElapsed: {elapsed_ms} ms | Gates: {gates['passed']}/{gates['total']} ({'PASS' if gates['ok'] else 'FAIL'})")
        for chk in gates["checks"]:
            mark = "PASS" if chk["passed"] else "FAIL"
            print(f"  [{mark}] {chk['id']}: actual={chk['actual']} (threshold={chk['threshold']}) — {chk['description']}")
        if svg_info:
            for k, v in svg_info.items():
                print(f"  [SVG] {k}: {v['path']} ({v['bytes']} B, viewBox={v['viewBox']}, texts={v['textElements']})")

    if args.check and not gates["ok"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
