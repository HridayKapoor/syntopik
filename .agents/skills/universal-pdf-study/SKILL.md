---
name: universal-pdf-study
description: >-
  Inspect, OCR, render, crop, white-halo annotate, generate SVG image-occlusion
  masks, and co-pilot live interactive study from any research paper, textbook,
  slide deck, or scanned PDF.
---

# Universal PDF Studio — Live Co-Piloted Reading, OCR, Callouts & SVG Occlusion

Standard text extractors silently drop scanned/handwritten pages and mangle equations, circuits, plots, and architecture diagrams — while dumping a 150-page PDF into chat blows up context and focus. **Never dump a whole raw PDF into chat.**

Use `syntopik run pdf_studio ...` (or `.venv/bin/python -B scripts/pdf_studio.py ...` in direct clones) across every harness.

---

## Dual Entry Modes

### Mode A — Live Co-Piloted Study (DEFAULT)
Use Mode A **by default** whenever the learner brings a paper, slide deck, textbook chapter, handwritten note PDF, or problem sheet to understand concepts or solve exercises interactively. **Do NOT stall to write a giant static `chapters/<slug>.md` summary first.**

1. **Triage First (`inspect` — `< 2s`, NEVER read/render before this)**:
   ```bash
   syntopik run pdf_studio inspect "sources/<file.pdf>"
   ```
   Reports `size_class` (`short <= 10p`, `medium 11..40p`, `long > 40p`), `recommended_strategy`, `visionPages`, and per-page `kind`, `images`, `math_density` (`0.0..1.0`), and `needs_vision` (`kind == "image-only"` OR `images > 0` OR `math_density >= 0.03`).
   - **Teacher-First Law**: if instructor lecture/whiteboard notes are paired with a textbook or exercise bank, follow the instructor's contiguous page order (`p.1 → p.N`) as the `#1` spine.
2. **Size-Tiered Reading Doctrine (Zero Context Bloat)**:
   - **Short (`<= 10` pages)**: `extract` all pages; visually inspect every `needs_vision` page via `render` + `read_file` / `Read`.
   - **Medium (`11–40` pages)**: check `tree-index`, extract only target ranges, and visually inspect flagged `needs_vision` pages (`<= 6` rendered pages per pass). For `> 15`-page spans, delegate to a background subagent (`invoke_subagent` / `Agent` / `spawn_agent`) returning a **2–6 sentence `(p. N)`-cited brief + `Pages consulted:` list** (never summarize unread pages!).
   - **Long (`> 40` pages)**: **never read front-to-back**. Run `tree-index` + `search` first, then extract and visually read ONLY hit pages.
3. **Crop, White-Halo Annotate & 1-Click SVG Image Occlusion (`annotate [--occlude]`) + Vision Gate**:
   Put cropped figures directly into the lesson with crisp white-halo (`DejaVuSans-Bold`) callouts — and pass **`--occlude`** whenever a labeled diagram, circuit, pathway, or architecture figure should double as an interactive spatial recall flashcard:
   ```bash
   syntopik run pdf_studio annotate \
     --pdf "sources/<file.pdf>" --page 7 --crop 0.08,0.12,0.92,0.68 --dpi 260 \
     --marks '[{"type":"arrow","from":[0.20,0.28],"to":[0.46,0.52],"label":"Key invariant","color":"#e11d48"},{"type":"box","rect":[0.55,0.40,0.82,0.72],"label":"Bottleneck stage","color":"#2563eb","propId":"systems/bottleneck"}]' \
     --occlude \
     --out "sources/_extracted/<slug>/fig-p7-ann.svg" \
     --out-png "sources/_extracted/<slug>/fig-p7-ann.png" \
     --out-html "sources/_extracted/<slug>/fig-p7-ann.html" \
     --title "Page 7 — Annotated Figure" --json
   ```
   - Marks (`0..1` normalized default, or `"coords": "px"`): `arrow`, `box`, `circle`, `label`. Also accepts `--image "<photo.jpg>"`.
   - **`--occlude` Output**: emits declarative SVG occlusion JSON (`masks: [{id, bbox, label, propId}]`) alongside the white-halo PNG/SVG, ready to attach directly to `proposition.media` / `srs_engine.py add-cue` for clickable reveal masks in **Syntopik Studio**, `dashboard.html` (`syntopik serve`), and Anki Image Occlusion export!
   - **Mandatory Unlooked-Finish Gate**: ALWAYS inspect `--out-png` via `read_file` / `Read` BEFORE showing the learner; adjust coordinates if any label or mask clips or misses its target.
   - **Multi-Harness Inline Display**:
     - *Antigravity 2.0*: write `--out-html` to `<artifactDir>/<slug>-fig.html` via `write_file` (`ArtifactMetadata: {UserFacing: true, RequestFeedback: false}`) and embed `<agent-embed src="file:///<artifactDir>/<slug>-fig.html"></agent-embed>` directly above your prompt.
     - *Claude Code / Codex / Other Agents*: display/link the verified PNG/SVG (`sources/_extracted/<slug>/fig-p7-ann.png`) or HTML artifact, referencing callout labels by name in `<= 160` words of prose!
4. **Immediate Live Transition**: step straight into `teach-chapter` or `practice-drill` citing `(p. N)`.

---

### Mode B — Full Chapter / Paper Map Ingestion
Use ONLY on explicit request to map/outline a full chapter, paper, or problem pack into `chapters/<slug>.md` — follow `.agents/skills/ingest-chapter/SKILL.md` (Mode B).

---

## Core `pdf_studio.py` Reference

```bash
syntopik run pdf_studio inspect "sources/<file.pdf>"
syntopik run pdf_studio tree-index "sources/<file.pdf>"
syntopik run pdf_studio search "sources/<file.pdf>" "raft leader election"
syntopik run pdf_studio extract "sources/<file.pdf>" --pages 1-15 --dpi 180
syntopik run pdf_studio tables "sources/<file.pdf>" --pages 1-5
syntopik run pdf_studio render "sources/<file.pdf>" --pages 7 --dpi 260 --crop 0.0,0.45,1.0,0.95
```
`extract` outputs clean Markdown (`pymupdf4llm` with C-stdout chatter silenced), runs offline **RapidOCR** (Tesseract fallback) on `image-only` pages, renders `needs_vision` PNGs, and extracts embedded figures.
