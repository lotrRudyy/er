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
      log("DBG", "candles_idle_cal", js);
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
        if (detectBlow(i)) {
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
    } else {
      publishSolvedEvent();
    }
  }
  publishMetricsIfDue(nowMs);
}

bool CandlesRiddle::onCmd(const char* cmd, const char* payload) {
  String message(cmd ? cmd : "");
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

void CandlesRiddle::log(const char* level, const String& msg) {
  if (!ctx_) return;
  ctx_->log(level, msg);
}

void CandlesRiddle::log(const char* level, const String& msg, const String& dataJson) {
  if (!ctx_) return;
  ctx_->log(level, msg, dataJson);
}

bool CandlesRiddle::publish(const char* topic, const char* type, uint32_t version, const String& dataJson,
                            const char* id, bool retained) {
  if (!ctx_) return false;
  return ctx_->publishEnvelope(topic, type, version, dataJson, id, retained);
}

bool CandlesRiddle::publish(const char* topic, const String& payload, bool retained) {
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
    metrics_[i].lastRaw = 0;    progress_[i] = -1;
  }
  progressed_ = 0;
  lastAction_ = millis();
  lastSeqActivityMs_ = 0;
  solved_ = false;
  solvedEventSent_ = false;
}

bool CandlesRiddle::detectBlow(int idx) {
  // Short window, but require a strong majority of samples over threshold.
  // This reduces false "blow" triggers from mic noise / ADC drift.
  const int samples = 80;
  const int needed = (samples * 3) / 5;  // 60%

  // Fixed baseline (no adaptive tracking)
    // Effective baseline: max(measured idle, configured minimum floor).
  // If the channel is saturated, effBase_[idx] is forced to 4095 and it will never trigger.
  const int baseEff = effBase_[idx];

  int over = 0;
  int maxWindow = 0;
  uint32_t sumWindow = 0;
  int lastRaw = 0;
  for (int k = 0; k < samples; k++) {
    int v = analogRead(kMicPins[idx]);

    sumWindow += uint32_t(v);
    if (v > maxWindow) maxWindow = v;
    lastRaw = v;

    // Metrics: collected continuously for debug/telemetry + baseline tracking.
    MicMetric& mm = metrics_[idx];
    mm.sum += uint32_t(v);
    mm.samples++;
    mm.lastRaw = uint16_t(v);
    if (uint16_t(v) > mm.maxVal) mm.maxVal = uint16_t(v);

    // Trigger condition (per your request):
    // 1) signal must be ABOVE the fixed base floor
    // 2) and it must exceed the delta threshold (120)
    // i.e. v > base + delta
        if (v > baseEff && (v - baseEff) > delta_[idx]) over++;
    delay(2);
  }
  uint16_t avgWindow = uint16_t(sumWindow / uint32_t(samples));
  lastRaw_[idx] = uint16_t(lastRaw);
  lastAvgWin_[idx] = avgWindow;
  lastMaxWin_[idx] = uint16_t(maxWindow);
  lastOver_[idx] = uint8_t(min(over, 255));
  lastNeeded_[idx] = uint8_t(needed);
  bool hit = over >= needed;
  lastHit_[idx] = hit ? 1 : 0;
  if (hit) {
    // Detailed trigger debug (always DBG; controllable via <node>/log/level).
    // Values are for the detection window that caused the trigger.
    String payload = String("{\"i\":") + idx +
                     ",\"raw\":" + lastRaw +
                     ",\"avg_win\":" + avgWindow +
                     ",\"max_win\":" + maxWindow +
                     ",\"base_min\":" + base_[idx] +
                     ",\"base_eff\":" + baseEff +
                     ",\"sat\":" + micSaturated_[idx] +
                     ",\"thr\":" + delta_[idx] +
                     ",\"over\":" + over +
                     ",\"need\":" + needed +
                     ",\"m_avg\":" + metrics_[idx].avg +
                     ",\"m_base\":" + metrics_[idx].base +
                     ",\"m_max\":" + metrics_[idx].maxVal +
                     "}";
    if (ctx_) {
      log("DBG", "candles_blow", payload);
    }
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
    solved_ = true;
    log("INF", "SEQUENCE OK -> SOLVED");
    publishSolvedEvent();
  } else {
    log("INF", "SEQUENCE FAIL -> reset");
    resetAll();
  }
}

void CandlesRiddle::evaluateSequenceIfDue(uint32_t nowMs) {
  if (solved_ || progressed_ == 0) return;
  if (nowMs - lastSeqActivityMs_ < kSeqTimeoutMs) return;
  evaluateSequence();
}

void CandlesRiddle::resetPuzzleState() {
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

  // Compute per-mic rolling stats (from samples gathered during detectBlow scans),
  // but keep the baseline fixed (base_[i]) as requested.
  for (int i = 0; i < 4; i++) {
    MicMetric& mm = metrics_[i];
    if (mm.samples > 0) mm.avg = mm.sum / mm.samples;
    else mm.avg = 0;
  }

  // One merged, readable log every second (even if no blow is detected).
  // Per your request, this must be emitted unconditionally for now.
  String payload;
  payload.reserve(320);
  payload = String("{\"k\":\"candles\",\"en\":") + (ctx_->enabled() ? "1" : "0") +
            ",\"sol\":" + (solved_ ? "1" : "0") +
            ",\"prog\":" + progressed_ +
            ",\"seq_to_ms\":" + kSeqTimeoutMs +
            ",\"m\":[";
  for (int i = 0; i < 4; i++) {
    MicMetric& mm = metrics_[i];
    if (i) payload += ",";
    payload += String("{\"i\":") + i +
               ",\"lit\":" + (lit_[i] ? "1" : "0") +
               ",\"raw\":" + lastRaw_[i] +
               ",\"avg_win\":" + lastAvgWin_[i] +
               ",\"max_win\":" + lastMaxWin_[i] +
               ",\"over\":" + lastOver_[i] +
               ",\"need\":" + lastNeeded_[i] +
               ",\"hit\":" + lastHit_[i] +
               ",\"avg\":" + mm.avg +
               ",\"max\":" + mm.maxVal +
               ",\"base_min\":" + base_[i] +
               ",\"base_eff\":" + effBase_[i] +
               ",\"sat\":" + micSaturated_[i] +
               ",\"thr\":" + delta_[i] +
               "}";
  }
  payload += "]}";

  // Emit through the standard logger too. This is the DBG path controlled by
  // MQTT topic `<node>/log/level`.
  // If DBG isn't enabled, this will be filtered out; the direct publish below
  // still guarantees visibility at least once per second as INF.
  log("DBG", "candles_mics", payload);

  // Bypass log-level filtering by publishing directly to the log topic.
  const auto& topic = ctx_->config().topics.log;
  if (topic.length() > 0) {
    Core::TimestampSource* tsSrc = ctx_->timestampSource();
    Core::TimestampFields ts{};
    if (tsSrc) ts = tsSrc->currentTimestamp();

    String env;
    env.reserve(80 + payload.length());
    env = String("{\"t\":") + String((int64_t)ts.epoch) +
          ",\"ts\":\"" + String(ts.ts) + "\"," +
          "\"time_valid\":" + (ts.timeValid ? "true" : "false") +
          ",\"lv\":\"INF\",\"msg\":\"candles_mics\",\"d_type\":\"object\",\"d\":" +
          payload + "}";
    publish(topic.c_str(), env, false);
  }

  // Reset accumulation for the next interval.
  for (int i = 0; i < 4; i++) {
    MicMetric& mm = metrics_[i];
    mm.sum = 0;
    mm.samples = 0;
    mm.maxVal = 0;
  }
}
