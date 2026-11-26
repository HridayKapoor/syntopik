#!/usr/bin/env node
import { existsSync, readdirSync, readFileSync } from "node:fs";
import { execFileSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");

const LEVEL_RANK = { basics: 0, main: 1, advanced: 2 };
const DONE_Q_STATUSES = new Set(["solved", "mastered", "skipped"]);

function readJsonl(filePath) {
	if (!existsSync(filePath)) return [];
	return readFileSync(filePath, "utf8")
		.split(/\r?\n/u)
		.map((l) => l.trim())
		.filter(Boolean)
		.map((l) => {
			try {
				return JSON.parse(l);
			} catch {
				return null;
			}
		})
		.filter(Boolean);
}

function listVisible(dirPath) {
	if (!existsSync(dirPath)) return [];
	return readdirSync(dirPath)
		.filter((name) => !name.startsWith("."))
		.sort();
}

function readActiveChapterTracker(chaptersDir) {
	if (!existsSync(chaptersDir)) return null;
	const files = listVisible(chaptersDir).filter((name) => /-tracker\.json$/iu.test(name));
	const activeTrackers = [];
	for (const fname of files) {
		try {
			const parsed = JSON.parse(readFileSync(path.join(chaptersDir, fname), "utf8"));
			if (parsed && typeof parsed === "object" && !Array.isArray(parsed) && parsed.active === true) {
				activeTrackers.push(parsed);
			}
		} catch {
			// Skip malformed tracker files without breaking Turn-0 hook.
		}
	}
	if (activeTrackers.length === 0) return null;
	activeTrackers.sort((a, b) => String(b.updatedAt || "").localeCompare(String(a.updatedAt || "")));
	return activeTrackers[0];
}

function isJeeProfile(tracker) {
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
	if (sources.some((s) => s?.kind === "pyq_bank" || /\b(?:jee|nta|eduniti|pyq)\b/iu.test(`${s?.label || ""} ${s?.path || ""}`))) {
		return true;
	}
	const meta = `${tracker?.title || ""} ${tracker?.goal || ""} ${tracker?.examTarget || ""}`;
	return /\b(?:jee|nta|iit)\b/iu.test(meta);
}

function formatTopicHint(topic) {
	const refs = Array.isArray(topic.sourceRefs) ? topic.sourceRefs : [];
	const parts = [];
	for (const ref of refs) {
		if (!ref || typeof ref !== "object") continue;
		const sid = String(ref.sourceId || "").trim().toUpperCase();
		const pages = String(ref.pages || "").trim();
		if (sid && pages) parts.push(`${sid} p.${pages}`);
		else if (sid) parts.push(sid);
		if (parts.length >= 2) break;
	}
	return parts.length > 0 ? ` (${parts.join(", ")})` : "";
}

function formatQuestionSummary(prefix, q) {
	const label = String(q.label || q.qid || "Question").trim();
	const pg = q.page != null && q.page !== "" ? ` (p.${q.page})` : "";
	const cat = prefix === "Retest" && q.lastMistakeCategory ? ` [${q.lastMistakeCategory}]` : "";
	return `${prefix} ${label}${pg}${cat}`;
}

function selectInterleavedQuestion(candidates, topicOrderMap, tracker) {
	const bucketOf = (q) => [
		q.status === "in_progress" ? 0 : 1,
		LEVEL_RANK[String(q.level || "basics")] ?? 1,
	];
	let minStatus = 99;
	let minLvl = 99;
	for (const { q } of candidates) {
		const [sr, lr] = bucketOf(q);
		if (sr < minStatus || (sr === minStatus && lr < minLvl)) {
			minStatus = sr;
			minLvl = lr;
		}
	}
	const pool = candidates.filter(({ q }) => {
		const [sr, lr] = bucketOf(q);
		return sr === minStatus && lr === minLvl;
	});

	const distinctTopics = new Set(pool.map(({ q }) => String(q.topicId || "")).filter(Boolean));
	if (distinctTopics.size >= 2 && minStatus === 1) {
		const questions = Array.isArray(tracker.questions) ? tracker.questions : [];
		const qById = new Map(questions.filter((q) => q && q.qid).map((q) => [q.qid, q]));
		const doneCounts = new Map();
		const stepHist = [];
		for (const q of questions) {
			const tid = String(q.topicId || "");
			const lvl = String(q.level || "basics");
			if (tid && DONE_Q_STATUSES.has(String(q.status || ""))) {
				const k = `${tid}::${lvl}`;
				doneCounts.set(k, (doneCounts.get(k) || 0) + 1);
			}
			if (tid && Number.isInteger(q.lastStep)) {
				stepHist.push([q.lastStep, tid]);
			}
		}
		const logHist = [];
		for (const entry of Array.isArray(tracker.sessionLog) ? tracker.sessionLog : []) {
			if (entry && entry.event === "mark_question" && qById.has(entry.qid)) {
				const tid = String(qById.get(entry.qid).topicId || "");
				if (tid) logHist.push([Number(entry.step || 0), tid]);
			}
		}
		const hist = logHist.length > 0 ? logHist : stepHist.sort((a, b) => a[0] - b[0]);
		const lastTopic = hist.length > 0 ? hist[hist.length - 1][1] : null;
		let tailLen = 0;
		for (let i = hist.length - 1; i >= 0; i--) {
			if (hist[i][1] === lastTopic) tailLen++;
			else break;
		}

		pool.sort((a, b) => {
			const qa = a.q;
			const qb = b.q;
			const ta = String(qa.topicId || "");
			const tb = String(qb.topicId || "");
			const streakA = lastTopic && ta === lastTopic && tailLen >= 2 ? 1 : 0;
			const streakB = lastTopic && tb === lastTopic && tailLen >= 2 ? 1 : 0;
			if (streakA !== streakB) return streakA - streakB;
			const repA = lastTopic && ta === lastTopic ? 1 : 0;
			const repB = lastTopic && tb === lastTopic ? 1 : 0;
			if (repA !== repB) return repA - repB;
			const doneA = doneCounts.get(`${ta}::${String(qa.level || "basics")}`) || 0;
			const doneB = doneCounts.get(`${tb}::${String(qb.level || "basics")}`) || 0;
			if (doneA !== doneB) return doneA - doneB;
			const ordA = topicOrderMap.get(ta) ?? 9999;
			const ordB = topicOrderMap.get(tb) ?? 9999;
			if (ordA !== ordB) return ordA - ordB;
			return a.idx - b.idx;
		});
		return pool[0].q;
	}

	pool.sort((a, b) => {
		const ordA = topicOrderMap.get(String(a.q.topicId || "")) ?? 9999;
		const ordB = topicOrderMap.get(String(b.q.topicId || "")) ?? 9999;
		if (ordA !== ordB) return ordA - ordB;
		return a.idx - b.idx;
	});
	return pool[0].q;
}

function computeAutopilotNextSummary(tracker) {
	const slug = String(tracker.slug || "chapter");
	const title = String(tracker.title || slug);
	const sources = Array.isArray(tracker.sources) ? tracker.sources : [];
	const topics = Array.isArray(tracker.topics) ? tracker.topics : [];
	const questions = Array.isArray(tracker.questions) ? tracker.questions : [];
	const milestones = (tracker.milestones && typeof tracker.milestones === "object") ? tracker.milestones : {};
	const doubtStack = Array.isArray(tracker.doubtStack) ? tracker.doubtStack : [];
	const stepCounter = Number.isFinite(Number(tracker.stepCounter)) ? Number(tracker.stepCounter) : 0;
	const retestGap = Math.max(1, Number(tracker.config?.retestGap) || 3);
	const jeeMode = isJeeProfile(tracker);

	const topicsById = new Map(topics.filter((t) => t && t.id).map((t) => [t.id, t]));
	const topicOrderMap = new Map(topics.map((t, i) => [String(t.id || ""), Number(t.order) || i + 1]));

	const computeBase = () => {
		if (sources.length > 0 && !milestones.sourcesIndexed) {
			const labels = sources.slice(0, 2).map((s) => String(s.label || s.id || "source")).join(", ");
			return `Index ${sources.length} source file(s) (${labels})`;
		}
		if (!milestones.probed) {
			return `Probe frontier for "${title}" (${topics.length} topics mapped)`;
		}

		const inProgTopics = topics
			.map((t, idx) => ({ t, idx }))
			.filter(({ t }) => String(t.status || "pending") === "in_progress");
		if (inProgTopics.length > 0) {
			inProgTopics.sort((a, b) =>
				(Number(a.t.order) || 9999) - (Number(b.t.order) || 9999)
				|| (LEVEL_RANK[String(a.t.level || "basics")] ?? 1) - (LEVEL_RANK[String(b.t.level || "basics")] ?? 1)
				|| a.idx - b.idx,
			);
			const chosen = inProgTopics[0].t;
			return `Teach Topic "${chosen.title || chosen.id}"${formatTopicHint(chosen)}`;
		}

		const masteredIds = new Set(topics.filter((t) => t && t.status === "mastered").map((t) => t.id));
		const hasRemainingTopics = topics.some((t) => t && t.status !== "mastered");

		const masteredRetestDue = [];
		const masteredRetestCooling = [];
		const masteredUnseen = [];
		let otherUnseenExists = false;

		questions.forEach((q, idx) => {
			if (!q || typeof q !== "object") return;
			const st = String(q.status || "unseen");
			if (DONE_Q_STATUSES.has(st)) return;
			if (q.topicId && masteredIds.has(q.topicId)) {
				if (st === "retest") {
					const ls = Number.isInteger(q.lastStep) ? q.lastStep : null;
					if (ls === null || stepCounter - ls >= retestGap) {
						masteredRetestDue.push({ q, idx });
					} else {
						masteredRetestCooling.push({ q, idx });
					}
				} else if (st === "unseen" || st === "in_progress") {
					masteredUnseen.push({ q, idx });
				}
			} else if (st === "unseen" || st === "in_progress") {
				otherUnseenExists = true;
			}
		});

		const sortRetest = (a, b) =>
			(LEVEL_RANK[String(a.q.level || "basics")] ?? 1) - (LEVEL_RANK[String(b.q.level || "basics")] ?? 1)
			|| (Number.isInteger(a.q.lastStep) ? a.q.lastStep : -1) - (Number.isInteger(b.q.lastStep) ? b.q.lastStep : -1)
			|| a.idx - b.idx;

		if (masteredRetestDue.length > 0) {
			masteredRetestDue.sort(sortRetest);
			return formatQuestionSummary("Retest", masteredRetestDue[0].q);
		}
		if (masteredUnseen.length > 0) {
			const chosenQ = selectInterleavedQuestion(masteredUnseen, topicOrderMap, tracker);
			return formatQuestionSummary("Solve", chosenQ);
		}
		if (masteredRetestCooling.length > 0 && !hasRemainingTopics && !otherUnseenExists) {
			masteredRetestCooling.sort(sortRetest);
			return formatQuestionSummary("Retest", masteredRetestCooling[0].q);
		}

		const pendingTopics = topics
			.map((t, idx) => ({ t, idx }))
			.filter(({ t }) => {
				const st = String(t.status || "pending");
				return st === "pending" || st === "probed";
			});
		if (pendingTopics.length > 0) {
			const unlocked = pendingTopics.filter(({ t }) => {
				const prereqs = Array.isArray(t.prereqTopicIds) ? t.prereqTopicIds : [];
				return prereqs.every((pid) => !topicsById.has(pid) || masteredIds.has(pid));
			});
			const pool = unlocked.length > 0 ? unlocked : pendingTopics;
			pool.sort((a, b) =>
				(Number(a.t.order) || 9999) - (Number(b.t.order) || 9999)
				|| (LEVEL_RANK[String(a.t.level || "basics")] ?? 1) - (LEVEL_RANK[String(b.t.level || "basics")] ?? 1)
				|| a.idx - b.idx,
			);
			const chosen = pool[0].t;
			return `Teach Topic "${chosen.title || chosen.id}"${formatTopicHint(chosen)}`;
		}

		const remRetestDue = [];
		const remUnseen = [];
		const remRetestCooling = [];
		questions.forEach((q, idx) => {
			if (!q || typeof q !== "object") return;
			const st = String(q.status || "unseen");
			if (DONE_Q_STATUSES.has(st)) return;
			if (st === "retest") {
				const ls = Number.isInteger(q.lastStep) ? q.lastStep : null;
				if (ls === null || stepCounter - ls >= retestGap) remRetestDue.push({ q, idx });
				else remRetestCooling.push({ q, idx });
			} else if (st === "unseen" || st === "in_progress") {
				remUnseen.push({ q, idx });
			}
		});
		if (remRetestDue.length > 0) {
			remRetestDue.sort(sortRetest);
			return formatQuestionSummary("Retest", remRetestDue[0].q);
		}
		if (remUnseen.length > 0) {
			const chosenQ = selectInterleavedQuestion(remUnseen, topicOrderMap, tracker);
			return formatQuestionSummary("Solve", chosenQ);
		}
		if (remRetestCooling.length > 0) {
			remRetestCooling.sort(sortRetest);
			return formatQuestionSummary("Retest", remRetestCooling[0].q);
		}

		if (!milestones.revisionSheetBuilt) {
			return jeeMode
				? `Build Formula & Trap Revision Sheet (chapters/${slug}-sheet.pdf)`
				: `Build Mastery & Pitfall Revision Sheet (chapters/${slug}-sheet.pdf)`;
		}
		if (!milestones.cbtMockPassed) {
			return jeeMode
				? `Run timed NTA JEE CBT chapter mock (exams/${slug}-mock.html)`
				: `Run timed mastery assessment (exams/${slug}-mock.html)`;
		}
		return `Chapter "${title}" complete (100% mastered)`;
	};

	const baseSummary = computeBase();
	if (doubtStack.length > 0) {
		const top = doubtStack[doubtStack.length - 1];
		const qText = String((top && top.query) || "Paused doubt").trim();
		return `Resolve doubt "${qText}" · resume: ${baseSummary}`;
	}
	return baseSummary;
}

function formatAutopilotLines(tracker) {
	const topics = Array.isArray(tracker.topics) ? tracker.topics : [];
	const questions = Array.isArray(tracker.questions) ? tracker.questions : [];
	const topicsMastered = topics.filter((t) => t && t.status === "mastered").length;
	const qDone = questions.filter((q) => q && DONE_Q_STATUSES.has(String(q.status || ""))).length;
	const qRetest = questions.filter((q) => q && q.status === "retest").length;
	const title = String(tracker.title || tracker.slug || "Untitled");
	const subject = String(tracker.subject || "General");
	const slug = String(tracker.slug || "chapter");
	const nextSummary = computeAutopilotNextSummary(tracker);
	return [
		`Active Chapter Autopilot: ${title} (${subject}, slug=${slug}) · Topics ${topicsMastered}/${topics.length} · Questions ${qDone}/${questions.length} (${qRetest} retest)`,
		`Autopilot Next: ${nextSummary}`,
	];
}

try {
	let rawStdin = "";
	try {
		rawStdin = readFileSync(0, "utf8").trim();
	} catch {
		rawStdin = "";
	}
	let payload = {};
	if (rawStdin) {
		try {
			const parsed = JSON.parse(rawStdin);
			if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
				payload = parsed;
			}
		} catch {
			payload = {};
		}
	}

	const wsEnv = process.env.SYNT_WORKSPACE || process.env.MLH_WORKSPACE || "";
	const root = wsEnv.trim() ? path.resolve(wsEnv.trim()) : repoRoot;

	const customKgEnv = process.env.SYNT_KG_DIR || process.env.MLH_KG_DIR || "";
	const kgDir = customKgEnv.trim()
		? path.resolve(customKgEnv.trim())
		: path.join(root, "knowledge-graph");

	const customChaptersEnv = process.env.SYNT_CHAPTERS_DIR || process.env.MLH_CHAPTERS_DIR || "";
	const chaptersDir = customChaptersEnv.trim()
		? path.resolve(customChaptersEnv.trim())
		: path.join(root, "chapters");

	// Fresh-clone auto-bootstrap: ONLY when running without a custom *_KG_DIR override
	// and knowledge-graph/graph-data.js does not exist yet on disk.
	const graphDataFile = path.join(kgDir, "graph-data.js");
	if (!customKgEnv.trim() && !existsSync(graphDataFile)) {
		const syncScript = path.join(repoRoot, "scripts", "sync-graph.mjs");
		if (existsSync(syncScript)) {
			try {
				const syncArgs = [syncScript, "--quiet"];
				if (wsEnv.trim()) syncArgs.push(`--workspace=${root}`);
				if (customChaptersEnv.trim()) syncArgs.push(`--chapters-dir=${chaptersDir}`);
				execFileSync(process.execPath, syncArgs, {
					cwd: root,
					stdio: "ignore",
					timeout: 6000,
				});
			} catch {
				// Best-effort fresh-clone bundle bootstrap.
			}
		}
	}

	const props = readJsonl(path.join(kgDir, "propositions.jsonl"));
	const chains = readJsonl(path.join(kgDir, "chains.jsonl"));
	const mistakes = readJsonl(path.join(kgDir, "mistakes.jsonl"));
	const chapters = listVisible(chaptersDir).filter((name) => !/-tracker\.json$/iu.test(name));
	const activeTracker = readActiveChapterTracker(chaptersDir);
	const sources = listVisible(path.join(root, "sources"));
	const demos = listVisible(path.join(root, "demos")).filter(
		(name) => !/-template\.html$/iu.test(name),
	);

	const counts = { solid: 0, shaky: 0, misconception: 0, planned: 0 };
	const today = new Date().toISOString().slice(0, 10);
	const due = [];
	const weak = [];

	for (const p of props) {
		if (p.status in counts) counts[p.status]++;
		if (p.status === "shaky" || p.status === "misconception") {
			weak.push(`${p.id} (${p.status})`);
		} else if (typeof p.nextReview === "string" && p.nextReview <= today) {
			due.push(`${p.id} (due ${p.nextReview})`);
		}
	}

	const catCounts = new Map();
	const chapterCounts = new Map();
	for (const m of mistakes) {
		if (!m || typeof m !== "object") continue;
		const cat = typeof m.category === "string" && m.category.trim() ? m.category.trim() : "unknown";
		catCounts.set(cat, (catCounts.get(cat) || 0) + 1);
		const rawCh = typeof m.chapter === "string" ? m.chapter.trim() : "";
		if (rawCh) {
			const chKey = rawCh.toLowerCase();
			const existing = chapterCounts.get(chKey);
			if (existing) existing.count++;
			else chapterCounts.set(chKey, { name: rawCh, count: 1 });
		}
	}

	const topCats = [...catCounts.entries()]
		.sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
		.slice(0, 3);
	const hottestChapters = [...chapterCounts.values()]
		.sort((a, b) => b.count - a.count || a.name.localeCompare(b.name))
		.slice(0, 3);

	const lines = [
		`# Syntopik — Live Study Snapshot (${today})`,
		`Knowledge Graph: ${props.length} propositions (${counts.solid} solid, ${counts.shaky} shaky, ${counts.misconception} misconception, ${counts.planned} planned) · ${chains.length} chains`,
		`Chapters saved (${chapters.length}): ${chapters.slice(0, 12).join(", ") || "none yet"}`,
		`Sources available (${sources.length}): ${sources.slice(0, 12).join(", ") || "none yet"}`,
		`Interactive demos (${demos.length}): ${demos.slice(0, 8).join(", ") || "none yet"}`,
	];
	if (activeTracker) {
		lines.push(...formatAutopilotLines(activeTracker));
	}
	if (weak.length > 0) lines.push(`Weak/Misconception priority: ${weak.slice(0, 8).join("; ")}`);
	if (due.length > 0) lines.push(`Due for spaced review: ${due.slice(0, 8).join("; ")}`);
	if (mistakes.length > 0 && topCats.length > 0) {
		const catsStr = topCats.map(([cat, cnt]) => `${cat} (${cnt})`).join(", ");
		const chStr = hottestChapters.map((c) => `${c.name} (${c.count})`).join(", ");
		lines.push(
			chStr
				? `Top recurring mistake traps (${mistakes.length} logged): ${catsStr} · Hottest chapters: ${chStr}`
				: `Top recurring mistake traps (${mistakes.length} logged): ${catsStr}`,
		);
	}

	const snapshotText = lines.join("\n");
	const isExplicitAntigravity = Boolean(
		payload?.conversationId || payload?.workspacePaths || payload?.hookEventName,
	);
	const isStandardHook =
		!isExplicitAntigravity &&
		Boolean(
			payload?.hook_event_name ||
				payload?.session_id ||
				process.env.CLAUDE_PROJECT_DIR ||
				process.env.CLAUDECODE ||
				process.env.CODEX_HOME,
		);

	if (isStandardHook) {
		process.stdout.write(
			JSON.stringify({
				hookSpecificOutput: {
					hookEventName: "SessionStart",
					additionalContext: snapshotText,
				},
			}),
		);
	} else {
		process.stdout.write(
			JSON.stringify({
				injectSteps: [
					{
						systemMessage: {
							systemMessage: snapshotText,
						},
					},
				],
			}),
		);
	}
} catch {
	process.stdout.write("{}");
}
