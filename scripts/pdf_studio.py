#!/usr/bin/env python3
"""Universal PDF Studio for Syntopik.

Handles ANY PDF: native digital text, scanned/image-only pages, handwritten
notes, diagram-heavy textbooks (Rahul Sardana, Cengage, NCERT, MS Chouhan),
tables, and PYQ papers. Never silently skips image-only pages.
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import hashlib
import html as html_lib
import io
import json
import math
import os
import re
import shutil
import subprocess
import sys
import unicodedata
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from syntopik.storage import save_json_atomic  # noqa: E402

import pymupdf as fitz
import pymupdf4llm


@contextlib.contextmanager
def _silence_stdout():
    """Suppress both Python-level and C/fd-1 stdout chatter from pymupdf4llm/RapidOCR."""
    sys_stdout = io.StringIO()
    saved_fd = os.dup(1)
    try:
        with open(os.devnull, "w", encoding="utf-8") as devnull:
            os.dup2(devnull.fileno(), 1)
            with contextlib.redirect_stdout(sys_stdout):
                yield
    finally:
        os.dup2(saved_fd, 1)
        os.close(saved_fd)


def open_pdf(path_str: str) -> tuple[Path, fitz.Document]:
    pdf_path = Path(path_str).expanduser().resolve()
    if not pdf_path.exists():
        raise SystemExit(f"PDF not found: {pdf_path}")
    try:
        doc = fitz.open(str(pdf_path))
    except Exception as exc:
        raise SystemExit(f"Failed to open PDF '{pdf_path}': {exc}")
    return pdf_path, doc


def parse_page_spec(spec: str | None, total_pages: int) -> list[int]:
    """Parse 1-based page specs like '1-5,8,12-14' into sorted 0-based indices."""
    if not spec:
        return list(range(total_pages))
    pages: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start_s, end_s = part.split("-", 1)
            try:
                a = int(start_s.strip())
                b = int(end_s.strip())
            except ValueError:
                raise SystemExit(f"Invalid page range in --pages: '{part}'")
            lo, hi = (a, b) if a <= b else (b, a)
            start = max(1, lo)
            end = min(total_pages, hi)
            for p in range(start, end + 1):
                pages.add(p - 1)
        else:
            try:
                p = int(part)
            except ValueError:
                raise SystemExit(f"Invalid page number in --pages: '{part}'")
            if 1 <= p <= total_pages:
                pages.add(p - 1)
    return sorted(pages)


def _largest_image_ratio(page: fitz.Page) -> float:
    page_area = float(page.rect.get_area())
    if page_area <= 0:
        return 0.0
    max_ratio = 0.0
    try:
        for info in page.get_image_info(xrefs=True):
            bbox = info.get("bbox")
            if not bbox or len(bbox) != 4:
                continue
            r = fitz.Rect(bbox) & page.rect
            ratio = float(r.get_area()) / page_area
            if ratio > max_ratio:
                max_ratio = ratio
    except Exception:
        pass
    return round(min(1.0, max_ratio), 3)


_MATH_EXTRA_CHARS = frozenset("∫∑∏√∂∇∞≈≠≤≥±×÷∈∉⊂⊃∪∩∧∨¬∀∃∅°²³¹µ·¼½¾=^+<>" )


def compute_math_density(text: str) -> float:
    """Fraction (0.0..1.0) of non-whitespace characters that are math/Greek/operator/super-subscript symbols."""
    non_ws = [ch for ch in text if not ch.isspace()]
    if not non_ws:
        return 0.0
    math_chars = 0
    for ch in non_ws:
        cp = ord(ch)
        if (
            (0x2200 <= cp <= 0x22FF)      # Mathematical Operators
            or (0x2100 <= cp <= 0x214F)   # Letterlike Symbols
            or (0x2190 <= cp <= 0x21FF)   # Arrows
            or (0x27C0 <= cp <= 0x27EF)   # Misc Mathematical Symbols-A
            or (0x2980 <= cp <= 0x29FF)   # Misc Mathematical Symbols-B
            or (0x2A00 <= cp <= 0x2AFF)   # Supplemental Mathematical Operators
            or (0x0370 <= cp <= 0x03FF)   # Greek and Coptic
            or (0x2070 <= cp <= 0x209F)   # Superscripts and Subscripts
            or (0x1D400 <= cp <= 0x1D7FF) # Mathematical Alphanumeric Symbols
            or ch in _MATH_EXTRA_CHARS
            or unicodedata.category(ch) == "Sm"
        ):
            math_chars += 1
    return round(min(1.0, math_chars / len(non_ws)), 4)


def classify_document_size(total_pages: int) -> tuple[str, str]:
    """Return (size_class, recommended_strategy) matching LearnHarness's size-tiered reading doctrine."""
    if total_pages <= 10:
        return (
            "short",
            "Short PDF (<=10p): extract all pages; visually inspect every page flagged needs_vision "
            "(image-only, images>0, or math_density>=0.03) via render/annotate + read_file.",
        )
    if total_pages <= 40:
        return (
            "medium",
            "Medium PDF (11-40p): use TOC/tree-index to extract promising ranges; visually inspect only "
            "needs_vision pages (delegate >15p reading to a research subagent returning a cited (p. N) brief).",
        )
    return (
        "long",
        "Long PDF (>40p): never read front-to-back; run tree-index and search for key terms first, "
        "then extract & visually inspect only hit pages (delegate synthesis to a research subagent).",
    )


def classify_page(page: fitz.Page) -> dict[str, Any]:
    text = page.get_text("text").strip()
    chars = len(text)
    images = page.get_images(full=True)
    drawings = page.get_drawings()
    img_count = len(images)
    draw_count = len(drawings)
    largest_img_ratio = _largest_image_ratio(page)
    math_density = compute_math_density(text)

    if chars == 0 and img_count == 0 and draw_count == 0:
        kind = "blank"
    elif (chars < 45 and (img_count > 0 or draw_count > 5)) or (largest_img_ratio >= 0.65 and chars < 180):
        kind = "image-only"
    elif img_count > 0 or draw_count > 25:
        kind = "text+diagrams"
    else:
        kind = "text"

    needs_vision = (kind == "image-only") or (img_count > 0) or (math_density >= 0.03)

    return {
        "page": page.number + 1,
        "kind": kind,
        "chars": chars,
        "images": img_count,
        "vectorDrawings": draw_count,
        "largestImageRatio": largest_img_ratio,
        "math_density": math_density,
        "needs_vision": needs_vision,
        "width": round(page.rect.width, 1),
        "height": round(page.rect.height, 1),
    }


def make_ocr_engine():
    try:
        from rapidocr_onnxruntime import RapidOCR

        return ("rapidocr", RapidOCR())
    except Exception:
        if shutil.which("tesseract"):
            return ("tesseract", None)
        return ("none", None)


def ocr_image_file(engine_tuple, image_path: Path) -> str:
    kind, engine = engine_tuple
    if kind == "rapidocr" and engine is not None:
        with contextlib.redirect_stdout(io.StringIO()):
            try:
                # Passing raw bytes avoids OpenCV non-ASCII path quirks on Unicode paths.
                result, _ = engine(image_path.read_bytes())
            except Exception:
                result, _ = engine(str(image_path))
        if not result:
            return ""
        return "\n".join(line[1] for line in result if len(line) >= 2 and line[1]).strip()
    if kind == "tesseract":
        proc = subprocess.run(
            ["tesseract", str(image_path), "stdout", "--psm", "6"],
            capture_output=True,
            text=True,
            check=False,
        )
        return proc.stdout.strip()
    return ""


def render_page_png(
    page: fitz.Page,
    out_path: Path,
    dpi: int = 180,
    crop_norm: tuple[float, float, float, float] | None = None,
) -> Path:
    clip = None
    if crop_norm is not None:
        x0, y0, x1, y1 = crop_norm
        if not (0.0 <= x0 < x1 <= 1.0 and 0.0 <= y0 < y1 <= 1.0):
            raise SystemExit("--crop coordinates must satisfy 0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1")
        rect = page.rect
        clip = fitz.Rect(
            rect.x0 + rect.width * x0,
            rect.y0 + rect.height * y0,
            rect.x0 + rect.width * x1,
            rect.y0 + rect.height * y1,
        )
    zoom = dpi / 72.0
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=clip, alpha=False)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    pix.save(str(out_path))
    return out_path


def slugify(name: str) -> str:
    """Unicode-safe deterministic slug that avoids collisions for non-ASCII filenames."""
    normalized = unicodedata.normalize("NFKD", name)
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_only.lower()).strip("-")
    has_non_ascii = any(ord(ch) > 127 for ch in name)
    if not slug:
        digest = hashlib.sha256(name.encode("utf-8")).hexdigest()[:8]
        return f"doc-{digest}"
    if has_non_ascii:
        digest = hashlib.sha256(name.encode("utf-8")).hexdigest()[:6]
        return f"{slug[:48]}-{digest}"
    return slug[:64]


def _format_markdown_table(rows: list[list[Any]]) -> str:
    cleaned = []
    for row in rows:
        if not row:
            continue
        cells = [re.sub(r"\s+", " ", str(c or "")).strip().replace("|", "\\|") for c in row]
        if any(cells):
            cleaned.append(cells)
    if not cleaned:
        return ""
    ncols = max(len(r) for r in cleaned)
    norm = [r + [""] * (ncols - len(r)) for r in cleaned]
    header = "| " + " | ".join(norm[0]) + " |"
    sep = "| " + " | ".join(["---"] * ncols) + " |"
    body = ["| " + " | ".join(r) + " |" for r in norm[1:]]
    return "\n".join([header, sep] + body)


def extract_page_tables(pdf_path: Path, doc: fitz.Document, idx: int) -> tuple[str, list[dict[str, Any]]]:
    """Extract structured tables using pdfplumber if available, else PyMuPDF find_tables()."""
    tables_out: list[dict[str, Any]] = []
    engine_used = "pymupdf"
    try:
        import pdfplumber  # type: ignore

        engine_used = "pdfplumber"
        with pdfplumber.open(str(pdf_path)) as pdoc:
            ppage = pdoc.pages[idx]
            raw_tables = ppage.extract_tables() or []
            if not raw_tables:
                # Retry borderless table extraction (common in JEE answer keys & data tables)
                raw_tables = ppage.extract_tables(
                    table_settings={"vertical_strategy": "text", "horizontal_strategy": "text"}
                ) or []
            for ti, rows in enumerate(raw_tables, start=1):
                md = _format_markdown_table(rows)
                if md:
                    tables_out.append({
                        "tableIndex": ti,
                        "rows": len(rows),
                        "cols": max((len(r) for r in rows if r), default=0),
                        "data": [[str(c or "").strip() for c in r] for r in rows if r],
                        "markdown": md,
                    })
        if tables_out:
            return engine_used, tables_out
    except ImportError:
        pass
    except Exception:
        pass

    # Fallback (or primary if pdfplumber isn't installed / found nothing): PyMuPDF find_tables
    engine_used = "pymupdf"
    page = doc[idx]
    try:
        finder = page.find_tables()
        for ti, tbl in enumerate(getattr(finder, "tables", []), start=1):
            rows = tbl.extract() or []
            md = _format_markdown_table(rows)
            if md:
                tables_out.append({
                    "tableIndex": ti,
                    "rows": len(rows),
                    "cols": max((len(r) for r in rows if r), default=0),
                    "data": [[str(c or "").strip() for c in r] for r in rows if r],
                    "markdown": md,
                })
    except Exception:
        pass
    return engine_used, tables_out


def cmd_inspect(args: argparse.Namespace) -> None:
    pdf_path, doc = open_pdf(args.pdf)
    with doc:
        toc = [{"level": lvl, "title": title.strip(), "page": pg} for lvl, title, pg in doc.get_toc()]
        summary = {"text": 0, "text+diagrams": 0, "image-only": 0, "blank": 0}
        pages = []
        for page in doc:
            info = classify_page(page)
            summary[info["kind"]] += 1
            pages.append(info)

        size_class, recommended_strategy = classify_document_size(len(doc))
        vision_pages = [p["page"] for p in pages if p["needs_vision"]]
        recommendation = (
            "Mostly scanned/image-only: extract with OCR + inspect rendered PNGs directly with vision."
            if pages and summary["image-only"] > len(pages) * 0.3
            else "Diagram-rich digital PDF: extract text via pymupdf4llm + inspect rendered page PNGs for diagrams/equations."
            if summary["text+diagrams"] > 0 or vision_pages
            else "Clean digital text PDF: fast pymupdf4llm extraction."
        )
        payload = {
            "file": str(pdf_path),
            "totalPages": len(doc),
            "size_class": size_class,
            "recommended_strategy": recommended_strategy,
            "visionPages": vision_pages,
            "summary": summary,
            "recommendation": recommendation,
            "toc": toc[:40],
            "pages": pages[: args.limit],
        }
    print(json.dumps(payload, indent=2, ensure_ascii=False))


def cmd_extract(args: argparse.Namespace) -> None:
    pdf_path, doc = open_pdf(args.pdf)
    with doc:
        indices = parse_page_spec(args.pages, len(doc))
        out_dir = (
            Path(args.out).expanduser().resolve()
            if args.out
            else (Path.cwd() / "sources" / "_extracted" / slugify(pdf_path.stem)).resolve()
        )
        pages_dir = out_dir / "pages"
        figures_dir = out_dir / "figures"
        out_dir.mkdir(parents=True, exist_ok=True)

        # Detect repeated watermark/logo xrefs across pages so figures/ stays clean.
        watermark_xrefs: set[int] = set()
        if args.extract_figures and len(indices) >= 4:
            xref_pages: dict[int, int] = {}
            for idx in indices:
                for xref in {img[0] for img in doc[idx].get_images(full=True)}:
                    xref_pages[xref] = xref_pages.get(xref, 0) + 1
            threshold = max(3, int(len(indices) * 0.5))
            watermark_xrefs = {x for x, cnt in xref_pages.items() if cnt >= threshold}

        ocr_engine = None
        md_sections: list[str] = [
            f"# Extracted: {pdf_path.name}\n",
            f"- Source: `{pdf_path}`\n",
            f"- Pages extracted: {len(indices)} of {len(doc)}\n",
        ]
        manifest_pages: list[dict[str, Any]] = []

        for idx in indices:
            page = doc[idx]
            info = classify_page(page)
            page_num = idx + 1
            rendered_png: str | None = None
            extracted_figures: list[str] = []

            needs_render = (
                args.render_all
                or info["kind"] in ("image-only", "text+diagrams")
            )
            if needs_render:
                png_path = render_page_png(page, pages_dir / f"page-{page_num:04d}.png", dpi=args.dpi)
                rendered_png = str(png_path)

            # Extract unique embedded raster figures above trivial icon sizes.
            if args.extract_figures:
                seen_page_xrefs: set[int] = set()
                fig_counter = 0
                for img_info in page.get_images(full=True):
                    xref = img_info[0]
                    if xref in seen_page_xrefs or xref in watermark_xrefs:
                        continue
                    seen_page_xrefs.add(xref)
                    try:
                        base = doc.extract_image(xref)
                        w, h = base.get("width", 0), base.get("height", 0)
                        if w < 120 or h < 80:
                            continue
                        fig_counter += 1
                        ext = base.get("ext", "png")
                        fig_path = figures_dir / f"p{page_num:04d}-fig{fig_counter:02d}.{ext}"
                        fig_path.parent.mkdir(parents=True, exist_ok=True)
                        fig_path.write_bytes(base["image"])
                        extracted_figures.append(str(fig_path))
                    except Exception:
                        continue

            page_md = ""
            method = "pymupdf4llm"
            if info["kind"] != "image-only" or args.ocr == "never":
                try:
                    # pymupdf4llm 2.1 prints parser banners to fd 1; keep CLI stdout pure JSON.
                    with _silence_stdout():
                        page_md = pymupdf4llm.to_markdown(doc, pages=[idx]).strip()
                except Exception:
                    page_md = page.get_text("text").strip()

            run_ocr = (
                args.ocr == "always"
                or (args.ocr == "auto" and (info["kind"] == "image-only" or len(page_md) < 40))
            )
            ocr_text = ""
            if run_ocr and info["kind"] != "blank":
                if rendered_png is None:
                    png_path = render_page_png(page, pages_dir / f"page-{page_num:04d}.png", dpi=args.dpi)
                    rendered_png = str(png_path)
                if ocr_engine is None:
                    ocr_engine = make_ocr_engine()
                ocr_text = ocr_image_file(ocr_engine, Path(rendered_png))
                method = f"ocr:{ocr_engine[0]}" if not page_md else f"{method}+ocr:{ocr_engine[0]}"

            body = page_md
            if ocr_text:
                if info["kind"] == "image-only" or len(body) < 40:
                    body = ocr_text
                elif ocr_text not in body:
                    body = f"{body}\n\n<!-- OCR supplement -->\n{ocr_text}"

            md_sections.append(f"\n---\n\n## Page {page_num} ({info['kind']} · {method})\n")
            if rendered_png:
                md_sections.append(f"- Rendered page PNG (inspect with `read_file` for diagrams/handwriting): `{rendered_png}`\n")
            if extracted_figures:
                md_sections.append(f"- Extracted figures: {', '.join(f'`{f}`' for f in extracted_figures)}\n")
            md_sections.append("\n" + (body or "_[Blank or visual-only page — inspect rendered PNG directly]_") + "\n")

            manifest_pages.append({
                **info,
                "method": method,
                "renderedPng": rendered_png,
                "figures": extracted_figures,
                "extractedChars": len(body),
            })

        md_path = out_dir / "extracted.md"
        index_path = out_dir / "index.json"
        md_path.write_text("".join(md_sections), encoding="utf-8")
        index_payload = {
            "source": str(pdf_path),
            "outDir": str(out_dir),
            "markdown": str(md_path),
            "pagesExtracted": len(manifest_pages),
            "pages": manifest_pages,
        }
        index_path.write_text(json.dumps(index_payload, indent=2, ensure_ascii=False), encoding="utf-8")
        print(json.dumps({
            "markdown": str(md_path),
            "index": str(index_path),
            "pagesExtracted": len(manifest_pages),
            "renderedPngCount": sum(1 for p in manifest_pages if p["renderedPng"]),
            "figureCount": sum(len(p["figures"]) for p in manifest_pages),
        }, indent=2, ensure_ascii=False))


def cmd_tables(args: argparse.Namespace) -> None:
    """Extract structured tables (pdfplumber with PyMuPDF fallback); flag scanned pages for vision/OCR."""
    pdf_path, doc = open_pdf(args.pdf)
    with doc:
        indices = parse_page_spec(args.pages, len(doc))
        results: list[dict[str, Any]] = []
        scanned_pages: list[int] = []
        total_tables = 0
        md_chunks: list[str] = [f"# Extracted Tables: {pdf_path.name}\n"]

        for idx in indices:
            info = classify_page(doc[idx])
            page_num = idx + 1
            if info["kind"] == "image-only":
                scanned_pages.append(page_num)
                continue
            engine, tables = extract_page_tables(pdf_path, doc, idx)
            if tables:
                total_tables += len(tables)
                results.append({"page": page_num, "engine": engine, "tables": tables})
                md_chunks.append(f"\n## Page {page_num} ({engine})\n")
                for t in tables:
                    md_chunks.append(f"\n### Table {t['tableIndex']} ({t['rows']}×{t['cols']})\n\n{t['markdown']}\n")

        out_md = None
        if args.out:
            out_path = Path(args.out).expanduser().resolve()
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text("".join(md_chunks), encoding="utf-8")
            out_md = str(out_path)

        print(json.dumps({
            "file": str(pdf_path),
            "pagesChecked": len(indices),
            "tableCount": total_tables,
            "scannedPagesNeedingVision": scanned_pages,
            "markdownOut": out_md,
            "pages": results,
        }, indent=2, ensure_ascii=False))


def cmd_render(args: argparse.Namespace) -> None:
    pdf_path, doc = open_pdf(args.pdf)
    with doc:
        indices = parse_page_spec(args.pages, len(doc))
        out_dir = (
            Path(args.out).expanduser().resolve()
            if args.out
            else (Path.cwd() / "sources" / "_extracted" / slugify(pdf_path.stem) / "pages").resolve()
        )
        crop_norm = None
        if args.crop:
            try:
                parts = [float(v.strip()) for v in args.crop.split(",")]
            except ValueError:
                raise SystemExit("--crop must be 4 numeric values: x0,y0,x1,y1 in 0..1")
            if len(parts) != 4:
                raise SystemExit("--crop must be x0,y0,x1,y1 in 0..1 normalized coordinates")
            crop_norm = (parts[0], parts[1], parts[2], parts[3])

        written = []
        for idx in indices:
            suffix = "-crop" if crop_norm else ""
            out_path = render_page_png(doc[idx], out_dir / f"page-{idx + 1:04d}{suffix}.png", dpi=args.dpi, crop_norm=crop_norm)
            written.append(str(out_path))
    print(json.dumps({"rendered": written}, indent=2, ensure_ascii=False))


def cmd_search(args: argparse.Namespace) -> None:
    pdf_path, doc = open_pdf(args.pdf)
    query = args.query.lower()
    hits_by_page: dict[int, dict[str, Any]] = {}
    with doc:
        for page in doc:
            text = page.get_text("text")
            pos = text.lower().find(query)
            if pos != -1:
                snippet = text[max(0, pos - 90): min(len(text), pos + len(query) + 140)].replace("\n", " ")
                hits_by_page[page.number + 1] = {
                    "page": page.number + 1,
                    "source": "pdf-text",
                    "snippet": re.sub(r"\s+", " ", snippet).strip(),
                }

    extracted_md = Path.cwd() / "sources" / "_extracted" / slugify(pdf_path.stem) / "extracted.md"
    if extracted_md.exists():
        content = extracted_md.read_text(encoding="utf-8")
        for block in content.split("\n## Page "):
            if query in block.lower():
                first_line = block.splitlines()[0] if block.splitlines() else ""
                m = re.match(r"(\d+)", first_line)
                if m:
                    pnum = int(m.group(1))
                    if pnum not in hits_by_page:
                        pos = block.lower().find(query)
                        snippet = block[max(0, pos - 90): min(len(block), pos + len(query) + 140)].replace("\n", " ")
                        hits_by_page[pnum] = {
                            "page": pnum,
                            "source": "ocr-cache",
                            "snippet": re.sub(r"\s+", " ", snippet).strip(),
                        }

    hits = [hits_by_page[p] for p in sorted(hits_by_page)]
    print(json.dumps({"query": args.query, "totalHits": len(hits), "hits": hits[: args.limit]}, indent=2, ensure_ascii=False))


_LABEL_ONLY_RE = re.compile(
    r"^(?:chapter|unit|part|module|section|lesson|topic)\s*(?:[-–:.]?\s*)?(?:\d+|[IVXLCDM]+)\s*[:.–-]?$",
    re.IGNORECASE,
)
_NEW_SECTION_START_RE = re.compile(
    r"^(?:chapter\s+\d+|unit\s+\d+|section\s+\d+|\d+(?:\.\d+){0,3}\s+[A-Za-z]|exercise\s*\d*|illustration\s*\d*|example\s*\d*|level\s*[-–]?\s*\d+|single\s+correct|multiple\s+correct|integer\s+type|matrix\s+match|previous\s+years|jee\s+(?:main|adv))",
    re.IGNORECASE,
)


def _norm_heading(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def _is_meaningful_heading(text: str) -> bool:
    clean = re.sub(r"\s+", " ", text).strip()
    if len(clean) < 3 or len(clean) > 160:
        return False
    alpha_count = sum(1 for ch in clean if ch.isalpha())
    if alpha_count < 3:
        return False
    # Reject bare page numbers / URLs / boilerplate
    if re.match(r"^(?:page\s*\d+|www\.|https?://)", clean, re.IGNORECASE):
        return False
    return True


def _extract_heuristic_sections(doc: fitz.Document) -> list[dict[str, Any]]:
    total = len(doc)
    sizes: list[float] = []
    for idx in range(min(total, 20)):
        for block in doc[idx].get_text("dict").get("blocks", []):
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    if span.get("text", "").strip():
                        sizes.append(round(float(span.get("size", 10.0)), 1))
    body_size = sorted(sizes)[len(sizes) // 2] if sizes else 10.5

    raw_by_page: dict[int, list[dict[str, Any]]] = {}
    edge_counts: dict[str, set[int]] = {}

    for idx, page in enumerate(doc):
        page_num = idx + 1
        page_h = max(1.0, float(page.rect.height))
        candidates: list[dict[str, Any]] = []
        for block in page.get_text("dict").get("blocks", []):
            if block.get("type", 0) != 0:
                continue
            for line in block.get("lines", []):
                spans = line.get("spans", [])
                if not spans:
                    continue
                text = re.sub(r"\s+", " ", "".join(s.get("text", "") for s in spans)).strip()
                if not _is_meaningful_heading(text):
                    continue
                max_sz = max(float(s.get("size", 0)) for s in spans)
                is_bold = any(bool(s.get("flags", 0) & 16) for s in spans)
                if not (max_sz >= body_size * 1.22 or (is_bold and _NEW_SECTION_START_RE.match(text))):
                    continue
                bbox = line.get("bbox", [0.0, 0.0, 0.0, 0.0])
                x0, y0, x1, y1 = float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])
                lvl = 1 if max_sz >= body_size * 1.45 else 2
                y_rel = y0 / page_h
                if y_rel <= 0.09 or y_rel >= 0.91:
                    edge_counts.setdefault(_norm_heading(text), set()).add(page_num)
                candidates.append({
                    "page": page_num,
                    "x0": x0,
                    "y0": y0,
                    "x1": x1,
                    "y1": y1,
                    "text": text,
                    "size": max_sz,
                    "bold": is_bold,
                    "level": lvl,
                    "y_rel": y_rel,
                })
        candidates.sort(key=lambda c: (round(c["y0"], 1), round(c["x0"], 1)))
        raw_by_page[page_num] = candidates

    # Filter repeating running headers/footers across >= 3 distinct pages.
    running_headers = {
        norm for norm, pgs in edge_counts.items() if total >= 4 and len(pgs) >= 3
    }

    sections: list[dict[str, Any]] = []
    for page_num in range(1, total + 1):
        cands = [
            c for c in raw_by_page.get(page_num, [])
            if not ((c["y_rel"] <= 0.09 or c["y_rel"] >= 0.91) and _norm_heading(c["text"]) in running_headers)
        ]
        # Merge consecutive lines belonging to the same multi-line heading (e.g. "Trigonometric" + "Equations").
        merged: list[dict[str, Any]] = []
        for c in cands:
            if merged:
                prev = merged[-1]
                v_gap = c["y0"] - prev["y1"]
                max_sz = max(prev["size"], c["size"], 1.0)
                size_close = abs(c["size"] - prev["size"]) <= max_sz * 0.25
                prev_is_label = bool(_LABEL_ONLY_RE.match(prev["text"]))
                curr_starts_new = bool(_NEW_SECTION_START_RE.match(c["text"]))
                close_vertically = (-max_sz * 0.6) <= v_gap <= (max_sz * 1.9)
                combined_len = len(prev["text"]) + 1 + len(c["text"])

                if close_vertically and combined_len <= 160 and not curr_starts_new and (size_close or prev_is_label):
                    sep = ": " if (prev_is_label and not prev["text"].endswith((":", "-", "–"))) else " "
                    prev["text"] = f"{prev['text']}{sep}{c['text']}"
                    prev["y1"] = max(prev["y1"], c["y1"])
                    prev["x1"] = max(prev["x1"], c["x1"])
                    prev["level"] = min(prev["level"], c["level"])
                    prev["size"] = max(prev["size"], c["size"])
                    continue
            merged.append(dict(c))

        seen_norm: set[str] = set()
        for m in merged:
            norm = _norm_heading(m["text"])
            if norm in seen_norm:
                continue
            seen_norm.add(norm)
            sections.append({
                "level": m["level"],
                "title": m["text"],
                "startPage": page_num,
                "endPage": page_num,
            })

    # Compute hierarchical endPage: each section runs until the next section of same or higher level.
    for i, sec in enumerate(sections):
        end_pg = total
        for j in range(i + 1, len(sections)):
            if sections[j]["level"] <= sec["level"]:
                end_pg = max(sec["startPage"], sections[j]["startPage"] - 1)
                break
        sec["endPage"] = end_pg

    return sections


def cmd_tree_index(args: argparse.Namespace) -> None:
    """Build a PageIndex-style vectorless hierarchical TOC & section-page map (zero API keys)."""
    pdf_path, doc = open_pdf(args.pdf)
    with doc:
        total = len(doc)
        out_dir = (
            Path(args.out).expanduser().resolve()
            if args.out
            else (Path.cwd() / "sources" / "_extracted" / slugify(pdf_path.stem)).resolve()
        )
        out_dir.mkdir(parents=True, exist_ok=True)

        toc = doc.get_toc()
        sections: list[dict[str, Any]] = []
        source_kind = "pdf-bookmarks"

        if toc:
            cleaned_toc = [
                (max(1, int(lvl)), re.sub(r"\s+", " ", str(title)).strip(), max(1, min(total, int(pg))))
                for lvl, title, pg in toc
                if str(title).strip()
            ]
            for i, (lvl, title, page_num) in enumerate(cleaned_toc):
                next_page = total
                for j in range(i + 1, len(cleaned_toc)):
                    if cleaned_toc[j][0] <= lvl:
                        next_page = max(page_num, cleaned_toc[j][2] - 1)
                        break
                sections.append({
                    "level": lvl,
                    "title": title,
                    "startPage": page_num,
                    "endPage": next_page,
                })
        else:
            source_kind = "heading-heuristic"
            sections = _extract_heuristic_sections(doc)
            if not sections:
                source_kind = "chunked-fallback"
                step = max(1, args.chunk_size)
                for p in range(1, total + 1, step):
                    sections.append({
                        "level": 1,
                        "title": f"Pages {p}-{min(total, p + step - 1)}",
                        "startPage": p,
                        "endPage": min(total, p + step - 1),
                    })

        tree_json = out_dir / "tree-index.json"
        tree_md = out_dir / "tree-index.md"
        payload = {
            "source": str(pdf_path),
            "totalPages": total,
            "indexMethod": source_kind,
            "sectionCount": len(sections),
            "sections": sections,
        }
        tree_json.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

        md_lines = [
            f"# Vectorless Tree Index: {pdf_path.name}",
            "",
            f"- Source: `{pdf_path}`",
            f"- Total pages: **{total}** · Method: `{source_kind}` · Sections: **{len(sections)}**",
            "",
        ]
        for s in sections:
            indent = "  " * max(0, s["level"] - 1)
            md_lines.append(f"{indent}- **{s['title']}** — pp. `{s['startPage']}–{s['endPage']}`")
        tree_md.write_text("\n".join(md_lines) + "\n", encoding="utf-8")

        print(json.dumps({
            "treeJson": str(tree_json),
            "treeMarkdown": str(tree_md),
            "indexMethod": source_kind,
            "sectionCount": len(sections),
            "preview": sections[:12],
        }, indent=2, ensure_ascii=False))


def _parse_crop_arg(crop_str: str | None) -> tuple[float, float, float, float] | None:
    if not crop_str:
        return None
    try:
        parts = [float(v.strip()) for v in crop_str.split(",")]
    except ValueError:
        raise SystemExit("--crop must be 4 numeric values: x0,y0,x1,y1 in 0..1")
    if len(parts) != 4:
        raise SystemExit("--crop must be x0,y0,x1,y1 in 0..1 normalized coordinates")
    x0, y0, x1, y1 = parts
    if not (0.0 <= x0 < x1 <= 1.0 and 0.0 <= y0 < y1 <= 1.0):
        raise SystemExit("--crop coordinates must satisfy 0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1")
    return (x0, y0, x1, y1)


def _load_marks_spec(marks_arg: str, default_coords: str = "norm") -> list[dict[str, Any]]:
    raw = marks_arg.strip()
    if not raw:
        return []
    candidate_path: Path | None = None
    if raw.startswith("@"):
        candidate_path = Path(raw[1:]).expanduser()
    elif not raw.startswith(("[", "{")):
        p = Path(raw).expanduser()
        if p.exists() and p.is_file():
            candidate_path = p
    if candidate_path is not None:
        if not candidate_path.exists():
            raise SystemExit(f"Marks JSON file not found: {candidate_path}")
        raw = candidate_path.read_text(encoding="utf-8")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid --marks JSON: {exc}")

    top_coords = default_coords
    if isinstance(parsed, dict):
        if isinstance(parsed.get("coords"), str):
            top_coords = parsed["coords"].strip().lower()
        marks_raw = parsed.get("marks", [])
    elif isinstance(parsed, list):
        marks_raw = parsed
    else:
        raise SystemExit("--marks must be a JSON array of callouts or an object containing a 'marks' array")

    if not isinstance(marks_raw, list):
        raise SystemExit("--marks must decode to a list of mark objects")

    normalized: list[dict[str, Any]] = []
    for idx, item in enumerate(marks_raw):
        if not isinstance(item, dict):
            raise SystemExit(f"Mark at index {idx} must be a JSON object")
        m = dict(item)
        m.setdefault("coords", top_coords)
        normalized.append(m)
    return normalized


def _resolve_xy(pt: Any, width: int, height: int, coords: str, field_name: str) -> tuple[float, float]:
    if not isinstance(pt, (list, tuple)) or len(pt) != 2:
        raise SystemExit(f"Mark field '{field_name}' must be a [x, y] pair")
    x, y = float(pt[0]), float(pt[1])
    if coords == "px":
        return x, y
    return x * width, y * height


def _resolve_marks_px(marks: list[dict[str, Any]], width: int, height: int) -> list[dict[str, Any]]:
    """Resolve normalized or pixel marks into concrete pixel geometry with defaults."""
    resolved: list[dict[str, Any]] = []
    min_dim = float(min(width, height))
    default_colors = {
        "arrow": "#e11d48",
        "box": "#2563eb",
        "rect": "#2563eb",
        "circle": "#d97706",
        "label": "#e11d48",
        "text": "#e11d48",
    }
    for idx, raw in enumerate(marks):
        mtype = str(raw.get("type", "")).strip().lower()
        if mtype == "rect":
            mtype = "box"
        elif mtype == "text":
            mtype = "label"
        if mtype not in ("arrow", "box", "circle", "label"):
            raise SystemExit(f"Unsupported mark type at index {idx}: '{mtype}' (expected arrow, box, circle, label)")

        coords = str(raw.get("coords", "norm")).strip().lower()
        if coords not in ("norm", "px"):
            raise SystemExit(f"Mark at index {idx} has invalid coords='{coords}' (expected 'norm' or 'px')")

        color = str(raw.get("color") or default_colors[mtype]).strip()
        label = str(raw.get("label") if raw.get("label") is not None else raw.get("text", "")).strip()
        dashed = bool(raw.get("dashed", False))
        mark_id = str(raw.get("id") or f"m{idx + 1}").strip()
        prop_id = str(raw.get("propId") or raw.get("prop_id") or "").strip()

        if mtype == "arrow":
            x1, y1 = _resolve_xy(raw.get("from"), width, height, coords, "from")
            x2, y2 = _resolve_xy(raw.get("to"), width, height, coords, "to")
            resolved.append({
                "id": mark_id,
                "propId": prop_id,
                "type": "arrow",
                "from": (x1, y1),
                "to": (x2, y2),
                "label": label,
                "color": color,
                "dashed": dashed,
            })
        elif mtype == "box":
            rect_val = raw.get("rect")
            if not isinstance(rect_val, (list, tuple)) or len(rect_val) != 4:
                raise SystemExit(f"Box mark at index {idx} requires 'rect': [x0, y0, x1, y1]")
            rx0, ry0 = _resolve_xy(rect_val[:2], width, height, coords, "rect[0:2]")
            rx1, ry1 = _resolve_xy(rect_val[2:], width, height, coords, "rect[2:4]")
            x0, x1 = (rx0, rx1) if rx0 <= rx1 else (rx1, rx0)
            y0, y1 = (ry0, ry1) if ry0 <= ry1 else (ry1, ry0)
            resolved.append({
                "id": mark_id,
                "propId": prop_id,
                "type": "box",
                "rect": (x0, y0, x1, y1),
                "label": label,
                "color": color,
                "dashed": dashed,
            })
        elif mtype == "circle":
            cx, cy = _resolve_xy(raw.get("center"), width, height, coords, "center")
            r_raw = float(raw.get("r", 0.06 if coords == "norm" else 36.0))
            r_px = r_raw if coords == "px" else r_raw * min_dim
            resolved.append({
                "id": mark_id,
                "propId": prop_id,
                "type": "circle",
                "center": (cx, cy),
                "r": max(4.0, r_px),
                "label": label,
                "color": color,
                "dashed": dashed,
            })
        else:  # label
            ax, ay = _resolve_xy(raw.get("at"), width, height, coords, "at")
            anchor = str(raw.get("anchor", "start")).strip().lower()
            if anchor not in ("start", "middle", "end"):
                anchor = "start"
            resolved.append({
                "id": mark_id,
                "propId": prop_id,
                "type": "label",
                "at": (ax, ay),
                "text": label,
                "color": color,
                "anchor": anchor,
            })
    return resolved


def _annotation_metrics(width: int, height: int) -> dict[str, float]:
    diag_scale = math.sqrt(max(1.0, float(width * height)))
    font_size = max(15.0, min(32.0, round(diag_scale * 0.026, 1)))
    stroke_w = max(3.0, round(font_size * 0.17, 1))
    halo_w = max(4.0, round(font_size * 0.28, 1))
    head_len = max(13.0, round(font_size * 0.82, 1))
    head_half = round(head_len * 0.44, 1)
    return {
        "font_size": font_size,
        "stroke_w": stroke_w,
        "halo_w": halo_w,
        "head_len": head_len,
        "head_half": head_half,
        "pad": max(10.0, round(font_size * 0.52, 1)),
    }


def _arrow_geometry(
    x1: float, y1: float, x2: float, y2: float, head_len: float, head_half: float
) -> tuple[float, float, list[tuple[float, float]]]:
    dx, dy = x2 - x1, y2 - y1
    length = math.hypot(dx, dy)
    if length < 1e-3:
        return x2, y2, [(x2, y2), (x2 - head_len, y2 - head_half), (x2 - head_len, y2 + head_half)]
    ux, uy = dx / length, dy / length
    px, py = -uy, ux
    eff_len = min(head_len, length * 0.45)
    eff_half = head_half * (eff_len / max(head_len, 1e-3))
    base_x = x2 - ux * eff_len
    base_y = y2 - uy * eff_len
    shaft_end_x = x2 - ux * (eff_len * 0.72)
    shaft_end_y = y2 - uy * (eff_len * 0.72)
    poly = [
        (x2, y2),
        (base_x + px * eff_half, base_y + py * eff_half),
        (base_x - px * eff_half, base_y - py * eff_half),
    ]
    return shaft_end_x, shaft_end_y, poly


def _clamp_label_xy(
    x: float,
    y: float,
    text: str,
    width: int,
    height: int,
    font_size: float,
    pad: float,
    anchor: str = "start",
) -> tuple[float, float]:
    lines = text.splitlines() or [""]
    max_chars = max((len(ln) for ln in lines), default=1)
    est_w = max_chars * font_size * 0.58
    line_h = font_size * 1.22
    total_h = line_h * len(lines)

    if anchor == "middle":
        x = max(pad + est_w / 2.0, min(width - pad - est_w / 2.0, x))
    elif anchor == "end":
        x = max(pad + est_w, min(width - pad, x))
    else:
        x = max(pad, min(width - pad - est_w, x))

    y = max(pad + font_size, min(height - pad - max(0.0, total_h - font_size), y))
    return x, y


def _compute_mark_label_placement(
    mark: dict[str, Any], width: int, height: int, metrics: dict[str, float]
) -> tuple[float, float, str] | None:
    fs = metrics["font_size"]
    pad = metrics["pad"]
    mtype = mark["type"]

    if mtype == "label":
        text = mark.get("text", "")
        if not text:
            return None
        ax, ay = mark["at"]
        anchor = mark.get("anchor", "start")
        cx, cy = _clamp_label_xy(ax, ay, text, width, height, fs, pad, anchor)
        return cx, cy, anchor

    text = mark.get("label", "")
    if not text:
        return None

    if mtype == "arrow":
        x1, y1 = mark["from"]
        x2, y2 = mark["to"]
        dx, dy = x2 - x1, y2 - y1
        length = math.hypot(dx, dy) or 1.0
        # Place label clearly behind the tail so it never touches the shaft or target
        off = max(10.0, fs * 0.52)
        lx = x1 - (dx / length) * off
        ly = y1 - (dy / length) * off
        if abs(dx) >= abs(dy):
            anchor = "end" if dx > 0 else "start"
            ly += fs * 0.30
        else:
            anchor = "middle"
            ly = (y1 - off * 0.95) if dy > 0 else (y1 + fs + off * 0.85)
        cx, cy = _clamp_label_xy(lx, ly, text, width, height, fs, pad, anchor)
        return cx, cy, anchor

    if mtype == "box":
        x0, y0, x1, y1 = mark["rect"]
        lx = x0 + 2.0
        if y0 >= fs + pad * 1.5:
            ly = y0 - pad * 0.78
        elif y1 + fs + pad * 1.5 <= height:
            ly = y1 + fs + pad * 0.55
        else:
            ly = y0 + fs + pad * 0.45
        cx, cy = _clamp_label_xy(lx, ly, text, width, height, fs, pad, "start")
        return cx, cy, "start"

    if mtype == "circle":
        cx0, cy0 = mark["center"]
        r = mark["r"]
        if cy0 - r >= fs + pad * 1.5:
            ly = cy0 - r - pad * 0.72
        else:
            ly = cy0 + r + fs + pad * 0.55
        cx, cy = _clamp_label_xy(cx0, ly, text, width, height, fs, pad, "middle")
        return cx, cy, "middle"

    return None


def _build_annotated_svg(
    base_png_bytes: bytes,
    width: int,
    height: int,
    marks_px: list[dict[str, Any]],
    title: str | None = None,
) -> str:
    metrics = _annotation_metrics(width, height)
    fs = metrics["font_size"]
    sw = metrics["stroke_w"]
    hw = metrics["halo_w"]
    head_len = metrics["head_len"]
    head_half = metrics["head_half"]
    b64 = base64.b64encode(base_png_bytes).decode("ascii")

    lines: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
    ]
    if title:
        lines.append(f"  <title>{html_lib.escape(title)}</title>")
    lines.append(
        f'  <image href="data:image/png;base64,{b64}" x="0" y="0" width="{width}" height="{height}" preserveAspectRatio="none"/>'
    )
    lines.append('  <g id="mlh-marks">')

    def _svg_text(tx: float, ty: float, text_str: str, color: str, anchor: str) -> None:
        safe_color = html_lib.escape(color, quote=True)
        text_lines = text_str.splitlines() or [""]
        line_h = round(fs * 1.22, 1)
        for i, ln in enumerate(text_lines):
            y_pos = round(ty + i * line_h, 1)
            lines.append(
                f'    <text x="{round(tx, 1)}" y="{y_pos}" text-anchor="{anchor}" '
                f'font-family="system-ui, -apple-system, Helvetica, Arial, sans-serif" '
                f'font-size="{round(fs, 1)}" font-weight="700" fill="{safe_color}" '
                f'stroke="#ffffff" stroke-width="{round(hw, 1)}" stroke-linejoin="round" '
                f'paint-order="stroke">{html_lib.escape(ln)}</text>'
            )

    for m in marks_px:
        mtype = m["type"]
        color = html_lib.escape(m["color"], quote=True)
        dash_attr = f' stroke-dasharray="{round(sw * 2.6, 1)} {round(sw * 1.6, 1)}"' if m.get("dashed") else ""

        if mtype == "arrow":
            x1, y1 = m["from"]
            x2, y2 = m["to"]
            sx, sy, poly = _arrow_geometry(x1, y1, x2, y2, head_len, head_half)
            pts_str = " ".join(f"{round(px, 1)},{round(py, 1)}" for px, py in poly)
            # High-contrast white halo underneath arrow shaft + head
            lines.append(
                f'    <line x1="{round(x1, 1)}" y1="{round(y1, 1)}" x2="{round(sx, 1)}" y2="{round(sy, 1)}" '
                f'stroke="#ffffff" stroke-opacity="0.92" stroke-width="{round(sw + 3.0, 1)}" '
                f'stroke-linecap="round"{dash_attr}/>'
            )
            lines.append(
                f'    <polygon points="{pts_str}" fill="#ffffff" stroke="#ffffff" '
                f'stroke-width="3" stroke-linejoin="round"/>'
            )
            lines.append(
                f'    <line x1="{round(x1, 1)}" y1="{round(y1, 1)}" x2="{round(sx, 1)}" y2="{round(sy, 1)}" '
                f'stroke="{color}" stroke-width="{round(sw, 1)}" stroke-linecap="round"{dash_attr}/>'
            )
            lines.append(f'    <polygon points="{pts_str}" fill="{color}"/>')

        elif mtype == "box":
            x0, y0, x1, y1 = m["rect"]
            bw, bh = max(1.0, x1 - x0), max(1.0, y1 - y0)
            lines.append(
                f'    <rect x="{round(x0, 1)}" y="{round(y0, 1)}" width="{round(bw, 1)}" height="{round(bh, 1)}" '
                f'rx="4" fill="{color}" fill-opacity="0.08" stroke="#ffffff" stroke-opacity="0.92" '
                f'stroke-width="{round(sw + 2.8, 1)}"{dash_attr}/>'
            )
            lines.append(
                f'    <rect x="{round(x0, 1)}" y="{round(y0, 1)}" width="{round(bw, 1)}" height="{round(bh, 1)}" '
                f'rx="4" fill="none" stroke="{color}" stroke-width="{round(sw, 1)}"{dash_attr}/>'
            )

        elif mtype == "circle":
            cx, cy = m["center"]
            r = m["r"]
            lines.append(
                f'    <circle cx="{round(cx, 1)}" cy="{round(cy, 1)}" r="{round(r, 1)}" '
                f'fill="{color}" fill-opacity="0.08" stroke="#ffffff" stroke-opacity="0.92" '
                f'stroke-width="{round(sw + 2.8, 1)}"{dash_attr}/>'
            )
            lines.append(
                f'    <circle cx="{round(cx, 1)}" cy="{round(cy, 1)}" r="{round(r, 1)}" '
                f'fill="none" stroke="{color}" stroke-width="{round(sw, 1)}"{dash_attr}/>'
            )

        placement = _compute_mark_label_placement(m, width, height, metrics)
        if placement is not None:
            tx, ty, anchor = placement
            label_text = m["text"] if mtype == "label" else m["label"]
            _svg_text(tx, ty, label_text, m["color"], anchor)

    lines.append("  </g>")
    lines.append("</svg>")
    return "\n".join(lines) + "\n"


def _load_annotation_font(size_px: int):
    from PIL import ImageFont

    ver = f"python{sys.version_info.major}.{sys.version_info.minor}"
    mpl_ttf_dir = Path(sys.prefix) / "lib" / ver / "site-packages" / "matplotlib" / "mpl-data" / "fonts" / "ttf"
    candidates: list[Path] = [
        mpl_ttf_dir / "DejaVuSans-Bold.ttf",
        mpl_ttf_dir / "DejaVuSans.ttf",
        # Windows venv layout fallback
        Path(sys.prefix) / "Lib" / "site-packages" / "matplotlib" / "mpl-data" / "fonts" / "ttf" / "DejaVuSans-Bold.ttf",
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf"),
        Path("/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
        Path("/System/Library/Fonts/HelveticaNeue.ttc"),
        Path("/System/Library/Fonts/Helvetica.ttc"),
        Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
        Path("/System/Library/Fonts/SFNS.ttf"),
    ]
    for font_path in candidates:
        if font_path.exists():
            try:
                return ImageFont.truetype(str(font_path), size=size_px)
            except Exception:
                continue
    return ImageFont.load_default()


def _parse_rgb_safe(color_str: str) -> tuple[int, int, int]:
    from PIL import ImageColor

    try:
        rgb = ImageColor.getrgb(color_str)
        return (int(rgb[0]), int(rgb[1]), int(rgb[2]))
    except Exception:
        return (225, 29, 72)


def _rasterize_annotated_png(
    base_img: Any,
    marks_px: list[dict[str, Any]],
    out_png: Path,
) -> Path:
    """Rasterize high-contrast marks + white-halo text directly onto the base image with Pillow."""
    from PIL import Image, ImageDraw

    base_rgba = base_img.convert("RGBA")
    width, height = base_rgba.size
    metrics = _annotation_metrics(width, height)

    # 2x supersampling for crisp anti-aliased arrows/circles on <= 1800px images
    ss = 2 if max(width, height) <= 1800 else 1
    overlay = Image.new("RGBA", (width * ss, height * ss), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    fs = int(round(metrics["font_size"] * ss))
    sw = max(2, int(round(metrics["stroke_w"] * ss)))
    halo_stroke = max(3, int(round((metrics["stroke_w"] + 2.8) * ss)))
    text_halo = max(3, int(round(metrics["halo_w"] * 0.58 * ss)))
    head_len = metrics["head_len"] * ss
    head_half = metrics["head_half"] * ss
    font = _load_annotation_font(fs)
    white_halo = (255, 255, 255, 238)

    for m in marks_px:
        mtype = m["type"]
        r_c, g_c, b_c = _parse_rgb_safe(m["color"])
        solid = (r_c, g_c, b_c, 255)
        tint = (r_c, g_c, b_c, 22)

        if mtype == "arrow":
            x1, y1 = m["from"][0] * ss, m["from"][1] * ss
            x2, y2 = m["to"][0] * ss, m["to"][1] * ss
            sx, sy, poly = _arrow_geometry(x1, y1, x2, y2, head_len, head_half)
            draw.line([(x1, y1), (sx, sy)], fill=white_halo, width=halo_stroke)
            # Expand arrowhead slightly for white halo
            cx_p = sum(p[0] for p in poly) / 3.0
            cy_p = sum(p[1] for p in poly) / 3.0
            halo_poly = [
                (cx_p + (px - cx_p) * 1.18, cy_p + (py - cy_p) * 1.18) for px, py in poly
            ]
            draw.polygon(halo_poly, fill=white_halo)
            draw.line([(x1, y1), (sx, sy)], fill=solid, width=sw)
            draw.polygon(poly, fill=solid)

        elif mtype == "box":
            x0, y0, x1, y1 = (v * ss for v in m["rect"])
            rad = max(2, 4 * ss)
            draw.rounded_rectangle([x0, y0, x1, y1], radius=rad, fill=tint, outline=white_halo, width=halo_stroke)
            draw.rounded_rectangle([x0, y0, x1, y1], radius=rad, fill=None, outline=solid, width=sw)

        elif mtype == "circle":
            cx, cy = m["center"][0] * ss, m["center"][1] * ss
            rad = m["r"] * ss
            bbox = [cx - rad, cy - rad, cx + rad, cy + rad]
            draw.ellipse(bbox, fill=tint, outline=white_halo, width=halo_stroke)
            draw.ellipse(bbox, fill=None, outline=solid, width=sw)

        placement = _compute_mark_label_placement(m, width, height, metrics)
        if placement is not None:
            tx, ty, anchor = placement
            label_text = m["text"] if mtype == "label" else m["label"]
            pil_anchor = {"start": "ls", "middle": "ms", "end": "rs"}.get(anchor, "ls")
            line_h = fs * 1.22
            for i, ln in enumerate(label_text.splitlines() or [""]):
                draw.text(
                    (tx * ss, ty * ss + i * line_h),
                    ln,
                    font=font,
                    fill=solid,
                    anchor=pil_anchor,
                    stroke_width=text_halo,
                    stroke_fill=(255, 255, 255, 255),
                )

    if ss > 1:
        overlay = overlay.resize((width, height), Image.Resampling.LANCZOS)
    combined = Image.alpha_composite(base_rgba, overlay).convert("RGB")
    out_png.parent.mkdir(parents=True, exist_ok=True)
    combined.save(str(out_png), format="PNG")
    return out_png


def _build_annotated_html(svg_text: str, title: str | None = None, marks_count: int = 0) -> str:
    """Compact, flush, transparent, theme-adaptive, zero-CDN HTML embed for <agent-embed>."""
    safe_title = html_lib.escape(title or "Annotated Figure")
    badge_text = f"{marks_count} callout{'s' if marks_count != 1 else ''}"
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>{safe_title}</title>
<style>
  html, body {{
    margin: 0;
    padding: 0;
    background: transparent;
    color: var(--foreground, inherit);
    font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    font-size: 13px;
    line-height: 1.4;
  }}
  .wrap {{
    padding: 6px 0 4px 0;
  }}
  .bar {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    padding: 0 2px 6px 2px;
    border-bottom: 1px solid var(--card-border, rgba(128, 128, 128, 0.25));
    margin-bottom: 8px;
  }}
  .title {{
    font-size: 12px;
    font-weight: 600;
    color: var(--foreground, inherit);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }}
  .meta {{
    font-size: 12px;
    color: var(--muted-foreground, gray);
    margin-left: 6px;
    font-weight: 400;
  }}
  .controls {{
    display: flex;
    align-items: center;
    gap: 6px;
    flex-shrink: 0;
  }}
  button {{
    font: inherit;
    font-size: 12px;
    padding: 2px 8px;
    border-radius: 6px;
    border: 1px solid var(--card-border, rgba(128, 128, 128, 0.35));
    background: var(--card, transparent);
    color: var(--foreground, inherit);
    cursor: pointer;
  }}
  button[aria-pressed="true"] {{
    border-color: var(--primary, #2563eb);
    color: var(--primary, #2563eb);
  }}
  .stage {{
    display: flex;
    justify-content: center;
    align-items: center;
    overflow: auto;
    border-radius: 8px;
    border: 1px solid var(--card-border, rgba(128, 128, 128, 0.22));
    background: var(--card, transparent);
    padding: 6px;
  }}
  .stage svg {{
    display: block;
    max-width: 100%;
    height: auto;
    max-height: 390px;
    border-radius: 4px;
    transition: filter 150ms ease;
  }}
  .stage.zoomed svg {{
    max-height: none;
    width: auto;
    max-width: none;
  }}
  .stage.inverted svg image {{
    filter: invert(0.92) hue-rotate(180deg);
  }}
  .stage.hide-marks #mlh-marks {{
    display: none;
  }}
</style>
</head>
<body>
  <div class="wrap">
    <div class="bar">
      <div class="title">{safe_title}<span class="meta">· {badge_text}</span></div>
      <div class="controls">
        <button id="btn-marks" type="button" aria-pressed="true">Marks</button>
        <button id="btn-invert" type="button" aria-pressed="false">Invert</button>
        <button id="btn-zoom" type="button" aria-pressed="false">Zoom</button>
      </div>
    </div>
    <div id="stage" class="stage">
      {svg_text.strip()}
    </div>
  </div>
  <script>
    (function () {{
      var stage = document.getElementById("stage");
      var btnMarks = document.getElementById("btn-marks");
      var btnInvert = document.getElementById("btn-invert");
      var btnZoom = document.getElementById("btn-zoom");
      btnMarks.addEventListener("click", function () {{
        var hidden = stage.classList.toggle("hide-marks");
        btnMarks.setAttribute("aria-pressed", String(!hidden));
      }});
      btnInvert.addEventListener("click", function () {{
        var inv = stage.classList.toggle("inverted");
        btnInvert.setAttribute("aria-pressed", String(inv));
      }});
      btnZoom.addEventListener("click", function () {{
        var zoomed = stage.classList.toggle("zoomed");
        btnZoom.setAttribute("aria-pressed", String(zoomed));
      }});
    }})();
  </script>
</body>
</html>
"""


def _build_occlusion_spec(
    src_path: str,
    width: int,
    height: int,
    marks_px: list[dict[str, Any]],
    *,
    mode: str = "hide_one",
) -> dict[str, Any]:
    """Build declarative SVG occlusion JSON (`masks: [{id, bbox, label, propId}]`) from marks."""
    w_f = float(max(1, width))
    h_f = float(max(1, height))
    metrics = _annotation_metrics(width, height)
    fs = metrics["font_size"]

    masks: list[dict[str, Any]] = []
    for idx, m in enumerate(marks_px, start=1):
        mtype = m["type"]
        if mtype == "box":
            x0, y0, x1, y1 = m["rect"]
        elif mtype == "circle":
            cx, cy = m["center"]
            r = m["r"]
            x0, y0, x1, y1 = cx - r, cy - r, cx + r, cy + r
        elif mtype == "arrow":
            tx, ty = m["to"]
            pad = max(18.0, fs * 1.1)
            x0, y0, x1, y1 = tx - pad, ty - pad, tx + pad, ty + pad
        else:  # label
            ax, ay = m["at"]
            txt = str(m.get("text") or "")
            est_w = max(48.0, len(txt) * fs * 0.62)
            est_h = max(24.0, fs * 1.4)
            anchor = m.get("anchor", "start")
            if anchor == "middle":
                x0 = ax - est_w / 2.0
            elif anchor == "end":
                x0 = ax - est_w
            else:
                x0 = ax
            y0 = ay - fs
            x1, y1 = x0 + est_w, y0 + est_h

        x0_c = max(0.0, min(w_f - 1.0, x0))
        y0_c = max(0.0, min(h_f - 1.0, y0))
        x1_c = max(x0_c + 1.0, min(w_f, x1))
        y1_c = max(y0_c + 1.0, min(h_f, y1))
        bw = x1_c - x0_c
        bh = y1_c - y0_c

        label_text = str(m.get("text") if mtype == "label" else m.get("label") or "").strip()
        masks.append(
            {
                "id": str(m.get("id") or f"m{idx}"),
                "bbox": [
                    round(x0_c / w_f, 4),
                    round(y0_c / h_f, 4),
                    round(bw / w_f, 4),
                    round(bh / h_f, 4),
                ],
                "bboxPx": [round(x0_c, 1), round(y0_c, 1), round(bw, 1), round(bh, 1)],
                "label": label_text,
                "propId": str(m.get("propId") or ""),
            }
        )

    occ_mode = str(mode or "hide_one").strip().lower()
    if occ_mode not in ("hide_one", "hide_all"):
        occ_mode = "hide_one"
    return {
        "src": src_path,
        "width": width,
        "height": height,
        "mode": occ_mode,
        "masks": masks,
    }


def cmd_annotate(args: argparse.Namespace) -> None:
    """Crop a PDF figure (or load an image) and overlay high-contrast white-halo SVG + PNG + HTML marks."""
    from PIL import Image

    if bool(args.image) == bool(args.pdf):
        raise SystemExit("Provide exactly one source: either --image <path> or --pdf <path> --page <N>")

    source_desc: str
    if args.image:
        img_path = Path(args.image).expanduser().resolve()
        if not img_path.exists():
            raise SystemExit(f"Image not found: {img_path}")
        with Image.open(img_path) as im:
            base_img = im.convert("RGB")
        buf = io.BytesIO()
        base_img.save(buf, format="PNG")
        base_png_bytes = buf.getvalue()
        source_desc = str(img_path)
    else:
        if args.page is None:
            raise SystemExit("--page <1-based> is required when using --pdf")
        pdf_path, doc = open_pdf(args.pdf)
        with doc:
            if args.page < 1 or args.page > len(doc):
                raise SystemExit(f"--page {args.page} out of range for '{pdf_path}' (1..{len(doc)})")
            page = doc[args.page - 1]
            crop_norm = _parse_crop_arg(args.crop)
            clip = None
            if crop_norm is not None:
                x0, y0, x1, y1 = crop_norm
                rect = page.rect
                clip = fitz.Rect(
                    rect.x0 + rect.width * x0,
                    rect.y0 + rect.height * y0,
                    rect.x0 + rect.width * x1,
                    rect.y0 + rect.height * y1,
                )
            zoom = max(72, int(args.dpi)) / 72.0
            pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=clip, alpha=False)
            base_png_bytes = pix.tobytes("png")
            base_img = Image.open(io.BytesIO(base_png_bytes)).convert("RGB")
            source_desc = f"{pdf_path}#page={args.page}" + (f"&crop={args.crop}" if args.crop else "")

    width, height = base_img.size
    marks_raw = _load_marks_spec(args.marks, default_coords=args.coords)
    marks_px = _resolve_marks_px(marks_raw, width, height)

    out_path = Path(args.out).expanduser().resolve()
    out_ext = out_path.suffix.lower()
    if out_ext not in (".svg", ".png"):
        raise SystemExit(f"--out must end in .svg or .png (got '{out_path.name}')")

    svg_text = _build_annotated_svg(base_png_bytes, width, height, marks_px, title=args.title)
    want_occlude = bool(getattr(args, "occlude", False))

    png_verify_path: str | None = None
    if out_ext == ".svg":
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(svg_text, encoding="utf-8")
        if args.out_png:
            p_png = Path(args.out_png).expanduser().resolve()
            _rasterize_annotated_png(base_img, marks_px, p_png)
            png_verify_path = str(p_png)
        elif want_occlude:
            p_png = out_path.with_suffix(".png")
            _rasterize_annotated_png(base_img, marks_px, p_png)
            png_verify_path = str(p_png)
    else:
        _rasterize_annotated_png(base_img, marks_px, out_path)
        png_verify_path = str(out_path)
        if args.out_png:
            p_png = Path(args.out_png).expanduser().resolve()
            if p_png != out_path:
                _rasterize_annotated_png(base_img, marks_px, p_png)
            png_verify_path = str(p_png)

    html_embed_path: str | None = None
    if args.out_html:
        p_html = Path(args.out_html).expanduser().resolve()
        p_html.parent.mkdir(parents=True, exist_ok=True)
        p_html.write_text(
            _build_annotated_html(svg_text, title=args.title, marks_count=len(marks_px)),
            encoding="utf-8",
        )
        html_embed_path = str(p_html)

    occlusion_json_path: str | None = None
    occlusion_spec: dict[str, Any] | None = None
    if want_occlude:
        occ_out_arg = getattr(args, "occlude_out", None)
        occ_path = (
            Path(occ_out_arg).expanduser().resolve()
            if occ_out_arg
            else out_path.with_name(f"{out_path.stem}.occlude.json")
        )
        # Save clean unannotated base PNG alongside occlusion JSON so masks cover clean image regions.
        clean_base_path = out_path.with_name(f"{out_path.stem}.base.png")
        clean_base_path.parent.mkdir(parents=True, exist_ok=True)
        base_img.save(str(clean_base_path), format="PNG")
        occ_spec = _build_occlusion_spec(
            str(clean_base_path),
            width,
            height,
            marks_px,
            mode=str(getattr(args, "occlude_mode", "hide_one") or "hide_one"),
        )
        save_json_atomic(occ_path, occ_spec)
        occlusion_json_path = str(occ_path)
        occlusion_spec = occ_spec

    payload: dict[str, Any] = {
        "source": source_desc,
        "width": width,
        "height": height,
        "marksCount": len(marks_px),
        "output": str(out_path),
        "pngVerify": png_verify_path,
        "htmlEmbed": html_embed_path,
    }
    if occlusion_json_path is not None:
        payload["occlusionJson"] = occlusion_json_path
        payload["occlusion"] = occlusion_spec
    print(json.dumps(payload, indent=2, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser(description="Universal PDF Studio (text, scanned, handwritten, tables, diagrams, tree-index)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_ins = sub.add_parser("inspect", help="Classify every page and inspect TOC")
    p_ins.add_argument("pdf")
    p_ins.add_argument("--limit", type=int, default=200)
    p_ins.set_defaults(func=cmd_inspect)

    p_tree = sub.add_parser("tree-index", help="Build a PageIndex-style hierarchical section-page tree (zero API keys)")
    p_tree.add_argument("pdf")
    p_tree.add_argument("--out", help="Output directory (default: sources/_extracted/<slug>)")
    p_tree.add_argument("--chunk-size", type=int, default=12)
    p_tree.set_defaults(func=cmd_tree_index)

    p_ext = sub.add_parser("extract", help="Extract Markdown + auto-OCR scanned pages + render diagram PNGs")
    p_ext.add_argument("pdf")
    p_ext.add_argument("--pages", help="1-based page spec, e.g. 1-12,18")
    p_ext.add_argument("--out", help="Output directory (default: sources/_extracted/<slug>)")
    p_ext.add_argument("--dpi", type=int, default=180)
    p_ext.add_argument("--ocr", choices=["auto", "always", "never"], default="auto")
    p_ext.add_argument("--render-all", action="store_true")
    p_ext.add_argument("--extract-figures", action=argparse.BooleanOptionalAction, default=True)
    p_ext.set_defaults(func=cmd_extract)

    p_tbl = sub.add_parser("tables", help="Extract structured tables (pdfplumber + PyMuPDF fallback)")
    p_tbl.add_argument("pdf")
    p_tbl.add_argument("--pages", help="1-based page spec, e.g. 1-5")
    p_tbl.add_argument("--out", help="Optional Markdown file path to save extracted tables")
    p_tbl.set_defaults(func=cmd_tables)

    p_ren = sub.add_parser("render", help="Render specific PDF pages (or normalized crop) to PNG")
    p_ren.add_argument("pdf")
    p_ren.add_argument("--pages", required=True)
    p_ren.add_argument("--dpi", type=int, default=200)
    p_ren.add_argument("--crop", help="Optional normalized crop: x0,y0,x1,y1 (0..1)")
    p_ren.add_argument("--out")
    p_ren.set_defaults(func=cmd_render)

    p_sea = sub.add_parser("search", help="Find exact pages matching a term across PDF text or OCR cache")
    p_sea.add_argument("pdf")
    p_sea.add_argument("query")
    p_sea.add_argument("--limit", type=int, default=20)
    p_sea.set_defaults(func=cmd_search)

    p_ann = sub.add_parser(
        "annotate",
        help="Crop a PDF figure or load an image and overlay high-contrast white-halo SVG + PNG + inline HTML callouts",
    )
    p_ann.add_argument("--image", help="Path to an existing PNG/JPG figure or handwritten photo")
    p_ann.add_argument("--pdf", help="Path to a source PDF file")
    p_ann.add_argument("--page", type=int, help="1-based page number (required with --pdf)")
    p_ann.add_argument("--crop", help="Optional normalized crop box x0,y0,x1,y1 in 0..1 (re-rendered crisp from PDF)")
    p_ann.add_argument("--dpi", type=int, default=260, help="Render DPI for PDF page/crop (default: 260)")
    p_ann.add_argument(
        "--marks",
        required=True,
        help="JSON array string (or path to JSON file) of arrow/box/circle/label callouts",
    )
    p_ann.add_argument("--coords", choices=["norm", "px"], default="norm", help="Default mark coordinate mode (default: norm)")
    p_ann.add_argument("--out", required=True, help="Output file path (.svg or .png)")
    p_ann.add_argument("--out-png", help="Optional Pillow rasterized verification PNG path for read_file Vision check")
    p_ann.add_argument("--out-html", help="Optional flush, zero-CDN HTML embed path ready for <agent-embed>")
    p_ann.add_argument("--occlude", action="store_true", help="Emit declarative SVG occlusion JSON (masks: [{id, bbox, label, propId}]) alongside white-halo PNG")
    p_ann.add_argument("--occlude-out", help="Optional output path for occlusion JSON (default: <out_stem>.occlude.json)")
    p_ann.add_argument("--occlude-mode", choices=["hide_one", "hide_all"], default="hide_one", help="Image occlusion masking mode (default: hide_one)")
    p_ann.add_argument("--title", help="Optional figure title/caption")
    p_ann.add_argument("--json", action="store_true", help="Emit JSON summary to stdout (default)")
    p_ann.set_defaults(func=cmd_annotate)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
