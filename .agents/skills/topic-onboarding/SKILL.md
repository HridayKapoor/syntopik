---
name: topic-onboarding
description: >-
  Onboard any new topic, university course, research paper, codebase, textbook,
  or exam subject — with attached files (Mode A: silent source triage and
  Teacher-First spine selection) or zero/partial material (Mode B: calm max
  3-question scoping interview, bounded anti-rabbit-hole deep research via
  scripts/research_scout.py, Scope Charter manifest, and tracker init).
---

# Topic Onboarding — Universal Source Triage & Bounded Syllabus Scout

Use this skill whenever a learner starts a brand-new topic, course, paper, repository, or exam unit — whether they supply a full folder of files or arrive with **zero study material** — or asks what sources to study from.

---

## Calm Onboarding Laws (Zero Friction, Zero Rabbit Holes)

1. **Max 3 Questions in 1 Turn**: Never run a drawn-out multi-turn intake interview. Ask at most `2–3` concise scoping questions in a single turn, lock the Scope Charter, and begin diagnostic probing immediately on the next turn.
2. **Anti-Rabbit-Hole Cap (`<= 8` Curated Sources)**: Bounded research must lock explicit `inScope` and `outOfScope` fences in `sources/_curated/<slug>/manifest.json` (`maxSources <= 8`) so web scouting never wanders into tangential blog posts or endless reading lists.
3. **Calm Turn Ceiling**: Keep onboarding prose `<= 160` words with `1` clear decision/approval action.

---

## Mode A — Sources Provided (PDFs, Slides, Notes, Papers, or Code Repo)

When the learner attaches or points to local files (`sources/`, folder path, or codebase):

1. **Silent Non-Blocking Triage (`< 3s`)**:
   - For PDFs: run `.venv/bin/python -B scripts/pdf_studio.py inspect "<path>"` (reads page counts, `size_class`, and `math_density` without dumping raw text).
   - For folders / code repos: inspect filenames, READMEs, syllabus files, lecture numbering, and problem banks.
2. **Detect Overlaps & Confirm Source Roles (Max 2–3 Crisp Questions, Only If Ambiguous)**:
   - Use your harness's structured question tool (`ask_question` / `AskUserQuestion` / `request_user_input` or one compact Markdown card) ONLY if roles or depth are unclear:
     1. **Canonical `#1` Spine**: Confirm which source sets topic order (**Teacher-First Law**: instructor lecture/whiteboard notes ALWAYS lead `p.1 → p.N` over secondary reference books!).
     2. **Target Depth & Profile**: `foundations` | `core` | `mastery` (`goalProfile`: `course` | `coding` | `research` | `jee` | `exam` | `general`).
     3. **Gap-Fill Permission**: Strictly local files only, or allow bounded web/scholarly lookup when a prerequisite is missing?
   - *(If the learner already stated clear intent — e.g., "Start SHM from this folder using my whiteboard notes" — skip straight to Step 3 with zero bureaucratic questions!)*
3. **Initialize Tracker, Scope Charter & Start Probing**:
   ```bash
   .venv/bin/python -B scripts/chapter_tracker.py init <slug> \
     --title "<Topic Title>" --subject "<Subject>" --source-dir "<path>" --set-active --json
   .venv/bin/python -B scripts/chapter_tracker.py set-scope <slug> \
     --goal-profile <course|coding|research|jee|exam|general> \
     --target-depth <foundations|core|mastery> --allow-web-research <true|false> --json
   ```
   Hand off seamlessly in the **same turn** to `.agents/skills/chapter-autopilot/SKILL.md` (background bounded-chunk indexing + Phase 1 diagnostic probe).

---

## Mode B — Zero or Partial Sources (*"Teach me Raft"*, *"Learn Transformers from scratch"*)

When the learner names a topic with no local files (or asks you to find authoritative material):

### Turn 1 — Single-Turn 3-Question Scoping Check
Ask at most **3** short questions together (`ask_question` / `AskUserQuestion` / `request_user_input` or a compact numbered card):
1. **Outcome & Depth**: What should you be able to *do* at the end (`foundations` intuition, `core` university/production fluency, or `mastery` proof/research/exam rigor)?
2. **Baseline**: What adjacent foundations do you already hold comfortably?
3. **Material Mode**: Should I curate a bounded pack (`<= 6` authoritative open lecture notes, papers, docs, and exercises) via deep research, or teach purely via interactive first-principles derivations + local verification?

### Turn 2 — Bounded 4-Tier Syllabus Scout, Approval Card & First Probe
Once the learner replies:

1. **Execute the Portable 4-Tier Research Cascade (Strictly Bounded, `<= 8` Sources)**:
   - **Tier 1 (`research` MCP active)**: call `web_search` (`runtime_seconds: 60`, `result_detail: "compact"`) + `read_url` (`runtime_seconds: 60`) targeting canonical university syllabi, RFCs/specs, primary papers, or official documentation.
   - **Tier 2 (`scripts/research_scout.py` -> portable `researchctl`)**:
     ```bash
     .venv/bin/python -B scripts/research_scout.py search "<topic> canonical syllabus lecture notes exercises" --json
     ```
   - **Tier 3 (Built-in Web Search + Keyless Scholarly APIs)**: combine native web search (`search_web` / `WebSearch` / `web_search`) with:
     ```bash
     .venv/bin/python -B scripts/research_scout.py scholar "<topic>" --sources arxiv,openalex,crossref,wikipedia --json
     ```
   - **Tier 4 (Pure Offline First-Principles)**: construct a verified milestone spine locally and explicitly note `offline-constructed` (never fabricate URLs).
2. **Persist the Bounded Scope Charter & Initialize Tracker**:
   ```bash
   .venv/bin/python -B scripts/research_scout.py manifest <slug> \
     --title "<Topic Title>" --subject "<Subject>" \
     --in-scope "<comma-separated core subtopics>" \
     --out-of-scope "<comma-separated rabbit-hole exclusions>" \
     --sources-json '<curated <=8 sources JSON>' --json

   .venv/bin/python -B scripts/chapter_tracker.py init <slug> \
     --title "<Topic Title>" --subject "<Subject>" --set-active --json
   .venv/bin/python -B scripts/chapter_tracker.py set-scope <slug> \
     --goal-profile <general|course|coding|research|jee|exam> \
     --target-depth <foundations|core|mastery> --allow-web-research true \
     --in-scope "<...>" --out-of-scope "<...>" --json
   ```
   Seed `4–8` ordered `topics[]` (`basics` → `main` → `advanced`) via `chapter_tracker.py add-topics`.
3. **Show Compact Source Charter + Fire Probe #1**:
   Present a `<= 4`-row table of the locked spine + explicit out-of-scope fence (`<= 120` words), open `synt_lesson_studio` (`mlh_lesson_studio`) if available, and fire **Diagnostic Probe #1** (`synt_quiz_card` / `mlh_quiz_card` or Tier 2/3 quiz tool) right away!
