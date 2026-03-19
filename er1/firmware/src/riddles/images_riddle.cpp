#include "images_riddle.h"

#include <cstring>
#include <strings.h>

namespace {

constexpr const char* kImagesSolvedPrefKey = "images_solved";

String prefixedMessage(const char* prefix, const String& msg) {
  String out(prefix);
  out += msg;
  return out;
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
    if (out.length() > 1) {
      out += ",";
    }
    out += "\"src\":\"";
    out += srcVal;
    out += "\"}";
    return out;
  }
  String out = String("{\"src\":\"") + srcVal + "\",\"msg\":\"" + dataJson + "\"}";
  return out;
}

}  // namespace

void ImagesRiddle::begin(Core::NodeContext& ctx, const char* nodeId) {
  ctx_ = &ctx;
  nodeId_ = (nodeId && nodeId[0]) ? nodeId : "images";

  for (int i = 0; i < kButtonCount; i++) {
    buttons_[i].pin = kButtonPins[i];
    buttons_[i].presses = 0;
    buttons_[i].lastChangeMs = 0;
    buttons_[i].lastLogMs = 0;
    pinMode(buttons_[i].pin, INPUT_PULLUP);
    bool lvl = digitalRead(buttons_[i].pin);
    buttons_[i].cur = lvl;
    buttons_[i].prev = lvl;
  }

  solved_ = false;
  gameActive_ = false;
  moduleEnabled_ = true;
  allDownHoldActive_ = false;
  solveArmedAfterRelease_ = false;
  startupBlockLogged_ = false;
  allDownHoldStartMs_ = 0;

  ctx_->prefs().putBool(kImagesSolvedPrefKey, false);
  publishState();
}

void ImagesRiddle::setGameMode(bool inGame) {
  gameActive_ = inGame;
  ctx_->prefs().putBool(kImagesSolvedPrefKey, false);
  resetState(inGame ? "game_start" : "game_off");
  publishState();
}

void ImagesRiddle::tick(uint32_t nowMs) {
  if (!ctx_) return;
  if (!gameActive_) return;

  for (int i = 0; i < kButtonCount; i++) {
    ButtonState& b = buttons_[i];
    bool lvl = digitalRead(b.pin);
    b.cur = lvl;
    if (b.cur != b.prev) {
      handleButtonEdge(i, b.cur);
      b.prev = b.cur;
    }
  }

  if (!ctx_->enabled() || !moduleEnabled_) {
    cancelAllDownHold("disabled");
    return;
  }

  handleAllDownHold(nowMs);
}

bool ImagesRiddle::onCmd(const char* cmd, const char* /*payload*/) {
  if (!cmd) return false;

  if (strcasecmp(cmd, "RESET_IMAGES") == 0 || strcasecmp(cmd, "RESET") == 0) {
    ctx_->prefs().putBool(kImagesSolvedPrefKey, false);
    resetState("reset_images");
    publishState();
    return true;
  }

  if (strcasecmp(cmd, "SOLVE_IMAGES") == 0 || strcasecmp(cmd, "SOLVE") == 0) {
    if (!solved_) {
      solved_ = true;
      allDownHoldActive_ = false;
      allDownHoldStartMs_ = 0;
      ctx_->prefs().putBool(kImagesSolvedPrefKey, true);
      log("INF", "IMAGES_SOLVED", "{\"mode\":\"cmd\",\"cmd\":\"OPEN\"}");
      publishSolvedEvent("images");
    } else {
      publishState();
    }
    return true;
  }

  if (strcasecmp(cmd, "ENABLE") == 0) {
    moduleEnabled_ = true;
    publishState();
    return true;
  }

  if (strcasecmp(cmd, "DISABLE") == 0) {
    moduleEnabled_ = false;
    cancelAllDownHold("disabled_cmd");
    publishState();
    return true;
  }

  if (strcasecmp(cmd, "STATUS") == 0) {
    publishState();
    return true;
  }

  return false;
}

void ImagesRiddle::handleButtonEdge(int idx, bool newState) {
  uint32_t now = millis();
  ButtonState& b = buttons_[idx];
  uint32_t dtChange = now - b.lastChangeMs;
  if (dtChange < kDebounceMs) return;

  uint32_t dtLog = now - b.lastLogMs;
  if (dtLog < kEdgeMinLogMs) return;

  b.lastChangeMs = now;
  b.lastLogMs = now;

  if (!newState) {
    b.presses++;
  }

  const char* stateStr = newState ? "RELEASED" : "PRESSED";
  String ev = String("{\"node\":\"images\",\"event\":\"button_state\",\"buttons\":[") +
              (buttons_[0].cur == LOW ? "1" : "0") + "," +
              (buttons_[1].cur == LOW ? "1" : "0") + "," +
              (buttons_[2].cur == LOW ? "1" : "0") + "," +
              (buttons_[3].cur == LOW ? "1" : "0") + "]}";
  publish("game/event", ev, false);
  String msg = String("BTN idx=") + idx +
               " pin=" + b.pin +
               " state=" + stateStr +
               " dt=" + dtChange + "ms" +
               " presses=" + b.presses;
  log("INF", msg);
  publishState();
}

void ImagesRiddle::resetState(const char* reason) {
  cancelAllDownHold(reason ? reason : "reset");
  bool wasSolved = solved_;
  solved_ = false;
  solveArmedAfterRelease_ = false;
  startupBlockLogged_ = false;
  if (reason) {
    String data = String("{\"reason\":\"") + reason +
                  "\",\"was_solved\":" + (wasSolved ? "1" : "0") + "}";
    log("INF", "IMAGES_STATE_RESET", data);
  }
}

bool ImagesRiddle::allButtonsPressed() const {
  for (int i = 0; i < kButtonCount; ++i) {
    if (buttons_[i].cur != LOW) {
      return false;
    }
  }
  return true;
}

void ImagesRiddle::handleAllDownHold(uint32_t nowMs) {
  const bool allPressed = allButtonsPressed();

  if (!allPressed) {
    cancelAllDownHold("release");

    if (!solveArmedAfterRelease_) {
      solveArmedAfterRelease_ = true;
      startupBlockLogged_ = false;
      log("INF", "IMAGES_SOLVE_ARMED", "{\"reason\":\"buttons_released_once\"}");
      publishState();
    }
    return;
  }

  if (solved_) {
    // Keep allowing manual/open-after-solve behavior; just stop hold-timer retriggers.
    allDownHoldActive_ = false;
    allDownHoldStartMs_ = 0;
    return;
  }

  if (!solveArmedAfterRelease_) {
    cancelAllDownHold("await_release");
    if (!startupBlockLogged_) {
      startupBlockLogged_ = true;
      log("INF", "IMAGES_SOLVE_BLOCKED", "{\"reason\":\"await_first_release\"}");
    }
    return;
  }

  if (!allDownHoldActive_) {
    startAllDownHold(nowMs);
    return;
  }

  if (nowMs - allDownHoldStartMs_ < kAllDownHoldMs) {
    return;
  }

  allDownHoldActive_ = false;
  allDownHoldStartMs_ = 0;

  solved_ = true;
  ctx_->prefs().putBool(kImagesSolvedPrefKey, true);
  log("INF", "IMAGES_SOLVED", "{\"mode\":\"all_hold\",\"cmd\":\"OPEN\"}");
  publishSolvedEvent("images");
}

void ImagesRiddle::startAllDownHold(uint32_t nowMs) {
  if (solved_ || allDownHoldActive_) return;
  allDownHoldActive_ = true;
  allDownHoldStartMs_ = nowMs;
  String data = String("{\"hold_ms\":") + kAllDownHoldMs + "}";
  log("INF", "ALL_DOWN_TIMER_START", data);
}

void ImagesRiddle::cancelAllDownHold(const char* reason) {
  allDownHoldStartMs_ = 0;
  if (!allDownHoldActive_) return;
  allDownHoldActive_ = false;
  const char* why = reason ? reason : "release";
  String data = String("{\"reason\":\"") + why + "\"}";
  log("INF", "ALL_DOWN_TIMER_CANCEL", data);
}

void ImagesRiddle::publishSolvedEvent(const char* rid) {
  solved_ = true;
  ctx_->prefs().putBool(kImagesSolvedPrefKey, true);
  String payload = String("{\"node\":\"") + rid + "\",\"event\":\"solved\"}";
  publish("game/event", payload, false);
  publishState();
  log("INF", String("SOLVED event sent for rid=") + rid);
}


void ImagesRiddle::publishState() {
  if (!ctx_) return;

  const bool effectiveEnabled = gameActive_ && moduleEnabled_ && ctx_->enabled();
  const bool allPressed = allButtonsPressed();
  const char* rawState = solved_ ? "solved" : (allDownHoldActive_ ? "holding" : "idle");

  String data;
  data.reserve(320);
  data = String("{\"id\":\"images\"") +
         ",\"mode\":\"" + (gameActive_ ? "ingame" : "standby") + "\"" +
         ",\"enabled\":" + String(effectiveEnabled ? "true" : "false") +
         ",\"solved\":" + String(solved_ ? "true" : "false") +
         ",\"armed_after_release\":" + String(solveArmedAfterRelease_ ? "true" : "false") +
         ",\"all_pressed\":" + String(allPressed ? "true" : "false") +
         ",\"raw_state\":\"" + rawState + "\"" +
         ",\"buttons\":{" +
         "\"jesus\":" + String(buttons_[3].cur == LOW ? "true" : "false") + "," +
         "\"blumen\":" + String(buttons_[2].cur == LOW ? "true" : "false") + "," +
         "\"natur\":" + String(buttons_[0].cur == LOW ? "true" : "false") + "," +
         "\"puppe\":" + String(buttons_[1].cur == LOW ? "true" : "false") +
         "}}";

  const auto& topics = ctx_->config().topics;
  if (topics.state.length() > 0) {
    publish(topics.state.c_str(), "state", 1, withSrc(data, nodeId_), nullptr, true);
  }
}

bool ImagesRiddle::publish(const char* topic, const char* type, uint32_t version, const String& dataJson,
                           const char* id, bool retained) const {
  if (!ctx_) return false;
  return ctx_->publishEnvelope(topic, type, version, dataJson, id, retained);
}

bool ImagesRiddle::publish(const char* topic, const String& payload, bool retained) const {
  if (!ctx_) return false;
  return ctx_->publish(topic, payload, retained);
}

void ImagesRiddle::log(const char* level, const String& msg) const {
  if (!ctx_) return;
  ctx_->log(level, prefixedMessage("[images] ", msg), withSrc("", nodeId_));
}

void ImagesRiddle::log(const char* level, const String& msg, const String& dataJson) const {
  if (!ctx_) return;
  ctx_->log(level, prefixedMessage("[images] ", msg), withSrc(dataJson, nodeId_));
}
