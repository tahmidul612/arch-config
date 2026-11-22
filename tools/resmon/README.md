# resmon

Month-long CPU / RAM / GPU usage logger for deciding whether to downgrade or
upgrade hardware. One Rust binary, one SQLite file, systemd user units.

Every 30 s it records:

- **System:** CPU busy %, load, RAM used (total − MemAvailable, so page cache
  doesn't count), cache, swap/zram, swap-in/out, and Linux pressure-stall
  (PSI) for CPU, memory and IO. PSI is the bottleneck signal: it measures how
  often work *waited* for that resource, not just how busy it was.
- **GPU (NVIDIA):** utilisation, VRAM, power, temperature, and per-process
  VRAM and SM %, read through NVML (`libnvidia-ml.so`, loaded at runtime; no
  `nvidia-smi` processes). SM % is the average of every driver sample since
  the previous reading.
- **Per app:** processes grouped by systemd cgroup (so Firefox, VS Code,
  `ollama.service`, a Docker container are each one row) with PSS memory,
  CPU % and VRAM. Shell-launched processes fall back to their command name.

## Files

| File | Installs to | Setup | Status |
| --- | --- | --- | --- |
| `src/` (Rust crate `resmon`, workspace `tools/Cargo.toml`) | `~/.local/bin/resmon` | `./install.sh` | Default |
| `../win11-vm/qga/` | `~/.local/bin/qga` | `./install.sh` (used for VM alerts) | Default |
| `systemd/*.service`, `systemd/*.timer` | `~/.config/systemd/user/` | `./install.sh` | Default |
| `routines.env` (generated, not in the repo) | `~/.config/resmon/routines.env`, mode 600 | `./install.sh` creates it; paste the triggers and tokens | Default |

## Install

Needs a Rust toolchain (`cargo`), SQLite, and libvirt for `qga`.

```bash
./install.sh            # build, install the binaries and units, start logging
./install.sh --linger   # also log when no one is logged in
./install.sh --remove   # stop; data and binaries are kept
```

Data lives in `~/.local/share/resmon/resmon.db` (~3–5 MB/day). Check it with
`resmon status`: the last sample should be under 30 s old.

## Use

```bash
resmon status
resmon report --days 7          # markdown: percentiles, "would 32 GB fit?",
resmon report --days 30 --out ~/resmon-30d.md   # top apps, bottleneck moments
resmon export                   # summary-{7d,30d}.json, reports, weekly/monthly history
```

`resmon-export.timer` refreshes `~/.local/share/resmon/exports/` hourly (local
copies only; `--out-dir` writes elsewhere). An export of 470 MB of data takes
about a second; the unit is still killed after 15 min and capped at half a core.

## Stuck-process alerts

`resmon-watch.timer` runs `resmon watch` every 15 min. If any app has sat at
≥ 80% of one core for 3 h straight (10th percentile, so bursty work like a Plex
transcode doesn't count), it sends a desktop notification and a KDE Connect
ping to any reachable device, and logs a `stuck_busy` row in `events`. Each
app alerts at most once per 24 h (state in `~/.local/state/resmon/alerts.json`).
Docker containers are named with their compose project and directory.

libvirt VMs are grouped as `vm:<domain>`. libvirt's own scope name,
`machine-qemu-<N>-<domain>`, gets a new N on every boot. A VM alert also asks
the guest, through `qga exec` and qemu-guest-agent, for its busiest processes,
e.g. `VM win11 (inside: RemotePlay 60%, obs64 35%)`. That shows whether a long
session was real work or something left spinning. It needs `qga` next to
`resmon`, access to `qemu:///system`, and a Windows guest with the agent
running.

```bash
resmon watch --dry-run                    # what's stuck right now
resmon watch --until <unix-ts> --dry-run  # replay a past window
```

To also get alerts elsewhere, set `RESMON_ALERT_CMD` in `routines.env`; it runs
with the title and body as `$1` and `$2`.

## Weekly and monthly reviews

`resmon-push-week.timer` (Mondays 07:30) and `resmon-push-month.timer` (the 1st,
07:20) POST the finished period's summary plus history to two Claude scheduled
tasks through their API triggers. Each run assesses the data, updates the
dashboard artifact and emails an HTML report. A push missed while the PC was off
fires at the next boot.

Triggers and tokens live in `~/.config/resmon/routines.env` (created by
`install.sh`, mode 600, never in the repo). Generate one per task at
claude.ai/code/routines: open the task, Add another trigger, API, Generate
token. Then:

```bash
resmon push week --dry-run   # see exactly what gets sent
resmon push week             # fire a review now
```

## Reading the report

- **RAM:** look at the "Would a smaller kit have fit?" table and p99. If
  usage above *capacity − 4 GB* is near 0%, that capacity is enough.
- **CPU:** high busy % with low CPU PSI means the CPU was used, not short.
  PSI p95 above ~20% means work regularly waited for cores.
- **GPU:** VRAM near the 10 GB ceiling caps model and scene size more than
  utilisation does.

Limitations: 30 s sampling smooths short spikes (PSI partially covers this),
the Intel iGPU isn't tracked, and PSS for other users' processes falls back
to RSS.

## Development

```bash
cd tools
cargo test -p resmon
cargo clippy --all-targets -- -D warnings   # pedantic lints are on for the workspace
```

The database schema and the JSON field names are unchanged from the earlier
Python version, so old databases and the Claude routines keep working.
