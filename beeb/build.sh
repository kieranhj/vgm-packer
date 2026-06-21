#!/bin/sh
# Build the incremental-decode player disc and run all simulation checks.
# Run from the beeb/ directory. Set BEEBASM if beebasm is not on your PATH.
set -e

BEEBASM=${BEEBASM:-beebasm}
VGM=${1:-../vgm/Ghost House (Tobikomi Remix).bbc.vgm}

echo "== pack =="
python pack_vgi.py "$VGM" -o music.vgi

echo "== sim: decoder =="
"$BEEBASM" -i player.asm -D TEST=1 -D RING_PAGE=96 -D VGI2=1 -d -labels labels.txt
python sim_test.py

echo "== sim: full player path =="
"$BEEBASM" -i player.asm -D TEST=0 -D RING_PAGE=96 -D VGI2=1 -d -labels labels_full.txt
python sim_test_player.py
python measure_cycles.py

echo "== build bootable disc =="
rm -f music.ssd
"$BEEBASM" -i player.asm -D TEST=0 -D RING_PAGE=96 -D VGI2=1 -do music.ssd -boot Player -title GHOSTV2 -opt 3
# pad to a standard 200 KB (80-track SS) image
python - <<'PY'
import os
p = "music.ssd"
full = 204800
sz = os.path.getsize(p)
if sz < full:
    with open(p, "ab") as f:
        f.write(b"\x00" * (full - sz))
print("music.ssd ready (%d bytes)" % os.path.getsize(p))
PY
