// Knowledge advisor: degrees, broadband and the Science yield.
import { esc, fin, ratio, colorScale, robustDomain, RAMPS } from "../core/util.js";
import { money, pct, int, count3, fmtShort } from "../core/fmt.js";
import { barList, histogram, rampLegend } from "../core/charts.js";
import { createTable } from "../core/table.js";
import { createScatter } from "../core/scatter.js";
import { section, tiles, segmented, mapPicker, frameBtn, sourcesHtml, topBy } from "../core/view.js";
import { rowsHighlight } from "../core/data.js";
import { scienceLens } from "../core/lenses.js";

const METRO_MIN = 250000;

export function mount(ctx) {
  const { app, data, el, link } = ctx;
  const n = data.nat;
  const nation = app.data.nation;
  const { metros, counties } = data.levels;
  const sci = nation.yields?.find((y) => y.key === "science");
  const usSci = n.yields?.science;
  const usShare = ratio(usSci, n.employed);
  const wavg = (k) => {
    if (!counties) return null;
    let a = 0, w = 0;
    for (const r of counties.rows) if (fin(r.v[k]) && fin(r.v.population)) { a += r.v[k] * r.v.population; w += r.v.population; }
    return w ? a / w : null;
  };

  el.insertAdjacentHTML("beforeend", tiles([
    { label: "Science workers", value: count3(usSci), sub: `${pct(usShare)} of the employed`, color: sci?.color },
    { label: "Degree holders", value: pct(wavg("bachelors")), sub: "adults 25+, BA or higher" },
    { label: "Broadband", value: pct(wavg("broadband")), sub: "of homes subscribe" },
  ]));

  // --- the map: degrees by default (the scatter's x axis), or the Science yield's share by metro ----------------
  const mapRow = document.createElement("div");
  mapRow.className = "adv-sec adv-mapsec";
  el.appendChild(mapRow);
  const picker = mapPicker(ctx, mapRow, {
    value: counties ? "bachelors" : "science",
    options: [
      ...(counties ? [{ value: "bachelors", label: "Degrees", lens: () => "bachelors" }] : []),
      { value: "science", label: "Science jobs", lens: () => scienceLens(data) },
      ...(counties ? [{ value: "broadband", label: "Broadband", lens: () => "broadband" }] : []),
      { value: "terrain", label: "Terrain", lens: () => null },
    ],
  });

  // --- science yield --------------------------------------------------------------------------------------------
  const sBody = section(el, { title: "The Science yield", note: `${esc(sci?.description || "Workers in professional, scientific & technical services and information.")} Metros of ${count3(METRO_MIN)}+ people for the share ranking.` });
  const sHost = document.createElement("div");
  let mode = "share";
  segmented(sBody, { label: "Rank by", value: mode, options: [{ value: "share", label: "Share of jobs" }, { value: "abs", label: "Most workers" }], onChange: (v) => { mode = v; drawSci(); } });
  const sMap = document.createElement("button");
  sMap.type = "button";
  sMap.className = "adv-mini";
  sMap.textContent = "Colour the map by science jobs";
  sMap.addEventListener("click", () => picker.set("science"));
  sBody.querySelector(".adv-seg")?.after(sMap);
  sBody.appendChild(sHost);
  const share = (r) => ratio(r.v.y_science, r.v.employed);
  function drawSci() {
    sHost.innerHTML = "";
    const list = mode === "abs" ? topBy(metros.rows, (r) => r.v.y_science, { n: 12 }) : topBy(metros.rows, share, { n: 12, min: METRO_MIN });
    barList(sHost, {
      rank: true, ariaLabel: "Science leaders",
      rows: list.map((x) => ({ key: x.r.key, label: x.r.name, sub: mode === "abs" ? pct(share(x.r)) : `×${(share(x.r) / usShare).toFixed(2)} U.S.`, value: x.v, text: mode === "abs" ? count3(x.v) : pct(x.v), color: sci?.color, row: x.r })),
      onHover: (r) => link.hover(r ? rowsHighlight(metros, [r.row]) : null),
      onClick: (r) => metros.focus(r.row),
    });
  }
  drawSci();

  if (!counties) {
    el.insertAdjacentHTML("beforeend", `<p class="adv-empty pad">counties.json is not built, so the county charts are missing.</p>`);
    el.insertAdjacentHTML("beforeend", sourcesHtml(data, ["acs_1yr"], []));
    return {};
  }

  // --- degrees vs income ----------------------------------------------------------------------------------------
  const dBody = section(el, { title: "Degrees and income", note: "Share of adults with a bachelor's degree or more against median household income; colour = broadband. Drag to select; hover the map to find a place." });
  const dCtl = document.createElement("div");
  dCtl.className = "adv-row";
  dBody.appendChild(dCtl);
  let level = "counties";
  segmented(dCtl, { label: "Places", value: level, options: [{ value: "counties", label: "Counties" }, { value: "metros", label: "Metros" }], onChange: (v) => { level = v; drawDeg(); } });
  const lv = () => data.levels[level];
  const inc = (r) => (level === "counties" ? r.v.income : r.v.median_income);
  const bbDom = robustDomain(counties.rows.map((r) => r.v.broadband), 0.02) || [0.7, 0.95];
  const bbColor = colorScale({ type: "seq", domain: bbDom, ramp: RAMPS.seq });
  const scatter = createScatter(dBody, {
    height: 300, points: [],
    x: { label: "Adults with a degree", fmt: (v) => fmtShort("pct", v) },
    y: { label: "Median household income", fmt: money, log: true },
    size: { label: "Population", fmt: count3, max: 14, min: 1.4 },
    color: (c) => bbColor(c), colorLabel: "Broadband", colorFmt: (v) => pct(v),
    onHover: (p) => link.hover(p ? rowsHighlight(lv(), [p.row]) : null),
    onClick: (p) => lv().focus(p.row),
    onBrush: (set) => {
      const sel = set ? lv().rows.filter((r) => set.has(r.key)) : [];
      link.base(sel.length ? rowsHighlight(lv(), sel, { dim: true }) : null, { frame: sel.length > 1 });
      selInfo.innerHTML = sel.length ? `<b>${int(sel.length)}</b> ${level} · ${count3(sel.reduce((a, r) => a + (r.v.population || 0), 0))} people ${frameBtn()}<button type="button" class="adv-mini" data-clear>Clear</button>` : "";
    },
  });
  dBody.insertAdjacentHTML("beforeend", rampLegend({ ramp: RAMPS.seq, domain: bbDom, fmt: (v) => fmtShort("pct", v), label: "Households with broadband" }));
  const selInfo = document.createElement("div");
  selInfo.className = "adv-brushinfo";
  dBody.appendChild(selInfo);
  const clearSel = () => { scatter.setSelected(null); link.base(null); selInfo.innerHTML = ""; };
  selInfo.addEventListener("click", (e) => { if (e.target.closest("[data-clear]")) clearSel(); });
  function drawDeg() {
    clearSel();
    const rows = lv().rows;
    scatter.update({
      points: rows.map((r) => ({ key: r.key, x: r.v.bachelors, y: inc(r), s: r.v.population, c: r.v.broadband, label: r.name, sub: r.sub, row: r })),
      labels: topBy(rows, (r) => r.v.population, { n: 6 }).map((x) => x.r.key),
    });
  }
  drawDeg();

  // --- broadband ------------------------------------------------------------------------------------------------
  const bBody = section(el, { title: "Who is offline", note: "Counties by the share of households with any broadband subscription (cable, fibre, DSL, satellite or a cellular data plan). Hover a bar to outline those counties." });
  const bHost = document.createElement("div");
  bBody.appendChild(bHost);
  const bbHist = histogram(bHost, {
    values: counties.rows.map((r) => ({ key: r.key, v: r.v.broadband })), bins: 30, domain: [0.5, 1], height: 120,
    fmt: (v) => fmtShort("pct", v), unitLabel: "counties", label: "Histogram of county broadband shares",
    colorOf: (_, t) => colorScale({ type: "seq", domain: [0, 1], ramp: RAMPS.seq })(t),
    onHover: (bin) => link.hover(bin ? rowsHighlight(counties, bin.keys.map((k) => counties.byKey.get(k)), { dim: true }) : null),
  });
  createTable(bBody, {
    caption: "County broadband", rank: true, maxHeight: 240, search: "Search counties",
    rows: counties.rows.filter((r) => fin(r.v.broadband)),
    columns: [
      { key: "name", label: "County", align: "l", value: (r) => r.name, html: (r) => `${esc(r.name)} <span class="sub">${esc(r.sub)}</span>` },
      { key: "population", label: "People", value: (r) => r.v.population, text: (r) => count3(r.v.population) },
      { key: "broadband", label: "Broadband", value: (r) => r.v.broadband, text: (r) => pct(r.v.broadband), bar: { max: 1, color: "rgba(74,134,214,.4)" } },
      { key: "bachelors", label: "Degrees", value: (r) => r.v.bachelors, text: (r) => pct(r.v.bachelors) },
    ],
    sort: { key: "broadband", dir: "asc" },
    onHover: (r) => link.hover(r ? rowsHighlight(counties, [r]) : null),
    onClick: (r) => counties.focus(r),
  });

  el.insertAdjacentHTML("beforeend", sourcesHtml(data, ["acs_1yr", "acs_5yr"], [
    "Science workers are counted where they live (ACS), not where they work, and cover whole industries (a law firm's receptionist counts; a hospital's researcher does not).",
    `Degrees and broadband are ${esc(data.countyVintage)} county figures; metro values here are population-weighted roll-ups of their counties.`,
    "The Science jobs map colours metro and micro areas only (grey hexes are outside them), because the yields come from 1-year ACS data that skip small places.",
    "Broadband means any paid subscription, including a phone data plan: it measures access at home, not speed.",
    ...(data.countiesOffMap ? [`${int(data.countiesOffMap)} of the smallest counties are smaller than one hex, so selecting them outlines nothing on the map.`] : []),
  ]));

  return {
    onMapHover(h) {
      const r = h.cell >= 0 ? lv().rowAtCell(h.cell) : null;
      scatter.setHot(r ? r.key : null);
      const c = h.cell >= 0 ? counties.rowAtCell(h.cell) : null;
      bbHist.setHot(c ? bbHist.binOf(c.v.broadband)?.i ?? null : null);
    },
    onEscape() {
      if (scatter.selected) { clearSel(); return true; }
      return false;
    },
  };
}
