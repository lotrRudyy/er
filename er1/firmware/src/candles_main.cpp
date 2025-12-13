#include <Arduino.h>
#include <IPAddress.h>
#include <cstring>

#include "core_node.h"
#include "riddles/candles_riddle.h"

using namespace Core;

// ======================= FIRMWARE INFO =======================
static const char* NODE_ID = "candles";
static const char* FW_VERSION = "1.3";
static const char* FW_DESC = "candles 1.3 - core shell + module (sequence/lighting identical)";
static const char* BUILD_ID = __DATE__ " " __TIME__;

// ======================= NETWORK CONFIG ======================
static const uint8_t MAC_ADDR[6] = {0xDE, 0xAD, 0xBE, 0xEF, 0xFE, 0x58};
static const IPAddress NET_IP(192, 168, 0, 15);
static const IPAddress NET_DNS(0, 0, 0, 0);
static const IPAddress NET_GW(0, 0, 0, 0);
static const IPAddress NET_SUBNET(255, 255, 255, 0);
static const IPAddress MQTT_SERVER(192, 168, 0, 10);
static constexpr uint16_t MQTT_PORT = 1883;

// ======================= OTA CONFIG ==========================
static const char* OTA_HOST = "192.168.0.10";
static constexpr uint16_t OTA_PORT = 80;
static const char* OTA_PATH = "/firmware/candles.bin";

// ======================= CORE + MODULE =======================
static NodeCore nodeCore;
static CandlesRiddle candles;

static bool logFilter(const char* level, void* user) {
  auto* module = static_cast<CandlesRiddle*>(user);
  return module ? module->shouldAllowLog(level) : true;
}

static void heartbeatBuilder(String& out, const NodeContext& ctx, void* user) {
  auto* module = static_cast<CandlesRiddle*>(user);
  uint32_t err = module ? module->errorCount() : 0;
  const char* health = (!ctx.enabled() || err > 0) ? "degraded" : "ok";
  HeartbeatFields hb{
      ctx.nodeId(),
      ctx.fwVersion(),
      ctx.buildId(),
      ctx.uptimeSeconds(),
      health,
      "ok",
      err > 0 ? "err" : "0",
  };
  buildHeartbeatPayload(out, hb);
}

static bool moduleCommandHandler(const char* cmd, const char* payload, void* user) {
  auto* module = static_cast<CandlesRiddle*>(user);
  return module ? module->onCmd(cmd, payload) : false;
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
  analogReadResolution(12);

  NodeCoreConfig cfg;
  cfg.nodeId = NODE_ID;
  cfg.buildId = BUILD_ID;
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
  cfg.log.format = LogFormat::LevelMsg;
  cfg.log.filter = logFilter;
  cfg.log.filterUser = &candles;

  cfg.heartbeat.intervalMs = 5000;
  cfg.heartbeat.builder = heartbeatBuilder;
  cfg.heartbeat.user = &candles;

  cfg.commands.cmdLogLevel = "DBG";
  cfg.commands.levelEnable = "INF";
  cfg.commands.levelDisable = "INF";
  cfg.commands.levelPing = "DBG";
  cfg.commands.allowReboot = true;
  cfg.commands.levelReboot = "INF";
  cfg.commands.logUnknown = true;
  cfg.commands.levelUnknown = "WRN";
  cfg.commands.logUpdate = false;

  cfg.ota.host = OTA_HOST;
  cfg.ota.port = OTA_PORT;
  cfg.ota.path = OTA_PATH;
  cfg.ota.infoLevel = "INF";
  cfg.ota.errLevel = "ERR";
  cfg.ota.statusPublisher = publishOtaStatus;

  nodeCore.begin(cfg);
  nodeCore.registerCommandHandler(moduleCommandHandler, &candles);

  NodeContext& ctx = nodeCore.context();
  candles.begin(ctx);
  ctx.log("INF", String("BOOT FW=") + FW_DESC);
}

void loop() {
  nodeCore.loop();
  candles.tick(millis());
}
