// Original ancient-era art for the game, drawn with plain canvas shapes (world units: km, hex radius R).
// Cities are clusters of flat-roofed mud-brick houses that spread as the population grows, with a walled citadel
// for a capital, city walls once Masonry is known, and the buildings the city has raised (fire temple, garden,
// observatory, market awnings). Units are drawn as figures in a faction-coloured medallion.

function rng(seed) {
  let s = seed >>> 0 || 1;
  return () => {
    s = (s + 0x6d2b79f5) | 0;
    let t = Math.imul(s ^ (s >>> 15), s | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const ADOBE = ["#d9b98a", "#cfae7c", "#e2c597", "#c8a370", "#d6b07a"];

function house(ctx, x, y, w, h, tone, px) {
  // shadow side, then the flat roof with a darker parapet line
  ctx.fillStyle = "rgba(60,40,20,0.45)";
  ctx.fillRect(x - w / 2 + px * 0.8, y - h / 2 + px * 0.8, w, h);
  ctx.fillStyle = tone;
  ctx.fillRect(x - w / 2, y - h / 2, w, h);
  ctx.strokeStyle = "rgba(90,62,34,0.75)";
  ctx.lineWidth = Math.max(px * 0.6, w * 0.08);
  ctx.strokeRect(x - w / 2, y - h / 2, w, h);
}

/** A city's footprint radius (km) for population `pop`. */
export const cityRadius = (R, pop) => R * Math.min(0.92, 0.32 + 0.06 * Math.min(pop, 10));

/** The hex radius to draw a city at so it reads on screen at camera scale `s` (px per km): never smaller than a
 *  footprint of 20 px + 2.5 px per citizen, so cities look like towns, not dots, when the map is zoomed out a bit. */
export function cityDrawR(R, pop, s) {
  const want = 20 + 2.5 * Math.min(pop, 14);
  return R * Math.max(1, want / (cityRadius(R, pop) * s));
}

/**
 * Draw an ancient city at (x, y). opts: {pop, color, capital, walls, blds, seed, px (km per screen px), name}.
 */
export function drawAncientCity(ctx, x, y, R, { pop = 1, color = "#c33", capital = false, walls = false, blds = {}, seed = 1, px = 0.1 } = {}) {
  const rand = rng(seed * 2654435761);
  const rad = cityRadius(R, pop);
  // trodden ground
  ctx.beginPath();
  ctx.arc(x, y, rad * 1.08, 0, Math.PI * 2);
  ctx.fillStyle = "rgba(196,170,120,0.55)";
  ctx.fill();
  // gardens and orchards around the edge
  const trees = 4 + Math.min(14, pop * 2);
  for (let k = 0; k < trees; k++) {
    const a = rand() * Math.PI * 2, d = rad * (0.95 + rand() * 0.35);
    ctx.beginPath();
    ctx.arc(x + Math.cos(a) * d, y + Math.sin(a) * d, R * (0.045 + rand() * 0.03), 0, Math.PI * 2);
    ctx.fillStyle = rand() < 0.5 ? "#5f8a3c" : "#76a04a";
    ctx.fill();
  }
  // houses: a jittered grid inside the footprint, densest near the centre
  const n = Math.min(70, 6 + pop * 6);
  const step = R * 0.13;
  const cells = [];
  for (let gy = -rad; gy <= rad; gy += step) for (let gx = -rad; gx <= rad; gx += step) {
    const d = Math.hypot(gx, gy);
    if (d < rad * 0.95) cells.push([gx, gy, d + rand() * rad * 0.5]);
  }
  cells.sort((a, b) => a[2] - b[2]);
  const core = capital ? R * 0.2 : R * 0.1;
  let placed = 0;
  for (const [gx, gy] of cells) {
    if (placed >= n) break;
    if (Math.hypot(gx, gy) < core) continue;
    const w = step * (0.6 + rand() * 0.35), h = step * (0.55 + rand() * 0.35);
    house(ctx, x + gx + (rand() - 0.5) * step * 0.25, y + gy + (rand() - 0.5) * step * 0.25, w, h, ADOBE[(rand() * ADOBE.length) | 0], px);
    placed++;
  }
  // buildings the city has raised
  const spots = [[-0.42, -0.25], [0.42, -0.2], [-0.3, 0.38], [0.36, 0.34], [0, -0.5], [0, 0.5]];
  let si = 0;
  const spot = () => { const [a, b] = spots[si++ % spots.length]; return [x + a * rad, y + b * rad]; };
  if (blds.temple) fireTemple(ctx, ...spot(), R * 0.13, px);
  if (blds.garden) garden(ctx, ...spot(), R * 0.17, px);
  if (blds.market || blds.bazaar) awnings(ctx, ...spot(), R * 0.14, px);
  if (blds.observatory) tower(ctx, ...spot(), R * 0.12, px);
  if (blds.library || blds.wisdom || blds.academy) hall(ctx, ...spot(), R * 0.13, px);
  // walls
  if (walls) {
    ctx.beginPath();
    ctx.arc(x, y, rad * 1.02, 0, Math.PI * 2);
    ctx.strokeStyle = "rgba(80,56,30,0.9)";
    ctx.lineWidth = Math.max(px * 2.6, R * 0.05);
    ctx.stroke();
    ctx.strokeStyle = "#d8bb88";
    ctx.lineWidth = Math.max(px * 1.4, R * 0.028);
    ctx.stroke();
    for (let k = 0; k < 8; k++) {
      const a = (k / 8) * Math.PI * 2 + 0.2;
      const tx = x + Math.cos(a) * rad * 1.02, ty = y + Math.sin(a) * rad * 1.02, s = R * 0.055;
      ctx.fillStyle = "#c9a56f";
      ctx.fillRect(tx - s, ty - s, s * 2, s * 2);
      ctx.strokeStyle = "rgba(80,56,30,0.9)";
      ctx.lineWidth = px;
      ctx.strokeRect(tx - s, ty - s, s * 2, s * 2);
    }
  }
  // the citadel (capital) or a central square with the people's banner
  if (capital) citadel(ctx, x, y, R * 0.2, color, px);
  else {
    ctx.fillStyle = "#e9d6ae";
    ctx.fillRect(x - R * 0.07, y - R * 0.07, R * 0.14, R * 0.14);
    flag(ctx, x, y - R * 0.02, R * 0.16, color, px);
  }
}

function citadel(ctx, x, y, s, color, px) {
  // a stepped mud-brick platform with a hall on top
  const tiers = [[1, 0.5, "#bf9a62"], [0.74, 0.38, "#cfab72"], [0.48, 0.28, "#ddbc84"]];
  let yy = y + s * 0.5;
  for (const [w, h, c] of tiers) {
    ctx.fillStyle = "rgba(60,40,20,0.5)";
    ctx.fillRect(x - s * w + px, yy - s * h + px, s * w * 2, s * h);
    ctx.fillStyle = c;
    ctx.fillRect(x - s * w, yy - s * h, s * w * 2, s * h);
    ctx.strokeStyle = "rgba(90,62,34,0.85)";
    ctx.lineWidth = px;
    ctx.strokeRect(x - s * w, yy - s * h, s * w * 2, s * h);
    yy -= s * h;
  }
  flag(ctx, x, yy, s * 0.9, color, px);
}

function flag(ctx, x, y, h, color, px) {
  ctx.strokeStyle = "#4a3520";
  ctx.lineWidth = Math.max(px, h * 0.06);
  ctx.beginPath();
  ctx.moveTo(x, y);
  ctx.lineTo(x, y - h);
  ctx.stroke();
  ctx.fillStyle = color;
  ctx.beginPath();
  ctx.moveTo(x, y - h);
  ctx.lineTo(x + h * 0.7, y - h * 0.82);
  ctx.lineTo(x, y - h * 0.62);
  ctx.closePath();
  ctx.fill();
}

function fireTemple(ctx, x, y, s, px) {
  ctx.fillStyle = "#e8d8b8";
  ctx.fillRect(x - s, y - s * 0.6, s * 2, s * 1.2);
  ctx.strokeStyle = "rgba(90,62,34,0.9)";
  ctx.lineWidth = px;
  ctx.strokeRect(x - s, y - s * 0.6, s * 2, s * 1.2);
  ctx.beginPath();
  ctx.arc(x, y - s * 0.6, s * 0.7, Math.PI, 0);
  ctx.fillStyle = "#d9c6a0";
  ctx.fill();
  ctx.stroke();
  ctx.beginPath();
  ctx.moveTo(x, y - s * 1.75);
  ctx.quadraticCurveTo(x + s * 0.35, y - s * 1.3, x, y - s * 1.1);
  ctx.quadraticCurveTo(x - s * 0.35, y - s * 1.3, x, y - s * 1.75);
  ctx.fillStyle = "#f08a24";
  ctx.fill();
}

function garden(ctx, x, y, s, px) {
  ctx.fillStyle = "#4f8a3a";
  ctx.fillRect(x - s, y - s, s * 2, s * 2);
  ctx.strokeStyle = "#d8bb88";
  ctx.lineWidth = Math.max(px, s * 0.12);
  ctx.strokeRect(x - s, y - s, s * 2, s * 2);
  ctx.strokeStyle = "#6fb6e0";
  ctx.lineWidth = Math.max(px, s * 0.14);
  ctx.beginPath();
  ctx.moveTo(x - s, y); ctx.lineTo(x + s, y);
  ctx.moveTo(x, y - s); ctx.lineTo(x, y + s);
  ctx.stroke();
}

function awnings(ctx, x, y, s, px) {
  const cols = ["#c0392b", "#e0b040", "#2e7d9a"];
  for (let k = 0; k < 3; k++) {
    ctx.fillStyle = cols[k];
    ctx.fillRect(x - s + k * s * 0.7, y - s * 0.3, s * 0.6, s * 0.6);
    ctx.strokeStyle = "rgba(60,40,20,0.7)";
    ctx.lineWidth = px * 0.7;
    ctx.strokeRect(x - s + k * s * 0.7, y - s * 0.3, s * 0.6, s * 0.6);
  }
}

function tower(ctx, x, y, s, px) {
  ctx.fillStyle = "#d4b685";
  ctx.beginPath();
  ctx.moveTo(x - s * 0.6, y + s);
  ctx.lineTo(x - s * 0.35, y - s * 1.2);
  ctx.lineTo(x + s * 0.35, y - s * 1.2);
  ctx.lineTo(x + s * 0.6, y + s);
  ctx.closePath();
  ctx.fill();
  ctx.strokeStyle = "rgba(90,62,34,0.9)";
  ctx.lineWidth = px;
  ctx.stroke();
  ctx.beginPath();
  ctx.arc(x, y - s * 1.2, s * 0.4, Math.PI, 0);
  ctx.fillStyle = "#4fa3e0";
  ctx.fill();
}

function hall(ctx, x, y, s, px) {
  ctx.fillStyle = "#efe2c4";
  ctx.fillRect(x - s, y - s * 0.5, s * 2, s);
  ctx.strokeStyle = "rgba(90,62,34,0.9)";
  ctx.lineWidth = px;
  ctx.strokeRect(x - s, y - s * 0.5, s * 2, s);
  ctx.fillStyle = "rgba(90,62,34,0.7)";
  for (let k = -2; k <= 2; k++) ctx.fillRect(x + k * s * 0.38 - px * 0.6, y - s * 0.5, px * 1.2, s);
}

// --- units -------------------------------------------------------------------------------------------------------

/** Draw a unit figure in a medallion at screen point (x, y), radius r (css px). */
export function drawUnitToken(ctx, type, x, y, r, color, { dim = false, selected = false } = {}) {
  ctx.save();
  if (selected) {
    ctx.beginPath();
    ctx.arc(x, y, r + 5, 0, Math.PI * 2);
    ctx.fillStyle = "rgba(255,236,160,0.55)";
    ctx.fill();
  }
  ctx.beginPath();
  ctx.arc(x + 1, y + 2, r, 0, Math.PI * 2);
  ctx.fillStyle = "rgba(0,0,0,0.45)";
  ctx.fill();
  ctx.beginPath();
  ctx.arc(x, y, r, 0, Math.PI * 2);
  const gr = ctx.createRadialGradient(x - r * 0.3, y - r * 0.4, r * 0.1, x, y, r);
  gr.addColorStop(0, "#fbf1da");
  gr.addColorStop(1, "#d8c59a");
  ctx.fillStyle = gr;
  ctx.fill();
  ctx.lineWidth = Math.max(2, r * 0.22);
  ctx.strokeStyle = color;
  ctx.stroke();
  ctx.translate(x, y);
  ctx.scale(r / 12, r / 12);
  if (dim) ctx.globalAlpha = 0.55;
  FIG[type]?.(ctx, color);
  ctx.restore();
}

// Figures on a 24-unit box centred at 0 (so −12…12). Dark ink, faction accents.
const INK = "#3a2a18";
function person(ctx, cloth, { x = 0, y = 0, s = 1 } = {}) {
  ctx.save();
  ctx.translate(x, y);
  ctx.scale(s, s);
  ctx.fillStyle = "#c98b5a";
  ctx.beginPath();
  ctx.arc(0, -5.2, 2.3, 0, Math.PI * 2);
  ctx.fill();
  ctx.fillStyle = cloth;
  ctx.beginPath();
  ctx.moveTo(-3.4, 6.5);
  ctx.lineTo(-2.6, -2.2);
  ctx.quadraticCurveTo(0, -3.4, 2.6, -2.2);
  ctx.lineTo(3.4, 6.5);
  ctx.closePath();
  ctx.fill();
  ctx.strokeStyle = INK;
  ctx.lineWidth = 0.7;
  ctx.stroke();
  ctx.restore();
}
const FIG = {
  pioneer(ctx, c) {
    // a covered cart with a person
    ctx.fillStyle = "#efe6cf";
    ctx.strokeStyle = INK;
    ctx.lineWidth = 0.8;
    ctx.beginPath();
    ctx.moveTo(-1, 3); ctx.lineTo(-1, -2); ctx.quadraticCurveTo(4.5, -7, 10, -2); ctx.lineTo(10, 3); ctx.closePath();
    ctx.fill(); ctx.stroke();
    ctx.fillStyle = "#8a5a2c";
    ctx.fillRect(-2, 3, 13, 1.8);
    for (const wx of [1.5, 8]) { ctx.beginPath(); ctx.arc(wx, 6.3, 2.2, 0, Math.PI * 2); ctx.fillStyle = "#5a3a1c"; ctx.fill(); }
    person(ctx, c, { x: -6, y: 0, s: 0.9 });
  },
  scout(ctx, c) {
    person(ctx, c, { x: -1, y: 1 });
    ctx.strokeStyle = "#6b4626";
    ctx.lineWidth = 1.2;
    ctx.beginPath(); ctx.moveTo(4.5, -8); ctx.lineTo(3, 8); ctx.stroke();
    ctx.fillStyle = "#2e6f9e";
    ctx.beginPath(); ctx.arc(5.6, -7, 1.4, 0, Math.PI * 2); ctx.fill();
  },
  builder(ctx, c) {
    person(ctx, c, { x: -2, y: 1 });
    ctx.save();
    ctx.translate(5, -2);
    ctx.rotate(0.6);
    ctx.fillStyle = "#6b4626";
    ctx.fillRect(-0.6, -1, 1.2, 9);
    ctx.fillStyle = "#8b8f96";
    ctx.fillRect(-3, -2.4, 6, 2.2);
    ctx.restore();
  },
  guard(ctx, c) {
    person(ctx, c, { x: 1.5, y: 1 });
    ctx.fillStyle = c;
    ctx.strokeStyle = INK;
    ctx.lineWidth = 0.8;
    ctx.beginPath();
    ctx.moveTo(-8, -3); ctx.lineTo(-1.5, -3); ctx.lineTo(-1.5, 3); ctx.quadraticCurveTo(-4.7, 8, -8, 3); ctx.closePath();
    ctx.fill(); ctx.stroke();
    ctx.strokeStyle = "#6b4626";
    ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(6, -10); ctx.lineTo(6, 8); ctx.stroke();
  },
  envoy(ctx, c) {
    person(ctx, c, { x: -2, y: 1 });
    ctx.strokeStyle = "#6b4626";
    ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(4, -10); ctx.lineTo(4, 8); ctx.stroke();
    ctx.fillStyle = "#f7f3ea";
    ctx.strokeStyle = INK;
    ctx.lineWidth = 0.6;
    ctx.beginPath(); ctx.moveTo(4, -10); ctx.lineTo(10, -8); ctx.lineTo(4, -5.5); ctx.closePath(); ctx.fill(); ctx.stroke();
  },
  caravan(ctx, c) {
    // a camel with a load
    ctx.fillStyle = "#c08a4a";
    ctx.strokeStyle = INK;
    ctx.lineWidth = 0.7;
    ctx.beginPath();
    ctx.moveTo(-8, 1); ctx.quadraticCurveTo(-6, -6, -2, -3); ctx.quadraticCurveTo(1, -7, 4, -2);
    ctx.lineTo(6, -6); ctx.lineTo(9, -6.5); ctx.lineTo(8.5, -4.5); ctx.lineTo(7, -4); ctx.lineTo(6, 1); ctx.closePath();
    ctx.fill(); ctx.stroke();
    ctx.lineWidth = 1.2;
    for (const lx of [-6.5, -4, 2, 4.5]) { ctx.beginPath(); ctx.moveTo(lx, 1); ctx.lineTo(lx, 7.5); ctx.stroke(); }
    ctx.fillStyle = c;
    ctx.fillRect(-4.5, -6.5, 5, 2.8);
  },
};
