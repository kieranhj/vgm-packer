#!/usr/bin/env python
# framelz.py
# "Proposal 5" - synchronised frame-LZ for SN76489 register music.
#
# The 8 logical register streams are advanced exactly one value per frame and,
# crucially, REPEAT TOGETHER: a musical phrase recurring means every register
# replays at once. VGC ignores this and runs 8 independent LZ contexts, each
# re-discovering the same repetition and each needing its own ring buffer.
#
# Frame-LZ exploits the sync directly. The data is laid out frame-major as
# fixed-width W-byte records, and a SINGLE LZ pass expresses literals, match
# lengths and offsets in *frame units*. Decode needs one context: one offset,
# one match countdown, one ring of the last `window` frames (window * W bytes).
#
# Two properties matter for 6502 playback:
#   * Per-frame cost is CONSTANT - exactly one frame (W byte copies, either from
#     the literal pointer or from ring[head-offset]) is consumed per frame, so a
#     long match never blows the frame budget (the key VGC failure mode).
#   * Held frames fall out for free: a frame identical to the previous one is
#     just a match with offset 1, so overlap-matches subsume whole-frame RLE.
#
# Byte format (LZ4-derived, but every count/offset is a FRAME count):
#   token   : hi nibble = literal frame count, lo nibble = match frame count
#             (lo == 0 means "no match", e.g. the trailing literal run)
#   [litext]: if literal nibble == 15, 255-chained extension bytes
#   literals: literal_count * W raw record bytes
#   offset  : 1 byte (window <= 256) or 2 bytes, stored as (offset_frames - 1)
#   [mext]  : if match nibble == 15, 255-chained extension bytes
#
# This module is import-only (no chip/VGM knowledge); the caller supplies the
# frame records. frame_lz() asserts its own round-trip via frame_unlz().

from collections import defaultdict

# how many hash-chain candidates to test per position (greedy parser, bounded
# for speed; raising it improves ratio slightly at encode-time cost only)
MAX_CHAIN = 64


def _emit_ext(out, value):
    # value >= 15; the 15 is already in the nibble. Emit (value-15) 255-chained.
    rem = value - 15
    while rem >= 255:
        out.append(255)
        rem -= 255
    out.append(rem)


def frame_lz(records, window=None, verify=True):
    """Compress a list of equal-length frame records (each a bytes of width W).
    `window` caps the back-reference distance in frames (None = unlimited).
    Returns the compressed bytearray. Asserts a full round-trip when verify."""
    n = len(records)
    if n == 0:
        return bytearray()
    W = len(records[0])

    if window is None or window > n:
        window = n
    offbytes = 1 if window <= 256 else 2

    table = defaultdict(list)   # frame bytes -> ascending list of positions
    out = bytearray()
    litbuf = []                 # pending literal record bytes

    def find_match(i):
        cand = table.get(records[i])
        if not cand:
            return 0, 0
        best_len = 0
        best_off = 0
        lo = i - window
        maxl = n - i
        scanned = 0
        for p in reversed(cand):            # most recent first
            if p < lo:
                break
            scanned += 1
            if scanned > MAX_CHAIN:
                break
            l = 0
            # overlap is legal: source index (p+l) stays < dest index (i+l)
            while l < maxl and records[p + l] == records[i + l]:
                l += 1
            if l > best_len:
                best_len = l
                best_off = i - p
                if best_len >= maxl:        # can't beat reaching the end
                    break
        return best_len, best_off

    def flush(mlen, off):
        lit = len(litbuf)
        token = (min(lit, 15) << 4) | (min(mlen, 15) if mlen > 0 else 0)
        out.append(token)
        if lit >= 15:
            _emit_ext(out, lit)
        for r in litbuf:
            out.extend(r)
        if mlen > 0:
            d = off - 1
            out.append(d & 255)
            if offbytes == 2:
                out.append((d >> 8) & 255)
            if mlen >= 15:
                _emit_ext(out, mlen)
        litbuf.clear()

    i = 0
    while i < n:
        mlen, off = find_match(i)
        if mlen >= 1:
            flush(mlen, off)
            for k in range(i, i + mlen):
                table[records[k]].append(k)
            i += mlen
        else:
            litbuf.append(records[i])
            table[records[i]].append(i)
            i += 1
    if litbuf:
        flush(0, 0)

    if verify:
        rebuilt = frame_unlz(out, W, offbytes)
        assert len(rebuilt) == n, \
            "frame-LZ round-trip length %d != %d" % (len(rebuilt), n)
        assert rebuilt == records, "frame-LZ round-trip mismatch"

    return out


def frame_unlz(data, W, offbytes):
    """Decode a frame_lz() stream back to the list of W-byte frame records.
    This mirrors exactly what the 6502 player would do, one frame at a time."""
    out = []
    idx = 0
    end = len(data)

    def read_ext(v):
        nonlocal idx
        while True:
            b = data[idx]
            idx += 1
            v += b
            if b != 255:
                return v

    while idx < end:
        token = data[idx]
        idx += 1
        lit = token >> 4
        m = token & 15
        if lit == 15:
            lit = read_ext(lit)
        for _ in range(lit):
            out.append(bytes(data[idx:idx + W]))
            idx += W
        if m != 0:
            d = data[idx]
            idx += 1
            if offbytes == 2:
                d |= data[idx] << 8
                idx += 1
            off = d + 1
            if m == 15:
                m = read_ext(m)
            src = len(out) - off
            for k in range(m):
                out.append(out[src + k])     # overlap-safe: src+k < len at access
    return out
