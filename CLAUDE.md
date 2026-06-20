# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Python compression tool that packs SN76489 PSG VGM chiptune files into a custom `.VGC` format optimized for streamed playback on 8-bit CPUs (e.g. 6502 BBC Micro). It compresses VGM to ~9-11% of original size while keeping the data decodable a frame at a time with only ~2KB of RAM workspace. **Only SN76489 PSG VGM files are supported** — the parser rejects VGMs that use any other sound chip.

## Commands

There is no build, test, or lint setup — these are standalone scripts run directly. Despite the README's "Python 2.x only" note, the code is now Python 3 compatible (see commit history; `vgmparser.py` branches on `sys.version_info`). Run with Python 3.

```bash
# Pack a VGM into a VGC (output defaults to <input>.vgc)
python vgmpacker.py example/Androids.vgm -o example/Androids.vgc

# Pack with optional Huffman post-pass (smaller, slower/variable to decode)
python vgmpacker.py input.vgm -n -o output.vgc

# Dump raw interleaved 11-byte-per-frame SN76489 register data (testing)
python vgmdump.py example/Androids.vgm -o example/Androids.raw
```

The `--buffer` option exists for experimentation but the 6502 decoder only supports the default 255-byte buffer; values >255 produce VGC files the decoder cannot use.

Scripts must be run from the repo root because they import from the `modules/` package (`from modules.lz4enc import LZ4`). The tool is not portable as a single file — `modules/` must travel with the scripts.

## Architecture

The pipeline has two stages, split across two layers:

**1. VGM → raw frame stream** (`modules/vgmparser.py`, class `VgmStream`)
- Parses the VGM container (metadata table keyed by VGM version, GD3 tags, command stream). Handles gzipped `.vgz` transparently.
- `as_binary()` flattens the timed VGM command stream into a fixed-rate (50/60Hz) stream of variable-length packets: one packet per frame containing only the SN76489 register writes for that frame, prefixed with a small header (play rate, packet count, duration, title, author). `0xFF` packet length terminates the stream.
- `write_vgm()` does the reverse (re-emit a VGM) and is used for round-tripping/testing.

**2. raw frame stream → VGC** (`vgmpacker.py`, class `VgmPacker`)
The core transform in `process()` follows the recipe documented at length in README.md ("How it works"). In code terms:
- `split_raw()` walks the packet stream and reconstructs the full state of all 11 SN76489 registers every frame, de-interleaving into 11 parallel byte streams (one per register). Register latch/command flag bits are stripped here (`register_mask = 15`). An EOF marker `0x08` is appended to the noise/tone3 stream (index 6).
- The 11 register streams are recombined into **8 logical streams**: three 16-bit tone streams (channels 0/1/2, each two registers paired via `combine_registers`), one noise stream, and four 4-bit volume streams.
- Run-length encoding: `rle2()` for the 16-bit tone streams, `rle()` for the 4-bit streams (count packed into the top 4 bits). The noise stream is first passed through `diff()` which replaces unchanged frames with `0x0f` ("skip") to avoid resetting the chip's LFSR (writing the noise register always restarts the noise generator).
- Each of the 8 streams is LZ4-compressed independently (`modules/lz4enc.py`), using a modified LZ4 with a 255-byte window and 8-bit (1-byte) offsets so the decoder needs only a 256-byte buffer per stream.
- Optional Huffman post-pass (`modules/huffman.py`, `-n`) builds one shared code table over all compressed blocks and re-encodes them.
- Output is wrapped in an LZ4 frame/block layout, but the magic number is changed to `VGC\0` (or `VGC\x80` if Huffman) so it is deliberately *not* LZ4-compatible. Exact byte layout is in README.md ("VGC File Format").

Key invariant: the format requires **streamed random access** to all 8 streams simultaneously — the decoder pulls one value per stream per frame, so the streams are compressed separately rather than as one blob. This constraint drives every design choice (per-stream LZ4, small window, RLE, the noise-skip marker).

### Self-verification in the code

`vgmpacker.py` is heavily defensive: `rle()`/`rle2()`/`diff()` each unpack their own output and `assert` it round-trips, `testUnpackLZ4()` decodes every LZ4 block and asserts equality against the source, and `process()` warns on out-of-range noise values. When modifying any encoder, these self-checks will catch breakage immediately — keep them in place.

## modules/

`lz4enc.py` and `huffman.py` are vendored from https://github.com/simondotm/lz4enc-python (the upstream home for those). Treat them as a shared dependency; prefer pulling fixes from upstream over divergent local edits. `.pyc`/`__pycache__` artifacts are checked in but irrelevant.

## Related projects (context, not in this repo)

- `vgm-player-bbc` — the 6502 decoder/playback routines that consume `.vgc` files (defines the 255-byte buffer constraint).
- `vgm-converter`, `ym2149f` — upstream tools for producing/converting suitable SN76489 VGMs.
