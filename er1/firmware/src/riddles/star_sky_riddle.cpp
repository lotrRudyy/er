#include "star_sky_riddle.h"
#include <cstring>
#include <strings.h>

namespace {
constexpr uint32_t kLedcFreq = 1000;
constexpr uint8_t kLedcRes = 8;
}

void StarSkyRiddle::begin(Core::NodeContext& ctx) {
  ctx_ = &ctx;
  const char* node = ctx.nodeId() ? ctx.nodeId() : "star_sky";
  (void)node;
  // Metrics go via core_log (DBG) so <node>/log/level controls verbosity.
  for (int i = 0; i < 4; i++) {
    ledcSetup(i, kLedcFreq, kLedcRes);
    ledcAttachPin(kLedPins[i], i);
    setStripRaw(i, 0);
  }
  cycleStartMs_ = millis();
  lastMetricMs_ = millis();
  gameActive_ = false;
  moduleEnabled_ = true;
  setAllStripsOff();
  publishState();
}

void StarSkyRiddle::setGameMode(bool inGame) {
  if (manualOverride_ && !inGame) {
    publishState();
    return;
  }
  gameActive_ = inGame;
  if (inGame) {
    moduleEnabled_ = true;
  }
  cycleStartMs_ = millis();
  setAllStripsOff();
  publishState();
}

void StarSkyRiddle::setManualOverride(bool enabled) {
  manualOverride_ = enabled;
  if (enabled) {
    gameActive_ = true;
    moduleEnabled_ = true;
    cycleStartMs_ = millis();
  } else if (!gameActive_) {
    setAllStripsOff();
  }
  publishState();
}

void StarSkyRiddle::tick(uint32_t nowMs) {
  if (!ctx_) return;
  if (gameActive_ && moduleEnabled_ && ctx_->enabled()) {
    applyPattern(nowMs);
  } else {
    setAllStripsOff();
  }
  publishMetricsIfDue(nowMs);
}

bool StarSkyRiddle::onCmd(const char* cmd, const char* payload) {
  if (!cmd) return false;

  if (strcasecmp(cmd, "RESET_STAR_SKY") == 0 || strcasecmp(cmd, "RESET") == 0) {
    cycleStartMs_ = millis();
    setAllStripsOff();
    log("INF", "STAR_SKY_RESET");
    publishState();
    return true;
  }

  if (strcasecmp(cmd, "SOLVE") == 0 || strcasecmp(cmd, "SOLVE_STAR_SKY") == 0 ||
      strcasecmp(cmd, "ON") == 0) {
    manualOverride_ = true;
    gameActive_ = true;
    moduleEnabled_ = true;
    cycleStartMs_ = millis();
    log("INF", "STAR_SKY_FORCE_ENABLE");
    publishState();
    return true;
  }

  if (strcasecmp(cmd, "ENABLE") == 0) {
    manualOverride_ = true;
    moduleEnabled_ = true;
    gameActive_ = true;
    cycleStartMs_ = millis();
    publishState();
    return true;
  }

  if (strcasecmp(cmd, "DISABLE") == 0 || strcasecmp(cmd, "OFF") == 0) {
    manualOverride_ = false;
    moduleEnabled_ = false;
    gameActive_ = false;
    setAllStripsOff();
    publishState();
    return true;
  }

  if (strcasecmp(cmd, "STATUS") == 0) {
    publishState();
    return true;
  }

  if (String(cmd).equalsIgnoreCase("CANDLES_SOLVED")) {
    if (!gameActive_) setGameMode(true);
    cycleStartMs_ = millis();
    log("INF", "CANDLES_SOLVED CMD received");
    publishState();
    return true;
  }

  String message(cmd ? cmd : "");
  if (payload && payload[0]) {
    message += " ";
    message += payload;
  }
  log("WRN", String("Unknown CMD: ") + message);
  return true;
}

void StarSkyRiddle::log(const char* level, const String& msg) const {
  if (!ctx_) return;
  ctx_->log(level, msg);
}

void StarSkyRiddle::log(const char* level, const String& msg, const String& dataJson) const {
  if (!ctx_) return;
  ctx_->log(level, msg, dataJson);
}

bool StarSkyRiddle::publish(const char* topic, const char* type, uint32_t version, const String& dataJson,
                            const char* id, bool retained) const {
  if (!ctx_) return false;
  return ctx_->publishEnvelope(topic, type, version, dataJson, id, retained);
}

bool StarSkyRiddle::publish(const char* topic, const String& payload, bool retained) const {
  if (!ctx_) return false;
  return ctx_->publish(topic, payload, retained);
}

void StarSkyRiddle::applyPattern(uint32_t nowMs) {
  if (!gameActive_) {
    setAllStripsOff();
    return;
  }

  uint32_t delta = (nowMs - cycleStartMs_) % kCycleMs;
  setStripRaw(0, 255);
  setStripRaw(1, 0);
  setStripRaw(2, 0);
  setStripRaw(3, 0);

  if (delta < kStepMs) {
    setStripRaw(1, 255);
  } else if (delta < kStepMs * 2) {
    setStripRaw(2, 255);
  } else if (delta < kStepMs * 3) {
    setStripRaw(3, 255);
  }
}

void StarSkyRiddle::setStripRaw(int idx, uint8_t duty) {
  if (idx < 0 || idx >= 4) return;
  ledcWrite(idx, duty);
}

void StarSkyRiddle::setAllStripsOff() {
  for (int i = 0; i < 4; i++) setStripRaw(i, 0);
}

void StarSkyRiddle::publishMetricsIfDue(uint32_t nowMs) {
  if (!ctx_) return;
  if (nowMs - lastMetricMs_ < kMetricIntervalMs) return;
  lastMetricMs_ = nowMs;

  uint32_t delta = (nowMs - cycleStartMs_) % kCycleMs;
  const char* phase = (!gameActive_) ? "OFF" :
                      (delta < kStepMs) ? "SET2" :
                      (delta < kStepMs * 2) ? "SET3" :
                      (delta < kStepMs * 3) ? "SET4" : "PAUSE";

  String payload = String("{\"fw\":\"") + ctx_->fwVersion() +
                   "\",\"up\":" + String(nowMs / 1000) +
                   ",\"k\":\"star_sky\"" +
                   ",\"candles\":0" +
                   ",\"phase\":\"" + phase + "\"" +
                   "}";
  log("DBG", "star_sky_metrics", payload);
}

void StarSkyRiddle::publishState() {
  if (!ctx_) return;

  const bool effectiveEnabled = gameActive_ && moduleEnabled_ && ctx_->enabled();
  const char* rawState = gameActive_ ? "active" : "idle";

  String data =
      String("{\"id\":\"star_sky\"") +
      ",\"mode\":\"" + (gameActive_ ? "ingame" : "standby") + "\"" +
      ",\"enabled\":" + (effectiveEnabled ? "true" : "false") +
      ",\"solved\":false" +
      ",\"raw_state\":\"" + rawState + "\"" +
      ",\"candles\":false" +
      "}";

  const auto& topics = ctx_->config().topics;
  if (topics.state.length() > 0) {
    publish(topics.state.c_str(), "state", 1, data, nullptr, true);
  }
}
