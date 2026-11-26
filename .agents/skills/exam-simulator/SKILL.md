---
name: exam-simulator
description: >-
  Build, grade, and debrief 6-type offline/live CBT exams, EV-skip calibration,
  distractor autopsies, exam readiness, Pre-Mortem Shields, and Adversarial
  Viva Defense with 4-tier evidence and micro-sprints.
---

# Exam Simulator — Timed Mastery Assessments, Offline/Live CBT & 3-Bucket Autopsy

Untimed study builds comprehension (Phase 1); timed simulation builds **retrieval speed, calibration under uncertainty, partial-credit judgment, and 2-pass triage** (Phase 2) across **any domain** (university midterms/finals, CS/ML technical screens, math/science tests, AND authentic NTA JEE Main & Advanced CBT):

- **Mode A — Timed CBT HTML (`exams/<slug>.html`)**: zero-distraction browser exam with 5-state palette, per-question stopwatch, offline LaTeX rendering, Light/Dark themes, **1-click live auto-save & grading under `syntopik serve`** (`POST /api/exam/submit`), and 100% offline `file://` Attempt JSON export + `exam_runner.py grade`.
- **Mode B — Live AI-Coached Micro-Assessment (`3–5` Questions Right in Chat)**: rapid 10–15 minute timed sprint or post-exam `R0–R5` miss repair directly inside your harness.

*(Portable CLI: prefer `syntopik run exam_runner ...` in attached repos, or `.venv/bin/python -B scripts/exam_runner.py ...` / `python3 scripts/exam_runner.py ...` in direct clones.)*

---

## Mode A — Timed Exam / CBT Workflow

### Phase 1 — Author a 100% Verified `spec.json`
Inspect the schema anytime:
```bash
syntopik run exam_runner schema
```

**Mandatory Pre-Compile Verification (Zero Unverified Keys!)**:
- **CS / Algorithms / ML**: verify outputs, edge cases, and time/space complexity via `code_verify.py`.
- **Mathematics & Physics**: verify via `math_verify.py` (and `eduniti_pyq.py` when pulling real JEE PYQs).
- **Chemistry**: verify via `chem_physical.py`, `chem_3d.py`, and `organic_reagents.py`.
- **Inline Figures**: render diagrams/circuits/plots/structures as local `.svg`/`.png` files (`physics_draw`, `circuit_draw`, `math_plot`, `chem_3d`) and pass in `question.figure` — `exam_runner.py build` embeds them inline so `exams/<slug>.html` works 100% offline.

### Supported Question Types & Marking Profiles (6 Types, Configurable Per Exam/Question)

| `type` | General Course / CS / USMLE Profile | Official 2024–2026 JEE / GATE Profile | Spec Fields |
| :--- | :--- | :--- | :--- |
| `single_correct` | `+1 / 0` or `+4 / -1` | JEE Main `+4 / -1 / 0`; JEE Adv `+3 / -1 / 0` | `options: [{id:"A", text:"...", whyWrong?, trapCategory?, confusedWith?}]`, `answer: "B"` |
| `multi_correct` | `proportional` or `strict_all_or_nothing` | **JEE Adv MSQ** (`jee_adv_msq`: `+4/+3/+2/+1/0/-2`) or **GATE MSQ** (`gate_msq`: `+2/0`) | `markingProfile`, `options: A..D`, `answer: ["A","C","D"]` |
| `numerical` | Exact/tolerance `+4 / 0` | **JEE Main Sec B** (`+4 / -1 / 0`); **JEE Adv / GATE NAT** (`+4/0` or `+3/0`) | `answer: 4.5`, `tolerance: 0.01`, `marksWrong: 0` or `-1` |
| `matrix_match` | Per-row partial credit or NBME EMQ (`4–8` cols) | Per-row partial (`grid`) or List-Match 4-option codes (`code_options`) | `matrixMode`, `rows`, `cols`, `rowExplanations`, `answer: {"P":["1","3"]}` |
| `assertion_reason` | Dual-statement causal-link traps (`4opt`/`5opt`) | Auto-populated canonical `A..D`/`E` + deterministic `arErrorKind` (`causal_link_trap`, truth flips) | `assertion`, `reason`, `arVariant`, `causalLinkExplanation`, `answer` |
| `vignette_stem` | Shared clinical case, passage, data table, or system-design stem | Inline `subQuestions[]` or top-level `spec.vignettes[]` (`q.vignetteId`) + `vignetteBreakdown` | `vignettes[]` or inline `stem`/`figure`/`subQuestions[]` |

Always populate `id`, `subject`, `chapter`, `propId` (enables automatic FSRS `good` credit on hits + `again` scheduling on misses!), `stem`, `solution`, `trapNote`, optional per-option `whyWrong`/`distractorReasons` (sealed pre-submit via `strip_presubmit_bundle()` and revealed post-submit as **Why-Wrong Distractor Autopsy**!), and `trapCategory` (`concept_gap`, `misconception`, `sign_error`, `unit_trap`, `condition_missed`, `algebra_slip`, `diagram_misread`, `careless`, `time_pressure`, `edge_case_missed`, `complexity_trap`, `off_by_one`, `state_mutation_bug`).

### Phase 2 — Compile & Launch (1-Click Live Auto-Save or Offline `file://`)
```bash
syntopik run exam_runner build /tmp/<slug>-spec.json --out exams/<slug>.html
```
- **5-State Palette & Optional Calm Telemetry**: Not Visited, Not Answered, Answered, Marked for Review, **Answered & Marked for Review (EVALUATED!)**, plus optional 4-pill confidence bar (`25%..95%`), option strike-through (`eliminated[]`), `blindChapterLabels` masking, and a dismissible **Pre-Mortem Shield** pre-flight banner.
- **Numeric Keypad & Timer**: on-screen keypad + keyboard input (no calculator in official JEE 2024–2026), `<15m` amber / `<5m` red alerts, per-question stopwatch, and `localStorage` auto-save.
- **Live 1-Click Auto-Save (`syntopik serve --open`)**: submitting `exams/<slug>.html` POSTs to `/api/exam/submit`, runs deterministic grading + EV-skip audit + distractor autopsy, logs `exam-attempts.jsonl` + `mistakes.jsonl`, updates FSRS-6 + FIRe + `learner_model`, and shows `"Graded & Saved to Knowledge Graph ✓"`. On `file://`, `"Download Attempt JSON"` + offline sealed distractor autopsy remain 100% functional.

### Phase 3 — Deterministic Grading, EV-Skip Calibration & 3-Bucket Autopsy
```bash
syntopik run exam_runner grade <spec.json> <attempt.json> --json
```
*(Pass `--kg-dir /tmp/... --no-sync` in tests so real ledgers stay untouched!)*
Automatically performs in-process:
1. **Exact Scoring + Bidirectional FSRS Sync**: awards **`good` credit** on every correctly solved `propId` (propagating FIRe credit + response `confidence` to `srs_engine` & `learner_model`) and schedules missed `propId`s with `--rating again`, logging option-level `distractorAutopsy` traps into `mistakes.jsonl`.
2. **Expected-Value (EV) Skip Calibration (`evCalibration`)**: evaluates exact break-even threshold $p_{\text{be}} = (P + U)/(B + P)$ (e.g., `0.20` for `+4/-1`, `0.25` for `+3/-1`), reporting `negEvGamblesCount`, `marksLostToNegEv`, `underconfidentSkipsCount`, `marksLeftOnTable`, and `evDisciplineScore` (`0..100`).
3. **3-Bucket Exam Autopsy & 3-Strike Escalation**: splits misses into **`knowledge`**, **`execution`**, and **`time_sink`** (`egoTraps`), plus `vignetteBreakdown` and **3-strike escalations** queuing a Surgical Bug-Hunt (`practice-drill`) + 4-day blank-sheet retest.

### Mode D — Exam-Day Readiness Forecaster, `<= 72h` Taper & Pre-Mortem Shield (`v3.2`)
Run whenever the learner asks *"Am I ready?"*, *"Predict my score"*, or *"Pre-Mortem Shield"* (prefers MCP **`synt_readiness`**; CLI fallback below):
```bash
syntopik readiness [--exam-date YYYY-MM-DD] [--subject <S>] [--max-marks N] [--premortem-only] --json
```
- Fuses FSRS-6 projected retention $R(t_{\text{exam}}, S)$, 4-tier Evidence depth, syllabus coverage, Blunder Fork clearance, and timed EV discipline into `readinessIndex` (`0..100`), Bayesian-shrunk `predictedScorePct`, top-3 ROI `topLevers` (`roiMarksPerHour`), `<= 72h` `taperMode` (`allowNewConcepts: false`, sleep floor), and a **non-fabricated 3-bullet Gollwitzer If-Then Pre-Mortem Shield** (returns `bullets: []` on empty cold-start logs — never invents filler traps!).

---

## Phase 4 & Mode B — Calm Surgical Debrief & Live In-Chat Micro-Sprint

1. **Concise Debrief (`<= 160` words — Never Lecture Walls of Text!)**:
   - Lead with `score / maxScore (%)`, `accuracyPct`, negative marks surrendered, and the **3-Bucket split (`Knowledge` / `Execution` / `Time-Sink`)** + any **3-Strike Escalations**.
   - Map each bucket cleanly: *Knowledge* → interleaved discrimination/Parsons drills; *Execution* → pre-submit invariant/unit/edge-case checks; *Time-Sink* → 2-pass triage cutoff.
   - Offer **1** clear next move: live `R0–R5` non-spoiling repair on top `2–3` misses (`synt_practice_card` + Pause Law), a 3-strike Surgical Bug-Hunt, OR a 1–2p Typst Pitfall PDF (`mistake_analytics export-traps` → `cheatsheet_pdf build`).
2. **Live Micro-Sprint (`3–5` Verified Questions in Chat)**:
   - Pose **1** verified question per turn (`synt_quiz_card`, native question tool, or calm Markdown card with `"I don't know"` = skip for `0` marks), track cumulative score & pacing, sync hits (`good`) & misses (`again`) to `mistakes.jsonl` + `srs_engine.py`, and repair the #1 leak.

---

## Mode C — 5-Turn Adversarial Viva Voce Defense Chamber & 4-Tier Evidence Ledger (v3.0)

Use for high-centrality / bottleneck nodes (`"Viva defense <topic>"` or when `synt_next_move` flags `transfer_verify` anti-inflation):
```bash
syntopik run viva_chamber start <propId> --json
syntopik run viva_chamber turn <sessionId> --response "<learner defense>" --confidence 0.80 --json
syntopik run viva_chamber status <sessionId> --json
```
1. **5-Turn Structured Defense (`knowledge-graph/viva-sessions.jsonl`)**:
   - **Turn 1 — Commitment Claim**: pins the learner's initial claim in the **Turn-1 Commitment Ledger**.
   - **Turn 2 — Boundary Perturbation**: tests edge/regime limits from `boundaryConditions[]` / premises.
   - **Turn 3 — Assumption / Anti-Pattern Attack**: probes real failure traps from `mistakes.jsonl` / `chains.jsonl`.
   - **Turn 4 — Transfer Perturbation**: tests downstream or novel-regime consequences.
   - **Turn 5 — Sharpened Synthesis**: reconciles Turn 1 with Turns 2–4 (auto-checked by `contradictionCheck`!).
2. **4-Tier Epistemic Evidence Ledger (`T1`–`T4`, `syntopik/evidence.py`)**:
   - **T1**: assisted / recognized · **T2**: unaided recall/quiz · **T3**: verified transfer (`Counterexample Arena`, `Derivation Blackout`, timed CBT) · **T4**: structural defense (`Adversarial Viva >= 0.75`, `Axiom-Cut Surgery`, unassisted synthesis).
   - Passing Viva (`>= 0.75` across `clarity`, `boundary_awareness`, `transfer`, `calibration`) awards **T4** evidence; critical boundary collapse demotes a fragile `solid` node back to `shaky` immediately so mastery never inflates!
