# SN76489 VGM Compression — Analysis & Alternative Schemes

*A study of how to compress SN76489 PSG register music for fast playback on 8-bit
(6502) CPUs, evaluating alternatives to the current VGC format.*

Date: 2026-06-20

---

## 1. Executive summary

The current `.VGC` format (8 de-interleaved register streams, each LZ4-compressed,
optional Huffman) achieves excellent compression but pays for it at runtime: the
6502 decoder has to juggle **8 independent LZ decode contexts** with ~2 KB of ring-buffer
workspace, and per-frame cost is **variable** (an LZ match-copy can be short or long).
Music playback wants *bounded* per-frame time, not best-case-fast.

We evaluated simpler, register-format-aware alternatives and measured them against VGC
on an 11-file real-world test corpus (BBC + SMS PSG tunes, several from shipped demos).
The headline finding:

> **A single ZX0/ZX02 stream over the de-interleaved (+RLE) data ("Proposal 4")
> compresses ~2.2× *smaller* than VGC and decodes far more simply** — provided the tune
> is decompressed once into RAM and then played back from there.

The catch is RAM regime, not ratio: P4 needs the whole (RLE'd) tune resident in RAM.
For typical BBC-sized tunes (~10–20 KB decompressed) that's fine; for large tunes
(VE3 ≈ 76 KB decompressed) it needs sideways-RAM banking or per-section decompression.

A pure run-length scheme with no back-references ("Proposal 2") is the cheapest possible
decode but trades **~4.0× larger** files than VGC — because the dominant redundancy in this
music is *long-range pattern repetition* (LZ-type), not *consecutive-frame runs* (RLE-type).

A scheme that exploits the 8 streams being frame-synchronised to collapse them to a single
LZ context with matches measured in *frames* ("Proposal 5") was built and measured — and
**loses to VGC by 2.4–5.8×**. It is a documented *negative* result: whole-frame matching is
defeated by volume envelopes that change nearly every frame, so almost every frame degrades
to a raw literal. Per-column independence (VGC's 8 contexts) is doing real work that no shared
frame-schedule can replace. See §5/§8.5/§9.

---

## 2. Problem statement

VGM is essentially a timed sequence of register writes to the sound chip (here the
SN76489 PSG). A maximum of **11 bytes** sets all channels, and playback is typically at a
fixed **50 Hz** (BBC) or 60 Hz. That makes raw VGM verbose (up to ~550 bytes/sec), hence
the need for compression.

Generic byte compressors (exomizer, LZ4, ZX0/ZX02) applied to the *interleaved* VGM stream
underperform, because a match has to span unrelated register bytes. The VGC format was
created to fix this by **de-interleaving** the register bytes into per-register streams,
each compressed separately — which compresses very well but is expensive to decode on 6502.

**Goal:** a scheme targeted at the SN76489 register format that is **simpler and faster to
decode at runtime on 6502**, accepting a worse ratio if necessary.

### SN76489 register layout (recap)

- 3 × square-wave tone channels — 10-bit, written as **2 bytes** each
- 1 × noise channel — 3-bit, **1 byte** (writing it resets the LFSR → audible artifacts,
  so it must only be written when it actually changes)
- 4 × volume registers — 4-bit, **1 byte** each

Total = 11 bytes to fully refresh the chip per frame.

---

## 3. How the current VGC format works

Pipeline (`modules/vgmparser.py` → `vgmpacker.py`):

1. **Distil** — parse VGM, flatten the timed command stream into a fixed-rate stream of
   variable-length per-frame packets containing only SN76489 writes (`VgmStream.as_binary()`).
2. **De-interleave** — reconstruct all 11 register values every frame and split into
   parallel per-register byte columns (`split_raw`), stripping the latch/command flag bits.
3. **Reformat to 8 logical streams** — 3 × 16-bit tone, 1 × noise, 4 × 4-bit volume.
4. **RLE** — `rle2()` for the 16-bit tone streams, `rle()` for the 4-bit streams
   (run length in the top 4 bits). Noise is first `diff()`'d so unchanged frames become a
   `0x0f` "skip" marker (prevents LFSR reset); `0x08` is appended as EOF.
5. **LZ4** — each of the 8 streams compressed independently, with a modified LZ4 using a
   255-byte window and 8-bit offsets (so the decoder needs only 256 bytes/stream).
6. **Huffman (optional, `-n`)** — shared code table over all blocks.
7. Wrapped in an LZ4-style frame/block layout with magic number `VGC\0` (or `VGC\x80`).

### Why it's expensive at runtime

- **8 independent LZ contexts** advanced per frame; ~2 KB ring-buffer workspace; heavy
  zero-page pressure.
- **Variable per-frame cost** — match-copies blow the frame budget unpredictably.
- VGC conflates two jobs — *compress small for storage* and *decode cheap for playback* —
  and solves both with the same LZ layer. Most alternatives below come from separating them.

---

## 4. Design space: two regimes

1. **Stay-compressed-in-RAM, decode per frame** (VGC's regime). Here you *must* trade
   ratio for bounded, cheap per-frame work → drop LZ back-references.
2. **Decompress-once-to-RAM, then play flat** (if the working set fits). Storage can use a
   *strong* packer; playback becomes trivial. Usually the better deal on a BBC with
   sideways RAM banks or for short/looping tunes.

Decode-cost taxonomy (fastest → slowest):
change-mask ≈ RLE-cursors (decompress-once) > single-stream LZSS > 8-stream LZ4 (VGC) > VGC+Huffman.

---

## 5. The proposals

### Proposal 1 — Change-mask frames (single stream, near-zero state)
One control byte per frame = bitmask of which registers changed; changed values follow;
a `mask == 0` byte carries a whole-frame *repeat count* (captures held notes). Decode: read
mask, write the flagged registers; no buffers, one cursor. Fastest/simplest, smallest RAM,
but weakest ratio — volume envelopes (which change most frames) don't compress under masking
alone. Good for very high update rates.

### Proposal 2 — Per-register RLE cursors (no LZ)  ✅ measured
Keep VGC's de-interleave + RLE (steps 2–4), **stop before LZ4**. Ship the 8 RLE streams.
Decode: 8 cursors + 8 countdown counters + 8 current values. Per frame, for each stream:
if `counter > 0` decrement (and optionally skip the chip write); else read next RLE token,
latch value+count, write. No back-references, no ring buffers, bounded time, ~tens of bytes
of state. The repo already produces these streams as the pre-LZ4 intermediate.

### Proposal 3 — Single-stream LZSS/LZSA/ZX0 streamed (one context)
One LZ context + one window buffer. To keep per-frame random access you must keep frames
**interleaved** — which is exactly the layout that hurt generic compressors — so it throws
away VGC's core insight. Not pursued. (A stardot.org.uk experiment got ~13% this way,
"faster than Exomizer but maybe not fast enough for 1 kHz".)

### Proposal 4 — ZX0/ZX02 store → RLE-cursor play (decompress-once)  ✅ measured
Store the de-interleaved (+RLE) streams as a **single ZX02 stream**. At load (or per
loop-section), decompress to flat RLE arrays in RAM, then play with Proposal 2's cursor
loop. ZX0 is a much stronger compressor than LZ4 (bit-level, optimal parse), and compressing
one concatenated blob shares a dictionary and avoids 8× per-block overhead. ZX02 is the
6502-optimized ZX0 variant (~138-byte decoder, fast, in-place backward decode).
Cost: RAM for the decompressed working set (≈ the Proposal 2 size).

A **P4f** variant ("flat") was also measured: ZX02 over the de-interleaved data **without**
RLE. Slightly larger compressed, but the decompressed form is a flat frame-indexed array —
even simpler playback (no run counters), at higher RAM cost.

### Proposal 5 — Synchronised frame-LZ (one context, offsets in frame units)  ❌ measured, rejected
The motivating insight: the 8 streams are advanced one value per frame and *repeat together*
— a phrase recurring replays every register at once. So lay the data out **frame-major** as
fixed-width `W`-byte records (3×2-byte tones + 1 noise + 2 nibble-packed volume bytes → W=9)
and run a **single** LZ pass whose literals, match lengths and offsets are all in **frame
units**. Decode needs one context: one offset, one match countdown, one ring of the last
`window` frames (`window × W` bytes). Two attractive properties: per-frame cost is *constant*
(exactly one frame consumed per frame — a long match never overruns the frame budget, VGC's
main failure mode), and held frames fall out for free as offset-1 overlap matches.

**It does not work** (§8.5). Forcing matches to be whole-frame is fatal: volume envelopes
change almost every frame, so the full record rarely repeats and nearly every frame degrades
to a raw `W`-byte literal. Result: 2.4–5.8× *larger* than VGC, worse even than RLE-only P2.
Splitting volumes into a second context (tones+noise | volumes, "P5-2") roughly halves the
size but still lands at 2.4× VGC. The experiment quantifies *why VGC de-interleaves*: per-column
independence captures partial repeats (e.g. a held bass under a moving melody) that any shared
frame-schedule structurally cannot. Implemented in `modules/framelz.py` (with a round-trip
self-check) and measured by `measure_proposal5.py`; kept for the record, not recommended.

### Format-aware tricks (layer onto any proposal)
- Keep `rle2` for 16-bit tones / split hi-lo (the tone high byte changes far less).
- Delta-code volume columns before RLE (envelopes are smooth) — repo has an unused `delta()`.
- Keep the noise LFSR `0x0f` skip marker and `0x08` EOF.
- RLE runs double as a "don't touch the chip" CPU optimization.

---

## 6. Related work (research)

- **snompiler** (github.com/joffb/snompiler) — *compiled music* for the Sega Master System:
  transpiles VGM into executable Z80 code (mostly `rst` dispatch calls), no decode loop.
  Output ≈ uncompressed VGM size. It's the **opposite extreme** from VGC: max decode speed,
  zero compression. Transferable idea = cheap per-token dispatch; but it ports poorly to
  6502 (no `rst`) and discards the de-interleave advantage. Reinforces Proposal 2's
  branch-dispatched cursor loop rather than literal compilation.
- **ZX0 / ZX02** (einar-saukas) — optimal LZ77/LZSS packer with a tiny fast 6502 decoder;
  in-place backward decode with a small delta margin. Basis of Proposal 4.
- **LZSA** (emmanuel-marty) — byte-aligned packer optimized for fast 8-bit decode.
- **stardot.org.uk 6502 compressors thread** — LZSS-streamed SN76489 player (~13%, volume
  nibbles packed 4→2 bytes/frame), explicitly trading ratio for decode speed; "~10% is the
  practical ballpark for SN music compression."
- **Arkos Tracker AKY** — sub-stream / no-LZ format, "very fast player, no buffer required" —
  same philosophy as Proposals 1–2.

---

## 7. Measurement methodology

Harness: `measure_proposal2.py` (repo root). For each VGM it computes, reusing the packer's
own `split_raw`/`rle`/`rle2`/`diff`/`combine_registers` so every scheme sees identical data:

- **raw** — distilled 11-byte/frame stream (what everything compresses from)
- **P2** — sum of the 8 RLE streams + minimal framing
- **P4** — `zx02.exe` over the concatenated de-interleave+RLE streams
- **P4f** — `zx02.exe` over the de-interleaved streams without RLE
- **VGC** — the real `.vgc` output (8× LZ4)
- **VGC+H** — `.vgc -n` (LZ4 + Huffman)

A second harness, `measure_proposal5.py` (reusing the same `distil`/`split_raw` helpers),
measures Proposal 5: it transposes the de-interleaved registers to frame-major fixed-width
records and runs the frame-LZ coder in `modules/framelz.py` at three windows, reporting the
P5-1 / P5-2 variants alongside P4/VGC/VGC+H (see §8.5).

Each metric runs independently (one broken column doesn't void the row) and prints its
failure reason. All artifacts (`.vgc`, `.zx02`, intermediate blobs) are **cached** in
`vgm/_cache/` and reused when newer than the source VGM, so re-runs are instant.
`zx02.exe` lives at `../fdload_dfs/bin/zx02.exe` (ZX0 v2.2). (P5 is pure-Python and fast, so
it is recomputed each run rather than cached.)

Test corpus: 11 real-world VGMs in `vgm/` (BBC + SMS PSG tunes, several taken from
previously-shipped demos as a representative comparison).

---

## 8. Results (bytes)

All 11 files now process cleanly in every column (the three toolchain bugs in §10 are fixed),
so the comparison is complete — including VGC+Huffman.

| file | raw | P2 (RLE) | P4 (zx02+RLE) | P4f (zx02 flat) | VGC | VGC+H | P4/VGC |
|---|--:|--:|--:|--:|--:|--:|--:|
| 6-16-export-evil-influences | 101,293 | 65,223 | **8,434** | 10,063 | 20,085 | 16,385 | 0.42× |
| BotB Slimeball — Fluid Dynamics | 21,847 | 18,953 | **3,491** | 3,926 | 5,674 | 5,003 | 0.62× |
| Collision Chaos | 14,846 | 10,929 | **1,569** | 2,177 | 2,209 | 2,095 | 0.71× |
| Diagonals | 48,282 | 31,355 | **5,603** | 6,329 | 11,568 | 9,491 | 0.48× |
| Ghost House (BBC) | 11,546 | 9,782 | **1,759** | 1,908 | 2,670 | 2,303 | 0.66× |
| U_LOADER | 15,695 | 14,087 | **1,196** | 1,236 | 3,537 | 2,896 | 0.34× |
| VE3 | 86,981 | 76,363 | **5,517** | 6,425 | 18,528 | 15,268 | 0.30× |
| intro_test | 12,564 | 10,963 | **559** | 719 | 771 | 786 | 0.73× |
| main_test | 47,163 | 40,998 | **3,870** | 4,568 | 6,794 | 5,851 | 0.57× |
| ne7-magic_beans (SMS PSG) | 20,281 | 15,898 | **3,087** | 3,656 | 5,708 | 4,959 | 0.54× |
| outro_test | 27,578 | 22,926 | **1,582** | 2,089 | 2,564 | 2,326 | 0.62× |

**Summary over all 11 comparable files** (raw = 408,076 B):

| scheme | % of raw | vs VGC |
|---|--:|--:|
| P2 — RLE only (8 cursors) | 77.8% | **3.96× bigger** |
| **P4 — zx02(de-interleave + RLE)** | **9.0%** | **0.46× (2.2× smaller)** |
| P4f — zx02(de-interleave, no RLE) | 10.6% | 0.54× (1.9× smaller) |
| VGC — 8× LZ4 (current) | 19.6% | 1.00× |
| VGC+H — LZ4 + Huffman | 16.7% | 0.85× |

Notes:
- **VGC+Huffman** now works on every file (previously crashed under Python 3) and beats plain
  VGC by ~15% on average. The one exception is `intro_test` (786 vs 771 B): a tiny payload
  where the shared Huffman table overhead slightly exceeds the savings.
- The most LZ-friendly tunes (`intro_test` 14.2× and `outro_test` 8.9× P2/VGC) are
  loader/jingle data dominated by long-range repetition — exactly where RLE-only collapses
  and ZX0/LZ shines.

### Decompressed RAM working set for P4 (≈ P2 size)
intro_test ≈ 11.0 KB · Ghost House ≈ 9.8 KB · Collision ≈ 10.9 KB · U_LOADER ≈ 14.1 KB ·
ne7-magic_beans ≈ 15.9 KB · BotB ≈ 19.0 KB · outro_test ≈ 22.9 KB · Diagonals ≈ 31.4 KB ·
main_test ≈ 41.0 KB · evil-influences ≈ 65.2 KB · VE3 ≈ 76.4 KB. This is the constraint
that decides whether P4 is usable for a given tune.

### 8.5 Proposal 5 (synchronised frame-LZ) results

Measured by `measure_proposal5.py` (frame-LZ in `modules/framelz.py`, greedy parse,
round-trip verified). `P5-1` = single 9-byte-record context; `P5-2` = split into
tones+noise (7 B) and volumes (2 B), two contexts. Each at three back-reference windows:
`w256` (ring ≈ 2.3 KB, ~VGC RAM regime), `w1820` (ring ≈ 16 KB, one sideways bank), `wInf`
(unlimited). Totals over all 11 files, summed against the same VGC baseline (80,108 B):

| scheme | RAM (ring) | bytes | vs VGC |
|---|---|--:|--:|
| P5-1 w256 | ~2.3 KB | 461,536 | 5.76× |
| P5-1 w1820 | ~16 KB | 351,171 | 4.38× |
| P5-1 wInf | full tune | 322,581 | 4.03× |
| P5-2 w256 | ~2.3 KB | 296,330 | 3.70× |
| P5-2 w1820 | ~16 KB | 219,706 | 2.74× |
| P5-2 wInf | full tune | 190,463 | 2.38× |
| *VGC (baseline)* | *2 KB* | *80,108* | *1.00×* |
| *P4 (reference)* | *full tune* | *36,667* | *0.46×* |

Every variant is larger than VGC — at matched RAM (`w256`) by 3.7–5.8×, and even with an
unlimited window by 2.4–4.0×. At a 16 KB bank (`w1820`) it is still 2.7× VGC *and* uses 8×
the RAM, i.e. strictly dominated by VGC on both axes; its only advantage is cheaper, constant
per-frame decode — not worth that cost.

Diagnostic detail: splitting volumes out (P5-1 → P5-2) nearly halves the size at every
window, confirming the volume envelopes are what break whole-frame matching. The single
biggest, most LZ-friendly losses are the loader/jingle tunes (`intro_test`: P5-2 wInf 3,025 B
vs VGC 771 B), where long-range repetition exists but is split across frames the shared
schedule can't match independently.

---

## 9. Interpretation

- **P4 wins on both axes where RAM allows.** A single ZX0 stream beats 8× LZ4 by ~2.5×
  *and* gives the trivial constant-time cursor-decode at playback. It is the clear default
  for tunes whose decompressed RLE set fits in RAM.
- **P2's poor ratio is diagnostic.** RLE saved only ~14% (raw→P2), so this music's
  redundancy is overwhelmingly *long-range pattern repetition*, which RLE cannot capture but
  LZ/ZX0 can. P2's only argument is decode simplicity/speed — and you pay ~4.4× in size.
- **For the streamed regime (tune doesn't fit decompressed), the real lever is "use a
  stronger byte coder than LZ4," not "drop to RLE."** P4 also hints VGC itself could be
  improved by swapping LZ4 for a ZX0-class coder, though per-frame ZX0 streaming across 8
  columns reintroduces the context-juggling cost.
- **P5's failure pins down VGC's load-bearing design choice.** Collapsing the 8 streams to a
  single frame-synchronised context is intuitively appealing (one context, constant per-frame
  decode), but it is 2.4–5.8× worse because *de-interleaving is the whole point*: per-column
  independence captures partial repeats that a whole-frame schedule cannot. The redundancy is
  long-range (so RLE-only P2 fails) *and* per-column-asynchronous (so frame-LZ P5 fails) — only
  per-column LZ (VGC) or a strong byte coder over the de-interleaved layout (P4) captures both.
  The cheap-decode goal is therefore better served by P4 + per-section decompression than by a
  new compression scheme.

---

## 10. Bugs found in the existing toolchain (now fixed)

The measurement run surfaced three real bugs in the stock `vgmpacker.py` / `modules/`
(faithfully reproduced by the harness, not harness artifacts). All three are now fixed and
the whole corpus packs cleanly:

1. **VGC+Huffman (`-n`) was broken under Python 3.** `modules/huffman.py buildTree()` pushed
   bare `[freq, …]` nodes onto the heap; on equal frequencies Python fell through to comparing
   an `int` symbol against a `list` node (`'<' not supported between instances of 'list' and
   'int'`), crashing on **every** file. **Fix:** heap entries are now `(priority, tiebreak,
   node)` tuples with a unique counter, so node payloads are never compared; the node shape is
   unchanged so `buildKey` is untouched.
2. **`as_binary()` crashed on an empty GD3 artist tag** (e.g. BotB). With an empty artist it
   fell back to `author = basename(...)` — a `str` — then `output_block.extend(author)` failed
   (`expected iterable of integers; got: 'str'`). **Fix:** encode the filename fallback to
   ASCII bytes, mirroring the title path.
3. **RLE round-trip assertion on out-of-range data** (Diagonals). `split_raw()` wrote a data
   byte (high bit clear) to `registers[latched_channel*2+1]` unmasked. Two cases land in a
   4-bit volume slot with an out-of-range value (e.g. `0x3c`) that later trips the `rle()`
   self-test: the noise channel (latched_channel 3 → `registers[7]`, channel-0 volume) and an
   unlatched data byte before any latch (latched_channel −1 → `registers[-1]` == `registers[10]`,
   channel-3 volume). **Fix:** noise-channel data bytes are routed to the noise register
   (`registers[6]`) masked; unlatched data bytes are ignored.

---

## 11. Recommendations

- **Default to Proposal 4** (ZX0/ZX02 store + RLE-cursor playback) for tunes whose
  decompressed RLE set fits in available RAM (incl. sideways RAM banks): smaller than VGC
  *and* faster/simpler to decode, with bounded per-frame time.
- **For tunes too large to decompress**, stay streamed but treat the lever as "stronger
  coder," not "RLE-only." Reserve **Proposal 2** for cases where decode CPU is the hard
  constraint and the size penalty is acceptable.
- **Consider Proposal 1** (change-mask) only for minimal-RAM or high-update-rate scenarios.
- **Do not pursue Proposal 5** (frame-synchronised LZ): measured 2.4–5.8× worse than VGC
  (§8.5). Its constant-per-frame decode is already available, more cheaply, from P4's cursor
  playback — chase cheap decode via P4 + per-section decompression, not a new coder.
- Carry forward the format-aware tricks (§5) and the noise `0x0f`/`0x08` markers regardless.

---

## 12. Next steps / open questions

1. Prototype the 6502 side: a ZX02 decompress-once loader + an 8-cursor RLE playback routine;
   measure actual per-frame cycle cost vs the existing VGC decoder.
2. Evaluate per-section / loop-point decompression so Proposal 4 scales to large tunes within
   limited RAM.
3. Explore swapping LZ4→ZX0-class coding inside a streamed format for the no-RAM case.

---

## 13. Sources

- snompiler — https://github.com/joffb/snompiler
- ZX0 — https://github.com/einar-saukas/ZX0 ; ZX02/6502 — https://forums.atariage.com/topic/336210-zx02-6502-size-optimized-compression
- LZSA — https://github.com/emmanuel-marty/lzsa
- stardot 6502 compressors thread — https://stardot.org.uk/forums/viewtopic.php?t=27861
- Arkos Tracker AKY — https://www.julien-nevo.com/arkostracker/index.php/the-aky-player/
- This repo's `README.md` (VGC format spec) and `vgmpacker.py` / `modules/vgmparser.py`.
