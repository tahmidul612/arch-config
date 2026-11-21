#!/usr/bin/env python3
# Copy a host file into the win11 guest via qemu-guest-agent guest-file-write (libvirt bindings, 64 KiB; 1 MiB hangs the Windows agent, 4 MiB exceeds libvirt RPC chunks).
import os, sys, json, base64, time, libvirt, libvirt_qemu
VM = os.environ.get('VM', 'win11')  # libvirt domain name
src, dst = sys.argv[1], sys.argv[2]
dom = libvirt.open('qemu:///system').lookupByName(VM)
def qga(cmd, t=120): return json.loads(libvirt_qemu.qemuAgentCommand(dom, json.dumps(cmd), t, 0))['return']
h = qga({"execute":"guest-file-open","arguments":{"path":dst,"mode":"wb"}})
n=0; t0=time.time()
try:
  with open(src,'rb') as f:
    while d := f.read(64<<10):
        qga({"execute":"guest-file-write","arguments":{"handle":h,"buf-b64":base64.b64encode(d).decode()}}); n+=len(d)
finally:
  qga({"execute":"guest-file-close","arguments":{"handle":h}})
print(f"done {n} bytes in {time.time()-t0:.0f}s")
