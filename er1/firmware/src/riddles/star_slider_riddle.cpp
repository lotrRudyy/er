#include "star_slider_riddle.h"

#include <cstring>
#include <strings.h>

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

    // Max antenna gain (largest read radius)
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
  gameActive_ = false;
  moduleEnabled_ = true;
  solvedFlag_ = false;
  lastAttemptPositions_ = "";
  if (prefs_) prefs_->putBool("solved", false);
  lastPublishedReaderOrder_ = currentOrderString();
  publishState();
}

void StarSliderRiddle::setGameMode(bool inGame) {
  gameActive_ = inGame;
  resetState(inGame ? "game_enable" : "game_standby");
  publishState();
}

void StarSliderRiddle::tick(uint32_t nowMs) {
  if (!gameActive_) return;
  pollReaders(nowMs);
  handleButton(nowMs);
  publishMetricsIfDue(nowMs);
}

bool StarSliderRiddle::onCmd(const char* cmd, const char* payload) {
  (void)payload;
  if (!cmd) return false;

  if (strcasecmp(cmd, "RESET_STAR_SLIDER") == 0 || strcasecmp(cmd, "RESET") == 0) {
    resetState("reset_star_slider");
    publishState();
    return true;
  }

  if (strcasecmp(cmd, "SOLVE") == 0 || strcasecmp(cmd, "SOLVE_STAR_SLIDER") == 0) {
    solvedFlag_ = true;
    solveAttempts_++;
    solveSuccess_++;
    if (prefs_) {
      prefs_->putBool("solved", true);
      log("INF", "STATE save solved=1");
    }
    publishSolvedEvent(solveAttempts_);
    publishState();
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

  if (strcasecmp(cmd, "STATUS") == 0) {
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
  lastAttemptPositions_ = "";

  // Clear last read snapshot.
  for (uint8_t i = 0; i < kReaderCount; i++) {
    tagValid_[i] = false;
    tagSize_[i] = 0;
    tagUid_[i][0] = tagUid_[i][1] = tagUid_[i][2] = tagUid_[i][3] = 0;
  }

  // Reset button edge state.
  lastPublishedReaderOrder_ = currentOrderString();
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

void StarSliderRiddle::pollReaders(uint32_t nowMs) {
  if (nowMs - lastPollMs_ < kPollIntervalMs) return;
  lastPollMs_ = nowMs;

  const String before = currentOrderString();
  for (uint8_t i = 0; i < kReaderCount; i++) {
    pollReader(i);
  }
  const String after = currentOrderString();

  if (after != before && after != lastPublishedReaderOrder_) {
    lastPublishedReaderOrder_ = after;
    publishState();
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

const char* StarSliderRiddle::labelForUid(uint8_t readerIdx, const byte* uid, uint8_t len) const {
  if (readerIdx >= kReaderCount || len < 4 || !uid) return "none";

  switch (readerIdx) {
    case 0:
      if (uidEquals(uid, kUidExpected[0], 4)) return "scorpio";
      if (uid[0] == 0x5A && uid[1] == 0xDF && uid[2] == 0x53 && uid[3] == 0xD2) return "gemini";
      if (uid[0] == 0x10 && uid[1] == 0x57 && uid[2] == 0x51 && uid[3] == 0x2F) return "libra";
      if (uid[0] == 0x4A && uid[1] == 0x72 && uid[2] == 0x4E && uid[3] == 0xD2) return "sagittarius";
      if (uid[0] == 0x10 && uid[1] == 0x1E && uid[2] == 0x51 && uid[3] == 0x2F) return "aquarius";
      if (uid[0] == 0xDA && uid[1] == 0x47 && uid[2] == 0x50 && uid[3] == 0xD2) return "pisces";
      if (uid[0] == 0xEA && uid[1] == 0x99 && uid[2] == 0x4F && uid[3] == 0xD2) return "leo";
      break;
    case 1:
      if (uid[0] == 0x1A && uid[1] == 0xB8 && uid[2] == 0x4D && uid[3] == 0xD2) return "scorpio";
      if (uid[0] == 0x6A && uid[1] == 0x88 && uid[2] == 0x4F && uid[3] == 0xD2) return "gemini";
      if (uid[0] == 0x4A && uid[1] == 0xE7 && uid[2] == 0x4B && uid[3] == 0xD2) return "libra";
      if (uid[0] == 0x5A && uid[1] == 0x11 && uid[2] == 0x4F && uid[3] == 0xD2) return "sagittarius";
      if (uidEquals(uid, kUidExpected[1], 4)) return "aquarius";
      if (uid[0] == 0xFA && uid[1] == 0x44 && uid[2] == 0x4F && uid[3] == 0xD2) return "pisces";
      if (uid[0] == 0x5A && uid[1] == 0x7A && uid[2] == 0x4C && uid[3] == 0xD2) return "leo";
      break;
    case 2:
      if (uid[0] == 0x7A && uid[1] == 0xDC && uid[2] == 0x4F && uid[3] == 0xD2) return "scorpio";
      if (uid[0] == 0xFA && uid[1] == 0x51 && uid[2] == 0x53 && uid[3] == 0xD2) return "gemini";
      if (uidEquals(uid, kUidExpected[2], 4)) return "libra";
      if (uid[0] == 0x4A && uid[1] == 0x8F && uid[2] == 0x4E && uid[3] == 0xD2) return "sagittarius";
      if (uid[0] == 0x7A && uid[1] == 0x4B && uid[2] == 0x4E && uid[3] == 0xD2) return "aquarius";
      if (uid[0] == 0x3A && uid[1] == 0xF3 && uid[2] == 0x4E && uid[3] == 0xD2) return "pisces";
      if (uid[0] == 0x9A && uid[1] == 0x1E && uid[2] == 0x4B && uid[3] == 0xD2) return "leo";
      break;
  }

  return "unknown";
}

String StarSliderRiddle::currentOrderString() const {
  String out;
  for (uint8_t i = 0; i < kReaderCount; i++) {
    if (i > 0) out += "-";
    if (tagValid_[i] && tagSize_[i] >= 4) {
      out += labelForUid(i, tagUid_[i], tagSize_[i]);
    } else {
      out += "none";
    }
  }
  return out;
}

String StarSliderRiddle::readerLabelsJson() const {
  String out = "[";
  for (uint8_t i = 0; i < kReaderCount; i++) {
    if (i > 0) out += ",";
    out += "\"";
    if (tagValid_[i] && tagSize_[i] >= 4) {
      out += labelForUid(i, tagUid_[i], tagSize_[i]);
    } else {
      out += "none";
    }
    out += "\"";
  }
  out += "]";
  return out;
}

String StarSliderRiddle::readerPositionsJson() const {
  String out = "{";
  for (uint8_t i = 0; i < kReaderCount; i++) {
    if (i > 0) out += ",";
    out += "\"r";
    out += String(i);
    out += "\":\"";
    if (tagValid_[i] && tagSize_[i] >= 4) {
      out += labelForUid(i, tagUid_[i], tagSize_[i]);
    } else {
      out += "none";
    }
    out += "\"";
  }
  out += "}";
  return out;
}

String StarSliderRiddle::currentAttemptPositionsJson() const {
  return String("{\"attempt\":") + String(solveAttempts_) + ",\"positions\":" + readerPositionsJson() + "}";
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
  lastAttemptPositions_ = currentAttemptPositionsJson();

  if (isCurrentPatternCorrect()) {
    solvedFlag_ = true;
    solveSuccess_++;
    if (prefs_) {
      prefs_->putBool("solved", true);
      log("INF", "STATE save solved=1");
    }
    log("INF", "pattern SOLVED", currentAttemptPositionsJson());
    publishSolvedEvent(solveAttempts_);
    publishState();
  } else {
    String data = String("{\"attempt\":") + String(solveAttempts_) + ",\"positions\":" + readerPositionsJson() + ",\"uids\":{";
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
    data += "}}";
    log("INF", "pattern WRONG", data);
    publishState();
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
    if (gameActive_ && moduleEnabled_ && ctx_ && ctx_->enabled()) {
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

  String sliderPayload = String("{\"node\":\"star_slider\",\"event\":\"solved\",\"attempt\":") + attemptIdx + "}";
  publish("game/event", sliderPayload, false);

  String skyPayload = String("{\"node\":\"star_sky\",\"event\":\"solved\",\"src\":\"star_slider\",\"attempt\":") + attemptIdx + "}";
  publish("game/event", skyPayload, false);

  const String lightPayload = "{\"cmd\":\"fade_to\",\"lights\":[\"r3_slider\",\"r3_cage\"],\"pct\":100,\"duration_ms\":1000}";
  publish("lighting/cmd", lightPayload, false);
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
  String data =
      String("{\"id\":\"star_slider\"") +
      ",\"mode\":\"" + (gameActive_ ? "ingame" : "standby") + "\"" +
      ",\"enabled\":" + ((gameActive_ && moduleEnabled_ && ctx_->enabled()) ? String("true") : String("false")) +
      ",\"solved\":" + (solvedFlag_ ? "true" : "false") +
      ",\"raw_state\":\"" + (solvedFlag_ ? "solved" : "idle") + "\"" +
      ",\"tries\":" + String(solveAttempts_) +
      ",\"reader_labels\":" + readerLabelsJson() +
      ",\"reader_positions\":" + readerPositionsJson() +
      ",\"last_attempt_positions\":" + (lastAttemptPositions_.length() > 0 ? lastAttemptPositions_ : String("null")) +
      ",\"attempts\":" + String(solveAttempts_) +
      ",\"success\":" + String(solveSuccess_) +
      "}";
  const auto& topics = ctx_->config().topics;
  if (topics.state.length() > 0) {
    publish(topics.state.c_str(), data, true);
  }
}
