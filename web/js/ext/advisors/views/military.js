// Military advisor ("General"): DoD installations and ranges (MIRTA), troops by state (DMDC), defense dollars by state
// (OLDCC), garrison metros, and veterans and armed-forces residents by county (ACS, counties.json).
import { esc, fin, el as mk } from "../core/util.js";
import { int, pct, money, count3, dollars } from "../core/fmt.js";
import { stackedBars, sizeLegend, swatchLegend } from "../core/charts.js";
import { createTable } from "../core/table.js";
import { section, tiles, chips, segmented, mapPicker, sourcesHtml } from "../core/view.js";
import { rowsHighlight, extendData } from "../core/data.js";
import { countyLens, stateLens } from "../core/lenses.js";
import { frameBox, focusPoint } from "../core/frame.js";

// Original colours per component (branch hues; reserve = lighter, Guard = gold tones).
const COMP_COLOR = {
  army: "#7d9a46", navy: "#3f6fc2", air_force: "#6fb3e0", marines: "#c24a3e",
  army_reserve: "#a8bd78", navy_reserve: "#8aa6db", air_force_reserve: "#a9d3ee", marine_reserve: "#e08f86",
  army_guard: "#d6b04a", air_guard: "#e6cf86", other: "#9a9aa8",
};
const COMP_SHORT = {
  army: "Army", navy: "Navy", air_force: "Air Force", marines: "Marines", army_reserve: "Army Res.", navy_reserve: "Navy Res.",
  air_force_reserve: "AF Res.", marine_reserve: "Marine Res.", army_guard: "Army Guard", air_guard: "Air Guard", other: "Other DoD",
};
const COMP_GROUPS = [
  { id: "active", label: "Active duty", ids: ["army", "navy", "air_force", "marines"] },
  { id: "reserve", label: "Reserve", ids: ["army_reserve", "navy_reserve", "air_force_reserve", "marine_reserve"] },
  { id: "guard", label: "National Guard", ids: ["army_guard", "air_guard"] },
  { id: "other", label: "Other", ids: ["other"] },
];
const TIER_LABEL = { major: "Major", base: "Base", site: "Site" };
const AREA_CAP = 3000; // km²: circles stop growing here (a few ranges are 10,000 km²+)
const STEEL_RAMP = ["#141b26", "#2a3a52", "#46627f", "#6f91ad", "#a9c3d6", "#eef4f8"];
const SPEND_RAMP = ["#1b1410", "#3e2a1c", "#6e4626", "#a66f34", "#d9a457", "#f8e2a6"];

export async function mount(ctx) {
  const { app, data, el, link } = ctx;
  await extendData(data, ["military"]);
  const mil = app.data.military;
  if (!mil) {
    el.innerHTML = `<p class="adv-empty pad">military.json is not built, so the General has nothing to report.</p>`;
    return {};
  }
  const { states: stLevel, metros, counties } = data.levels;
  const comps = (mil.components || []).map((c) => ({ ...c, color: COMP_COLOR[c.id] || "#9a9aa8", short: COMP_SHORT[c.id] || c.name }));
  const compById = new Map(comps.map((c) => [c.id, c]));
  const insts = (mil.installations || []).map((it, i) => ({ ...it, key: it.id || String(i) }));
  const instByKey = new Map(insts.map((it) => [it.key, it]));
  const nat = mil.national || {};
  const vint = mil.vintage || {};
  const stateIdx = (abbr) => stLevel.byKey.get(abbr)?.ref ?? null;
  const stateName = (abbr) => stLevel.byKey.get(abbr)?.name || STATE_NAMES[abbr] || abbr;
  const instCells = (it) => (it.cells && it.cells.length ? it.cells : it.cell != null ? [it.cell] : []);
  // cell → the installation boundaries covering it (largest first), for map → dashboard.
  const cellInst = new Map();
  for (const it of insts) for (const c of it.cells || []) if (!cellInst.has(c)) cellInst.set(c, it);
  const cellRange = new Map();
  for (const r of mil.ranges || []) for (const c of r.cells || []) if (!cellRange.has(c)) cellRange.set(c, r);

  const st = { comps: new Set(comps.map((c) => c.id)), tier: "big", state: null };

  // --- headline -------------------------------------------------------------------------------------------------
  const P = nat.personnel || {}, S = nat.spending || {};
  el.insertAdjacentHTML("beforeend", tiles([
    { label: "Active duty", value: count3(P.active?.total), sub: `+ ${count3(P.guard_reserve?.total)} Guard & Reserve`, color: "#a9c3d6" },
    { label: "Defense spending", value: money(S.total), sub: `${pct(S.pct_gdp)} of GDP · ${esc(vint.spending || "")}` },
    { label: "Veterans", value: count3(nat.veterans), sub: "civilians 18+ (ACS)" },
  ]) + `<p class="adv-callout">No headcount is published for individual bases, so the map sizes them by the land inside their boundary. Troop numbers below are by state.</p>`);

  // --- map colour -----------------------------------------------------------------------------------------------
  const mapRow = mk("div", "adv-sec adv-mapsec");
  el.appendChild(mapRow);
  const afLens = () => countyLens(data, "armed_forces_pct", {
    name: "Armed forces residents", emoji: "🎖️", ramp: STEEL_RAMP,
    description: "Active-duty service members among residents aged 16+, by county, counted where they live (ACS 5-year B23025). Each hex takes the county at its centre.",
  });
  const perResLens = () => stateLens(data, "def-per-resident", (abbr) => mil.states?.[abbr]?.spending?.per_resident ?? null, {
    name: "Defense $ per resident", emoji: "💵", format: "usd", unit: "$ per resident", ramp: SPEND_RAMP, source: "oldcc_spending",
    description: `Defense contracts, grants and payroll per resident, by state (${vint.spending || "OLDCC"}). Contracts follow the place of performance.`,
  });
  mapPicker(ctx, mapRow, {
    value: "terrain",
    options: [
      { value: "terrain", label: "Terrain", lens: () => null },
      ...(app.lensById?.get("veterans") ? [{ value: "veterans", label: "Veterans", lens: () => "veterans" }] : []),
      ...(counties ? [{ value: "af", label: "Troops at home", lens: afLens }] : []),
      { value: "dollars", label: "$ per resident", lens: perResLens },
    ],
  });

  // --- installations --------------------------------------------------------------------------------------------
  const iBody = section(el, {
    title: "Installations", kicker: esc(vint.installations || "DoD MIRTA"),
    note: `Every active DoD site in the lower 48: circle area follows the land inside its boundary (capped at ${int(AREA_CAP)} km²; sites without a boundary are dots). ${esc(mil.rules?.tier || "")}`,
  });
  const counts = new Map();
  for (const it of insts) counts.set(it.component, (counts.get(it.component) || 0) + 1);
  const compChips = chips(iBody, {
    label: "Components",
    items: comps.filter((c) => counts.get(c.id)).map((c) => ({ key: c.id, label: c.short, color: c.color, count: counts.get(c.id) })),
    onChange: (on) => { st.comps = on; apply(); },
  });
  const qRow = mk("div", "adv-row");
  qRow.innerHTML = COMP_GROUPS.map((g) => `<button type="button" class="adv-mini" data-g="${g.id}">${esc(g.label)}</button>`).join("") + `<button type="button" class="adv-mini" data-g="all">All</button>`;
  iBody.appendChild(qRow);
  qRow.addEventListener("click", (e) => {
    const g = e.target.closest("[data-g]")?.dataset.g;
    if (!g) return;
    st.comps = g === "all" ? new Set(comps.map((c) => c.id)) : new Set(COMP_GROUPS.find((x) => x.id === g).ids);
    compChips.set(st.comps);
    apply();
  });
  const tRow = mk("div", "adv-row");
  iBody.appendChild(tRow);
  segmented(tRow, {
    label: "Size", value: st.tier,
    options: [{ value: "major", label: "Major" }, { value: "big", label: "Major & bases" }, { value: "all", label: "Every site" }],
    onChange: (v) => { st.tier = v; apply(); },
  });
  const stateChip = mk("div", "adv-row adv-filterbar");
  iBody.appendChild(stateChip);
  stateChip.addEventListener("click", (e) => { if (e.target.closest("[data-clear-state]")) setState(null); });
  const legend = mk("div");
  legend.innerHTML = sizeLegend([100, 1000, AREA_CAP], (v) => 14 * Math.sqrt(v / AREA_CAP), (v) => `${int(v)} km²`, "Circle area = land inside the boundary");
  iBody.appendChild(legend);
  const instTable = createTable(iBody, {
    caption: "DoD installations", search: "Search installations, states", rank: true, maxHeight: 280,
    searchText: (r) => `${r.name} ${r.state} ${compById.get(r.component)?.name || ""}`,
    columns: [
      { key: "name", label: "Installation", align: "l", width: "minmax(0,1.9fr)", value: (r) => r.name, html: (r) => `<i class="dot" style="background:${esc(compById.get(r.component)?.color || "#999")}"></i>${esc(r.name)}${r.joint ? ` <span class="sub">joint</span>` : ""}`, tip: (r) => `${r.name} · ${compById.get(r.component)?.name || r.component}${r.status !== "act" ? ` · ${STATUS[r.status] || r.status}` : ""}` },
      { key: "component", label: "Service", align: "l", width: "minmax(0,.9fr)", hideBelow: 400, value: (r) => compById.get(r.component)?.short, text: (r) => compById.get(r.component)?.short || r.component },
      { key: "tier", label: "Tier", align: "l", width: "46px", value: (r) => ({ major: 0, base: 1, site: 2 })[r.tier], text: (r) => TIER_LABEL[r.tier] || r.tier },
      { key: "state", label: "State", align: "l", width: "40px", value: (r) => r.state, text: (r) => r.state || "–" },
      { key: "area", label: "Area", width: "minmax(0,.8fr)", value: (r) => r.area_km2, text: (r) => (fin(r.area_km2) ? `${count3(r.area_km2)} km²` : "–"), title: "Land inside the boundary (null: no boundary published)" },
    ],
    rows: [], sort: { key: "area", dir: "desc" },
    onHover: (r) => {
      link.hover(r ? { cells: new Set(instCells(r)), label: r.name } : null);
      bubbles.setHot(r ? r.key : null);
    },
    onClick: (r) => flyTo(r),
  });
  function flyTo(r) {
    if (r.cells && r.cells.length > 2) {
      const b = app.highlightBounds({ cells: new Set(r.cells) });
      if (b) return frameBox(app, b, { pad: 60, maxScale: 3 });
    }
    focusPoint(app, r.x, r.y, Math.min(app.cam.maxScale, Math.max(app.cam.scale, (app.cam.fitScale || 1) * 3)));
  }

  // --- by state -------------------------------------------------------------------------------------------------
  const sBody = section(el, {
    title: "Troops and dollars by state",
    note: `Personnel by duty state (${esc(vint.personnel || "DMDC")}); spending by place of performance (${esc(vint.spending || "OLDCC")}). Click a state to see its detail and filter the bases to it.`,
  });
  const sCtl = mk("div", "adv-row");
  sBody.appendChild(sCtl);
  let metric = "troops", showAll = false;
  segmented(sCtl, {
    label: "Measure", value: metric,
    options: [{ value: "troops", label: "Troops" }, { value: "dollars", label: "Dollars" }, { value: "perres", label: "Per resident" }, { value: "gdp", label: "% of GDP" }],
    onChange: (v) => { metric = v; drawStates(); },
  });
  const allBtn = mk("button", "adv-mini");
  allBtn.type = "button";
  sCtl.appendChild(allBtn);
  allBtn.addEventListener("click", () => { showAll = !showAll; drawStates(); });
  const sHost = mk("div");
  sBody.appendChild(sHost);
  const sLegend = mk("div");
  sBody.appendChild(sLegend);
  const detail = mk("div", "adv-detail");
  sBody.appendChild(detail);
  let stacks = null;
  const METRICS = {
    troops: {
      keys: [{ key: "active", label: "Active duty", color: "#6f91ad" }, { key: "guard", label: "Guard & Reserve", color: "#d6b04a" }, { key: "civ", label: "DoD civilians", color: "#9a9aa8" }],
      parts: (s) => ({ active: s.personnel?.active?.total, guard: s.personnel?.guard_reserve?.total, civ: s.personnel?.civilian?.total }),
      total: (s) => s.personnel?.total, fmt: count3,
    },
    dollars: {
      keys: [{ key: "contracts", label: "Contracts", color: "#d9a457" }, { key: "payroll", label: "Payroll", color: "#6f91ad" }, { key: "grants", label: "Grants", color: "#8fcbb8" }],
      parts: (s) => ({ contracts: s.spending?.contracts, payroll: s.spending?.payroll, grants: s.spending?.grants }),
      total: (s) => s.spending?.total, fmt: money,
    },
    perres: { keys: [{ key: "v", label: "$ per resident", color: "#d9a457" }], parts: (s) => ({ v: s.spending?.per_resident }), total: (s) => s.spending?.per_resident, fmt: dollars },
    gdp: { keys: [{ key: "v", label: "Share of state GDP", color: "#c46a4f" }], parts: (s) => ({ v: s.spending?.pct_gdp }), total: (s) => s.spending?.pct_gdp, fmt: (v) => pct(v) },
  };
  const milStates = Object.entries(mil.states || {}).filter(([, s]) => s);
  function drawStates() {
    const M = METRICS[metric];
    const rows = milStates
      .map(([abbr, s]) => ({ key: abbr, label: stateName(abbr), sub: stateIdx(abbr) == null ? "off map" : "", parts: M.parts(s), total: M.total(s), text: M.fmt(M.total(s)) }))
      .filter((r) => fin(r.total))
      .sort((a, b) => b.total - a.total);
    const shown = showAll ? rows : rows.slice(0, 15);
    allBtn.textContent = showAll ? "Top 15" : `All ${rows.length}`;
    sHost.innerHTML = "";
    stacks = stackedBars(sHost, {
      rows: shown, keys: M.keys, fmt: (v) => M.fmt(v), ariaLabel: "Defense by state",
      onHover: (x) => {
        if (!x) return link.hover(null);
        const i = stateIdx(x.row.key);
        link.hover(i != null ? { states: new Set([i]), dimOthers: true, label: x.row.label } : null);
      },
      onClick: (x) => setState(st.state === x.row.key ? null : x.row.key, true),
    });
    if (st.state) stacks.setSelected(st.state);
    sLegend.innerHTML = M.keys.length > 1 ? swatchLegend(M.keys) : "";
  }

  function drawDetail() {
    const abbr = st.state;
    const s = abbr ? mil.states?.[abbr] : null;
    if (!abbr) { detail.innerHTML = ""; return; }
    if (!s) { detail.innerHTML = `<p class="adv-empty">No defense figures for ${esc(stateName(abbr))}.</p>`; return; }
    const p = s.personnel, sp = s.spending;
    const branch = p ? [["Army", p.active?.army], ["Navy", p.active?.navy], ["Marine Corps", p.active?.marines], ["Air Force", p.active?.air_force], ["Space Force", p.active?.space_force], ["Coast Guard", p.active?.coast_guard]] : [];
    const nInst = insts.filter((it) => it.state === abbr).length;
    detail.innerHTML = `<div class="adv-card-detail">
      <h4 class="adv-h4">${esc(stateName(abbr))}</h4>
      ${tiles([
        { label: "Active duty", value: p ? int(p.active?.total) : "–", sub: p ? `${int(p.guard_reserve?.total)} Guard & Reserve` : "no DMDC figure" },
        { label: "Defense $", value: sp ? money(sp.total) : "–", sub: sp ? `${pct(sp.pct_gdp)} of GDP · ${dollars(sp.per_resident)}/resident` : "no OLDCC figure" },
        { label: "Installations", value: int(nInst), sub: "MIRTA sites filed here" },
      ])}
      ${p ? `<p class="adv-note"><b>Active duty by service:</b> ${branch.filter(([, v]) => fin(v) && v > 0).map(([k, v]) => `${esc(k)} ${int(v)}`).join(" · ")}. DoD civilians: ${int(p.civilian?.total)}.</p>` : ""}
      ${sp ? `<p class="adv-note"><b>Where the money goes:</b> contracts ${money(sp.contracts)}, payroll ${money(sp.payroll)}, grants ${money(sp.grants)}.</p>` : ""}
      ${sp?.top_vendors?.length ? `<p class="adv-note"><b>Top contractors:</b> ${sp.top_vendors.map((v) => `${esc(v.name)} ${money(v.contracts)}`).join(" · ")}</p>` : ""}
      ${sp?.top_counties?.length ? `<h4 class="adv-h4">Contract dollars by county</h4><div class="adv-chiplist">${sp.top_counties.map((c, i) => `<button type="button" class="adv-mini" data-county="${i}"${c.county ? "" : " disabled title=\"Not a county on the map (old Connecticut counties, Alaska, Hawaii)\""}>${esc(c.name)} <b>${money(c.contracts)}</b></button>`).join("")}</div>` : ""}
    </div>`;
    detail.querySelectorAll("[data-county]").forEach((b) => {
      const c = sp.top_counties[+b.dataset.county];
      const row = c.county ? counties?.byKey.get(c.county) : null;
      if (!row) return;
      b.addEventListener("pointerenter", () => link.hover(rowsHighlight(counties, [row])));
      b.addEventListener("pointerleave", () => link.hover(null));
      b.addEventListener("click", () => counties.focus(row));
    });
  }

  // --- garrisons ------------------------------------------------------------------------------------------------
  const gBody = section(el, {
    title: "Garrison cities",
    note: `${esc(mil.rules?.encampment || mil.rule || "")}`,
  });
  const garrisons = Object.entries(mil.cities || {}).map(([id, c]) => ({ ...c, key: id, row: metros.byKey.get(id) })).filter((g) => g.row);
  const gTable = createTable(gBody, {
    caption: "Garrison cities", rank: true, maxHeight: 250, search: "Search metros",
    searchText: (g) => g.row.name,
    columns: [
      { key: "name", label: "Metro", align: "l", width: "minmax(0,1.8fr)", value: (g) => g.row.name, html: (g) => `${esc(g.row.name)} <span class="sub">${esc(g.row.sub)}</span>`, tip: (g) => `${g.row.name}: ${(g.installations || []).map((id) => instByKey.get(id)?.name || id).join(", ")}` },
      { key: "tier", label: "Tier", align: "l", width: "minmax(0,.8fr)", value: (g) => (g.tier === "fortress" ? 0 : 1), text: (g) => `${g.tier === "fortress" ? "Fortress" : "Garrison"}${g.naval ? " ⚓" : ""}` },
      { key: "af", label: "In uniform", value: (g) => g.armed_forces, text: (g) => count3(g.armed_forces), title: "Armed-forces residents 16+ (ACS 5-year, where they live)" },
      { key: "share", label: "Share", value: (g) => g.share, text: (g) => pct(g.share), title: "Share of residents 16+ on active duty", bar: { max: Math.max(...garrisons.map((g) => g.share || 0), 0.01), color: "rgba(135,150,171,.35)" } },
    ],
    rows: garrisons, sort: { key: "share", dir: "desc" },
    onHover: (g) => link.hover(g ? rowsHighlight(metros, [g.row]) : null),
    onClick: (g) => metros.focus(g.row),
  });
  gBody.insertAdjacentHTML("beforeend", `<p class="adv-note">⚓ naval: the metro's qualifying Navy and Marine Corps installations outweigh its Army and Air Force ones (a major installation counts 4, a base 1). A big metro qualifies on headcount only when at least 2,000 residents and 0.3% of its residents 16+ serve: read the share column.</p>`);

  // --- ranges ---------------------------------------------------------------------------------------------------
  const rBody = section(el, { title: "Ranges and restricted ground", note: esc(mil.rules?.ranges || "") });
  const ranges = (mil.ranges || []).map((r) => ({ ...r, key: r.id }));
  const rTable = createTable(rBody, {
    caption: "Largest military ranges", rank: true, maxHeight: 240,
    columns: [
      { key: "name", label: "Range", align: "l", width: "minmax(0,2fr)", value: (r) => r.name, html: (r) => `<i class="dot" style="background:${esc(compById.get(r.component)?.color || "#999")}"></i>${esc(r.name)}` },
      { key: "state", label: "State", align: "l", width: "40px", value: (r) => r.state, text: (r) => r.state || "–" },
      { key: "area", label: "Area", width: "minmax(0,.9fr)", value: (r) => r.area_km2, text: (r) => `${count3(r.area_km2)} km²`, bar: { max: Math.max(...ranges.map((r) => r.area_km2 || 0), 1), color: "rgba(194,74,62,.3)" } },
    ],
    rows: ranges, sort: { key: "area", dir: "desc" },
    onHover: (r) => link.hover(r ? { cells: new Set(r.cells || []), dimOthers: true, color: "#f09a7f", label: r.name } : null),
    onClick: (r) => { const b = app.highlightBounds({ cells: new Set(r.cells || []) }); if (b) frameBox(app, b, { pad: 60, maxScale: 2.6 }); },
  });

  // --- veterans -------------------------------------------------------------------------------------------------
  const vBody = section(el, {
    title: "Veterans and troops at home",
    note: `Civilian veterans 18+ and active-duty residents 16+, counted where they live (${esc(vint.acs || data.countyVintage)}). State and metro shares are summed counts ÷ summed bases of their counties.`,
  });
  let vLevel = "states";
  const vCtl = mk("div", "adv-row");
  vBody.appendChild(vCtl);
  segmented(vCtl, {
    label: "Places", value: vLevel,
    options: [{ value: "states", label: "States" }, { value: "metros", label: "Metros" }, ...(counties ? [{ value: "counties", label: "Counties" }] : [])],
    onChange: (v) => { vLevel = v; vTable.setRows(vRows()); },
  });
  const vRows = () => data.levels[vLevel].rows.filter((r) => fin(r.v.veterans_pct) && (vLevel !== "counties" || (r.v.population || 0) >= 10000));
  const vTable = createTable(vBody, {
    caption: "Veterans and armed forces", rank: true, maxHeight: 260, search: "Search places",
    columns: [
      { key: "name", label: "Place", align: "l", width: "minmax(0,1.7fr)", value: (r) => r.name, html: (r) => `${esc(r.name)}${r.level !== "states" && r.sub ? ` <span class="sub">${esc(r.sub)}</span>` : ""}` },
      { key: "veterans", label: "Veterans", value: (r) => r.v.veterans, text: (r) => count3(r.v.veterans) },
      { key: "veterans_pct", label: "Share", value: (r) => r.v.veterans_pct, text: (r) => pct(r.v.veterans_pct), bar: { max: 0.2, color: "rgba(135,150,171,.35)" }, title: "Veterans among civilians 18+" },
      { key: "armed_forces_pct", label: "In uniform", value: (r) => r.v.armed_forces_pct, text: (r) => pct(r.v.armed_forces_pct), title: "Active duty among residents 16+" },
    ],
    rows: vRows(), sort: { key: "veterans_pct", dir: "desc" },
    onHover: (r) => link.hover(r ? rowsHighlight(data.levels[vLevel], [r]) : null),
    onClick: (r) => data.levels[vLevel].focus(r),
  });
  vBody.insertAdjacentHTML("beforeend", `<p class="adv-note">Counties of 10,000+ people only (small-county estimates are noisy).</p>`);

  el.insertAdjacentHTML("beforeend", sourcesHtml(data, mil.sources || ["mirta", "dmdc_personnel", "oldcc_spending", "acs_5yr"], [
    esc(mil.caveats || ""),
    `Personnel are ${esc(vint.personnel || "DMDC")}; spending is ${esc(vint.spending || "OLDCC")}, the newest edition published. They are different years.`,
    "Installation tiers come from boundary area and component, never headcount: a 'major' range can have few people, a small headquarters many.",
    "MIRTA files a few sites under an odd state (the Utah Test and Training Range is filed as Nevada): the table keeps MIRTA's state; ranges use the state holding most of their hexes.",
    "Alaska, Hawaii and Puerto Rico are in the state charts but not on the map.",
  ].filter(Boolean)));

  // --- map ------------------------------------------------------------------------------------------------------
  const bubbles = ctx.bubbles("bases", {
    rMax: 14, minR: 2.6, alpha: 0.6,
    tip: (b) => {
      const it = b.it, c = compById.get(it.component);
      return `<b>${esc(it.name)}</b><br>${esc(c?.name || it.component)} · ${esc(TIER_LABEL[it.tier] || it.tier)}${fin(it.area_km2) ? ` · ${count3(it.area_km2)} km²` : ""}${it.state ? ` · ${esc(it.state)}` : ""}${it.status !== "act" ? `<br><span class="sub">${esc(STATUS[it.status] || it.status)}</span>` : ""}`;
    },
    onHover: (b) => {
      instTable.setHot(b ? b.key : null);
      link.hover(b && b.it.cells?.length ? { cells: new Set(b.it.cells), noBeacon: true } : null);
    },
    onClick: (b) => { instTable.setHot(b.key); flyTo(b.it); },
  });

  function visible() {
    return insts.filter((it) => st.comps.has(it.component) && (st.tier === "all" || it.tier === "major" || (st.tier === "big" && it.tier === "base")) && (!st.state || it.state === st.state));
  }
  function setState(abbr, fly = false) {
    st.state = abbr;
    stacks?.setSelected(abbr);
    if (!abbr) stacks?.setSelected(null);
    apply();
    drawDetail();
    const i = abbr ? stateIdx(abbr) : null;
    if (i != null && fly) {
      const b = app.highlightBounds({ states: new Set([i]) });
      if (b) frameBox(app, b, { pad: 30, maxScale: 2.6 });
    }
  }
  function apply() {
    const vis = visible();
    instTable.setRows(vis);
    bubbles.set(vis.map((it) => ({ key: it.key, x: it.x, y: it.y, value: fin(it.area_km2) ? Math.min(AREA_CAP, it.area_km2) : 0, color: compById.get(it.component)?.color || "#999", it })), { vMax: AREA_CAP });
    const i = st.state ? stateIdx(st.state) : null;
    link.base(i != null ? { states: new Set([i]), dimOthers: true } : null);
    stateChip.innerHTML = st.state
      ? `<span class="adv-fchip">State: <b>${esc(stateName(st.state))}</b> · ${int(vis.length)} sites<button type="button" data-clear-state aria-label="Clear the state filter">✕</button></span>`
      : "";
  }
  drawStates();
  apply();

  return {
    onMapHover(h) {
      const owner = h.cell >= 0 ? app.w.raw.owner[h.cell] : -1;
      const abbr = owner >= 0 ? stLevel.rows[owner]?.key : null;
      stacks?.setHot(abbr || null);
      const r = h.cell >= 0 ? cellRange.get(h.cell) : null;
      rTable.setHot(r ? r.id : null, !!r);
      // A fort or range drawn by the map-art layer names its record; otherwise the boundary under the pointer.
      const inst = h.item?.kind === "fort" ? instByKey.get(h.item.data?.id) : h.cell >= 0 ? cellInst.get(h.cell) : null;
      if (inst) instTable.setHot(inst.key);
      if (h.item?.kind === "range") rTable.setHot(h.item.data?.id ?? null);
      const m = h.cell >= 0 ? metros.rowAtCell(h.cell) : null;
      gTable.setHot(m && mil.cities?.[m.key] ? m.key : null, false);
      const vr = h.cell >= 0 ? data.levels[vLevel]?.rowAtCell(h.cell) : null;
      vTable.setHot(vr ? vr.key : null, false);
    },
    onMapClick(ev) {
      const fort = ev.item?.kind === "fort" ? instByKey.get(ev.item.data?.id) : null;
      if (fort) { instTable.setHot(fort.key); flyTo(fort); return true; }
      // A click on a state (not a city) filters the General's view to it; a second click clears it.
      if (ev.city || ev.state == null) return false;
      const abbr = stLevel.rows[ev.state]?.key;
      if (!abbr) return false;
      setState(st.state === abbr ? null : abbr);
      return true;
    },
    onEscape() {
      if (st.state) { setState(null); return true; }
      return false;
    },
  };
}

const STATUS = { act: "Active", semi: "Semi-active", care: "Caretaker status" };
const STATE_NAMES = { AK: "Alaska", HI: "Hawaii", PR: "Puerto Rico" };

