#include <Arduino.h>
#include <SPI.h>
#include <Ethernet.h>
#include <PubSubClient.h>
#include <Update.h>
#include <DFRobotDFPlayerMini.h>

// ======================= FIRMWARE INFO =======================
static const char *FW_VERSION = "knocking_v5.14_3piezo_eth_ota_df_seq_timer_log";

// ======================= ETHERNET PINS =======================
#define ETH_CS   15
#define ETH_RST  27
#define ETH_SCK  18
#define ETH_MISO 19
#define ETH_MOSI 23

// ======================= NETWORK CONFIG ======================
byte mac[]       = { 0xDE, 0xAD, 0xBE, 0xEF, 0xFE, 0x56 };  // unique-ish MAC
IPAddress ip     (192, 168, 0, 14);
IPAddress dns    (0, 0, 0, 0);
IPAddress gw     (0, 0, 0, 0);
IPAddress subnet (255, 255, 255, 0);

// MQTT broker
IPAddress mqttServer(192, 168, 0, 10);
const uint16_t mqttPort = 1883;

// ======================= NODE TOPICS =========================
static const char *CLIENT_ID    = "knocking";
static const char *TOPIC_HB     = "esc/room3/knocking/hb";
static const char *TOPIC_CMD    = "esc/room3/knocking/cmd";
static const char *TOPIC_LOG    = "esc/room3/knocking/log";
static const char *TOPIC_METRIC = "esc/room3/knocking/metric";
static const char *TOPIC_EVENT  = "esc/room3/knocking/event";

// ======================= OTA CONFIG ==========================
static const char *OTA_HOST  = "192.168.0.10";
static const uint16_t OTA_PORT = 80;
static const char *OTA_PATH  = "/firmware/knocking.bin";

// ======================= DFPLAYER CONFIG =====================
HardwareSerial &dfSerial = Serial2;
DFRobotDFPlayerMini dfPlayer;
bool dfOk = false;

// ======================= PIEZO CONFIG ========================
static const int N_SENSORS = 3;
static const int PIEZO_PINS[N_SENSORS] = { 32, 33, 34 };

// Raw threshold for considering a knock
static const uint16_t KNOCK_RAW_THR = 1200;
// Release threshold (must fall below this to re-arm)
static const uint16_t KNOCK_REL_THR = 800;
// Debounce per sensor (ms)
static const unsigned long KNOCK_DEBOUNCE_MS = 120;

// Metric/EMA config
struct PiezoState {
  int      pin;
  uint32_t sum;
  uint16_t samples;
  uint16_t avg;
  uint16_t base;
  uint16_t maxVal;
  uint16_t lastRaw;
};
PiezoState piezo[N_SENSORS];

struct HitState {
  bool latched;
};
HitState hitState[N_SENSORS];

unsigned long lastMetricMs = 0;
// we keep these constants for future use, but the spam is controlled by a hard 10s gate now
static const unsigned long METRIC_INTERVAL_MS = 1000;
static const uint32_t METRIC_IDLE_INTERVAL_MS   = 10000;
static const uint16_t METRIC_ACTIVITY_THRESHOLD = 150;
static uint32_t lastIdleMetricMs = 0;

// ======================= SEQUENCE CONFIG =====================
// Target pattern: 0,0,0,0,1,1,2,2,2
static const int SEQ_EXPECT_LEN = 9;
static const int SEQ_EXPECT[SEQ_EXPECT_LEN] = {0,0,0,0,1,1,2,2,2};

static const int SEQ_MAX_LEN = 16;
int seqBuf[SEQ_MAX_LEN];
int seqLen = 0;

unsigned long lastSeqActivityMs = 0;
static const unsigned long SEQ_TIMEOUT_MS = 3000; // 3 s after last knock -> evaluate

// Per-sensor debounce
unsigned long lastKnockMs[N_SENSORS];

// ======================= GLOBALS =============================
EthernetClient ethClient;
PubSubClient mqtt(ethClient);

unsigned long lastHbMs = 0;
static const unsigned long HB_INTERVAL_MS = 5000;

bool enabled = true;

// ======================= LOG UTIL ============================
void publishLog(const char *lvl, const String &msg) {
  // Rate limit non-critical logs to max 1 / 10s
  static unsigned long lastLogMs = 0;
  unsigned long now = millis();

  bool isCritical = (strcmp(lvl, "ERR") == 0) || (strcmp(lvl, "CMD") == 0);
  if (!isCritical && (now - lastLogMs < 10000)) {
    return;
  }
  lastLogMs = now;

  String payload = String("{\"fw\":\"") + FW_VERSION +
                   "\",\"lvl\":\"" + lvl +
                   "\",\"msg\":\"" + msg + "\"}";
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
  String payload = String("{\"type\":\"SOLVED\",\"rid\":\"knocking\"}");
  mqtt.publish(TOPIC_EVENT, payload.c_str());
}

// ======================= SEQUENCE HELPERS ====================
void resetSequence() {
  seqLen = 0;
  lastSeqActivityMs = 0;
}

void playFailSoundX5() {
  if (!dfOk) return;
  for (int i = 0; i < 5; i++) {
    dfPlayer.play(1);  // fail sound on track 1
    delay(300);
  }
}

void evaluateSequence() {
  if (seqLen == 0) return;

  // Buffer string for debug
  String seqStr;
  for (int i = 0; i < seqLen; i++) {
    if (i > 0) seqStr += ",";
    seqStr += seqBuf[i];
  }

  String evalMsg = String("SEQ_EVAL len=") + seqLen + " buf=[" + seqStr + "]";
  publishLog("INFO", evalMsg);

  bool ok = true;
  if (seqLen != SEQ_EXPECT_LEN) {
    ok = false;
  } else {
    for (int i = 0; i < SEQ_EXPECT_LEN; i++) {
      if (seqBuf[i] != SEQ_EXPECT[i]) {
        ok = false;
        break;
      }
    }
  }

  if (ok) {
    publishLog("INFO", "SEQUENCE OK -> SOLVED & open maglock");
    if (dfOk) {
      dfPlayer.play(2);  // success sound (track 2)
    }
    publishSolvedEvent();
    resetSequence();
  } else {
    publishLog("INFO", "SEQUENCE FAIL -> FAIL SOUND x5 & reset");
    playFailSoundX5();
    resetSequence();
  }
}

void evaluateSequenceIfDue() {
  if (seqLen == 0) return;
  unsigned long now = millis();
  if (now - lastSeqActivityMs < SEQ_TIMEOUT_MS) return;
  // 3 seconds of silence -> evaluate whatever we have
  evaluateSequence();
}

// ======================= SOUND HANDLING ======================
void playKnockSound(int idx) {
  if (!dfOk) return;
  int count = idx + 1;  // 0 -> 1, 1 -> 2, 2 -> 3
  for (int i = 0; i < count; i++) {
    dfPlayer.play(1);
    delay(200);  // small gap between beeps
  }
}

// ======================= KNOCK HANDLING ======================
void registerKnock(int idx, uint16_t raw) {
  if (!enabled) return;
  unsigned long now = millis();
  if (now - lastKnockMs[idx] < KNOCK_DEBOUNCE_MS) return;
  lastKnockMs[idx] = now;

  publishLog("INFO", String("KNOCK on sensor ") + idx + " raw=" + raw);

  // Append to sequence buffer
  if (seqLen < SEQ_MAX_LEN) {
    seqBuf[seqLen++] = idx;
  }

  // Update activity timestamp for timer-based evaluation
  lastSeqActivityMs = now;

  // Per-sensor beep count (all on track 1):
  // sensor 0 -> 1 beep; 1 -> 2 beeps; 2 -> 3 beeps
  playKnockSound(idx);
}

// ======================= HEARTBEAT ===========================
void publishHeartbeatIfDue() {
  unsigned long now = millis();
  if (now - lastHbMs < HB_INTERVAL_MS) return;
  lastHbMs = now;

  String hb = String("{\"node\":\"knocking\",\"fw\":\"") + FW_VERSION +
              "\",\"ip\":\"192.168.0.14\",\"uptime\":" + String(millis()/1000) +
              ",\"df_ok\":" + (dfOk ? "true" : "false") +
              ",\"enabled\":" + (enabled ? "true" : "false") +
              "}";
  mqtt.publish(TOPIC_HB, hb.c_str(), true);
}

// ======================= METRICS =============================
// Now: single aggregated line, max once every 10 seconds.
void publishMetricsIfDue() {
  unsigned long now = millis();
  if (now - lastMetricMs < 10000) return;   // hard 10s gate
  lastMetricMs = now;

  uint32_t uptime = now / 1000;

  uint16_t avgVals[N_SENSORS];
  uint16_t baseVals[N_SENSORS];
  uint16_t maxVals[N_SENSORS];
  int dVals[N_SENSORS];

  for (int i = 0; i < N_SENSORS; i++) {
    PiezoState &ps = piezo[i];

    if (ps.samples > 0) {
      ps.avg = ps.sum / ps.samples;
    } else {
      ps.avg = 0;
    }

    if (ps.base == 0) {
      ps.base = ps.avg;
    } else {
      ps.base = (ps.base * 15 + ps.avg) / 16;
    }

    int d = (int)ps.avg - (int)ps.base;

    avgVals[i]  = ps.avg;
    baseVals[i] = ps.base;
    maxVals[i]  = ps.maxVal;
    dVals[i]    = d;
  }

  // Build single JSON array payload
  String payload = String("{\"t\":\"INF\",\"fw\":\"") + FW_VERSION +
                   "\",\"up\":" + uptime +
                   ",\"df\":" + (dfOk ? "1" : "0") +
                   ",\"en\":" + (enabled ? "1" : "0") +
                   ",\"thr\":" + KNOCK_RAW_THR;

  payload += ",\"avg\":[";
  for (int i = 0; i < N_SENSORS; i++) {
    if (i > 0) payload += ",";
    payload += avgVals[i];
  }
  payload += "]";

  payload += ",\"max\":[";
  for (int i = 0; i < N_SENSORS; i++) {
    if (i > 0) payload += ",";
    payload += maxVals[i];
  }
  payload += "]";

  payload += ",\"base\":[";
  for (int i = 0; i < N_SENSORS; i++) {
    if (i > 0) payload += ",";
    payload += baseVals[i];
  }
  payload += "]";

  payload += ",\"d\":[";
  for (int i = 0; i < N_SENSORS; i++) {
    if (i > 0) payload += ",";
    payload += dVals[i];
  }
  payload += "]}";

  mqtt.publish(TOPIC_METRIC, payload.c_str());

  // reset accumulators
  for (int i = 0; i < N_SENSORS; i++) {
    piezo[i].sum     = 0;
    piezo[i].samples = 0;
    piezo[i].maxVal  = 0;
  }
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
  // DFPlayer
  dfSerial.begin(9600, SERIAL_8N1, 16, 17); // RX=16, TX=17
  if (dfPlayer.begin(dfSerial)) {
    dfOk = true;
    dfPlayer.volume(25);
  } else {
    dfOk = false;
  }

  // Init piezo + hit states
  for (int i = 0; i < N_SENSORS; i++) {
    piezo[i].pin     = PIEZO_PINS[i];
    piezo[i].sum     = 0;
    piezo[i].samples = 0;
    piezo[i].avg     = 0;
    piezo[i].base    = 0;
    piezo[i].maxVal  = 0;
    piezo[i].lastRaw = 0;
    lastKnockMs[i]   = 0;
    hitState[i].latched = false;
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

  // Sample all piezos + edge detect
  for (int i = 0; i < N_SENSORS; i++) {
    PiezoState &ps = piezo[i];
    uint16_t raw = analogRead(ps.pin);
    ps.lastRaw = raw;
    ps.sum += raw;
    ps.samples++;
    if (raw > ps.maxVal) ps.maxVal = raw;

    // Edge-detect style knock detection using latch
    if (!hitState[i].latched && raw >= KNOCK_RAW_THR) {
      hitState[i].latched = true;
      registerKnock(i, raw);
    } else if (hitState[i].latched && raw < KNOCK_REL_THR) {
      hitState[i].latched = false;
    }
  }

  // Timeout-based sequence evaluation only
  evaluateSequenceIfDue();

  // Metrics + heartbeat
  publishMetricsIfDue();
  publishHeartbeatIfDue();
}
