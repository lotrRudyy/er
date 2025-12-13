#pragma once

#include <Arduino.h>

#include "core_node.h"

class ImagesRiddle {
public:
  void begin(Core::NodeContext& ctx);
  void tick(uint32_t nowMs);
  bool onCmd(const char* cmd, const char* payload);

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
  void checkSolved();
  void publishButtonMetricsOnChange(int idx);
  void publishMetricsIfDue();
  void publishSolvedEvent(const char* rid);
  void openImagesMaglock();
  void log(const char* level, const String& msg);

  Core::NodeContext* ctx_ = nullptr;

  static constexpr int kButtonCount = 4;
  static constexpr int kButtonPins[kButtonCount] = {25, 26, 14, 12};
  static constexpr uint32_t kDebounceMs = 30;
  static constexpr uint32_t kEdgeMinLogMs = 100;
  static constexpr uint32_t kMetricIntervalMs = 1000;

  static constexpr const char* kTopicMetric = "er1/room1/images_piano/metric";
  static constexpr const char* kTopicEvent = "er1/room1/images_piano/event";
  static constexpr const char* kTopicLockImagesCmd = "er1/ctrl/lock/images/cmd";

  ButtonState buttons_[kButtonCount];
  bool allPressedPrev_ = false;
  uint32_t lastMetricMs_ = 0;
};

