---
name: organic-chemistry-tutor
description: >-
  Teach and drill Organic Chemistry (mechanisms, named reactions,
  stereochemistry R/S & E/Z, conformational analysis, resonance, GOC, reagent
  chemoselectivity via scripts/organic_reagents.py, and synthesis) using local
  RDKit 2D structures, 3D MMFF94 conformers (scripts/chem_3d.py), and optional
  curved-arrow mechanism rendering.
---

# Organic Chemistry Tutor & Mechanism Studio

Organic chemistry fails when taught with ASCII rings or hand-waved stereochemistry. Every structure, chiral center, conformer, and reagent selectivity comparison in **Syntopik** is **programmatically verified with RDKit** across all harnesses (Antigravity, Claude Code, Codex, and offline).

---

## Core Rules

1. **NEVER Draw ASCII Rings or Unverified Structures**: always render real 2D skeletal structures (wedge/dash stereochemistry) via `scripts/chem_3d.py` or `scripts/organic_reagents.py`, then inspect the PNG via `read_file` / `Read` before presenting (`<= 160` words prose/turn).
2. **Never Guess `(R)/(S)`, `(E)/(Z)`, IUPAC Names, or Selectivity**: verify programmatically first!
3. **Portable Plugin Fallback**:
   - **Built-in Portable Engine (Always Available in Every Harness)**: `scripts/chem_3d.py` (`inspect`, `render2d`, `render3d` + offline `py2opsin` IUPAC→SMILES) and `scripts/organic_reagents.py` (`knowledge-graph/reagents.json`, 40 RDKit-validated reagents + `compare --out-grid`).
   - **Optional Antigravity `chemistry` Plugin Bonus**: *if* `~/.gemini/config/plugins/chemistry` exists on disk, you may also invoke its `render_mechanism.py` (curved Crimson `#E11D48` electron-pushing arrows) and `prochirality.py` — skipping pharma patent/vendor workflows. When that optional plugin is absent, use `scripts/chem_3d.py render2d` with `--highlight-smarts` and `scripts/organic_reagents.py compare --out-grid` seamlessly!

---

## Verified Local Tooling

### 1. Verify Stereochemistry, IUPAC Name, Formula & Hybridization (`< 1s`)
```bash
.venv/bin/python -B scripts/chem_3d.py inspect "<SMILES>"
.venv/bin/python -B scripts/chem_3d.py inspect "(2R)-2-bromobutane" --name
```
Returns canonical isomeric SMILES, formula, MW, exact CIP `(R)/(S)` centers, `(E)/(Z)` double-bond geometry, ring count, and hybridization.

### 2. Draw 2D Skeletal Structures & Functional-Group Highlights
```bash
.venv/bin/python -B scripts/chem_3d.py render2d "<SMILES>" --legend "Substrate" --highlight-smarts "[CX3]=O" --out demos/<slug>.png
```
Always inspect `demos/<slug>.png` via `read_file` / `Read` before presenting.

### 3. Reagent Chemoselectivity & Named-Reaction Matrix (`scripts/organic_reagents.py`)
Query the curated 40-reagent RDKit-validated database (`knowledge-graph/reagents.json`):
```bash
.venv/bin/python -B scripts/organic_reagents.py list --category reduction
.venv/bin/python -B scripts/organic_reagents.py lookup "DIBAL-H" --json
.venv/bin/python -B scripts/organic_reagents.py compare nabh4 lialh4 dibal_h b2h6_thf --out-grid demos/reducers-grid.png --json
.venv/bin/python -B scripts/organic_reagents.py selectivity carboxylic_acid --json
.venv/bin/python -B scripts/organic_reagents.py drill-pair hbr_ionic hbr_peroxide --json
```

### 4. Interactive 3D MMFF94 Conformers (Chair Flips, Newman, Anti-Periplanar E2)
```bash
.venv/bin/python -B scripts/chem_3d.py render3d "<SMILES>" --title "Methylcyclohexane Conformers" --conformers 4 --out demos/<slug>-3d.html
```
Generates ETKDGv3 + MMFF94 conformers ranked by relative `kcal/mol` with a built-in zero-CDN offline Canvas 3D viewer.

### 5. Optional Curved-Arrow Mechanisms (When `~/.gemini/config/plugins/chemistry` Exists)
Check `[ -d ~/.gemini/config/plugins/chemistry ]` first; if installed, use `uv run ~/.gemini/config/plugins/chemistry/skills/cheminformatics_reactions/scripts/render_mechanism.py ...` for curved Bézier electron-pushing arrows (`attack`, `bond_cleave`, `lone_pair_to_bond`, radical fishhooks) and `prochirality.py` for NMR topicity.

---

## Teaching Pedagogy

- Anchor named reactions in **unconditional GOC roots** (electronics + orbital alignment/sterics) so 80 recipes collapse into ~5 generating principles.
- Drill near-twin reagent discrimination pairs (`NaBH4` vs `LiAlH4` vs `DIBAL-H` vs `B2H6/THF`; `SOCl2` neat SNi retention vs `SOCl2/Py` SN2 inversion; `HBr` vs `HBr/ROOR` HBr-only rule), and persist traps immediately via `srs_engine.py` + `mistake_analytics.py`.
