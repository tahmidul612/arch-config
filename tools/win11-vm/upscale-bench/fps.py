#!/usr/bin/env python3
"""Frame-rate test: the barcoded 1080p60 stream simulation (play-barcode.mp4) loops in the guest; for each setup
two recordings run at the same time for REC seconds and are decoded by barcode.py:
  shm  host OBS reading the Looking Glass shared memory (what the guest's display driver delivers: player + IDD)
  dp1  the client's own FPS/UPS overlay (win:showFPS), read by OCR from DP-1 screenshots every ~1.5 s
       (UPS = frames the client received per second, FPS = frames it rendered); crops kept for checking
Setups: guest 1080p + client linear, guest 1080p + client FSR 1.0, guest 4K + client (1:1).
Output: $UPSCALE_DIR/work/fps/<setup>-{shm,dp1}.*, $UPSCALE_DIR/work/fps.json
"""
import glob, json, os, re, shutil, signal, subprocess, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import capture as C
import barcode

REC = 15
OBSWS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "looking-glass", "obsws.py")
OUT = os.path.join(C.W, "fps")
SETUPS = [("1080p", "linear"), ("1080p", "fsr1.0"), ("4k", "linear")]

def obs(reqs):
    r = subprocess.run([sys.executable, OBSWS, json.dumps(reqs)], capture_output=True, text=True, timeout=30)
    return r.returncode, r.stdout + r.stderr

def obs_up():
    if obs([{"requestType": "GetVersion"}])[0] == 0:
        return False
    C.sh("systemd-run", "--user", "--unit=obs-vm-capture", "--collect", "obs", "--profile", "VM-Capture", "--collection",
         "VM-Capture", "--minimize-to-tray", "--disable-shutdown-check", "--disable-updater")
    for _ in range(40):
        time.sleep(1)
        if obs([{"requestType": "GetVersion"}])[0] == 0: return True
    raise SystemExit("OBS did not come up")

def ocr_overlay(img, path):
    """FPS/UPS from the client's overlay at the top-left of DP-1."""
    from PIL import Image, ImageOps
    crop = Image.fromarray(img[0:90, 0:900]).convert("L")
    crop = ImageOps.invert(crop).resize((crop.width * 3, crop.height * 3), Image.LANCZOS)
    crop.save(path)
    t = subprocess.run(["tesseract", path, "-", "--psm", "7"], capture_output=True, text=True).stdout
    m = re.search(r"FPS[:;]\s*([\d.]+)\s*UPS[:;]\s*([\d.]+)", t.replace(",", "."))
    return (float(m.group(1)), float(m.group(2)), t.strip()) if m else (None, None, t.strip())

def record(tag):
    """OBS (shm) for REC seconds while sampling the client's FPS/UPS overlay."""
    os.makedirs(OUT, exist_ok=True)
    obs([{"requestType": "SetInputSettings", "requestData": {"inputName": "VM display", "inputSettings":
          {"shmFile": "/dev/shm/looking-glass", "dmabuf": False, "hideMouse": True}}}]); time.sleep(2)
    obs([{"requestType": "StartRecord"}])
    t0, samples, i = time.time(), [], 0
    while time.time() - t0 < REC:
        img = C.shot(None); i += 1
        samples.append(ocr_overlay(img, os.path.join(OUT, f"{tag}-overlay-{i:02d}.png")))
        time.sleep(0.5)
    _, o = obs([{"requestType": "StopRecord"}])
    m = re.search(r'"outputPath": "([^"]+)"', o)
    shm = os.path.join(OUT, f"{tag}-shm.mkv")
    if m:
        time.sleep(1); shutil.move(m.group(1), shm)
    return (shm if m else None), samples

def main():
    clip = os.path.join(C.W, "play-barcode.mp4")
    C.upload({"player.ps1": os.path.join(C.SUITE, "player.ps1"), "play-barcode.mp4": clip, "viewer.ps1": os.path.join(C.SUITE, "viewer.ps1")})
    C.register_tasks()
    u = C.user_of_tasks()
    C.gps(f"$p = New-ScheduledTaskPrincipal -UserId '{u}' -LogonType Interactive -RunLevel Limited; "
          "$s = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 1); "
          f"$a = New-ScheduledTaskAction -Execute powershell.exe -Argument '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File {C.GDIR}\\player.ps1 {C.GDIR}\\play-barcode.mp4'; "
          "Register-ScheduledTask -TaskName UT-Player -Action $a -Principal $p -Settings $s -Force | Out-Null")
    started_obs = obs_up()
    res = {"rec_s": REC, "runs": {}}
    cur = None
    def player(on):
        if on:
            C.gps(f"Remove-Item '{C.GDIR}\\player.stop','{C.GDIR}\\player.log' -EA SilentlyContinue; Start-ScheduledTask UT-Player"); time.sleep(6)
        else:
            C.gps(f"New-Item '{C.GDIR}\\player.stop' -Force | Out-Null"); time.sleep(2); C.gps("Stop-ScheduledTask UT-Player -EA SilentlyContinue")
    try:
        for gres, cond in SETUPS:
            if gres != cur:
                C.log("  " + (C.guest_res("UT-Res1080", f"{C.GDIR}\\res1080.out") if gres == "1080p" else
                              C.guest_res("UT-Res4K", f"{C.GDIR}\\res4k.out")).replace("\n", " | "))
                cur = gres; C.GUEST_1080[0] = gres == "1080p"
            tag = f"{gres}-{cond}"
            C.log(f"setup: guest {gres}, client {cond}")
            C.client(C.CONDS[cond] + ["win:showFPS=yes"])
            player(True)
            _, plog = C.gps(f"Get-Content '{C.GDIR}\\player.log' -EA SilentlyContinue")
            shm, samples = record(tag)
            player(False)
            ok = [x for x in samples if x[0] is not None]
            r = {"player": plog.strip(), "client_overlay": {
                "samples": [[a, b] for a, b, _ in samples], "ocr_fail": len(samples) - len(ok),
                "ups_mean": round(sum(x[1] for x in ok) / len(ok), 2) if ok else None,
                "ups_min": min(x[1] for x in ok) if ok else None,
                "fps_mean": round(sum(x[0] for x in ok) / len(ok), 2) if ok else None}}
            for name, f in (("shm", shm),):
                r[name] = barcode.stats(barcode.decode(f), 60.0) if f else {"error": "no recording"}
                if f:
                    p = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height,avg_frame_rate,r_frame_rate",
                                        "-of", "csv=p=0", f], capture_output=True, text=True)
                    r[name]["file"] = os.path.basename(f) + " " + p.stdout.strip()
            C.log("  " + json.dumps(r))
            res["runs"][tag] = r
            json.dump(res, open(os.path.join(C.W, "fps.json"), "w"), indent=1)
    finally:
        C.log("restore")
        try: player(False)
        except Exception: pass
        if started_obs:
            C.sh("systemctl", "--user", "stop", "obs-vm-capture.service")
        if cur is not None:
            try: C.log("  " + C.guest_res("Display-lgonly", r"C:\Tools\display\last-lgonly.out").replace("\n", " | "))
            except Exception as e: C.log(f"  RESTORE FAILED (run: vm-display lg): {e}")
        C.gps("Unregister-ScheduledTask -TaskName UT-Viewer,UT-Res1080,UT-Res4K,UT-Player -Confirm:$false -EA SilentlyContinue")
        try: C.clients_stop()
        except AssertionError as e: C.log(f"  {e}")
        C.restore_user_client()
        json.dump(res, open(os.path.join(C.W, "fps.json"), "w"), indent=1)

if __name__ == "__main__":
    main()
