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
  };

  struct FadeState {
    bool active = false;
    size_t index = 0;
    uint32_t startMs = 0;
    uint32_t durationMs = 0;
    uint32_t fromDuty = 0;
    uint32_t toDuty = 0;
    const char* reason = nullptr;
  };

  enum class BulkCommand : uint8_t {
    None = 0,
    AllOn,
    AllOff,
    SceneInitial,
    NonIngameAllOn,
  };

  LightingController();

  void begin(Core::NodeContext& ctx);
  void tick(uint32_t nowMs);

  bool onCmd(const char* cmd, const char* payload);
  void onGameStateMessage(const String& payload);
  void onLightingCommandTopic(const String& payload);
  void onMosfetCommandTopic(const char* topic, const String& payload);

private:
  bool mqttConnected() const;
  bool publish(const char* topic, const String& payload, bool retained) const;
  void log(const char* level, const String& msg) const;
  void log(const char* level, const String& msg, const String& dataJson) const;

  ChannelState* findById(const String& id);
  ChannelState* findByName(const String& name);
  ChannelState* findLight(const String& token);
  bool parseChannelIdFromTopic(const String& topic, String& outId) const;

  uint32_t clampDuty(uint32_t duty) const;
  uint32_t percentToDuty(uint32_t pct) const;
  uint32_t mapUserValueToDuty(int32_t v) const;

  bool setChannel(size_t index, bool on, uint32_t duty, bool preserveZeroDutyWhenOn = false);
  void applyOutput(ChannelState& ch);

  void stopAllFades();
  void stopFade(size_t index);
  void startFade(size_t index, uint32_t toDuty, uint32_t durationMs, const char* reason);
  void updateFade(FadeState& fade, uint32_t nowMs);

  void publishChannelState(const ChannelState& ch, const char* reason);
  void publishAllStates(const char* reason);
  void markDirty(size_t index, const char* reason);
  void clearDirty();
  void flushDirtyStates(uint32_t maxCount = 1);

  void queueBulkCommand(BulkCommand cmd);
  void startBulkCommand(BulkCommand cmd);
  void cancelBulkCommand();
  void runBulkCommandStep(uint32_t nowMs);
  size_t bulkApplyCountForTick() const;

  void queueChannelTarget(size_t index, bool on, uint32_t duty, const char* reason, bool preserveZeroDutyWhenOn = false);
  void clearPendingChannel(size_t index);
  void clearAllPendingChannels();
  void runPendingChannelStep(uint32_t nowMs);

private:
  Core::NodeContext* ctx_ = nullptr;
  LightingDriver driver_{};
  ChannelState channels_[kChannelCount]{};
  FadeState fades_[kChannelCount]{};

  const char* dirtyReasons_[kChannelCount]{};
  bool dirty_[kChannelCount]{};
  int currentPhase_ = -1;
  void applyPhaseScene(int phase, const char* reason);
  bool bootStatePublished_ = false;

  BulkCommand queuedBulkCommand_ = BulkCommand::None;
  BulkCommand activeBulkCommand_ = BulkCommand::None;
  uint8_t bulkIndex_ = 0;
  const char* bulkReason_ = nullptr;
  bool bulkTargetOn_ = false;
  uint32_t bulkTargetDuty_ = 0;
  uint32_t lastBulkStepMs_ = 0;

  bool pendingValid_[kChannelCount]{};
  bool pendingOn_[kChannelCount]{};
  uint32_t pendingDuty_[kChannelCount]{};
  bool pendingPreserveZero_[kChannelCount]{};
  const char* pendingReason_[kChannelCount]{};
  uint32_t lastPendingStepMs_ = 0;
};
