#pragma once

#include <Arduino.h>

#include "core_node.h"
#include "piano_riddle_fsm.h"

class PianoMapper {
public:
  void begin(Core::NodeContext& ctx);
  void tick(uint32_t nowMs);
  bool onCmd(const char* cmd, const char* payload);

private:
  struct Accum {
    float sumHz = 0.0f;
    float sumHzSq = 0.0f;
    float minHz = 0.0f;
    float maxHz = 0.0f;
    float sumConf = 0.0f;
    float sumMag = 0.0f;
    float maxMag = 0.0f;
    uint16_t hits = 0;
  };

  void resetAccum();
  void emitRow(bool forced);
  void setIndex(int idx);
  void nextIndex();
  void prevIndex();
  int parseNoteIndex(const char* payload) const;
  void logStatus(const char* msg) const;

  Core::NodeContext* ctx_ = nullptr;
  bool active_ = false;
  uint32_t lastWindowMs_ = 0;
  uint16_t targetWindows_ = 64;
  uint16_t collected_ = 0;
  int currentIdx_ = 0;
  Accum accum_;
  int16_t samples_[piano::kSamplesPerWindow];
};
