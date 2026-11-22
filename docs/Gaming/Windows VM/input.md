# Input

Two separate things: the host's keyboard and mouse reaching Windows (Deskflow), and a controller Remote Play accepts (a virtual DualShock 4 made on the host).

## Deskflow: keyboard and mouse

[Deskflow](https://github.com/deskflow/deskflow) shares the host's keyboard, mouse and clipboard with the VM, as if the VM's screen sat next to the host monitor. The host is the server and the VM the client, over libvirt's NAT on TCP 24800. Both sides use pinned TLS certificates, so neither shows a trust prompt.

It was picked after comparing the open-source options (2026-11-21): Deskflow 1.27 (active, GPL-2.0, official Windows MSI and an Arch package), Lan Mouse 0.11 (younger, weak clipboard; the fallback if Deskflow misbehaves on Wayland, never tried), Input Leap (archived) and Barrier (dead, X11 only).

Status: **working** with the monitor on the iGPU's HDMI port. Confirmed in real use (2026-11-21): mouse crossing, keyboard and clipboard. It is only needed in [hdmi mode](display.md#lg-on-hdmi): in lg mode the Looking Glass client passes keyboard and mouse, so `vm-display` runs the host server only in hdmi mode ([Start and stop](#start-and-stop)). Deskflow switched by `vm-display` has not been used in a real hdmi-mode session yet.

### 1. Certificates

```bash
mkdir -p /tmp/df && cd /tmp/df
~/path/to/arch-config/tools/win11-vm/deskflow/make-certs.sh
```

[`make-certs.sh`](https://github.com/tahmidul612/arch-config/blob/main/tools/win11-vm/deskflow/make-certs.sh) makes one self-signed identity per side (CN=Deskflow, 10 years) and writes each side's trust file with the other side's fingerprint as `v2:sha256:<hex>`. CachyOS has no `/etc/ssl/openssl.cnf`; without the minimal `-config` the script passes, `openssl req` fails, and with errors hidden you end up pinning the hash of an empty file.

### 2. Host (server)

=== "Install and configure"

    ```bash
    sudo pacman -S deskflow
    mkdir -p ~/.config/Deskflow/tls
    cp /tmp/df/host/* ~/.config/Deskflow/tls/ && chmod 600 ~/.config/Deskflow/tls/*
    cp tools/win11-vm/deskflow/Deskflow.host.conf ~/.config/Deskflow/Deskflow.conf
    cp tools/win11-vm/deskflow/deskflow-server.conf ~/.config/Deskflow/
    # replace <HOST> (output of `hostname`) and <USER> in both files
    ```

    Deskflow 1.27 needs every computer declared as `[computer_<name>] name=<name>` in `Deskflow.conf`. A `section: screens` in the server config is ignored, and the links then fail with "unknown computer name". `deskflow-server.conf` only holds the links: the VM is off the **left** edge of the host monitor.

=== "Firewall"

    The host firewall is UFW. Allow the port only on the libvirt bridge, only from the NAT subnet:

    ```bash
    sudo ufw allow in on virbr0 from 192.168.122.0/24 to any port 24800 proto tcp comment 'deskflow from win11 VM'
    sudo ufw status | grep 24800
    #  24800/tcp on virbr0   ALLOW   192.168.122.0/24   # deskflow from win11 VM
    ```

    The same pattern is used for [Mosh](../../Utilities/mosh.md).

    !!! danger "Don't load `/etc/nftables.conf`"
        `nftables.service` is inactive on this machine. Adding the rule to `/etc/nftables.conf` and running `nft -f` loaded a second filter table with a drop policy next to UFW's tables, which blocks other inbound traffic until it is deleted (`sudo nft delete table inet filter`).

### Start and stop

`vm-display` starts and stops the host server with the display mode: `vm-display hdmi` starts it, `vm-display lg` stops it, and `vm-display deskflow on|off|sync` does it by hand. It starts Deskflow through its desktop entry, because a bare `deskflow` command is refused by the desktop portal ("An app id is required").

At login, [`vm-display-deskflow.desktop`](https://github.com/tahmidul612/arch-config/blob/main/tools/win11-vm/looking-glass/vm-display-deskflow.desktop) runs `vm-display deskflow sync` after 8 s:

```bash
sed "s#<USER>#$USER#" tools/win11-vm/looking-glass/vm-display-deskflow.desktop > ~/.config/autostart/vm-display-deskflow.desktop
vm-display deskflow on && ss -ltn | grep 24800        # listening on 0.0.0.0:24800
```

If Deskflow's own entry is in `~/.config/autostart/` (its GUI adds one when you tick "start at login"), add `Hidden=true` to it so it doesn't start the server in lg mode.

Without `vm-display` (no Looking Glass), start it with `kioclient exec /usr/share/applications/org.deskflow.deskflow.desktop` and leave its autostart entry visible. KDE may ask you to allow input capture the first time the cursor crosses over.

### 3. Guest (client)

Run as administrator in the guest, or through [`guest-ps.py`](windows-guest.md#running-commands-in-the-guest):

```powershell
$d = 'C:\Users\Public\Downloads\deskflow'; New-Item -ItemType Directory -Force $d | Out-Null
$m = "$d\deskflow-1.27.0-win-x64.msi"
Invoke-WebRequest 'https://github.com/deskflow/deskflow/releases/download/v1.27.0/deskflow-1.27.0-win-x64.msi' -OutFile $m -UseBasicParsing
(Get-FileHash $m).Hash -eq '0CA5668C4731F5477D0B189BDB11196A2CA51281985D9D8C0757BF7F3B782D64'   # the MSI is not signed
Start-Process msiexec -ArgumentList "/i `"$m`" /qn /norestart" -Wait
Get-Service Deskflow     # Running, Automatic
```

Then put the settings and certificates in `C:\ProgramData\Deskflow\`:

1. [`Deskflow.guest.conf`](https://github.com/tahmidul612/arch-config/blob/main/tools/win11-vm/deskflow/Deskflow.guest.conf) as `C:\ProgramData\Deskflow\Deskflow.conf`. It points the client at `192.168.122.1` and tells the service (`[daemon] configFile`) to read this file.
2. `guest/deskflow.pem` and `guest/trusted-servers` from step 1 into `C:\ProgramData\Deskflow\tls\`. Write them without a BOM (`[IO.File]::WriteAllText` with `UTF8Encoding($false)`), then lock the folder:

    ```powershell
    icacls C:\ProgramData\Deskflow\tls /inheritance:r /grant:r "SYSTEM:(OI)(CI)F" "Administrators:(OI)(CI)F"
    ```

3. The service runs as SYSTEM and on its first start creates its own settings file, which then wins over ProgramData. Move it out of the way and restart:

    ```powershell
    $u = 'C:\Windows\System32\config\systemprofile\AppData\Roaming\Deskflow\Deskflow.conf'
    Stop-Service Deskflow; if (Test-Path $u) { Move-Item $u "$u.disabled" -Force }; Start-Service Deskflow
    ```

Check:

```powershell
Get-Content C:\ProgramData\Deskflow\deskflow-daemon.log -Tail 5   # settings file: C:/ProgramData/Deskflow/Deskflow.conf
```

On the host, `ss -tn | grep 24800` shows an established connection, and the guest's `deskflow-daemon.log` says "connected to secure socket". Then move the mouse off the left edge.

## Virtual DualShock 4

Remote Play only accepts Sony controllers. The Xbox controller stays on the host, and [`ds4gadget.py`](https://github.com/tahmidul612/arch-config/blob/main/tools/win11-vm/ds4gadget.py) creates a USB DualShock 4 (`054c:05c4`) from it with the kernel's `dummy_hcd` and `raw_gadget` modules. The DS4 is a real USB device on the host, so libvirt passes it to the VM like hardware, and Windows and Remote Play see a genuine wired DS4.

The descriptors and feature reports come from [ViGEmBus](https://github.com/nefarius/ViGEmBus)' `Ds4Pdo.cpp`. The script answers the control requests Remote Play sends at connect (feature reports `0x12`, `0xa3`, `0x02`, and the `0x14` set report), maps the Xbox pad's buttons and sticks from evdev, and logs every request; unknown ones are stalled and logged as `UNHANDLED`. The Xbox Guide button is the PS button.

Status: **working**. Verdict after real use (2026-11-21, started by hand): "the host side ds4 worked, i was playing the game using it for 6+ hours." Since then a user service runs it automatically, only while Remote Play is open; see below for what has been tested of that.

!!! note "Why not ViGEmBus in the guest"
    The obvious route is an Xbox pad in the VM plus ViGEmBus and a DS4 emulator. Remote Play ignores ViGEm's virtual DS4 (since v6 it sends control requests ViGEm doesn't answer), and Remote Play 9.x crashes on purpose when ViGEmBus is installed. See [Troubleshooting](troubleshooting.md#what-was-tried).

### Once: host prep

```bash
cd tools/win11-vm
sudo ./ds4-host-prep.sh          # udev rule for /dev/raw-gadget, loads the modules now
./ds4-modules-setup.sh           # asks for sudo; loads dummy_hcd + raw_gadget at every boot
```

[`ds4-host-prep.sh`](https://github.com/tahmidul612/arch-config/blob/main/tools/win11-vm/ds4-host-prep.sh) disables the old Xbox auto-attach udev rule if present (the pad must stay on the host), writes `/etc/udev/rules.d/70-raw-gadget.rules` so you own `/dev/raw-gadget`, and loads `dummy_hcd` and `raw_gadget`. [`ds4-modules-setup.sh`](https://github.com/tahmidul612/arch-config/blob/main/tools/win11-vm/ds4-modules-setup.sh) writes `/etc/modules-load.d/ds4-gadget.conf`. The gadget needs `python-evdev`, and your user must be able to read the pad's `/dev/input/event*` (here: member of `input`).

### The service: DS4 while Remote Play runs

[`ds4-vm.py`](https://github.com/tahmidul612/arch-config/blob/main/tools/win11-vm/ds4-vm.py), run by the user unit [`ds4-vm.service`](https://github.com/tahmidul612/arch-config/blob/main/tools/win11-vm/ds4-vm.service), asks the guest agent every 3 s whether `RemotePlay.exe` is running (`tasklist`). It talks to libvirt through `python-libvirt` over one open connection, so the poll costs almost nothing on the host (spawning `virsh` for every check used about 1% of a core):

- Remote Play running: it starts `ds4gadget.py` (restarted with back-off if it dies) and keeps exactly the current DS4 attached to the VM as a live USB hostdev. Stale entries from an earlier enumeration are detached, and it attaches again after a VM restart.
- Remote Play closed for 15 s, or the VM stopped: it detaches the DS4 and stops the gadget. Other games and apps never see a DS4; the Xbox pad stays with the host.
- Guest agent not answering (booting, busy): it keeps the current state.

Unplugging the Xbox pad is handled inside `ds4gadget.py`: it sends neutral input while the pad is gone and picks it up again when it is back, so Windows never sees the DS4 disappear.

```bash
mkdir -p ~/.local/lib/ds4-vm
cp tools/win11-vm/{ds4-vm.py,ds4gadget.py,ds4_report_desc.bin} ~/.local/lib/ds4-vm/
cp tools/win11-vm/ds4-vm.service ~/.config/systemd/user/
systemctl --user daemon-reload && systemctl --user enable --now ds4-vm
journalctl --user -u ds4-vm -f
```

Check: start Remote Play in the VM. Within a few seconds the journal shows the attach, then Remote Play's queries (`GET_REPORT feature 0x12`, `0xa3`, `0x02`) and a light-bar colour. Windows Device Manager shows "HID-compliant game controller" for `VID_054C&PID_05C4`. On the PS5, press PS and pick the user at "Who's using this controller?". Close Remote Play: after 15 s the journal says "Remote Play closed: stopping the virtual DS4".

Tested by the setup session, not yet in your own play: start and attach, Remote Play recognising it, detach when Remote Play closes, recovery when the hostdev is detached behind its back, and an emulator restart. Not tested: unplugging the Xbox pad during a session, a VM stop and start, and the first host reboot with the modules file.

`systemctl --user stop ds4-vm` stops it. `ds4gadget.py -v` logs every button press.

### Latency: Python vs Rust

The gadget is Python, so it was checked whether a Rust rewrite would cut input latency. [`ds4-bench/`](https://github.com/tahmidul612/arch-config/blob/main/tools/win11-vm/ds4-bench/README.md) holds a Rust port with the same descriptors and the same send loop (build a report, blocking write, sleep 4 ms), and a harness that presses a button on a fake Xbox pad (`uinput`) 2000 times at random moments. It times each press up to the kernel timestamp of the event on the DS4 that the host's `hid-playstation` driver creates for the gadget.

| Variant | Median | p95 | p99 | Max | Gadget CPU |
| --- | --- | --- | --- | --- | --- |
| Python (`ds4gadget.py`), 4 ms loop | 2.20 ms | 3.97 | 4.12 | 4.20 | 0.43% |
| Rust, 4 ms loop | 2.11 ms | 3.94 | 4.11 | 6.06 | 0.08% |
| Python, all 20 cores busy (`stress-ng`) | 2.27 ms | 4.04 | 4.20 | 5.82 | 0.51% |
| Rust, all 20 cores busy | 2.27 ms | 3.95 | 4.12 | 5.35 | 0.08% |
| Python, 1 ms loop | 0.61 ms | 1.10 | 1.16 | 1.21 | 0.84% |
| Rust, 1 ms loop | 0.62 ms | 1.09 | 1.15 | 1.22 | 0.15% |

Measured on 2026-11-21, no presses lost in any run. The two languages are within 0.1 ms of each other. Latency is spread evenly from 0 to about 4 ms, because a press waits for the next report, so the send interval sets it. A full CPU load didn't hurt Python either. Rust only saves CPU (0.1% against 0.4%), so the gadget stays in Python.

The 1 ms loop is not used. The numbers cover the host side only: in the VM, Windows polls the DS4 every 5 ms (the endpoint's `bInterval`), and a faster host loop doesn't change that. Remote Play's own sampling, the video stream and the trip to the PS5 add tens of milliseconds on top, so 1-2 ms would not be felt. Polling faster would also need a different `bInterval`, which is untested with Remote Play.

To repeat it, run `tools/win11-vm/ds4-bench/run-bench.sh` (about 5 minutes). It stops `ds4-vm.service` while it runs and starts it again afterwards.

### Manual fallback

Without the service, or to debug, with the Xbox pad plugged in. Stop the service first (`systemctl --user stop ds4-vm`) so the two don't fight over `/dev/raw-gadget`.

```bash
cd tools/win11-vm
python3 ds4gadget.py --log /tmp/ds4gadget.log &  # waits for the pad, then enumerates the DS4
lsusb -d 054c:05c4                               # the DS4 is on the host's USB

cat > /tmp/ds4-hostdev.xml <<'EOF'
<hostdev mode='subsystem' type='usb' managed='yes'>
  <source><vendor id='0x054c'/><product id='0x05c4'/></source>
</hostdev>
EOF
virsh -c qemu:///system attach-device win11 /tmp/ds4-hostdev.xml --live
```

The attach is `--live` only: after a VM restart or a gadget restart, run `detach-device` and `attach-device` again with the same file. `--no-pad` enumerates the DS4 without reading a controller, to test the USB side alone.

Known gap: rumble from the game is logged but not forwarded to the Xbox pad.

### Old: Xbox controller passed to the VM

Before the gadget, the Xbox pad (`045e:0b12`) was a USB hostdev of the VM, with a udev rule that re-attached it when replugged ([`xbox-vm-attach`](https://github.com/tahmidul612/arch-config/blob/main/tools/win11-vm/xbox-vm-attach) and its unit and rule). The replug attach worked in real use, but Remote Play ignores an Xbox pad, so it is **superseded**. `ds4-host-prep.sh` renames its rule to `.rules.disabled`; the script and unit can stay installed or be removed:

```bash
sudo rm /usr/local/bin/xbox-vm-attach /etc/systemd/system/xbox-vm-attach@.service /etc/udev/rules.d/99-xbox-vm-attach.rules.disabled
sudo systemctl daemon-reload
```

## Files

| File | Installed to | Purpose |
| --- | --- | --- |
| `tools/win11-vm/deskflow/Deskflow.host.conf`, `deskflow-server.conf` | `~/.config/Deskflow/` | Host server settings and layout |
| `tools/win11-vm/deskflow/Deskflow.guest.conf` | guest `C:\ProgramData\Deskflow\Deskflow.conf` | Guest client settings |
| `tools/win11-vm/deskflow/make-certs.sh` | writes `host/` and `guest/` TLS files | Pinned certificates |
| `tools/win11-vm/ds4-host-prep.sh` | `/etc/udev/rules.d/70-raw-gadget.rules` | `/dev/raw-gadget` access, modules now |
| `tools/win11-vm/ds4-modules-setup.sh` | `/etc/modules-load.d/ds4-gadget.conf` | Modules at boot |
| `tools/win11-vm/ds4-vm.py`, `ds4gadget.py`, `ds4_report_desc.bin` | `~/.local/lib/ds4-vm/` | Supervisor and the virtual DS4 |
| `tools/win11-vm/ds4-vm.service` | `~/.config/systemd/user/` | Runs the supervisor |
| `tools/win11-vm/ds4-bench/` | run from the repo | Latency benchmark and the Rust port it compares against |
| `tools/win11-vm/looking-glass/vm-display-deskflow.desktop` | `~/.config/autostart/` | Deskflow follows the display mode at login |
| `tools/win11-vm/xbox-vm-attach*`, `99-xbox-vm-attach.rules` | `/usr/local/bin/`, `/etc/systemd/system/`, `/etc/udev/rules.d/` | Superseded Xbox hot-plug attach |
