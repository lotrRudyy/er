// Room1 — Image+Piano — ER1
// Room: room1, Device: buttons-piano01
// Pattern: FSM (non-blocking), Ethernet W5500 (+HTTP OTA), MQTT topics per standards.
// Notes: Two FSMs: image + piano; publish SOLVED events separately.
#include <Arduino.h>

enum State { IDLE, ACTIVE, SOLVED, ERROR_STATE };
static State state = IDLE;
static unsigned long lastHb = 0;

void setup() {
  Serial.begin(115200);
  delay(50);
  Serial.println(F("[boot] Room1 — Image+Piano starting..."));
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
    // TODO: publish esc/room1/buttons-piano01/hb
  }
  delay(1);
}
