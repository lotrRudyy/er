#include <Arduino.h>
#include <IPAddress.h>
#include <cstring>

#include "core_node.h"
#include "ctrl/lighting_controller.h"

using namespace Core;

// ======================= FIRMWARE INFO =======================
static const char* NODE_ID = "lighting";
static const char* FW_VERSION = "2";
static const char* FW_DESC = "lighting controller (9x mosfet pwm)";

// ======================= NETWORK CONFIG ======================
static const uint8_t MAC_ADDR[6] = {0xDE, 0xAD, 0xBE, 0xEF, 0xFE, 0x54};  // must stay unique
static const IPAddress NET_IP(192, 168, 0, 12);
static const IPAddress NET_DNS(0, 0, 0, 0);
static const IPAddress NET_GW(0, 0, 0, 0);
static const IPAddress NET_SUBNET(255, 255, 255, 0);
static const IPAddress MQTT_SERVER(192, 168, 0, 10);
static constexpr uint16_t MQTT_PORT = 1883;

// ======================= TOPICS ==============================
static const char* TOPIC_MOSFET_CMD = "lighting/mosfet/+/cmd";

// ======================= OTA CONFIG ==========================
static const char* OTA_HOST = "192.168.0.10";
static constexpr uint16_t OTA_PORT = 80;
static const char* OTA_PATH = "/node_firmware/lighting.bin";
static const char* OTA_PATH_PREFIX = "/node_firmware/";
static const char* const OTA_ALLOWED_HOST = OTA_HOST;

// ======================= SERIAL DEBUG =========================
#ifndef LIGHTING_SERIAL_DEBUG
#define LIGHTING_SERIAL_DEBUG 1
#endif

#if LIGHTING_SERIAL_DEBUG
  #define SDBG(fmt, ...) do { Serial.printf("[lighting] " fmt "\n", ##__VA_ARGS__); } while(0)
#else
  #define SDBG(fmt, ...) do {} while(0)
#endif

static void printIp(const char* label, const IPAddress& ip) {
#if LIGHTING_SERIAL_DEBUG
  Serial.printf("[lighting] %s %u.%u.%u.%u\n", label, ip[0], ip[1], ip[2], ip[3]);
#else
  (void)label; (void)ip;
#endif
}

static void printMac(const uint8_t mac[6]) {
#if LIGHTING_SERIAL_DEBUG
  Serial.printf("[lighting] MAC %02X:%02X:%02X:%02X:%02X:%02X\n",
                mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
#else
  (void)mac;
#endif
}

// ======================= CORE + MODULE =======================
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

// ✅ Heartbeat builder REQUIRED, otherwise hb stays "offline" (LWT only)
static void heartbeatBuilder(String& out, const NodeContext& ctx, void* /*user*/) {
  // If you later expose error count from LightingController, set err.count here.
  ErrorInfo err{};
  buildHeartbeat(out, ctx, err);
}

// ======================= ARDUINO LIFECYCLE ===================
void setup() {
#if LIGHTING_SERIAL_DEBUG
  Serial.begin(115200);
  delay(200);
  Serial.println();
  SDBG("BOOT setup()");
  SDBG("FW v%s - %s", FW_VERSION, FW_DESC);
  SDBG("reset=%s", resetReasonShort());
  printMac(MAC_ADDR);
  printIp("IP     ", NET_IP);
  printIp("SUBNET ", NET_SUBNET);
  printIp("GW     ", NET_GW);
  printIp("DNS    ", NET_DNS);
  printIp("MQTT   ", MQTT_SERVER);
  SDBG("MQTT port %u", (unsigned)MQTT_PORT);
  SDBG("Sub topic: %s", TOPIC_MOSFET_CMD);
#endif

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

  // ✅ ENABLE HEARTBEAT (this makes lighting/hb overwrite the retained "offline")
  cfg.heartbeat.intervalMs = 20000;
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

#if LIGHTING_SERIAL_DEBUG
  SDBG("nodeCore.begin()");
#endif
  nodeCore.begin(cfg);

#if LIGHTING_SERIAL_DEBUG
  SDBG("registerCommandHandler()");
#endif
  nodeCore.registerCommandHandler(moduleCommandHandler, &lighting);

#if LIGHTING_SERIAL_DEBUG
  SDBG("registerSubscription(%s)", TOPIC_MOSFET_CMD);
#endif
  nodeCore.registerSubscription(TOPIC_MOSFET_CMD, mosfetCommandSubscription, &lighting);

  NodeContext& ctx = nodeCore.context();

#if LIGHTING_SERIAL_DEBUG
  SDBG("lighting.begin()");
#endif
  lighting.begin(ctx);

  ctx.log("INF", String("BOOT FW=") + FW_DESC + " rst=" + String(resetReasonShort()));

#if LIGHTING_SERIAL_DEBUG
  SDBG("setup() done");
#endif
}

void loop() {
  nodeCore.loop();
  lighting.tick(millis());
}
