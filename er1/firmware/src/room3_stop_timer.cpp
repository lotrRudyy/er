#include <Arduino.h>
#include <IPAddress.h>
#include <cstring>

#include "core_node.h"
#include "stop_timer_riddle.h"

using namespace Core;

// ======================= FIRMWARE INFO =======================
static const char* FW_VERSION = "1.3";
static const char* FW_DESC = "stop_timer 1.3 - core shell + DFPlayer knock module";

// ======================= NETWORK CONFIG ======================
static const uint8_t MAC_ADDR[6] = {0xDE, 0xAD, 0xBE, 0xEF, 0xFE, 0x56};
static const IPAddress NET_IP(192, 168, 0, 14);
static const IPAddress NET_DNS(0, 0, 0, 0);
static const IPAddress NET_GW(0, 0, 0, 0);
static const IPAddress NET_SUBNET(255, 255, 255, 0);
static const IPAddress MQTT_SERVER(192, 168, 0, 10);
static constexpr uint16_t MQTT_PORT = 1883;

// ======================= TOPICS ==============================
static const char* TOPIC_HB = "er1/room3/knocking/hb";
static const char* TOPIC_CMD = "er1/room3/knocking/cmd";
static const char* TOPIC_LOG = "er1/room3/knocking/log";
static const char* TOPIC_OTA = "er1/room3/knocking/ota";

// ======================= OTA CONFIG ==========================
static const char* OTA_HOST = "192.168.0.10";
static constexpr uint16_t OTA_PORT = 80;
static const char* OTA_PATH = "/firmware/knocking.bin";

// ======================= CORE + MODULE =======================
static NodeCore nodeCore;
static StopTimerRiddle stopTimer;

static bool logFilter(const char* level, void* user) {
  auto* module = static_cast<StopTimerRiddle*>(user);
  return module ? module->shouldAllowLog(level) : true;
}

static void heartbeatBuilder(String& out, const NodeContext& ctx, void* user) {
  auto* module = static_cast<StopTimerRiddle*>(user);
  bool dfOk = module ? module->dfReady() : false;
  uint32_t err = module ? module->errorCount() : 0;
  const char* st = (!ctx.enabled() || !dfOk || err > 0) ? "warn" : "ok";
  out = String("{\"fw\":\"") + ctx.fwVersion() +
        "\",\"up\":" + String(ctx.uptimeSeconds()) +
        ",\"st\":\"" + st + "\",\"err\":" + String(err) +
        "}";
}

static bool moduleCommandHandler(const char* cmd, const char* payload, void* user) {
  auto* module = static_cast<StopTimerRiddle*>(user);
  return module ? module->onCmd(cmd, payload) : false;
}

static void publishOtaStatus(const char* st, const String& dataJson, bool retained) {
  if (!TOPIC_OTA || !st) return;
  NodeContext& ctx = nodeCore.context();
  const char* fw = ctx.fwVersion() ? ctx.fwVersion() : FW_VERSION;
  String payload;
  payload.reserve(96 + dataJson.length());
  payload = String("{\"fw\":\"") + fw +
            "\",\"up\":" + String(ctx.uptimeSeconds()) +
            ",\"st\":\"" + st + "\",\"d\":" + dataJson + "}";
  ctx.publish(TOPIC_OTA, payload, retained);
}

// ======================= ARDUINO LIFECYCLE ===================
void setup() {
  NodeCoreConfig cfg;
  cfg.fwVersion = FW_VERSION;
  cfg.fwDescription = FW_DESC;
  cfg.startEnabled = true;
  cfg.prefsNamespace = "stop_timer";

  std::memcpy(cfg.net.mac, MAC_ADDR, sizeof(MAC_ADDR));
  cfg.net.ip = NET_IP;
  cfg.net.dns = NET_DNS;
  cfg.net.gateway = NET_GW;
  cfg.net.subnet = NET_SUBNET;
  cfg.net.mqttServer = MQTT_SERVER;
  cfg.net.mqttPort = MQTT_PORT;
  cfg.net.clientId = "knocking";
  cfg.net.topicLwt = TOPIC_HB;

  cfg.topics = {TOPIC_HB, TOPIC_CMD, TOPIC_LOG};
  cfg.log.format = LogFormat::FwUptimeLevelMsg;
  cfg.log.includeDataField = true;
  cfg.log.filter = logFilter;
  cfg.log.filterUser = &stopTimer;

  cfg.heartbeat.intervalMs = 5000;
  cfg.heartbeat.builder = heartbeatBuilder;
  cfg.heartbeat.user = &stopTimer;

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
  nodeCore.registerCommandHandler(moduleCommandHandler, &stopTimer);

  NodeContext& ctx = nodeCore.context();
  stopTimer.begin(ctx);
  ctx.log("INF", String("BOOT FW=") + FW_DESC);
}

void loop() {
  nodeCore.loop();
  stopTimer.tick(millis());
}
