#!/usr/bin/env python3
"""Offline half of the upscaling test.

For every clip in $UPSCALE_DIR/src (default ~/Videos/vm-upscale-test/src; see README.md for the downloads):
  1. simulate the PS Remote Play stream: 4K -> 1080p (bicubic, the console's scaler is unknown), 60 fps,
     low-latency hardware encode (NVENC, CBR, no B-frames, small VBV). Variants:
       hevc15  HEVC 15 Mbps   (primary: chiaki-ng's 1080p default = the official app's default; Sony's
                               "15 Mbps recommended" connection speed)
       h264_15 H.264 15 Mbps  (PS5 offers both codecs; which one the official PC app uses isn't documented)
       hevc8   HEVC 8 Mbps    (a weaker connection, to bracket)
       raw     1080p, no encode (separates scaling loss from compression loss)
  2. extract the test frames (SCENES: 8 consecutive frames each) as full-range RGB PNGs:
       ref/<scene>/NN.png            4K source (the reference)
       f1080/<variant>/<scene>/NN.png 1080p frames as the guest would show them
  3. offline upscales of every 1080p set to 4K: bilinear, bicubic, lanczos (proxies for the scaler inside the
     Remote Play client when the guest runs at 4K, which is undocumented)
       up/<variant>-<scaler>/<scene>/NN.png
  4. playback clips for perf.py (play-hevc15.mp4, play-h264_15.mp4: every clip's encode back to back) and fps.py
     (play-barcode.mp4: three clips at 1080p60, 15 Mbps H.264, with a 12-bit frame-number barcode, see barcode.py)
"""
import json, os, subprocess, sys
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.expanduser(os.environ.get("UPSCALE_DIR", "~/Videos/vm-upscale-test"))   # src/ (clips) and work/ (everything generated)
SRC, W = os.path.join(ROOT, "src"), os.path.join(ROOT, "work")
NF = 8
# name: (clip, start second at 60 fps)
SCENES = {
    "boat":     ("walk-1080.mp4", 2.0),     # open water, wake, bridge, sky, HUD text
    "road":     ("walk-11100.mp4", 6.0),    # following a motorbike down a dirt road: foliage, power lines, HUD
    "map":      ("walk-11100.mp4", 1.0),    # pause-menu map: UI text and icons (static)
    "bar":      ("walk-13800.mp4", 8.0),    # dark bar, neon, crowd
    "docks":    ("walk-8400.mp4", 6.0),     # boat by the docks, water, buildings
    "interior": ("walk-5700.mp4", 2.0),     # dim interior, doors
    "closeup":  ("walk-3120.mp4", 6.0),     # slow close-up: wood grain, cash, hands
    "trailer":  ("trailer2-0100.mp4", 4.0), # Rockstar Trailer 2: chain-link fence, face (30 fps source; shot runs 3.1-6.2 s)
}
COMMON = ["-preset", "p4", "-tune", "ll", "-rc", "cbr", "-bf", "0", "-g", "600", "-bufsize", "1M", "-delay", "0", "-zerolatency", "1"]
VARIANTS = {
    "hevc15":  ["-c:v", "hevc_nvenc", "-b:v", "15M", "-maxrate", "15M", *COMMON],
    "h264_15": ["-c:v", "h264_nvenc", "-b:v", "15M", "-maxrate", "15M", "-profile:v", "high", *COMMON],
    "hevc8":   ["-c:v", "hevc_nvenc", "-b:v", "8M", "-maxrate", "8M", *COMMON],
}
SCALERS = ["bilinear", "bicubic", "lanczos"]
TO_RGB = "scale=in_color_matrix=bt709:in_range=tv,format=rgb24"
DOWN = "fps=60,scale=1920:1080:flags=bicubic,format=yuv420p"

def ff(*args):
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True)

def frames(inp, start_frame, outdir, pre=""):
    os.makedirs(outdir, exist_ok=True)
    vf = (pre + "," if pre else "") + f"select='between(n\\,{start_frame}\\,{start_frame + NF - 1})',{TO_RGB}"
    ff("-i", inp, "-vf", vf, "-fps_mode", "passthrough", os.path.join(outdir, "%02d.png"))
    got = sorted(os.listdir(outdir))
    assert len(got) == NF, f"{outdir}: {len(got)} frames"

def encode(clip, variant):
    base = os.path.splitext(clip)[0]
    out = os.path.join(W, "enc", f"{base}-{variant}.mkv")
    if os.path.exists(out):
        return out
    os.makedirs(os.path.dirname(out), exist_ok=True)
    src = os.path.join(SRC, clip)
    if variant == "raw":
        ff("-i", src, "-vf", DOWN, "-an", "-c:v", "ffv1", out)
    else:
        ff("-i", src, "-vf", DOWN, "-an", *VARIANTS[variant],
           "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709", out)
    return out

def main():
    clips = sorted({c for c, _ in SCENES.values()})
    variants = ["raw", *VARIANTS]
    meta = {"scenes": SCENES, "variants": variants, "scalers": SCALERS, "nf": NF, "encoders": VARIANTS}
    with ThreadPoolExecutor(3) as ex:          # NVENC allows several sessions
        encs = {(c, v): ex.submit(encode, c, v) for c in clips for v in variants}
        encs = {k: f.result() for k, f in encs.items()}
    # real bitrate of each encode (sanity check on CBR)
    meta["bitrates"] = {}
    for (c, v), path in encs.items():
        p = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=bit_rate,duration", "-of", "json", path],
                           capture_output=True, text=True)
        meta["bitrates"][f"{c}:{v}"] = json.loads(p.stdout)["format"]
    jobs = []
    for s, (clip, t) in SCENES.items():
        n0 = round(t * 60)
        jobs.append((os.path.join(SRC, clip), n0, os.path.join(W, "ref", s), "fps=60"))
        for v in variants:
            jobs.append((encs[(clip, v)], n0, os.path.join(W, "f1080", v, s), ""))
    with ThreadPoolExecutor(8) as ex:
        list(ex.map(lambda j: frames(*j), jobs))
    ups = []
    for v in variants:
        for sc in SCALERS:
            for s in SCENES:
                ups.append((v, sc, s))
    def upscale(job):
        v, sc, s = job
        out = os.path.join(W, "up", f"{v}-{sc}", s); os.makedirs(out, exist_ok=True)
        ff("-i", os.path.join(W, "f1080", v, s, "%02d.png"), "-vf", f"scale=3840:2160:flags={sc}+accurate_rnd+full_chroma_int,format=rgb24",
           os.path.join(out, "%02d.png"))
    with ThreadPoolExecutor(8) as ex:
        list(ex.map(upscale, ups))
    for v in ("hevc15", "h264_15"):
        lst = os.path.join(W, "enc", f"list-{v}.txt")
        open(lst, "w").write("".join(f"file '{encs[(c, v)]}'\n" for c in clips))
        ff("-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", *(["-tag:v", "hvc1"] if v.startswith("hevc") else []),
           "-movflags", "+faststart", os.path.join(W, f"play-{v}.mp4"))
    box = ["drawbox=x=0:y=0:w=960:h=128:color=black:t=fill", "drawbox=x=32:y=32:w=64:h=64:color=white:t=fill"]
    box += [f"drawbox=x={160 + 64 * k}:y=32:w=64:h=64:color=white:t=fill:enable='mod(floor(n/{2 ** k}),2)'" for k in range(12)]
    bsrc = [c for c in ("walk-1080.mp4", "walk-11100.mp4", "walk-8400.mp4") if c in clips] or clips[:3]
    ff(*sum((["-i", os.path.join(SRC, c)] for c in bsrc), []), "-filter_complex",
       "".join(f"[{i}:v]" for i in range(len(bsrc))) + f"concat=n={len(bsrc)}:v=1,fps=60,scale=1920:1080:flags=bicubic," + ",".join(box) + ",format=yuv420p",
       "-an", *VARIANTS["h264_15"], "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
       "-movflags", "+faststart", os.path.join(W, "play-barcode.mp4"))
    json.dump(meta, open(os.path.join(W, "prep.json"), "w"), indent=1)
    print(json.dumps(meta["bitrates"], indent=1))

if __name__ == "__main__":
    main()
