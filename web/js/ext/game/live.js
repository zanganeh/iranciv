// What makes the game feel alive: the goal chain that pays gold, the "next step" picker that always offers one
// clear thing to do (plus at most two others), and helpers for live countdowns. Pure logic, no DOM: ui.js shows it.

import { UNITS, BUILDINGS, TECH, ERAS } from "./rules.js";
import {
  citiesOf, unitsOf, availableTechs, autoResearch, autoBuild, buildOptions, canFound, hurryCost, luxuriesOf, cityYields,
  routeTargets, embassyTargets,
} from "./game.js";

// --- goals ------------------------------------------------------------------------------------------------------
// One at a time, in order. Each pays gold into the treasury when done.
const imps = (g, f) => g.cities.filter((c) => c.f === f).reduce((n, c) => n + c.tiles.filter((i) => g.imp[i]).length, 0);
const anyBld = (g, f, id) => g.cities.some((c) => c.f === f && (id ? c.blds[id] : Object.keys(c.blds).length));
const eraOf = (g, f) => Math.max(0, ...Object.keys(g.factions[f].techs).map((t) => TECH[t].era));
export const GOALS = [
  { id: "found", icon: "🏕️", text: "Found your first city", gold: 20, done: (g, f) => citiesOf(g, f).length >= 1 },
  { id: "study", icon: "📜", text: "Choose something to study", gold: 10, done: (g, f) => !!g.factions[f].research || Object.keys(g.factions[f].techs).length > 0 },
  { id: "collect", icon: "🪙", text: "Collect coins from your city", gold: 10, done: (g, f) => (g.factions[f].collected || 0) > 0 },
  { id: "builder", icon: "⛏️", text: "Train a Builder", gold: 15, done: (g, f) => unitsOf(g, f).some((u) => u.type === "builder") || imps(g, f) > 0 },
  { id: "improve", icon: "🌾", text: "Build a farm or a mine", gold: 20, done: (g, f) => imps(g, f) > 0 },
  { id: "grow", icon: "👥", text: "Grow a city to 3 people", gold: 20, done: (g, f) => citiesOf(g, f).some((c) => c.pop >= 3) },
  { id: "building", icon: "🏛️", text: "Raise your first building", gold: 30, done: (g, f) => anyBld(g, f) },
  { id: "pioneer", icon: "🏕️", text: "Train a Pioneer", gold: 25, done: (g, f) => unitsOf(g, f).some((u) => u.type === "pioneer") || citiesOf(g, f).length >= 2 },
  { id: "second", icon: "🏙️", text: "Found a second city", gold: 30, done: (g, f) => citiesOf(g, f).length >= 2 },
  { id: "luxury", icon: "🌸", text: "Work a luxury with an Estate", gold: 40, done: (g, f) => luxuriesOf(g, g._board, f).size >= 1 },
  { id: "meet", icon: "🕊️", text: "Meet another people", gold: 30, done: (g, f) => Object.keys(g.factions[f].met).length >= 1 },
  { id: "classical", icon: "🏺", text: "Reach the Classical era", gold: 50, done: (g, f) => eraOf(g, f) >= 1 },
  { id: "route", icon: "🐪", text: "Open a trade route", gold: 40, done: (g, f) => g.cities.some((c) => c.f === f && c.routes.length) },
  { id: "temple", icon: "🔥", text: "Build a Fire Temple", gold: 40, done: (g, f) => anyBld(g, f, "temple") },
  { id: "four", icon: "🏙️", text: "Rule four cities", gold: 60, done: (g, f) => citiesOf(g, f).length >= 4 },
  { id: "wonder", icon: "✦", text: "Complete a wonder", gold: 100, done: (g, f) => g.factions[f].wonders.length >= 1 },
  { id: "medieval", icon: "📚", text: "Reach the Medieval era", gold: 100, done: (g, f) => eraOf(g, f) >= 2 },
];

export function currentGoal(g) {
  const k = g.goal || 0;
  return k < GOALS.length ? GOALS[k] : null;
}

/** Pays out every goal now done, in order. Returns the goals completed. */
export function claimGoals(g) {
  const out = [];
  const f = g.human;
  if (f < 0) return out;
  for (let G = currentGoal(g); G && G.done(g, f); G = currentGoal(g)) {
    g.factions[f].gold += G.gold;
    g.goal = (g.goal || 0) + 1;
    out.push(G);
  }
  return out;
}

// --- recommendations ----------------------------------------------------------------------------------------------

/** What the advisor would build in city c next (the same pick the rival peoples make). */
export function recommendBuild(g, c) {
  const before = c.build;
  c.build = null;
  autoBuild(g, c);
  const rec = c.build;
  c.build = before;
  return rec;
}

/** Up to three build choices: the advisor's pick first, then two good alternatives. */
export function topBuilds(g, c) {
  const rec = recommendBuild(g, c);
  const opts = buildOptions(g, c).filter((o) => o.ok);
  const out = [];
  const add = (o) => { if (o && !out.some((x) => x.kind === o.kind && x.id === o.id)) out.push(o); };
  if (rec) add(opts.find((o) => o.kind === rec.kind && o.id === rec.id));
  add(opts.filter((o) => o.kind === "bld").sort((a, b) => a.cost - b.cost)[0]);
  add(opts.find((o) => o.kind === "unit" && o.id === "pioneer"));
  add(opts.find((o) => o.kind === "unit" && o.id === "builder"));
  for (const o of opts) if (out.length < 3) add(o);
  return out.slice(0, 3);
}

/** Up to three research choices, the advisor's pick first. */
export function topTechs(g, f) {
  const F = g.factions[f];
  const before = F.research;
  autoResearch(g, f);
  const rec = F.research;
  F.research = before;
  const avail = availableTechs(g, f).sort((a, b) => a.cost - b.cost);
  const out = avail.filter((t) => t.id === rec);
  for (const t of avail) if (out.length < 3 && t.id !== rec) out.push(t);
  return out;
}

// --- the next step ------------------------------------------------------------------------------------------------
// Each step: {key, icon, text, act, unit?, city?, cell?, kind?, id?}. The first is the big button.

const idle = (u) => u.moves > 0 && u.dest < 0 && !u.sleep && !u.auto;

export function nextSteps(g, board, { settle = null, running = false } = {}) {
  const f = g.human;
  if (g.over || f < 0) return [];
  const F = g.factions[f];
  const mine = citiesOf(g, f);
  const units = unitsOf(g, f);
  const steps = [];
  const push = (s) => { if (!steps.some((x) => x.key === s.key)) steps.push(s); };

  // the first city
  if (!mine.length) {
    const p = units.find((u) => u.type === "pioneer");
    if (p) {
      const here = canFound(g, board, f, p.cell).ok;
      const best = settle?.[0];
      if (here && (best == null || best === p.cell || p.moves <= 0)) push({ key: "found", icon: "🏕️", text: "Found your city here", act: "found", unit: p, cell: p.cell });
      else if (best != null) push({ key: "settle", icon: "🏕️", text: "Move to the best site", sub: "★ on the map", act: "move", unit: p, cell: best });
      if (here) push({ key: "found", icon: "🏕️", text: "Found the city right here", act: "found", unit: p, cell: p.cell });
    }
  }
  // cities with nothing to make
  for (const c of mine) {
    if (c.build) continue;
    const rec = recommendBuild(g, c);
    if (rec) {
      const name = rec.kind === "unit" ? UNITS[rec.id].name : BUILDINGS[rec.id].name;
      push({ key: "build" + c.id, icon: rec.kind === "unit" ? UNITS[rec.id].emoji : "🏗️", text: `${c.name}: build a ${name}`, sub: "advised", act: "build", city: c, kind: rec.kind, id: rec.id, cell: c.cell });
    }
  }
  // research
  if (!F.research && availableTechs(g, f).length) push({ key: "research", icon: "📜", text: "Choose what to study", act: "research" });
  // coins waiting
  const coins = mine.reduce((n, c) => n + (c.coins || 0), 0);
  const income = mine.reduce((n, c) => n + cityYields(g, board, c).gold, 0);
  if (coins >= Math.max(10, income * 2) || (coins > 0 && !F.collected)) push({ key: "collect", icon: "🪙", text: `Collect ${coins} gold`, act: "collect" });
  // units waiting for orders
  for (const u of units) {
    if (!idle(u)) continue;
    if (u.type === "pioneer" && mine.length) push({ key: "u" + u.id, icon: "🏕️", text: "Send the Pioneer to settle", act: "auto", unit: u, cell: u.cell });
    else if (u.type === "builder") push({ key: "u" + u.id, icon: "⛏️", text: "Put the Builder to work", act: "auto", unit: u, cell: u.cell });
    else if (u.type === "scout") push({ key: "u" + u.id, icon: "🧭", text: "Send the Scout exploring", act: "explore", unit: u, cell: u.cell });
    else if (u.type === "caravan" && routeTargets(g, board, u).length) push({ key: "u" + u.id, icon: "🐪", text: "Send the Caravan to trade", act: "auto", unit: u, cell: u.cell });
    else if (u.type === "envoy" && embassyTargets(g, f).length) push({ key: "u" + u.id, icon: "🕊️", text: "Send the Envoy abroad", act: "auto", unit: u, cell: u.cell });
  }
  // unrest
  for (const c of mine) {
    const y = cityYields(g, board, c);
    if (y.mood.unhappy > 0 && !c.blds.temple && buildOptions(g, c).some((o) => o.id === "temple") && c.build?.id !== "temple") {
      push({ key: "calm" + c.id, icon: "🔥", text: `${c.name} is unhappy: build a Fire Temple`, act: "build", city: c, kind: "bld", id: "temple", cell: c.cell });
    }
  }
  // gold to spend
  const gold = F.gold;
  let rush = null;
  for (const c of mine) {
    const cost = hurryCost(c);
    if (c.build && cost > 0 && cost <= gold * 0.6 && (!rush || cost < rush.cost)) rush = { c, cost };
  }
  if (!running && steps.length === 0) steps.push({ key: "play", icon: "▶", text: "Let time run", act: "play" });
  if (rush) {
    const b = rush.c.build;
    push({ key: "rush", icon: "⚡", text: `Finish the ${b.kind === "unit" ? UNITS[b.id].name : BUILDINGS[b.id].name} now`, sub: `${rush.cost} 🪙`, act: "hurry", city: rush.c, cell: rush.c.cell });
  }
  if (!steps.length) {
    steps.push(running
      ? { key: "fast", icon: "⏩", text: "All is in hand: speed up time", act: "faster" }
      : { key: "play", icon: "▶", text: "Let time run", act: "play" });
  }
  return steps;
}

// --- countdowns ---------------------------------------------------------------------------------------------------
export const SPEEDS = [0, 8000, 4000, 2000]; // ms per season: paused, normal, fast, very fast

export function fmtSecs(s) {
  if (!isFinite(s)) return "∞";
  s = Math.max(0, Math.ceil(s));
  return s >= 60 ? `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}` : `${s}s`;
}

export { ERAS };
