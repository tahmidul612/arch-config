//! Early warning: alert when an app has stayed pinned on a core for hours.

use std::collections::HashMap;
use std::path::PathBuf;

use anyhow::Result;
use rusqlite::params;

use crate::stats::{Dist, int, now};
use crate::{db, paths, sys};

/// Busiest guest processes over 5 s, as `name pct%` pairs (Windows guest, PowerShell 5.1).
const GUEST_TOP_PS: &str = r"
$a = @{}; Get-Process | ? Id -ne $PID | % { $a[$_.Id] = $_.CPU }
Start-Sleep 5
$t = Get-Process | % { if ($a.ContainsKey($_.Id)) { [pscustomobject]@{N=$_.Name; P=($_.CPU - $a[$_.Id]) / 5 * 100} } }
($t | Sort P -Desc | Select -First 4 | ? { $_.P -ge 5 } | % { '{0} {1:N0}%' -f $_.N, $_.P }) -join ', '
";

struct Stuck {
    app: String,
    p10: i64,
    avg: i64,
}

/// Apps at or above `min_cpu` (% of one core) for the whole window ending at `until`.
///
/// Uses the 10th percentile, not the mean, so a busy-but-bursty app (a transcode, a compile)
/// doesn't count; only something that never let go does.
fn stuck_apps(until: i64, hours: f64, min_cpu: f64) -> Result<Vec<Stuck>> {
    #[expect(clippy::cast_possible_truncation, reason = "hours of seconds")]
    let since = until - (hours * 3600.0) as i64;
    let db = db::open_ro()?;
    let (n, interval): (i64, Option<f64>) = db.query_row(
        "SELECT COUNT(*), AVG(interval_s) FROM samples WHERE ts > ?1 AND ts <= ?2",
        params![since, until],
        |r| Ok((r.get(0)?, r.get(1)?)),
    )?;
    let n_f = n as f64;
    // The PC was off or the collector down for part of the window: no verdict.
    if n == 0 || n_f < 0.8 * hours * 3600.0 / interval.unwrap_or(30.0).max(1.0) {
        return Ok(Vec::new());
    }
    let mut per: HashMap<String, Vec<f64>> = HashMap::new();
    let mut st = db.prepare("SELECT grp, cpu_pct FROM groups WHERE ts > ?1 AND ts <= ?2")?;
    for r in st.query_map(params![since, until], |r| Ok((r.get::<_, String>(0)?, r.get::<_, Option<f64>>(1)?.unwrap_or(0.0))))? {
        let (grp, cpu) = r?;
        per.entry(grp).or_default().push(cpu);
    }
    let mut out: Vec<Stuck> = per
        .into_iter()
        .filter(|(_, v)| v.len() as f64 >= 0.9 * n_f)
        .filter_map(|(app, vals)| {
            let sum: f64 = vals.iter().sum();
            // Samples where the app wasn't recorded count as idle.
            let missing = usize::try_from(n).unwrap_or(0).saturating_sub(vals.len());
            let p10 = Dist::new(vals.into_iter().chain(std::iter::repeat_n(0.0, missing)).map(Some)).pct(10.0)?;
            (p10 >= min_cpu).then(|| Stuck { app, p10: int(p10), avg: int(sum / n_f) })
        })
        .collect();
    out.sort_by_key(|s| -s.avg);
    Ok(out)
}

/// `qga` is installed next to `resmon`; systemd user units don't have ~/.local/bin on PATH.
fn qga_path() -> PathBuf {
    std::env::current_exe().ok().and_then(|p| Some(p.parent()?.join("qga"))).filter(|p| p.exists()).unwrap_or_else(|| "qga".into())
}

/// `container:<id>` -> Docker name and compose project; `vm:<domain>` -> the guest's busiest processes.
fn describe_app(grp: &str) -> String {
    if let Some(dom) = grp.strip_prefix("vm:") {
        let qga = qga_path();
        let top = sys::run(40, &qga.to_string_lossy(), &["--vm", dom, "exec", "30"], Some(GUEST_TOP_PS));
        return match top.as_deref().map(str::trim) {
            Some("") => format!("VM {dom} (inside: nothing busy)"),
            Some(t) => format!("VM {dom} (inside: {})", t.lines().last().unwrap_or(t)),
            None => format!("VM {dom} (guest agent unreachable)"),
        };
    }
    let Some(id) = grp.strip_prefix("container:") else { return grp.to_owned() };
    let format = r#"{{.Name}}|{{index .Config.Labels "com.docker.compose.project"}}|{{index .Config.Labels "com.docker.compose.project.working_dir"}}"#;
    let Some(out) = sys::run(10, "docker", &["inspect", id, "--format", format], None) else { return grp.to_owned() };
    let mut parts = out.trim().splitn(3, '|');
    let name = parts.next().unwrap_or_default().trim_start_matches('/');
    let extra: Vec<String> = [parts.next().filter(|p| !p.is_empty()).map(|p| format!("compose {p}")), parts.next().filter(|d| !d.is_empty()).map(str::to_owned)]
        .into_iter()
        .flatten()
        .collect();
    if name.is_empty() {
        return grp.to_owned();
    }
    let extra = if extra.is_empty() { String::new() } else { format!(" ({})", extra.join(", ")) };
    format!("Docker {name} [{id}]{extra}")
}

/// Desktop notification, a ping to every reachable KDE Connect device, and the optional hook.
fn notify(title: &str, body: &str) {
    sys::run(10, "notify-send", &["-a", "resmon", "-u", "critical", title, body], None);
    if let Some(ids) = sys::run(10, "kdeconnect-cli", &["-a", "--id-only"], None) {
        let msg = format!("{title}: {body}");
        for dev in ids.split_whitespace() {
            sys::run(10, "kdeconnect-cli", &["-d", dev, "--ping-msg", &msg], None);
        }
    }
    if let Some(cmd) = paths::load_conf().get("RESMON_ALERT_CMD").filter(|c| !c.is_empty()) {
        // The hook gets the title and body as $1 and $2.
        sys::run(30, "sh", &["-c", cmd, "resmon-alert", title, body], None);
    }
}

pub fn watch(hours: f64, min_cpu: f64, cooldown_h: f64, until: Option<i64>, dry_run: bool) -> Result<()> {
    let now = now();
    let hits = stuck_apps(until.unwrap_or(now), hours, min_cpu)?;
    let state_path = paths::state_dir().join("alerts.json");
    let mut state: HashMap<String, i64> = std::fs::read(&state_path).ok().and_then(|b| serde_json::from_slice(&b).ok()).unwrap_or_default();
    #[expect(clippy::cast_possible_truncation, reason = "hours of seconds")]
    let cooldown = (cooldown_h * 3600.0) as i64;
    let (fresh, alerted): (Vec<&Stuck>, Vec<&Stuck>) = hits.iter().partition(|h| now - state.get(&h.app).copied().unwrap_or(0) >= cooldown);
    for (h, note) in fresh.iter().map(|h| (h, "")).chain(alerted.iter().map(|h| (h, " (already alerted)"))) {
        println!("stuck: {} avg {}% p10 {}% of a core for {hours} h{note}", h.app, h.avg, h.p10);
    }
    if dry_run || fresh.is_empty() {
        return Ok(());
    }
    let lines: Vec<String> = fresh.iter().map(|h| format!("{}: ~{}% of a core for {hours}+ h", describe_app(&h.app), h.avg)).collect();
    notify(&format!("resmon: {} process(es) stuck busy", fresh.len()), &lines.join("\n"));
    let db = db::open_rw()?;
    for l in &lines {
        db::event(&db, now, "stuck_busy", l)?;
    }
    for h in fresh {
        state.insert(h.app.clone(), now);
    }
    if let Some(dir) = state_path.parent() {
        std::fs::create_dir_all(dir)?;
    }
    std::fs::write(&state_path, serde_json::to_vec(&state)?)?;
    Ok(())
}
