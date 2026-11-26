# Syntopik — Master Architecture, Build Log & Autonomous Self-Improvement Blueprint

> **Purpose of this file:**
> 1. **Complete Ground-Truth Record** of everything built, audited, and verified in **Syntopik** (`<workspace>`, formerly *MY LEARNING HARNESS*) through **Phase 6: Syntopik v3.0.0 — Adaptive Learner Psychology, Pedagogical Brainstem, Causal Epistemics, Playable 6-Station Mastery Gym, Live Chalkboard, Safe `:::explore` Labs, Work Mentor, `Syntopik-Eval` Benchmarks & 3-OS CI** (2026-11-26).
> 2. **Executable Self-Improvement Specification & Curated `v3.1+` Roadmap** for **Google Antigravity 2.0**, **Anthropic Claude Code**, **OpenAI Codex / T3 Code**, Cursor, Windsurf, Copilot, Gemini CLI, and OpenCode. Reference `@SELF_IMPROVEMENT_BLUEPRINT.md` with `harness-self-improve` so Syntopik audits itself, implements bounded upgrades, verifies every invariant in `/tmp`, and evolves safely.

---

## 1. Non-Negotiable Architecture & Safety Invariants

Any self-improvement pass **MUST** preserve every invariant below:

1. **100% Official, Harness-Native, Zero-Ban-Risk Execution**:
   - Everything runs natively inside **Google Antigravity 2.0**, **Anthropic Claude Code**, **OpenAI Codex (CLI / Desktop / T3 Code)**, **Cursor**, **Windsurf**, **VS Code / Copilot**, **Gemini CLI**, **OpenCode**, or a local loopback browser using official workspace/plugin primitives (`AGENTS.md`, `GEMINI.md`, `CLAUDE.md`, `BOOTSTRAP.md`, `.agents/skills/*/SKILL.md`, `.claude-plugin/*.json`, `gemini-extension.json`, `.mcp.json`, `.codex/config.toml`, `pyproject.toml`, `package.json`).
   - **Never** scrape OAuth tokens, wrap proprietary CLIs in unauthorized consumer proxies, or spoof client identity.
2. **Zero Heavyweight Local AI / ML Models**:
   - Multimodal Vision (handwritten solutions, diagrams, dense equations) and deep reasoning run on the host harness's frontier model and subagents.
   - **Never install** multi-GB local VLM/ML/TTS/Docker stacks (`PyTorch`, `Docling`, `MinerU`, `Marker`/`Surya`, `olmOCR`, `MolScribe`, `DECIMER`, `kokoro-onnx`, `piper-tts`, `DeepTutor`/`OpenTutor`).
   - Keep `syntopik/` **100% pure Python 3.12+ stdlib** (`dependencies = []`, `20` modules) and keep optional `.venv/` fast, deterministic, and lightweight (CAS math, SciPy L-BFGS-B, cheminformatics, vector rendering, RapidOCR, Typst/FFmpeg glue).
3. **Multi-Harness Context Budget & Portable Skill Hygiene**:
   - `GEMINI.md` and `AGENTS.md` have **no YAML frontmatter**, stay **under 24 KB per file**, and stay **strictly `<= 9,500` bytes combined** (unified ceiling enforced by `study_doctor.py`, CI, and `tests/test_harness.py`; v3.0.0 baseline is **`7,281 B`**, `AGENTS.md = 4,757 B` + `GEMINI.md = 2,524 B`).
   - Both `CLAUDE.md` (`21` lines `< 45`, `2,809 B`) and `GEMINI.md` start with `@AGENTS.md` on line 1 with zero frontmatter.
   - Exact **17 skills** in `.agents/skills/<name>/SKILL.md` (mirrored at `.claude/skills/`): portable YAML frontmatter (`name` matching folder name + concise `description <= 215` chars target / `<= 1,024` hard spec with **zero literal `<...>` angle brackets** and **zero `.py`/`.mjs` script filenames** inside `description`; total `<= 3,400 B`).
4. **GitHub-Clean Learner Privacy, Plugin-Cache Safety & 3-OS Windows Portability**:
   - Personal learner state (`knowledge-graph/{propositions,chains,revlog,mistakes,reading-queue,forks,arenas,decisions,exam-attempts,derivations,evidence-ledger,viva-sessions}.jsonl`, `study-config.json`, `fsrs-params.json`, `blitz-sessions.json`, `learner-profile.json`, `whiteboard-session.json`, `review-queue.md`, `graph-data.js`, `chapters/*.md`, `chapters/*-tracker.json`, `exams/*.html`, generated `demos/*.html`, `sources/*`, `.syntopik/`, `*.syntopik-pack.zip` except `!packs/*.syntopik-pack.zip`) is **gitignored** and auto-bootstrapped on fresh clones — **never** tracked in Git or overwritten if already present.
   - Hooks and CLI commands MUST resolve workspaces safely (`explicit` → `SYNT_WORKSPACE || MLH_WORKSPACE` → `$CLAUDE_PROJECT_DIR`/`cwd` w/ `.syntopik/` or `knowledge-graph/` → writable checkout → `~/.syntopik`), **never** writing into read-only plugin/extension caches or leaking ephemeral `_npx` paths.
   - Tracked hook/MCP configs MUST remain **100% Windows-safe** (zero inline POSIX `SYNT_WORKSPACE=`, `$(git rev-parse ...)`, or `${CLAUDE_PLUGIN_ROOT:-.}` syntax; use `--workspace` flags and cross-platform Node/Python invocations).
   - `syntopik attach <dir>` MUST NEVER overwrite an existing host `AGENTS.md`/`CLAUDE.md`, preserves existing host hooks when merging `.claude/settings.json`, and writes local exclusions strictly to `.git/info/exclude`.
   - **Zero hardcoded machine paths** (zero personal user-home or Homebrew prefix paths in tracked files); resolve system binaries via `shutil.which(...)`.
5. **Local Server, Archive, AST & Sandbox Security Boundaries**:
   - `syntopik serve` (`syntopik/server.py`) binds strictly `127.0.0.1` by default, enforces RFC-3986 `Host`/`Origin` and per-process `X-Syntopik-Token` CSRF on all `POST`/`PATCH` routes, and enforces canonical path-jailing (`/api/demo/load` allowlist regex + canonical `is_relative_to(packs_dir)`). Opt-in `--lan` scopes pairing cookies **exclusively** to `/pocket`, `/api/pocket/state`, and `POST /api/review/batch` (returning `403` on `/api/profile*`, `/api/whiteboard*`, `/api/state`, `/api/next-move`, `/api/root-cause`).
   - `syntopik/packs.py` enforces **fail-closed PII stripping & smuggle rejection** for `learner-profile.json`, `whiteboard-session.json`, `evidence-ledger.jsonl`, `viva-sessions.jsonl`, `blitz-sessions.json`, and `exam-attempts.jsonl`, embeds `SHA256SUMS.json`, and blocks zip-slip, symlinks, zip bombs, and tampering.
   - `syntopik/explorable.py` & `dashboard.html` use a strict AST-whitelist RPN compiler/evaluator with **zero `eval()` / `new Function()`**; `syntopik/work_mentor.py` rejects `-` flag injection with `shell=False`.
   - Zero secret leaks in `GET /api/state`, `sync-graph.mjs`, and MCP responses (`sealedWinningMove`, surviving arena mutants, ungraded blitz expert answers, `blackout` hidden steps, locked whiteboard `prediction_gate` answers).
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
     1. `.venv/bin/python -B scripts/study_doctor.py` AND bare `python3 -B scripts/study_doctor.py --core-only` → `0 warn, 0 fail` (rule budget `<= 9,500 B`)
     2. `py_compile` (`SyntaxWarning` as error, compiled to `/tmp` so zero `__pycache__` is created) across `syntopik/*.py`, `scripts/*.py`, `benchmarks/*.py`, and `tests/*.py` + `node --check` on `bin/syntopik.mjs`, hooks, `sync-graph.mjs`, and `mcp-app/mlh-studio/dist/server.mjs`
     3. `python3 -B scripts/pedagogy_eval.py bench --json` → **`40/40 PASS`**
     4. `python3 -B benchmarks/syntopik_bench.py --days 60 --seed 42 --check --json` → **`7/7` ablation gates PASS**
     5. `python3 -B scripts/build_starter_packs.py --verify-only --json` → **`3/3` zero-PII starter packs PASS**
     6. `.venv/bin/python -B -m unittest discover -s tests -v` + bare-stdlib unittest suite → **`100% PASS`** (100% sandboxed in `tempfile.TemporaryDirectory()`)
     7. Visual PNG / headless browser inspection (`0` console errors via `http://127.0.0.1` and `file://`, zero external CDNs, single-row topbars at `1280×800`) for any UI/PDF change
     8. Zero personal or machine-specific paths and 3-OS CI green (`ubuntu-latest`, `macos-latest`, `windows-latest`).

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
| **Phase 5 (`v2.0.0`, 2026-11-26)** | **Zero-Clone Distribution, 2-Way Live Local Studio, True FIRe + Optimizer & Mastery Arenas:** Introduced `12` pure-stdlib `syntopik/` modules, `32` scripts, dual `uvx`/`npx` packaging, `.claude-plugin/*` + `gemini-extension.json` + `syntopik attach`, 2-way `127.0.0.1` Studio server w/ CSRF/SSE/opt-in `--lan` `/pocket`, True FIRe + SRC + SciPy L-BFGS-B personal FSRS-6 optimizer + Brier calibration + Calm Catch-Up, Blunder Forks, Counterexample Arena, ShadowBox Blitz, 48h Triage + Anti-Mastery PDF, ~20 KB Pocket HTML + QR, Zero-PII Study Packs, 17-tool MCP App, and Turn-0 context diet. |
| **Phase 6 (`v3.0.0`, 2026-11-26)** | **Personal AI Teacher & Cognitive Mastery OS:** Expanded to **`20` pure-stdlib `syntopik/` modules** (`learner_model`, `root_cause`, `evidence`, `brainstem`, `explorable`, `work_mentor`, `whiteboard`, `sim_eval`), **`36` deterministic `scripts/` engines** (`learner_profile.py`, `derivation_forge.py`, `viva_chamber.py`, `build_starter_packs.py`), **`21`-tool MCP App v3.0** (`synt_next_move`, `synt_whiteboard_step`, `synt_app_update_profile`, `blackout` cards), `schemaVersion: 5` Playable 6-Station Mastery Gym + Live AI Chalkboard SSE + safe zero-`eval` `:::explore` Slider Labs + Causal Suspect Halos & T1–T4 Badges, Anti-Vibe-Coding Work Mentor (`syntopik debrief`/`mode`), Semester Sunset (`syntopik sunset`), Multi-Course `.ics` Auto-Pacer (`syntopik pace`), **`40/40` TutorBench**, **`7/7` `Syntopik-Eval` 60-day ablation gates** (`+31.5%` reviews saved @ `94.8%`/`89.2%` retention, `-27.6%` Brier, `-86.5%` ER mismatch, `98.6%` post-absence, `90.4%` T3/T4 transfer), **3 curated zero-PII starter packs**, 10 new CLI verbs + all 5 P0/P1 CLI/Windows/multi-harness fixes, **3-OS CI (`ubuntu-latest`, `macos-latest`, `windows-latest`)** with wheel & `npm pack` E2E tests, GitHub Pages (`pages.yml`), and OIDC Trusted Publishing (`publish.yml`), all within `AGENTS.md + GEMINI.md = 7,281 B <= 9,500 B`. |

---

### 2.2 Pure-Stdlib Core Package (`syntopik/` — 20 Modules, Zero Mandatory Dependencies)

| Module | Role & Key Capabilities |
| :--- | :--- |
| `syntopik/__init__.py` | Package version (`3.0.0`) and metadata. |
| `syntopik/paths.py` | Package/`_bundle` root resolution, `--vault local` split-brain-safe & plugin-cache-safe workspace resolution, `SYNT_*`/`MLH_*` helpers, portable binary lookup, `sync_hub()`. |
| `syntopik/storage.py` | Reentrant thread + cross-process lock in OS temp dir, atomic JSON/JSONL/text read & write helpers (`tmp` + `os.replace`). |
| `syntopik/schemas.py` | Joint/binary hyperedge normalization + `encompassWeight` defaults (`0.50` / `0.35` / `0.0`), 13 mistake categories, subject/level normalization, slugify/timestamps. |
| `syntopik/fsrs_core.py` | Pure-stdlib FSRS-6 v6 math, confidence-aware BKT, Brier/ECE/hypercorrection calibration, True FIRe + SRC queue compression, Calm Backlog Recovery, Retention Forecasting, Leech Detection, Sibling Burying, lazy SciPy L-BFGS-B OOS-gated optimizer. |
| `syntopik/learner_model.py` | 3-Cadence Non-VARK Adaptive Learner Psychology (`learner-profile.json`, 20-event FIFO `< 8 KB`): Tier-1 calibration w/ Bayesian prior decay $6/(6+N)$, Tier-2 Pelánek 1PL/Elo placement, Tier-3 silent telemetry, 6 affective states, Kalyuga Expertise-Reversal `TeachingPolicy`, Failure DNA, `userOverrides`, 2-line compact policy output. |
| `syntopik/root_cause.py` | Non-destructive Bayesian Noisy-OR conjunctive DAG fault localizer (`localize_fault`), expected Shannon entropy reduction $\Delta H$ probe selector (`select_diagnostic_probe`), and cross-chapter blast-radius detector (`find_blast_radius`). |
| `syntopik/evidence.py` | 4-Tier Epistemic Evidence Ledger (`evidence-ledger.jsonl`, SHA-256 hash chain + `verify_ledger_chain`), anti-inflation alerts (`compute_evidence_summary`), and zero-PII/zero-CDN standalone HTML Mastery Dossier exporter (`export_mastery_dossier`). |
| `syntopik/brainstem.py` | Single deterministic 10-priority Pedagogical Brainstem (`plan_next_move`, `<= 180`-token Action Envelope) + 10-second mixed-source `drop_sources` enforcing Teacher-First spine ordering. |
| `syntopik/explorable.py` | Declarative `:::explore` Markdown block parser & strict AST-whitelist RPN compiler/evaluator (`parse_explorable_blocks`, `compile_explorable_spec`, `evaluate_rpn`, `sample_explorable_curve`, `evaluate_limit_check`) — zero `eval()`. |
| `syntopik/work_mentor.py` | Anti-Vibe-Coding Work Mentor: `debrief_git_diff` (strict `-` flag-injection rejection, 6 bug classes crystallized into deduped forks/arenas/KG nodes), `set_mentor_mode`, and `evaluate_mentor_gate` (`solid` T3+ silent passthrough vs 20s gate). |
| `syntopik/whiteboard.py` | Atomic Live AI-to-Whiteboard SSE Frame Bus (`whiteboard-session.json`): `get_session`, `push_frame` (5 validated kinds), `unlock_gate`, `pin_step`, `resolve_pin`, `clear_session`. |
| `syntopik/sim_eval.py` | Deterministic 60-day multi-persona cognitive simulator (`Syntopik-Eval`, 50-node conjunctive hypergraph × 5 personas × 6 controlled ablation arms) + 7 CI regression gates + SVG chart generator. |
| `syntopik/ingest.py` | Universal source ingestion (YouTube caption-only + `[mm:ss]` pause-points, arXiv Keshav 3-Pass, GitHub/local Golden-Path repo map, strict `http`/`https` Web URL slicing, notes/PDFs) + prioritized Incremental Reading queue (`reading-queue.jsonl`). |
| `syntopik/obsidian.py` | Bidirectional Obsidian vault sync (`<!-- syntopik:begin/end -->` markers, minimal YAML, `[[wikilinks]]`, `Trap-MOC.md`) + Matuschak Mnemonic Medium `:::cue` / `{{c1::...}}` / `Q:: A::` parser. |
| `syntopik/forks.py` | Blunder FEN & Critical-Moment Fork Replay (`forks.jsonl`), deterministic symbol/numeric mutation, repeat-blunder refutation without fix leak, Woodpecker latency compression. |
| `syntopik/packs.py` | Zero-PII shareable `.syntopik-pack.zip`/`.json` packs with fail-closed `EXCLUDED_PERSONAL_FILES` smuggle rejection (6 personal files), sanitized `derivations.jsonl` support, SHA-256 `SHA256SUMS.json`, zip-slip/symlink/zip-bomb guards, non-destructive ID+lexical dedup. |
| `syntopik/pocket.py` | ~20 KB zero-CDN mobile HTML5 commute deck (inline SVG occlusion, `localStorage` queue, `pocket-sync.json`), idempotent FSRS+FIRe sync replay, pure-stdlib ISO/IEC 18004 Byte-Mode QR encoder (SVG/ASCII). |
| `syntopik/server.py` | Zero-dep Python 3.12 `ThreadingHTTPServer` 2-way local Studio (`127.0.0.1`, CSRF token, path jail, SSE `/api/events` + `whiteboard_frame`, full v3.0 REST API, strict secret stripping, opt-in `--lan` scoped pairing for `/pocket`). |
| `syntopik/cli.py` | Unified `syntopik` v3.0 CLI (22 verbs: `init`, `attach`, `connect`, `serve`, `import`, `review`, `triage`, `pack`, `doctor`, `export`, `sync`, `run`, `next`, `profile`, `drop`, `demo`, `debrief`, `mode`, `sunset`, `pace`, `board`, `bench`). |

---

### 2.3 Complete Script & Benchmark Inventory (`scripts/` — 36 Scripts: 35 Python + 1 Node, plus `benchmarks/syntopik_bench.py`)

| Script | Subcommands / Role |
| :--- | :--- |
| `scripts/setup_harness.py` | `auto`, `status`, `init-data`, `configure`, `attach`, `install-mcp` — Cross-platform `< 2s` workspace bootstrapper (`10` ledgers), wheel `site-packages` guard, Windows `.claude/skills` junction/copy repair. |
| `scripts/learner_profile.py` | `status`, `calibrate`, `place`, `record`, `policy`, `override`, `reset` — CLI wrapper over `syntopik.learner_model`. |
| `scripts/srs_engine.py` | `add`, `review` (`--confidence`, `--duration-ms`, `--hints`, `--no-fire`), `due` (`--compress`, `--bury-siblings`, `--include-archived`), `recover`, `forecast`, `optimize`, `leeches`, `find-similar`, `add-cue`, `next-cue`, `export-anki` (Cloze + SVG IO), `push-anki`, `stats`, honoring Sunset `archived` & `targetRetention`. |
| `scripts/graph_intel.py` | `--bottlenecks`, `--islands`, `--path-to`, `--combos`, `--confusables`, `--sunset <slug>`, `--all` — Pure-stdlib `StdlibDiGraph` fallback + NetworkX hypergraph intelligence & Semester Sunset planner. |
| `scripts/daily_plan.py` | `--minutes`, `--micro 5`, `--energy low\|normal\|deep`, `--resume`, `--triage-hours 48 --sleep-hours 7`, `--pace --rebalance --ics <path>` — Fatigue-aware daily plans, Cold-Start Re-Entry, 48h Triage, and Multi-Course `.ics` Auto-Pacer. |
| `scripts/chapter_tracker.py` | `init`, `set-scope`, `add-source`, `add-topics`, `set-topic`, `add-slices`, `set-slice`, `checkpoint --breadcrumb`, `add-questions`, `mark-question`, `set-milestone`, `set-active`, `pause-doubt`, `clear-doubt`, `next`, `status`. |
| `scripts/ingest_source.py` | `auto`, `youtube`, `arxiv`, `repo`, `url`, `notes`, `queue add\|due\|advance` — Universal ingestion CLI & Incremental Reading queue. |
| `scripts/vault_sync.py` | `export`, `import`, `sync-notes` — Bidirectional Obsidian vault & Mnemonic Medium (`:::cue`, `{{c1::...}}`) sync CLI. |
| `scripts/pedagogy_eval.py` | `lint`, `check-card`, `bench` — Zero-LLM TutorBench pedagogical linter & **`40/40`** golden benchmark (9 rubrics × 6 domains, `--expertise`, `--affect`, `--bottleneck`, `--evidence-tier`). |
| `scripts/fork_replay.py` | `capture`, `due`, `check`, `mutate`, `stats` — Blunder FEN & Critical-Moment Fork Replay CLI (telemetry wired). |
| `scripts/falsify_arena.py` | `create`, `list`, `test`, `repair` — Adversarial Counterexample Arena (Examplar conceptual mutants + Lakatos `boundaryConditions[]` repair). |
| `scripts/decision_blitz.py` | `build`, `grade`, `stats` — 4-Min ShadowBox First-Move & Cue-Recognition Blitz + Newman Tier-1 analytics (telemetry wired). |
| `scripts/derivation_forge.py` | `create`, `list`, `blackout`, `check`, `cut` — Clean-Room Derivation Reconstruction (zero-leak L1–L3 blackout) & BFS Axiom-Cut Surgery (`derivations.jsonl`, T4 evidence wired). |
| `scripts/viva_chamber.py` | `start`, `turn`, `status`, `list` — 5-turn Adversarial Viva Voce Defense Chamber (`viva-sessions.jsonl`, Commitment Ledger, T4 promotion / `solid -> shaky` demotion). |
| `scripts/build_starter_packs.py` | `--verify`, `--verify-only`, `--json` — Deterministic builder & zero-PII verifier for the 3 flagship starter packs (`packs/*.syntopik-pack.zip`). |
| `scripts/code_verify.py` | `run`, `test-cases`, `complexity`, `ast-check` — Sandboxed CS/algorithms/ML runner, hidden test-cases, empirical $O(1)..O(2^N)$ fitter, static AST check. |
| `scripts/research_scout.py` | `status`, `search`, `read`, `scholar`, `manifest` — 4-tier portable research adapter & bounded Scope Charter (`maxSources <= 8`). |
| `scripts/mistake_analytics.py` | `log` (`--confused-with`), `summary` (3-strike `escalations`), `export-traps` — 13-category Newman/Rohrer & CS/coding mistake tracker (telemetry wired). |
| `scripts/exam_runner.py` | `schema`, `build`, `grade` (in-process `grade_attempt`, `good` credit on solved `propId`s, 3-bucket `knowledge`/`execution`/`time_sink` autopsy + `egoTraps`, telemetry wired). |
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
| `scripts/study_doctor.py` | `--core-only`, `--fix`, `--json` — Cross-platform `< 500 ms` health doctor checking all 20 `syntopik/` modules, 36 scripts, manifests, Windows-safe hooks, and unified `<= 9,500 B` rule budget. |
| `scripts/sync-graph.mjs` | `--open`, `--graph`, `--quiet`, `--json` — Pure-Node compiler for `graph-data.js` (`schemaVersion: 5`: sanitized v3.0 ledgers, `evidenceSummary`, `rootCauseHotspots`, offline `nextMove`, RPN `explorables`, `starterPacks`). |
| `benchmarks/syntopik_bench.py` | `--days 60`, `--seed 42`, `--svg`, `--markdown`, `--check`, `--json` — Reproducible 60-day `Syntopik-Eval` ablation runner, SVG chart generator, and 7-gate CI invariant checker. |

---

### 2.4 Skills, Hooks, 21-Tool MCP App v3.0 & Bundled Starter Packs
- **17 Portable Skills (`.agents/skills/`, mirrored at `.claude/skills`, `<= 3,400 B` total YAML descriptions)**: `topic-onboarding`, `chapter-autopilot`, `teach-chapter`, `practice-drill`, `grade-handwriting`, `exam-simulator`, `universal-pdf-study`, `ingest-chapter`, `visual-and-demo`, `revision-sheets`, `knowledge-graph`, `physics-circuits-and-units`, `physical-chemistry`, `organic-chemistry-tutor`, `manim-animator`, `subagent-orchestrator`, `harness-self-improve`.
- **Windows-Safe, Plugin-Cache-Safe Tri-Protocol Hooks (`.agents/hooks/`, `.claude-plugin/hooks.json`, `.claude/settings.json`, `.codex/hooks.json`)**: Support explicit `--workspace <dir>`, contain zero POSIX-only shell syntax, inject the 4-line Turn-0 teacher brief (`Learner Profile`, `Active TeachingPolicy`, `Due Forks/Arenas/Re-Entry`, `Pending Whiteboard Pins`) in `< 50 ms` pure Node, and watch all v3.0 ledgers and CLI verbs.
- **21-Tool SEP-1865 Studio MCP App v3.0 (`mcp-app/mlh-studio/`)**: `synt_quiz_card`, `synt_practice_card` (`standard` / `parsons` / `faded` / `blackout` with strict server-side sealing), `synt_lesson_studio` (with quiet TeachingPolicy/Affect override strip + opaque SVG occlusion), 5 app-only tools (incl. `synt_app_update_profile`), 5 ambient headless tools (`synt_next_move`, `synt_whiteboard_step`, `synt_capture_concept`, `synt_get_frontier`, `synt_log_review` w/ `hintsUsed`/`durationMs`), plus 8 `mlh_*` aliases. Single `com.google.antigravity/ui` `entrypoints` ONLY on `synt_lesson_studio`.
- **3 Zero-PII Curated Starter Packs (`packs/`)**: `raft-consensus.syntopik-pack.zip`, `shm-physics.syntopik-pack.zip`, and `transformers-attention.syntopik-pack.zip`.

---

### 2.5 Regression Watchlist (Never Regress)
1. `manim_helpers.py`: normalize `\Big|`/`\text{}`/`\implies` in fallback `LaTeXMath`; resolve TinyTeX portably across macOS & Linux.
2. `circuit_draw.py`: keep Wheatstone resistor labels outside diamond.
3. `pdf_studio.py`: suppress `pymupdf4llm` fd-1 stdout chatter; prioritize `.venv`'s `DejaVuSans-Bold.ttf` in `annotate`.
4. `math_verify.py`, `math_plot.py` & `syntopik/explorable.py`: strict AST-whitelist parsing (refuse `__import__`, `lambda`, `open`, nested powers, exponents `> 12`); zero `eval()`/`new Function()` in Python or `dashboard.html`.
5. `syntopik/fsrs_core.py` & `srs_engine.py`: scale BKT `p_transit` by rating; FIRe MUST NEVER auto-clear `misconception` nodes; honor Sunset `archived: true` & `targetRetention`; SciPy optimizer accepts ONLY on chronological OOS Log-Loss improvement; convert `$...$` to `\\(...\\)` for Anki MathJax.
6. `narrate_video.py`: escape `%%` in `argparse` help; keep Pillow PNG overlay burn-in for libass-free FFmpeg.
7. `sync-graph.mjs` & `session-start.mjs`: exclude `*-template.html` from demo counts; auto-bootstrap missing `graph-data.js`; never write into read-only plugin/extension caches; support `--workspace` without POSIX shell-isms; keep `schemaVersion: 5` free of sealed secrets.
8. `exam_runner.py` & `exam-cbt-template.html`: treat non-finite tokens (`NaN`, `inf`, `-inf`, `.`, `-`) as `unattempted`; preserve `Download Attempt JSON` on `file://` with zero console errors while auto-POSTing in live `syntopik serve` mode.
9. `mcp-app/mlh-studio`: keep `[hidden] { display: none !important; }` in `common.css`, disk-backed `state-store.mjs`, zero personal home paths, single `entrypoints` on `synt_lesson_studio`, and strict server-side sealing of `modelSolution`, `canonicalOrder`, `isDistractor`, and `blackout` expected answers.
10. `CLAUDE.md`, `GEMINI.md` & `SKILL.md`: keep `@AGENTS.md` on line 1 of BOTH `CLAUDE.md` and `GEMINI.md`; keep `AGENTS.md + GEMINI.md <= 9,500 B` (`7,281 B` baseline); keep literal `<...>` and script filenames out of `SKILL.md` YAML `description`.
11. `syntopik/server.py`, `packs.py`, `falsify_arena.py`, `forks.py`, `root_cause.py`, `work_mentor.py`, `viva_chamber.py`: enforce `127.0.0.1` + CSRF token + path jail + `--lan` route scoping (`/pocket` + `/api/pocket/state` + `POST /api/review/batch` only, blocking `/api/profile*` & `/api/whiteboard*`); fail-closed PII exclusion & smuggle rejection in `packs.py`; non-destructive `root_cause.py` (`solid` untouched); `-` flag-injection rejection in `work_mentor.py`; zero secret leaks across arenas/forks/whiteboard/blackout; `solid -> shaky` demotion on Viva boundary collapse.

---

## 3. Completed Backlog (`P0` → `P6` — 100% Landed & Verified)

- [x] **P0.1–P0.4** (`2cd9af6`): Mistake taxonomy, closed-loop `harness-self-improve`, offline NTA JEE CBT simulator, sandboxed `tests/test_harness.py`.
- [x] **P1.1–P1.4** (`f906ff6`): Parametric `physics_draw.py`, 40-reagent RDKit `organic_reagents.py`, `grade-handwriting` R0–R2 protocol, SymPy `math_plot.py`.
- [x] **LearnHarness Parity** (`81ad640`): Dry live co-pilot pedagogy, inline `<agent-embed>`, `pdf_studio.py` `math_density` + `annotate`, multi-cue FSRS-6, `premises[]` hyperedges, SEP-1865 Studio MCP App.
- [x] **P2–P3 + Chapter Autopilot** (`fb44afe`): `chapter_tracker.py`, NetworkX `graph_intel.py`, Visual Anki `media`, `audio_brief.py`, `daily_plan.py`, Reader Drawer + shortcuts, SHA-256 `backup_harness.py`, Context Footprint doctor.
- [x] **Phase 4 (`v1.0.0`, 2026-11-26)**: Syntopik rebrand, GitHub-clean separation + `setup_harness.py`, multi-harness configs, `code_verify.py`, `research_scout.py`, `topic-onboarding`, Sweller calm ceilings, Warm Obsidian / Archival Vellum UI.
- [x] **Phase 5 (`v2.0.0`, 2026-11-26)**: 12 pure-stdlib `syntopik/` modules, unified `syntopik` CLI (`uvx` + `npx`), `.claude-plugin/*`, `gemini-extension.json`, `syntopik attach`, 2-way `127.0.0.1` Studio server w/ CSRF/SSE/opt-in `--lan` `/pocket`, True FIRe + SRC + SciPy FSRS-6 optimizer + Brier calibration, Universal Ingestion, Obsidian/Mnemonic sync, Blunder Forks, Counterexample Arena, ShadowBox Blitz, 48h Triage + Anti-Mastery PDF, ~20 KB Pocket HTML + QR, Zero-PII Study Packs, 17-tool MCP App, and `-33.9%` Turn-0 context diet.
- [x] **Phase 6 (`v3.0.0`, 2026-11-26)**:
  - **P6.1 Learner Psychology & Causal Epistemics**: 3-Cadence Non-VARK `learner_model.py` + `learner_profile.py`, Bayesian Noisy-OR `root_cause.py`, SHA-256 4-tier `evidence.py` + Mastery Dossier, Pedagogical Brainstem `brainstem.py` (`syntopik next`, `syntopik drop`), fail-closed `packs.py` PII hardening.
  - **P6.2 Pedagogy & Lifecycle Engines**: Safe AST-whitelist RPN `explorable.py` (`:::explore` Tangle + Limiting-Case Labs), `derivation_forge.py` (Progressive Blackout + BFS Axiom-Cut Surgery), `viva_chamber.py` (5-turn Adversarial Viva Chamber), `whiteboard.py` (Live AI Chalkboard SSE + Reverse Pinning), `work_mentor.py` (`syntopik debrief`, `syntopik mode mentor`), Semester Sunset (`syntopik sunset`), and Multi-Course `.ics` Auto-Pacer (`syntopik pace`).
  - **P6.3 Playable Studio UI, 21-Tool MCP & Windows-Safe Portability**: `dashboard.html` 6-Station Mastery Gym + Live Chalkboard + Learner Profile/Overrides + zero-`eval` `:::explore`, `viewer.html` Causal Halos & T1–T4 Badges, `server.py` + `sync-graph.mjs` `schemaVersion: 5`, 21-tool MCP App v3.0, 10 new CLI verbs + all 5 P0/P1 CLI/Windows/`--vault local`/`_npx`/`attach` fixes.
  - **P6.4 Benchmarks, Starter Packs & 3-OS Release Engineering**: `Syntopik-Eval` (`sim_eval.py`, `benchmarks/syntopik_bench.py`, `7/7` CI gates + SVGs), `40/40` TutorBench (`pedagogy_eval.py`), 3 zero-PII starter packs (`build_starter_packs.py`), 3-OS CI (`ci.yml`) w/ wheel & `npm pack` E2E, GitHub Pages (`pages.yml`), and OIDC Trusted Publishing (`publish.yml`).

---

## 4. Curated `v3.1+` Roadmap

Future high-leverage initiatives that preserve zero-ML-weight, pure-stdlib `syntopik/`, calm anti-overstimulation ceilings, and the exact 17-skill footprint:

| Priority | Initiative | Core Mechanism & Research Lineage | Target Modules / Skills |
| :---: | :--- | :--- | :--- |
| **#1** | **Structural Isomorphism Rosetta Engine** | Gentner Structure-Mapping + Paynter Bond Graphs: canonize symbolic structures across subjects (e.g., damped SHM ↔ RLC ↔ PID ↔ Momentum SGD; Dijkstra ↔ Viterbi ↔ DAG DP) into `isomorphic_to` chords with explicit `breaksAt` negative-transfer guards, bijection prediction openers, and cross-domain FIRe credit (`0.25`). | `syntopik/isomorphism.py`, `syntopik/data/archetypes.json`, `graph_intel.py`, `viewer.html`, `topic-onboarding` |
| **#2** | **Offline-First Multi-Device Delta Sync** | Pure-stdlib append-only causal log (`sync-delta.json` / authenticated LAN pairing) merging `revlog.jsonl`, `evidence-ledger.jsonl`, and `learner-profile.json` across desktop/laptop/mobile with deterministic conflict resolution and zero cloud lock-in. | `syntopik/sync_delta.py`, `syntopik/pocket.py`, `syntopik/server.py` |
| **#3** | **Longitudinal Learner Telemetry Replay** | Calibrate `learner_model.py` Elo $K$-factor, affective state thresholds, and Kalyuga Expertise-Reversal crossover points against anonymized local `revlog.jsonl` + `learner-profile.json` histories inside `Syntopik-Eval` to verify personalized policy gains before adopting parameter changes. | `syntopik/sim_eval.py`, `benchmarks/syntopik_bench.py`, `harness-self-improve` |
| **#4** | **Structured Proof-Graph Handwriting Diff** | Link `grade-handwriting` Vision line-by-line parses directly to `derivations.jsonl` Causal Step DAGs so handwritten step breaks automatically spawn `Blunder Fork` checkpoints and causal ancestor probes. | `scripts/derivation_forge.py`, `syntopik/forks.py`, `grade-handwriting` |

---

## 5. Autonomous Self-Improvement Protocol

1. **Baseline Gate**: Run `.venv/bin/python -B scripts/study_doctor.py --json`, `python3 -B scripts/study_doctor.py --core-only --json`, `python3 -B scripts/pedagogy_eval.py bench --json` (`40/40`), `python3 -B benchmarks/syntopik_bench.py --days 60 --seed 42 --check --json` (`7/7`), `python3 -B scripts/build_starter_packs.py --verify-only --json` (`3/3`), `.venv/bin/python -B -m unittest discover -s tests -v`, and `git status --short`.
2. **Evidence Audit**: Inspect `knowledge-graph/harness-log.jsonl`, `mistakes.jsonl`, `revlog.jsonl`, `evidence-ledger.jsonl`, `learner-profile.json`, `propositions.jsonl`, `exam-attempts.jsonl`, and this blueprint.
3. **Bounded Scope**: Select 1–3 high-leverage upgrades or friction fixes preserving all Section 1 invariants (`GEMINI.md + AGENTS.md <= 9,500 B`, pure-stdlib `syntopik/`, zero heavy ML, zero personal paths, 3-OS Windows safety).
4. **Parallel Implementation + Read-Only `/tmp` Verification**: Use disjoint file ownership across subagents/swarm workers + an independent read-only verifier testing strictly in `/tmp` (including fresh-clone simulation, wheel + `npm pack` E2E, `syntopik serve` CSRF/path-jail/`--lan` checks, and `0`-console-error headless Chrome checks on both `http://127.0.0.1` and `file://`).
5. **Record & Commit**: Append 1 JSON line to `knowledge-graph/harness-log.jsonl`, update this blueprint, verify zero personal/Homebrew paths via `TestGitHubHygieneAndPortability` and zero `__pycache__`, and land one clean git commit.
