#include "ctrl/maglock_controller.h"

#include <ArduinoJson.h>
#include <cstring>

namespace {

constexpr const char* kLockCmdPrefix = "maglock/lock/";
constexpr const char* kLockStatePrefix = "maglock/lock/";
constexpr const char* kLockCmdSuffix = "/cmd";

constexpr MaglockController::PhaseFailSafeSpec kPhaseFailSafeTable[] = {
    {0, false, false},  // maintenance
    {1, false, false},  // standby
    {2, true, true},    // prepare
    {3, true, true},    // images
    {4, true, true},    // piano
    {5, false, true},   // open_prison
    {6, false, true},   // mount_wheel
    {7, false, true},   // rope_paths
    {8, false, true},   // tangram_magnet
    {9, false, true},   // chess
    {10, false, false}, // knocking_candles_pre
    {11, false, false}, // candles
    {12, false, false}, // star_slider
    {13, false, false}, // sissi
    {14, false, false}, // solved
};

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

const MaglockController::PhaseFailSafeSpec* MaglockController::findPhaseSpec(int phase) {
  for (const auto& spec : kPhaseFailSafeTable) {
    if (spec.phase == phase) return &spec;
  }
  return nullptr;
}

void MaglockController::begin(Core::NodeContext& ctx) {
  ctx_ = &ctx;
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
      lk.bootGuard = true;
      lk.cooldownStartMs = 0;
    }
    applyLockOutput(lk);
  }

  applyPhase(1, "boot_default_phase");
  lastMetricMs_ = millis();
  publishStateSnapshot();
  topicDbg_ = ctx.config().topics.dbg;
}

void MaglockController::tick(uint32_t nowMs) {
  if (!ctx_) return;
  updateProtectionWindows(nowMs);
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

void MaglockController::applyFailSafePhaseState(int phase, const char* reason) {
  const PhaseFailSafeSpec* spec = findPhaseSpec(phase);
  if (!spec) {
    log("WRN", String("Unknown phase for fail-safe mapping: ") + String(phase));
    return;
  }

  if (LockState* r2 = findLockById("r2")) setFailSafe(*r2, spec->r2Closed, reason);
  if (LockState* r3 = findLockById("r3")) setFailSafe(*r3, spec->r3Closed, reason);
}

void MaglockController::applyPhase(int newPhase, const char* reason) {
  if (!ctx_) return;

  const PhaseFailSafeSpec* spec = findPhaseSpec(newPhase);
  if (!spec) {
    log("WRN", String("Ignoring invalid phase: ") + String(newPhase));
    return;
  }

  const int oldPhase = currentPhase_;
  const bool changed = (oldPhase != newPhase);
  currentPhase_ = newPhase;

  if (changed) {
    String data = String("{\"from_phase\":") + String(oldPhase) +
                  ",\"to_phase\":" + String(currentPhase_) + "}";
    log("INF", reason && reason[0] ? reason : "phase changed", data);
    applyFailSafePhaseState(currentPhase_, "phase_apply");
  }

  publishStateSnapshot();
}

void MaglockController::onGameModeMessage(const String& msg) {
  if (!ctx_) return;

  JsonDocument doc;
  DeserializationError err = deserializeJson(doc, msg);
  if (!err && doc["phase"].is<int>()) {
    applyPhase(doc["phase"].as<int>(), "game_state_phase");
    return;
  }

  String trimmed = msg;
  trimmed.trim();
  if (trimmed.length() == 0) {
    log("WRN", "game/state empty payload");
    return;
  }

  bool numeric = true;
  size_t start = (trimmed[0] == '-') ? 1 : 0;
  for (size_t i = start; i < trimmed.length(); i++) {
    if (!isDigit(trimmed[i])) {
      numeric = false;
      break;
    }
  }
  if (numeric) {
    applyPhase(trimmed.toInt(), "game_state_phase_raw");
    return;
  }

  log("WRN", "game/state missing integer phase", msg);
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

  if (cmd == "SET_FAIL_SAFE") {
    bool closed = doc["enabled"].is<bool>() ? doc["enabled"].as<bool>() : true;
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
        setFailSafe(*lk, closed, closed ? "cmd:set_fail_safe_closed" : "cmd:set_fail_safe_open");
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
  return 5000;
}

bool MaglockController::shouldAllowLog(const char* level) {
  const bool isErr = (strcmp(level, "ERR") == 0);
  const bool isDbg = (strcmp(level, "DBG") == 0);
  bool allow = false;

  if (isErr) {
    allow = true;
  } else if (currentPhase_ == 1) {
    allow = false;
  } else if (currentPhase_ >= 3 && currentPhase_ <= 13) {
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
  payload += ",\"pulse\":";
  payload += lk.pulsing ? "1" : "0";
  payload += ",\"pulses\":";
  payload += String(lk.pulseCount);
  payload += ",\"cooldown\":";
  payload += lk.cooldown ? "1" : "0";
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

    const bool changed = lk.coilOn || lk.pulsing;

    lk.coilOn = false;
    lk.pulsing = false;
    lk.cooldown = true;
    lk.bootGuard = false;
    lk.cooldownStartMs = millis();

    applyLockOutput(lk);

    if (changed) {
      publishLockState(lk, reason);
    }
  }
}

void MaglockController::startPulse(LockState& lk, const char* reason) {
  if (lk.mode != LockMode::FailSecure) {
    log("WRN", String("OPEN on non-failsecure via pulse: ") + lk.id);
    return;
  }

  const uint32_t nowMs = millis();

  if (lk.bootGuard && (nowMs - bootMs_ < kBootGuardMs)) {
    log("WRN", String("OPEN blocked by boot guard for ") + lk.id);
    publishLockState(lk, "open_blocked_boot_guard");
    return;
  }

  if (lk.cooldown && (nowMs - lk.cooldownStartMs < kCooldownMs)) {
    log("WRN", String("OPEN blocked by cooldown for ") + lk.id);
    publishLockState(lk, "open_blocked_cooldown");
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
  lk.pulseStartMs = nowMs;
  lk.pulseCount++;
  applyLockOutput(lk);
  publishLockState(lk, reason);
}

void MaglockController::setFailSafe(LockState& lk, bool closed, const char* reason) {
  if (lk.mode != LockMode::FailSafe) {
    log("WRN", String("setFailSafe on non-failsafe: ") + lk.id);
    return;
  }
  lk.coilOn = closed;
  lk.pulsing = false;
  lk.cooldown = false;
  lk.bootGuard = false;
  applyLockOutput(lk);
  publishLockState(lk, reason);
}

void MaglockController::updateProtectionWindows(uint32_t nowMs) {
  for (auto& lk : locks_) {
    if (lk.mode != LockMode::FailSecure) continue;

    if (lk.bootGuard && (nowMs - bootMs_ >= kBootGuardMs)) {
      lk.bootGuard = false;
      publishLockState(lk, "boot_guard_cleared");
    }

    if (lk.cooldown && (nowMs - lk.cooldownStartMs >= kCooldownMs)) {
      lk.cooldown = false;
      publishLockState(lk, "cooldown_cleared");
    }
  }
}

void MaglockController::updatePulseTimers(uint32_t nowMs) {
  for (auto& lk : locks_) {
    if (lk.mode != LockMode::FailSecure) continue;

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
  }
}

void MaglockController::publishMetricsIfDue(uint32_t nowMs) {
  if (!ctx_) return;
  if (nowMs - lastMetricMs_ < kMetricIntervalMs) return;
  lastMetricMs_ = nowMs;

  String payload = String("{\"fw\":\"") + ctx_->fwVersion() +
                   "\",\"up\":" + String(nowMs / 1000) +
                   ",\"t\":\"MAG\"" +
                   ",\"phase\":" + String(currentPhase_) +
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
  String data = String("{\"phase\":") + String(currentPhase_) + ",\"locks\":[";
  for (size_t i = 0; i < kLockCount; i++) {
    if (i > 0) data += ",";
    data += "{\"id\":\"";
    data += locks_[i].id;
    data += "\",\"state\":\"";
    data += lockStateName(locks_[i]);
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
