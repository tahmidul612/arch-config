# vm-launch

"Windows VM" in the KDE app launcher: a Rust ([Tauri 2](https://tauri.app)) window that starts the win11 VM and sets up its display path through [`vm-display`](../looking-glass/vm-display). What it does and why: [docs/Gaming/Windows VM/launcher.md](../../../docs/Gaming/Windows%20VM/launcher.md).

## Files

| File | Installs to | Setup | Status |
| --- | --- | --- | --- |
| `install.sh` | runs the steps below | `./install.sh` | Default (run 2026-11-22) |
| `src/main.rs`, `Cargo.toml`, `build.rs`, `tauri.conf.json`, `capabilities/` | `~/.local/bin/vm-launch` (release build) | `install.sh` | Default |
| `ui/index.html`, `ui/app.css`, `ui/app.js` | embedded in the binary | | Default |
| `ui/vendor/gsap.min.js` | embedded | GSAP 3.15.1, [standard no-charge license](https://gsap.com/standard-license) | Default |
| `icons/net.local.vm-launch.svg`, `icons/net.local.vm-launch-small.svg` | `~/.local/share/icons/hicolor/` (PNGs rendered by `install.sh`; the small SVG is used up to 32 px) | `install.sh` | Default |
| `icons/icon.png` | window icon, embedded at build time | | Default |
| `vm-launch.desktop` | `~/.local/share/applications/` (`<USER>` filled in) | `install.sh` | Default |
| `PRODUCT.md` | not installed | design brief for the UI | Reference |

## Run

```bash
vm-launch              # the window
vm-launch --go         # launch at once with the last settings
vm-launch --dry-run    # same steps, nothing touched (vm-display launch --dry-run)
```

`VM` (default `win11`) and `VM_DISPLAY` (default `~/.local/bin/vm-display`) override the domain and the script.

The UI also works in a normal browser without Tauri, with a mock backend, for design work: open `ui/index.html?vm=running&mode=hdmi`, or `?launch&fail=rp` to play a launch that fails at Remote Play.

## Tested

On 2026-11-22: dry runs of both modes in the window; a live launch from a stopped VM in Looking Glass mode with 1080p + FSR, Remote Play and the DS4 (about one minute to "Ready"); a live switch of the running VM to 4K native (guest at 3840x2160 @150%, client started with FSR and CAS off). Direct HDMI was run only as a dry run. The panel is event-driven (inotify, uevents, pidfds; see the docs page); `VM_LAUNCH_DEBUG=1` logs each event. Idle cost and dependency versions: see the docs page.
