#pragma once

#include <Arduino.h>

#include "core_node.h"

class ImagesRiddle {
public:
  void begin(Core::NodeContext& ctx, const char* nodeId = nullptr);
  void tick(uint32_t nowMs);
  bool onCmd(const char* cmd, const char* payload);
  void setGameMode(bool inGame);

private:
  struct ButtonState {
    int pin;
    bool cur;
    bool prev;
    uint32_t lastChangeMs;
    uint32_t lastLogMs;
    uint32_t presses;
  };

  void handleButtonEdge(int idx, bool newState);
  void resetState(const char* reason);
  void handleAllDownHold(uint32_t nowMs);
  void startAllDownHold(uint32_t nowMs);
  void cancelAllDownHold(const char* reason);
  void publishButtonMetricsOnChange(int idx);
  void publishMetricsIfDue();
  void publishSolvedEvent(const char* rid);
  void publishState();
  void openImagesMaglock();
  bool publish(const char* topic, const char* type, uint32_t version, const String& dataJson,
               const char* id = nullptr, bool retained = false) const;
  bool publish(const char* topic, const String& payload, bool retained = false) const;
  void log(const char* level, const String& msg) const;
  void log(const char* level, const String& msg, const String& dataJson) const;

  Core::NodeContext* ctx_ = nullptr;
  String topicLockImagesCmd_;
  String nodeId_;

  static constexpr int kButtonCount = 4;
  static constexpr int kButtonPins[kButtonCount] = {27, 13, 14, 32}; // 27:natur, 13:puppe, 14:blumen, 32:jesus
  static constexpr uint32_t kDebounceMs = 30;
  static constexpr uint32_t kEdgeMinLogMs = 100;
  static constexpr uint32_t kMetricIntervalMs = 1000;
  static constexpr uint32_t kAllDownHoldMs = 200;

  ButtonState buttons_[kButtonCount];
  bool solved_ = false;
  bool retriggerArmed_ = true;
  bool gameActive_ = false;
  bool moduleEnabled_ = true;
  bool allDownHoldActive_ = false;
  uint32_t allDownHoldStartMs_ = 0;
  uint32_t lastMetricMs_ = 0;
};
