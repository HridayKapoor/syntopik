#!/usr/bin/env python3
"""Curated Eduniti JEE Main Physics PYQ Extractor & Chapter Query CLI.

Extracts ONLY clean structured PYQ JSON + referenced diagram PNGs from the
Eduniti `physics-v1-portal.zip` archive (skipping all HTML/CSS/JS/KaTeX/font
website boilerplate!), stores the clean bank under
`sources/_extracted/eduniti-pyqs/`, and exports zero-repetition question sets
directly compatible with `scripts/chapter_tracker.py add-questions`.

Subcommands:
    ingest-zip <zip_path> [--out-dir ...] [--json]
    chapters [--bank ...] [--json]
    export-chapter <chapter_query> [--bank ...] [--pyq-only] [--topic-id ...] [--out ...] [--json]
    show <uid> [--bank ...] [--json]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT_DIR = ROOT / "sources" / "_extracted" / "eduniti-pyqs"
DEFAULT_BANK_FILE = DEFAULT_OUT_DIR / "physics-jee-main-pyqs.json"

PREFIX_RE = re.compile(r"^\s*window\.PHYSICS_DATA\s*=\s*", re.DOTALL)


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(s or "").lower()).strip()


def _parse_data_js(raw: str) -> dict[str, Any]:
    cleaned = PREFIX_RE.sub("", raw, count=1).strip()
    if cleaned.endswith(";"):
        cleaned = cleaned[:-1].strip()
    return json.loads(cleaned)


def _classify_paper(paper_id: str) -> tuple[str, str]:
    pid = str(paper_id or "").lower()
    if pid.startswith("jee_"):
        return "jee_main_pyq", "main"
    if "theory" in pid:
        return "jee_main_theory_pyq", "basics"
    if "circuits" in pid or "experiments" in pid:
        return "jee_main_special_pyq", "main"
    if "rank_booster" in pid:
        return "rank_booster", "advanced"
    return "jee_main_pyq", "main"


def _normalize_kind(q_type: str, options: list[Any]) -> str:
    t = str(q_type or "").lower()
    if t in ("numerical", "integer", "numeric") or not options:
        return "numerical"
    if t in ("msq", "multi", "multi_correct"):
        return "msq"
    return "mcq"


def ingest_zip(zip_path: Path, out_dir: Path, extract_diagrams: bool = True) -> dict[str, Any]:
    if not zip_path.is_file():
        raise SystemExit(f"Zip archive not found: {zip_path}")

    out_dir.mkdir(parents=True, exist_ok=True)
    diag_dir = out_dir / "diagrams"

    with zipfile.ZipFile(zip_path, "r") as zf:
        if "data.js" not in zf.namelist():
            raise SystemExit(f"'data.js' not found inside {zip_path}")
        raw_js = zf.read("data.js").decode("utf-8", errors="replace")
        raw_data = _parse_data_js(raw_js)

        zip_members = set(zf.namelist())
        questions_out: list[dict[str, Any]] = []
        extracted_diagrams = 0
        seen_uids: set[str] = set()

        if extract_diagrams:
            diag_dir.mkdir(parents=True, exist_ok=True)

        for q in raw_data.get("questions", []):
            if not isinstance(q, dict):
                continue
            uid = str(q.get("uid") or "").strip()
            if not uid or uid in seen_uids:
                continue
            seen_uids.add(uid)

            paper_id = str(q.get("paperId") or "").strip()
            bucket, default_level = _classify_paper(paper_id)
            options = q.get("options") if isinstance(q.get("options"), list) else []
            kind = _normalize_kind(str(q.get("type") or ""), options)

            raw_diag = str(q.get("diagramDataUrl") or "").strip()
            diagram_rel: str | None = None
            has_diagram = bool(q.get("hasDiagram")) and bool(raw_diag)

            if has_diagram and raw_diag in zip_members:
                fname = Path(raw_diag).name
                if extract_diagrams:
                    target_png = diag_dir / fname
                    if not target_png.exists():
                        target_png.write_bytes(zf.read(raw_diag))
                        extracted_diagrams += 1
                    else:
                        extracted_diagrams += 1
                diagram_rel = f"diagrams/{fname}"
            else:
                has_diagram = False

            questions_out.append({
                "uid": uid,
                "paperId": paper_id,
                "paperTitle": str(q.get("paperTitle") or paper_id),
                "year": q.get("year"),
                "yearLabel": str(q.get("yearLabel") or ""),
                "number": q.get("number"),
                "displayNumber": str(q.get("displayNumber") or ""),
                "chapter": str(q.get("canonicalChapter") or q.get("chapter") or "General").strip(),
                "rawChapter": str(q.get("chapter") or "").strip(),
                "examTag": str(q.get("examTag") or "JEE MAIN"),
                "bucket": bucket,
                "level": default_level,
                "kind": kind,
                "promptLatex": str(q.get("promptLatex") or "").strip(),
                "options": options,
                "correctAnswer": q.get("correctAnswer"),
                "hasDiagram": has_diagram,
                "diagramRelPath": diagram_rel,
            })

    papers_meta = [
        {
            "id": p.get("id"),
            "title": p.get("title"),
            "shortTitle": p.get("shortTitle"),
            "yearLabel": p.get("yearLabel"),
            "category": p.get("category"),
            "totalQuestions": p.get("totalQuestions"),
        }
        for p in raw_data.get("papers", [])
        if isinstance(p, dict)
    ]

    chapters_order = list(raw_data.get("chaptersOrder", []))
    bank = {
        "schemaVersion": 1,
        "sourceZip": str(zip_path),
        "extractedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "scope": "JEE Main Physics PYQs + Eduniti Rank Booster (zero website boilerplate)",
        "chaptersOrder": chapters_order,
        "papers": papers_meta,
        "totalQuestions": len(questions_out),
        "questions": questions_out,
    }

    bank_file = out_dir / "physics-jee-main-pyqs.json"
    bank_file.write_text(json.dumps(bank, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    return {
        "status": "ingested",
        "sourceZip": str(zip_path),
        "bankFile": str(bank_file),
        "diagramsDir": str(diag_dir) if extract_diagrams else None,
        "chaptersCount": len(chapters_order),
        "papersCount": len(papers_meta),
        "totalQuestions": len(questions_out),
        "diagramsExtracted": extracted_diagrams,
        "skippedWebsiteFiles": len(zip_members) - 1 - extracted_diagrams,
    }


def load_bank(bank_path: Path) -> dict[str, Any]:
    if not bank_path.is_file():
        raise SystemExit(f"Eduniti PYQ bank not found at {bank_path}. Run `eduniti_pyq.py ingest-zip <zip>` first.")
    return json.loads(bank_path.read_text(encoding="utf-8"))


def match_chapter_questions(bank: dict[str, Any], query: str, pyq_only: bool = False) -> tuple[str, list[dict[str, Any]]]:
    qnorm = _norm(query)
    aliases = {
        "shm": "simple harmonic motion",
        "nlm": "laws of motion",
        "wep": "work energy power",
        "com": "center of mass collision",
        "ktg": "ktg thermodynamics",
        "emi": "electromagnetic induction",
        "ac": "alternating current",
        "emw": "electromagnetic waves",
        "em waves": "electromagnetic waves",
    }
    target = aliases.get(qnorm, qnorm)

    chapters = bank.get("chaptersOrder") or sorted({q.get("chapter", "") for q in bank.get("questions", [])})
    matched_name = None
    for ch in chapters:
        cn = _norm(ch)
        if cn == target or target in cn or cn in target:
            matched_name = ch
            break
    if not matched_name:
        # Token overlap fallback
        q_toks = set(target.split())
        best_score = 0
        for ch in chapters:
            score = len(q_toks & set(_norm(ch).split()))
            if score > best_score:
                best_score = score
                matched_name = ch
    if not matched_name:
        raise SystemExit(f"No Physics chapter matched '{query}'. Available: {', '.join(chapters)}")

    qs = [
        q for q in bank.get("questions", [])
        if _norm(q.get("chapter", "")) == _norm(matched_name)
        and (not pyq_only or q.get("bucket") != "rank_booster")
    ]
    return matched_name, qs


def cmd_ingest(args: argparse.Namespace) -> int:
    res = ingest_zip(
        Path(args.zip_path).expanduser().resolve(),
        Path(args.out_dir).expanduser().resolve(),
        extract_diagrams=not args.no_diagrams,
    )
    if args.json:
        print(json.dumps(res, indent=2, ensure_ascii=False))
    else:
        print(
            f"Ingested Eduniti PYQs → {res['bankFile']} "
            f"({res['totalQuestions']} questions across {res['chaptersCount']} chapters, "
            f"{res['diagramsExtracted']} diagrams; skipped {res['skippedWebsiteFiles']} website assets)"
        )
    return 0


def cmd_chapters(args: argparse.Namespace) -> int:
    bank = load_bank(Path(args.bank).expanduser().resolve())
    counts: dict[str, dict[str, int]] = {}
    for q in bank.get("questions", []):
        ch = q.get("chapter") or "General"
        rec = counts.setdefault(ch, {"total": 0, "jeeMainPyq": 0, "rankBooster": 0, "withDiagram": 0})
        rec["total"] += 1
        if q.get("bucket") == "rank_booster":
            rec["rankBooster"] += 1
        else:
            rec["jeeMainPyq"] += 1
        if q.get("hasDiagram"):
            rec["withDiagram"] += 1

    ordered = []
    seen = set()
    for ch in bank.get("chaptersOrder", []):
        if ch in counts:
            ordered.append({"chapter": ch, **counts[ch]})
            seen.add(ch)
    for ch, rec in sorted(counts.items()):
        if ch not in seen:
            ordered.append({"chapter": ch, **rec})

    if args.json:
        print(json.dumps({"totalQuestions": bank.get("totalQuestions", 0), "chapters": ordered}, indent=2, ensure_ascii=False))
    else:
        print(f"Eduniti JEE Main Physics Bank ({bank.get('totalQuestions', 0)} questions, {len(ordered)} chapters):")
        for item in ordered:
            print(
                f"  - {item['chapter']}: {item['total']} total "
                f"({item['jeeMainPyq']} PYQ + {item['rankBooster']} Rank Booster, {item['withDiagram']} diagrams)"
            )
    return 0


def cmd_export_chapter(args: argparse.Namespace) -> int:
    bank_path = Path(args.bank).expanduser().resolve()
    bank = load_bank(bank_path)
    ch_name, qs = match_chapter_questions(bank, args.chapter, pyq_only=args.pyq_only)

    source_id = args.source_id or f"eduniti-jee-main-{re.sub(r'[^a-z0-9]+', '-', ch_name.lower()).strip('-')}"
    exported: list[dict[str, Any]] = []
    for q in qs:
        qid = "pyq-" + re.sub(r"[^a-z0-9]+", "-", q["uid"].lower()).strip("-")
        exam_tag = q.get("examTag") or q.get("paperTitle") or "JEE Main"
        disp = q.get("displayNumber") or f"Q{q.get('number', '')}"
        short_prompt = re.sub(r"\s+", " ", q.get("promptLatex", "")).strip()
        if len(short_prompt) > 95:
            short_prompt = short_prompt[:94].rstrip() + "…"
        exported.append({
            "qid": qid,
            "sourceId": source_id,
            "edunitiUid": q["uid"],
            "label": f"[{exam_tag} {disp}] {short_prompt}",
            "page": q.get("number") or 0,
            "topicId": args.topic_id or "",
            "level": q.get("level") or "main",
            "kind": q.get("kind") or "mcq",
            "bucket": q.get("bucket") or "jee_main_pyq",
            "hasDiagram": bool(q.get("hasDiagram")),
            "diagramPath": str((bank_path.parent / q["diagramRelPath"]).resolve()) if q.get("diagramRelPath") else None,
        })

    if args.out:
        out_p = Path(args.out).expanduser().resolve()
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(json.dumps(exported, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    payload = {
        "chapter": ch_name,
        "sourceId": source_id,
        "count": len(exported),
        "pyqOnly": bool(args.pyq_only),
        "out": str(Path(args.out).expanduser().resolve()) if args.out else None,
        "questions": exported if not args.out or args.json else [],
    }
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(f"Exported {len(exported)} JEE Main questions for '{ch_name}' (sourceId={source_id})" + (f" → {args.out}" if args.out else ""))
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    bank_path = Path(args.bank).expanduser().resolve()
    bank = load_bank(bank_path)
    needle = args.uid.strip()
    found = None
    for q in bank.get("questions", []):
        qid = "pyq-" + re.sub(r"[^a-z0-9]+", "-", q["uid"].lower()).strip("-")
        if q["uid"] == needle or qid == needle:
            found = dict(q)
            found["qid"] = qid
            if q.get("diagramRelPath"):
                found["diagramAbsPath"] = str((bank_path.parent / q["diagramRelPath"]).resolve())
            break
    if not found:
        raise SystemExit(f"Question '{needle}' not found in {bank_path}")
    if args.json:
        print(json.dumps(found, indent=2, ensure_ascii=False))
    else:
        print(f"{found['uid']} · {found['chapter']} · {found['examTag']} ({found['kind'].upper()})")
        print(f"\n{found['promptLatex']}\n")
        for idx, opt in enumerate(found.get("options") or []):
            letter = chr(ord("A") + idx)
            print(f"  {letter}. {opt}")
        print(f"\nAnswer: {found.get('correctAnswer')}")
        if found.get("diagramAbsPath"):
            print(f"Diagram: {found['diagramAbsPath']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Extract & query curated Eduniti JEE Main Physics PYQs (skips all website boilerplate)."
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_in = sub.add_parser("ingest-zip", help="Extract clean PYQ JSON + referenced diagram PNGs from physics-v1-portal.zip")
    p_in.add_argument("zip_path", help="Path to physics-v1-portal.zip")
    p_in.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR), help="Target directory for clean bank")
    p_in.add_argument("--no-diagrams", action="store_true", help="Skip extracting diagram PNGs")
    p_in.add_argument("--json", action="store_true")
    p_in.set_defaults(func=cmd_ingest)

    p_ch = sub.add_parser("chapters", help="List all 25 Physics chapters and PYQ counts")
    p_ch.add_argument("--bank", default=str(DEFAULT_BANK_FILE))
    p_ch.add_argument("--json", action="store_true")
    p_ch.set_defaults(func=cmd_chapters)

    p_ex = sub.add_parser("export-chapter", help="Export chapter PYQs in chapter_tracker.py add-questions format")
    p_ex.add_argument("chapter", help="Chapter name or alias (e.g. 'SHM', 'Simple Harmonic Motion', 'Electrostatics')")
    p_ex.add_argument("--bank", default=str(DEFAULT_BANK_FILE))
    p_ex.add_argument("--pyq-only", action="store_true", help="Exclude Eduniti Rank Booster and export pure JEE Main PYQs only")
    p_ex.add_argument("--source-id", default=None)
    p_ex.add_argument("--topic-id", default="")
    p_ex.add_argument("--out", default=None, help="Write exported JSON list to file")
    p_ex.add_argument("--json", action="store_true")
    p_ex.set_defaults(func=cmd_export_chapter)

    p_sh = sub.add_parser("show", help="Inspect full LaTeX prompt, options, answer, and diagram path for a PYQ uid/qid")
    p_sh.add_argument("uid", help="Eduniti uid (e.g. jee_2020_top100::37) or tracker qid (pyq-jee-2020-top100-37)")
    p_sh.add_argument("--bank", default=str(DEFAULT_BANK_FILE))
    p_sh.add_argument("--json", action="store_true")
    p_sh.set_defaults(func=cmd_show)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
