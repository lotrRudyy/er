#pragma once
#include <Arduino.h>
#include <Preferences.h>
#include <ArduinoJson.h>

namespace Core {
struct BootInfo {
  uint32_t reboot_count = 0;
  uint32_t last_event_ts = 0;   // seconds since boot (approx)
  bool     solved = false;      // per-riddle flag (extend per sketch if needed)
};

class State {
 public:
  static void begin(const char* ns, const char* room, const char* device);
  static void setSolved(bool v, bool publishNow = true);
  static bool isSolved();
  static void touchEvent();                 // bump last_event_ts + throttled persist
  static void loop();                       // throttled persistence
  static void onConnected();                // publish retained state + BOOT event
  static const BootInfo& info();

  // MQTT glue provided by core_net:
  static void setPublishFn(bool (*fn)(const String&, const String&, bool, int));
  static void setTopicFn(String (*fn)(const String& leaf));

 private:
  static void save(bool force=false);
};
} // namespace Core
