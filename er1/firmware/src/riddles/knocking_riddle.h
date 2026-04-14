// knocking_riddle.h
#pragma once

#include <Arduino.h>
#include <driver/i2s.h>

#include "core_node.h"
#include "riddles/knock_samples.h"

class KnockingRiddle {
public:
  void begin(Core::NodeContext& ctx);
  void tick(uint32_t nowMs);
  bool onCmd(const char* cmd, const char* payload);
  void setGameMode(bool inGame);

  bool dfReady() const { return audioOk_; }
  bool isSolved() const { return solved_; }

  uint32_t errorCount() const { return errorCount_; }
  bool shouldAllowLog(const char* level);

private:
  static constexpr bool kDevLog = true;
  static constexpr bool kSerialDebugDefault = true;

  static constexpr int kSensorCount = 3;
  static constexpr int kPiezoPins[kSensorCount] = {32, 33, 34};
  static constexpr uint16_t kKnockThresholds[kSensorCount] = {700, 300, 1000};

  static constexpr uint32_t kKnockDebounceMs = 200;
  static constexpr uint32_t kKnockWindowMs = 40;

  static constexpr int kSeqExpectLen = 12;
  static constexpr int kSeqExpect[kSeqExpectLen] = {0, 0, 0, 0, 0, 0, 1, 1, 2, 2, 2, 2};
  static constexpr int kSeqMaxLen = 16;
  static constexpr uint32_t kSeqTimeoutMs = 3000;

  static constexpr uint8_t kSoundQueueMax = 16;

  static constexpr int kI2S_BCLK = 16;
  static constexpr int kI2S_LRC  = 17;
  static constexpr int kI2S_DIN  = 22;
  static constexpr i2s_port_t kI2SPort = I2S_NUM_0;

  static constexpr uint8_t kAudioVolume = 21;

  static constexpr size_t kAttemptStringMax = 48;
  static constexpr size_t kEmbeddedChunkFrames = 128;

  struct PiezoState {
    int pin;
    uint32_t sum;
    uint16_t samples;
    uint16_t avg;
    uint16_t base;
    uint16_t maxVal;
    uint16_t lastRaw;
    static const int kBuckets = 10;
    uint16_t bucketMax[kBuckets];
  };

  void serialLogLine(const char* level, const String& msg, const String* dataJson = nullptr) const;
  void log(const char* level, const String& msg) const;
  void log(const char* level, const String& msg, const String& dataJson) const;
  bool publish(const char* topic, const char* type, uint32_t version, const String& dataJson,
               const char* id = nullptr, bool retained = false) const;
  bool publish(const char* topic, const String& payload, bool retained = false) const;

  void updatePiezoSamples(uint32_t nowMs);
  void handleKnockWindow(uint32_t nowMs);
  void registerKnock(int idx, uint16_t raw, uint32_t nowMs);

  void playKnockSound(int idx);
  void enqueueSound(uint8_t track, int8_t srcIdx = -1);
  bool soundQueueEmpty() const;
  bool soundQueueFull() const;
  void serviceSound(uint32_t nowMs);

  void ensureRawI2sConfigured();
  bool startEmbeddedTrack(uint8_t track, int8_t srcIdx, uint32_t nowMs);
  void serviceEmbeddedSound(uint32_t nowMs);
  void stopEmbeddedSound();

  void evaluateSequence(bool timeoutAttempt = false);
  void evaluateSequenceIfDue(uint32_t nowMs);
  void resetSequence();

  String currentSequenceHyphen() const;
  String currentSequenceJson() const;

  void publishSolvedEvent();
  void publishState();
  void resetState(const char* reason);
  void publishAudioDebug(const char* reason) const;

  Core::NodeContext* ctx_ = nullptr;

  bool audioOk_ = false;
  bool serialDebug_ = kSerialDebugDefault;

  uint8_t soundQueue_[kSoundQueueMax];
  uint8_t soundHead_ = 0;
  uint8_t soundTail_ = 0;
  bool soundPlaying_ = false;
  uint32_t lastSoundStartMs_ = 0;
  uint8_t currentTrack_ = 0;

  bool rawI2sReady_ = false;
  bool embeddedPlaying_ = false;
  uint8_t embeddedTrack_ = 0;
  const int16_t* embeddedBuf_ = nullptr;
  size_t embeddedLen_ = 0;
  size_t embeddedPos_ = 0;

  PiezoState piezo_[kSensorCount];
  bool knockWindowActive_ = false;
  uint32_t knockWindowStart_ = 0;
  uint16_t windowMax_[kSensorCount] = {0, 0, 0};
  uint32_t lastKnockMsGlobal_ = 0;

  int seqBuf_[kSeqMaxLen];
  int seqLen_ = 0;
  uint32_t lastSeqActivityMs_ = 0;

  uint32_t tries_ = 0;
  String lastAttempt_;

  mutable uint32_t errorCount_ = 0;
  bool solved_ = false;
  bool gameActive_ = false;
  bool moduleEnabled_ = true;
};
