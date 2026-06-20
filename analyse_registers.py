#!/usr/bin/env python
# analyse_registers.py
# Statistical dissection of SN76489 register data to guide the design of a
# successor to the VGC format. Operates on exactly the de-interleaved register
# columns the packer uses (VgmPacker.split_raw), across several axes:
#
#   1. Volatility       - how often each register changes frame-to-frame
#   2. Change taxonomy  - per frame: tones-only / volumes-only / both / idle
#                         (this is the post-mortem on why frame-LZ / P5 failed)
#   3. Run lengths      - "don't touch the chip" potential per register
#   4. Entropy floor    - order-0 bits/symbol per column vs joint per frame
#   5. Delta coding     - raw-value entropy vs frame-delta entropy (tones, vols)
#   6. LZ potential     - match coverage + offset distribution, column-major
#                         (VGC's axis) vs frame-major (P5's axis)
#
# Usage: python analyse_registers.py [vgm_folder]   (default: vgm)

import os
import sys
import glob
import math
from collections import Counter, defaultdict

from measure_proposal2 import distil, quiet
from vgmpacker import VgmPacker

# logical register layout from the 11 split_raw columns
TONE_COLS = [0, 1, 2, 3, 4, 5]          # t0lo t0hi t1lo t1hi t2lo t2hi
NOISE_COL = 6
VOL_COLS = [7, 8, 9, 10]                 # v0 v1 v2 v3
REG_NAMES = ["t0lo", "t0hi", "t1lo", "t1hi", "t2lo", "t2hi",
             "nois", "vol0", "vol1", "vol2", "vol3"]

OFFSET_BUCKETS = [(1, 1), (2, 15), (16, 63), (64, 255),
                  (256, 1023), (1024, 4095), (4096, 1 << 30)]
BUCKET_LABELS = ["1", "2-15", "16-63", "64-255", "256-1k", "1k-4k", "4k+"]


# ----------------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------------

def columns(packer, data_block):
    """11 register columns (command bits stripped); noise EOF trimmed."""
    regs = packer.split_raw(data_block, True)
    nf = len(regs[0])
    cols = [list(regs[c][:nf]) for c in range(11)]   # trim 0x08 EOF on col 6
    return cols, nf


def entropy(seq):
    if not seq:
        return 0.0
    c = Counter(seq)
    n = len(seq)
    return -sum((v / n) * math.log2(v / n) for v in c.values())


def change_count(col):
    """number of frame-to-frame changes in a register column."""
    return sum(1 for i in range(1, len(col)) if col[i] != col[i - 1])


def tone_value(cols, ch, i):
    lo = cols[ch * 2][i] & 15
    hi = cols[ch * 2 + 1][i] & 63
    return lo | (hi << 4)            # 10-bit period


def lz_scan(items, window=None, min_match=1, khash=1, max_chain=64):
    """Greedy LZ over a list of hashable items. Returns (n, matched, hist)
    where hist maps offset-bucket -> matched item count. Used for both
    frame-major (items=frame bytes, min_match=1) and column-major byte axes."""
    n = len(items)
    if n == 0:
        return 0, 0, {b: 0 for b in OFFSET_BUCKETS}
    if window is None or window > n:
        window = n
    table = defaultdict(list)
    hist = {b: 0 for b in OFFSET_BUCKETS}
    matched = 0
    i = 0

    def key(p):
        return items[p] if khash == 1 else tuple(items[p:p + khash])

    while i < n:
        best_len = 0
        best_off = 0
        if i + khash <= n:
            cand = table.get(key(i))
            if cand:
                lo = i - window
                sc = 0
                maxl = n - i
                for p in reversed(cand):
                    if p < lo:
                        break
                    sc += 1
                    if sc > max_chain:
                        break
                    l = 0
                    while l < maxl and items[p + l] == items[i + l]:
                        l += 1
                    if l > best_len:
                        best_len = l
                        best_off = i - p
                        if best_len >= maxl:
                            break
        if best_len >= min_match:
            for bi, (a, b) in enumerate(OFFSET_BUCKETS):
                if a <= best_off <= b:
                    hist[(a, b)] += best_len
                    break
            matched += best_len
            end = i + best_len
            j = i
            while j < end and j + khash <= n:
                table[key(j)].append(j)
                j += 1
            i = end
        else:
            if i + khash <= n:
                table[key(i)].append(i)
            i += 1
    return n, matched, hist


# ----------------------------------------------------------------------------
# per-file analysis
# ----------------------------------------------------------------------------

def analyse(packer, data_block):
    cols, nf = columns(packer, data_block)
    r = {"nf": nf}

    # 1. volatility (store raw change counts; pool across corpus in main)
    r["changes"] = [change_count(cols[c]) for c in range(11)]

    # 2. change taxonomy per frame
    tone_idx = TONE_COLS + [NOISE_COL]
    both = tonly = vonly = idle = 0
    for i in range(1, nf):
        tc = any(cols[c][i] != cols[c][i - 1] for c in tone_idx)
        vc = any(cols[c][i] != cols[c][i - 1] for c in VOL_COLS)
        if tc and vc:
            both += 1
        elif tc:
            tonly += 1
        elif vc:
            vonly += 1
        else:
            idle += 1
    tot = max(1, nf - 1)
    r["taxonomy"] = (both / tot, tonly / tot, vonly / tot, idle / tot)

    # 3. run lengths derived from change counts in main (pooled)

    # 4. entropy floor: per-column order-0 vs joint-per-frame
    col_bits = sum(entropy(cols[c]) for c in range(11)) * nf
    frames9 = [bytes((cols[0][i], cols[1][i], cols[2][i], cols[3][i],
                      cols[4][i], cols[5][i], cols[6][i],
                      (cols[7][i] & 15) | ((cols[8][i] & 15) << 4),
                      (cols[9][i] & 15) | ((cols[10][i] & 15) << 4)))
               for i in range(nf)]
    joint_bits = entropy(frames9) * nf
    r["col_floor"] = col_bits / 8.0
    r["joint_floor"] = joint_bits / 8.0
    r["distinct_frames"] = len(set(frames9))

    # 5. delta vs raw entropy for tone periods and volumes
    def col_pair_entropy(values):
        raw = entropy(values)
        deltas = [(values[i] - values[i - 1]) for i in range(1, len(values))]
        return raw, entropy(deltas)
    tvals = [tone_value(cols, ch, i) for ch in range(3) for i in range(nf)]
    # per-channel to keep deltas meaningful, then average weighted
    traw = tdel = 0.0
    for ch in range(3):
        v = [tone_value(cols, ch, i) for i in range(nf)]
        a, b = col_pair_entropy(v)
        traw += a
        tdel += b
    r["tone_raw_H"], r["tone_delta_H"] = traw / 3, tdel / 3
    vraw = vdel = 0.0
    for c in VOL_COLS:
        a, b = col_pair_entropy(cols[c])
        vraw += a
        vdel += b
    r["vol_raw_H"], r["vol_delta_H"] = vraw / 4, vdel / 4

    # 6. LZ potential: column-major (8 logical byte streams) vs frame-major
    logical = [
        bytes(b for i in range(nf) for b in (cols[0][i], cols[1][i])),
        bytes(b for i in range(nf) for b in (cols[2][i], cols[3][i])),
        bytes(b for i in range(nf) for b in (cols[4][i], cols[5][i])),
        bytes(cols[6]),
        bytes(cols[7]), bytes(cols[8]), bytes(cols[9]), bytes(cols[10]),
    ]
    col_n = col_m = 0
    for s in logical:
        n, m, _ = lz_scan(list(s), window=None, min_match=2, khash=2)
        col_n += n
        col_m += m
    r["col_cov"] = col_m / max(1, col_n)

    fn, fm, fhist = lz_scan(frames9, window=None, min_match=1, khash=1)
    r["frame_cov"] = fm / max(1, fn)
    r["frame_hist"] = fhist
    r["frame_matched"] = fm
    return r


# ----------------------------------------------------------------------------
# main
# ----------------------------------------------------------------------------

def main():
    folder = sys.argv[1] if len(sys.argv) > 1 else "vgm"
    files = sorted(glob.glob(os.path.join(folder, "*.vgm")))
    packer = VgmPacker()

    results = []
    for i, f in enumerate(files, 1):
        name = os.path.basename(f)
        sys.stderr.write("[%d/%d] %s\n" % (i, len(files), name))
        sys.stderr.flush()
        try:
            with quiet():
                data_block, _ = distil(f)
                r = analyse(packer, data_block)
            r["name"] = name
            results.append(r)
        except Exception as e:
            sys.stderr.write("    FAILED: %r\n" % e)

    if not results:
        print("no files analysed")
        return

    tf = sum(r["nf"] for r in results)   # total frames (weighting)

    def wavg(key, idx=None):
        if idx is None:
            return sum(r[key] * r["nf"] for r in results) / tf
        return sum(r[key][idx] * r["nf"] for r in results) / tf

    # pooled per-register change counts and frame totals (avoids static-file skew)
    tot_changes = [sum(r["changes"][c] for r in results) for c in range(11)]
    tot_intervals = sum(r["nf"] - 1 for r in results)
    tot_runs = [sum(r["changes"][c] + 1 for r in results) for c in range(11)]

    print("\n" + "=" * 78)
    print("1. VOLATILITY  - %% of frames where each register changes (corpus, pooled)")
    print("=" * 78)
    print("  " + "  ".join("%5s" % n for n in REG_NAMES))
    print("  " + "  ".join("%4.0f%%" % (100 * tot_changes[c] / tot_intervals) for c in range(11)))
    print("  noise is near-static; volumes are the most volatile -> different treatment.")

    print("\n" + "=" * 78)
    print("2. FRAME CHANGE TAXONOMY  - what changes each frame (the P5 post-mortem)")
    print("=" * 78)
    print("  %-34s %7s %7s %7s %7s" % ("file", "both", "tone-o", "vol-o", "idle"))
    for r in results:
        b, t, v, idl = r["taxonomy"]
        print("  %-34s %6.1f%% %6.1f%% %6.1f%% %6.1f%%" %
              (r["name"][:34], b * 100, t * 100, v * 100, idl * 100))
    print("  " + "-" * 64)
    print("  %-34s %6.1f%% %6.1f%% %6.1f%% %6.1f%%" %
          ("CORPUS", wavg("taxonomy", 0) * 100, wavg("taxonomy", 1) * 100,
           wavg("taxonomy", 2) * 100, wavg("taxonomy", 3) * 100))
    print("  'vol-o' = frames whose ONLY change is volume: these defeat whole-frame")
    print("  matching (P5) yet are trivial for per-column LZ (VGC).")

    print("\n" + "=" * 78)
    print("3. RUN LENGTHS  - mean consecutive-equal run per register, pooled (chip-write avoidance)")
    print("=" * 78)
    print("  " + "  ".join("%5s" % n for n in REG_NAMES))
    print("  " + "  ".join("%5.1f" % (tf / tot_runs[c]) for c in range(11)))
    print("  high = long holds (cheap to skip / RLE); low = needs per-frame coding.")

    print("\n" + "=" * 78)
    print("4. ENTROPY FLOOR  - order-0 lower bound (bytes), corpus totals")
    print("=" * 78)
    raw = tf * 11
    cf = sum(r["col_floor"] for r in results)
    jf = sum(r["joint_floor"] for r in results)
    df = sum(r["distinct_frames"] for r in results)
    print("  raw (11 B/frame)                 : %9d" % raw)
    print("  per-column order-0 floor         : %9d  (%.1f%% of raw)" % (cf, 100 * cf / raw))
    print("  joint per-frame order-0 floor    : %9d  (%.1f%% of raw)" % (jf, 100 * jf / raw))
    print("  distinct 9-byte frames           : %9d  of %d total frames (%.1f%%)" %
          (df, tf, 100 * df / tf))
    print("  joint << sum-of-columns => registers are strongly CORRELATED (joint entropy is")
    print("  always <= summed marginals; the size of the gap here shows how much). But the")
    print("  joint alphabet is huge (%.0f%% distinct frames), so that correlation is only" % (100 * df / tf))
    print("  realisable via back-references/dictionary (sec 6), not a static per-frame table.")
    print("  Achievable-cheap floor is the per-column %.0f%% (a per-column entropy coder)." % (100 * cf / raw))

    print("\n" + "=" * 78)
    print("5. DELTA CODING  - order-0 entropy (bits/symbol): raw value vs frame delta")
    print("=" * 78)
    print("  tone period : raw %4.2f  delta %4.2f  bits  (%s)" %
          (wavg("tone_raw_H"), wavg("tone_delta_H"),
           "delta helps" if wavg("tone_delta_H") < wavg("tone_raw_H") else "delta worse"))
    print("  volume      : raw %4.2f  delta %4.2f  bits  (%s)" %
          (wavg("vol_raw_H"), wavg("vol_delta_H"),
           "delta helps" if wavg("vol_delta_H") < wavg("vol_raw_H") else "delta worse"))

    print("\n" + "=" * 78)
    print("6. LZ POTENTIAL  - match coverage and where the matches come from")
    print("=" * 78)
    print("  coverage = fraction of data expressible as a back-reference (greedy, unbounded)")
    print("  column-major (8 logical streams, min-match 2 B): %5.1f%%" %
          (100 * wavg("col_cov")))
    print("  frame-major  (whole 9-B frame,    min-match 1)  : %5.1f%%" %
          (100 * wavg("frame_cov")))
    print("  -> the gap is exactly the per-column advantage VGC exploits and P5 cannot.")
    print("")
    print("  frame-major match OFFSET distribution (%% of matched frames, by distance):")
    agg = {b: 0 for b in OFFSET_BUCKETS}
    tm = 0
    for r in results:
        for b in OFFSET_BUCKETS:
            agg[b] += r["frame_hist"][b]
        tm += r["frame_matched"]
    print("    " + "  ".join("%7s" % l for l in BUCKET_LABELS))
    print("    " + "  ".join("%6.1f%%" % (100 * agg[b] / max(1, tm)) for b in OFFSET_BUCKETS))
    print("  near offsets (1) = held frames (RLE); far offsets = phrase repetition.")
    print("  the spread tells you the window size a successor's offset coder must reach.")


if __name__ == "__main__":
    main()
