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
import copy
import html as html_lib
import json
import math
import re
import sys
import uuid
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from syntopik.paths import (  # noqa: E402
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

VALID_QUESTION_TYPES = (
    "single_correct",
    "multi_correct",
    "numerical",
    "matrix_match",
    "assertion_reason",
    "vignette_stem",
)
VALID_MULTI_MARKING_PROFILES = (
    "jee_adv_msq",
    "gate_msq",
    "strict_all_or_nothing",
    "proportional",
)
VALID_MATRIX_MODES = ("grid", "code_options")
VALID_AR_VARIANTS = ("4opt", "5opt")

_AR_TRUTH_TABLE: dict[str, tuple[bool, bool, bool]] = {
    "A": (True, True, True),
    "B": (True, True, False),
    "C": (True, False, False),
    "D": (False, True, False),
    "E": (False, False, False),
}

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


def _normalize_options(
    raw_items: Any,
    default_ids: list[str],
    qid: str,
    distractor_reasons: Any = None,
) -> list[dict[str, Any]]:
    """Normalize option objects while preserving optional `whyWrong`, `trapCategory`, and `confusedWith`."""
    out: list[dict[str, Any]] = []
    if isinstance(raw_items, dict):
        for k, v in raw_items.items():
            if isinstance(v, dict):
                row: dict[str, Any] = {"id": str(k).strip().upper(), "text": str(v.get("text", ""))}
                for extra_k in ("whyWrong", "trapCategory", "confusedWith"):
                    if v.get(extra_k):
                        row[extra_k] = str(v[extra_k]).strip()
                out.append(row)
            else:
                out.append({"id": str(k).strip().upper(), "text": str(v)})
    elif isinstance(raw_items, list):
        for idx, item in enumerate(raw_items):
            if isinstance(item, dict):
                item_id = str(item.get("id") or (default_ids[idx] if idx < len(default_ids) else str(idx + 1))).strip().upper()
                row = {"id": item_id, "text": str(item.get("text", ""))}
                for extra_k in ("whyWrong", "trapCategory", "confusedWith"):
                    if item.get(extra_k):
                        row[extra_k] = str(item[extra_k]).strip()
                out.append(row)
            else:
                item_id = default_ids[idx] if idx < len(default_ids) else str(idx + 1)
                out.append({"id": item_id, "text": str(item)})
    else:
        raise ValueError(f"Question '{qid}': 'options' must be a list or dict")
    if not out:
        raise ValueError(f"Question '{qid}': 'options' cannot be empty")

    # Merge optional flat `distractorReasons` mapping onto normalized options
    if isinstance(distractor_reasons, dict):
        by_id = {o["id"]: o for o in out}
        for dk, dv in distractor_reasons.items():
            dk_u = str(dk).strip().upper()
            target_opt = by_id.get(dk_u)
            if target_opt is None or dv is None:
                continue
            if isinstance(dv, dict):
                if dv.get("whyWrong") and not target_opt.get("whyWrong"):
                    target_opt["whyWrong"] = str(dv["whyWrong"]).strip()
                if dv.get("trapCategory") and not target_opt.get("trapCategory"):
                    target_opt["trapCategory"] = str(dv["trapCategory"]).strip()
                if dv.get("confusedWith") and not target_opt.get("confusedWith"):
                    target_opt["confusedWith"] = str(dv["confusedWith"]).strip()
            else:
                reason_txt = str(dv).strip()
                if reason_txt and not target_opt.get("whyWrong"):
                    target_opt["whyWrong"] = reason_txt

    # Validate any option-level trapCategory
    for opt in out:
        tc_raw = str(opt.get("trapCategory") or "").strip()
        if tc_raw:
            try:
                opt["trapCategory"] = normalize_mistake_category(tc_raw)
            except ValueError as exc:
                raise ValueError(
                    f"Question '{qid}' option '{opt['id']}': invalid trapCategory '{tc_raw}' "
                    f"(expected one of {VALID_MISTAKE_CATEGORIES})"
                ) from exc
        elif "trapCategory" in opt:
            opt.pop("trapCategory", None)
    return out


_SEALED_QUESTION_FIELDS: tuple[str, ...] = (
    "answer",
    "range",
    "tolerance",
    "solution",
    "trapCategory",
    "trapNote",
    "causalLinkExplanation",
    "rowExplanations",
    "rowAnswer",
)
_SEALED_BLIND_QUESTION_FIELDS: tuple[str, ...] = ("chapter", "propId")


def strip_presubmit_bundle(spec: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Strip answer/solution/trap/distractor (and blind chapter/propId) fields from a pre-submit exam bundle.

    Returns `(public_bundle, sealed_post_payload)` where:
    - `sealed_post_payload[qid][opt_id]` stores option-level `whyWrong` / `trapCategory` / `confusedWith`
    - `sealed_post_payload["_questions"][qid]` stores stripped question-level answer/solution/trap/blind fields
      so post-submit review/grading unlocks them ONLY after `state.submitted === true`.
    """
    bundle = copy.deepcopy(spec)
    blind_mode = bool(bundle.get("blindChapterLabels") is True)
    sealed: dict[str, Any] = {}
    sealed_questions: dict[str, dict[str, Any]] = {}
    q_fields = _SEALED_QUESTION_FIELDS + (_SEALED_BLIND_QUESTION_FIELDS if blind_mode else ())
    for q in bundle.get("questions") or []:
        if not isinstance(q, dict):
            continue
        qid = str(q.get("id") or "")
        q.pop("distractorReasons", None)
        q_sealed: dict[str, Any] = {}
        for qk in q_fields:
            if qk in q:
                val = q.pop(qk, None)
                if val is not None:
                    q_sealed[qk] = val
        if q_sealed and qid:
            sealed_questions[qid] = q_sealed
        opts = q.get("options")
        if not isinstance(opts, list):
            continue
        q_map: dict[str, dict[str, str]] = {}
        for opt in opts:
            if not isinstance(opt, dict):
                continue
            oid = str(opt.get("id") or "")
            meta: dict[str, str] = {}
            for k in ("whyWrong", "trapCategory", "confusedWith"):
                val = opt.pop(k, None)
                if val is not None and str(val).strip():
                    meta[k] = str(val).strip()
            if meta and oid:
                q_map[oid] = meta
        if q_map and qid:
            sealed[qid] = q_map
    if sealed_questions:
        sealed["_questions"] = sealed_questions
    return bundle, sealed


def _default_ar_options(labels: list[str], ar_variant: str) -> list[dict[str, str]]:
    l1, l2 = labels[0], labels[1]
    opts = [
        {"id": "A", "text": f"Both {l1} and {l2} are true, and {l2} is the correct explanation of {l1}."},
        {"id": "B", "text": f"Both {l1} and {l2} are true, but {l2} is NOT the correct explanation of {l1}."},
        {"id": "C", "text": f"{l1} is true, but {l2} is false."},
        {"id": "D", "text": f"{l1} is false, but {l2} is true."},
    ]
    if ar_variant == "5opt":
        opts.append({"id": "E", "text": f"Both {l1} and {l2} are false."})
    return opts


def _ar_error_kind(chosen: str, expected: str) -> str | None:
    c = str(chosen or "").strip().upper()
    e = str(expected or "").strip().upper()
    if not c or not e or c == e:
        return None
    if {c, e} == {"A", "B"}:
        return "causal_link_trap"
    tc = _AR_TRUTH_TABLE.get(c)
    te = _AR_TRUTH_TABLE.get(e)
    if tc is not None and te is not None:
        a_diff = tc[0] != te[0]
        r_diff = tc[1] != te[1]
        if a_diff and r_diff:
            return "double_truth_flip"
        if a_diff:
            return "assertion_truth_error"
        if r_diff:
            return "reason_truth_error"
    return "causal_link_trap"


def _normalize_vignette_entry(
    raw_v: dict[str, Any],
    fallback_id: str,
    spec_dir: Path,
    inline_media: bool,
) -> dict[str, Any]:
    vid = str(raw_v.get("id") or fallback_id).strip()
    if not vid:
        raise ValueError("Vignette requires a non-empty 'id'")
    body = str(raw_v.get("body") or raw_v.get("stem") or "").strip()
    if not body:
        raise ValueError(f"Vignette '{vid}' requires non-empty 'body' or 'stem'")
    v_out: dict[str, Any] = {
        "id": vid,
        "title": str(raw_v.get("title") or vid).strip(),
        "kind": str(raw_v.get("kind") or "passage").strip() or "passage",
        "body": body,
    }
    if raw_v.get("figureCaption"):
        v_out["figureCaption"] = str(raw_v["figureCaption"])
    if raw_v.get("figure"):
        v_out["figure"] = str(raw_v["figure"])
        if inline_media:
            svg_str, data_uri = inline_figure(str(raw_v["figure"]), spec_dir, vid)
            if svg_str:
                v_out["figureSvg"] = svg_str
            if data_uri:
                v_out["figureDataUri"] = data_uri
    return v_out


def normalize_spec(raw_spec: dict[str, Any], spec_dir: Path | None = None, inline_media: bool = False) -> dict[str, Any]:
    """Validate and normalize an exam spec dictionary across all 6 universal question types."""
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

    base_dir = spec_dir or ROOT

    # 1. Normalize top-level `vignettes` (list or dict)
    vignettes_map: dict[str, dict[str, Any]] = {}
    raw_vignettes = raw_spec.get("vignettes")
    if isinstance(raw_vignettes, list):
        for v_idx, rv in enumerate(raw_vignettes, start=1):
            if not isinstance(rv, dict):
                raise ValueError(f"Vignette #{v_idx} must be an object")
            nv = _normalize_vignette_entry(rv, f"V{v_idx}", base_dir, inline_media)
            vignettes_map[nv["id"]] = nv
    elif isinstance(raw_vignettes, dict):
        for vk, rv in raw_vignettes.items():
            if not isinstance(rv, dict):
                raise ValueError(f"Vignette '{vk}' must be an object")
            nv = _normalize_vignette_entry({**rv, "id": rv.get("id") or vk}, str(vk), base_dir, inline_media)
            vignettes_map[nv["id"]] = nv

    # 2. Flatten any inline `vignette_stem` questions into flat question sequence
    flattened_in: list[dict[str, Any]] = []
    for idx, q in enumerate(questions_in, start=1):
        if not isinstance(q, dict):
            raise ValueError(f"Question #{idx} must be an object")
        qtype_raw = str(q.get("type") or "").strip()
        if qtype_raw == "vignette_stem":
            vid = str(q.get("id") or f"V{idx}").strip()
            nv = _normalize_vignette_entry(q, vid, base_dir, inline_media)
            vignettes_map[nv["id"]] = nv
            sub_qs = q.get("subQuestions")
            if not isinstance(sub_qs, list) or not sub_qs:
                raise ValueError(f"Vignette question '{vid}': non-empty 'subQuestions' list is required")
            parent_subj = str(q.get("subject") or "General").strip()
            parent_chap = str(q.get("chapter") or "General").strip()
            parent_prop = str(q.get("propId") or "").strip()
            for sub_i, sq in enumerate(sub_qs, start=1):
                if not isinstance(sq, dict):
                    raise ValueError(f"Vignette '{vid}' sub-question #{sub_i} must be an object")
                if str(sq.get("type") or "").strip() == "vignette_stem":
                    raise ValueError(f"Vignette '{vid}': nested vignette_stem is not allowed")
                merged = dict(sq)
                merged["id"] = str(sq.get("id") or f"{nv['id']}.{sub_i}").strip()
                if not merged.get("subject"):
                    merged["subject"] = parent_subj
                if not merged.get("chapter"):
                    merged["chapter"] = parent_chap
                if not merged.get("propId") and parent_prop:
                    merged["propId"] = parent_prop
                merged["vignetteId"] = nv["id"]
                merged["vignetteOrder"] = sub_i
                merged["vignetteCount"] = len(sub_qs)
                flattened_in.append(merged)
        else:
            flattened_in.append(q)

    # Pre-count top-level vignetteId group sizes
    vig_group_counts: Counter[str] = Counter()
    for q in flattened_in:
        vid_ref = str(q.get("vignetteId") or "").strip()
        if vid_ref:
            if vid_ref not in vignettes_map:
                raise ValueError(f"Question '{q.get('id') or '?'}': unknown vignetteId '{vid_ref}'")
            vig_group_counts[vid_ref] += 1
    vig_seen_order: Counter[str] = Counter()

    seen_ids: set[str] = set()
    norm_questions: list[dict[str, Any]] = []
    max_score = 0.0

    for idx, q in enumerate(flattened_in, start=1):
        qid = str(q.get("id") or f"Q{idx}").strip()
        if qid in seen_ids:
            raise ValueError(f"Duplicate question id '{qid}'")
        seen_ids.add(qid)

        qtype = str(q.get("type") or "").strip()
        if qtype not in VALID_QUESTION_TYPES or qtype == "vignette_stem":
            raise ValueError(f"Question '{qid}': invalid type '{qtype}' (expected one of {VALID_QUESTION_TYPES})")

        stem = str(q.get("stem") or "").strip()
        if qtype == "assertion_reason":
            assertion_txt = str(q.get("assertion") or "").strip()
            reason_txt = str(q.get("reason") or "").strip()
            if not assertion_txt or not reason_txt:
                raise ValueError(f"Question '{qid}': assertion_reason requires non-empty 'assertion' and 'reason'")
            raw_labels = q.get("statementLabels")
            if isinstance(raw_labels, (list, tuple)) and len(raw_labels) == 2 and all(str(x).strip() for x in raw_labels):
                stmt_labels = [str(raw_labels[0]).strip(), str(raw_labels[1]).strip()]
            else:
                stmt_labels = ["Assertion (A)", "Reason (R)"]
            if not stem:
                stem = f"Read {stmt_labels[0]} and {stmt_labels[1]} below and select the correct option."
        elif not stem:
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

        vid_ref = str(q.get("vignetteId") or "").strip()
        if vid_ref:
            vig_seen_order[vid_ref] += 1
            nq["vignetteId"] = vid_ref
            nq["vignetteOrder"] = int(q.get("vignetteOrder") or vig_seen_order[vid_ref])
            nq["vignetteCount"] = int(q.get("vignetteCount") or vig_group_counts[vid_ref])

        if q.get("figureCaption"):
            nq["figureCaption"] = str(q["figureCaption"])
        if q.get("figure"):
            nq["figure"] = str(q["figure"])
            if inline_media:
                svg_str, data_uri = inline_figure(str(q["figure"]), base_dir, qid)
                if svg_str:
                    nq["figureSvg"] = svg_str
                if data_uri:
                    nq["figureDataUri"] = data_uri

        if qtype == "single_correct":
            opts = _normalize_options(q.get("options"), ["A", "B", "C", "D", "E"], qid, q.get("distractorReasons"))
            opt_ids = {o["id"] for o in opts}
            ans = str(q.get("answer") or "").strip().upper()
            if ans not in opt_ids:
                raise ValueError(f"Question '{qid}': single_correct answer '{ans}' not in options {sorted(opt_ids)}")
            nq["options"] = opts
            nq["answer"] = ans
            nq["marksCorrect"] = float(q.get("marksCorrect", 4))
            nq["marksWrong"] = float(q.get("marksWrong", -1))

        elif qtype == "assertion_reason":
            ar_variant = str(q.get("arVariant") or "4opt").strip().lower()
            if ar_variant not in VALID_AR_VARIANTS:
                raise ValueError(f"Question '{qid}': invalid arVariant '{ar_variant}' (expected one of {VALID_AR_VARIANTS})")
            raw_opts = q.get("options") if q.get("options") else _default_ar_options(stmt_labels, ar_variant)
            opts = _normalize_options(raw_opts, ["A", "B", "C", "D", "E"], qid, q.get("distractorReasons"))
            opt_ids = {o["id"] for o in opts}
            ans = str(q.get("answer") or "").strip().upper()
            if ans not in opt_ids:
                raise ValueError(f"Question '{qid}': assertion_reason answer '{ans}' not in options {sorted(opt_ids)}")
            nq["assertion"] = assertion_txt
            nq["reason"] = reason_txt
            nq["statementLabels"] = stmt_labels
            nq["arVariant"] = ar_variant
            nq["options"] = opts
            nq["answer"] = ans
            nq["marksCorrect"] = float(q.get("marksCorrect", 4))
            nq["marksWrong"] = float(q.get("marksWrong", -1))
            if q.get("causalLinkExplanation"):
                nq["causalLinkExplanation"] = str(q["causalLinkExplanation"]).strip()

        elif qtype == "multi_correct":
            opts = _normalize_options(q.get("options"), ["A", "B", "C", "D", "E"], qid, q.get("distractorReasons"))
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
            m_profile = str(q.get("markingProfile") or "jee_adv_msq").strip().lower()
            if m_profile not in VALID_MULTI_MARKING_PROFILES:
                raise ValueError(
                    f"Question '{qid}': invalid markingProfile '{m_profile}' (expected one of {VALID_MULTI_MARKING_PROFILES})"
                )
            profile_defaults = {
                "jee_adv_msq": (4.0, -2.0, True),
                "gate_msq": (2.0, 0.0, False),
                "strict_all_or_nothing": (4.0, -2.0, False),
                "proportional": (4.0, 0.0, True),
            }
            def_mc, def_mw, def_pm = profile_defaults[m_profile]
            nq["options"] = opts
            nq["answer"] = ans_list
            nq["markingProfile"] = m_profile
            nq["marksCorrect"] = float(q["marksCorrect"]) if q.get("marksCorrect") is not None else def_mc
            nq["marksWrong"] = float(q["marksWrong"]) if q.get("marksWrong") is not None else def_mw
            nq["partialMarking"] = bool(q["partialMarking"]) if q.get("partialMarking") is not None else def_pm
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
            matrix_mode = str(q.get("matrixMode") or "grid").strip().lower()
            if matrix_mode not in VALID_MATRIX_MODES:
                raise ValueError(f"Question '{qid}': invalid matrixMode '{matrix_mode}' (expected one of {VALID_MATRIX_MODES})")
            nq["rows"] = rows
            nq["cols"] = cols
            nq["matrixMode"] = matrix_mode
            if isinstance(q.get("rowExplanations"), dict) and q["rowExplanations"]:
                nq["rowExplanations"] = {
                    str(rk).strip().upper(): str(rv).strip()
                    for rk, rv in q["rowExplanations"].items()
                    if str(rv or "").strip()
                }

            raw_ans = q.get("answer")
            if matrix_mode == "code_options" and q.get("options") and isinstance(raw_ans, str):
                opts = _normalize_options(q.get("options"), ["A", "B", "C", "D", "E"], qid, q.get("distractorReasons"))
                opt_ids = {o["id"] for o in opts}
                ans_code = raw_ans.strip().upper()
                if ans_code not in opt_ids:
                    raise ValueError(f"Question '{qid}': matrix_match code_options answer '{ans_code}' not in {sorted(opt_ids)}")
                nq["options"] = opts
                nq["answer"] = ans_code
                if isinstance(q.get("rowAnswer"), dict) and q["rowAnswer"]:
                    nq["rowAnswer"] = {
                        str(rk).strip().upper(): sorted({str(v).strip().upper() for v in ([rv] if isinstance(rv, (str, int, float)) else list(rv)) if str(v).strip()})
                        for rk, rv in q["rowAnswer"].items()
                    }
                if isinstance(q.get("optionRowMap"), dict) and q["optionRowMap"]:
                    nq["optionRowMap"] = {
                        str(ok).strip().upper(): {
                            str(rk).strip().upper(): sorted({str(v).strip().upper() for v in ([rv] if isinstance(rv, (str, int, float)) else list(rv)) if str(v).strip()})
                            for rk, rv in ov.items()
                        }
                        for ok, ov in q["optionRowMap"].items()
                        if isinstance(ov, dict)
                    }
                nq["matrixSinglePerRow"] = bool(q.get("matrixSinglePerRow", True))
                nq["partialMarking"] = False
                nq["marksCorrect"] = float(q.get("marksCorrect", 4))
                nq["marksWrong"] = float(q.get("marksWrong", -1))
            else:
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

    out_spec: dict[str, Any] = {
        "schemaVersion": 1,
        "examSlug": exam_slug,
        "title": title,
        "examType": exam_type,
        "durationMinutes": int(duration_minutes) if duration_minutes.is_integer() else duration_minutes,
        "instructions": [str(x) for x in (raw_spec.get("instructions") or [])],
        "maxScore": round(max_score, 2),
        "vignettes": vignettes_map,
        "questions": norm_questions,
    }
    if raw_spec.get("blindChapterLabels"):
        out_spec["blindChapterLabels"] = True
    if raw_spec.get("preMortemShield"):
        out_spec["preMortemShield"] = raw_spec["preMortemShield"]
    return out_spec


def _is_empty_answer(qtype: str, ans: Any) -> bool:
    if ans is None:
        return True
    if qtype in ("single_correct", "assertion_reason"):
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
        if isinstance(ans, str):
            return ans.strip() == ""
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
    if qtype in ("single_correct", "assertion_reason"):
        return str(ans).strip().upper()
    if qtype == "multi_correct":
        vals = [ans] if isinstance(ans, str) else list(ans)
        return ", ".join(sorted({str(v).strip().upper() for v in vals if str(v).strip()}))
    if qtype == "numerical":
        return str(ans).strip()
    if qtype == "matrix_match":
        if isinstance(ans, str):
            return ans.strip().upper()
        if isinstance(ans, dict):
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
    """Deterministically score a single normalized question across all question types."""
    qtype = q["type"]
    max_marks = float(q["marksCorrect"])
    m_wrong = float(q.get("marksWrong", 0))
    m_unatt = float(q.get("marksUnattempted", 0))

    if _is_empty_answer(qtype, raw_ans):
        base_unatt: dict[str, Any] = {
            "status": "unattempted",
            "marks": round(m_unatt, 2),
            "maxMarks": round(max_marks, 2),
            "negativeLost": round(abs(m_unatt), 2) if m_unatt < 0 else 0.0,
            "studentAnswer": None,
            "correctAnswer": q["answer"],
            "detail": "unattempted",
        }
        if qtype == "multi_correct":
            base_unatt["wrongSelected"] = []
            base_unatt["missedCorrect"] = sorted(q["answer"])
        elif qtype == "assertion_reason":
            base_unatt["arErrorKind"] = None
        elif qtype == "matrix_match":
            row_keys = list(q["answer"].keys()) if isinstance(q["answer"], dict) else [r["id"] for r in (q.get("rows") or [])]
            base_unatt["rowResults"] = {rk: "blank" for rk in row_keys}
        return base_unatt

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

    if qtype == "assertion_reason":
        chosen = str(raw_ans).strip().upper()
        expected = str(q["answer"]).strip().upper()
        ok = chosen == expected
        marks = max_marks if ok else m_wrong
        err_kind = None if ok else _ar_error_kind(chosen, expected)
        return {
            "status": "correct" if ok else "wrong",
            "marks": round(marks, 2),
            "maxMarks": round(max_marks, 2),
            "negativeLost": round(abs(marks), 2) if marks < 0 else 0.0,
            "studentAnswer": chosen,
            "correctAnswer": expected,
            "arErrorKind": err_kind,
            "detail": "exact match" if ok else f"chose {chosen}, expected {expected} ({err_kind})",
        }

    if qtype == "multi_correct":
        vals = [raw_ans] if isinstance(raw_ans, str) else list(raw_ans)
        chosen_set = {str(v).strip().upper() for v in vals if str(v).strip()}
        chosen_sorted = sorted(chosen_set)
        correct_set = set(q["answer"])
        wrong_chosen = sorted(chosen_set - correct_set)
        missed = sorted(correct_set - chosen_set)

        if wrong_chosen:
            return {
                "status": "wrong",
                "marks": round(m_wrong, 2),
                "maxMarks": round(max_marks, 2),
                "negativeLost": round(abs(m_wrong), 2) if m_wrong < 0 else 0.0,
                "studentAnswer": chosen_sorted,
                "correctAnswer": q["answer"],
                "wrongSelected": wrong_chosen,
                "missedCorrect": missed,
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
                "wrongSelected": [],
                "missedCorrect": [],
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
                "wrongSelected": [],
                "missedCorrect": missed,
                "detail": "incomplete selection (partial marking disabled)",
            }

        # Official JEE Advanced partial marking or proportional profile
        n_total = len(correct_set)
        n_chosen = len(chosen_set)
        m_profile = str(q.get("markingProfile") or "jee_adv_msq").strip().lower()
        custom_partial = q.get("partialMarks")
        if isinstance(custom_partial, dict) and str(n_chosen) in custom_partial:
            pts = float(custom_partial[str(n_chosen)])
        elif m_profile == "proportional" or max_marks != 4.0:
            pts = round(max_marks * (n_chosen / n_total), 2)
        elif n_total >= 4 and n_chosen == 3:
            pts = 3.0
        elif n_total >= 3 and n_chosen == 2:
            pts = 2.0
        elif n_total >= 2 and n_chosen == 1:
            pts = 1.0
        else:
            pts = min(float(n_chosen), max(0.0, max_marks - 1.0))

        return {
            "status": "partial",
            "marks": round(pts, 2),
            "maxMarks": round(max_marks, 2),
            "negativeLost": 0.0,
            "studentAnswer": chosen_sorted,
            "correctAnswer": q["answer"],
            "wrongSelected": [],
            "missedCorrect": missed,
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
        if str(q.get("matrixMode") or "grid") == "code_options" and isinstance(q.get("answer"), str):
            chosen_code = str(raw_ans).strip().upper()
            expected_code = str(q["answer"]).strip().upper()
            ok = chosen_code == expected_code
            marks = max_marks if ok else m_wrong
            row_results: dict[str, str] = {}
            opt_row_map = q.get("optionRowMap") if isinstance(q.get("optionRowMap"), dict) else {}
            exp_rows = q.get("rowAnswer") if isinstance(q.get("rowAnswer"), dict) else opt_row_map.get(expected_code)
            ch_rows = opt_row_map.get(chosen_code) if isinstance(opt_row_map, dict) else None
            if isinstance(exp_rows, dict) and isinstance(ch_rows, dict):
                for rk, ev in exp_rows.items():
                    cv = ch_rows.get(rk, [])
                    row_results[rk] = "correct" if cv == ev else ("blank" if not cv else "wrong")
            else:
                for r in q.get("rows") or []:
                    row_results[r["id"]] = "correct" if ok else "wrong"
            return {
                "status": "correct" if ok else "wrong",
                "marks": round(marks, 2),
                "maxMarks": round(max_marks, 2),
                "negativeLost": round(abs(marks), 2) if marks < 0 else 0.0,
                "studentAnswer": chosen_code,
                "correctAnswer": expected_code,
                "rowResults": row_results,
                "detail": "exact code match" if ok else f"chose {chosen_code}, expected {expected_code}",
            }

        exp: dict[str, list[str]] = q["answer"]
        norm_student: dict[str, list[str]] = {}
        if isinstance(raw_ans, dict):
            for rk, rv in raw_ans.items():
                rk_u = str(rk).strip().upper()
                vals = [rv] if isinstance(rv, (str, int, float)) else list(rv or [])
                clean = sorted({str(x).strip().upper() for x in vals if str(x).strip()})
                if clean:
                    norm_student[rk_u] = clean

        matched_rows = 0
        wrong_rows = 0
        row_results = {}
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
                "rowResults": row_results,
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
                "rowResults": row_results,
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
            "rowResults": row_results,
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


def _extract_attempt_entry_full(attempt: dict[str, Any], qid: str) -> tuple[Any, int, str, float | None, list[str]]:
    """Extract `(answer, timeSpentSec, uiStatus, confidence, eliminated)` flexibly."""
    responses = attempt.get("responses")
    if not isinstance(responses, dict):
        responses = {}
    time_map = attempt.get("timeSpentByQuestion") or attempt.get("timeByQuestion") or {}

    raw = responses.get(qid)
    if isinstance(raw, dict) and any(k in raw for k in ("answer", "status", "timeSpentSec", "confidence", "eliminated")):
        ans = raw.get("answer")
        t_sec = int(round(float(raw.get("timeSpentSec", time_map.get(qid, 0)) or 0)))
        ui_status = str(raw.get("status") or "")
        conf_val: float | None = None
        raw_conf = raw.get("confidence")
        if isinstance(raw_conf, (int, float)) and not isinstance(raw_conf, bool):
            cf = float(raw_conf)
            if math.isfinite(cf):
                if 1.0 < cf <= 100.0:
                    cf = cf / 100.0
                conf_val = round(max(0.0, min(1.0, cf)), 4)
        raw_elim = raw.get("eliminated")
        eliminated = (
            sorted({str(x).strip().upper() for x in raw_elim if str(x).strip()})
            if isinstance(raw_elim, list)
            else []
        )
        return ans, t_sec, ui_status, conf_val, eliminated
    t_sec = int(round(float(time_map.get(qid, 0) or 0)))
    return raw, t_sec, "", None, []


def _extract_attempt_entry(attempt: dict[str, Any], qid: str) -> tuple[Any, int, str]:
    """Extract (answer, timeSpentSec, uiStatus) flexibly from standard or simplified attempt JSON."""
    ans, t_sec, ui_status, _conf, _elim = _extract_attempt_entry_full(attempt, qid)
    return ans, t_sec, ui_status


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

    public_spec, sealed_autopsy = strip_presubmit_bundle(spec)
    compiled = template.replace("__EXAM_TITLE__", html_lib.escape(public_spec["title"]))
    compiled = compiled.replace("__EXAM_SPEC_JSON__", safe_script_json(public_spec))
    sealed_b64 = (
        base64.b64encode(json.dumps(sealed_autopsy, ensure_ascii=False).encode("utf-8")).decode("ascii")
        if sealed_autopsy
        else ""
    )
    compiled = compiled.replace("__EXAM_SEALED_POST_B64__", sealed_b64)

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


def _extract_distractor_autopsy(q: dict[str, Any], sc: dict[str, Any]) -> list[dict[str, Any]]:
    """Return triggered option-level distractor autopsy items on wrong/partial responses."""
    st = sc["status"]
    if st not in ("wrong", "partial"):
        return []
    opts = q.get("options")
    if not isinstance(opts, list) or not opts:
        return []
    opts_by_id = {str(o.get("id") or "").upper(): o for o in opts if isinstance(o, dict)}

    triggered_ids: list[str] = []
    qtype = q["type"]
    if qtype in ("single_correct", "assertion_reason") or (qtype == "matrix_match" and isinstance(sc.get("studentAnswer"), str)):
        sa = str(sc.get("studentAnswer") or "").strip().upper()
        if sa and sa != str(q.get("answer") or "").strip().upper():
            triggered_ids.append(sa)
    elif qtype == "multi_correct":
        triggered_ids.extend(sc.get("wrongSelected") or [])

    out: list[dict[str, Any]] = []
    for oid in triggered_ids:
        opt = opts_by_id.get(oid)
        if not opt:
            continue
        why = str(opt.get("whyWrong") or "").strip()
        tcat = str(opt.get("trapCategory") or "").strip()
        conf_with = str(opt.get("confusedWith") or "").strip()
        if why or tcat or conf_with:
            entry: dict[str, Any] = {
                "optionId": oid,
                "text": str(opt.get("text") or ""),
                "whyWrong": why,
                "trapCategory": tcat or q.get("trapCategory") or "",
                "confusedWith": conf_with,
            }
            out.append(entry)
    return out


def _compute_ev_calibration(
    scored_rows: list[tuple[dict[str, Any], dict[str, Any], int, str, float | None, list[str]]],
    total_score: float,
    max_score: float,
    negative_lost: float,
) -> dict[str, Any]:
    """Compute exact break-even Expected-Value (EV) skip calibration metrics."""
    has_conf = False
    has_elim = False
    neg_ev_gambles = 0
    marks_lost_neg_ev = 0.0
    underconf_skips = 0
    marks_left_on_table = 0.0
    counterfactual_delta = 0.0
    per_question: list[dict[str, Any]] = []

    for q, sc, _q_time, _ui, q_conf, q_elim in scored_rows:
        b = max(0.0, float(q["marksCorrect"]))
        p_pen = abs(min(0.0, float(q.get("marksWrong", 0.0))))
        u = float(q.get("marksUnattempted", 0.0))
        p_be = 0.0 if (b + p_pen) <= 0.0 else max(0.0, min(1.0, (p_pen + u) / (b + p_pen)))

        p_marginal: float | None = None
        if q["type"] == "multi_correct" and q.get("partialMarking", True) and isinstance(sc.get("studentAnswer"), list):
            k = len(sc["studentAnswer"])
            if k >= 1:
                p_marginal = round((k + p_pen) / (1.0 + k + p_pen), 4)

        p_est: float | None = None
        if q_conf is not None:
            p_est = q_conf
            has_conf = True
        elif q_elim and q["type"] in ("single_correct", "assertion_reason") and isinstance(q.get("options"), list):
            m_opts = len(q["options"])
            rem = m_opts - len(set(q_elim) & {o["id"] for o in q["options"]})
            if rem >= 1 and rem < m_opts:
                p_est = round(1.0 / rem, 4)
                has_elim = True

        attempted = sc["status"] != "unattempted"
        verdict = "uncalibrated"
        ev_attempt: float | None = None
        if p_est is not None:
            ev_attempt = round(p_est * b - (1.0 - p_est) * p_pen, 4)
            if attempted:
                if p_pen >= 0.0 and p_be > 0.0 and p_est < p_be - 1e-9:
                    verdict = "neg_ev_gamble"
                    neg_ev_gambles += 1
                    marks_lost_neg_ev += max(0.0, u - float(sc["marks"]))
                    counterfactual_delta += u - float(sc["marks"])
                else:
                    verdict = "positive_ev"
            else:
                if p_est > p_be + 1e-9 and (ev_attempt - u) > 1e-9:
                    verdict = "underconfident_skip"
                    underconf_skips += 1
                    marks_left_on_table += max(0.0, ev_attempt - u)
                else:
                    verdict = "rational_skip"

        pq_item: dict[str, Any] = {
            "id": q["id"],
            "pBreakEven": round(p_be, 4),
            "confidence": p_est,
            "expectedValue": ev_attempt,
            "verdict": verdict,
        }
        if p_marginal is not None:
            pq_item["pMarginalNextOption"] = p_marginal
        per_question.append(pq_item)

    if has_conf:
        ev_basis = "confidence_exact"
    elif has_elim:
        ev_basis = "elimination_derived"
    else:
        ev_basis = "negative_mark_proxy"

    marks_lost_neg_ev = round(marks_lost_neg_ev, 2)
    marks_left_on_table = round(marks_left_on_table, 2)
    if ev_basis in ("confidence_exact", "elimination_derived"):
        denom = max(1.0, 0.25 * max_score)
        disc = 100.0 * max(0.0, min(1.0, 1.0 - (marks_lost_neg_ev + marks_left_on_table) / denom))
        counterfactual = round(total_score + counterfactual_delta, 2)
    else:
        denom = max(1.0, 0.20 * max_score)
        disc = 100.0 * max(0.0, min(1.0, 1.0 - negative_lost / denom))
        counterfactual = round(total_score + negative_lost, 2)

    return {
        "evBasis": ev_basis,
        "negEvGamblesCount": neg_ev_gambles,
        "marksLostToNegEv": marks_lost_neg_ev,
        "underconfidentSkipsCount": underconf_skips,
        "marksLeftOnTable": marks_left_on_table,
        "optimalSkipCounterfactualScore": counterfactual,
        "evDisciplineScore": round(disc, 1),
        "perQuestion": per_question,
    }


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
    log mistakes (with option-level distractor traps), compute 3-bucket autopsy,
    vignetteBreakdown, and exact Expected-Value (EV) skip calibration.
    """
    resolved_kg, _is_default_kg = resolve_kg_dir(kg_dir)
    slow_threshold = int(slow_sec)
    today = today_iso()
    attempt_id = f"ex-{uuid.uuid4().hex[:8]}"

    # First pass: score every question & compute median time spent across attempted questions.
    scored_rows: list[tuple[dict[str, Any], dict[str, Any], int, str, float | None, list[str]]] = []
    attempted_times: list[int] = []
    for q in spec["questions"]:
        raw_ans, q_time, ui_status, q_conf, q_elim = _extract_attempt_entry_full(attempt, q["id"])
        sc = score_question(q, raw_ans)
        scored_rows.append((q, sc, q_time, ui_status, q_conf, q_elim))
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
    vignette_bd: dict[str, dict[str, Any]] = {}
    q_details: list[dict[str, Any]] = []
    missed_items: list[tuple[dict[str, Any], dict[str, Any], int, float | None, list[dict[str, Any]]]] = []
    correct_items: list[tuple[dict[str, Any], dict[str, Any], int, float | None]] = []

    autopsy_buckets: dict[str, list[str]] = {"knowledge": [], "execution": [], "time_sink": []}
    ego_traps: list[dict[str, Any]] = []
    all_distractor_items: list[dict[str, Any]] = []
    distractor_cat_counts: Counter[str] = Counter()
    distractor_confused_counts: Counter[str] = Counter()
    vignettes_meta = spec.get("vignettes") if isinstance(spec.get("vignettes"), dict) else {}

    for q, sc, q_time, ui_status, q_conf, q_elim in scored_rows:
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

        q_distractors = _extract_distractor_autopsy(q, sc)
        for d_item in q_distractors:
            all_distractor_items.append({"questionId": qid, **d_item})
            if d_item.get("trapCategory"):
                distractor_cat_counts[d_item["trapCategory"]] += 1
            if d_item.get("confusedWith"):
                distractor_confused_counts[d_item["confusedWith"]] += 1

        if q["trapCategory"]:
            inferred_cat = q["trapCategory"]
        elif q_distractors and q_distractors[0].get("trapCategory"):
            inferred_cat = q_distractors[0]["trapCategory"]
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

        vid = str(q.get("vignetteId") or "").strip()
        if vid:
            vmeta = vignettes_meta.get(vid) or {}
            vb = vignette_bd.setdefault(
                vid,
                {
                    "id": vid,
                    "title": str(vmeta.get("title") or vid),
                    "kind": str(vmeta.get("kind") or "passage"),
                    "questionIds": [],
                    "score": 0.0,
                    "maxScore": 0.0,
                    "correct": 0,
                    "partial": 0,
                    "wrong": 0,
                    "unattempted": 0,
                },
            )
            vb["questionIds"].append(qid)
            vb["score"] = round(vb["score"] + sc["marks"], 2)
            vb["maxScore"] = round(vb["maxScore"] + sc["maxMarks"], 2)
            vb[st] += 1

        detail_row: dict[str, Any] = {
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
            "distractorAutopsy": q_distractors,
        }
        if q_conf is not None:
            detail_row["confidence"] = q_conf
        if q_elim:
            detail_row["eliminated"] = q_elim
        if vid:
            detail_row["vignetteId"] = vid
        if "arErrorKind" in sc:
            detail_row["arErrorKind"] = sc["arErrorKind"]
        if "wrongSelected" in sc:
            detail_row["wrongSelected"] = sc["wrongSelected"]
            detail_row["missedCorrect"] = sc["missedCorrect"]
        if "rowResults" in sc:
            detail_row["rowResults"] = sc["rowResults"]
        q_details.append(detail_row)

        if st in ("wrong", "partial"):
            missed_items.append((q, sc, q_time, q_conf, q_distractors))
        elif st == "correct":
            correct_items.append((q, sc, q_time, q_conf))

    for bd in subject_bd.values():
        bd["percentage"] = round((bd["score"] / bd["maxScore"]) * 100.0, 2) if bd["maxScore"] > 0 else 0.0
    for bd in chapter_bd.values():
        bd["percentage"] = round((bd["score"] / bd["maxScore"]) * 100.0, 2) if bd["maxScore"] > 0 else 0.0
    for vb in vignette_bd.values():
        vb["percentage"] = round((vb["score"] / vb["maxScore"]) * 100.0, 2) if vb["maxScore"] > 0 else 0.0
        n_sub = len(vb["questionIds"])
        n_att = vb["correct"] + vb["partial"] + vb["wrong"]
        if n_att == 0:
            vb["pattern"] = "unattempted"
        elif vb["correct"] == n_sub:
            vb["pattern"] = "solid"
        elif n_sub >= 2 and vb["correct"] == 0 and (vb["wrong"] + vb["partial"]) >= 2:
            vb["pattern"] = "whole_vignette_failure"
        else:
            vb["pattern"] = "isolated_subitem_miss"

    total_score = round(total_score, 2)
    max_score = round(max_score, 2)
    negative_lost = round(negative_lost, 2)
    attempted_count = correct_count + partial_count + wrong_count
    percentage = round((total_score / max_score) * 100.0, 2) if max_score > 0 else 0.0
    accuracy_pct = round((correct_count / attempted_count) * 100.0, 2) if attempted_count > 0 else 0.0
    time_spent_sec = int(round(float(attempt.get("timeSpentSec") or sum_q_time)))

    ev_calibration = _compute_ev_calibration(scored_rows, total_score, max_score, negative_lost)
    distractor_summary = {
        "triggeredCount": len(all_distractor_items),
        "topTrapCategories": [
            {"category": cat, "count": cnt} for cat, cnt in distractor_cat_counts.most_common(5)
        ],
        "topConfusedWith": [
            {"confusedWith": cw, "count": cnt} for cw, cnt in distractor_confused_counts.most_common(5)
        ],
        "items": all_distractor_items,
    }

    # Log mistakes (rich option-level entries when distractor autopsy exists; 100% compatible otherwise)
    mistake_ids: list[str] = []
    touched_chapters: set[str] = set()
    touched_props: set[str] = set()
    if not no_log:
        for q, sc, q_time, _q_conf, q_distractors in missed_items:
            stem_snip = re.sub(r"\s+", " ", q["stem"]).strip()[:140]
            trigger_cue = q["trapNote"] or f"{q['chapter']} ({q['id']}): {stem_snip}"
            student_str = _format_answer(q["type"], sc["studentAnswer"])
            correct_str = (
                f"{q['range'][0]}..{q['range'][1]}"
                if q["type"] == "numerical" and "range" in q
                else _format_answer(q["type"], q["answer"])
            )
            correct_move = (q["trapNote"] or q["solution"] or f"Correct answer: {correct_str}")[:240]

            if q_distractors:
                for d_item in q_distractors:
                    opt_id = d_item["optionId"]
                    cat = d_item.get("trapCategory") or q["trapCategory"] or (
                        "time_pressure" if sc["status"] == "wrong" and q_time > slow_threshold else "concept_gap"
                    )
                    why_txt = d_item.get("whyWrong") or sc["detail"]
                    wrong_move = f"Picked {opt_id} ({sc['marks']:+g} marks, {q_time}s) — {why_txt}"[:240]
                    entry: dict[str, Any] = {
                        "id": f"m-{uuid.uuid4().hex[:8]}",
                        "date": today,
                        "subject": q["subject"],
                        "chapter": q["chapter"],
                        "propId": q["propId"],
                        "category": cat,
                        "triggerCue": trigger_cue,
                        "wrongMove": wrong_move,
                        "correctMove": correct_move,
                        "sourceRef": f"exam:{spec['examSlug']}:{q['id']}:{opt_id}",
                    }
                    if d_item.get("confusedWith"):
                        entry["confusedWith"] = d_item["confusedWith"]
                    logged = _log_mistake_entry(resolved_kg, entry)
                    mistake_ids.append(str(logged.get("id") or entry["id"]))
            else:
                if q["trapCategory"]:
                    cat = q["trapCategory"]
                elif sc["status"] == "wrong" and q_time > slow_threshold:
                    cat = "time_pressure"
                else:
                    cat = "concept_gap"
                wrong_move = f"Answered {student_str} ({sc['status']}, {sc['marks']:+g} marks, {q_time}s) — {sc['detail']}"
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
            for q, sc, q_time, q_conf, _qd in missed_items:
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
                        confidence=q_conf,
                        duration_ms=q_time * 1000 if q_time > 0 else None,
                        no_sync=True,
                    )
                    srs_updated_ids.append(pid)
                    srs_missed_ids.append(pid)
                except Exception as exc:
                    srs_errors.append(f"{pid}: {exc}")

            credited_props_seen: set[str] = set()
            for q, sc, q_time, q_conf in correct_items:
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
                        confidence=q_conf,
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
        "evDisciplineScore": ev_calibration["evDisciplineScore"],
        "evCalibration": {
            "evBasis": ev_calibration["evBasis"],
            "negEvGamblesCount": ev_calibration["negEvGamblesCount"],
            "marksLostToNegEv": ev_calibration["marksLostToNegEv"],
            "underconfidentSkipsCount": ev_calibration["underconfidentSkipsCount"],
            "marksLeftOnTable": ev_calibration["marksLeftOnTable"],
            "evDisciplineScore": ev_calibration["evDisciplineScore"],
        },
        "distractorTriggeredCount": distractor_summary["triggeredCount"],
        "escalationsCount": len(three_strike_escalations),
        "mistakesLogged": len(mistake_ids),
        "srsUpdated": len(srs_updated_ids),
    }

    if not no_log:
        append_jsonl(resolved_kg / "exam-attempts.jsonl", attempt_summary_row)
        try:
            from syntopik.learner_model import record_telemetry_event

            for q, sc, q_time, _ui, q_conf, _q_elim in scored_rows:
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
                    confidence=q_conf,
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
        "evCalibration": ev_calibration,
        "distractorSummary": distractor_summary,
        "vignetteBreakdown": vignette_bd,
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
