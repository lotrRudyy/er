#pragma once

#include <Arduino.h>
#include <Preferences.h>

#include "core_node.h"

class StarSkyRiddle {
public:
  void begin(Core::NodeContext& ctx);
  void tick(uint32_t nowMs);
  bool onCmd(const char* cmd, const char* payload);

  uint32_t errorCount() const { return errorCount_; }
  bool candlesSolved() const { return candlesSolved_; }

  void handleCandlesEvent(const String& payload);

private:
  static constexpr uint32_t kMetricIntervalMs = 10000;
  static constexpr uint32_t kStepMs = 4000;
  static constexpr uint32_t kPauseMs = 15000;
  static constexpr uint32_t kCycleMs = kStepMs * 3 + kPauseMs;
  static constexpr int kLedPins[4] = {16, 17, 18, 19};

  void log(const char* level, const String& msg) const;
  void log(const char* level, const String& msg, const String& dataJson) const;
  bool publish(const char* topic, const char* type, uint32_t version, const String& dataJson,
               const char* id = nullptr, bool retained = false) const;
  bool publish(const char* topic, const String& payload, bool retained = false) const;

  void applyPattern(uint32_t nowMs);
  void setStripRaw(int idx, uint8_t duty);
  void setAllStripsOff();
  void publishMetricsIfDue(uint32_t nowMs);
  void publishState();
  void persistState();
  void loadState();

  Core::NodeContext* ctx_ = nullptr;
  Preferences prefs_;

  bool candlesSolved_ = false;
  uint32_t cycleStartMs_ = 0;
  uint32_t lastMetricMs_ = 0;
  uint32_t errorCount_ = 0;
};
