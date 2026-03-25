#include <Arduino.h>
#include <IPAddress.h>
#include <cstring>
#include <ArduinoJson.h>

#include "core_node.h"
#include "riddles/images_riddle.h"
#include "riddles/piano_riddle.h"

using namespace Core;

static const char* NODE_ID = "images_piano";
static const char* NODE_IMAGES = "images";
static const char* NODE_PIANO = "piano";
static const char* FW_VERSION = "28";
static const char* FW_DESC = "images_piano with new ota changed piano password";

static const uint8_t MAC_ADDR[6] = {0xDE, 0xAD, 0xBE, 0xEF, 0xFE, 0x59};
static const IPAddress NET_IP(192, 168, 0, 13);
static const IPAddress NET_DNS(0, 0, 0, 0);
static const IPAddress NET_GW(0, 0, 0, 0);
static const IPAddress NET_SUBNET(255, 255, 255, 0);
static const IPAddress MQTT_SERVER(192, 168, 0, 10);
static constexpr uint16_t MQTT_PORT = 1883;

static const char* TOPIC_GAME = "game/state";

static const char* OTA_HOST = "192.168.0.10";
static constexpr uint16_t OTA_PORT = 80;
static const char* OTA_PATH = "/node_firmware/images_piano.bin";
static const char* OTA_PATH_PREFIX = "/node_firmware/";
static const char* const OTA_ALLOWED_HOST = OTA_HOST;

static NodeCore nodeCore;
static ImagesRiddle imagesModule;
static PianoRiddle pianoRiddle;
static bool s_imagesEnabled = false;
static bool s_imagesSolved = false;
static bool s_pianoEnabled = false;
static bool s_pianoSolved = false;

struct PhaseCfg {
  bool imagesEnabled;
  bool imagesSolved;
  bool pianoEnabled;
  bool pianoSolved;
};

static constexpr PhaseCfg kPhaseCfg[15] = {
  {true,  false, true,  false}, // 0
  {false, false, false, false}, // 1
  {false, false, false, false}, // 2
  {true,  false, false, false}, // 3
  {false, true,  true,  false}, // 4
  {false, true,  false, true }, // 5
  {false, true,  false, true }, // 6
  {false, true,  false, true }, // 7
  {false, true,  false, true }, // 8
  {false, true,  false, true }, // 9
  {false, true,  false, true }, // 10
  {false, true,  false, true }, // 11
  {false, true,  false, true }, // 12
  {false, true,  false, true }, // 13
  {false, true,  false, true }  // 14
};

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

static void applyImagesTarget(bool enabled, bool solved) {
  if (enabled != s_imagesEnabled) {
    s_imagesEnabled = enabled;
    imagesModule.setGameMode(enabled);
    s_imagesSolved = false;  // setGameMode() resets state
  }
  if (solved != s_imagesSolved) {
    if (solved) {
      imagesModule.onCmd("SOLVE", "");
    } else {
      imagesModule.onCmd("RESET", "");
    }
    s_imagesSolved = solved;
  }
}

static void applyPianoTarget(bool enabled, bool solved) {
  if (enabled != s_pianoEnabled) {
    s_pianoEnabled = enabled;
    pianoRiddle.setGameMode(enabled);
    s_pianoSolved = false;  // setGameMode() resets state
  }
  if (solved != s_pianoSolved) {
    if (solved) {
      pianoRiddle.onCmd("SOLVE", "");
    } else {
      pianoRiddle.onCmd("RESET", "");
    }
    s_pianoSolved = solved;
  }
}

static void gameModeSubscription(NodeContext& ctx, const char* /*topic*/, const String& payload, void* /*userData*/) {
  (void)ctx;

  JsonDocument doc;
  if (deserializeJson(doc, payload)) return;

  int phase = doc["phase"] | -1;
  if (phase < 0 || phase >= 15) return;

  const PhaseCfg& cfg = kPhaseCfg[phase];
  applyImagesTarget(cfg.imagesEnabled, cfg.imagesSolved);
  applyPianoTarget(cfg.pianoEnabled, cfg.pianoSolved);
}

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
  cfg.net.ethRstPin = 16;

  cfg.topics = makeTopicConfig(cfg.nodeId);
  cfg.log.format = LogFormat::FwUptimeLevelMsg;
  cfg.log.includeDataField = true;
  cfg.heartbeat.intervalMs = 5000;
  cfg.heartbeat.builder = heartbeatBuilder;
  cfg.commands.levelEnable = "INF";
  cfg.commands.levelDisable = "INF";
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
  pianoRiddle.tick(now);
  static uint32_t lastImagesTick = 0;
  if (now - lastImagesTick >= 500) {
    lastImagesTick = now;
    imagesModule.tick(now);
  }
}
