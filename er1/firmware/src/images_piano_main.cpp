#include <Arduino.h>
#include <IPAddress.h>
#include <cstring>

#include "core_node.h"
#include "riddles/images_riddle.h"
#include "riddles/piano_riddle.h"

using namespace Core;

static const char* NODE_ID = "images_piano";
static const char* NODE_IMAGES = "images";
static const char* NODE_PIANO = "piano";
static const char* FW_VERSION = "18";
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

namespace {
bool payloadHasName(const String& payload, const char* key, const char* value) {
  if (!value || !value[0]) return false;
  const String quoted = String("\"") + key + "\":\"" + value + "\"";
  if (payload.indexOf(quoted) >= 0) return true;
  const String arr = String("\"") + key + "\":[";
  int pos = payload.indexOf(arr);
  if (pos < 0) return false;
  String needle = String("\"") + value + "\"";
  int end = payload.indexOf("]", pos);
  if (end < 0) end = payload.length();
  return payload.indexOf(needle, pos) >= 0 && payload.indexOf(needle, pos) < end;
}
String detectMode(const String& payload) {
  String upper = payload;
  upper.trim();
  upper.toUpperCase();
  if (upper == "MODE_MAINTENANCE" || upper.indexOf("\"MODE\":\"MODE_MAINTENANCE\"") >= 0) return "MODE_MAINTENANCE";
  if (upper == "MODE_PREPARE" || upper.indexOf("\"MODE\":\"MODE_PREPARE\"") >= 0) return "MODE_PREPARE";
  if (upper == "MODE_INGAME" || upper.indexOf("\"MODE\":\"MODE_INGAME\"") >= 0) return "MODE_INGAME";
  return "MODE_STANDBY";
}
bool shouldNodeBeActive(const String& payload, const char* nodeName) {
  const String mode = detectMode(payload);
  if (mode == "MODE_MAINTENANCE") return true;
  if (mode != "MODE_INGAME") return false;
  return payloadHasName(payload, "active", nodeName) || payloadHasName(payload, "solved", nodeName);
}
}

static void heartbeatBuilder(String& out, const NodeContext& ctx, void*) {
  ErrorInfo err = ctx.errorInfo();
  buildHeartbeat(out, ctx, err);
}

static bool moduleCommandHandler(const char* cmd, const char* payload, void*) {
  bool handled = false;
  handled |= imagesModule.onCmd(cmd, payload);
  handled |= pianoRiddle.onCmd(cmd, payload);
  return handled;
}

static void gameModeSubscription(NodeContext& ctx, const char*, const String& payload, void*) {
  const String mode = detectMode(payload);
  const bool imagesActive = shouldNodeBeActive(payload, NODE_IMAGES);
  const bool pianoActive = shouldNodeBeActive(payload, NODE_PIANO);
  imagesModule.setGameMode(imagesActive);
  pianoRiddle.setGameMode(pianoActive);
  if (pianoActive || mode == "MODE_MAINTENANCE") {
    ctx.prefs().putBool("images_solved", true);
  } else if (!imagesActive) {
    ctx.prefs().putBool("images_solved", false);
  }
}

void setup() {
  NodeCoreConfig cfg;
  cfg.nodeId = NODE_ID;
  cfg.fwVersion = FW_VERSION;
  cfg.fwDescription = FW_DESC;
  cfg.startEnabled = true;
  cfg.prefsNamespace = NODE_ID;
  std::memcpy(cfg.net.mac, MAC_ADDR, sizeof(MAC_ADDR));
  cfg.net.ip = NET_IP; cfg.net.dns = NET_DNS; cfg.net.gateway = NET_GW; cfg.net.subnet = NET_SUBNET;
  cfg.net.mqttServer = MQTT_SERVER; cfg.net.mqttPort = MQTT_PORT; cfg.net.clientId = NODE_ID; cfg.net.ethRstPin = 16;
  cfg.topics = makeTopicConfig(cfg.nodeId);
  cfg.log.format = LogFormat::FwUptimeLevelMsg;
  cfg.log.includeDataField = true;
  cfg.heartbeat.intervalMs = 5000;
  cfg.heartbeat.builder = heartbeatBuilder;
  cfg.commands.levelEnable = "INF"; cfg.commands.levelDisable = "INF"; cfg.commands.allowReboot = true;
  cfg.commands.logPing = false; cfg.commands.levelPing = "DBG"; cfg.commands.logUnknown = true; cfg.commands.levelUnknown = "WRN";
  cfg.commands.logUpdate = false; cfg.commands.levelUpdate = "INF"; cfg.commands.cmdLogLevel = "DBG";
  cfg.ota.host = OTA_HOST; cfg.ota.port = OTA_PORT; cfg.ota.path = OTA_PATH; cfg.ota.allowedHost = OTA_ALLOWED_HOST;
  cfg.ota.allowedPathPrefix = OTA_PATH_PREFIX; cfg.ota.infoLevel = "INF"; cfg.ota.errLevel = "ERR";
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
