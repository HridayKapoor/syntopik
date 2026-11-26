#!/usr/bin/env python3
"""Blunder FEN & Critical-Moment Fork Replay CLI for Syntopik v2.0.

Wraps `syntopik.forks` to capture pre-blunder checkpoints into
`knowledge-graph/forks.jsonl`, query due forks, generate deterministic
symbol/number mutations, and check learner moves (playing deterministic
refutations on repeated blunders BEFORE revealing the winning move).

Pure Python 3.12+ standard library only (optional lazy SymPy / code_verify).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from syntopik.forks import (  # noqa: E402
    capture_fork,
    check_fork_move,
    fork_stats,
    list_due_forks,
    mutate_fork,
)

__all__ = [
    "capture_fork",
    "list_due_forks",
    "check_fork_move",
    "mutate_fork",
    "fork_stats",
    "main",
]


def _parse_json_arg(raw: str | None, label: str) -> Any:
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    p = Path(s).expanduser()
    if p.is_file():
        s = p.read_text(encoding="utf-8")
    try:
        return json.loads(s)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON for {label}: {exc}") from exc


def _build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--workspace", type=Path, default=argparse.SUPPRESS, help="Workspace root directory.")
    common.add_argument("--kg-dir", type=Path, default=argparse.SUPPRESS, help="Override knowledge-graph/ directory.")
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="Emit JSON output.")

    parser = argparse.ArgumentParser(
        prog="fork_replay.py",
        description="Blunder FEN & Critical-Moment Fork Replay CLI for Syntopik v2.0.",
        parents=[common],
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_cap = sub.add_parser("capture", parents=[common], help="Capture a pre-blunder FEN checkpoint.")
    p_cap.add_argument("--id", dest="fork_id", default=None, help="Optional explicit fork ID.")
    p_cap.add_argument("--mistake-id", default=None, help="Linked mistakes.jsonl ID (auto-hydrates fields).")
    p_cap.add_argument("--prop-id", default="", help="Linked proposition ID.")
    p_cap.add_argument("--subject", default=None, help="Subject name (e.g. Physics, Computer Science).")
    p_cap.add_argument("--chapter", default=None, help="Chapter title or slug.")
    p_cap.add_argument(
        "--prefix",
        action="append",
        default=[],
        help="Prefix step before the blunder (repeatable).",
    )
    p_cap.add_argument("--prefix-json", default=None, help="JSON array of prefixSteps (string or file).")
    p_cap.add_argument("--prompt", default="", help="Single-line fork prompt shown after prefixSteps.")
    p_cap.add_argument("--blunder", default="", help="Historical wrong move (sealed).")
    p_cap.add_argument("--winning-move", default="", help="Correct next move (sealed).")
    p_cap.add_argument("--refutation", default="", help="Deterministic refutation played on repeat blunder.")
    p_cap.add_argument(
        "--engine",
        choices=("exact", "choice", "math", "code"),
        default="exact",
        help="Move verification engine (default: exact).",
    )
    p_cap.add_argument("--verify-json", default=None, help="Optional verifySpec JSON object or file.")
    p_cap.add_argument("--mutations-json", default=None, help="Optional mutationPool JSON array or file.")

    p_due = sub.add_parser("due", parents=[common], help="List due Blunder FEN forks (without leaking sealed answers).")
    p_due.add_argument("--limit", type=int, default=20, help="Maximum forks to return (default: 20).")
    p_due.add_argument("--subject", default=None, help="Filter by subject.")
    p_due.add_argument("--prop-id", default=None, help="Filter by proposition ID.")
    p_due.add_argument("--all", dest="include_all", action="store_true", help="Include non-due forks.")
    p_due.add_argument("--today", default=None, help="Override UTC reference date (YYYY-MM-DD).")
    p_due.add_argument("--include-sealed", action="store_true", help="Include sealed moves (for debugging/admin).")

    p_chk = sub.add_parser("check", parents=[common], help="Check a learner's move against a fork checkpoint.")
    p_chk.add_argument("fork_id", help="Fork ID to check.")
    p_chk.add_argument("--move", required=True, help="Learner's proposed next step / move.")
    p_chk.add_argument("--latency-ms", type=int, default=None, help="Decision latency in milliseconds.")
    p_chk.add_argument("--variant-id", default=None, help="Optional mutation variant ID being answered.")
    p_chk.add_argument(
        "--reveal-fix",
        action="store_true",
        help="Reveal sealedWinningMove after checking (default: stay sealed on blunder/miss).",
    )

    p_mut = sub.add_parser("mutate", parents=[common], help="Generate a deterministic symbol/number mutation of a fork.")
    p_mut.add_argument("fork_id", help="Fork ID to mutate.")
    p_mut.add_argument("--seed", type=int, default=None, help="Deterministic RNG seed.")
    p_mut.add_argument("--variant-index", type=int, default=None, help="Select an existing mutationPool index.")
    p_mut.add_argument("--substitutions", default=None, help="JSON dict of token swaps, e.g. '{\"A\":\"R\",\"T\":\"P\"}'.")
    p_mut.add_argument("--persist", action="store_true", help="Save generated mutation into fork's mutationPool.")
    p_mut.add_argument("--include-sealed", action="store_true", help="Include mutated sealed answers.")

    sub.add_parser("stats", parents=[common], help="Show Woodpecker latency compression & blunder-repeat stats.")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    ws = getattr(args, "workspace", None)
    kg_dir = getattr(args, "kg_dir", None)
    as_json = bool(getattr(args, "json", False))

    try:
        if args.command == "capture":
            prefix_steps = list(args.prefix or [])
            if args.prefix_json:
                parsed_pref = _parse_json_arg(args.prefix_json, "--prefix-json")
                if not isinstance(parsed_pref, list):
                    raise ValueError("--prefix-json must be a JSON array of strings.")
                prefix_steps.extend(str(x) for x in parsed_pref)

            verify_spec = _parse_json_arg(args.verify_json, "--verify-json") or {}
            if not isinstance(verify_spec, dict):
                raise ValueError("--verify-json must be a JSON object.")
            verify_spec.setdefault("engine", args.engine)

            muts = _parse_json_arg(args.mutations_json, "--mutations-json")
            if muts is not None and not isinstance(muts, list):
                raise ValueError("--mutations-json must be a JSON array.")

            res = capture_fork(
                prop_id=args.prop_id,
                prefix_steps=prefix_steps,
                fork_prompt=args.prompt,
                sealed_blunder=args.blunder,
                sealed_winning_move=args.winning_move,
                refutation_msg=args.refutation,
                mistake_id=args.mistake_id,
                subject=args.subject,
                chapter=args.chapter,
                verify_spec=verify_spec,
                mutation_pool=muts,
                fork_id=args.fork_id,
                workspace=ws,
                kg_dir=kg_dir,
            )
        elif args.command == "due":
            res = list_due_forks(
                workspace=ws,
                kg_dir=kg_dir,
                limit=args.limit,
                subject=args.subject,
                prop_id=args.prop_id,
                include_all=args.include_all,
                include_sealed=args.include_sealed,
                today=args.today,
            )
        elif args.command == "check":
            res = check_fork_move(
                args.fork_id,
                args.move,
                workspace=ws,
                kg_dir=kg_dir,
                latency_ms=args.latency_ms,
                variant_id=args.variant_id,
                reveal_fix=args.reveal_fix,
            )
        elif args.command == "mutate":
            subs = _parse_json_arg(args.substitutions, "--substitutions")
            if subs is not None and not isinstance(subs, dict):
                raise ValueError("--substitutions must be a JSON object.")
            res = mutate_fork(
                args.fork_id,
                workspace=ws,
                kg_dir=kg_dir,
                seed=args.seed,
                variant_index=args.variant_index,
                substitutions=subs,
                persist=args.persist,
                include_sealed=args.include_sealed,
            )
        elif args.command == "stats":
            res = fork_stats(workspace=ws, kg_dir=kg_dir)
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
        elif args.command == "capture":
            print(f"Fork {res['action']}: {res['id']} → {res['path']}")
        elif args.command == "due":
            print(f"Due forks ({res['dueCount']}/{res['totalForks']}):")
            for it in res["items"]:
                print(f"  - {it['id']} [{it['status']}] ({it.get('subject')}) {it.get('forkPrompt')}")
        elif args.command == "check":
            if res["outcome"] == "win":
                print(f"WIN on {res['forkId']} · nextReview={res['nextReview']} (stability={res['stabilityDays']}d)")
            elif res["outcome"] == "blunder_repeat":
                print(f"BLUNDER REPEAT on {res['forkId']}:")
                print(f"  Refutation: {res['refutation']}")
                if res.get("winningMove"):
                    print(f"  Winning move: {res['winningMove']}")
            else:
                print(f"MISS on {res['forkId']} · {res.get('nextPrompt', '')}")
        elif args.command == "mutate":
            v = res["variant"]
            print(f"Mutation {v['id']} ({v['label']}) for {res['forkId']}:")
            for step in v.get("prefixSteps") or []:
                print(f"  | {step}")
            print(f"  > {v.get('forkPrompt')}")
        elif args.command == "stats":
            print(
                f"Forks: {res['totalForks']} total ({res['dueCount']} due, {res['solidCount']} solid) · "
                f"wins={res['totalWins']} · blunderRepeats={res['totalBlunderRepeats']} · "
                f"medianLatencyMs={res['medianLatencyMs']}"
            )
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
