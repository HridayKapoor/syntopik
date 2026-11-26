---
name: subagent-orchestrator
description: >-
  Orchestrate parallel background subagents across Antigravity, Claude Code,
  Codex, or sequential bounded chunks for PDF/repo ingestion, syllabus scouting,
  verification, labs, and KG sync.
---

# Subagent Orchestrator — Calm Main Thread, Parallel Background Execution

Keep the learner's conversation calm, fast, and below `160` words per turn by offloading bulky reading, universal source ingestion, bounded research, multi-case verification, and simulation rendering to background workers.

*(Portable CLI: subagents invoke `syntopik <cmd>` / `syntopik run <engine>` in attached repos, or `.venv/bin/python -B scripts/<engine>.py` / bare `python3 scripts/<engine>.py` in direct clones.)*

---

## Multi-Harness Dispatch Mapping

| Harness | Background / Parallel Primitive | Notes |
| :--- | :--- | :--- |
| **Google Antigravity 2.0** | `invoke_subagent` (`research`, `self`, `image-generator`) · `run_workflow` | Native reactive wakeup; pass self-contained briefs |
| **Anthropic Claude Code** | `Agent` (`subagent_type: "Explore"` for read-only search/PDFs, or general background `run_in_background: true`) | `Explore` runs lean without full root context; pass exact paths & CLI commands |
| **OpenAI Codex / T3 Code** | `spawn_agent` / `/subagents` (`multi_agent = true` in `.codex/config.toml`) | Disjoint file ownership per worker |
| **Single-Threaded Harnesses** | Sequential bounded CLI chunks (`pdf_studio`, `ingest_source`, `research_scout`, `code_verify`, `math_verify`) | Cap to `<= 5` PDF pages or `<= 3` searches per turn |

---

## Five High-Leverage Subagent Roles

1. **Bounded PDF / Repo / Media Mapper** (`research` / `Explore`): runs `syntopik import` (`ingest_source.py`) or scans `15–20` page PDF windows with 1-page overlap (`pdf_studio inspect` + `tree-index` + Vision on `needs_vision` pages), returning a cited `(p. N)` / `[mm:ss]` brief + deduplicated topic/exercise JSON.
2. **Bounded Syllabus & Literature Scout** (`research` / `Explore`): executes the 4-tier `research_scout.py` cascade (`<= 8` primary sources, strict `inScope`/`outOfScope` fences) for `topic-onboarding`.
3. **Deterministic Problem & Code Verifier** (`self` / general agent): verifies `modelSolution`, Parsons canonical/distractor splits, edge cases, units, and empirical complexity via `math_verify`, `code_verify`, `chem_physical`, and `chem_3d` + hunts trivial shortcuts before posing drills.
4. **Interactive Lab & Manim Builder** (`self` / general agent): builds zero-CDN `demos/<slug>.html` labs or `animations/<slug>.mp4` scenes and visually audits PNG frames before delivery.
5. **Knowledge Graph, Vault & Revision Sheet Scribe** (`self` / general agent): runs `srs_engine find-similar`, updates `chapters/<slug>.md`, `propositions.jsonl`, `chains.jsonl`, syncs Obsidian/Mnemonic cues (`vault_sync`), and compiles `chapters/<slug>-sheet.pdf` via `cheatsheet_pdf`.

---

## Discipline Rules

1. **Never stall or send bare tool-only turns**: pair background launches with active teaching/probing prose; never poll in a loop.
2. **Self-contained briefs & disjoint writes**: subagents start cold — give exact paths, invariants, and exclusive output targets so two workers never edit the same file.
3. **Zero repo pollution**: scratch tests and intermediate renders belong in `/tmp` (with `-B` on Python so zero `__pycache__` is created).
