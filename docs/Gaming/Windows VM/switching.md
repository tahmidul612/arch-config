# Switching

How to move between the options on the other pages, in both directions. Each procedure says how to undo it, what it changes, what needs a restart, and how to confirm it worked.

## Display: lg and hdmi

One command does everything; the mode is stored in `~/.local/state/vm-display/mode` and logged to `~/.local/state/vm-display/vm-display.log`.

| You want | Host | Host keyboard shortcut | In the VM |
| --- | --- | --- | --- |
| lg → hdmi | `vm-display hdmi` | ++meta+ctrl+f11++ (toggle) | ++ctrl+alt+f11++ (toggle) |
| hdmi → lg | `vm-display lg` | ++meta+ctrl+f11++ (toggle) | ++ctrl+alt+f11++ (toggle) |
| Something is stuck | `lg-input rescue` | ++meta+ctrl+shift+f11++ | |

The [Launcher](launcher.md) does the same from a window (plus starting the VM, Remote Play and a recording). The app launcher also has "LG monitor: show the VM" (`vm-display hdmi`), "LG monitor: show the host" (`vm-display lg`), "toggle" and "rescue". Nothing needs a VM restart.

=== "lg → hdmi"

    `vm-display hdmi`:

    1. stops the Looking Glass client;
    2. runs the guest task `Display-hdmi`: the LG over HDMI becomes primary at 4K, the Looking Glass monitor moves to (3840,0);
    3. waits 3 s so the LG sees a signal on HDMI (the LG ignores a switch to an input without one);
    4. starts the host Deskflow server;
    5. turns the `lg-input` watcher's automation on and runs `lg-input hdmi`: the host sends VCP 0x60 = 0x11, then the VM sends LG's F4 = 0x90 ([below](#lg-input-switching));
    6. the watcher then turns host DP-1 off and makes DP-2 primary.

    If step 2 or 5 fails, it goes back to lg mode by itself.

    Confirm: Windows is on the LG, the LG's input menu says HDMI, no "out of range" message within ~15 s, and `vm-display status` says `mode: hdmi`, `input: hdmi`, `deskflow: running`. Confirmed 2026-11-21 at 4K: "Windows 4K, clean"; the host toggle (DP-1 off, the other monitor primary): "All correct".

    Undo: `vm-display lg`.

=== "hdmi → lg"

    `vm-display lg`:

    1. records mode lg and stops Deskflow (the Looking Glass client passes input in this mode);
    2. pauses the watcher's automation;
    3. if the LG is not on DP: turns host DP-1 back on from the saved layout and has the VM send F4 = 0xD0 (the VM can still reach the LG over HDMI at this point);
    4. `lg-input sync --force` makes sure DP-1 is on;
    5. runs the guest task `Display-lgonly`;
    6. starts the Looking Glass client full screen on DP-1.

    Confirm: the LG shows the client full screen, its menu says DisplayPort, no warning, and `vm-display status` says `mode: lg`, `input: dp`, `client: running`, `deskflow: stopped`. Confirmed 2026-11-21: "LG client, DP, clean"; the VM's toggle back: "All correct".

    Undo: `vm-display hdmi`.

=== "VM stops while in hdmi"

    Two things hand the LG back:

    - the guest task `LgInput-OnShutdown` (System event User32 1074, i.e. a Windows shutdown or restart) sends F4 = 0xD0 so the LG goes to DP;
    - when the VM is gone, the watcher runs `vm-display lg`, which turns DP-1 on, stops Deskflow and stores mode lg, so the next VM start comes up in lg mode. Windows' `Display-lgonly-AtLogon` task matches that on the guest side.

    Neither has been exercised yet. If the VM is off while the LG still shows HDMI, software cannot reach the LG: use its joystick, Input > DisplayPort, then `lg-input rescue`.

`lg-input rescue` turns every host display back on, tries to bring the LG back to DP, and runs `vm-display lg` if the VM is up. It has not been needed yet.

## LG input switching

This LG HDR 4K (model shown by EDID as "LG HDR 4K") switches inputs cleanly only with LG's private DDC/CI command, and only the side the LG is currently showing can talk to it:

- The host reaches the LG over DDC only while the LG shows DisplayPort; the VM only while it shows HDMI.
- The standard input-select command, VCP 0x60, half-switches it: the picture moves, but the LG's own input setting stays on the old input and it pops up "out of range" (seen 2026-11-21: "Display is showing VM, input list shows DisplayPort and out of range message popped up once").
- LG's command, VCP 0xF4 sent from DDC source address 0x50 (0x90 = HDMI-1, 0xD0 = DP), sets the input properly. Windows' monitor API (dxva2) can only send from 0x51, so the VM sends it with Intel's IGCL raw I2C call on the passed-through iGPU.
- From the host, 0xF4 is ignored nearly every time (it worked once, then never again); why is unknown.

So the working method is a hand-off:

| Direction | Step 1 | Step 2 | Success when |
| --- | --- | --- | --- |
| Host → VM | Host: `ddcutil -b <bus> --noverify setvcp 60 0x11` | VM: `lg-igcl.ps1 hdmi` (F4 = 0x90 from 0x50) | the VM reads 0x60 = 0x11 over HDMI |
| VM → host | VM: `lg-igcl.ps1 dp` (F4 = 0xD0 from 0x50) | | the host reads 0x60 = 0x0f over DP |

The VM's HDMI output must be active (layout `hdmi` or `extended`) for any of this; with the Looking Glass-only layout the iGPU sends no signal and the LG ignores the switch.

### Files

=== "Host"

    | File | Installed to | Purpose |
    | --- | --- | --- |
    | [`monitor-input/lg-input`](https://github.com/tahmidul612/arch-config/blob/main/tools/win11-vm/monitor-input/lg-input) | `~/.local/bin/lg-input` | `status`, `hdmi`/`vm`, `dp`/`host`, `toggle`, `sync [--force]`, `rescue`, `auto on\|off`, `watch` |
    | `monitor-input/lg-input-watch.service` | `~/.config/systemd/user/` | The watcher; `ExecStopPost` gives DP-1 back to the host whenever it stops |
    | `monitor-input/net.local.lg-input-{toggle,vm,host,rescue}.desktop` | `~/.local/share/applications/` | Launchers (replace `<USER>`) |
    | `monitor-input/register-shortcuts.py` | run once | Binds ++meta+ctrl+f11++ and ++meta+ctrl+shift+f11++ through kglobalaccel (needs `python-dbus`, `python-pyqt6`) |

    `lg-input` needs `ddcutil` (its udev rule gives the desktop user `/dev/i2c-*`), `kscreen-doctor` and `python-libvirt`. It finds the LG's I2C bus with `ddcutil detect`, and has the host outputs at the top of the file: `LG_OUT = 'DP-1'` (the LG) and `OTHER_OUT = 'DP-2'`.

    ```bash
    cd tools/win11-vm/monitor-input
    install -m755 lg-input ~/.local/bin/
    install -m644 lg-input-watch.service ~/.config/systemd/user/
    for f in net.local.lg-input-*.desktop; do sed "s#<USER>#$USER#" "$f" > ~/.local/share/applications/"$f"; done
    systemctl --user daemon-reload && systemctl --user enable --now lg-input-watch
    python3 register-shortcuts.py
    lg-input status -v        # dp + details
    ```

=== "Guest"

    | File | Installed to | Purpose |
    | --- | --- | --- |
    | [`guest/lg-igcl.ps1`](https://github.com/tahmidul612/arch-config/blob/main/tools/win11-vm/guest/lg-igcl.ps1) | `C:\Tools\monitor\` | `probe`, `dp`, `hdmi` over IGCL (`ControlLib.dll` from the Intel driver); needs admin |
    | [`guest/lg-vm-toggle.ps1`](https://github.com/tahmidul612/arch-config/blob/main/tools/win11-vm/guest/lg-vm-toggle.ps1) | `C:\Tools\monitor\` | Writes `request.txt` for the host watcher, which polls it through the guest agent; if nobody answers in 6 s it tries `lg-igcl.ps1 dp` locally, then shows a popup |
    | [`guest/register-tasks.ps1`](https://github.com/tahmidul612/arch-config/blob/main/tools/win11-vm/guest/register-tasks.ps1) | run once, elevated | Creates `LgInput-Toggle` (elevated, for the hotkey), `LgInput-OnShutdown` (SYSTEM, on event 1074), the `Display-*` tasks and the desktop shortcut "Switch LG monitor (host - VM)" with ++ctrl+alt+f11++ |

    `register-tasks.ps1` was written for these docs from the tasks on the working VM and has not been run itself.

### Watcher safety rules

- It turns DP-1 off only in hdmi mode, only when the host has lost DDC twice in a row *and* the VM reads 0x11, and only if DP-2 is connected and enabled.
- It turns DP-1 back on only if it turned it off itself (marker file `~/.local/state/lg-input/dp1-disabled-by-lg-input`), from the layout it saved (`layout.json`).
- It never acts while the LG state is unknown (off or asleep), backs off 30 s after a failed confirm, and takes a lock around every action.
- It does nothing in lg mode, while `lg-input auto off` is set, or while `/tmp/kvm-display-state` exists (a marker another display tool can use to say "hands off").
- `lg-input hdmi` refuses unless the VM's agent and HDMI output answer (`--force` overrides), so you can't strand the LG on an input the VM can't switch back.

Not exercised yet: the shutdown hook, `rescue`, the watcher reacting to a manual joystick switch, and the shortcuts after logging in again.

## Recording: host or guest OBS

=== "Guest → host (current)"

    1. Stop guest OBS if it runs: `Stop-ScheduledTask OBSRec` and close `obs64.exe`. Optionally disable the task.
    2. Set up [Host OBS](recording.md#host-obs-default). It needs lg mode's Looking Glass device, nothing else in the VM.
    3. Confirm: `vm-rec start`, wait a minute, `vm-rec status` shows `outputSkippedFrames` near 0; check the file with `mpdecimate`.

    Undo: the steps in the other tab.

=== "Host → guest"

    1. `vm-rec stop`; quit host OBS (`systemctl --user stop obs-vm-capture`).
    2. In the guest, set the "Recording" profile to QSV HEVC target usage 7 (the profile's state is unknown, [see](recording.md#guest-obs-alternative)) and start `OBSRec`.
    3. Confirm on a 2-minute test that skipped frames stay at 0 and the 3D engine stays below ~80% in Task Manager.

    Not exercised: this direction has not been done since recording moved to the host. Undo: the steps in the other tab.

## Deskflow

Follows the display mode by itself: `vm-display lg` stops the host server, `vm-display hdmi` starts it. At login, `~/.config/autostart/vm-display-deskflow.desktop` runs `vm-display deskflow sync`; Deskflow's own autostart entry, if any, is hidden (`Hidden=true`). By hand: `vm-display deskflow on|off|sync`. See [Input](input.md#start-and-stop).

## Controller

Nothing to switch: the virtual DualShock 4 appears when Remote Play starts in the VM and goes away when it closes ([Input](input.md#virtual-dualshock-4)).
