# Improving VGI compression without losing the bounded/consistent runtime

*Investigation report. Goal: shrink the `.vgi` stream (currently 1.52× the size
of `.vgc`) while preserving VGI's headline property — a low, flat, bounded
per-frame decode cost (§12.4 of `docs/compression-analysis.md`).*

## TL;DR

A new token format (**"v2"**) cuts the corpus `.vgi` by **8.3%** (1.52× → **1.40×**
`.vgc`) with the per-frame decode distribution essentially **unchanged**: median,
p90 and p99 are identical to v1, and the worst frame rises only from 2787 to
**3064 cycles (7.7% of a 50 Hz frame)** — still less than **⅔ of the VGC player's
5396** spike. Code grows 58 bytes; decode buffers and zero page are unchanged.
**Recommendation: adopt v2.**

## The constraint that rules out the obvious win

VGC is smaller mainly because it runs **RLE before LZ4**. But an RLE-skip layer is
exactly what *creates* per-frame variance: most frames just decrement a run
counter (near-free) and occasional frames LZ-decode a fresh token (expensive) —
which is *why* the VGC player spikes (§3, and the measured 5396-cycle tail). VGI's
flat cost comes precisely from **not** having that layer: every frame does the
same bounded LZSS step. So the search space is "make the per-column LZSS itself
smaller" — without adding any skip/RLE layer that would re-introduce variance.

## Where v1's bytes go (measured, whole corpus)

| | tokens | bytes | note |
|---|--:|--:|---|
| matches | 47306 | 94612 | **77.7% of output** — 2 B each (cmd+offset), avg len 16.8 |
| literals | 9581 hdr + 17600 data | 27181 | 22.3% |

Two facts drove the design:
- **16.1% of matches are offset‑1 "runs"** (held values) — for those the offset
  byte is pure overhead.
- only **3.9%** of matches hit the 129‑length cap, but long held notes that do get
  split into many 2‑byte tokens.

## Approaches tried

| approach | corpus `.vgi` | vs v1 | runtime impact | verdict |
|---|--:|--:|---|---|
| **v1** (greedy LZSS, shipped) | 122101 | 1.000× | baseline | — |
| **optimal parse**, same format | 119779 | 0.981× | **decoder identical** | free 1.9%, keep |
| extended match length only | ~ | small | +≤1 read on long-match start | folded into v2 |
| **v2 = run‑token + ext‑len + optimal** | **111972** | **0.917×** | max 2787→3064, dist. unchanged | **adopt** |
| 8 streams (combine tone lo/hi) | larger (≈0.94× on Ghost House but worse overall) | — | fewer contexts/RAM | **rejected — hurts ratio** |
| min‑match 3 | ≈ v2 | neutral | — | not worth it |
| 16‑bit offset, unbounded window | 0.78× (Ghost House) | big | breaks bounded RAM (window > 256 B/stream) | future "more‑RAM" mode |

**Why 8‑stream lost:** combining tone lo/hi into 16‑bit pairs dilutes the high
byte's stability (the pair changes whenever *either* byte changes), so per‑byte
matching on the separate columns actually compresses better. It would have saved
RAM (8×256 vs 11×256) and decode contexts, but it costs ratio, so it's rejected.

**16‑bit offsets** are the biggest untapped win (~0.78×, because ~half of all
matches reach further than 255 bytes back — §8.8), but they need a >256‑byte ring
per stream and a 2nd offset read, i.e. they trade the bounded‑RAM property. Kept
as a documented future knob, not adopted.

## The v2 format

Per‑column LZSS, decoded against a 256‑byte ring (8‑bit offsets), **one value per
stream per frame** — same model as v1:

```
0LLLLLLL            literal run, L+1 literal bytes follow            (1..128)
10LLLLLL [E]        RUN (offset 1 / repeat last byte):
                      LLLLLL<63 -> len = LLLLLL+2 (2..64)
                      LLLLLL==63 -> len = E (a full byte, 65..255)
                    no offset byte
11LLLLLL [E] off    MATCH: length as above, then one offset byte (1..255)
```

Two changes from v1: (1) offset‑1 runs get their own 1‑byte token (no offset
byte); (2) a single extension byte carries lengths up to 255. Token length is
capped at 255 so the per‑stream `rem` counter stays 8‑bit and a token start reads
at most cmd+ext+offset = **3 bytes** — that's the entire reason the worst case
barely moves. The RUN token shares the match copy path (it just sets the copy
index to `head‑1` and skips the offset fetch), so it is *cheaper* than a match,
not dearer. Encoder uses an optimal (DP) parse. Both a reference decoder and a
256‑byte‑ring decoder (a faithful model of the 6502) verify every stream.

## Results

### Size (corpus, 74052 frames)

| tune | v1 | opt | **v2** | .vgc |
|---|--:|--:|--:|--:|
| evil‑influences | 31410 | 30779 | 28758 | 20085 |
| BotB Slimeball | 10029 | 9888 | 9314 | 5674 |
| Collision Chaos | 4821 | 4772 | 4214 | 2209 |
| Diagonals | 16486 | 16178 | 14965 | 11568 |
| Ghost House | 3916 | 3801 | 3589 | 2670 |
| U_LOADER | 3116 | 3067 | 3015 | 3537 |
| VE3 | 25776 | 25109 | 23774 | 18528 |
| intro_test | 1686 | 1672 | 1447 | 771 |
| main_test | 9654 | 9507 | 9034 | 6794 |
| ne7‑magic_beans | 10100 | 9941 | 9267 | 5708 |
| outro_test | 5107 | 5065 | 4595 | 2564 |
| **TOTAL** | **122101** | **119779** | **111972** | **80108** |
| vs v1 | 1.000× | 0.981× | **0.917×** | 0.656× |
| vs .vgc | 1.52× | 1.50× | **1.40×** | 1.00× |

### Runtime — per-frame decode cost (cycles; SN write stubbed; budget 40000)

| | p50 | p90 | p99 | p99.9 | max | mean |
|---|--:|--:|--:|--:|--:|--:|
| v1 | 1466 | 1752 | 2167 | 2428 | 2787 | 1559 |
| **v2** | 1466 | 1749 | 2160 | 2455 | **3064** | 1553 |
| *(VGC, for scale)* | 1557 | 2995 | 3872 | 4307 | *5396* | *1559* |

The v1 and v2 distributions overlap almost exactly (see
`v2_cost_distribution.png`): same flat band, >50% of frames at the 1466 floor.
v2's worst frame is 277 cycles higher (the ext‑byte read when a long token
starts) — **7.7% of a frame vs v1's 7.0%**, and still well under the VGC player's
13.5%. Footprint: code+state 536→**594 B** (+58), decode buffers **2816 B** and
zero page **7 B** unchanged.

## Recommendation

- **Adopt v2.** 8.3% smaller for an unchanged decode profile, +58 B code, no extra
  RAM. Build the player with `-D VGI2=1`, pack with `pack_vgi2.py`. A v2 test disc
  is included (`music_v2.ssd`).
- **Take the optimal parse regardless** — even in the v1 format it's a free 1.9%
  (the decoder is byte‑for‑byte identical).
- **If more RAM is available**, a 16‑bit‑offset / larger‑window mode reaches
  ~0.78× (and could beat `.vgc`) while staying bounded‑*time* (it only adds one
  offset read per token); it costs a bigger ring per stream. Worth a follow‑up if
  the bank budget allows.

## Reproduce

```sh
cd beeb
python measure_v2.py            # size table (v1 / opt / v2 / vgc)
python measure_v2_runtime.py    # per-frame cost, v1 vs v2 (needs vgm-player-bbc)
python plot_v2.py               # v2_cost_distribution.png
python explore_vgi.py           # the full variant sweep (slow)
```
