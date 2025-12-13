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

void StopTimerRiddle::log(const char* level, const String& msg) {
  if (!ctx_) return;
  ctx_->log(level, msg);
}

void StopTimerRiddle::log(const char* level, const String& msg, const String& dataJson) {
  if (!ctx_) return;
  ctx_->log(level, msg, dataJson);
}
