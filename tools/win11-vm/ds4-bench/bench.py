#!/usr/bin/env python3
"""Input latency of a virtual DS4 gadget, measured on the host.

  bench.py NAME N CMD...     e.g. bench.py py-4ms 2000 python3 -I pywrap.py

1. Creates a fake Xbox pad with uinput (045e:0b12) and starts CMD with PAD=<its event node>.
2. Waits for the DS4 that hid-playstation creates for the gadget (054c:05c4) and switches that
   device's event timestamps to CLOCK_MONOTONIC.
3. Presses/releases BTN_SOUTH N times at random 8-25 ms gaps (random phase against the gadget's
   send loop) and records: kernel timestamp of the DS4's BTN_SOUTH event - time of the uinput write.
   The DS4 side uses the kernel's own timestamp, so this script's wake-up jitter isn't counted.
4. Prints one JSON line: latency percentiles in ms and the gadget's CPU % over the run.

Measures pad -> gadget -> dummy_hcd -> host HID driver. Not measured: the VM path (QEMU usb-host,
the guest's 5 ms polling, Remote Play, the stream to the PS5).
Needs: python-evdev, write access to /dev/uinput, /dev/raw-gadget, dummy_hcd + raw_gadget loaded,
hid_playstation, and ds4-vm.service stopped so nothing else holds /dev/raw-gadget.
"""
import os, sys, time, random, select, struct, fcntl, subprocess, statistics as S, json
import evdev
from evdev import UInput, AbsInfo, ecodes as E
name, n, cmd = sys.argv[1], int(sys.argv[2]), sys.argv[3:]
caps = {E.EV_KEY: [E.BTN_SOUTH, E.BTN_EAST, E.BTN_NORTH, E.BTN_WEST, E.BTN_TL, E.BTN_TR, E.BTN_SELECT, E.BTN_START, E.BTN_MODE, E.BTN_THUMBL, E.BTN_THUMBR],
        E.EV_ABS: [(c, AbsInfo(0, -32768, 32767, 16, 128, 0)) for c in (E.ABS_X, E.ABS_Y, E.ABS_RX, E.ABS_RY)] +
                  [(c, AbsInfo(0, 0, 1023, 0, 0, 0)) for c in (E.ABS_Z, E.ABS_RZ)] +
                  [(c, AbsInfo(0, -1, 1, 0, 0, 0)) for c in (E.ABS_HAT0X, E.ABS_HAT0Y)]}
ui = UInput(caps, name="bench-xbox", vendor=0x045E, product=0x0B12, version=1)
time.sleep(0.5)
env = dict(os.environ, PAD=ui.device.path)
p = subprocess.Popen(cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
ds4 = None
for _ in range(150):
    time.sleep(0.1)
    for path in evdev.list_devices():
        try: d = evdev.InputDevice(path)
        except OSError: continue
        if d.info.vendor == 0x054C and d.info.product == 0x05C4 and E.BTN_SOUTH in d.capabilities().get(E.EV_KEY, []):
            ds4 = d; break
        d.close()
    if ds4: break
if not ds4: p.kill(); ui.close(); sys.exit(f"{name}: DS4 never appeared")
fcntl.ioctl(ds4.fd, (1 << 30) | (4 << 16) | (ord('E') << 8) | 0xA0, struct.pack("i", time.CLOCK_MONOTONIC))
time.sleep(1.5)
while select.select([ds4.fd], [], [], 0)[0]: ds4.read()
def cpu():
    f = open(f"/proc/{p.pid}/stat").read().rsplit(")", 1)[1].split(); return (int(f[11]) + int(f[12])) / os.sysconf("SC_CLK_TCK")
c0, w0 = cpu(), time.monotonic()
lat, lost, v = [], 0, 0
for i in range(n):
    time.sleep(random.uniform(0.008, 0.025))
    v ^= 1
    t0 = time.monotonic_ns(); ui.write(E.EV_KEY, E.BTN_SOUTH, v); ui.syn()
    got = None; deadline = time.monotonic() + 0.3
    while got is None:
        r = select.select([ds4.fd], [], [], max(0, deadline - time.monotonic()))[0]
        if not r: break
        for ev in ds4.read():
            if ev.type == E.EV_KEY and ev.code == E.BTN_SOUTH and ev.value == v:
                got = (ev.sec * 10**9 + ev.usec * 1000 - t0) / 1e6
    if got is None: lost += 1
    else: lat.append(got)
c1, w1 = cpu(), time.monotonic()
p.terminate(); p.wait(); ui.close()
q = S.quantiles(lat, n=100)
res = dict(name=name, n=len(lat), lost=lost, min=min(lat), p50=q[49], mean=S.mean(lat), p95=q[94], p99=q[98], max=max(lat), sd=S.stdev(lat), cpu_pct=100 * (c1 - c0) / (w1 - w0))
print(json.dumps({k: round(x, 3) if isinstance(x, float) else x for k, x in res.items()}), flush=True)
