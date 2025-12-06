#include <Arduino.h>
#include <SPI.h>
#include <Ethernet.h>
#include <PubSubClient.h>
#include <Update.h>
#include <Preferences.h>
#include <MFRC522.h>

// ======================= FIRMWARE INFO =======================
// Simple numeric fw version used in all MQTT payloads
static const char *FW_VERSION = "1.0";
// Human-readable description for changelog / docs
static const char *FW_DESC    = "star_slider 1.0 – 3x MFRC522 + solve button, repeatable SOLVED, HB/log/metric v2, prefs-solved";

// ======================= ETHERNET PINS =======================
#define ETH_CS   15
#define ETH_RST  27
#define ETH_SCK  18
#define ETH_MISO 19
#define ETH_MOSI 23

// ======================= NETWORK CONFIG ======================
byte mac[]       = { 0xDE, 0xAD, 0xBE, 0xEF, 0xFE, 0x56 };  // unique-ish MAC
IPAddress ip     (192, 168, 0, 17);
IPAddress dns    (0, 0, 0, 0);
IPAddress gw     (0, 0, 0, 0);
IPAddress subnet (255, 255, 255, 0);

// MQTT broker
IPAddress mqttServer(192, 168, 0, 10);
const uint16_t mqttPort = 1883;

// ======================= NODE TOPICS =========================
static const char *CLIENT_ID    = "star_slider";
static const char *TOPIC_HB     = "esc/room3/star_slider/hb";
static const char *TOPIC_CMD    = "esc/room3/star_slider/cmd";
static const char *TOPIC_LOG    = "esc/room3/star_slider/log";
static const char *TOPIC_METRIC = "esc/room3/star_slider/metric";
static const char *TOPIC_EVENT  = "esc/room3/star_slider/event";

// ======================= OTA CONFIG ==========================
static const char *OTA_HOST  = "192.168.0.10";
static const uint16_t OTA_PORT = 80;
static const char *OTA_PATH  = "/firmware/star_slider.bin";

// ======================= RFID + BUTTON CONFIG ================
static const uint8_t READER_COUNT = 3;

// MFRC522 wiring (shared SPI on 18/19/23, separate SS, shared RST)
static const int RC522_RST_PIN      = 22;
static const int RC522_SS_PINS[3]   = {5, 17, 16};   // reader 0,1,2 SS pins

// Solve button (to GND, internal pull-up)
static const int BTN_PIN            = 25;
static const unsigned long BTN_DEBOUNCE_MS = 50;

// RFID polling interval
static const unsigned long POLL_INTERVAL_MS = 150;

// Expected UIDs per reader (4 bytes each) – TODO: replace with your real UIDs
// reader 0
const byte UID_EXPECTED[READER_COUNT][4] = {
  {0xDE, 0xAD, 0xBE, 0x01},  // expected tag on reader 0
  {0xDE, 0xAD, 0xBE, 0x02},  // expected tag on reader 1
  {0xDE, 0xAD, 0xBE, 0x03}   // expected tag on reader 2
};

// ======================= STATE ===============================
EthernetClient ethClient;
PubSubClient mqtt(ethClient);
Preferences prefs;

MFRC522 mfrc522[READER_COUNT] = {
  MFRC522(RC522_SS_PINS[0], RC522_RST_PIN),
  MFRC522(RC522_SS_PINS[1], RC522_RST_PIN),
  MFRC522(RC522_SS_PINS[2], RC522_RST_PIN)
};

bool enabled = true;

unsigned long lastHbMs     = 0;
unsigned long lastMetricMs = 0;
unsigned long lastPollMs   = 0;

static const unsigned long HB_INTERVAL_MS     = 5000;
static const unsigned long METRIC_INTERVAL_MS = 10000;

// error counter used in HB/log payloads
uint32_t g_errorCount = 0;

// Last seen tag per reader
bool   tagValid[READER_COUNT]    = {false, false, false};
byte   tagUid[READER_COUNT][4]   = {{0}};
uint8_t tagSize[READER_COUNT]    = {0};

// Button edge tracking
bool btnPrevState = true;  // HIGH = released
unsigned long btnLastChangeMs = 0;

// Puzzle state
bool solvedFlag = false;
uint32_t solveAttempts = 0;
uint32_t solveSuccess  = 0;

// ======================= LOG / HB HELPERS ====================
void publishLog(const char *lvl, const String &msg, const String &dataJson = String()) {
  // lvl: "DBG","INF","WRN","ERR"
  String payload = String("{\"fw\":\"") + FW_VERSION +
                   "\",\"up\":" + String(millis() / 1000) +
                   ",\"lv\":\"" + lvl + "\",\"msg\":\"" + msg + "\"";
  if (dataJson.length() > 0) {
    payload += ",\"d\":" + dataJson;
  }
  payload += "}";
  mqtt.publish(TOPIC_LOG, payload.c_str());

  if (strcmp(lvl, "ERR") == 0) {
    g_errorCount++;
  }
}

void publishHeartbeatIfDue() {
  unsigned long now = millis();
  if (now - lastHbMs < HB_INTERVAL_MS) return;
  lastHbMs = now;

  const char *st;
  if (!enabled) {
    st = "warn";
  } else if (g_errorCount > 0) {
    st = "warn";
  } else {
    st = "ok";
  }

  String hb = String("{\"fw\":\"") + FW_VERSION +
              "\",\"up\":" + String(now / 1000) +
              ",\"st\":\"" + st + "\",\"err\":" + String(g_errorCount) +
              "}";
  mqtt.publish(TOPIC_HB, hb.c_str(), true);
}

void publishMetricsIfDue() {
  unsigned long now = millis();
  if (now - lastMetricMs < METRIC_INTERVAL_MS) return;
  lastMetricMs = now;

  // Metrics format: {"fw":FW_VERSION,"up":uptime_s,"k":"metric_key",...}
  String payload = String("{\"fw\":\"") + FW_VERSION +
                   "\",\"up\":" + String(now / 1000) +
                   ",\"k\":\"star_slider\"" +
                   ",\"solved\":" + (solvedFlag ? "1" : "0") +
                   ",\"attempts\":" + String(solveAttempts) +
                   ",\"success\":" + String(solveSuccess) +
                   "}";
  mqtt.publish(TOPIC_METRIC, payload.c_str());
}

// ======================= EVENT PUBLISH =======================
void publishSolvedEvent(uint32_t attemptIdx) {
  String payload = String("{\"fw\":\"") + FW_VERSION +
                   "\",\"up\":" + String(millis() / 1000) +
                   ",\"event\":\"SOLVED\",\"attempt\":" + String(attemptIdx) +
                   "}";
  mqtt.publish(TOPIC_EVENT, payload.c_str());
}

// ======================= OTA (HTTP pull) =====================
bool doHttpOta() {
  EthernetClient client;
  String url = String("http://") + OTA_HOST + OTA_PATH;
  publishLog("INF", String("CMD UPDATE -> HTTP OTA ") + url);

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
  long total = 0;

  while (client.connected() && total < contentLength) {
    int len = client.read(buf, sizeof(buf));
    if (len <= 0) {
      delay(10);
      continue;
    }
    Update.write(buf, len);
    total += len;
  }

  if (!Update.end(true)) {
    publishLog("ERR", "OTA Update.end failed");
    return false;
  }

  publishLog("INF", "OTA OK, rebooting");
  delay(500);
  ESP.restart();
  return true;
}

// ======================= RFID HELPERS ========================
bool uidEquals(const byte *a, const byte *b, uint8_t len) {
  for (uint8_t i = 0; i < len; i++) {
    if (a[i] != b[i]) return false;
  }
  return true;
}

void pollReader(uint8_t idx) {
  if (idx >= READER_COUNT) return;
  MFRC522 &r = mfrc522[idx];

  // If no new card, mark invalid and bail
  if (!r.PICC_IsNewCardPresent()) {
    tagValid[idx] = false;
    return;
  }
  if (!r.PICC_ReadCardSerial()) {
    tagValid[idx] = false;
    return;
  }

  // Copy up to 4 bytes of UID
  uint8_t len = (r.uid.size > 4) ? 4 : r.uid.size;
  memcpy(tagUid[idx], r.uid.uidByte, len);
  tagSize[idx]  = len;
  tagValid[idx] = true;

  r.PICC_HaltA();
  r.PCD_StopCrypto1();
}

void pollReadersIfDue() {
  unsigned long now = millis();
  if (now - lastPollMs < POLL_INTERVAL_MS) return;
  lastPollMs = now;

  for (uint8_t i = 0; i < READER_COUNT; i++) {
    pollReader(i);
  }
}

// ======================= PATTERN EVAL ========================
bool isCurrentPatternCorrect() {
  // For each reader, we require a valid tag and matching UID_EXPECTED[i]
  for (uint8_t i = 0; i < READER_COUNT; i++) {
    if (!tagValid[i]) return false;
    if (tagSize[i] < 4) return false;   // we expect at least 4-byte UIDs
    if (!uidEquals(tagUid[i], UID_EXPECTED[i], 4)) return false;
  }
  return true;
}

// ======================= BUTTON HANDLING =====================
void evaluateSolveAttempt() {
  // One "attempt": read current tags + check pattern + update state
  // Extra fresh read on press (in addition to background polling)
  for (uint8_t i = 0; i < READER_COUNT; i++) {
    pollReader(i);
  }

  solveAttempts++;

  if (isCurrentPatternCorrect()) {
    solvedFlag = true;
    solveSuccess++;
    prefs.putBool("solved", true);

    publishLog("INF", "pattern SOLVED");
    publishSolvedEvent(solveAttempts);
  } else {
    // log UIDs for debugging
    String d = "{";
    for (uint8_t i = 0; i < READER_COUNT; i++) {
      d += "\"r";
      d += String(i);
      d += "\":\"";
      if (tagValid[i] && tagSize[i] >= 4) {
        for (uint8_t b = 0; b < 4; b++) {
          if (b > 0) d += "-";
          if (tagUid[i][b] < 0x10) d += "0";
          d += String(tagUid[i][b], HEX);
        }
      } else {
        d += "none";
      }
      d += "\"";
      if (i < READER_COUNT - 1) d += ",";
    }
    d += "}";

    publishLog("INF", "pattern WRONG", d);
  }
}

// Called regularly from loop()
void updateButton() {
  bool raw = digitalRead(BTN_PIN);   // HIGH = released (pull-up), LOW = pressed
  unsigned long now = millis();

  if (raw != btnPrevState) {
    btnPrevState = raw;
    btnLastChangeMs = now;
    return;
  }

  if ((now - btnLastChangeMs) < BTN_DEBOUNCE_MS) return;

  // Detect falling edge: HIGH -> LOW
  static bool btnWasPressed = false;
  if (raw == LOW && !btnWasPressed) {
    // just pressed
    btnWasPressed = true;
    if (enabled) {
      evaluateSolveAttempt();
    } else {
      publishLog("WRN", "button press while DISABLED");
    }
  } else if (raw == HIGH && btnWasPressed) {
    // released
    btnWasPressed = false;
  }
}

// ======================= CMD HANDLING ========================
void handleCmd(const String &msg) {
  if (msg == "DISABLE") {
    enabled = false;
    publishLog("INF", "CMD DISABLE");
    return;
  }
  if (msg == "ENABLE") {
    enabled = true;
    publishLog("INF", "CMD ENABLE");
    return;
  }
  if (msg == "PING") {
    publishLog("DBG", "CMD PING");
    publishHeartbeatIfDue();
    return;
  }
  if (msg == "UPDATE") {
    doHttpOta();
    return;
  }
  if (msg == "REBOOT") {
    publishLog("INF", "CMD REBOOT");
    delay(200);
    ESP.restart();
    return;
  }

  publishLog("WRN", String("Unknown CMD: ") + msg);
}

// ======================= MQTT HANDLING =======================
void mqttCallback(char *topicC, byte *payload, unsigned int length) {
  String topic(topicC);
  String msg;
  msg.reserve(length);
  for (unsigned int i = 0; i < length; i++) msg += (char)payload[i];
  msg.trim();

  if (topic == TOPIC_CMD) {
    handleCmd(msg);
  }
}

void mqttReconnect() {
  while (!mqtt.connected()) {
    if (mqtt.connect(CLIENT_ID, TOPIC_HB, 0, true, "offline")) {
      publishLog("INF", "MQTT connected");
      mqtt.subscribe(TOPIC_CMD);
      publishHeartbeatIfDue();  // initial HB
    } else {
      delay(2000);
    }
  }
}

// ======================= ETHERNET INIT =======================
void setupEthernet() {
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
}

// ======================= STATE PERSISTENCE ===================
void loadStateFromPrefs() {
  prefs.begin("star_slider", false);
  solvedFlag = prefs.getBool("solved", false);

  if (solvedFlag) {
    publishLog("INF", "BOOT solved=1 (repeatable SOLVED events enabled)");
  } else {
    publishLog("INF", "BOOT solved=0");
  }
}

// ======================= SETUP / LOOP ========================
void setup() {
  Serial.begin(115200);
  delay(200);

  pinMode(BTN_PIN, INPUT_PULLUP);

  setupEthernet();
  mqttReconnect();

  // Init MFRC522 readers
  for (uint8_t i = 0; i < READER_COUNT; i++) {
    mfrc522[i].PCD_Init();
  }

  loadStateFromPrefs();
}

void loop() {
  if (!mqtt.connected()) {
    mqttReconnect();
  }
  mqtt.loop();

  pollReadersIfDue();
  updateButton();
  publishMetricsIfDue();
  publishHeartbeatIfDue();
}
