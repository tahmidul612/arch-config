#!/usr/bin/env python3
"""resmon: long-running CPU / RAM / GPU usage logger with an attribution report.

Samples system-wide metrics plus per-application usage (grouped by systemd
cgroup, so "Firefox" is one row instead of 40 content processes) into SQLite,
then answers: where does my RAM / CPU / GPU actually go, and could I downgrade
or should I upgrade?

Usage:
    resmon.py collect [--interval 30]       # run forever (systemd user service)
    resmon.py report  [--days 30] [--out report.md]
    resmon.py status                         # quick sanity check of the database
    resmon.py export  [--out-dir DIR]        # 7d/30d JSON + reports, local copies
    resmon.py push week|month [--dry-run]    # send a review to the Claude scheduled task

Stdlib only. GPU data comes from nvidia-smi when present.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import signal
import sqlite3
import subprocess
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

DATA_DIR = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "resmon"
DB_PATH = Path(os.environ.get("RESMON_DB", DATA_DIR / "resmon.db"))
CLK_TCK = os.sysconf("SC_CLK_TCK")
PAGE_KB = os.sysconf("SC_PAGE_SIZE") // 1024
NCPU = os.cpu_count() or 1

# A group is stored for a sample if it crosses any of these, plus the top N by memory.
MIN_MEM_KB = 100 * 1024
MIN_CPU_PCT = 2.0
TOP_N_ALWAYS = 12

SCHEMA = """
CREATE TABLE IF NOT EXISTS samples (
    ts INTEGER PRIMARY KEY,
    interval_s REAL,
    cpu_pct REAL,            -- whole machine, 0-100
    load1 REAL,
    mem_total_kb INTEGER,
    mem_avail_kb INTEGER,    -- used = total - available (excludes reclaimable cache)
    mem_cached_kb INTEGER,
    mem_shmem_kb INTEGER,
    swap_used_kb INTEGER,
    swapin_pages INTEGER,    -- delta since previous sample
    swapout_pages INTEGER,
    psi_cpu_some REAL,       -- avg10, percent of time stalled
    psi_mem_some REAL,
    psi_mem_full REAL,
    psi_io_some REAL,
    gpu_util REAL,
    gpu_mem_util REAL,
    vram_used_mb REAL,
    vram_total_mb REAL,
    gpu_power_w REAL,
    gpu_temp_c REAL
);
CREATE TABLE IF NOT EXISTS groups (
    ts INTEGER,
    grp TEXT,
    detail TEXT,             -- biggest member processes by memory
    nprocs INTEGER,
    mem_kb INTEGER,          -- PSS where readable, RSS otherwise
    cpu_pct REAL,            -- percent of ONE core (100 = one full core)
    vram_mb REAL,
    gpu_sm_pct REAL
);
CREATE INDEX IF NOT EXISTS groups_ts ON groups(ts);
CREATE INDEX IF NOT EXISTS groups_grp ON groups(grp);
CREATE TABLE IF NOT EXISTS events (
    ts INTEGER,
    kind TEXT,
    detail TEXT
);
"""


# --------------------------------------------------------------------------- helpers

def read(path: str) -> str | None:
    try:
        with open(path, "r", errors="replace") as f:
            return f.read()
    except OSError:
        return None


def meminfo() -> dict[str, int]:
    out = {}
    for line in (read("/proc/meminfo") or "").splitlines():
        k, _, v = line.partition(":")
        parts = v.split()
        if parts:
            out[k] = int(parts[0])
    return out


def vmstat() -> dict[str, int]:
    out = {}
    for line in (read("/proc/vmstat") or "").splitlines():
        k, _, v = line.partition(" ")
        if v.strip().isdigit():
            out[k] = int(v)
    return out


def cpu_times() -> tuple[int, int]:
    """(busy, total) jiffies across all CPUs."""
    line = (read("/proc/stat") or "cpu 0").splitlines()[0]
    vals = [int(x) for x in line.split()[1:]]
    idle = vals[3] + (vals[4] if len(vals) > 4 else 0)  # idle + iowait
    total = sum(vals[:8])  # exclude guest columns (already counted in user)
    return total - idle, total


def psi(resource: str) -> tuple[float | None, float | None]:
    text = read(f"/proc/pressure/{resource}")
    if not text:
        return None, None
    some = full = None
    for line in text.splitlines():
        m = re.search(r"avg10=([\d.]+)", line)
        if not m:
            continue
        if line.startswith("some"):
            some = float(m.group(1))
        elif line.startswith("full"):
            full = float(m.group(1))
    return some, full


ESC = re.compile(r"\\x([0-9a-fA-F]{2})")
GENERIC = re.compile(r"^(session-\d+|init|user@\d+|user-\d+|app|background|"
                     r"-\.slice|system|user|machine)$")


def unescape(s: str) -> str:
    return ESC.sub(lambda m: chr(int(m.group(1), 16)), s)


def group_name(pid: int, comm: str) -> str:
    """Name the application a process belongs to, from its cgroup."""
    cg = read(f"/proc/{pid}/cgroup") or ""
    path = ""
    for line in cg.splitlines():
        if line.startswith("0::"):
            path = line[3:].strip()
            break
    parts = [p for p in path.split("/") if p]
    for comp in reversed(parts):
        name = unescape(comp)
        name = re.sub(r"\.(scope|service|slice)$", "", name)
        if GENERIC.match(name):
            continue
        m = re.match(r"^(?:docker|libpod)-([0-9a-f]{12})", name)
        if m:
            return f"container:{m.group(1)}"
        if name.startswith("app-"):
            name = name[4:]
            name = re.sub(r"^(flatpak|gnome|kde|cosmic)-", "", name)
            name = re.sub(r"@[^/]*$", "", name)          # app-foo@uuid
            name = re.sub(r"-[0-9a-f]{6,}$", "", name)   # app-foo-<hash>
            name = re.sub(r"-\d+$", "", name)            # app-foo-1234
            name = re.sub(r"^dbus-:[\d.]+-", "", name)       # dbus-activated services
            dots = name.split(".")
            if len(dots) >= 3:                                # reverse-DNS app ids
                last = dots[-1]
                name = dots[-2] if last.lower() in {"client", "app", "desktop", "browser"} else last
            return name or comm
        if comp.endswith(".service") or comp.endswith(".scope"):
            return name
    return comm  # shell-launched things in session scopes, kernel-ish processes


def nvidia_smi(args: list[str]) -> str | None:
    if not shutil.which("nvidia-smi"):
        return None
    try:
        return subprocess.run(["nvidia-smi", *args], capture_output=True, text=True,
                              timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return None


def num(s: str) -> float | None:
    try:
        return float(s)
    except ValueError:
        return None


def gpu_system() -> dict:
    out = nvidia_smi(["--query-gpu=utilization.gpu,utilization.memory,memory.used,"
                      "memory.total,power.draw,temperature.gpu",
                      "--format=csv,noheader,nounits"])
    if not out or not out.strip():
        return {}
    f = [x.strip() for x in out.strip().splitlines()[0].split(",")]
    keys = ["gpu_util", "gpu_mem_util", "vram_used_mb", "vram_total_mb", "gpu_power_w", "gpu_temp_c"]
    return {k: num(v) for k, v in zip(keys, f)}


def gpu_procs() -> dict[int, tuple[float, float]]:
    """pid -> (vram_mb, sm_pct). Uses pmon (covers graphics + compute)."""
    res: dict[int, list[float]] = {}
    out = nvidia_smi(["pmon", "-c", "1", "-s", "um"])
    if out:
        header = None
        for line in out.splitlines():
            if line.startswith("#"):
                cols = line.lstrip("#").split()
                if "pid" in cols:
                    header = cols
                continue
            if not header:
                continue
            vals = line.split()
            row = dict(zip(header, vals))
            pid = row.get("pid", "-")
            if not pid.isdigit():
                continue
            fb = num(row.get("fb", "-")) or 0.0
            sm = num(row.get("sm", "-")) or 0.0
            cur = res.setdefault(int(pid), [0.0, 0.0])
            cur[0] += fb
            cur[1] += sm
    # Compute apps report memory more reliably on some drivers.
    out = nvidia_smi(["--query-compute-apps=pid,used_gpu_memory", "--format=csv,noheader,nounits"])
    for line in (out or "").splitlines():
        p = [x.strip() for x in line.split(",")]
        if len(p) == 2 and p[0].isdigit() and num(p[1]) is not None:
            cur = res.setdefault(int(p[0]), [0.0, 0.0])
            cur[0] = max(cur[0], num(p[1]))
    return {k: (v[0], v[1]) for k, v in res.items()}


def proc_mem_kb(pid: int) -> int | None:
    text = read(f"/proc/{pid}/smaps_rollup")
    if text:
        m = re.search(r"^Pss:\s+(\d+)", text, re.M)
        if m:
            return int(m.group(1))
    statm = read(f"/proc/{pid}/statm")
    if statm:
        p = statm.split()
        if len(p) > 1:
            return int(p[1]) * PAGE_KB
    return None


# --------------------------------------------------------------------------- collector

class Collector:
    def __init__(self, interval: float):
        self.interval = interval
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(DB_PATH)
        self.db.executescript(SCHEMA)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.prev_cpu = cpu_times()
        self.prev_vm = vmstat()
        self.prev_proc: dict[int, tuple[int, int]] = {}  # pid -> (starttime, ticks)
        self.prev_t = time.monotonic()
        self.running = True
        signal.signal(signal.SIGTERM, self._stop)
        signal.signal(signal.SIGINT, self._stop)
        self.db.execute("INSERT INTO events VALUES (?,?,?)",
                        (int(time.time()), "start", f"interval={interval}s pid={os.getpid()}"))
        self.db.commit()
        self.scan_procs(0)  # prime per-process CPU counters

    def _stop(self, *_):
        self.running = False

    def scan_procs(self, dt: float) -> dict[str, dict]:
        groups: dict[str, dict] = defaultdict(lambda: {"n": 0, "mem": 0, "cpu": 0.0,
                                                       "vram": 0.0, "sm": 0.0, "members": defaultdict(int)})
        gpu = gpu_procs() if dt else {}
        seen: dict[int, tuple[int, int]] = {}
        for entry in os.listdir("/proc"):
            if not entry.isdigit():
                continue
            pid = int(entry)
            stat = read(f"/proc/{pid}/stat")
            if not stat:
                continue
            rp = stat.rfind(")")
            comm = stat[stat.find("(") + 1:rp]
            f = stat[rp + 2:].split()
            # f[0]=state; utime=f[11], stime=f[12], starttime=f[19], rss=f[21]
            try:
                ticks = int(f[11]) + int(f[12])
                start = int(f[19])
                rss_pages = int(f[21])
            except (IndexError, ValueError):
                continue
            seen[pid] = (start, ticks)
            if not dt:
                continue
            if rss_pages == 0 and pid not in gpu:
                continue  # kernel thread
            prev = self.prev_proc.get(pid)
            dticks = ticks - prev[1] if prev and prev[0] == start else 0
            cpu = 100.0 * dticks / CLK_TCK / dt
            mem = proc_mem_kb(pid) or rss_pages * PAGE_KB
            g = groups[group_name(pid, comm)]
            g["n"] += 1
            g["mem"] += mem
            g["cpu"] += cpu
            g["members"][comm] += mem
            if pid in gpu:
                g["vram"] += gpu[pid][0]
                g["sm"] += gpu[pid][1]
        self.prev_proc = seen
        return groups

    def sample(self):
        now_t = time.monotonic()
        dt = now_t - self.prev_t
        self.prev_t = now_t
        ts = int(time.time())

        busy, total = cpu_times()
        pb, pt = self.prev_cpu
        self.prev_cpu = (busy, total)
        cpu_pct = 100.0 * (busy - pb) / (total - pt) if total > pt else 0.0

        mi = meminfo()
        vm = vmstat()
        swin = vm.get("pswpin", 0) - self.prev_vm.get("pswpin", 0)
        swout = vm.get("pswpout", 0) - self.prev_vm.get("pswpout", 0)
        self.prev_vm = vm
        cs, _ = psi("cpu")
        ms, mf = psi("memory")
        ios, _ = psi("io")
        g = gpu_system()
        load1 = float((read("/proc/loadavg") or "0").split()[0])

        self.db.execute(
            "INSERT OR REPLACE INTO samples VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (ts, dt, cpu_pct, load1, mi.get("MemTotal"), mi.get("MemAvailable"),
             mi.get("Cached"), mi.get("Shmem"),
             mi.get("SwapTotal", 0) - mi.get("SwapFree", 0), swin, swout,
             cs, ms, mf, ios, g.get("gpu_util"), g.get("gpu_mem_util"), g.get("vram_used_mb"),
             g.get("vram_total_mb"), g.get("gpu_power_w"), g.get("gpu_temp_c")))

        groups = self.scan_procs(dt)
        by_mem = sorted(groups.items(), key=lambda kv: -kv[1]["mem"])
        keep = {k for k, _ in by_mem[:TOP_N_ALWAYS]}
        rows = []
        for name, v in groups.items():
            if name in keep or v["mem"] >= MIN_MEM_KB or v["cpu"] >= MIN_CPU_PCT or v["vram"] > 0:
                top = sorted(v["members"].items(), key=lambda kv: -kv[1])[:3]
                detail = ", ".join(f"{c}:{m // 1024}M" for c, m in top)
                rows.append((ts, name, detail, v["n"], v["mem"], round(v["cpu"], 2),
                             v["vram"], v["sm"]))
        self.db.executemany("INSERT INTO groups VALUES (?,?,?,?,?,?,?,?)", rows)
        self.db.commit()

    def run(self):
        time.sleep(min(self.interval, 5))
        while self.running:
            t0 = time.monotonic()
            try:
                self.sample()
            except Exception as e:  # keep logging through transient oddities
                print(f"resmon: sample failed: {e!r}", file=sys.stderr)
            remaining = self.interval - (time.monotonic() - t0)
            end = time.monotonic() + max(remaining, 1)
            while self.running and time.monotonic() < end:
                time.sleep(min(1.0, end - time.monotonic()))
        self.db.execute("INSERT INTO events VALUES (?,?,?)", (int(time.time()), "stop", ""))
        self.db.commit()


# --------------------------------------------------------------------------- report

def pct(values: list[float], p: float) -> float | None:
    v = sorted(x for x in values if x is not None)
    if not v:
        return None
    k = (len(v) - 1) * p / 100
    lo, hi = int(k), min(int(k) + 1, len(v) - 1)
    return v[lo] + (v[hi] - v[lo]) * (k - lo)


def gb(kb: float | None) -> str:
    return "–" if kb is None else f"{kb / 1048576:.1f} GB"


def f1(x: float | None, unit: str = "") -> str:
    return "–" if x is None else f"{x:.1f}{unit}"


def share(values: list[float], pred) -> str:
    v = [x for x in values if x is not None]
    if not v:
        return "–"
    return f"{100 * sum(1 for x in v if pred(x)) / len(v):.2f}%"


def oom_events(since: int) -> list[str]:
    if not shutil.which("journalctl"):
        return []
    try:
        out = subprocess.run(["journalctl", "-k", "--no-pager", "-o", "short-iso",
                              f"--since=@{since}", "--grep", "Out of memory|oom-kill|Killed process"],
                             capture_output=True, text=True, timeout=30).stdout
        return [l for l in out.splitlines() if l and not l.startswith("--")][:20]
    except (OSError, subprocess.SubprocessError):
        return []


def report(days: float) -> str:
    db = sqlite3.connect(DB_PATH)
    since = int(time.time() - days * 86400)
    S = db.execute("SELECT * FROM samples WHERE ts >= ? ORDER BY ts", (since,)).fetchall()
    cols = [d[0] for d in db.execute("SELECT * FROM samples LIMIT 0").description]
    if not S:
        return "No samples yet. Is the collector running? `systemctl --user status resmon`\n"
    s = [dict(zip(cols, r)) for r in S]
    n = len(s)
    span_h = (s[-1]["ts"] - s[0]["ts"]) / 3600
    total_kb = s[-1]["mem_total_kb"]
    used = [r["mem_total_kb"] - r["mem_avail_kb"] for r in s]
    swap = [r["swap_used_kb"] for r in s]
    cpu = [r["cpu_pct"] for r in s]
    has_gpu = any(r["gpu_util"] is not None for r in s)
    gpu = [r["gpu_util"] for r in s]
    vram = [r["vram_used_mb"] for r in s]
    vram_total = next((r["vram_total_mb"] for r in reversed(s) if r["vram_total_mb"]), None)
    L: list[str] = []
    w = L.append

    w(f"# Resource report ({datetime.fromtimestamp(s[0]['ts']):%Y-%m-%d %H:%M} → "
      f"{datetime.fromtimestamp(s[-1]['ts']):%Y-%m-%d %H:%M})\n")
    w(f"{n:,} samples over {span_h:.1f} h of logged time (only while the PC was on). "
      f"{NCPU} logical CPUs, {gb(total_kb)} RAM"
      + (f", {vram_total / 1024:.0f} GB VRAM." if vram_total else ".") + "\n")

    # ---------------- RAM
    w("## RAM\n")
    w("\"Used\" here is total minus MemAvailable, i.e. memory that could not be "
      "dropped. Page cache is excluded because Linux fills spare RAM with it and "
      "gives it back on demand.\n")
    w("| | p50 | p95 | p99 | max |\n|---|---|---|---|---|")
    w(f"| Used | {gb(pct(used, 50))} | {gb(pct(used, 95))} | {gb(pct(used, 99))} | {gb(max(used))} |")
    w(f"| Swap/zram used | {gb(pct(swap, 50))} | {gb(pct(swap, 95))} | {gb(pct(swap, 99))} | {gb(max(swap))} |")
    w(f"| Page cache | {gb(pct([r['mem_cached_kb'] for r in s], 50))} | | | {gb(max(r['mem_cached_kb'] or 0 for r in s))} |\n")
    w("Would a smaller kit have fit? (time the non-reclaimable usage was above each "
      "line; leave ~4 GB headroom for cache and the kernel)\n")
    w("| Capacity | time over capacity − 4 GB | time over capacity |\n|---|---|---|")
    for cap in (16, 24, 32, 48, 64, 96):
        ck = cap * 1048576
        w(f"| {cap} GB | {share(used, lambda x: x > ck - 4 * 1048576)} | {share(used, lambda x: x > ck)} |")
    ms = [r["psi_mem_some"] for r in s]
    mf = [r["psi_mem_full"] for r in s]
    sw_out = sum(r["swapout_pages"] or 0 for r in s) * PAGE_KB
    w(f"\nMemory pressure (PSI some avg10): p95 {f1(pct(ms, 95), '%')}, max {f1(max((x for x in ms if x is not None), default=None), '%')}; "
      f"full p99 {f1(pct(mf, 99), '%')}. Swapped out in total: {gb(sw_out)}.\n")

    # ---------------- CPU
    w("## CPU\n")
    cs = [r["psi_cpu_some"] for r in s]
    w("| | p50 | p95 | p99 | max |\n|---|---|---|---|---|")
    w(f"| Whole-CPU busy | {f1(pct(cpu, 50), '%')} | {f1(pct(cpu, 95), '%')} | {f1(pct(cpu, 99), '%')} | {f1(max(cpu), '%')} |")
    w(f"| ≈ threads busy | {f1(pct(cpu, 50) * NCPU / 100)} | {f1(pct(cpu, 95) * NCPU / 100)} | {f1(pct(cpu, 99) * NCPU / 100)} | {f1(max(cpu) * NCPU / 100)} |")
    w(f"| CPU pressure (PSI some) | {f1(pct(cs, 50), '%')} | {f1(pct(cs, 95), '%')} | {f1(pct(cs, 99), '%')} | {f1(max((x for x in cs if x is not None), default=None), '%')} |\n")
    w(f"Time above 50% busy: {share(cpu, lambda x: x > 50)}; above 80%: {share(cpu, lambda x: x > 80)}; "
      f"above 95%: {share(cpu, lambda x: x > 95)}. A 30 s average hides short bursts, so read p99/max "
      "together with PSI: PSI says how often runnable work had to wait for a core.\n")

    # ---------------- GPU
    w("## GPU\n")
    if has_gpu:
        w("| | p50 | p95 | p99 | max |\n|---|---|---|---|---|")
        w(f"| Utilisation | {f1(pct(gpu, 50), '%')} | {f1(pct(gpu, 95), '%')} | {f1(pct(gpu, 99), '%')} | {f1(max(x for x in gpu if x is not None), '%')} |")
        w(f"| VRAM used | {f1(pct(vram, 50), ' MB')} | {f1(pct(vram, 95), ' MB')} | {f1(pct(vram, 99), ' MB')} | {f1(max(x for x in vram if x is not None), ' MB')} |\n")
        if vram_total:
            w(f"Time with VRAM > 90% full: {share(vram, lambda x: x > 0.9 * vram_total)}; "
              f"GPU > 90% busy: {share(gpu, lambda x: x > 90)}; GPU idle (<5%): {share(gpu, lambda x: x < 5)}.\n")
    else:
        w("No NVIDIA data recorded (nvidia-smi missing or failing).\n")

    # ---------------- attribution
    G = db.execute("""SELECT grp, COUNT(*), AVG(mem_kb), MAX(mem_kb), SUM(cpu_pct), MAX(cpu_pct),
                             MAX(vram_mb), AVG(vram_mb), MAX(gpu_sm_pct)
                      FROM groups WHERE ts >= ? GROUP BY grp""", (since,)).fetchall()
    avg_dt = sum(r["interval_s"] or 0 for r in s) / n
    rows = []
    for grp, cnt, avg_mem, max_mem, sum_cpu, max_cpu, max_vram, avg_vram, max_sm in G:
        rows.append(dict(grp=grp, presence=cnt / n, avg_mem_all=avg_mem * cnt / n, avg_mem=avg_mem,
                         max_mem=max_mem, core_h=sum_cpu / 100 * avg_dt / 3600, max_cpu=max_cpu,
                         max_vram=max_vram or 0, avg_vram=(avg_vram or 0) * cnt / n, max_sm=max_sm or 0))

    w("## Where the RAM goes\n")
    w("Average share of RAM over the whole period (PSS where readable, so shared libraries are split fairly).\n")
    w("| App / service | avg (all time) | avg when running | peak | running | biggest processes |\n|---|---|---|---|---|---|")
    details = dict(db.execute("""SELECT grp, detail FROM groups g1 WHERE ts >= ? AND mem_kb =
                                 (SELECT MAX(mem_kb) FROM groups g2 WHERE g2.grp = g1.grp AND g2.ts >= ?)""",
                              (since, since)).fetchall())
    for r in sorted(rows, key=lambda r: -r["avg_mem_all"])[:20]:
        w(f"| {r['grp']} | {gb(r['avg_mem_all'])} | {gb(r['avg_mem'])} | {gb(r['max_mem'])} | "
          f"{100 * r['presence']:.0f}% | {details.get(r['grp'], '')} |")

    w("\n## Where the CPU goes\n")
    w("Core-hours = total CPU time consumed (1 core-hour = one thread fully busy for an hour).\n")
    w("| App / service | core-hours | share | peak (% of one core) |\n|---|---|---|---|")
    tot_core_h = sum(r["core_h"] for r in rows) or 1
    for r in sorted(rows, key=lambda r: -r["core_h"])[:20]:
        w(f"| {r['grp']} | {r['core_h']:.1f} | {100 * r['core_h'] / tot_core_h:.1f}% | {r['max_cpu']:.0f}% |")

    if has_gpu:
        w("\n## Where the GPU goes\n")
        w("| App / service | peak VRAM | avg VRAM (all time) | peak SM % |\n|---|---|---|---|")
        for r in sorted([r for r in rows if r["max_vram"] > 0], key=lambda r: -r["max_vram"])[:15]:
            w(f"| {r['grp']} | {r['max_vram']:.0f} MB | {r['avg_vram']:.0f} MB | {r['max_sm']:.0f}% |")

    # ---------------- how it ties together
    w("\n## How it ties together\n")
    w("### Peak-RAM moments\n")
    peaks = sorted(s, key=lambda r: -(r["mem_total_kb"] - r["mem_avail_kb"]))
    picked: list[dict] = []
    for r in peaks:  # 5 distinct moments, at least 1 h apart
        if all(abs(r["ts"] - p["ts"]) > 3600 for p in picked):
            picked.append(r)
        if len(picked) == 5:
            break
    for r in picked:
        top = db.execute("SELECT grp, mem_kb, cpu_pct, vram_mb FROM groups WHERE ts=? ORDER BY mem_kb DESC LIMIT 6",
                         (r["ts"],)).fetchall()
        apps = ", ".join(f"{g} {m / 1048576:.1f}G" for g, m, _, _ in top)
        w(f"- **{datetime.fromtimestamp(r['ts']):%a %b %d %H:%M}**: {gb(r['mem_total_kb'] - r['mem_avail_kb'])} used, "
          f"CPU {r['cpu_pct']:.0f}%, GPU {f1(r['gpu_util'], '%')}, VRAM {f1(r['vram_used_mb'], ' MB')}. Top: {apps}")

    w("\n### Bottleneck moments\n")
    w("A sample counts as constrained when the hardware was making work wait: "
      "CPU PSI > 20% or CPU > 90% (CPU), memory PSI > 5% or swapping out > 100 MB per sample (RAM), "
      "GPU > 95% busy or VRAM > 95% full (GPU).\n")
    cats = {
        "CPU": lambda r: (r["psi_cpu_some"] or 0) > 20 or r["cpu_pct"] > 90,
        "RAM": lambda r: (r["psi_mem_some"] or 0) > 5 or (r["swapout_pages"] or 0) * PAGE_KB > 100 * 1024,
        "GPU": lambda r: (r["gpu_util"] or 0) > 95 or (vram_total and (r["vram_used_mb"] or 0) > 0.95 * vram_total),
    }
    w("| Resource | constrained time | ≈ hours | apps most often on top during those moments |\n|---|---|---|---|")
    for name, pred in cats.items():
        hit = [r["ts"] for r in s if pred(r)]
        culprits = ""
        if hit:
            order = {"CPU": "cpu_pct", "RAM": "mem_kb", "GPU": "vram_mb"}[name]
            counts: dict[str, int] = defaultdict(int)
            for ts in hit[:5000]:
                top = db.execute(f"SELECT grp FROM groups WHERE ts=? ORDER BY {order} DESC LIMIT 2", (ts,)).fetchall()
                for (g,) in top:
                    counts[g] += 1
            culprits = ", ".join(f"{g} ({c})" for g, c in sorted(counts.items(), key=lambda kv: -kv[1])[:5])
        w(f"| {name} | {100 * len(hit) / n:.2f}% | {len(hit) * avg_dt / 3600:.1f} | {culprits} |")

    w("\n### Daily rhythm (average by hour of day)\n")
    w("| Hour | samples | RAM used | CPU | GPU |\n|---|---|---|---|---|")
    hours: dict[int, list[dict]] = defaultdict(list)
    for r in s:
        hours[datetime.fromtimestamp(r["ts"]).hour].append(r)
    for h in range(24):
        rs = hours.get(h)
        if not rs:
            continue
        au = sum(r["mem_total_kb"] - r["mem_avail_kb"] for r in rs) / len(rs)
        ac = sum(r["cpu_pct"] for r in rs) / len(rs)
        gu = [r["gpu_util"] for r in rs if r["gpu_util"] is not None]
        w(f"| {h:02d}:00 | {len(rs)} | {gb(au)} | {ac:.0f}% | {f1(sum(gu) / len(gu), '%') if gu else '–'} |")

    ooms = oom_events(since)
    w("\n## OOM kills\n")
    w("\n".join(f"- `{l}`" for l in ooms) if ooms else "None found in the kernel journal for this period.")

    # ---------------- verdict
    w("\n## Quick read\n")
    p99 = pct(used, 99)
    mx = max(used)
    for cap in (32, 48):
        ck = cap * 1048576
        if mx < ck - 4 * 1048576:
            w(f"- **RAM:** peak non-reclaimable use was {gb(mx)}, so {cap} GB would have covered every sample with headroom.")
            break
        if p99 < ck - 4 * 1048576:
            w(f"- **RAM:** {cap} GB covers 99% of the time (p99 {gb(p99)}), but the peak was {gb(mx)}. "
              "Check the peak-RAM moments above to see whether those were one-off jobs.")
            break
    else:
        w(f"- **RAM:** p99 is {gb(p99)}. You regularly use more than 44 GB, so 64 GB is earning its keep.")
    if (pct(ms, 95) or 0) > 5:
        w("- **RAM pressure:** memory PSI is non-trivial; you are short on RAM at times, not over-provisioned.")
    cpu99 = pct(cpu, 99) or 0
    cpsi95 = pct(cs, 95) or 0
    if cpu99 < 50 and cpsi95 < 10:
        w(f"- **CPU:** p99 {cpu99:.0f}% busy and low pressure. A CPU with fewer threads would rarely be the limit.")
    elif cpsi95 > 20:
        w(f"- **CPU:** pressure p95 {cpsi95:.0f}%. Work often waits for a core, so more cores/faster cores would help.")
    else:
        w(f"- **CPU:** p99 {cpu99:.0f}% busy. Busy at times but rarely saturated.")
    if has_gpu and vram_total:
        vmax = max(x for x in vram if x is not None)
        w(f"- **GPU:** p95 utilisation {f1(pct(gpu, 95), '%')}, peak VRAM {vmax:.0f}/{vram_total:.0f} MB. "
          + ("VRAM has hit the ceiling, so models/scenes are limited by it." if vmax > 0.95 * vram_total
             else "VRAM has room to spare."))
    return "\n".join(L) + "\n"


def stats(days: float) -> dict:
    """Compact machine-readable summary for the weekly/monthly assessment."""
    now = int(time.time())
    return summarize(now - int(days * 86400), now + 1, days)


def summarize(since: int, until: int, days: float | None = None) -> dict:
    db = sqlite3.connect(DB_PATH)
    if days is None:
        days = round((until - since) / 86400, 2)
    cols = [d[0] for d in db.execute("SELECT * FROM samples LIMIT 0").description]
    s = [dict(zip(cols, r)) for r in db.execute(
        "SELECT * FROM samples WHERE ts >= ? AND ts < ? ORDER BY ts", (since, until))]
    if not s:
        return {"days": days, "samples": 0}
    n = len(s)
    avg_dt = sum(r["interval_s"] or 0 for r in s) / n
    G = lambda kb: None if kb is None else round(kb / 1048576, 2)
    used = [r["mem_total_kb"] - r["mem_avail_kb"] for r in s]
    cpu = [r["cpu_pct"] for r in s]
    gpu = [r["gpu_util"] for r in s if r["gpu_util"] is not None]
    vram = [r["vram_used_mb"] for r in s if r["vram_used_mb"] is not None]
    vram_total = next((r["vram_total_mb"] for r in reversed(s) if r["vram_total_mb"]), None)
    r2 = lambda x: None if x is None else round(x, 2)
    frac = lambda vals, pred: round(100 * sum(1 for x in vals if x is not None and pred(x)) / max(len(vals), 1), 3)
    out = {
        "generated": datetime.now().isoformat(timespec="seconds"),
        "days": days, "samples": n,
        "start": datetime.fromtimestamp(s[0]["ts"]).isoformat(timespec="minutes"),
        "end": datetime.fromtimestamp(s[-1]["ts"]).isoformat(timespec="minutes"),
        "logged_hours": round(n * avg_dt / 3600, 1),
        "hardware": {"threads": NCPU, "ram_gb": G(s[-1]["mem_total_kb"]),
                     "vram_gb": r2(vram_total / 1024) if vram_total else None},
        "ram": {"used_p50_gb": G(pct(used, 50)), "used_p95_gb": G(pct(used, 95)),
                "used_p99_gb": G(pct(used, 99)), "used_max_gb": G(max(used)),
                "swap_max_gb": G(max(r["swap_used_kb"] or 0 for r in s)),
                "swapped_out_gb": G(sum(r["swapout_pages"] or 0 for r in s) * PAGE_KB),
                "psi_some_p95": r2(pct([r["psi_mem_some"] for r in s], 95)),
                "pct_time_over": {f"{c}GB": frac(used, lambda x, c=c: x > (c - 4) * 1048576)
                                  for c in (16, 24, 32, 48)}},
        "cpu": {"busy_p50": r2(pct(cpu, 50)), "busy_p95": r2(pct(cpu, 95)), "busy_p99": r2(pct(cpu, 99)),
                "busy_max": r2(max(cpu)), "psi_some_p95": r2(pct([r["psi_cpu_some"] for r in s], 95)),
                "pct_time_over_80": frac(cpu, lambda x: x > 80)},
        "gpu": None if not gpu else {
            "util_p50": r2(pct(gpu, 50)), "util_p95": r2(pct(gpu, 95)), "util_max": r2(max(gpu)),
            "vram_p95_mb": r2(pct(vram, 95)), "vram_max_mb": r2(max(vram)) if vram else None,
            "pct_time_vram_over_90": frac(vram, lambda x: x > 0.9 * vram_total) if vram_total else None,
            "pct_time_idle": frac(gpu, lambda x: x < 5)},
    }
    apps = []
    for grp, cnt, avg_mem, max_mem, sum_cpu, max_cpu, max_vram, avg_vram in db.execute(
            """SELECT grp, COUNT(*), AVG(mem_kb), MAX(mem_kb), SUM(cpu_pct), MAX(cpu_pct), MAX(vram_mb), AVG(vram_mb)
               FROM groups WHERE ts >= ? AND ts < ? GROUP BY grp""", (since, until)):
        apps.append({"app": grp, "ram_avg_gb": G(avg_mem * cnt / n), "ram_peak_gb": G(max_mem),
                     "running_pct": round(100 * cnt / n, 1),
                     "cpu_core_hours": round(sum_cpu / 100 * avg_dt / 3600, 2),
                     "cpu_peak_pct_of_core": round(max_cpu, 0),
                     "vram_peak_mb": round(max_vram or 0), "vram_avg_mb": round((avg_vram or 0) * cnt / n)})
    out["top_ram"] = sorted(apps, key=lambda a: -a["ram_avg_gb"])[:10]
    out["top_cpu"] = sorted(apps, key=lambda a: -a["cpu_core_hours"])[:10]
    out["top_vram"] = [a for a in sorted(apps, key=lambda a: -a["vram_peak_mb"]) if a["vram_peak_mb"] > 0][:8]
    peak = max(s, key=lambda r: r["mem_total_kb"] - r["mem_avail_kb"])
    top = db.execute("SELECT grp, mem_kb FROM groups WHERE ts=? ORDER BY mem_kb DESC LIMIT 5", (peak["ts"],)).fetchall()
    out["ram_peak_moment"] = {"at": datetime.fromtimestamp(peak["ts"]).isoformat(timespec="minutes"),
                              "used_gb": G(peak["mem_total_kb"] - peak["mem_avail_kb"]),
                              "cpu_pct": r2(peak["cpu_pct"]), "gpu_util": peak["gpu_util"],
                              "top_apps": [{"app": g, "gb": G(m)} for g, m in top]}
    out["oom_kills"] = len(oom_events(since)) if until > time.time() - 60 else None
    return out


def flat(sm: dict, kind: str, period: str) -> dict:
    """One history row (weekly.json / monthly.json) from a summary."""
    ram, cpu, gpu = sm["ram"], sm["cpu"], sm.get("gpu") or {}
    top = sm["top_ram"][0]["app"] if sm.get("top_ram") else None
    return {
        "kind": kind, "period": period, "start": sm["start"][:10], "end": sm["end"][:10],
        "logged_hours": sm["logged_hours"],
        "ram_p50_gb": ram["used_p50_gb"], "ram_p95_gb": ram["used_p95_gb"],
        "ram_p99_gb": ram["used_p99_gb"], "ram_max_gb": ram["used_max_gb"],
        "pct_time_over_28gb": ram["pct_time_over"]["32GB"], "pct_time_over_44gb": ram["pct_time_over"]["48GB"],
        "mem_psi_p95": ram["psi_some_p95"], "swapped_out_gb": ram["swapped_out_gb"],
        "cpu_p50": cpu["busy_p50"], "cpu_p95": cpu["busy_p95"], "cpu_p99": cpu["busy_p99"],
        "cpu_psi_p95": cpu["psi_some_p95"], "pct_time_cpu_over_80": cpu["pct_time_over_80"],
        "gpu_p50": gpu.get("util_p50"), "gpu_p95": gpu.get("util_p95"),
        "vram_max_mb": gpu.get("vram_max_mb"), "pct_time_vram_over_90": gpu.get("pct_time_vram_over_90"),
        "top_ram_app": top,
    }


def periods(kind: str) -> list[tuple[str, int, int]]:
    """(label, start_ts, end_ts) for every ISO week / calendar month with data, oldest first."""
    db = sqlite3.connect(DB_PATH)
    first = db.execute("SELECT MIN(ts) FROM samples").fetchone()[0]
    if first is None:
        return []
    from datetime import timedelta
    d = datetime.fromtimestamp(first).replace(hour=0, minute=0, second=0, microsecond=0)
    out = []
    if kind == "week":
        d -= timedelta(days=d.weekday())
        while d.timestamp() <= time.time():
            nxt = d + timedelta(days=7)
            y, w, _ = d.isocalendar()
            out.append((f"{y}-W{w:02d}", int(d.timestamp()), int(nxt.timestamp())))
            d = nxt
    else:
        d = d.replace(day=1)
        while d.timestamp() <= time.time():
            nxt = d.replace(year=d.year + (d.month == 12), month=d.month % 12 + 1)
            out.append((f"{d:%Y-%m}", int(d.timestamp()), int(nxt.timestamp())))
            d = nxt
    return out


def export(out_dir: Path):
    """Write 7- and 30-day summaries + full reports for the scheduled assessments."""
    import json
    out_dir.mkdir(parents=True, exist_ok=True)
    for label, days in (("7d", 7), ("30d", 30)):
        (out_dir / f"summary-{label}.json").write_text(json.dumps(stats(days), indent=1))
        (out_dir / f"report-{label}.md").write_text(report(days))
    # Full history, recomputed from raw samples each time, for the dashboard.
    apps = []
    for kind, fname in (("week", "weekly.json"), ("month", "monthly.json")):
        rows = []
        for label, a, b in periods(kind):
            sm = summarize(a, b)
            if not sm.get("samples"):
                continue
            row = flat(sm, kind, label)
            row["complete"] = b <= time.time()
            rows.append(row)
            seen = set()
            for a_ in sm["top_ram"][:8] + sm["top_cpu"][:8] + sm["top_vram"][:5]:
                if a_["app"] in seen:
                    continue
                seen.add(a_["app"])
                apps.append({"kind": kind, "period": label, "app": a_["app"],
                             "ram_avg_gb": a_["ram_avg_gb"], "ram_peak_gb": a_["ram_peak_gb"],
                             "cpu_core_hours": a_["cpu_core_hours"], "vram_peak_mb": a_["vram_peak_mb"]})
        (out_dir / fname).write_text(json.dumps(rows, indent=1))
    (out_dir / "apps.json").write_text(json.dumps(apps, indent=1))
    print(f"exported to {out_dir}")


CONF_PATH = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "resmon" / "routines.env"
FIRE_URL = "https://api.anthropic.com/v1/claude_code/routines/{}/fire"
MAX_TEXT = 65536


def load_conf() -> dict[str, str]:
    conf = {}
    for line in (read(str(CONF_PATH)) or "").splitlines():
        k, sep, v = line.strip().partition("=")
        if sep and not k.startswith("#"):
            conf[k.strip()] = v.strip().strip('"').strip("'")
    return conf


def build_payload(kind: str) -> str:
    """JSON text for one review: the period just finished plus history for the dashboard."""
    import json
    hist: dict[str, list] = {}
    reviewed = None
    period_apps: list = []
    for k in ("week", "month"):
        rows = []
        for label, a, b in periods(k):
            sm = summarize(a, b)
            if not sm.get("samples"):
                continue
            row = flat(sm, k, label)
            row["complete"] = b <= time.time()
            rows.append(row)
            if k == kind and row["complete"]:
                reviewed = (label, a, b, sm)
        hist[k] = rows
    if reviewed is None:  # nothing finished yet: review what exists so far
        cur = [p for p in periods(kind)]
        if cur:
            label, a, b = cur[-1]
            reviewed = (label, a, b, summarize(a, b))
    payload = {"resmon_push": 1, "host": os.uname().nodename, "kind": kind,
               "generated": datetime.now().astimezone().isoformat(timespec="seconds")}
    if reviewed:
        label, a, b, sm = reviewed
        payload["period"] = label
        payload["complete"] = b <= time.time()
        payload["summary"] = sm
        seen = set()
        for a_ in sm.get("top_ram", [])[:8] + sm.get("top_cpu", [])[:8] + sm.get("top_vram", [])[:5]:
            if a_["app"] not in seen:
                seen.add(a_["app"])
                period_apps.append({"kind": kind, "period": label, "app": a_["app"],
                                    "ram_avg_gb": a_["ram_avg_gb"], "ram_peak_gb": a_["ram_peak_gb"],
                                    "cpu_core_hours": a_["cpu_core_hours"], "vram_peak_mb": a_["vram_peak_mb"]})
    payload["apps"] = period_apps
    payload["weekly"] = hist["week"]
    payload["monthly"] = hist["month"]
    text = json.dumps(payload, separators=(",", ":"))
    while len(text) > MAX_TEXT and len(payload["weekly"]) > 4:  # keep newest history if it grows too big
        payload["weekly"] = payload["weekly"][-max(4, len(payload["weekly"]) // 2):]
        payload["history_truncated"] = True
        text = json.dumps(payload, separators=(",", ":"))
    return text


def push(kind: str, dry_run: bool = False):
    import urllib.request, urllib.error, json
    text = build_payload(kind)
    if dry_run:
        print(text)
        return
    conf = load_conf()
    key = "WEEKLY" if kind == "week" else "MONTHLY"
    trig, token = conf.get(f"RESMON_{key}_TRIGGER"), conf.get(f"RESMON_{key}_TOKEN")
    if not trig or not token:
        sys.exit(f"resmon: set RESMON_{key}_TRIGGER and RESMON_{key}_TOKEN in {CONF_PATH}")
    req = urllib.request.Request(
        FIRE_URL.format(trig), data=json.dumps({"text": text}).encode(), method="POST",
        headers={"Authorization": f"Bearer {token}", "anthropic-version": "2026-10-21",
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            out = json.loads(r.read() or b"{}")
            print(f"fired {kind} review ({len(text):,} chars): {out.get('claude_code_session_url', out)}")
    except urllib.error.HTTPError as e:
        sys.exit(f"resmon: fire failed {e.code}: {e.read().decode(errors='replace')[:500]}")


def status() -> str:
    if not DB_PATH.exists():
        return f"No database at {DB_PATH}\n"
    db = sqlite3.connect(DB_PATH)
    n, first, last = db.execute("SELECT COUNT(*), MIN(ts), MAX(ts) FROM samples").fetchone()
    g = db.execute("SELECT COUNT(*) FROM groups").fetchone()[0]
    size = DB_PATH.stat().st_size / 1048576
    if not n:
        return "Database exists but has no samples yet.\n"
    age = time.time() - last
    return (f"{DB_PATH} ({size:.1f} MB)\n{n:,} samples, {g:,} app rows\n"
            f"first {datetime.fromtimestamp(first):%Y-%m-%d %H:%M}, last {datetime.fromtimestamp(last):%Y-%m-%d %H:%M} "
            f"({age:.0f}s ago)\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("collect")
    c.add_argument("--interval", type=float, default=float(os.environ.get("RESMON_INTERVAL", 30)))
    r = sub.add_parser("report")
    r.add_argument("--days", type=float, default=30)
    r.add_argument("--out", type=Path)
    sub.add_parser("status")
    e = sub.add_parser("export")
    e.add_argument("--out-dir", type=Path, default=Path(__file__).resolve().parent / "exports")
    pu = sub.add_parser("push", help="send a review to the Claude scheduled task")
    pu.add_argument("kind", choices=["week", "month"])
    pu.add_argument("--dry-run", action="store_true", help="print the payload instead of sending")
    a = ap.parse_args()
    if a.cmd == "push":
        push(a.kind, a.dry_run)
        return
    if a.cmd == "collect":
        Collector(a.interval).run()
    elif a.cmd == "export":
        export(a.out_dir)
    elif a.cmd == "report":
        text = report(a.days)
        if a.out:
            a.out.write_text(text)
            print(f"wrote {a.out}")
        else:
            sys.stdout.write(text)
    else:
        sys.stdout.write(status())


if __name__ == "__main__":
    main()
