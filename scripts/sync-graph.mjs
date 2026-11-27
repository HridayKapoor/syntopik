#!/usr/bin/env node
import { existsSync, mkdirSync, readdirSync, readFileSync, realpathSync, statSync, writeFileSync } from "node:fs";
import { execFileSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { inflateRawSync } from "node:zlib";

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const rawArgv = process.argv.slice(2);

function getArgValue(flag) {
	const idx = rawArgv.indexOf(flag);
	if (idx !== -1 && idx + 1 < rawArgv.length && !rawArgv[idx + 1].startsWith("--")) {
		return rawArgv[idx + 1];
	}
	const prefix = `${flag}=`;
	const hit = rawArgv.find((a) => a.startsWith(prefix));
	return hit ? hit.slice(prefix.length) : null;
}

const workspaceOverride =
	getArgValue("--workspace") || process.env.SYNT_WORKSPACE || process.env.MLH_WORKSPACE || null;
const root = workspaceOverride ? path.resolve(workspaceOverride) : repoRoot;
const kgDirOverride =
	getArgValue("--kg-dir") || process.env.SYNT_KG_DIR || process.env.MLH_KG_DIR || null;
const kgDir = kgDirOverride ? path.resolve(kgDirOverride) : path.join(root, "knowledge-graph");
const chaptersDirOverride =
	getArgValue("--chapters-dir") || process.env.SYNT_CHAPTERS_DIR || process.env.MLH_CHAPTERS_DIR || null;
const chaptersDir = chaptersDirOverride ? path.resolve(chaptersDirOverride) : path.join(root, "chapters");
const outOverride = getArgValue("--out");
const outFile = outOverride ? path.resolve(outOverride) : path.join(kgDir, "graph-data.js");

const MAX_MD_CONTENT_BYTES = 150 * 1024;

const IGNORED_DIRS = new Set([
	".git",
	".venv",
	"node_modules",
	"__pycache__",
	"partial_movie_files",
	"Tex",
	"texts",
	"_extracted",
]);

function toPosix(p) {
	return p.split(path.sep).join("/");
}

function readJsonl(file) {
	if (!existsSync(file)) return [];
	return readFileSync(file, "utf8")
		.split(/\r?\n/u)
		.map((line) => line.trim())
		.filter(Boolean)
		.map((line) => {
			try {
				return JSON.parse(line);
			} catch {
				return null;
			}
		})
		.filter((item) => item && typeof item === "object" && !Array.isArray(item));
}

const STEM_ACRONYMS = new Map([
	["shm", "SHM"],
	["lcr", "LCR"],
	["rlc", "RLC"],
	["rc", "RC"],
	["rl", "RL"],
	["ac", "AC"],
	["dc", "DC"],
	["sn1", "SN1"],
	["sn2", "SN2"],
	["e1", "E1"],
	["e2", "E2"],
	["jee", "JEE"],
	["nta", "NTA"],
	["cbt", "CBT"],
	["mcq", "MCQ"],
	["mcqs", "MCQs"],
	["pyq", "PYQ"],
	["pyqs", "PYQs"],
	["3d", "3D"],
	["2d", "2D"],
	["pdf", "PDF"],
	["iupac", "IUPAC"],
	["vsepr", "VSEPR"],
	["nmr", "NMR"],
	["fsrs", "FSRS"],
	["bkt", "BKT"],
	["ode", "ODE"],
	["odes", "ODEs"],
	["ph", "pH"],
	["pka", "pKa"],
	["emf", "EMF"],
	["cs", "CS"],
	["ml", "ML"],
	["ai", "AI"],
	["nlp", "NLP"],
	["llm", "LLM"],
	["sql", "SQL"],
	["api", "API"],
	["ast", "AST"],
	["dag", "DAG"],
	["cpu", "CPU"],
	["gpu", "GPU"],
	["tcp", "TCP"],
	["http", "HTTP"],
]);

function prettifyName(filename) {
	const base = path.basename(filename, path.extname(filename));
	return base
		.replace(/[-_]+/gu, " ")
		.replace(/\s+/gu, " ")
		.trim()
		.split(" ")
		.map((word) => {
			const hit = STEM_ACRONYMS.get(word.toLowerCase());
			if (hit) return hit;
			return word.charAt(0).toUpperCase() + word.slice(1);
		})
		.join(" ");
}

function extractMarkdownMeta(fullPath) {
	try {
		const raw = readFileSync(fullPath, "utf8");
		const lines = raw.split(/\r?\n/u);
		let title = "";
		let excerpt = "";
		for (const rawLine of lines) {
			const line = rawLine.trim();
			if (!line) continue;
			if (!title && /^#\s+/.test(line)) {
				title = line.replace(/^#\s+/, "").trim();
				continue;
			}
			if (!excerpt && !line.startsWith("#") && !line.startsWith(">") && !line.startsWith("---") && !line.startsWith("```")) {
				excerpt = line
					.replace(/\[([^\]]+)\]\([^)]+\)/gu, "$1")
					.replace(/[*_`~]/gu, "")
					.slice(0, 190);
				if (line.length > 190) excerpt += "…";
			}
			if (title && excerpt) break;
		}
		const wordCount = raw.trim() ? raw.trim().split(/\s+/u).length : 0;
		const byteLen = Buffer.byteLength(raw, "utf8");
		let content = raw;
		let contentTruncated = false;
		if (byteLen > MAX_MD_CONTENT_BYTES) {
			content = Buffer.from(raw, "utf8").subarray(0, MAX_MD_CONTENT_BYTES).toString("utf8");
			const lastNl = content.lastIndexOf("\n");
			if (lastNl > MAX_MD_CONTENT_BYTES * 0.7) {
				content = content.slice(0, lastNl);
			}
			contentTruncated = true;
		}
		return { title, excerpt, wordCount, content, contentTruncated };
	} catch {
		return { title: "", excerpt: "", wordCount: 0, content: "", contentTruncated: false };
	}
}

function extractHtmlMeta(fullPath) {
	try {
		const head = readFileSync(fullPath, "utf8").slice(0, 12000);
		const titleMatch = head.match(/<title[^>]*>([\s\S]*?)<\/title>/iu);
		const title = titleMatch ? titleMatch[1].replace(/\s+/gu, " ").trim() : "";
		const is3d = /3dmol|three\.js|three\.min|conformer|molecule|smiles|pdb/iu.test(head);
		return { title, is3d };
	} catch {
		return { title: "", is3d: false };
	}
}

function walkFiles(baseDir, opts = {}) {
	const { extensions = null, excludeDirs = IGNORED_DIRS, relPrefix = null } = opts;
	if (!existsSync(baseDir)) return [];
	const out = [];

	function visit(currentDir) {
		let entries;
		try {
			entries = readdirSync(currentDir, { withFileTypes: true });
		} catch {
			return;
		}
		for (const entry of entries) {
			if (entry.name.startsWith(".")) continue;
			const full = path.join(currentDir, entry.name);
			if (entry.isDirectory()) {
				if (excludeDirs.has(entry.name)) continue;
				visit(full);
			} else if (entry.isFile()) {
				const ext = path.extname(entry.name).toLowerCase().replace(/^\./u, "");
				if (extensions && !extensions.has(ext)) continue;
				try {
					const st = statSync(full);
					let relPath = toPosix(path.relative(root, full));
					if (relPrefix && (relPath.startsWith("..") || path.isAbsolute(relPath))) {
						relPath = toPosix(path.posix.join(relPrefix, toPosix(path.relative(baseDir, full))));
					}
					const folder = toPosix(path.relative(baseDir, currentDir));
					out.push({
						name: entry.name,
						relPath,
						folder: folder === "." ? "" : folder,
						ext,
						sizeBytes: st.size,
						mtimeIso: st.mtime.toISOString(),
						fullPath: full,
					});
				} catch {
					// Skip unreadable file.
				}
			}
		}
	}

	visit(baseDir);
	out.sort((a, b) => b.mtimeIso.localeCompare(a.mtimeIso) || a.relPath.localeCompare(b.relPath));
	return out;
}

const DEFAULT_MILESTONES = {
	sourcesIndexed: false,
	probed: false,
	theoryComplete: false,
	questionsComplete: false,
	revisionSheetBuilt: false,
	cbtMockBuilt: false,
	cbtMockPassed: false,
	audioBriefBuilt: false,
};

function isJeeTracker(tracker) {
	const scopeProfile = String(
		tracker?.scopeCharter?.goalProfile ??
			tracker?.goalProfile ??
			tracker?.profile ??
			tracker?.config?.goalProfile ??
			"",
	)
		.trim()
		.toLowerCase();
	if (scopeProfile === "jee") return true;
	if (scopeProfile && scopeProfile !== "exam") return false;
	const sources = Array.isArray(tracker?.sources) ? tracker.sources : [];
	if (
		sources.some(
			(s) =>
				s?.kind === "pyq_bank" ||
				/\b(?:jee|nta|eduniti|pyq)\b/iu.test(`${s?.label || ""} ${s?.path || ""}`),
		)
	) {
		return true;
	}
	return /\b(?:jee|nta|iit)\b/iu.test(
		`${tracker?.title || ""} ${tracker?.goal || ""} ${tracker?.examTarget || ""}`,
	);
}

function computeTrackerNextHint(tracker, topics, questions, milestones) {
	const doubts = Array.isArray(tracker.doubtStack) ? tracker.doubtStack : [];
	if (doubts.length > 0) {
		const top = doubts[doubts.length - 1];
		const q = typeof top === "string" ? top : top?.query || "Open doubt";
		return `Resolve open doubt: ${q}`;
	}
	const sources = Array.isArray(tracker.sources) ? tracker.sources : [];
	if (sources.length > 0 && !milestones.sourcesIndexed) {
		return `Index ${sources.length} chapter source file${sources.length === 1 ? "" : "s"}`;
	}
	if (!milestones.probed) {
		return "Run Phase 1 diagnostic frontier probe";
	}
	const inProgTopic = topics.find((t) => t.status === "in_progress");
	if (inProgTopic) {
		return `Teach topic: ${inProgTopic.title || inProgTopic.id}`;
	}
	const masteredIds = new Set(topics.filter((t) => t.status === "mastered").map((t) => t.id));
	const stepCounter = Number.isFinite(tracker.stepCounter) ? tracker.stepCounter : 0;
	const retestGap = Number.isFinite(tracker?.config?.retestGap) ? tracker.config.retestGap : 3;

	const dueRetest = questions.find(
		(q) =>
			q.status === "retest" &&
			(!q.topicId || masteredIds.has(q.topicId)) &&
			(q.lastStep == null || stepCounter - q.lastStep >= retestGap),
	);
	if (dueRetest) {
		return `Retest question: ${dueRetest.label || dueRetest.qid}`;
	}
	const nextQ = questions.find(
		(q) =>
			(q.status === "unseen" || q.status === "in_progress" || !q.status) &&
			(!q.topicId || masteredIds.has(q.topicId)),
	);
	if (nextQ) {
		return `Solve question: ${nextQ.label || nextQ.qid}`;
	}
	const readyTopic = topics.find((t) => {
		if (t.status === "mastered") return false;
		const prereqs = Array.isArray(t.prereqTopicIds) ? t.prereqTopicIds : [];
		return prereqs.every((pid) => masteredIds.has(pid));
	});
	if (readyTopic) {
		return `Teach topic: ${readyTopic.title || readyTopic.id}`;
	}
	const remQ = questions.find((q) => ["unseen", "in_progress", "retest"].includes(q.status || "unseen"));
	if (remQ) {
		return `${remQ.status === "retest" ? "Retest" : "Solve"}: ${remQ.label || remQ.qid}`;
	}
	const jeeMode = isJeeTracker(tracker);
	if (!milestones.revisionSheetBuilt) {
		return jeeMode
			? "Compile 1-page Typst formula & trap sheet"
			: "Compile 1-page Typst mastery & pitfall sheet";
	}
	if (!milestones.cbtMockPassed) {
		return jeeMode
			? "Build & pass chapter NTA CBT mock"
			: "Build & pass timed mastery assessment";
	}
	return "Chapter mastered!";
}

function scanChapterTrackers() {
	const files = walkFiles(chaptersDir, {
		extensions: new Set(["json"]),
		relPrefix: "chapters",
	}).filter((item) => /-tracker\.json$/iu.test(item.name));

	const trackers = [];
	for (const item of files) {
		let raw;
		try {
			raw = JSON.parse(readFileSync(item.fullPath, "utf8"));
		} catch {
			continue;
		}
		if (!raw || typeof raw !== "object" || Array.isArray(raw)) continue;

		const slug =
			typeof raw.slug === "string" && raw.slug.trim()
				? raw.slug.trim()
				: item.name.replace(/-tracker\.json$/iu, "");
		const title =
			typeof raw.title === "string" && raw.title.trim()
				? raw.title.trim()
				: prettifyName(slug);
		const subject =
			typeof raw.subject === "string" && raw.subject.trim()
				? raw.subject.trim()
				: "General";
		const active = Boolean(raw.active);

		const rawTopics = Array.isArray(raw.topics) ? raw.topics : [];
		const topics = rawTopics
			.filter((t) => t && typeof t === "object")
			.map((t, idx) => ({
				id: String(t.id || `topic-${idx + 1}`),
				order: typeof t.order === "number" && Number.isFinite(t.order) ? t.order : idx + 1,
				title: String(t.title || t.id || `Topic ${idx + 1}`),
				level: String(t.level || "basics"),
				status: String(t.status || "pending"),
				prereqTopicIds: Array.isArray(t.prereqTopicIds) ? t.prereqTopicIds.map(String) : [],
				propIds: Array.isArray(t.propIds) ? t.propIds.map(String) : [],
			}))
			.sort((a, b) => a.order - b.order);

		const rawQuestions = Array.isArray(raw.questions) ? raw.questions : [];
		const questions = rawQuestions.filter((q) => q && typeof q === "object");

		const topicsTotal = topics.length;
		const topicsMastered = topics.filter((t) => t.status === "mastered").length;
		const topicsInProgress = topics.filter((t) => t.status === "in_progress").length;

		const questionsTotal = questions.length;
		const questionsMastered = questions.filter((q) => q.status === "mastered").length;
		const questionsSolved = questions.filter((q) => q.status === "solved" || q.status === "mastered").length;
		const questionsRetest = questions.filter((q) => q.status === "retest").length;
		const questionsInProgress = questions.filter((q) => q.status === "in_progress").length;
		const questionsSkipped = questions.filter((q) => q.status === "skipped").length;
		const questionsUnseen = questions.filter((q) => !q.status || q.status === "unseen").length;

		const milestones = { ...DEFAULT_MILESTONES };
		if (raw.milestones && typeof raw.milestones === "object" && !Array.isArray(raw.milestones)) {
			for (const [k, v] of Object.entries(raw.milestones)) {
				milestones[k] = Boolean(v);
			}
		}

		const activeQDenom = Math.max(0, questionsTotal - questionsSkipped);
		const denom = topicsTotal + activeQDenom;
		let pct = 0;
		if (denom > 0) {
			pct = Math.max(0, Math.min(100, Math.round(((topicsMastered + questionsSolved) / denom) * 100)));
		} else {
			const mVals = Object.values(milestones);
			if (mVals.length > 0) {
				pct = Math.round((mVals.filter(Boolean).length / mVals.length) * 100);
			}
		}

		const nextHint = computeTrackerNextHint(raw, topics, questions, milestones);

		trackers.push({
			slug,
			title,
			subject,
			active,
			relPath: item.relPath,
			createdAt: typeof raw.createdAt === "string" ? raw.createdAt : null,
			updatedAt: typeof raw.updatedAt === "string" ? raw.updatedAt : item.mtimeIso,
			mtimeIso: item.mtimeIso,
			stepCounter: Number.isFinite(raw.stepCounter) ? raw.stepCounter : 0,
			sourcesCount: Array.isArray(raw.sources) ? raw.sources.length : 0,
			doubtCount: Array.isArray(raw.doubtStack) ? raw.doubtStack.length : 0,
			topicsTotal,
			topicsMastered,
			topicsInProgress,
			questionsTotal,
			questionsSolved,
			questionsMastered,
			questionsRetest,
			questionsInProgress,
			questionsUnseen,
			questionsSkipped,
			pct,
			milestones,
			topics,
			nextHint,
		});
	}

	trackers.sort((a, b) => {
		if (a.active !== b.active) return a.active ? -1 : 1;
		const da = String(a.updatedAt || a.mtimeIso || "");
		const db = String(b.updatedAt || b.mtimeIso || "");
		if (da !== db) return db.localeCompare(da);
		return a.slug.localeCompare(b.slug);
	});

	return trackers;
}

function scanAudioBriefs() {
	const files = walkFiles(chaptersDir, {
		extensions: new Set(["mp3", "m4a"]),
		relPrefix: "chapters",
	});
	return files.map(({ fullPath: _fullPath, ...item }) => {
		const stem = path.basename(item.name, path.extname(item.name));
		const slug = stem.replace(/-audio$/iu, "");
		return {
			...item,
			slug,
			title: prettifyName(item.name),
		};
	});
}

function scanChapters(audioBriefs, chapterTrackers) {
	const files = walkFiles(chaptersDir, {
		extensions: new Set(["md", "pdf"]),
		relPrefix: "chapters",
	}).filter((item) => !/-tracker\.json$/iu.test(item.name));

	const audioBySlug = new Map();
	for (const ab of audioBriefs) {
		const key = String(ab.slug || "").toLowerCase();
		if (key && !audioBySlug.has(key)) audioBySlug.set(key, ab.relPath);
	}
	const trackerSlugs = new Set(chapterTrackers.map((t) => String(t.slug || "").toLowerCase()));

	return files.map(({ fullPath, ...item }) => {
		const stem = path.basename(item.name, path.extname(item.name));
		const exactSlug = stem.toLowerCase();
		const baseSlug = exactSlug.replace(/-(?:sheet|cheatsheet|cheat-sheet|formula|summary)$/iu, "");
		const audioPath = audioBySlug.get(exactSlug) || audioBySlug.get(baseSlug) || null;
		const trackerSlug = trackerSlugs.has(exactSlug)
			? exactSlug
			: trackerSlugs.has(baseSlug)
				? baseSlug
				: null;

		const isRevisionSheet =
			item.ext === "pdf" ||
			/(?:sheet|cheatsheet|cheat-sheet|formula|summary)/iu.test(item.name);
		if (item.ext === "md") {
			const meta = extractMarkdownMeta(fullPath);
			return {
				...item,
				slug: stem,
				title: meta.title || prettifyName(item.name),
				excerpt: meta.excerpt,
				wordCount: meta.wordCount,
				content: meta.content,
				contentTruncated: meta.contentTruncated,
				audioPath,
				trackerSlug,
				category: isRevisionSheet ? "sheet" : "chapter",
			};
		}
		return {
			...item,
			slug: stem,
			title: prettifyName(item.name),
			excerpt: "",
			wordCount: null,
			audioPath,
			trackerSlug,
			category: isRevisionSheet ? "sheet" : "chapter",
		};
	});
}

function scanDemos() {
	const files = walkFiles(path.join(root, "demos"), {
		extensions: new Set(["html", "svg", "png", "jpg", "jpeg", "webp"]),
	}).filter((item) => !/-template\.html$/iu.test(item.name));
	return files.map(({ fullPath, ...item }) => {
		let title = prettifyName(item.name);
		let kind = item.ext === "html" ? "simulation" : "diagram";
		if (item.ext === "html") {
			const meta = extractHtmlMeta(fullPath);
			if (meta.title) title = meta.title;
			if (meta.is3d || /(?:3d|mol|conformer|chair|stereo|isomer)/iu.test(item.name)) {
				kind = "3d-molecule";
			}
		} else if (/(?:circuit|lcr|rc|wheatstone|kirchhoff|diode|gate)/iu.test(item.name)) {
			kind = "circuit";
		} else if (/(?:mech|arrow|sn1|sn2|e1|e2|aldol|reaction|mol|chem|stereo)/iu.test(item.name)) {
			kind = "mechanism";
		}
		return {
			...item,
			title,
			kind,
		};
	});
}

function scanAnimations() {
	const animDir = path.join(root, "animations");
	const files = walkFiles(animDir, {
		extensions: new Set(["mp4", "webm", "mov"]),
	});
	return files.map(({ fullPath, ...item }) => {
		const dir = path.dirname(fullPath);
		const stem = path.basename(item.name, path.extname(item.name));
		const srtCandidate = path.join(dir, `${stem}.srt`);
		const vttCandidate = path.join(dir, `${stem}.vtt`);
		const pyCandidate = path.join(dir, `${stem}.py`);
		const rootPyCandidate = path.join(animDir, `${stem}.py`);
		const scriptFull = existsSync(pyCandidate)
			? pyCandidate
			: existsSync(rootPyCandidate)
				? rootPyCandidate
				: null;
		return {
			...item,
			title: prettifyName(item.name),
			srtPath: existsSync(srtCandidate) ? toPosix(path.relative(root, srtCandidate)) : null,
			vttPath: existsSync(vttCandidate) ? toPosix(path.relative(root, vttCandidate)) : null,
			scriptPath: scriptFull ? toPosix(path.relative(root, scriptFull)) : null,
		};
	});
}

function scanSources() {
	const files = walkFiles(path.join(root, "sources"));
	return files.map(({ fullPath: _fullPath, ...item }) => {
		let kind = "other";
		if (item.ext === "pdf") kind = "pdf";
		else if (["md", "txt", "rtf", "org", "tex"].includes(item.ext)) kind = "notes";
		else if (["png", "jpg", "jpeg", "webp", "svg", "heic"].includes(item.ext)) kind = "image";
		else if (["csv", "json", "jsonl", "xlsx", "pptx", "docx"].includes(item.ext)) kind = "document";
		return {
			...item,
			title: prettifyName(item.name),
			kind,
		};
	});
}

function scanExams() {
	const files = walkFiles(path.join(root, "exams"), {
		extensions: new Set(["html"]),
	});
	return files.map(({ fullPath, ...item }) => {
		const meta = extractHtmlMeta(fullPath);
		const slug = path.basename(item.name, path.extname(item.name));
		return {
			...item,
			slug,
			title: meta.title || prettifyName(item.name),
		};
	});
}

function toFiniteNumber(val) {
	return typeof val === "number" && Number.isFinite(val) ? val : null;
}

function computeMistakeStats(mistakes) {
	const total = mistakes.length;
	const catCounts = new Map();
	const chapterMap = new Map();
	const subjectMap = new Map();

	for (const m of mistakes) {
		const category = String(m.category || "concept_gap").trim() || "concept_gap";
		catCounts.set(category, (catCounts.get(category) || 0) + 1);

		const subject = String(m.subject || "General").trim() || "General";
		subjectMap.set(subject, (subjectMap.get(subject) || 0) + 1);

		const chapter = String(m.chapter || "").trim();
		if (chapter) {
			const key = `${subject}\u0000${chapter}`;
			const cur = chapterMap.get(key) || { chapter, subject, count: 0 };
			cur.count += 1;
			chapterMap.set(key, cur);
		}
	}

	const byCategory = [...catCounts.entries()]
		.map(([category, count]) => ({
			category,
			count,
			pct: total > 0 ? Number(((count / total) * 100).toFixed(1)) : 0,
		}))
		.sort((a, b) => b.count - a.count || a.category.localeCompare(b.category));

	const topCategories = byCategory.slice(0, 3);

	const hottestChapters = [...chapterMap.values()].sort(
		(a, b) =>
			b.count - a.count ||
			a.chapter.localeCompare(b.chapter) ||
			a.subject.localeCompare(b.subject),
	);

	const bySubject = {};
	for (const [subj, count] of [...subjectMap.entries()].sort(
		(a, b) => b[1] - a[1] || a[0].localeCompare(b[0]),
	)) {
		bySubject[subj] = count;
	}

	const recent = mistakes
		.map((m, idx) => ({ m, idx }))
		.sort((a, b) => {
			const da = String(a.m.date || "");
			const db = String(b.m.date || "");
			if (da !== db) return db.localeCompare(da);
			return b.idx - a.idx;
		})
		.slice(0, 8)
		.map((x) => x.m);

	return {
		total,
		byCategory,
		topCategories,
		hottestChapters,
		bySubject,
		recent,
	};
}

function computeExamStats(examAttempts) {
	const attemptsCount = examAttempts.length;
	if (attemptsCount === 0) {
		return {
			attemptsCount: 0,
			avgPct: null,
			avgAccuracyPct: null,
			bestPct: null,
			totalNegativeLost: 0,
			avgTimePerQuestionSec: null,
			latestAttempt: null,
		};
	}

	const pctVals = [];
	const accVals = [];
	let totalNegativeLost = 0;
	let totalTimeSec = 0;
	let totalQuestionDenom = 0;

	for (const a of examAttempts) {
		let pct = toFiniteNumber(a.percentage);
		if (pct === null) {
			const score = toFiniteNumber(a.score);
			const maxScore = toFiniteNumber(a.maxScore);
			if (score !== null && maxScore !== null && maxScore > 0) {
				pct = (score / maxScore) * 100;
			}
		}
		if (pct !== null) pctVals.push(pct);

		let acc = toFiniteNumber(a.accuracyPct);
		const correct = toFiniteNumber(a.correctCount) || 0;
		const partial = toFiniteNumber(a.partialCount) || 0;
		const wrong = toFiniteNumber(a.wrongCount) || 0;
		const unattempted = toFiniteNumber(a.unattemptedCount) || 0;
		const attempted = correct + partial + wrong;
		if (acc === null && attempted > 0) {
			acc = (correct / attempted) * 100;
		}
		if (acc !== null) accVals.push(acc);

		const neg = toFiniteNumber(a.negativeMarksLost);
		if (neg !== null) totalNegativeLost += Math.abs(neg);

		const spent = toFiniteNumber(a.timeSpentSec);
		const denom = attempted > 0 ? attempted : attempted + unattempted;
		if (spent !== null && spent >= 0 && denom > 0) {
			totalTimeSec += spent;
			totalQuestionDenom += denom;
		}
	}

	const avgPct = pctVals.length
		? Number((pctVals.reduce((s, v) => s + v, 0) / pctVals.length).toFixed(1))
		: null;
	const bestPct = pctVals.length
		? Number(Math.max(...pctVals).toFixed(1))
		: null;
	const avgAccuracyPct = accVals.length
		? Number((accVals.reduce((s, v) => s + v, 0) / accVals.length).toFixed(1))
		: null;
	const avgTimePerQuestionSec = totalQuestionDenom > 0
		? Number((totalTimeSec / totalQuestionDenom).toFixed(1))
		: null;

	const sortedDesc = examAttempts
		.map((a, idx) => ({ a, idx }))
		.sort((x, y) => {
			const dx = String(x.a.date || "");
			const dy = String(y.a.date || "");
			if (dx !== dy) return dy.localeCompare(dx);
			return y.idx - x.idx;
		});

	return {
		attemptsCount,
		avgPct,
		avgAccuracyPct,
		bestPct,
		totalNegativeLost: Number(totalNegativeLost.toFixed(2)),
		avgTimePerQuestionSec,
		latestAttempt: sortedDesc[0].a,
	};
}

function getBktValue(node) {
	if (typeof node.bktMastery === "number" && Number.isFinite(node.bktMastery)) return node.bktMastery;
	if (typeof node.confidence === "number" && Number.isFinite(node.confidence)) return node.confidence;
	return null;
}

function getStabilityDays(node) {
	const raw = node?.fsrs?.stability ?? node?.stabilityDays ?? node?.stability;
	return typeof raw === "number" && Number.isFinite(raw) ? raw : null;
}

function getNextReviewDate(node) {
	if (typeof node.nextReview === "string" && node.nextReview.trim()) {
		return node.nextReview.trim().slice(0, 10);
	}
	if (typeof node?.fsrs?.due === "string" && node.fsrs.due.trim()) {
		return node.fsrs.due.trim().slice(0, 10);
	}
	return null;
}

function normalizeChains(rawChains) {
	const chains = [];
	const normalizedEdges = [];
	const byKey = new Map();
	let hyperedgeCount = 0;

	for (const raw of rawChains) {
		if (!raw || typeof raw !== "object") continue;
		const conclusion =
			typeof raw.conclusion === "string" && raw.conclusion.trim()
				? raw.conclusion.trim()
				: typeof raw.to === "string" && raw.to.trim()
					? raw.to.trim()
					: "";
		if (!conclusion) continue;

		let premises = [];
		if (Array.isArray(raw.premises)) {
			premises = raw.premises
				.map((p) => (typeof p === "string" ? p.trim() : String(p ?? "").trim()))
				.filter((p) => p && p !== conclusion);
		} else if (typeof raw.from === "string" && raw.from.trim() && raw.from.trim() !== conclusion) {
			premises = [raw.from.trim()];
		}
		premises = [...new Set(premises)];
		if (premises.length === 0) continue;

		const chainKey = `${[...premises].sort().join("+")}>${conclusion}`;
		const relation =
			typeof raw.relation === "string" && raw.relation.trim()
				? raw.relation.trim()
				: "requires";
		const why =
			typeof raw.why === "string" && raw.why.trim()
				? raw.why.trim()
				: typeof raw.note === "string" && raw.note.trim()
					? raw.note.trim()
					: "";
		const note =
			typeof raw.note === "string" && raw.note.trim()
				? raw.note.trim()
				: why;

		const existing = byKey.get(chainKey);
		if (existing) {
			if (!existing.why && why) existing.why = why;
			if (!existing.note && note) existing.note = note;
			continue;
		}

		const isJoint = premises.length >= 2;
		if (isJoint) hyperedgeCount++;

		const normalized = {
			...raw,
			premises,
			conclusion,
			relation,
			chainKey,
			isJoint,
		};
		if (why) normalized.why = why;
		if (note) normalized.note = note;
		if (!isJoint) {
			normalized.from = premises[0];
			normalized.to = conclusion;
		} else {
			// Keep multi-premise hyperedges explicit without collapsing to a fake single `from`.
			delete normalized.from;
			normalized.to = conclusion;
		}

		byKey.set(chainKey, normalized);
		chains.push(normalized);

		for (const premise of premises) {
			const edge = {
				from: premise,
				to: conclusion,
				relation,
				chainKey,
				isJoint,
				premises,
				conclusion,
			};
			if (why) edge.why = why;
			if (note) edge.note = note;
			normalizedEdges.push(edge);
		}
	}

	return {
		chains,
		normalizedEdges,
		chainCount: chains.length,
		hyperedgeCount,
	};
}

function computeStats(
	propositions,
	chainSummary,
	chapters,
	chapterTrackers,
	activeChapterTracker,
	audioBriefs,
	demos,
	animations,
	sources,
	mistakes,
	examFiles,
	examAttempts,
	harnessLog,
) {
	const today = new Date().toISOString().slice(0, 10);
	const counts = { solid: 0, shaky: 0, misconception: 0, planned: 0, other: 0 };
	const bktValues = [];
	const subjectsSet = new Set();
	const dueItems = [];

	for (const p of propositions) {
		const status = p.status || "planned";
		if (status in counts) counts[status]++;
		else counts.other++;

		if (p.subject) subjectsSet.add(p.subject);
		const bkt = getBktValue(p);
		if (bkt !== null) bktValues.push(bkt);

		const nextReview = getNextReviewDate(p);
		const stabilityDays = getStabilityDays(p);
		const isDue =
			status === "misconception" ||
			status === "shaky" ||
			(nextReview !== null && nextReview <= today);

		if (isDue) {
			dueItems.push({
				id: p.id,
				title: p.title || p.id,
				subject: p.subject || "General",
				chapter: p.chapter || "",
				status,
				bktMastery: bkt,
				stabilityDays,
				nextReview,
				notes: p.notes || "",
			});
		}
	}

	const urgencyRank = (status) =>
		status === "misconception" ? 0 : status === "shaky" ? 1 : 2;
	dueItems.sort((a, b) => {
		const rankDiff = urgencyRank(a.status) - urgencyRank(b.status);
		if (rankDiff !== 0) return rankDiff;
		const dateA = a.nextReview || "0000-00-00";
		const dateB = b.nextReview || "0000-00-00";
		if (dateA !== dateB) return dateA.localeCompare(dateB);
		return (a.bktMastery ?? 0.5) - (b.bktMastery ?? 0.5);
	});

	const avgBktMastery = bktValues.length
		? Number((bktValues.reduce((acc, v) => acc + v, 0) / bktValues.length).toFixed(4))
		: null;

	return {
		today,
		stats: {
			totalNodes: propositions.length,
			totalEdges: chainSummary.normalizedEdges.length,
			chainCount: chainSummary.chainCount,
			hyperedgeCount: chainSummary.hyperedgeCount,
			counts,
			avgBktMastery,
			bktTrackedCount: bktValues.length,
			dueCount: dueItems.length,
			subjects: [...subjectsSet].sort(),
			chaptersCount: chapters.length,
			revisionSheetsCount: chapters.filter((c) => c.ext === "pdf" || c.category === "sheet").length,
			trackersCount: chapterTrackers.length,
			activeChapterPct: activeChapterTracker ? activeChapterTracker.pct : null,
			audioBriefsCount: audioBriefs.length,
			demosCount: demos.length,
			animationsCount: animations.length,
			sourcesCount: sources.length,
			mistakesCount: mistakes.length,
			examsCount: examFiles.length,
			examAttemptsCount: examAttempts.length,
			harnessLogCount: harnessLog.length,
		},
		dueItems,
	};
}

function safeScriptJson(value) {
	return JSON.stringify(value, null, 2)
		.replace(/<\/script/giu, "<\\/script")
		.replace(/\u2028/gu, "\\u2028")
		.replace(/\u2029/gu, "\\u2029");
}

function openTarget(targetPath) {
	if (process.platform === "darwin") {
		execFileSync("open", [targetPath], { stdio: "ignore" });
	} else if (process.platform === "linux") {
		execFileSync("xdg-open", [targetPath], { stdio: "ignore" });
	} else if (process.platform === "win32") {
		execFileSync("cmd", ["/c", "start", "", targetPath], { stdio: "ignore" });
	}
}

function extractIsoDate(val) {
	if (typeof val !== "string") return null;
	const m = /^(\d{4}-\d{2}-\d{2})/.exec(val.trim());
	return m ? m[1] : null;
}

function computeRetentionHeatmap(revlog, examAttempts, propositions, today) {
	const endMs = Date.parse(`${today}T00:00:00Z`);
	const safeEndMs = Number.isFinite(endMs) ? endMs : Date.now();
	const dayMs = 24 * 60 * 60 * 1000;
	const windowDays = 365;
	const startMs = safeEndMs - (windowDays - 1) * dayMs;
	const startDate = new Date(startMs).toISOString().slice(0, 10);

	const buckets = new Map();
	const ensureDay = (d) => {
		let entry = buckets.get(d);
		if (!entry) {
			entry = { count: 0, reviews: 0, exams: 0 };
			buckets.set(d, entry);
		}
		return entry;
	};
	const inWindow = (d) => Boolean(d && d >= startDate && d <= today);

	const revlogPropDates = new Set();
	for (const ev of revlog) {
		const d = extractIsoDate(ev.ts || ev.reviewedAt || ev.date || "");
		if (!inWindow(d)) continue;
		const b = ensureDay(d);
		b.count += 1;
		b.reviews += 1;
		if (ev.propId || ev.id) {
			revlogPropDates.add(`${String(ev.propId || ev.id)}::${d}`);
		}
	}

	for (const att of examAttempts) {
		const d = extractIsoDate(att.date || att.submittedAt || att.ts || "");
		if (!inWindow(d)) continue;
		const b = ensureDay(d);
		b.count += 1;
		b.exams += 1;
	}

	for (const p of propositions) {
		const pid = String(p.id || "");
		const seenDates = new Set();
		for (const rawD of [p.lastReviewed, p.lastTested, p?.fsrs?.last_review]) {
			const d = extractIsoDate(rawD || "");
			if (!inWindow(d) || seenDates.has(d)) continue;
			seenDates.add(d);
			if (pid && revlogPropDates.has(`${pid}::${d}`)) continue;
			const b = ensureDay(d);
			b.count += 1;
			b.reviews += 1;
		}
	}

	const levelFor = (c) => (c <= 0 ? 0 : c <= 2 ? 1 : c <= 5 ? 2 : c <= 9 ? 3 : 4);
	const days = [];
	const byDate = {};
	let totalEvents = 0;
	let activeDays = 0;
	let maxDailyCount = 0;

	for (let i = 0; i < windowDays; i++) {
		const d = new Date(startMs + i * dayMs).toISOString().slice(0, 10);
		const b = buckets.get(d);
		const count = b ? b.count : 0;
		const reviews = b ? b.reviews : 0;
		const exams = b ? b.exams : 0;
		if (count > 0) {
			byDate[d] = count;
			totalEvents += count;
			activeDays += 1;
			if (count > maxDailyCount) maxDailyCount = count;
		}
		days.push({ date: d, count, reviews, exams, level: levelFor(count) });
	}

	return {
		windowDays,
		startDate,
		endDate: today,
		totalEvents,
		activeDays,
		maxDailyCount,
		byDate,
		days,
	};
}

function computeCalibration(revlog) {
	const totalReviews = revlog.length;
	const calibrated = [];
	let hypercorrectionCount = 0;
	let luckyGuessCount = 0;

	for (const ev of revlog) {
		const rating = String(ev.rating || "").trim().toLowerCase();
		const hasCorrect = typeof ev.correct === "boolean";
		const y = hasCorrect
			? ev.correct
				? 1
				: 0
			: rating === "good" || rating === "easy"
				? 1
				: rating === "again" || rating === "hard"
					? 0
					: null;
		const rawConf =
			typeof ev.learnerConfidence === "number" && Number.isFinite(ev.learnerConfidence)
				? ev.learnerConfidence
				: typeof ev.confidence === "number" && Number.isFinite(ev.confidence)
					? ev.confidence
					: null;
		if (ev.hypercorrection === true) {
			hypercorrectionCount += 1;
		}
		if (rawConf === null || y === null) continue;
		const c = Math.max(0, Math.min(1, rawConf > 1 ? rawConf / 100 : rawConf));
		if (ev.hypercorrection !== true && c >= 0.75 && y === 0) {
			hypercorrectionCount += 1;
		}
		if (c <= 0.35 && y === 1) {
			luckyGuessCount += 1;
		}
		calibrated.push({ c, y });
	}

	const windowItems = calibrated.slice(-100);
	const n = windowItems.length;
	if (n === 0) {
		return {
			sampleCount: 0,
			totalReviews,
			brierScore: null,
			overconfidenceBias: null,
			accuracy: null,
			meanConfidence: null,
			ece: null,
			hypercorrectionCount,
			luckyGuessCount,
		};
	}

	let brierSum = 0;
	let biasSum = 0;
	let accSum = 0;
	let confSum = 0;
	const bins = Array.from({ length: 5 }, () => ({ n: 0, cSum: 0, ySum: 0 }));

	for (const { c, y } of windowItems) {
		const diff = c - y;
		brierSum += diff * diff;
		biasSum += diff;
		accSum += y;
		confSum += c;
		const binIdx = Math.min(4, Math.floor(c * 5));
		bins[binIdx].n += 1;
		bins[binIdx].cSum += c;
		bins[binIdx].ySum += y;
	}

	let ece = 0;
	for (const b of bins) {
		if (b.n === 0) continue;
		ece += (b.n / n) * Math.abs(b.cSum / b.n - b.ySum / b.n);
	}

	return {
		sampleCount: n,
		totalReviews,
		brierScore: Number((brierSum / n).toFixed(4)),
		overconfidenceBias: Number((biasSum / n).toFixed(4)),
		accuracy: Number((accSum / n).toFixed(4)),
		meanConfidence: Number((confSum / n).toFixed(4)),
		ece: Number(ece.toFixed(4)),
		hypercorrectionCount,
		luckyGuessCount,
	};
}

function computeLeeches(propositions, revlog) {
	const againCounts = new Map();
	for (const ev of revlog) {
		const pid = String(ev.propId || ev.id || "").trim();
		if (!pid) continue;
		if (String(ev.rating || "").trim().toLowerCase() === "again" || ev.correct === false) {
			againCounts.set(pid, (againCounts.get(pid) || 0) + 1);
		}
	}

	const leeches = [];
	for (const p of propositions) {
		const pid = String(p.id || "");
		const rawLapses = p.lapses ?? p?.fsrs?.lapses ?? againCounts.get(pid) ?? 0;
		const lapses = Number.isFinite(Number(rawLapses)) ? Number(rawLapses) : 0;
		const rawDiff = p.difficulty ?? p?.fsrs?.difficulty;
		const difficulty =
			rawDiff !== undefined && rawDiff !== null && Number.isFinite(Number(rawDiff))
				? Number(Number(rawDiff).toFixed(2))
				: null;

		const reasons = [];
		if (lapses >= 4) reasons.push(`lapses=${lapses}`);
		if (difficulty !== null && difficulty >= 8.5) reasons.push(`difficulty=${difficulty}`);
		if (reasons.length === 0) continue;

		leeches.push({
			id: pid,
			title: p.title || pid,
			subject: p.subject || "General",
			chapter: p.chapter || "",
			status: p.status || "shaky",
			lapses,
			difficulty,
			reason: reasons.join(", "),
			notes: p.notes || "",
		});
	}

	leeches.sort((a, b) => b.lapses - a.lapses || (b.difficulty ?? 0) - (a.difficulty ?? 0) || a.id.localeCompare(b.id));
	return leeches;
}

function normalizeReadingQueue(rawQueue) {
	let realRoot = root;
	try {
		if (existsSync(root)) realRoot = realpathSync(root);
	} catch {
		realRoot = root;
	}
	const items = rawQueue.map((item, idx) => {
		const priority =
			typeof item.priority === "number" && Number.isFinite(item.priority)
				? Math.max(0, Math.min(100, Math.round(item.priority)))
				: 50;
		const stage = typeof item.stage === "string" && item.stage.trim() ? item.stage.trim() : "queued";
		let relPath = typeof item.relPath === "string" && item.relPath.trim() ? toPosix(item.relPath.trim()) : "";
		if (!relPath && typeof item.sourcePath === "string" && item.sourcePath.trim()) {
			const rawSrc = item.sourcePath.trim();
			if (path.isAbsolute(rawSrc)) {
				const candSrcs = [rawSrc];
				try {
					if (existsSync(rawSrc)) candSrcs.push(realpathSync(rawSrc));
				} catch {
					// ignore
				}
				if (process.platform === "darwin" && rawSrc.startsWith("/var/")) {
					candSrcs.push(`/private${rawSrc}`);
				}
				for (const base of [root, realRoot]) {
					for (const src of candSrcs) {
						const rel = path.relative(base, src);
						if (rel && !rel.startsWith("..") && !path.isAbsolute(rel)) {
							relPath = toPosix(rel);
							break;
						}
					}
					if (relPath) break;
				}
			} else if (!rawSrc.startsWith("..")) {
				relPath = toPosix(rawSrc.replace(/^\.\/+/u, ""));
			}
		}
		const out = {
			...item,
			id: String(item.id || `rq-${idx + 1}`),
			title: String(item.title || item.slug || item.url || `Extract ${idx + 1}`),
			priority,
			stage,
		};
		if (relPath) out.relPath = relPath;
		return out;
	});
	items.sort((a, b) => {
		const doneA = a.stage === "crystallized" ? 1 : 0;
		const doneB = b.stage === "crystallized" ? 1 : 0;
		if (doneA !== doneB) return doneA - doneB;
		if (b.priority !== a.priority) return b.priority - a.priority;
		return String(a.nextReview || "").localeCompare(String(b.nextReview || ""));
	});
	return items;
}

// ---------------------------------------------------------------------------
// Syntopik v3.0 Pure-Node Sanitizers, Safe :::explore RPN Compiler & Packs
// ---------------------------------------------------------------------------

function readJsonObject(filePath) {
	if (!existsSync(filePath)) return null;
	try {
		const parsed = JSON.parse(readFileSync(filePath, "utf8"));
		return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : null;
	} catch {
		return null;
	}
}

function slugifySafe(raw, fallback = "item") {
	const s = String(raw || "")
		.toLowerCase()
		.replace(/[^a-z0-9]+/gu, "-")
		.replace(/^-+|-+$/gu, "");
	return s || fallback;
}

const SEALED_FORK_KEYS = new Set([
	"sealedWinningMove",
	"sealedBlunder",
	"refutationMsg",
	"verifySpec",
	"mutationPool",
]);

const SEALED_DECISION_KEYS = new Set([
	"bestMove",
	"expertRationale",
	"expertLens",
	"killCue",
	"firstMove",
	"rejectedDecoy",
	"sealedAnswer",
	"canonicalAnswer",
]);

const SEALED_GATE_KEYS = new Set([
	"canonicalAnswer",
	"expectedAnswer",
	"revealedBody",
	"sealedAnswer",
	"answer",
]);

function sanitizeFork(fork, today) {
	const out = {};
	for (const [k, v] of Object.entries(fork)) {
		if (!SEALED_FORK_KEYS.has(k)) out[k] = v;
	}
	if (out.mutationCount == null) {
		out.mutationCount = Array.isArray(fork.mutationPool) ? fork.mutationPool.length : 0;
	}
	if (!out.engine) {
		out.engine = fork?.verifySpec?.engine || "exact";
	}
	const status = String(out.status || "active").toLowerCase();
	const dueStr = typeof out.nextReview === "string" ? out.nextReview.slice(0, 10) : "";
	out.isDue = !dueStr || dueStr <= today || status === "active" || status === "shaky";
	return out;
}

function sanitizeArena(arena) {
	const mutants = Array.isArray(arena.sealedMutants) ? arena.sealedMutants : [];
	const killedSet = new Set(
		(Array.isArray(arena.killedMutants) ? arena.killedMutants : []).map((x) => String(x)),
	);
	const total = mutants.length > 0 ? mutants.length : Number(arena.totalMutants || 0);
	const killedCount =
		mutants.length > 0
			? mutants.filter((m) => m && typeof m === "object" && killedSet.has(String(m.id))).length
			: Number(arena.killedCount ?? killedSet.size);
	const survivedCount = Math.max(0, total - killedCount);
	const rawRepairs = Array.isArray(arena.lemmaRepairs) ? arena.lemmaRepairs : [];
	const lemmaRepairs = rawRepairs
		.filter((r) => r && typeof r === "object")
		.map((r) => ({
			id: r.id ?? null,
			lemma: r.lemma ?? "",
			ts: r.ts ?? null,
			persistedToProp: Boolean(r.persistedToProp),
		}));
	let assumptions = [];
	if (Array.isArray(arena.assumptionPredicates)) {
		assumptions = arena.assumptionPredicates.map((p) =>
			p && typeof p === "object" ? String(p.description || p.expr || "") : String(p),
		);
	} else if (Array.isArray(arena.assumptions)) {
		assumptions = arena.assumptions.map(String);
	}
	const status = String(
		arena.status || (total > 0 && killedCount >= total ? "cleared" : "active"),
	).toLowerCase();
	return {
		id: arena.id ?? null,
		propId: arena.propId ?? null,
		domain: arena.domain ?? null,
		title: arena.title ?? null,
		claim: arena.claim ?? null,
		entry: arena.entry ?? null,
		lang: arena.lang ?? null,
		status,
		assumptions,
		totalMutants: total,
		killedCount,
		survivedCount,
		killedMutants: [...killedSet].sort(),
		bestMinimalSize: arena.bestMinimalSize ?? null,
		lemmaRepairsCount: lemmaRepairs.length,
		lemmaRepairs,
		updatedAt: arena.updatedAt ?? null,
	};
}

function sanitizeDecision(dec) {
	const status = String(dec.status || "").trim().toLowerCase();
	const hasGradeFlags = ["killCueHit", "firstMoveHit", "decoyHit", "correct"].some(
		(k) => typeof dec[k] === "boolean",
	);
	const isGraded =
		dec.graded === true ||
		(dec.graded !== false &&
			!["pending", "ungraded", "open", "draft"].includes(status) &&
			hasGradeFlags);
	if (isGraded) return { ...dec };
	const out = {};
	for (const [k, v] of Object.entries(dec)) {
		if (!SEALED_DECISION_KEYS.has(k)) out[k] = v;
	}
	return out;
}

function sanitizeDerivation(deriv, level = 3) {
	const steps = (Array.isArray(deriv.steps) ? deriv.steps : []).filter((s) => s && typeof s === "object");
	const nonAxioms = steps.filter((s) => String(s.role || "step").toLowerCase() !== "axiom");
	const lvl = Math.max(1, Math.min(3, Math.trunc(Number(level) || 3)));
	const hiddenIds = new Set();
	if (nonAxioms.length > 0) {
		if (lvl === 1) {
			const inter = nonAxioms.filter((s) => String(s.role || "step").toLowerCase() === "step");
			const pool = inter.length > 0 ? inter : nonAxioms;
			const targetN = Math.max(1, Math.round(pool.length * 0.4));
			const stride = Math.max(1, Math.floor(pool.length / targetN));
			for (let idx = pool.length - 1; idx >= 0; idx -= stride) {
				hiddenIds.add(String(pool[idx].id || ""));
				if (hiddenIds.size >= targetN) break;
			}
		} else if (lvl === 2) {
			const targetN = Math.max(1, Math.round(nonAxioms.length * 0.65));
			for (let idx = nonAxioms.length - 1; idx >= 0; idx -= 1) {
				if (hiddenIds.size >= targetN) break;
				hiddenIds.add(String(nonAxioms[idx].id || ""));
			}
		} else {
			for (const s of nonAxioms) {
				hiddenIds.add(String(s.id || ""));
			}
		}
	}
	const publicSteps = steps.map((s) => {
		const sid = String(s.id || "");
		const role = String(s.role || "step").toLowerCase();
		const hidden = role !== "axiom" && hiddenIds.has(sid);
		return {
			id: sid,
			label: s.label || sid,
			role: s.role || "step",
			dependsOn: Array.isArray(s.dependsOn) ? s.dependsOn.map(String) : [],
			hidden,
			expr: hidden ? null : (s.expr ?? null),
			note: hidden ? null : (s.note || null),
		};
	});
	return {
		id: deriv.id ?? null,
		propId: deriv.propId ?? null,
		chapter: deriv.chapter ?? "General",
		title: deriv.title ?? deriv.id ?? "Derivation",
		domain: deriv.domain ?? "General",
		blackoutLevel: lvl,
		stepCount: steps.length,
		axiomCount: steps.filter((s) => String(s.role || "step").toLowerCase() === "axiom").length,
		theoremCount: steps.filter((s) => String(s.role || "step").toLowerCase() === "theorem").length,
		hiddenCount: publicSteps.filter((s) => s.hidden).length,
		hiddenStepIds: publicSteps.filter((s) => s.hidden).map((s) => s.id),
		steps: publicSteps,
		updatedAt: deriv.updatedAt ?? null,
	};
}

function sanitizeVivaSession(sess) {
	const prompts = (Array.isArray(sess.prompts) ? sess.prompts : []).filter((p) => p && typeof p === "object");
	const turns = (Array.isArray(sess.turns) ? sess.turns : []).filter((t) => t && typeof t === "object");
	const cur = Number.isInteger(sess.currentTurn) ? sess.currentTurn : turns.length + 1;
	const status = String(sess.status || "in_progress");
	const curPrompt =
		status === "in_progress" && cur >= 1 && cur <= prompts.length ? prompts[cur - 1] : null;
	const visiblePrompts =
		status === "completed" ? prompts : prompts.slice(0, Math.max(1, Math.min(cur, prompts.length)));
	return {
		id: sess.id ?? null,
		propId: sess.propId ?? null,
		propTitle: sess.propTitle ?? sess.propId ?? null,
		subject: sess.subject ?? "General",
		chapter: sess.chapter ?? "General",
		status,
		currentTurn: cur,
		totalTurns: Number(sess.totalTurns || 5),
		turnsCompleted: turns.length,
		turns,
		currentPrompt: curPrompt,
		prompts: visiblePrompts,
		commitmentLedger: sess.commitmentLedger ?? null,
		scores: sess.scores ?? null,
		composite:
			sess.scores && typeof sess.scores === "object" && typeof sess.scores.composite === "number"
				? sess.scores.composite
				: null,
		outcome: sess.outcome ?? null,
		evidenceRecorded: Boolean(sess.evidenceRecorded),
		demotedToShaky: Boolean(sess.demotedToShaky),
		createdAt: sess.createdAt ?? null,
		updatedAt: sess.updatedAt ?? null,
	};
}

function sanitizeWhiteboardFrame(frame) {
	const fr = { ...frame };
	const kind = String(fr.kind || "").toLowerCase();
	const gate = fr.gate && typeof fr.gate === "object" && !Array.isArray(fr.gate) ? { ...fr.gate } : null;
	const payload =
		fr.payload && typeof fr.payload === "object" && !Array.isArray(fr.payload) ? { ...fr.payload } : {};
	const isLockedGate =
		kind === "prediction_gate" && (fr.locked !== false || !(gate && gate.unlockedAt));
	if (isLockedGate) {
		for (const key of SEALED_GATE_KEYS) {
			delete fr[key];
			delete payload[key];
			if (gate) delete gate[key];
		}
		fr.body = "";
		fr.locked = true;
	} else if (kind === "prediction_gate" && gate) {
		if (fr.canonicalAnswer === undefined && gate.expectedAnswer !== undefined) {
			fr.canonicalAnswer = gate.expectedAnswer;
		}
	}
	if (gate) fr.gate = gate;
	fr.payload = payload;
	return fr;
}

function sanitizeWhiteboard(raw) {
	if (!raw || typeof raw !== "object") {
		return {
			schemaVersion: 1,
			sessionId: null,
			revision: 0,
			title: "",
			activeFrameId: null,
			frames: [],
			pins: [],
			pendingPins: [],
			pendingPinCount: 0,
			frameCount: 0,
			updatedAt: null,
		};
	}
	const frames = (Array.isArray(raw.frames) ? raw.frames : [])
		.filter((f) => f && typeof f === "object")
		.map(sanitizeWhiteboardFrame);
	const pins = (Array.isArray(raw.pins) ? raw.pins : []).filter((p) => p && typeof p === "object");
	const pendingPins = pins.filter((p) => !p.resolved);
	return {
		schemaVersion: 1,
		sessionId: raw.sessionId ?? null,
		revision: Number(raw.revision || 0),
		title: String(raw.title || ""),
		activeFrameId: raw.activeFrameId ?? null,
		frames,
		pins,
		pendingPins,
		pendingPinCount: pendingPins.length,
		frameCount: frames.length,
		updatedAt: raw.updatedAt ?? null,
	};
}

function defaultLearnerProfile() {
	return {
		schemaVersion: 1,
		updatedAt: null,
		calibration: {
			completed: false,
			completedAt: null,
			entryRamp: "balanced",
			challengeAppetite: "balanced",
			targetSuccessRate: 0.85,
			sessionTargetMinutes: 30,
			proseDensity: "standard",
			baselineLatencyMs: 22000,
			microTaskBias: 0.0,
			calibrationPrior: "calibrated",
		},
		userOverrides: {
			scaffoldingMode: null,
			allowProductiveFailure: null,
			maxProseWords: null,
		},
		globalStats: {
			elo: 1450.0,
			totalEvents: 0,
			priorWeight: 1.0,
			analogyFormalismRatio: 0.5,
			pfAttempts: 0,
			pfSurvivalRate: 0.65,
			latencyEwma: 1.0,
			pacingMultiplier: 1.0,
		},
		domains: {},
		affectiveState: {
			state: "flow",
			confidence: 0.5,
			activeSessionMinutes: 0.0,
			windowAccuracy: null,
			windowMeanHints: 0.0,
			windowMeanLatencyRatio: 1.0,
			errorStreak: 0,
			gamingSignals: 0,
			lastEventAt: null,
		},
		metacognition: {
			windowCount: 0,
			brierScore: null,
			ece: null,
			overconfidenceBias: null,
			label: "uncalibrated",
			recentHypercorrections: 0,
			recentLuckyGuesses: 0,
		},
		failureDna: {
			windowCount: 0,
			tierShares: {
				comprehension_model: 0.0,
				process_encoding: 0.0,
				executive_pacing: 0.0,
			},
			dominantTier: null,
			topCategories: [],
		},
		compiledPolicy: {
			domain: "General",
			domainElo: 1450.0,
			expertiseBand: "intermediate",
			scaffoldingMode: "faded",
			effectiveReadiness: 0.5,
			allowWorkedExample: false,
			allowProductiveFailure: true,
			maxProseWords: 135,
			analogyFormalismRatio: 0.5,
			targetSuccessRate: 0.85,
			pacingMultiplier: 1.0,
			requirePredictionFirst: false,
			energyOverride: null,
			affectState: "flow",
			affectIntervention: "none",
			activeGuardrails: [],
		},
	};
}

function computeEvidenceSummary(
	propositions,
	chainSummary,
	revlog,
	rawForks,
	rawArenas,
	rawViva,
	rawDerivations,
	evidenceLedger,
) {
	const TIER_LABELS = {
		1: "T1 Exposure",
		2: "T2 Cold Recall",
		3: "T3 Mechanistic Transfer",
		4: "T4 Adversarial Defense",
	};
	const adj = new Map();
	for (const e of chainSummary.normalizedEdges) {
		if (String(e.relation || "").toLowerCase() === "isomorphic_to") continue;
		if (!adj.has(e.from)) adj.set(e.from, new Set());
		adj.get(e.from).add(e.to);
	}
	const outDeg = new Map();
	const descMap = new Map();
	for (const [u, vs] of adj.entries()) {
		outDeg.set(u, vs.size);
		const visited = new Set();
		const queue = [...vs];
		while (queue.length > 0) {
			const cur = queue.shift();
			if (visited.has(cur) || cur === u) continue;
			visited.add(cur);
			const nexts = adj.get(cur);
			if (nexts) {
				for (const nxt of nexts) {
					if (!visited.has(nxt) && nxt !== u) queue.push(nxt);
				}
			}
		}
		descMap.set(u, visited);
	}

	const t2Rev = new Set();
	for (const r of revlog) {
		const pid = String(r.propId || r.id || "").trim();
		if (!pid) continue;
		const rating = String(r.rating || "").toLowerCase();
		const good = (typeof r.rating === "number" && r.rating >= 3) || rating === "good" || rating === "easy";
		const correct = typeof r.correct === "boolean" ? r.correct : good;
		const elapsed = Number(r.elapsedDays || 0);
		const ivl = Number(r.intervalDays || 0);
		if (correct && !r.luckyGuess && (elapsed >= 7 || ivl >= 7)) t2Rev.add(pid);
	}
	const t3Forks = new Set();
	for (const f of rawForks) {
		const pid = String(f.propId || "").trim();
		if (!pid) continue;
		const wins = Number(f.winCount || 0);
		const st = String(f.status || "").toLowerCase();
		if (wins >= 1 && (f.woodpeckerPassed || ["mastered", "solid", "cleared"].includes(st))) {
			t3Forks.add(pid);
		}
	}
	const t3Arenas = new Set();
	for (const a of rawArenas) {
		const pid = String(a.propId || "").trim();
		if (!pid) continue;
		const sa = sanitizeArena(a);
		const kr = sa.totalMutants > 0 ? sa.killedCount / sa.totalMutants : 0;
		if (kr >= 0.75 || ["cleared", "passed", "mastered"].includes(sa.status)) t3Arenas.add(pid);
	}
	const t4Viva = new Set();
	for (const v of rawViva) {
		const pid = String(v.propId || "").trim();
		if (!pid) continue;
		const score = v?.scores?.composite ?? v.score;
		if (
			v.passed ||
			v.outcome === "passed" ||
			(typeof score === "number" && score >= 0.75)
		) {
			t4Viva.add(pid);
		}
	}
	const t4Deriv = new Set();
	for (const d of rawDerivations) {
		const pid = String(d.propId || "").trim();
		if (!pid) continue;
		if (d.passed || d.blackoutPassed || d.axiomCutPassed || Number(d.completions || 0) >= 1) {
			t4Deriv.add(pid);
		}
	}
	const ledgerMax = new Map();
	const ledgerSrcs = new Map();
	for (const row of evidenceLedger) {
		if (row.verified === false) continue;
		const pid = String(row.propId || "").trim();
		const t = Number(row.tier);
		if (!pid || !TIER_LABELS[t]) continue;
		if (t > (ledgerMax.get(pid) || 0)) ledgerMax.set(pid, t);
		if (!ledgerSrcs.has(pid)) ledgerSrcs.set(pid, []);
		const src = String(row.source || "ledger");
		if (!ledgerSrcs.get(pid).includes(src)) ledgerSrcs.get(pid).push(src);
	}

	const tierCounts = { T1: 0, T2: 0, T3: 0, T4: 0 };
	const perProp = {};
	const antiInflationAlerts = [];
	for (const p of propositions) {
		const pid = String(p.id || "").trim();
		if (!pid) continue;
		const status = String(p.status || "planned").toLowerCase();
		let tier = 1;
		const sources = ["exposure"];
		const stab = Number(p?.fsrs?.stability || 0);
		if (t2Rev.has(pid) || (status === "solid" && stab >= 7) || (ledgerMax.get(pid) || 0) >= 2) {
			tier = Math.max(tier, 2);
			sources.push("cold_recall");
		}
		if (t3Arenas.has(pid)) {
			tier = Math.max(tier, 3);
			sources.push("falsify_arena");
		}
		if (t3Forks.has(pid)) {
			tier = Math.max(tier, 3);
			sources.push("fork_replay");
		}
		if ((ledgerMax.get(pid) || 0) >= 3) tier = Math.max(tier, 3);
		if (t4Viva.has(pid)) {
			tier = 4;
			sources.push("viva_chamber");
		}
		if (t4Deriv.has(pid)) {
			tier = 4;
			sources.push("derivation_forge");
		}
		if ((ledgerMax.get(pid) || 0) >= 4) tier = 4;
		for (const ls of ledgerSrcs.get(pid) || []) {
			if (!sources.includes(ls)) sources.push(ls);
		}
		tierCounts[`T${tier}`] += 1;
		const od = outDeg.get(pid) || 0;
		const descN = (descMap.get(pid) || new Set()).size;
		perProp[pid] = {
			propId: pid,
			title: String(p.title || pid),
			subject: String(p.subject || "General"),
			chapter: String(p.chapter || ""),
			status,
			tier,
			tierLabel: TIER_LABELS[tier],
			sources,
			outDegree: od,
			descendantsCount: descN,
		};
		const isBottleneck = od >= 2 || descN >= 2 || (status === "solid" && descN >= 1);
		if (isBottleneck && tier <= 2) {
			antiInflationAlerts.push({
				propId: pid,
				title: perProp[pid].title,
				subject: perProp[pid].subject,
				chapter: perProp[pid].chapter,
				status,
				currentTier: tier,
				currentTierLabel: TIER_LABELS[tier],
				requiredTier: 3,
				outDegree: od,
				descendantsCount: descN,
				suggestedAction: od >= 2 ? "falsify_arena" : "derivation_forge",
			});
		}
	}
	antiInflationAlerts.sort(
		(a, b) =>
			b.descendantsCount - a.descendantsCount ||
			b.outDegree - a.outDegree ||
			a.currentTier - b.currentTier ||
			a.propId.localeCompare(b.propId),
	);
	const totalProps = Object.keys(perProp).length;
	const verifiedTransferPct =
		totalProps > 0 ? Number((((tierCounts.T3 + tierCounts.T4) / totalProps) * 100).toFixed(1)) : 0.0;
	const headHash =
		evidenceLedger.length > 0 ? String(evidenceLedger[evidenceLedger.length - 1].entryHash || "") : "0".repeat(64);
	return {
		ok: true,
		totalPropositions: totalProps,
		tierCounts,
		verifiedTransferPct,
		propositions: perProp,
		antiInflationAlerts,
		ledgerIntegrity: {
			ok: true,
			count: evidenceLedger.length,
			brokenAt: null,
			headHash,
		},
	};
}

function computeRootCauseHotspots(propositions, chainSummary, mistakes) {
	const propsById = new Map(propositions.filter((p) => p?.id).map((p) => [String(p.id), p]));
	const outgoing = new Map();
	const incoming = new Map();
	for (const e of chainSummary.normalizedEdges) {
		if (String(e.relation || "").toLowerCase() === "isomorphic_to") continue;
		if (!outgoing.has(e.from)) outgoing.set(e.from, new Set());
		outgoing.get(e.from).add(e.to);
		if (!incoming.has(e.to)) incoming.set(e.to, new Set());
		incoming.get(e.to).add(e.from);
	}
	const missCounts = new Map();
	for (const m of mistakes) {
		const pid = String(m.propId || "").trim();
		if (pid) missCounts.set(pid, (missCounts.get(pid) || 0) + 1);
	}

	const blastRadius = [];
	const byProp = {};
	for (const [uid, p] of propsById.entries()) {
		const st = String(p.status || "planned").toLowerCase();
		if ((st !== "shaky" && st !== "misconception") || !outgoing.has(uid)) continue;
		const desc = new Set();
		const q = [...outgoing.get(uid)];
		while (q.length > 0) {
			const cur = q.shift();
			if (desc.has(cur) || cur === uid) continue;
			desc.add(cur);
			for (const nxt of outgoing.get(cur) || []) {
				if (!desc.has(nxt) && nxt !== uid) q.push(nxt);
			}
		}
		if (desc.size === 0) continue;
		const weakDesc = [...desc]
			.filter((d) => ["shaky", "misconception"].includes(String(propsById.get(d)?.status || "").toLowerCase()))
			.sort();
		let downstreamMisses = 0;
		for (const d of desc) downstreamMisses += missCounts.get(d) || 0;
		const chapters = [
			...new Set(
				[uid, ...desc]
					.map((id) => String(propsById.get(id)?.chapter || "").trim())
					.filter(Boolean),
			),
		].sort();
		const sevMult = st === "misconception" ? 1.4 : 1.0;
		const blastScore = Number(
			(
				sevMult *
				(1 + 0.25 * Math.max(0, chapters.length - 1)) *
				(desc.size + 2 * weakDesc.length + 1.5 * downstreamMisses + 0.5 * (missCounts.get(uid) || 0))
			).toFixed(3),
		);
		blastRadius.push({
			propId: uid,
			title: String(p.title || uid),
			subject: String(p.subject || "General"),
			chapter: String(p.chapter || ""),
			status: st,
			downstreamCount: desc.size,
			weakDownstreamCount: weakDesc.length,
			downstreamMissCount: downstreamMisses,
			affectedChapters: chapters,
			affectedNodeIds: [...desc].sort(),
			weakDownstreamIds: weakDesc,
			blastScore,
		});
		const est = Math.min(0.96, Number(((st === "misconception" ? 0.5 : 0.38) + 0.08 * weakDesc.length + 0.05 * downstreamMisses).toFixed(4)));
		byProp[uid] = Math.max(byProp[uid] || 0, est);
	}
	blastRadius.sort(
		(a, b) =>
			b.blastScore - a.blastScore ||
			b.weakDownstreamCount - a.weakDownstreamCount ||
			a.propId.localeCompare(b.propId),
	);

	// Also elevate direct weak/shaky parents of failing/weak leaves
	for (const p of propositions) {
		const pid = String(p.id || "");
		const st = String(p.status || "").toLowerCase();
		if (!pid || (st !== "shaky" && st !== "misconception" && !(missCounts.get(pid) > 0))) continue;
		for (const prem of incoming.get(pid) || []) {
			const pst = String(propsById.get(prem)?.status || "planned").toLowerCase();
			const score = pst === "misconception" ? 0.72 : pst === "shaky" ? 0.54 : 0.28;
			if (score > (byProp[prem] || 0)) byProp[prem] = score;
		}
	}

	return { byProp, blastRadius: blastRadius.slice(0, 12) };
}

// --- Safe AST-free :::explore RPN compiler (mirrors syntopik/explorable.py opcodes, ZERO eval) ---

const EXPLORE_MAX_EXP = 12;
const EXPLORE_MAX_LEN = 512;
const EXPLORE_MAX_OPS = 256;
const EXPLORE_BLOCKED_IDENTS = new Set([
	"__proto__",
	"constructor",
	"prototype",
	"toString",
	"valueOf",
	"hasOwnProperty",
	"isPrototypeOf",
	"propertyIsEnumerable",
	"toLocaleString",
	"__defineGetter__",
	"__defineSetter__",
	"__lookupGetter__",
	"__lookupSetter__",
]);
const EXPLORE_CONSTANTS = Object.freeze(
	Object.assign(Object.create(null), { pi: Math.PI, e: Math.E, tau: Math.PI * 2 }),
);
const EXPLORE_FUNCS = Object.freeze(
	Object.assign(Object.create(null), {
		sin: { arities: [1], fn: Math.sin },
		cos: { arities: [1], fn: Math.cos },
		tan: { arities: [1], fn: Math.tan },
		asin: { arities: [1], fn: Math.asin },
		acos: { arities: [1], fn: Math.acos },
		atan: { arities: [1], fn: Math.atan },
		sinh: { arities: [1], fn: Math.sinh },
		cosh: { arities: [1], fn: Math.cosh },
		tanh: { arities: [1], fn: Math.tanh },
		sqrt: { arities: [1], fn: Math.sqrt },
		abs: { arities: [1], fn: Math.abs },
		exp: { arities: [1], fn: Math.exp },
		log: { arities: [1, 2], fn: (x, b) => (b === undefined ? Math.log(x) : Math.log(x) / Math.log(b)) },
		ln: { arities: [1], fn: Math.log },
		floor: { arities: [1], fn: Math.floor },
		ceil: { arities: [1], fn: Math.ceil },
		min: { arities: [2], fn: Math.min },
		max: { arities: [2], fn: Math.max },
	}),
);

function isCleanExploreIdent(name) {
	return (
		typeof name === "string" &&
		name.length > 0 &&
		name.length <= 64 &&
		/^[A-Za-z_][A-Za-z0-9_]*$/u.test(name) &&
		!name.includes("__") &&
		!EXPLORE_BLOCKED_IDENTS.has(name)
	);
}

function tokenizeExploreExpr(rawExpr) {
	let s = String(rawExpr || "").trim();
	if (s.startsWith("$") && s.endsWith("$") && s.length >= 2) s = s.slice(1, -1).trim();
	s = s.replace(/−/gu, "-").replace(/\^/gu, "**");
	if (!s || s.length > EXPLORE_MAX_LEN) throw new Error("Expression empty or too long.");
	const tokens = [];
	let i = 0;
	while (i < s.length) {
		const ch = s[i];
		if (/\s/u.test(ch)) {
			i++;
			continue;
		}
		if (/[0-9.]/u.test(ch)) {
			const m = /^[0-9]*\.?[0-9]+(?:[eE][+-]?[0-9]+)?/u.exec(s.slice(i));
			if (!m) throw new Error(`Invalid number at ${i}`);
			const val = Number(m[0]);
			if (!Number.isFinite(val)) throw new Error("Non-finite number");
			tokens.push({ t: "num", v: val });
			i += m[0].length;
			continue;
		}
		if (/[A-Za-z_]/u.test(ch)) {
			const m = /^[A-Za-z_][A-Za-z0-9_]*/u.exec(s.slice(i));
			const name = m[0];
			if (!isCleanExploreIdent(name)) throw new Error(`Disallowed identifier: ${name}`);
			tokens.push({ t: "id", v: name });
			i += name.length;
			continue;
		}
		if (ch === "*" && s[i + 1] === "*") {
			tokens.push({ t: "op", v: "**" });
			i += 2;
			continue;
		}
		if ("+-*/(),".includes(ch)) {
			tokens.push({ t: ch === "(" || ch === ")" || ch === "," ? ch : "op", v: ch });
			i++;
			continue;
		}
		throw new Error(`Disallowed character in expression: ${ch}`);
	}
	return tokens;
}

function compileExprToRpn(rawExpr, allowedVars = null) {
	const tokens = tokenizeExploreExpr(rawExpr);
	let pos = 0;
	const ops = [];
	const peek = () => tokens[pos] || null;
	const consume = () => tokens[pos++];

	function parseAddSub(depth) {
		if (depth > 24) throw new Error("Expression too deep");
		parseMulDiv(depth + 1);
		while (peek() && peek().t === "op" && (peek().v === "+" || peek().v === "-")) {
			const op = consume().v;
			parseMulDiv(depth + 1);
			ops.push({ op: "bin", fn: op });
		}
	}

	function parseMulDiv(depth) {
		if (depth > 24) throw new Error("Expression too deep");
		parseUnary(depth + 1);
		while (peek() && peek().t === "op" && (peek().v === "*" || peek().v === "/")) {
			const op = consume().v;
			parseUnary(depth + 1);
			ops.push({ op: "bin", fn: op });
		}
	}

	function parseUnary(depth) {
		if (depth > 24) throw new Error("Expression too deep");
		if (peek() && peek().t === "op" && (peek().v === "+" || peek().v === "-")) {
			const op = consume().v;
			parseUnary(depth + 1);
			ops.push({ op: "unary", fn: op === "-" ? "neg" : "pos" });
			return;
		}
		parsePow(depth + 1);
	}

	function parsePow(depth) {
		if (depth > 24) throw new Error("Expression too deep");
		parsePrimary(depth + 1);
		if (peek() && peek().t === "op" && peek().v === "**") {
			consume();
			const startLen = ops.length;
			parseUnary(depth + 1);
			const rhsOps = ops.slice(startLen);
			if (rhsOps.some((o) => o.op === "bin" && o.fn === "**")) {
				throw new Error("Nested exponentiation disallowed");
			}
			if (rhsOps.length === 1 && rhsOps[0].op === "num" && Math.abs(rhsOps[0].val) > EXPLORE_MAX_EXP) {
				throw new Error("Exponent exceeds safe limit");
			}
			ops.push({ op: "bin", fn: "**" });
		}
	}

	function parsePrimary(depth) {
		if (depth > 24) throw new Error("Expression too deep");
		const tok = peek();
		if (!tok) throw new Error("Unexpected end of expression");
		if (tok.t === "num") {
			consume();
			ops.push({ op: "num", val: tok.v });
			return;
		}
		if (tok.t === "id") {
			consume();
			if (!isCleanExploreIdent(tok.v)) throw new Error(`Disallowed identifier: ${tok.v}`);
			if (peek() && peek().t === "(") {
				const spec = Object.prototype.hasOwnProperty.call(EXPLORE_FUNCS, tok.v)
					? EXPLORE_FUNCS[tok.v]
					: null;
				if (!spec) throw new Error(`Disallowed function: ${tok.v}`);
				consume(); // '('
				let arity = 0;
				if (!peek() || peek().t !== ")") {
					while (true) {
						parseAddSub(depth + 1);
						arity++;
						if (peek() && peek().t === ",") {
							consume();
							continue;
						}
						break;
					}
				}
				if (!peek() || peek().t !== ")") throw new Error("Missing ')'");
				consume();
				if (!spec.arities.includes(arity)) throw new Error(`Invalid arity for ${tok.v}`);
				ops.push({ op: "call", fn: tok.v, arity });
				return;
			}
			if (Object.prototype.hasOwnProperty.call(EXPLORE_CONSTANTS, tok.v)) {
				ops.push({ op: "const", name: tok.v, val: EXPLORE_CONSTANTS[tok.v] });
				return;
			}
			if (allowedVars !== null && !allowedVars.has(tok.v)) {
				throw new Error(`Unknown variable: ${tok.v}`);
			}
			ops.push({ op: "var", name: tok.v });
			return;
		}
		if (tok.t === "(") {
			consume();
			parseAddSub(depth + 1);
			if (!peek() || peek().t !== ")") throw new Error("Missing ')'");
			consume();
			return;
		}
		throw new Error(`Unexpected token: ${tok.v}`);
	}

	parseAddSub(0);
	if (pos !== tokens.length) throw new Error("Trailing tokens in expression");
	if (ops.length === 0 || ops.length > EXPLORE_MAX_OPS) throw new Error("RPN op count out of safe bounds");
	return ops;
}

function evaluateRpn(rpnOps, env = {}) {
	if (!Array.isArray(rpnOps) || rpnOps.length === 0 || rpnOps.length > EXPLORE_MAX_OPS) {
		throw new Error("Invalid RPN array");
	}
	const stack = [];
	for (const tok of rpnOps) {
		if (!tok || typeof tok !== "object") throw new Error("Invalid RPN token");
		if (tok.op === "num") {
			const n = Number(tok.val);
			if (!Number.isFinite(n)) throw new Error("Non-finite num");
			stack.push(n);
		} else if (tok.op === "const") {
			if (!Object.prototype.hasOwnProperty.call(EXPLORE_CONSTANTS, tok.name)) {
				throw new Error(`Disallowed constant: ${tok.name}`);
			}
			stack.push(EXPLORE_CONSTANTS[tok.name]);
		} else if (tok.op === "var") {
			if (!isCleanExploreIdent(tok.name)) throw new Error(`Disallowed var ${tok.name}`);
			const v = Object.prototype.hasOwnProperty.call(env, tok.name)
				? Number(env[tok.name])
				: Object.prototype.hasOwnProperty.call(EXPLORE_CONSTANTS, tok.name)
					? EXPLORE_CONSTANTS[tok.name]
					: NaN;
			if (!Number.isFinite(v)) throw new Error(`Missing var ${tok.name}`);
			stack.push(v);
		} else if (tok.op === "unary") {
			if (stack.length < 1) throw new Error("Stack underflow");
			const a = stack.pop();
			stack.push(tok.fn === "neg" ? -a : +a);
		} else if (tok.op === "bin") {
			if (stack.length < 2) throw new Error("Stack underflow");
			const b = stack.pop();
			const a = stack.pop();
			let r;
			if (tok.fn === "+") r = a + b;
			else if (tok.fn === "-") r = a - b;
			else if (tok.fn === "*") r = a * b;
			else if (tok.fn === "/") {
				if (b === 0) throw new Error("Division by zero");
				r = a / b;
			} else if (tok.fn === "**") {
				if (Math.abs(b) > EXPLORE_MAX_EXP) throw new Error("Exponent limit");
				r = Math.pow(a, b);
			} else {
				throw new Error(`Disallowed binary op: ${tok.fn}`);
			}
			if (!Number.isFinite(r) || Math.abs(r) > 1e100) throw new Error("Overflow");
			stack.push(r);
		} else if (tok.op === "call") {
			const spec = Object.prototype.hasOwnProperty.call(EXPLORE_FUNCS, tok.fn)
				? EXPLORE_FUNCS[tok.fn]
				: null;
			if (!spec || !spec.arities.includes(tok.arity) || stack.length < tok.arity) {
				throw new Error(`Disallowed function: ${tok.fn}`);
			}
			const args = stack.splice(stack.length - tok.arity, tok.arity);
			const r = spec.fn(...args);
			if (!Number.isFinite(r) || Math.abs(r) > 1e100) throw new Error("Non-finite call");
			stack.push(r);
		} else {
			throw new Error(`Disallowed RPN op: ${tok.op}`);
		}
	}
	if (stack.length !== 1 || !Number.isFinite(stack[0])) throw new Error("Bad RPN stack");
	return stack[0];
}

function parseKvPairsDsl(text) {
	const out = Object.create(null);
	const re = /([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^,\s]+))/gu;
	let m;
	while ((m = re.exec(text)) !== null) {
		const k = m[1];
		if (!isCleanExploreIdent(k)) continue;
		if (m[2] !== undefined || m[3] !== undefined) {
			out[k] = m[2] ?? m[3];
		} else {
			const num = Number(m[4]);
			out[k] = Number.isFinite(num) ? num : m[4];
		}
	}
	return out;
}

function compileExplorableSpecNode(spec) {
	if (!spec || typeof spec !== "object") throw new Error("Invalid spec");
	const titleRaw = String(spec.title || "").trim();
	const id = slugifySafe(spec.id || titleRaw || "explorable-lab", "explorable-lab");
	const title = titleRaw || prettifyName(id);
	const prompt = String(spec.prompt || "").trim();
	const params = [];
	const paramNames = new Set();
	const defaultEnv = Object.create(null);
	for (const p of Array.isArray(spec.params) ? spec.params : []) {
		if (!p || typeof p !== "object") continue;
		const name = String(p.name || "").trim();
		if (
			!isCleanExploreIdent(name) ||
			Object.prototype.hasOwnProperty.call(EXPLORE_CONSTANTS, name) ||
			Object.prototype.hasOwnProperty.call(EXPLORE_FUNCS, name)
		) {
			throw new Error(`Invalid param: ${name}`);
		}
		const pmin = Number(p.min ?? 0);
		const pmax = Number(p.max ?? 1);
		if (!Number.isFinite(pmin) || !Number.isFinite(pmax) || pmax <= pmin) throw new Error("Bad range");
		const step = Number(p.step ?? (pmax - pmin) / 50);
		const defVal = Math.max(pmin, Math.min(pmax, Number(p.default ?? (pmin + pmax) / 2)));
		paramNames.add(name);
		defaultEnv[name] = defVal;
		params.push({
			name,
			label: String(p.label || name).trim(),
			min: pmin,
			max: pmax,
			step: step > 0 ? step : (pmax - pmin) / 50,
			default: defVal,
			unit: String(p.unit || "").trim(),
		});
	}
	const outAllowed = paramNames;
	const outputs = [];
	for (let i = 0; i < (Array.isArray(spec.outputs) ? spec.outputs.length : 0); i++) {
		const o = spec.outputs[i];
		if (!o || typeof o !== "object") continue;
		const expr = String(o.expr || "").trim();
		const rpn = compileExprToRpn(expr, outAllowed);
		const entry = {
			label: String(o.label || `Output ${i + 1}`).trim(),
			expr,
			rpn,
			unit: String(o.unit || "").trim(),
		};
		if (Object.keys(defaultEnv).length > 0) {
			try {
				entry.defaultValue = Number(evaluateRpn(rpn, defaultEnv).toFixed(6));
			} catch {
				// ignore runtime domain error at default
			}
		}
		outputs.push(entry);
	}
	let curve = null;
	if (spec.curve && typeof spec.curve === "object") {
		const c = spec.curve;
		const xVar = String(c.xVar || "x").trim();
		if (
			!isCleanExploreIdent(xVar) ||
			Object.prototype.hasOwnProperty.call(EXPLORE_CONSTANTS, xVar) ||
			Object.prototype.hasOwnProperty.call(EXPLORE_FUNCS, xVar)
		) {
			throw new Error("Bad xVar");
		}
		const xMin = Number(c.xMin ?? 0);
		const xMax = Number(c.xMax ?? 1);
		if (!Number.isFinite(xMin) || !Number.isFinite(xMax) || xMax <= xMin) throw new Error("Bad x range");
		const rawSamples = Number(c.samples ?? 64);
		const samples = Number.isFinite(rawSamples) ? Math.max(2, Math.min(200, Math.round(rawSamples))) : 64;
		const curveVars = new Set([...paramNames, xVar]);
		const exactExpr = String(c.exactExpr || "").trim();
		const exactRpn = compileExprToRpn(exactExpr, curveVars);
		const approxExpr = String(c.approxExpr || "").trim();
		const approxRpn = approxExpr ? compileExprToRpn(approxExpr, curveVars) : null;
		const regimeThreshold =
			c.regimeThreshold !== undefined && c.regimeThreshold !== null && String(c.regimeThreshold).trim() !== ""
				? Number(c.regimeThreshold)
				: null;
		curve = {
			xVar,
			xMin,
			xMax,
			samples,
			exactExpr,
			exactRpn,
			approxExpr: approxExpr || null,
			approxRpn,
			regimeThreshold: Number.isFinite(regimeThreshold) ? regimeThreshold : null,
			yLabel: String(c.yLabel || "y").trim(),
		};
	}
	let limitCheck = null;
	if (spec.limitCheck && typeof spec.limitCheck === "object") {
		const lc = spec.limitCheck;
		const lVar = String(lc.var || params[0]?.name || "x").trim();
		if (!isCleanExploreIdent(lVar)) throw new Error("Bad limitCheck.var");
		const rawTarget = lc.target ?? 0;
		const numTarget = Number(rawTarget);
		const target = typeof rawTarget === "number" || (String(rawTarget).trim() !== "" && Number.isFinite(numTarget))
			? numTarget
			: String(rawTarget).trim();
		const expectedExpr = String(lc.expectedExpr || "").trim();
		const limVars = new Set([...paramNames, lVar]);
		const expectedRpn = expectedExpr ? compileExprToRpn(expectedExpr, limVars) : null;
		let expectedVal =
			lc.expectedVal !== undefined && lc.expectedVal !== null && Number.isFinite(Number(lc.expectedVal))
				? Number(lc.expectedVal)
				: null;
		if (expectedVal === null && expectedRpn) {
			const evalEnv = Object.assign(Object.create(null), defaultEnv);
			if (typeof target === "number" && Number.isFinite(target)) evalEnv[lVar] = target;
			else if (/^\+?inf|oo$/iu.test(String(target))) evalEnv[lVar] = 1e6;
			else if (/^-inf|-oo$/iu.test(String(target))) evalEnv[lVar] = -1e6;
			try {
				expectedVal = evaluateRpn(expectedRpn, evalEnv);
			} catch {
				expectedVal = null;
			}
		}
		limitCheck = {
			question: String(lc.question || `Predict the limiting value as ${lVar} -> ${target}.`).trim(),
			var: lVar,
			target,
			expectedExpr: expectedExpr || null,
			expectedRpn,
			expectedVal,
			hint: String(lc.hint || "Check the dominant term in the limiting regime.").trim(),
		};
	}
	return { id, title, prompt, params, outputs, curve, limitCheck };
}

function parseExplorableBlocks(markdownText) {
	if (!markdownText || !markdownText.includes(":::explore")) return [];
	const lines = String(markdownText).split(/\r?\n/u);
	const specs = [];
	let inCode = false;
	let fenceChar = "";
	let inExplore = false;
	let fenceArg = "";
	let buf = [];

	for (const line of lines) {
		const trimmed = line.trim();
		if (!inExplore) {
			const cf = /^\s*(`{3,}|~{3,})/u.exec(line);
			if (cf) {
				const ch = cf[1][0];
				if (!inCode) {
					inCode = true;
					fenceChar = ch;
				} else if (ch === fenceChar) {
					inCode = false;
					fenceChar = "";
				}
				continue;
			}
			if (inCode) continue;
			const om = /^:::explore(?:\s+(.*))?$/u.exec(trimmed);
			if (om) {
				inExplore = true;
				fenceArg = (om[1] || "").trim();
				buf = [];
				continue;
			}
		} else if (/^:::\s*$/u.test(trimmed)) {
			inExplore = false;
			let cleaned = buf.join("\n").trim();
			buf = [];
			if (cleaned.startsWith("```")) {
				const cl = cleaned.split(/\r?\n/u);
				if (cl.length >= 2 && cl[cl.length - 1].trim().startsWith("```")) {
					cleaned = cl.slice(1, -1).join("\n").trim();
				}
			}
			try {
				let rawSpec;
				if (cleaned.startsWith("{")) {
					rawSpec = JSON.parse(cleaned);
					if (fenceArg && !rawSpec.id) rawSpec.id = fenceArg;
				} else {
					rawSpec = { params: [], outputs: [] };
					if (fenceArg) rawSpec.id = fenceArg;
					const curve = {};
					const lim = {};
					for (const rl of cleaned.split(/\r?\n/u)) {
						const l = rl.trim();
						if (!l || l.startsWith("#") || !l.includes(":")) continue;
						const idx = l.indexOf(":");
						const kRaw = l.slice(0, idx).trim();
						const kLow = kRaw.toLowerCase();
						const vStr = l.slice(idx + 1).trim();
						if (kLow === "id" || kLow === "title" || kLow === "prompt") {
							rawSpec[kLow] = vStr.replace(/^["']|["']$/gu, "");
						} else if (kLow.startsWith("param ")) {
							const pobj = parseKvPairsDsl(vStr);
							pobj.name = kRaw.slice(6).trim();
							rawSpec.params.push(pobj);
						} else if (kLow.startsWith("output")) {
							const oobj = parseKvPairsDsl(vStr);
							if (!oobj.label && kRaw.includes(" ")) oobj.label = kRaw.slice(kRaw.indexOf(" ") + 1).trim();
							rawSpec.outputs.push(oobj);
						} else if (kLow === "curve") {
							Object.assign(curve, parseKvPairsDsl(vStr));
						} else if (kLow === "limitcheck" || kLow === "limit_check" || kLow === "limit") {
							Object.assign(lim, parseKvPairsDsl(vStr));
						}
					}
					if (Object.keys(curve).length > 0) rawSpec.curve = curve;
					if (Object.keys(lim).length > 0) rawSpec.limitCheck = lim;
				}
				specs.push(compileExplorableSpecNode(rawSpec));
			} catch {
				// Skip malformed :::explore block gracefully
			}
		} else {
			buf.push(line);
		}
	}
	return specs;
}

function scanExplorables(chapters) {
	const out = {};
	for (const ch of chapters) {
		if (ch.ext !== "md" || !ch.content || !ch.content.includes(":::explore")) continue;
		const compiled = parseExplorableBlocks(ch.content);
		if (compiled.length > 0) {
			out[ch.slug] = compiled;
		}
	}
	return out;
}

// --- Pure-Node ZIP pack-manifest.json reader for packs/*.syntopik-pack.zip ---

function readZipEntryText(zipPath, targetName, maxBytes = 4 * 1024 * 1024) {
	try {
		const buf = readFileSync(zipPath);
		if (buf.length < 22) return null;
		const minEocd = Math.max(0, buf.length - 65557);
		let eocd = -1;
		for (let i = buf.length - 22; i >= minEocd; i--) {
			if (buf.readUInt32LE(i) === 0x06054b50) {
				eocd = i;
				break;
			}
		}
		if (eocd === -1) return null;
		const cdSize = buf.readUInt32LE(eocd + 12);
		const cdOffset = buf.readUInt32LE(eocd + 16);
		let pos = cdOffset;
		const cdEnd = Math.min(buf.length, cdOffset + cdSize);
		while (pos + 46 <= cdEnd) {
			if (buf.readUInt32LE(pos) !== 0x02014b50) break;
			const compMethod = buf.readUInt16LE(pos + 10);
			const compSize = buf.readUInt32LE(pos + 20);
			const uncompSize = buf.readUInt32LE(pos + 24);
			const nameLen = buf.readUInt16LE(pos + 28);
			const extraLen = buf.readUInt16LE(pos + 30);
			const commentLen = buf.readUInt16LE(pos + 32);
			const localOffset = buf.readUInt32LE(pos + 42);
			const entryName = buf.subarray(pos + 46, pos + 46 + nameLen).toString("utf8");
			if (entryName === targetName) {
				if (uncompSize > maxBytes || compSize > maxBytes) return null;
				if (localOffset + 30 > buf.length || buf.readUInt32LE(localOffset) !== 0x04034b50) return null;
				const lNameLen = buf.readUInt16LE(localOffset + 26);
				const lExtraLen = buf.readUInt16LE(localOffset + 28);
				const dataStart = localOffset + 30 + lNameLen + lExtraLen;
				const dataSlice = buf.subarray(dataStart, dataStart + compSize);
				const raw =
					compMethod === 0
						? dataSlice
						: compMethod === 8
							? inflateRawSync(dataSlice, { maxOutputLength: maxBytes })
							: null;
				return raw ? raw.toString("utf8") : null;
			}
			pos += 46 + nameLen + extraLen + commentLen;
		}
	} catch {
		return null;
	}
	return null;
}

function readZipEntryJson(zipPath, targetName, maxBytes = 4 * 1024 * 1024) {
	const txt = readZipEntryText(zipPath, targetName, maxBytes);
	if (!txt) return null;
	try {
		return JSON.parse(txt);
	} catch {
		return null;
	}
}

function scanStarterPacks() {
	const candidateDirs = [];
	const seenDirs = new Set();
	for (const d of [path.join(root, "packs"), path.join(repoRoot, "packs")]) {
		const res = path.resolve(d);
		if (existsSync(res) && !seenDirs.has(res)) {
			seenDirs.add(res);
			candidateDirs.push(res);
		}
	}
	const out = [];
	const seenSlugs = new Set();
	for (const pdir of candidateDirs) {
		let entries;
		try {
			entries = readdirSync(pdir, { withFileTypes: true });
		} catch {
			continue;
		}
		for (const ent of entries) {
			if (!ent.isFile() || !ent.name.endsWith(".syntopik-pack.zip")) continue;
			const slug = ent.name.slice(0, -".syntopik-pack.zip".length).trim().toLowerCase();
			if (!/^[a-z0-9][a-z0-9_-]{0,63}$/u.test(slug) || seenSlugs.has(slug)) continue;
			const full = path.join(pdir, ent.name);
			let sizeBytes = 0;
			try {
				sizeBytes = statSync(full).size;
			} catch {
				continue;
			}
			const pm = readZipEntryJson(full, "pack-manifest.json");
			const compFiles = Array.isArray(pm?.companionFiles) ? pm.companionFiles.map(String) : [];
			const countJsonl = (name) => {
				const txt = readZipEntryText(full, name, 512 * 1024);
				return txt ? txt.split(/\r?\n/u).filter((l) => l.trim()).length : 0;
			};
			const meta = {
				slug,
				id: String(pm?.id || slug),
				title: String(pm?.title || prettifyName(slug)),
				subject: String(pm?.subject || "General"),
				author: String(pm?.author || "Syntopik"),
				license: String(pm?.license || "CC-BY-4.0"),
				filename: ent.name,
				sizeBytes,
				propositionCount: Array.isArray(pm?.propositions) ? pm.propositions.length : 0,
				chainCount: Array.isArray(pm?.chains) ? pm.chains.length : 0,
				chapterCount: Array.isArray(pm?.chapters)
					? pm.chapters.length
					: compFiles.filter((cf) => cf.startsWith("chapters/") && cf.endsWith(".md")).length,
				forkCount: Array.isArray(pm?.forks) ? pm.forks.length : countJsonl("forks.jsonl"),
				arenaCount: Array.isArray(pm?.arenas) ? pm.arenas.length : countJsonl("arenas.jsonl"),
				derivationCount: Array.isArray(pm?.derivations) ? pm.derivations.length : 0,
				examCount: Array.isArray(pm?.exams) ? pm.exams.length : 0,
			};
			if (typeof pm?.description === "string") meta.description = pm.description;
			seenSlugs.add(slug);
			out.push(meta);
		}
	}
	out.sort((a, b) => a.slug.localeCompare(b.slug));
	return out;
}

function loadArchetypesNode() {
	const candidates = [
		path.join(root, "syntopik", "data", "archetypes.json"),
		path.join(repoRoot, "syntopik", "data", "archetypes.json"),
		path.resolve(repoRoot, "..", "data", "archetypes.json"),
		path.join(repoRoot, "syntopik", "_bundle", "data", "archetypes.json"),
	];
	for (const cand of candidates) {
		try {
			if (!existsSync(cand)) continue;
			const raw = JSON.parse(readFileSync(cand, "utf8"));
			const arr = Array.isArray(raw) ? raw : (Array.isArray(raw?.archetypes) ? raw.archetypes : []);
			return arr.filter((a) => a && typeof a === "object" && a.id);
		} catch {
			// Skip unreadable candidate
		}
	}
	return [];
}

function normSpaceLower(text) {
	return String(text || "").trim().toLowerCase().replace(/\s+/gu, " ");
}

function escapeRegExp(s) {
	return s.replace(/[.*+?^${}()|[\]\\]/gu, "\\$&");
}

// Keyword specs are pure functions of the keyword, so compile each one once per run
// instead of once per (proposition x keyword) pair.
const ROSETTA_KW_SPEC_CACHE = new Map();
let rosettaCompactBlobKey = null;
let rosettaCompactBlobVal = "";

function rosettaKeywordSpec(k) {
	let spec = ROSETTA_KW_SPEC_CACHE.get(k);
	if (spec) return spec;
	if (/[^a-z0-9\s-]/u.test(k)) {
		spec = { hasSymbol: true, compact: k.replace(/\s+/gu, ""), re: null };
	} else {
		const parts = k.split(/[\s-]+/u).filter(Boolean).map(escapeRegExp);
		spec = {
			hasSymbol: false,
			compact: "",
			re: parts.length ? new RegExp(`(?<![a-z0-9])${parts.join("[\\s\\-_]+")}(?![a-z0-9])`, "u") : null,
		};
	}
	ROSETTA_KW_SPEC_CACHE.set(k, spec);
	return spec;
}

function keywordMatchesRosetta(kw, rawBlob, wordBlob) {
	const k = String(kw || "").trim().toLowerCase();
	if (!k) return false;
	const spec = rosettaKeywordSpec(k);
	if (spec.hasSymbol) {
		if (rawBlob.includes(k)) return true;
		if (rosettaCompactBlobKey !== rawBlob) {
			rosettaCompactBlobKey = rawBlob;
			rosettaCompactBlobVal = rawBlob.replace(/\s+/gu, "");
		}
		return spec.compact.length >= 4 && rosettaCompactBlobVal.includes(spec.compact);
	}
	return spec.re ? spec.re.test(wordBlob) : false;
}

function keywordWeightRosetta(kw) {
	const k = String(kw || "").trim().toLowerCase();
	const hasSymbol = /[^a-z0-9\s-]/u.test(k);
	const isPhrase = k.includes(" ") || k.includes("-");
	if (hasSymbol || isPhrase || k.length >= 9) return 1.45;
	if (k.length >= 5) return 1.15;
	return 0.95;
}

function domainContextAlignRosetta(prop, slot) {
	const subjChap = normSpaceLower(`${prop?.subject || ""} ${prop?.chapter || ""}`);
	const slotMeta = normSpaceLower(`${slot?.domain || ""} ${slot?.title || ""}`);
	if (!subjChap || !slotMeta) return 0;
	const stop = new Set(["and", "the", "for", "with", "from", "general", "system", "systems", "theory", "linear"]);
	const pToks = new Set((subjChap.match(/[a-z]{3,}/gu) || []).filter((t) => !stop.has(t)));
	const sToks = (slotMeta.match(/[a-z]{3,}/gu) || []).filter((t) => !stop.has(t));
	for (const t of sToks) {
		if (pToks.has(t)) return 0.08;
	}
	return 0;
}

function matchPropositionArchetypesNode(prop, archetypes, minScore = 0.35) {
	if (!prop || typeof prop !== "object") return [];
	const tags = (Array.isArray(prop.tags) ? prop.tags : [])
		.filter((t) => typeof t === "string" && t.trim())
		.map((t) => t.trim().toLowerCase());
	const tagSet = new Set(tags);
	const aliases = (Array.isArray(prop.aliases) ? prop.aliases : [])
		.filter((a) => typeof a === "string" && a.trim())
		.map((a) => a.trim());
	const cueParts = [];
	for (const c of Array.isArray(prop.cues) ? prop.cues : []) {
		if (c && typeof c === "object") {
			if (c.q) cueParts.push(String(c.q));
			if (c.a) cueParts.push(String(c.a));
		}
	}
	const headlineRaw = normSpaceLower([prop.title || "", aliases.join(" "), tags.join(" ")].join(" "));
	const headlineWord = headlineRaw.replace(/[_/]+/gu, " ");
	const fullRaw = normSpaceLower([
		prop.title || "",
		prop.statement || "",
		aliases.join(" "),
		tags.join(" "),
		prop.notes || "",
		cueParts.join(" "),
	].join(" "));
	const fullWord = fullRaw.replace(/[_/]+/gu, " ");
	if (!fullRaw) return [];

	const matches = [];
	for (const arch of archetypes) {
		const archId = String(arch.id || "").trim();
		if (!archId) continue;
		const explicitTag = tagSet.has(archId.toLowerCase());
		let bestSlot = null;
		let bestScore = 0;

		for (const slot of Array.isArray(arch.domains) ? arch.domains : []) {
			if (!slot || typeof slot !== "object") continue;
			const kws = (Array.isArray(slot.keywords) ? slot.keywords : []).map((k) => String(k || "")).filter(Boolean);
			let matchedCount = 0;
			let weightedHits = 0;
			let headlineHits = 0;
			for (const kw of kws) {
				if (keywordMatchesRosetta(kw, fullRaw, fullWord)) {
					matchedCount += 1;
					weightedHits += keywordWeightRosetta(kw);
					if (keywordMatchesRosetta(kw, headlineRaw, headlineWord)) headlineHits += 1;
				}
			}
			if (matchedCount === 0 && !explicitTag) continue;
			const base = weightedHits > 0 ? 1 - Math.exp(-0.42 * weightedHits) : 0;
			const headBoost = Math.min(0.22, 0.11 * headlineHits);
			const ctxBoost = domainContextAlignRosetta(prop, slot);
			let score = Math.min(0.99, base + headBoost + ctxBoost);
			if (!explicitTag && matchedCount === 1 && weightedHits < 1.35 && headlineHits === 0) {
				score = Math.min(score, 0.32);
			}
			if (explicitTag) score = Math.min(0.99, Math.max(score, 0.88 + ctxBoost));
			if (score > bestScore) {
				bestScore = score;
				bestSlot = slot;
			}
		}
		if (!bestSlot && explicitTag && Array.isArray(arch.domains) && arch.domains.length > 0) {
			bestSlot = arch.domains[0];
			bestScore = 0.88;
		}
		if (bestSlot && bestScore >= minScore) {
			matches.push({
				archetypeId: archId,
				archetypeName: String(arch.name || archId),
				canonicalEquation: String(arch.canonicalEquation || ""),
				domainSlot: String(bestSlot.domain || "General"),
				variableMap: bestSlot.variableMap && typeof bestSlot.variableMap === "object" ? { ...bestSlot.variableMap } : {},
				breaksAt: Array.isArray(arch.breaksAt) ? arch.breaksAt.map(String) : [],
				bijectionQuestion: String(arch.bijectionQuestion || ""),
				score: Number(bestScore.toFixed(4)),
			});
		}
	}
	matches.sort((a, b) => b.score - a.score || a.archetypeId.localeCompare(b.archetypeId));
	return matches;
}

function buildBijectionNode(srcMap, tgtMap) {
	const out = {};
	for (const [role, srcSym] of Object.entries(srcMap || {})) {
		if (tgtMap && Object.prototype.hasOwnProperty.call(tgtMap, role)) {
			out[role] = `${srcSym} <-> ${tgtMap[role]}`;
		}
	}
	return out;
}

function isCrossScopeNode(pa, pb, domA, domB) {
	const da = normSpaceLower(domA);
	const db = normSpaceLower(domB);
	if (da && db && da !== db) return true;
	const sa = normSpaceLower(pa?.subject);
	const sb = normSpaceLower(pb?.subject);
	if (sa && sb && sa !== sb) return true;
	const ca = normSpaceLower(pa?.chapter);
	const cb = normSpaceLower(pb?.chapter);
	if (ca && cb && ca !== cb) return true;
	return false;
}

function bestSlotForPropNode(prop, arch) {
	const domains = Array.isArray(arch?.domains) ? arch.domains.filter((d) => d && typeof d === "object") : [];
	if (domains.length === 0) return null;
	if (!prop) return domains[0];
	const ms = matchPropositionArchetypesNode(prop, [arch], 0);
	if (ms.length > 0) {
		const hit = domains.find((d) => String(d.domain || "") === ms[0].domainSlot);
		if (hit) return hit;
	}
	let best = domains[0];
	let bestAl = -1;
	for (const d of domains) {
		const al = domainContextAlignRosetta(prop, d);
		if (al > bestAl) {
			bestAl = al;
			best = d;
		}
	}
	return best;
}

function computeRosettaHubData(propositions, rawChains) {
	const archetypes = loadArchetypesNode();
	const archById = new Map(archetypes.map((a) => [String(a.id), a]));
	const propsById = new Map();
	const activeProps = [];
	for (const p of propositions) {
		const pid = String(p?.id || "").trim();
		if (!pid) continue;
		propsById.set(pid, p);
		if (!p.archived) activeProps.push(p);
	}

	const persistedMap = new Map();
	for (const raw of rawChains) {
		if (!raw || typeof raw !== "object") continue;
		if (String(raw.relation || "").trim().toLowerCase() !== "isomorphic_to") continue;
		const conc = String(raw.conclusion || raw.to || "").trim();
		const prems = Array.isArray(raw.premises)
			? raw.premises.map((x) => String(x || "").trim()).filter((x) => x && x !== conc)
			: (raw.from && String(raw.from).trim() !== conc ? [String(raw.from).trim()] : []);
		if (!conc || prems.length === 0) continue;
		for (const prem of prems) {
			const [a, b] = prem <= conc ? [prem, conc] : [conc, prem];
			const pairKey = `${a}\u0000${b}`;
			const breaks = Array.isArray(raw.breaksAt) ? raw.breaksAt.map((x) => String(x || "").trim()).filter(Boolean) : [];
			persistedMap.set(pairKey, {
				sourceId: a,
				targetId: b,
				archetypeId: String(raw.archetypeId || "").trim(),
				breaksAt: breaks,
				why: String(raw.why || raw.note || "").trim(),
			});
		}
	}

	const byArch = new Map();
	for (const p of activeProps) {
		const ms = matchPropositionArchetypesNode(p, archetypes, 0.35);
		for (const m of ms) {
			if (!byArch.has(m.archetypeId)) byArch.set(m.archetypeId, []);
			byArch.get(m.archetypeId).push({ prop: p, match: m });
		}
	}

	const chordsByPair = new Map();
	for (const [archId, entries] of [...byArch.entries()].sort((a, b) => a[0].localeCompare(b[0]))) {
		const arch = archById.get(archId);
		entries.sort((x, y) => String(x.prop.id).localeCompare(String(y.prop.id)));
		for (let i = 0; i < entries.length; i += 1) {
			for (let j = i + 1; j < entries.length; j += 1) {
				const { prop: pa, match: ma } = entries[i];
				const { prop: pb, match: mb } = entries[j];
				if (!isCrossScopeNode(pa, pb, ma.domainSlot, mb.domainSlot)) continue;
				const [srcP, srcM, tgtP, tgtM] = String(pa.id) <= String(pb.id) ? [pa, ma, pb, mb] : [pb, mb, pa, ma];
				const pairKey = `${srcP.id}\u0000${tgtP.id}`;
				const pers = persistedMap.get(pairKey);
				const isPers = Boolean(pers && (!pers.archetypeId || pers.archetypeId === archId));
				const crossBonus = normSpaceLower(srcM.domainSlot) !== normSpaceLower(tgtM.domainSlot) ? 0.08 : 0;
				const pairScore = Number(Math.min(0.99, Math.sqrt(srcM.score * tgtM.score) + crossBonus).toFixed(4));
				const chord = {
					id: `iso-${archId}-${srcP.id}--${tgtP.id}`,
					sourceId: String(srcP.id),
					targetId: String(tgtP.id),
					sourceTitle: String(srcP.title || srcP.id),
					targetTitle: String(tgtP.title || tgtP.id),
					sourceDomain: srcM.domainSlot,
					targetDomain: tgtM.domainSlot,
					sourceChapter: String(srcP.chapter || "General"),
					targetChapter: String(tgtP.chapter || "General"),
					sourceSubject: String(srcP.subject || "General"),
					targetSubject: String(tgtP.subject || "General"),
					archetypeId: archId,
					archetypeName: String(arch?.name || archId),
					canonicalEquation: String(arch?.canonicalEquation || ""),
					variableBijection: buildBijectionNode(srcM.variableMap, tgtM.variableMap),
					breaksAt: isPers && pers?.breaksAt?.length ? pers.breaksAt : (Array.isArray(arch?.breaksAt) ? arch.breaksAt : []),
					bijectionQuestion: String(arch?.bijectionQuestion || ""),
					score: pairScore,
					persisted: isPers,
				};
				const prev = chordsByPair.get(pairKey);
				if (
					!prev ||
					(chord.persisted && !prev.persisted) ||
					(chord.persisted === prev.persisted && chord.score > prev.score)
				) {
					chordsByPair.set(pairKey, chord);
				}
			}
		}
	}

	for (const [pairKey, pers] of persistedMap.entries()) {
		if (chordsByPair.has(pairKey)) {
			const ex = chordsByPair.get(pairKey);
			ex.persisted = true;
			if (pers.breaksAt.length > 0) ex.breaksAt = pers.breaksAt;
			continue;
		}
		const srcP = propsById.get(pers.sourceId) || { id: pers.sourceId, title: pers.sourceId, subject: "General", chapter: "General" };
		const tgtP = propsById.get(pers.targetId) || { id: pers.targetId, title: pers.targetId, subject: "General", chapter: "General" };
		const aid = pers.archetypeId || "custom_isomorphism";
		const arch = archById.get(aid) || null;
		const slotA = bestSlotForPropNode(srcP, arch) || {};
		const slotB = bestSlotForPropNode(tgtP, arch) || {};
		chordsByPair.set(pairKey, {
			id: `iso-${aid}-${pers.sourceId}--${pers.targetId}`,
			sourceId: pers.sourceId,
			targetId: pers.targetId,
			sourceTitle: String(srcP.title || pers.sourceId),
			targetTitle: String(tgtP.title || pers.targetId),
			sourceDomain: String(slotA.domain || srcP.subject || "General"),
			targetDomain: String(slotB.domain || tgtP.subject || "General"),
			sourceChapter: String(srcP.chapter || "General"),
			targetChapter: String(tgtP.chapter || "General"),
			sourceSubject: String(srcP.subject || "General"),
			targetSubject: String(tgtP.subject || "General"),
			archetypeId: aid,
			archetypeName: arch ? String(arch.name || aid) : aid.replace(/_/gu, " "),
			canonicalEquation: arch ? String(arch.canonicalEquation || "") : "",
			variableBijection: buildBijectionNode(slotA.variableMap || {}, slotB.variableMap || {}),
			breaksAt: pers.breaksAt.length ? pers.breaksAt : (Array.isArray(arch?.breaksAt) ? arch.breaksAt : []),
			bijectionQuestion: arch ? String(arch.bijectionQuestion || "") : "",
			score: 1.0,
			persisted: true,
		});
	}

	const chords = [...chordsByPair.values()].sort(
		(a, b) =>
			(a.persisted === b.persisted ? 0 : a.persisted ? -1 : 1) ||
			b.score - a.score ||
			a.archetypeId.localeCompare(b.archetypeId) ||
			a.sourceId.localeCompare(b.sourceId),
	);

	return {
		ok: true,
		chords,
		archetypesCount: archetypes.length,
		chordCount: chords.length,
		persistedCount: chords.filter((c) => c.persisted).length,
		discoveredCount: chords.filter((c) => !c.persisted).length,
	};
}

function readSyncStatusNode(kgDirPath) {
	const raw = readJsonObject(path.join(kgDirPath, "sync-state.json"));
	const lastExportedAt = typeof raw?.lastExportedAt === "string" && raw.lastExportedAt.trim() ? raw.lastExportedAt.trim() : null;
	const lastMergedAt = typeof raw?.lastMergedAt === "string" && raw.lastMergedAt.trim() ? raw.lastMergedAt.trim() : null;
	const lastSyncedAt =
		lastExportedAt && lastMergedAt
			? (lastExportedAt >= lastMergedAt ? lastExportedAt : lastMergedAt)
			: (lastMergedAt || lastExportedAt || null);
	return {
		ok: true,
		initialized: Boolean(raw && typeof raw.deviceId === "string" && raw.deviceId.trim()),
		deviceId: typeof raw?.deviceId === "string" && raw.deviceId.trim() ? raw.deviceId.trim() : null,
		lamportClock: Number(raw?.lamportClock || 0),
		lastExportedAt,
		lastMergedAt,
		lastSyncedAt,
		mergedDeltaCount: Array.isArray(raw?.mergedDeltaHashes) ? raw.mergedDeltaHashes.length : 0,
	};
}

function computeInterleaveStatsNode(rawSessions) {
	const rows = Array.isArray(rawSessions) ? rawSessions : [];
	const totalSessions = rows.length;
	const completedSessions = rows.filter((r) => String(r?.status || "").toLowerCase() === "completed").length;
	let t1Total = 0;
	let t1Correct = 0;
	let s2Total = 0;
	let s2Correct = 0;
	const latenciesMs = [];
	const confusionMatrix = {};
	const chaptersSeen = new Set();
	let latestMs = null;
	let latestIso = null;

	for (const r of rows) {
		for (const k of ["completedAt", "createdAt"]) {
			if (typeof r?.[k] === "string" && r[k].trim()) {
				const parsed = Date.parse(r[k]);
				if (Number.isFinite(parsed) && (latestMs === null || parsed > latestMs)) {
					latestMs = parsed;
					latestIso = r[k];
				}
			}
		}
		for (const it of Array.isArray(r?.items) ? r.items : []) {
			if (!it || typeof it !== "object") continue;
			const trueCh = String(it.sealedChapter || "").trim();
			const s1 = it.step1Result && typeof it.step1Result === "object" ? it.step1Result : null;
			const s2 = it.step2Result && typeof it.step2Result === "object" ? it.step2Result : null;
			if (s1) {
				t1Total += 1;
				if (trueCh) chaptersSeen.add(trueCh);
				const dur = Number(s1.durationMs);
				if (Number.isFinite(dur) && dur > 0) latenciesMs.push(Math.round(dur));
				if (s1.correct) {
					t1Correct += 1;
					if (trueCh) {
						confusionMatrix[trueCh] = confusionMatrix[trueCh] || {};
						confusionMatrix[trueCh][trueCh] = (confusionMatrix[trueCh][trueCh] || 0) + 1;
					}
				} else {
					const gCh = String(s1.confusedChapter || (s1.dontKnow ? "I don't know" : "Other"));
					if (trueCh) {
						confusionMatrix[trueCh] = confusionMatrix[trueCh] || {};
						confusionMatrix[trueCh][gCh] = (confusionMatrix[trueCh][gCh] || 0) + 1;
					}
				}
			}
			if (s2) {
				s2Total += 1;
				if (s2.correct) s2Correct += 1;
			}
		}
	}

	const t1Acc = t1Total > 0 ? Number(((t1Correct / t1Total) * 100).toFixed(1)) : null;
	const s2Acc = s2Total > 0 ? Number(((s2Correct / s2Total) * 100).toFixed(1)) : null;
	let medMs = null;
	if (latenciesMs.length > 0) {
		latenciesMs.sort((a, b) => a - b);
		const mid = Math.floor(latenciesMs.length / 2);
		medMs = latenciesMs.length % 2 === 1 ? latenciesMs[mid] : Math.round((latenciesMs[mid - 1] + latenciesMs[mid]) / 2);
	}
	const medSec = medMs !== null ? Number((medMs / 1000).toFixed(1)) : null;
	const daysSince = latestMs !== null ? Math.max(0, Math.floor((Date.now() - latestMs) / 86400000)) : null;

	const topConfusionPairs = [];
	for (const [tc, guesses] of Object.entries(confusionMatrix)) {
		for (const [gc, cnt] of Object.entries(guesses || {})) {
			if (gc !== tc) {
				topConfusionPairs.push({ trueChapter: tc, confusedWithChapter: gc, count: cnt });
			}
		}
	}
	topConfusionPairs.sort(
		(a, b) => b.count - a.count || a.trueChapter.localeCompare(b.trueChapter) || a.confusedWithChapter.localeCompare(b.confusedWithChapter),
	);

	return {
		ok: true,
		totalSessions,
		completedSessions,
		totalItemsChecked: t1Total,
		tier1Correct: t1Correct,
		tier1AccuracyPct: t1Acc,
		principleAccuracyPct: t1Acc,
		step2Correct: s2Correct,
		step2Total: s2Total,
		step2AccuracyPct: s2Acc,
		medianIdentifyMs: medMs,
		medianIdentifySec: medSec,
		lastSessionAt: latestIso,
		daysSinceLastInterleave: daysSince,
		distinctChaptersPracticed: chaptersSeen.size,
		confusionMatrix,
		topConfusionPairs: topConfusionPairs.slice(0, 5),
	};
}

function computeReadinessSummaryNode(
	propositions,
	chainSummary,
	chapterTrackers,
	mistakes,
	forksDue,
	examAttempts,
	calibration,
	evidenceSummary,
	interleaveStats,
	learnerProfile,
	kgDirPath,
	todayStr,
) {
	const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
	const clipStr = (s, max = 120) => {
		const clean = String(s || "").replace(/\s+/gu, " ").trim();
		return clean.length <= max ? clean : `${clean.slice(0, Math.max(8, max - 3)).trim()}...`;
	};

	const studyCfg = readJsonObject(path.join(kgDirPath, "study-config.json"))
		|| readJsonObject(path.join(kgDirPath, "exam-config.json"))
		|| {};
	const cfgSrc = existsSync(path.join(kgDirPath, "study-config.json"))
		? "study-config.json"
		: existsSync(path.join(kgDirPath, "exam-config.json"))
			? "exam-config.json"
			: null;

	const nowMs = Date.now();
	const candidates = [];
	if (Array.isArray(studyCfg.exams)) {
		for (const ex of studyCfg.exams) {
			if (!ex || typeof ex !== "object") continue;
			const rawD = ex.examDate || ex.date || ex.targetDate;
			if (rawD) {
				candidates.push({
					rawDate: String(rawD),
					name: String(ex.name || ex.title || studyCfg.targetExam || "Exam"),
					source: `${cfgSrc || "study-config.json"}:exams[]`,
				});
			}
		}
	}
	for (const k of ["examDate", "exam_date", "targetExamDate", "nextExamDate"]) {
		if (studyCfg[k]) {
			candidates.push({
				rawDate: String(studyCfg[k]),
				name: String(studyCfg.targetExam || studyCfg.examName || "Target Exam"),
				source: `${cfgSrc || "study-config.json"}:${k}`,
			});
			break;
		}
	}

	let bestExam = null;
	let bestHours = null;
	let bestExamMs = null;
	for (const c of candidates) {
		const raw = c.rawDate.trim();
		const isDateOnly = /^\d{4}-\d{2}-\d{2}$/u.test(raw);
		const parsedMs = isDateOnly ? Date.parse(`${raw}T09:00:00Z`) : Date.parse(raw);
		if (!Number.isFinite(parsedMs)) continue;
		let hrs = (parsedMs - nowMs) / 3600000;
		if (isDateOnly && raw === todayStr) {
			const eodMs = Date.parse(`${raw}T23:59:00Z`);
			hrs = clamp((eodMs - nowMs) / 3600000, 0, 24);
		}
		if (hrs < 0) continue;
		if (bestHours === null || hrs < bestHours) {
			bestHours = hrs;
			bestExam = c;
			bestExamMs = parsedMs;
		}
	}

	const sleepFloor = Number.isFinite(Number(studyCfg.sleepHours)) ? Math.max(7.0, Number(studyCfg.sleepHours)) : 7.5;
	const defaultMins = Number.isFinite(Number(studyCfg.dailyMinutes)) ? Math.max(30, Number(studyCfg.dailyMinutes)) : 120;
	let taperMode = {
		active: false,
		hoursRemaining: bestHours !== null ? Number(bestHours.toFixed(1)) : null,
		phase: "none",
		window: "none",
		allowNewConcepts: true,
		sleepFloorHours: Number(sleepFloor.toFixed(1)),
		maxSessionMinutes: defaultMins,
		examDate: bestExamMs !== null ? new Date(bestExamMs).toISOString().slice(0, 10) : null,
		examName: bestExam ? bestExam.name : null,
		examDateSource: bestExam ? bestExam.source : null,
	};
	if (bestHours !== null && bestHours >= 0 && bestHours <= 72) {
		const phase = bestHours <= 24 ? "final_24h" : "taper_72h";
		const win = bestHours <= 24 ? "final_24h" : bestHours <= 48 ? "48h_to_24h" : "72h_to_48h";
		const maxMins = bestHours <= 24 ? 60 : bestHours <= 48 ? 150 : 240;
		taperMode = {
			...taperMode,
			active: true,
			phase,
			window: win,
			allowNewConcepts: false,
			maxSessionMinutes: maxMins,
		};
	}

	const uncertaintyReasons = [];
	let examMs = bestExamMs;
	let examDateStr = taperMode.examDate;
	let examDateSource = taperMode.examDateSource || "default_14d";
	if (!examMs) {
		examMs = nowMs + 14 * 86400000;
		examDateStr = new Date(examMs).toISOString().slice(0, 10);
		examDateSource = "default_14d";
		uncertaintyReasons.push("No examDate configured; defaulted projection horizon to +14 days.");
	}
	const daysToExam = Number(Math.max(0, (examMs - nowMs) / 86400000).toFixed(1));

	const activeProps = propositions.filter((p) => p && p.id && !p.archived);
	const evProps = evidenceSummary?.propositions || {};
	const antiAlerts = Array.isArray(evidenceSummary?.antiInflationAlerts) ? evidenceSummary.antiInflationAlerts : [];
	const tierWeight = { 1: 0.25, 2: 0.6, 3: 0.88, 4: 1.0 };

	let totalWeight = 0;
	let weightedRExam = 0;
	let weightedEvid = 0;
	let plannedCount = 0;
	let mActive = 0;
	const decayCandidates = [];
	const chaptersSet = new Set();

	const factor = Math.pow(0.9, -1 / 0.2) - 1; // FSRS-6 default decay w20=0.2
	for (const p of activeProps) {
		const pid = String(p.id);
		const st = String(p.status || "planned").toLowerCase();
		const ch = String(p.chapter || "General").trim() || "General";
		chaptersSet.add(ch);
		const descN = Number(evProps[pid]?.descendantsCount || 0);
		const wi = 1.0 + 0.35 * Math.log1p(descN);

		let rEx = 0;
		if (st !== "planned") {
			const stab = Number(p?.fsrs?.stability || 0);
			const lastRev = p?.fsrs?.last_review || p?.fsrs?.lastReview || p.lastTested || p.updatedAt;
			const lastMs = lastRev ? Date.parse(String(lastRev)) : NaN;
			if (Number.isFinite(stab) && stab > 0 && Number.isFinite(lastMs)) {
				const dtDays = Math.max(0, (examMs - lastMs) / 86400000);
				rEx = clamp(Math.pow(1 + factor * (dtDays / stab), -0.2), 0, 1);
			} else if (st === "misconception") {
				rEx = 0.12;
			} else if (st === "shaky") {
				rEx = 0.35;
			} else {
				const refMs = Number.isFinite(lastMs) ? lastMs : nowMs;
				const dtDays = Math.max(0, (examMs - refMs) / 86400000);
				rEx = clamp(Math.pow(1 + factor * (dtDays / 7.0), -0.2), 0, 1);
			}
		}

		let tau = 0;
		if (st === "planned") {
			plannedCount += 1;
		} else {
			if (st === "misconception") mActive += 1;
			if (rEx < 0.7 && (st === "solid" || st === "shaky")) {
				decayCandidates.push({ propId: pid, weight: wi, rExam: rEx });
			}
			const tier = Number(evProps[pid]?.tier || 1);
			tau = tierWeight[tier] ?? 0.25;
		}

		totalWeight += wi;
		weightedRExam += wi * rEx;
		weightedEvid += wi * tau;
	}

	const cRet = totalWeight > 0 ? clamp(weightedRExam / totalWeight, 0, 1) : 0;
	const antiPen = Math.max(0.75, 1.0 - 0.04 * Math.min(6, antiAlerts.length));
	const cEvid = totalWeight > 0 ? clamp((weightedEvid / totalWeight) * antiPen, 0, 1) : 0;
	if (totalWeight === 0) uncertaintyReasons.push("Knowledge graph has 0 propositions in scope.");

	let tTot = 0;
	let tDone = 0;
	let qTot = 0;
	let qDone = 0;
	for (const tr of chapterTrackers) {
		tTot += Number(tr.topicsTotal || 0);
		tDone += Number(tr.topicsMastered || 0);
		qTot += Number(tr.questionsTotal || 0);
		qDone += Number(tr.questionsSolved || 0);
	}
	let cSyl = 0;
	if (tTot > 0) {
		cSyl = qTot > 0 ? clamp(0.6 * (tDone / tTot) + 0.4 * (qDone / qTot), 0, 1) : clamp(tDone / tTot, 0, 1);
	} else if (activeProps.length > 0) {
		cSyl = clamp((activeProps.length - plannedCount) / activeProps.length, 0, 1);
	}

	const clusterCounts = new Map();
	for (const m of mistakes) {
		const k = `${String(m.category || "concept_gap")}::${String(m.chapter || "General")}`;
		clusterCounts.set(k, (clusterCounts.get(k) || 0) + 1);
	}
	let e3Strike = 0;
	for (const c of clusterCounts.values()) if (c >= 3) e3Strike += 1;
	const fDue = forksDue.length;
	const cTrap =
		activeProps.length === 0 && mActive === 0 && fDue === 0 && e3Strike === 0
			? 0
			: clamp(Math.exp(-(1.5 * mActive + 1.0 * fDue + 1.2 * e3Strike) / Math.max(5.0, 0.35 * activeProps.length)), 0, 1);

	const execSignals = [];
	const mockPcts = [];
	const evScores = [];
	let totalNegLost = 0;
	let totalMockMax = 0;
	for (const a of examAttempts) {
		const pct = Number(a.percentage);
		if (Number.isFinite(pct)) mockPcts.push(clamp(pct, 0, 100));
		const negL = Math.max(0, Number(a.negativeMarksLost || 0));
		const mx = Math.max(0, Number(a.maxScore || 0));
		totalNegLost += negL;
		totalMockMax += mx;
		const evSc = Number(a?.evCalibration?.evDisciplineScore ?? a.evDisciplineScore);
		if (Number.isFinite(evSc)) evScores.push(clamp(evSc / 100, 0, 1));
		else if (mx > 0) evScores.push(clamp(1 - negL / Math.max(1, 0.2 * mx), 0, 1));
	}
	if (evScores.length > 0) {
		let wSum = 0;
		let vSum = 0;
		for (let i = 0; i < evScores.length; i += 1) {
			const w = Math.pow(0.65, evScores.length - 1 - i);
			wSum += w;
			vSum += w * evScores[i];
		}
		execSignals.push(vSum / wSum);
	}
	const overconfBias = Number(calibration?.sampleCount || 0) >= 3 && Number.isFinite(Number(calibration?.overconfidenceBias))
		? Number(calibration.overconfidenceBias)
		: 0;
	if (Number(calibration?.sampleCount || 0) >= 5 && Number.isFinite(Number(calibration?.brierScore))) {
		execSignals.push(clamp(1 - Math.min(1, 2 * Number(calibration.brierScore)), 0, 1));
	}
	if (Number(interleaveStats?.totalItemsChecked || 0) >= 5 && Number.isFinite(Number(interleaveStats?.tier1AccuracyPct))) {
		execSignals.push(clamp(Number(interleaveStats.tier1AccuracyPct) / 100, 0, 1));
	}
	let cExec = 0;
	let execConf = "untested";
	if (execSignals.length > 0) {
		cExec = clamp(execSignals.reduce((s, x) => s + x, 0) / execSignals.length, 0, 1);
		execConf = "observed";
	} else if (activeProps.length > 0) {
		cExec = 0.55;
		uncertaintyReasons.push("No graded timed mock exams yet; execution pillar uses conservative 55% prior.");
	}

	const readinessIndex = Number((100 * (0.32 * cRet + 0.22 * cEvid + 0.2 * cSyl + 0.14 * cTrap + 0.12 * cExec)).toFixed(1));
	const band = readinessIndex < 40 ? "needs_foundation" : readinessIndex < 65 ? "building" : readinessIndex < 85 ? "exam_ready" : "peak";

	let expectedPct = readinessIndex;
	const mMocks = mockPcts.length;
	if (mMocks > 0) {
		let wSum = 0;
		let vSum = 0;
		for (let i = 0; i < mMocks; i += 1) {
			const w = Math.pow(0.7, mMocks - 1 - i);
			wSum += w;
			vSum += w * mockPcts[i];
		}
		const sMock = vSum / wSum;
		const wMock = mMocks / (mMocks + 2.5);
		expectedPct = wMock * sMock + (1 - wMock) * readinessIndex;
	}
	const sigma = clamp(6.0 + 9.0 / Math.sqrt(1 + mMocks) + 8.0 * Math.max(0, overconfBias) + 5.0 * (1 - cEvid), 4.5, 18.0);
	const lowPct = Number(clamp(expectedPct - sigma, 0, 100).toFixed(1));
	const expPct = Number(clamp(expectedPct, 0, 100).toFixed(1));
	const highPct = Number(clamp(expectedPct + sigma, 0, 100).toFixed(1));

	const rawMaxMarks = Number(studyCfg.maxMarks);
	const effMaxMarks = Number.isFinite(rawMaxMarks) && rawMaxMarks > 0 ? rawMaxMarks : null;
	const predictedMarks = effMaxMarks !== null
		? {
			maxMarks: Number(effMaxMarks.toFixed(1)),
			low: Number(((effMaxMarks * lowPct) / 100).toFixed(1)),
			expected: Number(((effMaxMarks * expPct) / 100).toFixed(1)),
			high: Number(((effMaxMarks * highPct) / 100).toFixed(1)),
		}
		: null;

	// Non-fabricated Pre-Mortem Shield (strictly 0 bullets on empty logs; NEVER leaks sealed fork answers!)
	const bullets = [];
	const seenB = new Set();
	const addBullet = (sourceKind, key, badge, ifCue, thenAction, extra = {}) => {
		if (bullets.length >= 3) return;
		const k = `${sourceKind}::${String(key).toLowerCase()}`;
		if (seenB.has(k)) return;
		seenB.add(k);
		bullets.push({
			rank: bullets.length + 1,
			sourceKind,
			evidenceBadge: clipStr(badge, 72),
			ifCue: clipStr(ifCue, 150),
			thenAction: clipStr(thenAction, 160),
			...extra,
		});
	};
	if (forksDue.length > 0) {
		const f0 = forksDue[0];
		const ch = String(f0.chapter || f0.subject || "active chapter");
		const cue = String(f0.forkPrompt || (Array.isArray(f0.prefixSteps) && f0.prefixSteps[f0.prefixSteps.length - 1]) || ch);
		addBullet(
			"blunder_fork",
			f0.propId || f0.id || "fork",
			`Unresolved Fork ${f0.id || ""} · ${ch}`,
			`IF you hit the fork setup (${clipStr(cue, 85)}) in ${ch}`,
			`THEN verify boundary conditions & invariant before executing your first move in ${ch}.`,
			{ ...(f0.propId ? { propId: String(f0.propId) } : {}), ...(f0.id ? { forkId: String(f0.id) } : {}) },
		);
	}
	if (mistakes.length > 0) {
		const grouped = new Map();
		for (const m of mistakes) {
			const key = `${String(m.category || "concept_gap")}::${String(m.chapter || "General")}`;
			if (!grouped.has(key)) grouped.set(key, []);
			grouped.get(key).push(m);
		}
		const ranked = [...grouped.entries()].sort((a, b) => b[1].length - a[1].length || a[0].localeCompare(b[0]));
		const [k0, rows0] = ranked[0];
		const [cat0, ch0] = k0.split("::");
		const sample = rows0[rows0.length - 1];
		addBullet(
			"recurring_mistake",
			`${cat0}:${sample.propId || ch0}`,
			`${rows0.length >= 3 ? "3-Strike Escalation · " : ""}${rows0.length}x ${cat0} in ${ch0}`,
			`IF you see ${clipStr(sample.triggerCue || `${cat0.replace(/_/gu, " ")} setup in ${ch0}`, 95)}`,
			`THEN ${clipStr(sample.correctMove || "Verify the governing condition, sign, and unit before locking your answer.", 120)}`,
			sample.propId ? { propId: String(sample.propId) } : {},
		);
	} else if (Number(learnerProfile?.failureDna?.windowCount || 0) > 0) {
		const dna = learnerProfile.failureDna;
		const dom = String(dna.dominantTier || "comprehension_model");
		const pct = Math.round(Number(dna?.tierShares?.[dom] || 0) * 100);
		addBullet(
			"failure_dna",
			dom,
			`Failure DNA: ${dom} (${pct}%, n=${dna.windowCount})`,
			dom === "executive_pacing"
				? "IF a single question stalls beyond 180 seconds on Pass 1"
				: "IF a problem looks familiar on the surface",
			dom === "executive_pacing"
				? "THEN mark for review and move on to harvest high-confidence marks first."
				: "THEN name the governing law and check its validity assumptions before computing.",
		);
	}
	if (examAttempts.length > 0 && bullets.length < 3) {
		const negGambles = examAttempts.reduce((s, a) => s + Number(a?.evCalibration?.negEvGamblesCount || 0), 0);
		if (negGambles > 0 || totalNegLost > 0) {
			addBullet(
				"exam_trap",
				"neg_ev_gambling",
				negGambles > 0
					? `Exam EV Leak · ${negGambles} -EV gamble(s), -${Number(totalNegLost.toFixed(1))}m`
					: `Exam Negative Marks · -${Number(totalNegLost.toFixed(1))}m across ${examAttempts.length} mock(s)`,
				"IF your confidence on a negative-marked question is below break-even p* = (P + U) / (B + P)",
				"THEN skip it cleanly on Pass 1 instead of taking a negative-EV guess.",
			);
		}
	}
	const preMortemShield = bullets.length === 0
		? {
			ok: true,
			sourceBasis: "cold_start_minimal",
			count: 0,
			bullets: [],
			note: "Zero mistake, fork, failure-DNA, or exam-trap logs in scope; Pre-Mortem Shield stays empty (no generic filler).",
		}
		: { ok: true, sourceBasis: "observed_logs", count: bullets.length, bullets };

	// Top-3 ROI study levers (pure syntopik CLI commands)
	const marksScale = effMaxMarks !== null ? effMaxMarks : 100;
	const levers = [];
	const addLever = (kind, title, why, hoursEffort, gainPct, exactCmd, skillHint) => {
		if (gainPct <= 0.1 || hoursEffort <= 0) return;
		const marksGain = Number((marksScale * (gainPct / 100)).toFixed(2));
		levers.push({
			kind,
			title,
			why,
			effortMinutes: Math.round(hoursEffort * 60),
			hoursEffort: Number(hoursEffort.toFixed(2)),
			expectedReadinessGainPct: Number(gainPct.toFixed(2)),
			expectedMarksGain: marksGain,
			roiMarksPerHour: Number((marksGain / hoursEffort).toFixed(2)),
			exactCmd,
			skillHint,
		});
	};
	const firstMisc = activeProps.find((p) => String(p.status || "").toLowerCase() === "misconception");
	if (firstMisc) {
		addLever(
			"repair_misconception",
			`Repair misconception: ${clipStr(firstMisc.title || firstMisc.id, 52)}`,
			`Active misconception on '${firstMisc.id}' drags both retention and trap clearance.`,
			0.35,
			2.5,
			"syntopik review --json",
			"teach-chapter",
		);
	}
	if (fDue > 0 || e3Strike > 0) {
		addLever(
			"clear_forks",
			fDue > 0 ? `Clear ${fDue} due Blunder Fork(s)` : `Drill ${e3Strike} 3-strike trap cluster(s)`,
			`Eliminates recurring exam blunders (${fDue} due forks, ${e3Strike} 3-strike clusters).`,
			0.25,
			2.0,
			"syntopik next --json",
			"practice-drill",
		);
	}
	if (antiAlerts.length > 0) {
		const a0 = antiAlerts[0];
		addLever(
			"upgrade_evidence",
			`Verify bottleneck '${a0.propId}' to T3 transfer`,
			`High-leverage node (${a0.descendantsCount || 0} downstream) stuck at ${a0.currentTierLabel || "T1/T2"}.`,
			0.3,
			1.8,
			"syntopik next --json",
			"practice-drill",
		);
	}
	if (decayCandidates.length > 0) {
		addLever(
			"refresh_decay",
			`Refresh ${Math.min(5, decayCandidates.length)} decaying card(s) before exam`,
			`Projected exam-day retention drops below 70% on ${decayCandidates.length} learned proposition(s).`,
			0.25,
			1.6,
			"syntopik review --json",
			"knowledge-graph",
		);
	}
	if (chaptersSet.size >= 2 && taperMode.phase !== "final_24h" && (interleaveStats.tier1AccuracyPct === null || interleaveStats.tier1AccuracyPct < 80)) {
		addLever(
			"interleave_drill",
			`Run blind-chapter interleave across ${chaptersSet.size} chapters`,
			"Mixed-chapter principle discrimination prevents surface-feature confusion on unseen exam problems.",
			0.3,
			1.8,
			"syntopik interleave build --n 6 --json",
			"practice-drill",
		);
	}
	if (mMocks === 0 && activeProps.length > plannedCount && taperMode.phase !== "final_24h") {
		addLever(
			"timed_baseline",
			"Take 1 calibrated timed CBT baseline mock",
			"Replaces the unverified 55% execution prior with real pacing, EV-skip, and autopsy telemetry.",
			0.75,
			2.2,
			"syntopik readiness --json",
			"exam-simulator",
		);
	}
	levers.sort((a, b) => b.roiMarksPerHour - a.roiMarksPerHour || b.expectedMarksGain - a.expectedMarksGain || a.effortMinutes - b.effortMinutes);
	const topLevers = levers.slice(0, 3).map((lv, i) => ({ rank: i + 1, ...lv }));

	return {
		ok: true,
		readinessIndex,
		band,
		predictedScorePct: { low: lowPct, expected: expPct, high: highPct },
		predictedMarks,
		examDate: examDateStr,
		examDateSource,
		daysToExam,
		hasExamConfig: examDateSource !== "default_14d",
		taperMode,
		preMortemShield,
		topLevers,
		pillars: {
			projectedRetention: Number((cRet * 100).toFixed(1)),
			evidenceDepth: Number((cEvid * 100).toFixed(1)),
			forkClearance: Number((cTrap * 100).toFixed(1)),
			evDiscipline: Number((cExec * 100).toFixed(1)),
			syllabusCoverage: Number((cSyl * 100).toFixed(1)),
		},
		executionConfidence: execConf,
		uncertaintyReasons,
	};
}

function computeOfflineNextMove(learnerProfile, whiteboard, forksDue, dueItems, activeTracker, evidenceSummary) {
	const pol = learnerProfile?.compiledPolicy || defaultLearnerProfile().compiledPolicy;
	const pendingPins = whiteboard?.pendingPins || [];
	let mode = "teach_frontier";
	let targetNodeId = null;
	let rationale = "Workspace clear; drop sources or load a starter pack.";
	if (pendingPins.length > 0) {
		mode = "whiteboard_pin_answer";
		rationale = `Learner pinned whiteboard step ${pendingPins[0].stepIndex}: ${String(pendingPins[0].note || "").slice(0, 80)}`;
	} else if (!learnerProfile?.calibration?.completed && Number(learnerProfile?.globalStats?.totalEvents || 0) === 0) {
		mode = "onboarding_calibration";
		rationale = "First-time uncalibrated learner; optional 2-min non-VARK calibration available.";
	} else if (forksDue.length > 0) {
		mode = "fork_replay";
		targetNodeId = forksDue[0].propId || forksDue[0].id || null;
		rationale = `Due Blunder Fork '${forksDue[0].id}' ready for Woodpecker replay.`;
	} else if (dueItems.length > 0) {
		mode = "fire_review";
		targetNodeId = dueItems[0].id || null;
		rationale = `Due spaced review '${targetNodeId}' (${dueItems[0].status}).`;
	} else if ((evidenceSummary?.antiInflationAlerts || []).length > 0) {
		mode = "transfer_verify";
		targetNodeId = evidenceSummary.antiInflationAlerts[0].propId;
		rationale = `Bottleneck '${targetNodeId}' needs T3/T4 transfer verification.`;
	} else if (activeTracker) {
		mode = "teach_frontier";
		rationale = activeTracker.nextHint || `Continue active chapter ${activeTracker.title}.`;
	}
	return {
		ok: true,
		recommendedMode: mode,
		targetNodeId,
		chapterSlug: activeTracker?.slug || null,
		rationale,
		teachingPolicy: pol,
		pendingPins: pendingPins.slice(0, 3),
		alerts: [],
	};
}

const propositions = readJsonl(path.join(kgDir, "propositions.jsonl"));
const rawChains = readJsonl(path.join(kgDir, "chains.jsonl"));
const chainSummary = normalizeChains(rawChains);
const mistakes = readJsonl(path.join(kgDir, "mistakes.jsonl"));
const examAttempts = readJsonl(path.join(kgDir, "exam-attempts.jsonl"));
const harnessLog = readJsonl(path.join(kgDir, "harness-log.jsonl"));
const revlog = readJsonl(path.join(kgDir, "revlog.jsonl"));
const rawReadingQueue = readJsonl(path.join(kgDir, "reading-queue.jsonl"));
const rawForks = readJsonl(path.join(kgDir, "forks.jsonl"));
const rawArenas = readJsonl(path.join(kgDir, "arenas.jsonl"));
const rawDecisions = readJsonl(path.join(kgDir, "decisions.jsonl"));
const rawDerivations = readJsonl(path.join(kgDir, "derivations.jsonl"));
const rawVivaSessions = readJsonl(path.join(kgDir, "viva-sessions.jsonl"));
const rawInterleaveSessions = readJsonl(path.join(kgDir, "interleave-sessions.jsonl"));
const evidenceLedger = readJsonl(path.join(kgDir, "evidence-ledger.jsonl"));
const rawLearnerProfile = readJsonObject(path.join(kgDir, "learner-profile.json"));
const rawWhiteboard = readJsonObject(path.join(kgDir, "whiteboard-session.json"));
const reviewQueuePath = path.join(kgDir, "review-queue.md");
const reviewQueueMarkdown = existsSync(reviewQueuePath)
	? readFileSync(reviewQueuePath, "utf8")
	: "";

const chapterTrackers = scanChapterTrackers();
const activeChapterTracker = chapterTrackers.find((t) => t.active) || null;
const audioBriefs = scanAudioBriefs();
const chapters = scanChapters(audioBriefs, chapterTrackers);
const demos = scanDemos();
const animations = scanAnimations();
const sources = scanSources();
const examFiles = scanExams();
const mistakeStats = computeMistakeStats(mistakes);
const examStats = computeExamStats(examAttempts);
const { today, stats, dueItems } = computeStats(
	propositions,
	chainSummary,
	chapters,
	chapterTrackers,
	activeChapterTracker,
	audioBriefs,
	demos,
	animations,
	sources,
	mistakes,
	examFiles,
	examAttempts,
	harnessLog,
);

const retentionHeatmap = computeRetentionHeatmap(revlog, examAttempts, propositions, today);
const calibration = computeCalibration(revlog);
const leeches = computeLeeches(propositions, revlog);
const readingQueue = normalizeReadingQueue(rawReadingQueue);

// v3.0 sanitized payloads (zero sealed secret leaks!)
const learnerProfile = rawLearnerProfile || defaultLearnerProfile();
const forks = rawForks.map((f) => sanitizeFork(f, today));
const forksDue = forks.filter((f) => f.isDue);
const arenas = rawArenas.map(sanitizeArena);
const decisions = rawDecisions.map(sanitizeDecision);
const derivations = rawDerivations.map((d) => sanitizeDerivation(d, 3));
const vivaSessions = rawVivaSessions.map(sanitizeVivaSession);
const evidenceSummary = computeEvidenceSummary(
	propositions,
	chainSummary,
	revlog,
	rawForks,
	rawArenas,
	rawVivaSessions,
	rawDerivations,
	evidenceLedger,
);
const rootCauseHotspots = computeRootCauseHotspots(propositions, chainSummary, mistakes);
const whiteboard = sanitizeWhiteboard(rawWhiteboard);
const explorables = scanExplorables(chapters);
const starterPacks = scanStarterPacks();
const rosetta = computeRosettaHubData(propositions, rawChains);
const syncStatus = readSyncStatusNode(kgDir);
const interleaveStats = computeInterleaveStatsNode(rawInterleaveSessions);
const readinessSummary = computeReadinessSummaryNode(
	propositions,
	chainSummary,
	chapterTrackers,
	mistakes,
	forksDue,
	examAttempts,
	calibration,
	evidenceSummary,
	interleaveStats,
	learnerProfile,
	kgDir,
	today,
);
const nextMove = computeOfflineNextMove(
	learnerProfile,
	whiteboard,
	forksDue,
	dueItems,
	activeChapterTracker,
	evidenceSummary,
);

stats.revlogCount = revlog.length;
stats.leechesCount = leeches.length;
stats.readingQueueCount = readingQueue.length;
stats.brierScore = calibration.brierScore;
stats.forksCount = forks.length;
stats.forksDueCount = forksDue.length;
stats.arenasCount = arenas.length;
stats.decisionsCount = decisions.length;
stats.derivationsCount = derivations.length;
stats.vivaSessionsCount = vivaSessions.length;
stats.explorablesCount = Object.values(explorables).reduce((acc, arr) => acc + arr.length, 0);
stats.starterPacksCount = starterPacks.length;
stats.verifiedTransferPct = evidenceSummary.verifiedTransferPct;
stats.rosettaChordsCount = rosetta.chordCount;
stats.interleaveSessionsCount = interleaveStats.totalSessions;
stats.readinessIndex = readinessSummary.readinessIndex;

const studyGraph = {
	propositions,
	chains: chainSummary.chains,
	normalizedEdges: chainSummary.normalizedEdges,
	chainCount: chainSummary.chainCount,
};
const studyHub = {
	schemaVersion: 5,
	generatedAt: new Date().toISOString(),
	today,
	workspaceName: path.basename(root),
	stats,
	dueItems,
	reviewQueueMarkdown,
	chapters,
	chapterTrackers,
	activeChapterTracker,
	audioBriefs,
	demos,
	animations,
	sources,
	mistakes,
	mistakeStats,
	examAttempts,
	examFiles,
	examStats,
	harnessLog,
	retentionHeatmap,
	calibration,
	leeches,
	readingQueue,
	forksDue,
	forksDueCount: forksDue.length,
	arenasCount: arenas.length,
	learnerProfile,
	nextMove,
	forks,
	arenas,
	decisions,
	derivations,
	vivaSessions,
	evidenceSummary,
	rootCauseHotspots,
	whiteboard,
	explorables,
	starterPacks,
	rosetta,
	syncStatus,
	readinessSummary,
	interleaveStats,
};

const bundle = [
	"// Auto-generated by Syntopik sync — do not edit by hand.",
	`window.__STUDY_GRAPH__ = ${safeScriptJson(studyGraph)};`,
	`window.__STUDY_HUB__ = ${safeScriptJson(studyHub)};`,
	"",
].join("\n");

mkdirSync(path.dirname(outFile), { recursive: true });
// Skip the write when nothing but the timestamp changed so file watchers (Studio SSE, editors,
// cloud-sync clients) are not woken by every PostToolUse hook run.
let previousBundle = null;
try {
	previousBundle = existsSync(outFile) ? readFileSync(outFile, "utf8") : null;
} catch {
	previousBundle = null;
}
const stripGeneratedAt = (text) => text.replace(/"generatedAt": "[^"]*"/u, '"generatedAt": ""');
if (
	rawArgv.includes("--force") ||
	previousBundle === null ||
	stripGeneratedAt(previousBundle) !== stripGeneratedAt(bundle)
) {
	writeFileSync(outFile, bundle, "utf8");
}

const args = new Set(rawArgv);
if (args.has("--json")) {
	console.log(JSON.stringify(stats, null, 2));
} else if (!args.has("--quiet")) {
	const outRel = toPosix(path.relative(root, outFile)) || outFile;
	console.log(
		`Synced ${propositions.length} nodes, ${chainSummary.chainCount} chains (${chainSummary.normalizedEdges.length} edges), ${mistakes.length} mistakes, ${examFiles.length} exams (${examAttempts.length} attempts), ${chapters.length} chapters/sheets (${chapterTrackers.length} trackers, ${audioBriefs.length} audio), ${demos.length} labs, ${animations.length} videos, ${sources.length} sources → ${outRel}`,
	);
}

const wantsGraph = args.has("--graph");
const wantsOpen = args.has("--open") || wantsGraph;
if (wantsOpen) {
	const target = wantsGraph
		? path.join(kgDir, "viewer.html")
		: path.join(root, "dashboard.html");
	openTarget(target);
}
