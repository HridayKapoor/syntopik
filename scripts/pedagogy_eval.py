#!/usr/bin/env python3
"""Syntopik v2.0 — Zero-LLM TutorBench-Style Pedagogical Linter & Golden Eval Pack.

Pure Python 3.12+ standard library deterministic auditor verifying Syntopik's
calm, zero-sycophancy, zero-leak pedagogical invariants on tutor turns, graded
quiz cards, and a built-in 15-scenario multi-domain benchmark suite covering
TutorBench's 3 core rubrics (*Adaptive Explanation*, *Actionable Non-Spoiling
Error Feedback*, and *Scaffolding Hinting*) across CS, ML, Math, Physics, and
Chemistry.

Subcommands:
  - `lint [file_or_-] [--mode teach|practice|hint|conclude|side_doubt] [--max-words N] [--json]`
  - `check-card <json_file_or_-> [--json]`
  - `bench [--verbose] [--json]`
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
from typing import Any

# ---------------------------------------------------------------------------
# Pedagogical Invariant Thresholds (from AGENTS.md & TutorBench / LearnLM)
# ---------------------------------------------------------------------------

PROSE_WORD_LIMIT = 160
SIDE_DOUBT_WORD_LIMIT = 40
HINT_WORD_LIMIT = 35
MAX_CODE_BLOCK_LINES = 18
MAX_TABLE_DATA_ROWS = 4
MAX_IMPORTANT_CALLOUTS = 1
OPTION_PARITY_MAX_RATIO = 2.2

SYCOPHANCY_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("great_question", re.compile(r"\b(?:great|excellent|good|wonderful|fantastic|brilliant)\s+question\b", re.IGNORECASE)),
    ("exclamation_praise", re.compile(r"\b(?:excellent|awesome|amazing|fantastic|brilliant|wonderful|superb|outstanding|phenomenal|impressive|bravo|perfect)\s*!", re.IGNORECASE)),
    ("spot_on", re.compile(r"\b(?:spot\s+on|you\s+nailed\s+it|nailed\s+it|absolutely\s+right\s*!)\b", re.IGNORECASE)),
    ("generic_praise", re.compile(r"\b(?:well\s+done|great\s+job|good\s+job|nice\s+work|you(?:'re|\s+are)\s+doing\s+great|you(?:'ve|\s+have)\s+got\s+this|keep\s+up\s+the\s+great\s+work)\b", re.IGNORECASE)),
    ("flattery", re.compile(r"\b(?:i\s+love\s+(?:that|how\s+you)|what\s+a\s+great\s+(?:observation|insight|point)|that(?:'s|\s+is)\s+(?:so|really)\s+insightful|you(?:'re|\s+are)\s+so\s+smart)\b", re.IGNORECASE)),
)

OPTION_LEAK_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("recommended_tag", re.compile(r"\(\s*recommended\s*\)", re.IGNORECASE)),
    ("because_justification", re.compile(r"\bbecause\b", re.IGNORECASE)),
    ("since_justification", re.compile(r"\bsince\s+(?:it|the|this|that|they|we|a|an)\b", re.IGNORECASE)),
    ("therefore_justification", re.compile(r"\b(?:therefore|thus|which\s+means|due\s+to\s+the\s+fact|as\s+a\s+result\s+of|this\s+is\s+why)\b", re.IGNORECASE)),
    ("answer_spoiler", re.compile(r"\b(?:correct\s+answer|this\s+is\s+correct)\b", re.IGNORECASE)),
)

IDK_OPTION_RE = re.compile(r"^\s*(?:i\s+don['’]?t\s+know|i\s+do\s+not\s+know|unsure\s*/\s*i\s+don['’]?t\s+know)\s*\.?\s*$", re.IGNORECASE)

CONCLUSION_SIGNAL_RE = re.compile(
    r"\b(?:concluded|problem\s+complete|verified\s+solution|canonical\s+solution|model\s+solution|"
    r"locked\s+in|marking\s+(?:this|it)\s+solid|solution\s+verified|fully\s+solved|that\s+closes\s+this\s+problem)\b",
    re.IGNORECASE,
)

UNSOLICITED_NEXT_PROBLEM_RE = re.compile(
    r"(?:^|\n|\.\s+)(?:#+\s*)?(?:next\s+problem\s*:|here(?:'s|\s+is)\s+(?:the\s+next|another)\s+(?:problem|exercise|question)|"
    r"now\s+(?:try|solve)\s+(?:this|the\s+following)\s*(?:problem|exercise|one)?\s*:|"
    r"moving\s+(?:straight\s+)?on\s+to\s+(?:problem|question)\s+\d+|"
    r"problem\s+#?\d+\s*:)",
    re.IGNORECASE,
)

HINT_SPOILER_RE = re.compile(
    r"\b(?:the\s+(?:final\s+)?answer\s+is|final\s+answer\s*:|the\s+solution\s+is|just\s+write\s+down|"
    r"the\s+correct\s+option\s+is|spoiler\s*:)\b",
    re.IGNORECASE,
)

MULTI_PROBLEM_RE = re.compile(
    r"(?:^|\n)\s*(?:\*\*)?(?:Problem|Question|Exercise|Part)\s+(?:2|B|II)\b",
    re.IGNORECASE,
)

MD_MCQ_LINE_RE = re.compile(r"^\s*(?:[-*]\s+)?(?:\*\*)?([A-E])[)\.:](?:\*\*)?\s+(.+?)\s*$")


# ---------------------------------------------------------------------------
# Prose Counter (Respects AGENTS.md Exclusions Exactly)
# ---------------------------------------------------------------------------


def _extract_tables(text: str) -> tuple[str, list[int]]:
    """Detect Markdown tables and return `(text_with_first_valid_table_removed, data_row_counts)`."""
    lines = text.splitlines()
    out_lines: list[str] = []
    table_data_rows: list[int] = []
    removed_first_valid = False

    i = 0
    while i < len(lines):
        # Detect GFM table: header row with `|`, followed by separator `| :--- | --- |`
        if (
            i + 1 < len(lines)
            and "|" in lines[i]
            and re.match(r"^\s*\|?(?:\s*:?-{3,}:?\s*\|)+\s*:?-{3,}:?\s*\|?\s*$", lines[i + 1])
        ):
            j = i + 2
            while j < len(lines) and "|" in lines[j] and lines[j].strip():
                j += 1
            data_rows = max(0, j - (i + 2))
            table_data_rows.append(data_rows)
            if not removed_first_valid and data_rows <= MAX_TABLE_DATA_ROWS:
                removed_first_valid = True
            else:
                out_lines.extend(lines[i:j])
            i = j
            continue
        out_lines.append(lines[i])
        i += 1
    return "\n".join(out_lines), table_data_rows


def count_prose_words(text: str) -> dict[str, Any]:
    """Count prose words applying Syntopik's exact exclusions:
    - Excludes at most 1 display math block (`$$...$$` or `\\[...\\]`)
    - Excludes at most 1 fenced code block IF `<= 18` lines
    - Excludes at most 1 Markdown table IF `<= 4` data rows
    - Excludes `<agent-embed ...></agent-embed>` markup
    """
    working = text.replace("\r\n", "\n")

    # Remove inline embed HTML tags
    working = re.sub(r"<agent-embed\b[^>]*>.*?</agent-embed>", " ", working, flags=re.IGNORECASE | re.DOTALL)

    # Count IMPORTANT callouts before stripping
    important_callouts = len(re.findall(r"^\s*>\s*\[!IMPORTANT\]", working, flags=re.MULTILINE | re.IGNORECASE))

    # Fenced code blocks
    code_blocks: list[int] = []
    excluded_code_used = False

    def _handle_code(m: re.Match[str]) -> str:
        nonlocal excluded_code_used
        body = m.group(1)
        line_count = len([ln for ln in body.splitlines()]) if body.strip() else 0
        code_blocks.append(line_count)
        if not excluded_code_used and line_count <= MAX_CODE_BLOCK_LINES:
            excluded_code_used = True
            return "\n"
        return "\n" + body + "\n"

    working = re.sub(r"```[^\n]*\n(.*?)```", _handle_code, working, flags=re.DOTALL)

    # Display math blocks ($$ ... $$ or \[ ... \])
    math_blocks = 0
    excluded_math_used = False

    def _handle_math(m: re.Match[str]) -> str:
        nonlocal math_blocks, excluded_math_used
        math_blocks += 1
        if not excluded_math_used:
            excluded_math_used = True
            return "\n"
        return " " + m.group(1) + " "

    working = re.sub(r"\$\$(.*?)\$\$", _handle_math, working, flags=re.DOTALL)
    working = re.sub(r"\\\[(.*?)\\\]", _handle_math, working, flags=re.DOTALL)

    # Markdown tables
    working, table_data_rows = _extract_tables(working)

    # Strip alert header tokens (`> [!IMPORTANT]`), markdown links URLs, and formatting symbols
    cleaned = re.sub(r"^\s*>\s*\[![A-Z]+\]\s*", "", working, flags=re.MULTILINE)
    cleaned = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", cleaned)
    cleaned = re.sub(r"[#*_`~>|]", " ", cleaned)

    words = [w for w in re.findall(r"[A-Za-z0-9]+(?:['’.-][A-Za-z0-9]+)*", cleaned) if w]
    return {
        "proseWords": len(words),
        "codeBlockLines": code_blocks,
        "displayMathBlocks": math_blocks,
        "tableDataRows": table_data_rows,
        "importantCallouts": important_callouts,
        "proseWithoutExclusions": working,
    }


# ---------------------------------------------------------------------------
# Graded Quiz Card Auditor (`check-card` & Markdown MCQ auto-check)
# ---------------------------------------------------------------------------


def _extract_option_texts(card: dict[str, Any]) -> list[str]:
    raw_opts = card.get("options") or card.get("choices") or []
    texts: list[str] = []
    for opt in raw_opts:
        if isinstance(opt, str):
            texts.append(opt.strip())
        elif isinstance(opt, dict):
            lbl = opt.get("label") or opt.get("text") or opt.get("value") or ""
            desc = opt.get("description") or ""
            combined = f"{lbl} {desc}".strip() if desc else str(lbl).strip()
            texts.append(combined)
    return [t for t in texts if t]


def check_quiz_card(card: dict[str, Any]) -> dict[str, Any]:
    """Deterministically audit a graded quiz/probe/check card for anti-leak invariants."""
    violations: list[dict[str, Any]] = []
    graded = bool(card.get("graded", True))
    prompt = str(card.get("prompt") or card.get("question") or "").strip()
    options = _extract_option_texts(card)

    if len(options) < 2:
        violations.append(
            {
                "rule": "insufficient_options",
                "message": f"Quiz card has {len(options)} options; expected at least 2.",
            }
        )

    # Check sycophancy in prompt
    for pat_name, pat in SYCOPHANCY_PATTERNS:
        m = pat.search(prompt)
        if m:
            violations.append(
                {
                    "rule": "zero_sycophancy",
                    "match": m.group(0),
                    "message": f"Sycophantic phrase in prompt ({pat_name}): {m.group(0)!r}",
                }
            )

    # Check IDK option presence
    has_idk_option = any(IDK_OPTION_RE.match(opt) for opt in options) or bool(card.get("allowIDontKnow") is True and has_explicit_idk(card))
    if graded and not has_idk_option:
        violations.append(
            {
                "rule": "missing_idk_option",
                "message": "Graded card is missing an explicit \"I don't know\" non-penalty option.",
            }
        )

    # Check justification / (Recommended) leaks in options & descriptions
    raw_opts = card.get("options") or card.get("choices") or []
    for idx, opt_text in enumerate(options):
        if IDK_OPTION_RE.match(opt_text):
            continue
        raw_entry = raw_opts[idx] if idx < len(raw_opts) else None
        if isinstance(raw_entry, dict) and str(raw_entry.get("description") or "").strip():
            violations.append(
                {
                    "rule": "option_description_leak",
                    "optionIndex": idx,
                    "option": opt_text,
                    "message": f"Option {idx + 1} includes a description field; graded options must be bare claims.",
                }
            )
        for leak_name, leak_re in OPTION_LEAK_PATTERNS:
            m = leak_re.search(opt_text)
            if m:
                violations.append(
                    {
                        "rule": "option_justification_leak",
                        "kind": leak_name,
                        "optionIndex": idx,
                        "match": m.group(0),
                        "option": opt_text,
                        "message": f"Option {idx + 1} leaks reasoning/answer cue ({leak_name}: {m.group(0)!r}).",
                    }
                )

    # Check option length-parity ratio (max/min <= 2.2 for multi-word substantive options)
    substantive = [opt for opt in options if not IDK_OPTION_RE.match(opt)]
    word_counts = [max(1, len(re.findall(r"[A-Za-z0-9_]+", opt))) for opt in substantive]
    length_ratio: float | None = None
    if len(word_counts) >= 2 and max(word_counts) >= 4:
        length_ratio = round(max(word_counts) / max(1, min(word_counts)), 2)
        if length_ratio > OPTION_PARITY_MAX_RATIO:
            violations.append(
                {
                    "rule": "option_length_parity",
                    "ratio": length_ratio,
                    "maxRatio": OPTION_PARITY_MAX_RATIO,
                    "wordCounts": word_counts,
                    "message": (
                        f"Option length imbalance: max/min word ratio is {length_ratio} "
                        f"(limit <= {OPTION_PARITY_MAX_RATIO}, counts={word_counts})."
                    ),
                }
            )

    return {
        "ok": True,
        "passed": len(violations) == 0,
        "optionCount": len(options),
        "hasIDontKnow": has_idk_option,
        "lengthRatio": length_ratio,
        "violationCount": len(violations),
        "violations": violations,
    }


def has_explicit_idk(card: dict[str, Any]) -> bool:
    """True only when a card explicitly declares an auto-injected IDK flag alongside `kind`."""
    return bool(card.get("idkInjectedByUI") is True)


def _extract_markdown_mcq(text: str) -> list[str]:
    """Detect inline Markdown `A) ... B) ... C) ...` quiz options outside fenced code blocks."""
    stripped_code = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
    found: list[tuple[str, str]] = []
    for line in stripped_code.splitlines():
        m = MD_MCQ_LINE_RE.match(line)
        if m:
            found.append((m.group(1).upper(), m.group(2).strip()))
    letters = [lt for lt, _ in found]
    if len(found) >= 3 and "A" in letters and "B" in letters:
        return [opt for _, opt in found]
    return []


# ---------------------------------------------------------------------------
# Turn Linter (`lint`)
# ---------------------------------------------------------------------------


def _count_active_demands(prose_without_code_math: str) -> dict[str, Any]:
    """Count learner-directed active demands in prose (outside code/math/URLs/MCQ lists)."""
    lines = prose_without_code_math.splitlines()
    prose_lines: list[str] = []
    for ln in lines:
        if MD_MCQ_LINE_RE.match(ln):
            continue
        # Skip markdown blockquote callouts (usually unconditional truth statements)
        if ln.lstrip().startswith(">"):
            continue
        prose_lines.append(ln)
    body = "\n".join(prose_lines)
    # Strip URLs so query strings (?foo=bar) never count as question marks
    body = re.sub(r"https?://\S+", "", body)
    body = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", body)
    # Strip quoted text so example questions inside quotes don't double-count
    body_no_quotes = re.sub(r'"[^"\n]{2,120}"', '""', body)

    question_marks = len(re.findall(r"\?", body_no_quotes))
    multi_problem_hit = bool(MULTI_PROBLEM_RE.search(body))
    numbered_questions = len(re.findall(r"(?:^|\n)\s*\d+[.)]\s+[^\n]*\?", body_no_quotes))
    demand_count = max(question_marks, numbered_questions, 2 if multi_problem_hit else 0)
    return {
        "questionMarks": question_marks,
        "multiProblemDetected": multi_problem_hit,
        "demandCount": demand_count,
    }


def lint_turn(
    text: str,
    *,
    mode: str = "teach",
    max_words: int | None = None,
) -> dict[str, Any]:
    """Deterministically lint a tutor turn against Syntopik's calm & non-spoiling invariants."""
    clean_mode = str(mode or "teach").strip().lower()
    if max_words is not None:
        word_cap = int(max_words)
    elif clean_mode == "side_doubt":
        word_cap = SIDE_DOUBT_WORD_LIMIT
    elif clean_mode == "hint":
        word_cap = HINT_WORD_LIMIT
    else:
        word_cap = PROSE_WORD_LIMIT

    violations: list[dict[str, Any]] = []
    counts = count_prose_words(text)

    # 1. Prose word ceiling & block caps
    if counts["proseWords"] > word_cap:
        violations.append(
            {
                "rule": "prose_word_ceiling",
                "proseWords": counts["proseWords"],
                "limit": word_cap,
                "message": f"Prose word count {counts['proseWords']} exceeds {clean_mode} ceiling ({word_cap} words).",
            }
        )
    for idx, line_cnt in enumerate(counts["codeBlockLines"]):
        if line_cnt > MAX_CODE_BLOCK_LINES:
            violations.append(
                {
                    "rule": "code_block_too_long",
                    "blockIndex": idx,
                    "lines": line_cnt,
                    "limit": MAX_CODE_BLOCK_LINES,
                    "message": f"Code block #{idx + 1} has {line_cnt} lines (limit <= {MAX_CODE_BLOCK_LINES}).",
                }
            )
    for idx, row_cnt in enumerate(counts["tableDataRows"]):
        if row_cnt > MAX_TABLE_DATA_ROWS:
            violations.append(
                {
                    "rule": "table_too_large",
                    "tableIndex": idx,
                    "dataRows": row_cnt,
                    "limit": MAX_TABLE_DATA_ROWS,
                    "message": f"Table #{idx + 1} has {row_cnt} data rows (limit <= {MAX_TABLE_DATA_ROWS}).",
                }
            )
    if counts["importantCallouts"] > MAX_IMPORTANT_CALLOUTS:
        violations.append(
            {
                "rule": "callout_limit",
                "count": counts["importantCallouts"],
                "limit": MAX_IMPORTANT_CALLOUTS,
                "message": f"Turn has {counts['importantCallouts']} [!IMPORTANT] callouts (limit <= {MAX_IMPORTANT_CALLOUTS}).",
            }
        )

    # 2. Zero-sycophancy banlist (checked outside fenced code blocks)
    prose_scan = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)
    seen_syc: set[str] = set()
    for pat_name, pat in SYCOPHANCY_PATTERNS:
        for m in pat.finditer(prose_scan):
            phrase = m.group(0).strip()
            key = f"{pat_name}:{phrase.lower()}"
            if key not in seen_syc:
                seen_syc.add(key)
                violations.append(
                    {
                        "rule": "zero_sycophancy",
                        "kind": pat_name,
                        "match": phrase,
                        "message": f"Sycophantic phrase forbidden in Syntopik voice: {phrase!r}",
                    }
                )

    # 3. Single active demand per turn
    demands = _count_active_demands(counts["proseWithoutExclusions"])
    if demands["demandCount"] > 1:
        violations.append(
            {
                "rule": "single_active_demand",
                "demandCount": demands["demandCount"],
                "questionMarks": demands["questionMarks"],
                "multiProblemDetected": demands["multiProblemDetected"],
                "message": (
                    f"Turn contains {demands['demandCount']} active demands "
                    "(hard ceiling: 1 active demand/question per turn)."
                ),
            }
        )

    # 4. Post-Conclusion Pause Law
    is_conclusion = clean_mode == "conclude" or bool(CONCLUSION_SIGNAL_RE.search(prose_scan))
    if is_conclusion:
        m_next = UNSOLICITED_NEXT_PROBLEM_RE.search(prose_scan)
        if m_next:
            violations.append(
                {
                    "rule": "post_conclusion_pause_law",
                    "match": m_next.group(0).strip(),
                    "message": (
                        "Post-Conclusion Pause Law violated: unsolicited next problem appended "
                        "in the same turn as a problem conclusion."
                    ),
                }
            )

    # 5. Hint non-spoiling check
    if clean_mode == "hint":
        m_spoil = HINT_SPOILER_RE.search(prose_scan)
        if m_spoil:
            violations.append(
                {
                    "rule": "hint_spoiler_leak",
                    "match": m_spoil.group(0),
                    "message": f"Scaffolding hint leaks direct answer/solution ({m_spoil.group(0)!r}).",
                }
            )

    # 6. Auto-audit embedded Markdown MCQ block if present
    md_options = _extract_markdown_mcq(text)
    card_audit: dict[str, Any] | None = None
    if md_options:
        card_audit = check_quiz_card({"prompt": "", "options": md_options, "graded": True})
        for v in card_audit["violations"]:
            violations.append(v)

    return {
        "ok": True,
        "passed": len(violations) == 0,
        "mode": clean_mode,
        "proseWords": counts["proseWords"],
        "wordLimit": word_cap,
        "demands": demands,
        "embeddedCardChecked": card_audit is not None,
        "violationCount": len(violations),
        "violations": violations,
    }


# ---------------------------------------------------------------------------
# Built-in 15-Scenario Multi-Domain Golden Rubric Suite (TutorBench-Style)
# 3 Rubrics x 5 Domains (CS, ML, Math, Physics, Chemistry) = 15 Scenarios
# Each scenario verifies BOTH a compliant golden specimen AND an adversarial
# violation specimen to prove high precision and recall.
# ---------------------------------------------------------------------------

GOLDEN_SCENARIOS: tuple[dict[str, Any], ...] = (
    # --- Rubric 1: Adaptive Explanation (5 domains) ---
    {
        "id": "tb-01-cs-adaptive",
        "rubric": "adaptive_explanation",
        "domain": "CS",
        "title": "Dijkstra greedy settling invariant (non-negative weights)",
        "kind": "turn",
        "mode": "teach",
        "good": (
            "Dijkstra pops the unsettled vertex $u$ with smallest tentative distance $d[u]$ and locks it permanently.\n\n"
            "> [!IMPORTANT]\n"
            "> Locking $d[u]$ is valid strictly when all edge weights satisfy $w(e) \\ge 0$; any future detour through an unsettled node cannot lower an already-minimal path.\n\n"
            "```python\n"
            "while pq:\n"
            "    dist_u, u = heapq.heappop(pq)\n"
            "    if dist_u > dist[u]:\n"
            "        continue\n"
            "    for v, w in adj[u]:\n"
            "        if dist_u + w < dist[v]:\n"
            "            dist[v] = dist_u + w\n"
            "            heapq.heappush(pq, (dist[v], v))\n"
            "```\n\n"
            "Suppose we shift every edge weight by $+W$ to make negative weights positive; does that preserve shortest paths?"
        ),
        "bad": (
            "Great question! You nailed it on thinking about shortest paths! "
            + " ".join(["word"] * 170)
            + " What happens if a weight is negative? And how does Bellman-Ford fix it?"
        ),
        "expectedBadRules": ["prose_word_ceiling", "zero_sycophancy", "single_active_demand"],
    },
    {
        "id": "tb-02-ml-adaptive",
        "rubric": "adaptive_explanation",
        "domain": "ML",
        "title": "Scaled dot-product attention variance normalization",
        "kind": "turn",
        "mode": "teach",
        "good": (
            "If query and key coordinates are independent with mean $0$ and variance $1$, their dot product sums $d_k$ unit-variance terms, so its variance scales as $d_k$.\n\n"
            "$$\\operatorname{Var}\\left(\\frac{q \\cdot k}{\\sqrt{d_k}}\\right) = \\frac{d_k}{d_k} = 1$$\n\n"
            "> [!IMPORTANT]\n"
            "> Dividing by $\\sqrt{d_k}$ keeps pre-softmax logits at unit variance, preventing softmax saturation and vanishing gradients as head dimension grows.\n\n"
            "If we omit $1/\\sqrt{d_k}$ and increase $d_k$ from $64$ to $4096$, what happens to the entropy of the attention weights?"
        ),
        "bad": (
            "Awesome! Let's check your understanding. "
            "Why do we divide by sqrt(d_k)? What happens to gradients when logits explode? "
            "Also, how does LayerNorm interact with this?"
        ),
        "expectedBadRules": ["zero_sycophancy", "single_active_demand"],
    },
    {
        "id": "tb-03-math-adaptive",
        "rubric": "adaptive_explanation",
        "domain": "Math",
        "title": "Rank-Nullity invariant for linear maps",
        "kind": "turn",
        "mode": "teach",
        "good": (
            "A linear map $T: \\mathbb{R}^n \\to \\mathbb{R}^m$ splits domain dimensions into directions collapsed to zero and directions preserved independently in the image.\n\n"
            "| Quantity | Subspace | Meaning |\n"
            "| :--- | :--- | :--- |\n"
            "| $\\operatorname{rank}(T)$ | $\\operatorname{im}(T) \\subseteq \\mathbb{R}^m$ | Surviving output dimensions |\n"
            "| $\\operatorname{nullity}(T)$ | $\\ker(T) \\subseteq \\mathbb{R}^n$ | Collapsed input dimensions |\n\n"
            "> [!IMPORTANT]\n"
            "> For every finite-dimensional linear map $T: V \\to W$, $\\dim(\\ker T) + \\dim(\\operatorname{im} T) = \\dim(V)$ unconditionally.\n\n"
            "If $T: \\mathbb{R}^5 \\to \\mathbb{R}^3$ is surjective, what must $\\dim(\\ker T)$ equal?"
        ),
        "bad": (
            "Brilliant! Here is a comparison table:\n\n"
            "| A | B |\n| :--- | :--- |\n| 1 | 2 |\n| 3 | 4 |\n| 5 | 6 |\n| 7 | 8 |\n| 9 | 10 |\n\n"
            "What is the nullity? What is the rank?"
        ),
        "expectedBadRules": ["zero_sycophancy", "table_too_large", "single_active_demand"],
    },
    {
        "id": "tb-04-phys-adaptive",
        "rubric": "adaptive_explanation",
        "domain": "Physics",
        "title": "SHM direction-sensitive phasor check card",
        "kind": "card",
        "good": {
            "prompt": "A particle in SHM of period T moves from x = +A/2 (+x direction) to x = -A/2 (+x direction). What is the shortest elapsed time?",
            "graded": True,
            "options": [
                "T/3",
                "T/2",
                "2T/3",
                "5T/6",
                "I don't know",
            ],
        },
        "bad": {
            "prompt": "Great question! Pick the shortest elapsed time:",
            "graded": True,
            "options": [
                "T/2",
                "5T/6 (Recommended) because on the phasor circle the particle passes +A and -A through 5pi/3 radians",
                "T/3",
            ],
        },
        "expectedBadRules": ["zero_sycophancy", "missing_idk_option", "option_justification_leak", "option_length_parity"],
    },
    {
        "id": "tb-05-chem-adaptive",
        "rubric": "adaptive_explanation",
        "domain": "Chemistry",
        "title": "SN2 backside attack vs SN1 carbocation discrimination card",
        "kind": "card",
        "good": {
            "prompt": "Which statement holds for a concerted bimolecular SN2 substitution at a chiral carbon?",
            "graded": True,
            "options": [
                "Rate depends on substrate and nucleophile with inversion",
                "Rate depends only on substrate with complete racemization",
                "Rate depends only on nucleophile with full retention",
                "Rate depends on solvent polarity with carbocation shift",
                "I don't know",
            ],
        },
        "bad": {
            "prompt": "Which statement holds for SN2?",
            "graded": True,
            "options": [
                "Inversion",
                "Rate depends on both substrate and nucleophile concentration since it proceeds in one concerted backside-attack transition state",
                "Racemization",
                "I don't know",
            ],
        },
        "expectedBadRules": ["option_justification_leak", "option_length_parity"],
    },
    # --- Rubric 2: Actionable Non-Spoiling Error Feedback (5 domains) ---
    {
        "id": "tb-06-cs-feedback",
        "rubric": "error_feedback",
        "domain": "CS",
        "title": "Binary search loop bound error & Post-Conclusion Pause Law",
        "kind": "turn",
        "mode": "conclude",
        "good": (
            "Your trace fixed line 4 (`lo = mid + 1`) and closed the infinite-loop edge case on a 2-element array.\n\n"
            "Problem complete — invariant locked in: shrink the half-open or closed interval strictly on every branch so `hi - lo` decreases monotonically.\n\n"
            "Take a moment to note the invariant; say when you want to continue."
        ),
        "bad": (
            "Well done! Problem complete — canonical solution verified.\n\n"
            "Next problem: implement lower_bound on a rotated sorted array with duplicates."
        ),
        "expectedBadRules": ["zero_sycophancy", "post_conclusion_pause_law"],
    },
    {
        "id": "tb-07-ml-feedback",
        "rubric": "error_feedback",
        "domain": "ML",
        "title": "Train/test data leakage from pre-split standardization",
        "kind": "turn",
        "mode": "practice",
        "good": (
            "Your accuracy code runs cleanly, but line 2 leaks test information: `scaler.fit_transform(X)` computes the mean and variance across both training and test rows before splitting.\n\n"
            "Which single statistic from the test fold silently entered your training features, and how should the fit/transform calls change?"
        ),
        "bad": (
            "Good job trying! That's a really insightful attempt! "
            "Wait, did you fit on all data? Why does that leak variance? And what if we use k-fold cross-validation?"
        ),
        "expectedBadRules": ["zero_sycophancy", "single_active_demand"],
    },
    {
        "id": "tb-08-math-feedback",
        "rubric": "error_feedback",
        "domain": "Math",
        "title": "L'Hopital precondition check on non-indeterminate limit",
        "kind": "turn",
        "mode": "practice",
        "good": (
            "Line 2 applies L'Hopital's rule prematurely.\n\n"
            "At $x = 0$, the numerator $x^2 + 1$ approaches $1$, not $0$, while the denominator approaches $0$, so the ratio is not of indeterminate form $0/0$ or $\\infty/\\infty$.\n\n"
            "Re-evaluate the limit directly from the signs of the numerator and denominator near $x = 0$."
        ),
        "bad": (
            "Spot on effort! Wait, let's check two things. "
            "Problem 1: what is the numerator at 0? "
            "Problem 2: compute the derivative anyway?"
        ),
        "expectedBadRules": ["zero_sycophancy", "single_active_demand"],
    },
    {
        "id": "tb-09-phys-feedback",
        "rubric": "error_feedback",
        "domain": "Physics",
        "title": "Kinetic energy misused in completely inelastic collision",
        "kind": "turn",
        "mode": "practice",
        "good": (
            "Your momentum equation in line 1 is exact, but line 3 equates initial and final kinetic energy across a sticking collision.\n\n"
            "Because the blocks deform and stick together, mechanical energy dissipates into heat while net external impulse remains zero.\n\n"
            "Drop line 3 and compute the post-collision velocity from your conserved quantity alone."
        ),
        "bad": (
            "Great job! You've got this! Solution verified.\n\n"
            "Here's the next problem: find the maximum compression of a spring attached to block B."
        ),
        "expectedBadRules": ["zero_sycophancy", "post_conclusion_pause_law"],
    },
    {
        "id": "tb-10-chem-feedback",
        "rubric": "error_feedback",
        "domain": "Chemistry",
        "title": "Buffer stoichiometry before Henderson-Hasselbalch",
        "kind": "turn",
        "mode": "practice",
        "good": (
            "Line 2 plugs the initial acetic-acid moles directly into Henderson-Hasselbalch without first reacting the added strong base.\n\n"
            "Added $\\text{OH}^-$ converts weak acid $\\text{HA}$ quantitatively into conjugate base $\\text{A}^-$ before equilibrium settles.\n\n"
            "Update the post-neutralization moles of $\\text{HA}$ and $\\text{A}^-$ first, then recompute the ratio."
        ),
        "bad": (
            "Excellent! What a great observation! "
            "What are the new moles of HA? What are the new moles of A-? And does total volume cancel out?"
        ),
        "expectedBadRules": ["zero_sycophancy", "single_active_demand"],
    },
    # --- Rubric 3: Scaffolding Hinting (5 domains, <= 35w, non-spoiling) ---
    {
        "id": "tb-11-cs-hint",
        "rubric": "scaffolding_hinting",
        "domain": "CS",
        "title": "R1 non-spoiling hint for Coin Change DP state",
        "kind": "turn",
        "mode": "hint",
        "good": (
            "Define `dp[x]` as the fewest coins to reach exact sum `x`. "
            "If the last coin picked has value `c`, which smaller subproblem must already be optimal?"
        ),
        "bad": (
            "Awesome! The final answer is `dp[x] = min(dp[x], 1 + dp[x - c])` for every coin `c` up to `x`, "
            "initialized with infinity and `dp[0] = 0`, returning `-1` when unreachable."
        ),
        "expectedBadRules": ["zero_sycophancy", "hint_spoiler_leak"],
    },
    {
        "id": "tb-12-ml-hint",
        "rubric": "scaffolding_hinting",
        "domain": "ML",
        "title": "R1 non-spoiling hint for sigmoid gradient saturation",
        "kind": "turn",
        "mode": "hint",
        "good": (
            "Compute $\\sigma'(z) = \\sigma(z)(1 - \\sigma(z))$ at $z = 0$ versus $z = 6$. "
            "What happens when you multiply $20$ such factors along backprop?"
        ),
        "bad": (
            "You nailed it! The answer is that sigmoid derivative maxes at 0.25 so multiplying 20 layers shrinks gradients exponentially toward zero and you should just write down ReLU instead to fix every single layer in the deep network."
        ),
        "expectedBadRules": ["prose_word_ceiling", "zero_sycophancy", "hint_spoiler_leak"],
    },
    {
        "id": "tb-13-math-hint",
        "rubric": "scaffolding_hinting",
        "domain": "Math",
        "title": "R2 non-spoiling hint for epsilon-delta continuity of x^2",
        "kind": "turn",
        "mode": "hint",
        "good": (
            "Factor $|x^2 - a^2| = |x - a|\\,|x + a|$. "
            "If you first cap $\\delta \\le 1$, what upper bound does $|x + a|$ satisfy?"
        ),
        "bad": (
            "Perfect! Just write down delta = min(1, epsilon / (2*|a| + 1)) because that is the exact proof. "
            "Did you cap delta first? And why does triangle inequality give 2|a| + 1?"
        ),
        "expectedBadRules": ["zero_sycophancy", "hint_spoiler_leak", "single_active_demand"],
    },
    {
        "id": "tb-14-phys-hint",
        "rubric": "scaffolding_hinting",
        "domain": "Physics",
        "title": "R1 non-spoiling hint for pure rolling static friction direction",
        "kind": "turn",
        "mode": "hint",
        "good": (
            "Pretend the incline is frictionless first. "
            "Which way would the bottom contact point slip relative to the ramp, and which way must static friction oppose that tendency?"
        ),
        "bad": (
            "Great job! The final answer is static friction points up the incline with magnitude m g sin(theta) / 3 for a solid cylinder rolling without slipping down a rough plane."
        ),
        "expectedBadRules": ["zero_sycophancy", "hint_spoiler_leak"],
    },
    {
        "id": "tb-15-chem-hint",
        "rubric": "scaffolding_hinting",
        "domain": "Chemistry",
        "title": "R1 non-spoiling hint for Nernst reaction quotient Q",
        "kind": "turn",
        "mode": "hint",
        "good": (
            "Write the balanced net ionic cell reaction first, then recall the thermodynamic activity of pure solid electrodes. "
            "Which species remain inside $Q$?"
        ),
        "bad": (
            "Fantastic! The solution is Q = [Zn2+] / [Cu2+] because pure solids Zn(s) and Cu(s) have unit activity and drop out of the Nernst equation completely in every galvanic cell calculation."
        ),
        "expectedBadRules": ["zero_sycophancy", "hint_spoiler_leak"],
    },
)


def run_benchmark() -> dict[str, Any]:
    """Execute the 15-scenario multi-domain TutorBench-style golden rubric suite."""
    results: list[dict[str, Any]] = []
    passed_count = 0
    by_rubric: dict[str, dict[str, int]] = {}
    by_domain: dict[str, dict[str, int]] = {}

    for sc in GOLDEN_SCENARIOS:
        if sc["kind"] == "card":
            good_res = check_quiz_card(sc["good"])
            bad_res = check_quiz_card(sc["bad"])
        else:
            mode = sc.get("mode", "teach")
            good_res = lint_turn(sc["good"], mode=mode)
            bad_res = lint_turn(sc["bad"], mode=mode)

        bad_rules_found = {v["rule"] for v in bad_res["violations"]}
        expected_rules = list(sc["expectedBadRules"])
        missing_rules = [r for r in expected_rules if r not in bad_rules_found]

        scenario_passed = bool(good_res["passed"]) and (not bad_res["passed"]) and (len(missing_rules) == 0)
        if scenario_passed:
            passed_count += 1

        rb = by_rubric.setdefault(sc["rubric"], {"passed": 0, "total": 0})
        rb["total"] += 1
        if scenario_passed:
            rb["passed"] += 1

        dm = by_domain.setdefault(sc["domain"], {"passed": 0, "total": 0})
        dm["total"] += 1
        if scenario_passed:
            dm["passed"] += 1

        results.append(
            {
                "id": sc["id"],
                "rubric": sc["rubric"],
                "domain": sc["domain"],
                "title": sc["title"],
                "kind": sc["kind"],
                "passed": scenario_passed,
                "goodPassed": good_res["passed"],
                "goodViolations": good_res["violations"],
                "badCaught": not bad_res["passed"],
                "expectedBadRules": expected_rules,
                "foundBadRules": sorted(bad_rules_found),
                "missingBadRules": missing_rules,
            }
        )

    total = len(GOLDEN_SCENARIOS)
    return {
        "ok": passed_count == total,
        "passed": passed_count,
        "total": total,
        "passRate": round(passed_count / total, 4) if total else 0.0,
        "byRubric": by_rubric,
        "byDomain": by_domain,
        "scenarios": results,
    }


# ---------------------------------------------------------------------------
# CLI Parser & Entrypoint
# ---------------------------------------------------------------------------


def _read_input_text(file_arg: str | None) -> str:
    if not file_arg or file_arg == "-":
        return sys.stdin.read()
    path = Path(file_arg).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Input file not found: {path}")
    return path.read_text(encoding="utf-8", errors="replace")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pedagogy_eval.py",
        description="Syntopik v2.0 — Zero-LLM TutorBench-Style Pedagogical Linter & Golden Eval Pack",
    )
    parser.add_argument("--json", action="store_true", default=False, help="Emit structured JSON output")
    sub = parser.add_subparsers(dest="cmd", required=True)

    # lint
    p_lint = sub.add_parser("lint", help="Lint a tutor turn from a file or stdin against Syntopik invariants")
    p_lint.add_argument("file", nargs="?", default="-", help="Markdown/text file path or '-' for stdin")
    p_lint.add_argument(
        "--mode",
        default="teach",
        choices=("teach", "practice", "hint", "conclude", "side_doubt"),
        help="Turn mode controlling ceiling & pause/hint rules (default: teach)",
    )
    p_lint.add_argument("--max-words", type=int, default=None, help="Explicit prose word ceiling override")
    p_lint.add_argument("--json", action="store_true", default=argparse.SUPPRESS)

    # check-card
    p_card = sub.add_parser("check-card", help="Audit a graded quiz/probe/check JSON card for zero-leak integrity")
    p_card.add_argument("json_file", help="Path to quiz card JSON file or '-' for stdin")
    p_card.add_argument("--json", action="store_true", default=argparse.SUPPRESS)

    # bench
    p_bench = sub.add_parser("bench", help="Run the built-in 15-scenario multi-domain TutorBench golden eval suite")
    p_bench.add_argument("--verbose", action="store_true", help="Show per-scenario breakdown in text mode")
    p_bench.add_argument("--json", action="store_true", default=argparse.SUPPRESS)

    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    as_json = bool(getattr(args, "json", False))

    try:
        if args.cmd == "lint":
            text = _read_input_text(args.file)
            res = lint_turn(text, mode=args.mode, max_words=args.max_words)
            if as_json:
                print(json.dumps(res, indent=2, ensure_ascii=False))
            else:
                status = "PASS" if res["passed"] else "FAIL"
                print(
                    f"[{status}] mode={res['mode']} prose={res['proseWords']}/{res['wordLimit']}w "
                    f"demands={res['demands']['demandCount']} violations={res['violationCount']}"
                )
                for v in res["violations"]:
                    print(f"  - [{v['rule']}] {v['message']}")
            if not res["passed"]:
                raise SystemExit(1)

        elif args.cmd == "check-card":
            raw = _read_input_text(args.json_file)
            card = json.loads(raw)
            if not isinstance(card, dict):
                raise ValueError("Quiz card JSON must be an object.")
            res = check_quiz_card(card)
            if as_json:
                print(json.dumps(res, indent=2, ensure_ascii=False))
            else:
                status = "PASS" if res["passed"] else "FAIL"
                print(
                    f"[{status}] options={res['optionCount']} idk={res['hasIDontKnow']} "
                    f"ratio={res['lengthRatio']} violations={res['violationCount']}"
                )
                for v in res["violations"]:
                    print(f"  - [{v['rule']}] {v['message']}")
            if not res["passed"]:
                raise SystemExit(1)

        elif args.cmd == "bench":
            res = run_benchmark()
            if as_json:
                print(json.dumps(res, indent=2, ensure_ascii=False))
            else:
                status = "PASS" if res["ok"] else "FAIL"
                print(f"[{status}] Syntopik TutorBench Golden Suite: {res['passed']}/{res['total']} scenarios passed")
                for rb, st in res["byRubric"].items():
                    print(f"  rubric/{rb}: {st['passed']}/{st['total']}")
                for dm, st in res["byDomain"].items():
                    print(f"  domain/{dm}: {st['passed']}/{st['total']}")
                if args.verbose or not res["ok"]:
                    for sc in res["scenarios"]:
                        mark = "PASS" if sc["passed"] else "FAIL"
                        print(f"  [{mark}] {sc['id']} ({sc['domain']} / {sc['rubric']}): {sc['title']}")
                        if not sc["passed"]:
                            print(f"       goodViolations={sc['goodViolations']} missingBadRules={sc['missingBadRules']}")
            if not res["ok"]:
                raise SystemExit(1)
    except (ValueError, FileNotFoundError, json.JSONDecodeError) as exc:
        if as_json:
            print(json.dumps({"ok": False, "error": str(exc)}, indent=2, ensure_ascii=False))
        else:
            print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
