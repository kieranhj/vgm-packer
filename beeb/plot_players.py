#!/usr/bin/env python
# plot_players.py - from beeb/_cache/players.pkl produce:
#   players_distribution.png : corpus per-frame cost histogram, VGI vs VGI-unroll
#                              vs VGC-opt (with VGC baseline outline for context)
#   players_timeseries.png   : per-frame cost over a window of one tune for each
#                              player, to visualise the remaining spikiness
import os
import pickle
import sys

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

HERE = os.path.dirname(os.path.abspath(__file__))
PKL = os.path.join(HERE, "_cache", "players.pkl")

C = {"vgi": "#1f77b4", "vgu": "#2ca02c", "vgc": "#d62728", "vgcopt": "#ff7f0e"}
LBL = {"vgi": "VGI (looped)", "vgu": "VGI (unrolled)",
       "vgc": "VGC (original)", "vgcopt": "VGC-opt"}


def main():
    if not os.path.exists(PKL):
        print("run bench_players.py first"); return 1
    tunes = pickle.load(open(PKL, "rb"))["tunes"]

    pooled = {k: np.concatenate([np.array(t[k]) for t in tunes if t[k] is not None])
              for k in C}

    # ---- distribution histogram ----
    fig, ax = plt.subplots(figsize=(11, 5.5))
    hi = max(pooled[k].max() for k in pooled) + 200
    bins = np.arange(0, hi, 40)
    for k in ("vgi", "vgu", "vgcopt"):
        d = pooled[k]
        w = np.ones_like(d, float) * 100.0 / len(d)
        ax.hist(d, bins=bins, weights=w, histtype="stepfilled", alpha=0.5, color=C[k],
                label="%s  (median %d, p99 %d, max %d)" %
                (LBL[k], int(np.median(d)), int(np.percentile(d, 99)), int(d.max())))
    # VGC original as an outline for context
    d = pooled["vgc"]
    w = np.ones_like(d, float) * 100.0 / len(d)
    ax.hist(d, bins=bins, weights=w, histtype="step", color=C["vgc"], lw=1.2,
            label="%s  (median %d, p99 %d, max %d)" %
            (LBL["vgc"], int(np.median(d)), int(np.percentile(d, 99)), int(d.max())))
    ax.set_xlabel("per-frame decode cost (6502 cycles, SN write stubbed)")
    ax.set_ylabel("% of frames")
    ax.set_title("Per-frame decode cost distribution, whole corpus (74052 frames)")
    ax.legend(fontsize=9)
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(os.path.join(HERE, "players_distribution.png"), dpi=110)
    print("wrote players_distribution.png")

    # ---- time series for one spiky tune ----
    def spikiness(t):
        v = np.array(t["vgc"]) if t["vgc"] is not None else np.array(t["vgi"])
        return np.percentile(v, 99.5) / max(1, np.median(v))
    pick = max(tunes, key=spikiness)
    win = min(700, pick["nf"])
    x = np.arange(win)
    fig, axs = plt.subplots(2, 1, figsize=(11, 8), sharex=True)
    # top: the two VGC players (spiky)
    for k in ("vgc", "vgcopt"):
        if pick[k] is not None:
            axs[0].plot(x, np.array(pick[k])[:win], color=C[k], lw=0.7, label=LBL[k])
    axs[0].set_title("Per-frame cost over time - %s (first %d frames)\nVGC players: RLE-driven spikes"
                     % (pick["name"][:42], win))
    axs[0].set_ylabel("cycles"); axs[0].legend(fontsize=9); axs[0].grid(True, alpha=0.3)
    # bottom: the two VGI players (flat)
    for k in ("vgi", "vgu"):
        axs[1].plot(x, np.array(pick[k])[:win], color=C[k], lw=0.8, label=LBL[k])
    axs[1].set_title("VGI players: flat, bounded")
    axs[1].set_xlabel("frame"); axs[1].set_ylabel("cycles")
    axs[1].legend(fontsize=9); axs[1].grid(True, alpha=0.3)
    # shared y-limits so the spikiness is visually comparable
    ymax = max(np.array(pick[k])[:win].max() for k in C if pick[k] is not None) + 100
    axs[0].set_ylim(0, ymax); axs[1].set_ylim(0, ymax)
    fig.tight_layout()
    fig.savefig(os.path.join(HERE, "players_timeseries.png"), dpi=110)
    print("wrote players_timeseries.png (tune: %s)" % pick["name"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
