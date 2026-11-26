// The game extension: an ancient 4X game played on the real terrain (see rules.js for the setting).
import { installGame } from "./ui.js";

export function install(app) {
  installGame(app);
}
