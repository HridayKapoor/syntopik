---
name: harness-self-improve
description: >-
  Run Syntopik's closed-loop self-audit, SHA-256 backup, deterministic
  regression tests, and TutorBench pedagogical linting. Triggers on "improve the
  harness", "audit yourself", or every 5 topics.
---

# Syntopik Self-Improvement — Closed-Loop Audit, Backup & Upgrade Protocol

Use this skill to inspect **Syntopik** health across all harnesses, mine real learning friction from persistent ledgers, implement 1–3 bounded upgrades, verify every engineering AND pedagogical invariant in `/tmp`, and record the audit cleanly.

*(Portable CLI: use `syntopik doctor` / `syntopik run <engine>` in attached repos, or `.venv/bin/python -B scripts/<engine>.py` in direct clones.)*

---

## Non-Negotiable Architecture & Privacy Invariants

Every self-improvement pass MUST preserve **all** Section 1 invariants in `SELF_IMPROVEMENT_BLUEPRINT.md`:
1. **Multi-Harness Official Execution**: native primitives across Antigravity 2.0 (`GEMINI.md`, `.agents/`), Claude Code (`CLAUDE.md` starting with `@AGENTS.md`, `.claude/`, `.claude-plugin/`, `.mcp.json`), Gemini CLI (`gemini-extension.json`), OpenAI Codex / T3 Code (`AGENTS.md`, `.codex/`), and universal agents (`BOOTSTRAP.md`, `syntopik` CLI). Zero unofficial CLI proxies or token scraping.
2. **GitHub-Clean Learner Privacy & Zero Personal Paths**:
   - Learner data (`knowledge-graph/{propositions,chains,revlog,mistakes,reading-queue,exam-attempts}.jsonl`, `study-config.json`, `fsrs-params.json`, `review-queue.md`, `graph-data.js`, `chapters/*.md`, `chapters/*-tracker.json`, `exams/*.html`, `sources/*`) stays **gitignored** and untouched on disk; fresh clones auto-bootstrap via `setup_harness.py`.
   - **Zero `/Users/<name>` or hardcoded Homebrew paths** (`! git grep "/Users/"`); resolve binaries portably via `shutil.which(...)`.
3. **Zero Heavy Local AI / ML Weights**: never install PyTorch, FAISS, LightRAG, local VLMs, or Docker stacks; preserve bare Python 3.12+ stdlib core loop (`syntopik/` + FSRS-6/graph fallbacks), SciPy L-BFGS-B optimizer, `pint==0.26.2` + `molmass`, `chempy` (`--no-deps`), Pillow PNG captions, and portable TinyTeX lookup.
4. **Always-On Context Budgets (Zero Skill Explosion)**:
   - `GEMINI.md` + `AGENTS.md`: **zero YAML frontmatter**, `< 24 KB` each, v2.0 target **`<= 7,200 B`** (`<= 9,500 B` hard ceiling).
   - `CLAUDE.md`: starts with `@AGENTS.md` on line 1, `< 45` lines, zero frontmatter.
   - Exact **17** `.agents/skills/<name>/SKILL.md` skills: `name == folder`, crisp `140–210` char `description` (total `< 3,500 B`), **zero `<` or `>` angle brackets** inside `description`.
5. **Sandboxed CLI & Pedagogical Invariants**: every mutating CLI honors `SYNT_WORKSPACE` / `--kg-dir` / `--chapters-dir` / `--no-sync` (`MLH_*` fallbacks) and emits pure JSON on `--json`; tutor turns obey `scripts/pedagogy_eval.py` (`<= 160`w prose, zero sycophancy, zero option leaks, `1` demand/turn, Pause Law).

---

## 5-Step Closed-Loop Protocol

### Step 1 — Baseline Health, Backup & Pedagogical Regression Gate
```bash
.venv/bin/python -B scripts/study_doctor.py --json
.venv/bin/python -B scripts/pedagogy_eval.py bench --json
.venv/bin/python -B -m unittest discover -s tests -v
git status --short
```
*(Before major structural upgrades when live study data exists on disk, take an external safety snapshot via `.venv/bin/python -B scripts/backup_harness.py snapshot`.)*

### Step 2 — Evidence & Friction Audit
Inspect `SELF_IMPROVEMENT_BLUEPRINT.md`, `knowledge-graph/harness-log.jsonl`, `mistakes.jsonl`, `propositions.jsonl`, `revlog.jsonl` (Brier calibration & leeches), and `exam-attempts.jsonl`. Optionally lint recent tutor transcripts via `scripts/pedagogy_eval.py lint <transcript.md> --json`.

### Step 3 — Select Bounded Scope (`1–3` Cohesive Items)
Write explicit disjoint file ownership and interface contracts before editing.

### Step 4 — Implement + Independent Read-Only `/tmp` Verification
- Add sandboxed `unittest` tests to `tests/test_harness.py` (`tempfile.TemporaryDirectory()`, `-B` flag so zero `__pycache__` is created).
- Run an **independent read-only verifier** purely in `/tmp`:
  1. `.venv/bin/python -B scripts/study_doctor.py` → `0 warn, 0 fail`, `< 500 ms`
  2. `.venv/bin/python -B scripts/pedagogy_eval.py bench --json` → `15/15` TutorBench rubric pass (and `lint` / `check-card` on any edited prompt templates!)
  3. `py_compile` (`SyntaxWarning` as error, compiled to `/tmp`) across `syntopik/` & `scripts/` + `node --check` on all `.mjs` files
  4. `.venv/bin/python -B -m unittest discover -s tests -v` → `100% PASS` (plus bare `python3` core-stdlib smoke test!)
  5. Fresh-clone simulation in `/tmp` (`setup_harness.py auto` + `study_doctor.py --core-only`)
  6. `wc -c GEMINI.md AGENTS.md` (`<= 7,200 B` target / `<= 9,500 B` ceiling, zero frontmatter) + `! git grep "/Users/"`
  7. Visual PNG / headless Chrome (`0` console errors) inspection for any visual/UI change.

### Step 5 — Record Outcome & Commit Cleanly
Append 1 JSON line to `knowledge-graph/harness-log.jsonl` (`{id, date, type, items, summary, doctorOk, testsPassed}`), update `SELF_IMPROVEMENT_BLUEPRINT.md`, verify zero `__pycache__` and zero tracked learner files in `git status --short`, and make one clean git commit.
