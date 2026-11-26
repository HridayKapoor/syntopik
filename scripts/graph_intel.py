#!/usr/bin/env python3
"""Syntopik — Universal Knowledge Hypergraph Intelligence CLI.

Analyzes `knowledge-graph/propositions.jsonl` and `knowledge-graph/chains.jsonl`
(supporting BOTH legacy binary `{from, to}` chains AND multi-premise hyperedges
`{premises[], conclusion}`) across STEM, CS, Algorithms, Systems, ML/AI, and
Formal Proofs to surface:
  1. Bottleneck Roots (`--bottlenecks`): shaky / misconception / low-BKT (<0.65)
     nodes ranked by downstream descendant reach, reverse PageRank, and
     cross-chapter reach.
  2. Memorized Islands (`--islands`): zero-in-degree nodes whose `kind` is NOT
     in `{"axiom", "unconditional_truth", "definition"}` (flags rote formulas).
  3. Optimal Study Path (`--path-to <target_id>`): cycle-safe (SCC condensation
     fallback) topological prerequisite sequence highlighting solid vs shaky/missing.
  4. Cross-Chapter Combos (`--combos`): pairs of `solid` nodes from 2 distinct
     chapters/topics sharing mathematical, algorithmic, or structural themes
     for integrative multi-concept synthesis drills.

Importable helpers for `scripts/daily_plan.py` and other callers:
  - `load_knowledge_graph(kg_dir)`
  - `analyze_bottlenecks(G, ...)`
  - `analyze_islands(G)`
  - `compute_path_to(G, target_id)`
  - `find_combos(G, ...)`
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any

import networkx as nx

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_KG_DIR = ROOT / "knowledge-graph"

FOUNDATIONAL_KINDS: frozenset[str] = frozenset(
    {"axiom", "unconditional_truth", "definition"}
)

STATUS_SEVERITY_WEIGHT: dict[str, float] = {
    "misconception": 1.35,
    "shaky": 1.00,
    "missing": 1.10,
    "solid": 0.85,
    "planned": 0.85,
}

# Universal STEM + CS/ML/Systems/Proofs structural archetypes for cross-chapter synthesis discovery.
STRUCTURAL_THEMES: dict[str, tuple[str, ...]] = {
    "oscillation_waves": (
        "shm", "harmonic", "oscillat", "pendulum", "omega", "frequency",
        "restoring", "spring", "resonance", "damped", "phase", "wave",
        "superposition", "standing", "beat", "doppler", "lc",
    ),
    "energy_potential": (
        "energy", "potential", "work", "conservative", "barrier", "gibbs",
        "enthalpy", "activation", "capacit", "stored", "virial", "escape",
        "binding", "ionization",
    ),
    "calculus_rates": (
        "derivative", "differenti", "integral", "integrat", "rate", "kinetics",
        "gradient", "maxima", "minima", "extrema", "slope", "area", "decay",
        "exponential", "first-order", "rc", "rl", "flux",
    ),
    "symmetry_geometry": (
        "symmetry", "gauss", "ampere", "mirror", "lens", "optical", "axis",
        "chiral", "enantiomer", "stereochem", "plane", "conic", "parabola",
        "ellipse", "hyperbola", "tangent", "normal", "projection", "moment",
    ),
    "equilibrium_balance": (
        "equilibrium", "balance", "steady", "torque", "chatelier", "buffer",
        "solubility", "nernst", "reversible", "isothermal", "adiabatic",
        "hydrostatic", "buoyancy", "stability",
    ),
    "conservation_invariance": (
        "conserv", "momentum", "angular", "impulse", "collision", "kirchhoff",
        "charge", "invariant", "continuity", "bernoulli", "gauss",
    ),
    "fields_potentials": (
        "field", "electric", "magnetic", "gravitation", "dipole", "coulomb",
        "lorentz", "faraday", "induction", "interference", "diffraction",
        "polariz", "equipotential",
    ),
    "vectors_complex_linear": (
        "vector", "phasor", "complex", "argand", "matrix", "determinant",
        "rotation", "impedance", "cross", "dot", "skew", "orthogonal",
    ),
    "thermodynamics_stats": (
        "entropy", "boltzmann", "maxwell", "carnot", "microstate", "ideal",
        "pressure", "temperature", "heat", "probability", "distribution",
        "binomial", "permutation", "combination",
    ),
    "algorithms_complexity": (
        "algorithm", "amortiz", "asymptotic", "complexity", "dynamic programming",
        "memoiz", "greedy", "dijkstra", "bellman", "floyd", "bfs", "dfs",
        "shortest path", "spanning tree", "kruskal", "prim", "binary search",
        "two-pointer", "sliding window", "divide and conquer", "recurrence",
        "master theorem", "np-complete", "np-hard", "reduction", "heap", "trie",
        "hash", "topolog", "bipartite", "max-flow", "min-cut",
    ),
    "systems_concurrency": (
        "concurren", "parallel", "mutex", "semaphore", "deadlock", "livelock",
        "race condition", "atomic", "lineariz", "serializ", "consensus", "raft",
        "paxos", "quorum", "replication", "wal", "write-ahead", "mvcc", "cache",
        "coherence", "page table", "tlb", "virtual memory", "scheduler", "syscall",
        "kernel", "socket", "epoll", "latency", "throughput", "backpressure", "idempoten",
    ),
    "ml_optimization": (
        "gradient descent", "sgd", "adam", "backprop", "jacobian", "hessian",
        "convex", "regulariz", "cross-entropy", "kl divergence", "likelihood",
        "posterior", "bayesian", "attention", "transformer", "softmax", "embedding",
        "overfit", "bias-variance", "generalization", "kernel trick", "svm",
        "eigenvalue", "svd", "pca", "markov", "policy gradient", "loss",
    ),
    "proofs_discrete": (
        "induction", "contradiction", "contrapositive", "bijection", "injection",
        "surjection", "pigeonhole", "monovariant", "modular", "congruence",
        "fermat", "euler", "group", "ring", "isomorph", "equivalence",
        "partial order", "lattice", "countable", "diagonaliz", "halting",
        "automata", "decidab", "generating function", "combinator",
    ),
    "software_architecture": (
        "ownership", "borrow", "lifetime", "raii", "immutab", "closure",
        "monad", "functor", "type system", "polymorph", "trait", "interface",
        "dependency injection", "abstraction", "encapsulat", "state machine",
        "pipeline", "stream", "ast", "parser", "compiler", "bytecode",
        "garbage collect", "zero-copy", "memory safety",
    ),
}

COMBO_STOP_WORDS: frozenset[str] = frozenset(
    (
        "a an the and or of to in on at by for from with as is are was were be been "
        "being it its this that these those which who whose their them they we our "
        "you your can could may might must will would should has have had does do did "
        "such than then when where each every any all also into through over under "
        "between about after before during without within against across along "
        "law laws rule rules formula formulas equation equations theorem concept "
        "principle property properties relation relationship definition axiom basic "
        "general special case form value values system body particle object state "
        "note notes trap traps always never check using given find show prove"
    ).split()
)


def resolve_kg_paths(kg_dir_arg: Path | str | None = None) -> tuple[Path, Path, Path]:
    if kg_dir_arg is not None and str(kg_dir_arg).strip():
        kg_dir = Path(kg_dir_arg).expanduser().resolve()
    else:
        env_kg = (
            os.environ.get("SYNT_KG_DIR", "").strip()
            or os.environ.get("MLH_KG_DIR", "").strip()
        )
        env_ws = (
            os.environ.get("SYNT_WORKSPACE", "").strip()
            or os.environ.get("MLH_WORKSPACE", "").strip()
        )
        if env_kg:
            kg_dir = Path(env_kg).expanduser().resolve()
        elif env_ws:
            kg_dir = (Path(env_ws).expanduser().resolve() / "knowledge-graph").resolve()
        else:
            kg_dir = DEFAULT_KG_DIR.resolve()
    return kg_dir, kg_dir / "propositions.jsonl", kg_dir / "chains.jsonl"


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        if isinstance(obj, dict):
            rows.append(obj)
    return rows


def normalize_chain(raw: dict[str, Any]) -> dict[str, Any] | None:
    """Normalize legacy binary `{from, to}` and multi-premise `{premises[], conclusion}` chains."""
    if not isinstance(raw, dict):
        return None
    conclusion = str(raw.get("conclusion") or raw.get("to") or "").strip()
    if not conclusion:
        return None
    raw_premises = raw.get("premises")
    premises: list[str] = []
    if isinstance(raw_premises, list):
        for p in raw_premises:
            ps = str(p or "").strip()
            if ps and ps != conclusion and ps not in premises:
                premises.append(ps)
    else:
        src = str(raw.get("from") or "").strip()
        if src and src != conclusion:
            premises = [src]
    if not premises:
        return None
    relation = str(raw.get("relation") or "requires").strip() or "requires"
    why = str(raw.get("why") or raw.get("note") or "").strip()
    chain_key = f"{'+'.join(sorted(premises))}>{conclusion}"
    return {
        "premises": premises,
        "conclusion": conclusion,
        "relation": relation,
        "why": why,
        "chainKey": chain_key,
        "isJoint": len(premises) >= 2,
        "arity": len(premises),
    }


def build_graph(
    props: list[dict[str, Any]],
    chains: list[dict[str, Any]],
) -> tuple[nx.DiGraph, list[dict[str, Any]]]:
    """Build a NetworkX DiGraph expanding both binary edges and multi-premise hyperedges."""
    G = nx.DiGraph()
    for p in props:
        pid = str(p.get("id") or "").strip()
        if not pid:
            continue
        bkt = p.get("bktMastery")
        bkt_val = float(bkt) if isinstance(bkt, (int, float)) else None
        raw_tags = p.get("tags") or []
        tags = [str(t).strip().lower() for t in raw_tags if isinstance(t, str) and t.strip()]
        raw_aliases = p.get("aliases") or []
        aliases = [str(a).strip() for a in raw_aliases if isinstance(a, str) and a.strip()]
        G.add_node(
            pid,
            id=pid,
            title=str(p.get("title") or pid),
            subject=str(p.get("subject") or "General").strip() or "General",
            chapter=str(p.get("chapter") or "General").strip() or "General",
            kind=str(p.get("kind") or "derivation").strip() or "derivation",
            status=str(p.get("status") or "planned").strip().lower() or "planned",
            bktMastery=bkt_val,
            tags=tags,
            aliases=aliases,
            notes=str(p.get("notes") or "").strip(),
            nextReview=p.get("nextReview"),
            missingProp=False,
        )

    normalized_chains: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    for raw in chains:
        norm = normalize_chain(raw)
        if not norm or norm["chainKey"] in seen_keys:
            continue
        seen_keys.add(norm["chainKey"])
        normalized_chains.append(norm)

        conc = norm["conclusion"]
        for node_id in (*norm["premises"], conc):
            if node_id not in G:
                G.add_node(
                    node_id,
                    id=node_id,
                    title=node_id,
                    subject="Unknown",
                    chapter="Unknown",
                    kind="unknown",
                    status="missing",
                    bktMastery=None,
                    tags=[],
                    aliases=[],
                    notes="Referenced in chains.jsonl but not yet in propositions.jsonl",
                    nextReview=None,
                    missingProp=True,
                )

        he_meta = {
            "chainKey": norm["chainKey"],
            "premises": list(norm["premises"]),
            "conclusion": conc,
            "relation": norm["relation"],
            "why": norm["why"],
            "isJoint": norm["isJoint"],
            "arity": norm["arity"],
        }
        for prem in norm["premises"]:
            if G.has_edge(prem, conc):
                G[prem][conc]["hyperedges"].append(he_meta)
                G[prem][conc]["relations"].add(norm["relation"])
                G[prem][conc]["isJoint"] = G[prem][conc]["isJoint"] or norm["isJoint"]
                G[prem][conc]["maxArity"] = max(G[prem][conc]["maxArity"], norm["arity"])
            else:
                G.add_edge(
                    prem,
                    conc,
                    hyperedges=[he_meta],
                    relations={norm["relation"]},
                    isJoint=norm["isJoint"],
                    maxArity=norm["arity"],
                )

    G.graph["hyperedges"] = normalized_chains
    G.graph["hyperedgeCount"] = sum(1 for c in normalized_chains if c["isJoint"])
    G.graph["uniqueChains"] = len(normalized_chains)
    return G, normalized_chains


def load_knowledge_graph(
    kg_dir: Path | str | None = None,
) -> tuple[nx.DiGraph, list[dict[str, Any]], list[dict[str, Any]]]:
    """Load `propositions.jsonl` + `chains.jsonl` into a NetworkX DiGraph."""
    _kg_dir, props_file, chains_file = resolve_kg_paths(kg_dir)
    props = load_jsonl(props_file)
    chains = load_jsonl(chains_file)
    G, norm_chains = build_graph(props, chains)
    return G, props, norm_chains


def _ensure_graph(graph_or_dir: nx.DiGraph | Path | str | None) -> nx.DiGraph:
    if isinstance(graph_or_dir, nx.DiGraph):
        return graph_or_dir
    G, _props, _chains = load_knowledge_graph(graph_or_dir)
    return G


def _safe_reverse_pagerank(G: nx.DiGraph) -> dict[str, float]:
    n = G.number_of_nodes()
    if n == 0:
        return {}
    if G.number_of_edges() == 0:
        return {node: 1.0 / n for node in G.nodes()}
    G_rev = G.reverse(copy=True)
    try:
        return nx.pagerank(G_rev, alpha=0.85, max_iter=200, tol=1e-6)
    except Exception:
        # Fallback pure-Python power iteration if SciPy/convergence ever hiccups.
        pr = {node: 1.0 / n for node in G_rev.nodes()}
        alpha = 0.85
        for _ in range(50):
            new_pr = {node: (1.0 - alpha) / n for node in G_rev.nodes()}
            dangling_sum = sum(pr[u] for u in G_rev.nodes() if G_rev.out_degree(u) == 0)
            for node in G_rev.nodes():
                new_pr[node] += alpha * dangling_sum / n
            for u in G_rev.nodes():
                deg = G_rev.out_degree(u)
                if deg > 0:
                    share = alpha * pr[u] / deg
                    for v in G_rev.successors(u):
                        new_pr[v] += share
            pr = new_pr
        return pr


def _effective_bkt(attrs: dict[str, Any]) -> float:
    bkt = attrs.get("bktMastery")
    if isinstance(bkt, (int, float)):
        return max(0.0, min(1.0, float(bkt)))
    status = str(attrs.get("status") or "planned").lower()
    if status == "misconception":
        return 0.15
    if status == "shaky":
        return 0.35
    if status == "missing":
        return 0.10
    if status == "solid":
        return 0.85
    return 0.30


def analyze_bottlenecks(
    graph_or_dir: nx.DiGraph | Path | str | None,
    *,
    top: int = 10,
    bkt_threshold: float = 0.65,
) -> list[dict[str, Any]]:
    """Rank shaky, misconception, and low-BKT (<0.65) foundation nodes by downstream impact."""
    G = _ensure_graph(graph_or_dir)
    if G.number_of_nodes() == 0:
        return []

    rev_pr = _safe_reverse_pagerank(G)
    betweenness = nx.betweenness_centrality(G, normalized=True) if G.number_of_nodes() > 2 else {u: 0.0 for u in G.nodes()}

    candidates: list[dict[str, Any]] = []
    for u, attrs in G.nodes(data=True):
        status = str(attrs.get("status") or "planned").lower()
        raw_bkt = attrs.get("bktMastery")
        is_low_bkt = isinstance(raw_bkt, (int, float)) and float(raw_bkt) < bkt_threshold
        desc = nx.descendants(G, u)
        downstream_count = len(desc)

        is_candidate = (
            status in ("shaky", "misconception")
            or (is_low_bkt and (status != "planned" or downstream_count > 0))
            or (bool(attrs.get("missingProp")) and downstream_count > 0)
        )
        if not is_candidate:
            continue

        u_chapter = str(attrs.get("chapter") or "").strip()
        u_chapter_low = u_chapter.lower()
        blocked_chapters = sorted(
            {
                str(G.nodes[d].get("chapter") or "").strip()
                for d in desc
                if str(G.nodes[d].get("chapter") or "").strip()
                and str(G.nodes[d].get("chapter") or "").strip().lower() != u_chapter_low
                and str(G.nodes[d].get("chapter") or "").strip().lower() != "unknown"
            }
        )
        candidates.append(
            {
                "id": u,
                "attrs": attrs,
                "status": status,
                "rawBkt": float(raw_bkt) if isinstance(raw_bkt, (int, float)) else None,
                "effBkt": _effective_bkt(attrs),
                "downstreamIds": sorted(desc),
                "downstreamDescendants": downstream_count,
                "directDependents": G.out_degree(u),
                "reversePageRank": float(rev_pr.get(u, 0.0)),
                "betweenness": float(betweenness.get(u, 0.0)),
                "blockedChapters": blocked_chapters,
                "crossChapterReach": len(blocked_chapters),
            }
        )

    if not candidates:
        return []

    max_desc = max((c["downstreamDescendants"] for c in candidates), default=0)
    max_rev_pr = max((c["reversePageRank"] for c in candidates), default=0.0)
    max_cross = max((c["crossChapterReach"] for c in candidates), default=0)

    ranked: list[dict[str, Any]] = []
    for c in candidates:
        norm_desc = (c["downstreamDescendants"] / max_desc) if max_desc > 0 else 0.0
        norm_pr = (c["reversePageRank"] / max_rev_pr) if max_rev_pr > 0 else 0.0
        norm_cross = (c["crossChapterReach"] / max_cross) if max_cross > 0 else 0.0
        status_weight = STATUS_SEVERITY_WEIGHT.get(c["status"], 0.85)
        mastery_gap = max(0.0, 1.0 - c["effBkt"])

        # Weighted composite anchored on downstream blast radius (50%), reverse PageRank (30%),
        # cross-chapter reach (20%), plus small BKT tie-break for equal-topology leaves.
        impact_score = round(
            status_weight * (0.50 * norm_desc + 0.30 * norm_pr + 0.20 * norm_cross + 0.05 * mastery_gap),
            4,
        )

        attrs = c["attrs"]
        reason_parts = [f"status={c['status']}"]
        if c["rawBkt"] is not None:
            reason_parts.append(f"BKT={c['rawBkt']:.2f}")
        reason_parts.append(f"blocks {c['downstreamDescendants']} downstream node(s)")
        if c["crossChapterReach"] > 0:
            reason_parts.append(f"reaches {c['crossChapterReach']} other chapter(s): {', '.join(c['blockedChapters'][:3])}")

        ranked.append(
            {
                "id": c["id"],
                "title": attrs.get("title", c["id"]),
                "subject": attrs.get("subject", "General"),
                "chapter": attrs.get("chapter", "General"),
                "kind": attrs.get("kind", "derivation"),
                "status": c["status"],
                "bktMastery": c["rawBkt"],
                "downstreamDescendants": c["downstreamDescendants"],
                "directDependents": c["directDependents"],
                "reversePageRank": round(c["reversePageRank"], 5),
                "betweenness": round(c["betweenness"], 5),
                "crossChapterReach": c["crossChapterReach"],
                "blockedChapters": c["blockedChapters"],
                "downstreamIds": c["downstreamIds"][:15],
                "impactScore": impact_score,
                "reason": "; ".join(reason_parts),
            }
        )

    ranked.sort(
        key=lambda item: (
            -item["impactScore"],
            -item["downstreamDescendants"],
            -item["crossChapterReach"],
            -item["reversePageRank"],
            str(item["id"]),
        )
    )
    return ranked[: max(0, int(top))]


def analyze_islands(
    graph_or_dir: nx.DiGraph | Path | str | None,
) -> list[dict[str, Any]]:
    """Detect zero-in-degree nodes whose kind is NOT axiom/unconditional_truth/definition."""
    G = _ensure_graph(graph_or_dir)
    status_order = {"misconception": 0, "shaky": 1, "planned": 2, "solid": 3}
    islands: list[dict[str, Any]] = []

    for u, attrs in G.nodes(data=True):
        if attrs.get("missingProp"):
            continue
        if G.in_degree(u) != 0:
            continue
        kind_norm = str(attrs.get("kind") or "").strip().lower()
        if kind_norm in FOUNDATIONAL_KINDS:
            continue

        desc_count = len(nx.descendants(G, u))
        out_deg = G.out_degree(u)
        status = str(attrs.get("status") or "planned").lower()
        severity = "high" if (out_deg > 0 or status in ("shaky", "misconception")) else "medium"
        islands.append(
            {
                "id": u,
                "title": attrs.get("title", u),
                "subject": attrs.get("subject", "General"),
                "chapter": attrs.get("chapter", "General"),
                "kind": attrs.get("kind", "derivation"),
                "status": status,
                "bktMastery": attrs.get("bktMastery"),
                "outDegree": out_deg,
                "downstreamDescendants": desc_count,
                "severity": severity,
                "recommendation": (
                    "Derive this node from foundational premises in chains.jsonl "
                    "(or mark kind as axiom/unconditional_truth/definition if truly primitive)."
                ),
            }
        )

    islands.sort(
        key=lambda item: (
            -item["downstreamDescendants"],
            -item["outDegree"],
            status_order.get(item["status"], 9),
            str(item["id"]),
        )
    )
    return islands


def compute_path_to(
    graph_or_dir: nx.DiGraph | Path | str | None,
    target_id: str,
) -> dict[str, Any]:
    """Extract ancestor subgraph of `target_id` and compute a cycle-safe topological study sequence."""
    G = _ensure_graph(graph_or_dir)
    target = str(target_id or "").strip()
    if not target or target not in G:
        return {
            "found": False,
            "targetId": target,
            "error": f"Node '{target}' not found in knowledge graph.",
            "hasCycles": False,
            "cycleGroups": [],
            "totalSteps": 0,
            "solidCount": 0,
            "missingOrShakyCount": 0,
            "plannedCount": 0,
            "gapsToLearn": [],
            "sequence": [],
        }

    ancestors = nx.ancestors(G, target)
    sub_nodes = ancestors | {target}
    sub = G.subgraph(sub_nodes).copy()

    status_rank = {"solid": 0, "shaky": 1, "misconception": 2, "planned": 3, "missing": 4}

    def _node_tie_key(nid: str) -> tuple[int, int, str, str]:
        attrs = sub.nodes[nid]
        return (
            1 if nid == target else 0,
            status_rank.get(str(attrs.get("status") or "planned").lower(), 5),
            str(attrs.get("chapter") or "").lower(),
            nid,
        )

    has_cycles = not nx.is_directed_acyclic_graph(sub)
    cycle_groups: list[list[str]] = []
    cycle_member_set: set[str] = set()

    if not has_cycles:
        ordered_ids = list(nx.lexicographical_topological_sort(sub, key=_node_tie_key))
    else:
        # Cycle-safe SCC condensation topological sort.
        cond = nx.condensation(sub)
        for comp_id in cond.nodes():
            members = sorted(cond.nodes[comp_id]["members"], key=_node_tie_key)
            if len(members) > 1 or any(sub.has_edge(m, m) for m in members):
                cycle_groups.append(members)
                cycle_member_set.update(members)

        def _comp_tie_key(comp_id: int) -> tuple[int, str]:
            members = cond.nodes[comp_id]["members"]
            return (1 if target in members else 0, min(members))

        ordered_ids = []
        for comp_id in nx.lexicographical_topological_sort(cond, key=_comp_tie_key):
            members = sorted(cond.nodes[comp_id]["members"], key=_node_tie_key)
            ordered_ids.extend(members)

    # Index incoming hyperedges inside the ancestor subgraph for clarity.
    all_hyperedges = G.graph.get("hyperedges") or []
    incoming_by_conc: dict[str, list[dict[str, Any]]] = {}
    for he in all_hyperedges:
        conc = he.get("conclusion")
        if conc in sub_nodes:
            incoming_by_conc.setdefault(conc, []).append(he)

    sequence: list[dict[str, Any]] = []
    solid_count = 0
    missing_or_shaky = 0
    planned_count = 0
    gaps_to_learn: list[str] = []

    for step_idx, nid in enumerate(ordered_ids, start=1):
        attrs = sub.nodes[nid]
        status = str(attrs.get("status") or "planned").lower()
        is_missing = bool(attrs.get("missingProp")) or status == "missing"
        if is_missing:
            readiness = "missing"
            missing_or_shaky += 1
            gaps_to_learn.append(nid)
        elif status == "solid":
            readiness = "solid"
            solid_count += 1
        elif status in ("shaky", "misconception"):
            readiness = "shaky"
            missing_or_shaky += 1
            gaps_to_learn.append(nid)
        else:
            readiness = "planned"
            planned_count += 1
            gaps_to_learn.append(nid)

        direct_prereqs = sorted(sub.predecessors(nid))
        joint_groups = [
            {
                "premises": he["premises"],
                "relation": he["relation"],
                "why": he["why"],
                "isJoint": he["isJoint"],
            }
            for he in incoming_by_conc.get(nid, [])
        ]

        sequence.append(
            {
                "step": step_idx,
                "id": nid,
                "title": attrs.get("title", nid),
                "subject": attrs.get("subject", "General"),
                "chapter": attrs.get("chapter", "General"),
                "kind": attrs.get("kind", "derivation"),
                "status": status,
                "bktMastery": attrs.get("bktMastery"),
                "readiness": readiness,
                "isTarget": nid == target,
                "inCycle": nid in cycle_member_set,
                "directPrereqs": direct_prereqs,
                "chains": joint_groups,
            }
        )

    return {
        "found": True,
        "targetId": target,
        "targetTitle": G.nodes[target].get("title", target),
        "hasCycles": has_cycles,
        "cycleGroups": cycle_groups,
        "totalSteps": len(sequence),
        "solidCount": solid_count,
        "missingOrShakyCount": missing_or_shaky,
        "plannedCount": planned_count,
        "gapsToLearn": gaps_to_learn,
        "sequence": sequence,
    }


def _tokenize_stem_terms(text: str) -> set[str]:
    norm = unicodedata.normalize("NFKC", str(text or "")).lower()
    norm = re.sub(r"\\(?:text|mathrm|mathbf|vec|hat|bar|frac|sqrt|left|right)\b", " ", norm)
    raw = re.findall(r"[a-z]{3,}", norm)
    out: set[str] = set()
    for tok in raw:
        if tok in COMBO_STOP_WORDS:
            continue
        stem = tok
        for suf in ("ations", "ation", "ments", "ment", "ings", "ing", "ies", "ed", "es", "s"):
            if len(stem) > len(suf) + 3 and stem.endswith(suf):
                stem = stem[: -len(suf)] + ("y" if suf == "ies" else "")
                break
        if stem not in COMBO_STOP_WORDS and len(stem) >= 3:
            out.add(stem)
    return out


def _detect_structural_themes(text: str, tags: list[str]) -> set[str]:
    blob = (str(text or "") + " " + " ".join(tags)).lower()
    matched: set[str] = set()
    for theme, stems in STRUCTURAL_THEMES.items():
        if any(st in blob for st in stems):
            matched.add(theme)
    return matched


def find_combos(
    graph_or_dir: nx.DiGraph | Path | str | None,
    *,
    top: int = 8,
) -> list[dict[str, Any]]:
    """Find cross-chapter pairs of `solid` nodes sharing structural themes/keywords/tags."""
    G = _ensure_graph(graph_or_dir)
    solid_nodes: list[dict[str, Any]] = []
    for u, attrs in G.nodes(data=True):
        if attrs.get("missingProp"):
            continue
        if str(attrs.get("status") or "").lower() != "solid":
            continue
        chapter = str(attrs.get("chapter") or "").strip()
        if not chapter or chapter.lower() == "unknown":
            continue
        tags = list(attrs.get("tags") or [])
        text_blob = " ".join(
            [
                str(attrs.get("title") or ""),
                " ".join(attrs.get("aliases") or []),
                str(attrs.get("notes") or ""),
                chapter,
            ]
        )
        solid_nodes.append(
            {
                "id": u,
                "title": str(attrs.get("title") or u),
                "subject": str(attrs.get("subject") or "General"),
                "chapter": chapter,
                "bkt": _effective_bkt(attrs),
                "tags": set(tags),
                "themes": _detect_structural_themes(text_blob, tags),
                "keywords": _tokenize_stem_terms(
                    " ".join(
                        [
                            str(attrs.get("title") or ""),
                            " ".join(attrs.get("aliases") or []),
                            str(attrs.get("notes") or ""),
                        ]
                    )
                ),
            }
        )

    if len(solid_nodes) < 2:
        return []

    raw_pairs: list[dict[str, Any]] = []
    for i in range(len(solid_nodes)):
        a = solid_nodes[i]
        for j in range(i + 1, len(solid_nodes)):
            b = solid_nodes[j]
            if a["chapter"].lower() == b["chapter"].lower():
                continue

            shared_tags = sorted(a["tags"] & b["tags"])
            shared_themes = sorted(a["themes"] & b["themes"])
            shared_keywords = sorted(a["keywords"] & b["keywords"])
            if not (shared_tags or shared_themes or shared_keywords):
                continue

            cross_subject = a["subject"].lower() != b["subject"].lower()
            score = (
                3.0 * len(shared_tags)
                + 2.2 * len(shared_themes)
                + 1.1 * min(4, len(shared_keywords))
                + (1.0 if cross_subject else 0.5)
                + 0.4 * (a["bkt"] + b["bkt"])
            )

            bridge_terms = shared_tags[:2] + [t.replace("_", "/") for t in shared_themes[:2]] + shared_keywords[:3]
            bridge_label = ", ".join(dict.fromkeys(bridge_terms)) or "shared structure"

            # Canonical deterministic ordering of pair members.
            first, second = (a, b) if a["id"] <= b["id"] else (b, a)
            raw_pairs.append(
                {
                    "nodeA": {
                        "id": first["id"],
                        "title": first["title"],
                        "subject": first["subject"],
                        "chapter": first["chapter"],
                    },
                    "nodeB": {
                        "id": second["id"],
                        "title": second["title"],
                        "subject": second["subject"],
                        "chapter": second["chapter"],
                    },
                    "chapters": [first["chapter"], second["chapter"]],
                    "crossSubject": cross_subject,
                    "sharedTags": shared_tags,
                    "sharedThemes": shared_themes,
                    "sharedKeywords": shared_keywords[:8],
                    "synergyScore": round(score, 3),
                    "drillPrompt": (
                        f"Design an integrative multi-concept synthesis problem linking '{first['title']}' ({first['chapter']}) "
                        f"with '{second['title']}' ({second['chapter']}) via {bridge_label}."
                    ),
                }
            )

    raw_pairs.sort(
        key=lambda item: (
            -item["synergyScore"],
            item["nodeA"]["id"],
            item["nodeB"]["id"],
        )
    )

    # Light diversity filter so one hub node doesn't monopolize all slots when other pairs exist.
    limit = max(0, int(top))
    if limit == 0:
        return []
    selected: list[dict[str, Any]] = []
    overflow: list[dict[str, Any]] = []
    node_uses: dict[str, int] = {}
    for pair in raw_pairs:
        a_id = pair["nodeA"]["id"]
        b_id = pair["nodeB"]["id"]
        if node_uses.get(a_id, 0) < 3 and node_uses.get(b_id, 0) < 3:
            selected.append(pair)
            node_uses[a_id] = node_uses.get(a_id, 0) + 1
            node_uses[b_id] = node_uses.get(b_id, 0) + 1
            if len(selected) >= limit:
                break
        else:
            overflow.append(pair)

    if len(selected) < limit:
        selected.extend(overflow[: limit - len(selected)])
    return selected[:limit]


def _render_human_report(payload: dict[str, Any]) -> str:
    summary = payload["summary"]
    lines = [
        "=== Syntopik · Knowledge Graph Intelligence Report ===",
        (
            f"Nodes: {summary['nodeCount']} ({summary['propositionCount']} propositions) · "
            f"Directed Edges: {summary['edgeCount']} · Unique Chains: {summary['uniqueChains']} "
            f"(Hyperedges: {summary['hyperedgeCount']})"
        ),
        "",
    ]

    if "bottlenecks" in payload:
        bns = payload["bottlenecks"]
        lines.append(f"## Bottleneck Foundations ({len(bns)})")
        if not bns:
            lines.append("  None — no shaky, misconception, or low-BKT bottlenecks detected.")
        else:
            for idx, b in enumerate(bns, start=1):
                lines.append(
                    f"  {idx}. [{b['status']}] {b['id']} ({b['chapter']}) · "
                    f"impact={b['impactScore']} · blocks={b['downstreamDescendants']} "
                    f"(cross-ch={b['crossChapterReach']}, revPR={b['reversePageRank']:.4f})"
                )
                lines.append(f"     {b['title']}")
        lines.append("")

    if "islands" in payload:
        isls = payload["islands"]
        lines.append(f"## Memorized Islands ({len(isls)})")
        if not isls:
            lines.append("  None — every non-foundational node has incoming reasoning chains.")
        else:
            for idx, isl in enumerate(isls, start=1):
                lines.append(
                    f"  {idx}. [{isl['severity']}] {isl['id']} ({isl['chapter']} · kind={isl['kind']} · "
                    f"blocks={isl['downstreamDescendants']}): {isl['title']}"
                )
        lines.append("")

    if "pathTo" in payload:
        pt = payload["pathTo"]
        lines.append(f"## Study Path to '{pt['targetId']}'")
        if not pt["found"]:
            lines.append(f"  Error: {pt.get('error', 'Target node not found.')}")
        else:
            cycle_note = f" · CYCLES condensed ({len(pt['cycleGroups'])})" if pt["hasCycles"] else " · DAG verified"
            lines.append(
                f"  Steps: {pt['totalSteps']} ({pt['solidCount']} solid, "
                f"{pt['missingOrShakyCount']} shaky/missing, {pt['plannedCount']} planned){cycle_note}"
            )
            for s in pt["sequence"]:
                tag = "TARGET" if s["isTarget"] else s["readiness"].upper()
                cyc = " [cycle]" if s["inCycle"] else ""
                lines.append(
                    f"  {s['step']:>2}. [{tag:<7}] {s['id']} ({s['chapter']}){cyc} — {s['title']}"
                )
        lines.append("")

    if "combos" in payload:
        combos = payload["combos"]
        lines.append(f"## Cross-Chapter Synthesis Combos ({len(combos)})")
        if not combos:
            lines.append("  None yet — master solid propositions across 2+ chapters with overlapping themes.")
        else:
            for idx, c in enumerate(combos, start=1):
                lines.append(
                    f"  {idx}. ({c['synergyScore']}) {c['nodeA']['id']} [{c['nodeA']['chapter']}] "
                    f"x {c['nodeB']['id']} [{c['nodeB']['chapter']}]"
                )
                lines.append(f"     {c['drillPrompt']}")
        lines.append("")

    return "\n".join(lines).rstrip()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Syntopik Knowledge Graph Intelligence: bottlenecks, memorized islands, cycle-safe study paths, and integrative cross-chapter synthesis combos."
    )
    parser.add_argument("--kg-dir", help="Override knowledge-graph directory (or SYNT_KG_DIR / MLH_KG_DIR)")
    parser.add_argument("--bottlenecks", action="store_true", help="Rank shaky/misconception/low-BKT foundation bottlenecks")
    parser.add_argument("--islands", action="store_true", help="Detect zero-in-degree memorized islands (non-axiom/definition)")
    parser.add_argument("--path-to", dest="path_to", metavar="TARGET_ID", help="Compute cycle-safe topological prerequisite sequence to TARGET_ID")
    parser.add_argument("--combos", action="store_true", help="Generate integrative multi-concept synthesis pairs from solid nodes")
    parser.add_argument("--all", action="store_true", help="Run bottlenecks, islands, and combos together")
    parser.add_argument("--top", type=int, default=None, help="Max results (default: 10 for bottlenecks, 8 for combos)")
    parser.add_argument("--bkt-threshold", type=float, default=0.65, help="Low-BKT threshold for bottleneck detection (default: 0.65)")
    parser.add_argument("--json", action="store_true", help="Emit structured JSON output")
    args = parser.parse_args(argv)

    run_b = args.bottlenecks or args.all
    run_i = args.islands or args.all
    run_c = args.combos or args.all
    run_p = bool(args.path_to)
    if not (run_b or run_i or run_c or run_p):
        run_b = run_i = run_c = True

    kg_dir, _props_file, _chains_file = resolve_kg_paths(args.kg_dir)
    G, props, norm_chains = load_knowledge_graph(kg_dir)

    payload: dict[str, Any] = {
        "kgDir": str(kg_dir),
        "summary": {
            "propositionCount": len(props),
            "nodeCount": G.number_of_nodes(),
            "edgeCount": G.number_of_edges(),
            "uniqueChains": len(norm_chains),
            "hyperedgeCount": int(G.graph.get("hyperedgeCount", 0)),
            "isDag": nx.is_directed_acyclic_graph(G) if G.number_of_nodes() > 0 else True,
        },
    }

    if run_b:
        top_b = args.top if args.top is not None else 10
        payload["bottlenecks"] = analyze_bottlenecks(G, top=top_b, bkt_threshold=args.bkt_threshold)

    if run_i:
        payload["islands"] = analyze_islands(G)

    if run_p:
        path_res = compute_path_to(G, args.path_to)
        payload["pathTo"] = path_res

    if run_c:
        top_c = args.top if args.top is not None else 8
        payload["combos"] = find_combos(G, top=top_c)

    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(_render_human_report(payload))

    if run_p and not payload["pathTo"]["found"] and not (run_b or run_i or run_c):
        sys.exit(1)


if __name__ == "__main__":
    main()
