#include <Arduino.h>
#include <SPI.h>
#include <Ethernet.h>
#include <PubSubClient.h>
#include <Update.h>
#include <DFRobotDFPlayerMini.h>

// ======================= FIRMWARE INFO =======================
// Simple numeric version used in all MQTT payloads
static const char *FW_VERSION = "1.15";
// Human-readable description for docs / changelog only
static const char *FW_DESC    = "knocking 1.15 – double-play DF (50ms gap), no idle logic or pre-knock delay";

// DEV logging flag: 0 = only ERR logs, 1 = verbose logs (INF/DBG/WRN/ERR)
#define KNOCK_DEV_LOG 0

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

// DFPlayer digital volume (0–30)
static const uint8_t DF_VOLUME = 30;

// --- Sound queue: each entry is a track number for /mp3/000x.mp3
static const int SOUND_QUEUE_MAX = 16;
uint8_t soundQueue[SOUND_QUEUE_MAX];
uint8_t soundHead = 0;
uint8_t soundTail = 0;

bool soundPlaying = false;
unsigned long lastSoundStartMs = 0;
uint8_t currentTrack = 0;

// Forward declaration so we can use it earlier
void enqueueSound(uint8_t track);

// ======================= PIEZO CONFIG ========================
static const int N_SENSORS = 3;
static const int PIEZO_PINS[N_SENSORS] = { 32, 33, 34 };

// Raw threshold for considering a knock (base)
static const uint16_t KNOCK_RAW_THR      = 1200;
// Per-sensor thresholds (sensor 1 more sensitive, sensor 0 slightly lowered)
static const uint16_t KNOCK_THR[N_SENSORS] = { 900, 500, 1200 };

// Debounce between *global* knocks (ms)
static const unsigned long KNOCK_DEBOUNCE_MS = 200;
// Knock window length: group all hits within this time into one knock
static const unsigned long KNOCK_WINDOW_MS   = 40;

// Metric/EMA config
struct PiezoState {
  int      pin;
  uint32_t sum;
  uint16_t samples;
  uint16_t avg;
  uint16_t base;
  uint16_t maxVal;
  uint16_t lastRaw;

  // 10 buckets per second: each ~100ms (store MAX, not avg)
  static const int N_BUCKETS = 10;
  uint16_t bucketMax[N_BUCKETS];
};
PiezoState piezo[N_SENSORS];

unsigned long lastMetricMs = 0;
static const unsigned long METRIC_INTERVAL_MS = 1000;

// ======================= KNOCK-WINDOW STATE ==================
// We treat each physical knock as one "window" where we pick the
// sensor with the highest amplitude and only register that one.
bool knockWindowActive         = false;
unsigned long knockWindowStart = 0;
uint16_t windowMax[N_SENSORS]  = {0, 0, 0};
unsigned long lastKnockMsGlobal = 0;

// ======================= SEQUENCE CONFIG =====================
// Target pattern: 0,0,0,0,1,1,2,2,2
static const int SEQ_EXPECT_LEN = 9;
static const int SEQ_EXPECT[SEQ_EXPECT_LEN] = {0,0,0,0,1,1,2,2,2};

static const int SEQ_MAX_LEN = 16;
int seqBuf[SEQ_MAX_LEN];
int seqLen = 0;

unsigned long lastSeqActivityMs = 0;
static const unsigned long SEQ_TIMEOUT_MS = 3000; // 3 s after last knock -> evaluate

// ======================= GLOBALS =============================
EthernetClient ethClient;
PubSubClient mqtt(ethClient);

unsigned long lastHbMs = 0;
static const unsigned long HB_INTERVAL_MS = 5000;

bool enabled = true;

// error counter (for HB err field)
uint32_t g_errorCount = 0;

// ======================= LOG UTILS ===========================
// lvl: "DBG","INF","WRN","ERR"
void publishLog(const char *lvl, const String &msg, const String &dataJson = String()) {
  bool isErr = (strcmp(lvl, "ERR") == 0);
  bool allow = false;

  if (isErr) {
    allow = true;
  } else if (KNOCK_DEV_LOG) {
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
  String payload = String("{\"event\":\"SOLVED\",\"rid\":\"knocking\"}");
  mqtt.publish(TOPIC_EVENT, payload.c_str());
}

// ======================= SEQUENCE HELPERS ====================
void resetSequence() {
  seqLen = 0;
  lastSeqActivityMs = 0;
}

// ======================= SOUND QUEUE SMALL HELPERS ===========
bool soundQueueIsEmpty() {
  return soundHead == soundTail;
}

bool soundQueueIsFull() {
  return (uint8_t)((soundTail + 1) % SOUND_QUEUE_MAX) == soundHead;
}

// Per-track fallback duration in ms
unsigned long trackFallbackMs(uint8_t track) {
  switch (track) {
    case 1: return 205;   // 0001 = 00:00:00.205
    case 2: return 205;   // 0002 = 00:00:00.205
    case 3: return 205;   // 0003 = 00:00:00.205
    case 4: return 1240;  // 0004 = 00:00:01.240 (reset)
    default:
      // Should never happen – log it so we see if it does
      logErr("trackFallbackMs default", String("{\"track\":") + track + "}");
      return 500;  // safety fallback
  }
}

// Reset/error sound: play track 0004.mp3 from /mp3, once
void playFailSoundOnce() {
  enqueueSound(4);
}

// ======================= SEQUENCE EVAL =======================
void evaluateSequence() {
  if (seqLen == 0) return;

  // Buffer string for debug
  String seqStr;
  for (int i = 0; i < seqLen; i++) {
    if (i > 0) seqStr += ",";
    seqStr += seqBuf[i];
  }

  if (KNOCK_DEV_LOG) {
    String evalMsg = String("SEQ_EVAL len=") + seqLen + " buf=[" + seqStr + "]";
    publishLog("INF", evalMsg);
  }

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
    // SUCCESS: only open door, NO success sound
    publishLog("INF", "SEQUENCE OK -> SOLVED (no success sound)");
    publishSolvedEvent();
    resetSequence();
  } else {
    publishLog("INF", "SEQUENCE FAIL -> reset/error sound (track 4)");
    playFailSoundOnce();
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
// Sensor 0 -> 0001.mp3, sensor 1 -> 0002.mp3, sensor 2 -> 0003.mp3
void playKnockSound(int idx) {
  int track = 0;
  if (idx == 0)      track = 1;  // /mp3/0001.mp3
  else if (idx == 1) track = 2;  // /mp3/0002.mp3
  else if (idx == 2) track = 3;  // /mp3/0003.mp3
  else return;

  enqueueSound(track);
}

// ======================= KNOCK HANDLING ======================
void registerKnock(int idx, uint16_t raw) {
  if (!enabled) return;

  unsigned long now = millis();
  if (KNOCK_DEV_LOG) {
    String d = String("{\"idx\":") + idx + ",\"raw\":" + raw + "}";
    publishLog("INF", "KNOCK", d);
  }

  // Append to sequence buffer
  if (seqLen < SEQ_MAX_LEN) {
    seqBuf[seqLen++] = idx;
  }

  // Update activity timestamp for timer-based evaluation
  lastSeqActivityMs = now;

  // Queue sound for this sensor
  playKnockSound(idx);
}

// ======================= HEARTBEAT ===========================
void publishHeartbeatIfDue() {
  unsigned long now = millis();
  if (now - lastHbMs < HB_INTERVAL_MS) return;
  lastHbMs = now;

  const char *st;
  if (!enabled) {
    st = "warn";
  } else if (!dfOk || g_errorCount > 0) {
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

  for (int i = 0; i < N_SENSORS; i++) {
    PiezoState &ps = piezo[i];

    // Overall 1s average
    if (ps.samples > 0) {
      ps.avg = ps.sum / ps.samples;
    } else {
      ps.avg = 0;
    }

    // Rolling baseline (EMA)
    if (ps.base == 0) {
      ps.base = ps.avg;
    } else {
      ps.base = (ps.base * 15 + ps.avg) / 16;
    }

    int d = (int)ps.avg - (int)ps.base;

    // 10-bucket window array: MAX per 100ms
    String window = "[";
    for (int b = 0; b < PiezoState::N_BUCKETS; b++) {
      uint16_t wVal = ps.bucketMax[b];
      window += String(wVal);
      if (b < PiezoState::N_BUCKETS - 1) window += ",";
    }
    window += "]";

    String payload = String("{\"fw\":\"") + FW_VERSION +
                     "\",\"up\":" + uptime +
                     ",\"k\":\"knocking\"" +
                     ",\"df\":" + (dfOk ? "1" : "0") +
                     ",\"en\":" + (enabled ? "1" : "0") +
                     ",\"i\":" + i +
                     ",\"avg\":" + ps.avg +
                     ",\"max\":" + ps.maxVal +
                     ",\"base\":" + ps.base +
                     ",\"d\":" + d +
                     ",\"thr\":" + KNOCK_THR[i] +
                     ",\"w\":" + window +
                     "}";

    mqtt.publish(TOPIC_METRIC, payload.c_str());

    // Reset accumulators for next second
    ps.sum     = 0;
    ps.samples = 0;
    ps.maxVal  = 0;
    for (int b = 0; b < PiezoState::N_BUCKETS; b++) {
      ps.bucketMax[b] = 0;
    }
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

// ======================= SOUND QUEUE OPS =====================
void enqueueSound(uint8_t track) {
  if (!dfOk) return;

  if (soundQueueIsFull()) {
    if (KNOCK_DEV_LOG) {
      publishLog("WRN", "Sound queue full, dropping", String("{\"track\":") + track + "}");
    }
    return;
  }

  soundQueue[soundTail] = track;
  soundTail = (uint8_t)((soundTail + 1) % SOUND_QUEUE_MAX);

  if (KNOCK_DEV_LOG) {
    String d = String("{\"track\":") + track + ",\"head\":" + soundHead + ",\"tail\":" + soundTail + "}";
    publishLog("DBG", "ENQUEUE sound", d);
  }
}

// Called from loop() – manages DFPlayer playback from the queue
void serviceSound() {
  if (!dfOk) return;

  unsigned long now = millis();

  if (soundPlaying) {
    unsigned long needed = trackFallbackMs(currentTrack);
    if (now - lastSoundStartMs >= needed) {
      soundPlaying = false;
      if (KNOCK_DEV_LOG) {
        String d = String("{\"track\":") + currentTrack + ",\"dur\":" + needed + "}";
        publishLog("DBG", "Sound finished (per-track timeout)", d);
      }
    }
    return;
  }

  // Not currently playing: check queue
  if (soundQueueIsEmpty()) return;

  uint8_t track = soundQueue[soundHead];
  soundHead = (uint8_t)((soundHead + 1) % SOUND_QUEUE_MAX);

  if (KNOCK_DEV_LOG) {
    String d = String("{\"track\":") + track + ",\"head\":" + soundHead + ",\"tail\":" + soundTail + "}";
    publishLog("INF", "PLAY sound from queue", d);
  }

  // play only once, no extra delay
  dfPlayer.playMp3Folder(track);   // /mp3/000x.mp3

  lastSoundStartMs = millis();
  currentTrack     = track;
  soundPlaying     = true;
}

// ======================= SETUP ===============================
void setup() {
  // DFPlayer
  dfSerial.begin(9600, SERIAL_8N1, 16, 17); // RX=16, TX=17
  if (dfPlayer.begin(dfSerial)) {
    dfOk = true;
    dfPlayer.volume(DF_VOLUME);  // max volume
    // Files must be /mp3/0001.mp3 .. /mp3/0004.mp3
  } else {
    dfOk = false;
    logErr("DFPlayer init failed");
  }

  // Init piezo states
  for (int i = 0; i < N_SENSORS; i++) {
    piezo[i].pin     = PIEZO_PINS[i];
    piezo[i].sum     = 0;
    piezo[i].samples = 0;
    piezo[i].avg     = 0;
    piezo[i].base    = 0;
    piezo[i].maxVal  = 0;
    piezo[i].lastRaw = 0;

    for (int b = 0; b < PiezoState::N_BUCKETS; b++) {
      piezo[i].bucketMax[b] = 0;
    }
  }

  knockWindowActive  = false;
  lastKnockMsGlobal  = 0;

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

  // Sample all piezos + metrics + update knock window maxima
  for (int i = 0; i < N_SENSORS; i++) {
    PiezoState &ps = piezo[i];
    uint16_t raw = analogRead(ps.pin);
    ps.lastRaw = raw;

    // Global 1s accumulation
    ps.sum += raw;
    ps.samples++;
    if (raw > ps.maxVal) ps.maxVal = raw;

    // 10 buckets per second: each ~100ms (store MAX)
    uint8_t bucketIdx = (now / 100) % PiezoState::N_BUCKETS;
    if (raw > ps.bucketMax[bucketIdx]) {
      ps.bucketMax[bucketIdx] = raw;
    }

    // Knock window logic
    if (!knockWindowActive) {
      // No window yet: if any sensor crosses its threshold, start window
      if (raw >= KNOCK_THR[i]) {
        knockWindowActive = true;
        knockWindowStart  = now;
        for (int j = 0; j < N_SENSORS; j++) {
          windowMax[j] = 0;
        }
        windowMax[i] = raw;
      }
    } else {
      // Window already active: track per-sensor maxima
      if (raw > windowMax[i]) {
        windowMax[i] = raw;
      }
    }
  }

  // If a window is active and elapsed time > KNOCK_WINDOW_MS → decide winner
  if (knockWindowActive && (now - knockWindowStart >= KNOCK_WINDOW_MS)) {
    knockWindowActive = false;

    // Global debounce between knocks
    if (now - lastKnockMsGlobal >= KNOCK_DEBOUNCE_MS) {
      int bestIdx = -1;
      uint16_t bestVal = 0;
      for (int i = 0; i < N_SENSORS; i++) {
        if (windowMax[i] > bestVal) {
          bestVal = windowMax[i];
          bestIdx = i;
        }
      }

      if (bestIdx >= 0 && bestVal >= KNOCK_THR[bestIdx]) {
        lastKnockMsGlobal = now;

        if (KNOCK_DEV_LOG) {
          String d = String("{\"best\":") + bestIdx +
                     ",\"val\":" + bestVal +
                     ",\"m0\":" + windowMax[0] +
                     ",\"m1\":" + windowMax[1] +
                     ",\"m2\":" + windowMax[2] + "}";
          publishLog("INF", "KNOCK_WINDOW_WINNER", d);
        }

        registerKnock(bestIdx, bestVal);
      }
    }
  }

  // Timeout-based sequence evaluation only
  evaluateSequenceIfDue();

  // Metrics + heartbeat
  publishMetricsIfDue();
  publishHeartbeatIfDue();

  // Handle DFPlayer sound queue
  serviceSound();
}
