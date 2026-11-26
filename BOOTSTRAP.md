# Syntopik v3.0 — Universal 10-Second Bootstrap & Multi-Harness Guide (`BOOTSTRAP.md`)

Give this file (or `AGENTS.md`) to **any AI coding agent, IDE assistant, CLI harness, or browser learner** to turn any workspace into a deterministic, adaptive FSRS-6 + FIRe Deep-Mastery Operating System in under 10 seconds across **macOS, Linux, and Windows**.

---

## Step 1 — 10-Second Integration Matrix (Every User & Harness)

| User / Harness | Single 10-Second Command | What Works Immediately |
| :--- | :--- | :--- |
| **Browser-First / Zero-Clone Learner** | `npx github:HridayKapoor/syntopik serve --open` *(or `syntopik demo --open` for instant zero-PII starter packs)* | Auto-creates `~/.syntopik` vault, opens live 2-way Studio + Mastery Gym (`http://127.0.0.1:4173`), Live AI Chalkboard, PDF/URL drop, FIRe flashcards, SVG occlusion, `:::explore` labs, 1-click CBT grading — zero git clone. |
| **Claude Code (Any Repo)** | `/plugin marketplace add HridayKapoor/syntopik` → `/plugin install syntopik` | All 17 `/syntopik:*` skills, `syntopik-studio` v3.0 MCP (incl. `synt_next_move` & `synt_whiteboard_step`), and cache-safe `SessionStart` / `PostToolUse` hooks in any repo. |
| **Cursor / Windsurf / Copilot / Cline / Roo / Codex / OpenCode** | `npx skills add HridayKapoor/syntopik` *(+ optional `npx github:HridayKapoor/syntopik attach .`)* | Installs all 17 `agentskills.io` skills + optional thin pointer rule + harness-native MCP config (`.vscode/mcp.json` `"servers"`, `opencode.json` `"mcp"`, `.cursor/mcp.json`, `.codex/config.toml`). Pass `--copy-skills` on Windows if Developer Mode symlinks are off. |
| **Google Antigravity 2.0 (Any Repo)** | `uvx --from git+https://github.com/HridayKapoor/syntopik syntopik connect antigravity` | Installs global `~/.gemini/config/plugins/syntopik/` bundle (`plugin.json`, 17 skills, rules, hooks, SEP-1865 Studio v3.0 MCP) active across every workspace. |
| **Gemini CLI (Any Repo)** | `gemini extensions install https://github.com/HridayKapoor/syntopik` | Installs `gemini-extension.json`, `GEMINI.md` (with `@AGENTS.md` on line 1), 17 skills, hooks, and `syntopik-studio` v3.0 MCP server. |
| **Direct Git Clone / Power User** | `git clone https://github.com/HridayKapoor/syntopik && cd syntopik && syntopik serve --open` | Full local STEM/Manim/Typst stack + `syntopik doctor` + `syntopik bench --check` + offline `file://` fallback. |

### Silent Idempotent First-Run Bootstrap (`< 2s`, 3-OS Windows-Safe, Bare Python 3.12+ Stdlib)
On a fresh clone or worktree (whenever `knowledge-graph/graph-data.js` is absent), run once:
```bash
# macOS / Linux:
syntopik init --json 2>/dev/null || .venv/bin/python -B scripts/setup_harness.py auto --json 2>/dev/null || python3 -B scripts/setup_harness.py auto --json
# Windows PowerShell:
python -B scripts/setup_harness.py auto --json
```
Creates missing learner folders & gitignored ledgers (`propositions.jsonl`, `chains.jsonl`, `revlog.jsonl`, `mistakes.jsonl`, `reading-queue.jsonl`, `exam-attempts.jsonl`, `forks.jsonl`, `arenas.jsonl`, `decisions.jsonl`, `derivations.jsonl`, `viva-sessions.jsonl`, `learner-profile.json`, `whiteboard-session.json`, `review-queue.md`, `graph-data.js`) without ever overwriting existing data, and repairs Windows `.claude/skills` symlinks/copies cleanly (zero POSIX-only shell syntax).

---

## Step 2 — Unified `syntopik` v3.0 CLI Reference & Workspace Safety

All CLI verbs run on **bare Python 3.12+ stdlib** (or Node 20+ via `npx`) and automatically resolve bundled scripts whether inside a direct clone, wheel, or attached repository:

| Command | Purpose (Direct-Clone Script Fallback) |
| :--- | :--- |
| `syntopik next [--energy low\|normal\|deep] [--domain <S>] [--json]` | **Pedagogical Brainstem**: fuse FSRS-6/FIRe, Learner Psychology, causal root-cause $\Delta H$, Blunder Forks, and whiteboard pins into one `<= 180`-token Action Envelope (`syntopik.brainstem`). |
| `syntopik profile [status\|calibrate\|policy\|override\|reset] [--json]` | Manage 3-Cadence Non-VARK Adaptive Learner Profile, Kalyuga `TeachingPolicy`, and transparent overrides (`scripts/learner_profile.py`). |
| `syntopik drop <inputs...> [--slug <slug>] [--title "<T>"] [--json]` | 10-second mixed-source kickoff (PDF, URL, YouTube, repo, notes) enforcing Teacher-First spine order + Probe #1 (`syntopik.brainstem`). |
| `syntopik demo [--pack <slug\|all>] [--open] [--workspace <dir>]` | Load bundled zero-PII starter pack(s) into a clean sandbox workspace (`~/.syntopik-demo`) with optional browser Studio launch. |
| `syntopik debrief [--ref HEAD~1..HEAD \| --diff-file <p>] [--dry-run]` | **Work Mentor Debrief**: crystallize git diff bugs/invariants into Blunder Forks, Arenas, and Knowledge Graph propositions (`syntopik.work_mentor`). |
| `syntopik mode [mentor\|study] [--off] [--json]` | Toggle Anti-Vibe-Coding Work Mentor mode vs Study mode in `study-config.json`. |
| `syntopik sunset <slug> [--apply] [--json]` | **Semester Sunset**: snapshot chapter pack, keep core axioms at $R=0.80$, and archive low-leverage leaf nodes (`scripts/graph_intel.py`). |
| `syntopik pace [--rebalance] [--ics <file.ics>] [--json]` | Multi-course buffer-protected Auto-Pacer with RFC-5545 `.ics` calendar export (`scripts/daily_plan.py --pace`). |
| `syntopik board [status\|push\|pin\|clear] [--kind ...] [--json]` | Inspect or push frames/pins to `whiteboard-session.json` (Live AI-to-Whiteboard SSE bus, `syntopik.whiteboard`). |
| `syntopik bench [--days 60] [--seed 42] [--svg] [--markdown] [--check]` | Run the seeded 60-day multi-persona **Syntopik-Eval** cognitive ablation benchmark & CI gate (`benchmarks/syntopik_bench.py`). |
| `syntopik init [dir] [--vault global\|local]` | Non-destructively initialize workspace or `~/.syntopik` vault (`scripts/setup_harness.py auto`). |
| `syntopik attach <dir> [--harness auto\|all\|...] [--vault global\|local] [--copy-skills]` | Wire Syntopik into any existing repo via `.syntopik/` + `.git/info/exclude` (zero diff to tracked `.gitignore` or host `AGENTS.md`). |
| `syntopik connect <harness>` | Install global plugin, MCP config, or extension (`scripts/install_mcp_app.py`). |
| `syntopik serve [--port 4173] [--open] [--lan] [--workspace <dir>]` | Launch 2-way local Studio + Mastery Gym server (`127.0.0.1`, per-process CSRF token, SSE live refresh, offline `file://` fallback). |
| `syntopik import <target \| --pocket-sync file> [--slug <s\|--chapter s>]` | Universal importer (YouTube, arXiv, GitHub repo, URL, PDF/notes, Pocket sync) (`scripts/ingest_source.py`). |
| `syntopik review [--web\|--tui\|--recover\|--resume] [--energy low\|normal\|deep]` | FIRe-compressed FSRS-6 reviews, calm backlog recovery, or guilt-free Cold-Start Re-Entry (`scripts/srs_engine.py`, `scripts/daily_plan.py`). |
| `syntopik triage [--exam-in 48h] [--sleep-hours 7] [--crib-sheet]` | 48-hour Pareto exam triage plan (`salvage_70pct`/`protect_20pct`/`explicitly_cut`) + 1-page Anti-Mastery crib sheet. |
| `syntopik pack <export\|inspect\|verify\|import> ...` | Export, verify SHA-256, inspect, or merge zero-PII `.syntopik-pack.zip` Study Packs (`syntopik.packs`). |
| `syntopik export <obsidian\|pocket\|anki\|pack\|json\|dossier> [--out\|--vault <p>]` | Bidirectional Obsidian/Mnemonic sync, offline Pocket HTML deck, visual Anki `.apkg`, Study Pack, JSON, or Mastery Dossier. |
| `syntopik doctor [--fix] [--core-only]` & `syntopik sync` | Verify workspace, hooks, 17 skills, context budgets, and engines (`scripts/study_doctor.py`); rebuild `graph-data.js`. |
| `syntopik run <engine> [args...]` | Portable passthrough to any bundled script (`math_verify`, `code_verify`, `fork_replay`, `falsify_arena`, `decision_blitz`, `derivation_forge`, `viva_chamber`, `pedagogy_eval`, etc.). |

**Workspace Resolution Precedence** (honoring `MLH_*` fallbacks): explicit flag → `SYNT_WORKSPACE` → `$CLAUDE_PROJECT_DIR` / `cwd` (if `knowledge-graph/` or `.syntopik/` exists) → writable clone root → `~/.syntopik` (safe inside read-only plugin caches!). Optional overrides: `SYNT_KG_DIR`, `SYNT_CHAPTERS_DIR`, `SYNT_DATA_DIR`.

---

## Step 3 — 4-Axis Multi-Harness UX Capability Matrix

Detect your host's tools and pick the highest supported tier on each axis — Syntopik degrades gracefully to plain terminal Markdown with zero lost rigor:

| Learning Primitive | Tier 1 — Rich Interactive UI | Tier 2 — Native Structured Tool | Tier 3 — Universal Calm Markdown Fallback |
| :--- | :--- | :--- | :--- |
| **1. Graded Probe / Predict / Check** | `synt_quiz_card` (`mlh_quiz_card`) on SEP-1865 UI hosts (bare options, `"I don't know"`, `0–100%` confidence rail, thinking box, sketch pad) | Antigravity `ask_question` · Claude `AskUserQuestion` (`description: ""`) · Codex `request_user_input` (bare claims, zero leaks, include `"I don't know"`) | Single bordered Markdown card: bare options `A)..D)` + `E) I don't know`, inviting **choice + `0–100%` confidence + 1-line why** |
| **2. Practice, Parsons, Faded & Blackout** | `synt_practice_card` (`mode: "standard" \| "parsons" \| "faded" \| "blackout"`, server-sealed `modelSolution`/`canonicalOrder`/blackout answers) | Terminal MCP `synt_practice_card` (prints stem, shuffled `A..F` blocks, or `[HIDDEN]` blackout steps while keeping solutions sealed) or `derivation_forge` | Clean Markdown stem / blocks / `[HIDDEN]` steps → `R0–R5` hints (`<= 35`w, 1 rung/turn) → verbatim reveal + **Pause Law** |
| **3. Brainstem, Whiteboard & Memory** | `synt_next_move`, `synt_whiteboard_step`, `synt_capture_concept`, `synt_get_frontier`, `synt_log_review` (`hintsUsed`, `durationMs`) | `syntopik next`, `syntopik board push`, `syntopik profile status`, `syntopik review` | Read `learner-profile.json` & `whiteboard-session.json`; append `propositions.jsonl` + `revlog.jsonl` via `srs_engine.py` |
| **4. Visuals, `:::explore` Labs & Gym** | `<agent-embed>` · Claude Artifact · T3 `html_render` · `syntopik serve` live 2-way Studio & 6-station **Mastery Gym** (`:::explore` zero-`eval` RPN labs, Live Chalkboard, Blunder Fork, Arena, Blitz, Blackout/Axiom-Cut, Viva) | Deterministic SVG/PNG (`math_plot`, `physics_draw`, `circuit_draw`, `chem_3d`, `pdf_studio annotate --occlude`) + offline `demos/<slug>.html` | Inline Mermaid / KaTeX / `<= 4`-row table + `file://` link to `demos/<slug>.html` / `dashboard.html` |
| **5. Lesson DAG & Heavy Processing** | `synt_lesson_studio` side-pane (with live `TeachingPolicy` override strip) + parallel subagents (`invoke_subagent`, `Agent`, `spawn_agent`) | `update_steps` · `TaskCreate`/`TaskUpdate` · `update_plan` + bounded CLI (`pdf_studio`, `ingest_source`, `research_scout`) | 1-line `[Step k/N · Landmark]` breadcrumb + `<= 5` PDF pages or `<= 3` searches/turn |

---

## Step 4 — Portable 4-Tier Research Cascade & Calm Ceilings

When scouting open sources without local files (`topic-onboarding` Mode B, max `8` curated sources in `sources/_curated/<slug>/manifest.json`):
1. **Tier 1 (`research` MCP active)**: `web_search`, `read_url`, `batch_extract`, `crawl_page` — ALWAYS pass integer `runtime_seconds` (`60–180`) and `result_detail: "compact"`. Never retry blocked Google scrapes.
2. **Tier 2 (`syntopik run research_scout` → portable `researchctl`)**: auto-discovers `$RESEARCHCTL_BIN` / `PATH` / `~/.local/bin/researchctl`.
3. **Tier 3 (Built-in Web Search + Keyless Scholarly APIs)**: `syntopik run research_scout scholar "<query>" --sources arxiv,openalex,crossref,wikipedia --json` (pure stdlib HTTPS).
4. **Tier 4 (Pure Offline First-Principles)**: synthesize from local `sources/`, `knowledge-graph/`, and deterministic verifiers (explicitly labeled `offline-constructed`, zero fabricated URLs).

**Always-On Calm Ceilings** (verified offline by `syntopik run pedagogy_eval lint|bench` — 40 scenarios — and `syntopik bench --check`): `<= 160`w prose/turn (`90–130`w target, tightened to `<= 110`w on overload/fatigue), strictly `1` active demand/turn, `<= 40`w side doubts, zero sycophancy, zero VARK myths, zero streaks/confetti, and deterministic verification before showing formulas, code, or answers.
