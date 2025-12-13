#pragma once

#include <Arduino.h>
#include <Preferences.h>
#include <MFRC522.h>

#include "core_node.h"

class StarSliderRiddle {
public:
  void begin(Core::NodeContext& ctx);
  void tick(uint32_t nowMs);
  bool onCmd(const char* cmd, const char* payload);

  uint32_t errorCount() const { return errorCount_; }

private:
  static constexpr uint8_t kReaderCount = 3;
  static constexpr uint8_t kRc522RstPin = 22;
  static constexpr uint8_t kRc522SsPins[kReaderCount] = {5, 17, 16};
  static constexpr uint8_t kButtonPin = 25;
  static constexpr uint32_t kBtnDebounceMs = 50;
  static constexpr uint32_t kPollIntervalMs = 150;
  static constexpr uint32_t kMetricIntervalMs = 10000;
  static constexpr uint8_t kUidExpected[kReaderCount][4] = {
      {0xDE, 0xAD, 0xBE, 0x01},
      {0xDE, 0xAD, 0xBE, 0x02},
      {0xDE, 0xAD, 0xBE, 0x03}};

  void log(const char* level, const String& msg);
  void log(const char* level, const String& msg, const String& dataJson);
  void logErr(const String& msg, const String& dataJson = String());

  void pollReaders(uint32_t nowMs);
  void pollReader(uint8_t idx);
  bool uidEquals(const byte* a, const byte* b, uint8_t len) const;
  bool isCurrentPatternCorrect() const;
  void evaluateSolveAttempt();
  void handleButton(uint32_t nowMs);
  void publishSolvedEvent(uint32_t attemptIdx);
  void publishState();
  void publishMetricsIfDue(uint32_t nowMs);

  Core::NodeContext* ctx_ = nullptr;
  Preferences* prefs_ = nullptr;
  MFRC522 readers_[kReaderCount] = {
      MFRC522(kRc522SsPins[0], kRc522RstPin),
      MFRC522(kRc522SsPins[1], kRc522RstPin),
      MFRC522(kRc522SsPins[2], kRc522RstPin)};

  bool tagValid_[kReaderCount] = {false, false, false};
  byte tagUid_[kReaderCount][4] = {{0}};
  uint8_t tagSize_[kReaderCount] = {0};

  bool solvedFlag_ = false;
  uint32_t solveAttempts_ = 0;
  uint32_t solveSuccess_ = 0;
  bool btnPrevState_ = true;
  bool btnWasPressed_ = false;
  uint32_t btnLastChangeMs_ = 0;
  uint32_t lastPollMs_ = 0;
  uint32_t lastMetricMs_ = 0;
  uint32_t errorCount_ = 0;
  String topicDbg_;
};
