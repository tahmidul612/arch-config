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
| `ds4-modules-setup.sh` | `/etc/modules-load.d/ds4-gadget.conf` | `./ds4-modules-setup.sh` (asks for sudo) | Default |
| `ds4-vm.py`, `ds4gadget.py`, `ds4_report_desc.bin` | `~/.local/lib/ds4-vm/` | copy; `python3 ds4gadget.py [--log FILE] [--no-pad] [-v]` by hand | Default |
| `ds4-vm.service` | `~/.config/systemd/user/` | `systemctl --user enable --now ds4-vm` | Default (tested by the setup session only) |
| `looking-glass/lg-host-setup.sh` | AUR packages, `/etc/tmpfiles.d/10-looking-glass.conf` | `./lg-host-setup.sh` as your user | Default |
| `looking-glass/10-looking-glass.conf` | `/etc/tmpfiles.d/` (written by the script) | reference | Default |
| `looking-glass/client.ini` | `~/.config/looking-glass/` | copy | Default |
| `looking-glass/vm-display`, `looking-glass/lg-client-to-output.js` | symlink `vm-display` into `~/.local/bin/` | `vm-display lg\|hdmi\|toggle\|status\|client\|deskflow` | Default |
| `looking-glass/vm-display-deskflow.desktop` | `~/.config/autostart/` (fill in `<USER>`) | | Default |
| `looking-glass/vm-rec`, `looking-glass/obsws.py` | symlink `vm-rec` into `~/.local/bin/` (needs `python-websockets`) | `vm-rec start\|stop\|status` | Default (benchmarks only) |
| `looking-glass/obs/basic.ini`, `recordEncoder.json`, `VM-Capture.json` | `~/.config/obs-studio/basic/profiles/VM-Capture/`, `.../scenes/` (fill in `<RECORDINGS_DIR>`) | | Default |
| `monitor-input/lg-input`, `lg-input-watch.service` | `~/.local/bin/`, `~/.config/systemd/user/` | `systemctl --user enable --now lg-input-watch` | Default |
| `monitor-input/net.local.lg-input-*.desktop`, `register-shortcuts.py` | `~/.local/share/applications/` (fill in `<USER>`); shortcuts via kglobalaccel | `python3 register-shortcuts.py` once | Default |
| `guest/display-layout.ps1` | guest `C:\Tools\display\` | tasks `Display-*` | Default |
| `guest/lg-igcl.ps1`, `guest/lg-vm-toggle.ps1` | guest `C:\Tools\monitor\` | tasks `LgInput-*` | Default |
| `guest/register-tasks.ps1` | run once in the guest, elevated | `register-tasks.ps1 -User <WINDOWS_USER>` | Written for the docs, not run (the tasks it creates were) |
| `xbox-vm-attach`, `xbox-vm-attach@.service`, `99-xbox-vm-attach.rules`, `xbox-vm-attach-install.sh` | `/usr/local/bin/`, `/etc/systemd/system/`, `/etc/udev/rules.d/` | `sudo ./xbox-vm-attach-install.sh` | Superseded: worked, but Remote Play ignores an Xbox pad; `ds4-host-prep.sh` disables the rule |

## Differences from the copies that were run

These files were first written and run on the machine during setup. The repo copies were changed only where the originals hard-coded something local:

- `host-setup.sh` downloads the iGPU ROM from the [LongQT-sea/intel-igpu-passthru v0.1 release](https://github.com/LongQT-sea/intel-igpu-passthru/releases/tag/v0.1) and checks its sha256 (the original copied a local file with the same hash). It backs up GRUB to `/etc/default/grub.pre-vfio-igpu` instead of next to the script, and `host-rollback.sh` reads it from there.
- `ds4-host-prep.sh` (originally `host-prep.sh`) gives `/dev/raw-gadget` to `$SUDO_USER` instead of a fixed user name.
- `guest-ps.py`, `qga-push.py`, `qga-pull.py` and `xbox-vm-attach` read the domain name from `VM`.
- `deskflow/` holds templates of the configs the setup produced; `make-certs.sh` repeats the certificate commands that were run by hand.
- `xml/win11.xml` has no UUID, MAC or VNC password, and placeholders for the disk, ISO and runtime directory.
- `looking-glass/vm-display` and `vm-rec` find `guest-ps.py`, the KWin script and `obsws.py` relative to their own (symlink-resolved) location instead of a backup folder, and `vm-display` reads `VM`. `client.ini`'s comment now says 4K.
- `monitor-input/lg-input` reads `VM`, and its stand-down marker is described generically.
- `.desktop` files, the tmpfiles rule and the OBS profile have `<USER>` / `<RECORDINGS_DIR>` placeholders; the OBS profile lost two machine-specific lines (`FFFilePath`, `CookieId`).
- The display XML is the same for lg and hdmi mode (they differ only inside Windows), so there is one `xml/win11.xml`.
- Not included: the unused `lg-ddc.ps1` (dxva2, a dead end), Intel's IGCL headers and sample, `setres.ps1` (superseded by `display-layout.ps1`), and the Looking Glass IDD installer (download it).
