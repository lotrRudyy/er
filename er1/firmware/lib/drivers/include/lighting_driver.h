#pragma once

#include <Arduino.h>

struct MosfetPwmChannelConfig {
  const char* id;
  uint8_t pin;
  uint8_t ledcChannel;
};

// Low-level driver responsible for configuring LEDC PWM and writing duty values.
// Duty is raw LEDC (0..(2^resolutionBits-1)).
class MosfetPwmDriver {
public:
  void begin(const MosfetPwmChannelConfig* channels, size_t count, uint32_t freqHz, uint8_t resolutionBits);
  void writeDuty(uint8_t ledcChannel, uint32_t duty);
  uint32_t maxDuty() const { return maxDuty_; }

private:
  uint32_t maxDuty_ = 4095;
};
