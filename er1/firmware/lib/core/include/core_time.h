#pragma once
#include <cstddef>
#include <cstdint>

// Returns epoch seconds if wall-clock time is valid; otherwise returns uptime seconds.
int64_t core_epoch_seconds();

// Formats local wall-clock time as "YYYY-MM-DD HH:MM:SS.mmm" into out.
// Returns true if wall-clock time is valid and formatted; false otherwise.
bool core_format_ts(char* out, size_t outLen);
