"""Built-in importer plugin: Kindle `My Clippings.txt` highlights -> planned cards.

Also the reference example of the importer plugin API (`syntopik.importer_api`):
it only parses, and core handles caps, de-duplication and the ledger write.

Kindle appends every highlight, note and bookmark to one file::

    Thinking, Fast and Slow (Daniel Kahneman)
    - Your Highlight on page 20 | Location 301-303 | Added on Monday, 3 March 2025 21:04:11

    System 1 operates automatically and quickly, with little or no effort.
    ==========

Highlights become cards (book title -> chapter); a note at the same location is
attached to its highlight; bookmarks and empty entries are skipped; repeated
highlights of the same passage (Kindle re-appends edits) keep the longest one.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterator

_SEPARATOR = "=========="
_META_RE = re.compile(r"^-\s*(?:Your\s+)?(Highlight|Note|Bookmark)\b(.*)$", re.IGNORECASE)
_LOC_RE = re.compile(r"Location\s+(\d+)(?:-(\d+))?", re.IGNORECASE)
_PAGE_RE = re.compile(r"page\s+([0-9ivxlcdm]+)", re.IGNORECASE)
_AUTHOR_RE = re.compile(r"\s*\(([^()]*)\)\s*$")
_MIN_HIGHLIGHT_CHARS = 12


def _parse_entries(text: str) -> Iterator[dict[str, Any]]:
    for block in text.split(_SEPARATOR):
        lines = [ln.strip() for ln in block.strip().splitlines()]
        if len(lines) < 2:
            continue
        title_line = lines[0].lstrip("﻿").strip()
        meta = _META_RE.match(lines[1])
        if not meta:
            continue
        body = " ".join(ln for ln in lines[2:] if ln).strip()
        author_match = _AUTHOR_RE.search(title_line)
        author = author_match.group(1).strip() if author_match else ""
        book = _AUTHOR_RE.sub("", title_line).strip() or title_line
        loc = _LOC_RE.search(meta.group(2))
        page = _PAGE_RE.search(meta.group(2))
        yield {
            "type": meta.group(1).lower(),
            "book": book,
            "author": author,
            "locStart": int(loc.group(1)) if loc else None,
            "locEnd": int(loc.group(2) or loc.group(1)) if loc else None,
            "page": page.group(1) if page else None,
            "text": body,
        }


def parse(path: Path, options: dict[str, Any]) -> Iterator[dict[str, Any]]:
    text = Path(path).read_text(encoding="utf-8-sig", errors="replace")
    book_filter = str(options.get("book") or "").strip().lower()

    highlights: dict[tuple[str, int | None, str], dict[str, Any]] = {}
    order: list[tuple[str, int | None, str]] = []
    notes: list[dict[str, Any]] = []
    for entry in _parse_entries(text):
        if book_filter and book_filter not in entry["book"].lower():
            continue
        if entry["type"] == "note" and entry["text"]:
            notes.append(entry)
            continue
        if entry["type"] != "highlight" or len(entry["text"]) < _MIN_HIGHLIGHT_CHARS:
            continue
        key = (entry["book"], entry["locStart"], entry["text"][:24].lower())
        # Kindle re-appends a highlight when it is extended; keep the longest copy.
        same_spot = next(
            (k for k in order if k[0] == entry["book"] and k[1] is not None and k[1] == entry["locStart"]), None
        )
        if same_spot is not None:
            if len(entry["text"]) > len(highlights[same_spot]["text"]):
                highlights[same_spot] = entry
            continue
        highlights[key] = entry
        order.append(key)

    for note in notes:
        for key in order:
            h = highlights[key]
            if h["book"] == note["book"] and h["locStart"] is not None and note["locStart"] is not None and (
                h["locStart"] <= note["locStart"] <= (h["locEnd"] or h["locStart"])
            ):
                h["note"] = (h.get("note", "") + "\n" + note["text"]).strip()
                break

    for key in order:
        h = highlights[key]
        where = f"p. {h['page']}" if h["page"] else (f"loc. {h['locStart']}" if h["locStart"] else "")
        source = h["book"] + (f" ({h['author']})" if h["author"] else "")
        words = h["text"].split()
        lead = " ".join(words[:12]) + (" ..." if len(words) > 12 else "")
        yield {
            # The passage lead keeps each prompt distinct (core de-duplicates identical prompts).
            "q": f'From {h["book"]}: "{lead}" What is the idea here, and why does it hold?',
            "a": h["text"],
            "statement": h["text"],
            "chapter": h["book"],
            "notes": "\n".join(x for x in (f"{source}, {where}".strip(", "), h.get("note", "")) if x),
            "tags": ["highlight"],
        }


SYNTOPIK_IMPORTER = {
    "name": "kindle",
    "description": "Kindle 'My Clippings.txt' highlights (notes attach to their highlight)",
    "extensions": [".txt"],
    "accepts": "file",
    "apiVersion": 1,
    "parse": parse,
}
