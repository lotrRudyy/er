#include <Arduino.h>
#include <IPAddress.h>
#include <cstring>

#include "core_node.h"
#include "ctrl/lighting_controller.h"

using namespace Core;

namespace {

static const char* NODE_ID = "lighting";
static const char* FW_VERSION = "53";
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
static const char* TOPIC_LIGHTING_CMD = "lighting/cmd";

static const char* OTA_HOST = "192.168.0.10";
static constexpr uint16_t OTA_PORT = 80;
static const char* OTA_PATH = "/node_firmware/lighting.bin";
static const char* OTA_PATH_PREFIX = "/node_firmware/";
static const char* const OTA_ALLOWED_HOST = OTA_HOST;

static NodeCore nodeCore;
static LightingController lighting;

volatile uint32_t gLoopCounter = 0;
volatile uint32_t gLoopLastStartMs = 0;
volatile uint32_t gLoopLastEndMs = 0;
volatile uint32_t gLoopGapMaxMs = 0;
volatile uint32_t gNodeCoreStartMs = 0;
volatile uint32_t gNodeCoreEndMs = 0;
volatile uint32_t gLightingStartMs = 0;
volatile uint32_t gLightingEndMs = 0;
volatile uint32_t gStageSinceMs = 0;
const char* gStage = "boot";

void setStage(const char* stage, uint32_t nowMs) {
  gStage = stage;
  gStageSinceMs = nowMs;
}

void appendDiagFields(String& out, uint32_t nowMs) {
  if (out.endsWith("}")) out.remove(out.length() - 1);

  out += ",\"diag\":{";
  out += "\"loop_ctr\":";
  out += String((uint32_t)gLoopCounter);
  out += ",\"loop_last_start\":";
  out += String((uint32_t)gLoopLastStartMs);
  out += ",\"loop_last_end\":";
  out += String((uint32_t)gLoopLastEndMs);
  out += ",\"loop_gap_max\":";
  out += String((uint32_t)gLoopGapMaxMs);
  out += ",\"nodecore_start\":";
  out += String((uint32_t)gNodeCoreStartMs);
  out += ",\"nodecore_end\":";
  out += String((uint32_t)gNodeCoreEndMs);
  out += ",\"lighting_start\":";
  out += String((uint32_t)gLightingStartMs);
  out += ",\"lighting_end\":";
  out += String((uint32_t)gLightingEndMs);
  out += ",\"stage\":\"";
  out += escapeJson(gStage);
  out += "\"";
  out += ",\"stage_age_ms\":";
  out += String((uint32_t)(nowMs - gStageSinceMs));
  out += "}";
  out += "}";
}

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

static void lightingCommandSubscription(NodeContext& ctx, const char* topic, const String& payload, void* user) {
  (void)ctx;
  (void)topic;
  auto* module = static_cast<LightingController*>(user);
  if (module) module->onLightingCommandTopic(payload);
}

static void heartbeatBuilder(String& out, const NodeContext& ctx, void* user) {
  (void)user;
  ErrorInfo err{};
  buildHeartbeat(out, ctx, err);
  appendDiagFields(out, millis());
}

}  // namespace

void setup() {
  Serial.begin(115200);
  delay(200);

  const uint32_t nowMs = millis();
  gLoopLastStartMs = nowMs;
  gLoopLastEndMs = nowMs;
  gStageSinceMs = nowMs;
  setStage("setup_begin", nowMs);

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

  setStage("nodecore_begin", millis());
  nodeCore.begin(cfg);
  setStage("register_handlers", millis());
  nodeCore.registerCommandHandler(moduleCommandHandler, &lighting);
  nodeCore.registerSubscription(TOPIC_MOSFET_CMD, mosfetCommandSubscription, &lighting);
  nodeCore.registerSubscription(TOPIC_GAME_STATE, gameStateSubscription, &lighting);
  nodeCore.registerSubscription(TOPIC_LIGHTING_CMD, lightingCommandSubscription, &lighting);

  setStage("lighting_begin", millis());
  NodeContext& ctx = nodeCore.context();
  lighting.begin(ctx);
  ctx.log("INF", String("BOOT FW=") + FW_DESC + " rst=" + String(resetReasonShort()));
  setStage("setup_done", millis());
}

void loop() {
  const uint32_t nowMs = millis();
  ++gLoopCounter;

  const uint32_t loopGap = nowMs - (uint32_t)gLoopLastEndMs;
  if (loopGap > gLoopGapMaxMs) gLoopGapMaxMs = loopGap;
  if (loopGap > 2000) {
    nodeCore.context().log("WRN", String("lighting main loop gap ms=") + String(loopGap));
  }

  gLoopLastStartMs = nowMs;

  setStage("nodecore_loop", nowMs);
  gNodeCoreStartMs = millis();
  nodeCore.loop();
  gNodeCoreEndMs = millis();

  setStage("lighting_tick", millis());
  gLightingStartMs = millis();
  lighting.tick(millis());
  gLightingEndMs = millis();

  gLoopLastEndMs = millis();
  setStage("loop_idle", gLoopLastEndMs);
}
