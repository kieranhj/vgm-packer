#!/usr/bin/env python
# measure_v2_runtime.py - per-frame decode cost of v1 vs v2 player across the
# corpus, same methodology as bench_all (SN write stubbed). Confirms the v2
# format keeps (or improves) the bounded/consistent runtime while being smaller.
import os
import sys
import glob
import pickle
import statistics

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bench_all as B
import pack_vgi as v1

HERE = B.HERE
BEEBASM = B.BEEBASM
CACHE = B.CACHE


def build_and_measure(vgm, vgi2):
    # pack the right format to music.vgi
    import io, contextlib
    with contextlib.redirect_stdout(io.StringIO()):
        v1.pack(vgm, os.path.join(HERE, "music.vgi"), version=2 if vgi2 else 1)
    B.sh([BEEBASM, "-i", "player.asm", "-D", "TEST=0", "-D", "RING_PAGE=&C0",
          "-D", "VGI2=%d" % (1 if vgi2 else 0), "-D", "UNROLL=0",
          "-d", "-labels", "labels_full.txt"], cwd=HERE)
    lab = B.labels(os.path.join(HERE, "labels_full.txt"))
    img = open(os.path.join(HERE, "Player"), "rb").read()
    nf = img[lab["music_data"] - 0x1900 + 4] | (img[lab["music_data"] - 0x1900 + 5] << 8)
    return B.measure(img, 0x1900, lab["sn"],
                     (lab["init_streams"], 0, 0, 0, False, lab["do_frame"]), nf)


def smm(c):
    return min(c), int(statistics.median(c)), int(round(statistics.fmean(c))), max(c)


def main():
    files = sorted(glob.glob(os.path.join(B.ROOT, "vgm", "*.vgm")))
    rows = []
    for i, f in enumerate(files, 1):
        name = os.path.basename(f)
        sys.stderr.write("[%d/%d] %s\n" % (i, len(files), name)); sys.stderr.flush()
        c1 = build_and_measure(f, False)
        c2 = build_and_measure(f, True)
        rows.append((name, c1, c2))
    if not os.path.isdir(CACHE):
        os.makedirs(CACHE)
    pickle.dump({"tunes": [{"name": n, "v1": a, "v2": b} for n, a, b in rows]},
                open(os.path.join(CACHE, "v2_runtime.pkl"), "wb"))

    print("\n%-30s %-23s %-23s" % ("tune", "v1 min/med/mean/max", "v2 min/med/mean/max"))
    print("-" * 80)
    allv1, allv2 = [], []
    for n, a, b in rows:
        print("%-30s %5d %5d %5d %5d   %5d %5d %5d %5d" % ((n[:30],) + smm(a) + smm(b)))
        allv1 += a; allv2 += b
    a1, a2 = np.array(allv1), np.array(allv2)
    print("\ncorpus percentiles (cycles/frame):")
    ps = [50, 90, 99, 99.9, 100]
    print("  %-4s " % "" + " ".join("%7s" % ("p%g" % p if p < 100 else "max") for p in ps))
    print("  v1   " + " ".join("%7d" % int(np.percentile(a1, p)) for p in ps))
    print("  v2   " + " ".join("%7d" % int(np.percentile(a2, p)) for p in ps))
    print("\nmean: v1 %.0f  v2 %.0f   worst: v1 %d  v2 %d" %
          (a1.mean(), a2.mean(), a1.max(), a2.max()))


if __name__ == "__main__":
    main()
