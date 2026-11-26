/**
 * syntopik-studio — Official SEP-1865 MCP App Server v3.2 (with backwards-compatible mlh_* aliases)
 *
 * Exposes 23 tools (15 synt_* + 8 mlh_*):
 *   - Canonical UI model-visible tools (3):
 *       1. synt_quiz_card      (inline ui://syntopik/quiz-card.html)
 *       2. synt_practice_card  (inline ui://syntopik/practice-card.html — standard, parsons, faded, blackout)
 *       3. synt_lesson_studio  (aux_pane + global ui://syntopik/studio-pane.html)
 *   - Backwards-compatible UI aliases (3):
 *       mlh_quiz_card, mlh_practice_card, mlh_lesson_studio (ui://mlh/*.html)
 *   - Ambient Cross-Session Headless MCP tools (7, usable from ANY chat without spawning UI):
 *       synt_next_move, synt_whiteboard_step, synt_capture_concept, synt_get_frontier,
 *       synt_log_review, synt_readiness, synt_interleave
 *   - App-only tools (10, _meta.ui.visibility = ["app"], 0 model tokens):
 *       synt_app_load_state, synt_app_rate_flashcard, synt_app_save_sketch,
 *       synt_app_card_state, synt_app_update_profile (+ mlh_app_* aliases)
 *   - Terminal Host Safeguard:
 *       Inspects client initialize capabilities (`extensions["io.modelcontextprotocol/ui"]`).
 *       When absent (e.g., `claude` or `codex` TUI), emits clean Markdown question + bare
 *       lettered options + "I don't know" (quiz) or statement / shuffled Parsons blocks A..F (practice pose)
 *       in content[0].text while keeping correctAnswer, explanation, modelSolution, canonicalOrder,
 *       and isDistractor locked server-side!
 */

import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import {
  CallToolRequestSchema,
  ListResourcesRequestSchema,
  ListToolsRequestSchema,
  ReadResourceRequestSchema,
} from "@modelcontextprotocol/sdk/types.js";
import { RESOURCE_MIME_TYPE } from "@modelcontextprotocol/ext-apps";
import {
  hashSeed,
  validateInterleaveArgs,
  validateNextMoveArgs,
  validatePracticeAction,
  validatePracticeConclude,
  validatePracticePose,
  validateProfileUpdateArgs,
  validateQuizArgs,
  validateReadinessArgs,
  validateStudioArgs,
  validateWhiteboardStepArgs,
} from "./validation.mjs";
import {
  captureConcept,
  getFrontier,
  getReadiness,
  loadWorkspaceState,
  logReview,
  planNextMove,
  pushWhiteboardStep,
  rateFlashcard,
  runInterleave,
  saveSketchPng,
  updateLearnerProfile,
} from "./workspace.mjs";
import { createStateStore } from "./state-store.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));

function parseCliFlag(argv, flagName) {
  const eqPrefix = `${flagName}=`;
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === flagName && argv[i + 1]) {
      return argv[i + 1].trim();
    }
    if (arg.startsWith(eqPrefix)) {
      const val = arg.slice(eqPrefix.length).trim();
      if (val) return val;
    }
  }
  return "";
}

function isPluginCachePath(dirPath) {
  if (!dirPath) return false;
  const norm = path.resolve(dirPath).replace(/\\/g, "/");
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
    if (!fs.existsSync(dirPath)) return false;
    fs.accessSync(dirPath, fs.constants.W_OK);
    return true;
  } catch {
    return false;
  }
}

function resolveAttachedDir(dirPath) {
  if (!dirPath || !dirPath.trim()) return "";
  const abs = path.resolve(dirPath.trim());
  if (!fs.existsSync(abs)) return "";
  if (fs.existsSync(path.join(abs, "knowledge-graph"))) return abs;
  const dotSynt = path.join(abs, ".syntopik");
  if (fs.existsSync(dotSynt)) {
    try {
      if (fs.statSync(dotSynt).isDirectory()) return dotSynt;
    } catch {
      return "";
    }
  }
  return "";
}

function findRepoRootFallback() {
  // Bundled dist/server.mjs or src/server.mjs sits 3 levels inside <repo>/mcp-app/mlh-studio/{dist,src}
  const candidate = path.resolve(HERE, "..", "..", "..");
  if (
    !isPluginCachePath(candidate) &&
    isWritableDir(candidate) &&
    fs.existsSync(path.join(candidate, "AGENTS.md"))
  ) {
    return candidate;
  }
  return "";
}

function parseWorkspaceArg(argv) {
  const fromFlag = parseCliFlag(argv, "--workspace");
  const isGenericDotOrUnexpanded =
    !fromFlag || fromFlag === "." || fromFlag.includes("${");

  if (!isGenericDotOrUnexpanded) {
    const abs = path.resolve(fromFlag);
    if (!fs.existsSync(path.join(abs, "knowledge-graph")) && fs.existsSync(path.join(abs, ".syntopik", "knowledge-graph"))) {
      return path.join(abs, ".syntopik");
    }
    return abs;
  }

  const envWs = process.env.SYNT_WORKSPACE || process.env.MLH_WORKSPACE || "";
  if (envWs.trim()) {
    return path.resolve(envWs.trim());
  }

  const claudeDir = process.env.CLAUDE_PROJECT_DIR || "";
  if (claudeDir.trim() && !isPluginCachePath(claudeDir)) {
    const attached = resolveAttachedDir(claudeDir);
    if (attached) return attached;
    return path.resolve(claudeDir.trim());
  }

  const cwdAttached = resolveAttachedDir(process.cwd());
  if (cwdAttached && !isPluginCachePath(cwdAttached)) {
    return cwdAttached;
  }

  if (
    fromFlag === "." &&
    !isPluginCachePath(process.cwd()) &&
    fs.existsSync(path.join(process.cwd(), "AGENTS.md"))
  ) {
    return process.cwd();
  }

  const repoFallback = findRepoRootFallback();
  if (repoFallback) {
    return repoFallback;
  }

  if (fromFlag && fromFlag !== "." && !fromFlag.includes("${")) {
    return path.resolve(fromFlag);
  }
  return path.join(os.homedir(), ".syntopik");
}

const CLI_ARGS = process.argv.slice(2);
const WORKSPACE_ROOT = parseWorkspaceArg(CLI_ARGS);
const DATA_DIR_ARG = parseCliFlag(CLI_ARGS, "--data-dir");
const stateStore = createStateStore(WORKSPACE_ROOT, DATA_DIR_ARG);

const UI_RESOURCES = [
  {
    uri: "ui://syntopik/quiz-card.html",
    name: "Syntopik Quiz Card",
    description: "Calm inline interactive probe, prediction, and collapsed check card.",
    filename: "quiz-card.html",
    envOverrides: ["SYNT_QUIZ_HTML", "MLH_QUIZ_HTML"],
  },
  {
    uri: "ui://syntopik/practice-card.html",
    name: "Syntopik Practice Card",
    description: "Calm inline practice problem card (standard, Parsons, faded) with server-held hidden modelSolution.",
    filename: "practice-card.html",
    envOverrides: ["SYNT_PRACTICE_HTML", "MLH_PRACTICE_HTML"],
  },
  {
    uri: "ui://syntopik/studio-pane.html",
    name: "Syntopik Studio",
    description: "Live Syntopik Studio side-pane: Autopilot, Plan DAG, Knowledge Graph, FSRS-6 Flashcards w/ Image Occlusion, and Mistake Traps.",
    filename: "studio-pane.html",
    envOverrides: ["SYNT_STUDIO_HTML", "MLH_STUDIO_HTML"],
  },
  {
    uri: "ui://mlh/quiz-card.html",
    name: "Syntopik Quiz Card (Legacy Alias)",
    description: "Backwards-compatible quiz card resource alias.",
    filename: "quiz-card.html",
    envOverrides: ["SYNT_QUIZ_HTML", "MLH_QUIZ_HTML"],
  },
  {
    uri: "ui://mlh/practice-card.html",
    name: "Syntopik Practice Card (Legacy Alias)",
    description: "Backwards-compatible practice card resource alias.",
    filename: "practice-card.html",
    envOverrides: ["SYNT_PRACTICE_HTML", "MLH_PRACTICE_HTML"],
  },
  {
    uri: "ui://mlh/studio-pane.html",
    name: "Syntopik Studio (Legacy Alias)",
    description: "Backwards-compatible Studio side-pane resource alias.",
    filename: "studio-pane.html",
    envOverrides: ["SYNT_STUDIO_HTML", "MLH_STUDIO_HTML"],
  },
];

function readUiHtml(resource) {
  const candidates = [];
  for (const envKey of resource.envOverrides || []) {
    const envPath = process.env[envKey];
    if (envPath && envPath.trim()) {
      candidates.push(path.resolve(envPath.trim()));
    }
  }
  candidates.push(path.join(HERE, resource.filename));
  candidates.push(path.resolve(HERE, "..", "dist", resource.filename));

  for (const candidate of candidates) {
    if (fs.existsSync(candidate)) {
      return fs.readFileSync(candidate, "utf8");
    }
  }
  throw new Error(
    `UI bundle "${resource.filename}" not found (checked: ${candidates.join(", ")}). Run "npm run build" in mcp-app/mlh-studio.`
  );
}

function seededShuffleOptions(items, seed) {
  const arr = items.map((item) => ({ ...item }));
  let a = (Number(seed) || 1) >>> 0;
  const nextRand = () => {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
  for (let i = arr.length - 1; i > 0; i -= 1) {
    const j = Math.floor(nextRand() * (i + 1));
    [arr[i], arr[j]] = [arr[j], arr[i]];
  }
  return arr.map((item, idx) => ({
    ...item,
    letter: String.fromCharCode(65 + idx),
  }));
}

// Disk-backed state (survives iframe remounts, chat switches, and server restarts).
let cardSeq = 0;
const quizHistory = new Map();
const initialPractice = stateStore.loadPracticeContext();
let activeProblem = initialPractice.activeProblem;
const problemHistory = initialPractice.problemHistory;
const studioPlanState = stateStore.loadStudioPlan();

// CRITICAL: In quiz_card, `explanation` precedes `correctAnswer`!
const QUIZ_INPUT_SCHEMA = {
  type: "object",
  properties: {
    role: { type: "string", enum: ["probe", "predict", "check"] },
    nodeId: { type: "string" },
    question: { type: "string" },
    options: {
      type: "array",
      items: {
        type: "object",
        properties: {
          id: { type: "string" },
          label: { type: "string" },
        },
        required: ["id", "label"],
      },
    },
    multiSelect: { type: "boolean" },
    freeRecall: { type: "boolean" },
    explanation: { type: "string" },
    correctAnswer: {
      oneOf: [
        { type: "string" },
        { type: "array", items: { type: "string" } },
      ],
    },
    committedAnswer: { type: "string" },
  },
  required: ["role", "question", "explanation"],
};

const PRACTICE_INPUT_SCHEMA = {
  type: "object",
  properties: {
    action: { type: "string", enum: ["pose", "conclude"] },
    problemId: { type: "string" },
    statement: { type: "string" },
    mode: {
      type: "string",
      enum: ["standard", "parsons", "faded", "blackout", "recall", "discrimination", "composition"],
    },
    blocks: {
      type: "array",
      items: {
        type: "object",
        properties: {
          id: { type: "string" },
          code: { type: "string" },
          indent: { type: "integer" },
          isDistractor: { type: "boolean" },
        },
        required: ["id", "code"],
      },
    },
    canonicalOrder: {
      type: "array",
      items: { type: "string" },
    },
    steps: {
      type: "array",
      description: "Clean-Room Derivation Reconstruction steps for mode='blackout' (expectedExpr & hidden expr are sealed server-side until conclude).",
      items: {
        type: "object",
        properties: {
          id: { type: "string" },
          label: { type: "string" },
          role: { type: "string", enum: ["axiom", "step", "theorem"] },
          expr: { type: "string" },
          expectedExpr: { type: "string" },
          hidden: { type: "boolean" },
          dependsOn: { type: "array", items: { type: "string" } },
          note: { type: "string" },
        },
        required: ["id"],
      },
    },
    derivationId: { type: "string" },
    blackoutLevel: { type: "integer", enum: [1, 2, 3] },
    targets: { type: "array", items: { type: "string" } },
    modelSolution: { type: "string" },
    outcome: {
      type: "string",
      enum: ["solved", "solvedWithHints", "movedOn", "surrendered", "dismissed"],
    },
    verdict: { type: "string" },
    rung: {
      anyOf: [
        { type: "string", enum: ["R0", "R1", "R2", "R3", "R4", "R5"] },
        { type: "null" },
      ],
    },
    missClass: {
      anyOf: [{ type: "string" }, { type: "null" }],
    },
    retest: { type: "boolean" },
  },
  required: ["action"],
};

const STUDIO_INPUT_SCHEMA = {
  type: "object",
  properties: {
    planMermaid: { type: "string" },
    activeNodeId: { type: "string" },
    visitedNodeIds: { type: "array", items: { type: "string" } },
    tab: { type: "string", enum: ["autopilot", "plan", "graph", "flashcards", "traps"] },
  },
};

const CAPTURE_CONCEPT_SCHEMA = {
  type: "object",
  properties: {
    title: { type: "string", description: "Concise concept or invariant title." },
    statement: { type: "string", description: "Caveat-free unconditional truth or mechanism statement." },
    subject: { type: "string", description: "Subject area (e.g., CS, ML, Physics, Math, Chemistry)." },
    chapter: { type: "string", description: "Chapter, course, paper, or project context." },
    cuePrompt: { type: "string", description: "Optional active recall question cue." },
    cueAnswer: { type: "string", description: "Optional concise answer for cuePrompt." },
    premises: {
      type: "array",
      items: { type: "string" },
      description: "Optional prerequisite proposition IDs forming a hyperedge into this concept.",
    },
    relation: { type: "string", description: "Optional chain relation (default: requires)." },
    kind: { type: "string", description: "Optional proposition kind (default: concept)." },
    id: { type: "string", description: "Optional explicit kebab-case proposition ID." },
  },
  required: ["title", "statement", "subject", "chapter"],
};

const GET_FRONTIER_SCHEMA = {
  type: "object",
  properties: {
    limit: { type: "integer", description: "Max due items to return (default 10, max 50)." },
    compress: { type: "boolean", description: "Enable FIRe Spaced Repetition Compression knockouts (default true)." },
  },
};

const LOG_REVIEW_SCHEMA = {
  type: "object",
  properties: {
    propId: { type: "string", description: "Proposition ID to review." },
    rating: { type: "string", enum: ["again", "hard", "good", "easy"] },
    confidence: { type: "number", description: "Optional learner confidence (0..1 or 0..100)." },
    cueId: { type: "string", description: "Optional specific cue ID reviewed." },
    misconception: { type: "boolean", description: "Flag as active misconception when rating is 'again'." },
    durationMs: { type: "number", description: "Optional response duration in milliseconds." },
    hintsUsed: { type: "integer", description: "Optional hints used (0..5) for silent Tier-3 learner adaptation." },
  },
  required: ["propId", "rating"],
};

const NEXT_MOVE_SCHEMA = {
  type: "object",
  properties: {
    energy: { type: "string", enum: ["low", "normal", "deep"], description: "Optional cognitive energy mode." },
    domain: { type: "string", description: "Optional subject/domain focus." },
    event: {
      oneOf: [{ type: "string" }, { type: "object" }],
      description: "Optional recent event context (e.g. 'skip_calibration' or {kind:'miss', propId:'...'}).",
    },
  },
};

const WHITEBOARD_STEP_SCHEMA = {
  type: "object",
  properties: {
    kind: {
      type: "string",
      enum: ["derivation_step", "prediction_gate", "explore_widget", "diagram_callout", "summary"],
      description: "Whiteboard frame kind (default: derivation_step).",
    },
    title: { type: "string", description: "Concise frame title." },
    body: { type: "string", description: "Concise Markdown body (locked server-side when kind='prediction_gate')." },
    latex: { type: "string", description: "Optional display LaTeX equation." },
    propId: { type: "string", description: "Optional linked proposition ID." },
    stepIndex: { type: "integer", description: "Optional 0-based step index." },
    locked: { type: "boolean", description: "Lock frame behind prediction gate (default true for prediction_gate)." },
    prompt: { type: "string", description: "Prompt shown on locked prediction_gate frames." },
    revealedBody: { type: "string", description: "Body revealed once prediction_gate is unlocked." },
    expectedAnswer: { type: "string", description: "Optional expected prediction for gate comparison." },
    payload: { type: "object", description: "Optional structured payload (e.g., :::explore lab spec)." },
    sessionTitle: { type: "string", description: "Optional whiteboard session heading." },
    id: { type: "string", description: "Optional explicit frame ID (updates frame in-place if repeated)." },
    resolvePinId: { type: "string", description: "Optional learner pin ID resolved by this step." },
    resolution: { type: "string", description: "Optional short resolution note for resolvePinId." },
  },
};

const READINESS_SCHEMA = {
  type: "object",
  properties: {
    examDate: { type: "string", description: "Optional target exam date (YYYY-MM-DD)." },
    subject: { type: "string", description: "Optional subject filter." },
    chapter: { type: "string", description: "Optional chapter filter." },
    maxMarks: { type: "number", description: "Optional maximum exam marks for score projection." },
    premortemOnly: { type: "boolean", description: "Return only the 3-bullet Pre-Mortem Shield." },
  },
};

const INTERLEAVE_SCHEMA = {
  type: "object",
  properties: {
    action: {
      type: "string",
      enum: ["build", "check", "stats"],
      description: "Operation to run (default: build).",
    },
    n: { type: "integer", description: "Number of interleaved items to generate (default: 6)." },
    count: { type: "integer", description: "Alias for n." },
    subjects: {
      type: "array",
      items: { type: "string" },
      description: "Optional list of subjects to interleave.",
    },
    subject: { type: "string", description: "Optional single subject filter." },
    mode: { type: "string", description: "Drill mode (default: principle_first)." },
    seed: { type: "integer", description: "Optional deterministic RNG seed." },
    sessionId: { type: "string", description: "Session ID (required for action='check')." },
    itemId: { type: "string", description: "Item ID (required for action='check')." },
    selectedPrinciple: {
      type: "string",
      description: "Learner's selected principle (required for action='check').",
    },
    confidence: { type: "number", description: "Learner confidence (0..1 or 0..100)." },
    durationMs: { type: "number", description: "Response duration in milliseconds." },
  },
};

const UPDATE_PROFILE_SCHEMA = {
  type: "object",
  properties: {
    op: { type: "string", enum: ["override", "calibrate", "reset"] },
    scaffoldingMode: {
      anyOf: [
        { type: "string", enum: ["worked_example_parsons", "faded", "standard", "challenge_transfer", "auto", "clear"] },
        { type: "null" },
      ],
    },
    allowProductiveFailure: { anyOf: [{ type: "boolean" }, { type: "null" }] },
    maxProseWords: { anyOf: [{ type: "integer" }, { type: "null" }] },
    clearOverrides: { type: "boolean" },
    userOverrides: { type: "object" },
    entryRamp: { type: "string", enum: ["intuition_first", "balanced", "formalism_first"] },
    challengeAppetite: { type: "string", enum: ["guided", "balanced", "adversarial"] },
    proseDensity: { type: "string", enum: ["terse", "standard", "rich"] },
    sessionMinutes: { type: "integer" },
    domainFluency: { type: "object" },
  },
};

function makeAppToolPair(suffix, title, description, inputSchema) {
  return [
    {
      name: `synt_app_${suffix}`,
      title,
      description,
      inputSchema,
      _meta: { ui: { visibility: ["app"] } },
    },
    {
      name: `mlh_app_${suffix}`,
      title: `${title} (Alias)`,
      description,
      inputSchema,
      _meta: { ui: { visibility: ["app"] } },
    },
  ];
}

const TOOLS = [
  {
    name: "synt_quiz_card",
    title: "Syntopik Quiz / Predict / Check Card",
    description:
      "Render a calm inline probe, predict, or collapsed check card with confidence slider, 'I don't know', rough-work sketch pad, and client-side explanation reveal (with safe terminal Markdown fallback). Always emit explanation before correctAnswer.",
    inputSchema: QUIZ_INPUT_SCHEMA,
    _meta: {
      ui: { resourceUri: "ui://syntopik/quiz-card.html" },
    },
  },
  {
    name: "synt_practice_card",
    title: "Syntopik Practice Problem Card",
    description:
      "Pose a practice problem (standard, parsons, faded, or blackout derivation reconstruction; sealing modelSolution, canonicalOrder, and blackout answers server-side with a 1-active-problem gate) or conclude to reveal verbatim with verdict and Pause Law.",
    inputSchema: PRACTICE_INPUT_SCHEMA,
    _meta: {
      ui: { resourceUri: "ui://syntopik/practice-card.html" },
    },
  },
  {
    name: "synt_lesson_studio",
    title: "Syntopik Studio",
    description:
      "Open or update the live Syntopik Studio pane (Autopilot + Active TeachingPolicy strip, Plan DAG, Knowledge Graph, zero-token FSRS-6 Flashcards w/ Image Occlusion, Mistake Traps). All arguments optional.",
    inputSchema: STUDIO_INPUT_SCHEMA,
    _meta: {
      ui: { resourceUri: "ui://syntopik/studio-pane.html" },
      "com.google.antigravity/ui": {
        entrypoints: [
          { type: "aux_pane", title: "Syntopik Studio" },
          { type: "global", title: "Syntopik Studio" },
        ],
      },
    },
  },
  // --- Ambient Cross-Session Headless Knowledge & Teacher Tools (7, zero UI spawned) ---
  {
    name: "synt_next_move",
    title: "Syntopik Pedagogical Brainstem (Next Optimal Move)",
    description:
      "Return the compact <=180-token Action Envelope fusing FSRS-6/FIRe, Learner Psychology TeachingPolicy, causal root-cause probes, Blunder Forks, Rosetta isomorphicBridge openers, taper mode, and live whiteboard learner pins.",
    inputSchema: NEXT_MOVE_SCHEMA,
  },
  {
    name: "synt_whiteboard_step",
    title: "Push Live AI Whiteboard Frame",
    description:
      "Push a progressive frame (derivation_step, prediction_gate, explore_widget, diagram_callout, or summary) to knowledge-graph/whiteboard-session.json and return any unresolved learner pendingPins.",
    inputSchema: WHITEBOARD_STEP_SCHEMA,
  },
  {
    name: "synt_capture_concept",
    title: "Capture Concept to Syntopik Knowledge Graph",
    description:
      "Ambient headless capture: deduplicate via find-similar, save a verified proposition (plus optional recall cue and prerequisite hyperedge chain) to the Knowledge Graph, and sync graph-data.js.",
    inputSchema: CAPTURE_CONCEPT_SCHEMA,
  },
  {
    name: "synt_get_frontier",
    title: "Get Syntopik Study Frontier & Learner Policy",
    description:
      "Ambient headless query: return FIRe-compressed due items, active Autopilot, learnerProfile, Active TeachingPolicy, dueForks, arenasCount, coldStartDays, pendingPins, rosettaBridgesCount, syncDeviceId, readinessSummary, taperActive, traps, and Brier calibration.",
    inputSchema: GET_FRONTIER_SCHEMA,
  },
  {
    name: "synt_log_review",
    title: "Log FSRS-6 + FIRe Concept Review",
    description:
      "Ambient headless review: run srs_engine.py review with FIRe propagation, optional hintsUsed (0..5) & durationMs for Tier-3 learner psychology updates, and revlog.jsonl persistence.",
    inputSchema: LOG_REVIEW_SCHEMA,
  },
  {
    name: "synt_readiness",
    title: "Exam-Day Readiness Forecaster & Pre-Mortem Shield",
    description:
      "Project deterministic FSRS-6 retention at exam date, fuse 4-tier Evidence coverage, unresolved Blunder Forks, EV-skip discipline, top-3 ROI levers, <=72h taper mode, and non-fabricated 3-bullet Pre-Mortem Shield.",
    inputSchema: READINESS_SCHEMA,
  },
  {
    name: "synt_interleave",
    title: "Blind-Chapter Interleaved Drill Generator & Step-1 Grader",
    description:
      "Generate Rohrer blind-chapter interleaved drills (chapter labels & canonical answers sealed!) or grade Step-1 governing-principle identification cleanly without leaking sealed answers.",
    inputSchema: INTERLEAVE_SCHEMA,
  },
  // Backwards-compatible mlh_* aliases
  {
    name: "mlh_quiz_card",
    title: "Quiz / Predict / Check Card (Alias)",
    description: "Backwards-compatible alias for synt_quiz_card. Emit explanation before correctAnswer.",
    inputSchema: QUIZ_INPUT_SCHEMA,
    _meta: {
      ui: { resourceUri: "ui://mlh/quiz-card.html" },
    },
  },
  {
    name: "mlh_practice_card",
    title: "Practice Problem Card (Alias)",
    description: "Backwards-compatible alias for synt_practice_card (pose/conclude with hidden modelSolution).",
    inputSchema: PRACTICE_INPUT_SCHEMA,
    _meta: {
      ui: { resourceUri: "ui://mlh/practice-card.html" },
    },
  },
  {
    name: "mlh_lesson_studio",
    title: "Syntopik Studio (Alias)",
    description: "Backwards-compatible alias for synt_lesson_studio.",
    inputSchema: STUDIO_INPUT_SCHEMA,
    _meta: {
      ui: { resourceUri: "ui://mlh/studio-pane.html" },
    },
  },
  // --- App-Only Tools (visibility: ["app"] -> 0 tokens in model prompt!) ---
  ...makeAppToolPair(
    "load_state",
    "Load Workspace Study State",
    "App-only: read workspace chapter autopilot trackers, knowledge-graph ledgers, flashcards, and mistake traps.",
    {
      type: "object",
      properties: {
        section: { type: "string", enum: ["all", "autopilot", "graph", "flashcards", "traps"] },
      },
    }
  ),
  ...makeAppToolPair(
    "rate_flashcard",
    "Rate Flashcard via FSRS-6",
    "App-only: submit an FSRS-6 flashcard review directly to scripts/srs_engine.py.",
    {
      type: "object",
      properties: {
        propId: { type: "string" },
        rating: { type: "string", enum: ["again", "hard", "good", "easy"] },
        cueId: { type: "string" },
        misconception: { type: "boolean" },
        confidence: { type: "number" },
      },
      required: ["propId", "rating"],
    }
  ),
  ...makeAppToolPair(
    "save_sketch",
    "Save Rough-Work Sketch PNG",
    "App-only: save a canvas rough-work PNG into workspace sources/attempts/.",
    {
      type: "object",
      properties: {
        dataUrl: { type: "string" },
        label: { type: "string" },
      },
      required: ["dataUrl"],
    }
  ),
  ...makeAppToolPair(
    "card_state",
    "Persist Interactive Card State",
    "App-only: load or save quiz/practice/studio interactive card state on disk.",
    {
      type: "object",
      properties: {
        op: { type: "string", enum: ["get", "save"] },
        kind: { type: "string", enum: ["quiz", "practice", "studio"] },
        id: { type: "string" },
        state: { type: "object" },
      },
      required: ["op", "kind"],
    }
  ),
  ...makeAppToolPair(
    "update_profile",
    "Update Learner Profile & TeachingPolicy Overrides",
    "App-only: update Tier-1 calibration or transparent userOverrides in knowledge-graph/learner-profile.json.",
    UPDATE_PROFILE_SCHEMA
  ),
];

function toolError(message) {
  return {
    isError: true,
    content: [{ type: "text", text: message }],
  };
}

function formatTerminalQuizText(v, cardId, shuffleSeed) {
  const roleTag = v.nodeId ? `${v.role} (${v.nodeId})` : v.role;
  const lines = [
    `Quiz card [${roleTag}, id=${cardId}] (terminal fallback — present verbatim; correctAnswer & explanation locked server-side):`,
    "",
    v.question,
    "",
  ];
  if (!v.freeRecall && Array.isArray(v.options) && v.options.length > 0) {
    const shuffled = seededShuffleOptions(v.options, shuffleSeed);
    for (const opt of shuffled) {
      lines.push(`${opt.letter}) ${opt.label}`);
    }
    const dkLetter = String.fromCharCode(65 + shuffled.length);
    lines.push(`${dkLetter}) I don't know`);
    lines.push("");
    lines.push(
      v.multiSelect
        ? "Ask the learner for all applicable letters (or 'I don't know'), 0–100% confidence, and 1-line reasoning."
        : "Ask the learner for their letter choice (or 'I don't know'), 0–100% confidence, and 1-line reasoning."
    );
  } else {
    lines.push("- Free recall response (or 'I don't know')");
    lines.push("");
    lines.push("Ask the learner for their concise derivation/explanation (or 'I don't know') and 0–100% confidence.");
  }
  return lines.join("\n");
}

function formatTerminalParsonsText(record, safeShuffledBlocks) {
  const isFaded = record.mode === "faded";
  const lines = [
    `Practice problem posed (id=${record.problemId}, ${isFaded ? "FADED PARSONS" : "PARSONS"}) — Terminal fallback (committed solution & target block order locked server-side):`,
    "",
    record.statement,
    "",
    "Shuffled blocks (order the valid steps and omit any distractors):",
  ];
  for (const blk of safeShuffledBlocks) {
    const indentPad = blk.indent > 0 ? "  ".repeat(blk.indent) : "";
    lines.push(`${blk.letter}) ${indentPad}${blk.code}`);
  }
  lines.push("");
  lines.push(
    isFaded
      ? "Ask the learner for the ordered letters (e.g., B, A, D — omitting distractors) plus values for each ___ blank before calling action=\"conclude\"."
      : "Ask the learner for the ordered letters (e.g., B, A, D — omitting distractors) before calling action=\"conclude\"."
  );
  return lines.join("\n");
}

function formatTerminalBlackoutText(record, safeSteps) {
  const hiddenIds = safeSteps.filter((s) => s.hidden).map((s) => s.id);
  const lines = [
    `Practice problem posed (id=${record.problemId}, DERIVATION BLACKOUT L${record.blackoutLevel || 1}) — Terminal fallback (expected expressions & committed modelSolution sealed server-side):`,
    "",
    record.statement,
    "",
    "Derivation steps (reconstruct every [HIDDEN] step cleanly):",
  ];
  for (const st of safeSteps) {
    const depTag = Array.isArray(st.dependsOn) && st.dependsOn.length > 0 ? ` (from ${st.dependsOn.join(", ")})` : "";
    const body = st.hidden ? "[HIDDEN — reconstruct]" : st.expr;
    lines.push(`- [${st.id} · ${st.role}] ${st.label}${depTag}: ${body}`);
  }
  lines.push("");
  lines.push(
    `Ask the learner to reconstruct hidden step(s) ${hiddenIds.join(", ")} (or request R0–R5 hints) before calling action="conclude".`
  );
  return lines.join("\n");
}

function handleQuizCard(rawArgs, toolName, hasUiExtension) {
  const checked = validateQuizArgs(rawArgs);
  if (!checked.ok) {
    return toolError(`Invalid ${toolName} arguments: ${checked.error}`);
  }
  const v = checked.value;
  cardSeq += 1;
  const cardId = `quiz-${Date.now().toString(36)}-${cardSeq}`;
  const shuffleSeed = hashSeed(`${cardId}:${v.question}`);
  const pfx = toolName.startsWith("synt_") ? "synt" : "mlh";

  const fullPayload = {
    kind: `${pfx}_quiz_card`,
    cardId,
    role: v.role,
    nodeId: v.nodeId || null,
    question: v.question,
    options: v.options,
    multiSelect: v.multiSelect,
    freeRecall: v.freeRecall,
    explanation: v.explanation,
    correctAnswer: v.correctAnswer ?? null,
    committedAnswer: v.committedAnswer ?? null,
    shuffleSeed,
  };

  quizHistory.set(cardId, fullPayload);
  if (quizHistory.size > 100) {
    const oldest = quizHistory.keys().next().value;
    quizHistory.delete(oldest);
  }

  if (!hasUiExtension) {
    const safeStructured = {
      kind: `${pfx}_quiz_card`,
      cardId,
      role: v.role,
      nodeId: v.nodeId || null,
      question: v.question,
      options: v.options,
      multiSelect: v.multiSelect,
      freeRecall: v.freeRecall,
      shuffleSeed,
      terminalFallback: true,
    };
    return {
      content: [
        {
          type: "text",
          text: formatTerminalQuizText(v, cardId, shuffleSeed),
        },
      ],
      structuredContent: safeStructured,
    };
  }

  const roleTag = v.nodeId ? `${v.role} (${v.nodeId})` : v.role;
  return {
    content: [
      {
        type: "text",
        text: `Quiz card rendered [${roleTag}, id=${cardId}]. Awaiting learner response.`,
      },
    ],
    structuredContent: fullPayload,
  };
}

function handlePracticeCard(rawArgs, toolName, hasUiExtension) {
  const act = validatePracticeAction(rawArgs);
  if (!act.ok) {
    return toolError(`Invalid ${toolName} arguments: ${act.error}`);
  }
  const pfx = toolName.startsWith("synt_") ? "synt" : "mlh";

  // Re-sync from disk in case another process or reload touched practice state.
  const synced = stateStore.loadPracticeContext();
  activeProblem = synced.activeProblem;
  problemHistory.clear();
  for (const [k, rec] of synced.problemHistory.entries()) {
    problemHistory.set(k, rec);
  }

  if (act.action === "pose") {
    const checked = validatePracticePose(act.args, activeProblem);
    if (!checked.ok) {
      return toolError(`Invalid ${toolName} pose: ${checked.error}`);
    }
    const v = checked.value;
    const shuffleSeed = v.isParsonsMode ? hashSeed(`${v.problemId}:${v.statement}`) : null;
    const record = {
      problemId: v.problemId,
      statement: v.statement,
      mode: v.mode || null,
      isParsonsMode: v.isParsonsMode,
      isBlackoutMode: v.isBlackoutMode,
      blocks: v.blocks,
      canonicalOrder: v.canonicalOrder,
      steps: v.steps,
      derivationId: v.derivationId,
      blackoutLevel: v.blackoutLevel,
      shuffleSeed,
      targets: v.targets,
      modelSolution: v.modelSolution,
      posedAt: new Date().toISOString(),
      concluded: false,
    };
    activeProblem = record;
    problemHistory.set(record.problemId, record);
    stateStore.savePracticePose(record);

    // SECURITY INVARIANT: modelSolution, canonicalOrder, isDistractor, blackout expectedExpr/hidden expr,
    // and legacy mode/targets are NEVER leaked during pose!
    let safeShuffledBlocks = null;
    if (v.isParsonsMode && Array.isArray(v.blocks)) {
      const stripped = v.blocks.map((b) => ({
        id: b.id,
        code: b.code,
        indent: b.indent || 0,
        hasBlanks: /_{3,}/.test(b.code),
      }));
      safeShuffledBlocks = seededShuffleOptions(stripped, shuffleSeed);
    }

    let safeBlackoutSteps = null;
    if (v.isBlackoutMode && Array.isArray(v.steps)) {
      safeBlackoutSteps = v.steps.map((s) => ({
        id: s.id,
        label: s.label,
        role: s.role,
        dependsOn: Array.isArray(s.dependsOn) ? [...s.dependsOn] : [],
        hidden: Boolean(s.hidden),
        expr: s.hidden ? null : s.expr,
      }));
    }

    let textMsg;
    if (v.isBlackoutMode && safeBlackoutSteps) {
      const hiddenCount = safeBlackoutSteps.filter((s) => s.hidden).length;
      textMsg = hasUiExtension
        ? `Practice blackout derivation posed (id=${record.problemId}, L${record.blackoutLevel || 1}, ${hiddenCount}/${safeBlackoutSteps.length} steps hidden). Expected step expressions & modelSolution sealed server-side; awaiting reconstruction.`
        : formatTerminalBlackoutText(record, safeBlackoutSteps);
    } else if (v.isParsonsMode && safeShuffledBlocks) {
      textMsg = hasUiExtension
        ? `Practice ${v.mode} problem posed (id=${record.problemId}, ${safeShuffledBlocks.length} shuffled blocks). Committed solution & target block order locked server-side; awaiting learner ordering.`
        : formatTerminalParsonsText(record, safeShuffledBlocks);
    } else {
      textMsg = hasUiExtension
        ? `Practice problem posed (id=${record.problemId}). Committed modelSolution locked server-side; awaiting learner attempt or hint request.`
        : [
            `Practice problem posed (id=${record.problemId}) — Terminal fallback (committed modelSolution locked server-side):`,
            "",
            record.statement,
            "",
            "Present the problem cleanly and await the learner's attempt, R0–R5 hint request, or surrender before calling action=\"conclude\".",
          ].join("\n");
    }

    const structuredContent = {
      kind: `${pfx}_practice_card`,
      action: "pose",
      problemId: record.problemId,
      statement: record.statement,
      hasHiddenSolution: true,
      ...(v.isBlackoutMode && safeBlackoutSteps
        ? {
            cardMode: "blackout",
            derivationId: record.derivationId,
            blackoutLevel: record.blackoutLevel,
            steps: safeBlackoutSteps,
            hiddenStepIds: safeBlackoutSteps.filter((s) => s.hidden).map((s) => s.id),
          }
        : {}),
      ...(v.isParsonsMode && safeShuffledBlocks
        ? {
            cardMode: v.mode,
            blocks: safeShuffledBlocks,
            shuffleSeed,
          }
        : {}),
      ...(hasUiExtension ? {} : { terminalFallback: true }),
    };

    return {
      content: [{ type: "text", text: textMsg }],
      structuredContent,
    };
  }

  // action === "conclude"
  const checked = validatePracticeConclude(act.args, activeProblem, problemHistory);
  if (!checked.ok) {
    return toolError(`Invalid ${toolName} conclude: ${checked.error}`);
  }
  const { record, outcome, verdict, rung, missClass, retest } = checked.value;
  record.concluded = true;
  if (activeProblem && activeProblem.problemId === record.problemId) {
    activeProblem = null;
  }
  stateStore.savePracticeConclude(record.problemId);

  const canonicalLine =
    Array.isArray(record.canonicalOrder) && record.canonicalOrder.length > 0
      ? `Canonical block order: ${record.canonicalOrder.join(" → ")}`
      : "";

  const concludeText = hasUiExtension
    ? `Practice problem ${record.problemId} concluded (${outcome}${rung ? `, ${rung}` : ""}). Verbatim committed modelSolution revealed. Pause for learner go-ahead.`
    : [
        `Practice problem ${record.problemId} concluded (${outcome}${rung ? `, ${rung}` : ""}).`,
        `Verdict: ${verdict}`,
        ...(canonicalLine ? [canonicalLine] : []),
        "",
        "Committed Model Solution (reveal verbatim):",
        record.modelSolution,
        "",
        "Pause Law: stop here and ask 'Ready for the next problem?' before posing another item.",
      ].join("\n");

  return {
    content: [{ type: "text", text: concludeText }],
    structuredContent: {
      kind: `${pfx}_practice_card`,
      action: "conclude",
      problemId: record.problemId,
      statement: record.statement,
      outcome,
      verdict,
      rung,
      missClass,
      retest,
      modelSolution: record.modelSolution,
      ...(record.isBlackoutMode
        ? {
            cardMode: "blackout",
            derivationId: record.derivationId || null,
            blackoutLevel: record.blackoutLevel || 1,
            steps: Array.isArray(record.steps) ? record.steps : [],
          }
        : {}),
      ...(record.isParsonsMode ? { cardMode: record.mode } : {}),
      ...(Array.isArray(record.canonicalOrder) ? { canonicalOrder: record.canonicalOrder } : {}),
      ...(Array.isArray(record.blocks) ? { blocks: record.blocks } : {}),
    },
  };
}

function handleLessonStudio(rawArgs, toolName) {
  const v = validateStudioArgs(rawArgs);
  const pfx = toolName.startsWith("synt_") ? "synt" : "mlh";
  const persistedPlan = stateStore.loadStudioPlan();
  studioPlanState.planMermaid = persistedPlan.planMermaid;
  studioPlanState.activeNodeId = persistedPlan.activeNodeId;
  studioPlanState.visitedNodeIds = [...persistedPlan.visitedNodeIds];
  studioPlanState.tab = persistedPlan.tab || "";

  if (v.planMermaid !== undefined) {
    studioPlanState.planMermaid = v.planMermaid;
  }
  if (v.visitedNodeIds !== undefined) {
    studioPlanState.visitedNodeIds = [...new Set(v.visitedNodeIds)];
  }
  if (v.activeNodeId !== undefined) {
    if (
      studioPlanState.activeNodeId &&
      studioPlanState.activeNodeId !== v.activeNodeId &&
      !studioPlanState.visitedNodeIds.includes(studioPlanState.activeNodeId)
    ) {
      studioPlanState.visitedNodeIds.push(studioPlanState.activeNodeId);
    }
    studioPlanState.activeNodeId = v.activeNodeId;
  }
  if (v.tab !== undefined) {
    studioPlanState.tab = v.tab;
  }
  stateStore.saveStudioPlan(studioPlanState);

  const wsState = loadWorkspaceState(WORKSPACE_ROOT, "all");
  const defaultTab =
    v.tab ||
    studioPlanState.tab ||
    (wsState.activeChapter
      ? "autopilot"
      : studioPlanState.planMermaid
        ? "plan"
        : wsState.stats.reviewableDueCount > 0
          ? "flashcards"
          : "plan");

  const payload = {
    kind: `${pfx}_lesson_studio`,
    tab: defaultTab,
    plan: {
      planMermaid: studioPlanState.planMermaid,
      activeNodeId: studioPlanState.activeNodeId,
      visitedNodeIds: [...studioPlanState.visitedNodeIds],
    },
    ...wsState,
  };

  return {
    content: [
      {
        type: "text",
        text: `Syntopik Studio updated (tab=${defaultTab}, propositions=${wsState.stats.totalPropositions}, due=${wsState.stats.reviewableDueCount}, traps=${wsState.stats.totalMistakes}${wsState.activeChapter ? `, autopilot=${wsState.activeChapter.slug}:${wsState.activeChapter.pct}%` : ""}${studioPlanState.activeNodeId ? `, activeNode=${studioPlanState.activeNodeId}` : ""}).`,
      },
    ],
    structuredContent: payload,
  };
}

function createServer() {
  const server = new Server(
    { name: "syntopik-studio", version: "3.2.0" },
    { capabilities: { tools: {}, resources: {} } }
  );

  server.setRequestHandler(ListToolsRequestSchema, async () => ({
    tools: TOOLS,
  }));

  server.setRequestHandler(ListResourcesRequestSchema, async () => ({
    resources: UI_RESOURCES.map((r) => ({
      uri: r.uri,
      name: r.name,
      description: r.description,
      mimeType: RESOURCE_MIME_TYPE,
      _meta: {
        ui: {
          prefersBorder: false,
        },
      },
    })),
  }));

  server.setRequestHandler(ReadResourceRequestSchema, async (request) => {
    const uri = request.params?.uri;
    const found = UI_RESOURCES.find((r) => r.uri === uri);
    if (!found) {
      throw new Error(`Unknown resource URI: ${uri}`);
    }
    const html = readUiHtml(found);
    return {
      contents: [
        {
          uri: found.uri,
          mimeType: RESOURCE_MIME_TYPE,
          text: html,
          _meta: {
            ui: {
              prefersBorder: false,
            },
          },
        },
      ],
    };
  });

  server.setRequestHandler(CallToolRequestSchema, async (request) => {
    const name = String(request.params?.name || "");
    const args = request.params?.arguments ?? {};
    const hasUiExtension = Boolean(
      server.getClientCapabilities()?.extensions?.["io.modelcontextprotocol/ui"]
    );
    const pfx = name.startsWith("synt_") ? "synt" : "mlh";

    try {
      switch (name) {
        case "synt_quiz_card":
        case "mlh_quiz_card":
          return handleQuizCard(args, name, hasUiExtension);

        case "synt_practice_card":
        case "mlh_practice_card":
          return handlePracticeCard(args, name, hasUiExtension);

        case "synt_lesson_studio":
        case "mlh_lesson_studio":
          return handleLessonStudio(args, name);

        case "synt_next_move": {
          const checked = validateNextMoveArgs(args);
          if (!checked.ok) return toolError(`Invalid synt_next_move arguments: ${checked.error}`);
          const res = planNextMove(WORKSPACE_ROOT, checked.value);
          if (!res.ok) return toolError(res.error || "planNextMove failed.");
          const structuredContent = {
            kind: "synt_next_move",
            ...res,
          };
          return {
            content: [{ type: "text", text: JSON.stringify(structuredContent) }],
            structuredContent,
          };
        }

        case "synt_whiteboard_step": {
          const checked = validateWhiteboardStepArgs(args);
          if (!checked.ok) return toolError(`Invalid synt_whiteboard_step arguments: ${checked.error}`);
          const res = pushWhiteboardStep(WORKSPACE_ROOT, checked.value);
          if (!res.ok) return toolError(res.error || "pushWhiteboardStep failed.");
          const structuredContent = {
            kind: "synt_whiteboard_step",
            ...res,
          };
          return {
            content: [{ type: "text", text: JSON.stringify(structuredContent) }],
            structuredContent,
          };
        }

        case "synt_capture_concept": {
          const res = captureConcept(WORKSPACE_ROOT, args);
          if (!res.ok) return toolError(res.error);
          const structuredContent = {
            kind: "synt_capture_concept_result",
            id: res.id,
            deduplicated: res.deduplicated,
            matchedId: res.matchedId,
            matchedScore: res.matchedScore,
            cueAdded: res.cueAdded,
            chainAdded: res.chainAdded,
            synced: res.synced,
          };
          return {
            content: [{ type: "text", text: JSON.stringify(structuredContent) }],
            structuredContent,
          };
        }

        case "synt_get_frontier": {
          const res = getFrontier(WORKSPACE_ROOT, args);
          if (!res.ok) return toolError(res.error);
          const structuredContent = {
            kind: "synt_frontier",
            ...res,
          };
          return {
            content: [{ type: "text", text: JSON.stringify(structuredContent) }],
            structuredContent,
          };
        }

        case "synt_log_review": {
          const res = logReview(WORKSPACE_ROOT, args);
          if (!res.ok) return toolError(res.error);
          const structuredContent = {
            kind: "synt_log_review_result",
            ...res,
          };
          return {
            content: [{ type: "text", text: JSON.stringify(structuredContent) }],
            structuredContent,
          };
        }

        case "synt_readiness": {
          const checked = validateReadinessArgs(args);
          if (!checked.ok) return toolError(`Invalid synt_readiness arguments: ${checked.error}`);
          const res = getReadiness(WORKSPACE_ROOT, checked.value);
          if (!res.ok) return toolError(res.error || "synt_readiness failed.");
          const structuredContent = {
            kind: "synt_readiness",
            ...res,
          };
          return {
            content: [{ type: "text", text: JSON.stringify(structuredContent) }],
            structuredContent,
          };
        }

        case "synt_interleave": {
          const checked = validateInterleaveArgs(args);
          if (!checked.ok) return toolError(`Invalid synt_interleave arguments: ${checked.error}`);
          const res = runInterleave(WORKSPACE_ROOT, checked.value);
          if (!res.ok) return toolError(res.error || "synt_interleave failed.");
          const kind =
            checked.value.action === "stats"
              ? "synt_interleave_stats"
              : checked.value.action === "check"
                ? "synt_interleave_check"
                : "synt_interleave";
          const structuredContent = {
            kind,
            ...res,
          };
          return {
            content: [{ type: "text", text: JSON.stringify(structuredContent) }],
            structuredContent,
          };
        }

        case "synt_app_load_state":
        case "mlh_app_load_state": {
          const section =
            typeof args.section === "string" && ["all", "autopilot", "graph", "flashcards", "traps"].includes(args.section)
              ? args.section
              : "all";
          const data = loadWorkspaceState(WORKSPACE_ROOT, section);
          const freshPlan = stateStore.loadStudioPlan();
          studioPlanState.planMermaid = freshPlan.planMermaid;
          studioPlanState.activeNodeId = freshPlan.activeNodeId;
          studioPlanState.visitedNodeIds = [...freshPlan.visitedNodeIds];
          studioPlanState.tab = freshPlan.tab || "";
          const structuredContent = {
            kind: `${pfx}_app_state`,
            plan: {
              planMermaid: freshPlan.planMermaid,
              activeNodeId: freshPlan.activeNodeId,
              visitedNodeIds: [...freshPlan.visitedNodeIds],
            },
            savedTab: freshPlan.tab || "",
            ...data,
          };
          return {
            content: [{ type: "text", text: JSON.stringify(structuredContent) }],
            structuredContent,
          };
        }

        case "synt_app_rate_flashcard":
        case "mlh_app_rate_flashcard": {
          const res = rateFlashcard(WORKSPACE_ROOT, args);
          if (!res.ok) {
            return toolError(res.error);
          }
          const refreshed = loadWorkspaceState(WORKSPACE_ROOT, "flashcards");
          const structuredContent = {
            kind: `${pfx}_app_rate_result`,
            review: res.result,
            fireCredits: res.result?.fireCredits || [],
            fireRefreshedCount: res.result?.fireRefreshedCount || 0,
            fireSummary: res.result?.fireSummary || null,
            stats: refreshed.stats,
            flashcards: refreshed.flashcards,
          };
          return {
            content: [{ type: "text", text: JSON.stringify(structuredContent) }],
            structuredContent,
          };
        }

        case "synt_app_save_sketch":
        case "mlh_app_save_sketch": {
          const res = saveSketchPng(WORKSPACE_ROOT, args);
          if (!res.ok) {
            return toolError(res.error);
          }
          const structuredContent = {
            kind: `${pfx}_app_sketch_saved`,
            path: res.path,
            relativePath: res.relativePath,
            bytes: res.bytes,
          };
          return {
            content: [{ type: "text", text: `Saved rough-work sketch to ${res.relativePath}` }],
            structuredContent,
          };
        }

        case "synt_app_card_state":
        case "mlh_app_card_state": {
          const op = typeof args.op === "string" ? args.op.trim() : "";
          const kind = typeof args.kind === "string" ? args.kind.trim() : "";
          const id = typeof args.id === "string" ? args.id.trim() : "";
          if (!["get", "save"].includes(op)) {
            return toolError(`${name} op must be "get" or "save" (got ${JSON.stringify(args.op)}).`);
          }
          if (!["quiz", "practice", "studio"].includes(kind)) {
            return toolError(`${name} kind must be "quiz", "practice", or "studio" (got ${JSON.stringify(args.kind)}).`);
          }
          if (kind !== "studio" && !id) {
            return toolError(`${name} requires a non-empty id for kind="${kind}".`);
          }
          const saved =
            op === "get"
              ? stateStore.getCardState(kind, id)
              : stateStore.saveCardState(kind, id, args.state || {});
          const structuredContent = {
            kind: `${pfx}_app_card_state`,
            op,
            cardKind: kind,
            id: id || "default",
            found: saved !== null,
            state: saved,
          };
          return {
            content: [{ type: "text", text: JSON.stringify(structuredContent) }],
            structuredContent,
          };
        }

        case "synt_app_update_profile":
        case "mlh_app_update_profile": {
          const checked = validateProfileUpdateArgs(args);
          if (!checked.ok) return toolError(`Invalid ${name} arguments: ${checked.error}`);
          const res = updateLearnerProfile(WORKSPACE_ROOT, checked.value);
          if (!res.ok) return toolError(res.error || "Profile update failed.");
          const structuredContent = {
            kind: `${pfx}_app_profile_updated`,
            ...res,
          };
          return {
            content: [{ type: "text", text: JSON.stringify(structuredContent) }],
            structuredContent,
          };
        }

        default:
          return toolError(`Unknown tool: ${name}`);
      }
    } catch (err) {
      return toolError(`Tool "${name}" failed: ${err?.message || String(err)}`);
    }
  });

  return server;
}

async function main() {
  const server = createServer();
  const transport = new StdioServerTransport();
  await server.connect(transport);
}

main().catch((err) => {
  process.stderr.write(`[syntopik-studio] fatal: ${err?.stack || err}\n`);
  process.exit(1);
});
