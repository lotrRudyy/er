#include "core_mqtt.h"

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

namespace Core {

MqttClient* MqttClient::self_ = nullptr;

void MqttClient::begin(const NetConfig& cfg, MqttDelegate* delegate) {
  cfg_ = cfg;
  delegate_ = delegate;
  self_ = this;

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
  return mqtt_.subscribe(topic, qos);
}

void MqttClient::startEthernet() {
  pinMode(ETH_RST, OUTPUT);
  digitalWrite(ETH_RST, LOW);
  delay(10);
  digitalWrite(ETH_RST, HIGH);
  delay(50);

  SPI.begin(ETH_SCK, ETH_MISO, ETH_MOSI, ETH_CS);
  Ethernet.init(ETH_CS);
  Ethernet.begin(cfg_.mac, cfg_.ip, cfg_.dns, cfg_.gateway, cfg_.subnet);
  ethernetReady_ = true;
}

void MqttClient::ensureConnected() {
  if (mqtt_.connected()) return;
  const uint32_t now = millis();
  if (now - lastReconnectAttemptMs_ < 2000) {
    return;
  }
  lastReconnectAttemptMs_ = now;

  if (!ethernetReady_) {
    startEthernet();
  }

  if (!cfg_.clientId || !cfg_.topicLwt) return;

  bool ok = mqtt_.connect(cfg_.clientId, cfg_.topicLwt, 0, true, "offline");
  if (ok) {
    handleConnected();
  }
}

void MqttClient::handleConnected() {
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

}  // namespace Core

