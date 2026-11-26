// Rival AI: simple, readable heuristics by leaning. Research and city builds use game.js's auto pickers; this file
// moves units: pioneers look for good sites, scouts explore, builders improve worked land, caravans open routes.

import { UNITS, IMPROVEMENTS, hexDist, ring, siteScore, canImprove } from "./rules.js";
import {
  has, citiesOf, embassyTargets, canFound, foundCity, paths, pathTo, orderMove, improve, routeTargets, factionOf, autoResearch,
  autoBuild, cityYields, disband,
} from "./game.js";

export function aiTurn(g, board, f) {
  const F = g.factions[f];
  if (!F.research) autoResearch(g, f);
  for (const c of citiesOf(g, f)) if (!c.build) autoBuild(g, c);
  const worked = new Set();
  for (const c of citiesOf(g, f)) for (const i of cityYields(g, board, c).worked) worked.add(i);
  for (const u of g.units.filter((x) => x.f === f)) {
    try {
      actUnit(g, board, u, worked);
    } catch (err) {
      u.dest = -1; // never let one unit stall the turn
    }
  }
}

export function actUnit(g, board, u, worked = null) {
  if (!g.units.includes(u) || u.moves <= 0) return;
  switch (u.type) {
    case "pioneer": return pioneer(g, board, u);
    case "scout": return scout(g, board, u);
    case "builder": return builder(g, board, u, worked);
    case "caravan": return caravan(g, board, u);
    case "envoy": return envoy(g, board, u);
    default: return;
  }
}

function pioneer(g, board, u) {
  // Re-plan if the target went bad (another city was founded near it).
  if (u.dest >= 0 && !canFound(g, board, u.f, u.dest).ok) u.dest = -1;
  if (u.dest < 0 && u.target >= 0 && u.cell === u.target && canFound(g, board, u.f, u.cell).ok) {
    foundCity(g, board, u);
    return;
  }
  const first = citiesOf(g, u.f).length === 0;
  // The first city: settle at once if the ground is decent, else take one short step to a better site.
  const reach = first ? 2 : 9;
  const dist = paths(g, board, u, reach * 2.2);
  let best = -1, bestV = -Infinity;
  const home = citiesOf(g, u.f);
  for (const [cell, e] of dist) {
    if (!canFound(g, board, u.f, cell).ok) continue;
    let v = siteScore(board, cell) - e.cost * (first ? 6 : 2.5);
    if (home.length) {
      const d = Math.min(...home.map((c) => hexDist(board.grid, c.cell, cell)));
      if (d > 8) v -= (d - 8) * 4; // keep the realm compact
    }
    if (v > bestV) { bestV = v; best = cell; }
  }
  if (best < 0) {
    if (canFound(g, board, u.f, u.cell).ok) foundCity(g, board, u);
    else if (g.turn - (u.born ?? (u.born = g.turn)) > 25) disband(g, u);
    else wander(g, board, u);
    return;
  }
  u.target = best;
  if (best === u.cell) { foundCity(g, board, u); return; }
  orderMove(g, board, u, best);
  if (g.units.includes(u) && u.cell === best && u.moves > 0) foundCity(g, board, u);
}

function scout(g, board, u) {
  const F = g.factions[u.f];
  if (u.dest >= 0) return; // still travelling
  const dist = paths(g, board, u, 40);
  let best = -1, bestV = -Infinity;
  for (const [cell, e] of dist) {
    if (cell === u.cell) continue;
    let unseen = 0;
    for (const i of ring(board.grid, cell, UNITS.scout.sight)) if (!F.seen[i] && board.tiles[i].land) unseen++;
    if (!unseen) continue;
    const v = unseen - e.cost * 0.8;
    if (v > bestV) { bestV = v; best = cell; }
  }
  if (best >= 0) orderMove(g, board, u, best);
  else if (g.turn > 30) disband(g, u); // nothing left to find
}

function builder(g, board, u, worked) {
  const hasT = (t) => has(g, u.f, t);
  const order = ["qanat", "farm", "mine", "road"];
  if (!worked) {
    worked = new Set();
    for (const c of citiesOf(g, u.f)) for (const i of cityYields(g, board, c).worked) worked.add(i);
  }
  const wantAt = (cell) => {
    if (factionOf(g, cell) !== u.f) return null;
    for (const kind of order) {
      if (kind === "road" && !worked.has(cell)) continue;
      if (canImprove(board, cell, kind, hasT)) return kind;
    }
    return null;
  };
  const here = wantAt(u.cell);
  if (here && (worked.has(u.cell) || u.dest < 0)) { improve(g, board, u, here); return; }
  if (u.dest >= 0 && wantAt(u.dest)) return;
  const dist = paths(g, board, u, 14);
  let best = -1, bestV = -Infinity;
  for (const [cell, e] of dist) {
    const kind = wantAt(cell);
    if (!kind) continue;
    const v = (worked.has(cell) ? 10 : 2) + (kind === "qanat" ? 3 : kind === "road" ? -4 : 0) - e.cost;
    if (v > bestV) { bestV = v; best = cell; }
  }
  if (best < 0) return;
  orderMove(g, board, u, best);
  if (g.units.includes(u) && u.cell === best && u.moves > 0) improve(g, board, u, wantAt(best));
}

function caravan(g, board, u) {
  if (u.dest >= 0) return;
  const targets = routeTargets(g, board, u);
  if (!targets.length) {
    if (g.turn - (u.born ?? (u.born = g.turn)) > 15) disband(g, u);
    return;
  }
  const dist = paths(g, board, u, 120);
  let best = null, bestV = -Infinity;
  for (const c of targets) {
    // The city cell itself is not "enterable" during a plain search, so score by the cheapest neighbour.
    let cost = Infinity;
    for (let d = 0; d < 6; d++) {
      const j = board.grid.neighbour(c.cell, d);
      if (j >= 0 && dist.has(j)) cost = Math.min(cost, dist.get(j).cost);
    }
    if (cost === Infinity) continue;
    const v = (c.f !== u.f ? 6 : 0) + Math.min(16, hexDist(board.grid, u.cell, c.cell)) * 0.5 - cost * 0.4;
    if (v > bestV) { bestV = v; best = c; }
  }
  if (best) orderMove(g, board, u, best.cell);
}

function envoy(g, board, u) {
  if (u.dest >= 0) return;
  const targets = embassyTargets(g, u.f);
  if (!targets.length) {
    if (g.turn - (u.born ?? (u.born = g.turn)) > 15) disband(g, u);
    return;
  }
  targets.sort((a, b) => hexDist(board.grid, u.cell, a.cell) - hexDist(board.grid, u.cell, b.cell));
  for (const c of targets.slice(0, 3)) if (orderMove(g, board, u, c.cell)) return;
}

function wander(g, board, u) {
  const opts = [...paths(g, board, u, u.moves).keys()].filter((c) => c !== u.cell);
  if (opts.length) orderMove(g, board, u, opts[(g.turn * 7 + u.id) % opts.length]);
}

export { IMPROVEMENTS };
