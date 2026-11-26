#!/usr/bin/env python3
"""Spoken Audio Flash-Revision Generator (P2.3).

Converts Markdown chapter notes (`chapters/<slug>.md`), matching chapter traps
from `knowledge-graph/mistakes.jsonl`, and/or explicit JSON Q->A cards into a
hands-free commute/walk MP3 (or M4A) active-recall track with exact configurable
silence pauses (`--pause-sec 2.5` default, anchored on Rowe's 3-second active
retrieval Wait-Time threshold).

Uses `edge-tts` v7.x streaming API with 100% offline macOS `/usr/bin/say`
fallback + `ffmpeg` silence generation and clean concatenation.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_KG_DIR = ROOT / "knowledge-graph"
DEFAULT_CHAPTERS_DIR = ROOT / "chapters"

DEFAULT_EDGE_VOICE = "en-IN-NeerjaNeural"
DEFAULT_SAY_VOICE = "Rishi"

# Greek letters & common STEM LaTeX symbols -> natural spoken English.
LATEX_SYMBOL_MAP: list[tuple[str, str]] = [
    (r"\\varepsilon\b", " epsilon "),
    (r"\\epsilon\b", " epsilon "),
    (r"\\varphi\b", " phi "),
    (r"\\phi\b", " phi "),
    (r"\\vartheta\b", " theta "),
    (r"\\theta\b", " theta "),
    (r"\\omega\b", " omega "),
    (r"\\Omega\b", " capital omega "),
    (r"\\alpha\b", " alpha "),
    (r"\\beta\b", " beta "),
    (r"\\gamma\b", " gamma "),
    (r"\\Gamma\b", " capital gamma "),
    (r"\\delta\b", " delta "),
    (r"\\Delta\b", " delta "),
    (r"\\lambda\b", " lambda "),
    (r"\\Lambda\b", " capital lambda "),
    (r"\\mu\b", " mu "),
    (r"\\nu\b", " nu "),
    (r"\\pi\b", " pi "),
    (r"\\Pi\b", " capital pi "),
    (r"\\rho\b", " rho "),
    (r"\\sigma\b", " sigma "),
    (r"\\Sigma\b", " sum "),
    (r"\\tau\b", " tau "),
    (r"\\eta\b", " eta "),
    (r"\\kappa\b", " kappa "),
    (r"\\psi\b", " psi "),
    (r"\\chi\b", " chi "),
    (r"\\zeta\b", " zeta "),
    (r"\\infty\b", " infinity "),
    (r"\\pm\b", " plus or minus "),
    (r"\\mp\b", " minus or plus "),
    (r"\\times\b", " times "),
    (r"\\cdot\b", " dot "),
    (r"\\div\b", " divided by "),
    (r"\\approx\b", " approximately equals "),
    (r"\\equiv\b", " is equivalent to "),
    (r"\\neq\b|\\ne\b", " is not equal to "),
    (r"\\leq\b|\\le\b", " is less than or equal to "),
    (r"\\geq\b|\\ge\b", " is greater than or equal to "),
    (r"\\ll\b", " is much less than "),
    (r"\\gg\b", " is much greater than "),
    (r"\\propto\b", " is proportional to "),
    (r"\\rightleftharpoons\b|\\leftrightarrow\b", " in equilibrium with "),
    (r"\\implies\b|\\Rightarrow\b", " implies "),
    (r"\\rightarrow\b|\\to\b", " goes to "),
    (r"\\leftarrow\b", " from "),
    (r"\\partial\b", " partial "),
    (r"\\nabla\b", " del "),
    (r"\\oint\b", " closed integral of "),
    (r"\\int\b", " integral of "),
    (r"\\sum\b", " sum of "),
    (r"\\prod\b", " product of "),
    (r"\\sin\b", " sine "),
    (r"\\cos\b", " cosine "),
    (r"\\tan\b", " tangent "),
    (r"\\sec\b", " secant "),
    (r"\\csc\b", " cosecant "),
    (r"\\cot\b", " cotangent "),
    (r"\\ln\b", " natural log of "),
    (r"\\log\b", " log of "),
    (r"\\exp\b", " e to the "),
    (r"\\lim\b", " limit "),
    (r"\\circ\b|\\deg\b", " degrees "),
    (r"\\perp\b", " perpendicular to "),
    (r"\\parallel\b", " parallel to "),
    (r"\\angle\b", " angle "),
    (r"\\because\b", " because "),
    (r"\\therefore\b", " therefore "),
    (r"\\in\b", " in "),
    (r"\\notin\b", " not in "),
    (r"\\subset\b", " subset of "),
    (r"\\forall\b", " for all "),
    (r"\\exists\b", " there exists "),
]

UNICODE_MATH_MAP: dict[str, str] = {
    "Δ": " delta ",
    "δ": " delta ",
    "ω": " omega ",
    "Ω": " ohms ",
    "α": " alpha ",
    "β": " beta ",
    "γ": " gamma ",
    "θ": " theta ",
    "φ": " phi ",
    "λ": " lambda ",
    "μ": " mu ",
    "π": " pi ",
    "ρ": " rho ",
    "σ": " sigma ",
    "τ": " tau ",
    "ε": " epsilon ",
    "η": " eta ",
    "√": " square root of ",
    "∞": " infinity ",
    "±": " plus or minus ",
    "×": " times ",
    "·": " dot ",
    "÷": " divided by ",
    "≈": " approximately equals ",
    "≠": " not equal to ",
    "≤": " less than or equal to ",
    "≥": " greater than or equal to ",
    "∝": " proportional to ",
    "→": " to ",
    "⇒": " implies ",
    "⇌": " in equilibrium with ",
    "↔": " reversible with ",
    "°": " degrees ",
    "²": " squared ",
    "³": " cubed ",
    "⁻¹": " inverse ",
    "₀": " zero ",
    "₁": " one ",
    "₂": " two ",
    "₃": " three ",
    "∮": " closed integral of ",
    "∫": " integral of ",
    "∑": " sum of ",
    "—": ", ",
    "–": " to ",
    "∧": " and ",
}


def find_bin(name: str) -> str:
    which_hit = shutil.which(name)
    if which_hit:
        return which_hit
    home = Path.home()
    for cand in (
        str(home / ".local" / "bin" / name),
        f"/opt/homebrew/bin/{name}",
        f"/usr/local/bin/{name}",
        f"/usr/bin/{name}",
    ):
        if Path(cand).is_file() and os.access(cand, os.X_OK):
            return cand
    raise SystemExit(f"Required binary '{name}' not found on PATH.")


def probe_duration(path: Path, ffprobe_bin: str) -> float:
    proc = subprocess.run(
        [
            ffprobe_bin,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        raise RuntimeError(f"ffprobe failed on {path}: {proc.stderr.strip()}")
    return float(proc.stdout.strip())


def _extract_braced(text: str, start_idx: int) -> tuple[str, int] | None:
    """Extract balanced `{...}` group starting at `start_idx`, returning `(inner, end_idx)`."""
    if start_idx >= len(text) or text[start_idx] != "{":
        return None
    depth = 0
    for i in range(start_idx, len(text)):
        ch = text[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start_idx + 1 : i], i + 1
    return None


def _replace_macro_2args(text: str, macro_names: tuple[str, ...], formatter) -> str:
    pattern = re.compile(r"\\(?:" + "|".join(macro_names) + r")\s*")
    for _ in range(8):
        m = pattern.search(text)
        if not m:
            break
        pos = m.end()
        arg1 = _extract_braced(text, pos)
        if not arg1:
            break
        inner1, next_pos = arg1
        while next_pos < len(text) and text[next_pos].isspace():
            next_pos += 1
        arg2 = _extract_braced(text, next_pos)
        if not arg2:
            break
        inner2, end_pos = arg2
        text = text[: m.start()] + formatter(inner1, inner2) + text[end_pos:]
    return text


def _replace_macro_1arg(text: str, macro_names: tuple[str, ...], formatter) -> str:
    pattern = re.compile(r"\\(?:" + "|".join(macro_names) + r")\s*")
    for _ in range(12):
        m = pattern.search(text)
        if not m:
            break
        pos = m.end()
        arg1 = _extract_braced(text, pos)
        if not arg1:
            break
        inner1, end_pos = arg1
        text = text[: m.start()] + formatter(inner1) + text[end_pos:]
    return text


def _clean_chem_expr(expr: str) -> str:
    s = expr.replace("<=>", " in equilibrium with ").replace("->", " yields ")
    s = s.replace("^{", " ").replace("}", " ").replace("^", " ")
    s = re.sub(r"([A-Za-z])(\d+)", r"\1 \2", s)
    s = s.replace("+", " plus ")
    return f" {s} "


def latex_to_speech(raw: str) -> str:
    """Convert Markdown + LaTeX STEM prose into crisp, natural spoken English (zero raw backslashes)."""
    s = str(raw or "").strip()
    if not s:
        return ""

    # Markdown cleanup: links, inline code, emphasis, alert tags.
    s = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", s)
    s = re.sub(r"\[!(?:NOTE|TIP|IMPORTANT|WARNING|CAUTION)\]", "", s, flags=re.IGNORECASE)
    s = re.sub(r"[`*_~]+", "", s)
    s = re.sub(r"^#+\s*", "", s, flags=re.MULTILINE)
    s = re.sub(r"^>\s*", "", s, flags=re.MULTILINE)

    # Strip LaTeX math delimiters.
    s = s.replace("$$", " ").replace("$", " ")
    s = s.replace(r"\(", " ").replace(r"\)", " ").replace(r"\[", " ").replace(r"\]", " ")

    # Unwrap chemistry \ce{...} first.
    s = _replace_macro_1arg(s, ("ce",), _clean_chem_expr)

    # \sqrt[n]{x} before \sqrt{x}.
    s = re.sub(
        r"\\sqrt\s*\[\s*3\s*\]\s*\{([^{}]+)\}",
        r" cube root of \1 ",
        s,
    )
    s = re.sub(
        r"\\sqrt\s*\[\s*([^\]]+)\s*\]\s*\{([^{}]+)\}",
        r" \1 root of \2 ",
        s,
    )

    # Fractions & 1-arg LaTeX macros (iterated inside-out).
    for _ in range(4):
        s = _replace_macro_2args(s, ("frac", "dfrac", "tfrac"), lambda a, b: f" {a} over {b} ")
        s = _replace_macro_1arg(s, ("sqrt",), lambda a: f" square root of {a} ")
        s = _replace_macro_1arg(s, ("text", "mathrm", "mathbf", "mathit", "operatorname", "cancel", "boxed"), lambda a: f" {a} ")
        s = _replace_macro_1arg(s, ("vec", "overrightarrow"), lambda a: f" vector {a} ")
        s = _replace_macro_1arg(s, ("hat",), lambda a: f" {a} hat ")
        s = _replace_macro_1arg(s, ("bar", "overline"), lambda a: f" {a} bar ")
        s = _replace_macro_1arg(s, ("ddot",), lambda a: f" {a} double dot ")
        s = _replace_macro_1arg(s, ("dot",), lambda a: f" {a} dot ")
        s = _replace_macro_1arg(s, ("underbrace", "overbrace"), lambda a: f" {a} ")

    # Remove layout/spacing LaTeX commands.
    s = re.sub(r"\\(?:left|right|bigl?|bigr?|Bigl?|Bigr?|biggl?|biggr?|quad|qquad|displaystyle|limits)\b", " ", s)
    s = re.sub(r"\\[,;:! ]", " ", s)
    s = s.replace(r"\\", " ")

    # Handle degree exponents before generic Greek/symbol expansion so `^\circ` never becomes "to the power of degrees".
    s = re.sub(r"\^\s*\{\s*\\circ\s*\}", " degrees ", s)
    s = re.sub(r"\^\s*\\circ\b", " degrees ", s)

    # Greek & operator commands.
    for pat, repl in LATEX_SYMBOL_MAP:
        s = re.sub(pat, repl, s)

    # Unicode math glyphs.
    for ch, repl in UNICODE_MATH_MAP.items():
        if ch in s:
            s = s.replace(ch, repl)

    # Spoken exponents (handle common powers cleanly before generic ^{...}).
    s = re.sub(r"\^\s*\{\s*2\s*\}", " squared ", s)
    s = re.sub(r"\^\s*2\b", " squared ", s)
    s = re.sub(r"\^\s*\{\s*3\s*\}", " cubed ", s)
    s = re.sub(r"\^\s*3\b", " cubed ", s)
    s = re.sub(r"\^\s*\{\s*-1\s*\}", " inverse ", s)
    s = re.sub(r"\^\s*\{([^{}]+)\}", r" to the power of \1 ", s)
    s = re.sub(r"\^\s*([A-Za-z0-9-]+)", r" to the power of \1 ", s)

    # Spoken subscripts.
    s = re.sub(r"_\s*\{\s*0\s*\}", " zero ", s)
    s = re.sub(r"_0\b", " zero ", s)
    s = re.sub(r"_\s*\{\s*rms\s*\}", " R M S ", s, flags=re.IGNORECASE)
    s = re.sub(r"_\s*\{\s*eff\s*\}", " effective ", s, flags=re.IGNORECASE)
    s = re.sub(r"_\s*\{([^{}]+)\}", r" \1 ", s)
    s = re.sub(r"_([A-Za-z0-9]+)", r" \1 ", s)

    # Natural spoken slash inside short formulas like k/m, mv^2/R, dQ/dt.
    s = re.sub(r"(?<=[A-Za-z0-9)])\s*/\s*(?=[A-Za-z0-9(])", " over ", s)
    s = s.replace("<=", " less than or equal to ").replace(">=", " greater than or equal to ")
    s = s.replace("->", " to ").replace("=>", " implies ")
    s = s.replace("=", " equals ")

    # Drop any leftover LaTeX command names or raw braces/backslashes.
    s = re.sub(r"\\[A-Za-z]+", " ", s)
    s = s.replace("\\", " ").replace("{", " ").replace("}", " ")
    s = re.sub(r"\s+([,.;:!?])", r"\1", s)
    s = re.sub(r"\s+", " ", s).strip()
    if s and s[-1] not in ".!?":
        s += "."
    return s


def _norm_key(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()).strip()


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(text or "").lower()).strip("-")
    return slug or "revision"


def _chapter_matches(cand_chapter: str, target_names: list[str]) -> bool:
    c_norm = _norm_key(cand_chapter)
    if not c_norm:
        return False
    c_tokens = set(c_norm.split())
    for tgt in target_names:
        t_norm = _norm_key(tgt)
        if not t_norm:
            continue
        if c_norm == t_norm or t_norm in c_norm or c_norm in t_norm:
            return True
        # Abbreviation check (e.g., "shm" vs "simple harmonic motion").
        c_initials = "".join(w[0] for w in c_norm.split() if w)
        t_initials = "".join(w[0] for w in t_norm.split() if w)
        if (len(t_norm) >= 2 and t_norm == c_initials) or (len(c_norm) >= 2 and c_norm == t_initials):
            return True
        t_tokens = set(t_norm.split())
        if len(t_tokens) >= 2 and len(c_tokens & t_tokens) >= min(2, len(t_tokens)):
            return True
    return False


def load_cards_json(cards_path: Path) -> tuple[str | None, list[dict[str, str]]]:
    data = json.loads(cards_path.read_text(encoding="utf-8"))
    title: str | None = None
    if isinstance(data, dict):
        title = str(data.get("chapter") or data.get("title") or "").strip() or None
        raw_items = data.get("cards") or data.get("items") or []
    elif isinstance(data, list):
        raw_items = data
    else:
        raise SystemExit(f"Unsupported cards JSON in {cards_path}: expected list or object with 'cards'.")

    cards: list[dict[str, str]] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        q = str(item.get("q") or item.get("question") or item.get("prompt") or "").strip()
        a = str(item.get("a") or item.get("answer") or item.get("response") or "").strip()
        if q and a:
            cards.append({"q": q, "a": a, "source": str(item.get("source") or "cards")})
    return title, cards


def extract_cards_from_markdown(md_path: Path) -> tuple[str, list[dict[str, str]]]:
    """Extract crisp Q->A active-recall cards from chapter Markdown notes."""
    text = md_path.read_text(encoding="utf-8")
    lines = text.splitlines()

    chapter_title = md_path.stem.replace("-", " ").replace("_", " ").title()
    current_heading = ""
    section_bullets: dict[str, list[str]] = {}
    cards: list[dict[str, str]] = []

    pending_q: str | None = None
    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue

        # H1 title
        m_h1 = re.match(r"^#\s+(.+)$", line)
        if m_h1:
            chapter_title = re.sub(r"\s+[—–-]\s+.*$", "", m_h1.group(1).strip())
            continue

        # H2/H3 section heading
        m_h = re.match(r"^#{2,3}\s+(.+)$", line)
        if m_h:
            current_heading = re.sub(r"^\d+[\.)]\s*", "", m_h.group(1).strip())
            section_bullets.setdefault(current_heading, [])
            pending_q = None
            continue

        # Explicit Q: / A: lines (e.g., **Q:** ... / **A:** ...)
        m_q = re.match(r"^(?:[-*]\s*)?(?:\*\*)?Q(?:uestion)?\s*:(?:\*\*)?\s*(.+)$", line, flags=re.IGNORECASE)
        if m_q:
            pending_q = m_q.group(1).strip()
            continue
        m_a = re.match(r"^(?:[-*]\s*)?(?:\*\*)?A(?:nswer)?\s*:(?:\*\*)?\s*(.+)$", line, flags=re.IGNORECASE)
        if m_a and pending_q:
            cards.append({"q": pending_q, "a": m_a.group(1).strip(), "source": "markdown_qa"})
            pending_q = None
            continue

        # Trap / Warning callout lines
        m_trap = re.match(
            r"^(?:[-*>]\s*)?(?:\*\*)?(?:Trap|Exam Trap|Warning|Caution|Common Mistake)\s*:?(?:\*\*)?\s*(.+)$",
            line,
            flags=re.IGNORECASE,
        )
        if m_trap:
            body = m_trap.group(1).strip()
            if "->" in body or "—" in body:
                parts = re.split(r"\s*(?:->|—)\s*", body, maxsplit=1)
                if len(parts) == 2 and parts[0].strip() and parts[1].strip():
                    cards.append({
                        "q": f"Exam trap in {current_heading or chapter_title}: {parts[0].strip()} — what is the right rule?",
                        "a": parts[1].strip(),
                        "source": "markdown_trap",
                    })
                    continue
            cards.append({
                "q": f"What exam trap should you watch out for in {current_heading or chapter_title}?",
                "a": body,
                "source": "markdown_trap",
            })
            continue

        # Bold definition / formula bullets: `- **Term**: explanation` or `- **Term** — explanation`
        m_def = re.match(r"^[-*]\s+\*\*([^*]{2,90})\*\*\s*(?::|—|–|-)\s*(.+)$", line)
        if m_def:
            term = m_def.group(1).strip().rstrip(":")
            desc = m_def.group(2).strip()
            if term.lower() not in ("note", "example", "tip") and len(desc) >= 6:
                q_text = (
                    f"State the formula or rule for {term}."
                    if ("$" in desc or "=" in desc)
                    else f"Explain {term}."
                )
                cards.append({"q": q_text, "a": desc, "source": "markdown_def"})
                continue

        # Collect general bullets/prose under current section as fallback Q->A source.
        cleaned_bullet = re.sub(r"^[-*>]\s*", "", line)
        if current_heading and len(cleaned_bullet) >= 12 and not cleaned_bullet.startswith("|") and not cleaned_bullet.startswith("```"):
            section_bullets.setdefault(current_heading, []).append(cleaned_bullet)

    # Supplement from section headings that didn't yield explicit definitions/QAs yet.
    if len(cards) < 6:
        for heading, bullets in section_bullets.items():
            if not bullets:
                continue
            if heading.lower() in ("references", "sources", "links", "contents", "table of contents"):
                continue
            ans = " ".join(bullets[:2])
            if len(ans) > 260:
                ans = ans[:257].rsplit(" ", 1)[0] + "."
            cards.append({
                "q": f"What is the key idea behind {heading}?",
                "a": ans,
                "source": "markdown_section",
            })

    return chapter_title, cards


def extract_traps_from_kg(kg_dir: Path, chapter_names: list[str]) -> list[dict[str, str]]:
    """Extract Q->A recall cards from `<kg_dir>/mistakes.jsonl` matching the chapter."""
    mistakes_file = kg_dir / "mistakes.jsonl"
    if not mistakes_file.exists():
        return []

    cards: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for raw_line in mistakes_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except Exception:
            continue
        if not isinstance(row, dict):
            continue
        ch = str(row.get("chapter") or "").strip()
        if chapter_names and not _chapter_matches(ch, chapter_names):
            continue
        cue = str(row.get("triggerCue") or "").strip()
        wrong = str(row.get("wrongMove") or "").strip()
        correct = str(row.get("correctMove") or "").strip()
        if not cue or not correct:
            continue
        dedup_key = (_norm_key(cue), _norm_key(correct))
        if dedup_key in seen:
            continue
        seen.add(dedup_key)
        q = f"Trap check: when you see {cue}, what is the right move?"
        a = f"{correct}" + (f" Do not fall for: {wrong}." if wrong else "")
        cards.append({"q": q, "a": a, "source": "mistakes_trap"})
    return cards


def dedupe_cards(cards: list[dict[str, str]], max_cards: int) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    seen_q: set[str] = set()
    for c in cards:
        qk = _norm_key(c["q"])
        if not qk or qk in seen_q:
            continue
        seen_q.add(qk)
        out.append(c)
        if len(out) >= max_cards:
            break
    return out


async def _synth_edge_mp3(text: str, voice: str, rate: str, out_mp3: Path) -> None:
    import edge_tts

    comm = edge_tts.Communicate(text, voice=voice, rate=rate, boundary="SentenceBoundary")
    audio_bytes = bytearray()
    async for chunk in comm.stream():
        if chunk.get("type") == "audio":
            audio_bytes.extend(chunk["data"])
    if not audio_bytes:
        raise RuntimeError("edge-tts returned empty audio stream")
    out_mp3.write_bytes(bytes(audio_bytes))


def _synth_say_wav(text: str, voice: str, rate_wpm: int, out_wav: Path, ffmpeg_bin: str) -> None:
    if shutil.which("say") or Path("/usr/bin/say").is_file():
        say_bin = find_bin("say")
        aiff_path = out_wav.with_suffix(".aiff")
        proc = subprocess.run(
            [say_bin, "-v", voice, "-r", str(rate_wpm), "-o", str(aiff_path), text],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            proc = subprocess.run(
                [say_bin, "-r", str(rate_wpm), "-o", str(aiff_path), text],
                capture_output=True,
                text=True,
                check=False,
            )
            if proc.returncode != 0:
                raise RuntimeError(f"macOS say failed: {proc.stderr.strip()}")
        conv = subprocess.run(
            [ffmpeg_bin, "-y", "-v", "error", "-i", str(aiff_path), "-ar", "44100", "-ac", "1", "-c:a", "pcm_s16le", str(out_wav)],
            capture_output=True,
            text=True,
            check=False,
        )
        if conv.returncode != 0:
            raise RuntimeError(f"ffmpeg AIFF->WAV conversion failed: {conv.stderr.strip()}")
        return

    for espeak_name in ("espeak-ng", "espeak"):
        espeak_bin = shutil.which(espeak_name)
        if espeak_bin:
            raw_wav = out_wav.with_suffix(".espeak.wav")
            proc = subprocess.run(
                [espeak_bin, "-s", str(rate_wpm), "-w", str(raw_wav), text],
                capture_output=True,
                text=True,
                check=False,
            )
            if proc.returncode == 0 and raw_wav.is_file():
                conv = subprocess.run(
                    [ffmpeg_bin, "-y", "-v", "error", "-i", str(raw_wav), "-ar", "44100", "-ac", "1", "-c:a", "pcm_s16le", str(out_wav)],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if conv.returncode == 0:
                    return
    raise SystemExit("Offline TTS unavailable: install `edge-tts` (with network) or `espeak-ng` / macOS `say`.")


def _make_silence_wav(duration_sec: float, out_wav: Path, ffmpeg_bin: str) -> None:
    dur = max(0.05, float(duration_sec))
    proc = subprocess.run(
        [
            ffmpeg_bin,
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-t",
            f"{dur:.3f}",
            "-i",
            "anullsrc=r=44100:cl=mono",
            "-c:a",
            "pcm_s16le",
            str(out_wav),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg silence generation failed: {proc.stderr.strip()}")


def synthesize_segment(
    text: str,
    out_wav: Path,
    *,
    engine: str,
    edge_voice: str,
    edge_rate: str,
    say_voice: str,
    say_rate: int,
    ffmpeg_bin: str,
) -> str:
    """Synthesize a single spoken clip to 44.1kHz mono PCM WAV and return the engine used."""
    if engine in ("auto", "edge"):
        mp3_tmp = out_wav.with_suffix(".tmp.mp3")
        try:
            asyncio.run(_synth_edge_mp3(text, edge_voice, edge_rate, mp3_tmp))
            conv = subprocess.run(
                [ffmpeg_bin, "-y", "-v", "error", "-i", str(mp3_tmp), "-ar", "44100", "-ac", "1", "-c:a", "pcm_s16le", str(out_wav)],
                capture_output=True,
                text=True,
                check=False,
            )
            if conv.returncode != 0:
                raise RuntimeError(conv.stderr.strip())
            return "edge-tts"
        except Exception as exc:
            if engine == "edge":
                raise SystemExit(f"edge-tts failed (and --engine edge disabled fallback): {exc}")
            _synth_say_wav(text, say_voice, say_rate, out_wav, ffmpeg_bin)
            return "say-fallback"

    _synth_say_wav(text, say_voice, say_rate, out_wav, ffmpeg_bin)
    return "say"


def _fmt_ts(sec: float) -> str:
    m = int(sec // 60)
    s = sec - m * 60
    return f"{m:02d}:{s:04.1f}"


def build_audio_brief(
    cards: list[dict[str, str]],
    out_path: Path,
    *,
    chapter_label: str | None = None,
    engine: str = "auto",
    edge_voice: str = DEFAULT_EDGE_VOICE,
    edge_rate: str = "+0%",
    say_voice: str = DEFAULT_SAY_VOICE,
    say_rate: int = 180,
    pause_sec: float = 2.5,
    inter_card_sec: float = 1.2,
    transcript_path: Path | None = None,
) -> dict[str, Any]:
    if not cards:
        raise SystemExit("No Q->A cards available to synthesize.")

    ffmpeg_bin = find_bin("ffmpeg")
    ffprobe_bin = find_bin("ffprobe")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="mlh-audio-brief-") as tmp:
        work = Path(tmp)
        pause_wav = work / "pause.wav"
        gap_wav = work / "gap.wav"
        _make_silence_wav(pause_sec, pause_wav, ffmpeg_bin)
        _make_silence_wav(inter_card_sec, gap_wav, ffmpeg_bin)

        concat_entries: list[Path] = []
        timeline: list[dict[str, Any]] = []
        engines_used: set[str] = set()
        cursor = 0.0
        active_engine = engine

        for idx, card in enumerate(cards, start=1):
            q_spoken = latex_to_speech(card["q"])
            a_spoken = latex_to_speech(card["a"])
            prefix = f"Question {idx}. " if len(cards) > 1 else "Question. "
            q_line = f"{prefix}{q_spoken}"
            a_line = f"Answer. {a_spoken}"

            q_wav = work / f"q_{idx:03d}.wav"
            a_wav = work / f"a_{idx:03d}.wav"

            used_q = synthesize_segment(
                q_line,
                q_wav,
                engine=active_engine,
                edge_voice=edge_voice,
                edge_rate=edge_rate,
                say_voice=say_voice,
                say_rate=say_rate,
                ffmpeg_bin=ffmpeg_bin,
            )
            engines_used.add(used_q)
            # If edge-tts fell back offline on the first clip, avoid retrying network on every remaining clip.
            if active_engine == "auto" and used_q == "say-fallback":
                active_engine = "say"

            used_a = synthesize_segment(
                a_line,
                a_wav,
                engine=active_engine,
                edge_voice=edge_voice,
                edge_rate=edge_rate,
                say_voice=say_voice,
                say_rate=say_rate,
                ffmpeg_bin=ffmpeg_bin,
            )
            engines_used.add(used_a)

            q_dur = probe_duration(q_wav, ffprobe_bin)
            a_dur = probe_duration(a_wav, ffprobe_bin)

            q_start = cursor
            q_end = q_start + q_dur
            p_start = q_end
            p_end = p_start + pause_sec
            a_start = p_end
            a_end = a_start + a_dur
            cursor = a_end

            concat_entries.extend([q_wav, pause_wav, a_wav])
            if idx < len(cards):
                concat_entries.append(gap_wav)
                cursor += inter_card_sec

            timeline.append(
                {
                    "index": idx,
                    "source": card.get("source", "cards"),
                    "qStart": round(q_start, 2),
                    "qEnd": round(q_end, 2),
                    "pauseStart": round(p_start, 2),
                    "pauseEnd": round(p_end, 2),
                    "aStart": round(a_start, 2),
                    "aEnd": round(a_end, 2),
                    "qSpoken": q_line,
                    "aSpoken": a_line,
                }
            )

        concat_list = work / "concat.txt"
        concat_lines = [f"file '{str(p).replace(chr(39), chr(39) + chr(92) + chr(39) + chr(39))}'" for p in concat_entries]
        concat_list.write_text("\n".join(concat_lines) + "\n", encoding="utf-8")

        ext = out_path.suffix.lower()
        if ext == ".m4a":
            codec_args = ["-c:a", "aac", "-b:a", "128k"]
        else:
            codec_args = ["-c:a", "libmp3lame", "-q:a", "4"]

        proc = subprocess.run(
            [
                ffmpeg_bin,
                "-y",
                "-v",
                "error",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(concat_list),
                "-ar",
                "44100",
                "-ac",
                "1",
                *codec_args,
                str(out_path),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg audio concatenation failed: {proc.stderr.strip()}")

    actual_dur = round(probe_duration(out_path, ffprobe_bin), 2)
    file_bytes = out_path.stat().st_size
    primary_engine = "edge-tts" if "edge-tts" in engines_used else ("say-fallback" if "say-fallback" in engines_used else "say")

    if transcript_path is not None:
        transcript_path.parent.mkdir(parents=True, exist_ok=True)
        t_lines = [
            f"# Audio Flash-Revision Transcript: {chapter_label or out_path.stem}",
            f"# Engine: {primary_engine} | Cards: {len(cards)} | Recall Pause: {pause_sec:.1f}s | Duration: {actual_dur:.2f}s",
            "",
        ]
        for item in timeline:
            t_lines.append(f"[{_fmt_ts(item['qStart'])} - {_fmt_ts(item['qEnd'])}] Q{item['index']}: {item['qSpoken']}")
            t_lines.append(f"[{_fmt_ts(item['pauseStart'])} - {_fmt_ts(item['pauseEnd'])}] ... recall pause ({pause_sec:.1f}s) ...")
            t_lines.append(f"[{_fmt_ts(item['aStart'])} - {_fmt_ts(item['aEnd'])}] A{item['index']}: {item['aSpoken']}")
            t_lines.append("")
        transcript_path.write_text("\n".join(t_lines), encoding="utf-8")

    result: dict[str, Any] = {
        "output": str(out_path),
        "engine": primary_engine,
        "cardsCount": len(cards),
        "pauseSec": round(float(pause_sec), 2),
        "interCardSec": round(float(inter_card_sec), 2),
        "durationSec": actual_dur,
        "bytes": file_bytes,
    }
    if transcript_path is not None:
        result["transcript"] = str(transcript_path)
    return result


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Generate a spoken Q -> Recall Pause -> A flash-revision audio track (.mp3 / .m4a) from chapter notes, mistakes, or JSON cards."
    )
    parser.add_argument("--chapter", help="Path to Markdown chapter notes (e.g., chapters/shm.md)")
    parser.add_argument("--cards", help="Path to JSON file containing [{\"q\": ..., \"a\": ...}] cards")
    parser.add_argument("--chapter-name", help="Chapter name used for matching traps in mistakes.jsonl")
    parser.add_argument("--kg-dir", help="Override knowledge-graph directory (for sandboxed testing)")
    parser.add_argument("--out", help="Output audio path (.mp3 or .m4a; default: chapters/<slug>-audio.mp3)")
    parser.add_argument("--transcript", help="Optional companion .txt transcript/timing manifest output path")
    parser.add_argument("--engine", choices=["auto", "edge", "say"], default="auto", help="TTS engine (auto = edge-tts with offline macOS say fallback)")
    parser.add_argument("--voice", default=DEFAULT_EDGE_VOICE, help=f"Edge TTS voice (default: {DEFAULT_EDGE_VOICE})")
    parser.add_argument("--rate", default="+0%", help="Edge TTS speaking rate, e.g. '-5%%' or '+0%%'")
    parser.add_argument("--say-voice", default=DEFAULT_SAY_VOICE, help=f"Offline macOS say voice (default: {DEFAULT_SAY_VOICE})")
    parser.add_argument("--say-rate", type=int, default=180, help="Offline macOS say words/minute (default: 180)")
    parser.add_argument("--pause-sec", type=float, default=2.5, help="Recall silence pause between Question and Answer in seconds (default: 2.5)")
    parser.add_argument("--inter-card-sec", type=float, default=1.2, help="Silence gap between cards in seconds (default: 1.2)")
    parser.add_argument("--max-cards", type=int, default=20, help="Maximum number of Q->A cards to synthesize (default: 20)")
    parser.add_argument("--json", action="store_true", help="Emit structured JSON output")
    args = parser.parse_args(argv)

    if not args.chapter and not args.cards and not args.chapter_name:
        raise SystemExit("Provide at least one of --chapter <md_path>, --cards <json_path>, or --chapter-name <name>.")
    if args.pause_sec < 0.1:
        raise SystemExit("--pause-sec must be >= 0.1")
    if args.max_cards < 1:
        raise SystemExit("--max-cards must be >= 1")

    env_kg = os.environ.get("SYNT_KG_DIR") or os.environ.get("MLH_KG_DIR")
    kg_dir = (
        Path(args.kg_dir).expanduser().resolve()
        if args.kg_dir
        else (Path(env_kg).expanduser().resolve() if env_kg else DEFAULT_KG_DIR.resolve())
    )
    env_ch = os.environ.get("SYNT_CHAPTERS_DIR") or os.environ.get("MLH_CHAPTERS_DIR")
    chapters_dir = Path(env_ch).expanduser().resolve() if env_ch else DEFAULT_CHAPTERS_DIR
    cards: list[dict[str, str]] = []
    chapter_names: list[str] = []
    inferred_slug = "revision"

    if args.chapter_name:
        chapter_names.append(args.chapter_name.strip())
        inferred_slug = _slugify(args.chapter_name)

    if args.cards:
        cards_path = Path(args.cards).expanduser().resolve()
        if not cards_path.exists():
            raise SystemExit(f"Cards JSON not found: {cards_path}")
        json_title, json_cards = load_cards_json(cards_path)
        cards.extend(json_cards)
        if json_title:
            chapter_names.append(json_title)
            if not args.chapter_name:
                inferred_slug = _slugify(json_title)
        elif not args.chapter_name:
            inferred_slug = _slugify(cards_path.stem)

    if args.chapter:
        md_path = Path(args.chapter).expanduser().resolve()
        if not md_path.exists():
            raise SystemExit(f"Chapter Markdown not found: {md_path}")
        md_title, md_cards = extract_cards_from_markdown(md_path)
        cards.extend(md_cards)
        chapter_names.extend([md_title, md_path.stem])
        if not args.chapter_name:
            inferred_slug = _slugify(md_path.stem)

    # Pull matching personal exam traps from <kg_dir>/mistakes.jsonl.
    if chapter_names:
        trap_cards = extract_traps_from_kg(kg_dir, chapter_names)
        if args.cards:
            # Keep explicit --cards first so caller ordering & --max-cards are deterministic.
            cards.extend(trap_cards)
        else:
            # For chapter revision tracks, put high-yield personal traps right up front!
            cards = trap_cards + cards

    selected_cards = dedupe_cards(cards, max_cards=args.max_cards)
    if not selected_cards:
        raise SystemExit("No Q->A pairs could be extracted from the supplied chapter/cards/mistakes.")

    out_path = (
        Path(args.out).expanduser().resolve()
        if args.out
        else (chapters_dir / f"{inferred_slug}-audio.mp3")
    )
    transcript_path = Path(args.transcript).expanduser().resolve() if args.transcript else None

    result = build_audio_brief(
        selected_cards,
        out_path,
        chapter_label=chapter_names[0] if chapter_names else inferred_slug,
        engine=args.engine,
        edge_voice=args.voice,
        edge_rate=args.rate,
        say_voice=args.say_voice,
        say_rate=args.say_rate,
        pause_sec=args.pause_sec,
        inter_card_sec=args.inter_card_sec,
        transcript_path=transcript_path,
    )

    if args.json or True:
        # Always emit clean JSON so CLI & automation callers parse reliably.
        print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
