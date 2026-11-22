// Benchmark-only Rust port of ../../ds4gadget.py: same descriptors, same control handling and the
// same IN loop (build report, blocking EP write, sleep --sleep-us, default 4000). No external crates.
// Usage: ds4rs --pad /dev/input/eventN [--sleep-us N]. Not a replacement: no logging, no pad
// auto-detection, no reconnect logging. Built and run by ../run-bench.sh.
use std::fs::{File, OpenOptions};
use std::io::Read;
use std::os::fd::AsRawFd;
use std::sync::atomic::{AtomicBool, AtomicU8, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

unsafe extern "C" {
    fn ioctl(fd: i32, req: u64, ...) -> i32;
}
fn ioc(d: u64, t: u8, nr: u64, size: u64) -> u64 { (d << 30) | (size << 16) | ((t as u64) << 8) | nr }
fn io(nr: u64) -> u64 { ioc(0, b'U', nr, 0) }
fn iow(nr: u64, sz: u64) -> u64 { ioc(1, b'U', nr, sz) }
fn ior(nr: u64, sz: u64) -> u64 { ioc(2, b'U', nr, sz) }
fn iowr(nr: u64, sz: u64) -> u64 { ioc(3, b'U', nr, sz) }

#[derive(Clone, Copy)]
struct Pad { lx: u8, ly: u8, rx: u8, ry: u8, l2: u8, r2: u8, hx: i32, hy: i32, btn: u32 }
const NEUTRAL: Pad = Pad { lx: 128, ly: 128, rx: 128, ry: 128, l2: 0, r2: 0, hx: 0, hy: 0, btn: 0 };
// button bits
const SQ: u32 = 1; const CR: u32 = 2; const CI: u32 = 4; const TR: u32 = 8; const L1: u32 = 16; const R1: u32 = 32;
const SH: u32 = 64; const OP: u32 = 128; const L3: u32 = 256; const R3: u32 = 512; const PS: u32 = 1024; const TP: u32 = 2048;

fn report(s: &Pad, counter: u8, ts: u16) -> [u8; 64] {
    let hat = match (s.hx, s.hy) { (0, -1) => 0, (1, -1) => 1, (1, 0) => 2, (1, 1) => 3, (0, 1) => 4, (-1, 1) => 5, (-1, 0) => 6, (-1, -1) => 7, _ => 8 };
    let b = s.btn; let f = |m: u32, v: u8| if b & m != 0 { v } else { 0 };
    let mut r = [0u8; 64];
    r[0] = 1; r[1] = s.lx; r[2] = s.ly; r[3] = s.rx; r[4] = s.ry;
    r[5] = hat | f(SQ, 0x10) | f(CR, 0x20) | f(CI, 0x40) | f(TR, 0x80);
    r[6] = f(L1, 1) | f(R1, 2) | if s.l2 > 0 { 4 } else { 0 } | if s.r2 > 0 { 8 } else { 0 } | f(SH, 0x10) | f(OP, 0x20) | f(L3, 0x40) | f(R3, 0x80);
    r[7] = f(PS, 1) | f(TP, 2) | ((counter & 0x3f) << 2);
    r[8] = s.l2; r[9] = s.r2;
    r[10..12].copy_from_slice(&ts.to_le_bytes());
    r[12] = 0x1b;
    r[21..23].copy_from_slice(&8192i16.to_le_bytes());
    r[30] = 0x1b; r[33] = 1; r[34] = counter; r[35] = 0x80; r[39] = 0x80;
    r
}

fn absinfo(fd: i32, code: u64) -> (i32, i32) {
    let mut a = [0i32; 6];
    let req = ioc(2, b'E', 0x40 + code, 24);
    unsafe { ioctl(fd, req, a.as_mut_ptr()) };
    (a[1], a[2])
}

fn pad_thread(path: String, st: Arc<Mutex<Pad>>) {
    loop {
        let mut f = match File::open(&path) { Ok(f) => f, Err(_) => { std::thread::sleep(Duration::from_secs(1)); continue } };
        let fd = f.as_raw_fd();
        let rng: Vec<(i32, i32)> = (0..6).map(|i| absinfo(fd, i)).collect();
        let norm = |c: usize, v: i32| -> u8 {
            let (mn, mx) = rng[c]; let span = (mx - mn).max(1) as f64;
            (((v - mn) as f64 * 255.0 / span).round()).clamp(0.0, 255.0) as u8
        };
        let mut buf = [0u8; 24];
        loop {
            if f.read_exact(&mut buf).is_err() { *st.lock().unwrap() = NEUTRAL; break; }
            let typ = u16::from_ne_bytes([buf[16], buf[17]]);
            let code = u16::from_ne_bytes([buf[18], buf[19]]);
            let val = i32::from_ne_bytes([buf[20], buf[21], buf[22], buf[23]]);
            let mut s = st.lock().unwrap();
            match typ {
                1 => {
                    let m = match code { 0x130 => CR, 0x131 => CI, 0x133 => SQ, 0x134 => TR, 0x136 => L1, 0x137 => R1,
                                         0x13a => SH, 0x13b => OP, 0x13d => L3, 0x13e => R3, 0x13c => PS, 0xa7 => TP, _ => 0 };
                    if val != 0 { s.btn |= m } else { s.btn &= !m }
                }
                3 => match code {
                    0 => s.lx = norm(0, val), 1 => s.ly = norm(1, val), 3 => s.rx = norm(3, val), 4 => s.ry = norm(4, val),
                    2 => s.l2 = norm(2, val), 5 => s.r2 = norm(5, val), 0x10 => s.hx = val, 0x11 => s.hy = val, _ => {}
                },
                _ => {}
            }
        }
        std::thread::sleep(Duration::from_secs(1));
    }
}

const REPORT_DESC: &[u8] = include_bytes!("../../../ds4_report_desc.bin");

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let pad = args.iter().position(|a| a == "--pad").map(|i| args[i + 1].clone()).expect("--pad PATH");
    let sleep_us: u64 = args.iter().position(|a| a == "--sleep-us").map(|i| args[i + 1].parse().unwrap()).unwrap_or(4000);

    let mut dev = vec![18u8, 1, 0, 2, 0, 0, 0, 64]; dev.extend_from_slice(&[0x4c, 0x05, 0xc4, 0x05, 0, 1, 1, 2, 0, 1]);
    let hid = [9u8, 0x21, 0x11, 0x01, 0, 1, 0x22, (REPORT_DESC.len() & 0xff) as u8, (REPORT_DESC.len() >> 8) as u8];
    let ep_in = [7u8, 5, 0x84, 3, 64, 0, 5]; let ep_out = [7u8, 5, 0x03, 3, 64, 0, 5];
    let iface = [9u8, 4, 0, 0, 2, 3, 0, 0, 0];
    let mut body = iface.to_vec(); body.extend_from_slice(&hid); body.extend_from_slice(&ep_in); body.extend_from_slice(&ep_out);
    let tot = 9 + body.len();
    let mut cfg = vec![9u8, 2, tot as u8, (tot >> 8) as u8, 1, 1, 0, 0xc0, 0xfa]; cfg.extend_from_slice(&body);
    let s = |t: &str| { let mut v = vec![0u8, 3]; for u in t.encode_utf16() { v.extend_from_slice(&u.to_le_bytes()) } v[0] = v.len() as u8; v };
    let strings = [vec![4u8, 3, 9, 4], s("Sony Computer Entertainment"), s("Wireless Controller")];
    let f_a3: Vec<u8> = vec![0xA3, 0x41, 0x75, 0x67, 0x20, 0x20, 0x33, 0x20, 0x32, 0x30, 0x31, 0x33, 0, 0, 0, 0, 0, 0x30, 0x37, 0x3A, 0x30, 0x31, 0x3A, 0x31, 0x32, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0x01, 0x00, 0x31, 0x03, 0, 0, 0, 0x49, 0x00, 0x05, 0, 0, 0x80, 0x03, 0];
    let f_02: Vec<u8> = vec![0x02, 0x01, 0, 0, 0, 0, 0, 0x87, 0x22, 0x7B, 0xDD, 0xB2, 0x22, 0x47, 0xDD, 0xBD, 0x22, 0x43, 0xDD, 0x1C, 0x02, 0x1C, 0x02, 0x7F, 0x1E, 0x2E, 0xDF, 0x60, 0x1F, 0x4C, 0xE0, 0x3A, 0x1D, 0xC6, 0xDE, 0x08, 0x00];
    let mut f_12 = vec![0x12u8, 0x1C, 0x66, 0x6D, 0x07, 0x09, 0x8B, 0x08, 0x25, 0x00]; f_12.extend_from_slice(&[0; 6]);

    let file = OpenOptions::new().read(true).write(true).open("/dev/raw-gadget").expect("raw-gadget");
    let fd = file.as_raw_fd();
    let mut init = [0u8; 257]; init[..9].copy_from_slice(b"dummy_udc"); init[128..139].copy_from_slice(b"dummy_udc.0"); init[256] = 2;
    unsafe { assert!(ioctl(fd, iow(0, 257), init.as_mut_ptr()) >= 0); ioctl(fd, io(1), 0); }

    let st = Arc::new(Mutex::new(NEUTRAL));
    { let st = st.clone(); std::thread::spawn(move || pad_thread(pad, st)); }
    let configured = Arc::new(AtomicBool::new(false));
    let ep_handle = Arc::new(AtomicU8::new(255));
    { // IN loop: same structure as the Python version
        let (st, conf, eph) = (st.clone(), configured.clone(), ep_handle.clone());
        std::thread::spawn(move || {
            let t0 = Instant::now(); let mut counter: u8 = 0;
            loop {
                if !conf.load(Ordering::Acquire) { std::thread::sleep(Duration::from_millis(10)); continue }
                let ep = eph.load(Ordering::Acquire) as u16;
                counter = counter.wrapping_add(1);
                let ts = (t0.elapsed().as_micros() as f64 / 5.33) as u64 as u16;
                let r = { let s = st.lock().unwrap(); report(&s, counter, ts) };
                let mut buf = [0u8; 72];
                buf[0..2].copy_from_slice(&ep.to_le_bytes()); buf[4..8].copy_from_slice(&64u32.to_le_bytes()); buf[8..].copy_from_slice(&r);
                let rc = unsafe { ioctl(fd, iow(7, 8), buf.as_mut_ptr()) };
                if rc < 0 { std::thread::sleep(Duration::from_millis(20)) }
                if sleep_us > 0 { std::thread::sleep(Duration::from_micros(sleep_us)) }
            }
        });
    }
    let ep0_write = |data: &[u8], ln: usize| {
        let n = data.len().min(ln);
        let mut b = vec![0u8; 8 + n]; b[4..8].copy_from_slice(&(n as u32).to_le_bytes()); b[8..].copy_from_slice(&data[..n]);
        unsafe { ioctl(fd, iow(3, 8), b.as_mut_ptr()) };
    };
    let ep0_read = |n: usize| {
        let mut b = vec![0u8; 8 + n]; b[4..8].copy_from_slice(&(n as u32).to_le_bytes());
        unsafe { ioctl(fd, iowr(4, 8), b.as_mut_ptr()) };
    };
    let mut ein: i32 = -1;
    loop {
        let mut ev = [0u8; 16]; ev[4..8].copy_from_slice(&8u32.to_le_bytes());
        if unsafe { ioctl(fd, ior(2, 8), ev.as_mut_ptr()) } < 0 { continue }
        let t = u32::from_le_bytes([ev[0], ev[1], ev[2], ev[3]]);
        if t == 5 || t == 6 { configured.store(false, Ordering::Release); if ein >= 0 { unsafe { ioctl(fd, iow(6, 4), &(ein as u32)) }; } ein = -1; continue }
        if t != 2 { continue }
        let sp = &ev[8..16];
        let (rt, req) = (sp[0], sp[1]);
        let val = u16::from_le_bytes([sp[2], sp[3]]); let ln = u16::from_le_bytes([sp[6], sp[7]]) as usize;
        let typ = (rt >> 5) & 3;
        let (hi, lo) = ((val >> 8) as u8, (val & 0xff) as u8);
        match (typ, req) {
            (0, 6) => match hi {
                1 => ep0_write(&dev, ln), 2 => ep0_write(&cfg, ln), 0x22 => ep0_write(REPORT_DESC, ln), 0x21 => ep0_write(&hid, ln),
                3 if (lo as usize) < strings.len() => ep0_write(&strings[lo as usize], ln),
                _ => unsafe { ioctl(fd, io(12), 0); },
            },
            (0, 9) => {
                if val != 0 && ein < 0 {
                    let mut d = [0u8; 9]; d[..7].copy_from_slice(&ep_in); ein = unsafe { ioctl(fd, iow(5, 9), d.as_mut_ptr()) };
                    let mut d = [0u8; 9]; d[..7].copy_from_slice(&ep_out); unsafe { ioctl(fd, iow(5, 9), d.as_mut_ptr()) };
                    unsafe { ioctl(fd, iow(10, 4), &250u32); ioctl(fd, io(9), 0); }
                    ep_handle.store(ein as u8, Ordering::Release); configured.store(true, Ordering::Release);
                }
                ep0_read(0);
            }
            (0, 0xb) | (0, 1) | (0, 3) | (1, 0xa) | (1, 0xb) => ep0_read(0),
            (0, 0) => ep0_write(&[0, 0], ln), (0, 8) => ep0_write(&[1], ln),
            (1, 1) if hi == 3 => match lo { 0xa3 => ep0_write(&f_a3, ln), 0x02 => ep0_write(&f_02, ln), 0x12 => ep0_write(&f_12, ln), _ => unsafe { ioctl(fd, io(12), 0); } },
            (1, 1) => { let r = report(&st.lock().unwrap(), 0, 0); ep0_write(&r, ln) }
            (1, 9) => ep0_read(ln),
            (1, 2) => ep0_write(&[0], ln), (1, 3) => ep0_write(&[1], ln),
            _ => { if rt & 0x80 == 0 && ln > 0 { ep0_read(ln) } unsafe { ioctl(fd, io(12), 0); } }
        }
    }
}
