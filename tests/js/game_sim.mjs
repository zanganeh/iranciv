// Headless check of the game engine: plays all-AI games on the real board and prints a summary.
// Run: node tests/js/game_sim.mjs [games=3] [turns=150]
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { buildBoard, MAX_TURNS, yearLabel, TECHS } from "../../web/js/ext/game/rules.js";
import { newGame, endTurn, save, load, progress, score, citiesOf } from "../../web/js/ext/game/game.js";
import { aiTurn } from "../../web/js/ext/game/ai.js";

const root = fileURLToPath(new URL("../../web/data/", import.meta.url));
const read = (n) => JSON.parse(readFileSync(root + n + ".json", "utf8"));
const world = read("world"), resources = read("resources"), wonders = read("wonders"), lenses = read("lenses");
const quakes = lenses.lenses.find((l) => l.id === "quakes").values;

const games = +(process.argv[2] || 3), turns = +(process.argv[3] || MAX_TURNS);
let failures = 0;
const check = (ok, msg) => { if (!ok) { failures++; console.error("FAIL:", msg); } };

for (let s = 1; s <= games; s++) {
  const board = buildBoard(world, { resources, wonders });
  let g = newGame(board, { seed: s * 7919, human: -1, quakes });
  const t0 = performance.now();
  while (!g.over && g.turn < turns) {
    endTurn(g, board, aiTurn);
    if (g.turn === 40) { // a save/load round trip mid-game must continue identically
      const text = save(g);
      g = load(text, board);
      check(save(g) === text, "save/load round trip");
    }
  }
  const ms = performance.now() - t0;
  console.log(`game ${s}: ${g.turn} turns to ${yearLabel(g.turn)}, ${(ms / g.turn).toFixed(1)} ms/turn` +
    (g.over ? `, ${g.factions[g.over.winner].name} won (${g.over.kind})` : ""));
  for (const F of g.factions) {
    const cs = citiesOf(g, F.k);
    const p = progress(g, board, F.k);
    console.log(`  ${F.name.padEnd(8)} cities ${String(cs.length).padStart(2)} pop ${String(cs.reduce((a, c) => a + c.pop, 0)).padStart(3)}` +
      ` techs ${Object.keys(F.techs).length}/${TECHS.length} culture ${F.culture} gold ${F.goldEarned} score ${score(g, board, F.k)}` +
      ` | sci ${(p.science.value * 100) | 0}% cul ${(p.culture.value * 100) | 0}% pros ${(p.prosperity.value * 100) | 0}%`);
    check(cs.length >= 2, `${F.name} founded at least 2 cities`);
  }
  check(g.units.every((u) => board.tiles[u.cell].land), "all units on land");
  check(g.factions.every((F) => F.gold >= 0), "no treasury below zero");
  check(board.tiles.filter((t) => t.lux).length >= 60, "luxuries placed on the board");
  check(g.cities.every((c) => Array.isArray(c.queue) && Array.isArray(c.locked)), "cities carry a queue and locks");
  const stalls = g.log.filter((e) => /no room to grow|stopped growing/.test(e.text)).length;
  const broke = g.log.filter((e) => /treasury is empty/.test(e.text)).length;
  console.log(`  pressure: ${stalls} growth stalls (housing or unrest), ${broke} empty-treasury turns`);
  const quakesLog = g.log.filter((e) => /earthquake/.test(e.text)).length;
  console.log(`  events: ${quakesLog} quakes, ${g.log.filter((e) => /Drought/.test(e.text)).length} droughts, ` +
    `${g.log.filter((e) => /trade route from/.test(e.text)).length} trade routes, ${g.factions.reduce((a, F) => a + Object.keys(F.met).length, 0) / 2} contacts, ${g.factions.reduce((a, F) => a + Object.keys(F.embassy || {}).length, 0)} embassies, ${g.log.filter((e) => /flooded/.test(e.text)).length} floods`);
}
if (failures) { console.error(`${failures} check(s) failed`); process.exit(1); }
console.log("ok");
