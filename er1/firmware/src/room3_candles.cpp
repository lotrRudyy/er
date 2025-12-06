#include <Arduino.h>
#include <SPI.h>
#include <Ethernet.h>
#include <PubSubClient.h>
#include <Update.h>
#include <Preferences.h>

// ======================= FIRMWARE INFO =======================
// Simple numeric version used in all MQTT payloads
static const char *FW_VERSION = "1.1";
// Human-readable description for code comments / changelog
static const char *FW_DESC    = "candles 1.1 – 4x KY-037 pattern 1-2-0-3, flicker on wrong, HB/log/metric v2, prefs-solved";

// ======================= ETHERNET PINS =======================
#define ETH_CS   15
#define ETH_RST  27
#define ETH_SCK  18
#define ETH_MISO 19
#define ETH_MOSI 23

// ======================= NETWORK CONFIG ======================
byte mac[]       = { 0xDE, 0xAD, 0xBE, 0xEF, 0xFE, 0x57 };  // unique-ish MAC
IPAddress ip     (192, 168, 0, 15);
IPAddress dns    (0, 0, 0, 0);
IPAddress gw     (0, 0, 0, 0);
IPAddress subnet (255, 255, 255, 0);

// MQTT broker
IPAddress mqttServer(192, 168, 0, 10);
const uint16_t mqttPort = 1883;

// ======================= NODE TOPICS =========================
static const char *CLIENT_ID    = "candles";
static const char *TOPIC_HB     = "esc/room3/candles/hb";
static const char *TOPIC_CMD    = "esc/room3/candles/cmd";
static const char *TOPIC_LOG    = "esc/room3/candles/log";
static const char *TOPIC_METRIC = "esc/room3/candles/metric";
static const char *TOPIC_EVENT  = "esc/room3/candles/event";

// ======================= OTA CONFIG ==========================
static const char *OTA_HOST  = "192.168.0.10";
static const uint16_t OTA_PORT = 80;
static const char *OTA_PATH  = "/firmware/candles.bin";

// ======================= CANDLE HW CONFIG ====================
static const int MIC_COUNT = 4;
static const int MIC_PINS[MIC_COUNT] = {32, 33, 34, 35};     // KY-037 analog outputs
static const int LED_PINS[4]         = {16, 17, 18, 19};     // candle LEDs (PWM)

// ADC / blow detection
static const int MIC_THRESHOLD       = 1200;                 // raw ADC threshold
static const unsigned long BLOW_DEBOUNCE_MS = 250;          // per channel debounce

// Pattern & timing
static const unsigned long INACTIVITY_MS  = 3000;           // 3 s to "lock in" pattern
static const unsigned long POST_QUIET_MS  = 2000;           // quiet window after success
static const uint8_t MAX_SEQ_LEN          = 16;

// Required ritual pattern: 1-2-0-3
static const uint8_t REQUIRED_SEQ[] = {1, 2, 0, 3};
static const uint8_t REQ_LEN        = sizeof(REQUIRED_SEQ) / sizeof(REQUIRED_SEQ[0]);

// ======================= STATE ===============================
EthernetClient ethClient;
PubSubClient mqtt(ethClient);
Preferences prefs;

bool enabled = true;

unsigned long lastHbMs     = 0;
unsigned long lastMetricMs = 0;

static const unsigned long HB_INTERVAL_MS     = 5000;
static const unsigned long METRIC_INTERVAL_MS = 10000;

// error counter used in HB/log payloads
uint32_t g_errorCount = 0;

// Riddle FSM
enum RiddleState {
  RS_IDLE = 0,       // no input yet
  RS_RECORDING,      // collecting blows
  RS_POST_QUIET,     // solved, waiting 2s quiet
  RS_SOLVED          // permanently solved
};

RiddleState riddleState = RS_IDLE;
bool solvedFlag = false;

// Pattern buffer
uint8_t seqBuf[MAX_SEQ_LEN];
uint8_t seqLen = 0;

// Timing
unsigned long lastBlowMs = 0;
unsigned long stateEnterMs = 0;

// Per-channel debounce
unsigned long lastBlowMsChan[MIC_COUNT] = {0, 0, 0, 0};

// Metrics
uint32_t blowCountTotal = 0;
uint32_t wrongPatternCount = 0;

// ======================= HELPERS: LED CONTROL =================
void setLedRaw(int idx, uint8_t duty) {
  if (idx < 0 || idx >= 4) return;
  ledcWrite(idx, duty);  // channel == idx
}

void setCandleOn(int idx) {
  setLedRaw(idx, 255);
}

void setCandleOff(int idx) {
  setLedRaw(idx, 0);
}

void setAllCandlesOn() {
  for (int i = 0; i < 4; i++) setCandleOn(i);
}

void setAllCandlesOff() {
  for (int i = 0; i < 4; i++) setCandleOff(i);
}

// Short, blocking flicker used only on wrong pattern
void flickerCandlesOn() {
  unsigned long start = millis();
  unsigned long duration = 700;   // total flicker time (ms)

  while (millis() - start < duration) {
    for (int i = 0; i < 4; i++) {
      uint8_t r = random(120, 255);  // bright, warm flicker
      ledcWrite(i, r);
    }
    delay(random(40, 80)); // organic flicker timing
  }

  // Fade to full brightness
  for (int b = 0; b <= 255; b += 10) {
    for (int i = 0; i < 4; i++) ledcWrite(i, b);
    delay(15);
  }
}

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

  const char *rstStr =
    (riddleState == RS_IDLE)       ? "IDLE" :
    (riddleState == RS_RECORDING)  ? "REC" :
    (riddleState == RS_POST_QUIET) ? "POST_QUIET" : "SOLVED";

  // Metrics format: {"fw":FW_VERSION,"up":uptime_s,"k":"metric_key",...}
  String payload = String("{\"fw\":\"") + FW_VERSION +
                   "\",\"up\":" + String(now / 1000) +
                   ",\"k\":\"candles\"" +
                   ",\"state\":\"" + rstStr + "\"" +
                   ",\"thr\":" + String(MIC_THRESHOLD) +
                   ",\"seq_len\":" + String(seqLen) +
                   ",\"blows\":" + String(blowCountTotal) +
                   ",\"wrong\":" + String(wrongPatternCount) +
                   ",\"solved\":" + (solvedFlag ? "1" : "0") +
                   "}";
  mqtt.publish(TOPIC_METRIC, payload.c_str());
}

// ======================= EVENT PUBLISH =======================
void publishSolvedEvent() {
  String seqStr = "[";
  for (uint8_t i = 0; i < seqLen; i++) {
    if (i > 0) seqStr += ",";
    seqStr += String(seqBuf[i]);
  }
  seqStr += "]";

  String payload = String("{\"fw\":\"") + FW_VERSION +
                   "\",\"up\":" + String(millis() / 1000) +
                   ",\"event\":\"SOLVED\",\"seq\":" + seqStr + "}";
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

// ======================= PATTERN / FSM =======================
void resetPattern() {
  seqLen = 0;
  lastBlowMs = 0;
}

void resetRiddle() {
  solvedFlag = false;
  riddleState = RS_IDLE;
  stateEnterMs = millis();
  resetPattern();
  setAllCandlesOn();
  prefs.putBool("solved", false);
}

bool patternMatchesRequired() {
  if (seqLen != REQ_LEN) return false;
  for (uint8_t i = 0; i < REQ_LEN; i++) {
    if (seqBuf[i] != REQUIRED_SEQ[i]) return false;
  }
  return true;
}

void onBlow(uint8_t idx) {
  if (!enabled) return;
  if (riddleState == RS_SOLVED || riddleState == RS_POST_QUIET) return;

  unsigned long now = millis();

  if (riddleState == RS_IDLE) {
    // First blow of an attempt
    riddleState = RS_RECORDING;
    stateEnterMs = now;
    resetPattern();
  }

  if (riddleState == RS_RECORDING) {
    if (seqLen < MAX_SEQ_LEN) {
      seqBuf[seqLen++] = idx;
    }
    blowCountTotal++;
    lastBlowMs = now;

    // Visual: blown candle goes off
    setCandleOff(idx);
  }
}

void updateRiddleFsm() {
  unsigned long now = millis();

  if (riddleState == RS_RECORDING) {
    if (lastBlowMs > 0 && (now - lastBlowMs) > INACTIVITY_MS) {
      // Time to "lock in" and evaluate
      if (patternMatchesRequired()) {
        solvedFlag = true;
        prefs.putBool("solved", true);

        publishLog("INF", "pattern SOLVED");
        publishSolvedEvent();

        // Keep candles off on success
        setAllCandlesOff();

        riddleState = RS_POST_QUIET;
        stateEnterMs = now;
      } else {
        wrongPatternCount++;
        String data = String("{\"len\":") + String(seqLen) + "}";
        publishLog("INF", "pattern WRONG", data);

        // Cool flicker effect before resetting
        flickerCandlesOn();

        resetRiddle();
      }
    }
  } else if (riddleState == RS_POST_QUIET) {
    if (now - stateEnterMs > POST_QUIET_MS) {
      riddleState = RS_SOLVED;
      stateEnterMs = now;
      // stay dark; no LED re-enable
    }
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
}

// ======================= STATE PERSISTENCE ===================
void loadStateFromPrefs() {
  prefs.begin("candles", false);
  solvedFlag = prefs.getBool("solved", false);

  if (solvedFlag) {
    riddleState = RS_SOLVED;
    setAllCandlesOff();
    publishLog("INF", "BOOT solved=1, keeping candles off");
  } else {
    riddleState = RS_IDLE;
    setAllCandlesOn();
    publishLog("INF", "BOOT solved=0, reset riddle");
  }
  stateEnterMs = millis();
}

// ======================= SETUP / LOOP ========================
void setup() {
  Serial.begin(115200);
  delay(200);

  analogReadResolution(12);

  // Seed RNG for flicker
  randomSeed(analogRead(32));

  // LEDs PWM
  for (int i = 0; i < 4; i++) {
    ledcSetup(i, 1000, 8);     // channel i, 1 kHz, 8-bit
    ledcAttachPin(LED_PINS[i], i);
  }

  // Mic pins
  for (int i = 0; i < MIC_COUNT; i++) {
    pinMode(MIC_PINS[i], INPUT);
    lastBlowMsChan[i] = 0;
  }

  setupEthernet();
  mqttReconnect();

  loadStateFromPrefs();
}

void loop() {
  if (!mqtt.connected()) {
    mqttReconnect();
  }
  mqtt.loop();

  unsigned long now = millis();

  // Sample microphones & detect blows
  for (int i = 0; i < MIC_COUNT; i++) {
    int v = analogRead(MIC_PINS[i]);
    if (v >= MIC_THRESHOLD &&
        (now - lastBlowMsChan[i]) > BLOW_DEBOUNCE_MS) {
      lastBlowMsChan[i] = now;
      onBlow(i);
    }
  }

  updateRiddleFsm();
  publishMetricsIfDue();
  publishHeartbeatIfDue();
}
