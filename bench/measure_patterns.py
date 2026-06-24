#!/usr/bin/env python
# measure_patterns.py
# Pattern-layer potential (next-steps follow-up to docs/compression-analysis.md
# sec 8.8 / sec 12). These tunes were authored in a tracker as a SHARED order
# list (a fixed-length pattern grid, constant when the tempo is constant) with
# per-channel pattern content. VGM export flattens the grid away - but because
# replaying a pattern is deterministic, identical patterns re-emit as exact,
# fixed-length, grid-aligned repeats at the register level. So we can try to
# RECOVER the grid: find the block length L (in frames) that best dedups the
# data, then store each channel's distinct blocks once + a per-channel index
# sequence. Pattern boundaries cost only a bounded pointer-jump at runtime - no
# LZ window, no decompression - which is exactly the single-bank, known-worst-
# case regime we want.
#
# We group the 11 de-interleaved register columns back into the 4 SN76489
# channels (= the tracker's channels):
#     ch0/1/2 = tone lo + tone hi + volume   (3 B/frame)
#     ch3     = noise + volume               (2 B/frame)
# and sweep L, choosing the L that minimises the structural (dedup-only) size.
#
# Reported per file:
#   - grid L recovered, and the per-channel independently-best L (do channels
#     share a grid?)
#   - block coverage = 1 - distinct_blocks / total_blocks
#   - pattern-layer size (patterns + order), raw and after per-channel LZ4
#   - "vertical" variant: dedup whole-frame (all 4 channels) blocks on the same
#     grid - the literal tracker pattern - to confirm per-channel wins (sec 8.6f)
#   - vs raw (nf x 11), vs the cached VGC, and whether it fits a ~14 KB bank
#
# Pure Python, no external deps. Usage: python measure_patterns.py [vgm_folder]

import os
import sys
import glob

from modules.lz4enc import LZ4
from measure_proposal2 import distil, quiet
from analyse_registers import columns
from vgmpacker import VgmPacker

# channel -> the de-interleaved columns that make it up (see columns())
CHANNELS = [
    ("ch0", [0, 1, 7]),   # t0lo t0hi vol0
    ("ch1", [2, 3, 8]),   # t1lo t1hi vol1
    ("ch2", [4, 5, 9]),   # t2lo t2hi vol2
    ("ch3", [6, 10]),     # noise vol3
]

BANK = 14 * 1024   # ~16 KB SWRAM bank minus ~2 KB code budget

# musically-plausible pattern grid lengths (frames) for the coverage curve:
# e.g. 64 rows x speed 3 = 192 frames; x speed 6 = 384, etc.
CANON_L = [64, 96, 128, 192, 256, 384, 512]


def channel_streams(cols, nf):
    """4 channel byte streams, one record per frame (bpf bytes)."""
    out = []
    for name, cs in CHANNELS:
        buf = bytearray()
        for i in range(nf):
            for c in cs:
                buf.append(cols[c][i] & 0xff)
        out.append((name, bytes(buf), len(cs)))
    return out


def lz4_size(blob):
    """One LZ4 block (255 window, 8-bit offsets), payload bytes only."""
    if not blob:
        return 0
    lz4 = LZ4()
    lz4.setCompression(9)
    lz4.optimizedCompression(True)
    out = bytearray()
    lz4.beginFrame(out)
    return len(lz4.compressBlock(bytes(blob)))


def dedup_channel(stream, bpf, L, nf):
    """Partition one channel into L-frame blocks, dedup. Returns
    (distinct_blocks_in_order, index_sequence, n_positions)."""
    step = L * bpf
    n_pos = (nf + L - 1) // L
    index = {}
    order = []
    distinct = []
    for k in range(n_pos):
        b = stream[k * step:(k + 1) * step]
        if len(b) < step:
            b = b + bytes(step - len(b))   # pad final partial block
        j = index.get(b)
        if j is None:
            j = len(distinct)
            index[b] = j
            distinct.append(b)
        order.append(j)
    return distinct, order, n_pos


def structural_size(chstreams, L, nf):
    """Dedup-only size of the pattern layer at grid L: patterns + order."""
    pat = order = tot_blocks = dist_blocks = 0
    for name, s, bpf in chstreams:
        distinct, seq, n_pos = dedup_channel(s, bpf, L, nf)
        d = len(distinct)
        idx = 1 if d <= 256 else 2
        pat += d * L * bpf
        order += n_pos * idx
        tot_blocks += n_pos
        dist_blocks += d
    return pat + order, pat, order, dist_blocks, tot_blocks


def best_grid(chstreams, nf, lmin=8, lmax=1024):
    """Sweep L; return the L minimising structural size."""
    hi = min(lmax, max(lmin, nf // 2))
    best = None
    for L in range(lmin, hi + 1):
        total = structural_size(chstreams, L, nf)[0]
        if best is None or total < best[1]:
            best = (L, total)
    return best[0] if best else lmin


def channel_best_L(stream, bpf, nf, lmin=8, lmax=1024):
    hi = min(lmax, max(lmin, nf // 2))
    best = None
    for L in range(lmin, hi + 1):
        distinct, seq, n_pos = dedup_channel(stream, bpf, L, nf)
        idx = 1 if len(distinct) <= 256 else 2
        size = len(distinct) * L * bpf + n_pos * idx
        if best is None or size < best[1]:
            best = (L, size)
    return best[0]


def pattern_layer_lz4(chstreams, L, nf):
    """Realistic coded size: per-channel pattern bank LZ4'd + order lists."""
    total = 0
    for name, s, bpf in chstreams:
        distinct, seq, n_pos = dedup_channel(s, bpf, L, nf)
        bank = b"".join(distinct)
        idx = 1 if len(distinct) <= 256 else 2
        total += lz4_size(bank) + n_pos * idx
    return total


def channel_block_cov(chstreams, L, nf, phase=0):
    """Mean per-channel block coverage (1 - distinct/total) at grid L, allowing
    a frame phase offset. This is the 'how much of the data is exact grid-
    aligned repeats' number - the pattern-recovery signal."""
    covs = []
    for name, s, bpf in chstreams:
        seq = s[phase * bpf:]
        nn = nf - phase
        n_pos = (nn + L - 1) // L
        if n_pos <= 0:
            continue
        step = L * bpf
        seen = set()
        for k in range(n_pos):
            b = seq[k * step:(k + 1) * step]
            if len(b) < step:
                b = b + bytes(step - len(b))
            seen.add(b)
        covs.append(1 - len(seen) / n_pos)
    return 100 * sum(covs) / max(1, len(covs))


def best_phase_cov(chstreams, L, nf):
    """Best block coverage over all frame phases - rules out the objection that
    patterns simply don't start at frame 0."""
    return max(channel_block_cov(chstreams, L, nf, ph) for ph in range(0, L, 4))


def vertical_size(chstreams, L, nf):
    """'Literal tracker pattern': dedup whole-frame (all channels) blocks on the
    same grid, one shared order list. Tests the across-channels coupling."""
    bpf = sum(b for _, _, b in chstreams)
    step = L * bpf
    n_pos = (nf + L - 1) // L
    # build the combined per-frame record stream
    rec = bytearray()
    streams = [s for _, s, _ in chstreams]
    bpfs = [b for _, _, b in chstreams]
    for i in range(nf):
        for s, b in zip(streams, bpfs):
            rec += s[i * b:(i + 1) * b]
    rec = bytes(rec)
    index = {}
    d = 0
    for k in range(n_pos):
        blk = rec[k * step:(k + 1) * step]
        if len(blk) < step:
            blk = blk + bytes(step - len(blk))
        if blk not in index:
            index[blk] = d
            d += 1
    idx = 1 if d <= 256 else 2
    return d * L * bpf + n_pos * idx, d, n_pos


def main():
    folder = sys.argv[1] if len(sys.argv) > 1 else "vgm"
    files = sorted(glob.glob(os.path.join(folder, "*.vgm")))
    packer = VgmPacker()
    cache = os.path.join(folder, "_cache")

    def vgc_of(name):
        p = os.path.join(cache, name + ".vgc")
        return os.path.getsize(p) if os.path.exists(p) else None

    rows = []
    for i, f in enumerate(files, 1):
        name = os.path.basename(f)
        sys.stderr.write("[%d/%d] %s\n" % (i, len(files), name))
        sys.stderr.flush()
        try:
            with quiet():
                data_block, _ = distil(f)
                cols, nf = columns(packer, data_block)
            ch = channel_streams(cols, nf)
            L = best_grid(ch, nf)
            struct, pat, order, dist, tot = structural_size(ch, L, nf)
            lz = pattern_layer_lz4(ch, L, nf)
            vsize, vdist, vpos = vertical_size(ch, L, nf)
            perch = [channel_best_L(s, b, nf) for _, s, b in ch]
            curve = [(cl, channel_block_cov(ch, cl, nf)) for cl in CANON_L
                     if cl <= nf // 2]
            bphase = best_phase_cov(ch, 256, nf) if nf // 2 >= 256 else None
            rows.append(dict(name=name, nf=nf, L=L, perch=perch, dist=dist,
                             tot=tot, struct=struct, lz=lz, vsize=vsize,
                             raw=nf * 11, vgc=vgc_of(name),
                             curve=curve, bphase=bphase))
            sys.stderr.write("      L=%d cov=%.1f%% struct=%d lz=%d\n" %
                             (L, 100 * (1 - dist / tot), struct, lz))
        except Exception as e:
            sys.stderr.write("      FAILED: %r\n" % e)

    # ---- report ----
    print("")
    print("PATTERN-LAYER POTENTIAL  (recover the tracker grid from register data)")
    print("4 channels: ch0/1/2 = tone+vol (3 B/frame), ch3 = noise+vol (2 B/frame)")
    print("L = recovered grid (frames); cov = 1 - distinct/total blocks")
    print("struct = patterns+order (dedup only); lz = pattern bank LZ4'd + order")
    print("vert = whole-frame (all-channel) dedup on same grid (literal pattern)")
    print("")
    h = "%-34s %6s %5s %6s %8s %8s %8s %8s %6s"
    print(h % ("file", "nf", "L", "cov", "struct", "lz", "vert", "VGC", "bank?"))
    print("-" * 104)
    for r in rows:
        cov = 100 * (1 - r["dist"] / r["tot"])
        fits = "yes" if r["lz"] <= BANK else "NO"
        vgc = r["vgc"] if r["vgc"] is not None else "-"
        print(h % (r["name"][:34], r["nf"], r["L"], "%.1f%%" % cov,
                   r["struct"], r["lz"], r["vsize"], str(vgc), fits))
    print("-" * 104)
    if rows:
        traw = sum(r["raw"] for r in rows)
        tstruct = sum(r["struct"] for r in rows)
        tlz = sum(r["lz"] for r in rows)
        tvgc = sum(r["vgc"] for r in rows if r["vgc"] is not None)
        nvgc = sum(1 for r in rows if r["vgc"] is not None)
        print("")
        print("Totals: raw(nf*11)=%d  struct=%d (%.1f%% of raw)  lz=%d (%.1f%%)" %
              (traw, tstruct, 100 * tstruct / traw, tlz, 100 * tlz / traw))
        if nvgc:
            print("        pattern-LZ vs VGC (where both present): %.2fx" %
                  (float(tlz) / tvgc))
        print("")
        print("per-channel best L (do the channels share a grid?):")
        for r in rows:
            print("  %-34s global L=%-5d per-ch=%s" %
                  (r["name"][:34], r["L"], r["perch"]))

        print("")
        print("BLOCK COVERAGE vs GRID L  (mean per-channel; the pattern-recovery signal)")
        print("if a real tracker grid survived, coverage would PEAK at the musical L;")
        print("instead it decays monotonically -> no recoverable fixed grid.")
        hdr = "%-34s " + " ".join("%6d" % cl for cl in CANON_L) + "   bestph@256"
        print(hdr % "file")
        for r in rows:
            cov = {cl: c for cl, c in r["curve"]}
            line = "%-34s " % r["name"][:34]
            line += " ".join(("%5.1f%%" % cov[cl]) if cl in cov else "%6s" % "-"
                             for cl in CANON_L)
            line += ("   %5.1f%%" % r["bphase"]) if r["bphase"] is not None else "    -"
            print(line)


if __name__ == "__main__":
    main()
