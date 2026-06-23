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
*worst* frame, not the average. `.vgi` trades a little size (~1.4× `.vgc` on the
corpus, below) for a per-frame cost with almost no tail. The companion player
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
+0   'V','G','I',ver        magic + version (1 or 2)
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

## Self-verification

Every encoded column is round-tripped through **two independent decoders** and
asserted equal to the source before the file is written:

- a plain reference decoder (`v2_decode` / `lzss_decode`), and
- a **256-byte-ring decoder** (`v2_decode_ring` / `lzss_decode_ring`) that
  faithfully models the 6502 routine's ring/state machine.

If either ever disagrees, packing aborts with an assertion — so a `.vgi` that
writes successfully is guaranteed decodable by the documented state machine.

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
python vgipacker.py "song.vgm" -o "song.vgi"     # v2 (default)
python vgipacker.py "song.vgm" -o "song.vgi" --v1 # original greedy format
```

Run from the repo root (it imports the `modules/` package, like `vgmpacker.py`).
Only SN76489 PSG VGM files are supported.
