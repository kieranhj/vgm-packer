<!--
AI-GENERATED DOCUMENT
This document was written with the assistance of an AI model:
Claude Opus 4.8 (claude-opus-4-8). It describes vgipacker.py and the .vgi
format, both produced with the same assistance.
-->

# The `.vgi` format (incremental-decode SN76489)

`.vgi` is an alternative to `.vgc` produced by `vgipacker.py`. Both pack the
same SN76489 PSG register data; they differ entirely in **what the 6502 decoder
has to do per frame**.

- **`.vgc`** (`vgmpacker.py`) runs **RLE then LZ4** on each stream. It is the
  smaller file, but the decode cost is *spiky*: most frames only decrement an
  RLE run counter (nearly free), while occasional frames have to refill several
  LZ4 tokens at once (expensive). The worst frames are several times the median.
- **`.vgi`** (`vgipacker.py`) runs a tiny byte-aligned **LZSS per register
  column with no RLE pre-pass**, decoded **one value per stream per frame**. A
  long match or run is emitted one byte at a time across successive frames, so a
  single frame never decodes more than the start of one token per stream. The
  per-frame cost is therefore **bounded independently of match/run length** —
  low, flat and predictable.

That predictability is the whole point: a raster-budgeted demo cares about the
*worst* frame, not the average. `.vgi` v1 and v2 trade a little size (~1.4×
`.vgc` on the corpus, below) for a per-frame cost with almost no tail. **v3
mostly buys that size back**: it is 21.5-28.6% smaller than v2 over ten tunes
while decoding *three fewer streams*, which lands it at 1.02-1.09× `.vgc` on
three of the five tunes where both exist, 1.39× on one, and *ahead* of `.vgc`
on the fifth (*Dead On Time*, 4,610 against 5,950). With the player measured
(below) it is also faster than the `.vgc` player at both the mean and the worst
frame. The companion player
and the full playback-cost measurement live in the `vgm-player-bbc` repo
(`lib/vgiplayer.asm`, `docs/vgi-player.md`).

## Column model

The VGM is de-interleaved into **11 register columns** — exactly the SN76489
register set, one byte per frame each:

| col | register | col | register |
|--:|---|--:|---|
| 0 | tone0 freq lo | 6 | noise control |
| 1 | tone0 freq hi | 7 | vol0 |
| 2 | tone1 freq lo | 8 | vol1 |
| 3 | tone1 freq hi | 9 | vol2 |
| 4 | tone2 freq lo | 10 | vol3 (noise) |
| 5 | tone2 freq hi | | |

Each column is compressed independently with its own LZSS over a **256-byte ring
window** (so offsets are a single byte and the player needs only a 256-byte
buffer per stream). The **noise column (6)** keeps a `0x0f` "skip" marker for
frames whose noise value is unchanged, so the player can avoid re-writing the
noise register — writing it always restarts the chip's LFSR.

Each column decodes to exactly `nframes` bytes, so no end-of-stream marker is
needed. (Unlike `.vgc`, which uses an `0x08` EOF marker on the tone-3 stream.)

## File layout (little-endian, loaded as one blob)

```
+0   'V','G','I',ver        magic + version (1 or 2; v3 has its own layout)
+4   nframes (16-bit)
+6   11 x stream offset (16-bit, relative to file start)
+28  the 11 LZSS streams, concatenated
```

`vgm_init` in the player reads the header, biases each 16-bit offset by the load
address to get 11 absolute stream pointers, and zeroes each stream's decode
state.

## Token formats

### v1 — the original greedy LZSS

```
0LLLLLLL          literal run, L+1 literal bytes follow              (1..128)
1LLLLLLL off      match, length L+2 (2..129), then one offset byte   (1..255)
                  copy `length` bytes from (head - offset) in the ring
```

### v2 — the default (`vgipacker.py` emits this unless `--v1`)

```
0LLLLLLL          literal run, L+1 literal bytes follow              (1..128)
10LLLLLL [E]      RUN (offset 1 / repeat last byte):
                    LLLLLL < 63  -> length = LLLLLL + 2   (2..64)
                    LLLLLL == 63 -> length = E (a full byte, 65..255)
                  no offset byte
11LLLLLL [E] off  MATCH: length as above, then one offset byte       (1..255)
```

Two changes from v1 buy the size win:

1. **A dedicated RUN token** for offset-1 (held value) matches — 16% of all v1
   matches were offset-1, where the offset byte was pure overhead. The RUN token
   drops it, and on the player it shares the match copy path (it just sets the
   copy index to `head − 1` and skips the offset fetch), so it is *cheaper* to
   decode than a match, not dearer.
2. **A single extension byte** carries lengths up to 255, instead of splitting a
   long held note into many 2-byte tokens.

Length is **capped at 255** so the player's per-stream run counter stays 8-bit
and the start of any token reads at most `cmd + ext + offset = 3 bytes`. That
cap is the reason the worst-case frame barely moves between v1 and v2. v2 also
uses an **optimal (dynamic-programming) parse** rather than greedy.

## v3 — indexed tone periods (`--v3`)

v3 keeps the v2 token stream **exactly**. What changes is which columns exist,
and it is worth **21.5–28.6% of the file** while making the player *cheaper*,
not dearer.

### Why: the split is in the wrong place

The finding came from outside this repo. `arkos-player-bbc` compresses AY
register logs (Arkos's FAP format) and SN logs (`.vgc`/`.vgi`) of the *same
tunes*, and its `tools/stream_cost.py` put one coder over both: **the SN log of
a tune costs 1.76× to 2.06× the AY log of it**, before any compressor is
chosen. The cause is where each chip splits a period.

- The AY's 12-bit period splits **8 + 4**: the fine byte carries the churn and
  the coarse nibble is nearly static — on Rhino's *Acid Demo* it changes on
  **3.3%** of frames.
- The SN's 10-bit period splits **4 + 6**: *both* halves are active — 40.1% and
  43.8% of frames on the same tune.

A per-column LZ pays much less for one busy column beside one static column
than for two moderately busy ones. Re-splitting the SN period 8 + 2 to imitate
the AY recovers 19.5–27.6% of the tone bytes on its own. v3 goes further and
removes the split: since a tune only ever uses a few dozen to a couple of
hundred distinct periods per channel, **one byte of index per channel** names
them all.

### Column model

**8 columns** instead of 11:

| col | contents | col | contents |
|--:|---|--:|---|
| 0 | tone0 period index | 4 | vol0 |
| 1 | tone1 period index | 5 | vol1 |
| 2 | tone2 period index | 6 | vol2 |
| 3 | noise control (`0x0f` skip, as v2) | 7 | vol3 (noise) |

plus a **period table**: for each entry, the low nibble and the high 6 bits the
chip wants. The latch bits are **not** baked in — the player still ORs
`#&80/&a0/&c0` as it does today, which costs 2 cycles and lets all three
channels share one table.

The three channels **share one table** whenever the union of their periods fits
a byte index; it is then always the smaller table, and the streams are
byte-for-byte the same size either way. (A per-column LZ matches *exact
repeats*, so relabelling the alphabet cannot change what it finds — measured
identical, 3,007 and 9,192 bytes both ways, on two tunes.) Otherwise each
channel gets its own — `Targhan - Crtc` needs that, at 153/161/187 distinct
periods for a union of 272. **The player never has to know which happened**:
the header names a low and a high table per channel, and a shared table is
simply the same offsets repeated. If a single channel ever exceeded 256 the
packer refuses the tune and says to pack it as v2; none of the ten tunes
measured comes close.

### File layout (little-endian, loaded as one blob)

```
+0   'V','G','I',3
+4   nframes (16-bit)
+6   8 x stream offset (16-bit, relative to file start)
+22  6 x table offset (16-bit, relative to file start): low table then high
     table, per channel. Channels sharing a table simply repeat its offsets.
+34  the tables, then the 8 LZSS streams
```

Each table is `N` low-nibble bytes followed by `N` high-6-bit bytes, and the
header names **both halves outright** rather than a base and a count. That is
what keeps the player's mount to "add the load address, store": no arithmetic,
no entry count, and no shared-versus-per-channel special case.

### What the player does — built and measured

`lib/vgiplayer.asm` in the
[vgm-player-bbc](https://github.com/simondotm/vgm-player-bbc) project builds for
v3 with `-D VGI_V3=1`. Per tone channel, one stream decode disappears and two
indexed loads replace it:

```
    VGI_DECODE_U 0          ; the period index
    tay                     ; sn_write clobbers X but never Y
.v3_t0lo
    lda &ffff,y : ora #&80 : jsr sn_write
.v3_t0hi
    lda &ffff,y :            jsr sn_write
```

The six operands are poked with the biased table pointers at mount, so a lookup
costs one `absolute,Y` read and nothing else. A steady-state `VGI_DECODE_U` is
about **59 cycles**, and three of them go.

Measured over all 9,602 frames of `acid_demo` (`test/vgi/measure.py`, cycles at
2 MHz, including the SN76489 writes):

| player | min | mean | p99 | max |
|---|--:|--:|--:|--:|
| VGC (stock) | 294 | 1788 | 4022 | **5321** |
| VGI v2 looped | 1483 | 1572 | 2219 | 2677 |
| VGI v2 unrolled | 1052 | 1149 | 1863 | 2377 |
| VGI v3 looped | 1214 | 1277 | 1762 | 2125 |
| VGI v3 unrolled | 917 | **985** | 1511 | **1894** |

So v3 is cheaper than v2 everywhere — 295 cycles at the mean and 552 at the
worst frame for the looped build — and **VGI v3 unrolled beats the stock VGC
player at both ends**, mean 985 against 1788 and worst frame 1894 against 5321,
on a file about the same size. The ring also drops from 11 pages to 8,
**2,816 → 2,048 bytes**, and the unrolled v3 player is 46 bytes *smaller* than
unrolled v2.

`vgm_init` reads the new header — 8 stream offsets and 6 table offsets, all
biased by the load address — and checks the version byte so a v2 file handed to
a v3 build is refused rather than played as noise. The token decoder does not
change at all.

### Measured

Ten tunes, packed both ways with `vgipacker.py` today. Whole files, including
headers and tables. *Androids* is this repo's own `example/`, so it is the row
anyone can reproduce from a clone; the next four are the player project's
corpus (`vgm-player-bbc/music/vgm`); the last five are Arkos songs rendered to
SN76489 through `ym2sn.py` by `arkos-player-bbc/tools/compare_formats.py`.

| tune | frames | v2 | v3 | |
|---|--:|--:|--:|--:|
| Androids (`example/`) | 8,835 | 11,551 | 8,449 | −26.9% |
| BotB Slimeball - Fluid Dynamics | 4,458 | 9,314 | 7,316 | −21.5% |
| Collision Chaos | 5,000 | 4,214 | 3,201 | −24.0% |
| acid_demo | 9,602 | 12,587 | 9,423 | −25.1% |
| ne7 - magic_beans | 6,976 | 9,267 | 6,876 | −25.8% |
| Rhino - Acid Demo 21 | 9,602 | 10,069 | 7,718 | −23.3% |
| Targhan - Dead On Time | 3,728 | 6,460 | 4,610 | −28.6% |
| Tom&Jerry - Edge Grinder | 17,448 | 22,292 | 16,079 | −27.9% |
| Targhan - Orion Prime L4 | 24,194 | 10,530 | 8,126 | −22.8% |
| Targhan - Crtc | 11,591 | 28,043 | 21,438 | −23.6% |
| **TOTAL** | | **124,327** | **93,236** | **−25.0%** |

The Slimeball, Collision Chaos and magic_beans v2 figures are the same ones in
the corpus table below, so the two measurements agree where they overlap.

The tone columns alone go from 6,999 bytes to 3,996 + 646 of table on *Acid
Demo 21*, and from 15,881 to 9,192 + 470 on *Edge Grinder*. Re-splitting 8 + 2
instead - keeping six tone columns and rebuilding the SN pair at run time - was
measured at 5,067 and 12,181 for the same two, so **indexing is worth roughly
another third again over imitating the AY split**, and it is the version that
also removes three streams.

One thing that was tried and **rejected**: pairing the two 4-bit volumes into
one byte, the direct analogue of the AY packing that makes FAP's `r1+r3` stream
cheap. It costs +1,031 bytes on *Acid Demo* and +12 on *Edge Grinder*, and only
saves on one tune of five. The AY merge works because both its halves are
nearly static; the SN volumes are both active, and merging two active columns
multiplies their alphabets.

## Self-verification

Every encoded column is round-tripped through **two independent decoders** and
asserted equal to the source before the file is written:

- a plain reference decoder (`v2_decode` / `lzss_decode`), and
- a **256-byte-ring decoder** (`v2_decode_ring` / `lzss_decode_ring`) that
  faithfully models the 6502 routine's ring/state machine.

If either ever disagrees, packing aborts with an assertion — so a `.vgi` that
writes successfully is guaranteed decodable by the documented state machine.

**v3 adds two more checks**, because it is the only version where the columns
in the file are not the columns the chip wants:

- the decoded indices are expanded back through the period tables and the
  resulting **11 SN76489 register columns are asserted byte-for-byte equal to
  what v2 would have written** — a v3 file cannot make the chip do anything
  different, only reach the same place in fewer bytes; and
- the finished file is then **parsed back from its bytes** (`read_v3`) and
  expanded again, which is the only step that exercises the header — the
  in-memory round-trip never touches it.

## Corpus size comparison

Whole test corpus (11 SN76489 tunes, 74052 frames total). v2 is the default;
`opt` is v1's format with the optimal parse (a free 1.9% with a byte-identical
decoder); `.vgc` is shown for scale.

| tune | v1 | opt | **v2** | .vgc |
|---|--:|--:|--:|--:|
| evil-influences | 31410 | 30779 | 28758 | 20085 |
| BotB Slimeball | 10029 | 9888 | 9314 | 5674 |
| Collision Chaos | 4821 | 4772 | 4214 | 2209 |
| Diagonals | 16486 | 16178 | 14965 | 11568 |
| Ghost House | 3916 | 3801 | 3589 | 2670 |
| U_LOADER | 3116 | 3067 | 3015 | 3537 |
| VE3 | 25776 | 25109 | 23774 | 18528 |
| intro_test | 1686 | 1672 | 1447 | 771 |
| main_test | 9654 | 9507 | 9034 | 6794 |
| ne7-magic_beans | 10100 | 9941 | 9267 | 5708 |
| outro_test | 5107 | 5065 | 4595 | 2564 |
| **TOTAL** | **122101** | **119779** | **111972** | **80108** |
| vs v1 | 1.000× | 0.981× | **0.917×** | 0.656× |
| vs .vgc | 1.52× | 1.50× | **1.40×** | 1.00× |

So v2 is **8.3% smaller than v1** for an unchanged decode profile, and lands at
**~1.4× the size of `.vgc`** — the cost of dropping the RLE layer that gives
`.vgc` its size but also its per-frame spikes.

A **16-bit-offset / larger-window** mode reaches ~0.78× (it could even beat
`.vgc`) because roughly half of all matches reach further back than 255 bytes,
but it needs a >256-byte ring per stream and a second offset read — i.e. it
trades the bounded-RAM property. It is left as a documented future knob, not the
default.

## Usage

```
python vgipacker.py "song.vgm" -o "song.vgi"      # v2 (default)
python vgipacker.py "song.vgm" -o "song.vgi" --v1 # original greedy format
python vgipacker.py "song.vgm" -o "song.vgi" --v3 # indexed tone periods
```

Run from the repo root (it imports the `modules/` package, like `vgmpacker.py`).
Only SN76489 PSG VGM files are supported.
