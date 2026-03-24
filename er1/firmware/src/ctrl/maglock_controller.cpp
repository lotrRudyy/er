#include "ctrl/maglock_controller.h"

#include <ArduinoJson.h>
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

const char* MaglockController::modeName(GameMode mode) {
  switch (mode) {
    case GameMode::InGame: return "MODE_INGAME";
    case GameMode::Prepare: return "MODE_PREPARE";
    case GameMode::Maint: return "MODE_MAINTENANCE";
    case GameMode::Standby:
    default: return "MODE_STANDBY";
  }
}

MaglockController::GameMode MaglockController::phaseToMode(int phase) {
  if (phase == 0) return GameMode::Maint;
  if (phase == 1) return GameMode::Standby;
  if (phase == 2) return GameMode::Prepare;
  return GameMode::InGame;
}

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

  for (auto& lk : locks_) {
    if (lk.mode == LockMode::FailSecure) {
      lk.coilOn = false;
      lk.pulsing = false;
      lk.cooldown = false;
      lk.bootGuard = false;
      lk.cooldownStartMs = bootMs_;
    }
    applyLockOutput(lk);
  }

  loadGameMode();

  lastMetricMs_ = millis();
  applyHeartbeatInterval();
  publishStateSnapshot();
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

void MaglockController::applyPhase(int newPhase, const char* reason) {
  if (!ctx_) return;

  int oldPhase = currentPhase_;
  const bool changed = (oldPhase != newPhase);
  currentPhase_ = newPhase;

  GameMode newMode = phaseToMode(newPhase);
  GameMode oldMode = gameMode_;
  gameMode_ = newMode;

  if (changed) {
    String data = String("{\"from_phase\":") + String(oldPhase) +
                  ",\"to_phase\":" + String(currentPhase_) +
                  ",\"from_mode\":\"" + modeName(oldMode) +
                  "\",\"to_mode\":\"" + modeName(gameMode_) + "\"}";
    log("INF", reason && reason[0] ? reason : "phase changed", data);
    applyHeartbeatInterval();
    persistGameMode();

    bool r2Locked = false;
    bool r3Locked = false;
    switch (currentPhase_) {
      case 0: // maintenance
      case 1: // standby
      case 14: // solved
        r2Locked = false;
        r3Locked = false;
        break;
      case 2: // prepare
      case 3: // images
      case 4: // piano
        r2Locked = true;
        r3Locked = true;
        break;
      case 5: // open_prison
      case 6: // mount_wheel
      case 7: // rope_paths
      case 8: // tangram+magnet
      case 9: // chess
        r2Locked = false;
        r3Locked = true;
        break;
      case 10: // knocking + candles pre
      case 11: // candles
      case 12: // star_slider
      case 13: // sissi
      default:
        r2Locked = false;
        r3Locked = false;
        break;
    }

    if (LockState* r2 = findLockById("r2")) setFailSafe(*r2, r2Locked, "phase_apply");
    if (LockState* r3 = findLockById("r3")) setFailSafe(*r3, r3Locked, "phase_apply");
  }

  publishStateSnapshot();
}

void MaglockController::onGameModeMessage(const String& msg) {
  if (!ctx_) return;

  JsonDocument doc;
  DeserializationError err = deserializeJson(doc, msg);
  if (err) {
    String trimmed = msg;
    trimmed.trim();
    trimmed.toUpperCase();
    if (trimmed == "MODE_MAINTENANCE" || trimmed == "MAINT" || trimmed == "MAINTENANCE") {
      applyPhase(0, "game_state_fallback_mode");
    } else if (trimmed == "MODE_PREPARE" || trimmed == "PREPARE") {
      applyPhase(2, "game_state_fallback_mode");
    } else if (trimmed == "MODE_INGAME" || trimmed == "INGAME") {
      applyPhase(3, "game_state_fallback_mode");
    } else {
      applyPhase(1, "game_state_fallback_mode");
    }
    return;
  }

  if (doc["phase"].is<int>()) {
    applyPhase(doc["phase"].as<int>(), "game_state_phase");
    return;
  }

  String mode = String((const char*)(doc["mode"] | ""));
  mode.trim();
  if (mode == "MODE_MAINTENANCE") {
    applyPhase(0, "game_state_mode");
  } else if (mode == "MODE_PREPARE") {
    applyPhase(2, "game_state_mode");
  } else if (mode == "MODE_INGAME") {
    applyPhase(3, "game_state_mode");
  } else {
    applyPhase(1, "game_state_mode");
  }
}

void MaglockController::onLockCommandTopic(const char* topic, const String& payload) {
  handleLockCommandTopicInternal(String(topic ? topic : ""), payload);
}

void MaglockController::onMaglockCommandTopic(const String& payload) {
  JsonDocument doc;
  DeserializationError err = deserializeJson(doc, payload);
  if (err) {
    log("WRN", String("maglock/cmd json parse failed: ") + err.c_str());
    return;
  }

  String cmd = String((const char*)(doc["cmd"] | ""));
  cmd.trim();
  cmd.toUpperCase();
  if (!cmd.length()) {
    log("WRN", "maglock/cmd missing cmd");
    return;
  }

  if (cmd == "SET_PHASE") {
    if (!doc["phase"].is<int>()) {
      log("WRN", "set_phase requires integer phase");
      return;
    }
    applyPhase(doc["phase"].as<int>(), "maglock_cmd_set_phase");
    return;
  }

  if (cmd == "SET_MODE") {
    String mode = String((const char*)(doc["mode"] | ""));
    mode.trim();
    mode.toUpperCase();
    if (mode == "MODE_MAINTENANCE" || mode == "MAINTENANCE" || mode == "MAINT") applyPhase(0, "maglock_cmd_set_mode");
    else if (mode == "MODE_PREPARE" || mode == "PREPARE") applyPhase(2, "maglock_cmd_set_mode");
    else if (mode == "MODE_INGAME" || mode == "INGAME") applyPhase(3, "maglock_cmd_set_mode");
    else applyPhase(1, "maglock_cmd_set_mode");
    return;
  }

  if (cmd == "SET_FAIL_SAFE") {
    bool enabled = doc["enabled"].is<bool>() ? doc["enabled"].as<bool>() : true;
    JsonArray locks = doc["locks"].as<JsonArray>();
    if (locks.isNull()) {
      log("WRN", "set_fail_safe requires locks[]");
      return;
    }
    for (JsonVariant v : locks) {
      const char* id = v.as<const char*>();
      if (!id) continue;
      LockState* lk = findLockById(String(id));
      if (!lk) continue;
      if (lk->mode == LockMode::FailSafe) {
        setFailSafe(*lk, enabled, enabled ? "cmd:set_fail_safe_locked" : "cmd:set_fail_safe_open");
      }
    }
    publishStateSnapshot();
    return;
  }

  if (cmd == "OPEN" || cmd == "CLOSE") {
    String lock = String((const char*)(doc["lock"] | ""));
    if (!lock.length()) {
      log("WRN", String("maglock/cmd missing lock for ") + cmd);
      return;
    }
    LockState* lk = findLockById(lock);
    if (!lk) {
      log("ERR", String("Lock id not found: ") + lock);
      return;
    }
    handleLockCommand(*lk, cmd);
    return;
  }

  log("WRN", String("Unknown maglock/cmd: ") + cmd, payload);
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
  } else if (gameMode_ == GameMode::Standby) {
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
  publish(topic.c_str(), payload, true);
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
  prefs_->putString(kPrefsGameModeKey, modeName(gameMode_));
}

void MaglockController::loadGameMode() {
  if (!prefs_) {
    gameMode_ = GameMode::Standby;
    return;
  }

  String stored = prefs_->getString(kPrefsGameModeKey, "MODE_STANDBY");
  stored.trim();
  stored.toUpperCase();

  gameMode_ = GameMode::Standby;

  String data = String("{\"stored\":\"") + stored + "\",\"boot\":\"MODE_STANDBY\"}";
  log("INF", "MAGLOCK_BOOT_MODE", data);
}

void MaglockController::startPulse(LockState& lk, const char* reason) {
  if (lk.mode != LockMode::FailSecure) {
    log("WRN", String("OPEN on non-failsecure via pulse: ") + lk.id);
    return;
  }

  if (lk.pulsing || lk.coilOn) {
    log("WRN", String("OPEN ignored (already pulsing) for ") + lk.id);
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

    if (lk.coilOn && (nowMs - lk.pulseStartMs >= kHardCutoffMs)) {
      lk.coilOn = false;
      lk.pulsing = false;
      lk.cooldown = false;
      lk.bootGuard = false;
      lk.cooldownStartMs = nowMs;
      applyLockOutput(lk);
      publishLockState(lk, "hard_cutoff");
      continue;
    }

    if (lk.pulsing && (nowMs - lk.pulseStartMs >= kPulseMs)) {
      lk.pulsing = false;
      lk.coilOn = false;
      lk.cooldown = false;
      lk.bootGuard = false;
      lk.cooldownStartMs = nowMs;
      applyLockOutput(lk);
      publishLockState(lk, "pulse_done");
    }
  }
}

void MaglockController::publishMetricsIfDue(uint32_t nowMs) {
  if (!ctx_) return;
  if (nowMs - lastMetricMs_ < kMetricIntervalMs) return;
  lastMetricMs_ = nowMs;

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

  log("DBG", "maglock_metrics", payload);
}

void MaglockController::publishStateSnapshot() {
  if (!ctx_) return;
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
  String cmd = payload;
  cmd.trim();
  cmd.toUpperCase();
  handleLockCommand(*lk, cmd);
}

uint32_t MaglockController::hbIntervalForMode(GameMode mode) const {
  (void)mode;
  return 5000;
}

void MaglockController::applyHeartbeatInterval() {
  if (!ctx_) return;
  ctx_->setHeartbeatInterval(hbIntervalForMode(gameMode_));
}
