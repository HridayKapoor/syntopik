#!/usr/bin/env node
/**
 * Syntopik v2.0 Unified CLI (Zero-Dependency Node ESM Wrapper)
 *
 * Natively handles:
 *   init, attach, sync, mcp, --help, --version
 * Cleanly delegates to Python (.venv -> python3 -> python):
 *   serve, import, review, triage, pack, doctor, export, connect, run
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
		return pkg.version || "2.0.0";
	} catch {
		return "2.0.0";
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

Full Studio & Engine Commands (Python 3.12+ stdlib):
  serve [--port 4173] [--host 127.0.0.1] [--lan] [--open] [--workspace <dir>]
      Launch the 2-way local Studio web server (127.0.0.1, optional --lan Pocket QR).
  import <target> [--kind auto|youtube|arxiv|repo|url|notes] [--slug <slug>] [--register-tracker]
      Ingest PDFs, YouTube captions, arXiv papers, GitHub repos, or web articles.
  review [--web] [--limit 25] [--compress] [--bury-siblings]
      Run FIRe-compressed FSRS-6 due review or open Studio Flashcards.
  triage [--hours 48] [--sleep-hours 7] [--chapter <slug>] [--crib-sheet]
      Generate a Pareto 48h exam triage plan and optional 1-page Anti-Mastery crib sheet.
  pack <export|import|inspect|verify> [...]
      Create, verify, or import zero-PII portable Study Packs (.syntopik-pack.zip).
  export <obsidian|pocket|anki|pack> [...]
      Export to Obsidian vault, offline Pocket HTML deck, Anki .apkg, or Study Pack.
  connect <harness> [--bundle studio|full]
      Install or print global plugin/extension configuration for a harness.
  doctor [--core-only] [--json]
      Run workspace, runtime, and packaging health diagnostics.
  run <engine> [args...]
      Execute any bundled Syntopik engine script cleanly from any directory.

Options:
  -h, --help       Show this help message
  -v, --version    Print version
`;

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

function linkOrCopySkills(srcSkills, destSkills, forceCopy) {
	if (existsSync(destSkills)) {
		return { action: "preserved", created: false };
	}
	mkdirSync(path.dirname(destSkills), { recursive: true });
	if (!forceCopy) {
		try {
			symlinkSync(srcSkills, destSkills, process.platform === "win32" ? "junction" : "dir");
			return { action: "symlinked", created: true };
		} catch {
			// Fall back to directory copy on symlink-restricted environments
		}
	}
	cpSync(srcSkills, destSkills, { recursive: true });
	return { action: "copied", created: true };
}

function runAttachNative(argv) {
	const pos = positionalArgs(argv, ["--harness", "--vault"]);
	const targetDir = path.resolve(pos[0] || ".");
	const harnessRaw = (parseFlag(argv, "--harness") || "auto").toLowerCase();
	const vaultMode = (parseFlag(argv, "--vault") || "global").toLowerCase();
	const copySkills = hasFlag(argv, "--copy-skills");
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
		harnessRaw === "all" || harnessRaw === "auto"
			? [...ALL_HARNESSES]
			: [harnessRaw];

	const serverMjs = path.join(PACKAGE_ROOT, "mcp-app", "mlh-studio", "dist", "server.mjs");
	const srcSkills = path.join(PACKAGE_ROOT, ".agents", "skills");
	const hooksDir = path.join(PACKAGE_ROOT, ".agents", "hooks");

	const excludeEntries = [".syntopik/"];
	const written = [];
	const preserved = [];

	// Never touch existing host AGENTS.md or CLAUDE.md
	for (const hostDoc of ["AGENTS.md", "CLAUDE.md"]) {
		if (existsSync(path.join(targetDir, hostDoc))) {
			preserved.push(hostDoc);
		}
	}

	// Link/copy canonical .agents/skills
	if (existsSync(srcSkills)) {
		const destSkills = path.join(targetDir, ".agents", "skills");
		const skRes = linkOrCopySkills(srcSkills, destSkills, copySkills);
		if (skRes.created) {
			written.push(`.agents/skills (${skRes.action})`);
			excludeEntries.push(".agents/skills", ".agents/skills/");
		} else {
			preserved.push(".agents/skills");
		}
	}

	const mcpStdioEntry = {
		type: "stdio",
		command: "node",
		args: [serverMjs, `--workspace=${vaultPath}`],
	};

	if (selected.includes("cursor")) {
		const rulePath = path.join(targetDir, ".cursor", "rules", "syntopik.mdc");
		mkdirSync(path.dirname(rulePath), { recursive: true });
		writeFileSync(
			rulePath,
			`---\ndescription: Syntopik universal deep-mastery co-pilot rules and study vault pointer\nalwaysApply: false\n---\n\nFor learning, deep-understanding, review, or exam-prep tasks, load Syntopik skills from \`.agents/skills/\` and persist knowledge-graph state in \`${vaultDisplay}\`. Keep turns <= 160w prose, 1 active demand/turn, zero sycophancy, and run deterministic verifiers via \`syntopik run <engine>\`.\n`,
			"utf8",
		);
		written.push(".cursor/rules/syntopik.mdc");
		excludeEntries.push(".cursor/rules/syntopik.mdc");

		const cursorMcp = path.join(targetDir, ".cursor", "mcp.json");
		const { existed } = writeJsonMerged(cursorMcp, (doc) => {
			if (!doc.mcpServers || typeof doc.mcpServers !== "object") doc.mcpServers = {};
			doc.mcpServers["syntopik-studio"] = {
				command: "node",
				args: [serverMjs, `--workspace=${vaultPath}`],
			};
		});
		written.push(".cursor/mcp.json");
		if (!existed) excludeEntries.push(".cursor/mcp.json");
	}

	if (selected.includes("windsurf")) {
		const wsRule = path.join(targetDir, ".windsurf", "rules", "syntopik.md");
		mkdirSync(path.dirname(wsRule), { recursive: true });
		writeFileSync(
			wsRule,
			`---\ntrigger: model_decision\ndescription: Syntopik universal deep-mastery co-pilot rules and study vault pointer\n---\n\nFor learning, study, revision, or concept-mastery requests, follow Syntopik skills in \`.agents/skills/\` and persist study state to \`${vaultDisplay}\`. Keep turns <= 160w prose, 1 active demand/turn, zero sycophancy, and verify math/code via \`syntopik run <engine>\`.\n`,
			"utf8",
		);
		written.push(".windsurf/rules/syntopik.md");
		excludeEntries.push(".windsurf/rules/syntopik.md");
	}

	if (selected.includes("copilot")) {
		const ghInst = path.join(targetDir, ".github", "instructions", "syntopik.instructions.md");
		mkdirSync(path.dirname(ghInst), { recursive: true });
		writeFileSync(
			ghInst,
			`---\napplyTo: "**"\n---\n\nWhen asked to teach, probe, review, or verify STEM/CS concepts, follow Syntopik skills in \`.agents/skills/\` (vault: \`${vaultDisplay}\`): <= 160w calm turns, 1 active demand/turn, zero sycophancy, and deterministic checks via \`syntopik run <engine>\`.\n`,
			"utf8",
		);
		written.push(".github/instructions/syntopik.instructions.md");
		excludeEntries.push(".github/instructions/syntopik.instructions.md");

		const vscodeMcp = path.join(targetDir, ".vscode", "mcp.json");
		const { existed } = writeJsonMerged(vscodeMcp, (doc) => {
			if (!doc.servers || typeof doc.servers !== "object") doc.servers = {};
			doc.servers["syntopik-studio"] = {
				type: "stdio",
				command: "node",
				args: [serverMjs, `--workspace=${vaultPath}`],
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
				command: ["node", serverMjs, `--workspace=${vaultPath}`],
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
			const escapedServer = serverMjs.replace(/\\/gu, "\\\\").replace(/"/gu, '\\"');
			const escapedWs = vaultPath.replace(/\\/gu, "\\\\").replace(/"/gu, '\\"');
			const snippet = [
				"[mcp_servers.syntopik-studio]",
				'command = "node"',
				`args = ["${escapedServer}", "--workspace=${escapedWs}"]`,
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
			if (!Array.isArray(doc.hooks.SessionStart)) {
				doc.hooks.SessionStart = [
					{
						hooks: [
							{
								type: "command",
								command: `SYNT_WORKSPACE="${vaultPath}" node "${path.join(hooksDir, "session-start.mjs")}"`,
								timeout: 10,
							},
						],
					},
				];
			}
			if (!Array.isArray(doc.hooks.PostToolUse)) {
				doc.hooks.PostToolUse = [
					{
						matcher: "Write|Edit|MultiEdit|NotebookEdit|Bash",
						hooks: [
							{
								type: "command",
								command: `SYNT_WORKSPACE="${vaultPath}" node "${path.join(hooksDir, "post-graph-sync.mjs")}"`,
								timeout: 10,
							},
						],
					},
				];
			}
		});
		written.push(".claude/settings.json");
		if (!setRes.existed) excludeEntries.push(".claude/settings.json");

		const claudeSkills = path.join(targetDir, ".claude", "skills");
		const agentsSkillsDest = path.join(targetDir, ".agents", "skills");
		if (!existsSync(claudeSkills) && existsSync(agentsSkillsDest)) {
			const relTarget = path.relative(path.dirname(claudeSkills), agentsSkillsDest);
			try {
				symlinkSync(relTarget, claudeSkills, process.platform === "win32" ? "junction" : "dir");
				written.push(".claude/skills (symlinked)");
				excludeEntries.push(".claude/skills");
			} catch {
				// Optional on restricted symlink filesystems
			}
		}
	}

	if (selected.includes("gemini")) {
		const gemSettings = path.join(targetDir, ".gemini", "settings.json");
		const gemRes = writeJsonMerged(gemSettings, (doc) => {
			if (!doc.mcpServers || typeof doc.mcpServers !== "object") doc.mcpServers = {};
			doc.mcpServers["syntopik-studio"] = {
				command: "node",
				args: [serverMjs, `--workspace=${vaultPath}`],
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
		written,
		preserved,
		gitExclude,
		vaultInit,
	};

	if (asJson) {
		console.log(JSON.stringify(report, null, 2));
	} else {
		console.log(`Syntopik attached to ${targetDir}`);
		console.log(`  Vault      : ${vaultPath} (${vaultMode})`);
		console.log(`  Harnesses  : ${selected.join(", ")}`);
		console.log(`  Written    : ${written.length} adapter(s)`);
		if (preserved.length) console.log(`  Preserved  : ${preserved.join(", ")}`);
		if (gitExclude.updated) {
			console.log(`  Git Ignore : updated ${gitExclude.path} (zero diff to .gitignore)`);
		}
	}
	return 0;
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
			const cwd = process.cwd();
			if (existsSync(path.join(cwd, "knowledge-graph"))) {
				syncArgs.unshift("--workspace", cwd);
			} else if (existsSync(path.join(cwd, ".syntopik", "knowledge-graph"))) {
				syncArgs.unshift("--workspace", path.join(cwd, ".syntopik"));
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

	return delegateToPython(argv);
}

process.exit(main());
