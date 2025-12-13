#include "maglock_controller.h"

namespace {

constexpr const char* kTopicMetric = "er1/room0/maglock_ctrl/metric";
constexpr const char* kLockCmdPrefix = "er1/ctrl/lock/";
constexpr const char* kLockStatePrefix = "er1/ctrl/lock/";
constexpr const char* kLockCmdSuffix = "/cmd";

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
  for (auto& lk : locks_) {
    pinMode(lk.pin, OUTPUT);
    lk.coilOn = false;
    lk.pulsing = false;
    lk.cooldown = false;
    lk.pulseStartMs = 0;
    lk.cooldownStartMs = 0;
    lk.pulseCount = 0;
    applyLockOutput(lk);
  }

  lastMetricMs_ = millis();
  applyHeartbeatInterval();
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

  if (old != gameMode_) {
    auto modeName = [](GameMode m) -> const char* {
      switch (m) {
        case GameMode::InGame: return "INGAME";
        case GameMode::Maint: return "MAINT";
        case GameMode::Off:
        default: return "OFF";
      }
    };
    String data = String("{\"from\":\"") + modeName(old) +
                  "\",\"to\":\"" + modeName(gameMode_) + "\"}";
    log("INF", "gameMode changed", data);
    applyHeartbeatInterval();
  }
}

void MaglockController::onKnockingEvent(const String& msg) {
  if (msg.indexOf("SOLVED") < 0) return;
  LockState* lk = findLockById("knocking");
  if (!lk) {
    log("ERR", "Knocking SOLVED event but lock 'knocking' not found");
    return;
  }
  log("INF", "Knocking SOLVED -> OPEN knocking lock");
  handleLockCommand(*lk, "OPEN");
}

void MaglockController::onLockCommandTopic(const char* topic, const String& payload) {
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
  digitalWrite(lk.pin, lk.coilOn ? HIGH : LOW);
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
  payload += "}";
  ctx_->publish(topic.c_str(), payload);
}

MaglockController::LockState* MaglockController::findLockById(const String& id) {
  for (auto& lk : locks_) {
    if (id.equalsIgnoreCase(lk.id)) {
      return &lk;
    }
  }
  return nullptr;
}

void MaglockController::startPulse(LockState& lk, const char* reason) {
  if (lk.mode != LockMode::FailSecure) {
    log("WRN", String("OPEN on non-failsecure via pulse: ") + lk.id);
    return;
  }
  if (lk.pulsing || lk.cooldown) {
    log("WRN", String("OPEN ignored (pulse/cooldown active) for ") + lk.id);
    return;
  }
  lk.coilOn = true;
  lk.pulsing = true;
  lk.cooldown = false;
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
  applyLockOutput(lk);
  publishLockState(lk, reason);
}

void MaglockController::updatePulseTimers(uint32_t nowMs) {
  for (auto& lk : locks_) {
    if (lk.mode != LockMode::FailSecure) continue;

    if (lk.pulsing && (nowMs - lk.pulseStartMs >= kPulseMs)) {
      lk.pulsing = false;
      lk.coilOn = false;
      applyLockOutput(lk);
      publishLockState(lk, "pulse_done");
      lk.cooldown = true;
      lk.cooldownStartMs = nowMs;
    }
    if (lk.cooldown && (nowMs - lk.cooldownStartMs >= kCooldownMs)) {
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
                   ",\"k\":\"maglock_ctrl\"" +
                   ",\"mode\":\"" + String(modeName(gameMode_)) + "\"" +
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
    payload += "}";
  }
  payload += "]}";

  ctx_->publish(kTopicMetric, payload);
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
      applyLockOutput(lk);
      publishLockState(lk, "cmd:CLOSE");
    } else {
      setFailSafe(lk, true, "cmd:CLOSE");
    }
    return;
  }

  log("WRN", String("Unknown lock cmd for ") + lk.id + ": " + cmd);
}

void MaglockController::log(const char* level, const String& msg) {
  if (!ctx_) return;
  ctx_->log(level, msg);
}

void MaglockController::log(const char* level, const String& msg, const String& dataJson) {
  if (!ctx_) return;
  ctx_->log(level, msg, dataJson);
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
  switch (mode) {
    case GameMode::InGame: return 5000;
    case GameMode::Maint: return 10000;
    case GameMode::Off:
    default: return 15000;
  }
}

void MaglockController::applyHeartbeatInterval() {
  if (!ctx_) return;
  ctx_->setHeartbeatInterval(hbIntervalForMode(gameMode_));
}
