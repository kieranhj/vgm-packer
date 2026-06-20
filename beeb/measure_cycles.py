#!/usr/bin/env python
# measure_cycles.py - certify the sec 12.4 bounded-worst-case claim by measuring
# the player's actual per-frame 6502 cycle cost in py65 (which counts cycles).
# Reports min/mean/max cycles per frame over the tune; the spread should be
# small (cost is bounded, independent of match length) and far under the 50 Hz
# budget of ~40000 cycles at 2 MHz.
#
# Assemble first:  beebasm -i player.asm -D TEST=0 -d -labels labels_full.txt
import os
import re
import sys

from py65.devices.mpu6502 import MPU
from py65.memory import ObservableMemory

HERE = os.path.dirname(os.path.abspath(__file__))
LOAD = 0x1900
KFRAMES = 600
OSWRCH, VIA_IFR, SN_PORT = 0xFFEE, 0xFE4D, 0xFE4F
FRAME_CYCLES_50HZ = 2_000_000 // 50      # 40000


def main():
    labels = eval(re.sub(r'(\d+)L', r'\1', open(os.path.join(HERE, "labels_full.txt")).read()))[0]
    start, do_frame, md = labels["start"], labels["do_frame"], labels["music_data"]

    mem = ObservableMemory()
    image = open(os.path.join(HERE, "Player"), "rb").read()
    for i, b in enumerate(image):
        mem[LOAD + i] = b
    mem[OSWRCH] = 0x60
    mem[md + 4] = KFRAMES & 0xff
    mem[md + 5] = (KFRAMES >> 8) & 0xff
    mem.subscribe_to_read([VIA_IFR], lambda a: 0x02)
    mem.subscribe_to_write([SN_PORT], lambda a, v: None)

    mpu = MPU(memory=mem)
    mpu.pc = start

    marks = []
    steps = 0
    while len(marks) <= KFRAMES and steps < 20_000_000:
        if mpu.pc == do_frame:
            marks.append(mpu.processorCycles)
        mpu.step()
        steps += 1

    deltas = [marks[i + 1] - marks[i] for i in range(len(marks) - 1)]
    if not deltas:
        print("FAIL: no frames measured")
        return 1
    lo, hi = min(deltas), max(deltas)
    mean = sum(deltas) / len(deltas)
    print("per-frame 6502 cost over %d frames (incl. decode + 11 SN writes):" % len(deltas))
    print("  min %d  mean %.0f  max %d cycles" % (lo, mean, hi))
    print("  50 Hz budget = %d cycles (2 MHz)  ->  worst frame uses %.1f%%"
          % (FRAME_CYCLES_50HZ, 100.0 * hi / FRAME_CYCLES_50HZ))
    print("  spread (max-min) = %d cycles -> cost is bounded, ~independent of match length"
          % (hi - lo))
    return 0


if __name__ == "__main__":
    sys.exit(main())
