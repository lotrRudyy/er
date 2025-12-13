#include "core_node.h"

#include <cstdio>

namespace Core {

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

void NodeContext::requestHeartbeat() {
  if (core_) core_->publishHeartbeatNow();
}

void NodeContext::setHeartbeatInterval(uint32_t intervalMs) {
  if (core_) core_->setHeartbeatInterval(intervalMs);
}

uint32_t NodeContext::heartbeatInterval() const {
  return core_ ? core_->heartbeatInterval() : 0;
}

// -------- NodeCore --------
NodeCore::NodeCore() : ctx_(*this) {}

void NodeCore::begin(const NodeCoreConfig& cfg) {
  cfg_ = cfg;
  if (!cfg_.nodeId || cfg_.nodeId[0] == '\0') {
    cfg_.nodeId = cfg_.net.clientId;
  }
  if (!cfg_.buildId || cfg_.buildId[0] == '\0') {
    cfg_.buildId = __DATE__ " " __TIME__;
  }
  cfg_.topics = makeTopicConfig(cfg_.nodeId, cfg.topics);
  if (!cfg_.ota.targetFw || cfg_.ota.targetFw[0] == '\0') {
    cfg_.ota.targetFw = cfg_.fwVersion;
  }
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
  logger_.begin(&mqtt_.client(), logOpts);

  ota_.begin(cfg.ota, &logger_);

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

  for (size_t i = 0; i < subCount_; i++) {
    mqtt_.subscribe(subs_[i].topic);
  }

  publishHeartbeatNow();
}

void NodeCore::onMqttMessage(const char* topic, const uint8_t* payload, size_t length) {
  String msg;
  msg.reserve(length + 1);
  for (size_t i = 0; i < length; i++) {
    msg += static_cast<char>(payload[i]);
  }
  msg.trim();

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
  (void)arg;
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

  if (cfg_.commands.allowPing && strcmp(cmd, "PING") == 0) {
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
    ota_.perform();
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

void buildHeartbeatPayload(String& out, const HeartbeatFields& hb) {
  const char* node = (hb.nodeId && hb.nodeId[0]) ? hb.nodeId : "?";
  const char* fw = (hb.fw && hb.fw[0]) ? hb.fw : "?";
  const char* build = (hb.buildId && hb.buildId[0]) ? hb.buildId : "?";
  const char* health = (hb.health && hb.health[0]) ? hb.health : "ok";
  const char* mem = (hb.mem && hb.mem[0]) ? hb.mem : "ok";
  const char* lastErr = (hb.lastErr && hb.lastErr[0]) ? hb.lastErr : "0";

  String nodeEsc = escapeJson(node);
  String fwEsc = escapeJson(fw);
  String buildEsc = escapeJson(build);
  String healthEsc = escapeJson(health);
  String memEsc = escapeJson(mem);
  String lastErrEsc = escapeJson(lastErr);

  out.reserve(nodeEsc.length() + fwEsc.length() + buildEsc.length() + 64);
  out = "{\"node\":\"";
  out += nodeEsc;
  out += "\",\"fw\":\"";
  out += fwEsc;
  out += "\",\"build\":\"";
  out += buildEsc;
  out += "\",\"up\":";
  out += hb.uptime;
  out += ",\"health\":\"";
  out += healthEsc;
  out += "\",\"mem\":\"";
  out += memEsc;
  out += "\",\"last_err\":\"";
  out += lastErrEsc;
  out += "\"}";
}

}  // namespace Core
