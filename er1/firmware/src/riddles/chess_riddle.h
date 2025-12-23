#pragma once

#include <Arduino.h>

#include <MFRC522.h>

#include "core_node.h"

class ChessRiddle {
public:
  ChessRiddle();
  void begin(Core::NodeContext& ctx);
  void tick(uint32_t nowMs);
  bool onCmd(const char* cmd, const char* payload);

  uint32_t errorCount() const { return errorCount_; }

private:
  static constexpr int kReaderCount = 4;
  // Dynamic polling:
  //  - Start slow (1 reader/sec) until close to solved.
  //  - When 3 correct -> speed up.
  //  - When 4 correct -> solved.
  static constexpr uint32_t kPollSlowMs = 1000;  // one reader every 1s -> full scan 4s
  static constexpr uint32_t kPollFastMs = 100;   // one reader every 50ms -> full scan 200ms
  static constexpr uint32_t kCardLostMs = 2000;  // clear reader if no read for 2s
  static constexpr uint32_t kMetricIntervalMs = 1000;
  static constexpr uint32_t kHeartbeatIntervalMs = 5000;
  static constexpr char kTargetUIDs[kReaderCount][9] = {
      "607A512F", "A06B512F", "4015512F", "C06B512F"};

  // RFID wiring (shared SPI with W5500)
  static constexpr uint8_t kRfidCs[kReaderCount]  = {14, 13, 17, 16};
  static constexpr uint8_t kRfidRst[kReaderCount] = {32, 33, 25, 26};

  enum class RiddleState {
    Idle = 0,
    Partial,
    Solved
  };

  void log(const char* level, const String& msg);
  void log(const char* level, const String& msg, const String& dataJson);
  void logErr(const String& msg);

  void initReadersNoSpiBegin();
  void pollOneReader(uint32_t nowMs);
  static String uidToHexUpper(const MFRC522::Uid& uid);
  bool patternCorrect() const;
  bool anyTagPresent() const;
  void evaluatePattern();
  void publishSolvedEvent();
  void openMaglockR3();
  void publishState();
  void publishMetricsIfDue(uint32_t nowMs);
  uint8_t correctCount() const;
  void updatePollInterval();
  void logBoardChange(uint8_t changedReaderIdx);
  const char* uidToPieceNameOrEmpty(const String& uid) const;

  Core::NodeContext* ctx_ = nullptr;
  String topicMetric_;
  String topicLockR3Cmd_;

  // MFRC522 lib keeps CS/RST pins protected, so derive to access them for init.
  struct MFRC522X : public MFRC522 {
    using MFRC522::MFRC522;
    byte csPin() const { return _chipSelectPin; }
    byte rstPin() const { return _resetPowerDownPin; }
  };

  MFRC522X r1_;
  MFRC522X r2_;
  MFRC522X r3_;
  MFRC522X r4_;
  MFRC522X* readers_[kReaderCount] = {&r1_, &r2_, &r3_, &r4_};

  String readerUid_[kReaderCount];
  uint32_t lastSeenMs_[kReaderCount] = {0, 0, 0, 0};
  uint8_t currentReader_ = 0;
  uint32_t lastPollMs_ = 0;
  uint32_t lastMetricMs_ = 0;
  uint32_t lastSolvedMs_ = 0;
  uint32_t solvedCount_ = 0;
  RiddleState riddleState_ = RiddleState::Idle;
  uint32_t errorCount_ = 0;

  uint32_t pollIntervalMs_ = kPollSlowMs;
  bool maglockOpened_ = false;
};
