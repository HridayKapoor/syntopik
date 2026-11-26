/**
 * LearnHarness-grade argument healing, strict quiz validation, and
 * practice hidden-solution + 1-active-problem gate.
 */

const VALID_ROLES = new Set(["probe", "predict", "check"]);
const VALID_PRACTICE_ACTIONS = new Set(["pose", "conclude"]);
const VALID_PRACTICE_MODES = new Set([
  "standard",
  "parsons",
  "faded",
  "blackout",
  "recall",
  "discrimination",
  "composition",
]);
const PARSONS_MODES = new Set(["parsons", "faded"]);
const VALID_BLACKOUT_ROLES = new Set(["axiom", "step", "theorem"]);
const VALID_WHITEBOARD_KINDS = new Set([
  "derivation_step",
  "prediction_gate",
  "explore_widget",
  "diagram_callout",
  "summary",
]);
const VALID_ENERGY_MODES = new Set(["low", "normal", "deep"]);
const VALID_SCAFFOLDING_MODES = new Set([
  "worked_example_parsons",
  "faded",
  "standard",
  "challenge_transfer",
]);
const VALID_ENTRY_RAMPS = new Set(["intuition_first", "balanced", "formalism_first"]);
const VALID_CHALLENGE_APPETITES = new Set(["guided", "balanced", "adversarial"]);
const VALID_PROSE_DENSITIES = new Set(["terse", "standard", "rich"]);
const VALID_OUTCOMES = new Set([
  "solved",
  "solvedWithHints",
  "movedOn",
  "surrendered",
  "dismissed",
]);
const VALID_RUNGS = new Set(["R0", "R1", "R2", "R3", "R4", "R5"]);
const VALID_TABS = new Set(["autopilot", "plan", "graph", "flashcards", "traps"]);

function tryJsonParse(val) {
  if (typeof val !== "string") return val;
  const trimmed = val.trim();
  if (
    (trimmed.startsWith("[") && trimmed.endsWith("]")) ||
    (trimmed.startsWith("{") && trimmed.endsWith("}")) ||
    (trimmed.startsWith('"') && trimmed.endsWith('"'))
  ) {
    try {
      return JSON.parse(trimmed);
    } catch {
      return val;
    }
  }
  return val;
}

export function healRecord(raw) {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return {};
  const out = {};
  for (const [k, v] of Object.entries(raw)) {
    out[k] = typeof v === "string" ? tryJsonParse(v) : v;
  }
  return out;
}

export function hashSeed(text) {
  let h = 2166136261 >>> 0;
  const str = String(text || "");
  for (let i = 0; i < str.length; i += 1) {
    h ^= str.charCodeAt(i);
    h = Math.imul(h, 16777619) >>> 0;
  }
  return h || 1;
}

export function validateQuizArgs(rawArgs) {
  const args = healRecord(rawArgs);
  const errors = [];

  const role = typeof args.role === "string" ? args.role.trim() : "";
  if (!VALID_ROLES.has(role)) {
    errors.push(`role must be one of "probe", "predict", or "check" (got ${JSON.stringify(args.role)}).`);
  }

  const question = typeof args.question === "string" ? args.question.trim() : "";
  if (!question) {
    errors.push("question must be a non-empty string.");
  }

  const explanation = typeof args.explanation === "string" ? args.explanation.trim() : "";
  if (!explanation) {
    errors.push("explanation must be a non-empty string (reason before answer).");
  }

  const freeRecall = Boolean(args.freeRecall);
  const multiSelect = Boolean(args.multiSelect);
  const nodeId = typeof args.nodeId === "string" && args.nodeId.trim() ? args.nodeId.trim() : undefined;
  const committedAnswer =
    typeof args.committedAnswer === "string" && args.committedAnswer.trim()
      ? args.committedAnswer.trim()
      : undefined;

  let cleanOptions = [];
  let cleanCorrectAnswer;

  if (!freeRecall) {
    if (!Array.isArray(args.options) || args.options.length < 2) {
      errors.push("options must be an array of at least 2 items when freeRecall is false.");
    } else {
      const seenIds = new Set();
      const seenLabels = new Set();
      args.options.forEach((opt, idx) => {
        if (!opt || typeof opt !== "object" || Array.isArray(opt)) {
          errors.push(`options[${idx}] must be an object with {id, label}.`);
          return;
        }
        const id = typeof opt.id === "string" ? opt.id.trim() : "";
        const label = typeof opt.label === "string" ? opt.label.trim() : "";
        if (!id) errors.push(`options[${idx}].id must be a non-empty string.`);
        if (!label) errors.push(`options[${idx}].label must be a non-empty string.`);
        if (id) {
          if (seenIds.has(id)) {
            errors.push(`Duplicate option id "${id}". Every option id must be unique.`);
          }
          seenIds.add(id);
        }
        if (label) {
          const norm = label.replace(/\s+/g, " ").toLowerCase();
          if (seenLabels.has(norm)) {
            errors.push(`Duplicate option label "${label}". Every option label must be distinct.`);
          }
          seenLabels.add(norm);
        }
        if (id && label) {
          cleanOptions.push({ id, label });
        }
      });

      const validIds = new Set(cleanOptions.map((o) => o.id));
      if (args.correctAnswer === undefined || args.correctAnswer === null || args.correctAnswer === "") {
        errors.push("correctAnswer is required when freeRecall is false.");
      } else if (multiSelect) {
        const arr = Array.isArray(args.correctAnswer)
          ? args.correctAnswer
          : typeof args.correctAnswer === "string"
            ? [args.correctAnswer]
            : null;
        if (!arr || arr.length === 0) {
          errors.push("correctAnswer must be a non-empty array of valid option ids when multiSelect is true.");
        } else {
          const dedup = [];
          const seen = new Set();
          for (const rawId of arr) {
            const id = typeof rawId === "string" ? rawId.trim() : "";
            if (!id || !validIds.has(id)) {
              errors.push(`correctAnswer contains unknown option id ${JSON.stringify(rawId)}.`);
            } else if (!seen.has(id)) {
              seen.add(id);
              dedup.push(id);
            }
          }
          cleanCorrectAnswer = dedup;
        }
      } else {
        if (Array.isArray(args.correctAnswer)) {
          errors.push("correctAnswer must be a single option id string when multiSelect is false (set multiSelect: true for arrays).");
        } else if (typeof args.correctAnswer !== "string" || !validIds.has(args.correctAnswer.trim())) {
          errors.push(
            `correctAnswer (${JSON.stringify(args.correctAnswer)}) does not match any option id (${[...validIds].join(", ")}).`
          );
        } else {
          cleanCorrectAnswer = args.correctAnswer.trim();
        }
      }
    }
  } else {
    if (typeof args.correctAnswer === "string" && args.correctAnswer.trim()) {
      cleanCorrectAnswer = args.correctAnswer.trim();
    } else if (Array.isArray(args.correctAnswer)) {
      cleanCorrectAnswer = args.correctAnswer.map((s) => String(s).trim()).filter(Boolean);
    }
  }

  if (errors.length > 0) {
    return { ok: false, error: errors.join(" ") };
  }

  return {
    ok: true,
    value: {
      role,
      nodeId,
      question,
      options: cleanOptions,
      multiSelect,
      freeRecall,
      explanation,
      correctAnswer: cleanCorrectAnswer,
      committedAnswer,
    },
  };
}

export function validatePracticePose(rawArgs, activeProblem) {
  const args = healRecord(rawArgs);
  const errors = [];

  const statement = typeof args.statement === "string" ? args.statement.trim() : "";
  const modelSolution = typeof args.modelSolution === "string" ? args.modelSolution.trim() : "";
  const problemId =
    typeof args.problemId === "string" && args.problemId.trim()
      ? args.problemId.trim()
      : `prob-${Date.now().toString(36)}`;

  if (!statement) {
    errors.push("statement must be a non-empty string when action is 'pose'.");
  }
  if (!modelSolution) {
    errors.push("modelSolution must be a non-empty committed solution string when action is 'pose'.");
  }

  let mode;
  if (args.mode !== undefined && args.mode !== null && args.mode !== "") {
    if (typeof args.mode !== "string" || !VALID_PRACTICE_MODES.has(args.mode.trim())) {
      errors.push(
        `mode must be one of standard, parsons, faded, blackout, recall, discrimination, or composition (got ${JSON.stringify(args.mode)}).`
      );
    } else {
      mode = args.mode.trim();
    }
  }

  const isParsonsMode = Boolean(mode && PARSONS_MODES.has(mode));
  const isBlackoutMode = mode === "blackout";
  let cleanBlocks = null;
  let cleanCanonicalOrder = null;
  let cleanSteps = null;
  const derivationId =
    typeof args.derivationId === "string" && args.derivationId.trim() ? args.derivationId.trim() : null;
  const rawLevel = Number(args.blackoutLevel ?? args.level ?? 1);
  const blackoutLevel = Number.isFinite(rawLevel) ? Math.max(1, Math.min(3, Math.round(rawLevel))) : 1;

  if (args.blocks !== undefined && args.blocks !== null) {
    if (!Array.isArray(args.blocks) || args.blocks.length < 2) {
      errors.push("blocks must be an array of at least 2 block objects {id, code, indent?, isDistractor?}.");
    } else {
      const seenBlockIds = new Set();
      cleanBlocks = [];
      args.blocks.forEach((blk, idx) => {
        if (!blk || typeof blk !== "object" || Array.isArray(blk)) {
          errors.push(`blocks[${idx}] must be an object with {id, code}.`);
          return;
        }
        const id = typeof blk.id === "string" ? blk.id.trim() : "";
        const code = typeof blk.code === "string" ? blk.code : "";
        if (!id) errors.push(`blocks[${idx}].id must be a non-empty string.`);
        if (!code.trim()) errors.push(`blocks[${idx}].code must be a non-empty string.`);
        if (id) {
          if (seenBlockIds.has(id)) {
            errors.push(`Duplicate block id "${id}". Every Parsons block id must be unique.`);
          }
          seenBlockIds.add(id);
        }
        const rawIndent = Number(blk.indent ?? 0);
        const indent = Number.isFinite(rawIndent) ? Math.max(0, Math.min(8, Math.round(rawIndent))) : 0;
        const isDistractor = Boolean(blk.isDistractor);
        if (id && code.trim()) {
          cleanBlocks.push({ id, code, indent, isDistractor });
        }
      });

      const validBlockIds = new Set(cleanBlocks.map((b) => b.id));
      if (args.canonicalOrder !== undefined && args.canonicalOrder !== null) {
        if (!Array.isArray(args.canonicalOrder) || args.canonicalOrder.length === 0) {
          errors.push("canonicalOrder must be a non-empty array of block id strings.");
        } else {
          const dedupOrder = [];
          const seenOrd = new Set();
          for (const rawId of args.canonicalOrder) {
            const cid = typeof rawId === "string" ? rawId.trim() : "";
            if (!cid || !validBlockIds.has(cid)) {
              errors.push(`canonicalOrder contains unknown block id ${JSON.stringify(rawId)}.`);
            } else if (!seenOrd.has(cid)) {
              seenOrd.add(cid);
              dedupOrder.push(cid);
            }
          }
          cleanCanonicalOrder = dedupOrder;
        }
      } else if (cleanBlocks.length > 0) {
        cleanCanonicalOrder = cleanBlocks.filter((b) => !b.isDistractor).map((b) => b.id);
        if (cleanCanonicalOrder.length === 0) {
          errors.push("Parsons blocks must contain at least one non-distractor block (or explicit canonicalOrder).");
        }
      }
    }
  } else if (isParsonsMode) {
    errors.push(`blocks array (at least 2 items) is required when mode is "${mode}".`);
  }

  const rawSteps = args.steps ?? args.blackoutSteps;
  if (rawSteps !== undefined && rawSteps !== null) {
    if (!Array.isArray(rawSteps) || rawSteps.length < 2) {
      errors.push("steps must be an array of at least 2 derivation step objects {id, label?, role?, expr?, expectedExpr?, hidden?, dependsOn?}.");
    } else {
      const seenStepIds = new Set();
      cleanSteps = [];
      rawSteps.forEach((st, idx) => {
        if (!st || typeof st !== "object" || Array.isArray(st)) {
          errors.push(`steps[${idx}] must be an object.`);
          return;
        }
        const id = typeof st.id === "string" && st.id.trim() ? st.id.trim() : `s${idx + 1}`;
        if (seenStepIds.has(id)) {
          errors.push(`Duplicate blackout step id "${id}". Every step id must be unique.`);
        }
        seenStepIds.add(id);

        const rawRole = typeof st.role === "string" ? st.role.trim().toLowerCase() : "step";
        const role = VALID_BLACKOUT_ROLES.has(rawRole) ? rawRole : "step";
        const label = typeof st.label === "string" && st.label.trim() ? st.label.trim() : id;
        const rawExpr = typeof st.expr === "string" ? st.expr.trim() : "";
        const rawExpected =
          typeof st.expectedExpr === "string" && st.expectedExpr.trim()
            ? st.expectedExpr.trim()
            : typeof st.answer === "string" && st.answer.trim()
              ? st.answer.trim()
              : "";
        const hidden =
          typeof st.hidden === "boolean"
            ? st.hidden
            : role !== "axiom" && Boolean(rawExpected);
        const sealedExpr = hidden ? rawExpected || rawExpr : rawExpr;
        if (!sealedExpr) {
          errors.push(
            hidden
              ? `steps[${idx}] ("${id}") is hidden and requires expectedExpr (or expr) to seal server-side.`
              : `steps[${idx}] ("${id}") requires a non-empty expr.`
          );
        }
        const dependsOn = Array.isArray(st.dependsOn)
          ? [...new Set(st.dependsOn.map((d) => String(d || "").trim()).filter((d) => d && d !== id))]
          : [];
        const note = typeof st.note === "string" ? st.note.trim() : "";
        if (sealedExpr) {
          cleanSteps.push({
            id,
            label,
            role,
            dependsOn,
            hidden,
            expr: sealedExpr,
            expectedExpr: hidden ? sealedExpr : null,
            note,
          });
        }
      });

      if (isBlackoutMode && cleanSteps.length > 0 && !cleanSteps.some((s) => s.hidden)) {
        errors.push('mode="blackout" requires at least one step with hidden: true.');
      }
    }
  } else if (isBlackoutMode) {
    errors.push('steps array (at least 2 items with >= 1 hidden step) is required when mode is "blackout".');
  }

  let targets = [];
  if (args.targets !== undefined && args.targets !== null) {
    if (!Array.isArray(args.targets)) {
      errors.push("targets must be an array of proposition ID strings.");
    } else {
      targets = args.targets.map((t) => String(t).trim()).filter(Boolean);
    }
  }

  // Enforce LearnHarness 1-active-problem gate unless explicitly replacing the same problemId.
  if (activeProblem && !activeProblem.concluded) {
    const isExplicitReplace =
      typeof args.problemId === "string" && args.problemId.trim() === activeProblem.problemId;
    if (!isExplicitReplace) {
      errors.push(
        `Active practice problem "${activeProblem.problemId}" is still open. Conclude it with action="conclude" before posing another problem (or pass the same problemId to replace/repair it).`
      );
    }
  }

  if (errors.length > 0) {
    return { ok: false, error: errors.join(" ") };
  }

  return {
    ok: true,
    value: {
      problemId,
      statement,
      mode,
      isParsonsMode,
      isBlackoutMode,
      blocks: cleanBlocks,
      canonicalOrder: cleanCanonicalOrder,
      steps: cleanSteps,
      derivationId,
      blackoutLevel,
      targets,
      modelSolution,
    },
  };
}

export function validatePracticeConclude(rawArgs, activeProblem, problemHistory) {
  const args = healRecord(rawArgs);
  const errors = [];

  const requestedId =
    typeof args.problemId === "string" && args.problemId.trim() ? args.problemId.trim() : undefined;
  const record =
    (requestedId && problemHistory.get(requestedId)) ||
    (activeProblem && (!requestedId || activeProblem.problemId === requestedId) ? activeProblem : null);

  if (!record) {
    errors.push(
      requestedId
        ? `No posed practice problem found for problemId "${requestedId}".`
        : "No active practice problem to conclude. Pose a problem first."
    );
  }

  const outcome = typeof args.outcome === "string" ? args.outcome.trim() : "";
  if (!VALID_OUTCOMES.has(outcome)) {
    errors.push(
      `outcome must be one of solved, solvedWithHints, movedOn, surrendered, or dismissed (got ${JSON.stringify(args.outcome)}).`
    );
  }

  const verdict = typeof args.verdict === "string" ? args.verdict.trim() : "";
  if (!verdict) {
    errors.push("verdict must be a non-empty string summarizing the outcome.");
  }

  let rung = null;
  if (args.rung !== undefined && args.rung !== null && args.rung !== "") {
    const r = String(args.rung).trim();
    if (!VALID_RUNGS.has(r)) {
      errors.push(`rung must be R0..R5 or null (got ${JSON.stringify(args.rung)}).`);
    } else {
      rung = r;
    }
  }

  const missClass =
    typeof args.missClass === "string" && args.missClass.trim() ? args.missClass.trim() : null;
  const retest = Boolean(args.retest);

  if (errors.length > 0) {
    return { ok: false, error: errors.join(" ") };
  }

  return {
    ok: true,
    value: {
      record,
      outcome,
      verdict,
      rung,
      missClass,
      retest,
    },
  };
}

export function validatePracticeAction(rawArgs) {
  const args = healRecord(rawArgs);
  const action = typeof args.action === "string" ? args.action.trim() : "";
  if (!VALID_PRACTICE_ACTIONS.has(action)) {
    return {
      ok: false,
      error: `action must be "pose" or "conclude" (got ${JSON.stringify(args.action)}).`,
    };
  }
  return { ok: true, action, args };
}

export function validateStudioArgs(rawArgs) {
  const args = healRecord(rawArgs);
  const planMermaid =
    typeof args.planMermaid === "string" && args.planMermaid.trim()
      ? args.planMermaid.trim()
      : undefined;
  const activeNodeId =
    typeof args.activeNodeId === "string" && args.activeNodeId.trim()
      ? args.activeNodeId.trim()
      : undefined;
  const visitedNodeIds = Array.isArray(args.visitedNodeIds)
    ? args.visitedNodeIds.map((v) => String(v).trim()).filter(Boolean)
    : undefined;
  const rawTab = typeof args.tab === "string" ? args.tab.trim() : undefined;
  const tab = rawTab && VALID_TABS.has(rawTab) ? rawTab : undefined;
  return { planMermaid, activeNodeId, visitedNodeIds, tab };
}

export function validateNextMoveArgs(rawArgs) {
  const args = healRecord(rawArgs);
  const errors = [];
  let energy;
  if (args.energy !== undefined && args.energy !== null && args.energy !== "") {
    const e = String(args.energy).trim().toLowerCase();
    if (!VALID_ENERGY_MODES.has(e)) {
      errors.push(`energy must be low, normal, or deep (got ${JSON.stringify(args.energy)}).`);
    } else {
      energy = e;
    }
  }
  const domain =
    typeof args.domain === "string" && args.domain.trim() ? args.domain.trim() : undefined;
  const event =
    args.event !== undefined && args.event !== null && args.event !== "" ? args.event : undefined;
  if (errors.length > 0) return { ok: false, error: errors.join(" ") };
  return { ok: true, value: { energy, domain, event } };
}

export function validateWhiteboardStepArgs(rawArgs) {
  const args = healRecord(rawArgs);
  const errors = [];
  const rawKind =
    typeof args.kind === "string" && args.kind.trim()
      ? args.kind.trim().toLowerCase()
      : "derivation_step";
  if (!VALID_WHITEBOARD_KINDS.has(rawKind)) {
    errors.push(
      `kind must be one of derivation_step, prediction_gate, explore_widget, diagram_callout, or summary (got ${JSON.stringify(args.kind)}).`
    );
  }
  const title = typeof args.title === "string" ? args.title.trim() : "";
  const body =
    typeof args.body === "string"
      ? args.body.trim()
      : typeof args.content === "string"
        ? args.content.trim()
        : "";
  const latex = typeof args.latex === "string" && args.latex.trim() ? args.latex.trim() : undefined;
  const propId = typeof args.propId === "string" && args.propId.trim() ? args.propId.trim() : undefined;
  const id = typeof args.id === "string" && args.id.trim() ? args.id.trim() : undefined;
  const sessionTitle =
    typeof args.sessionTitle === "string" && args.sessionTitle.trim()
      ? args.sessionTitle.trim()
      : undefined;
  const resolvePinId =
    typeof args.resolvePinId === "string" && args.resolvePinId.trim()
      ? args.resolvePinId.trim()
      : undefined;
  const resolution =
    typeof args.resolution === "string" && args.resolution.trim()
      ? args.resolution.trim()
      : undefined;

  let stepIndex;
  if (args.stepIndex !== undefined && args.stepIndex !== null && args.stepIndex !== "") {
    const n = Number(args.stepIndex);
    if (!Number.isFinite(n) || n < 0) {
      errors.push(`stepIndex must be a non-negative integer (got ${JSON.stringify(args.stepIndex)}).`);
    } else {
      stepIndex = Math.round(n);
    }
  }

  const isGate = rawKind === "prediction_gate";
  const locked =
    typeof args.locked === "boolean" ? args.locked : isGate ? true : false;
  const gate =
    args.gate && typeof args.gate === "object" && !Array.isArray(args.gate)
      ? { ...args.gate }
      : undefined;
  const prompt =
    typeof args.prompt === "string" && args.prompt.trim()
      ? args.prompt.trim()
      : gate && typeof gate.prompt === "string" && gate.prompt.trim()
        ? gate.prompt.trim()
        : undefined;
  const revealedBody =
    typeof args.revealedBody === "string" && args.revealedBody.trim()
      ? args.revealedBody.trim()
      : gate && typeof gate.revealedBody === "string" && gate.revealedBody.trim()
        ? gate.revealedBody.trim()
        : undefined;
  const expectedAnswer =
    args.expectedAnswer !== undefined
      ? args.expectedAnswer
      : gate && gate.expectedAnswer !== undefined
        ? gate.expectedAnswer
        : undefined;
  const payload =
    args.payload && typeof args.payload === "object" && !Array.isArray(args.payload)
      ? args.payload
      : {};

  if (!title && !body && !latex && Object.keys(payload).length === 0 && !prompt) {
    errors.push("Provide at least one of title, body, latex, prompt, or payload for a whiteboard frame.");
  }
  if (errors.length > 0) return { ok: false, error: errors.join(" ") };

  return {
    ok: true,
    value: {
      id,
      kind: rawKind,
      title: title || rawKind.replace(/_/g, " "),
      body,
      latex,
      propId,
      stepIndex,
      locked,
      prompt,
      revealedBody,
      expectedAnswer,
      gate,
      payload,
      sessionTitle,
      resolvePinId,
      resolution,
    },
  };
}

export function validateProfileUpdateArgs(rawArgs) {
  const args = healRecord(rawArgs);
  const errors = [];
  const rawOverrides =
    args.userOverrides && typeof args.userOverrides === "object" && !Array.isArray(args.userOverrides)
      ? args.userOverrides
      : {};
  const hasCalibrationInput =
    args.entryRamp !== undefined ||
    args.challengeAppetite !== undefined ||
    args.domainFluency !== undefined ||
    args.proseDensity !== undefined ||
    args.sessionMinutes !== undefined;
  const hasOverrideInput =
    args.userOverrides !== undefined ||
    args.scaffoldingMode !== undefined ||
    args.allowProductiveFailure !== undefined ||
    args.maxProseWords !== undefined ||
    args.clearOverrides === true;

  let op = typeof args.op === "string" ? args.op.trim().toLowerCase() : "";
  if (!op) {
    op = hasCalibrationInput && !hasOverrideInput ? "calibrate" : "override";
  }
  if (!["override", "calibrate", "reset"].includes(op)) {
    errors.push(`op must be override, calibrate, or reset (got ${JSON.stringify(args.op)}).`);
  }

  const overrides = {};
  if (op === "override") {
    if (args.clearOverrides === true) {
      overrides.scaffoldingMode = null;
      overrides.allowProductiveFailure = null;
      overrides.maxProseWords = null;
    } else {
      const scaf =
        args.scaffoldingMode !== undefined ? args.scaffoldingMode : rawOverrides.scaffoldingMode;
      if (scaf !== undefined) {
        if (scaf === null || scaf === "" || scaf === "auto" || scaf === "clear") {
          overrides.scaffoldingMode = null;
        } else if (typeof scaf === "string" && VALID_SCAFFOLDING_MODES.has(scaf.trim())) {
          overrides.scaffoldingMode = scaf.trim();
        } else {
          errors.push(`scaffoldingMode must be one of ${[...VALID_SCAFFOLDING_MODES].join(", ")} or null.`);
        }
      }
      const allowPf =
        args.allowProductiveFailure !== undefined
          ? args.allowProductiveFailure
          : rawOverrides.allowProductiveFailure;
      if (allowPf !== undefined) {
        if (allowPf === null || allowPf === "" || allowPf === "auto" || allowPf === "clear") {
          overrides.allowProductiveFailure = null;
        } else if (typeof allowPf === "boolean") {
          overrides.allowProductiveFailure = allowPf;
        } else {
          errors.push("allowProductiveFailure must be boolean or null.");
        }
      }
      const maxW =
        args.maxProseWords !== undefined ? args.maxProseWords : rawOverrides.maxProseWords;
      if (maxW !== undefined) {
        if (maxW === null || maxW === "" || maxW === "auto" || maxW === "clear") {
          overrides.maxProseWords = null;
        } else {
          const n = Number(maxW);
          if (!Number.isFinite(n) || n < 60 || n > 160) {
            errors.push("maxProseWords must be an integer between 60 and 160 (or null).");
          } else {
            overrides.maxProseWords = Math.round(n);
          }
        }
      }
    }
  }

  const calibration = {};
  if (op === "calibrate") {
    if (args.entryRamp !== undefined) {
      const er = String(args.entryRamp).trim().toLowerCase();
      if (!VALID_ENTRY_RAMPS.has(er)) errors.push(`Invalid entryRamp ${JSON.stringify(args.entryRamp)}.`);
      else calibration.entryRamp = er;
    }
    if (args.challengeAppetite !== undefined) {
      const ca = String(args.challengeAppetite).trim().toLowerCase();
      if (!VALID_CHALLENGE_APPETITES.has(ca)) {
        errors.push(`Invalid challengeAppetite ${JSON.stringify(args.challengeAppetite)}.`);
      } else {
        calibration.challengeAppetite = ca;
      }
    }
    if (args.proseDensity !== undefined) {
      const pd = String(args.proseDensity).trim().toLowerCase();
      if (!VALID_PROSE_DENSITIES.has(pd)) {
        errors.push(`Invalid proseDensity ${JSON.stringify(args.proseDensity)}.`);
      } else {
        calibration.proseDensity = pd;
      }
    }
    if (args.sessionMinutes !== undefined) {
      const sm = Number(args.sessionMinutes);
      if (!Number.isFinite(sm) || sm < 5 || sm > 180) {
        errors.push("sessionMinutes must be between 5 and 180.");
      } else {
        calibration.sessionMinutes = Math.round(sm);
      }
    }
    if (args.domainFluency && typeof args.domainFluency === "object" && !Array.isArray(args.domainFluency)) {
      const cleanFluency = {};
      for (const [k, v] of Object.entries(args.domainFluency)) {
        const n = Number(v);
        if (k.trim() && Number.isFinite(n)) {
          cleanFluency[k.trim()] = Math.max(0, Math.min(3, Math.round(n)));
        }
      }
      calibration.domainFluency = cleanFluency;
    }
  }

  if (errors.length > 0) return { ok: false, error: errors.join(" ") };
  return { ok: true, value: { op, overrides, calibration } };
}
