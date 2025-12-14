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
  const char* allowedHost = nullptr;
  const char* allowedPathPrefix = "/firmware/";
  const char* targetId = nullptr;
  const char* infoLevel = "INF";
  const char* errLevel = "ERR";
  const char* targetFw = "?";
  OtaStatusPublisher statusPublisher = nullptr;
  uint32_t progressIntervalMs = 1000;
};

class OtaUpdater {
public:
  void begin(const OtaConfig& cfg, Logger* logger);
  bool perform(const char* cmdPayload = nullptr);
  void onMqttConnected();

private:
  OtaConfig cfg_{};
  Logger* logger_ = nullptr;
  String currentId_;
  String currentVersion_;
  String currentTarget_;
  String currentUrl_;
  String pendingVersion_;
  size_t expectedSize_ = 0;
  bool bootReportPending_ = false;
  bool bootReportOk_ = false;

  void publishStatus(const char* st, const String& dataJson, bool retained);
  void publishFail(const char* at, int code, const char* msg, size_t bytes, const char* extraJson = nullptr);
  void publishOk(size_t bytes, const char* sha256Hex = nullptr, bool retained = true);
  void publishStart();
  void publishProgress(int pct);
  String buildBaseJson() const;
};

}  // namespace Core
