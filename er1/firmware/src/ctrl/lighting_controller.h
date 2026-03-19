#pragma once

#include <Arduino.h>
#include "core_node.h"
#include "lighting_driver.h"

class LightingController {
public:
  static constexpr size_t kChannelCount = 10;

  struct ChannelState {
    const char* id = nullptr;
    const char* name = nullptr;
    uint8_t pin = 0;
    uint8_t ledcCh = 0;
    bool on = false;
    uint32_t duty = 0;
    uint8_t dimPercent = 25;
  };

  struct FadePair {
    bool active = false;
    const char* idA = nullptr;
    const char* idB = nullptr;
    uint32_t startMs = 0;
    uint32_t durationMs = 0;
    uint32_t fromDutyA = 0;
    uint32_t fromDutyB = 0;
    uint32_t toDutyA = 0;
    uint32_t toDutyB = 0;
    const char* tickReason = nullptr;
    const char* doneReason = nullptr;
  };

  LightingController();

  void begin(Core::NodeContext& ctx);
  void tick(uint32_t nowMs);
  bool onCmd(const char* cmd, const char* payload);
  void onGameModeMessage(const String& msg);
  void onLightingCommand(const String& payload);
  void onMosfetCommandTopic(const char* topic, const String& payload);

private:
  struct ParsedGameState {
    String mode;
    bool valid = false;
  };

  bool parseChannelIdFromTopic(const String& topic, String& outId);
  ChannelState* findById(const String& id);
  ChannelState* findByName(const String& name);
  ChannelState* findByAnyKey(const String& key);

  ParsedGameState parseGameState(const String& payload) const;
  String normalizeKey(const String& in) const;

  uint32_t clampDuty(uint32_t duty) const;
  uint32_t percentToDuty(uint32_t pct) const;
  uint32_t mapUserValueToDuty(int32_t v) const;

  void applyOutput(ChannelState& ch);
  void applySceneInitial(const char* reason);
  void applySceneAllOn(const char* reason);
  void applySceneAllOff(const char* reason);

  bool setChannel(const char* id, bool on, uint32_t duty, bool preserveZeroDutyWhenOn = false);
  bool setChannelPercent(const char* id, bool on, uint32_t pct);
  void publishChangedStates(const bool changed[], const char* reason);

  void resetFade(FadePair& fade);
  void startFadePair(FadePair& fade,
                     const char* idA, const char* idB,
                     uint32_t fromDutyA, uint32_t fromDutyB,
                     uint32_t toDutyA, uint32_t toDutyB,
                     uint32_t durationMs,
                     const char* tickReason,
                     const char* doneReason);
  void updateFadePair(FadePair& fade, uint32_t nowMs);
  void cancelScheduledEffects();

  bool handleLightingJsonCommand(JsonDocument& doc);

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

  bool bootStatePublished_ = false;
  bool lastMqttConnected_ = false;
  String appliedMode_;

  FadePair fade1_{};
  FadePair fade2_{};
};
