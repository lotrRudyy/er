#pragma once

#include <Arduino.h>

#include "core_node.h"

class PianoRiddle {
public:
  void begin(Core::NodeContext& ctx);
  void tick(uint32_t nowMs);
  bool onCmd(const char* cmd, const char* payload);

private:
  Core::NodeContext* ctx_ = nullptr;
};

