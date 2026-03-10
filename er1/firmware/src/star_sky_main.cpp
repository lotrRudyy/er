#include <Arduino.h>
#include <IPAddress.h>
#include <cstring>

#include "core_node.h"
// build id removed (version-only identity)
#include "riddles/star_sky_riddle.h"

using namespace Core;

// ======================= FIRMWARE INFO =======================
static const char* NODE_ID = "star_sky";
static const char* FW_VERSION = "2";
static const char* FW_DESC = "star_sky with new ota";

// ======================= NETWORK CONFIG ======================
static const uint8_t MAC_ADDR[6] = {0xDE, 0xAD, 0xBE, 0xEF, 0xFE, 0x55};  // star_sky node MAC - must stay unique
static const IPAddress NET_IP(192, 168, 0, 17);
static const IPAddress NET_DNS(0, 0, 0, 0);
static const IPAddress NET_GW(0, 0, 0, 0);
static const IPAddress NET_SUBNET(255, 255, 255, 0);
static const IPAddress MQTT_SERVER(192, 168, 0, 10);
static constexpr uint16_t MQTT_PORT = 1883;

// ======================= TOPICS ==============================
static String TOPIC_CANDLES_EVENT = Core::topic("candles", "evt");

// ======================= TOPICS ==============================
static const char* TOPIC_GAME = "game/state";

// ======================= OTA CONFIG ==========================
static const char* OTA_HOST = "192.168.0.10";
static constexpr uint16_t OTA_PORT = 80;
static const char* OTA_PATH = "/node_firmware/star_sky.bin";
static const char* OTA_PATH_PREFIX = "/node_firmware/";
static const char* const OTA_ALLOWED_HOST = OTA_HOST;

// ======================= CORE + MODULE =======================
static NodeCore nodeCore;
static StarSkyRiddle starSky;

static bool logFilter(const char* level, void* user) {
  auto* module = static_cast<StarSkyRiddle*>(user);
  if (!module) return true;
  bool isErr = (strcmp(level, "ERR") == 0);
  if (isErr) return true;
  return true;  // star_sky logs everything but still tracks errors via module if needed
}

static void heartbeatBuilder(String& out, const NodeContext& ctx, void* user) {
  auto* module = static_cast<StarSkyRiddle*>(user);
  ErrorInfo err{};
  if (module) {
    err.count = module->errorCount();
  }
  buildHeartbeat(out, ctx, err);
}

static bool moduleCommandHandler(const char* cmd, const char* payload, void* user) {
  auto* module = static_cast<StarSkyRiddle*>(user);
  return module ? module->onCmd(cmd, payload) : false;
}

static void gameModeSubscription(NodeContext& ctx, const char* /*topic*/, const String& payload, void* user) {
  (void)ctx;
  auto* module = static_cast<StarSkyRiddle*>(user);
  if (!module) return;
  String msg = payload;
  msg.trim();
  msg.toUpperCase();
  module->setGameMode(false);
}

static void candlesEventSubscription(NodeContext& ctx, const char* /*topic*/, const String& payload, void* user) {
  (void)ctx;
  auto* module = static_cast<StarSkyRiddle*>(user);
  if (!module) return;
  module->setGameMode(true);
  module->handleCandlesEvent(payload);
}

// ======================= ARDUINO LIFECYCLE ===================
void setup() {
  NodeCoreConfig cfg;
  cfg.nodeId = NODE_ID;
  cfg.fwVersion = FW_VERSION;
  cfg.fwDescription = FW_DESC;

  cfg.startEnabled = true;
  cfg.prefsNamespace = "star_sky";

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
  cfg.log.filterUser = &starSky;

  cfg.heartbeat.intervalMs = 20000;
  cfg.heartbeat.builder = heartbeatBuilder;
  cfg.heartbeat.user = &starSky;

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
  nodeCore.registerCommandHandler(moduleCommandHandler, &starSky);
  nodeCore.registerSubscription(TOPIC_GAME, gameModeSubscription, &starSky);
  nodeCore.registerSubscription(TOPIC_CANDLES_EVENT.c_str(), candlesEventSubscription, &starSky);

  NodeContext& ctx = nodeCore.context();
  starSky.begin(ctx);
  ctx.log("INF", String("BOOT FW=") + FW_DESC + " rst=" + resetReasonShort());
}

void loop() {
  nodeCore.loop();
  starSky.tick(millis());
}
