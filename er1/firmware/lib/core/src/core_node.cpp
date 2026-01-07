#include "core_node.h"

#include <cstdio>
#include <ctime>
#include <stdexcept>
#include <esp_system.h>

#include "core_time.h"

namespace Core {

namespace {
String escapeJson(const char* s);

// Log levels ordered from most verbose to least.
// Matches what we emit on the wire ("DBG","INF","WRN","ERR").
static int levelRank(const char* level) {
  if (!level) return 99;
  if (strcmp(level, "DBG") == 0) return 0;
  if (strcmp(level, "INF") == 0) return 1;
  if (strcmp(level, "WRN") == 0) return 2;
  if (strcmp(level, "ERR") == 0) return 3;
  return 99;
}
}

// -------- NodeContext --------
NodeContext::NodeContext(NodeCore& core) : core_(&core) {}

bool NodeContext::publish(const char* topic, const String& payload, bool retained, int) {
  if (!core_) return false;
  return core_->publish(topic, payload, retained);
}

bool NodeContext::publish(const char* topic, const char* payload, bool retained, int) {
  if (!core_) return false;
  return core_->publish(topic, payload, retained);
}

bool NodeContext::publishEvent(const char* type, const String& dataJson, uint32_t version, const char* id) {
  if (!core_) return false;
  const auto& topics = core_->cfg_.topics;
  if (topics.evt.length() == 0) return false;
  return core_->publishEnvelope(topics.evt.c_str(), type, version, dataJson, id, false);
}

bool NodeContext::publishState(const String& dataJson, bool retained) {
  if (!core_) return false;
  const auto& topics = core_->cfg_.topics;
  if (topics.state.length() == 0) return false;
  return core_->publishEnvelope(topics.state.c_str(), "state", 1, dataJson, nullptr, retained);
}

bool NodeContext::publishEnvelope(const char* topic, const char* type, uint32_t version, const String& dataJson,
                                  const char* id, bool retained) {
  if (!core_) return false;
  return core_->publishEnvelope(topic, type, version, dataJson, id, retained);
}

void NodeContext::log(const char* level, const String& msg) {
  if (!core_) return;
  core_->logger().publish(level, msg);
}

void NodeContext::log(const char* level, const String& msg, const String& dataJson) {
  if (!core_) return;
  core_->logger().publish(level, msg, dataJson);
}

bool NodeContext::enabled() const {
  return core_ ? core_->enabled() : false;
}

void NodeContext::setEnabled(bool en) {
  if (core_) core_->setEnabled(en);
}

uint32_t NodeContext::uptimeSeconds() const {
  return millis() / 1000;
}

uint32_t NodeContext::logErrorCount() const {
  return core_ ? core_->errorCount() : 0;
}

uint32_t NodeContext::lastErrorSinceUp() const {
  return core_ ? core_->activeErrorSinceUp() : 0;
}

String NodeContext::lastErrorMsg() const {
  if (!core_) return "";
  return core_->activeErrorMsg();
}

uint32_t NodeContext::errorCount() const {
  return core_ ? core_->errorCount() : 0;
}

uint32_t NodeContext::activeErrorCode() const {
  return core_ ? core_->activeErrorCode() : 0;
}

uint32_t NodeContext::activeErrorSinceUp() const {
  return core_ ? core_->activeErrorSinceUp() : 0;
}

String NodeContext::activeErrorMsg() const {
  return core_ ? core_->activeErrorMsg() : String();
}

void NodeContext::setError(uint32_t code, const char* msg) {
  if (!core_) return;
  core_->setErrorInternal(code, msg);
}

void NodeContext::clearError() {
  if (core_) core_->clearErrorInternal();
}

void NodeContext::bumpErrorCount() {
  if (core_) core_->bumpErrorCountInternal();
}

ErrorInfo NodeContext::errorInfo(const ErrorInfo& moduleErr) const {
  ErrorInfo out = moduleErr;
  if (!core_) return out;
  // err_cnt: monotonic count of any errors seen.
  uint32_t coreCount = core_->errorCount();
  if (out.count < coreCount) out.count = coreCount;

  // Active error comes from module (if provided) or core.
  if (out.code == 0) {
    out.code = core_->activeErrorCode();
    out.sinceUp = core_->activeErrorSinceUp();
    out.msg = core_->activeErrorMsg();
  }
  return out;
}

const char* NodeContext::fwVersion() const {
  return core_ ? core_->cfg_.fwVersion : nullptr;
}

const char* NodeContext::buildId() const {
  return core_ ? core_->cfg_.buildId : nullptr;
}

const char* NodeContext::nodeId() const {
  return core_ ? core_->cfg_.nodeId : nullptr;
}

const NodeCoreConfig& NodeContext::config() const {
  return core_->cfg_;
}

Preferences& NodeContext::prefs() {
  return core_->prefs_;
}

PubSubClient* NodeContext::mqttClient() {
  if (!core_) return nullptr;
  return &core_->mqtt_.client();
}

TimestampSource* NodeContext::timestampSource() {
  return core_;
}
void NodeContext::requestHeartbeat() {
  if (core_) core_->publishHeartbeatNow();
}

void NodeContext::setHeartbeatInterval(uint32_t intervalMs) {
  if (core_) core_->setHeartbeatInterval(intervalMs);
}

uint32_t NodeContext::heartbeatInterval() const {
  return core_ ? core_->heartbeatInterval() : 20000;
}

// -------- NodeCore --------
NodeCore::NodeCore() : ctx_(*this) {}

void NodeCore::begin(const NodeCoreConfig& cfg) {
  cfg_ = cfg;
  errState_ = {};
  // Ensure local time formatting matches Europe/Rome (CET/CEST), not UTC.
  core_time_init_tz();
  buildIdBuf_[0] = '\0';
  if (!core_format_build_ts(buildIdBuf_, sizeof(buildIdBuf_))) {
    buildIdBuf_[0] = '\0';
  }

  if (!cfg_.nodeId || cfg_.nodeId[0] == '\0') {
    cfg_.nodeId = cfg_.net.clientId;
  }
  // Respect module-provided buildId (used as OTA verifier id). Fallback to compile timestamp.
  if (!cfg_.buildId || cfg_.buildId[0] == '\0') {
    cfg_.buildId = buildIdBuf_;
  }
  cfg_.topics = makeTopicConfig(cfg_.nodeId, cfg.topics);
  // Runtime log-level control topic: <node>/log/level
  topicLogLevel_ = String(cfg_.nodeId) + "/log/level";
  if (!cfg_.ota.targetFw || cfg_.ota.targetFw[0] == '\0') {
    cfg_.ota.targetFw = cfg_.fwVersion;
  }
  if (!cfg_.ota.targetId || cfg_.ota.targetId[0] == '\0') {
    cfg_.ota.targetId = cfg_.nodeId;
  }
  cfg_.ota.statusCtx = cfg_.ota.statusCtx ? cfg_.ota.statusCtx : &ctx_;
  hbCfg_ = cfg.heartbeat;
  heartbeatIntervalMs_ = hbCfg_.intervalMs;
  enabled_ = cfg.startEnabled;
  subCount_ = 0;
  lastHeartbeatMs_ = 0;
  heartbeatImmediate_ = true;

  NetConfig netCfg = cfg.net;
  netCfg.topicLwt = cfg_.topics.hb.c_str();
  mqtt_.begin(netCfg, this);

  LogOptions logOpts = cfg.log;
  logOpts.topic = cfg_.topics.log.c_str();
  logOpts.fwVersion = cfg.fwVersion;
  // Compose runtime log-level filter with any user-provided filter.
  userLogFilter_ = logOpts.filter;
  userLogFilterUser_ = logOpts.filterUser;
  logOpts.filter = &NodeCore::logFilterThunk;
  logOpts.filterUser = this;
  logger_.begin(&mqtt_.client(), logOpts);
  logger_.setTimestampSource(this);

  // Default runtime level: DBG (most verbose). Can be changed at runtime via <node>/log/level.
  minLogRank_ = 0;
  topicLogLevel_ = topic(cfg_.nodeId, "log/level");

  ota_.begin(cfg_.ota, &logger_);

  const char* prefsNs = cfg.prefsNamespace ? cfg.prefsNamespace : cfg.net.clientId;
  if (prefsNs && prefsNs[0] != '\0') {
    prefsReady_ = prefs_.begin(prefsNs, false);
    if (!prefsReady_) {
      logger_.publish("ERR", String("Prefs begin failed ns=") + prefsNs);
    } else {
      restoreEnabledState();
    }
  }
}

bool NodeCore::logFilterThunk(const char* level, void* user) {
  auto* self = static_cast<NodeCore*>(user);
  if (!self) return true;
  const int r = levelRank(level);
  const bool allowByLevel = (r >= self->minLogRank_);
  if (!allowByLevel) return false;
  if (self->userLogFilter_) {
    return self->userLogFilter_(level, self->userLogFilterUser_);
  }
  return true;
}

void NodeCore::handleLogLevelMessage(const String& payload) {
  String p = payload;
  p.trim();
  p.toUpperCase();
  int newRank = minLogRank_;
  if (p == "DBG") newRank = 0;
  else if (p == "INF" || p == "INFO") newRank = 1;
  else if (p == "WRN" || p == "WARN") newRank = 2;
  else if (p == "ERR" || p == "ERROR") newRank = 3;
  else {
    logger_.publish("WRN", String("Invalid log level: ") + p);
    return;
  }
  minLogRank_ = newRank;
  logger_.publish("INF", String("log_level set to ") + p);
}

void NodeCore::loop() {
  mqtt_.loop();
  publishHeartbeatIfDue();
}

bool NodeCore::registerCommandHandler(CommandHandler handler, void* userData) {
  moduleCmdHandler_ = handler;
  moduleCmdUser_ = userData;
  return true;
}

bool NodeCore::registerSubscription(const char* topic, SubscriptionHandler handler, void* userData) {
  if (subCount_ >= kMaxSubscriptions || !topic || !handler) {
    return false;
  }
  subs_[subCount_++] = {topic, handler, userData};
  if (mqtt_.connected()) {
    mqtt_.subscribe(topic);
  }
  return true;
}

bool NodeCore::publish(const char* topic, const String& payload, bool retained) {
  return mqtt_.publish(topic, payload, retained);
}

bool NodeCore::publish(const char* topic, const char* payload, bool retained) {
  return mqtt_.publish(topic, payload, retained);
}

void NodeCore::publishHeartbeatNow() {
  heartbeatImmediate_ = true;
  publishHeartbeatIfDue();
}

void NodeCore::setHeartbeatInterval(uint32_t intervalMs) {
  heartbeatIntervalMs_ = intervalMs;
}

void NodeCore::setEnabled(bool en) {
  bool changed = (enabled_ != en);
  enabled_ = en;
  if (changed) {
    persistEnabledState(enabled_ ? "enabled" : "disabled");
  }
}

void NodeCore::onMqttConnected() {
  const char* lvl = cfg_.mqttConnectedLogLevel ? cfg_.mqttConnectedLogLevel : "INF";
  logger_.publish(lvl, "MQTT connected");

  if (cfg_.topics.cmd.length() > 0) {
    mqtt_.subscribe(cfg_.topics.cmd.c_str());
  }

  // Subscribe to time/state for clock sync
  mqtt_.subscribe("time/state");

  // Subscribe to runtime log-level control
  if (topicLogLevel_.length() > 0) {
    mqtt_.subscribe(topicLogLevel_.c_str());
  }

  for (size_t i = 0; i < subCount_; i++) {
    mqtt_.subscribe(subs_[i].topic);
  }

  publishHeartbeatNow();
  ota_.onMqttConnected();
}

void NodeCore::onMqttMessage(const char* topic, const uint8_t* payload, size_t length) {
  String msg;
  msg.reserve(length + 1);
  for (size_t i = 0; i < length; i++) {
    msg += static_cast<char>(payload[i]);
  }
  msg.trim();

  // Handle time sync before other messages
  if (strcmp(topic, "time/state") == 0) {
    handleTimeStateMessage(msg);
    return;
  }

  // Runtime log-level control
  if (topicLogLevel_.length() > 0 && strcmp(topic, topicLogLevel_.c_str()) == 0) {
    handleLogLevelMessage(msg);
    return;
  }

  if (cfg_.topics.cmd.length() > 0 && strcmp(topic, cfg_.topics.cmd.c_str()) == 0) {
    logCommandEnvelope(topic, msg);
    handleCommandMessage(msg);
    return;
  }

  dispatchSubscription(topic, msg);
}

void NodeCore::handleCommandMessage(const String& raw) {
  String trimmed = raw;
  trimmed.trim();
  String cmd = trimmed;
  String arg;

  int spaceIdx = trimmed.indexOf(' ');
  if (spaceIdx > 0) {
    cmd = trimmed.substring(0, spaceIdx);
    arg = trimmed.substring(spaceIdx + 1);
    arg.trim();
  } else {
    arg = "";
  }

  // Commands are case-insensitive.
  // Normalize to uppercase before dispatching to core/module handlers.
  cmd.toUpperCase();

  size_t cmdLen = min(static_cast<size_t>(cmd.length()), kCmdBufSize - 1);
  cmd.toCharArray(cmdBuf_, cmdLen + 1);
  size_t argLen = min(static_cast<size_t>(arg.length()), kPayloadBufSize - 1);
  arg.toCharArray(payloadBuf_, argLen + 1);

  if (handleCoreCommand(cmdBuf_, payloadBuf_)) {
    return;
  }

  if (moduleCmdHandler_ && moduleCmdHandler_(cmdBuf_, payloadBuf_, moduleCmdUser_)) {
    return;
  }

  if (cfg_.commands.logUnknown) {
    String msg = String(cfg_.commands.unknownPrefix ? cfg_.commands.unknownPrefix : "Unknown CMD: ") + trimmed;
    const char* lvl = cfg_.commands.levelUnknown ? cfg_.commands.levelUnknown : "WRN";
    logger_.publish(lvl, msg);
  }
}

bool NodeCore::handleCoreCommand(const char* cmd, const char* arg) {
  if (cfg_.commands.allowEnableDisable) {
    if (strcmp(cmd, "DISABLE") == 0) {
      setEnabled(false);
      if (cfg_.commands.logEnableDisable) {
        const char* lvl = cfg_.commands.levelDisable ? cfg_.commands.levelDisable : "INF";
        logger_.publish(lvl, "CMD DISABLE");
      }
      return true;
    }
    if (strcmp(cmd, "ENABLE") == 0) {
      setEnabled(true);
      if (cfg_.commands.logEnableDisable) {
        const char* lvl = cfg_.commands.levelEnable ? cfg_.commands.levelEnable : "INF";
        logger_.publish(lvl, "CMD ENABLE");
      }
      return true;
    }
  }

  // Immediate heartbeat.
  // Aliases:
  //   - PING
  //   - SEND_HB / SENDHB / HB
  //   - SEND HB
  if (cfg_.commands.allowPing &&
      (strcmp(cmd, "PING") == 0 || strcmp(cmd, "SEND_HB") == 0 || strcmp(cmd, "SENDHB") == 0 || strcmp(cmd, "HB") == 0 ||
       (strcmp(cmd, "SEND") == 0 && arg && ((arg[0] == 'H' || arg[0] == 'h') && (arg[1] == 'B' || arg[1] == 'b') && arg[2] == '\0')))) {
    if (cfg_.commands.logPing) {
      const char* lvl = cfg_.commands.levelPing ? cfg_.commands.levelPing : "DBG";
      logger_.publish(lvl, "CMD PING");
    }
    publishHeartbeatNow();
    return true;
  }

  if (cfg_.commands.allowUpdate && strcmp(cmd, "UPDATE") == 0) {
    if (cfg_.commands.logUpdate) {
      const char* lvl = cfg_.commands.levelUpdate ? cfg_.commands.levelUpdate : "INF";
      logger_.publish(lvl, "CMD UPDATE");
    }
    ota_.perform(arg);
    return true;
  }

  if (cfg_.commands.allowReboot && strcmp(cmd, "REBOOT") == 0) {
    if (cfg_.commands.logReboot) {
      const char* lvl = cfg_.commands.levelReboot ? cfg_.commands.levelReboot : "INF";
      logger_.publish(lvl, "CMD REBOOT");
    }
    delay(200);
    forceRestart();
    return true;
  }

  return false;
}

void NodeCore::publishHeartbeatIfDue() {
  if (cfg_.topics.hb.length() == 0 || !hbCfg_.builder) return;

  uint32_t now = millis();
  if (!heartbeatImmediate_ && (now - lastHeartbeatMs_) < heartbeatIntervalMs_) {
    return;
  }

  heartbeatImmediate_ = false;
  lastHeartbeatMs_ = now;

  String payload;
  payload.reserve(196);
  payload = "";
  hbCfg_.builder(payload, ctx_, hbCfg_.user);
  if (payload.length() == 0) return;

  mqtt_.publish(cfg_.topics.hb.c_str(), payload, true);
}

void NodeCore::logCommandEnvelope(const char* topic, const String& payload) {
  if (!cfg_.commands.cmdLogLevel) return;
  String msg = String("CMD topic=") + topic + " msg=" + payload;
  logger_.publish(cfg_.commands.cmdLogLevel, msg);
}

bool NodeCore::topicMatches(const char* filter, const char* topic) const {
  if (!filter || !topic) return false;

  const char* f = filter;
  const char* t = topic;

  while (*f && *t) {
    if (*f == '#') {
      return true;
    }
    if (*f == '+') {
      // skip to next level in topic
      while (*t && *t != '/') {
        t++;
      }
      f++;
      if (*f == '/') {
        if (*t != '/') return false;
        f++;
        if (*t == '/') t++;
      }
      continue;
    }
    if (*f != *t) {
      return false;
    }
    f++;
    t++;
  }

  if (*f == '#') return true;
  return (*f == '\0' && *t == '\0');
}

void NodeCore::dispatchSubscription(const char* topic, const String& payload) {
  for (size_t i = 0; i < subCount_; i++) {
    if (topicMatches(subs_[i].topic, topic)) {
      subs_[i].handler(ctx_, topic, payload, subs_[i].user);
    }
  }
}

void NodeCore::forceRestart() {
  ESP.restart();
}

void NodeCore::restoreEnabledState() {
  if (!prefsReady_) return;
  const bool hasKey = prefs_.isKey("enabled");
  if (hasKey) {
    enabled_ = prefs_.getBool("enabled", enabled_);
    logger_.publish("INF", String("STATE restore enabled=") + (enabled_ ? "1" : "0"));
  } else {
    logger_.publish("INF", String("STATE default enabled=") + (enabled_ ? "1" : "0"));
    prefs_.putBool("enabled", enabled_);
  }
}

void NodeCore::persistEnabledState(const char* reason) {
  if (!prefsReady_) return;
  String msg = String("STATE save enabled=") + (enabled_ ? "1" : "0");
  if (reason && reason[0]) {
    msg += " ";
    msg += reason;
  }
  logger_.publish("INF", msg);
  prefs_.putBool("enabled", enabled_);
}

TimestampFields NodeCore::currentTimestamp() {
  TimestampFields ts{};
  ts.epoch = core_epoch_seconds();
  ts.timeValid = core_format_ts(ts.ts, sizeof(ts.ts));
  if (!ts.timeValid) {
    ts.ts[0] = '\0';
    maybeWarnMissingTs();
  }
  return ts;
}

bool NodeCore::publishEnvelope(const char* topic, const char* type, uint32_t version, const String& dataJson,
                               const char* id, bool retained) {
  if (!topic || !type) return false;

  TimestampFields ts = currentTimestamp();

  String tStr = escapeJson(type);
  String idStr = escapeJson(id);
  String payload;
  payload.reserve(dataJson.length() + tStr.length() + idStr.length() + 80);
  payload = "{\"t\":";
  payload += static_cast<long long>(ts.epoch);
  payload += ",\"ts\":\"";
  payload += ts.ts;
  payload += "\",\"time_valid\":";
  payload += ts.timeValid ? "true" : "false";
  payload += ",\"type\":\"";
  payload += tStr;
  payload += "\",\"v\":";
  payload += version;
  if (id && id[0]) {
    payload += ",\"id\":\"";
    payload += idStr;
    payload += "\"";
  }
  payload += ",\"d\":";
  if (dataJson.length() > 0) {
    payload += dataJson;
  } else {
    payload += "{}";
  }
  payload += "}";

  return mqtt_.publish(topic, payload, retained);
}

void NodeCore::maybeWarnMissingTs() {
  if (missingTsWarned_ || missingTsWarningActive_) return;
  missingTsWarningActive_ = true;
  missingTsWarned_ = true;
  logger_.publish("WRN", "wall-clock time not available; time_valid=false");
  missingTsWarningActive_ = false;
}

void NodeCore::handleTimeStateMessage(const String& payload) {
  // Parse JSON: {"epoch": <int>, "ts": "<str>", "src": "<str>", "seq": <int>}
  // Minimal JSON parsing to avoid bloat

  // Look for "epoch" field
  int epochIdx = payload.indexOf("\"epoch\":");
  if (epochIdx < 0) {
    uint32_t now = millis();
    if (now - lastTimeSyncParseErrorMs_ >= 60000) {
      lastTimeSyncParseErrorMs_ = now;
      logger_.publish("ERR", "time_sync_parse epoch field missing");
    }
    return;
  }

  // Extract epoch value
  int colonIdx = payload.indexOf(':', epochIdx);
  if (colonIdx < 0) return;

  int commaIdx = payload.indexOf(',', colonIdx);
  if (commaIdx < 0) {
    commaIdx = payload.indexOf('}', colonIdx);
  }
  if (commaIdx < 0) return;

  String epochStr = payload.substring(colonIdx + 1, commaIdx);
  epochStr.trim();

  int64_t epoch = 0;
  try {
    epoch = std::stoll(epochStr.c_str());
  } catch (...) {
    uint32_t now = millis();
    if (now - lastTimeSyncParseErrorMs_ >= 60000) {
      lastTimeSyncParseErrorMs_ = now;
      logger_.publish("ERR", String("time_sync_parse invalid epoch: ") + epochStr);
    }
    return;
  }

  // Validate epoch
  if (epoch < 1672531200) { // kMinValidEpoch
    return;
  }

  // Check if we should sync
  bool wasValid = core_time_valid();
  int64_t currentEpoch = ::time(nullptr);
  int64_t deltaSec = 0;

  if (wasValid) {
    deltaSec = (currentEpoch > epoch) ? (currentEpoch - epoch) : (epoch - currentEpoch);
  }

  // Set time
  if (!core_set_time(epoch)) {
    logger_.publish("ERR", String("time_sync failed to set epoch=") + static_cast<long long>(epoch));
    return;
  }

  // Log appropriately
  if (!wasValid) {
    // First time setting valid time
    if (!timeValidFirstSet_) {
      timeValidFirstSet_ = true;
      logger_.publish("INF", String("time_sync epoch=") + static_cast<long long>(epoch) + " src=mqtt");
    }
  } else {
    // Resync
    if (deltaSec > 2) {
      logger_.publish("WRN", String("time_resync delta_s=") + static_cast<long long>(deltaSec));
    }
  }
}

uint32_t NodeCore::errorCount() const {
  uint32_t count = errState_.count;
  uint32_t logCount = logger_.errorCount();
  if (logCount > count) count = logCount;
  return count;
}

void NodeCore::setErrorInternal(uint32_t code, const char* msg) {
  if (code == 0) {
    clearErrorInternal();
    return;
  }
  errState_.count++;
  errState_.activeCode = code;
  errState_.activeSinceUp = millis() / 1000;
  errState_.activeMsg = msg ? msg : "";
}

void NodeCore::clearErrorInternal() {
  errState_.activeCode = 0;
  errState_.activeSinceUp = 0;
  errState_.activeMsg = "";
}

void NodeCore::bumpErrorCountInternal() {
  errState_.count++;
}

const char* resetReasonShort() {
  switch (esp_reset_reason()) {
    case ESP_RST_POWERON: return "poweron";
    case ESP_RST_EXT: return "ext";
    case ESP_RST_SW: return "sw";
    case ESP_RST_PANIC: return "panic";
    case ESP_RST_INT_WDT: return "int_wdt";
    case ESP_RST_TASK_WDT: return "task_wdt";
    case ESP_RST_WDT: return "wdt";
    case ESP_RST_DEEPSLEEP: return "deepsleep";
    case ESP_RST_BROWNOUT: return "brownout";
    case ESP_RST_SDIO: return "sdio";
    default: return "unknown";
  }
}

TopicConfig makeTopicConfig(const char* nodeId, const TopicConfig& overrideCfg) {
  TopicConfig out = overrideCfg;
  if (!nodeId || nodeId[0] == '\0') {
    return out;
  }

  auto ensure = [&](String& field, const char* channel) {
    if (field.length() == 0) {
      field = topic(nodeId, channel);
    }
  };

  ensure(out.hb, "hb");
  ensure(out.cmd, "cmd");
  ensure(out.evt, "evt");
  ensure(out.state, "state");
  ensure(out.dbg, "dbg");
  ensure(out.log, "log");
  ensure(out.cfg, "cfg");
  ensure(out.ota, "ota");
  return out;
}

namespace {
String escapeJson(const char* s) {
  if (!s) return "";
  String in(s);
  String out;
  out.reserve(in.length() + 8);
  for (size_t i = 0; i < in.length(); i++) {
    const char c = in.charAt(i);
    switch (c) {
      case '\\': out += "\\\\"; break;
      case '"': out += "\\\""; break;
      case '\b': out += "\\b"; break;
      case '\f': out += "\\f"; break;
      case '\n': out += "\\n"; break;
      case '\r': out += "\\r"; break;
      case '\t': out += "\\t"; break;
      default:
        if (static_cast<uint8_t>(c) < 0x20) {
          char buf[7];
          std::snprintf(buf, sizeof(buf), "\\u%04x", static_cast<uint8_t>(c));
          out += buf;
        } else {
          out += c;
        }
        break;
    }
  }
  return out;
}
}  // namespace

void buildHeartbeat(String& out, const NodeContext& ctx, const ErrorInfo& moduleErr) {
  ErrorInfo merged = ctx.errorInfo(moduleErr);
  HeartbeatFields hb{
      ctx.nodeId(),
      ctx.fwVersion(),
      ctx.buildId(),
      ctx.uptimeSeconds(),
      merged.count,
      merged.code,
      (merged.code != 0) ? merged.sinceUp : 0,
      (merged.code != 0 && merged.msg.length() > 0) ? merged.msg.c_str() : nullptr,
  };
  buildHeartbeatPayload(out, hb);
}

void buildHeartbeatPayload(String& out, const HeartbeatFields& hb) {
  const char* node = (hb.nodeId && hb.nodeId[0]) ? hb.nodeId : "?";
  const char* fw = (hb.fw && hb.fw[0]) ? hb.fw : "?";
  const char* build = (hb.buildId && hb.buildId[0]) ? hb.buildId : "?";
  const uint32_t heapFree = ESP.getFreeHeap();
  const uint32_t heapMin = ESP.getMinFreeHeap();
  const uint32_t heapSize = ESP.getHeapSize();
  const uint32_t heapLargest = ESP.getMaxAllocHeap();
  TimestampFields tsFields{};
  tsFields.epoch = core_epoch_seconds();
  tsFields.timeValid = core_format_ts(tsFields.ts, sizeof(tsFields.ts));
  if (!tsFields.timeValid) tsFields.ts[0] = '\0';

  String nodeEsc = escapeJson(node);
  String fwEsc = escapeJson(fw);
  String buildEsc = escapeJson(build);
  String errMsgEsc = escapeJson(hb.errMsg);

  out.reserve(nodeEsc.length() + fwEsc.length() + buildEsc.length() + sizeof(tsFields.ts) + 128);
  out = "{\"node\":\"";
  out += nodeEsc;
  out += "\",\"fw\":\"";
  out += fwEsc;
  out += "\",\"build\":\"";
  out += buildEsc;
  out += "\",\"up\":";
  out += hb.uptime;
  out += ",\"ts\":\"";
  out += tsFields.ts;
  out += "\",\"time_valid\":";
  out += tsFields.timeValid ? "true" : "false";
  out += ",\"heap_free\":";
  out += heapFree;
  out += ",\"heap_min\":";
  out += heapMin;
  out += ",\"heap_largest\":";
  out += heapLargest;
  out += ",\"heap_size\":";
  out += heapSize;
  out += ",\"err_cnt\":";
  out += hb.errCount;
  out += ",\"err_code\":";
  out += hb.errCode;
  if (hb.errSinceUp > 0) {
    out += ",\"err_since_up\":";
    out += hb.errSinceUp;
  }
  if (hb.errCode != 0 && hb.errMsg && hb.errMsg[0]) {
    out += ",\"err_msg\":\"";
    out += errMsgEsc;
    out += "\"";
  }
  out += "}";
}

}  // namespace Core
