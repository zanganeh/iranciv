// Level 1, "The First City": the Simurgh, the wise bird of Persian legend, teaches the game one step at a time.
// Each step says one thing, lights up the one control or place it is about, and moves on by itself once the
// player has done it. The level is the real game on the real map, with no rivals yet.

import { citiesOf, unitsOf, canFound } from "./game.js";

export const SIMURGH_SVG = `<svg class="gm-simurgh" viewBox="0 0 160 140" aria-hidden="true">
  <defs>
    <linearGradient id="smg-b" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#3fc1c9"/><stop offset="1" stop-color="#1d6f8a"/></linearGradient>
    <linearGradient id="smg-w" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#f2c45a"/><stop offset="1" stop-color="#c0392b"/></linearGradient>
  </defs>
  <g class="smg-tail">
    <path d="M70 92 C 40 110, 18 118, 4 134 C 30 126, 52 118, 74 100Z" fill="#1d6f8a"/>
    <path d="M76 96 C 60 118, 46 128, 36 138 C 58 130, 72 118, 82 102Z" fill="#2a9aa6"/>
    <path d="M84 98 C 80 120, 76 130, 74 140 C 88 128, 92 116, 90 100Z" fill="#3fc1c9"/>
    <circle cx="16" cy="128" r="5" fill="#f2c45a"/><circle cx="16" cy="128" r="2.4" fill="#1f3d7a"/>
    <circle cx="44" cy="132" r="4.5" fill="#f2c45a"/><circle cx="44" cy="132" r="2.1" fill="#1f3d7a"/>
    <circle cx="76" cy="134" r="4" fill="#f2c45a"/><circle cx="76" cy="134" r="1.9" fill="#1f3d7a"/>
  </g>
  <g class="smg-wing smg-back"><path d="M86 70 C 100 30, 130 14, 156 10 C 140 30, 128 50, 104 76Z" fill="url(#smg-w)" opacity=".8"/></g>
  <path d="M60 70 C 62 52, 82 46, 96 58 C 104 66, 100 92, 82 100 C 68 104, 58 88, 60 70Z" fill="url(#smg-b)"/>
  <path d="M92 56 C 96 40, 110 34, 118 40 C 122 44, 120 50, 114 52 L 104 60Z" fill="url(#smg-b)"/>
  <path d="M117 42 L 132 44 L 118 49Z" fill="#f2c45a"/>
  <circle cx="113" cy="43" r="2.2" fill="#fff"/><circle cx="113.5" cy="43" r="1.1" fill="#1a1a1a"/>
  <path d="M104 36 C 102 26, 108 20, 112 16 M108 37 C 110 28, 116 24, 122 22 M100 40 C 96 32, 98 24, 100 20" stroke="#f2c45a" stroke-width="2.4" fill="none" stroke-linecap="round"/>
  <g class="smg-wing smg-front"><path d="M74 66 C 70 34, 44 14, 16 8 C 30 30, 40 52, 66 80Z" fill="url(#smg-w)"/>
    <path d="M70 62 C 60 42, 46 30, 30 22 M66 70 C 56 54, 44 44, 26 38" stroke="#fff3c8" stroke-width="1.4" fill="none" opacity=".7"/></g>
</svg>`;

const imps = (g) => citiesOf(g, g.human).reduce((n, c) => n + c.tiles.filter((i) => g.imp[i]).length, 0);
const pioneer = (g) => unitsOf(g, g.human).find((u) => u.type === "pioneer");
const builder = (g) => unitsOf(g, g.human).find((u) => u.type === "builder");
const city = (g) => citiesOf(g, g.human)[0];

// {say, target(ctx) → {sel} | {cell} | null, done(ctx) → bool, next?: button label}
export const LEVEL1 = [
  { say: "Peace be upon you, young ruler. I am the Simurgh, keeper of this land's oldest wisdom. Let me guide your first steps.", next: "Continue" },
  { say: "Your people have come down from the mountains with a Pioneer. Rivers mean water and life, so I have marked the best place to settle with a ★.", next: "Show me", target: (x) => (x.settle?.[0] != null ? { cell: x.settle[0] } : null) },
  { say: "The green button always shows your best next move. Tap it to lead your Pioneer to the ★.", target: () => ({ sel: ".gm-step.main" }),
    done: (x) => !!city(x.g) || (pioneer(x.g) && (pioneer(x.g).dest >= 0 || canFound(x.g, x.board, x.g.human, pioneer(x.g).cell).ok && x.settle?.[0] === pioneer(x.g).cell)) },
  { say: "Time stands still until you let it flow. Tap ▶ and watch the seasons pass.", target: () => ({ sel: '[data-speed="1"]' }),
    done: (x) => !!city(x.g) || x.speed > 0 || (pioneer(x.g) && pioneer(x.g).dest < 0) },
  { say: "You have arrived. Tap the green button to found your first city.", target: () => ({ sel: ".gm-step.main" }), done: (x) => !!city(x.g) },
  { say: "Your first city stands! A city needs work to do. Tap the green button to train a Builder.", target: () => ({ sel: ".gm-step.main" }),
    done: (x) => !!builder(x.g) || imps(x.g) > 0 || city(x.g)?.build?.id === "builder" },
  { say: "Let time flow again. The ring over the city shows the work; when it closes, your Builder is ready.", target: () => ({ sel: '[data-speed="1"]' }),
    done: (x) => !!builder(x.g) || imps(x.g) > 0 },
  { say: "Your Builder is ready. Tap the green button to send it to farm the land by the river.", target: () => ({ sel: ".gm-step.main" }),
    done: (x) => imps(x.g) > 0 || !!builder(x.g)?.auto },
  { say: "Gold gathers in your city's purse: the coin over the city. Tap it to collect your gold.", target: (x) => (city(x.g) ? { cell: city(x.g).cell } : null),
    done: (x) => (x.g.factions[x.g.human].collected || 0) > 0 },
  { say: "Gold can finish work at once, buy land and pay for buildings. Now let your scholars study: tap the green button if it asks, or let time run.",
    target: () => ({ sel: ".gm-step.main" }), done: (x) => !!x.g.factions[x.g.human].research || Object.keys(x.g.factions[x.g.human].techs).length > 0 },
  { say: "Last task: grow your city to 3 people. Food makes it grow. Tap ▶▶▶ to make the seasons fly.", target: () => ({ sel: '[data-speed="3"]' }),
    done: (x) => (city(x.g)?.pop || 0) >= 3 },
];

export function createTutorial({ getCtx, onComplete }) {
  let k = 0, el = null, ring = null, lastSay = "";
  const steps = LEVEL1;

  function mount() {
    el = document.createElement("div");
    el.className = "gm-tut";
    el.innerHTML = `<div class="gm-tut-bird">${SIMURGH_SVG}</div><div class="gm-tut-bubble"><b>Simurgh</b><p></p><div class="gm-tut-foot"><span class="gm-tut-dots"></span><button class="btn gm-tut-next"></button><button class="gm-link gm-tut-skip">Skip the lesson</button></div></div>`;
    el.querySelector(".gm-tut-next").addEventListener("click", () => { k++; update(true); });
    el.querySelector(".gm-tut-skip").addEventListener("click", () => { k = steps.length; update(true); });
    document.body.appendChild(el);
    ring = document.createElement("div");
    ring.className = "gm-tut-ring";
    document.body.appendChild(ring);
  }

  /** Re-check the current step (call after any change and every frame or so); advances past steps already done. */
  function update(force = false) {
    if (!el) mount();
    const x = getCtx();
    if (!x.g) return;
    while (k < steps.length && steps[k].done && steps[k].done(x)) k++;
    if (k >= steps.length) { destroy(); onComplete?.(); return; }
    const st = steps[k];
    if (st.say !== lastSay || force) {
      lastSay = st.say;
      const p = el.querySelector("p");
      p.textContent = st.say;
      el.classList.remove("pop");
      void el.offsetWidth;
      el.classList.add("pop");
    }
    const nb = el.querySelector(".gm-tut-next");
    nb.style.display = st.next ? "" : "none";
    nb.textContent = st.next || "";
    el.querySelector(".gm-tut-dots").innerHTML = steps.map((_, j) => `<i class="${j < k ? "done" : j === k ? "on" : ""}"></i>`).join("");
    // spotlight
    const tg = st.target ? st.target(x) : null;
    x.view.tutCell = tg?.cell ?? -1;
    const node = tg?.sel ? document.querySelector(tg.sel) : null;
    if (node) {
      const r = node.getBoundingClientRect();
      ring.style.display = "block";
      ring.style.left = r.left - 6 + "px";
      ring.style.top = r.top - 6 + "px";
      ring.style.width = r.width + 12 + "px";
      ring.style.height = r.height + 12 + "px";
    } else ring.style.display = "none";
  }

  function destroy() {
    el?.remove();
    ring?.remove();
    el = ring = null;
    const x = getCtx();
    if (x.view) x.view.tutCell = -1;
  }

  return { update, destroy, get step() { return k; } };
}
