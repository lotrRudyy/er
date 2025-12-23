#include <Arduino.h>
#include <IPAddress.h>
#include <cstring>

#include "core_node.h"
#include "riddles/images_riddle.h"
#include "riddles/piano_mapper.h"
#include "riddles/piano_riddle_fsm.h"

using namespace Core;

// ======================= FIRMWARE INFO =======================
static const char* NODE_IDENTITY = "images_piano";
static const char* NODE_IMAGES = "images";
static const char* NODE_PIANO = "piano";
static const char* FW_VERSION = "1.19";
static const char* FW_DESC = "Single firmware with two logical MQTT nodes; OTA JSON, no PSK";
static const char* FW_BUILD_ID = "ZFX663XD7DXFSQG26ZAF";

// ======================= NETWORK CONFIG ======================
static const uint8_t MAC_ADDR[6] = {0xDE, 0xAD, 0xBE, 0xEF, 0xFE, 0x57};
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
static PianoRiddleFSM pianoFsm;
static PianoMapper pianoMapper;
static Logger pianoLogger;

struct ModuleBundle {
  ImagesRiddle* images;
  PianoRiddleFSM* piano;
  PianoMapper* mapper;
};

static ModuleBundle moduleBundle{&imagesModule, &pianoFsm, &pianoMapper};
static String topicPianoCmd;
static String topicPianoHb;
static String topicPianoLog;

// ======================= HELPERS =============================
static void publishPianoHeartbeat(NodeContext& ctx) {
  if (topicPianoHb.length() == 0) return;
  uint32_t errCnt = ctx.logErrorCount();
  uint32_t errCode = (errCnt > 0) ? 1 : 0;
  uint32_t errSince = (errCode > 0) ? ctx.lastErrorSinceUp() : 0;
  String errMsg = (errCode > 0) ? ctx.lastErrorMsg() : "";
  HeartbeatFields hb{
      NODE_PIANO,
      ctx.fwVersion(),
      ctx.buildId(),
      ctx.uptimeSeconds(),
      errCnt,
      errCode,
      errSince,
      (errCode > 0 && errMsg.length() > 0) ? errMsg.c_str() : nullptr,
  };
  String payload;
  buildHeartbeatPayload(payload, hb);
  ctx.publish(topicPianoHb.c_str(), payload, true);
}

static void heartbeatBuilder(String& out, const NodeContext& ctx, void* /*userData*/) {
  NodeContext& mutableCtx = const_cast<NodeContext&>(ctx);
  uint32_t errCnt = ctx.logErrorCount();
  uint32_t errCode = (errCnt > 0) ? 1 : 0;
  uint32_t errSince = (errCode > 0) ? ctx.lastErrorSinceUp() : 0;
  String errMsg = (errCode > 0) ? ctx.lastErrorMsg() : "";
  HeartbeatFields hb{
      NODE_IMAGES,
      ctx.fwVersion(),
      ctx.buildId(),
      ctx.uptimeSeconds(),
      errCnt,
      errCode,
      errSince,
      (errCode > 0 && errMsg.length() > 0) ? errMsg.c_str() : nullptr,
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
  if (!bundle || (!bundle->piano && !bundle->mapper)) return;
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
  if (bundle->piano) {
    bundle->piano->onCmd(cmdStr, argStr);
  }
  if (bundle->mapper) {
    bundle->mapper->onCmd(cmdStr, argStr);
  }
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
  NodeCoreConfig cfg;
  cfg.nodeId = NODE_IDENTITY;
  cfg.fwVersion = FW_VERSION;
  cfg.fwDescription = FW_DESC;
  cfg.buildId = FW_BUILD_ID;
  cfg.startEnabled = true;
  cfg.prefsNamespace = NODE_IDENTITY;

  std::memcpy(cfg.net.mac, MAC_ADDR, sizeof(MAC_ADDR));
  cfg.net.ip = NET_IP;
  cfg.net.dns = NET_DNS;
  cfg.net.gateway = NET_GW;
  cfg.net.subnet = NET_SUBNET;
  cfg.net.mqttServer = MQTT_SERVER;
  cfg.net.mqttPort = MQTT_PORT;
  cfg.net.clientId = NODE_IDENTITY;

  cfg.topics = makeTopicConfig(NODE_IMAGES);
  cfg.log.format = LogFormat::FwUptimeLevelMsg;
  cfg.log.includeDataField = true;
  cfg.heartbeat.intervalMs = 20000;
  cfg.heartbeat.builder = heartbeatBuilder;
  cfg.commands.cmdLogLevel = "DBG";
  cfg.commands.levelEnable = "INF";
  cfg.commands.levelDisable = "INF";
  cfg.commands.logPing = false;  // legacy: no log on ping
  cfg.commands.allowReboot = false;
  cfg.commands.logUnknown = false;
  cfg.commands.levelPing = "INF";
  cfg.commands.levelUpdate = "INF";
  cfg.commands.logUpdate = false;  // OTA logs handled inside updater

  cfg.ota.host = OTA_HOST;
  cfg.ota.port = OTA_PORT;
  cfg.ota.path = OTA_PATH;
  cfg.ota.allowedHost = OTA_ALLOWED_HOST;
  cfg.ota.allowedPathPrefix = OTA_PATH_PREFIX;
  cfg.ota.infoLevel = "INF";
  cfg.ota.errLevel = "ERR";
  cfg.ota.statusPublisher = publishOtaStatus;

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

  ctx.log("INF", String("BOOT FW=") + FW_DESC);
  imagesModule.begin(ctx, NODE_IMAGES);
  pianoFsm.begin(ctx, NODE_PIANO, &pianoLogger);
  pianoMapper.begin(ctx, &pianoLogger);
}

void loop() {
  nodeCore.loop();
  uint32_t now = millis();
  imagesModule.tick(now);
  pianoFsm.tick(now);
  pianoMapper.tick(now);
}
