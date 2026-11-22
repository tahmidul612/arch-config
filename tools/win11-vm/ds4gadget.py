#!/usr/bin/env python3
"""Virtual USB DualShock 4 (054C:05C4) driven by an Xbox controller.

Runs on dummy_hcd + raw_gadget, so the "DS4" appears as a real USB device on the host
and can be passed to a VM like hardware. Every control request is logged; unknown ones
are stalled and marked UNHANDLED so we can see what the host software asks for.

Usage: ds4gadget.py [--log FILE] [--no-pad]
"""
import argparse, ctypes, errno, fcntl, os, struct, sys, threading, time

# ---------------------------------------------------------------- raw_gadget ioctls
def _IOC(d, t, nr, size): return (d << 30) | (size << 16) | (ord(t) << 8) | nr
_IO   = lambda nr: _IOC(0, 'U', nr, 0)
_IOW  = lambda nr, sz: _IOC(1, 'U', nr, sz)
_IOR  = lambda nr, sz: _IOC(2, 'U', nr, sz)
_IOWR = lambda nr, sz: _IOC(3, 'U', nr, sz)
RAW_INIT, RAW_RUN = _IOW(0, 257), _IO(1)
RAW_EVENT_FETCH = _IOR(2, 8)
RAW_EP0_WRITE, RAW_EP0_READ = _IOW(3, 8), _IOWR(4, 8)
RAW_EP_ENABLE, RAW_EP_DISABLE = _IOW(5, 9), _IOW(6, 4)
RAW_EP_WRITE, RAW_EP_READ = _IOW(7, 8), _IOWR(8, 8)
RAW_CONFIGURE, RAW_VBUS_DRAW = _IO(9), _IOW(10, 4)
RAW_EP0_STALL = _IO(12)
EV_CONNECT, EV_CONTROL, EV_SUSPEND, EV_RESUME, EV_RESET, EV_DISCONNECT = 1, 2, 3, 4, 5, 6
EV_NAMES = {1: "CONNECT", 2: "CONTROL", 3: "SUSPEND", 4: "RESUME", 5: "RESET", 6: "DISCONNECT"}
USB_SPEED_FULL = 2

# ---------------------------------------------------------------- DS4 descriptors (from ViGEmBus Ds4Pdo.cpp)
HERE = os.path.dirname(os.path.abspath(__file__))
REPORT_DESC = open(os.path.join(HERE, "ds4_report_desc.bin"), "rb").read()
assert len(REPORT_DESC) == 467
DEVICE_DESC = struct.pack("<BBHBBBBHHHBBBB", 18, 1, 0x0200, 0, 0, 0, 64, 0x054C, 0x05C4, 0x0100, 1, 2, 0, 1)
HID_DESC = struct.pack("<BBHBBBH", 9, 0x21, 0x0111, 0, 1, 0x22, len(REPORT_DESC))
EP_IN_DESC = struct.pack("<BBBBHB", 7, 5, 0x84, 3, 64, 5)
EP_OUT_DESC = struct.pack("<BBBBHB", 7, 5, 0x03, 3, 64, 5)
IFACE_DESC = struct.pack("<BBBBBBBBB", 9, 4, 0, 0, 2, 3, 0, 0, 0)
_cfg_body = IFACE_DESC + HID_DESC + EP_IN_DESC + EP_OUT_DESC
CONFIG_DESC = struct.pack("<BBHBBBBB", 9, 2, 9 + len(_cfg_body), 1, 1, 0, 0xC0, 0xFA) + _cfg_body
def _string(s): b = s.encode("utf-16-le"); return bytes([2 + len(b), 3]) + b
STRINGS = {0: bytes([4, 3, 0x09, 0x04]), 1: _string("Sony Computer Entertainment"), 2: _string("Wireless Controller")}

DEV_MAC = bytes([0x1C, 0x66, 0x6D, 0x07, 0x09, 0x8B])   # little-endian as sent on the wire
HOST_MAC = bytes([0x00, 0x00, 0x00, 0x00, 0x00, 0x00])
FEATURE_REPORTS = {
    0xA3: bytes([0xA3, 0x41, 0x75, 0x67, 0x20, 0x20, 0x33, 0x20, 0x32, 0x30, 0x31, 0x33, 0x00, 0x00, 0x00, 0x00,
                 0x00, 0x30, 0x37, 0x3A, 0x30, 0x31, 0x3A, 0x31, 0x32, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
                 0x00, 0x00, 0x01, 0x00, 0x31, 0x03, 0x00, 0x00, 0x00, 0x49, 0x00, 0x05, 0x00, 0x00, 0x80, 0x03, 0x00]),
    0x02: bytes([0x02, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x87, 0x22, 0x7B, 0xDD, 0xB2, 0x22, 0x47, 0xDD, 0xBD,
                 0x22, 0x43, 0xDD, 0x1C, 0x02, 0x1C, 0x02, 0x7F, 0x1E, 0x2E, 0xDF, 0x60, 0x1F, 0x4C, 0xE0, 0x3A,
                 0x1D, 0xC6, 0xDE, 0x08, 0x00]),
    0x12: bytes([0x12]) + DEV_MAC + bytes([0x08, 0x25, 0x00]) + HOST_MAC,
}

# ---------------------------------------------------------------- logging
LOG = sys.stdout
VERBOSE = False
def log(msg):
    LOG.write(f"{time.strftime('%H:%M:%S')}.{int(time.time()*1000)%1000:03d} {msg}\n"); LOG.flush()

# ---------------------------------------------------------------- controller state
class State:
    def __init__(self):
        self.lock = threading.Lock()
        self.lx = self.ly = self.rx = self.ry = 128
        self.l2 = self.r2 = 0
        self.hx = self.hy = 0
        self.btn = set()
STATE = State()

def build_input_report(counter, ts):
    s = STATE
    with s.lock:
        hat = {(0, -1): 0, (1, -1): 1, (1, 0): 2, (1, 1): 3, (0, 1): 4, (-1, 1): 5, (-1, 0): 6, (-1, -1): 7}.get((s.hx, s.hy), 8)
        b = s.btn
        b5 = hat | (0x10 if "square" in b else 0) | (0x20 if "cross" in b else 0) | (0x40 if "circle" in b else 0) | (0x80 if "triangle" in b else 0)
        b6 = ((0x01 if "l1" in b else 0) | (0x02 if "r1" in b else 0) | (0x04 if s.l2 > 0 else 0) | (0x08 if s.r2 > 0 else 0) |
              (0x10 if "share" in b else 0) | (0x20 if "options" in b else 0) | (0x40 if "l3" in b else 0) | (0x80 if "r3" in b else 0))
        b7 = (0x01 if "ps" in b else 0) | (0x02 if "touch" in b else 0) | ((counter & 0x3F) << 2)
        r = bytearray(64)
        r[0:10] = bytes([0x01, s.lx, s.ly, s.rx, s.ry, b5, b6, b7, s.l2, s.r2])
    struct.pack_into("<H", r, 10, ts & 0xFFFF)
    r[12] = 0x1B                                              # temperature-ish
    struct.pack_into("<hhhhhh", r, 13, 0, 0, 0, 0, 8192, 0)    # gyro xyz, accel xyz (1g on Y)
    r[30] = 0x1B                                              # cable connected, battery full
    r[33] = 0x01                                              # one touch packet
    r[34] = counter & 0xFF
    r[35] = 0x80; r[39] = 0x80                                # both touch points "not touching"
    return bytes(r)

def pad_thread(no_pad):
    if no_pad:
        log("pad: disabled (--no-pad), sending neutral input"); return
    import evdev
    from evdev import ecodes as E
    BTN = {E.BTN_SOUTH: "cross", E.BTN_EAST: "circle", E.BTN_X: "square", E.BTN_Y: "triangle",
           E.BTN_TL: "l1", E.BTN_TR: "r1", E.BTN_SELECT: "share", E.BTN_START: "options",
           E.BTN_THUMBL: "l3", E.BTN_THUMBR: "r3", E.BTN_MODE: "ps", E.KEY_RECORD: "touch"}
    def neutral():
        with STATE.lock:
            STATE.lx = STATE.ly = STATE.rx = STATE.ry = 128; STATE.l2 = STATE.r2 = 0
            STATE.hx = STATE.hy = 0; STATE.btn.clear()
    waiting_logged = False
    while True:
        dev = None
        for p in evdev.list_devices():
            try: d = evdev.InputDevice(p)
            except OSError: continue
            if d.info.vendor == 0x045E and E.EV_ABS in d.capabilities():
                dev = d; break
            d.close()
        if not dev:
            if not waiting_logged: log("pad: no Xbox controller plugged in; sending neutral input until one appears")
            waiting_logged = True; time.sleep(1); continue
        waiting_logged = False
        caps = {c: dev.absinfo(c) for c in (E.ABS_X, E.ABS_Y, E.ABS_RX, E.ABS_RY, E.ABS_Z, E.ABS_RZ) if c in dict(dev.capabilities()[E.EV_ABS])}
        log(f"pad: using {dev.path} '{dev.name}' axes={ {E.ABS[c] if isinstance(E.ABS[c], str) else E.ABS[c][0]: (a.min, a.max) for c, a in caps.items()} }")
        def norm(code, v, out_max=255):
            a = caps[code]; return max(0, min(out_max, round((v - a.min) * out_max / (a.max - a.min))))
        try:
            for ev in dev.read_loop():
                with STATE.lock:
                    if ev.type == E.EV_KEY and ev.code in BTN:
                        (STATE.btn.add if ev.value else STATE.btn.discard)(BTN[ev.code])
                    elif ev.type == E.EV_ABS:
                        c, v = ev.code, ev.value
                        if c == E.ABS_X: STATE.lx = norm(c, v)
                        elif c == E.ABS_Y: STATE.ly = norm(c, v)
                        elif c == E.ABS_RX: STATE.rx = norm(c, v)
                        elif c == E.ABS_RY: STATE.ry = norm(c, v)
                        elif c == E.ABS_Z: STATE.l2 = norm(c, v)
                        elif c == E.ABS_RZ: STATE.r2 = norm(c, v)
                        elif c == E.ABS_HAT0X: STATE.hx = v
                        elif c == E.ABS_HAT0Y: STATE.hy = v
                if VERBOSE and ev.type == E.EV_KEY and ev.value == 1:
                    log(f"pad: key {E.KEY.get(ev.code) or E.BTN.get(ev.code)} -> {BTN.get(ev.code, '(unmapped)')}")
        except OSError as e:
            neutral()                         # release everything so nothing stays "held" while unplugged
            try: dev.close()                  # otherwise every unplug leaks the old device's fd
            except OSError: pass
            log(f"pad: controller unplugged ({e}); neutral input, waiting for it to come back"); time.sleep(1)

# ---------------------------------------------------------------- gadget
class Gadget:
    def __init__(self):
        self.fd = os.open("/dev/raw-gadget", os.O_RDWR)
        init = bytearray(257)
        init[0:9] = b"dummy_udc"; init[128:139] = b"dummy_udc.0"; init[256] = USB_SPEED_FULL
        fcntl.ioctl(self.fd, RAW_INIT, init)
        fcntl.ioctl(self.fd, RAW_RUN, 0)
        self.ep_in = self.ep_out = None
        self.configured = threading.Event()
        self.counter = 0

    def fetch_event(self):
        buf = bytearray(8 + 8); struct.pack_into("<II", buf, 0, 0, 8)
        fcntl.ioctl(self.fd, RAW_EVENT_FETCH, buf, True)
        t, n = struct.unpack_from("<II", buf, 0)
        return t, bytes(buf[8:8 + n])

    def ep0_write(self, data):
        buf = bytearray(struct.pack("<HHI", 0, 0, len(data)) + data)
        return fcntl.ioctl(self.fd, RAW_EP0_WRITE, buf, True)

    def ep0_read(self, n):
        buf = bytearray(struct.pack("<HHI", 0, 0, n) + bytes(n))
        r = fcntl.ioctl(self.fd, RAW_EP0_READ, buf, True)
        return bytes(buf[8:8 + max(r, 0)])

    def stall(self):
        try: fcntl.ioctl(self.fd, RAW_EP0_STALL, 0)
        except OSError as e: log(f"  stall failed: {e}")

    def enable_eps(self):
        if self.ep_in is not None: return
        self.ep_in = fcntl.ioctl(self.fd, RAW_EP_ENABLE, bytearray(EP_IN_DESC + b"\0\0"), True)
        self.ep_out = fcntl.ioctl(self.fd, RAW_EP_ENABLE, bytearray(EP_OUT_DESC + b"\0\0"), True)
        log(f"  endpoints enabled: in={self.ep_in} out={self.ep_out}")

    def disable_eps(self):
        self.configured.clear()
        for name in ("ep_in", "ep_out"):
            h = getattr(self, name)
            if h is not None:
                try: fcntl.ioctl(self.fd, RAW_EP_DISABLE, struct.pack("<I", h))
                except OSError: pass
                setattr(self, name, None)

    # ---- control requests
    def handle_control(self, setup):
        rt, req, val, idx, ln = struct.unpack("<BBHHH", setup)
        desc = f"bmRequestType={rt:02x} bRequest={req:02x} wValue={val:04x} wIndex={idx:04x} wLength={ln}"
        dir_in = rt & 0x80
        typ = (rt >> 5) & 3       # 0 std, 1 class, 2 vendor
        def send(data, what):
            log(f"CTRL {desc}  -> {what} ({min(len(data), ln)} bytes)")
            self.ep0_write(data[:ln])
        def ack(what):
            log(f"CTRL {desc}  -> {what}")
            self.ep0_read(0)
        if typ == 0:
            if req == 0x06:   # GET_DESCRIPTOR
                dt, di = val >> 8, val & 0xFF
                if dt == 1: return send(DEVICE_DESC, "device descriptor")
                if dt == 2: return send(CONFIG_DESC, "config descriptor")
                if dt == 3 and di in STRINGS: return send(STRINGS[di], f"string {di}")
                if dt == 0x22: return send(REPORT_DESC, "HID report descriptor")
                if dt == 0x21: return send(HID_DESC, "HID descriptor")
            elif req == 0x09:  # SET_CONFIGURATION
                if val:
                    self.enable_eps()
                    try: fcntl.ioctl(self.fd, RAW_VBUS_DRAW, struct.pack("<I", 250))
                    except OSError: pass
                    try: fcntl.ioctl(self.fd, RAW_CONFIGURE, 0)
                    except OSError as e: log(f"  configure: {e}")
                    self.configured.set()
                else:
                    self.disable_eps()
                return ack(f"SET_CONFIGURATION {val}")
            elif req == 0x0B: return ack("SET_INTERFACE")
            elif req == 0x00: return send(b"\x00\x00", "GET_STATUS")
            elif req == 0x08: return send(b"\x01", "GET_CONFIGURATION")
            elif req == 0x01: return ack("CLEAR_FEATURE")
            elif req == 0x03: return ack("SET_FEATURE")
        elif typ == 1:
            rtype, rid = val >> 8, val & 0xFF   # 1 input, 2 output, 3 feature
            if req == 0x01:   # GET_REPORT
                if rtype == 3 and rid in FEATURE_REPORTS:
                    return send(FEATURE_REPORTS[rid], f"GET_REPORT feature 0x{rid:02x}")
                if rtype == 1 and rid in (0, 1):
                    return send(build_input_report(self.counter, 0), "GET_REPORT input")
            elif req == 0x09:  # SET_REPORT
                data = self.ep0_read(ln)
                log(f"CTRL {desc}  -> SET_REPORT type={rtype} id=0x{rid:02x} data={data.hex()}")
                return
            elif req == 0x0A: return ack("SET_IDLE")
            elif req == 0x0B: return ack("SET_PROTOCOL")
            elif req == 0x02: return send(b"\x00", "GET_IDLE")
            elif req == 0x03: return send(b"\x01", "GET_PROTOCOL")
        log(f"CTRL {desc}  -> UNHANDLED (stall)" + ("" if dir_in else "  [OUT]"))
        if not dir_in and ln:
            try: self.ep0_read(ln)
            except OSError: pass
        self.stall()

    # ---- interrupt endpoints
    def in_loop(self):
        t0 = time.monotonic(); sent = 0
        while True:
            self.configured.wait()
            ep = self.ep_in
            if ep is None: time.sleep(0.01); continue
            self.counter = (self.counter + 1) & 0xFF
            ts = int((time.monotonic() - t0) * 1e6 / 5.33)
            data = build_input_report(self.counter, ts)
            buf = bytearray(struct.pack("<HHI", ep, 0, len(data)) + data)
            try:
                fcntl.ioctl(self.fd, RAW_EP_WRITE, buf, True); sent += 1
                if sent == 1 or sent % 15000 == 0: log(f"IN: {sent} input reports delivered")
            except OSError as e:
                if e.errno not in (errno.ESHUTDOWN, errno.EINVAL, errno.EBUSY, errno.ECONNRESET): log(f"IN: {e}")
                time.sleep(0.02)
            time.sleep(0.004)

    def out_loop(self):
        while True:
            self.configured.wait()
            ep = self.ep_out
            if ep is None: time.sleep(0.01); continue
            buf = bytearray(struct.pack("<HHI", ep, 0, 64) + bytes(64))
            try:
                n = fcntl.ioctl(self.fd, RAW_EP_READ, buf, True)
                d = bytes(buf[8:8 + n])
                if d == getattr(self, "_last_out", None): continue   # Remote Play repeats the same output report
                self._last_out = d
                if d[:1] == b"\x05": log(f"OUT report 0x05: flags={d[1]:02x} rumble weak={d[4]} strong={d[5]} rgb={d[6]:02x}{d[7]:02x}{d[8]:02x}")
                else: log(f"OUT data: {d.hex()}")
            except OSError as e:
                if e.errno not in (errno.ESHUTDOWN, errno.EINVAL, errno.EBUSY, errno.ECONNRESET): log(f"OUT: {e}")
                time.sleep(0.02)

    def run(self):
        threading.Thread(target=self.in_loop, daemon=True).start()
        threading.Thread(target=self.out_loop, daemon=True).start()
        while True:
            t, data = self.fetch_event()
            if t == EV_CONTROL:
                try: self.handle_control(data)
                except OSError as e: log(f"  control error: {e}")
            else:
                log(f"EVENT {EV_NAMES.get(t, t)}")
                if t in (EV_RESET, EV_DISCONNECT): self.disable_eps()

def main():
    global LOG, VERBOSE
    ap = argparse.ArgumentParser()
    ap.add_argument("--log"); ap.add_argument("--no-pad", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true", help="log every button press")
    a = ap.parse_args()
    VERBOSE = a.verbose
    if a.log: LOG = open(a.log, "a", buffering=1)
    log("ds4gadget starting")
    threading.Thread(target=pad_thread, args=(a.no_pad,), daemon=True).start()
    Gadget().run()

if __name__ == "__main__":
    main()
