#!/usr/bin/env python3
"""
Clean Workspace Backup, Integrity Verification & Safe Restore CLI for Syntopik.

Archives irreplaceable personal study data from Syntopik while
strictly excluding caches, virtualenvs, node_modules, extracted PDFs, and
Manim media caches. Embeds a SHA-256 `backup-manifest.json` inside every
archive and enforces strict zip-slip / symlink / checksum verification on
both `verify` and `restore`.

Pure Python 3.12 stdlib only (zero external dependencies).
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import posixpath
import re
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
MANIFEST_NAME = "backup-manifest.json"

# Irreplaceable directories archived recursively (subject to strict exclusions)
INCLUDED_DIRS: tuple[str, ...] = (
    "knowledge-graph",
    "chapters",
    "demos",
    "exams",
    ".agents",
    ".claude",
    ".codex",
)

# Selective code-only directories: only matching suffixes are archived
SELECTIVE_DIRS: dict[str, tuple[str, ...]] = {
    "animations": (".py",),
}

# Top-level Markdown & multi-harness config docs archived when present
INCLUDED_ROOT_FILES: tuple[str, ...] = (
    "SELF_IMPROVEMENT_BLUEPRINT.md",
    "GEMINI.md",
    "AGENTS.md",
    "CLAUDE.md",
    "BOOTSTRAP.md",
    "CONTRIBUTING.md",
    "README.md",
    ".mcp.json",
)

# Strict exclusion rules (Section 1 / P3.2)
EXCLUDED_DIR_NAMES: frozenset[str] = frozenset(
    {
        ".venv",
        "node_modules",
        ".git",
        "__pycache__",
        "_extracted",
        "partial_movie_files",
    }
)

EXCLUDED_REL_PREFIXES: tuple[str, ...] = (
    "animations/media/",
    "sources/_extracted/",
)

EXCLUDED_FILE_NAMES: frozenset[str] = frozenset(
    {
        ".DS_Store",
        MANIFEST_NAME,
    }
)

EXCLUDED_SUFFIXES: tuple[str, ...] = (
    ".pyc",
    ".pyo",
)

EXCLUDED_PATTERNS_DOC: list[str] = [
    ".venv/",
    "sources/_extracted/",
    "mcp-app/*/node_modules/",
    "animations/media/",
    ".git/",
    ".DS_Store",
    "__pycache__/",
    "*.pyc",
]

DRIVE_PREFIX_RE = re.compile(r"^[A-Za-z]:")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def is_safe_archive_path(raw_name: str) -> tuple[bool, str]:
    """Return (is_safe, normalized_posix_path_or_reason).

    Strictly rejects absolute paths, Windows drive prefixes, backslashes,
    null bytes, empty names, and any `..` traversal component (zip-slip).
    """
    if not isinstance(raw_name, str) or not raw_name or not raw_name.strip():
        return False, "empty archive member path"
    if "\x00" in raw_name:
        return False, f"null byte in path: {raw_name!r}"
    if "\\" in raw_name:
        return False, f"backslash disallowed in path: {raw_name!r}"
    if raw_name.startswith("/"):
        return False, f"absolute path disallowed: {raw_name!r}"
    if DRIVE_PREFIX_RE.match(raw_name):
        return False, f"drive-letter path disallowed: {raw_name!r}"

    raw_parts = raw_name.split("/")
    for part in raw_parts:
        if part == "..":
            return False, f"parent directory traversal disallowed: {raw_name!r}"

    # Normalize leading `./` segments if present while checking post-norm safety
    cleaned_parts = [p for p in raw_parts if p not in ("", ".")]
    if not cleaned_parts:
        return False, f"degenerate path: {raw_name!r}"

    norm = posixpath.normpath("/".join(cleaned_parts))
    if norm in (".", "..") or norm.startswith("/") or norm.startswith("../") or "/../" in norm:
        return False, f"unsafe normalized path: {raw_name!r} -> {norm!r}"

    return True, norm


def _is_excluded_rel(rel_posix: str) -> bool:
    parts = rel_posix.split("/")
    if not parts:
        return True
    filename = parts[-1]
    if filename in EXCLUDED_FILE_NAMES:
        return True
    for suf in EXCLUDED_SUFFIXES:
        if filename.endswith(suf):
            return True
    for part in parts[:-1]:
        if part in EXCLUDED_DIR_NAMES:
            return True
    for prefix in EXCLUDED_REL_PREFIXES:
        if rel_posix.startswith(prefix) or f"/{prefix}" in rel_posix:
            return True
    return False


def collect_backup_files(workspace: Path, exclude_archive: Path | None = None) -> list[tuple[str, Path]]:
    """Collect sorted (rel_posix, abs_path) pairs of irreplaceable study files."""
    ws = workspace.resolve()
    exclude_resolved = exclude_archive.resolve() if exclude_archive is not None else None
    found: dict[str, Path] = {}

    def maybe_add(abs_file: Path) -> None:
        if abs_file.is_symlink() or not abs_file.is_file():
            return
        resolved = abs_file.resolve()
        if exclude_resolved is not None and resolved == exclude_resolved:
            return
        try:
            rel_posix = abs_file.relative_to(ws).as_posix()
        except ValueError:
            return
        if _is_excluded_rel(rel_posix):
            return
        ok, norm = is_safe_archive_path(rel_posix)
        if not ok:
            return
        found[norm] = abs_file

    # 1. Top-level workspace docs
    for name in INCLUDED_ROOT_FILES:
        candidate = ws / name
        if candidate.exists():
            maybe_add(candidate)

    # 2. Full personal study directories
    for dirname in INCLUDED_DIRS:
        base = ws / dirname
        if not base.exists() or base.is_symlink() or not base.is_dir():
            continue
        for cur_root, dirnames, filenames in os.walk(base, followlinks=False):
            cur_path = Path(cur_root)
            # Prune excluded directories in-place
            pruned: list[str] = []
            for d in sorted(dirnames):
                child = cur_path / d
                if child.is_symlink():
                    continue
                if d in EXCLUDED_DIR_NAMES:
                    continue
                try:
                    rel_dir = child.relative_to(ws).as_posix() + "/"
                except ValueError:
                    continue
                if any(rel_dir.startswith(p) for p in EXCLUDED_REL_PREFIXES):
                    continue
                pruned.append(d)
            dirnames[:] = pruned

            for fname in sorted(filenames):
                maybe_add(cur_path / fname)

    # 3. Selective directories (e.g., animations/*.py excluding animations/media/)
    for dirname, suffixes in SELECTIVE_DIRS.items():
        base = ws / dirname
        if not base.exists() or base.is_symlink() or not base.is_dir():
            continue
        for cur_root, dirnames, filenames in os.walk(base, followlinks=False):
            cur_path = Path(cur_root)
            pruned = []
            for d in sorted(dirnames):
                child = cur_path / d
                if child.is_symlink() or d in EXCLUDED_DIR_NAMES:
                    continue
                try:
                    rel_dir = child.relative_to(ws).as_posix() + "/"
                except ValueError:
                    continue
                if any(rel_dir.startswith(p) for p in EXCLUDED_REL_PREFIXES):
                    continue
                pruned.append(d)
            dirnames[:] = pruned

            for fname in sorted(filenames):
                if any(fname.endswith(s) for s in suffixes):
                    maybe_add(cur_path / fname)

    return sorted(found.items(), key=lambda pair: pair[0])


def default_backup_path(workspace: Path) -> Path:
    ws = workspace.resolve()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return (ws.parent / f"syntopik-backup-{stamp}.tar.gz").resolve()


def create_snapshot(workspace: Path, out_path: Path | None = None) -> dict[str, Any]:
    ws = workspace.resolve()
    if not ws.exists() or not ws.is_dir():
        raise FileNotFoundError(f"Workspace directory not found: {ws}")

    archive_path = out_path.resolve() if out_path is not None else default_backup_path(ws)
    archive_path.parent.mkdir(parents=True, exist_ok=True)

    items = collect_backup_files(ws, exclude_archive=archive_path)
    manifest_files: list[dict[str, Any]] = []
    total_bytes = 0

    created_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    with tarfile.open(archive_path, "w:gz") as tar:
        for rel_posix, abs_file in items:
            data = abs_file.read_bytes()
            size = len(data)
            digest = sha256_bytes(data)
            total_bytes += size
            manifest_files.append(
                {
                    "path": rel_posix,
                    "sha256": digest,
                    "size": size,
                }
            )
            st = abs_file.stat()
            info = tarfile.TarInfo(name=rel_posix)
            info.size = size
            info.mtime = int(st.st_mtime)
            info.mode = st.st_mode & 0o777
            tar.addfile(info, io.BytesIO(data))

        manifest: dict[str, Any] = {
            "schemaVersion": 1,
            "createdAt": created_at,
            "workspaceName": ws.name,
            "fileCount": len(manifest_files),
            "totalBytes": total_bytes,
            "excludedPatterns": EXCLUDED_PATTERNS_DOC,
            "files": manifest_files,
        }
        manifest_bytes = (json.dumps(manifest, indent=2) + "\n").encode("utf-8")
        m_info = tarfile.TarInfo(name=MANIFEST_NAME)
        m_info.size = len(manifest_bytes)
        m_info.mtime = int(datetime.now(timezone.utc).timestamp())
        m_info.mode = 0o644
        tar.addfile(m_info, io.BytesIO(manifest_bytes))

    archive_bytes = archive_path.stat().st_size
    return {
        "ok": True,
        "command": "snapshot",
        "workspace": str(ws),
        "archive": str(archive_path),
        "createdAt": created_at,
        "fileCount": len(manifest_files),
        "totalBytes": total_bytes,
        "archiveBytes": archive_bytes,
        "manifestPath": MANIFEST_NAME,
    }


def verify_archive(archive_path: Path) -> dict[str, Any]:
    arch = archive_path.resolve()
    errors: list[str] = []
    unsafe_paths: list[str] = []
    mismatches: list[dict[str, Any]] = []
    missing: list[str] = []
    extra: list[str] = []

    if not arch.exists() or not arch.is_file():
        return {
            "ok": False,
            "command": "verify",
            "archive": str(arch),
            "fileCount": 0,
            "verifiedFiles": 0,
            "unsafePaths": [],
            "mismatches": [],
            "missing": [],
            "extra": [],
            "errors": [f"Archive file not found: {arch}"],
        }

    file_members: dict[str, tarfile.TarInfo] = {}
    manifest_raw: bytes | None = None

    try:
        with tarfile.open(arch, "r:gz") as tar:
            members = tar.getmembers()
            seen_names: set[str] = set()

            for member in members:
                ok_path, norm_or_reason = is_safe_archive_path(member.name)
                if not ok_path:
                    unsafe_paths.append(member.name)
                    errors.append(f"Unsafe archive path ({norm_or_reason})")
                    continue

                if member.issym() or member.islnk():
                    unsafe_paths.append(member.name)
                    errors.append(f"Symlink/hardlink disallowed in backup archive: {member.name!r}")
                    continue

                if member.isdev() or member.isfifo() or member.ischr() or member.isblk():
                    unsafe_paths.append(member.name)
                    errors.append(f"Special device/FIFO entry disallowed: {member.name!r}")
                    continue

                if member.isdir():
                    continue

                if not member.isfile():
                    unsafe_paths.append(member.name)
                    errors.append(f"Unsupported non-regular tar entry: {member.name!r}")
                    continue

                if norm_or_reason in seen_names:
                    errors.append(f"Duplicate archive entry: {norm_or_reason!r}")
                    continue
                seen_names.add(norm_or_reason)

                if norm_or_reason == MANIFEST_NAME:
                    fh = tar.extractfile(member)
                    manifest_raw = fh.read() if fh is not None else b""
                else:
                    file_members[norm_or_reason] = member

            manifest: dict[str, Any] | None = None
            if manifest_raw is None:
                errors.append(f"Missing {MANIFEST_NAME} in archive")
            else:
                try:
                    parsed = json.loads(manifest_raw.decode("utf-8"))
                    if not isinstance(parsed, dict) or not isinstance(parsed.get("files"), list):
                        errors.append(f"Malformed {MANIFEST_NAME}: expected JSON object with 'files' list")
                    else:
                        manifest = parsed
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    errors.append(f"Invalid JSON in {MANIFEST_NAME}: {exc}")

            verified_files = 0
            expected_paths: set[str] = set()

            if manifest is not None:
                for entry in manifest["files"]:
                    if not isinstance(entry, dict):
                        errors.append("Manifest contains non-object file entry")
                        continue
                    rel = entry.get("path")
                    exp_sha = entry.get("sha256")
                    exp_size = entry.get("size")
                    ok_rel, norm_rel = is_safe_archive_path(str(rel or ""))
                    if not ok_rel:
                        unsafe_paths.append(str(rel or ""))
                        errors.append(f"Unsafe manifest path ({norm_rel})")
                        continue
                    expected_paths.add(norm_rel)

                    member = file_members.get(norm_rel)
                    if member is None:
                        missing.append(norm_rel)
                        errors.append(f"Missing file listed in manifest: {norm_rel}")
                        continue

                    fh = tar.extractfile(member)
                    data = fh.read() if fh is not None else b""
                    actual_sha = sha256_bytes(data)
                    actual_size = len(data)
                    if actual_sha != exp_sha or (isinstance(exp_size, int) and actual_size != exp_size):
                        mismatches.append(
                            {
                                "path": norm_rel,
                                "expectedSha256": exp_sha,
                                "actualSha256": actual_sha,
                                "expectedSize": exp_size,
                                "actualSize": actual_size,
                            }
                        )
                        errors.append(f"Checksum/size mismatch for {norm_rel}")
                    else:
                        verified_files += 1

                for actual_path in sorted(file_members.keys()):
                    if actual_path not in expected_paths:
                        extra.append(actual_path)
                        errors.append(f"Unexpected file not listed in manifest: {actual_path}")

            file_count = len(manifest["files"]) if manifest is not None else len(file_members)
            return {
                "ok": len(errors) == 0,
                "command": "verify",
                "archive": str(arch),
                "createdAt": manifest.get("createdAt") if manifest else None,
                "workspaceName": manifest.get("workspaceName") if manifest else None,
                "fileCount": file_count,
                "verifiedFiles": verified_files,
                "unsafePaths": unsafe_paths,
                "mismatches": mismatches,
                "missing": missing,
                "extra": extra,
                "errors": errors,
            }
    except (tarfile.TarError, OSError) as exc:
        return {
            "ok": False,
            "command": "verify",
            "archive": str(arch),
            "fileCount": 0,
            "verifiedFiles": 0,
            "unsafePaths": unsafe_paths,
            "mismatches": mismatches,
            "missing": missing,
            "extra": extra,
            "errors": [f"Failed to read archive: {exc}"],
        }


def restore_archive(archive_path: Path, target_dir: Path, force: bool = False) -> dict[str, Any]:
    arch = archive_path.resolve()
    target = target_dir.resolve()

    # Gate 1: Full pre-flight verification (zip-slip, symlinks, manifest, SHA-256)
    verification = verify_archive(arch)
    if not verification["ok"]:
        return {
            "ok": False,
            "command": "restore",
            "archive": str(arch),
            "target": str(target),
            "restoredFiles": 0,
            "verifiedRestored": 0,
            "totalBytes": 0,
            "errors": verification["errors"],
            "unsafePaths": verification["unsafePaths"],
        }

    with tarfile.open(arch, "r:gz") as tar:
        manifest_member = next(
            (m for m in tar.getmembers() if is_safe_archive_path(m.name) == (True, MANIFEST_NAME)),
            None,
        )
        assert manifest_member is not None
        manifest_fh = tar.extractfile(manifest_member)
        assert manifest_fh is not None
        manifest = json.loads(manifest_fh.read().decode("utf-8"))
        manifest_map: dict[str, dict[str, Any]] = {
            is_safe_archive_path(entry["path"])[1]: entry for entry in manifest["files"]
        }

        # Gate 2: Check for existing target collisions unless --force is passed
        if not force and target.exists():
            collisions: list[str] = []
            for rel_posix in sorted(manifest_map.keys()):
                dest = (target / rel_posix).resolve()
                if not dest.is_relative_to(target):
                    return {
                        "ok": False,
                        "command": "restore",
                        "archive": str(arch),
                        "target": str(target),
                        "restoredFiles": 0,
                        "verifiedRestored": 0,
                        "totalBytes": 0,
                        "errors": [f"Path escapes target directory: {rel_posix}"],
                    }
                if dest.exists():
                    collisions.append(rel_posix)
            if collisions:
                sample = ", ".join(collisions[:5])
                suffix = f" (+{len(collisions) - 5} more)" if len(collisions) > 5 else ""
                return {
                    "ok": False,
                    "command": "restore",
                    "archive": str(arch),
                    "target": str(target),
                    "restoredFiles": 0,
                    "verifiedRestored": 0,
                    "totalBytes": 0,
                    "collisions": collisions,
                    "errors": [
                        f"Refusing to overwrite {len(collisions)} existing file(s) without --force: {sample}{suffix}"
                    ],
                }

        target.mkdir(parents=True, exist_ok=True)
        member_by_norm: dict[str, tarfile.TarInfo] = {}
        for m in tar.getmembers():
            ok_p, norm = is_safe_archive_path(m.name)
            if ok_p and m.isfile() and norm != MANIFEST_NAME:
                member_by_norm[norm] = m

        restored_files = 0
        total_bytes = 0
        for rel_posix in sorted(manifest_map.keys()):
            dest = (target / rel_posix).resolve()
            if not dest.is_relative_to(target):
                return {
                    "ok": False,
                    "command": "restore",
                    "archive": str(arch),
                    "target": str(target),
                    "restoredFiles": restored_files,
                    "verifiedRestored": 0,
                    "totalBytes": total_bytes,
                    "errors": [f"Path escapes target directory: {rel_posix}"],
                }
            member = member_by_norm[rel_posix]
            fh = tar.extractfile(member)
            data = fh.read() if fh is not None else b""
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            if member.mode:
                try:
                    os.chmod(dest, member.mode & 0o777)
                except OSError:
                    pass
            restored_files += 1
            total_bytes += len(data)

    # Gate 3: Re-hash every restored file from disk against manifest
    verified_restored = 0
    post_errors: list[str] = []
    for rel_posix, entry in sorted(manifest_map.items()):
        dest = (target / rel_posix).resolve()
        if not dest.exists():
            post_errors.append(f"Restored file missing after extract: {rel_posix}")
            continue
        actual_sha = sha256_file(dest)
        if actual_sha != entry["sha256"]:
            post_errors.append(f"Post-restore SHA-256 mismatch for {rel_posix}")
        else:
            verified_restored += 1

    return {
        "ok": len(post_errors) == 0,
        "command": "restore",
        "archive": str(arch),
        "target": str(target),
        "restoredFiles": restored_files,
        "verifiedRestored": verified_restored,
        "totalBytes": total_bytes,
        "errors": post_errors,
    }


def _build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--json",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Emit machine-readable JSON output.",
    )

    parser = argparse.ArgumentParser(
        prog="backup_harness.py",
        description="Clean workspace backup, SHA-256 verification, and zip-slip-safe restore for Syntopik.",
        parents=[common],
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_snap = sub.add_parser(
        "snapshot",
        parents=[common],
        help="Archive irreplaceable study data into a verified .tar.gz with SHA-256 backup-manifest.json.",
    )
    p_snap.add_argument(
        "--workspace",
        type=Path,
        default=ROOT,
        help=f"Workspace root to back up (default: {ROOT}).",
    )
    p_snap.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Output .tar.gz path (default: <workspace>/../syntopik-backup-YYYYMMDD-HHMMSS.tar.gz).",
    )

    p_ver = sub.add_parser(
        "verify",
        parents=[common],
        help="Verify archive integrity, path safety (no zip-slip/symlinks), and SHA-256 manifest checksums.",
    )
    p_ver.add_argument("archive", type=Path, help="Path to backup .tar.gz archive.")

    p_res = sub.add_parser(
        "restore",
        parents=[common],
        help="Safely extract a verified backup archive into --target and re-verify restored SHA-256 checksums.",
    )
    p_res.add_argument("archive", type=Path, help="Path to backup .tar.gz archive.")
    p_res.add_argument(
        "--target",
        type=Path,
        required=True,
        help="Destination directory for restored files.",
    )
    p_res.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing files in --target.",
    )

    return parser


def _print_result(result: dict[str, Any], as_json: bool) -> None:
    if as_json:
        print(json.dumps(result, indent=2))
        return

    cmd = result.get("command", "")
    ok = bool(result.get("ok"))
    if cmd == "snapshot" and ok:
        print(
            f"Snapshot created: {result['archive']}\n"
            f"  Files archived: {result['fileCount']} ({result['totalBytes']:,} B raw → {result['archiveBytes']:,} B tar.gz)\n"
            f"  Manifest:       {result['manifestPath']} (SHA-256 verified)"
        )
    elif cmd == "verify":
        if ok:
            print(
                f"Backup verified OK: {result['archive']}\n"
                f"  Verified files: {result['verifiedFiles']}/{result['fileCount']} · 0 unsafe paths · 0 mismatches"
            )
        else:
            print(f"Backup verification FAILED: {result['archive']}", file=sys.stderr)
            for err in result.get("errors", []):
                print(f"  - {err}", file=sys.stderr)
    elif cmd == "restore":
        if ok:
            print(
                f"Backup restored OK → {result['target']}\n"
                f"  Restored & SHA-256 verified: {result['verifiedRestored']}/{result['restoredFiles']} files ({result['totalBytes']:,} B)"
            )
        else:
            print(f"Backup restore FAILED: {result['archive']}", file=sys.stderr)
            for err in result.get("errors", []):
                print(f"  - {err}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    as_json = bool(getattr(args, "json", False))

    try:
        if args.command == "snapshot":
            res = create_snapshot(args.workspace, args.out)
        elif args.command == "verify":
            res = verify_archive(args.archive)
        elif args.command == "restore":
            res = restore_archive(args.archive, args.target, force=args.force)
        else:
            parser.error(f"Unknown command: {args.command}")
            return 2
    except Exception as exc:  # noqa: BLE001
        res = {
            "ok": False,
            "command": args.command,
            "errors": [str(exc)],
        }
        _print_result(res, as_json)
        return 1

    _print_result(res, as_json)
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
