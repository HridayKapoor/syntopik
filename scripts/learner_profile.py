#!/usr/bin/env python3
"""Syntopik v3.0 — Adaptive Learner Psychology Profile CLI.

Pure-stdlib wrapper around `syntopik.learner_model` exposing:
  - `status`    : Show current profile summary, domain Elos, affect, DNA, and compact policy
  - `calibrate` : Run Tier-1 non-VARK onboarding calibration
  - `place`     : Select or score Tier-2 per-chapter 2-3 probe adaptive placement
  - `record`    : Record a Tier-3 silent behavioral telemetry event
  - `policy`    : Compile and display Kalyuga Expertise-Reversal TeachingPolicy
  - `override`  : Set or clear transparent learner overrides
  - `reset`     : Reset `learner-profile.json` back to a clean default skeleton
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

from syntopik.learner_model import (  # noqa: E402
    CHALLENGE_TARGET_SUCCESS,
    ENTRY_RAMP_RATIOS,
    PROSE_DENSITY_WORDS,
    SCAFFOLDING_MODES,
    apply_tier1_calibration,
    apply_user_overrides,
    compile_teaching_policy,
    format_compact_policy_lines,
    load_profile,
    record_telemetry_event,
    save_profile,
    score_placement_probe,
    select_next_placement_probe,
)
from syntopik.paths import resolve_kg_dir  # noqa: E402
from syntopik.storage import load_jsonl  # noqa: E402


def _parse_domain_fluency(raw: str | None) -> dict[str, int] | None:
    if not raw or not raw.strip():
        return None
    text = raw.strip()
    if text.startswith("{"):
        obj = json.loads(text)
        if not isinstance(obj, dict):
            raise ValueError("--domain-fluency JSON must be an object.")
        return {str(k): int(v) for k, v in obj.items()}
    out: dict[str, int] = {}
    for pair in text.split(","):
        if "=" not in pair:
            continue
        k, v = pair.split("=", 1)
        if k.strip():
            out[k.strip()] = int(v.strip())
    return out or None


def _parse_bool_opt(raw: str | None) -> bool | None:
    if raw is None:
        return None
    low = str(raw).strip().lower()
    if low in ("none", "null", "clear", "auto"):
        return None
    if low in ("true", "1", "yes", "on"):
        return True
    if low in ("false", "0", "no", "off"):
        return False
    raise ValueError(f"Expected true/false/clear, got {raw!r}")


def _build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--workspace", type=Path, default=argparse.SUPPRESS, help="Workspace root directory.")
    common.add_argument("--kg-dir", type=Path, default=argparse.SUPPRESS, help="Override knowledge-graph/ directory.")
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="Emit JSON output.")

    parser = argparse.ArgumentParser(
        prog="learner_profile.py",
        description="Manage Syntopik's 3-Cadence Adaptive Learner Psychology Profile & TeachingPolicy.",
        parents=[common],
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_stat = sub.add_parser("status", parents=[common], help="Inspect learner profile & compiled TeachingPolicy.")
    p_stat.add_argument("--domain", default=None, help="Optional domain focus.")

    p_cal = sub.add_parser("calibrate", parents=[common], help="Apply Tier-1 non-VARK onboarding calibration.")
    p_cal.add_argument("--entry-ramp", choices=tuple(ENTRY_RAMP_RATIOS), default="balanced")
    p_cal.add_argument("--challenge-appetite", choices=tuple(CHALLENGE_TARGET_SUCCESS), default="balanced")
    p_cal.add_argument(
        "--domain-fluency",
        default=None,
        help="JSON dict or comma pairs e.g. 'Physics=2,Maths=1' (0..3 scale).",
    )
    p_cal.add_argument("--session-minutes", type=int, default=30)
    p_cal.add_argument("--prose-density", choices=tuple(PROSE_DENSITY_WORDS), default="standard")
    p_cal.add_argument("--micro-tasks", default=None, help="Optional JSON array of calibration micro-task outcomes.")

    p_place = sub.add_parser("place", parents=[common], help="Tier-2 per-chapter adaptive placement probe.")
    p_place.add_argument("--domain", required=True, help="Subject/domain name.")
    p_place.add_argument("--chapter", required=True, help="Chapter slug.")
    p_place.add_argument("--candidates", default=None, help="Optional JSON array of candidate probes.")
    p_place.add_argument("--score", action="store_true", help="Score a completed placement probe instead of selecting.")
    p_place.add_argument("--difficulty", type=float, default=1500.0)
    p_place.add_argument("--correct", action="store_true", default=False)
    p_place.add_argument("--wrong", action="store_true", default=False)
    p_place.add_argument("--confidence", type=float, default=0.5)
    p_place.add_argument("--dont-know", action="store_true", default=False)
    p_place.add_argument("--duration-ms", type=int, default=None)
    p_place.add_argument("--probe-index", type=int, default=1)

    p_rec = sub.add_parser("record", parents=[common], help="Record a Tier-3 telemetry event.")
    p_rec.add_argument("--domain", default="General")
    p_rec.add_argument("--prop-id", default=None)
    p_rec.add_argument("--kind", default="review")
    p_rec.add_argument("--correct", action="store_true", default=False)
    p_rec.add_argument("--wrong", action="store_true", default=False)
    p_rec.add_argument("--difficulty", type=float, default=1500.0)
    p_rec.add_argument("--confidence", type=float, default=None)
    p_rec.add_argument("--duration-ms", type=int, default=None)
    p_rec.add_argument("--hints", type=int, default=0)
    p_rec.add_argument("--mistake-category", default=None)
    p_rec.add_argument("--pf-outcome", default=None)
    p_rec.add_argument("--skipped", action="store_true", default=False)

    p_pol = sub.add_parser("policy", parents=[common], help="Compile and print active TeachingPolicy.")
    p_pol.add_argument("--domain", default=None)
    p_pol.add_argument("--node-bkt", type=float, default=None)
    p_pol.add_argument("--energy", choices=("low", "normal", "deep"), default="normal")
    p_pol.add_argument("--compact", action="store_true", help="Emit 2-line Turn-0 digest.")

    p_ov = sub.add_parser("override", parents=[common], help="Set or clear manual user overrides.")
    p_ov.add_argument("--scaffolding-mode", default=None, help=f"One of {SCAFFOLDING_MODES} or 'clear'.")
    p_ov.add_argument("--allow-pf", default=None, help="true | false | clear")
    p_ov.add_argument("--max-prose-words", default=None, help="60..160 or 'clear'")
    p_ov.add_argument("--session-minutes", type=int, default=None)
    p_ov.add_argument("--clear", action="store_true", help="Clear all overrides.")

    sub.add_parser("reset", parents=[common], help="Reset learner-profile.json to clean defaults.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    ws = getattr(args, "workspace", None)
    kg_explicit = getattr(args, "kg_dir", None)
    as_json = bool(getattr(args, "json", False))
    kg_dir = resolve_kg_dir(workspace=ws, explicit=kg_explicit)

    try:
        if args.command == "status":
            prof = load_profile(kg_dir)
            policy = compile_teaching_policy(prof, domain=args.domain)
            lines = format_compact_policy_lines(prof, domain=args.domain)
            res: dict[str, Any] = {
                "ok": True,
                "kgDir": str(kg_dir),
                "compactLines": lines,
                "compiledPolicy": policy,
                "profile": prof,
            }
        elif args.command == "calibrate":
            prof = load_profile(kg_dir)
            fluency = _parse_domain_fluency(args.domain_fluency)
            micro = json.loads(args.micro_tasks) if args.micro_tasks else None
            apply_tier1_calibration(
                prof,
                entry_ramp=args.entry_ramp,
                challenge_appetite=args.challenge_appetite,
                domain_fluency=fluency,
                session_minutes=args.session_minutes,
                prose_density=args.prose_density,
                micro_task_results=micro,
            )
            saved = save_profile(kg_dir, prof)
            res = {
                "ok": True,
                "savedPath": str(saved),
                "compactLines": format_compact_policy_lines(prof),
                "compiledPolicy": prof["compiledPolicy"],
                "calibration": prof["calibration"],
            }
        elif args.command == "place":
            prof = load_profile(kg_dir)
            if args.score:
                is_correct = False if args.wrong else (True if args.correct else not args.dont_know)
                scored = score_placement_probe(
                    prof,
                    args.domain,
                    args.chapter,
                    difficulty=args.difficulty,
                    correct=is_correct,
                    confidence=args.confidence,
                    dont_know=args.dont_know,
                    duration_ms=args.duration_ms,
                    probe_index=args.probe_index,
                )
                save_profile(kg_dir, prof)
                res = {"ok": True, **scored}
            else:
                kg_props = load_jsonl(kg_dir / "propositions.jsonl")
                chains = load_jsonl(kg_dir / "chains.jsonl")
                if args.candidates:
                    cands = json.loads(args.candidates)
                else:
                    chap_low = args.chapter.strip().lower()
                    cands = [
                        {
                            "id": p.get("id"),
                            "title": p.get("title"),
                            "level": p.get("level") or ("basics" if p.get("kind") == "axiom" else "main"),
                        }
                        for p in kg_props
                        if p.get("id") and (chap_low in str(p.get("chapter") or "").lower() or chap_low in str(p.get("id") or "").lower())
                    ]
                    if not cands:
                        cands = [
                            {"id": f"{args.chapter}-basics", "title": f"{args.chapter} Foundations", "level": "basics", "difficulty": 1200},
                            {"id": f"{args.chapter}-main", "title": f"{args.chapter} Core", "level": "main", "difficulty": 1500},
                            {"id": f"{args.chapter}-advanced", "title": f"{args.chapter} Advanced", "level": "advanced", "difficulty": 1800},
                        ]
                selected = select_next_placement_probe(
                    prof,
                    args.domain,
                    args.chapter,
                    cands,
                    kg_props=kg_props,
                    chains=chains,
                )
                save_profile(kg_dir, prof)
                res = {"ok": True, **selected}
        elif args.command == "record":
            is_correct = False if (args.wrong or args.skipped) else True
            if args.correct and not args.wrong:
                is_correct = True
            res = record_telemetry_event(
                kg_dir,
                domain=args.domain,
                prop_id=args.prop_id,
                kind=args.kind,
                correct=is_correct,
                difficulty=args.difficulty,
                confidence=args.confidence,
                duration_ms=args.duration_ms,
                hints_used=args.hints,
                mistake_category=args.mistake_category,
                pf_outcome=args.pf_outcome,
                skipped=args.skipped,
            )
        elif args.command == "policy":
            prof = load_profile(kg_dir)
            policy = compile_teaching_policy(prof, domain=args.domain, node_bkt=args.node_bkt, energy=args.energy)
            lines = format_compact_policy_lines(prof, domain=args.domain)
            res = {"ok": True, "compactLines": lines, "compiledPolicy": policy}
        elif args.command == "override":
            ov: dict[str, Any] = {}
            if args.clear:
                ov = {"scaffoldingMode": None, "allowProductiveFailure": None, "maxProseWords": None}
            else:
                if args.scaffolding_mode is not None:
                    ov["scaffoldingMode"] = None if args.scaffolding_mode.lower() in ("clear", "none", "auto") else args.scaffolding_mode
                if args.allow_pf is not None:
                    ov["allowProductiveFailure"] = _parse_bool_opt(args.allow_pf)
                if args.max_prose_words is not None:
                    ov["maxProseWords"] = None if str(args.max_prose_words).lower() in ("clear", "none", "auto") else int(args.max_prose_words)
                if args.session_minutes is not None:
                    ov["sessionTargetMinutes"] = args.session_minutes
            res = apply_user_overrides(kg_dir, ov)
        elif args.command == "reset":
            path = kg_dir / "learner-profile.json"
            if path.exists():
                path.unlink()
            fresh = load_profile(kg_dir)
            saved = save_profile(kg_dir, fresh)
            res = {"ok": True, "reset": True, "savedPath": str(saved), "profile": fresh}
        else:
            parser.error(f"Unknown command: {args.command}")
            return 2
    except Exception as exc:  # noqa: BLE001
        res = {"ok": False, "command": args.command, "error": str(exc)}

    if as_json:
        print(json.dumps(res, indent=2, ensure_ascii=False))
    else:
        if not res.get("ok"):
            print(f"ERROR: {res.get('error', 'Operation failed')}", file=sys.stderr)
        elif args.command in ("status", "policy", "calibrate"):
            for ln in res.get("compactLines") or []:
                print(ln)
            if args.command == "policy" and not getattr(args, "compact", False):
                pol = res["compiledPolicy"]
                print(
                    f"  readiness={pol['effectiveReadiness']} · pacing={pol['pacingMultiplier']} · "
                    f"intervention={pol['affectIntervention']}"
                )
        elif args.command == "place":
            if args.score:
                print(
                    f"Placed {res['domain']}/{res['chapterSlug']}: theta {res['oldTheta']} -> {res['newTheta']} "
                    f"({res['expertiseBand']}, done={res['done']}, earlyStop={res['earlyStop']})"
                )
            elif res.get("done"):
                print(f"Placement complete ({res.get('reason')}): theta={res['currentTheta']} ({res['expertiseBand']})")
            else:
                p = res["probe"]
                print(
                    f"Probe #{res['probeIndex']}: {p.get('id')} ({p.get('title')}, diff={p.get('difficulty')}, "
                    f"P={res['expectedSuccess']}, I={res['fisherInfo']})"
                )
        elif args.command == "record":
            print(
                f"Recorded {res['domain']} ({res['domainElo']} {res['expertiseBand']}) · "
                f"affect={res['affectiveState']['state']} · mode={res['compiledPolicy']['scaffoldingMode']}"
            )
        elif args.command == "override":
            print(f"Overrides updated: {res['userOverrides']} -> mode={res['compiledPolicy']['scaffoldingMode']}")
        elif args.command == "reset":
            print(f"Reset learner profile: {res['savedPath']}")

    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
