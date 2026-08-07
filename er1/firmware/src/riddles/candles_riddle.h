#pragma once

#include <Arduino.h>

#include "core_node.h"

class CandlesRiddle {
public:
  void begin(Core::NodeContext& ctx);
  void tick(uint32_t nowMs);
  bool onCmd(const char* cmd, const char* payload);
  void setGameMode(bool inGame);

  uint32_t errorCount() const { return errorCount_; }
  bool shouldAllowLog(const char* level);

private:
  static constexpr bool kDevLog = false;
  static constexpr uint32_t kMetricIntervalMs = 1000;
  static constexpr uint32_t kSeqTimeoutMs = 4500;
  static constexpr size_t kAttemptStringMax = 24;

  static constexpr int kLedPins[4] = {12, 14, 26, 25};
  static constexpr int kMicPins[4] = {33, 32, 35, 34};
  static constexpr int kOrder[4] = {2, 0, 3, 1};

  struct MicMetric {
    uint32_t sum;
    uint16_t samples;
    uint16_t avg;
    uint16_t base;
    uint16_t maxVal;
    uint16_t lastRaw;
  };

  static constexpr uint16_t kBlowWindowSamples = 250;
  static constexpr uint16_t kBlowNeededSamples = (kBlowWindowSamples * 60 + 99) / 100;

  void log(const char* level, const String& msg) const;
  void log(const char* level, const String& msg, const String& dataJson) const;
  bool publish(const char* topic, const char* type, uint32_t version, const String& dataJson,
               const char* id = nullptr, bool retained = false) const;
  bool publish(const char* topic, const String& payload, bool retained = false) const;

  void setLed(int idx, bool on);
  void setAllLeds(bool on);
  void initState();
  void calibrateBases();
  void clearBlowState(int idx);
  bool detectBlow(int idx, int thrAbs, uint32_t nowMs);
  void pushRollingSample(int idx, uint16_t raw, int thrAbs);
  void evaluateSequence(uint32_t nowMs);
  void evaluateSequenceIfDue(uint32_t nowMs);
  void resetPuzzleState();
  void flickerRelight(int cycles = 6, int onMs = 120, int offMs = 90);
  void resetAll();
  void publishSolvedEvent();
  void publishState();
  void publishMetricsIfDue(uint32_t nowMs);

  String currentSequenceHyphen() const;
  String currentSequenceJson() const;

  Core::NodeContext* ctx_ = nullptr;
  bool lit_[4] = {true, true, true, true};
  int progress_[4] = {-1, -1, -1, -1};
  int progressed_ = 0;
  uint32_t lastAction_ = 0;
  uint32_t lastSeqActivityMs_ = 0;
  bool solved_ = false;
  bool solvedEventSent_ = false;
  bool resetArmed_ = false;
  uint32_t resetArmMs_ = 0;
  MicMetric metrics_[4]{};
  uint32_t lastMetricMs_ = 0;
  uint16_t lastRaw_[4] = {0, 0, 0, 0};
  uint16_t lastAvgWin_[4] = {0, 0, 0, 0};
  uint16_t lastMaxWin_[4] = {0, 0, 0, 0};
  uint16_t lastOver_[4] = {0, 0, 0, 0};
  uint16_t lastNeeded_[4] = {0, 0, 0, 0};
  uint8_t lastHit_[4] = {0, 0, 0, 0};
  uint16_t sampleBuf_[4][kBlowWindowSamples] = {};
  uint16_t samplePos_[4] = {0, 0, 0, 0};
  uint16_t sampleCount_[4] = {0, 0, 0, 0};
  uint16_t overCount_[4] = {0, 0, 0, 0};
  uint32_t windowSum_[4] = {0, 0, 0, 0};
  uint16_t windowMax_[4] = {0, 0, 0, 0};
  uint32_t lastTrigMs_[4] = {0, 0, 0, 0};
  int base_[4] = {1592, 1800, 1135, 1666};
  static constexpr int kBaseMin[4] = {1592, 1800, 1135, 1666};
  int effBase_[4] = {1592, 1800, 1135, 1666};
  uint8_t micSaturated_[4] = {0, 0, 0, 0};
  int delta_[4] = {80, 80, 80, 80};
  uint32_t errorCount_ = 0;
  bool gameActive_ = false;
  bool moduleEnabled_ = true;

  uint32_t tries_ = 0;
  String lastAttempt_;
};
