#pragma once

#include <Arduino.h>

#include "core_node.h"
#include "maglock_driver.h"

class MaglockController {
public:
  void begin(Core::NodeContext& ctx);
  void tick(uint32_t nowMs);
  bool onCmd(const char* cmd, const char* payload);

  void onGameStateMessage(const String& payload);
  void onMaglockCommandTopic(const String& payload);
  void onLockCommandTopic(const char* topic, const String& payload);

  uint32_t errorCount() const { return errorCount_; }
  uint32_t currentHeartbeatIntervalMs() const { return 5000; }
  bool shouldAllowLog(const char* level);

private:
  enum class GlobalMode : uint8_t {
    Maintenance = 0,
    Standby,
    Prepare,
    InGame,
  };

  enum class LockMode : uint8_t {
    FailSecure = 0,
    FailSafe,
  };

  struct LockState {
    const char* id = nullptr;
    uint8_t pin = 0;
    LockMode mode = LockMode::FailSecure;
    bool coilOn = false;
    bool pulsing = false;
    bool cooldown = false;
    bool bootGuard = false;
    uint32_t pulseStartMs = 0;
    uint32_t cooldownStartMs = 0;
    uint32_t pulseCount = 0;
  };

  static constexpr uint32_t kPulseMs = 1000;
  static constexpr uint32_t kHardCutoffMs = 1200;
  static constexpr uint32_t kCooldownMs = 10000;
  static constexpr uint32_t kBootGuardMs = 10000;
  static constexpr uint32_t kMetricIntervalMs = 10000;

  static constexpr size_t kLockCount = 5;
  LockState locks_[kLockCount] = {
    {"r2", 16, LockMode::FailSafe},
    {"r3", 17, LockMode::FailSafe},
    {"images", 26, LockMode::FailSecure},
    {"slider", 33, LockMode::FailSecure},
    {"knocking", 25, LockMode::FailSecure},
  };

  void applyLockOutput(LockState& lk);
  const char* lockStateName(const LockState& lk) const;
  LockState* findLockById(const String& id);

  void publishLockState(const LockState& lk, const char* reason);
  void publishStateSnapshot(const char* reason);

  void setFailSafe(LockState& lk, bool locked, const char* reason);
  void startPulse(LockState& lk, const char* reason);
  void forceFailSecureSafe(const char* reason);
  void applyModeDefaults(GlobalMode mode, const char* reason);
  void updatePulseTimers(uint32_t nowMs);
  void publishMetricsIfDue(uint32_t nowMs);

  void handleSingleLockCommand(LockState& lk, const String& cmd, const char* reason);
  void handleLegacyLockCommandTopicInternal(const String& topic, const String& payload);

  void log(const char* level, const String& msg) const;
  void log(const char* level, const String& msg, const String& dataJson) const;
  bool publish(const char* topic, const String& payload, bool retained = false) const;

  Core::NodeContext* ctx_ = nullptr;
  MaglockDriver driver_{};
  GlobalMode mode_ = GlobalMode::Standby;
  uint32_t bootMs_ = 0;
  uint32_t lastMetricMs_ = 0;
  uint32_t errorCount_ = 0;
};
