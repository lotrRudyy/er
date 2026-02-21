#include "lighting_controller.h"

void MosfetPwmDriver::begin(const MosfetPwmChannelConfig* channels, size_t count, uint32_t freqHz,
                            uint8_t resolutionBits) {
  if (!channels) return;
  if (resolutionBits == 0) resolutionBits = 8;
  if (resolutionBits > 16) resolutionBits = 16;

  maxDuty_ = (1UL << resolutionBits) - 1UL;

  for (size_t i = 0; i < count; i++) {
    const auto& ch = channels[i];
    // Configure LEDC channel for PWM
    ledcSetup(ch.ledcChannel, freqHz, resolutionBits);
    ledcAttachPin(ch.pin, ch.ledcChannel);
    ledcWrite(ch.ledcChannel, 0);
  }
}

void MosfetPwmDriver::writeDuty(uint8_t ledcChannel, uint32_t duty) {
  if (duty > maxDuty_) duty = maxDuty_;
  ledcWrite(ledcChannel, duty);
}
