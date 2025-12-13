#include "core_log.h"

#include <cstdio>

namespace Core {

namespace {
String escapeJsonString(const String& in) {
  String out;
  out.reserve(in.length() + 8);
  for (size_t i = 0; i < in.length(); i++) {
    const char c = in.charAt(i);
    switch (c) {
      case '\\': out += "\\\\"; break;
      case '"': out += "\\\""; break;
      case '\b': out += "\\b"; break;
      case '\f': out += "\\f"; break;
      case '\n': out += "\\n"; break;
      case '\r': out += "\\r"; break;
      case '\t': out += "\\t"; break;
      default:
        if (static_cast<uint8_t>(c) < 0x20) {
          char buf[7];
          std::snprintf(buf, sizeof(buf), "\\u%04x", static_cast<uint8_t>(c));
          out += buf;
        } else {
          out += c;
        }
        break;
    }
  }
  return out;
}
}  // namespace

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
  (void)msg.uptime;
  String payload = "{";
  const String level = escapeJsonString(msg.level ? msg.level : "");
  const String message = msg.msg ? escapeJsonString(*(msg.msg)) : "";
  payload += "\"lv\":\"";
  payload += level;
  payload += "\",\"msg\":\"";
  payload += message;
  payload += "\"";

  if (includeData_ && msg.dataJson && msg.dataJson->length() > 0) {
    payload += ",\"d\":";
    payload += *(msg.dataJson);
  }

  payload += "}";
  return payload;
}

}  // namespace Core
