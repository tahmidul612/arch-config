#!/usr/bin/env python3
# Run a PowerShell script inside the win11 guest via qemu-guest-agent.
import os, sys, json, subprocess, base64, time
VM = os.environ.get('VM', 'win11')  # libvirt domain name
script = sys.stdin.read()
timeout = int(sys.argv[1]) if len(sys.argv) > 1 else 300
enc = base64.b64encode(script.encode('utf-16-le')).decode()
def qga(cmd):
    out = subprocess.run(['virsh','-c','qemu:///system','qemu-agent-command',VM,json.dumps(cmd),'--timeout','30'],capture_output=True,text=True)
    if out.returncode: sys.exit(out.stderr)
    return json.loads(out.stdout)['return']
pid = qga({"execute":"guest-exec","arguments":{"path":"powershell.exe","arg":["-NoProfile","-NonInteractive","-ExecutionPolicy","Bypass","-EncodedCommand",enc],"capture-output":True}})['pid']
t0=time.time()
while True:
    st = qga({"execute":"guest-exec-status","arguments":{"pid":pid}})
    if st.get('exited'): break
    if time.time()-t0>timeout: sys.exit(f"timeout, pid {pid} still running")
    time.sleep(2)
for k in ('out-data','err-data'):
    if k in st: sys.stdout.write(base64.b64decode(st[k]).decode('utf-8','replace'))
print(f"\n[exit {st.get('exitcode')}]")
