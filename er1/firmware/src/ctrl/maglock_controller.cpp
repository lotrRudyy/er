#include "ctrl/maglock_controller.h"

#include <ArduinoJson.h>
#include <cstring>

namespace {
constexpr const char* kPrefsGameModeKey = "game_mode";
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
      lk.cooldown = true;
      lk.bootGuard = true;
      lk.cooldownStartMs = bootMs_;
    }
    applyLockOutput(lk);
  }

  gameMode_ = GameMode::ModeStandby;
  lastMetricMs_ = millis();
  applyModeDefaults("boot");
  applyHeartbeatInterval();
  publishStateSnapshot();
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

void MaglockController::onGameStateMessage(const String& msg) {
  GameMode parsed = gameMode_;
  if (!parseGameStatePayload(msg, parsed)) {
    log("WRN", "GAME_STATE_PARSE_FAILED", String("{\"payload\":") + "\"" + msg + "\"}");
    return;
  }
  setGameModeInternal(parsed, "game_state");
}

void MaglockController::onMaglockCommandMessage(const String& payload) {
  String cmd;
  String lock;
  bool enabled = false;
  if (!parseMaglockCommand(payload, cmd, lock, enabled)) {
    log("WRN", "MAGLOCK_CMD_PARSE_FAILED", String("{\"payload\":") + "\"" + payload + "\"}");
    return;
  }

  cmd.toLowerCase();
  if (cmd == "set_mode") {
    GameMode newMode = gameMode_;
    if (lock.equalsIgnoreCase("MODE_MAINTENANCE")) newMode = GameMode::ModeMaintenance;
    else if (lock.equalsIgnoreCase("MODE_STANDBY")) newMode = GameMode::ModeStandby;
    else if (lock.equalsIgnoreCase("MODE_PREPARE")) newMode = GameMode::ModePrepare;
    else if (lock.equalsIgnoreCase("MODE_INGAME")) newMode = GameMode::ModeInGame;
    else {
      log("WRN", String("Unknown set_mode value: ") + lock);
      return;
    }
    setGameModeInternal(newMode, "cmd:set_mode");
    return;
  }

  if (cmd == "open" || cmd == "close") {
    LockState* lk = findLockById(lock);
    if (!lk) {
      log("ERR", String("Lock id not found: ") + lock);
      return;
    }
    if (cmd == "open") {
      if (lk->mode == LockMode::FailSecure) startPulse(*lk, "cmd:open");
      else setFailSafe(*lk, false, "cmd:open");
    } else {
      if (lk->mode == LockMode::FailSecure) {
        lk->coilOn = false;
        lk->pulsing = false;
        lk->cooldown = true;
        lk->bootGuard = false;
        lk->cooldownStartMs = millis();
        applyLockOutput(*lk);
        publishLockState(*lk, "cmd:close");
      } else {
        setFailSafe(*lk, true, "cmd:close");
      }
    }
    return;
  }

  if (cmd == "set_fail_safe") {
    LockState* lk = findLockById(lock);
    if (!lk) {
      log("ERR", String("Lock id not found: ") + lock);
      return;
    }
    if (lk->mode != LockMode::FailSafe) {
      log("WRN", String("set_fail_safe on non-failsafe: ") + lock);
      return;
    }
    setFailSafe(*lk, enabled, "cmd:set_fail_safe");
    return;
  }

  log("WRN", String("Unknown maglock cmd: ") + cmd);
}

uint32_t MaglockController::currentHeartbeatIntervalMs() const {
  return hbIntervalForMode(gameMode_);
}

bool MaglockController::shouldAllowLog(const char* level) {
  const bool isErr = (strcmp(level, "ERR") == 0);
  const bool isDbg = (strcmp(level, "DBG") == 0);
  bool allow = false;
  if (isErr) {
    allow = true;
  } else if (gameMode_ == GameMode::ModeStandby) {
    allow = false;
  } else if (gameMode_ == GameMode::ModeInGame) {
    allow = !isDbg;
  } else {
    allow = true;
  }
  if (isErr) errorCount_++;
  return allow;
}

void MaglockController::applyLockOutput(LockState& lk) {
  driver_.setCoil(lk.pin, lk.coilOn);
}

const char* MaglockController::lockStateName(const LockState& lk) const {
  if (lk.mode == LockMode::FailSecure) return lk.coilOn ? "OPEN" : "CLOSED";
  return lk.coilOn ? "CLOSED" : "OPEN";
}

const char* MaglockController::gameModeName(GameMode mode) const {
  switch (mode) {
    case GameMode::ModeMaintenance: return "MODE_MAINTENANCE";
    case GameMode::ModePrepare: return "MODE_PREPARE";
    case GameMode::ModeInGame: return "MODE_INGAME";
    case GameMode::ModeStandby:
    default: return "MODE_STANDBY";
  }
}

void MaglockController::publishLockState(const LockState& lk, const char* reason) {
  if (!ctx_) return;
  String topic = String("maglock/lock/") + lk.id + "/state";
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
    if (id.equalsIgnoreCase(lk.id)) return &lk;
    if (lk.id && String(lk.id).equalsIgnoreCase("r2r3") && id.equalsIgnoreCase("r3")) return &lk;
  }
  return nullptr;
}

void MaglockController::forceAllFailSecureOff(const char* reason) {
  for (auto& lk : locks_) {
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

void MaglockController::applyModeDefaults(const char* reason) {
  if (gameMode_ == GameMode::ModeMaintenance) {
    forceAllFailSecureOff(reason);
    if (LockState* r2 = findLockById("r2")) setFailSafe(*r2, false, reason);
    if (LockState* r2r3 = findLockById("r2r3")) setFailSafe(*r2r3, false, reason);
    return;
  }

  if (gameMode_ == GameMode::ModePrepare || gameMode_ == GameMode::ModeInGame) {
    forceAllFailSecureOff(reason);
    if (LockState* r2 = findLockById("r2")) setFailSafe(*r2, true, reason);
    if (LockState* r2r3 = findLockById("r2r3")) setFailSafe(*r2r3, true, reason);
    return;
  }

  forceAllFailSecureOff(reason);
  if (LockState* r2 = findLockById("r2")) setFailSafe(*r2, false, reason);
  if (LockState* r2r3 = findLockById("r2r3")) setFailSafe(*r2r3, false, reason);
}

void MaglockController::setGameModeInternal(GameMode newMode, const char* reason) {
  const GameMode old = gameMode_;
  gameMode_ = newMode;
  if (old != newMode) {
    String data = String("{\"from\":\"") + gameModeName(old) +
                  "\",\"to\":\"" + gameModeName(newMode) +
                  "\",\"reason\":\"" + (reason ? reason : "") + "\"}";
    log("INF", "MAGLOCK_MODE_CHANGED", data);
  }
  applyHeartbeatInterval();
  applyModeDefaults(reason ? reason : "mode");
  if (prefs_) prefs_->putString(kPrefsGameModeKey, gameModeName(gameMode_));
  publishStateSnapshot();
}

bool MaglockController::parseGameStatePayload(const String& msg, GameMode& outMode) {
  String trimmed = msg;
  trimmed.trim();
  if (trimmed.startsWith("{")) {
    StaticJsonDocument<384> doc;
    DeserializationError err = deserializeJson(doc, trimmed);
    if (err) return false;
    const char* mode = doc["mode"] | nullptr;
    if (!mode) return false;
    String m(mode);
    if (m.equalsIgnoreCase("MODE_MAINTENANCE")) outMode = GameMode::ModeMaintenance;
    else if (m.equalsIgnoreCase("MODE_STANDBY")) outMode = GameMode::ModeStandby;
    else if (m.equalsIgnoreCase("MODE_PREPARE")) outMode = GameMode::ModePrepare;
    else if (m.equalsIgnoreCase("MODE_INGAME")) outMode = GameMode::ModeInGame;
    else return false;
    return true;
  }

  trimmed.toUpperCase();
  if (trimmed == "MODE_MAINTENANCE" || trimmed == "MAINT" || trimmed == "MAINTENANCE") outMode = GameMode::ModeMaintenance;
  else if (trimmed == "MODE_STANDBY" || trimmed == "STANDBY") outMode = GameMode::ModeStandby;
  else if (trimmed == "MODE_PREPARE" || trimmed == "PREPARE") outMode = GameMode::ModePrepare;
  else if (trimmed == "MODE_INGAME" || trimmed == "INGAME") outMode = GameMode::ModeInGame;
  else return false;
  return true;
}

bool MaglockController::parseMaglockCommand(const String& payload, String& outCmd, String& outLock, bool& outEnabled) {
  StaticJsonDocument<384> doc;
  DeserializationError err = deserializeJson(doc, payload);
  if (err) return false;
  outCmd = String((const char*)(doc["cmd"] | ""));
  if (doc.containsKey("lock")) outLock = String((const char*)(doc["lock"] | ""));
  else if (doc.containsKey("mode")) outLock = String((const char*)(doc["mode"] | ""));
  outEnabled = doc["enabled"] | false;

  if (outCmd == "set_fail_safe") {
    if (doc.containsKey("locks") && doc["locks"].is<JsonArray>()) {
      JsonArray arr = doc["locks"].as<JsonArray>();
      for (JsonVariant v : arr) {
        String id = String((const char*)(v | ""));
        if (id.length() == 0) continue;
        LockState* lk = findLockById(id);
        if (!lk) continue;
        if (lk->mode != LockMode::FailSafe) continue;
        setFailSafe(*lk, outEnabled, "cmd:set_fail_safe");
      }
      outCmd = "noop_done";
      return true;
    }
  }
  return outCmd.length() > 0;
}

void MaglockController::startPulse(LockState& lk, const char* reason) {
  if (lk.mode != LockMode::FailSecure) {
    log("WRN", String("OPEN on non-failsecure via pulse: ") + lk.id);
    return;
  }
  if (gameMode_ == GameMode::ModeStandby) {
    log("WRN", String("OPEN ignored while MODE_STANDBY for ") + lk.id);
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

  String payload = String("{\"fw\":\"") + ctx_->fwVersion() +
                   "\",\"up\":" + String(nowMs / 1000) +
                   ",\"t\":\"MAG\"" +
                   ",\"mode\":\"" + String(gameModeName(gameMode_)) + "\"" +
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
  String data = String("{\"mode\":\"") + gameModeName(gameMode_) + "\",\"locks\":[";
  for (size_t i = 0; i < kLockCount; i++) {
    if (i > 0) data += ",";
    data += "{\"id\":\"";
    data += locks_[i].id;
    data += "\",\"coil\":";
    data += locks_[i].coilOn ? "1" : "0";
    data += ",\"state\":\"";
    data += lockStateName(locks_[i]);
    data += "\",\"pulse\":";
    data += locks_[i].pulsing ? "1" : "0";
    data += ",\"cooldown\":";
    data += locks_[i].cooldown ? "1" : "0";
    data += ",\"bootGuard\":";
    data += locks_[i].bootGuard ? "1" : "0";
    data += "}";
  }
  data += "]}";
  const auto& topics = ctx_->config().topics;
  if (topics.state.length() > 0) publish(topics.state.c_str(), "state", 1, data, nullptr, true);
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

uint32_t MaglockController::hbIntervalForMode(GameMode mode) const {
  (void)mode;
  return 5000;
}

void MaglockController::applyHeartbeatInterval() {
  if (!ctx_) return;
  ctx_->setHeartbeatInterval(hbIntervalForMode(gameMode_));
}
