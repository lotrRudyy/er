// Room2 — Chess — ER1
// Room: room2, Device: chess01
// Pattern: FSM (non-blocking), Ethernet W5500 (+HTTP OTA), MQTT topics per standards.
// Notes: No 'riddle' in name per your preference.
#include <Arduino.h>

enum State { IDLE, ACTIVE, SOLVED, ERROR_STATE };
static State state = IDLE;
static unsigned long lastHb = 0;

void setup() {
  Serial.begin(115200);
  delay(50);
  Serial.println(F("[boot] Room2 — Chess starting..."));
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
    // TODO: publish esc/room2/chess01/hb
  }
  delay(1);
}
