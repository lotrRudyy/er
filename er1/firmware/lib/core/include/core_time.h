#pragma once
#include <cstddef>
#include <cstdint>

// Returns epoch seconds if wall-clock time is valid; otherwise returns uptime seconds.
int64_t core_epoch_seconds();

// Formats local wall-clock time as "YYYY.MM.DD HH:MM:SS.mmm" into out.
// Returns true if wall-clock time is valid and formatted; false otherwise (out[0] is set to '\0').
bool core_format_ts(char* out, size_t outLen);

// Formats the compile-time __DATE__ + __TIME__ values as "YYYY.MM.DD HH:MM:SS.mmm" into out.
// Returns true on success; false if parsing fails (out[0] is set to '\0').
bool core_format_build_ts(char* out, size_t outLen);
