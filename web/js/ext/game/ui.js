// The game screen: a "game" mode with a right-hand dock (turn, stores, research, the selected unit or city,
// victory progress, chronicle), map overlays from render.js, and map clicks that select and move.

import {
  buildBoard, PEOPLES, LEANINGS, TECHS, TECH, UNITS, BUILDINGS, IMPROVEMENTS, LUXURIES, ERAS, MAX_TURNS, YIELD_INFO, yearLabel, hexDist,
  siteScore, upkeepOf,
} from "./rules.js";
import {
  newGame, endTurn, save, load, cityYields, factionTotals, availableTechs, setResearch, buildOptions, setBuild,
  buildCost, hurryCost, hurry, canFound, foundCity, reachable, orderMove, improveOptions, improve, routeTargets,
  unitsAt, cityAt, embassyTargets, siteOptions, siteBonus, SITE_RULES, citiesOf, unitsOf, factionOf, tileYield, progress, score, disband, VICTORY_NAME, WIN,
  queueBuild, unqueue, raiseQueued, toggleLock, buyOptions, buyTile, upkeep, luxuriesOf, paths, pathTo, collect,
} from "./game.js";
import { aiTurn, actUnit } from "./ai.js";
import { createView, touch, drawWorld, drawTokens, drawFx } from "./render.js";
import { GOALS, currentGoal, claimGoals, nextSteps, topBuilds, topTechs, SPEEDS, fmtSecs } from "./live.js";
import { createMinimap } from "./minimap.js";

const SAVE_KEY = "iranciv.game.v1";
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const store = {
  get() { try { return localStorage.getItem(SAVE_KEY); } catch { return null; } },
  set(v) { try { localStorage.setItem(SAVE_KEY, v); } catch { /* private mode: no autosave */ } },
  clear() { try { localStorage.removeItem(SAVE_KEY); } catch { /* ignore */ } },
};

export function installGame(app) {
  const view = createView();
  let board = null, g = null, dockEl = null, offs = [], busy = false, showTechs = false, queueMode = false, mini = null;
  // The clock: time runs by itself (speed 1-3) or waits (0). "Turn by turn" brings back the End turn button.
  const PREF_KEY = "iranciv.turnmode";
  let turnMode = false, speed = 0, seasonStart = performance.now(), hudEl = null, steps = [], lastLive = 0, rafId = 0;
  try { turnMode = localStorage.getItem(PREF_KEY) === "1"; } catch { /* no storage */ }

  if (!document.querySelector("style[data-bundled]")) { // the single-file build inlines the styles
    const link = document.createElement("link");
    link.rel = "stylesheet";
    link.href = "css/game.css";
    document.head.appendChild(link);
  }

  async function ensureBoard() {
    if (board) return board;
    const [wonders] = await Promise.all([app.loadData("wonders")]);
    board = buildBoard(app.data.world, { resources: app.data.resources, wonders });
    return board;
  }
  const quakes = () => app.data.lenses?.lenses?.find((l) => l.id === "quakes")?.values || null;

  // --- start screen: choose your people ---------------------------------------------------------------------------
  let startEl = null;
  function showStart() {
    closeStart();
    startEl = document.createElement("div");
    startEl.className = "gm-start";
    startEl.innerHTML = `<div class="gm-start-card panel" role="dialog" aria-modal="true" aria-label="New game">
      <div class="gm-start-kicker">3000 BCE · the Iranian plateau</div>
      <h2>Dawn on the Plateau</h2>
      <p class="gm-start-lede">Lead a wandering people to found cities on the real land of Iran: its rivers, fertile plains, deserts and mountains.
        Grow your cities, learn the ideas of three ages, trade with your rivals and leave a lasting mark before 1500 CE.</p>
      <div class="gm-start-peoples">${PEOPLES.map((p, k) => `<button class="gm-people" data-people="${k}" style="--fc:${p.color}">
        <span class="gm-crest">${esc(p.name[0])}</span><b>${esc(p.name)}</b><span class="gm-lean">${esc(LEANINGS[p.leaning].title)}</span>
        <small>${esc(LEANINGS[p.leaning].text)}</small></button>`).join("")}</div>
      <p class="gm-start-foot muted">The peoples are invented. The other three are led by the computer. Win by Science, Culture or Prosperity.</p>
    </div>`;
    startEl.addEventListener("click", (e) => {
      const b = e.target.closest("[data-people]");
      if (!b) return;
      closeStart();
      begin(+b.dataset.people);
    });
    document.body.appendChild(startEl);
  }
  function closeStart() { startEl?.remove(); startEl = null; }

  function begin(human) {
    g = newGame(board, { human, quakes: quakes() });
    store.set(save(g));
    show();
    report([{ text: `Your people, the ${g.factions[human].name}, arrive on the plateau. Found your first city with the Pioneer.` }], "3000 BCE");
  }

  function start(fresh = false) {
    const saved = fresh ? null : store.get();
    g = null;
    if (saved) {
      try { g = load(saved, board); } catch { g = null; }
    }
    if (!g) {
      view.g = null;
      if (dockEl) dockEl.innerHTML = `<div class="gm-sec"><p>Choose your people to begin.</p></div>`;
      app.requestDraw();
      showStart();
      return;
    }
    show();
  }

  function show() {
    view.cityView = null;
    view.placing = null;
    view.g = g;
    view.board = board;
    view.sel = null;
    view.reach = null;
    touch(view);
    const mine = unitsOf(g, g.human);
    const c0 = citiesOf(g, g.human)[0];
    const at = c0 ? c0.cell : mine[0]?.cell;
    if (at != null) app.flyTo(board.tiles[at].x, board.tiles[at].y, Math.max(app.cam.scale, 2.6));
    if (!c0 && mine[0]) select({ unit: mine.find((u) => u.type === "pioneer") || mine[0] });
    render();
  }

  // --- selection and orders ---------------------------------------------------------------------------------------
  function select(sel) {
    view.sel = sel;
    view.reach = sel?.unit && sel.unit.f === g.human ? reachable(g, board, sel.unit) : null;
    view.preview = null;
    view.settle = settleAdvice(sel?.unit);
    app.requestDraw();
    render();
  }
  // Where a selected pioneer might settle: the three best seen sites within 8 hexes, spread apart.
  function settleAdvice(u) {
    if (!u || u.type !== "pioneer" || u.f !== g.human) return null;
    const F = g.factions[g.human];
    const cand = [];
    for (let i = 0; i < board.n; i++) {
      if (!F.seen[i] || hexDist(board.grid, u.cell, i) > 8 || !canFound(g, board, g.human, i).ok) continue;
      cand.push([i, siteScore(board, i) - hexDist(board.grid, u.cell, i) * 1.5]);
    }
    cand.sort((a, b) => b[1] - a[1]);
    const out = [];
    for (const [i] of cand) {
      if (out.every((j) => hexDist(board.grid, i, j) >= 3)) out.push(i);
      if (out.length === 3) break;
    }
    return out;
  }
  function changed() {
    touch(view);
    if (view.sel?.unit && !g.units.includes(view.sel.unit)) view.sel = null;
    view.reach = view.sel?.unit && view.sel.unit.f === g.human ? reachable(g, board, view.sel.unit) : null;
    view.preview = null;
    view.settle = settleAdvice(view.sel?.unit);
    view.buy = view.cityView ? buyOptions(g, board, view.cityView) : null;
    celebrateGoals();
    store.set(save(g));
    app.requestDraw();
    render();
  }

  function celebrateGoals() {
    if (!g || g.over) return;
    for (const G of claimGoals(g)) {
      const c = citiesOf(g, g.human)[0];
      const cell = c ? c.cell : unitsOf(g, g.human)[0]?.cell;
      if (cell != null) view.fx.push({ kind: "plop", cell, t0: performance.now(), dur: 2200, text: `✓ ${G.text}  +${G.gold} 🪙`, color: "#b9ec9f", seed: Math.random() * 6, big: true });
      bumpGold();
      touch(view);
    }
  }

  function bumpGold() {
    const el = document.querySelector(".gm-hud-gold");
    if (!el) return;
    el.classList.remove("bump");
    void el.offsetWidth;
    el.classList.add("bump");
  }

  // Coins fly from the city to the gold counter.
  function collectAt(c) {
    const n = collect(g, c);
    if (!n) return 0;
    g.factions[g.human].collected = (g.factions[g.human].collected || 0) + n;
    const targets = c ? [c] : [];
    const goldEl = document.querySelector(".gm-hud-gold");
    const to = goldEl?.getBoundingClientRect();
    for (const city of targets.length ? targets : citiesOf(g, g.human)) {
      const t = board.tiles[city.cell];
      const cam = app.cam;
      const x = (t.x - cam.x) * cam.scale + cam.w / 2 - 34, y = (t.y - cam.y) * cam.scale + cam.h / 2 - 34 + 40;
      for (let k = 0; k < 6 && to; k++) {
        const coin = document.createElement("div");
        coin.className = "gm-coin";
        coin.textContent = "🪙";
        coin.style.left = x + (k - 3) * 6 + "px";
        coin.style.top = y + "px";
        document.body.appendChild(coin);
        requestAnimationFrame(() => {
          coin.style.transitionDelay = k * 45 + "ms";
          coin.style.left = to.left + to.width / 2 + "px";
          coin.style.top = to.top + to.height / 2 + "px";
          coin.style.opacity = "0.2";
        });
        setTimeout(() => coin.remove(), 900 + k * 45);
      }
    }
    setTimeout(bumpGold, 700);
    return n;
  }

  function onMapClick(ev) {
    if (app.mode !== "game" || !g) return false;
    const cell = ev.cell;
    if (cell == null || cell < 0) return true;
    if (view.cityView) return cityClick(cell);
    const F = g.factions[g.human];
    const u = view.sel?.unit;
    const here = unitsAt(g, cell).filter((x) => x.f === g.human);
    // Order the selected unit to move (or a caravan into a city).
    if (u && u.f === g.human && cell !== u.cell && !here.length && board.tiles[cell].land && F.seen[cell] && !g.over) {
      const c = cityAt(g, cell);
      if (!c || u.type === "caravan" || u.type === "envoy" || c.f === g.human) {
        if (orderMove(g, board, u, cell)) {
          if (!g.units.includes(u)) app.ui.toast(u.type === "envoy" ? "Embassy opened." : "Trade route opened.");
          changed();
        } else app.ui.toast("No way there.");
        return true;
      }
    }
    if (here.length) {
      // Clicking a stack cycles through it.
      const k = u ? here.indexOf(u) : -1;
      select({ unit: here[(k + 1) % here.length] });
      return true;
    }
    const c = cityAt(g, cell);
    if (c && c.f === g.human && c.coins > 0) {
      const n = collectAt(c);
      app.ui.toast(`+${n} gold collected`);
      changed();
      if (view.sel?.city !== c) { select({ city: c }); return true; }
    }
    if (c && F.seen[cell]) {
      if (view.sel?.city === c && c.f === g.human) openCity(c); // a second click opens the close-up
      else select({ city: c });
      return true;
    }
    if (F.seen[cell] && board.tiles[cell].land) { select({ cell }); return true; }
    select(null);
    return true;
  }

  // --- close-up city screen ---------------------------------------------------------------------------------------
  function openCity(c) {
    view.cityView = c;
    view.placing = null;
    view.cityTile = null;
    view.sel = { city: c };
    view.reach = null;
    view.settle = null;
    view.buy = buyOptions(g, board, c);
    reportEl?.remove();
    const t = board.tiles[c.cell];
    app.flyTo(t.x, t.y, 5.2, 600);
    app.requestDraw();
    render();
  }
  function closeCity() {
    const c = view.cityView;
    view.cityView = null;
    view.placing = null;
    view.buy = null;
    if (c) { const t = board.tiles[c.cell]; app.flyTo(t.x, t.y, 2.4, 500); }
    app.requestDraw();
    render();
  }
  function startPlacing(id) {
    const c = view.cityView;
    view.placing = { bld: id, options: siteOptions(g, board, c, id), queue: queueMode && !!c.build };
    app.requestDraw();
    render();
  }
  function cityClick(cell) {
    const c = view.cityView;
    if (view.placing) {
      const o = view.placing.options.find((x) => x.cell === cell);
      if (o) {
        if (view.placing.queue) queueBuild(g, c, "bld", view.placing.bld, cell);
        else setBuild(g, c, "bld", view.placing.bld, cell);
        app.ui.toast(`${BUILDINGS[view.placing.bld].name} will stand ${o.centre ? "in the city centre" : "here"}${view.placing.queue ? " (queued)" : ""}.`);
        view.placing = null;
        changed();
      }
      return true;
    }
    const buy = (view.buy || []).find((o) => o.cell === cell);
    if (buy) {
      if (buyTile(g, board, c, cell)) { app.ui.toast(`Bought for ${buy.cost} Gold.`); changed(); }
      else app.ui.toast(`You need ${buy.cost} Gold to buy this land.`);
      return true;
    }
    if (c.tiles.includes(cell)) {
      view.cityTile = cell;
      const built = new Set(Object.values(c.sites || {}));
      if (cell !== c.cell && !built.has(cell)) {
        const on = toggleLock(g, c, cell);
        app.ui.toast(on ? "Citizens will always work this hex." : "Unlocked: the city chooses again.");
        changed();
      } else render();
    }
    return true;
  }

  function nextUnit() {
    const mine = unitsOf(g, g.human).filter((u) => u.moves > 0 && u.dest < 0 && !u.sleep);
    if (!mine.length) { app.ui.toast("Every unit has moved. End the turn when ready."); return; }
    const k = view.sel?.unit ? mine.indexOf(view.sel.unit) : -1;
    const u = mine[(k + 1) % mine.length];
    select({ unit: u });
    app.flyTo(board.tiles[u.cell].x, board.tiles[u.cell].y, Math.max(app.cam.scale, 1.4), 400);
  }

  // --- the clock ---------------------------------------------------------------------------------------------
  const IMPORTANT = /won|age ends|completed the|earthquake|Drought|flooded|First contact|lost the race|treasury is empty/;

  /** One season passes: the world moves, and what changed is shown on the map. */
  function advanceSeason() {
    if (busy || !g || g.over) return;
    busy = true;
    const before = g.log.length;
    const pos = new Map(g.units.map((u) => [u.id, u.cell]));
    const mineBefore = new Map(g.cities.map((c) => [c.id, { pop: c.pop, blds: Object.keys(c.blds).length }]));
    endTurn(g, board, aiTurn);
    // Scouts set to explore move on by themselves at the start of every season.
    for (const u of unitsOf(g, g.human)) if (u.auto && u.moves > 0) actUnit(g, board, u);
    busy = false;
    seasonStart = performance.now();
    view.frac = 0;
    const now = performance.now();
    const F = g.factions[g.human];
    for (const u of g.units) {
      const from = pos.get(u.id);
      if (from != null && from !== u.cell && (F.seen[u.cell] || F.seen[from])) view.anim.set(u.id, { from, t0: now, dur: Math.min(700, view.msPerTurn * 0.6) });
    }
    for (const c of g.cities) {
      const b = mineBefore.get(c.id);
      if (!F.seen[c.cell]) continue;
      if (b && Object.keys(c.blds).length > b.blds) {
        const id = Object.keys(c.blds).find((k) => c.blds[k] === g.turn - 1) || Object.keys(c.blds).pop();
        const site = c.sites?.[id] ?? c.cell;
        view.fx.push({ kind: "plop", cell: site, t0: now, dur: 1800, text: `${BUILDINGS[id].wonder ? "✦ " : ""}${BUILDINGS[id].name} built`, seed: Math.random() * 6, big: !!BUILDINGS[id].wonder, color: c.f === g.human ? "#ffe9a8" : g.factions[c.f].color });
      }
      if (b && c.f === g.human && c.pop > b.pop) view.fx.push({ kind: "float", cell: c.cell, t0: now + 300, dur: 1400, text: "+1 👥", color: "#b9ec9f" });
      if (!b && c.f === g.human) view.fx.push({ kind: "plop", cell: c.cell, t0: now, dur: 1800, text: `${c.name} founded`, seed: 1, big: true });
    }
    const news = g.log.slice(before).filter((e) => e.f === g.human || e.f === -1);
    const big = news.filter((e) => IMPORTANT.test(e.text));
    if (turnMode) report(news, yearLabel(g.turn));
    else if (big.length) { report(big, yearLabel(g.turn)); setSpeed(0); }
    if (g.over) { store.clear(); setSpeed(0); }
    changed();
    // Pause when a city has nothing to make or research is empty: one clear thing needs you.
    if (!turnMode && speed > 0 && (citiesOf(g, g.human).some((c) => !c.build) || (!F.research && availableTechs(g, g.human).length))) setSpeed(0);
  }

  function doEndTurn() {
    if (busy || g.over) return;
    advanceSeason();
  }

  function setSpeed(k) {
    if (k > 0 && speed === 0) seasonStart = performance.now() - view.frac * SPEEDS[k]; // resume where the season was
    else if (k > 0) seasonStart = performance.now() - view.frac * SPEEDS[k];
    speed = k;
    view.msPerTurn = SPEEDS[k || 1];
    renderHud();
  }

  function loop(now) {
    rafId = requestAnimationFrame(loop);
    if (app.mode !== "game" || !g) return;
    if (!turnMode && speed > 0 && !busy && !g.over && !startEl) {
      view.frac = Math.min(1, (now - seasonStart) / SPEEDS[speed]);
      if (view.frac >= 1) advanceSeason();
    }
    const animating = view.fx.length || view.anim.size || view.cache?.purses?.some((p) => p.full >= 1) || view.stepCell >= 0;
    if (now - lastLive > (animating ? 16 : 120)) {
      lastLive = now;
      if (animating || speed > 0) app.requestDraw();
      updateLive();
    }
  }

  /** Countdowns and bars in the panel tick smoothly between seasons without rebuilding it. */
  function updateLive() {
    const ms = SPEEDS[speed || 1];
    for (const el of document.querySelectorAll("[data-cd]")) {
      const turns = +el.dataset.cd;
      el.textContent = turnMode ? `${isFinite(turns) ? turns : "∞"} turns` : fmtSecs((turns - view.frac) * ms / 1000);
    }
    for (const el of document.querySelectorAll("[data-bar]")) {
      const [p0, rate] = el.dataset.bar.split(",").map(Number);
      el.style.width = `${Math.max(0, Math.min(100, (p0 + rate * view.frac) * 100)).toFixed(1)}%`;
    }
    const ring = hudEl?.querySelector(".gm-season i");
    if (ring) ring.style.setProperty("--p", (view.frac * 100).toFixed(1));
  }

  // --- the HUD: resources on top, the clock and one clear next step at the bottom --------------------------------
  let picking = null; // "tech" while the research picker is open
  let lastSpeed = 1;
  function renderHud() {
    if (!hudEl) return;
    if (!g) { hudEl.innerHTML = ""; return; }
    const F = g.factions[g.human];
    const tot = factionTotals(g, board, g.human);
    const up = upkeep(g, g.human);
    const coins = citiesOf(g, g.human).reduce((n, c) => n + (c.coins || 0), 0);
    let settle = null;
    if (!citiesOf(g, g.human).length) settle = settleAdvice(unitsOf(g, g.human).find((u) => u.type === "pioneer"));
    steps = nextSteps(g, board, { settle, running: speed > 0 && !turnMode });
    view.stepCell = steps[0]?.cell ?? -1;
    const goal = currentGoal(g);
    const goalN = g.goal || 0;
    const stepBtn = (st, k) => `<button class="gm-step ${k ? "alt" : "main"}" data-step="${k}"><span class="gm-step-i">${st.icon}</span><span class="gm-step-t">${esc(st.text)}${st.sub ? `<small>${esc(st.sub)}</small>` : ""}</span></button>`;
    const clock = turnMode
      ? `<button class="gm-clockbtn wide" data-hud="end" ${busy || g.over ? "disabled" : ""}>End turn ⏎</button>`
      : [[0, "❚❚", "Pause (space)"], [1, "▶", "Normal speed (1)"], [2, "▶▶", "Fast (2)"], [3, "▶▶▶", "Very fast (3)"]].map(([k, l, t]) =>
        `<button class="gm-clockbtn ${speed === k ? "on" : ""}" data-speed="${k}" title="${t}" aria-label="${t}">${l}</button>`).join("");
    let pick = "";
    if (picking === "tech") {
      const ts = topTechs(g, g.human);
      pick = `<div class="gm-pick panel"><div class="gm-pick-h"><b>Choose what to study</b><button class="gm-x" data-hud="closepick" aria-label="Close">✕</button></div>
        <div class="gm-pick-row">${ts.map((t, k) => `<button class="gm-card ${k === 0 ? "rec" : ""}" data-pick-tech="${t.id}">${k === 0 ? '<span class="gm-rec">Advised</span>' : ""}<b>${esc(t.name)}</b>
          <span class="gm-card-big">📜 ${t.cost}</span><small>${esc(t.unlocks)}</small></button>`).join("")}</div></div>`;
    }
    hudEl.innerHTML = `
      <div class="gm-hud-top">
        <span class="gm-hud-res gm-hud-gold" title="Gold in the treasury. Income goes into each city's coin purse: tap a city's coins to collect them. Upkeep ${up.total} a season.">🪙 <b>${F.gold}</b>${coins ? `<small>+${coins} to collect</small>` : ""}</span>
        <span class="gm-hud-res" title="Science a season">📜 <b>${tot.sci}</b></span>
        <span class="gm-hud-res" title="Culture">🏺 <b>${F.culture}</b></span>
        <span class="gm-hud-res" title="People in ${tot.cities} cities">👥 <b>${tot.pop}</b></span>
        ${goal ? `<span class="gm-goal" title="Goal ${goalN + 1} of ${GOALS.length}">🎯 ${goal.icon} ${esc(goal.text)} <em>+${goal.gold} 🪙</em></span>` : ""}
      </div>
      ${pick}
      <div class="gm-hud-bar">
        <div class="gm-clock"><div class="gm-season" title="${esc(yearLabel(g.turn))}, season ${g.turn + 1} of ${MAX_TURNS}"><i style="--p:${(view.frac * 100).toFixed(1)}"></i><b>${esc(yearLabel(g.turn))}</b></div>${clock}</div>
        <div class="gm-steps">${steps.slice(0, 3).map(stepBtn).join("")}</div>
      </div>`;
  }

  function runStep(st) {
    if (!st || !g || g.over) return;
    const fly = (cell) => { if (cell != null && cell >= 0) { const t = board.tiles[cell]; app.flyTo(t.x, t.y, Math.max(app.cam.scale, 2.2), 450); } };
    switch (st.act) {
      case "found": {
        const c = foundCity(g, board, st.unit);
        if (c) { select({ city: c }); fly(c.cell); }
        break;
      }
      case "move": orderMove(g, board, st.unit, st.cell); select({ unit: st.unit }); fly(st.cell); break;
      case "build":
        setBuild(g, st.city, st.kind, st.id);
        select({ city: st.city });
        fly(st.city.cell);
        view.fx.push({ kind: "float", cell: st.city.cell, t0: performance.now(), dur: 1300, text: "🏗️ Work begins" });
        break;
      case "research": picking = "tech"; renderHud(); return;
      case "collect": { const n = collectAt(null); app.ui.toast(`+${n} gold collected`); break; }
      case "auto": case "explore":
        st.unit.auto = true;
        actUnit(g, board, st.unit);
        if (g.units.includes(st.unit)) { select({ unit: st.unit }); fly(st.unit.cell); }
        break;
      case "hurry":
        if (hurry(g, st.city)) view.fx.push({ kind: "float", cell: st.city.cell, t0: performance.now(), dur: 1300, text: "⚡ Finishes this season", color: "#ffd59a" });
        bumpGold();
        break;
      case "play": setSpeed(lastSpeed || 1); return;
      case "faster": setSpeed(Math.min(3, speed + 1)); return;
    }
    changed();
  }

  function onHudClick(e) {
    const b = e.target.closest("button");
    if (!b || b.disabled || !g) return;
    if (b.dataset.step != null) { runStep(steps[+b.dataset.step]); return; }
    if (b.dataset.speed != null) { const k = +b.dataset.speed; if (k) lastSpeed = k; setSpeed(k); return; }
    if (b.dataset.pickTech) { setResearch(g, g.human, b.dataset.pickTech); picking = null; changed(); return; }
    if (b.dataset.hud === "closepick") { picking = null; renderHud(); return; }
    if (b.dataset.hud === "end") doEndTurn();
  }

  // --- turn report: what happened while the world moved ----------------------------------------------------------
  let reportEl = null, reportTimer = 0;
  const ICON = [[/earthquake/i, "🌋"], [/drought/i, "☀️"], [/flood/i, "🌊"], [/learned/i, "📜"], [/grown/i, "👥"], [/founded/i, "🏛️"],
    [/trained/i, "⚒️"], [/built/i, "🏗️"], [/completed the/i, "✦"], [/contact|embassy|envoy/i, "🕊️"], [/trade route/i, "🐪"], [/borders/i, "🏺"], [/won|age ends/i, "🏆"]];
  function report(news, title) {
    reportEl?.remove();
    clearTimeout(reportTimer);
    if (!news.length) return;
    const order = (e) => (/won|age ends|completed the|learned|earthquake|Drought|flooded|First contact|founded/.test(e.text) ? 0 : 1);
    const items = [...news].sort((a, b) => order(a) - order(b)).slice(0, 6);
    reportEl = document.createElement("div");
    reportEl.className = "gm-report panel";
    reportEl.innerHTML = `<div class="gm-report-head"><b>${esc(title)}</b><button class="gm-x" aria-label="Close">✕</button></div>
      <ul>${items.map((e) => `<li${e.cell >= 0 ? ` data-cell="${e.cell}"` : ""}><span>${(ICON.find(([re]) => re.test(e.text)) || [0, "•"])[1]}</span>${esc(e.text)}</li>`).join("")}</ul>
      ${news.length > items.length ? `<div class="muted gm-small">and ${news.length - items.length} more in the Chronicle</div>` : ""}`;
    reportEl.addEventListener("click", (e) => {
      const li = e.target.closest("li[data-cell]");
      if (li) { const t = board.tiles[+li.dataset.cell]; app.flyTo(t.x, t.y, Math.max(app.cam.scale, 2.4), 500); }
      if (e.target.closest(".gm-x")) { reportEl.remove(); reportEl = null; }
    });
    document.body.appendChild(reportEl);
    reportTimer = setTimeout(() => { reportEl?.remove(); reportEl = null; }, 9000);
  }

  // --- the dock --------------------------------------------------------------------------------------------------
  function liveBar(p0, rate, color) {
    return `<span class="gm-bar"><span data-bar="${p0},${rate}" style="width:${Math.max(0, Math.min(100, p0 * 100)).toFixed(1)}%;background:${color}"></span></span>`;
  }
  function bar(v, color) {
    return `<span class="gm-bar"><span style="width:${Math.max(0, Math.min(100, v * 100)).toFixed(1)}%;background:${color}"></span></span>`;
  }
  const yieldChip = (k, v, extra = "") => `<span class="gm-y" title="${YIELD_INFO[k].label}"><span aria-hidden="true">${YIELD_INFO[k].emoji}</span>${v}${extra}</span>`;

  function render() {
    if (!dockEl || !g) return;
    if (view.cityView) { dockEl.innerHTML = cityScreenHtml(view.cityView); renderHud(); return; }
    const F = g.factions[g.human];
    const tot = factionTotals(g, board, g.human);
    const era = ERAS[Math.max(0, ...Object.keys(F.techs).map((t) => TECH[t].era), 0)];
    const h = [];
    h.push(`<div class="gm-head" style="--fc:${F.color}">
      <div class="gm-crest">${esc(F.name[0])}</div>
      <div class="gm-title"><b>${esc(F.name)}</b><span>${esc(era)} era</span></div>
      <div class="gm-year"><b>${esc(yearLabel(g.turn))}</b><span>Turn ${g.turn + 1} of ${MAX_TURNS}</span></div>
    </div>`);
    const up = upkeep(g, g.human), net = tot.gold - up.total;
    const lux = luxuriesOf(g, board, g.human);
    if (net < 0 && F.gold + net < 0 && !citiesOf(g, g.human).some((c) => c.coins > 0)) h.push(`<div class="gm-warn" style="margin:6px 14px 0">The treasury runs dry next season: a unit will be sent home.</div>`);
    if (g.over) {
      const W = g.factions[g.over.winner];
      const won = g.over.winner === g.human;
      h.push(`<div class="gm-over ${won ? "won" : ""}"><b>${won ? "Victory!" : "The age has ended."}</b>
        <p>${esc(W.name)} ${g.over.kind === "score" ? `lead on score at ${esc(yearLabel(g.over.turn))}` : `won a ${VICTORY_NAME[g.over.kind]} victory in ${esc(yearLabel(g.over.turn))}`}.</p>
        <p class="muted">${g.factions.map((x) => `${esc(x.name)} ${score(g, board, x.k)}`).join(" · ")}</p>
        <button class="btn" data-act="new">Play again</button></div>`);
    }
    // the selected unit, city or hex comes first
    h.push(selectionHtml());
    // research: one line and a bar; choices open as three cards
    const cur = F.research ? TECH[F.research] : null;
    h.push(`<section class="gm-sec"><h3>Studying</h3>`);
    if (cur) {
      const done = F.progress[cur.id] || 0;
      const left = tot.sci > 0 ? (cur.cost - done) / tot.sci : Infinity;
      h.push(`<div class="gm-row"><b>📜 ${esc(cur.name)}</b><span class="muted" data-cd="${left}">${turnMode ? `${Math.ceil(left)} turns` : fmtSecs(left * SPEEDS[speed || 1] / 1000)}</span></div>${liveBar(done / cur.cost, tot.sci / cur.cost, YIELD_INFO.sci.color)}
        <div class="muted gm-small">Unlocks ${esc(cur.unlocks)} · <button class="gm-link" data-act="techs">change</button></div>`);
    } else if (availableTechs(g, g.human).length) h.push(`<button class="btn gm-open" data-act="techs">📜 Choose what to study</button>`);
    else h.push(`<div class="muted">Every idea of the age is known.</div>`);
    h.push(`</section>`);
    h.push(`<div class="gm-lens" role="group" aria-label="Map view"><span class="muted">Map</span>${[[null, "Plain"], ["food", "🌾"], ["prod", "⚒️"], ["gold", "🪙"]].map(([k, l]) =>
      `<button class="${view.lens === k ? "on" : ""}" data-lens="${k || ""}" title="${k ? `Show ${YIELD_INFO[k].label} on the land` : "Plain map"}">${l}</button>`).join("")}
      <span class="muted" style="margin-left:auto" title="Luxuries you enjoy">💎 ${lux.size}</span></div>`);
    // everything else folds away
    const pr = progress(g, board, g.human);
    h.push(`<details class="gm-sec gm-guide"><summary>🏆 Victory</summary>
      <div class="gm-row"><b>📜 Science</b><span class="muted">${esc(pr.science.text)}</span></div>${bar(pr.science.value, YIELD_INFO.sci.color)}
      <div class="gm-row"><b>🏺 Culture</b><span class="muted">${esc(pr.culture.text)}</span></div>${bar(pr.culture.value, YIELD_INFO.cult.color)}
      <div class="gm-row"><b>🪙 Prosperity</b><span class="muted">${esc(pr.prosperity.text)}</span></div>${bar(pr.prosperity.value, YIELD_INFO.gold.color)}
      <div class="muted gm-small">Otherwise the highest score in 1500 CE wins.</div>
      <div class="gm-rivals">${g.factions.filter((x) => x.k !== g.human).map((x) => F.met[x.k] != null
        ? `<span style="--fc:${x.color}"><i></i>${esc(x.name)} · ${citiesOf(g, x.k).length} cities · score ${score(g, board, x.k)}</span>`
        : `<span class="muted"><i></i>An unknown people</span>`).join("")}</div></details>`);
    const logs = g.log.filter((e) => e.f === g.human || e.f === -1).slice(-10).reverse();
    h.push(`<details class="gm-sec gm-guide"><summary>📖 Chronicle</summary><ul class="gm-log">${logs.map((e) => `<li${e.cell >= 0 ? ` data-cell="${e.cell}"` : ""}><span>${esc(yearLabel(e.t))}</span> ${esc(e.text)}</li>`).join("")}</ul></details>`);
    h.push(`<details class="gm-sec gm-guide"><summary>❔ How to play</summary><ol>
      <li><b>Follow the big button</b> at the bottom: it always shows the most useful next step. Goals at the top pay gold.</li>
      <li><b>Time runs by itself.</b> Pause with ⏸ or the space bar, speed up with ▶▶. It pauses when something needs you.</li>
      <li><b>Collect coins.</b> Cities fill a coin purse. Tap it to bank the gold, then spend gold to finish builds now or buy land.</li>
      <li><b>Keep people content and housed.</b> Luxuries worked with an Estate and Fire Temples keep citizens content; water, farms, qanats and Aqueducts add housing.</li>
      <li><b>Win</b> by Science, Culture or Prosperity, or lead on score in 1500 CE.</li>
      <li>Keys: <kbd>Space</kbd> pause, <kbd>1</kbd>-<kbd>3</kbd> speed, <kbd>N</kbd> next unit, <kbd>F</kbd> found a city, <kbd>S</kbd> sleep.</li>
    </ol></details>`);
    h.push(`<div class="gm-foot"><button class="gm-link" data-act="next">Next unit</button><button class="gm-link" data-act="turnmode">${turnMode ? "⏱ Play in real time" : "⏭ Play turn by turn"}</button><button class="gm-link" data-act="new">New game</button></div>
      <div class="gm-foot" style="padding-top:4px"><button class="gm-link" data-act="exit">Back to the 2016 data map</button></div>
      <p class="gm-note muted">An ancient strategy game on Iran's real terrain, rivers and earthquake record. The peoples are invented.</p>`);
    dockEl.innerHTML = h.join("");
    renderHud();
  }

  function selectionHtml() {
    const sel = view.sel;
    if (!sel) {
      return `<section class="gm-sec gm-sel"><h3>Orders</h3><p class="muted gm-small">Click one of your units (the round tokens) to move it, or a city to run it.
        Pioneers found cities, scouts explore, builders improve land, envoys open embassies, caravans open trade routes.</p></section>`;
    }
    if (sel.unit) return unitHtml(sel.unit);
    if (sel.city) return cityHtml(sel.city);
    return tileHtml(sel.cell);
  }

  function tileLine(cell) {
    const t = board.tiles[cell];
    const y = tileYield(g, board, cell);
    const ys = Object.entries(y).filter(([, v]) => v).map(([k, v]) => yieldChip(k, v)).join("");
    const bits = [];
    if (t.ore) bits.push(`${esc(t.ore)} ore`);
    if (t.lux) bits.push(`${LUXURIES[t.lux].emoji} ${LUXURIES[t.lux].name}${g.imp[cell] ? " (worked)" : ": build an Estate here to enjoy it"}`);
    if (t.heritage) bits.push(`heritage ground: ${esc(t.heritage)}`);
    if (g.imp[cell]) bits.push(IMPROVEMENTS[g.imp[cell]].name);
    if (g.road[cell]) bits.push("road");
    const own = factionOf(g, cell);
    if (own >= 0) bits.push(`${g.factions[own].name} land`);
    const q = view.g.quakes?.[cell];
    if (q) bits.push(`quake risk ${q >= 6 ? "high" : q >= 2 ? "medium" : "low"}`);
    return `<div class="gm-tile"><b>${esc(t.name)}</b><div>${ys || '<span class="muted">no yield</span>'}</div>${bits.length ? `<div class="muted gm-small">${bits.join(" · ")}</div>` : ""}</div>`;
  }

  function unitHtml(u) {
    const U = UNITS[u.type];
    const mine = u.f === g.human;
    const h = [`<section class="gm-sec gm-sel"><h3>${mine ? "Your" : esc(g.factions[u.f].name)} ${esc(U.name)}</h3>`];
    h.push(`<p class="gm-small">${esc(U.desc)}</p>`);
    if (mine) {
      h.push(`<div class="gm-row"><span>Moves ${u.moves} / ${U.moves}${u.charges ? ` · ${u.charges} uses left` : ""}</span>${u.dest >= 0 ? '<span class="muted">on the way</span>' : ""}</div>`);
      const acts = [];
      if (u.type === "pioneer") {
        const chk = canFound(g, board, u.f, u.cell);
        acts.push(`<button class="btn" data-act="found" ${chk.ok && !g.over ? "" : "disabled"}>🏕️ Found a city here (F)</button>`);
        if (!chk.ok) acts.push(`<span class="muted gm-small">${esc(chk.why)}</span>`);
      }
      if (u.type === "builder") {
        for (const o of improveOptions(g, board, u)) {
          acts.push(`<button class="btn" data-imp="${o.id}" ${o.ok ? "" : "disabled"} title="${esc(IMPROVEMENTS[o.id].desc)}">${o.emoji} ${esc(o.name)}${o.ok ? "" : ` <small>${esc(o.why)}</small>`}</button>`);
        }
      }
      if (u.type === "caravan") {
        const ts = routeTargets(g, board, u);
        acts.push(`<span class="muted gm-small">${ts.length ? `Click a city to send it: ${ts.slice(0, 6).map((c) => esc(c.name)).join(", ")}${ts.length > 6 ? "…" : ""}. Cities further away pay more.` : "No city to trade with yet: meet a rival or found a city at least 5 hexes away."}</span>`);
      }
      if (u.type === "envoy") {
        const ts = embassyTargets(g, g.human);
        const names = [...new Map(ts.map((c) => [c.f, g.factions[c.f].name])).values()];
        acts.push(`<span class="muted gm-small">${names.length ? `Click a city of the ${names.map(esc).join(", ")} to open an embassy.` : "No people to send it to: meet a rival first (scouts help), or every people you know already hosts your embassy."}</span>`);
      }
      if (u.type === "scout") acts.push(`<button class="btn" data-act="explore">🧭 Explore on its own</button>`);
      acts.push(u.sleep ? `<button class="btn" data-act="wake">⏰ Wake</button>`
        : `<button class="btn" data-act="sleep" title="Skipped by Next unit until you wake it (S)">${u.type === "guard" ? "🛡️ Fortify" : "💤 Sleep"}</button>`);
      acts.push(`<button class="btn" data-act="skip">Skip turn</button><button class="btn" data-act="disband">Disband</button>`);
      h.push(`<div class="gm-acts">${acts.join("")}</div>`);
      if (u.moves > 0) h.push(`<p class="muted gm-small">Click a highlighted hex to move, or any hex further away to travel there over several turns.</p>`);
    }
    h.push(tileLine(u.cell));
    h.push(`</section>`);
    return h.join("");
  }

  function growText(c, y) {
    if (y.surplus < 0) return `${y.surplus} food: starving`;
    if (y.mood.unhappy > 0) return `+${y.surplus} food, growth stopped: unhappy`;
    if (c.pop >= y.mood.housing) return `+${y.surplus} food, growth stopped: no housing`;
    if (y.surplus <= 0) return "no spare food";
    const left = (y.need - c.food) / y.surplus;
    return `+${y.surplus} food, grows in <span data-cd="${left}">${turnMode ? `${Math.ceil(left)} turns` : fmtSecs(left * SPEEDS[speed || 1] / 1000)}</span>`;
  }

  function moodHtml(c, y) {
    const m = y.mood;
    const tip = (parts) => parts.map(([k, v]) => `${k} ${v > 0 ? "+" : ""}${v}`).join(", ");
    return `<div class="gm-mood">
      <span class="gm-y${m.unhappy ? " neg" : ""}" title="Content: ${esc(tip(m.parts))}"><span aria-hidden="true">${m.unhappy ? "😠" : "😊"}</span>${Math.min(c.pop, m.content)}/${c.pop}<small>${m.unhappy ? `${m.unhappy} unhappy, -${m.unhappy * 10}% output` : `content, room for ${m.content - c.pop}`}</small></span>
      <span class="gm-y${c.pop >= m.housing ? " neg" : ""}" title="Housing: ${esc(tip(m.housingParts))}"><span aria-hidden="true">🏠</span>${c.pop}/${m.housing}<small>housing</small></span>
      <span class="gm-y" title="Each building costs 1 Gold a turn; wonders are free"><span aria-hidden="true">🪙</span>${-Object.keys(c.blds).reduce((a, b) => a + upkeepOf(b), 0) || 0}<small>upkeep a season</small></span>
    </div>${m.unhappy ? `<p class="gm-small gm-why">${esc(c.name)} stops growing while anyone is unhappy. Work a new luxury with an Estate, or build a Fire Temple.</p>` : c.pop >= m.housing ? `<p class="gm-small gm-why">No room to grow: an Aqueduct, Granary, farms or qanats add housing.</p>` : ""}`;
  }

  const buildName = (b) => (b.kind === "unit" ? `${UNITS[b.id].emoji} ${UNITS[b.id].name}` : BUILDINGS[b.id].name);
  function queueHtml(c) {
    const q = c.queue || [];
    return `<div class="gm-queue">${q.length ? `<div class="muted gm-small">Then:</div><ol>${q.map((b, k) => `<li><span>${esc(buildName(b))}</span>
      ${k ? `<button class="gm-x" data-qup="${k}" title="Move up" aria-label="Move up">▲</button>` : ""}<button class="gm-x" data-qdel="${k}" title="Remove" aria-label="Remove">✕</button></li>`).join("")}</ol>` : ""}
      ${view.cityView ? `<div class="gm-qmode" role="group" aria-label="Clicking a choice"><span class="muted">Click a choice to</span>
        <button class="${queueMode ? "" : "on"}" data-qmode="0">build now</button><button class="${queueMode ? "on" : ""}" data-qmode="1">add to queue</button></div>` : ""}</div>`;
  }

  const bonusText = (bn) => Object.entries(bn).map(([k, v]) => `+${v} ${YIELD_INFO[k].emoji}`).join(" ");

  function cityScreenHtml(c) {
    const F = g.factions[g.human];
    const y = cityYields(g, board, c);
    const h = [];
    h.push(`<div class="gm-head" style="--fc:${F.color}"><div class="gm-crest">${esc(c.name[0])}</div>
      <div class="gm-title"><b>${c.capital ? "★ " : ""}${esc(c.name)}</b><span>Population ${c.pop} · ${esc(yearLabel(g.turn))}</span></div>
      <button class="btn" data-act="closecity">◀ Realm</button></div>`);
    h.push(`<div class="gm-stores">${["food", "prod", "gold", "sci", "cult"].map((k) => yieldChip(k, y[k])).join("")}</div>`);
    h.push(`<section class="gm-sec"><div class="gm-row"><b>👥 Growth</b><span class="muted">${growText(c, y)}</span></div>${bar(c.food / y.need, YIELD_INFO.food.color)}
      <p class="muted gm-small">The dashed hexes are the ${y.worked.length} your citizens farm and work.</p>`);
    if (c.build) {
      const cost = buildCost(c.build);
      const name = c.build.kind === "unit" ? UNITS[c.build.id].name : BUILDINGS[c.build.id].name;
      const turns = y.prod > 0 ? Math.max(1, Math.ceil((cost - c.prod) / y.prod)) : "∞";
      const where = c.build.kind === "bld" ? (c.build.site != null && c.build.site !== c.cell ? " · on its own hex" : " · site chosen when done") : "";
      const hc = hurryCost(c);
      const left = y.prod > 0 ? (cost - c.prod) / y.prod : Infinity;
      h.push(`<div class="gm-row"><b>⚒️ ${esc(name)}</b><span class="muted"><span data-cd="${left}">${turnMode ? `${turns} turns` : fmtSecs(left * SPEEDS[speed || 1] / 1000)}</span>${where}</span></div>${liveBar(c.prod / cost, y.prod / cost, YIELD_INFO.prod.color)}`);
      if (hc > 0) h.push(`<button class="btn gm-rush" data-act="hurry" ${F.gold >= hc ? "" : "disabled"}>⚡ Finish now for ${hc} 🪙</button>`);
    } else h.push(`<div class="gm-warn">Choose what ${esc(c.name)} builds next.</div>`);
    h.push(queueHtml(c));
    h.push(moodHtml(c, y));
    h.push(`<p class="muted gm-small">Click one of the city's hexes to lock a citizen on it (🔒), again to unlock. Click a gold-edged hex to buy it.</p>`);
    h.push(`</section>`);
    if (view.placing) {
      const B = BUILDINGS[view.placing.bld];
      const best = view.placing.options.filter((o) => !o.centre).sort((a, b) => Object.values(b.bonus).reduce((p, q) => p + q, 0) - Object.values(a.bonus).reduce((p, q) => p + q, 0))[0];
      h.push(`<section class="gm-sec gm-sel gm-placing"><h3>Place the ${esc(B.name)}</h3>
        <p class="gm-small">Click a highlighted hex on the map. ${esc(SITE_RULES[view.placing.bld]?.text || "")}. A building on its own hex takes that land out of farming; in the city centre it costs no land but earns no bonus.</p>
        ${best && Object.keys(best.bonus).length ? `<p class="gm-small">Best spot: <b>${esc(board.tiles[best.cell].name)}</b> ${bonusText(best.bonus)}</p>` : ""}
        <div class="gm-acts"><button class="btn" data-act="placecentre">Build in the city centre</button><button class="btn" data-act="cancelplace">Cancel</button></div></section>`);
    }
    const opts = buildOptions(g, c);
    h.push(`<section class="gm-sec"><h3>Raise a building</h3><div class="gm-builds">${opts.filter((o) => o.kind === "bld").map((o) => {
      const B = BUILDINGS[o.id];
      const on = c.build && c.build.kind === "bld" && c.build.id === o.id;
      return `<button class="gm-opt ${on ? "on" : ""} ${o.wonder ? "wonder" : ""}" data-place="${o.id}" title="${esc(B.desc)}">
        <b>${o.wonder ? "✦ " : ""}${esc(B.name)}</b><span>${o.cost} ⚒️ · ${esc(B.desc)}</span><small>${esc(SITE_RULES[o.id]?.text || "")}</small></button>`;
    }).join("") || '<p class="muted gm-small">Learn new ideas to unlock buildings.</p>'}</div></section>`);
    h.push(`<section class="gm-sec"><h3>Train</h3><div class="gm-builds">${opts.filter((o) => o.kind === "unit").map((o) => {
      const on = c.build && c.build.kind === "unit" && c.build.id === o.id;
      return `<button class="gm-opt ${on ? "on" : ""}" data-build="unit:${o.id}" ${o.ok || (queueMode && c.build) ? "" : "disabled"} title="${esc(UNITS[o.id].desc)}">
        <b>${UNITS[o.id].emoji} ${esc(o.name)}</b><span>${o.cost} ⚒️</span><small>${esc(o.ok ? UNITS[o.id].desc : o.why)}</small></button>`;
    }).join("")}</div></section>`);
    const blds = Object.keys(c.blds);
    h.push(`<section class="gm-sec"><h3>Buildings</h3>${blds.length ? `<ul class="gm-log">${blds.map((b) => {
      const site = c.sites?.[b];
      const own = site != null && site !== c.cell;
      return `<li${own ? ` data-cell="${site}"` : ""}><b>${esc(BUILDINGS[b].name)}</b> ${own ? `on ${esc(board.tiles[site].name)} ${bonusText(siteBonus(g, board, c, b, site))}` : '<span class="muted">in the city centre</span>'}</li>`;
    }).join("")}</ul>` : '<p class="muted gm-small">None yet.</p>'}</section>`);
    if (view.cityTile != null) h.push(`<section class="gm-sec"><h3>This hex</h3>${tileLine(view.cityTile)}</section>`);
    return h.join("");
  }

  function cityHtml(c) {
    const mine = c.f === g.human;
    const F = g.factions[c.f];
    const h = [`<section class="gm-sec gm-sel"><h3>${c.capital ? "★ " : ""}${esc(c.name)} <span class="muted">${mine ? "" : `· ${esc(F.name)}`}</span></h3>`];
    if (!mine) {
      h.push(`<p class="gm-small">Population ${c.pop}. Founded ${esc(yearLabel(c.founded))}.</p>${tileLine(c.cell)}</section>`);
      return h.join("");
    }
    const y = cityYields(g, board, c);
    const ms = SPEEDS[speed || 1] / 1000;
    const cd = (left) => `<span class="muted" data-cd="${left}">${turnMode ? `${isFinite(left) ? Math.ceil(left) : "∞"} turns` : fmtSecs(left * ms)}</span>`;
    // what it is making, with a live countdown and the gold button
    if (c.build) {
      const cost = buildCost(c.build);
      const left = y.prod > 0 ? (cost - c.prod) / y.prod : Infinity;
      const hc = hurryCost(c);
      h.push(`<div class="gm-making"><span class="gm-making-i">${c.build.kind === "unit" ? UNITS[c.build.id].emoji : "🏗️"}</span>
        <div><div class="gm-row" style="margin:0"><b>${esc(buildName(c.build))}</b>${cd(left)}</div>${liveBar(c.prod / cost, y.prod / cost, YIELD_INFO.prod.color)}</div>
        ${hc > 0 ? `<button class="btn gm-rush" data-act="hurry" ${g.factions[g.human].gold >= hc ? "" : "disabled"} title="Finish it this season">⚡ ${hc} 🪙</button>` : ""}</div>`);
    }
    // the next choice: three cards, the advised one first
    const top = topBuilds(g, c);
    h.push(`<div class="gm-cards-h">${c.build ? "Then make" : `<b>What should ${esc(c.name)} make?</b>`}</div>
      <div class="gm-cards">${top.map((o, k) => {
        const desc = o.kind === "unit" ? UNITS[o.id].desc : BUILDINGS[o.id].desc;
        const icon = o.kind === "unit" ? UNITS[o.id].emoji : o.wonder ? "✦" : "🏛️";
        const left = y.prod > 0 ? o.cost / y.prod : Infinity;
        return `<button class="gm-card ${k === 0 ? "rec" : ""}" data-build="${o.kind}:${o.id}" data-q="${c.build ? 1 : 0}" title="${esc(desc)}">${k === 0 ? '<span class="gm-rec">Advised</span>' : ""}
          <span class="gm-card-big">${icon}</span><b>${esc(o.name)}</b><small>${turnMode ? `${Math.ceil(left)} turns` : fmtSecs(left * ms)}</small></button>`;
      }).join("")}</div>`);
    h.push(queueHtml(c));
    h.push(`<div class="gm-row"><b>👥 ${c.pop}</b><span class="muted">${growText(c, y)}</span></div>${liveBar(c.food / y.need, y.surplus / y.need, YIELD_INFO.food.color)}`);
    h.push(moodHtml(c, y));
    h.push(`<button class="btn gm-open" data-act="opencity">🏛️ Open the city</button>`);
    h.push(`<details class="gm-more"><summary>All choices and details</summary>
      <div class="gm-stores">${["food", "prod", "gold", "sci", "cult"].map((k) => yieldChip(k, y[k])).join("")}</div>
      <div class="gm-builds">${buildOptions(g, c).map((o) => {
      const desc = o.kind === "unit" ? UNITS[o.id].desc : BUILDINGS[o.id].desc;
      const on = c.build && c.build.kind === o.kind && c.build.id === o.id;
      return `<button class="gm-opt ${on ? "on" : ""} ${o.wonder ? "wonder" : ""}" data-build="${o.kind}:${o.id}" ${o.ok || (queueMode && c.build) ? "" : "disabled"} title="${esc(desc)}">
        <b>${o.kind === "unit" ? UNITS[o.id].emoji + " " : o.wonder ? "✦ " : ""}${esc(o.name)}</b><span>${o.cost} ⚒️</span><small>${esc(o.ok ? desc : o.why)}</small></button>`;
    }).join("")}</div>`);
    const blds = Object.keys(c.blds).map((b) => BUILDINGS[b].name);
    h.push(`<div class="gm-small"><b>Buildings:</b> ${blds.length ? esc(blds.join(", ")) : '<span class="muted">none yet</span>'}</div>`);
    if (c.routes.length) h.push(`<div class="gm-small"><b>Trade:</b> ${c.routes.map((r) => `${esc(g.cities[r.to].name)} +${r.gold}🪙`).join(", ")}</div>`);
    const her = c.tiles.filter((i) => board.tiles[i].heritage).map((i) => board.tiles[i].heritage);
    if (her.length) h.push(`<div class="gm-small"><b>Heritage ground:</b> ${esc(her.join(", "))}</div>`);
    h.push(`<div class="muted gm-small">${c.tiles.length} hexes inside its borders; borders grow with culture (${c.cult}/${c.nextClaim === Infinity ? "max" : c.nextClaim}).</div>`);
    h.push(tileLine(c.cell));
    h.push(`</details></section>`);
    return h.join("");
  }

  function tileHtml(cell) {
    return `<section class="gm-sec gm-sel"><h3>Land</h3>${tileLine(cell)}</section>`;
  }

  function onDockClick(e) {
    const b = e.target.closest("button, li[data-cell]");
    if (!b || b.disabled) return;
    if (b.matches("li[data-cell]")) {
      const t = board.tiles[+b.dataset.cell];
      if (t && g.factions[g.human].seen[t.i]) app.flyTo(t.x, t.y, Math.max(app.cam.scale, 1.4), 500);
      return;
    }
    const u = view.sel?.unit, c = view.cityView || view.sel?.city;
    if (b.dataset.place) { startPlacing(b.dataset.place); return; }
    if (b.dataset.tech) { setResearch(g, g.human, b.dataset.tech); showTechs = false; changed(); return; }
    if (b.dataset.build) {
      const [k, id] = b.dataset.build.split(":");
      if ((queueMode || b.dataset.q === "1") && c.build) { if (!queueBuild(g, c, k, id)) app.ui.toast("Already queued, or the queue is full."); else app.ui.toast(`${b.querySelector("b")?.textContent || "It"} comes next.`); }
      else setBuild(g, c, k, id);
      changed();
      return;
    }
    if (b.dataset.qdel != null) { unqueue(c, +b.dataset.qdel); changed(); return; }
    if (b.dataset.qup != null) { raiseQueued(c, +b.dataset.qup); changed(); return; }
    if (b.dataset.qmode != null) { queueMode = b.dataset.qmode === "1"; render(); return; }
    if (b.dataset.lens != null) { view.lens = b.dataset.lens || null; app.requestDraw(); render(); return; }
    if (b.dataset.imp && u) { if (improve(g, board, u, b.dataset.imp)) app.ui.toast(`${IMPROVEMENTS[b.dataset.imp].name} built.`); changed(); return; }
    switch (b.dataset.act) {
      case "end": doEndTurn(); break;
      case "opencity": openCity(c); break;
      case "closecity": closeCity(); break;
      case "placecentre": setBuild(g, c, "bld", view.placing.bld, c.cell); view.placing = null; changed(); break;
      case "cancelplace": view.placing = null; app.requestDraw(); render(); break;
      case "next": nextUnit(); break;
      case "techs": picking = "tech"; renderHud(); break;
      case "turnmode":
        turnMode = !turnMode;
        try { localStorage.setItem(PREF_KEY, turnMode ? "1" : "0"); } catch { /* ignore */ }
        setSpeed(0);
        render();
        break;
      case "found": {
        const city = foundCity(g, board, u);
        if (city) { select({ city }); app.ui.toast(`${city.name} founded.`); }
        changed();
        break;
      }
      case "explore": actUnit(g, board, u); u.auto = true; changed(); break;
      case "skip": u.moves = 0; changed(); nextUnit(); break;
      case "sleep": u.sleep = true; u.dest = -1; changed(); nextUnit(); break;
      case "wake": u.sleep = false; changed(); break;
      case "disband": disband(g, u); view.sel = null; changed(); break;
      case "hurry": hurry(g, c); changed(); break;
      case "new": store.clear(); start(true); break;
      case "exit": app.setMode(null); break;
    }
  }

  function onKey(e) {
    if (app.mode !== "game" || !g || startEl || e.metaKey || e.ctrlKey || e.altKey) return;
    if (/^(INPUT|TEXTAREA|SELECT)$/.test(e.target?.tagName || "")) return;
    const k = e.key.toLowerCase();
    if (k === "enter" && !e.target.closest?.("button")) { e.preventDefault(); doEndTurn(); }
    else if (k === " " && !turnMode) { e.preventDefault(); if (speed) { lastSpeed = speed; setSpeed(0); } else setSpeed(lastSpeed || 1); }
    else if ((k === "1" || k === "2" || k === "3") && !turnMode) { e.preventDefault(); lastSpeed = +k; setSpeed(+k); }
    else if (k === "n") { e.preventDefault(); nextUnit(); }
    else if (k === "s" && view.sel?.unit?.f === g.human) { const u = view.sel.unit; u.sleep = !u.sleep; if (u.sleep) { u.dest = -1; nextUnit(); } changed(); }
    else if (k === "f" && view.sel?.unit?.type === "pioneer") {
      const city = foundCity(g, board, view.sel.unit);
      if (city) select({ city });
      changed();
    }
  }

  function onMove(e) {
    if (app.mode !== "game" || !board || e.pointerType === "touch") return;
    const cell = app.cellAt(e.clientX, e.clientY);
    if (cell === view.hover) return;
    view.hover = cell;
    const u = view.sel?.unit;
    view.preview = null;
    if (u && u.f === g.human && !view.cityView && cell >= 0 && cell !== u.cell && g.factions[g.human].seen[cell] && board.tiles[cell].land) {
      const dist = paths(g, board, u, 400, cell);
      const cells = pathTo(dist, cell);
      if (cells?.length) {
        const cost = dist.get(cell).cost, M = UNITS[u.type].moves;
        view.preview = { unit: u, cells, turns: cost <= u.moves || (cells.length === 1 && u.moves > 0) ? 0 : Math.ceil((cost - u.moves) / M) };
      }
    }
    app.requestDraw();
  }

  // --- mode ------------------------------------------------------------------------------------------------------
  app.registerMode("game", {
    async enter() {
      document.body.classList.add("game-on");
      app.setBare(true);
      app.setLens?.(null);
      app.clearSelection?.();
      dockEl = app.ui.dock("right", { width: 360, label: "Game" });
      dockEl.classList.add("gm-dock");
      dockEl.innerHTML = `<div class="gm-sec"><p>Preparing the land…</p></div>`;
      dockEl.addEventListener("click", onDockClick);
      app.registerOverlay({ id: "game-world", z: 40, draw: (ctx, cam) => drawWorld(ctx, cam, view) });
      mini = createMinimap(app, view);
      app.registerOverlay({ id: "game-tokens", z: 140, space: "screen", draw: (ctx, cam) => { drawTokens(ctx, cam, view); drawFx(ctx, cam, view); mini?.draw(cam); } });
      hudEl = document.createElement("div");
      hudEl.className = "gm-hud";
      hudEl.addEventListener("click", onHudClick);
      document.body.appendChild(hudEl);
      cancelAnimationFrame(rafId);
      rafId = requestAnimationFrame(loop);
      const canvas = document.getElementById("map");
      canvas.addEventListener("pointermove", onMove);
      addEventListener("keydown", onKey);
      offs = [() => canvas.removeEventListener("pointermove", onMove), () => removeEventListener("keydown", onKey)];
      playBtn?.classList.add("active");
      await ensureBoard();
      if (app.mode === "game") start(false);
    },
    exit() {
      if (g) store.set(save(g));
      document.body.classList.remove("game-on");
      app.setBare(false);
      app.unregisterOverlay("game-world");
      app.unregisterOverlay("game-tokens");
      dockEl?.removeEventListener("click", onDockClick);
      app.ui.undock("right");
      dockEl = null;
      offs.forEach((f) => f());
      offs = [];
      playBtn?.classList.remove("active");
      mini?.remove();
      mini = null;
      hudEl?.remove();
      hudEl = null;
      cancelAnimationFrame(rafId);
      setSpeed(0);
      closeStart();
      view.cityView = null;
      view.placing = null;
      reportEl?.remove();
      reportEl = null;
      view.g = null;
    },
    onEscape() {
      if (view.placing) { view.placing = null; app.requestDraw(); render(); return true; }
      if (view.cityView) { closeCity(); return true; }
      if (view.sel) { select(null); return true; }
      return true; // stay in the game; leave with the button
    },
  });
  app.on("mapclick", onMapClick);

  const playBtn = app.ui.addTopButton({
    id: "btn-game", icon: "🏺", label: "Play", title: "Play: an ancient 4X game on Iran's real terrain",
    onClick: () => app.setMode(app.mode === "game" ? null : "game"),
  });

  // test hooks
  app.game = {
    get state() { return g; }, get board() { return board; }, view,
    endTurn: () => doEndTurn(), select, render,
    peoples: PEOPLES, dist: (a, b) => hexDist(board.grid, a, b), WIN,
  };

  // The single-file build opens straight into the game (bundle-ext.js sets this).
  if (globalThis.__IRANCIV_START__ === "game") app.setMode("game");
}
