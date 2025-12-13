#include "candles_riddle.h"

void CandlesRiddle::begin(Core::NodeContext& ctx) {
  ctx_ = &ctx;
  const char* node = ctx.nodeId() ? ctx.nodeId() : "candles";
  topicEvent_ = Core::topic(node, "evt");
  topicMetric_ = Core::topic(node, "dbg");
  topicCmdStarSky_ = Core::topic("star_sky", "cmd");
  topicCmdLighting_ = Core::topic("lighting", "cmd");
  for (int i = 0; i < 4; i++) {
    pinMode(kLedPins[i], OUTPUT);
    setLed(i, true);
  }
  initState();
  lastMetricMs_ = millis();
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
  return kDevLog;
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
    metrics_[i].lastRaw = 0;
    lastTrig_[i] = 0;
    progress_[i] = -1;
  }
  progressed_ = 0;
  lastAction_ = millis();
  lastSeqActivityMs_ = 0;
  solved_ = false;
  solvedEventSent_ = false;
}

bool CandlesRiddle::detectBlow(int idx) {
  const int samples = 80;
  const int needed = samples / 3;
  uint32_t now = millis();
  if (now - lastTrig_[idx] < kRefractMs) return false;

  int over = 0;
  for (int k = 0; k < samples; k++) {
    int v = analogRead(kMicPins[idx]);
    if (abs(v - base_[idx]) > delta_[idx]) over++;
    delay(2);
  }

  bool hit = over >= needed;
  if (hit) {
    lastTrig_[idx] = millis();
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
  ctx_->publish(topicEvent_.c_str(), payload);
  ctx_->publish(topicCmdStarSky_.c_str(), "CANDLES_SOLVED");
  ctx_->publish(topicCmdLighting_.c_str(), "CANDLES_SOLVED");
  solvedEventSent_ = true;
}

void CandlesRiddle::publishMetricsIfDue(uint32_t nowMs) {
  if (!ctx_) return;
  if (nowMs - lastMetricMs_ < kMetricIntervalMs) return;
  lastMetricMs_ = nowMs;
  uint32_t uptime = nowMs / 1000;

  for (int i = 0; i < 4; i++) {
    MicMetric& mm = metrics_[i];
    if (mm.samples > 0) {
      mm.avg = mm.sum / mm.samples;
    } else {
      mm.avg = 0;
    }
    if (mm.base == 0) {
      mm.base = mm.avg;
    } else {
      mm.base = (mm.base * 15 + mm.avg) / 16;
    }

    String payload = String("{\"fw\":\"") + ctx_->fwVersion() +
                     "\",\"up\":" + uptime +
                     ",\"k\":\"candles\"" +
                     ",\"en\":" + (ctx_->enabled() ? "1" : "0") +
                     ",\"i\":" + i +
                     ",\"avg\":" + mm.avg +
                     ",\"max\":" + mm.maxVal +
                     ",\"base\":" + mm.base +
                     ",\"d\":" + (int(mm.avg) - int(mm.base)) +
                     ",\"thr\":" + delta_[i] +
                     "}";
    ctx_->publish(topicMetric_.c_str(), payload);
    mm.sum = 0;
    mm.samples = 0;
    mm.maxVal = 0;
  }
}
