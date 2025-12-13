#include "core_log.h"

namespace Core {

Logger::Logger() = default;

void Logger::begin(PubSubClient* client, const LogOptions& opts) {
  client_ = client;
  topic_ = opts.topic;
  fwVersion_ = opts.fwVersion;
  format_ = opts.format;
  includeData_ = opts.includeDataField;
  filter_ = opts.filter;
  filterUser_ = opts.filterUser;
  errorCount_ = 0;
}

bool Logger::shouldLog(const char* level) const {
  if (!filter_) return true;
  return filter_(level, filterUser_);
}

bool Logger::publish(const char* level, const String& msg) {
  String empty;
  return publish(level, msg, empty);
}

bool Logger::publish(const char* level, const String& msg, const String& dataJson) {
  if (!client_ || !topic_) return false;

  bool allowed = shouldLog(level);
  bool isError = (strcmp(level, "ERR") == 0);
  if (!allowed) {
    if (isError) {
      errorCount_++;
    }
    return false;
  }

  LogMessage lm{level, &msg, &dataJson, static_cast<uint32_t>(millis() / 1000)};
  String payload = buildPayload(lm);
  bool ok = client_->publish(topic_, payload.c_str());
  if (isError) {
    errorCount_++;
  }
  return ok;
}

String Logger::buildPayload(const LogMessage& msg) const {
  String payload = "{";
  switch (format_) {
    case LogFormat::LevelMsg:
      payload += "\"lvl\":\"";
      payload += msg.level;
      payload += "\",\"msg\":\"";
      payload += *(msg.msg);
      payload += "\"";
      break;
    case LogFormat::FwLevelMsg:
      payload += "\"fw\":\"";
      payload += (fwVersion_ ? fwVersion_ : "");
      payload += "\",\"lvl\":\"";
      payload += msg.level;
      payload += "\",\"msg\":\"";
      payload += *(msg.msg);
      payload += "\"";
      break;
    case LogFormat::FwUptimeLevelMsg:
    default:
      payload += "\"fw\":\"";
      payload += (fwVersion_ ? fwVersion_ : "");
      payload += "\",\"up\":";
      payload += msg.uptime;
      payload += ",\"lv\":\"";
      payload += msg.level;
      payload += "\",\"msg\":\"";
      payload += *(msg.msg);
      payload += "\"";
      break;
  }

  if (includeData_ && msg.dataJson && msg.dataJson->length() > 0) {
    payload += ",\"d\":";
    payload += *(msg.dataJson);
  }

  payload += "}";
  return payload;
}

}  // namespace Core
