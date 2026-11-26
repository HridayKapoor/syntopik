"""Syntopik v3.0 — Bayesian Noisy-OR / Conjunctive DAG Root-Cause Fault Localizer.

Pure Python 3.12+ standard library only (zero external imports).
Exploits Syntopik's conjunctive `chains.jsonl` (`premises[] -> conclusion`)
hypergraph using a non-destructive Bayesian conjunctive factor model:

    P(pass C | P_1..P_k) = (1 - s_C) * prod_i P(P_i intact)^{w_i}

- NEVER mutates `propositions.jsonl` or downgrades `solid` nodes directly.
- Computes posterior `suspectRootScore = P(ancestor faulty | fail C)` across
  upstream prerequisites up to `max_depth`.
- Ranks candidate diagnostic probes by expected Shannon entropy reduction
  `deltaH` (bits), automatically performing information-theoretic binary search
  over causal chains.
- Detects cross-chapter blast-radius bottlenecks explaining downstream misses.
"""

from __future__ import annotations

import math
from collections import Counter, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from syntopik.fsrs_core import card_retrievability, load_fsrs_parameters
from syntopik.schemas import normalize_chain
from syntopik.storage import load_jsonl

__all__ = [
    "localize_fault",
    "select_diagnostic_probe",
    "find_blast_radius",
]

DEFAULT_LEAF_SLIP = 0.10
BROKEN_NODE_RESIDUAL = 0.08  # P(pass factor | ancestor faulty) = eps^w
DEPTH_DECAY = 0.85
PROBE_SLIP = 0.10
PROBE_GUESS = 0.12


def _resolve_kg_dir(kg_dir: Path | str) -> Path:
    p = Path(kg_dir).expanduser().resolve()
    if (p / "knowledge-graph").is_dir() and not (p / "propositions.jsonl").is_file() and p.name != "knowledge-graph":
        return (p / "knowledge-graph").resolve()
    return p


def _clamp(val: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(val)))


def _entropy_bits(probs: list[float]) -> float:
    h = 0.0
    for p in probs:
        if p > 1e-12:
            h -= p * math.log2(p)
    return h


def _build_graph_maps(
    chains: list[dict[str, Any]],
) -> tuple[dict[str, list[tuple[str, float]]], dict[str, list[tuple[str, float]]]]:
    """Build incoming (`conc -> [(prem, w)]`) and outgoing (`prem -> [(conc, w)]`) maps."""
    best_edge: dict[tuple[str, str], float] = {}
    for raw in chains:
        nc = normalize_chain(raw)
        if not nc:
            continue
        w = float(nc["encompassWeight"])
        if w <= 0.0:
            continue
        conc = nc["conclusion"]
        for prem in nc["premises"]:
            key = (conc, prem)
            if w > best_edge.get(key, 0.0):
                best_edge[key] = w

    incoming: dict[str, list[tuple[str, float]]] = {}
    outgoing: dict[str, list[tuple[str, float]]] = {}
    for (conc, prem), w in best_edge.items():
        incoming.setdefault(conc, []).append((prem, w))
        outgoing.setdefault(prem, []).append((conc, w))

    for adj in (incoming, outgoing):
        for k in adj:
            adj[k].sort(key=lambda pair: (-pair[1], pair[0]))
    return incoming, outgoing


def _prior_intact_prob(
    prop: dict[str, Any] | None,
    *,
    now_dt: datetime,
    fsrs_params: tuple[float, ...],
) -> float:
    """Non-destructively estimate P(node intact) in [0.08, 0.96] from status, BKT & FSRS R."""
    if not isinstance(prop, dict):
        return 0.50

    status = str(prop.get("status") or "planned").strip().lower()
    raw_bkt = prop.get("bktMastery")
    bkt = _clamp(float(raw_bkt), 0.05, 0.98) if isinstance(raw_bkt, (int, float)) and not isinstance(raw_bkt, bool) else None

    retr: float | None = None
    if isinstance(prop.get("fsrs"), dict) and isinstance(prop["fsrs"].get("stability"), (int, float)):
        try:
            retr = _clamp(float(card_retrievability(prop["fsrs"], now_dt, parameters=fsrs_params)), 0.05, 0.99)
        except Exception:
            retr = None

    if status == "misconception":
        base = min(0.20, bkt if bkt is not None else 0.15)
        return _clamp(base, 0.08, 0.25)
    if status == "shaky":
        if bkt is not None and retr is not None:
            return _clamp(0.6 * bkt + 0.4 * retr, 0.20, 0.68)
        return _clamp(bkt if bkt is not None else (retr if retr is not None else 0.42), 0.20, 0.65)
    if status == "solid":
        if bkt is not None and retr is not None:
            return _clamp(0.55 * bkt + 0.45 * retr, 0.45, 0.96)
        if retr is not None:
            return _clamp(0.55 * 0.90 + 0.45 * retr, 0.45, 0.96)
        return _clamp(bkt if bkt is not None else 0.88, 0.60, 0.96)
    # planned or unknown
    return _clamp(bkt if bkt is not None else 0.35, 0.15, 0.55)


def _collect_ancestors(
    target_id: str,
    incoming: dict[str, list[tuple[str, float]]],
    *,
    max_depth: int = 4,
) -> dict[str, dict[str, Any]]:
    """Cycle-safe DFS collecting upstream ancestors with effective causal weight and min depth."""
    cap = max(1, int(max_depth))
    best: dict[str, dict[str, Any]] = {}

    def _dfs(cur: str, depth: int, path_weight: float, path: list[str], seen: set[str]) -> None:
        if depth > cap:
            return
        for prem, edge_w in incoming.get(cur, ()):
            if prem in seen or edge_w <= 0.0:
                continue
            # Scale edge weight into conjunctive exponent w_i in [0.20, 1.0] so multi-premise
            # edges remain strongly causal while decaying smoothly with graph depth.
            step_w = _clamp(edge_w * 1.4, 0.35, 1.0)
            eff_w = _clamp(path_weight * step_w * (DEPTH_DECAY ** (depth - 1)), 0.15, 1.0)
            prev = best.get(prem)
            if prev is None or eff_w > prev["weight"] + 1e-9 or (abs(eff_w - prev["weight"]) <= 1e-9 and depth < prev["depth"]):
                best[prem] = {
                    "weight": round(eff_w, 4),
                    "depth": depth,
                    "path": [*path, prem],
                }
            if depth < cap:
                seen.add(prem)
                _dfs(prem, depth + 1, path_weight * step_w, [*path, prem], seen)
                seen.remove(prem)

    _dfs(target_id, 1, 1.0, [target_id], {target_id})
    return best


def _upstream_closure(node_id: str, incoming: dict[str, list[tuple[str, float]]], scope: set[str]) -> set[str]:
    """Return ancestors of `node_id` restricted to `scope` (excluding `node_id`)."""
    visited: set[str] = set()
    q: deque[str] = deque([node_id])
    while q:
        cur = q.popleft()
        for prem, w in incoming.get(cur, ()):
            if w <= 0.0 or prem not in scope or prem in visited or prem == node_id:
                continue
            visited.add(prem)
            q.append(prem)
    return visited


# ---------------------------------------------------------------------------
# Public API 1: Bayesian Conjunctive / Noisy-OR Fault Localization
# ---------------------------------------------------------------------------


def localize_fault(
    kg_dir: Path | str,
    failed_node_id: str,
    *,
    max_depth: int = 4,
) -> dict[str, Any]:
    """Compute non-destructive posterior `suspectRootScore` over upstream ancestors of `failed_node_id`.

    Uses conjunctive factorization:
        P(pass C | P_1..P_k) = (1 - s_C) * prod_i P(P_i)^{w_i}
    where P(P_i) = 1 when intact and P(P_i) = eps when faulty.
    """
    kdir = _resolve_kg_dir(kg_dir)
    target = str(failed_node_id or "").strip()
    props = load_jsonl(kdir / "propositions.jsonl")
    chains = load_jsonl(kdir / "chains.jsonl")
    props_by_id = {str(p.get("id") or ""): p for p in props if isinstance(p, dict) and p.get("id")}

    if not target or target not in props_by_id:
        return {
            "ok": False,
            "failedNodeId": target,
            "error": f"Proposition not found: {target!r}",
            "suspects": [],
            "leafSlipPosterior": 1.0,
            "priorPassProb": 0.0,
        }

    fsrs_params = load_fsrs_parameters(kdir)
    now_dt = datetime.now(timezone.utc)
    incoming, _outgoing = _build_graph_maps(chains)
    anc_meta = _collect_ancestors(target, incoming, max_depth=max_depth)

    leaf_prop = props_by_id[target]
    leaf_status = str(leaf_prop.get("status") or "planned").lower()
    s_c = DEFAULT_LEAF_SLIP if leaf_status == "solid" else (0.16 if leaf_status == "shaky" else 0.22)

    if not anc_meta:
        return {
            "ok": True,
            "failedNodeId": target,
            "failedNodeTitle": str(leaf_prop.get("title") or target),
            "priorPassProb": round(1.0 - s_c, 4),
            "leafSlipPosterior": 1.0,
            "suspects": [],
            "nonDestructive": True,
            "summary": "No upstream prerequisites in chains.jsonl; failure localizes to the leaf itself.",
        }

    # Precompute marginal pass factor per ancestor: m_v = pi_v + (1 - pi_v) * eps^{w_v}
    anc_ids = sorted(anc_meta.keys())
    pi_map: dict[str, float] = {}
    factor_map: dict[str, float] = {}
    eps_w_map: dict[str, float] = {}

    for uid in anc_ids:
        pi_u = _prior_intact_prob(props_by_id.get(uid), now_dt=now_dt, fsrs_params=fsrs_params)
        w_u = float(anc_meta[uid]["weight"])
        eps_w = BROKEN_NODE_RESIDUAL ** w_u
        pi_map[uid] = pi_u
        eps_w_map[uid] = eps_w
        factor_map[uid] = pi_u + (1.0 - pi_u) * eps_w

    total_pass = (1.0 - s_c)
    for uid in anc_ids:
        total_pass *= factor_map[uid]
    total_fail = max(1e-9, 1.0 - total_pass)

    # Leaf slip posterior: P(all ancestors intact AND leaf slipped | fail C)
    prod_all_intact = 1.0
    for uid in anc_ids:
        prod_all_intact *= pi_map[uid]
    leaf_slip_post = _clamp((prod_all_intact * s_c) / total_fail, 0.0, 1.0)

    suspects: list[dict[str, Any]] = []
    for uid in anc_ids:
        pi_u = pi_map[uid]
        w_u = float(anc_meta[uid]["weight"])
        eps_w = eps_w_map[uid]

        # Product of all other factors B_{-u} = (1 - s_C) * prod_{v != u} m_v
        b_minus_u = total_pass / max(1e-12, factor_map[uid])
        p_fail_given_faulty = 1.0 - b_minus_u * eps_w
        p_fault_prior = 1.0 - pi_u

        # Exact Bayes posterior: P(u faulty | fail C)
        post = _clamp((p_fault_prior * p_fail_given_faulty) / total_fail, 0.0, 1.0)
        lift = round(post / max(1e-6, p_fault_prior), 3)

        p_obj = props_by_id.get(uid) or {}
        suspects.append(
            {
                "propId": uid,
                "title": str(p_obj.get("title") or uid),
                "subject": str(p_obj.get("subject") or "General"),
                "chapter": str(p_obj.get("chapter") or ""),
                "status": str(p_obj.get("status") or "planned"),
                "depth": int(anc_meta[uid]["depth"]),
                "causalWeight": w_u,
                "priorIntact": round(pi_u, 4),
                "priorFault": round(p_fault_prior, 4),
                "suspectRootScore": round(post, 4),
                "posteriorLift": lift,
                "path": anc_meta[uid]["path"],
            }
        )

    suspects.sort(key=lambda s: (-s["suspectRootScore"], s["depth"], s["propId"]))
    top = suspects[0]
    return {
        "ok": True,
        "failedNodeId": target,
        "failedNodeTitle": str(leaf_prop.get("title") or target),
        "priorPassProb": round(total_pass, 4),
        "leafSlipPosterior": round(leaf_slip_post, 4),
        "suspects": suspects,
        "topSuspect": top,
        "nonDestructive": True,
        "summary": (
            f"Top suspect root for {target}: {top['propId']} "
            f"(suspectRootScore={top['suspectRootScore']:.2f}, depth={top['depth']}, status={top['status']})."
        ),
    }


# ---------------------------------------------------------------------------
# Public API 2: Max-Entropy Diagnostic Probe Selector (Delta H in bits)
# ---------------------------------------------------------------------------


def select_diagnostic_probe(
    kg_dir: Path | str,
    failed_node_id: str,
    *,
    max_depth: int = 4,
) -> dict[str, Any]:
    """Rank upstream prerequisite probes by expected Shannon entropy reduction `deltaH` (bits)."""
    kdir = _resolve_kg_dir(kg_dir)
    loc = localize_fault(kdir, failed_node_id, max_depth=max_depth)
    if not loc.get("ok"):
        return {
            "ok": False,
            "failedNodeId": str(failed_node_id or ""),
            "error": loc.get("error", "Localization failed"),
            "currentEntropyBits": 0.0,
            "bestProbe": None,
            "candidates": [],
        }

    suspects = loc.get("suspects") or []
    if not suspects:
        return {
            "ok": True,
            "failedNodeId": loc["failedNodeId"],
            "currentEntropyBits": 0.0,
            "bestProbe": None,
            "candidates": [],
            "summary": "No upstream ancestors to probe; address the failed node directly.",
        }

    props = load_jsonl(kdir / "propositions.jsonl")
    chains = load_jsonl(kdir / "chains.jsonl")
    props_by_id = {str(p.get("id") or ""): p for p in props if isinstance(p, dict) and p.get("id")}
    incoming, _outgoing = _build_graph_maps(chains)

    # Hypothesis space H = {each upstream ancestor u is the root cause} U {LEAF_SLIP}
    scope = {s["propId"] for s in suspects}
    raw_weights: dict[str, float] = {s["propId"]: max(1e-6, float(s["suspectRootScore"])) for s in suspects}
    raw_weights["__leaf_slip__"] = max(1e-4, float(loc.get("leafSlipPosterior") or 0.05))
    z = sum(raw_weights.values())
    h_dist = {k: v / z for k, v in raw_weights.items()}
    h_now = _entropy_bits(list(h_dist.values()))

    anc_closures = {u: _upstream_closure(u, incoming, scope) for u in scope}
    suspect_by_id = {s["propId"]: s for s in suspects}

    candidates: list[dict[str, Any]] = []
    for uid in sorted(scope):
        # Probing node q tests q AND transitively depends on q's upstream ancestors!
        # If root fault h == q or h in Anc(q), probe q likely FAILS (passes only via guess).
        # Otherwise (h is unrelated branch, downstream of q, or leaf slip), probe q likely PASSES (fails only via slip).
        covered_by_q = {uid} | anc_closures[uid]

        p_pass_joint: dict[str, float] = {}
        p_fail_joint: dict[str, float] = {}
        for h_key, p_h in h_dist.items():
            if h_key in covered_by_q:
                p_pass_given_h = PROBE_GUESS
            else:
                p_pass_given_h = 1.0 - PROBE_SLIP
            p_pass_joint[h_key] = p_h * p_pass_given_h
            p_fail_joint[h_key] = p_h * (1.0 - p_pass_given_h)

        p_pass = sum(p_pass_joint.values())
        p_fail = sum(p_fail_joint.values())

        h_given_pass = _entropy_bits([v / p_pass for v in p_pass_joint.values()]) if p_pass > 1e-12 else 0.0
        h_given_fail = _entropy_bits([v / p_fail for v in p_fail_joint.values()]) if p_fail > 1e-12 else 0.0
        expected_h = p_pass * h_given_pass + p_fail * h_given_fail
        delta_h = max(0.0, h_now - expected_h)

        s_info = suspect_by_id[uid]
        p_obj = props_by_id.get(uid) or {}
        cues = p_obj.get("cues") or []
        prompt = ""
        if isinstance(cues, list) and cues and isinstance(cues[0], dict):
            prompt = str(cues[0].get("q") or "").strip()
        if not prompt:
            prompt = f"Verify prerequisite cleanly: {s_info['title']}"

        candidates.append(
            {
                "propId": uid,
                "title": s_info["title"],
                "subject": s_info["subject"],
                "chapter": s_info["chapter"],
                "status": s_info["status"],
                "depth": s_info["depth"],
                "priorIntact": s_info["priorIntact"],
                "suspectRootScore": s_info["suspectRootScore"],
                "coveredUpstreamCount": len(covered_by_q),
                "passProb": round(p_pass, 4),
                "expectedEntropyReductionBits": round(delta_h, 4),
                "deltaHBits": round(delta_h, 4),
                "prompt": prompt,
                "rationale": (
                    f"Probing '{s_info['title']}' cuts causal uncertainty by {delta_h:.2f} bits "
                    f"(suspect={s_info['suspectRootScore']:.2f}, covers {len(covered_by_q)} node(s))."
                ),
            }
        )

    candidates.sort(
        key=lambda c: (-c["expectedEntropyReductionBits"], -c["suspectRootScore"], c["depth"], c["propId"])
    )
    best = candidates[0] if candidates else None

    return {
        "ok": True,
        "failedNodeId": loc["failedNodeId"],
        "failedNodeTitle": loc.get("failedNodeTitle", loc["failedNodeId"]),
        "currentEntropyBits": round(h_now, 4),
        "bestProbe": best,
        "candidates": candidates,
        "totalAncestors": len(candidates),
        "summary": best["rationale"] if best else "No candidate probe available.",
    }


# ---------------------------------------------------------------------------
# Public API 3: Cross-Chapter Blast-Radius Detector
# ---------------------------------------------------------------------------


def find_blast_radius(kg_dir: Path | str, *, limit: int = 5) -> list[dict[str, Any]]:
    """Find shaky/misconception ancestors explaining the most downstream weak nodes & misses across chapters."""
    kdir = _resolve_kg_dir(kg_dir)
    props = load_jsonl(kdir / "propositions.jsonl")
    chains = load_jsonl(kdir / "chains.jsonl")
    mistakes = load_jsonl(kdir / "mistakes.jsonl")

    props_by_id = {str(p.get("id") or ""): p for p in props if isinstance(p, dict) and p.get("id")}
    if not props_by_id or not chains:
        return []

    _incoming, outgoing = _build_graph_maps(chains)
    miss_counts: Counter[str] = Counter()
    for m in mistakes:
        pid = str(m.get("propId") or "").strip()
        if pid:
            miss_counts[pid] += 1

    # Compute transitive downstream descendants per node
    results: list[dict[str, Any]] = []
    for uid, p_obj in props_by_id.items():
        status = str(p_obj.get("status") or "planned").strip().lower()
        if status not in ("shaky", "misconception"):
            continue
        if uid not in outgoing:
            continue

        desc: set[str] = set()
        q: deque[str] = deque([nxt for nxt, w in outgoing.get(uid, ()) if w > 0.0])
        while q:
            cur = q.popleft()
            if cur in desc or cur == uid:
                continue
            desc.add(cur)
            for nxt, w in outgoing.get(cur, ()):
                if w > 0.0 and nxt not in desc and nxt != uid:
                    q.append(nxt)

        if not desc:
            continue

        weak_desc = sorted(
            d
            for d in desc
            if str((props_by_id.get(d) or {}).get("status") or "").lower() in ("shaky", "misconception")
        )
        downstream_misses = sum(miss_counts.get(d, 0) for d in desc)
        own_misses = miss_counts.get(uid, 0)

        chapters = sorted(
            {
                str((props_by_id.get(nid) or {}).get("chapter") or "").strip()
                for nid in ({uid} | desc)
                if str((props_by_id.get(nid) or {}).get("chapter") or "").strip()
            }
        )
        sev_mult = 1.4 if status == "misconception" else 1.0
        cross_chap_bonus = 1.0 + 0.25 * max(0, len(chapters) - 1)
        blast_score = round(
            sev_mult * cross_chap_bonus * (len(desc) + 2.0 * len(weak_desc) + 1.5 * downstream_misses + 0.5 * own_misses),
            3,
        )

        results.append(
            {
                "propId": uid,
                "title": str(p_obj.get("title") or uid),
                "subject": str(p_obj.get("subject") or "General"),
                "chapter": str(p_obj.get("chapter") or ""),
                "status": status,
                "downstreamCount": len(desc),
                "weakDownstreamCount": len(weak_desc),
                "downstreamMissCount": int(downstream_misses),
                "affectedChapters": chapters,
                "affectedNodeIds": sorted(desc),
                "weakDownstreamIds": weak_desc,
                "blastScore": blast_score,
                "summary": (
                    f"{status} ancestor '{uid}' impacts {len(desc)} downstream node(s) "
                    f"({len(weak_desc)} weak, {downstream_misses} miss(es)) across {max(1, len(chapters))} chapter(s)."
                ),
            }
        )

    results.sort(key=lambda r: (-r["blastScore"], -r["weakDownstreamCount"], -r["downstreamMissCount"], r["propId"]))
    cap = max(1, int(limit or 5))
    return results[:cap]
