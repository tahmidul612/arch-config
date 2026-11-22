#!/usr/bin/env python3
"""Score every condition against the 4K source frames and write the report tables and crops.

Per scene (8 consecutive frames, scored as a 60 fps sequence so VMAF's motion term is meaningful), via libvmaf on
BT.709 limited-range YUV 4:4:4 converted identically from every RGB image:
  vmaf4k  VMAF 4K model (vmaf_4k_v0.6.1)
  neg     VMAF NEG (no-enhancement-gain: sharpening can't buy score)
  psnr_y  PSNR of luma
  ssim    SSIM (float_ssim)
Conditions:
  live:<cond>/<set>     what the Looking Glass client drew on DP-1 with the guest at 1080p (capture.py)
  off:<set>-<scaler>    offline 1080p->4K (prep.py); with <set>=hevc15 these stand in for "guest at 4K, the Remote
                        Play client scales" (the 4K guest->DP-1 path was measured bit-exact, so only the scaler differs)
Also: sanity pairs (live nearest == offline nearest-2x, live linear vs offline bilinear).
Output: $UPSCALE_DIR/work/results.json, report.md, crops/*.png
"""
import glob, json, os, subprocess, tempfile
from concurrent.futures import ThreadPoolExecutor
import numpy as np
from PIL import Image

ROOT = os.path.expanduser(os.environ.get("UPSCALE_DIR", "~/Videos/vm-upscale-test"))   # src/ (clips) and work/ (everything generated)
W = os.path.join(ROOT, "work")
meta = json.load(open(os.path.join(W, "prep.json")))
SCENES = list(meta["scenes"])
TMP = tempfile.mkdtemp(prefix="vmaf-", dir=os.path.join(W))
YUV = "scale=out_color_matrix=bt709:out_range=tv,format=yuv444p"

def conditions():
    c = {}
    for d in sorted(glob.glob(os.path.join(W, "cap", "*", "*"))):
        if os.path.isdir(d) and os.path.basename(os.path.dirname(d)) != "path4k":
            c[f"live:{os.path.basename(os.path.dirname(d))}/{os.path.basename(d)}"] = d
    for d in sorted(glob.glob(os.path.join(W, "up", "*"))):
        c[f"off:{os.path.basename(d)}"] = d
    return {k: v for k, v in c.items() if all(len(glob.glob(os.path.join(v, s, "*.png"))) == meta["nf"] for s in SCENES)}

def score(name, d, scene):
    log = os.path.join(TMP, f"{name.replace(':', '_').replace('/', '_')}-{scene}.json")
    subprocess.run(["ffmpeg", "-v", "error", "-framerate", "60", "-i", os.path.join(d, scene, "%02d.png"),
                    "-framerate", "60", "-i", os.path.join(W, "ref", scene, "%02d.png"), "-lavfi",
                    f"[0:v]{YUV}[d];[1:v]{YUV}[r];[d][r]libvmaf=model='version=vmaf_4k_v0.6.1\\:name=vmaf4k|version=vmaf_v0.6.1neg\\:name=neg'"
                    f":feature='name=psnr|name=float_ssim':log_fmt=json:log_path={log}:n_threads=4", "-f", "null", "-"], check=True)
    p = json.load(open(log))["pooled_metrics"]
    return {"vmaf4k": p["vmaf4k"]["mean"], "neg": p["neg"]["mean"], "psnr_y": p["psnr_y"]["mean"], "ssim": p["float_ssim"]["mean"]}

def img(p): return np.asarray(Image.open(p).convert("RGB")).astype(np.float32)
def psnr(a, b):
    m = np.mean((a - b) ** 2); return 99.0 if m == 0 else float(10 * np.log10(255 ** 2 / m))

def sanity():
    s = {}
    a = glob.glob(os.path.join(W, "cap", "nearest", "hevc15", "*", "*.png"))
    if a:
        vals = []
        for p in a:
            sc, n = p.split(os.sep)[-2:]
            src = img(os.path.join(W, "f1080", "hevc15", sc, n))
            vals.append(psnr(img(p), np.repeat(np.repeat(src, 2, 0), 2, 1)))
        s["live_nearest_vs_exact_2x_min_psnr"] = min(vals)
    a = glob.glob(os.path.join(W, "cap", "linear", "hevc15", "*", "*.png"))
    if a:
        s["live_linear_vs_offline_bilinear_mean_psnr"] = float(np.mean([psnr(img(p), img(os.path.join(W, "up", "hevc15-bilinear", *p.split(os.sep)[-2:]))) for p in a[:16]]))
    return s

def crops(conds):
    """Most detailed 384x216 region of the first frame of each scene (Sobel energy, watermark corner excluded), 2x nearest."""
    want = [("4K source", None), ("guest 4K, bicubic (RP client proxy)", "off:hevc15-bicubic"),
            ("guest 1080p, LG linear", "live:linear/hevc15"), ("guest 1080p, LG FSR 1.0", "live:fsr1.0/hevc15"),
            ("guest 1080p, LG FSR+CAS", "live:fsr1.0+cas0.5/hevc15")]
    os.makedirs(os.path.join(W, "crops"), exist_ok=True)
    for sc in SCENES:
        ref = img(os.path.join(W, "ref", sc, "01.png"))
        g = ref.mean(axis=2); e = np.abs(np.diff(g, axis=0))[:, :-1] + np.abs(np.diff(g, axis=1))[:-1, :]
        best, pos = -1, (0, 0)
        for y in range(0, 2160 - 216 - 200, 108):
            for x in range(0, 3840 - 384 - 600, 96):
                v = e[y:y + 216, x:x + 384].mean()
                if v > best: best, pos = v, (x, y)
        x, y = pos
        tiles = []
        for label, c in want:
            src = os.path.join(W, "ref", sc, "01.png") if c is None else (os.path.join(conds[c], sc, "01.png") if c in conds else None)
            if src:
                tiles.append(Image.open(src).convert("RGB").crop((x, y, x + 384, y + 216)).resize((768, 432), Image.NEAREST))
        out = Image.new("RGB", (768 * len(tiles), 432))
        for i, t in enumerate(tiles): out.paste(t, (768 * i, 0))
        out.save(os.path.join(W, "crops", f"{sc}.png"))
    return [l for l, c in want if c is None or c in conds]

def main():
    conds = conditions()
    jobs = [(n, d, s) for n, d in conds.items() for s in SCENES]
    with ThreadPoolExecutor(5) as ex:
        out = list(ex.map(lambda j: (j[0], j[2], score(*j)), jobs))
    res = {}
    for n, s, m in out: res.setdefault(n, {})[s] = m
    for n in res:
        res[n]["_mean"] = {k: float(np.mean([res[n][s][k] for s in SCENES])) for k in ("vmaf4k", "neg", "psnr_y", "ssim")}
        res[n]["_min_vmaf4k"] = min(res[n][s]["vmaf4k"] for s in SCENES)
    res["_sanity"] = sanity()
    res["_crop_columns"] = crops(conds)
    json.dump(res, open(os.path.join(W, "results.json"), "w"), indent=1)
    rows = sorted((n for n in res if not n.startswith("_")), key=lambda n: -res[n]["_mean"]["vmaf4k"])
    with open(os.path.join(W, "report.md"), "w") as f:
        f.write("| Condition | VMAF 4K | VMAF NEG | PSNR-Y | SSIM | worst-scene VMAF 4K |\n| --- | --- | --- | --- | --- | --- |\n")
        for n in rows:
            m = res[n]["_mean"]
            f.write(f"| {n} | {m['vmaf4k']:.2f} | {m['neg']:.2f} | {m['psnr_y']:.2f} | {m['ssim']:.4f} | {res[n]['_min_vmaf4k']:.2f} |\n")
        f.write("\nPer scene, VMAF 4K:\n\n| Condition | " + " | ".join(SCENES) + " |\n|" + " --- |" * (len(SCENES) + 1) + "\n")
        for n in rows:
            f.write(f"| {n} | " + " | ".join(f"{res[n][s]['vmaf4k']:.1f}" for s in SCENES) + " |\n")
        f.write(f"\nSanity: {json.dumps(res['_sanity'])}\n")
    print(open(os.path.join(W, "report.md")).read())

if __name__ == "__main__":
    main()
