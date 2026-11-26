---
name: revision-sheets
description: >-
  Compile dense, printable 1-2 page A4 Mastery, Formula, Invariant, and Pitfall
  Revision Sheet PDFs across any subject using local Typst with equations, trap
  callouts, tables, and SVGs.
---

# Printable 1–2 Page Mastery & Pitfall Revision Sheets (Typst)

When finishing a chapter/topic, consolidating invariants, or building pre-exam one-pagers across **any domain** (Systems/Algorithms cheat sheets, ML math summaries, university midterms, or JEE Formula & Trap sheets), compile a crisp **1–2 page A4 PDF** into `chapters/<slug>-sheet.pdf`.

*(Portable CLI: prefer `syntopik run cheatsheet_pdf ...` in attached repos, or `.venv/bin/python -B scripts/cheatsheet_pdf.py ...` in direct clones.)*

---

## 1. Fast Offline Compilation (`scripts/cheatsheet_pdf.py`)

`scripts/cheatsheet_pdf.py` resolves local `typst` portably via `PATH` — zero network downloads, compiles in **<150 ms**, defaults cleanly to `"Mastery & Pitfall Sheet"` (customizable via `--exam-tag`, e.g., `"CS Systems · Invariants"` or `"JEE Main + Advanced · Traps"`), and converts LaTeX math (`\frac`, `\sqrt`, `\vec`, `\ddot`, `\ce{}`, Greek symbols) into native Typst math.

```bash
# Inspect exact JSON schema:
syntopik run cheatsheet_pdf schema

# Compile from structured JSON (or from mistake_analytics export-traps output):
syntopik run cheatsheet_pdf build /tmp/topic-sheet.json \
  --out chapters/<slug>-sheet.pdf --columns 2 --accent blue

# Or compile directly from Markdown chapter notes:
syntopik run cheatsheet_pdf build chapters/<slug>.md \
  --out chapters/<slug>-sheet.pdf --columns 2 --accent emerald

# Anti-Mastery 1-Page Exam Crib Sheet (auto-excludes solid nodes with R >= 0.90 & lapses == 0):
syntopik run cheatsheet_pdf build --from-kg --chapter "<slug>" --anti-mastery --pages 1 \
  --out chapters/<slug>-crib.pdf
```
- **Anti-Mastery Mode (`--anti-mastery --pages 1`)**: strips already-mastered formulas (`R >= 0.90, lapses == 0`) so 100% of the single A4 page is reserved for `shaky`/`misconception` formulas, personal `mistakes.jsonl` traps, and `"If condition X -> use Y"` micro-decision cues.

### Supported Elements in JSON Specs
- **`items`**: boxed invariant / theorem / complexity / formula rows (`label`, `math`, `note`).
- **`bullets`**: caveat-free unconditional principles, preconditions, and edge cases (`$...$`, `**bold**`, `` `code` ``).
- **`table`**: high-signal discrimination tables (e.g., *Raft vs Paxos*, *DP vs Greedy*, *SN1 vs SN2*, *Time/Space Big-O*).
- **`figure` / `figures`**: embed verified vector diagrams from `physics_draw.py`, `circuit_draw.py`, `math_plot.py`, or `chem_3d.py`.
- **`traps`**: crimson left-bordered **PITFALL / TRAP** callout boxes populated directly from `knowledge-graph/mistakes.jsonl` (`mistake_analytics export-traps --chapter "<title>"`) and `misconception`/`shaky` nodes!
- **`quickChecks`**: 5-second sanity, dimensional, or boundary checks.

---

## 2. Mandatory Density & Visual Verification

Never deliver an uninspected PDF:
1. Check JSON stdout: confirm `"withinTwoPages": true` (**1–2 balanced pages** — never spill 3 orphan lines onto page 3!).
2. Render PNG previews and visually inspect via `read_file` / `Read`:
   ```bash
   syntopik run pdf_studio render chapters/<slug>-sheet.pdf --pages 1-2 --dpi 160 --out /tmp/sheet-preview
   ```
   Verify crisp equations, zero overlapping labels, clean column balance, and readable diagrams, then remove `/tmp/sheet-preview`.
3. Syncing (`PostToolUse` / `node scripts/sync-graph.mjs --quiet`) automatically lists `chapters/<slug>-sheet.pdf` in `dashboard.html` and `syntopik serve`.

---

## 3. Optional Typst `@preview` Packages (Custom `--keep-typ` Layouts)

- **Chemistry**: `@preview/typsium:0.3.2`, `@preview/whalogen:0.3.0`, `@preview/alchemist:0.2.0`
- **Physics & Units**: `@preview/physica:0.9.8`, `@preview/zero:0.7.1`
- **LaTeX & Diagrams**: `@preview/mitex:0.2.7`, `@preview/cetz:0.5.3`, `@preview/zap:0.6.0`, `@preview/lilaq:0.6.0`, `@preview/showybox:2.0.4`
