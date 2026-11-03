# resmon

Month-long CPU / RAM / GPU usage logger for deciding whether to downgrade or
upgrade hardware. Stdlib Python, one SQLite file, systemd user units.

Every 30 s it records:

- **System:** CPU busy %, load, RAM used (total − MemAvailable, so page cache
  doesn't count), cache, swap/zram, swap-in/out, and Linux pressure-stall
  (PSI) for CPU, memory and IO. PSI is the bottleneck signal: it measures how
  often work *waited* for that resource, not just how busy it was.
- **GPU (NVIDIA):** utilisation, VRAM, power, temperature, and per-process
  VRAM and SM % via `nvidia-smi pmon`.
- **Per app:** processes grouped by systemd cgroup (so Firefox, VS Code,
  `ollama.service`, a Docker container are each one row) with PSS memory,
  CPU % and VRAM. Shell-launched processes fall back to their command name.

## Install

```bash
./install.sh            # start logging now (while logged in)
./install.sh --linger   # also log when no one is logged in
./install.sh --remove   # stop; data is kept
```

Data lives in `~/.local/share/resmon/resmon.db` (~3–5 MB/day).

## Use

```bash
./resmon.py status
./resmon.py report --days 7          # markdown: percentiles, "would 32 GB fit?",
./resmon.py report --days 30 --out ~/resmon-30d.md   # top apps, bottleneck moments
./resmon.py export                   # writes exports/summary-{7d,30d}.json + reports
```

`resmon-export.timer` refreshes `exports/` hourly (local copies only).

## Weekly and monthly reviews

`resmon-push-week.timer` (Mondays 07:30) and `resmon-push-month.timer` (the 1st,
07:20) POST the finished period's summary plus history to two Claude scheduled
tasks through their API triggers. Each run assesses the data, updates the
dashboard artifact and emails an HTML report. A push missed while the PC was off
fires at the next boot.

Tokens live in `~/.config/resmon/routines.env` (created by `install.sh`, mode
600, never in the repo). Generate one per task at claude.ai/code/routines:
open the task, Add another trigger, API, Generate token. Then:

```bash
./resmon.py push week --dry-run   # see exactly what gets sent
./resmon.py push week             # fire a review now
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
