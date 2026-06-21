#!/usr/bin/env python
# beeb/vgc/measure.py - build the VGC player (OPT=0 baseline / OPT=1 optimised),
# run it in py65 over the whole tune, capture the SN76489 byte stream (for a
# byte-exact equivalence check) and the per-frame cycle cost. With no args it
# compares baseline vs optimised.
import os
import re
import sys
import subprocess
import statistics

import numpy as np
from py65.devices.mpu6502 import MPU
from py65.memory import ObservableMemory

HERE = os.path.dirname(os.path.abspath(__file__))
BEEBASM = os.environ.get("BEEBASM", "beebasm")
NF = 2559
RET = 0x9000


def labels(p):
    return eval(re.sub(r'(\d+)L', r'\1', open(p).read()))[0]


def build(opt):
    lp = os.path.join(HERE, "labels%d.txt" % opt)
    subprocess.run([BEEBASM, "-i", "sim.asm", "-D", "OPT=%d" % opt,
                    "-d", "-labels", lp], cwd=HERE, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return open(os.path.join(HERE, "Vgc"), "rb").read(), labels(lp)


def run(img, lab, breakdown=False):
    cap = []
    mem = ObservableMemory()
    for i, b in enumerate(img):
        mem[0x1100 + i] = b
    mem.subscribe_to_write([0xFE4F], lambda a, v: cap.append(v))
    mpu = MPU(memory=mem)

    def call(addr, a=0, x=0, y=0, c=False):
        mpu.a, mpu.x, mpu.y = a & 0xff, x & 0xff, y & 0xff
        mpu.p = (mpu.p | 1) if c else (mpu.p & ~1)
        sp = mpu.sp
        mem[0x100 + sp] = ((RET - 1) >> 8) & 0xff
        mem[0x100 + ((sp - 1) & 0xff)] = (RET - 1) & 0xff
        mpu.sp = (sp - 2) & 0xff
        mpu.pc = addr
        while mpu.pc != RET:
            mpu.step()

    bhi = lab["vgm_stream_buffers"] >> 8
    d = lab["vgm_data"]
    call(lab["vgm_init"], a=bhi, x=d & 0xff, y=(d >> 8) & 0xff, c=True)

    perframe = []
    VU = lab["vgm_update"]
    for _ in range(NF):
        sp = mpu.sp
        mem[0x100 + sp] = ((RET - 1) >> 8) & 0xff
        mem[0x100 + ((sp - 1) & 0xff)] = (RET - 1) & 0xff
        mpu.sp = (sp - 2) & 0xff
        mpu.pc = VU; mpu.a = 0
        c0 = mpu.processorCycles
        while mpu.pc != RET:
            mpu.step()
        perframe.append(mpu.processorCycles - c0)
    return cap, perframe


def stats(name, pf):
    a = np.array(pf)
    print("  %-10s min %4d  mean %5.0f  p99 %5d  max %5d   total %d" %
          (name, a.min(), a.mean(), int(np.percentile(a, 99)), a.max(), a.sum()))
    return a


def main():
    bimg, blab = build(0)
    bcap, bpf = run(bimg, blab)
    print("baseline:")
    b = stats("baseline", bpf)
    if not os.path.exists(os.path.join(HERE, "vgcplayer_opt.asm")):
        print("(no vgcplayer_opt.asm yet)")
        return 0
    oimg, olab = build(1)
    ocap, opf = run(oimg, olab)
    ok = (ocap == bcap)
    print("optimised: SN output %s (%d bytes)" %
          ("MATCHES baseline" if ok else "DIFFERS!", len(bcap)))
    o = stats("optimised", opf)
    if ok:
        print("\nspeedup: mean %.2fx  max %.2fx  total %.2fx  (saved %d cycles over %d frames)" %
              (b.mean() / o.mean(), b.max() / o.max(), b.sum() / o.sum(),
               b.sum() - o.sum(), NF))
    return 0 if (not os.path.exists(os.path.join(HERE, "vgcplayer_opt.asm")) or ok) else 1


if __name__ == "__main__":
    sys.exit(main())
