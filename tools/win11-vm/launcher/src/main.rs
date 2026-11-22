// vm-launch: a small window for starting the win11 Remote Play VM.
// The work itself is done by `vm-display` (looking-glass/vm-display). This process watches the host for changes
// and pushes a state snapshot to the page only when something changed; it runs `vm-display launch ...` and streams
// its `@step` lines to the page.
//
// Where changes come from (no polling except the last line):
//   VM started / stopped      inotify on libvirt's run dir (/run/libvirt/qemu/<vm>.pid appears / goes away)
//   display mode, picture     inotify on vm-display's state dir
//   virtual DS4 attached      kernel uevents (netlink, unprivileged) for USB 054c:05c4
//   client / OBS / ds4-vm     pidfd of each process: readable when it exits
//   LG input (DDC), OBS rec   asked every 15 s, and shortly after any other change (they have no event source)

use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::io::{BufRead, BufReader};
use std::os::fd::{AsRawFd, FromRawFd, OwnedFd, RawFd};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};
use tauri::{AppHandle, Emitter, Manager, State};

const SLOW_EVERY: Duration = Duration::from_secs(15);
const SETTLE: Duration = Duration::from_millis(1500); // re-check this long after an event (things start in steps)

fn vm_name() -> String {
    std::env::var("VM").unwrap_or_else(|_| "win11".into())
}
fn home() -> PathBuf {
    PathBuf::from(std::env::var("HOME").unwrap_or_else(|_| "/".into()))
}
fn vm_display_state() -> PathBuf {
    std::env::var("XDG_STATE_HOME").map(PathBuf::from).unwrap_or_else(|_| home().join(".local/state")).join("vm-display")
}
const LIBVIRT_RUN: &str = "/run/libvirt/qemu";

/// `vm-display`: $VM_DISPLAY, else ~/.local/bin/vm-display, else the copy in this repo.
fn vm_display() -> PathBuf {
    if let Ok(p) = std::env::var("VM_DISPLAY") {
        return p.into();
    }
    let local = home().join(".local/bin/vm-display");
    if local.exists() {
        return local;
    }
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../looking-glass/vm-display")
}

fn read_trim(p: impl AsRef<Path>) -> Option<String> {
    std::fs::read_to_string(p).ok().map(|s| s.trim().to_string())
}

/// One pass over /proc: comm for every process, cmdline only for python ones.
#[derive(Default)]
struct Procs {
    client: Option<i32>,
    ds4_vm: Option<i32>,
    obs: Option<i32>,
}
fn scan_procs() -> Procs {
    let mut p = Procs::default();
    let Ok(rd) = std::fs::read_dir("/proc") else { return p };
    for e in rd.flatten() {
        let name = e.file_name();
        let Some(pid) = name.to_str().and_then(|s| s.parse::<i32>().ok()) else { continue };
        let Ok(comm) = std::fs::read_to_string(format!("/proc/{pid}/comm")) else { continue };
        match comm.trim_end() {
            "looking-glass-c" => p.client = Some(pid),
            "obs" => p.obs = Some(pid),
            c if c.starts_with("python") && p.ds4_vm.is_none()
                && std::fs::read(format!("/proc/{pid}/cmdline")).is_ok_and(|b| b.windows(9).any(|w| w == b"ds4-vm.py")) => {
                    p.ds4_vm = Some(pid);
                }
            _ => {}
        }
    }
    p
}

/// The virtual DS4 (054c:05c4) is on the host's USB bus while ds4gadget.py runs,
/// which ds4-vm only does while RemotePlay.exe is open in the guest.
fn ds4_gadget_present() -> bool {
    let Ok(rd) = std::fs::read_dir("/sys/bus/usb/devices") else { return false };
    rd.flatten().any(|e| {
        read_trim(e.path().join("idVendor")).as_deref() == Some("054c")
            && read_trim(e.path().join("idProduct")).as_deref() == Some("05c4")
    })
}

#[derive(Serialize, Clone, PartialEq, Default)]
struct VmState {
    vm: &'static str,
    started_at: Option<u64>, // unix seconds; the page counts uptime itself
    mode: String,
    picture: String,
    client: bool,
    ds4_service: bool,
    ds4_attached: bool,
    obs: bool,
    lg_input: Option<String>,
    recording: Option<bool>,
    busy: bool,
}

struct App {
    state: Mutex<VmState>,
    busy: AtomicBool,
    wake: OwnedFd, // eventfd: ask the watcher for a fresh snapshot
    dry_run: bool,
    go: bool,
}
impl App {
    fn kick(&self) {
        let one: u64 = 1;
        unsafe { libc::write(self.wake.as_raw_fd(), (&one as *const u64).cast(), 8) };
    }
}

/// Fast part of the snapshot (files, /proc, sysfs) plus the pids worth watching for exit.
fn snapshot(prev: &VmState, busy: bool) -> (VmState, Procs) {
    let pid = PathBuf::from(format!("{LIBVIRT_RUN}/{}.pid", vm_name()));
    let started_at = std::fs::metadata(&pid).and_then(|m| m.modified()).ok()
        .and_then(|t| t.duration_since(UNIX_EPOCH).ok()).map(|d| d.as_secs());
    let sd = vm_display_state();
    let procs = scan_procs();
    let s = VmState {
        vm: if started_at.is_some() { "running" } else { "off" },
        started_at,
        mode: read_trim(sd.join("mode")).filter(|m| m == "hdmi").unwrap_or_else(|| "lg".into()),
        picture: read_trim(sd.join("picture")).filter(|p| p == "native").unwrap_or_else(|| "fsr".into()),
        client: procs.client.is_some(),
        ds4_service: procs.ds4_vm.is_some(),
        ds4_attached: ds4_gadget_present(),
        obs: procs.obs.is_some(),
        lg_input: prev.lg_input.clone(),
        recording: prev.recording,
        busy,
    };
    (s, procs)
}

/// The two things without an event source.
fn slow_probe(s: &mut VmState, obsws: &Option<PathBuf>) {
    s.lg_input = Command::new("lg-input").arg("status").stderr(Stdio::null()).output().ok()
        .map(|o| String::from_utf8_lossy(&o.stdout).trim().to_string()).filter(|v| !v.is_empty());
    s.recording = match (obsws, s.obs) {
        (Some(ws), true) => Command::new(ws).arg(r#"[{"requestType":"GetRecordStatus"}]"#).stderr(Stdio::null()).output().ok()
            .map(|o| String::from_utf8_lossy(&o.stdout).replace(' ', "").contains("\"outputActive\":true")),
        _ => Some(false),
    };
}

fn pidfd_open(pid: i32) -> Option<OwnedFd> {
    let fd = unsafe { libc::syscall(libc::SYS_pidfd_open, pid, 0) };
    (fd >= 0).then(|| unsafe { OwnedFd::from_raw_fd(fd as RawFd) })
}

/// Kernel uevents (NETLINK_KOBJECT_UEVENT, multicast group 1); receiving needs no privileges.
fn uevent_socket() -> Option<OwnedFd> {
    unsafe {
        let fd = libc::socket(libc::AF_NETLINK, libc::SOCK_DGRAM | libc::SOCK_CLOEXEC | libc::SOCK_NONBLOCK, libc::NETLINK_KOBJECT_UEVENT);
        if fd < 0 {
            return None;
        }
        let mut sa: libc::sockaddr_nl = std::mem::zeroed();
        sa.nl_family = libc::AF_NETLINK as u16;
        sa.nl_groups = 1;
        if libc::bind(fd, (&sa as *const libc::sockaddr_nl).cast(), std::mem::size_of::<libc::sockaddr_nl>() as u32) < 0 {
            libc::close(fd);
            return None;
        }
        Some(OwnedFd::from_raw_fd(fd))
    }
}

/// Drain a nonblocking fd; true if any message mentions the virtual DS4.
fn drain_uevents(fd: RawFd) -> bool {
    let mut buf = [0u8; 8192];
    let mut hit = false;
    loop {
        let n = unsafe { libc::recv(fd, buf.as_mut_ptr().cast(), buf.len(), libc::MSG_DONTWAIT) };
        if n <= 0 {
            return hit;
        }
        hit |= buf[..n as usize].windows(15).any(|w| w == b"PRODUCT=54c/5c4");
    }
}

fn watcher(handle: AppHandle, app: Arc<App>) {
    let debug = std::env::var_os("VM_LAUNCH_DEBUG").is_some();
    let dbg = |m: String| if debug { eprintln!("{:?} vm-launch: {m}", SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default().as_secs() % 86400) };
    let obsws = vm_display().canonicalize().ok().and_then(|p| p.parent().map(|d| d.join("obsws.py")));
    let mut ino = inotify::Inotify::init().ok();
    if let Some(i) = ino.as_mut() {
        use inotify::WatchMask as W;
        let sd = vm_display_state();
        let _ = std::fs::create_dir_all(&sd);
        let _ = i.watches().add(LIBVIRT_RUN, W::CREATE | W::DELETE | W::MOVED_TO | W::MOVED_FROM);
        let _ = i.watches().add(&sd, W::CLOSE_WRITE | W::MOVED_TO | W::CREATE);
    }
    let uev = uevent_socket();
    let mut pidfds: HashMap<i32, OwnedFd> = HashMap::new();
    let mut last_slow: Option<Instant> = None;
    let mut settle_at: Option<Instant> = Some(Instant::now()); // first snapshot right away
    let mut follow_up = true; // after an event: one more look SETTLE later
    let mut ino_buf = [0u8; 4096];

    loop {
        // what to wait on: wake eventfd, inotify, uevents, every watched process
        let mut fds: Vec<libc::pollfd> = vec![libc::pollfd { fd: app.wake.as_raw_fd(), events: libc::POLLIN, revents: 0 }];
        if let Some(i) = ino.as_ref() {
            fds.push(libc::pollfd { fd: i.as_raw_fd(), events: libc::POLLIN, revents: 0 });
        }
        if let Some(u) = uev.as_ref() {
            fds.push(libc::pollfd { fd: u.as_raw_fd(), events: libc::POLLIN, revents: 0 });
        }
        let pid_base = fds.len();
        let pids: Vec<i32> = pidfds.keys().copied().collect();
        for p in &pids {
            fds.push(libc::pollfd { fd: pidfds[p].as_raw_fd(), events: libc::POLLIN, revents: 0 });
        }
        let now = Instant::now();
        let slow_due = last_slow.map_or(now, |t| t + SLOW_EVERY);
        let due = settle_at.map_or(slow_due, |s| s.min(slow_due));
        let timeout = due.saturating_duration_since(now).as_millis().min(i32::MAX as u128) as i32;
        let n = unsafe { libc::poll(fds.as_mut_ptr(), fds.len() as libc::nfds_t, timeout) };

        let mut changed = false;
        if n > 0 {
            for (k, f) in fds.iter().enumerate() {
                if f.revents == 0 {
                    continue;
                }
                if f.fd == app.wake.as_raw_fd() {
                    let mut v = 0u64;
                    unsafe { libc::read(f.fd, (&mut v as *mut u64).cast(), 8) };
                    dbg("wake".into());
                    changed = true;
                } else if ino.as_ref().is_some_and(|i| i.as_raw_fd() == f.fd) {
                    if let Some(i) = ino.as_mut() {
                        if let Ok(evs) = i.read_events(&mut ino_buf) {
                            for e in evs { dbg(format!("inotify {:?} {:?}", e.mask, e.name)); }
                        }
                    }
                    changed = true;
                } else if uev.as_ref().is_some_and(|u| u.as_raw_fd() == f.fd) {
                    let hit = drain_uevents(f.fd);
                    if hit { dbg("uevent: virtual DS4".into()); }
                    changed |= hit;
                } else if k >= pid_base {
                    pidfds.remove(&pids[k - pid_base]); // that process exited
                    dbg(format!("pid {} exited", pids[k - pid_base]));
                    changed = true;
                }
            }
        }
        if changed {
            // look now, and once more when follow-on changes (client mapping, DS4 enumerating) have landed
            settle_at = Some(Instant::now());
            follow_up = true;
        }
        let now = Instant::now();
        let run_slow = last_slow.is_none_or(|t| now >= t + SLOW_EVERY) && !app.busy.load(Ordering::Relaxed);
        let run_fast = settle_at.is_some_and(|t| now >= t);
        if !run_fast && !run_slow {
            continue;
        }

        let prev = app.state.lock().unwrap().clone();
        let (mut s, procs) = snapshot(&prev, app.busy.load(Ordering::Relaxed));
        // a VM or mode change can move the LG input: ask DDC soon instead of in up to 15 s
        let big_change = s.vm != prev.vm || s.mode != prev.mode;
        if run_slow || (big_change && !s.busy) {
            slow_probe(&mut s, &obsws);
            last_slow = Some(Instant::now());
        }
        // keep a pidfd for each process we show, to hear when it exits
        let live: Vec<i32> = [procs.client, procs.obs, procs.ds4_vm].into_iter().flatten().collect();
        pidfds.retain(|p, _| live.contains(p));
        for p in live {
            if let std::collections::hash_map::Entry::Vacant(e) = pidfds.entry(p) {
                if let Some(fd) = pidfd_open(p) {
                    e.insert(fd);
                }
            }
        }
        // one follow-up look after an event, then rest until the next event or slow probe
        if run_fast {
            settle_at = follow_up.then(|| now + SETTLE);
            follow_up = false;
        }
        dbg(format!("look (fast={run_fast} slow={run_slow}) vm={} client={} obs={} ds4={} lg={:?} watching {:?}", s.vm, s.client, s.obs, s.ds4_attached, s.lg_input, pidfds.keys().collect::<Vec<_>>()));
        if s != prev {
            dbg("state changed -> page".into());
            *app.state.lock().unwrap() = s.clone();
            let _ = handle.emit("state", s);
        }
    }
}

#[tauri::command]
fn get_state(app: State<'_, Arc<App>>) -> VmState {
    app.state.lock().unwrap().clone()
}

#[derive(Deserialize)]
struct LaunchOpts {
    display: String,
    picture: String,
    switch_input: bool,
    remote_play: bool,
    ds4: bool,
    record: bool,
}

#[derive(Clone, Serialize)]
struct StepEvent {
    id: String,
    state: String,
    detail: String,
}

/// Run `vm-display <args>` in a thread; forward its @step lines as "step" events and emit "finished" at the end.
fn spawn_vm_display(handle: AppHandle, app: Arc<App>, args: Vec<String>) -> Result<(), String> {
    if app.busy.swap(true, Ordering::SeqCst) {
        return Err("already working".into());
    }
    app.kick();
    let mut child = match Command::new(vm_display()).args(&args).stdout(Stdio::piped()).stderr(Stdio::null()).spawn() {
        Ok(c) => c,
        Err(e) => {
            app.busy.store(false, Ordering::SeqCst);
            app.kick();
            return Err(format!("could not run vm-display: {e}"));
        }
    };
    std::thread::spawn(move || {
        let mut done: Option<bool> = None;
        if let Some(out) = child.stdout.take() {
            for line in BufReader::new(out).lines().map_while(Result::ok) {
                if let Some(rest) = line.strip_prefix("@step ") {
                    let mut it = rest.splitn(3, ' ');
                    let _ = handle.emit("step", StepEvent {
                        id: it.next().unwrap_or_default().into(),
                        state: it.next().unwrap_or_default().into(),
                        detail: it.next().unwrap_or_default().into(),
                    });
                } else if let Some(r) = line.strip_prefix("@done ") {
                    done = Some(r.trim() == "ok");
                }
            }
        }
        let status_ok = child.wait().map(|s| s.success()).unwrap_or(false);
        app.busy.store(false, Ordering::SeqCst);
        app.kick();
        let _ = handle.emit("finished", serde_json::json!({ "ok": done.unwrap_or(status_ok), "action": args[0] }));
    });
    Ok(())
}

#[tauri::command]
fn launch(handle: AppHandle, app: State<'_, Arc<App>>, opts: LaunchOpts) -> Result<(), String> {
    let hdmi = opts.display == "hdmi";
    let mut args: Vec<String> = vec!["launch".into(), "--display".into(), if hdmi { "hdmi" } else { "lg" }.into()];
    if !hdmi {
        args.extend(["--picture".into(), if opts.picture == "native" { "native" } else { "fsr" }.into()]);
    }
    args.extend(["--ds4".into(), if opts.ds4 { "on" } else { "off" }.into()]);
    if hdmi && !opts.switch_input {
        args.push("--no-switch".into());
    }
    if opts.remote_play {
        args.push("--remote-play".into());
    }
    if opts.record && !hdmi {
        args.push("--record".into());
    }
    if app.dry_run {
        args.push("--dry-run".into());
    }
    spawn_vm_display(handle, app.inner().clone(), args)
}

/// One-shot actions on a running VM.
#[tauri::command]
fn action(handle: AppHandle, app: State<'_, Arc<App>>, name: String) -> Result<(), String> {
    match name.as_str() {
        "host" | "shutdown" | "client" => {
            if app.dry_run {
                let _ = handle.emit("finished", serde_json::json!({ "ok": true, "action": name, "dry_run": true }));
                return Ok(());
            }
            spawn_vm_display(handle, app.inner().clone(), vec![name])
        }
        _ => Err(format!("unknown action {name}")),
    }
}

#[tauri::command]
fn config(app: State<'_, Arc<App>>) -> serde_json::Value {
    let now = SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(0);
    serde_json::json!({ "dry_run": app.dry_run, "go": app.go, "vm": vm_name(), "now": now })
}

#[tauri::command]
fn quit(handle: AppHandle) {
    handle.exit(0);
}

fn main() {
    // --dry-run: run vm-display's dry run (nothing is touched); --go: launch with the last settings right away
    let dry_run = std::env::args().any(|a| a == "--dry-run");
    let go = std::env::args().any(|a| a == "--go");
    let wake = unsafe { OwnedFd::from_raw_fd(libc::eventfd(0, libc::EFD_CLOEXEC | libc::EFD_NONBLOCK)) };
    let app = Arc::new(App { state: Mutex::new(VmState::default()), busy: AtomicBool::new(false), wake, dry_run, go });
    tauri::Builder::default()
        .manage(app.clone())
        .setup(move |a| {
            let handle = a.handle().clone();
            let st = a.state::<Arc<App>>().inner().clone();
            std::thread::spawn(move || watcher(handle, st));
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![get_state, launch, action, config, quit])
        .run(tauri::generate_context!())
        .expect("tauri run failed");
}
