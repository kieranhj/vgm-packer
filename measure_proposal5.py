#!/usr/bin/env python
# measure_proposal5.py
# Measures "Proposal 5" (synchronised frame-LZ: one LZ context, offsets in frame
# units, constant per-frame decode) against VGC (8x LZ4) and P4 (single zx02).
#
# P5 is computed on exactly the same distilled+de-interleaved register data VGC
# uses, transposed to frame-major fixed-width records, so the comparison is fair.
#
# Reported variants:
#   P5-1  single context  : one 9-byte record/frame (tones+noise+packed volumes)
#   P5-2  two contexts     : tones+noise (7B) and volumes (2B) as separate
#                            frame-LZ streams - isolates the volatile envelopes
# each at three back-reference windows:
#   w256   ~ VGC RAM regime (ring ~ 2.3KB)
#   w1820  ~ one 16KB sideways RAM bank (1820 * 9 ~= 16KB)
#   wInf   unlimited (decompress-once regime, compare against P4)

import os
import sys
import glob

from measure_proposal2 import (distil, build_streams, zx02_size, vgc_size,
                                fresh, quiet)
from vgmpacker import VgmPacker
from modules.framelz import frame_lz

WINDOWS = [("w256", 256), ("w1820", 1820), ("wInf", None)]
W_FULL = 9   # 6 tone + 1 noise + 2 packed-volume bytes per frame


def build_frames(packer, data_block):
    """Frame-major fixed-width records from the de-interleaved registers.
    Volumes are nibble-packed (v0|v1, v2|v3); noise is diffed to 0x0f skips
    (matches P4's treatment and what the player wants to avoid LFSR resets)."""
    regs = packer.split_raw(data_block, True)
    nf = len(regs[0])
    noise = packer.diff(regs[6], 0x0f)   # regs[6] has 0x08 EOF appended; len nf+1
    recs = []
    for i in range(nf):
        v01 = (regs[7][i] & 15) | ((regs[8][i] & 15) << 4)
        v23 = (regs[9][i] & 15) | ((regs[10][i] & 15) << 4)
        recs.append(bytes((regs[0][i], regs[1][i], regs[2][i], regs[3][i],
                           regs[4][i], regs[5][i], noise[i], v01, v23)))
    return recs


def p5_single(records, window):
    return len(frame_lz(records, window))


def p5_split(records, window):
    tones = [r[0:7] for r in records]    # 6 tone bytes + noise
    vols = [r[7:9] for r in records]     # 2 packed-volume bytes
    return len(frame_lz(tones, window)) + len(frame_lz(vols, window))


def main():
    folder = sys.argv[1] if len(sys.argv) > 1 else "vgm"
    files = sorted(glob.glob(os.path.join(folder, "*.vgm")))
    cache = os.path.join(folder, "_cache")
    if not os.path.isdir(cache):
        os.makedirs(cache)

    def cpath(name, ext):
        return os.path.join(cache, name + ext)

    def progress(msg):
        sys.stderr.write(msg + "\n")
        sys.stderr.flush()

    def safe(label, fn):
        try:
            with quiet():
                return fn()
        except Exception as e:
            progress("      %-6s FAILED: %s" % (label, repr(e)))
            return None

    rows = []
    for i, f in enumerate(files, 1):
        name = os.path.basename(f)
        progress("[%d/%d] %s" % (i, len(files), name))
        packer = VgmPacker()

        distilled = safe("dist", lambda: distil(f))
        if distilled is None:
            continue
        data_block, _ = distilled

        records = safe("frames", lambda: build_frames(packer, data_block))
        nframes = len(records) if records else 0

        row = {"name": name, "frames": nframes}
        if records is not None:
            for tag, win in WINDOWS:
                row["s_" + tag] = safe("P5-1 " + tag, lambda w=win: p5_single(records, w))
                row["d_" + tag] = safe("P5-2 " + tag, lambda w=win: p5_split(records, w))

        rle = safe("rle", lambda: build_streams(packer, data_block, True))
        row["p4"] = safe("p4", lambda: zx02_size(rle, cpath(name, ".p4.bin"), f)) if rle else None
        row["vgc"] = safe("vgc", lambda: vgc_size(packer, f, cpath(name, ".vgc"), False))
        row["vgh"] = safe("vgch", lambda: vgc_size(packer, f, cpath(name, ".vgch"), True))

        rows.append(row)
        progress("      frames=%d  P5-1: %s/%s/%s  P5-2: %s/%s/%s  P4=%s VGC=%s" % (
            nframes, row.get("s_w256"), row.get("s_w1820"), row.get("s_wInf"),
            row.get("d_w256"), row.get("d_w1820"), row.get("d_wInf"),
            row.get("p4"), row.get("vgc")))

    # ---- report ----
    print("")
    print("P5-1 = synchronised frame-LZ, single context (9-byte frame records)")
    print("P5-2 = frame-LZ split: tones+noise (7B) | volumes (2B), two contexts")
    print("  w256  ring ~2.3KB (VGC RAM regime) | w1820 ring ~16KB (one bank) | wInf full tune")
    print("P4   = single zx02 over de-interleave+RLE (decompress-once)")
    print("VGC  = 8x LZ4 (current); VGC+H = +Huffman")
    print("")

    hdr = "%-34s %6s | %7s %7s %7s | %7s %7s %7s | %7s %7s %7s"
    print(hdr % ("file", "frames",
                 "P5-1.256", "P5-1.1820", "P5-1.Inf",
                 "P5-2.256", "P5-2.1820", "P5-2.Inf",
                 "P4", "VGC", "VGC+H"))
    print("-" * 132)
    cell = lambda v: ("%7d" % v) if v is not None else "%7s" % "-"
    cols = ["s_w256", "s_w1820", "s_wInf", "d_w256", "d_w1820", "d_wInf", "p4", "vgc", "vgh"]
    tot = {c: 0 for c in cols}
    tot["vgc_for"] = {c: 0 for c in cols}   # VGC summed only where col present
    for r in rows:
        print(("%-34s %6s | " % (r["name"][:34], r["frames"])) +
              "%s %s %s | %s %s %s | %s %s %s" % tuple(cell(r.get(c)) for c in cols))
        for c in cols:
            if r.get(c) is not None and r.get("vgc") is not None:
                tot[c] += r[c]
                tot["vgc_for"][c] += r["vgc"]
    print("-" * 132)

    print("")
    print("Totals vs VGC (apples-to-apples: each column summed only over files where")
    print("both it and VGC succeeded):")
    print("  %-12s %12s %12s %10s" % ("scheme", "bytes", "VGC bytes", "vs VGC"))
    labels = {"s_w256": "P5-1 w256", "s_w1820": "P5-1 w1820", "s_wInf": "P5-1 wInf",
              "d_w256": "P5-2 w256", "d_w1820": "P5-2 w1820", "d_wInf": "P5-2 wInf",
              "p4": "P4", "vgc": "VGC", "vgh": "VGC+H"}
    for c in cols:
        ref = tot["vgc_for"][c]
        ratio = ("%.2fx" % (tot[c] / ref)) if ref else "-"
        print("  %-12s %12d %12d %10s" % (labels[c], tot[c], ref, ratio))


if __name__ == "__main__":
    main()
