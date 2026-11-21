# Audio

QEMU plays the VM's sound straight into the host's PipeWire. There is no viewer in between, so sound works whatever display mode is in use.

Status: **working**. Verdict after real use (2026-11-21), on VM audio during Remote Play: "Works and in sync." That verdict covers the PipeWire backend going straight to the speakers. The `win11_vm` sink below was added afterwards and has been checked with a test recording and a VM restart, not yet in a play session.

```text
Windows "Speakers (High Definition Audio Device)"  (ich9)
  -> QEMU PipeWire stream "win11"
  -> virtual sink win11_vm "Windows VM"            (win11-audio-sink.service)
  -> loopback "Windows VM (to speakers)"
  -> default output (USB DAC here)
```

The `win11_vm` sink exists so host OBS can record only the VM's sound, from `win11_vm.monitor`. Without host recording, QEMU's stream can go to the default output directly and the sink is optional.

## 1. VM XML

The `<audio type='pipewire'>` element is on [VM definition](vm-definition.md#audio). It replaced `<audio type='spice'>`, which makes no sound unless a SPICE viewer is connected.

## 2. Let QEMU reach your PipeWire socket

QEMU runs as `libvirt-qemu`, not as you, and cannot enter `/run/user/<UID>`. [`vm-audio-acl.service`](https://github.com/tahmidul612/arch-config/blob/main/tools/win11-vm/vm-audio-acl.service) gives that user traverse-only (`x`) permission on the runtime directory at each login:

```bash
cp tools/win11-vm/vm-audio-acl.service ~/.config/systemd/user/
systemctl --user enable --now vm-audio-acl
getfacl -p "$XDG_RUNTIME_DIR" | grep libvirt-qemu      # user:libvirt-qemu:--x
```

The alternative was `user = "<USER>"` in `/etc/libvirt/qemu.conf`. It was rejected because it applies to every VM and gives a compromised QEMU process your account's access.

## 3. The `win11_vm` sink

[`win11-audio-sink.service`](https://github.com/tahmidul612/arch-config/blob/main/tools/win11-vm/win11-audio-sink.service) runs a `pw-loopback`: its capture side is a sink called `win11_vm`, and its playback side follows the default output.

```bash
cp tools/win11-vm/win11-audio-sink.service ~/.config/systemd/user/
systemctl --user enable --now win11-audio-sink
```

Start the VM, then move QEMU's stream onto the sink once. WirePlumber remembers the choice for the stream name `win11`:

```bash
idx=$(pactl -f json list sink-inputs | python3 -c 'import json,sys; print(next(i["index"] for i in json.load(sys.stdin) if i["properties"].get("media.name")=="win11"))')
pactl move-sink-input "$idx" win11_vm
grep 'media.name:win11=' ~/.local/state/wireplumber/stream-properties   # ..."target":"win11_vm"...
```

## Check

The routing survives VM restarts: after a full stop and start on 2026-11-21 (22:47 UTC), QEMU's new `win11` stream went straight to `win11_vm` with no manual step. If it ever lands on the default output instead, repeat the `move-sink-input` step.

```bash
pactl list sink-inputs short       # the qemu-system-x86_64 "win11" stream is on win11_vm's index
pw-link -l | grep -A1 qemu         # QEMU output ports linked
```

In the guest, `Get-PnpDevice -Class AudioEndpoint` shows "Speakers (High Definition Audio Device)" and "Line In" as OK. Then play something in Windows.

## Not verified yet

- Microphone: the XML also creates an input stream `win11-mic`, which PipeWire connects to the default source (a webcam mic here). Nothing has used it. Remove the `<input>` line if you don't want the VM to have a mic.
- The 20 ms latency was not tuned or measured.

## Files

| File | Installed to | Purpose |
| --- | --- | --- |
| `tools/win11-vm/vm-audio-acl.service` | `~/.config/systemd/user/` | Traverse ACL on `$XDG_RUNTIME_DIR` for `libvirt-qemu` |
| `tools/win11-vm/win11-audio-sink.service` | `~/.config/systemd/user/` | Virtual sink `win11_vm`, looped to the default output |
