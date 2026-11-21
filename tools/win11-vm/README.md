# win11-vm

Helper files for the `win11` libvirt VM that runs Sony's PS Remote Play with the Intel iGPU passed through. The guide that explains every step is in [`docs/Gaming/Windows VM/`](../../docs/Gaming/Windows%20VM/README.md); this folder only holds the files it uses.

Nothing here contains secrets. Where a file needs a user, host or path from your machine it says `<USER>`, `<HOST>` or similar, or reads `$SUDO_USER`, `$XDG_RUNTIME_DIR` and the `VM` environment variable (default `win11`).

## Files

| File | Installs to | Setup | Status |
| --- | --- | --- | --- |
| `host-setup.sh` | `/etc/default/grub`, `/etc/modprobe.d/vfio-igpu.conf`, `/etc/mkinitcpio.conf.d/20-vfio-igpu.conf`, `/usr/local/share/vfio/` | `sudo ./host-setup.sh`, reboot | Default |
| `host-rollback.sh` | removes the above, restores GRUB | `sudo ./host-rollback.sh`, reboot | Default |
| `xml/win11.xml` | `virsh define` (after filling in placeholders) | see vm-definition.md | Default (reference) |
| `guest-ps.py` | run from here | `./guest-ps.py [timeout] <<'EOF' … EOF` | Default |
| `qga-push.py` | run from here (needs `python-libvirt`) | `./qga-push.py <host file> <guest path>` | Default |
| `qga-pull.py` | run from here | `./qga-pull.py <guest path> <host file>` | Default |
| `guest/idle-shutdown.ps1` | guest `C:\Tools\idle-shutdown\` | scheduled task `IdleShutdown`, see windows-guest.md | Default |
| `vm-audio-acl.service` | `~/.config/systemd/user/` | `systemctl --user enable --now vm-audio-acl` | Default |
| `win11-audio-sink.service` | `~/.config/systemd/user/` | `systemctl --user enable --now win11-audio-sink` | Default (needed for host recording) |
| `deskflow/Deskflow.host.conf`, `deskflow/deskflow-server.conf` | `~/.config/Deskflow/` | fill in `<HOST>`, `<USER>` | Default |
| `deskflow/Deskflow.guest.conf` | guest `C:\ProgramData\Deskflow\Deskflow.conf` | copy with the guest agent | Default |
| `deskflow/make-certs.sh` | writes `./host/` and `./guest/` TLS files | run once, copy the results | Default |
| `ds4-host-prep.sh` | `/etc/udev/rules.d/70-raw-gadget.rules`, loads `dummy_hcd` + `raw_gadget` | `sudo ./ds4-host-prep.sh` | Default |
| `ds4gadget.py`, `ds4_report_desc.bin` | run from here | `./ds4gadget.py [--log FILE] [--no-pad]` | Default (started by hand) |
| `xbox-vm-attach`, `xbox-vm-attach@.service`, `99-xbox-vm-attach.rules`, `xbox-vm-attach-install.sh` | `/usr/local/bin/`, `/etc/systemd/system/`, `/etc/udev/rules.d/` | `sudo ./xbox-vm-attach-install.sh` | Superseded: worked, but Remote Play ignores an Xbox pad; `ds4-host-prep.sh` disables the rule |

## Differences from the copies that were run

These files were first written and run on the machine during setup. The repo copies were changed only where the originals hard-coded something local:

- `host-setup.sh` downloads the iGPU ROM from the [LongQT-sea/intel-igpu-passthru v0.1 release](https://github.com/LongQT-sea/intel-igpu-passthru/releases/tag/v0.1) and checks its sha256 (the original copied a local file with the same hash). It backs up GRUB to `/etc/default/grub.pre-vfio-igpu` instead of next to the script, and `host-rollback.sh` reads it from there.
- `ds4-host-prep.sh` (originally `host-prep.sh`) gives `/dev/raw-gadget` to `$SUDO_USER` instead of a fixed user name.
- `guest-ps.py`, `qga-push.py`, `qga-pull.py` and `xbox-vm-attach` read the domain name from `VM`.
- `deskflow/` holds templates of the configs the setup produced; `make-certs.sh` repeats the certificate commands that were run by hand.
- `xml/win11.xml` has no UUID, MAC or VNC password, and placeholders for the disk, ISO and runtime directory.
