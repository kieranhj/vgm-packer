\ raster_vgc.asm - raster-timing harness for the VGC players (Model B / 6502).
\ -D OPT=0 = original vgcplayer.asm, -D OPT=1 = vgcplayer_opt.asm.
\ MODE 5; prints the player name; each frame raises the background (logical 0) to
\ a colour before vgm_update and drops it to black after - the band height = the
\ player's per-frame CPU time. After tom-seddon's cycle_exact_via_poll harness.
\ (ENABLE_HUFFMAN/ENABLE_VGM_FX come from vgcplayer_config.h.asm.)
OSWRCH   = &FFEE
palette  = &FE21
svia_ifr = &FE4D

ORG &70
GUARD &9f
INCLUDE "vgcplayer_config.h.asm"
INCLUDE "vgcplayer.h.asm"

ORG &1900
GUARD &5800
.start
IF OPT
RCOL = 3                  \ yellow
ELSE
RCOL = 1                  \ red
ENDIF
  LDA #22 : JSR OSWRCH : LDA #5 : JSR OSWRCH    \ MODE 5
  LDX #0
.h_curs
  LDA h_cursoroff,X : JSR OSWRCH : INX : CPX #10 : BNE h_curs
  LDX #0
.h_name
  LDA h_pname,X : BEQ h_named : JSR OSWRCH : INX : BNE h_name
.h_named
  SEI
  LDA #HI(vgm_stream_buffers)
  LDX #LO(vgc_data) : LDY #HI(vgc_data)
  SEC                       \ loop playback
  JSR vgm_init
.h_loop
  JSR waitvsync
  JSR h_delay_visible               \ vsync is ~38 scanlines above the visible top:
                                    \ wait 38*128=4864 cyc so the band is on-screen
  LDA #(RCOL EOR 7) : STA palette   \ band on  (logical 0 -> RCOL)
  JSR vgm_update
  LDA #(0 EOR 7)    : STA palette   \ band off (black)
  JMP h_loop
\ ~4864-cycle delay (38 scanlines) so the raster band lands in the visible area.
.h_delay_visible
  LDX #4
.hdv_o
  LDY #240
.hdv_i
  DEY : BNE hdv_i
  DEX : BNE hdv_o
  RTS

.h_cursoroff
  EQUB 23,1,0,0,0,0,0,0,0,0
.h_pname
IF OPT
  EQUS "VGC OPT (yellow)", 13, 0
ELSE
  EQUS "VGC ORIGINAL (red)", 13, 0
ENDIF

.waitvsync
  LDA #2
.wv
  BIT svia_ifr : BEQ wv
  STA svia_ifr
  RTS

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
.vgc_data
INCBIN "ghost.vgc"
.end

SAVE "Player", start, end, start
