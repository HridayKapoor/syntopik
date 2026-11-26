---
name: knowledge-graph
description: >-
  Maintain the Knowledge Hypergraph with FIRe credit, Rosetta isomorphism
  bridges, SRC knockouts, 4-tier evidence, causal halos, CRDT multi-device sync,
  sunset pruning, FSRS-6 tuning, occlusion, and Obsidian.
---

# Persistent Knowledge Hypergraph, FIRe HSRS, Calibration & Vault Sync

Every topic studied in **Syntopik** compounds into a privacy-safe (gitignored on disk, backed up via `backup_harness.py`) local hypergraph in `knowledge-graph/` shared across **Antigravity 2.0**, **Claude Code**, **Codex / T3 Code**, **Cursor/Windsurf/Copilot/OpenCode**, and `syntopik serve` (`SYNT_WORKSPACE` / `SYNT_KG_DIR`, `MLH_*` fallbacks). Core SRS, FIRe, graph intelligence, and vault sync run on **bare Python 3.12+ stdlib**!

*(Portable CLI: use `syntopik review|export|run <engine>` in attached repos, or `.venv/bin/python -B scripts/<engine>.py` / `python3 scripts/<engine>.py` in direct clones.)*

---

## File Schemas (`knowledge-graph/`)

### 1. `propositions.jsonl` (Atomic Propositions — Update In-Place by `id`)
```json
{"id":"systems/raft-leader-completeness","subject":"Computer Science","chapter":"Raft Consensus","title":"Any committed log entry is present in the log of every leader for all higher terms","aliases":["Raft Leader Completeness Property"],"kind":"unconditional_truth","status":"solid","confidence":0.94,"bktMastery":0.88,"lastTested":"2026-11-26","nextReview":"2026-12-03","media":{"src":"demos/raft-quorum.svg","mode":"hide_one","masks":[{"id":"m1","bbox":[0.15,0.20,0.45,0.38],"label":"Majority quorum intersection"}]},"cues":[{"id":"c1","kind":"application","q":"Why cannot a candidate missing a committed entry win a majority vote in Raft?","a":"Majority election quorum and majority replication quorum must intersect in at least one voter whose log is at least as up-to-date.","lastRating":"good","reviews":1}]}
```
- `kind`: `"unconditional_truth"` | `"axiom"` | `"definition"` | `"derivation"` | `"heuristic"`.
- `status`: `"solid"` (unaided) | `"shaky"` (hinted/low-confidence) | `"misconception"` (active wrong model) | `"planned"`.
- `cues[]`: distinct retrieval cues (`kind`: `recall` | `application` | `procedural` | `salience`, plus `{{c1::...}}` Cloze support) sharing one proposition-level FSRS-6 + BKT schedule.
- `media`: flat list `["demos/fig.png"]`, `{"front":[...],"back":[...]}`, or declarative **SVG Image Occlusion** spec (`{"src":"...", "mode":"hide_one|hide_all", "masks":[{"id","bbox","label","propId"}]}` auto-emitted by `pdf_studio.py annotate --occlude`).

### 2. `chains.jsonl` (Jointly-Entailing Hyperedges + FIRe Encompassing Weights)
```json
{"premises":["systems/majority-quorum-intersection","systems/up-to-date-vote-restriction"],"conclusion":"systems/raft-leader-completeness","relation":"derives","encompassWeight":0.35,"note":"Intersecting majorities + up-to-date voting jointly guarantee leader completeness","recordedAt":"2026-11-26"}
```
- Single-premise binary edges (`{"from":"...","to":"...","relation":"derives","encompassWeight":0.50}`) stay 100% compatible.
- **Joint-Entailment Law**: never split $A \wedge B \rightarrow C$ into separate binary edges.
- `relation`: `"requires"` | `"derives"` | `"generalizes"` | `"contrasts_with"` | `"isomorphic_to"` (default `encompassWeight`: `0.50` binary, `0.35`/premise joint, `0.25` `isomorphic_to` capped `<= 0.30`, `0.0` `contrasts_with`).
- **Rosetta `isomorphic_to` Chords**: symmetric cross-domain links carrying `archetypeId`, `variableBijection`, and explicit `breaksAt[]` negative-transfer guards; propagate attenuated (`0.25`, max `0.30`) bidirectional FIRe credit without ever clearing `misconception` twins or triggering SRC prerequisite knockouts.

### 3. `revlog.jsonl`, `mistakes.jsonl`, `reading-queue.jsonl`, `learner-profile.json`, `whiteboard-session.json` & `sync-state.json` (All Gitignored)
- `revlog.jsonl`: append-only review telemetry (`propId`, `cueId`, `ts`, `rating`, `elapsedDays`, `predictedR`, `learnerConfidence`, `correct`, `hintsUsed`, `durationMs`) powering personal FSRS-6 optimization, Tier-3 psychology updates, longitudinal policy replay, Brier calibration, and the 365-day retention heatmap.
- `mistakes.jsonl`: root errors (`category` across 13 types + optional `confusedWith` pair counter) kept separate from clean proposition statements.
- `reading-queue.jsonl`: SuperMemo-style Incremental Reading queue (`priority` 0–100, `stage`: `queued -> extracted -> clozed -> crystallized`).
- `learner-profile.json` (`< 8 KB`, `syntopik profile status|calibrate|policy|override|reset`): 3-Cadence Non-VARK Learner Psychology profile storing domain Pelánek Elos, 6-state affect, 3-tier NEA Failure DNA, bounded `calibratedHyperparams`, transparent `userOverrides`, and compiled Kalyuga `TeachingPolicy`.
- `whiteboard-session.json` (`syntopik board status|push|pin|clear`): atomic Live AI Chalkboard frames + unresolved learner `pendingPins`.
- `sync-state.json` (`syntopik sync-delta status|export|verify|merge`): opaque per-device ID, Lamport clock, and merged delta SHA-256 history for offline-first CRDT multi-device sync.

---

## Core v2.0 HSRS, Calibration & Vault Sync Workflows

1. **Deduplicate BEFORE Creating Any Node (`find-similar`)**:
   ```bash
   syntopik run srs_engine find-similar "Committed entry present in all future leaders" --top 5 --threshold 0.25 --json
   ```
   Reuse matching `id`s and append new phrasings to `aliases[]`.

2. **FIRe (Fractional Implicit Repetition) + SRC Knockouts + Confidence Telemetry**:
   ```bash
   syntopik run srs_engine review systems/raft-leader-completeness --rating good --cue-id c1 --confidence 0.85 --duration-ms 9200 --json
   syntopik run srs_engine due --compress --json
   ```
   - **FIRe** (enabled by default; `--no-fire` disables): on unaided `good`/`easy` of `C`, walks prerequisite ancestors in `chains.jsonl` up to depth `d <= 3` (`gamma = 0.7`), discounts by urgency `(1 - R_u)`, NEVER auto-clears `misconception` nodes, extends ancestor stability/interval, records `implicitCredit`/`lastImplicitReview`, and returns `fireCredits[]`.
   - **Spaced Repetition Compression (`due --compress`)**: greedy set-cover selecting composite due items that knock out `>= 2` due prerequisites in one review.
   - **Confidence-Aware BKT & Hypercorrection**: high-confidence miss ($c \ge 0.75, y=0$) flags `hypercorrection: true` (immediate contrastive repair + 1–2d retest); low-confidence hit ($c \le 0.35, y=1$) caps status at `shaky`.

3. **Calm Backlog Recovery, Readiness Forecast, Personal Optimizer & Leech Hygiene**:
   ```bash
   syntopik run srs_engine recover --daily-cap 15 --json                   # Zero-guilt 5-7d backlog smoother (risk * blast_radius)
   syntopik run srs_engine forecast --exam-date 2026-12-15 --json          # Projected R(t_exam), 7d load, Brier & overconfidence bias
   syntopik run srs_engine optimize --json                                 # Zero-PyTorch SciPy L-BFGS-B FSRS-6 fit (>=100 logs, OOS Log-Loss gate)
   syntopik run srs_engine leeches --json                                  # Flags lapses >= 4 or difficulty >= 8.5 -> split/prereq/teach-back
   ```
   - **Per-Type Sibling Burying**: buries sibling text clozes/cues from the same proposition for 24h while permitting grouped spatial sweeps on Image Occlusion cards.

4. **SVG Image Occlusion, Cloze & Visual Anki Export**:
   ```bash
   syntopik run pdf_studio annotate --pdf "sources/<file>.pdf" --page 5 --crop ... --marks '[...]' --occlude --out ... --json
   syntopik run srs_engine add-cue <propId> --q "{{c1::Quorum intersection}} ensures..." --a "..." --json
   syntopik export anki --out knowledge-graph/syntopik-deck.apkg          # Native Cloze + SVG Image Occlusion + Basic Visual cards
   syntopik run srs_engine push-anki
   ```

5. **Bidirectional Obsidian Vault & Mnemonic Medium (`:::cue`) Sync (`vault_sync.py`)**:
   ```bash
   syntopik export obsidian --out ~/Notes/Syntopik --json                  # Alias: --vault ~/Notes/Syntopik (clean YAML, [[wikilinks]], Trap MOC)
   syntopik run vault_sync import --vault ~/Notes/Syntopik --json          # Imports edited :::cue & {{c1::...}} without clobbering FSRS state
   syntopik run vault_sync sync-notes --chapters-dir chapters --json       # Bidirectional chapters/*.md :::cue {propId, id, kind} sync w/ line anchors
   ```
   Updates strictly inside `<!-- syntopik:begin -->...<!-- syntopik:end -->` markers so personal learner prose is 100% preserved across Git/iCloud!

6. **4-Tier Evidence Ledger, Causal Root-Cause Halos & Semester Sunset Pruning (v3.0)**:
   ```bash
   syntopik sunset <slug> [--apply] --json                                 # Snapshots zero-PII pack, keeps core axioms at R=0.80, archives leaf clutter
   syntopik export dossier --out chapters/mastery-dossier.md --json        # Verifiable evidence-tiered Mastery Dossier export
   syntopik profile status --json                                          # Inspect domain Elo, 6-state affect, Failure DNA & TeachingPolicy
   ```
   - **4-Tier Evidence (`T1`–`T4`, `syntopik/evidence.py`)**: separates passive recall (`T1`/`T2`) from verified transfer (`T3`: Arena / Blackout / CBT) and structural defense (`T4`: Viva / Axiom-Cut), surfacing anti-inflation alerts whenever a high-centrality node stays stuck at `T1`/`T2`.
   - **Bayesian Noisy-OR Causal Root-Cause Halos (`syntopik/root_cause.py`)**: traces recurring leaf failures upstream across `chains.jsonl`, renders amber causal-suspect halos (`rootCauseHotspots.byProp[id] >= 0.35`) in `knowledge-graph/viewer.html`, and selects the ancestor probe maximizing expected entropy reduction ($\Delta H$).

7. **Zero-Install Offline Pocket Commute Deck & Zero-PII Shareable Study Packs**:
   ```bash
   syntopik export pocket --limit 25 --out chapters/pocket-review.html     # ~45 KB zero-CDN mobile swipe/occlusion deck (opt-in LAN QR: syntopik serve --lan)
   syntopik import --pocket-sync pocket-sync.json                          # Idempotent FSRS-6 + FIRe replay by reviewId
   syntopik pack export <slug-or-subject> --out <slug>.syntopik-pack.zip   # SHA-256 verified, strips all personal FSRS/mastery/revlog PII
   syntopik pack import <path> --mode merge|suspend-all                    # Zip-slip/symlink safe; find-similar dedup never overwrites solid mastery
   syntopik pack inspect <path>
   ```

8. **Structural Isomorphism Rosetta Engine & Offline-First CRDT Multi-Device Sync (v3.1)**:
   ```bash
   syntopik rosetta archetypes --json                                      # Inspect 10 curated cross-domain structural archetypes
   syntopik rosetta scan [--min-score 0.35] --json                         # Discover cross-domain twins & breaksAt negative-transfer guards
   syntopik rosetta bridge --prop-id <id> --json                           # Build bijection opener from a mastered structural twin
   syntopik rosetta link --source <a_id> --target <b_id> --archetype <id> --json
   syntopik sync-delta status --json                                       # Device ID, Lamport clock & sync state
   syntopik sync-delta export --out sync-delta.json [--since <iso>] --json # Export SHA-256 tamper-evident syntopik-delta-v1 snapshot
   syntopik sync-delta verify --file sync-delta.json --json                # Fail-closed SHA-256, 10 MiB & slug verification
   syntopik sync-delta merge --file sync-delta.json [--dry-run] --json     # Commutative, monotonic, idempotent CRDT merge + evidence re-chain
   ```
   - **CRDT Convergence Guarantees**: G-Set union across append-only ledgers, monotonic chapter tracker topic/question lattices (mastery never regressed; milestones ORed), weighted per-device domain Elo merge, and deterministic SHA-256 re-chaining of `evidence-ledger.jsonl`.

9. **Graph Intelligence, Confusable Pairs, Daily Plans & Ambient MCP (v3.1)**:
   ```bash
   syntopik next --json                                                    # Unified <=180-token Pedagogical Brainstem Action Envelope (+ isomorphicBridge)
   syntopik run graph_intel --bottlenecks --islands --combos --confusables --rosetta --json
   syntopik run daily_plan --minutes 60 --json                             # Or --micro 5 for 5-min sprint; emits [A, B, A'] contrast triplets
   syntopik run audio_brief --chapter chapters/<slug>.md --pause-sec 2.5 --out chapters/<slug>-audio.mp3
   syntopik run mistake_analytics summary --json                           # Log/analyze 13 root categories + confusedWith pairs
   ```
   In *any* coding or reading chat, call headless MCP tools **`synt_next_move`** (includes `isomorphicBridge` when a mastered twin matches a frontier node), **`synt_whiteboard_step`**, **`synt_capture_concept`**, **`synt_get_frontier`** (includes `learnerProfile`, `teachingPolicy`, `dueForks`, `arenasCount`, `coldStartDays`, `pendingPins`, `rosettaBridgesCount`, and `syncDeviceId` when initialized), and **`synt_log_review`** (pass `hintsUsed` `0..5` & `durationMs`).
