#pragma once

#include <Arduino.h>
#include <SPI.h>
#include <Ethernet.h>
#include <PubSubClient.h>

namespace Core {

struct NetConfig {
  uint8_t mac[6];
  IPAddress ip;
  IPAddress dns;
  IPAddress gateway;
  IPAddress subnet;
  IPAddress mqttServer;
  uint16_t mqttPort = 1883;
  const char* clientId = nullptr;
  const char* topicLwt = nullptr;
};

class MqttDelegate {
public:
  virtual ~MqttDelegate() = default;
  virtual void onMqttConnected() = 0;
  virtual void onMqttMessage(const char* topic, const uint8_t* payload, size_t length) = 0;
};

class MqttClient {
public:
  void begin(const NetConfig& cfg, MqttDelegate* delegate);

  void loop();
  bool publish(const char* topic, const char* payload, bool retained = false);
  bool publish(const char* topic, const String& payload, bool retained = false);
  bool subscribe(const char* topic, uint8_t qos = 0);

  bool connected() { return mqtt_.connected(); }
  IPAddress localIp() const { return Ethernet.localIP(); }
  PubSubClient& client() { return mqtt_; }

private:
  void startEthernet();
  void ensureConnected();
  void handleConnected();
  void dispatchMessage(char* topic, uint8_t* payload, unsigned int len);

  static void mqttCallback(char* topic, uint8_t* payload, unsigned int len);

  NetConfig cfg_{};
  EthernetClient eth_;
  PubSubClient mqtt_{eth_};
  MqttDelegate* delegate_ = nullptr;
  uint32_t lastReconnectAttemptMs_ = 0;
  bool ethernetReady_ = false;
  static MqttClient* self_;
};

}  // namespace Core
