//! Talk to a libvirt guest through qemu-guest-agent: run PowerShell, copy files in and out.
//!
//! One libvirt connection per run, commands go straight through `virDomainQemuAgentCommand`
//! (no `virsh` process per call).

use std::fs::File;
use std::io::{self, BufWriter, Read, Write};
use std::path::{Path, PathBuf};
use std::process::ExitCode;
use std::thread::sleep;
use std::time::{Duration, Instant};

use anyhow::{Context, Result, bail};
use base64::Engine;
use base64::engine::general_purpose::STANDARD as B64;
use clap::{Parser, Subcommand};
use serde_json::{Value, json};
use virt::connect::Connect;
use virt::domain::Domain;

/// Bigger `guest-file-write` chunks hang the Windows agent and leak the handle.
const PUSH_CHUNK: usize = 64 << 10;
const PULL_CHUNK: usize = 1 << 20;
/// Seconds libvirt waits for one agent reply.
const AGENT_TIMEOUT: i32 = 120;

#[derive(Parser)]
#[command(version, about)]
struct Cli {
    /// libvirt domain name.
    #[arg(long, env = "VM", default_value = "win11", global = true)]
    vm: String,
    #[command(subcommand)]
    cmd: Cmd,
}

#[derive(Subcommand)]
enum Cmd {
    /// Run the PowerShell script on stdin in the guest (as SYSTEM) and exit with its exit code.
    Exec {
        /// Give up after this many seconds.
        #[arg(default_value_t = 300)]
        timeout: u64,
    },
    /// Copy a host file into the guest.
    Push { src: PathBuf, dst: String },
    /// Copy a guest file to the host.
    Pull { src: String, dst: PathBuf },
}

struct Agent {
    dom: Domain,
    // Keeps the connection alive for as long as the domain handle is used.
    _conn: Connect,
}

impl Agent {
    fn open(vm: &str) -> Result<Self> {
        // libvirt prints every error to stderr itself; ours already carries the message.
        virt::error::clear_error_callback();
        let conn = Connect::open(Some("qemu:///system")).context("connecting to qemu:///system")?;
        let dom = conn.lookup_domain_by_name(vm).with_context(|| format!("no libvirt domain {vm:?}"))?;
        Ok(Self { dom, _conn: conn })
    }

    fn call(&self, execute: &str, arguments: &Value) -> Result<Value> {
        let cmd = json!({ "execute": execute, "arguments": arguments }).to_string();
        let reply = self
            .dom
            .qemu_agent_command(&cmd, AGENT_TIMEOUT, 0)
            .with_context(|| format!("{execute} failed (is the guest agent running?)"))?;
        let mut v: Value = serde_json::from_str(&reply)?;
        Ok(v["return"].take())
    }

    fn file_close(&self, handle: &Value) -> Result<()> {
        self.call("guest-file-close", &json!({ "handle": handle })).map(drop)
    }
}

fn b64_field(v: &Value, key: &str) -> Result<Vec<u8>> {
    v[key].as_str().map_or(Ok(Vec::new()), |s| Ok(B64.decode(s)?))
}

/// Returns the guest's exit code.
fn exec(agent: &Agent, timeout: Duration) -> Result<i64> {
    let mut script = String::new();
    io::stdin().read_to_string(&mut script)?;
    let utf16: Vec<u8> = script.encode_utf16().flat_map(u16::to_le_bytes).collect();
    let pid = agent.call(
        "guest-exec",
        &json!({
            "path": "powershell.exe",
            "arg": ["-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                    "-EncodedCommand", B64.encode(utf16)],
            "capture-output": true,
        }),
    )?["pid"]
        .clone();

    let start = Instant::now();
    let mut wait = Duration::from_millis(100);
    let status = loop {
        let st = agent.call("guest-exec-status", &json!({ "pid": pid }))?;
        if st["exited"].as_bool() == Some(true) {
            break st;
        }
        if start.elapsed() > timeout {
            bail!("timeout, guest pid {pid} still running");
        }
        sleep(wait);
        wait = (wait * 2).min(Duration::from_secs(1));
    };
    io::stdout().write_all(&b64_field(&status, "out-data")?)?;
    io::stderr().write_all(&b64_field(&status, "err-data")?)?;
    let code = status["exitcode"].as_i64().unwrap_or(-1);
    eprintln!("\n[exit {code}]");
    Ok(code)
}

fn push(agent: &Agent, src: &Path, dst: &str) -> Result<u64> {
    let mut file = File::open(src).with_context(|| format!("opening {}", src.display()))?;
    let handle = agent.call("guest-file-open", &json!({ "path": dst, "mode": "wb" }))?;
    let mut copy = || -> Result<u64> {
        let mut buf = vec![0; PUSH_CHUNK];
        let mut total = 0;
        loop {
            let n = file.read(&mut buf)?;
            if n == 0 {
                return Ok(total);
            }
            agent.call("guest-file-write", &json!({ "handle": handle, "buf-b64": B64.encode(&buf[..n]) }))?;
            total += n as u64;
        }
    };
    let result = copy();
    agent.file_close(&handle)?;
    result
}

fn pull(agent: &Agent, src: &str, dst: &Path) -> Result<u64> {
    let mut part = dst.as_os_str().to_owned();
    part.push(".part");
    let part = PathBuf::from(part);
    let mut out = BufWriter::new(File::create(&part).with_context(|| format!("creating {}", part.display()))?);
    let handle = agent.call("guest-file-open", &json!({ "path": src, "mode": "rb" }))?;
    let start = Instant::now();
    let mut copy = || -> Result<u64> {
        let mut total = 0u64;
        loop {
            let r = agent.call("guest-file-read", &json!({ "handle": handle, "count": PULL_CHUNK }))?;
            let data = b64_field(&r, "buf-b64")?;
            out.write_all(&data)?;
            total += data.len() as u64;
            if data.is_empty() || r["eof"].as_bool() == Some(true) {
                return Ok(total);
            }
            if total % (128 << 20) < PULL_CHUNK as u64 {
                eprintln!("{} MiB, {:.1} MB/s", total >> 20, total as f64 / start.elapsed().as_secs_f64() / 1e6);
            }
        }
    };
    let result = copy();
    agent.file_close(&handle)?;
    let total = result?;
    out.into_inner().map_err(io::IntoInnerError::into_error)?.sync_all()?;
    std::fs::rename(&part, dst)?;
    Ok(total)
}

fn main() -> Result<ExitCode> {
    let cli = Cli::parse();
    let agent = Agent::open(&cli.vm)?;
    let start = Instant::now();
    let bytes = match cli.cmd {
        Cmd::Exec { timeout } => {
            let code = exec(&agent, Duration::from_secs(timeout))?;
            // Windows exit codes are 32-bit; anything outside 0..=255 reads as a plain failure.
            return Ok(u8::try_from(code).map_or(ExitCode::FAILURE, ExitCode::from));
        }
        Cmd::Push { src, dst } => push(&agent, &src, &dst)?,
        Cmd::Pull { src, dst } => pull(&agent, &src, &dst)?,
    };
    let secs = start.elapsed().as_secs_f64();
    eprintln!("done {bytes} bytes in {secs:.1}s ({:.1} MB/s)", bytes as f64 / secs / 1e6);
    Ok(ExitCode::SUCCESS)
}
