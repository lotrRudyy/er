#include "knocking_riddle.h"
#include <cstring>

// Minimal JSON escaping for filenames (usually not needed, but safe)
static String jsonEscape(const String& s) {
  String out;
  out.reserve(s.length() + 8);
  for (size_t i = 0; i < s.length(); i++) {
    char c = s[i];
    if (c == '\\' || c == '"') { out += '\\'; out += c; }
    else if (c == '\n') out += "\\n";
    else if (c == '\r') out += "\\r";
    else if (c == '\t') out += "\\t";
    else out += c;
  }
  return out;
}

void KnockingRiddle::begin(Core::NodeContext& ctx) {
  ctx_ = &ctx;

  // LittleFS (NO auto-format -> never wipes your sounds)
  if (!LittleFS.begin(false)) {
    audioOk_ = false;
    log("ERR", "LittleFS mount failed (audio disabled)");
  } else {
    audioOk_ = true;
  }

  // I2S -> MAX98357A
  audio_.setPinout(kI2S_BCLK, kI2S_LRC, kI2S_DIN);
  audio_.setVolume(kAudioVolume);
  audio_.setTone(0, 0, 15); // match your loudness/clarity setting

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
  fsListed_ = false;

  publishState();
}

void KnockingRiddle::tick(uint32_t nowMs) {
  if (!ctx_) return;

  // Keep audio pipeline alive
  if (audioOk_) {
    audio_.loop();
  }

  // Publish FS listing over MQTT once (after MQTT is connected)
  publishLittleFsListingOnce();

  updatePiezoSamples(nowMs);
  handleKnockWindow(nowMs);
  evaluateSequenceIfDue(nowMs);
  serviceSound(nowMs);
}

bool KnockingRiddle::onCmd(const char* cmd, const char* /*payload*/) {
  if (!cmd) return false;
  if (strcasecmp(cmd, "RESET_KNOCKING") == 0) {
    resetState("reset_knocking");
    publishState();
    return true;
  }
  return false;
}

bool KnockingRiddle::shouldAllowLog(const char* level) {
  (void)level;
  return kDevLog;
}

void KnockingRiddle::log(const char* level, const String& msg) const {
  if (!ctx_) return;
  if (strcmp(level, "ERR") == 0) errorCount_++;
  ctx_->log(level, msg);
}

void KnockingRiddle::log(const char* level, const String& msg, const String& dataJson) const {
  if (!ctx_) return;
  if (strcmp(level, "ERR") == 0) errorCount_++;
  ctx_->log(level, msg, dataJson);
}

bool KnockingRiddle::publish(const char* topic, const char* type, uint32_t version, const String& dataJson,
                             const char* id, bool retained) const {
  if (!ctx_) return false;
  return ctx_->publishEnvelope(topic, type, version, dataJson, id, retained);
}

bool KnockingRiddle::publish(const char* topic, const String& payload, bool retained) const {
  if (!ctx_) return false;
  return ctx_->publish(topic, payload, retained);
}

void KnockingRiddle::publishLittleFsListingOnce() {
  if (!audioOk_ || fsListed_ || !ctx_) return;

  auto* c = ctx_->mqttClient();
  if (!c || !c->connected()) return;

  fsListed_ = true;

  String files = "[";
  File root = LittleFS.open("/");
  File file = root.openNextFile();
  bool first = true;
  while (file) {
    if (!first) files += ",";
    first = false;
    files += "\"";
    files += jsonEscape(String(file.name()));
    files += "\"";
    file = root.openNextFile();
  }
  files += "]";

  log("INF", "LittleFS files", String("{\"files\":") + files + "}");

  const auto& topics = ctx_->config().topics;
  if (topics.state.length() > 0) {
    String data = String("{\"littlefs_files\":") + files + "}";
    publish(topics.state.c_str(), "littlefs_debug", 1, data, nullptr, false);
  }
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
    if (raw > ps.bucketMax[bucketIdx]) ps.bucketMax[bucketIdx] = raw;

    if (!knockWindowActive_) {
      if (raw >= kKnockThresholds[i]) {
        knockWindowActive_ = true;
        knockWindowStart_ = nowMs;
        for (int j = 0; j < kSensorCount; j++) windowMax_[j] = 0;
        windowMax_[i] = raw;
      }
    } else {
      if (raw > windowMax_[i]) windowMax_[i] = raw;
    }
  }
}

void KnockingRiddle::handleKnockWindow(uint32_t nowMs) {
  if (!knockWindowActive_ || (nowMs - knockWindowStart_ < kKnockWindowMs)) return;
  knockWindowActive_ = false;

  int bestIdx = -1;
  uint16_t bestVal = 0;
  for (int i = 0; i < kSensorCount; i++) {
    if (windowMax_[i] > bestVal) {
      bestVal = windowMax_[i];
      bestIdx = i;
    }
  }
  if (bestIdx < 0 || bestVal < kKnockThresholds[bestIdx]) return;

  // ✅ CHANGE YOU ASKED FOR:
  // Play the sound immediately after the 40ms window (as soon as we know the winner),
  // BEFORE any global debounce / lockout logic.
  playKnockSound(bestIdx);

  // Global debounce applies only to sequence acceptance
  if (nowMs - lastKnockMsGlobal_ < kKnockDebounceMs) {
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

  registerKnock(bestIdx, bestVal, nowMs);
}

void KnockingRiddle::registerKnock(int idx, uint16_t raw, uint32_t nowMs) {
  if (!ctx_->enabled()) return;

  if (kDevLog) {
    String data = String("{\"idx\":") + idx + ",\"raw\":" + raw + "}";
    log("INF", "KNOCK", data);
  }

  if (seqLen_ < kSeqMaxLen) {
    seqBuf_[seqLen_++] = idx;
  }

  // Keep consistent timebase with tick(nowMs)
  lastSeqActivityMs_ = nowMs;
}

void KnockingRiddle::playKnockSound(int idx) {
  int track = 0;
  if (idx == 0) track = 1;
  else if (idx == 1) track = 2;
  else if (idx == 2) track = 3;
  else return;
  enqueueSound((uint8_t)track);
}

void KnockingRiddle::enqueueSound(uint8_t track) {
  if (!audioOk_) return;
  if (track < 1 || track > 4) return;

  if (soundQueueFull()) {
    if (kDevLog) log("WRN", "Sound queue full, dropping", String("{\"track\":") + track + "}");
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
    case 1: return 120;
    case 2: return 120;
    case 3: return 120;
    case 4: return 1040;
    default: return 120;
  }
}

void KnockingRiddle::serviceSound(uint32_t nowMs) {
  if (!audioOk_) return;

  if (soundPlaying_) {
    unsigned long needed = trackFallbackMs(currentTrack_);
    if (nowMs - lastSoundStartMs_ >= needed) {
      soundPlaying_ = false;
      if (kDevLog) {
        String data = String("{\"track\":") + currentTrack_ + ",\"dur\":" + needed + "}";
        log("DBG", "Sound finished (timeout)", data);
      }
    }
    return;
  }

  if (soundQueueEmpty()) return;

  uint8_t track = soundQueue_[soundHead_];
  soundHead_ = static_cast<uint8_t>((soundHead_ + 1) % kSoundQueueMax);

  const char* path = kTrackPaths[track];
  if (!path) return;

  if (kDevLog) {
    String data = String("{\"track\":") + track +
                  ",\"path\":\"" + String(path) + "\"" +
                  ",\"head\":" + soundHead_ +
                  ",\"tail\":" + soundTail_ + "}";
    log("INF", "PLAY sound from queue", data);
  }

  audio_.connecttoFS(LittleFS, path);
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

  bool ok = (seqLen_ == kSeqExpectLen);
  if (ok) {
    for (int i = 0; i < kSeqExpectLen; i++) {
      if (seqBuf_[i] != kSeqExpect[i]) { ok = false; break; }
    }
  }

  if (ok) {
    log("INF", "SEQUENCE OK -> SOLVED");
    publishSolvedEvent();
    resetSequence();
  } else {
    log("INF", "SEQUENCE FAIL -> error sound (track 4) + reset");
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
  const auto& topics = ctx_->config().topics;
  if (topics.evt.length() > 0) {
    publish(topics.evt.c_str(), "riddle_solved", 1, data);
  }

  publish("maglock/lock/knocking/cmd", "OPEN");
  publishState();
}

void KnockingRiddle::resetState(const char* reason) {
  bool wasSolved = solved_;

  knockWindowActive_ = false;
  lastKnockMsGlobal_ = 0;

  soundHead_ = soundTail_ = 0;
  soundPlaying_ = false;
  lastSoundStartMs_ = 0;
  currentTrack_ = 0;

  solved_ = false;
  resetSequence();

  if (audioOk_) {
    audio_.stopSong();
  }

  const char* src = (reason && reason[0]) ? reason : "reset";
  String data = String("{\"src\":\"") + src + "\",\"was_solved\":" + (wasSolved ? "1" : "0") + "}";
  log("INF", "KNOCKING_STATE_RESET", data);
}

void KnockingRiddle::publishState() {
  if (!ctx_) return;
  String data = String("{\"mode\":\"listening\",\"solved\":") + (solved_ ? "true" : "false") + "}";
  const auto& topics = ctx_->config().topics;
  if (topics.state.length() > 0) {
    publish(topics.state.c_str(), "state", 1, data, nullptr, true);
  }
}
