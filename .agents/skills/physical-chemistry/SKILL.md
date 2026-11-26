---
name: physical-chemistry
description: >-
  Deterministically solve and verify Physical and Inorganic Chemistry: ionic
  redox balancing, stoichiometry, ICE equilibria, exact charge-balance pH and
  Ksp, Nernst, thermodynamics, and kinetics.
---

# Physical & Inorganic Chemistry Verification Engine

Never eyeball redox coefficients, dilute-acid pH traps ($10^{-8}\,\text{M}$ HCl is **pH 6.98**, never 8.0!), kJ-vs-J $\Delta G = \Delta H - T\Delta S$ conversions, or anomalous $d$-block configurations (`Cr`, `Cu`, `Mo`, `Pd`). Verify every quantitative step deterministically with `syntopik run chem_physical ...` (or `.venv/bin/python -B scripts/chem_physical.py ...` in direct clones).

---

## Core CLI Commands (`scripts/chem_physical.py`)

### 1. Chemical & Ionic Redox Balancing (`balance`)
```bash
syntopik run chem_physical balance "KMnO4 + FeSO4 + H2SO4 -> K2SO4 + MnSO4 + Fe2(SO4)3 + H2O"
syntopik run chem_physical balance "MnO4- + Fe2+ -> Mn2+ + Fe3+" --medium acidic
syntopik run chem_physical balance "Cr(OH)3 + IO3- -> CrO4^2- + I-" --medium basic
```

### 2. Stoichiometry, Limiting Reagent & Formula Composition (`stoich`, `formula`)
```bash
syntopik run chem_physical stoich "Fe + O2 -> Fe2O3" --given "Fe=10g, O2=5g" --yield-pct 85
syntopik run chem_physical stoich "H2SO4 + NaOH -> Na2SO4 + H2O" --given "H2SO4=25mL@0.2M, NaOH=40mL@0.2M"
syntopik run chem_physical formula "K2Cr2O7"
```

### 3. Chemical Equilibrium & Exact ICE Tables (`equil`)
```bash
syntopik run chem_physical equil --kc 4.0 --delta-ng 1 --temp 500
syntopik run chem_physical equil --reaction "H2 + I2 -> 2 HI" --kc 54.3 --init "H2=0.5, I2=0.5, HI=0" --volume 1.0
```

### 4. Exact Aqueous pH, Buffers & Solubility $K_{sp}$ (`ph`)
Uses exact charge-balance root-finding (`scipy.optimize.brentq`) — zero naive approximation failures:
```bash
syntopik run chem_physical ph strong-acid --conc 1e-8
syntopik run chem_physical ph weak-acid --conc 0.01 --ka 1.8e-5
syntopik run chem_physical ph buffer --acid-conc 0.10 --base-conc 0.15 --pka 4.74
syntopik run chem_physical ph ksp --ksp 1.8e-10 --m 1 --n 1 --common-ion 0.01
```

### 5. Electrochemistry: Nernst Equation & Faraday Electrolysis (`nernst`)
```bash
syntopik run chem_physical nernst --cathode "Cu2+/Cu" --anode "Zn2+/Zn" --q 0.01 --temp 298.15
syntopik run chem_physical nernst --current 2.0 --time 1800 --n 2 --molar-mass 63.546
```

### 6. Thermodynamics & Chemical Kinetics (`thermo`)
```bash
syntopik run chem_physical thermo gibbs --dh -92.4 --ds -198.3 --temp 298.15
syntopik run chem_physical thermo arrhenius --t1 300 --t2 310 --k1 0.01 --ea 52.9
syntopik run chem_physical thermo vant-hoff --t1 298 --t2 350 --k1 1.5 --dh 40.0
syntopik run chem_physical thermo kinetics --order 1 --k1 0.0693 --a0 1.0 --time 20
```

### 7. Exact Periodic Table & Inorganic Trends (`element`)
```bash
syntopik run chem_physical element Cr
syntopik run chem_physical element C N O F
```

---

## Calm Teaching Rules

- Pre-commit numeric solutions via `chem_physical.py` (and `math_verify.py units` for $\text{atm}\cdot\text{L} \leftrightarrow \text{J} \leftrightarrow \text{cal}$) before posing or grading any problem (`<= 160` words prose/turn).
- Spotlight classic traps: $K_w$ autoionization at $C \le 10^{-6}\,\text{M}$, 5% weak-acid breakdown, $\Delta H$ (kJ) vs $\Delta S$ (J), $p^3$/$d^5$ IE anomalies ($N > O$, $Be > B$, $Cr$, $Cu$), and $E^\circ$ being intensive (unlike $\Delta G^\circ = -nFE^\circ$).
- Persist `contrasts_with` edges and schedule reviews in real time via `srs_engine.py`.
