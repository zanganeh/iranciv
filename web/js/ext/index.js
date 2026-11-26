// Extension loader. Each entry is a folder under web/js/ext/ whose index.js exports install(app) (sync or async).
// `app` is the API object built in main.js (also window.__app). A missing folder is fine: it is reported with
// console.info and skipped, so an extension can be left out without breaking the app. A failing install() is logged
// as an error and does not stop the others. Modules are fetched in parallel and installed in list order.
// layers/: static map layers (roads, rail, ports, forts, wonders, trade routes) and their toggles.
// game/: the ancient 4X game ("Play" button), on the real terrain with invented peoples.
// cityView and advisors read US-only data (ACS citizens, districts, advisors' Census tables); off until ported.
const MODULES = ["./layers/index.js", "./game/index.js"];

export async function installExtensions(app, list = MODULES) {
  // The single-file build registers its extensions in globalThis.__IRANCIV_EXT__ instead of loading them by path.
  const pre = globalThis.__IRANCIV_EXT__ || {};
  const loads = list.map((spec) => (pre[spec] ? Promise.resolve({ m: pre[spec] }) : import(spec).then((m) => ({ m }), (err) => ({ err }))));
  const installed = [];
  for (let k = 0; k < list.length; k++) {
    const spec = list[k];
    const { m, err } = await loads[k];
    if (err) {
      console.info(`extension ${spec} not loaded (${err.message || err})`);
      continue;
    }
    if (typeof m.install !== "function") {
      console.info(`extension ${spec} has no install(app); skipped`);
      continue;
    }
    try {
      await m.install(app);
      installed.push(spec);
    } catch (e) {
      console.error(`extension ${spec} failed to install`, e);
    }
  }
  app.extensions = installed;
  app.emit?.("extensions", installed);
  return installed;
}
