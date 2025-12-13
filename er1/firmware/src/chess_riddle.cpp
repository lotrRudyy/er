#include "chess_riddle.h"

namespace {
constexpr const char* kTopicMetric = "er1/room2/chess/metric";
constexpr const char* kTopicEvent = "er1/room2/chess/event";
}

void ChessRiddle::begin(Core::NodeContext& ctx) {
  ctx_ = &ctx;
  rfidSerial_->begin(115200, SERIAL_8N1, kUartRxPin, kUartTxPin);
  for (int i = 0; i < kReaderCount; i++) {
    readerUid_[i] = "NONE";
  }
  lastMetricMs_ = millis();
}

void ChessRiddle::tick(uint32_t nowMs) {
  processUart();
  publishMetricsIfDue(nowMs);
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

void ChessRiddle::processUart() {
  while (rfidSerial_->available() > 0) {
    char c = rfidSerial_->read();
    if (c == '\n') {
      if (uartBufPos_ > 0 && uartBufPos_ < kUartBufSize) {
        uartBuf_[uartBufPos_] = '\0';
        applySnapshotTokens(uartBuf_);
      }
      uartBufPos_ = 0;
    } else if (c != '\r') {
      if (uartBufPos_ < kUartBufSize - 1) {
        uartBuf_[uartBufPos_++] = c;
      }
    }
  }
}

void ChessRiddle::applySnapshotTokens(char* line) {
  char* saveptr;
  char* token = strtok_r(line, " \r\n", &saveptr);
  if (!token || strcmp(token, "SNAP") != 0) return;

  String newUid[kReaderCount];
  for (int i = 0; i < kReaderCount; i++) newUid[i] = "NONE";

  while ((token = strtok_r(nullptr, " \r\n", &saveptr)) != nullptr) {
    if (token[0] != 'R') continue;
    int idx = token[1] - '0';
    if (idx < 0 || idx >= kReaderCount) continue;
    if (token[2] != '=') continue;
    const char* val = token + 3;
    if (strcmp(val, "NONE") == 0 || val[0] == '\0') {
      newUid[idx] = "NONE";
    } else {
      String s(val);
      s.toUpperCase();
      newUid[idx] = s;
    }
  }

  bool changed = false;
  for (int i = 0; i < kReaderCount; i++) {
    if (readerUid_[i] != newUid[i]) {
      readerUid_[i] = newUid[i];
      changed = true;
    }
  }
  if (changed) {
    evaluatePattern();
  }
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
  if (!ctx_->enabled()) return;
  bool correct = patternCorrect();
  bool anyPresent = anyTagPresent();

  switch (riddleState_) {
    case RiddleState::Idle:
      if (correct) {
        riddleState_ = RiddleState::Solved;
        lastSolvedMs_ = millis();
        solvedCount_++;
        publishSolvedEvent();
        log("INFO", "CHESS_SOLVED");
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
        log("INFO", "CHESS_SOLVED");
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
}

void ChessRiddle::publishSolvedEvent() {
  if (!ctx_) return;
  String payload = "{\"type\":\"SOLVED\",\"rid\":\"chess\"}";
  ctx_->publish(kTopicEvent, payload);
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

  String payload = String("{\"t\":\"INF\",\"up\":") + String(nowMs / 1000) +
                   ",\"en\":" + (ctx_->enabled() ? "1" : "0") +
                   ",\"solves\":" + solvedCount_ +
                   ",\"pattern\":\"" + patternStr + "\"}";
  ctx_->publish(kTopicMetric, payload);
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

