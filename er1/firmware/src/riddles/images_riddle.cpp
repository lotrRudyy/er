#include "images_riddle.h"

#include <cstring>

namespace {

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
  // Metrics go via core_log (DBG) so <node>/log/level controls verbosity.
  topicLockImagesCmd_ = Core::topic("maglock", "lock/images/cmd");

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
  allDownHoldActive_ = false;
  allDownHoldStartMs_ = 0;
  lastMetricMs_ = millis();
  publishState();
}

void ImagesRiddle::setGameMode(bool inGame) {
  gameActive_ = inGame;
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

  if (!ctx_->enabled()) {
    cancelAllDownHold("disabled");
    return;
  }

  handleAllDownHold(nowMs);
  publishMetricsIfDue();
}

bool ImagesRiddle::onCmd(const char* cmd, const char* /*payload*/) {
  if (!cmd) return false;
  if (strcasecmp(cmd, "RESET_IMAGES") == 0) {
    resetState("reset_images");
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
  String msg = String("BTN idx=") + idx +
               " pin=" + b.pin +
               " state=" + stateStr +
               " dt=" + dtChange + "ms" +
               " presses=" + b.presses;
  log("INF", msg);
  publishButtonMetricsOnChange(idx);
}

void ImagesRiddle::resetState(const char* reason) {
  cancelAllDownHold(reason ? reason : "reset");
  bool wasSolved = solved_;
  solved_ = false;
  if (reason) {
    String data = String("{\"src\":\"") + reason +
                  "\",\"was_solved\":" + (wasSolved ? "1" : "0") + "}";
    log("INF", "IMAGES_STATE_RESET", data);
  }
}

void ImagesRiddle::handleAllDownHold(uint32_t nowMs) {
  bool allPressed = true;
  for (int i = 0; i < kButtonCount; i++) {
    if (buttons_[i].cur != LOW) {
      allPressed = false;
      break;
    }
  }

  if (!allPressed) {
    cancelAllDownHold("release");
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
  if (!solved_) {
    solved_ = true;
    log("INF", "IMAGES_SOLVED", "{\"mode\":\"all_hold\",\"cmd\":\"OPEN\"}");
    publishSolvedEvent("images");
  } else {
    log("INF", "IMAGES_RETRIGGER", "{\"mode\":\"all_hold\",\"cmd\":\"OPEN\"}");
    publishState();
  }
  openImagesMaglock();
}

void ImagesRiddle::startAllDownHold(uint32_t nowMs) {
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

void ImagesRiddle::publishButtonMetricsOnChange(int idx) {
  ButtonState& b = buttons_[idx];
  uint32_t uptime = millis() / 1000;
  String payloadBtn = String("{\"t\":\"BTN\",\"fw\":\"") + ctx_->fwVersion() +
                      "\",\"up\":" + uptime +
                      ",\"en\":" + (ctx_->enabled() ? "1" : "0") +
                      ",\"i\":" + idx +
                      ",\"pin\":" + b.pin +
                      ",\"state\":" + (b.cur ? 1 : 0) +
                      ",\"presses\":" + b.presses +
                      "}";
  log("DBG", "images_btn", withSrc(payloadBtn, nodeId_));

  bool allPressedNow = true;
  for (int i = 0; i < kButtonCount; i++) {
    // IMPORTANT: this function is called from inside the per-button scan loop.
    // At this moment, buttons_[j].cur for j>idx may still contain the previous
    // tick's value. Read pins directly to avoid reporting a stale all_pressed.
    if (digitalRead(buttons_[i].pin) != LOW) {
      allPressedNow = false;
      break;
    }
  }

  String payloadAll = String("{\"t\":\"ALL\",\"fw\":\"") + ctx_->fwVersion() +
                      "\",\"up\":" + uptime +
                      ",\"all_pressed\":" + (allPressedNow ? 1 : 0) +
                      "}";
  log("DBG", "images_all", withSrc(payloadAll, nodeId_));
}

void ImagesRiddle::publishMetricsIfDue() {
  uint32_t now = millis();
  if (now - lastMetricMs_ < kMetricIntervalMs) return;
  lastMetricMs_ = now;

  uint32_t uptime = millis() / 1000;
  for (int i = 0; i < kButtonCount; i++) {
    ButtonState& b = buttons_[i];
    String payload = String("{\"t\":\"BTN\",\"fw\":\"") + ctx_->fwVersion() +
                     "\",\"up\":" + uptime +
                     ",\"en\":" + (ctx_->enabled() ? "1" : "0") +
                     ",\"i\":" + i +
                     ",\"pin\":" + b.pin +
                     ",\"state\":" + (b.cur ? 1 : 0) +
                     ",\"presses\":" + b.presses +
                     "}";
    log("DBG", "images_metrics", withSrc(payload, nodeId_));
  }

  bool allPressedNow = true;
  for (int i = 0; i < kButtonCount; i++) {
    if (buttons_[i].cur != LOW) {
      allPressedNow = false;
      break;
    }
  }

  String payloadAll = String("{\"t\":\"ALL\",\"fw\":\"") + ctx_->fwVersion() +
                      "\",\"up\":" + uptime +
                      ",\"all_pressed\":" + (allPressedNow ? 1 : 0) +
                      "}";
  log("DBG", "images_all", withSrc(payloadAll, nodeId_));
}

void ImagesRiddle::publishSolvedEvent(const char* rid) {
  solved_ = true;
  String data = String("{\"id\":\"") + rid + "\"}";
  const auto& topics = ctx_->config().topics;
  if (topics.evt.length() > 0) {
    publish(topics.evt.c_str(), "riddle_solved", 1, withSrc(data, nodeId_));
  }
  publishState();
  log("INF", String("SOLVED event sent for rid=") + rid);
}

void ImagesRiddle::openImagesMaglock() {
  publish(topicLockImagesCmd_.c_str(), "OPEN");
  log("INF", "Sent OPEN to images maglock");
}

void ImagesRiddle::publishState() {
  if (!ctx_) return;
  String data = String("{\"mode\":\"") + (gameActive_ ? "ingame" : "standby") + "\",\"solved\":" + (solved_ ? "true" : "false") + "}";
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
