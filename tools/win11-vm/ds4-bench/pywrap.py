#!/usr/bin/env python3
# Run the repo's ds4gadget.py unchanged, except that it reads the pad at $PAD (bench.py's fake pad,
# so a real Xbox pad that is plugged in is left alone) and, if $SLEEP is set, the IN loop sleeps
# $SLEEP seconds instead of 0.004. Started by bench.py.
import os, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import evdev
PAD = os.environ["PAD"]; SLEEP = float(os.environ.get("SLEEP", "0.004"))
evdev.list_devices = lambda *a: [PAD]
import ds4gadget as g
class T:
    def __getattr__(self, n): return getattr(time, n)
    def sleep(self, s): time.sleep(SLEEP if s == 0.004 else s)
g.time = T()
sys.argv = ["ds4gadget", "--log", os.environ.get("GLOG", "/dev/null")]
g.main()
