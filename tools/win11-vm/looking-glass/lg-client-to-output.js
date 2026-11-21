// KWin script: put the Looking Glass client window fullscreen on the output named in OUTPUT_NAME.
const OUTPUT_NAME = "DP-1";
const out = workspace.screens.find(o => o.name === OUTPUT_NAME);
for (const w of workspace.windowList()) {
  if (w.resourceClass === "looking-glass-client" && out) {
    w.fullScreen = false;
    workspace.sendClientToScreen(w, out);
    w.fullScreen = true;
    workspace.activeWindow = w;
  }
}
