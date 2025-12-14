// SCHEMA GUARD: Reusable Pattern for New Nodes
// This selftest validates canonical topic schema alignment (Core::topic builder).
// Use this pattern for any node migration or new node integration to prevent regressions.
// Steps:
//   1. Create a selftest environment in platformio.ini: [env:node_schema_guard]
//   2. Implement runSelfTests() to verify topic patterns and payload structure
//   3. Run: pio run -e node_schema_guard to compile-time validate before deployment

#include <Arduino.h>

#include "core_mqtt.h"

using namespace Core;

String failureReason;

void recordFailure(const char* name, const char* detail) {
  if (failureReason.length() == 0) {
    failureReason.reserve(strlen(name) + strlen(detail) + 3);
    failureReason += name;
    failureReason += ": ";
    failureReason += detail;
  }
}

bool expect(bool condition, const char* name, const char* detail) {
  if (!condition) {
    recordFailure(name, detail);
    return false;
  }
  return true;
}

bool runSelfTests() {
  bool ok = true;
  // Verify canonical topic builder
  String t1 = topic("images", "hb");
  ok &= expect(t1 == "images/hb", "topic_builder_images_hb", "expected images/hb");

  String t2 = topic("maglock", "dbg");
  ok &= expect(t2 == "maglock/dbg", "topic_builder_maglock_dbg", "expected maglock/dbg");
  // Post-migration this test should pass; verify dbg topic presence.

  return ok;
}

void setup() {
  Serial.begin(115200);
  delay(200);
  bool ok = runSelfTests();
  if (ok) {
    Serial.println("MAGLOCK_MIGRATION_SELFTEST: PASS");
  } else {
    Serial.print("MAGLOCK_MIGRATION_SELFTEST: FAIL ");
    Serial.println(failureReason.length() ? failureReason : "unknown");
  }
  while (true) {
    delay(1000);
  }
}

void loop() {}
