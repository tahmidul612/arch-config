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

- Is `ds4gadget.py` running, and is `054c:05c4` attached to the VM (`virsh -c qemu:///system dumpxml win11 | grep -A3 "type='usb'"`)? The attach is lost on every VM restart.
- After a gadget restart, detach and attach again; a stale hostdev entry points at the old USB address.
- On the console, press PS and choose the user at "Who's using this controller?".
- The PS button (Xbox Guide) opens Game Bar instead: set `UseNexusForGameBarEnabled=0` ([Windows guest](windows-guest.md#game-bar-and-the-ps-button)).

### `guest-ps.py` fails with "Unable to encode message payload"

The script is too big for one `guest-exec` call. Upload it with `qga-push.py` and run the file.

### A file in the guest stays locked ("being used by another process")

A `guest-file-write` with too large a chunk hung the agent and leaked the handle. Use a new file name, or reboot the guest. `qga-push.py` uses 64 KiB chunks for this reason.

### No sound from the VM

- `getfacl -p "$XDG_RUNTIME_DIR" | grep libvirt-qemu` must show `--x`; otherwise `systemctl --user start vm-audio-acl` and fully restart the VM.
- `pactl list sink-inputs short` should list QEMU's `win11` stream. If it is on a sink you don't hear, move it ([Audio](audio.md#3-the-win11_vm-sink)).

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
| XI2DS, DS4Windows | Not tried | Same ViGEm limitation. |
| Virtual DS4 on the host (`ds4gadget.py`) | **Used** | See [Input](input.md#virtual-dualshock-4). |

### Keyboard, mouse and firewall

| Option | Result | Why |
| --- | --- | --- |
| Deskflow 1.27 | **Used** | |
| Lan Mouse | Not tried | Named fallback if Deskflow misbehaves on Wayland. |
| Input Leap, Barrier | Not tried | Archived / unmaintained and X11 only. |
| nftables rule via `/etc/nftables.conf` | Failed | nftables.service was inactive; loading the file added a stray drop-policy table for about 4 minutes. Removed and replaced by a UFW rule. |

### Audio

| Option | Result | Why |
| --- | --- | --- |
| SPICE audio (original) | Superseded | Silent unless a SPICE viewer is connected. |
| PipeWire backend, ACL unit | **Used** | |
| PipeWire backend, `user=` in `qemu.conf` | Not tried | Applies to all VMs and widens what QEMU can access. |

### Idle shutdown

| Option | Result | Why |
| --- | --- | --- |
| Task Scheduler idle trigger (`ShutdownOnInactivity`) | Superseded | Windows 8+ ignores the idle duration; it fired minutes after boot and ignored controller play. Disabled. |
| `IdleShutdown` script task | **Used** | Input and network based; see [Windows guest](windows-guest.md#idle-shutdown). |

## Open items

Each with the test that would settle it.

| Item | How to settle it |
| --- | --- |
| Does CPU pinning help? | Compare frame pacing in a Remote Play session with and without `<cputune>`. |
| Why does 9.5 crash without ViGEmBus? | Read `RemotePlayCrashInfo_*.txt` after a 9.5 crash, or try hiding the hypervisor (`<kvm><hidden state='on'/></kvm>`). |
| Why did macvtap get no internet? | Check the router's client list and DHCP log while the VM is on macvtap. |
| Windows Update error `0x80240016` for the Intel driver | Retry the driver update through the Settings app on a fresh VM. |
| Does the Game Bar setting matter? | Toggle `UseNexusForGameBarEnabled` and press PS in Remote Play. |
| Mic input | Use a voice chat in a game; or remove the `<input>` stream. |
| Idle shutdown over days | Read `idle-shutdown.log` after a week of normal use. |
| Deskflow in Looking Glass mode | Play a session with Deskflow stopped and see whether the Looking Glass client's own input is enough. |
| DS4 gadget as a service | A user unit plus a libvirt hook that re-attaches the DS4 after each VM start. |
