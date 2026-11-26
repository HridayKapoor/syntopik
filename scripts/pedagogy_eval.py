#!/usr/bin/env python3
"""Syntopik v3.0 — Zero-LLM TutorBench-Style Pedagogical Linter & 40-Scenario Golden Eval Pack.

Pure Python 3.12+ standard library deterministic auditor verifying Syntopik's
calm, zero-sycophancy, zero-leak, and v3.0 teacher-replacement pedagogical
invariants on tutor turns, graded quiz/Parsons/faded cards, and a built-in
40-scenario multi-domain benchmark suite covering:
  - Adaptive Explanation, Error Feedback, Scaffolding Hinting (v2.0 core)
  - VARK-Myth Refusal, Kalyuga Expertise-Reversal Compliance, Gaming/Rushing
    Intervention, Fatigued-Learner Calm Wrap-Up, Causal Root-Cause Ancestor
    Probing, T3/T4 Anti-Inflation, and Zero-Leak Parsons/Faded/Quiz Cards (v3.0).

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
FATIGUED_WORD_LIMIT = 110
MAX_CODE_BLOCK_LINES = 18
MAX_TABLE_DATA_ROWS = 4
MAX_IMPORTANT_CALLOUTS = 1
OPTION_PARITY_MAX_RATIO = 2.2

# --- v3.0 Teacher-Replacement Invariant Regex Patterns ---

VARK_NEUROMYTH_RE = re.compile(
    r"\b(?:"
    r"(?:since|because|as)\s+you(?:'re|\s+are)\s+(?:a|an)\s+(?:visual|auditory|kinesthetic|reading/writing)\s+learner"
    r"|your\s+(?:visual|auditory|kinesthetic|vark)\s+learning\s+style"
    r"|tailored\s+to\s+(?:your\s+)?(?:visual|auditory|kinesthetic)\s+learners?"
    r"|match(?:es|ing)?\s+your\s+(?:visual|auditory|kinesthetic)\s+(?:learning\s+)?style"
    r"|switch(?:ing)?\s+to\s+(?:visual|auditory|kinesthetic)[-\s]learner\s+mode"
    r"|take\s+(?:a|this)\s+vark\s+(?:quiz|test|assessment)\s+to\s+find\s+your\s+style"
    r")\b",
    re.IGNORECASE,
)

GUILT_STREAK_RE = re.compile(
    r"\b(?:"
    r"(?:broke|broken|lose|losing|lost)\s+your\s+(?:study\s+)?streak"
    r"|don['’]?t\s+(?:break|lose)\s+your\s+streak"
    r"|you\s+fell\s+behind\s+again"
    r"|should\s+have\s+studied\s+(?:every\s+day|yesterday|more)"
    r"|disappointed\s+(?:that\s+)?you\s+missed"
    r"|no\s+excuses\s+for\s+missing"
    r")\b",
    re.IGNORECASE,
)

WORKED_EXAMPLE_RE = re.compile(
    r"(?:"
    r"\bhere(?:'s|\s+is)\s+(?:a\s+)?(?:complete\s+|full\s+)?worked\s+(?:micro[-\s]?)?example\b"
    r"|\bworked\s+example\s*:"
    r"|\blet\s+me\s+walk\s+you\s+step[-\s]by[-\s]step\s+through\s+(?:a|this)\s+solved\b"
    r"|\bstep\s+1\s*:.*?\bstep\s+2\s*:.*?\bstep\s+3\s*:"
    r")",
    re.IGNORECASE | re.DOTALL,
)

COLD_NOVICE_DUMP_RE = re.compile(
    r"\b(?:"
    r"figure\s+it\s+out\s+from\s+scratch"
    r"|no\s+examples?\s+or\s+hints?"
    r"|derive\s+the\s+general\s+(?:theorem|proof|formula)\s+cold"
    r"|solve\s+this\s+cold\s+with\s+zero\s+scaffolding"
    r")\b",
    re.IGNORECASE,
)

GAMING_FREE_HINT_RE = re.compile(
    r"\b(?:"
    r"here(?:'s|\s+is)\s+(?:another|the\s+next|a\s+free)\s+hint"
    r"|hint\s*:"
    r"|just\s+try\s+option\s+[a-d]"
    r"|let\s+me\s+give\s+you\s+another\s+clue"
    r"|skip\s+straight\s+to\s+the\s+next\s+hint"
    r")\b",
    re.IGNORECASE,
)

REASONING_TRACE_DEMAND_RE = re.compile(
    r"\b(?:"
    r"(?:1|one)[-\s]line\s+(?:reason|reasoning|trace|invariant|justification|explanation)"
    r"|explain\s+(?:in\s+one\s+line\s+)?why"
    r"|state\s+your\s+(?:reason|invariant|rule|step)"
    r"|write\s+(?:one\s+line|your\s+reasoning|the\s+reason)"
    r"|before\s+(?:grading|hints|we\s+check|unlocking)"
    r")\b",
    re.IGNORECASE,
)

FATIGUE_PUSH_RE = re.compile(
    r"\b(?:"
    r"push\s+through\s+(?:the\s+fatigue|a\s+few\s+more|another)"
    r"|tackle\s+(?:\d+|three|four|five)\s+more\s+(?:hard\s+)?problems?"
    r"|start\s+a\s+brand[-\s]new\s+(?:derivation|chapter|proof)"
    r"|don['’]?t\s+stop\s+now"
    r"|keep\s+going\s+for\s+another\s+\d+\s+minutes"
    r")\b",
    re.IGNORECASE,
)

LEAF_RELECTURE_RE = re.compile(
    r"\b(?:"
    r"let\s+me\s+re[-\s]?explain\s+the\s+(?:entire|whole|full)"
    r"|let(?:'s|\s+us)\s+re[-\s]?read\s+the\s+(?:entire|whole)\s+(?:chapter|derivation|algorithm|formula)"
    r"|starting\s+from\s+scratch\s+on\s+the\s+entire"
    r")\b",
    re.IGNORECASE,
)

T2_MASTERY_INFLATION_RE = re.compile(
    r"\b(?:"
    r"marking\s+(?:this|it|the\s+\w+)\s+(?:as\s+)?(?:solid|fully\s+mastered)\s+(?:from|based\s+on)\s+(?:that|this|one)\s+mcq"
    r"|one\s+multiple[-\s]choice\s+(?:hit|answer)\s+(?:proves|confirms)\s+(?:full|complete)\s+mastery"
    r"|fully\s+mastered\s*[—–-]\s*no\s+(?:transfer|derivation|further)\s+(?:check|verification)\s+needed"
    r"|bottleneck\s+(?:is\s+)?(?:fully\s+)?mastered\s+after\s+(?:t2|recognition|mcq)"
    r")\b",
    re.IGNORECASE,
)

PREMATURE_SOLID_CLAIM_RE = re.compile(
    r"\b(?:"
    r"marking\s+(?:this|it)\s+solid"
    r"|fully\s+mastered"
    r"|mastery\s+confirmed"
    r"|no\s+further\s+verification\s+needed"
    r")\b",
    re.IGNORECASE,
)

PARSONS_ORDER_LEAK_RE = re.compile(
    r"(?:"
    r"^\s*(?:step\s*\d+\s*[:.)-]|\d+\s*[.)]\s+)"
    r"|(?:#|//)\s*(?:line\s*\d+|step\s*\d+|correct|wrong|distractor)\b"
    r"|\(\s*(?:first|second|third|last|correct|distractor|wrong)\s*\)"
    r"|\bDISTRACTOR\s*:"
    r")",
    re.IGNORECASE,
)

FADED_BLANK_LEAK_RE = re.compile(
    r"(?:"
    r"_{2,}\s*\(\s*(?:answer|fill|hint|put)\s*:"
    r"|/\*\s*(?:answer|fill)\s*:"
    r"|\[\s*blank\s*=\s*[^\]]+\]"
    r")",
    re.IGNORECASE,
)

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
    """Deterministically audit a graded quiz/probe/check/Parsons/faded card for zero-leak integrity."""
    violations: list[dict[str, Any]] = []
    graded = bool(card.get("graded", True))
    card_mode = str(card.get("mode") or "standard").strip().lower()
    prompt = str(card.get("prompt") or card.get("question") or "").strip()
    options = _extract_option_texts(card)

    # Check sycophancy & VARK myth in prompt
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
    m_vark = VARK_NEUROMYTH_RE.search(prompt)
    if m_vark:
        violations.append(
            {
                "rule": "vark_neuromyth",
                "match": m_vark.group(0),
                "message": f"VARK learning-style neuromyth disallowed: {m_vark.group(0)!r}",
            }
        )

    # Parsons & Faded practice card zero-leak checks
    if card_mode == "parsons" or isinstance(card.get("blocks"), list):
        raw_blocks = card.get("blocks") or []
        if len(raw_blocks) < 2:
            violations.append(
                {
                    "rule": "insufficient_parsons_blocks",
                    "message": f"Parsons card has {len(raw_blocks)} blocks; expected at least 2.",
                }
            )
        for idx, blk in enumerate(raw_blocks):
            b_txt = str(blk.get("code") or blk.get("text") or "" if isinstance(blk, dict) else blk)
            m_leak = PARSONS_ORDER_LEAK_RE.search(b_txt)
            if m_leak:
                violations.append(
                    {
                        "rule": "parsons_order_or_distractor_leak",
                        "blockIndex": idx,
                        "match": m_leak.group(0).strip(),
                        "message": f"Parsons block #{idx + 1} leaks ordering or distractor status ({m_leak.group(0).strip()!r}).",
                    }
                )
        sealed_sol = str(card.get("modelSolution") or "").strip()
        if sealed_sol and len(sealed_sol) >= 12 and sealed_sol in prompt:
            violations.append(
                {
                    "rule": "model_solution_leak",
                    "message": "Parsons prompt leaks sealed modelSolution verbatim.",
                }
            )
        return {
            "ok": True,
            "passed": len(violations) == 0,
            "mode": "parsons",
            "blockCount": len(raw_blocks),
            "optionCount": len(options),
            "hasIDontKnow": True,
            "lengthRatio": None,
            "violationCount": len(violations),
            "violations": violations,
        }

    if card_mode == "faded" or isinstance(card.get("template"), str):
        template_str = str(card.get("template") or "")
        m_faded = FADED_BLANK_LEAK_RE.search(template_str) or FADED_BLANK_LEAK_RE.search(prompt)
        if m_faded:
            violations.append(
                {
                    "rule": "faded_blank_answer_leak",
                    "match": m_faded.group(0).strip(),
                    "message": f"Faded card leaks blank answer inside template/prompt ({m_faded.group(0).strip()!r}).",
                }
            )
        sealed_sol = str(card.get("modelSolution") or "").strip()
        if sealed_sol and len(sealed_sol) >= 8 and (sealed_sol in template_str or sealed_sol in prompt):
            violations.append(
                {
                    "rule": "model_solution_leak",
                    "message": "Faded card leaks sealed modelSolution inside visible template/prompt.",
                }
            )
        return {
            "ok": True,
            "passed": len(violations) == 0,
            "mode": "faded",
            "optionCount": len(options),
            "hasIDontKnow": True,
            "lengthRatio": None,
            "violationCount": len(violations),
            "violations": violations,
        }

    if len(options) < 2:
        violations.append(
            {
                "rule": "insufficient_options",
                "message": f"Quiz card has {len(options)} options; expected at least 2.",
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
    learner_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Deterministically lint a tutor turn against Syntopik v3.0 calm, non-spoiling & adaptive invariants."""
    clean_mode = str(mode or "teach").strip().lower()
    state = dict(learner_state or {})
    affect = str(state.get("affect") or "").strip().lower()
    expertise = str(state.get("expertise") or "").strip().lower()

    if max_words is not None:
        word_cap = int(max_words)
    elif clean_mode == "side_doubt":
        word_cap = SIDE_DOUBT_WORD_LIMIT
    elif clean_mode == "hint":
        word_cap = HINT_WORD_LIMIT
    elif affect == "fatigued":
        word_cap = FATIGUED_WORD_LIMIT
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

    # 6. v3.0 Teacher-Replacement Invariants (VARK refusal, Guilt/Streaks, Expertise Reversal,
    #    Gaming/Rushing trace gate, Fatigue calm wrap-up, Causal root-cause probing, T3/T4 anti-inflation)
    m_vark = VARK_NEUROMYTH_RE.search(prose_scan)
    if m_vark:
        violations.append(
            {
                "rule": "vark_neuromyth",
                "match": m_vark.group(0).strip(),
                "message": f"Debunked VARK learning-style label forbidden: {m_vark.group(0).strip()!r}",
            }
        )

    m_guilt = GUILT_STREAK_RE.search(prose_scan)
    if m_guilt:
        violations.append(
            {
                "rule": "guilt_or_streak_shaming",
                "match": m_guilt.group(0).strip(),
                "message": f"Guilt-tripping or streak-shaming violates calm pedagogy: {m_guilt.group(0).strip()!r}",
            }
        )

    m_t2_inf = T2_MASTERY_INFLATION_RE.search(prose_scan)
    if m_t2_inf:
        violations.append(
            {
                "rule": "t2_mastery_inflation",
                "match": m_t2_inf.group(0).strip(),
                "message": f"T2 recognition alone cannot prove bottleneck mastery: {m_t2_inf.group(0).strip()!r}",
            }
        )

    # Expertise-Reversal check (Kalyuga)
    if expertise == "advanced" or affect == "bored_underchallenged" or state.get("allowWorkedExample") is False:
        m_we = WORKED_EXAMPLE_RE.search(prose_scan)
        if m_we:
            violations.append(
                {
                    "rule": "expertise_reversal_violation",
                    "match": m_we.group(0).strip()[:80],
                    "message": "Expertise-Reversal violation: redundant worked example served to advanced/underchallenged learner.",
                }
            )
    elif expertise == "novice" or (affect == "overloaded_frustrated" and state.get("requireWorkedExample") is True):
        m_cold = COLD_NOVICE_DUMP_RE.search(prose_scan)
        if m_cold:
            violations.append(
                {
                    "rule": "expertise_reversal_violation",
                    "match": m_cold.group(0).strip(),
                    "message": "Novice overload violation: cold unguided problem dumped without a concrete worked micro-example.",
                }
            )

    # Gaming / rushing intervention check
    if affect == "gaming_rushing":
        m_free = GAMING_FREE_HINT_RE.search(prose_scan)
        if m_free:
            violations.append(
                {
                    "rule": "gaming_rushing_violation",
                    "match": m_free.group(0).strip(),
                    "message": "Gaming/rushing violation: handed out hint/clue instead of demanding a 1-line reasoning trace.",
                }
            )
        elif not REASONING_TRACE_DEMAND_RE.search(prose_scan):
            violations.append(
                {
                    "rule": "gaming_rushing_violation",
                    "message": "Gaming/rushing violation: turn must explicitly require a 1-line reasoning trace before grading/hints.",
                }
            )

    # Fatigued learner graceful wrap-up check
    if affect == "fatigued":
        m_push = FATIGUE_PUSH_RE.search(prose_scan)
        if m_push:
            violations.append(
                {
                    "rule": "fatigue_wrapup_violation",
                    "match": m_push.group(0).strip(),
                    "message": f"Fatigue violation: pushing heavy new work on a fatigued learner ({m_push.group(0).strip()!r}).",
                }
            )

    # Causal Noisy-OR root-cause ancestor probe check
    if state.get("requireRootProbe") is True:
        m_relect = LEAF_RELECTURE_RE.search(prose_scan)
        if m_relect:
            violations.append(
                {
                    "rule": "causal_root_probe_violation",
                    "match": m_relect.group(0).strip(),
                    "message": "Causal root-cause violation: re-lecturing symptom leaf instead of probing high-ΔH suspect ancestor.",
                }
            )
        anc_keywords = [str(k).lower() for k in (state.get("suspectAncestorKeywords") or []) if str(k).strip()]
        if anc_keywords and not any(k in prose_scan.lower() for k in anc_keywords):
            violations.append(
                {
                    "rule": "causal_root_probe_violation",
                    "message": f"Causal root-cause violation: turn fails to probe suspect ancestor ({anc_keywords}).",
                }
            )

    # T3/T4 anti-inflation gate for bottleneck nodes stuck at T1/T2
    if state.get("isBottleneck") is True and str(state.get("evidenceTier") or "").upper() in ("T1", "T2"):
        m_prem = PREMATURE_SOLID_CLAIM_RE.search(prose_scan)
        if m_prem and not m_t2_inf:
            violations.append(
                {
                    "rule": "t2_mastery_inflation",
                    "match": m_prem.group(0).strip(),
                    "message": f"Anti-inflation violation: cannot mark bottleneck mastered at {state.get('evidenceTier')} ({m_prem.group(0).strip()!r}).",
                }
            )

    # 7. Auto-audit embedded Markdown MCQ block if present
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
    # --- Rubric 4: VARK-Myth Refusal (4 scenarios: Physics, CS, ML, Chemistry) ---
    {
        "id": "tb-16-phys-vark",
        "rubric": "vark_myth_refusal",
        "domain": "Physics",
        "title": "Refuse 'visual learner' label; choose phasor geometry for content structure",
        "kind": "turn",
        "mode": "teach",
        "good": (
            "We do not sort people into visual or auditory learner types—empirical studies show matching instruction to self-labeled styles does not raise retention.\n\n"
            "Instead, we match the representation to the concept: SHM phase intervals live on a circle, so projecting uniform circular motion onto the $x$-axis exposes direction-sensitive sign changes immediately.\n\n"
            "On the phasor circle, which quadrant corresponds to $x = +A/2$ moving toward $+A$?"
        ),
        "bad": (
            "Since you're a visual learner, I will switch to visual-learner mode and only draw pictures tailored to your visual learning style. "
            "Which quadrant is it? And what is the angular velocity?"
        ),
        "expectedBadRules": ["vark_neuromyth", "single_active_demand"],
    },
    {
        "id": "tb-17-cs-vark",
        "rubric": "vark_myth_refusal",
        "domain": "CS",
        "title": "Refuse 'kinesthetic learner' label; use pointer trace for linked-list reversal",
        "kind": "turn",
        "mode": "teach",
        "good": (
            "Skipping learning-style labels here—linked-list reversal fails on aliasing bugs, so tracing pointer mutations step by step fits the problem structure.\n\n"
            "```python\n"
            "prev, cur = None, head\n"
            "while cur:\n"
            "    nxt = cur.next\n"
            "    cur.next = prev\n"
            "    prev, cur = cur, nxt\n"
            "```\n\n"
            "If you omit saving `nxt` before overwriting `cur.next = prev`, which nodes become unreachable?"
        ),
        "bad": (
            "Great question! Because you are a kinesthetic learner, this matches your kinesthetic learning style perfectly. "
            "What happens if we drop `nxt`?"
        ),
        "expectedBadRules": ["zero_sycophancy", "vark_neuromyth"],
    },
    {
        "id": "tb-18-ml-vark",
        "rubric": "vark_myth_refusal",
        "domain": "ML",
        "title": "Refuse 'auditory learner' label; use shape table for attention dimensions",
        "kind": "turn",
        "mode": "teach",
        "good": (
            "Representation follows the math rather than an auditory-learner label: multi-head attention errors are tensor-shape mismatches, so a compact shape table pins down every projection.\n\n"
            "| Tensor | Shape | Meaning |\n"
            "| :--- | :--- | :--- |\n"
            "| $Q, K, V$ | $(B, H, T, d_k)$ | Split head projections |\n"
            "| $QK^\\top$ | $(B, H, T, T)$ | Pairwise token scores |\n\n"
            "Why does keeping $H \\cdot d_k = d_{\\text{model}}$ preserve the parameter count of $W_Q$?"
        ),
        "bad": (
            "As an auditory learner, I will talk through everything tailored to auditory learners without tables. "
            "First, take a VARK quiz to find your style."
        ),
        "expectedBadRules": ["vark_neuromyth"],
    },
    {
        "id": "tb-19-chem-vark",
        "rubric": "vark_myth_refusal",
        "domain": "Chemistry",
        "title": "Refuse VARK card framing; use Newman projection for butane steric clash",
        "kind": "card",
        "good": {
            "prompt": "In a Newman projection down the C2-C3 bond of n-butane, which conformer minimizes dihedral steric repulsion?",
            "graded": True,
            "options": [
                "Antiperiplanar at 180 degrees",
                "Gauche staggered at 60 degrees",
                "Partially eclipsed at 120 degrees",
                "Synperiplanar eclipsed at 0 degrees",
                "I don't know",
            ],
        },
        "bad": {
            "prompt": "Because you are a visual learner, pick the lowest-energy conformer matching your visual learning style:",
            "graded": True,
            "options": [
                "Anti because the methyl groups are farthest apart",
                "Gauche",
            ],
        },
        "expectedBadRules": ["vark_neuromyth", "missing_idk_option", "option_justification_leak", "option_length_parity"],
    },
    # --- Rubric 5: Kalyuga Expertise-Reversal Compliance (5 scenarios) ---
    {
        "id": "tb-20-cs-er-novice",
        "rubric": "expertise_reversal",
        "domain": "CS",
        "title": "Novice receives brief concrete worked micro-example before 1 faded check",
        "kind": "turn",
        "mode": "teach",
        "learnerState": {"expertise": "novice", "requireWorkedExample": True},
        "good": (
            "Before generalizing recursion, trace a tiny case: `fact(3) = 3 * fact(2) = 3 * 2 * fact(1) = 6`, stopping strictly at base case `n <= 1`.\n\n"
            "> [!IMPORTANT]\n"
            "> Every recursive function needs a terminating base case and a strictly smaller recursive subproblem.\n\n"
            "For `sum_to(n)` returning $1 + 2 + \\dots + n$, what base case prevents infinite recursion on `n = 0`?"
        ),
        "bad": (
            "Figure it out from scratch with no examples or hints. "
            "Derive the general theorem cold for structural induction on trees."
        ),
        "expectedBadRules": ["expertise_reversal_violation"],
    },
    {
        "id": "tb-21-phys-er-novice",
        "rubric": "expertise_reversal",
        "domain": "Physics",
        "title": "Overloaded novice on torque lever arm receives concrete micro-example",
        "kind": "turn",
        "mode": "teach",
        "learnerState": {"expertise": "novice", "affect": "overloaded_frustrated", "requireWorkedExample": True},
        "good": (
            "Start with one concrete wrench: force $F = 10\\,\\text{N}$ acts perpendicular at $r = 0.2\\,\\text{m}$ from the bolt, giving $\\tau = r F \\sin 90^\\circ = 2.0\\,\\text{N}\\cdot\\text{m}$.\n\n"
            "If the same $10\\,\\text{N}$ force instead pushes straight through the bolt ($\\theta = 0^\\circ$), what does $\\tau$ equal?"
        ),
        "bad": (
            "Solve this cold with zero scaffolding: write the full rigid-body tensor cross-product derivation from scratch. "
            "What is the sign? And what is the moment of inertia?"
        ),
        "expectedBadRules": ["expertise_reversal_violation", "single_active_demand"],
    },
    {
        "id": "tb-22-math-er-advanced",
        "rubric": "expertise_reversal",
        "domain": "Math",
        "title": "Advanced learner on diagonalizability gets cold counterexample, ZERO worked example",
        "kind": "turn",
        "mode": "practice",
        "learnerState": {"expertise": "advanced", "allowWorkedExample": False},
        "good": (
            "Claim: every $n \\times n$ matrix whose eigenvalues are all real is diagonalizable over $\\mathbb{R}$.\n\n"
            "Construct a minimal $2 \\times 2$ counterexample and state which multiplicity condition fails."
        ),
        "bad": (
            "Here is a worked example first. Step 1: write A = [[2, 0], [0, 3]]. "
            "Step 2: compute det(A - lambda I) = 0. Step 3: find eigenvectors for each eigenvalue. "
            "Now try a counterexample."
        ),
        "expectedBadRules": ["expertise_reversal_violation"],
    },
    {
        "id": "tb-23-ml-er-bored",
        "rubric": "expertise_reversal",
        "domain": "ML",
        "title": "Underchallenged learner on LayerNorm vs BatchNorm gets bug-hunt, NO worked example",
        "kind": "turn",
        "mode": "practice",
        "learnerState": {"expertise": "advanced", "affect": "bored_underchallenged", "allowWorkedExample": False},
        "good": (
            "A teammate replaces LayerNorm with BatchNorm inside an autoregressive decoder trained with variable-length sequences and batch size $2$.\n\n"
            "Name the exact causal/inference failure mode this introduces during token-by-token generation."
        ),
        "bad": (
            "Worked example: let me walk you step-by-step through a solved BatchNorm mean calculation on `[1, 2, 3]`. "
            "Step 1: compute the mean. Step 2: compute variance. Step 3: normalize."
        ),
        "expectedBadRules": ["expertise_reversal_violation"],
    },
    {
        "id": "tb-24-sys-er-advanced",
        "rubric": "expertise_reversal",
        "domain": "Systems",
        "title": "Advanced learner on Raft Figure 8 commit bug gets cold partition prompt",
        "kind": "turn",
        "mode": "practice",
        "learnerState": {"expertise": "advanced", "allowWorkedExample": False},
        "good": (
            "Suppose a Raft leader in term $4$ sees an entry from term $2$ replicated on a majority of servers and commits it directly by counting replicas.\n\n"
            "Construct the minimal 5-node crash/election sequence that overwrites that committed entry."
        ),
        "bad": (
            "Here is a complete worked example of Raft leader election. Step 1: S1 times out. "
            "Step 2: S1 requests votes. Step 3: S1 wins majority. Now analyze Figure 8."
        ),
        "expectedBadRules": ["expertise_reversal_violation"],
    },
    # --- Rubric 6: Gaming / Rushing Intervention (4 scenarios) ---
    {
        "id": "tb-25-cs-gaming",
        "rubric": "gaming_rushing_intervention",
        "domain": "CS",
        "title": "Rapid wrong clicks on BFS shortest path -> demand 1-line reasoning trace",
        "kind": "turn",
        "mode": "practice",
        "learnerState": {"affect": "gaming_rushing"},
        "good": (
            "Pause before submitting another option—two rapid misses suggest guessing rather than checking the queue invariant.\n\n"
            "Before we check the next answer, write a 1-line reasoning trace: why does standard BFS break when edges have weights $1$ and $5$?"
        ),
        "bad": (
            "Here is another hint: look at the queue order. Just try option C next!"
        ),
        "expectedBadRules": ["gaming_rushing_violation"],
    },
    {
        "id": "tb-26-phys-gaming",
        "rubric": "gaming_rushing_intervention",
        "domain": "Physics",
        "title": "Rushing on pulley constraint -> require 1-line string-length trace before grading",
        "kind": "turn",
        "mode": "practice",
        "learnerState": {"affect": "gaming_rushing"},
        "good": (
            "Slow down and skip guessing. Before grading your acceleration ratio, state your 1-line invariant relating total rope length $L$ to $x_1$ and $x_2$."
        ),
        "bad": (
            "Hint: differentiate the rope equation twice to get a1 = 2*a2."
        ),
        "expectedBadRules": ["gaming_rushing_violation"],
    },
    {
        "id": "tb-27-math-gaming",
        "rubric": "gaming_rushing_intervention",
        "domain": "Math",
        "title": "Hint-spamming on integration by parts -> freeze hints & require 1-line u/dv reason",
        "kind": "turn",
        "mode": "practice",
        "learnerState": {"affect": "gaming_rushing"},
        "good": (
            "Holding further hints until we see your own move. For $\\int x e^x\\,dx$, explain in one line why setting $u = x$ simplifies the remaining integral while $u = e^x$ complicates it."
        ),
        "bad": (
            "Let me give you another clue—skip straight to the next hint and pick u = x."
        ),
        "expectedBadRules": ["gaming_rushing_violation"],
    },
    {
        "id": "tb-28-chem-gaming",
        "rubric": "gaming_rushing_intervention",
        "domain": "Chemistry",
        "title": "Rapid clicking on E2 vs SN2 -> demand 1-line substrate/base classification",
        "kind": "turn",
        "mode": "practice",
        "learnerState": {"affect": "gaming_rushing"},
        "good": (
            "Pausing multiple-choice checks here. Write one line identifying the substrate degree ($1^\\circ/2^\\circ/3^\\circ$) and base bulkiness for `t-BuO-` on 2-bromopropane before we grade."
        ),
        "bad": (
            "Here's a free hint: bulky bases favor elimination over substitution."
        ),
        "expectedBadRules": ["gaming_rushing_violation"],
    },
    # --- Rubric 7: Fatigued-Learner Graceful Calm Wrap-Up (4 scenarios) ---
    {
        "id": "tb-29-sys-fatigue",
        "rubric": "fatigue_calm_wrapup",
        "domain": "Systems",
        "title": "Fatigued learner after long session -> concise <=110w wrap-up, zero guilt",
        "kind": "turn",
        "mode": "conclude",
        "learnerState": {"affect": "fatigued"},
        "good": (
            "Accuracy dipped on the last three checks after 45 minutes—good signal to stop while the core invariant is fresh.\n\n"
            "> [!IMPORTANT]\n"
            "> Any two Raft quorums overlap in at least one server, carrying the highest committed term forward.\n\n"
            "Locking this in for tomorrow's short review. Want to wrap here?"
        ),
        "bad": (
            "Don't break your streak! Push through the fatigue and let's tackle 3 more hard problems right now—you should have studied every day this week."
        ),
        "expectedBadRules": ["guilt_or_streak_shaming", "fatigue_wrapup_violation"],
    },
    {
        "id": "tb-30-phys-fatigue",
        "rubric": "fatigue_calm_wrapup",
        "domain": "Physics",
        "title": "Fatigued learner on damped SHM -> calm 1-sentence anchor & guilt-free stop",
        "kind": "turn",
        "mode": "conclude",
        "learnerState": {"affect": "fatigued"},
        "good": (
            "Response time doubled on the last two drills, so let's switch to low-energy consolidation.\n\n"
            "One anchor to keep: underdamping lowers natural frequency slightly to $\\omega_d = \\sqrt{\\omega_0^2 - \\gamma^2}$ while exponentially shrinking amplitude.\n\n"
            "Everything is saved cleanly; stop here whenever you are ready."
        ),
        "bad": (
            "Don't stop now! Keep going for another 30 minutes or you broke your study streak."
        ),
        "expectedBadRules": ["guilt_or_streak_shaming", "fatigue_wrapup_violation"],
    },
    {
        "id": "tb-31-ml-fatigue",
        "rubric": "fatigue_calm_wrapup",
        "domain": "ML",
        "title": "Fatigued learner on backprop Jacobians -> short <=110w consolidation, no new derivation",
        "kind": "turn",
        "mode": "teach",
        "learnerState": {"affect": "fatigued"},
        "good": (
            "Wrapping at a clean checkpoint rather than opening full softmax-Jacobian algebra tonight.\n\n"
            "Core takeaway: cross-entropy + softmax collapses the output gradient cleanly to $\\hat{y} - y$.\n\n"
            "Ready to close the session here?"
        ),
        "bad": (
            "Let's start a brand-new derivation of second-order Hessian-vector products right now! "
            + " ".join(["matrix"] * 115)
        ),
        "expectedBadRules": ["prose_word_ceiling", "fatigue_wrapup_violation"],
    },
    {
        "id": "tb-32-math-fatigue",
        "rubric": "fatigue_calm_wrapup",
        "domain": "Math",
        "title": "Post-absence fatigued learner on Green's theorem -> calm catch-up, zero shame",
        "kind": "turn",
        "mode": "teach",
        "learnerState": {"affect": "fatigued"},
        "good": (
            "Your backlog is smoothed across five calm days—nothing is lost.\n\n"
            "Today's single anchor: Green's theorem equates counterclockwise boundary circulation to integrated microscopic curl over the enclosed region.\n\n"
            "Want to review just this one orientation check and end for today?"
        ),
        "bad": (
            "You fell behind again and it is disappointing that you missed five days—no excuses for missing review. "
            "Push through a few more proofs tonight."
        ),
        "expectedBadRules": ["guilt_or_streak_shaming", "fatigue_wrapup_violation"],
    },
    # --- Rubric 8: Causal Noisy-OR Root-Cause Ancestor Probing (4 scenarios) ---
    {
        "id": "tb-33-sys-root-probe",
        "rubric": "causal_root_probe",
        "domain": "Systems",
        "title": "Failed Raft Log Matching leaf -> ask 1 high-ΔH Quorum Intersection ancestor probe",
        "kind": "turn",
        "mode": "practice",
        "learnerState": {
            "requireRootProbe": True,
            "failedLeaf": "raft-log-matching",
            "suspectAncestorKeywords": ["quorum", "majority", "overlap"],
        },
        "good": (
            "Before revisiting Log Matching, let's isolate the prerequisite underneath it.\n\n"
            "In a 5-node cluster, can two disjoint partitions each form a majority quorum to elect two leaders in the same term?"
        ),
        "bad": (
            "Let me re-explain the entire Log Matching property from top to bottom with every RPC field and index check."
        ),
        "expectedBadRules": ["causal_root_probe_violation"],
    },
    {
        "id": "tb-34-phys-root-probe",
        "rubric": "causal_root_probe",
        "domain": "Physics",
        "title": "Failed SHM elapsed-time leaf -> probe Phasor direction sign ancestor",
        "kind": "turn",
        "mode": "practice",
        "learnerState": {
            "requireRootProbe": True,
            "failedLeaf": "shm-two-point-time",
            "suspectAncestorKeywords": ["phasor", "quadrant", "velocity"],
        },
        "good": (
            "Your elapsed-time miss traces upstream to velocity sign on the phasor circle.\n\n"
            "For $x(t) = A\\cos(\\omega t)$, does a point in the upper semicircle ($\\phi \\in (0, \\pi)$) have positive or negative velocity?"
        ),
        "bad": (
            "Let's re-read the entire chapter on SHM kinematics and memorize every time fraction table."
        ),
        "expectedBadRules": ["causal_root_probe_violation"],
    },
    {
        "id": "tb-35-ml-root-probe",
        "rubric": "causal_root_probe",
        "domain": "ML",
        "title": "Failed Multi-Head Attention gradient leaf -> probe dot-product variance ancestor",
        "kind": "turn",
        "mode": "practice",
        "learnerState": {
            "requireRootProbe": True,
            "failedLeaf": "multihead-grad",
            "suspectAncestorKeywords": ["variance", "dot product", "d_k"],
        },
        "good": (
            "Skip multi-head mechanics for one minute and test the upstream variance rule.\n\n"
            "If $q_i, k_i \\sim \\mathcal{N}(0, 1)$ independently for $i=1..d_k$, how does $\\operatorname{Var}(q \\cdot k)$ scale with $d_k$?"
        ),
        "bad": (
            "Starting from scratch on the entire Transformer block: first comes input embedding, then positional encoding, then projection matrices."
        ),
        "expectedBadRules": ["causal_root_probe_violation"],
    },
    {
        "id": "tb-36-cs-root-probe",
        "rubric": "causal_root_probe",
        "domain": "CS",
        "title": "Failed Dijkstra PQ leaf -> probe non-negative edge relaxation invariant",
        "kind": "turn",
        "mode": "practice",
        "learnerState": {
            "requireRootProbe": True,
            "failedLeaf": "dijkstra-pq",
            "suspectAncestorKeywords": ["negative", "non-negative", "relax"],
        },
        "good": (
            "Before fixing the heap loop, let's verify the prerequisite invariant.\n\n"
            "Why can an unsettled vertex $v$ never later shorten an already-popped minimum-distance vertex $u$ when all remaining edges are non-negative?"
        ),
        "bad": (
            "Let me re-explain the entire Dijkstra implementation line by line from imports to return value."
        ),
        "expectedBadRules": ["causal_root_probe_violation"],
    },
    # --- Rubric 9: T3/T4 Anti-Inflation & Zero-Leak Parsons/Faded Cards (4 scenarios) ---
    {
        "id": "tb-37-sys-anti-inflation",
        "rubric": "anti_inflation_and_zero_leak",
        "domain": "Systems",
        "title": "Refuse marking bottleneck Raft Safety solid from T2 MCQ recognition alone",
        "kind": "turn",
        "mode": "practice",
        "learnerState": {"isBottleneck": True, "evidenceTier": "T2"},
        "good": (
            "That multiple-choice answer checks out at T2 recognition, which confirms recall of the Leader Completeness rule.\n\n"
            "Because this node is a central bottleneck, let's verify T3 transfer before upgrading it: construct a 3-node log scenario where a candidate with a higher term still loses the up-to-date log check."
        ),
        "bad": (
            "Awesome! Marking this solid from that MCQ alone—one multiple-choice hit proves complete mastery and no further verification needed."
        ),
        "expectedBadRules": ["zero_sycophancy", "t2_mastery_inflation"],
    },
    {
        "id": "tb-38-phys-anti-inflation",
        "rubric": "anti_inflation_and_zero_leak",
        "domain": "Physics",
        "title": "Refuse marking bottleneck SHM Linearization mastered from T2 MCQ alone",
        "kind": "turn",
        "mode": "practice",
        "learnerState": {"isBottleneck": True, "evidenceTier": "T2"},
        "good": (
            "Your MCQ choice identifies the small-angle formula correctly (T2 recall).\n\n"
            "To clear the T3 transfer gate on this bottleneck, derive the first nonlinear correction term from $\\sin\\theta \\approx \\theta - \\theta^3/6$ and predict whether period increases or decreases at large amplitude."
        ),
        "bad": (
            "Fully mastered — no transfer check needed! Bottleneck is fully mastered after T2 recognition."
        ),
        "expectedBadRules": ["t2_mastery_inflation"],
    },
    {
        "id": "tb-39-cs-parsons-zero-leak",
        "rubric": "anti_inflation_and_zero_leak",
        "domain": "CS",
        "title": "Zero-leak Parsons block card for binary search (no order labels/distractor tags)",
        "kind": "card",
        "good": {
            "mode": "parsons",
            "graded": True,
            "prompt": "Arrange the lines to implement lower_bound on a sorted array (one block is a distractor).",
            "blocks": [
                {"id": "b1", "code": "lo, hi = 0, len(arr)"},
                {"id": "b2", "code": "while lo < hi:"},
                {"id": "b3", "code": "    mid = lo + (hi - lo) // 2"},
                {"id": "b4", "code": "    if arr[mid] < target: lo = mid + 1"},
                {"id": "b5", "code": "    else: hi = mid"},
                {"id": "b6", "code": "    else: hi = mid - 1"},
            ],
            "modelSolution": "lo, hi = 0, len(arr)\nwhile lo < hi:\n    mid = lo + (hi - lo) // 2\n    if arr[mid] < target: lo = mid + 1\n    else: hi = mid",
        },
        "bad": {
            "mode": "parsons",
            "graded": True,
            "prompt": "Great job! Arrange the blocks:",
            "blocks": [
                {"id": "b1", "code": "Step 1: lo, hi = 0, len(arr) # line 1"},
                {"id": "b2", "code": "while lo < hi: (second)"},
                {"id": "b3", "code": "    else: hi = mid - 1 # distractor"},
            ],
            "modelSolution": "lo, hi = 0, len(arr)",
        },
        "expectedBadRules": ["zero_sycophancy", "parsons_order_or_distractor_leak"],
    },
    {
        "id": "tb-40-ml-faded-zero-leak",
        "rubric": "anti_inflation_and_zero_leak",
        "domain": "ML",
        "title": "Zero-leak faded completion card for scaled dot-product attention",
        "kind": "card",
        "good": {
            "mode": "faded",
            "graded": True,
            "prompt": "Fill the single variance-normalization denominator in scaled dot-product attention.",
            "template": "scores = (Q @ K.T) / ____\nweights = softmax(scores, axis=-1)",
            "modelSolution": "math.sqrt(d_k)",
        },
        "bad": {
            "mode": "faded",
            "graded": True,
            "prompt": "Fill the blank (model solution math.sqrt(d_k)):",
            "template": "scores = (Q @ K.T) / ____ (answer: math.sqrt(d_k))\nweights = softmax(scores, axis=-1)",
            "modelSolution": "math.sqrt(d_k)",
        },
        "expectedBadRules": ["faded_blank_answer_leak", "model_solution_leak"],
    },
)


def run_benchmark() -> dict[str, Any]:
    """Execute the 40-scenario multi-domain TutorBench-style golden rubric suite."""
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
            lstate = sc.get("learnerState")
            good_res = lint_turn(sc["good"], mode=mode, learner_state=lstate)
            bad_res = lint_turn(sc["bad"], mode=mode, learner_state=lstate)

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
        description="Syntopik v3.0 — Zero-LLM TutorBench-Style Pedagogical Linter & 40-Scenario Golden Eval Pack",
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
    p_lint.add_argument(
        "--expertise",
        default=None,
        choices=("novice", "developing", "intermediate", "advanced"),
        help="Optional learner expertise band for Kalyuga Expertise-Reversal linting",
    )
    p_lint.add_argument(
        "--affect",
        default=None,
        choices=(
            "flow",
            "productive_confusion",
            "overloaded_frustrated",
            "bored_underchallenged",
            "gaming_rushing",
            "fatigued",
        ),
        help="Optional affective state for gaming/fatigue/overload policy linting",
    )
    p_lint.add_argument("--bottleneck", action="store_true", help="Flag target node as high-centrality bottleneck")
    p_lint.add_argument("--evidence-tier", default=None, choices=("T1", "T2", "T3", "T4"), help="Current evidence tier")
    p_lint.add_argument("--json", action="store_true", default=argparse.SUPPRESS)

    # check-card
    p_card = sub.add_parser("check-card", help="Audit a graded quiz/probe/check/Parsons/faded JSON card for zero-leak integrity")
    p_card.add_argument("json_file", help="Path to quiz card JSON file or '-' for stdin")
    p_card.add_argument("--json", action="store_true", default=argparse.SUPPRESS)

    # bench
    p_bench = sub.add_parser("bench", help="Run the built-in 40-scenario multi-domain TutorBench golden eval suite")
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
            lstate: dict[str, Any] = {}
            if args.expertise:
                lstate["expertise"] = args.expertise
            if args.affect:
                lstate["affect"] = args.affect
            if args.bottleneck:
                lstate["isBottleneck"] = True
            if args.evidence_tier:
                lstate["evidenceTier"] = args.evidence_tier
            res = lint_turn(text, mode=args.mode, max_words=args.max_words, learner_state=lstate or None)
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
