#pragma once

#include <Arduino.h>
#include <PubSubClient.h>

namespace Core {

enum class LogFormat : uint8_t {
  LevelMsg = 0,        // Compatibility only; all formats emit {"lv":"..","msg":"..","d":{}}
  FwLevelMsg,
  FwUptimeLevelMsg
};

struct LogMessage {
  const char* level;
  const String* msg;
  const String* dataJson;
  uint32_t uptime;
};

using LogFilterFn = bool (*)(const char* level, void* userData);

struct LogOptions {
  const char* topic = nullptr;
  const char* fwVersion = nullptr;
  LogFormat format = LogFormat::FwUptimeLevelMsg;
  bool includeDataField = true;
  LogFilterFn filter = nullptr;
  void* filterUser = nullptr;
};

class Logger {
public:
  Logger();

  void begin(PubSubClient* client, const LogOptions& opts);

  bool publish(const char* level, const String& msg);
  bool publish(const char* level, const String& msg, const String& dataJson);

  uint32_t errorCount() const { return errorCount_; }
  void resetErrorCount() { errorCount_ = 0; }
  void setTopic(const char* topic) { topic_ = topic; }
  void setFwVersion(const char* fw) { fwVersion_ = fw; }
  void setFilter(LogFilterFn fn, void* user) { filter_ = fn; filterUser_ = user; }
  void setFormat(LogFormat fmt) { format_ = fmt; }
  void setIncludeData(bool enabled) { includeData_ = enabled; }

private:
  bool shouldLog(const char* level) const;
  String buildPayload(const LogMessage& msg) const;

  PubSubClient* client_ = nullptr;
  const char* topic_ = nullptr;
  const char* fwVersion_ = nullptr;
  LogFormat format_ = LogFormat::FwUptimeLevelMsg;
  bool includeData_ = true;
  LogFilterFn filter_ = nullptr;
  void* filterUser_ = nullptr;
  uint32_t errorCount_ = 0;
};

}  // namespace Core
