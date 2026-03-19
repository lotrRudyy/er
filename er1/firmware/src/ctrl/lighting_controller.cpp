#include "ctrl/lighting_controller.h"

#include <Arduino.h>
#include <ArduinoJson.h>
#include <cstdlib>
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
  static const char* ids[kChannelCount] = {"1","2","3","4","5","6","7","8","9","10"};
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
    "r3_uv",
  };
  for (size_t i = 0; i < kChannelCount; i++) {
    channels_[i].id = ids[i];
    channels_[i].name = names[i];
    fades_[i].index = i;
    dirtyReasons_[i] = nullptr;
    dirty_[i] = false;
  }
}

bool LightingController::mqttConnected() const {
  if (!ctx_) return false;
  auto* c = ctx_->mqttClient();
  return c && c->connected();
}

bool LightingController::publish(const char* topic, const String& payload, bool retained) const {
  if (!ctx_ || !mqttConnected()) return false;
  return ctx_->publish(topic, payload, retained);
}

void LightingController::log(const char* level, const String& msg) const {
  if (ctx_) ctx_->log(level, msg);
}

void LightingController::log(const char* level, const String& msg, const String& dataJson) const {
  if (ctx_) ctx_->log(level, msg, dataJson);
}

bool LightingController::parseChannelIdFromTopic(const String& topic, String& outId) const {
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
  for (size_t i = 0; i < kChannelCount; i++) {
    if (name.equalsIgnoreCase(channels_[i].name)) return &channels_[i];
  }
  return nullptr;
}

LightingController::ChannelState* LightingController::findLight(const String& token) {
  ChannelState* ch = findByName(token);
  if (ch) return ch;
  return findById(token);
}

uint32_t LightingController::clampDuty(uint32_t duty) const {
  const uint32_t max = driver_.maxDuty();
  return duty > max ? max : duty;
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

bool LightingController::setChannel(size_t index, bool on, uint32_t duty, bool preserveZeroDutyWhenOn) {
  if (index >= kChannelCount) return false;
  ChannelState& ch = channels_[index];
  duty = clampDuty(duty);
  if (on && duty == 0 && !preserveZeroDutyWhenOn) duty = driver_.maxDuty();
  const bool changed = (ch.on != on) || (ch.duty != duty);
  ch.on = on;
  ch.duty = duty;
  if (changed) applyOutput(ch);
  return changed;
}

bool LightingController::setChannelByToken(const String& token, bool on, uint32_t duty, bool preserveZeroDutyWhenOn) {
  ChannelState* ch = findLight(token);
  if (!ch) return false;
  stopFade((size_t)ch->ledcCh);
  return setChannel((size_t)ch->ledcCh, on, duty, preserveZeroDutyWhenOn);
}

bool LightingController::setChannelPercentByToken(const String& token, bool on, uint32_t pct) {
  return setChannelByToken(token, on, on ? percentToDuty(pct) : 0);
}

void LightingController::stopFade(size_t index) {
  if (index >= kChannelCount) return;
  fades_[index].active = false;
}

void LightingController::stopAllFades() {
  for (size_t i = 0; i < kChannelCount; i++) fades_[i].active = false;
}

void LightingController::startFade(size_t index, uint32_t toDuty, uint32_t durationMs, const char* reason) {
  if (index >= kChannelCount) return;
  FadeState& fade = fades_[index];
  ChannelState& ch = channels_[index];
  fade.active = true;
  fade.index = index;
  fade.startMs = millis();
  fade.durationMs = durationMs == 0 ? 1 : durationMs;
  fade.fromDuty = ch.on ? ch.duty : 0;
  fade.toDuty = clampDuty(toDuty);
  fade.reason = reason;
  ch.on = true;
  applyOutput(ch);
}

void LightingController::updateFade(FadeState& fade, uint32_t nowMs) {
  if (!fade.active) return;
  const uint32_t elapsed = (uint32_t)(nowMs - fade.startMs);
  const uint32_t duration = fade.durationMs == 0 ? 1 : fade.durationMs;

  uint32_t duty = fade.toDuty;
  if (elapsed < duration) {
    if (fade.toDuty >= fade.fromDuty) {
      duty = fade.fromDuty + (uint32_t)(((uint64_t)(fade.toDuty - fade.fromDuty) * elapsed) / duration);
    } else {
      duty = fade.fromDuty - (uint32_t)(((uint64_t)(fade.fromDuty - fade.toDuty) * elapsed) / duration);
    }
  } else {
    fade.active = false;
  }

  ChannelState& ch = channels_[fade.index];
  const bool on = duty > 0 || fade.toDuty > 0;
  if (setChannel(fade.index, on, duty, true)) {
    markDirty(fade.index, fade.reason ? fade.reason : "fade");
  }
}

void LightingController::applySceneInitial(const char* reason) {
  stopAllFades();
  bool changed[kChannelCount] = {};
  changed[0] = setChannel(0, false, 0);
  changed[1] = setChannel(1, false, 0);
  changed[2] = setChannel(2, true, driver_.maxDuty());
  changed[3] = setChannel(3, true, driver_.maxDuty());
  changed[4] = setChannel(4, false, 0);
  changed[5] = setChannel(5, false, 0);
  changed[6] = setChannel(6, true, driver_.maxDuty());
  changed[7] = setChannel(7, false, 0);
  changed[8] = setChannel(8, false, 0);
  changed[9] = setChannel(9, false, 0);
  publishChangedStates(changed, reason);
}

void LightingController::applySceneAllOn(const char* reason) {
  stopAllFades();
  bool changed[kChannelCount] = {};
  for (size_t i = 0; i < kChannelCount; i++) changed[i] = setChannel(i, true, driver_.maxDuty());
  publishChangedStates(changed, reason);
}

void LightingController::applySceneAllOff(const char* reason) {
  stopAllFades();
  bool changed[kChannelCount] = {};
  for (size_t i = 0; i < kChannelCount; i++) changed[i] = setChannel(i, false, 0);
  publishChangedStates(changed, reason);
}

void LightingController::publishChannelState(const ChannelState& ch, const char* reason) {
  if (!ctx_) return;
  const uint32_t max = driver_.maxDuty();
  const uint32_t pct = (max == 0) ? 0 : (uint32_t)((ch.duty * (uint64_t)100 + (max / 2)) / max);

  String payload = String("{\"fw\":\"") + ctx_->fwVersion() +
                   "\",\"up\":" + String(millis() / 1000) +
                   ",\"id\":\"" + ch.id +
                   "\",\"light\":\"" + ch.name +
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

void LightingController::publishAllStates(const char* reason) {
  for (size_t i = 0; i < kChannelCount; i++) publishChannelState(channels_[i], reason);
}

void LightingController::markDirty(size_t index, const char* reason) {
  if (index >= kChannelCount) return;
  dirty_[index] = true;
  dirtyReasons_[index] = reason;
}

void LightingController::flushDirtyStates(uint32_t maxCount) {
  uint32_t sent = 0;
  for (size_t i = 0; i < kChannelCount && sent < maxCount; i++) {
    if (!dirty_[i]) continue;
    dirty_[i] = false;
    publishChannelState(channels_[i], dirtyReasons_[i] ? dirtyReasons_[i] : "state");
    dirtyReasons_[i] = nullptr;
    ++sent;
  }
}


void LightingController::queueBulkCommand(BulkCommand cmd) {
  queuedBulkCommand_ = cmd;
  queuedBulkAtMs_ = millis();
}

void LightingController::runQueuedBulkCommand() {
  if (queuedBulkCommand_ == BulkCommand::None) return;
  const uint32_t nowMs = millis();
  if ((uint32_t)(nowMs - lastBulkApplyMs_) < 150) return;

  const BulkCommand cmd = queuedBulkCommand_;
  queuedBulkCommand_ = BulkCommand::None;
  lastBulkApplyMs_ = nowMs;

  if (cmd == BulkCommand::AllOn) {
    applySceneAllOn("cmd_all_on");
    return;
  }
  if (cmd == BulkCommand::AllOff) {
    applySceneAllOff("cmd_all_off");
    return;
  }
  if (cmd == BulkCommand::SceneInitial) {
    applySceneInitial("cmd_scene_initial_ingame");
    return;
  }
}
void LightingController::publishChangedStates(const bool changed[], const char* reason) {
  for (size_t i = 0; i < kChannelCount; i++) {
    if (changed[i]) markDirty(i, reason);
  }
}

void LightingController::begin(Core::NodeContext& ctx) {
  ctx_ = &ctx;

  static const uint8_t kPins[kChannelCount] = {16, 17, 21, 22, 14, 26, 25, 32, 33, 4};
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
  queuedBulkCommand_ = BulkCommand::None;
  queuedBulkAtMs_ = 0;
  lastBulkApplyMs_ = 0;
  stopAllFades();
}

void LightingController::tick(uint32_t nowMs) {
  const bool conn = mqttConnected();
  if (conn && !bootStatePublished_) {
    publishAllStates("boot");
    bootStatePublished_ = true;
  }
  lastMqttConnected_ = conn;
  runQueuedBulkCommand();
  for (size_t i = 0; i < kChannelCount; i++) updateFade(fades_[i], nowMs);
  if (conn) flushDirtyStates(1);
}

bool LightingController::onCmd(const char* cmd, const char* payload) {
  String cmdStr = String(cmd ? cmd : "");
  String payloadStr = String(payload ? payload : "");
  cmdStr.trim();
  payloadStr.trim();

  // NodeCore delivers node-specific topic <node>/cmd here.
  // For JSON payloads published to lighting/cmd, some core paths pass the whole
  // JSON blob in cmd and leave payload empty. Forward raw JSON unchanged.
  if (cmdStr.startsWith("{")) {
    onLightingCommandTopic(cmdStr);
    return true;
  }
  if (payloadStr.startsWith("{")) {
    onLightingCommandTopic(payloadStr);
    return true;
  }

  String upper = cmdStr;
  upper.toUpperCase();
  if (upper == "ALL_ON") {
    onLightingCommandTopic(String("{\"cmd\":\"all_on\"}"));
    return true;
  }
  if (upper == "ALL_OFF") {
    onLightingCommandTopic(String("{\"cmd\":\"all_off\"}"));
    return true;
  }

  // Support split command style, e.g. cmd='turn_on' payload='"light":"r3_uv"'
  if (cmdStr.length() && payloadStr.length() && !payloadStr.startsWith("{")) {
    String json = String("{\"cmd\":\"") + cmdStr + "\"," + payloadStr + "}";
    onLightingCommandTopic(json);
    return true;
  }

  log("DBG", String("Lighting node cmd ignored: ") + cmdStr + (payloadStr.length() ? String(" ") + payloadStr : String("")));
  return true;
}

void LightingController::onGameStateMessage(const String& payload) {
  JsonDocument doc;
  DeserializationError err = deserializeJson(doc, payload);
  if (err) {
    log("WRN", String("lighting game/state json parse failed: ") + err.c_str());
    return;
  }

  const char* mode = doc["mode"] | "";
  String modeS(mode);
  modeS.trim();

  if (modeS == "MODE_INGAME") {
    applySceneInitial("game_state_ingame");
    return;
  }
  if (modeS == "MODE_MAINTENANCE" || modeS == "MODE_STANDBY" || modeS == "MODE_PREPARE") {
    applySceneAllOn("game_state_non_ingame");
    return;
  }

  log("WRN", String("lighting unknown mode in game/state: ") + modeS);
}

void LightingController::onLightingCommandTopic(const String& payload) {
  JsonDocument doc;
  DeserializationError err = deserializeJson(doc, payload);
  if (err) {
    log("WRN", String("lighting/cmd json parse failed: ") + err.c_str());
    return;
  }

  auto pickString = [&](const char* a, const char* b = nullptr, const char* c = nullptr) -> String {
    if (a && doc[a].is<const char*>()) return String(doc[a].as<const char*>());
    if (b && doc[b].is<const char*>()) return String(doc[b].as<const char*>());
    if (c && doc[c].is<const char*>()) return String(doc[c].as<const char*>());
    return String();
  };
  auto pickInt = [&](const char* a, const char* b = nullptr, int def = 0) -> int {
    if (a && doc[a].is<int>()) return doc[a].as<int>();
    if (b && doc[b].is<int>()) return doc[b].as<int>();
    return def;
  };
  auto pickArray = [&](const char* a, const char* b = nullptr) -> JsonArray {
    JsonArray arr;
    if (a) arr = doc[a].as<JsonArray>();
    if (arr.isNull() && b) arr = doc[b].as<JsonArray>();
    return arr;
  };

  String cmd = upperTrim(pickString("cmd", "CMD"));
  if (!cmd.length()) {
    log("WRN", String("lighting/cmd missing cmd payload=") + payload);
    return;
  }

  if (cmd == "ALL_ON") {
    queueBulkCommand(BulkCommand::AllOn);
    return;
  }
  if (cmd == "ALL_OFF") {
    queueBulkCommand(BulkCommand::AllOff);
    return;
  }
  if (cmd == "SCENE") {
    String scene = upperTrim(pickString("scene", "SCENE"));
    if (scene == "INITIAL_INGAME") {
      queueBulkCommand(BulkCommand::SceneInitial);
      return;
    }
    if (scene == "ALL_ON") {
      queueBulkCommand(BulkCommand::AllOn);
      return;
    }
    if (scene == "ALL_OFF") {
      queueBulkCommand(BulkCommand::AllOff);
      return;
    }
    log("WRN", String("unknown lighting scene: ") + scene);
    return;
  }

  bool changed[kChannelCount] = {};

  if (cmd == "TURN_ON" || cmd == "TURN_OFF" || cmd == "SET") {
    String light = pickString("light", "LIGHT");
    if (!light.length()) {
      log("WRN", String("lighting/cmd missing light for ") + cmd);
      return;
    }
    ChannelState* ch = findLight(light);
    if (!ch) {
      log("WRN", String("unknown light: ") + light);
      return;
    }
    size_t idx = (size_t)ch->ledcCh;
    if (cmd == "TURN_ON") changed[idx] = setChannel(idx, true, driver_.maxDuty());
    else if (cmd == "TURN_OFF") changed[idx] = setChannel(idx, false, 0);
    else {
      int32_t pct = pickInt("pct", "PCT", 100);
      changed[idx] = setChannel(idx, true, percentToDuty((uint32_t)max(0, min(100, pct))), true);
    }
    publishChangedStates(changed, "cmd_single");
    return;
  }

  if (cmd == "TURN_ON_MANY") {
    JsonArray arr = pickArray("lights", "LIGHTS");
    if (arr.isNull()) {
      log("WRN", "turn_on_many requires lights[]");
      return;
    }
    for (JsonVariant v : arr) {
      const char* name = v.as<const char*>();
      if (!name) continue;
      ChannelState* ch = findLight(String(name));
      if (!ch) continue;
      changed[(size_t)ch->ledcCh] = setChannel((size_t)ch->ledcCh, true, driver_.maxDuty());
    }
    publishChangedStates(changed, "cmd_turn_on_many");
    return;
  }

  if (cmd == "FADE_IN" || cmd == "FADE_TO") {
    JsonArray arr = pickArray("lights", "LIGHTS");
    if (arr.isNull()) {
      log("WRN", String(cmd) + " requires lights[]");
      return;
    }
    const uint32_t durationMs = (uint32_t)pickInt("duration_ms", "DURATION_MS", 1000);
    int32_t pct = 100;
    pct = pickInt("pct", "PCT", pct);
    pct = max(0, min(100, pct));
    const uint32_t targetDuty = percentToDuty((uint32_t)pct);

    for (JsonVariant v : arr) {
      const char* name = v.as<const char*>();
      if (!name) continue;
      ChannelState* ch = findLight(String(name));
      if (!ch) continue;
      size_t idx = (size_t)ch->ledcCh;
      if (cmd == "FADE_IN") {
        setChannel(idx, true, 0, true);
        startFade(idx, targetDuty, durationMs, "cmd_fade_in");
      } else {
        startFade(idx, targetDuty, durationMs, "cmd_fade_to");
      }
      changed[idx] = true;
    }
    publishChangedStates(changed, cmd == "FADE_IN" ? "cmd_fade_in_start" : "cmd_fade_to_start");
    return;
  }

  log("WRN", String("unknown lighting/cmd: ") + cmd, payload);
}

void LightingController::onMosfetCommandTopic(const char* topic, const String& payload) {
  String id;
  if (!parseChannelIdFromTopic(String(topic ? topic : ""), id)) {
    log("WRN", String("bad lighting topic: ") + (topic ? topic : ""));
    return;
  }

  ChannelState* ch = findById(id);
  if (!ch) {
    log("WRN", String("unknown channel id: ") + id);
    return;
  }

  bool handled = false;
  bool on = ch->on;
  uint32_t duty = ch->duty;

  {
    JsonDocument doc;
    if (!deserializeJson(doc, payload)) {
      String cmd = upperTrim(String((const char*)(doc["cmd"] | "")));
      if (cmd == "ON") {
        on = true;
        duty = driver_.maxDuty();
        handled = true;
      } else if (cmd == "OFF") {
        on = false;
        duty = 0;
        handled = true;
      } else if (cmd == "PWM") {
        int32_t v = 0;
        if (doc["value"].is<int>()) v = doc["value"].as<int>();
        else if (doc["pwm"].is<int>()) v = doc["pwm"].as<int>();
        else if (doc["duty"].is<int>()) v = doc["duty"].as<int>();
        on = true;
        duty = mapUserValueToDuty(v);
        handled = true;
      } else if (doc["on"].is<bool>()) {
        on = doc["on"].as<bool>();
        duty = on ? max(ch->duty, driver_.maxDuty()) : 0;
        handled = true;
      }
    }
  }

  if (!handled) {
    String p = upperTrim(payload);
    if (p == "ON") {
      on = true;
      duty = driver_.maxDuty();
      handled = true;
    } else if (p == "OFF") {
      on = false;
      duty = 0;
      handled = true;
    } else {
      int32_t v = 0;
      if (parseIntLoose(p, v)) {
        on = v > 0;
        duty = mapUserValueToDuty(v);
        handled = true;
      }
    }
  }

  if (!handled) {
    log("WRN", String("Unrecognized payload on channel ") + id + ": " + payload);
    return;
  }

  stopFade((size_t)ch->ledcCh);
  setChannel((size_t)ch->ledcCh, on, duty, true);
  publishChannelState(*ch, "mosfet_cmd");
}
