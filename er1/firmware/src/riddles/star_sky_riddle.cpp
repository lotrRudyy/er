#include "star_sky_riddle.h"
#include <cstring>

namespace {
constexpr uint32_t kLedcFreq = 1000;
constexpr uint8_t kLedcRes = 8;
constexpr const char* kPrefsKey = "candles";
}

void StarSkyRiddle::begin(Core::NodeContext& ctx) {
  ctx_ = &ctx;
  const char* node = ctx.nodeId() ? ctx.nodeId() : "star_sky";
  // Metrics go via core_log (DBG) so <node>/log/level controls verbosity.
  loadState();
  for (int i = 0; i < 4; i++) {
    ledcSetup(i, kLedcFreq, kLedcRes);
    ledcAttachPin(kLedPins[i], i);
    setStripRaw(i, 0);
  }
  cycleStartMs_ = millis();
  lastMetricMs_ = millis();
  publishState();
}

void StarSkyRiddle::tick(uint32_t nowMs) {
  if (!ctx_) return;
  if (ctx_->enabled()) {
    applyPattern(nowMs);
  } else {
    setAllStripsOff();
  }
  publishMetricsIfDue(nowMs);
}

bool StarSkyRiddle::onCmd(const char* cmd, const char* payload) {
  if (cmd && strcasecmp(cmd, "RESET_STAR_SKY") == 0) {
    bool wasSolved = candlesSolved_;
    candlesSolved_ = false;
    persistState();
    cycleStartMs_ = millis();
    String data = String("{\"src\":\"reset_star_sky\",\"was_on\":") + (wasSolved ? "1" : "0") + "}";
    log("INF", "STAR_SKY_STATE_RESET", data);
    publishState();
    return true;
  }

  if (cmd && String(cmd).equalsIgnoreCase("CANDLES_SOLVED")) {
    if (!candlesSolved_) {
      candlesSolved_ = true;
      persistState();
      cycleStartMs_ = millis();
      log("INF", "CANDLES_SOLVED CMD received, enabling pattern");
      publishState();
    } else {
      log("DBG", "CANDLES_SOLVED CMD (already enabled)");
    }
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

void StarSkyRiddle::handleCandlesEvent(const String& payload) {
  if (payload.indexOf("\"type\"") == -1 || payload.indexOf("\"riddle_solved\"") == -1) {
    return;
  }
  if (payload.indexOf("\"id\":\"candles\"") == -1) {
    return;
  }
  if (!candlesSolved_) {
    candlesSolved_ = true;
    persistState();
    cycleStartMs_ = millis();
    log("INF", "Candles SOLVED event received, enabling pattern");
    publishState();
  } else {
    log("DBG", "Candles SOLVED event (already enabled)");
  }
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
  if (!candlesSolved_) {
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
  const char* phase = (!candlesSolved_) ? "OFF" :
                      (delta < kStepMs) ? "SET2" :
                      (delta < kStepMs * 2) ? "SET3" :
                      (delta < kStepMs * 3) ? "SET4" : "PAUSE";

  String payload = String("{\"fw\":\"") + ctx_->fwVersion() +
                   "\",\"up\":" + String(nowMs / 1000) +
                   ",\"k\":\"star_sky\"" +
                   ",\"candles\":" + (candlesSolved_ ? "1" : "0") +
                   ",\"phase\":\"" + phase + "\"" +
                   "}";
  log("DBG", "star_sky_metrics", payload);
}

void StarSkyRiddle::persistState() {
  prefs_.putBool(kPrefsKey, candlesSolved_);
  log("INF", String("STATE save candles=") + (candlesSolved_ ? "1" : "0"));
}

void StarSkyRiddle::loadState() {
  prefs_.begin("star_sky", false);
  bool hasKey = prefs_.isKey(kPrefsKey);
  candlesSolved_ = prefs_.getBool(kPrefsKey, false);
  if (hasKey) {
    log("INF", String("STATE restore candles=") + (candlesSolved_ ? "1" : "0"));
  } else {
    log("INF", "STATE default candles=0");
    prefs_.putBool(kPrefsKey, candlesSolved_);
  }
  cycleStartMs_ = millis();
}

void StarSkyRiddle::publishState() {
  if (!ctx_) return;
  String data = String("{\"candles\":") + (candlesSolved_ ? "true" : "false") + "}";
  const auto& topics = ctx_->config().topics;
  if (topics.state.length() > 0) {
    publish(topics.state.c_str(), "state", 1, data, nullptr, true);
  }
}
