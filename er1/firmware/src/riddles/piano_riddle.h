// piano_riddle.h
#pragma once

#include <Arduino.h>
#include <Preferences.h>

#include "core_node.h"
#include "piano_detector.h"

class PianoRiddle {
public:
  void begin(Core::NodeContext& ctx, const char* srcId = nullptr);
  void tick(uint32_t nowMs);
  bool onCmd(const char* cmd, const char* payload);

  // Called from the C callback in piano_riddle.cpp
  void handleDetectorResult(int accepted, const char* pred, float s1, float s2, float margin,
                            float hps_ratio, int harmonic_ok, const char* t1, float t1s,
                            const char* t2, float t2s, const char* t3, float t3s);

private:
  bool publish(const char* topic, const char* type, uint32_t version, const String& dataJson,
               const char* id = nullptr, bool retained = false) const;
  bool publish(const char* topic, const String& payload, bool retained = false) const;

  void publishState();
  void publishSolvedEvent();
  void openLock() const;

  void resetProgress(const char* reason);
  void logCurrentSequence() const;

  void log(const char* level, const String& msg) const;
  void log(const char* level, const String& msg, const String& dataJson) const;

  bool equalsCmd(const char* cmd, const char* ref) const;
  void setModuleEnabled(bool en);

  Core::NodeContext* ctx_ = nullptr;
  Preferences* prefs_ = nullptr;

  String srcId_;
  String topicLockCmd_;

  bool moduleEnabled_ = true;

  bool solved_ = false;
  bool solvedPublished_ = false;

  size_t seqPos_ = 0;
  bool detectorStarted_ = false;

  static constexpr const char* kPrefsSolvedKey = "piano_solved";

  // Sequence (case-insensitive compare)
  static constexpr size_t kSequenceLen = 9;
  static constexpr const char* const kSequence[kSequenceLen] = {
      "c4", "d4", "e4", "f4", "g4", "f4", "e4", "d4", "c4",
  };

  static constexpr size_t kNoteMaxLen = 8;  // incl null terminator
  char played_[kSequenceLen][kNoteMaxLen] = {{0}};
  size_t playedLen_ = 0;
};
