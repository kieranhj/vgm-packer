# plots/ — figure scripts

These turn the cached benchmark results in `../bench/_cache/` into the `.png`
figures kept alongside them here. **Run the matching benchmark first** (it writes
the `.pkl`); then run the plot script.

## Prerequisites

- Python 3 with `pip install matplotlib numpy`.
- The cache file each script reads — produced by a script in `../bench/` (see the
  table). If the `.pkl` is missing, run that benchmark first.

Run each script **from this `plots/` directory**; the `.png` is written here.

## Scripts

| plot script | reads (from `../bench/_cache/`) | produced by | output `.png` |
|---|---|---|---|
| `plot_dist.py` | `bench_costs.pkl` | `bench_all.py` | `frame_cost_distribution.png` |
| `plot_players.py` | `players.pkl` | `bench_players.py` | `players_distribution.png`, `players_timeseries.png` |
| `plot_v2.py` | `v2_runtime.pkl` | `measure_v2_runtime.py` | `v2_cost_distribution.png` |

## Example

```sh
# 1) generate the cache (from ../bench)
cd ../bench && python bench_players.py

# 2) draw the figures (from ../plots)
cd ../plots && python plot_players.py
```

The committed `.png` files in this folder are the figures referenced by the
documents in `../docs/` (e.g. `compression-analysis.md`,
`incremental-player-prototype.md`).
