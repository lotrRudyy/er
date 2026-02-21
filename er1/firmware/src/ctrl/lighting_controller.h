#pragma once

#include <Arduino.h>

#include "core_node.h"
#include "lighting_controller.h"

class LightingController {
public:
  void begin(Core::NodeContext& ctx);
  void tick(uint32_t nowMs);

  // Called from MQTT subscription: lighting/mosfet/<id>/cmd
  void onMosfetCommandTopic(const char* topic, const String& payload);

  uint32_t errorCount() const { return errorCount_; }
  bool shouldAllowLog(const char* level);

private:
  struct ChannelState {
    const char* id;
    uint8_t pin;
    uint8_t ledcCh;
    bool dimmable;

    uint32_t duty = 0;        // raw duty (0..max)
    uint32_t lastOnDuty = 0;  // last non-zero duty
  };

  static constexpr uint8_t kResolutionBits = 12;  // 0..4095
  static constexpr uint32_t kFreqHz = 2000;
  static constexpr uint32_t kMetricIntervalMs = 10000;
  static constexpr uint8_t kChannelCount = 9;

  ChannelState channels_[kChannelCount] = {
      {"1", 16, 0, true},
      {"2", 17, 1, true},
      {"3", 18, 2, true},
      {"4", 19, 3, true},
      {"5", 21, 4, true},
      {"6", 22, 5, true},
      {"7", 23, 6, true},
      {"8", 25, 7, true},
      {"9", 26, 8, true},
  };

  void applyOutput(ChannelState& ch);
  void setDuty(ChannelState& ch, uint32_t duty, const char* reason);
  void setOn(ChannelState& ch, const char* reason);
  void setOff(ChannelState& ch, const char* reason);
  void setDimmed(ChannelState& ch, const char* reason);

  void publishChannelState(const ChannelState& ch, const char* reason);
  void publishStateSnapshot(const char* reason = nullptr);
  void publishMetricsIfDue(uint32_t nowMs);

  ChannelState* findChannelById(const String& id);

  bool parseAndApplyCommand(ChannelState& ch, const String& payload, const char* topic);
  static bool parseChannelIdFromTopic(const String& topic, String& outId);

  // Utils
  uint32_t clampDuty(uint32_t duty) const;
  uint32_t percentToDuty(uint32_t pct) const;
  uint32_t mapUserValueToDuty(int32_t v) const;

  void log(const char* level, const String& msg) const;
  void log(const char* level, const String& msg, const String& dataJson) const;
  bool publish(const char* topic, const String& payload, bool retained = false) const;

  Core::NodeContext* ctx_ = nullptr;
  MosfetPwmDriver driver_;
  uint32_t lastMetricMs_ = 0;
  uint32_t errorCount_ = 0;
  String topicDbg_;
};
