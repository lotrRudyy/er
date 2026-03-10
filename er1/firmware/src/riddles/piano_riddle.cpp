// piano_riddle.cpp
#include "piano_riddle.h"

#include <cstring>
#include <strings.h>

namespace {
PianoRiddle* gPianoRiddle = nullptr;

constexpr const char* kPrefsImagesSolvedKey = "images_solved";

bool imagesSolvedFromPrefs(Preferences* prefs) {
  return prefs ? prefs->getBool(kPrefsImagesSolvedKey, false) : false;
}

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

extern "C" void piano_detector_on_result(int accepted, const char* pred, float s1, float s2, float margin,
                                         float hps_ratio, int harmonic_ok, const char* t1, float t1s,
                                         const char* t2, float t2s, const char* t3, float t3s) {
  if (gPianoRiddle) {
    gPianoRiddle->handleDetectorResult(accepted, pred, s1, s2, margin, hps_ratio, harmonic_ok,
                                       t1, t1s, t2, t2s, t3, t3s);
  }
}

void PianoRiddle::begin(Core::NodeContext& ctx, const char* srcId) {
  ctx_ = &ctx;
  prefs_ = &ctx.prefs();

  srcId_ = (srcId && srcId[0]) ? srcId : "piano";
  topicLockCmd_ = Core::topic("maglock", "lock/r2/cmd");

  solved_ = prefs_ ? prefs_->getBool(kPrefsSolvedKey, false) : false;
  solvedPublished_ = solved_;

  gPianoRiddle = this;
  if (!detectorStarted_) {
    piano_detector_setup();
    detectorStarted_ = true;
  }

  solved_ = false;
  solvedPublished_ = false;
  if (prefs_) prefs_->putBool(kPrefsSolvedKey, false);
  resetProgress("boot");
  gameActive_ = false;
  moduleEnabled_ = true;
  publishState();
}

void PianoRiddle::setGameMode(bool inGame) {
  gameActive_ = inGame;
  solved_ = false;
  solvedPublished_ = false;
  if (prefs_) prefs_->putBool(kPrefsSolvedKey, false);
  resetProgress(inGame ? "game_start" : "game_off");
  publishState();
}

void PianoRiddle::tick(uint32_t nowMs) {
  if (detectorStarted_ && gameActive_ && imagesSolvedFromPrefs(prefs_) && moduleEnabled_ && ctx_ && ctx_->enabled()) {
    piano_detector_loop_once();
  }
  (void)nowMs;
}

bool PianoRiddle::onCmd(const char* cmd, const char* /*payload*/) {
  if (!cmd) return false;

  if (equalsCmd(cmd, "PIANO_ENABLE") || equalsCmd(cmd, "ENABLE")) {
    setModuleEnabled(true);
    publishState();
    return true;
  }
  if (equalsCmd(cmd, "PIANO_DISABLE") || equalsCmd(cmd, "DISABLE")) {
    setModuleEnabled(false);
    publishState();
    return true;
  }
  if (equalsCmd(cmd, "PIANO_RESET") || equalsCmd(cmd, "RESET_PIANO") || equalsCmd(cmd, "RESET")) {
    solved_ = false;
    solvedPublished_ = false;
    if (prefs_) prefs_->putBool(kPrefsSolvedKey, false);
    resetProgress("cmd");
    publishState();
    return true;
  }
  if (equalsCmd(cmd, "SOLVE") || equalsCmd(cmd, "SOLVE_PIANO")) {
    if (!solved_) {
      solved_ = true;
      if (prefs_) prefs_->putBool(kPrefsSolvedKey, true);
      openLock();
    }
    if (!solvedPublished_) {
      publishSolvedEvent();
      solvedPublished_ = true;
    }
    publishState();
    return true;
  }
  if (equalsCmd(cmd, "STATUS")) {
    publishState();
    return true;
  }
  if (equalsCmd(cmd, "PIANO_SET_SEQ") || equalsCmd(cmd, "SET_SEQ")) {
    log("WRN", "PIANO_SET_SEQ_UNSUPPORTED");
    return true;
  }
  return false;
}

void PianoRiddle::handleDetectorResult(int accepted, const char* pred, float s1, float s2, float margin,
                                       float hps_ratio, int harmonic_ok, const char* t1, float t1s,
                                       const char* t2, float t2s, const char* t3, float t3s) {
  const char* predSafe = pred ? pred : "";
  const char* t1Safe = t1 ? t1 : "";
  const char* t2Safe = t2 ? t2 : "";
  const char* t3Safe = t3 ? t3 : "";
  bool isAccepted = accepted != 0;

  String compat = String(isAccepted ? "NOTE_COMPAT " : "REJ_COMPAT ");
  compat += "pred="; compat += predSafe;
  compat += " s1="; compat += String(s1, 4);
  compat += " s2="; compat += String(s2, 4);
  compat += " m=";  compat += String(margin, 4);
  compat += " hps="; compat += String(hps_ratio, 2);
  compat += " harm="; compat += (harmonic_ok ? "1" : "0");
  compat += " top3=[";
  compat += t1Safe; compat += " "; compat += String(t1s, 4);
  compat += ", ";
  compat += t2Safe; compat += " "; compat += String(t2s, 4);
  compat += ", ";
  compat += t3Safe; compat += " "; compat += String(t3s, 4);
  compat += "]";

  String data = String("{\"t\":\"") + (isAccepted ? "NOTE" : "REJ") + "\",";
  data += "\"pred\":\""; data += predSafe; data += "\",";
  data += "\"s1\":"; data += String(s1, 6); data += ",";
  data += "\"s2\":"; data += String(s2, 6); data += ",";
  data += "\"margin\":"; data += String(margin, 6); data += ",";
  data += "\"hps\":"; data += String(hps_ratio, 6); data += ",";
  data += "\"harm\":"; data += harmonic_ok ? "1" : "0";
  data += ",\"top\":[{\"p\":\""; data += t1Safe; data += "\",\"s\":"; data += String(t1s, 6); data += "},";
  data += "{\"p\":\""; data += t2Safe; data += "\",\"s\":"; data += String(t2s, 6); data += "},";
  data += "{\"p\":\""; data += t3Safe; data += "\",\"s\":"; data += String(t3s, 6); data += "}],";
  data += "\"pos\":"; data += String(seqPos_) + ",";
  data += "\"solved\":"; data += solved_ ? "true" : "false";
  data += ",\"images_ready\":"; data += imagesSolvedFromPrefs(prefs_) ? "true" : "false";
  data += "}";

  log("INF", compat, data);

  if (!ctx_ || !gameActive_ || !imagesSolvedFromPrefs(prefs_) || !moduleEnabled_ || !ctx_->enabled()) return;
  if (predSafe[0] == '\0') return;

  if (isAccepted) {
    appendPlayed(predSafe);
    appendPlayedEncoded(encodeWhiteKey(predSafe));
    appendTopPrediction(predSafe);
    appendCombinedPrediction(predSafe);
  } else {
    appendTopPrediction(predSafe);
    appendCombinedPrediction(predSafe);
    publishState();
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

    logCurrentSequence();
    publishState();

    if (seqPos_ >= kSequenceLen) {
      if (!solved_) {
        openLock();
        solved_ = true;
        if (prefs_) prefs_->putBool(kPrefsSolvedKey, true);
      }
      if (!solvedPublished_) {
        publishSolvedEvent();
        solvedPublished_ = true;
      }
      resetProgress("solved");
      publishState();
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

  logCurrentSequence();
  publishState();
}

void PianoRiddle::publishState() {
  if (!ctx_) return;

  const bool imagesReady = imagesSolvedFromPrefs(prefs_);
  const bool effectiveActive = gameActive_ && imagesReady;

  String seqJson = "[";
  for (size_t i = 0; i < playedLen_; ++i) {
    if (i > 0) seqJson += ",";
    seqJson += "\"";
    seqJson += played_[i];
    seqJson += "\"";
  }
  seqJson += "]";

  String seqEncodedJson = "[";
  for (size_t i = 0; i < playedLen_; ++i) {
    if (i > 0) seqEncodedJson += ",";
    seqEncodedJson += "\"";
    seqEncodedJson += encodeWhiteKey(played_[i]);
    seqEncodedJson += "\"";
  }
  seqEncodedJson += "]";

  String full = String("{\"id\":\"") + srcId_ +
                "\",\"mode\":\"" + (effectiveActive ? "ingame" : "standby") +
                "\",\"enabled\":" + (effectiveActive && moduleEnabled_ && ctx_->enabled() ? "true" : "false") +
                ",\"solved\":" + (solved_ ? "true" : "false") +
                ",\"raw_state\":\"" + (solved_ ? "solved" : (seqPos_ > 0 ? "progress" : "idle")) +
                "\",\"images_ready\":" + (imagesReady ? "true" : "false") +
                ",\"progress\":" + String(seqPos_) +
                ",\"played_notes\":" + joinJsonArray(playedNotes_, playedNotesLen_) +
                ",\"played_encoded_notes\":" + joinJsonArray(playedEncodedNotes_, playedEncodedNotesLen_) +
                ",\"top_predictions\":" + joinJsonArray(topPredictions_, topPredictionsLen_) +
                ",\"played_encoded_plus_predictions\":" + joinJsonArray(combinedPredictions_, combinedPredictionsLen_) +
                ",\"sequence\":" + seqJson +
                ",\"sequence_encoded\":" + seqEncodedJson +
                "}";

  const auto& topics = ctx_->config().topics;
  if (topics.state.length() > 0) {
    publish(topics.state.c_str(), "state", 1, withSrc(full, srcId_), nullptr, true);
  }
}

void PianoRiddle::publishSolvedEvent() {
  if (!ctx_) return;
  String payload = "{\"id\":\"piano\",\"seq\":\"";
  for (size_t i = 0; i < kSequenceLen; ++i) {
    if (i > 0) payload += ",";
    payload += kSequence[i];
  }
  payload += "\"}";
  const auto& topics = ctx_->config().topics;
  if (topics.evt.length() > 0) {
    publish(topics.evt.c_str(), "riddle_solved", 1, withSrc(payload, srcId_));
  }
}

void PianoRiddle::openLock() const {
  if (!ctx_) return;
  publish(topicLockCmd_.c_str(), "OPEN");
}

void PianoRiddle::resetProgress(const char* reason) {
  seqPos_ = 0;
  playedLen_ = 0;
  memset(played_, 0, sizeof(played_));
  playedNotesLen_ = 0;
  playedEncodedNotesLen_ = 0;
  topPredictionsLen_ = 0;
  combinedPredictionsLen_ = 0;
  if (reason) {
    String data = String("{\"reason\":\"") + reason + "\"}";
    log("DBG", "PIANO_PROGRESS_RESET", data);
  }
}

void PianoRiddle::logCurrentSequence() const {
  if (!ctx_) return;

  String seq;
  for (size_t i = 0; i < playedLen_; ++i) {
    if (i > 0) seq += ",";
    seq += played_[i];
  }

  String data = String("{\"seq\":\"") + seq + "\",\"progress\":" + String(seqPos_) + "}";
  log("INF", "PIANO_SEQ", data);
  log("DBG", "PIANO_SEQ", data);
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

String PianoRiddle::joinJsonArray(const String* values, size_t count) const {
  String out = "[";
  for (size_t i = 0; i < count; ++i) {
    if (i > 0) out += ",";
    out += "\"";
    out += values[i];
    out += "\"";
  }
  out += "]";
  return out;
}

void PianoRiddle::appendPlayed(const char* note) {
  if (playedNotesLen_ < kHistoryMax) playedNotes_[playedNotesLen_++] = String(note);
}

void PianoRiddle::appendPlayedEncoded(const String& encoded) {
  if (playedEncodedNotesLen_ < kHistoryMax) playedEncodedNotes_[playedEncodedNotesLen_++] = encoded;
}

void PianoRiddle::appendTopPrediction(const char* note) {
  if (topPredictionsLen_ < kHistoryMax) topPredictions_[topPredictionsLen_++] = String(note);
}

void PianoRiddle::appendCombinedPrediction(const char* note) {
  if (combinedPredictionsLen_ < kHistoryMax) combinedPredictions_[combinedPredictionsLen_++] = encodeWhiteKey(note);
}

bool PianoRiddle::publish(const char* topic, const char* type, uint32_t version, const String& dataJson,
                          const char* id, bool retained) const {
  if (!ctx_) return false;
  return ctx_->publishEnvelope(topic, type, version, dataJson, id, retained);
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
