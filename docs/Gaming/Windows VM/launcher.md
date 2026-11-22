# Launcher

"Windows VM" in the app launcher starts the VM and sets up how its picture reaches the LG in one step. It is a small Rust ([Tauri 2](https://tauri.app)) window over [`vm-display`](https://github.com/tahmidul612/arch-config/blob/main/tools/win11-vm/looking-glass/vm-display): the window only reads state and shows progress, and every action is a `vm-display` command you can also run by hand ([Switching](switching.md)).

Source: [`tools/win11-vm/launcher/`](https://github.com/tahmidul612/arch-config/blob/main/tools/win11-vm/launcher/README.md).

## What it does

The left column shows what is live: the display mode, Windows' resolution, the LG's input, the Looking Glass client, Remote Play, the virtual DualShock 4 and OBS. While a launch runs it turns into the list of steps, each ticked off as `vm-display` reports it. The right side has the signal path (Windows → Looking Glass or the iGPU's HDMI → LG) and the choices:

| Choice | Options | What happens |
| --- | --- | --- |
| Display | Looking Glass, Direct HDMI | `vm-display lg` or `vm-display hdmi` ([Display](display.md)) |
| Picture (Looking Glass only) | 1080p + FSR, 4K native | 1080p: Windows at 1920x1080 @100%, the client upscales with FSR 1 ([why](display.md#resolution-and-upscaling)). 4K: Windows at 3840x2160 @150% (guest task `Display-lgonly4k`), the client started with `eglFilter:ffxFSR=no eglFilter:ffxCAS=no` so that nothing filters the picture (the flags were checked on the running client; that they override `client.ini` was not checked on screen). Direct HDMI is always native 4K. |
| Open PS Remote Play | on, off | starts `RemotePlay.exe` in the logged-in session (guest task `App-RemotePlay`) and waits until it runs |
| Virtual DualShock 4 | on, off | starts or stops `ds4-vm.service` ([Input](input.md)) |
| Switch the LG to HDMI (Direct HDMI only) | on, off | off sets Windows up for HDMI but leaves the LG on the host (`vm-display hdmi --no-switch`); switch it later with the LG's joystick, and the `lg-input` watcher turns DP-1 off. ++meta+ctrl+f11++ would go back to Looking Glass instead |
| Record with OBS (Looking Glass only) | on, off | `vm-rec start` after the picture is up ([Recording](recording.md)) |
| Close when ready | on, off | quits the window after a successful launch |

Choices are remembered. If the VM is already running when the window opens, the choices start from what it is doing and the button says Apply (the start and login steps are then skipped). "Back to host" runs `vm-display host`: the LG goes back to the host desktop, the client closes and Windows keeps running. "Hold to shut down" (hold for about a second) stops a recording and runs `vm-display shutdown`. ++enter++ launches, ++esc++ closes the window.

The menu entry's right-click actions are "Launch with last settings" (`vm-launch --go`), "Back to host", "Shut down Windows" and "Dry run" (`vm-launch --dry-run`: the same steps, nothing touched).

## Tested

On 2026-11-22: a live launch from a stopped VM in Looking Glass mode with 1080p + FSR, Remote Play and the DS4 ("Ready" after about a minute; the login wait held the layout until the AtLogon task had run); a live switch of the running VM to 4K native; dry runs of both modes. A shutdown through `vm-display shutdown` took 8 s, and the window showed the VM off within a second of it. Not run live yet: Direct HDMI and "Back to host". Remote Play closed by itself once, around the switch to 4K, without an error in the Application log; why was not found.

## Launch steps

`vm-display launch` runs these and prints one `@step <id> <run|ok|skip|fail> [detail]` line per change, which the window turns into the step list:

1. start the VM if it is off (`virsh start`);
2. wait for the guest agent;
3. wait for Windows' login: the `Display-lgonly-AtLogon` task puts Windows on 1080p 15 s after each login, so any other layout is applied only after it has run (skipped if the VM was already up);
4. start or stop `ds4-vm.service`;
5. the display path: the same steps as `vm-display lg` (LG on DP, guest layout, client) or `vm-display hdmi` (guest layout, Deskflow, LG to HDMI), with the same fallback to Looking Glass if the HDMI switch fails;
6. Remote Play and the recording, if chosen.

The guest tasks `Display-lgonly4k` and `App-RemotePlay` are registered the first time they are needed, for the same user as `Display-lgonly`; [`register-tasks.ps1`](https://github.com/tahmidul612/arch-config/blob/main/tools/win11-vm/guest/register-tasks.ps1) creates them too.

## Install

Needs `cargo`, `webkit2gtk-4.1` and `librsvg` (all in the Arch repos), plus the rest of this guide's setup.

```bash
cd tools/win11-vm/launcher
./install.sh
```

The script builds the release binary into `~/.local/bin/vm-launch`, links `~/.local/bin/vm-display` to the repo copy (an existing plain file is kept as `vm-display.pre-launcher`), renders the icon into `~/.local/share/icons/hicolor/` and installs the menu entry `vm-launch.desktop`.

Check it: "Windows VM" shows up in the app launcher with its icon, and `vm-launch --dry-run` opens the window with a "dry run" badge; Launch ticks through every step without starting anything.

## Why Tauri, and what it costs

The UI is HTML and CSS with [GSAP](https://gsap.com) for the step and signal-path animations, rendered by the system WebKitGTK, so no browser engine is bundled: the release binary is 3.6 MB (Tauri's default features trimmed to `wry` and `compression`). Dependencies as built on 2026-11-22: tauri 2.12.7, wry 0.57.3, serde 1.0.229, serde_json 1.0.154, inotify 0.11.5 (without its tokio feature), libc 0.2 and GSAP 3.15.1, each the latest stable release (Tauri 3 and libc 1.0 are still alpha).

The panel does not poll. One backend thread sleeps until the kernel reports a change and pushes a new state to the page only when something differs:

| Change | Source |
| --- | --- |
| VM started or stopped | inotify on libvirt's `/run/libvirt/qemu/` (`<name>.pid` appears and goes away) |
| Display mode, picture | inotify on `~/.local/state/vm-display/` |
| Virtual DS4 attached or removed | kernel uevents for USB `054c:05c4` (netlink; receiving needs no privileges) |
| Looking Glass client, OBS, `ds4-vm` exited | a pidfd per process, readable when it exits |
| LG input, OBS recording | asked every 15 s and right after a VM or mode change: DDC and OBS have no event source |

After each event it looks once more 1.5 s later, because things start in steps (the client maps its window, the DS4 enumerates). Starting a process has no event, so a client or OBS started outside the launcher shows up on the next of those looks. Uptime is counted by the page from the VM's start time. "Hold to shut down" stays in "shutting down" until libvirt removes the pid file, then says "Windows is off" (after 3 minutes it adds that Windows may be installing updates). Set `VM_LAUNCH_DEBUG=1` to log each event and look to stderr.

libvirt's own lifecycle events (`virConnectDomainEventRegisterAny`) would also report a guest-initiated shutdown in progress, but the Rust `virt` crate (0.5) does not wrap them; the run directory gives start and stop without linking libvirt.

Measured idle with the window open, over 30 s: 0.1% of one core for the window and its WebKit processes (2% when the page polled every 2 s), and 240 to 330 MB of memory (PSS), most of it WebKit's web process. That memory is the price of a web UI; a native toolkit would need a fraction of it but would lose the GSAP animations. Close the window once the game runs ("Close when ready"), and it uses nothing.
