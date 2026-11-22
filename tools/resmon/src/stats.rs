//! One period's samples and per-app aggregates, plus the JSON summary built from them.

use std::collections::HashMap;

use anyhow::{Context, Result};
use jiff::civil::Date;
use jiff::tz::TimeZone;
use jiff::{Timestamp, ToSpan, ZonedDateTime};
use rusqlite::{Connection, params};
use serde::Serialize;

use crate::sys;

pub const KB_PER_GB: f64 = 1_048_576.0;
/// RAM left for page cache and the kernel when asking "would a kit of N GB have fit?".
pub const HEADROOM_GB: f64 = 4.0;

pub fn local(ts: i64) -> ZonedDateTime {
    Timestamp::from_second(ts).unwrap_or_default().to_zoned_datetime(TimeZone::system())
}

pub fn now() -> i64 {
    Timestamp::now().as_second()
}

pub fn round(x: f64, digits: i32) -> f64 {
    let m = 10f64.powi(digits);
    (x * m).round() / m
}

fn r2(x: Option<f64>) -> Option<f64> {
    x.map(|v| round(v, 2))
}

/// Rounds to a whole number for JSON fields that are integers.
#[expect(clippy::cast_possible_truncation, reason = "MB and percent values, far inside i64")]
pub fn int(x: f64) -> i64 {
    x.round() as i64
}

pub fn gb(kb: f64) -> f64 {
    round(kb / KB_PER_GB, 2)
}

/// Sorted values with missing ones dropped, for percentiles and time-over-threshold shares.
pub struct Dist(Vec<f64>);

impl Dist {
    pub fn new(values: impl IntoIterator<Item = Option<f64>>) -> Self {
        let mut v: Vec<f64> = values.into_iter().flatten().collect();
        v.sort_unstable_by(f64::total_cmp);
        Self(v)
    }

    /// Linear-interpolated percentile, `p` in 0..=100.
    pub fn pct(&self, p: f64) -> Option<f64> {
        let v = &self.0;
        let last = v.len().checked_sub(1)?;
        let k = last as f64 * p / 100.0;
        #[expect(clippy::cast_possible_truncation, clippy::cast_sign_loss, reason = "k is in 0..=last")]
        let lo = k as usize;
        let hi = (lo + 1).min(last);
        Some(v[lo] + (v[hi] - v[lo]) * (k - lo as f64))
    }

    pub fn max(&self) -> Option<f64> {
        self.0.last().copied()
    }

    /// Percent of values matching `pred`; None when there are no values.
    pub fn share(&self, pred: impl Fn(f64) -> bool) -> Option<f64> {
        (!self.0.is_empty()).then(|| 100.0 * self.0.iter().filter(|&&x| pred(x)).count() as f64 / self.0.len() as f64)
    }

    pub fn is_empty(&self) -> bool {
        self.0.is_empty()
    }
}

pub struct Sample {
    pub ts: i64,
    pub interval_s: f64,
    pub cpu: f64,
    pub mem_total_kb: f64,
    /// total - MemAvailable: memory that could not be dropped.
    pub used_kb: f64,
    pub cached_kb: Option<f64>,
    pub swap_used_kb: Option<f64>,
    pub swapout_pages: Option<f64>,
    pub psi_cpu: Option<f64>,
    pub psi_mem: Option<f64>,
    pub psi_mem_full: Option<f64>,
    pub gpu_util: Option<f64>,
    pub vram_used_mb: Option<f64>,
    pub vram_total_mb: Option<f64>,
}

pub struct App {
    pub grp: String,
    /// Share of samples in which the app was recorded, 0..=1.
    pub presence: f64,
    /// Average memory over the whole period (0 while not running), KiB.
    pub avg_mem_all: f64,
    pub avg_mem: f64,
    pub max_mem: f64,
    pub core_hours: f64,
    pub max_cpu: f64,
    pub max_vram: f64,
    pub avg_vram_all: f64,
    pub max_sm: f64,
    /// Biggest member processes at the app's memory peak.
    pub detail: String,
}

pub struct Period {
    pub since: i64,
    pub until: i64,
    pub samples: Vec<Sample>,
    pub apps: Vec<App>,
    pub avg_dt: f64,
}

/// Running per-app totals for one period.
#[derive(Default)]
struct Acc {
    count: u32,
    sum_mem: f64,
    max_mem: f64,
    sum_cpu: f64,
    max_cpu: f64,
    sum_vram: f64,
    max_vram: f64,
    max_sm: f64,
    /// `detail` of the row with the highest memory.
    detail: String,
}

fn load_samples(db: &Connection, since: i64, until: i64) -> Result<Vec<Sample>> {
    let mut st = db.prepare_cached(
        "SELECT ts, interval_s, cpu_pct, mem_total_kb, mem_avail_kb, mem_cached_kb, swap_used_kb, swapout_pages,
                psi_cpu_some, psi_mem_some, psi_mem_full, gpu_util, vram_used_mb, vram_total_mb
         FROM samples WHERE ts >= ?1 AND ts < ?2 ORDER BY ts",
    )?;
    let rows = st.query_map(params![since, until], |r| {
        let total: f64 = r.get::<_, Option<f64>>(3)?.unwrap_or(0.0);
        Ok(Sample {
            ts: r.get(0)?,
            interval_s: r.get::<_, Option<f64>>(1)?.unwrap_or(0.0),
            cpu: r.get::<_, Option<f64>>(2)?.unwrap_or(0.0),
            mem_total_kb: total,
            used_kb: total - r.get::<_, Option<f64>>(4)?.unwrap_or(total),
            cached_kb: r.get(5)?,
            swap_used_kb: r.get(6)?,
            swapout_pages: r.get(7)?,
            psi_cpu: r.get(8)?,
            psi_mem: r.get(9)?,
            psi_mem_full: r.get(10)?,
            gpu_util: r.get(11)?,
            vram_used_mb: r.get(12)?,
            vram_total_mb: r.get(13)?,
        })
    })?;
    Ok(rows.collect::<rusqlite::Result<_>>()?)
}

/// Per-app totals for every range from ONE pass over `groups`. Per-range `GROUP BY` queries
/// re-read the same rows for each week, month and window (several seconds per export).
fn load_apps(db: &Connection, ranges: &[(i64, i64)]) -> Result<Vec<Vec<(String, Acc)>>> {
    let (Some(lo), Some(hi)) = (ranges.iter().map(|r| r.0).min(), ranges.iter().map(|r| r.1).max()) else { return Ok(Vec::new()) };
    let mut names: HashMap<String, usize> = HashMap::new();
    let mut accs: Vec<HashMap<usize, Acc>> = ranges.iter().map(|_| HashMap::new()).collect();
    let mut st = db.prepare("SELECT ts, grp, mem_kb, cpu_pct, vram_mb, gpu_sm_pct, detail FROM groups WHERE ts >= ?1 AND ts < ?2")?;
    let mut rows = st.query(params![lo, hi])?;
    while let Some(r) = rows.next()? {
        let ts: i64 = r.get(0)?;
        let grp = r.get_ref(1)?.as_str()?;
        // Look up by &str first so the common case doesn't allocate.
        let id = if let Some(&id) = names.get(grp) {
            id
        } else {
            let id = names.len();
            names.insert(grp.to_owned(), id);
            id
        };
        let f = |i| r.get::<_, Option<f64>>(i).map(Option::unwrap_or_default);
        let (mem, cpu, vram, sm) = (f(2)?, f(3)?, f(4)?, f(5)?);
        for (range, acc) in ranges.iter().zip(&mut accs) {
            if ts < range.0 || ts >= range.1 {
                continue;
            }
            let a = acc.entry(id).or_default();
            a.count += 1;
            a.sum_mem += mem;
            a.sum_cpu += cpu;
            a.sum_vram += vram;
            a.max_cpu = a.max_cpu.max(cpu);
            a.max_vram = a.max_vram.max(vram);
            a.max_sm = a.max_sm.max(sm);
            if mem > a.max_mem || a.count == 1 {
                a.max_mem = mem;
                a.detail.clear();
                a.detail.push_str(r.get_ref(6)?.as_str_or_null()?.unwrap_or_default());
            }
        }
    }
    let mut by_id: Vec<String> = vec![String::new(); names.len()];
    for (name, id) in names {
        by_id[id] = name;
    }
    Ok(accs.into_iter().map(|m| m.into_iter().map(|(id, a)| (by_id[id].clone(), a)).collect()).collect())
}

impl Period {
    /// Samples and per-app aggregates for `since <= ts < until`.
    pub fn load(db: &Connection, since: i64, until: i64) -> Result<Self> {
        Self::load_many(db, &[(since, until)])?.pop().context("load_many returns one period per range")
    }

    /// Several periods, sharing one scan of the per-app table.
    pub fn load_many(db: &Connection, ranges: &[(i64, i64)]) -> Result<Vec<Self>> {
        let apps = load_apps(db, ranges)?;
        ranges
            .iter()
            .zip(apps)
            .map(|(&(since, until), accs)| {
                let samples = load_samples(db, since, until)?;
                let n = samples.len().max(1) as f64;
                let avg_dt = samples.iter().map(|s| s.interval_s).sum::<f64>() / n;
                let apps = accs
                    .into_iter()
                    .map(|(grp, a)| {
                        let count = f64::from(a.count);
                        App {
                            grp,
                            presence: count / n,
                            avg_mem_all: a.sum_mem / n,
                            avg_mem: a.sum_mem / count,
                            max_mem: a.max_mem,
                            core_hours: a.sum_cpu / 100.0 * avg_dt / 3600.0,
                            max_cpu: a.max_cpu,
                            max_vram: a.max_vram,
                            avg_vram_all: a.sum_vram / n,
                            max_sm: a.max_sm,
                            detail: a.detail,
                        }
                    })
                    .collect();
                Ok(Self { since, until, samples, apps, avg_dt })
            })
            .collect()
    }

    pub fn dist(&self, f: impl Fn(&Sample) -> Option<f64>) -> Dist {
        Dist::new(self.samples.iter().map(f))
    }

    pub fn vram_total(&self) -> Option<f64> {
        self.samples.iter().rev().find_map(|s| s.vram_total_mb.filter(|&v| v > 0.0))
    }

    /// Apps sorted by `key`, descending; ties by name so the output is stable.
    pub fn top(&self, key: impl Fn(&App) -> f64) -> Vec<&App> {
        let mut v: Vec<&App> = self.apps.iter().collect();
        v.sort_by(|a, b| key(b).total_cmp(&key(a)).then_with(|| a.grp.cmp(&b.grp)));
        v
    }

    pub fn peak_ram(&self) -> Option<&Sample> {
        self.samples.iter().max_by(|a, b| a.used_kb.total_cmp(&b.used_kb))
    }
}

/// Top groups by memory at one sample: (group, mem KiB).
pub fn top_at(db: &Connection, ts: i64, limit: u32) -> Result<Vec<(String, f64)>> {
    let mut st = db.prepare_cached("SELECT grp, mem_kb FROM groups WHERE ts = ?1 ORDER BY mem_kb DESC LIMIT ?2")?;
    let rows = st.query_map(params![ts, limit], |r| Ok((r.get(0)?, r.get::<_, Option<f64>>(1)?.unwrap_or(0.0))))?;
    Ok(rows.collect::<rusqlite::Result<_>>()?)
}

// --------------------------------------------------------------------- JSON summary

#[derive(Serialize)]
pub struct Hardware {
    threads: usize,
    ram_gb: f64,
    vram_gb: Option<f64>,
}

#[derive(Serialize)]
pub struct TimeOver {
    #[serde(rename = "16GB")]
    gb16: f64,
    #[serde(rename = "24GB")]
    gb24: f64,
    #[serde(rename = "32GB")]
    gb32: f64,
    #[serde(rename = "48GB")]
    gb48: f64,
}

#[derive(Serialize)]
pub struct Ram {
    used_p50_gb: Option<f64>,
    used_p95_gb: Option<f64>,
    used_p99_gb: Option<f64>,
    used_max_gb: Option<f64>,
    swap_max_gb: f64,
    swapped_out_gb: f64,
    psi_some_p95: Option<f64>,
    /// Percent of time above capacity - 4 GB, per capacity.
    pct_time_over: TimeOver,
}

#[derive(Serialize)]
pub struct Cpu {
    busy_p50: Option<f64>,
    busy_p95: Option<f64>,
    busy_p99: Option<f64>,
    busy_max: Option<f64>,
    psi_some_p95: Option<f64>,
    pct_time_over_80: f64,
}

#[derive(Serialize)]
pub struct GpuStats {
    util_p50: Option<f64>,
    util_p95: Option<f64>,
    util_max: Option<f64>,
    vram_p95_mb: Option<f64>,
    vram_max_mb: Option<f64>,
    pct_time_vram_over_90: Option<f64>,
    pct_time_idle: f64,
}

#[derive(Serialize)]
pub struct AppJson {
    pub app: String,
    pub ram_avg_gb: f64,
    pub ram_peak_gb: f64,
    pub running_pct: f64,
    pub cpu_core_hours: f64,
    pub cpu_peak_pct_of_core: f64,
    pub vram_peak_mb: i64,
    pub vram_avg_mb: i64,
}

#[derive(Serialize)]
pub struct AppGb {
    app: String,
    gb: f64,
}

#[derive(Serialize)]
pub struct PeakMoment {
    at: String,
    used_gb: f64,
    cpu_pct: f64,
    gpu_util: Option<f64>,
    top_apps: Vec<AppGb>,
}

#[derive(Serialize)]
pub struct Summary {
    /// First and last sample, for history rows.
    #[serde(skip)]
    span: (i64, i64),
    pub generated: String,
    pub days: f64,
    pub samples: usize,
    pub start: String,
    pub end: String,
    pub logged_hours: f64,
    pub hardware: Hardware,
    pub ram: Ram,
    pub cpu: Cpu,
    pub gpu: Option<GpuStats>,
    pub top_ram: Vec<AppJson>,
    pub top_cpu: Vec<AppJson>,
    pub top_vram: Vec<AppJson>,
    pub ram_peak_moment: PeakMoment,
    pub oom_kills: Option<usize>,
}

/// One row of the per-app history (apps.json, push payload).
#[derive(Serialize)]
pub struct AppPeriod {
    kind: &'static str,
    period: String,
    app: String,
    ram_avg_gb: f64,
    ram_peak_gb: f64,
    cpu_core_hours: f64,
    vram_peak_mb: i64,
}

impl Summary {
    /// The apps worth tracking across periods: top 8 by RAM and CPU, top 5 by VRAM, deduplicated.
    pub fn notable_apps(&self, kind: &'static str, period: &str) -> Vec<AppPeriod> {
        let mut seen = std::collections::HashSet::new();
        let picks = self.top_ram.iter().take(8).chain(self.top_cpu.iter().take(8)).chain(self.top_vram.iter().take(5));
        picks
            .filter(|a| seen.insert(a.app.as_str()))
            .map(|a| AppPeriod {
                kind,
                period: period.to_owned(),
                app: a.app.clone(),
                ram_avg_gb: a.ram_avg_gb,
                ram_peak_gb: a.ram_peak_gb,
                cpu_core_hours: a.cpu_core_hours,
                vram_peak_mb: a.vram_peak_mb,
            })
            .collect()
    }
}

pub fn iso_minutes(ts: i64) -> String {
    local(ts).strftime("%Y-%m-%dT%H:%M").to_string()
}

/// Machine-readable summary of a period; None if it has no samples.
pub fn summarize(db: &Connection, p: &Period, days: f64) -> Result<Option<Summary>> {
    let (Some(first), Some(last), Some(peak)) = (p.samples.first(), p.samples.last(), p.peak_ram()) else { return Ok(None) };
    let n = p.samples.len() as f64;
    let used = p.dist(|s| Some(s.used_kb));
    let cpu = p.dist(|s| Some(s.cpu));
    let gpu = p.dist(|s| s.gpu_util);
    let vram = p.dist(|s| s.vram_used_mb);
    let vram_total = p.vram_total();
    let frac = |d: &Dist, pred: &dyn Fn(f64) -> bool| round(d.share(pred).unwrap_or(0.0), 3);
    let over = |cap: f64| frac(&used, &|x| x > (cap - HEADROOM_GB) * KB_PER_GB);
    let ggb = |x: Option<f64>| x.map(gb);

    let app_json = |a: &App| AppJson {
        app: a.grp.clone(),
        ram_avg_gb: gb(a.avg_mem_all),
        ram_peak_gb: gb(a.max_mem),
        running_pct: round(100.0 * a.presence, 1),
        cpu_core_hours: round(a.core_hours, 2),
        cpu_peak_pct_of_core: a.max_cpu.round(),
        vram_peak_mb: int(a.max_vram),
        vram_avg_mb: int(a.avg_vram_all),
    };
    let top = |key: &dyn Fn(&App) -> f64, k: usize| p.top(key).into_iter().take(k).map(app_json).collect::<Vec<_>>();

    Ok(Some(Summary {
        span: (first.ts, last.ts),
        generated: local(now()).strftime("%Y-%m-%dT%H:%M:%S").to_string(),
        days,
        samples: p.samples.len(),
        start: iso_minutes(first.ts),
        end: iso_minutes(last.ts),
        logged_hours: round(n * p.avg_dt / 3600.0, 1),
        hardware: Hardware { threads: sys::ncpu(), ram_gb: gb(last.mem_total_kb), vram_gb: r2(vram_total.map(|v| v / 1024.0)) },
        ram: Ram {
            used_p50_gb: ggb(used.pct(50.0)),
            used_p95_gb: ggb(used.pct(95.0)),
            used_p99_gb: ggb(used.pct(99.0)),
            used_max_gb: ggb(used.max()),
            swap_max_gb: gb(p.samples.iter().filter_map(|s| s.swap_used_kb).fold(0.0, f64::max)),
            swapped_out_gb: gb(p.samples.iter().filter_map(|s| s.swapout_pages).sum::<f64>() * sys::page_kb() as f64),
            psi_some_p95: r2(p.dist(|s| s.psi_mem).pct(95.0)),
            pct_time_over: TimeOver { gb16: over(16.0), gb24: over(24.0), gb32: over(32.0), gb48: over(48.0) },
        },
        cpu: Cpu {
            busy_p50: r2(cpu.pct(50.0)),
            busy_p95: r2(cpu.pct(95.0)),
            busy_p99: r2(cpu.pct(99.0)),
            busy_max: r2(cpu.max()),
            psi_some_p95: r2(p.dist(|s| s.psi_cpu).pct(95.0)),
            pct_time_over_80: frac(&cpu, &|x| x > 80.0),
        },
        gpu: (!gpu.is_empty()).then(|| GpuStats {
            util_p50: r2(gpu.pct(50.0)),
            util_p95: r2(gpu.pct(95.0)),
            util_max: r2(gpu.max()),
            vram_p95_mb: r2(vram.pct(95.0)),
            vram_max_mb: r2(vram.max()),
            pct_time_vram_over_90: vram_total.map(|t| frac(&vram, &|x| x > 0.9 * t)),
            pct_time_idle: frac(&gpu, &|x| x < 5.0),
        }),
        // Ranked on the rounded values shown, so equal-looking apps sort by name.
        top_ram: top(&|a| gb(a.avg_mem_all), 10),
        top_cpu: top(&|a| round(a.core_hours, 2), 10),
        top_vram: p.top(|a| a.max_vram.round()).into_iter().filter(|a| int(a.max_vram) > 0).take(8).map(app_json).collect(),
        ram_peak_moment: PeakMoment {
            at: iso_minutes(peak.ts),
            used_gb: gb(peak.used_kb),
            cpu_pct: round(peak.cpu, 2),
            gpu_util: peak.gpu_util,
            top_apps: top_at(db, peak.ts, 5)?.into_iter().map(|(app, kb)| AppGb { app, gb: gb(kb) }).collect(),
        },
        // Only meaningful while the journal still covers the period, i.e. for a period ending now.
        oom_kills: (p.until > now() - 60).then(|| sys::oom_events(p.since).len()),
    }))
}

// --------------------------------------------------------------------- weekly / monthly history

#[derive(Clone, Copy, PartialEq, Eq, clap::ValueEnum)]
pub enum Kind {
    Week,
    Month,
}

impl Kind {
    pub fn name(self) -> &'static str {
        match self {
            Self::Week => "week",
            Self::Month => "month",
        }
    }
}

pub struct PeriodRange {
    pub label: String,
    pub start: i64,
    pub end: i64,
}

impl PeriodRange {
    pub fn complete(&self) -> bool {
        self.end <= now()
    }
}

/// Every ISO week or calendar month (local time) from the first sample up to now, oldest first.
pub fn periods(db: &Connection, kind: Kind) -> Result<Vec<PeriodRange>> {
    let first: Option<i64> = db.query_row("SELECT MIN(ts) FROM samples", [], |r| r.get(0))?;
    let Some(first) = first else { return Ok(Vec::new()) };
    period_ranges(local(first).date(), kind, now(), &TimeZone::system())
}

fn period_ranges(first: Date, kind: Kind, now: i64, tz: &TimeZone) -> Result<Vec<PeriodRange>> {
    let mut d = match kind {
        Kind::Week => first.checked_sub(i64::from(first.weekday().to_monday_zero_offset()).days())?,
        Kind::Month => first.first_of_month(),
    };
    let midnight = |d: Date| -> Result<i64> { Ok(d.to_zoned_datetime(tz.clone())?.timestamp().as_second()) };
    let mut out = Vec::new();
    while midnight(d)? <= now {
        let (next, label) = match kind {
            Kind::Week => {
                let iso = d.iso_week_date();
                (d.checked_add(7.days())?, format!("{}-W{:02}", iso.year(), iso.week()))
            }
            Kind::Month => (d.checked_add(1.month())?, format!("{}-{:02}", d.year(), d.month())),
        };
        out.push(PeriodRange { label, start: midnight(d)?, end: midnight(next)? });
        d = next;
    }
    Ok(out)
}

/// One history row (weekly.json / monthly.json). Field names are what the Claude routines read.
#[derive(Serialize)]
pub struct HistRow {
    kind: &'static str,
    period: String,
    start: String,
    end: String,
    logged_hours: f64,
    ram_p50_gb: Option<f64>,
    ram_p95_gb: Option<f64>,
    ram_p99_gb: Option<f64>,
    ram_max_gb: Option<f64>,
    pct_time_over_28gb: f64,
    pct_time_over_44gb: f64,
    mem_psi_p95: Option<f64>,
    swapped_out_gb: f64,
    cpu_p50: Option<f64>,
    cpu_p95: Option<f64>,
    cpu_p99: Option<f64>,
    cpu_psi_p95: Option<f64>,
    pct_time_cpu_over_80: f64,
    gpu_p50: Option<f64>,
    gpu_p95: Option<f64>,
    vram_max_mb: Option<f64>,
    pct_time_vram_over_90: Option<f64>,
    top_ram_app: Option<String>,
    pub complete: bool,
}

impl HistRow {
    pub fn new(s: &Summary, kind: Kind, range: &PeriodRange) -> Self {
        let g = s.gpu.as_ref();
        Self {
            kind: kind.name(),
            period: range.label.clone(),
            start: local(s.span.0).strftime("%Y-%m-%d").to_string(),
            end: local(s.span.1).strftime("%Y-%m-%d").to_string(),
            logged_hours: s.logged_hours,
            ram_p50_gb: s.ram.used_p50_gb,
            ram_p95_gb: s.ram.used_p95_gb,
            ram_p99_gb: s.ram.used_p99_gb,
            ram_max_gb: s.ram.used_max_gb,
            pct_time_over_28gb: s.ram.pct_time_over.gb32,
            pct_time_over_44gb: s.ram.pct_time_over.gb48,
            mem_psi_p95: s.ram.psi_some_p95,
            swapped_out_gb: s.ram.swapped_out_gb,
            cpu_p50: s.cpu.busy_p50,
            cpu_p95: s.cpu.busy_p95,
            cpu_p99: s.cpu.busy_p99,
            cpu_psi_p95: s.cpu.psi_some_p95,
            pct_time_cpu_over_80: s.cpu.pct_time_over_80,
            gpu_p50: g.and_then(|g| g.util_p50),
            gpu_p95: g.and_then(|g| g.util_p95),
            vram_max_mb: g.and_then(|g| g.vram_max_mb),
            pct_time_vram_over_90: g.and_then(|g| g.pct_time_vram_over_90),
            top_ram_app: s.top_ram.first().map(|a| a.app.clone()),
            complete: range.complete(),
        }
    }
}

/// Summarises each range with its loaded period (same order), skipping empty ones.
pub fn summarize_ranges(db: &Connection, ranges: Vec<PeriodRange>, periods: impl Iterator<Item = Period>) -> Result<Vec<(PeriodRange, Summary)>> {
    let mut out = Vec::new();
    for (range, p) in ranges.into_iter().zip(periods) {
        let days = round((range.end - range.start) as f64 / 86400.0, 2);
        if let Some(s) = summarize(db, &p, days)? {
            out.push((range, s));
        }
    }
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn percentile_interpolates_like_numpy_linear() {
        let d = Dist::new([4.0, 1.0, 3.0, 2.0].map(Some));
        assert_eq!((d.pct(0.0), d.pct(50.0), d.pct(100.0)), (Some(1.0), Some(2.5), Some(4.0)));
    }

    #[test]
    fn percentile_of_nothing_is_none() {
        assert_eq!(Dist::new([None]).pct(50.0), None);
    }

    fn labels(first: &str, kind: Kind, now: &str) -> Vec<String> {
        let tz = TimeZone::get("America/Toronto").expect("tzdb");
        let now = now.parse::<Date>().unwrap().to_zoned_datetime(tz.clone()).unwrap().timestamp().as_second();
        period_ranges(first.parse().unwrap(), kind, now, &tz).unwrap().into_iter().map(|r| r.label).collect()
    }

    #[test]
    fn weeks_use_iso_years_across_new_year() {
        assert_eq!(labels("2026-12-30", Kind::Week, "2027-01-05"), ["2026-W53", "2027-W01"]);
    }

    #[test]
    fn months_roll_over_the_year() {
        assert_eq!(labels("2026-11-15", Kind::Month, "2027-01-02"), ["2026-11", "2026-12", "2027-01"]);
    }

    #[test]
    fn week_spanning_dst_end_is_one_hour_longer() {
        // DST ends Sun 2026-11-01, inside the week starting Mon 2026-10-26.
        let tz = TimeZone::get("America/Toronto").unwrap();
        let now = "2026-10-30".parse::<Date>().unwrap().to_zoned_datetime(tz.clone()).unwrap().timestamp().as_second();
        let r = period_ranges("2026-10-28".parse().unwrap(), Kind::Week, now, &tz).unwrap();
        assert_eq!(r[0].end - r[0].start, 7 * 86400 + 3600);
    }
}
