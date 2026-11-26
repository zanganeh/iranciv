// The game screen: a "game" mode with a right-hand dock (turn, stores, research, the selected unit or city,
// victory progress, chronicle), map overlays from render.js, and map clicks that select and move.

import {
  buildBoard, PEOPLES, LEANINGS, TECHS, TECH, UNITS, BUILDINGS, IMPROVEMENTS, ERAS, MAX_TURNS, YIELD_INFO, yearLabel, hexDist,
} from "./rules.js";
import {
  newGame, endTurn, save, load, cityYields, factionTotals, availableTechs, setResearch, buildOptions, setBuild,
  buildCost, hurryCost, hurry, canFound, foundCity, reachable, orderMove, improveOptions, improve, routeTargets,
  unitsAt, cityAt, embassyTargets, siteOptions, siteBonus, SITE_RULES, citiesOf, unitsOf, factionOf, tileYield, progress, score, disband, VICTORY_NAME, WIN,
} from "./game.js";
import { aiTurn, actUnit } from "./ai.js";
import { createView, touch, drawWorld, drawTokens } from "./render.js";

const SAVE_KEY = "iranciv.game.v1";
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const store = {
  get() { try { return localStorage.getItem(SAVE_KEY); } catch { return null; } },
  set(v) { try { localStorage.setItem(SAVE_KEY, v); } catch { /* private mode: no autosave */ } },
  clear() { try { localStorage.removeItem(SAVE_KEY); } catch { /* ignore */ } },
};

export function installGame(app) {
  const view = createView();
  let board = null, g = null, dockEl = null, offs = [], busy = false, showTechs = false;

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
    app.requestDraw();
    render();
  }
  function changed() {
    touch(view);
    if (view.sel?.unit && !g.units.includes(view.sel.unit)) view.sel = null;
    view.reach = view.sel?.unit && view.sel.unit.f === g.human ? reachable(g, board, view.sel.unit) : null;
    store.set(save(g));
    app.requestDraw();
    render();
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
    if (c) { const t = board.tiles[c.cell]; app.flyTo(t.x, t.y, 2.4, 500); }
    app.requestDraw();
    render();
  }
  function startPlacing(id) {
    const c = view.cityView;
    view.placing = { bld: id, options: siteOptions(g, board, c, id) };
    app.requestDraw();
    render();
  }
  function cityClick(cell) {
    const c = view.cityView;
    if (view.placing) {
      const o = view.placing.options.find((x) => x.cell === cell);
      if (o) {
        setBuild(g, c, "bld", view.placing.bld, cell);
        app.ui.toast(`${BUILDINGS[view.placing.bld].name} will stand ${o.centre ? "in the city centre" : "here"}.`);
        view.placing = null;
        changed();
      }
      return true;
    }
    if (c.tiles.includes(cell)) { view.cityTile = cell; render(); }
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

  function doEndTurn() {
    if (busy || g.over) return;
    busy = true;
    render();
    setTimeout(() => {
      const before = g.log.length;
      endTurn(g, board, aiTurn);
      // Scouts set to explore move on by themselves at the start of every turn.
      for (const u of unitsOf(g, g.human)) if (u.auto && u.moves > 0) actUnit(g, board, u);
      busy = false;
      const news = g.log.slice(before).filter((e) => e.f === g.human || e.f === -1);
      report(news, yearLabel(g.turn));
      if (g.over) store.clear();
      changed();
    }, 30);
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
  function bar(v, color) {
    return `<span class="gm-bar"><span style="width:${Math.max(0, Math.min(100, v * 100)).toFixed(1)}%;background:${color}"></span></span>`;
  }
  const yieldChip = (k, v, extra = "") => `<span class="gm-y" title="${YIELD_INFO[k].label}"><span aria-hidden="true">${YIELD_INFO[k].emoji}</span>${v}${extra}</span>`;

  function render() {
    if (!dockEl || !g) return;
    if (view.cityView) { dockEl.innerHTML = cityScreenHtml(view.cityView); return; }
    const F = g.factions[g.human];
    const tot = factionTotals(g, board, g.human);
    const era = ERAS[Math.max(0, ...Object.keys(F.techs).map((t) => TECH[t].era), 0)];
    const h = [];
    h.push(`<div class="gm-head" style="--fc:${F.color}">
      <div class="gm-crest">${esc(F.name[0])}</div>
      <div class="gm-title"><b>${esc(F.name)}</b><span>${esc(era)} era</span></div>
      <div class="gm-year"><b>${esc(yearLabel(g.turn))}</b><span>Turn ${g.turn + 1} of ${MAX_TURNS}</span></div>
    </div>`);
    h.push(`<div class="gm-stores">
      ${yieldChip("gold", F.gold, `<small>+${tot.gold}</small>`)}
      ${yieldChip("sci", tot.sci, "<small>/turn</small>")}
      ${yieldChip("cult", F.culture, `<small>+${tot.cult}</small>`)}
      <span class="gm-y" title="Population"><span aria-hidden="true">👥</span>${tot.pop}<small>${tot.cities} cities</small></span>
    </div>`);
    if (g.over) {
      const W = g.factions[g.over.winner];
      const won = g.over.winner === g.human;
      h.push(`<div class="gm-over ${won ? "won" : ""}"><b>${won ? "Victory!" : "The age has ended."}</b>
        <p>${esc(W.name)} ${g.over.kind === "score" ? `lead on score at ${esc(yearLabel(g.over.turn))}` : `won a ${VICTORY_NAME[g.over.kind]} victory in ${esc(yearLabel(g.over.turn))}`}.</p>
        <p class="muted">${g.factions.map((x) => `${esc(x.name)} ${score(g, board, x.k)}`).join(" · ")}</p>
        <button class="btn" data-act="new">Play again</button></div>`);
    } else {
      h.push(`<div class="gm-endrow"><button class="btn gm-end" data-act="end" ${busy ? "disabled" : ""}>${busy ? "The world moves…" : "End turn ⏎"}</button>
        <button class="btn" data-act="next" title="Select the next unit with moves left (N)">Next unit</button></div>`);
    }
    // research
    const cur = F.research ? TECH[F.research] : null;
    h.push(`<section class="gm-sec"><h3>Research</h3>`);
    if (cur) {
      const p = (F.progress[cur.id] || 0) / cur.cost;
      const turns = tot.sci > 0 ? Math.ceil((cur.cost - (F.progress[cur.id] || 0)) / tot.sci) : "∞";
      h.push(`<div class="gm-row"><b>${esc(cur.name)}</b><span class="muted">${turns} turns</span></div>${bar(p, YIELD_INFO.sci.color)}
        <div class="muted gm-small">Unlocks ${esc(cur.unlocks)}</div>`);
    } else if (availableTechs(g, g.human).length) h.push(`<div class="gm-warn">Choose what your scholars study.</div>`);
    else h.push(`<div class="muted">Every idea of the age is known.</div>`);
    if (showTechs || !cur) {
      h.push(`<div class="gm-techs">${availableTechs(g, g.human).map((t) => `<button class="gm-opt ${t.id === F.research ? "on" : ""}" data-tech="${t.id}">
        <b>${esc(t.name)}</b><span>${ERAS[t.era]} · ${t.cost} 📜</span><small>${esc(t.unlocks)}</small></button>`).join("")}</div>`);
    }
    h.push(`<button class="gm-link" data-act="techs">${showTechs ? "Hide choices" : "Change research"}</button>
      <div class="gm-known">${TECHS.map((t) => `<span class="${F.techs[t.id] ? "on" : ""}" title="${esc(t.name)}: ${esc(t.unlocks)}">${esc(t.name)}</span>`).join("")}</div></section>`);
    // selection
    h.push(selectionHtml());
    h.push(`<details class="gm-sec gm-guide" ${g.turn < 3 ? "open" : ""}><summary>How to play</summary><ol>
      <li><b>Settle.</b> Select your Pioneer (P) and press <i>Found a city</i>. Rivers, lakes and fertile plains make the best sites; the side panel shows each hex's yields.</li>
      <li><b>Build.</b> Click a city to choose what it makes. Builders (B) add farms, mines, qanats and roads; more Pioneers found more cities (each costs 1 population).</li>
      <li><b>Explore and meet.</b> Scouts (S) reveal the land. Meeting another people gives both sides new ideas. Envoys (E) open embassies; Caravans (C) open trade routes.</li>
      <li><b>Research.</b> Pick a tech; each era unlocks new buildings. Science comes from population, Libraries and trade.</li>
      <li><b>Grow your culture.</b> Culture widens your borders. Heritage ground (real UNESCO sites) inside your borders counts toward a Culture victory.</li>
      <li><b>Mind the land.</b> Earthquakes follow the real record of each place (Masonry halves the risk). Cities without water risk drought; river cities can flood.</li>
      <li><b>Win</b> by Science, Culture or Prosperity, or lead on score when the age ends in 1500 CE. Keys: <kbd>Enter</kbd> ends the turn, <kbd>N</kbd> next unit, <kbd>F</kbd> found a city.</li>
    </ol></details>`);
    // victory
    const pr = progress(g, board, g.human);
    h.push(`<section class="gm-sec"><h3>Paths to victory</h3>
      <div class="gm-row"><b>📜 Science</b><span class="muted">${esc(pr.science.text)}</span></div>${bar(pr.science.value, YIELD_INFO.sci.color)}
      <div class="gm-row"><b>🏺 Culture</b><span class="muted">${esc(pr.culture.text)}</span></div>${bar(pr.culture.value, YIELD_INFO.cult.color)}
      <div class="gm-row"><b>🪙 Prosperity</b><span class="muted">${esc(pr.prosperity.text)}</span></div>${bar(pr.prosperity.value, YIELD_INFO.gold.color)}
      <div class="muted gm-small">Otherwise the highest score in 1500 CE wins.</div>
      <div class="gm-rivals">${g.factions.filter((x) => x.k !== g.human).map((x) => F.met[x.k] != null
        ? `<span style="--fc:${x.color}"><i></i>${esc(x.name)} · ${citiesOf(g, x.k).length} cities · score ${score(g, board, x.k)}</span>`
        : `<span class="muted"><i></i>An unknown people</span>`).join("")}</div></section>`);
    // chronicle
    const logs = g.log.filter((e) => e.f === g.human || e.f === -1).slice(-10).reverse();
    h.push(`<section class="gm-sec"><h3>Chronicle</h3><ul class="gm-log">${logs.map((e) => `<li${e.cell >= 0 ? ` data-cell="${e.cell}"` : ""}><span>${esc(yearLabel(e.t))}</span> ${esc(e.text)}</li>`).join("")}</ul></section>`);
    h.push(`<div class="gm-foot"><button class="gm-link" data-act="new">New game</button><button class="gm-link" data-act="exit">Back to the 2016 data map</button></div>
      <p class="gm-note muted">An ancient 4X game on Iran's real terrain, rivers and earthquake record. The peoples are invented.</p>`);
    dockEl.innerHTML = h.join("");
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
      acts.push(`<button class="btn" data-act="skip">Skip turn</button><button class="btn" data-act="disband">Disband</button>`);
      h.push(`<div class="gm-acts">${acts.join("")}</div>`);
      if (u.moves > 0) h.push(`<p class="muted gm-small">Click a highlighted hex to move, or any hex further away to travel there over several turns.</p>`);
    }
    h.push(tileLine(u.cell));
    h.push(`</section>`);
    return h.join("");
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
    const turnsGrow = y.surplus > 0 ? Math.ceil((y.need - c.food) / y.surplus) : null;
    h.push(`<section class="gm-sec"><div class="gm-row"><b>👥 Growth</b><span class="muted">${y.surplus >= 0 ? `+${y.surplus} food, next citizen in ${turnsGrow ?? "∞"} turns` : `${y.surplus} food: starving`}</span></div>${bar(c.food / y.need, YIELD_INFO.food.color)}
      <p class="muted gm-small">The dashed hexes are the ${y.worked.length} your citizens farm and work.</p>`);
    if (c.build) {
      const cost = buildCost(c.build);
      const name = c.build.kind === "unit" ? UNITS[c.build.id].name : BUILDINGS[c.build.id].name;
      const turns = y.prod > 0 ? Math.max(1, Math.ceil((cost - c.prod) / y.prod)) : "∞";
      const where = c.build.kind === "bld" ? (c.build.site != null && c.build.site !== c.cell ? " · on its own hex" : " · site chosen when done") : "";
      const hc = hurryCost(c);
      h.push(`<div class="gm-row"><b>⚒️ ${esc(name)}</b><span class="muted">${turns} turns${where}</span></div>${bar(c.prod / cost, YIELD_INFO.prod.color)}`);
      if (hc > 0) h.push(`<button class="gm-link" data-act="hurry" ${F.gold >= hc ? "" : "disabled"}>Finish now for ${hc} 🪙</button>`);
    } else h.push(`<div class="gm-warn">Choose what ${esc(c.name)} builds next.</div>`);
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
      return `<button class="gm-opt ${on ? "on" : ""}" data-build="unit:${o.id}" ${o.ok ? "" : "disabled"} title="${esc(UNITS[o.id].desc)}">
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
    h.push(`<button class="btn gm-open" data-act="opencity">🏛️ Open the city: place buildings</button>`);
    h.push(`<div class="gm-stores">${["food", "prod", "gold", "sci", "cult"].map((k) => yieldChip(k, y[k])).join("")}</div>`);
    const turnsGrow = y.surplus > 0 ? Math.ceil((y.need - c.food) / y.surplus) : null;
    h.push(`<div class="gm-row"><b>👥 Population ${c.pop}</b><span class="muted">${y.surplus >= 0 ? `+${y.surplus} food, grows in ${turnsGrow ?? "∞"} turns` : `${y.surplus} food: starving`}</span></div>${bar(c.food / y.need, YIELD_INFO.food.color)}`);
    if (c.build) {
      const cost = buildCost(c.build);
      const name = c.build.kind === "unit" ? UNITS[c.build.id].name : BUILDINGS[c.build.id].name;
      const turns = y.prod > 0 ? Math.max(1, Math.ceil((cost - c.prod) / y.prod)) : "∞";
      const hc = hurryCost(c);
      h.push(`<div class="gm-row"><b>⚒️ ${esc(name)}</b><span class="muted">${Math.min(c.prod, cost)}/${cost}, ${turns} turns</span></div>${bar(c.prod / cost, YIELD_INFO.prod.color)}`);
      if (hc > 0) h.push(`<button class="gm-link" data-act="hurry" ${g.factions[g.human].gold >= hc ? "" : "disabled"}>Finish now for ${hc} 🪙</button>`);
    } else h.push(`<div class="gm-warn">Choose what ${esc(c.name)} builds.</div>`);
    h.push(`<div class="gm-builds">${buildOptions(g, c).map((o) => {
      const desc = o.kind === "unit" ? UNITS[o.id].desc : BUILDINGS[o.id].desc;
      const on = c.build && c.build.kind === o.kind && c.build.id === o.id;
      return `<button class="gm-opt ${on ? "on" : ""} ${o.wonder ? "wonder" : ""}" data-build="${o.kind}:${o.id}" ${o.ok ? "" : "disabled"} title="${esc(desc)}">
        <b>${o.kind === "unit" ? UNITS[o.id].emoji + " " : o.wonder ? "✦ " : ""}${esc(o.name)}</b><span>${o.cost} ⚒️</span><small>${esc(o.ok ? desc : o.why)}</small></button>`;
    }).join("")}</div>`);
    const blds = Object.keys(c.blds).map((b) => BUILDINGS[b].name);
    h.push(`<div class="gm-small"><b>Buildings:</b> ${blds.length ? esc(blds.join(", ")) : '<span class="muted">none yet</span>'}</div>`);
    if (c.routes.length) h.push(`<div class="gm-small"><b>Trade:</b> ${c.routes.map((r) => `${esc(g.cities[r.to].name)} +${r.gold}🪙`).join(", ")}</div>`);
    const her = c.tiles.filter((i) => board.tiles[i].heritage).map((i) => board.tiles[i].heritage);
    if (her.length) h.push(`<div class="gm-small"><b>Heritage ground:</b> ${esc(her.join(", "))}</div>`);
    h.push(`<div class="muted gm-small">${c.tiles.length} hexes inside its borders; borders grow with culture (${c.cult}/${c.nextClaim === Infinity ? "max" : c.nextClaim}).</div>`);
    h.push(tileLine(c.cell));
    h.push(`</section>`);
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
    if (b.dataset.build) { const [k, id] = b.dataset.build.split(":"); setBuild(g, c, k, id); changed(); return; }
    if (b.dataset.imp && u) { if (improve(g, board, u, b.dataset.imp)) app.ui.toast(`${IMPROVEMENTS[b.dataset.imp].name} built.`); changed(); return; }
    switch (b.dataset.act) {
      case "end": doEndTurn(); break;
      case "opencity": openCity(c); break;
      case "closecity": closeCity(); break;
      case "placecentre": setBuild(g, c, "bld", view.placing.bld, c.cell); view.placing = null; changed(); break;
      case "cancelplace": view.placing = null; app.requestDraw(); render(); break;
      case "next": nextUnit(); break;
      case "techs": showTechs = !showTechs; render(); break;
      case "found": {
        const city = foundCity(g, board, u);
        if (city) { select({ city }); app.ui.toast(`${city.name} founded.`); }
        changed();
        break;
      }
      case "explore": actUnit(g, board, u); u.auto = true; changed(); break;
      case "skip": u.moves = 0; changed(); nextUnit(); break;
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
    else if (k === "n") { e.preventDefault(); nextUnit(); }
    else if (k === "f" && view.sel?.unit?.type === "pioneer") {
      const city = foundCity(g, board, view.sel.unit);
      if (city) select({ city });
      changed();
    }
  }

  function onMove(e) {
    if (app.mode !== "game" || !board || e.pointerType === "touch") return;
    const cell = app.cellAt(e.clientX, e.clientY);
    if (cell !== view.hover) { view.hover = cell; app.requestDraw(); }
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
      app.registerOverlay({ id: "game-tokens", z: 140, space: "screen", draw: (ctx, cam) => drawTokens(ctx, cam, view) });
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
