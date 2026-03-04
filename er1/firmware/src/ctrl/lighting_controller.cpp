#include "ctrl/lighting_controller.h"

#include <Arduino.h>
#include <ArduinoJson.h>
#include <cstring>

namespace {

constexpr const char* kCmdPrefix = "lighting/mosfet/";
constexpr const char* kCmdSuffix = "/cmd";

#ifndef LIGHTING_SERIAL_DEBUG
#define LIGHTING_SERIAL_DEBUG 1
#endif

#if LIGHTING_SERIAL_DEBUG
  #define SDBG(fmt, ...) do { if (Serial) Serial.printf("[lighting] " fmt "\n", ##__VA_ARGS__); } while(0)
#else
  #define SDBG(fmt, ...) do {} while(0)
#endif

String makeStateTopic(const char* id) {
  String t = "lighting/mosfet/";
  t += id;
  t += "/state";
  return t;
}

String upperTrim(String s) {
  s.trim();
  s.toUpperCase();
  return s;
}

bool parseIntLoose(const String& s, int32_t& out) {
  String t = s;
  t.trim();
  if (!t.length()) return false;

  int idx = t.indexOf(' ');
  if (idx < 0) idx = t.indexOf(':');
  if (idx < 0) idx = t.indexOf('=');
  if (idx >= 0) {
    t = t.substring(idx + 1);
    t.trim();
  }

  char* endp = nullptr;
  long v = strtol(t.c_str(), &endp, 10);
  if (endp == t.c_str()) return false;
  out = (int32_t)v;
  return true;
}

} // namespace

LightingController::LightingController() {
  // set ids "1".."9"
  static const char* ids[kChannelCount] = {"1","2","3","4","5","6","7","8","9"};
  for (size_t i = 0; i < kChannelCount; i++) channels_[i].id = ids[i];
}

bool LightingController::mqttConnected() const {
  if (!ctx_) return false;
  auto* c = ctx_->mqttClient();
  return c && c->connected();
}

bool LightingController::parseChannelIdFromTopic(const String& topic, String& outId) {
  if (!topic.startsWith(kCmdPrefix) || !topic.endsWith(kCmdSuffix)) return false;
  int start = strlen(kCmdPrefix);
  int end = topic.length() - strlen(kCmdSuffix);
  if (end <= start) return false;
  outId = topic.substring(start, end);
  return outId.length() > 0;
}

void LightingController::begin(Core::NodeContext& ctx) {
  ctx_ = &ctx;

  // Must NOT collide with ETH pins in your build flags: 15,18,19,23,27
  static const uint8_t kPins[kChannelCount] = {
    16,   // r2-schach
    17,   // r2 schronk
    21,   // r1 bild
    22,   // r1 stuen
    14,   // r3 slider
    26,   // r3 cage
    25,   // torch stiege
    33,   // torch r2-r3
    32,   // torch r2
  };

  constexpr uint32_t kFreqHz = 2000;
  constexpr uint8_t kResBits = 12;

  SDBG("controller.begin(): init pwm freq=%lu res=%u", (unsigned long)kFreqHz, (unsigned)kResBits);

  LightingChannelConfig cfg[kChannelCount];
  for (size_t i = 0; i < kChannelCount; i++) {
    channels_[i].pin = kPins[i];
    channels_[i].ledcCh = (uint8_t)i;
    channels_[i].on = false;
    channels_[i].duty = 0;
    cfg[i] = {channels_[i].id, channels_[i].pin, channels_[i].ledcCh};
    SDBG("ch%u id=%s pin=%u ledc=%u", (unsigned)(i + 1), channels_[i].id, (unsigned)channels_[i].pin, (unsigned)channels_[i].ledcCh);
  }

  driver_.begin(cfg, kChannelCount, kFreqHz, kResBits);
  SDBG("pwm driver ready (maxDuty=%lu)", (unsigned long)driver_.maxDuty());

  // Ensure outputs start OFF (but DON'T publish yet — MQTT not connected at boot)
  for (size_t i = 0; i < kChannelCount; i++) {
    applyOutput(channels_[i]);
  }

  bootStatePublished_ = false;
  lastMqttConnected_ = false;
  lastMetricMs_ = millis();
}

void LightingController::tick(uint32_t /*nowMs*/) {
  const bool conn = mqttConnected();
  if (conn != lastMqttConnected_) {
    SDBG("MQTT connected=%d", (int)conn);
    lastMqttConnected_ = conn;
  }

  // Publish retained initial state once, right after MQTT is connected
  if (conn && !bootStatePublished_) {
    SDBG("publishing retained boot state (all channels)");
    publishAllStates("boot");
    bootStatePublished_ = true;
  }
}

bool LightingController::onCmd(const char* cmd, const char* payload) {
  if (!ctx_) return false;
  String msg(cmd ? cmd : "");
  if (payload && payload[0]) {
    msg += " ";
    msg += payload;
  }
  SDBG("node cmd (ignored): %s", msg.c_str());
  log("WRN", String("Unknown node CMD: ") + msg);
  return true;
}

LightingController::ChannelState* LightingController::findById(const String& id) {
  for (size_t i = 0; i < kChannelCount; i++) {
    if (id.equalsIgnoreCase(channels_[i].id)) return &channels_[i];
  }
  return nullptr;
}

uint32_t LightingController::clampDuty(uint32_t duty) const {
  const uint32_t max = driver_.maxDuty();
  return (duty > max) ? max : duty;
}

uint32_t LightingController::percentToDuty(uint32_t pct) const {
  if (pct > 100) pct = 100;
  const uint32_t max = driver_.maxDuty();
  return (uint32_t)((pct * (uint64_t)max + 50) / 100);
}

uint32_t LightingController::mapUserValueToDuty(int32_t v) const {
  if (v <= 0) return 0;
  if (v <= 100) return percentToDuty((uint32_t)v);
  if (v <= 255) {
    const uint32_t max = driver_.maxDuty();
    return (uint32_t)((v * (uint64_t)max + 127) / 255);
  }
  return clampDuty((uint32_t)v);
}

void LightingController::applyOutput(ChannelState& ch) {
  const uint32_t duty = ch.on ? ch.duty : 0;
  driver_.writeDuty(ch.ledcCh, duty);
  SDBG("apply: id=%s on=%d duty=%lu", ch.id, (int)ch.on, (unsigned long)duty);
}

bool LightingController::publish(const char* topic, const String& payload, bool retained) const {
  if (!ctx_) return false;
  if (!mqttConnected()) return false;  // <- quiet when offline
  return ctx_->publish(topic, payload, retained);
}

void LightingController::publishAllStates(const char* reason) {
  for (size_t i = 0; i < kChannelCount; i++) {
    publishChannelState(channels_[i], reason);
  }
}

void LightingController::log(const char* level, const String& msg) const {
  if (!ctx_) return;
  ctx_->log(level, msg);
}

void LightingController::log(const char* level, const String& msg, const String& dataJson) const {
  if (!ctx_) return;
  ctx_->log(level, msg, dataJson);
}

void LightingController::publishChannelState(const ChannelState& ch, const char* reason) {
  if (!ctx_) return;

  const uint32_t max = driver_.maxDuty();
  const uint32_t pct = (max == 0) ? 0 : (uint32_t)((ch.duty * (uint64_t)100 + (max / 2)) / max);

  String payload = String("{\"fw\":\"") + ctx_->fwVersion() +
                   "\",\"up\":" + String(millis() / 1000) +
                   ",\"id\":\"" + ch.id + "\"" +
                   ",\"on\":" + String(ch.on ? 1 : 0) +
                   ",\"duty\":" + String(ch.duty) +
                   ",\"pct\":" + String(pct) +
                   ",\"max\":" + String(max);

  if (reason && reason[0]) {
    payload += ",\"reason\":\"";
    payload += reason;
    payload += "\"";
  }
  payload += "}";

  String topic = makeStateTopic(ch.id);
  const bool ok = publish(topic.c_str(), payload, true);
  SDBG("state: id=%s on=%d duty=%lu pct=%lu reason=%s pub=%s",
       ch.id, (int)ch.on, (unsigned long)ch.duty, (unsigned long)pct,
       (reason ? reason : ""), ok ? "ok" : "skip/offline");
}

void LightingController::onMosfetCommandTopic(const char* topic, const String& payload) {
  if (!ctx_) return;

  SDBG("RX: topic=%s payload=%s", topic ? topic : "?", payload.c_str());

  String id;
  if (!parseChannelIdFromTopic(String(topic ? topic : ""), id)) {
    SDBG("RX: bad topic");
    log("WRN", String("bad topic: ") + (topic ? topic : ""));
    return;
  }

  ChannelState* ch = findById(id);
  if (!ch) {
    SDBG("RX: unknown id=%s", id.c_str());
    log("WRN", String("unknown channel id: ") + id);
    return;
  }

  bool handled = false;

  // JSON first
  {
    JsonDocument doc;
    DeserializationError err = deserializeJson(doc, payload);
    if (!err) {
      String cmdS;
      if (doc["cmd"].is<const char*>()) cmdS = doc["cmd"].as<const char*>();
      cmdS = upperTrim(cmdS);

      if (cmdS == "ON") { ch->on = true; handled = true; }
      else if (cmdS == "OFF") { ch->on = false; handled = true; }
      else if (cmdS == "DIM" || cmdS == "DIMMED") {
        ch->on = true;
        ch->duty = percentToDuty(ch->dimPercent);
        handled = true;
      } else if (cmdS == "PWM") {
        int32_t v = 0;
        if (doc["value"].is<int>()) v = doc["value"].as<int>();
        else if (doc["pwm"].is<int>()) v = doc["pwm"].as<int>();
        else if (doc["duty"].is<int>()) v = doc["duty"].as<int>();

        String unit;
        if (doc["unit"].is<const char*>()) unit = upperTrim(String(doc["unit"].as<const char*>()));

        ch->on = true;
        if (unit == "PERCENT" || unit == "%") {
          v = max(0, min(100, v));
          ch->duty = percentToDuty((uint32_t)v);
        } else if (unit == "8BIT") {
          const uint32_t maxd = driver_.maxDuty();
          if (v < 0) v = 0;
          if (v > 255) v = 255;
          ch->duty = (uint32_t)((v * (uint64_t)maxd + 127) / 255);
        } else {
          ch->duty = mapUserValueToDuty(v);
        }
        handled = true;
      } else if (doc["pwm"].is<int>() || doc["duty"].is<int>()) {
        int32_t v = doc["pwm"].is<int>() ? doc["pwm"].as<int>() : doc["duty"].as<int>();
        ch->on = (v > 0);
        ch->duty = mapUserValueToDuty(v);
        handled = true;
      }
    }
  }

  // Text fallback
  if (!handled) {
    String up = upperTrim(payload);
    if (up == "ON") { ch->on = true; handled = true; }
    else if (up == "OFF") { ch->on = false; handled = true; }
    else if (up == "DIM" || up == "DIMMED") {
      ch->on = true;
      ch->duty = percentToDuty(ch->dimPercent);
      handled = true;
    } else {
      int32_t v = 0;
      if (parseIntLoose(up, v)) {
        ch->on = (v > 0);
        ch->duty = mapUserValueToDuty(v);
        handled = true;
      }
    }
  }

  if (!handled) {
    errorCount_++;
    SDBG("RX: unhandled id=%s payload=%s", ch->id, payload.c_str());
    log("WRN", String("unhandled payload for ch ") + ch->id + ": " + payload);
    publishChannelState(*ch, "bad_cmd");
    return;
  }

  if (ch->on && ch->duty == 0) {
    ch->duty = driver_.maxDuty();
  }

  SDBG("RX ok: id=%s on=%d duty=%lu", ch->id, (int)ch->on, (unsigned long)ch->duty);

  applyOutput(*ch);
  publishChannelState(*ch, "cmd");
}
