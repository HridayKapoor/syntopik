# Changelog

All notable changes to **Syntopik** are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.0.0] - 2026-11-26

Major architectural and pedagogical release transforming **Syntopik** into a zero-clone, cross-IDE, 2-way local **Deep-Mastery Operating System** powered by 12 pure-stdlib `syntopik/` modules, 32 local deterministic engines, 17 skills, and a 17-tool MCP App while cutting Turn-0 context load by `-33.9%`.

### Added

#### Zero-Clone Distribution, Unified `syntopik` CLI & Cross-Harness Packaging
- **Unified `syntopik` CLI (`syntopik/cli.py`, `bin/syntopik.mjs`)**: 8 core verbs (`init`, `attach`, `connect`, `serve`, `import`, `review`, `doctor`, `export`) plus `triage`, `pack`, and `run <engine>` passthrough resolving bundled scripts from either a direct git clone or installed wheel/package.
- **Dual Zero-Clone Packaging (`pyproject.toml` + `package.json`)**:
  - `npx github:HridayKapoor/syntopik serve --open` (zero npm dependencies, pure ESM Node 20+ CLI).
  - `uvx --from git+https://github.com/HridayKapoor/syntopik syntopik serve --open` (`hatchling` wheel with `dependencies = []`, optional `fsrs`/`pdf`/`stem`/`all` extras, and `_bundle/` asset `force-include`).
- **Official Harness Plugins & Extensions**:
  - Claude Code Marketplace & Plugin manifests (`.claude-plugin/{marketplace.json,plugin.json,hooks.json}`) + plugin-cache-safe `.mcp.json` (`${CLAUDE_PLUGIN_ROOT:-.}`).
  - Official Gemini CLI extension (`gemini-extension.json`) for `gemini extensions install https://github.com/HridayKapoor/syntopik`.
  - Google Antigravity 2.0 global bundle installer (`syntopik connect antigravity` / `scripts/install_mcp_app.py install --bundle full`).
  - Non-destructive `syntopik attach <dir>` wiring thin pointer adapters & native MCP configs for Cursor (`.cursor/rules/syntopik.mdc`, `.cursor/mcp.json`), Windsurf (`.windsurf/rules/syntopik.md`), VS Code / Copilot (`.github/instructions/syntopik.instructions.md`, `.vscode/mcp.json` `"servers"`), OpenCode (`opencode.json` `"mcp"`), Codex, and Claude Code, writing local exclusions strictly to `.git/info/exclude` without overwriting host `AGENTS.md`/`CLAUDE.md`.

#### Core `syntopik/` Pure-Stdlib Package (`12` Modules) & Memory / FIRe / Optimizer Engine
- **Shared Core Infrastructure (`syntopik/{__init__,paths,storage,schemas}.py`)**: Plugin-cache-safe workspace resolution (`SYNT_WORKSPACE` → `$CLAUDE_PROJECT_DIR`/`cwd` → writable checkout → `~/.syntopik`), reentrant thread + cross-process temp-dir file locking, atomic JSON/JSONL writes, and joint/binary hyperedge schemas with default `encompassWeight`.
- **True FIRe + Spaced Repetition Compression (`syntopik/fsrs_core.py`, `scripts/srs_engine.py`)**: Pure-stdlib FSRS-6 v6 scheduling, Math Academy HSRS-style implicit prerequisite credit along `chains.jsonl` (depth-decayed, `(1 - R)` early-review discounted, never auto-clearing `misconception` nodes), and `due --compress` queue compression.
- **Personal FSRS-6 Optimizer, Calibration & Calm Queue Tools**: Zero-PyTorch SciPy L-BFGS-B parameter optimizer over `knowledge-graph/revlog.jsonl` gated on chronological out-of-sample Log-Loss improvement; rolling Brier/ECE/overconfidence/hypercorrection calibration; `recover --daily-cap 15` Calm Backlog Recovery; `forecast --exam-date`; `leeches` detector (`lapses >= 4` or `difficulty >= 8.5`); `--bury-siblings` 24h spacing; and native Cloze `{{c1::...}}` + SVG Image Occlusion Anki `.apkg` export.
- **Bare-Stdlib Graph Intelligence & Micro-Plans**: Pure-stdlib `StdlibDiGraph` (Tarjan SCC, Brandes, PageRank) fallback in `scripts/graph_intel.py` + `--confusables` discrimination pairs; `scripts/daily_plan.py --micro 5` and `[A, B, A']` interleaved contrast triplets; `scripts/pdf_studio.py annotate --occlude` declarative SVG occlusion masks.

#### 2-Way Live Local Studio Server (`syntopik serve`) & Upgraded Editorial Web UI
- **Zero-Dependency Local Studio Server (`syntopik/server.py`)**: Pure Python 3.12 `ThreadingHTTPServer` bound to `127.0.0.1`, per-process `X-Syntopik-Token` CSRF header enforcement, canonical path jail, live SSE `/api/events` sync, REST API (`/api/capabilities`, `/api/state`, `/api/review`, `/api/review/batch`, `/api/recover`, `/api/exam/submit`, `/api/tracker/*`, `/api/import`, `/api/export/*`, `/api/pack/*`, `/api/triage/*`, `/api/forks/check`, `/api/arena/submit`, `/api/blitz/grade`), and opt-in `--lan` pairing scoped strictly to `/pocket`, `/api/pocket/state`, and `POST /api/review/batch`.
- **Progressive Live UI (`dashboard.html`, `knowledge-graph/viewer.html`, `demos/exam-cbt-template.html`, `scripts/sync-graph.mjs` `schemaVersion: 4`)**:
  - `dashboard.html`: `● Live Studio` / `Offline` pill, SSE auto-refresh, `Low | Normal | Deep` Cognitive Energy selector, interactive Flashcard & clickable SVG Occlusion drawer with FIRe toasts, 365-day mineral-sage Retention Heatmap, Brier Calibration readout, 1-click Calm Catch-Up & Leech indicator, clickable Autopilot topic chips & milestones, 48h Triage / Anti-Mastery PDF / Pocket Deck / Study Pack actions, Sources Quick-Import bar + Incremental Reading queue, and inline `:::cue` / `{{c1::...}}` Mnemonic strips in Chapter Reader Drawer.
  - `knowledge-graph/viewer.html`: Live SSE mode, in-inspector FSRS ratings with FIRe toast, and `implicitCredit`, `lapses`, and `boundaryConditions[]` inspection.
  - `demos/exam-cbt-template.html` & `scripts/exam_runner.py`: Auto-POST `POST /api/exam/submit` in live mode ("Graded & Saved to Knowledge Graph ✓") + 3-Bucket Autopsy (`knowledge`, `execution`, `time_sink`, `egoTraps`, 3-strike escalations) + `good` FSRS credit on solved `propId`s, preserving `Download Attempt JSON` on `file://`.

#### Universal Source Ingestion & Bidirectional Obsidian / Mnemonic Medium Sync
- **1-Command Universal Importer (`syntopik/ingest.py`, `scripts/ingest_source.py`)**: Auto-detecting CLI (`auto`, `youtube`, `arxiv`, `repo`, `url`, `notes`, `queue add|due|advance`) producing caption-only YouTube lectures with `[mm:ss]` pause-points, arXiv Keshav 3-Pass guides, GitHub Golden-Path repo maps, strict `http://`/`https://` Web URL slices, and prioritized Incremental Reading queues (`knowledge-graph/reading-queue.jsonl`, tracker `slices[]`).
- **Bidirectional Obsidian & Mnemonic Sync (`syntopik/obsidian.py`, `scripts/vault_sync.py`)**: Non-destructive `export` (`<!-- syntopik:begin/end -->` markers, minimal YAML without noisy FSRS metadata, `[[wikilinks]]`, `Trap-MOC.md`) and idempotent `import` / `sync-notes` parsing `:::cue`, `{{c1::...}}` clozes, and `Q:: / A::` pairs with file/heading/line anchors.

#### Out-of-the-Box Mastery Modes, Moonshot Engines & Pedagogical Linting
- **Blunder FEN & Critical-Moment Fork Replay (`syntopik/forks.py`, `scripts/fork_replay.py`)**: Mid-derivation/mid-code blunder checkpoints in `knowledge-graph/forks.jsonl`, deterministic symbol/number mutation, repeat-blunder refutation without revealing `sealedWinningMove`, Woodpecker latency compression, and FSRS-6 scheduling.
- **Adversarial Counterexample Arena (`scripts/falsify_arena.py`)**: Server-sealed Conceptual Mutants (`knowledge-graph/arenas.jsonl`) adjudicated deterministically via isolated Python sandboxes (`-B -I`) and AST-safe math/physics evaluators with strict zero-leak survivor reporting and persistent **Lakatos Lemma Repair** into proposition `boundaryConditions[]`.
- **4-Min ShadowBox First-Move Blitz (`scripts/decision_blitz.py`)**: Interleaved lookalike cue-recognition drills (`Kill-Cue`, `First Move`, `Rejected Decoy`) with sealed Expert Lens diff and Newman Tier-1 `speedAccuracyZone` analytics (`knowledge-graph/decisions.jsonl`).
- **Zero-LLM TutorBench Pedagogical Linter (`scripts/pedagogy_eval.py`)**: `lint`, `check-card`, and 15-scenario `bench` verifying prose word limits (`<= 160`w), zero-sycophancy banlist, graded option length-parity (`<= 2.2×`), zero-leak options, single demand/turn, Pause Law, and non-spoiling hints.

#### Real-World Student-Life Tools
- **Zero-Guilt Cold-Start Re-Entry Brief & 3-State Energy Mode (`scripts/chapter_tracker.py`, `scripts/daily_plan.py`)**: `checkpoint --breadcrumb`, auto-absence detection (`daysAbsent >= 4`) suppressing scary overdue counts in favor of a 90-second 3-bullet Mental-Model Reload + warm-start cue (`0.60 <= R <= 0.85`), and `--energy low|normal|deep`.
- **48-Hour Exam Triage & Anti-Mastery 1-Page Crib Sheet (`scripts/daily_plan.py`, `scripts/cheatsheet_pdf.py`)**: `--triage-hours 48 --sleep-hours 7` Pareto ROI planner (`salvage_70pct`, `protect_20pct`, `explicitly_cut`) and `cheatsheet_pdf.py build --from-kg --anti-mastery --pages 1` auto-excluding rock-solid nodes (`R >= 0.90`, `lapses == 0`) from the A4 Typst sheet.
- **Zero-Install Offline Pocket Commute Deck & Pure-Stdlib QR (`syntopik/pocket.py`)**: ~20 KB zero-CDN mobile HTML5 swipe/tap review deck with inline SVG occlusion masks, `localStorage` queue, idempotent `pocket-sync.json` FIRe replay, and genuine pure-stdlib ISO/IEC 18004 Byte-Mode QR SVG/ASCII encoder.
- **Zero-PII Shareable Study Packs (`syntopik/packs.py`)**: SHA-256-verified `.syntopik-pack.zip`/`.json` packs stripping all personal mastery/FSRS/lapse/note fields, blocking zip-slip/symlinks/zip-bombs/tampering, and importing non-destructively (`merge` or `suspend-all` + `--probe`) without overwriting existing `solid` mastery.

#### Syntopik Studio MCP App v2.0 (`17` Tools) & Plugin-Cache-Safe Hooks
- **3 New Ambient Headless MCP Tools**: `synt_capture_concept`, `synt_get_frontier`, and `synt_log_review` (bringing `syntopik-studio` to **17 tools total** while keeping `com.google.antigravity/ui` `entrypoints` exclusively on `synt_lesson_studio`).
- **Adaptive Parsons / Faded Practice Cards & SVG Occlusion**: `synt_practice_card` now supports `mode: "standard" | "parsons" | "faded"` with server-sealed `canonicalOrder`, stripped `isDistractor`, keyboard-accessible block reordering, and zero-leak terminal fallback; `synt_lesson_studio` Flashcards tab renders workspace-jailed SVG occlusion masks and FIRe refresh toasts.
- **Plugin-Cache-Safe Hooks**: `.agents/hooks/{session-start,post-graph-sync}.mjs` never write into read-only plugin caches and watch `revlog.jsonl` and `reading-queue.jsonl`.

### Changed
- **Turn-0 Context-Budget Diet (`-33.9%` Load)**:
  - Trimmed `AGENTS.md` (`4,376 B`) + `GEMINI.md` (`2,172 B`) to **`6,548 B` combined** (`-29.1%` vs v1.0.0 `9,236 B`), `CLAUDE.md` to `2,403 B`, and all 17 `.agents/skills/*/SKILL.md` YAML descriptions to `3,219 B` (`-45.3%`, zero script filenames or `<...>` angle brackets), cutting total Antigravity Turn-0 load from `15,962 B` to **`10,551 B`**.
  - Upgraded all 17 existing skills with v2.0 workflows (Productive Failure, 3-turn Protégé Teach-Back, BKT-gated Parsons/Faded, Surgical Bug-Hunt, FIRe/SRC, 3-Bucket Exam Autopsy, `pedagogy_eval`, and portable `syntopik` CLI invocations) without adding a single new skill directory.
- **CI & Health Doctor (`scripts/study_doctor.py`, `.github/workflows/ci.yml`)**: Expanded health checks and CI gates to cover all 12 `syntopik/` modules, 32 `scripts/` engines, `bin/syntopik.mjs`, `pyproject.toml`, `package.json`, `.claude-plugin/*.json`, `gemini-extension.json`, and new gitignored ledgers.

---

## [1.0.0] - 2026-11-26

Initial public flagship release of **Syntopik** — the local-first, harness-native AI Deep-Mastery Operating System.

### Added

#### Multi-Harness Portability & Fresh-Clone Bootstrap
- **Universal Harness Bindings**: First-class native integration for **Google Antigravity 2.0** (`AGENTS.md` + `GEMINI.md`, `.agents/skills/`, `.agents/hooks.json`), **Anthropic Claude Code** (`CLAUDE.md` importing `@AGENTS.md`, `.claude/skills`, `.claude/settings.json`, `.mcp.json`), **OpenAI Codex / T3 Code** (`AGENTS.md`, `.codex/config.toml`, `.codex/hooks.json`), and **Any Agent** (`BOOTSTRAP.md` with 4-axis UX capability matrix).
- **Tri-Protocol Lifecycle Hooks**: `.agents/hooks/session-start.mjs` and `.agents/hooks/post-graph-sync.mjs` auto-detect Antigravity (`injectSteps`) vs. Claude Code / Codex (`hookSpecificOutput.additionalContext`), inject a concise Turn-0 study snapshot, and keep `knowledge-graph/graph-data.js` synced in real time across `SYNT_*` (and backwards-compatible `MLH_*`) workspace overrides.
- **Non-Destructive Auto-Bootstrap (`scripts/setup_harness.py`)**: Initializes missing gitignored learner directories and empty ledgers on fresh clones in `< 2s`, repairs Windows `.claude/skills` stubs, and never overwrites existing study data.

#### Universal Any-Subject Onboarding, Pedagogy & 17 Skills
- **Calm Anti-Overstimulation Operating System**: Enforces `<= 160` words of teaching prose per turn, `1` active cognitive demand per turn, dry zero-sycophancy feedback, Probe Frontier Summary, `Predict -> Teach -> Check` cycles, bare chance-calibrated graded choices with `"I don't know"`, and mandatory Post-Conclusion Pause Law.
- **17 Progressive-Disclosure Skills (`.agents/skills/` & `.claude/skills/`)**: `topic-onboarding`, `chapter-autopilot`, `teach-chapter`, `practice-drill`, `grade-handwriting`, `exam-simulator`, `universal-pdf-study`, `ingest-chapter`, `visual-and-demo`, `revision-sheets`, `knowledge-graph`, `physics-circuits-and-units`, `physical-chemistry`, `organic-chemistry-tutor`, `manim-animator`, `subagent-orchestrator`, and `harness-self-improve`.
- **Teacher-First & Anti-Rabbit-Hole Topic Onboarding (`scripts/research_scout.py`)**: Locks instructor lecture/whiteboard notes as the `#1` spine when provided, or runs a max-3-question scoping interview to curate bounded `sources/_curated/<slug>/manifest.json` charters (`maxSources <= 8`, `inScope`/`outOfScope` fences) via a portable 4-tier cascade (`research` MCP -> `researchctl` -> keyless arXiv/OpenAlex/Crossref/Wikipedia -> offline) with strict `http://`/`https://` URL validation.
- **Hands-Free Chapter Autopilot (`scripts/chapter_tracker.py`)**: Subject-agnostic mastery state machine (`chapters/<slug>-tracker.json`) supporting CS, ML, Systems, University Courses, Papers, STEM, and JEE with zero-repetition invariants, doubt parking (`pause-doubt`/`clear-doubt`), spaced retest cooling, and profile-aware milestone labels.

#### 26 Local Deterministic Engines (`scripts/`)
- **CS, Algorithms & Systems (`scripts/code_verify.py`)**: Pure-stdlib sandboxed runner (`python`, `node`, `cpp`, `c`, `rust`, `sh`), structured `test-cases` (function `--entry` including unexported JS functions, stdin/stdout, and `--reference-code` differential mode), empirical `O(1)`..`O(2^N)` complexity classifier (`python` & `node`), and static Python `ast-check`.
- **Mathematics, Physics & Circuits**: AST-safe `math_verify.py` (SymPy + Pint + Uncertainties), `math_plot.py` (5 SymPy static SVG & zero-CDN interactive Canvas labs), `physics_draw.py` (7 parametric mechanics/optics/EM presets incl. prism TIR & inclined projectiles), `circuit_draw.py` (Schemdraw), and `eduniti_pyq.py`.
- **Physical & Organic Chemistry**: `chem_physical.py` (exact charge-balance pH, redox conservation, equilibrium, Nernst, thermo), `chem_3d.py` (RDKit 2D/3D MMFF94 conformers + offline `py2opsin` IUPAC-to-SMILES + CIP `(R)/(S)` & `(E)/(Z)` + zero-CDN 3D viewer), and `organic_reagents.py` (40 RDKit-validated reagents in `knowledge-graph/reagents.json`).
- **PDFs, Revision Sheets, Manim, Memory & Timed Exams**: `pdf_studio.py` (`math_density` triage, `tree-index`, `tables`, RapidOCR, white-halo `annotate`), `cheatsheet_pdf.py` (Typst 1–2p A4 PDFs), `manim_helpers.py` + `narrate_video.py` (neural `edge-tts` + Pillow PNG caption overlay), `srs_engine.py` (lexical `find-similar`, multi-cue FSRS-6 + BKT, Visual Anki `.apkg`), `graph_intel.py` (NetworkX hypergraph bottlenecks, memorized islands, cycle-safe paths, cross-chapter combos), `mistake_analytics.py` (13-category Newman/Rohrer & CS/coding taxonomy), `daily_plan.py`, `audio_brief.py`, `exam_runner.py` + `demos/exam-cbt-template.html`, `backup_harness.py` (SHA-256 + zip-slip protection), and `study_doctor.py` (`--core-only` & full diagnostics).

#### Two-Way `Syntopik Studio` MCP App & Offline Calm Editorial UI
- **SEP-1865 `Syntopik Studio` MCP App (`mcp-app/mlh-studio/`)**: Pre-bundled, zero-CDN interactive **Quiz Cards**, **Practice Cards** (server-sealed `modelSolution`, 1-active gate), and 5-tab Side-Pane **Study Studio** (`Autopilot`, glowing SVG `Plan`, `Graph`, `Flashcards`, `Traps`) exposing `synt_*` tools (plus `mlh_*` aliases), disk-backed monotonic state persistence, and automatic terminal non-UI anti-leak Markdown fallback.
- **Calm Editorial Dark & Light Apps**: *"Warm Obsidian & Vellum Ink"* (Dark) and *"Archival Vellum & Graphite"* (Light) palettes across `dashboard.html` (7-tab Study Hub + offline Markdown/LaTeX Chapter Reader Drawer + keyboard shortcuts), `knowledge-graph/viewer.html` (joint-premise `premises[] -> conclusion` hypergraph), and `demos/exam-cbt-template.html`.

#### Release Engineering, Packaging & CI
- GitHub Actions CI matrix (`.github/workflows/ci.yml`) validating fresh-clone bootstrap, `--core-only` health doctor, strict `py_compile`/`node --check` syntax gates, pure-stdlib sandboxed regression tests, and public privacy/config hygiene on `ubuntu-latest` and `macos-latest`.
- `requirements-stem.txt` optional STEM specification, structured GitHub Issue & PR templates, `SECURITY.md`, MIT `LICENSE`, and sandboxed 34-test regression suite (`tests/test_harness.py`).
