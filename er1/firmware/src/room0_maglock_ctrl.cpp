#include <Arduino.h>
#include <SPI.h>
#include <Ethernet.h>
#include <PubSubClient.h>
#include <Update.h>

// ======================= FIRMWARE INFO =======================
static const char *FW_VERSION = "maglock_ctrl_v11.1";

// ======================= ETHERNET PINS =======================
#define ETH_CS   15
#define ETH_RST  27
#define ETH_SCK  18
#define ETH_MISO 19
#define ETH_MOSI 23

// ======================= NETWORK CONFIG ======================
byte mac[]       = { 0xDE, 0xAD, 0xBE, 0xEF, 0xFE, 0x55 };
IPAddress ip     (192, 168, 0, 11);
IPAddress dns    (0, 0, 0, 0);
IPAddress gw     (0, 0, 0, 0);
IPAddress subnet (255, 255, 255, 0);

// MQTT broker
IPAddress mqttServer(192, 168, 0, 10);
const uint16_t mqttPort = 1883;

// ======================= NODE TOPICS =========================
// This is the controller node itself (for HB/log/cmd/OTA)
static const char *CLIENT_ID    = "maglock_ctrl";
static const char *TOPIC_HB     = "esc/room0/maglock_ctrl/hb";
static const char *TOPIC_CMD    = "esc/room0/maglock_ctrl/cmd";
static const char *TOPIC_LOG    = "esc/room0/maglock_ctrl/log";

// Lock command/state topics pattern:
// esc/ctrl/lock/<id>/cmd
// esc/ctrl/lock/<id>/state

// ======================= OTA CONFIG ==========================
static const char *OTA_HOST  = "192.168.0.10";
static const uint16_t OTA_PORT = 80;
static const char *OTA_PATH  = "/firmware/maglock_ctrl.bin";

// ======================= LOCK CONFIG =========================
struct Lock {
  const char *id;       // "images", "r2", ...
  uint8_t     pin;      // GPIO
  bool        failSafe; // true = power=locked (fail-safe doors)
  bool        pulseActive;
  unsigned long pulseEndMs;
  bool        cooldownActive;
  unsigned long cooldownEndMs;
};

#define N_LOCKS 5

// Canonical lock IDs (mirror MQTT topics under esc/ctrl/lock/<id>/...)
static const char *LOCK_ID_IMAGES   = "images";
static const char *LOCK_ID_R2       = "r2";
static const char *LOCK_ID_R3       = "r3";
static const char *LOCK_ID_SLIDER   = "slider";
static const char *LOCK_ID_KNOCKING = "knocking";

// Mapping (per your current setup):
// images   -> GPIO26 (fail-secure, 1s pulse + 10s cooldown)
// r2       -> GPIO16 (fail-safe, OPEN = power OFF)
// r3       -> GPIO17 (fail-safe, OPEN = power OFF)
// slider   -> GPIO33 (fail-secure, 1s pulse + 10s cooldown)
// knocking -> GPIO25 (fail-secure, 1s pulse + 10s cooldown)
Lock locks[N_LOCKS] = {
  { LOCK_ID_IMAGES,   26, false, false, 0, false, 0 },
  { LOCK_ID_R2,       16, true,  false, 0, false, 0 },
  { LOCK_ID_R3,       17, true,  false, 0, false, 0 },
  { LOCK_ID_SLIDER,   33, false, false, 0, false, 0 },
  { LOCK_ID_KNOCKING, 25, false, false, 0, false, 0 }
};

// Fail-secure pulse length and cooldown (ms)
static const unsigned long FAILSEC_PULSE_MS    = 1000;
static const unsigned long FAILSEC_COOLDOWN_MS = 10000;

// ======================= GLOBALS =============================
EthernetClient ethClient;
PubSubClient mqtt(ethClient);

unsigned long lastHbMs = 0;
static const unsigned long HB_INTERVAL_MS = 5000;

// global enable flag
bool enabled = true;

// ======================= UTIL: LOG ===========================
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

  // HTTP GET
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

// ======================= LOCK HELPERS ========================
Lock *findLockById(const String &id) {
  for (int i = 0; i < N_LOCKS; i++) {
    if (id.equals(locks[i].id)) return &locks[i];
  }
  return nullptr;
}

void publishLockState(Lock &l, const char *state) {
  String topic = String("esc/ctrl/lock/") + l.id + "/state";
  String payload = String("{\"id\":\"") + l.id + "\",\"state\":\"" + state + "\"}";
  mqtt.publish(topic.c_str(), payload.c_str());
}

void setLockOutput(Lock &l, bool powered) {
  digitalWrite(l.pin, powered ? HIGH : LOW);
}

void openFailSafe(Lock &l) {
  // fail-safe: power = locked, no power = open
  l.pulseActive = false;
  l.cooldownActive = false;
  setLockOutput(l, false);  // remove power -> door open
  publishLockState(l, "OPEN");
}

void closeFailSafe(Lock &l) {
  l.pulseActive = false;
  l.cooldownActive = false;
  setLockOutput(l, true);   // power -> locked
  publishLockState(l, "CLOSED");
}

// Fail-secure OPEN with pulse + cooldown heat protection.
void pulseFailSecure(Lock &l) {
  unsigned long now = millis();
  if (l.pulseActive || l.cooldownActive) {
    return;
  }
  l.pulseActive = true;
  l.pulseEndMs = now + FAILSEC_PULSE_MS;
  l.cooldownActive = false;
  l.cooldownEndMs = 0;
  setLockOutput(l, true);   // power -> unlock
  publishLockState(l, "OPENING");
}

void stopFailSecurePulse(Lock &l) {
  l.pulseActive = false;
  l.cooldownActive = true;
  l.cooldownEndMs = millis() + FAILSEC_COOLDOWN_MS;
  setLockOutput(l, false);  // no power when idle
  publishLockState(l, "COOLDOWN");
}

void handleLockOpen(Lock &l) {
  if (l.failSafe) {
    // r2 / r3: OPEN = power OFF
    openFailSafe(l);
  } else {
    // images / slider / knocking: OPEN = power ON for 1 second (with cooldown)
    pulseFailSecure(l);
  }
}

void handleLockClose(Lock &l) {
  if (l.failSafe) {
    closeFailSafe(l);
  } else {
    // ensure coil is off; do not cancel cooldown
    l.pulseActive = false;
    setLockOutput(l, false);
    publishLockState(l, "CLOSED");
  }
}

// ======================= HEARTBEAT ===========================
void publishHeartbeatIfDue() {
  unsigned long now = millis();
  if (now - lastHbMs < HB_INTERVAL_MS) return;
  lastHbMs = now;

  String hb = String("{\"node\":\"maglock_ctrl\",\"fw\":\"") + FW_VERSION +
              "\",\"ip\":\"192.168.0.11\",\"uptime\":" + String(millis()/1000) +
              "}";
  mqtt.publish(TOPIC_HB, hb.c_str(), true);
}

// ======================= MQTT HANDLING =======================
void handleLockCmdTopic(const String &topic, const String &msg) {
  if (!enabled) {
    // ignore manual lock commands when disabled
    return;
  }

  // topic: esc/ctrl/lock/<id>/cmd
  int baseLen = String("esc/ctrl/lock/").length();
  int endIdx = topic.lastIndexOf("/cmd");
  if (endIdx <= baseLen) return;
  String id = topic.substring(baseLen, endIdx);

  Lock *l = findLockById(id);
  if (!l) {
    publishLog("WARN", String("Unknown lock id in cmd: ") + id);
    return;
  }

  if (msg == "OPEN") {
    handleLockOpen(*l);
  } else if (msg == "CLOSE") {
    handleLockClose(*l);
  } else {
    // "PULSE" or anything else is effectively removed/ignored
    publishLog("WARN", String("Unknown lock cmd: ") + msg);
  }
}

void handleCtrlCmd(const String &msg) {
  if (msg == "DISABLE") {
    enabled = false;
    publishLog("INFO", "CTRL DISABLE");
    return;
  }

  if (msg == "ENABLE") {
    enabled = true;
    publishLog("INFO", "CTRL ENABLE");
    return;
  }

  if (msg == "PING") {
    publishHeartbeatIfDue(); // immediate HB
  } else if (msg == "UPDATE") {
    doHttpOta();
  }
}

// handle SOLVED events from riddles
void handleGameEvent(const String &topic, const String &payload) {
  if (!enabled) {
    // ignore auto-open logic when disabled
    return;
  }

  // We only care about SOLVED from rid "knocking" for now.
  // Minimal parse: just search substrings.
  if (payload.indexOf("\"type\":\"SOLVED\"") < 0) return;

  // Check rid
  String ridMarker = String("\"rid\":\"") + LOCK_ID_KNOCKING + "\"";
  if (payload.indexOf(ridMarker) < 0) return;

  Lock *l = findLockById(String(LOCK_ID_KNOCKING));
  if (!l) {
    publishLog("ERR", "No lock mapping for rid=knocking");
    return;
  }

  publishLog("INFO", String("EVENT SOLVED rid=") + LOCK_ID_KNOCKING + " -> OPEN lock " + LOCK_ID_KNOCKING);
  handleLockOpen(*l);
}

void mqttCallback(char *topicC, byte *payload, unsigned int length) {
  String topic(topicC);
  String msg;
  msg.reserve(length);
  for (unsigned int i = 0; i < length; i++) msg += (char)payload[i];
  msg.trim();

  // Log basic info for debugging
  if (topic == TOPIC_CMD || topic.startsWith("esc/ctrl/lock/")) {
    publishLog("CMD", String(topic) + " = " + msg);
  }

  if (topic == TOPIC_CMD) {
    handleCtrlCmd(msg);
    return;
  }

  if (topic.startsWith("esc/ctrl/lock/") && topic.endsWith("/cmd")) {
    handleLockCmdTopic(topic, msg);
    return;
  }

  // Any sensor/riddle event
  if (topic.endsWith("/event")) {
    handleGameEvent(topic, msg);
    return;
  }
}

void mqttReconnect() {
  while (!mqtt.connected()) {
    if (mqtt.connect(CLIENT_ID, TOPIC_HB, 0, true, "offline")) {
      publishLog("INFO", "MQTT connected");

      // Subscribe to controller cmd
      mqtt.subscribe(TOPIC_CMD);

      // Subscribe to manual lock commands
      mqtt.subscribe("esc/ctrl/lock/+/cmd");

      // Subscribe to ALL game events
      mqtt.subscribe("esc/+/+/event");

      // Initial heartbeat
      publishHeartbeatIfDue();
    } else {
      delay(2000);
    }
  }
}

// ======================= SETUP ===============================
void setup() {
  // Init lock pins (all off -> LOW)
  for (int i = 0; i < N_LOCKS; i++) {
    pinMode(locks[i].pin, OUTPUT);
    digitalWrite(locks[i].pin, LOW);
    locks[i].pulseActive = false;
    locks[i].pulseEndMs  = 0;
    locks[i].cooldownActive = false;
    locks[i].cooldownEndMs  = 0;
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

  lastHbMs = millis();
}

// ======================= LOOP ================================
void loop() {
  if (!mqtt.connected()) {
    mqttReconnect();
  }
  mqtt.loop();

  unsigned long now = millis();

  // Handle fail-secure pulse timeouts (even if disabled – safety)
  for (int i = 0; i < N_LOCKS; i++) {
    Lock &l = locks[i];
    if (!l.failSafe) {
      if (l.pulseActive && (long)(now - l.pulseEndMs) >= 0) {
        stopFailSecurePulse(l);
      }
      if (l.cooldownActive && (long)(now - l.cooldownEndMs) >= 0) {
        l.cooldownActive = false;
        publishLockState(l, "IDLE");
      }
    }
  }

  publishHeartbeatIfDue();
}
