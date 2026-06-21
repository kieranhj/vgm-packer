#!/usr/bin/env python
# sim_compare.py - apples-to-apples per-frame cycle comparison between the new
# incremental player (player.asm) and the existing VGC player (vgm-player-bbc),
# both driven through the same py65 6502 simulation on the same tune.
#
# Fairness: the actual SN76489 write is stubbed to RTS in BOTH players, so we
# measure decode + register reconstruction only (the two players' real sn_write
# routines differ - the VGC one has no delay, mine has a conservative ~120-cycle
# /WE strobe delay - which is a tunable constant, not a decode difference).
#
# Prereqs:
#   - this player built:  beebasm -i player.asm -D TEST=0 -d -labels labels_full.txt   (-> Player)
#   - VGC player:  clone github.com/kieranhj/vgm-player-bbc next to vgm-packer,
#     pack the same tune to ghost.vgc there, then:
#       beebasm -i sim_vgc.asm -d -labels vgc_labels.txt   (-> Vgc)
#     (set VGM_PLAYER_BBC to override its location)
import os
import re
import sys

from py65.devices.mpu6502 import MPU
from py65.memory import ObservableMemory

HERE = os.path.dirname(os.path.abspath(__file__))
VGCDIR = os.environ.get("VGM_PLAYER_BBC", os.path.join(HERE, "..", "..", "vgm-player-bbc"))
NFRAMES = 2559                      # Ghost House
RTS = 0x60
RETSENT = 0x9000                    # return sentinel (outside both code/data)
BUDGET = 2_000_000 // 50           # 40000 cycles @ 2 MHz, one 50 Hz frame


def labels(path):
    return eval(re.sub(r'(\d+)L', r'\1', open(path).read()))[0]


def new_mpu(image, load, stubs):
    mem = ObservableMemory()
    for i, b in enumerate(image):
        mem[load + i] = b
    for addr in stubs:
        mem[addr] = RTS
    # benign reads for any hardware the code might poke
    mem.subscribe_to_read([0xFE4D], lambda a: 0x02)
    return MPU(memory=mem)


def call(mpu, addr, a=0, x=0, y=0, carry=False):
    """Call a 6502 subroutine, return cycles consumed."""
    mpu.a, mpu.x, mpu.y = a & 0xff, x & 0xff, y & 0xff
    mpu.p = (mpu.p | 0x01) if carry else (mpu.p & ~0x01)
    sp = mpu.sp
    mpu.memory[0x100 + sp] = ((RETSENT - 1) >> 8) & 0xff
    sp = (sp - 1) & 0xff
    mpu.memory[0x100 + sp] = (RETSENT - 1) & 0xff
    sp = (sp - 1) & 0xff
    mpu.sp = sp
    mpu.pc = addr
    c0 = mpu.processorCycles
    guard = 0
    while mpu.pc != RETSENT:
        mpu.step()
        guard += 1
        if guard > 2_000_000:
            raise RuntimeError("runaway at pc=%04X" % mpu.pc)
    return mpu.processorCycles - c0


def measure_new():
    lab = labels(os.path.join(HERE, "labels_full.txt"))
    img = open(os.path.join(HERE, "Player"), "rb").read()
    mpu = new_mpu(img, 0x1900, [lab["sn"]])
    call(mpu, lab["init_streams"])
    return [call(mpu, lab["do_frame"]) for _ in range(NFRAMES)]


def measure_vgc():
    lpath = os.path.join(VGCDIR, "vgc_labels.txt")
    ipath = os.path.join(VGCDIR, "Vgc")
    if not (os.path.exists(lpath) and os.path.exists(ipath)):
        return None
    lab = labels(lpath)
    img = open(ipath, "rb").read()
    mpu = new_mpu(img, 0x1100, [lab["sn_write"]])
    buffers_hi = lab["vgm_stream_buffers"] >> 8
    data = lab["vgm_data"]
    call(mpu, lab["vgm_init"], a=buffers_hi, x=data & 0xff, y=(data >> 8) & 0xff, carry=True)
    return [call(mpu, lab["vgm_update"]) for _ in range(NFRAMES)]


def stats(name, d):
    lo, hi, mean = min(d), max(d), sum(d) / len(d)
    print("  %-22s min %5d  mean %6.0f  max %5d  spread %5d  (worst = %4.1f%% of 50Hz)"
          % (name, lo, mean, hi, hi - lo, 100.0 * hi / BUDGET))
    return lo, hi, mean


def main():
    print("Per-frame DECODE+RECONSTRUCT cost, %d frames, Ghost House" % NFRAMES)
    print("(SN76489 write stubbed to RTS in both; 50 Hz budget = %d cycles @ 2 MHz)\n" % BUDGET)
    new = measure_new()
    nlo, nhi, nmean = stats("incremental (.vgi)", new)
    vgc = measure_vgc()
    if vgc is None:
        print("\n  VGC player not found under %s" % VGCDIR)
        print("  (clone vgm-player-bbc, build ghost.vgc + Vgc; see header) - skipping VGC side.")
        return 0
    vlo, vhi, vmean = stats("existing VGC (8xLZ4)", vgc)
    print("\n  mean: VGC %.0f vs incremental %.0f  (%.2fx)" % (vmean, nmean, nmean / vmean))
    print("  worst frame: VGC %d vs incremental %d  (%.2fx)" % (vhi, nhi, float(nhi) / vhi))
    print("  spread (max-min): VGC %d vs incremental %d" % (vhi - vlo, nhi - nlo))
    return 0


if __name__ == "__main__":
    sys.exit(main())
