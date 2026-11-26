#!/usr/bin/env node
/**
 * Syntopik v3.1 Unified CLI (Zero-Dependency Node ESM Wrapper)
 *
 * Natively handles:
 *   init, attach, sync, mcp, hook, --help, --version
 * Cleanly delegates to Python (.venv -> python3 -> python):
 *   serve, import, review, triage, pack, doctor, export, connect, run,
 *   next, profile, drop, demo, debrief, mode, sunset, pace, board, bench,
 *   rosetta, sync-delta, calibrate-policy, proof-diff
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
		return pkg.version || "3.1.0";
	} catch {
		return "3.1.0";
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
	"cursor",
	"windsurf",
	"copilot",
	"opencode",
	"codex",
	"claude",
	"antigravity",
	"gemini",
];

const DEFAULT_FALLBACK_HARNESSES = ["claude", "codex", "cursor"];

const HELP_TEXT = `Syntopik v${VERSION} — Universal AI Deep-Mastery OS

Usage:
  syntopik <command> [options]

Native Node Commands (zero Python required):
  init [dir] [--vault global|local] [--json]
      Initialize a Syntopik study workspace or ~/.syntopik vault (non-destructive).
  attach <dir> [--harness auto|all|claude|cursor|windsurf|copilot|codex|antigravity|opencode|gemini]
               [--vault global|local] [--copy-skills] [--json]
      Wire Syntopik non-destructively into any repository (zero git diff, preserves AGENTS.md/CLAUDE.md).
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
      Drop mixed sources (PDF, URL, YouTube, repo, notes) with Teacher-First classification.
  demo [--pack raft-consensus|shm-physics|transformers-attention|all] [--open] [--port 4173] [--json]
      Load bundled zero-PII starter packs into a clean demo workspace.
  serve [--port 4173] [--host 127.0.0.1] [--lan] [--open] [--workspace <dir>]
      Launch the 2-way local Studio web server (127.0.0.1, optional --lan Pocket QR).
  import <target> [--kind auto|youtube|arxiv|repo|url|notes] [--slug|--chapter <slug>] [--register-tracker]
      Ingest PDFs, YouTube captions, arXiv papers, GitHub repos, or web articles.
  review [--web] [--tui] [--recover --daily-cap 15] [--resume] [--energy low|normal|deep] [--limit 25]
      Run FIRe-compressed FSRS-6 review, TUI loop, calm recovery, re-entry, or Studio Flashcards.
  triage [--hours 48 | --exam-in 48h] [--sleep-hours 7] [--chapter <slug>] [--crib-sheet]
      Generate a Pareto 48h exam triage plan and optional 1-page Anti-Mastery crib sheet.
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
      Install or print global plugin/extension configuration for a harness.
  doctor [--core-only] [--fix] [--json]
      Run workspace, runtime, and packaging health diagnostics (optional non-destructive --fix).
  run <engine> [args...]
      Execute any bundled Syntopik engine script cleanly from any directory.

Options:
  -h, --help       Show this help message
  -v, --version    Print version
`;

function isEphemeralNpxRoot(rootDir = PACKAGE_ROOT) {
	return /[\\/]_npx[\\/]/i.test(String(rootDir || ""));
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

	const uniqueEntries = [...new Set(entries.filter(Boolean))];
	const block = [beginMarker, ...uniqueEntries, endMarker].join("\n");

	let nextContent;
	const startIdx = existing.indexOf(beginMarker);
	const endIdx = existing.indexOf(endMarker);
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

function readJsonOrEmpty(filePath) {
	if (!existsSync(filePath)) return {};
	try {
		const parsed = JSON.parse(readFileSync(filePath, "utf8"));
		return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : {};
	} catch {
		return {};
	}
}

function writeJsonMerged(filePath, mutator) {
	const existed = existsSync(filePath);
	const data = readJsonOrEmpty(filePath);
	mutator(data);
	mkdirSync(path.dirname(filePath), { recursive: true });
	writeFileSync(filePath, `${JSON.stringify(data, null, 2)}\n`, "utf8");
	return { existed };
}

function linkOrCopyDir(srcDir, destDir, forceCopy) {
	if (existsSync(destDir)) {
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

function detectInstalledHarnesses(targetDir) {
	const homeDir = os.homedir();
	const hasAgyHome = isDir(path.join(homeDir, ".gemini", "antigravity"));
	const checks = [
		["claude", existsSync(path.join(targetDir, ".claude")) || isDir(path.join(homeDir, ".claude"))],
		["codex", existsSync(path.join(targetDir, ".codex")) || isDir(path.join(homeDir, ".codex"))],
		["cursor", existsSync(path.join(targetDir, ".cursor")) || isDir(path.join(homeDir, ".cursor"))],
		[
			"windsurf",
			existsSync(path.join(targetDir, ".windsurf")) ||
				isDir(path.join(homeDir, ".windsurf")) ||
				isDir(path.join(homeDir, ".codeium", "windsurf")),
		],
		[
			"antigravity",
			isFile(path.join(targetDir, ".agents", "hooks.json")) ||
				isFile(path.join(targetDir, "GEMINI.md")) ||
				hasAgyHome,
		],
		[
			"gemini",
			existsSync(path.join(targetDir, ".gemini")) ||
				isFile(path.join(homeDir, ".gemini", "settings.json")) ||
				isDir(path.join(homeDir, ".gemini", "extensions")) ||
				(isDir(path.join(homeDir, ".gemini")) && !hasAgyHome),
		],
		[
			"copilot",
			existsSync(path.join(targetDir, ".vscode")) ||
				existsSync(path.join(targetDir, ".github", "instructions")),
		],
		[
			"opencode",
			isFile(path.join(targetDir, "opencode.json")) ||
				isDir(path.join(homeDir, ".config", "opencode")),
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
	const pos = positionalArgs(argv, ["--harness", "--vault"]);
	const targetDir = path.resolve(pos[0] || ".");
	const harnessRaw = (parseFlag(argv, "--harness") || "auto").toLowerCase();
	const vaultMode = (parseFlag(argv, "--vault") || "global").toLowerCase();
	const copySkillsFlag = hasFlag(argv, "--copy-skills");
	const asJson = hasFlag(argv, "--json");

	if (!["global", "local"].includes(vaultMode)) {
		console.error(`Invalid --vault '${vaultMode}': expected 'global' or 'local'.`);
		return 2;
	}
	const validHarnesses = new Set(["auto", "all", ...ALL_HARNESSES]);
	if (!validHarnesses.has(harnessRaw)) {
		console.error(`Invalid --harness '${harnessRaw}'.`);
		return 2;
	}

	mkdirSync(targetDir, { recursive: true });

	const vaultPath =
		vaultMode === "local"
			? path.join(targetDir, ".syntopik")
			: path.join(os.homedir(), ".syntopik");
	const vaultDisplay = vaultMode === "local" ? ".syntopik/" : "~/.syntopik/";
	const vaultInit = ensureLearnerWorkspace(vaultPath);

	const selected =
		harnessRaw === "all"
			? [...ALL_HARNESSES]
			: harnessRaw === "auto"
				? detectInstalledHarnesses(targetDir)
				: [harnessRaw];

	const ephemeralNpx = isEphemeralNpxRoot(PACKAGE_ROOT);
	const effectiveCopySkills = copySkillsFlag || ephemeralNpx;

	const serverMjs = path.resolve(PACKAGE_ROOT, "mcp-app", "mlh-studio", "dist", "server.mjs");
	const srcSkills = path.resolve(PACKAGE_ROOT, ".agents", "skills");
	const hooksDir = path.resolve(PACKAGE_ROOT, ".agents", "hooks");

	// Never bake ephemeral ~/.npm/_npx/<hash>/ paths into repo configs!
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

	const excludeEntries = [".syntopik/"];
	const written = [];
	const preserved = [];
	const notes = [];

	// Never touch existing host AGENTS.md or CLAUDE.md
	for (const hostDoc of ["AGENTS.md", "CLAUDE.md"]) {
		if (existsSync(path.join(targetDir, hostDoc))) {
			preserved.push(hostDoc);
		}
	}

	// Link/copy canonical .agents/skills (always copy when running from ephemeral _npx)
	if (existsSync(srcSkills)) {
		const destSkills = path.join(targetDir, ".agents", "skills");
		const skRes = linkOrCopyDir(srcSkills, destSkills, effectiveCopySkills);
		if (skRes.created) {
			written.push(`.agents/skills (${skRes.action})`);
			excludeEntries.push(".agents/skills", ".agents/skills/");
		} else {
			preserved.push(".agents/skills");
		}
	}

	const mcpStdioEntry = {
		type: "stdio",
		command: mcpCommand,
		args: [...mcpArgs],
	};

	if (selected.includes("cursor")) {
		const rulePath = path.join(targetDir, ".cursor", "rules", "syntopik.mdc");
		const existedRule = existsSync(rulePath);
		mkdirSync(path.dirname(rulePath), { recursive: true });
		writeFileSync(
			rulePath,
			`---\ndescription: Syntopik universal deep-mastery co-pilot rules and study vault pointer\nalwaysApply: false\n---\n\nFor learning, deep-understanding, review, or exam-prep tasks, load Syntopik skills from \`.agents/skills/\` and persist knowledge-graph state in \`${vaultDisplay}\`. Keep turns <= 160w prose, 1 active demand/turn, zero sycophancy, and run deterministic verifiers via \`syntopik run <engine>\`.\n`,
			"utf8",
		);
		written.push(".cursor/rules/syntopik.mdc");
		if (!existedRule) excludeEntries.push(".cursor/rules/syntopik.mdc");

		const cursorMcp = path.join(targetDir, ".cursor", "mcp.json");
		const { existed } = writeJsonMerged(cursorMcp, (doc) => {
			if (!doc.mcpServers || typeof doc.mcpServers !== "object") doc.mcpServers = {};
			doc.mcpServers["syntopik-studio"] = {
				command: mcpCommand,
				args: [...mcpArgs],
			};
		});
		written.push(".cursor/mcp.json");
		if (!existed) excludeEntries.push(".cursor/mcp.json");
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
		written.push(".windsurf/rules/syntopik.md");
		if (!existedWs) excludeEntries.push(".windsurf/rules/syntopik.md");
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
		written.push(".github/instructions/syntopik.instructions.md");
		if (!existedGh) excludeEntries.push(".github/instructions/syntopik.instructions.md");

		const vscodeMcp = path.join(targetDir, ".vscode", "mcp.json");
		const { existed } = writeJsonMerged(vscodeMcp, (doc) => {
			if (!doc.servers || typeof doc.servers !== "object") doc.servers = {};
			doc.servers["syntopik-studio"] = {
				type: "stdio",
				command: mcpCommand,
				args: [...mcpArgs],
				cwd: "${workspaceFolder}",
			};
		});
		written.push(".vscode/mcp.json");
		if (!existed) excludeEntries.push(".vscode/mcp.json");
	}

	if (selected.includes("opencode")) {
		const ocPath = path.join(targetDir, "opencode.json");
		const { existed } = writeJsonMerged(ocPath, (doc) => {
			if (!doc.mcp || typeof doc.mcp !== "object") doc.mcp = {};
			doc.mcp["syntopik-studio"] = {
				type: "local",
				command: [mcpCommand, ...mcpArgs],
				environment: { SYNT_WORKSPACE: vaultPath },
				enabled: true,
			};
		});
		written.push("opencode.json");
		if (!existed) excludeEntries.push("opencode.json");
	}

	if (selected.includes("codex")) {
		const codexToml = path.join(targetDir, ".codex", "config.toml");
		const existed = existsSync(codexToml);
		const prev = existed ? readFileSync(codexToml, "utf8") : "";
		if (!prev.includes("[mcp_servers.syntopik-studio]")) {
			mkdirSync(path.dirname(codexToml), { recursive: true });
			const esc = (s) => String(s).replace(/\\/gu, "\\\\").replace(/"/gu, '\\"');
			const argsList = mcpArgs.map((a) => `"${esc(a)}"`).join(", ");
			const snippet = [
				"[mcp_servers.syntopik-studio]",
				`command = "${esc(mcpCommand)}"`,
				`args = [${argsList}]`,
				"startup_timeout_sec = 10",
				"tool_timeout_sec = 60",
			].join("\n");
			const next = prev.trim() ? `${prev.replace(/\s+$/u, "")}\n\n${snippet}\n` : `${snippet}\n`;
			writeFileSync(codexToml, next, "utf8");
		}
		written.push(".codex/config.toml");
		if (!existed) excludeEntries.push(".codex/config.toml");
	}

	if (selected.includes("claude")) {
		const rootMcp = path.join(targetDir, ".mcp.json");
		const mcpRes = writeJsonMerged(rootMcp, (doc) => {
			if (!doc.mcpServers || typeof doc.mcpServers !== "object") doc.mcpServers = {};
			doc.mcpServers["syntopik-studio"] = mcpStdioEntry;
		});
		written.push(".mcp.json");
		if (!mcpRes.existed) excludeEntries.push(".mcp.json");

		const claudeSettings = path.join(targetDir, ".claude", "settings.json");
		const setRes = writeJsonMerged(claudeSettings, (doc) => {
			if (!doc.hooks || typeof doc.hooks !== "object") doc.hooks = {};
			if (!Array.isArray(doc.hooks.SessionStart)) doc.hooks.SessionStart = [];
			if (!Array.isArray(doc.hooks.PostToolUse)) doc.hooks.PostToolUse = [];

			appendDedupHook(
				doc.hooks.SessionStart,
				{
					hooks: [
						{
							type: "command",
							command: sessionHookCmd,
							timeout: 10,
						},
					],
				},
				sessionHookCmd,
			);
			appendDedupHook(
				doc.hooks.PostToolUse,
				{
					matcher: "Write|Edit|MultiEdit|NotebookEdit|Bash",
					hooks: [
						{
							type: "command",
							command: postHookCmd,
							timeout: 10,
						},
					],
				},
				postHookCmd,
			);
		});
		written.push(".claude/settings.json");
		if (!setRes.existed) excludeEntries.push(".claude/settings.json");

		const claudeSkills = path.join(targetDir, ".claude", "skills");
		const agentsSkillsDest = path.join(targetDir, ".agents", "skills");
		if (existsSync(agentsSkillsDest)) {
			const cRes = linkOrCopyDir(path.resolve(agentsSkillsDest), claudeSkills, effectiveCopySkills);
			if (cRes.created) {
				written.push(`.claude/skills (${cRes.action})`);
				excludeEntries.push(".claude/skills", ".claude/skills/");
			} else {
				preserved.push(".claude/skills");
			}
		}
	}

	let antigravityInfo = null;
	if (selected.includes("antigravity")) {
		const agyHooks = path.join(targetDir, ".agents", "hooks.json");
		const agyRes = writeJsonMerged(agyHooks, (doc) => {
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
		});
		written.push(".agents/hooks.json");
		if (!agyRes.existed) excludeEntries.push(".agents/hooks.json");

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
		const gemRes = writeJsonMerged(gemSettings, (doc) => {
			if (!doc.mcpServers || typeof doc.mcpServers !== "object") doc.mcpServers = {};
			doc.mcpServers["syntopik-studio"] = {
				command: mcpCommand,
				args: [...mcpArgs],
			};
		});
		written.push(".gemini/settings.json");
		if (!gemRes.existed) excludeEntries.push(".gemini/settings.json");
	}

	const gitExclude = updateGitExclude(targetDir, excludeEntries);
	const report = {
		ok: true,
		targetDir,
		vaultMode,
		vaultPath,
		harnesses: selected,
		ephemeralNpx,
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

	// Explicitly delegate v3.0/v3.1 Python engine verbs (including rosetta, sync-delta, calibrate-policy, proof-diff)
	return delegateToPython(argv);
}

process.exit(main());
