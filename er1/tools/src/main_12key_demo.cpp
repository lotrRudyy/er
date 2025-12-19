#include <Arduino.h>

// This demo assumes you already have audio samples coming in as int16_t mono at 48k.
// Hook your I2S read loop to feed KeyClassifier::process_i16(...).
//
// REQUIREMENT:
//  - Run export_esp_templates.py on your PC to generate templates_generated.h
//  - Put templates_generated.h next to your firmware sources and include path

#include "esp_key_classifier.h"
#include "templates_generated.h"

// Replace with your actual I2S reader.
// This stub shows integration shape only.
static bool i2s_read_blocking(int16_t* out, size_t n) {
  // TODO: implement using your existing I2S code
  (void)out; (void)n;
  return false;
}

KeyClassifier kc;

void setup() {
  Serial.begin(115200);
  delay(200);

  // In this demo, templates assume KC_FS.
  kc.begin(KC_FS);

  Serial.println("[12-key demo] ready");
  Serial.print("labels="); Serial.println(KC_NUM_LABELS);
  Serial.print("exemplars="); Serial.println(KC_NUM_EXEMPLARS);
  Serial.print("feat_d="); Serial.println(KC_FEAT_D);

  Serial.print("thresholds: t_abs="); Serial.print(kc.t_abs, 3);
  Serial.print(" t_margin="); Serial.println(kc.t_margin, 3);
}

void loop() {
  static int16_t buf[1024];

  if (!i2s_read_blocking(buf, 1024)) {
    delay(1);
    return;
  }

  KCResult r;
  if (kc.process_i16(buf, 1024, &r)) {
    if (r.accepted && r.label_index >= 0) {
      Serial.print("key ");
      Serial.print(KC_LABELS[r.label_index]);
      Serial.print(" s1=");
      Serial.print(r.s1, 4);
      Serial.print(" s2=");
      Serial.print(r.s2, 4);
      Serial.print(" margin=");
      Serial.println(r.margin, 4);
    } else {
      Serial.print("unknown s1=");
      Serial.print(r.s1, 4);
      Serial.print(" s2=");
      Serial.print(r.s2, 4);
      Serial.print(" margin=");
      Serial.println(r.margin, 4);
    }
  }
}
