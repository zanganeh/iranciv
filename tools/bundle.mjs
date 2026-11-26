// Builds dist/iranciv.html: the whole app (scripts, styles and data) in one self-contained HTML file, for hosts that
// serve a single page. Needs esbuild: `npx esbuild` must work, or pass its path as ESBUILD=/path/to/esbuild.
// Run: node tools/bundle.mjs
import { execFileSync } from "node:child_process";
import { readFileSync, writeFileSync, mkdirSync, readdirSync } from "node:fs";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("../", import.meta.url));
const web = root + "web/";
const esbuild = process.env.ESBUILD || "esbuild";
const js = execFileSync(esbuild, [web + "js/bundle-entry.js", "--bundle", "--format=esm", "--minify", "--log-level=error"], { encoding: "utf8", maxBuffer: 64 << 20 });
const css = ["style.css", "game.css"].map((f) => readFileSync(web + "css/" + f, "utf8")).join("\n");
const data = {};
for (const f of readdirSync(web + "data")) if (f.endsWith(".json")) data[f.slice(0, -5)] = JSON.parse(readFileSync(web + "data/" + f, "utf8"));
const safe = (s) => s.replace(/<\/(script|style)/gi, "<\\/$1");
let html = readFileSync(web + "index.html", "utf8");
html = html.replace(/\s*<link rel="stylesheet" href="css\/style.css">/, () => `\n  <style data-bundled>${safe(css)}</style>`);
html = html.replace(/<script type="module" src="js\/main.js"><\/script>/, () =>
  `<script>globalThis.__IRANCIV_DATA__=${safe(JSON.stringify(data))};</script>\n  <script type="module">${safe(js)}</script>`);
if (html.includes('src="js/main.js"') || !html.includes("data-bundled")) throw new Error("index.html layout changed; update tools/bundle.mjs");
mkdirSync(root + "dist", { recursive: true });
writeFileSync(root + "dist/iranciv.html", html);
console.log(`dist/iranciv.html: ${(html.length / 1e6).toFixed(2)} MB`);
