#!/usr/bin/env python
# plot_dist.py - visualise the per-frame cycle-cost distribution from the data
# captured by bench_all.py (bench/_cache/bench_costs.pkl). Produces a 2-panel PNG:
#   (top)    corpus-wide histogram of per-frame decode cost, incremental vs VGC
#   (bottom) per-frame cost over a window of one busy tune, to show whether the
#            VGC spikes are isolated or recurring.
import os
import pickle
import sys

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

HERE = os.path.dirname(os.path.abspath(__file__))
PKL = os.path.join(HERE, "..", "bench", "_cache", "bench_costs.pkl")
OUT = os.path.join(HERE, "frame_cost_distribution.png")

INCR_C = "#1f77b4"
VGC_C = "#d62728"


def main():
    if not os.path.exists(PKL):
        print("no %s - run bench_all.py first" % PKL)
        return 1
    blob = pickle.load(open(PKL, "rb"))
    tunes = blob["tunes"]

    alln = np.concatenate([np.array(t["incr"]) for t in tunes])
    allv = np.concatenate([np.array(t["vgc_cost"]) for t in tunes])

    # pick the time-series tune: the one whose VGC p99/median ratio is largest
    # (most "spiky" relative to its typical frame), among non-trivial tunes.
    def spikiness(t):
        v = np.array(t["vgc_cost"])
        return np.percentile(v, 99) / max(1.0, np.median(v))
    pick = max(tunes, key=spikiness)

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 9))

    # ---- panel 1: histogram ----
    hi = int(max(alln.max(), allv.max())) + 200
    bins = np.arange(0, hi, 50)
    for data, c, lab in ((alln, INCR_C, "incremental (.vgi)"),
                         (allv, VGC_C, "existing VGC (8xLZ4)")):
        w = np.ones_like(data, dtype=float) * 100.0 / len(data)
        ax1.hist(data, bins=bins, weights=w, histtype="stepfilled",
                 alpha=0.45, color=c,
                 label="%s  (median %d, p99 %d, max %d)"
                 % (lab, int(np.median(data)), int(np.percentile(data, 99)), int(data.max())))
    ax1.set_xlabel("per-frame decode cost (6502 cycles)")
    ax1.set_ylabel("% of frames")
    ax1.set_title("Per-frame decode cost distribution, whole corpus (%d frames)\n"
                  "decode + register reconstruct, SN write stubbed" % len(alln))
    ax1.legend(loc="upper right", fontsize=9)
    ax1.grid(True, alpha=0.3)

    # ---- panel 2: time series of the spikiest tune ----
    n = np.array(pick["incr"])
    v = np.array(pick["vgc_cost"])
    win = min(700, len(v))
    x = np.arange(win)
    ax2.plot(x, v[:win], color=VGC_C, lw=0.8, label="VGC")
    ax2.plot(x, n[:win], color=INCR_C, lw=0.9, label="incremental")
    ax2.axhline(n.max(), color=INCR_C, ls="--", lw=0.8, alpha=0.7,
                label="incremental worst (%d)" % n.max())
    ax2.set_xlabel("frame")
    ax2.set_ylabel("decode cost (cycles)")
    ax2.set_title("Per-frame cost over time - %s (first %d frames)" %
                  (pick["name"][:40], win))
    ax2.legend(loc="upper right", fontsize=9)
    ax2.grid(True, alpha=0.3)

    fig.tight_layout()
    fig.savefig(OUT, dpi=110)
    print("wrote %s" % OUT)

    # also print the spike-frequency numbers the figure illustrates
    for thr in (n.max(), 3500, 4000, 4500):
        print("  frames > %d cyc: incremental %.2f%%, VGC %.2f%%"
              % (thr, 100.0 * (alln > thr).mean(), 100.0 * (allv > thr).mean()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
