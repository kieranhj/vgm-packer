#!/usr/bin/env python
# explore_vgi.py - offline search for VGI compression improvements that KEEP the
# bounded/consistent per-frame decode (i.e. still one value per stream per frame,
# no RLE-skip layer that would make some frames cheap and others expensive).
#
# Measures corpus-total compressed size for several encoder/format variants, each
# with an optimal (DP) parse and a verified round-trip. Decoder cost implications
# are noted but the actual cycle measurement is done separately for the winner.
#
# Variants explored (all per-column, 256-byte window / 8-bit offset unless said):
#   base   current greedy LZSS (sanity)
#   opt    optimal parse, same format (decoder identical -> runtime identical)
#   opt+x  optimal + extended match length (merge >129 runs)
#   v2     opt + extended + 1-byte offset-1 RUN token (drops the offset byte)
#   v2/mm3 v2 with min-match 3 (skip break-even 2-byte matches)
#   v2/8   v2 on 8 streams (tone lo/hi combined) -> also less RAM, fewer contexts
#   16bit  optimal + 16-bit offset, unbounded window (NOT bounded-RAM; upper bound)
import sys
import glob
import io
import contextlib
from collections import defaultdict

sys.path.insert(0, ".")
from pack_vgi import build_columns

VGC_TOTAL = 80108          # corpus .vgc (plain LZ4) for reference
FRAME_OVH = 6              # bytes of per-stream framing (offset table entry etc.)


# ---------------------------------------------------------------- match finding
def longest_matches(data, max_off, min_off=1, cap=4096, chain=512):
    """For each position: (best_len, best_off) longest match with min_off<=off<=max_off."""
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


def longest_runs(data):
    """offset-1 run length per position (data[i..]==data[i-1])."""
    n = len(data)
    rlen = [0] * n
    run = 0
    for i in range(n - 1, -1, -1):
        if i >= 1 and data[i] == data[i - 1]:
            run = (rlen[i + 1] + 1) if i + 1 < n else 1
            # rlen[i+1] counts run starting at i+1; but that requires data[i+1]==data[i].
            # simpler explicit below
        rlen[i] = 0
    # explicit (clear) computation
    i = 0
    while i < n:
        if i >= 1 and data[i] == data[i - 1]:
            j = i
            base = data[i - 1]
            while j < n and data[j] == base:
                j += 1
            L = j - i
            for k in range(i, j):
                rlen[k] = L - (k - i)
            i = j
        else:
            i += 1
    return rlen


# ---------------------------------------------------------------- length coding
def ext_bytes(L, base, field_max):
    """number of extra length bytes for length L (LZ4-style), and the field value."""
    v = L - base
    if v < field_max:
        return 0
    rem = v - field_max
    e = 1
    while rem >= 255:
        rem -= 255; e += 1
    return e


# ---------------------------------------------------------------- optimal parse
def optimal(data, fmt):
    """DP optimal parse. fmt keys: off_bytes, len_bits, has_run, min_match,
    max_off, ext. Returns list of ops: ('L',start,len)|('M',off,len)|('R',len)."""
    n = len(data)
    if n == 0:
        return []
    off_bytes = fmt["off_bytes"]
    field_max = (1 << fmt["len_bits"]) - 1
    has_run = fmt["has_run"]
    mm = fmt["min_match"]
    ext = fmt["ext"]
    cap = 1 << 30 if ext else (field_max + mm)        # max encodable match length
    rcap = 1 << 30 if ext else (field_max + 2)

    mlen, moff = longest_matches(data, fmt["max_off"], min_off=2 if has_run else 1)
    rlen = longest_runs(data) if has_run else None

    def mcost(L):
        return 1 + off_bytes + (ext_bytes(L, mm, field_max) if ext else 0)

    def rcost(L):
        return 1 + (ext_bytes(L, 2, field_max) if ext else 0)

    INF = float("inf")
    dp = [INF] * (n + 1)
    dp[n] = 0
    nxt = [None] * (n + 1)
    for i in range(n - 1, -1, -1):
        best = INF; choice = None
        # literal run 1..128
        kmax = min(128, n - i)
        for k in range(1, kmax + 1):
            c = 1 + k + dp[i + k]
            if c < best:
                best = c; choice = ("L", i, k)
        # offset-1 run (take the full run; near-optimal given ~flat token cost)
        if has_run and rlen[i] >= 2:
            R = min(rlen[i], rcap)
            c = rcost(R) + dp[i + R]
            if c < best:
                best = c; choice = ("R", R)
        # general match (longest); also consider a short match in case it aligns
        if mlen[i] >= mm:
            M = min(mlen[i], cap)
            c = mcost(M) + dp[i + M]
            if c < best:
                best = c; choice = ("M", moff[i], M)
            if M > mm:                       # also try the minimal match length
                c = mcost(mm) + dp[i + mm]
                if c < best:
                    best = c; choice = ("M", moff[i], mm)
        dp[i] = best; nxt[i] = choice
    ops = []
    i = 0
    while i < n:
        op = nxt[i]
        ops.append(op)
        if op[0] == "L":
            i += op[2]
        elif op[0] == "R":
            i += op[1]
        else:
            i += op[2]
    return ops


def serialize(data, ops, fmt):
    off_bytes = fmt["off_bytes"]
    field_max = (1 << fmt["len_bits"]) - 1
    has_run = fmt["has_run"]
    mm = fmt["min_match"]
    ext = fmt["ext"]
    out = bytearray()

    def emit_len(field, L, base):
        if not ext:
            field |= (L - base)
            out.append(field)
            return
        v = L - base
        if v < field_max:
            out.append(field | v)
        else:
            out.append(field | field_max)
            rem = v - field_max
            while rem >= 255:
                out.append(255); rem -= 255
            out.append(rem)

    pos = 0
    for op in ops:
        if op[0] == "L":
            _, start, k = op
            out.append(k - 1)                      # 0LLLLLLL
            out += data[start:start + k]
            pos += k
        elif op[0] == "R":
            L = op[1]
            if has_run:
                emit_len(0x80, L, 2)               # 10LLLLLL (+ext)
            else:                                   # fall back to a real match off=1
                emit_len(0x80, L, mm)
                for _ in range(off_bytes):
                    out.append(1)
            pos += L
        else:
            _, off, L = op
            base_field = 0xC0 if has_run else 0x80
            emit_len(base_field, L, mm)
            for b in range(off_bytes):
                out.append((off >> (8 * b)) & 0xff)
            pos += L
    return bytes(out)


def decode(blob, nout, fmt):
    off_bytes = fmt["off_bytes"]
    field_max = (1 << fmt["len_bits"]) - 1
    has_run = fmt["has_run"]
    mm = fmt["min_match"]
    ext = fmt["ext"]
    out = bytearray()
    i = 0

    def read_len(field_val, base):
        if not ext:
            return base + field_val
        if field_val < field_max:
            return base + field_val
        s = 0
        nonlocal i
        while True:
            b = blob[i]; i += 1
            s += b
            if b != 255:
                break
        return base + field_max + s

    while len(out) < nout:
        cmd = blob[i]; i += 1
        if cmd < 0x80:
            k = cmd + 1
            out += blob[i:i + k]; i += k
        elif has_run and cmd < 0xC0:
            L = read_len(cmd & 0x3f, 2)
            src = len(out) - 1
            for _ in range(L):
                out.append(out[src])
        else:
            field = (cmd & 0x3f) if has_run else (cmd & 0x7f)
            L = read_len(field, mm)
            off = 0
            for b in range(off_bytes):
                off |= blob[i] << (8 * b); i += 1
            src = len(out) - off
            for _ in range(L):
                out.append(out[src]); src += 1
    return bytes(out[:nout])


# ---------------------------------------------------------------- drivers
def enc_size(data, fmt):
    if len(data) == 0:
        return 0
    ops = optimal(data, fmt)
    blob = serialize(data, ops, fmt)
    assert decode(blob, len(data), fmt) == data, "round-trip failed for %s" % fmt["name"]
    return len(blob)


def cols_for(f, eight):
    with contextlib.redirect_stdout(io.StringIO()):
        cols, nf, rate = build_columns(f)
    if not eight:
        return [bytes(c) for c in cols]            # 11 streams (noise diffed)
    # 8 streams: combine tone lo/hi pairs (interleaved), noise, 4 vols
    def comb(a, b):
        out = bytearray()
        for i in range(nf):
            out.append(cols[a][i]); out.append(cols[b][i])
        return bytes(out)
    return [comb(0, 1), comb(2, 3), comb(4, 5), bytes(cols[6]),
            bytes(cols[7]), bytes(cols[8]), bytes(cols[9]), bytes(cols[10])]


VARIANTS = [
    dict(name="opt          ", off_bytes=1, len_bits=7, has_run=False, min_match=2, max_off=255, ext=False, eight=False),
    dict(name="opt+extlen   ", off_bytes=1, len_bits=7, has_run=False, min_match=2, max_off=255, ext=True,  eight=False),
    dict(name="v2 run+ext   ", off_bytes=1, len_bits=6, has_run=True,  min_match=2, max_off=255, ext=True,  eight=False),
    dict(name="v2 mm3       ", off_bytes=1, len_bits=6, has_run=True,  min_match=3, max_off=255, ext=True,  eight=False),
    dict(name="v2 8-stream  ", off_bytes=1, len_bits=6, has_run=True,  min_match=2, max_off=255, ext=True,  eight=True),
    dict(name="v2 mm3 8-str ", off_bytes=1, len_bits=6, has_run=True,  min_match=3, max_off=255, ext=True,  eight=True),
    dict(name="16bit unbound", off_bytes=2, len_bits=6, has_run=True,  min_match=2, max_off=1 << 20, ext=True, eight=False),
]


def main():
    files = sorted(glob.glob("../vgm/*.vgm"))
    # baseline greedy (from pack_vgi) for reference
    from pack_vgi import lzss_encode
    base_total = 0
    cache11 = {}
    for f in files:
        c11 = cols_for(f, False)
        cache11[f] = c11
        base_total += sum(len(lzss_encode(c)) for c in c11) + FRAME_OVH * 11

    print("corpus compressed size (streams + framing), 11 tunes / 74052 frames")
    print("baseline greedy 11-stream : %7d   (1.00x)   [current VGI]" % base_total)
    print("reference .vgc (LZ4)      : %7d   (%.2fx)   [smaller=better]" %
          (VGC_TOTAL, VGC_TOTAL / base_total))
    print("-" * 64)
    for v in VARIANTS:
        total = 0
        for f in files:
            cols = cols_for(f, True) if v["eight"] else cache11[f]
            nstreams = len(cols)
            total += sum(enc_size(c, v) for c in cols) + FRAME_OVH * nstreams
        print("%-13s : %7d   (%.2fx)   vs vgc %.2fx" %
              (v["name"], total, total / base_total, total / VGC_TOTAL))
        sys.stdout.flush()


if __name__ == "__main__":
    main()
