# Windows guest

Windows 11 Pro (build 26100) with the QEMU guest agent from the virtio-win ISO. Everything on this page was done from the host through the agent. Doing it by hand inside Windows works the same way.

## Running commands in the guest

[`guest-ps.py`](https://github.com/tahmidul612/arch-config/blob/main/tools/win11-vm/guest-ps.py) sends a PowerShell script through `guest-exec` and prints its output and exit code.

```bash
cd tools/win11-vm
./guest-ps.py 120 <<'EOF'
Get-CimInstance Win32_VideoController | Select Name, DriverVersion, Status
EOF
```

What to know about it:

- The script runs as **SYSTEM in session 0**. It cannot see the desktop: no windows, no screenshots, and XInput shows no controllers from there. For anything that needs the logged-in desktop, register a scheduled task with an `Interactive` principal for the Windows user and start it (the `IdleShutdown` task below is an example).
- Output can contain PowerShell CLIXML progress records; filter lines starting with `#< CLIXML` and `<Objs`.
- Large scripts fail with "Unable to encode message payload". Upload the `.ps1` with `qga-push.py` and run the file instead.
- In Windows PowerShell 5.1, a hex literal like `0x80000000` is a negative Int32. Write `[uint32]2147483648`.

### Copying files

| Direction | Tool | Chunk size | Measured |
| --- | --- | --- | --- |
| Host → guest | [`qga-push.py`](https://github.com/tahmidul612/arch-config/blob/main/tools/win11-vm/qga-push.py) `<host file> <guest path>` | 64 KiB, libvirt Python bindings | ~35 MB/s |
| Guest → host | [`qga-pull.py`](https://github.com/tahmidul612/arch-config/blob/main/tools/win11-vm/qga-pull.py) `<guest path> <host file>` | 1 MiB | ~22 MB/s |

Larger chunks fail. A 1 MiB write hangs the Windows agent and leaks the file handle, which keeps that file name locked until the guest reboots. 4 MiB exceeds libvirt's RPC limit, and 16 MiB reads time out. Check the hash on both sides after a big copy (`sha256sum` and `Get-FileHash`).

## Intel graphics driver

Once the [iGPU is in the VM](vm-definition.md#igpu-passthrough), Windows shows it as "Video Controller" with no driver. Windows Update offers *Intel Corporation Display Driver Update (32.0.101.7088)*.

1. Install it from Windows Update. Through the WU COM API this reported `0x80240016`; the cause was never found. The downloaded package was then installed with:

    ```powershell
    pnputil /add-driver C:\Windows\SoftwareDistribution\Download\Install\iigd_dch.inf /install
    ```

    pnputil answered "Already exists in the system", so it is unclear which of the two actually installed it.

2. Check: `Get-PnpDevice -Class Display` lists **Intel(R) UHD Graphics 770** with status OK. There is no Code 43.
3. **Fully stop and start the VM** ([how](vm-definition.md#restarting-so-changes-apply)). Until then Windows does not detect the monitor on the iGPU's HDMI port, even with the driver loaded. After the restart it showed up as "LG HDR 4K" on HDMI.

## PS Remote Play 8.5, patched

Use **8.5.0.08070**, patched to report itself as 9.5. Remote Play 9.5 crashes on launch in this VM (`0xc0000005`, see [Troubleshooting](troubleshooting.md#remote-play-closes-right-after-it-opens)). Sony has replaced the live 8.5 MSI with a 1-byte file, so it comes from the Internet Archive. It is still Sony-signed, which you check before installing.

1. Download the MSI and check the signature:

    ```powershell
    $d = 'C:\Users\Public\Downloads\psrp-setup'; New-Item -ItemType Directory -Force $d | Out-Null
    $m = "$d\RemotePlayInstaller_8.5.0.08070_x64.msi"
    Invoke-WebRequest 'https://web.archive.org/web/20260320131233id_/https://remoteplay.dl.playstation.net/remoteplay/module/win/RemotePlayInstaller_8.5.0.08070_x64.msi' -OutFile $m -UseBasicParsing
    $s = Get-AuthenticodeSignature $m; $s.Status; $s.SignerCertificate.Subject   # Valid, Sony Interactive Entertainment
    ```

    Stop if it is not `Valid` and signed by Sony. The file is 19,881,984 bytes.

2. Uninstall any other Remote Play version, then install:

    ```powershell
    Start-Process msiexec -ArgumentList "/i `"$m`" /qn /norestart" -Wait
    ```

3. Download the [xeropresence/remoteplay-version-patcher v1.0.4](https://github.com/xeropresence/remoteplay-version-patcher/releases/tag/v1.0.4) and check its hash. It is unsigned.

    ```powershell
    $p = "$d\remoteplay-version-patcher.exe"
    Invoke-WebRequest 'https://github.com/xeropresence/remoteplay-version-patcher/releases/download/v1.0.4/remoteplay-version-patcher-v1.0.4.exe' -OutFile $p -UseBasicParsing
    (Get-FileHash $p).Hash -eq '97A2483FB56EBDF632F274E27E69E72BFF8F0C9D7B2564D80F34ED03E0CD4A38'
    ```

4. Copy `C:\Program Files (x86)\Sony\PS Remote Play\RemotePlay.exe` somewhere safe, then run the patcher as administrator. It patches the installed exe and waits for a key press. Run without a console, it crashes at that prompt, but by then the patch is already applied.
5. Check: `(Get-Item 'C:\Program Files (x86)\Sony\PS Remote Play\RemotePlay.exe').VersionInfo.FileVersion` is `9.5.0.8040`. The exe no longer carries Sony's signature, which is expected.

Sign in with the PSN account and connect to the console over the internet as usual. No hosts-file block of Sony's update servers was added; it is only worth adding if the patched app starts losing its pairing.

!!! tip "Connecting"
    Close the phone's Remote Play session first; the console accepts one session at a time. Repeatedly connecting and disconnecting the remote console can leave it in a bad state, so make each connection count.

### Game Bar and the PS button

The PS button on the [virtual DualShock 4](input.md#virtual-dualshock-4) is the Xbox/Guide button, and Game Bar grabs it. It was turned off for the Windows user:

```powershell
reg add HKCU\Software\Microsoft\GameBar /v UseNexusForGameBarEnabled /t REG_DWORD /d 0 /f
```

Run this as the Windows user (through `guest-ps.py`, write it under `HKU\<SID>` instead). Whether it changed anything was never checked.

## Idle shutdown

The VM shuts itself down after an hour of nobody using it. [`idle-shutdown.ps1`](https://github.com/tahmidul612/arch-config/blob/main/tools/win11-vm/guest/idle-shutdown.ps1) checks once a minute. "Idle" means both conditions hold for 60 minutes:

- no keyboard or mouse input (`GetLastInputInfo`, which counts Deskflow and Looking Glass input), and
- network receive below 125 KB/s. A Remote Play stream keeps the NIC well above that, so controller-only play counts as activity.

It then runs `shutdown /s /t 300` with a warning. Cancel with `shutdown /a`; it then waits another full hour. Create `C:\Tools\idle-shutdown\inhibit` to pause it. It logs to `C:\Tools\idle-shutdown\idle-shutdown.log`.

Install it as the logged-in user (replace `<WINDOWS_USER>`):

```powershell
New-Item -ItemType Directory -Force C:\Tools\idle-shutdown | Out-Null
# copy idle-shutdown.ps1 there (qga-push.py), then:
$a = New-ScheduledTaskAction -Execute powershell.exe -Argument '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File C:\Tools\idle-shutdown\idle-shutdown.ps1 -IdleMinutes 60'
$t = New-ScheduledTaskTrigger -AtLogOn -User <WINDOWS_USER>
$s = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName IdleShutdown -Action $a -Trigger $t -Settings $s -Principal (New-ScheduledTaskPrincipal -UserId <WINDOWS_USER> -LogonType Interactive) -Force
Start-ScheduledTask IdleShutdown
```

Check: `Get-ScheduledTask IdleShutdown` is `Running`, and the log has a `start:` line and a `status:` line every 15 minutes. Change the timeout by editing `-IdleMinutes` in the task.

!!! note "Why not Windows' own idle trigger"
    The VM had a task `ShutdownOnInactivity` using Task Scheduler's idle trigger. Windows 8 and later ignore the trigger's idle duration, so it fired a few minutes after boot, and it did not count controller play as activity. It is disabled. Whether the new task behaves right over days of real use has not been observed yet.

The VM has no sleep or hibernate, so Windows' own sleep timer does nothing.
