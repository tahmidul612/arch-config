# Input

Two separate things: the host's keyboard and mouse reaching Windows (Deskflow), and a controller Remote Play accepts (a virtual DualShock 4 made on the host).

## Deskflow: keyboard and mouse

[Deskflow](https://github.com/deskflow/deskflow) shares the host's keyboard, mouse and clipboard with the VM, as if the VM's screen sat next to the host monitor. The host is the server and the VM the client, over libvirt's NAT on TCP 24800. Both sides use pinned TLS certificates, so neither shows a trust prompt.

It was picked after comparing the open-source options (2026-11-21): Deskflow 1.27 (active, GPL-2.0, official Windows MSI and an Arch package), Lan Mouse 0.11 (younger, weak clipboard; the fallback if Deskflow misbehaves on Wayland, never tried), Input Leap (archived) and Barrier (dead, X11 only).

Status: **working**. Confirmed in real use (2026-11-21): mouse crossing, keyboard and clipboard.

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

=== "Start"

    Start Deskflow through its desktop entry, not as a bare command; otherwise the desktop portal refuses it with "An app id is required".

    ```bash
    kioclient exec /usr/share/applications/org.deskflow.deskflow.desktop
    cp /usr/share/applications/org.deskflow.deskflow.desktop ~/.config/autostart/
    ss -ltn | grep 24800        # listening on 0.0.0.0:24800
    ```

    The first time the cursor crosses over, KDE asks to allow input capture.

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

Status: **working**. Verdict after real use (2026-11-21): "the host side ds4 worked, i was playing the game using it for 6+ hours." There is no service yet; start it by hand as below.

!!! note "Why not ViGEmBus in the guest"
    The obvious route is an Xbox pad in the VM plus ViGEmBus and a DS4 emulator. Remote Play ignores ViGEm's virtual DS4 (since v6 it sends control requests ViGEm doesn't answer), and Remote Play 9.x crashes on purpose when ViGEmBus is installed. See [Troubleshooting](troubleshooting.md#what-was-tried).

### Once: host prep

```bash
cd tools/win11-vm
sudo ./ds4-host-prep.sh
```

[`ds4-host-prep.sh`](https://github.com/tahmidul612/arch-config/blob/main/tools/win11-vm/ds4-host-prep.sh) disables the old Xbox auto-attach udev rule if present (the pad must stay on the host), writes `/etc/udev/rules.d/70-raw-gadget.rules` so you own `/dev/raw-gadget`, and loads `dummy_hcd` and `raw_gadget`. It needs `python-evdev`, and your user must be able to read the pad's `/dev/input/event*` (here: member of `input`).

The modules are not loaded at boot. Either rerun the script after a reboot, or persist them:

```bash
printf 'dummy_hcd\nraw_gadget\n' | sudo tee /etc/modules-load.d/ds4gadget.conf
```

(Not done on this machine yet.)

### Each session: start and attach

Plug in the Xbox controller, then:

```bash
cd tools/win11-vm
./ds4gadget.py --log /tmp/ds4gadget.log &       # waits for the pad, then enumerates the DS4
lsusb -d 054c:05c4                               # the DS4 is on the host's USB

cat > /tmp/ds4-hostdev.xml <<'EOF'
<hostdev mode='subsystem' type='usb' managed='yes'>
  <source><vendor id='0x054c'/><product id='0x05c4'/></source>
</hostdev>
EOF
virsh -c qemu:///system attach-device win11 /tmp/ds4-hostdev.xml --live
```

Check: Windows Device Manager shows "HID-compliant game controller" for `VID_054C&PID_05C4`, and when Remote Play connects the log shows `GET_REPORT 0x12`, `0xa3`, `0x02`, then `SET_REPORT 0x14` and a light-bar colour. On the PS5, press PS and pick the user at "Who's using this controller?".

The attach is `--live` only. After the VM restarts, or if the gadget restarts and re-enumerates, detach and attach again:

```bash
virsh -c qemu:///system detach-device win11 /tmp/ds4-hostdev.xml --live
virsh -c qemu:///system attach-device win11 /tmp/ds4-hostdev.xml --live
```

Stop: kill `ds4gadget.py`, then detach as above.

`--no-pad` enumerates the DS4 without reading a controller, which is useful to test the USB side alone.

Known gaps: no systemd unit or VM start hook; rumble from the game is logged but not forwarded to the Xbox pad. Once the Xbox pad dropped off the host for a few seconds (`[Errno 19] No such device`); the script waited and picked it up again.

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
| `tools/win11-vm/ds4-host-prep.sh` | `/etc/udev/rules.d/70-raw-gadget.rules` | Modules and `/dev/raw-gadget` access |
| `tools/win11-vm/ds4gadget.py`, `ds4_report_desc.bin` | run in place | The virtual DS4 |
| `tools/win11-vm/xbox-vm-attach*`, `99-xbox-vm-attach.rules` | `/usr/local/bin/`, `/etc/systemd/system/`, `/etc/udev/rules.d/` | Superseded Xbox hot-plug attach |
