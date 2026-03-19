#include "candles_riddle.h"

#include <cstring>
#include <strings.h>

void CandlesRiddle::begin(Core::NodeContext& ctx) {
  ctx_ = &ctx;
  const char* node = ctx.nodeId() ? ctx.nodeId() : "candles";
  topicEvent_ = Core::topic(node, "evt");
  topicCmdStarSky_ = Core::topic("star_sky", "cmd");
  topicCmdLighting_ = Core::topic("lighting", "cmd");
  for (int i = 0; i < 4; i++) {
    pinMode(kLedPins[i], OUTPUT);
    setLed(i, false);
  }
  initState();
  for (int i = 0; i < 4; i++) {
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
  gameActive_ = false;
  moduleEnabled_ = true;
  publishState();
}

void CandlesRiddle::setGameMode(bool inGame) {
  gameActive_ = inGame;
  if (inGame) {
    resetPuzzleState();
    setAllLeds(true);
  } else {
    resetPuzzleState();
    setAllLeds(false);
  }
  publishState();
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
    metrics_[i].base = avg;

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

  if (!gameActive_) {
    setAllLeds(false);
    publishMetricsIfDue(nowMs);
    return;
  }

  if (ctx_->enabled() && moduleEnabled_) {
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
            // IMPORTANT: pass nowMs, not millis(), to avoid unsigned underflow
            // when computing (nowMs - resetArmMs_) later in this tick.
            evaluateSequence(nowMs);
          }
        }
      }
      evaluateSequenceIfDue(nowMs);
      // If we have a delayed reset armed (wrong full sequence), execute it after the same
      // timeout used for sequence inactivity.
      if (resetArmed_ && (nowMs - resetArmMs_ >= 3000)) {
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

  if (strcasecmp(cmd, "RESET_CANDLES") == 0 || strcasecmp(cmd, "RESET") == 0 || strcasecmp(cmd, "RELIGHT") == 0) {
    bool wasSolved = solved_;
    resetAll();
    String data = String("{\"src\":\"reset_candles\",\"was_solved\":") + (wasSolved ? "1" : "0") + "}";
    log("INF", "CANDLES_STATE_RESET", data);
    publishState();
    return true;
  }

  if (strcasecmp(cmd, "SOLVE") == 0 || strcasecmp(cmd, "SOLVE_CANDLES") == 0) {
    solved_ = true;
    resetArmed_ = false;
    log("INF", "CANDLES_FORCE_SOLVE");
    publishSolvedEvent();
    publishState();
    return true;
  }

  if (strcasecmp(cmd, "ENABLE") == 0) {
    moduleEnabled_ = true;
    log("INF", "CANDLES_ENABLE");
    publishState();
    return true;
  }

  if (strcasecmp(cmd, "DISABLE") == 0) {
    moduleEnabled_ = false;
    log("INF", "CANDLES_DISABLE");
    publishState();
    return true;
  }

  if (strcasecmp(cmd, "STATUS") == 0) {
    publishState();
    return true;
  }

  if (strcasecmp(cmd, "CAL") == 0 || strcasecmp(cmd, "CALIB") == 0 || strcasecmp(cmd, "CALIBRATE") == 0) {
    calibrateBases();
    log("INF", "CMD CALIBRATE -> recalibrated idle baselines");
    return true;
  }

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

void CandlesRiddle::setAllLeds(bool on) {
  for (int i = 0; i < 4; i++) setLed(i, on);
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
  resetArmed_ = false;
  tries_ = 0;
  attemptedSequencesCount_ = 0;
  for (size_t i = 0; i < kAttemptHistoryMax; ++i) {
    attemptedSequences_[i][0] = '\0';
  }
}

bool CandlesRiddle::detectBlow(int idx, int thrAbs) {
  static uint32_t lastTrig[4] = {0, 0, 0, 0};
  static constexpr uint32_t kRefractMs = 250;

  uint32_t now = millis();
  if (now - lastTrig[idx] < kRefractMs) return false;

  static constexpr int samples = 50;
  static constexpr int needed = (samples * 60 + 99) / 100;

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

    MicMetric& mm = metrics_[idx];
    mm.sum += (uint32_t)v;
    mm.samples++;
    mm.lastRaw = (uint16_t)v;
    if ((uint16_t)v > mm.maxVal) mm.maxVal = (uint16_t)v;
  }

  bool hit = (over >= needed);
  if (hit) lastTrig[idx] = now;

  lastRaw_[idx] = (uint16_t)lastRaw;
  lastAvgWin_[idx] = (uint16_t)(sum / (uint32_t)samples);
  lastMaxWin_[idx] = (uint16_t)maxV;
  lastOver_[idx] = (uint8_t)min(over, 255);
  lastNeeded_[idx] = (uint8_t)needed;
  lastHit_[idx] = hit ? 1 : 0;

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

void CandlesRiddle::evaluateSequence(uint32_t nowMs) {
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
    tries_++;
    appendAttemptedSequence();
    if (progressed_ >= 4) {
      resetArmed_ = true;
      resetArmMs_ = nowMs;
      log("INF", String("SEQUENCE FAIL (full) -> reset pending seq=") + currentSequenceHyphen());
    } else {
      log("INF", String("SEQUENCE FAIL -> reset seq=") + currentSequenceHyphen());
      resetAll();
    }
  }
}

void CandlesRiddle::evaluateSequenceIfDue(uint32_t nowMs) {
  if (solved_ || progressed_ == 0) return;
  if (progressed_ >= 4 || resetArmed_) return;
  if (nowMs - lastSeqActivityMs_ < kSeqTimeoutMs) return;
  evaluateSequence(nowMs);
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
    setLed(0, true);
    setLed(2, true);
    setLed(1, false);
    setLed(3, false);
    delay(onMs);

    setLed(0, false);
    setLed(2, false);
    setLed(1, true);
    setLed(3, true);
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
  publish("game/event", "{\"node\":\"candles\",\"event\":\"solved\"}", false);
  solvedEventSent_ = true;
}

void CandlesRiddle::publishMetricsIfDue(uint32_t nowMs) {
  if (!ctx_) return;
  if (nowMs - lastMetricMs_ < kMetricIntervalMs) return;
  lastMetricMs_ = nowMs;

  for (int i = 0; i < 4; i++) {
    MicMetric& mm = metrics_[i];
    if (mm.samples > 0) mm.avg = mm.sum / mm.samples;
    else mm.avg = 0;
  }

  for (int i = 0; i < 4; i++) {
    MicMetric& mm = metrics_[i];

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
    log("DBG", "candles_mic", d);
  }

  for (int i = 0; i < 4; i++) {
    MicMetric& mm = metrics_[i];
    mm.sum = 0;
    mm.samples = 0;
    mm.maxVal = 0;
  }
}

void CandlesRiddle::appendAttemptedSequence() {
  if (attemptedSequencesCount_ >= kAttemptHistoryMax) return;
  String seq = currentSequenceHyphen();
  seq.toCharArray(attemptedSequences_[attemptedSequencesCount_], kAttemptStringMax);
  attemptedSequencesCount_++;
}

String CandlesRiddle::currentSequenceHyphen() const {
  String out;
  for (int i = 0; i < progressed_; i++) {
    if (i > 0) out += "-";
    out += String(progress_[i] + 1);
  }
  return out;
}

String CandlesRiddle::currentSequenceJson() const {
  String out = "[";
  for (int i = 0; i < progressed_; i++) {
    if (i > 0) out += ",";
    out += "\"";
    out += String(progress_[i] + 1);
    out += "\"";
  }
  out += "]";
  return out;
}

String CandlesRiddle::attemptedSequencesJson() const {
  String out = "[";
  for (size_t i = 0; i < attemptedSequencesCount_; i++) {
    if (i > 0) out += ",";
    out += "\"";
    out += attemptedSequences_[i];
    out += "\"";
  }
  out += "]";
  return out;
}

void CandlesRiddle::publishState() {
  if (!ctx_) return;
  const auto& topics = ctx_->config().topics;
  if (topics.state.length() == 0) return;

  String data;
  data.reserve(320);
  data = String("{\"id\":\"candles\"") +
         ",\"mode\":\"" + (gameActive_ ? "ingame" : "standby") + "\"" +
         ",\"enabled\":" + String((gameActive_ && moduleEnabled_ && ctx_->enabled()) ? "true" : "false") +
         ",\"solved\":" + String(solved_ ? "true" : "false") +
         ",\"raw_state\":\"" + (solved_ ? "solved" : (progressed_ > 0 ? "progress" : "idle")) + "\"" +
         ",\"tries\":" + String(tries_) +
         ",\"sequence_current\":" + currentSequenceJson() +
         ",\"attempted_sequences\":" + attemptedSequencesJson() +
         ",\"lit\":[" + (lit_[0] ? "1" : "0") + "," + (lit_[1] ? "1" : "0") + "," + (lit_[2] ? "1" : "0") + "," + (lit_[3] ? "1" : "0") + "]" +
         ",\"progressed\":" + progressed_ +
         "}";

  publish(topics.state.c_str(), "state", 1, data, nullptr, true);
}
