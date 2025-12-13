#pragma once

#include <Arduino.h>
#include <Ethernet.h>
#include <Update.h>

#include "core_log.h"

namespace Core {

using OtaStatusPublisher = void (*)(const char* st, const String& dataJson, bool retained);

struct OtaConfig {
  const char* host = nullptr;
  uint16_t port = 80;
  const char* path = nullptr;
  const char* infoLevel = "INF";
  const char* errLevel = "ERR";
  const char* targetFw = "?";
  OtaStatusPublisher statusPublisher = nullptr;
  uint32_t progressIntervalMs = 1000;
};

class OtaUpdater {
public:
  void begin(const OtaConfig& cfg, Logger* logger);
  bool perform();

private:
  OtaConfig cfg_{};
  Logger* logger_ = nullptr;

  void publishStatus(const char* st, const String& dataJson, bool retained);
  void publishFail(const char* at, int code, const char* msg, size_t bytes);
  void publishOk(size_t bytes);
  void publishStart();
  void publishProgress(int pct);
};

}  // namespace Core
