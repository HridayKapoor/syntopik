---
name: revision-sheets
description: >-
  Compile ultra-dense, printable 1-2 page Mastery, Formula, Invariant & Pitfall
  Revision Sheet PDFs (chapters/slug-sheet.pdf) across any subject (CS, ML, math,
  physics, chemistry, courses, or JEE) using local Typst via
  scripts/cheatsheet_pdf.py, complete with equations, pitfall callouts, tables,
  and embedded SVGs.
---

# Printable 1–2 Page Mastery & Pitfall Revision Sheets (Typst)

When finishing a chapter/topic, consolidating invariants, or building pre-exam one-pagers across **any domain** (Systems/Algorithms cheat sheets, ML math summaries, university midterms, or JEE Formula & Trap sheets), compile a crisp **1–2 page A4 PDF** into `chapters/<slug>-sheet.pdf`.

---

## 1. Fast Offline Compilation (`scripts/cheatsheet_pdf.py`)

`scripts/cheatsheet_pdf.py` resolves local `typst` portably via `PATH` — zero network downloads, compiles in **<150 ms**, defaults cleanly to `"Mastery & Pitfall Sheet"` (customizable via `--exam-tag`, e.g., `"CS Systems · Invariants"` or `"JEE Main + Advanced · Traps"`), and converts LaTeX math (`\frac`, `\sqrt`, `\vec`, `\ddot`, `\ce{}`, Greek symbols) into native Typst math.

```bash
# Inspect exact JSON schema:
.venv/bin/python -B scripts/cheatsheet_pdf.py schema

# Compile from structured JSON (or from mistake_analytics.py export-traps output):
.venv/bin/python -B scripts/cheatsheet_pdf.py build /tmp/topic-sheet.json \
  --out chapters/<slug>-sheet.pdf --columns 2 --accent blue

# Or compile directly from Markdown chapter notes:
.venv/bin/python -B scripts/cheatsheet_pdf.py build chapters/<slug>.md \
  --out chapters/<slug>-sheet.pdf --columns 2 --accent emerald
```

### Supported Elements in JSON Specs
- **`items`**: boxed invariant / theorem / complexity / formula rows (`label`, `math`, `note`).
- **`bullets`**: caveat-free unconditional principles, preconditions, and edge cases (`$...$`, `**bold**`, `` `code` ``).
- **`table`**: high-signal discrimination tables (e.g., *Raft vs Paxos*, *DP vs Greedy*, *SN1 vs SN2*, *Time/Space Big-O*).
- **`figure` / `figures`**: embed verified vector diagrams from `scripts/physics_draw.py`, `circuit_draw.py`, `math_plot.py`, or `chem_3d.py`.
- **`traps`**: crimson left-bordered **PITFALL / TRAP** callout boxes populated directly from `knowledge-graph/mistakes.jsonl` (`mistake_analytics.py export-traps --chapter "<title>"`) and `misconception`/`shaky` nodes!
- **`quickChecks`**: 5-second sanity, dimensional, or boundary checks.

---

## 2. Mandatory Density & Visual Verification

Never deliver an uninspected PDF:
1. Check JSON stdout: confirm `"withinTwoPages": true` (**1–2 balanced pages** — never spill 3 orphan lines onto page 3!).
2. Render PNG previews and visually inspect via `read_file` / `Read`:
   ```bash
   .venv/bin/python -B scripts/pdf_studio.py render chapters/<slug>-sheet.pdf --pages 1-2 --dpi 160 --out /tmp/sheet-preview
   ```
   Verify crisp equations, zero overlapping labels, clean column balance, and readable diagrams, then remove `/tmp/sheet-preview`.
3. Syncing (`PostToolUse` / `node scripts/sync-graph.mjs --quiet`) automatically lists `chapters/<slug>-sheet.pdf` in `dashboard.html`.

---

## 3. Optional Typst `@preview` Packages (Custom `--keep-typ` Layouts)

- **Chemistry**: `@preview/typsium:0.3.2`, `@preview/whalogen:0.3.0`, `@preview/alchemist:0.2.0`
- **Physics & Units**: `@preview/physica:0.9.8`, `@preview/zero:0.7.1`
- **LaTeX & Diagrams**: `@preview/mitex:0.2.7`, `@preview/cetz:0.5.3`, `@preview/zap:0.6.0`, `@preview/lilaq:0.6.0`, `@preview/showybox:2.0.4`
