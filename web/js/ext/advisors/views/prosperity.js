// Prosperity & Housing advisor: income vs home values, poverty, rents and unemployment.
import { esc, fin, colorScale, RAMPS } from "../core/util.js";
import { money, dollars, pct, int, count3, fmtShort } from "../core/fmt.js";
import { barList, histogram, rampLegend } from "../core/charts.js";
import { createTable } from "../core/table.js";
import { createScatter } from "../core/scatter.js";
import { section, tiles, segmented, lensButton, sourcesHtml, topBy, frameBtn } from "../core/view.js";
import { rowsHighlight } from "../core/data.js";

const METRO_MIN = 250000;
const BIG_METRO = 500000;

export function mount(ctx) {
  const { app, data, el, link } = ctx;
  const n = data.nat;
  const { states, metros, counties } = data.levels;

  el.insertAdjacentHTML("beforeend", tiles([
    { label: "Median income", value: dollars(n.median_income), sub: "per household" },
    { label: "Median home", value: money(n.home_value), sub: `${(n.home_value / n.median_income).toFixed(1)}× income` },
    { label: "Unemployment", value: pct(n.unemployment), sub: data.vintage },
  ]));

  // --- income vs home value -------------------------------------------------------------------------------------
  const aBody = section(el, { title: "What a home costs", note: "Median home value against median household income. The dashed lines mark 3, 5 and 8 years of income: above them, homes are harder to afford. Drag a rectangle to select places; hover the map to find one here." });
  const aCtl = document.createElement("div");
  aCtl.className = "adv-row";
  aBody.appendChild(aCtl);
  let level = counties ? "counties" : "metros";
  segmented(aCtl, { label: "Places", value: level, options: [...(counties ? [{ value: "counties", label: "Counties" }] : []), { value: "metros", label: "Metros" }], onChange: (v) => { level = v; drawAfford(); } });
  for (const [id, label] of [["income", "Income lens"], ["home", "Home value lens"]]) {
    const b = lensButton(app, id, label);
    if (b) aCtl.appendChild(b);
  }
  const ratioDomain = [2, 10];
  const ratioColor = colorScale({ type: "seq", domain: ratioDomain, ramp: RAMPS.heat });
  const lv = () => data.levels[level];
  const inc = (r) => (level === "counties" ? r.v.income : r.v.median_income);
  const ratioOf = (r) => (fin(r.v.home_value) && fin(inc(r)) && inc(r) > 0 ? r.v.home_value / inc(r) : null);
  const scatter = createScatter(aBody, {
    height: 310, points: [],
    x: { label: "Median household income", fmt: money, log: true },
    y: { label: "Median home value", fmt: money, log: true },
    size: { label: "Population", fmt: count3, max: 14, min: 1.4 },
    refLines: [{ k: 3, label: "3×" }, { k: 5, label: "5×" }, { k: 8, label: "8×" }],
    color: (c) => ratioColor(c), colorLabel: "Home ÷ income", colorFmt: (v) => `×${(+v).toFixed(1)}`,
    onHover: (p) => { link.hover(p ? rowsHighlight(lv(), [p.row]) : null); affTable.setHot(p ? p.key : null, false); },
    onClick: (p) => lv().focus(p.row),
    onBrush: (set) => {
      const sel = set ? lv().rows.filter((r) => set.has(r.key)) : [];
      link.base(sel.length ? rowsHighlight(lv(), sel, { dim: true }) : null, { frame: sel.length > 1 });
      affTable.setSelected(set);
      selInfo.innerHTML = sel.length ? `<b>${int(sel.length)}</b> ${level} selected · ${count3(sel.reduce((a, r) => a + (r.v.population || 0), 0))} people ${frameBtn()}<button type="button" class="adv-mini" data-clear>Clear</button>` : "";
    },
  });
  aBody.insertAdjacentHTML("beforeend", rampLegend({ ramp: RAMPS.heat, domain: ratioDomain, fmt: (v) => `×${v}`, label: "Home value ÷ income" }));
  const selInfo = document.createElement("div");
  selInfo.className = "adv-brushinfo";
  aBody.appendChild(selInfo);
  const clearSel = () => { scatter.setSelected(null); affTable.setSelected(null); link.base(null); selInfo.innerHTML = ""; };
  selInfo.addEventListener("click", (e) => { if (e.target.closest("[data-clear]")) clearSel(); });
  aBody.insertAdjacentHTML("beforeend", `<h4 class="adv-h4">Affordability table</h4>`);
  const affTable = createTable(aBody, {
    caption: "Home value to income", rank: true, maxHeight: 260, rows: [], search: "Search places",
    columns: [
      { key: "name", label: "Place", align: "l", value: (r) => r.name, html: (r) => `${esc(r.name)} <span class="sub">${esc(r.sub)}</span>` },
      { key: "inc", label: "Income", value: inc, text: (r) => money(inc(r)) },
      { key: "home", label: "Home", value: (r) => r.v.home_value, text: (r) => money(r.v.home_value) },
      { key: "ratio", label: "Ratio", value: (r) => ratioOf(r), html: (r) => fin(ratioOf(r)) ? `<span class="dot" style="background:${ratioColor(ratioOf(r))}"></span>×${ratioOf(r).toFixed(1)}` : "–" },
    ],
    sort: { key: "ratio", dir: "desc" },
    onHover: (r) => { link.hover(r ? rowsHighlight(lv(), [r]) : null); scatter.setHot(r ? r.key : null); },
    onClick: (r) => lv().focus(r),
  });
  function drawAfford() {
    clearSel();
    const rows = lv().rows.filter((r) => fin(ratioOf(r)));
    scatter.update({
      points: rows.map((r) => ({ key: r.key, x: inc(r), y: r.v.home_value, s: r.v.population, c: ratioOf(r), label: r.name, sub: r.sub, row: r })),
      labels: topBy(rows, (r) => r.v.population, { n: 6 }).map((x) => x.r.key),
    });
    affTable.setRows(rows);
  }
  drawAfford();

  // --- poverty --------------------------------------------------------------------------------------------------
  let povHist = null;
  if (counties) {
    const pBody = section(el, { title: "Poverty", note: "Counties by the share of people below the official poverty line. Hover a bar to outline those counties; the table lists them all." });
    const pCtl = document.createElement("div");
    pCtl.className = "adv-row";
    pBody.appendChild(pCtl);
    const lb = lensButton(app, "poverty", "Poverty lens");
    if (lb) pCtl.appendChild(lb);
    const pHost = document.createElement("div");
    pBody.appendChild(pHost);
    const usPov = (() => {
      let a = 0, w = 0;
      for (const r of counties.rows) if (fin(r.v.poverty) && fin(r.v.population)) { a += r.v.poverty * r.v.population; w += r.v.population; }
      return w ? a / w : null;
    })();
    povHist = histogram(pHost, {
      values: counties.rows.map((r) => ({ key: r.key, v: r.v.poverty, w: r.v.population })), bins: 30, domain: [0, 0.45], height: 120,
      fmt: (v) => fmtShort("pct", v), unitLabel: "counties", label: "Histogram of county poverty rates",
      colorOf: (_, t) => colorScale({ type: "seq", domain: [0, 1], ramp: RAMPS.heat })(t),
      markers: fin(usPov) ? [{ v: usPov, label: `Lower 48 ${pct(usPov)}` }] : [],
      onHover: (bin) => link.hover(bin ? rowsHighlight(counties, bin.keys.map((k) => counties.byKey.get(k)), { dim: true }) : null),
    });
    const povTable = createTable(pBody, {
      caption: "County poverty rates", rank: true, maxHeight: 240, rows: counties.rows.filter((r) => fin(r.v.poverty)), search: "Search counties",
      columns: [
        { key: "name", label: "County", align: "l", value: (r) => r.name, html: (r) => `${esc(r.name)} <span class="sub">${esc(r.sub)}</span>` },
        { key: "population", label: "People", value: (r) => r.v.population, text: (r) => count3(r.v.population) },
        { key: "poverty", label: "Poverty", value: (r) => r.v.poverty, text: (r) => pct(r.v.poverty), bar: { max: 0.5, color: "rgba(232,119,74,.4)" } },
        { key: "income", label: "Income", value: (r) => r.v.income, text: (r) => money(r.v.income) },
      ],
      sort: { key: "poverty", dir: "desc" },
      onHover: (r) => link.hover(r ? rowsHighlight(counties, [r]) : null),
      onClick: (r) => counties.focus(r),
    });
    ctx.cleanup(() => povTable.destroy());
  }

  // --- rent -----------------------------------------------------------------------------------------------------
  const rBody = section(el, { title: "Rent burden by metro", note: `Median rent × 12 ÷ median household income, for metros of ${count3(METRO_MIN)}+ people. A ratio of two medians: renters usually earn less than the median household, so their real burden is higher.` });
  const rentRows = (list) => list.map((x) => ({ key: x.r.key, label: x.r.name, sub: `${dollars(x.r.v.median_rent)}/mo`, value: x.v, text: pct(x.v), row: x.r }));
  const rOpts = { onHover: (r) => link.hover(r ? rowsHighlight(metros, [r.row]) : null), onClick: (r) => metros.focus(r.row) };
  const hi = topBy(metros.rows, (r) => r.v.rent_burden, { n: 10, min: METRO_MIN });
  const lo = topBy(metros.rows, (r) => r.v.rent_burden, { n: 5, min: METRO_MIN, asc: true });
  const rmax = Math.max(...hi.map((x) => x.v));
  rBody.insertAdjacentHTML("beforeend", `<h4 class="adv-h4">Heaviest</h4>`);
  barList(rBody, { ...rOpts, rows: rentRows(hi), max: rmax, ariaLabel: "Heaviest rent burden" });
  rBody.insertAdjacentHTML("beforeend", `<h4 class="adv-h4">Lightest</h4>`);
  barList(rBody, { ...rOpts, rows: rentRows(lo), max: rmax, ariaLabel: "Lightest rent burden" });

  // --- unemployment ---------------------------------------------------------------------------------------------
  const uBody = section(el, { title: "Unemployment", note: `Unemployed ÷ civilian labour force (${esc(data.vintage)}). Metros of ${count3(BIG_METRO)}+ people.` });
  const uHost = document.createElement("div");
  let uLevel = "states";
  segmented(uBody, { label: "Areas", value: uLevel, options: [{ value: "states", label: "States" }, { value: "metros", label: "Large metros" }], onChange: (v) => { uLevel = v; drawU(); } });
  uBody.appendChild(uHost);
  function drawU() {
    uHost.innerHTML = "";
    const lvU = uLevel === "states" ? states : metros;
    const min = uLevel === "states" ? 0 : BIG_METRO;
    const high = topBy(lvU.rows, (r) => r.v.unemployment, { n: 8, min });
    const low = topBy(lvU.rows, (r) => r.v.unemployment, { n: 8, min, asc: true });
    const max = Math.max(...high.map((x) => x.v));
    const mk = (list) => list.map((x) => ({ key: x.r.key, label: x.r.name, sub: uLevel === "metros" ? x.r.sub : "", value: x.v, text: pct(x.v), tone: x.v > n.unemployment * 1.03 ? "bad" : x.v < n.unemployment * 0.97 ? "good" : null, row: x.r }));
    const o = { max, onHover: (r) => link.hover(r ? rowsHighlight(lvU, [r.row]) : null), onClick: (r) => lvU.focus(r.row) };
    uHost.insertAdjacentHTML("beforeend", `<h4 class="adv-h4">Highest</h4>`);
    barList(uHost, { ...o, rows: mk(high) });
    uHost.insertAdjacentHTML("beforeend", `<h4 class="adv-h4">Lowest</h4>`);
    barList(uHost, { ...o, rows: mk(low) });
  }
  drawU();

  el.insertAdjacentHTML("beforeend", sourcesHtml(data, ["acs_1yr", "acs_5yr"], [
    `Metro and state figures are ${esc(data.vintage)}; county figures are ${esc(data.countyVintage)} (five years pooled) and run lower in fast-rising markets.`,
    "Home values are owners' own estimates of what their home would sell for (ACS), not sale prices.",
    "Rent ÷ income compares two medians from different households; it is a rough guide, not the share of renters who are cost-burdened.",
    "Poverty uses the official federal thresholds, which are the same everywhere and do not adjust for local living costs.",
    ...(data.countiesOffMap ? [`${int(data.countiesOffMap)} of the smallest counties are smaller than one hex, so selecting them outlines nothing on the map.`] : []),
  ]));

  return {
    onMapHover(h) {
      const l = lv();
      const r = h.cell >= 0 ? l.rowAtCell(h.cell) : null;
      scatter.setHot(r ? r.key : null);
      affTable.setHot(r ? r.key : null);
      if (povHist && counties) {
        const c = h.cell >= 0 ? counties.rowAtCell(h.cell) : null;
        povHist.setHot(c ? povHist.binOf(c.v.poverty)?.i ?? null : null);
      }
    },
    onEscape() {
      if (scatter.selected) { clearSel(); return true; }
      return false;
    },
  };
}
