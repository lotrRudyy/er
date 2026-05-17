#include "core_mqtt.h"

#include <HardwareSerial.h>

#ifndef ETH_CS
#define ETH_CS 15
#endif
#ifndef ETH_RST
#define ETH_RST 27
#endif
#ifndef ETH_MISO
#define ETH_MISO 19
#endif
#ifndef ETH_MOSI
#define ETH_MOSI 23
#endif
#ifndef ETH_SCLK
#define ETH_SCLK 18
#endif
#ifndef ETH_SCK
#define ETH_SCK ETH_SCLK
#endif

namespace {

void serialMqttLog(const String& msg) {
  if (Serial) {
    Serial.println(String("[mqtt] ") + msg);
  }
}

String ipToString(const IPAddress& ip) {
  return String(ip[0]) + "." + String(ip[1]) + "." + String(ip[2]) + "." + String(ip[3]);
}

const char* ethernetHardwareStatusText(EthernetHardwareStatus status) {
  switch (status) {
    case EthernetNoHardware: return "no_hardware";
    case EthernetW5100: return "w5100";
    case EthernetW5200: return "w5200";
    case EthernetW5500: return "w5500";
    default: return "unknown";
  }
}

const char* ethernetLinkStatusText(EthernetLinkStatus status) {
  switch (status) {
    case Unknown: return "unknown";
    case LinkON: return "up";
    case LinkOFF: return "down";
    default: return "unknown";
  }
}

}  // namespace

namespace Core {

MqttClient* MqttClient::self_ = nullptr;

void MqttClient::begin(const NetConfig& cfg, MqttDelegate* delegate) {
  cfg_ = cfg;
  delegate_ = delegate;
  self_ = this;

  serialMqttLog(String("begin clientId=") + (cfg_.clientId ? cfg_.clientId : "<null>") +
                " mqtt=" + ipToString(cfg_.mqttServer) + ":" + String(cfg_.mqttPort));
  startEthernet();
  mqtt_.setServer(cfg_.mqttServer, cfg_.mqttPort);
  mqtt_.setCallback(MqttClient::mqttCallback);
  mqtt_.setKeepAlive(20);
  mqtt_.setSocketTimeout(8);
  mqtt_.setBufferSize(512);
}

void MqttClient::loop() {
  ensureConnected();
  if (mqtt_.connected()) {
    mqtt_.loop();
  }
}

bool MqttClient::publish(const char* topic, const char* payload, bool retained) {
  if (!topic || !payload) return false;
  return mqtt_.publish(topic, payload, retained);
}

bool MqttClient::publish(const char* topic, const String& payload, bool retained) {
  if (!topic) return false;
  return mqtt_.publish(topic, payload.c_str(), retained);
}

bool MqttClient::subscribe(const char* topic, uint8_t qos) {
  if (!topic) return false;
  const bool ok = mqtt_.subscribe(topic, qos);
  serialMqttLog(String("subscribe ") + (ok ? "OK " : "FAIL ") + topic + " qos=" + String(qos));
  return ok;
}

void MqttClient::startEthernet() {
  const int rstPin = (cfg_.ethRstPin >= 0) ? cfg_.ethRstPin : ETH_RST;
  serialMqttLog(String("ethernet reset pin=") + rstPin +
                " local_ip=" + ipToString(cfg_.ip) +
                " gw=" + ipToString(cfg_.gateway) +
                " dns=" + ipToString(cfg_.dns) +
                " subnet=" + ipToString(cfg_.subnet));
  pinMode(rstPin, OUTPUT);
  digitalWrite(rstPin, LOW);
  delay(10);
  digitalWrite(rstPin, HIGH);
  delay(50);

  SPI.begin(ETH_SCK, ETH_MISO, ETH_MOSI, ETH_CS);
  Ethernet.init(ETH_CS);
  Ethernet.begin(cfg_.mac, cfg_.ip, cfg_.dns, cfg_.gateway, cfg_.subnet);
  ethernetReady_ = true;

  serialMqttLog(String("ethernet ready hw=") + ethernetHardwareStatusText(Ethernet.hardwareStatus()) +
                " link=" + ethernetLinkStatusText(Ethernet.linkStatus()) +
                " local=" + ipToString(Ethernet.localIP()));
}

void MqttClient::ensureConnected() {
  if (mqtt_.connected()) return;
  const uint32_t now = millis();
  if (now - lastReconnectAttemptMs_ < 2000) {
    return;
  }
  lastReconnectAttemptMs_ = now;

  if (!ethernetReady_) {
    serialMqttLog("ethernet not ready; restarting ethernet");
    startEthernet();
  }

  if (!cfg_.clientId || !cfg_.topicLwt) {
    serialMqttLog("missing clientId or LWT topic; skipping connect");
    return;
  }

  serialMqttLog(String("connect attempt clientId=") + cfg_.clientId +
                " broker=" + ipToString(cfg_.mqttServer) + ":" + String(cfg_.mqttPort) +
                " link=" + ethernetLinkStatusText(Ethernet.linkStatus()) +
                " local=" + ipToString(Ethernet.localIP()));
  bool ok = mqtt_.connect(cfg_.clientId, cfg_.topicLwt, 0, true, "offline");
  if (ok) {
    serialMqttLog("connect OK");
    handleConnected();
  } else {
    serialMqttLog(String("connect FAIL state=") + mqtt_.state() +
                  " link=" + ethernetLinkStatusText(Ethernet.linkStatus()) +
                  " local=" + ipToString(Ethernet.localIP()));
  }
}

void MqttClient::handleConnected() {
  serialMqttLog(String("connected local=") + ipToString(Ethernet.localIP()));
  if (delegate_) {
    delegate_->onMqttConnected();
  }
}

void MqttClient::dispatchMessage(char* topic, uint8_t* payload, unsigned int len) {
  if (!delegate_) return;
  delegate_->onMqttMessage(topic, payload, len);
}

void MqttClient::mqttCallback(char* topic, uint8_t* payload, unsigned int len) {
  if (self_) {
    self_->dispatchMessage(topic, payload, len);
  }
}

String topic(const char* nodeId, const char* channel) {
  if (!nodeId || !channel) return "";

  String node = nodeId;
  if (node.startsWith("/")) {
    node.remove(0, 1);
  }
  while (node.endsWith("/")) {
    node.remove(node.length() - 1);
  }

  String ch = channel;
  if (ch.startsWith("/")) {
    ch.remove(0, 1);
  }
  while (ch.startsWith("/")) {
    ch.remove(0, 1);
  }

  if (node.length() == 0) return ch;
  if (ch.length() == 0) return node;

  String out;
  out.reserve(node.length() + ch.length() + 1);
  out = node;
  out += "/";
  out += ch;
  return out;
}

}  // namespace Core
