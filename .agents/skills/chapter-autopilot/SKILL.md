---
name: chapter-autopilot
description: >-
  Hands-free master topic and chapter orchestrator driving a learner from
  foundations to mastery across CS, ML, university courses, papers, STEM, or JEE
  with zero student logistics and zero repetition via scripts/chapter_tracker.py,
  graph_intel.py, daily_plan.py, audio_brief.py, and all specialist skills.
  Triggers on "Start [topic]", "Continue", "What's next?", or "I'm ready".
---

# Chapter Autopilot — Universal Hands-Free Foundations → Mastery Orchestrator

The learner's only job is thinking about the subject — whether Distributed Systems, Algorithms, Machine Learning, Mathematics, Physics, Chemistry, a research paper, or JEE prep. **100% of orchestration, source chunking, exercise tracking, spacing, interleaving, and deterministic verification happens silently behind the scenes.**

---

## Non-Negotiable Autopilot Laws

1. **Zero-Student-Logistics Law**: NEVER ask the learner to run CLI commands, remember script names, pick skills, track exercise IDs, or paste stage-specific prompts. Absorb every command silently and present only the next single calm learning interaction.
2. **Calm Anti-Overstimulation Pacing**: Keep teaching prose **`<= 160` words per turn** (target `90–130` words), strictly **`1` active demand per turn**, `<= 40` words on inline side-clarifications, and zero sycophantic praise.
3. **Zero-Repetition Invariant**: Every topic and every exercise/problem/PYQ is tracked in `chapters/<slug>-tracker.json` via `scripts/chapter_tracker.py`. Monotonic upserts (`add-questions`) NEVER reset `solved`/`mastered` back to `unseen`, and `next` NEVER re-serves an already `solved`/`mastered`/`skipped` item.
4. **Multi-Harness UX Adaptation**: Prefer `synt_quiz_card` / `synt_practice_card` / `synt_lesson_studio` (`mlh_*` aliases) on SEP-1865 UI hosts; fall back cleanly to native question/task tools (`ask_question` / `AskUserQuestion` / `request_user_input`, `update_steps` / `TaskCreate` / `update_plan`) or single calm Markdown cards (`BOOTSTRAP.md`).
5. **Real-Time Persistence Law (NEVER Defer to End of Session!)**: Update `knowledge-graph/propositions.jsonl`, `chains.jsonl`, `mistakes.jsonl`, `chapters/<slug>-tracker.json`, and Study Studio **immediately at every milestone**:
   - Right after Phase 1 Probing (`solid` floor nodes, `misconception`/`shaky` ceiling nodes, AND probe misses in `mistakes.jsonl`).
   - Right after Phase 2 Plan Lock (`planned` nodes + `chains.jsonl` edges + Topic 1 `in_progress` + top-down `prereq --> dependent` Mermaid DAG).
   - Immediately after EVERY missed check/question or mastered topic!
6. **Teacher-First Page-by-Page Priority Law (Zero Source Collision!)**: When the learner supplies instructor lecture/whiteboard PDFs or slides alongside reference books or problem sets (including JEE coaching modules or `scripts/eduniti_pyq.py`), **ALWAYS make the instructor's notes the `#1` canonical spine** in contiguous chronological order (`p.1 → p.N` / `Lec 1 → N`). Never scramble sequence around secondary texts or mix unrequested exam banks. If **no files** are supplied, invoke `.agents/skills/topic-onboarding/SKILL.md` (Mode B) first to lock a bounded Scope Charter!

---

## Flow A — Turn-1 New Topic/Chapter Kickoff (*"Start `<topic>` [from `<path>`]"*)

Execute cleanly so the learner starts thinking immediately with zero stall:

1. **Zero-Source Check**: If no files exist and no tracker exists yet, run `topic-onboarding` Mode B (`<= 3` scoping questions → bounded `research_scout.py manifest` → tracker init).
2. **Initialize Tracker (Zero-OCR Fast Metadata Triage)**:
   ```bash
   .venv/bin/python -B scripts/chapter_tracker.py init <slug> \
     --title "<Title>" --subject "<CS|ML|Maths|Physics|Chemistry|...>" \
     [--source-dir "<folder_or_path>"] --set-active --json
   ```
   - Supports arbitrary subjects + smart aliases (`CS`, `ML`, `Bio`, `Econ`, `Systems`, `Physics`, `Chemistry`, `Maths`), code/notebook files (`.py`, `.ts`, `.rs`, `.cpp`, `.ipynb`), slides, papers, and PDFs.
3. **Background Bounded-Window Source Indexing (NEVER Dump 70–150p PDFs Into Chat!)**:
   - Dispatch parallel background subagents (`invoke_subagent` / `Agent` / `spawn_agent`, or bounded chunks) over **15–20 page windows with 1-page overlap** (`[1..16], [16..31], ...`):
     - Text/mixed PDFs: `pdf_studio.py inspect` + `tree-index` + targeted `extract`/`tables`.
     - Scanned/whiteboard PDFs: inspect **3–5 rasterized pages per Vision step** (~150–200 DPI; 300 DPI for dense math/diagrams).
     - Codebases/problem sets: index modules, functions, test suites, and exercise IDs.
   - **Rolling Header Memory**: carry active parent headings (`H1/H2`) + last `qid` across chunk boundaries so multi-page sections and exercise banks merge without splitting or duplicating IDs.
   - Upsert `topics[]` (`basics`/`foundations` → `main`/`core` → `advanced`/`mastery`), deduplicated `questions[]`, `sources[].indexedRanges`, and `set-milestone <slug> sourcesIndexed --value true`.
4. **Start Live Probing in the SAME Turn — And Persist Immediately**:
   - Check `propositions.jsonl` & `mistakes.jsonl`, open `synt_lesson_studio` (`mlh_lesson_studio`) if active, and fire Probe #1 (`3–5` probes max).
   - In the **same turn** probing finishes: set `probed=true`, log Floor (`--rating good`) & Ceiling misses (`--rating again --misconception` + `mistake_analytics.py log`), seed `planned` nodes + `chains.jsonl`, push the Basics-at-Top (`prereq --> dependent`) DAG + `activeNodeId`, and initialize progress steps (`update_steps` / `TaskCreate` / `update_plan`).

---

## Flow B — Automatic Daily / Turn Loop (*"Continue"*, *"What's next?"*, *"I'm ready"*)

Silently query the deterministic state machine at turn start:
```bash
.venv/bin/python -B scripts/chapter_tracker.py next --include-warmup --json
```
*(If the learner gives a time budget like "I have 60 minutes", also run `.venv/bin/python -B scripts/daily_plan.py --minutes <N> --json`.)*

1. **Spaced Warmup (`warmupItems`, max `3`)**: weave `<= 3` due FSRS cues (`srs_engine.py next-cue`) before core work.
2. **`teach_topic` (Bloom Mastery Gate)**:
   - Read **ONLY** the active topic's `sourceRefs` pages/files (or bounded curated docs).
   - Crop & white-halo annotate key figures (`pdf_studio.py annotate`), render exact plots/diagrams/code traces inline (`visual-and-demo`), and **teach WITH named controls** in `<= 160` words.
   - Verify every claim deterministically (`math_verify.py`, `code_verify.py`, `chem_physical.py`, `chem_3d.py`) and run `Predict → Teach → Check`.
   - On unaided transfer check (~80–90%), run `srs_engine.py find-similar`, persist propositions + cues + `premises[] -> conclusion` hyperedges, and call `chapter_tracker.py set-topic <slug> <topic_id> --status mastered`.
3. **`solve_question` / `retest_question` (Rohrer Interleaving + `retestGap = 3`)**:
   - Interleave across `>= 2` mastered topics (`basics → main → advanced`); wait `stepCounter - lastStep >= retestGap` (`3–5` steps) before fresh-surface retests.
   - Verify `modelSolution` beforehand (`code_verify.py test-cases`/`complexity` for CS/ML; `math_verify.py`/`chem_physical.py` for STEM; `eduniti_pyq.py show <uid>` when using JEE PYQs) and pose via `synt_practice_card` (`mlh_practice_card`) or invite handwritten/code submission (`grade-handwriting`).
   - Log root errors via `mistake_analytics.py log`, update `srs_engine.py review`, and call `chapter_tracker.py mark-question <slug> <qid> --status solved|retest|mastered`.
4. **Prerequisite Bottleneck Detour**: when misses point to an earlier gap, run `scripts/graph_intel.py --bottlenecks --json`, run a 5-minute micro-repair, then resume `next`.
5. **Doubts & Tangents**: `<= 40`-word non-spoiling reply when a check is open; `chapter_tracker.py pause-doubt` → full verified answer → `clear-doubt` for deep detours.

---

## Flow C — End-of-Chapter Auto-Lock-In (`build_revision_sheet` → `run_cbt_mock` → `chapter_complete`)

1. **`build_revision_sheet`**: `mistake_analytics.py export-traps` + `chapters/<slug>.md` → `cheatsheet_pdf.py build` (`chapters/<slug>-sheet.pdf`), inspect PNG via `read_file`, set `revisionSheetBuilt=true`.
2. **Optional Spoken Audio Brief**: `audio_brief.py --chapter chapters/<slug>.md --pause-sec 2.5` (`chapters/<slug>-audio.mp3`), set `audioBriefBuilt=true`.
3. **Timed Mastery Assessment / NTA JEE CBT (`run_cbt_mock`)**: compile verified timed assessment (`exam_runner.py build ... --out exams/<slug>-mock.html` — profile-aware for general courses/CS/ML or full NTA JEE Main/Adv CBT), grade Attempt JSON via `exam_runner.py grade`, debrief top leaks with `R0–R5` repair, set `cbtMockPassed=true`, and offer `backup_harness.py snapshot`.
