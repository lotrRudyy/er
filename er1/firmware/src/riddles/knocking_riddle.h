#pragma once

#include <Arduino.h>
#include <DFRobotDFPlayerMini.h>

#include "core_node.h"

class KnockingRiddle {
public:
  void begin(Core::NodeContext& ctx);
  void tick(uint32_t nowMs);
  bool onCmd(const char* cmd, const char* payload);

  bool dfReady() const { return dfOk_; }
  uint32_t errorCount() const { return errorCount_; }
  bool shouldAllowLog(const char* level);

private:
  static constexpr bool kDevLog = true;
  static constexpr uint32_t kHbIntervalMs = 5000;
  static constexpr int kSensorCount = 3;
  static constexpr int kPiezoPins[kSensorCount] = {32, 33, 34};
  static constexpr uint16_t kKnockRawThreshold = 1200;
  static constexpr uint16_t kKnockThresholds[kSensorCount] = {900, 500, 1200};
  static constexpr uint32_t kKnockDebounceMs = 200;
  static constexpr uint32_t kKnockWindowMs = 40;
  static constexpr int kSeqExpectLen = 9;
  static constexpr int kSeqExpect[kSeqExpectLen] = {0, 0, 0, 0, 1, 1, 2, 2, 2};
  static constexpr int kSeqMaxLen = 16;
  static constexpr uint32_t kSeqTimeoutMs = 3000;
  static constexpr uint8_t kSoundQueueMax = 16;
  static constexpr uint8_t kDfVolume = 30;

  struct PiezoState {
    int pin;
    uint32_t sum;
    uint16_t samples;
    uint16_t avg;
    uint16_t base;
    uint16_t maxVal;
    uint16_t lastRaw;
    static const int kBuckets = 10;
    uint16_t bucketMax[kBuckets];
  };

  void log(const char* level, const String& msg);
  void log(const char* level, const String& msg, const String& dataJson);
  void logErr(const String& msg, const String& data = String());

  void initPiezo();
  void updatePiezoSamples(uint32_t nowMs);
  void handleKnockWindow(uint32_t nowMs);
  void registerKnock(int idx, uint16_t raw);
  void playKnockSound(int idx);
  void enqueueSound(uint8_t track);
  bool soundQueueEmpty() const;
  bool soundQueueFull() const;
  unsigned long trackFallbackMs(uint8_t track) const;
  void serviceSound(uint32_t nowMs);
  void evaluateSequence();
  void evaluateSequenceIfDue(uint32_t nowMs);
  void resetSequence();
  void publishSolvedEvent();

  Core::NodeContext* ctx_ = nullptr;
  String topicEvent_;
  HardwareSerial* dfSerial_ = nullptr;
  DFRobotDFPlayerMini dfPlayer_;
  bool dfOk_ = false;
  uint8_t soundQueue_[kSoundQueueMax];
  uint8_t soundHead_ = 0;
  uint8_t soundTail_ = 0;
  bool soundPlaying_ = false;
  uint32_t lastSoundStartMs_ = 0;
  uint8_t currentTrack_ = 0;

  PiezoState piezo_[kSensorCount];
  bool knockWindowActive_ = false;
  uint32_t knockWindowStart_ = 0;
  uint16_t windowMax_[kSensorCount] = {0, 0, 0};
  uint32_t lastKnockMsGlobal_ = 0;

  int seqBuf_[kSeqMaxLen];
  int seqLen_ = 0;
  uint32_t lastSeqActivityMs_ = 0;

  uint32_t errorCount_ = 0;
};
