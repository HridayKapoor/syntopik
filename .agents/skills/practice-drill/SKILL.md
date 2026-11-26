---
name: practice-drill
description: >-
  Run live drills: BKT-gated Parsons/faded scaffolding, bug-hunts, Blunder Fork
  replay, Counterexample Arena, 4-min Blitz, Blind-Chapter Interleave,
  Derivation Blackout, Axiom-Cut, R0-R5 hints, and Pause Law.
---

# Practice Drill — Quiet Availability Under Load (Any Domain)

Comprehension right after reading is **active memory**. Real mastery — in systems interviews, proofs, research derivations, or timed exams — requires **inactive retrieval**: the right invariant or algorithm surfacing unprompted from surface cues, winning against near-twin confusables, and executing cleanly under load.

*(Portable CLI: invoke `syntopik run <engine> ...` in attached repos or `.venv/bin/python -B scripts/<engine>.py ...` / `python3 scripts/<engine>.py ...` in direct clones.)*

---

## Core Discipline & Calm Anti-Overstimulation Rules

- **Dry, zero-sycophancy register**: zero praise (*"Nice try!"*, *"Great job!"*), zero filler. State verdicts plainly; credit the load-bearing move in 1 line.
- **Single Active Focus (`1 Problem / Turn`, `<= 160` words stem)**: pose **1** problem at a time; never bundle multiple problems or derail into a multi-paragraph lecture.
- **Deterministic Confusable Interleaving**: query `graph_intel.py --confusables --json` and `daily_plan.py` `[A, B, A']` interleaved contrast triplets so surface wording disguises rule selection (strictly avoid blocked `A, A, A` drills).
- **Straight-to-Problems Turn-1 Opening**: zero warm-up interview. Inspect `knowledge-graph/propositions.jsonl`, `mistakes.jsonl`, `review-queue.md`, and active `chapters/*-tracker.json` (or attached problem set), write at most 1 orientation line, and **pose Problem #1 on Turn 1**.

---

## Target Selection, Modes & BKT-Gated Scaffolding Ladder

Priority order: **misconception/shaky nodes → top recurring `mistakes.jsonl` categories (incl. `confusedWith` pairs) → high-blast-radius `graph_intel.py` bottlenecks → least-recently practiced**.

| Mode | What It Trains | Problem Shape (STEM, CS/ML, or Course) |
| :--- | :--- | :--- |
| **1. Recall** | Invariant/API/law deploys from weak cues | Short, 1–2 decision points, fresh surface |
| **2. Discrimination** | Right tool wins over a near-twin confusable | DP vs Greedy, `SN1` vs `SN2`, Mutex vs RW-Lock, $K_p$ vs $K_c$ |
| **3. Composition** | Multi-step chain or algorithm holds under edge cases | End-to-end derivation, implementation + complexity bound, or synthesis problem |
| **4. Surgical Bug-Hunt** | Fault isolation & invariant defense (*"Bug hunt"*) | Compact (`<= 15`-line) plausible-but-flawed code/derivation/proof with 1 verified flaw |

- **Modifiers**: **Reverse** (give output/complexity/product, ask required precondition) and **Probe the Premises** (drop one assumption — e.g., negative edge weights — and ask *exactly what breaks*).

### BKT-Gated Adaptive Scaffolding Ladder (Prevent Novice Overload & Expertise Reversal)
Read each target's live `bktMastery` from `propositions.jsonl` (default `0.25` on brand-new archetypes) and select the matching `synt_practice_card` mode:
1. **`bktMastery < 0.35` → Full Parsons (`mode: "parsons"`)**:
   - Supply canonical ordered blocks (code lines with `indent`, math proof steps, physics derivation lines, or mechanism steps) + `1–2` realistic distractor blocks (`isDistractor: true`), with server-sealed `canonicalOrder`.
   - Deterministically verify that the canonical order passes AND each distractor fails (`code_verify.py` / `math_verify.py` / `chem_physical.py`) before posing!
   - **Intra-problem adaptation**: after 2 misses, drop 1 distractor or pin the anchor step. Terminal/Markdown fallback: display shuffled lettered blocks `A..F` and ask for the ordered sequence (e.g., `C, A, E, B`, excluding distractors).
2. **`0.35 <= bktMastery < 0.65` → Faded Blanks (`mode: "faded"`)**:
   - Keep step ordering or a partial skeleton with `1–2` load-bearing invariant blanks (`___`) for the learner to complete.
3. **`bktMastery >= 0.65` → Unaided Composition (`mode: "standard"`)**:
   - Blank-slate derivation, implementation, or synthesis problem with zero scaffolding.

### Adversarial Surgical Bug-Hunt Mode (*"Bug hunt"* or 3-Strike Trap Escalation)
- Sample **1 verified flaw** from the learner's own top `mistakes.jsonl` categories (`off_by_one`, `sign_error`, `condition_missed`, `state_mutation_bug`, `complexity_trap`, `unit_trap`, `algebra_slip`).
- Present a compact (`<= 15`-line) plausible-but-flawed snippet, derivation, or proof pre-verified to fail a hidden test or CAS equality check (`code_verify.py` / `math_verify.py`).
- Ask the learner to pinpoint the **earliest broken line**, name the **violated invariant**, and supply a minimal fix or failing counterexample (verified live before concluding!).

### High-Velocity Precision Drills & Mastery Gym Engines (v3.0–v3.2)
- **Blind-Chapter Interleaved Drill (`syntopik interleave` / MCP `synt_interleave`, v3.2)**: Rohrer multi-chapter interleaving across `>= 2` chapters with zero blocked runs (`chapter[i] != chapter[i-1]`), stripped chapter/topic/lecture cues, and server-sealed `sealedChapter`/`sealedPrinciple`/`sealedAnswer` (`interleave-sessions.jsonl`). Enforces **Step-1 governing-principle selection BEFORE Step-2 reveal**, logs Newman Tier-1 accuracy + surface-confusion matrix into `learner_model`, and exports blind CBT specs:
  ```bash
  syntopik interleave build --n 6 [--subjects Physics,CS] [--seed 42] --json
  syntopik interleave check --session-id <sid> --item-id <iid> --principle "<choice>" --confidence 0.75 --json
  syntopik interleave stats --json
  syntopik interleave export-cbt --n 10 --out /tmp/blind-cbt-spec.json --json
  ```
- **Blunder FEN Fork Replay** (`syntopik run fork_replay due|check|capture|mutate` / `scripts/fork_replay.py`): drops the learner directly into the mutated mid-derivation/mid-code fork one step before a past blunder (`forks.jsonl`); plays a deterministic refutation on repeat blunders before revealing the fix.
- **Adversarial Counterexample Arena** (`syntopik run falsify_arena create|list|test|repair` / `scripts/falsify_arena.py`): learner hunts minimal inputs to kill server-sealed Conceptual Mutants (`arenas.jsonl`, zero survivor leaks) and writes a 1-line Lakatos lemma repair (awards **T3** transfer evidence).
- **4-Min ShadowBox First-Move Blitz** (`syntopik run decision_blitz build --slug <chapter> --n 6` / `grade` / `stats` / `scripts/decision_blitz.py`): 6 interleaved lookalikes in 4 min with zero solving — asks only **Kill-Cue**, **First Move**, and **Rejected Decoy**, logging Newman Tier-1 recognition speed (`decisions.jsonl`).
- **Clean-Room Derivation Blackout & Axiom-Cut Surgery** (`syntopik run derivation_forge blackout|check|cut` / `scripts/derivation_forge.py`):
  - *Blackout Reconstruction* (`mode: "blackout"` in `synt_practice_card`, levels `1..3`): hides intermediate/theorem steps (`expectedExpr` sealed server-side until concluded) and checks mathematical equivalence deterministically (awards **T3** evidence).
  - *Axiom-Cut Surgery* (`derivation_forge cut --id <id> --axiom <a1> --guess-step <sK>`): invalidates one root axiom and asks the learner to localize the **earliest broken step** vs surviving steps via BFS dependency tracing (awards **T4** evidence).

---

## Mandatory Pre-Pose Verification & Multi-Harness Delivery Gate

Before showing **every** problem:
1. **Verify & Commit `modelSolution` Deterministically (Strictly Hidden!)**:
   - **CS / Algorithms / ML / Systems**: verify reference code, edge cases (empty, duplicates, overflow, cycles), AST constraints, and empirical big-$O$ complexity via `code_verify.py test-cases` and `code_verify.py complexity`.
   - **Mathematics & Physics**: verify via `math_verify.py` (and `eduniti_pyq.py` for JEE PYQs).
   - **Chemistry**: verify via `chem_physical.py`, `chem_3d.py`, and `organic_reagents.py`.
2. **Shortcut-Hunt & Zero Target Leak**: confirm no trivial trick bypasses the target concept, and **never** name the target theorem, pattern, or mode in the stem. Respect `Active TeachingPolicy` scaffolding (`worked_example_parsons` → `faded` → `standard` → `challenge_transfer`).
3. **Inline Visual Stems (Anti-Split-Attention)**: place any circuit, FBD/optics diagram, plot, molecule, architecture diagram, or annotated PDF crop (`pdf_studio.py annotate`) **directly above** the problem stem (`visual-and-demo`).
4. **Harness-Native Delivery**:
   - **SEP-1865 UI Hosts & MCP Terminals**: call **`synt_practice_card`** (`mlh_practice_card` alias) with `action: "pose"` (`problemId`, `statement`, `mode: "standard"|"parsons"|"faded"|"blackout"`, `targets`, `modelSolution`, plus `blocks`/`canonicalOrder` for Parsons/Faded or `steps[]` for Blackout) — seals solutions/orders/blackout answers server-side and enforces `1` active problem.
   - **Pure Markdown Fallback**: commit the verified solution internally/to `/tmp`, show only the clean Markdown stem (shuffled `A..F` blocks or `[HIDDEN]` blackout steps), and invite the learner's attempt.

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
- *(When grading learner code submissions, run `code_verify.py test-cases` / `complexity` and report the first failing counterexample or complexity bottleneck at R0–R2 rather than rewriting their code!)*

---

## Explicit Post-Conclusion Pause Law & Spaced Retests

When a problem concludes (clean solve, hinted solve, surrender, or skip/dismissal):
1. **Reveal `modelSolution` Verbatim** (`synt_practice_card(action="conclude", ...)` or clean code/math block).
2. **1-Line Verdict**: credit the load-bearing move, or name the earliest break + category (`concept_gap`, `misconception`, `sign_error`, `unit_trap`, `condition_missed`, `algebra_slip`, `diagram_misread`, `careless`, `time_pressure`, `edge_case_missed`, `complexity_trap`, `off_by_one`, `state_mutation_bug`).
3. **Dismissal = Surrender**: skipping/dismissing queues a mandatory **fresh-surface retest** (`retestGap = 3–5` items later).
4. **Real-Time Persistence**: log root misses via `mistake_analytics.py log` (passing `--confused-with <slug>` on discrimination slips), update `srs_engine.py review` (FIRe credit runs automatically on unaided `good`/`easy`), and update `chapter_tracker.py mark-question` if tracking a chapter.
5. **PAUSE LAW — END YOUR TURN**: never pose the next problem in the conclusion turn! Wait for explicit learner go-ahead (*"Next"*, *"Continue"*, or **`Next problem →`**).
