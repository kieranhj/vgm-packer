\ sim.asm - standalone build of the VGC player for cycle measurement in py65.
\ -D OPT=0 builds the original player; -D OPT=1 the optimised one (vgcplayer_opt.asm).
.zp_start
ORG &70
GUARD &9f
INCLUDE "vgcplayer_config.h.asm"
INCLUDE "vgcplayer.h.asm"
.zp_end

ORG &1100
GUARD &7c00
.start
IF OPT
INCLUDE "vgcplayer_opt.asm"
ELSE
INCLUDE "vgcplayer.asm"
ENDIF

.vgm_buffer_start
ALIGN 256
.vgm_stream_buffers
  SKIP 2048
.vgm_buffer_end
.vgm_data
INCBIN "ghost.vgc"
.end
SAVE "Vgc", start, end, start
