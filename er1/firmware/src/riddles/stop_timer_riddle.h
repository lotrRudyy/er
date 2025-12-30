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
  bool publish(const char* topic, const char* type, uint32_t version, const String& dataJson,
               const char* id = nullptr, bool retained = false) const;
  bool publish(const char* topic, const String& payload, bool retained = false) const;
  void publishState(const char* status);
  void log(const char* level, const String& msg) const;
  void log(const char* level, const String& msg, const String& dataJson) const;

  Core::NodeContext* ctx_ = nullptr;
  uint32_t errorCount_ = 0;
};
