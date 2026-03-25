// knocking_riddle.cpp
#include "knocking_riddle.h"
#include <cstring>
#include <strings.h>

void KnockingRiddle::serialLogLine(const char* level, const String& msg, const String* dataJson) const {
  if (!serialDebug_) return;

  String line = String("[knocking][") + (level ? level : "?") + "] " + msg;
  if (dataJson && dataJson->length() > 0) {
    line += " ";
    line += *dataJson;
  }
  Serial.println(line);
}

void KnockingRiddle::begin(Core::NodeContext& ctx) {
  ctx_ = &ctx;
  audioOk_ = true;

  log("INF", "AUDIO_INIT",
      String("{\"bclk\":") + kI2S_BCLK +
      ",\"lrc\":" + kI2S_LRC +
      ",\"din\":" + kI2S_DIN +
      ",\"volume\":" + kAudioVolume +
      ",\"embedded_sample_rate\":" + KNOCK_SAMPLE_RATE +
      ",\"embedded_knock_len\":" + KNOCK_SAMPLE_LEN +
      ",\"embedded_knock4_len\":" + KNOCK4_SAMPLE_LEN +
      "}");

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

  rawI2sReady_ = false;
  embeddedPlaying_ = false;
  embeddedTrack_ = 0;
  embeddedBuf_ = nullptr;
  embeddedLen_ = 0;
  embeddedPos_ = 0;

  solved_ = false;
  moduleEnabled_ = true;
  tries_ = 0;
  lastAttempt_ = "";

  gameActive_ = false;
  publishState();
}

void KnockingRiddle::setGameMode(bool inGame) {
  gameActive_ = inGame;
  resetState(inGame ? "game_enable" : "game_standby");
  publishState();
}

void KnockingRiddle::tick(uint32_t nowMs) {
  if (!ctx_) return;
  if (!gameActive_) return;

  if (moduleEnabled_ && ctx_->enabled()) {
    updatePiezoSamples(nowMs);
    handleKnockWindow(nowMs);
    evaluateSequenceIfDue(nowMs);
  }

  if (embeddedPlaying_) {
    serviceEmbeddedSound(nowMs);
  } else {
    serviceSound(nowMs);
  }
}

bool KnockingRiddle::onCmd(const char* cmd, const char* payload) {
  if (!cmd) return false;

  if (strcasecmp(cmd, "RESET_KNOCKING") == 0 || strcasecmp(cmd, "RESET") == 0) {
    resetState("reset_knocking");
    publishState();
    return true;
  }

  if (strcasecmp(cmd, "SOLVE") == 0 || strcasecmp(cmd, "SOLVE_KNOCKING") == 0) {
    publishSolvedEvent();
    return true;
  }

  if (strcasecmp(cmd, "ENABLE") == 0) {
    moduleEnabled_ = true;
    publishState();
    return true;
  }

  if (strcasecmp(cmd, "DISABLE") == 0) {
    moduleEnabled_ = false;
    publishState();
    return true;
  }

  if (strcasecmp(cmd, "STATUS") == 0 || strcasecmp(cmd, "AUDIO_STATUS") == 0) {
    publishState();
    publishAudioDebug("cmd_status");
    return true;
  }

  if (strcasecmp(cmd, "DEBUG_ON") == 0 || strcasecmp(cmd, "SERIAL_ON") == 0) {
    serialDebug_ = true;
    log("INF", "SERIAL_DEBUG_ON");
    publishAudioDebug("serial_debug_on");
    return true;
  }

  if (strcasecmp(cmd, "DEBUG_OFF") == 0 || strcasecmp(cmd, "SERIAL_OFF") == 0) {
    log("INF", "SERIAL_DEBUG_OFF");
    serialDebug_ = false;
    publishAudioDebug("serial_debug_off");
    return true;
  }

  if (strcasecmp(cmd, "TEST_SOUND") == 0) {
    int track = atoi(payload ? payload : "0");
    uint32_t nowMs = millis();
    if (track >= 1 && track <= 4) {
      startEmbeddedTrack((uint8_t)track, -1, nowMs);
      publishAudioDebug("test_sound_embedded");
      return true;
    }

    log("WRN", "TEST_SOUND_BAD_ARG",
        String("{\"payload\":\"") + String(payload ? payload : "") + "\"}");
    return true;
  }

  return false;
}

bool KnockingRiddle::shouldAllowLog(const char* level) {
  (void)level;
  return kDevLog;
}

void KnockingRiddle::log(const char* level, const String& msg) const {
  serialLogLine(level, msg, nullptr);
  if (!ctx_) return;
  if (strcmp(level, "ERR") == 0) errorCount_++;
  ctx_->log(level, msg);
}

void KnockingRiddle::log(const char* level, const String& msg, const String& dataJson) const {
  serialLogLine(level, msg, &dataJson);
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

void KnockingRiddle::publishAudioDebug(const char* reason) const {
  if (!ctx_) return;

  const auto& topics = ctx_->config().topics;
  if (topics.dbg.length() == 0) return;

  String data =
      String("{\"reason\":\"") + String(reason ? reason : "?") + "\"" +
      ",\"game_active\":" + (gameActive_ ? "true" : "false") +
      ",\"module_enabled\":" + (moduleEnabled_ ? "true" : "false") +
      ",\"core_enabled\":" + (ctx_->enabled() ? "true" : "false") +
      ",\"audio_ok\":" + (audioOk_ ? "true" : "false") +
      ",\"embedded_playing\":" + (embeddedPlaying_ ? "true" : "false") +
      ",\"embedded_track\":" + embeddedTrack_ +
      ",\"embedded_pos\":" + embeddedPos_ +
      ",\"embedded_len\":" + embeddedLen_ +
      ",\"queue_head\":" + soundHead_ +
      ",\"queue_tail\":" + soundTail_ +
      ",\"seq_len\":" + seqLen_ +
      ",\"last_raw\":[" + String(piezo_[0].lastRaw) + "," + String(piezo_[1].lastRaw) + "," + String(piezo_[2].lastRaw) + "]" +
      "}";

  publish(topics.dbg.c_str(), "audio_debug", 1, data, nullptr, false);
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

        log("DBG", "PIEZO_HIT",
            String("{\"t\":") + nowMs +
            ",\"idx\":" + i +
            ",\"raw\":" + raw +
            ",\"thr\":" + kKnockThresholds[i] + "}");

        log("DBG", "WIN_START",
            String("{\"t\":") + nowMs +
            ",\"idx\":" + i +
            ",\"raw\":" + raw +
            ",\"win_ms\":" + kKnockWindowMs + "}");
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

  log("DBG", "WIN_DECIDED",
      String("{\"t\":") + nowMs +
      ",\"dt\":" + (nowMs - knockWindowStart_) +
      ",\"idx\":" + bestIdx +
      ",\"val\":" + bestVal +
      ",\"m0\":" + windowMax_[0] +
      ",\"m1\":" + windowMax_[1] +
      ",\"m2\":" + windowMax_[2] + "}");

  if (nowMs - lastKnockMsGlobal_ < kKnockDebounceMs) {
    return;
  }
  lastKnockMsGlobal_ = nowMs;

  playKnockSound(bestIdx);
  registerKnock(bestIdx, bestVal, nowMs);
}

void KnockingRiddle::registerKnock(int idx, uint16_t /*raw*/, uint32_t nowMs) {
  if (!moduleEnabled_ || !ctx_->enabled()) return;

  if (seqLen_ < kSeqMaxLen) {
    seqBuf_[seqLen_++] = idx;
  }
  lastSeqActivityMs_ = nowMs;
}

void KnockingRiddle::playKnockSound(int idx) {
  int track = 0;
  if (idx == 0) track = 1;
  else if (idx == 1) track = 2;
  else if (idx == 2) track = 3;
  else return;

  startEmbeddedTrack((uint8_t)track, (int8_t)idx, millis());
}

void KnockingRiddle::enqueueSound(uint8_t track, int8_t srcIdx) {
  if (track < 1 || track > 4) return;

  uint8_t srcNibble = (srcIdx >= 0 && srcIdx < kSensorCount) ? (uint8_t)srcIdx : 0x0F;
  uint8_t packed = (uint8_t)((srcNibble << 4) | (track & 0x0F));

  if (soundQueueFull()) {
    log("DBG", "SOUND_DROP",
        String("{\"t\":") + millis() + ",\"track\":" + track + ",\"idx\":" + (int)srcIdx + "}");
    return;
  }

  soundQueue_[soundTail_] = packed;
  soundTail_ = static_cast<uint8_t>((soundTail_ + 1) % kSoundQueueMax);
}

bool KnockingRiddle::soundQueueEmpty() const {
  return soundHead_ == soundTail_;
}

bool KnockingRiddle::soundQueueFull() const {
  return static_cast<uint8_t>((soundTail_ + 1) % kSoundQueueMax) == soundHead_;
}

void KnockingRiddle::ensureRawI2sConfigured() {
  if (rawI2sReady_) return;

  i2s_driver_uninstall(kI2SPort);

  i2s_config_t cfg{};
  cfg.mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_TX);
  cfg.sample_rate = KNOCK_SAMPLE_RATE;
  cfg.bits_per_sample = I2S_BITS_PER_SAMPLE_16BIT;
  cfg.channel_format = I2S_CHANNEL_FMT_RIGHT_LEFT;
  cfg.communication_format = I2S_COMM_FORMAT_STAND_I2S;
  cfg.intr_alloc_flags = 0;
  cfg.dma_buf_count = 8;
  cfg.dma_buf_len = 128;
  cfg.use_apll = false;
  cfg.tx_desc_auto_clear = true;
  cfg.fixed_mclk = 0;

  i2s_pin_config_t pins{};
  pins.bck_io_num = kI2S_BCLK;
  pins.ws_io_num = kI2S_LRC;
  pins.data_out_num = kI2S_DIN;
  pins.data_in_num = I2S_PIN_NO_CHANGE;

  i2s_driver_install(kI2SPort, &cfg, 0, nullptr);
  i2s_set_pin(kI2SPort, &pins);
  i2s_zero_dma_buffer(kI2SPort);
  rawI2sReady_ = true;
}

bool KnockingRiddle::startEmbeddedTrack(uint8_t track, int8_t srcIdx, uint32_t nowMs) {
  if (track < 1 || track > 4) return false;

  ensureRawI2sConfigured();

  switch (track) {
    case 1:
      embeddedBuf_ = knock1_pcm;
      embeddedLen_ = KNOCK_SAMPLE_LEN;
      break;
    case 2:
      embeddedBuf_ = knock2_pcm;
      embeddedLen_ = KNOCK_SAMPLE_LEN;
      break;
    case 3:
      embeddedBuf_ = knock3_pcm;
      embeddedLen_ = KNOCK_SAMPLE_LEN;
      break;
    case 4:
      embeddedBuf_ = knock4_pcm;
      embeddedLen_ = KNOCK4_SAMPLE_LEN;
      break;
    default:
      return false;
  }

  embeddedTrack_ = track;
  embeddedPos_ = 0;
  embeddedPlaying_ = true;

  log("DBG", "SOUND_START",
      String("{\"t\":") + nowMs +
      ",\"track\":" + track +
      ",\"idx\":" + (int)srcIdx +
      ",\"embedded\":true" +
      ",\"samples\":" + embeddedLen_ +
      ",\"sample_rate\":" + KNOCK_SAMPLE_RATE +
      "}");

  return true;
}

void KnockingRiddle::serviceEmbeddedSound(uint32_t nowMs) {
  (void)nowMs;
  if (!embeddedPlaying_ || !embeddedBuf_ || embeddedPos_ >= embeddedLen_) {
    return;
  }

  int16_t stereo[kEmbeddedChunkFrames * 2];
  size_t frames = 0;
  while (frames < kEmbeddedChunkFrames && embeddedPos_ < embeddedLen_) {
    int16_t s = embeddedBuf_[embeddedPos_++];
    stereo[frames * 2] = s;
    stereo[frames * 2 + 1] = s;
    ++frames;
  }

  size_t written = 0;
  if (frames > 0) {
    i2s_write(kI2SPort, stereo, frames * 2 * sizeof(int16_t), &written, portMAX_DELAY);
  }

  if (embeddedPos_ >= embeddedLen_) {
    log("DBG", "SOUND_DONE",
        String("{\"t\":") + millis() +
        ",\"track\":" + embeddedTrack_ +
        ",\"embedded\":true" +
        ",\"samples\":" + embeddedLen_ +
        "}");
    stopEmbeddedSound();
  }
}

void KnockingRiddle::stopEmbeddedSound() {
  embeddedPlaying_ = false;
  embeddedTrack_ = 0;
  embeddedBuf_ = nullptr;
  embeddedLen_ = 0;
  embeddedPos_ = 0;

  if (rawI2sReady_) {
    i2s_zero_dma_buffer(kI2SPort);
  }
}

void KnockingRiddle::serviceSound(uint32_t nowMs) {
  if (embeddedPlaying_) return;
  if (soundQueueEmpty()) return;

  uint8_t packed = soundQueue_[soundHead_];
  soundHead_ = static_cast<uint8_t>((soundHead_ + 1) % kSoundQueueMax);

  uint8_t track = (uint8_t)(packed & 0x0F);
  uint8_t srcNibble = (uint8_t)((packed >> 4) & 0x0F);
  int8_t srcIdx = (srcNibble == 0x0F) ? (int8_t)-1 : (int8_t)srcNibble;

  if (track >= 1 && track <= 4) {
    startEmbeddedTrack(track, srcIdx, nowMs);
  }
}

void KnockingRiddle::evaluateSequence(bool timeoutAttempt) {
  if (seqLen_ == 0) return;

  bool ok = (seqLen_ == kSeqExpectLen);
  if (ok) {
    for (int i = 0; i < kSeqExpectLen; i++) {
      if (seqBuf_[i] != kSeqExpect[i]) { ok = false; break; }
    }
  }

  if (ok) {
    log("DBG", "SEQ_OK", String("{\"t\":") + millis() + ",\"len\":" + seqLen_ + "}");
    publishSolvedEvent();
    resetSequence();
  } else {
    tries_++;
    lastAttempt_ = currentSequenceHyphen();
    log("DBG", "SEQ_FAIL",
        String("{\"t\":") + millis() +
        ",\"len\":" + seqLen_ +
        ",\"timeout\":" + (timeoutAttempt ? "1" : "0") + "}");
    enqueueSound(4, -1);
    resetSequence();
  }
}

void KnockingRiddle::evaluateSequenceIfDue(uint32_t nowMs) {
  if (seqLen_ == 0) return;
  if (nowMs - lastSeqActivityMs_ < kSeqTimeoutMs) return;
  evaluateSequence(true);
}

void KnockingRiddle::resetSequence() {
  seqLen_ = 0;
  lastSeqActivityMs_ = 0;
}

String KnockingRiddle::currentSequenceHyphen() const {
  String out;
  for (int i = 0; i < seqLen_; i++) {
    if (i > 0) out += "-";
    out += String(seqBuf_[i] + 1);
  }
  return out;
}

String KnockingRiddle::currentSequenceJson() const {
  String out = "[";
  for (int i = 0; i < seqLen_; i++) {
    if (i > 0) out += ",";
    out += "\"";
    out += String(seqBuf_[i] + 1);
    out += "\"";
  }
  out += "]";
  return out;
}

void KnockingRiddle::publishSolvedEvent() {
  if (!ctx_) return;
  solved_ = true;
  publish("game/event", "{\"node\":\"knocking\",\"event\":\"solved\"}", false);
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

  stopEmbeddedSound();

  solved_ = false;
  resetSequence();
  tries_ = 0;
  lastAttempt_ = "";

  const char* src = (reason && reason[0]) ? reason : "reset";
  String data = String("{\"src\":\"") + src + "\",\"was_solved\":" + (wasSolved ? "1" : "0") + "}";
  log("DBG", "KNOCKING_STATE_RESET", String("{\"t\":") + millis() + "," + data.substring(1));
}

void KnockingRiddle::publishState() {
  if (!ctx_) return;
  String data =
      String("{\"id\":\"knocking\"") +
      ",\"mode\":\"" + (gameActive_ ? "ingame" : "standby") + "\"" +
      ",\"enabled\":" + ((gameActive_ && moduleEnabled_ && ctx_->enabled()) ? String("true") : String("false")) +
      ",\"solved\":" + (solved_ ? "true" : "false") +
      ",\"raw_state\":\"" + (solved_ ? "solved" : (seqLen_ > 0 ? "progress" : "idle")) + "\"" +
      ",\"tries\":" + String(tries_) +
      ",\"sequence_current\":" + currentSequenceJson() +
      ",\"last_attempt\":\"" + lastAttempt_ + "\"" +
      "}";

  const auto& topics = ctx_->config().topics;
  if (topics.state.length() > 0) {
    publish(topics.state.c_str(), data, true);
  }
}
