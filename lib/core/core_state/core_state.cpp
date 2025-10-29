#include "core_state.hpp"

namespace Core {

static Preferences prefs;
static BootInfo S;
static uint32_t lastSaveMs = 0;
static const uint32_t SAVE_MIN_MS = 3000;   // wear guard

static String ROOM, DEVICE;
static bool   (*publish_)(const String&, const String&, bool, int) = nullptr;
static String (*topic_)(const String&) = nullptr;

void State::begin(const char* ns, const char* room, const char* device) {
  ROOM   = room   ? room   : "";
  DEVICE = device ? device : "";
  prefs.begin(ns ? ns : "app", false);

  S.reboot_count = prefs.getUInt("reboots", 0) + 1;
  S.last_event_ts = prefs.getUInt("last_ts", 0);
  S.solved        = prefs.getBool("solved", false);
  save(true);
}

void State::setPublishFn(bool (*fn)(const String&, const String&, bool, int)) { publish_ = fn; }
void State::setTopicFn(String (*fn)(const String&)) { topic_ = fn; }

void State::save(bool force) {
  const uint32_t now = millis();
  if (!force && (now - lastSaveMs) < SAVE_MIN_MS) return;
  prefs.putUInt("reboots", S.reboot_count);
  prefs.putUInt("last_ts",  S.last_event_ts);
  prefs.putBool("solved",   S.solved);
  lastSaveMs = now;
}

void State::setSolved(bool v, bool publishNow) {
  S.solved = v;
  S.last_event_ts = (uint32_t)(millis() / 1000);
  save();
  if (publishNow && publish_ && topic_) {
    StaticJsonDocument<160> d;
    d["solved"]  = S.solved;
    d["reboots"] = S.reboot_count;
    d["ts"]      = S.last_event_ts;
    String pay; serializeJson(d, pay);
    publish_(topic_("state"), pay, /*retained*/true, /*qos*/1);
  }
}

bool State::isSolved() { return S.solved; }

void State::touchEvent() {
  S.last_event_ts = (uint32_t)(millis() / 1000);
  save();
}

void State::onConnected() {
  if (publish_ && topic_) {
    // Retained snapshot so the controller/dashboard get an immediate picture
    StaticJsonDocument<160> d;
    d["boot"]    = true;
    d["reboots"] = S.reboot_count;
    d["solved"]  = S.solved;
    d["ts"]      = S.last_event_ts;
    String pay; serializeJson(d, pay);
    publish_(topic_("state"), pay, /*retained*/true, /*qos*/1);

    // Non-retained BOOT event
    publish_(topic_("event"), String("{\"type\":\"BOOT\"}"), false, 0);
  }
}

void State::loop() { save(); }

const BootInfo& State::info() { return S; }

} // namespace Core
