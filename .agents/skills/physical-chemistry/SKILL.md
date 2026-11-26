---
name: physical-chemistry
description: >-
  Deterministically solve and verify Physical & Inorganic Chemistry (university
  and JEE Main/Advanced): chemical & ionic redox balancing (acidic/basic),
  stoichiometry & limiting reagent, equilibrium Kp/Kc & ICE tables, exact
  charge-balance pH/buffers/Ksp, Nernst equation & Faraday electrolysis,
  thermodynamics, Arrhenius kinetics, and periodic trends via scripts/chem_physical.py.
---

# Physical & Inorganic Chemistry Verification Engine

Never eyeball redox coefficients, dilute-acid pH traps ($10^{-8}\,\text{M}$ HCl is **pH 6.98**, never 8.0!), kJ-vs-J $\Delta G = \Delta H - T\Delta S$ conversions, or anomalous $d$-block configurations (`Cr`, `Cu`, `Mo`, `Pd`). Verify every quantitative step deterministically with `.venv/bin/python -B scripts/chem_physical.py`.

---

## Core CLI Commands (`scripts/chem_physical.py`)

### 1. Chemical & Ionic Redox Balancing (`balance`)
```bash
.venv/bin/python -B scripts/chem_physical.py balance "KMnO4 + FeSO4 + H2SO4 -> K2SO4 + MnSO4 + Fe2(SO4)3 + H2O"
.venv/bin/python -B scripts/chem_physical.py balance "MnO4- + Fe2+ -> Mn2+ + Fe3+" --medium acidic
.venv/bin/python -B scripts/chem_physical.py balance "Cr(OH)3 + IO3- -> CrO4^2- + I-" --medium basic
```

### 2. Stoichiometry, Limiting Reagent & Formula Composition (`stoich`, `formula`)
```bash
.venv/bin/python -B scripts/chem_physical.py stoich "Fe + O2 -> Fe2O3" --given "Fe=10g, O2=5g" --yield-pct 85
.venv/bin/python -B scripts/chem_physical.py stoich "H2SO4 + NaOH -> Na2SO4 + H2O" --given "H2SO4=25mL@0.2M, NaOH=40mL@0.2M"
.venv/bin/python -B scripts/chem_physical.py formula "K2Cr2O7"
```

### 3. Chemical Equilibrium & Exact ICE Tables (`equil`)
```bash
.venv/bin/python -B scripts/chem_physical.py equil --kc 4.0 --delta-ng 1 --temp 500
.venv/bin/python -B scripts/chem_physical.py equil --reaction "H2 + I2 -> 2 HI" --kc 54.3 --init "H2=0.5, I2=0.5, HI=0" --volume 1.0
```

### 4. Exact Aqueous pH, Buffers & Solubility $K_{sp}$ (`ph`)
Uses exact charge-balance root-finding (`scipy.optimize.brentq`) — zero naive approximation failures:
```bash
.venv/bin/python -B scripts/chem_physical.py ph strong-acid --conc 1e-8
.venv/bin/python -B scripts/chem_physical.py ph weak-acid --conc 0.01 --ka 1.8e-5
.venv/bin/python -B scripts/chem_physical.py ph buffer --acid-conc 0.10 --base-conc 0.15 --pka 4.74
.venv/bin/python -B scripts/chem_physical.py ph ksp --ksp 1.8e-10 --m 1 --n 1 --common-ion 0.01
```

### 5. Electrochemistry: Nernst Equation & Faraday Electrolysis (`nernst`)
```bash
.venv/bin/python -B scripts/chem_physical.py nernst --cathode "Cu2+/Cu" --anode "Zn2+/Zn" --q 0.01 --temp 298.15
.venv/bin/python -B scripts/chem_physical.py nernst --current 2.0 --time 1800 --n 2 --molar-mass 63.546
```

### 6. Thermodynamics & Chemical Kinetics (`thermo`)
```bash
.venv/bin/python -B scripts/chem_physical.py thermo gibbs --dh -92.4 --ds -198.3 --temp 298.15
.venv/bin/python -B scripts/chem_physical.py thermo arrhenius --t1 300 --t2 310 --k1 0.01 --ea 52.9
.venv/bin/python -B scripts/chem_physical.py thermo vant-hoff --t1 298 --t2 350 --k1 1.5 --dh 40.0
.venv/bin/python -B scripts/chem_physical.py thermo kinetics --order 1 --k1 0.0693 --a0 1.0 --time 20
```

### 7. Exact Periodic Table & Inorganic Trends (`element`)
```bash
.venv/bin/python -B scripts/chem_physical.py element Cr
.venv/bin/python -B scripts/chem_physical.py element C N O F
```

---

## Calm Teaching Rules

- Pre-commit numeric solutions via `scripts/chem_physical.py` (and `scripts/math_verify.py units` for $\text{atm}\cdot\text{L} \leftrightarrow \text{J} \leftrightarrow \text{cal}$) before posing or grading any problem (`<= 160` words prose/turn).
- Spotlight classic traps: $K_w$ autoionization at $C \le 10^{-6}\,\text{M}$, 5% weak-acid breakdown, $\Delta H$ (kJ) vs $\Delta S$ (J), $p^3$/$d^5$ IE anomalies ($N > O$, $Be > B$, $Cr$, $Cu$), and $E^\circ$ being intensive (unlike $\Delta G^\circ = -nFE^\circ$).
- Persist `contrasts_with` edges and schedule reviews in real time via `scripts/srs_engine.py`.
