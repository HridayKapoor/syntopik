# Syntopik — Google Antigravity 2.0 Companion Rules (`GEMINI.md`)

Read and follow `AGENTS.md` in this workspace root for **Syntopik**'s universal pedagogy, 17-skill routing, calm cognitive-load ceilings (`<= 160` words/turn, `1` active demand/turn), and deterministic verification rules. Apply these native **Google Antigravity 2.0** bindings:

## Native Antigravity 2.0 Tool & UI Bindings

1. **Two-Way Interactive Studio (`syntopik-studio` / `mlh-studio` SEP-1865 MCP App)**:
   - When connected, prefer **`synt_quiz_card`** (`mlh_quiz_card` alias) for open `probe`/`predict` and collapsed `check` cards (`Thinking while reading`, shuffled bare options, `"I don't know"`, `0–100%` confidence slider, sketch pad), **`synt_practice_card`** (`mlh_practice_card`) for server-sealed `modelSolution` drills + Pause Law, and **`synt_lesson_studio`** (`mlh_lesson_studio`) for the 5-tab Side-Pane **Study Studio** (`Autopilot`, `Plan` DAG, `Graph`, `Flashcards`, `Traps`).
   - If uninstalled (`.venv/bin/python scripts/install_mcp_app.py install`), use `ask_question` (never `(Recommended)` on graded items; include `"I don't know"`).
2. **Live Inline Widgets (`generative_ui` `<agent-embed>`) & Progress (`update_steps`)**:
   - Render compact (`<= 450px`, flush `bg-transparent`, `themechange`-synced) labs, exact plots, circuits, molecules, and white-halo PDF crops (`pdf_studio.py annotate`) **inline in chat** by writing `<artifactDir>/<slug>.html` via `write_file` (`ArtifactMetadata: {UserFacing: true, RequestFeedback: false}`) and embedding `<agent-embed src="file:///<artifactDir>/<slug>.html"></agent-embed>`. Teach *with* named controls; archive zero-CDN copies in `demos/<slug>.html`. Track lesson landmarks via `update_steps`.
3. **Parallel Subagents & Bounded Research**:
   - Delegate `>15`-page PDF chunks and verification scouting to `invoke_subagent(TypeName="research", ...)` and demo/Manim/KG scribing to `invoke_subagent(TypeName="self", ...)`. When calling `research` MCP tools directly, always pass integer `runtime_seconds` (`60`–`180`) and `result_detail: "compact"`.

## Workspace Layout

- `AGENTS.md` · `GEMINI.md` · `CLAUDE.md` · `BOOTSTRAP.md` — Multi-harness rules & universal bootstrap protocol.
- `scripts/` · `.agents/skills/` · `.agents/hooks.json` — 26 deterministic engines, 17 skills, and tri-protocol hooks.
- `mcp-app/mlh-studio/` — Pre-bundled zero-CDN SEP-1865 Studio MCP App (`Syntopik Studio`).
- `sources/` · `chapters/` · `demos/` · `exams/` · `animations/` · `knowledge-graph/` · `tests/` — Inputs/curated manifests, trackers/notes/Typst PDFs/audio, labs, timed exam HTMLs, Manim MP4s, git-clean FSRS/hypergraph ledgers, and `dashboard.html` / `viewer.html`.
