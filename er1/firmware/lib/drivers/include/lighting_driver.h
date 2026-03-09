#pragma once

#include <Arduino.h>

// Per-channel PWM configuration for MOSFET-driven LED strips.
struct LightingChannelConfig {
  const char* id;      // "1".."9" (or any string id)
  uint8_t pin;         // GPIO pin
  uint8_t ledcChannel; // ESP32 LEDC channel index
};

// Low-level PWM driver. No MQTT, no parsing — just LEDC setup + duty writes.
class LightingDriver {
public:
  void begin(const LightingChannelConfig* channels,
             size_t count,
             uint32_t freqHz,
             uint8_t resolutionBits);

  void writeDuty(uint8_t ledcChannel, uint32_t duty);

  uint32_t maxDuty() const { return maxDuty_; }

private:
  uint32_t maxDuty_ = 255;
};
