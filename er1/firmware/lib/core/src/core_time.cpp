// core_time.cpp
#include "core_time.h"

#include <Arduino.h>
#include <cstdio>
#include <cstring>
#include <time.h>

#if defined(ESP32)
#include <sys/time.h>
#include <stdlib.h>  // setenv
#endif

namespace {
// "HH:MM:SS.mmm - YYYY.MM.DD" (25 chars) + null
constexpr size_t kTimestampMinLen = CORE_TS_LEN;

bool wallClockValid(time_t t) {
  // Anything before ~2023 is "not set" in practice.
  return t >= 1672531200;  // 2023-01-01 00:00:00 UTC
}

bool formatTimestamp(const struct tm& tmLocal, int ms, char* out, size_t outLen) {
  if (!out || outLen < kTimestampMinLen) return false;
  out[0] = '\0';

  char timePart[9];   // "HH:MM:SS"
  char datePart[11];  // "YYYY.MM.DD"

  if (strftime(timePart, sizeof(timePart), "%H:%M:%S", &tmLocal) == 0) return false;
  if (strftime(datePart, sizeof(datePart), "%Y.%m.%d", &tmLocal) == 0) return false;

  std::snprintf(out, outLen, "%s.%03d - %s", timePart, ms, datePart);
  return true;
}

int monthFromAbbrev(const char* mmm) {
  if (!mmm) return -1;
  static const char* months[] = {"Jan", "Feb", "Mar", "Apr", "May", "Jun",
                                 "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"};
  for (int i = 0; i < 12; i++) {
    if (std::strncmp(mmm, months[i], 3) == 0) return i + 1;
  }
  return -1;
}

bool parseCompileDate(const char* dateStr, int& year, int& month, int& day) {
  if (!dateStr) return false;
  char mmm[4] = {0};
  int d = 0;
  int y = 0;
  if (std::sscanf(dateStr, "%3s %d %d", mmm, &d, &y) != 3) return false;
  month = monthFromAbbrev(mmm);
  if (month < 1 || month > 12) return false;
  if (d < 1 || d > 31) return false;
  year = y;
  day = d;
  return true;
}

bool parseCompileTime(const char* timeStr, int& hour, int& minute, int& second) {
  if (!timeStr) return false;
  int h = 0, m = 0, s = 0;
  if (std::sscanf(timeStr, "%d:%d:%d", &h, &m, &s) != 3) return false;
  if (h < 0 || h > 23 || m < 0 || m > 59 || s < 0 || s > 60) return false;
  hour = h;
  minute = m;
  second = s;
  return true;
}
}  // namespace

void core_time_init_tz() {
#if defined(ESP32)
  // Europe/Rome (CET/CEST) POSIX TZ string:
  // CET is UTC+1, CEST is UTC+2
  // DST starts last Sunday of March at 02:00, ends last Sunday of October at 03:00
  setenv("TZ", "CET-1CEST,M3.5.0/2,M10.5.0/3", 1);
  tzset();
#endif
}

int64_t core_epoch_seconds() {
  time_t now = ::time(nullptr);
  if (wallClockValid(now)) return static_cast<int64_t>(now);
  return static_cast<int64_t>(millis() / 1000);
}

bool core_format_ts(char* out, size_t outLen) {
  if (!out || outLen < kTimestampMinLen) return false;
  out[0] = '\0';

  time_t now = ::time(nullptr);
  if (!wallClockValid(now)) return false;

  // Ensure localtime_r uses the intended timezone (not default UTC).
  core_time_init_tz();

  struct tm tmLocal;
#if defined(ESP32)
  if (!localtime_r(&now, &tmLocal)) return false;
#else
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

  return formatTimestamp(tmLocal, ms, out, outLen);
}

bool core_format_build_ts(char* out, size_t outLen) {
  if (!out || outLen < kTimestampMinLen) return false;
  out[0] = '\0';

  // Ensure compile-time "local" formatting matches Rome too.
  core_time_init_tz();

  int year = 0, month = 0, day = 0;
  int hour = 0, minute = 0, second = 0;
  if (!parseCompileDate(__DATE__, year, month, day)) return false;
  if (!parseCompileTime(__TIME__, hour, minute, second)) return false;

  struct tm tmBuild {};
  tmBuild.tm_year = year - 1900;
  tmBuild.tm_mon  = month - 1;
  tmBuild.tm_mday = day;
  tmBuild.tm_hour = hour;
  tmBuild.tm_min  = minute;
  tmBuild.tm_sec  = second;

  return formatTimestamp(tmBuild, 0, out, outLen);
}

bool core_set_time(int64_t epochSeconds) {
  if (epochSeconds < kMinValidEpoch) {
    return false;
  }

#if defined(ESP32)
  struct timeval tv;
  tv.tv_sec = static_cast<time_t>(epochSeconds);
  tv.tv_usec = 0;

  if (settimeofday(&tv, nullptr) != 0) return false;

  // Make sure subsequent localtime_r() calls produce Europe/Rome time.
  core_time_init_tz();
  return true;
#else
  // For non-ESP32, this may not be available or may require different handling
  (void)epochSeconds;
  return false;
#endif
}

bool core_time_valid() {
  time_t now = ::time(nullptr);
  return wallClockValid(now);
}
