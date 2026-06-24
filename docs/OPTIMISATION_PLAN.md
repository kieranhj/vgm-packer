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

## Tier 2 — the sound writes (DONE, measured −1110/frame)

`sn` held the /WE strobe low with a busy-wait (`LDX #&18` ≈ 120 cycles) ×11 ≈
**1300 cycles/frame** of pure waiting. I replaced it with the **hardware-proven
no-busy-wait sequence from the shipped vgm-player-bbc `sn_write`** — the ~6-cycle
read-back of `&FE40` between /WE low and /WE high is sufficient settle on a real
BBC. This is simpler and lower-risk than a hand-pipelined strobe (same goal,
same saving, and already validated on hardware), so I took it over the pipeline
idea.

Measured: full-frame mean **2616 → 1506**, max 3986 → 2876 (10.0% → **7.2%** of a
frame). SN byte stream still byte-exact. (Strobe timing is the one thing the
functional sim can't prove, but this is the exact routine the existing player
ships, so the risk is minimal.)

## Tier 3 — unroll + inline the decoder (DONE, behind `-D UNROLL=1`)

The looped decoder pays JSR+RTS (12) and INX/CPX/BNE (7) per stream and rebuilds
the ring page every call. The unrolled build inlines all 11 streams so each:

- drops the JSR/RTS and loop control;
- uses **absolute,Y with a constant ring page** (`LDA rbase,Y` / `STA rbase,Y`)
  instead of `(ringptr),Y` plus pointer setup — no pointer maintenance at all;
- keeps the rare new-token parse shared in `newtoken` (X=stream) so only the
  hot common path is duplicated.

Measured (UNROLL=1): decode-only floor **1158 → 673**, full-frame mean **1506 →
1025**, max 2876 → 2487 (**6.2%** of a frame). Byte-exact over the whole tune.
Cost: code+state **580 → 1252 bytes** (+672). Gated by `-D UNROLL=1` so the
compact looped build (580 B) stays the default; `music.ssd` = looped,
`music_unroll.ssd` = unrolled.

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

## Result (Ghost House, measured)

| build | decode-only floor | full-frame min/mean/max | code+state |
|---|--:|--:|--:|
| original | 1466 | 2886 / 2922 / 4294 | 536 |
| + Tier 1 (ring ptr / register byte) | 1158 | 2578 / 2616 / 3986 | 536 |
| + Tier 2 (no-delay sn) | 1158 | 1468 / 1506 / 2876 | 580 |
| + Tier 3 (`-D UNROLL=1`) | **673** | **983 / 1025 / 2487** | 1252 |

Net: full-frame mean **2922 → 1025 (−65%)**, worst frame **4294 → 2487** (10.7%
→ **6.2%** of a 50 Hz frame). For scale, the VGC player's decode-only worst case
is 5396; the unrolled v2 player's *whole-frame* worst (incl. sound) is now less
than half that.

## Tier 4 — remaining smaller levers (not done)

- Inline `fetchbyte` for literal streams (hold the source pointer in zero page),
  helping the ~22% literal frames.
- Fuse the `rem`/`flag` dispatch loads into one packed state byte.
- 8-stream tone-combine: fewer contexts (faster) but worse ratio — a pure
  speed-for-size knob, not recommended.

These are diminishing returns now that the frame is ~1 kcyc; the big wins (Tier
1–3) are banked. The one open item is confirming the Tier-2 strobe and the whole
player on real hardware / a cycle-exact emulator.
