#include "piano_mapper.h"

#include <algorithm>
#include <cfloat>
#include <cmath>
#include <cstdlib>
#include <cstring>
#include <strings.h>

namespace {
constexpr uint32_t kMapperWindowMs = 60;
}

void PianoMapper::begin(Core::NodeContext& ctx, Core::Logger* logger) {
  ctx_ = &ctx;
  logger_ = logger;
  resetAccum();
  currentIdx_ = 0;
}

void PianoMapper::tick(uint32_t nowMs) {
  if (!ctx_ || !active_) return;
  if (nowMs - lastWindowMs_ < kMapperWindowMs) return;
  lastWindowMs_ = nowMs;

  if (!piano::captureWindow(piano::kAudioInputPin, samples_, piano::kSamplesPerWindow)) {
    return;
  }

  piano::Detection det = piano::analyzeWindow(samples_, piano::kSamplesPerWindow);
  collected_++;

  float hz = det.noteHz;
  accum_.sumHz += hz;
  accum_.sumHzSq += hz * hz;
  if (collected_ == 1 || hz < accum_.minHz) {
    accum_.minHz = hz;
  }
  if (hz > accum_.maxHz) {
    accum_.maxHz = hz;
  }
  accum_.sumConf += det.confidence;
  accum_.sumMag += det.magnitude;
  if (det.magnitude > accum_.maxMag) {
    accum_.maxMag = det.magnitude;
  }
  if (det.noteIndex == currentIdx_) {
    accum_.hits++;
  }

  if (collected_ >= targetWindows_) {
    emitRow(false);
  }
}

bool PianoMapper::onCmd(const char* cmd, const char* payload) {
  if (!ctx_ || !cmd) return false;
  if (strcasecmp(cmd, "MAP_START") == 0) {
    int idx = currentIdx_;
    int parsed = parseNoteIndex(payload);
    if (parsed >= 0) idx = parsed;
    setIndex(idx);
    active_ = true;
    logStatus("MAP_START");
    return true;
  }
  if (strcasecmp(cmd, "MAP_STOP") == 0) {
    active_ = false;
    logStatus("MAP_STOP");
    return true;
  }
  if (strcasecmp(cmd, "MAP_NEXT") == 0) {
    nextIndex();
    logStatus("MAP_NEXT");
    return true;
  }
  if (strcasecmp(cmd, "MAP_PREV") == 0) {
    prevIndex();
    logStatus("MAP_PREV");
    return true;
  }
  if (strcasecmp(cmd, "MAP_ROW") == 0) {
    int parsed = parseNoteIndex(payload);
    if (parsed >= 0) {
      setIndex(parsed);
    }
    emitRow(true);
    return true;
  }
  if (strcasecmp(cmd, "MAP_DUMP") == 0) {
    const auto* table = piano::noteTable();
    size_t count = piano::noteCount();
    for (size_t i = 0; i < count; ++i) {
      String data = String("{\"i\":") + i +
                    ",\"tgt\":\"" + table[i].name +
                    "\",\"hz\":" + String(table[i].meanHz, 3) +
                    ",\"min\":" + String(table[i].minHz, 3) +
                    ",\"max\":" + String(table[i].maxHz, 3) +
                    "}";
      ctx_->log("DBG", "MAP_DUMP", data);
    }
    return true;
  }
  return false;
}

void PianoMapper::resetAccum() {
  accum_.sumHz = 0.0f;
  accum_.sumHzSq = 0.0f;
  accum_.sumConf = 0.0f;
  accum_.sumMag = 0.0f;
  accum_.maxMag = 0.0f;
  accum_.hits = 0;
  accum_.minHz = FLT_MAX;
  accum_.maxHz = 0.0f;
  collected_ = 0;
}

void PianoMapper::emitRow(bool forced) {
  if (!ctx_) return;
  if (collected_ == 0) {
    if (forced) {
      ctx_->log("WRN", "MAP_ROW_EMPTY");
    }
    return;
  }
  float meanHz = accum_.sumHz / collected_;
  float variance = (accum_.sumHzSq / collected_) - (meanHz * meanHz);
  if (variance < 0.0f) variance = 0.0f;
  float sd = sqrtf(variance);
  float range = (accum_.minHz == FLT_MAX) ? 0.0f : (accum_.maxHz - accum_.minHz);
  float meanConf = accum_.sumConf / collected_;
  float meanMag = accum_.sumMag / collected_;
  float hitRate = static_cast<float>(accum_.hits) / static_cast<float>(collected_);

  const auto* table = piano::noteTable();
  size_t tableCount = piano::noteCount();
  const char* name = (currentIdx_ >= 0 && static_cast<size_t>(currentIdx_) < tableCount) ? table[currentIdx_].name : "UNK";

  String data = String("{\"i\":") + currentIdx_ +
                ",\"tgt\":\"" + name +
                "\",\"n\":" + collected_ +
                ",\"hz\":" + String(meanHz, 3) +
                ",\"sd\":" + String(sd, 3) +
                ",\"min\":" + String(accum_.minHz == FLT_MAX ? 0.0f : accum_.minHz, 3) +
                ",\"max\":" + String(accum_.maxHz, 3) +
                ",\"rng\":" + String(range, 3) +
                ",\"conf\":" + String(meanConf, 3) +
                ",\"hm\":" + String(hitRate, 3) +
                ",\"mag\":" + String(meanMag, 3) +
                ",\"magmx\":" + String(accum_.maxMag, 3) +
                (forced ? ",\"forced\":1" : "") +
                "}";
  ctx_->log("INF", "MAP_ROW", data);
  resetAccum();
}

void PianoMapper::setIndex(int idx) {
  if (idx < 0) idx = 0;
  size_t count = piano::noteCount();
  if (static_cast<size_t>(idx) >= count) {
    idx = static_cast<int>(count - 1);
  }
  currentIdx_ = idx;
  resetAccum();
}

void PianoMapper::nextIndex() {
  int idx = currentIdx_ + 1;
  if (static_cast<size_t>(idx) >= piano::noteCount()) {
    idx = 0;
  }
  setIndex(idx);
}

void PianoMapper::prevIndex() {
  int idx = currentIdx_ - 1;
  if (idx < 0) {
    idx = static_cast<int>(piano::noteCount() - 1);
  }
  setIndex(idx);
}

int PianoMapper::parseNoteIndex(const char* payload) const {
  if (!payload || payload[0] == '\0') return -1;
  bool isNumber = true;
  for (const char* p = payload; *p; ++p) {
    if (*p < '0' || *p > '9') {
      isNumber = false;
      break;
    }
  }
  if (isNumber) {
    return atoi(payload);
  }
  return piano::noteIndexByName(payload);
}

void PianoMapper::logStatus(const char* msg) const {
  if (!ctx_) return;
  const auto* table = piano::noteTable();
  size_t tableCount = piano::noteCount();
  const char* name = (currentIdx_ >= 0 && static_cast<size_t>(currentIdx_) < tableCount) ? table[currentIdx_].name : "UNK";
  String data = String("{\"idx\":") + currentIdx_ +
                ",\"note\":\"" + name +
                "\",\"n\":" + targetWindows_ +
                ",\"active\":" + (active_ ? "1" : "0") +
                "}";
  if (logger_) {
    logger_->publish("DBG", msg, data);
  } else {
    ctx_->log("DBG", msg, data);
  }
}
