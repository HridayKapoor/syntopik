# Security Policy

## Supported Versions

| Version | Supported |
| :--- | :--- |
| `2.x` | Yes |
| `1.x` | Security fixes only (upgrade to `2.0.0`) |
| `< 1.0` | No |

---

## Reporting a Vulnerability

Please **do not** open a public GitHub issue for security vulnerabilities, CSRF/auth bypasses in `syntopik serve`, LAN pairing escapes, zip-slip/path-traversal defects, sandbox escapes, or XSS in offline HTML generators.

Report vulnerabilities privately through **GitHub Security Advisories**:

- **Private Advisory Form**: <https://github.com/HridayKapoor/syntopik/security/advisories/new>

Include:
1. Affected module (`syntopik/*`, `scripts/*`), MCP tool, HTTP endpoint, or HTML artifact and exact version/commit.
2. Minimal reproduction steps or proof-of-concept input.
3. Observed security impact and any suggested remediation.

We aim to acknowledge reports within **72 hours** and coordinate a tested fix and disclosure timeline with the reporter.

---

## Security & Privacy Model

### 1. Local-First Privacy & Zero-PII Export Guarantees
- **Zero telemetry and zero cloud accounts**: Syntopik makes no background analytics, tracking, or account-sync calls.
- **Zero external CDNs in offline & live UI**: `dashboard.html`, `knowledge-graph/viewer.html`, `demos/exam-cbt-template.html`, generated Pocket HTML5 commute decks (`syntopik/pocket.py`), and pre-bundled `mcp-app/mlh-studio/dist/*.html` load zero remote `<script>` or `<link>` resources.
- **GitHub-clean learner data separation & non-destructive `attach`**: Personal ledgers (`knowledge-graph/{propositions,chains,revlog,mistakes,reading-queue,forks,arenas,decisions,exam-attempts}.jsonl`, `study-config.json`, `fsrs-params.json`, `blitz-sessions.json`, `review-queue.md`, `graph-data.js`), study notes (`chapters/*`), sources (`sources/*`), `.syntopik/`, and `*.syntopik-pack.zip` are gitignored by design and remain strictly on local disk. `syntopik attach` writes exclusions to `.git/info/exclude` without modifying host `.gitignore` or overwriting host `AGENTS.md`/`CLAUDE.md`.
- **Zero-PII Shareable Study Packs (`syntopik/packs.py`)**:
  - `export_pack` strips all personal fields (`STRIPPED_PERSONAL_FIELDS`: `fsrs`, `bktMastery`, `nextReview`, `lapses`, `revlog`, `personalNotes`, `lastReviewed`, `lastImplicitReview`, `notes`, and local filesystem paths) so exported `.syntopik-pack.zip` / `.json` packs contain strictly shareable topic knowledge, DAG chains, cues, and canonical traps.
  - Every pack embeds cryptographic integrity metadata (`SHA256SUMS.json` + `contentSha256`). `verify_pack` and `import_pack` reject **zip-slip** (`..`, absolute paths, `\\`, null bytes), **POSIX symlinks / special files**, **zip bombs** (size & compression bounds), and **checksum tampering**.
  - Imports run ID + lexical deduplication and **never** overwrite existing `solid` mastery or personal FSRS schedules.
- **Backup archive integrity (`scripts/backup_harness.py`)**: Every backup embeds a SHA-256 `backup-manifest.json`; `verify` and `restore` reject path traversal (`../`), absolute paths, unsafe symlinks, and checksum mismatches.

### 2. Local Studio Server (`syntopik serve` / `syntopik/server.py`) Security Model
- **Loopback-only by default (`127.0.0.1`)**: `syntopik serve` binds strictly to `127.0.0.1` and rejects non-loopback `Host` and `Origin` headers (`403 Forbidden`) to prevent DNS-rebinding and cross-site request forgery from external tabs.
- **Per-process CSRF token (`X-Syntopik-Token`)**: Every mutating `POST` and `PATCH` endpoint requires a cryptographically generated per-process `X-Syntopik-Token` header (`403 Forbidden` if missing or invalid).
- **Strict canonical path jail & atomic locking**: Every file route canonicalizes paths (`resolve().is_relative_to(...)`) against the active workspace or bundled asset root, blocking directory traversal. All JSONL/JSON writes use OS temp-dir reentrant locks (`syntopik/storage.py`) and atomic `os.replace` swaps.
- **Opt-in `--lan` scoped mobile pairing**:
  - `--lan` is strictly opt-in (off by default). When enabled, Syntopik generates a one-time `pair=<token>` QR URL (`syntopik/pocket.py`) that exchanges for an `HttpOnly` session cookie.
  - LAN-authenticated clients are **strictly scoped exclusively to `/pocket`, `/api/pocket/state`, and `POST /api/review/batch`**. All privileged routes (filesystem browsing, `/api/import`, `/api/export/*`, `/api/pack/*`, `/api/tracker/*`, `/api/exam/submit`, `/api/arena/submit`) remain **loopback-only (`127.0.0.1`)**.

### 3. Execution Sandboxes & Zero-Leak Sealing Boundaries
- **`scripts/code_verify.py` (Educational Process Sandbox)**:
  - Runs learner submissions in ephemeral temporary directories with sanitized environment variables (`PYTHONNOUSERSITE=1`, `PYTHONDONTWRITEBYTECODE=1`, isolated `HOME`/`TMPDIR`), process-group timeouts (`SIGKILL` on expiry), stdout/stderr byte caps, and memory limits (`NODE_OPTIONS` heap cap + POSIX limits).
  - Defense-in-depth limits (v3.2+): no network on Linux via an unprivileged `unshare -rn` user+network namespace when the host allows it (opt out with `SYNT_SANDBOX_NETWORK=allow`); `RLIMIT_FSIZE` (64 MB per file) and an `RLIMIT_CPU` backstop on POSIX; a Job Object committed-memory cap on Windows. Every result carries a `sandbox` object stating which limits were actually applied (`networkIsolated`, `memoryLimit`: `rlimit_as` | `job_object` | `node_heap` | `none`).
  - **Threat-model boundary**: `code_verify.py` is a lightweight local process sandbox designed for **single-learner, self-authored study code**. It is **not** a hardened multi-tenant container, seccomp, or VM isolation boundary for executing untrusted remote code. Run Syntopik inside a dedicated OS container or VM when evaluating untrusted third-party submissions.
- **`scripts/falsify_arena.py` (Zero-Leak Adversarial Mutant Isolation)**:
  - Executes `code` arenas in isolated Python subprocesses (`-B -I`, strict timeout) and `math`/`physics` arenas via AST-whitelisted evaluators (lazy SymPy fallback).
  - **Strict zero-leak contract**: `test_arena_input` returns only `{validAssumption, failedAssumptions, killed, newlyKilled, survivedCount, minimalityScore, bestMinimalSize}` and **never** discloses surviving mutants' IDs, source code, expressions, or `trapTag`s.
- **Fork Replay & MCP Server Secret Sealing**:
  - `syntopik/forks.py` (`scripts/fork_replay.py`) plays deterministic refutations on repeat blunders without revealing `sealedWinningMove` unless `--reveal-fix` is explicitly passed.
  - `mcp-app/mlh-studio/` seals `modelSolution` and Parsons/Faded `canonicalOrder` server-side until `action: "conclude"` and strips `isDistractor` flags on pose. On terminal-only MCP hosts without `io.modelcontextprotocol/ui`, correct answers, explanations, and solutions are withheld from model-visible output.
- **Expression & HTML injection hardening**:
  - `scripts/math_verify.py` and `scripts/math_plot.py` enforce strict Python AST whitelisting and refuse `__import__`, file I/O, attribute traversal, and arbitrary callables.
  - `scripts/exam_runner.py` and `syntopik/pocket.py` escape inline JSON payloads (`\u003c`, `\u003e`, `\u0026`, `\u2028`, `\u2029`) to prevent `</script>` breakout XSS in standalone HTML exams and Pocket decks.

### 4. Untrusted External Content & Ingestion Rules (`syntopik/ingest.py`, `scripts/research_scout.py`)
- **Strict URL scheme & host validation**: Both `syntopik/ingest.py` (`scripts/ingest_source.py`) and `scripts/research_scout.py read` enforce `http://` and `https://` schemes with non-empty hostnames, rejecting `file://`, `javascript:`, `ftp:`, and local-filesystem URI traversal across all backends. Local note/repo ingestion enforces workspace path-jail boundaries.
- **Bounded scope & prompt-injection hygiene**: Curated source manifests are hard-capped at `maxSources <= 8` with explicit `inScope`/`outOfScope` fences. External web pages, YouTube transcripts, arXiv papers, PDFs, Obsidian vaults, Study Packs, and code repositories must always be treated as **untrusted data**, never as privileged system or shell instructions.
- **Official harness primitives only**: Syntopik integrates exclusively via documented workspace/plugin files (`AGENTS.md`, `GEMINI.md`, `CLAUDE.md`, `.claude-plugin/*`, `gemini-extension.json`, MCP stdio, hooks, and skills). Never scrape OAuth tokens or wrap proprietary CLIs in unauthorized proxies.
