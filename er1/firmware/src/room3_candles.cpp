#include <Arduino.h>
#include <SPI.h>
#include <Ethernet.h>
#include <PubSubClient.h>
#include <Update.h>

// ======================= FIRMWARE INFO =======================
// Simple numeric version used in all MQTT payloads
static const char *FW_VERSION = "1.1";
// Human-readable description for docs / changelog only
static const char *FW_DESC    = "candles 1.1 – 4-mic ORDER[2,0,3,1] with 3s sequence timer + MQTT trigger to star-sky/lighting";

// DEV logging flag: 0 = only ERR logs, 1 = verbose logs (INF/DBG/WRN/ERR)
#define CANDLES_DEV_LOG 0

// ======================= ETHERNET PINS =======================
#define ETH_CS   15
#define ETH_RST  27
#define ETH_SCK  18
#define ETH_MISO 19
#define ETH_MOSI 23

// ======================= NETWORK CONFIG ======================
byte mac[]       = { 0xDE, 0xAD, 0xBE, 0xEF, 0xFE, 0x58 };  // unique-ish MAC
IPAddress ip     (192, 168, 0, 15);   // room3 candles node
IPAddress dns    (0, 0, 0, 0);
IPAddress gw     (0, 0, 0, 0);
IPAddress subnet (255, 255, 255, 0);

// MQTT broker
IPAddress mqttServer(192, 168, 0, 10);
const uint16_t mqttPort = 1883;

// ======================= NODE TOPICS =========================
static const char *CLIENT_ID          = "candles";
static const char *TOPIC_HB           = "esc/room3/candles/hb";
static const char *TOPIC_CMD          = "esc/room3/candles/cmd";
static const char *TOPIC_LOG          = "esc/room3/candles/log";
static const char *TOPIC_METRIC       = "esc/room3/candles/metric";
static const char *TOPIC_EVENT        = "esc/room3/candles/event";
// Command targets for star sky + lighting controller
static const char *TOPIC_CMD_STAR_SKY = "esc/room3/star-sky/cmd";
static const char *TOPIC_CMD_LIGHTING = "esc/room0/lighting/cmd";

// ======================= OTA CONFIG ==========================
static const char *OTA_HOST  = "192.168.0.10";
static const uint16_t OTA_PORT = 80;
static const char *OTA_PATH  = "/firmware/candles.bin";

// ======================= PUZZLE CONFIG =======================
// LEDs: MOSFET gate pins for the 4 candles
static const int LED_PINS[4] = {16, 17, 18, 19};
// Mics: 4 analog inputs on ADC1
static const int MIC_PINS[4] = {32, 33, 34, 35};
// Required blow order
static const int ORDER[4]    = {2, 0, 3, 1};

// Fixed baselines & thresholds
static int BASE[4]  = {1515, 1490, 1485, 1508};
static int DELTA[4] = {120, 120, 120, 120};

// Refractory period per candle (ms)
static const unsigned long REFRACT_MS = 600;

// Sequence evaluation timeout after last blow (ms)
static const unsigned long SEQ_TIMEOUT_MS = 3000;

// ======================= PUZZLE STATE ========================
bool lit[4] = {true, true, true, true};
unsigned long lastTrig[4] = {0, 0, 0, 0};  // per-candle refractory

// Sequence buffer: order in which candles were blown
int  progress[4]          = {-1, -1, -1, -1};
int  progressed           = 0;             // seq length (0..4)
unsigned long lastAction  = 0;             // last blow or reset
unsigned long lastSeqActivityMs = 0;       // last blow timestamp

bool solved          = false;
bool solvedEventSent = false;

inline void setLed(int i, bool on) {
  digitalWrite(LED_PINS[i], on ? HIGH : LOW);
  lit[i] = on;
}

// ======================= GLOBALS =============================
EthernetClient ethClient;
PubSubClient mqtt(ethClient);

unsigned long lastHbMs     = 0;
unsigned long lastMetricMs = 0;
static const unsigned long HB_INTERVAL_MS     = 5000;
static const unsigned long METRIC_INTERVAL_MS = 1000;

bool enabled = true;

// error counter (for HB err field)
uint32_t g_errorCount = 0;

// ======================= METRIC STATE ========================
struct MicMetric {
  uint32_t sum;
  uint16_t samples;
  uint16_t avg;
  uint16_t base;
  uint16_t maxVal;
  uint16_t lastRaw;
};
MicMetric micMetrics[4];

// ======================= LOG UTILS ===========================
// lvl: "DBG","INF","WRN","ERR"
void publishLog(const char *lvl, const String &msg, const String &dataJson = String()) {
  bool isErr = (strcmp(lvl, "ERR") == 0);
  bool allow = false;

  if (isErr) {
    allow = true;
  } else if (CANDLES_DEV_LOG) {
    // DEV: allow all levels
    allow = true;
  } else {
    // PROD: only errors
    allow = false;
  }

  if (!allow) {
    if (isErr) g_errorCount++;  // still count errors for HB
    return;
  }

  String payload = String("{\"fw\":\"") + FW_VERSION +
                   "\",\"up\":" + String(millis() / 1000) +
                   ",\"lv\":\"" + lvl + "\",\"msg\":\"" + msg + "\"";
  if (dataJson.length() > 0) {
    payload += ",\"d\":" + dataJson;
  }
  payload += "}";

  mqtt.publish(TOPIC_LOG, payload.c_str());

  if (isErr) {
    g_errorCount++;
  }
}

void logErr(const String &msg, const String &dataJson = String()) {
  publishLog("ERR", msg, dataJson);
}

// ======================= OTA (HTTP pull) =====================
bool doHttpOta() {
  EthernetClient client;
  String url = String("http://") + OTA_HOST + OTA_PATH;
  publishLog("INF", String("CMD UPDATE -> HTTP OTA ") + url);

  if (!client.connect(OTA_HOST, OTA_PORT)) {
    logErr("OTA connect failed");
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
    logErr("OTA invalid content-length");
    return false;
  }

  if (!Update.begin(contentLength)) {
    logErr("OTA Update.begin failed");
    return false;
  }

  uint8_t buf[512];

  while (client.connected() || client.available()) {
    int len = client.read(buf, sizeof(buf));
    if (len <= 0) continue;
    Update.write(buf, len);
  }

  if (!Update.end(true)) {
    logErr("OTA Update.end failed");
    return false;
  }

  publishLog("INF", "OTA OK, rebooting");
  delay(500);
  ESP.restart();
  return true;
}

// ======================= EVENT PUBLISH =======================
void publishSolvedEvent() {
  if (solvedEventSent) return;

  String payload = String("{\"event\":\"SOLVED\",\"rid\":\"candles\"}");
  // General gameflow event
  mqtt.publish(TOPIC_EVENT, payload.c_str());
  // Trigger star sky and lighting controller
  mqtt.publish(TOPIC_CMD_STAR_SKY,  "CANDLES_SOLVED");
  mqtt.publish(TOPIC_CMD_LIGHTING,  "CANDLES_SOLVED");

  solvedEventSent = true;
}

// ======================= PUZZLE HELPERS ======================
void clearSequence() {
  for (int i = 0; i < 4; i++) {
    progress[i] = -1;
  }
  progressed          = 0;
  lastSeqActivityMs   = 0;
}

void resetPuzzleState() {
  for (int i = 0; i < 4; i++) {
    setLed(i, true);
    progress[i] = -1;
  }
  progressed        = 0;
  solved            = false;
  solvedEventSent   = false;
  lastAction        = millis();
  lastSeqActivityMs = 0;
}

void flickerRelight(int cycles = 6, int onMs = 80, int offMs = 60) {
  // Simple blocking flicker: all ON/OFF a few times, then ON
  for (int c = 0; c < cycles; c++) {
    for (int i = 0; i < 4; i++) setLed(i, true);
    delay(onMs);
    for (int i = 0; i < 4; i++) setLed(i, false);
    delay(offMs);
  }
  for (int i = 0; i < 4; i++) setLed(i, true);
}

void resetAll() {
  flickerRelight();  // wrong-sequence feedback (TODO: tune pattern)
  resetPuzzleState();
  publishLog("INF", "Reset (relight all)");
}

// ======================= BLOW DETECTION ======================
// Returns true if we detected a valid "blow" on mic i
bool detectBlow(int idx) {
  const int samples = 80;
  const int needed  = samples / 3;  // ~33% of samples over threshold

  unsigned long now = millis();
  if (now - lastTrig[idx] < REFRACT_MS) return false;

  int over = 0;
  for (int k = 0; k < samples; k++) {
    int v = analogRead(MIC_PINS[idx]);
    if (abs(v - BASE[idx]) > DELTA[idx]) over++;
    delay(2);
  }

  bool hit = (over >= needed);
  if (hit) {
    lastTrig[idx] = millis();
  }
  return hit;
}

// ======================= SEQUENCE EVAL =======================
void evaluateSequence() {
  if (progressed == 0) return;  // nothing to evaluate

  // Build debug string
  if (CANDLES_DEV_LOG) {
    String seqStr;
    for (int i = 0; i < progressed; i++) {
      if (i > 0) seqStr += ",";
      seqStr += progress[i];
    }
    publishLog("INF", String("SEQ_EVAL len=") + progressed + " buf=[" + seqStr + "]");
  }

  bool ok = true;

  // Must have exactly 4 blows and match ORDER
  if (progressed != 4) {
    ok = false;
  } else {
    for (int i = 0; i < 4; i++) {
      if (progress[i] != ORDER[i]) {
        ok = false;
        break;
      }
    }
  }

  if (ok) {
    // SUCCESS: solved, keep LEDs off
    solved = true;
    publishLog("INF", "SEQUENCE OK -> SOLVED");
    publishSolvedEvent();
  } else {
    // FAIL: wrong sequence -> flicker + reset
    publishLog("INF", "SEQUENCE FAIL -> reset");
    resetAll();
  }
}

void evaluateSequenceIfDue() {
  if (solved) return;
  if (progressed == 0) return;  // nothing started

  unsigned long now = millis();
  if (now - lastSeqActivityMs < SEQ_TIMEOUT_MS) return;

  // 3 seconds of no blows since last one -> evaluate
  evaluateSequence();
}

// ======================= HEARTBEAT ===========================
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

// ======================= METRICS =============================
void publishMetricsIfDue() {
  unsigned long now = millis();
  if (now - lastMetricMs < METRIC_INTERVAL_MS) return;
  lastMetricMs = now;

  uint32_t uptime = now / 1000;

  for (int i = 0; i < 4; i++) {
    MicMetric &mm = micMetrics[i];

    if (mm.samples > 0) {
      mm.avg = mm.sum / mm.samples;
    } else {
      mm.avg = 0;
    }

    // Rolling baseline (EMA)
    if (mm.base == 0) {
      mm.base = mm.avg;
    } else {
      mm.base = (mm.base * 15 + mm.avg) / 16;
    }

    int d = (int)mm.avg - (int)mm.base;

    String payload = String("{\"fw\":\"") + FW_VERSION +
                     "\",\"up\":" + uptime +
                     ",\"k\":\"candles\"" +
                     ",\"en\":" + (enabled ? "1" : "0") +
                     ",\"i\":" + i +
                     ",\"avg\":" + mm.avg +
                     ",\"max\":" + mm.maxVal +
                     ",\"base\":" + mm.base +
                     ",\"d\":" + d +
                     ",\"thr\":" + DELTA[i] +
                     "}";

    mqtt.publish(TOPIC_METRIC, payload.c_str());

    // reset accumulators
    mm.sum     = 0;
    mm.samples = 0;
    mm.maxVal  = 0;
  }
}

// ======================= MQTT HANDLING =======================
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

void mqttCallback(char *topicC, byte *payload, unsigned int length) {
  String topic(topicC);
  String msg;
  msg.reserve(length);
  for (unsigned int i = 0; i < length; i++) msg += (char)payload[i];
  msg.trim();

  if (topic == TOPIC_CMD) {
    publishLog("DBG", String("CMD topic=") + topic + " msg=" + msg);
    handleCmd(msg);
  }
}

void mqttReconnect() {
  int tries = 0;
  while (!mqtt.connected() && tries < 3) {
    if (mqtt.connect(CLIENT_ID, TOPIC_HB, 0, true, "offline")) {
      publishLog("INF", "MQTT connected");
      mqtt.subscribe(TOPIC_CMD);
      publishHeartbeatIfDue();  // initial HB
      return;
    } else {
      tries++;
      delay(1000);
    }
  }
  if (!mqtt.connected()) {
    logErr("MQTT reconnect failed", "{\"tries\":3}");
  }
}

// ======================= SETUP ===============================
void setup() {
  // LEDs
  for (int i = 0; i < 4; i++) {
    pinMode(LED_PINS[i], OUTPUT);
    setLed(i, true);
  }

  // Metrics init
  for (int i = 0; i < 4; i++) {
    micMetrics[i].sum     = 0;
    micMetrics[i].samples = 0;
    micMetrics[i].avg     = 0;
    micMetrics[i].base    = 0;
    micMetrics[i].maxVal  = 0;
    micMetrics[i].lastRaw = 0;
  }

  analogReadResolution(12);
  lastAction        = millis();
  lastSeqActivityMs = 0;

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

  unsigned long now = millis();

  // --- Metrics sampling: one raw read per mic per loop ---
  for (int i = 0; i < 4; i++) {
    uint16_t raw = analogRead(MIC_PINS[i]);
    micMetrics[i].lastRaw = raw;
    micMetrics[i].sum    += raw;
    micMetrics[i].samples++;
    if (raw > micMetrics[i].maxVal) micMetrics[i].maxVal = raw;
  }

  // --- Puzzle logic only when enabled ---
  if (enabled) {
    if (!solved) {
      // Scan for blows on each candle
      for (int i = 0; i < 4; i++) {
        if (!lit[i]) continue;  // already off -> ignore further blows

        if (detectBlow(i)) {
          lastAction        = now;
          lastSeqActivityMs = now;

          // Turn that candle OFF immediately (only once)
          setLed(i, false);

          // Record sequence step
          if (progressed < 4) {
            progress[progressed] = i;
            progressed++;

            if (CANDLES_DEV_LOG) {
              String d = String("{\"idx\":") + i +
                         ",\"step\":" + progressed + "}";
              publishLog("INF", "BLOW", d);
            }
          }

          // If all 4 are OFF (progressed == 4), evaluate immediately
          if (progressed >= 4) {
            evaluateSequence();
          }
        }
      }

      // If we have at least one blow, and 3s passed since last one -> evaluate
      evaluateSequenceIfDue();
    } else {
      // Once solved, keep sending event once in case of reconnects
      publishSolvedEvent();
    }
  }

  // Metrics + heartbeat
  publishMetricsIfDue();
  publishHeartbeatIfDue();
}
