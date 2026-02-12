#include <Arduino.h>
#include "Audio.h"
#include "LittleFS.h"

// I2S pins
static const int PIN_BCLK = 26;
static const int PIN_LRC  = 25;
static const int PIN_DIN  = 22;

// Trigger pins
static const int BTN1 = 32;
static const int BTN2 = 33;
static const int BTN3 = 27;
static const int BTN4 = 14;

Audio audio;

bool lastState1 = HIGH;
bool lastState2 = HIGH;
bool lastState3 = HIGH;
bool lastState4 = HIGH;

void setup() {
  Serial.begin(115200);

  if (!LittleFS.begin(false)) {
    Serial.println("LittleFS mount failed");
    while (true);
  }

  audio.setPinout(PIN_BCLK, PIN_LRC, PIN_DIN);
  audio.setVolume(21);

  pinMode(BTN1, INPUT_PULLUP);
  pinMode(BTN2, INPUT_PULLUP);
  pinMode(BTN3, INPUT_PULLUP);
  pinMode(BTN4, INPUT_PULLUP);

  Serial.println("Ready.");
}

void playFile(const char* path) {
  Serial.printf("Playing %s\n", path);
  audio.connecttoFS(LittleFS, path);
}

void loop() {
  audio.loop();

  bool s1 = digitalRead(BTN1);
  bool s2 = digitalRead(BTN2);
  bool s3 = digitalRead(BTN3);
  bool s4 = digitalRead(BTN4);

  if (s1 == LOW && lastState1 == HIGH) playFile("/1.wav");
  if (s2 == LOW && lastState2 == HIGH) playFile("/2.wav");
  if (s3 == LOW && lastState3 == HIGH) playFile("/3.wav");
  if (s4 == LOW && lastState4 == HIGH) playFile("/4.wav");

  lastState1 = s1;
  lastState2 = s2;
  lastState3 = s3;
  lastState4 = s4;

  delay(10); // small debounce
}
