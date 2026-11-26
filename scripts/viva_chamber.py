#!/usr/bin/env python3
"""5-Turn Adversarial Viva Voce Defense Chamber (`scripts/viva_chamber.py`).

Conducts a structured 5-turn defense for any Knowledge Graph proposition in
`knowledge-graph/viva-sessions.jsonl`:
  - Turn 1: Commitment Claim (establishes the Turn-1 Commitment Ledger)
  - Turn 2: Boundary Perturbation (grounded in `boundaryConditions[]` / premises)
  - Turn 3: Assumption / Anti-Pattern Attack (grounded in `antiPatterns[]` / `mistakes.jsonl` / `chains.jsonl`)
  - Turn 4: Transfer Perturbation (edge regime / downstream consequence)
  - Turn 5: Sharpened Synthesis (reconciling Turn-1 claim with Turns 2-4)

Evaluates deterministically across `clarity`, `boundary_awareness`, `transfer`,
`calibration`, and `contradictionCheck` (against the Turn-1 Commitment Ledger).
Promotes T4 evidence on pass (`>= 0.75`), or demotes a fragile `solid` node
back to `shaky` on critical boundary collapse.

Pure Python 3.12+ standard library only (zero external dependencies).
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
import unicodedata
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from syntopik.paths import resolve_kg_dir, resolve_workspace  # noqa: E402
from syntopik.schemas import normalize_chain, now_iso  # noqa: E402
from syntopik.storage import load_jsonl, save_jsonl_atomic, workspace_lock  # noqa: E402

__all__ = [
    "VIVA_SESSIONS_FILENAME",
    "resolve_viva_path",
    "load_viva_sessions",
    "start_viva",
    "submit_viva_turn",
    "get_viva_status",
    "list_viva_sessions",
    "main",
]

VIVA_SESSIONS_FILENAME = "viva-sessions.jsonl"

_STOP_WORDS: frozenset[str] = frozenset(
    (
        "a an the and or of to in on at by for from with as is are was were be been being "
        "it its this that these those which who what when where how does do did can could "
        "should would will may might must into over under between through about your my our "
        "we they them he she his her you i me"
    ).split()
)

_MECHANISM_CUES: frozenset[str] = frozenset(
    {
        "because",
        "since",
        "therefore",
        "implies",
        "causes",
        "breaks",
        "fails",
        "forces",
        "requires",
        "violates",
        "prevents",
        "conserves",
        "depends",
        "scales",
        "diverges",
        "vanishes",
        "linear",
        "nonlinear",
        "mechanism",
        "invariant",
        "proof",
        "derives",
        "leads",
        "guarantees",
        "contradicts",
    }
)

_BOUNDARY_CUES: frozenset[str] = frozenset(
    {
        "boundary",
        "limit",
        "regime",
        "condition",
        "assumption",
        "assumes",
        "requires",
        "unless",
        "only",
        "provided",
        "violated",
        "breaks",
        "fails",
        "invalid",
        "small",
        "large",
        "threshold",
        "edge",
        "exception",
        "fails",
        "quorum",
        "majority",
        "partition",
        "equilibrium",
        "approximation",
        "non",
        "zero",
        "overflow",
        "saturation",
    }
)

_TRANSFER_CUES: frozenset[str] = frozenset(
    {
        "instead",
        "counterexample",
        "refute",
        "false",
        "wrong",
        "misleading",
        "downstream",
        "changes",
        "shifts",
        "verify",
        "test",
        "measure",
        "compare",
        "predict",
        "example",
        "case",
        "scenario",
        "therefore",
        "consequence",
        "reconcile",
        "sharpen",
    }
)

_ABSOLUTE_OVERCLAIM_RE = re.compile(
    r"\b(always holds|always works|always true|never fails|no exceptions|works everywhere|holds unconditionally|regardless of)\b",
    flags=re.IGNORECASE,
)
_REVISION_ACK_RE = re.compile(
    r"\b(revise|revising|correction|corrected|narrow|narrowed|earlier i|turn 1|qualify|caveat|except when)\b",
    flags=re.IGNORECASE,
)


def resolve_viva_path(
    *,
    workspace: Path | str | None = None,
    kg_dir: Path | str | None = None,
) -> Path:
    return resolve_kg_dir(workspace=workspace, explicit=kg_dir) / VIVA_SESSIONS_FILENAME


def load_viva_sessions(
    kg_dir: Path | str | None = None,
    *,
    workspace: Path | str | None = None,
) -> list[dict[str, Any]]:
    return load_jsonl(resolve_viva_path(workspace=workspace, kg_dir=kg_dir))


def _tokenize(text: str) -> list[str]:
    norm = unicodedata.normalize("NFKC", str(text or "")).lower()
    raw = re.findall(r"[a-z][a-z0-9_-]{1,}", norm)
    return [t for t in raw if t not in _STOP_WORDS]


def _clamp_confidence(confidence: float | None) -> float:
    if confidence is None:
        return 0.5
    val = float(confidence)
    if not math.isfinite(val):
        return 0.5
    if 1.0 < val <= 100.0:
        val = val / 100.0
    return round(max(0.0, min(1.0, val)), 4)


def _extract_commitment_ledger(response: str) -> dict[str, Any]:
    text = str(response or "").strip()
    low = text.lower()
    tokens = _tokenize(text)
    has_universal = bool(_ABSOLUTE_OVERCLAIM_RE.search(text))
    # Capture simple "requires X" / "needs X" / "independent of X" / "depends on X" predicates
    requires_terms = re.findall(r"\b(?:requires?|needs?|assumes?)\s+([a-z0-9_\- ]{2,36})", low)
    forbids_terms = re.findall(r"\b(?:does not require|doesn't require|independent of|never needs?)\s+([a-z0-9_\- ]{2,36})", low)
    return {
        "rawClaim": text,
        "keyTokens": sorted(set(tokens))[:32],
        "assertedUniversal": has_universal,
        "requiresPhrases": [p.strip(" .,;:") for p in requires_terms[:6]],
        "forbidsPhrases": [p.strip(" .,;:") for p in forbids_terms[:6]],
    }


def _check_contradictions(
    turn_index: int,
    response: str,
    confidence: float,
    ledger: dict[str, Any] | None,
    content_tokens: list[str],
    turn_quality: float,
) -> dict[str, Any]:
    contradictions: list[str] = []
    bluff_detected = False
    low = response.lower()
    has_revision_ack = bool(_REVISION_ACK_RE.search(response))

    # High-confidence bluff detection: empty/tiny/ungrounded response asserted with high confidence
    if confidence >= 0.80 and (len(content_tokens) < 5 or turn_quality < 0.35):
        bluff_detected = True
        contradictions.append(
            f"Turn {turn_index} bluff flag: confidence={round(confidence * 100)}% with minimal/ungrounded defense."
        )

    if ledger and turn_index >= 2:
        # 1. Turn 1 claimed unconditional universality, then later turn admits failure without acknowledging revision
        if ledger.get("assertedUniversal") and any(w in low for w in ("breaks", "fails", "invalid", "does not hold", "doesn't hold")):
            if not has_revision_ack:
                contradictions.append(
                    f"Turn {turn_index} contradicts Turn-1 universal overclaim without acknowledging the revision."
                )

        # 2. Direct polarity flip on `requires X` vs `does not require X`
        for req in ledger.get("requiresPhrases") or []:
            req_toks = set(_tokenize(req))
            if not req_toks:
                continue
            for neg_m in re.findall(r"\b(?:does not require|doesn't require|no need for|independent of)\s+([a-z0-9_\- ]{2,36})", low):
                if req_toks & set(_tokenize(neg_m)) and not has_revision_ack:
                    contradictions.append(
                        f"Turn {turn_index} flipped Turn-1 requirement '{req}' to '{neg_m.strip()}'."
                    )
                    break

        for forb in ledger.get("forbidsPhrases") or []:
            forb_toks = set(_tokenize(forb))
            if not forb_toks:
                continue
            for pos_m in re.findall(r"\b(?:requires?|depends on|needs?)\s+([a-z0-9_\- ]{2,36})", low):
                if forb_toks & set(_tokenize(pos_m)) and not has_revision_ack:
                    contradictions.append(
                        f"Turn {turn_index} contradicted Turn-1 exclusion '{forb}' with '{pos_m.strip()}'."
                    )
                    break

        # 3. Late turn reasserts unconditional universality during a boundary perturbation
        if turn_index in (2, 3) and _ABSOLUTE_OVERCLAIM_RE.search(response) and not any(
            neg in low for neg in ("not always", "doesn't always", "does not always", "false that", "wrong to say")
        ):
            contradictions.append(
                f"Turn {turn_index} insisted the rule holds unconditionally under a boundary/assumption attack."
            )

    penalty = min(0.45, 0.18 * len(contradictions) + (0.12 if bluff_detected else 0.0))
    return {
        "flagged": bool(contradictions or bluff_detected),
        "bluffDetected": bluff_detected,
        "contradictions": contradictions,
        "penalty": round(penalty, 4),
    }


def _score_turn(
    turn_index: int,
    response: str,
    confidence: float,
    grounding: dict[str, Any],
    ledger: dict[str, Any] | None,
) -> dict[str, Any]:
    tokens = _tokenize(response)
    tok_set = set(tokens)
    word_count = len(response.split())

    domain_toks = set(grounding.get("domainTokens") or [])
    boundary_toks = set(grounding.get("boundaryTokens") or [])
    anti_toks = set(grounding.get("antiPatternTokens") or [])

    # 1. Clarity: substantive length + mechanism connectors + domain grounding
    length_score = min(1.0, word_count / 20.0) if word_count >= 5 else word_count / 15.0
    mech_hits = len(tok_set & _MECHANISM_CUES)
    mech_score = min(1.0, mech_hits / 2.0)
    ground_hits = len(tok_set & domain_toks)
    ground_score = min(1.0, ground_hits / max(1.0, min(4.0, len(domain_toks) * 0.4))) if domain_toks else 0.6
    clarity = round(0.35 * length_score + 0.35 * mech_score + 0.30 * ground_score, 4)

    # 2. Boundary awareness: explicit boundary cues + grounding in boundaryConditions/premises; penalize unhedged universal claims
    bc_cue_hits = len(tok_set & _BOUNDARY_CUES)
    bc_ground_hits = len(tok_set & boundary_toks)
    bc_score = min(1.0, 0.55 * min(1.0, bc_cue_hits / 2.0) + 0.45 * (min(1.0, bc_ground_hits / 2.0) if boundary_toks else min(1.0, bc_cue_hits / 3.0)))
    if _ABSOLUTE_OVERCLAIM_RE.search(response) and not any(
        neg in response.lower() for neg in ("not always", "does not always", "doesn't always", "false", "refute")
    ):
        bc_score = max(0.0, bc_score - 0.45)
    boundary_awareness = round(bc_score, 4)

    # 3. Transfer: refutation / consequence / anti-pattern engagement / synthesis
    tr_cue_hits = len(tok_set & _TRANSFER_CUES)
    anti_hits = len(tok_set & anti_toks)
    if turn_index == 1:
        transfer = round(min(1.0, 0.55 * ground_score + 0.45 * min(1.0, (bc_cue_hits + mech_hits) / 3.0)), 4)
    else:
        tr_base = 0.50 * min(1.0, (tr_cue_hits + mech_hits) / 3.0) + 0.30 * ground_score + 0.20 * (min(1.0, anti_hits / 1.0) if anti_toks else min(1.0, tr_cue_hits / 2.0))
        transfer = round(min(1.0, tr_base), 4)

    turn_quality = round(0.35 * clarity + 0.35 * boundary_awareness + 0.30 * transfer, 4)
    contradiction = _check_contradictions(turn_index, response, confidence, ledger, tokens, turn_quality)

    # 4. Calibration: 1 - Brier squared error between learner confidence and actual turn quality
    effective_q = max(0.0, turn_quality - contradiction["penalty"])
    calibration = round(max(0.0, 1.0 - ((confidence - effective_q) ** 2)), 4)

    return {
        "clarity": clarity,
        "boundary_awareness": boundary_awareness,
        "transfer": transfer,
        "calibration": calibration,
        "turnQuality": effective_q,
        "contradictionCheck": contradiction,
    }


def _build_defense_prompts(
    prop: dict[str, Any],
    premises: list[str],
    dependents: list[str],
    boundary_conditions: list[str],
    anti_patterns: list[str],
) -> list[dict[str, Any]]:
    title = str(prop.get("title") or prop.get("id"))
    bc_focus = (
        boundary_conditions[0]
        if boundary_conditions
        else (f"prerequisite `{premises[0]}`" if premises else "its primary validity assumption")
    )
    anti_focus = (
        anti_patterns[0]
        if anti_patterns
        else (
            f"assuming `{title}` holds even when `{'` ∧ `'.join(premises[:2])}` fail"
            if premises
            else f"applying `{title}` purely by surface formula matching without checking constraints"
        )
    )
    transfer_target = (
        f"downstream concept `{dependents[0]}`"
        if dependents
        else "an extreme limiting regime (parameters -> 0 or infinity)"
    )

    return [
        {
            "turn": 1,
            "stage": "commitment_claim",
            "prompt": (
                f"Turn 1 (Commitment Claim): State the unconditional truth of `{title}` in your own words, "
                f"name the exact conditions it requires, and specify when it does NOT apply."
            ),
        },
        {
            "turn": 2,
            "stage": "boundary_perturbation",
            "prompt": (
                f"Turn 2 (Boundary Perturbation): Suppose `{bc_focus}` is violated or pushed past its valid regime. "
                f"Does your Turn-1 claim survive? Identify the exact step or mechanism that breaks first."
            ),
        },
        {
            "turn": 3,
            "stage": "assumption_attack",
            "prompt": (
                f"Turn 3 (Assumption Attack): An adversary claims: \"{anti_focus}\". "
                f"Refute this concretely using the underlying mechanism or a minimal counterexample."
            ),
        },
        {
            "turn": 4,
            "stage": "transfer_perturbation",
            "prompt": (
                f"Turn 4 (Transfer Perturbation): Push `{title}` into {transfer_target}. "
                f"What changes structurally or quantitatively, and what check verifies your prediction?"
            ),
        },
        {
            "turn": 5,
            "stage": "synthesis",
            "prompt": (
                f"Turn 5 (Final Synthesis): Reconcile your Turn-1 commitment with Turns 2-4. "
                f"State the sharpened invariant (mechanism + exact boundary + failure signature) in 2-3 sentences."
            ),
        },
    ]


def _record_telemetry_safe(
    kg_dir: Path,
    *,
    domain: str,
    prop_id: str,
    correct: bool,
    confidence: float | None,
) -> None:
    try:
        from syntopik.learner_model import record_telemetry_event

        record_telemetry_event(
            kg_dir,
            domain=domain,
            prop_id=prop_id,
            kind="viva",
            correct=correct,
            difficulty=1650,
            confidence=confidence,
            duration_ms=None,
            hints_used=0,
            mistake_category=None if correct else "condition_missed",
            pf_outcome=None,
            skipped=False,
        )
    except Exception:
        pass


def _record_t4_evidence_safe(
    workspace: Path,
    prop_id: str,
    detail: dict[str, Any],
) -> bool:
    try:
        from syntopik.evidence import record_evidence

        record_evidence(workspace, prop_id, 4, "viva_chamber", detail=detail, verified=True)
        return True
    except Exception:
        return False


def _demote_solid_prop_to_shaky(kg_dir: Path, prop_id: str, reason: str) -> bool:
    props_path = kg_dir / "propositions.jsonl"
    with workspace_lock(props_path):
        props = load_jsonl(props_path)
        for p in props:
            if str(p.get("id") or "") == prop_id:
                if str(p.get("status") or "").lower() == "solid":
                    p["status"] = "shaky"
                    old_notes = str(p.get("notes") or "").strip()
                    tag = f"[Viva demotion: {reason}]"
                    p["notes"] = f"{old_notes} {tag}".strip() if old_notes else tag
                    save_jsonl_atomic(props_path, props)
                    return True
                return False
    return False


# ---------------------------------------------------------------------------
# Public importable API
# ---------------------------------------------------------------------------


def start_viva(
    prop_id: str,
    *,
    kg_dir: Path | str | None = None,
    workspace: Path | str | None = None,
) -> dict[str, Any]:
    """Start a 5-turn Adversarial Viva Voce defense session grounded in KG metadata."""
    pid = str(prop_id or "").strip()
    if not pid:
        raise ValueError("prop_id is required.")

    kdir = resolve_kg_dir(workspace=workspace, explicit=kg_dir)
    props = load_jsonl(kdir / "propositions.jsonl")
    prop = next((p for p in props if str(p.get("id") or "") == pid), None)
    if prop is None:
        raise KeyError(f"Proposition '{pid}' not found in {kdir / 'propositions.jsonl'}.")

    chains = load_jsonl(kdir / "chains.jsonl")
    mistakes = load_jsonl(kdir / "mistakes.jsonl")

    premises: list[str] = []
    dependents: list[str] = []
    chain_whys: list[str] = []
    for raw_c in chains:
        nc = normalize_chain(raw_c)
        if not nc:
            continue
        if nc["conclusion"] == pid:
            for pr in nc["premises"]:
                if pr not in premises:
                    premises.append(pr)
            if nc["why"]:
                chain_whys.append(nc["why"])
        if pid in nc["premises"]:
            if nc["conclusion"] not in dependents:
                dependents.append(nc["conclusion"])

    boundary_conditions = [str(b).strip() for b in (prop.get("boundaryConditions") or []) if str(b).strip()]
    anti_patterns = [str(a).strip() for a in (prop.get("antiPatterns") or []) if str(a).strip()]
    for m in mistakes:
        if str(m.get("propId") or "").strip() == pid:
            wm = str(m.get("wrongMove") or "").strip()
            tc = str(m.get("triggerCue") or "").strip()
            if wm and wm not in anti_patterns:
                anti_patterns.append(wm)
            if tc and tc not in boundary_conditions:
                boundary_conditions.append(tc)

    prompts = _build_defense_prompts(prop, premises, dependents, boundary_conditions, anti_patterns)
    domain_text = " ".join(
        [
            str(prop.get("title") or ""),
            str(prop.get("notes") or ""),
            " ".join(premises),
            " ".join(chain_whys),
            " ".join(boundary_conditions),
        ]
    )
    boundary_text = " ".join(boundary_conditions + premises + chain_whys)
    anti_text = " ".join(anti_patterns)

    ts = now_iso()
    sid = f"viva-{uuid.uuid4().hex[:8]}"
    session: dict[str, Any] = {
        "id": sid,
        "propId": pid,
        "propTitle": str(prop.get("title") or pid),
        "subject": str(prop.get("subject") or "General"),
        "chapter": str(prop.get("chapter") or "General"),
        "initialStatus": str(prop.get("status") or "planned").lower(),
        "status": "in_progress",
        "currentTurn": 1,
        "totalTurns": 5,
        "grounding": {
            "premises": premises,
            "dependents": dependents,
            "boundaryConditions": boundary_conditions,
            "antiPatterns": anti_patterns,
            "domainTokens": sorted(set(_tokenize(domain_text)))[:48],
            "boundaryTokens": sorted(set(_tokenize(boundary_text)))[:36],
            "antiPatternTokens": sorted(set(_tokenize(anti_text)))[:36],
        },
        "prompts": prompts,
        "commitmentLedger": None,
        "turns": [],
        "scores": None,
        "outcome": None,
        "evidenceRecorded": False,
        "demotedToShaky": False,
        "createdAt": ts,
        "updatedAt": ts,
    }

    path = resolve_viva_path(workspace=workspace, kg_dir=kg_dir)
    with workspace_lock(path):
        sessions = load_jsonl(path)
        sessions.append(session)
        save_jsonl_atomic(path, sessions)

    return {
        "ok": True,
        "sessionId": sid,
        "propId": pid,
        "propTitle": session["propTitle"],
        "status": session["status"],
        "currentTurn": 1,
        "totalTurns": 5,
        "prompt": prompts[0],
        "groundingSummary": {
            "premisesCount": len(premises),
            "boundaryConditionsCount": len(boundary_conditions),
            "antiPatternsCount": len(anti_patterns),
        },
    }


def submit_viva_turn(
    session_id: str,
    response: str,
    *,
    confidence: float | None = None,
    kg_dir: Path | str | None = None,
    workspace: Path | str | None = None,
) -> dict[str, Any]:
    """Submit the learner's response for the current Viva turn, evaluate rubric, and finalize on Turn 5."""
    sid = str(session_id or "").strip()
    resp_clean = str(response or "").strip()
    if not sid:
        raise ValueError("session_id is required.")
    if not resp_clean:
        raise ValueError("response cannot be empty.")

    conf_norm = _clamp_confidence(confidence)
    kdir = resolve_kg_dir(workspace=workspace, explicit=kg_dir)
    ws = resolve_workspace(workspace) if workspace is not None else kdir.parent
    path = resolve_viva_path(workspace=workspace, kg_dir=kg_dir)

    with workspace_lock(path):
        sessions = load_jsonl(path)
        idx = next((i for i, s in enumerate(sessions) if str(s.get("id") or "") == sid), None)
        if idx is None:
            raise KeyError(f"Viva session '{sid}' not found.")
        sess = sessions[idx]
        if sess.get("status") != "in_progress":
            raise ValueError(f"Viva session '{sid}' is already {sess.get('status')}.")

        turn_num = int(sess.get("currentTurn") or (len(sess.get("turns") or []) + 1))
        if turn_num == 1:
            sess["commitmentLedger"] = _extract_commitment_ledger(resp_clean)

        rubric = _score_turn(
            turn_num,
            resp_clean,
            conf_norm,
            sess.get("grounding") or {},
            sess.get("commitmentLedger"),
        )
        ts = now_iso()
        turn_rec = {
            "turn": turn_num,
            "stage": sess["prompts"][turn_num - 1]["stage"] if turn_num <= len(sess["prompts"]) else f"turn_{turn_num}",
            "response": resp_clean,
            "confidence": conf_norm,
            "rubric": rubric,
            "ts": ts,
        }
        sess["turns"].append(turn_rec)

        finished = turn_num >= int(sess.get("totalTurns") or 5)
        next_prompt: dict[str, Any] | None = None
        if not finished:
            sess["currentTurn"] = turn_num + 1
            next_prompt = sess["prompts"][turn_num]
        else:
            # Aggregate 5-turn defense
            turns = sess["turns"]
            avg_clarity = round(sum(t["rubric"]["clarity"] for t in turns) / len(turns), 4)
            avg_boundary = round(sum(t["rubric"]["boundary_awareness"] for t in turns) / len(turns), 4)
            avg_transfer = round(sum(t["rubric"]["transfer"] for t in turns) / len(turns), 4)
            avg_cal = round(sum(t["rubric"]["calibration"] for t in turns) / len(turns), 4)
            avg_conf = round(sum(t["confidence"] for t in turns) / len(turns), 4)

            all_contradictions = [
                c
                for t in turns
                for c in (t["rubric"]["contradictionCheck"].get("contradictions") or [])
            ]
            any_bluff = any(bool(t["rubric"]["contradictionCheck"].get("bluffDetected")) for t in turns)
            total_penalty = min(0.45, sum(float(t["rubric"]["contradictionCheck"].get("penalty") or 0.0) for t in turns) / 2.0)

            raw_score = 0.25 * avg_clarity + 0.30 * avg_boundary + 0.25 * avg_transfer + 0.20 * avg_cal
            composite = round(max(0.0, min(1.0, raw_score - total_penalty)), 4)

            passed = (composite >= 0.75) and (avg_boundary >= 0.55) and (not any_bluff)
            critical_collapse = (avg_boundary < 0.40) or (composite < 0.50) or (len(all_contradictions) >= 2)

            if passed:
                outcome = "passed"
                ev_ok = _record_t4_evidence_safe(
                    ws,
                    sess["propId"],
                    {
                        "sessionId": sid,
                        "compositeScore": composite,
                        "boundaryAwareness": avg_boundary,
                    },
                )
                sess["evidenceRecorded"] = ev_ok
            elif critical_collapse:
                outcome = "boundary_collapse"
                demoted = _demote_solid_prop_to_shaky(
                    kdir,
                    sess["propId"],
                    f"boundary_awareness={avg_boundary:.2f}, composite={composite:.2f}",
                )
                sess["demotedToShaky"] = demoted
            else:
                outcome = "needs_revision"

            sess["status"] = "completed"
            sess["outcome"] = outcome
            sess["scores"] = {
                "clarity": avg_clarity,
                "boundary_awareness": avg_boundary,
                "transfer": avg_transfer,
                "calibration": avg_cal,
                "contradictionCount": len(all_contradictions),
                "bluffDetected": any_bluff,
                "contradictions": all_contradictions,
                "composite": composite,
                "passThreshold": 0.75,
                "passed": passed,
            }
            _record_telemetry_safe(
                kdir,
                domain=str(sess.get("subject") or "General"),
                prop_id=str(sess["propId"]),
                correct=passed,
                confidence=avg_conf,
            )

        sess["updatedAt"] = ts
        sessions[idx] = sess
        save_jsonl_atomic(path, sessions)

    out: dict[str, Any] = {
        "ok": True,
        "sessionId": sid,
        "propId": sess["propId"],
        "turnCompleted": turn_num,
        "status": sess["status"],
        "turnRubric": rubric,
        "nextPrompt": next_prompt,
    }
    if finished:
        out["outcome"] = sess["outcome"]
        out["scores"] = sess["scores"]
        out["evidenceRecorded"] = sess["evidenceRecorded"]
        out["demotedToShaky"] = sess["demotedToShaky"]
    return out


def get_viva_status(
    session_id: str,
    *,
    kg_dir: Path | str | None = None,
    workspace: Path | str | None = None,
) -> dict[str, Any]:
    sid = str(session_id or "").strip()
    for s in load_viva_sessions(kg_dir=kg_dir, workspace=workspace):
        if str(s.get("id") or "") == sid:
            cur = int(s.get("currentTurn") or 1)
            cur_prompt = (
                s["prompts"][cur - 1]
                if s.get("status") == "in_progress" and 1 <= cur <= len(s.get("prompts") or [])
                else None
            )
            return {
                "ok": True,
                "session": s,
                "currentPrompt": cur_prompt,
            }
    raise KeyError(f"Viva session '{sid}' not found.")


def list_viva_sessions(
    kg_dir: Path | str | None = None,
    *,
    workspace: Path | str | None = None,
    prop_id: str | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    rows = load_viva_sessions(kg_dir=kg_dir, workspace=workspace)
    pf = str(prop_id or "").strip()
    sf = str(status or "").strip().lower()

    items: list[dict[str, Any]] = []
    for s in rows:
        if pf and str(s.get("propId") or "") != pf:
            continue
        if sf and str(s.get("status") or "").lower() != sf:
            continue
        items.append(
            {
                "id": s.get("id"),
                "propId": s.get("propId"),
                "propTitle": s.get("propTitle"),
                "status": s.get("status"),
                "outcome": s.get("outcome"),
                "turnsCompleted": len(s.get("turns") or []),
                "totalTurns": s.get("totalTurns", 5),
                "composite": (s.get("scores") or {}).get("composite") if isinstance(s.get("scores"), dict) else None,
                "evidenceRecorded": bool(s.get("evidenceRecorded")),
                "demotedToShaky": bool(s.get("demotedToShaky")),
                "updatedAt": s.get("updatedAt"),
            }
        )

    return {"ok": True, "count": len(items), "sessions": items}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--workspace", type=Path, default=argparse.SUPPRESS, help="Workspace root override.")
    common.add_argument("--kg-dir", type=Path, default=argparse.SUPPRESS, help="knowledge-graph directory override.")
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="Emit JSON output.")

    parser = argparse.ArgumentParser(
        prog="viva_chamber.py",
        description="5-Turn Adversarial Viva Voce Defense Chamber CLI.",
        parents=[common],
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_start = sub.add_parser("start", parents=[common], help="Start a 5-turn Viva defense for a proposition.")
    p_start.add_argument("--prop-id", required=True, help="Proposition ID to defend.")

    p_turn = sub.add_parser("turn", parents=[common], help="Submit response for the current Viva turn.")
    p_turn.add_argument("--session-id", required=True, help="Viva session ID.")
    p_turn.add_argument("--response", required=True, help="Learner defense response.")
    p_turn.add_argument("--confidence", type=float, default=None, help="Learner confidence 0..1 (or 0..100).")

    p_status = sub.add_parser("status", parents=[common], help="Inspect a Viva session.")
    p_status.add_argument("--session-id", required=True, help="Viva session ID.")

    p_list = sub.add_parser("list", parents=[common], help="List Viva sessions.")
    p_list.add_argument("--prop-id", default=None, help="Filter by proposition ID.")
    p_list.add_argument("--status", default=None, choices=["in_progress", "completed"], help="Filter by status.")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    ws = getattr(args, "workspace", None)
    kg_dir = getattr(args, "kg_dir", None)
    as_json = bool(getattr(args, "json", False))

    try:
        if args.command == "start":
            res = start_viva(args.prop_id, kg_dir=kg_dir, workspace=ws)
        elif args.command == "turn":
            res = submit_viva_turn(
                args.session_id,
                args.response,
                confidence=args.confidence,
                kg_dir=kg_dir,
                workspace=ws,
            )
        elif args.command == "status":
            res = get_viva_status(args.session_id, kg_dir=kg_dir, workspace=ws)
        elif args.command == "list":
            res = list_viva_sessions(kg_dir=kg_dir, workspace=ws, prop_id=args.prop_id, status=args.status)
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
        elif args.command == "start":
            print(f"Viva {res['sessionId']} started for {res['propId']} ({res['propTitle']}):")
            print(f"  {res['prompt']['prompt']}")
        elif args.command == "turn":
            rb = res["turnRubric"]
            print(
                f"Turn {res['turnCompleted']} scored: clarity={rb['clarity']:.2f} "
                f"boundary={rb['boundary_awareness']:.2f} transfer={rb['transfer']:.2f} "
                f"cal={rb['calibration']:.2f}"
            )
            if rb["contradictionCheck"]["flagged"]:
                for c in rb["contradictionCheck"]["contradictions"]:
                    print(f"  [FLAG] {c}")
            if res.get("nextPrompt"):
                print(f"  Next: {res['nextPrompt']['prompt']}")
            else:
                sc = res["scores"]
                print(
                    f"Viva complete: outcome={res['outcome']} composite={sc['composite']:.2f} "
                    f"(T4_recorded={res['evidenceRecorded']}, demoted={res['demotedToShaky']})"
                )
        elif args.command == "status":
            s = res["session"]
            print(f"Viva {s['id']} [{s['status']}] {s['propId']} · turns {len(s['turns'])}/{s['totalTurns']}")
        elif args.command == "list":
            print(f"Viva sessions ({res['count']}):")
            for s in res["sessions"]:
                print(f"  - {s['id']} [{s['status']}] {s['propId']} outcome={s['outcome']} score={s['composite']}")
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
