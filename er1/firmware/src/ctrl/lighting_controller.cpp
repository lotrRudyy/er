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

bool payloadContainsSolvedId(const String& payload, const char* rid) {
  if (!rid || !rid[0]) return false;
  if (payload.indexOf("\"type\":\"riddle_solved\"") == -1) return false;
  const String needle = String("\"id\":\"") + rid + "\"";
  if (payload.indexOf(needle) >= 0) return true;
  const String dNeedle = String("\"d\":{\"id\":\"") + rid + "\"";
  return payload.indexOf(dNeedle) >= 0;
}

} // namespace

LightingController::LightingController() {
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

  for (size_t i = 0; i < kChannelCount; i++) {
    applyOutput(channels_[i]);
  }

  bootStatePublished_ = false;
  lastMqttConnected_ = false;
  lastMetricMs_ = millis();
  inGame_ = false;
  pianoSolvedSeen_ = false;
  chessSolvedSeen_ = false;
  candlesSolvedSeen_ = false;

  pianoTorchPending_ = false;
  pianoTorchDueMs_ = 0;

  chessRoomPending_ = false;
  chessRoomDueMs_ = 0;

  resetFade(pianoFade_);
  resetFade(chessFade_);
  resetFade(candlesFade_);
}

void LightingController::tick(uint32_t nowMs) {
  const bool conn = mqttConnected();
  if (conn != lastMqttConnected_) {
    SDBG("MQTT connected=%d", (int)conn);
    lastMqttConnected_ = conn;
  }

  if (conn && !bootStatePublished_) {
    SDBG("publishing retained boot state (all channels)");
    publishAllStates("boot");
    bootStatePublished_ = true;
  }

  if (pianoTorchPending_ && (nowMs - pianoTorchDueMs_ >= kProgressDelayMs)) {
    pianoTorchPending_ = false;
    runPianoTorch("piano_delay");
  }

  if (chessRoomPending_ && (nowMs - chessRoomDueMs_ >= kProgressDelayMs)) {
    chessRoomPending_ = false;
    runChessRoom("chess_delay");
  }

  updateFadePair(pianoFade_, nowMs);
  updateFadePair(chessFade_, nowMs);
  updateFadePair(candlesFade_, nowMs);
}

bool LightingController::onCmd(const char* cmd, const char* payload) {
  if (!ctx_) return false;
  String cmdUp = upperTrim(String(cmd ? cmd : ""));

  if (cmdUp == "CANDLES_SOLVED") {
    handleProgressEvent("candles");
    return true;
  }

  String msg(cmd ? cmd : "");
  if (payload && payload[0]) {
    msg += " ";
    msg += payload;
  }
  SDBG("node cmd (ignored): %s", msg.c_str());
  log("WRN", String("Unknown node CMD: ") + msg);
  return true;
}

void LightingController::onGameModeMessage(const String& msg) {
  String trimmed = upperTrim(msg);
  const bool nextInGame = (trimmed == "INGAME");
  if (nextInGame) {
    inGame_ = true;
    pianoSolvedSeen_ = false;
    chessSolvedSeen_ = false;
    candlesSolvedSeen_ = false;

    pianoTorchPending_ = false;
    pianoTorchDueMs_ = 0;

    chessRoomPending_ = false;
    chessRoomDueMs_ = 0;

    resetFade(pianoFade_);
    resetFade(chessFade_);
    resetFade(candlesFade_);

    applySceneInitial("game_start");
    log("INF", "LIGHT_SCENE_INGAME");
    return;
  }

  if (inGame_) {
    log("INF", String("LIGHT_GAME_MODE ") + trimmed);
  }
  inGame_ = false;

  pianoTorchPending_ = false;
  pianoTorchDueMs_ = 0;

  chessRoomPending_ = false;
  chessRoomDueMs_ = 0;

  resetFade(pianoFade_);
  resetFade(chessFade_);
  resetFade(candlesFade_);

  bool changed[kChannelCount] = {};
  for (size_t i = 0; i < kChannelCount; i++) changed[i] = setChannel(channels_[i].id, false, 0);
  publishChangedStates(changed, "standby");
}

void LightingController::onEventTopic(const char* topic, const String& payload) {
  (void)topic;
  if (payloadContainsSolvedId(payload, "piano")) {
    handleProgressEvent("piano");
    return;
  }
  if (payloadContainsSolvedId(payload, "chess")) {
    handleProgressEvent("chess");
    return;
  }
  if (payloadContainsSolvedId(payload, "candles")) {
    handleProgressEvent("candles");
    return;
  }
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

bool LightingController::setChannel(const char* id, bool on, uint32_t duty, bool preserveZeroDutyWhenOn) {
  ChannelState* ch = findById(String(id ? id : ""));
  if (!ch) return false;

  duty = clampDuty(duty);
  if (on && duty == 0 && !preserveZeroDutyWhenOn) {
    duty = driver_.maxDuty();
  }

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
  for (size_t i = 0; i < kChannelCount; i++) {
    if (changed[i]) publishChannelState(channels_[i], reason);
  }
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
      dutyA = fade.fromDutyA +
              (uint32_t)(((uint64_t)(fade.toDutyA - fade.fromDutyA) * elapsed) / fade.durationMs);
    } else {
      dutyA = fade.fromDutyA -
              (uint32_t)(((uint64_t)(fade.fromDutyA - fade.toDutyA) * elapsed) / fade.durationMs);
    }

    if (fade.toDutyB >= fade.fromDutyB) {
      dutyB = fade.fromDutyB +
              (uint32_t)(((uint64_t)(fade.toDutyB - fade.fromDutyB) * elapsed) / fade.durationMs);
    } else {
      dutyB = fade.fromDutyB -
              (uint32_t)(((uint64_t)(fade.fromDutyB - fade.toDutyB) * elapsed) / fade.durationMs);
    }
  } else {
    fade.active = false;
  }

  bool changed[kChannelCount] = {};
  ChannelState* chA = findById(String(fade.idA));
  ChannelState* chB = findById(String(fade.idB));

  if (chA) changed[(size_t)chA->ledcCh] = setChannel(fade.idA, true, dutyA, true);
  if (chB) changed[(size_t)chB->ledcCh] = setChannel(fade.idB, true, dutyB, true);

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
  publishChangedStates(changed, reason);
}

void LightingController::runPianoTorch(const char* reason) {
  bool changed[kChannelCount] = {};
  changed[8] = setChannel("9", true, driver_.maxDuty());
  publishChangedStates(changed, reason);
}

void LightingController::runChessRoom(const char* reason) {
  bool changed[kChannelCount] = {};

  // Force both channels on at 0 first so the fade starts from fully dark.
  changed[4] = setChannel("5", true, 0, true);
  changed[5] = setChannel("6", true, 0, true);
  publishChangedStates(changed, reason);

  startFadePair(
    chessFade_,
    "5", "6",
    0, 0,
    driver_.maxDuty(), driver_.maxDuty(),
    kFadeMs,
    "chess_fade",
    "chess_fade_done"
  );
}

void LightingController::handleProgressEvent(const char* rid) {
  if (!rid || !rid[0]) return;
  if (!inGame_) {
    log("DBG", String("Ignoring progress while not INGAME: ") + rid);
    return;
  }

  bool changed[kChannelCount] = {};

  if (strcmp(rid, "piano") == 0) {
    if (pianoSolvedSeen_) return;
    pianoSolvedSeen_ = true;

    // Pins 16 and 17 (channels 1 and 2) fade from 0 to 100 immediately.
    changed[0] = setChannel("1", true, 0, true);
    changed[1] = setChannel("2", true, 0, true);
    publishChangedStates(changed, "piano_solved");

    startFadePair(
      pianoFade_,
      "1", "2",
      0, 0,
      driver_.maxDuty(), driver_.maxDuty(),
      kFadeMs,
      "piano_fade",
      "piano_fade_done"
    );

    pianoTorchPending_ = true;
    pianoTorchDueMs_ = millis();   // store start time

    log("INF", "LIGHT_PIANO_SOLVED");
    return;
  }

  if (strcmp(rid, "chess") == 0) {
    if (chessSolvedSeen_) return;
    chessSolvedSeen_ = true;

    changed[7] = setChannel("8", true, driver_.maxDuty());
    publishChangedStates(changed, "chess_solved");

    chessRoomPending_ = true;
    chessRoomDueMs_ = millis();

    log("INF", "LIGHT_CHESS_SOLVED");
    return;
  }

  if (strcmp(rid, "candles") == 0) {
    if (candlesSolvedSeen_) return;
    candlesSolvedSeen_ = true;

    ChannelState* ch5 = findById("5");
    ChannelState* ch6 = findById("6");

    const uint32_t fromDuty5 = ch5 ? (ch5->on ? ch5->duty : driver_.maxDuty()) : driver_.maxDuty();
    const uint32_t fromDuty6 = ch6 ? (ch6->on ? ch6->duty : driver_.maxDuty()) : driver_.maxDuty();

    changed[4] = setChannel("5", true, fromDuty5, true);
    changed[5] = setChannel("6", true, fromDuty6, true);
    publishChangedStates(changed, "candles_solved");

    startFadePair(
      candlesFade_,
      "5", "6",
      fromDuty5, fromDuty6,
      percentToDuty(30), percentToDuty(30),
      kFadeMs,
      "candles_fade",
      "candles_fade_done"
    );

    log("INF", "LIGHT_CANDLES_SOLVED");
    return;
  }
}

bool LightingController::publish(const char* topic, const String& payload, bool retained) const {
  if (!ctx_) return false;
  if (!mqttConnected()) return false;
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
        ch->on = true;
        ch->duty = mapUserValueToDuty(v);
        handled = true;
      } else if (doc["on"].is<bool>()) {
        ch->on = doc["on"].as<bool>();
        handled = true;
      }
    }
  }

  if (!handled) {
    String p = upperTrim(payload);
    if (p == "ON") {
      ch->on = true;
      handled = true;
    } else if (p == "OFF") {
      ch->on = false;
      handled = true;
    } else if (p == "DIM" || p == "DIMMED") {
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
