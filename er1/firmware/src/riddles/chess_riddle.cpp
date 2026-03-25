#include "chess_riddle.h"
#include <cstring>
#include <strings.h>

// W5500 CS pin used by core ethernet bring-up.
// Keep it HIGH whenever touching RFID readers.
#ifndef ETH_CS
#define ETH_CS 15
#endif

ChessRiddle::ChessRiddle()
    : r1_(kRfidCs[0], kRfidRst[0]),
      r2_(kRfidCs[1], kRfidRst[1]),
      r3_(kRfidCs[2], kRfidRst[2]),
      r4_(kRfidCs[3], kRfidRst[3]) {}

void ChessRiddle::begin(Core::NodeContext& ctx) {
  ctx_ = &ctx;

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
  gameActive_ = false;
  moduleEnabled_ = true;
  solveArmedAfterUnsatisfied_ = false;
  startupSolvedBlockLogged_ = false;
  publishState();
}

void ChessRiddle::setGameMode(bool inGame) {
  gameActive_ = inGame;
  resetState(inGame ? "game_enable" : "game_standby");
  publishState();
}

void ChessRiddle::tick(uint32_t nowMs) {
  if (!gameActive_) return;
  if (!moduleEnabled_) return;
  if (!ctx_ || !ctx_->enabled()) return;

  // Round robin: one reader per tick interval
  if (nowMs - lastPollMs_ < perReaderMs_) return;
  lastPollMs_ = nowMs;

  // Keep W5500 deselected while touching RFID.
  digitalWrite(ETH_CS, HIGH);
  // Keep all readers deselected.
  for (int j = 0; j < kReaderCount; j++) digitalWrite(kRfidCs[j], HIGH);

  const uint8_t i = currentReader_;
  bool event = pollOneReader(i, nowMs);
  if (event) {
    // Update speed policy based on how many are correct
    const int cc = correctCount();
    perReaderMs_ = (cc >= 3) ? kPollFastMs : kPollSlowMs;

    // Evaluate riddle state transitions first so state reflects the new puzzle state.
    evaluatePattern();

    // Publish state on every reader event, even if the high-level riddle state did not
    // change, because reader_labels/raw placement changed and the game master expects a
    // fresh chess/state message whenever we log a new reader table.
    publishState();

    // REQUIRED: print FULL TABLE on every event
    logFullTable();
  }

  currentReader_++;
  if (currentReader_ >= kReaderCount) currentReader_ = 0;
}

bool ChessRiddle::onCmd(const char* cmd, const char* payload) {
  if (!cmd) return false;

  if (strcasecmp(cmd, "RESET_CHESS") == 0 || strcasecmp(cmd, "RESET") == 0) {
    resetState("reset_chess");
    publishState();
    return true;
  }

  if (strcasecmp(cmd, "SOLVE") == 0 || strcasecmp(cmd, "SOLVE_CHESS") == 0) {
    riddleState_ = RiddleState::Solved;
    lastSolvedMs_ = millis();
    solvedCount_++;
    publishSolvedEvent();
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

  // Keep same behavior: log unknown commands, but don't crash.
  String message(cmd ? cmd : "");
  if (payload && payload[0]) {
    message += " ";
    message += payload;
  }
  log("WRN", String("Unknown CMD: ") + message);
  return true;
}

void ChessRiddle::initReadersNoSpiBegin() {
  // NodeCore Ethernet already called SPI.begin().
  // MFRC522::PCD_Init() often calls SPI.begin() internally and can trigger
  // ESP32 duplicate APB callback warnings. So we init without SPI.begin().

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
    log("INF", String("RFID init r") + String(i + 1) + " ver=0x" + String(ver, HEX));
  }
}

String ChessRiddle::uidToHexUpper(const MFRC522::Uid& uid) {
  String s;
  s.reserve(uid.size * 2);
  for (byte i = 0; i < uid.size; i++) {
    char b[3];
    snprintf(b, sizeof(b), "%02X", uid.uidByte[i]);
    s += b;
  }
  return s;
}

// IMPORTANT CHANGE:
// MFRC522 PICC_IsNewCardPresent() is edge-triggered (new tag enters field).
// We use it only to detect arrival/swap, and we maintain presence with a
// software state machine (tag still there / removed) using PICC_ReadCardSerial().
bool ChessRiddle::pollOneReader(uint8_t i, uint32_t nowMs) {
  MFRC522& r = *readers_[i];
  bool event = false;

  // Select only this reader
  for (int j = 0; j < kReaderCount; j++) digitalWrite(kRfidCs[j], HIGH);
  digitalWrite(kRfidCs[i], LOW);

  const bool hadTag = (readerUid_[i] != "NONE");
  bool levelPresent = false;  // "tag is present" (level)
  bool uidRead = false;

  // LEVEL PRESENCE CHECK:
  // Use WUPA (WakeupA) so we also detect tags that we previously HALTed.
  // REQA can fail on a halted PICC; WUPA wakes it up.
  byte atqa[2] = {0, 0};
  byte atqaSize = sizeof(atqa);
  MFRC522::StatusCode st = r.PICC_WakeupA(atqa, &atqaSize);
  if (st == MFRC522::STATUS_OK || st == MFRC522::STATUS_COLLISION) {
    levelPresent = true;
    lastSeenMs_[i] = nowMs;  // refresh presence even if UID read fails this poll

    // Try to read UID whenever presence is detected.
    // This handles: boot with tag already present, steady tags, and swaps.
    if (r.PICC_ReadCardSerial()) {
      String uid = uidToHexUpper(r.uid);
      if (uid.length() > 8) uid = uid.substring(0, 8);
      uidRead = true;

      if (!hadTag || readerUid_[i] != uid) {
        readerUid_[i] = uid;
        event = true;  // arrival or swap
      }

      r.PICC_HaltA();
      r.PCD_StopCrypto1();
    }
  }

  // REMOVAL DETECTION:
  // If we previously had a tag and REQA no longer sees one for >=2s, clear it.
  if (!levelPresent && hadTag) {
    if (nowMs - lastSeenMs_[i] >= kCardLostMs) {
      readerUid_[i] = "NONE";
      event = true;
    }
  }

  // Deselect reader after poll
  digitalWrite(kRfidCs[i], HIGH);
  (void)uidRead; // kept for future diagnostics
  return event;
}

bool ChessRiddle::patternCorrect() const {
  for (int i = 0; i < kReaderCount; i++) {
    if (readerUid_[i] != kTargetUIDs[i]) return false;
  }
  return true;
}

int ChessRiddle::correctCount() const {
  int c = 0;
  for (int i = 0; i < kReaderCount; i++) {
    if (readerUid_[i] == kTargetUIDs[i]) c++;
  }
  return c;
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

  if (!solveArmedAfterUnsatisfied_) {
    if (!correct) {
      solveArmedAfterUnsatisfied_ = true;
      startupSolvedBlockLogged_ = false;
      log("INF", "CHESS_SOLVE_ARMED", "{\"reason\":\"pattern_became_incorrect\"}");
    } else {
      if (!startupSolvedBlockLogged_) {
        startupSolvedBlockLogged_ = true;
        log("INF", "CHESS_SOLVE_BLOCKED", "{\"reason\":\"await_first_incorrect_state\"}");
      }

      const RiddleState prevState = riddleState_;
      riddleState_ = anyPresent ? RiddleState::Partial : RiddleState::Idle;
      if (prevState != riddleState_) {
        publishState();
      }
      return;
    }
  }

  const RiddleState prevState = riddleState_;
  switch (riddleState_) {
    case RiddleState::Idle:
      if (correct) {
        riddleState_ = RiddleState::Solved;
        lastSolvedMs_ = millis();
        solvedCount_++;
        publishSolvedEvent();
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
  publish("game/event", "{\"node\":\"chess\",\"event\":\"solved\"}", false);
}

void ChessRiddle::resetState(const char* reason) {
  bool wasSolved = (riddleState_ == RiddleState::Solved);
  for (int i = 0; i < kReaderCount; i++) {
    readerUid_[i] = "NONE";
    lastSeenMs_[i] = 0;
  }
  currentReader_ = 0;
  perReaderMs_ = kPollSlowMs;
  riddleState_ = RiddleState::Idle;
  lastSolvedMs_ = 0;
  solvedCount_ = 0;
  solveArmedAfterUnsatisfied_ = false;
  startupSolvedBlockLogged_ = false;
  const char* src = (reason && reason[0]) ? reason : "reset";
  String data = String("{\"src\":\"") + src + "\",\"was_solved\":" + (wasSolved ? "1" : "0") + "}";
  log("INF", "CHESS_STATE_RESET", data);
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

  const String data =
      String("{\"id\":\"chess\"") +
      ",\"mode\":\"" + (gameActive_ ? "ingame" : "standby") + "\"" +
      ",\"enabled\":" + ((gameActive_ && moduleEnabled_ && ctx_->enabled()) ? String("true") : String("false")) +
      ",\"solved\":" + ((riddleState_ == RiddleState::Solved) ? String("true") : String("false")) +
      ",\"armed_after_unsatisfied\":" + (solveArmedAfterUnsatisfied_ ? String("true") : String("false")) +
      ",\"raw_state\":\"" + stateName + "\"" +
      ",\"state\":\"" + stateName + "\"" +
      ",\"solved_count\":" + solvedCount_ +
      ",\"reader_labels\":" + readerLabelsJson() +
      "}";

  const auto& topics = ctx_->config().topics;
  if (topics.state.length() > 0) {
    publish(topics.state.c_str(), data, true);
  }
}



// ===== Required FULL TABLE formatting =====

const char* ChessRiddle::expectedLabelForReader(uint8_t i) {
  // Reader order is fixed:
  // R1=QUEEN, R2=HORSE, R3=ROOK, R4=KING
  switch (i) {
    case 0: return "QUEEN";
    case 1: return "HORSE";
    case 2: return "ROOK";
    case 3: return "KING";
    default: return "???";
  }
}

const char* ChessRiddle::presentLabelFromUid(const String& uid) {
  if (uid == "NONE" || uid.length() == 0) return "EMPTY";
  if (uid == "607A512F") return "KING";
  if (uid == "A06B512F") return "QUEEN";
  if (uid == "4015512F") return "ROOK";
  if (uid == "C06B512F") return "HORSE";
  return "UNKNOWN";
}

void ChessRiddle::logFullTable() const {
  if (!ctx_) return;

  Core::TimestampFields tsFields = {};
  String datePart;
  String timePart;
  auto* tsSource = ctx_->timestampSource();
  if (tsSource) {
    tsFields = tsSource->currentTimestamp();
    if (tsFields.timeValid && tsFields.ts[0] != '\0') {
      String tsStr(tsFields.ts);
      int spaceIdx = tsStr.indexOf(' ');
      if (spaceIdx > 0) {
        datePart = tsStr.substring(0, spaceIdx);
        timePart = tsStr.substring(spaceIdx + 1);
      }
    }
  }

  auto padded = [](const char* label) {
    String s(label ? label : "");
    while (s.length() < 5) s += " ";
    return s;
  };

  String msg;
  msg.reserve(256);
  msg += "---------------------------------------------------------------------------\n";
  msg += "chess/log INF ";
  if (timePart.length() > 0) {
    msg += timePart;
    if (datePart.length() > 0) {
      msg += " - ";
      msg += datePart;
    }
  } else {
    msg += "(time_unknown)";
  }
  msg += "\n---------------------------------------------------------------------------\n";

  for (int i = 0; i < kReaderCount; i++) {
    const String goal = padded(expectedLabelForReader(i));
    const String now = padded(presentLabelFromUid(readerUid_[i]));
    msg += "Reader ";
    msg += String(i + 1);
    msg += " (rst=";
    msg += String(kRfidRst[i]);
    msg += " cs=";
    msg += String(kRfidCs[i]);
    msg += ") | GOAL: ";
    msg += goal;
    msg += " | NOW : ";
    msg += now;
    if (i + 1 < kReaderCount) msg += "\n";
  }

  log("INF", msg);
}

String ChessRiddle::readerLabelsJson() const {
  String out = "{";
  out += "\"queen\":\"";
  out += presentLabelFromUid(readerUid_[0]);
  out += "\",\"knight\":\"";
  out += presentLabelFromUid(readerUid_[1]);
  out += "\",\"rook\":\"";
  out += presentLabelFromUid(readerUid_[2]);
  out += "\",\"king\":\"";
  out += presentLabelFromUid(readerUid_[3]);
  out += "\"}";
  return out;
}

void ChessRiddle::log(const char* level, const String& msg) const {
  if (!ctx_) return;
  ctx_->log(level, msg);
}

void ChessRiddle::log(const char* level, const String& msg, const String& dataJson) const {
  if (!ctx_) return;
  ctx_->log(level, msg, dataJson);
}

bool ChessRiddle::publish(const char* topic, const char* type, uint32_t version, const String& dataJson,
                          const char* id, bool retained) const {
  if (!ctx_) return false;
  return ctx_->publishEnvelope(topic, type, version, dataJson, id, retained);
}

bool ChessRiddle::publish(const char* topic, const String& payload, bool retained) const {
  if (!ctx_) return false;
  return ctx_->publish(topic, payload, retained);
}
