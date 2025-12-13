#pragma once

#include <Arduino.h>

struct MaglockChannelConfig {
  const char* id;
  uint8_t pin;
};

// Low-level driver responsible for configuring pins and toggling coils.
class MaglockDriver {
public:
  void begin(const MaglockChannelConfig* channels, size_t count);
  void setCoil(uint8_t pin, bool on);
};
