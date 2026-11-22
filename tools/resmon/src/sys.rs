//! Readers for /proc, plus naming the application a process belongs to from its cgroup.

use std::fs::File;
use std::io::{Read, Write};
use std::process::{Command, Stdio};
use std::sync::OnceLock;

/// Linux reports process CPU time in USER_HZ ticks, fixed at 100 by the kernel ABI.
pub const CLK_TCK: f64 = 100.0;

/// Reads a whole file into `buf` (cleared first). False if it can't be read.
pub fn read_into(path: &str, buf: &mut String) -> bool {
    buf.clear();
    File::open(path).and_then(|mut f| f.read_to_string(buf)).is_ok()
}

/// Page size in KiB, from the ELF auxiliary vector (AT_PAGESZ).
pub fn page_kb() -> u64 {
    static PAGE: OnceLock<u64> = OnceLock::new();
    *PAGE.get_or_init(|| {
        const AT_PAGESZ: u64 = 6;
        let aux = std::fs::read("/proc/self/auxv").unwrap_or_default();
        let mut words = aux.chunks_exact(8).map(|c| u64::from_ne_bytes(c.try_into().unwrap_or_default()));
        while let (Some(key), Some(val)) = (words.next(), words.next()) {
            if key == AT_PAGESZ {
                return val / 1024;
            }
        }
        4
    })
}

/// Logical CPUs, counted the same way `os.cpu_count()` and `nproc --all` do.
pub fn ncpu() -> usize {
    static N: OnceLock<usize> = OnceLock::new();
    *N.get_or_init(|| {
        std::fs::read_to_string("/proc/stat").map_or(1, |s| {
            s.lines().filter(|l| l.strip_prefix("cpu").is_some_and(|r| r.starts_with(|c: char| c.is_ascii_digit()))).count().max(1)
        })
    })
}

pub fn hostname() -> String {
    std::fs::read_to_string("/proc/sys/kernel/hostname").map(|s| s.trim().to_owned()).unwrap_or_default()
}

#[derive(Default)]
pub struct MemInfo {
    pub total: i64,
    pub avail: i64,
    pub cached: i64,
    pub shmem: i64,
    pub swap_used: i64,
}

pub fn meminfo(buf: &mut String) -> MemInfo {
    let mut m = MemInfo::default();
    let (mut swap_total, mut swap_free) = (0, 0);
    read_into("/proc/meminfo", buf);
    for line in buf.lines() {
        let Some((key, rest)) = line.split_once(':') else { continue };
        let val = rest.split_whitespace().next().and_then(|v| v.parse().ok()).unwrap_or(0);
        match key {
            "MemTotal" => m.total = val,
            "MemAvailable" => m.avail = val,
            "Cached" => m.cached = val,
            "Shmem" => m.shmem = val,
            "SwapTotal" => swap_total = val,
            "SwapFree" => swap_free = val,
            _ => {}
        }
    }
    m.swap_used = swap_total - swap_free;
    m
}

/// Cumulative (pswpin, pswpout) pages.
pub fn swap_pages(buf: &mut String) -> (i64, i64) {
    read_into("/proc/vmstat", buf);
    let (mut sin, mut sout) = (0, 0);
    for line in buf.lines() {
        let Some((key, val)) = line.split_once(' ') else { continue };
        match key {
            "pswpin" => sin = val.trim().parse().unwrap_or(0),
            "pswpout" => sout = val.trim().parse().unwrap_or(0),
            _ => {}
        }
    }
    (sin, sout)
}

/// (busy, total) jiffies across all CPUs. Guest time is already counted in user time.
pub fn cpu_times(buf: &mut String) -> (u64, u64) {
    read_into("/proc/stat", buf);
    let vals: Vec<u64> = buf.lines().next().unwrap_or("").split_whitespace().skip(1).take(8).filter_map(|v| v.parse().ok()).collect();
    let idle = vals.get(3).copied().unwrap_or(0) + vals.get(4).copied().unwrap_or(0); // idle + iowait
    let total: u64 = vals.iter().sum();
    (total - idle, total)
}

/// PSI avg10 for one resource: (some, full).
pub fn psi(resource: &str, buf: &mut String) -> (Option<f64>, Option<f64>) {
    let (mut some, mut full) = (None, None);
    if read_into(&format!("/proc/pressure/{resource}"), buf) {
        for line in buf.lines() {
            let avg10 = line.split_whitespace().find_map(|f| f.strip_prefix("avg10=")).and_then(|v| v.parse().ok());
            if line.starts_with("some") {
                some = avg10;
            } else if line.starts_with("full") {
                full = avg10;
            }
        }
    }
    (some, full)
}

pub fn load1(buf: &mut String) -> f64 {
    read_into("/proc/loadavg", buf);
    buf.split_whitespace().next().and_then(|v| v.parse().ok()).unwrap_or(0.0)
}

/// The fields of /proc/<pid>/stat the collector uses.
pub struct ProcStat<'a> {
    pub comm: &'a str,
    pub ticks: u64,
    pub start: u64,
    pub rss_pages: u64,
}

pub fn parse_stat(stat: &str) -> Option<ProcStat<'_>> {
    // comm may contain spaces and ')', so split around the last ')'.
    let (head, rest) = stat.rsplit_once(')')?;
    let comm = head.split_once('(')?.1;
    // After ") ": state=0 ... utime=11 stime=12 ... starttime=19 vsize=20 rss=21.
    let mut f = rest.split_whitespace();
    let mut nth = |n: usize| f.nth(n).and_then(|v| v.parse::<u64>().ok());
    let utime = nth(11)?;
    let stime = nth(0)?;
    let start = nth(6)?;
    let rss_pages = nth(1)?;
    Some(ProcStat { comm, ticks: utime + stime, start, rss_pages })
}

/// Proportional set size in KiB (shared pages split between their users); `None` if unreadable.
pub fn pss_kb(pid: &str, buf: &mut String) -> Option<u64> {
    if !read_into(&format!("/proc/{pid}/smaps_rollup"), buf) {
        return None;
    }
    let line = buf.lines().find(|l| l.starts_with("Pss:"))?;
    line.split_whitespace().nth(1)?.parse().ok()
}

/// Runs a command with a time limit (coreutils `timeout`), feeding `stdin`; stdout on success.
pub fn run(secs: u32, cmd: &str, args: &[&str], stdin: Option<&str>) -> Option<String> {
    let mut child = Command::new("timeout")
        .arg(secs.to_string())
        .arg(cmd)
        .args(args)
        .stdin(if stdin.is_some() { Stdio::piped() } else { Stdio::null() })
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .ok()?;
    if let (Some(input), Some(mut pipe)) = (stdin, child.stdin.take()) {
        pipe.write_all(input.as_bytes()).ok()?;
    }
    let out = child.wait_with_output().ok()?;
    out.status.success().then(|| String::from_utf8_lossy(&out.stdout).into_owned())
}

/// Kernel OOM-killer lines from the journal since `since` (at most 20).
pub fn oom_events(since: i64) -> Vec<String> {
    let since = format!("--since=@{since}");
    let args = ["-k", "--no-pager", "-o", "short-iso", &since, "--grep", "Out of memory|oom-kill|Killed process"];
    // journalctl exits 1 when nothing matches; that's simply no events.
    run(30, "journalctl", &args, None)
        .map(|out| out.lines().filter(|l| !l.is_empty() && !l.starts_with("--")).take(20).map(str::to_owned).collect())
        .unwrap_or_default()
}

// --------------------------------------------------------------------- app naming

fn unescape(s: &str) -> String {
    // systemd escapes unit names as \xNN.
    let mut out = String::with_capacity(s.len());
    let mut rest = s;
    while let Some(i) = rest.find("\\x") {
        out.push_str(&rest[..i]);
        let hex = rest.get(i + 2..i + 4).and_then(|h| u8::from_str_radix(h, 16).ok());
        if let Some(b) = hex {
            out.push(char::from(b));
            rest = &rest[i + 4..];
        } else {
            out.push_str("\\x");
            rest = &rest[i + 2..];
        }
    }
    out.push_str(rest);
    out
}

fn is_generic(name: &str) -> bool {
    let numbered = |prefix: &str| name.strip_prefix(prefix).is_some_and(|n| !n.is_empty() && n.bytes().all(|b| b.is_ascii_digit()));
    matches!(name, "init" | "app" | "background" | "-" | "system" | "user" | "machine")
        || numbered("session-")
        || numbered("user@")
        || numbered("user-")
}

/// Strips `-<suffix>` when the suffix is non-empty and every byte passes `pred`, at least `min` long.
fn strip_tail(name: &str, min: usize, pred: fn(u8) -> bool) -> &str {
    match name.rsplit_once('-') {
        Some((head, tail)) if tail.len() >= min.max(1) && tail.bytes().all(pred) => head,
        _ => name,
    }
}

/// `app-<launcher>-<id>@<uuid>.scope` -> a short application name.
fn app_name(unit: &str) -> &str {
    let mut name = unit;
    for prefix in ["flatpak-", "gnome-", "kde-", "cosmic-"] {
        if let Some(r) = name.strip_prefix(prefix) {
            name = r;
            break;
        }
    }
    if let Some((head, _)) = name.split_once('@') {
        name = head;
    }
    name = strip_tail(name, 6, |b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b));
    name = strip_tail(name, 1, |b| b.is_ascii_digit());
    if let Some(r) = name.strip_prefix("dbus-:")
        && let Some((addr, rest)) = r.split_once('-')
        && !addr.is_empty()
        && addr.bytes().all(|b| b.is_ascii_digit() || b == b'.')
    {
        name = rest;
    }
    // Reverse-DNS app ids: org.mozilla.firefox -> firefox, com.discordapp.Discord.desktop -> Discord.
    let dots: Vec<&str> = name.split('.').collect();
    if let [.., second_last, last] = dots[..]
        && dots.len() >= 3
    {
        let generic = ["client", "app", "desktop", "browser"].iter().any(|g| last.eq_ignore_ascii_case(g));
        name = if generic { second_last } else { last };
    }
    name
}

/// Names the application a process belongs to from its `/proc/<pid>/cgroup` text, so all of
/// Firefox's processes form one group. Falls back to `comm` for shell-launched processes.
pub fn group_name(cgroup: &str, comm: &str) -> String {
    let path = cgroup.lines().find_map(|l| l.strip_prefix("0::")).unwrap_or("").trim();
    for comp in path.rsplit('/').filter(|c| !c.is_empty()) {
        let full = unescape(comp);
        let name = [".scope", ".service", ".slice"].iter().find_map(|s| full.strip_suffix(s)).unwrap_or(&full);
        if is_generic(name) {
            continue;
        }
        for runtime in ["docker-", "libpod-"] {
            if let Some(id) = name.strip_prefix(runtime).and_then(|r| r.get(..12))
                && id.bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
            {
                return format!("container:{id}");
            }
        }
        // libvirt bumps the number on every VM boot: machine-qemu-<N>-<domain>.
        if let Some((num, dom)) = name.strip_prefix("machine-qemu-").and_then(|r| r.split_once('-'))
            && !num.is_empty()
            && num.bytes().all(|b| b.is_ascii_digit())
            && !dom.is_empty()
        {
            return format!("vm:{dom}");
        }
        if let Some(unit) = name.strip_prefix("app-") {
            let app = app_name(unit);
            return if app.is_empty() { comm } else { app }.to_owned();
        }
        // Unit suffixes are lowercase by definition, so an exact match is right.
        if [".service", ".scope"].iter().any(|s| comp.ends_with(s)) {
            return name.to_owned();
        }
    }
    comm.to_owned()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn name(path: &str) -> String {
        group_name(&format!("0::{path}\n"), "comm")
    }

    #[test]
    fn group_names_match_the_python_collector() {
        let cases = [
            ("/user.slice/user-1000.slice/user@1000.service/app.slice/app-org.mozilla.firefox@1a2b.scope", "firefox"),
            ("/user.slice/user-1000.slice/user@1000.service/app.slice/app-flatpak-com.discordapp.Discord-12345.scope", "Discord"),
            ("/user.slice/user-1000.slice/user@1000.service/app.slice/app-kde-org.kde.konsole-1a2b3c4d.scope", "konsole"),
            ("/user.slice/user-1000.slice/user@1000.service/app.slice/app-code-4f2a91.scope", "code"),
            ("/user.slice/user-1000.slice/user@1000.service/app.slice/app-com.example.Thing.desktop-77.scope", "Thing"),
            ("/user.slice/user-1000.slice/user@1000.service/app.slice/app-dbus\\x2d:1.2\\x2dorg.kde.kded6.service", "kded6"),
            ("/user.slice/user-1000.slice/user@1000.service/app.slice/pipewire.service", "pipewire"),
            ("/user.slice/user-1000.slice/session-2.scope", "comm"),
            ("/system.slice/ollama.service", "ollama"),
            ("/system.slice/docker-0123456789abcdef0123456789abcdef.scope", "container:0123456789ab"),
            ("/machine.slice/machine-qemu\\x2d3\\x2dwin11.scope/libvirt/emulator", "vm:win11"),
            ("/", "comm"),
            ("/init.scope", "comm"),
        ];
        for (path, want) in cases {
            assert_eq!(name(path), want, "{path}");
        }
    }

    #[test]
    fn parse_stat_handles_parens_in_comm() {
        let line = "42 (we (ird) x) S 1 2 3 4 5 6 7 8 9 10 150 50 0 0 20 0 1 0 777 1000 99 18446744073709551615";
        let s = parse_stat(line).expect("parses");
        assert_eq!((s.comm, s.ticks, s.start, s.rss_pages), ("we (ird) x", 200, 777, 99));
    }
}
