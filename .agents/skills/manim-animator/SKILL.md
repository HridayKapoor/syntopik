---
name: manim-animator
description: >-
  Generate 3Blue1Brown-style animated MP4 explanation videos with Manim and native
  LaTeX term morphing, chemical equations, optional voiceover narration, and
  frame-by-frame visual QA.
---

# Manim Animator — 3Blue1Brown-Style Concept & Derivation Videos

Use this skill when a **step-by-step animated derivation, geometric transformation, or algorithm trace video** unlocks intuition better than a static figure or slider lab (`animations/<slug>.mp4`).

> **Decision rule:** If the learner should *manipulate parameters themselves*, use `visual-and-demo`. If the learner needs a *choreographed visual derivation* (eigenvector stretching, Fourier/phasor synthesis, gradient descent, B-tree split, SHM curvature, or reaction mechanism), use **Manim** — delegating rendering to a background subagent (`invoke_subagent` / `Agent` / `spawn_agent`) so the live conversation never stalls.

---

## Portable Native LaTeX Setup (`scripts/manim_helpers.py`)

**Always import `manim_helpers` first** — it automatically locates TinyTeX / TeX Live across macOS (`~/Library/TinyTeX`, `~/.TinyTeX`), Linux (`~/.TinyTeX/bin/*-linux`), `$TINYTEX_BIN` / `$TEXLIVE_BIN`, and system `PATH`, prepending `latex` and `dvisvgm` cleanly inside non-login subshells.

```python
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from manim_helpers import CHEM_TEX_TEMPLATE, PALETTE  # portable TinyTeX PATH setup
from manim import *


class DerivationScene(Scene):
    def construct(self):
        self.camera.background_color = PALETTE["BG"]

        eq1 = MathTex(
            r"m", r"\frac{d^2 x}{dt^2}", r"=", r"-", r"k", r"x",
            tex_template=CHEM_TEX_TEMPLATE,
        ).scale(1.15)
        eq1.set_color_by_tex("x", PALETTE["YELLOW"])

        eq2 = MathTex(
            r"\frac{d^2 x}{dt^2}", r"=", r"-", r"\underbrace{\frac{k}{m}}_{\omega^2}", r"x",
            tex_template=CHEM_TEX_TEMPLATE,
        ).scale(1.15)

        self.play(Write(eq1))
        self.wait(0.8)
        self.play(TransformMatchingTex(eq1, eq2))
        self.wait(1.0)
```

### Authoring Craft
- Use `TransformMatchingTex(eq1, eq2)` over isolated tokens instead of abrupt `FadeOut`/`Write`.
- `CHEM_TEX_TEMPLATE` supports `amsmath`, `mathtools`, `physics` (`\dv`, `\pdv`, `\vb`), `cancel` (`\cancel{}`, `\cancelto{}{}`), and `mhchem` (`\ce{}`). Zero-TeX fallbacks (`LaTeXMath`, `TypstMath`) remain exported if TeX is absent.
- Keep clips tight (`15–45 s`, one core intuition, `buff >= 0.45`).

---

## Render, Optional Narration & Mandatory Frame Inspection

1. Save scene to `animations/<slug>.py` and render (`-qm` 720p30 default):
   ```bash
   .venv/bin/manim -qm --media_dir animations/media -o "<slug>.mp4" "animations/<slug>.py" <SceneClass>
   ```
   Copy final output to `animations/<slug>.mp4` (optionally add voiceover + Pillow-burned captions via `syntopik run narrate_video --video animations/<slug>.mp4 ...` or `.venv/bin/python -B scripts/narrate_video.py ...`).
2. **Mandatory Frame Audit**: extract 2–3 keyframes via `ffmpeg` into `/tmp` and inspect every frame with `read_file` / `Read` to confirm zero clipped math, zero overlapping labels, and crisp contrast; clean up `/tmp` frames.
3. Share a clickable `file://` link to `animations/<slug>.mp4` with 2–3 timestamped observation cues (`<= 120` words).
