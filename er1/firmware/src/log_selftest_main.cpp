#include <Arduino.h>
#include <ArduinoJson.h>

#include "core_log.h"

using Core::LogMessage;
using Core::Logger;
using Core::TimestampFields;

namespace {

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

bool validatePayload(const char* name, const String& data, bool expectObject, const char* expectedStringValue = nullptr) {
  Logger logger;
  TimestampFields ts{};
  ts.epoch = 123456;

  const String msg("test");
  LogMessage lm{"DBG", &msg, &data};
  String payload = logger.buildPayloadForTest(lm, ts);

  JsonDocument doc;
  DeserializationError err = deserializeJson(doc, payload);
  if (!expect(!err, name, err.c_str())) return false;

  const char* dType = doc["d_type"] | nullptr;
  if (!expect(dType != nullptr, name, "missing d_type")) return false;

  bool ok = true;
  if (expectObject) {
    ok &= expect(strcmp(dType, "object") == 0, name, "d_type not object");
    JsonVariant d = doc["d"];
    ok &= expect(d.is<JsonObject>(), name, "d is not object");
    if (d.is<JsonObject>()) {
      ok &= expect(d["a"] == 1, name, "object field a mismatch");
    }
  } else {
    ok &= expect(strcmp(dType, "string") == 0, name, "d_type not string");
    JsonVariantConst d = doc["d"];
    ok &= expect(d.is<const char*>(), name, "d is not string");
    if (expectedStringValue) {
      const String actual = d.as<String>();
      ok &= expect(actual == expectedStringValue, name, "string content mismatch");
    }
  }

  return ok;
}

bool runSelfTests() {
  bool allOk = true;
  allOk &= validatePayload("valid_object", String("{\"a\":1}"), true);
  allOk &= validatePayload("escaped_string", String("hello\\\"world"), false, "hello\"world");
  allOk &= validatePayload("malformed_object", String("{ \"a\": 1, }"), false, "{ \"a\": 1, }");
  allOk &= validatePayload("array_string", String("[1,2,3]"), false, "[1,2,3]");
  return allOk;
}

}  // namespace

void setup() {
  Serial.begin(115200);
  delay(200);
  bool ok = runSelfTests();
  if (ok) {
    Serial.println("CORE_LOG_SELFTEST: PASS");
  } else {
    Serial.print("CORE_LOG_SELFTEST: FAIL ");
    Serial.println(failureReason.length() ? failureReason : "unknown");
  }
  while (true) {
    delay(1000);
  }
}

void loop() {}
