---
name: ingest-chapter
description: >-
  Co-pilot live interactive PDF/paper/slide study (Mode A, default) or map a
  full chapter, research paper, lecture slide deck, textbook section, codebase,
  or problem sheet from sources/ using parallel subagents into a clean
  dependency DAG, unconditional foundations, pitfall sheet, and study launchpad (Mode B).
---

# Ingest Chapter — Live Co-Piloted Study & Parallel Chapter/Paper Mapper

Support both **instant live co-piloted learning straight from any document** and **structured chapter/paper mapping** across CS, ML, math, science, humanities, and competitive exams — without bloating your main conversation context.

---

## Choose the Right Entry Mode First

### Mode A — Live Co-Piloted Study (DEFAULT)
**Activate automatically** whenever the learner drops or references a PDF, paper, slide deck, or notes file to learn a concept, dissect a proof/algorithm, inspect a figure, or practice problems right now. **Do NOT stall to write `chapters/<slug>.md` first!**

1. **Fast Triage (`pdf_studio.py inspect`)**:
   ```bash
   .venv/bin/python -B scripts/pdf_studio.py inspect "<file.pdf>"
   ```
   Read `size_class` (`short <= 10p`, `medium 11..40p`, `long > 40p`), `recommended_strategy`, and per-page `math_density` + `needs_vision`. Honor the **Teacher-First Law** (`p.1 → p.N` on lecture/whiteboard notes).
2. **Size-Tiered Reading (Zero Context Bloat)**:
   - `short (<=10p)`: `extract` all + visually inspect `needs_vision` PNGs.
   - `medium (11..40p)`: `tree-index` + targeted `extract`; delegate `>15p` spans to a background subagent (`invoke_subagent` / `Agent` / `spawn_agent`) returning a **2–6 sentence `(p. N)`-cited brief + `Pages consulted:`**.
   - `long (>40p)`: `tree-index` + `search` first; read ONLY hit pages.
3. **Inline High-Contrast Figure Callouts (`pdf_studio.py annotate`)**:
   - Crop key figures (`--dpi 260`), overlay white-halo callouts, **inspect `--out-png` via `read_file`/`Read`**, and render inline (`<agent-embed>` in Antigravity or Artifact/image link in Claude Code/Codex) directly above your prompt.
4. **Immediate Live Handoff**: check `knowledge-graph/propositions.jsonl` and start `teach-chapter` or `practice-drill` (`<= 160` words prose/turn, `1` active demand/turn).

---

### Mode B — Full Chapter / Paper / Module Map (On Explicit Request)
**Activate when the learner explicitly asks** to ingest, outline, or map a whole chapter, paper, lecture pack, or problem bank into `chapters/<slug>.md`.

1. **Extract & Fan Out Subagents**:
   - Run `pdf_studio.py inspect` (and `tree-index` on `>40p` docs), `extract` target ranges, and visually inspect `needs_vision` PNGs.
   - For multi-file or `>15p` packs, fan out parallel subagents (`subagent-orchestrator` — or sequential `<= 15p` chunks in single-threaded harnesses):
     - **Subagent A (Foundations & Derivations)**: extracts unconditional truths/definitions, theorems/algorithms + validity preconditions, and exact `(p. N)` citations.
     - **Subagent B (Pitfalls, Edge Cases & Exercise Map)**: extracts near-twin discrimination pairs, boundary traps, and canonical exercise IDs.
2. **Deterministic Verification & KG Cross-Check**:
   - Cross-check `knowledge-graph/propositions.jsonl` (`solid` vs `shaky` prerequisites) and verify tricky equations/code/chemistry with `math_verify.py`, `code_verify.py`, or `chem_physical.py`.
3. **Write `chapters/<slug>.md` & Sync Tracker**:
   - Include: (1) Scope & Sources, (2) Basics-at-Top Mermaid `graph TD` (`4–8` coarse landmarks), (3) Unconditional Foundations, (4) Study Clusters with `(p. N)` citations, (5) Discrimination Traps, and (6) Mastery Checklist (optionally seeding `chapters/<slug>-tracker.json` via `chapter_tracker.py`).
4. **Calm Launchpad (`<= 120` words)**:
   - Present the compact Mermaid DAG and ask **1** launch question (`ask_question` / `AskUserQuestion` / `request_user_input` or calm Markdown card): *Start Hands-Free Autopilot (`chapter-autopilot`)* · *Diagnostic Fast-Track Probe* · *Jump to Practice Drills* · *Build an Interactive Lab*.
