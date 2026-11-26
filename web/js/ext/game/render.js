// Map overlays for the game: fog of war, borders, improvements and roads (world space, under the labels), and
// cities, units, selection and move range (screen space, on top). They read `view` (set by ui.js).

import { SQRT3, UNITS } from "./rules.js";
import { visibleSet, has, cityYields } from "./game.js";
import { drawAncientCity, drawUnitToken, drawBuilding, cityRadius, cityDrawR } from "./art.js";

const YIELD_EMOJI = { food: "🌾", prod: "⚒️", gold: "🪙", sci: "📜", cult: "🏺" };

export function createView() {
  return { g: null, board: null, sel: null, reach: null, vis: null, version: 0, cache: null, hover: -1 };
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
  return { version: view.version, fogDark, fogDim, fills, edges, farms, mines, qanats, roads };
}

/** World-space layer: territory, improvements, fog. */
export function drawWorld(ctx, cam, view) {
  if (!view.g) return;
  if (!view.cache || view.cache.version !== view.version) view.cache = buildCache(view);
  const c = view.cache;
  const { g } = view;
  const px = 1 / cam.scale; // one screen pixel in km
  g.factions.forEach((F, f) => {
    ctx.fillStyle = hexToRgba(F.color, 0.2);
    ctx.fill(c.fills[f]);
  });
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
  // cities
  ctx.textBaseline = "middle";
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
      const x = x0 + (k - (list.length - 1) / 2) * (2 * r + 3), y = y0;
      drawUnitToken(ctx, u.type, x, y, r, g.factions[u.f].color, { dim: u.f === me && u.moves <= 0, selected: sel?.unit === u });
    });
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
