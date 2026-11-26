---
name: grade-handwriting
description: >-
  Grade handwritten rough-paper derivations, proofs, physics/math/chemistry
  solutions, or whiteboard pseudocode photos line-by-line via Vision plus
  deterministic verification (math_verify.py, code_verify.py, chem_physical.py,
  chem_3d.py), localize the earliest broken line, deliver a single non-spoiling
  R0-R2 nudge, and log the root error to mistakes.jsonl.
---

# Handwritten & Whiteboard Solution Grader — Earliest-Break, Zero-Spoiler Coaching

Most deep problem-solving — mathematical proofs, physics derivations, organic mechanisms, and whiteboard algorithm traces — happens by hand. This skill turns photos or tablet sketches of rough work into **surgical line-by-line debugging** without spoiling the rest of the solution.

---

## Input Handling & Calm Turn Ceiling

- Accept photos/sketches **dragged directly into chat**, drawn in `Syntopik Studio`'s rough-work sketch pad, or saved on demand in `sources/attempts/` (inspect via `read_file` / `Read`; never pre-create empty folders).
- If a symbol, subscript, or indentation on a specific line is genuinely illegible or cropped, **ask the learner to clarify that exact line first** rather than guessing.
- Keep your response **`<= 160` words** with **1** clear verification nudge.

---

## Mandatory 5-Stage Grading Pipeline (FERMAT / VLMath Protocol)

### Stage 1 — Anti-Overcorrection Verbatim Transcription (`L1..Ln`)
Vision models instinctively "auto-fix" slips while reading. **Actively resist overcorrection:**
1. Transcribe every handwritten step `L1..Ln` **strictly as written** — preserving dropped minus signs, missing factors, off-by-one loop bounds, wrong base cases, unbalanced charges, crossed-out terms, and missing units verbatim.
2. Mark any unreadable token explicitly as `[?]` rather than filling in the textbook step.
3. Show the learner only the concise numbered lines `L1..L_{i_first}` needed to anchor your feedback (or confirm all lines if completely valid).

### Stage 2 — Deterministic Step-by-Step Verification (Never Trust Mental Algebra/Tracing!)
Audit **every transition** `L_{i-1} -> L_i` programmatically:
- **Mathematics, Proofs & Physics** (`scripts/math_verify.py`):
  - Step equivalence: `.venv/bin/python -B scripts/math_verify.py check-eq "<L_{i-1}>" "<L_i>"`
  - Calculus, ODEs, limits, series, vectors, matrices, error propagation, units: `eval`, `solve`, `ode`, `limit`, `check-dim`, `units`.
- **CS / Whiteboard Pseudocode / Recurrences** (`scripts/code_verify.py`):
  - Translate the learner's exact written logic faithfully (bugs intact!) into a tiny script in `/tmp` and test against small counterexamples and complexity bounds via `.venv/bin/python -B scripts/code_verify.py test-cases ...` / `complexity`.
- **Physical & Organic Chemistry** (`scripts/chem_physical.py`, `scripts/chem_3d.py`, `scripts/organic_reagents.py`):
  - Redox/stoich/ICE/exact charge-balance pH/Nernst/thermo (`chem_physical.py`), CIP `(R)/(S)` & `(E)/(Z)` (`chem_3d.py inspect`), and chemoselectivity/rearrangement risk (`organic_reagents.py`).

### Stage 3 — Earliest Divergence Localization ($i_{\text{first}}$)
1. Pinpoint the **single earliest invalid transition** $i_{\text{first}} = \min\{i : L_{i-1} \not\Rightarrow L_i\}$.
2. **Separate root error from cascade**: ignore downstream lines $L_{i+1}\dots L_n$ that merely propagated from $L_{i_{\text{first}}}$.
3. **Catch compensating double errors**: even if the boxed final answer or output matches, verify intermediate steps — two cancelling mistakes must still be surfaced!
4. *(Optional visual callout)*: if helpful, highlight the exact region on the learner's photo with a high-contrast white-halo box/arrow via `scripts/pdf_studio.py annotate --image <photo> ...`.

### Stage 4 — Non-Spoiling `R0–R2` Nudge (Default Turn-1 Response, `<= 35` Words)
- **If ALL lines hold**: confirm plainly in 1–2 lines, credit the key move, and optionally ask 1 quick limiting-case or complexity check.
- **If $L_{i_{\text{first}}}$ breaks**: confirm `L1..L_{i-1}` hold, **NEVER reveal the corrected $L_i$ or final answer upfront**, and deliver **ONE** surgical nudge:
  - **R0 — Line Localization**: *"Lines L1–L2 hold; re-check the transition L2 → L3."*
  - **R1 — Invariant / Law Cue**: *"Check the sign of friction work / base-case index when $n = 1$ in L3."*
  - **R2 — 5-Second Sanity Test**: *"Test $\theta \to 0$ (or input `[2, 1]`) on L2 vs L3."*
- Escalate to `R3–R5` (`practice-drill`) ONLY on follow-up request; show the full solution ONLY on explicit surrender.

### Stage 5 — Root-Error Logging & Spaced Follow-Up (Once Concluded)
Log **ONLY the single root error at $i_{\text{first}}$** into `knowledge-graph/mistakes.jsonl`:
```bash
.venv/bin/python -B scripts/mistake_analytics.py log \
  --subject "<Subject>" --chapter "<Topic/Chapter>" \
  --category "<concept_gap|misconception|sign_error|unit_trap|condition_missed|algebra_slip|diagram_misread|careless|time_pressure|edge_case_missed|complexity_trap|off_by_one|state_mutation_bug>" \
  --trigger-cue "<Surface cue on page>" --wrong-move "<Exact slip at L_first>" \
  --correct-move "<Invariant/check that catches it>" --prop-id "<optional-slug>" --source-ref "<photo>"
```
Update `scripts/srs_engine.py review <propId> --rating again|hard` when conceptual, and offer one fresh-surface retest.
