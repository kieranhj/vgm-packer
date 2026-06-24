# VGC player — cycle analysis & optimisation plan

*Analysis of the **existing** VGC player (`vgm-player-bbc/lib/vgcplayer.asm`,
huffman disabled) and a plan to cut its per-frame cost. Measured in py65;
1 frame @ 50 Hz = 40000 cycles @ 2 MHz. This is the player the VGI work has been
compared against; it is a separate project, so this is a plan, not edits.*

## Method

Built the player standalone (`sim_vgc.asm`), ran `vgm_update` for the whole tune
(Ghost House, 2559 frames) in py65 and attributed every executed instruction's
cycles to a code region by PC. Also counted calls to `vgm_get_register_data`
(= LZ-decoded bytes per frame).

## Where the cycles go (measured)

Per-frame total: **min 294 / mean 1538 / max 5638**. Decodes/frame: **min 0 /
mean 3.57 / max 11** (the RLE layer means most register slots just decrement a
counter; only some actually LZ-decode each frame).

| region | % of all cycles | per frame | per decoded byte |
|---|--:|--:|--:|
| **ZP context swap** (`vgm_get_register_data`) | **38.1%** | 586 | **164** |
| RLE dispatch + chip-write setup (`vgm_update_register1/2`) | 27.9% | 428 | — |
| actual LZ decode (`lz_decode_byte` + buffer fetch/store) | 21.6% | 332 | 93 |
| sound write (`sn_write`) | 7.2% | 110 | — |
| frame dispatch (`vgm_update`) | 5.3% | 82 | — |

## The headline problem: the per-byte context swap

`vgm_get_register_data` decodes **one** byte for a stream, but to do so it
**copies that stream's entire LZ context into a single shared zero-page/SMC
working set and copies it all back afterwards** — every byte:

- load 8 bytes from `vgm_streams[x]` → `zp_stream_src`, `zp_literal_cnt`,
  `zp_match_cnt`, and the two window SMC operands; then
- (decode one byte); then
- store those 8 bytes back to `vgm_streams[x]`.

That bookkeeping is **164 cycles/decode — nearly double the 93 cycles of actual
decompression — and 38% of the whole frame.** It exists because one compact
202-byte LZ decoder is *shared* across all 8 streams via fixed ZP labels and
self-modified window pointers; switching streams therefore means swapping the
context in and out. It is the classic small-code / slow-runtime trade, and it is
also the single thing the VGI player avoids by keeping each stream's state
resident (no swap) — which is most of why VGI's decode is cheaper and flatter.

On a busy frame (11 decodes) the swap alone is **11 × 164 ≈ 1800 cycles** — it is
the main driver of the 5638-cycle spikes, i.e. of the *variance*, not just the
mean.

## Implemented (`bench/vgc/`, measured)

The context-swap fix is **done and verified**. `bench/vgc/vgcplayer_opt.asm` keeps
each stream's LZ state **resident** instead of swapping it through ZP per byte:
X holds the stream index for the whole decode; literal/match counts are accessed
`abs,X` in place; the window buffer is `abs,Y` with its page self-modified once
per decode and the read/write indices `inc`-ed in place; the window fetch/store
are inlined (no JSR); only the stream read pointer is loaded once and saved once;
and because `get_register_data` now preserves X, the `vgm_temp` save/restore is
gone. `bench/vgc/measure.py` checks the SN76489 output is **byte-identical** to
the original and times both.

Measured (Ghost House, 2559 frames; confirmed byte-exact on evil-influences and
ne7 too):

| | mean | p99 | max | total |
|---|--:|--:|--:|--:|
| original | 1538 | 4052 | 5638 | 3,935,747 |
| optimised | **1172** | **2931** | **4840** | 2,998,368 |
| speedup | **1.31×** | 1.38× | 1.16× | 1.31× |

~24% off the mean and ~14% off the worst frame, for +0 RAM and *less* code (627
vs 757 bytes — the swap code was bigger than the resident accessors). That cuts
the per-decode swap from 164 to ~70 cycles. The remaining headroom is the
~70-cycle per-decode setup (window-page SMC + pointer load/save), which only a
full per-stream **unroll** removes (Tier A1 below) — the bigger, +~1 KB step.

## Plan, ranked by cycles saved

### Tier A — eliminate (or shrink) the context swap  ← biggest win

**A1 (full, recommended): unroll the decoder per stream.** Give each of the 8
streams its own copy of the decode state machine with its context hard-wired
(its `zp_*`/SMC operands baked in), so no swap is ever needed — exactly the
approach the VGI player's `-D UNROLL=1` build uses. Removes the full 164/decode.
- Expected: mean **1538 → ~950**, worst frame **5638 → ~3800** (the spikes shrink
  most, since they have the most decodes).
- Cost: decoder code ~202 B → ~1.4–1.6 KB (8 copies). Same trade VGI took.

**A2 (partial, cheaper): keep counts resident, SMC only the pointers.** Hold
`literal_cnt`/`match_cnt` per stream in `vgm_streams[x]` and access them with
`abs,X` (4 cyc) instead of copying to ZP; self-modify only the stream-source
fetch address and the two window operands from `vgm_streams[x]` (a few bytes in,
a few out) rather than the full 8-byte swap. Roughly **halves** the swap
(~80/decode saved → ~290/frame mean) for little extra code. A good middle option
if ~1.5 KB of unrolled code is too much for the bank.

### Tier B — unroll the 8-register dispatch & RLE fast path (~150–200/frame)

`vgm_update` issues 8 `lda #n : jsr vgm_update_register1/2`, and each register
does `tax / clc / dec counts,x / bne / rts` plus a `stx vgm_temp … ldx vgm_temp`
dance (because `vgm_get_register_data` clobbers X). Unrolling the eight register
slots into straight-line code with constant indices drops the per-call JSR/RTS,
the `tax`, and the `vgm_temp` save/restore on every register — including on the
**floor** frames (the 294-cycle all-RLE-run case), which become ~200.

### Tier C — inline the window fetch/store (~24/decode, ~85/frame)

`lz_decode_byte` calls `lz_fetch_buffer` and `lz_store_buffer` as subroutines
(each `jsr`+`rts` = 12 cycles overhead) on every decoded byte. They are 2-3
instructions each; inlining removes ~24 cycles/decode. (The code already notes
"cheaper to inline".)

### Tier D — minor

- `sn_write` is already the minimal no-busy-wait sequence; leave it.
- The RLE `clc` and `vgm_temp` traffic shrink naturally under Tier B.
- A 7-bit peek table would speed the **huffman** path a lot, but huffman is off in
  the measured config and is inherently the "smaller/slower" option anyway.

## Expected result

| step | mean | worst frame | decoder code |
|---|--:|--:|--:|
| current | 1538 | 5638 | ~0.95 KB |
| + Tier C (inline fetch/store) | ~1450 | ~5300 | ~0.95 KB |
| + Tier B (unroll dispatch/RLE) | ~1300 | ~5100 | ~1.1 KB |
| + Tier A2 (half the swap) | ~1000 | ~3900 | ~1.2 KB |
| + Tier A1 (kill the swap, unroll) | **~800–900** | **~3300** | **~2.2 KB** |

Tier A is worth ~3–4× the rest combined and, crucially, attacks the **spikes**
(it scales with decodes/frame). The structural conclusion mirrors the VGI work:
**stop swapping a shared context per byte — make each stream's state resident
(unroll)** — trading code size for both a lower mean and a much tighter worst
case. After Tier A the VGC player would sit close to the VGI looped build, though
VGI's v2 format + RUN token still gives it the lower *decode* count per frame.

*(Implementation would land in the `vgm-player-bbc` repo, not here; this document
is the analysis/plan only.)*
