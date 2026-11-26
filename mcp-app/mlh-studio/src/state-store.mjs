/**
 * Disk-backed atomic UI & session state store for mlh-studio MCP App.
 *
 * Why this exists:
 *   Antigravity destroys and recreates `ui://` iframes whenever the learner switches
 *   chats, scrolls, or reloads, and iframes run on ephemeral opaque origins without
 *   reliable localStorage. Persisting card submissions, in-progress drafts, sealed
 *   practice records, and Study Studio Plan DAG state on disk in `--data-dir`
 *   (`${PLUGIN_DATA}`) guarantees zero state loss across chat switches and restarts
 *   without ever polluting the learner's git workspace.
 */

import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import crypto from "node:crypto";

const MAX_QUIZ_CARDS = 500;
const MAX_PRACTICE_CARDS = 300;
const MAX_PRACTICE_RECORDS = 200;

function isTempWorkspace(wsPath) {
  const norm = path.resolve(wsPath || ".");
  const tmpDir = path.resolve(os.tmpdir());
  return (
    norm === tmpDir ||
    norm.startsWith(tmpDir + path.sep) ||
    norm.startsWith("/tmp/") ||
    norm.startsWith("/private/tmp/") ||
    norm.startsWith("/var/folders/")
  );
}

export function resolveDataDir(workspaceRoot, explicitDataDir) {
  const cleaned = typeof explicitDataDir === "string" ? explicitDataDir.trim() : "";
  if (cleaned && !cleaned.includes("${")) {
    return path.resolve(cleaned);
  }
  const envDir =
    (typeof process.env.SYNT_DATA_DIR === "string" && process.env.SYNT_DATA_DIR.trim()) ||
    (typeof process.env.MLH_DATA_DIR === "string" && process.env.MLH_DATA_DIR.trim()) ||
    "";
  if (envDir) {
    return path.resolve(envDir);
  }
  if (isTempWorkspace(workspaceRoot)) {
    const legacyTmp = path.join(path.resolve(workspaceRoot), ".mlh-studio-state");
    if (fs.existsSync(legacyTmp)) return legacyTmp;
    return path.join(path.resolve(workspaceRoot), ".syntopik-studio-state");
  }
  const wsHash = crypto
    .createHash("sha1")
    .update(path.resolve(workspaceRoot))
    .digest("hex")
    .slice(0, 12);
  const syntDir = path.join(os.homedir(), ".gemini", "syntopik-studio-data", wsHash);
  const legacyDir = path.join(os.homedir(), ".gemini", "mlh-studio-data", wsHash);
  if (!fs.existsSync(path.join(syntDir, "ui-state.json")) && fs.existsSync(path.join(legacyDir, "ui-state.json"))) {
    return legacyDir;
  }
  return syntDir;
}

function emptyStore() {
  return {
    schemaVersion: 1,
    quizCards: {},
    practiceCards: {},
    practiceRecords: {},
    activeProblemId: null,
    studioPlan: {
      planMermaid: "",
      activeNodeId: "",
      visitedNodeIds: [],
      tab: "",
    },
  };
}

function normalizeStore(raw) {
  const base = emptyStore();
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return base;
  if (raw.quizCards && typeof raw.quizCards === "object" && !Array.isArray(raw.quizCards)) {
    base.quizCards = { ...raw.quizCards };
  }
  if (raw.practiceCards && typeof raw.practiceCards === "object" && !Array.isArray(raw.practiceCards)) {
    base.practiceCards = { ...raw.practiceCards };
  }
  if (raw.practiceRecords && typeof raw.practiceRecords === "object" && !Array.isArray(raw.practiceRecords)) {
    base.practiceRecords = { ...raw.practiceRecords };
  }
  if (typeof raw.activeProblemId === "string" && raw.activeProblemId.trim()) {
    base.activeProblemId = raw.activeProblemId.trim();
  }
  if (raw.studioPlan && typeof raw.studioPlan === "object" && !Array.isArray(raw.studioPlan)) {
    base.studioPlan = {
      planMermaid: typeof raw.studioPlan.planMermaid === "string" ? raw.studioPlan.planMermaid : "",
      activeNodeId: typeof raw.studioPlan.activeNodeId === "string" ? raw.studioPlan.activeNodeId : "",
      visitedNodeIds: Array.isArray(raw.studioPlan.visitedNodeIds)
        ? [...new Set(raw.studioPlan.visitedNodeIds.map(String).filter(Boolean))]
        : [],
      tab: typeof raw.studioPlan.tab === "string" ? raw.studioPlan.tab : "",
    };
  }
  return base;
}

function pruneMapByTimestamp(obj, maxEntries, tsKey = "updatedAt") {
  const keys = Object.keys(obj);
  if (keys.length <= maxEntries) return obj;
  const sorted = keys.sort((a, b) => {
    const ta = String(obj[a]?.[tsKey] || "");
    const tb = String(obj[b]?.[tsKey] || "");
    return tb.localeCompare(ta);
  });
  const keep = new Set(sorted.slice(0, maxEntries));
  const out = {};
  for (const k of keys) {
    if (keep.has(k)) out[k] = obj[k];
  }
  return out;
}

function sanitizeCardId(rawId) {
  const s = typeof rawId === "string" ? rawId.trim() : "";
  if (!s || s.length > 160) return "";
  return s;
}

function sanitizeQuizPatch(patch) {
  if (!patch || typeof patch !== "object" || Array.isArray(patch)) return {};
  const out = {};
  if (typeof patch.revealed === "boolean") out.revealed = patch.revealed;
  if (Array.isArray(patch.selectedIds)) {
    out.selectedIds = [...new Set(patch.selectedIds.map((x) => String(x)).filter(Boolean))].slice(0, 32);
  }
  if (typeof patch.dontKnow === "boolean") out.dontKnow = patch.dontKnow;
  if (typeof patch.freeAnswer === "string") out.freeAnswer = patch.freeAnswer.slice(0, 12000);
  if (typeof patch.confidence === "number" && Number.isFinite(patch.confidence)) {
    out.confidence = Math.max(0, Math.min(100, Math.round(patch.confidence)));
  }
  if (typeof patch.thinkingWhileReading === "string") {
    out.thinkingWhileReading = patch.thinkingWhileReading.slice(0, 12000);
  }
  if (typeof patch.thinking === "string") out.thinking = patch.thinking.slice(0, 12000);
  if (typeof patch.submitted === "boolean") out.submitted = patch.submitted;
  if (
    patch.verdict === null ||
    ["correct", "incorrect", "skipped", "free_recall"].includes(patch.verdict)
  ) {
    out.verdict = patch.verdict;
  }
  if (patch.savedSketchPath === null || typeof patch.savedSketchPath === "string") {
    out.savedSketchPath = patch.savedSketchPath ? patch.savedSketchPath.slice(0, 500) : null;
  }
  if (typeof patch.statusMsg === "string") out.statusMsg = patch.statusMsg.slice(0, 500);
  if (["info", "success", "danger", "warning"].includes(patch.statusKind)) {
    out.statusKind = patch.statusKind;
  }
  return out;
}

function sanitizePracticePatch(patch) {
  if (!patch || typeof patch !== "object" || Array.isArray(patch)) return {};
  const out = {};
  if (typeof patch.attemptText === "string") out.attemptText = patch.attemptText.slice(0, 20000);
  if (Array.isArray(patch.sentHistory)) {
    out.sentHistory = patch.sentHistory.map((x) => String(x).slice(0, 200)).slice(-30);
  }
  if (typeof patch.statusMsg === "string") out.statusMsg = patch.statusMsg.slice(0, 500);
  if (["info", "success", "danger", "warning"].includes(patch.statusKind)) {
    out.statusKind = patch.statusKind;
  }
  if (typeof patch.nextRequested === "boolean") out.nextRequested = patch.nextRequested;
  if (Array.isArray(patch.parsonsOrder)) {
    out.parsonsOrder = [...new Set(patch.parsonsOrder.map((x) => String(x).slice(0, 120)).filter(Boolean))].slice(0, 40);
  }
  if (Array.isArray(patch.excludedIds)) {
    out.excludedIds = [...new Set(patch.excludedIds.map((x) => String(x).slice(0, 120)).filter(Boolean))].slice(0, 40);
  }
  if (patch.fadedBlanks && typeof patch.fadedBlanks === "object" && !Array.isArray(patch.fadedBlanks)) {
    const cleanBlanks = {};
    let count = 0;
    for (const [k, v] of Object.entries(patch.fadedBlanks)) {
      if (count >= 60) break;
      cleanBlanks[String(k).slice(0, 120)] = String(v ?? "").slice(0, 300);
      count += 1;
    }
    out.fadedBlanks = cleanBlanks;
  }
  if (patch.blackoutAnswers && typeof patch.blackoutAnswers === "object" && !Array.isArray(patch.blackoutAnswers)) {
    const cleanAnswers = {};
    let count = 0;
    for (const [k, v] of Object.entries(patch.blackoutAnswers)) {
      if (count >= 60) break;
      cleanAnswers[String(k).slice(0, 120)] = String(v ?? "").slice(0, 600);
      count += 1;
    }
    out.blackoutAnswers = cleanAnswers;
  }
  return out;
}

export function createStateStore(workspaceRoot, explicitDataDir) {
  const dataDir = resolveDataDir(workspaceRoot, explicitDataDir);
  const statePath = path.join(dataDir, "ui-state.json");

  function readDisk() {
    try {
      if (!fs.existsSync(statePath)) return emptyStore();
      const raw = JSON.parse(fs.readFileSync(statePath, "utf8"));
      return normalizeStore(raw);
    } catch {
      return emptyStore();
    }
  }

  function writeDisk(data) {
    try {
      fs.mkdirSync(dataDir, { recursive: true });
      const tmp = path.join(
        dataDir,
        `ui-state.${process.pid}.${crypto.randomBytes(4).toString("hex")}.tmp`
      );
      fs.writeFileSync(tmp, JSON.stringify(data, null, 2), "utf8");
      fs.renameSync(tmp, statePath);
      return true;
    } catch {
      return false;
    }
  }

  function updateDisk(mutator) {
    const data = readDisk();
    const res = mutator(data);
    data.quizCards = pruneMapByTimestamp(data.quizCards, MAX_QUIZ_CARDS, "updatedAt");
    data.practiceCards = pruneMapByTimestamp(data.practiceCards, MAX_PRACTICE_CARDS, "updatedAt");
    data.practiceRecords = pruneMapByTimestamp(data.practiceRecords, MAX_PRACTICE_RECORDS, "posedAt");
    writeDisk(data);
    return res;
  }

  return {
    dataDir,
    statePath,

    getCardState(kind, rawId) {
      const data = readDisk();
      if (kind === "studio") {
        return { ...data.studioPlan };
      }
      const id = sanitizeCardId(rawId);
      if (!id) return null;
      if (kind === "quiz") {
        return data.quizCards[id] ? { ...data.quizCards[id] } : null;
      }
      if (kind === "practice") {
        const uiState = data.practiceCards[id] ? { ...data.practiceCards[id] } : null;
        const rec = data.practiceRecords[id] || null;
        if (!uiState && !rec) return null;
        return {
          ...(uiState || {}),
          serverConcluded: Boolean(rec?.concluded),
        };
      }
      return null;
    },

    saveCardState(kind, rawId, patch) {
      const nowIso = new Date().toISOString();
      if (kind === "studio") {
        return updateDisk((data) => {
          if (patch && typeof patch === "object") {
            if (typeof patch.tab === "string" && ["autopilot", "plan", "graph", "flashcards", "traps"].includes(patch.tab)) {
              data.studioPlan.tab = patch.tab;
            }
          }
          return { ...data.studioPlan };
        });
      }

      const id = sanitizeCardId(rawId);
      if (!id) return null;

      if (kind === "quiz") {
        const clean = sanitizeQuizPatch(patch);
        return updateDisk((data) => {
          const prev = data.quizCards[id] || {};
          // Monotonic protection: once submitted === true, a late debounced draft
          // save (submitted !== true) can never regress the card back to unanswered.
          if (prev.submitted && clean.submitted !== true) {
            return { ...prev };
          }
          const merged = {
            ...prev,
            ...clean,
            updatedAt: nowIso,
          };
          if (merged.submitted) {
            merged.revealed = true;
          }
          data.quizCards[id] = merged;
          return { ...merged };
        });
      }

      if (kind === "practice") {
        const clean = sanitizePracticePatch(patch);
        return updateDisk((data) => {
          const prev = data.practiceCards[id] || {};
          const merged = {
            ...prev,
            ...clean,
            updatedAt: nowIso,
          };
          data.practiceCards[id] = merged;
          const rec = data.practiceRecords[id] || null;
          return {
            ...merged,
            serverConcluded: Boolean(rec?.concluded),
          };
        });
      }

      return null;
    },

    loadPracticeContext() {
      const data = readDisk();
      const history = new Map();
      for (const [k, rec] of Object.entries(data.practiceRecords)) {
        if (rec && typeof rec === "object") {
          history.set(k, { ...rec });
        }
      }
      const active =
        data.activeProblemId && history.has(data.activeProblemId) && !history.get(data.activeProblemId).concluded
          ? history.get(data.activeProblemId)
          : null;
      return { activeProblem: active, problemHistory: history };
    },

    savePracticePose(record) {
      return updateDisk((data) => {
        data.practiceRecords[record.problemId] = { ...record };
        data.activeProblemId = record.problemId;
        return { ...record };
      });
    },

    savePracticeConclude(problemId) {
      return updateDisk((data) => {
        if (data.practiceRecords[problemId]) {
          data.practiceRecords[problemId].concluded = true;
          data.practiceRecords[problemId].concludedAt = new Date().toISOString();
        }
        if (data.activeProblemId === problemId) {
          data.activeProblemId = null;
        }
        return data.practiceRecords[problemId] || null;
      });
    },

    loadStudioPlan() {
      return { ...readDisk().studioPlan };
    },

    saveStudioPlan(plan) {
      return updateDisk((data) => {
        data.studioPlan = {
          planMermaid: typeof plan.planMermaid === "string" ? plan.planMermaid : data.studioPlan.planMermaid,
          activeNodeId: typeof plan.activeNodeId === "string" ? plan.activeNodeId : data.studioPlan.activeNodeId,
          visitedNodeIds: Array.isArray(plan.visitedNodeIds)
            ? [...new Set(plan.visitedNodeIds.map(String).filter(Boolean))]
            : data.studioPlan.visitedNodeIds,
          tab: typeof plan.tab === "string" ? plan.tab : data.studioPlan.tab,
        };
        return { ...data.studioPlan };
      });
    },
  };
}
