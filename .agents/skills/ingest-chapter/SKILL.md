---
name: ingest-chapter
description: >-
  Co-pilot live study or import chapters, Anki decks, Jupyter notebooks,
  CSV/TSV cards, Notion/Obsidian folders, arXiv papers, YouTube, repos, and web
  docs into DAGs and reading slices.
---

# Ingest Chapter — Live Co-Piloted Study & Universal Source Mapper

Support both **instant live co-piloted learning straight from any document, paper, video, repo, or URL** and **structured full-module mapping** across CS, ML, math, science, humanities, and competitive exams — without bloating your main conversation context.

*(Portable CLI: use `syntopik import` / `syntopik run <engine>` in attached repos, or `.venv/bin/python -B scripts/<engine>.py` / `python3 scripts/<engine>.py` in direct clones.)*

---

## Universal 1-Command Pre-Ingestion & Ecosystem Importers (`syntopik import` / `syntopik/importers.py`)

When the source is an **Anki `.apkg`**, **Quizlet/Anki CSV or TSV**, **Jupyter `.ipynb` notebook**, **Notion `.zip` / Obsidian Markdown folder**, YouTube URL, arXiv paper, GitHub/local codebase, or web article, normalize it first into `sources/_ingested/<slug>/` (every imported concept starts `status: "planned"` — **zero fake mastery**, existing `solid` mastery is never overwritten!):

```bash
syntopik import "<target>" --slug <slug> --register-tracker --json
# Ecosystem importers (auto-detected by extension or explicit --kind):
syntopik import "<deck.apkg>" --kind anki --subject "<S>" --json           # Read-only SQLite, Basic+Cloze, HTML/MathJax clean, zero fake mastery
syntopik import "<cards.csv|tsv>" --kind cards --subject "<S>" --json      # UTF-8 BOM-safe, Anki #separator:/#deck: + Quizlet headerless TSV/CSV
syntopik import "<lab.ipynb>" --kind notebook --slug <slug> --json         # ZERO execution: AST literal_eval -> notebook.md + code-tests.json
syntopik import "<notion.zip|folder>" --kind notes_batch --slug <slug> --json # UUID stripping + link rewrite + frontmatter/:::cue/Cloze/Q::A::
# Web & media subcommands:
python3 -B scripts/ingest_source.py youtube "<url>" --slug <slug> --json       # caption-only 5-8 min chunks + [mm:ss] pause-points
python3 -B scripts/ingest_source.py arxiv "<id_or_url>" --download-pdf --json  # Atom metadata + Keshav 3-Pass guide + PDF
python3 -B scripts/ingest_source.py repo "<path_or_url>" --slug <slug> --json  # Golden-Path Architecture Manifest
python3 -B scripts/ingest_source.py url "<https_url>" --slug <slug> --json     # HTML->MD + Incremental Reading slices
python3 -B scripts/ingest_source.py queue due --json                           # SuperMemo-style priority * urgency queue
```
- **Anki `.apkg` & Quizlet CSV/TSV**: sanitizes HTML/scripts, preserves Cloze `{{cN::...}}` (`kind: "cloze"`), converts MathJax `\\(...\\)` → `$...$`, and rejects zip-slip/symlinks/zip-bombs. If an `.apkg` only has zstd `collection.anki21b` on Python `<3.14`, returns crisp guidance to re-export with *"Support older Anki versions"* checked.
- **Jupyter `.ipynb`**: pure `json.loads` + `ast.parse` + `ast.literal_eval` ONLY (**never executes notebook cells!**); exports `code-tests.json` directly runnable in `code_verify.py test-cases`.
- **Notion `.zip` / Obsidian MD Folders**: skips symlinks/hidden dirs, strips 32-hex & 36-char UUID suffixes from folders/files/URL-encoded Markdown links, and extracts `:::cue`, `{{cN::...}}`, and `Q:: / A::` cues into `reading-queue.jsonl`.
- **YouTube**: study strictly from timestamped captions (`lecture.md`), pausing every 6–10 min for an Active Pause-Point prediction/check so video study never turns passive.
- **arXiv**: walk **Keshav 3-Pass** (`Pass 1`: 5 Cs triage → `Pass 2`: key figures/equations via `pdf_studio.py annotate` → `Pass 3`: re-derive/stress-test core assumptions).
- **GitHub / Codebase**: trace **1 real request or forward pass** along the Golden-Path Manifest (`repo-map.md`) rather than reading files linearly.
- **Long Web Docs / Textbooks**: track incremental slices (`slices[]` in `chapter_tracker.py` + `knowledge-graph/reading-queue.jsonl`: `queued -> extracted -> clozed -> crystallized`, `1–2` slices/session).

---

## Choose the Right Entry Mode

### Mode A — Live Co-Piloted Study (DEFAULT)
**Activate automatically** whenever the learner drops or links a PDF, paper, slide deck, YouTube lecture, repo, or notes file to learn a concept, dissect a proof/algorithm, inspect a figure, or practice problems right now. **Do NOT stall to write `chapters/<slug>.md` first!**

1. **Fast Triage**:
   - For PDFs: run `syntopik run pdf_studio inspect "<file.pdf>"` (`size_class`: `short <= 10p`, `medium 11..40p`, `long > 40p`, `math_density`, `needs_vision`). Honor the **Teacher-First Law** (`p.1 → p.N` on lecture/whiteboard notes).
   - For YouTube/arXiv/repo/URL: run `syntopik import "<target>" --json` and open only the active segment, Pass, or Golden-Path file.
2. **Size-Tiered Reading (Zero Context Bloat)**:
   - `short (<=10p)`: `extract` all + visually inspect `needs_vision` PNGs.
   - `medium (11..40p)`: `tree-index` + targeted `extract`; delegate `>15p` spans to a background subagent (`invoke_subagent` / `Agent` / `spawn_agent`) returning a **2–6 sentence `(p. N)`-cited brief + `Pages consulted:`**.
   - `long (>40p)`: `tree-index` + `search` first; read ONLY hit pages.
3. **Inline High-Contrast Figure Callouts & SVG Occlusion (`pdf_studio.py annotate [--occlude]`)**:
   - Crop key figures (`--dpi 260`), overlay white-halo callouts (add `--occlude` to emit SVG occlusion masks for spatial flashcards), **inspect `--out-png` via `read_file`/`Read`**, and render inline directly above your prompt.
4. **Immediate Live Handoff**: check `knowledge-graph/propositions.jsonl` and start `teach-chapter` or `practice-drill` (`<= 160` words prose/turn, `1` active demand/turn).

---

### Mode B — Full Chapter / Paper / Module Map (On Explicit Request)
**Activate when the learner explicitly asks** to ingest, outline, or map a whole chapter, paper, lecture pack, repo, or problem bank into `chapters/<slug>.md`.

1. **Extract & Fan Out Subagents**:
   - Run `pdf_studio.py inspect` / `tree-index` (or `ingest_source.py`), extract target ranges, and inspect `needs_vision` PNGs.
   - For multi-file or `>15p` packs, fan out parallel subagents (`subagent-orchestrator` — or sequential `<= 15p` chunks in single-threaded harnesses):
     - **Subagent A (Foundations & Derivations)**: extracts unconditional truths/definitions, theorems/algorithms + validity preconditions, and exact `(p. N)` / `[mm:ss]` / file-line citations.
     - **Subagent B (Pitfalls, Edge Cases & Exercise Map)**: extracts near-twin discrimination pairs, boundary traps, and canonical exercise IDs.
2. **Deterministic Verification & KG Cross-Check**:
   - Cross-check `knowledge-graph/propositions.jsonl` (`solid` vs `shaky` prerequisites) and verify tricky equations/code/chemistry with `math_verify.py`, `code_verify.py`, or `chem_physical.py`.
3. **Write `chapters/<slug>.md` (with Optional Inline `:::cue` Blocks) & Sync Tracker**:
   - Include: (1) Scope & Sources, (2) Basics-at-Top Mermaid `graph TD` (`4–8` coarse landmarks), (3) Unconditional Foundations, (4) Study Clusters with citations, (5) Discrimination Traps, and (6) Mastery Checklist (optionally seeding `chapters/<slug>-tracker.json` via `chapter_tracker.py` and syncing inline `:::cue` blocks via `vault_sync.py sync-notes`).
4. **Calm Launchpad (`<= 120` words)**:
   - Present the compact Mermaid DAG and ask **1** launch question: *Start Hands-Free Autopilot (`chapter-autopilot`)* · *Diagnostic Fast-Track Probe* · *Jump to Practice Drills* · *Build an Interactive Lab*.
