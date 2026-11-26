---
name: visual-and-demo
description: >-
  Render verified inline HTML labs, declarative explore Tangle and Limiting-Case
  labs, live Whiteboard frames, SVGs, SymPy 2D/3D plots, and annotated PDF
  figures across all harnesses and offline archives.
---

# Visual & Interactive Demo Builder — Anti-Split-Attention, Verified Every Time

A visual earns its place **only** when geometry, state transitions, data-structure mutation, or dynamic parameter sweeps make an invariant obvious in 3 seconds better than prose. **Correctness is non-negotiable**: one flipped vector, wrong pointer edge, overlapping label, or broken slider poisons intuition.

*(Portable CLI: prefer `syntopik run <engine> ...` in attached repos, or `.venv/bin/python -B scripts/<engine>.py ...` in direct clones.)*

---

## Multi-Harness Rendering Tiers (Inline-First, Offline-Safe Forever)

Pick the highest visual tier supported by your active harness (`BOOTSTRAP.md`), and **always anchor the visual directly above the prompt that references its controls/labels by name**:

1. **Surface A — Google Antigravity 2.0 (`generative_ui` Inline Embed, Default `<= 450px`)**:
   - Write a self-contained HTML widget to `<artifactDir>/<slug>.html` using native **`write_file`** WITH `ArtifactMetadata: {UserFacing: true, RequestFeedback: false}` (never write the artifact file via shell/Python alone, which skips `ArtifactMetadata`).
   - Embed directly inside your reply (do **NOT** set a `height` attribute):
     ```html
     <agent-embed src="file:///<artifactDir>/<slug>.html"></agent-embed>
     ```
   - **Exact Flush Theme-Sync Contract**:
     - Use pure inline CSS (zero external requests) or Antigravity's one CSP-allowlisted Tailwind script (`<script src="https://www.gstatic.com/antigravity/web/dev/tailwindcss.min.js"></script>`).
     - `<body class="bg-transparent text-[var(--foreground)] antialiased p-0 m-0">` (no outer card wrapper; divide with `border-[var(--card-border)]`).
     - Use semantic tokens (`--foreground`, `--muted-foreground`, `--card`, `--card-border`, `--border`, `--primary`, `--chart-1..6`); never hardcode dark/light utility classes, never override `:root`, never use `h-screen`/`100vh`/`height: 100%` (collapses auto-height!), and attach `window.addEventListener('themechange', draw)` on `<canvas>`.
   - For wide/tall explorers (`> 450px`), omit `<agent-embed>`, use `bg-[var(--background)]`, and link the standalone side-pane artifact.
2. **Surface B — T3 Code (`html_preview` → `html_render`) & Claude Desktop Artifacts**:
   - Preview via `html_preview` (verifying PNG + zero console errors) and publish via `html_render` with the exact `contentHeight` (or emit a self-contained zero-CDN HTML Artifact in Claude Desktop).
3. **Surface C — Claude Code CLI, OpenAI Codex CLI & Any Terminal/IDE Harness**:
   - Save a **100% offline zero-CDN** interactive HTML lab to `demos/<slug>.html` (using Syntopik's calm *Warm Obsidian* `#111315` / *Archival Vellum* `#F7F5F0` Dark/Light toggle) + render crisp static `.png`/`.svg` figures (`math_plot`, `physics_draw`, `circuit_draw`, `chem_3d`, `pdf_studio annotate [--occlude]`) + inline Mermaid (`graph TD`, `sequenceDiagram`, `stateDiagram-v2`), providing a clickable `file://` link to `demos/<slug>.html`.
4. **Permanent Offline Hub Archive (`demos/<slug>.html`)**:
   - Regardless of harness, save reusable interactive labs (zero external CDN tags!) to `demos/<slug>.html` so `dashboard.html` and `syntopik serve` (**Interactive Labs**) preserve them 100% offline.

---

## Deterministic Plot & Diagram Generators (Prefer Over Hand-Coded Coordinates!)

- **Coordinate Geometry, Argand, Integrals, 3D Skew Lines & Functions (`scripts/math_plot.py`)**:
  ```bash
  syntopik run math_plot conic --type ellipse --a 4 --b 2.5 --tangent-t 45 --show-director --show-normal --out demos/ellipse.html --json
  syntopik run math_plot argand --loci "circle:1+2j,3;perp-bisector:0,4" --points "0;1+2j;4" --out demos/argand.png --json
  syntopik run math_plot integral --expr "sin(x)" --a 0 --b "2*pi" --out demos/integral.html --json
  syntopik run math_plot vector3d --lines "0,0,0|1,1,0;0,0,2|1,-1,0" --out demos/skew-3d.html --json
  syntopik run math_plot func --exprs "x**3 - 3*x" --xmin -3 --xmax 3 --mark-roots --mark-extrema --mark-inflection --out demos/cubic.png --json
  ```
- **CS / Algorithms / ML Visualizers**: step-by-step interactive Canvas/SVG visualizers (Raft log replication, B-tree splits, DP table fill, attention weights, gradient descent trajectories) — pre-verify reference traces via `code_verify.py`!
- **Physics, Circuits, Chemistry & PDF Crops**: `physics_draw.py`, `circuit_draw.py`, `chem_3d.py`, `organic_reagents.py`, `pdf_studio.py annotate [--occlude]`.

---

## Declarative `:::explore` Tangle + Limiting-Case Labs & Live Whiteboard Frames (v3.0)

1. **Zero-`eval` Declarative `:::explore` Labs (`syntopik/explorable.py`)**:
   - Embed Bret Victor Tangle + Mahajan Limiting-Case interactive widgets directly inside `chapters/<slug>.md` using `:::explore ... :::` fenced blocks (JSON spec with parameter `sliders`, `outputs`, exact vs approximation `curve` expressions, and optional pre-scrub `limitCheck` prediction gate).
   - Every expression compiles strictly via Python's AST whitelist into RPN opcodes (`push_num`, `push_var`, `add`, `sub`, `mul`, `div`, `pow`, `neg`, `sin`, `cos`, `tan`, `sqrt`, `abs`, `exp`, `log`) evaluated in the browser Chapter Reader & Live Whiteboard with **ZERO `eval()` or `new Function()`**.
2. **Live AI-to-Whiteboard SSE Frames (`synt_whiteboard_step` / `syntopik board push`)**:
   - Stream progressive teaching frames (`derivation_step`, locked `prediction_gate` blurred until learner submits prediction + confidence, `explore_widget`, `diagram_callout`, `summary`) to `knowledge-graph/whiteboard-session.json` for live rendering in `syntopik serve` (`Mastery Gym → Live AI Chalkboard`).
   - Always inspect returned **`pendingPins`** and answer learner-pinned step questions before pushing new derivation leaps!

---

## Craft & Mandatory Verification Gate

1. **Exact Numerics**: use **RK4** or **Velocity-Verlet** (`dt <= 0.02`) with live invariant readouts ($K, U, E$) for physical dynamics — never naive Euler; scale `<canvas>` by `devicePixelRatio`.
2. **Calm Restraint (`1` Widget / Turn)**: 3–7 visual elements max, **Reset + Limiting-Case Presets** (e.g., *Underdamped / Critical / Overdamped*, *Split Brain / Quorum*), `150ms` subtle motion (`prefers-reduced-motion` respected), and zero duplicated chat prose inside the widget.
3. **Mandatory Visual Audit**: verify JS syntax (`node --check` on extracted script), confirm `0` console/network errors, and visually inspect a rendered PNG via `read_file` / `Read` BEFORE presenting.
4. **Teach WITH Named Controls**: *"An artifact you never point at is decoration."* Explicitly direct the learner to named sliders/buttons (*"Step `electionTimeout` to `equal` and watch split votes repeat"*).
