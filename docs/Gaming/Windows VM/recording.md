# Recording

Gameplay is recorded on the **host**: OBS reads the Looking Glass shared memory and the VM's audio sink, and encodes on the NVIDIA card. That leaves the UHD 770 free for decoding the Remote Play stream. The iGPU could not decode, drive the desktop and encode at the same time.

| | Host OBS via Looking Glass (default) | Guest OBS, Quick Sync TU7 (alternative) |
| --- | --- | --- |
| Encoder | NVENC HEVC on the RTX 3080 | Intel Quick Sync HEVC on the UHD 770 |
| Guest iGPU load (3D engine) | 47% at 1440p, 67% at 4K | 79% at 1440p |
| Frames skipped in the benchmark | 1 of 7212 (1440p); 0 of 900 (4K) | 0 of 7295 (1440p) |
| Status | Default. Benchmarks only; not yet used on real gameplay | Benchmark only; left installed |

Benchmarks: Edge in the guest looping a 1080p60 25 Mbps test clip for 2 minutes, which is close to what Remote Play shows. "Unique frames" was checked with ffmpeg's `mpdecimate` on the result. At 4K the host recording had 899-900 of 900 unique frames per 15 s window.

## Host OBS (default)

1. Install OBS, the Looking Glass plugin ([Display](display.md#looking-glass) installs `obs-plugin-looking-glass-rc`), and `python-websockets` for `obsws.py`.
2. Copy the profile and scene collection:

    ```bash
    T=tools/win11-vm/looking-glass/obs
    install -Dm644 $T/basic.ini $T/recordEncoder.json -t ~/.config/obs-studio/basic/profiles/VM-Capture/
    install -Dm644 $T/VM-Capture.json ~/.config/obs-studio/basic/scenes/VM-Capture.json
    sed -i "s#<RECORDINGS_DIR>#$HOME/Videos/win11-vm#" ~/.config/obs-studio/basic/profiles/VM-Capture/basic.ini
    ```

    | Setting | Value |
    | --- | --- |
    | Canvas | 3840x2160 (the guest's mode) |
    | Output | 1920x1080 at 60 fps, Lanczos. Remote Play itself streams at most 1080p, so a 4K file would only be bigger |
    | Video | NVENC HEVC (`obs_nvenc_hevc_tex`), CQP 18, preset p5, tune hq, 2 B-frames, keyframe every 2 s |
    | Audio | AAC 320 kb/s, one track "VM audio" |
    | File | mkv in `<RECORDINGS_DIR>` |
    | Sources | "VM display": `looking-glass-obs` on `/dev/shm/looking-glass` (dmabuf off). "VM audio": `pulse_output_capture` of `win11_vm.monitor` |

    The audio source records the [`win11_vm` sink](audio.md#3-the-win11_vm-sink), so only the VM's sound ends up in the file.

3. Turn on OBS's WebSocket server with authentication (Tools > WebSocket Server Settings). [`obsws.py`](https://github.com/tahmidul612/arch-config/blob/main/tools/win11-vm/looking-glass/obsws.py) reads the port and password from `~/.config/obs-studio/plugin_config/obs-websocket/config.json`.
4. Link `vm-rec`:

    ```bash
    ln -s "$PWD/tools/win11-vm/looking-glass/vm-rec" ~/.local/bin/
    ```

### Use

```bash
vm-rec start      # starts OBS minimised (unit obs-vm-capture) if needed, then records
vm-rec status     # GetRecordStatus + GetStats: watch outputSkippedFrames
vm-rec stop
```

!!! warning "Frozen picture after a VM restart"
    OBS keeps its old mapping of the shared memory when the VM restarts or the shmem size changes, and then records one frozen frame. `vm-rec start` sets the "VM display" source's settings again before recording, which makes the plugin reopen the file. If you start recording from OBS's own window, toggle the source or restart OBS after a VM restart.

Check a finished file:

```bash
ffprobe -v error -show_entries stream=codec_name,width,height,r_frame_rate -of compact "<file>.mkv"
ffmpeg -ss 60 -t 30 -i "<file>.mkv" -vf mpdecimate -f null - 2>&1 | grep -o 'frame= *[0-9]*' | tail -1
```

The second command counts unique frames in 30 s. A healthy 60 fps recording shows close to 1800; a slideshow shows a handful.

In hdmi mode `vm-rec` still records the Looking Glass monitor, which is then Windows' second screen to the right of the LG. Use lg mode for recording.

Open item: 1-2% of frames repeated in the 1440p benchmark. Whether that came from Edge or from the capture was not established.

## Guest OBS (alternative)

OBS 33.0.2 is installed in the guest with a profile "Recording", started by the scheduled task `OBSRec` and controlled through its WebSocket on the guest's localhost. Only the Quick Sync TU7 settings worked:

| Encoder setting (1440p desktop, same benchmark) | Skipped | 3D engine |
| --- | --- | --- |
| QSV HEVC, target usage 7, CQP 18, no B-frames | 0 of 7295 | 79% |
| QSV H.264, target usage 7 | 0 of 7297 | 79% |
| QSV HEVC, target usage 1, CQP 16 | 6464 of 6753 | 99% |

Notes for QSV in OBS: it reads `qpi`/`qpp`/`qpb`, not `cqp`, from `recordEncoder.json`. After a forced OBS kill, delete `%APPDATA%\obs-studio\.sentinel` or the next start hangs on the crash dialog. `StopRecord` sometimes needs to be sent twice.

The guest profile was last rewritten by the benchmark script and has not been checked since, so treat its current settings as unknown.
