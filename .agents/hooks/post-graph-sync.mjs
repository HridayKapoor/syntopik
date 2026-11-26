#!/usr/bin/env node
import { readFileSync } from "node:fs";
import { execFileSync } from "node:child_process";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
const wsEnv = process.env.SYNT_WORKSPACE || process.env.MLH_WORKSPACE || "";
const root = wsEnv.trim() ? path.resolve(wsEnv.trim()) : repoRoot;

const WATCHED_DIRS = new Set([
	"knowledge-graph",
	"chapters",
	"demos",
	"animations",
	"sources",
	"exams",
]);

const STATIC_IGNORED_BASENAMES = new Set([
	"graph-data.js",
	"viewer.html",
	"reagents.json",
]);

const READONLY_CMD_RE =
	/^\s*(?:grep|rg|cat|head|tail|wc|ls|find|stat|file|shasum|sha256sum|md5|diff|git\s+(?:status|diff|log|show|grep|rev-parse|ls-files)|node\s+--check|python3?\s+-m\s+(?:py_compile|unittest))\b/iu;

const MUTATING_STUDY_SCRIPT_RE =
	/\b(?:srs_engine|chapter_tracker|exam_runner|mistake_analytics|cheatsheet_pdf|audio_brief|setup_harness)\.py\b/iu;

function isExternalTempPath(candidatePath) {
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

function commandTouchesWorkspace(payload) {
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
		/(?:>|>>|\btee\b|\bcp\b|\bmv\b|\btouch\b|\bmkdir\b).*?\b(?:knowledge-graph|chapters|demos|animations|sources|exams)\//iu.test(
			cmd,
		)
	) {
		return true;
	}
	return false;
}

function shouldSyncPath(targetPath) {
	if (!targetPath) return false;
	const normalized = targetPath.replace(/\\/gu, "/");
	const base = path.posix.basename(normalized);

	if (
		STATIC_IGNORED_BASENAMES.has(base) ||
		/-template\.html$/iu.test(base) ||
		normalized.includes("/partial_movie_files/") ||
		isExternalTempPath(targetPath)
	) {
		return false;
	}

	const absCandidate = path.isAbsolute(targetPath)
		? path.resolve(targetPath)
		: path.resolve(root, targetPath);
	const relFromRoot = path.relative(root, absCandidate).replace(/\\/gu, "/");
	if (!relFromRoot.startsWith("..") && !path.isAbsolute(relFromRoot)) {
		const topDir = relFromRoot.split("/")[0];
		return WATCHED_DIRS.has(topDir);
	}

	if (!path.isAbsolute(targetPath)) {
		return /(?:^|\/)(?:knowledge-graph|chapters|demos|animations|sources|exams)(?:\/|$)/u.test(
			normalized,
		);
	}
	return false;
}

try {
	const raw = readFileSync(0, "utf8").trim();
	const payload = raw ? JSON.parse(raw) : {};
	const candidatePaths = extractTargetPaths(payload);
	const needsSync =
		candidatePaths.some((p) => shouldSyncPath(p)) ||
		commandTouchesWorkspace(payload);

	if (needsSync) {
		const syncArgs = [path.join(repoRoot, "scripts", "sync-graph.mjs"), "--quiet"];
		if (wsEnv.trim()) {
			syncArgs.push(`--workspace=${root}`);
		}
		execFileSync(process.execPath, syncArgs, {
			cwd: root,
			stdio: "ignore",
			timeout: 8000,
		});
	}
} catch {
	// Never disrupt the agent loop on a best-effort hub/graph sync.
}

process.stdout.write("{}");
