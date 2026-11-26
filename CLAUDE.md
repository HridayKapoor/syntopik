@AGENTS.md

# Syntopik — Anthropic Claude Code Bindings (`CLAUDE.md`)

`AGENTS.md` (imported above) defines **Syntopik**'s canonical pedagogy, calm cognitive-load ceilings (`<= 160` words prose/turn, `1` active demand/turn), universal onboarding, 17 skills, and 26 deterministic verification scripts. In **Claude Code**, map each learning primitive as follows:

## Claude Code Tool & Workflow Mapping

1. **Skills (`.claude/skills/<name>/SKILL.md` → `../.agents/skills/`)**:
   - All 17 project skills are available natively via `.claude/skills/` (auto-invoked by description or explicitly via `/topic-onboarding`, `/chapter-autopilot`, `/teach-chapter`, `/practice-drill`, etc.). Read a skill's `SKILL.md` when its workflow activates.
   - On Windows clones without Git symlinks, run `python3 scripts/setup_harness.py auto --json` once to repair `.claude/skills` and initialize gitignored learner ledgers.
2. **Graded Probes, Predictions, Checks & Practice**:
   - **Claude Desktop / SEP-1865 UI Hosts** (with `.mcp.json` `syntopik-studio` active): use `synt_quiz_card` / `synt_practice_card` / `synt_lesson_studio` (or `mlh_*` aliases) for interactive inline cards and side-pane Study Studio.
   - **Claude Code Terminal CLI (`claude` TUI)**:
     - Prefer native **`AskUserQuestion`** for graded multiple-choice probes, predictions, and checks (`2–4` bare-claim options with `description: ""` on every option so zero hints leak, **never** `(Recommended)`, and include `{ label: "I don't know", description: "" }`). If `> 4` options are needed, render one clean bordered Markdown quiz card (`A..D` + `E) I don't know`) inviting choice + `0–100%` confidence + 1-line reasoning.
     - For `practice-drill`, you may call `synt_practice_card` (`pose` / `conclude`) — in terminal mode the server prints the problem stem cleanly while keeping `modelSolution` sealed server-side until `conclude` — or commit the verified solution in scratch and enforce the Post-Conclusion Pause Law in chat.
3. **Live Lesson Plan Progress**:
   - Track the `4–8` approved DAG landmarks using Claude Code's native **`TaskCreate`** and **`TaskUpdate`** tools (updating `in_progress` / `completed` as each node clicks).
4. **Visuals, Diagrams & Interactive Labs**:
   - Run deterministic generators (`scripts/math_plot.py`, `physics_draw.py`, `circuit_draw.py`, `chem_3d.py`, `organic_reagents.py`, `pdf_studio.py annotate`) via `Bash` (`.venv/bin/python -B ...`), inspect rendered PNGs via `Read` before presenting, save offline interactive HTML labs to `demos/<slug>.html`, and use inline Mermaid / KaTeX where helpful.
5. **Background Subagents (`Agent` tool)**:
   - Use `Agent` (`subagent_type: "Explore"` or general background agent) to read `>15`-page PDF ranges (`pdf_studio.py`), run bounded syllabus research (`research_scout.py` / `WebSearch` / `WebFetch`), or pre-verify multi-step CAS/coding problems (`math_verify.py`, `code_verify.py`) so the main teaching conversation stays calm and uncluttered.
6. **Hooks (`.claude/settings.json`)**:
   - `SessionStart` automatically injects your live Knowledge Graph, top mistake traps, and active Chapter Autopilot state via `.agents/hooks/session-start.mjs`.
   - `PostToolUse` automatically runs `scripts/sync-graph.mjs --quiet` whenever learner ledgers or chapters change.
