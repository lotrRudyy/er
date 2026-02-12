#pragma once

#include <Arduino.h>
#include <Ethernet.h>
#include <Update.h>

#include "core_log.h"

namespace Core {

class NodeContext;
using OtaStatusPublisher = void (*)(const char* st, const String& dataJson, bool retained);

struct OtaConfig {
  const char* host = nullptr;
  uint16_t port = 80;
  const char* path = nullptr;
  const char* allowedHost = nullptr;
  const char* allowedPathPrefix = "/node_firmware/";
  const char* targetId = nullptr;
  const char* infoLevel = "INF";
  const char* errLevel = "ERR";
  const char* targetFw = "?";
  OtaStatusPublisher statusPublisher = nullptr;
  uint32_t progressIntervalMs = 1000;
  NodeContext* statusCtx = nullptr;  // if null, NodeCore will supply its context
};

struct OtaUpdateCommand {
  char sha256[65]{};
  char version[48]{};
  char target[48]{};
  char urlHost[64]{};
  char urlPath[128]{};
  uint16_t urlPort = 0;
  bool hasUrlHost = false;
  bool hasUrlPath = false;
  bool hasUrlPort = false;
  size_t sizeBytes = 0;
  bool hasVersion = false;
  bool hasTarget = false;
};

// Parse OTA UPDATE payload (JSON or legacy tokens). Returns false on parse failure.
bool parseUpdateCommand(const char* payload, OtaUpdateCommand& out);

class OtaUpdater {
public:
  void begin(const OtaConfig& cfg, Logger* logger);
  bool perform(const char* cmdPayload = nullptr);
  void onMqttConnected();

private:
  OtaConfig cfg_{};
  Logger* logger_ = nullptr;
  String currentVersion_;
  String currentTarget_;
  String currentUrl_;
  String pendingVersion_;
  size_t expectedSize_ = 0;
  NodeContext* statusCtx_ = nullptr;
  bool bootReportPending_ = false;
  bool bootReportOk_ = false;
  uint32_t lastMissingVersionWarnMs_ = 0;

  void publishStatus(const char* st, const String& dataJson, bool retained);
  void publishFail(const char* at, int code, const char* msg, size_t bytes, const char* extraJson = nullptr);
  void publishOk(size_t bytes, const char* sha256Hex = nullptr, bool retained = true);
  void publishStart();
  void publishProgress(int pct);
  String buildBaseJson() const;
};

}  // namespace Core
