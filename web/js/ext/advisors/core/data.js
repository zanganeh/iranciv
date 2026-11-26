// The advisors' data model: three "levels" (states, metros, counties) of rows with typed fields, plus the links
// between rows and the hex map (row → highlight, cell → row). Built once from app.data (counties.json optional).
//
//   level = {id, label, noun, rows: [row], fields: [field], field(key), value(row, key),
//            highlight(rows, opts) → app.state.highlight, rowAtCell(cell) → row | null, focus(row), byKey: Map}
//   row   = {key, name, sub, level, ref, v: {fieldKey: value}}
//   field = {key, label, format, unit, higher_is, source, description, lens?, group, derived?}
import { fin, ratio } from "./util.js";
import { frameBox } from "./frame.js";

const COUNTY_LENS = {
  density: "density", income: "income", home_value: "home", poverty: "poverty", median_age: "age", foreign_born: "foreign_born",
  bachelors: "bachelors", broadband: "broadband", commute: "commute", homeownership: "homeownership", uninsured: "uninsured",
  forest: "forest", developed: "developed",
  // county lenses: Religion Census, CDC PLACES wellbeing (id amenities), FEMA disasters, ACS veterans
  faith: "faith", faith_tradition: "faith_tradition", amenities: "amenities", disasters: "disasters", veterans_pct: "veterans",
};
// County measures we can roll up to states and metros as population-weighted means.
const ROLLUP = ["density", "poverty", "median_age", "foreign_born", "bachelors", "broadband", "commute", "homeownership", "uninsured", "faith", "amenities"];
// Rolled-up means that only approximate the area's true value (a median, a mean of rates or of an index).
const APPROX = new Set(["median_age", "commute", "faith", "amenities"]);
// County counts summed to states and metros, with their share recomputed from the implied denominators
// (count ÷ share = the county's base population), so the area's share is exact, not a mean of county shares.
const SUMS = [["veterans", "veterans_pct"], ["armed_forces", "armed_forces_pct"]];
const LAND_KEYS = ["forest", "crop", "pasture", "grass", "shrub", "wetland", "developed"];
export const LAND_LABEL = {
  forest: "Forest", crop: "Cropland", pasture: "Pasture & hay", grass: "Grassland", shrub: "Scrub & desert", wetland: "Wetland", developed: "Built-up",
};
export const LAND_COLOR = {
  forest: "#3f7a3a", crop: "#d2c457", pasture: "#93b852", grass: "#b9b06a", shrub: "#c9a874", wetland: "#4f9a86", developed: "#9a8f9e",
};
export { LAND_KEYS };

export function buildData(app) {
  const { cities = [], states = [], nation = {}, counties, resources, sources = [] } = app.data;
  const w = app.w;
  const grid = w.grid;
  const owner = w.raw.owner;
  const countyOf = w.raw.county || [];
  const n = grid.size;

  // --- cell indexes ---------------------------------------------------------------------------------------------
  const stateCells = new Map();
  const countyCells = new Map();
  for (let i = 0; i < n; i++) {
    const o = owner[i];
    if (!(o >= 0)) continue;
    if (!stateCells.has(o)) stateCells.set(o, []);
    stateCells.get(o).push(i);
    const c = countyOf[i];
    if (c >= 0) {
      if (!countyCells.has(c)) countyCells.set(c, []);
      countyCells.get(c).push(i);
    }
  }
  // cell → metro: footprints, smaller metros first so a bigger metro's majority wins; then every city's own cell.
  const cellMetro = new Map();
  for (let k = cities.length - 1; k >= 0; k--) for (const cell of cities[k].footprint || []) cellMetro.set(cell, cities[k]);
  for (const c of cities) if (c.cell != null) cellMetro.set(c.cell, c);

  const srcTitle = new Map(sources.map((s) => [s.id, s]));
  const nat = nation.nation || {};
  const vintage = nation.vintage || "ACS 1-year";
  const countyVintage = counties?.vintage || "ACS 5-year";

  // --- fields ---------------------------------------------------------------------------------------------------
  const F = (key, label, format, o = {}) => ({ key, label, format, unit: o.unit || null, higher_is: o.higher_is || "neutral", source: o.source || "", description: o.description || "", lens: o.lens || null, group: o.group || "Economy", derived: !!o.derived });
  const econFields = (src) => [
    F("population", "Population", "int", { unit: "people", source: src, group: "People", description: `Residents (${vintage}, B01003).`, lens: "density" }),
    F("growth", "Growth", "signed", { unit: "population change", higher_is: "neutral", source: "pep_population", group: "People", description: `Population change ${(nat.growth_years || []).join("–")} (Census population estimates, same borders both years).` }),
    F("gdp", "GDP", "money", { unit: "$ per year", source: "bea_gdp", description: `${nation.gdp_vintage || "BEA"}: GDP in current dollars, counted where the work is done.` }),
    F("gdp_pc", "GDP per person", "usd", { unit: "$ per resident", higher_is: "good", source: "bea_gdp", description: "GDP ÷ residents (BEA GDP year ÷ ACS population year)." }),
    F("median_income", "Income", "usd", { unit: "median household income", higher_is: "good", source: src, group: "Prosperity", description: `Median household income (${vintage}).`, lens: "income" }),
    F("median_rent", "Rent", "usd", { unit: "median gross rent / month", source: src, group: "Prosperity", description: `Median gross rent per month (${vintage}).` }),
    F("home_value", "Home value", "usd", { unit: "median home value", source: src, group: "Prosperity", description: `Median value of owner-occupied homes (${vintage}).`, lens: "home" }),
    F("affordability", "Home value ÷ income", "ratio", { unit: "years of income", higher_is: "bad", group: "Prosperity", description: "Median home value ÷ median household income: how many years of income a typical home costs.", derived: true }),
    F("rent_burden", "Rent ÷ income", "pct", { unit: "median rent × 12 ÷ median income", higher_is: "bad", source: src, group: "Prosperity", description: "Median rent × 12 ÷ median household income (a ratio of medians, not the share of renters who are burdened)." }),
    F("unemployment", "Unemployment", "pct", { unit: "unemployed ÷ labour force", higher_is: "bad", source: src, group: "Prosperity", description: `Unemployed ÷ civilian labour force (${vintage}, B23025).` }),
    F("employed", "Employed", "int", { unit: "employed residents", source: src, description: `Employed residents, where they live (${vintage}).` }),
    ...(nation.yields || []).filter((y) => y.unit !== "usd").map((y) => F(`y_${y.key}`, `${y.label} workers`, "int", { unit: "workers", source: src, group: "Yields", description: y.description })),
  ];
  const rollupFields = () => counties ? [...ROLLUP.map((k) => {
    const f = counties.fields.find((x) => x.key === k);
    if (!f) return null;
    const approx = APPROX.has(k);
    return F(k, f.label + (approx ? " ≈" : ""), f.format, {
      unit: f.unit, higher_is: f.higher_is, source: f.source || "acs_5yr", group: groupOf(k), lens: COUNTY_LENS[k],
      description: k === "density" ? `Residents ÷ land area, summed over member counties (${countyVintage}).`
        : `${f.description} Rolled up from counties as a population-weighted mean${approx ? " (an approximation: not the area's own figure)" : ""}${f.source === "acs_5yr" || !f.source ? ` (${countyVintage})` : ""}.`,
      derived: true,
    });
  }), ...SUMS.flatMap(([n, p]) => {
    const fn = counties.fields.find((x) => x.key === n), fp = counties.fields.find((x) => x.key === p);
    if (!fn || !fp) return [];
    return [
      F(n, fn.label, fn.format, { unit: fn.unit, source: fn.source, group: groupOf(n), description: `${fn.description} Summed over member counties (${countyVintage}).`, derived: true }),
      F(p, fp.label, fp.format, { unit: fp.unit, source: fp.source, group: groupOf(p), lens: COUNTY_LENS[p], description: `${fp.description} Summed counts over member counties ÷ their summed base populations (${countyVintage}).`, derived: true }),
    ];
  })].filter(Boolean) : [];

  // --- roll-ups from counties -------------------------------------------------------------------------------------
  const roll = { states: new Map(), metros: new Map() };
  const cRows = [];
  if (counties) {
    const col = Object.fromEntries(counties.columns.map((k, j) => [k, j]));
    counties.rows.forEach((r, i) => cRows.push({ r, i }));
    const add = (map, key, r) => {
      if (key == null) return;
      let a = map.get(key);
      if (!a) map.set(key, (a = { pop: 0, land: 0, s: {}, wt: {}, n: {}, d: {}, dn: {}, dp: {}, zp: {} }));
      const pop = r[col.population];
      if (fin(pop)) a.pop += pop;
      if (fin(r[col.land_km2])) a.land += r[col.land_km2];
      for (const k of ROLLUP) {
        if (k === "density") continue;
        const v = r[col[k]];
        if (fin(v) && fin(pop) && pop > 0) {
          a.s[k] = (a.s[k] || 0) + v * pop;
          a.wt[k] = (a.wt[k] || 0) + pop;
        }
      }
      for (const [n, p] of SUMS) {
        if (col[n] == null) continue;
        const c = r[col[n]], s = col[p] != null ? r[col[p]] : null;
        if (!fin(c)) continue;
        a.n[n] = (a.n[n] || 0) + c;
        // A county with a zero count (share 0) has no implied base: it is estimated from its population at the
        // base-to-population ratio of the other counties (if every county is zero the share stays null, not 0).
        if (fin(s) && s > 0) {
          a.d[n] = (a.d[n] || 0) + c / s;
          a.dn[n] = (a.dn[n] || 0) + c;
          if (fin(pop)) a.dp[n] = (a.dp[n] || 0) + pop;
        } else if (c === 0 && fin(pop)) a.zp[n] = (a.zp[n] || 0) + pop;
      }
    };
    for (const { r } of cRows) {
      add(roll.states, r[col.state], r);
      add(roll.metros, r[col.cbsa], r);
    }
  }
  const rolled = (map, key) => {
    const a = map.get(key);
    const out = {};
    if (!a) return out;
    out.density = a.land > 0 ? a.pop / a.land : null;
    out.land_km2 = a.land || null;
    for (const k of ROLLUP) if (k !== "density") out[k] = a.wt[k] ? a.s[k] / a.wt[k] : null;
    for (const [n, p] of SUMS) {
      out[n] = a.n[n] ?? null;
      const zeroBase = a.zp[n] && a.dp[n] ? (a.zp[n] * a.d[n]) / a.dp[n] : 0;
      out[p] = a.d[n] > 0 ? a.dn[n] / (a.d[n] + zeroBase) : null;
    }
    return out;
  };

  const econValues = (o) => ({
    population: o.population ?? null,
    growth: o.growth ?? null,
    gdp: o.gdp ?? o.yields?.gold ?? null,
    gdp_pc: ratio(o.gdp ?? o.yields?.gold, o.population),
    median_income: o.median_income ?? null,
    median_rent: o.median_rent ?? null,
    home_value: o.home_value ?? null,
    affordability: ratio(o.home_value, o.median_income),
    rent_burden: o.rent_burden ?? null,
    unemployment: o.unemployment ?? null,
    employed: o.employed ?? null,
    ...Object.fromEntries((nation.yields || []).filter((y) => y.unit !== "usd").map((y) => [`y_${y.key}`, o.yields?.[y.key] ?? null])),
  });

  // --- resources by place ---------------------------------------------------------------------------------------
  const items = resources?.items || [];
  const typeById = new Map((resources?.types || []).map((t) => [t.id, t]));
  const mwByMetro = new Map();
  items.forEach((it) => {
    if (typeById.get(it.type)?.unit !== "MW" || !fin(it.value) || !it.cbsa) return;
    mwByMetro.set(it.cbsa, (mwByMetro.get(it.cbsa) || 0) + it.value);
  });

  // --- states ---------------------------------------------------------------------------------------------------
  const stateRows = (Array.isArray(states) ? states : []).map((s, idx) => ({
    key: s.abbr, name: s.name, sub: s.abbr, level: "states", ref: idx,
    v: {
      name: s.name, abbr: s.abbr, ...econValues(s), ...rolled(roll.states, s.abbr),
      ...Object.fromEntries(LAND_KEYS.map((k) => [`land_${k}`, s.land?.[k] ?? null])),
      energy_mw: s.energy ? s.energy.total : null,
    },
  }));
  const landFields = LAND_KEYS.map((k) => F(`land_${k}`, LAND_LABEL[k], "pct", { unit: "share of land", source: "nlcd", group: "Land", description: `Share of the land in ${LAND_LABEL[k].toLowerCase()} (NLCD, mean over the area's hexes).`, lens: k === "forest" ? "forest" : k === "developed" ? "developed" : null }));
  const energyField = F("energy_mw", "Mapped power", "mw", { unit: "MW of mapped sites", source: "eia_860m", group: "Energy", description: "Capacity of the power sites mapped as resources (each type above its size threshold): not the total generating capacity." });
  const states_ = makeLevel({
    id: "states", label: "States", noun: "state",
    rows: stateRows,
    fields: [F("name", "State", "text", { group: "Name" }), ...econFields("acs_1yr"), ...rollupFields(), F("land_km2", "Land area", "km2", { unit: "km²", source: "census_gazetteer", group: "Land", derived: true }), ...landFields, energyField],
    highlight: (rows) => ({ states: new Set(rows.map((r) => r.ref)) }),
    rowAtCell: (cell, byKey) => (owner[cell] >= 0 ? stateRows[owner[cell]] || null : null),
    focus: (row) => app.selectState(row.ref, { fly: true }),
    cellsOf: (row) => stateCells.get(row.ref) || [],
  });

  // --- metros ---------------------------------------------------------------------------------------------------
  const metroRows = cities.map((c) => ({
    key: c.id, name: c.name, sub: (c.states || []).join("–"), level: "metros", ref: c,
    v: {
      name: c.name, states: (c.states || []).join("–"), kind: c.metro === false ? "Micro" : "Metro",
      ...econValues(c), ...rolled(roll.metros, c.id), energy_mw: mwByMetro.get(c.id) ?? 0,
    },
  }));
  const metroByCity = new Map(metroRows.map((r) => [r.ref, r]));
  const metros = makeLevel({
    id: "metros", label: "Metros", noun: "metro",
    rows: metroRows,
    fields: [F("name", "Metro", "text", { group: "Name" }), F("states", "States", "text", { group: "Name" }), F("kind", "Type", "text", { group: "Name", description: "Metropolitan (50,000+ urban core) or micropolitan area (OMB)." }), ...econFields("acs_1yr"), ...rollupFields(), F("land_km2", "Land area", "km2", { unit: "km²", source: "census_gazetteer", group: "Land", derived: true }), energyField],
    highlight: (rows, o = {}) => {
      const cells = new Set();
      for (const r of rows) for (const c of r.ref.footprint || [r.ref.cell]) cells.add(c);
      return { cells, cities: rows.length <= (o.ringMax ?? 30) ? new Set(rows.map((r) => r.key)) : undefined };
    },
    rowAtCell: (cell) => { const c = cellMetro.get(cell); return c ? metroByCity.get(c) : null; },
    focus: (row) => app.selectCity(row.ref, { fly: true }),
    cellsOf: (row) => row.ref.footprint || [row.ref.cell],
  });

  // --- counties ---------------------------------------------------------------------------------------------------
  let counties_ = null;
  if (counties) {
    const cols = counties.columns;
    const cityName = new Map(cities.map((c) => [c.id, c.name]));
    // counties.json carries the faith_tradition category id; tables and filters read better with its name.
    const tradName = new Map((app.lensById?.get("faith_tradition")?.categories || []).map((c) => [c.id, c.name]));
    const countyRows = cRows.map(({ r, i }) => {
      const v = {};
      cols.forEach((k, j) => (v[k] = r[j]));
      v.metro = v.cbsa ? cityName.get(v.cbsa) || null : null;
      if (v.faith_tradition != null) v.faith_tradition = tradName.get(v.faith_tradition) || v.faith_tradition;
      return { key: v.geoid, name: shortCounty(v.name), full: v.name, sub: v.state, level: "counties", ref: i, v };
    });
    // counties.json rows follow world.json counties; map by geoid anyway in case they ever diverge.
    const worldIdx = new Map((w.raw.counties || []).map((c, k) => [c.geoid, k]));
    for (const row of countyRows) row.ref = worldIdx.get(row.key) ?? row.ref;
    const rowByWorld = new Map(countyRows.map((r) => [r.ref, r]));
    // Counties smaller than a hex own no cell: counties.json `cell` (the hex nearest the county's centre) stands in.
    const cellsOfRow = (r) => countyCells.get(r.ref) || (Number.isInteger(r.v.cell) ? [r.v.cell] : []);
    const fields = counties.fields.filter((f) => f.key !== "cbsa" && f.key !== "geoid" && f.key !== "cell").map((f) => ({
      ...F(f.key, f.key === "name" ? "County" : f.label, f.format, { unit: f.unit, higher_is: f.higher_is, source: f.source, description: f.description, lens: COUNTY_LENS[f.key], group: groupOf(f.key) }),
    }));
    fields.splice(2, 0, F("metro", "Metro", "text", { group: "Name", description: "The metro or micro area the county belongs to (OMB), if it is on the map." }));
    counties_ = makeLevel({
      id: "counties", label: "Counties", noun: "county",
      rows: countyRows, fields,
      highlight: (rows) => {
        const cells = new Set();
        for (const r of rows) for (const c of cellsOfRow(r)) cells.add(c);
        return { cells };
      },
      rowAtCell: (cell) => (countyOf[cell] >= 0 ? rowByWorld.get(countyOf[cell]) || null : null),
      focus: (row) => {
        let cells = cellsOfRow(row);
        // A county smaller than a hex owns no cell: frame its metro instead, or open its state.
        if (!cells.length && row.v.cbsa) cells = cities.find((c) => c.id === row.v.cbsa)?.footprint || [];
        if (!cells.length) return app.selectState(row.v.state, { fly: true });
        let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
        for (const c of cells) {
          const x = grid.cx(c), y = grid.cy(c);
          x0 = Math.min(x0, x); x1 = Math.max(x1, x); y0 = Math.min(y0, y); y1 = Math.max(y1, y);
        }
        const R = grid.R * 6;
        frameBox(app, { x0: x0 - R, y0: y0 - R, x1: x1 + R, y1: y1 + R }, { pad: 24, maxScale: 2.4 });
      },
      cellsOf: (row) => cellsOfRow(row),
    });
  }

  const countiesOffMap = counties_ ? counties_.rows.filter((r) => !counties_.cellsOf(r).length).length : 0; // no hex, not even a stand-in
  return {
    app, nation, nat, vintage, countyVintage, countiesOffMap,
    levels: { states: states_, metros, counties: counties_ },
    stateCells, countyCells, cellMetro, metroByCity,
    items, typeById,
    source: (id) => srcTitle.get(id) || null,
  };
}

/** "Los Angeles County" → "Los Angeles Co.", "Orleans Parish" → "Orleans Par." (tables and chart labels). */
export function shortCounty(name) {
  return String(name || "").replace(/ County$/, " Co.").replace(/ Parish$/, " Par.");
}

function groupOf(k) {
  if (["population", "density", "median_age", "foreign_born", "growth"].includes(k)) return "People";
  if (["income", "home_value", "poverty", "homeownership", "uninsured"].includes(k)) return "Prosperity";
  if (["bachelors", "broadband"].includes(k)) return "Knowledge";
  if (["commute"].includes(k)) return "Daily life";
  if (["forest", "crop", "pasture", "developed", "land_km2"].includes(k)) return "Land";
  if (["name", "state", "metro"].includes(k)) return "Name";
  if (["faith", "faith_tradition"].includes(k)) return "Faith";
  if (["amenities", "mhlth", "loneliness"].includes(k)) return "Wellbeing";
  if (["disasters"].includes(k)) return "Hazards";
  if (["veterans", "veterans_pct", "armed_forces", "armed_forces_pct"].includes(k)) return "Military";
  return "Other";
}

function makeLevel(def) {
  const fieldByKey = new Map(def.fields.map((f) => [f.key, f]));
  const byKey = new Map(def.rows.map((r) => [r.key, r]));
  return {
    ...def,
    byKey,
    field: (k) => fieldByKey.get(k) || null,
    value: (row, k) => row.v[k] ?? null,
    numericFields: () => def.fields.filter((f) => f.format !== "text"),
    /** Add fields after the fact (optional files that load on demand); a key already present is skipped. */
    addFields(list) {
      for (const f of list) {
        if (fieldByKey.has(f.key)) continue;
        def.fields.push(f);
        fieldByKey.set(f.key, f);
      }
    },
  };
}

// --- fields from files that load on demand ------------------------------------------------------------------------
// extendData(data, names) loads the named files (military, districts, citizens) through app.loadData and adds their
// columns to the levels once per data model. Missing files (null) add nothing. Returns the loaded files by name.
const F4 = (key, label, format, o = {}) => ({ key, label, format, unit: o.unit || null, higher_is: o.higher_is || "neutral", source: o.source || "", description: o.description || "", lens: o.lens || null, group: o.group || "Other", derived: !!o.derived });

export async function extendData(data, names) {
  const app = data.app;
  const files = Object.fromEntries(await Promise.all(names.map(async (n) => [n, await app.loadData(n).catch(() => null)])));
  data.extended ??= new Set();
  for (const n of names) {
    if (data.extended.has(n) || !files[n]) continue;
    data.extended.add(n);
    try {
      if (n === "military") extendMilitary(data, files[n]);
      if (n === "districts") extendDistricts(data, files[n]);
      if (n === "citizens") extendCitizens(data, files[n]);
    } catch (err) {
      console.error(`advisors: could not add the ${n} columns`, err);
    }
  }
  return files;
}

function extendMilitary(data, mil) {
  const { states, metros } = data.levels;
  const v = mil.vintage || {};
  const instBy = (k) => {
    const m = new Map();
    for (const it of mil.installations || []) if (it[k]) m.set(it[k], (m.get(it[k]) || 0) + 1);
    return m;
  };
  const byState = instBy("state"), byMetro = instBy("cbsa");
  const src = { personnel: "dmdc_personnel", spending: "oldcc_spending" };
  states.addFields([
    F4("mil_active", "Active-duty troops", "int", { unit: "people, by duty state", source: src.personnel, group: "Military", description: `Active-duty personnel of all services incl. Coast Guard, by duty state (${v.personnel || "DMDC"}). Excludes most deployed and temporary-duty personnel.` }),
    F4("mil_guard_reserve", "Guard & Reserve", "int", { unit: "people", source: src.personnel, group: "Military", description: `National Guard and Reserve members by state (${v.personnel || "DMDC"}).` }),
    F4("mil_civilian", "DoD civilians", "int", { unit: "people", source: src.personnel, group: "Military", description: `Civilian Defense Department employees by duty state (${v.personnel || "DMDC"}).` }),
    F4("def_spending", "Defense spending", "money", { unit: "$ per year", source: src.spending, group: "Military", description: `Defense contracts, grants and payroll in the state (${v.spending || "OLDCC"}). Contracts follow the place of performance, not where bases are.` }),
    F4("def_per_resident", "Defense $ per resident", "usd", { unit: "$ per resident", source: src.spending, group: "Military", description: `Defense spending ÷ residents, as published by OLDCC (${v.spending || "OLDCC"}).` }),
    F4("def_pct_gdp", "Defense share of GDP", "pct", { unit: "of state GDP", source: src.spending, group: "Military", description: `Defense spending ÷ state GDP, as published by OLDCC (${v.spending || "OLDCC"}).` }),
    F4("installations", "Installations", "int", { unit: "MIRTA sites", source: "mirta", group: "Military", description: `Active, semi-active and caretaker DoD installations in the state (${v.installations || "MIRTA"}), by the state MIRTA files them under. Sizes are not comparable: a site can be an office or a training range.` }),
  ]);
  for (const r of states.rows) {
    const s = mil.states?.[r.key];
    const p = s?.personnel, sp = s?.spending;
    Object.assign(r.v, {
      mil_active: p?.active?.total ?? null, mil_guard_reserve: p?.guard_reserve?.total ?? null, mil_civilian: p?.civilian?.total ?? null,
      def_spending: sp?.total ?? null, def_per_resident: sp?.per_resident ?? null, def_pct_gdp: sp?.pct_gdp ?? null,
      installations: s ? byState.get(r.key) || 0 : null,
    });
  }
  metros.addFields([
    F4("encampment", "Garrison", "text", { source: "mirta", group: "Military", description: `Fortress or garrison when the metro passes the Encampment rule; blank otherwise. ${mil.rules?.encampment || mil.rule || ""}` }),
    F4("installations", "Installations", "int", { unit: "MIRTA sites", source: "mirta", group: "Military", description: `DoD installations (MIRTA) whose point lies in the metro's member counties (${v.installations || "MIRTA"}). Sizes are not comparable.` }),
  ]);
  for (const r of metros.rows) {
    const c = mil.cities?.[r.key];
    r.v.encampment = c ? (c.tier === "fortress" ? "Fortress" : "Garrison") + (c.naval ? " (naval)" : "") : null;
    r.v.installations = byMetro.get(r.key) || 0;
  }
}

function extendDistricts(data, d) {
  const { metros, counties } = data.levels;
  const cat = new Map((d.catalog || []).map((c) => [c.id, c]));
  const vint = d.vintage || "BLS QCEW";
  const lb = (x) => (x?.lower_bound ? " (at least)" : "");
  metros.addFields([
    F4("city_type", "City type", "text", { source: "bls_qcew", group: "Districts", description: `The city's badge: named after its strongest district, by its speciality when one is at least 3× the national share of jobs (${vint}). Blank: no district passes (a diversified economy).` }),
    F4("districts", "Districts", "text", { source: "bls_qcew", group: "Districts", description: `Industry districts the metro qualifies for, strongest first. ${d.rule?.text || ""}` }),
    F4("n_districts", "Number of districts", "int", { source: "bls_qcew", group: "Districts", description: "How many districts pass the rule (up to 5)." }),
    F4("qcew_jobs", "Jobs (QCEW)", "int", { unit: "covered jobs, where the employer is", source: "bls_qcew", group: "Districts", description: `Annual average covered jobs, all ownerships, summed over member counties (${vint}). Counted where the employer is, unlike the ACS figures (where people live).` }),
    F4("qcew_pay", "Average pay (QCEW)", "usd", { unit: "$ per job per year", source: "bls_qcew", group: "Districts", description: `Average annual pay per covered job (${vint}).` }),
    F4("top_industry", "Most specialised industry", "text", { source: "bls_qcew", group: "Districts", description: "The detailed industry (of those this project fetches) with the highest location quotient and at least 1,000 jobs." }),
    F4("top_lq", "Its location quotient", "lq", { unit: "× the national share of jobs", source: "bls_qcew", group: "Districts", description: "Location quotient of the most specialised industry: its share of the metro's jobs ÷ its share of US jobs. A lower bound where BLS withheld some counties." }),
  ]);
  for (const r of metros.rows) {
    const c = d.cities?.[r.key];
    const top = c?.top?.[0];
    Object.assign(r.v, {
      city_type: c?.badge?.label ?? null,
      districts: c ? (c.districts || []).map((x) => cat.get(x.id)?.name || x.id).join(", ") || null : null,
      n_districts: c ? (c.districts || []).length : null,
      qcew_jobs: c?.emp ?? null, qcew_pay: c?.avg_pay ?? null,
      top_industry: top ? `${d.industries?.[top.naics]?.title || top.naics}${lb(top)}` : null,
      top_lq: top?.lq ?? null,
    });
  }
  if (counties && d.counties) {
    counties.addFields([
      F4("districts", "Districts", "text", { source: "bls_qcew", group: "Districts", description: `Industry districts the county qualifies for, strongest first (${vint}). ${d.rule?.county_note || ""}` }),
      F4("qcew_jobs", "Jobs (QCEW)", "int", { unit: "covered jobs, where the employer is", source: "bls_qcew", group: "Districts", description: `Annual average covered jobs, all ownerships (${vint}).` }),
    ]);
    for (const r of counties.rows) {
      const c = d.counties[r.key];
      r.v.districts = c ? (c.districts || []).map((x) => cat.get(x[0])?.name || x[0]).join(", ") || null : null;
      r.v.qcew_jobs = c?.emp ?? null;
    }
  }
}

// Citizens: a few telling shares of each ACS row (the City View draws the whole mix as person icons).
const CZ = [
  ["workforce", "executive", "Executives & managers"], ["workforce", "engineer", "Engineers & scientists"], ["workforce", "healer", "Health workers"],
  ["workforce", "service", "Service workers"], ["workforce", "farmer", "Farmers & foresters"], ["workforce", "builder", "Builders & repairers"],
  ["workforce", "maker", "Factory & transport workers"],
  ["age", "child", "Children"], ["age", "young", "Young adults"], ["age", "elder", "Elders 65+"],
  ["commute", "drove_alone", "Drive alone"], ["commute", "transit", "Public transit"], ["commute", "walk", "Walk to work"], ["commute", "home", "Work from home"],
  ["education", "graduate", "Graduate degrees"],
  ["nativity", "born_in_state", "Born in the state"], ["nativity", "foreign_born", "Born abroad (1-yr)"],
];
function extendCitizens(data, cz) {
  const { metros } = data.levels;
  const vint = cz.vintage || "ACS 1-year";
  const label = new Map(Object.entries(cz.groups || {}).flatMap(([row, list]) => list.map((g) => [`${row}.${g.key}`, g.label])));
  const rowDesc = cz.rows || {};
  const grp = { workforce: "Citizens: work", age: "Citizens: age", commute: "Citizens: commute", education: "Citizens: education", nativity: "Citizens: origin" };
  metros.addFields([
    ...CZ.map(([row, key, lbl]) => F4(`cz_${key}`, lbl, "pct", {
      unit: `share of ${(rowDesc[row] || row).toLowerCase()}`, source: "acs_1yr", group: grp[row],
      description: `${label.get(`${row}.${key}`) || lbl}: share of ${(rowDesc[row] || row).toLowerCase()} (${vint}). Blank where Census left the area out of that table.${row === "commute" ? " Shares of workers, not of all residents." : ""}`,
    })),
    F4("cz_median_age", "Median age (1-yr)", "years", { unit: "years", source: "acs_1yr", group: grp.age, description: `Median age of residents, ACS B01002 (${vint}): the area's own median, unlike the county roll-up.` }),
    F4("cz_degree", "Bachelor's or more (1-yr)", "pct", { unit: "of adults 25+", source: "acs_1yr", group: grp.education, description: `Adults 25+ with a bachelor's degree or higher (${vint}, C15003).` }),
    F4("cz_specialist", "Most over-represented citizen", "text", { source: "acs_1yr", group: "Citizens", description: `The category most over-represented against the national share (at least 1.5×). ${cz.rule?.text || ""}` }),
  ]);
  for (const r of metros.rows) {
    const c = cz.cities?.[r.key];
    for (const [row, key] of CZ) r.v[`cz_${key}`] = c?.[row]?.shares?.[key] ?? null;
    r.v.cz_median_age = c?.age?.median ?? null;
    r.v.cz_degree = c?.education?.bachelors_plus ?? null;
    const sp = (c?.specialists || []).find((s) => s.ratio >= 1);
    r.v.cz_specialist = sp ? `${label.get(`${sp.row}.${sp.key}`) || sp.key} ×${sp.ratio.toFixed(1)}${c.reliable === false ? " (micro: rough)" : ""}` : null;
  }
}

/** The highlight for a set of rows of one level, with defaults (gold outline, other land dimmed when asked). */
export function rowsHighlight(level, rows, opts = {}) {
  if (!level || !rows || !rows.length) return null;
  const one = rows.length === 1 ? rows[0] : null;
  const label = opts.label ?? (one ? (one.level === "states" ? one.name : `${one.full || one.name}${one.sub ? `, ${one.sub}` : ""}`) : "");
  return { ...level.highlight(rows, opts), dimOthers: !!opts.dim, color: opts.color, label };
}
