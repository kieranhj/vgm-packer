#!/usr/bin/env python
# measure_delta.py
# Next-steps item 2 from docs/compression-analysis.md (sec 12.1): does delta
# pre-coding survive into real compressed bytes?
#
# Sec 8.6(e) showed frame-delta cuts the *order-0* entropy of tone periods ~31%
# and volumes ~28%. But order-0 entropy ignores back-references, and delta
# actively destroys the long literal runs that LZ4/ZX0 match on (a held note is
# one long run of equal bytes pre-delta, and a run of zeros post-delta - LZ
# already collapses both, so delta may give nothing, or hurt). So whether delta
# helps once a *real* coder is applied is an empirical question, measured here.
#
# Layout note: we measure on the de-interleaved FLAT (no-RLE) per-register
# columns - the P4f layout - not the RLE'd VGC streams. VGC's rle2()/rle() are
# value-format-specific (rle2 asserts 10-bit tone words: lo<=15, hi<=63; rle
# packs a 4-bit volume into the low nibble) and cannot ingest signed deltas
# without a redesigned RLE. The flat layout isolates the delta question cleanly,
# round-trips exactly, and is exactly what the ZX0/P4f reference already uses.
# Noise is never delta-coded (it keeps its 0x0f skip diff to avoid LFSR resets).
#
# For each file, four sizes over the flat de-interleaved columns:
#   LZ4    = per-stream LZ4 (255 window, 8-bit offsets), VGC-style    (baseline)
#   LZ4-d  = same, tone+volume columns delta-coded first
#   ZX0    = ZX0 over the concatenated flat columns == P4f            (baseline)
#   ZX0-d  = same, tone+volume columns delta-coded first
# A negative delta column means delta loses to plain LZ on this music.

import os
import sys
import glob

from modules.lz4enc import LZ4
from vgmpacker import VgmPacker
from measure_proposal2 import distil, quiet, zx02_size, ZX02

# tone (0-5) and volume (7-10) register columns get delta-coded; noise (6) does not.
DELTA_COLS = [0, 1, 2, 3, 4, 5, 7, 8, 9, 10]


def delta_col(col):
    """Frame-to-frame byte delta of one register column (mod 256), reversible
    by running-sum from 0. Self-checks the round-trip."""
    out = bytearray()
    prev = 0
    for v in col:
        out.append((v - prev) & 255)
        prev = v
    # round-trip
    acc = 0
    for i, d in enumerate(out):
        acc = (acc + d) & 255
        assert acc == col[i]
    return out


def flat_streams(packer, registers, do_delta):
    """The 8 flat (no-RLE) de-interleaved streams, optionally with the tone and
    volume columns delta-coded. Mirrors measure_proposal2.build_streams(do_rle=
    False) when do_delta is False, so the ZX0 column equals the P4f reference."""
    regs = registers
    if do_delta:
        regs = list(registers)
        for c in DELTA_COLS:
            regs[c] = delta_col(registers[c])
    return [
        packer.combine_registers(regs, [0, 1]),
        packer.combine_registers(regs, [2, 3]),
        packer.combine_registers(regs, [4, 5]),
        packer.diff(registers[6], 0x0f),          # noise: never delta'd
        regs[7], regs[8], regs[9], regs[10],
    ]


def lz4_total(streams):
    """Sum of per-stream LZ4 block sizes (255 window, 8-bit offsets) - exactly
    VGC's compression step, minus the constant file framing/magic."""
    lz4 = LZ4()
    lz4.setCompression(9)
    lz4.optimizedCompression(True)
    out = bytearray()
    lz4.beginFrame(out)
    total = 0
    for s in streams:
        total += len(lz4.compressBlock(bytes(s)))
    return total


def main():
    folder = sys.argv[1] if len(sys.argv) > 1 else "vgm"
    files = sorted(glob.glob(os.path.join(folder, "*.vgm")))
    cache = os.path.join(folder, "_cache")
    if not os.path.isdir(cache):
        os.makedirs(cache)

    def progress(m):
        sys.stderr.write(m + "\n"); sys.stderr.flush()

    have_zx0 = os.path.exists(ZX02) or os.path.dirname(ZX02) == ""
    if not have_zx0:
        progress("note: ZX0 binary not found (%r); ZX0 columns will be '-'." % ZX02)
        progress("      build it (see measure_proposal2.py) or set ZX0_BIN.")

    rows = []
    for i, f in enumerate(files, 1):
        name = os.path.basename(f)
        progress("[%d/%d] %s" % (i, len(files), name))
        packer = VgmPacker()
        try:
            with quiet():
                data_block, _ = distil(f)
                registers = packer.split_raw(data_block, True)
                flat = flat_streams(packer, registers, False)
                flat_d = flat_streams(packer, registers, True)
                lz = lz4_total(flat)
                lzd = lz4_total(flat_d)
                zx = zxd = None
                if have_zx0:
                    zx = zx02_size(flat, os.path.join(cache, name + ".dz.flat.bin"), f)
                    zxd = zx02_size(flat_d, os.path.join(cache, name + ".dz.flatd.bin"), f)
        except Exception as e:
            progress("      FAILED: %r" % e)
            rows.append((name, None, None, None, None))
            continue
        rows.append((name, lz, lzd, zx, zxd))
        progress("      LZ4=%s LZ4-d=%s ZX0=%s ZX0-d=%s" % (lz, lzd, zx, zxd))

    # ---- report ----
    print("")
    print("Delta pre-coding on the FLAT de-interleaved columns (tone+volume only;")
    print("noise keeps its 0x0f skip diff). '-d' = columns delta-coded before the coder.")
    print("LZ4 = per-stream LZ4 (255 win); ZX0 = single stream over the concatenation (==P4f).")
    print("")
    hdr = "%-40s %9s %9s %7s %9s %9s %7s"
    print(hdr % ("file", "LZ4", "LZ4-d", "d/LZ4", "ZX0", "ZX0-d", "d/ZX0"))
    print("-" * 96)
    cell = lambda v: ("%9d" % v) if v is not None else "%9s" % "-"
    ratio = lambda a, b: ("%6.2fx" % (float(b) / a)) if (a and b) else "   -  "
    tot = {"lz": 0, "lzd": 0, "zx": 0, "zxd": 0}
    n = {"lz": 0, "lzd": 0, "zx": 0, "zxd": 0}
    for name, lz, lzd, zx, zxd in rows:
        print("%-40s %s %s %s %s %s %s" %
              (name[:40], cell(lz), cell(lzd), ratio(lz, lzd),
               cell(zx), cell(zxd), ratio(zx, zxd)))
        for k, v in (("lz", lz), ("lzd", lzd), ("zx", zx), ("zxd", zxd)):
            if v is not None:
                tot[k] += v; n[k] += 1
    print("-" * 96)
    comp = [r for r in rows if all(x is not None for x in r[1:])]
    if comp:
        c = {"lz": 0, "lzd": 0, "zx": 0, "zxd": 0}
        for _, lz, lzd, zx, zxd in comp:
            c["lz"] += lz; c["lzd"] += lzd; c["zx"] += zx; c["zxd"] += zxd
        print("")
        print("Totals over %d comparable files:" % len(comp))
        print("  LZ4   %8d  ->  LZ4-d %8d   (%+.1f%%)" %
              (c["lz"], c["lzd"], 100.0 * (c["lzd"] - c["lz"]) / c["lz"]))
        print("  ZX0   %8d  ->  ZX0-d %8d   (%+.1f%%)" %
              (c["zx"], c["zxd"], 100.0 * (c["zxd"] - c["zx"]) / c["zx"]))
        print("  (negative %% = delta helps; positive = delta hurts the real coder)")
    elif n["lz"]:
        print("")
        print("LZ4 totals over %d files: LZ4 %d -> LZ4-d %d (%+.1f%%)" %
              (n["lz"], tot["lz"], tot["lzd"],
               100.0 * (tot["lzd"] - tot["lz"]) / tot["lz"]))


if __name__ == "__main__":
    main()
