#include "core_ota.h"

#include <cstring>

namespace Core {

namespace {
constexpr uint32_t kConnectTimeoutMs = 5000;
constexpr uint32_t kHeaderTimeoutMs = 5000;
constexpr uint32_t kBodyStallTimeoutMs = 4000;
}  // namespace

void OtaUpdater::begin(const OtaConfig& cfg, Logger* logger) {
  cfg_ = cfg;
  logger_ = logger;
}

bool OtaUpdater::perform() {
  if (!cfg_.host || !cfg_.path || !logger_) return false;

  publishStart();

  EthernetClient client;
  client.setTimeout(kHeaderTimeoutMs);
  String url = String("http://") + cfg_.host + cfg_.path;
  logger_->publish(cfg_.infoLevel, String("CMD UPDATE -> HTTP OTA ") + url);

  uint32_t connStart = millis();
  bool connected = client.connect(cfg_.host, cfg_.port);
  while (!connected && (millis() - connStart) < kConnectTimeoutMs) {
    delay(10);
    connected = client.connect(cfg_.host, cfg_.port);
  }
  if (!connected) {
    logger_->publish(cfg_.errLevel, "OTA connect failed");
    publishFail("conn", -1, "connect_timeout", 0);
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
    publishFail("http", httpCode == 0 ? -1 : httpCode, "status", 0);
    client.stop();
    return false;
  }

  long contentLength = -1;
  String remoteVersion;
  while (client.connected()) {
    if (millis() - headerStart > kHeaderTimeoutMs) {
      logger_->publish(cfg_.errLevel, "OTA header timeout");
      publishFail("hdr", -1, "timeout", 0);
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
    publishFail("hdr", -1, "len", 0);
    client.stop();
    return false;
  }

  if (remoteVersion.length() > 0 && cfg_.targetFw && strlen(cfg_.targetFw) > 0) {
    if (remoteVersion.equals(String(cfg_.targetFw))) {
      logger_->publish(cfg_.infoLevel, String("OTA skip: version match (") + remoteVersion + ")");
      publishFail("ver", 0, "version_match", 0);
      client.stop();
      return false;
    }
  }

  if (!Update.begin(contentLength)) {
    logger_->publish(cfg_.errLevel, "OTA Update.begin failed");
    publishFail("write", Update.getError(), "begin", 0);
    client.stop();
    return false;
  }

  uint8_t buf[512];
  size_t totalWritten = 0;
  uint32_t lastProgressMs = 0;
  int lastPct = -1;
  uint32_t lastDataMs = millis();
  bool startedUpdate = true;

  while (client.connected() || client.available()) {
    int len = client.read(buf, sizeof(buf));
    if (len <= 0) {
      if (millis() - lastDataMs > kBodyStallTimeoutMs) {
        logger_->publish(cfg_.errLevel, "OTA data stall");
        publishFail("conn", -1, "stall", totalWritten);
        Update.abort();
        client.stop();
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
      publishFail("write", Update.getError(), "write", totalWritten);
      Update.abort();
      client.stop();
      return false;
    }
    totalWritten += written;

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
    publishFail("hdr", -1, "size_mismatch", totalWritten);
    Update.abort();
    client.stop();
    return false;
  }

  if (!Update.end(true)) {
    logger_->publish(cfg_.errLevel, "OTA Update.end failed");
    publishFail("end", Update.getError(), "end", totalWritten);
    Update.abort();
    client.stop();
    return false;
  }

  publishOk(totalWritten);
  logger_->publish(cfg_.infoLevel, "OTA OK, rebooting");
  delay(500);
  ESP.restart();
  return true;
}

void OtaUpdater::publishStatus(const char* st, const String& dataJson, bool retained) {
  if (!cfg_.statusPublisher || !st) return;
  cfg_.statusPublisher(st, dataJson, retained);
}

void OtaUpdater::publishFail(const char* at, int code, const char* msg, size_t bytes) {
  if (!cfg_.statusPublisher) return;
  if (!at || !msg) return;
  String data = String("{\"at\":\"") + at + "\",\"code\":" + code + ",\"msg\":\"" + msg + "\"";
  if (bytes > 0) {
    data += ",\"bytes\":";
    data += String(bytes);
  }
  data += "}";
  publishStatus("fail", data, true);
}

void OtaUpdater::publishOk(size_t bytes) {
  if (!cfg_.statusPublisher) return;
  String data = String("{\"bytes\":") + String(bytes) + "}";
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
