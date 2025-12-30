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
  // and calibrate a fixed baseline once at boot.
  for (int i = 0; i < 4; i++) {
    // 11dB extends measurable voltage range (Arduino-ESP32).
    analogSetPinAttenuation(kMicPins[i], ADC_11db);
  }
  calibrateBases();
  lastMetricMs_ = millis();
}

void CandlesRiddle::calibrateBases() {
  // One-time calibration at boot: sample each mic for a short window and
  // set a fixed DC baseline. This is NOT adaptive: it never changes again
  // until reboot.
  //
  // This prevents constant false triggers when mic modules output a biased
  // analog DC level (often mid-rail) or when ADC ranges differ per channel.
  const int samples = 300;   // ~600ms per channel with delay(2)
  const int delayMs = 2;

  uint16_t calAvg[4] = {0, 0, 0, 0};
  uint16_t calMax[4] = {0, 0, 0, 0};
  uint16_t calMin[4] = {4095, 4095, 4095, 4095};

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
    base_[i] = int(avg);
    calAvg[i] = avg;
    calMax[i] = mx;
    calMin[i] = mn;
  }

  // Log calibration results once. DBG so you can enable via <node>/log/level.
  if (ctx_) {
    String payload;
    payload.reserve(220);
    payload = String("{\"samples\":") + samples + ",\"delay_ms\":" + delayMs + ",\"b\":[" +
              calAvg[0] + "," + calAvg[1] + "," + calAvg[2] + "," + calAvg[3] +
              "],\"min\":[" + calMin[0] + "," + calMin[1] + "," + calMin[2] + "," + calMin[3] +
              "],\"max\":[" + calMax[0] + "," + calMax[1] + "," + calMax[2] + "," + calMax[3] +
              "]}";
    ctx_->log("DBG", "candles_cal", payload);

    // Warn if any channel is likely floating or saturated.
    for (int i = 0; i < 4; i++) {
      if (calAvg[i] <= 50 || calAvg[i] >= 4045 || calMax[i] >= 4090) {
        String w = String("{\"i\":") + i +
                   ",\"avg\":" + calAvg[i] +
                   ",\"min\":" + calMin[i] +
                   ",\"max\":" + calMax[i] +
                   "}";
        ctx_->log("WRN", "candles_adc_suspect", w);
      }
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

void CandlesRiddle::logErr(const String& msg, const String& dataJson) {
  errorCount_++;
  log("ERR", msg, dataJson);
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
  const uint16_t baseFixed = uint16_t(base_[idx]);

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

    if (abs(v - int(baseFixed)) > delta_[idx]) over++;
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
                     ",\"base\":" + baseFixed +
                     ",\"thr\":" + delta_[idx] +
                     ",\"over\":" + over +
                     ",\"need\":" + needed +
                     ",\"m_avg\":" + metrics_[idx].avg +
                     ",\"m_base\":" + metrics_[idx].base +
                     ",\"m_max\":" + metrics_[idx].maxVal +
                     "}";
    if (ctx_) ctx_->log("DBG", "candles_blow", payload);  }
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
  ctx_->publish(topicEvent_.c_str(), payload);
  ctx_->publish(topicCmdStarSky_.c_str(), "CANDLES_SOLVED");
  ctx_->publish(topicCmdLighting_.c_str(), "CANDLES_SOLVED");
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
               ",\"base\":" + base_[i] +
               ",\"thr\":" + delta_[i] +
               "}";
  }
  payload += "]}";

  // Emit through the standard logger too. This is the DBG path controlled by
  // MQTT topic `<node>/log/level`.
  // If DBG isn't enabled, this will be filtered out; the direct publish below
  // still guarantees visibility at least once per second as INF.
  ctx_->log("DBG", "candles_mics", payload);

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
    ctx_->publish(topic.c_str(), env, false);
  }

  // Reset accumulation for the next interval.
  for (int i = 0; i < 4; i++) {
    MicMetric& mm = metrics_[i];
    mm.sum = 0;
    mm.samples = 0;
    mm.maxVal = 0;
  }
}