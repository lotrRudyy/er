#pragma once

#include <Arduino.h>

#include "core_node.h"

class ChessRiddle {
public:
  void begin(Core::NodeContext& ctx);
  void tick(uint32_t nowMs);
  bool onCmd(const char* cmd, const char* payload);

  uint32_t errorCount() const { return errorCount_; }

private:
  static constexpr int kReaderCount = 4;
  static constexpr uint32_t kMetricIntervalMs = 1000;
  static constexpr uint32_t kHeartbeatIntervalMs = 5000;
  static constexpr char kTargetUIDs[kReaderCount][9] = {
      "607A512F", "A06B512F", "4015512F", "C06B512F"};
  static constexpr uint8_t kUartRxPin = 26;
  static constexpr uint8_t kUartTxPin = 25;
  static constexpr size_t kUartBufSize = 128;

  enum class RiddleState {
    Idle = 0,
    Partial,
    Solved
  };

  void log(const char* level, const String& msg);
  void log(const char* level, const String& msg, const String& dataJson);
  void logErr(const String& msg);

  void processUart();
  void applySnapshotTokens(char* line);
  bool patternCorrect() const;
  bool anyTagPresent() const;
  void evaluatePattern();
  void publishSolvedEvent();
  void publishState();
  void publishMetricsIfDue(uint32_t nowMs);

  Core::NodeContext* ctx_ = nullptr;
  String topicMetric_;
  HardwareSerial* rfidSerial_ = &Serial2;
  String readerUid_[kReaderCount];
  uint32_t lastMetricMs_ = 0;
  uint32_t lastSolvedMs_ = 0;
  uint32_t solvedCount_ = 0;
  RiddleState riddleState_ = RiddleState::Idle;
  char uartBuf_[kUartBufSize];
  size_t uartBufPos_ = 0;
  uint32_t errorCount_ = 0;
};
