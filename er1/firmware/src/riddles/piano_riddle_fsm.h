#pragma once

#include <Arduino.h>

#include "core_node.h"

namespace piano {

constexpr int kAudioInputPin = 34;
constexpr size_t kSamplesPerWindow = 128;
constexpr uint32_t kSampleRate = 4000;
constexpr uint32_t kSampleIntervalUs = 1000000UL / kSampleRate;

struct NoteDef {
  char name[5];
  float nominalHz;
  float meanHz;
  float minHz;
  float maxHz;
  float confFloor;
};

struct Detection {
  int noteIndex;
  float noteHz;
  float magnitude;
  float maxMagnitude;
  float confidence;
  bool hit;
};

const NoteDef* noteTable();
size_t noteCount();
const NoteDef* noteByName(const char* name);
int noteIndexByName(const char* name);
const int* defaultSequence(size_t* len);

bool captureWindow(int audioPin, int16_t* dest, size_t sampleCount);
Detection analyzeWindow(const int16_t* samples, size_t sampleCount);

}  // namespace piano

class PianoRiddleFSM {
public:
  void begin(Core::NodeContext& ctx);
  void tick(uint32_t nowMs);
  bool onCmd(const char* cmd, const char* payload);

private:
  void resetBuffer();
  void pushNote(int noteIdx);
  bool checkSequenceSolved() const;
  void logNote(const piano::Detection& det);
  void logBuffer(bool hit) const;
  void handleSolved();
  void publishSolvedEvent();
  void openLock() const;
  bool equalsCmd(const char* cmd, const char* ref) const;
  void setModuleEnabled(bool en);
  void clearSolvedState();

  Core::NodeContext* ctx_ = nullptr;
  Preferences* prefs_ = nullptr;
  bool moduleEnabled_ = true;
  bool solved_ = false;
  bool solvedPublished_ = false;

  static constexpr const char* kPrefsSolvedKey = "piano_solved";
  static constexpr size_t kBufferSize = 64;
  int8_t noteBuffer_[kBufferSize];
  size_t bufferLen_ = 0;
  size_t bufferHead_ = 0;
  uint32_t lastWindowMs_ = 0;
  uint32_t lastAcceptedMs_ = 0;
  int lastAcceptedNote_ = -1;
  int16_t sampleBuf_[piano::kSamplesPerWindow];

  size_t sequenceLen_ = 0;
  const int* sequence_ = nullptr;
};

