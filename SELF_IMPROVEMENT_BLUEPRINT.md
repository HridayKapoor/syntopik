# Syntopik — Master Architecture, Build Log & Autonomous Self-Improvement Blueprint

> **Purpose of this file:**
> 1. **Complete Ground-Truth Record** of everything built, audited, and verified in **Syntopik** (`<workspace>`, formerly *MY LEARNING HARNESS*) through **Phase 5: Syntopik v2.0.0 — Zero-Clone Distribution, 2-Way Live Local Studio, True FIRe + Optimizer, Universal Ingestion, Obsidian Sync, Moonshot Mastery Arenas & Student-Life Engines** (2026-11-26).
> 2. **Executable Self-Improvement Specification & Curated `v2.1` Roadmap** for **Google Antigravity 2.0**, **Anthropic Claude Code**, **OpenAI Codex / T3 Code**, Cursor, Windsurf, Copilot, Gemini CLI, and OpenCode. Reference `@SELF_IMPROVEMENT_BLUEPRINT.md` with `harness-self-improve` so Syntopik audits itself, implements bounded upgrades, verifies every invariant in `/tmp`, and evolves safely.

---

## 1. Non-Negotiable Architecture & Safety Invariants

Any self-improvement pass **MUST** preserve every invariant below:

1. **100% Official, Harness-Native, Zero-Ban-Risk Execution**:
   - Everything runs natively inside **Google Antigravity 2.0**, **Anthropic Claude Code**, **OpenAI Codex (CLI / Desktop / T3 Code)**, **Cursor**, **Windsurf**, **VS Code / Copilot**, **Gemini CLI**, **OpenCode**, or a local loopback browser using official workspace/plugin primitives (`AGENTS.md`, `GEMINI.md`, `CLAUDE.md`, `BOOTSTRAP.md`, `.agents/skills/*/SKILL.md`, `.claude-plugin/*.json`, `gemini-extension.json`, `.mcp.json`, `.codex/config.toml`, `pyproject.toml`, `package.json`).
   - **Never** scrape OAuth tokens, wrap proprietary CLIs in unauthorized consumer proxies, or spoof client identity.
2. **Zero Heavyweight Local AI / ML Models**:
   - Multimodal Vision (handwritten solutions, diagrams, dense equations) and deep reasoning run on the host harness's frontier model and subagents.
   - **Never install** multi-GB local VLM/ML/TTS/Docker stacks (`PyTorch`, `Docling`, `MinerU`, `Marker`/`Surya`, `olmOCR`, `MolScribe`, `DECIMER`, `kokoro-onnx`, `piper-tts`, `DeepTutor`/`OpenTutor`).
   - Keep `syntopik/` **100% pure Python 3.12+ stdlib** (`dependencies = []`) and keep optional `.venv/` fast, deterministic, and lightweight (CAS math, SciPy L-BFGS-B, cheminformatics, vector rendering, RapidOCR, Typst/FFmpeg glue).
3. **Multi-Harness Context Budget & Portable Skill Hygiene**:
   - `GEMINI.md` and `AGENTS.md` have **no YAML frontmatter**, stay **under 24 KB per file**, and stay **strictly `<= 9,500` bytes combined** (`<= 10,000 B` ceiling enforced by `study_doctor.py` and `tests/test_harness.py`; v2.0.0 baseline is **`6,548 B`**, `-29.1%` vs v1.0.0).
   - `CLAUDE.md` starts with `@AGENTS.md` on line 1 (avoiding Claude Code's `AGENTS.md` masking trap) and stays `< 45` lines (`21` lines in v2.0.0) with zero frontmatter.
   - Every `.agents/skills/<name>/SKILL.md` MUST have portable YAML frontmatter (`name` matching folder name + concise `description <= 210` chars target / `<= 1,024` hard spec with **zero literal `<...>` angle brackets** and **zero script filenames** inside `description`; v2.0.0 total across all 17 skills is `3,219 B`, total Antigravity Turn-0 load `10,551 B`, `-33.9%` vs v1.0.0).
4. **GitHub-Clean Learner Privacy, Plugin-Cache Safety & Cross-Platform Portability**:
   - Personal learner state (`knowledge-graph/{propositions,chains,revlog,mistakes,reading-queue,forks,arenas,decisions,exam-attempts}.jsonl`, `study-config.json`, `fsrs-params.json`, `blitz-sessions.json`, `review-queue.md`, `graph-data.js`, `chapters/*.md`, `chapters/*-tracker.json`, `exams/*.html`, generated `demos/*.html`, `sources/*`, `.syntopik/`, `*.syntopik-pack.zip`) is **gitignored** and auto-bootstrapped on fresh clones — **never** tracked in Git or overwritten if already present.
   - Hooks and CLI commands MUST resolve workspaces safely (`explicit` → `SYNT_WORKSPACE || MLH_WORKSPACE` → `$CLAUDE_PROJECT_DIR`/`cwd` w/ `knowledge-graph/` or `.syntopik/` → writable checkout → `~/.syntopik`), **never** writing into read-only plugin/extension caches.
   - `syntopik attach <dir>` MUST NEVER overwrite an existing host `AGENTS.md`/`CLAUDE.md` and writes local exclusions to `.git/info/exclude` (zero diff to host `.gitignore`).
   - **Zero hardcoded machine paths** (zero personal user-home or Homebrew prefix paths in tracked files, verified by `TestGitHubHygieneAndPortability`); resolve system binaries via `shutil.which(...)` first.
5. **Local Server, Archive & Sandbox Security Boundaries**:
   - `syntopik serve` (`syntopik/server.py`) binds strictly `127.0.0.1` by default, rejects non-loopback `Host`/`Origin`, requires per-process `X-Syntopik-Token` CSRF header on all `POST`/`PATCH` routes, and enforces canonical path-jailing. Opt-in `--lan` scopes pairing cookies **exclusively** to `/pocket`, `/api/pocket/state`, and `POST /api/review/batch`.
   - `syntopik/packs.py` strips all personal FSRS/BKT/lapse/note fields, embeds SHA-256 `SHA256SUMS.json`, and blocks zip-slip, symlinks, zip bombs, and tamper mismatches.
   - `scripts/falsify_arena.py` never leaks surviving mutants' IDs, source code, expressions, or `trapTag`s; `syntopik/forks.py` never leaks `sealedWinningMove` on repeat blunders unless explicitly requested (`--reveal-fix`).
6. **Known Dependency & Platform Landmines (DO NOT REGRESS)**:
   - **NEVER install `mendeleev`**: pins `pint<0.25.0`, breaking `pint==0.26.2`, and drags `pandas` + `SQLAlchemy`. Use `molmass`.
   - **NEVER run bare `uv pip install chempy`**: drags ~70 Jupyter packages via `pyodesys`. Always install `quantities` + `uv pip install --no-deps chempy`.
   - **NEVER install `manim-voiceover` / `manim-physics`**: use `scripts/narrate_video.py` (`edge-tts` + optional macOS `say` + `ffmpeg`).
   - **FFmpeg without `libass`**: keep Pillow transparent-PNG `overlay` caption burn-in in `scripts/narrate_video.py`.
   - **`edge-tts` v7.x API**: use `submaker.feed(chunk)` and `submaker.get_srt()`.
   - **`pymupdf4llm` fd-1 parser chatter**: always suppress stdout around `pymupdf4llm` calls so `--json` CLI output stays strictly parseable.
   - **TinyTeX subshell PATH**: import `scripts/manim_helpers.py` (`ensure_tinytex_in_path()`).
7. **Strict Verification & Clean-Workspace Gate**:
   - Every code/skill change MUST pass:
     1. `.venv/bin/python -B scripts/study_doctor.py` AND bare `python3 -B scripts/study_doctor.py --core-only` → `0 warn, 0 fail`
     2. `py_compile` (`SyntaxWarning` as error, compiled to `/tmp` so zero `__pycache__` is created) across `syntopik/*.py`, `scripts/*.py`, and `tests/*.py` + `node --check` on `bin/syntopik.mjs`, hooks, `sync-graph.mjs`, and `mcp-app/mlh-studio/dist/server.mjs`
     3. `python3 -B scripts/pedagogy_eval.py bench --json` → `15/15 PASS`
     4. `.venv/bin/python -B -m unittest discover -s tests -v` → `100% PASS` (100% sandboxed in `tempfile.TemporaryDirectory()`)
     5. Visual PNG / headless browser inspection (`0` console errors via both `http://127.0.0.1` and `file://`, zero external CDNs) for any UI/PDF change
     6. Zero personal or machine-specific paths (`TestGitHubHygieneAndPortability`) and clean `git status`.

---

## 2. Complete Record of Everything Built & Verified

### 2.1 Milestone History
| Commit / Phase | Milestone |
| :--- | :--- |
| `baf8f15`–`4e70aee` | **Core STEM OS Foundation:** Python 3.12 `.venv`, `pdf_studio.py`, `math_verify.py`, `chem_physical.py`, `chem_3d.py`, `circuit_draw.py`, `manim_helpers.py` + `narrate_video.py`, `cheatsheet_pdf.py`, `srs_engine.py`, `study_doctor.py`, and offline `dashboard.html` + `viewer.html`. |
| `2cd9af6` | **P0.1–P0.4 Upgrade:** `mistake_analytics.py`, `harness-self-improve` + `harness-log.jsonl`, offline NTA JEE CBT simulator `exam_runner.py` + `demos/exam-cbt-template.html`, and sandboxed `tests/test_harness.py`. |
| `f906ff6` | **P1.1–P1.4 Upgrade:** `physics_draw.py`, 40-reagent RDKit `organic_reagents.py` + `reagents.json`, `grade-handwriting` (FERMAT/VLMath R0–R2), and `math_plot.py`. |
| `81ad640` | **LearnHarness Parity + SEP-1865 Studio MCP App:** Dry zero-sycophancy voice, Probe Frontier Summary, `Predict → Teach → Check`, Pause Law, inline `<agent-embed>`, `pdf_studio.py` `math_density` + `annotate`, `srs_engine.py` `find-similar` + multi-cue FSRS-6, `premises[] -> conclusion` hyperedges, and `mcp-app/mlh-studio/`. |
| `fb44afe` | **Hands-Free Chapter Autopilot + P2/P3 Study OS:** `chapter_tracker.py` + `chapter-autopilot`, `graph_intel.py`, Visual Anki `media`, `audio_brief.py`, `daily_plan.py`, `dashboard.html` Reader Drawer + shortcuts, SHA-256 `backup_harness.py`, and Context Footprint doctor. |
| **Phase 4 (`v1.0.0`, 2026-11-26)** | **Universal Multi-Harness & Any-Subject Release (`Syntopik`):** GitHub-clean learner data architecture + `setup_harness.py` auto-bootstrap; tri-protocol hooks & configs across Antigravity 2.0, Claude Code, Codex/T3 Code, and `BOOTSTRAP.md`; `code_verify.py`, `research_scout.py`, and `topic-onboarding`; Sweller calm ceilings (`<= 160`w/turn) & Warm Obsidian / Archival Vellum Dark/Light palette. |
| **Phase 5 (`v2.0.0`, 2026-11-26)** | **Zero-Clone Distribution, 2-Way Live Local Studio, True FIRe + Optimizer & Mastery Arenas:** Introduced `12` pure-stdlib `syntopik/` modules (`paths`, `storage`, `schemas`, `fsrs_core`, `ingest`, `obsidian`, `forks`, `packs`, `pocket`, `server`, `cli`), `6` new scripts (`32` total: `ingest_source.py`, `vault_sync.py`, `pedagogy_eval.py`, `fork_replay.py`, `falsify_arena.py`, `decision_blitz.py`), dual zero-clone packaging (`pyproject.toml` `uvx` + `package.json`/`bin/syntopik.mjs` `npx`), official `.claude-plugin/*` + `gemini-extension.json` + `syntopik attach` cross-IDE adapters, 2-way `127.0.0.1` Studio server w/ CSRF/SSE/opt-in `--lan` `/pocket`, True FIRe + SRC + SciPy L-BFGS-B personal FSRS-6 optimizer + Brier calibration + Calm Catch-Up, Cold-Start Re-Entry + Cognitive Energy Mode, 48h Exam Triage + Anti-Mastery 1p Typst crib sheet, ~20 KB offline Pocket HTML5 deck + pure-stdlib QR, Zero-PII `.syntopik-pack.zip`, 17-tool MCP App (3 ambient headless tools + Parsons/Faded cards + SVG occlusion), and `-33.9%` Turn-0 context diet (`AGENTS.md + GEMINI.md = 6,548 B`). |

---

### 2.2 Pure-Stdlib Core Package (`syntopik/` — 12 Modules, Zero Mandatory Dependencies)

| Module | Role & Key Capabilities |
| :--- | :--- |
| `syntopik/__init__.py` | Package version (`2.0.0`) and metadata. |
| `syntopik/paths.py` | Package/`_bundle` root resolution, plugin-cache-safe workspace resolution, `SYNT_*`/`MLH_*` path helpers, portable binary lookup, `sync_hub()`. |
| `syntopik/storage.py` | Reentrant thread + cross-process lock in OS temp dir, atomic JSON/JSONL/text read & write helpers (`tmp` + `os.replace`). |
| `syntopik/schemas.py` | Joint/binary hyperedge normalization + `encompassWeight` defaults (`0.50` / `0.35` / `0.0`), 13 mistake categories, subject/level normalization, slugify/timestamps. |
| `syntopik/fsrs_core.py` | Pure-stdlib FSRS-6 v6 math, confidence-aware BKT, Brier/ECE/hypercorrection calibration, True FIRe + SRC queue compression, Calm Backlog Recovery, Retention Forecasting, Leech Detection, Sibling Burying, lazy SciPy L-BFGS-B OOS-gated optimizer. |
| `syntopik/ingest.py` | Universal source ingestion (YouTube caption-only + `[mm:ss]` pause-points, arXiv Keshav 3-Pass, GitHub/local Golden-Path repo map, strict `http`/`https` Web URL slicing, notes/PDFs) + prioritized Incremental Reading queue (`reading-queue.jsonl`). |
| `syntopik/obsidian.py` | Bidirectional Obsidian vault sync (`<!-- syntopik:begin/end -->` markers, minimal YAML, `[[wikilinks]]`, `Trap-MOC.md`) + Matuschak Mnemonic Medium `:::cue` / `{{c1::...}}` / `Q:: A::` parser. |
| `syntopik/forks.py` | Blunder FEN & Critical-Moment Fork Replay (`forks.jsonl`), deterministic symbol/numeric mutation, repeat-blunder refutation without fix leak, Woodpecker latency compression. |
| `syntopik/packs.py` | Zero-PII shareable `.syntopik-pack.zip`/`.json` packs (`STRIPPED_PERSONAL_FIELDS`, SHA-256 `SHA256SUMS.json`, zip-slip/symlink/zip-bomb guards, non-destructive ID+lexical dedup). |
| `syntopik/pocket.py` | ~20 KB zero-CDN mobile HTML5 commute deck (inline SVG occlusion, `localStorage` queue, `pocket-sync.json`), idempotent FSRS+FIRe sync replay, pure-stdlib ISO/IEC 18004 Byte-Mode QR encoder (SVG/ASCII). |
| `syntopik/server.py` | Zero-dep Python 3.12 `ThreadingHTTPServer` 2-way local Studio (`127.0.0.1`, CSRF token, path jail, SSE `/api/events`, full REST API, opt-in `--lan` scoped pairing for `/pocket`). |
| `syntopik/cli.py` | Unified `syntopik` CLI (`init`, `attach`, `connect`, `serve`, `import`, `review`, `triage`, `pack`, `doctor`, `export`, `run <engine>`). |

---

### 2.3 Complete Script Inventory (`scripts/` — 32 Scripts: 31 Python + 1 Node)

| Script | Subcommands / Role |
| :--- | :--- |
| `scripts/setup_harness.py` | `auto`, `status`, `init-data`, `configure`, `attach`, `install-mcp` — Cross-platform `< 2s` workspace bootstrapper (`schemaVersion: 4`), Windows `.claude/skills` repair. |
| `scripts/srs_engine.py` | `add`, `review` (`--confidence`, `--duration-ms`, `--no-fire`), `due` (`--compress`, `--bury-siblings`), `recover`, `forecast`, `optimize`, `leeches`, `find-similar`, `add-cue`, `next-cue`, `export-anki` (Cloze + SVG IO), `push-anki`, `stats`. |
| `scripts/graph_intel.py` | `--bottlenecks`, `--islands`, `--path-to`, `--combos`, `--confusables`, `--all` — Pure-stdlib `StdlibDiGraph` fallback + NetworkX hypergraph intelligence. |
| `scripts/daily_plan.py` | `--minutes`, `--micro 5`, `--energy low\|normal\|deep`, `--resume` (Cold-Start Re-Entry Brief), `--triage-hours 48 --sleep-hours 7` (Pareto Exam Triage), FIRe warmup + `[A,B,A']` contrast triplets. |
| `scripts/chapter_tracker.py` | `init`, `set-scope`, `add-source`, `add-topics`, `set-topic`, `add-slices`, `set-slice`, `checkpoint --breadcrumb`, `add-questions`, `mark-question`, `set-milestone`, `set-active`, `pause-doubt`, `clear-doubt`, `next`, `status`. |
| `scripts/ingest_source.py` | `auto`, `youtube`, `arxiv`, `repo`, `url`, `notes`, `queue add\|due\|advance` — Universal ingestion CLI & Incremental Reading queue. |
| `scripts/vault_sync.py` | `export`, `import`, `sync-notes` — Bidirectional Obsidian vault & Mnemonic Medium (`:::cue`, `{{c1::...}}`) sync CLI. |
| `scripts/pedagogy_eval.py` | `lint`, `check-card`, `bench` — Zero-LLM TutorBench pedagogical linter & 15-scenario golden benchmark. |
| `scripts/fork_replay.py` | `capture`, `due`, `check`, `mutate`, `stats` — Blunder FEN & Critical-Moment Fork Replay CLI. |
| `scripts/falsify_arena.py` | `create`, `list`, `test`, `repair` — Adversarial Counterexample Arena (Examplar conceptual mutants + Lakatos `boundaryConditions[]` repair). |
| `scripts/decision_blitz.py` | `build`, `grade`, `stats` — 4-Min ShadowBox First-Move & Cue-Recognition Blitz + Newman Tier-1 analytics. |
| `scripts/code_verify.py` | `run`, `test-cases`, `complexity`, `ast-check` — Sandboxed CS/algorithms/ML runner, hidden test-cases, empirical $O(1)..O(2^N)$ fitter, static AST check. |
| `scripts/research_scout.py` | `status`, `search`, `read`, `scholar`, `manifest` — 4-tier portable research adapter & bounded Scope Charter (`maxSources <= 8`). |
| `scripts/mistake_analytics.py` | `log` (`--confused-with`), `summary` (3-strike `escalations`), `export-traps` — 13-category Newman/Rohrer & CS/coding mistake tracker. |
| `scripts/exam_runner.py` | `schema`, `build`, `grade` (in-process `grade_attempt`, `good` credit on solved `propId`s, 3-bucket `knowledge`/`execution`/`time_sink` autopsy + `egoTraps`). |
| `scripts/pdf_studio.py` | `inspect` (`math_density`), `tree-index`, `extract`, `tables`, `render`, `search`, `annotate` (white-halo PNG + `--occlude` SVG occlusion JSON). |
| `scripts/cheatsheet_pdf.py` | `build` (`--from-kg --anti-mastery --pages 1|2`), `schema` — Offline `< 150 ms` Typst compiler for Mastery, Pitfall & Anti-Mastery Crib Sheet PDFs. |
| `scripts/math_verify.py` | `eval`, `check-eq`, `solve`, `small-osc`, `units`, `check-dim` (AST-safe), `limit`, `series`, `ode`, `vector`, `matrix`, `complex`, `error-prop`. |
| `scripts/math_plot.py` | `conic`, `argand`, `integral`, `vector3d`, `func` — Exact SymPy static SVG/PNG or zero-CDN interactive Canvas HTML labs. |
| `scripts/physics_draw.py` | `fbd-incline`, `pulley`, `optics-lens`, `optics-mirror`, `optics-prism` (auto-TIR), `field-charges`, `projectile`. |
| `scripts/circuit_draw.py` | `preset` (`rc`, `rl`, `lcr`, `wheatstone`, `divider`, `kirchhoff`), `spec` — Schemdraw circuits, logic gates, and transistors. |
| `scripts/chem_physical.py` | `balance`, `stoich`, `formula`, `equil`, `ph` (exact charge-balance), `nernst`, `thermo`, `element`. |
| `scripts/chem_3d.py` | `inspect`, `render2d`, `render3d` — RDKit 2D/3D MMFF94 conformers, offline `py2opsin` IUPAC→SMILES, CIP `(R)/(S)`/`(E)/(Z)`. |
| `scripts/organic_reagents.py` | `list`, `lookup`, `compare --out-grid`, `selectivity`, `drill-pair` over 40 RDKit-validated reagents (`knowledge-graph/reagents.json`). |
| `scripts/eduniti_pyq.py` | `chapters`, `stats`, `list`, `show`, `export-chapter` — Local JEE Main Physics PYQ extractor. |
| `scripts/manim_helpers.py` | Portable TinyTeX PATH injector + `CHEM_TEX_TEMPLATE` + zero-TeX math fallbacks. |
| `scripts/narrate_video.py` | Neural `edge-tts` / offline TTS voiceover + `.srt` + Pillow transparent-PNG `--burn-subs` compositor. |
| `scripts/audio_brief.py` | `--chapter` / `--cards`, `--pause-sec 2.5` — Spoken `Q → recall pause → A` revision `.mp3`/`.m4a` generator. |
| `scripts/backup_harness.py` | `snapshot`, `verify`, `restore` — SHA-256 manifest-verified `.tar.gz` backup & zip-slip-safe restore. |
| `scripts/install_mcp_app.py` | `install` (`--bundle studio|full`), `uninstall`, `status` — Zero-`node_modules` installer for Studio MCP or full global Antigravity plugin bundle. |
| `scripts/study_doctor.py` | `--core-only`, `--json` — Cross-platform `< 500 ms` health doctor checking all 12 `syntopik/` modules, 32 scripts, manifests, hooks, and context budgets. |
| `scripts/sync-graph.mjs` | `--open`, `--graph`, `--quiet`, `--json` — Compiles `graph-data.js` (`schemaVersion: 4`: `retentionHeatmap`, `calibration`, `leeches`, `readingQueue`, trackers, exams, inline Markdown). |

---

### 2.4 Skills, Hooks, 17-Tool MCP App & Packaging Summary
- **17 Portable Skills (`.agents/skills/`, mirrored at `.claude/skills`, `3,219 B` total YAML descriptions)**: `topic-onboarding`, `chapter-autopilot`, `teach-chapter`, `practice-drill`, `grade-handwriting`, `exam-simulator`, `universal-pdf-study`, `ingest-chapter`, `visual-and-demo`, `revision-sheets`, `knowledge-graph`, `physics-circuits-and-units`, `physical-chemistry`, `organic-chemistry-tutor`, `manim-animator`, `subagent-orchestrator`, `harness-self-improve`.
- **Plugin-Cache-Safe Tri-Protocol Hooks (`.agents/hooks/`, `.claude-plugin/hooks.json`, `.claude/settings.json`, `.codex/hooks.json`)**: Auto-detect Antigravity vs Claude Code / Codex, resolve workspaces without writing into read-only plugin caches, and sync `revlog.jsonl` & `reading-queue.jsonl`.
- **17-Tool SEP-1865 Studio MCP App (`mcp-app/mlh-studio/`)**: `synt_quiz_card`, `synt_practice_card` (`standard` / `parsons` / `faded` with sealed `canonicalOrder` + stripped `isDistractor`), `synt_lesson_studio` (SVG image occlusion + FIRe toast), 4 internal studio tools, 3 ambient headless tools (`synt_capture_concept`, `synt_get_frontier`, `synt_log_review`), plus 7 backwards-compatible `mlh_*` aliases. Single `com.google.antigravity/ui` `entrypoints` ONLY on `synt_lesson_studio`.

---

### 2.5 Regression Watchlist (Never Regress)
1. `manim_helpers.py`: normalize `\Big|`/`\text{}`/`\implies` in fallback `LaTeXMath`; resolve TinyTeX portably across macOS & Linux.
2. `circuit_draw.py`: keep Wheatstone resistor labels outside diamond.
3. `pdf_studio.py`: suppress `pymupdf4llm` fd-1 stdout chatter; prioritize `.venv`'s `DejaVuSans-Bold.ttf` in `annotate`.
4. `math_verify.py` & `math_plot.py`: strict AST/safe-namespace parsing (refuse `__import__`); farthest-pair `(bestA, bestB)` line clipping.
5. `syntopik/fsrs_core.py` & `srs_engine.py`: scale BKT `p_transit` by rating; FIRe MUST NEVER auto-clear `misconception` nodes; SciPy optimizer accepts ONLY on chronological OOS Log-Loss improvement; convert `$...$` to `\\(...\\)` for Anki MathJax.
6. `narrate_video.py`: escape `%%` in `argparse` help; keep Pillow PNG overlay burn-in for libass-free FFmpeg.
7. `sync-graph.mjs` & `session-start.mjs`: exclude `*-template.html` from demo counts; auto-bootstrap missing `graph-data.js`; never write into read-only plugin/extension caches.
8. `exam_runner.py` & `exam-cbt-template.html`: treat non-finite tokens (`NaN`, `inf`, `-inf`, `.`, `-`) as `unattempted`; preserve `Download Attempt JSON` on `file://` with zero console errors while auto-POSTing in live `syntopik serve` mode.
9. `mcp-app/mlh-studio`: keep `[hidden] { display: none !important; }` in `common.css`, disk-backed `state-store.mjs`, zero personal home paths, single `entrypoints` on `synt_lesson_studio`, and strict server-side sealing of `modelSolution`, `canonicalOrder`, and `isDistractor`.
10. `CLAUDE.md` & `SKILL.md`: keep `@AGENTS.md` on line 1 of `CLAUDE.md`; keep `AGENTS.md + GEMINI.md <= 9,500 B` (`6,548 B` baseline); keep literal `<...>` and script filenames out of `SKILL.md` YAML `description`.
11. `syntopik/server.py`, `packs.py`, `falsify_arena.py`, `forks.py`: enforce `127.0.0.1` + CSRF token + path jail + `--lan` route scoping (`/pocket` + `/api/pocket/state` + `POST /api/review/batch` only); strip all PII fields and reject zip-slip/symlinks in `packs.py`; never leak surviving mutants in `falsify_arena.py` or `sealedWinningMove` on repeat blunders in `forks.py`.

---

## 3. Completed Backlog (`P0` → `P5` — 100% Landed & Verified)

- [x] **P0.1–P0.4** (`2cd9af6`): Mistake taxonomy, closed-loop `harness-self-improve`, offline NTA JEE CBT simulator, sandboxed `tests/test_harness.py`.
- [x] **P1.1–P1.4** (`f906ff6`): Parametric `physics_draw.py`, 40-reagent RDKit `organic_reagents.py`, `grade-handwriting` R0–R2 protocol, SymPy `math_plot.py`.
- [x] **LearnHarness Parity** (`81ad640`): Dry live co-pilot pedagogy, inline `<agent-embed>`, `pdf_studio.py` `math_density` + `annotate`, multi-cue FSRS-6, `premises[]` hyperedges, SEP-1865 Studio MCP App.
- [x] **P2–P3 + Chapter Autopilot** (`fb44afe`): `chapter_tracker.py`, NetworkX `graph_intel.py`, Visual Anki `media`, `audio_brief.py`, `daily_plan.py`, Reader Drawer + shortcuts, SHA-256 `backup_harness.py`, Context Footprint doctor.
- [x] **Phase 4 (`v1.0.0`, 2026-11-26)**: Syntopik rebrand, GitHub-clean separation + `setup_harness.py`, multi-harness configs, `code_verify.py`, `research_scout.py`, `topic-onboarding`, Sweller calm ceilings, Warm Obsidian / Archival Vellum UI.
- [x] **Phase 5 (`v2.0.0`, 2026-11-26)**:
  - **P5.1 Core & Packaging**: 12 pure-stdlib `syntopik/` modules, unified `syntopik` CLI (`pyproject.toml` `uvx` + `package.json`/`bin/syntopik.mjs` `npx`), `.claude-plugin/*`, `gemini-extension.json`, `syntopik attach` cross-IDE adapters, global Antigravity bundle.
  - **P5.2 Memory & 2-Way Studio**: True FIRe + SRC, SciPy L-BFGS-B personal FSRS-6 optimizer, Brier/Hypercorrection calibration, Calm Backlog Recovery, `syntopik serve` (`127.0.0.1` CSRF + SSE + live 2-way `dashboard.html`/`viewer.html`/`exam-cbt-template.html` w/ 100% `file://` fallback).
  - **P5.3 Ingestion, Obsidian & Context Diet**: `syntopik import` (YouTube, arXiv Keshav 3-Pass, GitHub Golden-Path, Web URL IR queue), bidirectional Obsidian / Mnemonic Medium `:::cue` sync, 17-tool MCP App (3 ambient headless tools + Parsons/Faded + SVG occlusion), `pedagogy_eval.py` TutorBench linter, `-33.9%` Turn-0 context diet (`AGENTS.md + GEMINI.md = 6,548 B`).
  - **P5.4 Mastery Arenas & Student-Life Engines**: Blunder FEN Fork Replay (`forks.py`/`fork_replay.py`), Adversarial Counterexample Arena (`falsify_arena.py`), 4-Min ShadowBox First-Move Blitz (`decision_blitz.py`), Cold-Start Re-Entry Brief + Cognitive Energy Mode, 48h Exam Triage + Anti-Mastery 1p Typst Crib Sheet, ~20 KB Offline Pocket Commute HTML + Opt-In LAN QR (`pocket.py`), Zero-PII Shareable Study Packs (`packs.py`).

---

## 4. Curated `v2.1` Roadmap

All six initiatives below are verified, zero-ML-weight, calm/anti-overstimulation, and fold into the existing 17 skills without creating skill bloat:

| Priority | Initiative | Core Mechanism & Research Lineage | Target Modules / Skills |
| :---: | :--- | :--- | :--- |
| **#1** | **`:::explore` Bret Victor Tangle + Mahajan Limiting-Case Lab** | Declarative 10-line `:::explore` blocks inside `chapters/*.md` compiled via `math_verify.py` into a safe zero-`eval` JS/SVG runtime: scrubbing an inline parameter updates concrete values, a Ladder-of-Abstraction global sweep curve, and a Regime-Break Peeler splitting approximation vs exact curves, gated by an optional pre-scrub Mahajan `sp.limit` prediction. | `syntopik/explorable.py`, `dashboard.html` Reader Drawer, `visual-and-demo`, `teach-chapter` |
| **#2** | **Structural Isomorphism Rosetta Engine** | Gentner Structure-Mapping + Paynter Bond Graphs: canonize symbolic structures across subjects (e.g., damped SHM ↔ RLC ↔ PID ↔ Momentum SGD; Dijkstra ↔ Viterbi ↔ DAG DP) into `isomorphic_to` chords with explicit `breaksAt` negative-transfer guards, bijection prediction openers, and cross-domain FIRe credit (`0.25`). | `syntopik/isomorphism.py`, `syntopik/data/archetypes.json`, `graph_intel.py`, `viewer.html`, `topic-onboarding` |
| **#3** | **Clean-Room Derivation Reconstruction & Axiom-Cut Surgery** | Persist key derivations as Causal Step DAGs (`knowledge-graph/derivations.jsonl`) for two drills: (1) FSRS-paced Progressive Blackout accepting any algebraically valid alternate path via `math_verify`/`code_verify`, and (2) Axiom-Cut Surgery (*delete one assumption, locate the earliest step that breaks, state what survives*). | `scripts/derivation_forge.py`, `synt_practice_card` (`mode: "blackout"`), `teach-chapter`, `practice-drill` |
| **#4** | **Adversarial Viva Voce Defense Chamber** | Deterministic 5-turn Commitment-Ledger oral/text examiner grounded in highest-centrality nodes and overconfident `revlog.jsonl` misses: pins Turn-1 claims to a Commitment Ledger, perturbs assumptions in Turns 2–4 with CAS/code anti-bluff verification, scores 4 rubric axes, and demotes fragile `solid` nodes back to `shaky` (optional zero-weight Web Speech API). | `scripts/viva_chamber.py`, `POST /api/viva/turn`, `exam-simulator`, `teach-chapter` |
| **#5** | **Post-Exam "Semester Sunset" Graph-Centrality Pruning** | `syntopik sunset <slug>` snapshots finished courses to `chapters/_archived/*.syntopik-pack.zip`, uses `graph_intel.py` out-degree + cross-chapter reach to retire ~80% leaf trivia (`archived: true`), and lowers the ~20% load-bearing axioms to `0.80` maintenance retention (~65% review-load reduction) without erasing sequel-course foundations. | `syntopik/cli.py`, `scripts/graph_intel.py`, `scripts/srs_engine.py`, `dashboard.html`, `knowledge-graph` |
| **#6** | **Multi-Course Buffer-Protected Syllabus Auto-Pacer + `.ics` Export** | `syntopik pace` extends `study-config.json` to multiple weighted exams with a 20% guilt-free slack buffer (`--rebalance` silently absorbs missed days into the next buffer slot) and exports pure-stdlib RFC-5545 `.ics` focus blocks & exam milestones for Apple/Google Calendar with zero OAuth. | `scripts/daily_plan.py`, `syntopik/cli.py`, `dashboard.html`, `chapter-autopilot` |

---

## 5. Autonomous Self-Improvement Protocol

1. **Baseline Gate**: Run `.venv/bin/python -B scripts/study_doctor.py --json`, `python3 -B scripts/study_doctor.py --core-only --json`, `python3 -B scripts/pedagogy_eval.py bench --json`, `.venv/bin/python -B -m unittest discover -s tests -v`, and `git status --short`.
2. **Evidence Audit**: Inspect `knowledge-graph/harness-log.jsonl`, `mistakes.jsonl`, `revlog.jsonl`, `propositions.jsonl`, `exam-attempts.jsonl`, and this blueprint.
3. **Bounded Scope**: Select 1–3 high-leverage upgrades or friction fixes preserving all Section 1 invariants (`GEMINI.md + AGENTS.md <= 9,500 B`, pure-stdlib `syntopik/`, zero heavy ML, zero personal paths).
4. **Parallel Implementation + Read-Only `/tmp` Verification**: Use disjoint file ownership across subagents/swarm workers + an independent read-only verifier testing strictly in `/tmp` (including fresh-clone simulation, `syntopik serve` CSRF/path-jail checks, and `0`-console-error headless Chrome checks on both `http://127.0.0.1` and `file://`).
5. **Record & Commit**: Append 1 JSON line to `knowledge-graph/harness-log.jsonl`, update this blueprint, verify zero personal/Homebrew paths via `TestGitHubHygieneAndPortability` and zero `__pycache__`, and land one clean git commit.
