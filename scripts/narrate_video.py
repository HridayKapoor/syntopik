#!/usr/bin/env python3
"""Narrated Manim Video Builder (edge-tts v7 + macOS `say` offline fallback + FFmpeg + SRT).

Takes a Manim MP4 + timestamped narration JSON (or plain script text), synthesizes
clean narration audio via Microsoft Edge neural TTS (`edge-tts` v7.x streaming API)
with automatic offline fallback to macOS `say`, builds synchronized `.srt` captions,
and muxes everything cleanly into a narrated MP4 via `ffmpeg`.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import subprocess
import tempfile
from datetime import timedelta
from pathlib import Path
from typing import Any

import srt


DEFAULT_EDGE_VOICE = "en-IN-NeerjaNeural"
DEFAULT_SAY_VOICE = "Rishi"


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


def has_audio_stream(path: Path, ffprobe_bin: str) -> bool:
    proc = subprocess.run(
        [
            ffprobe_bin,
            "-v",
            "error",
            "-select_streams",
            "a",
            "-show_entries",
            "stream=index",
            "-of",
            "csv=p=0",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    return bool(proc.stdout.strip())


def load_cues(script_path: Path | None, inline_text: str | None) -> list[dict[str, Any]]:
    if inline_text:
        raw_text = inline_text.strip()
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", raw_text) if s.strip()]
        return [{"start": None, "text": s} for s in (sentences or [raw_text])]

    if script_path is None:
        raise SystemExit("Provide either --script <cues.json|narration.txt> or --text '...'")
    if not script_path.exists():
        raise SystemExit(f"Narration script not found: {script_path}")

    if script_path.suffix.lower() == ".json":
        data = json.loads(script_path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            raw_cues = data.get("cues") or data.get("segments") or data.get("narration") or []
        elif isinstance(data, list):
            raw_cues = data
        else:
            raise SystemExit("Narration JSON must be a list of cues or an object with a 'cues' list.")
        cues: list[dict[str, Any]] = []
        for idx, item in enumerate(raw_cues):
            if isinstance(item, str):
                text = item.strip()
                if text:
                    cues.append({"start": None, "text": text})
            elif isinstance(item, dict):
                text = str(item.get("text", "")).strip()
                if not text:
                    continue
                start = item.get("start")
                cues.append({
                    "start": float(start) if start is not None else None,
                    "text": text,
                    "voice": item.get("voice"),
                    "rate": item.get("rate"),
                    "gap": float(item.get("gap", 0.25)),
                })
            else:
                raise SystemExit(f"Unsupported cue at index {idx}: {item!r}")
        if not cues:
            raise SystemExit("Narration JSON contains no non-empty cues.")
        return cues

    # Plain text or Markdown script -> split by blank lines or sentences.
    raw_text = script_path.read_text(encoding="utf-8").strip()
    # Strip markdown headings/formatting lightly for natural speech
    cleaned = re.sub(r"^#+\s*", "", raw_text, flags=re.MULTILINE)
    cleaned = re.sub(r"[*_`]", "", cleaned)
    blocks = [b.strip().replace("\n", " ") for b in re.split(r"\n\s*\n", cleaned) if b.strip()]
    cues = []
    for blk in blocks:
        for sent in re.split(r"(?<=[.!?])\s+", blk):
            s = sent.strip()
            if s:
                cues.append({"start": None, "text": s, "gap": 0.25})
    if not cues:
        raise SystemExit(f"No readable text found in {script_path}")
    return cues


async def _synth_edge_clip(text: str, voice: str, rate: str, out_mp3: Path) -> str:
    """Synthesize one clip with edge-tts v7.x streaming API and return raw SRT from SubMaker."""
    import edge_tts

    comm = edge_tts.Communicate(text, voice=voice, rate=rate, boundary="SentenceBoundary")
    submaker = edge_tts.SubMaker()
    audio_bytes = bytearray()
    async for chunk in comm.stream():
        ctype = chunk.get("type")
        if ctype == "audio":
            audio_bytes.extend(chunk["data"])
        elif ctype == "SentenceBoundary":
            submaker.feed(chunk)
    if not audio_bytes:
        raise RuntimeError("edge-tts returned empty audio stream")
    out_mp3.write_bytes(bytes(audio_bytes))
    try:
        return submaker.get_srt()
    except Exception:
        return ""


def synth_say_clip(text: str, voice: str, rate_wpm: int, out_wav: Path, ffmpeg_bin: str) -> None:
    """Offline speech synthesizer (`say` on macOS, `espeak-ng`/`espeak` on Linux) converted to 48kHz stereo WAV."""
    if shutil.which("say") or Path("/usr/bin/say").is_file():
        say_bin = find_bin("say")
        aiff_path = out_wav.with_suffix(".aiff")
        cmd = [say_bin, "-v", voice, "-r", str(rate_wpm), "-o", str(aiff_path), text]
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            proc = subprocess.run([say_bin, "-r", str(rate_wpm), "-o", str(aiff_path), text], capture_output=True, text=True, check=False)
            if proc.returncode != 0:
                raise RuntimeError(f"macOS say failed: {proc.stderr.strip()}")
        conv = subprocess.run(
            [ffmpeg_bin, "-y", "-v", "error", "-i", str(aiff_path), "-ar", "48000", "-ac", "2", str(out_wav)],
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
                    [ffmpeg_bin, "-y", "-v", "error", "-i", str(raw_wav), "-ar", "48000", "-ac", "2", str(out_wav)],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if conv.returncode == 0:
                    return
    raise SystemExit("Offline TTS unavailable: use `edge-tts` or install `espeak-ng` / macOS `say`.")


def synthesize_cue(
    cue: dict[str, Any],
    idx: int,
    work_dir: Path,
    engine: str,
    edge_voice: str,
    edge_rate: str,
    say_voice: str,
    say_rate: int,
    ffmpeg_bin: str,
    ffprobe_bin: str,
) -> dict[str, Any]:
    text = cue["text"]
    c_voice = cue.get("voice")
    c_rate = cue.get("rate") or edge_rate
    used_engine = engine
    out_wav = work_dir / f"cue_{idx:03d}.wav"

    if engine in ("auto", "edge"):
        mp3_tmp = work_dir / f"cue_{idx:03d}.mp3"
        try:
            asyncio.run(_synth_edge_clip(text, c_voice or edge_voice, str(c_rate), mp3_tmp))
            proc = subprocess.run(
                [ffmpeg_bin, "-y", "-v", "error", "-i", str(mp3_tmp), "-ar", "48000", "-ac", "2", str(out_wav)],
                capture_output=True,
                text=True,
                check=False,
            )
            if proc.returncode != 0:
                raise RuntimeError(proc.stderr.strip())
            used_engine = "edge-tts"
        except Exception as exc:
            if engine == "edge":
                raise SystemExit(f"edge-tts failed (and --engine edge disabled fallback): {exc}")
            synth_say_clip(text, c_voice or say_voice, say_rate, out_wav, ffmpeg_bin)
            used_engine = "say-fallback"
    else:
        synth_say_clip(text, c_voice or say_voice, say_rate, out_wav, ffmpeg_bin)
        used_engine = "say"

    dur = probe_duration(out_wav, ffprobe_bin)
    return {
        "index": idx,
        "text": text,
        "requestedStart": cue.get("start"),
        "gap": float(cue.get("gap", 0.25)),
        "wav": out_wav,
        "duration": dur,
        "engine": used_engine,
    }


def schedule_cues(clips: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Place each cue on the timeline without overlapping audio clips."""
    cursor = 0.0
    scheduled = []
    for i, c in enumerate(clips):
        req = c["requestedStart"]
        if req is not None:
            start = max(float(req), cursor)
        else:
            start = cursor if i > 0 else 0.15
        end = start + c["duration"]
        scheduled.append({**c, "start": round(start, 3), "end": round(end, 3)})
        cursor = end + max(0.05, c["gap"])
    return scheduled


def write_srt_file(scheduled: list[dict[str, Any]], srt_path: Path) -> None:
    subs: list[srt.Subtitle] = []
    for i, c in enumerate(scheduled, start=1):
        subs.append(
            srt.Subtitle(
                index=i,
                start=timedelta(seconds=c["start"]),
                end=timedelta(seconds=c["end"]),
                content=c["text"],
            )
        )
    srt_path.parent.mkdir(parents=True, exist_ok=True)
    srt_path.write_text(srt.compose(subs), encoding="utf-8")


def mix_audio_timeline(scheduled: list[dict[str, Any]], total_dur: float, out_m4a: Path, ffmpeg_bin: str) -> None:
    """Place every synthesized WAV clip at its exact millisecond offset and render clean AAC narration."""
    cmd = [ffmpeg_bin, "-y", "-v", "error"]
    # Input 0: silent stereo 48kHz base bed of exact total duration
    cmd += ["-f", "lavfi", "-t", f"{max(0.5, total_dur):.3f}", "-i", "anullsrc=r=48000:cl=stereo"]
    for c in scheduled:
        cmd += ["-i", str(c["wav"])]

    filter_parts = []
    mix_inputs = ["[0:a]"]
    for idx, c in enumerate(scheduled, start=1):
        delay_ms = max(0, int(round(c["start"] * 1000)))
        filter_parts.append(f"[{idx}:a]adelay={delay_ms}|{delay_ms},apad=whole_dur={total_dur:.3f}[a{idx}]")
        mix_inputs.append(f"[a{idx}]")

    amix = f"{''.join(mix_inputs)}amix=inputs={len(mix_inputs)}:duration=first:normalize=0,alimiter=limit=0.95[aout]"
    filter_parts.append(amix)
    filter_complex = ";".join(filter_parts)

    cmd += [
        "-filter_complex",
        filter_complex,
        "-map",
        "[aout]",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-ar",
        "48000",
        str(out_m4a),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg audio mix failed: {proc.stderr.strip()}")


def ffmpeg_has_filter(ffmpeg_bin: str, filter_name: str) -> bool:
    proc = subprocess.run([ffmpeg_bin, "-hide_banner", "-filters"], capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        return False
    return bool(re.search(rf"\b{re.escape(filter_name)}\b", proc.stdout))


def probe_video_dimensions(path: Path, ffprobe_bin: str) -> tuple[int, int]:
    proc = subprocess.run(
        [
            ffprobe_bin,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "csv=p=0:s=x",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode == 0 and "x" in proc.stdout:
        w_s, h_s = proc.stdout.strip().splitlines()[0].split("x", 1)
        return int(w_s), int(h_s)
    return 1280, 720


def _load_caption_font(size_px: int):
    from PIL import ImageFont

    for font_path in (
        "/System/Library/Fonts/Helvetica.ttc",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/SFNS.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/TTF/DejaVuSans.ttf",
        "/usr/share/fonts/liberation/LiberationSans-Regular.ttf",
        "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/segoeui.ttf",
    ):
        if Path(font_path).exists():
            try:
                return ImageFont.truetype(font_path, size=size_px)
            except Exception:
                continue
    return ImageFont.load_default()


def render_caption_png(text: str, width: int, height: int, out_png: Path) -> Path:
    """Render a crisp transparent RGBA bottom caption pill for FFmpeg overlay burning."""
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    font_size = max(16, min(42, int(round(height * 0.044))))
    font = _load_caption_font(font_size)

    max_text_w = int(width * 0.84)
    words = text.split()
    lines: list[str] = []
    cur = ""
    for w in words:
        cand = f"{cur} {w}".strip()
        bbox = draw.textbbox((0, 0), cand, font=font, stroke_width=2)
        if bbox[2] - bbox[0] <= max_text_w or not cur:
            cur = cand
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    if not lines:
        lines = [text]

    line_metrics = [draw.textbbox((0, 0), ln, font=font, stroke_width=2) for ln in lines]
    line_widths = [b[2] - b[0] for b in line_metrics]
    line_heights = [max(font_size, b[3] - b[1]) for b in line_metrics]
    line_gap = max(4, int(font_size * 0.25))
    total_text_h = sum(line_heights) + line_gap * (len(lines) - 1)
    box_w = min(width - 24, max(line_widths) + int(font_size * 1.4))
    pad_y = max(8, int(font_size * 0.42))
    box_h = total_text_h + pad_y * 2

    bottom_margin = max(14, int(height * 0.05))
    x0 = (width - box_w) // 2
    y1 = height - bottom_margin
    y0 = max(8, y1 - box_h)
    radius = max(8, int(font_size * 0.45))

    draw.rounded_rectangle(
        [x0, y0, x0 + box_w, y1],
        radius=radius,
        fill=(12, 16, 24, 200),
        outline=(148, 163, 184, 90),
        width=1,
    )

    y_cursor = y0 + pad_y
    for ln, lw, lh in zip(lines, line_widths, line_heights):
        tx = (width - lw) // 2
        draw.text(
            (tx, y_cursor),
            ln,
            font=font,
            fill=(248, 250, 252, 255),
            stroke_width=2,
            stroke_fill=(0, 0, 0, 210),
        )
        y_cursor += lh + line_gap

    img.save(str(out_png), format="PNG")
    return out_png


def mux_video(
    video_path: Path,
    audio_path: Path,
    srt_path: Path,
    out_mp4: Path,
    video_dur: float,
    audio_dur: float,
    burn_subs: bool,
    scheduled: list[dict[str, Any]],
    work_dir: Path,
    ffmpeg_bin: str,
    ffprobe_bin: str,
) -> tuple[bool, str]:
    pad_needed = audio_dur > video_dur + 0.15
    pad_sec = max(0.0, audio_dur - video_dur + 0.25) if pad_needed else 0.0

    if burn_subs and not ffmpeg_has_filter(ffmpeg_bin, "subtitles"):
        # Native Pillow transparent PNG caption overlays via FFmpeg `overlay` (works without libass!)
        width, height = probe_video_dimensions(video_path, ffprobe_bin)
        cmd = [ffmpeg_bin, "-y", "-v", "error", "-i", str(video_path), "-i", str(audio_path)]
        png_paths: list[Path] = []
        for idx, cue in enumerate(scheduled):
            png_p = render_caption_png(cue["text"], width, height, work_dir / f"cap_{idx:03d}.png")
            png_paths.append(png_p)
            cmd += ["-i", str(png_p)]

        fc_parts: list[str] = []
        cur_v = "[0:v]"
        if pad_needed:
            fc_parts.append(f"{cur_v}tpad=stop_mode=clone:stop_duration={pad_sec:.3f}[vpad]")
            cur_v = "[vpad]"
        for idx, cue in enumerate(scheduled):
            in_idx = idx + 2
            nxt_v = f"[vc{idx}]"
            st = max(0.0, float(cue["start"]))
            en = max(st + 0.05, float(cue["end"]))
            fc_parts.append(f"{cur_v}[{in_idx}:v]overlay=0:0:enable='between(t,{st:.3f},{en:.3f})'{nxt_v}")
            cur_v = nxt_v

        cmd += [
            "-filter_complex",
            ";".join(fc_parts),
            "-map",
            cur_v,
            "-map",
            "1:a:0",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-crf",
            "18",
            "-preset",
            "veryfast",
            "-c:a",
            "copy",
            "-movflags",
            "+faststart",
            str(out_mp4),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg Pillow-overlay burn failed: {proc.stderr.strip()}")
        return pad_needed, "pillow-overlay"

    cmd = [ffmpeg_bin, "-y", "-v", "error", "-i", str(video_path), "-i", str(audio_path)]
    if not burn_subs:
        cmd += ["-i", str(srt_path)]

    vf_parts = []
    if pad_needed:
        vf_parts.append(f"tpad=stop_mode=clone:stop_duration={pad_sec:.3f}")
    if burn_subs:
        esc_srt = str(srt_path).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
        vf_parts.append(f"subtitles='{esc_srt}':force_style='FontSize=20,Outline=1.5'")

    if vf_parts:
        cmd += ["-vf", ",".join(vf_parts), "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18", "-preset", "veryfast"]
    else:
        cmd += ["-c:v", "copy"]

    cmd += ["-map", "0:v:0", "-map", "1:a:0", "-c:a", "copy"]
    if not burn_subs:
        cmd += ["-map", "2:0", "-c:s", "mov_text", "-metadata:s:s:0", "language=eng"]
    cmd += ["-movflags", "+faststart", str(out_mp4)]

    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg video mux failed: {proc.stderr.strip()}")
    return pad_needed, ("libass-subtitles" if burn_subs else "soft-mov_text")


def main() -> None:
    parser = argparse.ArgumentParser(description="Synthesize narration + SRT captions and mux into a Manim MP4")
    parser.add_argument("video", help="Input Manim MP4 video path")
    parser.add_argument("--script", help="Timestamped cues .json OR plain narration .txt/.md file")
    parser.add_argument("--text", help="Inline narration text (alternative to --script)")
    parser.add_argument("--out", help="Output narrated MP4 path (default: <video_stem>-narrated.mp4)")
    parser.add_argument("--srt", help="Output .srt subtitle path (default: alongside --out)")
    parser.add_argument("--engine", choices=["auto", "edge", "say"], default="auto", help="TTS engine (auto = edge-tts with offline macOS say fallback)")
    parser.add_argument("--voice", default=DEFAULT_EDGE_VOICE, help=f"Edge TTS voice (default: {DEFAULT_EDGE_VOICE})")
    parser.add_argument("--rate", default="+0%", help="Edge TTS speaking rate, e.g. '+5%%' or '-5%%'")
    parser.add_argument("--say-voice", default=DEFAULT_SAY_VOICE, help=f"Offline macOS say voice (default: {DEFAULT_SAY_VOICE})")
    parser.add_argument("--say-rate", type=int, default=185, help="Offline macOS say words/minute (default: 185)")
    parser.add_argument("--burn-subs", action="store_true", help="Hard-burn captions into video frames (auto-uses Pillow PNG overlays when FFmpeg lacks libass)")
    args = parser.parse_args()

    ffmpeg_bin = find_bin("ffmpeg")
    ffprobe_bin = find_bin("ffprobe")
    video_path = Path(args.video).expanduser().resolve()
    if not video_path.exists():
        raise SystemExit(f"Input video not found: {video_path}")

    out_mp4 = (
        Path(args.out).expanduser().resolve()
        if args.out
        else video_path.with_name(f"{video_path.stem}-narrated.mp4")
    )
    out_mp4.parent.mkdir(parents=True, exist_ok=True)
    srt_path = Path(args.srt).expanduser().resolve() if args.srt else out_mp4.with_suffix(".srt")

    cues = load_cues(Path(args.script).expanduser().resolve() if args.script else None, args.text)
    video_dur = probe_duration(video_path, ffprobe_bin)

    with tempfile.TemporaryDirectory(prefix="narrate-video-") as tmp:
        work_dir = Path(tmp)
        clips = [
            synthesize_cue(
                cue=c,
                idx=i,
                work_dir=work_dir,
                engine=args.engine,
                edge_voice=args.voice,
                edge_rate=args.rate,
                say_voice=args.say_voice,
                say_rate=args.say_rate,
                ffmpeg_bin=ffmpeg_bin,
                ffprobe_bin=ffprobe_bin,
            )
            for i, c in enumerate(cues)
        ]
        scheduled = schedule_cues(clips)
        write_srt_file(scheduled, srt_path)

        audio_end = max(c["end"] for c in scheduled)
        total_audio_dur = max(video_dur, audio_end + 0.15)
        mixed_m4a = work_dir / "narration.m4a"
        mix_audio_timeline(scheduled, total_audio_dur, mixed_m4a, ffmpeg_bin)
        padded, caption_mode = mux_video(
            video_path,
            mixed_m4a,
            srt_path,
            out_mp4,
            video_dur,
            audio_end,
            args.burn_subs,
            scheduled,
            work_dir,
            ffmpeg_bin,
            ffprobe_bin,
        )

    engines_used = sorted({c["engine"] for c in scheduled})
    print(json.dumps({
        "inputVideo": str(video_path),
        "output": str(out_mp4),
        "srt": str(srt_path),
        "enginesUsed": engines_used,
        "captionMode": caption_mode,
        "videoDurationSec": round(video_dur, 2),
        "narrationEndSec": round(audio_end, 2),
        "videoPadded": padded,
        "cueCount": len(scheduled),
        "cues": [
            {"start": c["start"], "end": c["end"], "engine": c["engine"], "text": c["text"]}
            for c in scheduled
        ],
    }, indent=2))


if __name__ == "__main__":
    main()
