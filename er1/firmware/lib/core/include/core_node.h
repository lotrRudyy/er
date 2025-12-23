#pragma once

#include <Arduino.h>
#include <Preferences.h>

#include "core_log.h"
#include "core_mqtt.h"
#include "core_ota.h"
#include "core_time.h"


namespace Core {

struct TopicConfig {
  String hb;
  String cmd;
  String evt;
  String state;
  String dbg;
  String log;
  String cfg;
  String ota;
};

struct HeartbeatConfig {
  uint32_t intervalMs = 5000;
  using Builder = void (*)(String& out, const class NodeContext& ctx, void* userData);
  Builder builder = nullptr;
  void* user = nullptr;
};

struct CommandConfig {
  bool allowEnableDisable = true;
  bool logEnableDisable = true;
  const char* levelEnable = "INF";
  const char* levelDisable = "INF";

  bool allowPing = true;
  bool logPing = true;
  const char* levelPing = "DBG";

  bool allowUpdate = true;
  bool logUpdate = true;
  const char* levelUpdate = "INF";

  bool allowReboot = false;
  bool logReboot = true;
  const char* levelReboot = "INF";

  bool logUnknown = true;
  const char* levelUnknown = "WRN";
  const char* unknownPrefix = "Unknown CMD: ";

  const char* cmdLogLevel = nullptr;  // e.g., "DBG"
};

struct NodeCoreConfig {
  const char* nodeId = nullptr;
  const char* buildId = nullptr;
  const char* fwVersion = nullptr;
  const char* fwDescription = nullptr;
  bool startEnabled = true;
  const char* prefsNamespace = nullptr;
  NetConfig net;
  TopicConfig topics;
  LogOptions log;
  HeartbeatConfig heartbeat;
  CommandConfig commands;
  OtaConfig ota;
  const char* mqttConnectedLogLevel = "INF";
};

class NodeCore;

class NodeContext {
public:
  explicit NodeContext(NodeCore& core);

  bool publish(const char* topic, const String& payload, bool retained = false, int /*qos*/ = 0);
  bool publish(const char* topic, const char* payload, bool retained = false, int /*qos*/ = 0);

  bool publishEvent(const char* type, const String& dataJson, uint32_t version = 1, const char* id = nullptr);
  bool publishState(const String& dataJson, bool retained = true);
  bool publishEnvelope(const char* topic, const char* type, uint32_t version, const String& dataJson,
                       const char* id = nullptr, bool retained = false);

  void log(const char* level, const String& msg);
  void log(const char* level, const String& msg, const String& dataJson);

  bool enabled() const;
  void setEnabled(bool en);

  uint32_t uptimeSeconds() const;
  uint32_t nowMs() const { return millis(); }
  PubSubClient* mqttClient();
  TimestampSource* timestampSource();

  const char* fwVersion() const;
  const char* buildId() const;
  const char* nodeId() const;
  const NodeCoreConfig& config() const;

  Preferences& prefs();

  void requestHeartbeat();
  void setHeartbeatInterval(uint32_t intervalMs);
  uint32_t heartbeatInterval() const;

private:
  NodeCore* core_;
};

using CommandHandler = bool (*)(const char* cmd, const char* payload, void* userData);
using SubscriptionHandler = void (*)(NodeContext& ctx, const char* topic, const String& payload, void* userData);

class NodeCore : public MqttDelegate, public TimestampSource {
public:
  friend class NodeContext;

  NodeCore();

  void begin(const NodeCoreConfig& cfg);
  void loop();

  NodeContext& context() { return ctx_; }
  const char* nodeId() const { return cfg_.nodeId; }
  const char* buildId() const { return cfg_.buildId; }

  bool registerCommandHandler(CommandHandler handler, void* userData);
  bool registerSubscription(const char* topic, SubscriptionHandler handler, void* userData);

  Logger& logger() { return logger_; }
  Preferences& prefs() { return prefs_; }

  bool publish(const char* topic, const String& payload, bool retained = false);
  bool publish(const char* topic, const char* payload, bool retained = false);

  void publishHeartbeatNow();
  void setHeartbeatInterval(uint32_t intervalMs);
  uint32_t heartbeatInterval() const { return heartbeatIntervalMs_; }

  bool enabled() const { return enabled_; }
  void setEnabled(bool en);

  // MqttDelegate overrides
  void onMqttConnected() override;
  void onMqttMessage(const char* topic, const uint8_t* payload, size_t length) override;

  // TimestampSource override
  TimestampFields currentTimestamp() override;

private:
  void handleCommandMessage(const String& raw);
  bool handleCoreCommand(const char* cmd, const char* arg);
  void publishHeartbeatIfDue();
  void logCommandEnvelope(const char* topic, const String& payload);
  bool topicMatches(const char* filter, const char* topic) const;
  void dispatchSubscription(const char* topic, const String& payload);
  void forceRestart();
  void restoreEnabledState();
  void persistEnabledState(const char* reason);
  bool publishEnvelope(const char* topic, const char* type, uint32_t version, const String& dataJson,
                       const char* id, bool retained);
  void maybeWarnMissingTs();
  void handleTimeStateMessage(const String& payload);

  // Runtime log-level control via MQTT (<node>/log/level).
  void handleLogLevelMessage(const String& payload);
  static bool logFilterThunk(const char* level, void* user);

  NodeCoreConfig cfg_{};
  HeartbeatConfig hbCfg_{};
  MqttClient mqtt_;
  Logger logger_;
  // User-provided log filter from cfg.log.filter (if any); composed with minLogRank_.
  LogFilterFn userLogFilter_ = nullptr;
  void* userLogFilterUser_ = nullptr;
  int minLogRank_ = 0;  // default DBG
  String topicLogLevel_;
  OtaUpdater ota_;
  Preferences prefs_;
  bool prefsReady_ = false;
  bool enabled_ = true;
  uint32_t lastHeartbeatMs_ = 0;
  uint32_t heartbeatIntervalMs_ = 0;
  bool heartbeatImmediate_ = false;
  char buildIdBuf_[CORE_TS_LEN] = {0};

  // Time sync state
  bool timeValidFirstSet_ = false;
  uint32_t lastTimeSyncParseErrorMs_ = 0;

  static constexpr size_t kMaxSubscriptions = 10;
  struct SubscriptionEntry {
    const char* topic;
    SubscriptionHandler handler;
    void* user;
  };
  SubscriptionEntry subs_[kMaxSubscriptions];
  size_t subCount_ = 0;

  CommandHandler moduleCmdHandler_ = nullptr;
  void* moduleCmdUser_ = nullptr;

  // buffers for parsed command/payload
  static constexpr size_t kCmdBufSize = 64;
  static constexpr size_t kPayloadBufSize = 256;
  char cmdBuf_[kCmdBufSize];
  char payloadBuf_[kPayloadBufSize];

  NodeContext ctx_;
  bool missingTsWarned_ = false;
  bool missingTsWarningActive_ = false;
};

TopicConfig makeTopicConfig(const char* nodeId, const TopicConfig& overrideCfg = {});
struct HeartbeatFields {
  const char* nodeId;
  const char* fw;
  const char* buildId;
  uint32_t uptime;
  const char* health;
  const char* mem;
  const char* lastErr;
};
void buildHeartbeatPayload(String& out, const HeartbeatFields& hb);

}  // namespace Core
