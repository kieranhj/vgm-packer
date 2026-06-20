#!/usr/bin/env python
# sim_test_player.py - exercise the REAL player path (TEST=0 build) in py65:
# the per-frame loop, SN76489 register encoding and the noise 0x0f skip. BBC
# hardware is stubbed (OSWRCH -> RTS, vsync flag always set, sound data port
# captured), so we can confirm the exact byte stream sent to the sound chip
# matches what the decoded register columns imply. (The /WE strobe timing is
# the only thing this can't check - that needs real hardware.)
#
# Assemble first:  beebasm -i player.asm -D TEST=0 -d -labels labels_full.txt
# then:            python sim_test_player.py
import os
import re
import sys

from py65.devices.mpu6502 import MPU
from py65.memory import ObservableMemory

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from pack_vgi import build_columns      # noqa: E402

VGM = os.path.join(os.path.dirname(HERE), "vgm", "Ghost House (Tobikomi Remix).bbc.vgm")
LOAD = 0x1900
KFRAMES = 200
SKIP = 0x0f

OSWRCH = 0xFFEE
VIA_IFR = 0xFE4D     # vsync poll
SN_PORT = 0xFE4F     # System VIA port A -> sound data bus


def expected_sn_stream(cols, k):
    """The SN76489 command bytes the player should emit for k frames."""
    out = []
    for f in range(k):
        c = [cols[s][f] for s in range(11)]
        out += [0x80 | c[0], c[1], 0xA0 | c[2], c[3], 0xC0 | c[4], c[5]]
        if c[6] != SKIP:
            out.append(0xE0 | c[6])
        out += [0x90 | c[7], 0xB0 | c[8], 0xD0 | c[9], 0xF0 | c[10]]
    return out


def main():
    labels = eval(re.sub(r'(\d+)L', r'\1', open(os.path.join(HERE, "labels_full.txt")).read()))[0]
    start = labels["start"]
    md = labels["music_data"]

    captured = []
    mem = ObservableMemory()
    image = open(os.path.join(HERE, "Player"), "rb").read()
    for i, b in enumerate(image):
        mem[LOAD + i] = b
    mem[OSWRCH] = 0x60                       # RTS, so the banner JSRs are harmless
    mem[md + 4] = KFRAMES & 0xff             # shorten the tune for the test
    mem[md + 5] = (KFRAMES >> 8) & 0xff

    mem.subscribe_to_read([VIA_IFR], lambda addr: 0x02)        # vsync always ready
    mem.subscribe_to_write([SN_PORT], lambda addr, val: captured.append(val))

    mpu = MPU(memory=mem)
    mpu.pc = start

    cols, nf, rate = build_columns(VGM)
    expected = expected_sn_stream(cols, KFRAMES)

    steps = 0
    while len(captured) < len(expected) and steps < 5_000_000:
        mpu.step()
        steps += 1

    got = captured[:len(expected)]
    if got != expected:
        n = min(len(got), len(expected))
        first = next((i for i in range(n) if got[i] != expected[i]), n)
        print("FAIL: SN stream differs at index %d (got %s exp %s); captured %d, expected %d"
              % (first, hex(got[first]) if first < len(got) else "-",
                 hex(expected[first]) if first < len(expected) else "-",
                 len(captured), len(expected)))
        return 1
    print("PASS: %d SN76489 bytes over %d frames match (%d sim instructions)."
          % (len(expected), KFRAMES, steps))
    return 0


if __name__ == "__main__":
    sys.exit(main())
