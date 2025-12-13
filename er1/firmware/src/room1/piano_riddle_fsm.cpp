#include "room1/piano_riddle_fsm.h"

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <strings.h>

namespace {
constexpr uint32_t kWindowIntervalMs = 45;
constexpr uint32_t kNoteDebounceMs = 140;
constexpr float kMinConfidence = 0.3f;
constexpr float kMinMagnitude = 1200.0f;
constexpr size_t kTotalNotes = 88;
constexpr const char* kTopicEvent = "er1/room1/images_piano/event";
constexpr const char* kTopicLockR2Cmd = "er1/ctrl/lock/r2/cmd";

const char* kSequenceNames[] = {
    "C4", "D4", "E4", "F4", "G4", "F4", "E4", "D4", "C4",
};

struct NoteTableState {
  piano::NoteDef entries[kTotalNotes];
  bool initialized = false;
};

NoteTableState& tableState() {
  static NoteTableState state;
  return state;
}

struct SequenceState {
  int idx[sizeof(kSequenceNames) / sizeof(kSequenceNames[0])];
  bool initialized = false;
};

SequenceState& sequenceState() {
  static SequenceState st;
  return st;
}

constexpr const char* kNoteNames[] = {
    "A", "A#", "B", "C", "C#", "D", "D#", "E", "F", "F#", "G", "G#",
};

constexpr float kBaseFreq = 27.5f;  // A0
constexpr float kPi = 3.14159265358979323846f;

float goertzelMagnitude(const int16_t* samples, size_t count, float freq) {
  if (!samples || freq <= 0.0f) return 0.0f;
  const float omega = 2.0f * kPi * freq / static_cast<float>(piano::kSampleRate);
  const float coeff = 2.0f * cosf(omega);
  float q0 = 0.0f;
  float q1 = 0.0f;
  float q2 = 0.0f;
  for (size_t i = 0; i < count; ++i) {
    q0 = coeff * q1 - q2 + static_cast<float>(samples[i]);
    q2 = q1;
    q1 = q0;
  }
  float magnitude = q1 * q1 + q2 * q2 - q1 * q2 * coeff;
  if (magnitude < 0.0f) magnitude = 0.0f;
  return sqrtf(magnitude);
}

void ensureNoteTable() {
  NoteTableState& state = tableState();
  if (state.initialized) return;
  for (size_t i = 0; i < kTotalNotes; ++i) {
    size_t nameIdx = i % (sizeof(kNoteNames) / sizeof(kNoteNames[0]));
    int octave = static_cast<int>((i + 9) / 12);
    std::snprintf(state.entries[i].name, sizeof(state.entries[i].name), "%s%d", kNoteNames[nameIdx], octave);
    float freq = kBaseFreq * powf(2.0f, static_cast<float>(i) / 12.0f);
    state.entries[i].nominalHz = freq;
    state.entries[i].meanHz = freq;
    state.entries[i].minHz = freq * 0.985f;
    state.entries[i].maxHz = freq * 1.015f;
    state.entries[i].confFloor = 0.25f;
  }
  state.initialized = true;
}

void ensureSequence() {
  SequenceState& st = sequenceState();
  if (st.initialized) return;
  ensureNoteTable();
  constexpr size_t seqLen = sizeof(kSequenceNames) / sizeof(kSequenceNames[0]);
  std::fill_n(st.idx, seqLen, -1);
  for (size_t i = 0; i < (sizeof(kSequenceNames) / sizeof(kSequenceNames[0])); ++i) {
    st.idx[i] = piano::noteIndexByName(kSequenceNames[i]);
  }
  st.initialized = true;
}

const char* safeName(int idx) {
  const auto* tbl = piano::noteTable();
  if (!tbl) return "UNK";
  size_t count = piano::noteCount();
  if (idx < 0 || static_cast<size_t>(idx) >= count) return "UNK";
  return tbl[idx].name;
}

}  // namespace

namespace piano {

const NoteDef* noteTable() {
  ensureNoteTable();
  return tableState().entries;
}

size_t noteCount() {
  return kTotalNotes;
}

const NoteDef* noteByName(const char* name) {
  if (!name) return nullptr;
  ensureNoteTable();
  const NoteDef* table = tableState().entries;
  for (size_t i = 0; i < kTotalNotes; ++i) {
    if (strcasecmp(table[i].name, name) == 0) {
      return &table[i];
    }
  }
  return nullptr;
}

int noteIndexByName(const char* name) {
  if (!name) return -1;
  ensureNoteTable();
  const NoteDef* table = tableState().entries;
  for (size_t i = 0; i < kTotalNotes; ++i) {
    if (strcasecmp(table[i].name, name) == 0) {
      return static_cast<int>(i);
    }
  }
  return -1;
}

const int* defaultSequence(size_t* len) {
  ensureSequence();
  if (len) {
    *len = sizeof(kSequenceNames) / sizeof(kSequenceNames[0]);
  }
  return sequenceState().idx;
}

bool captureWindow(int audioPin, int16_t* dest, size_t sampleCount) {
  if (!dest || audioPin < 0) return false;
  constexpr int32_t kCenter = 2048;
  for (size_t i = 0; i < sampleCount; ++i) {
    int32_t raw = analogRead(audioPin);
    dest[i] = static_cast<int16_t>(raw - kCenter);
    delayMicroseconds(kSampleIntervalUs);
  }
  return true;
}

Detection analyzeWindow(const int16_t* samples, size_t sampleCount) {
  Detection det{};
  det.noteIndex = -1;
  det.noteHz = 0.0f;
  det.magnitude = 0.0f;
  det.maxMagnitude = 0.0f;
  det.confidence = 0.0f;
  det.hit = false;

  ensureNoteTable();
  const NoteDef* table = tableState().entries;

  float bestMag = 0.0f;
  float secondMag = 0.0f;
  int bestIdx = -1;
  for (size_t i = 0; i < kTotalNotes; ++i) {
    float freq = table[i].meanHz > 0.0f ? table[i].meanHz : table[i].nominalHz;
    float mag = goertzelMagnitude(samples, sampleCount, freq);
    if (mag > bestMag) {
      secondMag = bestMag;
      bestMag = mag;
      bestIdx = static_cast<int>(i);
    } else if (mag > secondMag) {
      secondMag = mag;
    }
  }

  if (bestIdx >= 0) {
    det.noteIndex = bestIdx;
    det.noteHz = table[bestIdx].meanHz;
    det.magnitude = bestMag;
    det.maxMagnitude = bestMag;
    det.hit = true;
    if (bestMag > 0.0f) {
      float conf = (bestMag - secondMag) / bestMag;
      if (conf < 0.0f) conf = 0.0f;
      if (conf > 1.0f) conf = 1.0f;
      det.confidence = conf;
    }
  }

  return det;
}

}  // namespace piano

void PianoRiddleFSM::begin(Core::NodeContext& ctx) {
  ctx_ = &ctx;
  prefs_ = &ctx_->prefs();
  sequence_ = piano::defaultSequence(&sequenceLen_);
  resetBuffer();
  analogReadResolution(12);

  if (prefs_) {
    bool hasKey = prefs_->isKey(kPrefsSolvedKey);
    solved_ = prefs_->getBool(kPrefsSolvedKey, false);
    solvedPublished_ = solved_;
    if (hasKey) {
      ctx_->log("INF", String("STATE restore piano_solved=") + (solved_ ? "1" : "0"));
    } else {
      ctx_->log("INF", "STATE default piano_solved=0");
      prefs_->putBool(kPrefsSolvedKey, solved_);
    }
  } else {
    ctx_->log("INF", "STATE default piano_solved=0 (no prefs)");
  }

  if (solved_) {
    ctx_->log("INF", "PIANO_BOOT_SOLVED");
  } else {
    ctx_->log("INF", "PIANO_READY");
  }
}

void PianoRiddleFSM::tick(uint32_t nowMs) {
  if (!ctx_ || !sequence_ || sequenceLen_ == 0) return;
  if (!ctx_->enabled() || !moduleEnabled_) return;

  if (solved_) {
    if (!solvedPublished_) {
      handleSolved();
    }
    return;
  }

  if (nowMs - lastWindowMs_ < kWindowIntervalMs) return;
  lastWindowMs_ = nowMs;

  if (!piano::captureWindow(piano::kAudioInputPin, sampleBuf_, piano::kSamplesPerWindow)) {
    return;
  }

  piano::Detection det = piano::analyzeWindow(sampleBuf_, piano::kSamplesPerWindow);
  if (!det.hit) return;

  const auto* table = piano::noteTable();
  size_t tableCount = piano::noteCount();
  if (det.noteIndex < 0 || static_cast<size_t>(det.noteIndex) >= tableCount) return;
  const auto& note = table[det.noteIndex];

  float confFloor = std::max(note.confFloor, kMinConfidence);
  if (det.confidence < confFloor) return;
  if (det.magnitude < kMinMagnitude) return;
  if (det.noteHz < note.minHz || det.noteHz > note.maxHz) return;

  if (det.noteIndex == lastAcceptedNote_) {
    uint32_t dt = nowMs - lastAcceptedMs_;
    if (dt < kNoteDebounceMs) return;
  }

  lastAcceptedNote_ = det.noteIndex;
  lastAcceptedMs_ = nowMs;

  pushNote(det.noteIndex);
  logNote(det);
  bool hit = checkSequenceSolved();
  logBuffer(hit);
  if (hit) {
    solved_ = true;
    if (prefs_) {
      prefs_->putBool(kPrefsSolvedKey, true);
      ctx_->log("INF", "STATE save piano_solved=1");
    }
    handleSolved();
  }
}

bool PianoRiddleFSM::onCmd(const char* cmd, const char* payload) {
  if (!cmd) return false;
  if (equalsCmd(cmd, "PIANO_ENABLE")) {
    setModuleEnabled(true);
    return true;
  }
  if (equalsCmd(cmd, "PIANO_DISABLE")) {
    setModuleEnabled(false);
    return true;
  }
  if (equalsCmd(cmd, "PIANO_RESET")) {
    clearSolvedState();
    ctx_->log("INF", "PIANO_RESET");
    return true;
  }
  if (equalsCmd(cmd, "PIANO_SET_SEQ")) {
    ctx_->log("WRN", "PIANO_SET_SEQ_TODO");
    return true;
  }
  return false;
}

void PianoRiddleFSM::resetBuffer() {
  bufferLen_ = 0;
  bufferHead_ = 0;
  std::fill(std::begin(noteBuffer_), std::end(noteBuffer_), -1);
}

void PianoRiddleFSM::pushNote(int noteIdx) {
  noteBuffer_[bufferHead_] = static_cast<int8_t>(noteIdx);
  bufferHead_ = (bufferHead_ + 1) % kBufferSize;
  if (bufferLen_ < kBufferSize) {
    bufferLen_++;
  }
}

bool PianoRiddleFSM::checkSequenceSolved() const {
  if (!sequence_ || sequenceLen_ == 0) return false;
  if (bufferLen_ < sequenceLen_) return false;
  for (size_t start = 0; start <= bufferLen_ - sequenceLen_; ++start) {
    bool match = true;
    for (size_t i = 0; i < sequenceLen_; ++i) {
      size_t idx = (bufferHead_ + kBufferSize - bufferLen_ + start + i) % kBufferSize;
      if (noteBuffer_[idx] != sequence_[i]) {
        match = false;
        break;
      }
    }
    if (match) return true;
  }
  return false;
}

void PianoRiddleFSM::logNote(const piano::Detection& det) {
  if (!ctx_) return;
  String data = String("{\"no\":\"") + safeName(det.noteIndex) +
                "\",\"hz\":" + String(det.noteHz, 2) +
                ",\"cf\":" + String(det.confidence, 3) +
                ",\"mag\":" + String(det.magnitude, 1) +
                ",\"i\":" + String(bufferLen_) +
                "}";
  ctx_->log("INF", "PIANO_NOTE", data);
}

void PianoRiddleFSM::logBuffer(bool hit) const {
  if (!ctx_) return;
  if (bufferLen_ == 0) return;
  const auto* table = piano::noteTable();
  size_t tableCount = piano::noteCount();
  String bufStr;
  for (size_t i = 0; i < bufferLen_; ++i) {
    size_t idx = (bufferHead_ + kBufferSize - bufferLen_ + i) % kBufferSize;
    int noteIdx = noteBuffer_[idx];
    if (i > 0) bufStr += ",";
    if (noteIdx >= 0 && static_cast<size_t>(noteIdx) < tableCount) {
      bufStr += table[noteIdx].name;
    } else {
      bufStr += "UNK";
    }
  }
  String data = String("{\"buf\":\"") + bufStr + "\",\"hit\":" + (hit ? "1" : "0") + "}";
  ctx_->log("DBG", "PIANO_BUF", data);
}

void PianoRiddleFSM::handleSolved() {
  if (solvedPublished_) return;
  solvedPublished_ = true;
  ctx_->log("INF", "PIANO_SOLVED");
  publishSolvedEvent();
  openLock();
}

void PianoRiddleFSM::publishSolvedEvent() {
  if (!ctx_) return;
  String payload = String("{\"type\":\"SOLVED\",\"rid\":\"piano\",\"seq\":\"");
  if (sequence_ && sequenceLen_ > 0) {
    for (size_t i = 0; i < sequenceLen_; ++i) {
      if (i > 0) payload += ",";
      payload += safeName(sequence_[i]);
    }
  }
  payload += "\"}";
  ctx_->publish(kTopicEvent, payload);
}

void PianoRiddleFSM::openLock() const {
  if (!ctx_) return;
  ctx_->publish(kTopicLockR2Cmd, "OPEN");
}

bool PianoRiddleFSM::equalsCmd(const char* cmd, const char* ref) const {
  if (!cmd || !ref) return false;
  return strcasecmp(cmd, ref) == 0;
}

void PianoRiddleFSM::setModuleEnabled(bool en) {
  moduleEnabled_ = en;
  ctx_->log("INF", en ? "PIANO_ENABLE" : "PIANO_DISABLE");
}

void PianoRiddleFSM::clearSolvedState() {
  solved_ = false;
  solvedPublished_ = false;
  resetBuffer();
  lastAcceptedNote_ = -1;
  lastAcceptedMs_ = 0;
  if (prefs_) {
    prefs_->putBool(kPrefsSolvedKey, false);
    ctx_->log("INF", "STATE save piano_solved=0");
  }
}
