#!/usr/bin/env python3
"""Decode the frame-number barcode of play-barcode.mp4 (at 1080p: a white guard block at x=32, then 12 bits of
64x64 blocks from x=160, all at y=32) from any recording of it in which the video fills the frame.
  barcode.py RECORDING [--fps 60]
Prints JSON: unique_fps (new video frames per second = delivered frame rate), duplicates_pct (recorded frames that
repeat the previous video frame), skipped_video_frames (video frames never seen), longest_freeze_ms,
median/worst-second unique frames. Assumes the recording itself runs at --fps."""
import json, subprocess, sys
import numpy as np

def decode(path):
    proc = subprocess.Popen(["ffmpeg", "-v", "error", "-i", path, "-vf",
                             "scale=1920:1080:flags=area,crop=1024:128:0:0,format=gray", "-f", "rawvideo", "-"],
                            stdout=subprocess.PIPE)
    idx = []
    while True:
        buf = proc.stdout.read(1024 * 128)
        if len(buf) < 1024 * 128: break
        a = np.frombuffer(buf, np.uint8).reshape(128, 1024)
        s = lambda x: a[48:80, x + 16:x + 48].mean()          # centre 32x32 of a 64x64 block
        if s(32) < 160 or a[48:80, 112:144].mean() > 96:      # guard white, gap black: else no barcode here
            idx.append(None); continue
        idx.append(sum(1 << k for k in range(12) if s(160 + 64 * k) > 128))
    proc.wait()
    return idx

def stats(idx, fps):
    v = [i for i in idx if i is not None]
    if len(v) < 10:
        return {"error": "no barcode found", "frames": len(idx)}
    dup = sum(1 for a, b in zip(v, v[1:]) if a == b)
    fwd = [(b - a) % 4096 for a, b in zip(v, v[1:]) if b != a]
    skips = sum(d - 1 for d in fwd if d < 600)                 # ignore the loop wrap-around
    runs, r = [], 1
    for a, b in zip(v, v[1:]):
        if a == b: r += 1
        else: runs.append(r); r = 1
    runs.append(r)
    n = int(fps)
    per_s = [len(set(v[i:i + n])) for i in range(0, len(v) - n + 1, n)]
    return {"recorded_frames": len(idx), "no_barcode": len(idx) - len(v),
            "unique_fps": round(len(set(v)) / (len(v) / fps), 2) if len(set(v)) < 2000 else None,
            "new_frames_per_s": round((len(v) - 1 - dup) / ((len(v) - 1) / fps), 2),
            "duplicates_pct": round(100 * dup / max(1, len(v) - 1), 2), "skipped_video_frames": skips,
            "longest_freeze_ms": round(max(runs) * 1000 / fps, 1),
            "median_second_new": float(np.median(per_s)) if per_s else None, "worst_second_new": min(per_s) if per_s else None}

if __name__ == "__main__":
    path = sys.argv[1]
    fps = float(sys.argv[sys.argv.index("--fps") + 1]) if "--fps" in sys.argv else 60.0
    print(json.dumps(stats(decode(path), fps)))
