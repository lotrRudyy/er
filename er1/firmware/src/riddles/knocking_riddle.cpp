#include "knocking_riddle.h"

void KnockingRiddle::begin(Core::NodeContext& ctx) {
  ctx_ = &ctx;
  dfSerial_ = &Serial2;
  dfSerial_->begin(9600, SERIAL_8N1, 16, 17);

  if (dfPlayer_.begin(*dfSerial_)) {
    dfOk_ = true;
    dfPlayer_.volume(kDfVolume);
  } else {
    dfOk_ = false;
    logErr("DFPlayer init failed");
  }

  for (int i = 0; i < kSensorCount; i++) {
    piezo_[i].pin = kPiezoPins[i];
    piezo_[i].sum = 0;
    piezo_[i].samples = 0;
    piezo_[i].avg = 0;
    piezo_[i].base = 0;
    piezo_[i].maxVal = 0;
    piezo_[i].lastRaw = 0;
    for (int b = 0; b < PiezoState::kBuckets; b++) {
      piezo_[i].bucketMax[b] = 0;
    }
  }

  knockWindowActive_ = false;
  lastKnockMsGlobal_ = 0;
  seqLen_ = 0;
  lastSeqActivityMs_ = 0;
  soundHead_ = soundTail_ = 0;
  soundPlaying_ = false;
  lastSoundStartMs_ = 0;
  currentTrack_ = 0;

  solved_ = false;
  publishState();
}

void KnockingRiddle::tick(uint32_t nowMs) {
  if (!ctx_) return;
  updatePiezoSamples(nowMs);
  handleKnockWindow(nowMs);
  evaluateSequenceIfDue(nowMs);
  serviceSound(nowMs);
}

bool KnockingRiddle::onCmd(const char* /*cmd*/, const char* /*payload*/) {
  return false;
}

bool KnockingRiddle::shouldAllowLog(const char* level) {
  bool isErr = (strcmp(level, "ERR") == 0);
  if (isErr) {
    errorCount_++;
    return true;
  }
  return kDevLog;
}

void KnockingRiddle::log(const char* level, const String& msg) {
  if (!ctx_) return;
  ctx_->log(level, msg);
}

void KnockingRiddle::log(const char* level, const String& msg, const String& dataJson) {
  if (!ctx_) return;
  ctx_->log(level, msg, dataJson);
}

void KnockingRiddle::logErr(const String& msg, const String& dataJson) {
  errorCount_++;
  log("ERR", msg, dataJson);
}

void KnockingRiddle::updatePiezoSamples(uint32_t nowMs) {
  for (int i = 0; i < kSensorCount; i++) {
    PiezoState& ps = piezo_[i];
    uint16_t raw = analogRead(ps.pin);
    ps.lastRaw = raw;
    ps.sum += raw;
    ps.samples++;
    if (raw > ps.maxVal) ps.maxVal = raw;
    uint8_t bucketIdx = (nowMs / 100) % PiezoState::kBuckets;
    if (raw > ps.bucketMax[bucketIdx]) {
      ps.bucketMax[bucketIdx] = raw;
    }

    if (!knockWindowActive_) {
      if (raw >= kKnockThresholds[i]) {
        knockWindowActive_ = true;
        knockWindowStart_ = nowMs;
        for (int j = 0; j < kSensorCount; j++) {
          windowMax_[j] = 0;
        }
        windowMax_[i] = raw;
      }
    } else {
      if (raw > windowMax_[i]) {
        windowMax_[i] = raw;
      }
    }
  }
}

void KnockingRiddle::handleKnockWindow(uint32_t nowMs) {
  if (!knockWindowActive_ || (nowMs - knockWindowStart_ < kKnockWindowMs)) {
    return;
  }
  knockWindowActive_ = false;
  if (nowMs - lastKnockMsGlobal_ < kKnockDebounceMs) {
    return;
  }

  int bestIdx = -1;
  uint16_t bestVal = 0;
  for (int i = 0; i < kSensorCount; i++) {
    if (windowMax_[i] > bestVal) {
      bestVal = windowMax_[i];
      bestIdx = i;
    }
  }
  if (bestIdx < 0 || bestVal < kKnockThresholds[bestIdx]) {
    return;
  }

  lastKnockMsGlobal_ = nowMs;
  if (kDevLog) {
    String data = String("{\"best\":") + bestIdx +
                  ",\"val\":" + bestVal +
                  ",\"m0\":" + windowMax_[0] +
                  ",\"m1\":" + windowMax_[1] +
                  ",\"m2\":" + windowMax_[2] + "}";
    log("INF", "KNOCK_WINDOW_WINNER", data);
  }
  registerKnock(bestIdx, bestVal);
}

void KnockingRiddle::registerKnock(int idx, uint16_t raw) {
  if (!ctx_->enabled()) return;
  if (kDevLog) {
    String data = String("{\"idx\":") + idx + ",\"raw\":" + raw + "}";
    log("INF", "KNOCK", data);
  }

  if (seqLen_ < kSeqMaxLen) {
    seqBuf_[seqLen_++] = idx;
  }
  lastSeqActivityMs_ = millis();
  playKnockSound(idx);
}

void KnockingRiddle::playKnockSound(int idx) {
  int track = 0;
  if (idx == 0) track = 1;
  else if (idx == 1) track = 2;
  else if (idx == 2) track = 3;
  else return;
  enqueueSound(track);
}

void KnockingRiddle::enqueueSound(uint8_t track) {
  if (!dfOk_) return;
  if (soundQueueFull()) {
    if (kDevLog) {
      log("WRN", "Sound queue full, dropping", String("{\"track\":") + track + "}");
    }
    return;
  }
  soundQueue_[soundTail_] = track;
  soundTail_ = static_cast<uint8_t>((soundTail_ + 1) % kSoundQueueMax);
  if (kDevLog) {
    String data = String("{\"track\":") + track +
                  ",\"head\":" + soundHead_ +
                  ",\"tail\":" + soundTail_ + "}";
    log("DBG", "ENQUEUE sound", data);
  }
}

bool KnockingRiddle::soundQueueEmpty() const {
  return soundHead_ == soundTail_;
}

bool KnockingRiddle::soundQueueFull() const {
  return static_cast<uint8_t>((soundTail_ + 1) % kSoundQueueMax) == soundHead_;
}

unsigned long KnockingRiddle::trackFallbackMs(uint8_t track) const {
  switch (track) {
    case 1:
    case 2:
    case 3:
      return 205;
    case 4:
      return 1240;
    default:
      return 500;
  }
}

void KnockingRiddle::serviceSound(uint32_t nowMs) {
  if (!dfOk_) return;

  if (soundPlaying_) {
    unsigned long needed = trackFallbackMs(currentTrack_);
    if (nowMs - lastSoundStartMs_ >= needed) {
      soundPlaying_ = false;
      if (kDevLog) {
        String data = String("{\"track\":") + currentTrack_ + ",\"dur\":" + needed + "}";
        log("DBG", "Sound finished (per-track timeout)", data);
      }
    }
    return;
  }

  if (soundQueueEmpty()) return;

  uint8_t track = soundQueue_[soundHead_];
  soundHead_ = static_cast<uint8_t>((soundHead_ + 1) % kSoundQueueMax);

  if (kDevLog) {
    String data = String("{\"track\":") + track +
                  ",\"head\":" + soundHead_ +
                  ",\"tail\":" + soundTail_ + "}";
    log("INF", "PLAY sound from queue", data);
  }

  dfPlayer_.playMp3Folder(track);
  lastSoundStartMs_ = nowMs;
  currentTrack_ = track;
  soundPlaying_ = true;
}

void KnockingRiddle::evaluateSequence() {
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
    log("INF", "SEQUENCE OK -> SOLVED (no success sound)");
    publishSolvedEvent();
    resetSequence();
  } else {
    log("INF", "SEQUENCE FAIL -> reset/error sound (track 4)");
    enqueueSound(4);
    resetSequence();
  }
}

void KnockingRiddle::evaluateSequenceIfDue(uint32_t nowMs) {
  if (seqLen_ == 0) return;
  if (nowMs - lastSeqActivityMs_ < kSeqTimeoutMs) return;
  evaluateSequence();
}

void KnockingRiddle::resetSequence() {
  seqLen_ = 0;
  lastSeqActivityMs_ = 0;
}

void KnockingRiddle::publishSolvedEvent() {
  if (!ctx_) return;
  solved_ = true;
  String data = "{\"id\":\"knocking\"}";
  ctx_->publishEvent("riddle_solved", data);
  publishState();
}

void KnockingRiddle::publishState() {
  if (!ctx_) return;
  String data = String("{\"mode\":\"listening\",\"solved\":") + (solved_ ? "true" : "false") + "}";
  ctx_->publishState(data, true);
}
