# Contributing to Syntopik

Thank you for helping improve **Syntopik** — the local-first, harness-native AI Deep-Mastery Operating System. Every contribution must preserve learner privacy, cross-platform portability, deterministic accuracy, and strict context-window budgets.

---

## Non-Negotiable Architecture Invariants

1. **Zero Personal Data or Machine-Specific Paths in Git**:
   - Learner state (`knowledge-graph/{propositions,chains,mistakes,exam-attempts}.jsonl`, `review-queue.md`, `graph-data.js`, `chapters/*.md`, `chapters/*-tracker.json`, `exams/*.html`, `demos/*.html` except `*-template.html`, `sources/*`) is **gitignored by design**.
   - Never commit personal study data or hardcoded `/Users/<name>` / `/opt/homebrew/bin` paths. Always resolve binaries via `shutil.which(...)` first with cross-platform fallbacks, and verify `git grep -i "/Users/"` returns `0` hits before opening a PR.
2. **Zero Heavyweight Local AI / ML Weights**:
   - Keep `.venv/` fast, deterministic, and lightweight (CAS math, sandboxed code execution, cheminformatics, vector diagrams, FSRS-6/BKT scheduling, Typst/FFmpeg glue). Never add multi-GB local VLM/OCR/TTS model weights or Docker stacks.
3. **Strict Multi-Harness Rule & Skill Token Budgets**:
   - `GEMINI.md` and `AGENTS.md` MUST contain **no YAML frontmatter**, stay `< 24 KB` each, and stay **strictly `<= 9,500` bytes combined** (`<= 10,000 B` hard gate in `study_doctor.py`).
   - `CLAUDE.md` MUST start with `@AGENTS.md` on line 1 (preventing Claude Code's `AGENTS.md` masking behavior) and stay `< 90` lines.
   - Every `.agents/skills/<name>/SKILL.md` MUST have portable YAML frontmatter containing `name: <exact-folder-name>` and a concise `description` (`<= 1,024` chars, **zero literal `<...>` angle brackets** inside `description`).
4. **Sandboxed CLI & Test Discipline**:
   - Every mutating script MUST support `--kg-dir <dir>` / `--chapters-dir <dir>` / `--no-sync` (and honor `SYNT_*` with `MLH_*` fallbacks) so tests run 100% inside `tempfile.TemporaryDirectory()` without touching real study data.
   - Every `--json` command must emit strictly parseable JSON on `stdout` (warnings/logs to `stderr`).

---

## Local Development & Verification Gate

Run all five checks before committing (always pass `-B` to Python so zero `__pycache__` directories are left in the repo):

```bash
# 1. Bootstrap fresh clone state (idempotent, never overwrites existing data):
.venv/bin/python -B scripts/setup_harness.py auto --json

# 2. Full workspace & cross-platform health doctor (must report 0 warn, 0 fail in <500 ms):
.venv/bin/python -B scripts/study_doctor.py

# 3. Strict Python & Node syntax checks (zero SyntaxWarnings, zero __pycache__):
.venv/bin/python -B -c "import pathlib, py_compile, tempfile; d=tempfile.mkdtemp(); [py_compile.compile(str(p), cfile=f'{d}/{p.name}.pyc', doraise=True) for p in list(pathlib.Path('scripts').glob('*.py')) + list(pathlib.Path('tests').glob('*.py'))]"
node --check scripts/sync-graph.mjs .agents/hooks/session-start.mjs .agents/hooks/post-graph-sync.mjs mcp-app/mlh-studio/dist/server.mjs

# 4. Sandboxed stdlib unittest regression suite (must pass 100% in <12 s):
.venv/bin/python -B -m unittest discover -s tests -v

# 5. Always-on rule budget & zero personal-path leak check:
wc -c GEMINI.md AGENTS.md
! git grep -n "/Users/"
```

If you modify `mcp-app/mlh-studio/src/**`, rebuild the zero-CDN bundles via:

```bash
cd mcp-app/mlh-studio && node build.mjs
```
