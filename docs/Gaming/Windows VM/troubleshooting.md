# Troubleshooting

## Symptoms and fixes

### Remote Play closes right after it opens

Remote Play 9.5 dies about 20 seconds after launch: Event Viewer shows Application Error 1000 for `RemotePlay.exe`, exception `0xc0000005`, faulting module "unknown". Two causes were found:

1. **ViGEmBus is installed.** Remote Play 9.x deliberately crashes when it finds ViGEmBus (its crash info names it as "Badware ViGemBus"; see [DS4Windows issue #27](https://github.com/ds4windowsapp/DS4Windows/issues/27)). Uninstall ViGEmBus *and* delete its service, because the MSI uninstall leaves it behind: `sc.exe delete ViGEmBus`.
2. **Something else in this VM.** With ViGEmBus gone, and with Deskflow stopped as well, 9.5 still crashed. The cause was not found; detection of the VM is a guess.

Fix: use the [patched 8.5](windows-guest.md#ps-remote-play-85-patched). It runs fine in the same VM.

### The HDMI monitor stays dark after the Intel driver installs

Fully stop and start the VM ([how](vm-definition.md#restarting-so-changes-apply)). A restart from inside Windows, or a hot driver install, is not enough.

Check that the monitor is on the HDMI input and awake. Windows reports a monitor on another input as PnP status "Unknown", and display changes (resolution etc.) fail until it is active.

### Code 43 or a black screen with the iGPU

Not seen here. The documented fallbacks are the `RKL_TGL_ADL_RPL_GOPv17_igd.rom` from the same [LongQT-sea release](https://github.com/LongQT-sea/intel-igpu-passthru/releases/tag/v0.1) (change the `<rom file=...>`), and setting the firmware's primary display to IGD.

### The VM won't start after adding the iGPU: `vfio_container_dma_map ... -22`

`<maxphysaddr>` is missing or larger than the IOMMU width. See [Host preparation](host-prep.md#4-read-the-iommu-address-width).

### The VNC password disappeared

The XML was dumped without `--security-info` and redefined. Set the password again in the `<graphics type='vnc'>` element, and dump with `--security-info` from now on.

### Deskflow: the guest says "Timed out"

The host is not listening or the firewall drops the port. Check `ss -ltn | grep 24800` on the host and `sudo ufw status | grep 24800`. Remember that UFW is the active firewall; see [Input](input.md#2-host-server) before touching nftables.

### Deskflow: the host server exits with code 4

Run `deskflow-core server` by hand to see the reason. "unknown computer name" means the computers are not declared as `[computer_<name>]` in `Deskflow.conf` (a `screens` section is ignored in 1.27).

### Deskflow: the guest ignores `C:\ProgramData\Deskflow\Deskflow.conf`

The service is reading its own `systemprofile\AppData\Roaming\Deskflow\Deskflow.conf`. Rename that file ([Input](input.md#3-guest-client)) and restart the service. `deskflow-daemon.log` names the settings file it loaded.

### The controller does nothing in Remote Play

- `journalctl --user -u ds4-vm -n 30`: did it see `RemotePlay.exe` and attach the DS4? `virsh -c qemu:///system dumpxml win11 | grep -A3 "type='usb'"` should list `054c:05c4` while Remote Play runs.
- `lsmod | grep -E 'dummy_hcd|raw_gadget'` after a reboot; if empty, run `ds4-modules-setup.sh`.
- If you run the gadget by hand, detach and attach again after any restart; a stale hostdev entry points at the old USB address.
- On the console, press PS and choose the user at "Who's using this controller?".
- The PS button (Xbox Guide) opens Game Bar instead: set `UseNexusForGameBarEnabled=0` ([Windows guest](windows-guest.md#game-bar-and-the-ps-button)).

### `guest-ps.py` fails with "Unable to encode message payload"

The script is too big for one `guest-exec` call. Upload it with `qga-push.py` and run the file.

### A file in the guest stays locked ("being used by another process")

A `guest-file-write` with too large a chunk hung the agent and leaked the handle. Use a new file name, or reboot the guest. `qga-push.py` uses 64 KiB chunks for this reason.

### No sound from the VM

- `getfacl -p "$XDG_RUNTIME_DIR" | grep libvirt-qemu` must show `--x`; otherwise `systemctl --user start vm-audio-acl` and fully restart the VM.
- `pactl list sink-inputs short` should list QEMU's `win11` stream. If it is on a sink you don't hear, move it ([Audio](audio.md#3-the-win11_vm-sink)).

### The Looking Glass IDD won't install: `SetupCopyOEMInfW: 0xe0000242`

The silent install can't ask whether to trust the publisher. Add HostFission's certificate to `LocalMachine\TrustedPublisher` and run `LGIddInstall.exe install LGIdd LGInput` ([Display](display.md#looking-glass), Guest tab).

### The Looking Glass client shows only its splash screen

The client is connected but the guest sends no frames: no `Format:` line in `journalctl --user -u lg-client` after "Starting session". The upscaling tests hit this several times on 2026-11-21, each after many client restarts and guest resolution changes in a row; restarting the client or re-applying the layout did not help. Restart the IDD in the guest (Device Manager, or elevated PowerShell), then `vm-display lg`:

```powershell
$d = Get-PnpDevice -FriendlyName 'Looking Glass Indirect Display Device'
Disable-PnpDevice -InstanceId $d.InstanceId -Confirm:$false; Start-Sleep 3
Enable-PnpDevice -InstanceId $d.InstanceId -Confirm:$false
```

Afterwards the client may sit in a small window; `vm-display client` puts it back full screen on DP-1.

### The 1080p guest looks blocky in the Looking Glass client

FSR is off, or the client hasn't had a new frame since it started: the first frame is drawn before the filter is set up. Open or move a window in Windows; if it stays blocky, check `[eglFilter] ffxFSR=yes` in `~/.config/looking-glass/client.ini` and the overlay (++right-ctrl+o++). Without FSR the client scales an exact 2x with nearest neighbour.

### No 4K modes in the guest with Looking Glass

The shared memory is too small: 64 MiB fits 1440p only. Set the `shmem` size to 128 MiB, `truncate -s 0 /dev/shm/looking-glass`, and fully stop and start the VM.

### The HDMI output turns itself off, or input switching to HDMI is ignored

The IDD helper enforces "Looking Glass as the only display" unless `HKLM\SOFTWARE\LookingGlass\IDD` `ExclusiveMonitor` is `0` (restart `LGIddHelper` after changing it). The LG ignores a switch to an input that has no signal, so the HDMI output must be active (`Display-hdmi`) before `lg-input hdmi`. Check the helper's log in `C:\ProgramData\Looking Glass (IDD)\` for "Enforced Looking Glass as the only display".

### The LG says "out of range" and its input menu shows the wrong input

Only VCP 0x60 reached it, which half-switches this monitor. `lg-input hdmi` follows 0x60 with the VM's F4 command; if the VM could not send it (its HDMI output was off, or the guest agent did not answer), pick the input on the LG's joystick and run `lg-input rescue`.

### The LG shows "No Signal" on HDMI and nothing answers

The VM is off or its HDMI output is off, so neither side can reach the LG over DDC. Use the joystick, Input > DisplayPort, then `lg-input rescue`.

### A recording shows one frozen frame

Host OBS still maps the old shared memory after a VM restart or resize. Start recordings with `vm-rec start`, which reopens it, or toggle the "VM display" source.

### The Looking Glass client is on the wrong screen or not running

`vm-display client` starts it again and moves it full screen to DP-1. Nothing starts it at login.

## What was tried

What was tried and dropped, with the reason. None of these are steps to follow.

### Client and approach

| Option | Result | Why it was dropped |
| --- | --- | --- |
| chiaki-ng (installed on the host) over PSN | Failed | Needs a one-time registration over the console's LAN or IP. TCP 9295, 997 and 9304 are closed at the remote site, and chiaki-ng 1.10.1 has no PSN-only registration (an unofficial fork claims one). |
| Official Remote Play in a Windows VM | **Used** | Works over PSN with nothing done at the remote site. |
| Remote Play under Wine/Lutris | Not tried | Reported as not playable (sign-in fails). |
| Asobi | Not tried | Paid; Linux build only in a Steam playtest; internet play unconfirmed. |
| Someone at the remote site registers chiaki-ng | Not tried | Would remove the need for the VM, but needs on-site help. |
| A real DualShock 4 / DualSense, or a hardware adapter (Xbox pad → DS4) | Not tried | No Sony controller at hand; an adapter costs money. The virtual DS4 made both unnecessary. |

### VM

| Option | Result | Why |
| --- | --- | --- |
| 4 vCPU, unpinned (original) | Superseded | Showed as 4 one-core sockets; Windows client uses at most 2. Replaced by 8 vCPU in one socket, pinned. |
| macvtap on the wired NIC (bridge mode) | Failed | Meant to avoid double NAT. The guest got a LAN address but no internet (DNS resolved, ping and HTTPS failed). The cause is only guessed (the router not letting new devices online). Back on libvirt NAT, which works. |
| Legacy IGD passthrough | Not used | QEMU 10.1+ limits it to Gen 6-9; `x-igd-lpc` is i440fx-only. |
| GOPv17 ROM, firmware IGD-primary | Not needed | The noGOP ROM worked. |
| Hiding the hypervisor from Windows | Not tried | Low odds of fixing the 9.5 crash, small slowdown. |

### Remote Play version

| Option | Result |
| --- | --- |
| 9.5.0.08040 (current from Sony) | Failed: crash on launch, see above. Tested with ViGEmBus present, removed, and with Deskflow stopped. |
| 8.5 from Sony's own URL | Failed: Sony now serves a 1-byte file there (MSI error 1620 through the bootstrapper). |
| 8.5 from Uptodown | Failed: blocked by Cloudflare for automated download. |
| 8.5.0.08070 MSI from the Internet Archive + version patcher | **Used.** Sony-signed MSI; the patcher makes it report 9.5. |
| Hosts-file block of Sony's update servers | Not applied: no forced update with the patched version, and blocking the wrong host could break PSN sign-in. Add only if the pairing keeps resetting. |

### Controller

| Option | Result | Why |
| --- | --- | --- |
| Xbox pad passed to the VM + ViGEmBus + DS4Emulator 2.2 | Failed | Windows saw the pad and the emulated DS4 (XInput live, ~127 HID reports/s), but Remote Play ignored it and the PS button did nothing. Remote Play ignores ViGEm's DS4 since v6 ([ViGEmBus #172](https://github.com/nefarius/ViGEmBus/issues/172)) and 9.x crashes with ViGEmBus installed. ViGEmBus is uninstalled. |
| DS4Emulator's keyboard/mouse mode | Turned off | It grabbed the mouse (`ActivateInAnyWindow=0`, `EnableCentering=0`). Moot once DS4Emulator was dropped. |
| Xbox pad hot-plug attach (`xbox-vm-attach`) | Superseded | Worked on replug, but the pad has to stay on the host for the virtual DS4. |
| Virtual DS4 started by hand | Superseded | Worked for 6+ hours of play; replaced by `ds4-vm.service`. |
| `ds4-vm.service` attaching the DS4 for as long as the VM runs | Superseded | Changed so other games and apps never see a DS4: now only while Remote Play runs. |
| XI2DS, DS4Windows | Not tried | Same ViGEm limitation. |
| Virtual DS4 on the host (`ds4gadget.py`) | **Used** | See [Input](input.md#virtual-dualshock-4). |
| Rust rewrite of the virtual DS4 | Not adopted | Benchmarked against a Rust port: same latency (within 0.1 ms, median about 2.2 ms on the host), only CPU differs (0.1% vs 0.4%). See [Input](input.md#latency-python-vs-rust). |

### Keyboard, mouse and firewall

| Option | Result | Why |
| --- | --- | --- |
| Deskflow 1.27 | **Used** | |
| Deskflow always on from autostart | Superseded | A second input path in lg mode; now started only in hdmi mode. |
| Lan Mouse | Not tried | Named fallback if Deskflow misbehaves on Wayland. |
| Input Leap, Barrier | Not tried | Archived / unmaintained and X11 only. |
| nftables rule via `/etc/nftables.conf` | Failed | nftables.service was inactive; loading the file added a stray drop-policy table for about 4 minutes. Removed and replaced by a UFW rule. |

### Display

| Option | Result | Why |
| --- | --- | --- |
| SPICE / VNC in virt-manager | Kept as boot console only | Reported as "very choppy" before the iGPU was passed through; the cause was not measured. |
| SPICE with streaming off + remote-viewer, Sunshine/Moonlight inside the VM | Not tried | The iGPU passthrough came first and made them unnecessary. |
| HDMI dummy plug, capture card | Not tried | Wanted a solution without extra hardware. The Looking Glass IDD needs neither. |
| 1440p desktop (2026-11-21) | Superseded | Set to cut the load of guest recording. Back to native 4K once recording moved to the host and 1440p looked soft in Looking Glass. |
| Native 4K desktop at 150% in lg mode (2026-11-21) | Superseded | Remote Play streams 1080p, so the guest only upscaled it. Replaced by 1080p + client FSR: as sharp or sharper for the stream, guest iGPU 41% instead of 79%. |
| Looking Glass B7 (stable) | Not used | No IDD, so it would still need a monitor or dummy plug on the iGPU. |
| Looking Glass at 1440p, upscaled to the 4K panel | Superseded | Text looked softer than on HDMI. |
| Looking Glass upscaling a 1080p guest: default (nearest), linear, CAS 0.5, FSR 1 at 0.0/0.5/1.0, FSR 1 + CAS 0.5 | **FSR 1 at 1.0 used** | Best VMAF NEG of all, level with or ahead of a 4K guest (lanczos/bicubic stand-ins for Remote Play's scaler). Linear blurs (-9 VMAF); CAS on top of FSR oversharpens. Measured with [`upscale-bench`](https://github.com/tahmidul612/arch-config/blob/main/tools/win11-vm/upscale-bench/README.md); see [Display](display.md#resolution-and-upscaling). |
| Mirroring the Looking Glass monitor and the HDMI output | Failed | `SDC_TOPOLOGY_CLONE` returned 31, a supplied clone config 87, `DisplaySwitch /clone` did nothing: the two are on different adapters and no path feeds the IDD from the Intel source. Replaced by the lg and hdmi modes. |
| **lg mode** and **hdmi mode** with `vm-display` | **Used** | See [Display](display.md). |

### LG input switching

| Option | Result | Why |
| --- | --- | --- |
| VCP 0x60 from the host or from Windows (dxva2) | Failed | Half-switches: picture moves, menu stale, "out of range". dxva2 can't send from source 0x50. |
| LG's F4 from the host (`ddcutil --i2c-source-addr=x50 setvcp f4 0x90`) | Failed | Worked once, then ignored on every later try. |
| Host 0x60, then VM F4 over IGCL; VM F4 to go back | **Used** | Confirmed clean both ways. |

### Recording

| Option | Result | Why |
| --- | --- | --- |
| Guest OBS, QSV HEVC, 4K canvas and 4K60 output | Failed | 625 of 739 frames skipped; the iGPU was overloaded. |
| Guest OBS, QSV HEVC target usage 1, 4K canvas to 1080p | Failed | A 3 h 36 min session logged 98.3% of frames skipped; the file was a slideshow (audio was fine). The UHD 770 can't decode the stream, run the desktop and encode at once. |
| Guest OBS, QSV target usage 7 at 1440p | Works (benchmark) | Kept as the alternative; close to the iGPU's limit. |
| Guest OBS with x264 | Unknown | The benchmark was invalid: OBS kept using QSV. |
| HDMI splitter and capture card | Not tried | Needs hardware. |
| Host OBS via Looking Glass, NVENC | **Used** | See [Recording](recording.md). |
| Scream for audio | Not used | The Looking Glass docs advise against it; QEMU's PipeWire stream plus the `win11_vm` sink records cleanly. |

### Audio

| Option | Result | Why |
| --- | --- | --- |
| SPICE audio (original) | Superseded | Silent unless a SPICE viewer is connected. |
| PipeWire backend, ACL unit | **Used** | |
| PipeWire backend, `user=` in `qemu.conf` | Not tried | Applies to all VMs and widens what QEMU can access. |
| Looking Glass client's own audio | Disabled | It would become Windows' default output and bypass the PipeWire path. |

### Idle shutdown

| Option | Result | Why |
| --- | --- | --- |
| Task Scheduler idle trigger (`ShutdownOnInactivity`) | Superseded | Windows 8+ ignores the idle duration; it fired minutes after boot and ignored controller play. Disabled. |
| `IdleShutdown` script task | **Used** | Input and network based; see [Windows guest](windows-guest.md#idle-shutdown). |

## Open items

Each with the test that would settle it. Status as of 2026-11-21.

### Verify in real use

| Item | How to settle it |
| --- | --- |
| Remote Play streamed in lg mode: does its anti-tamper object to the Looking Glass IDD; quality and latency | Play a session through the Looking Glass client. |
| Desktop text at 1080p + FSR in lg mode | Compare the same window in lg and hdmi mode (hdmi stays native 4K). |
| Remote Play's own frame pacing through Looking Glass (the frame-rate test used a WPF player) | Turn on the client's FPS display (++right-ctrl+d++) in a session; UPS should stay near 60. |
| `vm-rec` on real gameplay | Record a session; check smooth video (`mpdecimate`) and game audio. |
| VM audio after a host reboot (a VM restart is already verified) | Reboot the host, start the VM, check `pactl list sink-inputs short`. Fallback: pin QEMU's stream to the sink in the XML. |
| Virtual DS4: pad unplug during a session, VM stop and start, first host reboot | Do each once with `journalctl --user -u ds4-vm -f` open. |
| hdmi ↔ lg round trip with the new per-layout resolutions (the virtual monitor now changes 4K ↔ 1080p, which preceded every stuck-splash case in the tests) | `vm-display hdmi`, then `vm-display lg`: the guest at 3840x2160 150%, then 1920x1080 100%, and `journalctl --user -u lg-client` shows a `Format:` line. Not run yet: the LG stopped answering DDC on DP-1 the same night. |
| `Display-lgonly-AtLogon` on the next VM boot | Boot the VM; `vm-display status` shows the Looking Glass monitor at 1920x1080 100%. |
| Deskflow in hdmi mode | Move the mouse into the VM after `vm-display hdmi`. |
| LG switching: VM shutdown in hdmi mode (guest hook to DP, then the watcher runs `vm-display lg`) | Shut Windows down in hdmi mode and check that the LG lands on the Looking Glass client. |
| `lg-input rescue` and its shortcut | Press ++meta+ctrl+shift+f11++ in hdmi mode. |
| A manual joystick input switch in hdmi mode | The watcher should follow (DP-1 on/off), but `vm-display`'s stored mode does not change, so the next toggle may go the wrong way once. Switch with the joystick, then toggle. |
| KDE shortcuts pressed for real, and after a re-login | They probably don't fire while Deskflow has the cursor in the VM; use ++ctrl+alt+f11++ there. |
| Windows display sleep in hdmi mode | May make the watcher turn DP-1 off and on repeatedly. Let Windows blank the screen and watch `journalctl --user -u lg-input-watch`. |
| Idle shutdown: no shutdown during controller-only play, shutdown after 60 min idle | Read `idle-shutdown.log` after both. |

### Not yet explained

| Item | How to settle it |
| --- | --- |
| Slideshow-like playback seen on the LG during the 2026-11-21 playback benchmark (`perf.py`) | Not reproduced: the frame-rate test (`fps.py`) measured about 60 frames per second for both guest resolutions. `perf.py` polled the guest's GPU engine counters (`Get-Counter`) while the clip played and `fps.py` did not, so that polling is the first suspect; the run also overlapped a moment with two Looking Glass clients. Re-run `perf.py` with the client's FPS display on. |
| Does CPU pinning help? | Compare frame pacing with and without `<cputune>`. |
| Why does 9.5 crash without ViGEmBus? | Read `RemotePlayCrashInfo_*.txt` after a 9.5 crash, or try hiding the hypervisor (`<kvm><hidden state='on'/></kvm>`). |
| Why did macvtap get no internet? | Check the router's client list and DHCP log while the VM is on macvtap. |
| Windows Update error `0x80240016` for the Intel driver | Retry the driver update through the Settings app on a fresh VM. |
| Why is the host's F4 command ignored by the LG? | Compare `ddcutil` traces of the one success and later failures. |
| 1-2% repeated frames in the host capture benchmark | Capture a source with a frame counter instead of Edge. |
| Does the Game Bar setting matter? | Toggle `UseNexusForGameBarEnabled` and press PS in Remote Play. |

### Decisions and options

- The Xbox pad reaches the VM only as the DS4 while Remote Play runs; other games in the VM get no controller.
- Rumble from the game is not forwarded to the Xbox pad.
- Move from Looking Glass B8-rc1 to the final B8 when it ships (client, OBS plugin and IDD must match).
- The kvmfr module instead of the `/dev/shm` file (DMA-BUF import).
- Stay on patched Remote Play 8.5 and don't let it update itself.
- The mic input stream `win11-mic` is unused.

### Clean-up

- Guest benchmark leftovers (task `BenchEdge`, test clips), the in-guest OBS and its `OBSRec` task, and the disabled `ShutdownOnInactivity` task.
- The HostFission certificate in `TrustedPublisher`, if Looking Glass is removed.
- The unused Xbox hot-plug udev rule, unit and script on the host.
