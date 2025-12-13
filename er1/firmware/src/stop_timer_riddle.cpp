#include "stop_timer_riddle.h"

#include <cstring>

namespace {
HardwareSerial& kDfSerial = Serial2;
}  // namespace

void StopTimerRiddle::begin(Core::NodeContext& ctx) {
  ctx_ = &ctx;
  serial_ = &kDfSerial;
  serial_->begin(9600, SERIAL_8N1, 16, 17);
  if (dfPlayer_.begin(*serial_)) {
    dfOk_ = true;
    dfPlayer_.volume(kDfVolume);
  } else {
    dfOk_ = false;
    log("ERR", "DFPlayer init failed");
  }

  initSensors();
  resetSequence();
  lastMetricMs_ = millis();
}

void StopTimerRiddle::tick(uint32_t nowMs) {
  sampleSensors(nowMs);
  evaluateSequenceIfDue(nowMs);
  publishMetricsIfDue(nowMs);
}

bool StopTimerRiddle::onCmd(const char* cmd, const char* payload) {
  (void)cmd;
  (void)payload;
  return false;
}

bool StopTimerRiddle::shouldAllowLog(const char* level) {
  bool isErr = (strcmp(level, "ERR") == 0);
  if (isErr) {
    errorCount_++;
    return true;
  }
  return kDevLog;
}

void StopTimerRiddle::initSensors() {
  for (int i = 0; i < kSensorCount; i++) {
    PiezoState& ps = piezo_[i];
    ps.pin = kPiezoPins[i];
    ps.sum = 0;
    ps.samples = 0;
    ps.avg = 0;
    ps.base = 0;
    ps.maxVal = 0;
    ps.lastRaw = 0;
    hitLatched_[i] = false;
    lastKnockMs_[i] = 0;
    for (int b = 0; b < PiezoState::kBucketCount; b++) {
      ps.bucketSum[b] = 0;
      ps.bucketSamples[b] = 0;
    }
  }
  seqLen_ = 0;
  lastSeqActivityMs_ = 0;
  lastMetricMs_ = millis();
}

void StopTimerRiddle::sampleSensors(uint32_t nowMs) {
  for (int i = 0; i < kSensorCount; i++) {
    PiezoState& ps = piezo_[i];
    uint16_t raw = analogRead(ps.pin);
    ps.lastRaw = raw;

    ps.sum += raw;
    ps.samples++;
    if (raw > ps.maxVal) {
      ps.maxVal = raw;
    }

    uint8_t bucketIdx = (nowMs / 100) % PiezoState::kBucketCount;
    ps.bucketSum[bucketIdx] += raw;
    ps.bucketSamples[bucketIdx] += 1;

    if (!hitLatched_[i] && raw >= kKnockRawThr) {
      hitLatched_[i] = true;
      registerKnock(i, raw, nowMs);
    } else if (hitLatched_[i] && raw < kKnockRelThr) {
      hitLatched_[i] = false;
    }
  }
}

void StopTimerRiddle::registerKnock(int idx, uint16_t raw, uint32_t nowMs) {
  if (!ctx_ || !ctx_->enabled()) return;
  if (nowMs - lastKnockMs_[idx] < kKnockDebounceMs) return;
  lastKnockMs_[idx] = nowMs;

  if (kDevLog) {
    log("INF", String("KNOCK on sensor ") + idx + " raw=" + raw);
  }

  if (seqLen_ < kSeqMaxLen) {
    seqBuf_[seqLen_++] = idx;
  }
  lastSeqActivityMs_ = nowMs;
  playKnockSound(idx);
}

void StopTimerRiddle::evaluateSequence() {
  if (seqLen_ == 0) return;

  if (kDevLog) {
    String seqStr;
    for (int i = 0; i < seqLen_; i++) {
      if (i > 0) seqStr += ",";
      seqStr += seqBuf_[i];
    }
    log("INF", String("SEQ_EVAL len=") + seqLen_ + " buf=[" + seqStr + "]");
  }

  bool ok = true;
  if (seqLen_ != kSeqExpectLen) {
    ok = false;
  } else {
    for (int i = 0; i < kSeqExpectLen; i++) {
      if (seqBuf_[i] != kSeqExpect[i]) {
        ok = false;
        break;
      }
    }
  }

  if (ok) {
    log("INF", "SEQUENCE OK -> SOLVED & open maglock");
    if (dfOk_) {
      dfPlayer_.play(2);
    }
    publishSolvedEvent();
  } else {
    log("INF", "SEQUENCE FAIL -> FAIL SOUND x5 & reset");
    playFailSoundX5();
  }
  resetSequence();
}

void StopTimerRiddle::evaluateSequenceIfDue(uint32_t nowMs) {
  if (seqLen_ == 0) return;
  if (nowMs - lastSeqActivityMs_ < kSeqTimeoutMs) return;
  evaluateSequence();
}

void StopTimerRiddle::resetSequence() {
  seqLen_ = 0;
  lastSeqActivityMs_ = 0;
}

void StopTimerRiddle::playKnockSound(int idx) {
  if (!dfOk_) return;
  int count = idx + 1;
  for (int i = 0; i < count; i++) {
    dfPlayer_.play(1);
    delay(200);
  }
}

void StopTimerRiddle::playFailSoundX5() {
  if (!dfOk_) return;
  for (int i = 0; i < 5; i++) {
    dfPlayer_.play(1);
    delay(300);
  }
}

void StopTimerRiddle::publishSolvedEvent() {
  if (!ctx_) return;
  ctx_->publish(StopTimerRiddle::kTopicEvent, "{\"event\":\"SOLVED\",\"rid\":\"stop_timer\"}");
}

void StopTimerRiddle::publishMetricsIfDue(uint32_t nowMs) {
  if (!ctx_) return;
  if (nowMs - lastMetricMs_ < kMetricIntervalMs) return;
  lastMetricMs_ = nowMs;
  uint32_t uptime = nowMs / 1000;

  for (int i = 0; i < kSensorCount; i++) {
    PiezoState& ps = piezo_[i];
    ps.avg = (ps.samples > 0) ? (ps.sum / ps.samples) : 0;
    if (ps.base == 0) {
      ps.base = ps.avg;
    } else {
      ps.base = (ps.base * 15 + ps.avg) / 16;
    }
    int d = static_cast<int>(ps.avg) - static_cast<int>(ps.base);

    String window = "[";
    for (int b = 0; b < PiezoState::kBucketCount; b++) {
      uint16_t wAvg = 0;
      if (ps.bucketSamples[b] > 0) {
        wAvg = ps.bucketSum[b] / ps.bucketSamples[b];
      }
      window += String(wAvg);
      if (b < PiezoState::kBucketCount - 1) window += ",";
    }
    window += "]";

    String payload = String("{\"fw\":\"") + ctx_->fwVersion() +
                     "\",\"up\":" + uptime +
                     ",\"k\":\"stop_timer\"" +
                     ",\"df\":" + (dfOk_ ? "1" : "0") +
                     ",\"en\":" + (ctx_->enabled() ? "1" : "0") +
                     ",\"i\":" + i +
                     ",\"avg\":" + ps.avg +
                     ",\"max\":" + ps.maxVal +
                     ",\"base\":" + ps.base +
                     ",\"d\":" + d +
                     ",\"thr\":" + kKnockRawThr +
                     ",\"w\":" + window +
                     "}";

    ctx_->publish(StopTimerRiddle::kTopicMetric, payload);

    ps.sum = 0;
    ps.samples = 0;
    ps.maxVal = 0;
    for (int b = 0; b < PiezoState::kBucketCount; b++) {
      ps.bucketSum[b] = 0;
      ps.bucketSamples[b] = 0;
    }
  }
}

void StopTimerRiddle::log(const char* level, const String& msg) {
  if (!ctx_) return;
  ctx_->log(level, msg);
}

void StopTimerRiddle::log(const char* level, const String& msg, const String& dataJson) {
  if (!ctx_) return;
  ctx_->log(level, msg, dataJson);
}
