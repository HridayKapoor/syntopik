@AGENTS.md

# Syntopik v3.0 — Anthropic Claude Code Bindings (`CLAUDE.md`)

`AGENTS.md` (imported above) defines Syntopik's pedagogy, `Active TeachingPolicy`, calm ceilings (`<= 160`w prose/turn, `1` active demand/turn), plain-English Mastery Gym routing, and deterministic verification.

## Installation & Claude Code Bindings

1. **Plugin & Skills (Any Repo or Direct Clone — 3-OS Windows-Safe)**:
   - Install anywhere in 10 seconds: `/plugin marketplace add HridayKapoor/syntopik` → `/plugin install syntopik` (exposes all 17 skills, `syntopik-studio` v3.0 MCP, and plugin-cache-safe hooks across any project).
   - Direct clones expose all 17 skills via `.claude/skills/` (`syntopik init` or `python3 -B scripts/setup_harness.py auto --json` repairs Windows symlinks/copies and initializes gitignored ledgers safely on macOS, Linux, and Windows).
2. **Graded Quizzes, Practice, Brainstem & Ambient MCP (`syntopik-studio` v3.0)**:
   - **SEP-1865 UI Hosts (Claude Desktop / VS Code)**: use `synt_quiz_card`, `synt_practice_card` (`mode: "standard" | "parsons" | "faded" | "blackout"`), and `synt_lesson_studio` (`mlh_*` aliases valid).
   - **Ambient Headless Tools (Any Chat)**: call `synt_next_move` (Pedagogical Brainstem), `synt_whiteboard_step` (Live AI Chalkboard + learner `pendingPins`), `synt_capture_concept`, `synt_get_frontier` (incl. `learnerProfile`, `teachingPolicy`, `dueForks`, `coldStartDays`), and `synt_log_review` (`hintsUsed`, `durationMs`).
   - **Claude Code Terminal CLI (`claude` TUI)**: prefer **`AskUserQuestion`** (`2–4` bare claims, `description: ""` on every option, never `(Recommended)`, include `{ label: "I don't know", description: "" }`); if `> 4` options, use one calm Markdown card (`A..D` + `E) I don't know`). `synt_practice_card` prints clean stems, shuffled `A..F` Parsons blocks, or `[HIDDEN]` blackout steps while keeping solutions sealed server-side.
3. **Progress, Visuals, CLI Verbs & Subagents**:
   - Track `4–8` DAG landmarks via **`TaskCreate`** / **`TaskUpdate`**.
   - Run v3.0 verbs (`syntopik next|profile|drop|demo|debrief|mode|sunset|pace|board|bench|triage|serve`) or verifiers via `Bash` (`syntopik run <engine>`), inspect PNGs via `Read` before presenting, save offline labs to `demos/<slug>.html`, and launch the live 2-way Studio + Mastery Gym via `syntopik serve --open`.
   - Offload `>15`p PDFs, bounded research, or multi-step verification to **`Agent`** (`subagent_type: "Explore"` or background worker).
4. **Hooks (`.claude/settings.json` / `.claude-plugin/hooks.json`)**:
   - `SessionStart` injects live KG, `Active TeachingPolicy`, traps, whiteboard pins, and Autopilot state; `PostToolUse` runs `sync-graph.mjs --quiet` on ledger updates (safe inside read-only plugin caches and on Windows without POSIX-only subshells).
