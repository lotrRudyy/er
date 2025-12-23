#include <Arduino.h>
#include <IPAddress.h>
#include <cstring>

#include "core_node.h"
#include "ctrl/maglock_controller.h"

using namespace Core;

// ======================= FIRMWARE INFO =======================
static const char* FW_VERSION = "1.6";
static const char* FW_DESC = "maglock_ctrl 1.6 - OTA JSON command, PSK removed";
static const char* FW_BUILD_ID = "LPRPDRR5X7Y1B0HOAW2H";

// ======================= NETWORK CONFIG ======================
static const uint8_t MAC_ADDR[6] = {0xDE, 0xAD, 0xBE, 0xEF, 0xFE, 0x50};
static const IPAddress NET_IP(192, 168, 0, 11);
static const IPAddress NET_DNS(0, 0, 0, 0);
static const IPAddress NET_GW(0, 0, 0, 0);
static const IPAddress NET_SUBNET(255, 255, 255, 0);
static const IPAddress MQTT_SERVER(192, 168, 0, 10);
static constexpr uint16_t MQTT_PORT = 1883;

// ======================= TOPICS ==============================
static const char* TOPIC_GAME = "game/state";
static const char* TOPIC_KNOCK_EVENT = "knocking/evt";
static const char* TOPIC_LOCK_CMD = "maglock/lock/+/cmd";

// ======================= OTA CONFIG ==========================
static const char* OTA_HOST = "192.168.0.10";
static constexpr uint16_t OTA_PORT = 80;
static const char* OTA_PATH = "/node_firmware/maglock.bin";
static const char* OTA_PATH_PREFIX = "/node_firmware/";
static const char* const OTA_ALLOWED_HOST = OTA_HOST;

// ======================= CORE + MODULE =======================
static NodeCore nodeCore;
static MaglockController maglock;

static bool logFilter(const char* level, void* user) {
  auto* module = static_cast<MaglockController*>(user);
  return module ? module->shouldAllowLog(level) : true;
}

static void heartbeatBuilder(String& out, const NodeContext& ctx, void* user) {
  auto* module = static_cast<MaglockController*>(user);
  uint32_t errCnt = module ? module->errorCount() : 0;
  if (ctx.logErrorCount() > errCnt) errCnt = ctx.logErrorCount();
  uint32_t errCode = (errCnt > 0) ? 1 : 0;
  uint32_t errSince = (errCode > 0) ? ctx.lastErrorSinceUp() : 0;
  String errMsg = (errCode > 0) ? ctx.lastErrorMsg() : "";
  HeartbeatFields hb{
      ctx.nodeId(),
      ctx.fwVersion(),
      ctx.buildId(),
      ctx.uptimeSeconds(),
      errCnt,
      errCode,
      errSince,
      (errCode > 0 && errMsg.length() > 0) ? errMsg.c_str() : nullptr,
  };
  buildHeartbeatPayload(out, hb);
}

static bool moduleCommandHandler(const char* cmd, const char* payload, void* user) {
  auto* module = static_cast<MaglockController*>(user);
  return module ? module->onCmd(cmd, payload) : false;
}

static void gameModeSubscription(NodeContext& ctx, const char* /*topic*/, const String& payload, void* user) {
  (void)ctx;
  auto* module = static_cast<MaglockController*>(user);
  if (module) module->onGameModeMessage(payload);
}

static void knockingSubscription(NodeContext& ctx, const char* /*topic*/, const String& payload, void* user) {
  (void)ctx;
  auto* module = static_cast<MaglockController*>(user);
  if (module) module->onKnockingEvent(payload);
}

static void lockCommandSubscription(NodeContext& ctx, const char* topic, const String& payload, void* user) {
  (void)ctx;
  auto* module = static_cast<MaglockController*>(user);
  if (module) module->onLockCommandTopic(topic, payload);
}

static void publishOtaStatus(const char* st, const String& dataJson, bool retained) {
  if (!st) return;
  NodeContext& ctx = nodeCore.context();
  const auto& topics = ctx.config().topics;
  if (topics.ota.length() == 0) return;
  const char* fw = ctx.fwVersion() ? ctx.fwVersion() : FW_VERSION;
  String payload;
  payload.reserve(96 + dataJson.length());
  payload = String("{\"fw\":\"") + fw + "\",\"up\":" + String(ctx.uptimeSeconds()) +
            ",\"st\":\"" + st + "\",\"d\":" + dataJson + "}";
  ctx.publish(topics.ota.c_str(), payload, retained);
}

// ======================= ARDUINO LIFECYCLE ===================
void setup() {
  NodeCoreConfig cfg;
  cfg.nodeId = "maglock";
  cfg.fwVersion = FW_VERSION;
  cfg.fwDescription = FW_DESC;
  cfg.buildId = FW_BUILD_ID;
  cfg.startEnabled = true;

  std::memcpy(cfg.net.mac, MAC_ADDR, sizeof(MAC_ADDR));
  cfg.net.ip = NET_IP;
  cfg.net.dns = NET_DNS;
  cfg.net.gateway = NET_GW;
  cfg.net.subnet = NET_SUBNET;
  cfg.net.mqttServer = MQTT_SERVER;
  cfg.net.mqttPort = MQTT_PORT;
  cfg.net.clientId = "maglock";

  cfg.topics = makeTopicConfig(cfg.nodeId);
  cfg.log.format = LogFormat::FwUptimeLevelMsg;
  cfg.log.filter = logFilter;
  cfg.log.filterUser = &maglock;
  cfg.heartbeat.intervalMs = maglock.currentHeartbeatIntervalMs();
  cfg.heartbeat.builder = heartbeatBuilder;
  cfg.heartbeat.user = &maglock;

  cfg.commands.cmdLogLevel = nullptr;
  cfg.commands.levelEnable = "INF";
  cfg.commands.levelDisable = "INF";
  cfg.commands.levelPing = "DBG";
  cfg.commands.logPing = true;
  cfg.commands.allowReboot = true;
  cfg.commands.levelReboot = "INF";
  cfg.commands.logUnknown = false;
  cfg.commands.logUpdate = false;

  cfg.ota.host = OTA_HOST;
  cfg.ota.port = OTA_PORT;
  cfg.ota.path = OTA_PATH;
  cfg.ota.allowedHost = OTA_ALLOWED_HOST;
  cfg.ota.allowedPathPrefix = OTA_PATH_PREFIX;
  cfg.ota.infoLevel = "INF";
  cfg.ota.errLevel = "ERR";
  cfg.ota.statusPublisher = publishOtaStatus;

  nodeCore.begin(cfg);
  nodeCore.registerCommandHandler(moduleCommandHandler, &maglock);
  nodeCore.registerSubscription(TOPIC_GAME, gameModeSubscription, &maglock);
  nodeCore.registerSubscription(TOPIC_KNOCK_EVENT, knockingSubscription, &maglock);
  nodeCore.registerSubscription(TOPIC_LOCK_CMD, lockCommandSubscription, &maglock);

  NodeContext& ctx = nodeCore.context();
  maglock.begin(ctx);
  ctx.log("INF", String("BOOT FW=") + FW_DESC);
}

void loop() {
  nodeCore.loop();
  maglock.tick(millis());
}
