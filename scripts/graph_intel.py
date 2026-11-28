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
  5. Confusable Lookalikes (`--confusables`): pairs of nodes with high surface /
     lexical similarity AND divergent governing premises (or explicit mistake
     confusions) for Rohrer & Taylor discriminative-contrast interleaving.

Runs cleanly on bare Python 3.12+ (pure-stdlib Tarjan SCC / BFS / Brandes /
PageRank fallback when `networkx` is not installed).
"""

from __future__ import annotations

import argparse
import hashlib
import heapq
import json
import math
import os
import re
import sys
import unicodedata
from collections import deque
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    import networkx as nx  # type: ignore[import-not-found]
except ImportError:
    nx = None  # type: ignore[assignment]

from syntopik.paths import resolve_kg_dir as _resolve_kg_dir_core  # noqa: E402
from syntopik.schemas import normalize_chain  # noqa: E402
from syntopik.storage import load_jsonl  # noqa: E402

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


# ---------------------------------------------------------------------------
# Pure-stdlib DiGraph fallback (drop-in subset of NetworkX DiGraph API)
# ---------------------------------------------------------------------------


class _NodeView:
    def __init__(self, store: dict[Any, dict[str, Any]]) -> None:
        self._store = store

    def __call__(self, data: bool = False):
        if data:
            return list(self._store.items())
        return list(self._store.keys())

    def __getitem__(self, key: Any) -> dict[str, Any]:
        return self._store[key]

    def __contains__(self, key: Any) -> bool:
        return key in self._store

    def __iter__(self):
        return iter(self._store)

    def __len__(self) -> int:
        return len(self._store)


class StdlibDiGraph:
    """Minimal pure-stdlib directed graph implementing the subset of NetworkX API used by Syntopik."""

    def __init__(self) -> None:
        self._nodes: dict[Any, dict[str, Any]] = {}
        self._succ: dict[Any, dict[Any, dict[str, Any]]] = {}
        self._pred: dict[Any, dict[Any, dict[str, Any]]] = {}
        self.graph: dict[str, Any] = {}
        self.nodes = _NodeView(self._nodes)

    def __contains__(self, n: Any) -> bool:
        return n in self._nodes

    def __len__(self) -> int:
        return len(self._nodes)

    def __getitem__(self, u: Any) -> dict[Any, dict[str, Any]]:
        return self._succ[u]

    def number_of_nodes(self) -> int:
        return len(self._nodes)

    def number_of_edges(self) -> int:
        return sum(len(nbrs) for nbrs in self._succ.values())

    def add_node(self, u: Any, **attrs: Any) -> None:
        if u not in self._nodes:
            self._nodes[u] = dict(attrs)
            self._succ[u] = {}
            self._pred[u] = {}
        else:
            self._nodes[u].update(attrs)

    def add_edge(self, u: Any, v: Any, **attrs: Any) -> None:
        if u not in self._nodes:
            self.add_node(u)
        if v not in self._nodes:
            self.add_node(v)
        edge_dict = self._succ[u].get(v)
        if edge_dict is None:
            edge_dict = dict(attrs)
            self._succ[u][v] = edge_dict
            self._pred[v][u] = edge_dict
        else:
            edge_dict.update(attrs)

    def has_edge(self, u: Any, v: Any) -> bool:
        return u in self._succ and v in self._succ[u]

    def in_degree(self, u: Any) -> int:
        return len(self._pred.get(u, ()))

    def out_degree(self, u: Any) -> int:
        return len(self._succ.get(u, ()))

    def predecessors(self, u: Any):
        return iter(self._pred.get(u, ()))

    def successors(self, u: Any):
        return iter(self._succ.get(u, ()))

    def copy(self) -> StdlibDiGraph:
        g = StdlibDiGraph()
        g.graph = dict(self.graph)
        for u, attrs in self._nodes.items():
            g.add_node(u, **dict(attrs))
        for u, nbrs in self._succ.items():
            for v, eattrs in nbrs.items():
                g.add_edge(u, v, **dict(eattrs))
        return g

    def reverse(self, copy: bool = True) -> StdlibDiGraph:
        g = StdlibDiGraph()
        g.graph = dict(self.graph)
        for u, attrs in self._nodes.items():
            g.add_node(u, **dict(attrs))
        for u, nbrs in self._succ.items():
            for v, eattrs in nbrs.items():
                g.add_edge(v, u, **dict(eattrs))
        return g

    def subgraph(self, nbunch: Any) -> StdlibDiGraph:
        keep = set(nbunch)
        g = StdlibDiGraph()
        g.graph = dict(self.graph)
        for u in keep:
            if u in self._nodes:
                g.add_node(u, **dict(self._nodes[u]))
        for u in keep:
            if u in self._succ:
                for v, eattrs in self._succ[u].items():
                    if v in keep:
                        g.add_edge(u, v, **dict(eattrs))
        return g


def _is_graph_obj(obj: Any) -> bool:
    if isinstance(obj, StdlibDiGraph):
        return True
    if nx is not None and isinstance(obj, nx.DiGraph):
        return True
    return False


def _graph_descendants(G: Any, u: Any) -> set[Any]:
    if nx is not None and isinstance(G, nx.DiGraph):
        return set(nx.descendants(G, u))
    visited: set[Any] = set()
    q: deque[Any] = deque(G.successors(u))
    while q:
        cur = q.popleft()
        if cur in visited or cur == u:
            continue
        visited.add(cur)
        for nxt in G.successors(cur):
            if nxt not in visited and nxt != u:
                q.append(nxt)
    return visited


def _graph_ancestors(G: Any, u: Any) -> set[Any]:
    if nx is not None and isinstance(G, nx.DiGraph):
        return set(nx.ancestors(G, u))
    visited: set[Any] = set()
    q: deque[Any] = deque(G.predecessors(u))
    while q:
        cur = q.popleft()
        if cur in visited or cur == u:
            continue
        visited.add(cur)
        for prv in G.predecessors(cur):
            if prv not in visited and prv != u:
                q.append(prv)
    return visited


def _graph_is_dag(G: Any) -> bool:
    if nx is not None and isinstance(G, nx.DiGraph):
        return bool(nx.is_directed_acyclic_graph(G))
    in_deg = {u: G.in_degree(u) for u in G.nodes()}
    q: deque[Any] = deque(u for u, d in in_deg.items() if d == 0)
    visited = 0
    while q:
        u = q.popleft()
        visited += 1
        for v in G.successors(u):
            in_deg[v] -= 1
            if in_deg[v] == 0:
                q.append(v)
    return visited == G.number_of_nodes()


def _graph_lex_topo_sort(G: Any, key: Callable[[Any], Any]) -> list[Any]:
    if nx is not None and isinstance(G, nx.DiGraph):
        return list(nx.lexicographical_topological_sort(G, key=key))
    in_deg = {u: G.in_degree(u) for u in G.nodes()}
    heap: list[tuple[Any, Any]] = []
    for u, d in in_deg.items():
        if d == 0:
            heapq.heappush(heap, (key(u), u))
    order: list[Any] = []
    while heap:
        _, u = heapq.heappop(heap)
        order.append(u)
        for v in G.successors(u):
            in_deg[v] -= 1
            if in_deg[v] == 0:
                heapq.heappush(heap, (key(v), v))
    if len(order) != G.number_of_nodes():
        raise ValueError("Graph contains a cycle")
    return order


def _tarjan_scc(G: Any) -> list[set[Any]]:
    """Iterative Tarjan strongly connected components algorithm (pure stdlib)."""
    index_counter = 0
    indices: dict[Any, int] = {}
    lowlink: dict[Any, int] = {}
    on_stack: set[Any] = set()
    stack: list[Any] = []
    sccs: list[set[Any]] = []

    for start in G.nodes():
        if start in indices:
            continue
        call_stack: list[tuple[Any, Any]] = [(start, iter(G.successors(start)))]
        indices[start] = lowlink[start] = index_counter
        index_counter += 1
        stack.append(start)
        on_stack.add(start)

        while call_stack:
            v, nbr_iter = call_stack[-1]
            try:
                w = next(nbr_iter)
                if w not in indices:
                    indices[w] = lowlink[w] = index_counter
                    index_counter += 1
                    stack.append(w)
                    on_stack.add(w)
                    call_stack.append((w, iter(G.successors(w))))
                elif w in on_stack:
                    if indices[w] < lowlink[v]:
                        lowlink[v] = indices[w]
            except StopIteration:
                call_stack.pop()
                if lowlink[v] == indices[v]:
                    comp: set[Any] = set()
                    while True:
                        w = stack.pop()
                        on_stack.remove(w)
                        comp.add(w)
                        if w == v:
                            break
                    sccs.append(comp)
                if call_stack:
                    parent = call_stack[-1][0]
                    if lowlink[v] < lowlink[parent]:
                        lowlink[parent] = lowlink[v]

    return sccs


def _graph_condensation(G: Any) -> Any:
    if nx is not None and isinstance(G, nx.DiGraph):
        return nx.condensation(G)
    sccs = _tarjan_scc(G)
    cond = StdlibDiGraph()
    node_to_comp: dict[Any, int] = {}
    for cid, members in enumerate(sccs):
        cond.add_node(cid, members=set(members))
        for m in members:
            node_to_comp[m] = cid
    for u in G.nodes():
        cu = node_to_comp[u]
        for v in G.successors(u):
            cv = node_to_comp[v]
            if cu != cv and not cond.has_edge(cu, cv):
                cond.add_edge(cu, cv)
    return cond


def _graph_betweenness(G: Any) -> dict[Any, float]:
    n = G.number_of_nodes()
    if n <= 2:
        return {u: 0.0 for u in G.nodes()}
    if nx is not None and isinstance(G, nx.DiGraph):
        return dict(nx.betweenness_centrality(G, normalized=True))
    # Pure-stdlib Brandes algorithm for unweighted directed graphs.
    # Per-source state is kept only for nodes reachable from `s` (sparse dicts), so a
    # source costs O(reachable) instead of O(n); sources with no successors add nothing.
    cb: dict[Any, float] = {v: 0.0 for v in G.nodes()}
    for s in G.nodes():
        if not any(True for _ in G.successors(s)):
            continue
        S: list[Any] = []
        P: dict[Any, list[Any]] = {s: []}
        sigma: dict[Any, float] = {s: 1.0}
        dist: dict[Any, int] = {s: 0}
        Q: deque[Any] = deque([s])
        while Q:
            v = Q.popleft()
            S.append(v)
            dv = dist[v]
            sv = sigma[v]
            for w in G.successors(v):
                dw = dist.get(w)
                if dw is None:
                    Q.append(w)
                    dist[w] = dw = dv + 1
                    sigma[w] = 0.0
                    P[w] = []
                if dw == dv + 1:
                    sigma[w] += sv
                    P[w].append(v)
        delta: dict[Any, float] = dict.fromkeys(S, 0.0)
        while S:
            w = S.pop()
            sw = sigma[w]
            if sw > 0:
                dw1 = 1.0 + delta[w]
                for v in P[w]:
                    delta[v] += (sigma[v] / sw) * dw1
            if w != s:
                cb[w] += delta[w]
    scale = 1.0 / ((n - 1) * (n - 2))
    return {v: val * scale for v, val in cb.items()}


# ---------------------------------------------------------------------------
# Path & graph builders
# ---------------------------------------------------------------------------


def resolve_kg_paths(kg_dir_arg: Path | str | None = None) -> tuple[Path, Path, Path]:
    kg_dir = _resolve_kg_dir_core(explicit=kg_dir_arg)
    return kg_dir, kg_dir / "propositions.jsonl", kg_dir / "chains.jsonl"


def build_graph(
    props: list[dict[str, Any]],
    chains: list[dict[str, Any]],
) -> tuple[Any, list[dict[str, Any]]]:
    """Build a directed graph (`nx.DiGraph` when installed, else pure-stdlib `StdlibDiGraph`)
    expanding both binary edges and multi-premise hyperedges.
    """
    G: Any = nx.DiGraph() if nx is not None else StdlibDiGraph()
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
            statement=str(p.get("statement") or "").strip(),
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
    isomorphic_chains: list[dict[str, Any]] = []
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
                    statement="",
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

        if str(norm["relation"]).strip().lower() == "isomorphic_to":
            isomorphic_chains.append(norm)
            continue

        he_meta = {
            "chainKey": norm["chainKey"],
            "premises": list(norm["premises"]),
            "conclusion": conc,
            "relation": norm["relation"],
            "why": norm["why"],
            "isJoint": norm["isJoint"],
            "arity": norm["arity"],
            "encompassWeight": norm["encompassWeight"],
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
    G.graph["isomorphicChains"] = isomorphic_chains
    G.graph["hyperedgeCount"] = sum(1 for c in normalized_chains if c["isJoint"])
    G.graph["uniqueChains"] = len(normalized_chains)
    return G, normalized_chains


def load_knowledge_graph(
    kg_dir: Path | str | None = None,
) -> tuple[Any, list[dict[str, Any]], list[dict[str, Any]]]:
    """Load `propositions.jsonl` + `chains.jsonl` into a directed graph."""
    resolved_kg, props_file, chains_file = resolve_kg_paths(kg_dir)
    props = load_jsonl(props_file)
    chains = load_jsonl(chains_file)
    G, norm_chains = build_graph(props, chains)
    G.graph["kgDir"] = str(resolved_kg)
    return G, props, norm_chains


def _ensure_graph(graph_or_dir: Any) -> Any:
    if _is_graph_obj(graph_or_dir):
        return graph_or_dir
    G, _props, _chains = load_knowledge_graph(graph_or_dir)
    return G


def _safe_reverse_pagerank(G: Any) -> dict[str, float]:
    n = G.number_of_nodes()
    if n == 0:
        return {}
    if G.number_of_edges() == 0:
        return {node: 1.0 / n for node in G.nodes()}
    G_rev = G.reverse(copy=True)
    if nx is not None and isinstance(G_rev, nx.DiGraph):
        try:
            return dict(nx.pagerank(G_rev, alpha=0.85, max_iter=200, tol=1e-6))
        except Exception:
            pass
    # Pure-Python power iteration (matches NetworkX PageRank cleanly on bare Python).
    nodes_list = list(G_rev.nodes())
    pr = {node: 1.0 / n for node in nodes_list}
    alpha = 0.85
    for _ in range(80):
        new_pr = {node: (1.0 - alpha) / n for node in nodes_list}
        dangling_sum = sum(pr[u] for u in nodes_list if G_rev.out_degree(u) == 0)
        dangling_share = alpha * dangling_sum / n
        for node in nodes_list:
            new_pr[node] += dangling_share
        for u in nodes_list:
            deg = G_rev.out_degree(u)
            if deg > 0:
                share = alpha * pr[u] / deg
                for v in G_rev.successors(u):
                    new_pr[v] += share
        diff = sum(abs(new_pr[u] - pr[u]) for u in nodes_list)
        pr = new_pr
        if diff < 1e-7:
            break
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


# ---------------------------------------------------------------------------
# Graph Intelligence Analyses
# ---------------------------------------------------------------------------


def analyze_bottlenecks(
    graph_or_dir: Any,
    *,
    top: int = 10,
    bkt_threshold: float = 0.65,
) -> list[dict[str, Any]]:
    """Rank shaky, misconception, and low-BKT (<0.65) foundation nodes by downstream impact."""
    G = _ensure_graph(graph_or_dir)
    if G.number_of_nodes() == 0:
        return []

    rev_pr = _safe_reverse_pagerank(G)
    betweenness = _graph_betweenness(G)

    candidates: list[dict[str, Any]] = []
    for u, attrs in G.nodes(data=True):
        status = str(attrs.get("status") or "planned").lower()
        raw_bkt = attrs.get("bktMastery")
        is_low_bkt = isinstance(raw_bkt, (int, float)) and float(raw_bkt) < bkt_threshold
        desc = _graph_descendants(G, u)
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


def analyze_islands(graph_or_dir: Any) -> list[dict[str, Any]]:
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

        desc_count = len(_graph_descendants(G, u))
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


def compute_path_to(graph_or_dir: Any, target_id: str) -> dict[str, Any]:
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

    ancestors = _graph_ancestors(G, target)
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

    has_cycles = not _graph_is_dag(sub)
    cycle_groups: list[list[str]] = []
    cycle_member_set: set[str] = set()

    if not has_cycles:
        ordered_ids = _graph_lex_topo_sort(sub, key=_node_tie_key)
    else:
        cond = _graph_condensation(sub)
        for comp_id in cond.nodes():
            members = sorted(cond.nodes[comp_id]["members"], key=_node_tie_key)
            if len(members) > 1 or any(sub.has_edge(m, m) for m in members):
                cycle_groups.append(members)
                cycle_member_set.update(members)

        def _comp_tie_key(comp_id: int) -> tuple[int, str]:
            members = cond.nodes[comp_id]["members"]
            return (1 if target in members else 0, min(members))

        ordered_ids = []
        for comp_id in _graph_lex_topo_sort(cond, key=_comp_tie_key):
            members = sorted(cond.nodes[comp_id]["members"], key=_node_tie_key)
            ordered_ids.extend(members)

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


def find_combos(graph_or_dir: Any, *, top: int = 8) -> list[dict[str, Any]]:
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
                str(attrs.get("statement") or ""),
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
                            str(attrs.get("statement") or ""),
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


def _char_trigrams(terms: set[str]) -> set[str]:
    grams: set[str] = set()
    for t in terms:
        if len(t) <= 3:
            grams.add(t)
        else:
            for i in range(len(t) - 2):
                grams.add(t[i : i + 3])
    return grams


_CONFUSABLE_CACHE: dict[str, list[tuple[Any, ...]]] = {}
_CONFUSABLE_CACHE_MAX = 8
# Bump when the pair scoring in `find_confusables` changes, so on-disk results are recomputed.
_CONFUSABLE_ALGO_VERSION = 1
_CONFUSABLE_DISK_FILE = Path(".cache") / "confusables.json"


def _confusable_rows_to_json(rows: list[tuple[Any, ...]]) -> list[list[Any]]:
    return [[*r[:4], sorted(r[4]), sorted(r[5]), *r[6:]] for r in rows]


def _confusable_rows_from_json(rows: Any) -> list[tuple[Any, ...]] | None:
    try:
        return [(r[0], r[1], r[2], r[3], set(r[4]), set(r[5]), r[6], r[7], r[8], bool(r[9])) for r in rows]
    except (TypeError, IndexError, KeyError):
        return None


def _read_confusable_disk_cache(kg_dir: Path | None, key: str) -> list[tuple[Any, ...]] | None:
    """Pair-scan results survive between CLI runs; reviews do not change the key, edits do."""
    if kg_dir is None:
        return None
    try:
        data = json.loads((kg_dir / _CONFUSABLE_DISK_FILE).read_text(encoding="utf-8"))
        entry = data.get("entries", {}).get(key) if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None
    return _confusable_rows_from_json(entry) if isinstance(entry, list) else None


def _write_confusable_disk_cache(kg_dir: Path | None, key: str, rows: list[tuple[Any, ...]]) -> None:
    if kg_dir is None or not kg_dir.is_dir():
        return
    path = kg_dir / _CONFUSABLE_DISK_FILE
    try:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            entries = data.get("entries", {}) if isinstance(data, dict) else {}
        except (OSError, ValueError):
            entries = {}
        entries.pop(key, None)
        entries[key] = _confusable_rows_to_json(rows)
        while len(entries) > _CONFUSABLE_CACHE_MAX:
            entries.pop(next(iter(entries)))
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps({"entries": entries}), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        # Read-only vaults (plugin caches, CI checkouts) just skip the disk cache.
        return


def find_confusables(
    graph_or_dir: Any,
    *,
    kg_dir: Path | str | None = None,
    top: int = 8,
    min_similarity: float = 0.30,
) -> list[dict[str, Any]]:
    """Pair lookalike propositions with high lexical/theme similarity AND divergent `chains.jsonl`
    governing premises (or explicit `confusedWith` hits in `mistakes.jsonl`) for interleaved
    discriminative-contrast drills (`[A, B, A']`).
    """
    G = _ensure_graph(graph_or_dir)

    # Load optional mistakes.jsonl for explicit `confusedWith` links.
    confused_hits: dict[tuple[str, str], int] = {}
    resolved_kg: Path | None = None
    if kg_dir is not None and str(kg_dir).strip():
        resolved_kg = Path(kg_dir).expanduser().resolve()
    elif isinstance(G.graph.get("kgDir"), str) and G.graph["kgDir"]:
        resolved_kg = Path(G.graph["kgDir"])
    if resolved_kg is not None:
        for m in load_jsonl(resolved_kg / "mistakes.jsonl"):
            p1 = str(m.get("propId") or "").strip()
            p2 = str(m.get("confusedWith") or "").strip()
            if p1 and p2 and p1 != p2:
                pair_key = (p1, p2) if p1 <= p2 else (p2, p1)
                confused_hits[pair_key] = confused_hits.get(pair_key, 0) + 1

    # Gather incoming premises (direct + contrasts_with links) from chains.jsonl.
    premises_by_node: dict[str, set[str]] = {}
    explicit_contrast_pairs: set[tuple[str, str]] = set()
    for he in G.graph.get("hyperedges") or []:
        conc = str(he.get("conclusion") or "")
        rel = str(he.get("relation") or "requires").lower()
        prems = [str(p) for p in (he.get("premises") or []) if str(p)]
        if rel in ("contrasts_with", "contrasts", "contrast", "analogous"):
            for pr in prems:
                pk = (pr, conc) if pr <= conc else (conc, pr)
                explicit_contrast_pairs.add(pk)
        else:
            premises_by_node.setdefault(conc, set()).update(prems)

    # Prepare lexical/theme vectors per non-missing node.
    nodes_info: list[dict[str, Any]] = []
    doc_freq: dict[str, int] = {}
    for u, attrs in G.nodes(data=True):
        if attrs.get("missingProp"):
            continue
        tags = list(attrs.get("tags") or [])
        text_blob = " ".join(
            [
                str(u).replace("-", " ").replace("_", " "),
                str(attrs.get("title") or ""),
                str(attrs.get("statement") or ""),
                " ".join(attrs.get("aliases") or []),
                str(attrs.get("notes") or ""),
            ]
        )
        terms = _tokenize_stem_terms(text_blob)
        for t in terms:
            doc_freq[t] = doc_freq.get(t, 0) + 1
        nodes_info.append(
            {
                "id": u,
                "title": str(attrs.get("title") or u),
                "subject": str(attrs.get("subject") or "General"),
                "chapter": str(attrs.get("chapter") or "General"),
                "status": str(attrs.get("status") or "planned"),
                "terms": terms,
                "trigrams": _char_trigrams(terms),
                "themes": _detect_structural_themes(text_blob, tags),
                "premises": set(premises_by_node.get(u, set())),
            }
        )

    n_docs = len(nodes_info)
    if n_docs < 2:
        return []

    idf = {t: math.log(1.0 + (n_docs - df + 0.5) / (df + 0.5)) for t, df in doc_freq.items()}
    top_n = max(0, int(top))
    if top_n == 0:
        return []
    # Per-node IDF mass, computed once instead of once per pair (the pair loop is O(n^2)).
    for info in nodes_info:
        info["termWeight"] = sum(idf.get(t, 1.0) for t in info["terms"])
        info["trigramCount"] = len(info["trigrams"])

    explicit_ids: set[str] = set()
    for pk in list(confused_hits) + list(explicit_contrast_pairs):
        explicit_ids.update(pk)

    def _best_pairs() -> list[tuple[Any, ...]]:
        """Exact top-`top_n` pairs by (-score, -mistakeHits, idA, idB).

        Rows are cheap tuples; dicts are built only for the winners. Once `top_n` rows are
        held, a pair whose rounded score is below the current cut-off cannot enter, so it is
        dropped before any further work.
        """
        kept: list[tuple[Any, ...]] = []
        cutoff = -math.inf  # rounded score a new pair must reach to be considered
        for i in range(n_docs):
            a = nodes_info[i]
            a_id = a["id"]
            a_terms = a["terms"]
            a_tri = a["trigrams"]
            a_themes = a["themes"]
            a_weight = a["termWeight"]
            a_tri_n = a["trigramCount"]
            a_explicit = a_id in explicit_ids
            for j in range(i + 1, n_docs):
                b = nodes_info[j]

                b_id = b["id"]
                maybe_explicit = a_explicit and b_id in explicit_ids
                first, second = (a, b) if a_id <= b_id else (b, a)

                shared_theme_set = a_themes & b["themes"]
                theme_union = a_themes | b["themes"]
                theme_sim = (len(shared_theme_set) / len(theme_union)) if theme_union else 0.0

                # Premise divergence via symmetric difference of governing premises in chains.jsonl.
                p_a = first["premises"]
                p_b = second["premises"]
                p_union = p_a | p_b
                if p_union:
                    premise_div = len(p_a ^ p_b) / len(p_union)
                else:
                    premise_div = 0.45 if (first["chapter"].lower() != second["chapter"].lower() or shared_theme_set) else 0.0

                if not maybe_explicit:
                    if premise_div <= 0.0:
                        continue
                    if cutoff > -math.inf:
                        # Cheap upper bound on the score from set sizes alone (Dice <= 2*min/sum).
                        b_weight = b["termWeight"]
                        b_tri_n = b["trigramCount"]
                        term_ub = (2.0 * min(a_weight, b_weight) / (a_weight + b_weight)) if (a_weight + b_weight) > 0 else 0.0
                        tri_ub = (2.0 * min(a_tri_n, b_tri_n) / (a_tri_n + b_tri_n)) if (a_tri_n + b_tri_n) > 0 else 0.0
                        lex_ub = 0.70 * min(1.0, term_ub) + 0.30 * tri_ub
                        surface_ub = max(lex_ub, 0.70 * lex_ub + 0.30 * theme_sim)
                        if 0.48 * surface_ub + 0.37 * premise_div + 0.15 * theme_sim + 1e-9 < cutoff - 0.0005:
                            continue

                # Weighted IDF Dice on stemmed terms + char-trigram Dice.
                shared_terms = a_terms & b["terms"]
                if shared_terms:
                    shared_w = sum(idf.get(t, 1.0) for t in shared_terms)
                    denom_w = a_weight + b["termWeight"]
                    term_dice = (2.0 * shared_w / denom_w) if denom_w > 0 else 0.0
                else:
                    term_dice = 0.0

                tri_union = a_tri_n + b["trigramCount"]
                tri_dice = (2.0 * len(a_tri & b["trigrams"]) / tri_union) if tri_union > 0 else 0.0
                lex_sim = 0.70 * term_dice + 0.30 * tri_dice
                surface_sim = max(lex_sim, 0.70 * lex_sim + 0.30 * theme_sim)

                if not maybe_explicit and surface_sim < min_similarity:
                    continue

                if maybe_explicit:
                    pair_key = (first["id"], second["id"])
                    mistake_hits = confused_hits.get(pair_key, 0)
                    is_explicit_contrast = pair_key in explicit_contrast_pairs
                else:
                    mistake_hits = 0
                    is_explicit_contrast = False

                # Require either explicit confusion/contrast OR high surface lookalike + divergent premises.
                if not (mistake_hits > 0 or is_explicit_contrast):
                    if premise_div <= 0.0:
                        continue

                score = round(
                    0.48 * surface_sim
                    + 0.37 * premise_div
                    + 0.15 * theme_sim
                    + (0.35 * min(3, mistake_hits))
                    + (0.30 if is_explicit_contrast else 0.0),
                    3,
                )
                if score < cutoff:
                    continue
                kept.append(
                    (
                        -score,
                        -mistake_hits,
                        first["id"],
                        second["id"],
                        shared_terms,
                        shared_theme_set,
                        lex_sim,
                        surface_sim,
                        premise_div,
                        is_explicit_contrast,
                    )
                )
                if len(kept) >= 4 * top_n + 256:
                    kept.sort(key=lambda row: row[:4])
                    del kept[top_n:]
                    cutoff = -kept[-1][0]
        kept.sort(key=lambda row: row[:4])
        return kept[:top_n]

    # The pair scan is O(n^2), so remember its result for unchanged inputs. The key covers
    # everything a score depends on (terms, themes, chapter, premises, confusions, contrasts);
    # titles and statuses are only displayed and are re-read from the current graph below.
    fp = hashlib.blake2b(digest_size=16)
    fp.update(repr((_CONFUSABLE_ALGO_VERSION, top_n, float(min_similarity))).encode("utf-8"))
    for info in sorted(nodes_info, key=lambda x: str(x["id"])):
        fp.update(
            repr(
                (
                    str(info["id"]),
                    sorted(info["terms"]),
                    sorted(info["themes"]),
                    info["chapter"],
                    sorted(info["premises"]),
                )
            ).encode("utf-8")
        )
    fp.update(repr(sorted(confused_hits.items())).encode("utf-8"))
    fp.update(repr(sorted(explicit_contrast_pairs)).encode("utf-8"))
    cache_key = fp.hexdigest()
    cached = _CONFUSABLE_CACHE.get(cache_key)
    if cached is None:
        cached = _read_confusable_disk_cache(resolved_kg, cache_key)
        if cached is None:
            cached = _best_pairs()
            _write_confusable_disk_cache(resolved_kg, cache_key, cached)
        if len(_CONFUSABLE_CACHE) >= _CONFUSABLE_CACHE_MAX:
            _CONFUSABLE_CACHE.pop(next(iter(_CONFUSABLE_CACHE)))
        _CONFUSABLE_CACHE[cache_key] = cached

    info_by_id = {info["id"]: info for info in nodes_info}
    pairs_out: list[dict[str, Any]] = []
    for neg_score, neg_hits, id_a, id_b, shared_terms, shared_theme_set, lex_sim, surface_sim, premise_div, is_explicit_contrast in cached:
        first = info_by_id[id_a]
        second = info_by_id[id_b]
        p_a = first["premises"]
        p_b = second["premises"]
        pairs_out.append(
            {
                "nodeA": {
                    "id": first["id"],
                    "title": first["title"],
                    "subject": first["subject"],
                    "chapter": first["chapter"],
                    "status": first["status"],
                },
                "nodeB": {
                    "id": second["id"],
                    "title": second["title"],
                    "subject": second["subject"],
                    "chapter": second["chapter"],
                    "status": second["status"],
                },
                "lexicalSimilarity": round(lex_sim, 3),
                "surfaceSimilarity": round(surface_sim, 3),
                "premiseDivergence": round(premise_div, 3),
                "sharedThemes": sorted(shared_theme_set),
                "sharedTerms": sorted(shared_terms)[:8],
                "distinctPremisesA": sorted(p_a - p_b),
                "distinctPremisesB": sorted(p_b - p_a),
                "confusedMistakeHits": -neg_hits,
                "explicitContrast": is_explicit_contrast,
                "confusableScore": -neg_score,
                "contrastPrompt": (
                    f"Interleaved [A, B, A'] discrimination: contrast '{first['title']}' ({first['id']}) "
                    f"against '{second['title']}' ({second['id']}) — name the validity premise that separates them."
                ),
            }
        )
    return pairs_out


def compute_sunset_plan(
    kg_dir: Path | str | None,
    chapter_slug: str,
    *,
    keep_ratio: float = 0.20,
    target_retention: float = 0.80,
) -> dict[str, Any]:
    """Separate a completed chapter's propositions into ~20% high-centrality `keepAxioms`
    (with `targetRetention: 0.80`) and ~80% `archiveLeaves` (`archived: true`) using
    out-degree, downstream/cross-chapter reach, reverse PageRank, and Brandes betweenness.
    """
    slug_raw = str(chapter_slug or "").strip()
    if not slug_raw:
        raise ValueError("chapter_slug cannot be empty.")
    slug_low = re.sub(r"[^a-z0-9]+", "-", slug_raw.lower()).strip("-") or slug_raw.lower()
    slug_words = slug_low.replace("-", " ")

    resolved_kg, _props_file, _chains_file = resolve_kg_paths(kg_dir)
    G, props, _norm_chains = load_knowledge_graph(resolved_kg)

    # Optionally include propIds listed in chapters/<slug>-tracker.json when present
    tracker_prop_ids: set[str] = set()
    tracker_chapter_title = ""
    tracker_path = resolved_kg.parent / "chapters" / f"{slug_low}-tracker.json"
    if tracker_path.is_file():
        try:
            tr_obj = json.loads(tracker_path.read_text(encoding="utf-8"))
            if isinstance(tr_obj, dict):
                tracker_chapter_title = str(tr_obj.get("title") or "").strip().lower()
                for t in tr_obj.get("topics") or []:
                    if isinstance(t, dict):
                        for pid in t.get("propIds") or []:
                            if str(pid).strip():
                                tracker_prop_ids.add(str(pid).strip())
        except Exception:
            pass

    matched_props: list[dict[str, Any]] = []
    for p in props:
        pid = str(p.get("id") or "").strip()
        if not pid:
            continue
        chap = str(p.get("chapter") or "").strip()
        chap_low = chap.lower()
        chap_slug = re.sub(r"[^a-z0-9]+", "-", chap_low).strip("-")
        pid_low = pid.lower()
        is_match = (
            pid in tracker_prop_ids
            or chap_slug == slug_low
            or slug_low in chap_slug
            or slug_words in chap_low
            or slug_low in pid_low
            or (bool(tracker_chapter_title) and tracker_chapter_title[:12] in chap_low)
        )
        if is_match:
            matched_props.append(p)

    if not matched_props:
        return {
            "ok": True,
            "chapterSlug": slug_low,
            "matchedCount": 0,
            "keepCount": 0,
            "archiveCount": 0,
            "keepRatio": round(float(keep_ratio), 3),
            "targetRetention": round(float(target_retention), 3),
            "keepAxioms": [],
            "archiveLeaves": [],
            "summary": f"No propositions matched chapter '{slug_raw}'.",
        }

    rev_pr = _safe_reverse_pagerank(G)
    betweenness = _graph_betweenness(G)
    eff_retention = round(max(0.50, min(0.95, float(target_retention))), 3)

    scored: list[dict[str, Any]] = []
    for p in matched_props:
        pid = str(p["id"])
        ch_low = str(p.get("chapter") or "").strip().lower()
        out_deg = int(G.out_degree(pid)) if pid in G else 0
        desc = _graph_descendants(G, pid) if pid in G else set()
        cross_ch = sorted(
            {
                str(G.nodes[d].get("chapter") or "").strip()
                for d in desc
                if str(G.nodes[d].get("chapter") or "").strip()
                and str(G.nodes[d].get("chapter") or "").strip().lower() != ch_low
                and str(G.nodes[d].get("chapter") or "").strip().lower() != "unknown"
            }
        )
        pr_val = float(rev_pr.get(pid, 0.0))
        bw_val = float(betweenness.get(pid, 0.0))
        kind_low = str(p.get("kind") or "derivation").strip().lower()
        is_foundational = kind_low in FOUNDATIONAL_KINDS

        scored.append(
            {
                "id": pid,
                "title": str(p.get("title") or pid),
                "subject": str(p.get("subject") or "General"),
                "chapter": str(p.get("chapter") or "General"),
                "kind": kind_low,
                "status": str(p.get("status") or "planned").lower(),
                "outDegree": out_deg,
                "downstreamDescendants": len(desc),
                "crossChapterReach": len(cross_ch),
                "crossChapters": cross_ch,
                "reversePageRank": round(pr_val, 5),
                "betweenness": round(bw_val, 5),
                "isFoundational": is_foundational,
            }
        )

    max_out = max((x["outDegree"] for x in scored), default=0)
    max_desc = max((x["downstreamDescendants"] for x in scored), default=0)
    max_cross = max((x["crossChapterReach"] for x in scored), default=0)
    max_pr = max((x["reversePageRank"] for x in scored), default=0.0)
    max_bw = max((x["betweenness"] for x in scored), default=0.0)

    for x in scored:
        n_out = (x["outDegree"] / max_out) if max_out > 0 else 0.0
        n_desc = (x["downstreamDescendants"] / max_desc) if max_desc > 0 else 0.0
        n_cross = (x["crossChapterReach"] / max_cross) if max_cross > 0 else 0.0
        n_pr = (x["reversePageRank"] / max_pr) if max_pr > 0 else 0.0
        n_bw = (x["betweenness"] / max_bw) if max_bw > 0 else 0.0
        cent = 0.60 * n_pr + 0.40 * n_bw
        score = 0.30 * n_out + 0.25 * n_cross + 0.25 * cent + 0.15 * n_desc + (0.15 if x["isFoundational"] else 0.0)
        x["sunsetScore"] = round(score, 4)

    scored.sort(
        key=lambda x: (
            -x["sunsetScore"],
            -x["crossChapterReach"],
            -x["outDegree"],
            -x["downstreamDescendants"],
            x["id"],
        )
    )

    n_total = len(scored)
    keep_n = max(1, min(n_total, int(round(n_total * max(0.05, min(0.50, float(keep_ratio)))))))
    if n_total >= 4 and keep_n == n_total:
        keep_n = max(1, n_total - 1)

    keep_axioms: list[dict[str, Any]] = []
    archive_leaves: list[dict[str, Any]] = []
    for idx, item in enumerate(scored):
        if idx < keep_n:
            keep_axioms.append(
                {
                    **item,
                    "archived": False,
                    "targetRetention": eff_retention,
                    "reason": (
                        f"Core axiom retained at R={int(round(eff_retention * 100))}% "
                        f"(outDeg={item['outDegree']}, crossCh={item['crossChapterReach']}, score={item['sunsetScore']})."
                    ),
                }
            )
        else:
            archive_leaves.append(
                {
                    **item,
                    "archived": True,
                    "reason": (
                        f"Leaf/derivative archived after semester sunset "
                        f"(outDeg={item['outDegree']}, crossCh={item['crossChapterReach']}, score={item['sunsetScore']})."
                    ),
                }
            )

    actual_ratio = round(len(keep_axioms) / n_total, 3) if n_total else 0.0
    return {
        "ok": True,
        "chapterSlug": slug_low,
        "matchedCount": n_total,
        "keepCount": len(keep_axioms),
        "archiveCount": len(archive_leaves),
        "keepRatio": actual_ratio,
        "targetRetention": eff_retention,
        "keepAxioms": keep_axioms,
        "archiveLeaves": archive_leaves,
        "summary": (
            f"Sunset '{slug_low}': keep {len(keep_axioms)}/{n_total} core axiom(s) at R={int(round(eff_retention * 100))}% "
            f"and archive {len(archive_leaves)} leaf/derivative node(s)."
        ),
    }


def find_rosetta_bridges(
    kg_dir: Path | str | None = None,
    *,
    min_score: float = 0.35,
) -> dict[str, Any]:
    """Report cross-domain structural isomorphism clusters, negative-transfer `breaksAt`
    warnings, and concrete transfer opportunities from mastered twins to unmastered targets."""
    from syntopik.isomorphism import (
        build_bijection_opener,
        discover_isomorphisms,
        load_archetypes,
    )

    resolved_kg_input: Path | str | None = kg_dir
    if _is_graph_obj(kg_dir):
        resolved_kg_input = kg_dir.graph.get("kgDir")
    resolved_kg, props_file, _chains_file = resolve_kg_paths(resolved_kg_input)

    archetypes = load_archetypes()
    arch_by_id = {str(a["id"]): a for a in archetypes}
    props = load_jsonl(props_file)
    props_by_id = {str(p.get("id") or ""): p for p in props if isinstance(p, dict) and p.get("id")}

    disc = discover_isomorphisms(resolved_kg, min_score=min_score)
    chords = list(disc.get("chords") or [])
    matched_archs = list(disc.get("archetypesMatched") or [])

    chords_by_arch: dict[str, list[dict[str, Any]]] = {}
    for ch in chords:
        chords_by_arch.setdefault(str(ch["archetypeId"]), []).append(ch)

    clusters: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    seen_warning_archs: set[str] = set()

    for m_arch in matched_archs:
        aid = str(m_arch["archetypeId"])
        arch_meta = arch_by_id.get(aid) or {}
        c_chords = chords_by_arch.get(aid, [])
        pids = list(m_arch.get("propositionIds") or [])
        cluster_props: list[dict[str, Any]] = []
        chapters_set: set[str] = set()
        for pid in pids:
            p = props_by_id.get(pid) or {}
            chap = str(p.get("chapter") or "General")
            chapters_set.add(chap)
            cluster_props.append(
                {
                    "id": pid,
                    "title": str(p.get("title") or pid),
                    "subject": str(p.get("subject") or "General"),
                    "chapter": chap,
                    "status": str(p.get("status") or "planned").lower(),
                    "bktMastery": p.get("bktMastery"),
                }
            )
        breaks = list(m_arch.get("breaksAt") or arch_meta.get("breaksAt") or [])
        if c_chords and not breaks:
            breaks = list(c_chords[0].get("breaksAt") or [])

        clusters.append(
            {
                "archetypeId": aid,
                "archetypeName": str(m_arch.get("archetypeName") or aid),
                "canonicalEquation": str(m_arch.get("canonicalEquation") or arch_meta.get("canonicalEquation") or ""),
                "invariantStructure": str(arch_meta.get("invariantStructure") or ""),
                "domains": list(m_arch.get("domainsCovered") or []),
                "chapters": sorted(chapters_set),
                "propositionCount": len(cluster_props),
                "chordCount": len(c_chords),
                "propositions": cluster_props,
                "chords": c_chords,
                "breaksAt": breaks,
                "bijectionQuestion": str(arch_meta.get("bijectionQuestion") or ""),
            }
        )

        if breaks and aid not in seen_warning_archs and (c_chords or len(cluster_props) >= 1):
            seen_warning_archs.add(aid)
            warnings.append(
                {
                    "archetypeId": aid,
                    "archetypeName": str(m_arch.get("archetypeName") or aid),
                    "breaksAt": breaks,
                }
            )

    # Transfer opportunities: unmastered (planned/shaky/misconception) targets that have a mastered twin
    transfer_opportunities: list[dict[str, Any]] = []
    for p in props:
        pid = str(p.get("id") or "").strip()
        if not pid or bool(p.get("archived")):
            continue
        status = str(p.get("status") or "planned").strip().lower()
        bkt = p.get("bktMastery")
        already_mastered = status == "solid" or (isinstance(bkt, (int, float)) and float(bkt) >= 0.70 and status != "misconception")
        if already_mastered:
            continue
        opener = build_bijection_opener(resolved_kg, pid, min_score=min_score)
        if opener is not None:
            transfer_opportunities.append(
                {
                    "targetPropId": pid,
                    "targetTitle": str(p.get("title") or pid),
                    "targetChapter": str(p.get("chapter") or "General"),
                    "targetStatus": status,
                    "knownPropId": opener["knownPropId"],
                    "knownTitle": opener["knownTitle"],
                    "knownChapter": opener["knownChapter"],
                    "archetypeId": opener["archetypeId"],
                    "archetypeName": opener["archetypeName"],
                    "variableBijection": opener["variableBijection"],
                    "breaksAt": opener["breaksAt"],
                    "openerPrompt": opener["openerPrompt"],
                }
            )

    transfer_opportunities.sort(key=lambda t: (t["archetypeId"], t["targetPropId"]))

    cross_clusters = [c for c in clusters if c["chordCount"] > 0]
    summary = (
        f"Rosetta found {len(chords)} cross-domain chord(s) across {len(cross_clusters)} active cluster(s), "
        f"{len(warnings)} negative-transfer warning group(s), and {len(transfer_opportunities)} transfer opportunity(ies)."
        if chords or transfer_opportunities
        else f"No cross-domain isomorphism chords yet ({len(clusters)} single-domain archetype match(es) detected)."
    )

    return {
        "ok": True,
        "kgDir": str(resolved_kg),
        "archetypeCount": len(archetypes),
        "chordCount": len(chords),
        "persistedChordCount": int(disc.get("persistedCount") or 0),
        "discoveredChordCount": int(disc.get("discoveredCount") or 0),
        "clusterCount": len(clusters),
        "clusters": clusters,
        "chords": chords,
        "negativeTransferWarnings": warnings,
        "transferOpportunities": transfer_opportunities,
        "summary": summary,
    }


# ---------------------------------------------------------------------------
# CLI reporting & entrypoint
# ---------------------------------------------------------------------------


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

    if "confusables" in payload:
        confs = payload["confusables"]
        lines.append(f"## Lookalike Confusable Pairs ({len(confs)})")
        if not confs:
            lines.append("  None detected — no high-similarity pairs with divergent governing premises.")
        else:
            for idx, cf in enumerate(confs, start=1):
                lines.append(
                    f"  {idx}. ({cf['confusableScore']}) {cf['nodeA']['id']} <-> {cf['nodeB']['id']} "
                    f"(sim={cf['surfaceSimilarity']}, premiseDiv={cf['premiseDivergence']})"
                )
                lines.append(f"     {cf['contrastPrompt']}")
        lines.append("")

    if "sunset" in payload:
        ss = payload["sunset"]
        lines.append(f"## Semester Sunset Plan: `{ss['chapterSlug']}`")
        lines.append(f"  {ss['summary']}")
        if ss["keepAxioms"]:
            lines.append(f"  Keep Axioms ({len(ss['keepAxioms'])} @ R={int(round(ss['targetRetention'] * 100))}%):")
            for item in ss["keepAxioms"]:
                lines.append(
                    f"    - [KEEP] {item['id']} (score={item['sunsetScore']}, out={item['outDegree']}, "
                    f"crossCh={item['crossChapterReach']}): {item['title']}"
                )
        if ss["archiveLeaves"]:
            lines.append(f"  Archive Leaves ({len(ss['archiveLeaves'])}):")
            for item in ss["archiveLeaves"][:15]:
                lines.append(
                    f"    - [ARCHIVE] {item['id']} (score={item['sunsetScore']}, out={item['outDegree']}): {item['title']}"
                )
        lines.append("")

    if "rosetta" in payload:
        ros = payload["rosetta"]
        lines.append(f"## Rosetta Cross-Domain Isomorphism Bridges ({ros['chordCount']} chord(s))")
        lines.append(f"  {ros['summary']}")
        for cl in ros.get("clusters") or []:
            if cl["chordCount"] == 0:
                continue
            lines.append(
                f"  - [{cl['archetypeId']}] {cl['archetypeName']} · {cl['canonicalEquation']}"
            )
            lines.append(f"    Domains: {', '.join(cl['domains'])}")
            for ch in cl["chords"][:6]:
                tag = "persisted" if ch["persisted"] else "discovered"
                lines.append(
                    f"      * ({tag}, score={ch['score']}) {ch['sourceId']} [{ch['sourceChapter']}] "
                    f"<-> {ch['targetId']} [{ch['targetChapter']}]"
                )
            for warn in (cl.get("breaksAt") or [])[:2]:
                lines.append(f"    ! Where analogy breaks: {warn}")
        opps = ros.get("transferOpportunities") or []
        if opps:
            lines.append(f"  Transfer Opportunities ({len(opps)}):")
            for opp in opps[:8]:
                lines.append(
                    f"    - {opp['knownPropId']} (solid) -> {opp['targetPropId']} ({opp['targetStatus']}): {opp['openerPrompt']}"
                )
        lines.append("")

    return "\n".join(lines).rstrip()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Syntopik Knowledge Graph Intelligence: bottlenecks, memorized islands, cycle-safe study paths, synthesis combos, lookalike confusables, Rosetta isomorphism bridges, and semester sunset plans."
    )
    parser.add_argument("--kg-dir", help="Override knowledge-graph directory (or SYNT_KG_DIR / MLH_KG_DIR)")
    parser.add_argument("--bottlenecks", action="store_true", help="Rank shaky/misconception/low-BKT foundation bottlenecks")
    parser.add_argument("--islands", action="store_true", help="Detect zero-in-degree memorized islands (non-axiom/definition)")
    parser.add_argument("--path-to", dest="path_to", metavar="TARGET_ID", help="Compute cycle-safe topological prerequisite sequence to TARGET_ID")
    parser.add_argument("--combos", action="store_true", help="Generate integrative multi-concept synthesis pairs from solid nodes")
    parser.add_argument("--confusables", action="store_true", help="Detect high-similarity lookalike pairs with divergent governing premises")
    parser.add_argument("--rosetta", action="store_true", help="Discover cross-domain structural isomorphism clusters, breaksAt warnings, and transfer opportunities")
    parser.add_argument("--sunset", metavar="SLUG", help="Compute Semester Sunset plan (~20%% core axioms @ R=80%%, ~80%% archived leaves) for chapter SLUG")
    parser.add_argument("--all", action="store_true", help="Run bottlenecks, islands, combos, confusables, and rosetta together")
    parser.add_argument("--top", type=int, default=None, help="Max results (default: 10 for bottlenecks, 8 for combos/confusables)")
    parser.add_argument("--bkt-threshold", type=float, default=0.65, help="Low-BKT threshold for bottleneck detection (default: 0.65)")
    parser.add_argument("--json", action="store_true", help="Emit structured JSON output")
    args = parser.parse_args(argv)

    run_b = args.bottlenecks or args.all
    run_i = args.islands or args.all
    run_c = args.combos or args.all
    run_cf = args.confusables or args.all
    run_r = args.rosetta or args.all
    run_p = bool(args.path_to)
    run_ss = bool(args.sunset)
    if not (run_b or run_i or run_c or run_cf or run_r or run_p or run_ss):
        run_b = run_i = run_c = run_cf = True

    kg_dir, _props_file, _chains_file = resolve_kg_paths(args.kg_dir)
    G, props, norm_chains = load_knowledge_graph(kg_dir)

    payload: dict[str, Any] = {
        "kgDir": str(kg_dir),
        "backend": "networkx" if (nx is not None and isinstance(G, nx.DiGraph)) else "stdlib",
        "summary": {
            "propositionCount": len(props),
            "nodeCount": G.number_of_nodes(),
            "edgeCount": G.number_of_edges(),
            "uniqueChains": len(norm_chains),
            "hyperedgeCount": int(G.graph.get("hyperedgeCount", 0)),
            "isDag": _graph_is_dag(G) if G.number_of_nodes() > 0 else True,
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

    if run_cf:
        top_cf = args.top if args.top is not None else 8
        payload["confusables"] = find_confusables(G, kg_dir=kg_dir, top=top_cf)

    if run_r:
        payload["rosetta"] = find_rosetta_bridges(kg_dir)

    if run_ss:
        payload["sunset"] = compute_sunset_plan(kg_dir, args.sunset)

    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(_render_human_report(payload))

    if run_p and not payload["pathTo"]["found"] and not (run_b or run_i or run_c or run_cf or run_r or run_ss):
        sys.exit(1)


if __name__ == "__main__":
    main()
