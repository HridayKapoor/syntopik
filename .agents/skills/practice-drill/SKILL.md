---
name: practice-drill
description: >-
  Run a calm, LearnHarness-grade live retrieval, discrimination, and composition
  practice session across CS/coding (verified via scripts/code_verify.py), math,
  physics, chemistry, or exam problems: Turn-1 problem opening, hidden verified
  modelSolution commitment, concise R0-R5 hints, never-auto-conclude rule,
  Post-Conclusion Pause Law, and synt_practice_card or mlh_practice_card support.
---

# Practice Drill — Quiet Availability Under Load (Any Domain)

Comprehension right after reading is **active memory**. Real mastery — in systems interviews, proofs, research derivations, or timed exams — requires **inactive retrieval**: the right invariant or algorithm surfacing unprompted from surface cues, winning against near-twin confusables, and executing cleanly under load.

---

## Core Discipline & Calm Anti-Overstimulation Rules

- **Dry, zero-sycophancy register**: zero praise (*"Nice try!"*, *"Great job!"*), zero filler. State verdicts plainly; credit the load-bearing move in 1 line.
- **Single Active Focus (`1 Problem / Turn`, `<= 160` words stem)**: pose **1** problem at a time; never bundle multiple problems or derail into a multi-paragraph lecture.
- **Interleaving by Default**: alternate distinct targets (`basics/foundations → main/core → advanced/mastery`) so short-term echo never masquerades as retrieval.
- **Straight-to-Problems Turn-1 Opening**: zero warm-up interview. Inspect `knowledge-graph/propositions.jsonl`, `mistakes.jsonl`, `review-queue.md`, and active `chapters/*-tracker.json` (or attached problem set), write at most 1 orientation line, and **pose Problem #1 on Turn 1**.

---

## Target Selection, Three Modes & Worked-to-Faded Scaffolding

Priority order: **misconception/shaky nodes → top recurring `mistakes.jsonl` categories → high-blast-radius `graph_intel.py` bottlenecks → least-recently practiced**.

| Mode | What It Trains | Problem Shape (STEM, CS/ML, or Course) |
| :--- | :--- | :--- |
| **1. Recall** | Invariant/API/law deploys from weak cues | Short, 1–2 decision points, fresh surface |
| **2. Discrimination** | Right tool wins over a near-twin confusable | DP vs Greedy, `SN1` vs `SN2`, Mutex vs RW-Lock, $K_p$ vs $K_c$ |
| **3. Composition** | Multi-step chain or algorithm holds under edge cases | End-to-end derivation, implementation + complexity bound, or synthesis problem |

- **Modifiers**: **Reverse** (give output/complexity/product, ask required precondition) and **Probe the Premises** (drop one assumption — e.g., allow negative edge weights — and ask *exactly what breaks*).
- **Novice Scaffolding**: if an archetype is completely unseen and the learner stalls immediately, step down to **1 Worked Micro-Example (`<= 5` steps) → 1 Faded Problem → Full Unaided Problem**.

---

## Mandatory Pre-Pose Verification & Multi-Harness Delivery Gate

Before showing **every** problem:
1. **Verify & Commit `modelSolution` Deterministically (Strictly Hidden!)**:
   - **CS / Algorithms / ML / Systems**: verify reference code, tricky edge cases (empty, duplicates, overflow, cycles), AST constraints, and empirical big-$O$ complexity via `.venv/bin/python -B scripts/code_verify.py test-cases ...` and `code_verify.py complexity ...`.
   - **Mathematics & Physics**: verify via `scripts/math_verify.py` (and `scripts/eduniti_pyq.py` when serving JEE PYQs).
   - **Chemistry**: verify via `scripts/chem_physical.py`, `scripts/chem_3d.py`, and `scripts/organic_reagents.py`.
2. **Shortcut-Hunt & Zero Target Leak**: confirm no trivial trick bypasses the target concept, and **never** name the target theorem, pattern, or mode in the stem (*"Solve this Dijkstra problem..."* ruins retrieval!).
3. **Inline Visual Stems (Anti-Split-Attention)**: place any circuit, FBD/optics ray diagram, plot, molecule, architecture diagram, or annotated PDF crop (`pdf_studio.py annotate`) **directly above** the problem stem (`visual-and-demo`).
4. **Harness-Native Delivery**:
   - **SEP-1865 UI Hosts & MCP Terminals**: call **`synt_practice_card`** (`mlh_practice_card` alias) with `action: "pose"` (`problemId`, `statement`, `mode`, `targets`, `modelSolution`) — seals `modelSolution`/`mode`/`targets` server-side and enforces `1` active problem (`server.mjs` automatically prints the stem cleanly on non-UI terminal hosts!).
   - **Pure Markdown Fallback**: commit the verified solution internally/to `/tmp`, show only the clean Markdown stem, and invite the learner's attempt or code.

---

## Active Loop — `<= 35`-Word `R0–R5` Hints & Never-Auto-Conclude

- **Never Auto-Conclude**: stay on the active problem across multiple attempts or code revisions while learning yield remains; conclude **ONLY** on a verified solve OR explicit learner request to reveal/skip.
- **Side Clarification Cap (`<= 40` words)**: clarify ambiguous problem statements for free in 1–2 sentences without leaking the move.
- **`R0–R5` Progressive Hint Ladder (`1` rung per turn, `<= 35` words each)**:
  - **R0 — Invariant / Attention Redirect**: *"Check the constraint on negative weights / boundary $n = 0$."*
  - **R1 — Structural Pattern**: name the shape without naming the theorem/algorithm.
  - **R2 — Candidate Class / Boundary Check**: narrow to 2–3 candidate approaches; leave discrimination to the learner.
  - **R3 — Name the Principle / Invariant**: surrender retrieval; keep execution with the learner.
  - **R4 — Setup Skeleton**: give the recurrence/FBD/state setup; learner completes & verifies.
  - **R5 — Single Worked Step** (or full solution on explicit surrender).
- *(When grading learner code submissions, run `scripts/code_verify.py test-cases` / `complexity` and report the first failing counterexample or complexity bottleneck at R0–R2 rather than rewriting their code!)*

---

## Explicit Post-Conclusion Pause Law & Spaced Retests

When a problem concludes (clean solve, hinted solve, surrender, or skip/dismissal):
1. **Reveal `modelSolution` Verbatim** (`synt_practice_card(action="conclude", ...)` or clean code/math block).
2. **1-Line Verdict**: credit the load-bearing move, or name the earliest break + category (`concept_gap`, `misconception`, `sign_error`, `unit_trap`, `condition_missed`, `algebra_slip`, `diagram_misread`, `careless`, `time_pressure`, `edge_case_missed`, `complexity_trap`, `off_by_one`, `state_mutation_bug`).
3. **Dismissal = Surrender**: skipping/dismissing queues a mandatory **fresh-surface retest** (`retestGap = 3–5` items later).
4. **Real-Time Persistence**: log root misses via `scripts/mistake_analytics.py log`, update `scripts/srs_engine.py review`, and update `scripts/chapter_tracker.py mark-question` if tracking a chapter.
5. **PAUSE LAW — END YOUR TURN**: never pose the next problem in the conclusion turn! Wait for explicit learner go-ahead (*"Next"*, *"Continue"*, or **`Next problem →`**).
