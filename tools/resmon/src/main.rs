//! resmon: long-running CPU / RAM / GPU usage logger with an attribution report.
//!
//! Samples system-wide metrics plus per-application usage (grouped by systemd cgroup, so
//! Firefox is one row instead of 40 content processes) into SQLite, then answers: where do
//! RAM, CPU and GPU actually go, and could the hardware be downgraded or should it be upgraded?

mod collect;
mod db;
mod export;
mod gpu;
mod report;
mod stats;
mod sys;
mod watch;

use std::path::PathBuf;

use anyhow::Result;
use clap::{Parser, Subcommand};

use stats::{Kind, Period, now};

mod paths {
    use std::collections::HashMap;
    use std::path::PathBuf;

    fn xdg(var: &str, fallback: &str) -> PathBuf {
        std::env::var_os(var)
            .filter(|v| !v.is_empty())
            .map_or_else(|| PathBuf::from(std::env::var_os("HOME").unwrap_or_default()).join(fallback), PathBuf::from)
            .join("resmon")
    }

    pub fn data_dir() -> PathBuf {
        xdg("XDG_DATA_HOME", ".local/share")
    }

    pub fn state_dir() -> PathBuf {
        xdg("XDG_STATE_HOME", ".local/state")
    }

    pub fn config_file() -> PathBuf {
        xdg("XDG_CONFIG_HOME", ".config").join("routines.env")
    }

    /// `KEY=value` lines from ~/.config/resmon/routines.env (quotes stripped, `#` comments skipped).
    pub fn load_conf() -> HashMap<String, String> {
        parse_env(&std::fs::read_to_string(config_file()).unwrap_or_default())
    }

    fn parse_env(text: &str) -> HashMap<String, String> {
        text.lines()
            .filter_map(|l| l.trim().split_once('='))
            .filter(|(k, _)| !k.starts_with('#'))
            .map(|(k, v)| (k.trim().to_owned(), v.trim().trim_matches(['"', '\'']).to_owned()))
            .collect()
    }

    #[cfg(test)]
    mod tests {
        #[test]
        fn env_file_strips_quotes_and_comments() {
            let conf = super::parse_env("# c\nA=1\n B = \"two\" \n#C=3\nD='4'\nnoequals\n");
            let mut got: Vec<_> = conf.iter().map(|(k, v)| format!("{k}={v}")).collect();
            got.sort();
            assert_eq!(got, ["A=1", "B=two", "D=4"]);
        }
    }
}

#[derive(Parser)]
#[command(version, about, long_about = None)]
struct Cli {
    #[command(subcommand)]
    cmd: Cmd,
}

#[derive(Subcommand)]
enum Cmd {
    /// Sample forever (the systemd user service).
    Collect {
        #[arg(long, env = "RESMON_INTERVAL", default_value_t = 30.0)]
        interval: f64,
    },
    /// Markdown report: percentiles, "would a smaller kit fit?", top apps, bottlenecks.
    Report {
        #[arg(long, default_value_t = 30.0)]
        days: f64,
        #[arg(long)]
        out: Option<PathBuf>,
    },
    /// Quick sanity check of the database.
    Status,
    /// Write 7d/30d summaries, reports and weekly/monthly history as local files.
    Export {
        /// Defaults to $XDG_DATA_HOME/resmon/exports.
        #[arg(long)]
        out_dir: Option<PathBuf>,
    },
    /// Send the finished week or month to its Claude scheduled task.
    Push {
        kind: Kind,
        /// Print the payload instead of sending it.
        #[arg(long)]
        dry_run: bool,
    },
    /// Alert when an app stays pinned on a core for hours.
    Watch {
        #[arg(long, default_value_t = 3.0)]
        hours: f64,
        /// % of ONE core, 10th percentile over the window.
        #[arg(long, default_value_t = 80.0)]
        min_cpu: f64,
        /// Hours before re-alerting the same app.
        #[arg(long, default_value_t = 24.0)]
        cooldown: f64,
        /// Window end as a unix timestamp (replay a past window).
        #[arg(long)]
        until: Option<i64>,
        /// Print hits, don't notify.
        #[arg(long)]
        dry_run: bool,
    },
}

fn status() -> Result<()> {
    let path = db::path();
    if !path.exists() {
        println!("No database at {}", path.display());
        return Ok(());
    }
    let db = db::open_ro()?;
    let (n, first, last): (i64, Option<i64>, Option<i64>) =
        db.query_row("SELECT COUNT(*), MIN(ts), MAX(ts) FROM samples", [], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)))?;
    let (Some(first), Some(last)) = (first, last) else {
        println!("Database exists but has no samples yet.");
        return Ok(());
    };
    let groups: i64 = db.query_row("SELECT COUNT(*) FROM groups", [], |r| r.get(0))?;
    let mb = std::fs::metadata(&path)?.len() as f64 / 1_048_576.0;
    let fmt = "%Y-%m-%d %H:%M";
    println!("{} ({mb:.1} MB)\n{n} samples, {groups} app rows", path.display());
    println!("first {}, last {} ({}s ago)", stats::local(first).strftime(fmt), stats::local(last).strftime(fmt), now() - last);
    Ok(())
}

fn main() -> Result<()> {
    match Cli::parse().cmd {
        Cmd::Collect { interval } => collect::run(interval),
        Cmd::Report { days, out } => {
            let db = db::open_ro()?;
            #[expect(clippy::cast_possible_truncation, reason = "days of seconds")]
            let since = now() - (days * 86400.0) as i64;
            let text = report::render(&db, &Period::load(&db, since, now() + 1)?)?;
            match out {
                Some(path) => {
                    std::fs::write(&path, text)?;
                    println!("wrote {}", path.display());
                }
                None => print!("{text}"),
            }
            Ok(())
        }
        Cmd::Status => status(),
        Cmd::Export { out_dir } => export::export(&out_dir.unwrap_or_else(|| paths::data_dir().join("exports"))),
        Cmd::Push { kind, dry_run } => export::push(kind, dry_run),
        Cmd::Watch { hours, min_cpu, cooldown, until, dry_run } => watch::watch(hours, min_cpu, cooldown, until, dry_run),
    }
}
