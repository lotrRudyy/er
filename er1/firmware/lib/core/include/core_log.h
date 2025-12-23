#pragma once

#include <Arduino.h>
#include <PubSubClient.h>

#include "core_time.h"

namespace Core {

struct TimestampFields {
  int64_t epoch = 0;
  bool timeValid = false;
  char ts[CORE_TS_LEN] = {0};
};

class TimestampSource {
public:
  virtual ~TimestampSource() = default;
  virtual TimestampFields currentTimestamp() = 0;
};

enum class LogFormat : uint8_t {
  LevelMsg = 0,        // Compatibility only; all formats emit {"lv":"..","msg":"..","d":{}}
  FwLevelMsg,
  FwUptimeLevelMsg
};

struct LogMessage {
  const char* level;
  const String* msg;
  const String* dataJson;
};

using LogFilterFn = bool (*)(const char* level, void* userData);

struct LogOptions {
  const char* topic = nullptr;
  const char* fwVersion = nullptr;
  const char* serialTag = nullptr;
  LogFormat format = LogFormat::FwUptimeLevelMsg;
  bool includeDataField = true;
  bool serialDebug = true;
  LogFilterFn filter = nullptr;
  void* filterUser = nullptr;
};

class Logger {
public:
  Logger();

  void begin(PubSubClient* client, const LogOptions& opts);

  bool publish(const char* level, const String& msg);
  bool publish(const char* level, const String& msg, const String& dataJson);

  void setSerialDebug(bool enabled) { serialDebug_ = enabled; }
  void setSerialTag(const char* tag) { serialTag_ = tag; }
  void setTimestampSource(TimestampSource* src) { tsSource_ = src; }
  uint32_t errorCount() const { return errorCount_; }
  uint32_t lastErrorSinceUp() const { return lastErrorSinceUp_; }
  const String& lastErrorMsg() const { return lastErrorMsg_; }
  void resetErrorCount() { errorCount_ = 0; }
  void setTopic(const char* topic) { topic_ = topic; }
  void setFwVersion(const char* fw) { fwVersion_ = fw; }
  void setFilter(LogFilterFn fn, void* user) { filter_ = fn; filterUser_ = user; }
  void setFormat(LogFormat fmt) { format_ = fmt; }
  void setIncludeData(bool enabled) { includeData_ = enabled; }
#ifdef CORE_LOG_SELFTEST
  String buildPayloadForTest(const LogMessage& msg, const TimestampFields& ts) const { return buildPayload(msg, ts); }
#endif

private:
  bool shouldLog(const char* level) const;
  TimestampFields timestamp();
  String buildPayload(const LogMessage& msg, const TimestampFields& ts) const;
  bool emitMissingTsWarning(const TimestampFields& ts);
  void serialPrint(const LogMessage& msg, const TimestampFields& ts) const;

  PubSubClient* client_ = nullptr;
  const char* topic_ = nullptr;
  const char* fwVersion_ = nullptr;
  const char* serialTag_ = nullptr;
  LogFormat format_ = LogFormat::FwUptimeLevelMsg;
  bool serialDebug_ = true;
  bool includeData_ = true;
  LogFilterFn filter_ = nullptr;
  void* filterUser_ = nullptr;
  uint32_t errorCount_ = 0;
  uint32_t lastErrorSinceUp_ = 0;
  String lastErrorMsg_;
  TimestampSource* tsSource_ = nullptr;
  bool warnedMissingTs_ = false;
  bool warningActive_ = false;
};

}  // namespace Core
