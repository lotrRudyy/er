#include "ctrl/maglock_controller.h"

#include <ArduinoJson.h>
#include <cstring>

namespace {

constexpr const char* kLockCmdPrefix = "maglock/lock/";
constexpr const char* kLockCmdSuffix = "/cmd";
constexpr const char* kLockStatePrefix = "maglock/lock/";

String makeLockStateTopic(const char* id) {
  String topic = kLockStatePrefix;
  topic += id;
  topic += "/state";
  return topic;
}

bool parseLockIdFromTopic(const String& topic, String& outId) {
  if (!topic.startsWith(kLockCmdPrefix) || !topic.endsWith(kLockCmdSuffix)) return false;
  const int start = (int)strlen(kLockCmdPrefix);
  const int end = topic.length() - (int)strlen(kLockCmdSuffix);
  if (end <= start) return false;
  outId = topic.substring(start, end);
  return outId.length() > 0;
}

String upperTrim(String s) {
  s.trim();
  s.toUpperCase();
  return s;
}

}  // namespace

void MaglockController::begin(Core::NodeContext& ctx) {
  ctx_ = &ctx;
  bootMs_ = millis();
  lastMetricMs_ = bootMs_;

  MaglockChannelConfig channels[kLockCount];
  for (size_t i = 0; i < kLockCount; i++) {
    channels[i] = {locks_[i].id, locks_[i].pin};
    locks_[i].coilOn = false;
    locks_[i].pulsing = false;
    locks_[i].cooldown = (locks_[i].mode == LockMode::FailSecure);
    locks_[i].bootGuard = (locks_[i].mode == LockMode::FailSecure);
    locks_[i].pulseStartMs = 0;
    locks_[i].cooldownStartMs = bootMs_;
    locks_[i].pulseCount = 0;
  }

  driver_.begin(channels, kLockCount);

  for (size_t i = 0; i < kLockCount; i++) {
    applyLockOutput(locks_[i]);
  }

  applyModeDefaults(GlobalMode::Standby, "boot_standby");
  publishStateSnapshot("boot");
}

void MaglockController::tick(uint32_t nowMs) {
  if (!ctx_) return;
  updatePulseTimers(nowMs);
  publishMetricsIfDue(nowMs);
}

bool MaglockController::onCmd(const char* cmd, const char* payload) {
  String cmdStr(cmd ? cmd : "");
  String payloadStr(payload ? payload : "");
  cmdStr.trim();
  payloadStr.trim();

  if (cmdStr.startsWith("{")) {
    onMaglockCommandTopic(cmdStr);
    return true;
  }
  if (payloadStr.startsWith("{")) {
    onMaglockCommandTopic(payloadStr);
    return true;
  }

  String upper = upperTrim(cmdStr);
  if (upper == "OPEN" && payloadStr.length()) {
    onMaglockCommandTopic(String("{\"cmd\":\"open\",\"lock\":\"") + payloadStr + "\"}");
    return true;
  }
  if (upper == "CLOSE" && payloadStr.length()) {
    onMaglockCommandTopic(String("{\"cmd\":\"close\",\"lock\":\"") + payloadStr + "\"}");
    return true;
  }

  log("WRN", String("Unknown node CMD: ") + cmdStr + (payloadStr.length() ? String(" ") + payloadStr : String("")));
  return true;
}

bool MaglockController::shouldAllowLog(const char* level) {
  const bool isErr = (strcmp(level, "ERR") == 0);
  const bool isDbg = (strcmp(level, "DBG") == 0);
  if (isErr) errorCount_++;
  if (mode_ == GlobalMode::Standby && !isErr) return false;
  if (mode_ == GlobalMode::InGame && isDbg) return false;
  return true;
}

void MaglockController::onGameStateMessage(const String& payload) {
  JsonDocument doc;
  DeserializationError err = deserializeJson(doc, payload);
  if (err) {
    log("WRN", String("maglock game/state json parse failed: ") + err.c_str());
    return;
  }

  String modeStr = String((const char*)(doc["mode"] | ""));
  modeStr.trim();

  GlobalMode newMode = GlobalMode::Standby;
  if (modeStr == "MODE_MAINTENANCE") newMode = GlobalMode::Maintenance;
  else if (modeStr == "MODE_STANDBY") newMode = GlobalMode::Standby;
  else if (modeStr == "MODE_PREPARE") newMode = GlobalMode::Prepare;
  else if (modeStr == "MODE_INGAME") newMode = GlobalMode::InGame;
  else {
    log("WRN", String("unknown maglock game/state mode: ") + modeStr);
    return;
  }

  mode_ = newMode;
  applyModeDefaults(newMode, "game_state");
  publishStateSnapshot("game_state");
}

void MaglockController::onMaglockCommandTopic(const String& payload) {
  JsonDocument doc;
  DeserializationError err = deserializeJson(doc, payload);
  if (err) {
    log("WRN", String("maglock/cmd json parse failed: ") + err.c_str());
    return;
  }

  auto pickString = [&](const char* a, const char* b = nullptr) -> String {
    if (a && doc[a].is<const char*>()) return String(doc[a].as<const char*>());
    if (b && doc[b].is<const char*>()) return String(doc[b].as<const char*>());
    return String();
  };

  String cmd = upperTrim(pickString("cmd", "CMD"));
  if (!cmd.length()) {
    log("WRN", String("maglock/cmd missing cmd payload=") + payload);
    return;
  }

  if (cmd == "OPEN" || cmd == "CLOSE") {
    String lockId = pickString("lock", "LOCK");
    if (!lockId.length()) {
      log("WRN", String("maglock/cmd missing lock for ") + cmd);
      return;
    }
    LockState* lk = findLockById(lockId);
    if (!lk) {
      log("WRN", String("unknown lock: ") + lockId);
      return;
    }
    handleSingleLockCommand(*lk, cmd, "maglock_cmd");
    return;
  }

  if (cmd == "SET_MODE") {
    String modeStr = pickString("mode", "MODE");
    modeStr.trim();
    if (modeStr == "MODE_MAINTENANCE") mode_ = GlobalMode::Maintenance;
    else if (modeStr == "MODE_STANDBY") mode_ = GlobalMode::Standby;
    else if (modeStr == "MODE_PREPARE") mode_ = GlobalMode::Prepare;
    else if (modeStr == "MODE_INGAME") mode_ = GlobalMode::InGame;
    else {
      log("WRN", String("unknown set_mode value: ") + modeStr);
      return;
    }
    applyModeDefaults(mode_, "cmd_set_mode");
    publishStateSnapshot("cmd_set_mode");
    return;
  }

  if (cmd == "SET_FAIL_SAFE") {
    const bool enabled = doc["enabled"].is<bool>() ? doc["enabled"].as<bool>() : true;
    JsonArray arr = doc["locks"].as<JsonArray>();
    if (arr.isNull()) arr = doc["LOCKS"].as<JsonArray>();

    if (!arr.isNull()) {
      for (JsonVariant v : arr) {
        const char* id = v.as<const char*>();
        if (!id) continue;
        LockState* lk = findLockById(String(id));
        if (!lk) continue;
        if (lk->mode != LockMode::FailSafe) continue;
        setFailSafe(*lk, enabled, enabled ? "cmd_failsafe_locked" : "cmd_failsafe_open");
      }
      publishStateSnapshot(enabled ? "cmd_failsafe_locked" : "cmd_failsafe_open");
      return;
    }

    String lockId = pickString("lock", "LOCK");
    if (!lockId.length()) {
      log("WRN", String("set_fail_safe requires lock or locks[] payload=") + payload);
      return;
    }
    LockState* lk = findLockById(lockId);
    if (!lk || lk->mode != LockMode::FailSafe) {
      log("WRN", String("set_fail_safe invalid lock: ") + lockId);
      return;
    }
    setFailSafe(*lk, enabled, enabled ? "cmd_failsafe_locked" : "cmd_failsafe_open");
    publishStateSnapshot(enabled ? "cmd_failsafe_locked" : "cmd_failsafe_open");
    return;
  }

  log("WRN", String("unknown maglock/cmd: ") + cmd, payload);
}

void MaglockController::onLockCommandTopic(const char* topic, const String& payload) {
  handleLegacyLockCommandTopicInternal(String(topic ? topic : ""), payload);
}

void MaglockController::applyLockOutput(LockState& lk) {
  driver_.setCoil(lk.pin, lk.coilOn);
}

const char* MaglockController::lockStateName(const LockState& lk) const {
  if (lk.mode == LockMode::FailSecure) return lk.coilOn ? "OPEN" : "CLOSED";
  return lk.coilOn ? "CLOSED" : "OPEN";
}

MaglockController::LockState* MaglockController::findLockById(const String& id) {
  for (size_t i = 0; i < kLockCount; i++) {
    if (id.equalsIgnoreCase(locks_[i].id)) return &locks_[i];
  }
  return nullptr;
}

void MaglockController::publishLockState(const LockState& lk, const char* reason) {
  if (!ctx_) return;
  String payload = String("{\"fw\":\"") + ctx_->fwVersion() +
                   "\",\"up\":" + String(millis() / 1000) +
                   ",\"id\":\"" + lk.id + "\"" +
                   ",\"state\":\"" + String(lockStateName(lk)) + "\"" +
                   ",\"coil\":" + String(lk.coilOn ? 1 : 0) +
                   ",\"pulse\":" + String(lk.pulsing ? 1 : 0) +
                   ",\"cooldown\":" + String(lk.cooldown ? 1 : 0) +
                   ",\"bootGuard\":" + String(lk.bootGuard ? 1 : 0) +
                   ",\"pulses\":" + String(lk.pulseCount);
  if (reason && reason[0]) {
    payload += ",\"reason\":\"";
    payload += reason;
    payload += "\"";
  }
  payload += "}";
  String topic = makeLockStateTopic(lk.id);
  publish(topic.c_str(), payload, true);
}

void MaglockController::publishStateSnapshot(const char* reason) {
  if (!ctx_) return;
  const char* modeName = "MODE_STANDBY";
  switch (mode_) {
    case GlobalMode::Maintenance: modeName = "MODE_MAINTENANCE"; break;
    case GlobalMode::Standby: modeName = "MODE_STANDBY"; break;
    case GlobalMode::Prepare: modeName = "MODE_PREPARE"; break;
    case GlobalMode::InGame: modeName = "MODE_INGAME"; break;
  }
  String payload = String("{\"mode\":\"") + modeName + "\",\"locks\":[";
  for (size_t i = 0; i < kLockCount; i++) {
    if (i > 0) payload += ",";
    payload += String("{\"id\":\"") + locks_[i].id +
               "\",\"state\":\"" + lockStateName(locks_[i]) +
               "\",\"coil\":" + String(locks_[i].coilOn ? 1 : 0) +
               ",\"pulse\":" + String(locks_[i].pulsing ? 1 : 0) +
               ",\"cooldown\":" + String(locks_[i].cooldown ? 1 : 0) +
               ",\"bootGuard\":" + String(locks_[i].bootGuard ? 1 : 0) + "}";
  }
  payload += "]";
  if (reason && reason[0]) {
    payload += ",\"reason\":\"";
    payload += reason;
    payload += "\"";
  }
  payload += "}";
  const auto& topics = ctx_->config().topics;
  if (topics.state.length() > 0) publish(topics.state.c_str(), payload, true);
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

void MaglockController::startPulse(LockState& lk, const char* reason) {
  if (lk.mode != LockMode::FailSecure) {
    log("WRN", String("OPEN pulse on non-failsecure: ") + lk.id);
    return;
  }
  if (lk.pulsing || lk.cooldown || lk.bootGuard) {
    log("WRN", String("OPEN ignored for ") + lk.id + " (pulse/cooldown/bootguard active)");
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

void MaglockController::forceFailSecureSafe(const char* reason) {
  for (size_t i = 0; i < kLockCount; i++) {
    LockState& lk = locks_[i];
    if (lk.mode != LockMode::FailSecure) continue;
    const bool changed = lk.coilOn || lk.pulsing;
    lk.coilOn = false;
    lk.pulsing = false;
    lk.cooldown = true;
    lk.bootGuard = true;
    lk.cooldownStartMs = millis();
    applyLockOutput(lk);
    if (changed) publishLockState(lk, reason);
  }
}

void MaglockController::applyModeDefaults(GlobalMode mode, const char* reason) {
  if (mode == GlobalMode::Maintenance || mode == GlobalMode::Standby) {
    forceFailSecureSafe(reason);
    if (LockState* r2 = findLockById("r2")) setFailSafe(*r2, false, reason);
    if (LockState* r3 = findLockById("r3")) setFailSafe(*r3, false, reason);
    return;
  }
  if (mode == GlobalMode::Prepare || mode == GlobalMode::InGame) {
    forceFailSecureSafe(reason);
    if (LockState* r2 = findLockById("r2")) setFailSafe(*r2, true, reason);
    if (LockState* r3 = findLockById("r3")) setFailSafe(*r3, true, reason);
    return;
  }
}

void MaglockController::updatePulseTimers(uint32_t nowMs) {
  for (size_t i = 0; i < kLockCount; i++) {
    LockState& lk = locks_[i];
    if (lk.mode != LockMode::FailSecure) continue;

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
      lk.cooldown = true;
      lk.bootGuard = false;
      lk.cooldownStartMs = nowMs;
      applyLockOutput(lk);
      publishLockState(lk, "pulse_done");
      continue;
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

  const char* modeName = "MODE_STANDBY";
  switch (mode_) {
    case GlobalMode::Maintenance: modeName = "MODE_MAINTENANCE"; break;
    case GlobalMode::Standby: modeName = "MODE_STANDBY"; break;
    case GlobalMode::Prepare: modeName = "MODE_PREPARE"; break;
    case GlobalMode::InGame: modeName = "MODE_INGAME"; break;
  }

  String payload = String("{\"fw\":\"") + ctx_->fwVersion() +
                   "\",\"up\":" + String(nowMs / 1000) +
                   ",\"mode\":\"" + modeName + "\",\"locks\":[";
  for (size_t i = 0; i < kLockCount; i++) {
    if (i > 0) payload += ",";
    payload += String("{\"id\":\"") + locks_[i].id +
               "\",\"coil\":" + String(locks_[i].coilOn ? 1 : 0) +
               ",\"pulse\":" + String(locks_[i].pulsing ? 1 : 0) +
               ",\"cooldown\":" + String(locks_[i].cooldown ? 1 : 0) +
               ",\"bootGuard\":" + String(locks_[i].bootGuard ? 1 : 0) +
               ",\"pulses\":" + String(locks_[i].pulseCount) + "}";
  }
  payload += "]}";
  log("DBG", "maglock_metrics", payload);
}

void MaglockController::handleSingleLockCommand(LockState& lk, const String& cmd, const char* reason) {
  if (!ctx_ || !ctx_->enabled()) {
    log("WRN", String("lock cmd while disabled: ") + lk.id + " cmd=" + cmd);
    return;
  }
  if (cmd == "OPEN") {
    if (lk.mode == LockMode::FailSecure) startPulse(lk, reason);
    else setFailSafe(lk, false, reason);
    publishStateSnapshot(reason);
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
      publishLockState(lk, reason);
    } else {
      setFailSafe(lk, true, reason);
    }
    publishStateSnapshot(reason);
    return;
  }
  log("WRN", String("unknown lock cmd for ") + lk.id + ": " + cmd);
}

void MaglockController::handleLegacyLockCommandTopicInternal(const String& topic, const String& payload) {
  String lockId;
  if (!parseLockIdFromTopic(topic, lockId)) return;
  LockState* lk = findLockById(lockId);
  if (!lk) {
    log("ERR", String("lock id not found: ") + lockId);
    return;
  }
  handleSingleLockCommand(*lk, upperTrim(payload), "legacy_lock_cmd");
}

void MaglockController::log(const char* level, const String& msg) const {
  if (ctx_) ctx_->log(level, msg);
}

void MaglockController::log(const char* level, const String& msg, const String& dataJson) const {
  if (ctx_) ctx_->log(level, msg, dataJson);
}

bool MaglockController::publish(const char* topic, const String& payload, bool retained) const {
  if (!ctx_) return false;
  return ctx_->publish(topic, payload, retained);
}
