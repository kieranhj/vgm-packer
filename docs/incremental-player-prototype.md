# Incremental-decode SN76489 player (BBC Micro) — §12.4 prototype

A 6502 prototype for the **single-bank, bounded-worst-case** regime in
`compression-analysis.md` §12.4: compressed music + code in one place, a
known per-frame cost, and **no runtime bulk decompression**.

It validates the core claim — that a byte-aligned per-column LZSS can be
**decoded one value per stream per frame**, so a long match never lands in a
single frame and the per-frame cost is bounded *independently of match length*.

> **The packer and player now default to the "v2" format** (offset-1 RUN token +
> extended length + optimal parse): ~8.3% smaller than the original v1 with the
> per-frame decode distribution unchanged. `vgipacker.py` emits v2 (use `--v1` for
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

## Raster-timing demo discs (`build_raster.sh`)

Four bootable discs, one per player, all playing the same tune (U_LOADER), built
on a raster harness adapted from tom-seddon's `cycle_exact_via_poll`. Each boots
to MODE 5, prints the player's name, and every frame raises the background colour
before the player call and drops it to black after — so the height of the
coloured band = that player's per-frame CPU time. A different colour per player:

| disc | player | band colour |
|---|---|---|
| `raster_vgi.ssd` | VGI looped | blue |
| `raster_vgi_unroll.ssd` | VGI unrolled | cyan |
| `raster_vgc.ssd` | VGC original | red |
| `raster_vgcopt.ssd` | VGC-opt | yellow |

Run them in jsbeeb (Model B) — open all four to compare the band heights; VGC's
band jitters frame-to-frame (RLE spikes), the VGI bands sit nearly still:

- VGI looped:   https://bbc.xania.org/?autoboot&disc1=https://raw.githubusercontent.com/kieranhj/vgm-packer/vgi-experiment/discs/raster_vgi.ssd
- VGI unrolled: https://bbc.xania.org/?autoboot&disc1=https://raw.githubusercontent.com/kieranhj/vgm-packer/vgi-experiment/discs/raster_vgi_unroll.ssd
- VGC original: https://bbc.xania.org/?autoboot&disc1=https://raw.githubusercontent.com/kieranhj/vgm-packer/vgi-experiment/discs/raster_vgc.ssd
- VGC-opt:      https://bbc.xania.org/?autoboot&disc1=https://raw.githubusercontent.com/kieranhj/vgm-packer/vgi-experiment/discs/raster_vgcopt.ssd

Rebuild with `./build_raster.sh [path/to/tune.vgm]`.

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

## Layout

This is an **experiment branch**. The clean, shipped distillations of this work
live in pull requests: the packer is `vgipacker.py` (repo root; PR to
vgm-packer) and the 6502 player is `lib/vgiplayer.asm` in the sibling
vgm-player-bbc repo. What remains here is the R&D: the prototype player, the
measurement harnesses, the plots and the design docs.

**`vgipacker.py`** (repo root) — VGM → `.vgi` (v2 by default; `--v1` for the
original), with self-verifying round-trips. The bench scripts import it.

**`bench/`** — measurement & benchmark harnesses:

- `player.asm` — the prototype 6502 player (BeebAsm). `-D TEST=1` builds a
  harness that decodes into a buffer for the simulator; `-D TEST=0` builds the
  real, bootable player. `-D VGI2=1` (default) decodes v2; `-D UNROLL=1` builds
  the faster unrolled decoder (+~0.7 KB); default `UNROLL=0` is compact looped.
- `sim_test.py` / `sim_test_player.py` / `measure_cycles.py` — py65 checks.
- `sim_compare.py` / `sim_vgc.asm` — per-frame cycle comparison vs the VGC
  player (needs a `vgm-player-bbc` checkout; see below).
- `bench_all.py`, `bench_players.py`, `explore_vgi.py`, `measure_v2*.py`,
  `measure_proposal*.py`, `analyse_registers.py`, ... — the corpus studies.
  Cached results land in `bench/_cache/` (git-ignored).
- `bench/vgc/` — the VGC comparison harness (`sim.asm` / `raster_vgc.asm` /
  `measure.py`). The VGC players are **not** vendored here any more; the asm
  `INCLUDE`s them from the sibling `../../../vgm-player-bbc/lib/` (the optimised
  one needs that repo's `vgc-opt-player` branch). See `VGC_OPTIMISATION_PLAN.md`.
- `build.sh` — pack, run all checks, build the disc (→ `discs/music.ssd`).
- `build_raster.sh` — build the four raster-timing discs (→ `discs/`).

**`plots/`** — `plot_dist.py`, `plot_players.py`, `plot_v2.py` (read
`bench/_cache/*.pkl`) and their `.png` outputs.

**`discs/`** — bootable disc images: `music.ssd` (Ghost House, looped),
`music_unroll.ssd` (unrolled), and the four `raster_*.ssd` timing discs.

**`docs/`** — this file plus `compression-analysis.md`, `COMPRESSION_REPORT.md`,
`OPTIMISATION_PLAN.md`, `VGC_OPTIMISATION_PLAN.md`.

## Build / test

Needs [BeebAsm](https://github.com/stardot/beebasm) and `pip install py65`.
From the `bench/` directory (set `BEEBASM` if it isn't on `PATH`):

```sh
cd bench
./build.sh                                   # default tune (Ghost House)
./build.sh "../vgm/U_LOADER.vgm"             # any SN76489 VGM in the corpus
```

`build.sh` packs the tune, runs the three simulation checks, and writes
`discs/music.ssd`.

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
