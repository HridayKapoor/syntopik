#!/usr/bin/env node
import { existsSync, mkdirSync, readdirSync, readFileSync, realpathSync, statSync, writeFileSync } from "node:fs";
import { execFileSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

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

const propositions = readJsonl(path.join(kgDir, "propositions.jsonl"));
const rawChains = readJsonl(path.join(kgDir, "chains.jsonl"));
const chainSummary = normalizeChains(rawChains);
const mistakes = readJsonl(path.join(kgDir, "mistakes.jsonl"));
const examAttempts = readJsonl(path.join(kgDir, "exam-attempts.jsonl"));
const harnessLog = readJsonl(path.join(kgDir, "harness-log.jsonl"));
const revlog = readJsonl(path.join(kgDir, "revlog.jsonl"));
const rawReadingQueue = readJsonl(path.join(kgDir, "reading-queue.jsonl"));
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

stats.revlogCount = revlog.length;
stats.leechesCount = leeches.length;
stats.readingQueueCount = readingQueue.length;
stats.brierScore = calibration.brierScore;

const studyGraph = {
	propositions,
	chains: chainSummary.chains,
	normalizedEdges: chainSummary.normalizedEdges,
	chainCount: chainSummary.chainCount,
};
const studyHub = {
	schemaVersion: 4,
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
};

const bundle = [
	"// Auto-generated by scripts/sync-graph.mjs — do not edit by hand.",
	`window.__STUDY_GRAPH__ = ${safeScriptJson(studyGraph)};`,
	`window.__STUDY_HUB__ = ${safeScriptJson(studyHub)};`,
	"",
].join("\n");

mkdirSync(path.dirname(outFile), { recursive: true });
writeFileSync(outFile, bundle, "utf8");

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
