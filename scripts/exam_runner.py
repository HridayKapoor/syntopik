#!/usr/bin/env python3
"""Offline Timed Mastery & NTA JEE CBT Exam Builder & Deterministic Grader for Syntopik.

Compiles verified assessment/JEE question specs (single_correct, multi_correct with official
JEE Advanced partial marking, numerical with tolerance/range, matrix_match with
per-row partial marking) into 100% standalone offline CBT HTML files, and
grades exported attempt JSONs into mistakes.jsonl, exam-attempts.jsonl, and FSRS
spaced-repetition reviews via scripts/srs_engine.py.
"""

from __future__ import annotations

import argparse
import base64
import html as html_lib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from syntopik.paths import (  # noqa: E402
    find_node as _find_node,
    resolve_exams_dir as _resolve_exams_dir_core,
    resolve_kg_dir as _resolve_kg_dir_core,
    resolve_package_root,
    sync_hub,
)
from syntopik.schemas import (  # noqa: E402
    VALID_MISTAKE_CATEGORIES,
    normalize_mistake_category,
    slugify as _slugify_core,
    today_iso,
)
from syntopik.storage import append_jsonl_locked, write_text_atomic  # noqa: E402

PKG_ROOT = resolve_package_root()
DEFAULT_KG_DIR = ROOT / "knowledge-graph"
DEFAULT_EXAMS_DIR = ROOT / "exams"
TEMPLATE_PATH = PKG_ROOT / "demos" / "exam-cbt-template.html"
SRS_SCRIPT = PKG_ROOT / "scripts" / "srs_engine.py"
SYNC_SCRIPT = PKG_ROOT / "scripts" / "sync-graph.mjs"

VALID_QUESTION_TYPES = ("single_correct", "multi_correct", "numerical", "matrix_match")

MIME_BY_EXT = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}


def slugify(text: str) -> str:
    return _slugify_core(text, default="jee-cbt-exam", max_len=0)


def safe_script_json(value: Any) -> str:
    """Serialize JSON safe for embedding inside an HTML <script> tag."""
    raw = json.dumps(value, ensure_ascii=False, indent=2)
    return (
        raw.replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def resolve_kg_dir(kg_dir_arg: str | Path | None) -> tuple[Path, bool]:
    kg_dir = _resolve_kg_dir_core(explicit=kg_dir_arg)
    is_default = kg_dir == DEFAULT_KG_DIR.resolve()
    return kg_dir, is_default


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    append_jsonl_locked(path, row)


def sync_hub_quiet(kg_dir: Path | str | None = None, workspace: Path | str | None = None) -> None:
    sync_hub(workspace=workspace, kg_dir=kg_dir, quiet=True)


def _normalize_labeled_items(raw_items: Any, default_ids: list[str], kind: str, qid: str) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    if isinstance(raw_items, dict):
        for k, v in raw_items.items():
            out.append({"id": str(k).strip().upper(), "text": str(v)})
    elif isinstance(raw_items, list):
        for idx, item in enumerate(raw_items):
            if isinstance(item, dict):
                item_id = str(item.get("id") or (default_ids[idx] if idx < len(default_ids) else str(idx + 1))).strip().upper()
                out.append({"id": item_id, "text": str(item.get("text", ""))})
            else:
                item_id = default_ids[idx] if idx < len(default_ids) else str(idx + 1)
                out.append({"id": item_id, "text": str(item)})
    else:
        raise ValueError(f"Question '{qid}': '{kind}' must be a list or dict")
    if not out:
        raise ValueError(f"Question '{qid}': '{kind}' cannot be empty")
    return out


def inline_figure(figure_ref: str | None, spec_dir: Path, qid: str) -> tuple[str | None, str | None]:
    """Return (figureSvg, figureDataUri) for a local SVG/raster figure or inline data."""
    if not figure_ref:
        return None, None
    ref = str(figure_ref).strip()
    if not ref:
        return None, None
    if ref.startswith("data:image/"):
        return None, ref
    if ref.lstrip().startswith("<svg"):
        return ref, None

    p = Path(ref).expanduser()
    candidates = [p] if p.is_absolute() else [(spec_dir / p).resolve(), (ROOT / p).resolve()]
    resolved = next((c for c in candidates if c.is_file()), None)
    if resolved is None:
        raise ValueError(f"Question '{qid}': figure file not found: '{ref}'")

    ext = resolved.suffix.lower()
    if ext == ".svg":
        svg = resolved.read_text(encoding="utf-8")
        svg = re.sub(r"^\s*<\?xml[^>]*\?>\s*", "", svg, flags=re.IGNORECASE)
        svg = re.sub(r"^\s*<!DOCTYPE[^>]*>\s*", "", svg, flags=re.IGNORECASE)
        return svg.strip(), None
    if ext in MIME_BY_EXT:
        b64 = base64.b64encode(resolved.read_bytes()).decode("ascii")
        return None, f"data:{MIME_BY_EXT[ext]};base64,{b64}"
    raise ValueError(f"Question '{qid}': unsupported figure extension '{ext}' (use .svg, .png, .jpg, .webp, .gif)")


def normalize_spec(raw_spec: dict[str, Any], spec_dir: Path | None = None, inline_media: bool = False) -> dict[str, Any]:
    """Validate and normalize an exam spec dictionary."""
    if not isinstance(raw_spec, dict):
        raise ValueError("Exam spec must be a JSON object")
    title = str(raw_spec.get("title") or "").strip()
    if not title:
        raise ValueError("Exam spec requires a non-empty 'title'")
    questions_in = raw_spec.get("questions")
    if not isinstance(questions_in, list) or not questions_in:
        raise ValueError("Exam spec requires a non-empty 'questions' array")

    exam_slug = slugify(raw_spec.get("examSlug") or title)
    exam_type = str(raw_spec.get("examType") or "JEE Advanced").strip()
    duration_minutes = float(raw_spec.get("durationMinutes", 60))
    if duration_minutes <= 0:
        raise ValueError("'durationMinutes' must be > 0")

    seen_ids: set[str] = set()
    norm_questions: list[dict[str, Any]] = []
    max_score = 0.0

    for idx, q in enumerate(questions_in, start=1):
        if not isinstance(q, dict):
            raise ValueError(f"Question #{idx} must be an object")
        qid = str(q.get("id") or f"Q{idx}").strip()
        if qid in seen_ids:
            raise ValueError(f"Duplicate question id '{qid}'")
        seen_ids.add(qid)

        qtype = str(q.get("type") or "").strip()
        if qtype not in VALID_QUESTION_TYPES:
            raise ValueError(f"Question '{qid}': invalid type '{qtype}' (expected one of {VALID_QUESTION_TYPES})")

        stem = str(q.get("stem") or "").strip()
        if not stem:
            raise ValueError(f"Question '{qid}': non-empty 'stem' is required")

        trap_cat_raw = str(q.get("trapCategory") or "").strip()
        trap_cat = ""
        if trap_cat_raw:
            try:
                trap_cat = normalize_mistake_category(trap_cat_raw)
            except ValueError as exc:
                raise ValueError(
                    f"Question '{qid}': invalid trapCategory '{trap_cat_raw}' (expected one of {VALID_MISTAKE_CATEGORIES})"
                ) from exc

        nq: dict[str, Any] = {
            "id": qid,
            "type": qtype,
            "subject": str(q.get("subject") or "General").strip(),
            "chapter": str(q.get("chapter") or "General").strip(),
            "propId": str(q.get("propId") or "").strip(),
            "propTitle": str(q.get("propTitle") or "").strip(),
            "stem": stem,
            "solution": str(q.get("solution") or "").strip(),
            "trapNote": str(q.get("trapNote") or "").strip(),
            "trapCategory": trap_cat,
            "marksUnattempted": float(q.get("marksUnattempted", 0)),
        }

        if q.get("figureCaption"):
            nq["figureCaption"] = str(q["figureCaption"])
        if q.get("figure"):
            nq["figure"] = str(q["figure"])
            if inline_media:
                svg_str, data_uri = inline_figure(str(q["figure"]), spec_dir or ROOT, qid)
                if svg_str:
                    nq["figureSvg"] = svg_str
                if data_uri:
                    nq["figureDataUri"] = data_uri

        if qtype == "single_correct":
            opts = _normalize_labeled_items(q.get("options"), ["A", "B", "C", "D", "E"], "options", qid)
            opt_ids = {o["id"] for o in opts}
            ans = str(q.get("answer") or "").strip().upper()
            if ans not in opt_ids:
                raise ValueError(f"Question '{qid}': single_correct answer '{ans}' not in options {sorted(opt_ids)}")
            nq["options"] = opts
            nq["answer"] = ans
            nq["marksCorrect"] = float(q.get("marksCorrect", 4))
            nq["marksWrong"] = float(q.get("marksWrong", -1))

        elif qtype == "multi_correct":
            opts = _normalize_labeled_items(q.get("options"), ["A", "B", "C", "D", "E"], "options", qid)
            opt_ids = {o["id"] for o in opts}
            raw_ans = q.get("answer")
            if isinstance(raw_ans, str):
                ans_list = [raw_ans.strip().upper()]
            elif isinstance(raw_ans, list):
                ans_list = sorted({str(a).strip().upper() for a in raw_ans if str(a).strip()})
            else:
                raise ValueError(f"Question '{qid}': multi_correct 'answer' must be a non-empty list of option IDs")
            if not ans_list or not set(ans_list).issubset(opt_ids):
                raise ValueError(f"Question '{qid}': multi_correct answer {ans_list} not subset of {sorted(opt_ids)}")
            nq["options"] = opts
            nq["answer"] = ans_list
            nq["marksCorrect"] = float(q.get("marksCorrect", 4))
            nq["marksWrong"] = float(q.get("marksWrong", -2))
            nq["partialMarking"] = bool(q.get("partialMarking", True))
            if isinstance(q.get("partialMarks"), dict):
                nq["partialMarks"] = {str(k): float(v) for k, v in q["partialMarks"].items()}

        elif qtype == "numerical":
            has_range = isinstance(q.get("range"), list) and len(q["range"]) == 2
            if has_range:
                lo, hi = float(q["range"][0]), float(q["range"][1])
                if lo > hi:
                    raise ValueError(f"Question '{qid}': numerical range [{lo}, {hi}] has min > max")
                nq["range"] = [lo, hi]
                nq["answer"] = float(q["answer"]) if q.get("answer") is not None else round((lo + hi) / 2.0, 6)
            else:
                if q.get("answer") is None or str(q.get("answer")).strip() == "":
                    raise ValueError(f"Question '{qid}': numerical question requires 'answer' or 'range'")
                nq["answer"] = float(q["answer"])
                tol = float(q.get("tolerance", 0.01))
                if tol < 0:
                    raise ValueError(f"Question '{qid}': tolerance must be >= 0")
                nq["tolerance"] = tol
            nq["marksCorrect"] = float(q.get("marksCorrect", 4))
            nq["marksWrong"] = float(q.get("marksWrong", 0))

        elif qtype == "matrix_match":
            rows = _normalize_labeled_items(q.get("rows"), ["P", "Q", "R", "S", "T"], "rows", qid)
            cols = _normalize_labeled_items(q.get("cols"), ["1", "2", "3", "4", "5"], "cols", qid)
            row_ids = {r["id"] for r in rows}
            col_ids = {c["id"] for c in cols}
            raw_ans = q.get("answer")
            if not isinstance(raw_ans, dict) or not raw_ans:
                raise ValueError(f"Question '{qid}': matrix_match requires non-empty 'answer' mapping")
            norm_ans: dict[str, list[str]] = {}
            for rk, rv in raw_ans.items():
                rk_u = str(rk).strip().upper()
                if rk_u not in row_ids:
                    raise ValueError(f"Question '{qid}': matrix answer row '{rk_u}' not in rows {sorted(row_ids)}")
                vals = [rv] if isinstance(rv, (str, int, float)) else list(rv)
                vals_u = sorted({str(v).strip().upper() for v in vals if str(v).strip()})
                if not vals_u or not set(vals_u).issubset(col_ids):
                    raise ValueError(f"Question '{qid}': matrix answer for '{rk_u}' ({vals_u}) not subset of cols {sorted(col_ids)}")
                norm_ans[rk_u] = vals_u
            nq["rows"] = rows
            nq["cols"] = cols
            nq["answer"] = norm_ans
            nq["matrixSinglePerRow"] = bool(q.get("matrixSinglePerRow", False))
            nq["partialMarking"] = bool(q.get("partialMarking", True))
            n_rows = len(norm_ans)
            if q.get("marksPerRow") is not None:
                per_row = float(q["marksPerRow"])
                nq["marksPerRow"] = per_row
                nq["marksCorrect"] = float(q["marksCorrect"]) if q.get("marksCorrect") is not None else round(per_row * n_rows, 4)
            elif q.get("marksCorrect") is not None:
                mc = float(q["marksCorrect"])
                nq["marksCorrect"] = mc
                nq["marksPerRow"] = round(mc / n_rows, 4) if n_rows else 0.0
            else:
                nq["marksPerRow"] = 1.0
                nq["marksCorrect"] = float(n_rows)
            nq["marksWrong"] = float(q.get("marksWrong", 0))
            if q.get("marksWrongPerRow") is not None:
                nq["marksWrongPerRow"] = float(q["marksWrongPerRow"])

        max_score += float(nq["marksCorrect"])
        norm_questions.append(nq)

    return {
        "schemaVersion": 1,
        "examSlug": exam_slug,
        "title": title,
        "examType": exam_type,
        "durationMinutes": int(duration_minutes) if duration_minutes.is_integer() else duration_minutes,
        "instructions": [str(x) for x in (raw_spec.get("instructions") or [])],
        "maxScore": round(max_score, 2),
        "questions": norm_questions,
    }


def _is_empty_answer(qtype: str, ans: Any) -> bool:
    if ans is None:
        return True
    if qtype == "single_correct":
        return str(ans).strip() == ""
    if qtype == "multi_correct":
        if isinstance(ans, str):
            return ans.strip() == ""
        return not isinstance(ans, list) or len([x for x in ans if str(x).strip()]) == 0
    if qtype == "numerical":
        s = str(ans).strip()
        if s in ("", ".", "-", "+", "-.", "+."):
            return True
        try:
            return not math.isfinite(float(s))
        except (ValueError, TypeError):
            return True
    if qtype == "matrix_match":
        if not isinstance(ans, dict):
            return True
        for v in ans.values():
            if isinstance(v, list) and any(str(x).strip() for x in v):
                return False
            if isinstance(v, (str, int, float)) and str(v).strip():
                return False
        return True
    return True


def _format_answer(qtype: str, ans: Any) -> str:
    if _is_empty_answer(qtype, ans):
        return "Unattempted"
    if qtype == "single_correct":
        return str(ans).strip().upper()
    if qtype == "multi_correct":
        vals = [ans] if isinstance(ans, str) else list(ans)
        return ", ".join(sorted({str(v).strip().upper() for v in vals if str(v).strip()}))
    if qtype == "numerical":
        return str(ans).strip()
    if qtype == "matrix_match" and isinstance(ans, dict):
        parts = []
        for k in sorted(ans.keys()):
            v = ans[k]
            vals = [v] if isinstance(v, (str, int, float)) else list(v or [])
            clean = sorted({str(x).strip().upper() for x in vals if str(x).strip()})
            if clean:
                parts.append(f"{str(k).upper()}->{','.join(clean)}")
        return "; ".join(parts) if parts else "Unattempted"
    return str(ans)


def score_question(q: dict[str, Any], raw_ans: Any) -> dict[str, Any]:
    """Deterministically score a single normalized question."""
    qtype = q["type"]
    max_marks = float(q["marksCorrect"])
    m_wrong = float(q.get("marksWrong", 0))
    m_unatt = float(q.get("marksUnattempted", 0))

    if _is_empty_answer(qtype, raw_ans):
        return {
            "status": "unattempted",
            "marks": round(m_unatt, 2),
            "maxMarks": round(max_marks, 2),
            "negativeLost": round(abs(m_unatt), 2) if m_unatt < 0 else 0.0,
            "studentAnswer": None,
            "correctAnswer": q["answer"],
            "detail": "unattempted",
        }

    if qtype == "single_correct":
        chosen = str(raw_ans).strip().upper()
        ok = chosen == q["answer"]
        marks = max_marks if ok else m_wrong
        return {
            "status": "correct" if ok else "wrong",
            "marks": round(marks, 2),
            "maxMarks": round(max_marks, 2),
            "negativeLost": round(abs(marks), 2) if marks < 0 else 0.0,
            "studentAnswer": chosen,
            "correctAnswer": q["answer"],
            "detail": "exact match" if ok else f"chose {chosen}, expected {q['answer']}",
        }

    if qtype == "multi_correct":
        vals = [raw_ans] if isinstance(raw_ans, str) else list(raw_ans)
        chosen_set = {str(v).strip().upper() for v in vals if str(v).strip()}
        chosen_sorted = sorted(chosen_set)
        correct_set = set(q["answer"])
        wrong_chosen = sorted(chosen_set - correct_set)

        if wrong_chosen:
            return {
                "status": "wrong",
                "marks": round(m_wrong, 2),
                "maxMarks": round(max_marks, 2),
                "negativeLost": round(abs(m_wrong), 2) if m_wrong < 0 else 0.0,
                "studentAnswer": chosen_sorted,
                "correctAnswer": q["answer"],
                "detail": f"selected wrong option(s): {','.join(wrong_chosen)}",
            }

        if chosen_set == correct_set:
            return {
                "status": "correct",
                "marks": round(max_marks, 2),
                "maxMarks": round(max_marks, 2),
                "negativeLost": 0.0,
                "studentAnswer": chosen_sorted,
                "correctAnswer": q["answer"],
                "detail": "all correct options selected",
            }

        if not q.get("partialMarking", True):
            return {
                "status": "wrong",
                "marks": round(m_wrong, 2),
                "maxMarks": round(max_marks, 2),
                "negativeLost": round(abs(m_wrong), 2) if m_wrong < 0 else 0.0,
                "studentAnswer": chosen_sorted,
                "correctAnswer": q["answer"],
                "detail": "incomplete selection (partial marking disabled)",
            }

        # Official JEE Advanced partial marking
        n_total = len(correct_set)
        n_chosen = len(chosen_set)
        custom_partial = q.get("partialMarks")
        if isinstance(custom_partial, dict) and str(n_chosen) in custom_partial:
            pts = float(custom_partial[str(n_chosen)])
        elif max_marks != 4.0:
            pts = round(max_marks * (n_chosen / n_total), 2)
        elif n_total >= 4 and n_chosen == 3:
            pts = 3.0
        elif n_total >= 3 and n_chosen == 2:
            pts = 2.0
        elif n_total >= 2 and n_chosen == 1:
            pts = 1.0
        else:
            pts = min(float(n_chosen), max(0.0, max_marks - 1.0))

        missed = sorted(correct_set - chosen_set)
        return {
            "status": "partial",
            "marks": round(pts, 2),
            "maxMarks": round(max_marks, 2),
            "negativeLost": 0.0,
            "studentAnswer": chosen_sorted,
            "correctAnswer": q["answer"],
            "detail": f"partial ({n_chosen}/{n_total} correct, missed {','.join(missed)})",
        }

    if qtype == "numerical":
        try:
            val = float(str(raw_ans).strip())
            if not math.isfinite(val):
                raise ValueError("non-finite")
        except (ValueError, TypeError):
            return {
                "status": "unattempted",
                "marks": round(m_unatt, 2),
                "maxMarks": round(max_marks, 2),
                "negativeLost": round(abs(m_unatt), 2) if m_unatt < 0 else 0.0,
                "studentAnswer": None,
                "correctAnswer": q.get("range") or q["answer"],
                "detail": "unattempted (invalid/non-finite numerical input)",
            }
        if "range" in q:
            lo, hi = float(q["range"][0]), float(q["range"][1])
            ok = (lo - 1e-9) <= val <= (hi + 1e-9)
            ref_desc = f"[{lo}, {hi}]"
        else:
            tol = float(q.get("tolerance", 0.01))
            ok = abs(val - float(q["answer"])) <= (tol + 1e-9)
            ref_desc = f"{q['answer']} (±{tol})"
        marks = max_marks if ok else m_wrong
        return {
            "status": "correct" if ok else "wrong",
            "marks": round(marks, 2),
            "maxMarks": round(max_marks, 2),
            "negativeLost": round(abs(marks), 2) if marks < 0 else 0.0,
            "studentAnswer": val,
            "correctAnswer": q.get("range") or q["answer"],
            "detail": f"within {ref_desc}" if ok else f"got {val}, expected {ref_desc}",
        }

    if qtype == "matrix_match":
        exp: dict[str, list[str]] = q["answer"]
        norm_student: dict[str, list[str]] = {}
        for rk, rv in raw_ans.items():
            rk_u = str(rk).strip().upper()
            vals = [rv] if isinstance(rv, (str, int, float)) else list(rv or [])
            clean = sorted({str(x).strip().upper() for x in vals if str(x).strip()})
            if clean:
                norm_student[rk_u] = clean

        matched_rows = 0
        wrong_rows = 0
        row_results: dict[str, str] = {}
        for rk, exp_cols in exp.items():
            ch_cols = norm_student.get(rk, [])
            if not ch_cols:
                row_results[rk] = "blank"
            elif ch_cols == exp_cols:
                matched_rows += 1
                row_results[rk] = "correct"
            else:
                wrong_rows += 1
                row_results[rk] = "wrong"
        # Extra unexpected row keys count as wrong rows
        for rk in norm_student:
            if rk not in exp:
                wrong_rows += 1
                row_results[rk] = "wrong"

        if matched_rows == len(exp) and wrong_rows == 0:
            return {
                "status": "correct",
                "marks": round(max_marks, 2),
                "maxMarks": round(max_marks, 2),
                "negativeLost": 0.0,
                "studentAnswer": norm_student,
                "correctAnswer": exp,
                "detail": f"all {len(exp)} rows exact",
            }

        if not q.get("partialMarking", True):
            return {
                "status": "wrong",
                "marks": round(m_wrong, 2),
                "maxMarks": round(max_marks, 2),
                "negativeLost": round(abs(m_wrong), 2) if m_wrong < 0 else 0.0,
                "studentAnswer": norm_student,
                "correctAnswer": exp,
                "detail": f"{matched_rows}/{len(exp)} rows matched (partial marking disabled)",
            }

        per_row = float(q.get("marksPerRow", max_marks / len(exp) if exp else 1.0))
        wrong_per_row = float(q["marksWrongPerRow"]) if q.get("marksWrongPerRow") is not None else m_wrong
        neg_deduction = wrong_rows * wrong_per_row if wrong_per_row < 0 else 0.0
        marks = round(matched_rows * per_row + neg_deduction, 2)
        status = "partial" if matched_rows > 0 else "wrong"
        return {
            "status": status,
            "marks": marks,
            "maxMarks": round(max_marks, 2),
            "negativeLost": round(abs(neg_deduction), 2),
            "studentAnswer": norm_student,
            "correctAnswer": exp,
            "detail": f"{matched_rows}/{len(exp)} rows exact, {wrong_rows} wrong",
        }

    raise ValueError(f"Unsupported question type: {qtype}")


def _log_mistake_entry(kg_dir: Path, entry: dict[str, Any]) -> dict[str, Any]:
    """Append a mistake record using mistake_analytics.append_mistake if available, else exact schema fallback."""
    scripts_dir = str(ROOT / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    try:
        import mistake_analytics  # type: ignore

        fn = getattr(mistake_analytics, "append_mistake", None)
        if callable(fn):
            res = fn(kg_dir, entry, no_sync=True)
            if isinstance(res, dict) and res.get("id"):
                return res
            return entry
    except Exception:
        pass

    append_jsonl(kg_dir / "mistakes.jsonl", entry)
    return entry


def _extract_attempt_entry(attempt: dict[str, Any], qid: str) -> tuple[Any, int, str]:
    """Extract (answer, timeSpentSec, uiStatus) flexibly from standard or simplified attempt JSON."""
    responses = attempt.get("responses")
    if not isinstance(responses, dict):
        responses = {}
    time_map = attempt.get("timeSpentByQuestion") or attempt.get("timeByQuestion") or {}

    raw = responses.get(qid)
    if isinstance(raw, dict) and ("answer" in raw or "status" in raw or "timeSpentSec" in raw):
        ans = raw.get("answer")
        t_sec = int(round(float(raw.get("timeSpentSec", time_map.get(qid, 0)) or 0)))
        ui_status = str(raw.get("status") or "")
        # In NTA CBT, only answered / answered_marked (or raw answer present) count for evaluation;
        # if explicitly marked without answer, ans is None.
        return ans, t_sec, ui_status
    t_sec = int(round(float(time_map.get(qid, 0) or 0)))
    return raw, t_sec, ""


def _build_schema_example() -> dict[str, Any]:
    spec_example = {
        "examSlug": "jee-adv-mini-mock-01",
        "title": "JEE Advanced Mini Mock — Mechanics & Electrochemistry",
        "examType": "JEE Advanced",
        "durationMinutes": 45,
        "instructions": [
            "Single Correct: +4 / -1",
            "Multi-Correct: +4 / +3 / +2 / +1 / 0 / -2 (official JEE Advanced partial marking)",
            "Numerical: +4 / 0 (tolerance ±0.01)",
            "Matrix Match: +1 per exact row match (max +4), 0 negative",
        ],
        "questions": [
            {
                "id": "Q1",
                "type": "single_correct",
                "subject": "Physics",
                "chapter": "Rotational Mechanics",
                "propId": "phy-rot-rolling-incline",
                "stem": "A solid sphere rolls without slipping down an incline of angle $\\theta$. Its linear acceleration is:",
                "figure": "demos/incline.svg",
                "options": [
                    {"id": "A", "text": "$g\\sin\\theta$"},
                    {"id": "B", "text": "$\\frac{5}{7}g\\sin\\theta$"},
                    {"id": "C", "text": "$\\frac{2}{3}g\\sin\\theta$"},
                    {"id": "D", "text": "$\\frac{1}{2}g\\sin\\theta$"},
                ],
                "answer": "B",
                "marksCorrect": 4,
                "marksWrong": -1,
                "solution": "For a solid sphere, $I = \\frac{2}{5}MR^2$, so $a = \\frac{g\\sin\\theta}{1 + I/(MR^2)} = \\frac{5}{7}g\\sin\\theta$.",
                "trapNote": "Do not confuse solid sphere (5/7) with hollow sphere (3/5) or solid cylinder (2/3).",
                "trapCategory": "concept_gap",
            },
            {
                "id": "Q2",
                "type": "multi_correct",
                "subject": "Chemistry",
                "chapter": "Electrochemistry",
                "propId": "chem-elec-nernst",
                "stem": "For a galvanic cell at $298\\,\\text{K}$, which of the following statements are correct?",
                "options": [
                    {"id": "A", "text": "$\\Delta G^\\circ = -nFE^\\circ_{\\text{cell}}$"},
                    {"id": "B", "text": "$E^\\circ_{\\text{cell}}$ is an extensive property"},
                    {"id": "C", "text": "At equilibrium, $E_{\\text{cell}} = 0$ and $Q = K$"},
                    {"id": "D", "text": "$\\ln K = \\frac{nFE^\\circ_{\\text{cell}}}{RT}$"},
                ],
                "answer": ["A", "C", "D"],
                "marksCorrect": 4,
                "marksWrong": -2,
                "solution": "$E^\\circ$ is intensive (does not scale when doubling the reaction); $\\Delta G^\\circ$ is extensive.",
                "trapNote": "Multiplying a cell reaction by 2 doubles $\\Delta G^\\circ$ and $n$, leaving $E^\\circ$ unchanged.",
                "trapCategory": "misconception",
            },
            {
                "id": "Q3",
                "type": "numerical",
                "subject": "Maths",
                "chapter": "Definite Integration",
                "propId": "math-calc-king-rule",
                "stem": "Evaluate $\\int_0^3 x\\,dx$.",
                "answer": 4.5,
                "tolerance": 0.01,
                "marksCorrect": 4,
                "marksWrong": 0,
                "solution": "$\\left[\\frac{x^2}{2}\\right]_0^3 = 4.5$.",
                "trapNote": "Check both upper and lower limits carefully.",
                "trapCategory": "algebra_slip",
            },
            {
                "id": "Q4",
                "type": "matrix_match",
                "subject": "Physics",
                "chapter": "Thermodynamics",
                "propId": "phy-thermo-processes",
                "stem": "Match each ideal-gas process in List I with its invariant / property in List II.",
                "rows": [
                    {"id": "P", "text": "Isothermal"},
                    {"id": "Q", "text": "Isochoric"},
                    {"id": "R", "text": "Adiabatic"},
                    {"id": "S", "text": "Isobaric"},
                ],
                "cols": [
                    {"id": "1", "text": "$\\Delta U = 0$"},
                    {"id": "2", "text": "$W = 0$"},
                    {"id": "3", "text": "$Q = 0$"},
                    {"id": "4", "text": "$P = \\text{const}$"},
                ],
                "answer": {"P": ["1"], "Q": ["2"], "R": ["3"], "S": ["4"]},
                "marksCorrect": 4,
                "marksPerRow": 1,
                "marksWrong": 0,
                "solution": "Direct definitions of the four canonical thermodynamic processes for an ideal gas.",
                "trapNote": "Check sign conventions: $Q = \\Delta U + W$ (Physics) vs $\\Delta U = q + w$ (Chemistry).",
                "trapCategory": "sign_error",
            },
        ],
    }

    attempt_example = {
        "schemaVersion": 1,
        "examSlug": "jee-adv-mini-mock-01",
        "examTitle": "JEE Advanced Mini Mock — Mechanics & Electrochemistry",
        "startedAt": "2026-11-25T10:00:00Z",
        "submittedAt": "2026-11-25T10:32:10Z",
        "timeSpentSec": 1930,
        "responses": {
            "Q1": {"answer": "B", "status": "answered", "timeSpentSec": 75, "markedForReview": False, "visited": True},
            "Q2": {"answer": ["A", "C"], "status": "answered_marked", "timeSpentSec": 210, "markedForReview": True, "visited": True},
            "Q3": {"answer": "4.50", "status": "answered", "timeSpentSec": 50, "markedForReview": False, "visited": True},
            "Q4": {"answer": {"P": ["1"], "Q": ["2"], "R": ["3"], "S": ["1"]}, "status": "answered", "timeSpentSec": 145, "markedForReview": False, "visited": True},
        },
    }
    return {"specExample": spec_example, "attemptExample": attempt_example}


def cmd_schema(_args: argparse.Namespace) -> None:
    print(json.dumps(_build_schema_example(), indent=2, ensure_ascii=False))


def cmd_build(args: argparse.Namespace) -> None:
    spec_path = Path(args.spec).expanduser().resolve()
    if not spec_path.is_file():
        raise SystemExit(f"Spec file not found: {spec_path}")
    if not TEMPLATE_PATH.is_file():
        raise SystemExit(f"CBT template missing: {TEMPLATE_PATH}")

    try:
        raw_spec = json.loads(spec_path.read_text(encoding="utf-8"))
        spec = normalize_spec(raw_spec, spec_dir=spec_path.parent, inline_media=True)
    except Exception as exc:
        raise SystemExit(f"Invalid exam spec: {exc}")

    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    if "__EXAM_SPEC_JSON__" not in template:
        raise SystemExit(f"Template {TEMPLATE_PATH} missing __EXAM_SPEC_JSON__ placeholder")

    compiled = template.replace("__EXAM_TITLE__", html_lib.escape(spec["title"]))
    compiled = compiled.replace("__EXAM_SPEC_JSON__", safe_script_json(spec))

    out_path = (
        Path(args.out).expanduser().resolve()
        if args.out
        else (_resolve_exams_dir_core() / f"{spec['examSlug']}.html").resolve()
    )
    write_text_atomic(out_path, compiled)

    if not args.no_sync and out_path.parent.name == "exams":
        sync_hub_quiet(workspace=out_path.parent.parent)

    print(
        json.dumps(
            {
                "examSlug": spec["examSlug"],
                "title": spec["title"],
                "questionCount": len(spec["questions"]),
                "maxScore": spec["maxScore"],
                "output": str(out_path),
                "bytes": out_path.stat().st_size,
            },
            indent=2,
            ensure_ascii=False,
        )
    )


EXECUTION_CATEGORIES: frozenset[str] = frozenset(
    {"sign_error", "unit_trap", "algebra_slip", "careless", "off_by_one", "state_mutation_bug"}
)


def _median_positive(values: list[int]) -> float:
    pos = sorted(v for v in values if v > 0)
    if not pos:
        return 0.0
    mid = len(pos) // 2
    if len(pos) % 2 == 1:
        return float(pos[mid])
    return (pos[mid - 1] + pos[mid]) / 2.0


def _classify_autopsy_bucket(
    status: str,
    cat: str,
    q_time: int,
    slow_sec: int,
    time_ratio: float,
) -> str:
    """Bucket question into `knowledge`, `execution`, `time_sink`, or `solid`."""
    is_time_sink = (
        cat == "time_pressure"
        or q_time > slow_sec
        or (time_ratio >= 1.75 and q_time >= 60)
    )
    if status == "correct":
        return "time_sink" if (q_time > slow_sec and time_ratio >= 2.0) else "solid"
    if is_time_sink:
        return "time_sink"
    if status == "unattempted":
        return "knowledge"
    if cat in EXECUTION_CATEGORIES:
        return "execution"
    return "knowledge"


def grade_attempt(
    spec: dict[str, Any],
    attempt: dict[str, Any],
    *,
    kg_dir: Path | str | None = None,
    slow_sec: int = 180,
    no_log: bool = False,
    no_srs: bool = False,
    no_sync: bool = False,
) -> dict[str, Any]:
    """Deterministically grade a normalized exam `spec` against `attempt`, update FSRS-6
    (crediting `good` on solved `propId`s and `again` on missed `propId`s in-process),
    log mistakes, compute a 3-bucket `knowledge`/`execution`/`time_sink` autopsy, and flag
    3-strike escalations.
    """
    resolved_kg, _is_default_kg = resolve_kg_dir(kg_dir)
    slow_threshold = int(slow_sec)
    today = today_iso()
    attempt_id = f"ex-{uuid.uuid4().hex[:8]}"

    # First pass: score every question & compute median time spent across attempted questions.
    scored_rows: list[tuple[dict[str, Any], dict[str, Any], int, str]] = []
    attempted_times: list[int] = []
    for q in spec["questions"]:
        raw_ans, q_time, ui_status = _extract_attempt_entry(attempt, q["id"])
        sc = score_question(q, raw_ans)
        scored_rows.append((q, sc, q_time, ui_status))
        if sc["status"] != "unattempted" and q_time > 0:
            attempted_times.append(q_time)

    median_time = _median_positive(attempted_times)
    if median_time <= 0.0:
        median_time = max(30.0, slow_threshold / 2.0)

    total_score = 0.0
    max_score = 0.0
    correct_count = 0
    partial_count = 0
    wrong_count = 0
    unattempted_count = 0
    negative_lost = 0.0
    sum_q_time = 0

    slow_questions: list[str] = []
    negative_trap_questions: list[str] = []
    subject_bd: dict[str, dict[str, Any]] = {}
    chapter_bd: dict[str, dict[str, Any]] = {}
    q_details: list[dict[str, Any]] = []
    missed_items: list[tuple[dict[str, Any], dict[str, Any], int]] = []
    correct_items: list[tuple[dict[str, Any], dict[str, Any], int]] = []

    autopsy_buckets: dict[str, list[str]] = {"knowledge": [], "execution": [], "time_sink": []}
    ego_traps: list[dict[str, Any]] = []

    for q, sc, q_time, ui_status in scored_rows:
        qid = q["id"]
        total_score += sc["marks"]
        max_score += sc["maxMarks"]
        negative_lost += sc["negativeLost"]
        sum_q_time += q_time

        st = sc["status"]
        if st == "correct":
            correct_count += 1
        elif st == "partial":
            partial_count += 1
        elif st == "wrong":
            wrong_count += 1
        else:
            unattempted_count += 1

        if q_time > slow_threshold:
            slow_questions.append(qid)
        if sc["negativeLost"] > 0:
            negative_trap_questions.append(qid)

        if q["trapCategory"]:
            inferred_cat = q["trapCategory"]
        elif st == "wrong" and q_time > slow_threshold:
            inferred_cat = "time_pressure"
        else:
            inferred_cat = "concept_gap"

        time_ratio = round(q_time / median_time, 2) if median_time > 0 else 0.0
        bucket = _classify_autopsy_bucket(st, inferred_cat, q_time, slow_threshold, time_ratio)
        if bucket in autopsy_buckets:
            autopsy_buckets[bucket].append(qid)
        if bucket == "time_sink" and (st != "correct" or time_ratio >= 2.0):
            ego_traps.append(
                {
                    "id": qid,
                    "chapter": q["chapter"],
                    "status": st,
                    "marks": sc["marks"],
                    "timeSpentSec": q_time,
                    "timeRatio": time_ratio,
                }
            )

        subj = q["subject"]
        sb = subject_bd.setdefault(
            subj,
            {
                "score": 0.0,
                "maxScore": 0.0,
                "correct": 0,
                "partial": 0,
                "wrong": 0,
                "unattempted": 0,
                "negativeMarksLost": 0.0,
                "timeSpentSec": 0,
            },
        )
        sb["score"] = round(sb["score"] + sc["marks"], 2)
        sb["maxScore"] = round(sb["maxScore"] + sc["maxMarks"], 2)
        sb[st] += 1
        sb["negativeMarksLost"] = round(sb["negativeMarksLost"] + sc["negativeLost"], 2)
        sb["timeSpentSec"] += q_time

        ch = q["chapter"]
        cb = chapter_bd.setdefault(
            ch,
            {
                "subject": subj,
                "score": 0.0,
                "maxScore": 0.0,
                "correct": 0,
                "partial": 0,
                "wrong": 0,
                "unattempted": 0,
                "negativeMarksLost": 0.0,
                "timeSpentSec": 0,
            },
        )
        cb["score"] = round(cb["score"] + sc["marks"], 2)
        cb["maxScore"] = round(cb["maxScore"] + sc["maxMarks"], 2)
        cb[st] += 1
        cb["negativeMarksLost"] = round(cb["negativeMarksLost"] + sc["negativeLost"], 2)
        cb["timeSpentSec"] += q_time

        q_details.append(
            {
                "id": qid,
                "type": q["type"],
                "subject": subj,
                "chapter": ch,
                "propId": q["propId"],
                "status": st,
                "uiStatus": ui_status,
                "marks": sc["marks"],
                "maxMarks": sc["maxMarks"],
                "negativeLost": sc["negativeLost"],
                "timeSpentSec": q_time,
                "timeRatio": time_ratio,
                "isSlow": q_time > slow_threshold,
                "autopsyBucket": bucket,
                "studentAnswer": sc["studentAnswer"],
                "correctAnswer": sc["correctAnswer"],
                "detail": sc["detail"],
                "trapCategory": q["trapCategory"],
                "trapNote": q["trapNote"],
            }
        )

        if st in ("wrong", "partial"):
            missed_items.append((q, sc, q_time))
        elif st == "correct":
            correct_items.append((q, sc, q_time))

    for bd in subject_bd.values():
        bd["percentage"] = round((bd["score"] / bd["maxScore"]) * 100.0, 2) if bd["maxScore"] > 0 else 0.0
    for bd in chapter_bd.values():
        bd["percentage"] = round((bd["score"] / bd["maxScore"]) * 100.0, 2) if bd["maxScore"] > 0 else 0.0

    total_score = round(total_score, 2)
    max_score = round(max_score, 2)
    negative_lost = round(negative_lost, 2)
    attempted_count = correct_count + partial_count + wrong_count
    percentage = round((total_score / max_score) * 100.0, 2) if max_score > 0 else 0.0
    accuracy_pct = round((correct_count / attempted_count) * 100.0, 2) if attempted_count > 0 else 0.0
    time_spent_sec = int(round(float(attempt.get("timeSpentSec") or sum_q_time)))

    # Log mistakes
    mistake_ids: list[str] = []
    touched_chapters: set[str] = set()
    touched_props: set[str] = set()
    if not no_log:
        for q, sc, q_time in missed_items:
            if q["trapCategory"]:
                cat = q["trapCategory"]
            elif sc["status"] == "wrong" and q_time > slow_threshold:
                cat = "time_pressure"
            else:
                cat = "concept_gap"

            stem_snip = re.sub(r"\s+", " ", q["stem"]).strip()[:140]
            trigger_cue = q["trapNote"] or f"{q['chapter']} ({q['id']}): {stem_snip}"
            student_str = _format_answer(q["type"], sc["studentAnswer"])
            correct_str = (
                f"{q['range'][0]}..{q['range'][1]}"
                if q["type"] == "numerical" and "range" in q
                else _format_answer(q["type"], q["answer"])
            )
            wrong_move = f"Answered {student_str} ({sc['status']}, {sc['marks']:+g} marks, {q_time}s) — {sc['detail']}"
            correct_move = (q["trapNote"] or q["solution"] or f"Correct answer: {correct_str}")[:240]

            entry = {
                "id": f"m-{uuid.uuid4().hex[:8]}",
                "date": today,
                "subject": q["subject"],
                "chapter": q["chapter"],
                "propId": q["propId"],
                "category": cat,
                "triggerCue": trigger_cue,
                "wrongMove": wrong_move,
                "correctMove": correct_move,
                "sourceRef": f"exam:{spec['examSlug']}:{q['id']}",
            }
            logged = _log_mistake_entry(resolved_kg, entry)
            mistake_ids.append(str(logged.get("id") or entry["id"]))
            touched_chapters.add(q["chapter"].lower())
            if q["propId"]:
                touched_props.add(q["propId"].lower())

    # Update SRS in-process (zero subprocess overhead; credits `good` on solved & `again` on missed).
    srs_updated_ids: list[str] = []
    srs_missed_ids: list[str] = []
    srs_credited_ids: list[str] = []
    srs_fire_credits: list[dict[str, Any]] = []
    srs_errors: list[str] = []

    if not no_srs:
        scripts_dir = str(PKG_ROOT / "scripts")
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)
        try:
            import srs_engine  # type: ignore
        except Exception as exc:
            srs_engine = None
            srs_errors.append(f"srs_engine import failed: {exc}")

        if srs_engine is not None:
            missed_props_seen: set[str] = set()
            for q, sc, q_time in missed_items:
                pid = q["propId"]
                if not pid or pid in missed_props_seen:
                    continue
                missed_props_seen.add(pid)
                clean_stem = re.sub(r"\s+", " ", q["stem"]).strip()[:90]
                prop_title = q["propTitle"] or f"{q['chapter']}: {clean_stem}"
                note_txt = f"Missed {q['id']} in {spec['examSlug']} ({sc['status']}, {sc['marks']:+g})"
                try:
                    srs_engine.record_review(
                        resolved_kg,
                        pid,
                        "again",
                        title=prop_title,
                        subject=q["subject"],
                        chapter=q["chapter"],
                        notes=note_txt,
                        misconception=(q["trapCategory"] == "misconception"),
                        duration_ms=q_time * 1000 if q_time > 0 else None,
                        no_sync=True,
                    )
                    srs_updated_ids.append(pid)
                    srs_missed_ids.append(pid)
                except Exception as exc:
                    srs_errors.append(f"{pid}: {exc}")

            credited_props_seen: set[str] = set()
            for q, sc, q_time in correct_items:
                pid = q["propId"]
                if not pid or pid in missed_props_seen or pid in credited_props_seen:
                    continue
                credited_props_seen.add(pid)
                clean_stem = re.sub(r"\s+", " ", q["stem"]).strip()[:90]
                prop_title = q["propTitle"] or f"{q['chapter']}: {clean_stem}"
                try:
                    res = srs_engine.record_review(
                        resolved_kg,
                        pid,
                        "good",
                        title=prop_title,
                        subject=q["subject"],
                        chapter=q["chapter"],
                        duration_ms=q_time * 1000 if q_time > 0 else None,
                        no_sync=True,
                    )
                    srs_updated_ids.append(pid)
                    srs_credited_ids.append(pid)
                    if res.get("fireCredits"):
                        srs_fire_credits.extend(res["fireCredits"])
                except Exception as exc:
                    srs_errors.append(f"{pid}: {exc}")

    # 3-strike escalation check across mistakes.jsonl
    three_strike_escalations: list[dict[str, Any]] = []
    try:
        scripts_dir = str(PKG_ROOT / "scripts")
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)
        import mistake_analytics  # type: ignore

        all_mistakes = mistake_analytics.load_mistakes(resolved_kg)
        msum = mistake_analytics.summarize_mistakes(all_mistakes, top=5)
        for esc in msum.get("escalations") or []:
            ch_low = str(esc.get("chapter") or "").lower()
            pr_low = str(esc.get("propId") or "").lower()
            if (not touched_chapters and not touched_props) or (ch_low in touched_chapters) or (pr_low and pr_low in touched_props):
                three_strike_escalations.append(esc)
    except Exception:
        pass

    autopsy_summary = {
        "medianTimeSec": round(median_time, 1),
        "buckets": autopsy_buckets,
        "counts": {k: len(v) for k, v in autopsy_buckets.items()},
        "egoTraps": ego_traps,
    }

    attempt_summary_row = {
        "id": attempt_id,
        "date": today,
        "examSlug": spec["examSlug"],
        "examTitle": spec["title"],
        "examType": spec["examType"],
        "durationMinutes": spec["durationMinutes"],
        "timeSpentSec": time_spent_sec,
        "score": total_score,
        "maxScore": max_score,
        "percentage": percentage,
        "accuracyPct": accuracy_pct,
        "correctCount": correct_count,
        "partialCount": partial_count,
        "wrongCount": wrong_count,
        "unattemptedCount": unattempted_count,
        "negativeMarksLost": negative_lost,
        "slowQuestions": slow_questions,
        "subjectBreakdown": subject_bd,
        "autopsyCounts": autopsy_summary["counts"],
        "escalationsCount": len(three_strike_escalations),
        "mistakesLogged": len(mistake_ids),
        "srsUpdated": len(srs_updated_ids),
    }

    if not no_log:
        append_jsonl(resolved_kg / "exam-attempts.jsonl", attempt_summary_row)
        try:
            from syntopik.learner_model import record_telemetry_event

            for q, sc, q_time, _ui in scored_rows:
                st = sc["status"]
                if q["trapCategory"]:
                    err_cat = q["trapCategory"]
                elif st == "wrong" and q_time > slow_threshold:
                    err_cat = "time_pressure"
                else:
                    err_cat = "concept_gap"
                record_telemetry_event(
                    resolved_kg,
                    domain=str(q.get("subject") or "General"),
                    prop_id=str(q.get("propId") or "") or None,
                    kind="exam",
                    correct=(st == "correct"),
                    difficulty=1560,
                    confidence=None,
                    duration_ms=int(q_time * 1000) if q_time > 0 else None,
                    hints_used=0,
                    mistake_category=err_cat if st in ("wrong", "partial") else None,
                    pf_outcome=None,
                    skipped=(st == "unattempted"),
                )
        except Exception:
            pass

    if not no_sync and (not no_log or not no_srs):
        sync_hub_quiet(kg_dir=resolved_kg)

    return {
        **attempt_summary_row,
        "negativeTrapQuestions": negative_trap_questions,
        "chapterBreakdown": chapter_bd,
        "autopsy": autopsy_summary,
        "threeStrikeEscalations": three_strike_escalations,
        "mistakeIds": mistake_ids,
        "srsUpdatedProps": srs_updated_ids,
        "srsMissedProps": srs_missed_ids,
        "srsCreditedProps": srs_credited_ids,
        "fireCredits": srs_fire_credits,
        "srsErrors": srs_errors,
        "questions": q_details,
    }


def cmd_grade(args: argparse.Namespace) -> None:
    spec_path = Path(args.spec).expanduser().resolve()
    attempt_path = Path(args.attempt).expanduser().resolve()
    if not spec_path.is_file():
        raise SystemExit(f"Spec file not found: {spec_path}")
    if not attempt_path.is_file():
        raise SystemExit(f"Attempt file not found: {attempt_path}")

    try:
        raw_spec = json.loads(spec_path.read_text(encoding="utf-8"))
        spec = normalize_spec(raw_spec, spec_dir=spec_path.parent, inline_media=False)
        attempt = json.loads(attempt_path.read_text(encoding="utf-8"))
        if not isinstance(attempt, dict):
            raise ValueError("Attempt JSON must be an object")
    except Exception as exc:
        raise SystemExit(f"Failed to load spec/attempt: {exc}")

    kg_dir, _ = resolve_kg_dir(getattr(args, "kg_dir", None))
    full_report = grade_attempt(
        spec,
        attempt,
        kg_dir=kg_dir,
        slow_sec=int(getattr(args, "slow_sec", 180)),
        no_log=bool(getattr(args, "no_log", False)),
        no_srs=bool(getattr(args, "no_srs", False)),
        no_sync=bool(getattr(args, "no_sync", False)),
    )

    if getattr(args, "out_report", None):
        rp = Path(args.out_report).expanduser().resolve()
        write_text_atomic(rp, json.dumps(full_report, indent=2, ensure_ascii=False) + "\n")

    if getattr(args, "json", False):
        print(json.dumps(full_report, indent=2, ensure_ascii=False))
    else:
        print(f"=== {spec['title']} ({spec['examType']}) ===")
        print(
            f"Score: {full_report['score']:g} / {full_report['maxScore']:g} ({full_report['percentage']}%) | "
            f"Accuracy: {full_report['accuracyPct']}% | Time: {full_report['timeSpentSec']}s"
        )
        neg_lost = full_report["negativeMarksLost"]
        neg_label = f"-{neg_lost:g}" if neg_lost > 0 else "0"
        print(
            f"Counts: {full_report['correctCount']} correct, {full_report['partialCount']} partial, "
            f"{full_report['wrongCount']} wrong, {full_report['unattemptedCount']} unattempted | "
            f"Negative Marks Lost: {neg_label}"
        )
        ac = full_report["autopsy"]["counts"]
        print(
            f"Autopsy (median {full_report['autopsy']['medianTimeSec']}s): "
            f"knowledge={ac['knowledge']} · execution={ac['execution']} · time_sink={ac['time_sink']}"
        )
        if full_report["slowQuestions"]:
            print(f"Slow Questions (>{args.slow_sec}s): {', '.join(full_report['slowQuestions'])}")
        if full_report["negativeTrapQuestions"]:
            print(f"Negative-Mark Traps: {', '.join(full_report['negativeTrapQuestions'])}")
        if full_report["threeStrikeEscalations"]:
            esc_keys = ", ".join(f"{e['key']} ({e['count']}x)" for e in full_report["threeStrikeEscalations"][:4])
            print(f"3-Strike Escalations: {esc_keys}")
        print("\nSubject Breakdown:")
        for subj, bd in full_report["subjectBreakdown"].items():
            print(
                f"  - {subj}: {bd['score']:g}/{bd['maxScore']:g} ({bd['percentage']}%) · "
                f"{bd['correct']}C / {bd['partial']}P / {bd['wrong']}W / {bd['unattempted']}U · "
                f"-{bd['negativeMarksLost']:g} neg · {bd['timeSpentSec']}s"
            )
        print(
            f"\nLogged: {full_report['mistakesLogged']} mistakes -> {kg_dir / 'mistakes.jsonl'} | "
            f"SRS updated: {full_report['srsUpdated']} prop(s) "
            f"({len(full_report['srsCreditedProps'])} good, {len(full_report['srsMissedProps'])} again)"
        )
        if full_report["srsErrors"]:
            print(f"SRS Warnings: {'; '.join(full_report['srsErrors'])}", file=sys.stderr)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Offline NTA JEE CBT Exam Simulator — Builder & Deterministic Grader"
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_schema = sub.add_parser("schema", help="Print example spec.json and attempt.json schemas")
    p_schema.set_defaults(func=cmd_schema)

    p_build = sub.add_parser("build", help="Compile a spec.json into a standalone offline NTA CBT HTML file")
    p_build.add_argument("spec", help="Path to exam spec JSON")
    p_build.add_argument("--out", help="Output HTML path (default: exams/<examSlug>.html)")
    p_build.add_argument("--no-sync", action="store_true", help="Skip syncing dashboard graph-data.js")
    p_build.set_defaults(func=cmd_build)

    p_grade = sub.add_parser("grade", help="Deterministically grade attempt.json against spec.json")
    p_grade.add_argument("spec", help="Path to exam spec JSON")
    p_grade.add_argument("attempt", help="Path to exported attempt JSON")
    p_grade.add_argument("--kg-dir", help="Override knowledge-graph directory (for sandboxed testing)")
    p_grade.add_argument("--no-sync", action="store_true", help="Skip syncing dashboard graph-data.js")
    p_grade.add_argument("--no-log", action="store_true", help="Do not append mistakes.jsonl or exam-attempts.jsonl")
    p_grade.add_argument("--no-srs", action="store_true", help="Do not push missed propIds to srs_engine.py")
    p_grade.add_argument("--slow-sec", type=int, default=180, help="Slow-question threshold in seconds (default: 180)")
    p_grade.add_argument("--out-report", help="Optional path to write full JSON grading report")
    p_grade.add_argument("--json", action="store_true", help="Emit full grading report as JSON")
    p_grade.set_defaults(func=cmd_grade)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
