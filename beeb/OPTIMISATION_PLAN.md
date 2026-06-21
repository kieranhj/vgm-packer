# VGI player — runtime optimisation plan

*Cycle-cost analysis of `player.asm` (the v2 incremental decoder) and a ranked
plan to reduce per-frame cycles. Counts are 6502 cycles @ 2 MHz; one 50 Hz frame
= 40000 cycles. All "decode-only" numbers have the SN write stubbed; "full-frame"
includes the 11 sound-chip writes.*

## Where the time goes (Ghost House, measured in py65)

| | decode-only | full-frame (incl. sound) |
|---|--:|--:|
| before any optimisation | min 1466 / max 2874 | min 2886 / mean 2922 / max 4294 |
| **after Tier 1 (done)** | **min 1158 / max 2566** | **min 2578 / mean 2616 / max 3986** |

Two roughly equal halves dominate the frame: **decode (~1158–1237)** and **the 11
sound writes (~1300)**. The decode floor (all 11 streams continuing a run) is the
per-stream produce+store machinery, run 11×/frame; the sound cost is almost
entirely the `sn` busy-wait delay.

## Tier 1 — ring-pointer + register byte (DONE, measured −308/frame)

The common match-continue path set up the ring pointer **twice** (identical code
in `dcopy` and `dstore`) and round-tripped the decoded byte through `tmp` three
times. Fixed by computing `ringptr` once per `decode` (low byte is always 0, set
once in `init_streams`; high byte = `RING_PAGE+X` set once in `produce`) and
keeping the byte in `A` to the return. Per-stream cost 97 → 69 cycles.

Measured: **−308 cycles/frame** (28×11) — decode floor 1466 → **1158 (−21%)**,
full-frame mean 2922 → **2616**. Byte-exact (sim_test + sim_test_player PASS).
Low risk, no code-size or RAM change, still bounded.

## Tier 2 — the sound writes (biggest remaining lever, ~1300/frame)

`sn` holds the /WE strobe low with a busy-wait (`LDX #&18` = 24 iterations ≈ 120
cycles) ×11 calls ≈ **1300 cycles/frame** — now the largest single item, and pure
waiting. Two ways to reclaim it:

1. **Pipeline the strobe behind decode (recommended, hardware-safe).** Restructure
   `do_frame` from "decode all 11, then write all 11" to **decode-one / write-one**,
   issuing the next chip write's settle wait *as real decode work* of the following
   register instead of a busy loop: put data on the bus + pull /WE low, do the next
   register's decode (~100 cycles ≫ the chip's ~8 µs/16-cycle need), then raise /WE.
   The settle time becomes free. Est. **−~1300/frame**, and it's *more* correct than
   a guessed delay because the wait is genuine elapsed time.
2. **Right-size the delay.** The SN76489 needs /WE low only ~16 CPU cycles; `&18`
   is 4–8× too much. Dropping to ~`&04` saves ~1100/frame. Simpler but still a
   guessed constant — verify on hardware. (The vgm-player-bbc `sn_write` uses no
   explicit loop at all.)

Either needs confirmation on a real BBC / cycle-exact emulator (the strobe timing
is the one thing the functional sim can't check), so it's gated behind hardware
validation — but it roughly **halves the full-frame cost**.

## Tier 3 — unroll + inline the decoder (~−350/frame decode, +~1 KB code)

The per-stream wrapper pays JSR+RTS (12) and the loop's INX/CPX/BNE (7) every
stream, and recomputes the ring page (`TXA:CLC:ADC:STA`, 9) every `decode`.
Unrolling the 11-stream loop into 11 specialised, inlined decoders lets each:

- drop the JSR/RTS (−12/stream) and loop control (−7/stream);
- use **absolute,Y with a constant ring page** (`LDA RINGn,Y` / `STA RINGn,Y`,
  4–5 cyc) instead of `(ringptr),Y` plus the page setup (−~9/stream and no pointer
  maintenance);
- hold per-stream `rem`/`flag`/`copy`/`head`/`src` in fixed zero-page slots.

Estimated decode floor 1158 → **~750–850** (another ~−350/frame). Cost: code grows
from ~0.6 KB to ~1.5–2 KB (11 copies of the body). Worthwhile if cycles matter more
than code in the bank; can be a build option (`-D UNROLL=1`) so the compact looped
build stays available.

## Tier 4 — smaller levers / knobs

- **Streamline `fetchbyte` for literal streams.** Literal-continue frames rebuild
  `fptr` from `st_src` every byte (~31 cyc). Holding the source pointer in zero
  page per stream (natural under Tier 3) makes a literal byte ~a single `(src),Y`
  read. Helps the ~22% literal frames.
- **Combine dispatch loads.** `decode` does `LDA st_rem / LDA st_flag` (two
  abs,X loads + branches, 14 cyc) every stream. A packed state byte (rem in low
  bits, mode in sign) could fuse these — small, fiddly.
- **8 streams instead of 11 (size/speed knob).** Combining tone lo/hi into one
  16-bit stream is 3 fewer decode contexts (~−250/frame of per-stream overhead) but
  was measured ~larger on ratio (§COMPRESSION_REPORT). A pure speed-for-size trade,
  not recommended unless cycles are critical.

## Suggested order & expected result

| step | decode-only floor | full-frame mean | risk |
|---|--:|--:|---|
| current (Tier 1 done) | 1158 | 2616 | shipped |
| + Tier 2 (pipeline sound) | 1158 | **~1300** | hardware-verify |
| + Tier 3 (unroll/inline) | **~800** | ~950 | code size |
| + Tier 4 (literals, etc.) | ~750 | ~900 | small |

Tier 2 is the highest value (halves the frame) and should come next, pending a
hardware/emulator check of the strobe. Tier 3 is the biggest *decode* win if the
extra ~1 KB of code is acceptable, and pairs naturally with Tier 4. Even Tier 1
alone already widens the gap to the VGC player (whose decode-only worst case is
5396): the v2 player now sits at ~1158 typical / 2566 worst decode, i.e. well
under ⅓ of VGC's spike.
