#!/usr/bin/env node
import { accessSync, constants, existsSync, readFileSync, statSync } from "node:fs";
import { execFileSync } from "node:child_process";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const hookDir = path.dirname(fileURLToPath(import.meta.url));
const packageRoot = existsSync(path.join(path.resolve(hookDir, ".."), "scripts", "sync-graph.mjs"))
	? path.resolve(hookDir, "..")
	: path.resolve(hookDir, "..", "..");

function isPluginCachePath(dirPath) {
	if (!dirPath) return false;
	const norm = path.resolve(dirPath).replace(/\\/gu, "/");
	return (
		norm.includes("/.claude/plugins/cache/") ||
		norm.includes("/.claude/plugins/marketplaces/") ||
		norm.includes("/.gemini/extensions/") ||
		norm.includes("/.gemini/config/plugins/") ||
		norm.includes("/.codex/plugins/") ||
		norm.includes("/node_modules/")
	);
}

function isWritableDir(dirPath) {
	try {
		if (!existsSync(dirPath)) return false;
		accessSync(dirPath, constants.W_OK);
		return true;
	} catch {
		return false;
	}
}

function resolveAttachedOrDirect(dirPath) {
	if (!dirPath || typeof dirPath !== "string" || !dirPath.trim()) return null;
	const abs = path.resolve(dirPath.trim());
	if (!existsSync(abs)) return null;
	if (existsSync(path.join(abs, "knowledge-graph"))) return abs;
	const dotSynt = path.join(abs, ".syntopik");
	if (existsSync(dotSynt)) {
		try {
			if (statSync(dotSynt).isDirectory()) return dotSynt;
		} catch {
			return null;
		}
		return abs;
	}
	return null;
}

function resolveWorkspaceRoot(payload) {
	const wsEnv = (process.env.SYNT_WORKSPACE || process.env.MLH_WORKSPACE || "").trim();
	if (wsEnv) return path.resolve(wsEnv);

	const candidates = [
		process.env.CLAUDE_PROJECT_DIR || "",
		Array.isArray(payload?.workspacePaths) ? payload.workspacePaths[0] : "",
		typeof payload?.cwd === "string" ? payload.cwd : "",
		process.cwd(),
	];
	for (const cand of candidates) {
		const hit = resolveAttachedOrDirect(cand);
		if (hit && !isPluginCachePath(hit)) return hit;
	}

	if (!isPluginCachePath(packageRoot) && isWritableDir(packageRoot)) {
		return packageRoot;
	}
	return path.join(os.homedir(), ".syntopik");
}

function findSyncScript(workspaceRoot) {
	const candidates = [
		path.join(workspaceRoot, "scripts", "sync-graph.mjs"),
		path.join(packageRoot, "scripts", "sync-graph.mjs"),
		path.resolve(hookDir, "..", "scripts", "sync-graph.mjs"),
		path.resolve(hookDir, "..", "..", "scripts", "sync-graph.mjs"),
	];
	if (process.env.CLAUDE_PLUGIN_ROOT) {
		candidates.push(path.join(process.env.CLAUDE_PLUGIN_ROOT, "scripts", "sync-graph.mjs"));
	}
	return candidates.find((p) => existsSync(p)) || null;
}

const WATCHED_DIRS = new Set([
	"knowledge-graph",
	"chapters",
	"demos",
	"animations",
	"sources",
	"exams",
]);

const WATCHED_BASENAMES = new Set([
	"propositions.jsonl",
	"chains.jsonl",
	"mistakes.jsonl",
	"revlog.jsonl",
	"reading-queue.jsonl",
	"exam-attempts.jsonl",
	"harness-log.jsonl",
]);

const STATIC_IGNORED_BASENAMES = new Set([
	"graph-data.js",
	"viewer.html",
	"reagents.json",
]);

const READONLY_CMD_RE =
	/^\s*(?:grep|rg|cat|head|tail|wc|ls|find|stat|file|shasum|sha256sum|md5|diff|git\s+(?:status|diff|log|show|grep|rev-parse|ls-files)|node\s+--check|python3?\s+-m\s+(?:py_compile|unittest))\b/iu;

const MUTATING_STUDY_SCRIPT_RE =
	/\b(?:srs_engine|chapter_tracker|exam_runner|mistake_analytics|cheatsheet_pdf|audio_brief|setup_harness|ingest_source|vault_sync|mnemonic_sync)\.py\b/iu;

function isExternalTempPath(candidatePath, root) {
	const abs = path.isAbsolute(candidatePath)
		? path.resolve(candidatePath)
		: path.resolve(root, candidatePath);
	if (abs === root || abs.startsWith(root + path.sep)) {
		return false;
	}
	const sysTmp = path.resolve(os.tmpdir());
	return (
		abs === sysTmp ||
		abs.startsWith(sysTmp + path.sep) ||
		abs.startsWith("/tmp/") ||
		abs.startsWith("/private/tmp/") ||
		abs.startsWith("/var/folders/")
	);
}

function extractTargetPaths(payload) {
	const paths = [];
	const args =
		payload?.toolCall?.args ??
		payload?.tool_input ??
		payload?.args ??
		payload ??
		{};

	const directKeys = [
		"Path",
		"path",
		"TargetFile",
		"targetFile",
		"target_file",
		"file_path",
		"filePath",
		"notebook_path",
		"Destination",
		"outputPath",
	];
	for (const key of directKeys) {
		if (typeof args?.[key] === "string" && args[key].trim()) {
			paths.push(args[key].trim());
		}
	}

	if (Array.isArray(args?.edits)) {
		for (const edit of args.edits) {
			const fp = edit?.file_path || edit?.path || edit?.Path;
			if (typeof fp === "string" && fp.trim()) {
				paths.push(fp.trim());
			}
		}
	}

	const toolName = String(
		payload?.tool_name ?? payload?.toolCall?.name ?? payload?.toolName ?? "",
	).trim();

	const patchBlob = [
		typeof args === "string" ? args : "",
		typeof args?.patch === "string" ? args.patch : "",
		typeof args?.input === "string" ? args.input : "",
		typeof args?.diff === "string" ? args.diff : "",
		typeof args?.cmd === "string" ? args.cmd : "",
		typeof args?.command === "string" ? args.command : "",
	]
		.filter(Boolean)
		.join("\n");

	if (toolName === "apply_patch" || patchBlob.includes("*** Begin Patch")) {
		const patchRe = /^\*\*\* (?:Add|Update|Delete) File:\s*(.+)$/gmu;
		let match;
		while ((match = patchRe.exec(patchBlob)) !== null) {
			const p = match[1].trim();
			if (p) paths.push(p);
		}
	}

	return paths;
}

function commandTouchesWorkspace(payload, root) {
	const args = payload?.toolCall?.args ?? payload?.tool_input ?? payload?.args ?? {};
	const cmd = String(
		args?.command ?? args?.CommandLine ?? args?.cmd ?? "",
	).trim();
	if (!cmd || READONLY_CMD_RE.test(cmd)) {
		return false;
	}
	// Ignore commands explicitly targeting isolated /tmp sandboxes outside root.
	if (
		/(?:^|\s|["'=])(?:\/tmp\/|\/private\/tmp\/|\$TMPDIR)/u.test(cmd) &&
		!cmd.includes(root)
	) {
		return false;
	}
	if (MUTATING_STUDY_SCRIPT_RE.test(cmd)) {
		return true;
	}
	if (
		/(?:>|>>|\btee\b|\bcp\b|\bmv\b|\btouch\b|\bmkdir\b).*?(?:\b(?:knowledge-graph|chapters|demos|animations|sources|exams)\/|\b(?:revlog|reading-queue|propositions|chains|mistakes)\.jsonl\b)/iu.test(
			cmd,
		)
	) {
		return true;
	}
	return false;
}

function shouldSyncPath(targetPath, root) {
	if (!targetPath) return false;
	const normalized = targetPath.replace(/\\/gu, "/");
	const base = path.posix.basename(normalized);

	if (
		STATIC_IGNORED_BASENAMES.has(base) ||
		/-template\.html$/iu.test(base) ||
		normalized.includes("/partial_movie_files/") ||
		isExternalTempPath(targetPath, root)
	) {
		return false;
	}

	const absCandidate = path.isAbsolute(targetPath)
		? path.resolve(targetPath)
		: path.resolve(root, targetPath);
	const relFromRoot = path.relative(root, absCandidate).replace(/\\/gu, "/");
	if (!relFromRoot.startsWith("..") && !path.isAbsolute(relFromRoot)) {
		const parts = relFromRoot.split("/");
		const topDir = parts[0] === ".syntopik" && parts.length > 1 ? parts[1] : parts[0];
		if (WATCHED_DIRS.has(topDir) || WATCHED_BASENAMES.has(base)) {
			return true;
		}
	}

	if (!path.isAbsolute(targetPath)) {
		if (WATCHED_BASENAMES.has(base)) return true;
		return /(?:^|\/)(?:\.syntopik\/)?(?:knowledge-graph|chapters|demos|animations|sources|exams)(?:\/|$)/u.test(
			normalized,
		);
	}
	return false;
}

try {
	const raw = readFileSync(0, "utf8").trim();
	const payload = raw ? JSON.parse(raw) : {};
	const root = resolveWorkspaceRoot(payload);
	const customKgEnv = (process.env.SYNT_KG_DIR || process.env.MLH_KG_DIR || "").trim();
	const customChaptersEnv = (process.env.SYNT_CHAPTERS_DIR || process.env.MLH_CHAPTERS_DIR || "").trim();
	const kgDir = customKgEnv ? path.resolve(customKgEnv) : path.join(root, "knowledge-graph");

	const candidatePaths = extractTargetPaths(payload);
	const needsSync =
		candidatePaths.some((p) => shouldSyncPath(p, root)) ||
		commandTouchesWorkspace(payload, root);

	const canWrite =
		!isPluginCachePath(root) &&
		(existsSync(kgDir) ? isWritableDir(kgDir) : existsSync(root) && isWritableDir(root));

	if (needsSync && canWrite) {
		const syncScript = findSyncScript(root);
		if (syncScript) {
			const syncArgs = [syncScript, "--quiet", `--workspace=${root}`];
			if (customKgEnv) syncArgs.push(`--kg-dir=${kgDir}`);
			if (customChaptersEnv) syncArgs.push(`--chapters-dir=${path.resolve(customChaptersEnv)}`);
			execFileSync(process.execPath, syncArgs, {
				cwd: root,
				stdio: "ignore",
				timeout: 8000,
			});
		}
	}
} catch {
	// Never disrupt the agent loop on a best-effort hub/graph sync.
}

process.stdout.write("{}");
