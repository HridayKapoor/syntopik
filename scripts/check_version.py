#!/usr/bin/env python3
"""Keep the Syntopik release version consistent across every manifest.

`pyproject.toml` is the single source of truth. This checker verifies (or with
`--set`, rewrites) every other file that has to carry the same version, and
can assert that a release tag matches it.

Usage:
    python3 -B scripts/check_version.py                 # verify, exit 1 on drift
    python3 -B scripts/check_version.py --tag v3.2.0    # also verify a git tag
    python3 -B scripts/check_version.py --set 3.3.0     # bump every file at once
    python3 -B scripts/check_version.py --json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")

# (relative path, regex with exactly one capture group around the version)
TARGETS: list[tuple[str, str]] = [
    ("package.json", r'^  "version": "([^"]+)"'),
    ("syntopik/__init__.py", r'^__version__ = "([^"]+)"'),
    ("gemini-extension.json", r'^  "version": "([^"]+)"'),
    (".claude-plugin/plugin.json", r'^  "version": "([^"]+)"'),
    (".claude-plugin/marketplace.json", r'"version": "([^"]+)"'),
    ("mcp-app/mlh-studio/package.json", r'^  "version": "([^"]+)"'),
    ("mcp-app/mlh-studio/plugin.json", r'^  "version": "([^"]+)"'),
    ("mcp-app/mlh-studio/src/server.mjs", r'name: "syntopik-studio", version: "([^"]+)"'),
    ("bin/syntopik.mjs", r'return (?:pkg\.version \|\| )?"([^"]+)";'),
]


def canonical_version() -> str:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return str(data["project"]["version"])


def scan(expected: str) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for rel, pattern in TARGETS:
        path = ROOT / rel
        if not path.is_file():
            rows.append({"file": rel, "found": [], "ok": False, "error": "missing file"})
            continue
        found = re.findall(pattern, path.read_text(encoding="utf-8"), flags=re.M)
        rows.append({
            "file": rel,
            "found": found,
            "ok": bool(found) and all(v == expected for v in found),
            **({} if found else {"error": "version pattern not found"}),
        })
    return rows


def set_version(new: str) -> list[str]:
    old = canonical_version()
    changed: list[str] = []
    pyproject = ROOT / "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8")
    text2 = re.sub(r'^version = "[^"]+"', f'version = "{new}"', text, count=1, flags=re.M)
    if text2 != text:
        pyproject.write_text(text2, encoding="utf-8")
        changed.append("pyproject.toml")
    for rel, pattern in TARGETS:
        path = ROOT / rel
        text = path.read_text(encoding="utf-8")

        def _sub(m: re.Match[str]) -> str:
            whole = m.group(0)
            start = m.start(1) - m.start(0)
            end = m.end(1) - m.start(0)
            return whole[:start] + new + whole[end:]

        text2 = re.sub(pattern, _sub, text, flags=re.M)
        if text2 != text:
            path.write_text(text2, encoding="utf-8")
            changed.append(rel)
    if old != new:
        print(f"Bumped {old} -> {new}. Rebuild mcp-app/mlh-studio/dist (npm run build) and update CHANGELOG.md.", file=sys.stderr)
    return changed


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Verify or bump the Syntopik version across all manifests.")
    ap.add_argument("--tag", help="Release tag (e.g. v3.2.0) that must match pyproject.toml.")
    ap.add_argument("--set", dest="new_version", help="Rewrite every manifest to this version.")
    ap.add_argument("--json", action="store_true", help="Emit a JSON report.")
    args = ap.parse_args(argv)

    if args.new_version:
        if not SEMVER_RE.match(args.new_version):
            ap.error(f"not a semantic version: {args.new_version!r}")
        set_version(args.new_version)

    expected = canonical_version()
    rows = scan(expected)
    tag_ok = True
    if args.tag:
        tag_ok = args.tag.removeprefix("refs/tags/").removeprefix("v") == expected
    ok = tag_ok and all(r["ok"] for r in rows)

    if args.json:
        print(json.dumps({"ok": ok, "version": expected, "tag": args.tag, "tagOk": tag_ok, "files": rows}, indent=2))
    else:
        for r in rows:
            mark = "ok  " if r["ok"] else "FAIL"
            print(f"{mark} {r['file']}: {', '.join(map(str, r['found'])) or r.get('error')}")
        if args.tag:
            print(f"{'ok  ' if tag_ok else 'FAIL'} tag {args.tag} vs pyproject {expected}")
        print(f"Version {expected}: {'consistent' if ok else 'DRIFT DETECTED'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
