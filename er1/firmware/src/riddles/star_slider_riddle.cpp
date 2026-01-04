#include "star_slider_riddle.h"

#include <cstring>

void StarSliderRiddle::begin(Core::NodeContext& ctx) {
  ctx_ = &ctx;
  prefs_ = &ctx.prefs();
  const char* node = ctx.nodeId() ? ctx.nodeId() : "star_slider";
  (void)node;
  // Metrics go via core_log (DBG) so <node>/log/level controls verbosity.

  pinMode(kButtonPin, INPUT_PULLUP);
  btnPrevState_ = digitalRead(kButtonPin);
  btnWasPressed_ = false;
  btnLastChangeMs_ = millis();
  lastPollMs_ = millis();
  lastMetricMs_ = millis();

  // Ensure W5500 is deselected while we init RFID.
  pinMode(ETH_CS, OUTPUT);
  digitalWrite(ETH_CS, HIGH);

  for (uint8_t i = 0; i < kReaderCount; i++) {
    pinMode(kRc522RstPins[i], OUTPUT);
    digitalWrite(kRc522RstPins[i], LOW);
  }

  delay(50);

  for (uint8_t i = 0; i < kReaderCount; i++) {
    digitalWrite(kRc522RstPins[i], HIGH);
  }
  delay(50);

  // Ensure all RC522 CS pins are outputs and deselected.
  for (uint8_t i = 0; i < kReaderCount; i++) {
    pinMode(kRc522SsPins[i], OUTPUT);
    digitalWrite(kRc522SsPins[i], HIGH);
  }

  for (uint8_t i = 0; i < kReaderCount; i++) {
    readers_[i].PCD_Init();

    // 🔥 Max antenna gain (largest read radius)
    readers_[i].PCD_SetAntennaGain(MFRC522::RxGain_max);

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
  // IMPORTANT:
  // No RFID polling here. Riddle evaluation happens ONLY on button press.
  handleButton(nowMs);
  publishMetricsIfDue(nowMs);
}

bool StarSliderRiddle::onCmd(const char* cmd, const char* payload) {
  (void)payload;
  if (!cmd) return false;

  // Images-style dedicated reset command (case-insensitive).
  if (strcasecmp(cmd, "RESET_STAR_SLIDER") == 0) {
    resetState("reset_star_slider");
    publishState();
    return true;
  }

  return false;
}

void StarSliderRiddle::resetState(const char* reason) {
  const bool wasSolved = solvedFlag_;

  // Reset puzzle state.
  solvedFlag_ = false;
  solveAttempts_ = 0;
  solveSuccess_ = 0;

  // Clear last read snapshot.
  for (uint8_t i = 0; i < kReaderCount; i++) {
    tagValid_[i] = false;
    tagSize_[i] = 0;
    tagUid_[i][0] = tagUid_[i][1] = tagUid_[i][2] = tagUid_[i][3] = 0;
  }

  // Reset button edge state.
  btnPrevState_ = digitalRead(kButtonPin);
  btnWasPressed_ = false;
  btnLastChangeMs_ = millis();

  if (prefs_) {
    prefs_->putBool("solved", false);
  }

  const char* src = (reason && reason[0]) ? reason : "reset";
  String data = String("{\"src\":\"") + src + "\",\"was_solved\":" + (wasSolved ? "1" : "0") + "}";
  log("INF", "STAR_SLIDER_STATE_RESET", data);
}

void StarSliderRiddle::log(const char* level, const String& msg) const {
  if (!ctx_) return;
  if (strcmp(level, "ERR") == 0) {
    errorCount_++;
  }
  ctx_->log(level, msg);
}

void StarSliderRiddle::log(const char* level, const String& msg, const String& dataJson) const {
  if (!ctx_) return;
  if (strcmp(level, "ERR") == 0) {
    errorCount_++;
  }
  ctx_->log(level, msg, dataJson);
}

bool StarSliderRiddle::publish(const char* topic, const char* type, uint32_t version, const String& dataJson,
                               const char* id, bool retained) const {
  if (!ctx_) return false;
  return ctx_->publishEnvelope(topic, type, version, dataJson, id, retained);
}

bool StarSliderRiddle::publish(const char* topic, const String& payload, bool retained) const {
  if (!ctx_) return false;
  return ctx_->publish(topic, payload, retained);
}

// Kept for compatibility; not used now.
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

  // Chess-style bus hygiene: ensure ETH + other RC522 are deselected.
  digitalWrite(ETH_CS, HIGH);
  for (uint8_t j = 0; j < kReaderCount; j++) digitalWrite(kRc522SsPins[j], HIGH);

  // LEVEL presence detect (WUPA), not edge-triggered IsNewCardPresent().
  byte atqa[2] = {0, 0};
  byte atqaSize = sizeof(atqa);
  MFRC522::StatusCode st = reader.PICC_WakeupA(atqa, &atqaSize);
  if (!(st == MFRC522::STATUS_OK || st == MFRC522::STATUS_COLLISION)) {
    tagValid_[idx] = false;
    tagSize_[idx] = 0;
    return;
  }

  if (!reader.PICC_ReadCardSerial()) {
    tagValid_[idx] = false;
    tagSize_[idx] = 0;
    return;
  }

  uint8_t len = reader.uid.size > 4 ? 4 : reader.uid.size;
  byte tmp[4] = {0, 0, 0, 0};
  std::memcpy(tmp, reader.uid.uidByte, len);

  std::memcpy(tagUid_[idx], tmp, 4);
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
  // Snapshot should be taken ONLY here (button press).
  // Clear snapshot first so stale reads can't survive a failed read.
  for (uint8_t i = 0; i < kReaderCount; i++) {
    tagValid_[i] = false;
    tagSize_[i] = 0;
    tagUid_[i][0] = tagUid_[i][1] = tagUid_[i][2] = tagUid_[i][3] = 0;
  }

  // Read all readers now (single sweep).
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

  // Evaluate on PRESS (active-low), not release.
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
  // Directly command maglock to open the slider lock
  publish("maglock/lock/slider/cmd", "OPEN");
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
