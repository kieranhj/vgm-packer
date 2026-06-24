#!/usr/bin/env python
# measure_proposal2.py
# Measures "Proposal 2" (per-register RLE streams, no LZ) against the existing
# VGC format (8x LZ4) and VGC+Huffman, across a folder of VGM files.
# Reuses VgmPacker's own rle/rle2/diff/combine_registers so Proposal 2 is
# computed on exactly the same distilled+de-interleaved data that VGC uses.

import os
import sys
import glob
import subprocess
import contextlib
import traceback

from modules.vgmparser import VgmStream
from vgmpacker import VgmPacker

DEVNULL = open(os.devnull, "w")

# ZX0 v2.2 compressor (the doc's "zx02.exe" is ZX0 v2.2). Resolve from the
# ZX0_BIN env var, else the first existing candidate. On a fresh clone, build it
# with: git clone https://github.com/einar-saukas/ZX0 && cd ZX0/src &&
#       cc -O2 -o zx0 zx0.c optimize.c compress.c memory.c
# Flags are identical to the original Windows binary: -f <in> <out>.
def _find_zx0():
    cand = [
        os.environ.get("ZX0_BIN"),
        os.path.join("..", "ZX0", "src", "zx0"),
        os.path.join("..", "fdload_dfs", "bin", "zx02.exe"),
        "zx0",
    ]
    for c in cand:
        if c and (os.path.exists(c) or os.path.dirname(c) == ""):
            return c
    return cand[0]

ZX02 = _find_zx0()

@contextlib.contextmanager
def quiet():
    # the packer/parser are extremely chatty; hide their stdout
    old = sys.stdout
    sys.stdout = DEVNULL
    try:
        yield
    finally:
        sys.stdout = old

def distil(vgm_path):
    """Replicate VgmPacker.process() up to the raw register block (header trimmed)."""
    vgm = VgmStream(vgm_path)
    data_block = vgm.as_binary()

    header_size = data_block[0]
    play_rate   = data_block[1]
    data_offset = 0
    if header_size == 5 and play_rate == 50:
        data_offset = header_size + 1
        data_offset += data_block[data_offset] + 1
        data_offset += data_block[data_offset] + 1
    # else: process() prints "No header." and leaves data_offset = 0

    return data_block[data_offset:], play_rate

def build_streams(packer, data_block, do_rle):
    """The 8 de-interleaved per-register streams, optionally RLE-encoded.
    Noise (index 3) always gets the diff so the 0x0f skip markers exist."""
    registers = packer.split_raw(data_block, True)
    if do_rle:
        return [
            packer.rle2(packer.combine_registers(registers, [0, 1])),
            packer.rle2(packer.combine_registers(registers, [2, 3])),
            packer.rle2(packer.combine_registers(registers, [4, 5])),
            packer.rle(packer.diff(registers[6], 0x0f)),
            packer.rle(registers[7]),
            packer.rle(registers[8]),
            packer.rle(registers[9]),
            packer.rle(registers[10]),
        ]
    return [
        packer.combine_registers(registers, [0, 1]),
        packer.combine_registers(registers, [2, 3]),
        packer.combine_registers(registers, [4, 5]),
        packer.diff(registers[6], 0x0f),
        registers[7], registers[8], registers[9], registers[10],
    ]

def proposal2_size(streams):
    """Sum of the 8 per-register RLE streams + minimal framing (8 lengths)."""
    return sum(len(s) for s in streams) + 2 * len(streams)

def fresh(cache, src):
    """True if a cached artifact exists and is newer than its source."""
    return os.path.exists(cache) and os.path.getmtime(cache) >= os.path.getmtime(src)

def zx02_size(streams, blob_path, src):
    """ZX02 the concatenation of the streams; cache both the blob and the .zx02."""
    out = blob_path + ".zx02"
    if not (fresh(blob_path, src) and fresh(out, src)):
        blob = bytearray()
        for s in streams:
            blob += s
        with open(blob_path, "wb") as fh:
            fh.write(blob)
        if os.path.exists(out):
            os.remove(out)
        subprocess.run([ZX02, "-f", blob_path, out],
                       stdout=DEVNULL, stderr=DEVNULL, check=True)
    return os.path.getsize(out)

def vgc_size(packer, src, cache, use_huffman):
    """Pack to a cached .vgc path; reuse if newer than the source VGM."""
    if not fresh(cache, src):
        packer.process(src, cache, buffersize=255, use_huffman=use_huffman)
    return os.path.getsize(cache)

def main():
    folder = sys.argv[1] if len(sys.argv) > 1 else "vgm"
    files = sorted(glob.glob(os.path.join(folder, "*.vgm")))

    packer = VgmPacker()
    cache = os.path.join(folder, "_cache")
    if not os.path.isdir(cache):
        os.makedirs(cache)

    def cpath(name, ext):
        return os.path.join(cache, name + ext)

    def progress(msg):
        sys.stderr.write(msg + "\n")
        sys.stderr.flush()

    def safe(label, fn):
        """Run one metric; return its value or None (so one broken column
        doesn't void the whole row). Logs the failure reason once."""
        try:
            with quiet():
                return fn()
        except Exception as e:
            progress("      %-4s FAILED: %s" % (label, repr(e)))
            return None

    rows = []
    for i, f in enumerate(files, 1):
        name = os.path.basename(f)
        orig = os.path.getsize(f)
        progress("[%d/%d] %s" % (i, len(files), name))
        packer = VgmPacker()  # fresh per file: avoid any cross-file state leak

        distilled = safe("dist", lambda: distil(f))
        if distilled is None:
            rows.append((name, "-", orig, None, None, None, None, None, None))
            continue
        data_block, rate = distilled
        raw = len(data_block)

        rle_streams  = safe("rle",  lambda: build_streams(packer, data_block, True))
        flat_streams = safe("flat", lambda: build_streams(packer, data_block, False))

        p2  = proposal2_size(rle_streams) if rle_streams is not None else None
        p4  = safe("p4",  lambda: zx02_size(rle_streams,  cpath(name, ".p4.bin"),  f)) if rle_streams  is not None else None
        p4f = safe("p4f", lambda: zx02_size(flat_streams, cpath(name, ".p4f.bin"), f)) if flat_streams is not None else None
        vgc = safe("vgc", lambda: vgc_size(packer, f, cpath(name, ".vgc"),  False))
        vgh = safe("vgch", lambda: vgc_size(packer, f, cpath(name, ".vgch"), True))

        rows.append((name, rate, orig, raw, p2, p4, p4f, vgc, vgh))
        progress("      raw=%s p2=%s p4=%s p4f=%s vgc=%s vgc+h=%s"
                 % (raw, p2, p4, p4f, vgc, vgh))

    # ---- report ----
    print("")
    print("P2    = de-interleave + RLE, no LZ (8 cursors, ~constant-time decode)")
    print("P4    = zx02(de-interleave + RLE)   decompress-once, then play P2 cursors")
    print("P4f   = zx02(de-interleave only)    decompress-once, flat frame arrays")
    print("VGC   = 8x LZ4 (current);  VGC+H = + Huffman")
    print("raw   = distilled 11-byte/frame stream (what everything compresses from)")
    print("")
    hdr = "%-40s %4s %9s %8s %8s %8s %8s %8s %8s"
    print(hdr % ("file", "Hz", "raw", "P2", "P4", "P4f", "VGC", "VGC+H", "P2/VGC"))
    print("-" * 118)

    cell = lambda v: ("%8d" % v) if v is not None else "%8s" % "-"

    # per-column totals summed only over files where BOTH that column and
    # the VGC baseline are present, so the ratio summary is apples-to-apples.
    keys = ["raw", "p2", "p4", "p4f", "vgc", "vgh"]
    t = {k: 0 for k in keys}
    n = {k: 0 for k in keys}
    for r in rows:
        name = r[0][:40]
        _, rate, orig, raw, p2, p4, p4f, vgc, vgh = r
        vals = dict(raw=raw, p2=p2, p4=p4, p4f=p4f, vgc=vgc, vgh=vgh)
        for k in keys:
            if vals[k] is not None:
                t[k] += vals[k]; n[k] += 1
        ratio = ("%5.2fx" % (float(p2) / vgc)) if (p2 and vgc) else "   -  "
        print("%-40s %4s %9s %s %s %s %s %s   %s" %
              (name, rate, (raw if raw is not None else "-"),
               cell(p2), cell(p4), cell(p4f), cell(vgc), cell(vgh), ratio))

    print("-" * 118)
    print("processed: " + ", ".join("%s=%d/%d" % (k, n[k], len(rows)) for k in keys))

    # apples-to-apples summary over the rows where P2/P4/P4f/VGC are all present
    comparable = [r for r in rows if all(x is not None for x in (r[4], r[5], r[6], r[7]))]
    if comparable:
        c = {k: 0 for k in ["raw", "p2", "p4", "p4f", "vgc"]}
        for r in comparable:
            _, _, _, raw, p2, p4, p4f, vgc, _ = r
            c["raw"] += raw; c["p2"] += p2; c["p4"] += p4; c["p4f"] += p4f; c["vgc"] += vgc
        print("")
        print("Summary over %d comparable files (raw=%d bytes):" % (len(comparable), c["raw"]))
        print("                 as %% of raw      vs VGC")
        pct = lambda k: 100.0 * c[k] / c["raw"]
        vsv = lambda k: float(c[k]) / c["vgc"]
        print("P2   (RLE only)   : %5.1f%%          %5.2fx" % (pct("p2"),  vsv("p2")))
        print("P4   (zx02+RLE)   : %5.1f%%          %5.2fx" % (pct("p4"),  vsv("p4")))
        print("P4f  (zx02 flat)  : %5.1f%%          %5.2fx" % (pct("p4f"), vsv("p4f")))
        print("VGC  (8x LZ4)     : %5.1f%%          1.00x" % pct("vgc"))

if __name__ == "__main__":
    main()
