#pragma once

#include <Arduino.h>

#include "core_node.h"

class StopTimerRiddle {
public:
  void begin(Core::NodeContext& ctx);
  void tick(uint32_t nowMs);
  bool onCmd(const char* cmd, const char* payload);

  bool shouldAllowLog(const char* level);
  bool dfReady() const { return true; }
  uint32_t errorCount() const { return errorCount_; }

private:
  void publishState(const char* status);
  void log(const char* level, const String& msg);
  void log(const char* level, const String& msg, const String& dataJson);

  Core::NodeContext* ctx_ = nullptr;
  uint32_t errorCount_ = 0;
};
