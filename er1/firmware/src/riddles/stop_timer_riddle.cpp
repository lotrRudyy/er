#include "stop_timer_riddle.h"

#include <cstring>

void StopTimerRiddle::begin(Core::NodeContext& ctx) {
  ctx_ = &ctx;
  errorCount_ = 0;

  const char* fw = ctx_->fwVersion();
  String msg = "stop_timer placeholder";
  if (fw && fw[0]) {
    msg += String(" fw=") + fw;
  }
  log("INF", msg);
  publishState("idle");
}

void StopTimerRiddle::tick(uint32_t nowMs) {
  (void)nowMs;
}

bool StopTimerRiddle::onCmd(const char* cmd, const char* payload) {
  (void)cmd;
  (void)payload;
  return false;
}

bool StopTimerRiddle::shouldAllowLog(const char* level) {
  if (level && strcmp(level, "ERR") == 0) {
    errorCount_++;
    return true;
  }
  return true;
}

bool StopTimerRiddle::publish(const char* topic, const char* type, uint32_t version, const String& dataJson,
                              const char* id, bool retained) const {
  if (!ctx_) return false;
  return ctx_->publishEnvelope(topic, type, version, dataJson, id, retained);
}

bool StopTimerRiddle::publish(const char* topic, const String& payload, bool retained) const {
  if (!ctx_) return false;
  return ctx_->publish(topic, payload, retained);
}

void StopTimerRiddle::publishState(const char* status) {
  if (!ctx_) return;
  const char* st = (status && status[0]) ? status : "idle";
  String data = String("{\"status\":\"") + st + "\"}";
  const auto& topics = ctx_->config().topics;
  if (topics.state.length() > 0) {
    publish(topics.state.c_str(), "state", 1, data, nullptr, true);
  }
}

void StopTimerRiddle::log(const char* level, const String& msg) const {
  if (!ctx_) return;
  ctx_->log(level, msg);
}

void StopTimerRiddle::log(const char* level, const String& msg, const String& dataJson) const {
  if (!ctx_) return;
  ctx_->log(level, msg, dataJson);
}
