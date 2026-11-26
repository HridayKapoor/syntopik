# Contributing to Syntopik

Thank you for helping improve **Syntopik** — the local-first, harness-native AI Deep-Mastery Operating System. Every contribution must preserve learner privacy, bare-stdlib portability, deterministic accuracy, calm pedagogical invariants, and strict context-window budgets.

---

## Non-Negotiable Architecture Invariants

1. **Zero Personal Data or Machine-Specific Paths in Git**:
   - Learner state (`knowledge-graph/{propositions,chains,revlog,mistakes,reading-queue,forks,arenas,decisions,exam-attempts}.jsonl`, `study-config.json`, `fsrs-params.json`, `blitz-sessions.json`, `review-queue.md`, `graph-data.js`, `chapters/*.md`, `chapters/*-tracker.json`, `exams/*.html`, generated `demos/*.html` except `*-template.html`, `sources/*`, `.syntopik/`, `*.syntopik-pack.zip`) is **gitignored by design**.
   - Never commit personal study data or hardcoded user-home or Homebrew prefix paths. Always resolve binaries via `shutil.which(...)` first (or `syntopik.paths.find_binary(...)`), and verify `TestGitHubHygieneAndPortability` passes with `0` machine-path leaks before opening a PR.
2. **Pure-Stdlib `syntopik/` Core & Zero Heavyweight Local ML Weights**:
   - All `12` modules in `syntopik/` MUST run on **bare Python 3.12+ standard library** (`dependencies = []` in `pyproject.toml`), using lazy optional imports (`fsrs`, `scipy`, `sympy`, `networkx`, `rdkit`, `genanki`) only when enhanced features are requested.
   - Keep `.venv/` fast, deterministic, and lightweight. Never add multi-GB local VLM/OCR/TTS/PyTorch/Docker stacks.
3. **Strict Multi-Harness Context Budgets & Portable Skill Hygiene**:
   - `AGENTS.md` and `GEMINI.md` MUST contain **no YAML frontmatter**, stay `< 24 KB` each, and stay **strictly `<= 9,500` bytes combined** (`<= 10,000 B` hard gate in `study_doctor.py`). Keep additions lean relative to the **v2.0.0 baseline of `6,548 B`** (`AGENTS.md = 4,376 B` + `GEMINI.md = 2,172 B`; total Antigravity Turn-0 load `10,551 B`, `-33.9%` vs v1.0.0).
   - `CLAUDE.md` MUST start with `@AGENTS.md` on line 1 (preventing Claude Code's `AGENTS.md` masking behavior) and stay `< 45` lines with zero frontmatter.
   - Every `.agents/skills/<name>/SKILL.md` MUST have portable YAML frontmatter containing `name: <exact-folder-name>` and a concise `description` (`<= 210` chars target / `<= 1,024` spec limit, **zero literal `<...>` angle brackets** and **zero script filenames** inside `description`).
4. **Pedagogical & Anti-Leak Discipline (`scripts/pedagogy_eval.py`)**:
   - Any changes to teaching prompts, skills, or graded card schemas MUST satisfy `scripts/pedagogy_eval.py bench` (`15/15 PASS`): `<= 160` words prose/turn, zero sycophancy, bare matched-length options (`max/min <= 2.2`), explicit `"I don't know"`, single active demand/turn, non-spoiling hints (`<= 35`w), and Post-Conclusion Pause Law.
5. **Plugin-Cache Safety, Sandboxed CLIs & Dual `http://` / `file://` UI Compatibility**:
   - Every mutating CLI MUST support `--workspace` / `--kg-dir` / `--chapters-dir` / `--no-sync` (via `syntopik.paths`, honoring `SYNT_*` with `MLH_*` fallbacks) and never write into read-only plugin/extension caches.
   - Every `--json` command must emit strictly parseable JSON on `stdout` (warnings/logs to `stderr`).
   - All HTML surfaces (`dashboard.html`, `knowledge-graph/viewer.html`, `demos/exam-cbt-template.html`, `mcp-app/mlh-studio/dist/*.html`, Pocket decks) MUST remain **zero-CDN** and degrade cleanly with **zero console errors** on `file://` when `syntopik serve` is not running.

---

## Codebase Layout

- `syntopik/` (`12` pure-stdlib modules): core package (`paths`, `storage`, `schemas`, `fsrs_core`, `ingest`, `obsidian`, `forks`, `packs`, `pocket`, `server`, `cli`).
- `scripts/` (`32` engines — 31 Python + 1 Node): deterministic STEM/CS verifiers, ingestion, Obsidian sync, mastery arenas, TutorBench linter, PDF/Typst/Manim/Anki tools, and `sync-graph.mjs`.
- `bin/syntopik.mjs` + `package.json` + `pyproject.toml`: zero-clone Node 20+ (`npx`) & Python 3.12+ (`uvx`) entrypoints.
- `.agents/skills/` (`17` skills) & `.agents/hooks/`: progressive-disclosure skills and tri-protocol hooks.
- `.claude-plugin/` & `gemini-extension.json`: official Claude Code marketplace/plugin and Gemini CLI extension manifests.
- `mcp-app/mlh-studio/`: 17-tool SEP-1865 `syntopik-studio` MCP App (`src/` → zero-CDN `dist/`).

---

## Local Development & Verification Gate

Run all checks before committing (always pass `-B` to Python so zero `__pycache__` directories are left in the repo):

```bash
# 1. Bootstrap fresh clone state (idempotent, never overwrites existing data):
.venv/bin/python -B scripts/setup_harness.py auto --json

# 2. Full STEM doctor AND bare-stdlib core-only doctor (both must report 0 warn, 0 fail):
.venv/bin/python -B scripts/study_doctor.py
python3 -B scripts/study_doctor.py --core-only

# 3. Strict Python & Node syntax checks across syntopik/, scripts/, tests/, bin/, hooks/, MCP (zero warnings, zero __pycache__):
python3 -B -W error::SyntaxWarning -c "import pathlib, py_compile, tempfile; d=tempfile.mkdtemp(); [py_compile.compile(str(p), cfile=f'{d}/{p.parent.name}_{p.name}.pyc', doraise=True) for p in list(pathlib.Path('syntopik').glob('*.py')) + list(pathlib.Path('scripts').glob('*.py')) + list(pathlib.Path('tests').glob('*.py'))]"
node --check bin/syntopik.mjs scripts/sync-graph.mjs .agents/hooks/session-start.mjs .agents/hooks/post-graph-sync.mjs mcp-app/mlh-studio/dist/server.mjs

# 4. TutorBench pedagogical golden suite (15/15 PASS):
python3 -B scripts/pedagogy_eval.py bench --json

# 5. Sandboxed stdlib unittest regression suite (100% PASS in tempfile.TemporaryDirectory):
.venv/bin/python -B -m unittest discover -s tests -v

# 6. Always-on context-budget check (v2.0.0 baseline: 6,548 B <= 9,500 B), manifest JSON check & zero path leaks:
wc -c AGENTS.md GEMINI.md CLAUDE.md
python3 -B -c "import json, pathlib; [json.loads(pathlib.Path(p).read_text()) for p in ['package.json','gemini-extension.json','.mcp.json','.claude-plugin/plugin.json','.claude-plugin/marketplace.json','.claude-plugin/hooks.json']]"
.venv/bin/python -B -m unittest tests.test_harness.TestGitHubHygieneAndPortability -v
```

If you modify `mcp-app/mlh-studio/src/**`, rebuild the zero-CDN bundles via:

```bash
cd mcp-app/mlh-studio && node build.mjs
```
