//! `export` (local JSON + markdown copies) and `push` (fire the weekly/monthly Claude review).

use std::path::Path;
use std::time::Duration;

use anyhow::{Context, Result, bail};
use serde::Serialize;

use crate::stats::{self, AppPeriod, HistRow, Kind, Period, PeriodRange, Summary, now, periods, summarize_ranges};
use crate::{db, paths, report, sys};

const FIRE_URL: &str = "https://api.anthropic.com/v1/claude_code/routines/{}/fire";
/// The routine trigger accepts at most this much text.
const MAX_TEXT: usize = 65536;

fn write_json(path: &Path, value: &impl Serialize) -> Result<()> {
    let mut out = Vec::new();
    let mut ser = serde_json::Serializer::with_formatter(&mut out, serde_json::ser::PrettyFormatter::with_indent(b" "));
    value.serialize(&mut ser)?;
    std::fs::write(path, out).with_context(|| format!("writing {}", path.display()))
}

type History = Vec<(PeriodRange, Summary)>;

/// The given windows plus every week and month summary, all from one scan of the app table.
fn load_all(db: &rusqlite::Connection, windows: &[(i64, i64)]) -> Result<(Vec<Period>, History, History)> {
    let (weeks, months) = (periods(db, Kind::Week)?, periods(db, Kind::Month)?);
    let spans: Vec<(i64, i64)> = windows.iter().copied().chain(weeks.iter().chain(&months).map(|r| (r.start, r.end))).collect();
    let mut loaded = Period::load_many(db, &spans)?.into_iter();
    let windows = loaded.by_ref().take(windows.len()).collect();
    let n_weeks = weeks.len();
    let weeks = summarize_ranges(db, weeks, loaded.by_ref().take(n_weeks))?;
    let months = summarize_ranges(db, months, loaded)?;
    Ok((windows, weeks, months))
}

fn rows(hist: &[(PeriodRange, Summary)], kind: Kind) -> Vec<HistRow> {
    hist.iter().map(|(r, s)| HistRow::new(s, kind, r)).collect()
}

pub fn export(out_dir: &Path) -> Result<()> {
    std::fs::create_dir_all(out_dir)?;
    let db = db::open_ro()?;
    let until = now() + 1;
    let windows = [("7d", 7), ("30d", 30)];
    let spans: Vec<(i64, i64)> = windows.iter().map(|&(_, days)| (until - days * 86400, until)).collect();
    let (loaded, weeks, months) = load_all(&db, &spans)?;
    for ((label, days), p) in windows.into_iter().zip(&loaded) {
        let path = out_dir.join(format!("summary-{label}.json"));
        let days = days as f64;
        match stats::summarize(&db, p, days)? {
            Some(s) => write_json(&path, &s)?,
            None => write_json(&path, &serde_json::json!({ "days": days, "samples": 0 }))?,
        }
        std::fs::write(out_dir.join(format!("report-{label}.md")), report::render(&db, p)?)?;
    }
    let mut apps = Vec::new();
    for (kind, file, hist) in [(Kind::Week, "weekly.json", &weeks), (Kind::Month, "monthly.json", &months)] {
        write_json(&out_dir.join(file), &rows(hist, kind))?;
        apps.extend(hist.iter().flat_map(|(r, s)| s.notable_apps(kind.name(), &r.label)));
    }
    write_json(&out_dir.join("apps.json"), &apps)?;
    println!("exported to {}", out_dir.display());
    Ok(())
}

// --------------------------------------------------------------------- push

#[derive(Serialize)]
struct Payload {
    resmon_push: u8,
    host: String,
    kind: &'static str,
    generated: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    period: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    complete: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    summary: Option<Summary>,
    apps: Vec<AppPeriod>,
    weekly: Vec<HistRow>,
    monthly: Vec<HistRow>,
    #[serde(skip_serializing_if = "std::ops::Not::not")]
    history_truncated: bool,
}

/// The period just finished (or the current one if none has finished yet) plus all history.
fn build_payload(kind: Kind) -> Result<String> {
    let db = db::open_ro()?;
    let (_, weeks, months) = load_all(&db, &[])?;
    let (weekly, monthly) = (rows(&weeks, Kind::Week), rows(&months, Kind::Month));
    let hist = if kind == Kind::Week { weeks } else { months };
    // The last finished period; before any has finished, the current one.
    let pick = hist.iter().rposition(|(r, _)| r.complete()).unwrap_or(0);
    let reviewed = hist.into_iter().nth(pick);
    let apps = reviewed.as_ref().map(|(r, s)| s.notable_apps(kind.name(), &r.label)).unwrap_or_default();
    let complete = reviewed.as_ref().map(|(r, _)| r.complete());
    let (period, summary) = reviewed.map(|(r, s)| (r.label, s)).unzip();
    let mut payload = Payload {
        resmon_push: 1,
        host: sys::hostname(),
        kind: kind.name(),
        generated: stats::local(now()).strftime("%Y-%m-%dT%H:%M:%S%:z").to_string(),
        period,
        complete,
        summary,
        apps,
        weekly,
        monthly,
        history_truncated: false,
    };
    let mut text = serde_json::to_string(&payload)?;
    // Keep the newest weekly history if the payload outgrows the trigger limit.
    while text.len() > MAX_TEXT && payload.weekly.len() > 4 {
        let keep = (payload.weekly.len() / 2).max(4);
        payload.weekly.drain(..payload.weekly.len() - keep);
        payload.history_truncated = true;
        text = serde_json::to_string(&payload)?;
    }
    Ok(text)
}

pub fn push(kind: Kind, dry_run: bool) -> Result<()> {
    let text = build_payload(kind)?;
    if dry_run {
        println!("{text}");
        return Ok(());
    }
    let conf = paths::load_conf();
    let key = if kind == Kind::Week { "WEEKLY" } else { "MONTHLY" };
    let (Some(trigger), Some(token)) = (conf.get(&format!("RESMON_{key}_TRIGGER")), conf.get(&format!("RESMON_{key}_TOKEN"))) else {
        bail!("set RESMON_{key}_TRIGGER and RESMON_{key}_TOKEN in {}", paths::config_file().display());
    };
    if token.is_empty() {
        bail!("RESMON_{key}_TOKEN is empty in {}", paths::config_file().display());
    }
    let agent: ureq::Agent = ureq::Agent::config_builder()
        .http_status_as_error(false)
        .timeout_global(Some(Duration::from_mins(1)))
        .build()
        .into();
    let mut resp = agent
        .post(&FIRE_URL.replace("{}", trigger))
        .header("Authorization", &format!("Bearer {token}"))
        .header("anthropic-version", "2026-10-21")
        .send_json(serde_json::json!({ "text": text }))?;
    let status = resp.status();
    let body = resp.body_mut().read_to_string()?;
    if !status.is_success() {
        bail!("fire failed {}: {}", status.as_u16(), body.chars().take(500).collect::<String>());
    }
    let out: serde_json::Value = serde_json::from_str(&body).unwrap_or_default();
    let session = out.get("claude_code_session_url").map_or_else(|| out.to_string(), ToString::to_string);
    println!("fired {} review ({} chars): {session}", kind.name(), text.len());
    Ok(())
}
