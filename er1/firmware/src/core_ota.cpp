#include "core_ota.h"

namespace Core {

void OtaUpdater::begin(const OtaConfig& cfg, Logger* logger) {
  cfg_ = cfg;
  logger_ = logger;
}

bool OtaUpdater::perform() {
  if (!cfg_.host || !cfg_.path || !logger_) return false;

  publishStart();

  EthernetClient client;
  String url = String("http://") + cfg_.host + cfg_.path;
  logger_->publish(cfg_.infoLevel, String("CMD UPDATE -> HTTP OTA ") + url);

  if (!client.connect(cfg_.host, cfg_.port)) {
    logger_->publish(cfg_.errLevel, "OTA connect failed");
    publishFail("conn", -1, "connect", 0);
    return false;
  }

  client.print("GET ");
  client.print(cfg_.path);
  client.print(" HTTP/1.1\r\nHost: ");
  client.print(cfg_.host);
  client.print("\r\nConnection: close\r\n\r\n");

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
  while (client.connected()) {
    String line = client.readStringUntil('\n');
    if (line == "\r" || line.length() == 0) break;
    String low = line;
    low.toLowerCase();
    if (low.startsWith("content-length:")) {
      low.replace("content-length:", "");
      low.trim();
      contentLength = low.toInt();
    }
  }

  if (contentLength <= 0) {
    logger_->publish(cfg_.errLevel, "OTA invalid content-length");
    publishFail("hdr", -1, "len", 0);
    client.stop();
    return false;
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

  while (client.connected() || client.available()) {
    int len = client.read(buf, sizeof(buf));
    if (len <= 0) continue;
    size_t written = Update.write(buf, len);
    if (written != static_cast<size_t>(len)) {
      logger_->publish(cfg_.errLevel, "OTA Update.write failed");
      totalWritten += written;
      publishFail("write", Update.getError(), "write", totalWritten);
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
  }

  if (!Update.end(true)) {
    logger_->publish(cfg_.errLevel, "OTA Update.end failed");
    publishFail("end", Update.getError(), "end", totalWritten);
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
