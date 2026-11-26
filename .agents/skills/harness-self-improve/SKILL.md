---
name: harness-self-improve
description: >-
  Run a closed-loop self-audit, SHA-256 backup, and safe upgrade of Syntopik
  using SELF_IMPROVEMENT_BLUEPRINT.md, study_doctor.py, setup_harness.py,
  backup_harness.py, tests/test_harness.py, and knowledge-graph/harness-log.jsonl.
  Triggers on "improve the harness", "audit yourself", "self-improve", or after
  every 5 chapters/topics studied.
---

# Syntopik Self-Improvement — Closed-Loop Audit, Backup & Upgrade Protocol

Use this skill to inspect **Syntopik** health across all harnesses, mine real learning friction from persistent ledgers, implement 1–3 bounded upgrades, verify every invariant in `/tmp`, and record the audit cleanly.

---

## Non-Negotiable Architecture & Privacy Invariants

Every self-improvement pass MUST preserve **all** Section 1 invariants in `SELF_IMPROVEMENT_BLUEPRINT.md`:
1. **Multi-Harness Official Execution**: native primitives across Antigravity 2.0 (`GEMINI.md`, `.agents/`), Claude Code (`CLAUDE.md` starting with `@AGENTS.md`, `.claude/`, `.mcp.json`), OpenAI Codex / T3 Code (`AGENTS.md`, `.codex/`), and universal agents (`BOOTSTRAP.md`). Zero unofficial CLI proxies or token scraping.
2. **GitHub-Clean Learner Privacy & Zero Personal Paths**:
   - Learner data (`knowledge-graph/{propositions,chains,mistakes,exam-attempts}.jsonl`, `review-queue.md`, `graph-data.js`, `chapters/*.md`, `chapters/*-tracker.json`, `exams/*.html`, `sources/*`) stays **gitignored** and untouched on disk; fresh clones auto-bootstrap via `scripts/setup_harness.py`.
   - **Zero `/Users/<name>` or hardcoded Homebrew paths** (`! git grep "/Users/"`); always resolve binaries via `shutil.which(...)` first.
3. **Zero Heavy Local AI / ML Weights**: never install multi-GB VLM/OCR/TTS/Docker stacks; preserve `pint==0.26.2` + `molmass` (never `mendeleev`), `chempy` (`--no-deps`), Pillow PNG caption burn-in, `pymupdf4llm` stdout suppression, and portable TinyTeX lookup.
4. **Always-On Context Budgets**:
   - `GEMINI.md` + `AGENTS.md`: **zero YAML frontmatter**, `< 24 KB` each, **strictly `<= 9,500` bytes combined** (`<= 10,000 B` hard ceiling).
   - `CLAUDE.md`: starts with `@AGENTS.md` on line 1, `< 90` lines, zero frontmatter.
   - Every `.agents/skills/<name>/SKILL.md`: `name == folder` + concise `description <= 1,024` chars with **zero `<` or `>` angle brackets** inside `description`.
5. **Sandboxed CLI & Test Contracts**: every mutating CLI supports `--kg-dir` / `--chapters-dir` / `--no-sync` (honoring `SYNT_*` + `MLH_*` fallbacks) and pure JSON on `--json` stdout.

---

## 5-Step Closed-Loop Protocol

### Step 1 — Baseline Health, Backup & Regression Gate
```bash
.venv/bin/python -B scripts/study_doctor.py --json
.venv/bin/python -B -m unittest discover -s tests -v
git status --short
```
*(Before major structural upgrades when live study data exists on disk, take a safety snapshot outside the repo via `.venv/bin/python -B scripts/backup_harness.py snapshot`.)*

### Step 2 — Evidence & Friction Audit
Inspect `SELF_IMPROVEMENT_BLUEPRINT.md` (Section 2.5 Watchlist + backlog), `knowledge-graph/harness-log.jsonl`, `mistakes.jsonl`, `propositions.jsonl`, and `exam-attempts.jsonl`.

### Step 3 — Select Bounded Scope (`1–3` Cohesive Items)
Write explicit disjoint file ownership and interface contracts before editing.

### Step 4 — Implement + Independent Read-Only `/tmp` Verification
- Add sandboxed `unittest` tests to `tests/test_harness.py` (`tempfile.TemporaryDirectory()`, `-B` flag so zero `__pycache__` is created).
- Run an **independent read-only verifier** purely in `/tmp`:
  1. `.venv/bin/python -B scripts/study_doctor.py` → `0 warn, 0 fail`, `< 500 ms`
  2. `py_compile` (`SyntaxWarning` as error, compiled to `/tmp`) + `node --check` on all `.mjs` files
  3. `.venv/bin/python -B -m unittest discover -s tests -v` → `100% PASS`
  4. Fresh-clone simulation in `/tmp` (`setup_harness.py auto` + `study_doctor.py`)
  5. `wc -c GEMINI.md AGENTS.md` (`<= 9,500 B`, zero frontmatter) + `! git grep "/Users/"`
  6. Visual PNG / headless Chrome `file://` (`0` console errors) inspection for any visual/UI change.

### Step 5 — Record Outcome & Commit Cleanly
Append 1 JSON line to `knowledge-graph/harness-log.jsonl` (`{id, date, type, items, summary, doctorOk, testsPassed}`), update `SELF_IMPROVEMENT_BLUEPRINT.md`, verify zero `__pycache__` and zero tracked learner files in `git status --short`, and make one clean git commit.
