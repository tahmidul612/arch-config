//! The sampler: system-wide metrics plus per-application usage every `interval` seconds.

use std::collections::HashMap;
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};
use std::thread::sleep;
use std::time::{Duration, Instant};

use anyhow::Result;
use rusqlite::{Connection, params};

use crate::gpu::{Gpu, GpuProc};
use crate::stats::{now, round};
use crate::{db, sys};

/// A group is stored for a sample if it crosses any of these, plus the top N by memory.
const MIN_MEM_KB: u64 = 100 * 1024;
const MIN_CPU_PCT: f64 = 2.0;
const TOP_N_ALWAYS: usize = 12;
/// About hourly at the default interval. PASSIVE never waits on readers.
const CHECKPOINT_EVERY: u64 = 120;
/// Samples between real PSS reads of a process. Reading smaps_rollup makes the kernel walk the
/// page tables (~0.4 s of system CPU per full scan); in between, RSS x the last PSS/RSS ratio.
const PSS_EVERY: u32 = 10;

#[derive(Default)]
struct Group {
    nprocs: u32,
    mem_kb: u64,
    cpu_pct: f64,
    vram_mb: f64,
    sm_pct: f64,
    /// Memory per process name, for the "biggest processes" detail column.
    members: HashMap<String, u64>,
}

/// What the collector remembers about a process between samples.
struct Proc {
    /// Start time in ticks; tells a reused pid apart.
    start: u64,
    ticks: u64,
    /// PSS/RSS at the last real read; None until the first one.
    pss_ratio: Option<f64>,
    /// Samples until the next real PSS read.
    pss_due: u32,
}

struct Collector {
    db: Connection,
    gpu: Gpu,
    buf: String,
    prev_cpu: (u64, u64),
    prev_swap: (i64, i64),
    prev_procs: HashMap<u32, Proc>,
    prev_t: Instant,
}

impl Collector {
    fn new() -> Result<Self> {
        let mut buf = String::new();
        let mut c = Self {
            db: db::open_rw()?,
            gpu: Gpu::default(),
            prev_cpu: sys::cpu_times(&mut buf),
            prev_swap: sys::swap_pages(&mut buf),
            buf,
            prev_procs: HashMap::new(),
            prev_t: Instant::now(),
        };
        c.scan_procs(None, &HashMap::new()); // prime the per-process CPU counters
        Ok(c)
    }

    /// Walks /proc. With `dt` = None only the CPU counters are recorded.
    fn scan_procs(&mut self, dt: Option<f64>, gpu: &HashMap<u32, GpuProc>) -> HashMap<String, Group> {
        let mut groups: HashMap<String, Group> = HashMap::new();
        let mut seen = HashMap::with_capacity(self.prev_procs.len());
        let Ok(dir) = std::fs::read_dir("/proc") else { return groups };
        let page_kb = sys::page_kb();
        for entry in dir.flatten() {
            let fname = entry.file_name();
            let Some(pid_s) = fname.to_str() else { continue };
            let Ok(pid) = pid_s.parse::<u32>() else { continue };
            if !sys::read_into(&format!("/proc/{pid_s}/stat"), &mut self.buf) {
                continue; // exited
            }
            let Some(st) = sys::parse_stat(&self.buf) else { continue };
            let prev = self.prev_procs.get(&pid).filter(|p| p.start == st.start);
            let mut cur = Proc { start: st.start, ticks: st.ticks, pss_ratio: None, pss_due: 0 };
            if let Some(p) = prev {
                (cur.pss_ratio, cur.pss_due) = (p.pss_ratio, p.pss_due);
            }
            let (Some(dt), true) = (dt, st.rss_pages > 0 || gpu.contains_key(&pid)) else {
                seen.insert(pid, cur); // priming pass, or a kernel thread
                continue;
            };
            let dticks = prev.map_or(0, |p| st.ticks.saturating_sub(p.ticks));
            let comm = st.comm.to_owned();
            let rss_kb = st.rss_pages * page_kb;
            let mem = match cur.pss_ratio {
                Some(ratio) if cur.pss_due > 0 => {
                    cur.pss_due -= 1;
                    #[expect(clippy::cast_possible_truncation, clippy::cast_sign_loss, reason = "ratio in 0..=1 of a KiB count")]
                    let est = (rss_kb as f64 * ratio) as u64;
                    est
                }
                _ => {
                    // PSS is unreadable for other users' processes: RSS then, ratio 1.
                    let pss = sys::pss_kb(pid_s, &mut self.buf).unwrap_or(rss_kb);
                    cur.pss_ratio = Some(if rss_kb > 0 { pss as f64 / rss_kb as f64 } else { 1.0 });
                    // The first re-read is staggered by pid so the cost spreads over samples.
                    cur.pss_due = if prev.and_then(|p| p.pss_ratio).is_some() { PSS_EVERY - 1 } else { pid % PSS_EVERY };
                    pss
                }
            };
            seen.insert(pid, cur);
            sys::read_into(&format!("/proc/{pid_s}/cgroup"), &mut self.buf);
            let g = groups.entry(sys::group_name(&self.buf, &comm)).or_default();
            g.nprocs += 1;
            g.mem_kb += mem;
            g.cpu_pct += 100.0 * dticks as f64 / sys::CLK_TCK / dt;
            if let Some(gp) = gpu.get(&pid) {
                g.vram_mb += gp.vram_mb;
                g.sm_pct += gp.sm_pct;
            }
            *g.members.entry(comm).or_default() += mem;
        }
        self.prev_procs = seen;
        groups
    }

    fn sample(&mut self) -> Result<()> {
        let t = Instant::now();
        let dt = (t - self.prev_t).as_secs_f64();
        self.prev_t = t;
        let ts = now();
        let buf = &mut self.buf;

        let (busy, total) = sys::cpu_times(buf);
        let (pb, pt) = std::mem::replace(&mut self.prev_cpu, (busy, total));
        let cpu_pct = if total > pt { 100.0 * busy.saturating_sub(pb) as f64 / (total - pt) as f64 } else { 0.0 };
        let mem = sys::meminfo(buf);
        let swap = sys::swap_pages(buf);
        let (pin, pout) = std::mem::replace(&mut self.prev_swap, swap);
        let (psi_cpu, _) = sys::psi("cpu", buf);
        let (psi_mem, psi_mem_full) = sys::psi("memory", buf);
        let (psi_io, _) = sys::psi("io", buf);
        let load1 = sys::load1(buf);
        let gpu = self.gpu.sample();
        let g = &gpu.system;

        let groups = self.scan_procs(Some(dt), &gpu.procs);
        let mut by_mem: Vec<(&String, &Group)> = groups.iter().collect();
        by_mem.sort_unstable_by_key(|(_, g)| std::cmp::Reverse(g.mem_kb));

        let tx = self.db.transaction()?;
        tx.prepare_cached("INSERT OR REPLACE INTO samples VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)")?.execute(params![
            ts, dt, cpu_pct, load1, mem.total, mem.avail, mem.cached, mem.shmem, mem.swap_used,
            swap.0 - pin, swap.1 - pout, psi_cpu, psi_mem, psi_mem_full, psi_io,
            g.util, g.mem_util, g.vram_used_mb, g.vram_total_mb, g.power_w, g.temp_c,
        ])?;
        {
            let mut ins = tx.prepare_cached("INSERT INTO groups VALUES (?,?,?,?,?,?,?,?)")?;
            for (rank, (name, v)) in by_mem.into_iter().enumerate() {
                if rank >= TOP_N_ALWAYS && v.mem_kb < MIN_MEM_KB && v.cpu_pct < MIN_CPU_PCT && v.vram_mb <= 0.0 {
                    continue;
                }
                let mut top: Vec<(&String, &u64)> = v.members.iter().collect();
                top.sort_unstable_by(|a, b| b.1.cmp(a.1));
                let detail = top.iter().take(3).map(|(c, m)| format!("{c}:{}M", *m / 1024)).collect::<Vec<_>>().join(", ");
                let cpu = round(v.cpu_pct, 2);
                ins.execute(params![ts, name, detail, v.nprocs, i64::try_from(v.mem_kb)?, cpu, v.vram_mb, v.sm_pct])?;
            }
        }
        tx.commit()?;
        Ok(())
    }
}

pub fn run(interval: f64) -> Result<()> {
    let stop = Arc::new(AtomicBool::new(false));
    for sig in [signal_hook::consts::SIGTERM, signal_hook::consts::SIGINT] {
        signal_hook::flag::register(sig, Arc::clone(&stop))?;
    }
    let mut c = Collector::new()?;
    db::event(&c.db, now(), "start", &format!("interval={interval}s pid={} (rust)", std::process::id()))?;

    let pause = |secs: f64| {
        let end = Instant::now() + Duration::from_secs_f64(secs.max(0.0));
        while !stop.load(Ordering::Relaxed) && Instant::now() < end {
            sleep((end - Instant::now()).min(Duration::from_secs(1)));
        }
    };
    pause(interval.min(5.0));
    let mut taken = 0u64;
    while !stop.load(Ordering::Relaxed) {
        let t0 = Instant::now();
        // Keep logging through transient oddities (a locked DB, a weird /proc entry).
        if let Err(e) = c.sample() {
            eprintln!("resmon: sample failed: {e:#}");
        }
        taken += 1;
        if taken.is_multiple_of(CHECKPOINT_EVERY) {
            c.db.query_row("PRAGMA wal_checkpoint(PASSIVE)", [], |_| Ok(()))?;
        }
        pause((interval - t0.elapsed().as_secs_f64()).max(1.0));
    }
    db::event(&c.db, now(), "stop", "")?;
    Ok(())
}
