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

Three further follow-ups, all measured: **per-section decompression is viable** (capping the
window to one 16 KB bank costs only ~1.1 pp of LZ coverage, §8.7) — so P4 works on large tunes by
sectioning them; **delta pre-coding is a dead end** (it *hurts* both LZ4 +11.5% and ZX0 +14.3%,
§8.9); and **the tracker pattern grid cannot be recovered from the register stream** (§8.10 — no
grid survives envelopes/transposition; loses to VGC by 1.73×). Finally, for the strict regime of
**one 16 KB bank, no runtime decompression, and a known worst-case per-frame cost**, the answer is
*not* ZX0/P4 but a fixed *incremental* byte-aligned decoder whose cost is bounded independently of
match length — it fits ~9/11 corpus tunes; the two longest don't fit that regime at all (§12.4).

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

A third tool, `analyse_registers.py`, does no compression — it computes descriptive
statistics on the de-interleaved register columns (volatility, change taxonomy, run lengths,
entropy floor, delta gain, LZ coverage and offset distribution, per-section coverage, and the
column-major offset distribution) to guide successor design (see §8.6–§8.8). It is pure-Python
with no external dependencies, so it runs in any environment.

`measure_delta.py` measures delta pre-coding (§8.9) — baseline vs delta-coded columns under both
LZ4 and ZX0. `measure_patterns.py` measures tracker-grid recovery (§8.10) — pure Python, no deps.

Each metric runs independently (one broken column doesn't void the row) and prints its
failure reason. All artifacts (`.vgc`, `.zx02`, intermediate blobs) are **cached** in
`vgm/_cache/` and reused when newer than the source VGM, so re-runs are instant. ZX0 is located
via `$ZX0_BIN` / `../ZX0/src/zx0` / `../fdload_dfs/bin/zx02.exe` / `PATH` (all ZX0 v2.2; see
§12.0 to build it). (P5 and the pattern harness are pure-Python and fast, so they are recomputed
each run rather than cached.)

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

### 8.6 Register-data analysis (successor design guidance)

`analyse_registers.py` dissects the de-interleaved register columns across eight axes to
guide a VGC successor (the first six here, plus per-section coverage in §8.7 and the
column-major offset distribution in §8.8). All figures are corpus totals over the 11 files
(74,052 frames), pooled (not mean-of-means), computed on the same `split_raw` columns the
packer uses.

**(a) Volatility — % of frames where each register changes:**

| t0lo | t0hi | t1lo | t1hi | t2lo | t2hi | nois | vol0 | vol1 | vol2 | vol3 |
|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|
| 39% | 38% | 37% | 32% | 33% | 25% | **12%** | 47% | 43% | 17% | 44% |

Noise is near-static; volumes are the most volatile. The three register classes behave
differently enough to warrant different treatment. (The README's "tone high byte changes
far less" holds only weakly — t*hi is only slightly calmer than t*lo.)

**(b) Frame change taxonomy — what changes each frame** (the P5 post-mortem):

| both | tone-only | vol-only | idle |
|--:|--:|--:|--:|
| 59.1% | 11.1% | 20.1% | 9.7% |

**79% of frames change a volume** (both + vol-only). This is *why* whole-frame matching
(P5) fails: a frame rarely recurs exactly because volumes keep moving — yet those same
frames are trivial for per-column LZ, which ignores the unrelated volume churn. It varies
widely by tune (VE3 88.7% both; `intro_test` 53% tone-only; Collision 43% idle).

**(c) Run lengths — mean consecutive-equal run per register (pooled):**

| t0lo | t0hi | t1lo | t1hi | t2lo | t2hi | nois | vol0 | vol1 | vol2 | vol3 |
|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|
| 2.5 | 2.7 | 2.7 | 3.1 | 3.0 | 3.9 | **8.3** | 2.1 | 2.3 | 5.7 | 2.3 |

Mostly short (2–4). RLE earns its keep as a cheap pre-pass + skip-marker mechanism, not as
the main compressor — consistent with P2's poor standalone ratio.

**(d) Entropy floor (order-0, no LZ), corpus bytes:**

| measure | bytes | % of raw |
|---|--:|--:|
| raw (11 B/frame) | 814,572 | 100% |
| per-column order-0 floor | 285,131 | 35.0% |
| joint per-frame order-0 floor | 102,777 | 12.6% |
| distinct 9-byte frames | 33,419 / 74,052 | 45.1% |

Joint ≪ summed-marginals confirms strong inter-register correlation, but the joint alphabet
is huge (45% of frames are distinct), so that correlation is only realisable via
back-references — **not** a static per-frame table (a frame dictionary would dwarf the data).
The cheap, achievable no-LZ floor is the **per-column 35%** (≈ what an entropy coder alone
buys; close to what VGC+Huffman achieves). Entropy coding is a secondary lever; LZ is primary.

**(e) Delta coding — order-0 entropy (bits/symbol), raw vs frame-delta:**

| stream | raw H | delta H | reduction |
|---|--:|--:|--:|
| tone period | 4.45 | **3.09** | 31% |
| volume | 2.50 | **1.81** | 28% |

Delta-coding tone periods and volume columns before the coder is a real, currently-unused win
(the repo has a dormant `delta()`). Low-risk headroom for the successor.

**(f) LZ potential — match coverage and offset distribution** (greedy, unbounded window):

| axis | coverage (fraction expressible as a back-reference) |
|---|--:|
| **column-major** (8 logical streams) | **99.4%** |
| frame-major (whole 9-B frame) | 54.9% |

This 99.4% vs 54.9% gap is the headline of the whole study: **de-interleaving is
load-bearing.** Frame-major coupling throws away ~45% of reachable redundancy — the exact
ceiling P5 hit.

Frame-major match **offset distribution** (% of matched frames by back-distance):

| 1 | 2–15 | 16–63 | 64–255 | 256–1k | 1k–4k | 4k+ |
|--:|--:|--:|--:|--:|--:|--:|
| 9.0% | 3.9% | 6.6% | 22.9% | 34.6% | 15.4% | 7.6% |

Only ~9% are offset-1 (held frames; RLE territory) and only ~19% fall within 255. **Half the
matches live at 256–4k frames back** — long-range phrase repetition. A successor's offset
coder must reach thousands of frames, which means either the decompress-once regime (P4) or a
genuinely large window. This is the same fact that made `zx02 -m256` cost ~2× and made
unbounded P4 win.

**Design conclusions:**
1. **Keep de-interleaving** — it is the single biggest lever (99.4% coverage). No row-major,
   no whole-frame coupling.
2. **Treat the three register classes separately** — noise (sparse), tones (medium),
   volumes (volatile, dominate the budget).
3. ~~Add delta pre-coding~~ — **measured, rejected (§8.9).** The order-0 entropy win does not
   survive a real coder: delta makes both LZ4 (+11.5%) and ZX0 (+14.3%) *worse* by destroying
   the literal runs they match on.
4. **Use a wide-window, strong coder** (ZX0-class) over the de-interleaved layout — the
   redundancy is long-range, so the window/offset must reach thousands of frames. **This is a
   *storage* statement, not a runtime one:** ZX0's bit-level, variable-length decode is the
   *worst* tool to run per-frame across 8 columns. Use it once, in the decompress-once regime
   (P4) — store the columns **concatenated as a single ZX0 stream** (one context, shared
   dictionary), decompress per bank into RAM, then play the flat cursor loop. "Per column" is
   the data *layout*, not eight runtime coders.
5. **RLE and entropy coding are secondary** — useful cheap passes, not the main event.

The implied successor shape is *VGC's column layout + a ZX0-class wide-window coder over the
concatenated columns*, used **decompress-once per bank → flat cursor playback** so the strong
coder never runs in the per-frame path. §8.7 shows bank-sized sections cost only ~1.1 pp of
coverage, so this resolves the RAM/decode tension. (See §12.4 for the harder single-bank,
no-decompress, bounded-worst-case regime, where ZX0 is *not* applicable and the answer is a
fixed incremental byte-aligned coder instead.)

### 8.7 Per-section LZ coverage (is bank-capped P4 viable?)

§8.6(f) measured LZ coverage with an *unbounded* window (99.4% column-major). But the P4
"decompress-once" regime only stays usable on large tunes if you can decompress **one
bank-sized section at a time** — which caps how far back a match may reach. This axis
hard-partitions each of the 8 column-major streams into sections of `N` frames (so **no match
crosses a section boundary**, exactly as if each section were decompressed independently) and
runs unbounded LZ *within* each section. A 16 KB BBC sideways-RAM bank holds ≈1820 frames at
9 B/frame; we bracket half-, one- and two-bank sections. Corpus totals:

| section (frames) | ≈ KB/bank | column-major coverage |
|---|--:|--:|
| 455 | 4.0 | 96.1% |
| 910 | 8.0 | 97.5% |
| **1820 (one bank)** | **16.0** | **98.3%** |
| 3640 | 32.0 | 98.8% |
| unbounded | full tune | 99.4% |

**Per-section decompression is viable.** Capping the window to one 16 KB bank costs only
~1.1 percentage points of coverage (98.3% vs 99.4%) — the redundancy in this music is
overwhelmingly *intra-bank*, not whole-tune-spanning. Even a 4 KB section keeps 96.1%. So a
large tune (VE3, evil-influences) can be split into bank-sized sections, each ZX0'd and
decompressed on demand, with negligible ratio loss versus a single unbounded stream. This
removes the only objection to P4 (the RAM regime, §1/§4) for tunes that don't fit decompressed:
section them. The decode loop is unchanged (P2's cursors); only a per-section reload is added.

### 8.8 Column-major match offset distribution

§8.6(f) charted the *frame-major* offset spread. This is the **column-major** equivalent —
where the VGC-axis matches actually come from, measured in each stream's own byte units (tone
streams are 2 B/frame, noise + the four volumes are 1 B/frame). It sizes the offset-field width
a *streamed* per-column successor coder must encode.

| 1 | 2–15 | 16–63 | 64–255 | 256–1k | 1k–4k | 4k+ |
|--:|--:|--:|--:|--:|--:|--:|
| 9.0% | 8.1% | 9.2% | 21.5% | 28.8% | 15.9% | 7.6% |

Only **~48% of matched bytes fall within offset 255** (1 + 2–15 + 16–63 + 64–255). The
remaining ~52% reach 256 bytes – thousands back. **This indicts VGC's window directly:** VGC
uses 8-bit (1-byte) offsets — a 255-byte window — so it can express *under half* of the
per-column redundancy that's actually there. That single design choice (chosen to keep the
6502 decoder's per-stream buffer at 256 bytes) is the biggest gap between VGC and a ZX0-class
coder over the same de-interleaved layout (P4's 0.46×). A streamed per-column successor that
wanted to close that gap *without* the decompress-once regime would need a ≥16-bit offset field
and a kilobyte-plus window per stream — which reintroduces exactly the per-stream RAM/context
cost VGC's small window was avoiding. This is the quantified tension that makes P4
(decompress-once, wide window, trivial cursor playback) the better lever than a wider-window
streamed VGC.

### 8.9 Delta pre-coding (measured — rejected)

§8.6(e) showed frame-delta cuts the *order-0* entropy of tone periods ~31% and volumes ~28%.
Item 2 of the plan asked whether that survives into real compressed bytes once a
back-referencing coder is applied. It does not — **delta makes both coders worse**, because it
destroys the long literal runs that LZ/ZX0 match on (a held note is one long equal-byte run
pre-delta and a run of zeros post-delta — both already collapse under LZ; delta just shuffles
which one, while breaking *cross-instance* matches). Measured by `measure_delta.py` on the flat
de-interleaved columns (tone + volume columns delta-coded; noise keeps its `0x0f` skip diff),
under both per-stream LZ4 (255 window) and a single ZX0 stream (= P4f). Corpus totals:

| coder | baseline | + delta | change |
|---|--:|--:|--:|
| LZ4 (255-window, per stream) | 114,458 | 127,656 | **+11.5%** |
| ZX0 (single stream, = P4f) | 38,644 | 44,174 | **+14.3%** |

Every file regresses under both coders (LZ4 +6…+24%, ZX0 +9…+32%); the sole near-neutral case
is `intro_test` under LZ4 (0.99×). So the order-0 entropy win is a mirage once LZ/ZX0 is in
play — **the successor should not delta-code.** (The format-aware "delta volumes before RLE"
trick in §5 is likewise not worth it.) Note this was measured on the *flat* layout, not the
RLE'd VGC streams: VGC's `rle2`/`rle` are value-format-specific — `rle2` asserts 10-bit tone
words (lo ≤ 15, hi ≤ 63) and `rle` packs a 4-bit volume into the low nibble — and cannot ingest
signed deltas without a redesigned RLE, so the flat layout is the clean place to isolate the
question. The conclusion (delta hurts a real coder) is coder-level and carries over.

### 8.10 Pattern-layer recovery (measured — rejected)

These tunes were authored in a tracker as a **shared order list** (a fixed-length pattern grid,
constant when the tempo is constant) with per-channel pattern content. That structure is lost on
VGM export. Could it be **recovered** from the register stream — grouping the 11 columns back
into the 4 SN76489 channels (ch0/1/2 = tone lo+hi+vol, ch3 = noise+vol) and deduping
fixed-length, grid-aligned blocks? A pattern index has *bounded* decode cost (a pointer-jump at
each grid boundary, no LZ window, no decompression), so if it worked it would fit the
single-bank / known-worst-case regime ideally. Measured by `measure_patterns.py` (sweeps grid
length `L` and frame phase). **It does not work:**

- **No grid peak.** If a real tracker grid survived, mean per-channel block coverage would
  *peak* at the musical `L`. Instead it **decays monotonically** with `L` — VE3: 55.6% at L=64
  → 22.3% at 192 → 9.8% at 384; evil-influences: 41.5% → 16.9% → 10.6%. Best-phase alignment
  (ruling out "patterns don't start at frame 0") doesn't change this (VE3 best-phase @256 =
  21.9%, evil = 7.5%).
- **Size-optimal `L` collapses to micro-blocks** (8–12 frames) — i.e. there is only short-range
  repetition (which LZ/RLE already get), no musical-scale fixed grid. Per-channel best-`L`
  values don't even agree across channels (VE3: `[8, 28, 8, 14]`), so there is no shared grid to
  recover.
- **It loses to VGC.** The best (size-optimal) pattern layer, LZ4'd, totals **1.73× VGC**, and
  the large tunes (evil 40,978 B, VE3 23,897 B, Diagonals 19,770 B) **still don't fit a 16 KB
  bank** — worse than VGC, not better. The whole-frame ("vertical", literal-tracker-pattern)
  variant is far worse again (≈2× the per-channel size), reconfirming §8.6(f).

The cause is the same trio that sank P5: **per-frame volume envelopes**, plus **pattern
transposition** (the same phrase replayed at a different pitch → different tone periods → no
byte match; pitch is logarithmic so it isn't even an additive delta) and per-instance
**variation**. Two plays of the "same" pattern are simply not byte-identical at register level.
The repetition is real (LZ gets 99.4%) but only reachable via *flexible-offset, flexible-length,
partial* back-references — which is exactly the variable-cost decode we were trying to avoid.

**Conclusion:** the pattern/order data cannot be reverse-engineered from the `.vgc` register
stream. If a bounded-cost pattern layer is wanted, it must be taken **upstream** — from the
tracker module (the native song/score) *before* VGM export, where the grid still exists
losslessly — not recovered after the fact.

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
- **Do not delta-code** (§8.9): it makes LZ4 (+11.5%) and ZX0 (+14.3%) worse.
- **Do not try to recover the tracker pattern grid from the register stream** (§8.10): it is not
  there to recover (envelopes/transposition/variation destroy exact repeats); take pattern data
  from the upstream tracker module if a bounded-cost pattern layer is wanted.
- **For a strict single 16 KB bank with bounded worst-case per-frame cost and no runtime
  decompression** (§12.4): use a fixed *incremental* byte-aligned coder (VGC/LZ4/LZSA-class) that
  yields one value per stream per frame — not ZX0, not P4. Fits ~9/11 corpus tunes; the two
  longest don't fit that regime at all.
- Carry forward the format-aware tricks (§5, minus delta) and the noise `0x0f`/`0x08` markers.

---

## 12. Next steps / continuation plan

This section is written to be picked up cold (e.g. on the claude.ai/code web app on a fresh
clone), without the local laptop state. Read §8.6 first — it sets the design direction.

### 12.0 Environment note (IMPORTANT — read before running anything)

The repo is self-contained for *most* tooling, but **ZX0/ZX02 is not in this repo**:

- `analyse_registers.py`, `measure_proposal5.py`, and `vgmpacker.py` itself are **pure Python 3,
  no external deps** — they run anywhere, including the web app, against the committed corpus in
  `vgm/`. Start here.
- `measure_proposal2.py`'s **P4 / P4f columns shell out to `zx02.exe`** at
  `../fdload_dfs/bin/zx02.exe` — a *Windows binary in a sibling repo* that will **not** exist on
  a fresh clone or a Linux cloud box. Its VGC / VGC+H columns still work (pure Python). To
  reproduce P4 elsewhere you must first obtain ZX0:
  - The repo Makefile uses OpenWatcom (`owcc`); on a plain Linux box just compile the C sources
    directly: `git clone https://github.com/einar-saukas/ZX0 && cd ZX0/src &&
    cc -O2 -o zx0 zx0.c optimize.c compress.c memory.c`. This builds ZX0 v2.2 (the same compressor
    the original `zx02.exe` was — flags `-f <in> <out>`, and `-m <n>` for the bounded-window
    experiments in §3/§8.5).
  - **`measure_proposal2.py` now finds ZX0 automatically:** `$ZX0_BIN`, then `../ZX0/src/zx0`,
    then the old `../fdload_dfs/bin/zx02.exe`, then `zx0` on `PATH`. So once built as above, the
    P4/P4f columns just work. Reproduced on Linux: VGC 19.6% and P2 77.8% match exactly; **P4
    lands at 8.7% / 0.44×** (marginally better than the documented 9.0% — this ZX0 build is a
    touch stronger than the original).
  - All `zx0`/`zx02` outputs are cached under `vgm/_cache/` (gitignored), so once built, re-runs
    are instant. (Don't run two cache-sharing harnesses concurrently — see the guard in
    `measure_delta.py`.)

### 12.1 Ranked work items

Ordered by value-per-effort given §8.6. Items 1–2 need no external tools.

1. **Per-section LZ coverage sweep (no deps).** ✅ **Done** — implemented in
   `analyse_registers.py` (axis 7) and reported in §8.7. It hard-partitions each column-major
   stream into sections of `N` frames (no cross-section matches) and runs unbounded LZ within
   each section, bracketing half-/one-/two-bank sizes. **Finding:** capping to one 16 KB bank
   costs only ~1.1 pp of coverage (98.3% vs 99.4% unbounded) — per-section decompression is
   viable, the redundancy is overwhelmingly intra-bank. This removes the RAM-regime objection to
   P4 for large tunes: section them.

2. **Delta pre-coding measurement.** ✅ **Done** — implemented in `measure_delta.py` and reported
   in §8.9. **Finding:** delta *hurts* — LZ4 +11.5%, ZX0 +14.3% — because it destroys the literal
   runs the coders match on. The successor should not delta-code. (Measured on the flat layout;
   the RLE'd VGC path can't ingest signed deltas without a redesigned RLE, but the conclusion is
   coder-level and carries over.)

3. **Column-major offset histogram.** ✅ **Done** — implemented in `analyse_registers.py`
   (axis 8) and reported in §8.8. **Finding:** only ~48% of matched bytes fall within offset 255,
   so VGC's 1-byte offset (255-byte window) can express under half the per-column redundancy that
   exists; ~52% of matches reach 256 B – thousands back. A streamed per-column successor would
   need a ≥16-bit offset field (and the RAM that implies), which is why P4's decompress-once
   wide-window regime is the better lever than a wider-window streamed VGC.

4. **Prototype the successor coder (ZX0 now buildable, §12.0).** Per the revised §8.6 conclusions:
   VGC's column layout + a ZX0-class coder over the **concatenated** columns (no delta — §8.9),
   used decompress-once. Measure against VGC and P4. (P4 already *is* essentially this; the open
   piece is per-bank sectioning + a flat playback prototype.)

5. **6502-side validation.** Prototype a ZX02 decompress-once loader + the 8-cursor RLE playback
   loop (Proposal 4's runtime), and measure real per-frame cycle cost vs the existing VGC decoder.
   This is the claim that P4 is both smaller *and* cheaper to decode — currently asserted, not
   measured on hardware/emulator.

6. **Incremental-decode worst-case model for the single-bank regime (§12.4).** ✅ **Done** —
   6502 prototype in `beeb/` (player + packer + py65 verification). Decoder byte-exact in
   simulation; **measured worst-case per-frame cost 3927 cycles = 9.8% of the 50 Hz budget**,
   bounded and ~independent of match length. Remaining: confirm the sound-chip /WE strobe timing
   on real hardware/emulator (the one thing the simulator can't exercise).

### 12.2 Rejected — do not revisit

- **Proposal 5 (frame-synchronised LZ):** 2.4–5.8× worse than VGC (§8.5); de-interleaving is
  load-bearing (§8.6f, 99.4% vs 54.9% coverage). Code kept in `modules/framelz.py` for the record.
- **Proposal 3 (single-stream interleaved LZ):** throws away de-interleaving — same root flaw.
- **`zx02 -m256` as a P4 ratio play:** forfeits the long-range matches that are the point
  (§8.6f); only ~19% of matches fall within 255 frames. It is a *streamed-regime* option (~0.85×
  VGC, single context), not a way to keep P4's 0.46×.
- **Delta pre-coding (§8.9):** hurts both LZ4 (+11.5%) and ZX0 (+14.3%). Measured by
  `measure_delta.py`; kept for the record.
- **Recovering the tracker pattern grid from register data (§8.10):** not recoverable (no grid
  peak; loses to VGC by 1.73×). Measured by `measure_patterns.py`. Pattern data must come from the
  upstream tracker module, not the `.vgc` stream.

### 12.3 Repo map for a fresh start

- `vgmpacker.py` — the current VGC packer (pipeline in `process()`; self-verifying encoders).
- `modules/vgmparser.py` — VGM → distilled per-frame stream (`VgmStream.as_binary()`).
- `modules/framelz.py` — Proposal 5 frame-LZ (rejected; reference only).
- `measure_proposal2.py` — P2/P4/P4f/VGC/VGC+H harness (auto-finds ZX0, see §12.0).
- `measure_proposal5.py` — P5 harness.
- `measure_delta.py` — delta pre-coding harness (§8.9; needs ZX0 for its ZX0 columns).
- `measure_patterns.py` — pattern-grid recovery harness (§8.10; pure Python).
- `analyse_registers.py` — descriptive stats (§8.6–§8.8); the place to start new analysis.
- `beeb/` — 6502 incremental-decode player prototype for §12.4 (BeebAsm + packer + py65 tests +
  bootable `music.ssd`); see `beeb/README.md`.
- `vgm/` — the 11-file corpus (committed); `vgm/_cache/` — artifacts (gitignored).
- `docs/compression-analysis.md` — this document.

### 12.4 The single-bank, bounded-worst-case regime (no runtime decompression)

A distinct target from P4's decompress-once regime, and the harder one: **code + compressed data
in one 16 KB SWRAM bank, a known worst-case per-frame cost, no second bank, and no bulk
decompression competing with timing-critical effects.** This rules out P4/P4f (need the
decompressed working set resident *and* spend the decode at runtime) and any wide-window/ZX0
streaming (bit-level variable decode; the windows alone exceed the bank).

Key fact that makes it tractable: in a *streamed per-column* decoder you pull **one value per
stream per frame**, so a long match never lands in a single frame — it costs one copy per frame
as you consume it. With an *incremental* decoder (per-stream state: `literal_remaining` /
`match_remaining` / `rle_remaining`), the worst case is bounded *independently of match/run
lengths*:

```
worst_frame ≈ Σ over 8 streams ( token_parse + one_copy )
```

i.e. the pathological frame is "all 8 streams expire a run at once" → 8 × (read control byte,
maybe a 1-byte offset, one copy) — a small fixed bound, not a function of the data. VGC's
"variable per-frame cost" (§3) is therefore a *decoder-implementation* artifact (it expands whole
LZ4 sequences on refill), **not** a format limit; a fixed incremental decoder over the existing
VGC/LZ4 layout (or a byte-aligned LZSA-class one) certifies the bound. Workspace (~2 KB of ring
buffers) lives in **main RAM**, not the bank.

Sizing against the bank (using the measured VGC sizes as the streamed proxy, ~14 KB data budget
after code): **9/11 corpus tunes fit** (intro 771 … Diagonals 11,568). Only the two longest —
**evil 20,085 and VE3 18,528** — don't, and nothing in this regime saves them: a stronger byte
coder forces bit-level variable decode (out), a wider window forces > bank RAM (out, §8.8), RLE-
only is 4× too big (§8), and pattern recovery loses to VGC (§8.10). Those tunes genuinely need
either the decompress-once regime (give up "no decompression"), multi-bank storage (give up
"one bank"), or upstream re-authoring shorter / as native pattern data (§8.10).

**Prototype built and certified (item 6, done).** `beeb/` contains a 6502 player for this
regime (BeebAsm) plus a Python packer (`.vgi` = 11 per-register columns, each a byte-aligned
LZSS over a 256-byte ring, 8-bit offsets, decoded one value per stream per frame). Verified in a
py65 6502 simulation: the decoder is byte-exact vs the source, and the full SN76489 output
matches. **Measured per-frame cost: min 2886 / mean 2924 / max 3927 cycles** — the worst frame is
**9.8% of the 50 Hz budget** (40000 cyc @ 2 MHz), with a 1041-cycle spread, confirming the cost
is bounded and ~independent of match length. A bootable disc (`beeb/music.ssd`, Ghost House,
~51 s) is included; only the sound-chip /WE strobe timing remains to be confirmed on hardware.

**Head-to-head vs the existing VGC player, full corpus** (`beeb/bench_all.py`: both players
through the same py65 cycle measurement on all 11 tunes, SN write stubbed in both so only
decode + register reconstruction is timed). Fixed footprint: incremental = 536 B code + 2816 B
buffers (11×256) + 7 zp = **3359 B**; VGC = 768 B code + 2048 B buffers (8×256) + 8 zp =
**2824 B**. Per-frame cost (cycles, 50 Hz budget = 40000) and compressed size:

| | `.vgi` total | `.vgc` total | incr worst frame | VGC worst frame | incr mean range | VGC mean range |
|---|--:|--:|--:|--:|--:|--:|
| corpus (74052 frames) | 122,101 | 80,108 | **2787 (7.0%)** | **5396 (13.5%)** | 1508–1600 | 996–2557 |

Three clear results: (1) **size** — `.vgi` is **1.52× larger** than `.vgc` (flat per-column LZSS
vs RLE+LZ4 is the price of the simpler decoder); (2) **worst-case decode** — the incremental
player is **bounded and flat at ≤2787 cycles (≤7.0%) on every tune**, consistently **~half** the
VGC player's 4814–5396 (≤13.5%) spikes (which occur when several streams refill their LZ4 at
once — the §3 variable cost, now measured); (3) **mean decode** — the incremental mean is
rock-steady (~1550) while VGC's is data-dependent (cheap on repetitive tunes via RLE-run skips,
dearer on busy ones).

The per-frame **distribution** (`beeb/plot_dist.py`, fig. `beeb/frame_cost_distribution.png`)
shows VGC's spikes are *frequent, not one-off*. Corpus percentiles (cycles):

| | p50 | p90 | p99 | p99.9 | max |
|---|--:|--:|--:|--:|--:|
| incremental | 1466 | 1752 | 2167 | 2428 | **2787** |
| existing VGC | 1557 | 2995 | 3872 | 4307 | **5396** |

VGC exceeds the incremental decoder's *entire* worst case (2787) on **13.4% of frames**, and
3500 cycles on 3.6%; the >4500 spikes are rare (0.04%) but set the ceiling you must budget for.
The incremental decoder never crosses 2787 (its p99.9 is 2428). So the incremental scheme trades
~1.5× storage (and ~0.5 KB RAM) for a bounded, predictable worst case — exactly what a
timing-critical, single-bank player budgets against. Full per-tune table (with min/median) in
`beeb/README.md`.

---

## 13. Sources

- snompiler — https://github.com/joffb/snompiler
- ZX0 — https://github.com/einar-saukas/ZX0 ; ZX02/6502 — https://forums.atariage.com/topic/336210-zx02-6502-size-optimized-compression
- LZSA — https://github.com/emmanuel-marty/lzsa
- stardot 6502 compressors thread — https://stardot.org.uk/forums/viewtopic.php?t=27861
- Arkos Tracker AKY — https://www.julien-nevo.com/arkostracker/index.php/the-aky-player/
- This repo's `README.md` (VGC format spec) and `vgmpacker.py` / `modules/vgmparser.py`.
