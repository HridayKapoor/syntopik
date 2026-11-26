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

Pure Python 3.12+ standard library (optional lazy SymPy fallback).
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from collections import deque
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from syntopik.forks import _math_equivalent, _normalize_text_move  # noqa: E402
from syntopik.paths import resolve_kg_dir, resolve_workspace  # noqa: E402
from syntopik.schemas import now_iso, slugify  # noqa: E402
from syntopik.storage import load_jsonl, save_jsonl_atomic, workspace_lock  # noqa: E402

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
    "main",
]

DERIVATIONS_FILENAME = "derivations.jsonl"
VALID_STEP_ROLES: frozenset[str] = frozenset({"axiom", "step", "theorem"})
_MAX_ANSWER_LEN = 1024


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
            mistake_category=None if correct else "concept_gap",
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
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
