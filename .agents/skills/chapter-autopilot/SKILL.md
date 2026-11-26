---
name: chapter-autopilot
description: >-
  Hands-free orchestrator using the next-move brainstem, ICS auto-pacer, 72h
  taper and exam readiness, 48h triage, and sunset pruning. Triggers on
  "Start", "Continue", "Am I ready?", or "5-min review".
---

# Chapter Autopilot — Universal Hands-Free Foundations → Mastery Orchestrator

The learner's only job is thinking about the subject — CS, ML, Systems, Math, Physics, Chemistry, Research Papers, or JEE prep. **100% of orchestration, source chunking, exercise tracking, FIRe spacing, interleaving, and verification happens silently behind the scenes.**

*(Portable CLI: prefer `syntopik <cmd>` / `syntopik run <engine>` in attached repos; direct clones also support `.venv/bin/python -B scripts/<engine>.py` or bare `python3 scripts/<engine>.py`.)*

---

## Non-Negotiable Autopilot Laws

1. **Zero-Student-Logistics Law**: NEVER ask the learner to run CLI commands, remember script names, pick skills, or track exercise IDs. Absorb every command silently and present only the next single calm learning interaction.
2. **Calm Anti-Overstimulation Pacing**: Keep teaching prose **`<= 160` words/turn** (target `90–130`w), strictly **`1` active demand/turn**, `<= 40`w on side clarifications, and zero sycophantic praise.
3. **Zero-Repetition Invariant**: Every topic and exercise is tracked in `chapters/<slug>-tracker.json` via `chapter_tracker.py`. Monotonic upserts (`add-questions`) NEVER reset `solved`/`mastered` back to `unseen`, and `next` NEVER re-serves solved/mastered/skipped items.
4. **Multi-Harness UX Adaptation**: Prefer `synt_quiz_card` / `synt_practice_card` (`standard`/`parsons`/`faded`) / `synt_lesson_studio` on SEP-1865 hosts; degrade cleanly to native question/task tools or single calm Markdown cards (`BOOTSTRAP.md`). For zero-token flashcards, SVG occlusion, or 1-click CBT grading, mention the optional live 2-way browser workbench (`syntopik serve --open`, offline `file://` fallback).
5. **Real-Time Persistence Law**: Update `propositions.jsonl`, `chains.jsonl`, `revlog.jsonl`, `mistakes.jsonl`, `chapters/<slug>-tracker.json`, and Study Studio **immediately** after probing, plan lock, every missed check/question, and every mastered topic.
6. **Teacher-First Priority Law**: When instructor lecture/whiteboard notes accompany textbooks or problem banks, ALWAYS lock the instructor's sequence (`p.1 → p.N` / `Lec 1 → N`) as the `#1` canonical spine. If no files exist, run `topic-onboarding` Mode B first.

---

## Plain-English Intent Routing (Absorb Silently!)

| Learner Phrase / Intent | Silent Action Behind the Scenes |
| :--- | :--- |
| *"Start `<topic>`"* / *"Continue"* / *"What's next?"* / *"I'm ready"* | Query Pedagogical Brainstem **`synt_next_move`** / `syntopik next --json` first (then `chapter_tracker next --include-warmup --json` for frontier details) |
| *"Drop these files/links"* / mixed sources | **10-Second Drop**: `syntopik drop <inputs...> --json` (locks Teacher-First spine + tracker + Probe #1) |
| *"I fell behind"* / *"Clear my overdue pile"* | **Calm Catch-Up**: `syntopik review --recover --daily-cap 15 --json` (zero-guilt 5–7 day smoothing ranked by risk × blast radius) |
| *"5-minute review"* / *"I have 5 minutes"* | **Micro-Review Sprint**: `syntopik run daily_plan --micro 5 --json` (`3–5` highest-ROI cues: bottleneck + trap + occlusion/audio) |
| *"Plan my semester"* / *"Export calendar"* / *"Auto-pace"* | **Multi-Course Auto-Pacer**: `syntopik pace [--rebalance] [--ics <file.ics>] --json` (buffer-protected schedule + RFC-5545 `.ics` calendar export) |
| *"Exam is over"* / *"Sunset `<chapter>`"* | **Semester Sunset Pruning**: `syntopik sunset <slug> [--apply] --json` (snapshots zero-PII pack, keeps core axioms at $R=0.80$, archives low-leverage leaves) |
| *"Exam in 48 hours"* / crunch `< 72h` | **48h Exam Triage & `<= 72h` Taper**: `syntopik readiness --json` (auto-activates `taper_72h` / `final_24h` with `allowNewConcepts: false` & sleep floor) + `syntopik triage --exam-in 48h --sleep-hours 7 [--crib-sheet] --json` |
| *"Am I ready?"* / *"Score forecast"* / *"Pre-Mortem Shield"* | **Exam-Day Readiness & Pre-Mortem**: MCP **`synt_readiness`** or `syntopik readiness [--exam-date YYYY-MM-DD] [--max-marks N] [--premortem-only] --json` (`exam-simulator`) |
| *"Mix chapters"* / *"Blind interleave"* | **Blind-Chapter Interleave**: MCP **`synt_interleave`** or `syntopik interleave build --n 6 --json` (`practice-drill`) |
| *"Blunder Fork"* / *"Arena"* / *"4-Min Blitz"* / *"Blackout"* / *"Axiom-Cut"* | Route to **Mastery Gym** (`practice-drill`: `fork_replay`, `falsify_arena`, `decision_blitz`, `derivation_forge`) |
| *"Teach-back"* / *"Viva defense"* | **Protégé Capstone** (`teach-chapter`) or **5-Turn Adversarial Viva Defense** (`exam-simulator` / `viva_chamber`) |
| *"Work mentor mode"* / *"Debrief my git diff"* | **Anti-Vibe-Coding Work Mentor**: `syntopik mode mentor` / `syntopik debrief --ref HEAD~1..HEAD --json` (crystallizes real diff bugs into Forks & KG nodes) |
| Returning after `>= 4` days / *"Where was I?"* | **Cold-Start Re-Entry Brief**: `syntopik review --resume` (suppresses overdue guilt; 90s 3-bullet reload + 1 anchor cue `0.60 <= R <= 0.85`) |
| *"Low energy"* / *"Deep focus"* | **Cognitive Energy Mode**: `syntopik next --energy low\|normal\|deep --json` / `daily_plan --energy ...` |
| Anki `.apkg`, `.ipynb`, CSV/TSV, Notion `.zip`, YouTube, arXiv, GitHub, URL | `syntopik drop <target>` or `syntopik import <target> --register-tracker --json` (`topic-onboarding` / `ingest-chapter`) |

---

## Flow A — Turn-1 New Topic/Chapter Kickoff (*"Start `<topic>` [from `<path>`]"*)

1. **Source / Zero-Source Check**:
   - External URL/YouTube/arXiv/GitHub → `syntopik import "<target>" --register-tracker --json`.
   - Zero local files & no tracker → run `topic-onboarding` Mode B (`<= 3` scoping questions → bounded `research_scout.py manifest` → tracker init).
2. **Initialize Tracker (Fast Metadata Triage)**:
   ```bash
   syntopik run chapter_tracker init <slug> \
     --title "<Title>" --subject "<CS|ML|Maths|Physics|Chemistry|...>" \
     [--source-dir "<folder_or_path>"] --set-active --json
   ```
3. **Background Bounded-Window Source Indexing (NEVER Dump 70–150p PDFs Into Chat!)**:
   - Dispatch parallel background subagents (`invoke_subagent` / `Agent` / `spawn_agent`, or bounded chunks) over **15–20 page windows with 1-page overlap** (`pdf_studio.py inspect` + `tree-index` + targeted `extract`/`tables`; `3–5` rasterized pages/Vision step on scanned notes).
   - Carry active parent headings (`H1/H2`) + last `qid` across chunk boundaries, upsert `topics[]` (`basics` → `main` → `advanced`), deduplicated `questions[]`, `sources[].indexedRanges`, and `set-milestone <slug> sourcesIndexed --value true`.
4. **Start Live Probing in the SAME Turn & Persist Immediately**:
   - Check `propositions.jsonl` & `mistakes.jsonl` (or `synt_get_frontier`), open `synt_lesson_studio` if active, and fire Probe #1 (`3–5` probes max).
   - In the **same turn** probing finishes: set `probed=true`, log Floor (`--rating good`) & Ceiling misses (`--rating again --misconception` + `mistake_analytics.py log`), seed `planned` nodes + `chains.jsonl`, push the Basics-at-Top (`prereq --> dependent`) DAG + `activeNodeId`, and initialize progress steps.

---

## Flow B — Automatic Daily / Turn Loop (*"Continue"*, *"What's next?"*, *"I'm ready"*)

Silently query the **Pedagogical Brainstem** first (prefers MCP **`synt_next_move`**; CLI fallback below):
```bash
syntopik next --json
syntopik run chapter_tracker next --include-warmup --json
```
Execute the brainstem's `recommendedMode` directly (`whiteboard_pin_answer` → `onboarding_calibration` → `cold_start_reentry` → `calm_wrapup` → `bridge_worked_example` → `causal_probe` → `fork_replay` → `fire_review` → `transfer_verify` → `pre_exam_taper` → `blind_interleave` → `teach_frontier`) while obeying `teachingPolicy` constraints (`maxProseWords`, `scaffoldingMode`, `affectIntervention`). When `pre_exam_taper` is active (`<= 72h` to exam), enforce `allowNewConcepts: false` (consolidate `0.55 <= R <= 0.85`, clear due Forks, review Pre-Mortem Shield, protect sleep floor). *(If the learner gives a time budget like "I have 60 minutes", also run `syntopik run daily_plan --minutes <N> --json`; for multi-course calendar scheduling run `syntopik pace --ics <file.ics>`.)*

1. **FIRe-Compressed Spaced Warmup (`warmupItems`, max `3`)**: weave `<= 3` high-leverage cues from `srs_engine.py due --compress` (composite reviews that knock out due prerequisites via FIRe; note quiet 1-line FIRe summary when prerequisites refresh!). Also surface 1–2 due extracts from `reading-queue.jsonl` when active.
2. **`teach_topic` (Bloom Mastery Gate)**:
   - Read **ONLY** the active topic's `sourceRefs` pages/files (or active incremental `slices[]`).
   - Crop & white-halo/occlusion annotate key figures (`pdf_studio.py annotate [--occlude]`), render exact plots/diagrams/code traces inline (`visual-and-demo`), and **teach WITH named controls** in `<= 160` words.
   - Verify every claim deterministically (`math_verify`, `code_verify`, `chem_physical`, `chem_3d`) and run `Productive Failure (bottlenecks) → Predict → Teach → Check` (+ optional 3-turn Protégé Teach-Back).
   - On unaided transfer (~80–90%), run `srs_engine.py find-similar`, persist propositions + cues + `premises[] -> conclusion` hyperedges, and call `chapter_tracker.py set-topic <slug> <topic_id> --status mastered`.
3. **`solve_question` / `retest_question` (Confusable Interleaving + BKT Scaffolding + `retestGap = 3`)**:
   - Interleave across `>= 2` mastered topics using `graph_intel.py --confusables` `[A, B, A']` contrast triplets; wait `stepCounter - lastStep >= retestGap` (`3–5` steps) before fresh-surface retests.
   - Verify `modelSolution` beforehand and select BKT-gated scaffolding (`< 0.35` Parsons → `0.35..0.65` Faded → `>= 0.65` Standard via `synt_practice_card`, or invite handwritten/code submission via `grade-handwriting`).
   - Log root errors via `mistake_analytics.py log`, update `srs_engine.py review`, and call `chapter_tracker.py mark-question <slug> <qid> --status solved|retest|mastered`.
4. **Prerequisite Bottleneck & Leech Detours**: when misses point upstream or `srs_engine.py leeches` flags a card (`lapses >= 4`), run `graph_intel.py --bottlenecks --json`, run a 5-min micro-repair or Teach-Back split, then resume `next`.
5. **Doubts & Tangents**: `<= 40`-word non-spoiling reply when a check is open; `chapter_tracker.py pause-doubt` → full verified answer → `clear-doubt` for deep detours.

---

## Flow C — End-of-Chapter Auto-Lock-In (`build_revision_sheet` → `run_cbt_mock` → `chapter_complete`)

1. **`build_revision_sheet`**: `mistake_analytics.py export-traps` + `chapters/<slug>.md` → `cheatsheet_pdf.py build` (`chapters/<slug>-sheet.pdf`), inspect PNG via `read_file`, set `revisionSheetBuilt=true`.
2. **Optional Spoken Audio Brief**: `audio_brief.py --chapter chapters/<slug>.md --pause-sec 2.5` (`chapters/<slug>-audio.mp3`), set `audioBriefBuilt=true`.
3. **Timed Mastery Assessment / NTA JEE CBT (`run_cbt_mock`)**: compile verified timed assessment (`exam_runner.py build ... --out exams/<slug>-mock.html`), grade in 1 click under `syntopik serve` (or `exam_runner.py grade` offline), debrief the 3-bucket Knowledge/Execution/Time-Sink autopsy with `R0–R5` repair, set `cbtMockPassed=true`, and offer `backup_harness.py snapshot` or `syntopik export obsidian`.
