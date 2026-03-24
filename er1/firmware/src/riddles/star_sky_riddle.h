#pragma once

#include <Arduino.h>
#include <Preferences.h>

#include "core_node.h"

class StarSkyRiddle {
public:
  void begin(Core::NodeContext& ctx);
  void tick(uint32_t nowMs);
  bool onCmd(const char* cmd, const char* payload);
  void setGameMode(bool inGame);
  void setManualOverride(bool enabled);

  uint32_t errorCount() const { return errorCount_; }

private:
  static constexpr uint32_t kMetricIntervalMs = 10000;
  static constexpr uint32_t kStepMs = 7000;
  static constexpr uint32_t kPauseMs = 5000;
  static constexpr uint32_t kCycleMs = kStepMs * 3 + kPauseMs;
  static constexpr int kLedPins[4] = { 26, 25, 16, 32 };
  /*
    26 UV LEDs
    25 Libra
    16 Aquarius
    32 Scorpio
  */

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

  Core::NodeContext* ctx_ = nullptr;
  Preferences prefs_;
  uint32_t cycleStartMs_ = 0;
  uint32_t lastMetricMs_ = 0;
  uint32_t errorCount_ = 0;
  bool gameActive_ = false;
  bool moduleEnabled_ = true;
  bool manualOverride_ = false;
};
