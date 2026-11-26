#!/usr/bin/env python3
"""Syntopik v2.0 — Bidirectional Obsidian Vault & Matuschak Mnemonic Medium Sync CLI.

Thin standalone entrypoint delegating to `syntopik.obsidian`. Pure Python 3.12+ stdlib.
"""

from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from syntopik.obsidian import (  # noqa: E402, F401
    SYNT_BEGIN,
    SYNT_END,
    VALID_CUE_KINDS,
    build_parser,
    export_vault,
    import_vault,
    main,
    parse_frontmatter,
    parse_mnemonic_markdown,
    render_frontmatter,
    splice_fenced_block,
    sync_notes,
)


if __name__ == "__main__":
    main()
