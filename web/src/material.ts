// Material Design 3 for the web (Material Web components) and an M3 color scheme generated
// from Gatekeeper's verdigris seed with Material's own color utilities.
import "@material/web/button/filled-button.js";
import "@material/web/button/filled-tonal-button.js";
import "@material/web/button/outlined-button.js";
import "@material/web/button/text-button.js";
import "@material/web/chips/assist-chip.js";
import "@material/web/chips/chip-set.js";
import "@material/web/dialog/dialog.js";
import "@material/web/elevation/elevation.js";
import "@material/web/icon/icon.js";
import "@material/web/progress/circular-progress.js";
import "@material/web/progress/linear-progress.js";
import "@material/web/ripple/ripple.js";
import "@material/web/switch/switch.js";
import "@material/web/tabs/primary-tab.js";
import "@material/web/tabs/tabs.js";
import "@material/web/textfield/outlined-text-field.js";
import { argbFromHex, applyTheme, themeFromSourceColor } from "@material/material-color-utilities";

export const SEED = "#0F7B5F";

/** Writes the --md-sys-color-* tokens (light scheme) onto <html>. */
export function applyMaterialTheme() {
  const theme = themeFromSourceColor(argbFromHex(SEED));
  applyTheme(theme, { target: document.documentElement, dark: false });
}
