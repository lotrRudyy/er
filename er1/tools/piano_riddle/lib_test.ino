#include <AudioInI2S.h>

#define SAMPLE_SIZE 1024
#define SAMPLE_RATE 44100

#define MIC_BCK_PIN 26
#define MIC_WS_PIN 25
#define MIC_DATA_PIN 33

AudioInI2S mic(MIC_BCK_PIN, MIC_WS_PIN, MIC_DATA_PIN);

int32_t samples[SAMPLE_SIZE];

void setup() {
  Serial.begin(115200);
  mic.begin(SAMPLE_SIZE, SAMPLE_RATE);
}

void loop() {
  mic.read(samples);

  // print a few samples only (plotter will still work, but faster)
  for (int i = 0; i < 256; i++) {
    Serial.println(samples[i]);
  }
}
