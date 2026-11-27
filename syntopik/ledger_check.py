"""Syntopik — Learner Ledger Integrity Check & Safe Repair (pure stdlib).

`storage.load_jsonl` deliberately skips blank or corrupted lines so one bad
line never crashes a study session. The catch: the next `save_jsonl_atomic`
rewrites the file from what was loaded, and the corrupted record disappears
without a trace. A crash mid-append, a bad merge or a hand edit can therefore
silently lose learning history.

This module makes that visible and recoverable:

- `scan_ledgers(kg_dir)` reports corrupted / non-object lines (with line
  numbers), duplicate proposition ids, and chains whose premises or
  conclusion point at propositions that do not exist.
- `repair_ledgers(kg_dir)` moves corrupted lines into
  `knowledge-graph/.quarantine/<file>.<timestamp>.jsonl` (never deleting
  them) and rewrites the ledger with only the valid rows. Duplicates and
  dangling chains are reported, never auto-merged or deleted, because only
  the learner can say which copy is right.

The hash-chained `evidence-ledger.jsonl` is scanned but never rewritten; its
own verifier (`syntopik.evidence.verify_ledger_chain`) owns repairs there.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from syntopik.schemas import normalize_chain
from syntopik.storage import workspace_lock, write_text_atomic

__all__ = ["LEDGER_FILES", "scan_ledgers", "repair_ledgers"]

LEDGER_FILES: tuple[str, ...] = (
    "propositions.jsonl",
    "chains.jsonl",
    "revlog.jsonl",
    "mistakes.jsonl",
    "exam-attempts.jsonl",
    "reading-queue.jsonl",
    "derivations.jsonl",
    "forks.jsonl",
    "arenas.jsonl",
    "decisions.jsonl",
    "viva-sessions.jsonl",
    "interleave-sessions.jsonl",
    "evidence-ledger.jsonl",
)
_NEVER_REWRITE = {"evidence-ledger.jsonl"}
_MAX_EXAMPLES = 5


def _scan_file(path: Path) -> dict[str, Any]:
    bad: list[dict[str, Any]] = []
    valid = 0
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        return {"file": path.name, "rows": 0, "corrupt": [], "unreadable": f"not UTF-8: {exc.reason}"}
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError as exc:
            bad.append({"line": lineno, "reason": f"invalid JSON: {exc.msg}", "preview": line[:80]})
            continue
        if not isinstance(obj, dict):
            bad.append({"line": lineno, "reason": f"not a JSON object ({type(obj).__name__})", "preview": line[:80]})
            continue
        valid += 1
    return {"file": path.name, "rows": valid, "corrupt": bad}


def _load_objects(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.is_file():
        return rows
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            obj = json.loads(raw)
        except ValueError:
            continue
        if isinstance(obj, dict):
            rows.append(obj)
    return rows


def scan_ledgers(kg_dir: Path | str) -> dict[str, Any]:
    """Scan every learner ledger and return a JSON-serialisable integrity report."""
    kg = Path(kg_dir)
    files: list[dict[str, Any]] = []
    corrupt_total = 0
    for name in LEDGER_FILES:
        p = kg / name
        if not p.is_file():
            continue
        info = _scan_file(p)
        corrupt_total += len(info["corrupt"]) + (1 if info.get("unreadable") else 0)
        files.append(info)

    props = _load_objects(kg / "propositions.jsonl")
    id_counts = Counter(str(p.get("id")).strip() for p in props if str(p.get("id") or "").strip())
    duplicates = sorted(pid for pid, n in id_counts.items() if n > 1)
    missing_ids = sum(1 for p in props if not str(p.get("id") or "").strip())
    known = set(id_counts)

    dangling: list[dict[str, Any]] = []
    for idx, raw in enumerate(_load_objects(kg / "chains.jsonl"), 1):
        norm = normalize_chain(raw)
        if not norm:
            continue
        missing = [n for n in [*norm["premises"], norm["conclusion"]] if n not in known]
        if missing:
            dangling.append({"index": idx, "chainKey": norm["chainKey"], "missing": missing})

    issues = corrupt_total + len(duplicates) + missing_ids + len(dangling)
    return {
        "ok": issues == 0,
        "issues": issues,
        "corruptLines": corrupt_total,
        "files": files,
        "propositions": len(props),
        "duplicateIds": duplicates,
        "propositionsMissingId": missing_ids,
        "danglingChains": len(dangling),
        "danglingExamples": dangling[:_MAX_EXAMPLES],
    }


def summarize(report: dict[str, Any]) -> str:
    if report["ok"]:
        return f"{len(report['files'])} ledger(s) clean ({report['propositions']} propositions)"
    parts = []
    if report["corruptLines"]:
        where = ", ".join(f"{f['file']}:{','.join(str(b['line']) for b in f['corrupt'][:3])}" for f in report["files"] if f["corrupt"])
        parts.append(f"{report['corruptLines']} corrupted line(s) [{where}] (run doctor --fix to quarantine)")
    if report["duplicateIds"]:
        parts.append(f"{len(report['duplicateIds'])} duplicate proposition id(s): {', '.join(report['duplicateIds'][:3])}")
    if report["propositionsMissingId"]:
        parts.append(f"{report['propositionsMissingId']} proposition(s) without id")
    if report["danglingChains"]:
        parts.append(f"{report['danglingChains']} chain(s) point at missing propositions")
    return "; ".join(parts)


def repair_ledgers(kg_dir: Path | str, *, now: datetime | None = None) -> dict[str, Any]:
    """Quarantine corrupted lines (never delete them) and rewrite ledgers with valid rows only."""
    kg = Path(kg_dir)
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    quarantine_dir = kg / ".quarantine"
    repaired: list[dict[str, Any]] = []
    for name in LEDGER_FILES:
        if name in _NEVER_REWRITE:
            continue
        path = kg / name
        if not path.is_file():
            continue
        with workspace_lock(path):
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                repaired.append({"file": name, "skipped": "not UTF-8; left untouched for manual recovery"})
                continue
            keep: list[str] = []
            bad: list[str] = []
            for raw in text.splitlines():
                line = raw.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    bad.append(raw)
                    continue
                (keep if isinstance(obj, dict) else bad).append(line)
            if not bad:
                continue
            quarantine_dir.mkdir(parents=True, exist_ok=True)
            qfile = quarantine_dir / f"{path.stem}.{stamp}.jsonl"
            with qfile.open("a", encoding="utf-8") as fh:
                fh.write("".join(b + "\n" for b in bad))
            write_text_atomic(path, "".join(k + "\n" for k in keep))
            repaired.append({"file": name, "quarantined": len(bad), "kept": len(keep), "quarantineFile": str(qfile)})
    return {"ok": True, "repaired": repaired, "quarantinedLines": sum(r.get("quarantined", 0) for r in repaired)}
