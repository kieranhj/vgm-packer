# Incremental-decode SN76489 player (BBC Micro) — §12.4 prototype

A 6502 prototype for the **single-bank, bounded-worst-case** regime in
`docs/compression-analysis.md` §12.4: compressed music + code in one place, a
known per-frame cost, and **no runtime bulk decompression**.

It validates the core claim — that a byte-aligned per-column LZSS can be
**decoded one value per stream per frame**, so a long match never lands in a
single frame and the per-frame cost is bounded *independently of match length*.

## Result (measured in simulation)

| check | result |
|---|---|
| decoder vs source (`sim_test.py`, 512 frames × 11 streams) | **PASS** — byte-exact |
| full player SN76489 output (`sim_test_player.py`, 200 frames) | **PASS** — byte-exact |
| per-frame cost (`measure_cycles.py`) | **min 2886 / mean 2924 / max 3927 cycles** |
| worst frame vs 50 Hz budget (40000 cyc @ 2 MHz) | **9.8%** |

The 1041-cycle spread (and ~10% budget use) is the §12.4 thesis made concrete:
bounded, decode-once-per-frame, no decompression hitch. The worst frame is when
many streams start a new command at once; it is still tiny.

### vs the existing VGC player (full corpus, `bench_all.py`)

Both players driven through the same py65 cycle measurement on all 11 corpus
tunes, **SN76489 write stubbed to RTS in both** so only decode + register
reconstruction is timed (the players' real `sn_write` routines differ — VGC's
has no strobe delay, mine has a conservative tunable one).

**Fixed footprint (tune-independent):**

| player | code+state | decode buffers | zp | total RAM |
|---|--:|--:|--:|--:|
| incremental (`.vgi`) | 536 | 2816 (11×256) | 7 | **3359** |
| existing VGC (8× LZ4) | 768 | 2048 (8×256) | 8 | **2824** |

**Compressed size & per-frame cost (cycles; 50 Hz budget = 40000 @ 2 MHz).**
Columns: incremental and VGC each as min / median / mean / max.

| tune | frames | `.vgi` | `.vgc` | incr min/med/mean/max | VGC min/med/mean/max |
|---|--:|--:|--:|--:|--:|
| evil-influences | 15210 | 31410 | 20085 | 1466 / 1481 / 1580 / 2612 | 294 / 1561 / 1669 / 5396 |
| BotB Slimeball | 4458 | 10029 | 5674 | 1466 / 1479 / 1600 / 2757 | 294 / 1539 / 1655 / 5050 |
| Collision Chaos | 5000 | 4821 | 2209 | 1466 / 1466 / 1521 / 2787 | 294 / 626 / 996 / 4873 |
| Diagonals | 8069 | 16486 | 11568 | 1466 / 1466 / 1578 / 2612 | 294 / 1406 / 1538 / 4814 |
| Ghost House | 2559 | 3916 | 2670 | 1466 / 1466 / 1548 / 2503 | 294 / 1541 / 1521 / 4814 |
| U_LOADER | 1999 | 3116 | 3537 | 1466 / 1466 / 1556 / 2582 | 294 / 2462 / 2557 / 4814 |
| VE3 | 12547 | 25776 | 18528 | 1466 / 1479 / 1582 / 2706 | 294 / 2160 / 2247 / 5050 |
| intro_test | 2242 | 1686 | 771 | 1466 / 1466 / 1508 / 2461 | 294 / 1820 / 1787 / 5116 |
| main_test | 8846 | 9654 | 6794 | 1466 / 1466 / 1530 / 2615 | 294 / 1604 / 1797 / 4873 |
| ne7-magic_beans | 6976 | 10100 | 5708 | 1466 / 1466 / 1548 / 2721 | 294 / 897 / 1028 / 5396 |
| outro_test | 6146 | 5107 | 2564 | 1466 / 1466 / 1514 / 2496 | 294 / 1272 / 1499 / 5352 |
| **total / worst** | 74052 | **122101** | **80108** | max **2787** | max **5396** |

### Distribution — are VGC's spikes one-off or frequent? (`plot_dist.py`)

Corpus-wide per-frame cost (74052 frames), and how often each player exceeds a
threshold:

| | p50 | p90 | p99 | p99.9 | max |
|---|--:|--:|--:|--:|--:|
| incremental | 1466 | 1752 | 2167 | 2428 | **2787** |
| existing VGC | 1557 | 2995 | 3872 | 4307 | **5396** |

| frames over… | incremental | VGC |
|---|--:|--:|
| > 2787 (incr's worst) | 0.00% | **13.42%** |
| > 3500 | 0.00% | 3.63% |
| > 4000 | 0.00% | 0.46% |
| > 4500 | 0.00% | 0.04% |

**The spikes are frequent, not one-off:** VGC exceeds the incremental decoder's
*entire* worst case on **>1 in 8 frames** (13.4%), and tops 3500 cycles on ~1 in
28. The really big spikes (>4500) are rare (1 in ~2500) but you must still budget
for the 5396 ceiling. The incremental decoder never crosses 2787 — even its
p99.9 (2428) sits below VGC's median-ish band. See
`frame_cost_distribution.png` (histogram + a cost-over-time trace of the spikiest
tune, Collision Chaos, where VGC idles cheaply on RLE runs but spikes recur
throughout).

Reading it:

- **Size:** VGC wins — `.vgi` is **1.52× larger** overall (flat per-column LZSS
  with no RLE vs VGC's RLE+LZ4). The exception is busy tunes like U_LOADER where
  RLE has little to chew on and `.vgi` is actually smaller.
- **RAM:** VGC is a bit leaner (2824 vs 3359 B) — 8 stream buffers vs my 11
  (combining tone lo/hi into 8 streams would close most of that gap).
- **Worst-case per-frame cost (the point):** the incremental decoder is
  **bounded and flat — 2461–2787 cycles (≤7.0% of the frame) on every tune** —
  while VGC swings up to **4814–5396 (≤13.5%)**. The incremental worst case is
  consistently **~half VGC's**.
- **Mean cost:** the incremental mean is rock-steady (~1500–1600); VGC's mean is
  data-dependent — cheaper on repetitive tunes (Collision 996, ne7 1028, where
  RLE runs skip most decodes) but pricier on busy ones (U_LOADER 2557, VE3 2247).

So the trade is exactly the §12.4 thesis: the incremental scheme gives up ~1.5×
on storage (and a little RAM) to buy a **bounded, predictable worst-case** decode
— half VGC's ceiling — which is what a timing-critical demo budgets against.
(Real per-frame totals add each player's sound writes on top; tunable, not a
decode difference.)

Reproduce the single-tune view with `python sim_compare.py`, or the whole table
with `python bench_all.py` (needs a `vgm-player-bbc` checkout with `sim_vgc.asm`
copied in; set `VGM_PLAYER_BBC`).

## The `.vgi` format

11 register columns (one per SN76489 register), each compressed independently
with a tiny LZSS over a **256-byte ring window** (8-bit offsets). The noise
column keeps the `0x0f` "skip" marker so the LFSR is not reset on unchanged
frames. Per frame the player pulls one byte from each stream, rebuilds the chip
state and writes all 11 registers (noise only when changed).

LZSS token stream (per column), decoded against the 256-byte ring:

- `cmd` bit7 = 0 → literal run of `(cmd & 0x7f)+1` bytes, which follow (1..128)
- `cmd` bit7 = 1 → match of `(cmd & 0x7f)+2` bytes (2..129), then one offset
  byte (1..255); copy from `head - offset`.

Each column decodes to exactly `nframes` bytes (no end marker needed).

File layout (little-endian, loaded as one blob):

```
+0   'V','G','I',1
+4   nframes (16-bit)
+6   11 x stream offset (16-bit, relative to file start)
+28  the 11 LZSS streams, concatenated
```

This flat (no-RLE) layout is intentionally simple for the prototype; it is a
touch larger than VGC's RLE+LZ4 but trivially bounded to decode (see §8.9/P4f).

## Files

- `pack_vgi.py` — VGM → `.vgi`, with a self-verifying round-trip (both a plain
  decoder and a faithful ring-decoder model).
- `player.asm` — the 6502 player (BeebAsm). `-D TEST=1` builds a harness that
  decodes into a buffer for the simulator; `-D TEST=0` builds the real,
  bootable player.
- `sim_test.py` / `sim_test_player.py` / `measure_cycles.py` — py65 checks.
- `sim_compare.py` / `sim_vgc.asm` — per-frame cycle comparison vs the existing
  VGC player (needs a `vgm-player-bbc` checkout; see below).
- `bench_all.py` / `plot_dist.py` — full-corpus size/footprint/cost table and the
  cost-distribution figure.
- `build.sh` — pack, run all checks, build the disc.
- `music.ssd` — bootable 200 KB disc image (Ghost House, ~51 s).

## Build / test

Needs [BeebAsm](https://github.com/stardot/beebasm) and `pip install py65`.
From this directory (set `BEEBASM` if it isn't on `PATH`):

```sh
./build.sh                                   # default tune (Ghost House)
./build.sh "../vgm/U_LOADER.vgm"             # any SN76489 VGM in the corpus
```

`build.sh` packs the tune, runs the three simulation checks, and writes
`music.ssd`.

## Running on a BBC (or emulator)

Boot the disc: `SHIFT`+`BREAK` (it is set to `*EXEC !BOOT`, which runs `Player`).
Tested geometry: single-sided, 80-track, DFS.

## Caveats

- The decoder, register encoding and per-frame cost are verified in a 6502
  simulator. The **sound-chip /WE strobe timing** (the `sn` routine's delay
  loop) and the **vsync poll** are the only parts that need real hardware — if
  notes sound wrong, the first thing to tweak is the `LDX #&18` delay in `sn`.
- The player runs with interrupts disabled for the whole tune (so the OS
  keyboard scan can't collide on the sound port), then returns to the prompt.
  Press `BREAK` to stop early.
- Workspace: rings live at `&6000-&6AFF` (2.75 KB); code + data load at `&1900`.
  This prototype keeps everything in main RAM rather than a sideways bank — the
  §12.4 bank sizing is a separate (already-measured) claim; this proves the
  decode cost.
