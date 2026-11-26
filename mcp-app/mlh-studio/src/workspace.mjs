/**
 * Workspace IO helpers for mlh-studio app-only tools:
 *  - Read knowledge-graph/*.jsonl cleanly (0-byte / missing safe)
 *  - Compute due flashcards with LearnHarness multi-cue weights
 *  - Rate flashcards via local `.venv/bin/python scripts/srs_engine.py review`
 *  - Save rough-work canvas PNG sketches into `sources/attempts/`
 */

import fs from "node:fs";
import path from "node:path";
import crypto from "node:crypto";
import { execFileSync } from "node:child_process";

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
    };
  }
  const weighted = cues.map((c, idx) => {
    const key = typeof c.lastRating === "string" ? c.lastRating.toLowerCase() : "untried";
    const weight = CUE_WEIGHTS[key] ?? CUE_WEIGHTS.untried;
    return { cue: c, weight, idx };
  });
  const total = weighted.reduce((acc, w) => acc + w.weight, 0);
  // Deterministic-if-seeded or uniform draw over weights
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
  };
}

export function loadWorkspaceState(workspaceRoot, section = "all") {
  const customKg = process.env.SYNT_KG_DIR || process.env.MLH_KG_DIR || "";
  const kgDir = customKg.trim()
    ? path.resolve(customKg.trim())
    : path.join(workspaceRoot, "knowledge-graph");
  const today = new Date().toISOString().slice(0, 10);

  const propositions = readJsonl(path.join(kgDir, "propositions.jsonl"));
  const chains = readJsonl(path.join(kgDir, "chains.jsonl"));
  const mistakes = readJsonl(path.join(kgDir, "mistakes.jsonl"));
  const examAttempts = readJsonl(path.join(kgDir, "exam-attempts.jsonl"));

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
    });
  }

  flashcards.sort((a, b) => a.urgency - b.urgency || String(a.nextReview || "").localeCompare(String(b.nextReview || "")));
  const dueCount = flashcards.filter((f) => f.isDue && f.status !== "planned").length;
  const reviewableDueCount = flashcards.filter((f) => f.isDue).length;

  // Summarize mistake categories
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
  };

  const payload = {
    workspaceName: path.basename(workspaceRoot),
    stats,
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
  const venvPosix = path.join(workspaceRoot, ".venv", "bin", "python");
  if (fs.existsSync(venvPosix)) return venvPosix;
  const venvWin = path.join(workspaceRoot, ".venv", "Scripts", "python.exe");
  if (fs.existsSync(venvWin)) return venvWin;
  const preferred = process.platform === "win32" ? ["python", "python3"] : ["python3", "python"];
  return findExecutableOnPath(preferred) || preferred[0];
}

export function rateFlashcard(workspaceRoot, { propId, rating, cueId, misconception }) {
  const cleanId = typeof propId === "string" ? propId.trim() : "";
  const cleanRating = typeof rating === "string" ? rating.trim().toLowerCase() : "";
  if (!cleanId || !/^[A-Za-z0-9._:/-]+$/.test(cleanId)) {
    return { ok: false, error: `Invalid propId: ${JSON.stringify(propId)}` };
  }
  if (!VALID_RATINGS.has(cleanRating)) {
    return { ok: false, error: `rating must be again, hard, good, or easy (got ${JSON.stringify(rating)})` };
  }

  const pythonBin = resolvePythonBin(workspaceRoot);
  const srsScript = path.join(workspaceRoot, "scripts", "srs_engine.py");
  const customKg = process.env.SYNT_KG_DIR || process.env.MLH_KG_DIR || "";
  const kgDir = customKg.trim()
    ? path.resolve(customKg.trim())
    : path.join(workspaceRoot, "knowledge-graph");

  if (!fs.existsSync(srsScript)) {
    return { ok: false, error: `scripts/srs_engine.py not found in workspace ${workspaceRoot}` };
  }

  const buildArgs = (includeCue) => {
    const argv = ["-B", srsScript, "--kg-dir", kgDir, "review", cleanId, "--rating", cleanRating, "--no-sync"];
    if (includeCue && typeof cueId === "string" && cueId.trim()) {
      argv.push("--cue-id", cueId.trim());
    }
    if (misconception) {
      argv.push("--misconception");
    }
    return argv;
  };

  let stdout;
  try {
    stdout = execFileSync(pythonBin, buildArgs(true), {
      cwd: workspaceRoot,
      encoding: "utf8",
      timeout: 15000,
    });
  } catch (err) {
    const stderr = String(err?.stderr || err?.message || "");
    if (cueId && stderr.includes("unrecognized arguments") && stderr.includes("--cue-id")) {
      try {
        stdout = execFileSync(pythonBin, buildArgs(false), {
          cwd: workspaceRoot,
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

  // Quietly refresh graph-data.js if sync-graph.mjs exists
  const syncScript = path.join(workspaceRoot, "scripts", "sync-graph.mjs");
  if (fs.existsSync(syncScript)) {
    try {
      const syncArgs = [syncScript, "--quiet", `--workspace=${workspaceRoot}`];
      if (customKg.trim()) {
        syncArgs.push(`--kg-dir=${kgDir}`);
      }
      execFileSync(process.execPath, syncArgs, {
        cwd: workspaceRoot,
        stdio: "ignore",
        timeout: 10000,
      });
    } catch {
      // Non-fatal sync error
    }
  }

  let parsed = {};
  try {
    parsed = JSON.parse(stdout);
  } catch {
    parsed = { raw: stdout.trim() };
  }

  return { ok: true, result: parsed };
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
