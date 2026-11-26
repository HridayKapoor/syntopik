---
name: exam-simulator
description: >-
  Build, grade, and debrief offline/live CBT exams, NTA JEE mocks, and
  Adversarial Viva Defense chambers with 4-tier evidence gates, 3-bucket
  Knowledge/Execution/Time-Sink autopsy, and micro-sprints.
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

### Supported Question Types & Marking Profiles (Configurable Per Exam/Question)

| `type` | General Course / CS Profile | Official 2024–2026 JEE Main & Advanced Profile | Spec Fields |
| :--- | :--- | :--- | :--- |
| `single_correct` | `+1 / 0` or `+4 / -1` | JEE Main `+4 / -1 / 0`; JEE Adv `+3 / -1 / 0` | `options: [{id:"A", text:"..."}]`, `answer: "B"` |
| `multi_correct` | Exact or subset partial credit | **JEE Adv MSQ**: $\emptyset \Rightarrow 0$; any wrong $\Rightarrow -2$; all right $\Rightarrow +4$; strict subset $S \subset C \Rightarrow +\|S\|$ (`+4/+3/+2/+1/0/-2`) | `options: A..D`, `answer: ["A","C","D"]` |
| `numerical` | Exact/tolerance `+4 / 0` | **JEE Main Sec B**: 5 compulsory Qs/subject, **`+4 / -1 / 0`**; **JEE Adv**: `+4 / 0` or `+3 / 0`, 2-decimal range/tolerance | `answer: 4.5`, `tolerance: 0.01`, `marksWrong: 0` or `-1` |
| `matrix_match` | Per-row partial credit | Per-row partial (`+1`/`+2` per exact row) or List-Match combos | `rows`, `cols`, `answer: {"P":["1","3"]}` |

Always populate `id`, `subject`, `chapter`, `propId` (enables automatic FSRS `good` credit on hits + `again` scheduling on misses!), `stem`, `solution`, `trapNote`, and `trapCategory` (`concept_gap`, `misconception`, `sign_error`, `unit_trap`, `condition_missed`, `algebra_slip`, `diagram_misread`, `careless`, `time_pressure`, `edge_case_missed`, `complexity_trap`, `off_by_one`, `state_mutation_bug`).

### Phase 2 — Compile & Launch (1-Click Live Auto-Save or Offline `file://`)
```bash
syntopik run exam_runner build /tmp/<slug>-spec.json --out exams/<slug>.html
```
- **5-State Palette**: Not Visited, Not Answered, Answered (evaluated), Marked for Review (not evaluated), **Answered & Marked for Review (EVALUATED at submit/timeout!)**.
- **Numeric Keypad & Timer**: on-screen keypad + keyboard input (no calculator in official JEE 2024–2026), `<15m` amber / `<5m` red alerts, per-question stopwatch, and `localStorage` auto-save.
- **Live 1-Click Auto-Save (`syntopik serve --open`)**: when opened via `http://127.0.0.1:4173`, submitting `exams/<slug>.html` automatically POSTs to `/api/exam/submit`, runs deterministic grading, logs `exam-attempts.jsonl` + `mistakes.jsonl`, updates FSRS-6 + FIRe, and renders `"Graded & Saved to Knowledge Graph ✓"` immediately. On `file://`, `"Download Attempt JSON"` stays available for offline grading.

### Phase 3 — Deterministic Grading, `good` Credit & 3-Bucket Autopsy
```bash
syntopik run exam_runner grade <spec.json> <attempt.json> --json
```
*(Pass `--kg-dir /tmp/... --no-sync` in tests so real ledgers stay untouched!)*
Automatically performs in-process:
1. **Exact Scoring + Bidirectional FSRS Sync**: awards **`good` credit** on every correctly solved `propId` (propagating FIRe credit to prerequisites!) and schedules missed `propId`s with `--rating again`, appending `revlog.jsonl`, `mistakes.jsonl`, and `exam-attempts.jsonl`.
2. **3-Bucket Exam Autopsy**: classifies every miss/drag into:
   - **`knowledge`**: conceptual gaps, misconceptions, missed preconditions.
   - **`execution`**: sign/unit/algebra/off-by-one/edge-case/careless slips on known concepts.
   - **`time_sink`**: ego-trap questions flagged by elevated `timeSpent / medianTime` (or `> 180s`).
3. **3-Strike Escalation**: whenever a `(micro-topic, root-cause)` pair reaches **3 strikes** in `mistakes.jsonl`, flags escalation to queue a targeted **Surgical Bug-Hunt** (`practice-drill`) + an unaided **Blank-Sheet Delayed Retest** in 4 days.

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
