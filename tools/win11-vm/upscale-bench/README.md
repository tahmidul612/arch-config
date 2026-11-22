# upscale-bench

Tests that decided how lg mode shows the VM: a 1080p guest upscaled by the Looking Glass client (FSR 1, CAS, linear, nearest) against a 4K guest, for a simulated PS Remote Play stream, plus its cost and frame rate. The write-up and results are in [`docs/Gaming/Windows VM/display.md`](../../../docs/Gaming/Windows%20VM/display.md#resolution-and-upscaling).

## Files

| File | Installs to | Setup | Status |
| --- | --- | --- | --- |
| `prep.py` | run from here | offline: Remote Play stream simulation, test frames, offline upscales | Benchmark |
| `capture.py` | run from here; copies `viewer.ps1` and frames to guest `C:\Tools\upscale\` | live: shows frames in the guest, captures DP-1 per client setting; `--only COND,...`, `--skip-4k` | Benchmark |
| `metrics.py` | run from here | VMAF 4K, VMAF NEG, PSNR, SSIM against the 4K source; writes `report.md` and crops | Benchmark |
| `perf.py` | run from here; copies `player.ps1` and the clip | guest iGPU and CPU, client CPU, RTX load while the clip plays | Benchmark |
| `fps.py`, `barcode.py` | run from here | frame rate: barcoded clip, host OBS on the shared memory plus the client's FPS/UPS overlay (OCR) | Benchmark |
| `viewer.ps1`, `player.ps1` | guest `C:\Tools\upscale\` (copied by the scripts) | started by temporary user-session tasks `UT-*`, removed afterwards | Benchmark |
| `results-2026-11-21/` | | the run in the docs: `report.md` (tables, per scene), `results.json`, `perf.json`, `fps.json`, `prep.json` (encoder settings, real bitrates) | Reference |

Footage, frames, captures and crops stay out of the repo; they go to `$UPSCALE_DIR` (default `~/Videos/vm-upscale-test`): `src/` for the clips, `work/` for everything generated (about 7 GB).

## Running it

Needs: `yt-dlp`, `ffmpeg` with `libvmaf` and NVENC, `python-numpy`, `python-pillow`, `python-libvirt`, `tesseract` (for `fps.py`), Spectacle, OBS with the VM-Capture profile ([Recording](../../../docs/Gaming/Windows%20VM/recording.md)) and the VM running with the guest agent. The live scripts take over DP-1 (about 2 minutes per client setting): keep other windows off it.

1. Footage. The run used sections of [a GTA VI PS5 Pro walkthrough](https://www.youtube.com/watch?v=YDNs_sPEcJc) (format 628, 4K60 VP9) and [Trailer 2](https://www.youtube.com/watch?v=VQRLujxTm3c) (format 625):

    ```bash
    mkdir -p ~/Videos/vm-upscale-test/src && cd ~/Videos/vm-upscale-test/src
    for t in 1080 3120 5700 8400 11100 13800; do
      yt-dlp -f 628 --download-sections "*$t-$((t+12))" -o "walk-$t.%(ext)s" https://www.youtube.com/watch?v=YDNs_sPEcJc
    done
    yt-dlp -f 625 --download-sections "*00:01:00-00:01:20" -o "trailer2-0100.%(ext)s" https://www.youtube.com/watch?v=VQRLujxTm3c
    ```

2. `./prep.py`: encodes each clip at 1080p60 like a game stream (NVENC, CBR, no B-frames; HEVC 15 Mbps, H.264 15 Mbps, HEVC 8 Mbps, and uncompressed) and extracts 8 scenes of 8 consecutive frames. Change `SCENES` if you use other footage.
3. `./capture.py` runs a 4K path check, switches the guest to 1080p, then every client setting in `CONDS`. It aborts if the nearest-neighbour calibration is not pixel-exact, retries a capture that shows the wrong frame, and restarts the guest's Looking Glass display device if it stops sending frames. At the end the guest goes back to `Display-lgonly` and the normal client is restarted and checked.
4. `./metrics.py` writes `work/report.md`, `work/results.json` and side-by-side crops in `work/crops/`.
5. `./perf.py` and `./fps.py` for cost and frame rate. `prep.py` also makes their clips: `work/play-hevc15.mp4` / `play-h264_15.mp4` (perf.py; the guest had no HEVC decoder, so H.264 played) and `work/play-barcode.mp4` (fps.py: a 12-bit frame-number barcode in the top-left strip, decoded by `barcode.py`).

## Differences from the copies that were run

The run happened while lg mode's default was still 4K. Afterwards `lgonly` defaults to 1080p, so the 4K phases here switch with a new task, `UT-Res4K`, and the restore goes to `Display-lgonly` (now 1080p). Of these edits only the task registration was tested. `perf.py` no longer reports a guest `looking-glass-host` CPU figure (with the IDD there is no such process; it read 0). The data paths moved to `$UPSCALE_DIR` and the repo-relative KWin script and `obsws.py`.

## What the checks caught

- Two Looking Glass clients at once: `systemctl stop a b` stops nothing when one unit is missing, and the client renames its process title, so `pkill -f` misses it. `capture.py` stops units one at a time, kills leftovers by `comm` name and refuses to run unless only its own client is up.
- A new client gets no frame until the guest redraws, so it shows its splash: the scripts flip the viewer to another image first.
- The 30 fps game in a 60 fps upload has near-duplicate frames: the stale-frame check treats frames within 40 dB as equivalent, with a 25 dB floor so a splash screen never passes.
- OBS samples the shared memory on its own 60 Hz clock: about 10% repeated frames paired with skipped ones is clock beat, not dropped frames (the video still advances 60 frames per second).
