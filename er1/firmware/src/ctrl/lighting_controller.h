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

  // Node-level commands (<node>/cmd).
  bool onCmd(const char* cmd, const char* payload);

  void onGameModeMessage(const String& msg);
  void onEventTopic(const char* topic, const String& payload);

  // Called by NodeCore subscription callback for lighting/mosfet/<id>/cmd
  void onMosfetCommandTopic(const char* topic, const String& payload);

private:
  bool parseChannelIdFromTopic(const String& topic, String& outId);

  ChannelState* findById(const String& id);

  uint32_t clampDuty(uint32_t duty) const;
  uint32_t percentToDuty(uint32_t pct) const;
  uint32_t mapUserValueToDuty(int32_t v) const;

  void applyOutput(ChannelState& ch);
  void applySceneInitial(const char* reason);
  void handleProgressEvent(const char* rid);
  void runPianoTorch(const char* reason);
  void runChessRoom(const char* reason);

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

  bool inGame_ = false;
  bool pianoSolvedSeen_ = false;
  bool chessSolvedSeen_ = false;
  bool candlesSolvedSeen_ = false;

  bool pianoTorchPending_ = false;
  uint32_t pianoTorchDueMs_ = 0;

  bool chessRoomPending_ = false;
  uint32_t chessRoomDueMs_ = 0;

  FadePair pianoFade_{};
  FadePair chessFade_{};
  FadePair candlesFade_{};

  static constexpr uint32_t kFadeMs = 7000;
  static constexpr uint32_t kProgressDelayMs = 7000;
};
