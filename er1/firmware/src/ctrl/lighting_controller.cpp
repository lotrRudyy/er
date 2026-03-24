#include "ctrl/lighting_controller.h"

#include <Arduino.h>
#include <ArduinoJson.h>
#include <cstdlib>
#include <cstring>

namespace {

constexpr const char* kCmdPrefix = "lighting/mosfet/";
constexpr const char* kCmdSuffix = "/cmd";
constexpr uint8_t kBulkOrder[LightingController::kChannelCount] = {0, 1, 2, 3, 4, 5, 6, 7, 8, 9};
constexpr uint32_t kBulkStepGapMs = 30;
constexpr uint32_t kPendingStepGapMs = 15;

enum LightIndex : size_t {
  kR2Chess = 0,
  kR2Schronk = 1,
  kR1Bild = 2,
  kR1Stuen = 3,
  kR3Slider = 4,
  kR3Cage = 5,
  kTorchStiege = 6,
  kTorchR2R3 = 7,
  kTorchR2 = 8,
  kR3Uv = 9,
};

struct PhaseScene {
  int phase;
  uint8_t pct[LightingController::kChannelCount];
};

constexpr PhaseScene kPhaseScenes[] = {
  {0,  {  0,  0,  0,  0,  0,  0,  0,  0,  0,  0}}, // standby
  {1,  {100,100,100,100,100,100,100,100,100,100}}, // maintenance
  {2,  {  0,  0,100,100,100,100,100,  0,  0,  0}}, // prepare
  {3,  {  0,  0,100,100,  0,  0,100,  0,  0,  0}}, // start
  {4,  {  0,  0,100,100,  0,  0,100,  0,  0,  0}}, // piano
  {5,  {100,100,100,100,  0,  0,100,  0,100,  0}}, // open_prison
  {6,  {100,100,100,100,  0,  0,100,  0,100,  0}}, // mount_wheel
  {7,  {100,100,100,100,  0,  0,100,  0,100,  0}}, // rope_paths
  {8,  {100,100,100,100,  0,  0,100,  0,100,  0}}, // tangram_magnet
  {9,  {100,100,100,100,  0,  0,100,  0,100,  0}}, // chess
  {10, {100,100,100,100,100,100,100,100,100,  0}}, // knocking_candles_pre
  {11, {100,100,100,100,100,100,100,100,100,  0}}, // candles
  {12, {100,100,100,100, 25, 25,100,100,100,100}}, // star_slider
  {13, {100,100,100,100,100,100,100,100,100,100}}, // sissi
  {14, {100,100,100,100,100,100,100,100,100,100}}, // solved
};

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

const PhaseScene* findPhaseScene(int phase) {
  for (const auto& scene : kPhaseScenes) {
    if (scene.phase == phase) return &scene;
  }
  return nullptr;
}

} // namespace

LightingController::LightingController() {
  static const char* ids[kChannelCount] = {"1","2","3","4","5","6","7","8","9","10"};
  static const char* names[kChannelCount] = {
    "r2_chess", "r2_schronk", "r1_bild", "r1_stuen", "r3_slider",
    "r3_cage", "torch_stiege", "torch_r2r3", "torch_r2", "r3_uv",
  };

  for (size_t i = 0; i < kChannelCount; i++) {
    channels_[i].id = ids[i];
    channels_[i].name = names[i];
    fades_[i].index = i;
    dirty_[i] = false;
    dirtyReasons_[i] = nullptr;
    pendingValid_[i] = false;
    pendingOn_[i] = false;
    pendingDuty_[i] = 0;
    pendingPreserveZero_[i] = false;
    pendingReason_[i] = nullptr;
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
  int start = (int)strlen(kCmdPrefix);
  int end = topic.length() - (int)strlen(kCmdSuffix);
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
  return ch ? ch : findById(token);
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
  driver_.writeDuty(ch.ledcCh, ch.on ? ch.duty : 0);
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

void LightingController::stopFade(size_t index) {
  if (index < kChannelCount) fades_[index].active = false;
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

  const bool on = duty > 0 || fade.toDuty > 0;
  if (setChannel(fade.index, on, duty, true)) {
    markDirty(fade.index, fade.reason ? fade.reason : "fade");
  }
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
  for (size_t i = 0; i < kChannelCount; i++) {
    publishChannelState(channels_[i], reason);
  }
}

void LightingController::markDirty(size_t index, const char* reason) {
  if (index >= kChannelCount) return;
  dirty_[index] = true;
  dirtyReasons_[index] = reason;
}

void LightingController::clearDirty() {
  for (size_t i = 0; i < kChannelCount; i++) {
    dirty_[i] = false;
    dirtyReasons_[i] = nullptr;
    pendingValid_[i] = false;
    pendingOn_[i] = false;
    pendingDuty_[i] = 0;
    pendingPreserveZero_[i] = false;
    pendingReason_[i] = nullptr;
  }
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
}

void LightingController::cancelBulkCommand() {
  activeBulkCommand_ = BulkCommand::None;
  queuedBulkCommand_ = BulkCommand::None;
  bulkIndex_ = 0;
  lastBulkStepMs_ = 0;
  bulkReason_ = nullptr;
  bulkTargetOn_ = false;
  bulkTargetDuty_ = 0;
}

void LightingController::startBulkCommand(BulkCommand cmd) {
  stopAllFades();
  clearAllPendingChannels();

  activeBulkCommand_ = cmd;
  queuedBulkCommand_ = BulkCommand::None;
  bulkIndex_ = 0;
  lastBulkStepMs_ = 0;

  switch (cmd) {
    case BulkCommand::AllOn:
      bulkReason_ = "cmd_all_on";
      bulkTargetOn_ = true;
      bulkTargetDuty_ = driver_.maxDuty();
      break;
    case BulkCommand::AllOff:
      bulkReason_ = "cmd_all_off";
      bulkTargetOn_ = false;
      bulkTargetDuty_ = 0;
      break;
    case BulkCommand::None:
    default:
      bulkReason_ = nullptr;
      bulkTargetOn_ = false;
      bulkTargetDuty_ = 0;
      break;
  }
}

size_t LightingController::bulkApplyCountForTick() const {
  return 1;
}

void LightingController::runBulkCommandStep(uint32_t nowMs) {
  if (queuedBulkCommand_ != BulkCommand::None && queuedBulkCommand_ != activeBulkCommand_) {
    startBulkCommand(queuedBulkCommand_);
  }
  if (activeBulkCommand_ == BulkCommand::None) return;
  if (lastBulkStepMs_ != 0 && (uint32_t)(nowMs - lastBulkStepMs_) < kBulkStepGapMs) return;

  lastBulkStepMs_ = nowMs;

  size_t steps = bulkApplyCountForTick();
  while (steps-- && bulkIndex_ < kChannelCount) {
    const size_t idx = kBulkOrder[bulkIndex_++];
    queueChannelTarget(idx, bulkTargetOn_, bulkTargetDuty_, bulkReason_ ? bulkReason_ : "bulk", false);
  }

  if (bulkIndex_ >= kChannelCount) {
    activeBulkCommand_ = BulkCommand::None;
    bulkIndex_ = 0;
    lastBulkStepMs_ = 0;
  }
}

void LightingController::queueChannelTarget(size_t index, bool on, uint32_t duty, const char* reason, bool preserveZeroDutyWhenOn) {
  if (index >= kChannelCount) return;
  pendingValid_[index] = true;
  pendingOn_[index] = on;
  pendingDuty_[index] = clampDuty(duty);
  pendingPreserveZero_[index] = preserveZeroDutyWhenOn;
  pendingReason_[index] = reason;
}

void LightingController::clearPendingChannel(size_t index) {
  if (index >= kChannelCount) return;
  pendingValid_[index] = false;
  pendingOn_[index] = false;
  pendingDuty_[index] = 0;
  pendingPreserveZero_[index] = false;
  pendingReason_[index] = nullptr;
}

void LightingController::clearAllPendingChannels() {
  for (size_t i = 0; i < kChannelCount; i++) {
    clearPendingChannel(i);
  }
}

void LightingController::runPendingChannelStep(uint32_t nowMs) {
  if (lastPendingStepMs_ != 0 && (uint32_t)(nowMs - lastPendingStepMs_) < kPendingStepGapMs) return;

  for (size_t i = 0; i < kChannelCount; i++) {
    if (!pendingValid_[i]) continue;

    const bool changed = setChannel(i, pendingOn_[i], pendingDuty_[i], pendingPreserveZero_[i]);
    const char* reason = pendingReason_[i];
    clearPendingChannel(i);
    lastPendingStepMs_ = nowMs;

    if (changed) markDirty(i, reason ? reason : "pending");
    break;
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

  for (size_t i = 0; i < kChannelCount; i++) {
    applyOutput(channels_[i]);
  }

  bootStatePublished_ = false;
  cancelBulkCommand();
  clearAllPendingChannels();
  stopAllFades();
  lastPendingStepMs_ = 0;
  clearDirty();
}

void LightingController::tick(uint32_t nowMs) {
  const bool conn = mqttConnected();
  if (conn && !bootStatePublished_) {
    publishAllStates("boot");
    bootStatePublished_ = true;
  }

  runBulkCommandStep(nowMs);
  runPendingChannelStep(nowMs);

  for (size_t i = 0; i < kChannelCount; i++) {
    updateFade(fades_[i], nowMs);
  }

  if (conn) flushDirtyStates(1);
}

bool LightingController::onCmd(const char* cmd, const char* payload) {
  String cmdStr(cmd ? cmd : "");
  String payloadStr(payload ? payload : "");
  cmdStr.trim();
  payloadStr.trim();

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
  if (upper == "TURN_ON" && payloadStr.length()) {
    onLightingCommandTopic(String("{\"cmd\":\"turn_on\",\"light\":\"") + payloadStr + "\"}");
    return true;
  }
  if (upper == "TURN_OFF" && payloadStr.length()) {
    onLightingCommandTopic(String("{\"cmd\":\"turn_off\",\"light\":\"") + payloadStr + "\"}");
    return true;
  }

  log("DBG", String("Lighting node cmd ignored: ") + cmdStr + (payloadStr.length() ? String(" ") + payloadStr : String("")));
  return true;
}

void LightingController::applyPhaseScene(int phase, const char* reason) {
  if (currentPhase_ == phase) return;

  const PhaseScene* scene = findPhaseScene(phase);
  if (!scene) {
    log("WRN", String("lighting has no scene for phase ") + phase);
    return;
  }

  currentPhase_ = phase;
  stopAllFades();
  cancelBulkCommand();
  clearAllPendingChannels();

  for (size_t i = 0; i < kChannelCount; ++i) {
    const uint8_t pct = scene->pct[i];
    const bool on = pct > 0;
    const uint32_t duty = percentToDuty(pct);
    queueChannelTarget(i, on, duty, reason ? reason : "phase_scene", false);
  }
}

void LightingController::onGameStateMessage(const String& payload) {
  JsonDocument doc;
  DeserializationError err = deserializeJson(doc, payload);
  if (err) {
    log("WRN", String("lighting game/state json parse failed: ") + err.c_str());
    return;
  }

  if (!doc["phase"].is<int>()) {
    log("WRN", String("lighting game/state missing integer phase: ") + payload);
    return;
  }

  applyPhaseScene(doc["phase"].as<int>(), "game_state_phase");
}

void LightingController::onLightingCommandTopic(const String& payload) {
  JsonDocument doc;
  DeserializationError err = deserializeJson(doc, payload);
  if (err) {
    log("WRN", String("lighting/cmd json parse failed: ") + err.c_str());
    return;
  }

  auto pickString = [&](const char* a, const char* b = nullptr) -> String {
    if (a && doc[a].is<const char*>()) return String(doc[a].as<const char*>());
    if (b && doc[b].is<const char*>()) return String(doc[b].as<const char*>());
    return String();
  };

  auto pickInt = [&](const char* a, const char* b = nullptr, int def = 0) -> int {
    if (a && doc[a].is<int>()) return doc[a].as<int>();
    if (b && doc[b].is<int>()) return doc[b].as<int>();
    return def;
  };

  String cmd = upperTrim(pickString("cmd", "CMD"));
  if (!cmd.length()) {
    log("WRN", String("lighting/cmd missing cmd payload=") + payload);
    return;
  }

  if (cmd == "SET_PHASE") {
    if (!doc["phase"].is<int>()) {
      log("WRN", "lighting set_phase requires integer phase");
      return;
    }
    applyPhaseScene(doc["phase"].as<int>(), "lighting_cmd_set_phase");
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

    cancelBulkCommand();
    size_t idx = (size_t)ch->ledcCh;
    stopFade(idx);
    clearPendingChannel(idx);

    if (cmd == "TURN_ON") {
      queueChannelTarget(idx, true, driver_.maxDuty(), "cmd_single", false);
    } else if (cmd == "TURN_OFF") {
      queueChannelTarget(idx, false, 0, "cmd_single", false);
    } else {
      int32_t pct = pickInt("pct", "PCT", 100);
      if (pct < 0) pct = 0;
      if (pct > 100) pct = 100;
      queueChannelTarget(idx, true, percentToDuty((uint32_t)pct), "cmd_single", true);
    }
    return;
  }

  if (cmd == "TURN_ON_MANY") {
    JsonArray arr = doc["lights"].as<JsonArray>();
    if (arr.isNull()) arr = doc["LIGHTS"].as<JsonArray>();
    if (arr.isNull()) {
      log("WRN", String("turn_on_many requires lights[] payload=") + payload);
      return;
    }

    cancelBulkCommand();
    for (JsonVariant v : arr) {
      const char* name = v.as<const char*>();
      if (!name) continue;
      ChannelState* ch = findLight(String(name));
      if (!ch) continue;
      size_t idx = (size_t)ch->ledcCh;
      stopFade(idx);
      clearPendingChannel(idx);
      queueChannelTarget(idx, true, driver_.maxDuty(), "cmd_turn_on_many", false);
    }
    return;
  }

  if (cmd == "FADE_IN" || cmd == "FADE_TO") {
    JsonArray arr = doc["lights"].as<JsonArray>();
    if (arr.isNull()) arr = doc["LIGHTS"].as<JsonArray>();
    if (arr.isNull()) {
      log("WRN", String(cmd) + " requires lights[] payload=" + payload);
      return;
    }

    cancelBulkCommand();
    int pct = pickInt("pct", "PCT", 100);
    if (pct < 0) pct = 0;
    if (pct > 100) pct = 100;

    const uint32_t targetDuty = percentToDuty((uint32_t)pct);
    const uint32_t durationMs = (uint32_t)pickInt("duration_ms", "DURATION_MS", 1000);

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
      markDirty(idx, cmd == "FADE_IN" ? "cmd_fade_in_start" : "cmd_fade_to_start");
    }
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

  cancelBulkCommand();
  const size_t idx = (size_t)ch->ledcCh;
  stopFade(idx);
  clearPendingChannel(idx);
  queueChannelTarget(idx, on, duty, "mosfet_cmd", true);
}
