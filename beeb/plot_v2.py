#!/usr/bin/env python
# plot_v2.py - overlay the per-frame decode-cost distribution of the v1 and v2
# players (from measure_v2_runtime.py) to show v2 keeps the tight/bounded shape
# while being smaller. Reads beeb/_cache/v2_runtime.pkl.
import os
import pickle
import sys

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

HERE = os.path.dirname(os.path.abspath(__file__))
PKL = os.path.join(HERE, "_cache", "v2_runtime.pkl")
OUT = os.path.join(HERE, "v2_cost_distribution.png")


def main():
    if not os.path.exists(PKL):
        print("no %s" % PKL); return 1
    blob = pickle.load(open(PKL, "rb"))
    v1 = np.concatenate([np.array(t["v1"]) for t in blob["tunes"]])
    v2 = np.concatenate([np.array(t["v2"]) for t in blob["tunes"]])

    hi = int(max(v1.max(), v2.max())) + 100
    bins = np.arange(0, hi, 25)
    fig, ax = plt.subplots(figsize=(10, 5))
    for d, c, lab in ((v1, "#1f77b4", "v1 (current)"), (v2, "#2ca02c", "v2 (run+extlen+optimal)")):
        w = np.ones_like(d, float) * 100.0 / len(d)
        ax.hist(d, bins=bins, weights=w, histtype="stepfilled", alpha=0.45, color=c,
                label="%s  (median %d, p99 %d, max %d)"
                % (lab, int(np.median(d)), int(np.percentile(d, 99)), int(d.max())))
    ax.set_xlabel("per-frame decode cost (6502 cycles)")
    ax.set_ylabel("% of frames")
    ax.set_title("VGI v1 vs v2 per-frame decode cost, whole corpus (%d frames)\n"
                 "decode + register reconstruct, SN write stubbed" % len(v1))
    ax.legend(loc="upper right")
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(OUT, dpi=110)
    print("wrote %s" % OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
