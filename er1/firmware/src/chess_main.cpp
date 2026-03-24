#include <Arduino.h>
#include <IPAddress.h>
#include <cstring>
#include <ArduinoJson.h>

#include "core_node.h"
#include "riddles/chess_riddle.h"

using namespace Core;

static const char* NODE_ID = "chess";
static const char* FW_VERSION = "20";
static const char* FW_DESC = "chess phase-driven state sync and full reader updates";

static const uint8_t MAC_ADDR[6] = {0xDE, 0xAD, 0xBE, 0xEF, 0xFE, 0x57};
static const IPAddress NET_IP(192, 168, 0, 14);
static const IPAddress NET_DNS(0, 0, 0, 0);
static const IPAddress NET_GW(0, 0, 0, 0);
static const IPAddress NET_SUBNET(255, 255, 255, 0);
static const IPAddress MQTT_SERVER(192, 168, 0, 10);
static constexpr uint16_t MQTT_PORT = 1883;

static const char* TOPIC_GAME = "game/state";

static const char* OTA_HOST = "192.168.0.10";
static constexpr uint16_t OTA_PORT = 80;
static const char* OTA_PATH = "/node_firmware/chess.bin";
static const char* OTA_PATH_PREFIX = "/node_firmware/";
static const char* const OTA_ALLOWED_HOST = OTA_HOST;

static NodeCore nodeCore;
static ChessRiddle chess;
static bool s_gameEnabled = false;

struct ChessPhaseState {
  bool enabled;
  bool solved;
};

static ChessPhaseState stateForPhase(int phase) {
  switch (phase) {
    case 0: return {true,  false}; // maintenance
    case 1: return {false, false}; // standby
    case 2: return {false, false}; // prepare
    case 3: return {false, false};
    case 4: return {false, false};
    case 5: return {false, false};
    case 6: return {false, false};
    case 7: return {false, false};
    case 8: return {false, false};
    case 9: return {true,  false}; // chess
    case 10: return {false, true };
    case 11: return {false, true };
    case 12: return {false, true };
    case 13: return {false, true };
    case 14: return {false, true };
    default: return {false, false};
  }
}

static int parsePhaseFromGameState(const String& payload) {
  JsonDocument doc;
  if (deserializeJson(doc, payload)) return -1;
  if (!doc["phase"].is<int>()) return -1;
  return doc["phase"].as<int>();
}

static bool s_gameSolved = false;

static void applyPhaseState(ChessRiddle* module, bool enabled, bool solved) {
  if (!module) return;
  if (enabled != s_gameEnabled) {
    s_gameEnabled = enabled;
    module->setGameMode(enabled);
  }
  if (solved != s_gameSolved) {
    s_gameSolved = solved;
    if (solved) {
      module->onCmd("SOLVE", "");
    }
  }
}

static void heartbeatBuilder(String& out, const NodeContext& ctx, void* user) {
  auto* module = static_cast<ChessRiddle*>(user);
  ErrorInfo err{};
  if (module) err.count = module->errorCount();
  buildHeartbeat(out, ctx, err);
}

static bool moduleCommandHandler(const char* cmd, const char* payload, void* user) {
  auto* module = static_cast<ChessRiddle*>(user);
  return module ? module->onCmd(cmd, payload) : false;
}

static void gameModeSubscription(NodeContext& ctx, const char* /*topic*/, const String& payload, void* user) {
  (void)ctx;
  const int phase = parsePhaseFromGameState(payload);
  if (phase < 0) return;
  const ChessPhaseState next = stateForPhase(phase);
  applyPhaseState(static_cast<ChessRiddle*>(user), next.enabled, next.solved);
}

void setup() {
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
  cfg.log.includeDataField = true;

  cfg.heartbeat.intervalMs = 5000;
  cfg.heartbeat.builder = heartbeatBuilder;
  cfg.heartbeat.user = &chess;

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
  nodeCore.registerCommandHandler(moduleCommandHandler, &chess);
  nodeCore.registerSubscription(TOPIC_GAME, gameModeSubscription, &chess);

  NodeContext& ctx = nodeCore.context();
  chess.begin(ctx);
  ctx.log("INF", String("BOOT FW=") + FW_DESC + " rst=" + resetReasonShort());
}

void loop() {
  nodeCore.loop();
  chess.tick(millis());
}
