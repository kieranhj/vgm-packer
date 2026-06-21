\ player.asm - 6502 incremental-decode SN76489 player (BBC Micro)
\ Prototype for docs/compression-analysis.md sec 12.4: validates that a
\ byte-aligned per-column LZSS can be decoded ONE value per stream per frame,
\ so the per-frame cost is bounded independently of match/run length.
\
\ 11 streams (one per SN76489 register column), each its own tiny LZSS with a
\ 256-byte ring window (8-bit offsets). Per frame we pull one byte from each
\ stream, rebuild the chip state and write it. The noise column carries 0x0f
\ "skip" markers so the LFSR is not reset on unchanged frames.
\
\ Build the disc:   beebasm -i player.asm -D TEST=0 -do music.ssd -boot !BOOT -title VGIPLAY
\ Build sim test:   beebasm -i player.asm -D TEST=1 -o player.bin -d -labels labels.txt

MIN_MATCH = 2
SKIP      = &0f
RINGHI    = RING_PAGE     \ rings at RING_PAGE..+&0AFF, one 256-byte page/stream
                          \ pass via -D RING_PAGE=&60 (real player) or &C0 (sim/big tunes)

\ zero page
fptr      = &70          \ stream read pointer (2)
ringptr   = &72          \ ring access pointer (2)
tmp       = &74
framelo   = &75
framehi   = &76
outp      = &77          \ TEST: output pointer (2)
fctr      = &79          \ TEST: frame countdown (2)

KFRAMES   = 512          \ TEST: frames to decode in the simulator
OUTBUF    = &4000        \ TEST: captured register rows (KFRAMES*11 bytes)

ORG &1900
GUARD RING_PAGE*256     \ code + INCBIN data must stay below the rings

\ ---------------------------------------------------------------------------
\ per-stream state (11 streams)
\ ---------------------------------------------------------------------------
.st_srcL  SKIP 11
.st_srcH  SKIP 11
.st_rem   SKIP 11        \ bytes left in current run (0 => fetch a new command)
.st_flag  SKIP 11        \ bit7 set => match mode, clear => literal mode
.st_copy  SKIP 11        \ ring read index while copying a match
.st_head  SKIP 11        \ ring write index
.regbuf   SKIP 11        \ this frame's 11 decoded register values

\ ---------------------------------------------------------------------------
\ entry
\ ---------------------------------------------------------------------------
.start
IF TEST
  JSR init_streams
  LDA #LO(OUTBUF) : STA outp
  LDA #HI(OUTBUF) : STA outp+1
  LDA #LO(KFRAMES): STA fctr
  LDA #HI(KFRAMES): STA fctr+1
.t_loop
  LDX #0
.t_col
  JSR decode             \ X=stream -> A=byte, X preserved
  PHA : TXA : TAY : PLA   \ Y = stream, A = byte
  STA (outp),Y
  INX : CPX #11 : BNE t_col
  CLC : LDA outp : ADC #11 : STA outp : BCC t_nc : INC outp+1
.t_nc
  LDA fctr : BNE t_dl : DEC fctr+1
.t_dl
  DEC fctr
  LDA fctr : ORA fctr+1 : BNE t_loop
.testdone
  JMP testdone           \ sim stops when PC reaches here
ELSE
  LDX #0                  \ print a banner before we take over the machine
.pr_loop
  LDA banner,X
  BEQ pr_done
  JSR &FFEE              \ OSWRCH
  INX
  BNE pr_loop
.pr_done
  SEI
  LDA #&FF : STA &FE43    \ System VIA DDRA = all outputs (sound data bus)
  JSR init_streams
  LDA music_data+4 : STA framelo
  LDA music_data+5 : STA framehi
.mloop
  JSR waitvsync
  JSR do_frame
  LDA framelo : BNE m_declo
  DEC framehi
.m_declo
  DEC framelo
  LDA framelo : ORA framehi : BNE mloop
  \ silence all four channels (attenuation = 15 = off)
  LDA #&9F : JSR sn
  LDA #&BF : JSR sn
  LDA #&DF : JSR sn
  LDA #&FF : JSR sn
  CLI
  RTS
ENDIF

\ ---------------------------------------------------------------------------
\ init the 11 stream pointers/state from the .vgi header
\ ---------------------------------------------------------------------------
.init_streams
  LDX #0
.is_loop
  TXA : ASL A : TAY              \ Y = stream*2
  LDA music_data+6,Y            \ offset lo (relative to file start)
  CLC : ADC #LO(music_data)
  STA st_srcL,X
  LDA music_data+7,Y            \ offset hi
  ADC #HI(music_data)
  STA st_srcH,X
  LDA #0
  STA st_rem,X
  STA st_flag,X
  STA st_copy,X
  STA st_head,X
  INX : CPX #11 : BNE is_loop
  RTS

\ ---------------------------------------------------------------------------
\ fetch one raw byte from stream X's compressed data, advancing its pointer
\ ---------------------------------------------------------------------------
.fetchbyte
  LDA st_srcL,X : STA fptr
  LDA st_srcH,X : STA fptr+1
  LDY #0
  LDA (fptr),Y
  INC st_srcL,X
  BNE fb_done
  INC st_srcH,X
.fb_done
  RTS

\ ---------------------------------------------------------------------------
\ decode one output byte from stream X (incremental). A=byte, X preserved.
\ Worst case: fetch command (+offset) then one copy/literal - bounded, no
\ dependence on match length (a long match emits one byte per call).
\ ---------------------------------------------------------------------------
.decode
  LDA st_rem,X
  BNE produce            \ still inside a run -> just emit the next byte
  JSR fetchbyte          \ new command byte (flags reflect fetchbyte's INC, not A,
                         \  so test the returned byte explicitly)
IF VGI2
  \ v2 tokens: 0=literal(7-bit), 10=run(off1,6-bit+ext), 11=match(6-bit+ext,off)
  CMP #&80
  BCC v2_lit
  CMP #&C0
  BCC v2_run
  AND #&3f               \ match length field
  CMP #&3f
  BNE v2_m_short
  JSR fetchbyte          \ ext: full length byte (65..255)
  STA st_rem,X
  JMP v2_m_off
.v2_m_short
  CLC : ADC #2           \ len = field+2 (2..64)
  STA st_rem,X
.v2_m_off
  JSR fetchbyte          \ offset
  STA tmp
  LDA st_head,X
  SEC : SBC tmp
  STA st_copy,X
  LDA #&80 : STA st_flag,X
  JMP produce
.v2_run
  AND #&3f               \ run length field
  CMP #&3f
  BNE v2_r_short
  JSR fetchbyte
  STA st_rem,X
  JMP v2_r_set
.v2_r_short
  CLC : ADC #2
  STA st_rem,X
.v2_r_set
  LDA st_head,X
  SEC : SBC #1           \ offset 1 (repeat last byte), no offset byte read
  STA st_copy,X
  LDA #&80 : STA st_flag,X
  JMP produce
.v2_lit
  AND #&7f : CLC : ADC #1
  STA st_rem,X
  LDA #0 : STA st_flag,X
  JMP produce
ELSE
  CMP #&80               \ v1 tokens: 0=literal(7-bit), 1=match(7-bit+off)
  BCS dmatch
  AND #&7f               \ literal run length-1
  CLC : ADC #1
  STA st_rem,X
  LDA #0 : STA st_flag,X
  JMP produce
.dmatch
  AND #&7f               \ match length-MIN_MATCH
  CLC : ADC #MIN_MATCH
  STA st_rem,X
  JSR fetchbyte          \ offset
  STA tmp
  LDA st_head,X
  SEC : SBC tmp          \ ring read index = head - offset
  STA st_copy,X
  LDA #&80 : STA st_flag,X
ENDIF
.produce
  LDA st_flag,X
  BMI dcopy
  JSR fetchbyte          \ literal byte
  JMP dstore
.dcopy
  LDA #0 : STA ringptr
  TXA : CLC : ADC #RINGHI : STA ringptr+1
  LDY st_copy,X
  LDA (ringptr),Y
  INC st_copy,X
.dstore
  STA tmp                \ the decoded byte
  LDA #0 : STA ringptr
  TXA : CLC : ADC #RINGHI : STA ringptr+1
  LDY st_head,X
  LDA tmp
  STA (ringptr),Y
  INC st_head,X
  DEC st_rem,X
  LDA tmp
  RTS

IF TEST = 0
\ ---------------------------------------------------------------------------
\ one frame: decode all 11 streams, then write the SN76489
\ ---------------------------------------------------------------------------
.do_frame
  LDX #0
.dfl
  JSR decode
  STA regbuf,X
  INX : CPX #11 : BNE dfl

  LDA regbuf+0  : ORA #&80 : JSR sn      \ tone0 freq lo (latch)
  LDA regbuf+1             : JSR sn      \ tone0 freq hi (data, bit7=0)
  LDA regbuf+2  : ORA #&A0 : JSR sn      \ tone1 freq lo
  LDA regbuf+3            : JSR sn       \ tone1 freq hi
  LDA regbuf+4  : ORA #&C0 : JSR sn      \ tone2 freq lo
  LDA regbuf+5            : JSR sn       \ tone2 freq hi
  LDA regbuf+6  : CMP #SKIP : BEQ no_noise
  ORA #&E0 : JSR sn                      \ noise control (only when changed)
.no_noise
  LDA regbuf+7  : ORA #&90 : JSR sn      \ vol0
  LDA regbuf+8  : ORA #&B0 : JSR sn      \ vol1
  LDA regbuf+9  : ORA #&D0 : JSR sn      \ vol2
  LDA regbuf+10 : ORA #&F0 : JSR sn      \ vol3 (noise)
  RTS

\ ---------------------------------------------------------------------------
\ wait for vertical sync by polling System VIA IFR bit 1 (CA1 = 6845 vsync)
\ ---------------------------------------------------------------------------
.waitvsync
  LDA #2
.wv
  BIT &FE4D
  BEQ wv
  STA &FE4D              \ clear the vsync interrupt flag
  RTS

\ ---------------------------------------------------------------------------
\ write A to the SN76489 (System VIA port A + addressable latch line 0 = /WE)
\ interrupts are disabled for the whole tune so the OS keyboard scan cannot
\ collide on port A.
\ ---------------------------------------------------------------------------
.sn
  STA &FE4F             \ data byte onto port A
  LDA #0  : STA &FE40   \ latch line 0 -> 0 : sound write enable low
  LDX #&18
.sn_d
  DEX : BNE sn_d        \ hold ~ a few us for the slow sound chip
  LDA #8  : STA &FE40   \ latch line 0 -> 1 : write enable high
  RTS

.banner
  EQUS "Incremental VGI player (sec 12.4)", 13
  EQUS "Ghost House - 51s, decoding from one bank", 13, 13, 0
ENDIF

\ ---------------------------------------------------------------------------
\ packed music, appended to the image; init_streams reads its header
\ ---------------------------------------------------------------------------
.music_data
INCBIN "music.vgi"
.end_of_image

SAVE "Player", &1900, end_of_image, start
