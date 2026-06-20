#!/usr/bin/env python
# pack_vgi.py - pack a VGM into the ".vgi" incremental-decode format used by the
# 6502 prototype player (docs/compression-analysis.md sec 12.4).
#
# The format validates the single-bank, bounded-worst-case claim: 11 register
# columns, each compressed with a tiny byte-aligned LZSS (<=256 window, 8-bit
# offsets). The decoder yields exactly ONE byte per stream per frame, so a long
# match never lands in one frame - per-frame cost is bounded independently of
# match/run length. No RLE pre-pass here (keeps the decoder a single level; the
# flat-vs-RLE size cost is small, see sec 8.9 / P4f), except the noise column
# which keeps the 0x0f "skip" marker so the LFSR is not reset on unchanged
# frames.
#
# LZSS token stream (per column), decoded against a 256-byte ring of output:
#   cmd byte, bit7 = 0 : literal run, (cmd & 0x7f)+1 bytes follow verbatim (1..128)
#   cmd byte, bit7 = 1 : match, length (cmd & 0x7f)+2 (2..129), then 1 offset
#                        byte (1..255); copy length bytes from head-offset.
# Each column decodes to exactly nframes bytes, so no end marker is needed.
#
# File layout (little-endian), loaded as one blob at a fixed address:
#   +0  'V','G','I',1            magic + version
#   +4  nframes (16-bit)
#   +6  11 x stream offset (16-bit, relative to file start)
#   +28 the 11 LZSS streams, concatenated
#
# Usage: python beeb/pack_vgi.py <in.vgm> [-o out.vgi]

import os
import sys
import argparse
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from measure_proposal2 import distil, quiet            # noqa: E402
from vgmpacker import VgmPacker                          # noqa: E402

NOISE_COL = 6
SKIP = 0x0f
MIN_MATCH = 2
MAX_MATCH = 129          # (0x7f)+2
MAX_LIT = 128            # (0x7f)+1
MAX_OFF = 255


def noise_diff(col):
    """Replace unchanged consecutive noise frames with 0x0f (skip), so the
    player can avoid re-writing the noise register (which resets the LFSR)."""
    out = bytearray()
    for i, v in enumerate(col):
        if i and v == col[i - 1]:
            out.append(SKIP)
        else:
            out.append(v & 0xff)
    return out


def lzss_encode(data, max_chain=256):
    """Greedy LZSS in the format above. Self-verified by lzss_decode below."""
    n = len(data)
    out = bytearray()
    table = defaultdict(list)
    lits = bytearray()
    i = 0

    def flush():
        j = 0
        while j < len(lits):
            chunk = lits[j:j + MAX_LIT]
            out.append(len(chunk) - 1)          # bit7=0 literal run
            out.extend(chunk)
            j += MAX_LIT
        del lits[:]

    while i < n:
        best_len, best_off = 0, 0
        if i + MIN_MATCH <= n:
            key = bytes(data[i:i + 2])
            cand = table.get(key)
            if cand:
                maxl = min(MAX_MATCH, n - i)
                seen = 0
                for p in reversed(cand):
                    off = i - p
                    if off > MAX_OFF:
                        break
                    seen += 1
                    if seen > max_chain:
                        break
                    l = 0
                    while l < maxl and data[p + l] == data[i + l]:
                        l += 1
                    if l > best_len:
                        best_len, best_off = l, off
                        if l >= maxl:
                            break
        if best_len >= MIN_MATCH:
            flush()
            out.append(0x80 | (best_len - MIN_MATCH))
            out.append(best_off)
            end = i + best_len
            while i < end:
                if i + MIN_MATCH <= n:
                    table[bytes(data[i:i + 2])].append(i)
                i += 1
        else:
            lits.append(data[i])
            if i + MIN_MATCH <= n:
                table[bytes(data[i:i + 2])].append(i)
            i += 1
    flush()
    return bytes(out)


def lzss_decode(blob, nout):
    """Reference decoder (full output). The 6502 player uses a 256-byte ring;
    since offsets are <=255 that is bit-identical to indexing the full output."""
    out = bytearray()
    i = 0
    while len(out) < nout:
        cmd = blob[i]; i += 1
        if cmd < 0x80:
            cnt = cmd + 1
            out += blob[i:i + cnt]
            i += cnt
        else:
            length = (cmd & 0x7f) + MIN_MATCH
            off = blob[i]; i += 1
            src = len(out) - off
            for _ in range(length):
                out.append(out[src]); src += 1
    return bytes(out[:nout])


def lzss_decode_ring(blob, nout):
    """Incremental ring decoder - a faithful model of the 6502 routine, used as
    an extra self-check that the ring/state machine reproduces the data."""
    ring = bytearray(256)
    head = 0
    rem = 0
    is_match = False
    copy = 0
    out = bytearray()
    i = 0
    while len(out) < nout:
        if rem == 0:
            cmd = blob[i]; i += 1
            if cmd < 0x80:
                rem = (cmd & 0x7f) + 1
                is_match = False
            else:
                rem = (cmd & 0x7f) + MIN_MATCH
                off = blob[i]; i += 1
                copy = (head - off) & 0xff
                is_match = True
        if is_match:
            b = ring[copy]
            copy = (copy + 1) & 0xff
        else:
            b = blob[i]; i += 1
        ring[head] = b
        head = (head + 1) & 0xff
        rem -= 1
        out.append(b)
    return bytes(out)


def build_columns(vgm_path):
    packer = VgmPacker()
    with quiet():
        data_block, rate = distil(vgm_path)
        regs = packer.split_raw(data_block, True)
    nf = len(regs[0])
    cols = [bytearray(regs[c][:nf]) for c in range(11)]   # trim noise EOF
    cols[NOISE_COL] = noise_diff(cols[NOISE_COL])
    return cols, nf, rate


def pack(vgm_path, out_path):
    cols, nf, rate = build_columns(vgm_path)
    blobs = []
    for c in range(11):
        blob = lzss_encode(cols[c])
        assert lzss_decode(blob, nf) == bytes(cols[c]), "lzss round-trip failed col %d" % c
        assert lzss_decode_ring(blob, nf) == bytes(cols[c]), "ring round-trip failed col %d" % c
        blobs.append(blob)

    header = bytearray()
    header += b"VGI\x01"
    header += bytes((nf & 0xff, (nf >> 8) & 0xff))
    base = 4 + 2 + 11 * 2
    off = base
    offsets = []
    for b in blobs:
        offsets.append(off)
        off += len(b)
    for o in offsets:
        header += bytes((o & 0xff, (o >> 8) & 0xff))
    assert len(header) == base

    data = bytes(header) + b"".join(blobs)
    with open(out_path, "wb") as fh:
        fh.write(data)

    total = len(data)
    print("packed %s" % os.path.basename(vgm_path))
    print("  frames   : %d  (%d Hz, ~%.1f s)" % (nf, rate, nf / float(rate or 50)))
    print("  per-stream LZSS bytes: %s" % " ".join(str(len(b)) for b in blobs))
    print("  total .vgi: %d bytes (header %d + streams %d)" %
          (total, base, total - base))
    print("  -> %s" % out_path)
    return data, nf


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input")
    ap.add_argument("-o", "--output")
    args = ap.parse_args()
    out = args.output or (os.path.splitext(args.input)[0] + ".vgi")
    pack(args.input, out)


if __name__ == "__main__":
    main()
