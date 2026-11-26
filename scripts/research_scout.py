#!/usr/bin/env python3
"""Bounded Syllabus-Scoped Deep Research & Source Curation CLI for Syntopik.

Implements the portable 4-tier research cascade with ZERO hardcoded user paths:
  - Tier 1: Active `research` MCP tools in agent session (documented in `status`)
  - Tier 2: Portable `researchctl` CLI discovery (`RESEARCHCTL_BIN` -> `PATH` ->
            `~/.local/bin/researchctl` -> `LOCAL_RESEARCH_STACK_DIR` -> sibling/home
            `local-research-stack/scripts/researchctl.py`) invoking
            `researchctl call research <tool> '<json>'` with mandatory integer
            `runtime_seconds` (5..300) + `result_detail:"compact"` and reading full
            JSON from `<stack_root>/reports/research-<tool>-result.json`.
  - Tier 3: Keyless open scholarly & reference HTTPS APIs (`arXiv` Atom,
            `OpenAlex`, `Crossref`, `MediaWiki/Wikipedia`) via stdlib `urllib.request`.
  - Tier 4: Pure offline local fallback (`sources/`, `knowledge-graph/`).

Also manages bounded **Scope Charters & Curated Source Manifests** under
`sources/_curated/<slug>/manifest.json` (`maxSources <= 8`, explicit `inScope` and
`outOfScope` anti-rabbit-hole fences, and primary/secondary source roles).

Pure Python 3.12 standard library only (zero external dependencies).
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import html as html_lib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
USER_AGENT = "Syntopik/1.0 (Local-First Study OS; +https://github.com/syntopik/syntopik)"

HARD_MAX_SOURCES = 8
VALID_SOURCE_ROLES: tuple[str, ...] = (
    "primary_spine",
    "secondary_reference",
    "problem_bank",
    "paper",
    "official_docs",
    "code_repo",
    "overview",
)
GATEWAY_TOOLS: tuple[str, ...] = (
    "web_search",
    "read_url",
    "engine_search",
    "batch_extract",
    "reddit_search",
    "reddit_post",
    "reddit_browse",
    "crawl_page",
    "crawl_site",
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(text or "").strip().lower()).strip("-") or "topic"


def _clamp_runtime_seconds(val: int | float | None, default: int = 60) -> int:
    if val is None:
        return default
    return max(5, min(300, int(round(float(val)))))


def _resolve_workspace(explicit: str | Path | None = None) -> Path:
    if explicit is not None and str(explicit).strip():
        return Path(explicit).expanduser().resolve()
    env_ws = os.environ.get("SYNT_WORKSPACE") or os.environ.get("MLH_WORKSPACE")
    if env_ws and env_ws.strip():
        return Path(env_ws).expanduser().resolve()
    return ROOT


def _resolve_curated_root(workspace: Path, explicit_curated: str | Path | None = None) -> Path:
    if explicit_curated is not None and str(explicit_curated).strip():
        return Path(explicit_curated).expanduser().resolve()
    return (workspace / "sources" / "_curated").resolve()


# ---------------------------------------------------------------------------
# Tier 2: Portable `researchctl` Discovery & Unified Gateway Caller
# ---------------------------------------------------------------------------


def discover_researchctl(workspace: Path | None = None) -> dict[str, Any]:
    """Discover `researchctl` portably across env vars, PATH, `~/.local/bin`, and sibling/home folders."""
    ws = (workspace or ROOT).resolve()
    home = Path.home()
    candidates: list[tuple[str, Path]] = []

    env_bin = os.environ.get("RESEARCHCTL_BIN")
    if env_bin and env_bin.strip():
        candidates.append(("env:RESEARCHCTL_BIN", Path(env_bin).expanduser()))

    which_bin = shutil.which("researchctl")
    if which_bin:
        candidates.append(("PATH", Path(which_bin)))

    candidates.append(("~/.local/bin/researchctl", home / ".local" / "bin" / "researchctl"))

    env_stack = os.environ.get("LOCAL_RESEARCH_STACK_DIR")
    if env_stack and env_stack.strip():
        candidates.append(
            ("env:LOCAL_RESEARCH_STACK_DIR", Path(env_stack).expanduser() / "scripts" / "researchctl.py")
        )

    for label, base_dir in (
        ("sibling:local-research-stack", ws.parent / "local-research-stack"),
        ("home:Documents/projects/local-research-stack", home / "Documents" / "projects" / "local-research-stack"),
        ("home:projects/local-research-stack", home / "projects" / "local-research-stack"),
        ("home:local-research-stack", home / "local-research-stack"),
    ):
        candidates.append((label, base_dir / "scripts" / "researchctl.py"))

    chosen_src: str | None = None
    chosen_bin: Path | None = None
    for src, cand in candidates:
        if cand.is_file() and (os.access(cand, os.X_OK) or cand.suffix == ".py"):
            chosen_src = src
            chosen_bin = cand
            break

    if chosen_bin is None:
        return {
            "available": False,
            "source": None,
            "binary": None,
            "commandPrefix": None,
            "stackRoot": None,
        }

    cmd_prefix = (
        [sys.executable, "-B", str(chosen_bin)]
        if chosen_bin.suffix == ".py" and not os.access(chosen_bin, os.X_OK)
        else [str(chosen_bin)]
    )

    stack_root: Path | None = None
    try:
        proc = subprocess.run([*cmd_prefix, "paths"], capture_output=True, text=True, timeout=3.0, check=False)
        if proc.returncode == 0:
            for line in proc.stdout.splitlines():
                if line.startswith("Installation:"):
                    raw_inst = line.split(":", 1)[1].strip()
                    if raw_inst:
                        stack_root = Path(raw_inst).expanduser().resolve()
                    break
    except Exception:
        stack_root = None

    if stack_root is None and chosen_bin.name == "researchctl.py" and chosen_bin.parent.name == "scripts":
        stack_root = chosen_bin.resolve().parent.parent

    return {
        "available": True,
        "source": chosen_src,
        "binary": str(chosen_bin),
        "commandPrefix": cmd_prefix,
        "stackRoot": str(stack_root) if stack_root else None,
    }


def call_researchctl_tool(
    tool_name: str,
    arguments: dict[str, Any],
    *,
    runtime_seconds: int = 60,
    workspace: Path | None = None,
) -> dict[str, Any]:
    """Invoke `researchctl call research <tool> '<json>'` and parse full report JSON."""
    if tool_name not in GATEWAY_TOOLS:
        raise ValueError(f"Unsupported research gateway tool {tool_name!r}; expected one of {GATEWAY_TOOLS}.")

    info = discover_researchctl(workspace)
    if not info["available"] or not info["commandPrefix"]:
        return {
            "ok": False,
            "tier": 2,
            "available": False,
            "error": "local-research-stack (`researchctl`) is not installed on this machine.",
        }

    rt = _clamp_runtime_seconds(runtime_seconds, default=60)
    payload = dict(arguments)
    payload["runtime_seconds"] = rt
    if tool_name == "web_search":
        payload.setdefault("result_detail", "compact")

    call_started = time.time() - 0.5
    cmd = [*info["commandPrefix"], "call", "research", tool_name, json.dumps(payload)]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=float(rt + 25),
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "tier": 2,
            "tool": tool_name,
            "error": f"researchctl call timed out after {rt}s",
        }

    # Prefer reading full untruncated JSON from `<stack_root>/reports/research-<tool>-result.json`
    report_data: Any = None
    stack_root_str = info.get("stackRoot")
    if stack_root_str:
        report_file = Path(stack_root_str) / "reports" / f"research-{tool_name}-result.json"
        if report_file.is_file() and report_file.stat().st_mtime >= call_started:
            try:
                raw_report = json.loads(report_file.read_text(encoding="utf-8"))
                texts = [
                    c.get("text", "")
                    for c in (raw_report.get("content") or [])
                    if isinstance(c, dict) and c.get("type") == "text"
                ]
                joined = "\n".join(texts).strip()
                if joined:
                    try:
                        report_data = json.loads(joined)
                    except json.JSONDecodeError:
                        report_data = joined
                else:
                    report_data = raw_report
            except Exception:
                report_data = None

    if report_data is None and proc.stdout.strip():
        lines = [ln.strip() for ln in proc.stdout.splitlines() if ln.strip()]
        if lines:
            try:
                last_obj = json.loads(lines[-1])
                preview = last_obj.get("preview")
                if isinstance(preview, str) and preview.strip():
                    try:
                        report_data = json.loads(preview)
                    except json.JSONDecodeError:
                        report_data = preview
                else:
                    report_data = last_obj
            except json.JSONDecodeError:
                report_data = proc.stdout.strip()

    return {
        "ok": proc.returncode == 0,
        "tier": 2,
        "tool": tool_name,
        "runtimeSeconds": rt,
        "exitCode": proc.returncode,
        "data": report_data,
        "stderr": proc.stderr.strip() or None,
    }


# ---------------------------------------------------------------------------
# Tier 3: Keyless Open Scholarly & Reference APIs (Stdlib HTTPS)
# ---------------------------------------------------------------------------


def _http_get(url: str, *, timeout: float = 10.0, accept: str = "application/json") -> bytes:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": accept,
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read(2 * 1024 * 1024)


def _strip_html(raw_html: str) -> str:
    s = re.sub(r"<(script|style|noscript)[^>]*>[\s\S]*?</\1>", " ", raw_html, flags=re.IGNORECASE)
    s = re.sub(r"<(br|p|div|li|h[1-6]|tr)[^>]*>", "\n", s, flags=re.IGNORECASE)
    s = re.sub(r"<[^>]+>", " ", s)
    s = html_lib.unescape(s)
    lines = [" ".join(ln.split()) for ln in s.splitlines()]
    return "\n".join(ln for ln in lines if ln).strip()


def query_arxiv(query: str, limit: int = 4, timeout: float = 10.0) -> list[dict[str, Any]]:
    q = urllib.parse.quote_plus(query)
    url = f"https://export.arxiv.org/api/query?search_query=all:{q}&start=0&max_results={max(1, min(15, limit))}"
    raw = _http_get(url, timeout=timeout, accept="application/atom+xml")
    root = ET.fromstring(raw.decode("utf-8", errors="replace"))
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    items: list[dict[str, Any]] = []
    for entry in root.findall("atom:entry", ns):
        title = " ".join((entry.findtext("atom:title", default="", namespaces=ns) or "").split())
        summary = " ".join((entry.findtext("atom:summary", default="", namespaces=ns) or "").split())
        published = (entry.findtext("atom:published", default="", namespaces=ns) or "")[:4]
        abs_url = (entry.findtext("atom:id", default="", namespaces=ns) or "").strip()
        authors = [
            (a.findtext("atom:name", default="", namespaces=ns) or "").strip()
            for a in entry.findall("atom:author", ns)
            if (a.findtext("atom:name", default="", namespaces=ns) or "").strip()
        ]
        pdf_url = None
        for link in entry.findall("atom:link", ns):
            if link.attrib.get("title") == "pdf" or link.attrib.get("type") == "application/pdf":
                pdf_url = link.attrib.get("href")
                break
        if title:
            items.append(
                {
                    "provider": "arxiv",
                    "title": title,
                    "authors": authors[:5],
                    "year": int(published) if published.isdigit() else None,
                    "url": abs_url,
                    "pdfUrl": pdf_url,
                    "snippet": summary[:420],
                    "suggestedRole": "paper",
                }
            )
    return items


def _reconstruct_openalex_abstract(inv: dict[str, list[int]] | None) -> str:
    if not isinstance(inv, dict) or not inv:
        return ""
    pos_to_word: dict[int, str] = {}
    for word, positions in inv.items():
        if isinstance(positions, list):
            for p in positions:
                if isinstance(p, int) and 0 <= p < 2000:
                    pos_to_word[p] = word
    if not pos_to_word:
        return ""
    ordered = [pos_to_word[k] for k in sorted(pos_to_word.keys())]
    return " ".join(ordered)[:420]


def query_openalex(query: str, limit: int = 4, timeout: float = 10.0) -> list[dict[str, Any]]:
    q = urllib.parse.quote_plus(query)
    url = f"https://api.openalex.org/works?search={q}&per-page={max(1, min(15, limit))}"
    data = json.loads(_http_get(url, timeout=timeout).decode("utf-8", errors="replace"))
    items: list[dict[str, Any]] = []
    for w in data.get("results") or []:
        if not isinstance(w, dict):
            continue
        title = str(w.get("display_name") or w.get("title") or "").strip()
        if not title:
            continue
        authors = []
        for auth in (w.get("authorships") or [])[:5]:
            name = ((auth or {}).get("author") or {}).get("display_name")
            if name:
                authors.append(str(name))
        oa = w.get("open_access") or {}
        loc = w.get("primary_location") or {}
        landing_url = loc.get("landing_page_url") or w.get("doi") or w.get("id") or ""
        pdf_url = oa.get("oa_url") or loc.get("pdf_url")
        wtype = str(w.get("type") or "")
        items.append(
            {
                "provider": "openalex",
                "title": title,
                "authors": authors,
                "year": w.get("publication_year"),
                "citations": w.get("cited_by_count"),
                "url": str(landing_url),
                "pdfUrl": str(pdf_url) if pdf_url else None,
                "snippet": _reconstruct_openalex_abstract(w.get("abstract_inverted_index")),
                "suggestedRole": "secondary_reference" if "book" in wtype else "paper",
            }
        )
    return items


def query_crossref(query: str, limit: int = 4, timeout: float = 10.0) -> list[dict[str, Any]]:
    q = urllib.parse.quote_plus(query)
    url = f"https://api.crossref.org/works?query={q}&rows={max(1, min(15, limit))}"
    data = json.loads(_http_get(url, timeout=timeout).decode("utf-8", errors="replace"))
    raw_items = ((data.get("message") or {}).get("items")) or []
    items: list[dict[str, Any]] = []
    for it in raw_items:
        if not isinstance(it, dict):
            continue
        titles = it.get("title") or []
        title = str(titles[0]).strip() if titles else ""
        if not title:
            continue
        authors = []
        for a in (it.get("author") or [])[:5]:
            if isinstance(a, dict):
                full = f"{a.get('given', '')} {a.get('family', '')}".strip()
                if full:
                    authors.append(full)
        issued = (((it.get("issued") or {}).get("date-parts")) or [[None]])[0]
        year = issued[0] if issued and isinstance(issued[0], int) else None
        ctype = str(it.get("type") or "")
        abstract = _strip_html(str(it.get("abstract") or ""))[:420]
        items.append(
            {
                "provider": "crossref",
                "title": title,
                "authors": authors,
                "year": year,
                "publisher": it.get("publisher"),
                "type": ctype,
                "url": str(it.get("URL") or (f"https://doi.org/{it['DOI']}" if it.get("DOI") else "")),
                "pdfUrl": None,
                "snippet": abstract,
                "suggestedRole": "primary_spine" if "book" in ctype or "monograph" in ctype else "paper",
            }
        )
    return items


def query_wikipedia(query: str, limit: int = 4, timeout: float = 10.0) -> list[dict[str, Any]]:
    q = urllib.parse.quote_plus(query)
    url = (
        f"https://en.wikipedia.org/w/api.php?action=query&list=search&format=json"
        f"&utf8=1&srlimit={max(1, min(15, limit))}&srsearch={q}"
    )
    data = json.loads(_http_get(url, timeout=timeout).decode("utf-8", errors="replace"))
    hits = ((data.get("query") or {}).get("search")) or []
    items: list[dict[str, Any]] = []
    for h in hits:
        if not isinstance(h, dict):
            continue
        title = str(h.get("title") or "").strip()
        if not title:
            continue
        slug = urllib.parse.quote(title.replace(" ", "_"))
        items.append(
            {
                "provider": "wikipedia",
                "title": title,
                "authors": ["Wikipedia Contributors"],
                "year": None,
                "url": f"https://en.wikipedia.org/wiki/{slug}",
                "pdfUrl": None,
                "snippet": _strip_html(str(h.get("snippet") or ""))[:420],
                "suggestedRole": "overview",
            }
        )
    return items


def run_scholar_search(
    query: str,
    *,
    providers: list[str] | None = None,
    limit_per_provider: int = 3,
    timeout: float = 10.0,
) -> dict[str, Any]:
    chosen = providers or ["arxiv", "openalex", "crossref", "wikipedia"]
    dispatch = {
        "arxiv": query_arxiv,
        "openalex": query_openalex,
        "crossref": query_crossref,
        "wikipedia": query_wikipedia,
    }
    results: list[dict[str, Any]] = []
    provider_errors: dict[str, str] = {}
    for p in chosen:
        fn = dispatch.get(p)
        if fn is None:
            provider_errors[p] = f"Unknown scholarly provider '{p}'"
            continue
        try:
            results.extend(fn(query, limit=limit_per_provider, timeout=timeout))
        except Exception as exc:  # noqa: BLE001
            provider_errors[p] = str(exc)

    return {
        "ok": len(results) > 0 or len(provider_errors) == 0,
        "tier": 3 if results else 4,
        "offline": len(results) == 0 and len(provider_errors) == len(chosen),
        "query": query,
        "providersQueried": chosen,
        "count": len(results),
        "results": results,
        "providerErrors": provider_errors,
    }


def read_url_tier3(url: str, *, timeout: float = 12.0, max_chars: int = 12000) -> dict[str, Any]:
    """Clean stdlib URL text extractor with MediaWiki API shortcut for Wikipedia articles."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Only http:// and https:// URLs are allowed (got {url!r}).")

    if "wikipedia.org/wiki/" in url:
        page_slug = parsed.path.split("/wiki/", 1)[1]
        api_url = (
            f"{parsed.scheme}://{parsed.netloc}/w/api.php?action=query&prop=extracts"
            f"&explaintext=1&redirects=1&format=json&titles={page_slug}"
        )
        raw = json.loads(_http_get(api_url, timeout=timeout).decode("utf-8", errors="replace"))
        pages = ((raw.get("query") or {}).get("pages")) or {}
        for page in pages.values():
            if isinstance(page, dict) and page.get("extract"):
                txt = str(page["extract"]).strip()
                return {
                    "ok": True,
                    "tier": 3,
                    "url": url,
                    "title": page.get("title", ""),
                    "characters": len(txt),
                    "truncated": len(txt) > max_chars,
                    "text": txt[:max_chars],
                }

    raw_bytes = _http_get(url, timeout=timeout, accept="text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.5")
    decoded = raw_bytes.decode("utf-8", errors="replace")
    m_title = re.search(r"<title[^>]*>(.*?)</title>", decoded, flags=re.IGNORECASE | re.DOTALL)
    title = _strip_html(m_title.group(1)) if m_title else ""
    text = _strip_html(decoded) if "<html" in decoded.lower() or "<body" in decoded.lower() else decoded.strip()
    return {
        "ok": True,
        "tier": 3,
        "url": url,
        "title": title,
        "characters": len(text),
        "truncated": len(text) > max_chars,
        "text": text[:max_chars],
    }


# ---------------------------------------------------------------------------
# Bounded Scope Charter & Curated Source Manifest (`sources/_curated/<slug>/manifest.json`)
# ---------------------------------------------------------------------------


def _parse_str_list(raw: list[str] | str | None) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        items: list[str] = []
        for entry in raw:
            items.extend(_parse_str_list(entry))
        return items
    s = str(raw).strip()
    if not s:
        return []
    if s.startswith("["):
        try:
            arr = json.loads(s)
            if isinstance(arr, list):
                return [str(x).strip() for x in arr if str(x).strip()]
        except json.JSONDecodeError:
            pass
    return [part.strip() for part in re.split(r"[,;\n]+", s) if part.strip()]


def manifest_path_for(slug: str, workspace: Path, curated_dir: str | Path | None = None) -> Path:
    clean_slug = _slugify(slug)
    base = _resolve_curated_root(workspace, curated_dir)
    return base / clean_slug / "manifest.json"


def load_manifest(slug: str, workspace: Path, curated_dir: str | Path | None = None) -> dict[str, Any] | None:
    mp = manifest_path_for(slug, workspace, curated_dir)
    if not mp.is_file():
        return None
    return json.loads(mp.read_text(encoding="utf-8"))


def save_manifest(
    slug: str,
    data: dict[str, Any],
    workspace: Path,
    curated_dir: str | Path | None = None,
) -> Path:
    max_sources = int(data.get("maxSources", HARD_MAX_SOURCES))
    if max_sources < 1 or max_sources > HARD_MAX_SOURCES:
        raise ValueError(
            f"Anti-rabbit-hole violation: maxSources={max_sources} must be between 1 and {HARD_MAX_SOURCES}."
        )
    sources = data.get("sources") or []
    if len(sources) > max_sources:
        raise ValueError(
            f"Anti-rabbit-hole violation: manifest has {len(sources)} sources, exceeding maxSources={max_sources} (hard cap {HARD_MAX_SOURCES})."
        )
    data["maxSources"] = max_sources
    data["updatedAt"] = _utc_now_iso()

    mp = manifest_path_for(slug, workspace, curated_dir)
    mp.parent.mkdir(parents=True, exist_ok=True)
    mp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return mp


def upsert_manifest(
    slug: str,
    *,
    workspace: Path,
    curated_dir: str | Path | None = None,
    title: str | None = None,
    subject: str | None = None,
    goal: str | None = None,
    target_depth: str | None = None,
    baseline: str | None = None,
    in_scope: list[str] | None = None,
    out_of_scope: list[str] | None = None,
    max_sources: int | None = None,
    approved: bool | None = None,
    add_sources: list[dict[str, Any]] | None = None,
    remove_source_id: str | None = None,
) -> tuple[Path, dict[str, Any]]:
    clean_slug = _slugify(slug)
    existing = load_manifest(clean_slug, workspace, curated_dir)
    now = _utc_now_iso()

    if existing is None:
        doc: dict[str, Any] = {
            "schemaVersion": 1,
            "slug": clean_slug,
            "title": title or clean_slug.replace("-", " ").title(),
            "subject": subject or "General",
            "goal": goal or "",
            "targetDepth": target_depth or "core",
            "baseline": baseline or "",
            "maxSources": HARD_MAX_SOURCES if max_sources is None else int(max_sources),
            "inScope": in_scope or [],
            "outOfScope": out_of_scope or [],
            "approved": bool(approved) if approved is not None else False,
            "createdAt": now,
            "updatedAt": now,
            "sources": [],
        }
    else:
        doc = dict(existing)
        if title is not None:
            doc["title"] = title
        if subject is not None:
            doc["subject"] = subject
        if goal is not None:
            doc["goal"] = goal
        if target_depth is not None:
            doc["targetDepth"] = target_depth
        if baseline is not None:
            doc["baseline"] = baseline
        if max_sources is not None:
            doc["maxSources"] = int(max_sources)
        if in_scope is not None:
            doc["inScope"] = in_scope
        if out_of_scope is not None:
            doc["outOfScope"] = out_of_scope
        if approved is not None:
            doc["approved"] = bool(approved)

    sources_list: list[dict[str, Any]] = list(doc.get("sources") or [])
    if remove_source_id:
        sources_list = [s for s in sources_list if str(s.get("id")) != remove_source_id]

    for raw_src in add_sources or []:
        role = str(raw_src.get("role") or "secondary_reference").strip()
        if role not in VALID_SOURCE_ROLES:
            raise ValueError(f"Invalid source role {role!r}. Expected one of: {', '.join(VALID_SOURCE_ROLES)}")
        src_title = str(raw_src.get("title") or "").strip()
        src_url = str(raw_src.get("url") or raw_src.get("path") or "").strip()
        if not src_title:
            raise ValueError("Each curated source requires a non-empty 'title'.")
        sid = str(raw_src.get("id") or _slugify(src_title)[:36])

        # Demote previous primary_spine to secondary_reference if replacing canonical #1 spine
        if role == "primary_spine":
            for prev in sources_list:
                if prev.get("role") == "primary_spine" and prev.get("id") != sid:
                    prev["role"] = "secondary_reference"

        entry_obj = {
            "id": sid,
            "title": src_title,
            "role": role,
            "url": src_url,
            "authors": raw_src.get("authors") or [],
            "year": raw_src.get("year"),
            "whyInScope": str(raw_src.get("whyInScope") or raw_src.get("note") or "").strip(),
            "approved": bool(raw_src.get("approved", doc.get("approved", False))),
            "addedAt": now,
        }
        existing_idx = next((i for i, s in enumerate(sources_list) if s.get("id") == sid or (src_url and s.get("url") == src_url)), None)
        if existing_idx is not None:
            sources_list[existing_idx] = entry_obj
        else:
            sources_list.append(entry_obj)

    doc["sources"] = sources_list
    path = save_manifest(clean_slug, doc, workspace, curated_dir)
    return path, doc


def _filter_by_scope_fences(items: list[dict[str, Any]], manifest: dict[str, Any] | None) -> tuple[list[dict[str, Any]], int]:
    if not manifest:
        return items, 0
    out_terms = [t.lower() for t in (manifest.get("outOfScope") or []) if isinstance(t, str) and len(t.strip()) >= 3]
    if not out_terms:
        return items, 0
    kept: list[dict[str, Any]] = []
    dropped = 0
    for item in items:
        hay = f"{item.get('title', '')} {item.get('snippet', '')}".lower()
        if any(term in hay for term in out_terms):
            dropped += 1
        else:
            kept.append(item)
    return kept, dropped


# ---------------------------------------------------------------------------
# CLI Subcommands
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--workspace", default=argparse.SUPPRESS, help="Workspace root override")
    common.add_argument("--curated-dir", default=argparse.SUPPRESS, help="Override sources/_curated directory")
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="Emit structured JSON output")

    parser = argparse.ArgumentParser(
        prog="research_scout.py",
        description="Syntopik bounded syllabus-scoped research scout & curated source manifest manager.",
        parents=[common],
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    # 1. status
    sub.add_parser("status", parents=[common], help="Inspect 4-tier research cascade & local-research-stack availability")

    # 2. search
    p_s = sub.add_parser("search", parents=[common], help="Run bounded web/scholarly search via Tier 2 researchctl -> Tier 3 fallback")
    p_s.add_argument("query", help="Focused syllabus-scoped search query")
    p_s.add_argument("--tool", choices=["web_search", "engine_search", "reddit_search"], default="web_search")
    p_s.add_argument("--limit", type=int, default=5, help="Max results (default: 5)")
    p_s.add_argument("--runtime-seconds", type=int, default=60, help="Gateway runtime budget 5..300s (default: 60)")
    p_s.add_argument("--engine", default="google", help="Engine for engine_search (default: google)")
    p_s.add_argument("--subreddit", default=None, help="Optional subreddit filter for reddit_search")
    p_s.add_argument("--scope-slug", default=None, help="Optional manifest slug to apply outOfScope rabbit-hole filtering")
    p_s.add_argument("--scholar-only", action="store_true", help="Skip researchctl and query Tier 3 open scholarly APIs directly")

    # 3. read
    p_r = sub.add_parser("read", parents=[common], help="Extract clean bounded text from a URL (Tier 2 read_url/crawl_page -> Tier 3 HTTPS)")
    p_r.add_argument("url", help="Target http(s) URL to read")
    p_r.add_argument("--tool", choices=["read_url", "crawl_page", "reddit_post"], default="read_url")
    p_r.add_argument("--runtime-seconds", type=int, default=None, help="Gateway runtime budget 5..300s (default: 120 for crawl_page, 60 otherwise)")
    p_r.add_argument("--max-chars", type=int, default=12000, help="Character ceiling to protect agent context (default: 12000)")
    p_r.add_argument("--direct-https", action="store_true", help="Use stdlib Tier 3 reader directly without researchctl")

    # 4. scholar
    p_sc = sub.add_parser("scholar", parents=[common], help="Query keyless scholarly & reference APIs (arXiv, OpenAlex, Crossref, Wikipedia)")
    p_sc.add_argument("query", help="Topic, textbook, or paper query")
    p_sc.add_argument("--providers", "--sources", dest="providers", default="arxiv,openalex,crossref,wikipedia", help="Comma-separated providers (default: all 4)")
    p_sc.add_argument("--limit", type=int, default=3, help="Results per provider (default: 3)")
    p_sc.add_argument("--timeout", type=float, default=10.0, help="Per-provider HTTPS timeout in seconds (default: 10.0)")
    p_sc.add_argument("--scope-slug", default=None, help="Optional manifest slug to filter out-of-scope hits")

    # 5. manifest
    p_m = sub.add_parser("manifest", parents=[common], help="Create, inspect, or update a bounded Scope Charter (`maxSources <= 8`)")
    p_m.add_argument(
        "target",
        nargs="+",
        help="`<slug>` OR `<action> <slug>` where action is init|show|update|add-source|remove-source|approve",
    )
    p_m.add_argument("--title", default=None, help="Human-readable topic/course title")
    p_m.add_argument("--subject", default=None, help="Subject domain (e.g., Computer Science, Machine Learning, Physics)")
    p_m.add_argument("--goal", default=None, help="Learner goal statement")
    p_m.add_argument("--depth", default=None, help="Target depth (foundations | core | mastery)")
    p_m.add_argument("--baseline", default=None, help="Learner current baseline summary")
    p_m.add_argument("--in-scope", action="append", default=None, help="In-scope subtopics (comma-separated or repeatable)")
    p_m.add_argument("--out-of-scope", action="append", default=None, help="Out-of-scope rabbit-hole fences (comma-separated or repeatable)")
    p_m.add_argument("--max-sources", type=int, default=None, help=f"Maximum curated sources (1..{HARD_MAX_SOURCES}, default: {HARD_MAX_SOURCES})")
    p_m.add_argument("--approve", action="store_true", help="Mark Scope Charter & sources approved by learner")
    p_m.add_argument("--source-json", "--sources-json", dest="source_json", action="append", default=None, help="JSON source object or array to add (`{title, role, url, whyInScope}`)")
    p_m.add_argument("--source-title", default=None, help="Source title when adding a single source via flags")
    p_m.add_argument("--source-url", default=None, help="Source URL or local path when adding a single source")
    p_m.add_argument("--source-role", choices=list(VALID_SOURCE_ROLES), default="secondary_reference", help="Source role")
    p_m.add_argument("--why-in-scope", default=None, help="1-line justification tying source to Scope Charter")
    p_m.add_argument("--remove-id", default=None, help="Source ID to remove from manifest")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    as_json = bool(getattr(args, "json", False))
    ws = _resolve_workspace(getattr(args, "workspace", None))
    curated_dir = getattr(args, "curated_dir", None)

    try:
        if args.cmd == "status":
            rctl = discover_researchctl(ws)
            curated_root = _resolve_curated_root(ws, curated_dir)
            manifests = sorted(p.parent.name for p in curated_root.glob("*/manifest.json")) if curated_root.is_dir() else []
            payload = {
                "ok": True,
                "workspace": str(ws),
                "curatedDir": str(curated_root),
                "curatedManifests": manifests,
                "hardMaxSources": HARD_MAX_SOURCES,
                "tiers": {
                    "tier1_mcp": {
                        "tools": list(GATEWAY_TOOLS),
                        "contract": "Always pass integer runtime_seconds (5..300) and result_detail='compact' on web_search.",
                    },
                    "tier2_researchctl": rctl,
                    "tier3_scholar_https": {
                        "available": True,
                        "providers": ["arxiv", "openalex", "crossref", "wikipedia"],
                    },
                    "tier4_offline_local": {
                        "available": True,
                        "sourcesDirExists": (ws / "sources").is_dir(),
                    },
                },
            }
            if as_json:
                print(json.dumps(payload, indent=2))
            else:
                t2 = f"available ({rctl['source']}: {rctl['binary']})" if rctl["available"] else "not installed (using Tier 3 HTTPS scholarly fallback)"
                print(f"Syntopik Research Scout Status\n  Tier 2 researchctl : {t2}\n  Tier 3 Scholar APIs: arxiv, openalex, crossref, wikipedia\n  Curated Manifests  : {len(manifests)} ({', '.join(manifests) or 'none'})")
            return 0

        if args.cmd == "search":
            manifest = load_manifest(args.scope_slug, ws, curated_dir) if args.scope_slug else None
            rt = _clamp_runtime_seconds(args.runtime_seconds, default=60)
            rctl = discover_researchctl(ws)

            if not args.scholar_only and rctl["available"]:
                tool = args.tool
                tool_args: dict[str, Any] = {"query": args.query, "limit": args.limit}
                if tool == "engine_search":
                    tool_args["engine"] = args.engine
                elif tool == "reddit_search" and args.subreddit:
                    tool_args["subreddit"] = args.subreddit

                res = call_researchctl_tool(tool, tool_args, runtime_seconds=rt, workspace=ws)
                # Automatic fallback from challenged/timed-out engine_search to web_search
                if not res["ok"] and tool == "engine_search":
                    res = call_researchctl_tool("web_search", {"query": args.query, "limit": args.limit}, runtime_seconds=rt, workspace=ws)
                if res["ok"]:
                    dropped = 0
                    data_obj = res.get("data")
                    if manifest is not None:
                        if isinstance(data_obj, list):
                            kept, dropped = _filter_by_scope_fences(data_obj, manifest)
                            res["data"] = kept
                        elif isinstance(data_obj, dict):
                            for list_key in ("results", "items", "data"):
                                if isinstance(data_obj.get(list_key), list):
                                    kept, dropped = _filter_by_scope_fences(data_obj[list_key], manifest)
                                    data_obj[list_key] = kept
                                    break
                    res["droppedOutOfScope"] = dropped
                    if as_json:
                        print(json.dumps(res, indent=2, ensure_ascii=False))
                    else:
                        print(f"Research Search '{args.query}' (tier 2 · {res.get('tool')} · droppedOutOfScope={dropped}):")
                        items = (
                            res["data"]
                            if isinstance(res.get("data"), list)
                            else (
                                next(
                                    (res["data"][k] for k in ("results", "items", "data") if isinstance(res["data"].get(k), list)),
                                    [],
                                )
                                if isinstance(res.get("data"), dict)
                                else []
                            )
                        )
                        for it in items[:10]:
                            if isinstance(it, dict):
                                print(f"  - {it.get('title', '(untitled)')} -> {it.get('url', '')}")
                        if not items:
                            print(f"  {str(res.get('data', ''))[:400]}")
                    return 0

            scholar = run_scholar_search(args.query, limit_per_provider=max(1, min(5, args.limit)))
            filtered, dropped = _filter_by_scope_fences(scholar["results"], manifest)
            scholar["results"] = filtered[: max(1, args.limit * 2)]
            scholar["count"] = len(scholar["results"])
            scholar["droppedOutOfScope"] = dropped
            if as_json:
                print(json.dumps(scholar, indent=2, ensure_ascii=False))
            else:
                print(f"Search '{args.query}' (tier {scholar['tier']} · {scholar['count']} result(s) · droppedOutOfScope={dropped}):")
                for item in scholar["results"]:
                    yr = f" ({item['year']})" if item.get("year") else ""
                    print(f"  - [{item['provider']}] {item['title']}{yr} -> {item.get('url')}")
            return 0

        if args.cmd == "read":
            parsed_url = urllib.parse.urlparse(args.url)
            if parsed_url.scheme.lower() not in ("http", "https") or not parsed_url.netloc:
                raise ValueError(f"Only http:// and https:// URLs with a valid host are allowed (got {args.url!r}).")

            default_rt = 120 if args.tool == "crawl_page" else 60
            rt = _clamp_runtime_seconds(args.runtime_seconds, default=default_rt)
            rctl = discover_researchctl(ws)
            if not args.direct_https and rctl["available"]:
                res = call_researchctl_tool(args.tool, {"url": args.url}, runtime_seconds=rt, workspace=ws)
                if res["ok"]:
                    if isinstance(res.get("data"), str) and len(res["data"]) > args.max_chars:
                        res["data"] = res["data"][: args.max_chars]
                        res["truncated"] = True
                    if as_json:
                        print(json.dumps(res, indent=2, ensure_ascii=False))
                    else:
                        print(f"Read {args.url} (tier 2 · {args.tool} · {rt}s):")
                        body = res["data"] if isinstance(res.get("data"), str) else json.dumps(res.get("data"), ensure_ascii=False)
                        print(body[:1200])
                    return 0

            out = read_url_tier3(args.url, max_chars=args.max_chars)
            if as_json:
                print(json.dumps(out, indent=2, ensure_ascii=False))
            else:
                print(f"Read {out['url']} (tier 3 · {out['characters']} chars): {out.get('title', '')}")
                print(out.get("text", "")[:1200])
            return 0

        if args.cmd == "scholar":
            providers = [p.strip().lower() for p in args.providers.split(",") if p.strip()]
            if providers == ["all"]:
                providers = ["arxiv", "openalex", "crossref", "wikipedia"]
            res = run_scholar_search(
                args.query,
                providers=providers,
                limit_per_provider=args.limit,
                timeout=args.timeout,
            )
            if args.scope_slug:
                manifest = load_manifest(args.scope_slug, ws, curated_dir)
                filtered, dropped = _filter_by_scope_fences(res["results"], manifest)
                res["results"] = filtered
                res["count"] = len(filtered)
                res["droppedOutOfScope"] = dropped
            if as_json:
                print(json.dumps(res, indent=2, ensure_ascii=False))
            else:
                print(f"Scholarly Search '{args.query}' — {res['count']} result(s) (tier {res['tier']}):")
                for item in res["results"]:
                    yr = f" ({item['year']})" if item.get("year") else ""
                    print(f"  - [{item['provider']}] {item['title']}{yr} -> {item.get('url')}")
            return 0

        if args.cmd == "manifest":
            known_actions = {"init", "show", "update", "add-source", "remove-source", "approve"}
            tokens = list(args.target)
            if len(tokens) >= 2 and tokens[0] in known_actions:
                action, raw_slug = tokens[0], tokens[1]
            else:
                raw_slug = tokens[0]
                has_mutations = any(
                    v is not None and v is not False
                    for v in (
                        args.title,
                        args.subject,
                        args.goal,
                        args.depth,
                        args.baseline,
                        args.in_scope,
                        args.out_of_scope,
                        args.max_sources,
                        args.approve,
                        args.source_json,
                        args.source_title,
                        args.remove_id,
                    )
                )
                action = "update" if has_mutations else "show"

            if action == "show":
                existing = load_manifest(raw_slug, ws, curated_dir)
                if existing is None:
                    raise FileNotFoundError(f"Manifest not found for slug '{_slugify(raw_slug)}'.")
                print(json.dumps({"ok": True, "path": str(manifest_path_for(raw_slug, ws, curated_dir)), "manifest": existing}, indent=2, ensure_ascii=False))
                return 0

            add_sources: list[dict[str, Any]] = []
            for raw_sj in args.source_json or []:
                parsed_sj = json.loads(raw_sj)
                if isinstance(parsed_sj, list):
                    add_sources.extend(parsed_sj)
                elif isinstance(parsed_sj, dict):
                    add_sources.append(parsed_sj)
            if args.source_title:
                add_sources.append(
                    {
                        "title": args.source_title,
                        "url": args.source_url or "",
                        "role": args.source_role,
                        "whyInScope": args.why_in_scope or "",
                    }
                )

            in_scope_list = _parse_str_list(args.in_scope) if args.in_scope is not None else None
            out_scope_list = _parse_str_list(args.out_of_scope) if args.out_of_scope is not None else None
            approved_val = True if (action == "approve" or args.approve) else None

            path, doc = upsert_manifest(
                raw_slug,
                workspace=ws,
                curated_dir=curated_dir,
                title=args.title,
                subject=args.subject,
                goal=args.goal,
                target_depth=args.depth,
                baseline=args.baseline,
                in_scope=in_scope_list,
                out_of_scope=out_scope_list,
                max_sources=args.max_sources,
                approved=approved_val,
                add_sources=add_sources,
                remove_source_id=args.remove_id,
            )
            out_obj = {
                "ok": True,
                "action": action,
                "path": str(path),
                "sourceCount": len(doc["sources"]),
                "maxSources": doc["maxSources"],
                "manifest": doc,
            }
            if as_json:
                print(json.dumps(out_obj, indent=2, ensure_ascii=False))
            else:
                print(
                    f"Manifest saved: {path}\n"
                    f"  Topic: {doc['title']} ({doc['subject']} · depth={doc['targetDepth']})\n"
                    f"  Sources: {len(doc['sources'])}/{doc['maxSources']} · inScope={len(doc['inScope'])} · outOfScope={len(doc['outOfScope'])}"
                )
            return 0

    except Exception as exc:  # noqa: BLE001
        err = {"ok": False, "command": args.cmd, "error": str(exc)}
        if as_json:
            print(json.dumps(err, indent=2))
        else:
            print(f"Error: {exc}", file=sys.stderr)
        return 1

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
