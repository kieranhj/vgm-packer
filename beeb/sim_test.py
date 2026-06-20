#!/usr/bin/env python
# sim_test.py - verify the 6502 incremental decoder (player.asm, TEST build)
# reproduces the register columns exactly, using a py65 6502 simulation. This
# validates the decode logic without a BBC; the hardware sound-write timing is
# the only part it cannot exercise.
#
# Assemble the TEST build first:
#   beebasm -i player.asm -D TEST=1 -d -labels labels.txt   (writes "Player")
# then: python sim_test.py
import os
import re
import sys

from py65.devices.mpu6502 import MPU

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from pack_vgi import build_columns      # noqa: E402

VGM = os.path.join(os.path.dirname(HERE), "vgm", "Ghost House (Tobikomi Remix).bbc.vgm")
KFRAMES = 512
OUTBUF = 0x4000
LOAD = 0x1900


def load_labels(path):
    txt = open(path).read()
    txt = re.sub(r'(\d+)L', r'\1', txt)        # 6477L -> 6477 (py2 long syntax)
    obj = eval(txt)                            # [{...}]
    return obj[0]


def main():
    labels = load_labels(os.path.join(HERE, "labels.txt"))
    start = labels["start"]
    testdone = labels["testdone"]

    image = open(os.path.join(HERE, "Player"), "rb").read()
    mpu = MPU()
    for i, b in enumerate(image):
        mpu.memory[LOAD + i] = b
    mpu.pc = start

    steps = 0
    MAXSTEPS = 60_000_000
    while mpu.pc != testdone:
        mpu.step()
        steps += 1
        if steps > MAXSTEPS:
            print("FAIL: did not reach testdone after %d steps (pc=%04X)" % (steps, mpu.pc))
            return 1
    print("decoder ran %d frames in %d simulated instructions" % (KFRAMES, steps))

    cols, nf, rate = build_columns(VGM)
    kf = min(KFRAMES, nf)
    bad = 0
    first = None
    for f in range(kf):
        for s in range(11):
            got = mpu.memory[OUTBUF + f * 11 + s]
            exp = cols[s][f]
            if got != exp:
                bad += 1
                if first is None:
                    first = (f, s, exp, got)
    if bad:
        print("FAIL: %d/%d byte mismatches; first at frame %d stream %d exp=%02X got=%02X"
              % (bad, kf * 11, first[0], first[1], first[2], first[3]))
        return 1
    print("PASS: all %d decoded bytes (%d frames x 11 streams) match the source." % (kf * 11, kf))
    return 0


if __name__ == "__main__":
    sys.exit(main())
