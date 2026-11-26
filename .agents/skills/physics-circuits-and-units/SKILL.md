---
name: physics-circuits-and-units
description: >-
  Render textbook-grade Physics & Electrical Circuit schematics (RC, RL, LCR,
  Wheatstone bridges, Kirchhoff loops, logic gates) with Schemdraw, exact
  parametric physics diagrams (FBDs, pulleys, ray optics lenses/mirrors/prisms,
  E-fields, projectiles) with scripts/physics_draw.py, and deterministically
  verify algebra, calculus, ODEs, error propagation, and units with SymPy + Pint.
---

# Physics Diagrams, Circuits, Units & Deterministic Verification

Never draw ASCII circuits, eyeball ray angles or force vectors, or guess SI/CGS conversions. Verify every physics calculation and figure deterministically across any harness.

---

## 1. Textbook-Grade Circuit & Digital Logic Schematics (`scripts/circuit_draw.py`)

- **Quick presets (`rc`, `rl`, `lcr`, `wheatstone`, `divider`, `kirchhoff`)**:
  ```bash
  .venv/bin/python -B scripts/circuit_draw.py preset wheatstone --dark --out demos/wheatstone.png
  ```
- **Declarative JSON spec** (`scripts/circuit_draw.py spec <file.json> --out demos/<slug>.png --dark`) supporting multi-loop bridges, switches, logic gates (`And`/`Or`/`Not`/`Nand`/`Nor`/`Xor`/`Xnor`), transistors, meters, and current arrows.
- **Mandatory visual check**: always inspect generated PNGs via `read_file` / `Read` to verify crisp labels and zero overlapping nodes before presenting.

---

## 2. Precision Parametric Physics Diagrams (`scripts/physics_draw.py`)

Use `scripts/physics_draw.py` whenever Mechanics, Ray Optics, or Electrostatics requires exact angles, focal lengths, vector proportions, or Total Internal Reflection (`--out <path.svg|path.png>`, `--theme light|dark`, `--dpi 220`, `--json`):

```bash
# Free-Body Diagram on incline (computes N, friction, netForce, acceleration, regime):
.venv/bin/python -B scripts/physics_draw.py fbd-incline --angle 30 --mass 2 --mu 0.2 --show-components --theme dark --out demos/fbd-incline.png --json

# Pulley systems (atwood | incline-pulley | table-pulley):
.venv/bin/python -B scripts/physics_draw.py pulley --preset incline-pulley --m1 4 --m2 2 --angle 30 --mu 0.1 --out demos/pulley.png --json

# Ray Optics — Thin Lens (Cartesian sign convention: object left -> u < 0; handles u == -f):
.venv/bin/python -B scripts/physics_draw.py optics-lens --f 15 --u -30 --h 4 --out demos/lens.png --json

# Ray Optics — Spherical Mirror (concave | convex; handles u == f cleanly):
.venv/bin/python -B scripts/physics_draw.py optics-mirror --type concave --f -15 --u -25 --h 4 --out demos/mirror.png --json

# Ray Optics — Prism (exact Snell's law, --min-dev, automatic TIR when r2 > theta_c):
.venv/bin/python -B scripts/physics_draw.py optics-prism --A 60 --n 1.5 --min-dev --out demos/prism.png --json

# 2D Electric Field Lines + Equipotentials:
.venv/bin/python -B scripts/physics_draw.py field-charges --charges "1,-1.5,0;-1,1.5,0" --equipotentials --out demos/dipole.png --json

# Projectile Motion (level ground or inclined plane --incline beta):
.venv/bin/python -B scripts/physics_draw.py projectile --v0 20 --angle 45 --incline 15 --out demos/projectile.png --json
```
- **Mandatory visual check**: inspect the rendered PNG via `read_file` / `Read` before presenting (and embed inline via `visual-and-demo` when teaching).

---

## 3. Deterministic Symbolic Math, ODEs, Error Propagation & Units (`scripts/math_verify.py`)

```bash
.venv/bin/python -B scripts/math_verify.py eval "integrate(x*exp(-x), (x, 0, oo))"
.venv/bin/python -B scripts/math_verify.py check-eq "sin(2*x)/(1 + cos(2*x))" "tan(x)"
.venv/bin/python -B scripts/math_verify.py small-osc "U0*(1 - cos(alpha*x))" --x0 0
.venv/bin/python -B scripts/math_verify.py units "1 atm * L" --to "J"
.venv/bin/python -B scripts/math_verify.py check-dim "sqrt(k/m)" --defs "k=N/m,m=kg" --expect "1/s"
.venv/bin/python -B scripts/math_verify.py error-prop "g = 4*pi**2*L/T**2" --vals "L=1.00+-0.01,T=2.00+-0.02"
```
