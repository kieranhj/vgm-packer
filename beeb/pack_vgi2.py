#!/usr/bin/env python
# pack_vgi2.py - improved VGI ("v2") packer. Same bounded, one-value-per-stream-
# per-frame decode model as v1, but a better token format + optimal parse:
#
#   0LLLLLLL            literal run, L+1 bytes follow            (1..128)
#   10LLLLLL [E]        RUN (offset-1): if LLLLLL<63 len=LLLLLL+2 (2..64),
#                       else len = E (a full byte, 65..255). NO offset byte.
#   11LLLLLL [E] off    MATCH: length encoded as for RUN, then 1 offset byte.
#
# vs v1 this (a) drops the offset byte on offset-1 runs - 16% of matches - and
# (b) extends lengths to 255 via a single byte, both cutting token count. Token
# length is capped at 255 so the per-stream `rem` counter stays 8-bit and a token
# start reads at most cmd+ext+offset = 3 bytes (keeps the decode bounded/flat).
#
# File layout: 'V','G','I',2 | nframes(16) | 11 x stream offset(16) | streams.
import os
import sys
import argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pack_vgi import build_columns                        # noqa: E402
from explore_vgi import longest_matches, longest_runs     # noqa: E402

MAXLEN = 255
MINOFF = 2          # offset-1 handled by the RUN token


def _runcost(L):
    return 1 + (1 if L > 64 else 0)


def _matchcost(L):
    return 2 + (1 if L > 64 else 0)


def optimal_v2(data):
    n = len(data)
    if n == 0:
        return []
    mlen, moff = longest_matches(data, 255, min_off=MINOFF)
    rlen = longest_runs(data)
    INF = float("inf")
    dp = [INF] * (n + 1)
    dp[n] = 0
    nxt = [None] * (n + 1)
    for i in range(n - 1, -1, -1):
        best = INF; choice = None
        kmax = min(128, n - i)
        for k in range(1, kmax + 1):
            c = 1 + k + dp[i + k]
            if c < best:
                best = c; choice = ("L", i, k)
        R = min(rlen[i], MAXLEN)
        if R >= 2:
            c = _runcost(R) + dp[i + R]
            if c < best:
                best = c; choice = ("R", R)
        M = min(mlen[i], MAXLEN)
        if M >= 2:
            c = _matchcost(M) + dp[i + M]
            if c < best:
                best = c; choice = ("M", moff[i], M)
            c2 = _matchcost(2) + dp[i + 2]
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


def _emit_len(out, base_cmd, L):
    if L <= 64:
        out.append(base_cmd | (L - 2))
    else:
        out.append(base_cmd | 0x3f)
        out.append(L)                       # full length byte (65..255)


def serialize_v2(data, ops):
    out = bytearray()
    for op in ops:
        if op[0] == "L":
            _, start, k = op
            out.append(k - 1)
            out += data[start:start + k]
        elif op[0] == "R":
            _emit_len(out, 0x80, op[1])
        else:
            _, off, L = op
            _emit_len(out, 0xC0, L)
            out.append(off)
    return bytes(out)


def decode_v2(blob, nout):
    out = bytearray()
    i = 0
    while len(out) < nout:
        cmd = blob[i]; i += 1
        if cmd < 0x80:
            k = cmd + 1
            out += blob[i:i + k]; i += k
        else:
            field = cmd & 0x3f
            if field < 0x3f:
                L = field + 2
            else:
                L = blob[i]; i += 1
            if cmd < 0xC0:
                off = 1
            else:
                off = blob[i]; i += 1
            src = len(out) - off
            for _ in range(L):
                out.append(out[src]); src += 1
    return bytes(out[:nout])


def decode_v2_ring(blob, nout):
    """256-byte ring model of the 6502 decoder (extra self-check)."""
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


def pack(vgm_path, out_path):
    import io
    import contextlib
    with contextlib.redirect_stdout(io.StringIO()):
        cols, nf, rate = build_columns(vgm_path)
    blobs = []
    for c in range(11):
        data = bytes(cols[c])
        ops = optimal_v2(data)
        blob = serialize_v2(data, ops)
        assert decode_v2(blob, nf) == data, "v2 round-trip failed col %d" % c
        assert decode_v2_ring(blob, nf) == data, "v2 ring round-trip failed col %d" % c
        blobs.append(blob)

    header = bytearray(b"VGI\x02")
    header += bytes((nf & 0xff, (nf >> 8) & 0xff))
    base = 4 + 2 + 11 * 2
    off = base
    for b in blobs:
        header += bytes((off & 0xff, (off >> 8) & 0xff))
        off += len(b)
    data = bytes(header) + b"".join(blobs)
    if out_path:
        with open(out_path, "wb") as fh:
            fh.write(data)
    return data, nf


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input")
    ap.add_argument("-o", "--output")
    args = ap.parse_args()
    out = args.output or (os.path.splitext(args.input)[0] + ".vgi2")
    data, nf = pack(args.input, out)
    print("packed %s: %d frames, %d bytes -> %s" %
          (os.path.basename(args.input), nf, len(data), out))


if __name__ == "__main__":
    main()
