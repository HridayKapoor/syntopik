"""Exam dates the learner is working toward (`study-config.json` `exams[]`).

One place to read and edit the list, shared by `syntopik pace --add-exam/--remove-exam`
and the dashboard's `GET/POST /api/exams`. Pure standard library.
"""

from __future__ import annotations

from datetime import date as date_cls
from pathlib import Path
from typing import Any

from syntopik.storage import load_json, save_json_atomic, workspace_lock

MAX_EXAM_NAME_CHARS = 80


def study_config_path(kg_dir: Path | str) -> Path:
    """Same lookup order the pacer and readiness use: study-config.json, then exam-config.json."""
    kg = Path(kg_dir)
    for fname in ("study-config.json", "exam-config.json"):
        if (kg / fname).is_file():
            return kg / fname
    return kg / "study-config.json"


def _name_of(ex: dict[str, Any]) -> str:
    return str(ex.get("name") or ex.get("title") or ex.get("subject") or "").strip()


def _load(cfg_path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    cfg = load_json(cfg_path, default={})
    if not isinstance(cfg, dict):
        cfg = {}
    exams = [ex for ex in (cfg.get("exams") or []) if isinstance(ex, dict)]
    if not exams and cfg.get("examDate"):
        # Carry a legacy single `examDate` over so adding a second exam doesn't silently drop it.
        exams.append({"name": str(cfg.get("targetExam") or "Exam"), "examDate": str(cfg["examDate"])[:10]})
        cfg.pop("examDate", None)
    return cfg, exams


def list_exams(kg_dir: Path | str, *, today: date_cls | None = None) -> list[dict[str, Any]]:
    """Exams sorted by date, each with `name`, `examDate` and `daysAway` (negative once past)."""
    today = today or date_cls.today()
    _cfg, exams = _load(study_config_path(kg_dir))
    out: list[dict[str, Any]] = []
    for ex in exams:
        raw = str(ex.get("examDate") or ex.get("date") or "").strip()
        try:
            d = date_cls.fromisoformat(raw[:10])
        except ValueError:
            continue
        out.append({"name": _name_of(ex) or "Exam", "examDate": d.isoformat(), "daysAway": (d - today).days})
    out.sort(key=lambda e: e["examDate"])
    return out


def parse_exam_date(raw: str | None, *, today: date_cls | None = None) -> date_cls:
    today = today or date_cls.today()
    text = str(raw or "").strip()
    try:
        d = date_cls.fromisoformat(text[:10]) if text else None
    except ValueError:
        d = None
    if d is None:
        raise ValueError("Give the exam date as YYYY-MM-DD, for example 2026-12-20.")
    if d <= today:
        raise ValueError(f"{d.isoformat()} is not in the future, so there is nothing to pace toward.")
    return d


def set_exam(
    kg_dir: Path | str,
    name: str,
    exam_date: str | date_cls,
    *,
    chapter: str | None = None,
    today: date_cls | None = None,
) -> dict[str, Any]:
    """Add an exam, or update the one with the same name (case-insensitive)."""
    clean = " ".join(str(name or "").split())[:MAX_EXAM_NAME_CHARS]
    if not clean:
        raise ValueError("Give the exam a name, for example \"Biology final\".")
    d = exam_date if isinstance(exam_date, date_cls) else parse_exam_date(exam_date, today=today)
    entry: dict[str, Any] = {"name": clean, "examDate": d.isoformat()}
    if chapter and str(chapter).strip():
        entry["chapterSlug"] = str(chapter).strip()
    cfg_path = study_config_path(kg_dir)
    with workspace_lock(cfg_path):
        cfg, exams = _load(cfg_path)
        replaced = False
        for i, ex in enumerate(exams):
            if _name_of(ex).casefold() == clean.casefold():
                exams[i] = {**ex, **entry}
                replaced = True
        if not replaced:
            exams.append(entry)
        cfg["exams"] = exams
        save_json_atomic(cfg_path, cfg)
    return {
        "name": clean,
        "examDate": d.isoformat(),
        "daysAway": (d - (today or date_cls.today())).days,
        "replaced": replaced,
    }


def remove_exam(kg_dir: Path | str, name: str) -> dict[str, Any]:
    """Remove an exam by name (case-insensitive). Raises ValueError listing known names if absent."""
    target = " ".join(str(name or "").split())
    cfg_path = study_config_path(kg_dir)
    with workspace_lock(cfg_path):
        cfg, exams = _load(cfg_path)
        kept = [ex for ex in exams if _name_of(ex).casefold() != target.casefold()]
        if not target or len(kept) == len(exams):
            known = ", ".join(_name_of(ex) for ex in exams if _name_of(ex)) or "none yet"
            raise ValueError(f"No exam named '{target}'. Exams set: {known}.")
        cfg["exams"] = kept
        save_json_atomic(cfg_path, cfg)
    return {"name": target, "removed": True}


def set_daily_minutes(kg_dir: Path | str, minutes: int) -> int:
    if not 15 <= int(minutes) <= 720:
        raise ValueError("Daily study time must be between 15 and 720 minutes.")
    cfg_path = study_config_path(kg_dir)
    with workspace_lock(cfg_path):
        cfg = load_json(cfg_path, default={})
        if not isinstance(cfg, dict):
            cfg = {}
        cfg["dailyMinutes"] = int(minutes)
        save_json_atomic(cfg_path, cfg)
    return int(minutes)
