// piano_riddle.h
#pragma once

#include <Arduino.h>
#include <Preferences.h>

#include "core_node.h"

extern "C" {
void piano_detector_setup();
void piano_detector_loop_once();
}

class PianoRiddle {
public:
  void begin(Core::NodeContext& ctx, const char* srcId = "piano");
  void tick(uint32_t nowMs);
  bool onCmd(const char* cmd, const char* payload);

private:
  static constexpr const char* kPrefsSolvedKey = "piano_solved";

  static constexpr size_t kSequenceLen = 8;
  static constexpr size_t kNoteMaxLen = 8;

  static constexpr const char* const kSequence[kSequenceLen] = {
    "c4","d4","e4","f4","g4","f4","e4","d4"
  };

  void handleDetectorResult(int accepted, const char* pred, float s1, float s2, float margin, float hps_ratio,
                            int harmonic_ok, const char* t1, float t1s, const char* t2, float t2s, const char* t3,
                            float t3s);

  void onAcceptedNote(const char* predSafe);

  void publishState();
  void publishSolvedEvent();
  void openLock() const;

  void resetProgress(const char* reason);
  void logCurrentSequence() const;

  bool publish(const char* topic, const char* type, uint32_t version, const String& dataJson, const char* id = nullptr,
               bool retained = false) const;
  bool publish(const char* topic, const String& payload, bool retained = false) const;

  void log(const char* level, const String& msg) const;
  void log(const char* level, const String& msg, const String& dataJson) const;

  bool equalsCmd(const char* cmd, const char* ref) const;
  void setModuleEnabled(bool en);

  Core::NodeContext* ctx_ = nullptr;
  Preferences* prefs_ = nullptr;

  bool detectorStarted_ = false;
  bool moduleEnabled_ = true;

  String srcId_;
  String topicLockCmd_;

  bool solved_ = false;
  bool solvedPublished_ = false;

  // progress in [0..kSequenceLen]
  size_t seqPos_ = 0;

  // played notes we are currently matching (length == seqPos_)
  char played_[kSequenceLen][kNoteMaxLen] = {};
  size_t playedLen_ = 0;
};
