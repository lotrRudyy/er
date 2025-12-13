#pragma once

#include "core_node.h"

// Placeholder for future lighting integrations to keep controller layout consistent.
class LightingController {
public:
  void begin(Core::NodeContext& ctx);
  void tick(uint32_t nowMs);
};
