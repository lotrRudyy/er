# Audio Diagnostics – Piano

These sketches are **non-production diagnostic tools** used to validate
the microphone → ESP32 signal chain before calibration and Goertzel work.

## Files

- `diag_dual_mic_cal_10keys_v1.ino`
  - Records 10 keys (A0 upward)
  - Captures parallel stats from:
    - MAX9814 → ADC1
    - INMP441 → I2S
  - Outputs per-key metrics + JSON blob

## Usage

- Flash manually (not part of firmware build)
- Type `start` in Serial
- Follow key prompts
- Save JSON output for calibration records

## DO NOT SHIP
These files are not part of production firmware.
