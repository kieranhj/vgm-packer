#!/usr/bin/env python
# measure_v2.py - corpus size comparison of the VGI improvements that preserve
# the bounded per-frame decode:
#   v1       current greedy LZSS (the shipped .vgi)
#   opt      v1 format but OPTIMAL parse - decoder is byte-for-byte identical,
#            so this is a pure free win (same runtime)
#   v2       new format: offset-1 RUN token + extended length + optimal parse
#   .vgc     existing VGC (LZ4) for reference (from beeb/_cache)
import os
import sys
import glob
import io
import contextlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pack_vgi as v1
import pack_vgi2 as v2
import explore_vgi as ex

CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_cache")
FRAME = 28          # .vgi/.vgi2 header (magic+nframes+11 offsets)

V1FMT = dict(name="opt", off_bytes=1, len_bits=7, has_run=False,
             min_match=2, max_off=255, ext=False)


def opt_only_size(cols, nf):
    total = FRAME
    for c in cols:
        data = bytes(c)
        ops = ex.optimal(data, V1FMT)
        blob = ex.serialize(data, ops, V1FMT)
        assert ex.decode(blob, len(data), V1FMT) == data
        total += len(blob)
    return total


def main():
    files = sorted(glob.glob("../vgm/*.vgm"))
    rows = []
    for f in files:
        name = os.path.basename(f)
        with contextlib.redirect_stdout(io.StringIO()):
            cols, nf, rate = v1.build_columns(f)
            d1, _ = v1.pack(f, "/tmp/_m.vgi")
            d2, _ = v2.pack(f, None)
        opt = opt_only_size([bytes(c) for c in cols], nf)
        vgcp = os.path.join(CACHE, name.replace(" ", "_") + ".vgc")
        # cached_vgc used a regex-sanitised name; fall back to glob if needed
        if not os.path.exists(vgcp):
            import re
            safe = re.sub(r'[^A-Za-z0-9._-]', '_', name) + ".vgc"
            vgcp = os.path.join(CACHE, safe)
        vgc = os.path.getsize(vgcp) if os.path.exists(vgcp) else None
        rows.append((name, nf, len(d1), opt, len(d2), vgc))
        sys.stderr.write("  %-34s v1=%d opt=%d v2=%d vgc=%s\n" %
                         (name[:34], len(d1), opt, len(d2), vgc))

    print("\n%-32s %6s %7s %7s %7s %7s" % ("tune", "frames", "v1", "opt", "v2", "vgc"))
    print("-" * 80)
    t = [0, 0, 0, 0]
    for name, nf, a, o, b, g in rows:
        print("%-32s %6d %7d %7d %7d %7s" %
              (name[:32], nf, a, o, b, g if g else "-"))
        t[0] += a; t[1] += o; t[2] += b
        if g:
            t[3] += g
    print("-" * 80)
    print("%-32s %6s %7d %7d %7d %7d" % ("TOTAL", "", t[0], t[1], t[2], t[3]))
    print("\nratios vs current v1:")
    print("  opt (free, same decoder): %.3fx  (%.1f%% smaller)" % (t[1] / t[0], 100 * (1 - t[1] / t[0])))
    print("  v2 (run+extlen+optimal) : %.3fx  (%.1f%% smaller)" % (t[2] / t[0], 100 * (1 - t[2] / t[0])))
    print("ratios vs .vgc:")
    print("  v1 %.2fx   opt %.2fx   v2 %.2fx" % (t[0] / t[3], t[1] / t[3], t[2] / t[3]))


if __name__ == "__main__":
    main()
