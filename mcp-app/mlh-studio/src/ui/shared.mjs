/**
 * Shared zero-CDN UI utilities for mlh-studio MCP Apps:
 *  - Official @modelcontextprotocol/ext-apps host connection & theming
 *  - XSS-safe Markdown + pure-JS LaTeX-to-HTML renderer
 *  - Deterministic seeded Fisher-Yates option shuffler
 *  - Collapsible mini rough-work <canvas> SketchPad
 */

import {
  App,
  applyDocumentTheme,
  applyHostFonts,
  applyHostStyleVariables,
} from "@modelcontextprotocol/ext-apps";

export function escapeHtml(str) {
  return String(str ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

// --- Pure-JS LaTeX-to-HTML Renderer (zero CDN, XSS-safe) ---

const LATEX_SYMBOLS = {
  alpha: "α", beta: "β", gamma: "γ", delta: "δ", epsilon: "ϵ", varepsilon: "ε",
  zeta: "ζ", eta: "η", theta: "θ", vartheta: "ϑ", iota: "ι", kappa: "κ",
  lambda: "λ", mu: "μ", nu: "ν", xi: "ξ", pi: "π", varpi: "ϖ",
  rho: "ρ", varrho: "ϱ", sigma: "σ", varsigma: "ς", tau: "τ", upsilon: "υ",
  phi: "ϕ", varphi: "φ", chi: "χ", psi: "ψ", omega: "ω",
  Gamma: "Γ", Delta: "Δ", Theta: "Θ", Lambda: "Λ", Xi: "Ξ", Pi: "Π",
  Sigma: "Σ", Upsilon: "Υ", Phi: "Φ", Psi: "Ψ", Omega: "Ω",
  infty: "∞", partial: "∂", nabla: "∇", hbar: "ℏ", ell: "ℓ",
  times: "×", cdot: "·", pm: "±", mp: "∓", div: "÷", ast: "∗", star: "⋆",
  leq: "≤", le: "≤", geq: "≥", ge: "≥", neq: "≠", ne: "≠",
  approx: "≈", equiv: "≡", sim: "∼", simeq: "≃", cong: "≅", propto: "∝",
  to: "→", rightarrow: "→", leftarrow: "←", leftrightarrow: "↔",
  Rightarrow: "⇒", Leftarrow: "⇐", Leftrightarrow: "⇔", implies: "⇒", iff: "⇔",
  mapsto: "↦", uparrow: "↑", downarrow: "↓",
  in: "∈", notin: "∉", subset: "⊂", subseteq: "⊆", supset: "⊃", supseteq: "⊇",
  cup: "∪", cap: "∩", emptyset: "∅", varnothing: "∅",
  forall: "∀", exists: "∃", land: "∧", wedge: "∧", lor: "∨", vee: "∨", neg: "¬", lnot: "¬",
  perp: "⊥", parallel: "∥", angle: "∠", triangle: "△", circ: "∘", deg: "°",
  sum: "∑", prod: "∏", int: "∫", iint: "∬", oint: "∮",
  ldots: "…", cdots: "⋯", dots: "…", vdots: "⋮", ddots: "⋱",
  quad: "\u2001", qquad: "\u2001\u2001", ",": "\u2009", ";": "\u2005", "!": "", " ": "\u00a0",
  "%": "%", "{": "{", "}": "}", "_": "_", "$": "$", "&": "&",
};

const MATH_FUNCS = new Set([
  "sin", "cos", "tan", "cot", "sec", "csc", "arcsin", "arccos", "arctan",
  "sinh", "cosh", "tanh", "ln", "log", "exp", "lim", "det", "dim", "ker",
  "max", "min", "sup", "inf", "arg", "gcd", "mod", "Re", "Im",
]);

const BLACKBOARD = {
  R: "ℝ", N: "ℕ", Z: "ℤ", Q: "ℚ", C: "ℂ", H: "ℍ", F: "𝔽", P: "ℙ", E: "𝔼",
};

function readGroup(str, idx) {
  let i = idx;
  while (i < str.length && str[i] === " ") i += 1;
  if (i >= str.length) return { content: "", next: i };
  if (str[i] === "{") {
    let depth = 1;
    let j = i + 1;
    while (j < str.length && depth > 0) {
      if (str[j] === "\\" && j + 1 < str.length) {
        j += 2;
        continue;
      }
      if (str[j] === "{") depth += 1;
      else if (str[j] === "}") depth -= 1;
      j += 1;
    }
    return { content: str.slice(i + 1, Math.max(i + 1, j - 1)), next: j };
  }
  if (str[i] === "\\") {
    let j = i + 1;
    while (j < str.length && /[A-Za-z]/.test(str[j])) j += 1;
    if (j === i + 1 && j < str.length) j += 1;
    return { content: str.slice(i, j), next: j };
  }
  return { content: str[i], next: i + 1 };
}

function renderChem(raw) {
  const esc = escapeHtml(raw.trim())
    .replace(/&lt;=&gt;|<=>/g, " ⇌ ")
    .replace(/-&gt;|->/g, " → ")
    .replace(/&lt;-|<-/g, " ← ");
  // Charges like ^2+, ^2-, ^+, ^-
  let out = esc.replace(/\^(\d*[+-]|[+-]\d*)/g, "<sup>$1</sup>");
  // Element/paren subscripts like H2O, SO4, (OH)2
  out = out.replace(/([A-Za-z)\]])(\d+)/g, "$1<sub>$2</sub>");
  return `<span class="math-op">${out}</span>`;
}

function renderTexInner(rawTex) {
  const tex = String(rawTex || "")
    .replace(/\\begin\{aligned\}|\\end\{aligned\}|\\begin\{align\*?\}|\\end\{align\*?\}/g, "")
    .replace(/\\left\s*([()[\]|.]|\\\{|\\\})/g, (_, d) => (d === "." ? "" : d.replace(/^\\/, "")))
    .replace(/\\right\s*([()[\]|.]|\\\{|\\\})/g, (_, d) => (d === "." ? "" : d.replace(/^\\/, "")));

  let out = "";
  let i = 0;

  while (i < tex.length) {
    const ch = tex[i];

    if (ch === "\\" && tex[i + 1] === "\\") {
      out += "<br/>";
      i += 2;
      continue;
    }

    if (ch === "\\") {
      let j = i + 1;
      if (j < tex.length && /[A-Za-z]/.test(tex[j])) {
        while (j < tex.length && /[A-Za-z]/.test(tex[j])) j += 1;
      } else if (j < tex.length) {
        j += 1;
      }
      const cmd = tex.slice(i + 1, j);
      i = j;

      if (cmd === "frac" || cmd === "dfrac" || cmd === "tfrac") {
        const num = readGroup(tex, i);
        const den = readGroup(tex, num.next);
        i = den.next;
        out += `<span class="math-frac"><span class="math-num">${renderTexInner(num.content)}</span><span class="math-den">${renderTexInner(den.content)}</span></span>`;
      } else if (cmd === "sqrt") {
        let rootHtml = "";
        let k = i;
        while (k < tex.length && tex[k] === " ") k += 1;
        if (tex[k] === "[") {
          const endBracket = tex.indexOf("]", k + 1);
          if (endBracket !== -1) {
            rootHtml = `<sup>${renderTexInner(tex.slice(k + 1, endBracket))}</sup>`;
            i = endBracket + 1;
          }
        }
        const rad = readGroup(tex, i);
        i = rad.next;
        out += `<span class="math-sqrt">${rootHtml}√<span class="math-rad">${renderTexInner(rad.content)}</span></span>`;
      } else if (cmd === "text" || cmd === "mathrm" || cmd === "operatorname") {
        const g = readGroup(tex, i);
        i = g.next;
        out += `<span class="math-op">${escapeHtml(g.content)}</span>`;
      } else if (cmd === "mathbf" || cmd === "boldsymbol" || cmd === "bm") {
        const g = readGroup(tex, i);
        i = g.next;
        out += `<strong>${renderTexInner(g.content)}</strong>`;
      } else if (cmd === "mathbb") {
        const g = readGroup(tex, i);
        i = g.next;
        const mapped = g.content
          .split("")
          .map((c) => BLACKBOARD[c] || escapeHtml(c))
          .join("");
        out += `<span class="math-op">${mapped}</span>`;
      } else if (cmd === "ce") {
        const g = readGroup(tex, i);
        i = g.next;
        out += renderChem(g.content);
      } else if (cmd === "vec") {
        const g = readGroup(tex, i);
        i = g.next;
        out += `<span class="math-vec">${renderTexInner(g.content)}</span>`;
      } else if (cmd === "hat" || cmd === "widehat") {
        const g = readGroup(tex, i);
        i = g.next;
        out += `<span class="math-hat">${renderTexInner(g.content)}</span>`;
      } else if (cmd === "bar" || cmd === "overline") {
        const g = readGroup(tex, i);
        i = g.next;
        out += `<span class="math-over">${renderTexInner(g.content)}</span>`;
      } else if (cmd === "dot") {
        const g = readGroup(tex, i);
        i = g.next;
        out += `${renderTexInner(g.content)}\u0307`;
      } else if (cmd === "ddot") {
        const g = readGroup(tex, i);
        i = g.next;
        out += `${renderTexInner(g.content)}\u0308`;
      } else if (cmd === "tilde" || cmd === "widetilde") {
        const g = readGroup(tex, i);
        i = g.next;
        out += `${renderTexInner(g.content)}\u0303`;
      } else if (cmd === "cancel") {
        const g = readGroup(tex, i);
        i = g.next;
        out += `<span class="math-cancel">${renderTexInner(g.content)}</span>`;
      } else if (cmd === "underbrace") {
        const g = readGroup(tex, i);
        i = g.next;
        out += `<span>${renderTexInner(g.content)}</span>`;
      } else if (MATH_FUNCS.has(cmd)) {
        out += `<span class="math-op">${cmd}</span>`;
      } else if (Object.prototype.hasOwnProperty.call(LATEX_SYMBOLS, cmd)) {
        out += LATEX_SYMBOLS[cmd];
      } else {
        out += escapeHtml(cmd);
      }
      continue;
    }

    if (ch === "^") {
      const g = readGroup(tex, i + 1);
      i = g.next;
      out += `<sup>${renderTexInner(g.content)}</sup>`;
      continue;
    }
    if (ch === "_") {
      const g = readGroup(tex, i + 1);
      i = g.next;
      out += `<sub>${renderTexInner(g.content)}</sub>`;
      continue;
    }
    if (ch === "{" || ch === "}") {
      i += 1;
      continue;
    }
    if (ch === "&") {
      out += "\u2002";
      i += 1;
      continue;
    }
    if (ch === "-") {
      out += "−";
      i += 1;
      continue;
    }
    if (/[A-Za-z]/.test(ch)) {
      out += `<span class="math-var">${ch}</span>`;
      i += 1;
      continue;
    }
    out += escapeHtml(ch);
    i += 1;
  }

  return out;
}

export function renderRichText(src) {
  const input = String(src ?? "");
  if (!input.trim()) return "";

  const tokens = [];
  const hold = (html) => {
    const key = `\u0000TOK${tokens.length}\u0000`;
    tokens.push(html);
    return key;
  };

  // 1. Extract fenced code blocks first (raw escaped)
  let work = input.replace(/```([A-Za-z0-9_-]*)\n([\s\S]*?)```/g, (_, _lang, code) =>
    hold(`<pre><code>${escapeHtml(code.replace(/\n$/, ""))}</code></pre>`)
  );

  // 2. Extract inline code spans
  work = work.replace(/`([^`\n]+)`/g, (_, code) => hold(`<code>${escapeHtml(code)}</code>`));

  // 3. Extract display math $$...$$ and \[...\]
  work = work.replace(/\$\$([\s\S]+?)\$\$/g, (_, tex) =>
    hold(`<span class="math-display">${renderTexInner(tex.trim())}</span>`)
  );
  work = work.replace(/\\\[([\s\S]+?)\\\]/g, (_, tex) =>
    hold(`<span class="math-display">${renderTexInner(tex.trim())}</span>`)
  );

  // 4. Extract inline math $...$ and \(...\)
  work = work.replace(/(?<!\$)\$(?!\$)([^\n$]+?)(?<!\$)\$(?!\$)/g, (_, tex) =>
    hold(`<span class="math-inline">${renderTexInner(tex.trim())}</span>`)
  );
  work = work.replace(/\\\((.+?)\\\)/g, (_, tex) =>
    hold(`<span class="math-inline">${renderTexInner(tex.trim())}</span>`)
  );

  // 5. Escape all remaining text (strict XSS protection!)
  work = escapeHtml(work);

  // 6. Basic inline Markdown (**bold**, *italic*)
  work = work.replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>");
  work = work.replace(/(?<!\*)\*([^*\n]+)\*(?!\*)/g, "<em>$1</em>");

  // 7. Block formatting (headings, blockquotes, lists, paragraphs)
  const lines = work.split(/\r?\n/);
  const blocks = [];
  let inUl = false;
  let inOl = false;

  const closeLists = () => {
    if (inUl) {
      blocks.push("</ul>");
      inUl = false;
    }
    if (inOl) {
      blocks.push("</ol>");
      inOl = false;
    }
  };

  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed) {
      closeLists();
      continue;
    }
    const hMatch = /^(#{1,4})\s+(.*)$/.exec(trimmed);
    if (hMatch) {
      closeLists();
      const lvl = Math.min(4, Math.max(2, hMatch[1].length));
      blocks.push(`<h${lvl}>${hMatch[2]}</h${lvl}>`);
      continue;
    }
    if (trimmed.startsWith("&gt; ")) {
      closeLists();
      blocks.push(`<blockquote>${trimmed.slice(5)}</blockquote>`);
      continue;
    }
    const ulMatch = /^[-*•]\s+(.*)$/.exec(trimmed);
    if (ulMatch) {
      if (inOl) {
        blocks.push("</ol>");
        inOl = false;
      }
      if (!inUl) {
        blocks.push("<ul>");
        inUl = true;
      }
      blocks.push(`<li>${ulMatch[1]}</li>`);
      continue;
    }
    const olMatch = /^\d+\.\s+(.*)$/.exec(trimmed);
    if (olMatch) {
      if (inUl) {
        blocks.push("</ul>");
        inUl = false;
      }
      if (!inOl) {
        blocks.push("<ol>");
        inOl = true;
      }
      blocks.push(`<li>${olMatch[1]}</li>`);
      continue;
    }
    closeLists();
    blocks.push(`<p>${trimmed}</p>`);
  }
  closeLists();

  let html = blocks.join("\n");
  html = html.replace(/\u0000TOK(\d+)\u0000/g, (_, idx) => tokens[Number(idx)] ?? "");
  return html;
}

export function renderInlineRich(src) {
  const html = renderRichText(src);
  // Strip wrapping single <p>...</p> for compact option labels
  const m = /^<p>([\s\S]*)<\/p>$/.exec(html.trim());
  return m ? m[1] : html;
}

// --- Deterministic Seeded Shuffle ---

export function seededShuffle(items, seed) {
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

// --- Collapsible Mini Rough-Work Canvas SketchPad ---

export function createSketchPad({ title = "Rough work sketch (optional)" } = {}) {
  const wrap = document.createElement("div");
  wrap.className = "sketch-wrap";

  let isOpen = false;
  let tool = "pen";
  let drawing = false;
  let strokes = [];
  let currentStroke = null;

  const toggleBtn = document.createElement("button");
  toggleBtn.type = "button";
  toggleBtn.className = "sketch-toggle";
  const toggleLabel = document.createElement("span");
  toggleLabel.textContent = title;
  const toggleState = document.createElement("span");
  toggleState.className = "subtle";
  toggleState.textContent = "Show";
  toggleBtn.append(toggleLabel, toggleState);

  const body = document.createElement("div");
  body.className = "sketch-body";
  body.hidden = true;

  const toolbar = document.createElement("div");
  toolbar.className = "row-between";

  const leftTools = document.createElement("div");
  leftTools.className = "row";
  const penBtn = document.createElement("button");
  penBtn.type = "button";
  penBtn.className = "btn btn-sm btn-primary";
  penBtn.textContent = "Pen";
  const eraserBtn = document.createElement("button");
  eraserBtn.type = "button";
  eraserBtn.className = "btn btn-sm";
  eraserBtn.textContent = "Eraser";
  leftTools.append(penBtn, eraserBtn);

  const rightTools = document.createElement("div");
  rightTools.className = "row";
  const undoBtn = document.createElement("button");
  undoBtn.type = "button";
  undoBtn.className = "btn btn-sm";
  undoBtn.textContent = "Undo";
  const clearBtn = document.createElement("button");
  clearBtn.type = "button";
  clearBtn.className = "btn btn-sm";
  clearBtn.textContent = "Clear";
  rightTools.append(undoBtn, clearBtn);

  toolbar.append(leftTools, rightTools);

  const canvas = document.createElement("canvas");
  canvas.className = "sketch-canvas";
  canvas.width = 640;
  canvas.height = 240;

  body.append(toolbar, canvas);
  wrap.append(toggleBtn, body);

  const ctx = canvas.getContext("2d");

  function redraw() {
    ctx.fillStyle = "#FCFBF8";
    ctx.fillRect(0, 0, canvas.width, canvas.height);

    // Subtle archival dot grid for neat rough-paper alignment
    ctx.fillStyle = "#DFD9CD";
    for (let x = 20; x < canvas.width; x += 24) {
      for (let y = 20; y < canvas.height; y += 24) {
        ctx.fillRect(x, y, 1.5, 1.5);
      }
    }

    ctx.lineCap = "round";
    ctx.lineJoin = "round";
    for (const s of strokes) {
      if (s.points.length === 0) continue;
      ctx.strokeStyle = s.tool === "eraser" ? "#FCFBF8" : "#1C2024";
      ctx.lineWidth = s.tool === "eraser" ? 16 : 2.5;
      ctx.beginPath();
      ctx.moveTo(s.points[0].x, s.points[0].y);
      for (let i = 1; i < s.points.length; i += 1) {
        ctx.lineTo(s.points[i].x, s.points[i].y);
      }
      if (s.points.length === 1) {
        ctx.lineTo(s.points[0].x + 0.5, s.points[0].y + 0.5);
      }
      ctx.stroke();
    }

    const count = strokes.filter((s) => s.tool === "pen").length;
    toggleLabel.textContent = count > 0 ? `${title} · ${count} stroke${count === 1 ? "" : "s"}` : title;
  }

  function getPoint(ev) {
    const rect = canvas.getBoundingClientRect();
    const scaleX = canvas.width / Math.max(1, rect.width);
    const scaleY = canvas.height / Math.max(1, rect.height);
    return {
      x: (ev.clientX - rect.left) * scaleX,
      y: (ev.clientY - rect.top) * scaleY,
    };
  }

  canvas.addEventListener("pointerdown", (ev) => {
    canvas.setPointerCapture(ev.pointerId);
    drawing = true;
    currentStroke = { tool, points: [getPoint(ev)] };
    strokes.push(currentStroke);
    redraw();
  });

  canvas.addEventListener("pointermove", (ev) => {
    if (!drawing || !currentStroke) return;
    currentStroke.points.push(getPoint(ev));
    redraw();
  });

  const endStroke = () => {
    drawing = false;
    currentStroke = null;
  };
  canvas.addEventListener("pointerup", endStroke);
  canvas.addEventListener("pointercancel", endStroke);

  penBtn.addEventListener("click", () => {
    tool = "pen";
    penBtn.className = "btn btn-sm btn-primary";
    eraserBtn.className = "btn btn-sm";
  });

  eraserBtn.addEventListener("click", () => {
    tool = "eraser";
    eraserBtn.className = "btn btn-sm btn-primary";
    penBtn.className = "btn btn-sm";
  });

  undoBtn.addEventListener("click", () => {
    strokes.pop();
    redraw();
  });

  clearBtn.addEventListener("click", () => {
    strokes = [];
    redraw();
  });

  toggleBtn.addEventListener("click", () => {
    isOpen = !isOpen;
    body.hidden = !isOpen;
    toggleState.textContent = isOpen ? "Hide" : "Show";
    if (isOpen) redraw();
  });

  redraw();

  return {
    el: wrap,
    hasInk() {
      return strokes.some((s) => s.tool === "pen" && s.points.length > 0);
    },
    toPngDataUrl() {
      redraw();
      return canvas.toDataURL("image/png");
    },
    clear() {
      strokes = [];
      redraw();
    },
  };
}

// --- Official @modelcontextprotocol/ext-apps Bootstrap ---

export function bootstrapMcpApp(appName, { onToolInput, onToolResult, onToolCancelled, onReady }) {
  // Sensible initial theme before host context arrives
  const prefersDark =
    typeof window !== "undefined" &&
    window.matchMedia &&
    window.matchMedia("(prefers-color-scheme: dark)").matches;
  applyDocumentTheme(prefersDark ? "dark" : "light");

  const app = new App({ name: appName, version: "2.0.0" }, {});
  let connected = false;

  const applyHostCtx = (ctx) => {
    if (!ctx || typeof ctx !== "object") return;
    if (ctx.theme === "dark" || ctx.theme === "light") {
      applyDocumentTheme(ctx.theme);
    }
    if (ctx.styles?.variables) {
      applyHostStyleVariables(ctx.styles.variables);
    }
    if (ctx.styles?.css?.fonts) {
      applyHostFonts(ctx.styles.css.fonts);
    }
  };

  // Register ALL handlers BEFORE app.connect() so no notification is missed!
  app.ontoolinput = (params) => {
    onToolInput?.(params?.arguments ?? {});
  };
  app.ontoolresult = (result) => {
    onToolResult?.(result);
  };
  app.ontoolcancelled = (params) => {
    onToolCancelled?.(params);
  };
  app.onhostcontextchanged = (ctx) => {
    applyHostCtx(ctx);
  };

  const getCaps = () => {
    if (!connected) {
      // Before handshake completes (e.g., standalone preview), return null so UI stays inspectable
      return null;
    }
    const hc = app.getHostCapabilities() || {};
    return {
      canMessage: Boolean(hc.message),
      canUpdateContext: Boolean(hc.updateModelContext),
      canCallTools: Boolean(hc.serverTools),
    };
  };

  const connectPromise = app
    .connect()
    .then(() => {
      connected = true;
      const ctx = app.getHostContext();
      applyHostCtx(ctx);
      onReady?.({ connected: true, caps: getCaps(), hostContext: ctx });
    })
    .catch(() => {
      connected = false;
      // Standalone top-level preview fallback (when no parent iframe host exists)
      if (typeof window !== "undefined" && window === window.parent) {
        window.addEventListener("message", (ev) => {
          const d = ev?.data;
          if (!d || d.jsonrpc !== "2.0" || typeof d.method !== "string") return;
          if (d.method === "ui/notifications/host-context-changed") {
            applyHostCtx(d.params);
          } else if (d.method === "ui/notifications/tool-input") {
            onToolInput?.(d.params?.arguments ?? {});
          } else if (d.method === "ui/notifications/tool-result") {
            onToolResult?.(d.params);
          } else if (d.method === "ui/notifications/tool-cancelled") {
            onToolCancelled?.(d.params);
          }
        });
      }
      onReady?.({ connected: false, caps: null, hostContext: null });
    });

  return { app, getCaps, isConnected: () => connected, ready: connectPromise };
}
