// piano_riddle.cpp
#include "piano_riddle.h"

#include <cstring>
#include <strings.h>

namespace {
PianoRiddle* gPianoRiddle = nullptr;

String withSrc(const String& dataJson, const String& src) {
  const char* srcVal = (src.length() > 0) ? src.c_str() : "";
  if (dataJson.length() == 0) {
    return String("{\"src\":\"") + srcVal + "\"}";
  }
  bool looksObject = dataJson.startsWith("{") && dataJson.endsWith("}");
  if (looksObject) {
    String out = dataJson;
    out.remove(out.length() - 1);
    if (out.length() > 1) out += ",";
    out += "\"src\":\"";
    out += srcVal;
    out += "\"}";
    return out;
  }
  return String("{\"src\":\"") + srcVal + "\",\"msg\":\"" + dataJson + "\"}";
}
}  // namespace

constexpr const char* const PianoRiddle::kSequence[PianoRiddle::kSequenceLen];

extern "C" void piano_detector_on_result(int accepted, const char* pred, float s1, float s2,
                                         float margin, float hps_ratio, int harmonic_ok,
                                         const char* t1, float t1s, const char* t2,
                                         float t2s, const char* t3, float t3s) {
  (void)hps_ratio;
  (void)harmonic_ok;
  if (gPianoRiddle) {
    gPianoRiddle->handleDetectorResult(accepted, pred, s1, s2, margin, hps_ratio, harmonic_ok,
                                       t1, t1s, t2, t2s, t3, t3s);
  }
}

void PianoRiddle::begin(Core::NodeContext& ctx, const char* srcId) {
  ctx_ = &ctx;
  prefs_ = &ctx.prefs();

  srcId_ = (srcId && srcId[0]) ? srcId : "piano";

  solved_ = false;
  solvedPublished_ = false;

  gPianoRiddle = this;
  if (!detectorStarted_) {
    piano_detector_setup();
    detectorStarted_ = true;
  }

  if (prefs_) prefs_->putBool(kPrefsSolvedKey, false);
  resetProgress("boot");
  gameActive_ = false;
  moduleEnabled_ = true;
}

void PianoRiddle::setGameMode(bool inGame) {
  gameActive_ = inGame;
  solved_ = false;
  solvedPublished_ = false;
  if (prefs_) prefs_->putBool(kPrefsSolvedKey, false);
  resetProgress(inGame ? "game_start" : "game_off");
}

void PianoRiddle::tick(uint32_t nowMs) {
  if (detectorStarted_ && gameActive_ && moduleEnabled_ && ctx_ && ctx_->enabled()) {
    piano_detector_loop_once();
  }
  (void)nowMs;
}

bool PianoRiddle::onCmd(const char* cmd, const char* /*payload*/) {
  if (!cmd) return false;

  if (equalsCmd(cmd, "PIANO_ENABLE") || equalsCmd(cmd, "ENABLE")) {
    setModuleEnabled(true);
    return true;
  }
  if (equalsCmd(cmd, "PIANO_DISABLE") || equalsCmd(cmd, "DISABLE")) {
    setModuleEnabled(false);
    return true;
  }
  if (equalsCmd(cmd, "PIANO_RESET") || equalsCmd(cmd, "RESET_PIANO") || equalsCmd(cmd, "RESET")) {
    solved_ = false;
    solvedPublished_ = false;
    if (prefs_) prefs_->putBool(kPrefsSolvedKey, false);
    resetProgress("cmd");
    return true;
  }
  if (equalsCmd(cmd, "SOLVE") || equalsCmd(cmd, "SOLVE_PIANO")) {
    if (!solved_) {
      solved_ = true;
      if (prefs_) prefs_->putBool(kPrefsSolvedKey, true);
    }
    if (!solvedPublished_) {
      publishSolvedEvent();
      solvedPublished_ = true;
    }
    return true;
  }
  if (equalsCmd(cmd, "STATUS")) {
    return true;
  }
  if (equalsCmd(cmd, "PIANO_SET_SEQ") || equalsCmd(cmd, "SET_SEQ")) {
    log("WRN", "PIANO_SET_SEQ_UNSUPPORTED");
    return true;
  }
  return false;
}

void PianoRiddle::handleDetectorResult(int accepted, const char* pred, float s1, float s2,
                                       float margin, float hps_ratio, int harmonic_ok,
                                       const char* t1, float t1s, const char* t2,
                                       float t2s, const char* t3, float t3s) {
  (void)hps_ratio;
  (void)harmonic_ok;

  if (!ctx_ || !gameActive_ || !moduleEnabled_ || !ctx_->enabled()) {
    return;
  }

  const char* predSafe = pred ? pred : "";
  if (predSafe[0] == '\0') {
    return;
  }

  publishDetectionEvent(accepted, predSafe, s1, s2, margin, t1, t1s, t2, t2s, t3, t3s);

  if (accepted == 0 || solved_) {
    return;
  }

  const char* expected = (seqPos_ < kSequenceLen) ? kSequence[seqPos_] : nullptr;

  if (expected && strcasecmp(predSafe, expected) == 0) {
    if (playedLen_ < kSequenceLen) {
      strncpy(played_[playedLen_], predSafe, kNoteMaxLen - 1);
      played_[playedLen_][kNoteMaxLen - 1] = '\0';
      playedLen_++;
    }
    seqPos_++;

    if (seqPos_ >= kSequenceLen) {
      solved_ = true;
      if (prefs_) prefs_->putBool(kPrefsSolvedKey, true);
      if (!solvedPublished_) {
        publishSolvedEvent();
        solvedPublished_ = true;
      }
    }
    return;
  }

  resetProgress("mismatch");

  if (strcasecmp(predSafe, kSequence[0]) == 0) {
    seqPos_ = 1;
    strncpy(played_[0], predSafe, kNoteMaxLen - 1);
    played_[0][kNoteMaxLen - 1] = '\0';
    playedLen_ = 1;
  }
}

void PianoRiddle::publishDetectionEvent(int accepted, const char* pred, float s1, float s2,
                                        float margin, const char* t1, float t1s,
                                        const char* t2, float t2s, const char* t3,
                                        float t3s) {
  if (!ctx_) return;

  const char* predSafe = pred ? pred : "";
  const char* t1Safe = t1 ? t1 : "";
  const char* t2Safe = t2 ? t2 : "";
  const char* t3Safe = t3 ? t3 : "";

  String payload = "{";
  payload += "\"note\":\"";
  payload += predSafe;
  payload += "\",";
  payload += "\"encoded\":\"";
  payload += encodeWhiteKey(predSafe);
  payload += "\",";
  payload += "\"accepted\":";
  payload += (accepted != 0) ? "true" : "false";
  payload += ",";
  payload += "\"margins\":{";
  payload += "\"s1\":";
  payload += String(s1, 6);
  payload += ",\"s2\":";
  payload += String(s2, 6);
  payload += ",\"margin\":";
  payload += String(margin, 6);
  payload += "},";
  payload += "\"top3\":[";
  payload += "{\"note\":\"";
  payload += t1Safe;
  payload += "\",\"encoded\":\"";
  payload += encodeWhiteKey(t1Safe);
  payload += "\",\"score\":";
  payload += String(t1s, 6);
  payload += "},";
  payload += "{\"note\":\"";
  payload += t2Safe;
  payload += "\",\"encoded\":\"";
  payload += encodeWhiteKey(t2Safe);
  payload += "\",\"score\":";
  payload += String(t2s, 6);
  payload += "},";
  payload += "{\"note\":\"";
  payload += t3Safe;
  payload += "\",\"encoded\":\"";
  payload += encodeWhiteKey(t3Safe);
  payload += "\",\"score\":";
  payload += String(t3s, 6);
  payload += "}";
  payload += "]}";

  const auto& topics = ctx_->config().topics;
  if (topics.state.length() > 0) {
    publish(topics.state.c_str(), payload, false);
  }
}

void PianoRiddle::publishSolvedEvent() {
  if (!ctx_) return;
  publish("game/event", "{\"node\":\"piano\",\"event\":\"solved\"}", false);
}

void PianoRiddle::resetProgress(const char* reason) {
  seqPos_ = 0;
  playedLen_ = 0;
  memset(played_, 0, sizeof(played_));
  if (reason) {
    String data = String("{\"reason\":\"") + reason + "\"}";
    log("DBG", "PIANO_PROGRESS_RESET", data);
  }
}

String PianoRiddle::encodeWhiteKey(const char* note) const {
  if (!note || !note[0]) return "";
  struct MapEntry { const char* note; const char* code; };
  static const MapEntry kMap[] = {
    {"f2","A"},{"g2","B"},{"a2","C"},{"b2","D"},
    {"c3","E"},{"d3","F"},{"e3","G"},{"f3","H"},
    {"g3","I"},{"a3","J"},{"b3","K"},{"c4","L"},
    {"d4","M"},{"e4","N"},{"f4","O"},{"g4","P"},
    {"a4","Q"},{"b4","R"},{"c5","S"},{"d5","T"},
    {"e5","U"},{"f5","V"},{"g5","W"},{"a5","X"},
    {"b5","Y"},{"c6","Z"}
  };
  for (const auto& entry : kMap) {
    if (strcasecmp(note, entry.note) == 0) return String(entry.code);
  }
  return String(note);
}

bool PianoRiddle::publish(const char* topic, const String& payload, bool retained) const {
  if (!ctx_) return false;
  return ctx_->publish(topic, payload, retained);
}

void PianoRiddle::log(const char* level, const String& msg) const {
  if (ctx_) {
    String tagged = String("[") + srcId_ + "] " + msg;
    ctx_->log(level, tagged, withSrc("", srcId_));
  }
}

void PianoRiddle::log(const char* level, const String& msg, const String& dataJson) const {
  if (ctx_) {
    String tagged = String("[") + srcId_ + "] " + msg;
    ctx_->log(level, tagged, withSrc(dataJson, srcId_));
  }
}

bool PianoRiddle::equalsCmd(const char* cmd, const char* ref) const {
  if (!cmd || !ref) return false;
  return strcasecmp(cmd, ref) == 0;
}

void PianoRiddle::setModuleEnabled(bool en) {
  moduleEnabled_ = en;
  log("INF", en ? "PIANO_ENABLE" : "PIANO_DISABLE");
}
