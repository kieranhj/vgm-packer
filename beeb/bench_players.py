#!/usr/bin/env python
# bench_players.py - per-frame decode cost across the corpus for all four players:
#   VGI        (player.asm, UNROLL=0)
#   VGI-unroll (player.asm, UNROLL=1)
#   VGC        (vgc/sim.asm, OPT=0  - original)
#   VGC-opt    (vgc/sim.asm, OPT=1  - resident-context)
# SN write stubbed to RTS in all four (so only decode + dispatch is timed; all
# now share the same sn routine, so this isolates the decompression cost).
# Pickles per-tune per-frame arrays for the plots and prints a summary table.
import os
import re
import sys
import glob
import shutil
import pickle
import statistics
import io
import contextlib

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bench_all as B
import pack_vgi

HERE = B.HERE
ROOT = B.ROOT
BEEBASM = B.BEEBASM
CACHE = B.CACHE
VGCDIR = os.path.join(HERE, "vgc")


def vgi_build(unroll):
    B.sh([BEEBASM, "-i", "player.asm", "-D", "TEST=0", "-D", "RING_PAGE=&C0",
          "-D", "VGI2=1", "-D", "UNROLL=%d" % unroll, "-d", "-labels",
          "labels_full.txt"], cwd=HERE)
    lab = B.labels(os.path.join(HERE, "labels_full.txt"))
    img = open(os.path.join(HERE, "Player"), "rb").read()
    nf = img[lab["music_data"] - 0x1900 + 4] | (img[lab["music_data"] - 0x1900 + 5] << 8)
    return img, lab, nf


def vgi_measure(img, lab, nf):
    return B.measure(img, 0x1900, lab["sn"],
                     (lab["init_streams"], 0, 0, 0, False, lab["do_frame"]), nf)


def vgc_build(opt, vgcfile):
    shutil.copyfile(vgcfile, os.path.join(VGCDIR, "ghost.vgc"))
    B.sh([BEEBASM, "-i", "sim.asm", "-D", "OPT=%d" % opt, "-d",
          "-labels", "labels%d.txt" % opt], cwd=VGCDIR)
    lab = B.labels(os.path.join(VGCDIR, "labels%d.txt" % opt))
    img = open(os.path.join(VGCDIR, "Vgc"), "rb").read()
    return img, lab


def vgc_measure(img, lab, nf):
    bhi = lab["vgm_stream_buffers"] >> 8
    d = lab["vgm_data"]
    return B.measure(img, 0x1100, lab["sn_write"],
                     (lab["vgm_init"], bhi, d & 0xff, (d >> 8) & 0xff, True, lab["vgm_update"]), nf)


def smm(c):
    return min(c), int(statistics.median(c)), int(round(statistics.fmean(c))), max(c)


def main():
    files = sorted(glob.glob(os.path.join(ROOT, "vgm", "*.vgm")))
    rows = []
    for i, f in enumerate(files, 1):
        name = os.path.basename(f)
        sys.stderr.write("[%d/%d] %s\n" % (i, len(files), name)); sys.stderr.flush()
        with contextlib.redirect_stdout(io.StringIO()):
            pack_vgi.pack(f, os.path.join(HERE, "music.vgi"), version=2)
        img0, lab0, nf = vgi_build(0); vgi = vgi_measure(img0, lab0, nf)
        img1, lab1, _ = vgi_build(1); vgu = vgi_measure(img1, lab1, nf)
        vgcfile = os.path.join(CACHE, re.sub(r'[^A-Za-z0-9._-]', '_', name) + ".vgc")
        gb = go = None
        if os.path.exists(vgcfile):
            ib, lb = vgc_build(0, vgcfile); gb = vgc_measure(ib, lb, nf)
            io_, lo = vgc_build(1, vgcfile); go = vgc_measure(io_, lo, nf)
        rows.append(dict(name=name, nf=nf, vgi=vgi, vgu=vgu, vgc=gb, vgcopt=go))

    pickle.dump({"tunes": rows}, open(os.path.join(CACHE, "players.pkl"), "wb"))

    keys = [("vgi", "VGI"), ("vgu", "VGI-unr"), ("vgc", "VGC"), ("vgcopt", "VGC-opt")]
    print("\nper-frame decode cost (cycles, SN write stubbed)  -  min/median/mean/max\n")
    print("%-26s %5s | " % ("tune", "nf") +
          " | ".join("%-21s" % n for _, n in keys))
    print("-" * 122)
    pooled = {k: [] for k, _ in keys}
    for r in rows:
        cells = []
        for k, _ in keys:
            if r[k] is None:
                cells.append("%-21s" % "-")
            else:
                cells.append("%4d %4d %4d %4d   " % smm(r[k]))
                pooled[k] += r[k]
        print("%-26s %5d | " % (r["name"][:26], r["nf"]) + " | ".join(cells))
    print("-" * 122)
    print("\ncorpus percentiles (pooled):")
    print("  %-9s %6s %6s %6s %7s %6s" % ("player", "p50", "p90", "p99", "p99.9", "max"))
    for k, n in keys:
        if not pooled[k]:
            continue
        a = np.array(pooled[k])
        print("  %-9s %6d %6d %6d %7d %6d" %
              (n, np.percentile(a, 50), np.percentile(a, 90), np.percentile(a, 99),
               np.percentile(a, 99.9), a.max()))


if __name__ == "__main__":
    main()
