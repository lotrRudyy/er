# Timestamp Standardization Verification

## Status: COMPLETE ✅

All timestamps in the er/er1 repository have been standardized to the canonical format:

```
YYYY.MM.DD HH:MM:SS.mmm
```

## Verification Summary

### Firmware (C++)
- **core_format_ts()**: Formats runtime wall-clock to canonical format
  - Location: `er1/firmware/lib/core/src/core_time.cpp:77-98`
  - Returns true if wall-clock valid, formats into buffer
  - Returns false (empties buffer) if wall-clock not valid
  - Milliseconds always included via `gettimeofday()` on ESP32

- **core_format_build_ts()**: Formats compile-time __DATE__/__TIME__ to canonical format
  - Location: `er1/firmware/lib/core/src/core_time.cpp:100-121`
  - Parses `__DATE__` ("Mmm dd yyyy") and `__TIME__` ("HH:MM:SS")
  - Always outputs .000 milliseconds (compile-time has no ms precision)

- **Usage in core_node.cpp**:
  - Line 119: Calls `core_format_build_ts()` to populate buildIdBuf_
  - Line 423: Uses `core_format_ts()` in envelope publishing with time_valid boolean
  - Line 532: Uses `core_format_ts()` in heartbeat payload with time_valid boolean

- **Usage in core_log.cpp**:
  - Line 115: Gets timestamp via `core_format_ts()` with time_valid flag
  - All log payloads include "ts" and "time_valid" fields

### Scripts/Tooling

#### mqtt_logs.sh
- Location: `er1/pi-runtime/scripts/mqtt_logs.sh:18`
- Format string: `date +"%Y.%m.%d %H:%M:%S.%3N"`
- Outputs canonical format with milliseconds (%3N)

#### ota_verify.py
- Location: `er1/pi-runtime/scripts/ota_verify.py:67`
- Format string: `datetime.now().strftime("%Y.%m.%d %H:%M:%S.%f")[:-3]`
- Outputs canonical format with milliseconds

### JSON Payloads

All MQTT messages include:
- **"ts"**: ISO string timestamp in canonical format (or empty if wall-clock invalid)
- **"time_valid"**: Boolean indicating whether timestamp is from wall-clock
- **"build"**: Formatted via `core_format_build_ts()` with .000 milliseconds

### Documentation

- Created `er1/firmware/lib/core/include/CORE_TIME_USAGE.md` documenting:
  - Canonical format requirement
  - API description for core_format_ts() and core_format_build_ts()
  - Integration guidelines for other components
  - Example usage for C++, Python, and shell scripts

## Acceptance Criteria Met

✅ No ISO8601 formats (YYYY-MM-DDTHH:MM:SS)
✅ No YYYY-MM-DD dash-separated dates
✅ No "Dec 13 2025" style build timestamps
✅ No toISOString() calls
✅ No old strftime patterns
✅ All firmware timestamps use core_format_ts() or core_format_build_ts()
✅ All scripts use equivalent formatting (%3N in bash, %f in Python)
✅ All JSON payloads include time_valid boolean
✅ Milliseconds always present (.mmm)
✅ No heap allocations (stack buffers with snprintf)
✅ No schema changes (existing fields preserved)
✅ No MQTT topic changes (payload-only)

## Files Checked

Production Code:
- ✅ C++: core_time.cpp, core_node.cpp, core_log.cpp (all using canonical format)
- ✅ Python: ota_verify.py (using canonical format)
- ✅ Shell: mqtt_logs.sh (using canonical format)
- ✅ All riddle main files (candles, chess, knocking, images_piano, etc.)

Excluded (No Changes Needed):
- ✅ Build artifacts (.pio/build)
- ✅ Vendor dependencies (.pio/libdeps)
- ✅ VS Code config (.vscode)

## Conclusion

The er/er1 repository timestamp standardization is **COMPLETE**.

All timestamps across firmware, scripts, and tooling conform to the canonical format `YYYY.MM.DD HH:MM:SS.mmm` with proper wall-clock validity tracking and consistent millisecond precision.
