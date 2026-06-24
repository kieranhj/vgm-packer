#!/bin/sh
# Build four raster-timing demo discs (one per player) for the same tune.
# Each boots into MODE 5, prints the player name, and shows a coloured raster
# band whose height = the player's per-frame CPU time. Run from bench/.
#
# The two VGC discs INCLUDE the VGC players from the sibling vgm-player-bbc
# checkout (see vgc/raster_vgc.asm); the optimised one needs that repo's
# vgc-opt-player branch (lib/vgcplayer_opt.asm).
set -e
BEEBASM=${BEEBASM:-beebasm}
TUNE=${1:-../vgm/U_LOADER.vgm}
RP=64           # RING_PAGE for VGI rings at &4000 (below the MODE 5 screen &5800)
mkdir -p ../discs

echo "== pack data =="
python ../vgipacker.py "$TUNE" -o music.vgi          # VGI v2 -> music.vgi
# VGC: reuse cached .vgc if present, else pack it
SAFE=$(python -c "import re,sys,os;print(re.sub(r'[^A-Za-z0-9._-]','_',os.path.basename('$TUNE'))+'.vgc')")
if [ -f "_cache/$SAFE" ]; then cp "_cache/$SAFE" vgc/ghost.vgc;
else python ../vgmpacker.py "$TUNE" -o vgc/ghost.vgc; fi

echo "== build discs =="
"$BEEBASM" -i player.asm -D TEST=0 -D RING_PAGE=$RP -D VGI2=1 -D UNROLL=0 -D HARNESS=1 \
    -do ../discs/raster_vgi.ssd        -boot Player -title "VGI LOOP" -opt 3
"$BEEBASM" -i player.asm -D TEST=0 -D RING_PAGE=$RP -D VGI2=1 -D UNROLL=1 -D HARNESS=1 \
    -do ../discs/raster_vgi_unroll.ssd -boot Player -title "VGI UNR"  -opt 3
( cd vgc
  "$BEEBASM" -i raster_vgc.asm -D OPT=0 -do ../../discs/raster_vgc.ssd    -boot Player -title "VGC ORIG" -opt 3
  "$BEEBASM" -i raster_vgc.asm -D OPT=1 -do ../../discs/raster_vgcopt.ssd -boot Player -title "VGC OPT"  -opt 3 )

echo "== pad to 200 KB =="
python - <<'PY'
import os
for p in ("../discs/raster_vgi.ssd","../discs/raster_vgi_unroll.ssd",
          "../discs/raster_vgc.ssd","../discs/raster_vgcopt.ssd"):
    sz=os.path.getsize(p)
    if sz<204800:
        open(p,"ab").write(b"\x00"*(204800-sz))
    print("  %-30s %d bytes"%(p,os.path.getsize(p)))
PY
