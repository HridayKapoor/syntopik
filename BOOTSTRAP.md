# Syntopik v3.2 — Universal 10-Second Bootstrap & 14-Harness Guide (`BOOTSTRAP.md`)

Give this file (or `AGENTS.md`) to **any of 14 AI coding agents, IDE assistants, CLI harnesses, or browser learners** to turn any workspace into a deterministic, adaptive FSRS-6 + FIRe Deep-Mastery Operating System in under 10 seconds across **macOS, Linux, and Windows** — via native stdio MCP OR **zero-Node loopback Streamable-HTTP MCP (`POST http://127.0.0.1:4173/mcp`)**.

---

## Step 1 — 14-Agent Universal Compatibility Matrix (Stdio & Zero-Node Loopback `POST /mcp`)

Run `syntopik doctor --compat` anytime to detect installed harnesses on your machine and print exact 1-line setup commands. Pass `--transport http` on `syntopik attach` to connect any HTTP-capable agent directly to `syntopik serve`'s pure-Python loopback `POST /mcp` endpoint (zero Node.js required; requires `X-Syntopik-Token` or `Authorization: Bearer`, automatically blocked `403` when `--lan` is active):

| # | Harness | Single 10-Second Command | Native Rules & MCP Config (Host-Safe, Zero Git Diff) | Transports |
| :-: | :--- | :--- | :--- | :--- |
| 1 | **Google Antigravity 2.0** | `syntopik connect antigravity` *(or `uvx --from git+https://github.com/HridayKapoor/syntopik syntopik connect antigravity`)* | `GEMINI.md` *(preserved)*, `.agents/skills`, `.agents/hooks.json`; global `~/.gemini/config/plugins/syntopik*/plugin.json` (23-tool SEP-1865 Studio) | `stdio` |
| 2 | **Anthropic Claude Code** | `/plugin marketplace add HridayKapoor/syntopik` → `/plugin install syntopik` *(or `syntopik attach . --harness claude`)* | `CLAUDE.md` *(preserved)*, `.claude/skills`, `.claude/settings.json`; `.mcp.json` (`mcpServers`: stdio or `{type:"http", url, headers}`) | `stdio` · `http` |
| 3 | **OpenAI Codex / T3 Code** | `npx skills add HridayKapoor/syntopik && syntopik attach . --harness codex` | `AGENTS.md` *(preserved)*, `.agents/skills`, `.codex/hooks.json`; `.codex/config.toml` (`[mcp_servers.syntopik-studio]`) | `stdio` · `http` |
| 4 | **Cursor** | `npx skills add HridayKapoor/syntopik && syntopik attach . --harness cursor` | `.cursor/rules/syntopik.mdc` (`alwaysApply: false`); `.cursor/mcp.json` (`mcpServers`: stdio or `{url, headers}`) | `stdio` · `http` |
| 5 | **Windsurf** | `npx skills add HridayKapoor/syntopik && syntopik attach . --harness windsurf` | `.windsurf/rules/syntopik.md` (`trigger: model_decision`); global `~/.codeium/windsurf/mcp_config.json` (`mcpServers`, HTTP uses `serverUrl`) | `stdio` · `http` |
| 6 | **GitHub Copilot (VS Code)** | `npx skills add HridayKapoor/syntopik && syntopik attach . --harness copilot` | `.github/instructions/syntopik.instructions.md`; `.vscode/mcp.json` (`servers` root key, `{type:"stdio"\|"http"}`) | `stdio` · `http` |
| 7 | **Gemini CLI** | `gemini extensions install https://github.com/HridayKapoor/syntopik` *(or `syntopik attach . --harness gemini`)* | `GEMINI.md` (`@AGENTS.md` on L1), `gemini-extension.json`; `.gemini/settings.json` (`mcpServers`, HTTP uses `httpUrl`) | `stdio` · `http` |
| 8 | **OpenCode** | `npx skills add HridayKapoor/syntopik && syntopik attach . --harness opencode` | `AGENTS.md` *(preserved)*; `opencode.json` (`mcp` root key: `type:"local"` stdio or `type:"remote"` HTTP) | `stdio` · `http` |
| 9 | **Cline** | `syntopik attach . --harness cline [--transport http]` | `.clinerules/syntopik.md` *(preserves existing regular `.clinerules` file!)*; global `cline_mcp_settings.json` (HTTP `type:"streamableHttp"`) | `stdio` · `http` |
| 10 | **Roo Code** | `syntopik attach . --harness roo [--transport http]` | `.roo/rules/syntopik.md`; `.roo/mcp.json` (`mcpServers`, HTTP `type:"streamable-http"`) | `stdio` · `http` |
| 11 | **Zed** | `syntopik attach . --harness zed [--transport http]` | `.rules` *(only if none of Zed's 9 higher/peer rule files exist; else `.agents/syntopik-rules.md`)*; `.zed/settings.json` (`context_servers`, JSONC-safe) | `stdio` · `http` |
| 12 | **Aider** | `syntopik attach . --harness aider` | `.agents/syntopik-rules.md` merged safely into `.aider.conf.yml` `read:` list *(rules-only CLI, preserves host `AGENTS.md`)* | Rules CLI |
| 13 | **Continue** | `syntopik attach . --harness continue [--transport http]` | `.continue/rules/syntopik.md`; `.continue/mcpServers/syntopik.yaml` (YAML `mcpServers:`, HTTP uses `requestOptions.headers`) | `stdio` · `http` |
| 14 | **Kiro** | `syntopik attach . --harness kiro [--transport http]` | `.kiro/steering/syntopik.md` (`inclusion: manual`); `.kiro/settings/mcp.json` (`mcpServers`, stdio or `{url, headers}`) | `stdio` · `http` |
| — | **Browser-First / Zero-Clone** | `npx github:HridayKapoor/syntopik serve --open` *(or `syntopik demo --open`)* | Auto-creates `~/.syntopik` vault, opens Day-1 4-Door Launchpad + Studio + Mastery Gym (`http://127.0.0.1:4173`) + loopback `POST /mcp` | Browser + HTTP |

*Windows tip*: pass `--copy-skills` on `syntopik attach` if Windows Developer Mode symlinks are off. Ephemeral runner caches (`_npx`, `uv/archive-v0`, `pipx`, `/tmp`) are auto-detected and NEVER baked into host configs.

### Silent Idempotent First-Run Bootstrap (`< 2s`, 3-OS Windows-Safe, Bare Python 3.12+ Stdlib)
On a fresh clone or worktree (whenever `knowledge-graph/graph-data.js` is absent), run once:
```bash
# macOS / Linux:
syntopik init --json 2>/dev/null || .venv/bin/python -B scripts/setup_harness.py auto --json 2>/dev/null || python3 -B scripts/setup_harness.py auto --json
# Windows PowerShell:
python -B scripts/setup_harness.py auto --json
```
Creates missing learner folders & gitignored ledgers (`propositions.jsonl`, `chains.jsonl`, `revlog.jsonl`, `mistakes.jsonl`, `reading-queue.jsonl`, `exam-attempts.jsonl`, `forks.jsonl`, `arenas.jsonl`, `decisions.jsonl`, `derivations.jsonl`, `viva-sessions.jsonl`, `learner-profile.json`, `whiteboard-session.json`, `review-queue.md`, `graph-data.js`) without ever overwriting existing data, and repairs Windows `.claude/skills` symlinks/copies cleanly.

---

## Step 2 — Unified `syntopik` v3.2 CLI Reference & Workspace Safety

All **28 CLI verbs** run on **bare Python 3.12+ stdlib** (or Node 20+ via `npx`) and automatically resolve bundled scripts whether inside a direct clone, wheel, or attached repository:

| Command | Purpose (Direct-Clone Script Fallback) |
| :--- | :--- |
| `syntopik next [--energy low\|normal\|deep] [--domain <S>] [--json]` | **Pedagogical Brainstem**: fuse FSRS-6/FIRe, `<=72h` Taper Mode, Learner Psychology, causal $\Delta H$, Blunder Forks, Rosetta `isomorphicBridge`, Blind Interleave, and whiteboard pins into one `<= 180`-token envelope (`syntopik.brainstem`). |
| `syntopik readiness [--exam-date YYYY-MM-DD] [--subject <S>] [--max-marks N] [--premortem-only] [--json]` | **Exam-Day Readiness Forecaster, `<=72h` Taper & Pre-Mortem Shield**: project FSRS-6 $R(t_{\text{exam}})$, 4-tier Evidence, Forks, EV discipline, top-3 ROI levers, and non-fabricated 3-bullet If-Then Shield (`syntopik.readiness`). |
| `syntopik interleave [build\|check\|complete\|stats\|export-cbt] [--n 6] [--subjects ...] [--seed N] [--json]` | **Blind-Chapter Interleaved Drill Generator**: strip chapter/lecture cues, enforce zero blocked runs, seal answers server-side, grade Step-1 governing principle first, track confusion matrix, or export blind CBT specs (`syntopik.interleave`). |
| `syntopik import <target \| --pocket-sync file> [--kind auto\|anki\|cards\|notebook\|notes_batch\|youtube\|arxiv\|repo\|url\|notes] [--slug <s\|--chapter s>]` | **Universal Ecosystem Importer**: Anki `.apkg` (Basic+Cloze, zero fake mastery), Quizlet/Anki CSV/TSV, Jupyter `.ipynb` (zero-exec AST test-case extraction), Notion `.zip`/Obsidian MD folders (UUID stripping + link rewrite), YouTube, arXiv, repo, URL, PDF, Pocket sync (`syntopik.importers`, `syntopik.ingest`). |
| `syntopik attach <dir> [--harness auto\|all\|<14 ids>] [--transport stdio\|http] [--http-url <url>] [--vault global\|local] [--copy-skills]` | Wire Syntopik into any existing repo across 14 agents via `.syntopik/` + `.git/info/exclude` (zero diff to tracked `.gitignore` or host `AGENTS.md`, zero ephemeral cache paths, `syntopik.agents_compat`). |
| `syntopik connect <harness> [--bundle studio\|full]` | Install global plugin, extension, or print exact config for any of the 14 harnesses (`scripts/install_mcp_app.py`, `syntopik.agents_compat`). |
| `syntopik doctor [--compat] [--fix] [--core-only] [--json]` & `syntopik sync` | Audit workspace, 27 stdlib modules, hooks, 17 skills, rule budgets, or print the 14-agent `--compat` matrix (`scripts/study_doctor.py`); rebuild `graph-data.js`. |
| `syntopik serve [--port 4173] [--open] [--lan] [--workspace <dir>]` | Launch 2-way local Studio + Mastery Gym + zero-Node loopback `POST /mcp` Streamable-HTTP MCP server (`127.0.0.1`, CSRF/Bearer token, SSE, offline `file://` fallback; `/mcp` blocked under `--lan`). |
| `syntopik rosetta [scan\|bridge\|link\|archetypes] [--prop-id <id>] [--source <a> --target <b> --archetype <arch>] [--json]` | **Structural Isomorphism Rosetta Engine**: discover cross-domain twins across 10 archetypes, emit bijection openers + `breaksAt` guards, and link `isomorphic_to` chords (`0.25` cross-domain FIRe, `syntopik.isomorphism`). |
| `syntopik sync-delta [status\|export\|verify\|merge] [--out <p>] [--file <p>] [--since <iso>] [--dry-run] [--json]` | **Offline-First CRDT Delta Sync**: export, SHA-256 verify, and monotonically merge `syntopik-delta-v1` snapshots across devices (`syntopik.sync_delta`). |
| `syntopik calibrate-policy [--apply] [--min-events 12] [--json]` | **Longitudinal Policy Replay**: 70/30 chronological TimeSeriesSplit replay adopting bounded `learner_model` shifts on OOS gain `>= 0.005` (`benchmarks/syntopik_bench.py --replay`). |
| `syntopik proof-diff --id <deriv_id> --steps-json <json_or_path> [--no-spawn-fork] [--json]` | **Proof-Graph Handwriting Diff**: align handwritten steps against `derivations.jsonl`, emit zero-leak `R0–R2` nudges, auto-spawn Blunder Forks & $\Delta H$ causal probes (`scripts/derivation_forge.py diff`). |
| `syntopik profile [status\|calibrate\|policy\|override\|reset] [--json]` | Manage 3-Cadence Non-VARK Adaptive Learner Profile, Kalyuga `TeachingPolicy`, and transparent overrides (`scripts/learner_profile.py`). |
| `syntopik drop <inputs...> [--slug <slug>] [--title "<T>"] [--json]` | 10-second mixed-source kickoff (PDF, `.apkg`, `.ipynb`, CSV, Notion `.zip`, URL, YouTube, repo) enforcing Teacher-First spine order + Probe #1 (`syntopik.brainstem`). |
| `syntopik demo [--pack <slug\|all>] [--open] [--workspace <dir>]` | Load bundled zero-PII starter pack(s) into a clean sandbox workspace (`~/.syntopik-demo`) with optional browser Studio launch. |
| `syntopik debrief [--ref HEAD~1..HEAD \| --diff-file <p>] [--dry-run]` | **Work Mentor Debrief**: crystallize git diff bugs/invariants into Blunder Forks, Arenas, and Knowledge Graph propositions (`syntopik.work_mentor`). |
| `syntopik mode [mentor\|study] [--off] [--json]` | Show (no argument) or toggle Anti-Vibe-Coding Work Mentor mode vs Study mode in `knowledge-graph/mentor-config.json`. |
| `syntopik sunset <slug> [--apply] [--json]` | **Semester Sunset**: snapshot chapter pack, keep core axioms at $R=0.80$, and archive low-leverage leaf nodes (`scripts/graph_intel.py`). |
| `syntopik pace [--rebalance] [--ics <file.ics>] [--json]` | Multi-course buffer-protected Auto-Pacer with RFC-5545 `.ics` calendar export (`scripts/daily_plan.py --pace`). |
| `syntopik board [status\|push\|pin\|clear] [--kind ...] [--json]` | Inspect or push frames/pins to `whiteboard-session.json` (Live AI-to-Whiteboard SSE bus, `syntopik.whiteboard`). |
| `syntopik bench [--days 60] [--seed 42] [--svg] [--markdown] [--check]` | Run the seeded 60-day multi-persona **Syntopik-Eval** cognitive ablation benchmark & CI gate (`benchmarks/syntopik_bench.py`). |
| `syntopik init [dir] [--vault global\|local]` | Non-destructively initialize workspace or `~/.syntopik` vault (`scripts/setup_harness.py auto`). |
| `syntopik review [--web\|--tui\|--recover\|--resume] [--energy low\|normal\|deep]` | FIRe-compressed FSRS-6 reviews, calm backlog recovery, or guilt-free Cold-Start Re-Entry (`scripts/srs_engine.py`, `scripts/daily_plan.py`). |
| `syntopik triage [--exam-in 48h] [--sleep-hours 7] [--crib-sheet]` | 48-hour Pareto exam triage plan (`salvage_70pct`/`protect_20pct`/`explicitly_cut`) + 1-page Anti-Mastery crib sheet. |
| `syntopik pack <export\|inspect\|verify\|import> ...` | Export, verify SHA-256, inspect, or merge zero-PII `.syntopik-pack.zip` Study Packs (`syntopik.packs`). |
| `syntopik export <obsidian\|pocket\|anki\|pack\|json\|dossier> [--out\|--vault <p>]` | Bidirectional Obsidian/Mnemonic sync, offline Pocket HTML deck, visual Anki `.apkg`, Study Pack, JSON, or Mastery Dossier. |
| `syntopik run <engine> [args...]` | Portable passthrough to any bundled script (`exam_runner`, `math_verify`, `code_verify`, `fork_replay`, `falsify_arena`, `decision_blitz`, `derivation_forge`, `viva_chamber`, `pedagogy_eval`, etc.). |

**Workspace Resolution Precedence** (honoring `MLH_*` fallbacks): explicit flag → `SYNT_WORKSPACE` → `$CLAUDE_PROJECT_DIR` / `cwd` (if `knowledge-graph/` or `.syntopik/` exists) → writable clone root → `~/.syntopik` (safe inside read-only plugin caches!). Optional overrides: `SYNT_KG_DIR`, `SYNT_CHAPTERS_DIR`, `SYNT_DATA_DIR`.

---

## Step 3 — 4-Axis Multi-Harness UX Capability Matrix

Detect your host's tools and pick the highest supported tier on each axis — Syntopik degrades gracefully to plain terminal Markdown with zero lost rigor:

| Learning Primitive | Tier 1 — Rich Interactive UI | Tier 2 — Native Structured Tool | Tier 3 — Universal Calm Markdown Fallback |
| :--- | :--- | :--- | :--- |
| **1. Graded Probe / Predict / Check** | `synt_quiz_card` (`mlh_quiz_card`) on SEP-1865 UI hosts (bare options, `"I don't know"`, `0–100%` confidence rail, thinking box, sketch pad) | Antigravity `ask_question` · Claude `AskUserQuestion` (`description: ""`) · Codex `request_user_input` (bare claims, zero leaks, include `"I don't know"`) | Single bordered Markdown card: bare options `A)..D)` + `E) I don't know`, inviting **choice + `0–100%` confidence + 1-line why** |
| **2. Practice, Parsons, Faded & Blackout** | `synt_practice_card` (`mode: "standard" \| "parsons" \| "faded" \| "blackout"`, server-sealed `modelSolution`/`canonicalOrder`/blackout answers) | Terminal MCP `synt_practice_card` (prints stem, shuffled `A..F` blocks, or `[HIDDEN]` blackout steps while keeping solutions sealed) or `derivation_forge` | Clean Markdown stem / blocks / `[HIDDEN]` steps → `R0–R5` hints (`<= 35`w, 1 rung/turn) → verbatim reveal + **Pause Law** |
| **3. Brainstem, Readiness, Interleave & Memory** | 7 headless MCP tools (stdio `syntopik-studio` OR zero-Node loopback `POST /mcp`): `synt_next_move`, `synt_whiteboard_step`, `synt_capture_concept`, `synt_get_frontier`, `synt_log_review`, `synt_readiness`, `synt_interleave` | `syntopik next`, `syntopik readiness`, `syntopik interleave`, `syntopik board push`, `syntopik profile status`, `syntopik review` | Read `learner-profile.json` & `whiteboard-session.json`; append `propositions.jsonl` + `revlog.jsonl` via `srs_engine.py` |
| **4. Visuals, `:::explore` Labs, Gym & 6-Type CBT** | `<agent-embed>` · Claude Artifact · T3 `html_render` · `syntopik serve` live 2-way Studio (Day-1 4-Door Launchpad, 6-station **Mastery Gym** w/ Blind Interleave, Exam Readiness + Pre-Mortem + 6-type EV-calibrated CBT) | Deterministic SVG/PNG (`math_plot`, `physics_draw`, `circuit_draw`, `chem_3d`, `pdf_studio annotate --occlude`) + offline `demos/<slug>.html` | Inline Mermaid / KaTeX / `<= 4`-row table + `file://` link to `demos/<slug>.html` / `dashboard.html` |
| **5. Lesson DAG & Heavy Processing** | `synt_lesson_studio` side-pane (with live `TeachingPolicy` override strip) + parallel subagents (`invoke_subagent`, `Agent`, `spawn_agent`) | `update_steps` · `TaskCreate`/`TaskUpdate` · `update_plan` + bounded CLI (`pdf_studio`, `ingest_source`, `research_scout`) | 1-line `[Step k/N · Landmark]` breadcrumb + `<= 5` PDF pages or `<= 3` searches/turn |

---

## Step 4 — Portable 4-Tier Research Cascade & Calm Ceilings

When scouting open sources without local files (`topic-onboarding` Mode B, max `8` curated sources in `sources/_curated/<slug>/manifest.json`):
1. **Tier 1 (`research` MCP active)**: `web_search`, `read_url`, `batch_extract`, `crawl_page` — ALWAYS pass integer `runtime_seconds` (`60–180`) and `result_detail: "compact"`. Never retry blocked Google scrapes.
2. **Tier 2 (`syntopik run research_scout` → portable `researchctl`)**: auto-discovers `$RESEARCHCTL_BIN` / `PATH` / `~/.local/bin/researchctl`.
3. **Tier 3 (Built-in Web Search + Keyless Scholarly APIs)**: `syntopik run research_scout scholar "<query>" --sources arxiv,openalex,crossref,wikipedia --json` (pure stdlib HTTPS).
4. **Tier 4 (Pure Offline First-Principles)**: synthesize from local `sources/`, `knowledge-graph/`, and deterministic verifiers (explicitly labeled `offline-constructed`, zero fabricated URLs).

**Always-On Calm Ceilings** (verified offline by `syntopik run pedagogy_eval lint|bench` — 40 scenarios — and `syntopik bench --check`): `<= 160`w prose/turn (`90–130`w target, tightened to `<= 110`w on overload/fatigue), strictly `1` active demand/turn, `<= 40`w side doubts, zero sycophancy, zero VARK myths, zero streaks/confetti, and deterministic verification before showing formulas, code, or answers.
