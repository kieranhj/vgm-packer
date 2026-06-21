# Incremental-decode SN76489 player (BBC Micro) — §12.4 prototype

A 6502 prototype for the **single-bank, bounded-worst-case** regime in
`docs/compression-analysis.md` §12.4: compressed music + code in one place, a
known per-frame cost, and **no runtime bulk decompression**.

It validates the core claim — that a byte-aligned per-column LZSS can be
**decoded one value per stream per frame**, so a long match never lands in a
single frame and the per-frame cost is bounded *independently of match length*.

> **The packer and player now default to the "v2" format** (offset-1 RUN token +
> extended length + optimal parse): ~8.3% smaller than the original v1 with the
> per-frame decode distribution unchanged. `pack_vgi.py` emits v2 (use `--v1` for
> the original); the player is built with `-D VGI2=1`. Full study in
> `COMPRESSION_REPORT.md`. The tables below labelled "incremental"/"v1" are the
> original baseline; v2 is 8.3% smaller for the same runtime profile.

## Result (measured in simulation, default v2 build)

Decoder + full player SN76489 output are byte-exact (`sim_test.py` /
`sim_test_player.py`). Per-frame cost incl. sound (`measure_cycles.py`), after the
optimisations in `OPTIMISATION_PLAN.md`:

| build | min | mean | max | worst vs 50 Hz |
|---|--:|--:|--:|--:|
| looped (default, `music.ssd`) | 1468 | 1506 | 2876 | 7.2% |
| unrolled (`-D UNROLL=1`, `music_unroll.ssd`) | 983 | 1025 | 2487 | 6.2% |

Down from 2922 mean / 4294 max before optimisation. The unrolled build is faster
(decode floor 673 vs 1158) at +672 bytes of code; the looped build is the compact
default. See `OPTIMISATION_PLAN.md` for the cycle analysis and tier breakdown.

### Four-player comparison (full corpus, `bench_players.py`)

All four players driven through the same py65 measurement on the 11-tune corpus
(74052 frames), **SN write stubbed to RTS in every one** so only decode +
dispatch is timed. This is now an apples-to-apples comparison: all four share the
same no-delay `sn` routine, so stubbing it isolates the decompression cost.

| player | code+state | decode buffers | zp |
|---|--:|--:|--:|
| VGI (looped, default) | 580 | 2816 (11×256) | 7 |
| VGI (unrolled, `UNROLL=1`) | 1252 | 2816 (11×256) | 7 |
| VGC (original) | ~950 | 2048 (8×256) | 8 |
| VGC-opt (`OPT=1`, `vgc/`) | ~830 | 2048 (8×256) | 8 |

**Per-frame decode cost, corpus percentiles (cycles):**

| player | p50 | p90 | p99 | p99.9 | max |
|---|--:|--:|--:|--:|--:|
| **VGI (unrolled)** | **673** | 996 | 1458 | 1801 | **2404** |
| VGI (looped) | 1158 | 1451 | 1876 | 2196 | 2770 |
| VGC-opt | 1143 | 2168 | 2855 | 3345 | 4624 |
| VGC (original) | 1557 | 2995 | 3872 | 4307 | 5396 |

Reading it (see `players_distribution.png` and `players_timeseries.png`):

- **VGI is flat and bounded; VGC is spiky.** The VGI players sit in a tight band
  (looped median 1158 / max 2770; unrolled 673 / 2404). The VGC players are
  bimodal — cheap when their RLE counters are running, expensive when several
  streams refill an LZ token at once — so they spike (opt max 4624, original
  5396). The time-series plot makes this obvious: VGC spikes recur every few
  frames, VGI is a near-flat line.
- **VGI-unrolled wins on every metric** — lowest typical (673) *and* tightest
  worst case (2404). It's the fastest, most predictable player.
- **VGC-opt** (the resident-context rewrite, see `VGC_OPTIMISATION_PLAN.md`) cuts
  the original's median 1557→1143 and max 5396→4624. Its low median actually
  beats VGI-looped's (RLE lets it skip most decodes), but its worst case is still
  ~1.7–1.9× VGI's because the spikes remain.
- **Trade-off summary:** VGC compresses smaller (RLE+LZ4; `.vgi` is ~1.4× `.vgc`,
  see `COMPRESSION_REPORT.md`) and has a slightly smaller buffer; VGI trades that
  for a *bounded, predictable* per-frame cost — the thing a timing-critical demo
  budgets against.

Reproduce: `python bench_players.py` (needs the cached `.vgc` files in `_cache/`,
produced by `bench_all.py`/`measure_v2.py`, and the vendored player in `vgc/`),
then `python plot_players.py`.

Note: this is decode-only. Real per-frame totals add each player's sound writes —
VGI writes all 11 registers every frame, VGC only the ones that changed (RLE), so
VGC's sound cost is lower; that is a separate, real difference, not measured here.

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

- `pack_vgi.py` — VGM → `.vgi` (v2 format by default; `--v1` for the original),
  with self-verifying round-trips (a plain decoder and a faithful ring model).
- `player.asm` — the 6502 player (BeebAsm). `-D TEST=1` builds a harness that
  decodes into a buffer for the simulator; `-D TEST=0` builds the real, bootable
  player. `-D VGI2=1` (default) decodes v2; `-D VGI2=0` decodes v1. `-D UNROLL=1`
  builds the faster unrolled decoder (+~0.7 KB code); default `UNROLL=0` is the
  compact looped build. (`UNROLL=1 ./build.sh` builds the unrolled disc.)
- `sim_test.py` / `sim_test_player.py` / `measure_cycles.py` — py65 checks.
- `sim_compare.py` / `sim_vgc.asm` — per-frame cycle comparison vs the existing
  VGC player (needs a `vgm-player-bbc` checkout; see below).
- `bench_all.py` / `plot_dist.py` — earlier VGI-vs-VGC size/footprint study.
- `bench_players.py` / `plot_players.py` — current 4-player corpus benchmark
  (VGI looped/unrolled, VGC original/opt) → `players_distribution.png`,
  `players_timeseries.png`.
- `vgc/` — vendored VGC player (`vgcplayer.asm`, by Simon Morris) + the optimised
  `vgcplayer_opt.asm` + `measure.py`. See `VGC_OPTIMISATION_PLAN.md`.
- `explore_vgi.py` / `measure_v2*.py` / `plot_v2.py` — the "v2" format study that
  led to v2 becoming the default: 8.3% smaller for an unchanged decode profile.
  See `COMPRESSION_REPORT.md`.
- `build.sh` — pack, run all checks, build the disc.
- `music.ssd` — bootable 200 KB disc (Ghost House, ~51 s, v2, looped decoder).
- `music_unroll.ssd` — same tune with the faster unrolled decoder (`-D UNROLL=1`).
- `OPTIMISATION_PLAN.md` — runtime cycle analysis and the optimisation tiers.

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
