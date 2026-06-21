\ sim_vgc.asm - minimal harness to assemble the existing VGC player standalone
\ so it can be driven from py65 for a cycle-cost comparison. No main loop: the
\ simulator calls vgm_init once then vgm_update per frame.

.zp_start
ORG &70
GUARD &8f
INCLUDE "lib/vgcplayer_config.h.asm"
INCLUDE "lib/vgcplayer.h.asm"
.zp_end

ORG &1100
GUARD &7c00
.start
INCLUDE "lib/vgcplayer.asm"

.vgm_buffer_start
ALIGN 256
.vgm_stream_buffers
  SKIP 2048           \ 8 x 256-byte decode buffers
.vgm_buffer_end
.vgm_data
INCBIN "ghost.vgc"
.end

SAVE "Vgc", start, end, start
