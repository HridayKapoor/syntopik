# Syntopik — Universal 10-Second Bootstrap & Multi-Harness Guide (`BOOTSTRAP.md`)

Give this file (or `AGENTS.md`) to **any AI coding agent, IDE assistant, CLI harness, or browser learner** to turn any workspace into a deterministic, FSRS-6 + FIRe Deep-Mastery Operating System in under 10 seconds.

---

## Step 1 — 10-Second Integration Matrix (Every User & Harness)

| User / Harness | Single 10-Second Command | What Works Immediately |
| :--- | :--- | :--- |
| **Browser-First / Zero-Clone Learner** | `npx github:HridayKapoor/syntopik serve --open` *(or `uvx --from git+https://github.com/HridayKapoor/syntopik syntopik serve --open`)* | Auto-creates `~/.syntopik` vault, opens live 2-way Studio (`http://127.0.0.1:4173`), PDF/URL import, FIRe flashcards, SVG occlusion, 1-click CBT grading — zero git clone. |
| **Claude Code (Any Repo)** | `/plugin marketplace add HridayKapoor/syntopik` → `/plugin install syntopik` | All 17 `/syntopik:*` skills, `syntopik-studio` MCP (incl. ambient capture tools), and plugin-cache-safe `SessionStart` / `PostToolUse` hooks in any repo. |
| **Cursor / Windsurf / Copilot / Cline / Roo / Codex / OpenCode** | `npx skills add HridayKapoor/syntopik` *(+ optional `npx github:HridayKapoor/syntopik attach .`)* | Installs all 17 `agentskills.io` skills + optional thin pointer rule (`.cursor/rules/syntopik.mdc`, `.windsurf/rules/syntopik.md`, `.github/instructions/syntopik.instructions.md`) + harness-native MCP config (`.vscode/mcp.json` `"servers"`, `opencode.json` `"mcp"`, `.cursor/mcp.json`). Use `--copy` if symlinks are disabled. |
| **Google Antigravity 2.0 (Any Repo)** | `uvx --from git+https://github.com/HridayKapoor/syntopik syntopik connect antigravity` | Installs global `~/.gemini/config/plugins/syntopik/` bundle (`plugin.json`, 17 skills, rules, hooks, SEP-1865 Studio MCP) active across every workspace. |
| **Gemini CLI (Any Repo)** | `gemini extensions install https://github.com/HridayKapoor/syntopik` | Installs `gemini-extension.json`, `GEMINI.md`, skills, hooks, and `syntopik-studio` MCP server. |
| **Direct Git Clone / Power User** | `git clone https://github.com/HridayKapoor/syntopik && cd syntopik && syntopik serve --open` | Full local STEM/Manim/Typst stack + `syntopik doctor` + offline `file://` fallback. |

### Silent Idempotent First-Run Bootstrap (`< 2s`, Bare Python 3.12 Stdlib)
On a fresh clone or worktree (whenever `knowledge-graph/graph-data.js` is absent), run once:
```bash
syntopik init --json 2>/dev/null || .venv/bin/python -B scripts/setup_harness.py auto --json 2>/dev/null || python3 scripts/setup_harness.py auto --json
```
Creates missing learner folders & gitignored ledgers (`propositions.jsonl`, `chains.jsonl`, `revlog.jsonl`, `mistakes.jsonl`, `reading-queue.jsonl`, `exam-attempts.jsonl`, `review-queue.md`, `graph-data.js`) without ever overwriting existing data, and repairs Windows `.claude/skills` links.

---

## Step 2 — Unified `syntopik` CLI Reference & Workspace Safety

All 8 verbs work on **bare Python 3.12+ stdlib** (or Node 20+ via `npx`) and automatically resolve bundled scripts whether inside a direct clone, wheel, or attached repository:

| Command | Purpose (Direct-Clone Script Fallback) |
| :--- | :--- |
| `syntopik init [dir]` | Non-destructively initialize workspace or `~/.syntopik` vault (`scripts/setup_harness.py auto`). |
| `syntopik attach <dir> [--harness auto\|all\|...] [--vault global\|local]` | Wire Syntopik into any existing repo cleanly via `.syntopik/` + `.git/info/exclude` (zero diff to tracked `.gitignore` or existing `AGENTS.md`). |
| `syntopik connect <harness>` | Install global plugin, MCP config, or extension (`scripts/install_mcp_app.py`). |
| `syntopik serve [--port 4173] [--open] [--workspace <dir>]` | Launch 2-way local Studio server (`127.0.0.1`, per-process CSRF token, SSE live refresh, offline `file://` fallback intact). |
| `syntopik import <pdf\|dir\|arxiv\|youtube\|github\|url> [--chapter <slug>]` | 1-command universal importer & incremental reading queue (`scripts/ingest_source.py`). |
| `syntopik review [--web\|--tui] [--compress] [--recover]` | Run FSRS-6 + FIRe compressed reviews or calm backlog recovery (`scripts/srs_engine.py`, `scripts/daily_plan.py`). |
| `syntopik doctor [--fix] [--core-only]` | Verify workspace, hooks, skills, context budgets, and engines (`scripts/study_doctor.py`). |
| `syntopik export <obsidian\|anki\|json> [--out <path>]` | Bidirectional Obsidian/Mnemonic sync (`scripts/vault_sync.py`) or Visual/IO Anki `.apkg` (`scripts/srs_engine.py export-anki`). |
| `syntopik run <engine> [args...]` | Portable passthrough to any bundled script (`math_verify`, `code_verify`, `pdf_studio`, `pedagogy_eval`, etc.). |

**Workspace Resolution Precedence** (honoring `MLH_*` fallbacks): explicit flag → `SYNT_WORKSPACE` → `$CLAUDE_PROJECT_DIR` / `cwd` (if `knowledge-graph/` or `.syntopik/` exists) → writable clone root → `~/.syntopik` (safe inside read-only plugin caches!). Optional overrides: `SYNT_KG_DIR`, `SYNT_CHAPTERS_DIR`, `SYNT_DATA_DIR`.

---

## Step 3 — 4-Axis Multi-Harness UX Capability Matrix

Detect your host's tools and pick the highest supported tier on each axis — Syntopik degrades gracefully to plain terminal Markdown with zero lost rigor:

| Learning Primitive | Tier 1 — Rich Interactive UI | Tier 2 — Native Structured Tool | Tier 3 — Universal Calm Markdown Fallback |
| :--- | :--- | :--- | :--- |
| **1. Graded Probe / Predict / Check** | `synt_quiz_card` (`mlh_quiz_card`) on SEP-1865 UI hosts (bare options, `"I don't know"`, `0–100%` confidence rail, thinking box, sketch pad) | Antigravity `ask_question` · Claude `AskUserQuestion` (`description: ""`) · Codex `request_user_input` (bare claims, zero leaks, include `"I don't know"`) | Single bordered Markdown card: bare options `A)..D)` + `E) I don't know`, inviting **choice + `0–100%` confidence + 1-line why** |
| **2. Practice, Parsons & Bug-Hunt** | `synt_practice_card` (`mode: "standard" \| "parsons" \| "faded"`, server-sealed `modelSolution`/`canonicalOrder`, keyboard reorder + blanks) | Terminal MCP `synt_practice_card` (prints stem or shuffled `A..F` blocks while keeping solution sealed) or pre-committed `/tmp` solution | Clean Markdown stem (or `A..F` blocks) → `R0–R5` hints (`<= 35`w, 1 rung/turn) → verbatim reveal + **Pause Law** |
| **3. Ambient Cross-Session Memory** | `synt_capture_concept`, `synt_get_frontier`, `synt_log_review` (headless MCP in any chat) | `syntopik review` / `syntopik run srs_engine ...` | Append to `knowledge-graph/propositions.jsonl` + `revlog.jsonl` via `srs_engine.py` |
| **4. Visuals, Occlusion & Live Workbench** | `<agent-embed>` · Claude Artifact · T3 `html_render` · `syntopik serve` live 2-way Studio (SVG occlusion, FIRe cards, 1-click CBT auto-save) | Deterministic SVG/PNG (`math_plot`, `physics_draw`, `circuit_draw`, `chem_3d`, `pdf_studio annotate --occlude`) + offline `demos/<slug>.html` | Inline Mermaid / KaTeX / `<= 4`-row table + `file://` link to `demos/<slug>.html` / `dashboard.html` |
| **5. Lesson DAG & Heavy Processing** | `synt_lesson_studio` side-pane + parallel subagents (`invoke_subagent`, `Agent`, `spawn_agent`) | `update_steps` · `TaskCreate`/`TaskUpdate` · `update_plan` + bounded CLI (`pdf_studio`, `ingest_source`, `research_scout`) | 1-line `[Step k/N · Landmark]` breadcrumb + `<= 5` PDF pages or `<= 3` searches/turn |

---

## Step 4 — Portable 4-Tier Research Cascade & Calm Ceilings

When scouting open sources without local files (`topic-onboarding` Mode B, max `8` curated sources in `sources/_curated/<slug>/manifest.json`):
1. **Tier 1 (`research` MCP active)**: `web_search`, `read_url`, `batch_extract`, `crawl_page` — ALWAYS pass integer `runtime_seconds` (`60–180`) and `result_detail: "compact"`. Never retry blocked Google scrapes.
2. **Tier 2 (`syntopik run research_scout` → portable `researchctl`)**: auto-discovers `$RESEARCHCTL_BIN` / `PATH` / `~/.local/bin/researchctl`.
3. **Tier 3 (Built-in Web Search + Keyless Scholarly APIs)**: `syntopik run research_scout scholar "<query>" --sources arxiv,openalex,crossref,wikipedia --json` (pure stdlib HTTPS).
4. **Tier 4 (Pure Offline First-Principles)**: synthesize from local `sources/`, `knowledge-graph/`, and deterministic verifiers (explicitly labeled `offline-constructed`, zero fabricated URLs).

**Always-On Calm Ceilings** (verified offline by `syntopik run pedagogy_eval lint|bench`): `<= 160`w prose/turn (`90–130`w target), strictly `1` active demand/turn, `<= 40`w side doubts, zero sycophancy, zero streaks/confetti, and deterministic verification before showing formulas, code, or answers.
