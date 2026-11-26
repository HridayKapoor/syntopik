@AGENTS.md

# Syntopik v3.2 — Google Antigravity 2.0 & Gemini CLI Bindings (`GEMINI.md`)

Applies `AGENTS.md` with native **Antigravity 2.0 / Gemini CLI** tools:

## Native Tools, Brainstem, Whiteboard & Studio UI

1. **Interactive Studio & Ambient MCP (`syntopik-studio` v3.2 + zero-Node loopback `POST /mcp`)**:
   - **UI Cards**: prefer **`synt_quiz_card`** (`probe`/`predict`/`check`, bare options, `"I don't know"`, `0–100%` confidence, sketch pad), **`synt_practice_card`** (server-sealed `standard|parsons|faded|blackout`, Pause Law), and **`synt_lesson_studio`** (5-tab Side-Pane: `Autopilot` w/ overrides, `Plan`, `Graph`, `Flashcards` w/ SVG occlusion + FIRe, `Traps`). `mlh_*` aliases valid.
   - **7 Headless Tools** (stdio OR loopback `POST /mcp`): **`synt_next_move`** (`<= 180`-token Brainstem + Rosetta + taper), **`synt_whiteboard_step`** (5 kinds & `pendingPins`), **`synt_capture_concept`**, **`synt_get_frontier`** (FIRe due + policy + `readinessSummary`/`taperActive`/`rosettaBridgesCount`/`syncDeviceId`), **`synt_log_review`** (`hintsUsed`/`durationMs`), **`synt_readiness`** (FSRS forecast, `<=72h` taper, Pre-Mortem Shield), and **`synt_interleave`** (zero-leak blind-chapter drills).
   - Without MCP (`syntopik connect antigravity|gemini`), use **`ask_question`** (never `(Recommended)` on graded items; include `"I don't know"`).
2. **Inline Widgets (`generative_ui` `<agent-embed>`) & Progress (`update_steps`)**:
   - Write compact (`<= 450px`, `bg-transparent`, `themechange`-synced) labs, plots, circuits, molecules, and PDF crops (`pdf_studio annotate`) to `<artifactDir>/<slug>.html` (`ArtifactMetadata: {UserFacing: true, RequestFeedback: false}`), embed `<agent-embed src="file:///<artifactDir>/<slug>.html"></agent-embed>`, teach *with* controls, and archive zero-CDN copies in `demos/<slug>.html`. Track via **`update_steps`**.
3. **Subagents, Research & Workbench**:
   - Delegate `>15`p PDFs/scouting to `invoke_subagent(TypeName="research", ...)` and labs/Manim/KG to `TypeName="self"`. On `research` MCP tools, pass integer `runtime_seconds` (`60–180`) and `result_detail: "compact"`. Launch Studio + Gym + `POST /mcp` (`127.0.0.1`, `file://` fallback) via `syntopik serve --open`.
