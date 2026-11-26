// Game state and turn engine. Pure (no DOM): the browser UI and the Node tests drive the same functions.
//
// State `g` is plain data plus a few typed arrays, so it saves to JSON (see save/load). The board (rules.buildBoard)
// is the fixed real terrain; the per-hex things that change in play (cities, territory, farms, roads) live in `g`
// and are mirrored onto board.tiles by syncBoard() so rules.canImprove / moveCost can read them.

import {
  PEOPLES, TECHS, TECH, UNITS, BUILDINGS, IMPROVEMENTS, MAX_TURNS, YIELD_KEYS,
  LEANINGS, LUXURIES, upkeepOf, canImprove, hexDist, ring, moveCost, siteScore, yearLabel,
} from "./rules.js";

export const WIN = { heritage: 4, culture: 2600, gold: 9500, pop: 72 };
const CITY_MIN_DIST = 4; // hexes between city centres
const CLAIM_RADIUS = 3; // how far a city's borders can grow

// --- random --------------------------------------------------------------------------------------------------------
function rand(g) {
  // mulberry32, state in g.rng so a saved game continues identically
  let t = (g.rng = (g.rng + 0x6d2b79f5) | 0);
  t = Math.imul(t ^ (t >>> 15), t | 1);
  t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
  return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
}

// --- setup -----------------------------------------------------------------------------------------------------------

/**
 * A new game on `board`. opts: {seed, peoples (count, 2–4), human (index of the human faction, or -1 for an
 * all-AI game), quakes (per-cell M5.5+ counts since 1900, from lenses.json)}.
 */
export function newGame(board, { seed = (Date.now() & 0x7fffffff), peoples = 4, human = 0, quakes = null } = {}) {
  const n = board.n;
  const g = {
    v: 1, seed, rng: seed | 0, turn: 0, human, nextId: 1, over: null,
    factions: [], cities: [], units: [], log: [],
    terr: new Int16Array(n).fill(-1), // city id owning the hex
    imp: new Array(n).fill(null), // improvement kind
    road: new Uint8Array(n),
    cityAt: new Int16Array(n).fill(-1),
    quakes: quakes ? Array.from(quakes, (v) => v || 0) : null,
  };
  const starts = pickStarts(g, board, peoples);
  for (let k = 0; k < starts.length; k++) {
    const p = PEOPLES[k];
    const f = {
      k, id: p.id, name: p.name, color: p.color, leaning: p.leaning, ai: k !== human,
      techs: {}, research: null, progress: {}, gold: 10, goldEarned: 0, culture: 0, nameIdx: 0,
      seen: new Uint8Array(n), met: {}, embassy: {}, wonders: [], start: starts[k],
    };
    g.factions.push(f);
    addUnit(g, k, "pioneer", starts[k]);
    addUnit(g, k, "scout", starts[k]);
  }
  syncBoard(g, board);
  for (const f of g.factions) vision(g, board, f.k);
  logMsg(g, -1, `${yearLabel(0)}. Four peoples wander the plateau, each looking for a place to settle.`);
  return g;
}

function pickStarts(g, board, count) {
  const cand = [];
  for (let i = 0; i < board.n; i++) {
    const t = board.tiles[i];
    if (!t.land || !t.passable || t.relief >= 2) continue;
    cand.push([i, siteScore(board, i)]);
  }
  cand.sort((a, b) => b[1] - a[1]);
  const top = cand.slice(0, Math.max(60, Math.floor(cand.length * 0.12)));
  for (let tries = 0; tries < 400; tries++) {
    const out = [];
    const minD = tries < 200 ? 16 : 11;
    const order = top.map((c) => [c[0], rand(g)]).sort((a, b) => a[1] - b[1]).map((c) => c[0]);
    for (const i of order) {
      if (out.every((j) => hexDist(board.grid, i, j) >= minD)) out.push(i);
      if (out.length === count) return out;
    }
  }
  return top.slice(0, count).map((c) => c[0]);
}

export function syncBoard(g, board) {
  Object.defineProperty(g, "_board", { value: board, writable: true, enumerable: false, configurable: true });
  const built = new Set();
  for (const c of g.cities) for (const v of Object.values(c.sites || {})) built.add(v);
  for (let i = 0; i < board.n; i++) {
    const t = board.tiles[i];
    if (!t.land) continue;
    t.improvement = g.imp[i];
    t.road = !!g.road[i];
    t.city = g.cityAt[i] >= 0 || built.has(i); // no farm or mine under a city or a building
  }
}

// --- helpers -----------------------------------------------------------------------------------------------------
export const has = (g, f, tech) => !!g.factions[f].techs[tech];
export const factionOf = (g, cell) => (g.terr[cell] >= 0 ? g.cities[g.terr[cell]].f : -1);
export const citiesOf = (g, f) => g.cities.filter((c) => c.f === f);
export const unitsOf = (g, f) => g.units.filter((u) => u.f === f);
export const unitsAt = (g, cell) => g.units.filter((u) => u.cell === cell);
export const cityAt = (g, cell) => (g.cityAt[cell] >= 0 ? g.cities[g.cityAt[cell]] : null);

function logMsg(g, f, text, cell = -1) {
  g.log.push({ t: g.turn, f, text, cell });
  if (g.log.length > 400) g.log.splice(0, g.log.length - 400);
}

function addUnit(g, f, type, cell, home = -1) {
  const u = { id: g.nextId++, f, type, cell, moves: UNITS[type].moves, dest: -1, home };
  if (UNITS[type].charges) u.charges = UNITS[type].charges;
  g.units.push(u);
  return u;
}

function removeUnit(g, u) {
  const k = g.units.indexOf(u);
  if (k >= 0) g.units.splice(k, 1);
}

// --- yields --------------------------------------------------------------------------------------------------------

export function tileYield(g, board, i) {
  const t = board.tiles[i];
  const y = { ...t.base };
  const imp = g.imp[i];
  if (imp) for (const [k, v] of Object.entries(IMPROVEMENTS[imp].yields)) y[k] += v;
  if (imp === "mine" && t.ore) y.prod += 1;
  if (g.road[i]) y.gold += 1;
  return y;
}

// hungry: needs food to eat; capped: cannot grow anyway (no housing, or growth would only add unhappy citizens).
const tileValue = (y, hungry, capped = false) => y.food * (hungry ? 4 : capped ? 0.6 : 2.5) + y.prod * 2 + y.gold * 1.2 + y.sci * 1.5 + y.cult * 1.2;

/** A city's yields this turn: centre + its best `pop` tiles inside its borders, then buildings and trade. */
export function cityYields(g, board, c) {
  const centre = tileYield(g, board, c.cell);
  const y = {
    food: Math.max(2, centre.food + 1), prod: Math.max(1, centre.prod + 1), gold: centre.gold + 1,
    sci: centre.sci + 1, cult: centre.cult + 1,
  };
  const pool = [];
  const built = builtCells(c);
  for (const i of c.tiles) if (i !== c.cell && !built.has(i)) pool.push([i, tileYield(g, board, i)]);
  const worked = [];
  let food = y.food;
  const locked = (c.locked || []).filter((i) => pool.some((p) => p[0] === i)).slice(0, c.pop);
  const mood = cityMood(g, board, c);
  const capped = c.pop >= mood.housing || c.pop >= mood.content;
  for (let k = 0; k < c.pop && pool.length; k++) {
    const hungry = food < 2 * c.pop + (capped ? 0 : 2);
    let best = 0;
    if (k < locked.length) best = pool.findIndex((p) => p[0] === locked[k]);
    else for (let j = 1; j < pool.length; j++) if (tileValue(pool[j][1], hungry, capped) > tileValue(pool[best][1], hungry, capped)) best = j;
    const [i, ty] = pool.splice(best, 1)[0];
    worked.push(i);
    for (const key of YIELD_KEYS) y[key] += ty[key];
    food += ty.food;
  }
  y.sci += Math.floor(c.pop / 2); // every two citizens keep one scholar
  const gift = LEANINGS[g.factions[c.f].leaning];
  if (gift) y[gift.yield] += gift.n || 1;
  const pct = { food: 0, prod: 0, gold: 0, sci: 0, cult: 0 };
  let growth = 0, routeBonus = 0;
  for (const b of Object.keys(c.blds)) {
    const B = BUILDINGS[b];
    for (const [k, v] of Object.entries(B.yields || {})) y[k] += v;
    for (const [k, v] of Object.entries(B.pct || {})) pct[k] += v;
    growth += B.growth || 0;
    routeBonus += B.routes || 0;
    const site = c.sites?.[b];
    if (site != null && site !== c.cell) for (const [k, v] of Object.entries(siteBonus(g, board, c, b, site))) y[k] += v;
  }
  for (const r of c.routes) y.gold += r.gold + routeBonus;
  for (const r of c.routes) if (r.foreign) y.sci += 1;
  const unrest = 0.1 * Math.min(5, mood.unhappy); // each unhappy citizen costs 10% of everything but food
  const broke = g.factions[c.f].broke > 0 ? 0.25 : 0;
  for (const k of YIELD_KEYS) {
    const cut = k === "food" ? 0 : unrest + (k === "prod" ? broke : 0);
    y[k] = Math.round(y[k] * (1 + pct[k]) * (1 - cut));
  }
  const eat = 2 * c.pop;
  return { ...y, eat, surplus: y.food - eat, worked, growth, need: growNeed(c.pop, growth), mood, unrest };
}

// --- contentment and housing ----------------------------------------------------------------------------------------
// Citizens beyond what a city can keep content are unhappy: the city stops growing and loses 10% of its Production,
// Gold, Science and Culture for each one. A city also stops growing when it runs out of housing.

/** Luxury kinds people f enjoys: a luxury hex inside their borders with any improvement on it. */
export function luxuriesOf(g, board, f) {
  const out = new Map();
  for (const c of g.cities) if (c.f === f) for (const i of c.tiles) {
    const L = board.tiles[i].lux;
    if (L && g.imp[i] && !out.has(L)) out.set(L, i);
  }
  return out;
}

export function cityMood(g, board, c) {
  const mine = citiesOf(g, c.f);
  const lux = luxuriesOf(g, board, c.f).size;
  const parts = [["Base", 4]];
  if (c.capital) parts.push(["Capital", 1]);
  if (lux) parts.push([`Luxuries (${lux})`, lux]);
  let bld = 0, all = 0;
  for (const b of Object.keys(c.blds)) bld += BUILDINGS[b].content || 0;
  for (const x of mine) for (const b of Object.keys(x.blds)) all += BUILDINGS[b].contentAll || 0;
  if (bld) parts.push(["Buildings", bld]);
  if (all) parts.push(["Wonders", all]);
  const size = Math.floor((mine.length - 1) / 3);
  if (size) parts.push([`Size of the realm (${mine.length} cities)`, -size]);
  const content = parts.reduce((a, p) => a + p[1], 0);
  // housing
  const t = board.tiles[c.cell];
  const hp = [["Base", 4]];
  if (t.fresh) hp.push(["Fresh water", 2]);
  else if (t.coast) hp.push(["Coast", 1]);
  let hb = 0;
  for (const b of Object.keys(c.blds)) hb += BUILDINGS[b].housing || 0;
  if (hb) hp.push(["Buildings", hb]);
  const qanats = Math.min(3, c.tiles.filter((i) => g.imp[i] === "qanat").length);
  if (qanats) hp.push(["Qanats", qanats]);
  const farms = Math.min(4, Math.floor(c.tiles.filter((i) => g.imp[i] === "farm").length / 2));
  if (farms) hp.push(["Farms", farms]);
  const housing = hp.reduce((a, p) => a + p[1], 0);
  return { content, unhappy: Math.max(0, c.pop - content), parts, housing, housingParts: hp };
}

// --- upkeep ----------------------------------------------------------------------------------------------------------
// Every ordinary building costs 1 Gold a turn. Each city supports one unit for free, plus two for the realm; every
// unit beyond that costs 1 Gold. A treasury that runs dry disbands a unit, or idles the workshops for two turns.

export function upkeep(g, f) {
  let blds = 0;
  const cs = citiesOf(g, f);
  for (const c of cs) for (const b of Object.keys(c.blds)) blds += upkeepOf(b);
  const n = unitsOf(g, f).length, free = cs.length + 2;
  const units = Math.max(0, n - free);
  return { blds, units, free, total: blds + units };
}

function payUpkeep(g, f) {
  const F = g.factions[f];
  if (F.broke > 0) F.broke--;
  F.gold -= upkeep(g, f).total;
  if (F.gold >= 0) return;
  F.gold = 0;
  const order = ["scout", "guard", "caravan", "envoy", "builder"];
  const u = unitsOf(g, f).filter((x) => order.includes(x.type)).sort((a, b) => order.indexOf(a.type) - order.indexOf(b.type) || b.id - a.id)[0];
  if (u && upkeep(g, f).units > 0) {
    removeUnit(g, u);
    logMsg(g, f, `The treasury is empty: a ${UNITS[u.type].name} could not be paid and went home.`, u.cell);
  } else {
    F.broke = 2;
    logMsg(g, f, `The treasury is empty: unpaid workers leave the workshops (-25% Production for 2 turns).`);
  }
}

// --- building sites -------------------------------------------------------------------------------------------------
// A building stands in the city centre (no bonus, no land lost) or on one of the city's own hexes within 2 steps.
// On a hex it takes that land out of farming but earns a bonus from what is around it, so where you build matters.

export const SITE_RULES = {
  granary: { yield: "food", text: "+1 Food per farm or fertile plain next to it (up to 3)" },
  library: { yield: "sci", text: "+1 Science per building next to it (up to 2), +1 by a river" },
  market: { yield: "gold", text: "+1 Gold on a river or road, +1 next to the city centre" },
  workshop: { yield: "prod", text: "+1 Production per hill, mountain or mine next to it (up to 3)" },
  temple: { yield: "cult", text: "+2 Culture on hills, +2 next to heritage ground" },
  aqueduct: { yield: "food", text: "+2 Food on a river or next to hills" },
  caravanserai: { yield: "gold", text: "+1 Gold per road next to it, +2 at the desert's edge" },
  academy: { yield: "sci", text: "+1 Science per building next to it (up to 3)" },
  wisdom: { yield: "sci", text: "+1 Science per building next to it (up to 3)" },
  bazaar: { yield: "gold", text: "+1 Gold per building next to it (up to 3), +1 on a road" },
  bimaristan: { yield: "food", text: "+2 Food by fresh water" },
  garden: { yield: "cult", text: "+3 Culture by a river or lake" },
  observatory: { yield: "sci", text: "+3 Science on hills" },
};

function builtCells(c) {
  const out = new Set();
  for (const v of Object.values(c.sites || {})) if (v !== c.cell) out.add(v);
  if (c.build?.site != null && c.build.site !== c.cell) out.add(c.build.site);
  return out;
}

/** The bonus building `b` earns standing on `cell` of city c (not the centre). */
export function siteBonus(g, board, c, b, cell) {
  const t = board.tiles[cell];
  const rule = SITE_RULES[b];
  if (!rule) return {};
  const nb = [];
  for (let d = 0; d < 6; d++) { const j = board.grid.neighbour(cell, d); if (j >= 0) nb.push(j); }
  const bldNext = nb.filter((j) => j === c.cell || Object.entries(c.sites || {}).some(([k, v]) => v === j && k !== b)).length;
  let n = 0;
  switch (b) {
    case "granary": n = Math.min(3, nb.filter((j) => g.imp[j] === "farm" || board.tiles[j].cover === "cropland").length); break;
    case "library": n = Math.min(2, bldNext) + (t.river ? 1 : 0); break;
    case "market": n = (t.river || g.road[cell] ? 1 : 0) + (nb.includes(c.cell) ? 1 : 0); break;
    case "workshop": n = Math.min(3, nb.filter((j) => board.tiles[j].land && (board.tiles[j].relief >= 1 || g.imp[j] === "mine")).length); break;
    case "temple": n = (t.relief >= 1 ? 2 : 0) + (nb.some((j) => board.tiles[j].heritage) || t.heritage ? 2 : 0); break;
    case "aqueduct": n = t.river || t.nearHills ? 2 : 0; break;
    case "caravanserai": n = nb.filter((j) => g.road[j]).length + (nb.some((j) => board.tiles[j].cover === "barren") ? 2 : 0); break;
    case "academy": case "wisdom": n = Math.min(3, bldNext); break;
    case "bazaar": n = Math.min(3, bldNext) + (g.road[cell] ? 1 : 0); break;
    case "bimaristan": n = t.fresh ? 2 : 0; break;
    case "garden": n = t.fresh ? 3 : 0; break;
    case "observatory": n = t.relief >= 1 ? 3 : 0; break;
  }
  return n ? { [rule.yield]: n } : {};
}

/** Where building `b` can stand in city c: [{cell, centre, bonus}] (the centre first). */
export function siteOptions(g, board, c, b) {
  const out = [{ cell: c.cell, centre: true, bonus: {} }];
  const taken = builtCells(c);
  for (const i of c.tiles) {
    if (i === c.cell || taken.has(i) || hexDist(board.grid, c.cell, i) > 2) continue;
    const t = board.tiles[i];
    if (!t.land || !t.passable) continue;
    out.push({ cell: i, centre: false, bonus: siteBonus(g, board, c, b, i) });
  }
  return out;
}

const bonusSum = (bn) => Object.values(bn).reduce((a, v) => a + v, 0);

/** The site the AI (or "place for me") picks: the biggest bonus worth more than the land it covers. */
export function bestSite(g, board, c, b) {
  let best = c.cell, bestV = 0.5;
  for (const o of siteOptions(g, board, c, b)) {
    if (o.centre) continue;
    const v = bonusSum(o.bonus) * 2.5 - tileValue(tileYield(g, board, o.cell), false) * 0.25;
    if (v > bestV) { bestV = v; best = o.cell; }
  }
  return best;
}

export const growNeed = (pop, growth = 0) => Math.round((15 + 8 * pop + pop * pop) * Math.max(0.4, 1 - growth));

export function factionTotals(g, board, f) {
  const t = { food: 0, prod: 0, gold: 0, sci: 0, cult: 0, pop: 0, cities: 0 };
  for (const c of citiesOf(g, f)) {
    const y = cityYields(g, board, c);
    for (const k of YIELD_KEYS) t[k] += y[k];
    t.pop += c.pop;
    t.cities++;
  }
  return t;
}

export function heritageHeld(g, board, f) {
  const out = [];
  for (const c of citiesOf(g, f)) for (const i of c.tiles) if (board.tiles[i].heritage) out.push(i);
  return out;
}

// --- research --------------------------------------------------------------------------------------------------------

export function availableTechs(g, f) {
  const F = g.factions[f];
  return TECHS.filter((t) => !F.techs[t.id] && t.req.every((r) => F.techs[r]));
}

export function setResearch(g, f, techId) {
  const F = g.factions[f];
  if (!availableTechs(g, f).some((t) => t.id === techId)) return false;
  F.research = techId;
  return true;
}

// --- cities ----------------------------------------------------------------------------------------------------------

export function canFound(g, board, f, cell) {
  const t = board.tiles[cell];
  if (!t || !t.land) return { ok: false, why: "Not land." };
  if (!t.passable || t.relief >= 2) return { ok: false, why: "Too rugged: cities need flat land or hills." };
  const owner = factionOf(g, cell);
  if (owner >= 0 && owner !== f) return { ok: false, why: `Inside the ${g.factions[owner].name} borders.` };
  for (const c of g.cities) if (hexDist(board.grid, c.cell, cell) < CITY_MIN_DIST) return { ok: false, why: `Too close to ${c.name}.` };
  for (const u of g.units) {
    if (u.f !== f && u.type === "guard" && hexDist(board.grid, u.cell, cell) <= 1) return { ok: false, why: `A ${g.factions[u.f].name} guard holds this ground.` };
  }
  return { ok: true, why: "" };
}

export function foundCity(g, board, u) {
  const chk = canFound(g, board, u.f, u.cell);
  if (u.type !== "pioneer" || !chk.ok) return null;
  const F = g.factions[u.f];
  const names = PEOPLES[u.f].cities;
  const name = names[F.nameIdx % names.length] + (F.nameIdx >= names.length ? ` ${Math.floor(F.nameIdx / names.length) + 1}` : "");
  F.nameIdx++;
  const c = {
    id: g.cities.length, f: u.f, name, cell: u.cell, pop: 1, food: 0, prod: 0, build: null, blds: {}, routes: [],
    cult: 0, claims: 0, nextClaim: 8, tiles: [], founded: g.turn, capital: citiesOf(g, u.f).length === 0, sites: {},
    queue: [], locked: [],
  };
  g.cities.push(c);
  g.cityAt[c.cell] = c.id;
  for (const i of ring(board.grid, c.cell, 1)) claim(g, board, c, i);
  removeUnit(g, u);
  syncBoard(g, board);
  logMsg(g, u.f, `${F.name} founded ${name}${c.capital ? ", their first city" : ""}.`, c.cell);
  vision(g, board, u.f);
  return c;
}

function claim(g, board, c, i) {
  if (i < 0 || !board.tiles[i].land || g.terr[i] >= 0) return false;
  g.terr[i] = c.id;
  c.tiles.push(i);
  return true;
}

function growBorders(g, board, c) {
  while (c.cult >= c.nextClaim) {
    let best = -1, bestV = -Infinity;
    for (const i of c.tiles) for (let d = 0; d < 6; d++) {
      const j = board.grid.neighbour(i, d);
      if (j < 0 || !board.tiles[j].land || g.terr[j] >= 0 || hexDist(board.grid, c.cell, j) > CLAIM_RADIUS) continue;
      const v = tileValue(tileYield(g, board, j), false) + (board.tiles[j].heritage ? 20 : 0) - hexDist(board.grid, c.cell, j);
      if (v > bestV) { bestV = v; best = j; }
    }
    if (best < 0) { c.nextClaim = Infinity; return; }
    claim(g, board, c, best);
    c.claims++;
    c.nextClaim += 10 + 6 * c.claims;
    if (board.tiles[best].heritage) logMsg(g, c.f, `${c.name}'s borders now take in ${board.tiles[best].heritage}.`, best);
  }
}

/** What a city can build now: [{kind: "unit"|"bld", id, name, cost, ok, why}] */
export function buildOptions(g, c) {
  const out = [];
  for (const [id, U] of Object.entries(UNITS)) {
    if (U.tech && !has(g, c.f, U.tech)) continue;
    const why = id === "pioneer" && c.pop < U.minPop ? `Needs ${U.minPop} population` : "";
    out.push({ kind: "unit", id, name: U.name, cost: U.cost, ok: !why, why });
  }
  for (const [id, B] of Object.entries(BUILDINGS)) {
    if (c.blds[id] || (B.tech && !has(g, c.f, B.tech))) continue;
    if (B.wonder && g.cities.some((x) => x.blds[id])) continue;
    out.push({ kind: "bld", id, name: B.name, cost: B.cost, ok: true, why: "", wonder: !!B.wonder });
  }
  return out;
}

export function setBuild(g, c, kind, id, site = null) {
  const o = buildOptions(g, c).find((x) => x.kind === kind && x.id === id);
  if (!o || !o.ok) return false;
  c.build = { kind, id };
  if (kind === "bld" && site != null && siteOptions(g, board_(g), c, id).some((x) => x.cell === site)) c.build.site = site;
  if (kind === "bld") c.queue = (c.queue || []).filter((q) => !(q.kind === "bld" && q.id === id));
  return true;
}

// The build queue: what the city makes after the current item, in order.
export function queueBuild(g, c, kind, id, site = null) {
  if (!c.build) return setBuild(g, c, kind, id, site);
  c.queue = c.queue || [];
  if (c.queue.length >= 6) return false;
  if (kind === "bld" && (c.build.kind === "bld" && c.build.id === id || c.queue.some((q) => q.kind === "bld" && q.id === id))) return false;
  if (!buildOptions(g, c).some((x) => x.kind === kind && x.id === id)) return false;
  const q = { kind, id };
  if (site != null) q.site = site;
  c.queue.push(q);
  return true;
}
export function unqueue(c, k) { if (c.queue?.[k]) c.queue.splice(k, 1); }
export function raiseQueued(c, k) {
  if (!c.queue || k <= 0 || k >= c.queue.length) return;
  [c.queue[k - 1], c.queue[k]] = [c.queue[k], c.queue[k - 1]];
}
function nextFromQueue(g, c) {
  c.build = null;
  while (c.queue?.length) {
    const q = c.queue.shift();
    if (setBuild(g, c, q.kind, q.id, q.site ?? null)) return;
    // a queued Pioneer waits for the city to reach 2 population
    if (q.kind === "unit" && buildOptions(g, c).some((o) => o.kind === "unit" && o.id === q.id)) { c.build = { kind: q.kind, id: q.id }; return; }
  }
}

// Citizens: a locked hex is always worked first (up to the city's population).
export function toggleLock(g, c, cell) {
  c.locked = c.locked || [];
  const k = c.locked.indexOf(cell);
  if (k >= 0) { c.locked.splice(k, 1); return false; }
  if (cell === c.cell || !c.tiles.includes(cell) || builtCells(c).has(cell)) return false;
  c.locked.push(cell);
  if (c.locked.length > c.pop) c.locked.shift();
  return true;
}

// Buying land: any free hex next to the city's borders, within reach of its centre.
export const tileCost = (g, board, c, cell) => 10 + 3 * c.tiles.length + 8 * hexDist(board.grid, c.cell, cell);
export function buyOptions(g, board, c) {
  const out = new Map();
  for (const i of c.tiles) for (let d = 0; d < 6; d++) {
    const j = board.grid.neighbour(i, d);
    if (j < 0 || out.has(j) || !board.tiles[j].land || g.terr[j] >= 0 || hexDist(board.grid, c.cell, j) > CLAIM_RADIUS) continue;
    out.set(j, tileCost(g, board, c, j));
  }
  return [...out].map(([cell, cost]) => ({ cell, cost }));
}
export function buyTile(g, board, c, cell) {
  const o = buyOptions(g, board, c).find((x) => x.cell === cell);
  const F = g.factions[c.f];
  if (!o || F.gold < o.cost) return false;
  F.gold -= o.cost;
  claim(g, board, c, cell);
  syncBoard(g, board);
  logMsg(g, c.f, `${c.name} bought the land at ${board.tiles[cell].name} for ${o.cost} Gold.`, cell);
  return true;
}
// setBuild is called without the board; the board is attached to g at runtime (not saved).
const board_ = (g) => g._board;

export const buildCost = (b) => (b.kind === "unit" ? UNITS[b.id].cost : BUILDINGS[b.id].cost);
export const hurryCost = (c) => (c.build ? Math.max(0, Math.ceil((buildCost(c.build) - c.prod) * 2.5)) : 0);

export function hurry(g, c) {
  const cost = hurryCost(c);
  const F = g.factions[c.f];
  if (!c.build || cost <= 0 || F.gold < cost) return false;
  F.gold -= cost;
  c.prod = buildCost(c.build);
  return true;
}

function finishBuild(g, board, c) {
  const b = c.build;
  const F = g.factions[c.f];
  if (b.kind === "unit") {
    if (b.id === "pioneer") {
      if (c.pop < 2) return false;
      c.pop--;
    }
    addUnit(g, c.f, b.id, c.cell, c.id);
    logMsg(g, c.f, `${c.name} trained a ${UNITS[b.id].name}.`, c.cell);
  } else {
    const B = BUILDINGS[b.id];
    if (B.wonder && g.cities.some((x) => x.blds[b.id])) {
      logMsg(g, c.f, `${c.name} lost the race for the ${B.name}; the work goes to the stores.`, c.cell);
      c.build = null;
      return true;
    }
    c.blds[b.id] = g.turn;
    c.sites = c.sites || {};
    const valid = b.site != null && siteOptions(g, board, c, b.id).some((x) => x.cell === b.site);
    c.sites[b.id] = valid ? b.site : bestSite(g, board, c, b.id);
    if (c.sites[b.id] !== c.cell) { g.imp[c.sites[b.id]] = null; syncBoard(g, board); }
    if (B.wonder) {
      F.wonders.push(b.id);
      logMsg(g, -1, `The ${F.name} completed the ${B.name} in ${c.name}.`, c.cell);
    } else logMsg(g, c.f, `${c.name} built a ${B.name}.`, c.cell);
  }
  c.prod -= buildCost(b);
  return true;
}

// --- movement --------------------------------------------------------------------------------------------------------

/** Cells unit `u` may not enter: rival units (no sharing a hex) and rival cities (a caravan's goal excepted). */
function blockedFor(g, u) {
  const out = new Set();
  for (const v of g.units) if (v.f !== u.f) out.add(v.cell);
  for (const c of g.cities) if (c.f !== u.f) out.add(-1 - c.cell); // negative key: a city, enterable as a goal
  return out;
}

/** Can unit `u` enter `cell`? `final`: the last step (a caravan may enter a city there). */
function enterable(g, board, u, cell, final, blocked = blockedFor(g, u)) {
  if (moveCost(board.tiles[cell]) === Infinity || blocked.has(cell)) return false;
  if (blocked.has(-1 - cell) && !(final && (u.type === "caravan" || u.type === "envoy"))) return false;
  return true;
}

/** Dijkstra from the unit: Map cell → {cost, prev}. maxCost bounds the search. */
export function paths(g, board, u, maxCost = 60, target = -1) {
  const blocked = blockedFor(g, u);
  const dist = new Map([[u.cell, { cost: 0, prev: -1 }]]);
  const heap = [[0, u.cell]];
  const push = (e) => {
    heap.push(e);
    let k = heap.length - 1;
    while (k > 0) {
      const p = (k - 1) >> 1;
      if (heap[p][0] <= heap[k][0]) break;
      [heap[p], heap[k]] = [heap[k], heap[p]];
      k = p;
    }
  };
  const pop = () => {
    const top = heap[0], last = heap.pop();
    if (heap.length) {
      heap[0] = last;
      let k = 0;
      for (;;) {
        const l = 2 * k + 1, r = l + 1;
        let m = k;
        if (l < heap.length && heap[l][0] < heap[m][0]) m = l;
        if (r < heap.length && heap[r][0] < heap[m][0]) m = r;
        if (m === k) break;
        [heap[m], heap[k]] = [heap[k], heap[m]];
        k = m;
      }
    }
    return top;
  };
  while (heap.length) {
    const [d, i] = pop();
    if (d > dist.get(i).cost) continue;
    if (i === target) break;
    for (let k = 0; k < 6; k++) {
      const j = board.grid.neighbour(i, k);
      if (j < 0) continue;
      if (!enterable(g, board, u, j, j === target, blocked)) continue;
      const nd = d + moveCost(board.tiles[j]);
      if (nd > maxCost) continue;
      const cur = dist.get(j);
      if (!cur || nd < cur.cost) {
        dist.set(j, { cost: nd, prev: i });
        push([nd, j]);
      }
    }
  }
  return dist;
}

export function pathTo(dist, cell) {
  if (!dist.has(cell)) return null;
  const out = [];
  for (let c = cell; c >= 0 && dist.get(c).prev !== -1; c = dist.get(c).prev) out.push(c);
  return out.reverse();
}

/** Cells the unit can reach this turn (it may always take one step while it has moves left). */
export function reachable(g, board, u) {
  const out = new Set();
  if (u.moves <= 0) return out;
  const dist = paths(g, board, u, u.moves + 3);
  for (const [cell, e] of dist) {
    if (cell === u.cell) continue;
    const prevCost = e.prev === u.cell ? 0 : dist.get(e.prev).cost;
    if (prevCost < u.moves) out.add(cell);
  }
  return out;
}

/** Send a unit toward `cell`: it moves now as far as it can and keeps going on later turns. */
export function orderMove(g, board, u, cell) {
  u.sleep = false;
  if (cell === u.cell) { u.dest = -1; return true; }
  const dist = paths(g, board, u, 400, cell);
  if (!dist.has(cell)) return false;
  u.dest = cell;
  advance(g, board, u, dist);
  return true;
}

function advance(g, board, u, dist = null) {
  if (u.dest < 0 || u.moves <= 0) return;
  dist = dist || paths(g, board, u, 400, u.dest);
  const path = pathTo(dist, u.dest);
  if (!path) { u.dest = -1; return; }
  for (const cell of path) {
    if (u.moves <= 0) break;
    if (!enterable(g, board, u, cell, cell === u.dest)) { u.moves = 0; break; }
    u.moves = Math.max(0, u.moves - moveCost(board.tiles[cell]));
    u.cell = cell;
    revealAround(g, board, u.f, cell, UNITS[u.type].sight);
    if (cell === u.dest) { u.dest = -1; arrive(g, board, u); break; }
  }
}

function arrive(g, board, u) {
  const c = cityAt(g, u.cell);
  if (!c) return;
  if (u.type === "caravan") openRoute(g, board, u, c);
  else if (u.type === "envoy") openEmbassy(g, u, c);
}

/** Rival peoples the faction has met but has no embassy with yet. */
export function embassyTargets(g, f) {
  const F = g.factions[f];
  return g.cities.filter((c) => c.f !== f && F.met[c.f] != null && !F.embassy?.[c.f]);
}

function openEmbassy(g, u, c) {
  const F = g.factions[u.f], O = g.factions[c.f];
  F.embassy = F.embassy || {};
  if (c.f === u.f || F.embassy[c.f]) return false;
  F.embassy[c.f] = g.turn;
  for (const X of [F, O]) {
    X.culture += 20;
    if (X.research) X.progress[X.research] = (X.progress[X.research] || 0) + 20;
  }
  removeUnit(g, u);
  logMsg(g, u.f, `Your envoy reached ${c.name}: an embassy with the ${O.name} (+20 Science and Culture, then +2 Science a turn).`, c.cell);
  logMsg(g, c.f, `A ${F.name} envoy arrived in ${c.name} and opened an embassy (+20 Science and Culture).`, c.cell);
  return true;
}

// --- unit actions ------------------------------------------------------------------------------------------------

export function improve(g, board, u, kind) {
  if (u.type !== "builder" || u.moves <= 0) return false;
  const owner = factionOf(g, u.cell);
  if (owner !== u.f) return false;
  if (!canImprove(board, u.cell, kind, (t) => has(g, u.f, t))) return false;
  if (kind === "road") g.road[u.cell] = 1;
  else g.imp[u.cell] = kind;
  syncBoard(g, board);
  u.moves = 0;
  u.charges--;
  if (u.charges <= 0) removeUnit(g, u);
  return true;
}

export function improveOptions(g, board, u) {
  if (u.type !== "builder") return [];
  const own = factionOf(g, u.cell) === u.f;
  return Object.entries(IMPROVEMENTS).map(([id, I]) => {
    const ok = own && u.moves > 0 && canImprove(board, u.cell, id, (t) => has(g, u.f, t));
    let why = "";
    if (!own) why = "Only inside your borders";
    else if (I.tech && !has(g, u.f, I.tech)) why = `Needs ${TECH[I.tech].name}`;
    else if (!ok) why = u.moves <= 0 ? "No moves left" : "Not on this ground";
    return { id, name: I.name, emoji: I.emoji, ok, why };
  });
}

export function routeTargets(g, board, u) {
  const home = g.cities[u.home] || null;
  return g.cities.filter((c) => c.id !== u.home && (c.f !== u.f ? g.factions[u.f].met[c.f] : true) &&
    (!home || hexDist(board.grid, home.cell, c.cell) >= 5) && !(home && home.routes.some((r) => r.to === c.id)));
}

function maxRoutes(c) { return 2 + (c.blds.caravanserai ? 1 : 0); }

function openRoute(g, board, u, c) {
  const home = g.cities[u.home] || citiesOf(g, u.f)[0];
  if (!home || home.id === c.id || home.routes.some((r) => r.to === c.id)) return false;
  if (home.routes.length >= maxRoutes(home)) {
    logMsg(g, u.f, `${home.name} already runs ${home.routes.length} trade routes; build a Caravanserai for more.`, home.cell);
    return false;
  }
  const foreign = c.f !== u.f;
  const d = hexDist(board.grid, home.cell, c.cell);
  const gold = 1 + Math.min(4, Math.floor(d / 4)) + (foreign ? 1 : 0);
  home.routes.push({ to: c.id, gold, foreign });
  c.routes.push({ to: home.id, gold: Math.max(1, gold - 1), foreign });
  removeUnit(g, u);
  logMsg(g, u.f, `A caravan opened a trade route from ${home.name} to ${c.name} (+${gold} Gold a turn).`, c.cell);
  if (foreign) logMsg(g, c.f, `A ${g.factions[u.f].name} caravan opened a trade route to ${c.name}.`, c.cell);
  return true;
}

export function disband(g, u) { removeUnit(g, u); }

// --- vision and contact ---------------------------------------------------------------------------------------------

function revealAround(g, board, f, cell, radius) {
  const F = g.factions[f];
  for (const i of ring(board.grid, cell, radius)) F.seen[i] = 1;
}

/** The cells faction f sees right now (units' sight, cities and their borders + 1). */
export function visibleSet(g, board, f) {
  const vis = new Set();
  for (const u of g.units) if (u.f === f) for (const i of ring(board.grid, u.cell, UNITS[u.type].sight)) vis.add(i);
  for (const c of g.cities) if (c.f === f) {
    for (const i of ring(board.grid, c.cell, 2)) vis.add(i);
    for (const t of c.tiles) vis.add(t);
  }
  return vis;
}

function vision(g, board, f) {
  const F = g.factions[f];
  const vis = visibleSet(g, board, f);
  for (const i of vis) F.seen[i] = 1;
  for (const o of g.factions) {
    if (o.k === f || F.met[o.k]) continue;
    const seesThem = g.units.some((u) => u.f === o.k && vis.has(u.cell)) || g.cities.some((c) => c.f === o.k && vis.has(c.cell));
    if (seesThem) meet(g, f, o.k);
  }
}

function meet(g, a, b) {
  const A = g.factions[a], B = g.factions[b];
  if (A.met[b]) return;
  A.met[b] = g.turn;
  B.met[a] = g.turn;
  // Contact spreads ideas: both get a little research toward their current tech.
  for (const F of [A, B]) if (F.research) F.progress[F.research] = (F.progress[F.research] || 0) + 8;
  logMsg(g, a, `First contact with the ${B.name}. Their envoys bring new ideas (+8 Science).`);
  logMsg(g, b, `First contact with the ${A.name}. Their envoys bring new ideas (+8 Science).`);
}

// --- end of turn -----------------------------------------------------------------------------------------------------

/**
 * Finish the human's turn: the AI factions act (ai(g, board, f) is passed in to keep this module AI-free), every
 * city works, grows and builds, research advances, borders grow, events strike, and the next turn starts.
 */
export function endTurn(g, board, ai = null) {
  if (g.over) return g;
  for (const F of g.factions) if (F.ai && ai) ai(g, board, F.k);
  for (const F of g.factions) {
    if (!F.research) autoResearch(g, F.k);
    const sci = processCities(g, board, F.k) + 2 * Object.keys(F.embassy || {}).length;
    research(g, F.k, sci);
    payUpkeep(g, F.k);
  }
  events(g, board);
  g.turn++;
  for (const u of g.units) u.moves = UNITS[u.type].moves;
  // Standing orders keep moving at the start of the turn.
  for (const u of [...g.units]) if (u.dest >= 0) advance(g, board, u);
  for (const F of g.factions) vision(g, board, F.k);
  checkVictory(g, board);
  return g;
}

function processCities(g, board, f) {
  const F = g.factions[f];
  let sci = 0;
  for (const c of citiesOf(g, f)) {
    if (!c.build) autoBuild(g, c);
    const y = cityYields(g, board, c);
    sci += y.sci;
    F.gold += y.gold;
    F.goldEarned += y.gold;
    F.culture += y.cult;
    c.cult += y.cult;
    growBorders(g, board, c);
    // growth: stops while any citizen is unhappy or the city has no room
    c.food += y.surplus;
    const full = c.pop >= y.mood.housing, upset = y.mood.unhappy > 0;
    if ((full || upset) && c.food >= y.need) {
      c.food = y.need;
      const why = full ? "housing" : "unrest";
      if (c.stall !== why) logMsg(g, f, full
        ? `${c.name} has no room to grow (housing ${y.mood.housing}). Aqueducts, granaries, farms and qanats add housing.`
        : `${c.name} has stopped growing: ${y.mood.unhappy} citizen${y.mood.unhappy > 1 ? "s are" : " is"} unhappy. Temples and luxuries help.`, c.cell);
      c.stall = why;
    } else if (c.food >= y.need) {
      c.stall = null;
      c.food -= y.need;
      c.pop++;
      if (c.blds.granary) c.food += Math.floor(y.need * 0.25);
      logMsg(g, f, `${c.name} has grown to ${c.pop} population.`, c.cell);
    } else if (c.food < 0) {
      if (c.pop > 1) {
        c.pop--;
        logMsg(g, f, `${c.name} is starving and lost 1 population.`, c.cell);
      }
      c.food = 0;
    }
    // production
    if (c.build) {
      c.prod += y.prod;
      if (c.prod >= buildCost(c.build)) {
        if (finishBuild(g, board, c)) nextFromQueue(g, c);
      }
    } else c.prod += y.prod;
  }
  return sci;
}

function research(g, f, sci) {
  const F = g.factions[f];
  if (!F.research) return;
  F.progress[F.research] = (F.progress[F.research] || 0) + sci;
  const t = TECH[F.research];
  if (F.progress[t.id] >= t.cost) {
    const over = F.progress[t.id] - t.cost;
    F.techs[t.id] = g.turn;
    delete F.progress[t.id];
    logMsg(g, f, `${F.name} scholars learned ${t.name}. Unlocks: ${t.unlocks}.`);
    F.research = null;
    autoResearch(g, f);
    if (F.research) F.progress[F.research] = (F.progress[F.research] || 0) + over;
  }
}

// Default picks, used for AI factions and when the human leaves a choice empty.
const LEAN_TECH = {
  builder: ["bronze", "pottery", "masonry", "irrigation", "qanat", "mathematics", "roads"],
  trader: ["pottery", "bronze", "trade", "roads", "banking", "paper"],
  expander: ["irrigation", "pottery", "qanat", "bronze", "masonry", "medicine"],
  scholar: ["pottery", "writing", "mysticism", "mathematics", "philosophy", "astronomy", "paper"],
};
const LEAN_BLD = {
  builder: ["granary", "workshop", "library", "aqueduct", "market", "temple", "garden", "academy", "observatory"],
  trader: ["market", "granary", "caravanserai", "bazaar", "library", "temple", "wisdom", "observatory"],
  expander: ["granary", "aqueduct", "bimaristan", "workshop", "market", "library", "temple", "garden"],
  scholar: ["library", "granary", "temple", "academy", "wisdom", "garden", "observatory", "market"],
};

export function autoResearch(g, f) {
  const F = g.factions[f];
  const avail = availableTechs(g, f);
  if (!avail.length) { F.research = null; return null; }
  const pref = LEAN_TECH[F.leaning] || [];
  avail.sort((a, b) => {
    const pa = pref.indexOf(a.id), pb = pref.indexOf(b.id);
    return (a.cost - (pa >= 0 ? 30 - pa * 2 : 0)) - (b.cost - (pb >= 0 ? 30 - pb * 2 : 0));
  });
  F.research = avail[0].id;
  return F.research;
}

export function autoBuild(g, c) {
  const F = g.factions[c.f];
  const opts = buildOptions(g, c).filter((o) => o.ok);
  const mine = citiesOf(g, c.f);
  const units = unitsOf(g, c.f);
  const count = (type) => units.filter((u) => u.type === type).length + mine.filter((x) => x.build?.id === type).length;
  const wantCities = F.leaning === "expander" ? 9 : 6;
  const pick = (kind, id) => opts.find((o) => o.kind === kind && o.id === id);
  let choice = null;
  // First fix what holds the city back: unrest, no room to grow, an empty treasury.
  const y = g._board ? cityYields(g, g._board, c) : null;
  if (y) {
    const net = factionTotals(g, g._board, c.f).gold - upkeep(g, c.f).total;
    if (y.mood.unhappy > 0) choice = pick("bld", "temple") || pick("bld", "bazaar") || pick("bld", "wisdom") || pick("bld", "bimaristan");
    if (!choice && c.pop >= y.mood.housing - 1) choice = pick("bld", "aqueduct") || pick("bld", "granary") || pick("bld", "bimaristan");
    if (!choice && net < 2) choice = pick("bld", "market") || pick("bld", "caravanserai") || pick("bld", "bazaar");
  }
  if (!choice && mine.length + count("pioneer") < wantCities && count("pioneer") < 1 + (mine.length >= 3 ? 1 : 0) && c.pop >= 3) choice = pick("unit", "pioneer");
  if (!choice && count("builder") < Math.ceil(mine.length / 2) + 1) choice = pick("unit", "builder");
  if (!choice && has(g, c.f, "writing") && count("envoy") < 1 && new Set(embassyTargets(g, c.f).map((x) => x.f)).size > 0) choice = pick("unit", "envoy");
  if (!choice && has(g, c.f, "trade") && c.routes.length < maxRoutes(c) && count("caravan") < 1 + (F.leaning === "trader" ? 1 : 0) && (g.turn + c.id) % 3 === 0) choice = pick("unit", "caravan");
  if (!choice) for (const b of LEAN_BLD[F.leaning] || []) if ((choice = pick("bld", b))) break;
  if (!choice) choice = opts.find((o) => o.kind === "bld") || (count("builder") < mine.length ? pick("unit", "builder") : null);
  if (choice) c.build = { kind: choice.kind, id: choice.id };
  return c.build;
}

// --- events ------------------------------------------------------------------------------------------------------

function events(g, board) {
  for (const c of g.cities) {
    // Earthquakes: the hex's real record (M5.5+ within 50 km since 1900) sets the chance of a damaging quake.
    const q = g.quakes?.[c.cell] || 0;
    if (q > 0) {
      let p = 1 - Math.exp(-(q * 30) / 126 * 0.06);
      if (has(g, c.f, "masonry")) p *= 0.5;
      if (rand(g) < p) {
        const lost = c.pop > 2 ? 1 : 0;
        c.pop -= lost;
        c.prod = Math.floor(c.prod / 2);
        logMsg(g, c.f, `An earthquake struck ${c.name}${lost ? ", and 1 population was lost" : ""}. Half its work in progress was lost.${has(g, c.f, "masonry") ? "" : " Masonry halves the risk."}`, c.cell);
      }
    }
    // Floods: river cities sometimes lose their stores; a farm on the river may be washed out.
    if (board.tiles[c.cell].river && rand(g) < 0.02) {
      c.food = Math.floor(c.food / 2);
      const farm = c.tiles.find((i) => board.tiles[i].river && g.imp[i] === "farm");
      if (farm != null && rand(g) < 0.5) { g.imp[farm] = null; syncBoard(g, board); }
      logMsg(g, c.f, `The river flooded at ${c.name}: half the stored food was lost${farm != null && !g.imp[farm] ? " and a farm was washed out" : ""}.`, c.cell);
    }
    // Drought: cities with no river, lake or qanat inside their borders.
    const watered = c.tiles.some((i) => board.tiles[i].fresh || g.imp[i] === "qanat");
    if (!watered && rand(g) < 0.03) {
      c.food = 0;
      if (c.pop > 3) c.pop--;
      logMsg(g, c.f, `Drought in ${c.name}: the stores ran dry. A river, lake or qanat inside its borders protects it.`, c.cell);
    }
  }
}

// --- victory -------------------------------------------------------------------------------------------------------

export function progress(g, board, f) {
  const F = g.factions[f];
  const techs = Object.keys(F.techs).length;
  const pop = citiesOf(g, f).reduce((s, c) => s + c.pop, 0);
  const her = heritageHeld(g, board, f).length;
  const obs = F.wonders.includes("observatory");
  return {
    science: { value: (techs / TECHS.length) * 0.85 + (obs ? 0.15 : 0), text: `${techs}/${TECHS.length} techs, Observatory ${obs ? "built" : "not built"}`, done: techs === TECHS.length && obs },
    culture: { value: Math.min(1, her / WIN.heritage) * 0.5 + Math.min(1, F.culture / WIN.culture) * 0.5, text: `${her}/${WIN.heritage} heritage sites, ${F.culture}/${WIN.culture} culture`, done: her >= WIN.heritage && F.culture >= WIN.culture },
    prosperity: { value: Math.min(1, F.goldEarned / WIN.gold) * 0.5 + Math.min(1, pop / WIN.pop) * 0.5, text: `${F.goldEarned}/${WIN.gold} gold earned, ${pop}/${WIN.pop} people`, done: F.goldEarned >= WIN.gold && pop >= WIN.pop },
  };
}

export function score(g, board, f) {
  const F = g.factions[f];
  const pop = citiesOf(g, f).reduce((s, c) => s + c.pop, 0);
  return pop * 4 + Object.keys(F.techs).length * 6 + heritageHeld(g, board, f).length * 15 +
    Math.floor(F.culture / 25) + Math.floor(F.goldEarned / 40) + F.wonders.length * 30;
}

const VICTORY_NAME = { science: "Science", culture: "Culture", prosperity: "Prosperity", score: "Score" };

function checkVictory(g, board) {
  for (const F of g.factions) {
    const p = progress(g, board, F.k);
    for (const kind of ["science", "culture", "prosperity"]) {
      if (p[kind].done) {
        g.over = { winner: F.k, kind, turn: g.turn };
        logMsg(g, -1, `${yearLabel(g.turn)}: the ${F.name} won a ${VICTORY_NAME[kind]} victory.`);
        return;
      }
    }
  }
  if (g.turn >= MAX_TURNS) {
    const scores = g.factions.map((F) => score(g, board, F.k));
    const best = scores.indexOf(Math.max(...scores));
    g.over = { winner: best, kind: "score", turn: g.turn, scores };
    logMsg(g, -1, `${yearLabel(g.turn)}: the age ends. The ${g.factions[best].name} lead with ${scores[best]} points.`);
  }
}
export { VICTORY_NAME };

// --- save / load ---------------------------------------------------------------------------------------------------

const bits = (a) => Array.from(a, (v) => (v ? "1" : "0")).join("");
export function save(g) {
  return JSON.stringify({
    ...g,
    terr: Array.from(g.terr), road: bits(g.road), cityAt: Array.from(g.cityAt),
    factions: g.factions.map((F) => ({ ...F, seen: bits(F.seen) })),
  });
}

export function load(text, board) {
  const o = JSON.parse(text);
  if (!o || o.v !== 1) throw new Error("not a saved game");
  const n = board.n;
  const fromBits = (s) => Uint8Array.from({ length: n }, (_, i) => (s[i] === "1" ? 1 : 0));
  const g = {
    ...o,
    terr: Int16Array.from(o.terr), road: fromBits(o.road), cityAt: Int16Array.from(o.cityAt),
    factions: o.factions.map((F) => ({ ...F, seen: fromBits(F.seen) })),
  };
  for (const c of g.cities) { c.queue = c.queue || []; c.locked = c.locked || []; }
  syncBoard(g, board);
  return g;
}
