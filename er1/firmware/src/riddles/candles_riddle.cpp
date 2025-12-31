#include "candles_riddle.h"

void CandlesRiddle::begin(Core::NodeContext& ctx) {
  ctx_ = &ctx;
  const char* node = ctx.nodeId() ? ctx.nodeId() : "candles";
  topicEvent_ = Core::topic(node, "evt");
  // Metrics go via core_log (DBG) so <node>/log/level controls verbosity.
  topicCmdStarSky_ = Core::topic("star_sky", "cmd");
  topicCmdLighting_ = Core::topic("lighting", "cmd");
  for (int i = 0; i < 4; i++) {
    pinMode(kLedPins[i], OUTPUT);
    setLed(i, true);
  }
  initState();
  // Configure ADC range for the microphone pins (helps prevent saturation)
  // and take a one-time idle probe (for logs only).
  for (int i = 0; i < 4; i++) {
    // 11dB extends measurable voltage range (Arduino-ESP32).
    analogSetPinAttenuation(kMicPins[i], ADC_11db);
  }
  // Many mic boards have a bias network that takes a moment to settle.
  // If we sample immediately at boot, the readings can be very low and then
  // jump later (e.g. ~300 -> ~3000). We therefore wait briefly, then only
  // *log* the idle level. The detection baseline itself stays fixed to the
  // configured base_[].
  delay(1500);
  calibrateBases();
  lastMetricMs_ = millis();
}

void CandlesRiddle::calibrateBases() {
  // One-time idle calibration at boot.
  //
  // IMPORTANT:
  // - We do NOT use an adaptive baseline.
  // - We DO measure the idle DC level once at boot, because many mic boards
  //   have a mid-rail bias that can drift during power-up (e.g. ~300 -> ~3000).
  //
  // Your provided base_[i] values are treated as a *minimum floor* (kBaseMin[i]).
  // The effective baseline used for detection is:
  //   effBase_[i] = max(kBaseMin[i], measured_idle_avg)
  //
  // If a channel appears saturated (near 4095), we mark it saturated and set
  // effBase_ to 4095 so it cannot trigger "blow" until hardware is fixed.
  const int samples = 300;   // ~600ms per channel with delay(2)
  const int delayMs = 2;

  for (int i = 0; i < 4; i++) {
    uint32_t sum = 0;
    uint16_t mx = 0;
    uint16_t mn = 4095;
    for (int k = 0; k < samples; k++) {
      uint16_t v = uint16_t(analogRead(kMicPins[i]));
      sum += v;
      if (v > mx) mx = v;
      if (v < mn) mn = v;
      delay(delayMs);
    }

    uint16_t avg = uint16_t(sum / uint32_t(samples));
    metrics_[i].base = avg;  // informational only (for logs)

    // Determine effective base
    micSaturated_[i] = 0;
    if (avg > 4050 || mx > 4090) {
      micSaturated_[i] = 1;
      effBase_[i] = 4095;
    } else {
      int eff = int(avg);
      if (eff < kBaseMin[i]) eff = kBaseMin[i];
      effBase_[i] = eff;
    }

    if (ctx_) {
      String js;
      js.reserve(120);
      js = String("{\"i\":") + i +
           ",\"avg\":" + avg +
           ",\"min\":" + mn +
           ",\"max\":" + mx +
           ",\"base_min\":" + kBaseMin[i] +
           ",\"eff_base\":" + effBase_[i] +
           ",\"sat\":" + micSaturated_[i] +
           "}";
      log("INF", "candles_idle_cal", js);
      if (micSaturated_[i]) log("WRN", "candles_adc_saturated", js);
    }
  }
}

void CandlesRiddle::tick(uint32_t nowMs) {
  if (!ctx_) return;

  if (ctx_->enabled()) {
    if (!solved_) {
      for (int i = 0; i < 4; i++) {
        if (!lit_[i]) continue;

        // --- NEW FLOW ---
        // Only call detectBlow() AFTER we saw threshold exceeded once.
        // Threshold is per-mic: effBase + delta (delta is your 120).
        const int thrAbs = effBase_[i] + delta_[i];
        const int v0 = analogRead(kMicPins[i]);
        // Live telemetry (so candles_mic always shows current values even before first blow)
        lastRaw_[i] = (uint16_t)v0;
        lastAvgWin_[i] = (uint16_t)v0;
        lastMaxWin_[i] = (uint16_t)v0;
        lastOver_[i] = 0;
        lastNeeded_[i] = 0;
        lastHit_[i] = 0;

        // Keep rolling metrics meaningful even when no blow windows run
        MicMetric& mmLive = metrics_[i];
        mmLive.sum += (uint32_t)v0;
        mmLive.samples++;
        mmLive.lastRaw = (uint16_t)v0;
        if ((uint16_t)v0 > mmLive.maxVal) mmLive.maxVal = (uint16_t)v0;

        if (v0 > thrAbs && detectBlow(i, thrAbs)) {
          lastAction_ = nowMs;
          lastSeqActivityMs_ = nowMs;
          setLed(i, false);
          if (progressed_ < 4) {
            progress_[progressed_] = i;
            progressed_++;
            if (kDevLog) {
              String data = String("{\"idx\":") + i + ",\"step\":" + progressed_ + "}";
              log("INF", "BLOW", data);
            }
          }
          if (progressed_ >= 4) {
            evaluateSequence();
          }
        }
      }
      evaluateSequenceIfDue(nowMs);
      // If we have a delayed reset armed (wrong full sequence), execute it after the same
      // timeout used for sequence inactivity.
      if (resetArmed_ && (nowMs - resetArmMs_ >= kSeqTimeoutMs)) {
        resetArmed_ = false;
        log("INF", "SEQUENCE FAIL (delayed) -> reset");
        resetAll();
      }
    } else {
      publishSolvedEvent();
    }
  }
  publishMetricsIfDue(nowMs);
}

bool CandlesRiddle::onCmd(const char* cmd, const char* payload) {
  if (!cmd) return false;

  // NOTE: Core normalizes commands to uppercase before calling onCmd().
  // Keep comparisons uppercase.
  if (strcmp(cmd, "RESET") == 0 || strcmp(cmd, "RELIGHT") == 0) {
    resetAll();
    log("INF", "CMD RESET -> relight+reset");
    return true;
  }

  if (strcmp(cmd, "CAL") == 0 || strcmp(cmd, "CALIB") == 0 || strcmp(cmd, "CALIBRATE") == 0) {
    calibrateBases();
    log("INF", "CMD CALIBRATE -> recalibrated idle baselines");
    return true;
  }

  // Keep legacy behavior: log unknown commands as WRN but consume them.
  String message(cmd);
  if (payload && payload[0]) {
    message += " ";
    message += payload;
  }
  log("WRN", String("Unknown CMD: ") + message);
  return true;
}

bool CandlesRiddle::shouldAllowLog(const char* level) {
  bool isErr = (strcmp(level, "ERR") == 0);
  if (isErr) {
    errorCount_++;
    return true;
  }
  // Do not hard-disable logs here.
  // The core already controls verbosity via runtime MQTT log level
  // (<node>/log/level). If we return false here, DBG/INF logs will never
  // be published regardless of the configured log level.
  return true;
}

void CandlesRiddle::log(const char* level, const String& msg) const {
  if (!ctx_) return;
  ctx_->log(level, msg);
}

void CandlesRiddle::log(const char* level, const String& msg, const String& dataJson) const {
  if (!ctx_) return;
  ctx_->log(level, msg, dataJson);
}

bool CandlesRiddle::publish(const char* topic, const char* type, uint32_t version, const String& dataJson,
                            const char* id, bool retained) const {
  if (!ctx_) return false;
  return ctx_->publishEnvelope(topic, type, version, dataJson, id, retained);
}

bool CandlesRiddle::publish(const char* topic, const String& payload, bool retained) const {
  if (!ctx_) return false;
  return ctx_->publish(topic, payload, retained);
}

void CandlesRiddle::setLed(int idx, bool on) {
  digitalWrite(kLedPins[idx], on ? HIGH : LOW);
  lit_[idx] = on;
}

void CandlesRiddle::initState() {
  for (int i = 0; i < 4; i++) {
    metrics_[i].sum = 0;
    metrics_[i].samples = 0;
    metrics_[i].avg = 0;
    metrics_[i].base = 0;
    metrics_[i].maxVal = 0;
    metrics_[i].lastRaw = 0;
    progress_[i] = -1;
  }
  progressed_ = 0;
  lastAction_ = millis();
  lastSeqActivityMs_ = 0;
  solved_ = false;
  solvedEventSent_ = false;
}

/**
 * Called only after we saw one sample exceed thrAbs in tick().
 * Now grab the next 50 samples (no delay). If >=60% are above thrAbs -> hit.
 */
bool CandlesRiddle::detectBlow(int idx, int thrAbs) {
  static uint32_t lastTrig[4] = {0, 0, 0, 0};
  static constexpr uint32_t kRefractMs = 250;

  uint32_t now = millis();
  if (now - lastTrig[idx] < kRefractMs) return false;

  static constexpr int samples = 50;
  static constexpr int needed = (samples * 60 + 99) / 100;  // ceil(0.60*samples) => 30

  int over = 0;
  int minV = 4096;
  int maxV = 0;
  uint32_t sum = 0;
  int lastRaw = 0;

  for (int k = 0; k < samples; k++) {
    int v = analogRead(kMicPins[idx]);
    lastRaw = v;
    sum += (uint32_t)v;
    if (v < minV) minV = v;
    if (v > maxV) maxV = v;
    if (v > thrAbs) over++;

    // Accumulate metrics so publishMetricsIfDue() remains meaningful.
    MicMetric& mm = metrics_[idx];
    mm.sum += (uint32_t)v;
    mm.samples++;
    mm.lastRaw = (uint16_t)v;
    if ((uint16_t)v > mm.maxVal) mm.maxVal = (uint16_t)v;
  }

  bool hit = (over >= needed);
  if (hit) lastTrig[idx] = now;

  // Save last window stats for your existing per-mic telemetry
  lastRaw_[idx] = (uint16_t)lastRaw;
  lastAvgWin_[idx] = (uint16_t)(sum / (uint32_t)samples);
  lastMaxWin_[idx] = (uint16_t)maxV;
  lastOver_[idx] = (uint8_t)min(over, 255);
  lastNeeded_[idx] = (uint8_t)needed;
  lastHit_[idx] = hit ? 1 : 0;

  // Log only on hit to avoid spam (you still get 10s telemetry in candles_mic)
  if (hit) {
    const int baseEff = effBase_[idx];
    String payload = String("{\"i\":") + idx +
                     ",\"base_eff\":" + baseEff +
                     ",\"delta\":" + delta_[idx] +
                     ",\"thr_abs\":" + thrAbs +
                     ",\"samples\":" + samples +
                     ",\"needed\":" + needed +
                     ",\"over\":" + over +
                     ",\"avg\":" + lastAvgWin_[idx] +
                     ",\"min\":" + minV +
                     ",\"max\":" + maxV +
                     "}";
    log("INF", "candles_blow", payload);
  }

  return hit;
}

void CandlesRiddle::evaluateSequence() {
  if (progressed_ == 0) return;

  if (kDevLog) {
    String seqStr;
    for (int i = 0; i < progressed_; i++) {
      if (i > 0) seqStr += ",";
      seqStr += progress_[i];
    }
    log("INF", String("SEQ_EVAL len=") + progressed_ + " buf=[" + seqStr + "]");
  }

  bool ok = true;
  if (progressed_ != 4) {
    ok = false;
  } else {
    for (int i = 0; i < 4; i++) {
      if (progress_[i] != kOrder[i]) {
        ok = false;
        break;
      }
    }
  }

  if (ok) {
    resetArmed_ = false;
    solved_ = true;
    log("INF", "SEQUENCE OK -> SOLVED");
    publishSolvedEvent();
  } else {
    if (progressed_ >= 4) {
      // Full sequence entered but wrong: give players a moment before resetting,
      // matching the normal sequence-timeout wait.
      resetArmed_ = true;
      resetArmMs_ = millis();
      log("INF", "SEQUENCE FAIL (full) -> reset pending");
    } else {
      log("INF", "SEQUENCE FAIL -> reset");
      resetAll();
    }
  }
}

void CandlesRiddle::evaluateSequenceIfDue(uint32_t nowMs) {
  if (solved_ || progressed_ == 0) return;
  // If we've already collected a full 4-step sequence, evaluation happens immediately.
  // If a delayed reset is armed, don't re-evaluate.
  if (progressed_ >= 4 || resetArmed_) return;
  if (nowMs - lastSeqActivityMs_ < kSeqTimeoutMs) return;
  evaluateSequence();
}

void CandlesRiddle::resetPuzzleState() {
  resetArmed_ = false;
  for (int i = 0; i < 4; i++) {
    setLed(i, true);
    progress_[i] = -1;
  }
  progressed_ = 0;
  solved_ = false;
  solvedEventSent_ = false;
  lastAction_ = millis();
  lastSeqActivityMs_ = 0;
}

void CandlesRiddle::flickerRelight(int cycles, int onMs, int offMs) {
  for (int c = 0; c < cycles; c++) {
    for (int i = 0; i < 4; i++) setLed(i, true);
    delay(onMs);
    for (int i = 0; i < 4; i++) setLed(i, false);
    delay(offMs);
  }
  for (int i = 0; i < 4; i++) setLed(i, true);
}

void CandlesRiddle::resetAll() {
  resetArmed_ = false;
  flickerRelight();
  resetPuzzleState();
  log("INF", "Reset (relight all)");
}

void CandlesRiddle::publishSolvedEvent() {
  if (solvedEventSent_) return;
  if (!ctx_) return;
  String payload = "{\"event\":\"SOLVED\",\"rid\":\"candles\"}";
  publish(topicEvent_.c_str(), payload);
  publish(topicCmdStarSky_.c_str(), "CANDLES_SOLVED");
  publish(topicCmdLighting_.c_str(), "CANDLES_SOLVED");
  solvedEventSent_ = true;
}

void CandlesRiddle::publishMetricsIfDue(uint32_t nowMs) {
  if (!ctx_) return;
  if (nowMs - lastMetricMs_ < kMetricIntervalMs) return;
  lastMetricMs_ = nowMs;

  // Compute per-mic rolling stats (from samples gathered during detectBlow scans).
  for (int i = 0; i < 4; i++) {
    MicMetric& mm = metrics_[i];
    if (mm.samples > 0) mm.avg = mm.sum / mm.samples;
    else mm.avg = 0;
  }

  // IMPORTANT: PubSubClient has a small max packet size by default (often 256 bytes).
  // The previous "mics":[...] aggregate payload could exceed it and silently fail to publish.
  // Emit one compact message per mic so we always get 1Hz telemetry.
  const auto& topic = ctx_->config().topics.log;
  Core::TimestampSource* tsSrc = ctx_->timestampSource();

  for (int i = 0; i < 4; i++) {
    MicMetric& mm = metrics_[i];

    // Compact data object (keep keys short; avoid large arrays).
    String d;
    d.reserve(160);
    d = String("{\"i\":") + i +
        ",\"lit\":" + (lit_[i] ? "1" : "0") +
        ",\"raw\":" + lastRaw_[i] +
        ",\"aw\":" + lastAvgWin_[i] +
        ",\"mw\":" + lastMaxWin_[i] +
        ",\"ov\":" + lastOver_[i] +
        ",\"need\":" + lastNeeded_[i] +
        ",\"hit\":" + lastHit_[i] +
        ",\"avg\":" + mm.avg +
        ",\"max\":" + mm.maxVal +
        ",\"base\":" + effBase_[i] +
        ",\"sat\":" + micSaturated_[i] +
        ",\"thr\":" + delta_[i] +
        "}";

    // DBG path (subject to <node>/log/level).
    log("DBG", "candles_mic", d);
  }

  // Reset accumulation for the next interval.
  for (int i = 0; i < 4; i++) {
    MicMetric& mm = metrics_[i];
    mm.sum = 0;
    mm.samples = 0;
    mm.maxVal = 0;
  }
}
