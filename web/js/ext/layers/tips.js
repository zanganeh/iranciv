// Hover tooltips for the map-art layers (HTML strings; every data value is escaped). Each says where the number comes
// from and the caveat that matters for it (FAF values are model estimates; bases have no published headcount; the
// trade arc is a drawing aid; curated landmarks are an editorial pick).
import { esc, compact, int, pct } from "../../format.js";
import { FORT_KINDS, fortKind, WONDER_KINDS } from "../../render/art/sites.js";
import { MODE_STYLE } from "../../render/art/improvements.js";

const muted = (s) => `<span class="muted">${s}</span>`;
const num = (n, d = 1) => (n == null || !Number.isFinite(n) ? "–" : compact(n));
const sign = (x) => (x > 0 ? "+" : x < 0 ? "−" : "±");

function usd(n) {
  if (n == null || !Number.isFinite(n)) return "–";
  const a = Math.abs(n);
  if (a >= 1e12) return `$${(n / 1e12).toFixed(2)} trillion`;
  if (a >= 1e9) return `$${(n / 1e9).toFixed(a >= 1e10 ? 1 : 2)} billion`;
  if (a >= 1e6) return `$${(n / 1e6).toFixed(1)} million`;
  return `$${int(n)}`;
}

const HUB_NAME = { L: "Large airport", M: "Medium airport" };
const PORT_TYPE = { coastal: "Coastal port", great_lakes: "Great Lakes port", inland: "Inland river port" };

export const tips = {
  port(p, m) {
    const yr = p.year ?? "";
    const lines = [
      `<b>${esc(p.name)}</b> ${muted(`${esc(PORT_TYPE[p.type] || "Port")}, ${esc(p.state || "")}`)}`,
    ];
    if (p.tons != null) lines.push(`#${esc(p.rank)} by tonnage · <b>${num(p.tons)}</b> short tons (${esc(yr)})`);
    if (p.foreign != null || p.domestic != null) lines.push(`Foreign ${num(p.foreign)} · domestic ${num(p.domestic)}`);
    if (p.imports != null || p.exports != null) lines.push(`Imports ${num(p.imports)} · exports ${num(p.exports)}`);
    lines.push(muted("Natural Earth 1:10m ports."));
    return lines.join("<br>");
  },
  airport(a) {
    const lines = [`<b>${esc(a.id)} · ${esc(a.name)}</b> ${muted(esc([a.city, a.state].filter(Boolean).join(", ")))}`];
    lines.push(a.hub ? esc(HUB_NAME[a.hub] || a.hub) : "All-cargo airport (not a primary passenger airport)");
    if (a.enplanements != null) {
      let ch = "";
      if (a.enplanements_prev > 0) {
        const r = a.enplanements / a.enplanements_prev - 1;
        ch = ` (${sign(r)}${Math.abs(r * 100).toFixed(1)}% on ${a.year - 1})`;
      }
      lines.push(`<b>${num(a.enplanements)}</b> boardings in ${esc(a.year)}${ch}`);
    }
    if (a.cargo_lbs != null) lines.push(`All-cargo landed weight ${num(a.cargo_lbs)} lb`);
    lines.push(muted("OurAirports: large and medium airports with scheduled service."));
    return lines.join("<br>");
  },
  fort(f, m) {
    const comp = m.data.military?.components?.find((c) => c.id === f.component);
    const name = comp?.name || FORT_KINDS[fortKind(f.component)]?.name || "Defense site";
    const tier = { major: "Major installation", base: "Base", site: "Site" }[f.tier] || "";
    const status = { act: "", semi: " · semi-active", care: " · caretaker status" }[f.status] ?? "";
    const lines = [
      `<b>${esc(f.name)}</b> ${muted(esc(f.state || ""))}`,
      `${esc(name)}${f.joint ? " · joint base" : ""} · ${esc(tier)}${esc(status)}`,
    ];
    if (f.area_km2 != null) lines.push(`Boundary ${int(f.area_km2)} km²`);
    lines.push(muted("DoD MIRTA. Sized by boundary area: no per-base headcount is published."));
    return lines.join("<br>");
  },
  range(r, m) {
    const comp = m.data.military?.components?.find((c) => c.id === r.component);
    return [
      `<b>${esc(r.name)}</b> ${muted(esc(r.state || ""))}`,
      `Restricted ground · ${esc(comp?.name || "")} · ${int(r.area_km2)} km²`,
      muted("One of the 30 largest DoD boundaries (MIRTA), drawn on the hexes it covers."),
    ].join("<br>");
  },
  wonder(x, m) {
    const k = WONDER_KINDS[x.kind]?.name || "Wonder";
    const rule = m.data.wonders?.rules?.find((r) => r.id === x.rule);
    const mt = x.metrics || {};
    const lines = [`<b>${esc(x.name)}</b> ${muted(esc([x.state].filter(Boolean).join("")))}`];
    if (x.kind === "university") {
      lines.push(`${esc(k)} · #${esc(x.rank)} by Wikipedia languages`);
      if (mt.wikipedia_languages != null) lines.push(`Wikipedia articles in ${int(mt.wikipedia_languages)} languages`);
      if (mt.rd_usd != null) lines.push(`R&amp;D ${usd(mt.rd_usd)} (FY${esc(mt.year)})${mt.federal_usd != null ? `, ${pct(mt.federal_usd / mt.rd_usd, 0)} federal` : ""}`);
      if (mt.enrollment != null) lines.push(`${int(mt.enrollment)} students (Wikidata)`);
    } else if (x.rule === "landmark") {
      lines.push(`UNESCO World Heritage${mt.inscribed ? `, inscribed ${esc(mt.inscribed)}` : ""}`);
      if (mt.parts?.length) lines.push(muted(`Includes ${esc(mt.parts.slice(0, 4).join(", "))}${mt.parts.length > 4 ? "…" : ""}`));
    } else if (x.curated) {
      lines.push(`${esc(k)} · hand-picked (curated, not ranked by data)`);
      if (mt.visits != null) lines.push(`${num(mt.visits)} recreation visits (${esc(mt.year)})`);
      if (mt.designated) lines.push(`National Historic Landmark since ${esc(mt.designated)}`);
    } else {
      lines.push(`${esc(k)} · #${esc(x.rank)} by area among national parks`);
      if (mt.visits != null) lines.push(`<b>${num(mt.visits)}</b> recreation visits (${esc(mt.year)})${mt.visits_of ? ` ${muted(`(${esc(mt.visits_of)})`)}` : ""}`);
      if (mt.area_km2 != null) lines.push(`${int(mt.area_km2)} km²`);
    }
    if (rule?.text) lines.push(muted(esc(rule.text)));
    return lines.join("<br>");
  },
  route(rt, m) {
    const r = rt.route;
    const t = m.data.trade || {};
    const a = rt.a?.short || rt.a?.name || r.a, b = rt.b?.short || rt.b?.name || r.b;
    const modes = Object.entries(r.modes || {}).filter(([, v]) => v >= 0.05).sort((p, q) => q[1] - p[1])
      .map(([k, v]) => `${esc(MODE_STYLE[k]?.label || k)} ${Math.round(v * 100)}%`).join(" · ");
    const com = t.commodities?.[r.top_commodity];
    const lines = [
      `<b>${esc(a)} ⇄ ${esc(b)}</b> ${muted(`trade route #${esc(r.rank)}`)}`,
      `<b>${usd(r.value_musd * 1e6)}</b> of freight a year, ${num(r.tons_k * 1e3)} short tons (FAF ${esc(t.year ?? "")} estimate)`,
    ];
    if (modes) lines.push(modes);
    if (com) lines.push(`Top commodity: ${esc(com)}`);
    lines.push(muted("Both directions summed. Drawn as an arc between each zone's largest city: FAF records where freight starts and ends, not its route."));
    return lines.join("<br>");
  },
};
