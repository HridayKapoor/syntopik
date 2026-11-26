# Syntopik — Google Antigravity 2.0 Bindings (`GEMINI.md`)

Follow `AGENTS.md` for Syntopik's pedagogy, calm ceilings (`<= 160`w prose, `1` demand/turn), plain-English routing, and verification. Apply these native **Antigravity 2.0** bindings:

## Native Antigravity 2.0 Tools & Studio UI

1. **Interactive Studio & Ambient MCP (`syntopik-studio` / `mlh-studio`)**:
   - **UI Cards**: prefer **`synt_quiz_card`** (open `probe`/`predict`, collapsed `check`, bare options, `"I don't know"`, `0–100%` confidence, sketch pad), **`synt_practice_card`** (sealed `modelSolution`, `mode: "standard" | "parsons" | "faded"`, Pause Law), and **`synt_lesson_studio`** (5-tab Side-Pane `Autopilot`, `Plan` DAG, `Graph`, `Flashcards` w/ SVG occlusion + FIRe toast, `Traps`). `mlh_*` aliases remain valid.
   - **Ambient Headless Tools** (any chat, zero UI): **`synt_capture_concept`** (auto-dedup + chain link), **`synt_get_frontier`** (FIRe due queue + bottlenecks + Brier calibration), **`synt_log_review`** (FIRe + `revlog.jsonl`).
   - If MCP is absent (`syntopik connect antigravity` or `scripts/install_mcp_app.py install`), use **`ask_question`** (never `(Recommended)` on graded items; include `"I don't know"`).
2. **Inline Widgets (`generative_ui` `<agent-embed>`) & Progress (`update_steps`)**:
   - Write compact (`<= 450px`, flush `bg-transparent`, `themechange`-synced) labs, plots, circuits, molecules, and white-halo/occlusion PDF crops (`pdf_studio.py annotate`) to `<artifactDir>/<slug>.html` via `write_file` (`ArtifactMetadata: {UserFacing: true, RequestFeedback: false}`), embed `<agent-embed src="file:///<artifactDir>/<slug>.html"></agent-embed>`, teach *with* named controls, and archive zero-CDN copies in `demos/<slug>.html`. Track landmarks via **`update_steps`**.
3. **Subagents, Research & Workbench**:
   - Delegate `>15`p PDFs/scouting to `invoke_subagent(TypeName="research", ...)` and labs/Manim/KG scribing to `invoke_subagent(TypeName="self", ...)`. On `research` MCP tools, pass integer `runtime_seconds` (`60–180`) and `result_detail: "compact"`. Launch live 2-way Studio (`127.0.0.1`, offline `file://` fallback) via `syntopik serve --open`.
