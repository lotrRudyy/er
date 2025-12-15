# Piano calibration artifacts

## What belongs in this directory
- Final per-key/map summary JSON files (`map_*` and `cal_*`) that capture the compact output of a completed calibration run.

## What stays out
- Runtime logs, streaming dumps, and raw key/state traces belong under `data/logs/` and are ignored by git.
- Intermediate/raw calibration captures should be kept locally (or archived elsewhere) but not committed here.

## File naming
- `map_YYYY-MM-DD_fw-X.YY.json` for finalized mapping summaries.
- `cal_YYYY-MM-DD_fw-X.YY.json` for finalized calibration summaries.

## Size & rotation
- Keep each file ≤ 500 KB to avoid inflating the repo.
- Retain only the latest two files per piano; older summaries can be pruned once verified and safely archived.
