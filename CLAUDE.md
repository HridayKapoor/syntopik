@AGENTS.md

# Syntopik — Anthropic Claude Code Bindings (`CLAUDE.md`)

`AGENTS.md` (imported above) defines Syntopik's pedagogy, calm ceilings (`<= 160`w prose/turn, `1` active demand/turn), plain-English routing, and deterministic verification.

## Installation & Claude Code Bindings

1. **Plugin & Skills (Any Repo or Direct Clone)**:
   - Install anywhere in 10 seconds: `/plugin marketplace add HridayKapoor/syntopik` → `/plugin install syntopik` (exposes all 17 skills, `syntopik-studio` MCP, and plugin-cache-safe hooks across any project).
   - Direct clones expose all 17 skills via `.claude/skills/` (`syntopik init` or `python3 scripts/setup_harness.py auto --json` repairs Windows symlinks and initializes gitignored ledgers).
2. **Graded Quizzes, Practice & Ambient MCP (`syntopik-studio`)**:
   - **SEP-1865 UI Hosts (Claude Desktop / VS Code)**: use `synt_quiz_card`, `synt_practice_card` (`mode: "standard" | "parsons" | "faded"`), and `synt_lesson_studio` (`mlh_*` aliases valid).
   - **Ambient Headless Capture (Any Chat)**: call `synt_capture_concept`, `synt_get_frontier`, and `synt_log_review` to capture insights, read FIRe-compressed frontiers, or log reviews without spawning UI.
   - **Claude Code Terminal CLI (`claude` TUI)**: prefer **`AskUserQuestion`** (`2–4` bare claims, `description: ""` on every option, never `(Recommended)`, include `{ label: "I don't know", description: "" }`); if `> 4` options, use one calm Markdown card (`A..D` + `E) I don't know`). For drills, `synt_practice_card` prints clean stems (and numbered `A..F` blocks in `parsons` mode) while keeping solutions sealed server-side.
3. **Progress, Visuals & Subagents**:
   - Track `4–8` DAG landmarks via **`TaskCreate`** / **`TaskUpdate`**.
   - Run verifiers & figure generators via `Bash` (`syntopik run <engine> ...` or `.venv/bin/python -B scripts/<engine>.py ...`), inspect PNGs via `Read` before presenting, save offline labs to `demos/<slug>.html`, and use `syntopik serve --open` for the live 2-way workbench.
   - Offload `>15`p PDFs, bounded research, or multi-step verification to **`Agent`** (`subagent_type: "Explore"` or background worker).
4. **Hooks (`.claude/settings.json` / `.claude-plugin/hooks.json`)**:
   - `SessionStart` injects live KG, traps, and Autopilot state; `PostToolUse` runs `sync-graph.mjs --quiet` on ledger updates (safe inside read-only plugin caches).
