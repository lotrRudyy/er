#include <Arduino.h>
#include <IPAddress.h>
#include <cstring>

#include "core_node.h"
#include "ctrl/lighting_controller.h"

using namespace Core;

static const char* NODE_ID = "lighting";
static const char* FW_VERSION = "59";
static const char* FW_DESC = "lighting controller (10x mosfet pwm incl. uv)";

static const uint8_t MAC_ADDR[6] = {0xDE, 0xAD, 0xBE, 0xEF, 0xFE, 0x54};
static const IPAddress NET_IP(192, 168, 0, 12);
static const IPAddress NET_DNS(0, 0, 0, 0);
static const IPAddress NET_GW(0, 0, 0, 0);
static const IPAddress NET_SUBNET(255, 255, 255, 0);
static const IPAddress MQTT_SERVER(192, 168, 0, 10);
static constexpr uint16_t MQTT_PORT = 1883;

static const char* TOPIC_GAME_STATE = "game/state";
static const char* TOPIC_LIGHTING_CMD = "lighting/cmd";

static const char* OTA_HOST = "192.168.0.10";
static constexpr uint16_t OTA_PORT = 80;
static const char* OTA_PATH = "/node_firmware/lighting.bin";
static const char* OTA_PATH_PREFIX = "/node_firmware/";
static const char* const OTA_ALLOWED_HOST = OTA_HOST;

static NodeCore nodeCore;
static LightingController lighting;

namespace {
enum class LoopStage : uint8_t {
  Boot = 0,
  BeforeNodeCore,
  AfterNodeCore,
  BeforeLightingTick,
  AfterLightingTick,
  Idle,
};

volatile uint32_t gLoopCounter = 0;
volatile uint32_t gLastLoopStartMs = 0;
volatile uint32_t gLastLoopEndMs = 0;
volatile uint32_t gMaxLoopGapMs = 0;
volatile uint32_t gLastNodeCoreStartMs = 0;
volatile uint32_t gLastNodeCoreEndMs = 0;
volatile uint32_t gLastLightingStartMs = 0;
volatile uint32_t gLastLightingEndMs = 0;
volatile uint32_t gLastStageChangeMs = 0;
volatile LoopStage gLoopStage = LoopStage::Boot;
uint32_t gLastGapWarnMs = 0;

const char* loopStageName(LoopStage s) {
  switch (s) {
    case LoopStage::Boot: return "boot";
    case LoopStage::BeforeNodeCore: return "before_nodecore";
    case LoopStage::AfterNodeCore: return "after_nodecore";
    case LoopStage::BeforeLightingTick: return "before_lighting_tick";
    case LoopStage::AfterLightingTick: return "after_lighting_tick";
    case LoopStage::Idle: return "idle";
    default: return "unknown";
  }
}

void setLoopStage(LoopStage s, uint32_t nowMs) {
  gLoopStage = s;
  gLastStageChangeMs = nowMs;
}

void appendDiagFields(String& out, uint32_t nowMs) {
  if (!out.endsWith("}")) return;
  out.remove(out.length() - 1);
  out += ",\"diag\":{"n         "\"loop_ctr\":" + String((uint32_t)gLoopCounter) +
         ",\"loop_last_start\":" + String((uint32_t)gLastLoopStartMs) +
         ",\"loop_last_end\":" + String((uint32_t)gLastLoopEndMs) +
         ",\"loop_gap_max\":" + String((uint32_t)gMaxLoopGapMs) +
         ",\"nodecore_start\":" + String((uint32_t)gLastNodeCoreStartMs) +
         ",\"nodecore_end\":" + String((uint32_t)gLastNodeCoreEndMs) +
         ",\"lighting_start\":" + String((uint32_t)gLastLightingStartMs) +
         ",\"lighting_end\":" + String((uint32_t)gLastLightingEndMs) +
         ",\"stage\":\"" + String(loopStageName(gLoopStage)) + "\"" +
         ",\"stage_age_ms\":" + String((uint32_t)(nowMs - gLastStageChangeMs)) +
         "}}";
}
} // namespace

static bool moduleCommandHandler(const char* cmd, const char* payload, void* user) {
  auto* module = static_cast<LightingController*>(user);
  return module ? module->onCmd(cmd, payload) : false;
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

void setup() {
  Serial.begin(115200);
  delay(200);

  const uint32_t nowMs = millis();
  gLastLoopStartMs = nowMs;
  gLastLoopEndMs = nowMs;
  gLastStageChangeMs = nowMs;
  setLoopStage(LoopStage::Boot, nowMs);

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
  nodeCore.registerSubscription(TOPIC_GAME_STATE, gameStateSubscription, &lighting);
  nodeCore.registerSubscription(TOPIC_LIGHTING_CMD, lightingCommandSubscription, &lighting);

  NodeContext& ctx = nodeCore.context();
  lighting.begin(ctx);
  ctx.log("INF", String("BOOT FW=") + FW_DESC + " rst=" + String(resetReasonShort()));
}

void loop() {
  const uint32_t nowMs = millis();
  const uint32_t gapMs = (gLastLoopStartMs == 0) ? 0 : (uint32_t)(nowMs - gLastLoopStartMs);
  gLastLoopStartMs = nowMs;
  gLoopCounter++;
  if (gapMs > gMaxLoopGapMs) gMaxLoopGapMs = gapMs;

  if (gapMs > 1500 && (uint32_t)(nowMs - gLastGapWarnMs) > 1000) {
    gLastGapWarnMs = nowMs;
    Serial.printf("[lighting][WRN] LOOP_GAP gap=%lu stage=%s stage_age=%lu ctr=%lu\n",
                  (unsigned long)gapMs,
                  loopStageName(gLoopStage),
                  (unsigned long)(nowMs - gLastStageChangeMs),
                  (unsigned long)gLoopCounter);
    NodeContext& ctx = nodeCore.context();
    String data = String("{\"gap_ms\":") + String(gapMs) +
                  ",\"stage\":\"" + loopStageName(gLoopStage) + "\"" +
                  ",\"stage_age_ms\":" + String((uint32_t)(nowMs - gLastStageChangeMs)) +
                  ",\"loop_ctr\":" + String((uint32_t)gLoopCounter) +
                  "}";
    ctx.log("WRN", "lighting loop gap", data);
  }

  setLoopStage(LoopStage::BeforeNodeCore, nowMs);
  gLastNodeCoreStartMs = nowMs;
  nodeCore.loop();
  gLastNodeCoreEndMs = millis();

  const uint32_t afterNodeMs = millis();
  setLoopStage(LoopStage::BeforeLightingTick, afterNodeMs);
  gLastLightingStartMs = afterNodeMs;
  lighting.tick(afterNodeMs);
  gLastLightingEndMs = millis();
  setLoopStage(LoopStage::Idle, gLastLightingEndMs);
  gLastLoopEndMs = gLastLightingEndMs;
}
