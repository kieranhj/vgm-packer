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
