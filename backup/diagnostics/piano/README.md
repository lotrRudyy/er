# Piano Diagnostics (ESP32)

Non-production sketches for piano_riddle capture/debug on the ESP32. Keep one canonical sketch per tool and evolve via git history (no `_v2` files).

## Layout
- `record_raw_i2s/record_raw_i2s.ino` — records raw I2S frames + FFT magnitudes for calibration runs; logs to Serial and SPIFFS for host-side ingestion.
- `capture_features_fft/capture_features_fft.ino` — runs FFT-based feature extraction / note detection loop and streams JSON metrics for debugging.

## Usage
- Flash manually (not part of the main firmware build).
- Open Serial (115200 baud) and follow prompts/commands in the sketch (`start`, key cues, etc.).
- After a run, pull artifacts from SPIFFS and store under `er1/data/piano_riddle/raw/sessions/<timestamp>_<tag>/` on the host.

## Notes
- Lab-only utilities; do not ship in production firmware.
- If you need variants, branch/change these files rather than adding new `_vN` sketches.
