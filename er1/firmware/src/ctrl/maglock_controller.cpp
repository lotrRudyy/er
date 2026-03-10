#include "ctrl/maglock_controller.h"

#include <cstring>

namespace {

constexpr const char* kLockCmdPrefix = "maglock/lock/";
constexpr const char* kLockStatePrefix = "maglock/lock/";
constexpr const char* kLockCmdSuffix = "/cmd";
constexpr const char* kPrefsGameModeKey = "game_mode";

String makeLockStateTopic(const char* id) {
  String topic = kLockStatePrefix;
  topic += id;
  topic += "/state";
  return topic;
}

bool parseLockIdFromTopic(const String& topic, String& outId) {
  if (!topic.startsWith(kLockCmdPrefix) || !topic.endsWith(kLockCmdSuffix)) {
    return false;
  }
  int start = strlen(kLockCmdPrefix);
  int end = topic.length() - strlen(kLockCmdSuffix);
  if (end <= start) return false;
  outId = topic.substring(start, end);
  return true;
}

}  // namespace

void MaglockController::begin(Core::NodeContext& ctx) {
  ctx_ = &ctx;
  prefs_ = &ctx.prefs();
  bootMs_ = millis();

  MaglockChannelConfig channels[kLockCount];
  for (size_t i = 0; i < kLockCount; i++) {
    channels[i] = {locks_[i].id, locks_[i].pin};
    locks_[i].coilOn = false;
    locks_[i].pulsing = false;
    locks_[i].cooldown = false;
    locks_[i].bootGuard = false;
    locks_[i].pulseStartMs = 0;
    locks_[i].cooldownStartMs = 0;
    locks_[i].pulseCount = 0;
  }

  driver_.begin(channels, kLockCount);

  // Force dangerous fail-secure outputs OFF immediately on boot and
  // start a boot guard so they cannot be retriggered right away.
  for (auto& lk : locks_) {
    if (lk.mode == LockMode::FailSecure) {
      lk.coilOn = false;
      lk.pulsing = false;
      lk.cooldown = true;
      lk.bootGuard = true;
      lk.cooldownStartMs = bootMs_;
    }
    applyLockOutput(lk);
  }

  // SAFETY: always boot into OFF/standby locally.
  loadGameMode();

  lastMetricMs_ = millis();
  applyHeartbeatInterval();
  publishStateSnapshot();
  // use canonical dbg topic from node config
  topicDbg_ = ctx.config().topics.dbg;
}

void MaglockController::tick(uint32_t nowMs) {
  if (!ctx_) return;
  updatePulseTimers(nowMs);
  publishMetricsIfDue(nowMs);
}

bool MaglockController::onCmd(const char* cmd, const char* payload) {
  String msg(cmd ? cmd : "");
  if (payload && payload[0]) {
    msg += " ";
    msg += payload;
  }
  log("WRN", String("Unknown node CMD: ") + msg);
  return true;
}

void MaglockController::onGameModeMessage(const String& msg) {
  if (!ctx_) return;
  String trimmed = msg;
  trimmed.trim();
  trimmed.toUpperCase();

  GameMode old = gameMode_;
  if (trimmed == "INGAME") {
    gameMode_ = GameMode::InGame;
  } else if (trimmed == "MAINT" || trimmed == "MAINTENANCE") {
    gameMode_ = GameMode::Maint;
  } else {
    gameMode_ = GameMode::Off;
  }

  auto modeName = [](GameMode m) -> const char* {
    switch (m) {
      case GameMode::InGame: return "INGAME";
      case GameMode::Maint: return "MAINT";
      case GameMode::Off:
      default: return "OFF";
    }
  };

  if (old != gameMode_) {
    String data = String("{\"from\":\"") + modeName(old) +
                  "\",\"to\":\"" + modeName(gameMode_) + "\"}";
    log("INF", "gameMode changed", data);
    applyHeartbeatInterval();
    persistGameMode();
  }

  if (gameMode_ == GameMode::InGame) {
  if (LockState* r2 = findLockById("r2")) setFailSafe(*r2, true, "game_start");
  if (LockState* r3 = findLockById("r3")) setFailSafe(*r3, true, "game_start");
} else {
  // Any non-InGame mode should force fail-secure outputs safe
  // and open the fail-safe room locks.
  forceAllFailSecureOff("mode_safe");
  if (LockState* r2 = findLockById("r2")) setFailSafe(*r2, false, "standby_open");
  if (LockState* r3 = findLockById("r3")) setFailSafe(*r3, false, "standby_open");
}

  publishStateSnapshot();
}

void MaglockController::onLockCommandTopic(const char* topic, const String& payload) {
  // Canonical lock control interface:
  //   maglock/lock/<id>/cmd  payload: OPEN | CLOSE
  handleLockCommandTopicInternal(String(topic ? topic : ""), payload);
}

uint32_t MaglockController::currentHeartbeatIntervalMs() const {
  return hbIntervalForMode(gameMode_);
}

bool MaglockController::shouldAllowLog(const char* level) {
  bool isErr = (strcmp(level, "ERR") == 0);
  bool isDbg = (strcmp(level, "DBG") == 0);
  bool allow = false;
  if (isErr) {
    allow = true;
  } else if (gameMode_ == GameMode::Off) {
    allow = false;
  } else if (gameMode_ == GameMode::InGame) {
    allow = !isDbg;
  } else {
    allow = true;
  }
  if (isErr) {
    errorCount_++;
  }
  return allow;
}

void MaglockController::applyLockOutput(LockState& lk) {
  driver_.setCoil(lk.pin, lk.coilOn);
}

const char* MaglockController::lockStateName(const LockState& lk) const {
  if (lk.mode == LockMode::FailSecure) {
    return lk.coilOn ? "OPEN" : "CLOSED";
  }
  return lk.coilOn ? "CLOSED" : "OPEN";
}

void MaglockController::publishLockState(const LockState& lk, const char* reason) {
  if (!ctx_) return;
  String topic = makeLockStateTopic(lk.id);
  String payload = String("{\"fw\":\"") + ctx_->fwVersion() +
                   "\",\"up\":" + String(millis() / 1000) +
                   ",\"id\":\"" + lk.id + "\"" +
                   ",\"state\":\"" + String(lockStateName(lk)) + "\"";
  if (reason && reason[0]) {
    payload += ",\"reason\":\"";
    payload += reason;
    payload += "\"";
  }
  payload += ",\"coil\":";
  payload += lk.coilOn ? "1" : "0";
  payload += ",\"pulses\":";
  payload += String(lk.pulseCount);
  payload += ",\"bootGuard\":";
  payload += lk.bootGuard ? "1" : "0";
  payload += "}";
  publish(topic.c_str(), payload);
  publishStateSnapshot();
}

MaglockController::LockState* MaglockController::findLockById(const String& id) {
  for (auto& lk : locks_) {
    if (id.equalsIgnoreCase(lk.id)) {
      return &lk;
    }
  }
  return nullptr;
}

void MaglockController::forceAllFailSecureOff(const char* reason) {
  for (auto& lk : locks_) {
    if (lk.mode != LockMode::FailSecure) continue;

    bool changed = lk.coilOn || lk.pulsing;

    lk.coilOn = false;
    lk.pulsing = false;
    lk.cooldown = true;
    lk.bootGuard = true;
    lk.cooldownStartMs = millis();

    applyLockOutput(lk);

    if (changed) {
      publishLockState(lk, reason);
    }
  }
}

void MaglockController::persistGameMode() {
  if (!prefs_) return;

  const char* modeStr = "OFF";
  switch (gameMode_) {
    case GameMode::InGame: modeStr = "INGAME"; break;
    case GameMode::Maint: modeStr = "MAINT"; break;
    case GameMode::Off:
    default: modeStr = "OFF"; break;
  }

  prefs_->putString(kPrefsGameModeKey, modeStr);
}

void MaglockController::loadGameMode() {
  if (!prefs_) {
    gameMode_ = GameMode::Off;
    return;
  }

  String stored = prefs_->getString(kPrefsGameModeKey, "OFF");
  stored.trim();
  stored.toUpperCase();

  // SAFETY CHOICE: never auto-resume game mode on boot.
  gameMode_ = GameMode::Off;

  String data = String("{\"stored\":\"") + stored + "\",\"boot\":\"OFF\"}";
  log("INF", "MAGLOCK_BOOT_MODE", data);
}

void MaglockController::startPulse(LockState& lk, const char* reason) {
  if (lk.mode != LockMode::FailSecure) {
    log("WRN", String("OPEN on non-failsecure via pulse: ") + lk.id);
    return;
  }

  if (gameMode_ == GameMode::Off) {
    log("WRN", String("OPEN ignored while OFF for ") + lk.id);
    return;
  }

  if (lk.pulsing || lk.cooldown || lk.bootGuard) {
    log("WRN", String("OPEN ignored (pulse/cooldown/bootguard active) for ") + lk.id);
    return;
  }

  lk.coilOn = true;
  lk.pulsing = true;
  lk.cooldown = false;
  lk.bootGuard = false;
  lk.pulseStartMs = millis();
  lk.pulseCount++;
  applyLockOutput(lk);
  publishLockState(lk, reason);
}

void MaglockController::setFailSafe(LockState& lk, bool locked, const char* reason) {
  if (lk.mode != LockMode::FailSafe) {
    log("WRN", String("setFailSafe on non-failsafe: ") + lk.id);
    return;
  }
  lk.coilOn = locked;
  lk.pulsing = false;
  lk.cooldown = false;
  lk.bootGuard = false;
  applyLockOutput(lk);
  publishLockState(lk, reason);
}

void MaglockController::updatePulseTimers(uint32_t nowMs) {
  for (auto& lk : locks_) {
    if (lk.mode != LockMode::FailSecure) continue;

    // Hard safety cutoff in case normal pulse completion is missed.
    if (lk.coilOn && (nowMs - lk.pulseStartMs >= kHardCutoffMs)) {
      lk.coilOn = false;
      lk.pulsing = false;
      lk.cooldown = true;
      lk.bootGuard = false;
      lk.cooldownStartMs = nowMs;
      applyLockOutput(lk);
      publishLockState(lk, "hard_cutoff");
      continue;
    }

    if (lk.pulsing && (nowMs - lk.pulseStartMs >= kPulseMs)) {
      lk.pulsing = false;
      lk.coilOn = false;
      applyLockOutput(lk);
      publishLockState(lk, "pulse_done");
      lk.cooldown = true;
      lk.bootGuard = false;
      lk.cooldownStartMs = nowMs;
    }

    if (lk.bootGuard && (nowMs - lk.cooldownStartMs >= kBootGuardMs)) {
      lk.bootGuard = false;
      lk.cooldown = false;
      publishLockState(lk, "boot_guard_done");
    } else if (lk.cooldown && !lk.bootGuard && (nowMs - lk.cooldownStartMs >= kCooldownMs)) {
      lk.cooldown = false;
      publishLockState(lk, "cooldown_done");
    }
  }
}

void MaglockController::publishMetricsIfDue(uint32_t nowMs) {
  if (!ctx_) return;
  if (nowMs - lastMetricMs_ < kMetricIntervalMs) return;
  lastMetricMs_ = nowMs;

  auto modeName = [](GameMode m) -> const char* {
    switch (m) {
      case GameMode::InGame: return "INGAME";
      case GameMode::Maint: return "MAINT";
      case GameMode::Off:
      default: return "OFF";
    }
  };

  String payload = String("{\"fw\":\"") + ctx_->fwVersion() +
                   "\",\"up\":" + String(nowMs / 1000) +
                   ",\"t\":\"MAG\"" +
                   ",\"mode\":\"" + String(modeName(gameMode_)) + "\"" +
                   ",\"en\":" + (ctx_->enabled() ? "1" : "0") +
                   ",\"locks\":[";
  for (size_t i = 0; i < kLockCount; i++) {
    if (i > 0) payload += ",";
    payload += "{\"id\":\"";
    payload += locks_[i].id;
    payload += "\",\"coil\":";
    payload += locks_[i].coilOn ? "1" : "0";
    payload += ",\"pulses\":";
    payload += String(locks_[i].pulseCount);
    payload += ",\"pulse\":";
    payload += locks_[i].pulsing ? "1" : "0";
    payload += ",\"cooldown\":";
    payload += locks_[i].cooldown ? "1" : "0";
    payload += ",\"bootGuard\":";
    payload += locks_[i].bootGuard ? "1" : "0";
    payload += "}";
  }
  payload += "]}";

  // Emit as DBG log so it can be suppressed with <node>/log/level.
  log("DBG", "maglock_metrics", payload);
}

void MaglockController::publishStateSnapshot() {
  if (!ctx_) return;
  auto modeName = [](GameMode m) -> const char* {
    switch (m) {
      case GameMode::InGame: return "INGAME";
      case GameMode::Maint: return "MAINT";
      case GameMode::Off:
      default: return "OFF";
    }
  };
  String data = String("{\"mode\":\"") + modeName(gameMode_) + "\",\"locks\":[";
  for (size_t i = 0; i < kLockCount; i++) {
    if (i > 0) data += ",";
    data += "{\"id\":\"";
    data += locks_[i].id;
    data += "\",\"coil\":";
    data += locks_[i].coilOn ? "1" : "0";
    data += ",\"pulse\":";
    data += locks_[i].pulsing ? "1" : "0";
    data += ",\"cooldown\":";
    data += locks_[i].cooldown ? "1" : "0";
    data += ",\"bootGuard\":";
    data += locks_[i].bootGuard ? "1" : "0";
    data += "}";
  }
  data += "]}";
  const auto& topics = ctx_->config().topics;
  if (topics.state.length() > 0) {
    publish(topics.state.c_str(), "state", 1, data, nullptr, true);
  }
}

void MaglockController::handleLockCommand(LockState& lk, const String& cmd) {
  if (!ctx_->enabled()) {
    log("WRN", String("Lock cmd while DISABLED: ") + lk.id + " cmd=" + cmd);
    return;
  }

  if (cmd == "OPEN") {
    if (lk.mode == LockMode::FailSecure) {
      startPulse(lk, "cmd:OPEN");
    } else {
      setFailSafe(lk, false, "cmd:OPEN");
    }
    return;
  }

  if (cmd == "CLOSE") {
    if (lk.mode == LockMode::FailSecure) {
      lk.coilOn = false;
      lk.pulsing = false;
      lk.cooldown = true;
      lk.bootGuard = false;
      lk.cooldownStartMs = millis();
      applyLockOutput(lk);
      publishLockState(lk, "cmd:CLOSE");
    } else {
      setFailSafe(lk, true, "cmd:CLOSE");
    }
    return;
  }

  log("WRN", String("Unknown lock cmd for ") + lk.id + ": " + cmd);
}

void MaglockController::log(const char* level, const String& msg) const {
  if (!ctx_) return;
  ctx_->log(level, msg);
}

void MaglockController::log(const char* level, const String& msg, const String& dataJson) const {
  if (!ctx_) return;
  ctx_->log(level, msg, dataJson);
}

bool MaglockController::publish(const char* topic, const char* type, uint32_t version, const String& dataJson,
                                const char* id, bool retained) const {
  if (!ctx_) return false;
  return ctx_->publishEnvelope(topic, type, version, dataJson, id, retained);
}

bool MaglockController::publish(const char* topic, const String& payload, bool retained) const {
  if (!ctx_) return false;
  return ctx_->publish(topic, payload, retained);
}

void MaglockController::handleLockCommandTopicInternal(const String& topic, const String& payload) {
  String lockId;
  if (!parseLockIdFromTopic(topic, lockId)) return;

  LockState* lk = findLockById(lockId);
  if (!lk) {
    log("ERR", String("Lock id not found: ") + lockId);
    return;
  }
  handleLockCommand(*lk, payload);
}

uint32_t MaglockController::hbIntervalForMode(GameMode mode) const {
  (void)mode;
  return 20000;
}

void MaglockController::applyHeartbeatInterval() {
  if (!ctx_) return;
  ctx_->setHeartbeatInterval(hbIntervalForMode(gameMode_));
}
