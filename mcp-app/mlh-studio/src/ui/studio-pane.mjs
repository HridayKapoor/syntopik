/**
 * ui://mlh/studio-pane.html — Live Antigravity aux_pane & global Study Studio
 *
 * 4 crisp tabs:
 *  1. Plan (PlanMarginalia parity): pure-JS SVG Mermaid graph TD/LR DAG renderer with
 *     glowing activeNodeId, completed visitedNodeIds, and click-to-focus (gated on message capability)
 *  2. Graph: searchable proposition & hyperedge chain inspector
 *  3. Flashcards: zero-token multi-cue FSRS-6 reviewer via mlh_app_rate_flashcard
 *  4. Traps: top recurring mistake categories & cue -> wrong -> fix cards
 */

import {
  bootstrapMcpApp,
  escapeHtml,
  renderRichText,
} from "./shared.mjs";

document.body.classList.add("pane-mode");
const root = document.getElementById("app");

const state = {
  tab: "plan",
  workspaceName: "Syntopik",
  plan: { planMermaid: "", activeNodeId: "", visitedNodeIds: [] },
  selectedPlanNode: null,
  propositions: [],
  chains: [],
  flashcards: [],
  mistakes: [],
  topCategories: [],
  chapterTrackers: [],
  activeChapter: null,
  apBusy: false,
  apToast: "",
  apToastKind: "info",
  stats: {
    totalPropositions: 0,
    solid: 0,
    shaky: 0,
    misconception: 0,
    planned: 0,
    totalChains: 0,
    totalMistakes: 0,
    dueCount: 0,
    reviewableDueCount: 0,
    trackersCount: 0,
    activeChapterPct: null,
    activeChapterSlug: null,
  },
  // Graph filters
  graphQuery: "",
  graphStatus: "all",
  expandedPropId: null,
  // Flashcard session state
  fcShowAll: false,
  fcIndex: 0,
  fcRevealed: false,
  fcUnmaskedIds: new Set(),
  fcRatingBusy: false,
  fcToast: "",
  fcToastKind: "info",
  // Trap filter
  trapCategory: "all",
  // Host capabilities
  caps: null,
  connected: false,
  refreshing: false,
  serverStale: false,
};

// --- Pure-JS Mermaid DAG Parser & Layered SVG Renderer ---

function parseMermaidDag(src) {
  const text = String(src || "").trim();
  if (!text) return { direction: "TD", nodes: [], edges: [] };

  let direction = "TD";
  const nodesMap = new Map();
  const edges = [];

  const ensureNode = (id, label) => {
    const cleanId = id.trim();
    if (!cleanId) return;
    const prev = nodesMap.get(cleanId);
    const cleanLabel = label ? label.replace(/^["'`]|["'`]$/g, "").trim() : "";
    if (!prev) {
      nodesMap.set(cleanId, { id: cleanId, label: cleanLabel || cleanId });
    } else if (cleanLabel && prev.label === prev.id) {
      prev.label = cleanLabel;
    }
  };

  const parseNodeToken = (tok) => {
    const s = tok.trim();
    // Matches id[label], id("label"), id((label)), id{label}, or bare id
    const m = /^([A-Za-z0-9_.-]+)\s*(?:\[\[([^\]]+)\]\]|\[([^\]]+)\]|\(\(([^)]+)\)\)|\(([^)]+)\)|\{([^}]+)\})?$/.exec(s);
    if (!m) return null;
    const id = m[1];
    const label = m[2] || m[3] || m[4] || m[5] || m[6] || "";
    ensureNode(id, label);
    return id;
  };

  const lines = text.split(/\r?\n/);
  for (const rawLine of lines) {
    const line = rawLine.replace(/%%.*$/, "").trim();
    if (!line) continue;
    const dirMatch = /^(?:graph|flowchart)\s+(TD|TB|LR|RL|BT)\b/i.exec(line);
    if (dirMatch) {
      direction = dirMatch[1].toUpperCase();
      continue;
    }
    if (/^(?:subgraph|end|classDef|class|style|linkStyle|click)\b/i.test(line)) {
      continue;
    }

    // Split chained edges like A --> B --> C or labeled A -->|why| B
    const edgeRe = /\s*(?:--+>|==+>|-\.-+>|---)\s*(?:\|([^|]*)\|\s*)?/g;
    const parts = [];
    const labels = [];
    let lastIdx = 0;
    let match;
    while ((match = edgeRe.exec(line)) !== null) {
      parts.push(line.slice(lastIdx, match.index).trim());
      labels.push((match[1] || "").trim());
      lastIdx = match.index + match[0].length;
    }
    parts.push(line.slice(lastIdx).trim());

    if (parts.length > 1) {
      for (let i = 0; i < parts.length - 1; i += 1) {
        const fromId = parseNodeToken(parts[i]);
        const toId = parseNodeToken(parts[i + 1]);
        if (fromId && toId) {
          edges.push({ from: fromId, to: toId, label: labels[i] || "" });
        }
      }
    } else {
      parseNodeToken(line);
    }
  }

  return { direction, nodes: [...nodesMap.values()], edges };
}

function renderPlanDagSvg(dag, activeNodeId, visitedSet, selectedId) {
  if (!dag.nodes.length) return "";

  const isLR = dag.direction === "LR" || dag.direction === "RL";
  const inDeg = new Map();
  const adj = new Map();
  for (const n of dag.nodes) {
    inDeg.set(n.id, 0);
    adj.set(n.id, []);
  }
  for (const e of dag.edges) {
    if (adj.has(e.from) && inDeg.has(e.to)) {
      adj.get(e.from).push(e.to);
      inDeg.set(e.to, inDeg.get(e.to) + 1);
    }
  }

  // Longest-path topological layering (safe against cycles)
  const layer = new Map();
  const queue = [];
  for (const n of dag.nodes) {
    if (inDeg.get(n.id) === 0) {
      layer.set(n.id, 0);
      queue.push(n.id);
    }
  }
  const visitedOrder = new Set(queue);
  let head = 0;
  while (head < queue.length) {
    const u = queue[head++];
    const uLayer = layer.get(u) ?? 0;
    for (const v of adj.get(u) || []) {
      const nextL = Math.max(layer.get(v) ?? 0, uLayer + 1);
      layer.set(v, nextL);
      inDeg.set(v, inDeg.get(v) - 1);
      if (inDeg.get(v) <= 0 && !visitedOrder.has(v)) {
        visitedOrder.add(v);
        queue.push(v);
      }
    }
  }
  for (const n of dag.nodes) {
    if (!layer.has(n.id)) layer.set(n.id, 0);
  }

  const buckets = new Map();
  for (const n of dag.nodes) {
    const l = layer.get(n.id);
    if (!buckets.has(l)) buckets.set(l, []);
    buckets.get(l).push(n);
  }
  const layerKeys = [...buckets.keys()].sort((a, b) => a - b);

  const nodeW = 208;
  const nodeH = 54;
  const gapX = isLR ? 56 : 24;
  const gapY = isLR ? 24 : 52;
  const pad = 20;

  const maxBucket = Math.max(...layerKeys.map((k) => buckets.get(k).length));
  const totalW = isLR
    ? pad * 2 + layerKeys.length * nodeW + Math.max(0, layerKeys.length - 1) * gapX
    : pad * 2 + maxBucket * nodeW + Math.max(0, maxBucket - 1) * gapX;
  const totalH = isLR
    ? pad * 2 + maxBucket * nodeH + Math.max(0, maxBucket - 1) * gapY
    : pad * 2 + layerKeys.length * nodeH + Math.max(0, layerKeys.length - 1) * gapY;

  const pos = new Map();
  layerKeys.forEach((lk, li) => {
    const items = buckets.get(lk);
    items.forEach((node, idx) => {
      if (isLR) {
        const colH = items.length * nodeH + (items.length - 1) * gapY;
        const startY = (totalH - colH) / 2;
        pos.set(node.id, {
          x: pad + li * (nodeW + gapX),
          y: startY + idx * (nodeH + gapY),
        });
      } else {
        const rowW = items.length * nodeW + (items.length - 1) * gapX;
        const startX = (totalW - rowW) / 2;
        pos.set(node.id, {
          x: startX + idx * (nodeW + gapX),
          y: pad + li * (nodeH + gapY),
        });
      }
    });
  });

  const edgeSvg = dag.edges
    .map((e) => {
      const a = pos.get(e.from);
      const b = pos.get(e.to);
      if (!a || !b) return "";
      let pathD;
      if (isLR) {
        const x1 = a.x + nodeW;
        const y1 = a.y + nodeH / 2;
        const x2 = b.x;
        const y2 = b.y + nodeH / 2;
        const cx = (x1 + x2) / 2;
        pathD = `M ${x1} ${y1} C ${cx} ${y1}, ${cx} ${y2}, ${x2} ${y2}`;
      } else {
        const x1 = a.x + nodeW / 2;
        const y1 = a.y + nodeH;
        const x2 = b.x + nodeW / 2;
        const y2 = b.y;
        const cy = (y1 + y2) / 2;
        pathD = `M ${x1} ${y1} C ${x1} ${cy}, ${x2} ${cy}, ${x2} ${y2}`;
      }
      const isActiveEdge = e.to === activeNodeId || e.from === activeNodeId;
      const stroke = isActiveEdge ? "var(--mlh-accent)" : "var(--mlh-border-2)";
      return `<path d="${pathD}" fill="none" stroke="${stroke}" stroke-width="${isActiveEdge ? 2 : 1.5}" marker-end="url(#mlh-arrow)"/>`;
    })
    .join("");

  const nodeSvg = dag.nodes
    .map((n) => {
      const p = pos.get(n.id);
      const isActive = n.id === activeNodeId;
      const isVisited = visitedSet.has(n.id) && !isActive;
      const isSelected = n.id === selectedId;
      const fill = isActive
        ? "var(--mlh-bg-info)"
        : isVisited
          ? "var(--mlh-bg-success)"
          : "var(--mlh-bg)";
      const stroke = isActive
        ? "var(--mlh-accent)"
        : isSelected
          ? "var(--mlh-fg)"
          : isVisited
            ? "var(--mlh-border-success)"
            : "var(--mlh-border-2)";
      const strokeW = isActive ? 2.4 : isSelected ? 2 : 1.25;
      // Clean 2-line wrap for node title + truncated monospace slug (zero overflow!)
      const wrapTwoLines = (text, maxChars = 25) => {
        const clean = String(text || "").replace(/\s+/g, " ").trim();
        if (clean.length <= maxChars) return [clean];
        const words = clean.split(" ");
        let l1 = "";
        let i = 0;
        while (i < words.length) {
          const next = l1 ? `${l1} ${words[i]}` : words[i];
          if (next.length <= maxChars) {
            l1 = next;
            i += 1;
          } else if (!l1) {
            l1 = words[i].slice(0, maxChars - 1) + "…";
            i += 1;
            break;
          } else {
            break;
          }
        }
        const rest = words.slice(i).join(" ");
        if (!rest) return [l1];
        const l2 = rest.length > maxChars ? `${rest.slice(0, maxChars - 1)}…` : rest;
        return [l1, l2];
      };
      const titleLines = wrapTwoLines(n.label, 25);
      const shortId = n.id.length > 26 ? `${n.id.slice(0, 25)}…` : n.id;
      const titleSvg =
        titleLines.length === 1
          ? `<text x="${p.x + nodeW / 2}" y="${p.y + 23}" text-anchor="middle" font-size="11.5" font-weight="${isActive ? 700 : 600}" fill="var(--mlh-fg)">${escapeHtml(titleLines[0])}</text>`
          : `<text x="${p.x + nodeW / 2}" y="${p.y + 16}" text-anchor="middle" font-size="11" font-weight="${isActive ? 700 : 600}" fill="var(--mlh-fg)">${escapeHtml(titleLines[0])}</text>
             <text x="${p.x + nodeW / 2}" y="${p.y + 29}" text-anchor="middle" font-size="11" font-weight="${isActive ? 700 : 600}" fill="var(--mlh-fg)">${escapeHtml(titleLines[1])}</text>`;
      const glow = isActive
        ? `<rect x="${p.x - 3}" y="${p.y - 3}" width="${nodeW + 6}" height="${nodeH + 6}" rx="12" fill="none" stroke="var(--mlh-accent)" stroke-opacity="0.35" stroke-width="3"/>`
        : "";
      const checkDot = isVisited
        ? `<circle cx="${p.x + nodeW - 12}" cy="${p.y + 12}" r="6" fill="var(--mlh-fg-success)"/><text x="${p.x + nodeW - 12}" y="${p.y + 15.5}" text-anchor="middle" font-size="9" font-weight="700" fill="#fff">✓</text>`
        : isActive
          ? `<circle cx="${p.x + nodeW - 12}" cy="${p.y + 12}" r="4.5" fill="var(--mlh-accent)"/>`
          : "";
      return `<g class="dag-node" data-node-id="${escapeHtml(n.id)}" style="cursor:pointer">
        ${glow}
        <rect x="${p.x}" y="${p.y}" width="${nodeW}" height="${nodeH}" rx="9" fill="${fill}" stroke="${stroke}" stroke-width="${strokeW}"/>
        ${titleSvg}
        <text x="${p.x + nodeW / 2}" y="${p.y + 44}" text-anchor="middle" font-size="9.5" font-family="var(--mlh-mono)" fill="var(--mlh-fg-3)">${escapeHtml(shortId)}</text>
        ${checkDot}
      </g>`;
    })
    .join("");

  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${totalW} ${totalH}" width="100%" style="max-width:${Math.max(totalW, 340)}px;height:auto;display:block;margin:0 auto">
    <defs>
      <marker id="mlh-arrow" viewBox="0 0 10 10" refX="8.5" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
        <path d="M 0 1.5 L 9 5 L 0 8.5 z" fill="var(--mlh-fg-3)"/>
      </marker>
    </defs>
    ${edgeSvg}
    ${nodeSvg}
  </svg>`;
}

// --- Data refresh & app-only tool actions ---

async function refreshFromServer() {
  if (!state.caps?.canCallTools || state.refreshing) return;
  state.refreshing = true;
  render();
  try {
    const res = await bridge.app.callServerTool({
      name: "synt_app_load_state",
      arguments: { section: "all" },
    });
    if (res?.structuredContent) {
      applyStatePayload(res.structuredContent, false);
    }
  } catch {
    // Non-fatal
  }
  state.refreshing = false;
  render();
}

async function rateActiveFlashcard(card, rating) {
  if (!state.caps?.canCallTools || state.fcRatingBusy) return;
  state.fcRatingBusy = true;
  state.fcToast = "";
  render();

  try {
    const args = { propId: card.propId, rating };
    if (card.cue && card.cue.id && !card.cue.synthetic) {
      args.cueId = card.cue.id;
    }
    const res = await bridge.app.callServerTool({
      name: "synt_app_rate_flashcard",
      arguments: args,
    });
    if (res?.isError) {
      state.fcToast = res?.content?.[0]?.text || "Rating failed.";
      state.fcToastKind = "danger";
    } else if (res?.structuredContent) {
      const sc = res.structuredContent;
      if (sc.stats) state.stats = { ...state.stats, ...sc.stats };
      if (Array.isArray(sc.flashcards)) state.flashcards = sc.flashcards;
      const rev = sc.review || {};
      const bktPct = typeof rev.bktMastery === "number" ? ` · BKT ${Math.round(rev.bktMastery * 100)}%` : "";
      const fireCount = Array.isArray(rev.fireCredits)
        ? rev.fireCredits.length
        : Array.isArray(sc.fireCredits)
          ? sc.fireCredits.length
          : Number(rev.fireRefreshedCount || sc.fireRefreshedCount || 0);
      const fireText =
        fireCount > 0
          ? ` · FIRe refreshed ${fireCount} prerequisite${fireCount === 1 ? "" : "s"}`
          : "";
      state.fcToast = `Rated "${rating}" · next ${rev.nextReview || "updated"}${bktPct}${fireText}`;
      state.fcToastKind = "success";
      state.fcRevealed = false;
      state.fcUnmaskedIds = new Set();
      const deck = getActiveFlashcardDeck();
      if (state.fcIndex >= deck.length) state.fcIndex = 0;
    }
  } catch (err) {
    state.fcToast = `Rating error: ${err?.message || String(err)}`;
    state.fcToastKind = "danger";
  }

  state.fcRatingBusy = false;
  render();
}

async function sendFocusMessage(text) {
  if (!state.caps?.canMessage) return;
  try {
    await bridge.app.sendMessage({
      role: "user",
      content: [{ type: "text", text }],
    });
  } catch {
    // Non-fatal
  }
}

function getActiveFlashcardDeck() {
  if (state.fcShowAll) return state.flashcards;
  const due = state.flashcards.filter((f) => f.isDue);
  return due.length > 0 ? due : state.flashcards;
}

function statusBadge(st) {
  if (st === "solid") return `<span class="badge badge-success">solid</span>`;
  if (st === "shaky") return `<span class="badge badge-warning">shaky</span>`;
  if (st === "misconception") return `<span class="badge badge-danger">misconception</span>`;
  return `<span class="badge">${escapeHtml(st || "planned")}</span>`;
}

// --- Tab Renderers ---

const AP_MILESTONES = [
  ["sourcesIndexed", "Sources Indexed"],
  ["probed", "Frontier Probed"],
  ["theoryComplete", "Theory Complete"],
  ["questionsComplete", "Practice Complete"],
  ["revisionSheetBuilt", "Revision Sheet"],
  ["cbtMockBuilt", "Assessment Built"],
  ["cbtMockPassed", "Assessment Passed"],
  ["audioBriefBuilt", "Audio Brief"],
];

function topicStatusBadge(status) {
  if (status === "mastered") return `<span class="badge badge-success">mastered</span>`;
  if (status === "in_progress") return `<span class="badge badge-info">in progress</span>`;
  if (status === "probed") return `<span class="badge badge-warning">probed</span>`;
  return `<span class="badge">pending</span>`;
}

function levelBadge(level) {
  const lv = String(level || "basics").toLowerCase();
  if (lv === "advanced") return `<span class="badge badge-danger">adv</span>`;
  if (lv === "main") return `<span class="badge badge-warning">main</span>`;
  return `<span class="badge badge-info">basics</span>`;
}

async function triggerContinueAutopilot() {
  if (!state.caps?.canMessage || state.apBusy) return;
  state.apBusy = true;
  state.apToast = "";
  render();
  try {
    const res = await bridge.app.sendMessage({
      role: "user",
      content: [{ type: "text", text: "Continue" }],
    });
    if (res?.isError) {
      state.apToast = "Host declined sending message.";
      state.apToastKind = "danger";
    } else {
      state.apToast = 'Sent "Continue" to chat — advancing next step ✓';
      state.apToastKind = "success";
    }
  } catch (err) {
    state.apToast = `Send failed: ${err?.message || String(err)}`;
    state.apToastKind = "danger";
  }
  state.apBusy = false;
  render();
}

function renderAutopilotTab(container) {
  const tracker = state.activeChapter || state.chapterTrackers.find((t) => t.active) || null;

  if (state.apToast) {
    const toast = document.createElement("div");
    toast.className = `toast ${state.apToastKind === "success" ? "toast-success" : state.apToastKind === "danger" ? "toast-danger" : ""}`;
    toast.textContent = state.apToast;
    container.append(toast);
  }

  if (state.serverStale) {
    const warn = document.createElement("div");
    warn.className = "toast toast-danger";
    warn.textContent =
      "Updated Syntopik Studio server detected on disk — toggle the plugin Off → On in Plugins Manager (or restart your harness) to load Autopilot & persistent card state.";
    container.append(warn);
  }

  if (!tracker) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.innerHTML = `<div style="font-weight:600;margin-bottom:5px">No Active Autopilot Unit</div><div class="muted">Type <code>Start &lt;Topic, Course, or Chapter&gt;</code> in chat to initialize guided mastery across CS, ML, Math, Physics, Chem, or any domain.</div>`;
    container.append(empty);

    if (state.chapterTrackers.length > 0) {
      const box = document.createElement("div");
      box.className = "list-item";
      box.innerHTML = `<strong style="font-size:12px">Saved Chapter Trackers (${state.chapterTrackers.length})</strong>` +
        state.chapterTrackers
          .map((t) => `<div class="row-between"><span>${escapeHtml(t.title)} <span class="subtle">(${escapeHtml(t.subject)})</span></span><span class="badge">${t.pct}%</span></div>`)
          .join("");
      container.append(box);
    }
    return;
  }

  // Header & Primary Action
  const headCard = document.createElement("div");
  headCard.className = "list-item";
  const topMeta = document.createElement("div");
  topMeta.className = "row-between";
  topMeta.innerHTML = `
    <div class="row">
      <span class="badge badge-info">${escapeHtml(tracker.subject || "General")}</span>
      <span class="badge badge-success">${tracker.pct ?? 0}%</span>
      ${tracker.doubtCount > 0 ? `<span class="badge badge-warning">${tracker.doubtCount} doubt${tracker.doubtCount === 1 ? "" : "s"}</span>` : ""}
    </div>
    <span class="mono-tag">${escapeHtml(tracker.slug)}</span>
  `;
  headCard.append(topMeta);

  const titleEl = document.createElement("div");
  titleEl.style.fontSize = "14.5px";
  titleEl.style.fontWeight = "700";
  titleEl.textContent = tracker.title || tracker.slug;
  headCard.append(titleEl);

  if (tracker.nextHint) {
    const nextBox = document.createElement("div");
    nextBox.className = "ap-next-box";
    nextBox.innerHTML = `<strong>Next:</strong> ${escapeHtml(tracker.nextHint)}`;
    headCard.append(nextBox);
  }

  if (state.caps?.canMessage) {
    const contBtn = document.createElement("button");
    contBtn.type = "button";
    contBtn.className = "btn btn-primary btn-full";
    contBtn.disabled = state.apBusy;
    contBtn.textContent = state.apBusy ? "Sending…" : "Continue Next Step →";
    contBtn.addEventListener("click", triggerContinueAutopilot);
    headCard.append(contBtn);
  } else {
    const hint = document.createElement("div");
    hint.className = "subtle";
    hint.innerHTML = `Type <code>Continue</code> in chat to advance to the next step.`;
    headCard.append(hint);
  }
  container.append(headCard);

  // Topics Progress & Per-Topic Pills
  const tTot = Number(tracker.topicsTotal) || 0;
  const tMastered = Number(tracker.topicsMastered) || 0;
  const tPct = tTot > 0 ? Math.round((tMastered / tTot) * 100) : 0;
  const topicsCard = document.createElement("div");
  topicsCard.className = "list-item";
  topicsCard.innerHTML = `
    <div class="row-between">
      <strong style="font-size:12.5px">Topics Progress</strong>
      <span class="subtle">${tMastered}/${tTot} mastered (${tPct}%)</span>
    </div>
    <div class="ap-seg-track">
      <span class="ap-seg-fill ap-seg-success" style="width:${tPct}%"></span>
    </div>
  `;
  const topicsArr = Array.isArray(tracker.topics) ? tracker.topics : [];
  if (topicsArr.length > 0) {
    const tList = document.createElement("div");
    tList.className = "ap-topic-list";
    for (const t of topicsArr.slice(0, 12)) {
      const row = document.createElement("div");
      row.className = "ap-topic-row";
      row.innerHTML = `
        <span class="ap-topic-name" title="${escapeHtml(t.title || t.id)}">${escapeHtml(t.title || t.id)}</span>
        <div class="row" style="flex-shrink:0;gap:4px;">
          ${levelBadge(t.level)}
          ${topicStatusBadge(t.status)}
        </div>
      `;
      tList.append(row);
    }
    topicsCard.append(tList);
  } else {
    const note = document.createElement("div");
    note.className = "subtle";
    note.textContent = "Topics will populate automatically after source indexing.";
    topicsCard.append(note);
  }
  container.append(topicsCard);

  // Question Ledger Progress
  const qTot = Number(tracker.questionsTotal) || 0;
  const qSolved = Number(tracker.questionsSolved) || 0;
  const qUnseen = Number(tracker.questionsUnseen) || 0;
  const qRetest = Number(tracker.questionsRetest) || 0;
  const qSolvedPct = qTot > 0 ? (qSolved / qTot) * 100 : 0;
  const qRetestPct = qTot > 0 ? (qRetest / qTot) * 100 : 0;

  const qCard = document.createElement("div");
  qCard.className = "list-item";
  qCard.innerHTML = `
    <div class="row-between">
      <strong style="font-size:12.5px">Question Ledger</strong>
      <span class="subtle">${qSolved}/${qTot} solved</span>
    </div>
    <div class="ap-seg-track">
      <span class="ap-seg-fill ap-seg-success" style="width:${qSolvedPct}%"></span>
      ${qRetest > 0 ? `<span class="ap-seg-fill ap-seg-warning" style="width:${qRetestPct}%"></span>` : ""}
    </div>
    <div class="row">
      <span class="badge badge-success">${qSolved} solved</span>
      <span class="badge">${qUnseen} unseen</span>
      <span class="badge${qRetest > 0 ? " badge-warning" : ""}">${qRetest} retest</span>
    </div>
  `;
  container.append(qCard);

  // Milestones Checklist
  const msObj = tracker.milestones && typeof tracker.milestones === "object" ? tracker.milestones : {};
  const msDone = AP_MILESTONES.filter(([k]) => Boolean(msObj[k])).length;
  const msCard = document.createElement("div");
  msCard.className = "list-item";
  msCard.innerHTML = `
    <div class="row-between">
      <strong style="font-size:12.5px">Milestones</strong>
      <span class="subtle">${msDone}/${AP_MILESTONES.length} complete</span>
    </div>
    <div class="ap-milestones-grid">
      ${AP_MILESTONES.map(([k, label]) => {
        const done = Boolean(msObj[k]);
        return `<div class="ap-ms-item${done ? " done" : ""}"><span>${done ? "✓" : "○"}</span><span>${escapeHtml(label)}</span></div>`;
      }).join("")}
    </div>
  `;
  container.append(msCard);
}

function buildDagFromTracker(tracker) {
  if (!tracker || !Array.isArray(tracker.topics) || tracker.topics.length === 0) return null;
  const nodes = tracker.topics.map((t) => ({ id: t.id, label: t.title || t.id }));
  const idSet = new Set(nodes.map((n) => n.id));
  const edges = [];
  for (const t of tracker.topics) {
    const prereqs = Array.isArray(t.prereqTopicIds) ? t.prereqTopicIds : [];
    for (const p of prereqs) {
      if (idSet.has(p)) edges.push({ from: p, to: t.id, label: "" });
    }
  }
  const visitedIds = tracker.topics.filter((t) => t.status === "mastered").map((t) => t.id);
  const inProg = tracker.topics.find((t) => t.status === "in_progress");
  const nextReady = tracker.topics.find((t) => t.status !== "mastered");
  const activeId = inProg ? inProg.id : nextReady ? nextReady.id : "";
  return { dag: { direction: "TD", nodes, edges }, activeId, visitedIds };
}

function renderPlanTab(container) {
  const mermaid = state.plan?.planMermaid || "";
  let dag;
  let activeNodeId = state.plan?.activeNodeId || "";
  let visitedSet = new Set(state.plan?.visitedNodeIds || []);

  if (mermaid) {
    dag = parseMermaidDag(mermaid);
  } else {
    const synthesized = buildDagFromTracker(state.activeChapter);
    if (synthesized) {
      dag = synthesized.dag;
      activeNodeId = synthesized.activeId;
      visitedSet = new Set(synthesized.visitedIds);
    }
  }

  if (!dag || !dag.nodes.length) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.innerHTML = `<div style="font-weight:600;margin-bottom:4px">No active lesson plan yet</div><div class="muted">When a lesson begins, the dependency DAG renders live here with your active station highlighted.</div>`;
    container.append(empty);
    return;
  }

  const legend = document.createElement("div");
  legend.className = "row-between";
  legend.innerHTML = `<span class="muted"><strong>Lesson Dependency DAG</strong> (${dag.nodes.length} nodes)</span><div class="row"><span class="badge badge-info">Active</span><span class="badge badge-success">Visited</span></div>`;
  container.append(legend);

  const frame = document.createElement("div");
  frame.className = "dag-frame";
  frame.innerHTML = renderPlanDagSvg(dag, activeNodeId, visitedSet, state.selectedPlanNode);
  frame.querySelectorAll(".dag-node").forEach((g) => {
    g.addEventListener("click", () => {
      state.selectedPlanNode = g.getAttribute("data-node-id");
      render();
    });
  });
  container.append(frame);

  const focusId = state.selectedPlanNode || activeNodeId;
  const focusNode = dag.nodes.find((n) => n.id === focusId);
  if (focusNode) {
    const bar = document.createElement("div");
    bar.className = "list-item";
    const top = document.createElement("div");
    top.className = "row-between";
    top.innerHTML = `<div><strong>${escapeHtml(focusNode.label)}</strong> <span class="mono-tag">${escapeHtml(focusNode.id)}</span></div>`;

    if (state.caps?.canMessage) {
      const askBtn = document.createElement("button");
      askBtn.type = "button";
      askBtn.className = "btn btn-sm btn-primary";
      askBtn.textContent = "Focus node in chat";
      askBtn.addEventListener("click", () => {
        sendFocusMessage(`Let's focus on lesson node \`${focusNode.id}\` (${focusNode.label}).`);
      });
      top.append(askBtn);
    }
    bar.append(top);
    container.append(bar);
  }
}

function renderGraphTab(container) {
  const s = state.stats;
  const summary = document.createElement("div");
  summary.className = "row";
  summary.innerHTML = `
    <span class="badge">${s.totalPropositions} nodes</span>
    <span class="badge badge-success">${s.solid || 0} solid</span>
    <span class="badge badge-warning">${s.shaky || 0} shaky</span>
    <span class="badge badge-danger">${s.misconception || 0} misconception</span>
    <span class="badge">${s.totalChains} chains</span>
  `;
  container.append(summary);

  const controls = document.createElement("div");
  controls.className = "row";
  const search = document.createElement("input");
  search.type = "search";
  search.className = "input";
  search.style.flex = "1 1 160px";
  search.placeholder = "Filter propositions by title, id, chapter…";
  search.value = state.graphQuery;
  search.addEventListener("input", () => {
    state.graphQuery = search.value;
    renderGraphList(listWrap);
  });
  controls.append(search);

  for (const st of ["all", "solid", "shaky", "misconception", "planned"]) {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = `btn btn-sm${state.graphStatus === st ? " btn-primary" : ""}`;
    chip.textContent = st === "all" ? "All" : st;
    chip.addEventListener("click", () => {
      state.graphStatus = st;
      render();
    });
    controls.append(chip);
  }
  container.append(controls);

  const listWrap = document.createElement("div");
  listWrap.style.display = "flex";
  listWrap.style.flexDirection = "column";
  listWrap.style.gap = "8px";
  container.append(listWrap);
  renderGraphList(listWrap);
}

function renderGraphList(listWrap) {
  listWrap.innerHTML = "";
  const q = state.graphQuery.trim().toLowerCase();
  const filtered = state.propositions.filter((p) => {
    if (state.graphStatus !== "all" && (p.status || "planned") !== state.graphStatus) return false;
    if (!q) return true;
    const hay = `${p.id || ""} ${p.title || ""} ${p.subject || ""} ${p.chapter || ""} ${p.notes || ""}`.toLowerCase();
    return hay.includes(q);
  });

  if (filtered.length === 0) {
    listWrap.innerHTML = `<div class="empty-state"><div style="font-weight:600;margin-bottom:4px">No propositions match</div><div class="muted">${state.propositions.length === 0 ? "Your Knowledge Graph starts clean and grows as you study chapters." : "Try clearing the filter."}</div></div>`;
    return;
  }

  for (const p of filtered.slice(0, 80)) {
    const item = document.createElement("div");
    item.className = "list-item";
    const bkt = typeof p.bktMastery === "number" ? `${Math.round(p.bktMastery * 100)}% BKT` : "";
    const top = document.createElement("div");
    top.className = "row-between";
    top.style.cursor = "pointer";
    top.innerHTML = `
      <div class="row">
        ${statusBadge(p.status)}
        <strong>${escapeHtml(p.title || p.id)}</strong>
      </div>
      <div class="row">
        <span class="mono-tag">${escapeHtml(p.id)}</span>
        ${bkt ? `<span class="subtle">${bkt}</span>` : ""}
      </div>
    `;
    top.addEventListener("click", () => {
      state.expandedPropId = state.expandedPropId === p.id ? null : p.id;
      renderGraphList(listWrap);
    });
    item.append(top);

    const meta = document.createElement("div");
    meta.className = "subtle";
    meta.textContent = [p.subject, p.chapter, p.nextReview ? `next: ${p.nextReview}` : ""].filter(Boolean).join(" · ");
    item.append(meta);

    if (state.expandedPropId === p.id) {
      if (p.notes) {
        const notes = document.createElement("div");
        notes.className = "stem";
        notes.innerHTML = renderRichText(p.notes);
        item.append(notes);
      }
      const relatedChains = state.chains.filter(
        (c) => c.conclusion === p.id || (Array.isArray(c.premises) && c.premises.includes(p.id))
      );
      if (relatedChains.length > 0) {
        const cWrap = document.createElement("div");
        cWrap.className = "subtle";
        cWrap.innerHTML = relatedChains
          .map((c) => `<div>• <code>${escapeHtml(c.premises.join(" ∧ "))}</code> → <code>${escapeHtml(c.conclusion)}</code> (${escapeHtml(c.relation)})</div>`)
          .join("");
        item.append(cWrap);
      }
      if (state.caps?.canMessage) {
        const qBtn = document.createElement("button");
        qBtn.type = "button";
        qBtn.className = "btn btn-sm btn-primary";
        qBtn.style.alignSelf = "flex-start";
        qBtn.textContent = "Quiz me on this";
        qBtn.addEventListener("click", () => {
          sendFocusMessage(`Quiz me on proposition \`${p.id}\` (${p.title || p.id}).`);
        });
        item.append(qBtn);
      }
    }
    listWrap.append(item);
  }
}

function extractOcclusionSpec(card) {
  if (!card || typeof card !== "object") return null;
  const media = card.cue?.media || card.media || null;
  const rawOcc =
    card.cue?.occlusion ||
    media?.occlusion ||
    card.occlusion ||
    (Array.isArray(media?.masks) ? media : null);
  if (!rawOcc) return null;

  let masks = [];
  let src = media?.src || "";
  let dataUri = media?.dataUri || "";
  let width = Number(media?.width || 0);
  let height = Number(media?.height || 0);
  let mode = media?.mode || "hide_all";

  if (Array.isArray(rawOcc)) {
    masks = rawOcc;
  } else if (typeof rawOcc === "object") {
    masks = Array.isArray(rawOcc.masks) ? rawOcc.masks : [];
    if (rawOcc.src) src = rawOcc.src;
    if (rawOcc.dataUri) dataUri = rawOcc.dataUri;
    if (rawOcc.width) width = Number(rawOcc.width);
    if (rawOcc.height) height = Number(rawOcc.height);
    if (rawOcc.mode) mode = rawOcc.mode;
  }

  const normalizedMasks = masks
    .filter((m) => m && typeof m === "object")
    .map((m, idx) => {
      const bbox = Array.isArray(m.bbox) && m.bbox.length >= 4
        ? m.bbox.map((v) => Number(v) || 0)
        : [Number(m.x) || 0, Number(m.y) || 0, Number(m.w ?? m.width) || 0, Number(m.h ?? m.height) || 0];
      return {
        id: String(m.id || `m${idx + 1}`),
        bbox,
        label: String(m.label || m.answer || m.text || m.id || `Region ${idx + 1}`),
        propId: m.propId ? String(m.propId) : null,
      };
    })
    .filter((m) => m.bbox[2] > 0 && m.bbox[3] > 0);

  if (normalizedMasks.length === 0) return null;
  return {
    src,
    dataUri,
    width: width > 0 ? width : 640,
    height: height > 0 ? height : 360,
    mode,
    masks: normalizedMasks,
  };
}

function renderOcclusionWidget(occSpec) {
  const wrap = document.createElement("div");
  wrap.className = "io-stage";

  const W = occSpec.width;
  const H = occSpec.height;
  const allNormalized = occSpec.masks.every(
    ({ bbox: [x, y, w, h] }) => x >= 0 && y >= 0 && x + w <= 1.05 && y + h <= 1.05
  );

  const revealedCount = occSpec.masks.filter(
    (m) => state.fcRevealed || state.fcUnmaskedIds.has(m.id)
  ).length;

  const bar = document.createElement("div");
  bar.className = "io-toolbar";
  const info = document.createElement("span");
  info.className = "subtle";
  info.textContent = `Image Occlusion · ${revealedCount}/${occSpec.masks.length} revealed (click masks to inspect)`;

  const toggleAllBtn = document.createElement("button");
  toggleAllBtn.type = "button";
  toggleAllBtn.className = "btn btn-sm";
  const allOpen = revealedCount === occSpec.masks.length;
  toggleAllBtn.textContent = allOpen ? "Re-mask" : "Reveal all";
  toggleAllBtn.addEventListener("click", () => {
    if (allOpen) {
      state.fcUnmaskedIds = new Set();
    } else {
      state.fcUnmaskedIds = new Set(occSpec.masks.map((m) => m.id));
    }
    render();
  });
  bar.append(info, toggleAllBtn);
  wrap.append(bar);

  const hasBgImage = Boolean(occSpec.dataUri);
  const svgParts = [];
  if (hasBgImage) {
    svgParts.push(
      `<image href="${escapeHtml(occSpec.dataUri)}" x="0" y="0" width="${W}" height="${H}" preserveAspectRatio="xMidYMid meet" />`
    );
  } else {
    svgParts.push(
      `<rect x="1" y="1" width="${W - 2}" height="${H - 2}" rx="8" fill="var(--mlh-bg)" stroke="var(--mlh-border)" stroke-width="1.5"/>`
    );
    if (occSpec.src) {
      svgParts.push(
        `<text x="14" y="22" font-size="11" font-family="var(--mlh-mono)" fill="var(--mlh-fg-3)">${escapeHtml(occSpec.src)}</text>`
      );
    }
  }

  for (let i = 0; i < occSpec.masks.length; i += 1) {
    const m = occSpec.masks[i];
    const [rx, ry, rw, rh] = allNormalized
      ? [m.bbox[0] * W, m.bbox[1] * H, m.bbox[2] * W, m.bbox[3] * H]
      : m.bbox;
    const isRevealed = state.fcRevealed || state.fcUnmaskedIds.has(m.id);
    const shortLabel = m.label.length > 26 ? `${m.label.slice(0, 25)}…` : m.label;
    const maskCls = `io-mask-group${isRevealed ? " io-mask-revealed" : ""}${hasBgImage ? " io-mask-over-img" : " io-mask-no-img"}`;
    const maskText = !isRevealed || !hasBgImage
      ? `<text x="${rx + rw / 2}" y="${ry + rh / 2 + 4}" text-anchor="middle" font-size="12" font-weight="600" fill="${isRevealed ? "var(--mlh-fg-success)" : "var(--mlh-fg-inv)"}">${escapeHtml(isRevealed ? shortLabel : `${i + 1} · ?`)}</text>`
      : "";
    svgParts.push(
      `<g class="${maskCls}" data-mask-id="${escapeHtml(m.id)}" role="button" tabindex="0" aria-label="${escapeHtml(isRevealed ? `Revealed: ${m.label}` : `Reveal occluded region ${i + 1}`)}">` +
        `<rect class="io-mask-rect" x="${rx}" y="${ry}" width="${rw}" height="${rh}" rx="6"/>` +
        maskText +
        `</g>`
    );
  }

  const svgBox = document.createElement("div");
  svgBox.innerHTML = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${W} ${H}" width="100%" style="height:auto;display:block;border-radius:7px">${svgParts.join("")}</svg>`;

  const toggleMask = (maskId) => {
    if (state.fcUnmaskedIds.has(maskId)) state.fcUnmaskedIds.delete(maskId);
    else state.fcUnmaskedIds.add(maskId);
    render();
  };
  svgBox.querySelectorAll(".io-mask-group").forEach((g) => {
    const mid = g.getAttribute("data-mask-id");
    g.addEventListener("click", () => toggleMask(mid));
    g.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter" || ev.key === " ") {
        ev.preventDefault();
        toggleMask(mid);
      }
    });
  });
  wrap.append(svgBox);

  const revealedMasks = occSpec.masks.filter(
    (m) => state.fcRevealed || state.fcUnmaskedIds.has(m.id)
  );
  if (revealedMasks.length > 0) {
    const legend = document.createElement("div");
    legend.className = "row";
    for (const m of revealedMasks) {
      const pill = document.createElement("span");
      pill.className = "badge badge-success";
      pill.textContent = `${m.id}: ${m.label}`;
      legend.append(pill);
    }
    wrap.append(legend);
  }

  return wrap;
}

function renderFlashcardsTab(container) {
  const deck = getActiveFlashcardDeck();
  const topBar = document.createElement("div");
  topBar.className = "row-between";

  const leftInfo = document.createElement("span");
  leftInfo.className = "muted";
  leftInfo.textContent =
    deck.length > 0
      ? `Card ${Math.min(state.fcIndex + 1, deck.length)} of ${deck.length} · Zero-token FSRS-6`
      : "Zero-token FSRS-6 Reviewer";

  const modeBtn = document.createElement("button");
  modeBtn.type = "button";
  modeBtn.className = "btn btn-sm";
  modeBtn.textContent = state.fcShowAll ? "Showing: All" : "Showing: Due & Weak";
  modeBtn.addEventListener("click", () => {
    state.fcShowAll = !state.fcShowAll;
    state.fcIndex = 0;
    state.fcRevealed = false;
    state.fcUnmaskedIds = new Set();
    render();
  });
  topBar.append(leftInfo, modeBtn);
  container.append(topBar);

  if (state.fcToast) {
    const toast = document.createElement("div");
    toast.className = `toast ${state.fcToastKind === "success" ? "toast-success" : state.fcToastKind === "danger" ? "toast-danger" : ""}`;
    toast.textContent = state.fcToast;
    container.append(toast);
  }

  if (deck.length === 0) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.innerHTML = `<div style="font-weight:600;margin-bottom:4px">No flashcards ready yet</div><div class="muted">Propositions saved to your Knowledge Graph appear here automatically for zero-token spaced recall.</div>`;
    container.append(empty);
    return;
  }

  const card = deck[state.fcIndex % deck.length];
  const fc = document.createElement("div");
  fc.className = "fc-card";

  const meta = document.createElement("div");
  meta.className = "row-between";
  const cueBadge =
    card.cue && card.cue.totalCues > 1
      ? `<span class="badge badge-info">Cue ${card.cue.index + 1}/${card.cue.totalCues}${card.cue.lastRating ? ` · ${escapeHtml(card.cue.lastRating)}` : ""}</span>`
      : "";
  meta.innerHTML = `<div class="row">${statusBadge(card.status)}<span class="subtle">${escapeHtml(card.subject)} · ${escapeHtml(card.chapter)}</span></div><div class="row">${cueBadge}<span class="mono-tag">${escapeHtml(card.propId)}</span></div>`;
  fc.append(meta);

  const qEl = document.createElement("div");
  qEl.className = "stem";
  qEl.innerHTML = renderRichText(card.cue?.q || card.title);
  fc.append(qEl);

  const occSpec = extractOcclusionSpec(card);
  if (occSpec) {
    fc.append(renderOcclusionWidget(occSpec));
  }

  if (!state.fcRevealed) {
    const flipBtn = document.createElement("button");
    flipBtn.type = "button";
    flipBtn.className = "btn btn-primary btn-full";
    flipBtn.textContent = "Show Answer";
    flipBtn.addEventListener("click", () => {
      state.fcRevealed = true;
      render();
    });
    fc.append(flipBtn);
  } else {
    const hr = document.createElement("hr");
    hr.className = "fc-divider";
    fc.append(hr);

    const aEl = document.createElement("div");
    aEl.className = "stem";
    aEl.innerHTML = renderRichText(card.cue?.a || card.title);
    fc.append(aEl);

    if (card.notes && !(card.cue?.a || "").includes(card.notes)) {
      const noteEl = document.createElement("div");
      noteEl.className = "subtle";
      noteEl.innerHTML = `<strong>Notes / Traps:</strong> ${renderRichText(card.notes)}`;
      fc.append(noteEl);
    }

    const rateGrid = document.createElement("div");
    rateGrid.className = "rating-grid";
    const ratings = [
      { id: "again", label: "Again", cls: "btn-danger" },
      { id: "hard", label: "Hard", cls: "btn-warning" },
      { id: "good", label: "Good", cls: "btn-success" },
      { id: "easy", label: "Easy", cls: "btn-info" },
    ];
    for (const r of ratings) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = `btn ${r.cls}`;
      btn.disabled = state.fcRatingBusy || (state.connected && !state.caps?.canCallTools);
      btn.textContent = r.label;
      btn.addEventListener("click", () => {
        if (state.caps?.canCallTools) {
          rateActiveFlashcard(card, r.id);
        } else {
          state.fcToast = `Rated "${r.label}" locally (standalone preview).`;
          state.fcToastKind = "info";
          state.fcRevealed = false;
          state.fcUnmaskedIds = new Set();
          state.fcIndex = (state.fcIndex + 1) % deck.length;
          render();
        }
      });
      rateGrid.append(btn);
    }
    fc.append(rateGrid);
  }

  container.append(fc);

  if (deck.length > 1) {
    const nav = document.createElement("div");
    nav.className = "row-between";
    const prev = document.createElement("button");
    prev.type = "button";
    prev.className = "btn btn-sm";
    prev.textContent = "← Prev";
    prev.addEventListener("click", () => {
      state.fcIndex = (state.fcIndex - 1 + deck.length) % deck.length;
      state.fcRevealed = false;
      state.fcUnmaskedIds = new Set();
      render();
    });
    const next = document.createElement("button");
    next.type = "button";
    next.className = "btn btn-sm";
    next.textContent = "Skip →";
    next.addEventListener("click", () => {
      state.fcIndex = (state.fcIndex + 1) % deck.length;
      state.fcRevealed = false;
      state.fcUnmaskedIds = new Set();
      render();
    });
    nav.append(prev, next);
    container.append(nav);
  }
}

function renderTrapsTab(container) {
  if (state.mistakes.length === 0) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.innerHTML = `<div style="font-weight:600;margin-bottom:4px">No mistake traps logged yet</div><div class="muted">Recurring errors from drills, handwritten checks, and timed assessments appear here as cue → fix cards.</div>`;
    container.append(empty);
    return;
  }

  // Category distribution bars
  if (state.topCategories.length > 0) {
    const chartBox = document.createElement("div");
    chartBox.className = "list-item";
    const maxCount = Math.max(1, ...state.topCategories.map((c) => c.count));
    chartBox.innerHTML = `<div class="row-between"><strong style="font-size:12.5px">Top Recurring Categories</strong><span class="subtle">${state.mistakes.length} logged</span></div>`;
    for (const c of state.topCategories.slice(0, 6)) {
      const pct = Math.round((c.count / maxCount) * 100);
      const row = document.createElement("div");
      row.className = "bar-row";
      row.style.cursor = "pointer";
      row.innerHTML = `<span>${escapeHtml(c.category)}</span><div class="bar-track"><div class="bar-fill" style="width:${pct}%"></div></div><span class="subtle" style="text-align:right">${c.count}</span>`;
      row.addEventListener("click", () => {
        state.trapCategory = state.trapCategory === c.category ? "all" : c.category;
        render();
      });
      chartBox.append(row);
    }
    container.append(chartBox);
  }

  if (state.trapCategory !== "all") {
    const filterRow = document.createElement("div");
    filterRow.className = "row-between";
    filterRow.innerHTML = `<span class="subtle">Filtered by: <strong>${escapeHtml(state.trapCategory)}</strong></span>`;
    const clr = document.createElement("button");
    clr.type = "button";
    clr.className = "btn btn-sm";
    clr.textContent = "Show all";
    clr.addEventListener("click", () => {
      state.trapCategory = "all";
      render();
    });
    filterRow.append(clr);
    container.append(filterRow);
  }

  const shown =
    state.trapCategory === "all"
      ? state.mistakes
      : state.mistakes.filter((m) => m.category === state.trapCategory);

  for (const m of shown.slice(0, 50)) {
    const item = document.createElement("div");
    item.className = "list-item";
    item.innerHTML = `
      <div class="row-between">
        <span class="badge badge-danger">${escapeHtml(m.category || "trap")}</span>
        <span class="subtle">${escapeHtml([m.subject, m.chapter, m.date].filter(Boolean).join(" · "))}</span>
      </div>
      ${m.triggerCue ? `<div class="muted"><strong>Cue:</strong> ${renderRichText(m.triggerCue)}</div>` : ""}
      ${m.wrongMove ? `<div class="muted" style="color:var(--mlh-fg-danger)"><strong>Trap:</strong> ${renderRichText(m.wrongMove)}</div>` : ""}
      ${m.correctMove ? `<div class="muted" style="color:var(--mlh-fg-success)"><strong>Fix:</strong> ${renderRichText(m.correctMove)}</div>` : ""}
    `;
    container.append(item);
  }
}

function render() {
  root.innerHTML = "";
  const shell = document.createElement("div");
  shell.className = "studio-shell";

  // Top header
  const topRow = document.createElement("div");
  topRow.className = "row-between";
  const titleEl = document.createElement("div");
  titleEl.className = "row";
  titleEl.innerHTML = `<strong style="font-size:14px">Syntopik Studio</strong><span class="subtle">${escapeHtml(state.workspaceName)}</span>`;

  const actions = document.createElement("div");
  actions.className = "row";
  if (state.caps?.canCallTools) {
    const refBtn = document.createElement("button");
    refBtn.type = "button";
    refBtn.className = "btn btn-sm";
    refBtn.disabled = state.refreshing;
    refBtn.textContent = state.refreshing ? "Syncing…" : "↻ Refresh";
    refBtn.addEventListener("click", refreshFromServer);
    actions.append(refBtn);
  }
  topRow.append(titleEl, actions);
  shell.append(topRow);

  // 5-tab bar
  const tabs = [
    { id: "autopilot", label: "Autopilot", count: state.activeChapter ? `${state.activeChapter.pct}%` : null },
    { id: "plan", label: "Plan", count: null },
    { id: "graph", label: "Graph", count: state.stats.totalPropositions || null },
    { id: "flashcards", label: "Flashcards", count: state.stats.reviewableDueCount || null },
    { id: "traps", label: "Traps", count: state.stats.totalMistakes || null },
  ];
  const tabBar = document.createElement("div");
  tabBar.className = "tab-bar";
  for (const t of tabs) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = `tab-btn${state.tab === t.id ? " active" : ""}`;
    btn.innerHTML = `<span>${t.label}</span>${t.count ? `<span class="tab-count">${escapeHtml(String(t.count))}</span>` : ""}`;
    btn.addEventListener("click", () => {
      state.tab = t.id;
      render();
      if (state.connected && state.caps?.canCallTools) {
        bridge.app
          .callServerTool({
            name: "synt_app_card_state",
            arguments: { op: "save", kind: "studio", id: "default", state: { tab: state.tab } },
          })
          .catch(() => {});
      }
    });
    tabBar.append(btn);
  }
  shell.append(tabBar);

  // Active tab pane
  const pane = document.createElement("div");
  pane.style.display = "flex";
  pane.style.flexDirection = "column";
  pane.style.gap = "10px";

  if (state.tab === "autopilot") renderAutopilotTab(pane);
  else if (state.tab === "plan") renderPlanTab(pane);
  else if (state.tab === "graph") renderGraphTab(pane);
  else if (state.tab === "flashcards") renderFlashcardsTab(pane);
  else renderTrapsTab(pane);

  shell.append(pane);
  root.append(shell);
}

function applyStatePayload(sc, allowTabSwitch = true) {
  if (!sc || typeof sc !== "object") return;
  if (
    sc.kind === "mlh_app_state" ||
    sc.kind === "mlh_lesson_studio" ||
    sc.kind === "synt_app_state" ||
    sc.kind === "synt_lesson_studio"
  ) {
    state.serverStale = !Array.isArray(sc.chapterTrackers);
  }
  if (allowTabSwitch && typeof sc.tab === "string") state.tab = sc.tab;
  if (typeof sc.workspaceName === "string") state.workspaceName = sc.workspaceName;
  if (sc.plan && typeof sc.plan === "object") {
    state.plan = {
      planMermaid: sc.plan.planMermaid || "",
      activeNodeId: sc.plan.activeNodeId || "",
      visitedNodeIds: Array.isArray(sc.plan.visitedNodeIds) ? sc.plan.visitedNodeIds : [],
    };
  }
  if (Array.isArray(sc.chapterTrackers)) state.chapterTrackers = sc.chapterTrackers;
  if ("activeChapter" in sc) state.activeChapter = sc.activeChapter || null;
  if (Array.isArray(sc.propositions)) state.propositions = sc.propositions;
  if (Array.isArray(sc.chains)) state.chains = sc.chains;
  if (Array.isArray(sc.flashcards)) state.flashcards = sc.flashcards;
  if (Array.isArray(sc.mistakes)) state.mistakes = sc.mistakes;
  if (Array.isArray(sc.topCategories)) state.topCategories = sc.topCategories;
  if (sc.stats && typeof sc.stats === "object") state.stats = { ...state.stats, ...sc.stats };
  render();
}

const bridge = bootstrapMcpApp("syntopik-studio-pane", {
  onToolInput(args) {
    if (args?.tab) state.tab = args.tab;
    if (args?.planMermaid) state.plan.planMermaid = args.planMermaid;
    if (args?.activeNodeId) state.plan.activeNodeId = args.activeNodeId;
    render();
  },
  onToolResult(result) {
    if (result?.structuredContent) {
      applyStatePayload(result.structuredContent, true);
    }
  },
  onReady({ connected, caps }) {
    state.connected = connected;
    state.caps = caps;
    render();
    // Always quietly refresh live workspace progress & disk-persisted Plan DAG on mount
    // so switching chats never leaves Study Studio stuck on a stale trajectory snapshot.
    if (connected && caps?.canCallTools) {
      refreshFromServer();
    }
  },
});

render();
