#include <Arduino.h>
#include <SPI.h>
#include <Ethernet.h>
#include <PubSubClient.h>
#include <Update.h>

// ======================= FIRMWARE INFO =======================
static const char *FW_VERSION = "chess_v1.1_eth_ota_uart_knockstyle";

// ======================= ETHERNET PINS =======================
#define ETH_CS   15
#define ETH_RST  27
#define ETH_SCK  18
#define ETH_MISO 19
#define ETH_MOSI 23

// ======================= NETWORK CONFIG ======================
byte mac[]       = { 0xDE, 0xAD, 0xBE, 0xEF, 0xFE, 0x57 };  // unique-ish MAC
IPAddress ip     (192, 168, 0, 13);
IPAddress dns    (0, 0, 0, 0);
IPAddress gw     (0, 0, 0, 0);
IPAddress subnet (255, 255, 255, 0);

// MQTT broker
IPAddress mqttServer(192, 168, 0, 10);
const uint16_t mqttPort = 1883;

// ======================= NODE TOPICS =========================
static const char *CLIENT_ID    = "chess";
static const char *TOPIC_HB     = "esc/room2/chess/hb";
static const char *TOPIC_CMD    = "esc/room2/chess/cmd";
static const char *TOPIC_LOG    = "esc/room2/chess/log";
static const char *TOPIC_METRIC = "esc/room2/chess/metric";
static const char *TOPIC_EVENT  = "esc/room2/chess/event";

// ======================= OTA CONFIG ==========================
static const char *OTA_HOST  = "192.168.0.10";
static const uint16_t OTA_PORT = 80;
static const char *OTA_PATH  = "/firmware/chess.bin";

// ======================= UART FROM RFID NODE =================
// RFID-ESP: TX2=25 -> NET-ESP RX2=26
//           RX2=26 <- NET-ESP TX2=25 (unused now, but wired)
#define UART_RX_PIN 26
#define UART_TX_PIN 25
HardwareSerial &rfidSerial = Serial2;

// ======================= CHESS TARGET UIDS ===================
// R0..R3 = king, queen, rook, horse
static const int N_READERS = 4;
static const char *TARGET_UIDS[N_READERS] = {
  "607A512F", // R0 - king
  "A06B512F", // R1 - queen
  "4015512F", // R2 - rook
  "C06B512F"  // R3 - horse
};

// ======================= GLOBALS =============================
EthernetClient ethClient;
PubSubClient   mqtt(ethClient);

// Readers state from RFID node
String readerUid[N_READERS];  // "NONE" or actual UID
unsigned long lastSnapshotMs = 0;

// UART buffer
static const size_t UART_BUF_SIZE = 128;
char   uartBuf[UART_BUF_SIZE];
size_t uartBufPos = 0;

// Timers
unsigned long lastHbMs     = 0;
unsigned long lastMetricMs = 0;
static const unsigned long HB_INTERVAL_MS     = 5000;
static const unsigned long METRIC_INTERVAL_MS = 1000;

bool enabled = true;
unsigned long lastSolvedMs = 0;
unsigned long solvedCount  = 0;

// ======================= FSM ================================
enum RiddleState {
  STATE_IDLE = 0,
  STATE_PARTIAL,
  STATE_SOLVED
};

RiddleState riddleState = STATE_IDLE;

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
void publishSolvedEvent() {
  String payload = String("{\"type\":\"SOLVED\",\"rid\":\"chess\"}");
  mqtt.publish(TOPIC_EVENT, payload.c_str());
}

// ======================= HEARTBEAT ===========================
void publishHeartbeatIfDue() {
  unsigned long now = millis();
  if (now - lastHbMs < HB_INTERVAL_MS) return;
  lastHbMs = now;

  String hb = String("{\"node\":\"chess\",\"fw\":\"") + FW_VERSION +
              "\",\"ip\":\"192.168.0.13\",\"uptime\":" + String(millis()/1000) +
              ",\"enabled\":" + (enabled ? "true" : "false") +
              ",\"solvedCount\":" + String(solvedCount) +
              "}";

  mqtt.publish(TOPIC_HB, hb.c_str(), true);
}

// ======================= METRICS =============================
void publishMetricsIfDue() {
  unsigned long now = millis();
  if (now - lastMetricMs < METRIC_INTERVAL_MS) return;
  lastMetricMs = now;

  unsigned long uptime = millis() / 1000;

  String patternStr;
  for (int i = 0; i < N_READERS; i++) {
    if (i > 0) patternStr += ",";
    patternStr += readerUid[i];
  }

  String payload = String("{\"t\":\"INF\",\"up\":") + uptime +
                   ",\"en\":" + (enabled ? "1" : "0") +
                   ",\"solves\":" + solvedCount +
                   ",\"pattern\":\"" + patternStr + "\"}";

  mqtt.publish(TOPIC_METRIC, payload.c_str());
}

// ======================= PATTERN / FSM =======================
bool patternCorrect() {
  for (int i = 0; i < N_READERS; i++) {
    if (readerUid[i] != TARGET_UIDS[i]) {
      return false;
    }
  }
  return true;
}

bool anyTagPresent() {
  for (int i = 0; i < N_READERS; i++) {
    if (readerUid[i] != "NONE") return true;
  }
  return false;
}

void evaluatePattern() {
  if (!enabled) return;

  bool correct    = patternCorrect();
  bool anyPresent = anyTagPresent();

  switch (riddleState) {
    case STATE_IDLE:
      if (correct) {
        riddleState = STATE_SOLVED;
        lastSolvedMs = millis();
        solvedCount++;
        publishSolvedEvent();
        publishLog("INFO", "CHESS_SOLVED");
      } else if (anyPresent) {
        riddleState = STATE_PARTIAL;
      }
      break;

    case STATE_PARTIAL:
      if (correct) {
        riddleState = STATE_SOLVED;
        lastSolvedMs = millis();
        solvedCount++;
        publishSolvedEvent();
        publishLog("INFO", "CHESS_SOLVED");
      } else if (!anyPresent) {
        riddleState = STATE_IDLE;
      }
      break;

    case STATE_SOLVED:
      // If they mess up the board again, allow re-solve later
      if (!correct) {
        if (anyPresent) {
          riddleState = STATE_PARTIAL;
        } else {
          riddleState = STATE_IDLE;
        }
      }
      break;
  }
}

// ======================= UART SNAPSHOT PARSING ===============
void applySnapshotTokens(char *line) {
  // Expected: SNAP R0=XXXX R1=XXXX R2=XXXX R3=XXXX
  char *saveptr;
  char *token = strtok_r(line, " \r\n", &saveptr);

  if (!token || strcmp(token, "SNAP") != 0) return;

  String newUid[N_READERS];
  for (int i = 0; i < N_READERS; i++) newUid[i] = "NONE";

  while ((token = strtok_r(nullptr, " \r\n", &saveptr)) != nullptr) {
    if (token[0] != 'R') continue;
    int idx = token[1] - '0';
    if (idx < 0 || idx >= N_READERS) continue;
    if (token[2] != '=') continue;

    const char *val = token + 3;
    if (strcmp(val, "NONE") == 0 || val[0] == '\0') {
      newUid[idx] = "NONE";
    } else {
      String s(val);
      s.toUpperCase();
      newUid[idx] = s;
    }
  }

  bool changed = false;
  for (int i = 0; i < N_READERS; i++) {
    if (readerUid[i] != newUid[i]) {
      readerUid[i] = newUid[i];
      changed = true;
    }
  }

  lastSnapshotMs = millis();
  if (changed) {
    evaluatePattern();
  }
}

void processUart() {
  while (rfidSerial.available() > 0) {
    char c = rfidSerial.read();
    if (c == '\n') {
      if (uartBufPos > 0 && uartBufPos < UART_BUF_SIZE) {
        uartBuf[uartBufPos] = '\0';
        applySnapshotTokens(uartBuf);
      }
      uartBufPos = 0;
    } else {
      if (uartBufPos < UART_BUF_SIZE - 1) {
        uartBuf[uartBufPos++] = c;
      }
    }
  }
}

// ======================= MQTT HANDLING =======================
void handleCmd(const String &msg) {
  if (msg == "DISABLE") {
    enabled = false;
    riddleState = STATE_IDLE;
    publishLog("INFO", "CMD DISABLE");
    return;
  }
  if (msg == "ENABLE") {
    enabled = true;
    riddleState = STATE_IDLE;
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
  Serial.begin(115200);
  delay(200);
  Serial.println();
  Serial.print("=== ROOM2 CHESS NET BOOT === ");
  Serial.println(FW_VERSION);

  // Init reader UIDs to NONE
  for (int i = 0; i < N_READERS; i++) {
    readerUid[i] = "NONE";
  }

  // UART from RFID node
  rfidSerial.begin(115200, SERIAL_8N1, UART_RX_PIN, UART_TX_PIN);

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

  processUart();
  publishMetricsIfDue();
  publishHeartbeatIfDue();
}
