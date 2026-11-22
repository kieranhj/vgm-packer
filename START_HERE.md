# vgm-packer: start here

Kieran's fork of simondotm's vgm-packer: Python tools that compress SN76489 VGM music for 8-bit
machines (`.vgc`, LZ4-based), plus Kieran's own `.vgi` format (`vgipacker.py`): per-register LZSS
streams decoded one value per frame, so the BBC player's per-frame cost is low and bounded. A tool.

| | |
|---|---|
| GitHub | https://github.com/kieranhj/vgm-packer (public, fork) |
| Upstream | https://github.com/simondotm/vgm-packer (remote `upstream`; last upstream commit 2020-05-19) |
| Local path | D:\beeb\mine\vgm-packer (moved from OneDrive\BEEB\Repos\vgm-packer on 2026-11-22) |
| Branch | `vgi-format` (8 commits past upstream); other branches: `master` (9 past upstream: Python 3 fixes, VGX notes), `vgi-experiment` (25 past `master`: the VGI R&D layout, compression report) |
| Last commit | 2026-09-06: v3 is the default now that the player reads it |

## What's in here
- `vgmpacker.py` - the `.vgc` packer (upstream's, ported to Python 3); `vgmdump.py` - VGM dump utility.
- `vgipacker.py` - the `.vgi` packer (v3 default: tone periods indexed into a table, 8 columns).
- `modules/` - shared Python (VGM parser, LZ4 encoder, huffman).
- `docs/vgi-format.md` - the `.vgi` format and its design.
- `example/` - `Androids.vgm` packed as `.vgc`, `.vgi` and `.raw`.

## Build and run
- `make`: prints usage; nothing to build (Python 3 scripts).
- `python vgipacker.py tune.vgm -o tune.vgi`; `python vgmpacker.py tune.vgm -o tune.vgc`; `python vgmdump.py tune.vgm`.
- The matching 6502 player is in `D:\beeb\mine\vgm-player-bbc` (branch `kieranhj/vgi-player`).

## Local changes
- Differs from upstream: Python 3 port of the modules, two crash fixes in `split_raw()` / `as_binary()`,
  `huffman.py` tie-break fix, and the new `vgipacker.py` + `.vgi` v3 format (this branch).
- `master` and `vgi-format` have diverged (neither contains the other); `vgi-experiment` holds the wider R&D.
- No uncommitted work. New, uncommitted: `Makefile`, `START_HERE.md`.

## Status and next steps
- `.vgi` v3 is done and is the default; the player reads it (vgm-player-bbc README: it beats the VGC
  player's mean and worst-frame cost).
- Open: whether to merge `vgi-format` into `master`, and whether to offer anything upstream (dormant since 2020).

## See also
- `README.md`, `docs/vgi-format.md`. Related on D: `D:\beeb\mine\vgm-player-bbc`, `D:\beeb\mine\vgm-converter`,
  `D:\beeb\mine\vgmplay`.
