#include "lighting_controller.h"

#include <ArduinoJson.h>

namespace {

constexpr const char* kCmdPrefix = "lighting/mosfet/";
constexpr const char* kCmdSuffix = "/cmd";

String makeStateTopic(const char* id) {
  String t = "lighting/mosfet/";
  t += id;
  t += "/state";
  return t;
}

static bool isNumberString(const String& s) {
  if (!s.length()) return false;
  for (size_t i = 0; i < s.length(); i++) {
    const char c = s[i];
    if (c == '-' && i == 0) continue;
    if (c < '0' || c > '9') return false;
  }
  return true;
}

}  // namespace

void LightingController::begin(Core::NodeContext& ctx) {
  ctx_ = &ctx;
  topicDbg_ = ctx.config().topics.dbg;

  MosfetPwmChannelConfig cfg[kChannelCount];
  for (size_t i = 0; i < kChannelCount; i++) {
    cfg[i] = {channels_[i].id, channels_[i].pin, channels_[i].ledcCh};
    channels_[i].duty = 0;
    channels_[i].lastOnDuty = percentToDuty(100);
  }

  driver_.begin(cfg, kChannelCount, kFreqHz, kResolutionBits);
  for (auto& ch : channels_) {
    applyOutput(ch);
  }

  lastMetricMs_ = millis();
  publishStateSnapshot("boot");
}

void LightingController::tick(uint32_t nowMs) {
  if (!ctx_) return;
  publishMetricsIfDue(nowMs);
}

void LightingController::onMosfetCommandTopic(const char* topic, const String& payload) {
  if (!ctx_) return;
  String id;
  if (!parseChannelIdFromTopic(String(topic ? topic : ""), id)) {
    log("WRN", "lighting cmd on unexpected topic", String("{\"topic\":\"") + String(topic ? topic : "") + "\"}");
    return;
  }

  ChannelState* ch = findChannelById(id);
  if (!ch) {
    log("WRN", "lighting cmd unknown channel", String("{\"id\":\"") + id + "\"}");
    return;
  }
  parseAndApplyCommand(*ch, payload, topic);
}

bool LightingController::shouldAllowLog(const char* level) {
  bool isErr = (strcmp(level, "ERR") == 0);
  if (isErr) errorCount_++;
  return true;
}

void LightingController::applyOutput(ChannelState& ch) {
  driver_.writeDuty(ch.ledcCh, ch.duty);
}

uint32_t LightingController::clampDuty(uint32_t duty) const {
  const uint32_t max = driver_.maxDuty();
  return (duty > max) ? max : duty;
}

uint32_t LightingController::percentToDuty(uint32_t pct) const {
  if (pct > 100) pct = 100;
  const uint32_t max = driver_.maxDuty();
  return (max * pct) / 100;
}

uint32_t LightingController::mapUserValueToDuty(int32_t v) const {
  if (v <= 0) return 0;
  if (v <= 100) return percentToDuty((uint32_t)v);
  if (v <= 255) {
    // 8-bit -> scale to resolution
    const uint32_t max = driver_.maxDuty();
    return (max * (uint32_t)v) / 255;
  }
  return clampDuty((uint32_t)v);
}

void LightingController::setDuty(ChannelState& ch, uint32_t duty, const char* reason) {
  duty = clampDuty(duty);
  ch.duty = duty;
  if (duty > 0) ch.lastOnDuty = duty;
  applyOutput(ch);
  publishChannelState(ch, reason);
}

void LightingController::setOn(ChannelState& ch, const char* reason) {
  uint32_t duty = ch.lastOnDuty ? ch.lastOnDuty : percentToDuty(100);
  setDuty(ch, duty, reason);
}

void LightingController::setOff(ChannelState& ch, const char* reason) {
  setDuty(ch, 0, reason);
}

void LightingController::setDimmed(ChannelState& ch, const char* reason) {
  if (!ch.dimmable) {
    setOn(ch, "not_dimmable_on");
    return;
  }
  setDuty(ch, percentToDuty(25), reason);
}

void LightingController::publishChannelState(const ChannelState& ch, const char* reason) {
  if (!ctx_) return;

  const uint32_t max = driver_.maxDuty();
  const uint32_t pct = (max > 0) ? (ch.duty * 100) / max : 0;

  String payload = String("{\"fw\":\"") + ctx_->fwVersion() +
                   "\",\"up\":" + String(millis() / 1000) +
                   ",\"id\":\"" + ch.id +
                   "\",\"pin\":" + String(ch.pin) +
                   ",\"dimmable\":" + (ch.dimmable ? "1" : "0") +
                   ",\"on\":" + (ch.duty > 0 ? "1" : "0") +
                   ",\"pwm\":" + String(ch.duty) +
                   ",\"pct\":" + String(pct);
  if (reason && reason[0]) {
    payload += ",\"reason\":\"";
    payload += reason;
    payload += "\"";
  }
  payload += "}";

  String topic = makeStateTopic(ch.id);
  publish(topic.c_str(), payload, true);
  publishStateSnapshot(reason);
}

void LightingController::publishStateSnapshot(const char* reason) {
  if (!ctx_) return;
  const uint32_t max = driver_.maxDuty();

  String payload = String("{\"fw\":\"") + ctx_->fwVersion() +
                   "\",\"up\":" + String(millis() / 1000) +
                   ",\"t\":\"LIGHT\"";
  if (reason && reason[0]) {
    payload += String(",\"reason\":\"") + reason + "\"";
  }
  payload += ",\"ch\":[";
  for (size_t i = 0; i < kChannelCount; i++) {
    if (i) payload += ",";
    const auto& ch = channels_[i];
    const uint32_t pct = (max > 0) ? (ch.duty * 100) / max : 0;
    payload += String("{\"id\":\"") + ch.id +
               "\",\"on\":" + (ch.duty > 0 ? "1" : "0") +
               ",\"pwm\":" + String(ch.duty) +
               ",\"pct\":" + String(pct) + "}";
  }
  payload += "]}";

  publish("lighting/state", payload, true);
}

void LightingController::publishMetricsIfDue(uint32_t nowMs) {
  if (!ctx_) return;
  if (nowMs - lastMetricMs_ < kMetricIntervalMs) return;
  lastMetricMs_ = nowMs;
  publishStateSnapshot("metric");
}

LightingController::ChannelState* LightingController::findChannelById(const String& id) {
  for (auto& ch : channels_) {
    if (id.equalsIgnoreCase(ch.id)) return &ch;
  }
  return nullptr;
}

bool LightingController::parseChannelIdFromTopic(const String& topic, String& outId) {
  if (!topic.startsWith(kCmdPrefix) || !topic.endsWith(kCmdSuffix)) return false;
  const int start = strlen(kCmdPrefix);
  const int end = topic.length() - strlen(kCmdSuffix);
  if (end <= start) return false;
  outId = topic.substring(start, end);
  return true;
}

bool LightingController::parseAndApplyCommand(ChannelState& ch, const String& payload, const char* /*topic*/) {
  String p = payload;
  p.trim();
  if (!p.length()) return false;

  // Try JSON first.
  if (p[0] == '{') {
    StaticJsonDocument<256> doc;
    DeserializationError err = deserializeJson(doc, p);
    if (err) {
      log("WRN", "lighting json parse error", String("{\"err\":\"") + err.c_str() + "\"}");
    } else {
      const char* cmd = doc["cmd"] | doc["command"] | "";
      String cmdS(cmd);
      cmdS.trim();
      cmdS.toUpperCase();

      if (cmdS == "ON") {
        setOn(ch, "json_on");
        return true;
      }
      if (cmdS == "OFF") {
        setOff(ch, "json_off");
        return true;
      }
      if (cmdS == "DIM" || cmdS == "DIMMED") {
        setDimmed(ch, "json_dim");
        return true;
      }

      // PWM / duty
      if (doc.containsKey("pwm") || doc.containsKey("duty") || cmdS == "PWM") {
        int32_t v = doc["value"] | doc["pwm"] | doc["duty"] | 0;
        const char* unit = doc["unit"] | "";
        String unitS(unit);
        unitS.toLowerCase();
        uint32_t duty = 0;
        if (unitS == "percent" || unitS == "%") {
          duty = percentToDuty((uint32_t)max(0, min(100, (int)v)));
        } else if (unitS == "8bit" || unitS == "byte") {
          duty = mapUserValueToDuty(max(0, min(255, (int)v)));
        } else {
          duty = mapUserValueToDuty(v);
        }
        setDuty(ch, duty, "json_pwm");
        return true;
      }
    }
    // fall through to text parsing if JSON is malformed/unknown
  }

  String up = p;
  up.toUpperCase();

  if (up == "ON") {
    setOn(ch, "on");
    return true;
  }
  if (up == "OFF") {
    setOff(ch, "off");
    return true;
  }
  if (up == "DIM" || up == "DIMMED") {
    setDimmed(ch, "dim");
    return true;
  }

  // PWM forms: "PWM 123" / "PWM:123" / "PWM=123"
  if (up.startsWith("PWM")) {
    String rest = p.substring(3);
    rest.trim();
    if (rest.startsWith(":")) rest = rest.substring(1);
    if (rest.startsWith("=")) rest = rest.substring(1);
    rest.trim();
    if (isNumberString(rest)) {
      int32_t v = rest.toInt();
      setDuty(ch, mapUserValueToDuty(v), "pwm");
      return true;
    }
    log("WRN", "lighting PWM command missing/invalid value", String("{\"payload\":\"") + p + "\"}");
    return false;
  }

  // Number alone
  if (isNumberString(p)) {
    int32_t v = p.toInt();
    setDuty(ch, mapUserValueToDuty(v), "value");
    return true;
  }

  log("WRN", "lighting unknown command", String("{\"payload\":\"") + p + "\"}");
  return false;
}

void LightingController::log(const char* level, const String& msg) const {
  if (!ctx_) return;
  ctx_->log(level, msg);
}

void LightingController::log(const char* level, const String& msg, const String& dataJson) const {
  if (!ctx_) return;
  ctx_->log(level, msg, dataJson);
}

bool LightingController::publish(const char* topic, const String& payload, bool retained) const {
  if (!ctx_) return false;
  return ctx_->publish(topic, payload, retained);
}
