//! The human-readable markdown report: percentiles, "would a smaller kit fit?", top apps, bottlenecks.

use std::fmt::Write as _;

use anyhow::Result;
use rusqlite::{Connection, params};

use crate::stats::{Dist, HEADROOM_GB, KB_PER_GB, Period, Sample, local, top_at};
use crate::sys;

fn gb1(kb: f64) -> String {
    format!("{:.1} GB", kb / KB_PER_GB)
}

fn gbs(kb: Option<f64>) -> String {
    kb.map_or_else(|| "–".into(), gb1)
}

fn f1(x: Option<f64>, unit: &str) -> String {
    x.map_or_else(|| "–".into(), |x| format!("{x:.1}{unit}"))
}

fn share(d: &Dist, pred: impl Fn(f64) -> bool) -> String {
    d.share(pred).map_or_else(|| "–".into(), |s| format!("{s:.2}%"))
}

fn thousands(n: usize) -> String {
    let s = n.to_string();
    let mut out = String::with_capacity(s.len() + s.len() / 3);
    for (i, c) in s.chars().enumerate() {
        if i > 0 && (s.len() - i).is_multiple_of(3) {
            out.push(',');
        }
        out.push(c);
    }
    out
}

/// Whether a sample counts as constrained for one resource.
type Constrained<'a> = &'a dyn Fn(&Sample) -> bool;

/// A markdown table row: `| a | b | c |`.
fn row<const N: usize>(cells: [&str; N]) -> String {
    let mut out = String::from("|");
    for c in cells {
        // An empty cell is `| |`, not `|  |`.
        out.push(' ');
        if !c.is_empty() {
            out.push_str(c);
            out.push(' ');
        }
        out.push('|');
    }
    out
}

/// A `label | p50 | p95 | p99 | max` table row.
fn quartet(label: &str, d: &Dist, fmt: impl Fn(Option<f64>) -> String) -> String {
    row([label, &fmt(d.pct(50.0)), &fmt(d.pct(95.0)), &fmt(d.pct(99.0)), &fmt(d.max())])
}

/// Most frequent top-2 apps (by `col`) across the given sample timestamps.
fn culprits(db: &Connection, ts: &[i64], col: &str) -> Result<String> {
    let sql = format!(
        "SELECT grp, COUNT(*) AS c FROM (
             SELECT grp, ROW_NUMBER() OVER (PARTITION BY ts ORDER BY {col} DESC) AS rn
             FROM groups WHERE ts IN (SELECT value FROM json_each(?1)))
         WHERE rn <= 2 GROUP BY grp ORDER BY c DESC, grp LIMIT 5"
    );
    let json = serde_json::to_string(ts)?;
    let mut st = db.prepare(&sql)?;
    let rows = st.query_map(params![json], |r| Ok(format!("{} ({})", r.get::<_, String>(0)?, r.get::<_, i64>(1)?)))?;
    Ok(rows.collect::<rusqlite::Result<Vec<_>>>()?.join(", "))
}

#[expect(clippy::too_many_lines, reason = "one linear document, section by section")]
pub fn render(db: &Connection, p: &Period) -> Result<String> {
    let s = &p.samples;
    let (Some(first), Some(last)) = (s.first(), s.last()) else {
        return Ok("No samples yet. Is the collector running? `systemctl --user status resmon`\n".into());
    };
    let ncpu = sys::ncpu() as f64;
    let n = s.len() as f64;
    let used = p.dist(|r| Some(r.used_kb));
    let swap = p.dist(|r| r.swap_used_kb);
    let cpu = p.dist(|r| Some(r.cpu));
    let gpu = p.dist(|r| r.gpu_util);
    let vram = p.dist(|r| r.vram_used_mb);
    let psi_mem = p.dist(|r| r.psi_mem);
    let psi_cpu = p.dist(|r| r.psi_cpu);
    let vram_total = p.vram_total();
    let page_kb = sys::page_kb() as f64;
    let fmt = "%Y-%m-%d %H:%M";
    let mut o = String::new();
    let w = &mut o;

    writeln!(w, "# Resource report ({} → {})\n", local(first.ts).strftime(fmt), local(last.ts).strftime(fmt))?;
    let vram_hw = vram_total.map_or_else(|| ".".into(), |v| format!(", {:.0} GB VRAM.", v / 1024.0));
    writeln!(
        w,
        "{} samples over {:.1} h of logged time (only while the PC was on). {ncpu} logical CPUs, {} RAM{vram_hw}\n",
        thousands(s.len()),
        (last.ts - first.ts) as f64 / 3600.0,
        gb1(last.mem_total_kb),
    )?;

    // ---------------- RAM
    writeln!(w, "## RAM\n")?;
    writeln!(w, "\"Used\" here is total minus MemAvailable, i.e. memory that could not be dropped. Page cache is excluded because Linux fills spare RAM with it and gives it back on demand.\n")?;
    writeln!(w, "| | p50 | p95 | p99 | max |\n|---|---|---|---|---|")?;
    writeln!(w, "{}", quartet("Used", &used, gbs))?;
    writeln!(w, "{}", quartet("Swap/zram used", &swap, gbs))?;
    let cache = p.dist(|r| r.cached_kb);
    writeln!(w, "{}\n", row(["Page cache", &gbs(cache.pct(50.0)), "", "", &gbs(cache.max())]))?;
    writeln!(w, "Would a smaller kit have fit? (time the non-reclaimable usage was above each line; leave ~4 GB headroom for cache and the kernel)\n")?;
    writeln!(w, "| Capacity | time over capacity − 4 GB | time over capacity |\n|---|---|---|")?;
    for cap in [16.0, 24.0, 32.0, 48.0, 64.0, 96.0] {
        let ck = cap * KB_PER_GB;
        let near = share(&used, |x| x > ck - HEADROOM_GB * KB_PER_GB);
        writeln!(w, "{}", row([&format!("{cap} GB"), &near, &share(&used, |x| x > ck)]))?;
    }
    let swapped = s.iter().filter_map(|r| r.swapout_pages).sum::<f64>() * page_kb;
    writeln!(
        w,
        "\nMemory pressure (PSI some avg10): p95 {}, max {}; full p99 {}. Swapped out in total: {}.\n",
        f1(psi_mem.pct(95.0), "%"),
        f1(psi_mem.max(), "%"),
        f1(p.dist(|r| r.psi_mem_full).pct(99.0), "%"),
        gb1(swapped),
    )?;

    // ---------------- CPU
    writeln!(w, "## CPU\n")?;
    writeln!(w, "| | p50 | p95 | p99 | max |\n|---|---|---|---|---|")?;
    writeln!(w, "{}", quartet("Whole-CPU busy", &cpu, |x| f1(x, "%")))?;
    writeln!(w, "{}", quartet("≈ threads busy", &cpu, |x| f1(x.map(|x| x * ncpu / 100.0), "")))?;
    writeln!(w, "{}\n", quartet("CPU pressure (PSI some)", &psi_cpu, |x| f1(x, "%")))?;
    writeln!(
        w,
        "Time above 50% busy: {}; above 80%: {}; above 95%: {}. A 30 s average hides short bursts, so read p99/max together with PSI: PSI says how often runnable work had to wait for a core.\n",
        share(&cpu, |x| x > 50.0),
        share(&cpu, |x| x > 80.0),
        share(&cpu, |x| x > 95.0),
    )?;

    // ---------------- GPU
    writeln!(w, "## GPU\n")?;
    if gpu.is_empty() {
        writeln!(w, "No NVIDIA data recorded (NVML unavailable).\n")?;
    } else {
        writeln!(w, "| | p50 | p95 | p99 | max |\n|---|---|---|---|---|")?;
        writeln!(w, "{}", quartet("Utilisation", &gpu, |x| f1(x, "%")))?;
        writeln!(w, "{}\n", quartet("VRAM used", &vram, |x| f1(x, " MB")))?;
        if let Some(t) = vram_total {
            writeln!(
                w,
                "Time with VRAM > 90% full: {}; GPU > 90% busy: {}; GPU idle (<5%): {}.\n",
                share(&vram, |x| x > 0.9 * t),
                share(&gpu, |x| x > 90.0),
                share(&gpu, |x| x < 5.0),
            )?;
        }
    }

    // ---------------- attribution
    writeln!(w, "## Where the RAM goes\n")?;
    writeln!(w, "Average share of RAM over the whole period (PSS where readable, so shared libraries are split fairly).\n")?;
    writeln!(w, "| App / service | avg (all time) | avg when running | peak | running | biggest processes |\n|---|---|---|---|---|---|")?;
    for a in p.top(|a| a.avg_mem_all).into_iter().take(20) {
        let running = format!("{:.0}%", 100.0 * a.presence);
        writeln!(w, "{}", row([&a.grp, &gb1(a.avg_mem_all), &gb1(a.avg_mem), &gb1(a.max_mem), &running, &a.detail]))?;
    }

    writeln!(w, "\n## Where the CPU goes\n")?;
    writeln!(w, "Core-hours = total CPU time consumed (1 core-hour = one thread fully busy for an hour).\n")?;
    writeln!(w, "| App / service | core-hours | share | peak (% of one core) |\n|---|---|---|---|")?;
    let total_core_h = p.apps.iter().map(|a| a.core_hours).sum::<f64>().max(f64::MIN_POSITIVE);
    for a in p.top(|a| a.core_hours).into_iter().take(20) {
        writeln!(w, "| {} | {:.1} | {:.1}% | {:.0}% |", a.grp, a.core_hours, 100.0 * a.core_hours / total_core_h, a.max_cpu)?;
    }

    if !gpu.is_empty() {
        writeln!(w, "\n## Where the GPU goes\n")?;
        writeln!(w, "| App / service | peak VRAM | avg VRAM (all time) | peak SM % |\n|---|---|---|---|")?;
        for a in p.top(|a| a.max_vram).into_iter().filter(|a| a.max_vram > 0.0).take(15) {
            writeln!(w, "| {} | {:.0} MB | {:.0} MB | {:.0}% |", a.grp, a.max_vram, a.avg_vram_all, a.max_sm)?;
        }
    }

    // ---------------- how it ties together
    writeln!(w, "\n## How it ties together\n")?;
    writeln!(w, "### Peak-RAM moments\n")?;
    let mut peaks: Vec<&Sample> = s.iter().collect();
    peaks.sort_by(|a, b| b.used_kb.total_cmp(&a.used_kb));
    let mut picked: Vec<&Sample> = Vec::new();
    for r in peaks {
        // Five distinct moments, at least an hour apart.
        if picked.iter().all(|q| (r.ts - q.ts).abs() > 3600) {
            picked.push(r);
            if picked.len() == 5 {
                break;
            }
        }
    }
    for r in picked {
        let apps = top_at(db, r.ts, 6)?.iter().map(|(g, m)| format!("{g} {:.1}G", m / KB_PER_GB)).collect::<Vec<_>>().join(", ");
        writeln!(
            w,
            "- **{}**: {} used, CPU {:.0}%, GPU {}, VRAM {}. Top: {apps}",
            local(r.ts).strftime("%a %b %d %H:%M"),
            gb1(r.used_kb),
            r.cpu,
            f1(r.gpu_util, "%"),
            f1(r.vram_used_mb, " MB"),
        )?;
    }

    writeln!(w, "\n### Bottleneck moments\n")?;
    writeln!(w, "A sample counts as constrained when the hardware was making work wait: CPU PSI > 20% or CPU > 90% (CPU), memory PSI > 5% or swapping out > 100 MB per sample (RAM), GPU > 95% busy or VRAM > 95% full (GPU).\n")?;
    writeln!(w, "| Resource | constrained time | ≈ hours | apps most often on top during those moments |\n|---|---|---|---|")?;
    let vt = vram_total.unwrap_or(f64::INFINITY);
    let cats: [(&str, &str, Constrained); 3] = [
        ("CPU", "cpu_pct", &|r| r.psi_cpu.unwrap_or(0.0) > 20.0 || r.cpu > 90.0),
        ("RAM", "mem_kb", &|r| r.psi_mem.unwrap_or(0.0) > 5.0 || r.swapout_pages.unwrap_or(0.0) * page_kb > 100.0 * 1024.0),
        ("GPU", "vram_mb", &|r| r.gpu_util.unwrap_or(0.0) > 95.0 || r.vram_used_mb.unwrap_or(0.0) > 0.95 * vt),
    ];
    for (name, col, pred) in cats {
        let hit: Vec<i64> = s.iter().filter(|r| pred(r)).map(|r| r.ts).collect();
        let who = if hit.is_empty() { String::new() } else { culprits(db, &hit, col)? };
        let h = hit.len() as f64;
        writeln!(w, "| {name} | {:.2}% | {:.1} | {who} |", 100.0 * h / n, h * p.avg_dt / 3600.0)?;
    }

    writeln!(w, "\n### Daily rhythm (average by hour of day)\n")?;
    writeln!(w, "| Hour | samples | RAM used | CPU | GPU |\n|---|---|---|---|---|")?;
    // (count, used, cpu, gpu sum, gpu count) per local hour.
    let mut hours = [(0usize, 0.0, 0.0, 0.0, 0usize); 24];
    for r in s {
        let h = &mut hours[usize::try_from(local(r.ts).hour()).unwrap_or(0)];
        h.0 += 1;
        h.1 += r.used_kb;
        h.2 += r.cpu;
        if let Some(g) = r.gpu_util {
            h.3 += g;
            h.4 += 1;
        }
    }
    for (hour, &(cnt, used, cpu, gsum, gcnt)) in hours.iter().enumerate().filter(|(_, h)| h.0 > 0) {
        let c = cnt as f64;
        let g = if gcnt > 0 { f1(Some(gsum / gcnt as f64), "%") } else { "–".into() };
        writeln!(w, "| {hour:02}:00 | {cnt} | {} | {:.0}% | {g} |", gb1(used / c), cpu / c)?;
    }

    let ooms = sys::oom_events(p.since);
    writeln!(w, "\n## OOM kills\n")?;
    if ooms.is_empty() {
        writeln!(w, "None found in the kernel journal for this period.")?;
    } else {
        for l in ooms {
            writeln!(w, "- `{l}`")?;
        }
    }

    // ---------------- verdict
    writeln!(w, "\n## Quick read\n")?;
    let (p99, mx) = (used.pct(99.0).unwrap_or(0.0), used.max().unwrap_or(0.0));
    let ram = [32.0, 48.0].into_iter().find_map(|cap| {
        let line = (cap - HEADROOM_GB) * KB_PER_GB;
        if mx < line {
            Some(format!("- **RAM:** peak non-reclaimable use was {}, so {cap} GB would have covered every sample with headroom.", gb1(mx)))
        } else if p99 < line {
            Some(format!(
                "- **RAM:** {cap} GB covers 99% of the time (p99 {}), but the peak was {}. Check the peak-RAM moments above to see whether those were one-off jobs.",
                gb1(p99),
                gb1(mx)
            ))
        } else {
            None
        }
    });
    let ram = ram.unwrap_or_else(|| format!("- **RAM:** p99 is {}. You regularly use more than 44 GB, so 64 GB is earning its keep.", gb1(p99)));
    writeln!(w, "{ram}")?;
    if psi_mem.pct(95.0).unwrap_or(0.0) > 5.0 {
        writeln!(w, "- **RAM pressure:** memory PSI is non-trivial; you are short on RAM at times, not over-provisioned.")?;
    }
    let (cpu99, cpsi95) = (cpu.pct(99.0).unwrap_or(0.0), psi_cpu.pct(95.0).unwrap_or(0.0));
    if cpu99 < 50.0 && cpsi95 < 10.0 {
        writeln!(w, "- **CPU:** p99 {cpu99:.0}% busy and low pressure. A CPU with fewer threads would rarely be the limit.")?;
    } else if cpsi95 > 20.0 {
        writeln!(w, "- **CPU:** pressure p95 {cpsi95:.0}%. Work often waits for a core, so more cores/faster cores would help.")?;
    } else {
        writeln!(w, "- **CPU:** p99 {cpu99:.0}% busy. Busy at times but rarely saturated.")?;
    }
    if let (Some(t), Some(vmax)) = (vram_total, vram.max()) {
        let verdict = if vmax > 0.95 * t { "VRAM has hit the ceiling, so models/scenes are limited by it." } else { "VRAM has room to spare." };
        writeln!(w, "- **GPU:** p95 utilisation {}, peak VRAM {vmax:.0}/{t:.0} MB. {verdict}", f1(gpu.pct(95.0), "%"))?;
    }
    Ok(o)
}

#[cfg(test)]
mod tests {
    #[test]
    fn thousands_groups_digits() {
        assert_eq!([0, 999, 1000, 68121, 1_234_567].map(super::thousands), ["0", "999", "1,000", "68,121", "1,234,567"]);
    }
}
