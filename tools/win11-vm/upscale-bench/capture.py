#!/usr/bin/env python3
"""Live half of the upscaling test: show frames in the win11 guest, capture what the Looking Glass client draws
on the host's DP-1 (native 3840x2160), for each client scaling condition.

  capture.py [--only COND,...] [--skip-4k]

Phases
  0. checks: VM running, guest agent up, the AtLogon layout task has already fired
  1. upload: viewer.ps1, calibration patterns, frames (metadata-free PNGs) to C:\\Tools\\upscale\\
  2. 4K path check (guest at 3840x2160 @150%, task UT-Res4K): show 4K noise + 4K reference frames; the capture must
     equal them (proves the guest->Looking Glass->DP-1->screenshot path is lossless, so the "guest at 4K" case
     only loses what the Remote Play client's own scaler loses, which is modelled offline)
  3. guest to 1920x1080 @100% (display-layout.ps1 through a user-session task)
  4. per condition: restart the client with that condition's options, fullscreen on DP-1, then
       - calibration: 1080p noise; with nearest it must decimate exactly back to the noise (validates the path,
         alignment and colour; aborts the run otherwise); every condition's calibration capture is kept
       - frames: every scene frame of the frame sets in COND_SETS; each capture is matched against all 8
         frames of its scene and retried if it matches another one (stale frame)
  finally: viewer closed, temp tasks removed, guest back to its default layout (Display-lgonly), the normal client
           restarted (vm-display client) and checked to receive frames

Output: $UPSCALE_DIR/work/cap/<cond>/<set>/<scene>/NN.png, .../cap/<cond>/calib.png, .../capture.json
"""
import argparse, base64, hashlib, json, ntpath, os, subprocess, sys, time
import numpy as np
from PIL import Image
import libvirt, libvirt_qemu

ROOT = os.path.expanduser(os.environ.get("UPSCALE_DIR", "~/Videos/vm-upscale-test"))   # src/ (clips) and work/ (everything generated)
W = os.path.join(ROOT, "work")
SUITE = os.path.dirname(os.path.abspath(__file__))
VM = os.environ.get("VM", "win11")
GDIR = r"C:\Tools\upscale"
KWIN_JS = os.path.join(SUITE, "..", "looking-glass", "lg-client-to-output.js")
BASE_OPTS = ["app:renderer=EGL", "win:alerts=no", "win:quickSplash=yes", "win:title=Windows VM",
             "spice:enable=no", "input:showCursorDot=no", "input:hideCursor=yes"]
# condition -> client options (on top of BASE_OPTS); egl:scale 0=auto 1=nearest 2=linear
CONDS = {
    "nearest":    ["egl:scale=1"],
    "linear":     ["egl:scale=2"],
    "auto":       ["egl:scale=0"],
    "fsr1.0":     ["egl:scale=0", "eglFilter:ffxFSR=yes", "eglFilter:ffxFSRSharpness=1.0"],
    "fsr0.5":     ["egl:scale=0", "eglFilter:ffxFSR=yes", "eglFilter:ffxFSRSharpness=0.5"],
    "fsr0.0":     ["egl:scale=0", "eglFilter:ffxFSR=yes", "eglFilter:ffxFSRSharpness=0.0"],
    "cas0.5":     ["egl:scale=2", "eglFilter:ffxCAS=yes", "eglFilter:ffxCASSharpness=0.5"],
    "fsr1.0+cas0.5": ["egl:scale=0", "eglFilter:ffxFSR=yes", "eglFilter:ffxFSRSharpness=1.0",
                      "eglFilter:ffxCAS=yes", "eglFilter:ffxCASSharpness=0.5"],
}
COND_SETS = {c: ["hevc15"] for c in CONDS}
COND_SETS["linear"] = ["hevc15", "raw"]
COND_SETS["fsr1.0"] = ["hevc15", "raw"]
REF4K_SCENES = ["map", "trailer"]
meta = json.load(open(os.path.join(W, "prep.json")))
SCENES, NF = list(meta["scenes"]), meta["nf"]

def log(m): print(time.strftime("%H:%M:%S ") + m, flush=True)

# ------------------------------------------------------------------ guest agent
libvirt.registerErrorHandler(lambda *a: None, None)
conn = libvirt.open("qemu:///system")
dom = conn.lookupByName(VM)
def qga(cmd, t=60):
    return json.loads(libvirt_qemu.qemuAgentCommand(dom, json.dumps(cmd), t, 0))["return"]

def gps(script, timeout=120):
    """PowerShell in the guest as SYSTEM; returns (exitcode, stdout+stderr)."""
    enc = base64.b64encode(("$ProgressPreference='SilentlyContinue'; " + script).encode("utf-16-le")).decode()
    pid = qga({"execute": "guest-exec", "arguments": {"path": "powershell.exe", "capture-output": True,
               "arg": ["-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-EncodedCommand", enc]}})["pid"]
    t0 = time.time()
    while time.time() - t0 < timeout:
        st = qga({"execute": "guest-exec-status", "arguments": {"pid": pid}})
        if st.get("exited"):
            out = base64.b64decode(st.get("out-data", "")).decode("utf-8", "replace")
            err = base64.b64decode(st.get("err-data", "")).decode("utf-8", "replace")
            if st.get("exitcode") and "CLIXML" not in err[:20]:
                log(f"  guest stderr: {err.strip()[:300]}")
            return st.get("exitcode"), out
        time.sleep(0.2)
    raise TimeoutError(script[:80])

def gwrite(path, data):
    h = qga({"execute": "guest-file-open", "arguments": {"path": path, "mode": "wb"}})
    try:
        for i in range(0, len(data), 64 << 10):
            qga({"execute": "guest-file-write", "arguments": {"handle": h, "buf-b64": base64.b64encode(data[i:i + (64 << 10)]).decode()}})
    finally:
        qga({"execute": "guest-file-close", "arguments": {"handle": h}})

def gread(path):
    try:
        h = qga({"execute": "guest-file-open", "arguments": {"path": path, "mode": "rb"}}, 10)
    except libvirt.libvirtError:
        return None
    try:
        r = qga({"execute": "guest-file-read", "arguments": {"handle": h, "count": 4096}}, 10)
        return base64.b64decode(r.get("buf-b64", "")).decode("utf-8", "replace")
    finally:
        qga({"execute": "guest-file-close", "arguments": {"handle": h}}, 10)

# ------------------------------------------------------------------ files
def staged(src, rel):
    """Metadata-free copy (no gAMA/cHRM/iCCP: GDI+ would colour-correct those) under work/stage."""
    dst = os.path.join(W, "stage", rel)
    if not os.path.exists(dst):
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        Image.open(src).convert("RGB").save(dst, optimize=False, compress_level=1)
    return dst

def calib_patterns():
    rng = np.random.default_rng(612)
    out = {}
    for name, (w, h) in {"calib1080": (1920, 1080), "calib4k": (3840, 2160)}.items():
        p = os.path.join(W, "stage", f"{name}.png")
        if not os.path.exists(p):
            os.makedirs(os.path.dirname(p), exist_ok=True)
            Image.fromarray(rng.integers(0, 256, (h, w, 3), dtype=np.uint8)).save(p, compress_level=1)
        out[name] = p
    return out

def upload(files):
    """files: {guest_rel: host_path}; skips files whose size+md5 already match."""
    code, out = gps(f"$r=@{{}}; if(Test-Path '{GDIR}'){{ Get-ChildItem '{GDIR}' -Recurse -File | % {{ $r[$_.FullName.Substring({len(GDIR) + 1})] = (Get-FileHash $_.FullName -Algorithm MD5).Hash }} }}; $r | ConvertTo-Json -Compress", 600)
    have = json.loads(out.strip() or "{}") if out.strip().startswith("{") else {}
    dirs = sorted({ntpath.dirname(r) for r in files if ntpath.dirname(r)})
    gps("; ".join(f"New-Item -ItemType Directory -Force -Path '{GDIR}\\{d}' | Out-Null" for d in ["", *dirs]), 60)
    todo = {r: p for r, p in files.items() if have.get(r, "").lower() != hashlib.md5(open(p, "rb").read()).hexdigest()}
    log(f"upload: {len(todo)} of {len(files)} files to send")
    t0 = time.time(); n = 0
    for i, (r, p) in enumerate(sorted(todo.items())):
        d = open(p, "rb").read(); gwrite(f"{GDIR}\\{r}", d); n += len(d)
        if i % 20 == 19: log(f"  {i + 1}/{len(todo)}  {n / 1e6 / (time.time() - t0):.1f} MB/s")

# ------------------------------------------------------------------ guest tasks / viewer
def user_of_tasks():
    _, out = gps("(Get-ScheduledTask -TaskName Display-lgonly).Principal.UserId")
    return out.strip()

def register_tasks():
    u = user_of_tasks(); assert u, "no Display-lgonly task"
    ps = (f"$p = New-ScheduledTaskPrincipal -UserId '{u}' -LogonType Interactive -RunLevel Limited; "
          "$s = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 3); "
          f"$a = New-ScheduledTaskAction -Execute powershell.exe -Argument '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File {GDIR}\\viewer.ps1'; "
          "Register-ScheduledTask -TaskName UT-Viewer -Action $a -Principal $p -Settings $s -Force | Out-Null; "
          f"$a = New-ScheduledTaskAction -Execute powershell.exe -Argument '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -Command \"& C:\\Tools\\display\\display-layout.ps1 -Layout lgonly -Width 1920 -Height 1080 -Scale 100 *> {GDIR}\\res1080.out\"'; "
          "Register-ScheduledTask -TaskName UT-Res1080 -Action $a -Principal $p -Settings $s -Force | Out-Null; "
          f"$a = New-ScheduledTaskAction -Execute powershell.exe -Argument '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -Command \"& C:\\Tools\\display\\display-layout.ps1 -Layout lgonly -Width 3840 -Height 2160 -Scale 150 *> {GDIR}\\res4k.out\"'; "
          "Register-ScheduledTask -TaskName UT-Res4K -Action $a -Principal $p -Settings $s -Force | Out-Null; 'ok'")
    code, out = gps(ps); assert "ok" in out, out

_token = [int(time.time())]
def viewer_cmd(arg, timeout=15):
    _token[0] += 1; tok = str(_token[0])
    gwrite(f"{GDIR}\\cmd.txt", f"{tok} {arg}".encode())
    t0 = time.time()
    while time.time() - t0 < timeout:
        a = gread(f"{GDIR}\\ack.txt")
        if a and a.split(" ", 1)[0] == tok:
            if "error" in a: raise RuntimeError(a)
            return a
        time.sleep(0.05)
    raise TimeoutError(f"viewer did not ack {arg}")

def viewer_start():
    gps(f"Stop-ScheduledTask UT-Viewer -EA SilentlyContinue; Remove-Item '{GDIR}\\ack.txt','{GDIR}\\cmd.txt' -EA SilentlyContinue; Start-ScheduledTask UT-Viewer")
    time.sleep(4)

def viewer_stop():
    try: viewer_cmd("quit", 5)
    except Exception: pass
    gps("Stop-ScheduledTask UT-Viewer -EA SilentlyContinue")

def guest_res(task, outfile, wait=14):
    gps(f"Start-ScheduledTask {task}"); time.sleep(wait)
    _, out = gps(f"Get-Content '{outfile}' -EA SilentlyContinue")
    return out.strip()

# ------------------------------------------------------------------ host client + capture
def sh(*a, check=False):
    return subprocess.run(a, capture_output=True, text=True, check=check)

def client_pids():
    """PIDs of every looking-glass-client (it rewrites its argv, so match the comm name, not the command line)."""
    out = []
    for d in os.listdir("/proc"):
        if d.isdigit():
            try:
                if open(f"/proc/{d}/comm").read().strip() == "looking-glass-c": out.append(int(d))
            except OSError: pass
    return out

def clients_stop():
    for u in ("lg-client.service", "ut-lgc.service"):     # one at a time: a missing unit makes a joint stop do nothing
        sh("systemctl", "--user", "stop", u)
    for _ in range(50):
        if not client_pids(): return
        time.sleep(0.1)
    for pid in client_pids():
        try: os.kill(pid, 15)
        except OSError: pass
    time.sleep(1)
    assert not client_pids(), f"could not stop Looking Glass clients {client_pids()}"

def idd_restart():
    """The guest's Looking Glass display driver can stop sending frames after many client restarts and mode
    switches (seen 2026-11-21); disabling and re-enabling the device brings it back."""
    log("  guest sends no frames: restarting the Looking Glass display device")
    gps('$d = Get-PnpDevice -FriendlyName "Looking Glass Indirect Display Device"; '
        'Disable-PnpDevice -InstanceId $d.InstanceId -Confirm:$false; Start-Sleep 3; '
        'Enable-PnpDevice -InstanceId $d.InstanceId -Confirm:$false; Start-Sleep 5', 90)

def lgc_log_size():
    p = os.path.join(W, "lgc.log"); return os.path.getsize(p) if os.path.exists(p) else 0

def frames_arriving(since, secs=8):
    t0 = time.time()
    while time.time() - t0 < secs:
        with open(os.path.join(W, "lgc.log"), "rb") as f:
            f.seek(since)
            if b"Format:" in f.read(): return True
        time.sleep(0.5)
    return False

GUEST_1080 = [False]      # set once the guest has been switched to 1080p (recovery has to restore it)

def client(opts, _recover=True):
    """Start the test client; make sure the guest is actually sending it frames (recover the guest driver if not)."""
    since = lgc_log_size()
    client_start(opts)
    if frames_arriving(since):
        return
    for img in (f"{GDIR}\\f\\hevc15\\map\\01.png", f"{GDIR}\\calib1080.png"):   # a redraw usually wakes it
        try: viewer_cmd(img)
        except Exception: pass
    if frames_arriving(since, 5):
        return
    if not _recover:
        raise SystemExit("guest still sends no frames after restarting its display device")
    idd_restart()
    if GUEST_1080[0]:
        log("  " + guest_res("UT-Res1080", f"{GDIR}\\res1080.out").replace("\n", " | "))
        viewer_start()
    client(opts, _recover=False)

def client_start(opts):
    clients_stop()
    sh("systemd-run", "--user", "--unit=ut-lgc", "--collect", "-p", "StandardOutput=append:" + os.path.join(W, "lgc.log"),
       "-p", "StandardError=append:" + os.path.join(W, "lgc.log"), "/usr/bin/looking-glass-client", *BASE_OPTS, *opts, check=True)
    time.sleep(3)
    for _ in range(2):
        r = sh("qdbus6", "org.kde.KWin", "/Scripting", "org.kde.kwin.Scripting.loadScript", KWIN_JS, "ut-place")
        sid = r.stdout.strip()
        if sid:
            sh("qdbus6", "org.kde.KWin", f"/Scripting/Script{sid}", "org.kde.kwin.Script.run"); time.sleep(0.5)
        sh("qdbus6", "org.kde.KWin", "/Scripting", "org.kde.kwin.Scripting.unloadScript", "ut-place"); time.sleep(1.5)
    time.sleep(2)
    main_pid = int(sh("systemctl", "--user", "show", "ut-lgc", "-p", "MainPID", "--value").stdout.strip() or 0)
    assert client_pids() == [main_pid], f"expected only the test client {main_pid}, found {client_pids()}"

def restore_user_client():
    """Normal client back (vm-display client); verify it gets frames, else recover the guest driver once."""
    for attempt in range(2):
        t = time.strftime("%Y-%m-%d %H:%M:%S")
        sh(os.path.expanduser("~/.local/bin/vm-display"), "client"); time.sleep(6)
        j = sh("journalctl", "--user", "-u", "lg-client", "--since", t, "--no-pager").stdout
        if "Format:" in j:
            log(f"  normal client receiving frames, clients now: {client_pids()}"); return
        if attempt == 0:
            idd_restart()
            log("  " + guest_res("Display-lgonly", r"C:\Tools\display\last-lgonly.out").replace("\n", " | "))
    log("  WARNING: the normal client gets no frames; run: vm-display lg")

def shot(path):
    tmp = os.path.join(W, "full.png")
    sh("spectacle", "-b", "-n", "-f", "-o", tmp, check=True)
    im = Image.open(tmp).convert("RGB")
    assert im.size == (7680, 2160), im.size          # DP-1 (0,0) + DP-2, native pixels
    im = im.crop((0, 0, 3840, 2160))
    if path:
        os.makedirs(os.path.dirname(path), exist_ok=True); im.save(path, compress_level=1)
    return np.asarray(im)

def psnr(a, b):
    m = float(np.mean((a.astype(np.float32) - b.astype(np.float32)) ** 2)); return 99.0 if m == 0 else float(10 * np.log10(255 ** 2 / m))

def down(a):   # 2x box downscale, 3840x2160 -> 1920x1080
    return a.reshape(1080, 2, 1920, 2, 3).astype(np.float32).mean(axis=(1, 3))

def show_and_shot(guest_rel, out, expect=None, candidates=None, tries=3):
    for attempt in range(tries):
        if attempt:                      # force a redraw: show something else first, then the frame again
            viewer_cmd(f"{GDIR}\\calib1080.png"); time.sleep(0.5)
        ack = viewer_cmd(f"{GDIR}\\{guest_rel}")
        time.sleep(0.35 + 0.3 * attempt)
        cap = shot(out)
        if candidates is None:
            return cap, ack, None
        d = down(cap)
        scores = [psnr(d, c) for c in candidates]
        # frames within 40 dB of the expected one are near-duplicates (30 fps game in a 60 fps upload): equivalent
        others = [scores[j] for j in range(len(candidates)) if j != expect and psnr(candidates[j], candidates[expect]) < 40]
        if scores[expect] > 25 and (not others or scores[expect] >= max(others) - 0.05):
            return cap, ack, scores
        log(f"  stale? {guest_rel}: best={int(np.argmax(scores))} want={expect} {['%.1f' % s for s in scores]}; retry")
    raise RuntimeError(f"capture never matched {guest_rel}")

# ------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--only"); ap.add_argument("--skip-4k", action="store_true")
    a = ap.parse_args()
    conds = a.only.split(",") if a.only else list(CONDS)
    assert dom.state()[0] == libvirt.VIR_DOMAIN_RUNNING, "VM not running"
    qga({"execute": "guest-ping"})
    _, out = gps("$i = Get-ScheduledTaskInfo Display-lgonly-AtLogon; $u = (Get-CimInstance Win32_OperatingSystem).LastBootUpTime; "
                 "'{0}|{1}|{2}' -f $i.LastRunTime.ToString('o'), $u.ToString('o'), (Get-Date).ToString('o')")
    log(f"AtLogon last run | boot | now: {out.strip()}")
    cal = calib_patterns()
    files = {"viewer.ps1": os.path.join(SUITE, "viewer.ps1"), "calib1080.png": cal["calib1080"], "calib4k.png": cal["calib4k"]}
    sets = sorted({s for c in conds for s in COND_SETS[c]})
    for st in sets:
        for sc in SCENES:
            for i in range(1, NF + 1):
                rel = f"f\\{st}\\{sc}\\{i:02d}.png"
                files[rel] = staged(os.path.join(W, "f1080", st, sc, f"{i:02d}.png"), os.path.join("f1080", st, sc, f"{i:02d}.png"))
    for sc in REF4K_SCENES:
        for i in range(1, NF + 1):
            files[f"ref\\{sc}\\{i:02d}.png"] = staged(os.path.join(W, "ref", sc, f"{i:02d}.png"), os.path.join("ref", sc, f"{i:02d}.png"))
    upload(files)
    register_tasks()
    cj = os.path.join(W, "capture.json")
    res = json.load(open(cj)) if os.path.exists(cj) else {}
    res.setdefault("conds", {}); res.setdefault("path4k", {})
    capdir = os.path.join(W, "cap")
    try:
        # ---- 4K path check
        if not a.skip_4k:
            log("phase: 4K path check (guest at 3840x2160)")
            log("  " + guest_res("UT-Res4K", f"{GDIR}\\res4k.out").replace("\n", " | "))
            client(CONDS["linear"]); viewer_start()
            cap, ack, _ = show_and_shot("calib4k.png", os.path.join(capdir, "path4k", "calib4k.png"))
            ref = np.asarray(Image.open(cal["calib4k"]).convert("RGB"))
            diff = np.abs(cap.astype(int) - ref.astype(int))
            res["path4k"]["calib"] = {"ack": ack, "psnr": psnr(cap, ref), "exact_frac": float((diff.max(axis=2) == 0).mean()), "maxdiff": int(diff.max())}
            log(f"  4K noise: {res['path4k']['calib']}")
            for sc in REF4K_SCENES:
                for i in range(1, NF + 1):
                    cap, ack, _ = show_and_shot(f"ref\\{sc}\\{i:02d}.png", os.path.join(capdir, "path4k", sc, f"{i:02d}.png"))
                    ref = np.asarray(Image.open(files[f"ref\\{sc}\\{i:02d}.png"]).convert("RGB"))
                    res["path4k"][f"{sc}/{i:02d}"] = psnr(cap, ref)
            log(f"  4K frames: min PSNR {min(v for k, v in res['path4k'].items() if k != 'calib'):.2f} dB")
            viewer_stop()
        # ---- guest to 1080p
        log("phase: guest to 1920x1080 @100%")
        log("  " + guest_res("UT-Res1080", f"{GDIR}\\res1080.out").replace("\n", " | "))
        GUEST_1080[0] = True
        viewer_start()
        ack = viewer_cmd(f"{GDIR}\\calib1080.png"); log(f"  viewer: {ack}")
        assert "screen=1920x1080" in ack, f"guest is not at 1920x1080: {ack}"
        noise = np.asarray(Image.open(cal["calib1080"]).convert("RGB"))
        for c in conds:
            log(f"condition {c}: {' '.join(CONDS[c])}")
            client(CONDS[c])
            # a new client gets no frame until the guest redraws (the display driver only sends changes), so
            # flip to another image before the pattern; retry until the capture is not the client's splash
            for attempt in range(3):
                viewer_cmd(f"{GDIR}\\f\\hevc15\\map\\01.png"); time.sleep(0.6)
                cap, ack, _ = show_and_shot("calib1080.png", os.path.join(capdir, c, "calib.png"))
                if psnr(down(cap), noise.astype(np.float32)) > 12: break
                log(f"  calibration capture is not the pattern (splash?), attempt {attempt + 1}")
            r = {"opts": CONDS[c], "calib_psnr_vs_nearest2x": psnr(cap, np.repeat(np.repeat(noise, 2, 0), 2, 1))}
            if c == "nearest":
                blocks = cap.reshape(1080, 2, 1920, 2, 3)
                uniform = bool((blocks == blocks[:, :1, :, :1]).all())
                exact = float((blocks[:, 0, :, 0] == noise).all(axis=2).mean())
                r.update(uniform_2x2=uniform, exact_frac=exact)
                log(f"  calibration: 2x2 blocks uniform={uniform}, pixels equal to the pattern={exact:.6f}")
                if exact < 0.999:
                    raise SystemExit("calibration failed: the capture path is not pixel-exact; fix before trusting any metric")
            log(f"  calib PSNR vs nearest-2x: {r['calib_psnr_vs_nearest2x']:.2f} dB")
            r["frames"] = {}
            for st in COND_SETS[c]:
                for sc in SCENES:
                    cands = [np.asarray(Image.open(os.path.join(W, "f1080", st, sc, f"{j:02d}.png")).convert("RGB")).astype(np.float32) for j in range(1, NF + 1)]
                    for i in range(1, NF + 1):
                        if os.path.exists(os.path.join(capdir, c, st, sc, f"{i:02d}.png")):
                            continue                     # resume: captured by an earlier run of this condition
                        _, _, scores = show_and_shot(f"f\\{st}\\{sc}\\{i:02d}.png", os.path.join(capdir, c, st, sc, f"{i:02d}.png"), i - 1, cands)
                        r["frames"][f"{st}/{sc}/{i:02d}"] = scores[i - 1] if scores else None
                log(f"  {st}: {len(SCENES) * NF} frames captured")
            res["conds"][c] = r
            json.dump(res, open(os.path.join(W, "capture.json"), "w"), indent=1)
    finally:
        log("restore: viewer off, guest to its default layout, normal client")
        try: viewer_stop()
        except Exception as e: log(f"  viewer stop: {e}")
        try: log("  " + guest_res("Display-lgonly", r"C:\Tools\display\last-lgonly.out").replace("\n", " | "))
        except Exception as e: log(f"  RESTORE FAILED (run: vm-display lg): {e}")
        try: gps("Unregister-ScheduledTask -TaskName UT-Viewer,UT-Res1080,UT-Res4K -Confirm:$false -EA SilentlyContinue")
        except Exception: pass
        try: clients_stop()
        except AssertionError as e: log(f"  {e}")
        restore_user_client()
        json.dump(res, open(os.path.join(W, "capture.json"), "w"), indent=1)

if __name__ == "__main__":
    main()
