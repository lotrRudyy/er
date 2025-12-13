#pragma once

#include <Arduino.h>

#include "core_node.h"

class MaglockController {
public:
  void begin(Core::NodeContext& ctx);
  void tick(uint32_t nowMs);
  bool onCmd(const char* cmd, const char* payload);

  void onGameModeMessage(const String& msg);
  void onKnockingEvent(const String& msg);
  void onLockCommandTopic(const char* topic, const String& payload);

  uint32_t errorCount() const { return errorCount_; }
  uint32_t currentHeartbeatIntervalMs() const;
  bool shouldAllowLog(const char* level);

private:
  enum class GameMode : uint8_t {
    Off = 0,
    InGame,
    Maint
  };

  enum class LockMode : uint8_t {
    FailSecure = 0,
    FailSafe
  };

  struct LockState {
    const char* id;
    uint8_t pin;
    LockMode mode;

    bool coilOn = false;
    bool pulsing = false;
    bool cooldown = false;
    uint32_t pulseStartMs = 0;
    uint32_t cooldownStartMs = 0;
    uint32_t pulseCount = 0;
  };

  static constexpr uint32_t kPulseMs = 1000;
  static constexpr uint32_t kCooldownMs = 10000;
  static constexpr uint32_t kMetricIntervalMs = 10000;

  static constexpr size_t kLockCount = 5;
  LockState locks_[kLockCount] = {
      {"images", 26, LockMode::FailSecure},
      {"r2", 16, LockMode::FailSafe},
      {"r3", 17, LockMode::FailSafe},
      {"slider", 33, LockMode::FailSecure},
      {"knocking", 25, LockMode::FailSecure},
  };

  void applyLockOutput(LockState& lk);
  const char* lockStateName(const LockState& lk) const;
  void publishLockState(const LockState& lk, const char* reason);
  LockState* findLockById(const String& id);
  void startPulse(LockState& lk, const char* reason);
  void setFailSafe(LockState& lk, bool locked, const char* reason);
  void updatePulseTimers(uint32_t nowMs);
  void publishMetricsIfDue(uint32_t nowMs);
  void handleLockCommand(LockState& lk, const String& cmd);
  void log(const char* level, const String& msg);
  void log(const char* level, const String& msg, const String& dataJson);
  void handleLockCommandTopicInternal(const String& topic, const String& payload);

  uint32_t hbIntervalForMode(GameMode mode) const;
  void applyHeartbeatInterval();

  Core::NodeContext* ctx_ = nullptr;
  GameMode gameMode_ = GameMode::Off;
  uint32_t lastMetricMs_ = 0;
  uint32_t errorCount_ = 0;
};
