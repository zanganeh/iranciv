// Map overlays for the game: fog of war, borders, improvements and roads (world space, under the labels), and
// cities, units, selection and move range (screen space, on top). They read `view` (set by ui.js).

import { SQRT3, UNITS, BUILDINGS, LUXURIES } from "./rules.js";
import { visibleSet, has, cityYields, tileYield, bestSite, coinCap } from "./game.js";
import { drawAncientCity, drawUnitToken, drawBuilding, cityRadius, cityDrawR } from "./art.js";

const YIELD_EMOJI = { food: "🌾", prod: "⚒️", gold: "🪙", sci: "📜", cult: "🏺" };

export function createView() {
  return {
    g: null, board: null, sel: null, reach: null, vis: null, version: 0, cache: null, hover: -1, lens: null, preview: null, settle: null, buy: null,
    frac: 0, msPerTurn: 8000, fx: [], anim: new Map(), stepCell: -1,
  };
}

/** Call after anything in the game changed (move, turn, build). */
export function touch(view) {
  view.version++;
  const { g, board } = view;
  view.vis = g && g.human >= 0 ? visibleSet(g, board, g.human) : null;
}

function hexPath(path, x, y, R) {
  for (let k = 0; k < 6; k++) {
    const a = (Math.PI / 180) * (60 * k - 30);
    const px = x + R * Math.cos(a), py = y + R * Math.sin(a);
    if (k === 0) path.moveTo(px, py);
    else path.lineTo(px, py);
  }
  path.closePath();
}

// Corner pair of the edge toward neighbour direction d (0 E, 1 SE, 2 SW, 3 W, 4 NW, 5 NE), pointy-top hexes.
const EDGE = [[0, 1], [1, 2], [2, 3], [3, 4], [4, 5], [5, 0]];
function corner(x, y, R, k) {
  const a = (Math.PI / 180) * (60 * k - 30);
  return [x + R * Math.cos(a), y + R * Math.sin(a)];
}

function hexToRgba(hex, a) {
  const n = parseInt(hex.slice(1), 16);
  return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
}

function buildCache(view) {
  const { g, board } = view;
  const { grid } = board;
  const R = grid.R;
  const me = g.human;
  const F = me >= 0 ? g.factions[me] : null;
  const fogDark = new Path2D(), fogDim = new Path2D();
  if (F) {
    for (let i = 0; i < board.n; i++) {
      if (!F.seen[i]) hexPath(fogDark, grid.cx(i), grid.cy(i), R + 0.6);
      else if (!view.vis.has(i) && board.tiles[i].land) hexPath(fogDim, grid.cx(i), grid.cy(i), R + 0.3);
    }
  }
  const seen = (i) => !F || F.seen[i];
  const fills = g.factions.map(() => new Path2D());
  const edges = g.factions.map(() => new Path2D());
  for (let i = 0; i < board.n; i++) {
    const cid = g.terr[i];
    if (cid < 0 || !seen(i)) continue;
    const f = g.cities[cid].f;
    const x = grid.cx(i), y = grid.cy(i);
    hexPath(fills[f], x, y, R + 0.2);
    for (let d = 0; d < 6; d++) {
      const j = grid.neighbour(i, d);
      const other = j >= 0 && g.terr[j] >= 0 ? g.cities[g.terr[j]].f : -1;
      if (other === f) continue;
      const [a, b] = EDGE[d];
      const [x0, y0] = corner(x, y, R - 1.2, a), [x1, y1] = corner(x, y, R - 1.2, b);
      edges[f].moveTo(x0, y0);
      edges[f].lineTo(x1, y1);
    }
  }
  const farms = new Path2D(), mines = new Path2D(), qanats = new Path2D(), roads = new Path2D();
  for (let i = 0; i < board.n; i++) {
    if (!seen(i)) continue;
    const x = grid.cx(i), y = grid.cy(i);
    const imp = g.imp[i];
    if (imp === "farm") {
      for (let k = -1; k <= 1; k++) { farms.moveTo(x - 7, y + k * 4); farms.lineTo(x + 7, y + k * 4); }
    } else if (imp === "mine") {
      mines.moveTo(x - 6, y + 5); mines.lineTo(x, y - 5); mines.lineTo(x + 6, y + 5); mines.closePath();
    } else if (imp === "qanat") {
      for (let k = -1; k <= 1; k++) { qanats.moveTo(x + k * 6 + 2, y); qanats.arc(x + k * 6, y, 2, 0, Math.PI * 2); }
    }
    if (g.road[i] || g.cityAt[i] >= 0) {
      for (let d = 0; d < 3; d++) {
        const j = grid.neighbour(i, d);
        if (j >= 0 && seen(j) && (g.road[j] || g.cityAt[j] >= 0) && (g.road[i] || g.road[j])) {
          roads.moveTo(x, y);
          roads.lineTo(grid.cx(j), grid.cy(j));
        }
      }
    }
  }
  // map lens: shade every seen land hex by one yield
  let lens = null;
  if (view.lens) {
    lens = [1, 2, 3, 4, 5].map(() => new Path2D());
    for (let i = 0; i < board.n; i++) {
      if (!board.tiles[i].land || !seen(i)) continue;
      const v = Math.min(5, tileYield(g, board, i)[view.lens]);
      if (v > 0) hexPath(lens[v - 1], grid.cx(i), grid.cy(i), R - 0.6);
    }
  }
  // things worth marking with an icon: luxuries and heritage ground
  const marks = [];
  for (let i = 0; i < board.n; i++) {
    const t = board.tiles[i];
    if (t.land && seen(i) && (t.lux || t.heritage) && g.cityAt[i] < 0) marks.push(i);
  }
  // per-city banner details for my cities
  const info = new Map();
  for (const c of g.cities) {
    if (c.f !== me) continue;
    const y = cityYields(g, board, c);
    const cost = c.build ? (c.build.kind === "unit" ? UNITS[c.build.id].cost : BUILDINGS[c.build.id].cost) : 0;
    info.set(c.id, {
      build: c.build ? (c.build.kind === "unit" ? UNITS[c.build.id].emoji : "⚒️") : "⚒️–",
      bturns: c.build ? (y.prod > 0 ? Math.max(1, Math.ceil((cost - c.prod) / y.prod)) : "∞") : "",
      grow: y.mood.unhappy > 0 ? "😠" : c.pop >= y.mood.housing ? "🏠" : y.surplus > 0 ? `👥${Math.ceil((y.need - c.food) / y.surplus)}` : y.surplus < 0 ? "⚠️" : "👥∞",
    });
  }
  // buildings under construction: where they will stand, how far along, how fast
  const sites = [];
  for (const c of g.cities) {
    if (!c.build || c.build.kind !== "bld" || (F && !F.seen[c.cell])) continue;
    const B = BUILDINGS[c.build.id];
    const cell = c.build.site != null ? c.build.site : bestSite(g, board, c, c.build.id);
    const y = cityYields(g, board, c);
    sites.push({ c, cell, id: c.build.id, centre: cell === c.cell, p0: Math.min(1, c.prod / B.cost), rate: y.prod / B.cost, mine: c.f === me });
  }
  // coin purses of my cities
  const purses = [];
  for (const c of g.cities) {
    if (c.f !== me || !c.coins) continue;
    purses.push({ c, coins: c.coins, full: c.coins / coinCap(cityYields(g, board, c).gold) });
  }
  return { version: view.version, lensKey: view.lens, sites, purses, fogDark, fogDim, fills, edges, farms, mines, qanats, roads, lens, marks, info };
}

/** World-space layer: territory, improvements, fog. */
export function drawWorld(ctx, cam, view) {
  if (!view.g) return;
  if (!view.cache || view.cache.version !== view.version || view.cache.lensKey !== view.lens) view.cache = buildCache(view);
  const c = view.cache;
  const { g } = view;
  const px = 1 / cam.scale; // one screen pixel in km
  g.factions.forEach((F, f) => {
    ctx.fillStyle = hexToRgba(F.color, view.lens ? 0.08 : 0.2);
    ctx.fill(c.fills[f]);
  });
  if (c.lens) {
    const col = LENS_COLOR[view.lens];
    c.lens.forEach((p, k) => { ctx.fillStyle = hexToRgba(col, 0.12 + k * 0.15); ctx.fill(p); });
  }
  ctx.lineCap = "round";
  ctx.strokeStyle = "rgba(120,86,48,0.9)";
  ctx.lineWidth = Math.max(1.2, 2.2 * px);
  ctx.setLineDash([4 * px + 1.5, 3 * px + 1]);
  ctx.stroke(c.roads);
  ctx.setLineDash([]);
  ctx.lineWidth = Math.max(0.8, 1.4 * px);
  ctx.strokeStyle = "rgba(230,240,170,0.85)";
  ctx.stroke(c.farms);
  ctx.fillStyle = "rgba(90,70,60,0.85)";
  ctx.fill(c.mines);
  ctx.fillStyle = "rgba(120,200,255,0.9)";
  ctx.fill(c.qanats);
  g.factions.forEach((F, f) => {
    ctx.strokeStyle = F.color;
    ctx.lineWidth = Math.max(1.6, 3 * px);
    ctx.stroke(c.edges[f]);
  });
  // cities, drawn on the land (hidden under the fog where unexplored)
  if (cam.scale >= 0.9) {
    const me = g.human >= 0 ? g.factions[g.human] : null;
    const R = view.board.grid.R;
    for (const city of g.cities) {
      if (me && !me.seen[city.cell]) continue;
      const t = view.board.tiles[city.cell];
      const outside = new Set();
      for (const [b, cell] of Object.entries(city.sites || {})) {
        if (cell === city.cell) continue;
        outside.add(b);
        const u = view.board.tiles[cell];
        drawBuilding(ctx, b, u.x, u.y, R * 0.5, px);
      }
      drawAncientCity(ctx, t.x, t.y, cityDrawR(R, city.pop, cam.scale), {
        pop: city.pop, color: g.factions[city.f].color, capital: city.capital, walls: has(g, city.f, "masonry"),
        blds: city.blds, seed: city.id * 7919 + city.cell, px, outside,
      });
    }
  }
  // construction sites: scaffolding, and the building rising out of the ground as the work goes on
  if (cam.scale >= 0.9) for (const st of c.sites) {
    if (st.centre) continue;
    const t = view.board.tiles[st.cell];
    const p = Math.min(1, st.p0 + st.rate * view.frac);
    drawSite(ctx, st.id, t.x, t.y, view.board.grid.R * 0.5, px, p);
  }
  // move range of the selected unit
  if (view.reach && view.reach.size) {
    const p = new Path2D();
    for (const i of view.reach) hexPath(p, view.board.grid.cx(i), view.board.grid.cy(i), view.board.grid.R - 1.5);
    ctx.fillStyle = "rgba(255,255,255,0.16)";
    ctx.fill(p);
    ctx.strokeStyle = "rgba(255,255,255,0.55)";
    ctx.lineWidth = Math.max(0.8, 1.2 * px);
    ctx.stroke(p);
  }
  if (view.hover >= 0) {
    const p = new Path2D();
    hexPath(p, view.board.grid.cx(view.hover), view.board.grid.cy(view.hover), view.board.grid.R - 1);
    ctx.strokeStyle = "rgba(255,240,180,0.9)";
    ctx.lineWidth = Math.max(1, 2 * px);
    ctx.stroke(p);
  }
  if (view.cityView) drawCityFocus(ctx, cam, view, px);
  ctx.fillStyle = "rgba(14,16,22,0.97)";
  ctx.fill(c.fogDark);
  ctx.fillStyle = "rgba(14,16,22,0.42)";
  ctx.fill(c.fogDim);
}

function drawSite(ctx, id, x, y, s, px, p) {
  // the plot, then the building revealed from the bottom up
  ctx.save();
  ctx.beginPath();
  ctx.arc(x, y, s * 1.15, 0, Math.PI * 2);
  ctx.fillStyle = "rgba(160,130,90,0.55)";
  ctx.fill();
  ctx.beginPath();
  const top = y + s * 1.2 - s * 2.6 * p;
  ctx.rect(x - s * 1.4, top, s * 2.8, s * 2.8);
  ctx.clip();
  ctx.globalAlpha = 0.9;
  drawBuilding(ctx, id, x, y, s, px);
  ctx.restore();
  // scaffolding poles and braces over the top
  ctx.strokeStyle = "rgba(120,84,40,0.95)";
  ctx.lineWidth = Math.max(px, s * 0.07);
  const h = s * 1.5, w = s * 0.9;
  ctx.beginPath();
  for (const dx of [-w, 0, w]) { ctx.moveTo(x + dx, y + s * 0.6); ctx.lineTo(x + dx, y + s * 0.6 - h); }
  for (let k = 0; k <= 2; k++) { const yy = y + s * 0.6 - (h * k) / 2; ctx.moveTo(x - w, yy); ctx.lineTo(x + w, yy); }
  ctx.moveTo(x - w, y + s * 0.6); ctx.lineTo(x, y + s * 0.6 - h / 2);
  ctx.moveTo(x, y + s * 0.6 - h / 2); ctx.lineTo(x + w, y + s * 0.6 - h);
  ctx.stroke();
  // a little crane
  ctx.beginPath();
  ctx.moveTo(x + w, y + s * 0.6 - h); ctx.lineTo(x + w, y - h * 1.05);
  ctx.lineTo(x - w * 0.4, y - h * 1.05);
  ctx.moveTo(x - w * 0.2, y - h * 1.05); ctx.lineTo(x - w * 0.2, y - h * 0.7);
  ctx.stroke();
}

const LENS_COLOR = { food: "#7fbf3f", prod: "#d9823b", gold: "#f2c94c", sci: "#4fa3e0", cult: "#b07ce0" };

// The close-up city screen: dim the land outside the city, mark the worked hexes and the building sites on offer.
function drawCityFocus(ctx, cam, view, px) {
  const { g, board } = view;
  const c = view.cityView;
  const { grid } = board;
  const R = grid.R;
  const inCity = new Set(c.tiles);
  const dim = new Path2D();
  const half = 3000;
  dim.rect(cam.x - half, cam.y - half, half * 2, half * 2);
  for (const i of c.tiles) hexPath(dim, grid.cx(i), grid.cy(i), R + 0.2);
  ctx.fillStyle = "rgba(10,10,14,0.62)";
  ctx.fill(dim, "evenodd");
  const edge = new Path2D();
  for (const i of c.tiles) for (let d = 0; d < 6; d++) {
    const j = grid.neighbour(i, d);
    if (j >= 0 && inCity.has(j)) continue;
    const [a, b] = EDGE[d];
    const [x0, y0] = corner(grid.cx(i), grid.cy(i), R, a), [x1, y1] = corner(grid.cx(i), grid.cy(i), R, b);
    edge.moveTo(x0, y0);
    edge.lineTo(x1, y1);
  }
  ctx.strokeStyle = "#f2dfa8";
  ctx.lineWidth = Math.max(2, 3.5 * px);
  ctx.stroke(edge);
  // worked hexes: a ring of light
  const worked = cityYields(g, board, c).worked;
  for (const i of worked) {
    const p = new Path2D();
    hexPath(p, grid.cx(i), grid.cy(i), R - 2.2);
    ctx.strokeStyle = "rgba(255,246,210,0.75)";
    ctx.lineWidth = Math.max(1.5, 2.5 * px);
    ctx.setLineDash([6 * px, 4 * px]);
    ctx.stroke(p);
    ctx.setLineDash([]);
  }
  // locked hexes: a solid ring
  for (const i of c.locked || []) {
    if (!worked.includes(i)) continue;
    const p = new Path2D();
    hexPath(p, grid.cx(i), grid.cy(i), R - 2.2);
    ctx.strokeStyle = "#fff6d2";
    ctx.lineWidth = Math.max(2, 3 * px);
    ctx.stroke(p);
  }
  // land for sale next to the borders
  if (!view.placing) for (const o of view.buy || []) {
    const p = new Path2D();
    hexPath(p, grid.cx(o.cell), grid.cy(o.cell), R - 1.5);
    ctx.fillStyle = "rgba(242,201,76,0.10)";
    ctx.fill(p);
    ctx.strokeStyle = view.hover === o.cell ? "#fff" : "rgba(242,201,76,0.6)";
    ctx.setLineDash([3 * px, 3 * px]);
    ctx.lineWidth = Math.max(1, 1.5 * px);
    ctx.stroke(p);
    ctx.setLineDash([]);
  }
  // building sites on offer while placing
  for (const o of view.placing?.options || []) {
    const p = new Path2D();
    hexPath(p, grid.cx(o.cell), grid.cy(o.cell), R - 1.2);
    const v = Object.values(o.bonus).reduce((a, b) => a + b, 0);
    ctx.fillStyle = o.centre ? "rgba(236,208,142,0.28)" : v > 0 ? "rgba(120,220,120,0.30)" : "rgba(236,208,142,0.16)";
    ctx.fill(p);
    ctx.strokeStyle = view.hover === o.cell ? "#fff" : "#ecd08e";
    ctx.lineWidth = Math.max(2, 3 * px);
    ctx.stroke(p);
  }
}

/** Screen-space layer: cities, units, selection. */
export function drawTokens(ctx, cam, view) {
  const { g, board } = view;
  if (!g) return;
  const s = cam.scale;
  const sx = (x) => (x - cam.x) * s + cam.w / 2, sy = (y) => (y - cam.y) * s + cam.h / 2;
  const me = g.human;
  const F = me >= 0 ? g.factions[me] : null;
  const visible = (i) => !F || view.vis?.has(i);
  const seen = (i) => !F || F.seen[i];
  const small = s < 0.9;
  const onScreen = (x, y) => x > -80 && y > -40 && x < cam.w + 80 && y < cam.h + 40;
  // destinations of my units
  const sel = view.sel;
  if (sel?.unit && sel.unit.dest >= 0) {
    ctx.strokeStyle = "rgba(255,240,180,0.8)";
    ctx.setLineDash([5, 4]);
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    ctx.moveTo(sx(board.tiles[sel.unit.cell].x), sy(board.tiles[sel.unit.cell].y));
    ctx.lineTo(sx(board.tiles[sel.unit.dest].x), sy(board.tiles[sel.unit.dest].y));
    ctx.stroke();
    ctx.setLineDash([]);
  }
  // the path a unit would take to the hovered hex
  const pv = view.preview;
  if (pv && sel?.unit && pv.unit === sel.unit && pv.cells.length) {
    ctx.strokeStyle = "rgba(255,240,180,0.95)";
    ctx.lineWidth = 2.5;
    ctx.setLineDash([2, 5]);
    ctx.lineCap = "round";
    ctx.beginPath();
    ctx.moveTo(sx(board.tiles[sel.unit.cell].x), sy(board.tiles[sel.unit.cell].y));
    for (const i of pv.cells) ctx.lineTo(sx(board.tiles[i].x), sy(board.tiles[i].y));
    ctx.stroke();
    ctx.setLineDash([]);
    const end = board.tiles[pv.cells[pv.cells.length - 1]];
    const label = pv.turns === 0 ? "this turn" : `${pv.turns} turn${pv.turns > 1 ? "s" : ""}`;
    ctx.font = "700 12px system-ui, sans-serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    const w = ctx.measureText(label).width + 12;
    ctx.fillStyle = "rgba(20,22,28,0.88)";
    roundRect(ctx, sx(end.x) - w / 2, sy(end.y) - 26, w, 18, 9);
    ctx.fill();
    ctx.fillStyle = "#fff0b4";
    ctx.fillText(label, sx(end.x), sy(end.y) - 17);
  }
  ctx.textBaseline = "middle";
  // luxuries and heritage ground
  if (!small && view.cache?.marks) {
    const fs = Math.max(11, Math.min(20, board.grid.R * s * 0.42));
    ctx.font = `${fs}px system-ui, sans-serif`;
    ctx.textAlign = "center";
    for (const i of view.cache.marks) {
      const t = board.tiles[i];
      const x = sx(t.x) + board.grid.R * s * 0.36, y = sy(t.y) - board.grid.R * s * 0.36;
      if (!onScreen(x, y)) continue;
      ctx.globalAlpha = t.lux && !g.imp[i] ? 0.75 : 1;
      ctx.fillText(t.lux ? LUXURIES[t.lux].emoji : "🏛️", x, y);
    }
    ctx.globalAlpha = 1;
  }
  // suggested city sites for a selected pioneer
  for (const [k, i] of (view.settle || []).entries()) {
    const t = board.tiles[i];
    const x = sx(t.x), y = sy(t.y);
    if (!onScreen(x, y)) continue;
    ctx.beginPath();
    ctx.arc(x, y, small ? 8 : k === 0 ? 15 : 12, 0, Math.PI * 2);
    ctx.fillStyle = k === 0 ? "rgba(236,208,142,0.95)" : "rgba(236,208,142,0.6)";
    ctx.fill();
    ctx.fillStyle = "#2a2216";
    ctx.font = `700 ${small ? 10 : k === 0 ? 17 : 14}px system-ui, sans-serif`;
    ctx.textAlign = "center";
    ctx.fillText("★", x, y + 0.5);
    if (k === 0 && !small) {
      ctx.font = "700 11px system-ui, sans-serif";
      const w = ctx.measureText("Good site").width + 10;
      ctx.fillStyle = "rgba(20,22,28,0.85)";
      roundRect(ctx, x - w / 2, y + 17, w, 16, 8);
      ctx.fill();
      ctx.fillStyle = "#ecd08e";
      ctx.fillText("Good site", x, y + 25.5);
    }
  }
  // yields on the city's own hexes (close-up city screen)
  if (view.cityView && !view.placing) {
    const cv = view.cityView;
    ctx.font = "600 11px system-ui, sans-serif";
    ctx.textAlign = "center";
    const built = new Set(Object.values(cv.sites || {}));
    for (const i of cv.tiles) {
      if (i === cv.cell || built.has(i)) continue;
      const y = tileYield(g, board, i);
      const label = ["food", "prod", "gold", "sci", "cult"].filter((k) => y[k]).map((k) => `${y[k]}${YIELD_EMOJI[k]}`).join(" ");
      if (!label) continue;
      const x = sx(board.tiles[i].x), yy = sy(board.tiles[i].y) + board.grid.R * s * 0.45;
      const w = ctx.measureText(label).width + 8;
      ctx.fillStyle = "rgba(20,22,28,0.72)";
      roundRect(ctx, x - w / 2, yy - 8, w, 16, 8);
      ctx.fill();
      ctx.fillStyle = "#f4efe2";
      ctx.fillText(label, x, yy + 0.5);
      if ((cv.locked || []).includes(i)) ctx.fillText("🔒", x, sy(board.tiles[i].y) - board.grid.R * s * 0.45);
    }
    ctx.font = "700 12px system-ui, sans-serif";
    const gold = g.factions[g.human].gold;
    for (const o of view.buy || []) {
      const x = sx(board.tiles[o.cell].x), yy = sy(board.tiles[o.cell].y);
      const label = `Buy ${o.cost}🪙`;
      const w = ctx.measureText(label).width + 10;
      ctx.fillStyle = gold >= o.cost ? "rgba(70,56,18,0.9)" : "rgba(30,28,24,0.8)";
      roundRect(ctx, x - w / 2, yy - 9, w, 18, 9);
      ctx.fill();
      ctx.fillStyle = gold >= o.cost ? "#ffe08a" : "#9a917c";
      ctx.fillText(label, x, yy + 0.5);
    }
  }
  // cities
  for (const c of g.cities) {
    if (!seen(c.cell)) continue;
    const t = board.tiles[c.cell];
    const x = sx(t.x), y = sy(t.y);
    if (!onScreen(x, y)) continue;
    const col = g.factions[c.f].color;
    let r = cityRadius(cityDrawR(board.grid.R, c.pop, s), c.pop) * s;
    if (small) {
      r = 5;
      ctx.beginPath();
      ctx.arc(x, y, r, 0, Math.PI * 2);
      ctx.fillStyle = col;
      ctx.fill();
      ctx.lineWidth = 2;
      ctx.strokeStyle = "rgba(255,255,255,0.9)";
      ctx.stroke();
    }
    if (sel?.city === c) {
      ctx.beginPath();
      ctx.arc(x, y, r + 4, 0, Math.PI * 2);
      ctx.strokeStyle = "#fff4b0";
      ctx.lineWidth = 2.5;
      ctx.stroke();
    }
    // banner
    ctx.font = `600 ${small ? 10 : 12}px system-ui, sans-serif`;
    const label = c.name;
    const tw = ctx.measureText(label).width;
    const pw = 18, h = small ? 15 : 18;
    const bx = x - (tw + pw + 10) / 2, by = y - r - h - (c.capital && !small ? 14 : 4);
    ctx.fillStyle = "rgba(20,22,28,0.82)";
    roundRect(ctx, bx, by, tw + pw + 10, h, 4);
    ctx.fill();
    ctx.fillStyle = col;
    roundRect(ctx, bx, by, pw, h, 4);
    ctx.fill();
    ctx.fillStyle = "#fff";
    ctx.textAlign = "center";
    ctx.fillText(String(c.pop), bx + pw / 2, by + h / 2 + 0.5);
    ctx.textAlign = "left";
    ctx.fillStyle = "#f4efe2";
    ctx.fillText(label, bx + pw + 5, by + h / 2 + 0.5);
    if (c.capital) {
      ctx.fillStyle = "#ecd08e";
      ctx.textAlign = "center";
      ctx.fillText("★", bx + tw + pw + 13, by + h / 2 + 0.5);
      ctx.textAlign = "left";
    }
    // my cities: what is being made and when the city grows
    const inf = view.cache?.info?.get(c.id);
    if (inf && !small) {
      ctx.font = "600 11px system-ui, sans-serif";
      const line = `${inf.build}${inf.bturns}  ${inf.grow}`;
      const lw = ctx.measureText(line).width + 10;
      ctx.fillStyle = "rgba(20,22,28,0.72)";
      roundRect(ctx, x - lw / 2, by + h + 1, lw, 15, 4);
      ctx.fill();
      ctx.fillStyle = "#e8dcc0";
      ctx.textAlign = "center";
      ctx.fillText(line, x, by + h + 9);
      ctx.textAlign = "left";
    }
  }
  // placement labels (close-up city screen)
  if (view.placing?.options) {
    ctx.textAlign = "center";
    for (const o of view.placing.options) {
      const t = board.tiles[o.cell];
      const x = sx(t.x), y = sy(t.y) - (o.centre ? board.grid.R * s * 0.62 : 0);
      const parts = Object.entries(o.bonus).map(([k, v]) => `+${v} ${YIELD_EMOJI[k]}`);
      const label = o.centre ? "City centre" : parts.length ? parts.join(" ") : "no bonus";
      ctx.font = "700 13px system-ui, sans-serif";
      const w = ctx.measureText(label).width + 14;
      ctx.fillStyle = parts.length ? "rgba(24,60,24,0.9)" : "rgba(20,22,28,0.85)";
      roundRect(ctx, x - w / 2, y - 11, w, 22, 11);
      ctx.fill();
      ctx.fillStyle = parts.length ? "#c8f5b4" : "#e8dcc0";
      ctx.fillText(label, x, y + 0.5);
    }
  }
  const now = performance.now();
  // build progress rings with a live countdown, on my construction sites
  if (!small) for (const st of view.cache?.sites || []) {
    if (!st.mine) continue;
    const t = board.tiles[st.cell];
    const x = sx(t.x), y = sy(t.y) - (st.centre ? 0 : board.grid.R * s * 0.15);
    if (!onScreen(x, y)) continue;
    const p = Math.min(1, st.p0 + st.rate * view.frac);
    const rr = st.centre ? 13 : Math.max(13, Math.min(26, board.grid.R * s * 0.32));
    const cx = st.centre ? x + 30 : x, cy = st.centre ? y - 30 : y - rr - 6;
    ctx.beginPath();
    ctx.arc(cx, cy, rr, 0, Math.PI * 2);
    ctx.fillStyle = "rgba(20,22,28,0.78)";
    ctx.fill();
    ctx.beginPath();
    ctx.arc(cx, cy, rr - 2, -Math.PI / 2, -Math.PI / 2 + Math.PI * 2 * p);
    ctx.strokeStyle = "#f0a04b";
    ctx.lineWidth = 3.5;
    ctx.stroke();
    ctx.font = `${Math.round(rr * 0.9)}px system-ui, sans-serif`;
    ctx.textAlign = "center";
    ctx.fillStyle = "#fff";
    ctx.fillText("🏗️", cx, cy + 1);
    const left = st.rate > 0 ? ((1 - p) / st.rate) * view.msPerTurn / 1000 : Infinity;
    const lab = isFinite(left) ? (left >= 60 ? `${Math.floor(left / 60)}:${String(Math.ceil(left % 60)).padStart(2, "0")}` : `${Math.ceil(left)}s`) : "∞";
    ctx.font = "700 11px system-ui, sans-serif";
    const w = ctx.measureText(lab).width + 10;
    ctx.fillStyle = "rgba(20,22,28,0.85)";
    roundRect(ctx, cx - w / 2, cy + rr + 2, w, 15, 7);
    ctx.fill();
    ctx.fillStyle = "#ffd59a";
    ctx.fillText(lab, cx, cy + rr + 10);
  }
  // coin purses over my cities: tap the city to collect
  for (const pu of view.cache?.purses || []) {
    const t = board.tiles[pu.c.cell];
    const x = sx(t.x) - (small ? 12 : 34), y = sy(t.y) - (small ? 14 : 34);
    if (!onScreen(x, y)) continue;
    const pulse = pu.full >= 1 ? 1 + 0.08 * Math.sin(now / 150) : 1;
    const r = (small ? 8 : 12 + 6 * Math.min(1, pu.full)) * pulse;
    ctx.beginPath();
    ctx.arc(x, y, r, 0, Math.PI * 2);
    const grad = ctx.createRadialGradient(x - r * 0.3, y - r * 0.3, r * 0.2, x, y, r);
    grad.addColorStop(0, "#fff3b0");
    grad.addColorStop(1, pu.full >= 1 ? "#e0a020" : "#d9b23a");
    ctx.fillStyle = grad;
    ctx.fill();
    ctx.strokeStyle = "#7a5410";
    ctx.lineWidth = 2;
    ctx.stroke();
    ctx.fillStyle = "#4a3208";
    ctx.textAlign = "center";
    ctx.font = `800 ${small ? 9 : 12}px system-ui, sans-serif`;
    ctx.fillText(String(pu.coins), x, y + 1);
  }
  // a pointer at whatever the next-step button is about
  if (view.stepCell >= 0 && !view.cityView) {
    const t = board.tiles[view.stepCell];
    const x = sx(t.x), y = sy(t.y) - (small ? 16 : 40) + Math.sin(now / 220) * 5;
    if (onScreen(x, y)) {
      ctx.beginPath();
      ctx.moveTo(x, y + 12); ctx.lineTo(x - 9, y); ctx.lineTo(x - 4, y); ctx.lineTo(x - 4, y - 10);
      ctx.lineTo(x + 4, y - 10); ctx.lineTo(x + 4, y); ctx.lineTo(x + 9, y); ctx.closePath();
      ctx.fillStyle = "#ffe27a";
      ctx.fill();
      ctx.strokeStyle = "#5a4210";
      ctx.lineWidth = 1.5;
      ctx.stroke();
    }
  }
  // units, grouped by cell
  const byCell = new Map();
  for (const u of g.units) {
    if (!visible(u.cell)) continue;
    if (!byCell.has(u.cell)) byCell.set(u.cell, []);
    byCell.get(u.cell).push(u);
  }
  ctx.textAlign = "center";
  for (const [cell, list] of byCell) {
    const t = board.tiles[cell];
    const c = g.cityAt[cell] >= 0 ? g.cities[g.cityAt[cell]] : null;
    const r = small ? 8 : 14;
    const x0 = sx(t.x), y0 = sy(t.y) + (c ? (small ? 10 : cityRadius(cityDrawR(board.grid.R, c.pop, s), c.pop) * s - 6) : 0);
    if (!onScreen(x0, y0)) continue;
    list.forEach((u, k) => {
      let x = x0 + (k - (list.length - 1) / 2) * (2 * r + 3), y = y0;
      const an = view.anim.get(u.id);
      if (an) { // glide in from where it was
        const e = Math.min(1, (now - an.t0) / an.dur);
        const ease = 1 - (1 - e) * (1 - e);
        const ft = board.tiles[an.from];
        x += (sx(ft.x) - sx(t.x)) * (1 - ease);
        y += (sy(ft.y) - sy(t.y)) * (1 - ease) - Math.sin(ease * Math.PI) * 6;
        if (e >= 1) view.anim.delete(u.id);
      }
      drawUnitToken(ctx, u.type, x, y, r, g.factions[u.f].color, { dim: u.f === me && u.moves <= 0, selected: sel?.unit === u });
    });
  }
}

/** Short-lived effects: completion "plops", floating numbers, goal celebrations. Screen space. */
export function drawFx(ctx, cam, view) {
  const { board } = view;
  if (!board || !view.fx.length) return;
  const now = performance.now();
  const s = cam.scale;
  const sx = (x) => (x - cam.x) * s + cam.w / 2, sy = (y) => (y - cam.y) * s + cam.h / 2;
  view.fx = view.fx.filter((f) => now - f.t0 < f.dur);
  for (const f of view.fx) {
    const e = (now - f.t0) / f.dur;
    const t = board.tiles[f.cell];
    const x = sx(t.x), y = sy(t.y);
    ctx.save();
    if (f.kind === "plop") {
      // ring of light, dust, and a bouncing label
      ctx.globalAlpha = 1 - e;
      ctx.beginPath();
      ctx.arc(x, y, 10 + 70 * e, 0, Math.PI * 2);
      ctx.strokeStyle = f.color || "#ffe9a8";
      ctx.lineWidth = 4 * (1 - e) + 1;
      ctx.stroke();
      for (let k = 0; k < 10; k++) {
        const a = (k / 10) * Math.PI * 2 + f.seed;
        const d = 18 + 45 * e;
        ctx.beginPath();
        ctx.arc(x + Math.cos(a) * d, y + Math.sin(a) * d * 0.6 + 10 * e, 5 * (1 - e) + 1, 0, Math.PI * 2);
        ctx.fillStyle = "rgba(220,200,160,0.8)";
        ctx.fill();
      }
    }
    if (f.text) {
      const rise = f.kind === "plop" ? 50 : 40;
      const bounce = f.kind === "plop" ? Math.max(0, Math.sin(Math.min(1, e * 3) * Math.PI)) * 8 : 0;
      ctx.globalAlpha = e < 0.75 ? 1 : (1 - e) / 0.25;
      ctx.font = `800 ${f.big ? 18 : 14}px system-ui, sans-serif`;
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      const ty = y - 30 - rise * e - bounce;
      const w = ctx.measureText(f.text).width + 16;
      ctx.fillStyle = "rgba(20,22,28,0.85)";
      roundRect(ctx, x - w / 2, ty - 13, w, 26, 13);
      ctx.fill();
      ctx.fillStyle = f.color || "#fff0b4";
      ctx.fillText(f.text, x, ty + 1);
    }
    ctx.restore();
  }
}

function roundRect(ctx, x, y, w, h, r) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}

export { SQRT3 };
