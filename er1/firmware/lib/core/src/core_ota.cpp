#include "core_ota.h"

#include <ArduinoJson.h>
#include <cctype>
#include <cstdlib>
#include <cstring>
#include <esp_system.h>
#include <mbedtls/sha256.h>

#include "core_node.h"

namespace Core {

namespace {
constexpr uint32_t kConnectTimeoutMs = 5000;
constexpr uint32_t kHeaderTimeoutMs = 5000;
constexpr uint32_t kBodyStallTimeoutMs = 4000;
constexpr size_t kSha256HexLen = 64;
constexpr size_t kSha256HexBufLen = kSha256HexLen + 1;
constexpr size_t kMaxHostLen = 63;
constexpr size_t kMaxPathLen = 127;
constexpr size_t kMaxVersionLen = 47;
constexpr size_t kMaxIdLen = 47;
constexpr size_t kMaxTargetLen = 47;
constexpr const char* kDefaultPathPrefix = "/node_firmware/";
constexpr uint32_t kPendingMagic = 0xC05A4E2A;

using CommandFields = OtaUpdateCommand;

RTC_DATA_ATTR struct {
  uint32_t magic = 0;
  char version[kMaxVersionLen + 1]{};
  char id[kMaxIdLen + 1]{};
} g_pending;

void clearPending() {
  g_pending.magic = 0;
  g_pending.version[0] = '\0';
  g_pending.id[0] = '\0';
}

void persistPending(const char* id, const char* version) {
  clearPending();
  if (!id || !version) return;
  std::strncpy(g_pending.id, id, kMaxIdLen);
  g_pending.id[kMaxIdLen] = '\0';
  std::strncpy(g_pending.version, version, kMaxVersionLen);
  g_pending.version[kMaxVersionLen] = '\0';
  g_pending.magic = kPendingMagic;
}

bool startsWith(const char* value, const char* prefix) {
  if (!value || !prefix) return false;
  while (*prefix && *value) {
    if (*value != *prefix) return false;
    ++value;
    ++prefix;
  }
  return *prefix == '\0';
}

bool isHex(const char* str, size_t len) {
  if (!str) return false;
  for (size_t i = 0; i < len; i++) {
    const char c = str[i];
    if (!std::isxdigit(static_cast<unsigned char>(c))) return false;
  }
  return true;
}

void bytesToHex(const uint8_t* bytes, size_t len, char* out, size_t outLen) {
  static const char* kHex = "0123456789abcdef";
  if (!bytes || !out || outLen < (len * 2 + 1)) return;
  for (size_t i = 0; i < len; i++) {
    out[i * 2] = kHex[(bytes[i] >> 4) & 0xF];
    out[i * 2 + 1] = kHex[bytes[i] & 0xF];
  }
  out[len * 2] = '\0';
}

bool parseUrl(const String& raw, CommandFields& out) {
  String url = raw;
  url.trim();
  if (url.length() == 0) return false;

  if (url.startsWith("/")) {
    if (static_cast<size_t>(url.length()) > kMaxPathLen) return false;
    out.hasUrlPath = true;
    url.toCharArray(out.urlPath, kMaxPathLen + 1);
    return true;
  }

  if (url.startsWith("http://")) {
    url = url.substring(7);
  } else if (url.startsWith("https://")) {
    return false;
  }

  int slashIdx = url.indexOf('/');
  String hostPort = (slashIdx >= 0) ? url.substring(0, slashIdx) : url;
  String path = (slashIdx >= 0) ? url.substring(slashIdx) : "/";

  if (hostPort.length() == 0 || static_cast<size_t>(hostPort.length()) > kMaxHostLen) return false;
  int colonIdx = hostPort.indexOf(':');
  if (colonIdx > 0) {
    String portStr = hostPort.substring(colonIdx + 1);
    hostPort = hostPort.substring(0, colonIdx);
    int portVal = portStr.toInt();
    if (portVal > 0 && portVal <= 65535) {
      out.urlPort = static_cast<uint16_t>(portVal);
      out.hasUrlPort = true;
    }
  }
  if (static_cast<size_t>(path.length()) > kMaxPathLen) return false;
  if (!path.startsWith("/")) {
    path = "/" + path;
  }

  out.hasUrlHost = true;
  hostPort.toCharArray(out.urlHost, kMaxHostLen + 1);

  out.hasUrlPath = true;
  path.toCharArray(out.urlPath, kMaxPathLen + 1);
  return true;
}

bool constantTimeEquals(const char* a, const char* b, size_t len) {
  if (!a || !b) return false;
  unsigned char diff = 0;
  for (size_t i = 0; i < len; i++) {
    diff |= static_cast<unsigned char>(a[i]) ^ static_cast<unsigned char>(b[i]);
  }
  return diff == 0;
}

void copyBounded(const char* src, char* dst, size_t dstLen) {
  if (!dst || dstLen == 0) return;
  if (!src) {
    dst[0] = '\0';
    return;
  }
  std::strncpy(dst, src, dstLen - 1);
  dst[dstLen - 1] = '\0';
}

void refreshPresenceFlags(CommandFields& out) {
  out.hasVersion = out.version[0] != '\0';
  out.hasId = out.id[0] != '\0';
  out.hasTarget = out.target[0] != '\0';
}

bool parseSize(const JsonVariantConst& var, size_t& out) {
  if (var.is<uint32_t>()) {
    out = var.as<uint32_t>();
    return out > 0;
  }
  if (var.is<uint64_t>()) {
    out = static_cast<size_t>(var.as<uint64_t>());
    return out > 0;
  }
  if (var.is<const char*>()) {
    const char* s = var.as<const char*>();
    if (!s) return false;
    long val = atol(s);
    if (val <= 0) return false;
    out = static_cast<size_t>(val);
    return true;
  }
  return false;
}

bool parseJsonCommand(const char* payload, CommandFields& out) {
  if (!payload || payload[0] == '\0') return false;
  // Be tolerant to callers accidentally passing the full command line,
  // e.g. "UPDATE {...}" instead of only the JSON argument.
  while (*payload == ' ' || *payload == '\t' || *payload == '\r' || *payload == '\n') {
    payload++;
  }
  if (std::strncmp(payload, "UPDATE", 6) == 0) {
    const char* brace = std::strchr(payload, '{');
    if (brace) payload = brace;
  }

  // JsonDocument grows as needed; defaults are sufficient for URLs + sha256.
  JsonDocument doc;
  DeserializationError err = deserializeJson(doc, payload);
  if (err) return false;
  JsonObjectConst obj = doc.as<JsonObjectConst>();

  const char* sha = obj["sha256"] | obj["sha"];
  if (sha) {
    copyBounded(sha, out.sha256, sizeof(out.sha256));
  }
  const char* url = obj["url"];
  if (url) {
    parseUrl(String(url), out);
  }
  const char* path = obj["path"];
  if (path && !out.hasUrlPath) {
    parseUrl(String(path), out);
  }
  const char* host = obj["host"];
  if (host && !out.hasUrlHost) {
    copyBounded(host, out.urlHost, sizeof(out.urlHost));
    out.hasUrlHost = true;
  }
  uint16_t port = obj["port"] | 0;
  if (port > 0) {
    out.urlPort = port;
    out.hasUrlPort = true;
  }
  const char* version = obj["version"] | obj["ver"];
  if (version) {
    copyBounded(version, out.version, sizeof(out.version));
  }
  const char* id = obj["id"] | obj["nonce"];
  if (id) {
    copyBounded(id, out.id, sizeof(out.id));
  }
  const char* target = obj["target"] | obj["node"] | obj["dev"];
  if (target) {
    copyBounded(target, out.target, sizeof(out.target));
  }
  JsonVariantConst sizeField = obj["size"] | obj["bytes"];
  if (!sizeField.isNull()) {
    parseSize(sizeField, out.sizeBytes);
  }
  return true;
}

bool parseLegacyTokens(const String& payload, CommandFields& out) {
  String trimmed = payload;
  trimmed.trim();
  if (trimmed.length() == 0) return false;

  int pos = 0;
  while (pos < trimmed.length()) {
    int spaceIdx = trimmed.indexOf(' ', pos);
    String token = (spaceIdx < 0) ? trimmed.substring(pos) : trimmed.substring(pos, spaceIdx);
    pos = (spaceIdx < 0) ? trimmed.length() : spaceIdx + 1;
    token.trim();
    if (token.length() == 0) continue;
    int eqIdx = token.indexOf('=');
    if (eqIdx <= 0) {
      continue;
    }
    String key = token.substring(0, eqIdx);
    String value = token.substring(eqIdx + 1);
    key.trim();
    value.trim();
    key.toLowerCase();

    if (key == "sha256" || key == "sha") {
      value.toLowerCase();
      value.toCharArray(out.sha256, kSha256HexBufLen);
    } else if (key == "url") {
      parseUrl(value, out);
    } else if (key == "path") {
      parseUrl(value, out);
    } else if (key == "host") {
      value.toCharArray(out.urlHost, kMaxHostLen + 1);
      out.hasUrlHost = true;
    } else if (key == "port") {
      out.urlPort = static_cast<uint16_t>(value.toInt());
      out.hasUrlPort = out.urlPort > 0;
    } else if (key == "version" || key == "ver") {
      value.toCharArray(out.version, kMaxVersionLen + 1);
    } else if (key == "id" || key == "nonce") {
      value.toCharArray(out.id, kMaxIdLen + 1);
    } else if (key == "target" || key == "node" || key == "dev") {
      value.toCharArray(out.target, kMaxTargetLen + 1);
    } else if (key == "size" || key == "bytes") {
      long val = value.toInt();
      if (val > 0) out.sizeBytes = static_cast<size_t>(val);
    }
  }
  return true;
}

bool parseCommandPayload(const char* payload, CommandFields& out) {
  if (parseJsonCommand(payload, out) || parseLegacyTokens(String(payload), out)) {
    refreshPresenceFlags(out);
    return true;
  }
  return false;
}
}  // namespace

bool parseUpdateCommand(const char* payload, OtaUpdateCommand& out) {
  out = {};
  return parseCommandPayload(payload, out);
}

void OtaUpdater::begin(const OtaConfig& cfg, Logger* logger) {
  cfg_ = cfg;
  logger_ = logger;
  statusCtx_ = cfg_.statusCtx;
  currentId_ = "";
  currentTarget_ = cfg_.targetId ? cfg_.targetId : "";
  currentUrl_ = "";
  expectedSize_ = 0;
  bootReportPending_ = false;
  bootReportOk_ = false;
  pendingVersion_ = "";
  currentVersion_ = cfg_.targetFw ? cfg_.targetFw : "";
  lastMissingVersionWarnMs_ = 0;

  if (g_pending.magic == kPendingMagic) {
    pendingVersion_ = String(g_pending.version);
    currentId_ = String(g_pending.id);
    bootReportPending_ = true;
    bootReportOk_ = (pendingVersion_.length() > 0 && cfg_.targetFw && pendingVersion_ == String(cfg_.targetFw));
  }
}

bool OtaUpdater::perform(const char* cmdPayload) {
  if (!logger_) return false;

  currentId_ = "";
  currentUrl_ = "";
  expectedSize_ = 0;
  pendingVersion_ = "";
  currentVersion_ = cfg_.targetFw ? cfg_.targetFw : "";
  currentTarget_ = cfg_.targetId ? cfg_.targetId : "";

  CommandFields cmd{};
  if (!cmdPayload || cmdPayload[0] == '\0') {
    logger_->publish(cfg_.errLevel, "OTA payload missing");
    publishFail("parse", -1, "missing_payload", 0, "\"reason\":\"missing_payload\"");
    return false;
  }

  if (!parseUpdateCommand(cmdPayload, cmd)) {
    logger_->publish(cfg_.errLevel, "OTA payload parse failed");
    publishFail("parse", -1, "invalid_payload", 0, "\"reason\":\"invalid_payload\"");
    return false;
  }

  currentId_ = cmd.hasId ? String(cmd.id) : "";
  currentTarget_ = cfg_.targetId ? cfg_.targetId : "";
  if (cmd.hasTarget) {
    currentTarget_ = cmd.target;
  }
  currentVersion_ = cmd.hasVersion ? String(cmd.version) : "";
  pendingVersion_ = currentVersion_;
  expectedSize_ = cmd.sizeBytes;

  size_t shaLen = strlen(cmd.sha256);
  if (shaLen != kSha256HexLen || !isHex(cmd.sha256, shaLen)) {
    logger_->publish(cfg_.errLevel, "OTA missing/invalid sha256");
    publishFail("auth", -1, "invalid_sha256", 0, "\"reason\":\"invalid_sha256\"");
    return false;
  }

  if (!cmd.hasVersion) {
    uint32_t nowMs = millis();
    if (lastMissingVersionWarnMs_ == 0 || nowMs - lastMissingVersionWarnMs_ >= 60000) {
      lastMissingVersionWarnMs_ = nowMs;
      logger_->publish("WRN", "OTA missing version; ignoring");
    }
    return false;
  }

  if (!cmd.hasId) {
    logger_->publish(cfg_.errLevel, "OTA missing id");
    publishFail("auth", -1, "missing_id", 0, "\"reason\":\"missing_id\"");
    return false;
  }

  const char* expectedTarget = cfg_.targetId;
  if (expectedTarget && expectedTarget[0]) {
    if (!cmd.hasTarget) {
      logger_->publish(cfg_.errLevel, "OTA missing target");
      publishFail("auth", -1, "missing_target", 0, "\"reason\":\"missing_target\"");
      return false;
    }
    if (strcmp(cmd.target, expectedTarget) != 0) {
      String extra = String("\"reason\":\"target_mismatch\",\"expected\":\"") + expectedTarget + "\"";
      if (cmd.target[0] != '\0') {
        extra += ",\"got\":\"";
        extra += cmd.target;
        extra += "\"";
      }
      publishFail("auth", -1, "target_mismatch", 0, extra.c_str());
      logger_->publish(cfg_.errLevel, "OTA target mismatch");
      return false;
    }
  }

  const char* host = cmd.hasUrlHost ? cmd.urlHost : cfg_.host;
  const char* path = cmd.hasUrlPath ? cmd.urlPath : cfg_.path;
  uint16_t port = cmd.hasUrlPort ? cmd.urlPort : cfg_.port;

  if (!host || host[0] == '\0' || !path || path[0] == '\0') {
    logger_->publish(cfg_.errLevel, "OTA missing host/path");
    publishFail("auth", -1, "missing_url", 0, "\"reason\":\"missing_url\"");
    return false;
  }

  const char* allowedHost = cfg_.allowedHost ? cfg_.allowedHost : host;
  const char* allowedPathPrefix =
      (cfg_.allowedPathPrefix && cfg_.allowedPathPrefix[0]) ? cfg_.allowedPathPrefix : kDefaultPathPrefix;

  if (!allowedHost || strcmp(host, allowedHost) != 0) {
    String extra = String("\"reason\":\"host_not_allowed\",\"host\":\"") + host + "\"";
    publishFail("auth", -1, "host_not_allowed", 0, extra.c_str());
    logger_->publish(cfg_.errLevel, "OTA host not allowlisted");
    return false;
  }
  if (!allowedPathPrefix || !startsWith(path, allowedPathPrefix)) {
    String extra = String("\"reason\":\"path_not_allowed\",\"path\":\"") + path + "\"";
    publishFail("auth", -1, "path_not_allowed", 0, extra.c_str());
    logger_->publish(cfg_.errLevel, "OTA path not allowlisted");
    return false;
  }

  currentId_ = cmd.id;
  currentUrl_ = String("http://") + host;
  if (port != 0 && port != 80) {
    currentUrl_ += ":";
    currentUrl_ += String(port);
  }
  currentUrl_ += path;

  persistPending(currentId_.c_str(), currentVersion_.c_str());
  bootReportPending_ = true;
  bootReportOk_ = false;

  publishStart();
  logger_->publish(cfg_.infoLevel, String("OTA_START id=") + currentId_ + " ver=" + currentVersion_);

  EthernetClient client;
  client.setTimeout(kHeaderTimeoutMs);
  logger_->publish(cfg_.infoLevel, String("CMD UPDATE -> HTTP OTA ") + currentUrl_ + " sha256=" + cmd.sha256);

  uint32_t connStart = millis();
  bool connected = client.connect(host, port);
  while (!connected && (millis() - connStart) < kConnectTimeoutMs) {
    delay(10);
    delay(0);
    connected = client.connect(host, port);
  }
  if (!connected) {
    logger_->publish(cfg_.errLevel, "OTA connect failed");
    publishFail("conn", -1, "connect_timeout", 0, "\"reason\":\"connect_timeout\"");
    client.stop();
    return false;
  }

  client.print("GET ");
  client.print(path);
  client.print(" HTTP/1.1\r\nHost: ");
  client.print(host);
  client.print("\r\nConnection: close\r\n\r\n");

  uint32_t headerStart = millis();
  while (!client.available() && (millis() - headerStart) < kHeaderTimeoutMs) {
    delay(10);
    delay(0);
  }

  String statusLine = client.readStringUntil('\n');
  statusLine.trim();
  int httpCode = 0;
  if (statusLine.startsWith("HTTP/")) {
    int spaceIdx = statusLine.indexOf(' ');
    if (spaceIdx > 0) {
      httpCode = statusLine.substring(spaceIdx + 1).toInt();
    }
  }

  if (httpCode != 200) {
    logger_->publish(cfg_.errLevel, "OTA HTTP status != 200");
    publishFail("http", httpCode == 0 ? -1 : httpCode, "status", 0, "\"reason\":\"http_status\"");
    client.stop();
    return false;
  }

  long contentLength = -1;
  String remoteVersion;
  while (client.connected()) {
    if (millis() - headerStart > kHeaderTimeoutMs) {
      logger_->publish(cfg_.errLevel, "OTA header timeout");
      publishFail("hdr", -1, "timeout", 0, "\"reason\":\"hdr_timeout\"");
      client.stop();
      return false;
    }
    String line = client.readStringUntil('\n');
    line.trim();
    if (line.length() == 0) break;
    int colonIdx = line.indexOf(':');
    if (colonIdx <= 0) {
      delay(0);
      continue;
    }
    String key = line.substring(0, colonIdx);
    String value = line.substring(colonIdx + 1);
    key.trim();
    value.trim();
    key.toLowerCase();
    if (key == "content-length") {
      contentLength = value.toInt();
    } else if (key == "x-fw-version" || key == "x-ota-version") {
      remoteVersion = value;
    }
    delay(0);
  }

  if (contentLength <= 0) {
    logger_->publish(cfg_.errLevel, "OTA invalid content-length");
    publishFail("hdr", -1, "len", 0, "\"reason\":\"invalid_length\"");
    client.stop();
    return false;
  }

  if (remoteVersion.length() > 0 && cmd.version[0] != '\0' && !remoteVersion.equals(String(cmd.version))) {
    String extra = String("\"reason\":\"version_mismatch\",\"cmd\":\"") + cmd.version + "\",\"hdr\":\"" + remoteVersion +
                   "\"";
    logger_->publish(cfg_.errLevel, "OTA header version mismatch");
    publishFail("hdr", -1, "version_mismatch", 0, extra.c_str());
    client.stop();
    return false;
  }

  if (expectedSize_ > 0 && static_cast<size_t>(contentLength) != expectedSize_) {
    String extra = String("\"reason\":\"size_mismatch_hdr\",\"expected\":") + String(expectedSize_) +
                   ",\"content_length\":" + String(contentLength);
    logger_->publish(cfg_.errLevel, "OTA size mismatch vs expected size");
    publishFail("hdr", -1, "size_mismatch", 0, extra.c_str());
    client.stop();
    return false;
  }

  if (remoteVersion.length() > 0 && cfg_.targetFw && strlen(cfg_.targetFw) > 0) {
    if (remoteVersion.equals(String(cfg_.targetFw))) {
      logger_->publish(cfg_.infoLevel, String("OTA skip: version match (") + remoteVersion + ")");
      publishFail("ver", 0, "version_match", 0, "\"reason\":\"version_match\"");
      client.stop();
      return false;
    }
  }

  if (!Update.begin(contentLength)) {
    logger_->publish(cfg_.errLevel, "OTA Update.begin failed");
    publishFail("write", Update.getError(), "begin", 0, "\"reason\":\"update_begin\"");
    client.stop();
    return false;
  }

  mbedtls_sha256_context shaCtx;
  mbedtls_sha256_init(&shaCtx);
  bool shaReady = (mbedtls_sha256_starts_ret(&shaCtx, 0) == 0);

  uint8_t buf[512];
  size_t totalWritten = 0;
  uint32_t lastProgressMs = 0;
  int lastPct = -1;
  uint32_t lastDataMs = millis();
  bool startedUpdate = true;
  bool shaError = !shaReady;

  while (client.connected() || client.available()) {
    int len = client.read(buf, sizeof(buf));
    if (len <= 0) {
      if (millis() - lastDataMs > kBodyStallTimeoutMs) {
        logger_->publish(cfg_.errLevel, "OTA data stall");
        publishFail("conn", -1, "stall", totalWritten, "\"reason\":\"stall\"");
        Update.abort();
        client.stop();
        mbedtls_sha256_free(&shaCtx);
        return false;
      }
      delay(1);
      continue;
    }
    lastDataMs = millis();
    size_t written = Update.write(buf, len);
    if (written != static_cast<size_t>(len)) {
      logger_->publish(cfg_.errLevel, "OTA Update.write failed");
      totalWritten += written;
      publishFail("write", Update.getError(), "write", totalWritten, "\"reason\":\"write_failed\"");
      Update.abort();
      client.stop();
      mbedtls_sha256_free(&shaCtx);
      return false;
    }
    totalWritten += written;

    if (!shaError) {
      if (mbedtls_sha256_update_ret(&shaCtx, buf, len) != 0) {
        shaError = true;
      }
    }

    if (contentLength > 0) {
      int pct = static_cast<int>((totalWritten * 100) / contentLength);
      if (pct > 100) pct = 100;
      uint32_t now = millis();
      if (pct != lastPct && now - lastProgressMs >= cfg_.progressIntervalMs) {
        lastProgressMs = now;
        lastPct = pct;
        publishProgress(pct);
      }
    }
    delay(0);
  }

  if (startedUpdate && contentLength > 0 && totalWritten != static_cast<size_t>(contentLength)) {
    logger_->publish(cfg_.errLevel, "OTA size mismatch");
    publishFail("hdr", -1, "size_mismatch", totalWritten, "\"reason\":\"size_mismatch\"");
    Update.abort();
    client.stop();
    mbedtls_sha256_free(&shaCtx);
    return false;
  }

  if (expectedSize_ > 0 && totalWritten != expectedSize_) {
    String extra = String("\"reason\":\"size_mismatch\",\"expected\":") + String(expectedSize_) +
                   ",\"actual\":" + String(totalWritten);
    logger_->publish(cfg_.errLevel, "OTA size mismatch vs expected");
    publishFail("hdr", -1, "size_mismatch", totalWritten, extra.c_str());
    Update.abort();
    client.stop();
    mbedtls_sha256_free(&shaCtx);
    return false;
  }

  uint8_t shaRaw[32]{};
  bool haveSha = false;
  if (!shaError) {
    haveSha = (mbedtls_sha256_finish_ret(&shaCtx, shaRaw) == 0);
  }
  mbedtls_sha256_free(&shaCtx);

  if (!haveSha) {
    logger_->publish(cfg_.errLevel, "OTA sha256 calculation failed");
    publishFail("hash", -1, "sha256_error", totalWritten, "\"reason\":\"sha256_error\"");
    Update.abort();
    client.stop();
    return false;
  }

  char actualSha[kSha256HexBufLen]{};
  bytesToHex(shaRaw, sizeof(shaRaw), actualSha, sizeof(actualSha));
  if (!constantTimeEquals(actualSha, cmd.sha256, kSha256HexLen)) {
    String extra =
        String("\"reason\":\"sha256_mismatch\",\"expected\":\"") + cmd.sha256 + "\",\"actual\":\"" + actualSha + "\"";
    logger_->publish(cfg_.errLevel, "OTA sha256 mismatch");
    publishFail("hash", -1, "sha256_mismatch", totalWritten, extra.c_str());
    Update.abort();
    client.stop();
    return false;
  }

  if (!Update.end(true)) {
    logger_->publish(cfg_.errLevel, "OTA Update.end failed");
    publishFail("end", Update.getError(), "end", totalWritten, "\"reason\":\"update_end\"");
    Update.abort();
    client.stop();
    return false;
  }

  String flashData = buildBaseJson();
  flashData += ",\"sha256\":\"";
  flashData += actualSha;
  flashData += "\",\"bytes\":";
  flashData += String(totalWritten);
  flashData += "}";
  publishStatus("OTA_FLASHED", flashData, true);
  logger_->publish(cfg_.infoLevel, "OTA FLASHED, rebooting");
  delay(200);
  ESP.restart();
  return true;
}

void OtaUpdater::publishStatus(const char* st, const String& dataJson, bool retained) {
  if (!st) return;
  if (cfg_.statusPublisher) {
    cfg_.statusPublisher(st, dataJson, retained);
    return;
  }
  if (!statusCtx_) return;
  const auto& topics = statusCtx_->config().topics;
  if (topics.ota.length() == 0) return;
  const char* fw = statusCtx_->fwVersion();
  if (!fw || !fw[0]) {
    fw = cfg_.targetFw ? cfg_.targetFw : "?";
  }
  const char* build = statusCtx_->buildId();
  if (!build || !build[0]) {
    build = "?";
  }
  String payload;
  payload.reserve(128 + dataJson.length());
  payload = String("{\"fw\":\"") + fw + "\",\"up\":" + String(statusCtx_->uptimeSeconds()) +
            ",\"build\":\"" + build + "\",\"st\":\"" + st + "\",\"d\":" + dataJson + "}";
  statusCtx_->publish(topics.ota.c_str(), payload, retained);
}

void OtaUpdater::publishFail(const char* at, int code, const char* msg, size_t bytes, const char* extraJson) {
  if (!at || !msg) return;
  String data = buildBaseJson();
  data += ",\"at\":\"";
  data += at;
  data += "\",\"code\":";
  data += String(code);
  data += ",\"msg\":\"";
  data += msg;
  data += "\"";
  if (bytes > 0) {
    data += ",\"bytes\":";
    data += String(bytes);
  }
  if (extraJson && extraJson[0] != '\0') {
    data += ",";
    data += extraJson;
  }
  data += "}";
  publishStatus("OTA_FAIL", data, true);
  clearPending();
  bootReportPending_ = false;
  bootReportOk_ = false;
  pendingVersion_ = "";
}

void OtaUpdater::publishOk(size_t bytes, const char* sha256Hex, bool retained) {
  String data = buildBaseJson();
  if (bytes > 0) {
    data += ",\"bytes\":";
    data += String(bytes);
  }
  if (sha256Hex && sha256Hex[0]) {
    data += ",\"sha256\":\"";
    data += sha256Hex;
    data += "\"";
  }
  data += "}";
  publishStatus("OTA_OK", data, retained);
}

void OtaUpdater::publishStart() {
  String data = buildBaseJson();
  data += "}";
  publishStatus("OTA_START", data, true);
}

void OtaUpdater::publishProgress(int pct) {
  if (pct < 0) pct = 0;
  if (pct > 100) pct = 100;
  String data = buildBaseJson();
  data += ",\"pct\":";
  data += String(pct);
  data += "}";
  publishStatus("OTA_PROGRESS", data, false);
}

String OtaUpdater::buildBaseJson() const {
  String data = String("{\"id\":\"") + (currentId_.length() ? currentId_ : "?") + "\"";
  if (currentVersion_.length()) {
    data += ",\"version\":\"";
    data += currentVersion_;
    data += "\"";
  }
  if (currentTarget_.length()) {
    data += ",\"target\":\"";
    data += currentTarget_;
    data += "\"";
  }
  if (currentUrl_.length()) {
    data += ",\"url\":\"";
    data += currentUrl_;
    data += "\"";
  }
  return data;
}

void OtaUpdater::onMqttConnected() {
  if (!bootReportPending_) return;
  String data = buildBaseJson();
  data += ",\"running\":\"";
  data += (cfg_.targetFw ? cfg_.targetFw : "?");
  data += "\"";
  if (pendingVersion_.length()) {
    data += ",\"expected\":\"";
    data += pendingVersion_;
    data += "\"";
  }
  if (bootReportOk_) {
    data += "}";
    publishStatus("OTA_OK", data, true);
    if (logger_) logger_->publish(cfg_.infoLevel, String("OTA_OK id=") + currentId_);
  } else {
    data += ",\"reason\":\"version_mismatch_boot\"}";
    publishStatus("OTA_FAIL", data, true);
    if (logger_) logger_->publish(cfg_.errLevel, String("OTA_FAIL boot version mismatch id=") + currentId_);
  }
  clearPending();
  bootReportPending_ = false;
}

}  // namespace Core
