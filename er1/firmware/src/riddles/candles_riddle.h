#pragma once

#include <Arduino.h>

#include "core_node.h"

class CandlesRiddle {
public:
  void begin(Core::NodeContext& ctx);
  void tick(uint32_t nowMs);
  bool onCmd(const char* cmd, const char* payload);

  uint32_t errorCount() const { return errorCount_; }
  bool shouldAllowLog(const char* level);

private:
  static constexpr bool kDevLog = false;
  static constexpr uint32_t kMetricIntervalMs = 1000;
  static constexpr uint32_t kSeqTimeoutMs = 3000;
  // LED pins (candle LEDs) in order: 1..4
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

  void log(const char* level, const String& msg);
  void log(const char* level, const String& msg, const String& dataJson);
  bool publish(const char* topic, const char* type, uint32_t version, const String& dataJson,
               const char* id = nullptr, bool retained = false);
  bool publish(const char* topic, const String& payload, bool retained = false);

  void setLed(int idx, bool on);
  void initState();
  void calibrateBases();
  bool detectBlow(int idx);
  void evaluateSequence();
  void evaluateSequenceIfDue(uint32_t nowMs);
  void resetPuzzleState();
  void flickerRelight(int cycles = 6, int onMs = 80, int offMs = 60);
  void resetAll();
  void publishSolvedEvent();
  void publishMetricsIfDue(uint32_t nowMs);

  Core::NodeContext* ctx_ = nullptr;
  String topicEvent_;
  String topicCmdStarSky_;
  String topicCmdLighting_;
  bool lit_[4] = {true, true, true, true};
  int progress_[4] = {-1, -1, -1, -1};
  int progressed_ = 0;
  uint32_t lastAction_ = 0;
  uint32_t lastSeqActivityMs_ = 0;
  bool solved_ = false;
  bool solvedEventSent_ = false;
  MicMetric metrics_[4]{};
  uint32_t lastMetricMs_ = 0;
  // Last scan window stats per mic (updated whenever detectBlow() runs)
  uint16_t lastRaw_[4] = {0,0,0,0};
  uint16_t lastAvgWin_[4] = {0,0,0,0};
  uint16_t lastMaxWin_[4] = {0,0,0,0};
  uint8_t lastOver_[4] = {0,0,0,0};
  uint8_t lastNeeded_[4] = {0,0,0,0};
  uint8_t lastHit_[4] = {0,0,0,0};
  int base_[4] = {1515, 1490, 1485, 1508};
  static constexpr int kBaseMin[4] = {1515, 1490, 1485, 1508};
  int effBase_[4] = {1515, 1490, 1485, 1508};
  uint8_t micSaturated_[4] = {0, 0, 0, 0};
  int delta_[4] = {120, 120, 120, 120};
  uint32_t errorCount_ = 0;
};
