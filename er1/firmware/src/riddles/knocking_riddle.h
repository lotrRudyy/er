// knocking_riddle.h
#pragma once

#include <Arduino.h>

// MAX98357A via I2S + files from LittleFS
#include "Audio.h"
#include "LittleFS.h"

#include "core_node.h"

class KnockingRiddle {
public:
  void begin(Core::NodeContext& ctx);
  void tick(uint32_t nowMs);
  bool onCmd(const char* cmd, const char* payload);
  void setGameMode(bool inGame);

  // Kept name for compatibility with older code paths (was DFPlayer)
  bool dfReady() const { return audioOk_; }

  uint32_t errorCount() const { return errorCount_; }
  bool shouldAllowLog(const char* level);

private:
  static constexpr bool kDevLog = true;

  static constexpr int kSensorCount = 3;
  static constexpr int kPiezoPins[kSensorCount] = {32, 33, 34};

  static constexpr uint16_t kKnockThresholds[kSensorCount] = {700, 300, 1000};

  // Global debounce / lockout (sequence acceptance). NOTE: sound is played BEFORE this check.
  static constexpr uint32_t kKnockDebounceMs = 200;

  // Window used to determine which sensor was hit (winner selection)
  static constexpr uint32_t kKnockWindowMs = 40;

  static constexpr int kSeqExpectLen = 9;
  static constexpr int kSeqExpect[kSeqExpectLen] = {0, 0, 0, 0, 1, 1, 2, 2, 2};
  static constexpr int kSeqMaxLen = 16;
  static constexpr uint32_t kSeqTimeoutMs = 3000;

  static constexpr uint8_t kSoundQueueMax = 16;

  // I2S pins (do not collide with ETH pins 15/27/18/19/23 or piezos 32/33/34)
  static constexpr int kI2S_BCLK = 16;
  static constexpr int kI2S_LRC  = 17;
  static constexpr int kI2S_DIN  = 22;

  // ESP32-audioI2S volume range: 0..21
  static constexpr uint8_t kAudioVolume = 21;

  // Tracks in LittleFS root: /1.wav .. /4.wav
  static constexpr const char* kTrackPaths[5] = {
      nullptr, "/1.wav", "/2.wav", "/3.wav", "/4.wav"};

  // Background silence to keep I2S clock + amp "awake"
  static constexpr const char* kSilencePath = "/silence.wav";

  static constexpr size_t kAttemptHistoryMax = 64;
  static constexpr size_t kAttemptStringMax = 48;

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
  unsigned long trackFallbackMs(uint8_t track) const;
  void serviceSound(uint32_t nowMs);

  void startSilenceIfIdle();
  void stopSilenceIfActive();

  void evaluateSequence(bool timeoutAttempt = false);
  void evaluateSequenceIfDue(uint32_t nowMs);
  void resetSequence();

  void appendAttemptedSequence();
  String currentSequenceHyphen() const;
  String currentSequenceJson() const;
  String attemptedSequencesJson() const;

  void publishSolvedEvent();
  void publishState();
  void resetState(const char* reason);

  void publishLittleFsListingOnce(); // MQTT debug listing

  Core::NodeContext* ctx_ = nullptr;

  // Audio
  Audio audio_;
  bool audioOk_ = false;

  // FS listing over MQTT (once after MQTT connected)
  bool fsListed_ = false;

  // Background silence state
  bool silenceAvailable_ = false;
  bool silenceActive_ = false;

  // Sound queue state
  uint8_t soundQueue_[kSoundQueueMax];
  uint8_t soundHead_ = 0;
  uint8_t soundTail_ = 0;
  bool soundPlaying_ = false;
  uint32_t lastSoundStartMs_ = 0;
  uint8_t currentTrack_ = 0;

  // Knock window state
  PiezoState piezo_[kSensorCount];
  bool knockWindowActive_ = false;
  uint32_t knockWindowStart_ = 0;
  uint16_t windowMax_[kSensorCount] = {0, 0, 0};
  uint32_t lastKnockMsGlobal_ = 0;

  // Sequence buffer
  int seqBuf_[kSeqMaxLen];
  int seqLen_ = 0;
  uint32_t lastSeqActivityMs_ = 0;

  char attemptedSequences_[kAttemptHistoryMax][kAttemptStringMax] = {{0}};
  size_t attemptedSequencesCount_ = 0;
  uint32_t tries_ = 0;

  mutable uint32_t errorCount_ = 0;
  bool solved_ = false;
  bool gameActive_ = false;
  bool moduleEnabled_ = true;
};
