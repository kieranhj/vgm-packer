\ vgcplayer_opt.asm - optimised VGC player (huffman omitted).
\ Based on Simon Morris's vgm-player-bbc/lib/vgcplayer.asm. The key change is
\ removing the per-decoded-byte zero-page CONTEXT SWAP that the original does in
\ vgm_get_register_data (~164 cyc/byte, 38% of the frame). Instead each stream's
\ LZ state stays RESIDENT and is accessed in place:
\   - X holds the stream index for the whole decode (never clobbered);
\   - literal/match counts are abs,X in vgm_streams (no copy to ZP);
\   - the window buffer is abs,Y with the page self-modified once per decode,
\     the read/write indices held abs,X (inc in place) - and inlined (no JSR);
\   - only the stream read pointer is loaded to ZP once and saved once.
\ get_register_data preserves X, so the vgm_temp save/restore is dropped too.
\ Produces byte-identical SN76489 output to the original (verified in measure.py).

VGM_STREAMS = 8

.vgm_streams      skip VGM_STREAMS*8   \ resident per-stream state (declared first
                                       \ so the abs,X aliases below can resolve)

\ resident per-stream state (stride 8); X = stream index
src_lo = vgm_streams + VGM_STREAMS*0
src_hi = vgm_streams + VGM_STREAMS*1
lit_lo = vgm_streams + VGM_STREAMS*2
lit_hi = vgm_streams + VGM_STREAMS*3
mat_lo = vgm_streams + VGM_STREAMS*4
mat_hi = vgm_streams + VGM_STREAMS*5
wsrc   = vgm_streams + VGM_STREAMS*6
wdst   = vgm_streams + VGM_STREAMS*7

\ zero page (from VGM_ZP in the .h)
zp_src  = VGM_ZP + 0      \ stream read ptr (2)
zp_tl   = VGM_ZP + 2      \ fetch_count lo
zp_th   = VGM_ZP + 3      \ fetch_count hi
zp_tmp  = VGM_ZP + 4      \ scratch

.vgm_start

\ ---------------- user API ----------------
.vgm_init
{
    sta vgm_buffers
    lda #0 : ror a : sta vgm_loop
    stx vgm_source+0
    sty vgm_source+1
    jmp vgm_stream_mount
}

.vgm_update
{
    lda vgm_finished
    bne exit
    lda#3:jsr vgm_update_register1   \ Tone3 (carries EOF marker)
    bcc more_updates
    cpy #&08
    beq finished
.more_updates
    lda#7:jsr vgm_update_register1   \ Volume3
    lda#1:jsr vgm_update_register2   \ Tone1
    lda#2:jsr vgm_update_register2   \ Tone2
    lda#4:jsr vgm_update_register1   \ Volume0
    lda#5:jsr vgm_update_register1   \ Volume1
    lda#6:jsr vgm_update_register1   \ Volume2
    lda#0:jsr vgm_update_register2   \ Tone0, returns 0 in X
    txa
.exit
    rts
.finished
    lda vgm_loop
    beq no_looping
    ldx vgm_source+0
    ldy vgm_source+1
    lda vgm_loop : asl a
    lda vgm_buffers
    jsr vgm_init
    jmp vgm_update
.no_looping
    sty vgm_finished
    jmp sn_reset
}

\ ---------------- sound chip ----------------
.sn_write
{
    ldx #255 : stx &fe43
    sta &fe4f
    inx : stx &fe40
    lda &fe40 : ora #8 : sta &fe40
    rts
}
.sn_reset
{
    lda #&9f : jsr sn_write
    lda #&bf : jsr sn_write
    lda #&df : jsr sn_write
    lda #&ff : jmp sn_write
}

\ ---------------- VGC parsing (init only) ----------------
.vgm_next_block
{
    ldy #0
    lda (zp_block_data),Y : clc : adc #4 : sta zp_block_size+0
    iny
    lda (zp_block_data),Y : adc #0 : sta zp_block_size+1
    lda zp_block_data+0 : clc : adc zp_block_size+0 : sta zp_block_data+0
    lda zp_block_data+1 : adc zp_block_size+1 : sta zp_block_data+1
    rts
}

.vgm_stream_mount
{
    stx zp_block_data+0
    sty zp_block_data+1
    ldy #3
    lda (zp_block_data), y
    sta vgm_flags
    lda zp_block_data+0 : clc : adc #7 : sta zp_block_data+0
    bcc no_block_hi
    inc zp_block_data+1
.no_block_hi
    ldx #0
    stx vgm_finished
.block_loop
    lda zp_block_data+0 : clc : adc #4 : sta src_lo, x
    lda zp_block_data+1 : adc #0 : sta src_hi, x
    lda #0
    sta lit_lo, x
    sta lit_hi, x
    sta mat_lo, x
    sta mat_hi, x
    sta wsrc, x
    sta wdst, x
    lda #1 : sta vgm_register_counts, x
    jsr vgm_next_block
    inx : cpx #8 : bne block_loop
    rts
}

\ ---------------- workspace ----------------
.vgm_buffers      equb 0
.vgm_finished     equb 0
.vgm_flags        equb 0
.vgm_loop         equb 0
.vgm_source       equw 0
.vgm_register_counts skip 8
.vgm_register_headers
    EQUB &80 + (0<<5) : EQUB &80 + (1<<5) : EQUB &80 + (2<<5) : EQUB &80 + (3<<5)
    EQUB &90 + (0<<5) : EQUB &90 + (1<<5) : EQUB &90 + (2<<5) : EQUB &90 + (3<<5)

IF ENABLE_VGM_FX
.vgm_fx SKIP 11
VGM_FX_TONE0_LO = 0
ENDIF

\ ---------------- register update (RLE wrapper) ----------------
\ get_register_data preserves X, so no vgm_temp dance is needed.
.vgm_update_register1
{
    tax
    clc
    dec vgm_register_counts,x
    bne skip_register_update
    jsr vgm_get_register_data    \ A=value, X=stream preserved
    tay
    and #&0f
    ora vgm_register_headers,x
    cmp #&ef
    beq skip_tone3
    jsr sn_write                 \ clobbers X
    ldx vgm_temp_x               \ restore stream index (sn_write clobbered X)
.skip_tone3
    tya
    lsr a : lsr a : lsr a : lsr a
    clc : adc #1
    sta vgm_register_counts,x
IF ENABLE_VGM_FX
    tya : and #&0f : ora vgm_register_headers,x
    cmp #&ef
    beq skip_tone3_fx
    and #&0f
    sta vgm_fx,x
.skip_tone3_fx
ENDIF
    sec
}
.skip_register_update
{
    rts
}

.vgm_update_register2
{
    jsr vgm_update_register1
    bcc skip_register_update
    txa
    jsr vgm_get_register_data
IF ENABLE_VGM_FX
    sta vgm_fx+8,x
ENDIF
    jmp sn_write
}

\ vgm_temp_x: sn_write clobbers X, so register1 stashes the stream id here once.
.vgm_temp_x equb 0

\ ---------------- the resident LZ decoder ----------------
\ A = stream id (0-7); returns decoded byte in A; preserves X = stream.
.vgm_get_register_data
{
    tax
    stx vgm_temp_x               \ so register1 can restore X after sn_write
    txa : clc : adc vgm_buffers  \ window buffer page for this stream
    sta win_fetch+2              \ **SMC** hi of abs,Y window instructions
    sta win_store_a+2
    sta win_store_b+2
    lda src_lo,x : sta zp_src+0
    lda src_hi,x : sta zp_src+1
    jsr lz_decode_byte
    pha
    lda zp_src+0 : sta src_lo,x
    lda zp_src+1 : sta src_hi,x
    pla
    rts
}

\ fetch a raw byte from this stream's compressed data (zp_src), advancing it
.lz_fetch_byte
{
    ldy #0
    lda (zp_src),y
    inc zp_src+0
    bne ok
    inc zp_src+1
.ok
    rts
}

\ multi-byte length: in A=lo init; out A=lo, zp_th=hi; preserves X
.lz_fetch_count
{
    ldy #0
    sty zp_th
    cmp #15
    bne done
    sta zp_tl
.loop
    jsr lz_fetch_byte
    pha
    clc : adc zp_tl : sta zp_tl
    lda zp_th : adc #0 : sta zp_th
    pla
    cmp #255
    beq loop
    lda zp_tl
.done
    rts
}

.lz_decode_byte
.try_literal
    lda lit_lo,x
    bne is_literal
    lda lit_hi,x
    beq try_match
.is_literal
    jsr lz_fetch_byte            \ literal byte -> A
    ldy wdst,x
.win_store_a
    sta &ff00,y                 \ **SMC hi** store to window
    inc wdst,x
    sta stashA+1                \ **SMC** save output byte
    inc lit_lo,x
    bne lit_done
    inc lit_hi,x
.lit_done
    bne end_literal
.begin_matches
    jsr lz_fetch_byte            \ match offset
    sta zp_tmp
    lda wdst,x
    sec : sbc zp_tmp
    sta wsrc,x
    lda mat_lo,x
    jsr lz_fetch_count           \ A=lo, zp_th=hi
    clc : adc #4 : sta mat_lo,x
    lda zp_th : adc #0 : sta mat_hi,x
.end_literal
.stashA
    lda #0                      \ **SMC**
    rts

.try_match
    lda mat_hi,x
    bne is_match
    lda mat_lo,x
    beq try_token
.is_match
    ldy wsrc,x
.win_fetch
    lda &ff00,y                 \ **SMC hi** read from window -> A
    inc wsrc,x
    ldy wdst,x
.win_store_b
    sta &ff00,y                 \ **SMC hi** store to window
    inc wdst,x
    sta stashAA+1               \ **SMC** save output
    lda mat_lo,x
    bne mskiphi
    dec mat_hi,x
.mskiphi
    dec mat_lo,x
.end_match
.stashAA
    lda #0                      \ **SMC**
    rts

.try_token
    jsr lz_fetch_byte            \ token
    sta zp_tmp
    and #&0f
    sta mat_lo,x
    lda #0 : sta mat_hi,x
    lda zp_tmp
    lsr a : lsr a : lsr a : lsr a
    jsr lz_fetch_count
    sta lit_lo,x
    lda zp_th : sta lit_hi,x
    lda lit_hi,x
    bne has_literals
    lda lit_lo,x
    bne has_literals
    jsr begin_matches
    jmp try_match
.has_literals
    clc
    lda lit_lo,x : eor #&ff : adc #1 : sta lit_lo,x
    lda lit_hi,x : eor #&ff : adc #0 : sta lit_hi,x
    jmp try_literal

.vgm_end

PRINT "opt vgm player size is", (vgm_end-vgm_start), "bytes"
