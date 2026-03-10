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
  void setGameMode(bool inGame);

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
    {0x3A, 0x55, 0x55, 0xD2}, // r0 scorpio (pov von spieler: rechts)
    {0x9A, 0x71, 0x4C, 0xD2}, // r1 aquarius
    {0x3A, 0x09, 0x51, 0xD2}  // r2 libra (pov von spieler: links)
  };

  static constexpr size_t kAttemptHistoryMax = 64;
  static constexpr size_t kAttemptStringMax = 64;

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
  void resetState(const char* reason);
  void publishState();
  void publishMetricsIfDue(uint32_t nowMs);

  const char* labelForUid(const byte* uid, uint8_t len) const;
  String currentOrderString() const;
  String attemptedStarSignsJson() const;
  String readerLabelsJson() const;
  void appendAttemptedOrder();

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
  bool gameActive_ = false;
  bool moduleEnabled_ = true;

  char attemptedStarSigns_[kAttemptHistoryMax][kAttemptStringMax] = {{0}};
  size_t attemptedStarSignsCount_ = 0;
};
