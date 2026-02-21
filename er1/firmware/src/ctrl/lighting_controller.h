#pragma once

#include <Arduino.h>

#include "core_node.h"
#include "lighting_driver.h"

// 9-channel MOSFET lighting controller.
// MQTT interface (subscribed by lighting_main):
//   lighting/mosfet/<id>/cmd    payload: ON | OFF | DIM | PWM <v> | <v>
// Publishes retained:
//   lighting/mosfet/<id>/state  JSON
class LightingController {
public:
  static constexpr size_t kChannelCount = 9;

  void begin(Core::NodeContext& ctx);
  void tick(uint32_t nowMs);

  // Optional: handle node-level unknown commands (from <node>/cmd)
  bool onCmd(const char* cmd, const char* payload);

  // Subscription handler for lighting/mosfet/+/cmd
  void onMosfetCommandTopic(const char* topic, const String& payload);

private:
  struct ChannelState {
    const char* id;
    uint8_t pin;
    uint8_t ledcCh;
    bool on = false;
    uint32_t duty = 0;         // 0..maxDuty
    uint8_t dimPercent = 25;   // default DIM level
  };

  static bool parseChannelIdFromTopic(const String& topic, String& outId);

  uint32_t clampDuty(uint32_t duty) const;
  uint32_t percentToDuty(uint32_t pct) const;
  uint32_t mapUserValueToDuty(int32_t v) const;

  void applyOutput(ChannelState& ch);
  void publishChannelState(const ChannelState& ch, const char* reason);
  void publishBootSnapshotOnce();

  void log(const char* level, const String& msg) const;
  void log(const char* level, const String& msg, const String& dataJson) const;
  bool publish(const char* topic, const String& payload, bool retained = false) const;

  ChannelState* findById(const String& id);

  ChannelState channels_[kChannelCount] = {
    {"1", 0, 0, false, 0, 25},
    {"2", 0, 1, false, 0, 25},
    {"3", 0, 2, false, 0, 25},
    {"4", 0, 3, false, 0, 25},
    {"5", 0, 4, false, 0, 25},
    {"6", 0, 5, false, 0, 25},
    {"7", 0, 6, false, 0, 25},
    {"8", 0, 7, false, 0, 25},
    {"9", 0, 8, false, 0, 25},
  }; 

  Core::NodeContext* ctx_ = nullptr;
  LightingDriver driver_;

  uint32_t lastMetricMs_ = 0;
  uint32_t errorCount_ = 0;

  bool bootSnapshotSent_ = false;
};
