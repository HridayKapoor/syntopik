/**
 * ui://syntopik/practice-card.html — Inline Practice Problem Card (Standard, Parsons & Faded)
 *
 * Parity with LearnHarness PracticeDock + Commitment & Pause Law + Adaptive Parsons/Faded Scaffolding:
 *  - Active pose: displays Markdown + LaTeX statement ONLY (NEVER modelSolution, canonicalOrder, or isDistractor!)
 *  - Standard mode: working trace textarea + collapsible rough-work <canvas> sketch pad
 *  - Parsons / Faded mode: keyboard-accessible block ordering (↑/↓ buttons + Alt+Arrow keys),
 *    distractor exclude/restore tray, and inline faded blank inputs for ___ segments
 *  - 3 capability-gated actions: Ask Hint | Submit Attempt | Reveal Solution
 *  - Conclude: renders outcome/rung/missClass/retest badges + verdict + canonical order + verbatim committed modelSolution
 *  - Enforces Pause Law via explicit "Next problem →" button
 */

import {
  bootstrapMcpApp,
  createSketchPad,
  escapeHtml,
  renderRichText,
} from "./shared.mjs";

const root = document.getElementById("app");

const state = {
  data: null,
  loading: true,
  cancelled: false,
  attemptText: "",
  parsonsOrder: [],
  excludedIds: [],
  fadedBlanks: {},
  busy: false,
  sentHistory: [],
  statusMsg: "",
  statusKind: "info",
  nextRequested: false,
  serverConcluded: false,
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

function getProblemKey(sc) {
  if (!sc || typeof sc !== "object") return "";
  if (typeof sc.problemId === "string" && sc.problemId.trim()) return sc.problemId.trim();
  return `prob-h-${hashFallbackKey(sc.statement || "")}`;
}

function isParsonsPayload(d) {
  return Boolean(
    d &&
      (d.cardMode === "parsons" || d.cardMode === "faded") &&
      Array.isArray(d.blocks) &&
      d.blocks.length > 0
  );
}

function initParsonsStateFromData(d) {
  if (!isParsonsPayload(d)) {
    state.parsonsOrder = [];
    state.excludedIds = [];
    state.fadedBlanks = {};
    return;
  }
  state.parsonsOrder = d.blocks.map((b) => b.id);
  state.excludedIds = [];
  state.fadedBlanks = {};
}

function serializePracticeState() {
  return {
    attemptText: state.attemptText,
    parsonsOrder: [...state.parsonsOrder],
    excludedIds: [...state.excludedIds],
    fadedBlanks: { ...state.fadedBlanks },
    sentHistory: [...state.sentHistory],
    statusMsg: state.statusMsg,
    statusKind: state.statusKind,
    nextRequested: Boolean(state.nextRequested),
  };
}

async function persistNow() {
  if (persistTimer) {
    clearTimeout(persistTimer);
    persistTimer = null;
  }
  const key = getProblemKey(state.data);
  if (!key || !state.connected || !state.caps?.canCallTools) return;
  try {
    await bridge.app.callServerTool({
      name: "synt_app_card_state",
      arguments: {
        op: "save",
        kind: "practice",
        id: key,
        state: serializePracticeState(),
      },
    });
  } catch {
    // Non-fatal
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
  const key = getProblemKey(state.data);
  if (!key || !state.connected || !state.caps?.canCallTools) return;
  if (state.hydratedKey === key || state.hydratingKey === key) return;
  state.hydratingKey = key;

  try {
    const res = await bridge.app.callServerTool({
      name: "synt_app_card_state",
      arguments: { op: "get", kind: "practice", id: key },
    });
    if (getProblemKey(state.data) !== key) return;
    state.hydratedKey = key;
    state.hydratingKey = "";

    const sc = res?.structuredContent;
    const saved = sc?.found && sc?.state && typeof sc.state === "object" ? sc.state : null;
    if (!saved) return;

    if (typeof saved.serverConcluded === "boolean") {
      state.serverConcluded = saved.serverConcluded;
    }
    if (!state.userTouched) {
      if (typeof saved.attemptText === "string") {
        state.attemptText = saved.attemptText;
      }
      if (isParsonsPayload(state.data)) {
        const validIds = new Set(state.data.blocks.map((b) => b.id));
        if (Array.isArray(saved.parsonsOrder) && saved.parsonsOrder.length > 0) {
          const cleanOrd = saved.parsonsOrder.filter((id) => validIds.has(id));
          const cleanExcl = Array.isArray(saved.excludedIds)
            ? saved.excludedIds.filter((id) => validIds.has(id) && !cleanOrd.includes(id))
            : [];
          const covered = new Set([...cleanOrd, ...cleanExcl]);
          for (const b of state.data.blocks) {
            if (!covered.has(b.id)) cleanOrd.push(b.id);
          }
          state.parsonsOrder = cleanOrd;
          state.excludedIds = cleanExcl;
        }
        if (saved.fadedBlanks && typeof saved.fadedBlanks === "object") {
          state.fadedBlanks = { ...saved.fadedBlanks };
        }
      }
    }
    if (Array.isArray(saved.sentHistory) && saved.sentHistory.length > 0) {
      state.sentHistory = saved.sentHistory;
    }
    if (typeof saved.nextRequested === "boolean") {
      state.nextRequested = saved.nextRequested;
    }
    if (!state.statusMsg && typeof saved.statusMsg === "string" && saved.statusMsg) {
      state.statusMsg = saved.statusMsg;
      state.statusKind = saved.statusKind || "info";
    }
    render();
  } catch {
    if (state.hydratingKey === key) state.hydratingKey = "";
  }
}

const OUTCOME_META = {
  solved: { label: "Solved", cls: "badge-success" },
  solvedWithHints: { label: "Solved with Hints", cls: "badge-info" },
  movedOn: { label: "Moved On", cls: "badge-warning" },
  surrendered: { label: "Surrendered", cls: "badge-danger" },
  dismissed: { label: "Dismissed", cls: "badge-warning" },
};

function resolveFilledCode(blk) {
  let idx = 0;
  return String(blk.code || "").replace(/_{3,}/g, () => {
    const key = `${blk.id}::${idx}`;
    idx += 1;
    const val = (state.fadedBlanks[key] || "").trim();
    return val ? `[${val}]` : "___";
  });
}

function buildParsonsSummaryLines(d) {
  const byId = new Map(d.blocks.map((b) => [b.id, b]));
  const activeBlocks = state.parsonsOrder.map((id) => byId.get(id)).filter(Boolean);
  const exclBlocks = state.excludedIds.map((id) => byId.get(id)).filter(Boolean);
  const letters = activeBlocks.map((b) => b.letter || b.id).join(", ");
  const ids = activeBlocks.map((b) => b.id).join(", ");
  const lines = [`- **Ordered blocks (${d.cardMode}):** \`${letters || "(none)"}\` (\`${ids || "none"}\`)`];
  activeBlocks.forEach((b, i) => {
    const pad = b.indent > 0 ? "  ".repeat(b.indent) : "";
    const renderedCode = d.cardMode === "faded" ? resolveFilledCode(b) : b.code;
    lines.push(`  ${i + 1}. [${b.letter || b.id}] \`${pad}${renderedCode}\``);
  });
  if (exclBlocks.length > 0) {
    lines.push(
      `- **Excluded (distractors):** ${exclBlocks.map((b) => `\`${b.letter || b.id}\` (${b.id})`).join(", ")}`
    );
  }
  return lines;
}

async function maybeSaveSketch(tag) {
  if (!sketchPad || !sketchPad.hasInk() || !state.caps?.canCallTools) return null;
  try {
    const res = await bridge.app.callServerTool({
      name: "synt_app_save_sketch",
      arguments: {
        dataUrl: sketchPad.toPngDataUrl(),
        label: `practice-${tag}`,
      },
    });
    const sc = res?.structuredContent;
    if (sc?.relativePath) {
      if (state.caps?.canUpdateContext) {
        await bridge.app.updateModelContext({
          content: [
            {
              type: "text",
              text: `Learner rough-work sketch for practice problem ${state.data.problemId}: ${sc.relativePath} (${sc.path})`,
            },
          ],
        });
      }
      return sc.relativePath;
    }
  } catch {
    // Non-fatal
  }
  return null;
}

async function sendPracticeAction(kind) {
  if (!state.data || state.busy) return;
  if (state.connected && state.caps && !state.caps.canMessage) return;

  state.busy = true;
  state.statusMsg = "";
  render();

  try {
    const sketchPath = kind === "next" ? null : await maybeSaveSketch(kind);
    const pid = state.data.problemId || "active";
    const parsons = isParsonsPayload(state.data);
    let text = "";

    if (kind === "hint") {
      const lines = [`**[Practice · Hint Request]** (\`${pid}\`)`];
      if (parsons) lines.push(...buildParsonsSummaryLines(state.data));
      if (state.attemptText.trim()) {
        lines.push(`- **Current thinking / work so far:** ${state.attemptText.trim()}`);
      } else if (!parsons) {
        lines.push(`- **Request:** Give me the next minimal non-spoiling hint rung (R0–R5).`);
      }
      if (sketchPath) lines.push(`- **Rough-work sketch:** \`${sketchPath}\``);
      text = lines.join("\n");
    } else if (kind === "attempt") {
      const lines = [`**[Practice · Attempt]** (\`${pid}\`)`];
      if (parsons) {
        lines.push(...buildParsonsSummaryLines(state.data));
        if (state.attemptText.trim()) {
          lines.push(`- **Notes / Reasoning:** ${state.attemptText.trim()}`);
        }
      } else {
        lines.push(`- **Working / Answer:** ${state.attemptText.trim() || "(see sketch)"}`);
      }
      if (sketchPath) lines.push(`- **Rough-work sketch:** \`${sketchPath}\``);
      text = lines.join("\n");
    } else if (kind === "reveal") {
      const lines = [`**[Practice · Reveal Solution]** (\`${pid}\`)`];
      lines.push(`- **Request:** I'm surrendering / asking to conclude and reveal the committed model solution.`);
      if (parsons) lines.push(...buildParsonsSummaryLines(state.data));
      if (state.attemptText.trim()) {
        lines.push(`- **Last thinking before reveal:** ${state.attemptText.trim()}`);
      }
      if (sketchPath) lines.push(`- **Rough-work sketch:** \`${sketchPath}\``);
      text = lines.join("\n");
    } else if (kind === "next") {
      text = "Next problem";
    }

    const labelMap = {
      hint: "Hint request sent",
      attempt: "Attempt submitted",
      reveal: "Reveal requested",
      next: "Requested next problem",
    };

    if (state.caps?.canMessage) {
      state.sentHistory.push(labelMap[kind] || "Sent");
      if (kind === "next") state.nextRequested = true;
      state.statusMsg = `${labelMap[kind]}.`;
      state.statusKind = "success";
      await persistNow();

      const res = await bridge.app.sendMessage({
        role: "user",
        content: [{ type: "text", text }],
      });
      if (res?.isError) {
        state.sentHistory.pop();
        if (kind === "next") state.nextRequested = false;
        state.statusMsg = "Host declined sending message.";
        state.statusKind = "danger";
        await persistNow();
      }
    } else {
      state.statusMsg = `Prepared "${kind}" message (standalone preview).`;
      state.statusKind = "info";
    }
  } catch (err) {
    state.statusMsg = `Action failed: ${err?.message || String(err)}`;
    state.statusKind = "danger";
  }

  state.busy = false;
  render();
}

function moveParsonsBlock(index, delta) {
  const target = index + delta;
  if (target < 0 || target >= state.parsonsOrder.length) return;
  const next = [...state.parsonsOrder];
  [next[index], next[target]] = [next[target], next[index]];
  state.userTouched = true;
  state.parsonsOrder = next;
  schedulePersist(150);
  render();
}

function excludeParsonsBlock(blockId) {
  if (!state.parsonsOrder.includes(blockId)) return;
  state.userTouched = true;
  state.parsonsOrder = state.parsonsOrder.filter((id) => id !== blockId);
  if (!state.excludedIds.includes(blockId)) {
    state.excludedIds = [...state.excludedIds, blockId];
  }
  schedulePersist(150);
  render();
}

function restoreParsonsBlock(blockId) {
  if (!state.excludedIds.includes(blockId)) return;
  state.userTouched = true;
  state.excludedIds = state.excludedIds.filter((id) => id !== blockId);
  state.parsonsOrder = [...state.parsonsOrder, blockId];
  schedulePersist(150);
  render();
}

function renderParsonsCodeContent(blk, isFaded) {
  const codeWrap = document.createElement("div");
  codeWrap.className = "parsons-code";
  if (blk.indent > 0) {
    codeWrap.style.paddingLeft = `${blk.indent * 14}px`;
  }
  const raw = String(blk.code || "");
  if (!isFaded || !/_{3,}/.test(raw)) {
    codeWrap.textContent = raw;
    return codeWrap;
  }

  const parts = raw.split(/(_{3,})/g);
  let blankIdx = 0;
  for (const part of parts) {
    if (/^_{3,}$/.test(part)) {
      const key = `${blk.id}::${blankIdx}`;
      const myIdx = blankIdx;
      blankIdx += 1;
      const inp = document.createElement("input");
      inp.type = "text";
      inp.className = "faded-blank-input";
      inp.placeholder = "___";
      inp.setAttribute("aria-label", `Blank ${myIdx + 1} in block ${blk.letter || blk.id}`);
      inp.value = state.fadedBlanks[key] || "";
      inp.disabled = state.busy;
      inp.addEventListener("input", () => {
        state.userTouched = true;
        state.fadedBlanks[key] = inp.value;
        schedulePersist();
      });
      codeWrap.append(inp);
    } else if (part) {
      codeWrap.append(document.createTextNode(part));
    }
  }
  return codeWrap;
}

function renderParsonsWorkspace(d) {
  const isFaded = d.cardMode === "faded";
  const byId = new Map(d.blocks.map((b) => [b.id, b]));
  const wrap = document.createElement("div");
  wrap.className = "parsons-wrap";

  const hdr = document.createElement("div");
  hdr.className = "field-label";
  const leftLabel = document.createElement("span");
  leftLabel.textContent = isFaded
    ? "Order valid steps & fill blanks (exclude distractors)"
    : "Order valid steps (exclude any distractors)";
  const resetBtn = document.createElement("button");
  resetBtn.type = "button";
  resetBtn.className = "btn btn-sm btn-ghost";
  resetBtn.disabled = state.busy;
  resetBtn.textContent = "Reset";
  resetBtn.addEventListener("click", () => {
    state.userTouched = true;
    initParsonsStateFromData(d);
    schedulePersist(150);
    render();
  });
  hdr.append(leftLabel, resetBtn);
  wrap.append(hdr);

  const list = document.createElement("div");
  list.className = "parsons-list";
  list.setAttribute("role", "list");

  state.parsonsOrder.forEach((blockId, idx) => {
    const blk = byId.get(blockId);
    if (!blk) return;

    const row = document.createElement("div");
    row.className = "parsons-row";
    row.setAttribute("role", "listitem");
    row.tabIndex = 0;
    row.addEventListener("keydown", (ev) => {
      if (ev.target && ev.target.tagName === "INPUT") return;
      if ((ev.altKey || ev.shiftKey) && ev.key === "ArrowUp") {
        ev.preventDefault();
        moveParsonsBlock(idx, -1);
      } else if ((ev.altKey || ev.shiftKey) && ev.key === "ArrowDown") {
        ev.preventDefault();
        moveParsonsBlock(idx, 1);
      }
    });

    const num = document.createElement("span");
    num.className = "parsons-index";
    num.textContent = `${idx + 1}.`;

    const letter = document.createElement("span");
    letter.className = "opt-letter";
    letter.textContent = blk.letter || String.fromCharCode(65 + idx);

    const codeEl = renderParsonsCodeContent(blk, isFaded);

    const ctrls = document.createElement("div");
    ctrls.className = "parsons-controls";

    const upBtn = document.createElement("button");
    upBtn.type = "button";
    upBtn.className = "btn btn-sm";
    upBtn.disabled = state.busy || idx === 0;
    upBtn.textContent = "↑";
    upBtn.title = "Move block up";
    upBtn.setAttribute("aria-label", `Move block ${blk.letter || blk.id} up`);
    upBtn.addEventListener("click", () => moveParsonsBlock(idx, -1));

    const downBtn = document.createElement("button");
    downBtn.type = "button";
    downBtn.className = "btn btn-sm";
    downBtn.disabled = state.busy || idx === state.parsonsOrder.length - 1;
    downBtn.textContent = "↓";
    downBtn.title = "Move block down";
    downBtn.setAttribute("aria-label", `Move block ${blk.letter || blk.id} down`);
    downBtn.addEventListener("click", () => moveParsonsBlock(idx, 1));

    const exclBtn = document.createElement("button");
    exclBtn.type = "button";
    exclBtn.className = "btn btn-sm btn-ghost";
    exclBtn.disabled = state.busy || state.parsonsOrder.length <= 1;
    exclBtn.textContent = "Exclude";
    exclBtn.title = "Mark as distractor and remove from sequence";
    exclBtn.addEventListener("click", () => excludeParsonsBlock(blk.id));

    ctrls.append(upBtn, downBtn, exclBtn);
    row.append(num, letter, codeEl, ctrls);
    list.append(row);
  });

  wrap.append(list);

  if (state.excludedIds.length > 0) {
    const tray = document.createElement("div");
    tray.className = "parsons-tray";
    const trayTitle = document.createElement("div");
    trayTitle.className = "subtle";
    trayTitle.innerHTML = `<strong>Excluded as distractors (${state.excludedIds.length})</strong>`;
    tray.append(trayTitle);

    for (const blockId of state.excludedIds) {
      const blk = byId.get(blockId);
      if (!blk) continue;
      const exRow = document.createElement("div");
      exRow.className = "parsons-row excluded";

      const letter = document.createElement("span");
      letter.className = "opt-letter";
      letter.textContent = blk.letter || "?";

      const codeEl = document.createElement("div");
      codeEl.className = "parsons-code";
      codeEl.textContent = blk.code;

      const restoreBtn = document.createElement("button");
      restoreBtn.type = "button";
      restoreBtn.className = "btn btn-sm";
      restoreBtn.disabled = state.busy;
      restoreBtn.textContent = "Restore";
      restoreBtn.addEventListener("click", () => restoreParsonsBlock(blk.id));

      exRow.append(letter, codeEl, restoreBtn);
      tray.append(exRow);
    }
    wrap.append(tray);
  }

  return wrap;
}

function render() {
  root.innerHTML = "";

  if (state.cancelled) {
    root.innerHTML = `<div class="card"><div class="card-bd"><span class="muted">Practice card cancelled.</span></div></div>`;
    return;
  }

  if (state.loading || !state.data) {
    root.innerHTML = `<div class="card"><div class="card-hd"><span class="badge badge-info">Practice</span><span class="subtle">Loading problem…</span></div><div class="card-bd"><span class="muted">Waiting for practice problem…</span></div></div>`;
    return;
  }

  const d = state.data;
  const isConcluded = d.action === "conclude";
  const isParsons = isParsonsPayload(d);
  const hostBlocksMessage = state.connected && state.caps && !state.caps.canMessage;

  const card = document.createElement("div");
  card.className = "card";

  // Header (NEVER displays legacy recall/discrimination/composition mode or targets!)
  const hd = document.createElement("div");
  hd.className = "card-hd";
  const left = document.createElement("div");
  left.className = "row";
  const titleBadge = document.createElement("span");
  titleBadge.className = isConcluded ? "badge" : "badge badge-info";
  titleBadge.textContent = isConcluded
    ? "Practice · Concluded"
    : d.cardMode === "faded"
      ? "Practice · Faded Parsons"
      : d.cardMode === "parsons"
        ? "Practice · Parsons"
        : "Practice Problem";
  left.append(titleBadge);
  if (d.problemId) {
    const idTag = document.createElement("span");
    idTag.className = "mono-tag";
    idTag.textContent = d.problemId;
    left.append(idTag);
  }

  const right = document.createElement("div");
  right.className = "row";
  if (isConcluded) {
    const om = OUTCOME_META[d.outcome] || { label: d.outcome || "Concluded", cls: "" };
    const ob = document.createElement("span");
    ob.className = `badge ${om.cls}`;
    ob.textContent = om.label;
    right.append(ob);
    if (d.rung) {
      const rb = document.createElement("span");
      rb.className = "badge";
      rb.textContent = `Hint ${d.rung}`;
      right.append(rb);
    }
    if (d.missClass) {
      const mb = document.createElement("span");
      mb.className = "badge badge-warning";
      mb.textContent = d.missClass;
      right.append(mb);
    }
    if (d.retest) {
      const rt = document.createElement("span");
      rt.className = "badge badge-danger";
      rt.textContent = "Retest queued";
      right.append(rt);
    }
  } else {
    if (state.serverConcluded) {
      const doneTag = document.createElement("span");
      doneTag.className = "badge badge-success";
      doneTag.textContent = "Concluded";
      right.append(doneTag);
    } else {
      const lockTag = document.createElement("span");
      lockTag.className = "subtle";
      lockTag.textContent = "Solution locked server-side";
      right.append(lockTag);
    }
  }
  hd.append(left, right);
  card.append(hd);

  const bd = document.createElement("div");
  bd.className = "card-bd";

  if (d.statement) {
    const stem = document.createElement("div");
    stem.className = "stem";
    stem.innerHTML = renderRichText(d.statement);
    bd.append(stem);
  }

  if (!isConcluded) {
    if (isParsons) {
      bd.append(renderParsonsWorkspace(d));
    }

    const workWrap = document.createElement("div");
    const workLabel = document.createElement("div");
    workLabel.className = "field-label";
    workLabel.innerHTML = isParsons
      ? `<span>Reasoning / notes (optional)</span><span class="subtle">Explain invariant or why a distractor fails</span>`
      : `<span>Your attempt / thinking</span><span class="subtle">Iterate freely — ask hints anytime</span>`;
    const workInput = document.createElement("textarea");
    workInput.className = "input";
    workInput.rows = isParsons ? 2 : 4;
    workInput.placeholder = isParsons
      ? "Optional: note why you excluded a distractor or chose this order…"
      : "Write your setup, intermediate steps, or where you're stuck…";
    workInput.value = state.attemptText;
    workInput.disabled = state.busy;
    workInput.addEventListener("input", () => {
      state.userTouched = true;
      state.attemptText = workInput.value;
      schedulePersist();
    });
    workWrap.append(workLabel, workInput);
    bd.append(workWrap);

    if (state.sentHistory.length > 0) {
      const hist = document.createElement("div");
      hist.className = "subtle";
      hist.textContent = `Sent: ${state.sentHistory.slice(-4).join(" · ")}`;
      bd.append(hist);
    }

    if (!sketchPad) sketchPad = createSketchPad({ title: "Rough work sketch (kept across hints)" });
    bd.append(sketchPad.el);
  } else {
    if (d.verdict) {
      const vBox = document.createElement("div");
      vBox.className = "list-item";
      vBox.innerHTML = `<div class="subtle"><strong>Verdict</strong></div><div class="stem">${renderRichText(d.verdict)}</div>`;
      bd.append(vBox);
    }

    if (Array.isArray(d.canonicalOrder) && d.canonicalOrder.length > 0) {
      const ordBox = document.createElement("div");
      ordBox.className = "list-item";
      ordBox.innerHTML = `<div class="subtle"><strong>Canonical Block Order</strong></div><div class="row">${d.canonicalOrder
        .map((id, i) => `<span class="mono-tag">${i + 1}. ${escapeHtml(id)}</span>`)
        .join("")}</div>`;
      bd.append(ordBox);
    }

    if (d.modelSolution) {
      const solBox = document.createElement("div");
      solBox.className = "list-item";
      solBox.innerHTML = `<div class="row-between"><span class="subtle"><strong>Committed Model Solution</strong></span><span class="badge badge-success">Verbatim</span></div><div class="stem">${renderRichText(d.modelSolution)}</div>`;
      bd.append(solBox);
    }
  }

  if (state.statusMsg) {
    const toast = document.createElement("div");
    toast.className = `toast ${state.statusKind === "success" ? "toast-success" : state.statusKind === "danger" ? "toast-danger" : ""}`;
    toast.textContent = state.statusMsg;
    bd.append(toast);
  }

  card.append(bd);

  if (!hostBlocksMessage) {
    const ft = document.createElement("div");
    ft.className = "card-ft";

    if (!isConcluded) {
      const leftBtns = document.createElement("div");
      leftBtns.className = "row";

      const hintBtn = document.createElement("button");
      hintBtn.type = "button";
      hintBtn.className = "btn btn-warning";
      hintBtn.disabled = state.busy;
      hintBtn.textContent = "Ask Hint";
      hintBtn.addEventListener("click", () => sendPracticeAction("hint"));

      const revealBtn = document.createElement("button");
      revealBtn.type = "button";
      revealBtn.className = "btn btn-ghost";
      revealBtn.disabled = state.busy;
      revealBtn.textContent = "Reveal Solution";
      revealBtn.addEventListener("click", () => sendPracticeAction("reveal"));

      leftBtns.append(hintBtn, revealBtn);

      const submitBtn = document.createElement("button");
      submitBtn.type = "button";
      submitBtn.className = "btn btn-primary";
      submitBtn.disabled = state.busy;
      submitBtn.textContent = state.busy ? "Sending…" : isParsons ? "Submit Order" : "Submit Attempt";
      submitBtn.addEventListener("click", () => sendPracticeAction("attempt"));

      ft.append(leftBtns, submitBtn);
    } else {
      const pauseNote = document.createElement("span");
      pauseNote.className = "subtle";
      pauseNote.textContent = "Ask any follow-up in chat, or continue when ready.";

      const nextBtn = document.createElement("button");
      nextBtn.type = "button";
      nextBtn.className = "btn btn-primary";
      nextBtn.disabled = state.busy;
      nextBtn.textContent = "Next problem →";
      nextBtn.addEventListener("click", () => sendPracticeAction("next"));

      ft.append(pauseNote, nextBtn);
    }

    card.append(ft);
  }

  root.append(card);
}

function loadPayload(sc) {
  if (!sc || typeof sc !== "object") return;
  const prevKey = getProblemKey(state.data);
  const nextKey = getProblemKey(sc);
  const isNewProblem =
    !state.data ||
    prevKey !== nextKey ||
    (state.data.action === "conclude" && sc.action === "pose");
  state.data = sc;
  state.loading = false;
  state.cancelled = false;
  if (isNewProblem) {
    state.attemptText = "";
    state.sentHistory = [];
    state.statusMsg = "";
    state.nextRequested = false;
    state.serverConcluded = sc.action === "conclude";
    state.hydratedKey = "";
    state.hydratingKey = "";
    state.userTouched = false;
    sketchPad = null;
    initParsonsStateFromData(sc);
  }
  render();
  hydrateFromServer();
}

const bridge = bootstrapMcpApp("syntopik-practice-card", {
  onToolInput(args) {
    if (!state.data && args?.statement) {
      state.loading = true;
      render();
    }
  },
  onToolResult(result) {
    if (result?.isError) {
      const msg = result?.content?.[0]?.text || "Practice tool returned an error.";
      state.loading = false;
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
