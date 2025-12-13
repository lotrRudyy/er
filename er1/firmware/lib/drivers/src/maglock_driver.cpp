#include "maglock_driver.h"

void MaglockDriver::begin(const MaglockChannelConfig* channels, size_t count) {
  if (!channels) return;
  for (size_t i = 0; i < count; i++) {
    pinMode(channels[i].pin, OUTPUT);
    digitalWrite(channels[i].pin, LOW);
  }
}

void MaglockDriver::setCoil(uint8_t pin, bool on) {
  digitalWrite(pin, on ? HIGH : LOW);
}
