# bench/ — measurement & benchmark harnesses

The R&D harnesses behind the `.vgi` format and the incremental player. They pack
the corpus, assemble the 6502 prototype (`player.asm`), simulate it in py65, and
measure per-frame decode cost and compressed size. Results are cached in
`bench/_cache/` (git-ignored); the scripts in `../plots/` turn those caches into
figures.

## Prerequisites

- Python 3 with `pip install py65 numpy`.
- [BeebAsm](https://github.com/stardot/beebasm) — set `BEEBASM` to its path, or
  put `beebasm` on `PATH`.
- The SN76489 VGM corpus in `../vgm/` (already in the repo).
- For the VGC comparisons: a sibling `vgm-player-bbc` checkout (set
  `VGM_PLAYER_BBC`, default `../../vgm-player-bbc`). The *optimised* VGC player
  needs that repo's `vgc-opt-player` branch checked out (for
  `lib/vgcplayer_opt.asm`); without it, only the original VGC player is measured.

Run everything **from this `bench/` directory**.

## The prototype: build & verify

`build.sh` is the one-shot path — it packs a tune, assembles `player.asm`, runs
the byte-exact simulation checks, and writes a bootable disc to `../discs/`:

```sh
./build.sh                          # default tune (Ghost House)
./build.sh "../vgm/U_LOADER.vgm"    # any SN76489 VGM in ../vgm/
UNROLL=1 ./build.sh                 # the faster unrolled decoder
```

The individual checks (each assembles `player.asm` with beebasm first — see the
commands in each file's header):

| script | what it asserts / measures |
|---|---|
| `sim_test.py` | the decoder is **byte-exact** (decodes each column, compares to the packer's reference) |
| `sim_test_player.py` | the **full player** SN76489 output is byte-exact |
| `measure_cycles.py` | the prototype's **per-frame cycle cost** (py65) |

## Corpus studies

These iterate the whole `../vgm/` corpus. The ones that write a `.pkl` populate
`bench/_cache/` for the plot scripts.

| script | produces | notes |
|---|---|---|
| `bench_all.py` | `_cache/bench_costs.pkl` | size / in-RAM footprint study across formats |
| `bench_players.py` | `_cache/players.pkl` | 4-player per-frame cost (VGI looped/unrolled, VGC orig/opt); needs `vgm-player-bbc` |
| `measure_v2.py` | size table (stdout) | v1 / optimal-parse / v2 / `.vgc` totals |
| `measure_v2_runtime.py` | `_cache/v2_runtime.pkl` | per-frame v1 vs v2 cost; needs `vgm-player-bbc` |
| `explore_vgi.py` | sweep table (stdout) | the full format-variant sweep that led to v2 (**slow**) |

Typical run (then plot — see `../plots/README.md`):

```sh
python measure_v2.py
python measure_v2_runtime.py
python bench_all.py
python bench_players.py
```

## Earlier analysis (see `../docs/compression-analysis.md`)

These informed the format design; they print their findings to stdout.

| script | topic |
|---|---|
| `analyse_registers.py` | statistical dissection of the SN76489 register streams |
| `measure_delta.py` | delta-coding potential (§12.1) |
| `measure_patterns.py` | pattern-layer potential |
| `measure_proposal2.py` | Proposal 2 — per-register RLE, no LZ |
| `measure_proposal5.py` | Proposal 5 — synchronised frame-LZ (a measured negative result) |

## VGC comparison

| script | what it does |
|---|---|
| `sim_compare.py` | per-frame cycle compare: prototype vs the VGC player (manual build steps in its header; needs `vgm-player-bbc`) |
| `vgc/measure.py` | VGC **original vs optimised**, byte-exact + cycles. `vgc/sim.asm` and `vgc/raster_vgc.asm` `INCLUDE` the players from `../../../vgm-player-bbc/lib/`; the optimised one needs that repo's `vgc-opt-player` branch |

## Raster discs

`build_raster.sh [tune.vgm]` builds four bootable raster-timing discs (one per
player) into `../discs/`, each showing a coloured band whose height is that
player's per-frame CPU time.
