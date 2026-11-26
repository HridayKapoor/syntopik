/**
 * Workspace IO helpers for syntopik-studio MCP App & ambient headless tools:
 *  - Resolve scripts/ from workspaceRoot first, then PACKAGE_ROOT (plugin-cache & attached-repo safe!)
 *  - Read knowledge-graph/*.jsonl cleanly (0-byte / missing safe)
 *  - Compute due flashcards with LearnHarness multi-cue weights + safe offline occlusion media
 *  - Rate flashcards via srs_engine.py review (returning FIRe refresh credits + revlog persistence)
 *  - Ambient headless helpers: captureConcept(), getFrontier(), logReview()
 *  - Save rough-work canvas PNG sketches into sources/attempts/
 */

import fs from "node:fs";
import path from "node:path";
import crypto from "node:crypto";
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const MODULE_DIR = path.dirname(fileURLToPath(import.meta.url));

const CUE_WEIGHTS = {
  again: 4,
  hard: 3,
  untried: 3,
  good: 2,
  easy: 1,
};

const VALID_RATINGS = new Set(["again", "hard", "good", "easy"]);

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

export function resolvePackageRoot() {
  const candidates = [
    path.resolve(MODULE_DIR, ".."),
    path.resolve(MODULE_DIR, "..", "..", ".."),
    process.env.CLAUDE_PLUGIN_ROOT || "",
    process.env.SYNT_PACKAGE_ROOT || "",
  ].filter(Boolean);

  for (const cand of candidates) {
    const abs = path.resolve(cand);
    if (fs.existsSync(path.join(abs, "scripts", "sync-graph.mjs"))) {
      return abs;
    }
  }
  return path.resolve(MODULE_DIR, "..", "..", "..");
}

export function resolveScriptPath(workspaceRoot, scriptName) {
  const pkgRoot = resolvePackageRoot();
  const candidates = [
    path.join(workspaceRoot, "scripts", scriptName),
    path.join(pkgRoot, "scripts", scriptName),
    path.resolve(MODULE_DIR, "..", "scripts", scriptName),
    path.resolve(MODULE_DIR, "..", "..", "..", "scripts", scriptName),
  ];
  if (process.env.CLAUDE_PLUGIN_ROOT) {
    candidates.push(path.join(process.env.CLAUDE_PLUGIN_ROOT, "scripts", scriptName));
  }
  for (const cand of candidates) {
    if (fs.existsSync(cand)) return cand;
  }
  return null;
}

export function resolveKgDir(workspaceRoot) {
  const customKg = process.env.SYNT_KG_DIR || process.env.MLH_KG_DIR || "";
  return customKg.trim()
    ? path.resolve(customKg.trim())
    : path.join(workspaceRoot, "knowledge-graph");
}

function writeJsonlAtomic(filePath, rows) {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  const content = rows.length > 0 ? `${rows.map((r) => JSON.stringify(r)).join("\n")}\n` : "";
  const tmp = path.join(
    path.dirname(filePath),
    `.${path.basename(filePath)}.${process.pid}.${crypto.randomBytes(4).toString("hex")}.tmp`
  );
  fs.writeFileSync(tmp, content, "utf8");
  fs.renameSync(tmp, filePath);
}

function appendJsonlSafe(filePath, obj) {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  fs.appendFileSync(filePath, `${JSON.stringify(obj)}\n`, "utf8");
}

function syncGraphQuiet(workspaceRoot, kgDir) {
  const syncScript = resolveScriptPath(workspaceRoot, "sync-graph.mjs");
  if (!syncScript) return false;
  try {
    const customKg = process.env.SYNT_KG_DIR || process.env.MLH_KG_DIR || "";
    const customCh = process.env.SYNT_CHAPTERS_DIR || process.env.MLH_CHAPTERS_DIR || "";
    const syncArgs = [syncScript, "--quiet", `--workspace=${workspaceRoot}`];
    if (customKg.trim() || path.resolve(kgDir) !== path.resolve(path.join(workspaceRoot, "knowledge-graph"))) {
      syncArgs.push(`--kg-dir=${kgDir}`);
    }
    if (customCh.trim()) {
      syncArgs.push(`--chapters-dir=${path.resolve(customCh.trim())}`);
    }
    execFileSync(process.execPath, syncArgs, {
      cwd: fs.existsSync(workspaceRoot) ? workspaceRoot : process.cwd(),
      stdio: "ignore",
      timeout: 10000,
    });
    return true;
  } catch {
    return false;
  }
}

function isJeeTracker(tracker) {
  const scopeProfile = String(
    tracker?.scopeCharter?.goalProfile ??
      tracker?.goalProfile ??
      tracker?.profile ??
      tracker?.config?.goalProfile ??
      ""
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
        /\b(?:jee|nta|eduniti|pyq)\b/i.test(`${s?.label || ""} ${s?.path || ""}`)
    )
  ) {
    return true;
  }
  return /\b(?:jee|nta|iit)\b/i.test(
    `${tracker?.title || ""} ${tracker?.goal || ""} ${tracker?.examTarget || ""}`
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
    return `Index ${sources.length} source file${sources.length === 1 ? "" : "s"}`;
  }
  if (!milestones.probed) {
    return "Run Phase 1 frontier probe";
  }
  const inProg = topics.find((t) => t.status === "in_progress");
  if (inProg) {
    return `Teach topic: ${inProg.title || inProg.id}`;
  }
  const masteredIds = new Set(topics.filter((t) => t.status === "mastered").map((t) => t.id));
  const stepCounter = Number.isFinite(tracker.stepCounter) ? tracker.stepCounter : 0;
  const retestGap = Number.isFinite(tracker?.config?.retestGap) ? tracker.config.retestGap : 3;

  const dueRetest = questions.find(
    (q) =>
      q.status === "retest" &&
      (!q.topicId || masteredIds.has(q.topicId)) &&
      (q.lastStep == null || stepCounter - q.lastStep >= retestGap)
  );
  if (dueRetest) {
    return `Retest: ${dueRetest.label || dueRetest.qid}`;
  }
  const nextQ = questions.find(
    (q) =>
      (q.status === "unseen" || q.status === "in_progress" || !q.status) &&
      (!q.topicId || masteredIds.has(q.topicId))
  );
  if (nextQ) {
    return `Solve: ${nextQ.label || nextQ.qid}`;
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
      ? "Build 1-page Typst formula & trap sheet"
      : "Build 1-page Typst mastery & pitfall sheet";
  }
  if (!milestones.cbtMockPassed) {
    return jeeMode
      ? "Build & pass chapter NTA CBT mock"
      : "Build & pass timed mastery assessment";
  }
  return "Chapter complete";
}

export function loadChapterTrackers(workspaceRoot) {
  const customCh = process.env.SYNT_CHAPTERS_DIR || process.env.MLH_CHAPTERS_DIR || "";
  const chaptersDir = customCh.trim()
    ? path.resolve(customCh.trim())
    : path.join(workspaceRoot, "chapters");
  if (!fs.existsSync(chaptersDir)) {
    return { chapterTrackers: [], activeChapter: null };
  }

  let entries = [];
  try {
    entries = fs.readdirSync(chaptersDir, { withFileTypes: true });
  } catch {
    return { chapterTrackers: [], activeChapter: null };
  }

  const trackers = [];
  for (const entry of entries) {
    if (!entry.isFile() || !/-tracker\.json$/i.test(entry.name)) continue;
    const full = path.join(chaptersDir, entry.name);
    let raw;
    try {
      raw = JSON.parse(fs.readFileSync(full, "utf8"));
    } catch {
      continue;
    }
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) continue;

    const slug =
      typeof raw.slug === "string" && raw.slug.trim()
        ? raw.slug.trim()
        : entry.name.replace(/-tracker\.json$/i, "");
    const title =
      typeof raw.title === "string" && raw.title.trim() ? raw.title.trim() : slug;
    const subject =
      typeof raw.subject === "string" && raw.subject.trim() ? raw.subject.trim() : "General";
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

    let mtimeIso = "";
    try {
      mtimeIso = fs.statSync(full).mtime.toISOString();
    } catch {
      mtimeIso = "";
    }

    trackers.push({
      slug,
      title,
      subject,
      active,
      updatedAt: typeof raw.updatedAt === "string" ? raw.updatedAt : mtimeIso,
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
      nextHint: computeTrackerNextHint(raw, topics, questions, milestones),
    });
  }

  trackers.sort((a, b) => {
    if (a.active !== b.active) return a.active ? -1 : 1;
    const da = String(a.updatedAt || "");
    const db = String(b.updatedAt || "");
    if (da !== db) return db.localeCompare(da);
    return a.slug.localeCompare(b.slug);
  });

  const activeChapter = trackers.find((t) => t.active) || null;
  return { chapterTrackers: trackers, activeChapter };
}

export function readJsonl(filePath) {
  if (!fs.existsSync(filePath)) return [];
  const raw = fs.readFileSync(filePath, "utf8");
  const rows = [];
  for (const line of raw.split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed) continue;
    try {
      const obj = JSON.parse(trimmed);
      if (obj && typeof obj === "object" && !Array.isArray(obj)) {
        rows.push(obj);
      }
    } catch {
      // Skip malformed line gracefully
    }
  }
  return rows;
}

export function computeCalibrationSummary(revlog) {
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
      hypercorrectionCount,
      luckyGuessCount,
    };
  }

  let brierSum = 0;
  let biasSum = 0;
  let accSum = 0;
  let confSum = 0;
  for (const { c, y } of windowItems) {
    const diff = c - y;
    brierSum += diff * diff;
    biasSum += diff;
    accSum += y;
    confSum += c;
  }

  return {
    sampleCount: n,
    totalReviews,
    brierScore: Number((brierSum / n).toFixed(4)),
    overconfidenceBias: Number((biasSum / n).toFixed(4)),
    accuracy: Number((accSum / n).toFixed(4)),
    meanConfidence: Number((confSum / n).toFixed(4)),
    hypercorrectionCount,
    luckyGuessCount,
  };
}

function inlineWorkspaceImageDataUri(workspaceRoot, rawSrc) {
  if (typeof rawSrc !== "string" || !rawSrc.trim()) return null;
  const src = rawSrc.trim();
  if (src.startsWith("data:image/")) return src;
  if (/^https?:\/\//i.test(src)) return null;
  const absRoot = path.resolve(workspaceRoot);
  const absCandidate = path.isAbsolute(src) ? path.resolve(src) : path.resolve(absRoot, src);
  if (!absCandidate.startsWith(absRoot + path.sep)) return null;
  if (!fs.existsSync(absCandidate)) return null;
  try {
    const st = fs.statSync(absCandidate);
    if (!st.isFile() || st.size === 0 || st.size > 512 * 1024) return null;
    const ext = path.extname(absCandidate).toLowerCase();
    const mimeMap = {
      ".svg": "image/svg+xml",
      ".png": "image/png",
      ".jpg": "image/jpeg",
      ".jpeg": "image/jpeg",
      ".webp": "image/webp",
    };
    const mime = mimeMap[ext];
    if (!mime) return null;
    const b64 = fs.readFileSync(absCandidate).toString("base64");
    return `data:${mime};base64,${b64}`;
  } catch {
    return null;
  }
}

function resolveCardMedia(workspaceRoot, prop, cueObj) {
  const rawMedia = (cueObj && cueObj.media) || prop.media || null;
  const rawOcc =
    (cueObj && cueObj.occlusion) ||
    (rawMedia && rawMedia.occlusion) ||
    prop.occlusion ||
    null;

  if (!rawMedia && !rawOcc) return { media: null, occlusion: null };

  const media = rawMedia && typeof rawMedia === "object" && !Array.isArray(rawMedia)
    ? { ...rawMedia }
    : typeof rawMedia === "string"
      ? { src: rawMedia }
      : null;

  let occlusion = rawOcc;
  if (occlusion && typeof occlusion === "object" && !Array.isArray(occlusion)) {
    occlusion = { ...occlusion };
    if (occlusion.src && !occlusion.dataUri) {
      const uri = inlineWorkspaceImageDataUri(workspaceRoot, occlusion.src);
      if (uri) occlusion.dataUri = uri;
    }
  }
  if (media && media.src && !media.dataUri) {
    const uri = inlineWorkspaceImageDataUri(workspaceRoot, media.src);
    if (uri) media.dataUri = uri;
  }
  return { media, occlusion };
}

export function selectWeightedCue(prop, seed = null) {
  const cues = Array.isArray(prop.cues)
    ? prop.cues.filter((c) => c && typeof c === "object" && (c.q || c.a))
    : [];
  if (cues.length === 0) {
    return {
      id: null,
      q: prop.prompt || `State and justify: ${prop.title || prop.id}`,
      a: [prop.title, prop.notes ? `Notes / Traps: ${prop.notes}` : ""].filter(Boolean).join("\n\n"),
      lastRating: null,
      synthetic: true,
      index: 0,
      totalCues: 1,
      media: prop.media || null,
      occlusion: prop.occlusion || null,
    };
  }
  const weighted = cues.map((c, idx) => {
    const key = typeof c.lastRating === "string" ? c.lastRating.toLowerCase() : "untried";
    const weight = CUE_WEIGHTS[key] ?? CUE_WEIGHTS.untried;
    return { cue: c, weight, idx };
  });
  const total = weighted.reduce((acc, w) => acc + w.weight, 0);
  let r;
  if (typeof seed === "number") {
    const x = Math.sin(seed + 1) * 10000;
    r = (x - Math.floor(x)) * total;
  } else {
    r = Math.random() * total;
  }
  let chosen = weighted[0];
  for (const item of weighted) {
    r -= item.weight;
    if (r <= 0) {
      chosen = item;
      break;
    }
  }
  return {
    id: chosen.cue.id || `c${chosen.idx + 1}`,
    q: chosen.cue.q || prop.prompt || prop.title || prop.id,
    a: chosen.cue.a || prop.title || "",
    lastRating: chosen.cue.lastRating ?? null,
    synthetic: false,
    index: chosen.idx,
    totalCues: cues.length,
    media: chosen.cue.media || null,
    occlusion: chosen.cue.occlusion || null,
  };
}

export function loadWorkspaceState(workspaceRoot, section = "all") {
  const kgDir = resolveKgDir(workspaceRoot);
  const today = new Date().toISOString().slice(0, 10);

  const propositions = readJsonl(path.join(kgDir, "propositions.jsonl"));
  const chains = readJsonl(path.join(kgDir, "chains.jsonl"));
  const mistakes = readJsonl(path.join(kgDir, "mistakes.jsonl"));
  const examAttempts = readJsonl(path.join(kgDir, "exam-attempts.jsonl"));
  const revlog = readJsonl(path.join(kgDir, "revlog.jsonl"));

  const statusCounts = { solid: 0, shaky: 0, misconception: 0, planned: 0 };
  let bktSum = 0;
  let bktCount = 0;

  const flashcards = [];
  for (const p of propositions) {
    const st = typeof p.status === "string" ? p.status : "planned";
    statusCounts[st] = (statusCounts[st] || 0) + 1;
    if (typeof p.bktMastery === "number" && Number.isFinite(p.bktMastery)) {
      bktSum += p.bktMastery;
      bktCount += 1;
    }
    const nextReview = typeof p.nextReview === "string" ? p.nextReview : null;
    const isDue =
      st === "misconception" ||
      st === "shaky" ||
      (nextReview !== null && nextReview <= today) ||
      st === "planned";
    const urgency =
      st === "misconception" ? 0 : st === "shaky" ? 1 : nextReview && nextReview <= today ? 2 : st === "planned" ? 3 : 4;
    const cue = selectWeightedCue(p);
    const { media, occlusion } = resolveCardMedia(workspaceRoot, p, cue);
    flashcards.push({
      propId: p.id,
      title: p.title || p.id,
      subject: p.subject || "General",
      chapter: p.chapter || "General",
      status: st,
      bktMastery: typeof p.bktMastery === "number" ? p.bktMastery : null,
      nextReview,
      notes: p.notes || "",
      isDue,
      urgency,
      cue,
      cuesCount: Array.isArray(p.cues) ? p.cues.length : 0,
      ...(media ? { media } : {}),
      ...(occlusion ? { occlusion } : {}),
    });
  }

  flashcards.sort((a, b) => a.urgency - b.urgency || String(a.nextReview || "").localeCompare(String(b.nextReview || "")));
  const dueCount = flashcards.filter((f) => f.isDue && f.status !== "planned").length;
  const reviewableDueCount = flashcards.filter((f) => f.isDue).length;

  const categoryCounts = new Map();
  for (const m of mistakes) {
    const cat = String(m.category || "uncategorized");
    categoryCounts.set(cat, (categoryCounts.get(cat) || 0) + 1);
  }
  const topCategories = [...categoryCounts.entries()]
    .map(([category, count]) => ({ category, count }))
    .sort((a, b) => b.count - a.count);

  const normalizedChains = chains.map((c) => {
    if (Array.isArray(c.premises) && c.conclusion) {
      return {
        premises: c.premises.map(String),
        conclusion: String(c.conclusion),
        relation: c.relation || "derives",
        note: c.note || c.why || "",
        isHyperedge: c.premises.length >= 2,
      };
    }
    return {
      premises: c.from ? [String(c.from)] : [],
      conclusion: c.to ? String(c.to) : "",
      relation: c.relation || "requires",
      note: c.why || c.note || "",
      isHyperedge: false,
    };
  });

  const { chapterTrackers, activeChapter } = loadChapterTrackers(workspaceRoot);
  const calibration = computeCalibrationSummary(revlog);

  const stats = {
    today,
    totalPropositions: propositions.length,
    ...statusCounts,
    avgBktMastery: bktCount > 0 ? Math.round((bktSum / bktCount) * 1000) / 1000 : null,
    totalChains: normalizedChains.length,
    totalMistakes: mistakes.length,
    totalExams: examAttempts.length,
    dueCount,
    reviewableDueCount,
    trackersCount: chapterTrackers.length,
    activeChapterPct: activeChapter ? activeChapter.pct : null,
    activeChapterSlug: activeChapter ? activeChapter.slug : null,
    brierScore: calibration.brierScore,
  };

  const payload = {
    workspaceName: path.basename(workspaceRoot),
    stats,
    calibration,
  };

  if (section === "all" || section === "autopilot") {
    payload.chapterTrackers = chapterTrackers;
    payload.activeChapter = activeChapter;
  }
  if (section === "all" || section === "graph") {
    payload.propositions = propositions.slice(0, 300);
    payload.chains = normalizedChains.slice(0, 500);
  }
  if (section === "all" || section === "flashcards") {
    payload.flashcards = flashcards.slice(0, 200);
  }
  if (section === "all" || section === "traps") {
    payload.mistakes = mistakes.slice(-150).reverse();
    payload.topCategories = topCategories;
  }

  return payload;
}

function findExecutableOnPath(names) {
  const pathEnv = process.env.PATH || "";
  const dirs = pathEnv.split(path.delimiter).filter(Boolean);
  const isWin = process.platform === "win32";
  const exts = isWin
    ? (process.env.PATHEXT || ".EXE;.CMD;.BAT;.COM").split(";").filter(Boolean)
    : [""];
  for (const name of names) {
    for (const dir of dirs) {
      for (const ext of exts) {
        const candidate = path.join(dir, isWin && !name.toLowerCase().endsWith(ext.toLowerCase()) ? `${name}${ext}` : name);
        try {
          if (fs.existsSync(candidate) && fs.statSync(candidate).isFile()) {
            return candidate;
          }
        } catch {
          // Ignore unreadable PATH entries
        }
      }
    }
  }
  return null;
}

function resolvePythonBin(workspaceRoot) {
  const roots = [workspaceRoot, resolvePackageRoot()];
  for (const r of roots) {
    const venvPosix = path.join(r, ".venv", "bin", "python");
    if (fs.existsSync(venvPosix)) return venvPosix;
    const venvWin = path.join(r, ".venv", "Scripts", "python.exe");
    if (fs.existsSync(venvWin)) return venvWin;
  }
  const preferred = process.platform === "win32" ? ["python", "python3"] : ["python3", "python"];
  return findExecutableOnPath(preferred) || preferred[0];
}

function normalizeConfidence(rawConf) {
  if (typeof rawConf !== "number" || !Number.isFinite(rawConf)) return null;
  const val = rawConf > 1 ? rawConf / 100 : rawConf;
  return Math.max(0, Math.min(1, Number(val.toFixed(4))));
}

function slugifyPart(text, maxLen = 28) {
  return String(text || "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, maxLen);
}

function tokenizeSimple(text) {
  return String(text || "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, " ")
    .trim()
    .split(/\s+/)
    .filter((w) => w.length >= 2);
}

function findSimilarFallbackJs(props, query) {
  const qNorm = String(query || "").trim().toLowerCase().replace(/\s+/g, " ");
  const qSet = new Set(tokenizeSimple(query));
  if (!qNorm || qSet.size === 0) return [];
  const hits = [];
  for (const p of props) {
    const pid = String(p.id || "").trim().toLowerCase();
    const titleNorm = String(p.title || "").trim().toLowerCase().replace(/\s+/g, " ");
    if (qNorm === pid || qNorm === titleNorm) {
      hits.push({ id: p.id, title: p.title || p.id, score: 1.0 });
      continue;
    }
    const candTokens = new Set(tokenizeSimple(`${p.id || ""} ${p.title || ""} ${p.statement || ""} ${p.notes || ""}`));
    if (candTokens.size === 0) continue;
    let inter = 0;
    for (const t of qSet) {
      if (candTokens.has(t)) inter += 1;
    }
    const dice = (2 * inter) / (qSet.size + candTokens.size);
    if (dice >= 0.5) {
      hits.push({ id: p.id, title: p.title || p.id, score: Number(dice.toFixed(4)) });
    }
  }
  hits.sort((a, b) => b.score - a.score || String(a.id).localeCompare(String(b.id)));
  return hits.slice(0, 3);
}

export function rateFlashcard(workspaceRoot, { propId, rating, cueId, misconception, confidence, durationMs }) {
  const cleanId = typeof propId === "string" ? propId.trim() : "";
  const cleanRating = typeof rating === "string" ? rating.trim().toLowerCase() : "";
  if (!cleanId || !/^[A-Za-z0-9._:/-]+$/.test(cleanId)) {
    return { ok: false, error: `Invalid propId: ${JSON.stringify(propId)}` };
  }
  if (!VALID_RATINGS.has(cleanRating)) {
    return { ok: false, error: `rating must be again, hard, good, or easy (got ${JSON.stringify(rating)})` };
  }

  const pythonBin = resolvePythonBin(workspaceRoot);
  const srsScript = resolveScriptPath(workspaceRoot, "srs_engine.py");
  const kgDir = resolveKgDir(workspaceRoot);

  if (!srsScript) {
    return { ok: false, error: `scripts/srs_engine.py not found in workspace ${workspaceRoot} or package root` };
  }

  const normConf = normalizeConfidence(confidence);
  const cleanDur =
    typeof durationMs === "number" && Number.isFinite(durationMs) && durationMs >= 0
      ? Math.round(durationMs)
      : null;

  const buildArgs = ({ includeCue, includeV2Flags }) => {
    const argv = ["-B", srsScript, "--kg-dir", kgDir, "review", cleanId, "--rating", cleanRating, "--no-sync"];
    if (includeCue && typeof cueId === "string" && cueId.trim()) {
      argv.push("--cue-id", cueId.trim());
    }
    if (misconception) {
      argv.push("--misconception");
    }
    if (includeV2Flags && normConf !== null) {
      argv.push("--confidence", String(normConf));
    }
    if (includeV2Flags && cleanDur !== null) {
      argv.push("--duration-ms", String(cleanDur));
    }
    return argv;
  };

  let stdout;
  try {
    stdout = execFileSync(pythonBin, buildArgs({ includeCue: true, includeV2Flags: true }), {
      cwd: fs.existsSync(workspaceRoot) ? workspaceRoot : process.cwd(),
      encoding: "utf8",
      timeout: 15000,
    });
  } catch (err) {
    const stderr = String(err?.stderr || err?.message || "");
    if (stderr.includes("unrecognized arguments")) {
      const retryIncludeCue = !(cueId && stderr.includes("--cue-id"));
      try {
        stdout = execFileSync(pythonBin, buildArgs({ includeCue: retryIncludeCue, includeV2Flags: false }), {
          cwd: fs.existsSync(workspaceRoot) ? workspaceRoot : process.cwd(),
          encoding: "utf8",
          timeout: 15000,
        });
      } catch (retryErr) {
        return { ok: false, error: `srs_engine.py failed: ${String(retryErr?.stderr || retryErr?.message || retryErr)}` };
      }
    } else {
      return { ok: false, error: `srs_engine.py failed: ${stderr.trim() || String(err)}` };
    }
  }

  const synced = syncGraphQuiet(workspaceRoot, kgDir);

  let parsed = {};
  try {
    parsed = JSON.parse(stdout);
  } catch {
    parsed = { raw: stdout.trim() };
  }

  const fireCredits = Array.isArray(parsed.fireCredits)
    ? parsed.fireCredits
    : Array.isArray(parsed.fire_credits)
      ? parsed.fire_credits
      : [];
  const fireRefreshedCount = fireCredits.length;
  const fireSummary =
    fireRefreshedCount > 0
      ? `FIRe refreshed ${fireRefreshedCount} prerequisite${fireRefreshedCount === 1 ? "" : "s"}`
      : null;

  return {
    ok: true,
    synced,
    result: {
      ...parsed,
      fireCredits,
      fireRefreshedCount,
      ...(fireSummary ? { fireSummary } : {}),
    },
  };
}

export function captureConcept(
  workspaceRoot,
  { title, statement, subject, chapter, cuePrompt, cueAnswer, premises, relation, notes, kind, id }
) {
  const cleanTitle = typeof title === "string" ? title.trim() : "";
  const cleanStatement = typeof statement === "string" ? statement.trim() : "";
  const cleanSubject = typeof subject === "string" && subject.trim() ? subject.trim() : "General";
  const cleanChapter = typeof chapter === "string" && chapter.trim() ? chapter.trim() : "General";
  if (!cleanTitle) return { ok: false, error: "title must be a non-empty string." };
  if (!cleanStatement) return { ok: false, error: "statement must be a non-empty string." };

  const kgDir = resolveKgDir(workspaceRoot);
  fs.mkdirSync(kgDir, { recursive: true });
  const propsFile = path.join(kgDir, "propositions.jsonl");
  const chainsFile = path.join(kgDir, "chains.jsonl");
  const props = readJsonl(propsFile);
  const existingChains = readJsonl(chainsFile);

  // Deduplicate via srs_engine.py find-similar (with pure-JS fallback if python/venv absent)
  let matches = [];
  const srsScript = resolveScriptPath(workspaceRoot, "srs_engine.py");
  if (props.length > 0 && srsScript) {
    try {
      const pyBin = resolvePythonBin(workspaceRoot);
      const out = execFileSync(
        pyBin,
        ["-B", srsScript, "--kg-dir", kgDir, "find-similar", cleanTitle, "--top", "3", "--threshold", "0.72"],
        {
          cwd: fs.existsSync(workspaceRoot) ? workspaceRoot : process.cwd(),
          encoding: "utf8",
          timeout: 12000,
        }
      );
      const parsed = JSON.parse(out);
      if (Array.isArray(parsed?.matches)) matches = parsed.matches;
    } catch {
      matches = findSimilarFallbackJs(props, cleanTitle);
    }
  } else if (props.length > 0) {
    matches = findSimilarFallbackJs(props, cleanTitle);
  }

  const topMatch = matches.length > 0 ? matches[0] : null;
  const exactIdHit =
    typeof id === "string" && id.trim() ? props.find((p) => p.id === id.trim()) : null;
  const dupTarget =
    exactIdHit ||
    (topMatch && Number(topMatch.score) >= 0.78
      ? props.find((p) => p.id === topMatch.id) || null
      : null);

  const today = new Date().toISOString().slice(0, 10);
  const nowIso = new Date().toISOString();
  const cleanCueQ = typeof cuePrompt === "string" ? cuePrompt.trim() : "";
  const cleanCueA = typeof cueAnswer === "string" ? cueAnswer.trim() : "";
  let propId;
  let deduplicated = false;
  let cueAdded = false;

  if (dupTarget) {
    deduplicated = true;
    propId = String(dupTarget.id);
    if (cleanCueQ) {
      if (!Array.isArray(dupTarget.cues)) dupTarget.cues = [];
      const alreadyHasCue = dupTarget.cues.some(
        (c) => String(c?.q || "").trim().toLowerCase() === cleanCueQ.toLowerCase()
      );
      if (!alreadyHasCue) {
        const nextIdx = dupTarget.cues.length + 1;
        dupTarget.cues.push({
          id: `c${nextIdx}`,
          q: cleanCueQ,
          a: cleanCueA || cleanStatement,
          lastRating: null,
          reviews: 0,
        });
        cueAdded = true;
      }
    }
    if (cleanStatement && !String(dupTarget.notes || "").includes(cleanStatement)) {
      dupTarget.notes = dupTarget.notes ? `${dupTarget.notes} | ${cleanStatement}` : cleanStatement;
    }
    dupTarget.updatedAt = nowIso;
    writeJsonlAtomic(propsFile, props);
  } else {
    const existingIds = new Set(props.map((p) => String(p.id || "")));
    let baseId =
      typeof id === "string" && id.trim()
        ? slugifyPart(id.trim(), 64)
        : [slugifyPart(cleanSubject, 14), slugifyPart(cleanChapter, 18), slugifyPart(cleanTitle, 28)]
            .filter(Boolean)
            .join("-") || `prop-${Date.now().toString(36)}`;
    propId = baseId;
    let suffix = 2;
    while (existingIds.has(propId)) {
      propId = `${baseId}-${suffix}`;
      suffix += 1;
    }

    const cues = [];
    if (cleanCueQ || cleanCueA) {
      cues.push({
        id: "c1",
        q: cleanCueQ || `State and justify: ${cleanTitle}`,
        a: cleanCueA || cleanStatement,
        lastRating: null,
        reviews: 0,
      });
      cueAdded = true;
    }

    const newProp = {
      id: propId,
      subject: cleanSubject,
      chapter: cleanChapter,
      title: cleanTitle,
      statement: cleanStatement,
      kind: typeof kind === "string" && kind.trim() ? kind.trim() : "concept",
      status: "shaky",
      bktMastery: 0.35,
      confidence: 0.35,
      notes: typeof notes === "string" && notes.trim() ? notes.trim() : cleanStatement,
      createdAt: nowIso,
      lastTested: today,
      nextReview: today,
      ...(cues.length > 0 ? { cues } : {}),
    };
    props.push(newProp);
    writeJsonlAtomic(propsFile, props);
  }

  let chainAdded = false;
  const cleanPremises = Array.isArray(premises)
    ? [...new Set(premises.map((p) => String(p || "").trim()).filter((p) => p && p !== propId))]
    : [];
  if (cleanPremises.length > 0) {
    const chainKey = `${[...cleanPremises].sort().join("+")}>${propId}`;
    const alreadyExists = existingChains.some((c) => {
      const concl = String(c.conclusion || c.to || "").trim();
      if (concl !== propId) return false;
      const prems = Array.isArray(c.premises)
        ? c.premises.map((x) => String(x || "").trim()).filter(Boolean)
        : c.from
          ? [String(c.from).trim()]
          : [];
      return `${[...new Set(prems)].sort().join("+")}>${concl}` === chainKey;
    });
    if (!alreadyExists) {
      const rel = typeof relation === "string" && relation.trim() ? relation.trim() : "requires";
      const rec = {
        premises: cleanPremises,
        conclusion: propId,
        relation: rel,
        why: cleanStatement,
        ...(cleanPremises.length === 1 ? { from: cleanPremises[0], to: propId } : { to: propId }),
      };
      existingChains.push(rec);
      writeJsonlAtomic(chainsFile, existingChains);
      chainAdded = true;
    }
  }

  const synced = syncGraphQuiet(workspaceRoot, kgDir);

  return {
    ok: true,
    id: propId,
    deduplicated,
    matchedId: deduplicated ? propId : null,
    matchedScore: topMatch ? topMatch.score : null,
    cueAdded,
    chainAdded,
    synced,
  };
}

function compressDueItemsJs(dueItems, chains) {
  const dueIds = new Set(dueItems.map((d) => d.id));
  const parentsOf = new Map();
  for (const c of chains) {
    const concl = String(c.conclusion || c.to || "").trim();
    const rel = String(c.relation || "requires").trim().toLowerCase();
    if (!concl || rel === "contrasts_with" || rel === "analogous") continue;
    const prems = Array.isArray(c.premises)
      ? c.premises.map((x) => String(x || "").trim()).filter(Boolean)
      : c.from
        ? [String(c.from).trim()]
        : [];
    if (!parentsOf.has(concl)) parentsOf.set(concl, new Set());
    for (const pr of prems) {
      if (pr && pr !== concl) parentsOf.get(concl).add(pr);
    }
  }

  const coveredBy = new Map();
  for (const item of dueItems) {
    const visited = new Set();
    const queue = [{ id: item.id, depth: 0 }];
    while (queue.length > 0) {
      const cur = queue.shift();
      if (cur.depth >= 3) continue;
      for (const pr of parentsOf.get(cur.id) || []) {
        if (visited.has(pr)) continue;
        visited.add(pr);
        queue.push({ id: pr, depth: cur.depth + 1 });
      }
    }
    const dueAnc = [...visited].filter((id) => dueIds.has(id) && id !== item.id);
    if (dueAnc.length >= 2) {
      coveredBy.set(item.id, dueAnc);
    }
  }

  const knockedOut = new Set();
  const kept = [];
  for (const item of dueItems) {
    if (knockedOut.has(item.id)) continue;
    const cov = coveredBy.get(item.id) || [];
    for (const ancId of cov) {
      const ancItem = dueItems.find((x) => x.id === ancId);
      if (ancItem && ancItem.status !== "misconception") {
        knockedOut.add(ancId);
      }
    }
    kept.push({
      ...item,
      ...(cov.length >= 2 ? { fireKnockouts: cov } : {}),
    });
  }
  return { items: kept, knockedOutIds: [...knockedOut] };
}

export function getFrontier(workspaceRoot, { limit = 10, compress = true } = {}) {
  const cleanLimit = Math.max(1, Math.min(50, Math.round(Number(limit) || 10)));
  const useCompress = compress !== false;
  const kgDir = resolveKgDir(workspaceRoot);
  const today = new Date().toISOString().slice(0, 10);

  const propositions = readJsonl(path.join(kgDir, "propositions.jsonl"));
  const chains = readJsonl(path.join(kgDir, "chains.jsonl"));
  const mistakes = readJsonl(path.join(kgDir, "mistakes.jsonl"));
  const revlog = readJsonl(path.join(kgDir, "revlog.jsonl"));
  const readingQueue = readJsonl(path.join(kgDir, "reading-queue.jsonl"));

  let dueItems = null;
  let dueCount = 0;
  let knockedOutIds = [];

  const srsScript = resolveScriptPath(workspaceRoot, "srs_engine.py");
  if (srsScript && propositions.length > 0) {
    const pyBin = resolvePythonBin(workspaceRoot);
    const runDue = (withCompress) => {
      const argv = ["-B", srsScript, "--kg-dir", kgDir, "due", "--limit", String(cleanLimit)];
      if (withCompress) argv.push("--compress");
      const out = execFileSync(pyBin, argv, {
        cwd: fs.existsSync(workspaceRoot) ? workspaceRoot : process.cwd(),
        encoding: "utf8",
        timeout: 12000,
      });
      return JSON.parse(out);
    };
    try {
      const parsed = runDue(useCompress);
      if (Array.isArray(parsed?.items)) {
        dueItems = parsed.items;
        dueCount = Number(parsed.dueCount ?? parsed.items.length);
        knockedOutIds = Array.isArray(parsed.knockedOutIds)
          ? parsed.knockedOutIds
          : Array.isArray(parsed.compressedOut)
            ? parsed.compressedOut
            : [];
      }
    } catch (err) {
      const stderr = String(err?.stderr || err?.message || "");
      if (useCompress && stderr.includes("unrecognized arguments")) {
        try {
          const parsed = runDue(false);
          if (Array.isArray(parsed?.items)) {
            const comp = compressDueItemsJs(parsed.items, chains);
            dueItems = comp.items.slice(0, cleanLimit);
            dueCount = Number(parsed.dueCount ?? parsed.items.length);
            knockedOutIds = comp.knockedOutIds;
          }
        } catch {
          dueItems = null;
        }
      }
    }
  }

  if (dueItems === null) {
    const rawDue = propositions
      .filter((p) => {
        const st = String(p.status || "planned");
        const nr = typeof p.nextReview === "string" ? p.nextReview : null;
        return st === "misconception" || st === "shaky" || (nr !== null && nr <= today);
      })
      .map((p) => ({
        id: p.id,
        title: p.title || p.id,
        subject: p.subject || "General",
        chapter: p.chapter || "",
        status: p.status || "planned",
        bktMastery: typeof p.bktMastery === "number" ? p.bktMastery : null,
        nextReview: p.nextReview || null,
        notes: p.notes || "",
      }))
      .sort((a, b) => {
        const r = (s) => (s === "misconception" ? 0 : s === "shaky" ? 1 : 2);
        return r(a.status) - r(b.status) || String(a.nextReview || "").localeCompare(String(b.nextReview || ""));
      });
    dueCount = rawDue.length;
    if (useCompress) {
      const comp = compressDueItemsJs(rawDue, chains);
      dueItems = comp.items.slice(0, cleanLimit);
      knockedOutIds = comp.knockedOutIds;
    } else {
      dueItems = rawDue.slice(0, cleanLimit);
    }
  }

  const { activeChapter } = loadChapterTrackers(workspaceRoot);
  const autopilot = activeChapter
    ? {
        slug: activeChapter.slug,
        title: activeChapter.title,
        subject: activeChapter.subject,
        pct: activeChapter.pct,
        topicsMastered: activeChapter.topicsMastered,
        topicsTotal: activeChapter.topicsTotal,
        questionsSolved: activeChapter.questionsSolved,
        questionsTotal: activeChapter.questionsTotal,
        nextAction: activeChapter.nextHint,
      }
    : null;

  const catCounts = new Map();
  for (const m of mistakes) {
    const cat = String(m.category || "concept_gap");
    catCounts.set(cat, (catCounts.get(cat) || 0) + 1);
  }
  const topCategories = [...catCounts.entries()]
    .map(([category, count]) => ({ category, count }))
    .sort((a, b) => b.count - a.count)
    .slice(0, 3);
  const topMistakeTraps = mistakes
    .slice(-3)
    .reverse()
    .map((m) => ({
      id: m.id || null,
      category: m.category || "concept_gap",
      subject: m.subject || "General",
      chapter: m.chapter || "",
      triggerCue: m.triggerCue || "",
      wrongMove: m.wrongMove || "",
      correctMove: m.correctMove || "",
    }));

  const calibration = computeCalibrationSummary(revlog);
  const leechesCount = propositions.filter((p) => {
    const lapses = Number(p.lapses ?? p?.fsrs?.lapses ?? 0);
    const diff = Number(p.difficulty ?? p?.fsrs?.difficulty ?? 0);
    return lapses >= 4 || diff >= 8.5;
  }).length;
  const readingQueueDueCount = readingQueue.filter((r) => String(r.stage || "queued") !== "crystallized").length;

  return {
    ok: true,
    today,
    compressed: useCompress,
    dueCount,
    dueItems,
    knockedOutCount: knockedOutIds.length,
    knockedOutIds,
    autopilot,
    topMistakeTraps,
    topCategories,
    calibration,
    leechesCount,
    readingQueueDueCount,
    stats: {
      totalPropositions: propositions.length,
      totalChains: chains.length,
      totalMistakes: mistakes.length,
      totalReviews: revlog.length,
    },
  };
}

export function logReview(workspaceRoot, { propId, rating, confidence, cueId, misconception, durationMs }) {
  const kgDir = resolveKgDir(workspaceRoot);
  const revlogPath = path.join(kgDir, "revlog.jsonl");
  const beforeSize = fs.existsSync(revlogPath) ? fs.statSync(revlogPath).size : 0;

  const rated = rateFlashcard(workspaceRoot, {
    propId,
    rating,
    cueId,
    misconception,
    confidence,
    durationMs,
  });
  if (!rated.ok) return rated;

  const normConf = normalizeConfidence(confidence);
  const cleanRating = String(rating || "").trim().toLowerCase();
  const correct = cleanRating === "good" || cleanRating === "easy";
  const hypercorrection = Boolean(
    rated.result?.hypercorrection || (normConf !== null && normConf >= 0.75 && !correct)
  );

  // If running against a v1 srs_engine.py that did not append revlog.jsonl, record the review event safely.
  const afterSize = fs.existsSync(revlogPath) ? fs.statSync(revlogPath).size : 0;
  let revlogAppended = afterSize > beforeSize;
  if (!revlogAppended) {
    try {
      appendJsonlSafe(revlogPath, {
        propId: rated.result?.id || String(propId).trim(),
        cueId: typeof cueId === "string" && cueId.trim() ? cueId.trim() : null,
        ts: new Date().toISOString(),
        rating: cleanRating,
        correct,
        ...(normConf !== null ? { learnerConfidence: normConf } : {}),
        ...(typeof durationMs === "number" && Number.isFinite(durationMs)
          ? { durationMs: Math.round(durationMs) }
          : {}),
        hypercorrection,
      });
      revlogAppended = true;
      syncGraphQuiet(workspaceRoot, kgDir);
    } catch {
      // Non-fatal
    }
  }

  return {
    ok: true,
    id: rated.result?.id || String(propId).trim(),
    rating: cleanRating,
    status: rated.result?.status || null,
    stabilityDays: rated.result?.stabilityDays ?? null,
    difficulty: rated.result?.difficulty ?? null,
    bktMastery: rated.result?.bktMastery ?? null,
    nextReview: rated.result?.nextReview || null,
    fireCredits: rated.result?.fireCredits || [],
    fireRefreshedCount: rated.result?.fireRefreshedCount || 0,
    fireSummary: rated.result?.fireSummary || null,
    hypercorrection,
    revlogAppended,
    synced: rated.synced,
  };
}

export function saveSketchPng(workspaceRoot, { dataUrl, label }) {
  if (typeof dataUrl !== "string") {
    return { ok: false, error: "dataUrl must be a base64 PNG data URL." };
  }
  const match = /^data:image\/png;base64,([A-Za-z0-9+/=]+)$/.exec(dataUrl.trim());
  if (!match) {
    return { ok: false, error: "dataUrl must match data:image/png;base64,..." };
  }
  const buf = Buffer.from(match[1], "base64");
  if (buf.length === 0) {
    return { ok: false, error: "Empty PNG payload." };
  }
  if (buf.length > 5 * 1024 * 1024) {
    return { ok: false, error: "PNG payload exceeds 5 MiB limit." };
  }

  const attemptsDir = path.join(workspaceRoot, "sources", "attempts");
  fs.mkdirSync(attemptsDir, { recursive: true });

  const now = new Date();
  const stamp = now.toISOString().replace(/[-:]/g, "").replace(/\..+$/, "").replace("T", "-");
  const suffix = crypto.randomBytes(2).toString("hex");
  const safeLabel =
    typeof label === "string" && label.trim()
      ? "-" + label.trim().toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 24)
      : "";
  const filename = `sketch-${stamp}${safeLabel}-${suffix}.png`;
  const absPath = path.join(attemptsDir, filename);
  fs.writeFileSync(absPath, buf);

  return {
    ok: true,
    path: absPath,
    relativePath: path.posix.join("sources", "attempts", filename),
    bytes: buf.length,
  };
}
