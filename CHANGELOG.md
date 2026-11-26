# Changelog

All notable changes to **Syntopik** are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
