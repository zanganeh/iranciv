// The game's own minimap: the ancient land under the fog, each people's borders and cities, and the camera's
// view box. Click or drag on it to move the camera. (The data map's minimap shows modern colours, so the game
// hides it and draws this one instead.)

const COVER_COLOR = {
  forest: "#4c6b3c", scrub: "#a99a63", grassland: "#8ba352", pasture: "#94a85a", cropland: "#7d9a44",
  wetland: "#5e7d68", urban: "#7d9a44", barren: "#c8b285", ice: "#e3e9ec", none: "#9a8f6c",
};

export function createMinimap(app, view) {
  const wrap = document.createElement("div");
  wrap.className = "gm-mini panel";
  wrap.innerHTML = `<canvas aria-label="Minimap: click to move the map"></canvas>`;
  const canvas = wrap.querySelector("canvas");
  const base = document.createElement("canvas");
  let k = 1, sig = "", baseVersion = -1, W = 0, H = 0;
  const dpr = Math.min(2, window.devicePixelRatio || 1);

  function size() {
    const grid = view.board.grid;
    const worldW = grid.W * (grid.cols + 0.5), worldH = grid.H * (grid.rows + 0.5);
    W = 200;
    k = W / worldW;
    H = Math.round(worldH * k);
    for (const c of [canvas, base]) { c.width = W * dpr; c.height = H * dpr; }
    canvas.style.width = W + "px";
    canvas.style.height = H + "px";
  }

  function rebuild() {
    const { g, board } = view;
    const { grid } = board;
    const ctx = base.getContext("2d");
    const s = k * dpr;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.fillStyle = "#16273a";
    ctx.fillRect(0, 0, base.width, base.height);
    const F = g.human >= 0 ? g.factions[g.human] : null;
    const cw = grid.W * s + 1, ch = grid.H * s + 1.5;
    for (let i = 0; i < board.n; i++) {
      const t = board.tiles[i];
      if (!t.land) continue;
      const x = (grid.cx(i) - grid.W / 2) * s, y = (grid.cy(i) - grid.H / 2) * s;
      if (F && !F.seen[i]) { ctx.fillStyle = "#0b0c10"; ctx.fillRect(x, y, cw, ch); continue; }
      ctx.fillStyle = t.relief >= 2 ? "#8a7f6a" : COVER_COLOR[t.cover] || COVER_COLOR.none;
      ctx.fillRect(x, y, cw, ch);
      const cid = g.terr[i];
      if (cid >= 0) {
        ctx.fillStyle = g.factions[g.cities[cid].f].color;
        ctx.globalAlpha = 0.55;
        ctx.fillRect(x, y, cw, ch);
        ctx.globalAlpha = 1;
      }
      if (F && !view.vis?.has(i)) { ctx.fillStyle = "rgba(10,12,16,0.35)"; ctx.fillRect(x, y, cw, ch); }
    }
    for (const c of g.cities) {
      if (F && !F.seen[c.cell]) continue;
      ctx.beginPath();
      ctx.arc(grid.cx(c.cell) * s, grid.cy(c.cell) * s, (c.capital ? 3 : 2.2) * dpr, 0, Math.PI * 2);
      ctx.fillStyle = "#fff";
      ctx.fill();
      ctx.lineWidth = 1.2 * dpr;
      ctx.strokeStyle = g.factions[c.f].color;
      ctx.stroke();
    }
    baseVersion = view.version;
    sig = "";
  }

  /** Called from the map overlay every frame; repaints only when the view or the game changed. */
  function draw(cam) {
    if (!view.g || !view.board) return;
    if (!W) size();
    if (baseVersion !== view.version) rebuild();
    const x0 = cam.x - cam.w / 2 / cam.scale, y0 = cam.y - cam.h / 2 / cam.scale;
    const x1 = cam.x + cam.w / 2 / cam.scale, y1 = cam.y + cam.h / 2 / cam.scale;
    const now = `${x0 | 0},${y0 | 0},${x1 | 0},${y1 | 0},${baseVersion}`;
    if (now === sig) return;
    sig = now;
    const ctx = canvas.getContext("2d");
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.drawImage(base, 0, 0);
    const s = k * dpr;
    ctx.strokeStyle = "#ffe9a8";
    ctx.lineWidth = 1.5 * dpr;
    ctx.strokeRect(x0 * s, y0 * s, (x1 - x0) * s, (y1 - y0) * s);
  }

  let dragging = false;
  const jump = (e) => {
    const r = canvas.getBoundingClientRect();
    const x = (e.clientX - r.left) / k, y = (e.clientY - r.top) / k;
    if (dragging === "drag") { app.cam.centerOn(x, y); app.requestDraw(); } else app.flyTo(x, y, app.cam.scale, 300);
  };
  canvas.addEventListener("pointerdown", (e) => { dragging = true; canvas.setPointerCapture(e.pointerId); jump(e); });
  canvas.addEventListener("pointermove", (e) => { if (dragging) { dragging = "drag"; jump(e); } });
  canvas.addEventListener("pointerup", () => (dragging = false));

  document.body.appendChild(wrap);
  return { draw, el: wrap, remove: () => wrap.remove() };
}
