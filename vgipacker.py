#!/usr/bin/env python
# ******************************************************************
# AI-GENERATED CODE
# ------------------------------------------------------------------
# vgipacker.py - pack an SN76489 PSG VGM into the ".vgi" incremental-
# decode format. Generated with the assistance of an AI model:
# Claude Opus 4.8 (claude-opus-4-8).
# ******************************************************************
#
# WHAT THIS IS
# ------------
# vgipacker.py is a sibling of vgmpacker.py. Both pack the same SN76489
# register data; they differ in what the 6502 decoder has to do per frame:
#
#   .vgc (vgmpacker.py) - RLE + LZ4 per stream. Smallest on disc, but the
#       decoder cost SPIKES: most frames just decrement an RLE counter (cheap),
#       occasional frames refill several LZ tokens at once (expensive).
#   .vgi (this script)  - a tiny byte-aligned LZSS per register column, with
#       NO RLE pre-pass, decoded ONE value per stream per frame. A long match
#       never lands in a single frame, so the per-frame cost is bounded
#       *independently of match/run length* - low, flat and predictable, which
#       is what a timing-critical (raster-budgeted) demo needs.
#
# .vgi is a touch larger than .vgc (~1.4x on the test corpus) - the price of
# dropping the RLE layer that causes the spikes. See docs/vgi-format.md for the
# full rationale, the corpus size comparison and the playback-cost study.
#
# Only SN76489 PSG VGM files are supported (same constraint as vgmpacker.py).
#
# FORMAT (default "v2"; --v1 the original greedy format, --v3 indexed periods)
# ---------------------------------------------------------------------------
# 11 register columns (one per SN76489 register), each compressed independently
# with a byte-aligned LZSS over a 256-byte ring window (8-bit offsets). The
# noise column keeps the 0x0f "skip" marker so the LFSR is not reset on
# unchanged frames. Each column decodes to exactly nframes bytes (no end
# marker). File layout (little-endian, loaded as one blob):
#
#   +0   'V','G','I',ver        magic + version (1 or 2; v3 is below)
#   +4   nframes (16-bit)
#   +6   11 x stream offset (16-bit, relative to file start)
#   +28  the 11 LZSS streams, concatenated
#
# v1 token stream (per column):
#   0LLLLLLL            literal run, L+1 bytes follow                  (1..128)
#   1LLLLLLL off        match, len (L)+2 (2..129), then 1 offset byte  (1..255)
#
# v2 token stream (per column) - ~8% smaller for an unchanged decode profile:
#   0LLLLLLL            literal run, L+1 bytes follow                  (1..128)
#   10LLLLLL [E]        RUN (offset 1 / repeat last byte):
#                         LLLLLL<63 -> len = LLLLLL+2 (2..64)
#                         LLLLLL==63 -> len = E (a full byte, 65..255)
#                       no offset byte
#   11LLLLLL [E] off    MATCH: length as above, then one offset byte (1..255)
# Length is capped at 255 so the player's per-stream run counter stays 8-bit
# and a token start reads at most cmd+ext+offset = 3 bytes - that is what keeps
# the worst case bounded. v2 uses an optimal (DP) parse.
#
# v3 (--v3) keeps the v2 tokens and changes the COLUMNS: the tone lo/hi pair
# becomes ONE column of indices into a table of the periods the tune uses, so
# there are 8 columns and not 11. It is 23-29% smaller AND cheaper to decode
# (three fewer per-stream state machines, three fewer ring pages), because the
# SN's 4+6 period split puts entropy in both halves where the AY's 8+4 leaves
# one half nearly static - see docs/vgi-format.md for the measurement that
# found it. Its layout:
#
#   +0   'V','G','I',3
#   +4   nframes (16-bit)
#   +6   8 x stream offset (16-bit, relative to file start)
#   +22  table mode (byte: 1 = one shared table, 3 = one per channel)
#   +23  3 x table offset (16-bit); +29 3 x entry count (byte, 0 = 256)
#   +32  the tables (N low nibbles then N high 6-bit values), then the streams
#
# Every format is SELF-VERIFIED here: each encoded column is decoded by two
# independent reference decoders (a plain one and a 256-byte-ring model of the
# 6502 routine) and asserted to round-trip exactly. v3 additionally expands its
# indices back through the tables and asserts the 11 SN76489 register columns
# come out byte-for-byte as v2 would have written them.
#
# Usage:
#   python vgipacker.py <in.vgm> [-o out.vgi] [--v1 | --v3]
#
# Run from the repo root (it imports the modules/ package, like vgmpacker.py).

import os
import sys
import argparse
import contextlib
from collections import defaultdict

from modules.vgmparser import VgmStream
from vgmpacker import VgmPacker

NOISE_COL = 6
SKIP = 0x0f
MIN_MATCH = 2
MAX_MATCH = 129          # (0x7f)+2
MAX_LIT = 128            # (0x7f)+1
MAX_OFF = 255


@contextlib.contextmanager
def _quiet():
    """The parser/packer are chatty on stdout; hide it while we reuse them."""
    old = sys.stdout
    try:
        with open(os.devnull, "w") as devnull:
            sys.stdout = devnull
            yield
    finally:
        sys.stdout = old


def _distil(vgm_path):
    """Run the VGM through the standard parser and trim the as_binary() header,
    yielding the raw interleaved register block + the play rate. This mirrors
    the front half of VgmPacker.process() (see vgmpacker.py)."""
    vgm = VgmStream(vgm_path)
    data_block = vgm.as_binary()

    header_size = data_block[0]
    play_rate = data_block[1]
    data_offset = 0
    if header_size == 5 and play_rate == 50:
        data_offset = header_size + 1
        data_offset += data_block[data_offset] + 1     # skip title
        data_offset += data_block[data_offset] + 1     # skip author
    # else: no recognised header, leave data_offset = 0 (matches process())
    return data_block[data_offset:], play_rate


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


def build_columns(vgm_path):
    """De-interleave the VGM into 11 register columns, applying the noise-skip
    diff to the noise column. Returns (columns, nframes, play_rate)."""
    with _quiet():
        packer = VgmPacker()
        data_block, rate = _distil(vgm_path)
        regs = packer.split_raw(data_block, True)
    nf = len(regs[0])
    cols = [bytearray(regs[c][:nf]) for c in range(11)]   # trim noise EOF marker
    cols[NOISE_COL] = noise_diff(cols[NOISE_COL])
    return cols, nf, rate


# ===========================================================================
# v1 format - the original greedy LZSS. Kept for reference/comparison (--v1).
# ===========================================================================
def lzss_encode(data, max_chain=256):
    """Greedy LZSS in the v1 format. Self-verified by lzss_decode below."""
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
    """Reference v1 decoder (full output). The 6502 player uses a 256-byte ring;
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
    """Incremental v1 ring decoder - a faithful model of the 6502 routine, an
    extra self-check that the ring/state machine reproduces the data."""
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


# ===========================================================================
# v2 format (default): offset-1 RUN token + single-byte extended length, with
# an optimal (DP) parse. ~8% smaller than v1 for an unchanged bounded decode.
# ===========================================================================
V2_MAXLEN = 255
V2_MINOFF = 2          # offset 1 is handled by the RUN token


def _longest_matches(data, max_off, min_off, cap=V2_MAXLEN, chain=512):
    n = len(data)
    table = defaultdict(list)
    mlen = [0] * n
    moff = [0] * n
    for i in range(n):
        if i + 2 <= n:
            key = data[i:i + 2]
            cand = table[key]
            best_l = 0; best_o = 0; seen = 0
            maxl = min(cap, n - i)
            for p in reversed(cand):
                off = i - p
                if off > max_off:
                    break
                seen += 1
                if seen > chain:
                    break
                if off < min_off:
                    continue
                l = 0
                while l < maxl and data[p + l] == data[i + l]:
                    l += 1
                if l > best_l:
                    best_l = l; best_o = off
                    if l >= maxl:
                        break
            mlen[i] = best_l; moff[i] = best_o
            cand.append(i)
    return mlen, moff


def _longest_runs(data):
    n = len(data)
    rlen = [0] * n
    i = 0
    while i < n:
        if i >= 1 and data[i] == data[i - 1]:
            j = i
            base = data[i - 1]
            while j < n and data[j] == base:
                j += 1
            for k in range(i, j):
                rlen[k] = (j - i) - (k - i)
            i = j
        else:
            i += 1
    return rlen


def _v2_optimal(data):
    n = len(data)
    if n == 0:
        return []
    mlen, moff = _longest_matches(data, 255, V2_MINOFF)
    rlen = _longest_runs(data)
    rcost = lambda L: 1 + (1 if L > 64 else 0)
    mcost = lambda L: 2 + (1 if L > 64 else 0)
    INF = float("inf")
    dp = [INF] * (n + 1); dp[n] = 0
    nxt = [None] * (n + 1)
    for i in range(n - 1, -1, -1):
        best = INF; choice = None
        for k in range(1, min(128, n - i) + 1):
            c = 1 + k + dp[i + k]
            if c < best:
                best = c; choice = ("L", i, k)
        R = min(rlen[i], V2_MAXLEN)
        if R >= 2:
            c = rcost(R) + dp[i + R]
            if c < best:
                best = c; choice = ("R", R)
        M = min(mlen[i], V2_MAXLEN)
        if M >= 2:
            c = mcost(M) + dp[i + M]
            if c < best:
                best = c; choice = ("M", moff[i], M)
            c2 = mcost(2) + dp[i + 2]
            if c2 < best:
                best = c2; choice = ("M", moff[i], 2)
        dp[i] = best; nxt[i] = choice
    ops = []
    i = 0
    while i < n:
        op = nxt[i]
        ops.append(op)
        i += op[1] if op[0] == "R" else op[2]
    return ops


def _v2_emit_len(out, base_cmd, L):
    if L <= 64:
        out.append(base_cmd | (L - 2))
    else:
        out.append(base_cmd | 0x3f)
        out.append(L)


def v2_encode(data):
    out = bytearray()
    for op in _v2_optimal(data):
        if op[0] == "L":
            _, start, k = op
            out.append(k - 1)
            out += data[start:start + k]
        elif op[0] == "R":
            _v2_emit_len(out, 0x80, op[1])
        else:
            _, off, L = op
            _v2_emit_len(out, 0xC0, L)
            out.append(off)
    return bytes(out)


def v2_decode(blob, nout):
    """Reference v2 decoder (full output)."""
    out = bytearray(); i = 0
    while len(out) < nout:
        cmd = blob[i]; i += 1
        if cmd < 0x80:
            k = cmd + 1
            out += blob[i:i + k]; i += k
        else:
            field = cmd & 0x3f
            L = field + 2 if field < 0x3f else blob[i]
            if field == 0x3f:
                i += 1
            off = 1 if cmd < 0xC0 else blob[i]
            if cmd >= 0xC0:
                i += 1
            src = len(out) - off
            for _ in range(L):
                out.append(out[src]); src += 1
    return bytes(out[:nout])


def v2_decode_ring(blob, nout):
    """Incremental v2 ring decoder - a faithful model of the 6502 routine."""
    ring = bytearray(256); head = 0; rem = 0; copy = 0; ismatch = False
    out = bytearray(); i = 0
    while len(out) < nout:
        if rem == 0:
            cmd = blob[i]; i += 1
            if cmd < 0x80:
                rem = cmd + 1; ismatch = False
            else:
                field = cmd & 0x3f
                if field < 0x3f:
                    rem = field + 2
                else:
                    rem = blob[i]; i += 1
                if cmd < 0xC0:
                    copy = (head - 1) & 0xff
                else:
                    copy = (head - blob[i]) & 0xff; i += 1
                ismatch = True
        if ismatch:
            b = ring[copy]; copy = (copy + 1) & 0xff
        else:
            b = blob[i]; i += 1
        ring[head] = b; head = (head + 1) & 0xff
        rem -= 1
        out.append(b)
    return bytes(out[:nout])


# ===========================================================================
# v3 columns - the same v2 tokens over fewer, quieter streams.
#
# The tone columns stop being "low nibble" and "high 6 bits" and become ONE
# column of indices into a table of the periods the tune actually uses. Why
# that is worth 23-29% of the file, and why it also costs FEWER cycles to
# decode, is in docs/vgi-format.md.
# ===========================================================================
V3_HEADER = 4 + 2 + 8 * 2 + 1 + 3 * 2 + 3       # 32 bytes


def periods(cols, chan):
    """The 10-bit tone period of `chan` per frame, from the v2 lo/hi columns."""
    lo, hi = cols[2 * chan], cols[2 * chan + 1]
    out = []
    for i in range(len(lo)):
        assert lo[i] <= 0x0f and hi[i] <= 0x3f, "unexpected tone column value"
        out.append(lo[i] | (hi[i] << 4))
    return out


def build_v3(cols):
    """(8 columns, tables, mode) for the v2 columns of one tune.

    mode 1 = one table shared by all three channels, used whenever the union of
    their periods fits a byte index - it is then always the smaller table, and
    the stream sizes are identical either way, because a per-column LZ matches
    exact repeats and cannot see a relabelling of the alphabet.
    mode 3 = one table per channel, for tunes whose union overflows.
    """
    per = [periods(cols, k) for k in range(3)]
    union = sorted(set(v for p in per for v in p))
    if len(union) <= 256:
        mode, tables = 1, [union]
        maps = [{v: i for i, v in enumerate(union)}] * 3
    else:
        mode, tables = 3, [sorted(set(p)) for p in per]
        for k, t in enumerate(tables):
            if len(t) > 256:
                raise ValueError(
                    "channel %d uses %d distinct periods and a byte index "
                    "cannot address them - pack this tune as v2" % (k, len(t)))
        maps = [{v: i for i, v in enumerate(t)} for t in tables]

    scols = [bytearray(maps[k][v] for v in per[k]) for k in range(3)]
    scols.append(bytearray(cols[NOISE_COL]))
    scols += [bytearray(cols[c]) for c in range(7, 11)]
    return scols, tables, mode


def v3_expand(scols, tables, mode):
    """8 v3 columns + tables -> the 11 v2 columns.

    This is exactly what the player does with two table lookups per channel,
    and it is what the packer asserts against before writing a file: a v3 file
    must drive the SN76489 with the byte-for-byte stream a v2 file would.
    """
    tabs = (tables * 3) if mode == 1 else tables
    cols = [None] * 11
    for k in range(3):
        t = tabs[k]
        cols[2 * k] = bytearray(t[i] & 0x0f for i in scols[k])
        cols[2 * k + 1] = bytearray((t[i] >> 4) & 0x3f for i in scols[k])
    cols[NOISE_COL] = bytearray(scols[3])
    for c in range(4):
        cols[7 + c] = bytearray(scols[4 + c])
    return cols


def serialise_tables(tables):
    """Each table as N low-nibble bytes then N high-6-bit bytes.

    The latch bits are NOT baked in: the player ORs #&80/&a0/&c0 as it already
    does, which costs 2 cycles and keeps one shared table legal for all three
    channels instead of forcing three.
    """
    out = bytearray()
    for t in tables:
        out += bytes(v & 0x0f for v in t)
        out += bytes((v >> 4) & 0x3f for v in t)
    return bytes(out)


def read_v3(blob):
    """Parse a v3 file back into (8 columns, tables, mode, nframes).

    Reading the file the way a player would is the only thing that checks the
    HEADER - the round-trip asserts inside the packer never touch it.
    """
    u16 = lambda o: blob[o] | (blob[o + 1] << 8)
    assert blob[:4] == b"VGI\x03", "not a v3 .vgi"
    nf = u16(4)
    soff = [u16(6 + 2 * i) for i in range(8)]
    mode = blob[22]
    toff = [u16(23 + 2 * k) for k in range(3)]
    counts = [blob[29 + k] or 256 for k in range(3)]

    tables = []
    for k in range(3 if mode == 3 else 1):
        n, o = counts[k], toff[k]
        tables.append([blob[o + i] | (blob[o + n + i] << 4) for i in range(n)])
    scols = [v2_decode_ring(blob[soff[i]:], nf) for i in range(8)]
    return scols, tables, mode, nf


def pack_v3(vgm_path, out_path):
    """Pack a VGM to .vgi v3: 8 columns, v2 tokens, plus the period tables."""
    cols, nf, rate = build_columns(vgm_path)
    scols, tables, mode = build_v3(cols)

    blobs = []
    for c, col in enumerate(scols):
        data = bytes(col)
        blob = v2_encode(data)
        assert v2_decode(blob, nf) == data, "v3 round-trip failed col %d" % c
        assert v2_decode_ring(blob, nf) == data, "v3 ring round-trip failed col %d" % c
        blobs.append(blob)

    # The check that matters: the chip sees what v2 would have made it see.
    back = v3_expand([v2_decode_ring(b, nf) for b in blobs], tables, mode)
    for c in range(11):
        assert bytes(back[c]) == bytes(cols[c]), \
            "v3 does not reproduce register column %d" % c

    tbl_blob = serialise_tables(tables)
    tbl_off = [V3_HEADER] * 3
    if mode == 3:
        o = V3_HEADER
        for k in range(3):
            tbl_off[k] = o
            o += 2 * len(tables[k])

    header = bytearray(b"VGI\x03")
    header += bytes((nf & 0xff, (nf >> 8) & 0xff))
    off = V3_HEADER + len(tbl_blob)
    for b in blobs:
        header += bytes((off & 0xff, (off >> 8) & 0xff))
        off += len(b)
    header.append(mode)
    for o in tbl_off:
        header += bytes((o & 0xff, (o >> 8) & 0xff))
    for k in range(3):
        t = tables[0] if mode == 1 else tables[k]
        header.append(len(t) & 0xff)                 # 0 means 256
    assert len(header) == V3_HEADER

    data = bytes(header) + tbl_blob + b"".join(blobs)

    # ... and read the finished file back the way the player will, which is the
    # only check that covers the header.
    rcols, rtables, rmode, rnf = read_v3(data)
    assert rnf == nf and rmode == mode, "v3 header round-trip failed"
    for c in range(11):
        assert bytes(v3_expand(rcols, rtables, rmode)[c]) == bytes(cols[c]), \
            "v3 file does not reproduce register column %d" % c

    if out_path:
        with open(out_path, "wb") as fh:
            fh.write(data)

    total = len(data)
    print("packed %s  (v3)" % os.path.basename(vgm_path))
    print("  frames   : %d  (%d Hz, ~%.1f s)" % (nf, rate, nf / float(rate or 50)))
    print("  periods  : %s  (%s)"
          % ("/".join(str(len(set(periods(cols, k)))) for k in range(3)),
             "one shared table" if mode == 1 else "a table per channel"))
    print("  per-stream bytes: %s" % " ".join(str(len(b)) for b in blobs))
    print("  total .vgi: %d bytes (header %d + tables %d + streams %d)"
          % (total, V3_HEADER, len(tbl_blob), total - V3_HEADER - len(tbl_blob)))
    if out_path:
        print("  -> %s" % out_path)
    return data, nf


def pack(vgm_path, out_path, version=2):
    """Pack a VGM to .vgi. version=2 (default) = optimal RUN+extlen format;
    version=1 = the original greedy LZSS (kept for reference/comparison);
    version=3 = v2 tokens over 8 columns, with the tone periods indexed."""
    if version == 3:
        return pack_v3(vgm_path, out_path)
    cols, nf, rate = build_columns(vgm_path)
    blobs = []
    for c in range(11):
        data = bytes(cols[c])
        if version == 2:
            blob = v2_encode(data)
            assert v2_decode(blob, nf) == data, "v2 round-trip failed col %d" % c
            assert v2_decode_ring(blob, nf) == data, "v2 ring round-trip failed col %d" % c
        else:
            blob = lzss_encode(data)
            assert lzss_decode(blob, nf) == data, "v1 round-trip failed col %d" % c
            assert lzss_decode_ring(blob, nf) == data, "v1 ring round-trip failed col %d" % c
        blobs.append(blob)

    header = bytearray()
    header += b"VGI\x02" if version == 2 else b"VGI\x01"
    header += bytes((nf & 0xff, (nf >> 8) & 0xff))
    base = 4 + 2 + 11 * 2
    off = base
    for b in blobs:
        header += bytes((off & 0xff, (off >> 8) & 0xff))
        off += len(b)
    assert len(header) == base

    data = bytes(header) + b"".join(blobs)
    if out_path:
        with open(out_path, "wb") as fh:
            fh.write(data)

    total = len(data)
    print("packed %s  (v%d)" % (os.path.basename(vgm_path), version))
    print("  frames   : %d  (%d Hz, ~%.1f s)" % (nf, rate, nf / float(rate or 50)))
    print("  per-stream bytes: %s" % " ".join(str(len(b)) for b in blobs))
    print("  total .vgi: %d bytes (header %d + streams %d)" % (total, base, total - base))
    if out_path:
        print("  -> %s" % out_path)
    return data, nf


def main():
    ap = argparse.ArgumentParser(
        description="Pack an SN76489 VGM into the .vgi incremental-decode format.")
    ap.add_argument("input", help="input .vgm/.vgz file")
    ap.add_argument("-o", "--output", help="output .vgi (default: <input>.vgi)")
    ap.add_argument("-1", "--v1", action="store_true",
                    help="emit the original greedy v1 format instead of v2")
    ap.add_argument("-3", "--v3", action="store_true",
                    help="emit v3: 8 columns with the tone periods indexed "
                         "(23-29%% smaller, but needs a v3-aware player)")
    args = ap.parse_args()
    out = args.output or (os.path.splitext(args.input)[0] + ".vgi")
    version = 1 if args.v1 else (3 if args.v3 else 2)
    pack(args.input, out, version=version)


if __name__ == "__main__":
    main()
