#pragma once

#include <Arduino.h>
#include <Preferences.h>

#include "core_node.h"
#include "piano_detector.h"

class PianoRiddle {
public:
  void begin(Core::NodeContext& ctx, const char* nodeId = nullptr, Core::Logger* logger = nullptr);
  void tick(uint32_t nowMs);
  bool onCmd(const char* cmd, const char* payload);
  void handleDetectorResult(int accepted, const char* pred, float s1, float s2, float margin,
                            float hps_ratio, int harmonic_ok, const char* t1, float t1s,
                            const char* t2, float t2s, const char* t3, float t3s);

private:
  void publishState();
  void publishSolvedEvent();
  void openLock() const;
  void resetProgress(const char* reason);
  void log(const char* level, const String& msg) const;
  void log(const char* level, const String& msg, const String& dataJson) const;
  bool equalsCmd(const char* cmd, const char* ref) const;
  void setModuleEnabled(bool en);

  Core::NodeContext* ctx_ = nullptr;
  Core::Logger* logger_ = nullptr;
  Preferences* prefs_ = nullptr;

  String nodeId_;
  String topicEvt_;
  String topicState_;
  String topicLockCmd_;
  bool moduleEnabled_ = true;
  bool solved_ = false;
  bool solvedPublished_ = false;
  size_t seqPos_ = 0;
  uint32_t lastAcceptedMs_ = 0;
  bool detectorStarted_ = false;

  static constexpr uint32_t kNoteTimeoutMs = 3000;
  static constexpr const char* kPrefsSolvedKey = "piano_solved";
  static constexpr size_t kSequenceLen = 9;
  static constexpr const char* kSequence[kSequenceLen];
};
