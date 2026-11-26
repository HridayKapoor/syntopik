/**
 * ui://mlh/quiz-card.html — Inline Probe / Predict / Collapsed Check Card
 *
 * Parity with LearnHarness QuestionDock + ReadingThinkingField + AnsweredCard:
 *  - role === "check": starts collapsed behind "Check your understanding" + live "Thinking while reading" field
 *  - role === "probe" | "predict": starts open immediately
 *  - Deterministic client-side option shuffling (A/B/C/D) + dashed "I don't know" non-penalty row
 *  - Color-coded 0–100% Confidence slider (default 50%, hidden when "I don't know" selected)
 *  - "Your thinking" textarea + collapsible mini rough-work <canvas> sketch pad
 *  - Instant client-side grading + tucked "Why" explanation disclosure + app.sendMessage() back to chat
 */

import {
  bootstrapMcpApp,
  createSketchPad,
  escapeHtml,
  renderInlineRich,
  renderRichText,
  seededShuffle,
} from "./shared.mjs";

const root = document.getElementById("app");

const state = {
  data: null,
  loading: true,
  cancelled: false,
  revealed: false,
  shuffledOptions: [],
  selectedIds: new Set(),
  dontKnow: false,
  freeAnswer: "",
  confidence: 50,
  thinkingWhileReading: "",
  thinking: "",
  submitted: false,
  submitting: false,
  verdict: null, // "correct" | "incorrect" | "skipped" | "free_recall"
  savedSketchPath: null,
  statusMsg: "",
  statusKind: "info",
  caps: null,
  connected: false,
  hydratedKey: "",
  hydratingKey: "",
  userTouched: false,
};

let sketchPad = null;
let persistTimer = null;

function hashFallbackKey(text) {
  let h = 2166136261 >>> 0;
  const str = String(text || "");
  for (let i = 0; i < str.length; i += 1) {
    h ^= str.charCodeAt(i);
    h = Math.imul(h, 16777619) >>> 0;
  }
  return (h || 1).toString(36);
}

function getCardKey(sc) {
  if (!sc || typeof sc !== "object") return "";
  if (typeof sc.cardId === "string" && sc.cardId.trim()) return sc.cardId.trim();
  return `quiz-h-${hashFallbackKey(`${sc.role || "probe"}|${sc.nodeId || ""}|${sc.question || ""}`)}`;
}

function serializeQuizState() {
  return {
    revealed: Boolean(state.revealed),
    selectedIds: [...state.selectedIds],
    dontKnow: Boolean(state.dontKnow),
    freeAnswer: state.freeAnswer,
    confidence: state.confidence,
    thinkingWhileReading: state.thinkingWhileReading,
    thinking: state.thinking,
    submitted: Boolean(state.submitted),
    verdict: state.verdict,
    savedSketchPath: state.savedSketchPath,
    statusMsg: state.statusMsg,
    statusKind: state.statusKind,
  };
}

async function persistNow() {
  if (persistTimer) {
    clearTimeout(persistTimer);
    persistTimer = null;
  }
  const key = getCardKey(state.data);
  if (!key || !state.connected || !state.caps?.canCallTools) return;
  try {
    await bridge.app.callServerTool({
      name: "synt_app_card_state",
      arguments: {
        op: "save",
        kind: "quiz",
        id: key,
        state: serializeQuizState(),
      },
    });
  } catch {
    // Non-fatal: keep local UI responsive
  }
}

function schedulePersist(delayMs = 250) {
  if (persistTimer) clearTimeout(persistTimer);
  persistTimer = setTimeout(() => {
    persistTimer = null;
    persistNow();
  }, delayMs);
}

async function hydrateFromServer() {
  const key = getCardKey(state.data);
  if (!key || !state.connected || !state.caps?.canCallTools) return;
  if (state.hydratedKey === key || state.hydratingKey === key) return;
  state.hydratingKey = key;

  try {
    const res = await bridge.app.callServerTool({
      name: "synt_app_card_state",
      arguments: { op: "get", kind: "quiz", id: key },
    });
    if (getCardKey(state.data) !== key) return;
    state.hydratedKey = key;
    state.hydratingKey = "";

    const sc = res?.structuredContent;
    const saved = sc?.found && sc?.state && typeof sc.state === "object" ? sc.state : null;
    if (!saved) return;

    // Always restore a submitted card; restore an in-progress draft unless the user
    // already started editing fresh inputs before this fast lookup returned.
    if (saved.submitted === true || !state.userTouched) {
      if (typeof saved.revealed === "boolean") state.revealed = saved.revealed;
      if (Array.isArray(saved.selectedIds)) {
        const validOptIds = new Set(state.shuffledOptions.map((o) => o.id));
        const restored = saved.selectedIds.filter((id) => validOptIds.size === 0 || validOptIds.has(id));
        state.selectedIds = new Set(restored);
      }
      if (typeof saved.dontKnow === "boolean") state.dontKnow = saved.dontKnow;
      if (typeof saved.freeAnswer === "string") state.freeAnswer = saved.freeAnswer;
      if (typeof saved.confidence === "number" && Number.isFinite(saved.confidence)) {
        state.confidence = saved.confidence;
      }
      if (typeof saved.thinkingWhileReading === "string") {
        state.thinkingWhileReading = saved.thinkingWhileReading;
      }
      if (typeof saved.thinking === "string") state.thinking = saved.thinking;
      if (saved.savedSketchPath !== undefined) state.savedSketchPath = saved.savedSketchPath;
      if (saved.submitted === true) {
        state.submitted = true;
        state.revealed = true;
        state.verdict = saved.verdict || evaluateVerdict();
        state.statusMsg = saved.statusMsg || "Response saved & sent to tutor.";
        state.statusKind = saved.statusKind || "success";
      }
      render();
    }
  } catch {
    if (state.hydratingKey === key) state.hydratingKey = "";
  }
}

function roleLabel(role) {
  if (role === "probe") return "Probe";
  if (role === "predict") return "Prediction";
  return "Check";
}

function roleBadgeClass(role) {
  if (role === "predict") return "badge badge-warning";
  if (role === "check") return "badge badge-info";
  return "badge";
}

function confidenceClass(val) {
  if (val <= 30) return "conf-low";
  if (val >= 70) return "conf-high";
  return "conf-mid";
}

function normalizeCorrectSet(correctAnswer) {
  if (Array.isArray(correctAnswer)) return new Set(correctAnswer.map(String));
  if (typeof correctAnswer === "string" && correctAnswer) return new Set([correctAnswer]);
  return new Set();
}

function evaluateVerdict() {
  if (state.dontKnow) return "skipped";
  if (state.data.freeRecall) return "free_recall";
  const expected = normalizeCorrectSet(state.data.correctAnswer);
  if (expected.size === 0) return "free_recall";
  if (state.selectedIds.size !== expected.size) return "incorrect";
  for (const id of expected) {
    if (!state.selectedIds.has(id)) return "incorrect";
  }
  return "correct";
}

function buildChatMessage() {
  const d = state.data;
  const lines = [];
  const headerNode = d.nodeId ? ` · \`node: ${d.nodeId}\`` : "";
  lines.push(`**[Quiz Response · ${roleLabel(d.role).toUpperCase()}]**${headerNode}`);

  if (state.dontKnow) {
    lines.push(`- **Selection:** I don't know`);
    lines.push(`- **Verdict:** Skipped (honest gap)`);
  } else if (d.freeRecall) {
    lines.push(`- **Answer:** ${state.freeAnswer.trim() || "(empty)"}`);
    lines.push(`- **Verdict:** Free-recall (awaiting tutor check)`);
    lines.push(`- **Confidence:** ${state.confidence}%`);
  } else {
    const picked = state.shuffledOptions.filter((o) => state.selectedIds.has(o.id));
    const selStr = picked.map((o) => `\`${o.letter}\` — ${o.label} (\`${o.id}\`)`).join("; ");
    lines.push(`- **Selection:** ${selStr}`);
    lines.push(`- **Verdict:** ${state.verdict === "correct" ? "Correct" : "Incorrect"}`);
    lines.push(`- **Confidence:** ${state.confidence}%`);
    const orderStr = state.shuffledOptions.map((o) => `${o.letter}=\`${o.id}\``).join(", ");
    lines.push(`- **Option order:** ${orderStr}`);
  }

  if (state.thinkingWhileReading.trim()) {
    lines.push(`- **Thinking while reading:** ${state.thinkingWhileReading.trim()}`);
  }
  if (state.thinking.trim()) {
    lines.push(`- **Your thinking:** ${state.thinking.trim()}`);
  }
  if (state.savedSketchPath) {
    lines.push(`- **Rough-work sketch:** \`${state.savedSketchPath}\``);
  }
  return lines.join("\n");
}

function canSubmitNow() {
  if (!state.data || state.submitted || state.submitting) return false;
  if (state.dontKnow) return true;
  if (state.data.freeRecall) return state.freeAnswer.trim().length > 0;
  return state.selectedIds.size > 0;
}

async function handleSubmit() {
  if (!canSubmitNow()) return;
  state.submitting = true;
  state.statusMsg = "";
  render();

  try {
    // 1. Save rough-work sketch PNG only if the learner actually drew on it
    if (sketchPad && sketchPad.hasInk() && state.caps?.canCallTools) {
      try {
        const res = await bridge.app.callServerTool({
          name: "synt_app_save_sketch",
          arguments: {
            dataUrl: sketchPad.toPngDataUrl(),
            label: state.data.role || "quiz",
          },
        });
        const sc = res?.structuredContent;
        if (sc?.relativePath) {
          state.savedSketchPath = sc.relativePath;
          if (state.caps?.canUpdateContext) {
            await bridge.app.updateModelContext({
              content: [
                {
                  type: "text",
                  text: `Learner attached rough-work sketch at ${sc.relativePath} (${sc.path}) for ${state.data.role}${state.data.nodeId ? ` (${state.data.nodeId})` : ""}.`,
                },
              ],
            });
          }
        }
      } catch {
        // Non-fatal: continue submitting response
      }
    }

    // 2. Instant client-side verdict + persist to disk BEFORE sendMessage so
    // immediate chat switches / iframe remounts can never reset the card!
    state.verdict = evaluateVerdict();
    state.submitted = true;
    state.submitting = false;
    state.statusMsg = state.caps?.canMessage ? "Response sent to tutor." : "Graded locally (standalone preview).";
    state.statusKind = state.caps?.canMessage ? "success" : "info";
    await persistNow();

    // 3. Send clean structured response into active Antigravity chat if supported
    if (state.caps?.canMessage) {
      const msgText = buildChatMessage();
      const sendRes = await bridge.app.sendMessage({
        role: "user",
        content: [{ type: "text", text: msgText }],
      });
      if (sendRes?.isError) {
        state.statusMsg = "Graded & saved locally; host declined sending chat message.";
        state.statusKind = "danger";
        await persistNow();
      } else {
        state.statusMsg = "Response sent to tutor.";
        state.statusKind = "success";
      }
    } else if (!state.connected) {
      state.statusMsg = "Graded locally (standalone preview).";
      state.statusKind = "info";
    }
  } catch (err) {
    state.submitting = false;
    state.statusMsg = `Submit error: ${err?.message || String(err)}`;
    state.statusKind = "danger";
  }

  render();
}

function render() {
  root.innerHTML = "";

  if (state.cancelled) {
    root.innerHTML = `<div class="card"><div class="card-bd"><span class="muted">Quiz card cancelled.</span></div></div>`;
    return;
  }

  if (state.loading || !state.data) {
    root.innerHTML = `<div class="card"><div class="card-hd"><span class="badge badge-info">Quiz</span><span class="subtle">Preparing question…</span></div><div class="card-bd"><span class="muted">Waiting for question payload…</span></div></div>`;
    return;
  }

  const d = state.data;
  const card = document.createElement("div");
  card.className = "card";

  // Header
  const hd = document.createElement("div");
  hd.className = "card-hd";
  const leftHd = document.createElement("div");
  leftHd.className = "row";
  const rBadge = document.createElement("span");
  rBadge.className = roleBadgeClass(d.role);
  rBadge.textContent = roleLabel(d.role);
  leftHd.append(rBadge);
  if (d.nodeId) {
    const nTag = document.createElement("span");
    nTag.className = "mono-tag";
    nTag.textContent = d.nodeId;
    leftHd.append(nTag);
  }
  if (d.multiSelect && !d.freeRecall) {
    const mTag = document.createElement("span");
    mTag.className = "subtle";
    mTag.textContent = "Select all that apply";
    leftHd.append(mTag);
  }

  const rightHd = document.createElement("div");
  rightHd.className = "row";
  if (state.submitted) {
    const vBadge = document.createElement("span");
    if (state.verdict === "correct") {
      vBadge.className = "badge badge-success";
      vBadge.textContent = "✓ Correct";
    } else if (state.verdict === "incorrect") {
      vBadge.className = "badge badge-danger";
      vBadge.textContent = "✗ Incorrect";
    } else if (state.verdict === "skipped") {
      vBadge.className = "badge badge-warning";
      vBadge.textContent = "Skipped · Gap noted";
    } else {
      vBadge.className = "badge badge-info";
      vBadge.textContent = "Submitted for review";
    }
    rightHd.append(vBadge);
  }
  hd.append(leftHd, rightHd);
  card.append(hd);

  const bd = document.createElement("div");
  bd.className = "card-bd";

  // --- LearnHarness Check Arrival Law: collapsed behind reveal bar + Thinking while reading ---
  if (d.role === "check" && !state.revealed) {
    const twrWrap = document.createElement("div");
    const twrLabel = document.createElement("div");
    twrLabel.className = "field-label";
    twrLabel.innerHTML = `<span>Thinking while reading</span><span class="subtle">Optional · rides with your check</span>`;
    const twrInput = document.createElement("textarea");
    twrInput.className = "input";
    twrInput.rows = 2;
    twrInput.placeholder = "Jot reactions, doubts, or half-formed predictions as you read…";
    twrInput.value = state.thinkingWhileReading;
    twrInput.addEventListener("input", () => {
      state.userTouched = true;
      state.thinkingWhileReading = twrInput.value;
      schedulePersist();
    });
    twrWrap.append(twrLabel, twrInput);
    bd.append(twrWrap);

    const revealBtn = document.createElement("button");
    revealBtn.type = "button";
    revealBtn.className = "btn btn-primary btn-full";
    revealBtn.textContent = "Check your understanding";
    revealBtn.addEventListener("click", () => {
      state.userTouched = true;
      state.revealed = true;
      render();
      persistNow();
    });
    bd.append(revealBtn);

    card.append(bd);
    root.append(card);
    return;
  }

  // Show Thinking while reading snippet if learner wrote notes before revealing check
  if (d.role === "check" && state.thinkingWhileReading.trim()) {
    const twrNote = document.createElement("div");
    twrNote.className = "list-item";
    twrNote.innerHTML = `<div class="subtle"><strong>Thinking while reading</strong></div><div class="muted">${escapeHtml(state.thinkingWhileReading)}</div>`;
    bd.append(twrNote);
  }

  // Question stem
  const stem = document.createElement("div");
  stem.className = "stem";
  stem.innerHTML = renderRichText(d.question);
  bd.append(stem);

  const expectedSet = normalizeCorrectSet(d.correctAnswer);

  // Options (MC) OR Free Recall textarea
  if (!d.freeRecall) {
    const allShort =
      state.shuffledOptions.length <= 4 &&
      state.shuffledOptions.every((o) => String(o.label || "").length <= 48 && !o.label.includes("\n"));
    const grid = document.createElement("div");
    grid.className = allShort ? "options-grid two-col" : "options-grid";

    for (const opt of state.shuffledOptions) {
      const btn = document.createElement("button");
      btn.type = "button";
      const isSelected = state.selectedIds.has(opt.id);
      let cls = "opt-row";
      if (state.submitted) {
        if (expectedSet.has(opt.id)) cls += " correct";
        else if (isSelected) cls += " wrong";
      } else if (isSelected) {
        cls += " selected";
      }
      btn.className = cls;
      btn.disabled = state.submitted;
      btn.innerHTML = `<span class="opt-letter">${escapeHtml(opt.letter)}</span><span class="opt-label">${renderInlineRich(opt.label)}</span>`;
      btn.addEventListener("click", () => {
        if (state.submitted) return;
        state.userTouched = true;
        state.dontKnow = false;
        if (d.multiSelect) {
          if (state.selectedIds.has(opt.id)) state.selectedIds.delete(opt.id);
          else state.selectedIds.add(opt.id);
        } else {
          state.selectedIds.clear();
          state.selectedIds.add(opt.id);
        }
        render();
        persistNow();
      });
      grid.append(btn);
    }
    bd.append(grid);
  } else {
    const frWrap = document.createElement("div");
    const frLabel = document.createElement("div");
    frLabel.className = "field-label";
    frLabel.textContent = "Your answer";
    const frInput = document.createElement("textarea");
    frInput.className = "input";
    frInput.rows = 3;
    frInput.placeholder = "Write your explanation, formula, or derivation…";
    frInput.value = state.freeAnswer;
    frInput.disabled = state.submitted || state.dontKnow;
    frInput.addEventListener("input", () => {
      state.userTouched = true;
      state.freeAnswer = frInput.value;
      updateSubmitBtnState();
      schedulePersist();
    });
    frWrap.append(frLabel, frInput);
    bd.append(frWrap);
  }

  // Dashed "I don't know" non-penalty row
  const dkBtn = document.createElement("button");
  dkBtn.type = "button";
  dkBtn.className = `opt-row dont-know${state.dontKnow ? " selected" : ""}`;
  dkBtn.disabled = state.submitted;
  dkBtn.textContent = "I don't know";
  dkBtn.addEventListener("click", () => {
    if (state.submitted) return;
    state.userTouched = true;
    state.dontKnow = !state.dontKnow;
    if (state.dontKnow) {
      state.selectedIds.clear();
    }
    render();
    persistNow();
  });
  bd.append(dkBtn);

  // Interactive 0–100% Confidence Slider (hidden when "I don't know" is picked!)
  if (!state.dontKnow) {
    const confBox = document.createElement("div");
    confBox.className = "conf-box";
    const chancePct =
      !d.freeRecall && state.shuffledOptions.length > 0
        ? Math.round(100 / state.shuffledOptions.length)
        : null;
    const confHead = document.createElement("div");
    confHead.className = "row-between";
    const confTitle = document.createElement("span");
    confTitle.className = "muted";
    confTitle.innerHTML = chancePct
      ? `Confidence <span class="subtle">(chance: ${chancePct}%)</span>`
      : "Confidence";
    const confReadout = document.createElement("span");
    confReadout.className = `conf-val ${confidenceClass(state.confidence)}`;
    confReadout.textContent = `${state.confidence}%`;
    confHead.append(confTitle, confReadout);

    const slider = document.createElement("input");
    slider.type = "range";
    slider.min = "0";
    slider.max = "100";
    slider.step = "5";
    slider.value = String(state.confidence);
    slider.disabled = state.submitted;
    slider.className = "conf-slider";
    slider.setAttribute("aria-label", "Confidence percentage");
    slider.addEventListener("input", () => {
      state.userTouched = true;
      state.confidence = Number(slider.value);
      confReadout.textContent = `${state.confidence}%`;
      confReadout.className = `conf-val ${confidenceClass(state.confidence)}`;
      schedulePersist(200);
    });
    slider.addEventListener("change", () => {
      state.userTouched = true;
      state.confidence = Number(slider.value);
      persistNow();
    });

    const ticks = document.createElement("div");
    ticks.className = "conf-ticks";
    ticks.innerHTML = `<span>0% doubt</span><span>25%</span><span>50% coin flip</span><span>75%</span><span>100% certain</span>`;
    confBox.append(confHead, slider, ticks);
    bd.append(confBox);
  }

  // "Your thinking" textarea
  const thinkWrap = document.createElement("div");
  const thinkLabel = document.createElement("div");
  thinkLabel.className = "field-label";
  thinkLabel.innerHTML = `<span>Your thinking</span><span class="subtle">Primary signal for the tutor</span>`;
  const thinkInput = document.createElement("textarea");
  thinkInput.className = "input";
  thinkInput.rows = 2;
  thinkInput.placeholder = "Why did you pick this (or eliminate the others)?";
  thinkInput.value = state.thinking;
  thinkInput.disabled = state.submitted;
  thinkInput.addEventListener("input", () => {
    state.userTouched = true;
    state.thinking = thinkInput.value;
    schedulePersist();
  });
  thinkWrap.append(thinkLabel, thinkInput);
  bd.append(thinkWrap);

  // Collapsible Mini Rough-Work Sketch Pad (pre-submit)
  if (!state.submitted) {
    if (!sketchPad) sketchPad = createSketchPad();
    bd.append(sketchPad.el);
  } else if (state.savedSketchPath) {
    const skChip = document.createElement("div");
    skChip.className = "subtle";
    skChip.innerHTML = `Attached sketch: <span class="mono-tag">${escapeHtml(state.savedSketchPath)}</span>`;
    bd.append(skChip);
  }

  // Post-submit tucked "Why" / reference disclosure (NEVER mounted before submit!)
  if (state.submitted) {
    const details = document.createElement("details");
    details.className = "why-box";
    if (state.verdict === "incorrect" || state.verdict === "skipped") {
      details.open = true;
    }
    const summary = document.createElement("summary");
    summary.textContent = d.freeRecall ? "Reference answer & key idea" : "Why";
    const body = document.createElement("div");
    body.className = "stem";
    if (d.freeRecall && d.committedAnswer) {
      body.innerHTML =
        renderRichText(d.committedAnswer) +
        (d.explanation && d.explanation !== d.committedAnswer
          ? `<hr class="fc-divider"/>${renderRichText(d.explanation)}`
          : "");
    } else {
      body.innerHTML = renderRichText(d.explanation);
    }
    details.append(summary, body);
    bd.append(details);
  }

  if (state.statusMsg) {
    const toast = document.createElement("div");
    toast.className = `toast ${state.statusKind === "success" ? "toast-success" : state.statusKind === "danger" ? "toast-danger" : ""}`;
    toast.textContent = state.statusMsg;
    bd.append(toast);
  }

  card.append(bd);

  // Footer with capability-gated Submit button
  if (!state.submitted) {
    const ft = document.createElement("div");
    ft.className = "card-ft";

    const hostBlocksMessage = state.connected && state.caps && !state.caps.canMessage;
    const hint = document.createElement("span");
    hint.className = "subtle";
    hint.textContent = hostBlocksMessage
      ? "Chat messaging unavailable on this surface — grades locally."
      : state.dontKnow
        ? "Honest gaps help calibrate the next step."
        : "Submits selection, confidence, and thinking to your tutor.";

    const submitBtn = document.createElement("button");
    submitBtn.id = "mlh-quiz-submit";
    submitBtn.type = "button";
    submitBtn.className = "btn btn-primary";
    submitBtn.disabled = !canSubmitNow();
    submitBtn.textContent = state.submitting
      ? "Submitting…"
      : hostBlocksMessage
        ? "Check locally"
        : "Submit";
    submitBtn.addEventListener("click", handleSubmit);

    ft.append(hint, submitBtn);
    card.append(ft);
  }

  root.append(card);
}

function updateSubmitBtnState() {
  const btn = document.getElementById("mlh-quiz-submit");
  if (btn) btn.disabled = !canSubmitNow();
}

function loadPayload(sc) {
  if (!sc || typeof sc !== "object") return;
  const prevKey = getCardKey(state.data);
  const nextKey = getCardKey(sc);
  const isNewCard = !state.data || prevKey !== nextKey;
  state.data = sc;
  state.loading = false;
  state.cancelled = false;
  if (isNewCard) {
    state.revealed = sc.role !== "check";
    state.shuffledOptions = Array.isArray(sc.options)
      ? seededShuffle(sc.options, sc.shuffleSeed || 1)
      : [];
    state.selectedIds = new Set();
    state.dontKnow = false;
    state.freeAnswer = "";
    state.confidence = 50;
    state.thinkingWhileReading = "";
    state.thinking = "";
    state.submitted = false;
    state.submitting = false;
    state.verdict = null;
    state.savedSketchPath = null;
    state.statusMsg = "";
    state.hydratedKey = "";
    state.hydratingKey = "";
    state.userTouched = false;
    sketchPad = null;
  }
  render();
  hydrateFromServer();
}

const bridge = bootstrapMcpApp("syntopik-quiz-card", {
  onToolInput(args) {
    if (!state.data && args?.question) {
      state.loading = true;
      render();
    }
  },
  onToolResult(result) {
    if (result?.isError) {
      const msg = result?.content?.[0]?.text || "Quiz tool returned an error.";
      state.loading = false;
      state.statusMsg = msg;
      state.statusKind = "danger";
      root.innerHTML = `<div class="card"><div class="card-bd"><div class="toast toast-danger">${escapeHtml(msg)}</div></div></div>`;
      return;
    }
    if (result?.structuredContent) {
      loadPayload(result.structuredContent);
    }
  },
  onToolCancelled() {
    state.cancelled = true;
    render();
  },
  onReady({ connected, caps }) {
    state.connected = connected;
    state.caps = caps;
    render();
    hydrateFromServer();
  },
});

render();
