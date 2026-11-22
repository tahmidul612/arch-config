# Windows VM for PS Remote Play

A PS5 at a remote site, played from this desktop with Sony's official PS Remote Play client. The client runs in a Windows 11 VM (libvirt/QEMU) because nothing on Linux can reach this console. [chiaki-ng](https://github.com/chiaki-ng/chiaki-ng) needs a one-time registration over the console's LAN or a reachable IP, and the remote site allows neither. The official client connects through PSN with nothing set up at the other end.

To make that playable, the VM gets:

- the CPU's Intel UHD 770 iGPU, passed through with VFIO, for video decode and a real display output
- a virtual DualShock 4 made on the host from an Xbox controller, because Remote Play accepts only Sony controllers
- Deskflow for the host's keyboard and mouse, PipeWire for sound, and the guest agent for automation

## How it fits together

```text
 Host (CachyOS, RTX 3080 drives the desktop)                Windows 11 VM "win11"
 ----------------------------------------------            ----------------------------------
 Xbox pad --evdev--> ds4gadget.py                           PS Remote Play 8.5 (patched)
                     dummy_hcd + raw_gadget                    ^ DS4 054c:05c4 (USB hostdev)
                     = USB DualShock 4  ------------------->   |
 Deskflow server  <--- TCP 24800, TLS, virbr0 ------------  Deskflow client (service)
 PipeWire  <------ QEMU pipewire audio "win11" -----------  ich9 sound
 guest-ps.py / qga-*.py --- qemu-guest-agent ------------>  PowerShell, file copy
 vfio-pci: UHD 770 00:02.0  ============================>  Intel UHD 770 (decode, display)
                                                              -> HDMI to the monitor, or
 looking-glass-client  <=== /dev/shm/looking-glass =======     Looking Glass virtual display
 libvirt NAT 192.168.122.1  <---------------------------->  192.168.122.x -> internet -> PSN
```

There are two ways to see the VM: a Looking Glass window on the host (the default) or the monitor plugged straight into the iGPU's HDMI port, switched over with DDC/CI. The [Launcher](launcher.md) ("Windows VM" in the app menu) starts the VM and picks between them in one step.

## Status

Active right now (2026-11-21, night): lg mode with the guest at 1920x1080 (100%) upscaled to 4K by the Looking Glass client's FSR 1, `ds4-vm.service` enabled, Deskflow stopped (it runs only in hdmi mode), host OBS profile VM-Capture ready.

As of 2026-11-21. "Confirmed" means it was used in a real session; "tested" means it was checked by commands or a benchmark only.

| Part | Choice | Status |
| --- | --- | --- |
| Client | PS Remote Play 8.5.0.08070, patched to report 9.5 | Confirmed |
| CPU | 8 vCPU, one socket, pinned to P-cores 8-15, emulator on E-cores 16-19 | In use; benefit not measured |
| Network | libvirt NAT `default` | Confirmed |
| iGPU | VFIO, UPT mode, `Universal_noGOP_igd.rom`, Intel driver 32.0.101.7088 | Confirmed |
| Display | lg mode: guest at 1080p, Looking Glass client upscales to the 4K LG over DP with FSR 1 (default); hdmi mode: Windows at 4K on the LG's HDMI input; switched with `vm-display` | Both modes and the switching confirmed clean; 1080p + FSR measured as sharp as a 4K guest for a simulated Remote Play stream ([Display](display.md#resolution-and-upscaling)); a real Remote Play session through Looking Glass not yet |
| Recording | Host OBS from Looking Glass, NVENC HEVC, 1080p60 (`vm-rec`) | Benchmarks only |
| Controller | Virtual DS4 from the Xbox pad (`ds4gadget.py`), run by `ds4-vm.service` only while Remote Play is open | Gadget confirmed (6+ hours, started by hand); the service tested only |
| Keyboard and mouse | lg mode: the Looking Glass client. hdmi mode: Deskflow 1.27, TLS pinned, UFW rule on `virbr0`, started by `vm-display` | Deskflow confirmed on the HDMI monitor (mouse, keyboard, clipboard) |
| Audio | QEMU PipeWire backend, `win11_vm` loopback sink | Backend confirmed ("works and in sync"); the sink tested, including a VM restart |
| Idle shutdown | `IdleShutdown` task: 60 min without input and without a stream | Running; not yet observed over days |
| Old: Xbox pad in the VM + ViGEmBus | | Failed; Remote Play ignores it |
| Old: Remote Play 9.5 | | Failed; crashes in this VM |
| Old: guest OBS on Quick Sync | | Failed at the settings used in play; kept as a benchmarked alternative |
| Old: mirroring Looking Glass and HDMI | | Failed; Windows can't clone across the two adapters |

## Reading order

1. [Host preparation](host-prep.md): IOMMU, the iGPU to vfio-pci, the address width
2. [VM definition](vm-definition.md): every change to the libvirt XML
3. [Windows guest](windows-guest.md): guest agent helpers, Intel driver, Remote Play, idle shutdown
4. [Display](display.md): Looking Glass (lg mode) and the LG on HDMI (hdmi mode)
5. [Input](input.md): Deskflow and the virtual DualShock 4
6. [Audio](audio.md): PipeWire backend and the loopback sink
7. [Recording](recording.md): host OBS through Looking Glass, guest OBS as the alternative
8. [Switching](switching.md): lg ↔ hdmi, LG input switching, host ↔ guest recording
9. [Launcher](launcher.md): one window to start the VM and choose the display path
10. [Troubleshooting](troubleshooting.md): symptoms, everything that was tried, open items
10. [Rollback](rollback.md): undo any step

The helper files are in [`tools/win11-vm/`](https://github.com/tahmidul612/arch-config/blob/main/tools/win11-vm/README.md).

## Everyday use

```bash
virsh -c qemu:///system start win11
vm-display lg            # Looking Glass client full screen on the LG (or: vm-display hdmi)
# start Remote Play in the VM: ds4-vm.service attaches the virtual DS4 by itself
vm-rec start             # optional: record; vm-rec stop when done
# the VM shuts itself down after an hour idle, or:
virsh -c qemu:///system shutdown win11 --mode agent
```

Keyboard shortcuts: ++meta+ctrl+f11++ on the host or ++ctrl+alt+f11++ in the VM toggles between lg and hdmi mode; tap ++right-ctrl++ to capture or release input in the Looking Glass client.
