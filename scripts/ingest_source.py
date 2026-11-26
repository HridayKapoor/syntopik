#!/usr/bin/env python3
"""Syntopik v2.0 — 1-Command Universal Source Importer & Incremental Reading Queue CLI.

Thin standalone entrypoint delegating to `syntopik.ingest`. Pure Python 3.12+ stdlib.
"""

from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from syntopik.ingest import (  # noqa: E402, F401
    READING_STAGES,
    build_parser,
    chunk_markdown_slices,
    chunk_transcript,
    compute_queue_rank,
    detect_source_type,
    extract_youtube_video_id,
    html_to_markdown,
    ingest_arxiv,
    ingest_auto,
    ingest_notes,
    ingest_repo,
    ingest_url,
    ingest_youtube,
    load_reading_queue,
    main,
    normalize_arxiv_id,
    parse_arxiv_atom,
    parse_timedtext_xml,
    parse_vtt,
    queue_add,
    queue_advance,
    queue_due,
    validate_http_url,
)


if __name__ == "__main__":
    main()
