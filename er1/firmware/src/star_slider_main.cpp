#include <Arduino.h>
#include <IPAddress.h>
#include <cstring>

#include "core_node.h"
// build id removed (version-only identity)
#include "riddles/star_slider_riddle.h"

using namespace Core;

// ======================= FIRMWARE INFO =======================
static const char* NODE_ID = "star_slider";
static const char* FW_VERSION = "13";
static const char* FW_DESC = "star_slider with new ota";

// ======================= NETWORK CONFIG ======================
static const uint8_t MAC_ADDR[6] = {0xDE, 0xAD, 0xBE, 0xEF, 0xFE, 0x52};  // star_slider node MAC - must stay unique
static const IPAddress NET_IP(192, 168, 0, 18);
static const IPAddress NET_DNS(0, 0, 0, 0);
static const IPAddress NET_GW(0, 0, 0, 0);
static const IPAddress NET_SUBNET(255, 255, 255, 0);
static const IPAddress MQTT_SERVER(192, 168, 0, 10);
static constexpr uint16_t MQTT_PORT = 1883;

// ======================= TOPICS ==============================
static const char* TOPIC_GAME = "game/state";
static const char* TOPIC_EVT = "candles/evt";

// ======================= OTA CONFIG ==========================
static const char* OTA_HOST = "192.168.0.10";
static constexpr uint16_t OTA_PORT = 80;
static const char* OTA_PATH = "/node_firmware/star_slider.bin";
static const char* OTA_PATH_PREFIX = "/node_firmware/";
static const char* const OTA_ALLOWED_HOST = OTA_HOST;

// ======================= CORE + MODULE =======================
static NodeCore nodeCore;
static StarSliderRiddle starSlider;

static void heartbeatBuilder(String& out, const NodeContext& ctx, void* userData) {
  auto* module = static_cast<StarSliderRiddle*>(userData);
  ErrorInfo err{};
  if (module) {
    err.count = module->errorCount();
  }
  buildHeartbeat(out, ctx, err);
}

static bool moduleCommandHandler(const char* cmd, const char* payload, void* userData) {
  auto* module = static_cast<StarSliderRiddle*>(userData);
  return module ? module->onCmd(cmd, payload) : false;
}

static void gameModeSubscription(NodeContext& ctx, const char* /*topic*/, const String& payload, void* userData) {
  (void)ctx;
  auto* module = static_cast<StarSliderRiddle*>(userData);
  if (!module) return;
  String msg = payload;
  msg.trim();
  msg.toUpperCase();
  module->setGameMode(false);
}

static void eventSubscription(NodeContext& ctx, const char* /*topic*/, const String& payload, void* userData) {
  (void)ctx;
  auto* module = static_cast<StarSliderRiddle*>(userData);
  if (!module) return;
  const bool isCandlesSolved =
      (payload.indexOf("\"rid\":\"candles\"") >= 0 && payload.indexOf("\"event\":\"SOLVED\"") >= 0) ||
      (payload.indexOf("\"type\":\"riddle_solved\"") >= 0 && payload.indexOf("\"id\":\"candles\"") >= 0);
  if (!isCandlesSolved) return;
  module->setGameMode(true);
}

// ======================= ARDUINO LIFECYCLE ===================
void setup() {
  NodeCoreConfig cfg;
  cfg.nodeId = "star_slider";
  cfg.fwVersion = FW_VERSION;
  cfg.fwDescription = FW_DESC;

  cfg.startEnabled = true;
  cfg.prefsNamespace = "star_slider";

  std::memcpy(cfg.net.mac, MAC_ADDR, sizeof(MAC_ADDR));
  cfg.net.ip = NET_IP;
  cfg.net.dns = NET_DNS;
  cfg.net.gateway = NET_GW;
  cfg.net.subnet = NET_SUBNET;
  cfg.net.mqttServer = MQTT_SERVER;
  cfg.net.mqttPort = MQTT_PORT;
  cfg.net.clientId = "star_slider";

  cfg.topics = makeTopicConfig(cfg.nodeId);
  cfg.log.format = LogFormat::FwUptimeLevelMsg;
  cfg.log.includeDataField = true;

  cfg.heartbeat.intervalMs = 5000;
  cfg.heartbeat.builder = heartbeatBuilder;
  cfg.heartbeat.user = &starSlider;

  cfg.commands.levelEnable = "INF";
  cfg.commands.levelDisable = "INF";
  cfg.commands.levelReboot = "INF";
  cfg.commands.unknownPrefix = "Unknown CMD: ";
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
  nodeCore.registerCommandHandler(moduleCommandHandler, &starSlider);
  nodeCore.registerSubscription(TOPIC_GAME, gameModeSubscription, &starSlider);
  nodeCore.registerSubscription(TOPIC_EVT, eventSubscription, &starSlider);

  NodeContext& ctx = nodeCore.context();
  starSlider.begin(ctx);
  ctx.log("INF", String("BOOT FW=") + FW_DESC + " rst=" + resetReasonShort());
}

void loop() {
  nodeCore.loop();
  starSlider.tick(millis());
}
