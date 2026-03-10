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

  // Start fresh progress on boot (no timeout tracking exists anymore)
  solved_ = false;
  solvedPublished_ = false;
  if (prefs_) prefs_->putBool(kPrefsSolvedKey, false);
  resetProgress("boot");
  gameActive_ = false;
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
  if (detectorStarted_ && gameActive_) {
    piano_detector_loop_once();
  }
  (void)nowMs;
  // NO TIMEOUT. Ever.
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
  data += "\"pos\":"; data += String(seqPos_); data += ",";
  data += "\"solved\":"; data += solved_ ? "true" : "false";
  data += "}";

  log("INF", compat, data);

  // Still allow replay even if already solved (so it can re-open lock).
  if (!ctx_ || !gameActive_ || !moduleEnabled_ || !ctx_->enabled()) return;
  if (!isAccepted) return;
  if (predSafe[0] == '\0') return;

  const char* expected = (seqPos_ < kSequenceLen) ? kSequence[seqPos_] : nullptr;

  // Correct next note -> append and advance
  if (expected && strcasecmp(predSafe, expected) == 0) {
    if (playedLen_ < kSequenceLen) {
      strncpy(played_[playedLen_], predSafe, kNoteMaxLen - 1);
      played_[playedLen_][kNoteMaxLen - 1] = '\0';
      playedLen_++;
    }
    seqPos_++;

    // Emit sequence after every accepted note
    logCurrentSequence();
    publishState();

    if (seqPos_ >= kSequenceLen) {
      // Only open lock the first time this riddle is solved
      if (!solved_) {
        openLock();
        solved_ = true;
        if (prefs_) prefs_->putBool(kPrefsSolvedKey, true);
      }

      // Publish solved event once
      if (!solvedPublished_) {
        publishSolvedEvent();
        solvedPublished_ = true;
      }

      // Rearm only for visual/state reset, not for another unlock
      resetProgress("solved");
      publishState();
    }
    return;
  }

  // Mismatch: reset completely, then if this note can start the sequence,
  // start with it (progress=1).
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
  String data = String("{\"mode\":\"") + (gameActive_ ? "ingame" : "standby") + "\",\"solved\":" + (solved_ ? "true" : "false") +
                ",\"enabled\":" + (gameActive_ && moduleEnabled_ && ctx_->enabled() ? "true" : "false") +
                ",\"progress\":" + String(seqPos_) + "}";
  const auto& topics = ctx_->config().topics;
  if (topics.state.length() > 0) {
    publish(topics.state.c_str(), "state", 1, withSrc(data, srcId_), nullptr, true);
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
