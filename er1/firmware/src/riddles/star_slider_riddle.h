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
  static constexpr uint8_t kRc522RstPins[kReaderCount] = {26, 32, 33};
  static constexpr uint8_t kRc522SsPins[kReaderCount] = {5, 17, 16};
  static constexpr uint8_t kButtonPin = 25;
  static constexpr uint32_t kBtnDebounceMs = 50;
  static constexpr uint32_t kPollIntervalMs = 150;      // kept (unused now; button-only evaluation)
  static constexpr uint32_t kMetricIntervalMs = 10000;
  static constexpr uint8_t kUidExpected[kReaderCount][4] = {
    {0x5A, 0xDF, 0x53, 0xD2}, // r0
    {0x6A, 0x88, 0x4F, 0xD2}, // r1
    {0xFA, 0x51, 0x53, 0xD2}  // r2
  };

  void log(const char* level, const String& msg) const;
  void log(const char* level, const String& msg, const String& dataJson) const;
  bool publish(const char* topic, const char* type, uint32_t version, const String& dataJson,
               const char* id = nullptr, bool retained = false) const;
  bool publish(const char* topic, const String& payload, bool retained = false) const;

  void pollReaders(uint32_t nowMs);     // kept for compatibility; NOT used now
  void pollReader(uint8_t idx);         // used ONLY from evaluateSolveAttempt()
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
    MFRC522(kRc522SsPins[0], kRc522RstPins[0]),
    MFRC522(kRc522SsPins[1], kRc522RstPins[1]),
    MFRC522(kRc522SsPins[2], kRc522RstPins[2])};


  bool tagValid_[kReaderCount] = {false, false, false};
  byte tagUid_[kReaderCount][4] = {{0}};
  uint8_t tagSize_[kReaderCount] = {0};

  bool solvedFlag_ = false;
  uint32_t solveAttempts_ = 0;
  uint32_t solveSuccess_ = 0;
  bool btnPrevState_ = true;
  bool btnWasPressed_ = false;
  uint32_t btnLastChangeMs_ = 0;
  uint32_t lastPollMs_ = 0;         // kept (unused now; button-only evaluation)
  uint32_t lastMetricMs_ = 0;
  mutable uint32_t errorCount_ = 0;
};
