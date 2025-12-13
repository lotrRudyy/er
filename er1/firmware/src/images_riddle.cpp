#include "images_riddle.h"

namespace {

String prefixedMessage(const char* prefix, const String& msg) {
  String out(prefix);
  out += msg;
  return out;
}

}  // namespace

void ImagesRiddle::begin(Core::NodeContext& ctx) {
  ctx_ = &ctx;
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

  allPressedPrev_ = true;
  for (int i = 0; i < kButtonCount; i++) {
    if (buttons_[i].cur != LOW) {
      allPressedPrev_ = false;
      break;
    }
  }

  lastMetricMs_ = millis();
}

void ImagesRiddle::tick(uint32_t /*nowMs*/) {
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

  checkSolved();
}

bool ImagesRiddle::onCmd(const char* /*cmd*/, const char* /*payload*/) {
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
  log("INFO", msg);
  publishButtonMetricsOnChange(idx);
}

void ImagesRiddle::checkSolved() {
  bool allPressedNow = true;
  for (int i = 0; i < kButtonCount; i++) {
    if (buttons_[i].cur != LOW) {
      allPressedNow = false;
      break;
    }
  }

  if (!allPressedPrev_ && allPressedNow) {
    log("INFO", "ALL 4 BUTTONS PRESSED EDGE -> SOLVED (images riddle)");
    if (ctx_->enabled()) {
      publishSolvedEvent("images");
      openImagesMaglock();
    } else {
      log("INFO", "SOLVED condition reached but node is DISABLED -> no event / no open");
    }
  }

  allPressedPrev_ = allPressedNow;
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
  ctx_->publish(kTopicMetric, payloadBtn);

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
  ctx_->publish(kTopicMetric, payloadAll);
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
    ctx_->publish(kTopicMetric, payload);
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
  ctx_->publish(kTopicMetric, payloadAll);
}

void ImagesRiddle::publishSolvedEvent(const char* rid) {
  String payload = String("{\"type\":\"SOLVED\",\"rid\":\"") + rid + "\"}";
  ctx_->publish(kTopicEvent, payload);
  log("INFO", String("SOLVED event sent for rid=") + rid);
}

void ImagesRiddle::openImagesMaglock() {
  ctx_->publish(kTopicLockImagesCmd, "OPEN");
  log("INFO", "Sent OPEN to images maglock");
}

void ImagesRiddle::log(const char* level, const String& msg) {
  if (!ctx_) return;
  ctx_->log(level, prefixedMessage("[images] ", msg));
}

