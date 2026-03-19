#include <Arduino.h>
#include <IPAddress.h>
#include <cstring>

#include "core_node.h"
#include "riddles/knocking_riddle.h"

using namespace Core;

static const char* NODE_ID = "knocking";
static const char* FW_VERSION = "23";
static const char* FW_DESC = "embedded knock samples only";

static const uint8_t MAC_ADDR[6] = {0xDE, 0xAD, 0xBE, 0xEF, 0xFE, 0x51};
static const IPAddress NET_IP(192, 168, 0, 15);
static const IPAddress NET_DNS(0, 0, 0, 0);
static const IPAddress NET_GW(0, 0, 0, 0);
static const IPAddress NET_SUBNET(255, 255, 255, 0);
static const IPAddress MQTT_SERVER(192, 168, 0, 10);
static constexpr uint16_t MQTT_PORT = 1883;

static const char* TOPIC_GAME = "game/state";

static const char* OTA_HOST = "192.168.0.10";
static constexpr uint16_t OTA_PORT = 80;
static const char* OTA_PATH = "/node_firmware/knocking.bin";
static const char* OTA_PATH_PREFIX = "/node_firmware/";
static const char* const OTA_ALLOWED_HOST = OTA_HOST;

static NodeCore nodeCore;
static KnockingRiddle riddle;
static bool s_gameEnabled = false;

static bool jsonArrayContains(const String& payload, const char* key, const char* value) {
  String marker = String("\"") + key + "\":[";
  int start = payload.indexOf(marker);
  if (start < 0) return false;
  start += marker.length();
  int end = payload.indexOf(']', start);
  if (end < 0) return false;
  String match = String("\"") + value + "\"";
  return payload.substring(start, end).indexOf(match) >= 0;
}

static bool computeEnabledFromGameState(const String& payload, const char* nodeName) {
  if (payload.indexOf("\"mode\":\"MODE_MAINTENANCE\"") >= 0) return true;
  if (payload.indexOf("\"mode\":\"MODE_INGAME\"") < 0) return false;
  return jsonArrayContains(payload, "active", nodeName) || jsonArrayContains(payload, "solved", nodeName);
}

static void applyEnabled(KnockingRiddle* module, bool enabled) {
  if (!module) return;
  if (enabled == s_gameEnabled) return;
  s_gameEnabled = enabled;
  module->setGameMode(enabled);
}

static bool logFilter(const char* level, void* user) {
  auto* module = static_cast<KnockingRiddle*>(user);
  return module ? module->shouldAllowLog(level) : true;
}

static void heartbeatBuilder(String& out, const NodeContext& ctx, void* user) {
  auto* module = static_cast<KnockingRiddle*>(user);
  ErrorInfo err{};
  if (module) err.count = module->errorCount();
  buildHeartbeat(out, ctx, err);
}

static bool moduleCommandHandler(const char* cmd, const char* payload, void* user) {
  auto* module = static_cast<KnockingRiddle*>(user);
  return module ? module->onCmd(cmd, payload) : false;
}

static void gameModeSubscription(NodeContext& ctx, const char* /*topic*/, const String& payload, void* user) {
  (void)ctx;
  applyEnabled(static_cast<KnockingRiddle*>(user), computeEnabledFromGameState(payload, NODE_ID));
}

void setup() {
  Serial.begin(115200);
  delay(200);
  Serial.println("[knocking][INF] SERIAL_READY");

  NodeCoreConfig cfg;
  cfg.nodeId = NODE_ID;
  cfg.fwVersion = FW_VERSION;
  cfg.fwDescription = FW_DESC;
  cfg.startEnabled = true;

  std::memcpy(cfg.net.mac, MAC_ADDR, sizeof(MAC_ADDR));
  cfg.net.ip = NET_IP;
  cfg.net.dns = NET_DNS;
  cfg.net.gateway = NET_GW;
  cfg.net.subnet = NET_SUBNET;
  cfg.net.mqttServer = MQTT_SERVER;
  cfg.net.mqttPort = MQTT_PORT;
  cfg.net.clientId = NODE_ID;

  cfg.topics = makeTopicConfig(cfg.nodeId);
  cfg.log.format = LogFormat::FwUptimeLevelMsg;
  cfg.log.includeDataField = true;
  cfg.log.filter = logFilter;
  cfg.log.filterUser = &riddle;

  cfg.heartbeat.intervalMs = 5000;
  cfg.heartbeat.builder = heartbeatBuilder;
  cfg.heartbeat.user = &riddle;

  cfg.commands.levelEnable = "INF";
  cfg.commands.levelDisable = "INF";
  cfg.commands.levelReboot = "INF";
  cfg.commands.allowReboot = true;
  cfg.commands.logPing = false;
  cfg.commands.levelPing = "DBG";
  cfg.commands.logUnknown = true;
  cfg.commands.levelUnknown = "WRN";
  cfg.commands.logUpdate = false;
  cfg.commands.levelUpdate = "INF";
  cfg.commands.cmdLogLevel = "DBG";

  cfg.ota.host = OTA_HOST;
  cfg.ota.port = OTA_PORT;
  cfg.ota.path = OTA_PATH;
  cfg.ota.allowedHost = OTA_ALLOWED_HOST;
  cfg.ota.allowedPathPrefix = OTA_PATH_PREFIX;
  cfg.ota.infoLevel = "INF";
  cfg.ota.errLevel = "ERR";

  nodeCore.begin(cfg);
  nodeCore.registerCommandHandler(moduleCommandHandler, &riddle);
  nodeCore.registerSubscription(TOPIC_GAME, gameModeSubscription, &riddle);

  NodeContext& ctx = nodeCore.context();
  riddle.begin(ctx);
  ctx.log("INF", String("BOOT FW=") + FW_DESC + " rst=" + resetReasonShort());
}

void loop() {
  nodeCore.loop();
  riddle.tick(millis());
}
