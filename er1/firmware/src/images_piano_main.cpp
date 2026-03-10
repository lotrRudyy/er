#include <Arduino.h>
#include <IPAddress.h>
#include <cstring>

#include "core_node.h"
// build id removed (version-only identity)
#include "riddles/images_riddle.h"
#include "riddles/piano_riddle.h"

using namespace Core;

// ======================= FIRMWARE INFO =======================
static const char* NODE_ID = "images_piano";
static const char* NODE_IMAGES = "images";
static const char* NODE_PIANO = "piano";
static const char* FW_VERSION = "3";
static const char* FW_DESC = "images_piano with new ota changed piano password";

// ======================= NETWORK CONFIG ======================
static const uint8_t MAC_ADDR[6] = {0xDE, 0xAD, 0xBE, 0xEF, 0xFE, 0x59};  // images_piano node MAC - must stay unique
static const IPAddress NET_IP(192, 168, 0, 13);
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
static const char* OTA_PATH = "/node_firmware/images_piano.bin";
static const char* OTA_PATH_PREFIX = "/node_firmware/";
static const char* const OTA_ALLOWED_HOST = OTA_HOST;

// ======================= MODULES =============================
static NodeCore nodeCore;
static ImagesRiddle imagesModule;
static PianoRiddle pianoRiddle;

// ======================= HELPERS =============================
static void heartbeatBuilder(String& out, const NodeContext& ctx, void* /*userData*/) {
  ErrorInfo err = ctx.errorInfo();
  buildHeartbeat(out, ctx, err);
}

static bool moduleCommandHandler(const char* cmd, const char* payload, void* userData) {
  (void)userData;
  bool handled = false;
  handled |= imagesModule.onCmd(cmd, payload);
  handled |= pianoRiddle.onCmd(cmd, payload);
  return handled;
}

static void gameModeSubscription(NodeContext& ctx, const char* /*topic*/, const String& payload, void* /*userData*/) {
  (void)ctx;
  String msg = payload;
  msg.trim();
  msg.toUpperCase();
  const bool inGame = (msg == "INGAME");
  imagesModule.setGameMode(inGame);
  pianoRiddle.setGameMode(inGame);
}

// ======================= ARDUINO LIFECYCLE ===================
void setup() {
  NodeCoreConfig cfg;
  cfg.nodeId = NODE_ID;
  cfg.fwVersion = FW_VERSION;
  cfg.fwDescription = FW_DESC;

  cfg.startEnabled = true;
  cfg.prefsNamespace = NODE_ID;

  std::memcpy(cfg.net.mac, MAC_ADDR, sizeof(MAC_ADDR));
  cfg.net.ip = NET_IP;
  cfg.net.dns = NET_DNS;
  cfg.net.gateway = NET_GW;
  cfg.net.subnet = NET_SUBNET;
  cfg.net.mqttServer = MQTT_SERVER;
  cfg.net.mqttPort = MQTT_PORT;
  cfg.net.clientId = NODE_ID;
  cfg.net.ethRstPin = 16;  // images_piano only: Ethernet reset on GPIO21

  cfg.topics = makeTopicConfig(cfg.nodeId);
  cfg.log.format = LogFormat::FwUptimeLevelMsg;
  cfg.log.includeDataField = true;
  cfg.heartbeat.intervalMs = 20000;
  cfg.heartbeat.builder = heartbeatBuilder;
  cfg.commands.levelEnable = "INF";
  cfg.commands.levelDisable = "INF";
  cfg.commands.allowReboot = true;
  cfg.commands.logPing = false;  // legacy: no log on ping
  cfg.commands.levelPing = "DBG";
  cfg.commands.logUnknown = true;
  cfg.commands.levelUnknown = "WRN";
  cfg.commands.logUpdate = false;  // OTA logs handled inside updater
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
  nodeCore.registerCommandHandler(moduleCommandHandler, nullptr);
  nodeCore.registerSubscription(TOPIC_GAME, gameModeSubscription, nullptr);

  NodeContext& ctx = nodeCore.context();

  ctx.log("INF", String("BOOT FW=") + FW_DESC + " rst=" + resetReasonShort());
  imagesModule.begin(ctx, NODE_IMAGES);
  pianoRiddle.begin(ctx, NODE_PIANO);
}

void loop() {
  nodeCore.loop();
  uint32_t now = millis();
  // piano must run every loop (audio / timing sensitive)
  pianoRiddle.tick(now);

  // images can be slow
  static uint32_t lastImagesTick = 0;
  if (now - lastImagesTick >= 500) {
    lastImagesTick = now;
    imagesModule.tick(now);
  }
}
