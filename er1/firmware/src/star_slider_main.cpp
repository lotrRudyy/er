#include <Arduino.h>
#include <IPAddress.h>
#include <cstring>

#include <ArduinoJson.h>

#include "core_node.h"
#include "riddles/star_slider_riddle.h"

using namespace Core;

static const char* NODE_ID = "star_slider";
static const char* FW_VERSION = "16";
static const char* FW_DESC = "star_slider with new ota";

static const uint8_t MAC_ADDR[6] = {0xDE, 0xAD, 0xBE, 0xEF, 0xFE, 0x52};
static const IPAddress NET_IP(192, 168, 0, 18);
static const IPAddress NET_DNS(0, 0, 0, 0);
static const IPAddress NET_GW(0, 0, 0, 0);
static const IPAddress NET_SUBNET(255, 255, 255, 0);
static const IPAddress MQTT_SERVER(192, 168, 0, 10);
static constexpr uint16_t MQTT_PORT = 1883;

static const char* TOPIC_GAME = "game/state";

static const char* OTA_HOST = "192.168.0.10";
static constexpr uint16_t OTA_PORT = 80;
static const char* OTA_PATH = "/node_firmware/star_slider.bin";
static const char* OTA_PATH_PREFIX = "/node_firmware/";
static const char* const OTA_ALLOWED_HOST = OTA_HOST;

static NodeCore nodeCore;
static StarSliderRiddle starSlider;
static bool s_gameEnabled = false;
static bool s_gameSolved = false;

struct PhaseCfg {
  bool enabled;
  bool solved;
};

static constexpr PhaseCfg kPhaseCfg[15] = {
  {true,  false}, // 0
  {false, false}, // 1
  {false, false}, // 2
  {false, false}, // 3
  {false, false}, // 4
  {false, false}, // 5
  {false, false}, // 6
  {false, false}, // 7
  {false, false}, // 8
  {false, false}, // 9
  {false, false}, // 10
  {false, false}, // 11
  {true,  false}, // 12
  {false, true }, // 13
  {false, true }  // 14
};

static void applyTarget(StarSliderRiddle* module, bool enabled, bool solved) {
  if (!module) return;

  if (enabled != s_gameEnabled) {
    s_gameEnabled = enabled;
    module->setGameMode(enabled);
    s_gameSolved = false;  // setGameMode() resets state
  }

  if (solved != s_gameSolved) {
    if (solved) {
      module->onCmd("SOLVE", "");
    } else {
      module->onCmd("RESET", "");
    }
    s_gameSolved = solved;
  }
}

static void heartbeatBuilder(String& out, const NodeContext& ctx, void* userData) {
  auto* module = static_cast<StarSliderRiddle*>(userData);
  ErrorInfo err{};
  if (module) err.count = module->errorCount();
  buildHeartbeat(out, ctx, err);
}

static bool moduleCommandHandler(const char* cmd, const char* payload, void* userData) {
  auto* module = static_cast<StarSliderRiddle*>(userData);
  return module ? module->onCmd(cmd, payload) : false;
}

static void gameModeSubscription(NodeContext& ctx, const char* /*topic*/, const String& payload, void* userData) {
  (void)ctx;

  StaticJsonDocument<128> doc;
  if (deserializeJson(doc, payload)) return;

  int phase = doc["phase"] | -1;
  if (phase < 0 || phase >= 15) return;

  const PhaseCfg& cfg = kPhaseCfg[phase];
  applyTarget(static_cast<StarSliderRiddle*>(userData), cfg.enabled, cfg.solved);
}

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

  NodeContext& ctx = nodeCore.context();
  starSlider.begin(ctx);
  ctx.log("INF", String("BOOT FW=") + FW_DESC + " rst=" + resetReasonShort());
}

void loop() {
  nodeCore.loop();
  starSlider.tick(millis());
}
