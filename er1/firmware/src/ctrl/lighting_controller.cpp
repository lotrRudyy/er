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

}  // namespace

LightingController::LightingController() {
  static const char* ids[kChannelCount] = {"1", "2", "3", "4", "5", "6", "7", "8", "9", "10"};
  static const char* names[kChannelCount] = {
    "r2_chess",
    "r2_schronk",
    "r1_bild",
    "r1_stuen",
    "r3_slider",
    "r3_cage",
    "torch_stiege",
    "torch_r2r3",
    "torch_r2",
    "r3_uv"
  };
  for (size_t i = 0; i < kChannelCount; i++) {
    channels_[i].id = ids[i];
    channels_[i].name = names[i];
  }
}

bool LightingController::mqttConnected() const {
  if (!ctx_) return false;
  auto* c = ctx_->mqttClient();
  return c && c->connected();
}

String LightingController::normalizeKey(const String& in) const {
  String s = in;
  s.trim();
  s.toLowerCase();
  s.replace("-", "_");
  s.replace(" ", "_");
  return s;
}

bool LightingController::parseChannelIdFromTopic(const String& topic, String& outId) {
  if (!topic.startsWith(kCmdPrefix) || !topic.endsWith(kCmdSuffix)) return false;
  int start = strlen(kCmdPrefix);
  int end = topic.length() - strlen(kCmdSuffix);
  if (end <= start) return false;
  outId = topic.substring(start, end);
  return outId.length() > 0;
}

LightingController::ChannelState* LightingController::findById(const String& id) {
  for (size_t i = 0; i < kChannelCount; i++) {
    if (id.equalsIgnoreCase(channels_[i].id)) return &channels_[i];
  }
  return nullptr;
}

LightingController::ChannelState* LightingController::findByName(const String& name) {
  const String n = normalizeKey(name);
  for (size_t i = 0; i < kChannelCount; i++) {
    if (n == normalizeKey(channels_[i].name)) return &channels_[i];
  }
  if (n == "r2_schach") return findById("1");
  if (n == "torch_r2_r3") return findById("8");
  return nullptr;
}

LightingController::ChannelState* LightingController::findByAnyKey(const String& key) {
  ChannelState* ch = findById(key);
  if (ch) return ch;
  return findByName(key);
}

LightingController::ParsedGameState LightingController::parseGameState(const String& payload) const {
  ParsedGameState gs;
  String t = payload;
  t.trim();
  if (!t.length()) return gs;

  JsonDocument doc;
  if (deserializeJson(doc, t) == DeserializationError::Ok) {
    if (doc["mode"].is<const char*>()) {
      gs.mode = upperTrim(String(doc["mode"].as<const char*>()));
      gs.valid = gs.mode.length() > 0;
      return gs;
    }
  }

  gs.mode = upperTrim(t);
  gs.valid = gs.mode.length() > 0;
  return gs;
}

void LightingController::begin(Core::NodeContext& ctx) {
  ctx_ = &ctx;

  static const uint8_t kPins[kChannelCount] = {
    16,   // r2 schach
    17,   // r2 schronk
    21,   // r1 bild
    22,   // r1 stuen
    14,   // r3 slider
    26,   // r3 cage
    25,   // torch stiege
    32,   // torch r2-r3
    33,   // torch r2
    4,    // r3 uv
  };

  constexpr uint32_t kFreqHz = 2000;
  constexpr uint8_t kResBits = 12;

  LightingChannelConfig cfg[kChannelCount];
  for (size_t i = 0; i < kChannelCount; i++) {
    channels_[i].pin = kPins[i];
    channels_[i].ledcCh = (uint8_t)i;
    channels_[i].on = false;
    channels_[i].duty = 0;
    cfg[i] = {channels_[i].id, channels_[i].pin, channels_[i].ledcCh};
  }

  driver_.begin(cfg, kChannelCount, kFreqHz, kResBits);
  for (size_t i = 0; i < kChannelCount; i++) applyOutput(channels_[i]);

  bootStatePublished_ = false;
  lastMqttConnected_ = false;
  appliedMode_ = "MODE_STANDBY";
  resetFade(fade1_);
  resetFade(fade2_);
}

void LightingController::tick(uint32_t nowMs) {
  const bool conn = mqttConnected();
  if (conn != lastMqttConnected_) {
    SDBG("MQTT connected=%d", (int)conn);
    lastMqttConnected_ = conn;
  }
  if (conn && !bootStatePublished_) {
    publishAllStates("boot");
    bootStatePublished_ = true;
  }
  updateFadePair(fade1_, nowMs);
  updateFadePair(fade2_, nowMs);
}

bool LightingController::onCmd(const char* cmd, const char* payload) {
  if (!ctx_) return false;
  String msg(cmd ? cmd : "");
  if (payload && payload[0]) {
    msg += " ";
    msg += payload;
  }
  log("WRN", String("Unknown node CMD: ") + msg);
  return true;
}

void LightingController::cancelScheduledEffects() {
  resetFade(fade1_);
  resetFade(fade2_);
}

void LightingController::onGameModeMessage(const String& msg) {
  ParsedGameState gs = parseGameState(msg);
  if (!gs.valid) {
    log("WRN", String("Bad game/state payload: ") + msg);
    return;
  }

  cancelScheduledEffects();
  appliedMode_ = gs.mode;

  if (gs.mode == "MODE_INGAME" || gs.mode == "INGAME") {
    applySceneInitial("game_state");
    return;
  }

  if (gs.mode == "MODE_MAINTENANCE" || gs.mode == "MODE_STANDBY" || gs.mode == "MODE_PREPARE" ||
      gs.mode == "MAINTENANCE" || gs.mode == "MAINT" || gs.mode == "STANDBY" || gs.mode == "PREPARE") {
    applySceneAllOn("game_state");
    return;
  }

  log("WRN", String("Unknown game mode: ") + gs.mode);
}

bool LightingController::handleLightingJsonCommand(JsonDocument& doc) {
  String cmd;
  if (doc["cmd"].is<const char*>()) cmd = upperTrim(String(doc["cmd"].as<const char*>()));
  if (!cmd.length()) return false;

  if (cmd == "SCENE") {
    String name;
    if (doc["name"].is<const char*>()) name = upperTrim(String(doc["name"].as<const char*>()));
    if (name == "INGAME_START") {
      cancelScheduledEffects();
      applySceneInitial("lighting_cmd");
      return true;
    }
    if (name == "ALL_ON" || name == "STANDBY" || name == "PREPARE" || name == "MAINTENANCE") {
      cancelScheduledEffects();
      applySceneAllOn("lighting_cmd");
      return true;
    }
    if (name == "ALL_OFF") {
      cancelScheduledEffects();
      applySceneAllOff("lighting_cmd");
      return true;
    }
    return false;
  }

  auto applySingle = [&](const String& key, bool on, int pct, const char* reason) -> bool {
    ChannelState* ch = findByAnyKey(key);
    if (!ch) return false;
    bool changed[kChannelCount] = {};
    changed[(size_t)ch->ledcCh] = (pct >= 0)
      ? setChannelPercent(ch->id, on, (uint32_t)pct)
      : setChannel(ch->id, on, on ? driver_.maxDuty() : 0);
    publishChangedStates(changed, reason);
    return true;
  };

  if (cmd == "TURN_ON" || cmd == "ON") {
    String light = doc["light"].is<const char*>() ? String(doc["light"].as<const char*>()) : String();
    return applySingle(light, true, -1, "lighting_cmd");
  }
  if (cmd == "TURN_OFF" || cmd == "OFF") {
    String light = doc["light"].is<const char*>() ? String(doc["light"].as<const char*>()) : String();
    return applySingle(light, false, -1, "lighting_cmd");
  }
  if (cmd == "SET") {
    String light = doc["light"].is<const char*>() ? String(doc["light"].as<const char*>()) : String();
    int pct = doc["pct"].is<int>() ? doc["pct"].as<int>() : 100;
    if (pct < 0) pct = 0;
    if (pct > 100) pct = 100;
    return applySingle(light, pct > 0, pct, "lighting_cmd");
  }
  if (cmd == "FADE_IN") {
    JsonArray lights = doc["lights"].as<JsonArray>();
    if (lights.isNull() || lights.size() != 2) return false;
    String keyA = lights[0].is<const char*>() ? String(lights[0].as<const char*>()) : String();
    String keyB = lights[1].is<const char*>() ? String(lights[1].as<const char*>()) : String();
    ChannelState* chA = findByAnyKey(keyA);
    ChannelState* chB = findByAnyKey(keyB);
    if (!chA || !chB) return false;
    uint32_t durationMs = doc["duration_ms"].is<unsigned long>() ? doc["duration_ms"].as<unsigned long>() : 10000UL;
    bool changed[kChannelCount] = {};
    changed[(size_t)chA->ledcCh] = setChannel(chA->id, false, 0, true);
    changed[(size_t)chB->ledcCh] = setChannel(chB->id, false, 0, true);
    publishChangedStates(changed, "lighting_cmd");
    startFadePair(fade1_, chA->id, chB->id, 0, 0, driver_.maxDuty(), driver_.maxDuty(), durationMs, "fade_in", "fade_in_done");
    return true;
  }
  if (cmd == "FADE_TO") {
    JsonArray lights = doc["lights"].as<JsonArray>();
    if (lights.isNull() || lights.size() != 2) return false;
    String keyA = lights[0].is<const char*>() ? String(lights[0].as<const char*>()) : String();
    String keyB = lights[1].is<const char*>() ? String(lights[1].as<const char*>()) : String();
    ChannelState* chA = findByAnyKey(keyA);
    ChannelState* chB = findByAnyKey(keyB);
    if (!chA || !chB) return false;
    int pct = doc["pct"].is<int>() ? doc["pct"].as<int>() : 100;
    if (pct < 0) pct = 0;
    if (pct > 100) pct = 100;
    uint32_t durationMs = doc["duration_ms"].is<unsigned long>() ? doc["duration_ms"].as<unsigned long>() : 10000UL;
    startFadePair(
      fade1_,
      chA->id, chB->id,
      chA->on ? chA->duty : 0,
      chB->on ? chB->duty : 0,
      percentToDuty((uint32_t)pct),
      percentToDuty((uint32_t)pct),
      durationMs,
      "fade_to",
      "fade_to_done"
    );
    return true;
  }
  return false;
}

void LightingController::onLightingCommand(const String& payload) {
  JsonDocument doc;
  if (deserializeJson(doc, payload) != DeserializationError::Ok) {
    log("WRN", String("Bad lighting/cmd payload: ") + payload);
    return;
  }
  if (!handleLightingJsonCommand(doc)) {
    log("WRN", String("Unhandled lighting/cmd payload: ") + payload);
  }
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
}

bool LightingController::setChannel(const char* id, bool on, uint32_t duty, bool preserveZeroDutyWhenOn) {
  ChannelState* ch = findById(String(id ? id : ""));
  if (!ch) return false;
  duty = clampDuty(duty);
  if (on && duty == 0 && !preserveZeroDutyWhenOn) duty = driver_.maxDuty();
  bool changed = (ch->on != on) || (ch->duty != duty);
  ch->on = on;
  ch->duty = duty;
  if (changed) applyOutput(*ch);
  return changed;
}

bool LightingController::setChannelPercent(const char* id, bool on, uint32_t pct) {
  return setChannel(id, on, on ? percentToDuty(pct) : 0);
}

void LightingController::publishChangedStates(const bool changed[], const char* reason) {
  for (size_t i = 0; i < kChannelCount; i++) if (changed[i]) publishChannelState(channels_[i], reason);
}

void LightingController::resetFade(FadePair& fade) {
  fade.active = false;
  fade.idA = nullptr;
  fade.idB = nullptr;
  fade.startMs = 0;
  fade.durationMs = 0;
  fade.fromDutyA = 0;
  fade.fromDutyB = 0;
  fade.toDutyA = 0;
  fade.toDutyB = 0;
  fade.tickReason = nullptr;
  fade.doneReason = nullptr;
}

void LightingController::startFadePair(FadePair& fade,
                                       const char* idA, const char* idB,
                                       uint32_t fromDutyA, uint32_t fromDutyB,
                                       uint32_t toDutyA, uint32_t toDutyB,
                                       uint32_t durationMs,
                                       const char* tickReason,
                                       const char* doneReason) {
  fade.active = true;
  fade.idA = idA;
  fade.idB = idB;
  fade.startMs = millis();
  fade.durationMs = durationMs;
  fade.fromDutyA = clampDuty(fromDutyA);
  fade.fromDutyB = clampDuty(fromDutyB);
  fade.toDutyA = clampDuty(toDutyA);
  fade.toDutyB = clampDuty(toDutyB);
  fade.tickReason = tickReason;
  fade.doneReason = doneReason;
}

void LightingController::updateFadePair(FadePair& fade, uint32_t nowMs) {
  if (!fade.active || !fade.idA || !fade.idB || fade.durationMs == 0) return;

  const uint32_t elapsed = (uint32_t)(nowMs - fade.startMs);
  uint32_t dutyA = fade.toDutyA;
  uint32_t dutyB = fade.toDutyB;
  const char* reason = fade.doneReason;

  if (elapsed < fade.durationMs) {
    reason = fade.tickReason;
    if (fade.toDutyA >= fade.fromDutyA) {
      dutyA = fade.fromDutyA + (uint32_t)(((uint64_t)(fade.toDutyA - fade.fromDutyA) * elapsed) / fade.durationMs);
    } else {
      dutyA = fade.fromDutyA - (uint32_t)(((uint64_t)(fade.fromDutyA - fade.toDutyA) * elapsed) / fade.durationMs);
    }
    if (fade.toDutyB >= fade.fromDutyB) {
      dutyB = fade.fromDutyB + (uint32_t)(((uint64_t)(fade.toDutyB - fade.fromDutyB) * elapsed) / fade.durationMs);
    } else {
      dutyB = fade.fromDutyB - (uint32_t)(((uint64_t)(fade.fromDutyB - fade.toDutyB) * elapsed) / fade.durationMs);
    }
  } else {
    fade.active = false;
  }

  bool changed[kChannelCount] = {};
  ChannelState* chA = findById(String(fade.idA));
  ChannelState* chB = findById(String(fade.idB));
  const bool onA = (dutyA > 0) || !fade.active;
  const bool onB = (dutyB > 0) || !fade.active;
  if (chA) changed[(size_t)chA->ledcCh] = setChannel(fade.idA, onA, dutyA, true);
  if (chB) changed[(size_t)chB->ledcCh] = setChannel(fade.idB, onB, dutyB, true);
  publishChangedStates(changed, reason);
}

void LightingController::applySceneInitial(const char* reason) {
  bool changed[kChannelCount] = {};
  changed[0] = setChannel("1", false, 0);
  changed[1] = setChannel("2", false, 0);
  changed[2] = setChannel("3", true, driver_.maxDuty());
  changed[3] = setChannel("4", true, driver_.maxDuty());
  changed[4] = setChannel("5", false, 0);
  changed[5] = setChannel("6", false, 0);
  changed[6] = setChannel("7", true, driver_.maxDuty());
  changed[7] = setChannel("8", false, 0);
  changed[8] = setChannel("9", false, 0);
  changed[9] = setChannel("10", false, 0);
  publishChangedStates(changed, reason);
}

void LightingController::applySceneAllOn(const char* reason) {
  bool changed[kChannelCount] = {};
  for (size_t i = 0; i < kChannelCount; i++) changed[i] = setChannel(channels_[i].id, true, driver_.maxDuty());
  publishChangedStates(changed, reason);
}

void LightingController::applySceneAllOff(const char* reason) {
  bool changed[kChannelCount] = {};
  for (size_t i = 0; i < kChannelCount; i++) changed[i] = setChannel(channels_[i].id, false, 0);
  publishChangedStates(changed, reason);
}

bool LightingController::publish(const char* topic, const String& payload, bool retained) const {
  if (!ctx_) return false;
  if (!mqttConnected()) return false;
  return ctx_->publish(topic, payload, retained);
}

void LightingController::publishAllStates(const char* reason) {
  for (size_t i = 0; i < kChannelCount; i++) publishChannelState(channels_[i], reason);
}

void LightingController::log(const char* level, const String& msg) const {
  if (ctx_) ctx_->log(level, msg);
}

void LightingController::log(const char* level, const String& msg, const String& dataJson) const {
  if (ctx_) ctx_->log(level, msg, dataJson);
}

void LightingController::publishChannelState(const ChannelState& ch, const char* reason) {
  if (!ctx_) return;
  const uint32_t max = driver_.maxDuty();
  const uint32_t pct = (max == 0) ? 0 : (uint32_t)((ch.duty * (uint64_t)100 + (max / 2)) / max);
  String payload = String("{\"fw\":\"") + ctx_->fwVersion() +
                   "\",\"up\":" + String(millis() / 1000) +
                   ",\"id\":\"" + ch.id +
                   "\",\"name\":\"" + ch.name +
                   "\",\"mode\":\"" + appliedMode_ +
                   "\",\"on\":" + String(ch.on ? 1 : 0) +
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
  publish(topic.c_str(), payload, true);
}

void LightingController::onMosfetCommandTopic(const char* topic, const String& payload) {
  if (!ctx_) return;
  String id;
  if (!parseChannelIdFromTopic(String(topic ? topic : ""), id)) {
    log("WRN", String("bad topic: ") + (topic ? topic : ""));
    return;
  }

  ChannelState* ch = findById(id);
  if (!ch) {
    log("WRN", String("unknown channel id: ") + id);
    return;
  }

  bool handled = false;
  {
    JsonDocument doc;
    if (deserializeJson(doc, payload) == DeserializationError::Ok) {
      String cmdS;
      if (doc["cmd"].is<const char*>()) cmdS = upperTrim(String(doc["cmd"].as<const char*>()));
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
      }
    }
  }
  if (!handled) {
    String p = upperTrim(payload);
    if (p == "ON") { ch->on = true; handled = true; }
    else if (p == "OFF") { ch->on = false; handled = true; }
    else if (p == "DIM" || p == "DIMMED") {
      ch->on = true;
      ch->duty = percentToDuty(ch->dimPercent);
      handled = true;
    } else {
      int32_t v = 0;
      if (parseIntLoose(p, v)) {
        ch->on = (v > 0);
        ch->duty = mapUserValueToDuty(v);
        handled = true;
      }
    }
  }
  if (!handled) {
    log("WRN", String("Unrecognized payload on channel ") + id + ": " + payload);
    return;
  }
  if (ch->on && ch->duty == 0) ch->duty = driver_.maxDuty();
  if (!ch->on) ch->duty = 0;
  applyOutput(*ch);
  publishChannelState(*ch, "cmd");
}
