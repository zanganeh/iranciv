// Sound for the game, made in the browser with Web Audio (no sound files). The music is an original, endlessly
// varied melody on a plucked, santur-like voice in the Persian Shur mode (on D, with the quarter-flat second), over
// a soft frame-drum pulse in 6/8. Effects: coins, a finished building, a goal, a click. Starts only after a tap.

const SHUR = [0, 1.5, 3, 5, 7, 8, 10, 12, 13.5, 15, 17]; // semitones above D: D E-koron F G A Bb C D E-koron F G
const D3 = 146.83;

let ctx = null, master = null, musicGain = null, fxGain = null, timer = 0, nextAt = 0, step = 0, deg = 4, on = true;
const KEY = "iranciv.sound";
try { on = localStorage.getItem(KEY) !== "0"; } catch { /* no storage */ }

function ensure() {
  if (ctx) return ctx;
  const AC = window.AudioContext || window.webkitAudioContext;
  if (!AC) return null;
  ctx = new AC();
  master = ctx.createGain();
  master.gain.value = on ? 0.9 : 0;
  master.connect(ctx.destination);
  musicGain = ctx.createGain();
  musicGain.gain.value = 0.32;
  fxGain = ctx.createGain();
  fxGain.gain.value = 0.55;
  // a little room
  const verb = ctx.createConvolver();
  const len = ctx.sampleRate * 1.6, buf = ctx.createBuffer(2, len, ctx.sampleRate);
  for (let c = 0; c < 2; c++) {
    const d = buf.getChannelData(c);
    for (let i = 0; i < len; i++) d[i] = (Math.random() * 2 - 1) * Math.pow(1 - i / len, 3);
  }
  verb.buffer = buf;
  const wet = ctx.createGain();
  wet.gain.value = 0.25;
  musicGain.connect(master);
  musicGain.connect(verb);
  fxGain.connect(master);
  verb.connect(wet);
  wet.connect(master);
  return ctx;
}

const freq = (semi) => D3 * Math.pow(2, semi / 12);

/** One struck string: two detuned partials and a fast decay, like a hammered dulcimer. */
function pluck(f, t, { vol = 0.25, dur = 1.6, out = musicGain } = {}) {
  for (const [mult, det, v] of [[1, -3, 1], [1, 4, 0.8], [2, 0, 0.35], [3, 2, 0.12]]) {
    const o = ctx.createOscillator();
    o.type = mult === 1 ? "triangle" : "sine";
    o.frequency.value = f * mult;
    o.detune.value = det;
    const gn = ctx.createGain();
    gn.gain.setValueAtTime(0, t);
    gn.gain.linearRampToValueAtTime(vol * v, t + 0.004);
    gn.gain.exponentialRampToValueAtTime(0.0008, t + dur / mult);
    o.connect(gn);
    gn.connect(out);
    o.start(t);
    o.stop(t + dur + 0.05);
  }
}

/** A soft frame-drum stroke: low "dum" or bright "tak". */
function drum(t, low) {
  const o = ctx.createOscillator();
  o.type = "sine";
  o.frequency.setValueAtTime(low ? 95 : 210, t);
  o.frequency.exponentialRampToValueAtTime(low ? 55 : 160, t + 0.12);
  const gn = ctx.createGain();
  gn.gain.setValueAtTime(low ? 0.22 : 0.07, t);
  gn.gain.exponentialRampToValueAtTime(0.001, t + (low ? 0.35 : 0.12));
  o.connect(gn);
  gn.connect(musicGain);
  o.start(t);
  o.stop(t + 0.4);
}

// The melody wanders the mode in small steps, leans on the 4th (A) and comes home to D; long notes get the santur's
// tremolo (quick repeated strokes).
function schedule() {
  if (!ctx) return;
  const beat = 0.21; // an eighth note in 6/8
  while (nextAt < ctx.currentTime + 0.6) {
    const t = nextAt;
    const bar = step % 6;
    if (bar === 0) drum(t, true);
    else if (bar === 3) drum(t, true);
    else if (bar === 2 || bar === 5) drum(t, false);
    // melody: a note on most eighths, with rests and held notes
    if (Math.random() < 0.72) {
      const r = Math.random();
      deg += r < 0.35 ? -1 : r < 0.7 ? 1 : r < 0.8 ? -2 : r < 0.88 ? 2 : 0;
      if (step % 48 === 42) deg = 4; // lean on the A
      if (step % 48 === 47) deg = 0; // and come home
      deg = Math.max(0, Math.min(SHUR.length - 1, deg));
      const f = freq(SHUR[deg] + 12);
      if (Math.random() < 0.18) for (let k = 0; k < 4; k++) pluck(f, t + k * beat / 4, { vol: 0.12 - k * 0.015, dur: 0.6 });
      else pluck(f, t, { vol: 0.17 });
      if (bar === 0 && Math.random() < 0.6) pluck(freq(SHUR[deg % 7]), t, { vol: 0.08, dur: 2.2 }); // a low string
    }
    nextAt += beat;
    step++;
  }
}

export const sound = {
  /** Call from a click: browsers only allow audio after the player has interacted. */
  start() {
    if (!ensure()) return;
    if (ctx.state === "suspended") ctx.resume();
    if (timer) return;
    nextAt = ctx.currentTime + 0.1;
    timer = setInterval(schedule, 200);
  },
  stop() { clearInterval(timer); timer = 0; },
  get on() { return on; },
  toggle() {
    on = !on;
    try { localStorage.setItem(KEY, on ? "1" : "0"); } catch { /* ignore */ }
    if (ensure()) master.gain.setTargetAtTime(on ? 0.9 : 0, ctx.currentTime, 0.05);
    return on;
  },
  coin() {
    if (!ctx || !on) return;
    const t = ctx.currentTime;
    for (let k = 0; k < 4; k++) pluck(freq(31 + (k % 2) * 4), t + k * 0.06, { vol: 0.12, dur: 0.35, out: fxGain });
  },
  built() {
    if (!ctx || !on) return;
    const t = ctx.currentTime;
    [0, 7, 12, 15].forEach((s, k) => pluck(freq(12 + s), t + k * 0.07, { vol: 0.16, dur: 1.8, out: fxGain }));
  },
  goal() {
    if (!ctx || !on) return;
    const t = ctx.currentTime;
    [0, 3, 5, 7, 12].forEach((s, k) => pluck(freq(17 + s), t + k * 0.09, { vol: 0.15, dur: 1.2, out: fxGain }));
  },
  click() {
    if (!ctx || !on) return;
    pluck(freq(24), ctx.currentTime, { vol: 0.06, dur: 0.25, out: fxGain });
  },
};
