#include "core_ota.h"

#include <cctype>
#include <cstring>
#include <mbedtls/md.h>
#include <mbedtls/platform_util.h>
#include <mbedtls/sha256.h>

namespace Core {

namespace {
constexpr uint32_t kConnectTimeoutMs = 5000;
constexpr uint32_t kHeaderTimeoutMs = 5000;
constexpr uint32_t kBodyStallTimeoutMs = 4000;
constexpr size_t kSha256HexLen = 64;
constexpr size_t kSha256HexBufLen = kSha256HexLen + 1;
constexpr size_t kMaxHostLen = 63;
constexpr size_t kMaxPathLen = 127;
constexpr const char* kDefaultPathPrefix = "/firmware/";

struct CommandFields {
  char sha256[kSha256HexBufLen]{};
  char hmac[kSha256HexBufLen]{};
  char urlHost[kMaxHostLen + 1]{};
  char urlPath[kMaxPathLen + 1]{};
  bool hasUrlHost = false;
  bool hasUrlPath = false;
};

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
    hostPort = hostPort.substring(0, colonIdx);
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

bool computeHmac(const char* key, const char* msg, char* outHex, size_t outLen) {
  if (!key || !msg || !outHex || outLen < kSha256HexBufLen) return false;
  const mbedtls_md_info_t* info = mbedtls_md_info_from_type(MBEDTLS_MD_SHA256);
  if (!info) return false;
  unsigned char out[32];
  int rc = mbedtls_md_hmac(info, reinterpret_cast<const unsigned char*>(key), strlen(key),
                           reinterpret_cast<const unsigned char*>(msg), strlen(msg), out);
  if (rc != 0) return false;
  bytesToHex(out, sizeof(out), outHex, outLen);
  return true;
}

#ifdef OTA_VALIDATION_SELF_TEST
bool runValidationSelfTest(Logger* logger) {
  const char* psk = "test-key";
  const char* sha = "5e884898da28047151d0e56f8dc6292773603d0d6aabbdd62a11ef721d1542d8";  // sha256("password")
  const char* badSha = "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff";

  char hmac[kSha256HexBufLen]{};
  if (!computeHmac(psk, sha, hmac, sizeof(hmac))) {
    if (logger) logger->publish("ERR", "OTA self-test: hmac generation failed");
    return false;
  }

  if (!constantTimeEquals(hmac, hmac, kSha256HexLen)) {
    if (logger) logger->publish("ERR", "OTA self-test: constant time compare failed");
    return false;
  }

  char recomputed[kSha256HexBufLen]{};
  if (!computeHmac(psk, badSha, recomputed, sizeof(recomputed))) {
    if (logger) logger->publish("ERR", "OTA self-test: hmac recompute failed");
    return false;
  }

  if (constantTimeEquals(hmac, recomputed, kSha256HexLen)) {
    if (logger) logger->publish("ERR", "OTA self-test: hmac mismatch not detected");
    return false;
  }

  if (logger) logger->publish("DBG", "OTA self-test: validation helpers OK");
  return true;
}
#endif
}  // namespace

void OtaUpdater::begin(const OtaConfig& cfg, Logger* logger) {
  cfg_ = cfg;
  logger_ = logger;
}

bool OtaUpdater::perform(const char* cmdPayload) {
  if (!cfg_.host || !cfg_.path || !logger_) return false;

#ifdef OTA_VALIDATION_SELF_TEST
  static bool selfTestRan = false;
  if (!selfTestRan) {
    selfTestRan = true;
    if (!runValidationSelfTest(logger_)) {
      publishFail("auth", -1, "self_test_failed", 0);
      return false;
    }
  }
#endif

  CommandFields cmd{};
  if (!cfg_.psk || cfg_.psk[0] == '\0') {
    logger_->publish(cfg_.errLevel, "OTA missing PSK (define OTA_PSK build flag)");
    publishFail("auth", -1, "psk_missing", 0, "\"reason\":\"psk_missing\"");
    return false;
  }

  if (!cmdPayload || cmdPayload[0] == '\0') {
    logger_->publish(cfg_.errLevel, "OTA payload missing sha256/hmac");
    publishFail("auth", -1, "missing_sha256", 0, "\"reason\":\"missing_sha256\"");
    return false;
  }

  String payload = String(cmdPayload);
  payload.trim();
  if (payload.length() == 0) {
    logger_->publish(cfg_.errLevel, "OTA payload empty after trim");
    publishFail("auth", -1, "missing_sha256", 0, "\"reason\":\"missing_sha256\"");
    return false;
  }

  bool parseError = false;
  int pos = 0;
  while (pos < payload.length()) {
    int spaceIdx = payload.indexOf(' ', pos);
    String token = (spaceIdx < 0) ? payload.substring(pos) : payload.substring(pos, spaceIdx);
    pos = (spaceIdx < 0) ? payload.length() : spaceIdx + 1;
    token.trim();
    if (token.length() == 0) continue;
    int eqIdx = token.indexOf('=');
    if (eqIdx <= 0) {
      parseError = true;
      break;
    }
    String key = token.substring(0, eqIdx);
    String value = token.substring(eqIdx + 1);
    key.trim();
    value.trim();
    key.toLowerCase();

    if (key == "sha256") {
      value.toLowerCase();
      if (value.length() != kSha256HexLen || !isHex(value.c_str(), value.length())) {
        logger_->publish(cfg_.errLevel, "OTA invalid sha256 field");
        publishFail("auth", -1, "invalid_sha256", 0, "\"reason\":\"invalid_sha256\"");
        return false;
      }
      value.toCharArray(cmd.sha256, kSha256HexBufLen);
    } else if (key == "hmac") {
      value.toLowerCase();
      if (value.length() != kSha256HexLen || !isHex(value.c_str(), value.length())) {
        logger_->publish(cfg_.errLevel, "OTA invalid hmac field");
        publishFail("auth", -1, "invalid_hmac", 0, "\"reason\":\"invalid_hmac\"");
        return false;
      }
      value.toCharArray(cmd.hmac, kSha256HexBufLen);
    } else if (key == "url") {
      if (!parseUrl(value, cmd)) {
        parseError = true;
        break;
      }
    } else if (key == "path") {
      if (!value.startsWith("/")) {
        parseError = true;
        break;
      }
      if (static_cast<size_t>(value.length()) > kMaxPathLen) {
        parseError = true;
        break;
      }
      cmd.hasUrlPath = true;
      value.toCharArray(cmd.urlPath, kMaxPathLen + 1);
    } else {
      parseError = true;
      break;
    }
  }

  if (parseError) {
    logger_->publish(cfg_.errLevel, "OTA payload parse error");
    publishFail("auth", -1, "invalid_payload", 0, "\"reason\":\"invalid_payload\"");
    return false;
  }
  if (cmd.sha256[0] == '\0') {
    logger_->publish(cfg_.errLevel, "OTA missing sha256 field");
    publishFail("auth", -1, "missing_sha256", 0, "\"reason\":\"missing_sha256\"");
    return false;
  }
  if (cmd.hmac[0] == '\0') {
    logger_->publish(cfg_.errLevel, "OTA missing hmac field");
    publishFail("auth", -1, "missing_hmac", 0, "\"reason\":\"missing_hmac\"");
    return false;
  }

  const char* allowedHost = cfg_.allowedHost ? cfg_.allowedHost : cfg_.host;
  const char* allowedPathPrefix =
      (cfg_.allowedPathPrefix && cfg_.allowedPathPrefix[0]) ? cfg_.allowedPathPrefix : kDefaultPathPrefix;

  if (!allowedHost || allowedHost[0] == '\0') {
    logger_->publish(cfg_.errLevel, "OTA allowlist missing host");
    publishFail("auth", -1, "host_not_allowed", 0, "\"reason\":\"host_not_allowed\"");
    return false;
  }
  if (strcmp(cfg_.host, allowedHost) != 0) {
    String extra = String("\"reason\":\"host_not_allowed\",\"host\":\"") + cfg_.host + "\"";
    logger_->publish(cfg_.errLevel, "OTA host not allowlisted");
    publishFail("auth", -1, "host_not_allowed", 0, extra.c_str());
    return false;
  }
  if (!allowedPathPrefix || allowedPathPrefix[0] == '\0' || !startsWith(cfg_.path, allowedPathPrefix)) {
    String extra = String("\"reason\":\"path_not_allowed\",\"path\":\"") + cfg_.path + "\"";
    logger_->publish(cfg_.errLevel, "OTA path not under allowlist prefix");
    publishFail("auth", -1, "path_not_allowed", 0, extra.c_str());
    return false;
  }
  if (cmd.hasUrlHost && strcmp(cmd.urlHost, allowedHost) != 0) {
    String extra = String("\"reason\":\"host_not_allowed\",\"cmd_host\":\"") + cmd.urlHost + "\"";
    logger_->publish(cfg_.errLevel, "OTA command host rejected by allowlist");
    publishFail("auth", -1, "host_not_allowed", 0, extra.c_str());
    return false;
  }
  if (cmd.hasUrlPath) {
    if (!startsWith(cmd.urlPath, allowedPathPrefix)) {
      String extra = String("\"reason\":\"path_not_allowed\",\"cmd_path\":\"") + cmd.urlPath + "\"";
      logger_->publish(cfg_.errLevel, "OTA command path rejected by allowlist prefix");
      publishFail("auth", -1, "path_not_allowed", 0, extra.c_str());
      return false;
    }
    if (strcmp(cmd.urlPath, cfg_.path) != 0) {
      String extra = String("\"reason\":\"path_not_allowed\",\"cmd_path\":\"") + cmd.urlPath +
                     "\",\"expected_path\":\"" + cfg_.path + "\"";
      logger_->publish(cfg_.errLevel, "OTA command path mismatch");
      publishFail("auth", -1, "path_not_allowed", 0, extra.c_str());
      return false;
    }
  }

  char computedHmac[kSha256HexBufLen]{};
  if (!computeHmac(cfg_.psk, cmd.sha256, computedHmac, sizeof(computedHmac))) {
    logger_->publish(cfg_.errLevel, "OTA could not compute HMAC");
    publishFail("auth", -1, "hmac_error", 0, "\"reason\":\"hmac_error\"");
    return false;
  }
  if (!constantTimeEquals(computedHmac, cmd.hmac, kSha256HexLen)) {
    logger_->publish(cfg_.errLevel, "OTA HMAC mismatch");
    publishFail("auth", -1, "hmac_mismatch", 0, "\"reason\":\"hmac_mismatch\"");
    return false;
  }

  publishStart();

  EthernetClient client;
  client.setTimeout(kHeaderTimeoutMs);
  String url = String("http://") + cfg_.host + cfg_.path;
  logger_->publish(cfg_.infoLevel, String("CMD UPDATE -> HTTP OTA ") + url + " sha256=" + cmd.sha256);

  uint32_t connStart = millis();
  bool connected = client.connect(cfg_.host, cfg_.port);
  while (!connected && (millis() - connStart) < kConnectTimeoutMs) {
    delay(10);
    delay(0);
    connected = client.connect(cfg_.host, cfg_.port);
  }
  if (!connected) {
    logger_->publish(cfg_.errLevel, "OTA connect failed");
    publishFail("conn", -1, "connect_timeout", 0, "\"reason\":\"connect_timeout\"");
    client.stop();
    return false;
  }

  client.print("GET ");
  client.print(cfg_.path);
  client.print(" HTTP/1.1\r\nHost: ");
  client.print(cfg_.host);
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

  publishOk(totalWritten, actualSha);
  logger_->publish(cfg_.infoLevel, "OTA OK, rebooting");
  delay(500);
  ESP.restart();
  return true;
}

void OtaUpdater::publishStatus(const char* st, const String& dataJson, bool retained) {
  if (!cfg_.statusPublisher || !st) return;
  cfg_.statusPublisher(st, dataJson, retained);
}

void OtaUpdater::publishFail(const char* at, int code, const char* msg, size_t bytes, const char* extraJson) {
  if (!cfg_.statusPublisher) return;
  if (!at || !msg) return;
  String data = String("{\"at\":\"") + at + "\",\"code\":" + code + ",\"msg\":\"" + msg + "\"";
  if (bytes > 0) {
    data += ",\"bytes\":";
    data += String(bytes);
  }
  if (extraJson && extraJson[0] != '\0') {
    data += ",";
    data += extraJson;
  }
  data += "}";
  publishStatus("fail", data, true);
}

void OtaUpdater::publishOk(size_t bytes, const char* sha256Hex) {
  if (!cfg_.statusPublisher) return;
  String data = String("{\"bytes\":") + String(bytes);
  if (sha256Hex && sha256Hex[0]) {
    data += ",\"sha256\":\"";
    data += sha256Hex;
    data += "\"";
  }
  data += "}";
  publishStatus("ok", data, true);
}

void OtaUpdater::publishStart() {
  if (!cfg_.statusPublisher) return;
  const char* target = (cfg_.targetFw && cfg_.targetFw[0] != '\0') ? cfg_.targetFw : "?";
  String data = String("{\"to\":\"") + target + "\"}";
  publishStatus("start", data, true);
}

void OtaUpdater::publishProgress(int pct) {
  if (!cfg_.statusPublisher) return;
  if (pct < 0) pct = 0;
  if (pct > 100) pct = 100;
  String data = String("{\"pct\":") + pct + "}";
  publishStatus("prog", data, false);
}

}  // namespace Core
