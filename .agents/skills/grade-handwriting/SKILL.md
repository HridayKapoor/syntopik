---
name: grade-handwriting
description: >-
  Grade handwritten derivations, proofs, STEM solutions, or whiteboard photos
  line-by-line via Vision, proof-graph diffing, and deterministic verification,
  localizing earliest breaks with R0-R2 nudges.
---

# Handwritten & Whiteboard Solution Grader — Earliest-Break, Zero-Spoiler Coaching

Most deep problem-solving — mathematical proofs, physics derivations, organic mechanisms, and whiteboard algorithm traces — happens by hand. This skill turns photos or tablet sketches of rough work into **surgical line-by-line debugging** without spoiling the rest of the solution.

*(Portable CLI: prefer `syntopik run <engine> ...` in attached repos, or `.venv/bin/python -B scripts/<engine>.py ...` in direct clones.)*

---

## Input Handling & Calm Turn Ceiling

- Accept photos/sketches **dragged directly into chat**, dropped into `sources/_inbox/`, drawn in `Syntopik Studio`'s rough-work sketch pad, or saved on demand in `sources/attempts/` (inspect via `read_file` / `Read`; never pre-create empty folders).
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
- **Structured Proof-Graph Handwriting Diff (`syntopik proof-diff` / `derivation_forge.py diff`)**:
  - Whenever a canonical Causal Step DAG exists in `knowledge-graph/derivations.jsonl` for the proof being graded, pass the verbatim transcribed steps `[{"line":1,"expr":"..."}, ...]` directly to:
    ```bash
    syntopik proof-diff --id <derivation_id> --steps-json '<steps_json_or_path>' --json
    # Direct-clone equivalent:
    syntopik run derivation_forge diff --id <derivation_id> --steps-json '<steps_json_or_path>' --json
    ```
  - Deterministically aligns each line against the step DAG (exact normalized or AST-safe SymPy/numeric equivalence), classifies `earliestBreakLine` + `errorKind` (`sign_flip`, `missing_factor`, `wrong_substitution`, `skipped_step`, `invalid_algebra`), emits **zero-leak `R0` / `R1` / `R2` nudges** (never revealing the target expression!), **auto-spawns a Blunder FEN Fork** into `forks.jsonl`, logs the root slip in `mistakes.jsonl`, and returns the optimal $\Delta H$ causal ancestor `diagnosticProbe` (pass `--no-spawn-fork` for read-only dry inspection).
- **Mathematics, Proofs & Physics** (`math_verify.py`):
  - Step equivalence: `syntopik run math_verify check-eq "<L_{i-1}>" "<L_i>"`
  - Calculus, ODEs, limits, series, vectors, matrices, error propagation, units: `eval`, `solve`, `ode`, `limit`, `check-dim`, `units`.
- **CS / Whiteboard Pseudocode / Recurrences** (`code_verify.py`):
  - Translate the learner's exact written logic faithfully (bugs intact!) into a tiny script in `/tmp` and test against small counterexamples and complexity bounds via `syntopik run code_verify test-cases ...` / `complexity`.
- **Physical & Organic Chemistry** (`chem_physical.py`, `chem_3d.py`, `organic_reagents.py`):
  - Redox/stoich/ICE/exact charge-balance pH/Nernst/thermo (`chem_physical`), CIP `(R)/(S)` & `(E)/(Z)` (`chem_3d inspect`), and chemoselectivity/rearrangement risk (`organic_reagents`).

### Stage 3 — Earliest Divergence Localization ($i_{\text{first}}$)
1. Pinpoint the **single earliest invalid transition** $i_{\text{first}} = \min\{i : L_{i-1} \not\Rightarrow L_i\}$.
2. **Separate root error from cascade**: ignore downstream lines $L_{i+1}\dots L_n$ that merely propagated from $L_{i_{\text{first}}}$.
3. **Catch compensating double errors**: even if the boxed final answer or output matches, verify intermediate steps — two cancelling mistakes must still be surfaced!
4. *(Optional visual callout)*: highlight the exact region on the learner's photo with a high-contrast white-halo box/arrow via `syntopik run pdf_studio annotate --image <photo> ...`.

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
syntopik run mistake_analytics log \
  --subject "<Subject>" --chapter "<Topic/Chapter>" \
  --category "<concept_gap|misconception|sign_error|unit_trap|condition_missed|algebra_slip|diagram_misread|careless|time_pressure|edge_case_missed|complexity_trap|off_by_one|state_mutation_bug>" \
  --trigger-cue "<Surface cue on page>" --wrong-move "<Exact slip at L_first>" \
  --correct-move "<Invariant/check that catches it>" --prop-id "<optional-slug>" --source-ref "<photo>"
```
Update `syntopik run srs_engine review <propId> --rating again|hard` when conceptual, and offer one fresh-surface retest (or Surgical Bug-Hunt if it hits 3 strikes).
- **Auto-Capture Mid-Derivation Fork & $\Delta H$ Causal Probe**: if `syntopik proof-diff` ran against `derivations.jsonl`, it already auto-spawned the Blunder FEN Fork (`spawnedFork`) and surfaced any ancestor `diagnosticProbe` (act on that probe if conceptual). For ad-hoc derivations without a stored DAG, capture the pre-blunder prefix (`L1..L_{i-1}`), sealed blunder, and winning move via `syntopik run fork_replay capture` so the learner replays that exact fork days later with mutated symbols/numbers instead of redoing Line 1.
