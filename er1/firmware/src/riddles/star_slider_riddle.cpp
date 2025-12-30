#include "star_slider_riddle.h"

#include <cstring>

void StarSliderRiddle::begin(Core::NodeContext& ctx) {
  ctx_ = &ctx;
  prefs_ = &ctx.prefs();
  const char* node = ctx.nodeId() ? ctx.nodeId() : "star_slider";
  // Metrics go via core_log (DBG) so <node>/log/level controls verbosity.

  pinMode(kButtonPin, INPUT_PULLUP);
  btnPrevState_ = digitalRead(kButtonPin);
  btnWasPressed_ = false;
  btnLastChangeMs_ = millis();
  lastPollMs_ = millis();
  lastMetricMs_ = millis();

  for (uint8_t i = 0; i < kReaderCount; i++) {
    readers_[i].PCD_Init();
    tagValid_[i] = false;
    tagSize_[i] = 0;
  }

  bool hasStoredSolve = prefs_ && prefs_->isKey("solved");
  solvedFlag_ = prefs_ ? prefs_->getBool("solved", false) : false;
  if (hasStoredSolve) {
    log("INF", String("STATE restore solved=") + (solvedFlag_ ? "1" : "0"));
  } else if (prefs_) {
    log("INF", "STATE default solved=0");
    prefs_->putBool("solved", solvedFlag_);
  } else {
    log("INF", "STATE default solved=0 (no prefs)");
  }
  publishState();
}

void StarSliderRiddle::tick(uint32_t nowMs) {
  pollReaders(nowMs);
  handleButton(nowMs);
  publishMetricsIfDue(nowMs);
}

bool StarSliderRiddle::onCmd(const char* cmd, const char* payload) {
  (void)cmd;
  (void)payload;
  return false;
}

void StarSliderRiddle::log(const char* level, const String& msg) {
  if (!ctx_) return;
  if (strcmp(level, "ERR") == 0) {
    errorCount_++;
  }
  ctx_->log(level, msg);
}

void StarSliderRiddle::log(const char* level, const String& msg, const String& dataJson) {
  if (!ctx_) return;
  if (strcmp(level, "ERR") == 0) {
    errorCount_++;
  }
  ctx_->log(level, msg, dataJson);
}

bool StarSliderRiddle::publish(const char* topic, const char* type, uint32_t version, const String& dataJson,
                               const char* id, bool retained) {
  if (!ctx_) return false;
  return ctx_->publishEnvelope(topic, type, version, dataJson, id, retained);
}

bool StarSliderRiddle::publish(const char* topic, const String& payload, bool retained) {
  if (!ctx_) return false;
  return ctx_->publish(topic, payload, retained);
}

void StarSliderRiddle::pollReaders(uint32_t nowMs) {
  if (nowMs - lastPollMs_ < kPollIntervalMs) return;
  lastPollMs_ = nowMs;
  for (uint8_t i = 0; i < kReaderCount; i++) {
    pollReader(i);
  }
}

void StarSliderRiddle::pollReader(uint8_t idx) {
  if (idx >= kReaderCount) return;
  MFRC522& reader = readers_[idx];

  if (!reader.PICC_IsNewCardPresent()) {
    tagValid_[idx] = false;
    return;
  }
  if (!reader.PICC_ReadCardSerial()) {
    tagValid_[idx] = false;
    return;
  }

  uint8_t len = reader.uid.size > 4 ? 4 : reader.uid.size;
  std::memcpy(tagUid_[idx], reader.uid.uidByte, len);
  tagSize_[idx] = len;
  tagValid_[idx] = true;

  reader.PICC_HaltA();
  reader.PCD_StopCrypto1();
}

bool StarSliderRiddle::uidEquals(const byte* a, const byte* b, uint8_t len) const {
  for (uint8_t i = 0; i < len; i++) {
    if (a[i] != b[i]) return false;
  }
  return true;
}

bool StarSliderRiddle::isCurrentPatternCorrect() const {
  for (uint8_t i = 0; i < kReaderCount; i++) {
    if (!tagValid_[i]) return false;
    if (tagSize_[i] < 4) return false;
    if (!uidEquals(tagUid_[i], kUidExpected[i], 4)) return false;
  }
  return true;
}

void StarSliderRiddle::evaluateSolveAttempt() {
  for (uint8_t i = 0; i < kReaderCount; i++) {
    pollReader(i);
  }

  solveAttempts_++;

  if (isCurrentPatternCorrect()) {
    solvedFlag_ = true;
    solveSuccess_++;
    if (prefs_) {
      prefs_->putBool("solved", true);
      log("INF", "STATE save solved=1");
    }
    log("INF", "pattern SOLVED");
    publishSolvedEvent(solveAttempts_);
    publishState();
  } else {
    String data = "{";
    for (uint8_t i = 0; i < kReaderCount; i++) {
      if (i > 0) data += ",";
      data += "\"r";
      data += String(i);
      data += "\":\"";
      if (tagValid_[i] && tagSize_[i] >= 4) {
        for (uint8_t b = 0; b < 4; b++) {
          if (b > 0) data += "-";
          if (tagUid_[i][b] < 0x10) data += "0";
          data += String(tagUid_[i][b], HEX);
        }
      } else {
        data += "none";
      }
      data += "\"";
    }
    data += "}";
    log("INF", "pattern WRONG", data);
  }
}

void StarSliderRiddle::handleButton(uint32_t nowMs) {
  bool raw = digitalRead(kButtonPin);
  if (raw != btnPrevState_) {
    btnPrevState_ = raw;
    btnLastChangeMs_ = nowMs;
    return;
  }
  if (nowMs - btnLastChangeMs_ < kBtnDebounceMs) return;

  if (raw == LOW && !btnWasPressed_) {
    btnWasPressed_ = true;
    if (ctx_ && ctx_->enabled()) {
      evaluateSolveAttempt();
    } else {
      log("WRN", "button press while DISABLED");
    }
  } else if (raw == HIGH && btnWasPressed_) {
    btnWasPressed_ = false;
  }
}

void StarSliderRiddle::publishSolvedEvent(uint32_t attemptIdx) {
  if (!ctx_) return;
  String payload = String("{\"id\":\"star_slider\",\"attempt\":") + attemptIdx + "}";
  const auto& topics = ctx_->config().topics;
  if (topics.evt.length() > 0) {
    publish(topics.evt.c_str(), "riddle_solved", 1, payload);
  }
}

void StarSliderRiddle::publishMetricsIfDue(uint32_t nowMs) {
  if (!ctx_) return;
  if (nowMs - lastMetricMs_ < kMetricIntervalMs) return;
  lastMetricMs_ = nowMs;

  String payload = String("{\"fw\":\"") + ctx_->fwVersion() +
                   "\",\"up\":" + String(nowMs / 1000) +
                   ",\"k\":\"star_slider\"" +
                   ",\"solved\":" + (solvedFlag_ ? "1" : "0") +
                   ",\"attempts\":" + String(solveAttempts_) +
                   ",\"success\":" + String(solveSuccess_) +
                   "}";
  log("DBG", "star_slider_metrics", payload);
}

void StarSliderRiddle::publishState() {
  if (!ctx_) return;
  String data = String("{\"solved\":") + (solvedFlag_ ? "true" : "false") +
                ",\"attempts\":" + solveAttempts_ +
                ",\"success\":" + solveSuccess_ + "}";
  const auto& topics = ctx_->config().topics;
  if (topics.state.length() > 0) {
    publish(topics.state.c_str(), "state", 1, data, nullptr, true);
  }
}
