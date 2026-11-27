#!/usr/bin/env node
/**
 * Syntopik v3.2 Unified CLI (Zero-Dependency Node ESM Wrapper)
 *
 * Natively handles:
 *   init, attach (14 coding agents, stdio + Streamable HTTP), sync, mcp, hook, --help, --version
 * Cleanly delegates to Python (.venv -> python3 -> python):
 *   serve, import, review, triage, pack, doctor (--compat), export, connect, run,
 *   next, profile, drop, demo, debrief, mode, sunset, pace, board, bench,
 *   rosetta, sync-delta, calibrate-policy, proof-diff, readiness, interleave
 */

import { spawnSync } from "node:child_process";
import {
	cpSync,
	existsSync,
	lstatSync,
	mkdirSync,
	readFileSync,
	statSync,
	symlinkSync,
	writeFileSync,
} from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const PACKAGE_ROOT = path.resolve(__dirname, "..");

const VERSION = (() => {
	try {
		const pkg = JSON.parse(readFileSync(path.join(PACKAGE_ROOT, "package.json"), "utf8"));
		return pkg.version || "3.2.0";
	} catch {
		return "3.2.0";
	}
})();

const LEARNER_DIRS = [
	"chapters",
	"demos",
	"exams",
	"animations",
	"sources",
	"knowledge-graph",
];

const LEDGER_FILES = [
	"propositions.jsonl",
	"chains.jsonl",
	"mistakes.jsonl",
	"exam-attempts.jsonl",
	"harness-log.jsonl",
	"revlog.jsonl",
	"reading-queue.jsonl",
	"derivations.jsonl",
	"evidence-ledger.jsonl",
	"viva-sessions.jsonl",
];

const STARTER_REVIEW_QUEUE = `# Spaced Review & Weak-Edge Queue

> Automatically maintained via FSRS v6 (\`scripts/srs_engine.py\`).
> Ask Syntopik: *"Check my review-queue.md and run a 10-minute interleaved tune-up."*

## Due / Weak Edges (\`misconception\`, \`shaky\`, or Due Today)
_Nothing due today — start any topic to build your knowledge graph._

## Upcoming Spaced Recall (\`solid\`)
_None scheduled yet._

## Active Misconception Watchlist
_None active._
`;

const ALL_HARNESSES = [
	"antigravity",
	"claude",
	"codex",
	"cursor",
	"windsurf",
	"copilot",
	"gemini",
	"opencode",
	"cline",
	"roo",
	"zed",
	"aider",
	"continue",
	"kiro",
];

const DEFAULT_FALLBACK_HARNESSES = ["claude", "codex", "cursor"];
const DEFAULT_HTTP_MCP_URL = "http://127.0.0.1:4173/mcp";

const ZED_PRECEDENCE_RULE_FILES = [
	".rules",
	".cursorrules",
	".windsurfrules",
	".clinerules",
	".github/copilot-instructions.md",
	"AGENT.md",
	"AGENTS.md",
	"CLAUDE.md",
	"GEMINI.md",
];

const HELP_TEXT = `Syntopik v${VERSION} — Universal AI Deep-Mastery OS

Usage:
  syntopik <command> [options]

Native Node Commands (zero Python required):
  init [dir] [--vault global|local] [--json]
      Initialize a Syntopik study workspace or ~/.syntopik vault (non-destructive).
  attach <dir> [--harness auto|all|antigravity|claude|codex|cursor|windsurf|copilot|gemini|opencode|cline|roo|zed|aider|continue|kiro]
               [--vault global|local] [--transport stdio|http] [--http-url http://127.0.0.1:4173/mcp] [--copy-skills] [--json]
      Wire Syntopik non-destructively into any repository across 14 coding agents (zero git diff, preserves AGENTS.md/CLAUDE.md/.clinerules).
  sync [--workspace <dir>] [--json]
      Rebuild knowledge-graph/graph-data.js (schemaVersion 4).
  mcp [--workspace <dir>]
      Run the SEP-1865 Syntopik Studio MCP App over stdio.
  hook <session-start|post-graph-sync> [--workspace <dir>]
      Run a Syntopik lifecycle hook portably (used by _npx-safe & Windows-safe configs).

Full Studio & Engine Commands (Python 3.12+ stdlib):
  next [--energy low|normal|deep] [--domain <name>] [--json]
      Plan the single optimal next pedagogical move (Brainstem).
  profile [status|calibrate|policy|override|reset] [--json]
      Inspect, calibrate, override, or reset the Adaptive Learner Profile.
  drop <inputs...> [--slug <slug>] [--title <title>] [--json]
      Drop mixed sources (PDF, Anki .apkg, CSV/TSV, Jupyter .ipynb, Notion/MD, URL, YouTube, repo) with Teacher-First spine.
  demo [--pack raft-consensus|shm-physics|transformers-attention|all] [--open] [--port 4173] [--json]
      Load bundled zero-PII starter packs into a clean demo workspace.
  serve [--port 4173] [--host 127.0.0.1] [--lan] [--open] [--workspace <dir>]
      Launch the 2-way local Studio web server + loopback Streamable-HTTP MCP (127.0.0.1, optional --lan Pocket QR).
  import <target> [--kind auto|youtube|arxiv|repo|url|notes|anki|cards|notebook|notes_batch] [--slug|--chapter <slug>] [--register-tracker]
      Ingest Anki .apkg, CSV/TSV cards, Jupyter .ipynb, Notion/MD folders, PDFs, YouTube captions, arXiv papers, GitHub repos, or URLs.
  review [--web] [--tui] [--recover --daily-cap 15] [--resume] [--energy low|normal|deep] [--limit 25]
      Run FIRe-compressed FSRS-6 review, TUI loop, calm recovery, re-entry, or Studio Flashcards.
  triage [--hours 48 | --exam-in 48h] [--sleep-hours 7] [--chapter <slug>] [--crib-sheet]
      Generate a Pareto 48h exam triage plan and optional 1-page Anti-Mastery crib sheet.
  readiness [--exam-date YYYY-MM-DD] [--subject <name>] [--max-marks <n>] [--premortem-only] [--json]
      Compute Exam-Day Readiness forecast, <=72h Pre-Exam Taper Mode, and evidence-backed Pre-Mortem Shield.
  report [--days 7] [--md <path>] [--json]
      Weekly progress report: reviews, streak, true retention, mastery, due load, most-lapsed concepts and focus.
  interleave [build|check|complete|stats|export-cbt] [--n 6] [--subjects ...] [--mode principle_first] [--seed N] [--json]
      Run Rohrer blind-chapter interleaved drills with Newman Tier-1 governing-principle discrimination.
  debrief [--ref HEAD~1..HEAD] [--dry-run] [--json]
      Crystallize git diff bugs/invariants into Forks, Arenas, and KG propositions.
  mode [mentor|study] [--off] [--json]
      Toggle Anti-Vibe-Coding Work Mentor mode vs Study mode.
  sunset <slug> [--apply] [--json]
      Archive a finished chapter pack, keep core axioms at R=0.80, and retire leaves.
  pace [--rebalance] [--ics <path>] [--json]
      Run the multi-course buffer-protected Auto-Pacer with optional .ics export.
  board [status|push|pin|clear] [--json]
      Inspect or push frames/pins to the live AI-to-Whiteboard SSE bus.
  bench [--days 60] [--seed 42] [--svg] [--check] [--json]
      Run the deterministic 60-day multi-persona Syntopik-Eval cognitive benchmark.
  rosetta [scan|bridge|link|archetypes] [--prop-id <id>] [--source <id>] [--target <id>] [--archetype <id>] [--json]
      Discover cross-domain structural isomorphisms, bijection openers, and Rosetta chords.
  sync-delta [export|merge|verify|status] [--out <path>] [--file <path>] [--since <iso>] [--dry-run] [--json]
      Export, verify, or monotonically merge CRDT multi-device sync deltas.
  calibrate-policy [--apply] [--json]
      Replay longitudinal learner history on a 70/30 chronological split to calibrate hyperparameters.
  proof-diff --id <derivation_id> --steps-json <json_or_path> [--no-spawn-fork] [--json]
      Diff handwritten proof steps against a Causal Step DAG with zero-leak R0-R2 nudges.
  pack <export|import|inspect|verify> [...]
      Create, verify, or import zero-PII portable Study Packs (.syntopik-pack.zip).
  export <obsidian|pocket|anki|pack|json|dossier> [--out|--vault <path>] [--pocket] [...]
      Export to Obsidian vault, offline Pocket HTML deck, Anki .apkg, Study Pack, JSON, or Mastery Dossier.
  connect <harness> [--bundle studio|full]
      Install or print global plugin/extension configuration for any of the 14 harnesses.
  doctor [--core-only] [--compat] [--fix] [--json]
      Run workspace, runtime, 14-agent compatibility matrix (--compat), and packaging diagnostics.
  run <engine> [args...]
      Execute any bundled Syntopik engine script cleanly from any directory.

Options:
  -h, --help       Show this help message
  -v, --version    Print version
`;

function isEphemeralInstallRoot(rootDir = PACKAGE_ROOT) {
	const raw = String(rootDir || "").trim();
	if (!raw) return false;
	const norm = raw.replace(/\\/gu, "/").replace(/\/+/gu, "/").toLowerCase();
	const parts = norm.split("/").filter(Boolean);
	const partSet = new Set(parts);
	if (partSet.has("_npx") || partSet.has("uv-cache") || partSet.has("pipx") || partSet.has(".pipx")) {
		return true;
	}
	if (/(?:^|\/)uv\/(?:cache\/)?(?:archive-v\d+|builds-v\d+|environments-v\d+)(?:\/|$)/u.test(norm)) {
		return true;
	}
	if (parts.some((p) => p.startsWith("archive-v")) && partSet.has("uv")) {
		return true;
	}
	if (
		norm === "/tmp" ||
		norm.startsWith("/tmp/") ||
		norm === "/private/tmp" ||
		norm.startsWith("/private/tmp/") ||
		norm.startsWith("/var/folders/") ||
		norm.startsWith("/private/var/folders/") ||
		norm.includes("/appdata/local/temp/") ||
		norm.endsWith("/appdata/local/temp") ||
		norm.includes("/windows/temp/") ||
		norm.endsWith("/windows/temp")
	) {
		return true;
	}
	try {
		const tmpNorm = path.resolve(os.tmpdir()).replace(/\\/gu, "/").toLowerCase();
		const resNorm = path.resolve(raw).replace(/\\/gu, "/").toLowerCase();
		if (resNorm === tmpNorm || resNorm.startsWith(`${tmpNorm}/`)) {
			return true;
		}
	} catch {
		// ignore
	}
	return false;
}

function isEphemeralNpxRoot(rootDir = PACKAGE_ROOT) {
	return isEphemeralInstallRoot(rootDir);
}

function parseFlag(argv, flag) {
	for (let i = 0; i < argv.length; i++) {
		if (argv[i] === flag && i + 1 < argv.length) return argv[i + 1];
		if (argv[i].startsWith(`${flag}=`)) return argv[i].slice(flag.length + 1);
	}
	return null;
}

function hasFlag(argv, flag) {
	return argv.includes(flag);
}

function positionalArgs(argv, valueFlags) {
	const out = [];
	const valSet = new Set(valueFlags);
	for (let i = 0; i < argv.length; i++) {
		const tok = argv[i];
		if (tok === "--") {
			out.push(...argv.slice(i + 1));
			break;
		}
		if (tok.startsWith("-")) {
			if (!tok.includes("=") && valSet.has(tok)) {
				i += 1;
			}
			continue;
		}
		out.push(tok);
	}
	return out;
}

function isDir(p) {
	try {
		return existsSync(p) && statSync(p).isDirectory();
	} catch {
		return false;
	}
}

function isFile(p) {
	try {
		return existsSync(p) && statSync(p).isFile();
	} catch {
		return false;
	}
}

function resolveAttachedWorkspace(cwdDir) {
	const abs = path.resolve(cwdDir);
	const dotSyntKg = path.join(abs, ".syntopik", "knowledge-graph");
	if (isDir(dotSyntKg)) return path.join(abs, ".syntopik");
	if (isDir(path.join(abs, "knowledge-graph"))) return abs;
	if (isDir(path.join(abs, ".syntopik"))) return path.join(abs, ".syntopik");
	return null;
}

function ensureLearnerWorkspace(wsDir) {
	const ws = path.resolve(wsDir);
	const kgDir = path.join(ws, "knowledge-graph");
	const createdDirs = [];
	const createdFiles = [];

	mkdirSync(ws, { recursive: true });
	for (const rel of LEARNER_DIRS) {
		const target = path.join(ws, rel);
		if (!existsSync(target)) {
			mkdirSync(target, { recursive: true });
			createdDirs.push(rel);
		}
	}

	for (const fname of LEDGER_FILES) {
		const fp = path.join(kgDir, fname);
		if (!existsSync(fp)) {
			writeFileSync(fp, "", "utf8");
			createdFiles.push(`knowledge-graph/${fname}`);
		}
	}

	const rqPath = path.join(kgDir, "review-queue.md");
	if (!existsSync(rqPath)) {
		writeFileSync(rqPath, STARTER_REVIEW_QUEUE, "utf8");
		createdFiles.push("knowledge-graph/review-queue.md");
	}

	const graphJs = path.join(kgDir, "graph-data.js");
	const syncScript = path.join(PACKAGE_ROOT, "scripts", "sync-graph.mjs");
	if (!existsSync(graphJs) && existsSync(syncScript)) {
		const res = spawnSync(
			process.execPath,
			[
				syncScript,
				"--workspace",
				ws,
				"--kg-dir",
				kgDir,
				"--chapters-dir",
				path.join(ws, "chapters"),
				"--out",
				graphJs,
				"--quiet",
			],
			{ cwd: ws, stdio: "pipe", encoding: "utf8" },
		);
		if (res.status === 0 && existsSync(graphJs)) {
			createdFiles.push("knowledge-graph/graph-data.js");
		}
	}

	return {
		ok: true,
		workspace: ws,
		kgDir,
		createdDirs,
		createdFiles,
	};
}

function resolveGitInfoExclude(repoDir) {
	const dotGit = path.join(repoDir, ".git");
	if (!existsSync(dotGit)) return null;
	try {
		const st = lstatSync(dotGit);
		if (st.isDirectory()) {
			return path.join(dotGit, "info", "exclude");
		}
		if (st.isFile()) {
			const raw = readFileSync(dotGit, "utf8");
			const m = /^gitdir:\s*(.+)$/mu.exec(raw.trim());
			if (m) {
				const gitDir = path.resolve(repoDir, m[1].trim());
				return path.join(gitDir, "info", "exclude");
			}
		}
	} catch {
		return null;
	}
	return null;
}

function updateGitExclude(repoDir, entries) {
	const excludeFile = resolveGitInfoExclude(repoDir);
	if (!excludeFile) return { updated: false, path: null };

	mkdirSync(path.dirname(excludeFile), { recursive: true });
	const existing = existsSync(excludeFile) ? readFileSync(excludeFile, "utf8") : "";
	const beginMarker = "# >>> syntopik >>>";
	const endMarker = "# <<< syntopik <<<";

	const startIdx = existing.indexOf(beginMarker);
	const endIdx = existing.indexOf(endMarker);
	const priorEntries =
		startIdx !== -1 && endIdx !== -1 && endIdx >= startIdx
			? existing
					.slice(startIdx + beginMarker.length, endIdx)
					.split(/\r?\n/u)
					.map((ln) => ln.trim())
					.filter(Boolean)
			: [];

	const uniqueEntries = [...new Set([...priorEntries, ...entries.filter(Boolean)])];
	const block = [beginMarker, ...uniqueEntries, endMarker].join("\n");

	let nextContent;
	if (startIdx !== -1 && endIdx !== -1 && endIdx >= startIdx) {
		const before = existing.slice(0, startIdx).replace(/\s+$/u, "");
		const after = existing.slice(endIdx + endMarker.length).replace(/^\s+/u, "");
		const parts = [];
		if (before) parts.push(before);
		parts.push(block);
		if (after) parts.push(after);
		nextContent = `${parts.join("\n\n")}\n`;
	} else {
		const trimmed = existing.replace(/\s+$/u, "");
		nextContent = trimmed ? `${trimmed}\n\n${block}\n` : `${block}\n`;
	}

	writeFileSync(excludeFile, nextContent, "utf8");
	return { updated: true, path: excludeFile, entries: uniqueEntries };
}

function pathExistsOrSymlink(p) {
	try {
		lstatSync(p);
		return true;
	} catch {
		return false;
	}
}

function isRegularFileOrNonDirSymlink(p) {
	try {
		const lst = lstatSync(p);
		if (lst.isFile()) return true;
		if (lst.isSymbolicLink()) {
			try {
				return !statSync(p).isDirectory();
			} catch {
				return true;
			}
		}
		return false;
	} catch {
		return false;
	}
}

function stripJsonc(text) {
	let out = "";
	let inStr = false;
	let esc = false;
	for (let i = 0; i < text.length; i++) {
		const ch = text[i];
		if (inStr) {
			out += ch;
			if (esc) esc = false;
			else if (ch === "\\") esc = true;
			else if (ch === '"') inStr = false;
			continue;
		}
		if (ch === '"') {
			inStr = true;
			out += ch;
			continue;
		}
		if (ch === "/" && text[i + 1] === "/") {
			i += 2;
			while (i < text.length && text[i] !== "\n" && text[i] !== "\r") i++;
			i--;
			continue;
		}
		if (ch === "/" && text[i + 1] === "*") {
			i += 2;
			while (i + 1 < text.length && !(text[i] === "*" && text[i + 1] === "/")) i++;
			i++;
			continue;
		}
		out += ch;
	}
	// Strip trailing commas outside strings
	let res = "";
	inStr = false;
	esc = false;
	for (let i = 0; i < out.length; i++) {
		const ch = out[i];
		if (inStr) {
			res += ch;
			if (esc) esc = false;
			else if (ch === "\\") esc = true;
			else if (ch === '"') inStr = false;
			continue;
		}
		if (ch === '"') {
			inStr = true;
			res += ch;
			continue;
		}
		if (ch === ",") {
			let j = i + 1;
			while (j < out.length && /\s/u.test(out[j])) j++;
			if (j < out.length && (out[j] === "}" || out[j] === "]")) continue;
		}
		res += ch;
	}
	return res;
}

function writeJsonMerged(filePath, mutator) {
	return writeJsoncMerged(filePath, mutator);
}

function applyJsonMerge(filePath, mutator, relLabel, { written, preserved, excludeEntries, notes, trackExclude = true }) {
	const res = writeJsonMerged(filePath, mutator);
	if (res.merged) {
		if (!written.includes(relLabel)) written.push(relLabel);
		if (trackExclude && !res.existed) excludeEntries.push(relLabel);
		return true;
	}
	if (!preserved.includes(relLabel)) preserved.push(relLabel);
	notes.push(`Warning: preserved existing ${relLabel} (could not parse safely as JSON/JSONC).`);
	return false;
}

function writeJsoncMerged(filePath, mutator) {
	const existed = existsSync(filePath);
	let data = {};
	if (existed) {
		const raw = readFileSync(filePath, "utf8");
		if (raw.trim()) {
			try {
				const parsed = JSON.parse(stripJsonc(raw));
				if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
					return { existed: true, merged: false };
				}
				data = parsed;
			} catch {
				return { existed: true, merged: false };
			}
		}
	}
	mutator(data);
	mkdirSync(path.dirname(filePath), { recursive: true });
	writeFileSync(filePath, `${JSON.stringify(data, null, 2)}\n`, "utf8");
	return { existed, merged: true };
}

function upsertCodexTomlSection(prevToml, snippet) {
	const header = "[mcp_servers.syntopik-studio]";
	if (!prevToml.includes(header)) {
		return prevToml.trim() ? `${prevToml.replace(/\s+$/u, "")}\n\n${snippet.trim()}\n` : `${snippet.trim()}\n`;
	}
	const lines = prevToml.split(/\r?\n/u);
	let startIdx = -1;
	let endIdx = lines.length;
	for (let i = 0; i < lines.length; i++) {
		const trimmed = lines[i].trim();
		if (startIdx === -1) {
			if (trimmed === header) startIdx = i;
		} else if (trimmed.startsWith("[") && trimmed.endsWith("]")) {
			endIdx = i;
			break;
		}
	}
	if (startIdx === -1) {
		return prevToml.trim() ? `${prevToml.replace(/\s+$/u, "")}\n\n${snippet.trim()}\n` : `${snippet.trim()}\n`;
	}
	const before = lines.slice(0, startIdx).join("\n").replace(/\s+$/u, "");
	const after = lines.slice(endIdx).join("\n").replace(/^\s+/u, "");
	const parts = [before, snippet.trim(), after].filter(Boolean);
	return `${parts.join("\n\n")}\n`;
}

function stripYamlScalar(val) {
	const t = val.trim();
	if (t.length >= 2 && ((t.startsWith('"') && t.endsWith('"')) || (t.startsWith("'") && t.endsWith("'")))) {
		return t.slice(1, -1);
	}
	return t;
}

function mergeAiderYaml(prevYaml, desiredReads) {
	const desired = [...new Set(desiredReads.filter(Boolean))];
	if (!prevYaml.trim()) {
		return `read:\n${desired.map((d) => `  - ${d}`).join("\n")}\n`;
	}
	const lines = prevYaml.split(/\r?\n/u);
	const readIdx = lines.findIndex((ln) => /^read\s*:/u.test(ln));
	if (readIdx === -1) {
		return `${prevYaml.replace(/\s+$/u, "")}\n\nread:\n${desired.map((d) => `  - ${d}`).join("\n")}\n`;
	}
	const mHead = /^read\s*:\s*(.*)$/u.exec(lines[readIdx]);
	const restRaw = mHead ? mHead[1] : "";
	const restClean =
		restRaw.trim().startsWith('"') || restRaw.trim().startsWith("'")
			? restRaw.trim()
			: restRaw.split("#")[0].trim();

	if (restClean.startsWith("[") && restClean.endsWith("]")) {
		const inner = restClean.slice(1, -1).trim();
		const existing = inner ? inner.split(",").map((s) => stripYamlScalar(s)).filter(Boolean) : [];
		for (const item of desired) {
			if (!existing.includes(item)) existing.push(item);
		}
		lines[readIdx] = `read: [${existing.join(", ")}]`;
		return `${lines.join("\n")}\n`;
	}
	if (restClean) {
		const single = stripYamlScalar(restClean);
		const merged = single ? [single] : [];
		for (const item of desired) {
			if (!merged.includes(item)) merged.push(item);
		}
		lines.splice(readIdx, 1, "read:", ...merged.map((m) => `  - ${m}`));
		return `${lines.join("\n")}\n`;
	}

	let endIdx = readIdx + 1;
	const existingItems = [];
	let indent = "  ";
	let lastItemIdx = readIdx;
	while (endIdx < lines.length) {
		const ln = lines[endIdx];
		if (!ln.trim() || ln.trimStart().startsWith("#")) {
			endIdx++;
			continue;
		}
		if (!ln.startsWith(" ") && !ln.startsWith("\t")) break;
		const mItem = /^([ \t]+)-\s+(.+?)\s*$/u.exec(ln);
		if (mItem) {
			indent = mItem[1];
			existingItems.push(stripYamlScalar(mItem[2].split(" #")[0]));
			lastItemIdx = endIdx;
		}
		endIdx++;
	}
	const toAdd = desired.filter((item) => !existingItems.includes(item));
	if (toAdd.length > 0) {
		lines.splice(lastItemIdx + 1, 0, ...toAdd.map((item) => `${indent}- ${item}`));
	}
	return `${lines.join("\n")}\n`;
}

function linkOrCopyDir(srcDir, destDir, forceCopy) {
	if (pathExistsOrSymlink(destDir)) {
		return { action: "preserved", created: false };
	}
	const absSrc = path.resolve(srcDir);
	mkdirSync(path.dirname(destDir), { recursive: true });
	if (!forceCopy) {
		try {
			// Always use an absolute target path so Windows NTFS junctions succeed cleanly.
			symlinkSync(absSrc, destDir, process.platform === "win32" ? "junction" : "dir");
			return { action: process.platform === "win32" ? "junction" : "symlinked", created: true };
		} catch {
			// Fall back to recursive copy on symlink-restricted environments
		}
	}
	cpSync(absSrc, destDir, { recursive: true });
	return { action: "copied", created: true };
}

function vscodeUserDirs(homeDir) {
	const dirs = [
		path.join(homeDir, "Library", "Application Support", "Code", "User"),
		path.join(homeDir, ".config", "Code", "User"),
		path.join(homeDir, "AppData", "Roaming", "Code", "User"),
	];
	if (process.env.APPDATA && process.env.APPDATA.trim()) {
		dirs.unshift(path.join(process.env.APPDATA, "Code", "User"));
	}
	return dirs;
}

function resolveClineMcpSettingsPath(homeDir) {
	if (process.env.CLINE_MCP_SETTINGS_PATH && process.env.CLINE_MCP_SETTINGS_PATH.trim()) {
		return path.resolve(process.env.CLINE_MCP_SETTINGS_PATH.trim());
	}
	for (const d of vscodeUserDirs(homeDir)) {
		const gsDir = path.join(d, "globalStorage", "saoudrizwan.claude-dev");
		if (isDir(gsDir)) {
			return path.join(gsDir, "settings", "cline_mcp_settings.json");
		}
	}
	return path.join(homeDir, ".cline", "data", "settings", "cline_mcp_settings.json");
}

function detectInstalledHarnesses(targetDir) {
	const homeDir = os.homedir();
	const xdgConfig = process.env.XDG_CONFIG_HOME || path.join(homeDir, ".config");
	const appdata = process.env.APPDATA || path.join(homeDir, "AppData", "Roaming");
	const vsDirs = vscodeUserDirs(homeDir);
	const hasAgyHome = isDir(path.join(homeDir, ".gemini", "antigravity"));

	const checks = [
		[
			"antigravity",
			isFile(path.join(targetDir, ".agents", "hooks.json")) || hasAgyHome,
		],
		[
			"claude",
			existsSync(path.join(targetDir, ".claude")) ||
				isFile(path.join(targetDir, ".mcp.json")) ||
				isDir(path.join(homeDir, ".claude")) ||
				Boolean(process.env.CLAUDECODE),
		],
		[
			"codex",
			existsSync(path.join(targetDir, ".codex")) ||
				isDir(path.join(homeDir, ".codex")) ||
				Boolean(process.env.CODEX_HOME),
		],
		[
			"cursor",
			existsSync(path.join(targetDir, ".cursor")) ||
				isDir(path.join(homeDir, ".cursor")) ||
				(process.platform === "darwin" && isDir("/Applications/Cursor.app")),
		],
		[
			"windsurf",
			existsSync(path.join(targetDir, ".windsurf")) ||
				isDir(path.join(homeDir, ".windsurf")) ||
				isDir(path.join(homeDir, ".codeium", "windsurf")) ||
				(process.platform === "darwin" && isDir("/Applications/Windsurf.app")),
		],
		[
			"copilot",
			existsSync(path.join(targetDir, ".vscode")) ||
				existsSync(path.join(targetDir, ".github", "instructions")) ||
				vsDirs.some((d) => isDir(d)),
		],
		[
			"gemini",
			existsSync(path.join(targetDir, ".gemini")) ||
				isFile(path.join(homeDir, ".gemini", "settings.json")) ||
				isDir(path.join(homeDir, ".gemini", "extensions")) ||
				(isDir(path.join(homeDir, ".gemini")) && !hasAgyHome),
		],
		[
			"opencode",
			isFile(path.join(targetDir, "opencode.json")) ||
				isDir(path.join(xdgConfig, "opencode")) ||
				isDir(path.join(appdata, "opencode")),
		],
		[
			"cline",
			pathExistsOrSymlink(path.join(targetDir, ".clinerules")) ||
				isDir(path.join(homeDir, ".cline")) ||
				Boolean(process.env.CLINE_MCP_SETTINGS_PATH) ||
				vsDirs.some((d) => isDir(path.join(d, "globalStorage", "saoudrizwan.claude-dev"))),
		],
		[
			"roo",
			existsSync(path.join(targetDir, ".roo")) ||
				isDir(path.join(homeDir, ".roo")) ||
				vsDirs.some((d) => isDir(path.join(d, "globalStorage", "rooveterinaryinc.roo-cline"))),
		],
		[
			"zed",
			existsSync(path.join(targetDir, ".zed")) ||
				isDir(path.join(xdgConfig, "zed")) ||
				isDir(path.join(appdata, "Zed")) ||
				(process.platform === "darwin" && isDir("/Applications/Zed.app")),
		],
		[
			"aider",
			isFile(path.join(targetDir, ".aider.conf.yml")) ||
				isFile(path.join(homeDir, ".aider.conf.yml")),
		],
		[
			"continue",
			existsSync(path.join(targetDir, ".continue")) ||
				isDir(path.join(homeDir, ".continue")) ||
				vsDirs.some((d) => isDir(path.join(d, "globalStorage", "continue.continue"))),
		],
		[
			"kiro",
			existsSync(path.join(targetDir, ".kiro")) ||
				isDir(path.join(homeDir, ".kiro")) ||
				(process.platform === "darwin" && isDir("/Applications/Kiro.app")),
		],
	];

	const detected = checks.filter(([, present]) => present).map(([name]) => name);
	return detected.length > 0 ? detected : [...DEFAULT_FALLBACK_HARNESSES];
}

function hookListHasCommand(hookList, commandStr) {
	if (!Array.isArray(hookList)) return false;
	for (const group of hookList) {
		if (!group || typeof group !== "object") continue;
		if (group.command === commandStr) return true;
		if (Array.isArray(group.hooks)) {
			for (const h of group.hooks) {
				if (h && typeof h === "object" && h.command === commandStr) return true;
			}
		}
	}
	return false;
}

function appendDedupHook(hookList, entry, commandStr) {
	if (!hookListHasCommand(hookList, commandStr)) {
		hookList.push(entry);
	}
}

function inspectAntigravityPlugin() {
	const pluginsRoot = path.join(os.homedir(), ".gemini", "config", "plugins");
	for (const name of ["syntopik", "syntopik-studio", "mlh-studio"]) {
		const pdir = path.join(pluginsRoot, name);
		if (isFile(path.join(pdir, "plugin.json"))) {
			return {
				installed: true,
				pluginName: name,
				pluginDir: pdir,
				hasDistServer: isFile(path.join(pdir, "dist", "server.mjs")),
			};
		}
	}
	return {
		installed: false,
		pluginName: null,
		pluginDir: path.join(pluginsRoot, "syntopik"),
		hasDistServer: false,
	};
}

function runAttachNative(argv) {
	const pos = positionalArgs(argv, ["--harness", "--vault", "--transport", "--http-url"]);
	const targetDir = path.resolve(pos[0] || ".");
	const harnessRaw = (parseFlag(argv, "--harness") || "auto").toLowerCase();
	const vaultMode = (parseFlag(argv, "--vault") || "global").toLowerCase();
	const transportMode = (parseFlag(argv, "--transport") || "stdio").toLowerCase();
	const httpUrl = parseFlag(argv, "--http-url") || DEFAULT_HTTP_MCP_URL;
	const copySkillsFlag = hasFlag(argv, "--copy-skills");
	const asJson = hasFlag(argv, "--json");

	if (!["global", "local"].includes(vaultMode)) {
		console.error(`Invalid --vault '${vaultMode}': expected 'global' or 'local'.`);
		return 2;
	}
	if (!["stdio", "http"].includes(transportMode)) {
		console.error(`Invalid --transport '${transportMode}': expected 'stdio' or 'http'.`);
		return 2;
	}
	const validHarnesses = new Set(["auto", "all", ...ALL_HARNESSES]);
	if (!validHarnesses.has(harnessRaw)) {
		console.error(`Invalid --harness '${harnessRaw}'.`);
		return 2;
	}

	mkdirSync(targetDir, { recursive: true });

	const homeDir = os.homedir();
	const vaultPath =
		vaultMode === "local"
			? path.join(targetDir, ".syntopik")
			: path.join(homeDir, ".syntopik");
	const vaultDisplay = vaultMode === "local" ? ".syntopik/" : "~/.syntopik/";
	const vaultInit = ensureLearnerWorkspace(vaultPath);

	const selected =
		harnessRaw === "all"
			? [...ALL_HARNESSES]
			: harnessRaw === "auto"
				? detectInstalledHarnesses(targetDir)
				: [harnessRaw];

	const ephemeralNpx = isEphemeralInstallRoot(PACKAGE_ROOT);
	const effectiveCopySkills = copySkillsFlag || ephemeralNpx;
	const writeGlobal =
		harnessRaw !== "auto" ||
		!(vaultMode === "local" && isEphemeralInstallRoot(targetDir) && !isEphemeralInstallRoot(homeDir));

	const serverMjs = path.resolve(PACKAGE_ROOT, "mcp-app", "mlh-studio", "dist", "server.mjs");
	const srcSkills = path.resolve(PACKAGE_ROOT, ".agents", "skills");
	const hooksDir = path.resolve(PACKAGE_ROOT, ".agents", "hooks");

	// Never bake ephemeral _npx / uv/archive-v0 / pipx / temp paths into repo configs!
	const mcpCommand = ephemeralNpx ? "npx" : "node";
	const mcpArgs = ephemeralNpx
		? ["-y", "syntopik", "mcp", `--workspace=${vaultPath}`]
		: [serverMjs, `--workspace=${vaultPath}`];
	const sessionHookCmd = ephemeralNpx
		? `npx -y syntopik hook session-start --workspace "${vaultPath}"`
		: `node "${path.join(hooksDir, "session-start.mjs")}" --workspace "${vaultPath}"`;
	const postHookCmd = ephemeralNpx
		? `npx -y syntopik hook post-graph-sync --workspace "${vaultPath}"`
		: `node "${path.join(hooksDir, "post-graph-sync.mjs")}" --workspace "${vaultPath}"`;

	const genericRulesMd = `# Syntopik Deep-Mastery Co-Pilot Rules\n\nFor learning, deep-understanding, review, or exam-prep tasks, activate Syntopik skills in \`.agents/skills/\` and persist study state to \`${vaultDisplay}\`. Keep turns <= 160w prose, 1 active demand/turn, zero sycophancy, and verify math/code deterministically via \`syntopik run <engine>\`.\n`;
	const esc = (s) => String(s).replace(/\\/gu, "\\\\").replace(/"/gu, '\\"');

	const excludeEntries = [".syntopik/"];
	const written = [];
	const preserved = [];
	const notes = [];
	const addUnique = (arr, val) => {
		if (!arr.includes(val)) arr.push(val);
	};

	// Never touch existing host AGENTS.md, CLAUDE.md, or GEMINI.md
	for (const hostDoc of ["AGENTS.md", "CLAUDE.md", "GEMINI.md"]) {
		if (existsSync(path.join(targetDir, hostDoc))) {
			addUnique(preserved, hostDoc);
		}
	}

	// Link/copy canonical .agents/skills (always copy when running from ephemeral cache)
	if (existsSync(srcSkills)) {
		const destSkills = path.join(targetDir, ".agents", "skills");
		const skRes = linkOrCopyDir(srcSkills, destSkills, effectiveCopySkills);
		if (skRes.created) {
			addUnique(written, `.agents/skills (${skRes.action})`);
			excludeEntries.push(".agents/skills", ".agents/skills/");
		} else {
			addUnique(preserved, ".agents/skills");
		}
	}

	const isHttp = transportMode === "http";

	if (selected.includes("cursor")) {
		const rulePath = path.join(targetDir, ".cursor", "rules", "syntopik.mdc");
		const existedRule = existsSync(rulePath);
		mkdirSync(path.dirname(rulePath), { recursive: true });
		writeFileSync(
			rulePath,
			`---\ndescription: Syntopik universal deep-mastery co-pilot rules and study vault pointer\nalwaysApply: false\n---\n\nFor learning, deep-understanding, review, or exam-prep tasks, load Syntopik skills from \`.agents/skills/\` and persist knowledge-graph state in \`${vaultDisplay}\`. Keep turns <= 160w prose, 1 active demand/turn, zero sycophancy, and run deterministic verifiers via \`syntopik run <engine>\`.\n`,
			"utf8",
		);
		addUnique(written, ".cursor/rules/syntopik.mdc");
		if (!existedRule) excludeEntries.push(".cursor/rules/syntopik.mdc");

		const cursorMcp = path.join(targetDir, ".cursor", "mcp.json");
		applyJsonMerge(
			cursorMcp,
			(doc) => {
				if (!doc.mcpServers || typeof doc.mcpServers !== "object") doc.mcpServers = {};
				doc.mcpServers["syntopik-studio"] = isHttp
					? { url: httpUrl, headers: { Authorization: "Bearer ${env:SYNTOPIK_TOKEN}" } }
					: { command: mcpCommand, args: [...mcpArgs] };
			},
			".cursor/mcp.json",
			{ written, preserved, excludeEntries, notes },
		);
	}

	if (selected.includes("windsurf")) {
		const wsRule = path.join(targetDir, ".windsurf", "rules", "syntopik.md");
		const existedWs = existsSync(wsRule);
		mkdirSync(path.dirname(wsRule), { recursive: true });
		writeFileSync(
			wsRule,
			`---\ntrigger: model_decision\ndescription: Syntopik universal deep-mastery co-pilot rules and study vault pointer\n---\n\nFor learning, study, revision, or concept-mastery requests, follow Syntopik skills in \`.agents/skills/\` and persist study state to \`${vaultDisplay}\`. Keep turns <= 160w prose, 1 active demand/turn, zero sycophancy, and verify math/code via \`syntopik run <engine>\`.\n`,
			"utf8",
		);
		addUnique(written, ".windsurf/rules/syntopik.md");
		if (!existedWs) excludeEntries.push(".windsurf/rules/syntopik.md");

		const wsMcp = path.join(homeDir, ".codeium", "windsurf", "mcp_config.json");
		if (writeGlobal) {
			applyJsonMerge(
				wsMcp,
				(doc) => {
					if (!doc.mcpServers || typeof doc.mcpServers !== "object") doc.mcpServers = {};
					// Windsurf HTTP uses `serverUrl` (NOT `url`!)
					doc.mcpServers["syntopik-studio"] = isHttp
						? { serverUrl: httpUrl, headers: { Authorization: "Bearer ${env:SYNTOPIK_TOKEN}" } }
						: { command: mcpCommand, args: [...mcpArgs] };
				},
				wsMcp,
				{ written, preserved, excludeEntries, notes, trackExclude: false },
			);
		} else {
			notes.push(`Windsurf global MCP config skipped in local-sandbox auto mode (${wsMcp}).`);
		}
	}

	if (selected.includes("copilot")) {
		const ghInst = path.join(targetDir, ".github", "instructions", "syntopik.instructions.md");
		const existedGh = existsSync(ghInst);
		mkdirSync(path.dirname(ghInst), { recursive: true });
		writeFileSync(
			ghInst,
			`---\napplyTo: "**"\n---\n\nWhen asked to teach, probe, review, or verify STEM/CS concepts, follow Syntopik skills in \`.agents/skills/\` (vault: \`${vaultDisplay}\`): <= 160w calm turns, 1 active demand/turn, zero sycophancy, and deterministic checks via \`syntopik run <engine>\`.\n`,
			"utf8",
		);
		addUnique(written, ".github/instructions/syntopik.instructions.md");
		if (!existedGh) excludeEntries.push(".github/instructions/syntopik.instructions.md");

		const vscodeMcp = path.join(targetDir, ".vscode", "mcp.json");
		applyJsonMerge(
			vscodeMcp,
			(doc) => {
				// Copilot / VS Code root key is `servers` (NOT `mcpServers`!)
				if (!doc.servers || typeof doc.servers !== "object") doc.servers = {};
				doc.servers["syntopik-studio"] = isHttp
					? { type: "http", url: httpUrl, headers: { Authorization: "Bearer ${env:SYNTOPIK_TOKEN}" } }
					: { type: "stdio", command: mcpCommand, args: [...mcpArgs], cwd: "${workspaceFolder}" };
			},
			".vscode/mcp.json",
			{ written, preserved, excludeEntries, notes },
		);
	}

	if (selected.includes("opencode")) {
		const ocPath = path.join(targetDir, "opencode.json");
		applyJsonMerge(
			ocPath,
			(doc) => {
				if (!doc.mcp || typeof doc.mcp !== "object") doc.mcp = {};
				doc.mcp["syntopik-studio"] = isHttp
					? {
							type: "remote",
							url: httpUrl,
							headers: { Authorization: "Bearer {env:SYNTOPIK_TOKEN}" },
							enabled: true,
						}
					: {
							type: "local",
							command: [mcpCommand, ...mcpArgs],
							environment: { SYNT_WORKSPACE: vaultPath },
							enabled: true,
						};
			},
			"opencode.json",
			{ written, preserved, excludeEntries, notes },
		);
	}

	if (selected.includes("codex")) {
		const codexToml = path.join(targetDir, ".codex", "config.toml");
		const existed = existsSync(codexToml);
		const prev = existed ? readFileSync(codexToml, "utf8") : "";
		mkdirSync(path.dirname(codexToml), { recursive: true });
		const argsList = mcpArgs.map((a) => `"${esc(a)}"`).join(", ");
		const snippet = isHttp
			? [
					"[mcp_servers.syntopik-studio]",
					`url = "${esc(httpUrl)}"`,
					'bearer_token_env_var = "SYNTOPIK_TOKEN"',
					"startup_timeout_sec = 10",
					"tool_timeout_sec = 60",
				].join("\n")
			: [
					"[mcp_servers.syntopik-studio]",
					`command = "${esc(mcpCommand)}"`,
					`args = [${argsList}]`,
					"startup_timeout_sec = 10",
					"tool_timeout_sec = 60",
				].join("\n");
		writeFileSync(codexToml, upsertCodexTomlSection(prev, snippet), "utf8");
		addUnique(written, ".codex/config.toml");
		if (!existed) excludeEntries.push(".codex/config.toml");
	}

	if (selected.includes("claude")) {
		const rootMcp = path.join(targetDir, ".mcp.json");
		applyJsonMerge(
			rootMcp,
			(doc) => {
				if (!doc.mcpServers || typeof doc.mcpServers !== "object") doc.mcpServers = {};
				doc.mcpServers["syntopik-studio"] = isHttp
					? { type: "http", url: httpUrl, headers: { Authorization: "Bearer ${SYNTOPIK_TOKEN}" } }
					: { type: "stdio", command: mcpCommand, args: [...mcpArgs] };
			},
			".mcp.json",
			{ written, preserved, excludeEntries, notes },
		);

		const claudeSettings = path.join(targetDir, ".claude", "settings.json");
		applyJsonMerge(
			claudeSettings,
			(doc) => {
				if (!doc.hooks || typeof doc.hooks !== "object") doc.hooks = {};
				if (!Array.isArray(doc.hooks.SessionStart)) doc.hooks.SessionStart = [];
				if (!Array.isArray(doc.hooks.PostToolUse)) doc.hooks.PostToolUse = [];

				appendDedupHook(
					doc.hooks.SessionStart,
					{
						hooks: [{ type: "command", command: sessionHookCmd, timeout: 10 }],
					},
					sessionHookCmd,
				);
				appendDedupHook(
					doc.hooks.PostToolUse,
					{
						matcher: "Write|Edit|MultiEdit|NotebookEdit|Bash",
						hooks: [{ type: "command", command: postHookCmd, timeout: 10 }],
					},
					postHookCmd,
				);
			},
			".claude/settings.json",
			{ written, preserved, excludeEntries, notes },
		);

		const claudeSkills = path.join(targetDir, ".claude", "skills");
		const agentsSkillsDest = path.join(targetDir, ".agents", "skills");
		if (existsSync(agentsSkillsDest)) {
			const cRes = linkOrCopyDir(path.resolve(agentsSkillsDest), claudeSkills, effectiveCopySkills);
			if (cRes.created) {
				addUnique(written, `.claude/skills (${cRes.action})`);
				excludeEntries.push(".claude/skills", ".claude/skills/");
			} else {
				addUnique(preserved, ".claude/skills");
			}
		}
	}

	let antigravityInfo = null;
	if (selected.includes("antigravity")) {
		const agyHooks = path.join(targetDir, ".agents", "hooks.json");
		applyJsonMerge(
			agyHooks,
			(doc) => {
				if (!doc["study-session-context"] || typeof doc["study-session-context"] !== "object") {
					doc["study-session-context"] = {};
				}
				if (!Array.isArray(doc["study-session-context"].SessionStart)) {
					doc["study-session-context"].SessionStart = [];
				}
				appendDedupHook(
					doc["study-session-context"].SessionStart,
					{ type: "command", command: sessionHookCmd, timeout: 10 },
					sessionHookCmd,
				);

				if (!doc["knowledge-graph-autosync"] || typeof doc["knowledge-graph-autosync"] !== "object") {
					doc["knowledge-graph-autosync"] = {};
				}
				if (!Array.isArray(doc["knowledge-graph-autosync"].PostToolUse)) {
					doc["knowledge-graph-autosync"].PostToolUse = [];
				}
				appendDedupHook(
					doc["knowledge-graph-autosync"].PostToolUse,
					{
						matcher:
							"write_file|edit_file|replace_file_content|multi_replace_file_content|write_to_file|run_command",
						type: "command",
						command: postHookCmd,
						timeout: 10,
					},
					postHookCmd,
				);
			},
			".agents/hooks.json",
			{ written, preserved, excludeEntries, notes },
		);

		antigravityInfo = {
			...inspectAntigravityPlugin(),
			workspaceHooks: ".agents/hooks.json",
		};
		notes.push(
			antigravityInfo.installed
				? `Antigravity plugin detected (${antigravityInfo.pluginName}); workspace hooks wired in .agents/hooks.json.`
				: "Antigravity workspace hooks wired in .agents/hooks.json (host AGENTS.md preserved). Run `syntopik connect antigravity` for global Studio MCP.",
		);
	}

	if (selected.includes("gemini")) {
		const gemSettings = path.join(targetDir, ".gemini", "settings.json");
		applyJsonMerge(
			gemSettings,
			(doc) => {
				if (!doc.mcpServers || typeof doc.mcpServers !== "object") doc.mcpServers = {};
				// Gemini CLI HTTP uses `httpUrl` (NOT `url`!)
				doc.mcpServers["syntopik-studio"] = isHttp
					? { httpUrl: httpUrl, headers: { Authorization: "Bearer ${SYNTOPIK_TOKEN}" } }
					: { command: mcpCommand, args: [...mcpArgs] };
			},
			".gemini/settings.json",
			{ written, preserved, excludeEntries, notes },
		);
	}

	if (selected.includes("cline")) {
		const clinerulesRoot = path.join(targetDir, ".clinerules");
		if (isRegularFileOrNonDirSymlink(clinerulesRoot)) {
			addUnique(preserved, ".clinerules");
			notes.push("Preserved existing regular file .clinerules (skipped creating .clinerules/syntopik.md).");
		} else {
			const clineRule = path.join(clinerulesRoot, "syntopik.md");
			const existedCr = existsSync(clineRule);
			mkdirSync(path.dirname(clineRule), { recursive: true });
			writeFileSync(clineRule, genericRulesMd, "utf8");
			addUnique(written, ".clinerules/syntopik.md");
			if (!existedCr) excludeEntries.push(".clinerules/syntopik.md");
		}

		const clineMcp = resolveClineMcpSettingsPath(homeDir);
		if (writeGlobal) {
			applyJsonMerge(
				clineMcp,
				(doc) => {
					if (!doc.mcpServers || typeof doc.mcpServers !== "object") doc.mcpServers = {};
					// Cline HTTP MUST use camelCase `streamableHttp`!
					doc.mcpServers["syntopik-studio"] = isHttp
						? {
								type: "streamableHttp",
								url: httpUrl,
								headers: { Authorization: "Bearer ${SYNTOPIK_TOKEN}" },
								disabled: false,
								autoApprove: [],
							}
						: {
								command: mcpCommand,
								args: [...mcpArgs],
								disabled: false,
								autoApprove: [],
							};
				},
				clineMcp,
				{ written, preserved, excludeEntries, notes, trackExclude: false },
			);
		} else {
			notes.push(`Cline global MCP config skipped in local-sandbox auto mode (${clineMcp}).`);
		}
	}

	if (selected.includes("roo")) {
		const rooRule = path.join(targetDir, ".roo", "rules", "syntopik.md");
		const existedRr = existsSync(rooRule);
		mkdirSync(path.dirname(rooRule), { recursive: true });
		writeFileSync(rooRule, genericRulesMd, "utf8");
		addUnique(written, ".roo/rules/syntopik.md");
		if (!existedRr) excludeEntries.push(".roo/rules/syntopik.md");

		const rooMcp = path.join(targetDir, ".roo", "mcp.json");
		applyJsonMerge(
			rooMcp,
			(doc) => {
				if (!doc.mcpServers || typeof doc.mcpServers !== "object") doc.mcpServers = {};
				// Roo Code HTTP uses hyphenated `streamable-http`!
				doc.mcpServers["syntopik-studio"] = isHttp
					? {
							type: "streamable-http",
							url: httpUrl,
							headers: { Authorization: "Bearer ${SYNTOPIK_TOKEN}" },
							disabled: false,
							alwaysAllow: [],
						}
					: {
							command: mcpCommand,
							args: [...mcpArgs],
							disabled: false,
							alwaysAllow: [],
						};
			},
			".roo/mcp.json",
			{ written, preserved, excludeEntries, notes },
		);
	}

	if (selected.includes("zed")) {
		const existingPrec = ZED_PRECEDENCE_RULE_FILES.filter((rel) =>
			isRegularFileOrNonDirSymlink(path.join(targetDir, rel)),
		);
		if (existingPrec.length > 0) {
			for (const rel of existingPrec) addUnique(preserved, rel);
			const fbRule = path.join(targetDir, ".agents", "syntopik-rules.md");
			const existedFb = existsSync(fbRule);
			mkdirSync(path.dirname(fbRule), { recursive: true });
			writeFileSync(fbRule, genericRulesMd, "utf8");
			addUnique(written, ".agents/syntopik-rules.md");
			if (!existedFb) excludeEntries.push(".agents/syntopik-rules.md");
			notes.push(
				`Zed uses first-match rules; preserved ${existingPrec.join(", ")} instead of creating .rules (wrote .agents/syntopik-rules.md).`,
			);
		} else {
			const zedRules = path.join(targetDir, ".rules");
			const existedZr = existsSync(zedRules);
			writeFileSync(zedRules, genericRulesMd, "utf8");
			addUnique(written, ".rules");
			if (!existedZr) excludeEntries.push(".rules");
		}

		const zedSettings = path.join(targetDir, ".zed", "settings.json");
		const zedRes = writeJsoncMerged(zedSettings, (doc) => {
			if (!doc.context_servers || typeof doc.context_servers !== "object") doc.context_servers = {};
			doc.context_servers["syntopik-studio"] = isHttp
				? { url: httpUrl, headers: { Authorization: "Bearer ${SYNTOPIK_TOKEN}" } }
				: {
						command: mcpCommand,
						args: [...mcpArgs],
						env: { SYNT_WORKSPACE: vaultPath },
					};
		});
		if (zedRes.merged) {
			addUnique(written, ".zed/settings.json");
			if (!zedRes.existed) excludeEntries.push(".zed/settings.json");
		} else {
			addUnique(preserved, ".zed/settings.json");
			notes.push("Preserved existing .zed/settings.json (could not parse safely as JSON/JSONC).");
		}
	}

	if (selected.includes("aider")) {
		const aiderRule = path.join(targetDir, ".agents", "syntopik-rules.md");
		const existedAr = existsSync(aiderRule);
		mkdirSync(path.dirname(aiderRule), { recursive: true });
		writeFileSync(aiderRule, genericRulesMd, "utf8");
		addUnique(written, ".agents/syntopik-rules.md");
		if (!existedAr) excludeEntries.push(".agents/syntopik-rules.md");

		const aiderCfg = path.join(targetDir, ".aider.conf.yml");
		const existedAc = existsSync(aiderCfg);
		const prevYaml = existedAc ? readFileSync(aiderCfg, "utf8") : "";
		const desiredReads = [];
		if (isFile(path.join(targetDir, "AGENTS.md"))) desiredReads.push("AGENTS.md");
		desiredReads.push(".agents/syntopik-rules.md");
		writeFileSync(aiderCfg, mergeAiderYaml(prevYaml, desiredReads), "utf8");
		addUnique(written, ".aider.conf.yml");
		if (!existedAc) excludeEntries.push(".aider.conf.yml");
		if (isHttp) {
			notes.push("Aider is a rules-only CLI (no MCP client); wired .aider.conf.yml read list.");
		}
	}

	if (selected.includes("continue")) {
		const contRule = path.join(targetDir, ".continue", "rules", "syntopik.md");
		const existedCr = existsSync(contRule);
		mkdirSync(path.dirname(contRule), { recursive: true });
		writeFileSync(
			contRule,
			`---\nname: Syntopik Deep-Mastery Co-Pilot\ndescription: Syntopik universal deep-mastery co-pilot rules and study vault pointer\nalwaysApply: false\n---\n\nFor learning, deep-understanding, review, or exam-prep tasks, activate Syntopik skills in \`.agents/skills/\` and persist study state to \`${vaultDisplay}\`. Keep turns <= 160w prose, 1 active demand/turn, zero sycophancy, and verify math/code deterministically via \`syntopik run <engine>\`.\n`,
			"utf8",
		);
		addUnique(written, ".continue/rules/syntopik.md");
		if (!existedCr) excludeEntries.push(".continue/rules/syntopik.md");

		const contMcp = path.join(targetDir, ".continue", "mcpServers", "syntopik.yaml");
		const existedCm = existsSync(contMcp);
		mkdirSync(path.dirname(contMcp), { recursive: true });
		const yamlBody = isHttp
			? [
					"name: Syntopik Studio",
					"version: 0.0.1",
					"schema: v1",
					"mcpServers:",
					"  - name: syntopik-studio",
					"    type: streamable-http",
					`    url: "${esc(httpUrl)}"`,
					"    requestOptions:",
					"      headers:",
					'        Authorization: "Bearer ${SYNTOPIK_TOKEN}"',
				].join("\n") + "\n"
			: [
					"name: Syntopik Studio",
					"version: 0.0.1",
					"schema: v1",
					"mcpServers:",
					"  - name: syntopik-studio",
					`    command: "${esc(mcpCommand)}"`,
					"    args:",
					...mcpArgs.map((a) => `      - "${esc(a)}"`),
				].join("\n") + "\n";
		writeFileSync(contMcp, yamlBody, "utf8");
		addUnique(written, ".continue/mcpServers/syntopik.yaml");
		if (!existedCm) excludeEntries.push(".continue/mcpServers/syntopik.yaml");
	}

	if (selected.includes("kiro")) {
		const kiroRule = path.join(targetDir, ".kiro", "steering", "syntopik.md");
		const existedKr = existsSync(kiroRule);
		mkdirSync(path.dirname(kiroRule), { recursive: true });
		writeFileSync(
			kiroRule,
			`---\ntitle: Syntopik Deep-Mastery Co-Pilot\ndescription: Syntopik universal deep-mastery co-pilot rules and study vault pointer\ninclusion: manual\n---\n\nFor learning, deep-understanding, review, or exam-prep tasks, activate Syntopik skills in \`.agents/skills/\` and persist study state to \`${vaultDisplay}\`. Keep turns <= 160w prose, 1 active demand/turn, zero sycophancy, and verify math/code deterministically via \`syntopik run <engine>\`.\n`,
			"utf8",
		);
		addUnique(written, ".kiro/steering/syntopik.md");
		if (!existedKr) excludeEntries.push(".kiro/steering/syntopik.md");

		const kiroMcp = path.join(targetDir, ".kiro", "settings", "mcp.json");
		applyJsonMerge(
			kiroMcp,
			(doc) => {
				if (!doc.mcpServers || typeof doc.mcpServers !== "object") doc.mcpServers = {};
				doc.mcpServers["syntopik-studio"] = isHttp
					? {
							url: httpUrl,
							headers: { Authorization: "Bearer ${SYNTOPIK_TOKEN}" },
							disabled: false,
							autoApprove: [],
						}
					: {
							command: mcpCommand,
							args: [...mcpArgs],
							disabled: false,
							autoApprove: [],
						};
			},
			".kiro/settings/mcp.json",
			{ written, preserved, excludeEntries, notes },
		);
	}

	const gitExclude = updateGitExclude(targetDir, excludeEntries);
	const report = {
		ok: true,
		targetDir,
		vaultMode,
		vaultPath,
		transport: transportMode,
		httpUrl: isHttp ? httpUrl : null,
		harnesses: selected,
		ephemeralNpx,
		ephemeralInstallRoot: ephemeralNpx,
		written,
		preserved,
		notes,
		gitExclude,
		vaultInit,
	};
	if (antigravityInfo) report.antigravity = antigravityInfo;

	if (asJson) {
		console.log(JSON.stringify(report, null, 2));
	} else {
		console.log(`Syntopik attached to ${targetDir}`);
		console.log(`  Vault      : ${vaultPath} (${vaultMode})`);
		console.log(`  Transport  : ${transportMode}${isHttp ? ` (${httpUrl})` : ""}`);
		console.log(`  Harnesses  : ${selected.join(", ")}`);
		console.log(`  Written    : ${written.length} adapter(s)`);
		if (preserved.length) console.log(`  Preserved  : ${preserved.join(", ")}`);
		for (const note of notes) console.log(`  Note       : ${note}`);
		if (gitExclude.updated) {
			console.log(`  Git Ignore : updated ${gitExclude.path} (zero diff to .gitignore)`);
		}
	}
	return 0;
}

function runHookNative(argv) {
	const hookName = (argv[0] || "").trim().toLowerCase();
	const hookFiles = {
		"session-start": "session-start.mjs",
		"post-graph-sync": "post-graph-sync.mjs",
	};
	const scriptFile = hookFiles[hookName];
	if (!scriptFile) {
		console.error("Usage: syntopik hook <session-start|post-graph-sync> [--workspace <dir>]");
		return 2;
	}

	const rest = argv.slice(1);
	const wsFlag = parseFlag(rest, "--workspace");
	const hookScript = path.join(PACKAGE_ROOT, ".agents", "hooks", scriptFile);
	if (!existsSync(hookScript)) {
		console.error(`Hook script not found: ${hookScript}`);
		return 1;
	}

	const env = { ...process.env };
	if (wsFlag && wsFlag.trim()) {
		const resolvedFlag = path.resolve(wsFlag.trim());
		const attached = resolveAttachedWorkspace(resolvedFlag);
		env.SYNT_WORKSPACE = attached || resolvedFlag;
	}

	const res = spawnSync(process.execPath, [hookScript, ...rest], {
		stdio: "inherit",
		env,
	});
	return typeof res.status === "number" ? res.status : 1;
}

function findPython() {
	const candidates = [
		path.join(process.cwd(), ".venv", "bin", "python"),
		path.join(process.cwd(), ".venv", "Scripts", "python.exe"),
		path.join(PACKAGE_ROOT, ".venv", "bin", "python"),
		path.join(PACKAGE_ROOT, ".venv", "Scripts", "python.exe"),
	];
	for (const c of candidates) {
		if (existsSync(c)) {
			try {
				if (statSync(c).isFile()) return c;
			} catch {
				// ignore
			}
		}
	}
	for (const name of ["python3", "python"]) {
		const probe = spawnSync(name, ["--version"], { stdio: "ignore" });
		if (probe.status === 0) return name;
	}
	return null;
}

function delegateToPython(argv) {
	const py = findPython();
	if (!py) {
		console.error(
			"Syntopik requires Python 3.12+ for this command (none of .venv/bin/python, python3, or python was found on PATH).",
		);
		return 1;
	}
	const env = {
		...process.env,
		PYTHONDONTWRITEBYTECODE: "1",
		PYTHONPATH: process.env.PYTHONPATH
			? `${PACKAGE_ROOT}${path.delimiter}${process.env.PYTHONPATH}`
			: PACKAGE_ROOT,
	};
	const res = spawnSync(py, ["-B", "-m", "syntopik.cli", ...argv], {
		stdio: "inherit",
		env,
	});
	if (res.error) {
		console.error(`Failed to invoke Python CLI: ${res.error.message}`);
		return 1;
	}
	return typeof res.status === "number" ? res.status : 1;
}

function main() {
	const argv = process.argv.slice(2);
	if (argv.length === 0 || argv[0] === "-h" || argv[0] === "--help" || argv[0] === "help") {
		console.log(HELP_TEXT);
		return 0;
	}
	if (argv[0] === "-v" || argv[0] === "--version") {
		console.log(`syntopik ${VERSION}`);
		return 0;
	}

	const cmd = argv[0];
	const rest = argv.slice(1);

	if (cmd === "sync") {
		const syncScript = path.join(PACKAGE_ROOT, "scripts", "sync-graph.mjs");
		const syncArgs = [...rest];
		const hasWsFlag = Boolean(parseFlag(rest, "--workspace"));
		const hasWsEnv = Boolean(process.env.SYNT_WORKSPACE || process.env.MLH_WORKSPACE);
		if (!hasWsFlag && !hasWsEnv) {
			const resolved = resolveAttachedWorkspace(process.cwd());
			if (resolved) {
				syncArgs.unshift("--workspace", resolved);
			}
		}
		const res = spawnSync(process.execPath, [syncScript, ...syncArgs], { stdio: "inherit" });
		return typeof res.status === "number" ? res.status : 1;
	}

	if (cmd === "mcp") {
		const serverMjs = path.join(PACKAGE_ROOT, "mcp-app", "mlh-studio", "dist", "server.mjs");
		if (!existsSync(serverMjs)) {
			console.error(`Syntopik Studio MCP server not found at ${serverMjs}`);
			return 1;
		}
		const res = spawnSync(process.execPath, [serverMjs, ...rest], { stdio: "inherit" });
		return typeof res.status === "number" ? res.status : 1;
	}

	if (cmd === "hook") {
		return runHookNative(rest);
	}

	if (cmd === "init") {
		const pos = positionalArgs(rest, ["--workspace", "--vault"]);
		const vaultMode = (parseFlag(rest, "--vault") || "").toLowerCase();
		const wsFlag = parseFlag(rest, "--workspace");
		const target =
			wsFlag ||
			pos[0] ||
			(vaultMode === "global"
				? path.join(os.homedir(), ".syntopik")
				: vaultMode === "local"
					? path.join(process.cwd(), ".syntopik")
					: process.cwd());
		const result = ensureLearnerWorkspace(target);
		if (hasFlag(rest, "--json")) {
			console.log(JSON.stringify(result, null, 2));
		} else {
			console.log(`Syntopik initialized: ${result.workspace}`);
			console.log(
				`  Created ${result.createdDirs.length} dir(s), ${result.createdFiles.length} file(s) (0 existing files overwritten).`,
			);
		}
		return 0;
	}

	if (cmd === "attach") {
		return runAttachNative(rest);
	}

	// Explicitly delegate v3.0/v3.1/v3.2 Python engine verbs (rosetta, sync-delta, calibrate-policy, proof-diff, readiness, interleave, doctor --compat, import)
	return delegateToPython(argv);
}

process.exit(main());
