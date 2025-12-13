#include "piano_riddle.h"

void PianoRiddle::begin(Core::NodeContext& ctx) {
  ctx_ = &ctx;
  (void)ctx_;
}

void PianoRiddle::tick(uint32_t /*nowMs*/) {
  // Placeholder module; future piano logic lives here.
}

bool PianoRiddle::onCmd(const char* /*cmd*/, const char* /*payload*/) {
  return false;
}

