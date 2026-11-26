// Used only by the single-file build (tools/bundle.mjs): registers the extensions so ext/index.js needs no import().
import * as layers from "./ext/layers/index.js";
import * as game from "./ext/game/index.js";

globalThis.__IRANCIV_EXT__ = { "./layers/index.js": layers, "./game/index.js": game };
