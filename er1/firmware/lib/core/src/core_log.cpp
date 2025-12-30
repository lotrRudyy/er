#include "core_log.h"

#include <ArduinoJson.h>
#include <cctype>
#include <cstdio>

#include "core_time.h"

namespace Core {

namespace {
bool isValidJsonObject(const String& in) {
  if (in.length() == 0) return false;
  size_t start = 0;
  size_t end = in.length();
  while (start < end && std::isspace(static_cast<unsigned char>(in.charAt(start)))) start++;
  while (end > start && std::isspace(static_cast<unsigned char>(in.charAt(end - 1)))) end--;
  if (end <= start) return false;
  if (in.charAt(start) != '{' || in.charAt(end - 1) != '}') return false;

  JsonDocument doc;
  DeserializationError err = deserializeJson(doc, in);
  if (err) return false;
  return doc.is<JsonObject>();
}

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
  serialTag_ = opts.serialTag;
  serialDebug_ = opts.serialDebug;
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

TimestampFields Logger::timestamp() {
  if (tsSource_) {
    return tsSource_->currentTimestamp();
  }
  TimestampFields ts{};
  ts.epoch = core_epoch_seconds();
  ts.timeValid = core_format_ts(ts.ts, sizeof(ts.ts));
  return ts;
}

bool Logger::publish(const char* level, const String& msg) {
  String empty;
  return publish(level, msg, empty);
}

bool Logger::publish(const char* level, const String& msg, const String& dataJson) {
  if (!client_ || !topic_) return false;

  bool allowed = shouldLog(level);
  bool isError = (strcmp(level, "ERR") == 0);
  if (!allowed && !isError) return false;

  LogMessage lm{level, &msg, &dataJson};
  TimestampFields ts = timestamp();
  String payload = buildPayload(lm, ts);
  bool ok = true;
  if (allowed) {
    ok = client_->publish(topic_, payload.c_str());
  }

  if (!ts.timeValid) {
    emitMissingTsWarning(ts);
  }

  if (isError) {
    errorCount_++;
    lastErrorSinceUp_ = millis() / 1000;
    lastErrorMsg_ = msg;
  }
  return ok;
}

bool Logger::emitMissingTsWarning(const TimestampFields& ts) {
  if (warnedMissingTs_ || warningActive_ || ts.timeValid) return false;
  if (!client_ || !topic_) return false;

  warnedMissingTs_ = true;
  warningActive_ = true;
  String msg = "wall-clock time not available; time_valid=false";
  LogMessage lm{"WRN", &msg, nullptr};
  String payload = buildPayload(lm, ts);
  bool ok = client_->publish(topic_, payload.c_str());
  warningActive_ = false;
  return ok;
}

String Logger::buildPayload(const LogMessage& msg, const TimestampFields& ts) const {
  String payload = "{";
  payload += "\"t\":";
  payload += static_cast<long long>(ts.epoch);
  payload += ",\"ts\":\"";
  payload += ts.ts;
  payload += "\",\"time_valid\":";
  payload += ts.timeValid ? "true" : "false";

  const String level = escapeJsonString(msg.level ? msg.level : "");
  const String message = msg.msg ? escapeJsonString(*(msg.msg)) : "";
  payload += ",\"lv\":\"";
  payload += level;
  payload += "\",\"msg\":\"";
  payload += message;
  payload += "\"";

  if (includeData_ && msg.dataJson && msg.dataJson->length() > 0) {
    const String& data = *(msg.dataJson);
    const bool dataIsObject = isValidJsonObject(data);
    payload += ",\"d_type\":\"";
    payload += dataIsObject ? "object" : "string";
    payload += "\"";
    if (dataIsObject) {
      payload += ",\"d\":";
      payload += data;
    } else {
      const String dataEscaped = escapeJsonString(data);
      payload += ",\"d\":\"";
      payload += dataEscaped;
      payload += "\"";
    }
  }

  payload += "}";
  return payload;
}

}  // namespace Core
