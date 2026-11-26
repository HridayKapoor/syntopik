# Syntopik — Master Architecture, Build Log & Autonomous Self-Improvement Blueprint

> **Purpose of this file:**
> 1. **Complete Ground-Truth Record** of everything built, audited, and verified in **Syntopik** (`<workspace>`, formerly *MY LEARNING HARNESS*) through **Phase 4: Multi-Harness Portability, Universal Topic Onboarding, CS/Code Verifier & Calm Editorial UI** (2026-11-26).
> 2. **Executable Self-Improvement Specification** for **Google Antigravity 2.0**, **Anthropic Claude Code**, **OpenAI Codex / T3 Code**, and any agent harness. Reference `@SELF_IMPROVEMENT_BLUEPRINT.md` with `harness-self-improve` so Syntopik audits itself, implements bounded upgrades, verifies every invariant in `/tmp`, and evolves safely.

---

## 1. Non-Negotiable Architecture & Safety Invariants

Any self-improvement pass **MUST** preserve every invariant below:

1. **100% Official, Harness-Native, Zero-Ban-Risk Execution**:
   - Everything runs natively inside **Google Antigravity 2.0**, **Anthropic Claude Code**, **OpenAI Codex (CLI / Desktop / T3 Code)**, Cursor, Windsurf, or any standard agent using official workspace primitives (`AGENTS.md`, `GEMINI.md`, `CLAUDE.md`, `BOOTSTRAP.md`, `.agents/skills/*/SKILL.md`, `.claude/skills`, `.agents/hooks.json`, `.claude/settings.json`, `.mcp.json`, `.codex/config.toml`).
   - **Never** scrape OAuth tokens, wrap proprietary CLIs in unauthorized consumer proxies, or spoof client identity.
2. **Zero Heavyweight Local AI / ML Models**:
   - Multimodal Vision (handwritten solutions, diagrams, dense equations) and deep reasoning run on the host harness's frontier model and subagents.
   - **Never install** multi-GB local VLM/ML/TTS/Docker stacks (`Docling`, `MinerU`, `Marker`/`Surya`, `olmOCR`, `MolScribe`, `DECIMER`, `kokoro-onnx`, `piper-tts`, `DeepTutor`/`OpenTutor`).
   - Keep `.venv/` strictly fast, deterministic, and lightweight (CAS math, sandboxed code execution, cheminformatics, vector rendering, OCR fallback, FSRS-6/BKT scheduling, Typst/FFmpeg glue).
3. **Multi-Harness Context Budget & Portable Skill Hygiene**:
   - `GEMINI.md` and `AGENTS.md` have **no YAML frontmatter**, stay **under 24 KB per file**, and stay **strictly `<= 9,500` bytes combined** (`<= 10,000 B` / ~2,500-token ceiling enforced by `study_doctor.py` and `tests/test_harness.py`).
   - `CLAUDE.md` starts with `@AGENTS.md` on line 1 (avoiding Claude Code's `AGENTS.md` masking trap) and stays `< 90` lines with zero frontmatter.
   - Every `.agents/skills/<name>/SKILL.md` MUST have portable YAML frontmatter (`name` matching folder name + concise `description <= 1,024` chars with **zero literal `<...>` angle brackets** inside `description`).
4. **GitHub-Clean Learner Privacy & Cross-Platform Portability**:
   - Personal learner state (`knowledge-graph/{propositions,chains,mistakes,exam-attempts}.jsonl`, `review-queue.md`, `graph-data.js`, `chapters/*.md`, `chapters/*-tracker.json`, `exams/*.html`, generated `demos/*.html`, `sources/*`) is **gitignored** and auto-bootstrapped on fresh clones via `setup_harness.py` / `study_doctor.py` / `session-start.mjs` — **never** tracked in Git or overwritten if already present.
   - **Zero hardcoded machine paths** (`git grep "/Users/"` must return `0` tracked matches); resolve system binaries via `shutil.which(...)` first with macOS/Linux/Windows fallbacks.
5. **Known Dependency & Platform Landmines (DO NOT REGRESS)**:
   - **NEVER install `mendeleev`**: pins `pint<0.25.0`, breaking `pint==0.26.2`, and drags `pandas` + `SQLAlchemy`. Use `molmass`.
   - **NEVER run bare `uv pip install chempy`**: drags ~70 Jupyter packages via `pyodesys`. Always install `quantities` + `uv pip install --no-deps chempy`.
   - **NEVER install `manim-voiceover` / `manim-physics`**: use `scripts/narrate_video.py` (`edge-tts` + optional macOS `say` + `ffmpeg`).
   - **FFmpeg without `libass`**: keep Pillow transparent-PNG `overlay` caption burn-in in `scripts/narrate_video.py` (auto-detecting `subtitles` filter only if available).
   - **`edge-tts` v7.x API**: use `submaker.feed(chunk)` and `submaker.get_srt()` (`.subs` and VTT are removed).
   - **`pymupdf4llm` fd-1 parser chatter**: always suppress stdout around `pymupdf4llm` calls so `--json` CLI output stays strictly parseable.
   - **TinyTeX subshell PATH**: import `scripts/manim_helpers.py` (`ensure_tinytex_in_path()` checks `$TINYTEX_BIN`/`$TEXLIVE_BIN`, `~/Library/TinyTeX`, `~/.TinyTeX` across macOS & Linux, and `shutil.which("latex")`).
6. **Strict Verification & Clean-Workspace Gate**:
   - Every code/skill change MUST pass:
     1. `.venv/bin/python -B scripts/study_doctor.py` → `0 warn, 0 fail`
     2. `py_compile` (`SyntaxWarning` as error, compiled to `/tmp` so zero `__pycache__` is created) + `node --check`
     3. `--help` exits `0` across all CLI scripts
     4. `.venv/bin/python -B -m unittest discover -s tests -v` → `100% PASS` (100% sandboxed in `tempfile.TemporaryDirectory()`)
     5. Visual PNG / headless browser inspection (`0` console errors via `file://`) for any visual/PDF/UI change
     6. Zero personal paths (`! git grep "/Users/"`) and clean `git status`.

---

## 2. Complete Record of Everything Built & Verified

### 2.1 Milestone History
| Commit / Phase | Milestone |
| :--- | :--- |
| `baf8f15`–`4e70aee` | **Core STEM OS Foundation:** Python 3.12 `.venv`, `pdf_studio.py` (RapidOCR + PageIndex `tree-index` + `pdfplumber` tables), `math_verify.py` (SymPy + Pint + Uncertainties), `chem_physical.py`, `chem_3d.py` (RDKit + offline `py2opsin`), `circuit_draw.py` (Schemdraw), `manim_helpers.py` + `narrate_video.py`, `cheatsheet_pdf.py` (Typst), `srs_engine.py` (FSRS-6 + BKT + Anki), `study_doctor.py`, and offline `dashboard.html` + `viewer.html`. |
| `2cd9af6` | **P0.1–P0.4 Upgrade:** `mistake_analytics.py` (9-category Newman/Rohrer taxonomy + `export-traps` → Typst PDF), `harness-self-improve` skill + `harness-log.jsonl`, offline NTA JEE CBT simulator `exam_runner.py` + `demos/exam-cbt-template.html` + `exam-simulator` skill, and sandboxed `tests/test_harness.py`. |
| `f906ff6` | **P1.1–P1.4 Upgrade:** `physics_draw.py` (7 exact physics/optics presets incl. TIR & inclined projectiles), 40-reagent RDKit-validated `reagents.json` + `organic_reagents.py`, `grade-handwriting` skill (FERMAT/VLMath R0–R2 protocol), and `math_plot.py` (5 SymPy static + zero-CDN interactive Canvas labs). |
| `81ad640` | **LearnHarness Live AI-in-Chat Parity + SEP-1865 Studio MCP App:** Dry zero-sycophancy voice, Probe Frontier Summary, `Predict → Teach → Check`, Pause Law, inline `<agent-embed>` widgets, `pdf_studio.py` `math_density` + white-halo `annotate`, `srs_engine.py` `find-similar` + weighted multi-cue FSRS-6, `premises[] -> conclusion` hyperedges, and pre-bundled `mcp-app/mlh-studio/` + `install_mcp_app.py`. |
| `fb44afe` | **Hands-Free Chapter Autopilot + Complete P2/P3 Study OS:** `chapter_tracker.py` + `chapter-autopilot` skill + Turn-0 Active Autopilot hook + Studio `Autopilot` tab; `graph_intel.py` (NetworkX bottlenecks/islands/cycle-safe paths/combos), Visual Anki `media`, `audio_brief.py` (`Q → 2.5s pause → A` MP3), `daily_plan.py`, `dashboard.html` Markdown+LaTeX Reader Drawer + shortcuts, SHA-256/zip-slip-safe `backup_harness.py`, and P3.3 Context Footprint doctor (`100%` of P0–P3 complete). |
| **Phase 4 (`Syntopik`, 2026-11-26)** | **Universal Multi-Harness & Any-Subject Transformation (`Syntopik`):** Rebranded to **Syntopik** (`SYNT_` / `synt_*` w/ `MLH_` / `mlh_*` backwards compatibility); GitHub-clean learner data architecture (`.gitignore` + idempotent `setup_harness.py` fresh-clone auto-bootstrap); tri-protocol hooks & configs for **Antigravity 2.0** (`GEMINI.md`), **Claude Code** (`CLAUDE.md` w/ `@AGENTS.md`, `.claude/settings.json`, `.claude/skills`, `.mcp.json`), **OpenAI Codex / T3 Code** (`AGENTS.md`, `.codex/config.toml`, `.codex/hooks.json`), and **Any Agent** (`BOOTSTRAP.md`); new `code_verify.py` (sandboxed CS/algorithms/ML runner, test-cases, empirical big-O fitter, AST check), `research_scout.py` (4-tier portable `researchctl` + keyless arXiv/OpenAlex/Crossref/Wikipedia + anti-rabbit-hole `manifest.json`), and 17th skill `topic-onboarding`; generalized `chapter_tracker.py`, `graph_intel.py`, `daily_plan.py`, and all 17 skills across CS/ML/Courses/Papers/STEM/JEE with Sweller Calm Anti-Overstimulation ceilings (`<= 160`w/turn) and *"Warm Obsidian & Archival Vellum"* Dark/Light UI tokens + Terminal MCP host safeguard. |

---

### 2.2 Toolchain & Portable Runtime Inventory
- **Python 3.12+** (`.venv/bin/python`, managed via `uv` or standard `venv`): `sympy`, `scipy`, `numpy`, `pint`, `uncertainties`, `lark`, `latex2sympy2-extended`, `networkx`, `pymupdf`, `pymupdf4llm`, `pdfplumber`, `rapidocr-onnxruntime`, `pillow`, `rdkit`, `py2opsin`, `chempy` (`--no-deps`), `molmass`, `quantities`, `schemdraw`, `matplotlib`, `manim`, `edge-tts`, `srt`, `fsrs`, `genanki`.
- **Node.js 20+**: powers tri-protocol hooks, `sync-graph.mjs`, and the zero-runtime-dep bundled SEP-1865 MCP server (`mcp-app/mlh-studio/dist/server.mjs`).
- **System Binaries (resolved portably via `shutil.which` first)**: `typst` (A4 Mastery/Trap PDFs), `TinyTeX` / TeX Live (`latex` + `dvisvgm` + `mhchem` for Manim), `ffmpeg` & `ffprobe` (audio briefs & video narration), optional macOS `say` / `tesseract` / `java` (`py2opsin`), optional `researchctl` (`local-research-stack`).

---

### 2.3 Complete Script Inventory (`scripts/` — 26 Scripts: 25 Python + 1 Node)

| Script | Subcommands / Role |
| :--- | :--- |
| `scripts/setup_harness.py` | `auto`, `status`, `init-data`, `configure`, `install-mcp` — Cross-platform `<2s` workspace bootstrapper, fresh-clone ledger initializer, Windows `.claude/skills` repair, and capability detector. |
| `scripts/code_verify.py` | `run`, `test-cases`, `complexity`, `ast-check` — Sandboxed CS/algorithms/ML verifier (Python/Node/C++/Rust/sh), hidden edge-case tester, empirical $O(1)..O(2^N)$ runtime fitter, and AST constraint checker. |
| `scripts/research_scout.py` | `status`, `search`, `read`, `scholar`, `manifest` — Portable 4-tier research adapter (`researchctl` → keyless arXiv/OpenAlex/Crossref/Wikipedia) + bounded Scope Charter (`maxSources <= 8`, `inScope`/`outOfScope` fences). |
| `scripts/chapter_tracker.py` | `init`, `set-scope`, `add-source`, `add-topics`, `set-topic`, `add-questions`, `mark-question`, `set-milestone`, `set-active`, `pause-doubt`, `clear-doubt`, `next`, `status` — Universal any-subject Autopilot state machine (`chapters/<slug>-tracker.json`). |
| `scripts/graph_intel.py` | `--bottlenecks`, `--islands`, `--path-to`, `--combos`, `--all` — NetworkX hypergraph analytics across STEM, CS, Systems, ML, and Proofs. |
| `scripts/daily_plan.py` | `--minutes`, `--config` — Profile-aware time-boxed study planner (15–20% FSRS warmup / 50–60% Autopilot or bottleneck deep work / 25–30% interleaved trap & synthesis drills). |
| `scripts/audio_brief.py` | `--chapter` / `--cards`, `--pause-sec 2.5` — Spoken `Q → recall pause → A` flash-revision `.mp3`/`.m4a` generator with natural spoken LaTeX cleanup. |
| `scripts/backup_harness.py` | `snapshot`, `verify`, `restore` — SHA-256 manifest-verified `.tar.gz` backup & zip-slip-safe restore of all personal study data. |
| `scripts/pdf_studio.py` | `inspect` (`math_density`/`size_class`), `tree-index`, `extract`, `tables`, `render`, `search`, `annotate` (white-halo SVG + `DejaVuSans-Bold` PNG + `<agent-embed>` HTML). |
| `scripts/math_verify.py` | `eval`, `check-eq`, `solve`, `small-osc`, `units`, `check-dim` (AST-safe), `limit`, `series`, `ode`, `vector`, `matrix`, `complex`, `error-prop` (`--latex`). |
| `scripts/math_plot.py` | `conic`, `argand`, `integral`, `vector3d`, `func` — Exact SymPy static SVG/PNG or 100% offline zero-CDN interactive Canvas HTML labs. |
| `scripts/physics_draw.py` | `fbd-incline`, `pulley`, `optics-lens`, `optics-mirror`, `optics-prism` (auto-TIR), `field-charges`, `projectile` (level & inclined). |
| `scripts/circuit_draw.py` | `preset` (`rc`, `rl`, `lcr`, `wheatstone`, `divider`, `kirchhoff`), `spec` — Schemdraw circuits, logic gates, and transistors. |
| `scripts/chem_physical.py` | `balance`, `stoich`, `formula`, `equil`, `ph` (exact charge-balance), `nernst`, `thermo`, `element`. |
| `scripts/chem_3d.py` | `inspect`, `render2d`, `render3d` — RDKit 2D structures, offline `py2opsin` IUPAC→SMILES + CIP `(R)/(S)`/`(E)/(Z)`, 3D MMFF94 conformers. |
| `scripts/organic_reagents.py` | `list`, `lookup`, `compare --out-grid`, `selectivity`, `drill-pair` over 40 RDKit-validated reagents (`knowledge-graph/reagents.json`). |
| `scripts/eduniti_pyq.py` | `chapters`, `stats`, `list`, `show`, `export-chapter` — Local JEE Main Physics PYQ bank extractor with exact LaTeX & cropped diagrams. |
| `scripts/manim_helpers.py` | Portable TinyTeX PATH injector + `CHEM_TEX_TEMPLATE` (`\ce{}`, `\cancel{}`) + zero-TeX math fallbacks. |
| `scripts/narrate_video.py` | Neural `edge-tts` / offline TTS voiceover + `.srt` + Pillow transparent-PNG `--burn-subs` compositor. |
| `scripts/cheatsheet_pdf.py` | `build`, `schema` — Offline <150 ms Typst compiler for 1–2p A4 Mastery & Pitfall PDFs (`chapters/<slug>-sheet.pdf`). |
| `scripts/srs_engine.py` | `review` (`--cue-id`), `due`, `find-similar`, `add-cue`, `next-cue`, `export-anki` (Visual Anki `media`), `push-anki`, `stats`. |
| `scripts/mistake_analytics.py` | `log`, `summary`, `export-traps` — Newman/Rohrer cognitive & coding mistake taxonomy (`knowledge-graph/mistakes.jsonl`). |
| `scripts/exam_runner.py` | `schema`, `build`, `grade` — Offline timed exams & authentic NTA JEE Main/Adv CBT HTML compiler + deterministic partial-marking grader. |
| `scripts/install_mcp_app.py` | `install`, `uninstall`, `status` — Portable zero-`node_modules` installer for the `syntopik-studio` (`mlh-studio`) SEP-1865 MCP App. |
| `scripts/study_doctor.py` | `--json` — Cross-platform <500 ms health doctor (auto-bootstraps missing learner files on fresh clones; `0 warn, 0 fail`). |
| `scripts/sync-graph.mjs` | `--open`, `--graph`, `--quiet`, `--json` — Syncs hypergraph, trackers, mistakes, exams, audio briefs, and inline Markdown into `graph-data.js`. |

---

### 2.4 All 17 Skills, Tri-Protocol Hooks, `Syntopik Studio` MCP App & Calm UI
- **17 Portable Skills (`.agents/skills/`, mirrored at `.claude/skills`):** `topic-onboarding`, `chapter-autopilot`, `teach-chapter`, `practice-drill`, `grade-handwriting`, `exam-simulator`, `universal-pdf-study`, `ingest-chapter`, `visual-and-demo`, `revision-sheets`, `knowledge-graph`, `physics-circuits-and-units`, `physical-chemistry`, `organic-chemistry-tutor`, `manim-animator`, `subagent-orchestrator`, `harness-self-improve`.
- **Tri-Protocol Hooks (`.agents/hooks.json`, `.claude/settings.json`, `.codex/hooks.json`):** `session-start.mjs` & `post-graph-sync.mjs` auto-detect Antigravity (`injectSteps`) vs Claude Code / OpenAI Codex (`hookSpecificOutput.additionalContext`), resolving repo root via `import.meta.url` and honoring `SYNT_* || MLH_*` env overrides.
- **Terminal-Safe SEP-1865 Studio MCP App (`mcp-app/mlh-studio/`, `.mcp.json`, `.codex/config.toml`):** Exposes `synt_quiz_card`, `synt_practice_card`, `synt_lesson_studio` (plus `mlh_*` aliases). Renders interactive Warm-Obsidian / Archival-Vellum UI cards on SEP-1865 hosts and automatically degrades to clean Markdown prompts in terminal-only MCP clients while keeping answers and `modelSolution` sealed server-side.

---

### 2.5 Regression Watchlist (Never Regress)
1. `manim_helpers.py`: normalize `\Big|`/`\text{}`/`\implies` in fallback `LaTeXMath`; resolve TinyTeX portably across macOS & Linux.
2. `circuit_draw.py`: keep Wheatstone resistor labels outside diamond.
3. `pdf_studio.py`: suppress `pymupdf4llm` fd-1 stdout chatter; prioritize `.venv`'s `DejaVuSans-Bold.ttf` in `annotate` so math/subscript glyphs (`∮`, `₀`, `∧`, `∥`) never render as tofu boxes.
4. `math_verify.py` & `math_plot.py`: strict AST/safe-namespace expression parsing (refuse `__import__` injection); farthest-pair `(bestA, bestB)` line clipping in `math_plot.py` so near-corner lines never collapse.
5. `srs_engine.py`: scale BKT `p_transit` by rating so `--rating again` never inflates low-prior mastery; convert `$...$` to `\\(...\\)` for Anki MathJax.
6. `narrate_video.py`: escape `%%` in `argparse` help; keep Pillow PNG overlay burn-in for libass-free FFmpeg.
7. `sync-graph.mjs` & `session-start.mjs`: exclude `*-template.html` from demo counts; auto-bootstrap missing `graph-data.js` on fresh clones.
8. `exam_runner.py` & `exam-cbt-template.html`: treat non-finite tokens (`NaN`, `inf`, `-inf`, `.`, `-`) as `unattempted` (`0` marks), align accuracy to `(correct / attempted) * 100`, and render `0` (not `-0`) when negative marks lost is zero.
9. `mcp-app/mlh-studio`: keep `[hidden] { display: none !important; }` in `common.css`, disk-backed `state-store.mjs` for iframe remount persistence, zero `/Users/...` paths in tracked `mcp_config.json`, and non-UI terminal text fallback when `io.modelcontextprotocol/ui` is absent.
10. `CLAUDE.md` & `SKILL.md`: keep `@AGENTS.md` on line 1 of `CLAUDE.md` so Claude Code never masks `AGENTS.md`, and keep literal `<...>` angle brackets out of `SKILL.md` YAML `description` strings.

---

## 3. Completed Backlog (`P0` → `P4` — 100% Landed & Verified)

- [x] **P0.1–P0.4** (`2cd9af6`): Mistake taxonomy (`mistake_analytics.py`), closed-loop `harness-self-improve` + `harness-log.jsonl`, offline NTA JEE CBT simulator (`exam_runner.py`), and sandboxed `tests/test_harness.py`.
- [x] **P1.1–P1.4** (`f906ff6`): Parametric `physics_draw.py`, 40-reagent RDKit `organic_reagents.py` + `reagents.json`, FERMAT/VLMath `grade-handwriting` skill, and SymPy static/interactive `math_plot.py`.
- [x] **LearnHarness Parity Tier 1 + Tier 2** (`81ad640`): Dry live co-pilot pedagogy, inline `<agent-embed>` visuals, `pdf_studio.py` `math_density` + `annotate`, `srs_engine.py` `find-similar` + multi-cue FSRS-6, `premises[]` hyperedges, and SEP-1865 Studio MCP App.
- [x] **P2.1–P2.4 & P3.1–P3.3 + Chapter Autopilot** (`fb44afe`): `chapter_tracker.py` + `chapter-autopilot`, NetworkX `graph_intel.py`, Visual Anki `media`, `audio_brief.py`, `daily_plan.py`, `dashboard.html` Markdown+LaTeX Reader + shortcuts, `backup_harness.py`, and Context Footprint doctor.
- [x] **P4.1–P4.4 — Syntopik Universal Multi-Harness & Any-Subject Release** *(Completed 2026-11-26)*:
  - **P4.1**: GitHub-clean `.gitignore` learner-data separation + cross-platform `scripts/setup_harness.py` fresh-clone auto-bootstrap + MIT `LICENSE` + `CONTRIBUTING.md`.
  - **P4.2**: Tri-protocol hooks & native configs across **Antigravity 2.0** (`GEMINI.md`), **Claude Code** (`CLAUDE.md`, `.claude/settings.json`, `.claude/skills`, `.mcp.json`), **OpenAI Codex / T3 Code** (`AGENTS.md`, `.codex/config.toml`, `.codex/hooks.json`), and **Any Agent** (`BOOTSTRAP.md`) + terminal-safe `syntopik-studio` MCP App.
  - **P4.3**: Universal any-subject generalization (`chapter_tracker.py` arbitrary subjects & `scopeCharter`, `scripts/code_verify.py` CS/algorithms/ML sandbox & complexity fitter, `scripts/research_scout.py` 4-tier bounded research & scholarly scout, `topic-onboarding` 17th skill).
  - **P4.4**: Sweller Calm Anti-Overstimulation turn ceilings (`<= 160`w/turn, `1` demand/turn) + *"Warm Obsidian & Archival Vellum"* editorial Dark/Light palette across `dashboard.html`, `viewer.html`, `exam-cbt-template.html`, and `Syntopik Studio`.

---

## 4. Autonomous Self-Improvement Protocol

1. **Baseline Gate**: Run `.venv/bin/python -B scripts/study_doctor.py --json`, `.venv/bin/python -B -m unittest discover -s tests -v`, and `git status --short`.
2. **Evidence Audit**: Inspect `knowledge-graph/harness-log.jsonl`, `mistakes.jsonl`, `propositions.jsonl`, `exam-attempts.jsonl`, and this blueprint.
3. **Bounded Scope**: Select 1–3 high-leverage upgrades or friction fixes preserving all Section 1 invariants (`GEMINI.md + AGENTS.md <= 9,500 B`, zero heavy ML, zero personal paths).
4. **Parallel Implementation + Read-Only `/tmp` Verification**: Use disjoint file ownership across subagents/swarm workers + an independent read-only verifier testing strictly in `/tmp` (including fresh-clone simulation and `0`-console-error headless Chrome checks).
5. **Record & Commit**: Append 1 JSON line to `knowledge-graph/harness-log.jsonl`, update this blueprint, ensure `! git grep "/Users/"` and zero `__pycache__`, and land one clean git commit.
