#!/usr/bin/env node
/**
 * Zero-CDN single-file bundler for mlh-studio (SEP-1865 Antigravity MCP App).
 *
 * Produces:
 *   dist/server.mjs          — Zero-runtime-dependency Node ESM MCP server
 *   dist/quiz-card.html      — Self-contained inline quiz/predict/check UI
 *   dist/practice-card.html  — Self-contained inline practice problem UI
 *   dist/studio-pane.html    — Self-contained aux_pane/global Study Studio UI
 */

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import * as esbuild from "esbuild";

const ROOT = path.dirname(fileURLToPath(import.meta.url));
const SRC = path.join(ROOT, "src");
const DIST = path.join(ROOT, "dist");

fs.mkdirSync(DIST, { recursive: true });

function escapeInlineScript(js) {
  return js.replace(/<\/script/gi, "<\\/script");
}

function escapeInlineStyle(css) {
  return css.replace(/<\/style/gi, "<\\/style");
}

function wrapSingleFileHtml({ title, css, js }) {
  return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>${title}</title>
<style>${escapeInlineStyle(css)}</style>
</head>
<body>
<div id="app"></div>
<script type="module">${escapeInlineScript(js)}</script>
</body>
</html>
`;
}

async function buildServer() {
  const outFile = path.join(DIST, "server.mjs");
  await esbuild.build({
    entryPoints: [path.join(SRC, "server.mjs")],
    bundle: true,
    platform: "node",
    target: "node20",
    format: "esm",
    outfile: outFile,
    banner: {
      js: [
        "#!/usr/bin/env node",
        'import { createRequire as __mlhCreateRequire } from "node:module";',
        "const require = __mlhCreateRequire(import.meta.url);",
      ].join("\n"),
    },
    minify: false,
    logLevel: "warning",
  });
  fs.chmodSync(outFile, 0o755);
  return { name: "dist/server.mjs", bytes: fs.statSync(outFile).size };
}

async function buildUiApp({ entry, outName, title }) {
  const commonCss = fs.readFileSync(path.join(SRC, "ui", "common.css"), "utf8");
  const result = await esbuild.build({
    entryPoints: [path.join(SRC, "ui", entry)],
    bundle: true,
    platform: "browser",
    target: ["es2022"],
    format: "esm",
    write: false,
    minify: true,
    define: {
      "process.env.NODE_ENV": '"production"',
    },
    logLevel: "warning",
  });

  if (!result.outputFiles || result.outputFiles.length === 0) {
    throw new Error(`esbuild produced no output for ${entry}`);
  }
  const bundledJs = result.outputFiles[0].text;
  const html = wrapSingleFileHtml({ title, css: commonCss, js: bundledJs });

  // Hard CSP hygiene check: forbid any external http(s) <script> or <link> tags.
  if (/<(?:script|link)\b[^>]*\b(?:src|href)\s*=\s*["']https?:\/\//i.test(html)) {
    throw new Error(`External CDN script/link detected in ${outName}!`);
  }

  const outFile = path.join(DIST, outName);
  fs.writeFileSync(outFile, html, "utf8");
  return { name: `dist/${outName}`, bytes: fs.statSync(outFile).size };
}

async function main() {
  const outputs = [];
  outputs.push(await buildServer());
  outputs.push(
    await buildUiApp({
      entry: "quiz-card.mjs",
      outName: "quiz-card.html",
      title: "Syntopik Quiz Card",
    })
  );
  outputs.push(
    await buildUiApp({
      entry: "practice-card.mjs",
      outName: "practice-card.html",
      title: "Syntopik Practice Card",
    })
  );
  outputs.push(
    await buildUiApp({
      entry: "studio-pane.mjs",
      outName: "studio-pane.html",
      title: "Syntopik Studio",
    })
  );

  for (const item of outputs) {
    console.log(`Built ${item.name} (${(item.bytes / 1024).toFixed(1)} KiB)`);
  }
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
