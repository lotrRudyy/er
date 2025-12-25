#include <Arduino.h>
#include <Ethernet.h>
#include <IPAddress.h>
#include <cstring>

#include "core_node.h"
#include "../include/fw_build_id.h"
#include "riddles/images_riddle.h"
#include "riddles/piano_riddle.h"

using namespace Core;

// ======================= FIRMWARE INFO =======================
static const char* NODE_ID = "images_piano";
static const char* NODE_IMAGES = "images";
static const char* NODE_PIANO = "piano";
static const char* FW_VERSION = "1.22";
static const char* FW_DESC = "Single firmware with two logical MQTT nodes; OTA JSON, no PSK";

// ======================= NETWORK CONFIG ======================
static const uint8_t MAC_ADDR[6] = {0xDE, 0xAD, 0xBE, 0xEF, 0xFE, 0x59};  // images_piano node MAC - must stay unique
static const IPAddress NET_IP(192, 168, 0, 12);
static const IPAddress NET_DNS(0, 0, 0, 0);
static const IPAddress NET_GW(0, 0, 0, 0);
static const IPAddress NET_SUBNET(255, 255, 255, 0);
static const IPAddress MQTT_SERVER(192, 168, 0, 10);
static constexpr uint16_t MQTT_PORT = 1883;

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
static Logger pianoLogger;

struct ModuleBundle {
  ImagesRiddle* images;
  PianoRiddle* piano;
};

static ModuleBundle moduleBundle{&imagesModule, &pianoRiddle};
static String topicPianoCmd;
static String topicPianoHb;
static String topicPianoLog;

// ======================= HELPERS =============================
static void publishPianoHeartbeat(NodeContext& ctx) {
  if (topicPianoHb.length() == 0) return;
  ErrorInfo err = ctx.errorInfo();
  HeartbeatFields hb{
      NODE_PIANO,
      ctx.fwVersion(),
      ctx.buildId(),
      ctx.uptimeSeconds(),
      err.count,
      err.code,
      (err.code != 0) ? err.sinceUp : 0,
      (err.code != 0 && err.msg.length() > 0) ? err.msg.c_str() : nullptr,
  };
  String payload;
  buildHeartbeatPayload(payload, hb);
  ctx.publish(topicPianoHb.c_str(), payload, true);
}

static void heartbeatBuilder(String& out, const NodeContext& ctx, void* /*userData*/) {
  NodeContext& mutableCtx = const_cast<NodeContext&>(ctx);
  ErrorInfo err = ctx.errorInfo();
  HeartbeatFields hb{
      NODE_IMAGES,
      ctx.fwVersion(),
      ctx.buildId(),
      ctx.uptimeSeconds(),
      err.count,
      err.code,
      (err.code != 0) ? err.sinceUp : 0,
      (err.code != 0 && err.msg.length() > 0) ? err.msg.c_str() : nullptr,
  };
  buildHeartbeatPayload(out, hb);
  publishPianoHeartbeat(mutableCtx);
}

static bool moduleCommandHandler(const char* cmd, const char* payload, void* userData) {
  ModuleBundle* bundle = static_cast<ModuleBundle*>(userData);
  bool handled = false;
  if (bundle && bundle->images) {
    handled |= bundle->images->onCmd(cmd, payload);
  }
  return handled;
}

static void pianoCmdSubscription(NodeContext& ctx, const char* /*topic*/, const String& payload, void* userData) {
  ModuleBundle* bundle = static_cast<ModuleBundle*>(userData);
  if (!bundle || !bundle->piano) return;
  String trimmed = payload;
  trimmed.trim();
  if (trimmed.length() == 0) return;

  String cmd = trimmed;
  String arg;
  int spaceIdx = trimmed.indexOf(' ');
  if (spaceIdx > 0) {
    cmd = trimmed.substring(0, spaceIdx);
    arg = trimmed.substring(spaceIdx + 1);
    arg.trim();
  }
  const char* cmdStr = cmd.c_str();
  const char* argStr = arg.c_str();
  bundle->piano->onCmd(cmdStr, argStr);
}

// ======================= ARDUINO LIFECYCLE ===================
void setup() {
  NodeCoreConfig cfg;
  cfg.nodeId = NODE_ID;
  cfg.fwVersion = FW_VERSION;
  cfg.fwDescription = FW_DESC;
  cfg.buildId = fwBuildId();
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

  cfg.topics = makeTopicConfig(NODE_IMAGES);
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
  nodeCore.registerCommandHandler(moduleCommandHandler, &moduleBundle);

  topicPianoCmd = Core::topic(NODE_PIANO, "cmd");
  topicPianoHb = Core::topic(NODE_PIANO, "hb");
  topicPianoLog = Core::topic(NODE_PIANO, "log");
  nodeCore.registerSubscription(topicPianoCmd.c_str(), pianoCmdSubscription, &moduleBundle);

  NodeContext& ctx = nodeCore.context();

  LogOptions pianoLogOpts;
  pianoLogOpts.topic = topicPianoLog.c_str();
  pianoLogOpts.format = LogFormat::FwUptimeLevelMsg;
  pianoLogOpts.includeDataField = true;
  pianoLogger.begin(ctx.mqttClient(), pianoLogOpts);
  pianoLogger.setTimestampSource(ctx.timestampSource());

  ctx.log("INF", String("BOOT FW=") + FW_DESC + " rst=" + resetReasonShort());
  imagesModule.begin(ctx, NODE_IMAGES);
  pianoRiddle.begin(ctx, NODE_PIANO, &pianoLogger);
}

void loop() {
  nodeCore.loop();
  uint32_t now = millis();
  imagesModule.tick(now);
  pianoRiddle.tick(now);
}
