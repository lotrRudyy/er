#include <Arduino.h>
#include <IPAddress.h>
#include <cstring>

#include "core_node.h"
#include "riddles/knocking_riddle.h"

using namespace Core;

// ======================= FIRMWARE INFO =======================
static const char* NODE_ID = "knocking";
static const char* FW_VERSION = "23";
static const char* FW_DESC = "embedded knock samples only";

// ======================= NETWORK CONFIG ======================
static const uint8_t MAC_ADDR[6] = {0xDE, 0xAD, 0xBE, 0xEF, 0xFE, 0x51};  // knocking node MAC - must stay unique
static const IPAddress NET_IP(192, 168, 0, 15);
static const IPAddress NET_DNS(0, 0, 0, 0);
static const IPAddress NET_GW(0, 0, 0, 0);
static const IPAddress NET_SUBNET(255, 255, 255, 0);
static const IPAddress MQTT_SERVER(192, 168, 0, 10);
static constexpr uint16_t MQTT_PORT = 1883;

// ======================= TOPICS ==============================
static const char* TOPIC_GAME = "game/state";

// ======================= OTA CONFIG ==========================
static const char* OTA_HOST = "192.168.0.10";
static constexpr uint16_t OTA_PORT = 80;
static const char* OTA_PATH = "/node_firmware/knocking.bin";
static const char* OTA_PATH_PREFIX = "/node_firmware/";
static const char* const OTA_ALLOWED_HOST = OTA_HOST;

// ======================= CORE + MODULE =======================
static NodeCore nodeCore;
static KnockingRiddle riddle;

static bool logFilter(const char* level, void* user) {
  auto* module = static_cast<KnockingRiddle*>(user);
  return module ? module->shouldAllowLog(level) : true;
}

namespace {
bool payloadHasName(const String& payload, const char* key, const char* value) {
  if (!value || !value[0]) return false;
  const String quoted = String(""") + key + "":"" + value + """;
  if (payload.indexOf(quoted) >= 0) return true;
  const String arr = String(""") + key + "":[";
  int pos = payload.indexOf(arr);
  if (pos < 0) return false;
  String needle = String(""") + value + """;
  int end = payload.indexOf("]", pos);
  if (end < 0) end = payload.length();
  return payload.indexOf(needle, pos) >= 0 && payload.indexOf(needle, pos) < end;
}
String detectMode(const String& payload) {
  String upper = payload;
  upper.trim();
  upper.toUpperCase();
  if (upper == "MODE_MAINTENANCE" || upper.indexOf(""MODE":"MODE_MAINTENANCE"") >= 0) return "MODE_MAINTENANCE";
  if (upper == "MODE_PREPARE" || upper.indexOf(""MODE":"MODE_PREPARE"") >= 0) return "MODE_PREPARE";
  if (upper == "MODE_INGAME" || upper.indexOf(""MODE":"MODE_INGAME"") >= 0) return "MODE_INGAME";
  return "MODE_STANDBY";
}
bool shouldNodeBeActive(const String& payload, const char* nodeName) {
  const String mode = detectMode(payload);
  if (mode == "MODE_MAINTENANCE") return true;
  if (mode != "MODE_INGAME") return false;
  return payloadHasName(payload, "active", nodeName) || payloadHasName(payload, "solved", nodeName);
}
}

static void heartbeatBuilder(String& out, const NodeContext& ctx, void* user) {
  auto* module = static_cast<KnockingRiddle*>(user);
  ErrorInfo err{};
  if (module) {
    err.count = module->errorCount();
  }
  buildHeartbeat(out, ctx, err);
}

static void gameModeSubscription(NodeContext& ctx, const char* /*topic*/, const String& payload, void* user) {
  (void)ctx;
  auto* module = static_cast<KnockingRiddle*>(user);
  if (!module) return;
  module->setGameMode(shouldNodeBeActive(payload, "knocking"));
}

static bool moduleCommandHandler(const char* cmd, const char* payload, void* user) {
  auto* module = static_cast<KnockingRiddle*>(user);
  return module ? module->onCmd(cmd, payload) : false;
}



// ======================= ARDUINO LIFECYCLE ===================
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
