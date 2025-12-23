#include "chess_riddle.h"

// W5500 CS pin used by core ethernet bring-up.
// We keep it HIGH whenever touching the RFID readers.
#ifndef ETH_CS
#define ETH_CS 15
#endif

ChessRiddle::ChessRiddle()
    : r1_(kRfidCs[0], kRfidRst[0]),
      r2_(kRfidCs[1], kRfidRst[1]),
      r3_(kRfidCs[2], kRfidRst[2]),
      r4_(kRfidCs[3], kRfidRst[3]) {
  // readers_ already points to r1_..r4_ via in-class init.
}

void ChessRiddle::begin(Core::NodeContext& ctx) {
  ctx_ = &ctx;
  const char* node = ctx.nodeId() ? ctx.nodeId() : "chess";
  (void)node;
  topicLockR3Cmd_ = Core::topic("maglock", "lock/r3/cmd");

  // Ensure all CS pins are driven HIGH before any RFID init.
  pinMode(ETH_CS, OUTPUT);
  digitalWrite(ETH_CS, HIGH);
  for (int i = 0; i < kReaderCount; i++) {
    pinMode(kRfidCs[i], OUTPUT);
    digitalWrite(kRfidCs[i], HIGH);
    readerUid_[i] = "NONE";
    lastSeenMs_[i] = 0;
  }

  initReadersNoSpiBegin();

  lastPollMs_ = millis();
  lastMetricMs_ = millis();
  pollIntervalMs_ = kPollSlowMs;
  maglockOpened_ = false;
  publishState();
}

void ChessRiddle::tick(uint32_t nowMs) {
  pollOneReader(nowMs);
  publishMetricsIfDue(nowMs);
}

uint8_t ChessRiddle::correctCount() const {
  uint8_t c = 0;
  for (int i = 0; i < kReaderCount; i++) {
    if (readerUid_[i] == kTargetUIDs[i]) c++;
  }
  return c;
}

void ChessRiddle::updatePollInterval() {
  const uint8_t c = correctCount();
  // Start slow, speed up as we get close.
  if (c < 3) pollIntervalMs_ = kPollSlowMs;
  else pollIntervalMs_ = kPollFastMs;
}

const char* ChessRiddle::uidToPieceNameOrEmpty(const String& uid) const {
  if (uid.length() == 0 || uid == "NONE") return "";
  // Map the 4 known target UIDs to names. Order: Horse/Rook/Queen/King.
  if (uid == kTargetUIDs[0]) return "Horse";
  if (uid == kTargetUIDs[1]) return "Rook";
  if (uid == kTargetUIDs[2]) return "Queen";
  if (uid == kTargetUIDs[3]) return "King";
  return "";
}

void ChessRiddle::logBoardChange(uint8_t changedReaderIdx) {
  if (!ctx_) return;
  // Format requested by you:
  // Reader N (rst=.. cs=..) Horse/Rook/Queen/King: v1/v2/v3/v4
  // Where vK is the piece name detected on reader K ("" if none/unknown).
  const uint8_t cs = kRfidCs[changedReaderIdx];
  const uint8_t rst = kRfidRst[changedReaderIdx];

  String v1(uidToPieceNameOrEmpty(readerUid_[0]));
  String v2(uidToPieceNameOrEmpty(readerUid_[1]));
  String v3(uidToPieceNameOrEmpty(readerUid_[2]));
  String v4(uidToPieceNameOrEmpty(readerUid_[3]));

  // Convert empty to "" explicitly.
  if (v1.length() == 0) v1 = "\"\"";
  if (v2.length() == 0) v2 = "\"\"";
  if (v3.length() == 0) v3 = "\"\"";
  if (v4.length() == 0) v4 = "\"\"";

  String msg;
  msg.reserve(96);
  msg += "Reader ";
  msg += String(changedReaderIdx + 1);
  msg += " (rst=";
  msg += String(rst);
  msg += " cs=";
  msg += String(cs);
  msg += ") Horse/Rook/Queen/King: ";
  msg += v1; msg += "/";
  msg += v2; msg += "/";
  msg += v3; msg += "/";
  msg += v4;

  log("INF", msg);
}

bool ChessRiddle::onCmd(const char* cmd, const char* payload) {
  String message(cmd ? cmd : "");
  if (payload && payload[0]) {
    message += " ";
    message += payload;
  }
  log("WRN", String("Unknown CMD: ") + message);
  return true;
}

void ChessRiddle::initReadersNoSpiBegin() {
  // IMPORTANT: NodeCore's MQTT brings up Ethernet and calls SPI.begin() already.
  // MFRC522::PCD_Init() calls SPI.begin() internally (many versions) and can
  // trigger the ESP32 duplicate APB callback warning. So we replicate init
  // without calling SPI.begin().

  for (int i = 0; i < kReaderCount; i++) {
    auto* r = readers_[i];

    // Deselect everything while toggling reset.
    digitalWrite(ETH_CS, HIGH);
    for (int j = 0; j < kReaderCount; j++) digitalWrite(kRfidCs[j], HIGH);

    pinMode(r->csPin(), OUTPUT);
    digitalWrite(r->csPin(), HIGH);

    pinMode(r->rstPin(), OUTPUT);
    digitalWrite(r->rstPin(), LOW);
    delay(50);
    digitalWrite(r->rstPin(), HIGH);
    delay(50);

    r->PCD_Reset();

    // Same defaults used by the MFRC522 library init path.
    r->PCD_WriteRegister(MFRC522::TModeReg, 0x80);
    r->PCD_WriteRegister(MFRC522::TPrescalerReg, 0xA9);
    r->PCD_WriteRegister(MFRC522::TReloadRegH, 0x03);
    r->PCD_WriteRegister(MFRC522::TReloadRegL, 0xE8);
    r->PCD_WriteRegister(MFRC522::TxASKReg, 0x40);
    r->PCD_WriteRegister(MFRC522::ModeReg, 0x3D);
    r->PCD_SetAntennaGain(MFRC522::RxGain_max);
    r->PCD_AntennaOn();

    delay(10);

    const byte ver = r->PCD_ReadRegister(MFRC522::VersionReg);
    log("INF", String("RFID init r") + i + " ver=0x" + String(ver, HEX));
  }
}

String ChessRiddle::uidToHexUpper(const MFRC522::Uid& uid) {
  String s;
  s.reserve(uid.size * 2);
  for (byte i = 0; i < uid.size; i++) {
    if (uid.uidByte[i] < 0x10) s += '0';
    s += String(uid.uidByte[i], HEX);
  }
  s.toUpperCase();
  return s;
}

void ChessRiddle::pollOneReader(uint32_t nowMs) {
  if (nowMs - lastPollMs_ < pollIntervalMs_) return;
  lastPollMs_ = nowMs;

  // Keep W5500 deselected while touching RFID.
  digitalWrite(ETH_CS, HIGH);
  // Keep other readers deselected.
  for (int j = 0; j < kReaderCount; j++) digitalWrite(kRfidCs[j], HIGH);

  const uint8_t i = currentReader_;
  MFRC522& r = *readers_[i];

  bool changed = false;
  bool tagEvent = false;

  if (r.PICC_IsNewCardPresent() && r.PICC_ReadCardSerial()) {
    const String uid = uidToHexUpper(r.uid);
    lastSeenMs_[i] = nowMs;
    if (readerUid_[i] != uid) {
      readerUid_[i] = uid;
      changed = true;
      tagEvent = true;
      // Log only on changes: new/different tag.
      log("INF", String("RFID r") + i + " uid=" + uid);
    }

    r.PICC_HaltA();
    r.PCD_StopCrypto1();
  } else {
    // Clear if we haven't seen the tag in a while.
    if (readerUid_[i] != "NONE" && (nowMs - lastSeenMs_[i] > kCardLostMs)) {
      readerUid_[i] = "NONE";
      changed = true;
      tagEvent = true;
      log("INF", String("RFID r") + i + " uid=NONE");
    }
  }

  if (changed) {
    updatePollInterval();
    if (tagEvent) {
      logBoardChange(i);
    }
    evaluatePattern();
  }

  currentReader_++;
  if (currentReader_ >= kReaderCount) currentReader_ = 0;
}

bool ChessRiddle::patternCorrect() const {
  for (int i = 0; i < kReaderCount; i++) {
    if (readerUid_[i] != kTargetUIDs[i]) {
      return false;
    }
  }
  return true;
}

bool ChessRiddle::anyTagPresent() const {
  for (int i = 0; i < kReaderCount; i++) {
    if (readerUid_[i] != "NONE") return true;
  }
  return false;
}

void ChessRiddle::evaluatePattern() {
  if (!ctx_ || !ctx_->enabled()) return;
  const bool correct = patternCorrect();
  const bool anyPresent = anyTagPresent();

  // Allow re-opening on a new future solve.
  if (!correct) {
    maglockOpened_ = false;
  }

  const RiddleState prevState = riddleState_;
  switch (riddleState_) {
    case RiddleState::Idle:
      if (correct) {
        riddleState_ = RiddleState::Solved;
        lastSolvedMs_ = millis();
        solvedCount_++;
        publishSolvedEvent();
        openMaglockR3();
        log("INF", "CHESS_SOLVED");
      } else if (anyPresent) {
        riddleState_ = RiddleState::Partial;
      }
      break;
    case RiddleState::Partial:
      if (correct) {
        riddleState_ = RiddleState::Solved;
        lastSolvedMs_ = millis();
        solvedCount_++;
        publishSolvedEvent();
        openMaglockR3();
        log("INF", "CHESS_SOLVED");
      } else if (!anyPresent) {
        riddleState_ = RiddleState::Idle;
      }
      break;
    case RiddleState::Solved:
      if (!correct) {
        riddleState_ = anyPresent ? RiddleState::Partial : RiddleState::Idle;
      }
      break;
  }

  if (prevState != riddleState_) {
    publishState();
  }
}

void ChessRiddle::publishSolvedEvent() {
  if (!ctx_) return;
  const String payload = "{\"id\":\"chess\"}";
  ctx_->publishEvent("riddle_solved", payload);
  publishState();
}

void ChessRiddle::openMaglockR3() {
  if (!ctx_) return;
  if (maglockOpened_) return;
  if (topicLockR3Cmd_.length() == 0) return;
  ctx_->publish(topicLockR3Cmd_.c_str(), "OPEN");
  maglockOpened_ = true;
  log("INF", "Sent OPEN to maglock r3");
}

void ChessRiddle::publishState() {
  if (!ctx_) return;
  const char* stateName = "idle";
  switch (riddleState_) {
    case RiddleState::Partial: stateName = "partial"; break;
    case RiddleState::Solved:  stateName = "solved"; break;
    case RiddleState::Idle:
    default: stateName = "idle"; break;
  }
  const String data = String("{\"state\":\"") + stateName +
                      "\",\"solved_count\":" + solvedCount_ +
                      ",\"enabled\":" + (ctx_->enabled() ? "true" : "false") + "}";
  ctx_->publishState(data, true);
}

void ChessRiddle::publishMetricsIfDue(uint32_t nowMs) {
  if (!ctx_) return;
  if (nowMs - lastMetricMs_ < kMetricIntervalMs) return;
  lastMetricMs_ = nowMs;

  String patternStr;
  for (int i = 0; i < kReaderCount; i++) {
    if (i > 0) patternStr += ",";
    patternStr += readerUid_[i];
  }

  // Emit as DBG log so it can be suppressed with <node>/log/level.
  const String data = String("{\"en\":") + (ctx_->enabled() ? "1" : "0") +
                      ",\"solves\":" + solvedCount_ +
                      ",\"pattern\":\"" + patternStr + "\"}";
  ctx_->log("DBG", "chess_metrics", data);
}

void ChessRiddle::log(const char* level, const String& msg) {
  if (!ctx_) return;
  ctx_->log(level, msg);
}

void ChessRiddle::log(const char* level, const String& msg, const String& dataJson) {
  if (!ctx_) return;
  ctx_->log(level, msg, dataJson);
}

void ChessRiddle::logErr(const String& msg) {
  errorCount_++;
  log("ERR", msg);
}
