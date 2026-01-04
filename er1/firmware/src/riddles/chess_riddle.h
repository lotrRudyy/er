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

  // Polling policy:
  // - <3 correct => 1000ms per reader
  // - >=3 correct => 100ms per reader (max safe speed you requested)
  static constexpr uint32_t kPollSlowMs = 200;
  static constexpr uint32_t kPollFastMs = 100;

  // Clear reader if we haven't confirmed presence for >=2s
  static constexpr uint32_t kCardLostMs = 2000;

  // RFID wiring (shared SPI with W5500)
  static constexpr uint8_t kRfidCs[kReaderCount]  = {14, 13, 17, 16};
  static constexpr uint8_t kRfidRst[kReaderCount] = {32, 33, 25, 26};

  // Expected correct UID per reader (Reader1..4):
  // R1=QUEEN, R2=HORSE, R3=ROOK, R4=KING
  static constexpr char kTargetUIDs[kReaderCount][9] = {
      "A06B512F", // QUEEN
      "C06B512F", // HORSE
      "4015512F", // ROOK
      "607A512F"  // KING
  };

  enum class RiddleState { Idle = 0, Partial, Solved };

  // Logging helpers
  void log(const char* level, const String& msg) const;
  void log(const char* level, const String& msg, const String& dataJson) const;
  bool publish(const char* topic, const char* type, uint32_t version, const String& dataJson,
               const char* id = nullptr, bool retained = false) const;
  bool publish(const char* topic, const String& payload, bool retained = false) const;

  // RFID
  void initReadersNoSpiBegin();
  bool pollOneReader(uint8_t i, uint32_t nowMs); // returns true if EVENT happened
  static String uidToHexUpper(const MFRC522::Uid& uid);

  // Pattern/state
  bool patternCorrect() const;
  int correctCount() const;
  bool anyTagPresent() const;
  void evaluatePattern();
  void publishSolvedEvent();
  void publishState();
  void resetState(const char* reason);

  // Event logging format (FULL TABLE)
  static const char* expectedLabelForReader(uint8_t i);
  static const char* presentLabelFromUid(const String& uid);
  void logFullTable() const;

  Core::NodeContext* ctx_ = nullptr;

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

  String readerUid_[kReaderCount];        // "NONE" or 8-hex UID
  uint32_t lastSeenMs_[kReaderCount] = {0, 0, 0, 0};

  uint8_t currentReader_ = 0;
  uint32_t lastPollMs_ = 0;
  uint32_t perReaderMs_ = kPollSlowMs;

  uint32_t lastSolvedMs_ = 0;
  uint32_t solvedCount_ = 0;
  RiddleState riddleState_ = RiddleState::Idle;
  uint32_t errorCount_ = 0;
};
