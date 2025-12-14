#pragma once
#include <cstddef>
#include <cstdint>

// Minimum valid epoch (2023-01-01 00:00:00 UTC)
constexpr int64_t kMinValidEpoch = 1672531200;

// Returns epoch seconds if wall-clock time is valid; otherwise returns uptime seconds.
int64_t core_epoch_seconds();

// Formats local wall-clock time as "YYYY.MM.DD HH:MM:SS.mmm" into out.
// Returns true if wall-clock time is valid and formatted; false otherwise (out[0] is set to '\0').
bool core_format_ts(char* out, size_t outLen);

// Formats the compile-time __DATE__ + __TIME__ values as "YYYY.MM.DD HH:MM:SS.mmm" into out.
// Returns true on success; false if parsing fails (out[0] is set to '\0').
bool core_format_build_ts(char* out, size_t outLen);

// Sets system wall-clock time to the given epoch seconds (POSIX timestamp).
// Returns true if successful, false otherwise.
bool core_set_time(int64_t epochSeconds);

// Returns true if the wall-clock time is currently valid.
bool core_time_valid();
