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
  static constexpr uint32_t kPollIntervalMs = 500;
  static constexpr uint32_t kMetricIntervalMs = 10000;
  static constexpr uint8_t kUidExpected[kReaderCount][4] = {
    {0x3A, 0x55, 0x55, 0xD2}, // r0 scorpio (pov von spieler: rechts)
    {0x9A, 0x71, 0x4C, 0xD2}, // r1 aquarius
    {0x3A, 0x09, 0x51, 0xD2}  // r2 libra (pov von spieler: links)
  };

  static constexpr size_t kAttemptStringMax = 128;

/*
  1: skorpion      "r0":"3a-55-55-d2","r1":"1a-b8-4d-d2","r2":"7a-dc-4f-d2"
  2: gemini        "r0":"5a-df-53-d2","r1":"6a-88-4f-d2","r2":"fa-51-53-d2"
  3: libera        "r0":"10-57-51-2f","r1":"4a-e7-4b-d2","r2":"3a-09-51-d2"
  4: sagittarius   "r0":"4a-72-4e-d2","r1":"5a-11-4f-d2","r2":"4a-8f-4e-d2"
  5: aquarius      "r0":"10-1e-51-2f","r1":"9a-71-4c-d2","r2":"7a-4b-4e-d2"
  6: pisces        "r0":"da-47-50-d2","r1":"fa-44-4f-d2","r2":"3a-f3-4e-d2"
  7: leo           "r0":"ea-99-4f-d2","r1":"5a-7a-4c-d2","r2":"9a-1e-4b-d2"
*/

  void log(const char* level, const String& msg) const;
  void log(const char* level, const String& msg, const String& dataJson) const;
  bool publish(const char* topic, const char* type, uint32_t version, const String& dataJson,
               const char* id = nullptr, bool retained = false) const;
  bool publish(const char* topic, const String& payload, bool retained = false) const;

  void pollReaders(uint32_t nowMs);
  void pollReader(uint8_t idx);         // used ONLY from evaluateSolveAttempt()
  bool uidEquals(const byte* a, const byte* b, uint8_t len) const;
  bool isCurrentPatternCorrect() const;
  void evaluateSolveAttempt();
  void handleButton(uint32_t nowMs);
  void publishSolvedEvent(uint32_t attemptIdx);
  void resetState(const char* reason);
  void publishState();
  void publishMetricsIfDue(uint32_t nowMs);

  const char* labelForUid(uint8_t readerIdx, const byte* uid, uint8_t len) const;
  String currentOrderString() const;
  String readerLabelsJson() const;
  String readerPositionsJson() const;
  String currentAttemptPositionsJson() const;

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
  uint32_t lastPollMs_ = 0;
  uint32_t lastMetricMs_ = 0;
  mutable uint32_t errorCount_ = 0;
  bool gameActive_ = false;
  bool moduleEnabled_ = true;

  String lastAttemptPositions_;
  String lastPublishedReaderOrder_;
};
