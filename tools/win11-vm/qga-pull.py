#!/usr/bin/env python3
# Copy a file out of the win11 guest via qemu-guest-agent guest-file-read.
import os, sys, json, subprocess, base64, time
VM = os.environ.get('VM', 'win11')  # libvirt domain name
src, dst = sys.argv[1], sys.argv[2]
def qga(cmd, t=120):
    out = subprocess.run(['virsh','-c','qemu:///system','qemu-agent-command',VM,json.dumps(cmd),'--timeout',str(t)],capture_output=True,text=True)
    if out.returncode: sys.exit(out.stderr)
    return json.loads(out.stdout)['return']
h = qga({"execute":"guest-file-open","arguments":{"path":src,"mode":"rb"}})
n=0; t0=time.time()
with open(dst+'.part','wb') as f:
    while True:
        r = qga({"execute":"guest-file-read","arguments":{"handle":h,"count":1024*1024}})
        d = base64.b64decode(r['buf-b64']); f.write(d); n+=len(d)
        if r.get('eof') or not d: break
        if (n>>20)%128==0: print(f"{n>>20} MiB {n/(time.time()-t0)/1e6:.1f} MB/s", flush=True)
qga({"execute":"guest-file-close","arguments":{"handle":h}})
os.rename(dst+'.part', dst)
print(f"done {n} bytes in {time.time()-t0:.0f}s")
