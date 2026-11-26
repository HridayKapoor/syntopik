---
name: exam-simulator
description: >-
  Build, launch, grade, and debrief 100% offline timed mastery assessments and
  authentic NTA JEE Main & IIT JEE Advanced CBT mock exams via
  scripts/exam_runner.py and demos/exam-cbt-template.html, plus live in-chat
  timed micro-sprints and R0-R5 post-exam debriefs across CS, ML, math, science,
  and competitive exams.
---

# Exam Simulator — Timed Mastery Assessments, Offline NTA JEE CBT & Surgical Debrief

Untimed study builds comprehension (Phase 1); timed simulation builds **retrieval speed, calibration under uncertainty, partial-credit judgment, and 2-pass triage** (Phase 2) across **any domain** (university midterms/finals, CS/ML technical screens, math/science tests, AND authentic NTA JEE Main & Advanced CBT):

- **Mode A — Standalone Offline Timed CBT HTML (`exams/<slug>.html`)**: zero-distraction browser exam with 5-state palette, per-question stopwatch, offline LaTeX rendering, Light/Dark editorial themes, Attempt JSON export, and deterministic `exam_runner.py grade`.
- **Mode B — Live AI-Coached Micro-Assessment (`3–5` Questions Right in Chat)**: rapid 10–15 minute timed sprint or post-exam `R0–R5` miss repair directly inside your harness.

---

## Mode A — Standalone Offline Timed Exam / CBT Workflow

### Phase 1 — Author a 100% Verified `spec.json`
Inspect the schema anytime:
```bash
.venv/bin/python -B scripts/exam_runner.py schema
```

**Mandatory Pre-Compile Verification (Zero Unverified Keys!)**:
- **CS / Algorithms / ML**: verify outputs, edge cases, and time/space complexity via `scripts/code_verify.py`.
- **Mathematics & Physics**: verify via `scripts/math_verify.py` (and `scripts/eduniti_pyq.py` when pulling real JEE PYQs).
- **Chemistry**: verify via `scripts/chem_physical.py`, `scripts/chem_3d.py`, and `scripts/organic_reagents.py`.
- **Inline Figures**: render diagrams/circuits/plots/structures as local `.svg`/`.png` files (`physics_draw.py`, `circuit_draw.py`, `math_plot.py`, `chem_3d.py`) and pass in `question.figure` — `exam_runner.py build` embeds them inline so `exams/<slug>.html` is **100% standalone offline**.

### Supported Question Types & Marking Profiles (Configurable Per Exam/Question)

| `type` | General Course / CS Profile | Official 2024–2026 JEE Main & Advanced Profile | Spec Fields |
| :--- | :--- | :--- | :--- |
| `single_correct` | `+1 / 0` or `+4 / -1` | JEE Main `+4 / -1 / 0`; JEE Adv `+3 / -1 / 0` | `options: [{id:"A", text:"..."}]`, `answer: "B"` |
| `multi_correct` | Exact or subset partial credit | **JEE Adv MSQ**: $\emptyset \Rightarrow 0$; any wrong $\Rightarrow -2$; all right $\Rightarrow +4$; strict subset $S \subset C \Rightarrow +\|S\|$ (`+4/+3/+2/+1/0/-2`) | `options: A..D`, `answer: ["A","C","D"]` |
| `numerical` | Exact/tolerance `+4 / 0` | **JEE Main Sec B**: 5 compulsory Qs/subject, **`+4 / -1 / 0`**; **JEE Adv**: `+4 / 0` or `+3 / 0`, 2-decimal range/tolerance | `answer: 4.5`, `tolerance: 0.01`, `marksWrong: 0` or `-1` |
| `matrix_match` | Per-row partial credit | Per-row partial (`+1`/`+2` per exact row) or List-Match combos | `rows`, `cols`, `answer: {"P":["1","3"]}` |

Always populate `id`, `subject`, `chapter`, `propId` (for automatic FSRS review scheduling!), `stem`, `solution`, `trapNote`, and `trapCategory` (`concept_gap`, `misconception`, `sign_error`, `unit_trap`, `condition_missed`, `algebra_slip`, `diagram_misread`, `careless`, `time_pressure`, `edge_case_missed`, `complexity_trap`, `off_by_one`, `state_mutation_bug`).

### Phase 2 — Compile & Launch Offline HTML
```bash
.venv/bin/python -B scripts/exam_runner.py build /tmp/<slug>-spec.json --out exams/<slug>.html
```
- **5-State Palette**: Not Visited, Not Answered, Answered (evaluated), Marked for Review (not evaluated), **Answered & Marked for Review (EVALUATED at submit/timeout!)**.
- **Numeric Keypad & Timer**: on-screen keypad + keyboard input (no calculator in official JEE 2024–2026), `<15m` amber / `<5m` red alerts, per-question stopwatch, `localStorage` auto-save, and **"Download Attempt JSON"** on submit.

### Phase 3 — Deterministic Grading & Automatic Ledger Sync
```bash
.venv/bin/python -B scripts/exam_runner.py grade <spec.json> <attempt.json> --json
```
*(Pass `--kg-dir /tmp/... --no-sync` in tests so real ledgers stay untouched!)*
Automatically computes exact scores/partial credit, flags slow questions (`> 180s`) & negative-mark traps, logs misses to `knowledge-graph/mistakes.jsonl`, appends `knowledge-graph/exam-attempts.jsonl`, and schedules missed `propId`s via `scripts/srs_engine.py review <propId> --rating again`.

---

## Phase 4 & Mode B — Calm Surgical Debrief & Live In-Chat Micro-Sprint

1. **Concise Debrief (`<= 160` words — Never Lecture Walls of Text!)**:
   - Lead with `score / maxScore (%)`, `accuracyPct`, negative marks surrendered, and `> 3m` time sinks.
   - Group misses by **Newman Error Tier**: *Comprehension/Model-Selection* → interleaved discrimination drills; *Process/Encoding* → pre-submit invariant/unit/edge-case sanity checks; *Executive/Pacing* → 2-pass triage.
   - Offer **1** clear next move: live `R0–R5` non-spoiling repair on the top `2–3` misses (`synt_practice_card` / `mlh_practice_card` with Pause Law) OR a 1–2p Typst Pitfall PDF (`mistake_analytics.py export-traps` → `cheatsheet_pdf.py build`).
2. **Live Micro-Sprint (`3–5` Verified Questions in Chat)**:
   - Pose **1** verified question per turn (`synt_quiz_card` / `mlh_quiz_card`, native question tool, or calm Markdown card with `"I don't know"` = skip for `0` marks), track cumulative score & pacing, sync misses to `mistakes.jsonl` + `srs_engine.py`, and repair the #1 leak.
