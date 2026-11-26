// Commerce advisor ("Master of Trade"): FAF5 freight routes between zones (trade.json), the busiest ports and airports
// and the road and rail network (infrastructure.json).
import { esc, fin, el as mk, withAlpha } from "../core/util.js";
import { int, pct, money, count3, signedPct } from "../core/fmt.js";
import { barList, shareBar, stackedBars, swatchLegend, sizeLegend } from "../core/charts.js";
import { createTable } from "../core/table.js";
import { section, tiles, segmented, sourcesHtml } from "../core/view.js";
import { frameBox, focusPoint } from "../core/frame.js";

// Route colours by FAF mode (original palette: ochre road, iron rail, blue water, lilac air, sea-green mixed,
// dark copper pipeline drawn dashed).
const MODE_COLOR = { truck: "#d9a457", rail: "#a3acb8", water: "#4f9ad6", air: "#c9a0e0", multiple: "#8fcbb8", pipeline: "#b0703e", other: "#9a9aa8" };
const PORT_COLOR = "#4f9ad6", AIR_COLOR = "#e6cf86";
const MUSD = 1e6; // trade.json values are million dollars

export async function mount(ctx) {
  const { app, el, link, data } = ctx;
  const [trade, infra] = await Promise.all([app.loadData("trade"), app.loadData("infrastructure")]);
  if (!trade && !infra) {
    el.innerHTML = `<p class="adv-empty pad">trade.json and infrastructure.json are not built, so the Master of Trade has nothing to report.</p>`;
    return {};
  }
  const grid = app.w.grid;
  const metros = data.levels.metros;
  const api = { hotRoute: null };
  let zoneOfCity = null;

  // --- headline -------------------------------------------------------------------------------------------------
  const nm = trade?.national_musd || {};
  const topPort = infra?.ports?.[0], topAir = infra?.airports?.[0];
  el.insertAdjacentHTML("beforeend", tiles([
    { label: "Domestic freight", value: fin(nm.domestic) ? money(nm.domestic * MUSD) : "–", sub: `${esc(trade?.vintage || "FAF")} · by value` },
    { label: "Busiest port", value: topPort ? `${count3(topPort.tons)} t` : "–", sub: topPort ? topPort.name : "" },
    { label: "Busiest airport", value: topAir ? count3(topAir.enplanements) : "–", sub: topAir ? `${topAir.id} boardings ${topAir.year}` : "" },
  ]) + (trade ? `<p class="adv-callout">Freight values are model estimates from the Freight Analysis Framework, in current dollars: what moves between places, not what each place produces. Imports ${money(nm.import * MUSD)} and exports ${money(nm.export * MUSD)} are counted separately.</p>` : ""));

  // --- trade routes ---------------------------------------------------------------------------------------------
  let routes = [], zoneById = new Map(), routeCells = new Map(), overlayOff = null;
  const st = { mode: null, zone: null, route: null };
  let routeBars = null, zTable = null;
  const rDetail = mk("div", "adv-detail");
  if (trade) {
    zoneById = new Map((trade.zones || []).map((z) => [z.id, z]));
    const modeName = new Map((trade.modes || []).map((m) => [m.id, m.name]));
    const comm = trade.commodities || {};
    const zName = (id) => zoneById.get(id)?.short || zoneById.get(id)?.name || id;
    routes = (trade.routes || []).map((r) => {
      const pts = (r.path || []).filter((c) => c >= 0 && c < grid.size).map((c) => [grid.cx(c), grid.cy(c)]);
      return { ...r, key: `${r.a}-${r.b}`, label: `${zName(r.a)} ⇄ ${zName(r.b)}`, pts, path2d: smoothPath(pts) };
    });
    routes.forEach((r) => { for (const c of r.path || []) { if (!routeCells.has(c)) routeCells.set(c, []); routeCells.get(c).push(r); } });
    for (const list of routeCells.values()) list.sort((a, b) => b.value_musd - a.value_musd);
    const vMax = Math.max(1, ...routes.map((r) => r.value_musd));

    const rBody = section(el, { title: "Trade routes", kicker: esc(trade.vintage || ""), note: `${esc(trade.rule || "")}` });
    // Mode mix of the routes, value-weighted.
    const modeTot = {};
    for (const r of routes) for (const [m, s] of Object.entries(r.modes || {})) modeTot[m] = (modeTot[m] || 0) + (s || 0) * r.value_musd;
    const modeParts = (trade.modes || []).map((m) => ({ key: m.id, label: m.name, value: modeTot[m.id] || 0, color: MODE_COLOR[m.id] || "#999", text: money((modeTot[m.id] || 0) * MUSD) })).filter((p) => p.value > 0);
    rBody.insertAdjacentHTML("beforeend", `<h4 class="adv-h4">How the ${routes.length} routes' freight moves (by value)</h4>`);
    const modeBar = shareBar(rBody, {
      parts: modeParts,
      onHover: (p) => { api.modeHot = p ? p.key : null; redraw(); },
      onClick: (p) => setMode(st.mode === p.key ? null : p.key),
    });
    const fRow = mk("div", "adv-row adv-filterbar");
    rBody.appendChild(fRow);
    fRow.addEventListener("click", (e) => {
      if (e.target.closest("[data-clear-mode]")) setMode(null);
      if (e.target.closest("[data-clear-zone]")) setZone(null);
    });
    const rHost = mk("div");
    rBody.appendChild(rHost);
    rBody.appendChild(rDetail);
    rBody.insertAdjacentHTML("beforeend", `<p class="adv-note">${esc(trade.path_note || "")} Line width follows value; colour, the main mode (pipeline dashed).</p>${swatchLegend((trade.modes || []).filter((m) => modeTot[m.id] > 0).map((m) => ({ label: m.name, color: MODE_COLOR[m.id] })))}`);

    const routeHighlight = (r) => {
      const za = zoneById.get(r.a), zb = zoneById.get(r.b);
      const cells = new Set();
      const cities = new Set();
      for (const z of [za, zb]) {
        if (z?.anchor?.city) cities.add(z.anchor.city);
        for (const id of z?.cities || []) for (const c of metros.byKey.get(id)?.ref.footprint || []) cells.add(c);
      }
      return { cells, cities, label: r.label };
    };
    const visibleRoutes = () => routes.filter((r) => (!st.mode || r.top_mode === st.mode) && (!st.zone || r.a === st.zone || r.b === st.zone));
    const drawRoutes = () => {
      const vis = visibleRoutes();
      rHost.innerHTML = "";
      routeBars = barList(rHost, {
        rank: !st.mode && !st.zone, ariaLabel: "Largest freight routes", max: vMax,
        rows: vis.map((r) => ({
          key: r.key, label: r.label, value: r.value_musd, text: money(r.value_musd * MUSD), color: MODE_COLOR[r.top_mode],
          sub: `${modeName.get(r.top_mode) || r.top_mode} · ${comm[r.top_commodity] || "–"}`, full: `#${r.rank} ${zoneById.get(r.a)?.name || r.a} ⇄ ${zoneById.get(r.b)?.name || r.b}`,
        })),
        selected: st.route?.key,
        onHover: (row) => {
          const r = row ? routes.find((x) => x.key === row.key) : null;
          setHotRoute(r);
          link.hover(r ? routeHighlight(r) : null);
        },
        onClick: (row) => selectRoute(routes.find((x) => x.key === row.key)),
      });
      fRow.innerHTML = (st.mode ? `<span class="adv-fchip">Mode: <b>${esc(modeName.get(st.mode) || st.mode)}</b><button type="button" data-clear-mode aria-label="Clear the mode filter">✕</button></span>` : "")
        + (st.zone ? `<span class="adv-fchip">Zone: <b>${esc(zName(st.zone))}</b> · ${vis.length} routes<button type="button" data-clear-zone aria-label="Clear the zone filter">✕</button></span>` : "");
      if (!vis.length) rHost.innerHTML = `<p class="adv-empty">None of the ${routes.length} largest routes${st.zone ? " touches this zone" : ""}${st.mode ? " goes mainly by this mode" : ""}.</p>`;
      modeBar.setSelected(st.mode);
      redraw();
    };
    function setMode(m) { st.mode = m; drawRoutes(); }
    function setZone(id) {
      st.zone = id;
      drawRoutes();
      zTable?.setSelected(id ? new Set([id]) : null);
      const z = id ? zoneById.get(id) : null;
      if (z) {
        const h = zoneHighlight(z);
        link.base({ ...h, dimOthers: true }, { frame: true });
      } else link.base(null);
      drawZoneDetail();
    }
    function selectRoute(r) {
      st.route = st.route === r ? null : r;
      routeBars?.setSelected(st.route?.key ?? null);
      drawRouteDetail();
      redraw();
      if (st.route) frameRoute(st.route);
    }
    function frameRoute(r) {
      if (!r.pts.length) return;
      let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
      for (const [x, y] of r.pts) { x0 = Math.min(x0, x); x1 = Math.max(x1, x); y0 = Math.min(y0, y); y1 = Math.max(y1, y); }
      const R = grid.R * 3;
      frameBox(app, { x0: x0 - R, y0: y0 - R, x1: x1 + R, y1: y1 + R }, { pad: 40, maxScale: 2.6 });
    }
    function drawRouteDetail() {
      const r = st.route;
      if (!r) { rDetail.innerHTML = ""; return; }
      const parts = Object.entries(r.modes || {}).filter(([, s]) => s > 0).sort((a, b) => b[1] - a[1]);
      rDetail.innerHTML = `<div class="adv-card-detail">
        <h4 class="adv-h4">#${r.rank} ${esc(zoneById.get(r.a)?.name || r.a)} ⇄ ${esc(zoneById.get(r.b)?.name || r.b)}</h4>
        ${tiles([
          { label: "Value", value: money(r.value_musd * MUSD), sub: "both directions" },
          { label: "Weight", value: `${count3(r.tons_k * 1000)} t`, sub: "short tons" },
          { label: "Distance", value: `${int(r.dist_km)} km`, sub: `${count3(r.tmiles_m * 1e6)} ton-miles` },
        ])}
        <p class="adv-note"><b>By mode:</b> ${parts.map(([m, s]) => `${esc(modeName.get(m) || m)} ${pct(s)}`).join(" · ")}. <b>Largest commodity:</b> ${esc(comm[r.top_commodity] || "–")} (among each zone pair's top five).</p>
        <button type="button" class="adv-mini" data-unpin>Close</button></div>`;
      rDetail.querySelector("[data-unpin]").addEventListener("click", () => selectRoute(st.route));
    }

    // Zones table.
    const zBody = section(el, {
      title: "Freight zones",
      note: "FAF splits the country into metro areas and the rest of each state. Value of freight that starts or ends in each zone (domestic), plus its trade with other countries. Hover to outline a zone's cities; click to see its routes and partners.",
    });
    const zoneHighlight = (z) => {
      const cells = new Set();
      for (const id of z.cities || []) for (const c of metros.byKey.get(id)?.ref.footprint || []) cells.add(c);
      if (!cells.size && z.anchor?.cell != null) cells.add(z.anchor.cell);
      return { cells, cities: z.anchor?.city ? new Set([z.anchor.city]) : undefined, label: z.name };
    };
    const zones = (trade.zones || []).map((z) => ({ ...z, key: z.id }));
    zTable = createTable(zBody, {
      caption: "FAF freight zones", rank: true, maxHeight: 260, search: "Search zones",
      searchText: (z) => `${z.name} ${(z.states || []).join(" ")}`,
      columns: [
        { key: "name", label: "Zone", align: "l", width: "minmax(0,1.9fr)", value: (z) => z.short || z.name, html: (z) => `${esc(z.short || z.name)} <span class="sub">${esc((z.states || []).join("–"))}</span>`, tip: (z) => z.name },
        { key: "within", label: "Within", value: (z) => z.totals_musd?.within, text: (z) => money((z.totals_musd?.within ?? NaN) * MUSD), title: "Freight that starts and ends inside the zone" },
        { key: "other", label: "To & from zones", short: "To & from other zones", value: (z) => z.totals_musd?.other_zones, text: (z) => money((z.totals_musd?.other_zones ?? NaN) * MUSD), bar: { max: Math.max(...zones.map((z) => z.totals_musd?.other_zones || 0), 1), color: "rgba(47,164,184,.3)" } },
        { key: "foreign", label: "Imports + exports", short: "Foreign trade", value: (z) => (z.totals_musd ? (z.totals_musd.import || 0) + (z.totals_musd.export || 0) : null), text: (z) => (z.totals_musd ? money(((z.totals_musd.import || 0) + (z.totals_musd.export || 0)) * MUSD) : "–"), hideBelow: 380 },
      ],
      rows: zones, sort: { key: "other", dir: "desc" },
      onHover: (z) => link.hover(z ? zoneHighlight(z) : null),
      onClick: (z) => setZone(st.zone === z.id ? null : z.id),
    });
    const zDetail = mk("div", "adv-detail");
    zBody.appendChild(zDetail);
    function drawZoneDetail() {
      const z = st.zone ? zoneById.get(st.zone) : null;
      if (!z) { zDetail.innerHTML = ""; return; }
      zDetail.innerHTML = `<div class="adv-card-detail"><h4 class="adv-h4">${esc(z.name)}</h4>
        <p class="adv-note">${z.kind === "rest_of_state" ? "The rest of the state outside its FAF metro zones. " : ""}${int(z.counties)} counties on the map · cities: ${(z.cities || []).slice(0, 6).map((id) => esc(metros.byKey.get(id)?.name || id)).join(", ") || "none on the map"}${(z.cities || []).length > 6 ? "…" : ""}</p>
        <h4 class="adv-h4">Top partners (any state)</h4><div class="adv-zp"></div></div>`;
      barList(zDetail.querySelector(".adv-zp"), {
        compact: true, ariaLabel: "Top partner zones",
        rows: (z.partners || []).map(([id, v, m]) => ({ key: id, label: zoneById.get(id)?.name || `Zone ${id} (off the map)`, value: v, text: money(v * MUSD), sub: modeName.get(m) || m, color: MODE_COLOR[m] })),
        onHover: (row) => { const pz = row ? zoneById.get(row.key) : null; link.hover(pz ? zoneHighlight(pz) : null); },
        onClick: (row) => { if (zoneById.has(row.key)) setZone(row.key); },
      });
    }

    // The routes overlay: all visible routes (unless the map-art layer already draws them), the hovered one on top.
    const tradeLayerOn = () => {
      try { return (app.staticLayers?.() || []).some((l) => /trade/i.test(l.id) && (typeof l.visible !== "function" || l.visible())); } catch { return false; }
    };
    const overlay = {
      id: "adv-commerce-routes", z: 18, space: "world",
      draw(ctx2, cam) {
        const s = cam.scale;
        ctx2.save();
        ctx2.lineCap = "round";
        ctx2.lineJoin = "round";
        const vis = visibleRoutes();
        const hot = api.hotRoute || st.route;
        if (!tradeLayerOn()) {
          for (const r of vis) {
            if (r === hot) continue;
            const wpx = 1.2 + 4.5 * Math.sqrt(r.value_musd / vMax);
            const faded = api.modeHot && r.top_mode !== api.modeHot;
            ctx2.setLineDash(r.top_mode === "pipeline" ? [6 / s, 4 / s] : []);
            ctx2.strokeStyle = "rgba(10,8,4,.55)";
            ctx2.lineWidth = (wpx + 2) / s;
            if (!faded) ctx2.stroke(r.path2d);
            ctx2.strokeStyle = withAlpha(MODE_COLOR[r.top_mode] || "#999", faded ? 0.18 : 0.85);
            ctx2.lineWidth = wpx / s;
            ctx2.stroke(r.path2d);
          }
        }
        if (hot) {
          ctx2.setLineDash([]);
          const wpx = 2.5 + 5 * Math.sqrt(hot.value_musd / vMax);
          ctx2.strokeStyle = "rgba(10,8,4,.9)";
          ctx2.lineWidth = (wpx + 4) / s;
          ctx2.stroke(hot.path2d);
          ctx2.strokeStyle = "#ffd36a";
          ctx2.lineWidth = wpx / s;
          ctx2.stroke(hot.path2d);
          for (const p of [hot.pts[0], hot.pts[hot.pts.length - 1]]) {
            if (!p) continue;
            ctx2.beginPath();
            ctx2.arc(p[0], p[1], 5 / s, 0, Math.PI * 2);
            ctx2.fillStyle = "#fff3c4";
            ctx2.fill();
            ctx2.strokeStyle = "rgba(10,8,4,.9)";
            ctx2.lineWidth = 1.5 / s;
            ctx2.stroke();
          }
        }
        ctx2.restore();
      },
    };
    overlayOff = app.registerOverlay(overlay);
    ctx.cleanup(() => overlayOff?.());
    function setHotRoute(r) {
      if (api.hotRoute === r) return;
      api.hotRoute = r || null;
      redraw();
    }
    api.setHotRoute = setHotRoute;
    api.selectRoute = selectRoute;
    api.drawRoutes = drawRoutes;
    drawRoutes();
  }
  function redraw() { app.requestDraw(); }

  // --- ports and airports -----------------------------------------------------------------------------------------
  let portBars = null, airBars = null, portBubbles = null, airBubbles = null;
  if (infra) {
    const v = infra.vintage || {};
    const pBody = section(el, { title: "Ports", kicker: esc(v.ports || ""), note: `${esc(infra.rules?.ports || "")} Bars split each port's tonnage into domestic, imports and exports.` });
    const ports = (infra.ports || []).map((p) => ({ ...p, key: `p${p.id}` }));
    const PK = [{ key: "domestic", label: "Domestic", color: "#8fcbb8" }, { key: "imports", label: "Imports", color: "#4f9ad6" }, { key: "exports", label: "Exports", color: "#d9a457" }];
    const portHover = (p) => {
      link.hover(p ? { cells: new Set(p.cell != null ? [p.cell] : []), label: p.name } : null);
      portBubbles?.setHot(p ? p.key : null);
    };
    portBars = stackedBars(pBody, {
      rows: ports.slice(0, 15).map((p) => ({ key: p.key, label: p.name, sub: p.state, parts: { domestic: p.domestic, imports: p.imports, exports: p.exports }, total: p.tons, text: `${count3(p.tons)} t` })),
      keys: PK, fmt: (x) => `${count3(x)} t`, ariaLabel: "Busiest ports by tonnage",
      onHover: (x) => portHover(x ? ports.find((p) => p.key === x.row.key) : null),
      onClick: (x) => flyTo(ports.find((p) => p.key === x.row.key)),
    });
    pBody.insertAdjacentHTML("beforeend", swatchLegend(PK) + `<p class="adv-note">Top 15 of ${int(ports.length)} principal ports in the lower 48 (short tons).</p>`);

    const aBody = section(el, { title: "Airports", kicker: esc(v.airports || ""), note: esc(infra.rules?.airports || "") });
    const airports = (infra.airports || []).map((a) => ({ ...a, key: `a${a.id}` }));
    let aMode = "pax";
    const aHost = mk("div");
    segmented(aBody, { label: "Rank by", value: aMode, options: [{ value: "pax", label: "Passengers" }, { value: "cargo", label: "Cargo" }], onChange: (x) => { aMode = x; drawAir(); } });
    aBody.appendChild(aHost);
    const airHover = (a) => {
      link.hover(a ? { cells: new Set(a.cell != null ? [a.cell] : []), label: `${a.id} · ${a.name}` } : null);
      airBubbles?.setHot(a ? a.key : null);
    };
    function drawAir() {
      aHost.innerHTML = "";
      const k = aMode === "pax" ? "enplanements" : "cargo_lbs";
      const list = airports.filter((a) => fin(a[k])).sort((a, b) => b[k] - a[k]).slice(0, 15);
      airBars = barList(aHost, {
        rank: true, ariaLabel: aMode === "pax" ? "Busiest airports by boardings" : "Busiest cargo airports",
        rows: list.map((a) => {
          const ch = aMode === "pax" && fin(a.enplanements_prev) && a.enplanements_prev > 0 ? a.enplanements / a.enplanements_prev - 1 : null;
          return {
            key: a.key, label: `${a.id} ${a.city || a.name}`, full: a.name, value: a[k],
            text: aMode === "pax" ? count3(a[k]) : `${count3(a[k])} lb`,
            sub: aMode === "pax" ? (fin(ch) ? `${signedPct(ch)} on ${a.year - 1}` : "") : `${a.state}`,
            tone: fin(ch) ? (ch >= 0 ? "good" : "bad") : undefined, color: AIR_COLOR,
          };
        }),
        onHover: (r) => airHover(r ? airports.find((a) => a.key === r.key) : null),
        onClick: (r) => flyTo(airports.find((a) => a.key === r.key)),
      });
      aHost.insertAdjacentHTML("beforeend", `<p class="adv-note">${aMode === "pax" ? "Passenger boardings (enplanements) in the year; change on the year before." : "All-cargo landed weight in pounds, as the FAA publishes it (aircraft weight landed, not freight carried)."}</p>`);
    }
    drawAir();

    // On the map: circles for ports (tons) and airports (boardings or cargo).
    const mBody = section(el, { title: "On the map" });
    let show = "both";
    segmented(mBody, {
      label: "Show", value: show,
      options: [{ value: "both", label: "Ports & airports" }, { value: "ports", label: "Ports" }, { value: "air", label: "Airports" }, { value: "none", label: "Routes only" }],
      onChange: (x) => { show = x; drawSites(); },
    });
    const sizeBox = mk("div");
    mBody.appendChild(sizeBox);
    portBubbles = ctx.bubbles("ports", {
      rMax: 20, alpha: 0.5,
      tip: (b) => `<b>${esc(b.it.name)}</b><br>Port · <b>${count3(b.it.tons)} t</b> (${b.it.year}) · rank ${b.it.rank}<br><span class="sub">domestic ${count3(b.it.domestic)} · imports ${count3(b.it.imports)} · exports ${count3(b.it.exports)}</span>`,
      onHover: (b) => portBars?.setHot(b ? b.key : null),
      onClick: (b) => { portBars?.setHot(b.key); flyTo(b.it); },
    });
    airBubbles = ctx.bubbles("airports", {
      rMax: 16, alpha: 0.5,
      tip: (b) => `<b>${esc(b.it.id)} · ${esc(b.it.name)}</b><br>${esc(infra.hubs?.[b.it.hub] || "Cargo airport")} · <b>${count3(b.it.enplanements)}</b> boardings${fin(b.it.cargo_lbs) ? `<br><span class="sub">all-cargo ${count3(b.it.cargo_lbs)} lb landed</span>` : ""}`,
      onHover: (b) => airBars?.setHot(b ? b.key : null),
      onClick: (b) => { airBars?.setHot(b.key); flyTo(b.it); },
    });
    function drawSites() {
      const pMax = Math.max(1, ...ports.map((p) => p.tons || 0)), aMax = Math.max(1, ...airports.map((a) => a.enplanements || 0));
      portBubbles.set(show === "both" || show === "ports" ? ports.filter((p) => fin(p.tons)).map((p) => ({ key: p.key, x: p.x, y: p.y, value: p.tons, color: PORT_COLOR, it: p })) : [], { vMax: pMax });
      airBubbles.set(show === "both" || show === "air" ? airports.filter((a) => fin(a.enplanements) && a.hub && a.hub !== "N").map((a) => ({ key: a.key, x: a.x, y: a.y, value: a.enplanements, color: AIR_COLOR, it: a })) : [], { vMax: aMax });
      sizeBox.innerHTML = (show === "both" || show === "ports" ? sizeLegend([10e6, 100e6], (x) => 20 * Math.sqrt(x / pMax), (x) => `${count3(x)} t`, "Ports (blue): tonnage") : "")
        + (show === "both" || show === "air" ? sizeLegend([1e6, 20e6], (x) => 16 * Math.sqrt(x / aMax), (x) => count3(x), "Airports (gold): boardings, hubs only") : "");
    }
    drawSites();

    // Roads and rail: the counts and rules (the map-art layers draw them; switches live in the Map menu).
    const nBody = section(el, { title: "Roads and rails", open: false });
    const nRoad = [0, 0], nRail = [0, 0];
    for (const r of infra.roads || []) nRoad[r[2]] = (nRoad[r[2]] || 0) + 1;
    for (const r of infra.rail || []) nRail[r[2]] = (nRail[r[2]] || 0) + 1;
    const toggles = (() => { try { return app.ui.mapToggles?.() || []; } catch { return []; } })();
    nBody.innerHTML = `<p class="adv-note">${(infra.road_classes || []).map((c, i) => `<b>${esc(c.name)}</b>: ${int(nRoad[i] || 0)} hex links`).join(" · ")}<br>${(infra.rail_tiers || []).map((c, i) => `<b>${esc(c.name)}</b>: ${int(nRail[i] || 0)} hex links`).join(" · ")}</p>
      <p class="adv-note">${esc(infra.rules?.roads || "")}</p><p class="adv-note">${esc(infra.rules?.rail || "")}</p>
      ${toggles.length ? `<p class="adv-note">Switch the road, rail and site art on or off from the <b>Map</b> menu beside Resources.</p>` : ""}`;
  }
  function flyTo(p) {
    if (!p) return;
    focusPoint(app, p.x, p.y, Math.min(app.cam.maxScale, Math.max(app.cam.scale, (app.cam.fitScale || 1) * 3)));
  }

  el.insertAdjacentHTML("beforeend", sourcesHtml(data, [...(trade?.sources || []), ...(infra?.sources || [])], [
    "FAF values are model estimates for 2024 in current dollars and thousand short tons; zones are whole metro areas or the rest of a state, so a route between two cities stands for everything moving between their regions.",
    "Routes count only flows between zones in different states; the drawn line follows mapped highways as a drawing aid (FAF assigns no route). Pipeline routes carry crude oil or gas.",
    "Rest-of-state zones are drawn from their largest city, though the flow comes from the whole region.",
    "Port tonnage is calendar 2023 (USACE), two years older than the airport counts (FAA 2025). Port positions are port-area centroids, so long river ports are approximate.",
    "Natural Earth roads and rails are map art, not statistics: an older, simplified alignment holding about half the real rail network.",
  ]));

  return {
    onMapHover(h) {
      if (!routes.length) return;
      const list = h.cell >= 0 ? (routeCells.get(h.cell) || []).filter((r) => (!st.mode || r.top_mode === st.mode) && (!st.zone || r.a === st.zone || r.b === st.zone)) : [];
      const fromLayer = h.item?.kind === "route" ? routes.find((x) => x.a === h.item.data?.route?.a && x.b === h.item.data?.route?.b) : null;
      const r = fromLayer || list[0] || null;
      if (h.item?.kind === "port") portBars?.setHot(`p${h.item.data?.id}`);
      if (h.item?.kind === "airport") airBars?.setHot(`a${h.item.data?.id}`);
      routeBars?.setHot(r ? r.key : null);
      // Only light the route up from the map when no circle is under the pointer (those have their own hover).
      api.setHotRoute?.(r);
      const z = h.cell >= 0 ? zoneOfCell(h.cell) : null;
      zTable?.setHot(z, false);
    },
    onMapClick(ev) {
      if (ev.item?.kind === "port" || ev.item?.kind === "airport") { flyTo(ev.item.data); return true; }
      const list = ev.item?.kind === "route" ? routes.filter((x) => x.a === ev.item.data?.route?.a && x.b === ev.item.data?.route?.b) : ev.cell >= 0 ? routeCells.get(ev.cell) || [] : [];
      const r = list.find((x) => (!st.mode || x.top_mode === st.mode) && (!st.zone || x.a === st.zone || x.b === st.zone));
      if (r && !ev.city) { api.selectRoute(r); return true; }
      return false;
    },
    onEscape() {
      if (st.route) { api.selectRoute(st.route); return true; }
      if (st.zone || st.mode) { st.zone = null; st.mode = null; link.base(null); api.drawRoutes?.(); zTable?.setSelected(null); return true; }
      return false;
    },
  };

  function zoneOfCell(cell) {
    if (!zoneOfCity) { zoneOfCity = new Map(); for (const z of zoneById.values()) for (const id of z.cities || []) zoneOfCity.set(id, z.id); }
    const m = metros.rowAtCell(cell);
    return m ? zoneOfCity.get(m.key) ?? null : null;
  }
}

/** A smooth Path2D through hex-centre points (quadratic curves through the midpoints), in km. */
function smoothPath(pts) {
  const p = new Path2D();
  if (!pts.length) return p;
  p.moveTo(pts[0][0], pts[0][1]);
  if (pts.length === 2) { p.lineTo(pts[1][0], pts[1][1]); return p; }
  for (let i = 1; i < pts.length - 1; i++) {
    const mx = (pts[i][0] + pts[i + 1][0]) / 2, my = (pts[i][1] + pts[i + 1][1]) / 2;
    p.quadraticCurveTo(pts[i][0], pts[i][1], mx, my);
  }
  const l = pts[pts.length - 1];
  p.lineTo(l[0], l[1]);
  return p;
}
