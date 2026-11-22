#!/usr/bin/env python3
"""Playback benchmark: the simulated Remote Play stream (1080p, 15 Mbps) looping in the guest, per display setup:
  guest 1080p + Looking Glass linear / FSR 1.0 / FSR 1.0 + CAS 0.5   (client upscales 1080p -> 4K on the RTX)
  guest 4K    + Looking Glass linear (1:1)                           (Windows upscales the video, the client copies 4K)
For each, SAMPLE seconds of: host GPU utilisation and power (nvidia-smi, 100 ms), client CPU %, guest total CPU %, guest iGPU 3D and video-decode engine utilisation, and whether the
picture is moving (two DP-1 screenshots 0.5 s apart must differ). Output: $UPSCALE_DIR/work/perf.json
"""
import json, os, subprocess, sys, threading, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import capture as C

SAMPLE = 15
SETUPS = [("1080p", "linear"), ("1080p", "fsr1.0"), ("1080p", "fsr1.0+cas0.5"), ("4k", "linear")]
GUEST_MEASURE = r"""
$cores = (Get-CimInstance Win32_ComputerSystem).NumberOfLogicalProcessors
$s = Get-Counter '\Processor(_Total)\% Processor Time','\GPU Engine(*engtype_3D)\Utilization Percentage','\GPU Engine(*engtype_VideoDecode)\Utilization Percentage' -SampleInterval 1 -MaxSamples SECS -EA SilentlyContinue
$cpu = @(); $g3d = @(); $dec = @()
foreach ($x in $s) { $c = $x.CounterSamples
  $cpu += ($c | ? Path -like '*processor(_total)*' | Measure CookedValue -Sum).Sum
  $g3d += ($c | ? Path -like '*engtype_3d*' | Measure CookedValue -Sum).Sum
  $dec += ($c | ? Path -like '*engtype_videodecode*' | Measure CookedValue -Sum).Sum }
@{ guest_cpu_pct = ($cpu | Measure -Average).Average;
   igpu_3d_pct = ($g3d | Measure -Average).Average; igpu_decode_pct = ($dec | Measure -Average).Average; cores = $cores } | ConvertTo-Json -Compress
""".replace("SECS", str(SAMPLE))

def host_sample(out, secs):
    p = subprocess.Popen(["nvidia-smi", "--query-gpu=utilization.gpu,power.draw", "--format=csv,noheader,nounits", "-lms", "100"],
                         stdout=subprocess.PIPE, text=True)
    pid = C.sh("systemctl", "--user", "show", "ut-lgc", "-p", "MainPID", "--value").stdout.strip()
    def cpu():
        f = open(f"/proc/{pid}/stat").read().rsplit(")", 1)[1].split(); return (int(f[11]) + int(f[12])) / os.sysconf("SC_CLK_TCK")
    c0, w0 = cpu(), time.time(); time.sleep(secs); c1, w1 = cpu(), time.time()
    p.terminate(); rows = [l.split(",") for l in p.communicate()[0].strip().splitlines() if "," in l]
    u = [float(r[0]) for r in rows]; pw = [float(r[1]) for r in rows]
    out.update(gpu_util_pct=float(np.mean(u)), gpu_power_w=float(np.mean(pw)), client_cpu_pct_of_one_core=100 * (c1 - c0) / (w1 - w0))

def moving():
    a = C.shot(None); time.sleep(0.5); b = C.shot(None)
    return float(np.mean(np.abs(a.astype(np.int16) - b.astype(np.int16)))) > 1.0

def main():
    _, out = C.gps("(Get-AppxPackage *HEVCVideoExtension* | Select -First 1).Name")
    hevc = bool(out.strip())
    clip = os.path.join(C.W, "play-hevc15.mp4" if hevc else "play-h264_15.mp4")
    C.log(f"guest HEVC decoder: {hevc}; playing {os.path.basename(clip)}")
    C.upload({"player.ps1": os.path.join(C.SUITE, "player.ps1"), "play.mp4": clip, "viewer.ps1": os.path.join(C.SUITE, "viewer.ps1")})
    C.register_tasks()
    u = C.user_of_tasks()
    C.gps(f"$p = New-ScheduledTaskPrincipal -UserId '{u}' -LogonType Interactive -RunLevel Limited; "
          "$s = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 1); "
          f"$a = New-ScheduledTaskAction -Execute powershell.exe -Argument '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File {C.GDIR}\\player.ps1'; "
          "Register-ScheduledTask -TaskName UT-Player -Action $a -Principal $p -Settings $s -Force | Out-Null")
    res = {"clip": os.path.basename(clip), "sample_s": SAMPLE, "runs": {}}
    cur_res = None
    def player(on):
        if on:
            C.gps(f"Remove-Item '{C.GDIR}\\player.stop','{C.GDIR}\\player.log' -EA SilentlyContinue; Start-ScheduledTask UT-Player"); time.sleep(6)
        else:
            C.gps(f"New-Item '{C.GDIR}\\player.stop' -Force | Out-Null"); time.sleep(2); C.gps("Stop-ScheduledTask UT-Player -EA SilentlyContinue")
    try:
        for gres, cond in SETUPS:
            if gres != cur_res:
                player(False)
                C.log("  " + (C.guest_res("UT-Res1080", f"{C.GDIR}\\res1080.out") if gres == "1080p" else
                              C.guest_res("UT-Res4K", f"{C.GDIR}\\res4k.out")).replace("\n", " | "))
                cur_res = gres
            C.log(f"setup: guest {gres}, client {cond}")
            C.client(C.CONDS[cond])
            player(True)
            _, plog = C.gps(f"Get-Content '{C.GDIR}\\player.log' -EA SilentlyContinue")
            r = {"player": plog.strip(), "moving": moving()}
            snap = C.shot(None)
            from PIL import Image
            Image.fromarray(snap).resize((960, 540)).save(os.path.join(C.W, f"perf-{gres}-{cond}.png"))
            if not r["moving"]:
                raise SystemExit(f"picture is not moving with guest {gres} / {cond}: see work/perf-{gres}-{cond}.png")
            th = threading.Thread(target=host_sample, args=(r, SAMPLE)); th.start()
            _, g = C.gps(GUEST_MEASURE, SAMPLE + 60); th.join()
            r.update(json.loads(g.strip().splitlines()[-1]))
            C.log("  " + json.dumps({k: (round(v, 1) if isinstance(v, float) else v) for k, v in r.items()}))
            res["runs"][f"{gres}/{cond}"] = r
            player(False)
            json.dump(res, open(os.path.join(C.W, "perf.json"), "w"), indent=1)
    finally:
        C.log("restore")
        try: player(False)
        except Exception: pass
        if cur_res is not None:
            try: C.log("  " + C.guest_res("Display-lgonly", r"C:\Tools\display\last-lgonly.out").replace("\n", " | "))
            except Exception as e: C.log(f"  RESTORE FAILED (run: vm-display lg): {e}")
        C.gps("Unregister-ScheduledTask -TaskName UT-Viewer,UT-Res1080,UT-Res4K,UT-Player -Confirm:$false -EA SilentlyContinue")
        try: C.clients_stop()
        except AssertionError as e: C.log(f"  {e}")
        C.restore_user_client()
        json.dump(res, open(os.path.join(C.W, "perf.json"), "w"), indent=1)

if __name__ == "__main__":
    main()
