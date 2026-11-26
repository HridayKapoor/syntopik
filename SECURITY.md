# Security Policy

## Supported Versions

| Version | Supported |
| :--- | :--- |
| `1.x` | Yes |
| `< 1.0` | No |

---

## Reporting a Vulnerability

Please **do not** open a public GitHub issue for security vulnerabilities, sandbox escapes, XSS in offline HTML generators, or path-traversal defects.

Report vulnerabilities privately through **GitHub Security Advisories**:

- **Private Advisory Form**: <https://github.com/HridayKapoor/syntopik/security/advisories/new>

Include:
1. Affected script, MCP tool, or HTML artifact and exact version/commit.
2. Minimal reproduction steps or proof-of-concept input.
3. Observed security impact and any suggested remediation.

We aim to acknowledge reports within **72 hours** and coordinate a tested fix and disclosure timeline with the reporter.

---

## Security & Privacy Model

### 1. Local-First Privacy Guarantees
- **Zero telemetry and zero cloud accounts**: Syntopik makes no background analytics, tracking, or account-sync calls.
- **Zero external CDNs in offline UI**: `dashboard.html`, `knowledge-graph/viewer.html`, `demos/exam-cbt-template.html`, and pre-bundled `mcp-app/mlh-studio/dist/*.html` load zero remote `<script>` or `<link>` resources.
- **GitHub-clean learner data separation**: Personal ledgers (`knowledge-graph/{propositions,chains,mistakes,exam-attempts}.jsonl`, `review-queue.md`, `graph-data.js`), study notes (`chapters/*`), sources (`sources/*`), and generated exams (`exams/*`) are gitignored by design and remain strictly on local disk.
- **Archive integrity (`scripts/backup_harness.py`)**: Every backup archive embeds a SHA-256 `backup-manifest.json`; `verify` and `restore` reject path traversal (`../`), absolute paths, unsafe symlinks, and checksum mismatches.

### 2. Execution & Parser Sandbox Boundaries
- **`scripts/code_verify.py` (Educational Process Sandbox)**:
  - Runs learner submissions in ephemeral temporary directories with sanitized environment variables (`PYTHONNOUSERSITE=1`, `PYTHONDONTWRITEBYTECODE=1`, isolated `HOME`/`TMPDIR`), process-group timeouts (`SIGKILL` on expiry), stdout/stderr byte caps, and memory limits (`NODE_OPTIONS` heap cap + POSIX limits).
  - **Threat-model boundary**: `code_verify.py` is a lightweight local process sandbox designed for **single-learner, self-authored study code**. It is **not** a hardened multi-tenant container, seccomp, or VM isolation boundary for executing untrusted remote code. If you evaluate untrusted third-party submissions, run Syntopik inside a dedicated OS container or VM.
- **Expression & HTML injection hardening**:
  - `scripts/math_verify.py` and `scripts/math_plot.py` enforce strict Python AST whitelisting and refuse `__import__`, file I/O, attribute traversal, and arbitrary callables.
  - `scripts/exam_runner.py` escapes inline JSON payloads (`\u003c`, `\u003e`, `\u0026`, `\u2028`, `\u2029`) to prevent `</script>` breakout XSS in standalone HTML exams.
- **MCP Server secret sealing (`mcp-app/mlh-studio/`)**:
  - `synt_practice_card` / `mlh_practice_card` seals `modelSolution` server-side until `action: "conclude"`. On terminal-only MCP hosts without `io.modelcontextprotocol/ui`, both quiz explanations/correct answers and practice solutions are withheld from model-visible output.

### 3. Untrusted External Content & Research Rules (`scripts/research_scout.py`)
- **Strict URL scheme validation**: `research_scout.py read` enforces `http://` and `https://` schemes before dispatching to any research backend, blocking `file://` and local-filesystem URI traversal across all tiers.
- **Bounded scope & prompt-injection hygiene**: Curated source manifests are hard-capped at `maxSources <= 8` with explicit `inScope`/`outOfScope` fences. External web pages, papers, PDFs, and code repositories fetched during topic onboarding must always be treated as **untrusted data**, never as privileged system or shell instructions.
- **Official harness primitives only**: Syntopik integrates exclusively via documented workspace files (`AGENTS.md`, `GEMINI.md`, `CLAUDE.md`, MCP stdio, hooks, and skills). Never scrape OAuth tokens or wrap proprietary CLIs in unauthorized proxies.
