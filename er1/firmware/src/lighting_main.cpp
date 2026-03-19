#include <Arduino.h>
#include <IPAddress.h>
#include <cstring>

#include "core_node.h"
#include "ctrl/lighting_controller.h"

using namespace Core;

static const char* NODE_ID = "lighting";
static const char* FW_VERSION = "48";
static const char* FW_DESC = "lighting controller (10x mosfet pwm incl. uv)";

static const uint8_t MAC_ADDR[6] = {0xDE, 0xAD, 0xBE, 0xEF, 0xFE, 0x54};
static const IPAddress NET_IP(192, 168, 0, 12);
static const IPAddress NET_DNS(0, 0, 0, 0);
static const IPAddress NET_GW(0, 0, 0, 0);
static const IPAddress NET_SUBNET(255, 255, 255, 0);
static const IPAddress MQTT_SERVER(192, 168, 0, 10);
static constexpr uint16_t MQTT_PORT = 1883;

static const char* TOPIC_MOSFET_CMD = "lighting/mosfet/+/cmd";
static const char* TOPIC_GAME_STATE = "game/state";

static const char* OTA_HOST = "192.168.0.10";
static constexpr uint16_t OTA_PORT = 80;
static const char* OTA_PATH = "/node_firmware/lighting.bin";
static const char* OTA_PATH_PREFIX = "/node_firmware/";
static const char* const OTA_ALLOWED_HOST = OTA_HOST;

static NodeCore nodeCore;
static LightingController lighting;

static bool moduleCommandHandler(const char* cmd, const char* payload, void* user) {
  auto* module = static_cast<LightingController*>(user);
  return module ? module->onCmd(cmd, payload) : false;
}

static void mosfetCommandSubscription(NodeContext& ctx, const char* topic, const String& payload, void* user) {
  (void)ctx;
  auto* module = static_cast<LightingController*>(user);
  if (module) module->onMosfetCommandTopic(topic, payload);
}

static void gameStateSubscription(NodeContext& ctx, const char* topic, const String& payload, void* user) {
  (void)ctx;
  (void)topic;
  auto* module = static_cast<LightingController*>(user);
  if (module) module->onGameStateMessage(payload);
}

static void heartbeatBuilder(String& out, const NodeContext& ctx, void* user) {
  (void)user;
  ErrorInfo err{};
  buildHeartbeat(out, ctx, err);
}

void setup() {
  Serial.begin(115200);
  delay(200);

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

  cfg.heartbeat.intervalMs = 5000;
  cfg.heartbeat.builder = heartbeatBuilder;
  cfg.heartbeat.user = &lighting;

  cfg.commands.allowReboot = true;
  cfg.commands.levelReboot = "INF";
  cfg.commands.logPing = false;
  cfg.commands.levelPing = "DBG";
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
  nodeCore.registerCommandHandler(moduleCommandHandler, &lighting);
  nodeCore.registerSubscription(TOPIC_MOSFET_CMD, mosfetCommandSubscription, &lighting);
  nodeCore.registerSubscription(TOPIC_GAME_STATE, gameStateSubscription, &lighting);

  NodeContext& ctx = nodeCore.context();
  lighting.begin(ctx);
  ctx.log("INF", String("BOOT FW=") + FW_DESC + " rst=" + String(resetReasonShort()));
}

void loop() {
  nodeCore.loop();
  lighting.tick(millis());
}
