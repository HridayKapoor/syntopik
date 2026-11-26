#!/usr/bin/env python3
"""Publication-Grade Physics & Electrical Circuit Generator (Schemdraw).

Renders crisp vector SVG/PNG schematics for JEE Physics circuits (RC, RL, LCR,
Wheatstone bridges, Kirchhoff loops, capacitor networks, diodes, logic gates).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import schemdraw
import schemdraw.elements as elm
from schemdraw import logic


def configure_theme(dark: bool) -> None:
    if dark:
        schemdraw.theme("dark")
    else:
        schemdraw.theme("default")


def draw_preset(preset: str, out_path: Path, dark: bool) -> None:
    configure_theme(dark)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with schemdraw.Drawing(file=str(out_path), show=False) as d:
        d.config(unit=3.0, fontsize=12)
        if preset == "rc":
            d += elm.SourceV().up().label(r"$\mathcal{E}$")
            d += elm.Switch().right().label("$S$")
            d += elm.Resistor().right().label("$R$")
            d += elm.Capacitor().down().label("$C$")
            d += elm.Line().left()
            d += elm.Line().left()
        elif preset == "rl":
            d += elm.SourceV().up().label(r"$\mathcal{E}$")
            d += elm.Switch().right().label("$S$")
            d += elm.Resistor().right().label("$R$")
            d += elm.Inductor2().down().label("$L$")
            d += elm.Line().left()
            d += elm.Line().left()
        elif preset == "lcr":
            d += elm.SourceSin().up().label(r"$V_0\sin(\omega t)$")
            d += elm.Resistor().right().label("$R$")
            d += elm.Inductor2().right().label("$L$")
            d += elm.Capacitor().down().label("$C$")
            d += elm.Line().left()
            d += elm.Line().left()
        elif preset == "wheatstone":
            top = d.here
            r1 = d.add(elm.Resistor().at(top).theta(-45).label("$R_1$", loc="top"))
            r2 = d.add(elm.Resistor().at(top).theta(-135).label("$R_2$", loc="top"))
            d.add(elm.MeterA().endpoints(r2.end, r1.end).label("$G$"))
            r3 = d.add(elm.Resistor().at(r2.end).theta(-45).label("$R_3$", loc="bottom"))
            d.add(elm.Resistor().endpoints(r1.end, r3.end).label("$R_4$", loc="bottom"))
            d.add(elm.Dot().at(top).label("$A$", loc="top"))
            d.add(elm.Dot().at(r3.end).label("$B$", loc="bottom"))
        elif preset == "divider":
            d += elm.SourceV().up(6).label("$V_{in}$")
            d += elm.Line().right()
            d += elm.Resistor().down().label("$R_1$")
            d += elm.Dot()
            d.push()
            d += elm.Line().right(1.5).label("$V_{out}$", loc="right")
            d.pop()
            d += elm.Resistor().down().label("$R_2$")
            d += elm.Line().left()
        elif preset == "kirchhoff":
            d += elm.SourceV().up().label(r"$\mathcal{E}_1$")
            d += elm.Resistor().right().label("$R_1$")
            d += elm.Dot()
            d.push()
            d += elm.Resistor().down().label("$R_3$")
            d += elm.Dot()
            d.pop()
            d += elm.Resistor().right().label("$R_2$")
            d += elm.SourceV().down().reverse().label(r"$\mathcal{E}_2$", loc="bottom")
            d += elm.Line().left()
            d += elm.Line().left()
        else:
            raise SystemExit(f"Unknown preset: {preset}")


def _resolve_anchor(ref: Any, named: dict[str, Any]) -> Any:
    if isinstance(ref, (list, tuple)) and len(ref) == 2:
        return (float(ref[0]), float(ref[1]))
    if isinstance(ref, str):
        if "." in ref:
            name, attr = ref.split(".", 1)
            if name in named:
                return getattr(named[name], attr)
        elif ref in named:
            return named[ref].end
    raise SystemExit(f"Unknown anchor reference: {ref!r}")


def draw_from_json(spec_path: Path, out_path: Path, dark: bool) -> None:
    """Render a declarative list of circuit elements from JSON.

    Supports standard circuit elements, logic gates, push/pop, and named anchors
    (id / at / to / endpoints).
    """
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    configure_theme(dark)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    allowed_elm = {
        "Resistor", "Capacitor", "Capacitor2", "Inductor", "Inductor2",
        "SourceV", "SourceI", "SourceSin", "SourceControlledV", "SourceControlledI",
        "Battery", "BatteryCell", "Switch", "SwitchSpdt", "SwitchSpdt2",
        "Diode", "LED", "Zener", "Fuse", "Ground", "Line", "Dot",
        "MeterA", "MeterV", "Lamp", "Opamp", "BjtNpn", "BjtPnp", "NFet", "PFet",
    }
    allowed_logic = {"And", "Or", "Not", "Nand", "Nor", "Xor", "Xnor"}

    named: dict[str, Any] = {}
    with schemdraw.Drawing(file=str(out_path), show=False) as d:
        d.config(unit=float(spec.get("unit", 3.0)), fontsize=int(spec.get("fontsize", 12)))
        for item in spec.get("elements", []):
            if item.get("op") == "push":
                d.push()
                continue
            if item.get("op") == "pop":
                d.pop()
                continue
            etype = item["type"]
            if etype in allowed_elm:
                cls = getattr(elm, etype)
            elif etype in allowed_logic:
                cls = getattr(logic, etype)
            else:
                raise SystemExit(f"Unsupported element type: {etype}")
            comp = cls()
            if "at" in item:
                comp = comp.at(_resolve_anchor(item["at"], named))
            if "endpoints" in item:
                ep = item["endpoints"]
                if not isinstance(ep, list) or len(ep) != 2:
                    raise SystemExit("endpoints must be a 2-element list [start, end]")
                comp = comp.endpoints(_resolve_anchor(ep[0], named), _resolve_anchor(ep[1], named))
            else:
                direction = item.get("dir")
                length = item.get("length")
                if direction in ("up", "down", "left", "right"):
                    comp = getattr(comp, direction)(length) if length is not None else getattr(comp, direction)()
                if "theta" in item:
                    comp = comp.theta(float(item["theta"]))
                if "to" in item:
                    comp = comp.to(_resolve_anchor(item["to"], named))
            if item.get("reverse"):
                comp = comp.reverse()
            if "color" in item:
                comp = comp.color(str(item["color"]))
            if "label" in item:
                loc = item.get("loc")
                comp = comp.label(str(item["label"]), loc=loc) if loc else comp.label(str(item["label"]))
            added = d.add(comp)
            if "id" in item:
                named[str(item["id"])] = added


def main() -> None:
    parser = argparse.ArgumentParser(description="Physics & Electrical Circuit Diagram Generator (Schemdraw)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p1 = sub.add_parser("preset", help="Render a standard JEE circuit preset (rc, rl, lcr, wheatstone, divider, kirchhoff)")
    p1.add_argument("name", choices=["rc", "rl", "lcr", "wheatstone", "divider", "kirchhoff"])
    p1.add_argument("--out", required=True)
    p1.add_argument("--dark", action="store_true")

    p2 = sub.add_parser("spec", help="Render a custom circuit from a JSON element spec")
    p2.add_argument("json_file")
    p2.add_argument("--out", required=True)
    p2.add_argument("--dark", action="store_true")

    args = parser.parse_args()
    out_path = Path(args.out).expanduser().resolve()
    if args.cmd == "preset":
        draw_preset(args.name, out_path, args.dark)
    else:
        draw_from_json(Path(args.json_file).expanduser().resolve(), out_path, args.dark)
    print(json.dumps({"output": str(out_path)}, indent=2))


if __name__ == "__main__":
    main()
