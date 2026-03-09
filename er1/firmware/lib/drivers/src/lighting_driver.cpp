#include "lighting_driver.h"

void LightingDriver::begin(const LightingChannelConfig* channels,
                           size_t count,
                           uint32_t freqHz,
                           uint8_t resolutionBits) {
  if (!channels || count == 0) return;

  if (resolutionBits < 1) resolutionBits = 1;
  if (resolutionBits > 20) resolutionBits = 20; // ESP32 supports up to 20-bit LEDC in many configs
  maxDuty_ = (resolutionBits >= 32) ? 0xFFFFFFFFu : ((1u << resolutionBits) - 1u);

  for (size_t i = 0; i < count; i++) {
    const auto& ch = channels[i];
    ledcSetup(ch.ledcChannel, freqHz, resolutionBits);
    ledcAttachPin(ch.pin, ch.ledcChannel);
    ledcWrite(ch.ledcChannel, 0);
  }
}

void LightingDriver::writeDuty(uint8_t ledcChannel, uint32_t duty) {
  if (duty > maxDuty_) duty = maxDuty_;
  ledcWrite(ledcChannel, duty);
}
