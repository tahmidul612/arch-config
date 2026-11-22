# Display

The VM's picture reaches the LG HDR 4K monitor in one of two ways, and `vm-display` switches between them ([Switching](switching.md)). In lg mode Windows runs at 1920x1080@60 (100%) and the Looking Glass client upscales it to the 4K panel with AMD FSR 1 ([why](#resolution-and-upscaling)); in hdmi mode Windows drives the LG directly at 3840x2160@60 (150%).

| | **lg** (default) | **hdmi** |
| --- | --- | --- |
| What you see | The [Looking Glass](https://looking-glass.io) client, full screen on the host's DP-1, which shows a virtual monitor inside Windows | Windows directly, on the LG's HDMI input wired to the iGPU |
| LG input | DisplayPort (host) | HDMI (VM), switched over DDC/CI |
| Keyboard and mouse | Looking Glass client | Deskflow ([Input](input.md#deskflow-keyboard-and-mouse)) |
| Host recording (`vm-rec`) | Records what you see | Records the virtual monitor, which in this mode is Windows' *second* screen |
| Guest resolution | 1920x1080 @100%, upscaled by the client (FSR 1) | 3840x2160 @150% |
| Status | Default. Switching to it confirmed clean ("LG client, DP, clean") | Alternative. Confirmed clean at 4K ("Windows 4K, clean") |

Default was chosen on 2026-11-21: "Looking Glass on LG seems like the better choice with less complexity. But keep the other option (HDMI with the switching), and make it easy to switch between the two methods."

Not verified yet: a real Remote Play session watched through the Looking Glass client (the measurements below use a simulated stream), and how desktop text at 1080p + FSR compares with hdmi mode's native 4K.

## Looking Glass

Looking Glass copies the guest's frames into a shared memory file, `/dev/shm/looking-glass`, and the host client draws them. This uses B8-rc1 with its Indirect Display Driver (IDD): a virtual monitor in Windows, so the iGPU needs no monitor or HDMI dummy plug in lg mode. The stable B7 has no IDD.

The IDD renders on the passed-through Intel UHD 770 (its log says "Selected render adapter Intel(R) UHD Graphics 770").

=== "Host"

    1. Client, OBS plugin and the shared-memory file:

        ```bash
        tools/win11-vm/looking-glass/lg-host-setup.sh      # as your user; paru asks for sudo
        ```

        [`lg-host-setup.sh`](https://github.com/tahmidul612/arch-config/blob/main/tools/win11-vm/looking-glass/lg-host-setup.sh) installs `looking-glass-rc` and `obs-plugin-looking-glass-rc` from the AUR and writes [`/etc/tmpfiles.d/10-looking-glass.conf`](https://github.com/tahmidul612/arch-config/blob/main/tools/win11-vm/looking-glass/10-looking-glass.conf): `f /dev/shm/looking-glass 0660 <USER> kvm -`. QEMU runs as `libvirt-qemu`, which is in `kvm`, so both sides can read and write it. The kvmfr kernel module is an alternative to the `/dev/shm` file; it was not used.

    2. Client settings, [`client.ini`](https://github.com/tahmidul612/arch-config/blob/main/tools/win11-vm/looking-glass/client.ini) to `~/.config/looking-glass/client.ini`:

        - `setGuestRes=no` keeps the guest at its fixed mode (1080p in lg mode) instead of resizing it to the client window.
        - `[eglFilter] ffxFSR=yes`, `ffxFSRSharpness=1.0`: FSR 1 upscales the 1080p guest to 4K. Without it the client's default (`egl:scale=0`, auto) uses nearest neighbour for an exact 2x, which is blocky. See [Resolution and upscaling](#resolution-and-upscaling).
        - `[lgmp] shmDevice=/dev/shm/looking-glass` (B8 renamed `app:shmFile`; the old name still works but logs a warning).
        - `[spice] audio=no`: the client's own audio device would become Windows' default output and bypass the [PipeWire path](audio.md).
        - `escapeKey=KEY_RIGHTCTRL`, because the MX Keys keyboard has no Scroll Lock (the default key). Confirmed: "works perfectly".

    3. `vm-display` and its KWin script, linked into `~/.local/bin`:

        ```bash
        ln -s "$PWD/tools/win11-vm/looking-glass/vm-display" ~/.local/bin/
        ```

        `vm-display lg` and `vm-display client` start the client as a transient user unit `lg-client` and run [`lg-client-to-output.js`](https://github.com/tahmidul612/arch-config/blob/main/tools/win11-vm/looking-glass/lg-client-to-output.js) through KWin's scripting D-Bus API, which moves the window full screen onto the output `DP-1` (edit `OUTPUT_NAME` and `LG_OUT` for yours). Nothing starts the client at login or VM boot; run `vm-display lg` after starting the VM.

=== "VM XML"

    ```xml
    <memballoon model='none'/>
    <shmem name='looking-glass'>
      <model type='ivshmem-plain'/>
      <size unit='M'>128</size>
    </shmem>
    ```

    128 MiB is needed for 3840x2160 (hdmi mode's extended virtual monitor, and the 4K test runs): with 64 MiB, the setup session found, the guest offers no 4K modes. After changing the size, empty the file before the next start so QEMU regrows it while the tmpfiles permissions stay (also from the setup session):

    ```bash
    truncate -s 0 /dev/shm/looking-glass
    ```

    Then fully stop and start the VM ([how](vm-definition.md#restarting-so-changes-apply)).

=== "Guest"

    1. Download the IDD installer ([looking-glass.io/artifact/B8-rc1/idd](https://looking-glass.io/artifact/B8-rc1/idd); the zip's sha256 is `65dd2c3d873b2a3cd7adf11d0e0f83ade18f7968711abbfb85a408d514ddb50f`) and copy `looking-glass-idd-setup.exe` into the guest. Its signature is valid, `CN=HostFission`.
    2. Run `looking-glass-idd-setup.exe /S /ivshmem` as administrator. It installs the IVSHMEM driver, but in a silent install the IDD itself fails with `SetupCopyOEMInfW: 0xe0000242`: Windows cannot ask whether to trust the publisher. Trust the publisher's certificate, then install the IDD:

        ```powershell
        $d = 'C:\Program Files\Looking Glass (IDD)'
        $c = (Get-AuthenticodeSignature "$d\lgidd.cat").SignerCertificate    # thumbprint 57476AACDE410C05A63E9FEC8B97938605B9D8F5
        $st = New-Object Security.Cryptography.X509Certificates.X509Store('TrustedPublisher','LocalMachine')
        $st.Open('ReadWrite'); $st.Add($c); $st.Close()
        Set-Location $d; .\LGIddInstall.exe install LGIdd LGInput
        Get-Service LGIddHelper          # Running, Automatic
        ```

    3. Let other monitors stay active next to the virtual one. By default the IDD helper enforces "Looking Glass as the only display" every few seconds, which would switch off the iGPU's HDMI output that hdmi mode and [input switching](switching.md#lg-input-switching) need:

        ```powershell
        New-ItemProperty 'HKLM:\SOFTWARE\LookingGlass\IDD' -Name ExclusiveMonitor -PropertyType DWord -Value 0 -Force
        Restart-Service LGIddHelper
        ```

    4. Copy [`display-layout.ps1`](https://github.com/tahmidul612/arch-config/blob/main/tools/win11-vm/guest/display-layout.ps1) to `C:\Tools\display\` and create its tasks with [`register-tasks.ps1`](https://github.com/tahmidul612/arch-config/blob/main/tools/win11-vm/guest/register-tasks.ps1) `-User <WINDOWS_USER>` (elevated). The layouts:

        | `-Layout` | Result |
        | --- | --- |
        | `lgonly` | Looking Glass monitor only (lg mode); the iGPU's HDMI output is off |
        | `hdmi` | LG over HDMI primary at (0,0), Looking Glass monitor extended at (3840,0) |
        | `extended` | Looking Glass primary at (0,0), LG over HDMI at (3840,0) |
        | `status` | Prints the active layout |

        Defaults depend on the layout: `lgonly` is 1920x1080 at 100%, `hdmi` and `extended` are 3840x2160 at 150% (all 60 Hz); `-Width`, `-Height`, `-Hz` and `-Scale` override them. Until 2026-11-21 every layout defaulted to 3840x2160 at 150%. The script switches monitors on with `SDC_TOPOLOGY_EXTEND` and then edits the active configuration; building one from all paths returns error 87. If the result has no active Looking Glass monitor, it applies `lgonly` again. Task `Display-lgonly-AtLogon` puts Windows back in `lgonly` 15 seconds after each login. Log: `C:\Tools\display\display-layout.log`.

Check:

```bash
vm-display status
# mode:   lg
# input:  dp   (lg-input auto: paused)
# client: running
# deskflow: stopped
# VM:     ... Looking Glass: 1920x1080 at (0,0) 60.00Hz 100%
```

### Client keys

The escape key here is Right Ctrl. Bindings as listed by the setup session; Looking Glass's own docs have the full list.

| Keys | Action |
| --- | --- |
| tap ++right-ctrl++ | Capture or release keyboard and mouse |
| ++right-ctrl+f++ | Full screen / window |
| ++right-ctrl+q++ | Quit (reopen with `vm-display client`) |
| ++right-ctrl+d++ | FPS display |
| ++right-ctrl+o++ | Overlay (EGL filters; FSR is on through `client.ini`) |
| ++right-ctrl+shift+r++ | Guest display recovery |
| ++right-ctrl+equal++ | Resize the guest to the window. Avoid it: the guest should stay at its fixed 1080p mode. `vm-display lg` sets it again |

The client has no minimise key; use KDE's. `looking-glass-client input:escapeKey=help` lists other key names.

## Resolution and upscaling

Remote Play streams at most 1080p, so a 4K Windows desktop only upscales that stream inside the guest. Since 2026-11-21 lg mode runs Windows at 1080p and lets the host's Looking Glass client upscale on the RTX 3080 with AMD FidelityFX FSR 1. It was measured against the old 4K desktop with the test suite in [`tools/win11-vm/upscale-bench/`](https://github.com/tahmidul612/arch-config/blob/main/tools/win11-vm/upscale-bench/README.md).

How it was tested:

- **The stream.** The playstation.com Remote Play pages give no resolution, codec or bitrate; the only figures are "at least 5 Mbps, 15 Mbps recommended". A Sony support article says PS5 picks up to 1080p automatically (seen only as a search summary; the page refuses automated fetches). chiaki-ng's source sets its 1080p preset to 15 Mbps, and its release notes reportedly say that matches the official app's default (also from a search summary); PS5 can stream H.264 or HEVC. The test downscales 4K GTA VI footage (a PS5 Pro walkthrough and Rockstar's Trailer 2, from YouTube) to 1080p60 and encodes it like a game stream: NVENC, CBR 15 Mbps, no B-frames, low latency.
- **The frames.** 8 scenes of 8 consecutive frames each (water, foliage, the map screen, a dark bar, docks, an interior, a close-up and a chain-link fence), shown 1:1 in the guest and captured from DP-1 at native 3840x2160.
- **Path checks.** With the guest at 4K, the capture equalled the shown frames bit for bit; with the guest at 1080p and nearest scaling, every 2x2 block matched its pixel exactly. So any difference comes from the scaler.
- **Scoring.** Each setup is compared with the 4K source frames by VMAF 4K, VMAF NEG (which doesn't reward sharpening), PSNR and SSIM. The 4K desktop case is modelled offline with lanczos and bicubic, because Remote Play's own scaler is undocumented.

Results for the 15 Mbps HEVC stream (H.264 at 15 Mbps scored the same):

| Setup | VMAF 4K | VMAF NEG | PSNR-Y |
| --- | --- | --- | --- |
| Guest 1080p, client FSR 1 + CAS 0.5 | 94.4 | 78.6 | 36.9 |
| **Guest 1080p, client FSR 1 (sharpness 1.0)** | 91.4 | **80.9** | 39.2 |
| Guest 1080p, client FSR 1 (sharpness 0.5) | 89.1 | 80.1 | 40.2 |
| Guest 4K, Remote Play upscales (lanczos stand-in) | 88.6 | 80.2 | 40.4 |
| Guest 4K, Remote Play upscales (bicubic stand-in) | 87.6 | 79.0 | 40.2 |
| Guest 1080p, client default (nearest at 2x) | 86.6 | 78.1 | 37.8 |
| Guest 1080p, client linear | 79.5 | 69.9 | 39.1 |
| Uncompressed 1080p, lanczos (no stream compression) | 93.7 | 87.6 | 42.2 |

- FSR 1 alone is the best on VMAF NEG and level with or ahead of the 4K desktop. Adding CAS raises plain VMAF but lowers NEG and PSNR: oversharpening.
- The stream's compression costs more than any scaler: about 5 VMAF points.
- FSR sharpens rather than recovering detail (on uncompressed frames it scores NEG 86.6 against lanczos' 87.6), and on a compressed stream that sharpening helps.

Cost and smoothness, the 1080p60 clip playing in the guest:

| Guest | Guest iGPU 3D | Client CPU | RTX 3080 | Client UPS (frames received/s) |
| --- | --- | --- | --- | --- |
| 1080p, FSR 1 | 41% | 6.5% of a core | 37%, 99.5 W | 59.6 (lowest 57.9) |
| 4K, Windows upscales | 79% | 19.7% of a core | 37%, 96.5 W | 58.6 (lowest 55.9) |

Both play at close to 60 fps (host OBS on the shared memory read about 53 new frames per second for both, the rest being clock beat between OBS and the guest); the longest freeze seen in either was 50 ms. One 15 s run per setup, with a WPF media player in the guest, not Remote Play.

Check that FSR is on: press ++right-ctrl+o++; under AMD FidelityFX FSR, "Enabled" is ticked. The first frame after the client starts is drawn before the filter is set up, so a desktop that hasn't changed since then looks blocky until something in Windows redraws (open or move a window; mouse movement alone doesn't count, the client draws the cursor itself).

## LG on HDMI

The motherboard's HDMI port (the iGPU) is wired to the LG's HDMI input; the LG's DisplayPort goes to the host. In hdmi mode Windows drives the LG directly, so there is no client in between.

Requirements on top of the Looking Glass setup above (the virtual monitor stays, extended to the right):

- `ExclusiveMonitor=0` (above), so the HDMI output can be active.
- The [LG input switching](switching.md#lg-input-switching) tools, because only DDC/CI commands, sent from the right side, switch this LG cleanly.
- Deskflow for keyboard and mouse, which `vm-display hdmi` starts.

```bash
vm-display hdmi
vm-display status     # mode: hdmi, input: hdmi, client: stopped, deskflow: running
```

While the LG shows the VM, the `lg-input` watcher turns the host's DP-1 off and makes the other monitor (DP-2) primary, so no windows land on a screen you can't see. It turns DP-1 back on when you switch back or the VM stops.

## Fallback: SPICE console

`virtio-vga` and SPICE stay in the XML. Open the VM in virt-manager to see the boot screens and the firmware, or when the iGPU output and Looking Glass are both unavailable. Windows lists it as a third monitor ("QEMU Monitor"); the layouts above leave it inactive.
