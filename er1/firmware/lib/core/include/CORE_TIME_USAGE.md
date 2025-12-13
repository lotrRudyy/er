Canonical core time formatter
===========================

This repository uses a single canonical time formatter implemented in `core_time`.

Format
------

All timestamps MUST be emitted as:

YYYY.MM.DD HH:MM:SS.mmm

Example: `2025.12.13 22:39:01.353`

API
---

- `bool core_format_ts(char* out, size_t outLen)`
  - Formats the runtime wall-clock into `out` using the canonical format.
  - Returns `true` when wall-clock time is valid and `out` contains the timestamp.
  - Returns `false` when wall-clock is not valid; `out[0]` is set to '\0' in that case.

- `bool core_format_build_ts(char* out, size_t outLen)`
  - Parses `__DATE__`/`__TIME__` and emits the canonical format into `out`.
  - Milliseconds are always present (zero-padded) — if unavailable, `.000` is used.

Notes for integrations
----------------------

- Keep existing JSON fields — do not remove fields like `build` or `ts`.
- Add or preserve a boolean `time_valid` alongside runtime timestamps when wall-clock validity is relevant.
- For scripts/tools (Python/Go/Node/shell) use an equivalent formatting string: `%%Y.%%m.%%d %%H:%%M:%%S.%%3N` or Python `datetime.now().strftime("%Y.%m.%d %H:%M:%S.%f")[:-3]`.

Examples
--------

C++ (firmware):

char buf[32];
if (core_format_ts(buf, sizeof(buf))) {
  // buf contains "YYYY.MM.DD HH:MM:SS.mmm"
} else {
  // buf[0] == '\0' and time_valid should be false
}

Python (scripts):

from datetime import datetime
ts = datetime.now().strftime("%Y.%m.%d %H:%M:%S.%f")[:-3]
