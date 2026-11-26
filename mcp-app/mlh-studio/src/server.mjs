/**
 * syntopik-studio — Official SEP-1865 MCP App Server (with backwards-compatible mlh_* aliases)
 *
 * Exposes:
 *   - Canonical model-visible tools:
 *       1. synt_quiz_card      (inline ui://syntopik/quiz-card.html)
 *       2. synt_practice_card  (inline ui://syntopik/practice-card.html)
 *       3. synt_lesson_studio  (aux_pane + global ui://syntopik/studio-pane.html)
 *   - Backwards-compatible model-visible aliases:
 *       mlh_quiz_card, mlh_practice_card, mlh_lesson_studio (ui://mlh/*.html)
 *   - App-only tools (_meta.ui.visibility = ["app"], 0 model tokens):
 *       synt_app_load_state, synt_app_rate_flashcard, synt_app_save_sketch, synt_app_card_state
 *       (+ mlh_app_* aliases)
 *   - Terminal Host Safeguard:
 *       Inspects client initialize capabilities (`extensions["io.modelcontextprotocol/ui"]`).
 *       When absent (e.g., `claude` or `codex` TUI), emits clean Markdown question + bare
 *       lettered options + "I don't know" (quiz) or statement (practice pose) in content[0].text
 *       while keeping correctAnswer, explanation, and modelSolution locked server-side!
 */

import fs from "node:fs";
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
  validatePracticeAction,
  validatePracticeConclude,
  validatePracticePose,
  validateQuizArgs,
  validateStudioArgs,
} from "./validation.mjs";
import { loadWorkspaceState, rateFlashcard, saveSketchPng } from "./workspace.mjs";
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

function findRepoRootFallback() {
  // Bundled dist/server.mjs or src/server.mjs sits 3 levels inside <repo>/mcp-app/mlh-studio/{dist,src}
  const candidate = path.resolve(HERE, "..", "..", "..");
  if (fs.existsSync(path.join(candidate, "AGENTS.md"))) {
    return candidate;
  }
  return "";
}

function parseWorkspaceArg(argv) {
  const fromFlag = parseCliFlag(argv, "--workspace");
  const isGenericDotOrUnexpanded =
    !fromFlag || fromFlag === "." || fromFlag.includes("${");

  if (!isGenericDotOrUnexpanded) {
    return path.resolve(fromFlag);
  }

  const envWs = process.env.SYNT_WORKSPACE || process.env.MLH_WORKSPACE || "";
  if (envWs.trim()) {
    return path.resolve(envWs.trim());
  }

  const claudeDir = process.env.CLAUDE_PROJECT_DIR || "";
  if (claudeDir.trim()) {
    return path.resolve(claudeDir.trim());
  }

  if (fromFlag === "." && fs.existsSync(path.join(process.cwd(), "AGENTS.md"))) {
    return process.cwd();
  }

  const repoFallback = findRepoRootFallback();
  if (repoFallback) {
    return repoFallback;
  }

  if (fromFlag && !fromFlag.includes("${")) {
    return path.resolve(fromFlag);
  }
  return process.cwd();
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
    description: "Calm inline practice problem card with server-held hidden modelSolution.",
    filename: "practice-card.html",
    envOverrides: ["SYNT_PRACTICE_HTML", "MLH_PRACTICE_HTML"],
  },
  {
    uri: "ui://syntopik/studio-pane.html",
    name: "Syntopik Studio",
    description: "Live Syntopik Studio side-pane: Autopilot, Plan DAG, Knowledge Graph, FSRS-6 Flashcards, and Mistake Traps.",
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
    mode: { type: "string", enum: ["recall", "discrimination", "composition"] },
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
      "Pose a practice problem (locking modelSolution hidden server-side with a 1-active-problem gate) or conclude it to reveal the committed modelSolution verbatim with verdict and Pause Law.",
    inputSchema: PRACTICE_INPUT_SCHEMA,
    _meta: {
      ui: { resourceUri: "ui://syntopik/practice-card.html" },
    },
  },
  {
    name: "synt_lesson_studio",
    title: "Syntopik Studio",
    description:
      "Open or update the live Syntopik Studio pane (Autopilot progress, Plan DAG, Knowledge Graph, zero-token FSRS-6 Flashcards, Mistake Traps). All arguments optional.",
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
    // Terminal Host Safeguard (#94 §1.C): include bare question + lettered options + "I don't know"
    // in content[0].text while keeping correctAnswer, explanation, and committedAnswer hidden server-side!
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
    const record = {
      problemId: v.problemId,
      statement: v.statement,
      mode: v.mode || null,
      targets: v.targets,
      modelSolution: v.modelSolution,
      posedAt: new Date().toISOString(),
      concluded: false,
    };
    activeProblem = record;
    problemHistory.set(record.problemId, record);
    stateStore.savePracticePose(record);

    // SECURITY INVARIANT: modelSolution, mode, and targets are NEVER sent on pose!
    const textMsg = hasUiExtension
      ? `Practice problem posed (id=${record.problemId}). Committed modelSolution locked server-side; awaiting learner attempt or hint request.`
      : [
          `Practice problem posed (id=${record.problemId}) — Terminal fallback (committed modelSolution locked server-side):`,
          "",
          record.statement,
          "",
          "Present the problem cleanly and await the learner's attempt, R0–R5 hint request, or surrender before calling action=\"conclude\".",
        ].join("\n");

    return {
      content: [{ type: "text", text: textMsg }],
      structuredContent: {
        kind: `${pfx}_practice_card`,
        action: "pose",
        problemId: record.problemId,
        statement: record.statement,
        hasHiddenSolution: true,
        ...(hasUiExtension ? {} : { terminalFallback: true }),
      },
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

  const concludeText = hasUiExtension
    ? `Practice problem ${record.problemId} concluded (${outcome}${rung ? `, ${rung}` : ""}). Verbatim committed modelSolution revealed. Pause for learner go-ahead.`
    : [
        `Practice problem ${record.problemId} concluded (${outcome}${rung ? `, ${rung}` : ""}).`,
        `Verdict: ${verdict}`,
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
    { name: "syntopik-studio", version: "2.0.0" },
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
