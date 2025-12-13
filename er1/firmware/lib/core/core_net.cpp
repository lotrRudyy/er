#include <Arduino.h>
#include <SPI.h>
#include <Ethernet.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>

#include <config.hpp>        // must define ROOT, ROOM, DEVICE, MQTT_BROKER, MQTT_PORT, (optional) MQTT_USER/PASS
#include <core_state.hpp>    // for boot info in HB

// -------- Pins from build_flags (fallbacks if missing) --------
#ifndef ETH_CS
#  define ETH_CS 15
#endif
#ifndef ETH_RST
#  define ETH_RST 2
#endif
#ifndef HB_INTERVAL_MS
#  define HB_INTERVAL_MS 7000
#endif

// -------- Globals --------
static EthernetClient eth;
static PubSubClient   mqtt(eth);

static uint8_t mac[6];               // generated from chip ID
static uint32_t lastHbMs = 0;
static bool mqttConnectedOnce = false;

// -------- Topic helper --------
static String topicBase() {
  // er1/<room>/<device>/
  String t = String(ROOT) + "/" + ROOM + "/" + DEVICE + "/";
  return t;
}
String topic(const String& leaf) {
  return topicBase() + leaf;
}

// -------- Publish wrapper (QoS is accepted but ignored by PubSubClient for publish) --------
bool mqttPublish(const String& t, const String& p, bool retained, int /*qos*/) {
  return mqtt.publish(t.c_str(), p.c_str(), retained);
}

// -------- Incoming messages (extend if you want commands) --------
static void onMqttMsg(char* t, uint8_t* payload, unsigned int len) {
  (void)payload; (void)len;
  // Example: handle commands on er1/<room>/<device>/cmd if you subscribe to it
  // For now, no-op. You can parse JSON here.
}

// -------- Ethernet helpers --------
static void genMac(uint8_t out[6]) {
  // deterministic MAC from eFuse
  uint64_t id = ESP.getEfuseMac();
  out[0] = 0x02; // locally administered
  out[1] = 0x00;
  out[2] = (id >> 32) & 0xFF;
  out[3] = (id >> 24) & 0xFF;
  out[4] = (id >> 16) & 0xFF;
  out[5] = (id >> 8)  & 0xFF;
}

static void ethernetBegin() {
  pinMode(ETH_RST, OUTPUT);
  digitalWrite(ETH_RST, LOW);
  delay(5);
  digitalWrite(ETH_RST, HIGH);
  delay(50);

  Ethernet.init(ETH_CS);
  genMac(mac);
  Ethernet.begin(mac);   // DHCP
  // Optional: set a DHCP timeout watchdog if desired
}

// -------- MQTT connect (with LWT="offline") --------
static bool mqttConnect() {
  mqtt.setServer(MQTT_BROKER, MQTT_PORT);

  String willTopic = topic("hb");
  const char* willMsg = "offline";
  const bool  willRet = true;
  const uint8_t willQ = 0; // PubSubClient uses 0/1 for subscribe; publish QoS ignored, but LWT QoS is accepted.

  mqtt.setCallback(onMqttMsg);
  mqtt.setBufferSize(512);
  mqtt.setKeepAlive(20);
  mqtt.setSocketTimeout(8);
  mqtt.setCleanSession(true);
  mqtt.setWill(willTopic.c_str(), willMsg, willRet, willQ);

  String clientId = String("er1-") + ROOM + "-" + DEVICE + "-" + String((uint32_t)ESP.getEfuseMac(), HEX);

#ifdef MQTT_USER
  bool ok = mqtt.connect(clientId.c_str(), MQTT_USER, MQTT_PASS);
#else
  bool ok = mqtt.connect(clientId.c_str());
#endif

  if (ok) {
    mqttConnectedOnce = true;

    // On connect, publish an immediate HB (overwrites possible retained "offline")
    StaticJsonDocument<192> d;
    d["online"]  = true;
    d["uptime"]  = (uint32_t)(millis()/1000);
    d["ip"]      = Ethernet.localIP().toString();
    d["reboots"] = Core::State::info().reboot_count;
    String pay; serializeJson(d, pay);
    mqttPublish(topic("hb"), pay, /*retained*/true, 0);

    // State boot & BOOT event via Core::State
    Core::State::onConnected();

    // Example subscriptions (uncomment if you implement handlers):
    // mqtt.subscribe(topic("cmd").c_str(), 1);
  }
  return ok;
}

// -------- Public API for sketches --------
void netBegin() {
  ethernetBegin();
  mqttConnect();

  // Give Core::State the publish/topic functions
  Core::State::setPublishFn([](const String& t, const String& p, bool r, int qos){
    return mqttPublish(t, p, r, qos);
  });
  Core::State::setTopicFn([](const String& leaf){
    return topic(leaf);
  });
}

bool netConnected() {
  return mqtt.connected();
}

// Heartbeat payload each HB_INTERVAL_MS
static void publishHeartbeatIfDue() {
  const uint32_t now = millis();
  if (now - lastHbMs < HB_INTERVAL_MS) return;
  lastHbMs = now;

  StaticJsonDocument<224> d;
  d["online"]  = true;
  d["uptime"]  = (uint32_t)(now/1000);
  d["ip"]      = Ethernet.localIP().toString();
  d["reboots"] = Core::State::info().reboot_count;
  d["ts_last"] = Core::State::info().last_event_ts;
  String pay; serializeJson(d, pay);

  mqttPublish(topic("hb"), pay, /*retained*/true, 0);
}

void netLoop() {
  // Reconnect strategy
  if (!mqtt.connected()) {
    // Optional small backoff
    static uint32_t lastTry = 0;
    if (millis() - lastTry > 2000) {
      lastTry = millis();
      mqttConnect();
    }
  } else {
    mqtt.loop();
    publishHeartbeatIfDue();
  }

  // Let Core::State perform throttled persistence
  Core::State::loop();
}
