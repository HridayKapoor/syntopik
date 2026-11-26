---
name: knowledge-graph
description: >-
  Maintain the learner's persistent local Knowledge Hypergraph in knowledge-graph/
  (propositions.jsonl, chains.jsonl, mistakes.jsonl, review-queue.md) across any
  subject and AI harness using srs_engine.py (find-similar dedup, FSRS-6 + BKT
  multi-cue scheduling, Visual Anki media), graph_intel.py, daily_plan.py, and
  audio_brief.py.
---

# Persistent Knowledge Hypergraph, FSRS-6 & Cross-Chapter Intelligence

Every topic you study in **Syntopik** compounds into a human-readable, privacy-safe (gitignored on disk, backed up via `scripts/backup_harness.py`) local hypergraph in `knowledge-graph/` shared seamlessly across **Antigravity 2.0**, **Claude Code**, **OpenAI Codex / T3 Code**, and any agent (`SYNT_KG_DIR` / `MLH_KG_DIR`).

---

## File Schemas

### 1. `knowledge-graph/propositions.jsonl` (One JSON object per line)
Tracks atomic propositions across any domain — CS invariants, ML theorems, math/physics derivations, chemical principles, or course concepts (update-in-place by `id`, never duplicate):
```json
{"id":"systems/raft-leader-completeness","subject":"Computer Science","chapter":"Raft Consensus","title":"Any committed log entry is present in the log of every leader for all higher terms","aliases":["Raft Leader Completeness Property"],"kind":"unconditional_truth","status":"solid","confidence":0.94,"lastTested":"2026-11-26","nextReview":"2026-12-03","media":["demos/raft-quorum.png"],"cues":[{"id":"c1","q":"Why cannot a candidate missing a committed entry win a majority vote in Raft?","a":"Majority election quorum and majority replication quorum must intersect in at least one voter whose log is at least as up-to-date.","lastRating":"good","reviews":1}]}
```
- `kind`: `"unconditional_truth"` | `"axiom"` | `"definition"` | `"derivation"` | `"heuristic"`
- `status`: `"solid"` (verified unaided) | `"shaky"` (needed hints/partial) | `"misconception"` (active wrong model) | `"planned"`
- `media` *(optional)*: flat list `["demos/fig.png"]` or `{"front":[...],"back":[...]}` (or per-cue `cues[].media`) embedded into Visual Anki cards!
- `cues` *(optional)*: distinct retrieval cues sharing **one** proposition-level FSRS-6 + BKT schedule.

### 2. `knowledge-graph/chains.jsonl` (Jointly-Entailing Hyperedges + Binary Edges)
- **Jointly-Entailing Hyperedge (preferred whenever `>= 2` premises are jointly required)**:
  ```json
  {"premises":["systems/majority-quorum-intersection","systems/up-to-date-vote-restriction"],"conclusion":"systems/raft-leader-completeness","relation":"derives","note":"Intersecting majorities + up-to-date voting jointly guarantee every new leader holds prior committed entries","recordedAt":"2026-11-26"}
  ```
- **Single-Premise Binary Edge (100% backwards-compatible)**:
  ```json
  {"from":"physics/coulomb-inverse-square","to":"physics/gauss-law-flux","relation":"derives","why":"1/r^2 field falloff cancels r^2 sphere area growth","recordedAt":"2026-11-26"}
  ```
- **Joint-Entailment Law**: Never collapse $A \wedge B \rightarrow C$ into separate binary edges (which falsely claims $A$ alone entails $C$).
- `relation`: `"requires"` | `"derives"` | `"generalizes"` | `"contrasts_with"`.

---

## Core CLI Workflows

1. **Check Lexical Identity BEFORE Creating Any Node (`find-similar`)**:
   ```bash
   .venv/bin/python -B scripts/srs_engine.py find-similar "Committed entry present in all future leaders" --top 5 --threshold 0.25 --json
   ```
   Reuse matching `id`s and append new phrasings to `aliases[]` rather than duplicating nodes.
2. **Multi-Cue FSRS-6 + BKT Scheduling (`add-cue`, `next-cue`, `review`)**:
   ```bash
   .venv/bin/python -B scripts/srs_engine.py add-cue systems/raft-leader-completeness --q "..." --a "..."
   .venv/bin/python -B scripts/srs_engine.py next-cue systems/raft-leader-completeness --json
   .venv/bin/python -B scripts/srs_engine.py review systems/raft-leader-completeness --rating good --cue-id c1
   .venv/bin/python -B scripts/srs_engine.py due
   ```
   *(Weighted cue selection: `again: 4, hard: 3, untried/null: 3, good: 2, easy: 1`. Only rate `good`/`easy` when demonstrated **unaided**!)*
3. **Visual Anki Export & Live Sync (`export-anki`, `push-anki`)**:
   ```bash
   .venv/bin/python -B scripts/srs_engine.py export-anki --out knowledge-graph/syntopik-deck.apkg
   .venv/bin/python -B scripts/srs_engine.py push-anki
   ```
4. **Cross-Topic Bottlenecks, Memorized Islands, Paths & Synthesis Combos (`scripts/graph_intel.py`)**:
   ```bash
   .venv/bin/python -B scripts/graph_intel.py --bottlenecks --islands --combos --json
   .venv/bin/python -B scripts/graph_intel.py --path-to systems/raft-leader-completeness --json
   ```
5. **Time-Boxed Daily Study Plan & Spoken Audio Commute Briefs**:
   ```bash
   .venv/bin/python -B scripts/daily_plan.py --minutes 60 --json
   .venv/bin/python -B scripts/audio_brief.py --chapter chapters/<slug>.md --pause-sec 2.5 --out chapters/<slug>-audio.mp3
   ```
6. **Mistake Taxonomy (`scripts/mistake_analytics.py`)**: log root errors in `knowledge-graph/mistakes.jsonl` (`log`, `summary`, `export-traps`) — keep errors separate from clean proposition statements.
