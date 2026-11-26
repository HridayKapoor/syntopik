# Syntopik — Universal Self-Configuring Agent Bootstrap (`BOOTSTRAP.md`)

Give this file (or `AGENTS.md`) to **any AI coding agent, IDE assistant, CLI harness, or chat environment** (Cursor, Windsurf, Cline, Roo Code, Zed, OpenCode, Gemini CLI, OpenAI Codex, T3 Code, Claude Code, Google Antigravity, or local/web chat with shell access) to turn the workspace into a deterministic, FSRS-backed Deep-Mastery Operating System in under 2 seconds.

---

## Step 1 — Silent Idempotent Self-Bootstrap (`< 2s`)

On a fresh clone, Git worktree, or first session (whenever `knowledge-graph/graph-data.js` is absent or the user says *"Set up Syntopik"*), run once:

```bash
.venv/bin/python -B scripts/setup_harness.py auto --json 2>/dev/null || python3 scripts/setup_harness.py auto --json
```

What `setup_harness.py auto` does safely and non-destructively:
1. Creates any missing learner directories (`chapters/`, `demos/`, `exams/`, `animations/`, `sources/`, `knowledge-graph/`) and initializes empty gitignored personal ledgers (`propositions.jsonl`, `chains.jsonl`, `mistakes.jsonl`, `exam-attempts.jsonl`, `review-queue.md`, `graph-data.js`) **without ever overwriting existing study data**.
2. Repairs `.claude/skills` directory links on Windows clones where Git checked out symlinks as plain text.
3. Probes available local capabilities (`.venv/bin/python`, `node`, `typst`, `TinyTeX`/`latex`, `ffmpeg`, `researchctl`, and active harness configs) and returns a machine-readable JSON capability report.

Optional environment overrides (honored across all Python & Node scripts, with backwards-compatible `MLH_*` fallbacks):
- `SYNT_WORKSPACE` (fallback `MLH_WORKSPACE`) — workspace root path (useful in Git worktrees like T3 Code).
- `SYNT_KG_DIR` (fallback `MLH_KG_DIR`) — custom/shared `knowledge-graph/` directory.
- `SYNT_CHAPTERS_DIR` (fallback `MLH_CHAPTERS_DIR`) — custom/shared `chapters/` directory.
- `SYNT_DATA_DIR` (fallback `MLH_DATA_DIR`) — persistent MCP Studio UI card state directory.

---

## Step 2 — Adapt Every Learning Interaction via the 4-Axis UX Capability Matrix

Syntopik never breaks when switching between a rich GUI IDE and a plain terminal CLI. Detect your host's available tools and pick the highest supported tier on each axis:

| Learning Primitive | Tier 1 — Rich Interactive UI | Tier 2 — Native Structured Tool | Tier 3 — Universal Calm Markdown Fallback |
| :--- | :--- | :--- | :--- |
| **1. Graded Probe / Predict / Check** | `synt_quiz_card` (`mlh_quiz_card`) on SEP-1865 UI hosts (shuffled bare options, `"I don't know"`, `0–100%` confidence rail, thinking box, sketch pad) | Antigravity `ask_question` · Claude `AskUserQuestion` (`description: ""`) · Codex `request_user_input` (bare claims, zero leaks, include `"I don't know"`) | Single clean bordered Markdown card: bare options `A)..D)` + `E) I don't know`, inviting **choice + `0–100%` confidence + 1-line reasoning** |
| **2. Practice Problem (`pose` / `conclude`)** | `synt_practice_card` (`mlh_practice_card`) on SEP-1865 UI hosts (server-sealed `modelSolution`, 1-active gate, sketch pad, verbatim reveal) | Terminal MCP `synt_practice_card` (renders stem cleanly while sealing `modelSolution` server-side) or verified solution pre-committed in `/tmp` | Clean Markdown stem → `R0–R5` hints (`<= 35` words, 1 rung/turn) → verbatim committed solution on conclude + **Pause Law** (*"Ready for the next problem?"*) |
| **3. Visuals, Plots, Code Traces & Labs** | Antigravity `generative_ui` `<agent-embed>` · Claude Artifact · T3 Code `html_preview` → `html_render` | Deterministic local SVG/PNG (`math_plot.py`, `physics_draw.py`, `circuit_draw.py`, `chem_3d.py`, `pdf_studio.py annotate`) + offline `demos/<slug>.html` | Inline Mermaid diagram / KaTeX math / compact `<= 4`-row table + clickable `demos/<slug>.html` link (always reference named features/controls!) |
| **4. Lesson DAG & Live Progress** | `synt_lesson_studio` (`mlh_lesson_studio`) side-pane (`Autopilot`, glowing SVG `Plan`, `Graph`, `Flashcards`, `Traps`) | Antigravity `update_steps` · Claude `TaskCreate`/`TaskUpdate` · Codex `update_plan` | Single 1-line top breadcrumb: `[Step 2/6 · Landmark Title · Predict -> Teach -> Check]` |
| **5. Heavy PDFs, Research & Verification** | Parallel subagents (`invoke_subagent`, `Agent`, `spawn_agent`) | Bounded CLI calls (`pdf_studio.py`, `research_scout.py`, `math_verify.py`, `code_verify.py`, `chem_physical.py`) | Sequential bounded chunks (`<= 5` PDF pages or `<= 3` searches per turn) keeping chat calm |

---

## Step 3 — Portable 4-Tier Deep Research & Syllabus Discovery Cascade

When onboarding a topic without user files (`topic-onboarding` Mode B) or verifying external curriculum/paper facts, follow this strict bounded cascade (zero hardcoded user paths, max `8` curated sources in `sources/_curated/<slug>/manifest.json`):

1. **Tier 1 — Active `research` MCP Server (if connected in session)**:
   - Tools: `web_search`, `read_url`, `engine_search`, `batch_extract`, `reddit_search`, `reddit_post`, `reddit_browse`, `crawl_page`, `crawl_site`.
   - **Mandatory Contract**: ALWAYS pass integer `runtime_seconds` (`60` for search/read, `90` for `batch_extract`, `120` for `crawl_page`, `<= 180` for bounded `crawl_site`) and `result_detail: "compact"` on `web_search`. Never retry blocked Google scrapes; use `web_search`.
2. **Tier 2 — Portable `researchctl` via `scripts/research_scout.py`**:
   - Auto-discovers `$RESEARCHCTL_BIN` → `which researchctl` → `~/.local/bin/researchctl` → `$LOCAL_RESEARCH_STACK_DIR` → sibling/home `local-research-stack`:
     ```bash
     .venv/bin/python -B scripts/research_scout.py search "MIT 6.824 Raft lecture notes" --json
     .venv/bin/python -B scripts/research_scout.py read "https://pdos.csail.mit.edu/6.824/papers/raft-extended.pdf" --json
     ```
3. **Tier 3 — Native Harness Web Search + Keyless Scholarly APIs (`research_scout.py scholar`)**:
   - Combine your harness's built-in search (`search_web` / `WebSearch` / `web_search`) with `scripts/research_scout.py scholar "<query>" --sources arxiv,openalex,crossref,wikipedia --json` (pure Python stdlib HTTPS, zero API keys).
4. **Tier 4 — Pure Offline First-Principles Mode**:
   - When offline, synthesize from local `sources/`, `knowledge-graph/`, and deterministic verifiers (`math_verify.py`, `code_verify.py`, `chem_physical.py`, `chem_3d.py`), explicitly labeling the syllabus as offline-constructed without fabricating external citations.

---

## Step 4 — Enforce Calm Anti-Overstimulation Pacing Everywhere

- **`<= 160` words of teaching prose per turn** (target `90–130` words; split larger ideas across two nodes).
- **Strictly `1` active cognitive demand per turn** (never bundle two questions or a wall of prose + a topic switch).
- **Zero sycophancy, zero streaks/confetti, zero unverified formulas/code** — check every non-trivial claim with `math_verify.py`, `code_verify.py`, or `chem_physical.py` before showing it, and persist progress to `knowledge-graph/` and `chapters/<slug>-tracker.json` in real time.
