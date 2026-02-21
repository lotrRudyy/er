#pragma once

#include <Arduino.h>
#include "core_node.h"
#include "lighting_driver.h"   // from lib/drivers/include

class LightingController {
public:
  static constexpr size_t kChannelCount = 9;

  struct ChannelState {
    const char* id = nullptr;     // "1".."9"
    uint8_t pin = 0;
    uint8_t ledcCh = 0;
    bool on = false;
    uint32_t duty = 0;
    uint8_t dimPercent = 25;      // default DIM/DIMMED
  };

  LightingController();

  void begin(Core::NodeContext& ctx);
  void tick(uint32_t nowMs);

  // Node-level commands (<node>/cmd). Not used for MOSFET control.
  bool onCmd(const char* cmd, const char* payload);

  // Called by NodeCore subscription callback for lighting/mosfet/<id>/cmd
  void onMosfetCommandTopic(const char* topic, const String& payload);

private:
  bool parseChannelIdFromTopic(const String& topic, String& outId);

  ChannelState* findById(const String& id);

  uint32_t clampDuty(uint32_t duty) const;
  uint32_t percentToDuty(uint32_t pct) const;
  uint32_t mapUserValueToDuty(int32_t v) const;

  void applyOutput(ChannelState& ch);

  bool publish(const char* topic, const String& payload, bool retained) const;
  void publishChannelState(const ChannelState& ch, const char* reason);
  void publishAllStates(const char* reason);

  void log(const char* level, const String& msg) const;
  void log(const char* level, const String& msg, const String& dataJson) const;

  bool mqttConnected() const;

private:
  Core::NodeContext* ctx_ = nullptr;

  ChannelState channels_[kChannelCount]{};
  LightingDriver driver_{};

  uint32_t lastMetricMs_ = 0;
  uint32_t errorCount_ = 0;

  bool bootStatePublished_ = false;
  bool lastMqttConnected_ = false;
};
