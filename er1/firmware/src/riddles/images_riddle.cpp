#include "images_riddle.h"

#include <cstring>

namespace {

String prefixedMessage(const char* prefix, const String& msg) {
  String out(prefix);
  out += msg;
  return out;
}

}  // namespace

void ImagesRiddle::begin(Core::NodeContext& ctx, const char* nodeId) {
  ctx_ = &ctx;
  nodeId_ = (nodeId && nodeId[0]) ? nodeId : "images";
  topicDbg_ = Core::topic(nodeId_.c_str(), "dbg");
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
  allDownHoldActive_ = false;
  allDownHoldStartMs_ = 0;
  lastMetricMs_ = millis();
  publishState();
}

void ImagesRiddle::tick(uint32_t nowMs) {
  if (!ctx_) return;

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

  if (solved_) return;

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
  solved_ = true;
  log("INF", "IMAGES_SOLVED", "{\"mode\":\"all_hold\",\"cmd\":\"OPEN\"}");
  publishSolvedEvent("images");
  openImagesMaglock();
  publishState();
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
  ctx_->publish(topicDbg_.c_str(), payloadBtn);

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
  ctx_->publish(topicDbg_.c_str(), payloadAll);
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
    ctx_->publish(topicDbg_.c_str(), payload);
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
  ctx_->publish(topicDbg_.c_str(), payloadAll);
}

void ImagesRiddle::publishSolvedEvent(const char* rid) {
  solved_ = true;
  String data = String("{\"id\":\"") + rid + "\"}";
  ctx_->publishEvent("riddle_solved", data);
  publishState();
  log("INF", String("SOLVED event sent for rid=") + rid);
}

void ImagesRiddle::openImagesMaglock() {
  ctx_->publish(topicLockImagesCmd_.c_str(), "OPEN");
  log("INF", "Sent OPEN to images maglock");
}

void ImagesRiddle::publishState() {
  if (!ctx_) return;
  String data = String("{\"mode\":\"listening\",\"solved\":") + (solved_ ? "true" : "false") + "}";
  ctx_->publishState(data, true);
}

void ImagesRiddle::log(const char* level, const String& msg) {
  if (!ctx_) return;
  ctx_->log(level, prefixedMessage("[images] ", msg));
}

void ImagesRiddle::log(const char* level, const String& msg, const String& dataJson) {
  if (!ctx_) return;
  ctx_->log(level, prefixedMessage("[images] ", msg), dataJson);
}
