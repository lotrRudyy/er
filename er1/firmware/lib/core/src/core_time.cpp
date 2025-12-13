#include "core_time.h"

#include <Arduino.h>
#include <time.h>

#if defined(ESP32)
#include <sys/time.h>
#endif

namespace {
bool wallClockValid(time_t t) {
  // Anything before ~2023 is “not set” in practice.
  return t >= 1672531200; // 2023-01-01 00:00:00 UTC
}
}

int64_t core_epoch_seconds() {
  time_t now = ::time(nullptr);
  if (wallClockValid(now)) return static_cast<int64_t>(now);
  return static_cast<int64_t>(millis() / 1000);
}

bool core_format_ts(char* out, size_t outLen) {
  if (!out || outLen < 24) return false; // needs 23 chars + null
  time_t now = ::time(nullptr);
  if (!wallClockValid(now)) return false;

  struct tm tmLocal;
#if defined(ESP32)
  if (!localtime_r(&now, &tmLocal)) return false;
#else
  // Fallback; on non-ESP32 Arduino this may be weak, but compiles.
  struct tm* tmp = localtime(&now);
  if (!tmp) return false;
  tmLocal = *tmp;
#endif

  int ms = 0;
#if defined(ESP32)
  struct timeval tv;
  if (gettimeofday(&tv, nullptr) == 0) {
    ms = static_cast<int>(tv.tv_usec / 1000);
  }
#endif

  // YYYY-MM-DD HH:MM:SS
  char base[20];
  if (strftime(base, sizeof(base), "%Y-%m-%d %H:%M:%S", &tmLocal) == 0) return false;

  // Append .mmm
  // Total: 19 + 1 + 3 = 23 chars
  snprintf(out, outLen, "%s.%03d", base, ms);
  return true;
}
