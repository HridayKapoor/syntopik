#!/usr/bin/env python3
"""Clean-Room Derivation Reconstruction & Axiom-Cut Surgery CLI (`scripts/derivation_forge.py`).

Stores structured step-dependency DAGs in `knowledge-graph/derivations.jsonl` and powers:
  1. `blackout`: progressive Clean-Room Derivation Reconstruction (levels 1..3)
     without leaking hidden step expressions.
  2. `check`: deterministic equivalence verification of a learner's reconstructed
     step via exact normalized comparison + strict math-AST numeric sampling
     (with optional lazy SymPy fallback, zero `__builtins__`).
  3. `cut`: Axiom-Cut Surgery — BFS over `dependsOn` to compute `earliestBreak`,
     `invalidatedSteps`, and `survivingSteps`, grade learner localization, and
     record T4 epistemic evidence on pass.
  4. `diff`: Structured Proof-Graph Handwriting Diff — aligns handwritten
     student steps sequentially against the topologically ordered Causal Step
     DAG, classifies earliest break (`sign_flip`, `missing_factor`,
     `dropped_term`, `wrong_axiom`, `algebraic_jump`), emits zero-leak R0-R2
     Socratic nudges, auto-spawns a Blunder FEN Fork, and runs Bayesian
     root-cause `select_diagnostic_probe`.

Pure Python 3.12+ standard library (optional lazy SymPy fallback).
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import heapq
import json
import random
import sys
import uuid
from collections import deque
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from syntopik.forks import (  # noqa: E402
    _extract_math_symbols,
    _math_equivalent,
    _normalize_text_move,
    _preprocess_math_text,
    _safe_eval_math_ast,
    validate_math_expr_ast,
)
from syntopik.paths import resolve_kg_dir, resolve_workspace  # noqa: E402
from syntopik.schemas import now_iso, slugify, today_iso  # noqa: E402
from syntopik.storage import append_jsonl_locked, load_jsonl, save_jsonl_atomic, workspace_lock  # noqa: E402

__all__ = [
    "DERIVATIONS_FILENAME",
    "VALID_STEP_ROLES",
    "resolve_derivations_path",
    "load_derivations",
    "save_derivations",
    "create_derivation",
    "list_derivations",
    "get_blackout_challenge",
    "check_blackout_step",
    "grade_axiom_cut",
    "diff_handwritten_steps",
    "main",
]

DERIVATIONS_FILENAME = "derivations.jsonl"
VALID_STEP_ROLES: frozenset[str] = frozenset({"axiom", "step", "theorem"})
_MAX_ANSWER_LEN = 1024
_MAX_DIFF_STEPS = 40
_MAX_DIFF_EXPR_LEN = 512

_ERROR_KIND_TO_MISTAKE_CATEGORY: dict[str, str] = {
    "sign_flip": "sign_error",
    "missing_factor": "algebra_slip",
    "dropped_term": "algebra_slip",
    "wrong_axiom": "concept_gap",
    "algebraic_jump": "algebra_slip",
}


def resolve_derivations_path(
    *,
    workspace: Path | str | None = None,
    kg_dir: Path | str | None = None,
) -> Path:
    return resolve_kg_dir(workspace=workspace, explicit=kg_dir) / DERIVATIONS_FILENAME


def load_derivations(
    kg_dir: Path | str | None = None,
    *,
    workspace: Path | str | None = None,
) -> list[dict[str, Any]]:
    return load_jsonl(resolve_derivations_path(workspace=workspace, kg_dir=kg_dir))


def save_derivations(
    rows: list[dict[str, Any]],
    kg_dir: Path | str | None = None,
    *,
    workspace: Path | str | None = None,
) -> Path:
    return save_jsonl_atomic(resolve_derivations_path(workspace=workspace, kg_dir=kg_dir), rows)


def _validate_and_normalize_derivation(raw: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("Derivation specification must be a JSON object.")

    title = str(raw.get("title") or "").strip()
    raw_id = str(raw.get("id") or "").strip()
    if not raw_id:
        base = slugify(title or str(raw.get("propId") or "derivation"), max_len=36)
        raw_id = f"deriv-{base}-{uuid.uuid4().hex[:6]}"
    if not title:
        title = raw_id

    raw_steps = raw.get("steps")
    if not isinstance(raw_steps, list) or not raw_steps:
        raise ValueError("Derivation requires a non-empty 'steps' array.")

    norm_steps: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for idx, st in enumerate(raw_steps, start=1):
        if not isinstance(st, dict):
            raise ValueError(f"Step #{idx} must be an object.")
        sid = str(st.get("id") or f"s{idx}").strip()
        if not sid:
            raise ValueError(f"Step #{idx} has empty id.")
        if sid in seen_ids:
            raise ValueError(f"Duplicate step id {sid!r} in derivation {raw_id!r}.")
        seen_ids.add(sid)

        role = str(st.get("role") or "step").strip().lower()
        if role not in VALID_STEP_ROLES:
            raise ValueError(f"Step {sid!r} has invalid role {role!r}; expected axiom|step|theorem.")

        expr = str(st.get("expr") or "").strip()
        if not expr:
            raise ValueError(f"Step {sid!r} requires a non-empty 'expr'.")

        raw_deps = st.get("dependsOn") or []
        if not isinstance(raw_deps, list):
            raise ValueError(f"Step {sid!r} 'dependsOn' must be a list of step IDs.")
        deps = [str(d).strip() for d in raw_deps if str(d).strip()]
        if sid in deps:
            raise ValueError(f"Step {sid!r} cannot depend on itself.")

        norm_steps.append(
            {
                "id": sid,
                "label": str(st.get("label") or sid).strip(),
                "expr": expr,
                "role": role,
                "dependsOn": list(dict.fromkeys(deps)),
                "note": str(st.get("note") or "").strip(),
            }
        )

    # Verify all dependsOn exist and graph is acyclic (Kahn's algorithm)
    children: dict[str, list[str]] = {s["id"]: [] for s in norm_steps}
    in_deg: dict[str, int] = {s["id"]: 0 for s in norm_steps}
    for s in norm_steps:
        for dep in s["dependsOn"]:
            if dep not in seen_ids:
                raise ValueError(f"Step {s['id']!r} depends on unknown step {dep!r}.")
            children[dep].append(s["id"])
            in_deg[s["id"]] += 1

    q: deque[str] = deque(sid for sid, deg in in_deg.items() if deg == 0)
    visited = 0
    while q:
        u = q.popleft()
        visited += 1
        for v in children[u]:
            in_deg[v] -= 1
            if in_deg[v] == 0:
                q.append(v)
    if visited != len(norm_steps):
        raise ValueError(f"Derivation {raw_id!r} contains a dependency cycle in 'dependsOn'.")

    ts = now_iso()
    return {
        "id": raw_id,
        "propId": str(raw.get("propId") or "").strip() or None,
        "chapter": str(raw.get("chapter") or "General").strip() or "General",
        "title": title,
        "domain": str(raw.get("domain") or "General").strip() or "General",
        "steps": norm_steps,
        "createdAt": str(raw.get("createdAt") or ts),
        "updatedAt": ts,
    }


def _find_derivation(
    derivation_id: str,
    *,
    kg_dir: Path | str | None = None,
    workspace: Path | str | None = None,
) -> dict[str, Any]:
    target = str(derivation_id or "").strip()
    if not target:
        raise ValueError("derivation_id is required.")
    for row in load_derivations(kg_dir=kg_dir, workspace=workspace):
        if str(row.get("id") or "") == target:
            return row
    raise KeyError(f"Derivation '{target}' not found.")


def _record_telemetry_safe(
    kg_dir: Path,
    *,
    domain: str,
    prop_id: str | None,
    kind: str,
    correct: bool,
    difficulty: int = 1580,
    mistake_category: str | None = None,
) -> None:
    try:
        from syntopik.learner_model import record_telemetry_event

        record_telemetry_event(
            kg_dir,
            domain=domain,
            prop_id=prop_id,
            kind=kind,
            correct=correct,
            difficulty=difficulty,
            confidence=None,
            duration_ms=None,
            hints_used=0,
            mistake_category=None if correct else (mistake_category or "concept_gap"),
            pf_outcome=None,
            skipped=False,
        )
    except Exception:
        pass


def _record_evidence_safe(
    workspace: Path,
    prop_id: str | None,
    tier: int,
    source: str,
    detail: dict[str, Any],
) -> bool:
    if not prop_id:
        return False
    try:
        from syntopik.evidence import record_evidence

        record_evidence(workspace, prop_id, tier, source, detail=detail, verified=True)
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Public importable API
# ---------------------------------------------------------------------------


def create_derivation(
    spec: dict[str, Any],
    *,
    kg_dir: Path | str | None = None,
    workspace: Path | str | None = None,
) -> dict[str, Any]:
    """Validate and upsert a derivation DAG into `knowledge-graph/derivations.jsonl`."""
    record = _validate_and_normalize_derivation(spec)
    path = resolve_derivations_path(workspace=workspace, kg_dir=kg_dir)
    with workspace_lock(path):
        rows = load_jsonl(path)
        replaced = False
        for idx, existing in enumerate(rows):
            if str(existing.get("id") or "") == record["id"]:
                record["createdAt"] = existing.get("createdAt") or record["createdAt"]
                rows[idx] = record
                replaced = True
                break
        if not replaced:
            rows.append(record)
        save_jsonl_atomic(path, rows)

    return {
        "ok": True,
        "action": "updated" if replaced else "created",
        "path": str(path),
        "id": record["id"],
        "derivation": record,
    }


def list_derivations(
    kg_dir: Path | str | None = None,
    *,
    workspace: Path | str | None = None,
    prop_id: str | None = None,
    chapter: str | None = None,
    domain: str | None = None,
) -> dict[str, Any]:
    """List saved derivations with optional filters."""
    rows = load_derivations(kg_dir=kg_dir, workspace=workspace)
    pf = str(prop_id or "").strip()
    cf = str(chapter or "").strip().lower()
    df = str(domain or "").strip().lower()

    items: list[dict[str, Any]] = []
    for r in rows:
        if pf and str(r.get("propId") or "") != pf:
            continue
        if cf and cf not in str(r.get("chapter") or "").lower():
            continue
        if df and str(r.get("domain") or "").lower() != df:
            continue
        steps = [s for s in (r.get("steps") or []) if isinstance(s, dict)]
        items.append(
            {
                "id": r.get("id"),
                "propId": r.get("propId"),
                "chapter": r.get("chapter"),
                "title": r.get("title"),
                "domain": r.get("domain"),
                "stepCount": len(steps),
                "axiomCount": sum(1 for s in steps if s.get("role") == "axiom"),
                "theoremCount": sum(1 for s in steps if s.get("role") == "theorem"),
                "updatedAt": r.get("updatedAt"),
            }
        )

    return {"ok": True, "count": len(items), "derivations": items}


def get_blackout_challenge(
    derivation_id: str,
    *,
    level: int = 1,
    kg_dir: Path | str | None = None,
    workspace: Path | str | None = None,
) -> dict[str, Any]:
    """Return a clean-room blackout derivation view hiding steps according to `level` (1..3)."""
    lvl = max(1, min(3, int(level)))
    deriv = _find_derivation(derivation_id, kg_dir=kg_dir, workspace=workspace)
    steps = list(deriv.get("steps") or [])

    non_axioms = [s for s in steps if str(s.get("role") or "step") != "axiom"]
    hidden_ids: set[str] = set()
    if non_axioms:
        if lvl == 1:
            # Hide intermediate steps (~35%, at least 1), preferring non-theorem steps first
            inter = [s for s in non_axioms if s.get("role") == "step"]
            pool = inter if inter else non_axioms
            target_n = max(1, round(len(pool) * 0.4))
            # Evenly spaced deterministic indices from the end/middle
            step_stride = max(1, len(pool) // target_n)
            for idx in range(len(pool) - 1, -1, -step_stride):
                hidden_ids.add(str(pool[idx]["id"]))
                if len(hidden_ids) >= target_n:
                    break
        elif lvl == 2:
            # Hide ~65% of non-axiom steps (including final theorem)
            target_n = max(1, round(len(non_axioms) * 0.65))
            # Hide theorem(s) + alternating intermediate steps
            for s in reversed(non_axioms):
                if len(hidden_ids) >= target_n:
                    break
                hidden_ids.add(str(s["id"]))
        else:
            # Level 3: Clean-Room — hide EVERY non-axiom step & theorem
            hidden_ids = {str(s["id"]) for s in non_axioms}

    public_steps: list[dict[str, Any]] = []
    for s in steps:
        sid = str(s["id"])
        is_hidden = sid in hidden_ids
        public_steps.append(
            {
                "id": sid,
                "label": s.get("label", sid),
                "role": s.get("role", "step"),
                "dependsOn": list(s.get("dependsOn") or []),
                "hidden": is_hidden,
                "expr": None if is_hidden else s.get("expr"),
                "note": None if is_hidden else (s.get("note") or None),
            }
        )

    return {
        "ok": True,
        "derivationId": deriv["id"],
        "propId": deriv.get("propId"),
        "title": deriv.get("title"),
        "domain": deriv.get("domain"),
        "level": lvl,
        "totalSteps": len(steps),
        "hiddenCount": len(hidden_ids),
        "hiddenStepIds": [s["id"] for s in public_steps if s["hidden"]],
        "steps": public_steps,
    }


def check_blackout_step(
    derivation_id: str,
    step_id: str,
    answer: str,
    *,
    kg_dir: Path | str | None = None,
    workspace: Path | str | None = None,
    reveal: bool = False,
) -> dict[str, Any]:
    """Check a learner's reconstructed step against canonical `expr` via normalized + AST-safe math equivalence."""
    deriv = _find_derivation(derivation_id, kg_dir=kg_dir, workspace=workspace)
    sid = str(step_id or "").strip()
    step = next((s for s in (deriv.get("steps") or []) if str(s.get("id") or "") == sid), None)
    if step is None:
        raise KeyError(f"Step '{sid}' not found in derivation '{deriv['id']}'.")

    learner_ans = str(answer or "").strip()
    canonical = str(step.get("expr") or "").strip()
    if not learner_ans:
        raise ValueError("Learner answer cannot be empty.")

    method = "none"
    passed = False
    if _normalize_text_move(learner_ans) == _normalize_text_move(canonical):
        passed = True
        method = "exact_normalized"
    elif len(learner_ans) <= _MAX_ANSWER_LEN and len(canonical) <= _MAX_ANSWER_LEN and _math_equivalent(learner_ans, canonical):
        passed = True
        method = "math_equivalence"

    kdir = resolve_kg_dir(workspace=workspace, explicit=kg_dir)
    ws = resolve_workspace(workspace) if workspace is not None else kdir.parent
    _record_telemetry_safe(
        kdir,
        domain=str(deriv.get("domain") or "General"),
        prop_id=deriv.get("propId"),
        kind="derivation_check",
        correct=passed,
        difficulty=1560,
    )
    ev_recorded = False
    if passed and str(step.get("role") or "") == "theorem":
        ev_recorded = _record_evidence_safe(
            ws,
            deriv.get("propId"),
            3,
            "derivation_blackout",
            {"derivationId": deriv["id"], "stepId": sid, "method": method},
        )

    return {
        "ok": True,
        "derivationId": deriv["id"],
        "propId": deriv.get("propId"),
        "stepId": sid,
        "label": step.get("label", sid),
        "role": step.get("role", "step"),
        "passed": passed,
        "method": method,
        "learnerAnswer": learner_ans,
        "canonicalExpr": canonical if (passed or reveal) else None,
        "note": step.get("note") if (passed or reveal) else None,
        "evidenceRecorded": ev_recorded,
    }


def grade_axiom_cut(
    derivation_id: str,
    axiom_step_id: str,
    *,
    guess_step_id: str | None = None,
    kg_dir: Path | str | None = None,
    workspace: Path | str | None = None,
) -> dict[str, Any]:
    """Perform Axiom-Cut Surgery via BFS over `dependsOn` and optionally grade learner's localization."""
    deriv = _find_derivation(derivation_id, kg_dir=kg_dir, workspace=workspace)
    steps = list(deriv.get("steps") or [])
    step_by_id = {str(s["id"]): s for s in steps}
    order_idx = {str(s["id"]): i for i, s in enumerate(steps)}

    cut_id = str(axiom_step_id or "").strip()
    if cut_id not in step_by_id:
        raise KeyError(f"Step '{cut_id}' not found in derivation '{deriv['id']}'.")

    # Build forward dependency adjacency: parent -> direct dependents
    children: dict[str, list[str]] = {sid: [] for sid in step_by_id}
    for s in steps:
        sid = str(s["id"])
        for dep in s.get("dependsOn") or []:
            if dep in children:
                children[dep].append(sid)

    # BFS from cut_id to collect all invalidated downstream steps
    invalidated_set: set[str] = set()
    q: deque[str] = deque(children[cut_id])
    while q:
        cur = q.popleft()
        if cur in invalidated_set:
            continue
        invalidated_set.add(cur)
        for nxt in children[cur]:
            if nxt not in invalidated_set:
                q.append(nxt)

    invalidated_steps = sorted(invalidated_set, key=lambda sid: order_idx[sid])
    surviving_steps = [
        str(s["id"])
        for s in steps
        if str(s["id"]) != cut_id and str(s["id"]) not in invalidated_set
    ]

    # Earliest breaks: invalidated steps that directly depend on cut_id and NOT on any other invalidated step
    earliest_candidates = [
        sid
        for sid in invalidated_steps
        if cut_id in (step_by_id[sid].get("dependsOn") or [])
        and not any(d in invalidated_set for d in (step_by_id[sid].get("dependsOn") or []))
    ]
    earliest_break = earliest_candidates[0] if earliest_candidates else (invalidated_steps[0] if invalidated_steps else None)

    passed: bool | None = None
    evidence_recorded = False
    guess_clean = str(guess_step_id).strip() if guess_step_id is not None and str(guess_step_id).strip() else None
    explanation: str

    kdir = resolve_kg_dir(workspace=workspace, explicit=kg_dir)
    ws = resolve_workspace(workspace) if workspace is not None else kdir.parent

    if guess_clean is not None:
        if guess_clean not in step_by_id:
            passed = False
            explanation = f"Guessed step '{guess_clean}' does not exist in derivation '{deriv['id']}'."
        elif earliest_break is None:
            passed = guess_clean == cut_id
            explanation = f"Cutting '{cut_id}' invalidates 0 downstream steps."
        elif guess_clean in earliest_candidates:
            passed = True
            explanation = (
                f"Exact earliest break localized at '{guess_clean}' "
                f"({len(invalidated_steps)} invalidated, {len(surviving_steps)} surviving)."
            )
            evidence_recorded = _record_evidence_safe(
                ws,
                deriv.get("propId"),
                4,
                "derivation_axiom_cut",
                {
                    "derivationId": deriv["id"],
                    "cutAxiom": cut_id,
                    "earliestBreak": earliest_break,
                    "invalidatedCount": len(invalidated_steps),
                },
            )
        elif guess_clean in invalidated_set:
            passed = False
            explanation = (
                f"'{guess_clean}' is downstream of the break, not the earliest break. "
                f"Earliest break occurs at '{earliest_break}'."
            )
        else:
            passed = False
            explanation = (
                f"'{guess_clean}' survives cutting '{cut_id}' (does not depend on it). "
                f"Earliest break occurs at '{earliest_break}'."
            )

        _record_telemetry_safe(
            kdir,
            domain=str(deriv.get("domain") or "General"),
            prop_id=deriv.get("propId"),
            kind="derivation_cut",
            correct=bool(passed),
            difficulty=1620,
        )
    else:
        explanation = (
            f"Cutting '{cut_id}' breaks '{earliest_break}' first "
            f"({len(invalidated_steps)} invalidated, {len(surviving_steps)} surviving)."
            if earliest_break
            else f"Cutting '{cut_id}' leaves all {len(surviving_steps)} other step(s) intact."
        )

    return {
        "ok": True,
        "derivationId": deriv["id"],
        "propId": deriv.get("propId"),
        "cutAxiom": cut_id,
        "cutAxiomLabel": step_by_id[cut_id].get("label", cut_id),
        "earliestBreak": earliest_break,
        "earliestBreakCandidates": earliest_candidates,
        "invalidatedSteps": invalidated_steps,
        "survivingSteps": surviving_steps,
        "guessStep": guess_clean,
        "passed": passed,
        "evidenceRecorded": evidence_recorded,
        "explanation": explanation,
    }


# ---------------------------------------------------------------------------
# Structured Proof-Graph Handwriting Diff (v3.1)
# ---------------------------------------------------------------------------


def _resolve_kg_and_ws(
    kg_dir: Path | str | None = None,
    workspace: Path | str | None = None,
) -> tuple[Path, Path]:
    if kg_dir is not None and str(kg_dir).strip():
        p = Path(kg_dir).expanduser().resolve()
        if (
            (p / "knowledge-graph").is_dir()
            and not (p / DERIVATIONS_FILENAME).is_file()
            and p.name != "knowledge-graph"
        ):
            kdir = (p / "knowledge-graph").resolve()
            ws = p
        else:
            kdir = p
            ws = Path(workspace).expanduser().resolve() if workspace is not None else kdir.parent
        return kdir, ws
    kdir = resolve_kg_dir(workspace=workspace, explicit=None)
    ws = resolve_workspace(workspace) if workspace is not None else kdir.parent
    return kdir, ws


def _topological_steps(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Order DAG steps topologically: axioms -> intermediate steps -> theorems."""
    role_rank = {"axiom": 0, "step": 1, "theorem": 2}
    step_by_id = {str(s["id"]): s for s in steps}
    orig_idx = {str(s["id"]): i for i, s in enumerate(steps)}
    children: dict[str, list[str]] = {sid: [] for sid in step_by_id}
    in_deg: dict[str, int] = {sid: 0 for sid in step_by_id}

    for s in steps:
        sid = str(s["id"])
        for dep in s.get("dependsOn") or []:
            d = str(dep)
            if d in step_by_id:
                children[d].append(sid)
                in_deg[sid] += 1

    heap: list[tuple[int, int, str]] = [
        (role_rank.get(str(step_by_id[sid].get("role") or "step"), 1), orig_idx[sid], sid)
        for sid, deg in in_deg.items()
        if deg == 0
    ]
    heapq.heapify(heap)
    ordered: list[dict[str, Any]] = []
    while heap:
        _, _, u = heapq.heappop(heap)
        ordered.append(step_by_id[u])
        for v in children[u]:
            in_deg[v] -= 1
            if in_deg[v] == 0:
                heapq.heappush(
                    heap,
                    (role_rank.get(str(step_by_id[v].get("role") or "step"), 1), orig_idx[v], v),
                )

    if len(ordered) != len(steps):
        seen = {str(s["id"]) for s in ordered}
        ordered.extend(s for s in steps if str(s["id"]) not in seen)
    return ordered


def _split_equation(raw: str) -> tuple[str, str] | None:
    cleaned = str(raw or "").strip().strip("$").strip()
    if "==" in cleaned:
        lhs, rhs = cleaned.split("==", 1)
        return lhs.strip(), rhs.strip()
    if "=" in cleaned and not any(op in cleaned for op in ("<=", ">=", "!=")):
        lhs, rhs = cleaned.split("=", 1)
        return lhs.strip(), rhs.strip()
    return None


def _step_expr_match(student_expr: str, canonical_expr: str) -> tuple[bool, str | None]:
    if not student_expr or not canonical_expr:
        return False, None
    if _normalize_text_move(student_expr) == _normalize_text_move(canonical_expr):
        return True, "exact_normalized"
    if (
        len(student_expr) <= _MAX_DIFF_EXPR_LEN
        and len(canonical_expr) <= _MAX_DIFF_EXPR_LEN
        and _math_equivalent(student_expr, canonical_expr)
    ):
        return True, "math_equivalence"
    return False, None


def _split_additive_terms(node: ast.AST, sign: int = 1) -> list[tuple[int, str]]:
    if isinstance(node, ast.Expression):
        return _split_additive_terms(node.body, sign)
    if isinstance(node, ast.UnaryOp):
        if isinstance(node.op, ast.UAdd):
            return _split_additive_terms(node.operand, sign)
        if isinstance(node.op, ast.USub):
            return _split_additive_terms(node.operand, -sign)
    if isinstance(node, ast.BinOp):
        if isinstance(node.op, ast.Add):
            return _split_additive_terms(node.left, sign) + _split_additive_terms(node.right, sign)
        if isinstance(node.op, ast.Sub):
            return _split_additive_terms(node.left, sign) + _split_additive_terms(node.right, -sign)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and node.value == 0:
        return []
    return [(sign, ast.unparse(node))]


def _comparison_pair(student_expr: str, expected_expr: str) -> tuple[str, str]:
    eq_s = _split_equation(student_expr)
    eq_e = _split_equation(expected_expr)
    if eq_s and eq_e:
        ls, rs = eq_s
        le, re_ = eq_e
        if _math_equivalent(ls, le):
            return rs, re_
        if _math_equivalent(rs, re_):
            return ls, le
        return f"({ls})-({rs})", f"({le})-({re_})"
    if eq_s and not eq_e:
        return eq_s[1], expected_expr
    if eq_e and not eq_s:
        return student_expr, eq_e[1]
    return student_expr, expected_expr


def _has_additive_term_sign_flip(comp_s: str, comp_e: str) -> bool:
    prep_s = _preprocess_math_text(comp_s)
    prep_e = _preprocess_math_text(comp_e)
    if not prep_s or not prep_e:
        return False
    try:
        parsed_s = validate_math_expr_ast(prep_s)
        parsed_e = validate_math_expr_ast(prep_e)
    except ValueError:
        return False

    s_terms = _split_additive_terms(parsed_s)
    e_terms = _split_additive_terms(parsed_e)
    if len(s_terms) < 2 or len(s_terms) != len(e_terms):
        return False

    used: set[int] = set()
    flipped = 0
    for s_sign, s_tex in s_terms:
        matched_idx: int | None = None
        for idx, (e_sign, e_tex) in enumerate(e_terms):
            if idx in used:
                continue
            if _math_equivalent(s_tex, e_tex):
                matched_idx = idx
                if s_sign != e_sign:
                    flipped += 1
                break
        if matched_idx is None:
            return False
        used.add(matched_idx)
    return flipped > 0


def _is_sign_flip(student_expr: str, expected_expr: str) -> bool:
    eq_s = _split_equation(student_expr)
    eq_e = _split_equation(expected_expr)
    if eq_s and eq_e:
        ls, rs = eq_s
        le, re_ = eq_e
        if _math_equivalent(ls, le) and _math_equivalent(rs, f"-({re_})"):
            return True
        if _math_equivalent(ls, f"-({le})") and _math_equivalent(rs, re_):
            return True
        if _math_equivalent(ls, re_) and _math_equivalent(rs, f"-({le})"):
            return True
        if _math_equivalent(ls, f"-({re_})") and _math_equivalent(rs, le):
            return True
        comp_s, comp_e = _comparison_pair(student_expr, expected_expr)
        return _has_additive_term_sign_flip(comp_s, comp_e)

    comp_s, comp_e = _comparison_pair(student_expr, expected_expr)
    if _math_equivalent(comp_s, f"-({comp_e})"):
        return True
    return _has_additive_term_sign_flip(comp_s, comp_e)


def _is_missing_factor(student_expr: str, expected_expr: str) -> bool:
    comp_s, comp_e = _comparison_pair(student_expr, expected_expr)
    prep_s = _preprocess_math_text(comp_s)
    prep_e = _preprocess_math_text(comp_e)
    if not prep_s or not prep_e:
        return False
    try:
        validate_math_expr_ast(prep_s)
        validate_math_expr_ast(prep_e)
    except ValueError:
        return False

    syms_s = _extract_math_symbols(prep_s)
    syms_e = _extract_math_symbols(prep_e)
    all_syms = sorted(syms_s | syms_e)

    # 1. Constant numeric ratio check via deterministic AST-safe probing
    rng = random.Random(20261127)
    ratios: list[float] = []
    for _ in range(12):
        env = {name: rng.uniform(1.17, 4.83) for name in all_syms}
        try:
            vs = _safe_eval_math_ast(prep_s, env)
            ve = _safe_eval_math_ast(prep_e, env)
        except Exception:
            continue
        if abs(ve) <= 1e-9:
            continue
        ratios.append(vs / ve)
    if len(ratios) >= 5:
        r0 = ratios[0]
        if abs(r0) > 1e-9 and abs(r0 - 1.0) > 1e-5 and abs(r0 + 1.0) > 1e-5:
            if all(abs(r - r0) <= 1e-4 * max(1.0, abs(r0)) for r in ratios[1:]):
                return True

    # 2. Single missing or extra symbolic factor (e.g., dropped mass m or omega factor)
    missing_syms = sorted(syms_e - syms_s)
    extra_syms = sorted(syms_s - syms_e)
    if len(missing_syms) == 1 and not extra_syms:
        sym = missing_syms[0]
        for cand in (
            f"({comp_s})*({sym})",
            f"({comp_s})/({sym})",
            f"({comp_s})*({sym})**2",
            f"({comp_s})/({sym})**2",
            f"-({comp_s})*({sym})",
            f"-({comp_s})/({sym})",
        ):
            if _math_equivalent(cand, comp_e):
                return True
    if len(extra_syms) == 1 and not missing_syms:
        sym = extra_syms[0]
        for cand in (
            f"({comp_s})/({sym})",
            f"({comp_s})*({sym})",
            f"-({comp_s})/({sym})",
            f"-({comp_s})*({sym})",
        ):
            if _math_equivalent(cand, comp_e):
                return True
    return False


def _is_dropped_term(student_expr: str, expected_expr: str) -> bool:
    comp_s, comp_e = _comparison_pair(student_expr, expected_expr)
    prep_s = _preprocess_math_text(comp_s)
    prep_e = _preprocess_math_text(comp_e)
    if not prep_s or not prep_e:
        return False
    try:
        parsed_s = validate_math_expr_ast(prep_s)
        parsed_e = validate_math_expr_ast(prep_e)
    except ValueError:
        return False

    s_terms = _split_additive_terms(parsed_s)
    e_terms = _split_additive_terms(parsed_e)
    if len(e_terms) < 2 or not (1 <= len(s_terms) < len(e_terms)):
        return False

    used: set[int] = set()
    for s_sign, s_tex in s_terms:
        matched_idx: int | None = None
        for idx, (e_sign, e_tex) in enumerate(e_terms):
            if idx in used:
                continue
            if s_sign == e_sign and _math_equivalent(s_tex, e_tex):
                matched_idx = idx
                break
        if matched_idx is None:
            return False
        used.add(matched_idx)
    return True


def _classify_step_error(
    student_expr: str,
    expected_expr: str,
    *,
    step_role: str,
    later_steps: list[dict[str, Any]],
) -> str:
    """Deterministically classify earliest divergence into one of 5 errorKind classes."""
    # Check if student jumped straight to a downstream step while skipping the expected step
    for later in later_steps:
        later_expr = str(later.get("expr") or "")
        if later_expr and _step_expr_match(student_expr, later_expr)[0]:
            return "algebraic_jump"

    if _is_sign_flip(student_expr, expected_expr):
        return "sign_flip"
    if _is_missing_factor(student_expr, expected_expr):
        return "missing_factor"
    if _is_dropped_term(student_expr, expected_expr):
        return "dropped_term"
    if str(step_role or "").strip().lower() == "axiom":
        return "wrong_axiom"
    return "algebraic_jump"


def _safe_step_label(step: dict[str, Any], canonical_expr: str) -> str:
    """Return a leak-free human label for a step (never exposes canonical `expr`)."""
    sid = str(step.get("id") or "step").strip()
    raw_label = str(step.get("label") or sid).strip()
    norm_lbl = _normalize_text_move(raw_label)
    norm_expr = _normalize_text_move(canonical_expr)
    eq = _split_equation(canonical_expr)
    rhs_norm = _normalize_text_move(eq[1]) if eq else ""
    if norm_expr and norm_expr in norm_lbl:
        return f"step {sid}"
    if rhs_norm and len(rhs_norm) >= 3 and rhs_norm in norm_lbl:
        return f"step {sid}"
    return raw_label


def _contains_expr_leak(text: str, canonical_expr: str) -> bool:
    norm_txt = _normalize_text_move(text)
    norm_expr = _normalize_text_move(canonical_expr)
    if norm_expr and norm_expr in norm_txt:
        return True
    eq = _split_equation(canonical_expr)
    if eq:
        rhs_norm = _normalize_text_move(eq[1])
        if rhs_norm and len(rhs_norm) >= 3 and rhs_norm in norm_txt:
            return True
    return False


def _build_socratic_nudges(
    *,
    break_line: int,
    step: dict[str, Any],
    error_kind: str,
    dep_labels: list[str],
    canonical_expr: str,
) -> dict[str, str]:
    """Generate calibrated R0/R1/R2 Socratic nudges with guaranteed ZERO canonical expr leak."""
    safe_label = _safe_step_label(step, canonical_expr)
    sid = str(step.get("id") or "this step")

    r0 = (
        f"Check line {break_line} ({safe_label}): your derivation diverges here. "
        "Re-verify this transition against the immediately preceding verified line."
    )

    r1_by_kind = {
        "sign_flip": (
            f"Sign & direction check on line {break_line} ({safe_label}): test your relation at a positive "
            "displacement/input—does the resulting direction oppose or reinforce the state?"
        ),
        "missing_factor": (
            f"Scaling & dimensional check on line {break_line} ({safe_label}): compare numerical coefficients "
            "and multiplying parameters on both sides—did a constant or scaling factor drop out?"
        ),
        "dropped_term": (
            f"Completeness check on line {break_line} ({safe_label}): account for every additive contribution "
            "carried forward from the verified prefix—did a term disappear?"
        ),
        "wrong_axiom": (
            f"Premise check on line {break_line} ({safe_label}): re-examine the governing physical or mathematical "
            "axiom required here before manipulating symbols."
        ),
        "algebraic_jump": (
            f"Transition check on line {break_line} ({safe_label}): write the explicit intermediate step linking "
            "the verified prefix to this claim without skipping an unestablished dependency."
        ),
    }
    r1 = r1_by_kind.get(error_kind, r1_by_kind["algebraic_jump"])

    dep_clause = (
        f"Combine the verified premise(s) ({', '.join(dep_labels)}) directly"
        if dep_labels
        else "Start directly from the governing definition and declared sign convention"
    )
    r2_focus_by_kind = {
        "sign_flip": "and check whether a negative/restoring sign is required to keep the system physically consistent.",
        "missing_factor": "and check dimensions plus a limiting case (e.g., doubling a parameter) to restore the missing factor.",
        "dropped_term": "and verify that every term from both sides of the conservation or balance relation survives.",
        "wrong_axiom": "and state the exact valid law for this regime before substituting into downstream steps.",
        "algebraic_jump": "and isolate the single missing intermediate relation before jumping to the conclusion.",
    }
    r2 = f"Line {break_line} scaffold ({safe_label}): {dep_clause}, {r2_focus_by_kind.get(error_kind, r2_focus_by_kind['algebraic_jump'])}"

    # Defense-in-depth zero-leak safeguard
    nudges = {"R0": r0, "R1": r1, "R2": r2}
    for level, msg in list(nudges.items()):
        if _contains_expr_leak(msg, canonical_expr):
            nudges[level] = (
                f"Line {break_line} (step {sid}, {level}): re-check your {error_kind.replace('_', ' ')} "
                "using the verified prefix and a dimensional/sign sanity check."
            )
    return nudges


def diff_handwritten_steps(
    kg_dir: Path | str | None,
    derivation_id: str,
    student_steps: list[dict[str, Any]],
    *,
    auto_spawn_fork: bool = True,
    workspace: Path | str | None = None,
) -> dict[str, Any]:
    """Structured Proof-Graph Handwriting Diff against a Causal Step DAG in `derivations.jsonl`.

    Aligns `student_steps` sequentially against topologically ordered derivation steps
    (axioms -> intermediate steps -> theorem), localizes the earliest break, classifies
    `errorKind`, builds zero-leak R0/R1/R2 Socratic nudges, and optionally auto-spawns
    a Blunder FEN Fork + structured mistake + root-cause diagnostic probe.
    """
    if not isinstance(student_steps, list):
        raise ValueError("student_steps must be a list of step objects.")
    if len(student_steps) > _MAX_DIFF_STEPS:
        raise ValueError(f"student_steps exceeds maximum limit of {_MAX_DIFF_STEPS} steps.")

    norm_student: list[dict[str, Any]] = []
    for idx, raw in enumerate(student_steps, start=1):
        if not isinstance(raw, dict):
            raise ValueError(f"student_steps[{idx - 1}] must be a JSON object.")
        raw_line = raw.get("line")
        if isinstance(raw_line, int) and not isinstance(raw_line, bool) and raw_line > 0:
            line_no = raw_line
        else:
            line_no = idx
        expr = str(raw.get("expr") if raw.get("expr") is not None else raw.get("expression") or "").strip()
        if len(expr) > _MAX_DIFF_EXPR_LEN:
            raise ValueError(f"Student step line {line_no} exceeds {_MAX_DIFF_EXPR_LEN} character limit.")
        norm_student.append(
            {
                "line": line_no,
                "expr": expr,
                "note": str(raw.get("note") or "").strip(),
            }
        )

    kdir, ws = _resolve_kg_and_ws(kg_dir=kg_dir, workspace=workspace)
    deriv = _find_derivation(derivation_id, kg_dir=kdir)
    dag_steps = _topological_steps(list(deriv.get("steps") or []))
    step_by_id = {str(s["id"]): s for s in dag_steps}

    # Precompute transitive dependencies in topological order
    trans_deps: dict[str, set[str]] = {}
    for s in dag_steps:
        sid = str(s["id"])
        deps_set: set[str] = set()
        for d in s.get("dependsOn") or []:
            ds = str(d)
            if ds in step_by_id:
                deps_set.add(ds)
                deps_set.update(trans_deps.get(ds, set()))
        trans_deps[sid] = deps_set

    verified_ids: set[str] = set()
    verified_prefix: list[dict[str, Any]] = []
    status_by_id: dict[str, dict[str, Any]] = {}

    earliest_break_step: dict[str, Any] | None = None
    earliest_break_line: int | None = None
    broken_student_expr: str = ""
    error_kind: str | None = None

    for st in norm_student:
        if earliest_break_step is not None or len(verified_ids) >= len(dag_steps):
            break

        unverified = [s for s in dag_steps if str(s["id"]) not in verified_ids]
        ready = [
            s
            for s in unverified
            if all(str(d) in verified_ids for d in (s.get("dependsOn") or []))
        ]
        expected = ready[0] if ready else unverified[0]
        expected_id = str(expected["id"])

        matched, method = _step_expr_match(st["expr"], str(expected.get("expr") or ""))
        if matched:
            verified_ids.add(expected_id)
            verified_prefix.append(expected)
            status_by_id[expected_id] = {
                "stepId": expected_id,
                "label": _safe_step_label(expected, str(expected.get("expr") or "")),
                "role": expected.get("role", "step"),
                "dependsOn": list(expected.get("dependsOn") or []),
                "status": "verified",
                "studentLine": st["line"],
                "studentExpr": st["expr"],
                "matchMethod": method,
                "brokenDependencies": [],
            }
            continue

        # Allow swapping independent ready axioms/branches when all prerequisites are already verified
        alt_matched: tuple[dict[str, Any], str | None] | None = None
        for cand in ready[1:]:
            ok_alt, m_alt = _step_expr_match(st["expr"], str(cand.get("expr") or ""))
            if ok_alt:
                alt_matched = (cand, m_alt)
                break
        if alt_matched is not None:
            cand_step, m_alt = alt_matched
            cid = str(cand_step["id"])
            verified_ids.add(cid)
            verified_prefix.append(cand_step)
            status_by_id[cid] = {
                "stepId": cid,
                "label": _safe_step_label(cand_step, str(cand_step.get("expr") or "")),
                "role": cand_step.get("role", "step"),
                "dependsOn": list(cand_step.get("dependsOn") or []),
                "status": "verified",
                "studentLine": st["line"],
                "studentExpr": st["expr"],
                "matchMethod": m_alt,
                "brokenDependencies": [],
            }
            continue

        # Earliest divergence localized
        earliest_break_step = expected
        earliest_break_line = st["line"]
        broken_student_expr = st["expr"]
        later_steps = [s for s in unverified if str(s["id"]) != expected_id]
        error_kind = _classify_step_error(
            broken_student_expr,
            str(expected.get("expr") or ""),
            step_role=str(expected.get("role") or "step"),
            later_steps=later_steps,
        )
        status_by_id[expected_id] = {
            "stepId": expected_id,
            "label": _safe_step_label(expected, str(expected.get("expr") or "")),
            "role": expected.get("role", "step"),
            "dependsOn": list(expected.get("dependsOn") or []),
            "status": "broken",
            "studentLine": st["line"],
            "studentExpr": st["expr"],
            "matchMethod": None,
            "errorKind": error_kind,
            "brokenDependencies": [],
        }
        break

    # Populate remaining steps as `skipped_dependency` or `not_reached`
    broken_id = str(earliest_break_step["id"]) if earliest_break_step is not None else None
    for s in dag_steps:
        sid = str(s["id"])
        if sid in status_by_id:
            continue
        direct_unmet = [str(d) for d in (s.get("dependsOn") or []) if str(d) not in verified_ids]
        trans_unmet = [d for d in sorted(trans_deps.get(sid, set())) if d not in verified_ids]
        if broken_id is not None and (broken_id in trans_deps.get(sid, set()) or direct_unmet or trans_unmet):
            st_name = "skipped_dependency"
            b_deps = direct_unmet if direct_unmet else trans_unmet
        else:
            st_name = "not_reached"
            b_deps = []
        status_by_id[sid] = {
            "stepId": sid,
            "label": _safe_step_label(s, str(s.get("expr") or "")),
            "role": s.get("role", "step"),
            "dependsOn": list(s.get("dependsOn") or []),
            "status": st_name,
            "studentLine": None,
            "studentExpr": None,
            "matchMethod": None,
            "brokenDependencies": b_deps,
        }

    aligned_steps = [status_by_id[str(s["id"])] for s in dag_steps]
    all_verified = len(verified_ids) == len(dag_steps) and earliest_break_step is None
    verified_prefix_exprs = [str(s.get("expr") or "") for s in verified_prefix if str(s.get("expr") or "").strip()]

    nudges: dict[str, str] | None = None
    mistake_category: str | None = None
    spawned_fork: dict[str, Any] | None = None
    logged_mistake_id: str | None = None
    diagnostic_probe: dict[str, Any] | None = None
    evidence_recorded = False

    if earliest_break_step is not None and earliest_break_line is not None and error_kind is not None:
        expected_expr = str(earliest_break_step.get("expr") or "")
        safe_break_label = _safe_step_label(earliest_break_step, expected_expr)
        dep_labels = [
            _safe_step_label(step_by_id[str(d)], str(step_by_id[str(d)].get("expr") or ""))
            for d in (earliest_break_step.get("dependsOn") or [])
            if str(d) in step_by_id
        ]
        nudges = _build_socratic_nudges(
            break_line=earliest_break_line,
            step=earliest_break_step,
            error_kind=error_kind,
            dep_labels=dep_labels,
            canonical_expr=expected_expr,
        )
        mistake_category = _ERROR_KIND_TO_MISTAKE_CATEGORY.get(error_kind, "algebra_slip")

        _record_telemetry_safe(
            kdir,
            domain=str(deriv.get("domain") or "General"),
            prop_id=deriv.get("propId"),
            kind="derivation_diff",
            correct=False,
            difficulty=1580,
            mistake_category=mistake_category,
        )

        if auto_spawn_fork:
            # 1. Log structured mistake record (non-fatal)
            try:
                mid = f"m-{uuid.uuid4().hex[:8]}"
                mistake_rec = {
                    "id": mid,
                    "date": today_iso(),
                    "subject": str(deriv.get("domain") or "General"),
                    "chapter": str(deriv.get("chapter") or "General"),
                    "propId": str(deriv.get("propId") or ""),
                    "category": mistake_category,
                    "triggerCue": f"{deriv['title']} — line {earliest_break_line} ({safe_break_label}) [{error_kind}]",
                    "wrongMove": broken_student_expr or "(blank)",
                    "correctMove": nudges["R1"],
                    "sourceRef": f"derivation:{deriv['id']}#{broken_id}",
                }
                append_jsonl_locked(kdir / "mistakes.jsonl", mistake_rec)
                logged_mistake_id = mid
            except Exception:
                logged_mistake_id = None

            # 2. Auto-spawn/update Blunder FEN Fork frozen right at the verified prefix (non-fatal)
            try:
                from syntopik.forks import capture_fork

                seed_key = f"{deriv['id']}|{broken_id}|{error_kind}"
                fid = f"fork-pdiff-{hashlib.sha256(seed_key.encode('utf-8')).hexdigest()[:8]}"
                spawned_fork = capture_fork(
                    prop_id=str(deriv.get("propId") or ""),
                    prefix_steps=verified_prefix_exprs,
                    fork_prompt=(
                        f"Continue '{deriv['title']}' from the verified prefix: "
                        f"write the exact next step ({safe_break_label})."
                    ),
                    sealed_blunder=broken_student_expr or "(blank)",
                    sealed_winning_move=expected_expr,
                    refutation_msg=nudges["R1"],
                    mistake_id=logged_mistake_id,
                    subject=str(deriv.get("domain") or "General"),
                    chapter=str(deriv.get("chapter") or "General"),
                    verify_spec={"engine": "math"},
                    fork_id=fid,
                    kg_dir=kdir,
                )
            except Exception:
                spawned_fork = None

        # 3. Root-cause Bayesian diagnostic probe on suspect ancestors (non-fatal, read-only)
        prop_id = str(deriv.get("propId") or "").strip()
        if prop_id:
            try:
                from syntopik.root_cause import select_diagnostic_probe

                probe_res = select_diagnostic_probe(kdir, prop_id)
                if isinstance(probe_res, dict) and probe_res.get("ok"):
                    diagnostic_probe = probe_res
            except Exception:
                diagnostic_probe = None
    elif all_verified:
        _record_telemetry_safe(
            kdir,
            domain=str(deriv.get("domain") or "General"),
            prop_id=deriv.get("propId"),
            kind="derivation_diff",
            correct=True,
            difficulty=1580,
        )
        evidence_recorded = _record_evidence_safe(
            ws,
            deriv.get("propId"),
            3,
            "derivation_proof_diff",
            {"derivationId": deriv["id"], "verifiedSteps": len(verified_ids)},
        )

    return {
        "ok": True,
        "derivationId": deriv["id"],
        "propId": deriv.get("propId"),
        "title": deriv.get("title"),
        "domain": deriv.get("domain"),
        "chapter": deriv.get("chapter"),
        "allVerified": all_verified,
        "verifiedCount": len(verified_ids),
        "totalSteps": len(dag_steps),
        "studentStepCount": len(norm_student),
        "earliestBreakLine": earliest_break_line,
        "earliestBreakStepId": broken_id,
        "errorKind": error_kind,
        "mistakeCategory": mistake_category,
        "nudges": nudges,
        "verifiedPrefix": verified_prefix_exprs,
        "steps": aligned_steps,
        "spawnedFork": spawned_fork,
        "loggedMistakeId": logged_mistake_id,
        "diagnosticProbe": diagnostic_probe,
        "evidenceRecorded": evidence_recorded,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _load_json_arg(raw: str) -> Any:
    s = str(raw or "").strip()
    if s.startswith("@"):
        return json.loads(Path(s[1:]).expanduser().read_text(encoding="utf-8"))
    p = Path(s).expanduser()
    if not s.startswith(("{", "[")) and p.is_file():
        return json.loads(p.read_text(encoding="utf-8"))
    return json.loads(s)


def _build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--workspace", type=Path, default=argparse.SUPPRESS, help="Workspace root override.")
    common.add_argument("--kg-dir", type=Path, default=argparse.SUPPRESS, help="knowledge-graph directory override.")
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="Emit JSON output.")

    parser = argparse.ArgumentParser(
        prog="derivation_forge.py",
        description="Clean-Room Derivation Reconstruction & Axiom-Cut Surgery CLI.",
        parents=[common],
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_create = sub.add_parser("create", parents=[common], help="Create or update a derivation DAG in derivations.jsonl.")
    p_create.add_argument("--spec", default=None, help="JSON string, file path, or @path to derivation spec.")
    p_create.add_argument("--id", default=None, help="Derivation ID.")
    p_create.add_argument("--prop-id", default=None, help="Linked proposition ID.")
    p_create.add_argument("--chapter", default="General", help="Chapter name.")
    p_create.add_argument("--title", default=None, help="Derivation title.")
    p_create.add_argument("--domain", default="General", help="Domain / subject.")
    p_create.add_argument("--steps", default=None, help="JSON array (or @path) of derivation steps.")

    p_list = sub.add_parser("list", parents=[common], help="List derivations in derivations.jsonl.")
    p_list.add_argument("--prop-id", default=None, help="Filter by proposition ID.")
    p_list.add_argument("--chapter", default=None, help="Filter by chapter.")
    p_list.add_argument("--domain", default=None, help="Filter by domain.")

    p_bo = sub.add_parser("blackout", parents=[common], help="Build a level 1..3 clean-room blackout challenge.")
    p_bo.add_argument("--id", required=True, help="Derivation ID.")
    p_bo.add_argument("--level", type=int, default=1, choices=[1, 2, 3], help="Blackout difficulty (1..3).")

    p_chk = sub.add_parser("check", parents=[common], help="Check a learner's reconstructed step expression.")
    p_chk.add_argument("--id", required=True, help="Derivation ID.")
    p_chk.add_argument("--step", required=True, help="Step ID to grade.")
    p_chk.add_argument("--answer", required=True, help="Learner's mathematical expression or equation.")
    p_chk.add_argument("--reveal", action="store_true", help="Reveal canonical step expression on miss.")

    p_cut = sub.add_parser("cut", parents=[common], help="Run Axiom-Cut Surgery and optionally grade localization.")
    p_cut.add_argument("--id", required=True, help="Derivation ID.")
    p_cut.add_argument("--axiom", required=True, help="Axiom/step ID to invalidate.")
    p_cut.add_argument("--guess-step", default=None, help="Learner's guess for the earliest broken step.")

    p_diff = sub.add_parser("diff", parents=[common], help="Diff handwritten derivation steps against the Causal Step DAG.")
    p_diff.add_argument("--id", required=True, help="Derivation ID.")
    p_diff.add_argument("--steps-json", dest="steps_json", required=True, help="JSON array, file path, or @path of student steps.")
    p_diff.add_argument("--no-spawn-fork", dest="no_spawn_fork", action="store_true", help="Do not auto-spawn a Blunder FEN fork or log mistake on break.")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    ws = getattr(args, "workspace", None)
    kg_dir = getattr(args, "kg_dir", None)
    as_json = bool(getattr(args, "json", False))

    try:
        if args.command == "create":
            if args.spec:
                spec = _load_json_arg(args.spec)
            else:
                if not args.steps:
                    raise ValueError("Provide --spec <json/file> or --steps <json>.")
                spec = {
                    "id": args.id,
                    "propId": args.prop_id,
                    "chapter": args.chapter,
                    "title": args.title,
                    "domain": args.domain,
                    "steps": _load_json_arg(args.steps),
                }
            res = create_derivation(spec, kg_dir=kg_dir, workspace=ws)
        elif args.command == "list":
            res = list_derivations(
                kg_dir=kg_dir,
                workspace=ws,
                prop_id=args.prop_id,
                chapter=args.chapter,
                domain=args.domain,
            )
        elif args.command == "blackout":
            res = get_blackout_challenge(args.id, level=args.level, kg_dir=kg_dir, workspace=ws)
        elif args.command == "check":
            res = check_blackout_step(
                args.id,
                args.step,
                args.answer,
                kg_dir=kg_dir,
                workspace=ws,
                reveal=bool(args.reveal),
            )
        elif args.command == "cut":
            res = grade_axiom_cut(
                args.id,
                args.axiom,
                guess_step_id=args.guess_step,
                kg_dir=kg_dir,
                workspace=ws,
            )
        elif args.command == "diff":
            student_steps = _load_json_arg(args.steps_json)
            res = diff_handwritten_steps(
                kg_dir,
                args.id,
                student_steps,
                auto_spawn_fork=not bool(args.no_spawn_fork),
                workspace=ws,
            )
        else:
            parser.error(f"Unknown command: {args.command}")
            return 2
    except Exception as exc:  # noqa: BLE001
        res = {"ok": False, "command": args.command, "error": str(exc)}

    if as_json:
        print(json.dumps(res, indent=2, ensure_ascii=False))
    else:
        if not res.get("ok"):
            print(f"ERROR: {res.get('error', 'Failed')}", file=sys.stderr)
        elif args.command == "create":
            print(f"{res['action'].title()} derivation {res['id']} ({len(res['derivation']['steps'])} steps).")
        elif args.command == "list":
            print(f"Derivations ({res['count']}):")
            for d in res["derivations"]:
                print(f"  - {d['id']} [{d['domain']}] {d['title']} ({d['stepCount']} steps)")
        elif args.command == "blackout":
            print(f"Blackout L{res['level']} · {res['title']} ({res['hiddenCount']}/{res['totalSteps']} hidden):")
            for s in res["steps"]:
                body = "[HIDDEN]" if s["hidden"] else s["expr"]
                print(f"  [{s['id']} · {s['role']}] {s['label']}: {body}")
        elif args.command == "check":
            verdict = "PASS" if res["passed"] else "MISS"
            print(f"[{verdict}] {res['derivationId']}::{res['stepId']} ({res['method']})")
        elif args.command == "cut":
            print(res["explanation"])
        elif args.command == "diff":
            if res.get("allVerified"):
                print(f"[VERIFIED] {res['derivationId']}: {res['verifiedCount']}/{res['totalSteps']} steps verified.")
            elif res.get("earliestBreakLine") is not None:
                print(
                    f"[BREAK line {res['earliestBreakLine']}] {res['derivationId']}::{res['earliestBreakStepId']} "
                    f"({res['errorKind']}) · {res['verifiedCount']}/{res['totalSteps']} verified"
                )
                nudges = res.get("nudges") or {}
                for lvl in ("R0", "R1", "R2"):
                    if nudges.get(lvl):
                        print(f"  {lvl}: {nudges[lvl]}")
                if isinstance(res.get("spawnedFork"), dict) and res["spawnedFork"].get("id"):
                    print(f"  Fork: {res['spawnedFork']['id']}")
                probe = (res.get("diagnosticProbe") or {}).get("bestProbe")
                if isinstance(probe, dict) and probe.get("propId"):
                    print(f"  Diagnostic Probe: {probe['propId']} (deltaH={probe.get('deltaHBits', 0)} bits)")
            else:
                print(f"[INCOMPLETE] {res['derivationId']}: {res['verifiedCount']}/{res['totalSteps']} steps verified.")
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
