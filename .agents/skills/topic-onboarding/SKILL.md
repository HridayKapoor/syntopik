---
name: topic-onboarding
description: >-
  Onboard any topic, course, YouTube, arXiv, GitHub repo, web doc, or PDF via
  Teacher-First drop, starter demo packs, optional 2-min non-VARK calibration,
  90s adaptive placement, and bounded Scope Charters.
---

# Topic Onboarding — Universal 1-Command Import, Source Triage & Bounded Scout

Use this skill whenever a learner starts a brand-new topic, course, YouTube lecture, arXiv paper, GitHub repository, web doc, PDF, or exam unit — whether they supply a folder/URL or arrive with **zero study material**.

*(Portable CLI: prefer `syntopik <cmd>` / `syntopik run <engine>` in attached repos; direct clones also support `.venv/bin/python -B scripts/<engine>.py` or bare `python3 scripts/<engine>.py`.)*

---

## Calm Onboarding Laws, 3-Cadence Calibration & Plain-English Routing

1. **Zero CLI Memorization & 10-Second Start (`syntopik drop` / `syntopik demo`)**: absorb plain-English requests silently:
   - **Mixed local/URL files dropped together** → `syntopik drop <inputs...> [--slug <slug>] --json` (classifies Teacher-First spine vs problem bank vs references automatically, ingests safely, creates/updates tracker, and returns Probe #1!).
   - **Instant zero-PII starter packs** (*"Try a demo"* / *"Load starter pack"*) → `syntopik demo [--pack <slug|all>] [--open] --json` (loads bundled verified packs into `~/.syntopik-demo` without touching live ledgers).
   - *"Study this YouTube link / playlist"* → `syntopik import <youtube_url>` (caption-only, 5–8 min chunks + pause-points)
   - *"Walk me through arXiv 1706.03762"* → `syntopik import <arxiv_id_or_url>` (Keshav 3-Pass guide + optional PDF)
   - *"Help me understand this GitHub repo"* → `syntopik import <repo_url_or_path>` (Golden-Path Architecture Manifest)
   - *"Learn from these web docs / blog / notes"* → `syntopik import <url_or_path>` (Incremental Reading slices in `reading-queue.jsonl`)
   - *"Teach me `<topic>`"* (zero sources) → Mode B 3-question scoping + bounded research
2. **Tier-1 Optional 2-Min Non-VARK Onboarding Calibration & Tier-2 `<90s` Placement**:
   - **Tier 1 (First-time uncalibrated learner only, ALWAYS skippable)**: when `synt_next_move` returns `recommendedMode: "onboarding_calibration"`, offer a calm 1-turn non-VARK check (`entryRamp`: `intuition_first | balanced | formalism_first`, `challengeAppetite`: `guided | balanced | adversarial`, `domainFluency` `0..3`, `sessionMinutes`, `proseDensity`: `terse | standard | rich`) and persist via `syntopik profile calibrate --entry-ramp ... --challenge-appetite ... --json`. Prior weight decays automatically as $w_{\text{prior}}(N) = 6 / (6 + N)$ — NEVER ask VARK learning-style questions!
   - **Tier 2 (`<90s` Per-Chapter Adaptive Placement)**: before teaching a new chapter, run 2–3 max-information Pelánek 1PL/Elo probes (`syntopik run learner_profile place --domain <S> --chapter <slug> --json`, scoring each via `--score`) with automatic KG bypass on already-`solid` prerequisites ($R \ge 0.80$) and 2-probe early stop.
3. **Max 3 Questions in 1 Turn & Anti-Rabbit-Hole Cap (`<= 8` Curated Sources)**: never run a drawn-out intake interview; lock explicit `inScope` and `outOfScope` fences in `sources/_curated/<slug>/manifest.json` (`maxSources <= 8`), keep onboarding prose `<= 160`w with `1` clear decision, and fire Probe #1 immediately.

---

## Mode A — Sources Provided (YouTube, arXiv, GitHub, Web URL, PDF, or Folder)

### 1. 1-Command Universal Ingestion (`syntopik import` / `scripts/ingest_source.py`)
Auto-detect and normalize any external or local source into `sources/_ingested/<slug>/`:
```bash
# Auto-detect type (youtube | arxiv | repo | url | notes | pdf) & register tracker:
syntopik import "<target>" --slug <slug> --register-tracker --json
# Direct-clone equivalent:
python3 -B scripts/ingest_source.py auto "<target>" --slug <slug> --register-tracker --json
```
- **YouTube (`ingest_source.py youtube <url>`)**: fetches captions ONLY (never downloads audio/video!), chunks into 5–8 min sections with `[mm:ss]` backlinks and auto-placed **Active Pause-Point** checks in `sources/_ingested/<slug>/lecture.md`.
- **arXiv (`ingest_source.py arxiv <id_or_url> [--download-pdf]`)**: fetches Atom metadata, optionally downloads official PDF for `pdf_studio.py`, and scaffolds a **Keshav 3-Pass** study guide (`Pass 1: 5 Cs`, `Pass 2: Figures/Equations`, `Pass 3: Assumptions`).
- **GitHub / Local Repo (`ingest_source.py repo <path_or_url> [--clone]`)**: ignores `.git`/`node_modules`/`.venv`/binaries and emits a token-budgeted **Architecture & Golden-Path Manifest** (`repo-map.md` + `manifest.json` with entry points, core modules, and test files).
- **Web Docs / Articles (`ingest_source.py url <https_url>`)**: validates `http`/`https` strictly, extracts clean Markdown, and queues slices in `knowledge-graph/reading-queue.jsonl` (`ingest_source.py queue due|advance`).
- **Local PDFs / Folders**: run `syntopik run pdf_studio inspect "<path>"` (`< 3s`, reads `size_class` and `math_density` without dumping text) or `ingest_source.py notes "<path>"`.

### 2. Confirm Source Roles (Only If Ambiguous) & Lock Tracker
Use native question tools (`ask_question` / `AskUserQuestion` / `request_user_input` or 1 calm Markdown card) ONLY if unclear:
1. **Canonical `#1` Spine**: confirm ordering (**Teacher-First Law**: instructor lecture/whiteboard notes ALWAYS lead `p.1 → p.N` over secondary texts!).
2. **Target Depth & Profile**: `foundations` | `core` | `mastery` (`goalProfile`: `course` | `coding` | `research` | `jee` | `exam` | `general`).
3. **Gap-Fill Permission**: local files only, or allow bounded scholarly/web gap-fill?

Initialize tracker & scope, then hand off in the **same turn** to `chapter-autopilot`:
```bash
syntopik run chapter_tracker init <slug> --title "<Topic Title>" --subject "<Subject>" --source-dir "<path>" --set-active --json
syntopik run chapter_tracker set-scope <slug> --goal-profile <course|coding|research|jee|exam|general> --target-depth <foundations|core|mastery> --allow-web-research <true|false> --json
```

---

## Mode B — Zero or Partial Sources (*"Teach me Raft"*, *"Learn Transformers"*)

### Turn 1 — Single-Turn 3-Question Scoping Check
Ask at most **3** short questions together:
1. **Outcome & Depth**: what should you be able to *do* (`foundations` intuition, `core` university/production fluency, or `mastery` proof/research/exam rigor)?
2. **Baseline**: what adjacent foundations do you already hold comfortably?
3. **Material Mode**: curate a bounded pack (`<= 6` authoritative open lecture notes, papers, docs, exercises) via deep research, or teach purely via first-principles derivations + local verification?

### Turn 2 — Bounded 4-Tier Syllabus Scout, Scope Charter & Probe #1
1. **Run the Portable 4-Tier Research Cascade (`<= 8` Primary Sources)**:
   - **Tier 1 (`research` MCP active)**: `web_search` (`runtime_seconds: 60`, `result_detail: "compact"`) + `read_url` (`runtime_seconds: 60`) targeting canonical university syllabi, RFCs, primary papers, or official docs.
   - **Tier 2 (`syntopik run research_scout search "<topic> canonical syllabus lecture notes" --json`)**: portable `researchctl`.
   - **Tier 3 (Native Web Search + Keyless Scholarly APIs)**: `syntopik run research_scout scholar "<topic>" --sources arxiv,openalex,crossref,wikipedia --json`.
   - **Tier 4 (Pure Offline First-Principles)**: construct a verified milestone spine locally labeled `offline-constructed` (never fabricate URLs).
2. **Persist Scope Charter & Tracker**:
   ```bash
   syntopik run research_scout manifest <slug> \
     --title "<Topic Title>" --subject "<Subject>" \
     --in-scope "<core subtopics>" --out-of-scope "<exclusions>" \
     --sources-json '<curated <=8 sources JSON>' --json
   syntopik run chapter_tracker init <slug> --title "<Topic Title>" --subject "<Subject>" --set-active --json
   syntopik run chapter_tracker set-scope <slug> --goal-profile <general|course|coding|research|jee|exam> --target-depth <foundations|core|mastery> --allow-web-research true --in-scope "<...>" --out-of-scope "<...>" --json
   ```
   Seed `4–8` ordered `topics[]` (`basics` → `main` → `advanced`) via `chapter_tracker add-topics`.
3. **Show Compact Source Charter + Fire Probe #1**:
   Present a `<= 4`-row table of the locked spine + out-of-scope fence (`<= 120` words), open `synt_lesson_studio` if active, and fire **Diagnostic Probe #1** (`synt_quiz_card` or Tier 2/3 quiz tool) immediately!
