## Summary

<!-- Briefly describe what this PR changes and why. Link related issues (e.g. Fixes #12). -->

## Type of Change

- [ ] Bug fix (non-breaking change that fixes an issue)
- [ ] Feature / deterministic engine / skill upgrade
- [ ] Multi-harness compatibility / MCP Studio / offline UI
- [ ] Documentation, CI, or repository hygiene

## Verification Checklist

Please verify every applicable invariant from [`CONTRIBUTING.md`](./CONTRIBUTING.md) before requesting review:

- [ ] **Health doctor (`0 warn, 0 fail`)**: Ran `.venv/bin/python -B scripts/study_doctor.py` (or `python3 -B scripts/study_doctor.py --core-only --json` for core-only changes).
- [ ] **Sandboxed tests (`100% PASS`)**: Ran `.venv/bin/python -B -m unittest discover -s tests -v` (or the core stdlib unittest suite) with zero mutations outside `/tmp`.
- [ ] **Zero personal paths or study data**: Confirmed no local user home paths, no hardcoded Homebrew node paths, and no tracked personal learner ledgers (`knowledge-graph/{propositions,chains,mistakes,exam-attempts}.jsonl`, `review-queue.md`, `graph-data.js`, `chapters/*`, `sources/*`, `exams/*`).
- [ ] **Zero external CDNs**: Confirmed offline HTML hubs (`dashboard.html`, `knowledge-graph/viewer.html`, `demos/exam-cbt-template.html`) and `mcp-app/mlh-studio/dist/*.html` contain zero external `https://` `<script>` or `<link>` tags (rebuilt `mcp-app/mlh-studio/dist/` via `node build.mjs` if `src/` changed).
- [ ] **Rule & skill budgets**: Confirmed `GEMINI.md + AGENTS.md <= 9,500 B` (zero YAML frontmatter), `CLAUDE.md` starts with `@AGENTS.md`, and every `.agents/skills/*/SKILL.md` frontmatter has valid `name`/`description` with zero `<` or `>` characters.
- [ ] **Clean tree**: No `__pycache__` directories, no `.DS_Store`, and no heavyweight local ML/VLM weights added.
