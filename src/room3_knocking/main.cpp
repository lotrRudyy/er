// Room3 — Knocking — ER1
// Room: room3, Device: knocking01
// Pattern: FSM (non-blocking), Ethernet W5500 (+HTTP OTA), MQTT topics per standards.
// Notes: Piezos on ADC1; publish SOLVED pattern.
#include <Arduino.h>

enum State { IDLE, ACTIVE, SOLVED, ERROR_STATE };
static State state = IDLE;
static unsigned long lastHb = 0;

void setup() {
  Serial.begin(115200);
  delay(50);
  Serial.println(F("[boot] Room3 — Knocking starting..."));
  // TODO: init SPI + Ethernet(W5500), HTTP OTA (/update, auth), MQTT client
}

void loop() {
  // TODO: service Ethernet/MQTT/OTA

  switch (state) {
    case IDLE:   /* TODO */ break;
    case ACTIVE: /* TODO */ break;
    case SOLVED: /* TODO */ break;
    case ERROR_STATE: /* TODO */ break;
  }

  if (millis() - lastHb > HB_MS) {
    lastHb = millis();
    Serial.println(F("[hb] alive"));
    // TODO: publish esc/room3/knocking01/hb
  }
  delay(1);
}
