#pragma once

#include <Arduino.h>
#include <DFRobotDFPlayerMini.h>

#include "core_node.h"

class StopTimerRiddle {
public:
  void begin(Core::NodeContext& ctx);
  void tick(uint32_t nowMs);
  bool onCmd(const char* cmd, const char* payload);

  bool shouldAllowLog(const char* level);
  bool dfReady() const { return dfOk_; }
  uint32_t errorCount() const { return errorCount_; }

private:
  static constexpr bool kDevLog = false;
  static constexpr int kSensorCount = 3;
  static constexpr uint8_t kPiezoPins[kSensorCount] = {32, 33, 34};
  static constexpr uint16_t kKnockRawThr = 1200;
  static constexpr uint16_t kKnockRelThr = 800;
  static constexpr uint32_t kKnockDebounceMs = 120;
  static constexpr uint32_t kMetricIntervalMs = 1000;
  static constexpr uint32_t kSeqTimeoutMs = 3000;
  static constexpr uint8_t kDfVolume = 25;
  static constexpr int kSeqExpectLen = 9;
  static constexpr int kSeqExpect[kSeqExpectLen] = {0, 0, 0, 0, 1, 1, 2, 2, 2};
  static constexpr int kSeqMaxLen = 16;
  static constexpr const char* kTopicMetric = "er1/room3/knocking/metric";
  static constexpr const char* kTopicEvent = "er1/room3/knocking/event";

  struct PiezoState {
    uint8_t pin = 0;
    uint32_t sum = 0;
    uint16_t samples = 0;
    uint16_t avg = 0;
    uint16_t base = 0;
    uint16_t maxVal = 0;
    uint16_t lastRaw = 0;

    static constexpr int kBucketCount = 10;
    uint32_t bucketSum[kBucketCount];
    uint16_t bucketSamples[kBucketCount];
  };

  void initSensors();
  void sampleSensors(uint32_t nowMs);
  void registerKnock(int idx, uint16_t raw, uint32_t nowMs);
  void evaluateSequence();
  void evaluateSequenceIfDue(uint32_t nowMs);
  void resetSequence();
  void playKnockSound(int idx);
  void playFailSoundX5();
  void publishSolvedEvent();
  void publishMetricsIfDue(uint32_t nowMs);
  void log(const char* level, const String& msg);
  void log(const char* level, const String& msg, const String& dataJson);

  Core::NodeContext* ctx_ = nullptr;
  HardwareSerial* serial_ = nullptr;
  DFRobotDFPlayerMini dfPlayer_;
  bool dfOk_ = false;

  PiezoState piezo_[kSensorCount];
  bool hitLatched_[kSensorCount];
  uint32_t lastKnockMs_[kSensorCount];
  int seqBuf_[kSeqMaxLen];
  int seqLen_ = 0;
  uint32_t lastSeqActivityMs_ = 0;
  uint32_t lastMetricMs_ = 0;
  uint32_t errorCount_ = 0;
};
