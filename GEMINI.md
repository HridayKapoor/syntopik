@AGENTS.md

# Syntopik v3.0 — Google Antigravity 2.0 & Gemini CLI Bindings (`GEMINI.md`)

`AGENTS.md` (above) defines Syntopik's pedagogy, `Active TeachingPolicy`, calm ceilings (`<= 160`w prose, `1` demand/turn), Mastery Gym routing, and verification. Apply these native **Antigravity 2.0 / Gemini CLI** bindings:

## Native Tools, Brainstem, Whiteboard & Studio UI

1. **Interactive Studio & Ambient MCP (`syntopik-studio` / `mlh-studio` v3.0)**:
   - **UI Cards**: prefer **`synt_quiz_card`** (open `probe`/`predict`, collapsed `check`, bare options, `"I don't know"`, `0–100%` confidence, sketch pad), **`synt_practice_card`** (server-sealed `modelSolution`/`canonicalOrder`/blackout answers, `mode: "standard"|"parsons"|"faded"|"blackout"`, Pause Law), and **`synt_lesson_studio`** (5-tab Side-Pane `Autopilot` w/ live TeachingPolicy & override strip, `Plan` DAG, `Graph`, `Flashcards` w/ SVG occlusion + FIRe toast, `Traps`). `mlh_*` aliases valid.
   - **Ambient Headless Tools** (any chat, zero UI): **`synt_next_move`** (`<= 180`-token Brainstem Action Envelope), **`synt_whiteboard_step`** (push `derivation_step|prediction_gate|explore_widget|diagram_callout|summary` & read learner `pendingPins`), **`synt_capture_concept`**, **`synt_get_frontier`** (FIRe due + `learnerProfile`/`teachingPolicy` + `dueForks`/`arenasCount`/`coldStartDays`/`pendingPins`), and **`synt_log_review`** (FIRe + `hintsUsed`/`durationMs`).
   - If MCP is absent (`syntopik connect antigravity`), use **`ask_question`** (never `(Recommended)` on graded items; include `"I don't know"`).
2. **Inline Widgets (`generative_ui` `<agent-embed>`) & Progress (`update_steps`)**:
   - Write compact (`<= 450px`, flush `bg-transparent`, `themechange`-synced) labs, plots, circuits, molecules, and PDF crops (`pdf_studio annotate`) to `<artifactDir>/<slug>.html` via `write_file` (`ArtifactMetadata: {UserFacing: true, RequestFeedback: false}`), embed `<agent-embed src="file:///<artifactDir>/<slug>.html"></agent-embed>`, teach *with* named controls, and archive zero-CDN copies in `demos/<slug>.html`. Track landmarks via **`update_steps`**.
3. **Subagents, Research & Workbench**:
   - Delegate `>15`p PDFs/scouting to `invoke_subagent(TypeName="research", ...)` and labs/Manim/KG scribing to `invoke_subagent(TypeName="self", ...)`. On `research` MCP tools, pass integer `runtime_seconds` (`60–180`) and `result_detail: "compact"`. Launch 2-way Studio + Mastery Gym (`127.0.0.1`, offline `file://` fallback) via `syntopik serve --open`.
