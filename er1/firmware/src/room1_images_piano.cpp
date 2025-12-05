#include <Arduino.h>
#include <SPI.h>
#include <Ethernet.h>
#include <PubSubClient.h>
#include <Update.h>

// ======================= FIRMWARE INFO =======================
// bumped version for naming cleanup
static const char *FW_VERSION = "images_piano_v1.7_4btn_eth_ota_solved_open_edge";

// ======================= ETHERNET PINS =======================
#define ETH_CS   15
#define ETH_RST  27
#define ETH_SCK  18
#define ETH_MISO 19
#define ETH_MOSI 23

// ======================= NETWORK CONFIG ======================
byte mac[]       = { 0xDE, 0xAD, 0xBE, 0xEF, 0xFE, 0x57 };  // unique-ish MAC
IPAddress ip     (192, 168, 0, 12); // room1 images_piano node (buttons=images, mic=piano)
IPAddress dns    (0, 0, 0, 0);
IPAddress gw     (0, 0, 0, 0);
IPAddress subnet (255, 255, 255, 0);

// MQTT broker
IPAddress mqttServer(192, 168, 0, 10);
const uint16_t mqttPort = 1883;

// ======================= NODE TOPICS =========================
static const char *CLIENT_ID    = "images_piano";
static const char *TOPIC_HB     = "esc/room1/images_piano/hb";
static const char *TOPIC_CMD    = "esc/room1/images_piano/cmd";
static const char *TOPIC_LOG    = "esc/room1/images_piano/log";
static const char *TOPIC_METRIC = "esc/room1/images_piano/metric";
static const char *TOPIC_EVENT  = "esc/room1/images_piano/event";

// Maglock controller topic for images lock
static const char *TOPIC_LOCK_IMAGES_CMD = "esc/ctrl/lock/images/cmd";

// ======================= OTA CONFIG ==========================
static const char *OTA_HOST  = "192.168.0.10";
static const uint16_t OTA_PORT = 80;
static const char *OTA_PATH  = "/firmware/images_piano.bin";

// ======================= BUTTON CONFIG =======================
// 4 buttons for the images riddle (on images_piano node)
static const int N_BTNS = 4;
static const int BTN_PINS[N_BTNS] = { 25, 26, 14, 12 };

// Debounce (ms) for button edges
static const unsigned long BTN_DEBOUNCE_MS = 30;

struct ButtonState {
  int pin;
  bool cur;
  bool prev;
  unsigned long lastChangeMs;
  uint32_t presses;
};

ButtonState btn[N_BTNS];

// Track "all 4 pressed" state to detect rising edge
bool allPressedPrev = false;

// ======================= GLOBALS =============================
EthernetClient ethClient;
PubSubClient mqtt(ethClient);

unsigned long lastHbMs     = 0;
unsigned long lastMetricMs = 0;

static const unsigned long HB_INTERVAL_MS     = 5000;
static const unsigned long METRIC_INTERVAL_MS = 1000;

bool enabled = true;

// ======================= LOG UTIL ============================
void publishLog(const char *lvl, const String &msg) {
  String payload = String("{\"lvl\":\"") + lvl + "\",\"msg\":\"" + msg + "\"}";
  mqtt.publish(TOPIC_LOG, payload.c_str());
}

// ======================= OTA (HTTP pull) =====================
bool doHttpOta() {
  EthernetClient client;
  String url = String("http://") + OTA_HOST + OTA_PATH;
  publishLog("INFO", String("CMD UPDATE -> HTTP OTA ") + url);

  if (!client.connect(OTA_HOST, OTA_PORT)) {
    publishLog("ERR", "OTA connect failed");
    return false;
  }

  client.print("GET ");
  client.print(OTA_PATH);
  client.print(" HTTP/1.1\r\nHost: ");
  client.print(OTA_HOST);
  client.print("\r\nConnection: close\r\n\r\n");

  long contentLength = -1;

  // Read headers
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
    publishLog("ERR", "OTA invalid content-length");
    return false;
  }

  if (!Update.begin(contentLength)) {
    publishLog("ERR", "OTA Update.begin failed");
    return false;
  }

  uint8_t buf[512];

  while (client.connected() || client.available()) {
    int len = client.read(buf, sizeof(buf));
    if (len <= 0) continue;
    Update.write(buf, len);
  }

  if (!Update.end(true)) {
    publishLog("ERR", "OTA Update.end failed");
    return false;
  }

  publishLog("INFO", "OTA OK, rebooting");
  delay(500);
  ESP.restart();
  return true;
}

// ======================= EVENT PUBLISH =======================
void publishSolvedEvent(const char *rid) {
  String payload = String("{\"type\":\"SOLVED\",\"rid\":\"") + rid + "\"}";
  mqtt.publish(TOPIC_EVENT, payload.c_str());
  publishLog("INFO", String("SOLVED event sent for rid=") + rid);
}

// ======================= MAGLOCK CONTROL =====================
void openImagesMaglock() {
  mqtt.publish(TOPIC_LOCK_IMAGES_CMD, "OPEN");
  publishLog("INFO", "Sent OPEN to images maglock");
}

// ======================= HEARTBEAT ===========================
void publishHeartbeatIfDue() {
  unsigned long now = millis();
  if (now - lastHbMs < HB_INTERVAL_MS) return;
  lastHbMs = now;

  String hb = String("{\"node\":\"images_piano\",\"fw\":\"") + FW_VERSION +
              "\",\"ip\":\"192.168.0.12\",\"uptime\":" + String(millis()/1000) +
              ",\"df_ok\":false"   // no DFPlayer on this node
              ",\"enabled\":" + (enabled ? "true" : "false") +
              "}";
  mqtt.publish(TOPIC_HB, hb.c_str(), true);
}

// ======================= METRICS =============================
void publishMetricsIfDue() {
  unsigned long now = millis();
  if (now - lastMetricMs < METRIC_INTERVAL_MS) return;
  lastMetricMs = now;

  uint32_t uptime = millis() / 1000;

  // Per-button metrics
  for (int i = 0; i < N_BTNS; i++) {
    ButtonState &b = btn[i];

    String payload = String("{\"t\":\"BTN\",\"up\":") + uptime +
                     ",\"en\":" + (enabled ? "1" : "0") +
                     ",\"i\":" + i +
                     ",\"pin\":" + b.pin +
                     ",\"state\":" + (b.cur ? 1 : 0) +   // 1 = released (HIGH), 0 = pressed (LOW)
                     ",\"presses\":" + b.presses +
                     "}";

    mqtt.publish(TOPIC_METRIC, payload.c_str());
  }

  // Overall "all pressed" status for debugging
  bool allPressedNow = true;
  for (int i = 0; i < N_BTNS; i++) {
    if (digitalRead(btn[i].pin) != LOW) {
      allPressedNow = false;
      break;
    }
  }

  String payloadAll = String("{\"t\":\"ALL\",\"up\":") + uptime +
                      ",\"all_pressed\":" + (allPressedNow ? 1 : 0) +
                      "}";
  mqtt.publish(TOPIC_METRIC, payloadAll.c_str());
}

// ======================= BUTTON HANDLING =====================
void handleButtonEdge(int idx, bool newState) {
  ButtonState &b = btn[idx];
  unsigned long now = millis();
  unsigned long dt  = now - b.lastChangeMs;   // time since last accepted edge

  if (dt < BTN_DEBOUNCE_MS) return;
  b.lastChangeMs = now;

  const char *stateStr = newState ? "RELEASED" : "PRESSED";

  // More detailed edge log for debugging flapping buttons
  String msg = String("BTN idx=") + idx +
               " pin=" + b.pin +
               " state=" + stateStr +
               " dt=" + dt + "ms" +
               " presses=" + b.presses;
  publishLog("INFO", msg);

  if (!newState) {
    // Count only presses
    b.presses++;
  }
}

// ======================= IMAGES SOLVE LOGIC ==================
// SOLVED only when:
//  - all 4 buttons are pressed at the same time (all LOW)
//  - we *just* transitioned from "not all pressed" to "all pressed"
//  - that gives exactly one SOLVED per "all-pressed" episode
void checkImagesSolved() {
  // Check current "all pressed" state using debounced values
  bool allPressedNow = true;
  for (int i = 0; i < N_BTNS; i++) {
    if (btn[i].cur != LOW) {  // INPUT_PULLUP: LOW = pressed
      allPressedNow = false;
      break;
    }
  }

  // Rising edge: previously not all pressed, now all pressed
  if (!allPressedPrev && allPressedNow) {
    publishLog("INFO", "ALL 4 BUTTONS PRESSED EDGE -> SOLVED (images riddle)");
    if (enabled) {
      publishSolvedEvent("images");
      openImagesMaglock();
    } else {
      publishLog("INFO", "SOLVED condition reached but node is DISABLED -> no event / no open");
    }
  }

  // Remember for next loop
  allPressedPrev = allPressedNow;
}

// ======================= MQTT HANDLING =======================
void handleCmd(const String &msg) {
  if (msg == "DISABLE") {
    enabled = false;
    publishLog("INFO", "CMD DISABLE");
    return;
  }
  if (msg == "ENABLE") {
    enabled = true;
    publishLog("INFO", "CMD ENABLE");
    return;
  }
  if (msg == "PING") {
    publishHeartbeatIfDue();
    return;
  }
  if (msg == "UPDATE") {
    doHttpOta();
    return;
  }
}

void mqttCallback(char *topicC, byte *payload, unsigned int length) {
  String topic(topicC);
  String msg;
  msg.reserve(length);
  for (unsigned int i = 0; i < length; i++) msg += (char)payload[i];
  msg.trim();

  if (topic == TOPIC_CMD) {
    publishLog("CMD", String("CMD topic=") + topic + " msg=" + msg);
    handleCmd(msg);
  }
}

void mqttReconnect() {
  while (!mqtt.connected()) {
    if (mqtt.connect(CLIENT_ID, TOPIC_HB, 0, true, "offline")) {
      publishLog("INFO", "MQTT connected");
      mqtt.subscribe(TOPIC_CMD);
      publishHeartbeatIfDue();  // initial HB
    } else {
      delay(2000);
    }
  }
}

// ======================= SETUP ===============================
void setup() {
  // Buttons
  for (int i = 0; i < N_BTNS; i++) {
    btn[i].pin          = BTN_PINS[i];
    btn[i].presses      = 0;
    btn[i].lastChangeMs = 0;
    pinMode(btn[i].pin, INPUT_PULLUP);
    bool lvl = digitalRead(btn[i].pin);
    btn[i].cur  = lvl;
    btn[i].prev = lvl;
  }

  // Initialise allPressedPrev to the *current* state so we don't
  // fire a SOLVED immediately on boot if all 4 are already held.
  allPressedPrev = true;
  for (int i = 0; i < N_BTNS; i++) {
    if (btn[i].cur != LOW) {
      allPressedPrev = false;
      break;
    }
  }

  // Ethernet reset
  pinMode(ETH_RST, OUTPUT);
  digitalWrite(ETH_RST, LOW);
  delay(50);
  digitalWrite(ETH_RST, HIGH);
  delay(50);

  SPI.begin(ETH_SCK, ETH_MISO, ETH_MOSI, ETH_CS);
  Ethernet.init(ETH_CS);
  Ethernet.begin(mac, ip, dns, gw, subnet);

  mqtt.setServer(mqttServer, mqttPort);
  mqtt.setCallback(mqttCallback);

  lastHbMs     = millis();
  lastMetricMs = millis();
}

// ======================= LOOP ================================
void loop() {
  if (!mqtt.connected()) {
    mqttReconnect();
  }
  mqtt.loop();

  // Button sampling & edge detection
  for (int i = 0; i < N_BTNS; i++) {
    ButtonState &b = btn[i];
    bool lvl = digitalRead(b.pin); // INPUT_PULLUP: HIGH = released, LOW = pressed
    b.cur = lvl;
    if (b.cur != b.prev) {
      handleButtonEdge(i, b.cur);
      b.prev = b.cur;
    }
  }

  // Check images riddle solve condition (all 4 pressed edge)
  checkImagesSolved();

  // Metrics + heartbeat
  publishMetricsIfDue();
  publishHeartbeatIfDue();
}
