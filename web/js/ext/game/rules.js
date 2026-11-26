// Game rules: the board built from the real map data, and the catalogue of units, buildings, improvements and techs.
// Pure functions and constants only (no DOM), so the simulation also runs under Node for tests.
//
// The board is Iran's real terrain (world.json): land cover, relief, rivers, lakes and coasts set each hex's yields.
// The setting is ancient (about 3000 BCE to 1500 CE) and the peoples are invented, so nothing modern is used:
// no provinces, cities, roads or oil. Real mines (resources.json) become ore deposits and World Heritage sites
// (wonders.json) become heritage ground, because both are properties of the land itself.

export const SQRT3 = Math.sqrt(3);
const NB = [
  [[1, 0], [0, 1], [-1, 1], [-1, 0], [-1, -1], [0, -1]],
  [[1, 0], [1, 1], [0, 1], [-1, 0], [0, -1], [1, -1]],
];

export const YIELD_KEYS = ["food", "prod", "gold", "sci", "cult"];
export const YIELD_INFO = {
  food: { label: "Food", emoji: "🌾", color: "#7fbf3f" },
  prod: { label: "Production", emoji: "⚒️", color: "#d9823b" },
  gold: { label: "Gold", emoji: "🪙", color: "#f2c94c" },
  sci: { label: "Science", emoji: "📜", color: "#4fa3e0" },
  cult: { label: "Culture", emoji: "🏺", color: "#b07ce0" },
};

export const START_YEAR = -3000;
export const TURN_YEARS = 30;
export const MAX_TURNS = 150;
export const yearOf = (turn) => START_YEAR + turn * TURN_YEARS;
export const yearLabel = (turn) => {
  const y = yearOf(turn);
  return y < 0 ? `${-y} BCE` : y === 0 ? "1 CE" : `${y} CE`;
};

// Invented peoples. Names and colours are original; none stands for a real group.
export const PEOPLES = [
  { id: "kavari", name: "Kavari", adj: "Kavari", color: "#d8583c", leaning: "builder", cities: ["Kavar", "Tesh", "Ombal", "Rudan", "Siravel", "Pashto", "Velkar", "Imran", "Dorvi", "Ashal", "Mirreh", "Talun"] },
  { id: "selenet", name: "Selenet", adj: "Selenet", color: "#3c8fd8", leaning: "trader", cities: ["Selen", "Arvo", "Kesh", "Lomira", "Navesh", "Tirva", "Orsal", "Benu", "Hadrel", "Quessa", "Mavel", "Zorin"] },
  { id: "durvash", name: "Durvash", adj: "Durvash", color: "#5aa64a", leaning: "expander", cities: ["Durva", "Kelm", "Hosran", "Pirak", "Valesh", "Morun", "Sakra", "Elvan", "Garro", "Ishel", "Tumar", "Feyr"] },
  { id: "amarin", name: "Amarin", adj: "Amarin", color: "#c79a2a", leaning: "scholar", cities: ["Amar", "Lisha", "Corvan", "Nedda", "Ruvel", "Ostra", "Pelim", "Yarvo", "Dessa", "Hamir", "Solva", "Kiran"] },
];

// --- techs -------------------------------------------------------------------------------------------------------
export const ERAS = ["Ancient", "Classical", "Medieval"];
export const TECHS = [
  { id: "pottery", name: "Pottery", era: 0, cost: 30, req: [], unlocks: "Granary" },
  { id: "irrigation", name: "Irrigation", era: 0, cost: 35, req: [], unlocks: "Farms away from rivers" },
  { id: "bronze", name: "Bronze Working", era: 0, cost: 40, req: [], unlocks: "Mines, Guard" },
  { id: "writing", name: "Writing", era: 0, cost: 50, req: ["pottery"], unlocks: "Library, Envoy" },
  { id: "trade", name: "Trade", era: 1, cost: 165, req: ["pottery", "bronze"], unlocks: "Market, Caravan" },
  { id: "qanat", name: "Qanats", era: 1, cost: 180, req: ["irrigation", "bronze"], unlocks: "Qanats on dry land" },
  { id: "masonry", name: "Masonry", era: 1, cost: 155, req: ["bronze"], unlocks: "Workshop; halves earthquake damage risk" },
  { id: "mysticism", name: "Mysticism", era: 1, cost: 170, req: ["writing"], unlocks: "Fire Temple" },
  { id: "mathematics", name: "Mathematics", era: 1, cost: 205, req: ["writing", "masonry"], unlocks: "Aqueduct" },
  { id: "roads", name: "Royal Roads", era: 1, cost: 200, req: ["trade", "masonry"], unlocks: "Roads, Caravanserai" },
  { id: "philosophy", name: "Philosophy", era: 2, cost: 780, req: ["mysticism", "mathematics"], unlocks: "Academy" },
  { id: "paper", name: "Paper", era: 2, cost: 780, req: ["writing", "trade"], unlocks: "House of Wisdom" },
  { id: "astronomy", name: "Astronomy", era: 2, cost: 1400, req: ["philosophy", "mathematics"], unlocks: "Observatory" },
  { id: "banking", name: "Banking", era: 2, cost: 900, req: ["roads", "paper"], unlocks: "Bazaar" },
  { id: "medicine", name: "Medicine", era: 2, cost: 900, req: ["philosophy", "qanat"], unlocks: "Bimaristan" },
];
export const TECH = Object.fromEntries(TECHS.map((t) => [t.id, t]));

// --- units -------------------------------------------------------------------------------------------------------
export const UNITS = {
  pioneer: { name: "Pioneer", emoji: "🏕️", letter: "P", cost: 32, moves: 2, sight: 2, minPop: 2, desc: "Founds a new city (uses the Pioneer and 1 population)." },
  builder: { name: "Builder", emoji: "⛏️", letter: "B", cost: 22, moves: 2, sight: 1, charges: 3, desc: "Builds farms, mines, qanats and roads (3 uses)." },
  scout: { name: "Scout", emoji: "🧭", letter: "S", cost: 12, moves: 3, sight: 3, desc: "Explores. Sees far." },
  guard: { name: "Guard", emoji: "🛡️", letter: "G", cost: 20, moves: 1, sight: 2, tech: "bronze", desc: "Holds its hex: no rival unit can enter it, and no rival may found a city next to it." },
  envoy: { name: "Envoy", emoji: "🕊️", letter: "E", cost: 25, moves: 3, sight: 2, tech: "writing", desc: "Opens an embassy: move it into a rival city. Both peoples gain Science and Culture, and you get +2 Science a turn from then on." },
  caravan: { name: "Caravan", emoji: "🐪", letter: "C", cost: 30, moves: 2, sight: 1, tech: "trade", desc: "Open a trade route: move it into another city (yours or a rival's) for lasting Gold in both." },
};

// --- buildings ---------------------------------------------------------------------------------------------------
export const BUILDINGS = {
  granary: { name: "Granary", cost: 35, tech: "pottery", yields: { food: 2 }, desc: "+2 Food" },
  library: { name: "Library", cost: 45, tech: "writing", yields: { sci: 2 }, pct: { sci: 0.25 }, desc: "+2 Science, +25% Science" },
  market: { name: "Market", cost: 50, tech: "trade", yields: { gold: 2 }, pct: { gold: 0.25 }, desc: "+2 Gold, +25% Gold" },
  workshop: { name: "Workshop", cost: 50, tech: "masonry", yields: { prod: 2 }, desc: "+2 Production" },
  temple: { name: "Fire Temple", cost: 50, tech: "mysticism", yields: { cult: 2 }, desc: "+2 Culture" },
  aqueduct: { name: "Aqueduct", cost: 70, tech: "mathematics", growth: 0.25, desc: "Cities grow 25% faster" },
  caravanserai: { name: "Caravanserai", cost: 60, tech: "roads", yields: { gold: 2 }, routes: 1, desc: "+2 Gold, +1 Gold per trade route" },
  academy: { name: "Academy", cost: 90, tech: "philosophy", yields: { sci: 3 }, pct: { sci: 0.25 }, desc: "+3 Science, +25% Science" },
  wisdom: { name: "House of Wisdom", cost: 100, tech: "paper", yields: { sci: 2, cult: 2 }, desc: "+2 Science, +2 Culture" },
  bazaar: { name: "Bazaar", cost: 100, tech: "banking", yields: { gold: 3 }, pct: { gold: 0.25 }, desc: "+3 Gold, +25% Gold" },
  bimaristan: { name: "Bimaristan", cost: 90, tech: "medicine", yields: { food: 2 }, growth: 0.25, desc: "+2 Food, cities grow 25% faster" },
  garden: { name: "Garden of Four Streams", cost: 260, tech: "mysticism", wonder: true, yields: { cult: 5, food: 2 }, desc: "Wonder: +5 Culture, +2 Food. A walled garden split by four water channels." },
  observatory: { name: "Great Observatory", cost: 600, tech: "astronomy", wonder: true, yields: { sci: 6 }, desc: "Wonder: +6 Science. Needed for a Science victory." },
};

// --- improvements -------------------------------------------------------------------------------------------------
// can(board, i, has) → bool; has(techId) → bool.
export const IMPROVEMENTS = {
  farm: { name: "Farm", emoji: "🌾", yields: { food: 1 }, desc: "+1 Food. Next to fresh water, or anywhere with Irrigation." },
  mine: { name: "Mine", emoji: "⛏️", yields: { prod: 2 }, tech: "bronze", desc: "+2 Production on hills, mountains or ore (+1 more on ore)." },
  qanat: { name: "Qanat", emoji: "💧", yields: { food: 2 }, tech: "qanat", desc: "+2 Food on dry land, from water led underground from the hills." },
  road: { name: "Road", emoji: "🛤️", yields: { gold: 1 }, tech: "roads", desc: "+1 Gold, and moving along it costs less." },
};

export function canImprove(board, i, kind, has) {
  const t = board.tiles[i];
  if (!t || !t.land || t.city) return false;
  const imp = IMPROVEMENTS[kind];
  if (imp.tech && !has(imp.tech)) return false;
  if (kind === "road") return !t.road;
  if (t.improvement) return false;
  if (kind === "farm") return (t.cover === "cropland" || t.cover === "grassland" || t.cover === "pasture" || t.cover === "urban" || t.cover === "scrub") && t.relief <= 1 && (t.fresh || has("irrigation"));
  if (kind === "mine") return t.relief >= 1 || t.ore;
  if (kind === "qanat") return t.relief === 0 && !t.fresh && (t.cover === "barren" || t.cover === "scrub" || t.cover === "grassland") && t.nearHills;
  return false;
}

// --- the board ------------------------------------------------------------------------------------------------------

export function makeGrid({ radius: R, cols, rows }) {
  const W = SQRT3 * R, H = 1.5 * R;
  return {
    R, W, H, cols, rows, size: cols * rows,
    cx: (i) => W * ((i % cols) + 0.5 * (((i / cols) | 0) & 1)),
    cy: (i) => H * ((i / cols) | 0),
    neighbour(i, d) {
      const c = i % cols, r = (i / cols) | 0;
      const [dc, dr] = NB[r & 1][d];
      const nc = c + dc, nr = r + dr;
      return nc < 0 || nr < 0 || nc >= cols || nr >= rows ? -1 : nr * cols + nc;
    },
    cellAt(x, y) {
      const q = ((SQRT3 / 3) * x - y / 3) / R, r = ((2 / 3) * y) / R;
      let rx = Math.round(q), rz = Math.round(r);
      const ry = Math.round(-q - r);
      const dx = Math.abs(rx - q), dy = Math.abs(ry - (-q - r)), dz = Math.abs(rz - r);
      if (dx > dy && dx > dz) rx = -ry - rz;
      else if (dy <= dz) rz = -rx - ry;
      const row = rz, col = rx + ((row - (row & 1)) >> 1);
      return col < 0 || row < 0 || col >= cols || row >= rows ? -1 : row * cols + col;
    },
  };
}

/** Hex distance between two cells (odd-r offset → cube). */
export function hexDist(grid, a, b) {
  const cube = (i) => {
    const r = (i / grid.cols) | 0, c = i % grid.cols;
    const q = c - ((r - (r & 1)) >> 1);
    return [q, r, -q - r];
  };
  const [aq, ar, as] = cube(a), [bq, br, bs] = cube(b);
  return Math.max(Math.abs(aq - bq), Math.abs(ar - br), Math.abs(as - bs));
}

/** Cells within `radius` steps of `i` (including i), by breadth-first rings. */
export function ring(grid, i, radius) {
  const out = [i], seen = new Set([i]);
  let frontier = [i];
  for (let k = 0; k < radius; k++) {
    const next = [];
    for (const c of frontier) for (let d = 0; d < 6; d++) {
      const n = grid.neighbour(c, d);
      if (n >= 0 && !seen.has(n)) { seen.add(n); next.push(n); out.push(n); }
    }
    frontier = next;
  }
  return out;
}

const COVER_KEYS = ["none", "forest_deciduous", "forest_evergreen", "forest_mixed", "scrub", "grassland", "pasture", "cropland", "wetland", "urban", "barren", "ice"];
const COVER_NAME = { forest: "Forest", scrub: "Scrub & steppe", grassland: "Grassland", pasture: "Pasture", cropland: "Fertile plain", wetland: "Marsh", urban: "Fertile plain", barren: "Desert", ice: "Snowfield", none: "Land" };
const RELIEF_NAME = ["", "Hills", "Mountains", "Peaks"];

/**
 * Build the board from world.json (+ optional resources.json, wonders.json). Playable land = Iranian land hexes
 * (owner >= 0); neighbouring countries and the sea are off the board.
 */
export function buildBoard(world, { resources = null, wonders = null } = {}) {
  const grid = makeGrid(world.grid);
  const n = grid.size;
  const tables = world.tables || {};
  const coverKey = (id) => {
    const row = (tables.cover || []).find((c) => c.id === id);
    const k = row?.key || COVER_KEYS[id] || "none";
    return k.startsWith("forest") ? "forest" : k;
  };
  // Rivers: cells a river polyline passes through (sampled every 4 km).
  const river = new Uint8Array(n);
  for (const r of world.rivers || []) {
    const p = r.pts;
    for (let k = 0; k + 3 < p.length; k += 2) {
      const [x0, y0, x1, y1] = [p[k], p[k + 1], p[k + 2], p[k + 3]];
      const steps = Math.max(1, Math.ceil(Math.hypot(x1 - x0, y1 - y0) / 4));
      for (let s = 0; s <= steps; s++) {
        const c = grid.cellAt(x0 + ((x1 - x0) * s) / steps, y0 + ((y1 - y0) * s) / steps);
        if (c >= 0) river[c] = 1;
      }
    }
  }
  const ore = new Map();
  for (const it of resources?.items || []) {
    const t = resources.types?.find((x) => x.id === it.type);
    if (t?.category === "mineral" && it.cell >= 0) ore.set(it.cell, t.name.replace(/ mine$/i, ""));
  }
  const heritage = new Map();
  for (const wd of wonders?.wonders || []) if (wd.rule === "landmark" && wd.cell >= 0) heritage.set(wd.cell, wd.name);

  const tiles = new Array(n);
  for (let i = 0; i < n; i++) {
    const land = world.owner[i] >= 0;
    const water = world.water[i];
    tiles[i] = { i, land, water, x: grid.cx(i), y: grid.cy(i) };
    if (!land) continue;
    const t = tiles[i];
    t.relief = world.relief[i] | 0;
    t.cover = coverKey(world.cover[i]);
    t.elev = world.elev[i];
    t.river = !!river[i];
    t.ore = ore.get(i) || null;
    t.heritage = heritage.get(i) || null;
    t.crop = world.pct?.crop?.[i] ?? 0;
  }
  for (let i = 0; i < n; i++) {
    const t = tiles[i];
    if (!t.land) continue;
    let coast = false, lake = false, hills = false;
    for (let d = 0; d < 6; d++) {
      const j = grid.neighbour(i, d);
      if (j < 0) continue;
      const u = tiles[j];
      if (u.water === 3) lake = true;
      else if (u.water) coast = true;
      if (u.land && u.relief >= 1) hills = true;
    }
    t.coast = coast;
    t.fresh = t.river || lake;
    t.nearHills = hills || t.relief >= 1;
    t.passable = t.relief < 3;
    t.base = baseYields(t);
    t.name = tileName(t);
  }
  return { grid, tiles, n, landCount: tiles.filter((t) => t.land).length };
}

export function baseYields(t) {
  const y = { food: 0, prod: 0, gold: 0, sci: 0, cult: 0 };
  switch (t.cover) {
    case "cropland": case "urban": y.food = 2; break;
    case "grassland": case "pasture": y.food = 2; break;
    case "forest": y.food = 1; y.prod = 1; break;
    case "scrub": y.food = 1; break;
    case "wetland": y.food = 1; break;
    default: y.food = 0;
  }
  if (t.crop >= 30) y.food += 1;
  if (t.relief === 1) y.prod += 1;
  if (t.relief === 2) { y.prod += 2; y.food = Math.max(0, y.food - 1); }
  if (t.fresh) { y.food += 1; y.gold += 1; }
  if (t.coast) y.gold += 1;
  if (t.ore) y.prod += 1;
  if (t.heritage) y.cult += 2;
  return y;
}

export function tileName(t) {
  const parts = [];
  if (t.relief) parts.push(RELIEF_NAME[t.relief]);
  parts.push(COVER_NAME[t.cover] || "Land");
  let s = parts.join(", ");
  if (t.river) s += " · river";
  else if (t.fresh) s += " · lakeside";
  if (t.coast) s += " · coast";
  return s;
}

/** Movement cost to enter a tile (Infinity = impassable). */
export function moveCost(t) {
  if (!t || !t.land || !t.passable) return Infinity;
  if (t.road) return 0.5;
  let c = 1;
  if (t.relief === 1 || t.cover === "forest" || t.cover === "wetland") c = 2;
  if (t.relief === 2) c = 3;
  return c;
}

/** How good a hex is as a city site: its own yields + the best of its neighbours within 2 steps. */
export function siteScore(board, i) {
  const t = board.tiles[i];
  if (!t.land || !t.passable || t.relief >= 2) return -1;
  const sum = (y) => y.food * 3 + y.prod * 2 + y.gold + y.cult;
  let s = sum(t.base) * 2 + (t.fresh ? 6 : 0) + (t.coast ? 2 : 0);
  const near = ring(board.grid, i, 2).slice(1).map((j) => board.tiles[j]).filter((u) => u.land).map((u) => sum(u.base));
  near.sort((a, b) => b - a);
  for (let k = 0; k < Math.min(8, near.length); k++) s += near[k];
  return s;
}
