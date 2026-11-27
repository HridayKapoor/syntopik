"""Zero-Install Offline Pocket Commute Deck & Pure-Stdlib QR Pairing for Syntopik v2.0.

Generates a single self-contained (~25-45 KB), zero-CDN, mobile-first HTML5
commute flashcard deck in Syntopik's Warm Obsidian / Archival Vellum palette
(with inline SVG occlusion masks, confidence rail, 4-button FSRS ratings,
offline `localStorage` queue, and 1-tap `pocket-sync.json` export), replays
`pocket-sync.json` idempotently through FSRS-6 + FIRe, and renders genuine
ISO/IEC 18004 QR codes (SVG & ASCII) in pure Python stdlib for opt-in LAN pairing.

Pure Python 3.12+ standard library only (zero external dependencies).
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import html as html_lib
from html.parser import HTMLParser
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Safe syntopik.* imports with pure-stdlib fallbacks
# ---------------------------------------------------------------------------

try:
    from syntopik.paths import resolve_kg_dir as _resolve_kg_dir
    from syntopik.paths import resolve_workspace as _resolve_workspace
    from syntopik.paths import sync_hub as _sync_hub
except Exception:  # pragma: no cover - standalone fallback
    def _resolve_workspace(explicit: Path | str | None = None) -> Path:
        if explicit is not None and str(explicit).strip():
            return Path(explicit).expanduser().resolve()
        env_ws = os.environ.get("SYNT_WORKSPACE", "").strip() or os.environ.get("MLH_WORKSPACE", "").strip()
        if env_ws:
            return Path(env_ws).expanduser().resolve()
        return Path.cwd().resolve()

    def _resolve_kg_dir(workspace: Path | str | None = None, *, explicit: Path | str | None = None) -> Path:
        if explicit is not None and str(explicit).strip():
            return Path(explicit).expanduser().resolve()
        return (_resolve_workspace(workspace) / "knowledge-graph").resolve()

    def _sync_hub(**_: Any) -> bool:
        return False


try:
    from syntopik.storage import append_jsonl_locked as _append_jsonl_locked
    from syntopik.storage import load_jsonl as _load_jsonl
    from syntopik.storage import save_jsonl_atomic as _save_jsonl_atomic
    from syntopik.storage import workspace_lock as _workspace_lock
    from syntopik.storage import write_text_atomic as _write_text_atomic
except Exception:  # pragma: no cover - standalone fallback
    def _workspace_lock(_path: Path | str, timeout: float = 10.0) -> Any:
        return contextlib.nullcontext()
    def _write_text_atomic(path: Path | str, content: str, *, encoding: str = "utf-8") -> Path:
        target = Path(path).expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(target.parent), prefix=f".{target.name}.", suffix=".tmp")
        tmp_path = Path(tmp)
        try:
            with os.fdopen(fd, "w", encoding=encoding) as fh:
                fh.write(content)
            os.replace(tmp_path, target)
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise
        return target

    def _load_jsonl(path: Path | str) -> list[dict[str, Any]]:
        p = Path(path).expanduser()
        if not p.is_file():
            return []
        rows: list[dict[str, Any]] = []
        for ln in p.read_text(encoding="utf-8").splitlines():
            s = ln.strip()
            if not s:
                continue
            try:
                obj = json.loads(s)
            except Exception:
                continue
            if isinstance(obj, dict):
                rows.append(obj)
        return rows

    def _save_jsonl_atomic(path: Path | str, rows: list[dict[str, Any]]) -> Path:
        body = "\n".join(json.dumps(r, ensure_ascii=False) for r in rows)
        return _write_text_atomic(path, (body + "\n") if body else "")

    def _append_jsonl_locked(path: Path | str, rows: dict[str, Any] | list[dict[str, Any]]) -> Path:
        target = Path(path).expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        items = [rows] if isinstance(rows, dict) else list(rows)
        if not items:
            return target
        with target.open("a", encoding="utf-8") as fh:
            for it in items:
                fh.write(json.dumps(it, ensure_ascii=False) + "\n")
        return target


try:
    from syntopik.schemas import now_iso as _now_iso
    from syntopik.schemas import today_iso as _today_iso
except Exception:  # pragma: no cover - standalone fallback
    def _now_iso() -> str:
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    def _today_iso() -> str:
        return datetime.now(timezone.utc).date().isoformat()


try:
    from syntopik.fsrs_core import RATING_TO_INT as _RATING_TO_INT
    from syntopik.fsrs_core import RATING_TRANSIT as _RATING_TRANSIT
    from syntopik.fsrs_core import apply_fire_credit as _apply_fire_credit
    from syntopik.fsrs_core import assess_confidence_signal as _assess_confidence_signal
    from syntopik.fsrs_core import bkt_update as _bkt_update
    from syntopik.fsrs_core import card_retrievability as _card_retrievability
    from syntopik.fsrs_core import load_fsrs_parameters as _load_fsrs_parameters
    from syntopik.fsrs_core import review_card_dict as _review_card_dict
except Exception:  # pragma: no cover - standalone fallback
    _RATING_TO_INT = {"again": 1, "hard": 2, "good": 3, "easy": 4}
    _RATING_TRANSIT = {"again": 0.05, "hard": 0.10, "good": 0.25, "easy": 0.35}

    def _load_fsrs_parameters(kg_dir: Path | str | None = None) -> tuple[float, ...]:
        return ()

    def _card_retrievability(card_dict: dict[str, Any] | None, now: datetime | None = None, **_: Any) -> float:
        return 0.5

    def _bkt_update(prior: float, correct: bool, p_transit: float = 0.25, **_: Any) -> float:
        p = max(0.01, min(0.99, float(prior)))
        num = p * (0.9 if correct else 0.1)
        den = num + (1.0 - p) * (0.2 if correct else 0.8)
        post = num / den if den else p
        return round(max(0.01, min(0.995, post + (1.0 - post) * p_transit)), 3)

    def _assess_confidence_signal(correct: bool, learner_confidence: float | None) -> dict[str, Any]:
        if learner_confidence is None:
            return {"learnerConfidence": None, "hypercorrection": False, "luckyGuess": False, "brierError": None}
        c = max(0.0, min(1.0, float(learner_confidence)))
        y = 1.0 if correct else 0.0
        return {
            "learnerConfidence": round(c, 4),
            "hypercorrection": bool((not correct) and c >= 0.75),
            "luckyGuess": bool(correct and c <= 0.35),
            "brierError": round((c - y) ** 2, 4),
        }

    def _review_card_dict(card_dict: dict[str, Any] | None, rating: str | int, now: datetime | None = None, **_: Any) -> tuple[dict[str, Any], dict[str, Any]]:
        ref_now = now or datetime.now(timezone.utc)
        r = str(rating).strip().lower()
        prev_s = float((card_dict or {}).get("stability") or 1.0)
        prev_d = float((card_dict or {}).get("difficulty") or 5.0)
        mult = {"again": 0.4, "hard": 1.15, "good": 1.9, "easy": 2.6}.get(r, 1.9)
        new_s = max(0.25, prev_s * mult)
        ivl = max(1, int(round(new_s)))
        due_dt = ref_now + timedelta(days=ivl)
        return {
            "card_id": int((card_dict or {}).get("card_id") or int(ref_now.timestamp() * 1000)),
            "state": 2,
            "step": None,
            "stability": round(new_s, 4),
            "difficulty": round(prev_d, 4),
            "due": due_dt.isoformat(),
            "last_review": ref_now.isoformat(),
        }, {"intervalDays": ivl, "elapsedDays": 0.0, "predictedR": None, "backend": "fallback"}

    def _apply_fire_credit(*_: Any, **__: Any) -> list[dict[str, Any]]:
        return []


__all__ = [
    "export_pocket_html",
    "import_pocket_sync",
    "sanitize_svg",
    "encode_qr_matrix",
    "render_qr_svg",
    "render_qr_ascii",
    "main",
]


# ---------------------------------------------------------------------------
# Pure-Stdlib ISO/IEC 18004 QR Code Encoder (Byte Mode, EC Level L, v1-v10)
# ---------------------------------------------------------------------------

# (version, total_codewords, ec_codewords_per_block, num_blocks_g1, data_cw_g1, num_blocks_g2, data_cw_g2, align_coords)
_QR_VERSION_TABLE: tuple[tuple[int, int, int, int, int, int, int, tuple[int, ...]], ...] = (
    (1, 26, 7, 1, 19, 0, 0, ()),
    (2, 44, 10, 1, 34, 0, 0, (6, 18)),
    (3, 70, 15, 1, 55, 0, 0, (6, 22)),
    (4, 100, 20, 1, 80, 0, 0, (6, 26)),
    (5, 134, 26, 1, 108, 0, 0, (6, 30)),
    (6, 172, 18, 2, 68, 0, 0, (6, 34)),
    (7, 196, 20, 2, 78, 0, 0, (6, 22, 38)),
    (8, 242, 24, 2, 97, 0, 0, (6, 24, 42)),
    (9, 292, 30, 2, 116, 0, 0, (6, 26, 46)),
    (10, 346, 18, 2, 68, 2, 69, (6, 28, 50)),
)

# Precomputed GF(256) log/exp tables with primitive polynomial x^8 + x^4 + x^3 + x^2 + 1 (0x11D)
_GF_EXP = [0] * 512
_GF_LOG = [0] * 256
_x = 1
for _i in range(255):
    _GF_EXP[_i] = _x
    _GF_LOG[_x] = _i
    _x <<= 1
    if _x & 0x100:
        _x ^= 0x11D
for _i in range(255, 512):
    _GF_EXP[_i] = _GF_EXP[_i - 255]


def _gf_mul(a: int, b: int) -> int:
    if a == 0 or b == 0:
        return 0
    return _GF_EXP[_GF_LOG[a] + _GF_LOG[b]]


def _rs_generator_poly(degree: int) -> list[int]:
    poly = [1]
    for i in range(degree):
        root = _GF_EXP[i]
        nxt = [0] * (len(poly) + 1)
        for j, coef in enumerate(poly):
            nxt[j] ^= coef
            nxt[j + 1] ^= _gf_mul(coef, root)
        poly = nxt
    return poly


def _rs_remainder(data: list[int], ec_len: int) -> list[int]:
    gen = _rs_generator_poly(ec_len)
    buf = list(data) + [0] * ec_len
    for i in range(len(data)):
        factor = buf[i]
        if factor != 0:
            for j in range(1, len(gen)):
                buf[i + j] ^= _gf_mul(gen[j], factor)
    return buf[len(data) :]


def _qr_format_bits(mask_id: int) -> int:
    # EC Level L = 0b01; 5-bit data = (0b01 << 3) | mask_id
    data = (0b01 << 3) | (mask_id & 0x7)
    rem = data << 10
    gen = 0b10100110111
    for bit in range(14, 9, -1):
        if rem & (1 << bit):
            rem ^= gen << (bit - 10)
    return ((data << 10) | rem) ^ 0b101010000010010


def _qr_version_bits(version: int) -> int:
    rem = version << 12
    gen = 0b1111100100101
    for bit in range(17, 11, -1):
        if rem & (1 << bit):
            rem ^= gen << (bit - 12)
    return (version << 12) | rem


def _mask_pred(mask_id: int, r: int, c: int) -> bool:
    if mask_id == 0:
        return (r + c) % 2 == 0
    if mask_id == 1:
        return r % 2 == 0
    if mask_id == 2:
        return c % 3 == 0
    if mask_id == 3:
        return (r + c) % 3 == 0
    if mask_id == 4:
        return ((r // 2) + (c // 3)) % 2 == 0
    if mask_id == 5:
        return ((r * c) % 2 + (r * c) % 3) == 0
    if mask_id == 6:
        return (((r * c) % 2 + (r * c) % 3) % 2) == 0
    return (((r + c) % 2 + (r * c) % 3) % 2) == 0


def _qr_penalty(mat: list[list[int]]) -> int:
    n = len(mat)
    score = 0
    # Rule 1: adjacent runs >= 5
    for line in mat:
        run = 1
        for i in range(1, n):
            if line[i] == line[i - 1]:
                run += 1
            else:
                if run >= 5:
                    score += run - 2
                run = 1
        if run >= 5:
            score += run - 2
    for c in range(n):
        run = 1
        for r in range(1, n):
            if mat[r][c] == mat[r - 1][c]:
                run += 1
            else:
                if run >= 5:
                    score += run - 2
                run = 1
        if run >= 5:
            score += run - 2
    # Rule 2: 2x2 blocks
    for r in range(n - 1):
        for c in range(n - 1):
            v = mat[r][c]
            if v == mat[r][c + 1] == mat[r + 1][c] == mat[r + 1][c + 1]:
                score += 3
    # Rule 4: dark/light balance
    dark = sum(sum(row) for row in mat)
    pct = abs((dark * 100) / (n * n) - 50.0)
    score += int(pct // 5) * 10
    return score


def encode_qr_matrix(payload: str) -> list[list[int]]:
    """Encode a UTF-8 URL or string into a scannable ISO/IEC 18004 QR boolean matrix (0/1)."""
    raw = str(payload or "").encode("utf-8")
    if not raw:
        raise ValueError("QR payload cannot be empty.")

    chosen = None
    for spec in _QR_VERSION_TABLE:
        ver, _tot, _ec, g1_b, g1_d, g2_b, g2_d, _align = spec
        data_cap = g1_b * g1_d + g2_b * g2_d
        count_bits = 8 if ver <= 9 else 16
        needed_bits = 4 + count_bits + len(raw) * 8
        if (needed_bits + 7) // 8 <= data_cap:
            chosen = spec
            break
    if chosen is None:
        raise ValueError(f"QR payload too long ({len(raw)} bytes; max 271 bytes supported).")

    ver, _tot, ec_per_block, g1_b, g1_d, g2_b, g2_d, align_coords = chosen
    data_cap = g1_b * g1_d + g2_b * g2_d
    count_bits = 8 if ver <= 9 else 16

    # Build bit stream: Mode 0100 (Byte) + char count + bytes + terminator + pad bytes
    bits: list[int] = []
    def _push(val: int, width: int) -> None:
        for shift in range(width - 1, -1, -1):
            bits.append((val >> shift) & 1)

    _push(0b0100, 4)
    _push(len(raw), count_bits)
    for b in raw:
        _push(b, 8)
    cap_bits = data_cap * 8
    term = min(4, cap_bits - len(bits))
    _push(0, term)
    while len(bits) % 8 != 0:
        bits.append(0)
    codewords = [
        int("".join(str(bit) for bit in bits[i : i + 8]), 2)
        for i in range(0, len(bits), 8)
    ]
    pad_cycle = (0xEC, 0x11)
    pidx = 0
    while len(codewords) < data_cap:
        codewords.append(pad_cycle[pidx % 2])
        pidx += 1

    # Split into blocks and compute Reed-Solomon EC codewords
    data_blocks: list[list[int]] = []
    ec_blocks: list[list[int]] = []
    pos = 0
    for count, size in ((g1_b, g1_d), (g2_b, g2_d)):
        for _ in range(count):
            blk = codewords[pos : pos + size]
            pos += size
            data_blocks.append(blk)
            ec_blocks.append(_rs_remainder(blk, ec_per_block))

    # Interleave data + EC codewords
    interleaved: list[int] = []
    max_d = max(len(b) for b in data_blocks)
    for i in range(max_d):
        for blk in data_blocks:
            if i < len(blk):
                interleaved.append(blk[i])
    for i in range(ec_per_block):
        for blk in ec_blocks:
            interleaved.append(blk[i])

    stream_bits: list[int] = []
    for cw in interleaved:
        for shift in range(7, -1, -1):
            stream_bits.append((cw >> shift) & 1)

    size = 21 + 4 * (ver - 1)
    mat = [[0] * size for _ in range(size)]
    reserved = [[False] * size for _ in range(size)]

    def _set_func(r: int, c: int, val: int) -> None:
        if 0 <= r < size and 0 <= c < size:
            mat[r][c] = val
            reserved[r][c] = True

    # Finder patterns + separators
    for fr, fc in ((0, 0), (0, size - 7), (size - 7, 0)):
        for dr in range(-1, 8):
            for dc in range(-1, 8):
                r, c = fr + dr, fc + dc
                if not (0 <= r < size and 0 <= c < size):
                    continue
                if 0 <= dr <= 6 and 0 <= dc <= 6:
                    on = (
                        dr in (0, 6)
                        or dc in (0, 6)
                        or (2 <= dr <= 4 and 2 <= dc <= 4)
                    )
                    _set_func(r, c, 1 if on else 0)
                else:
                    _set_func(r, c, 0)

    # Alignment patterns
    for ar in align_coords:
        for ac in align_coords:
            if reserved[ar][ac]:
                continue
            for dr in range(-2, 3):
                for dc in range(-2, 3):
                    on = max(abs(dr), abs(dc)) != 1
                    _set_func(ar + dr, ac + dc, 1 if on else 0)

    # Timing patterns
    for i in range(8, size - 8):
        if not reserved[6][i]:
            _set_func(6, i, 1 if i % 2 == 0 else 0)
        if not reserved[i][6]:
            _set_func(i, 6, 1 if i % 2 == 0 else 0)

    # Dark module + reserve format info areas
    _set_func(4 * ver + 9, 8, 1)
    for i in range(9):
        if i != 6:
            reserved[8][i] = True
            reserved[i][8] = True
    for i in range(8):
        reserved[8][size - 1 - i] = True
        reserved[size - 1 - i][8] = True

    # Version info for v >= 7
    if ver >= 7:
        vbits = _qr_version_bits(ver)
        for i in range(18):
            bit = (vbits >> i) & 1
            r = i // 3
            c = size - 11 + (i % 3)
            _set_func(r, c, bit)
            _set_func(c, r, bit)

    # Zig-zag data placement
    bit_idx = 0
    col = size - 1
    upward = True
    while col > 0:
        if col == 6:
            col -= 1
        rows = range(size - 1, -1, -1) if upward else range(size)
        for r in rows:
            for dc in (0, -1):
                c = col + dc
                if not reserved[r][c]:
                    mat[r][c] = stream_bits[bit_idx] if bit_idx < len(stream_bits) else 0
                    bit_idx += 1
        col -= 2
        upward = not upward

    def _apply_format(grid: list[list[int]], mask_id: int) -> None:
        fbits = _qr_format_bits(mask_id)
        # Around top-left
        coords_a = [
            (8, 0), (8, 1), (8, 2), (8, 3), (8, 4), (8, 5), (8, 7), (8, 8),
            (7, 8), (5, 8), (4, 8), (3, 8), (2, 8), (1, 8), (0, 8),
        ]
        coords_b = [
            (size - 1, 8), (size - 2, 8), (size - 3, 8), (size - 4, 8),
            (size - 5, 8), (size - 6, 8), (size - 7, 8),
            (8, size - 8), (8, size - 7), (8, size - 6), (8, size - 5),
            (8, size - 4), (8, size - 3), (8, size - 2), (8, size - 1),
        ]
        for i in range(15):
            bit = (fbits >> (14 - i)) & 1
            ra, ca = coords_a[i]
            rb, cb = coords_b[i]
            grid[ra][ca] = bit
            grid[rb][cb] = bit

    best_mat: list[list[int]] | None = None
    best_pen = 10**9
    for mask_id in range(8):
        cand = [row[:] for row in mat]
        for r in range(size):
            for c in range(size):
                if not reserved[r][c] and _mask_pred(mask_id, r, c):
                    cand[r][c] ^= 1
        _apply_format(cand, mask_id)
        pen = _qr_penalty(cand)
        if pen < best_pen:
            best_pen = pen
            best_mat = cand

    assert best_mat is not None
    return best_mat


def render_qr_svg(
    url: str,
    *,
    module_size: int = 6,
    quiet_zone: int = 4,
    dark: str = "#1C2024",
    light: str = "#F7F5F0",
) -> str:
    """Render a scannable zero-dependency SVG QR code for a pairing URL."""
    mat = encode_qr_matrix(url)
    n = len(mat)
    qz = max(1, int(quiet_zone))
    ms = max(2, int(module_size))
    dim = (n + 2 * qz) * ms

    rects: list[str] = []
    for r, row in enumerate(mat):
        y = (r + qz) * ms
        c = 0
        while c < n:
            if row[c]:
                start = c
                while c < n and row[c]:
                    c += 1
                rects.append(f'<rect x="{(start + qz) * ms}" y="{y}" width="{(c - start) * ms}" height="{ms}"/>')
            else:
                c += 1

    safe_label = html_lib.escape(url, quote=True)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {dim} {dim}" '
        f'width="{dim}" height="{dim}" role="img" aria-label="QR code for {safe_label}" shape-rendering="crispEdges">'
        f'<rect width="100%" height="100%" fill="{html_lib.escape(light, quote=True)}"/>'
        f'<g fill="{html_lib.escape(dark, quote=True)}">{"".join(rects)}</g>'
        f"</svg>"
    )


def render_qr_ascii(url: str, *, invert: bool = False, quiet_zone: int = 2) -> str:
    """Render a compact terminal-scannable Unicode half-block QR code for a URL."""
    mat = encode_qr_matrix(url)
    n = len(mat)
    qz = max(1, int(quiet_zone))
    pad_row = [0] * (n + 2 * qz)
    padded = [pad_row[:] for _ in range(qz)] + [
        ([0] * qz) + row[:] + ([0] * qz) for row in mat
    ] + [pad_row[:] for _ in range(qz)]
    if invert:
        padded = [[1 - v for v in row] for row in padded]
    if len(padded) % 2 == 1:
        padded.append([1 if invert else 0] * len(padded[0]))

    # Use Unicode upper/lower/full half-blocks so QR stays square in terminal fonts
    lines: list[str] = []
    for r in range(0, len(padded), 2):
        top = padded[r]
        bot = padded[r + 1]
        chars: list[str] = []
        for t, b in zip(top, bot):
            if t and b:
                chars.append("█")
            elif t and not b:
                chars.append("▀")
            elif not t and b:
                chars.append("▄")
            else:
                chars.append(" ")
        lines.append("".join(chars))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Offline Pocket Commute Deck HTML Generator (Zero-CDN, Mobile-First)
# ---------------------------------------------------------------------------

_POCKET_HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en" data-theme="dark">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover" />
<meta name="apple-mobile-web-app-capable" content="yes" />
<link rel="icon" type="image/svg+xml" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='8' fill='%2336538C'/%3E%3Ctext x='16' y='21.5' text-anchor='middle' font-family='system-ui,sans-serif' font-size='15' font-weight='800' fill='%23FCFBF8'%3ESY%3C/text%3E%3C/svg%3E" />
<title>__TITLE__</title>
<style>
:root,[data-theme="dark"]{
  --bg:#111315;--bg-elev:#171A1D;--bg-card:#1E2227;--bg-input:#252A30;
  --border:#2A3038;--border-strong:#3B434E;--text:#E8E6E1;--text-2:#A8B0BC;--muted:#8E97A4;
  --solid:#4E9F76;--solid-bg:rgba(78,159,118,.16);
  --shaky:#D4943A;--shaky-bg:rgba(212,148,58,.16);
  --mis:#C85A53;--mis-bg:rgba(200,90,83,.16);
  --accent:#7C93C3;--accent-bg:rgba(124,147,195,.16);
  --solid-ink:#6FC496;--shaky-ink:#E2A85A;--mis-ink:#E88880;--accent-ink:#9DB0DA;
  --font-prose:"Newsreader","Charter",Georgia,serif;
  --font-ui:-apple-system,BlinkMacSystemFont,"SF Pro Text",system-ui,sans-serif;
  --font-mono:"SF Mono",ui-monospace,monospace;
}
[data-theme="light"]{
  --bg:#F7F5F0;--bg-elev:#FCFBF8;--bg-card:#FFFFFF;--bg-input:#EFECE4;
  --border:#E2DDD2;--border-strong:#C8C1B2;--text:#1C2024;--text-2:#495057;--muted:#5E656D;
  --solid:#256B48;--solid-bg:rgba(37,107,72,.12);
  --shaky:#A86814;--shaky-bg:rgba(168,104,20,.12);
  --mis:#B23A34;--mis-bg:rgba(178,58,52,.12);
  --accent:#36538C;--accent-bg:rgba(54,83,140,.12);
  --solid-ink:#256B48;--shaky-ink:#8A5410;--mis-ink:#B23A34;--accent-ink:#36538C;
}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent;}
html,body{margin:0;padding:0;min-height:100%;background:var(--bg);color:var(--text);font-family:var(--font-ui);font-variant-numeric:tabular-nums;}
body{display:flex;flex-direction:column;min-height:100dvh;padding:env(safe-area-inset-top) 14px env(safe-area-inset-bottom) 14px;}
.hdr{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:12px 2px 8px;max-width:560px;width:100%;margin:0 auto;}
.brand{display:flex;align-items:center;gap:8px;min-width:0;}
.mark{width:28px;height:28px;border-radius:8px;background:var(--accent);color:#fff;display:grid;place-items:center;font-weight:700;font-size:13px;flex-shrink:0;}
.btxt{display:flex;flex-direction:column;min-width:0;}
.btxt strong,.btxt h1{font-size:13.5px;margin:0;font-weight:700;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.btxt span{font-size:11px;color:var(--muted);}
.hbtns{display:flex;gap:6px;flex-shrink:0;}
.ibtn{background:var(--bg-elev);color:var(--text-2);border:1px solid var(--border);border-radius:9px;padding:7px 10px;font-size:12px;font-weight:600;cursor:pointer;}
.ibtn:active{transform:scale(.97);}
.prog-wrap{max-width:560px;width:100%;margin:0 auto 10px;}
.prog-bar{height:4px;background:var(--bg-input);border-radius:99px;overflow:hidden;}
.prog-fill{height:100%;background:var(--accent);width:0%;transition:width .25s ease;}
.prog-meta{display:flex;justify-content:space-between;font-size:11.5px;color:var(--muted);margin-top:5px;}
main{flex:1;display:flex;flex-direction:column;justify-content:center;max-width:560px;width:100%;margin:0 auto;padding-bottom:14px;}
.card{background:var(--bg-card);border:1px solid var(--border);border-radius:16px;padding:20px 18px;box-shadow:0 8px 26px rgba(0,0,0,.18);display:flex;flex-direction:column;gap:14px;touch-action:pan-y;}
.meta-row{display:flex;align-items:center;justify-content:space-between;gap:8px;flex-wrap:wrap;}
.pill{font-size:11px;font-weight:600;padding:3px 9px;border-radius:99px;border:1px solid var(--border);color:var(--text-2);background:var(--bg-elev);}
.pill.solid{color:var(--solid-ink);background:var(--solid-bg);border-color:transparent;}
.pill.shaky{color:var(--shaky-ink);background:var(--shaky-bg);border-color:transparent;}
.pill.misconception{color:var(--mis-ink);background:var(--mis-bg);border-color:transparent;}
.q-label{font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);font-weight:600;}
.q-text{font-family:var(--font-prose);font-size:19px;line-height:1.45;margin:0;color:var(--text);}
.a-box{border-top:1px solid var(--border);padding-top:14px;display:none;flex-direction:column;gap:10px;}
.a-box.open{display:flex;}
.a-text{font-family:var(--font-prose);font-size:17px;line-height:1.5;margin:0;color:var(--text);}
.occ-wrap{position:relative;border:1px solid var(--border);border-radius:11px;overflow:hidden;background:var(--bg-elev);}
.occ-wrap svg,.occ-wrap img.occ-bg{display:block;width:100%;height:auto;}
.occ-wrap svg.occ-overlay{position:absolute;inset:0;width:100%;height:100%;}
.occ-rect{fill:var(--accent);fill-opacity:1;stroke:var(--bg-card);stroke-width:2;cursor:pointer;transition:fill-opacity .2s,fill .2s;}
.occ-rect.revealed{fill:var(--solid);fill-opacity:.10;stroke:var(--solid);stroke-dasharray:4 3;}
.occ-rect.no-bg.revealed{fill:var(--solid-bg);fill-opacity:1;stroke-dasharray:none;}
.occ-lbl{font-size:11px;fill:#fff;font-weight:700;pointer-events:none;text-anchor:middle;dominant-baseline:central;}
.occ-lbl.revealed{fill:var(--solid);}
.occ-lbl.has-bg.revealed{display:none;}
.occ-chips{display:flex;flex-wrap:wrap;gap:6px;margin-top:6px;}
.occ-chips:empty{display:none;}
.conf-box{background:var(--bg-elev);border:1px solid var(--border);border-radius:12px;padding:10px 12px;}
.conf-hdr{display:flex;justify-content:space-between;font-size:12px;color:var(--text-2);margin-bottom:6px;}
.conf-slider{width:100%;accent-color:var(--accent);height:28px;}
.reveal-btn{width:100%;padding:14px;border-radius:12px;border:1px solid var(--accent);background:var(--accent-bg);color:var(--text);font-size:15px;font-weight:650;cursor:pointer;}
.reveal-btn:active{transform:scale(.99);}
.rate-grid{display:none;grid-template-columns:repeat(4,1fr);gap:8px;}
.rate-grid.open{display:grid;}
.rbtn{padding:12px 6px;border-radius:11px;border:1px solid var(--border);background:var(--bg-elev);color:var(--text);font-size:13px;font-weight:650;cursor:pointer;display:flex;flex-direction:column;align-items:center;gap:2px;}
.rbtn small{font-size:10.5px;font-weight:500;color:var(--muted);}
.rbtn.again{border-color:var(--mis);background:var(--mis-bg);color:var(--mis-ink);}
.rbtn.hard{border-color:var(--shaky);background:var(--shaky-bg);color:var(--shaky-ink);}
.rbtn.good{border-color:var(--solid);background:var(--solid-bg);color:var(--solid-ink);}
.rbtn.easy{border-color:var(--accent);background:var(--accent-bg);color:var(--accent-ink);}
.rbtn:active{transform:scale(.97);}
.done-card{text-align:center;padding:28px 18px;gap:14px;}
.done-card h2{margin:0;font-family:var(--font-prose);font-size:22px;}
.done-card p{margin:0;font-size:14px;color:var(--text-2);line-height:1.5;}
.sync-bar{max-width:560px;width:100%;margin:0 auto;display:flex;gap:8px;padding:8px 0 6px;flex-wrap:wrap;}
.sbtn{flex:1;min-width:140px;padding:11px 14px;border-radius:11px;border:1px solid var(--border-strong);background:var(--bg-elev);color:var(--text);font-size:13px;font-weight:650;cursor:pointer;}
.sbtn.primary{border-color:var(--solid);background:var(--solid-bg);color:var(--solid-ink);}
.toast{position:fixed;bottom:20px;left:50%;transform:translateX(-50%);background:var(--bg-card);border:1px solid var(--border-strong);color:var(--text);padding:8px 14px;border-radius:99px;font-size:12.5px;opacity:0;pointer-events:none;transition:opacity .2s;}
.toast.show{opacity:1;}
.hint{font-size:11.5px;color:var(--muted);text-align:center;margin-top:4px;}
code{font-family:var(--font-mono);font-size:.9em;background:var(--bg-input);padding:1px 5px;border-radius:5px;}
</style>
</head>
<body>
<header class="hdr">
  <div class="brand">
    <div class="mark">S</div>
    <div class="btxt">
      <h1 id="deckTitle">__DECK_HEADER__</h1>
      <span id="deckSub">Offline Pocket Review · Zero CDN</span>
    </div>
  </div>
  <div class="hbtns">
    <button class="ibtn" id="undoBtn" type="button" title="Undo last rating">Undo</button>
    <button class="ibtn" id="themeBtn" type="button" title="Toggle theme">Theme</button>
  </div>
</header>
<section class="prog-wrap" aria-label="Progress">
  <div class="prog-bar"><div class="prog-fill" id="progFill"></div></div>
  <div class="prog-meta"><span id="progTxt">0 / 0</span><span id="queuedTxt">0 queued</span></div>
</section>
<main id="stage"></main>
<footer class="sync-bar">
  <button class="sbtn primary" id="downloadBtn" type="button">Download pocket-sync.json</button>
  <button class="sbtn" id="lanSyncBtn" type="button" style="display:none;">Push to LAN Studio</button>
  <button class="sbtn" id="resetBtn" type="button">Restart Deck</button>
</footer>
<div class="toast" id="toast"></div>
<script>
(function(){
"use strict";
const DECK = __DECK_JSON__;
const STORE_KEY = "syntopik_pocket_queue_" + (DECK.deckId || "default");
const THEME_KEY = "syntopik_pocket_theme";
const root = document.documentElement;
const stage = document.getElementById("stage");
const progFill = document.getElementById("progFill");
const progTxt = document.getElementById("progTxt");
const queuedTxt = document.getElementById("queuedTxt");
const toastEl = document.getElementById("toast");
const lanBtn = document.getElementById("lanSyncBtn");

let queue = [];
try { queue = JSON.parse(localStorage.getItem(STORE_KEY) || "[]"); if (!Array.isArray(queue)) queue = []; } catch(e){ queue = []; }
const savedTheme = localStorage.getItem(THEME_KEY);
if (savedTheme === "light" || savedTheme === "dark") root.setAttribute("data-theme", savedTheme);

let idx = 0;
let revealed = false;
let cardStartMs = Date.now();
let touchStartX = null;

// Skip cards already rated in this localStorage session
const ratedKeys = new Set(queue.map(r => r.propId + "::" + (r.cueId || "default")));
while (idx < DECK.cards.length && ratedKeys.has(DECK.cards[idx].propId + "::" + (DECK.cards[idx].cueId || "default"))) idx++;

if (DECK.apiBase || (window.location && /^https?:/.test(window.location.protocol))) {
  lanBtn.style.display = "inline-block";
}

function toast(msg){
  toastEl.textContent = msg;
  toastEl.classList.add("show");
  clearTimeout(toastEl._t);
  toastEl._t = setTimeout(() => toastEl.classList.remove("show"), 2200);
}
function saveQueue(){
  try { localStorage.setItem(STORE_KEY, JSON.stringify(queue)); } catch(e){}
  queuedTxt.textContent = queue.length + " queued";
}
function esc(s){
  return String(s == null ? "" : s).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
}
function prettyMath(raw){
  let s = esc(raw);
  const sym = {
    "\\\\omega":"ω","\\\\Omega":"Ω","\\\\pi":"π","\\\\theta":"θ","\\\\phi":"φ","\\\\alpha":"α",
    "\\\\beta":"β","\\\\gamma":"γ","\\\\Delta":"Δ","\\\\delta":"δ","\\\\lambda":"λ","\\\\mu":"μ",
    "\\\\sigma":"σ","\\\\tau":"τ","\\\\pm":"±","\\\\le":"≤","\\\\ge":"≥","\\\\neq":"≠",
    "\\\\to":"→","\\\\Rightarrow":"⇒","\\\\infty":"∞","\\\\times":"×","\\\\cdot":"·","\\\\approx":"≈"
  };
  for (const [k,v] of Object.entries(sym)) s = s.replace(new RegExp(k, "g"), v);
  s = s.replace(/\\frac\{([^{}]+)\}\{([^{}]+)\}/g, "($1)/($2)");
  s = s.replace(/\\sqrt\{([^{}]+)\}/g, "√($1)");
  s = s.replace(/\$\$([\s\S]+?)\$\$/g, "<code>$1</code>");
  s = s.replace(/\$([^$]+?)\$/g, "<code>$1</code>");
  s = s.replace(/\^([0-9a-zA-Z]+)/g, "<sup>$1</sup>");
  s = s.replace(/_([0-9a-zA-Z]+)/g, "<sub>$1</sub>");
  return s;
}
function makeReviewId(propId, cueId, ts){
  const src = propId + "|" + cueId + "|" + ts + "|" + queue.length;
  let h1 = 0x811c9dc5, h2 = 0x01000193;
  for (let i = 0; i < src.length; i++){
    const ch = src.charCodeAt(i);
    h1 = Math.imul(h1 ^ ch, 0x01000193) >>> 0;
    h2 = Math.imul(h2 ^ (ch + i), 0x27d4eb2d) >>> 0;
  }
  return ("00000000" + h1.toString(16)).slice(-8) + ("0000" + (h2 & 0xffff).toString(16)).slice(-4);
}
function sanitizeSvgClient(raw){
  let s = String(raw == null ? "" : raw);
  s = s.replace(/<\?[\s\S]*?\?>/g, "").replace(/<!DOCTYPE[\s\S]*?>/gi, "").replace(/<!ENTITY[\s\S]*?>/gi, "");
  s = s.replace(/<(script|foreignObject|iframe|object|embed|style|animate\w*|set|handler|listener)\b[^>]*>[\s\S]*?<\/\1>/gi, "");
  s = s.replace(/<(script|foreignObject|iframe|object|embed|style|animate\w*|set|handler|listener)\b[^>]*\/?>/gi, "");
  s = s.replace(/\s+on[a-z0-9_-]+\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>]+)/gi, "");
  s = s.replace(/\s+(?:xlink:)?href\s*=\s*(?:"\s*(?:javascript|vbscript|data):[^"]*"|'\s*(?:javascript|vbscript|data):[^']*'|[^\s>]*javascript:[^\s>]*)/gi, "");
  return s.trim();
}
function renderOcclusion(card){
  const occ = Array.isArray(card.occlusion) ? card.occlusion : [];
  const rawSvg = typeof card.svg === "string" ? sanitizeSvgClient(card.svg) : "";
  if (!occ.length && !rawSvg) return "";
  const w = Number(card.svgWidth || 360), h = Number(card.svgHeight || 200);
  const hasBg = Boolean(rawSvg);
  const masks = occ.map((m, i) => {
    const x = Number(m.x || 10), y = Number(m.y || 10), rw = Number(m.w || 80), rh = Number(m.h || 28);
    const lbl = esc(m.label || ("Mask " + (i + 1)));
    const cls = revealed ? " revealed" : "";
    const rectCls = (hasBg ? "" : " no-bg") + cls;
    const lblCls = (hasBg ? " has-bg" : "") + cls;
    const txt = (revealed && !hasBg) ? lbl : (revealed ? "" : "?");
    return '<g class="occ-item" data-occ="'+i+'" data-has-bg="'+(hasBg ? "1" : "0")+'"><rect class="occ-rect'+rectCls+'" x="'+x+'" y="'+y+'" width="'+rw+'" height="'+rh+'" rx="5"/><text class="occ-lbl'+lblCls+'" x="'+(x+rw/2)+'" y="'+(y+rh/2)+'">'+txt+'</text></g>';
  }).join("");
  const chips = occ.length ? '<div class="occ-chips" id="occChips"></div>' : "";
  if (rawSvg){
    let svgDoc = rawSvg;
    if (!/^<svg[\s>]/i.test(svgDoc)){
      svgDoc = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 '+w+' '+h+'">' + svgDoc + '</svg>';
    } else if (!/\bxmlns\s*=/i.test(svgDoc)){
      svgDoc = svgDoc.replace(/^<svg\b/i, '<svg xmlns="http://www.w3.org/2000/svg"');
    }
    const dataUri = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(svgDoc).replace(/'/g, "%27").replace(/"/g, "%22");
    const overlay = occ.length ? '<svg class="occ-overlay" viewBox="0 0 '+w+' '+h+'">' + masks + '</svg>' : "";
    return '<div class="occ-wrap"><img class="occ-bg" src="' + dataUri + '" alt="Card diagram" />' + overlay + '</div>' + chips;
  }
  const bg = '<rect x="0" y="0" width="'+w+'" height="'+h+'" fill="var(--bg-elev)"/>';
  return '<div class="occ-wrap"><svg viewBox="0 0 '+w+' '+h+'">'+bg+masks+'</svg></div>' + chips;
}
function render(){
  const total = DECK.cards.length;
  const doneN = Math.min(idx, total);
  progFill.style.width = total ? ((doneN / total) * 100).toFixed(1) + "%" : "100%";
  progTxt.textContent = doneN + " / " + total;
  queuedTxt.textContent = queue.length + " queued";

  if (!total || idx >= total){
    stage.innerHTML =
      '<section class="card done-card">' +
        '<h2>Session complete</h2>' +
        '<p>' + queue.length + ' review(s) saved offline in this browser. Tap <strong>Download pocket-sync.json</strong> and run <code>syntopik import --pocket-sync pocket-sync.json</code> back on your computer.</p>' +
      '</section>';
    return;
  }

  const card = DECK.cards[idx];
  revealed = false;
  cardStartMs = Date.now();
  const st = esc(card.status || "planned");
  stage.innerHTML =
    '<article class="card" id="activeCard">' +
      '<div class="meta-row">' +
        '<span class="pill ' + st + '">' + st + '</span>' +
        '<span class="pill">' + esc(card.subject || "General") + (card.chapter ? " · " + esc(card.chapter) : "") + '</span>' +
      '</div>' +
      '<div class="q-label">Recall Prompt</div>' +
      '<p class="q-text">' + prettyMath(card.q) + '</p>' +
      renderOcclusion(card) +
      '<div class="conf-box">' +
        '<div class="conf-hdr"><span>Confidence before reveal</span><strong id="confVal">65%</strong></div>' +
        '<input class="conf-slider" id="confSlider" aria-label="Confidence before reveal" type="range" min="0" max="100" step="5" value="65" />' +
      '</div>' +
      '<button class="reveal-btn" id="revealBtn" type="button">Show Answer</button>' +
      '<div class="a-box" id="aBox">' +
        '<div class="q-label">Unconditional Truth / Answer</div>' +
        '<p class="a-text">' + prettyMath(card.a) + '</p>' +
      '</div>' +
      '<div class="rate-grid" id="rateGrid">' +
        '<button class="rbtn again" data-rate="again" type="button">Again<small>1</small></button>' +
        '<button class="rbtn hard" data-rate="hard" type="button">Hard<small>2</small></button>' +
        '<button class="rbtn good" data-rate="good" type="button">Good<small>3</small></button>' +
        '<button class="rbtn easy" data-rate="easy" type="button">Easy<small>4</small></button>' +
      '</div>' +
      '<div class="hint">Tap to reveal · Swipe right = Good, left = Again</div>' +
    '</article>';

  const slider = document.getElementById("confSlider");
  const confVal = document.getElementById("confVal");
  slider.addEventListener("input", () => { confVal.textContent = slider.value + "%"; });

  const revealBtn = document.getElementById("revealBtn");
  const aBox = document.getElementById("aBox");
  const rateGrid = document.getElementById("rateGrid");
  function syncOccChips(){
    const chipsEl = document.getElementById("occChips");
    if (!chipsEl) return;
    const items = [];
    document.querySelectorAll(".occ-item").forEach(g => {
      const rect = g.querySelector(".occ-rect");
      if (!rect || !rect.classList.contains("revealed")) return;
      const oi = Number(g.getAttribute("data-occ"));
      const m = (card.occlusion || [])[oi];
      if (!m) return;
      const mid = esc(m.id || ("m" + (oi + 1)));
      const mlbl = esc(m.label || ("Mask " + (oi + 1)));
      items.push('<span class="pill solid">' + mid + ': ' + mlbl + '</span>');
    });
    chipsEl.innerHTML = items.join("");
  }
  function doReveal(){
    if (revealed) return;
    revealed = true;
    aBox.classList.add("open");
    rateGrid.classList.add("open");
    revealBtn.style.display = "none";
    document.querySelectorAll(".occ-rect,.occ-lbl").forEach(el => el.classList.add("revealed"));
    document.querySelectorAll(".occ-item").forEach(g => {
      const oi = Number(g.getAttribute("data-occ"));
      const hasBg = g.getAttribute("data-has-bg") === "1";
      const m = (card.occlusion || [])[oi];
      const t = g.querySelector(".occ-lbl");
      if (m && t) t.textContent = hasBg ? "" : (m.label || ("Mask " + (oi + 1)));
    });
    syncOccChips();
  }
  revealBtn.addEventListener("click", doReveal);

  document.querySelectorAll(".occ-item").forEach(g => {
    g.addEventListener("click", () => {
      const oi = Number(g.getAttribute("data-occ"));
      const hasBg = g.getAttribute("data-has-bg") === "1";
      const m = (card.occlusion || [])[oi];
      const rect = g.querySelector(".occ-rect"), lbl = g.querySelector(".occ-lbl");
      if (rect && lbl && m){
        const isRev = rect.classList.toggle("revealed");
        lbl.classList.toggle("revealed", isRev);
        lbl.textContent = isRev ? (hasBg ? "" : (m.label || ("Mask " + (oi + 1)))) : "?";
        syncOccChips();
      }
    });
  });

  function rate(rating){
    const ts = new Date().toISOString().replace(/\.\d{3}Z$/, "Z");
    const conf = Number(slider.value) / 100.0;
    const dur = Math.max(0, Date.now() - cardStartMs);
    queue.push({
      reviewId: makeReviewId(card.propId, card.cueId || "default", ts),
      propId: card.propId,
      cueId: card.cueId || "default",
      rating: rating,
      confidence: Math.round(conf * 100) / 100,
      durationMs: dur,
      reviewedAt: ts
    });
    saveQueue();
    idx += 1;
    render();
  }
  rateGrid.querySelectorAll(".rbtn").forEach(b => b.addEventListener("click", () => rate(b.getAttribute("data-rate"))));

  const cardEl = document.getElementById("activeCard");
  cardEl.addEventListener("touchstart", e => { if (e.touches.length === 1) touchStartX = e.touches[0].clientX; }, {passive:true});
  cardEl.addEventListener("touchend", e => {
    if (touchStartX === null || !revealed) { touchStartX = null; return; }
    const dx = (e.changedTouches[0] ? e.changedTouches[0].clientX : touchStartX) - touchStartX;
    touchStartX = null;
    if (dx > 75) rate("good");
    else if (dx < -75) rate("again");
  }, {passive:true});

  window._syntKey = function(e){
    if (e.code === "Space" && !revealed){ e.preventDefault(); doReveal(); }
    else if (revealed && {"1":"again","2":"hard","3":"good","4":"easy"}[e.key]){
      e.preventDefault(); rate({"1":"again","2":"hard","3":"good","4":"easy"}[e.key]);
    }
  };
}

document.addEventListener("keydown", e => { if (window._syntKey) window._syntKey(e); });
document.getElementById("themeBtn").addEventListener("click", () => {
  const nxt = root.getAttribute("data-theme") === "dark" ? "light" : "dark";
  root.setAttribute("data-theme", nxt);
  try { localStorage.setItem(THEME_KEY, nxt); } catch(e){}
});
document.getElementById("undoBtn").addEventListener("click", () => {
  if (!queue.length) { toast("Nothing to undo"); return; }
  queue.pop();
  saveQueue();
  idx = Math.max(0, idx - 1);
  render();
  toast("Undid last review");
});
document.getElementById("resetBtn").addEventListener("click", () => {
  idx = 0;
  render();
  toast("Deck restarted (queued reviews preserved)");
});
document.getElementById("downloadBtn").addEventListener("click", () => {
  const payload = {
    schemaVersion: 1,
    exportedAt: new Date().toISOString().replace(/\.\d{3}Z$/, "Z"),
    deckId: DECK.deckId,
    chapter: DECK.chapter || null,
    reviews: queue
  };
  const blob = new Blob([JSON.stringify(payload, null, 2) + "\n"], {type:"application/json"});
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "pocket-sync.json";
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  toast("Downloaded pocket-sync.json (" + queue.length + " reviews)");
});
lanBtn.addEventListener("click", async () => {
  if (!queue.length) { toast("No queued reviews to sync"); return; }
  const base = DECK.apiBase || "";
  const endpoint = base + "/api/review/batch";
  try {
    const reqOpts = {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      credentials: "same-origin",
      body: JSON.stringify({schemaVersion: 1, exportedAt: new Date().toISOString(), reviews: queue})
    };
    const host = (window.location && window.location.hostname) ? window.location.hostname : "";
    const isLoopback = /^(localhost|127\.0\.0\.1|\[::1\]|::1)$/i.test(host);
    if (!reqOpts.headers["X-Syntopik-Token"] && isLoopback){
      try {
        const capResp = await fetch(base + "/api/capabilities", {credentials: "same-origin"});
        if (capResp.ok){
          const cap = await capResp.json();
          if (cap && cap.token) reqOpts.headers["X-Syntopik-Token"] = cap.token;
        }
      } catch(_e){}
    }
    const resp = await fetch(endpoint, reqOpts);
    if (!resp.ok) throw new Error("HTTP " + resp.status);
    const data = await resp.json();
    const syncedCount = data.importedCount ?? queue.length;
    queue = [];
    saveQueue();
    if (idx >= DECK.cards.length) render();
    toast("Synced " + syncedCount + " review(s) to Studio");
  } catch(err){
    toast("Offline — tap Download pocket-sync.json instead");
  }
});

render();
})();
</script>
</body>
</html>
"""


_ALLOWED_SVG_TAGS: frozenset[str] = frozenset(
    {
        "svg",
        "g",
        "defs",
        "title",
        "desc",
        "rect",
        "circle",
        "ellipse",
        "line",
        "polyline",
        "polygon",
        "path",
        "text",
        "tspan",
        "marker",
        "lineargradient",
        "radialgradient",
        "stop",
        "clippath",
        "mask",
        "pattern",
        "symbol",
        "use",
    }
)

_FORBIDDEN_SVG_SUBTREE_TAGS: frozenset[str] = frozenset(
    {
        "script",
        "foreignobject",
        "iframe",
        "object",
        "embed",
        "style",
        "applet",
        "audio",
        "video",
        "canvas",
        "template",
        "meta",
        "link",
        "base",
        "form",
        "input",
        "button",
        "textarea",
        "select",
        "animate",
        "animatemotion",
        "animatetransform",
        "set",
        "handler",
        "listener",
    }
)

_SVG_TAG_CANON: dict[str, str] = {
    "lineargradient": "linearGradient",
    "radialgradient": "radialGradient",
    "clippath": "clipPath",
}

_SVG_ATTR_CANON: dict[str, str] = {
    "viewbox": "viewBox",
    "preserveaspectratio": "preserveAspectRatio",
    "gradientunits": "gradientUnits",
    "gradienttransform": "gradientTransform",
    "markerwidth": "markerWidth",
    "markerheight": "markerHeight",
    "markerunits": "markerUnits",
    "refx": "refX",
    "refy": "refY",
    "patternunits": "patternUnits",
    "clippathunits": "clipPathUnits",
    "maskunits": "maskUnits",
    "textlength": "textLength",
    "lengthadjust": "lengthAdjust",
    "spreadmethod": "spreadMethod",
}

_SVG_DANGEROUS_URI_RE = re.compile(r"(?:javascript|vbscript|data)\s*:", re.IGNORECASE)


class _StrictSVGSanitizer(HTMLParser):
    """Allowlist SVG sanitizer stripping <script>, <foreignObject>, <iframe>, on*=, and unsafe URIs."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._out: list[str] = []
        self._skip_depth = 0
        self._open_stack: list[str] = []

    def _clean_attrs(self, attrs: list[tuple[str, str | None]]) -> str:
        parts: list[str] = []
        for raw_k, raw_v in attrs:
            if not raw_k:
                continue
            k = raw_k.strip()
            kl = k.lower()
            if not re.fullmatch(r"[a-zA-Z_:][a-zA-Z0-9_.:-]*", k):
                continue
            if kl.startswith("on"):
                continue
            val = "" if raw_v is None else str(raw_v)
            val_compact = re.sub(r"[\x00-\x20]+", "", val)
            if _SVG_DANGEROUS_URI_RE.search(val_compact):
                continue
            if kl in ("href", "xlink:href", "src", "action", "formaction"):
                if not val.strip().startswith("#"):
                    continue
            if kl == "style":
                if re.search(r"url\s*\(|expression\s*\(|@import|javascript:|vbscript:", val_compact, re.IGNORECASE):
                    continue
            attr_name = _SVG_ATTR_CANON.get(kl, k)
            parts.append(f' {attr_name}="{html_lib.escape(val, quote=True)}"')
        return "".join(parts)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tl = tag.lower()
        if tl in _FORBIDDEN_SVG_SUBTREE_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth > 0 or tl not in _ALLOWED_SVG_TAGS:
            return
        canon = _SVG_TAG_CANON.get(tl, tl)
        self._out.append(f"<{canon}{self._clean_attrs(attrs)}>")
        self._open_stack.append(canon)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tl = tag.lower()
        if self._skip_depth > 0 or tl in _FORBIDDEN_SVG_SUBTREE_TAGS or tl not in _ALLOWED_SVG_TAGS:
            return
        canon = _SVG_TAG_CANON.get(tl, tl)
        self._out.append(f"<{canon}{self._clean_attrs(attrs)}/>")

    def handle_endtag(self, tag: str) -> None:
        tl = tag.lower()
        if tl in _FORBIDDEN_SVG_SUBTREE_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if self._skip_depth > 0 or tl not in _ALLOWED_SVG_TAGS:
            return
        canon = _SVG_TAG_CANON.get(tl, tl)
        if canon in self._open_stack:
            while self._open_stack:
                popped = self._open_stack.pop()
                self._out.append(f"</{popped}>")
                if popped == canon:
                    break

    def handle_data(self, data: str) -> None:
        if self._skip_depth > 0:
            return
        if data:
            self._out.append(html_lib.escape(data, quote=False))

    def finish(self) -> str:
        while self._open_stack:
            self._out.append(f"</{self._open_stack.pop()}>")
        return "".join(self._out).strip()


def sanitize_svg(raw_svg: str) -> str:
    """Sanitize untrusted SVG markup, stripping scripts, foreignObject, iframes, on*= handlers, and javascript: URIs."""
    text = str(raw_svg or "").strip()
    if not text:
        return ""
    text = re.sub(r"<\?[\s\S]*?\?>", "", text)
    text = re.sub(r"<!(?:DOCTYPE|ENTITY)\b[^>]*>", "", text, flags=re.IGNORECASE)
    sanitizer = _StrictSVGSanitizer()
    sanitizer.feed(text)
    sanitizer.close()
    return sanitizer.finish()


def _select_pocket_cards(
    props: list[dict[str, Any]],
    *,
    limit: int = 25,
    chapter: str | None = None,
    include_upcoming: bool = True,
    kg_dir: Path | None = None,
) -> tuple[list[dict[str, Any]], int]:
    today_str = _today_iso()
    ref_now = datetime.now(timezone.utc)
    params = _load_fsrs_parameters(kg_dir) if kg_dir else ()
    chap_filter = str(chapter or "").strip().lower()

    due_rows: list[tuple[tuple[int, float, str], dict[str, Any]]] = []
    upcoming_rows: list[tuple[str, dict[str, Any]]] = []

    for p in props:
        if not isinstance(p, dict) or not p.get("id"):
            continue
        if p.get("archived") is True or p.get("suspended") is True:
            continue
        p_chap = str(p.get("chapter") or "").lower()
        p_subj = str(p.get("subject") or "").lower()
        if chap_filter and chap_filter not in p_chap and chap_filter != p_subj and chap_filter not in str(p.get("id") or "").lower():
            continue

        status = str(p.get("status") or "planned").lower()
        due_str = str(p.get("nextReview") or "")[:10]
        is_due = status in ("misconception", "shaky") or (bool(due_str) and due_str <= today_str)

        cues = [c for c in (p.get("cues") or []) if isinstance(c, dict) and (c.get("q") or c.get("a"))]
        if cues:
            # Prefer weakest or least-reviewed cue
            cues_sorted = sorted(
                cues,
                key=lambda c: (
                    {"again": 0, "hard": 1, None: 2, "good": 3, "easy": 4}.get(c.get("lastRating"), 2),
                    int(c.get("reviews") or 0),
                ),
            )
            cue = cues_sorted[0]
            cue_id = str(cue.get("id") or "c1")
            q_text = str(cue.get("q") or p.get("title") or p["id"])
            a_text = str(cue.get("a") or p.get("statement") or p.get("title") or "")
            occ = cue.get("occlusion") or p.get("occlusion") or []
            svg = cue.get("svg") or p.get("svg")
        else:
            cue_id = "default"
            q_text = f"State and justify: {p.get('title') or p['id']}"
            a_text = str(p.get("statement") or p.get("title") or "")
            occ = p.get("occlusion") or []
            svg = p.get("svg")

        card_obj: dict[str, Any] = {
            "propId": str(p["id"]),
            "cueId": cue_id,
            "subject": str(p.get("subject") or "General"),
            "chapter": str(p.get("chapter") or ""),
            "status": status,
            "q": q_text,
            "a": a_text,
        }
        if isinstance(occ, list) and occ:
            card_obj["occlusion"] = occ
        if isinstance(svg, str) and svg.strip():
            clean_svg = sanitize_svg(svg)
            if clean_svg:
                card_obj["svg"] = clean_svg

        if is_due:
            retr = _card_retrievability(p.get("fsrs"), ref_now, parameters=params) if params else _card_retrievability(p.get("fsrs"), ref_now)
            sev = 0 if status == "misconception" else (1 if status == "shaky" else 2)
            due_rows.append(((sev, float(retr), str(p["id"])), card_obj))
        elif include_upcoming:
            upcoming_rows.append((due_str or "9999-99-99", card_obj))

    due_rows.sort(key=lambda item: item[0])
    upcoming_rows.sort(key=lambda item: (item[0], item[1]["propId"]))

    cap = max(1, int(limit))
    selected = [c for _, c in due_rows[:cap]]
    due_count = len(due_rows)
    if len(selected) < cap and include_upcoming:
        selected.extend(c for _, c in upcoming_rows[: cap - len(selected)])
    return selected, due_count


def export_pocket_html(
    workspace: Path | str | None = None,
    out_path: Path | str | None = None,
    limit: int = 25,
    chapter: str | None = None,
    *,
    kg_dir: Path | str | None = None,
    include_upcoming: bool = True,
    title: str | None = None,
    api_base: str | None = None,
) -> dict[str, Any]:
    """Generate a single zero-CDN, mobile-first offline HTML5 Pocket Commute Deck."""
    ws = _resolve_workspace(workspace)
    kdir = _resolve_kg_dir(workspace=ws, explicit=kg_dir)
    props = _load_jsonl(kdir / "propositions.jsonl")

    cards, due_count = _select_pocket_cards(
        props,
        limit=limit,
        chapter=chapter,
        include_upcoming=include_upcoming,
        kg_dir=kdir,
    )

    deck_title = str(title or (f"Syntopik Pocket — {chapter}" if chapter else "Syntopik Pocket Commute")).strip()
    deck_id = hashlib.sha256(f"{deck_title}|{_today_iso()}|{len(cards)}".encode("utf-8")).hexdigest()[:10]
    deck_payload = {
        "schemaVersion": 1,
        "deckId": deck_id,
        "generatedAt": _now_iso(),
        "chapter": chapter,
        "apiBase": api_base or "",
        "cards": cards,
    }
    # Escape `</` so embedded JSON can never close the <script> tag
    safe_json = json.dumps(deck_payload, ensure_ascii=False).replace("</", "<\\/")

    html_doc = (
        _POCKET_HTML_TEMPLATE.replace("__TITLE__", html_lib.escape(deck_title))
        .replace("__DECK_HEADER__", html_lib.escape(deck_title))
        .replace("__DECK_JSON__", safe_json)
    )

    if out_path is not None and str(out_path).strip():
        dest = Path(out_path).expanduser().resolve()
    else:
        dest = (ws / "chapters" / "pocket-review.html").resolve()

    _write_text_atomic(dest, html_doc, encoding="utf-8")
    size_bytes = dest.stat().st_size

    return {
        "ok": True,
        "outPath": str(dest),
        "bytes": size_bytes,
        "cardCount": len(cards),
        "dueCount": due_count,
        "chapter": chapter,
        "deckId": deck_id,
        "zeroCdn": True,
    }


def _parse_iso_utc(raw: Any) -> datetime:
    if isinstance(raw, datetime):
        return raw.astimezone(timezone.utc) if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    if isinstance(raw, str) and raw.strip():
        s = raw.strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(s)
            return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def _regenerate_review_queue_md(props: list[dict[str, Any]], queue_file: Path, params: tuple[float, ...]) -> None:
    today_str = _today_iso()
    ref_now = datetime.now(timezone.utc)
    due_items: list[tuple[float, dict[str, Any]]] = []
    upcoming: list[tuple[str, dict[str, Any]]] = []
    for p in props:
        st = str(p.get("status") or "planned")
        retr = _card_retrievability(p.get("fsrs"), ref_now, parameters=params) if params else _card_retrievability(p.get("fsrs"), ref_now)
        if st in ("misconception", "shaky") or (isinstance(p.get("nextReview"), str) and p["nextReview"] <= today_str):
            due_items.append((0.0 if st == "misconception" else retr, p))
        elif st == "solid":
            upcoming.append((str(p.get("nextReview") or "9999-99-99"), p))
    due_items.sort(key=lambda item: item[0])
    upcoming.sort(key=lambda item: item[0])

    lines = [
        "# Spaced Review & Weak-Edge Queue",
        "",
        f"> Automatically maintained via FSRS v6 (`syntopik.pocket`) · Updated `{today_str}`.",
        "",
        "## Due / Weak Edges (`misconception`, `shaky`, or Due Today)",
    ]
    if due_items:
        for retr, p in due_items[:30]:
            lines.append(
                f"- **`{p['id']}`** ({p.get('subject', '?')} · {p.get('chapter', '?')} · `{p.get('status')}` · next: `{p.get('nextReview', 'now')}` · R≈{round(retr * 100)}%): {p.get('title', '')}"
            )
    else:
        lines.append("_Nothing due today — keep building new chapters!_")
    lines.append("")
    lines.append("## Upcoming Spaced Recall (`solid`)")
    if upcoming:
        for d, p in upcoming[:30]:
            lines.append(f"- `{d}` · **`{p['id']}`** ({p.get('chapter', p.get('subject', ''))}): {p.get('title', '')}")
    else:
        lines.append("_None scheduled yet._")
    lines.append("")
    _write_text_atomic(queue_file, "\n".join(lines), encoding="utf-8")


def import_pocket_sync(
    workspace: Path | str | None = None,
    sync_path: Path | str | dict[str, Any] | None = None,
    *,
    kg_dir: Path | str | None = None,
    no_fire: bool = False,
    no_sync: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Idempotently replay `pocket-sync.json` reviews (deduped by `reviewId`) through FSRS-6 + FIRe."""
    if isinstance(sync_path, dict):
        payload = sync_path
        src_label = "<inline-payload>"
    else:
        if sync_path is None or not str(sync_path).strip():
            raise ValueError("sync_path (file path or dict payload) is required.")
        sp = Path(sync_path).expanduser().resolve()
        if not sp.is_file():
            raise FileNotFoundError(f"Pocket sync file not found: {sp}")
        payload = json.loads(sp.read_text(encoding="utf-8"))
        src_label = str(sp)

    reviews = payload.get("reviews") if isinstance(payload, dict) else None
    if not isinstance(reviews, list):
        raise ValueError("Invalid pocket-sync payload: expected JSON object with 'reviews' list.")

    ws = _resolve_workspace(workspace)
    kdir = _resolve_kg_dir(workspace=ws, explicit=kg_dir)
    props_path = kdir / "propositions.jsonl"
    chains_path = kdir / "chains.jsonl"
    revlog_path = kdir / "revlog.jsonl"
    queue_path = kdir / "review-queue.md"

    imported_results: list[dict[str, Any]] = []
    new_revlog_rows: list[dict[str, Any]] = []
    all_fire_credits: list[dict[str, Any]] = []
    skipped_dups = 0
    unknown_props: list[str] = []

    with _workspace_lock(kdir):
        props = _load_jsonl(props_path)
        chains = _load_jsonl(chains_path)
        existing_revlog = _load_jsonl(revlog_path)
        params = _load_fsrs_parameters(kdir)

        seen_review_ids: set[str] = {
            str(r.get("reviewId")).strip()
            for r in existing_revlog
            if isinstance(r, dict) and r.get("reviewId")
        }
        props_by_id: dict[str, dict[str, Any]] = {
            str(p.get("id")): p for p in props if isinstance(p, dict) and p.get("id")
        }

        # Replay chronologically so repeated reviews of the same card advance FSRS accurately
        ordered = sorted(
            [r for r in reviews if isinstance(r, dict)],
            key=lambda r: str(r.get("reviewedAt") or ""),
        )

        for row in ordered:
            prop_id = str(row.get("propId") or "").strip()
            rating = str(row.get("rating") or "").strip().lower()
            if rating not in _RATING_TO_INT:
                continue

            rev_id = str(row.get("reviewId") or "").strip()
            if not rev_id:
                rev_id = hashlib.sha256(
                    f"{prop_id}|{row.get('cueId')}|{rating}|{row.get('reviewedAt')}".encode("utf-8")
                ).hexdigest()[:12]

            if rev_id in seen_review_ids:
                skipped_dups += 1
                continue

            target = props_by_id.get(prop_id)
            if target is None:
                unknown_props.append(prop_id)
                continue

            seen_review_ids.add(rev_id)
            rev_dt = _parse_iso_utc(row.get("reviewedAt"))
            conf_raw = row.get("confidence")
            conf_val: float | None = None
            if isinstance(conf_raw, (int, float)) and not isinstance(conf_raw, bool):
                conf_val = max(0.0, min(1.0, float(conf_raw) / 100.0 if float(conf_raw) > 1.0 else float(conf_raw)))
            dur_ms = int(row["durationMs"]) if isinstance(row.get("durationMs"), (int, float)) else None

            if params:
                new_card, meta = _review_card_dict(
                    target.get("fsrs"),
                    rating,
                    now=rev_dt,
                    parameters=params,
                    review_duration_ms=dur_ms,
                )
            else:
                new_card, meta = _review_card_dict(target.get("fsrs"), rating, now=rev_dt)

            correct = rating in ("good", "easy")
            prior_bkt = float(target.get("bktMastery", 0.30))
            new_bkt = _bkt_update(
                prior_bkt,
                correct,
                p_transit=_RATING_TRANSIT[rating],
                learner_confidence=conf_val,
            )
            conf_sig = _assess_confidence_signal(correct, conf_val)
            due_date = str(new_card.get("due") or "")[:10] or rev_dt.date().isoformat()

            target["fsrs"] = new_card
            target["bktMastery"] = new_bkt
            target["confidence"] = new_bkt
            target["lastTested"] = rev_dt.date().isoformat()
            target["nextReview"] = due_date
            if rating == "again":
                target["status"] = "shaky"
                target["lapses"] = int(target.get("lapses") or 0) + 1
            elif rating == "hard":
                target["status"] = "shaky"
            else:
                target["status"] = "solid"

            cue_id = str(row.get("cueId") or "").strip()
            if cue_id and isinstance(target.get("cues"), list):
                for c in target["cues"]:
                    if isinstance(c, dict) and str(c.get("id") or "") == cue_id:
                        c["lastRating"] = rating
                        c["reviews"] = int(c.get("reviews") or 0) + 1
                        break

            fire_credits: list[dict[str, Any]] = []
            if not no_fire and correct and chains:
                fire_credits = _apply_fire_credit(
                    prop_id,
                    rating,
                    props,
                    chains,
                    now=rev_dt,
                    parameters=params if params else None,
                )
                all_fire_credits.extend(fire_credits)

            rev_entry: dict[str, Any] = {
                "reviewId": rev_id,
                "source": "pocket",
                "ts": rev_dt.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
                "propId": prop_id,
                "cueId": cue_id or None,
                "rating": rating,
                "ratingInt": _RATING_TO_INT[rating],
                "correct": correct,
                "learnerConfidence": conf_sig.get("learnerConfidence"),
                "hypercorrection": bool(conf_sig.get("hypercorrection")),
                "luckyGuess": bool(conf_sig.get("luckyGuess")),
                "brierError": conf_sig.get("brierError"),
                "durationMs": dur_ms,
                "elapsedDays": meta.get("elapsedDays"),
                "intervalDays": meta.get("intervalDays"),
                "predictedR": meta.get("predictedR"),
                "newStability": round(float(new_card.get("stability") or 0.0), 4),
                "newBktMastery": new_bkt,
                "fireCreditCount": len(fire_credits),
            }
            new_revlog_rows.append(rev_entry)
            imported_results.append(
                {
                    "reviewId": rev_id,
                    "propId": prop_id,
                    "rating": rating,
                    "status": target["status"],
                    "bktMastery": new_bkt,
                    "nextReview": due_date,
                    "fireCredits": len(fire_credits),
                }
            )

        if not dry_run and imported_results:
            _save_jsonl_atomic(props_path, props)
            _append_jsonl_locked(revlog_path, new_revlog_rows)
            _regenerate_review_queue_md(props, queue_path, params)

    if not dry_run and imported_results and not no_sync:
        _sync_hub(workspace=ws, kg_dir=kdir, quiet=True)

    return {
        "ok": True,
        "source": src_label,
        "dryRun": bool(dry_run),
        "importedCount": len(imported_results),
        "skippedDuplicateCount": skipped_dups,
        "unknownPropIds": unknown_props,
        "fireCreditsCount": len(all_fire_credits),
        "fireCredits": all_fire_credits,
        "results": imported_results,
    }


# ---------------------------------------------------------------------------
# CLI (`python3 -m syntopik.pocket export|import-sync|qr`)
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--workspace", type=Path, default=argparse.SUPPRESS, help="Workspace root directory.")
    common.add_argument("--kg-dir", type=Path, default=argparse.SUPPRESS, help="Override knowledge-graph/ directory.")
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="Emit JSON output.")

    parser = argparse.ArgumentParser(
        prog="syntopik.pocket",
        description="Export offline Pocket Commute HTML deck, replay pocket-sync.json, and render pairing QR codes.",
        parents=[common],
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_exp = sub.add_parser("export", parents=[common], help="Export zero-CDN mobile Pocket HTML deck.")
    p_exp.add_argument("--out", type=Path, default=None, help="Output HTML path (default: chapters/pocket-review.html).")
    p_exp.add_argument("--limit", type=int, default=25, help="Max cards to embed (default: 25).")
    p_exp.add_argument("--chapter", default=None, help="Filter by chapter or subject substring.")
    p_exp.add_argument("--title", default=None, help="Custom deck header title.")
    p_exp.add_argument("--due-only", action="store_true", help="Exclude upcoming non-due cards.")

    p_imp = sub.add_parser("import-sync", parents=[common], help="Replay pocket-sync.json idempotently through FSRS + FIRe.")
    p_imp.add_argument("sync_file", type=Path, help="Path to pocket-sync.json.")
    p_imp.add_argument("--no-fire", action="store_true", help="Disable FIRe ancestor implicit credit.")
    p_imp.add_argument("--no-sync", action="store_true", help="Skip hub sync after replay.")
    p_imp.add_argument("--dry-run", action="store_true", help="Validate without modifying knowledge-graph files.")

    p_qr = sub.add_parser("qr", parents=[common], help="Render a scannable pure-stdlib SVG or ASCII QR code.")
    p_qr.add_argument("url", help="Pairing URL to encode.")
    p_qr.add_argument("--format", choices=("ascii", "svg"), default="ascii", help="Output format (default: ascii).")
    p_qr.add_argument("--out", type=Path, default=None, help="Optional file path to write SVG/ASCII QR.")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    ws = getattr(args, "workspace", None)
    kg_dir = getattr(args, "kg_dir", None)
    as_json = bool(getattr(args, "json", False))

    try:
        if args.command == "export":
            res = export_pocket_html(
                workspace=ws,
                out_path=args.out,
                limit=args.limit,
                chapter=args.chapter,
                kg_dir=kg_dir,
                include_upcoming=not args.due_only,
                title=args.title,
            )
        elif args.command == "import-sync":
            res = import_pocket_sync(
                workspace=ws,
                sync_path=args.sync_file,
                kg_dir=kg_dir,
                no_fire=args.no_fire,
                no_sync=args.no_sync,
                dry_run=args.dry_run,
            )
        elif args.command == "qr":
            rendered = render_qr_svg(args.url) if args.format == "svg" else render_qr_ascii(args.url)
            if args.out is not None:
                _write_text_atomic(args.out, rendered + ("\n" if not rendered.endswith("\n") else ""))
            res = {
                "ok": True,
                "url": args.url,
                "format": args.format,
                "outPath": str(args.out.resolve()) if args.out else None,
                "qr": rendered,
            }
        else:
            parser.error(f"Unknown command: {args.command}")
            return 2
    except Exception as exc:  # noqa: BLE001
        res = {"ok": False, "command": args.command, "errors": [str(exc)]}

    if as_json:
        print(json.dumps(res, indent=2, ensure_ascii=False))
    else:
        if res.get("ok"):
            if args.command == "export":
                print(
                    f"Exported offline Pocket deck: {res['outPath']}\n"
                    f"  {res['cardCount']} cards ({res['dueCount']} due) · {res['bytes']:,} B · zero-CDN"
                )
            elif args.command == "import-sync":
                print(
                    f"Pocket sync complete: +{res['importedCount']} imported, "
                    f"{res['skippedDuplicateCount']} duplicates skipped, {res['fireCreditsCount']} FIRe credits"
                )
            elif args.command == "qr":
                print(res["qr"])
        else:
            for err in res.get("errors", ["Operation failed"]):
                print(f"ERROR: {err}", file=sys.stderr)
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
