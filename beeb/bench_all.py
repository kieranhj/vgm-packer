#!/usr/bin/env python
# bench_all.py - full-corpus comparison of the new incremental (.vgi) player vs
# the existing VGC player (vgm-player-bbc): compressed size, fixed code+buffer
# footprint, and per-frame runtime cost (cycles), all measured in py65.
#
# For each VGM: pack .vgi and .vgc, assemble both players, run both decoders for
# the whole tune with the SN76489 write stubbed to RTS (so only decode + register
# reconstruction is timed - the players' real sn_write routines differ).
#
# Needs: beebasm (env BEEBASM or PATH), py65, and a vgm-player-bbc checkout
# (env VGM_PLAYER_BBC, default ../../vgm-player-bbc).
import os
import re
import sys
import glob
import subprocess

from py65.devices.mpu6502 import MPU
from py65.memory import ObservableMemory

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
VGCDIR = os.environ.get("VGM_PLAYER_BBC", os.path.join(HERE, "..", "..", "vgm-player-bbc"))
BEEBASM = os.environ.get("BEEBASM", "beebasm")
RTS = 0x60
RETSENT = 0x9000
BUDGET = 2_000_000 // 50

sys.path.insert(0, HERE)
from pack_vgi import pack as pack_vgi    # noqa: E402


def sh(cmd, cwd=None):
    subprocess.run(cmd, cwd=cwd, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def labels(path):
    return eval(re.sub(r'(\d+)L', r'\1', open(path).read()))[0]


def call(mpu, addr, a=0, x=0, y=0, carry=False):
    mpu.a, mpu.x, mpu.y = a & 0xff, x & 0xff, y & 0xff
    mpu.p = (mpu.p | 0x01) if carry else (mpu.p & ~0x01)
    sp = mpu.sp
    mpu.memory[0x100 + sp] = ((RETSENT - 1) >> 8) & 0xff
    mpu.memory[0x100 + ((sp - 1) & 0xff)] = (RETSENT - 1) & 0xff
    mpu.sp = (sp - 2) & 0xff
    mpu.pc = addr
    c0 = mpu.processorCycles
    while mpu.pc != RETSENT:
        mpu.step()
    return mpu.processorCycles - c0


def make_mpu(image, load, stub_addr):
    mem = ObservableMemory()
    for i, b in enumerate(image):
        mem[load + i] = b
    mem[stub_addr] = RTS
    mem.subscribe_to_read([0xFE4D], lambda a: 0x02)
    return MPU(memory=mem)


def measure(image, load, stub, init, frames):
    """init = (addr, a, x, y, carry); returns (min, mean, max)."""
    mpu = make_mpu(image, load, stub)
    call(mpu, init[0], init[1], init[2], init[3], init[4])
    costs = [call(mpu, init[5]) for _ in range(frames)]   # init[5] = per-frame addr
    return min(costs), sum(costs) / len(costs), max(costs)


def bench_one(vgm):
    name = os.path.basename(vgm)
    # --- pack both formats ---
    data, nf = pack_vgi(vgm, os.path.join(HERE, "music.vgi"))
    vgi_size = len(data)
    vgc_path = os.path.join(VGCDIR, "ghost.vgc")
    sh([sys.executable, os.path.join(ROOT, "vgmpacker.py"), vgm, "-o", vgc_path], cwd=ROOT)
    vgc_size = os.path.getsize(vgc_path)

    # --- assemble both players ---
    # rings up at &C000 so even the largest .vgi fits below them in the 64K sim
    # (ring location does not affect cycle counts).
    sh([BEEBASM, "-i", "player.asm", "-D", "TEST=0", "-D", "RING_PAGE=&C0",
        "-d", "-labels", "labels_full.txt"], cwd=HERE)
    sh([BEEBASM, "-i", "sim_vgc.asm", "-d", "-labels", "vgc_labels.txt"], cwd=VGCDIR)
    nl = labels(os.path.join(HERE, "labels_full.txt"))
    vl = labels(os.path.join(VGCDIR, "vgc_labels.txt"))

    # --- measure ---
    nimg = open(os.path.join(HERE, "Player"), "rb").read()
    nstats = measure(nimg, 0x1900, nl["sn"],
                     (nl["init_streams"], 0, 0, 0, False, nl["do_frame"]), nf)
    vimg = open(os.path.join(VGCDIR, "Vgc"), "rb").read()
    bhi = vl["vgm_stream_buffers"] >> 8
    d = vl["vgm_data"]
    vstats = measure(vimg, 0x1100, vl["sn_write"],
                     (vl["vgm_init"], bhi, d & 0xff, (d >> 8) & 0xff, True, vl["vgm_update"]), nf)

    return dict(name=name, nf=nf, vgi=vgi_size, vgc=vgc_size,
                n=nstats, v=vstats, nl=nl, vl=vl)


def main():
    files = sorted(glob.glob(os.path.join(ROOT, "vgm", "*.vgm")))
    rows = []
    for i, f in enumerate(files, 1):
        sys.stderr.write("[%d/%d] %s\n" % (i, len(files), os.path.basename(f)))
        sys.stderr.flush()
        try:
            rows.append(bench_one(f))
        except Exception as e:
            sys.stderr.write("    FAILED: %r\n" % e)

    if not rows:
        print("no rows")
        return 1

    # fixed footprints (tune-independent), taken from the last build's labels
    nl, vl = rows[-1]["nl"], rows[-1]["vl"]
    n_prog = nl["music_data"] - 0x1900           # code + state tables
    n_buf = 11 * 256                              # 11 stream ring buffers
    v_prog = vl["vgm_stream_buffers"] - 0x1100   # code + state tables
    v_buf = vl["vgm_data"] - vl["vgm_stream_buffers"]

    print("\n=== FOOTPRINT (fixed, tune-independent) ===")
    print("  %-22s %6s %9s %6s %8s" % ("player", "code", "buffers", "zp", "total"))
    print("  %-22s %6d %9d %6d %8d" %
          ("incremental (.vgi)", n_prog, n_buf, 7, n_prog + n_buf + 7))
    print("  %-22s %6d %9d %6d %8d" %
          ("existing VGC (8xLZ4)", v_prog, v_buf, 8, v_prog + v_buf + 8))
    print("  (incremental: 11 x 256B rings; VGC: 8 x 256B buffers. zp approx.)")

    print("\n=== COMPRESSED SIZE & PER-FRAME COST (decode+reconstruct, SN write stubbed) ===")
    print("  budget = %d cycles/frame (50 Hz @ 2 MHz)\n" % BUDGET)
    h = "  %-32s %6s %7s %7s | %16s | %16s"
    print(h % ("tune", "frames", ".vgi", ".vgc", "incr  mean/max", "VGC   mean/max"))
    print("  " + "-" * 96)
    tot = dict(nf=0, vgi=0, vgc=0)
    nmax = vmax = 0
    for r in rows:
        print("  %-32s %6d %7d %7d | %7.0f /%6d | %7.0f /%6d" %
              (r["name"][:32], r["nf"], r["vgi"], r["vgc"],
               r["n"][1], r["n"][2], r["v"][1], r["v"][2]))
        tot["nf"] += r["nf"]; tot["vgi"] += r["vgi"]; tot["vgc"] += r["vgc"]
        nmax = max(nmax, r["n"][2]); vmax = max(vmax, r["v"][2])
    print("  " + "-" * 96)
    print("  %-32s %6d %7d %7d |  corpus-wide worst-case frame:    | " %
          ("TOTAL", tot["nf"], tot["vgi"], tot["vgc"]))
    print("  %-32s %6s %7s %7s | incr max %d (%.1f%%)  VGC max %d (%.1f%%)" %
          ("", "", "", "", nmax, 100.0 * nmax / BUDGET, vmax, 100.0 * vmax / BUDGET))
    print("\n  .vgi total %d vs .vgc total %d  (%.2fx)" %
          (tot["vgi"], tot["vgc"], float(tot["vgi"]) / tot["vgc"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
