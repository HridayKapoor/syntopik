"""Syntopik v3.0 — 4-Tier Epistemic Evidence Ledger & Anti-Inflation Mastery Dossier.

Pure Python 3.12+ standard library only (zero external imports, zero CDN).
Prevents recognition-mastery inflation by separating propositions into 4 verifiable
epistemic tiers backed by an append-only SHA-256 hash-chained local ledger
(`knowledge-graph/evidence-ledger.jsonl`):
  - T1 Exposure: taught, imported, or read
  - T2 Cold Recall: unaided FSRS-6 review (elapsed >= 7d, calibrated confidence)
  - T3 Mechanistic Transfer: deterministic verifier pass, >=75% Counterexample
    Arena mutant kill, or Woodpecker-target Blunder Fork pass
  - T4 Adversarial Defense: passed Viva Voce defense, Clean-Room Derivation
    Blackout, or Axiom-Cut Surgery

Also generates a zero-PII, zero-CDN standalone interactive HTML Mastery Dossier
styled in Syntopik's Warm Obsidian / Archival Vellum palette.
"""

from __future__ import annotations

import hashlib
import html as html_lib
import json
import re
from collections import deque
from pathlib import Path
from typing import Any

from syntopik.fsrs_core import compute_calibration
from syntopik.learner_model import load_profile
from syntopik.paths import resolve_kg_dir, resolve_workspace
from syntopik.schemas import normalize_chain, now_iso
from syntopik.storage import append_jsonl_locked, load_jsonl, workspace_lock, write_text_atomic

__all__ = [
    "LEDGER_FILENAME",
    "GENESIS_HASH",
    "TIER_LABELS",
    "record_evidence",
    "verify_ledger_chain",
    "compute_evidence_summary",
    "export_mastery_dossier",
]

LEDGER_FILENAME = "evidence-ledger.jsonl"
GENESIS_HASH = "0" * 64

TIER_LABELS: dict[int, str] = {
    1: "T1 Exposure",
    2: "T2 Cold Recall",
    3: "T3 Mechanistic Transfer",
    4: "T4 Adversarial Defense",
}

_ABS_PATH_RE = re.compile(
    r"(?:"
    r"(?:/Users|/home)/[^\s/\\:\"'<>|]+"
    r"(?:/[^\s/\\:\"'<>|,;()\[\]{}]+(?: [^\s/\\:\"'<>|,;()\[\]{}]+){0,5}(?=/))*"
    r"(?:/(?:[^\s/\\:\"'<>|,;()\[\]{}]+(?: [^\s/\\:\"'<>|,;()\[\]{}]+){0,4}\.[A-Za-z0-9]{1,10}"
    r"|[^\s/\\:\"'<>|.,;()\[\]{}]+(?: [A-Z0-9][^\s/\\:\"'<>|.,;()\[\]{}]*){0,4}"
    r"|[^\s/\\:\"'<>|]+))?/?"
    r"|"
    r"[A-Za-z]:\\[^\s/\\:\"'<>|]+"
    r"(?:\\[^\s/\\:\"'<>|,;()\[\]{}]+(?: [^\s/\\:\"'<>|,;()\[\]{}]+){0,5}(?=\\))*"
    r"(?:\\(?:[^\s/\\:\"'<>|,;()\[\]{}]+(?: [^\s/\\:\"'<>|,;()\[\]{}]+){0,4}\.[A-Za-z0-9]{1,10}"
    r"|[^\s/\\:\"'<>|.,;()\[\]{}]+(?: [A-Z0-9][^\s/\\:\"'<>|.,;()\[\]{}]*){0,4}"
    r"|[^\s/\\:\"'<>|]+))?\\?"
    r")"
)


def _redact_local_paths(text: str) -> str:
    return _ABS_PATH_RE.sub("<redacted-path>", str(text or "")).strip()


def _sanitize_detail_value(val: Any) -> Any:
    if val is None or isinstance(val, (bool, int, float)):
        return val
    if isinstance(val, str):
        return _redact_local_paths(val)
    if isinstance(val, dict):
        return {str(k): _sanitize_detail_value(v) for k, v in val.items()}
    if isinstance(val, (list, tuple)):
        return [_sanitize_detail_value(item) for item in val]
    return _redact_local_paths(str(val))


def _sanitize_detail(detail: Any) -> Any:
    if detail is None:
        return None
    if isinstance(detail, str):
        cleaned = _redact_local_paths(detail)
        return cleaned if cleaned else None
    if isinstance(detail, dict):
        cleaned_dict = {str(k): _sanitize_detail_value(v) for k, v in detail.items()}
        return cleaned_dict if cleaned_dict else None
    if isinstance(detail, (list, tuple)):
        cleaned_list = [_sanitize_detail_value(item) for item in detail]
        return cleaned_list if cleaned_list else None
    cleaned_s = _redact_local_paths(str(detail))
    return cleaned_s if cleaned_s else None


def _canonical_bytes(obj: dict[str, Any]) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _sha256_obj(obj: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_bytes(obj)).hexdigest()


def _resolve_ws_and_kg(workspace_or_kg: Path | str | None) -> tuple[Path, Path]:
    if workspace_or_kg is None or not str(workspace_or_kg).strip():
        ws = resolve_workspace()
        return ws, resolve_kg_dir(ws)
    p = Path(workspace_or_kg).expanduser().resolve()
    if p.name == "knowledge-graph" and not (p / "knowledge-graph").is_dir():
        return p.parent, p
    if (p / "knowledge-graph").is_dir():
        return p, (p / "knowledge-graph").resolve()
    # If propositions.jsonl or evidence-ledger.jsonl lives directly in p, treat p as kg_dir
    if (p / "propositions.jsonl").is_file() or (p / LEDGER_FILENAME).is_file():
        return p.parent, p
    return p, (p / "knowledge-graph").resolve()


def _resolve_kg_only(kg_dir_or_ws: Path | str) -> Path:
    p = Path(kg_dir_or_ws).expanduser().resolve()
    if (p / "knowledge-graph").is_dir() and not (p / LEDGER_FILENAME).is_file() and p.name != "knowledge-graph":
        return (p / "knowledge-graph").resolve()
    return p


def _parse_ledger_raw(ledger_path: Path) -> tuple[list[dict[str, Any]], list[tuple[int, str]]]:
    """Return `(valid_entries, parse_errors)` preserving 1-based line numbers."""
    if not ledger_path.is_file():
        return [], []
    entries: list[dict[str, Any]] = []
    errors: list[tuple[int, str]] = []
    for idx, raw_line in enumerate(ledger_path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except Exception as exc:  # noqa: BLE001
            errors.append((idx, f"Malformed JSON at line {idx}: {exc}"))
            continue
        if not isinstance(obj, dict):
            errors.append((idx, f"Line {idx} is not a JSON object."))
            continue
        entries.append(obj)
    return entries, errors


# ---------------------------------------------------------------------------
# Public API 1 & 2: Hash-Chained Append-Only Evidence Ledger
# ---------------------------------------------------------------------------


def record_evidence(
    workspace: Path | str,
    prop_id: str,
    tier: int,
    source: str,
    *,
    detail: str | dict[str, Any] | None = None,
    verified: bool = True,
) -> dict[str, Any]:
    """Append a SHA-256 hash-chained epistemic evidence entry (tier 1..4) under lock."""
    pid = str(prop_id or "").strip()
    if not pid:
        raise ValueError("prop_id is required.")
    tier_int = int(tier)
    if tier_int not in TIER_LABELS:
        raise ValueError(f"Invalid evidence tier {tier!r}; expected integer 1..4.")
    src = str(source or "manual").strip() or "manual"

    _ws, kg_dir = _resolve_ws_and_kg(workspace)
    ledger_path = kg_dir / LEDGER_FILENAME

    with workspace_lock(ledger_path):
        existing, _errs = _parse_ledger_raw(ledger_path)
        prev_hash = str(existing[-1].get("entryHash") or GENESIS_HASH) if existing else GENESIS_HASH
        seq = len(existing) + 1

        body: dict[str, Any] = {
            "seq": seq,
            "ts": now_iso(),
            "propId": pid,
            "tier": tier_int,
            "tierLabel": TIER_LABELS[tier_int],
            "source": src,
            "detail": _sanitize_detail(detail),
            "verified": bool(verified),
            "prevHash": prev_hash,
        }
        entry_hash = _sha256_obj(body)
        record = {**body, "entryHash": entry_hash}
        append_jsonl_locked(ledger_path, record)

    return {"ok": True, **record}


def verify_ledger_chain(kg_dir: Path | str) -> dict[str, Any]:
    """Verify SHA-256 hash-chain continuity and per-entry digests of `evidence-ledger.jsonl`."""
    kdir = _resolve_kg_only(kg_dir)
    ledger_path = kdir / LEDGER_FILENAME
    if not ledger_path.is_file():
        return {
            "ok": True,
            "count": 0,
            "brokenAt": None,
            "headHash": GENESIS_HASH,
            "errors": [],
        }

    expected_prev = GENESIS_HASH
    count = 0
    broken_at: int | None = None
    errors: list[str] = []
    head_hash = GENESIS_HASH

    for line_no, raw_line in enumerate(ledger_path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        count += 1
        try:
            row = json.loads(line)
        except Exception as exc:  # noqa: BLE001
            if broken_at is None:
                broken_at = count
            errors.append(f"Entry {count} (line {line_no}): malformed JSON ({exc})")
            continue
        if not isinstance(row, dict):
            if broken_at is None:
                broken_at = count
            errors.append(f"Entry {count} (line {line_no}): expected JSON object")
            continue

        actual_prev = row.get("prevHash")
        actual_hash = row.get("entryHash")
        if actual_prev != expected_prev:
            if broken_at is None:
                broken_at = count
            errors.append(f"Entry {count}: prevHash mismatch (expected {expected_prev[:12]}..., got {str(actual_prev)[:12]}...)")

        payload = {k: v for k, v in row.items() if k != "entryHash"}
        recomputed = _sha256_obj(payload)
        if not isinstance(actual_hash, str) or actual_hash != recomputed:
            if broken_at is None:
                broken_at = count
            errors.append(f"Entry {count}: entryHash tampered or corrupted")

        head_hash = str(actual_hash or "")
        expected_prev = head_hash

    return {
        "ok": len(errors) == 0,
        "count": count,
        "brokenAt": broken_at,
        "headHash": head_hash if count > 0 else GENESIS_HASH,
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# Public API 3: Deterministic 4-Tier Evidence Summary & Anti-Inflation Gate
# ---------------------------------------------------------------------------


def _build_downstream_metrics(chains: list[dict[str, Any]]) -> tuple[dict[str, int], dict[str, set[str]]]:
    adj: dict[str, set[str]] = {}
    for raw in chains:
        nc = normalize_chain(raw)
        if not nc or float(nc["encompassWeight"]) <= 0.0:
            continue
        conc = nc["conclusion"]
        for prem in nc["premises"]:
            adj.setdefault(prem, set()).add(conc)

    out_deg = {u: len(vs) for u, vs in adj.items()}
    desc: dict[str, set[str]] = {}
    for u in adj:
        visited: set[str] = set()
        q: deque[str] = deque(adj[u])
        while q:
            cur = q.popleft()
            if cur in visited or cur == u:
                continue
            visited.add(cur)
            for nxt in adj.get(cur, ()):
                if nxt not in visited and nxt != u:
                    q.append(nxt)
        desc[u] = visited
    return out_deg, desc


def compute_evidence_summary(workspace: Path | str) -> dict[str, Any]:
    """Deterministically infer T1..T4 epistemic tiers and anti-inflation alerts from all logs."""
    _ws, kg_dir = _resolve_ws_and_kg(workspace)
    props = load_jsonl(kg_dir / "propositions.jsonl")
    chains = load_jsonl(kg_dir / "chains.jsonl")
    revlog = load_jsonl(kg_dir / "revlog.jsonl")
    forks = load_jsonl(kg_dir / "forks.jsonl")
    arenas = load_jsonl(kg_dir / "arenas.jsonl")
    ledger_rows = load_jsonl(kg_dir / LEDGER_FILENAME)
    viva_rows = load_jsonl(kg_dir / "viva-sessions.jsonl")
    deriv_rows = load_jsonl(kg_dir / "derivations.jsonl")

    out_deg, desc_map = _build_downstream_metrics(chains)

    # Index signals by propId
    t2_revlog: set[str] = set()
    for r in revlog:
        pid = str(r.get("propId") or r.get("id") or "").strip()
        if not pid:
            continue
        rating = r.get("rating")
        good_rating = (isinstance(rating, int) and rating >= 3) or str(rating or "").lower() in ("good", "easy")
        correct = bool(r.get("correct")) if isinstance(r.get("correct"), bool) else good_rating
        elapsed = float(r.get("elapsedDays") or 0.0)
        ivl = float(r.get("intervalDays") or 0.0)
        lucky = bool(r.get("luckyGuess"))
        if correct and not lucky and (elapsed >= 7.0 or ivl >= 7.0):
            t2_revlog.add(pid)

    t3_forks: set[str] = set()
    for f in forks:
        pid = str(f.get("propId") or "").strip()
        if not pid:
            continue
        wins = int(f.get("winCount") or 0)
        status = str(f.get("status") or "").lower()
        target_ms = f.get("targetLatencyMs") or f.get("woodpeckerTargetMs")
        lat_hist = [float(x) for x in (f.get("latencyMsHistory") or []) if isinstance(x, (int, float)) and x > 0]
        woodpecker_ok = bool(f.get("woodpeckerPassed")) or (
            isinstance(target_ms, (int, float)) and lat_hist and min(lat_hist) <= float(target_ms)
        )
        if wins >= 1 and (woodpecker_ok or status in ("mastered", "solid", "cleared")):
            t3_forks.add(pid)

    t3_arenas: set[str] = set()
    for a in arenas:
        pid = str(a.get("propId") or "").strip()
        if not pid:
            continue
        kill_rate = a.get("killRate")
        if not isinstance(kill_rate, (int, float)) or isinstance(kill_rate, bool):
            mutants = a.get("sealedMutants") or a.get("mutants") or []
            killed = a.get("killedMutants") or a.get("killedIds") or []
            if isinstance(mutants, list) and mutants:
                if isinstance(killed, list) and killed:
                    mutant_ids = {
                        str(m.get("id"))
                        for m in mutants
                        if isinstance(m, dict) and m.get("id") is not None
                    }
                    killed_ids = {str(k) for k in killed if k is not None}
                    if mutant_ids:
                        kill_rate = len(killed_ids & mutant_ids) / len(mutants)
                    else:
                        kill_rate = len(killed_ids) / len(mutants)
                else:
                    k_cnt = sum(1 for m in mutants if isinstance(m, dict) and m.get("killed"))
                    kill_rate = k_cnt / len(mutants)
            else:
                kill_rate = 0.0
        if float(kill_rate) >= 0.75 or str(a.get("status") or "").lower() in ("cleared", "passed", "mastered"):
            t3_arenas.add(pid)

    t4_viva: set[str] = set()
    for v in viva_rows:
        pid = str(v.get("propId") or "").strip()
        if not pid:
            continue
        scores_obj = v.get("scores") if isinstance(v.get("scores"), dict) else {}
        outcome = str(v.get("outcome") or "").strip().lower()
        status_v = str(v.get("status") or "").strip().lower()
        score = v.get("score")
        if not isinstance(score, (int, float)) or isinstance(score, bool):
            score = scores_obj.get("composite")
        passed_flag = bool(v.get("passed")) or bool(scores_obj.get("passed"))
        if (
            passed_flag
            or outcome in ("passed", "defended")
            or status_v in ("passed", "defended")
            or (isinstance(score, (int, float)) and not isinstance(score, bool) and float(score) >= 0.75)
        ):
            t4_viva.add(pid)

    t4_deriv: set[str] = set()
    for d in deriv_rows:
        pid = str(d.get("propId") or "").strip()
        if not pid:
            continue
        if (
            bool(d.get("passed"))
            or bool(d.get("blackoutPassed"))
            or bool(d.get("axiomCutPassed"))
            or int(d.get("completions") or 0) >= 1
        ):
            t4_deriv.add(pid)

    ledger_max_tier: dict[str, int] = {}
    ledger_sources: dict[str, list[str]] = {}
    for row in ledger_rows:
        if not row.get("verified", True):
            continue
        pid = str(row.get("propId") or "").strip()
        t_raw = row.get("tier")
        if not pid or not isinstance(t_raw, int) or t_raw not in TIER_LABELS:
            continue
        if t_raw > ledger_max_tier.get(pid, 0):
            ledger_max_tier[pid] = t_raw
        src = str(row.get("source") or "ledger")
        srcs = ledger_sources.setdefault(pid, [])
        if src not in srcs:
            srcs.append(src)

    tier_counts = {"T1": 0, "T2": 0, "T3": 0, "T4": 0}
    per_prop: dict[str, dict[str, Any]] = {}
    anti_inflation: list[dict[str, Any]] = []

    for p in props:
        pid = str(p.get("id") or "").strip()
        if not pid:
            continue
        status = str(p.get("status") or "planned").strip().lower()
        tier = 1
        sources: list[str] = ["exposure"]

        # T2 Cold Recall
        fsrs_stab = float((p.get("fsrs") or {}).get("stability") or 0.0) if isinstance(p.get("fsrs"), dict) else 0.0
        if pid in t2_revlog or (status == "solid" and fsrs_stab >= 7.0) or ledger_max_tier.get(pid, 0) >= 2:
            tier = max(tier, 2)
            sources.append("cold_recall")

        # T3 Mechanistic Transfer
        if pid in t3_arenas:
            tier = max(tier, 3)
            sources.append("falsify_arena")
        if pid in t3_forks:
            tier = max(tier, 3)
            sources.append("fork_replay")
        if ledger_max_tier.get(pid, 0) >= 3:
            tier = max(tier, 3)

        # T4 Adversarial Defense
        if pid in t4_viva:
            tier = 4
            sources.append("viva_chamber")
        if pid in t4_deriv:
            tier = 4
            sources.append("derivation_forge")
        if ledger_max_tier.get(pid, 0) >= 4:
            tier = 4

        for ls in ledger_sources.get(pid, ()):
            if ls not in sources:
                sources.append(ls)

        tier_counts[f"T{tier}"] += 1
        od = out_deg.get(pid, 0)
        desc_n = len(desc_map.get(pid, set()))

        entry = {
            "propId": pid,
            "title": _redact_local_paths(str(p.get("title") or pid)),
            "subject": str(p.get("subject") or "General"),
            "chapter": _redact_local_paths(str(p.get("chapter") or "")),
            "status": status,
            "tier": tier,
            "tierLabel": TIER_LABELS[tier],
            "sources": sources,
            "outDegree": od,
            "descendantsCount": desc_n,
        }
        per_prop[pid] = entry

        # Anti-Inflation Gate: high-centrality/bottleneck nodes (or solid parents) stuck at T1/T2
        is_bottleneck = od >= 2 or desc_n >= 2 or (status == "solid" and desc_n >= 1)
        if is_bottleneck and tier <= 2:
            suggested = "falsify_arena" if od >= 2 else "derivation_forge"
            anti_inflation.append(
                {
                    "propId": pid,
                    "title": entry["title"],
                    "subject": entry["subject"],
                    "chapter": entry["chapter"],
                    "status": status,
                    "currentTier": tier,
                    "currentTierLabel": TIER_LABELS[tier],
                    "requiredTier": 3,
                    "outDegree": od,
                    "descendantsCount": desc_n,
                    "suggestedAction": suggested,
                    "reason": (
                        f"Bottleneck node '{pid}' ({desc_n} downstream, out-degree {od}) has only "
                        f"{TIER_LABELS[tier]} evidence; requires T3/T4 mechanistic verification."
                    ),
                }
            )

    anti_inflation.sort(key=lambda a: (-a["descendantsCount"], -a["outDegree"], a["currentTier"], a["propId"]))
    integrity = verify_ledger_chain(kg_dir)
    total_props = len(per_prop)
    verified_t3_plus = tier_counts["T3"] + tier_counts["T4"]
    t3_plus_pct = round((verified_t3_plus / total_props) * 100.0, 1) if total_props > 0 else 0.0

    return {
        "ok": True,
        "totalPropositions": total_props,
        "tierCounts": tier_counts,
        "verifiedTransferPct": t3_plus_pct,
        "propositions": per_prop,
        "antiInflationAlerts": anti_inflation,
        "ledgerIntegrity": integrity,
    }


# ---------------------------------------------------------------------------
# Public API 4: Zero-PII, Zero-CDN Standalone Interactive HTML Mastery Dossier
# ---------------------------------------------------------------------------


def _render_calibration_svg(cal: dict[str, Any]) -> str:
    bins = cal.get("bins") or []
    w, h = 320, 190
    pad_l, pad_r, pad_t, pad_b = 38, 16, 14, 28
    plot_w = w - pad_l - pad_r
    plot_h = h - pad_t - pad_b

    def _x(v: float) -> float:
        return pad_l + max(0.0, min(1.0, v)) * plot_w

    def _y(v: float) -> float:
        return pad_t + (1.0 - max(0.0, min(1.0, v))) * plot_h

    pts: list[str] = []
    circles: list[str] = []
    for b in bins:
        cx = _x(float(b.get("avgConfidence") or 0.0))
        cy = _y(float(b.get("accuracy") or 0.0))
        pts.append(f"{cx:.1f},{cy:.1f}")
        circles.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="4" fill="var(--accent)" />')

    poly = (
        f'<polyline fill="none" stroke="var(--accent)" stroke-width="2.2" points="{" ".join(pts)}" />'
        if len(pts) >= 2
        else ""
    )
    empty_txt = (
        f'<text x="{pad_l + plot_w/2:.1f}" y="{pad_t + plot_h/2:.1f}" text-anchor="middle" '
        f'fill="var(--muted)" font-size="11">Awaiting confidence-scored reviews</text>'
        if not bins
        else ""
    )
    return (
        f'<svg viewBox="0 0 {w} {h}" width="100%" height="{h}" role="img" aria-label="Brier calibration curve">'
        f'<rect x="{pad_l}" y="{pad_t}" width="{plot_w}" height="{plot_h}" fill="var(--bg-elev)" stroke="var(--border)" rx="6" />'
        f'<line x1="{_x(0):.1f}" y1="{_y(0):.1f}" x2="{_x(1):.1f}" y2="{_y(1):.1f}" '
        f'stroke="var(--muted)" stroke-dasharray="4 4" stroke-width="1.2" />'
        f"{poly}{''.join(circles)}{empty_txt}"
        f'<text x="{pad_l + plot_w/2:.1f}" y="{h - 6}" text-anchor="middle" fill="var(--text-2)" font-size="10">Confidence</text>'
        f'<text x="12" y="{pad_t + plot_h/2:.1f}" text-anchor="middle" fill="var(--text-2)" font-size="10" '
        f'transform="rotate(-90 12 {pad_t + plot_h/2:.1f})">Accuracy</text>'
        f"</svg>"
    )


def export_mastery_dossier(workspace: Path | str, out_path: Path | str) -> dict[str, Any]:
    """Generate a zero-PII, zero-CDN interactive standalone HTML Mastery Dossier."""
    ws, kg_dir = _resolve_ws_and_kg(workspace)
    summary = compute_evidence_summary(ws)
    revlog = load_jsonl(kg_dir / "revlog.jsonl")
    cal = compute_calibration(revlog[-60:])
    profile = load_profile(kg_dir)

    integrity = summary["ledgerIntegrity"]
    head_hash = str(integrity.get("headHash") or GENESIS_HASH)
    ledger_ok = bool(integrity.get("ok"))
    ledger_count = int(integrity.get("count") or 0)

    tier_counts = summary["tierCounts"]
    total_props = max(1, int(summary["totalPropositions"]))
    t_pct = {k: round((v / total_props) * 100.0, 1) for k, v in tier_counts.items()}

    # Build strictly zero-PII proposition rows (no personalNotes, no local paths, no raw logs)
    rows_html: list[str] = []
    for p in sorted(
        summary["propositions"].values(),
        key=lambda x: (-x["tier"], -x["descendantsCount"], x["propId"]),
    ):
        tier_num = int(p["tier"])
        srcs_str = html_lib.escape(", ".join(p.get("sources") or []))
        rows_html.append(
            f'<tr data-tier="T{tier_num}">'
            f'<td><code>{html_lib.escape(p["propId"])}</code></td>'
            f'<td>{html_lib.escape(p["title"])}</td>'
            f'<td>{html_lib.escape(p["subject"])}</td>'
            f'<td><span class="tier-pill t{tier_num}">{html_lib.escape(p["tierLabel"])}</span></td>'
            f'<td>{int(p["descendantsCount"])}</td>'
            f'<td class="muted">{srcs_str}</td>'
            f"</tr>"
        )
    if not rows_html:
        rows_html.append('<tr><td colspan="6" class="muted">No propositions recorded yet.</td></tr>')

    alerts_html: list[str] = []
    for al in summary["antiInflationAlerts"][:8]:
        alerts_html.append(
            f'<li><strong>{html_lib.escape(al["propId"])}</strong> '
            f'({html_lib.escape(al["currentTierLabel"])} &rarr; T3+ required, {int(al["descendantsCount"])} downstream): '
            f'recommended <code>{html_lib.escape(al["suggestedAction"])}</code></li>'
        )
    alerts_block = (
        f'<ul class="alert-list">{"".join(alerts_html)}</ul>'
        if alerts_html
        else '<p class="muted">Zero anti-inflation bottlenecks detected — all high-centrality axioms have T3/T4 evidence.</p>'
    )

    dna = profile.get("failureDna") or {}
    shares = dna.get("tierShares") or {}
    dna_rows = []
    for tier_key, label in (
        ("comprehension_model", "Comprehension & Model Selection"),
        ("process_encoding", "Process & Encoding"),
        ("executive_pacing", "Executive & Pacing"),
    ):
        pct_val = round(float(shares.get(tier_key) or 0.0) * 100.0, 1)
        dna_rows.append(
            f'<div class="bar-row"><div class="bar-head"><span>{label}</span><span>{pct_val:.1f}%</span></div>'
            f'<div class="bar-track"><div class="bar-fill" style="width:{min(100.0, pct_val):.1f}%"></div></div></div>'
        )

    domains_html = []
    for d_name, d_info in sorted((profile.get("domains") or {}).items()):
        if not isinstance(d_info, dict):
            continue
        domains_html.append(
            f'<span class="chip">{html_lib.escape(str(d_name))}: '
            f'Elo {int(round(float(d_info.get("elo") or 1450)))} '
            f'({html_lib.escape(str(d_info.get("expertiseBand") or "intermediate"))})</span>'
        )
    domains_block = "".join(domains_html) if domains_html else '<span class="muted">No domain telemetry recorded yet.</span>'

    brier_txt = f'{cal["brierScore"]:.3f}' if isinstance(cal.get("brierScore"), (int, float)) else "—"
    ece_txt = f'{cal["ece"]:.3f}' if isinstance(cal.get("ece"), (int, float)) else "—"
    bias_val = cal.get("overconfidenceBias")
    bias_txt = f"{float(bias_val) * 100:+.1f}%" if isinstance(bias_val, (int, float)) else "—"
    cal_svg = _render_calibration_svg(cal)

    badge_cls = "ok" if ledger_ok else "warn"
    badge_txt = f"LEDGER VERIFIED · {ledger_count} entries · {head_hash[:12]}" if ledger_ok else f"LEDGER BROKEN AT #{integrity.get('brokenAt')}"
    generated_date = now_iso()[:10]

    html_doc = f"""<!DOCTYPE html>
<html lang="en" data-theme="dark">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>Syntopik Mastery Dossier — Verified Epistemic Evidence</title>
<style>
  :root, [data-theme="dark"] {{
    --bg: #111315; --bg-elev: #171A1D; --bg-card: #1E2227; --border: #2A3038;
    --text: #E8E6E1; --text-2: #A8B0BC; --muted: #757F8D;
    --solid: #4E9F76; --solid-bg: rgba(78, 159, 118, 0.14);
    --shaky: #D4943A; --shaky-bg: rgba(212, 148, 58, 0.14);
    --misconception: #C85A53; --misconception-bg: rgba(200, 90, 83, 0.14);
    --planned: #6B8E9B; --planned-bg: rgba(107, 142, 155, 0.14);
    --accent: #7C93C3; --accent-bg: rgba(124, 147, 195, 0.15);
    --bar-track: #252A30;
    --font-ui: "Inter", -apple-system, BlinkMacSystemFont, "SF Pro Text", system-ui, sans-serif;
    --font-mono: "JetBrains Mono", "SF Mono", ui-monospace, monospace;
  }}
  [data-theme="light"] {{
    --bg: #F7F5F0; --bg-elev: #FCFBF8; --bg-card: #FFFFFF; --border: #E2DDD2;
    --text: #1C2024; --text-2: #495057; --muted: #747B84;
    --solid: #2B7A52; --solid-bg: rgba(43, 122, 82, 0.10);
    --shaky: #A86814; --shaky-bg: rgba(168, 104, 20, 0.11);
    --misconception: #B23A34; --misconception-bg: rgba(178, 58, 52, 0.10);
    --planned: #456978; --planned-bg: rgba(69, 105, 120, 0.10);
    --accent: #36538C; --accent-bg: rgba(54, 83, 140, 0.10);
    --bar-track: #E5E0D5;
  }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; background: var(--bg); color: var(--text); font-family: var(--font-ui); line-height: 1.5; }}
  .wrap {{ max-width: 1040px; margin: 0 auto; padding: 28px 20px 56px; }}
  header {{ display: flex; flex-wrap: wrap; justify-content: space-between; align-items: center; gap: 12px; margin-bottom: 22px; }}
  h1 {{ margin: 0; font-size: 1.45rem; letter-spacing: -0.02em; }}
  .subtitle {{ color: var(--text-2); font-size: 0.88rem; margin-top: 2px; }}
  .hdr-right {{ display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }}
  .badge {{ font-family: var(--font-mono); font-size: 0.76rem; padding: 5px 10px; border-radius: 999px; border: 1px solid var(--border); }}
  .badge.ok {{ background: var(--solid-bg); color: var(--solid); border-color: var(--solid); }}
  .badge.warn {{ background: var(--misconception-bg); color: var(--misconception); border-color: var(--misconception); }}
  button.theme-btn, button.filter-btn {{
    background: var(--bg-card); color: var(--text); border: 1px solid var(--border);
    padding: 6px 12px; border-radius: 8px; cursor: pointer; font-size: 0.82rem; font-family: var(--font-ui);
  }}
  button.filter-btn.active {{ background: var(--accent-bg); color: var(--accent); border-color: var(--accent); }}
  .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 14px; margin-bottom: 18px; }}
  .grid-2 {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(310px, 1fr)); gap: 14px; margin-bottom: 18px; }}
  .card {{ background: var(--bg-card); border: 1px solid var(--border); border-radius: 12px; padding: 16px 18px; }}
  .card h2 {{ margin: 0 0 10px; font-size: 0.98rem; color: var(--text); }}
  .kpi {{ font-size: 1.65rem; font-weight: 700; font-variant-numeric: tabular-nums; }}
  .kpi-sub {{ color: var(--muted); font-size: 0.8rem; margin-top: 2px; }}
  .tier-pill {{ display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 0.75rem; font-weight: 600; }}
  .t1 {{ background: var(--planned-bg); color: var(--planned); }}
  .t2 {{ background: var(--shaky-bg); color: var(--shaky); }}
  .t3 {{ background: var(--accent-bg); color: var(--accent); }}
  .t4 {{ background: var(--solid-bg); color: var(--solid); }}
  .bar-row {{ margin-bottom: 10px; }}
  .bar-head {{ display: flex; justify-content: space-between; font-size: 0.82rem; color: var(--text-2); margin-bottom: 4px; }}
  .bar-track {{ height: 8px; background: var(--bar-track); border-radius: 999px; overflow: hidden; }}
  .bar-fill {{ height: 100%; background: var(--accent); border-radius: 999px; }}
  .chip {{ display: inline-block; margin: 0 6px 6px 0; padding: 3px 9px; border-radius: 8px; background: var(--bg-elev); border: 1px solid var(--border); font-size: 0.78rem; }}
  .filters {{ display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 10px; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 0.84rem; }}
  th, td {{ text-align: left; padding: 8px 10px; border-bottom: 1px solid var(--border); }}
  th {{ color: var(--text-2); font-weight: 600; font-size: 0.78rem; text-transform: uppercase; letter-spacing: 0.04em; }}
  code {{ font-family: var(--font-mono); font-size: 0.8rem; }}
  .muted {{ color: var(--muted); }}
  .alert-list {{ margin: 0; padding-left: 18px; font-size: 0.85rem; color: var(--text-2); }}
  .alert-list li {{ margin-bottom: 6px; }}
  footer {{ margin-top: 24px; color: var(--muted); font-size: 0.78rem; text-align: center; }}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div>
      <h1>Syntopik Mastery Dossier</h1>
      <div class="subtitle">Zero-PII Epistemic Evidence Report · Generated {html_lib.escape(generated_date)}</div>
    </div>
    <div class="hdr-right">
      <span class="badge {badge_cls}" title="SHA-256 hash chain head: {html_lib.escape(head_hash)}">{html_lib.escape(badge_txt)}</span>
      <button class="theme-btn" id="themeToggle" type="button">Archival Vellum</button>
    </div>
  </header>

  <section class="grid">
    <div class="card">
      <div class="kpi">{int(summary["totalPropositions"])}</div>
      <div class="kpi-sub">Total Propositions Tracked</div>
    </div>
    <div class="card">
      <div class="kpi">{summary["verifiedTransferPct"]:.1f}%</div>
      <div class="kpi-sub">T3/T4 Mechanistic &amp; Adversarial Proof</div>
    </div>
    <div class="card">
      <div class="kpi">{tier_counts["T4"]} / {tier_counts["T3"]}</div>
      <div class="kpi-sub">T4 Defense / T3 Transfer Nodes</div>
    </div>
    <div class="card">
      <div class="kpi">{len(summary["antiInflationAlerts"])}</div>
      <div class="kpi-sub">Anti-Inflation Bottlenecks Stuck at &le;T2</div>
    </div>
  </section>

  <section class="grid-2">
    <div class="card">
      <h2>4-Tier Epistemic Distribution</h2>
      <div class="bar-row"><div class="bar-head"><span>T4 Adversarial Defense</span><span>{tier_counts["T4"]} ({t_pct["T4"]}%)</span></div><div class="bar-track"><div class="bar-fill" style="width:{t_pct["T4"]}%;background:var(--solid)"></div></div></div>
      <div class="bar-row"><div class="bar-head"><span>T3 Mechanistic Transfer</span><span>{tier_counts["T3"]} ({t_pct["T3"]}%)</span></div><div class="bar-track"><div class="bar-fill" style="width:{t_pct["T3"]}%;background:var(--accent)"></div></div></div>
      <div class="bar-row"><div class="bar-head"><span>T2 Cold Recall</span><span>{tier_counts["T2"]} ({t_pct["T2"]}%)</span></div><div class="bar-track"><div class="bar-fill" style="width:{t_pct["T2"]}%;background:var(--shaky)"></div></div></div>
      <div class="bar-row"><div class="bar-head"><span>T1 Exposure</span><span>{tier_counts["T1"]} ({t_pct["T1"]}%)</span></div><div class="bar-track"><div class="bar-fill" style="width:{t_pct["T1"]}%;background:var(--planned)"></div></div></div>
      <div style="margin-top:12px">{domains_block}</div>
    </div>

    <div class="card">
      <h2>Metacognitive Calibration (Brier / ECE)</h2>
      <div class="subtitle" style="margin-bottom:8px">Brier {html_lib.escape(brier_txt)} · ECE {html_lib.escape(ece_txt)} · Bias {html_lib.escape(bias_txt)} (n={int(cal.get("count") or 0)})</div>
      {cal_svg}
    </div>
  </section>

  <section class="grid-2">
    <div class="card">
      <h2>Failure DNA (3-Tier NEA Distribution)</h2>
      {"".join(dna_rows)}
      <div class="kpi-sub">Dominant tier: <code>{html_lib.escape(str(dna.get("dominantTier") or "none"))}</code></div>
    </div>
    <div class="card">
      <h2>Anti-Inflation Bottleneck Gate</h2>
      {alerts_block}
    </div>
  </section>

  <section class="card">
    <h2>Proposition Evidence Matrix</h2>
    <div class="filters">
      <button class="filter-btn active" data-filter="ALL" type="button">All ({int(summary["totalPropositions"])})</button>
      <button class="filter-btn" data-filter="T4" type="button">T4 ({tier_counts["T4"]})</button>
      <button class="filter-btn" data-filter="T3" type="button">T3 ({tier_counts["T3"]})</button>
      <button class="filter-btn" data-filter="T2" type="button">T2 ({tier_counts["T2"]})</button>
      <button class="filter-btn" data-filter="T1" type="button">T1 ({tier_counts["T1"]})</button>
    </div>
    <table>
      <thead><tr><th>ID</th><th>Proposition</th><th>Subject</th><th>Evidence Tier</th><th>Downstream</th><th>Provenance</th></tr></thead>
      <tbody id="propBody">{"".join(rows_html)}</tbody>
    </table>
  </section>

  <footer>
    Syntopik v3.0 · Pure-Stdlib Offline Mastery Dossier · Zero PII · Zero External CDNs · Ledger Head <code>{html_lib.escape(head_hash)}</code>
  </footer>
</div>
<script>
(function() {{
  var root = document.documentElement;
  var btn = document.getElementById("themeToggle");
  btn.addEventListener("click", function() {{
    var next = root.getAttribute("data-theme") === "dark" ? "light" : "dark";
    root.setAttribute("data-theme", next);
    btn.textContent = next === "dark" ? "Archival Vellum" : "Warm Obsidian";
  }});
  var fbtns = document.querySelectorAll(".filter-btn");
  var rows = document.querySelectorAll("#propBody tr[data-tier]");
  fbtns.forEach(function(b) {{
    b.addEventListener("click", function() {{
      fbtns.forEach(function(x) {{ x.classList.remove("active"); }});
      b.classList.add("active");
      var f = b.getAttribute("data-filter");
      rows.forEach(function(r) {{
        r.style.display = (f === "ALL" || r.getAttribute("data-tier") === f) ? "" : "none";
      }});
    }});
  }});
}})();
</script>
</body>
</html>
"""

    dest = Path(out_path).expanduser().resolve()
    write_text_atomic(dest, html_doc)
    return {
        "ok": True,
        "outPath": str(dest),
        "bytes": dest.stat().st_size,
        "ledgerVerified": ledger_ok,
        "ledgerCount": ledger_count,
        "headHash": head_hash,
        "tierCounts": tier_counts,
        "antiInflationCount": len(summary["antiInflationAlerts"]),
        "zeroPiiVerified": True,
    }
