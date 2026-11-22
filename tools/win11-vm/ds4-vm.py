#!/usr/bin/env python3
"""ds4-vm: keep a virtual DualShock 4 attached to the win11 VM while PS Remote Play runs in it.

PS Remote Play ignores Xbox controllers, so ds4gadget.py (dummy_hcd + raw_gadget) presents a USB
DualShock 4 (054c:05c4) fed by the Xbox controller over evdev, and this supervisor passes that
virtual DS4 to the VM as a libvirt USB hostdev.

  RemotePlay.exe running in the VM (checked through the guest agent every few seconds)
               -> run ds4gadget.py (restarted if it dies) and keep its hostdev attached
                  (attached again after a VM restart; stale entries from an old enumeration removed)
  Remote Play closed (for GRACE seconds) or VM stopped -> detach and stop ds4gadget.py
  Other games/apps never see a DS4; the Xbox controller stays with the host.
  Xbox controller unplugged/replugged -> handled inside ds4gadget.py (neutral input meanwhile), so
                  Windows and Remote Play never see the DS4 disappear.

Runs as the user service ds4-vm.service. Needs the dummy_hcd and raw_gadget modules
(/etc/modules-load.d/ds4-gadget.conf), /dev/raw-gadget owned by the user (70-raw-gadget.rules)
and python-libvirt.
"""
import base64, json, os, signal, subprocess, sys, time, xml.etree.ElementTree as ET
import libvirt, libvirt_qemu

VM, URI = os.environ.get("VM", "win11"), "qemu:///system"
VID, PID = "054c", "05c4"
HERE = os.path.dirname(os.path.abspath(__file__))
GADGET = [sys.executable, "-u", os.path.join(HERE, "ds4gadget.py")]
POLL = 3.0
GRACE = 15.0          # keep the DS4 this long after Remote Play closes (a quick restart doesn't make it flap)
RP_EXE = "RemotePlay.exe"

def log(msg):
    print(f"{time.strftime('%H:%M:%S')} ds4-vm: {msg}", flush=True)

# One libvirt connection for the whole run (spawning virsh several times per poll cost ~1% CPU).
libvirt.registerErrorHandler(lambda *a: None, None)
_conn = None
def dom():
    """The VM's domain handle, or None if libvirtd or the domain isn't there."""
    global _conn
    try:
        if _conn is None or not _conn.isAlive():
            _conn = libvirt.open(URI)
        return _conn.lookupByName(VM)
    except libvirt.libvirtError:
        _conn = None
        return None

def vm_running():
    d = dom()
    try:
        return bool(d and d.state()[0] == libvirt.VIR_DOMAIN_RUNNING)
    except libvirt.libvirtError:
        return False

def qga(cmd, timeout=5):
    d = dom()
    if d is None:
        raise RuntimeError("VM not found")
    return json.loads(libvirt_qemu.qemuAgentCommand(d, json.dumps(cmd), timeout, 0))["return"]

def rp_running():
    """True if RemotePlay.exe runs in the guest; None if the guest agent can't be asked (booting, busy)."""
    try:
        pid = qga({"execute": "guest-exec", "arguments": {
            "path": r"C:\Windows\System32\tasklist.exe",
            "arg": ["/FI", f"IMAGENAME eq {RP_EXE}", "/NH", "/FO", "CSV"], "capture-output": True}})["pid"]
        for _ in range(20):
            st = qga({"execute": "guest-exec-status", "arguments": {"pid": pid}})
            if st.get("exited"):
                out = base64.b64decode(st.get("out-data", "")).decode("utf-8", "replace")
                return RP_EXE.lower() in out.lower()
            time.sleep(0.1)
    except Exception:
        pass
    return None

def gadget_usb():
    """(bus, dev) of the virtual DS4 on the host, or None."""
    base = "/sys/bus/usb/devices"
    for n in os.listdir(base):
        p = os.path.join(base, n)
        try:
            if open(f"{p}/idVendor").read().strip() == VID and open(f"{p}/idProduct").read().strip() == PID:
                return int(open(f"{p}/busnum").read()), int(open(f"{p}/devnum").read())
        except OSError:
            continue
    return None

def hostdev_xml(bus, dev):
    return (f"<hostdev mode='subsystem' type='usb' managed='yes'><source>"
            f"<vendor id='0x{VID}'/><product id='0x{PID}'/><address bus='{bus}' device='{dev}'/>"
            f"</source></hostdev>")

def attached_entries():
    """(bus, dev) of every DS4 hostdev in the VM's live XML."""
    d = dom()
    try:
        xml = d.XMLDesc(0) if d else None
    except libvirt.libvirtError:
        xml = None
    if not xml:
        return []
    out = []
    for hd in ET.fromstring(xml).findall("./devices/hostdev[@type='usb']"):
        src = hd.find("source")
        v, p, a = src.find("vendor"), src.find("product"), src.find("address")
        if v is None or p is None or a is None:
            continue
        if v.get("id", "").lower().endswith(VID) and p.get("id", "").lower().endswith(PID):
            out.append((int(a.get("bus")), int(a.get("device"))))
    return out

def apply(action, xml):
    """attach-device / detach-device on the running VM (live only, like virsh --live)."""
    d = dom()
    try:
        if d is None:
            raise libvirt.libvirtError("VM not found")
        (d.attachDeviceFlags if action == "attach-device" else d.detachDeviceFlags)(xml, libvirt.VIR_DOMAIN_AFFECT_LIVE)
        log(f"{action}: done")
        return True
    except libvirt.libvirtError as e:
        log(f"{action}: {e}")
        return False

def sync_hostdev():
    """Make the VM hold exactly the current virtual DS4."""
    cur = gadget_usb()
    entries = attached_entries()
    for e in entries:
        if e != cur:
            apply("detach-device", hostdev_xml(*e))      # left over from an earlier enumeration
    if cur and cur not in entries:
        apply("attach-device", hostdev_xml(*cur))

class Supervisor:
    def __init__(self):
        self.proc = None
        self.backoff = 1
        self.warned_modules = False
        signal.signal(signal.SIGTERM, self.stop_and_exit)
        signal.signal(signal.SIGINT, self.stop_and_exit)

    def start_gadget(self):
        if not os.path.exists("/dev/raw-gadget"):
            if not self.warned_modules:
                log("/dev/raw-gadget missing: the dummy_hcd/raw_gadget modules are not loaded "
                    "(/etc/modules-load.d/ds4-gadget.conf, or: sudo modprobe dummy_hcd raw_gadget)")
                self.warned_modules = True
            return
        self.warned_modules = False
        log("Remote Play running: starting the virtual DS4")
        self.proc = subprocess.Popen(GADGET, stdout=sys.stdout, stderr=sys.stderr)
        self.started = time.monotonic()

    def stop_gadget(self, why):
        if self.proc and self.proc.poll() is None:
            log(f"{why}: stopping the virtual DS4")
            self.proc.terminate()
            try:
                self.proc.wait(5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None

    def stop_and_exit(self, *_):
        if self.proc and vm_running():
            for e in attached_entries():
                apply("detach-device", hostdev_xml(*e))
        self.stop_gadget("service stopping")
        sys.exit(0)

    def run(self):
        log(f"started; the virtual DS4 runs only while {RP_EXE} runs in the VM")
        rp_seen = 0.0                                    # monotonic time Remote Play was last seen
        last_rp = None
        while True:
            try:
                running = vm_running()
                rp = rp_running() if running else False
                now = time.monotonic()
                if rp:
                    rp_seen = now
                elif rp is None and self.proc:          # agent didn't answer: keep the current state
                    rp_seen = now
                want = running and (now - rp_seen) < GRACE
                if rp != last_rp and rp is not None:
                    log(f"{RP_EXE} {'running' if rp else 'not running'}" if running else "VM not running")
                    last_rp = rp
                if want:
                    if self.proc and self.proc.poll() is not None:
                        log(f"ds4gadget exited with {self.proc.returncode}; restarting in {self.backoff}s")
                        self.proc = None
                        time.sleep(self.backoff)
                        self.backoff = min(self.backoff * 2, 30)
                    if not self.proc:
                        self.start_gadget()
                    elif time.monotonic() - self.started > 60:
                        self.backoff = 1
                    if self.proc:
                        sync_hostdev()
                elif self.proc:
                    if running:
                        for e in attached_entries():
                            apply("detach-device", hostdev_xml(*e))
                    self.stop_gadget("Remote Play closed" if running else "VM not running")
            except Exception as e:                       # never die on a glitch (libvirt hiccup, sysfs race)
                log(f"error: {e!r}")
            time.sleep(POLL)

if __name__ == "__main__":
    Supervisor().run()
