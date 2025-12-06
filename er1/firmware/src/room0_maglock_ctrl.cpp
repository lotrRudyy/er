#include <Arduino.h>
#include <SPI.h>
#include <Ethernet.h>
#include <PubSubClient.h>
#include <Update.h>

// ======================= FIRMWARE INFO =======================
// Simple numeric version used in all MQTT payloads
static const char *FW_VERSION = "1.1";
// Human-readable description for changelog / docs
static const char *FW_DESC    = "maglock_ctrl 1.1 – ER1 protocol-aligned (images,r2,r3,slider,knocking), 1s pulses + 10s cooldown, HB/log/metric v2, gameMode";

// ======================= ETHERNET PINS =======================
#define ETH_CS   15
#define ETH_RST  27
#define ETH_SCK  18
#define ETH_MISO 19
#define ETH_MOSI 23

// ======================= NETWORK CONFIG ======================
byte mac[]       = { 0xDE, 0xAD, 0xBE, 0xEF, 0xFE, 0x50 };  // unique-ish MAC
IPAddress ip     (192, 168, 0, 11);
IPAddress dns    (0, 0, 0, 0);
IPAddress gw     (0, 0, 0, 0);
IPAddress subnet (255, 255, 255, 0);

// MQTT broker
IPAddress mqttServer(192, 168, 0, 10);
const uint16_t mqttPort = 1883;

// ======================= NODE TOPICS =========================
static const char *CLIENT_ID    = "maglock_ctrl";
static const char *TOPIC_HB     = "esc/room0/maglock_ctrl/hb";
static const char *TOPIC_CMD    = "esc/room0/maglock_ctrl/cmd";
static const char *TOPIC_LOG    = "esc/room0/maglock_ctrl/log";
static const char *TOPIC_METRIC = "esc/room0/maglock_ctrl/metric";

// Global game-mode topic (simple string: OFF|INGAME|MAINT)
static const char *TOPIC_GAME   = "esc/game/state";

// Lock topics: esc/ctrl/lock/<id>/{cmd,state}
static const char *LOCK_CMD_PREFIX   = "esc/ctrl/lock/";    // + <id> + "/cmd"
static const char *LOCK_STATE_PREFIX = "esc/ctrl/lock/";    // + <id> + "/state"

// ======================= OTA CONFIG ==========================
static const char *OTA_HOST  = "192.168.0.10";
static const uint16_t OTA_PORT = 80;
static const char *OTA_PATH  = "/firmware/maglock_ctrl.bin";

// ======================= GAME MODE ===========================
enum GameMode {
  MODE_OFF = 0,   // no group in-game, minimal logs, slow HB
  MODE_INGAME,    // players in-game, normal logs, fast HB
  MODE_MAINT      // maintenance / debugging, verbose logs
};

GameMode gameMode = MODE_OFF;

// ======================= LOCK CONFIG =========================
enum LockMode {
  FAIL_SECURE = 0,  // no power = locked, pulse to unlock
  FAIL_SAFE         // no power = unlocked, hold power to lock
};

struct Lock {
  const char *id;
  uint8_t     pin;
  LockMode    mode;

  // runtime state
  bool        coilOn;            // current physical output (true = HIGH)
  bool        pulsing;           // only for fail-secure
  bool        cooldown;          // only for fail-secure
  unsigned long pulseStartMs;
  unsigned long cooldownStartMs;
  uint32_t    pulseCount;
};

static const unsigned long PULSE_MS     = 1000;   // 1 s pulse
static const unsigned long COOLDOWN_MS = 10000;  // 10 s cooldown

Lock locks[] = {
  {"images",   26, FAIL_SECURE},
  {"r2",       16, FAIL_SAFE},
  {"r3",       17, FAIL_SAFE},
  {"slider",   33, FAIL_SECURE},
  {"knocking", 25, FAIL_SECURE}
};

static const size_t LOCK_COUNT = sizeof(locks) / sizeof(locks[0]);

// ======================= STATE ===============================
EthernetClient ethClient;
PubSubClient mqtt(ethClient);

bool enabled = true;

unsigned long lastHbMs     = 0;
unsigned long lastMetricMs = 0;

static const unsigned long METRIC_INTERVAL_MS = 10000;

// error counter used in HB/log payloads
uint32_t g_errorCount = 0;

// ======================= UTILS ===============================
Lock* findLockById(const String &id) {
  for (size_t i = 0; i < LOCK_COUNT; i++) {
    if (id.equalsIgnoreCase(locks[i].id)) {
      return &locks[i];
    }
  }
  return nullptr;
}

String makeLockStateTopic(const char *id) {
  String t = LOCK_STATE_PREFIX;
  t += id;
  t += "/state";
  return t;
}

// ======================= LOG / HB / METRIC ===================
unsigned long currentHbIntervalMs() {
  switch (gameMode) {
    case MODE_INGAME: return 5000;   // tighter when players are in
    case MODE_MAINT:  return 10000;  // medium
    case MODE_OFF:
    default:          return 15000;  // slow when idle
  }
}

// Log helper; gate by gameMode
void publishLog(const char *lvl, const String &msg, const String &dataJson = String()) {
  // Respect gameMode:
  // - MODE_OFF: only ERR
  // - MODE_INGAME: INF/WRN/ERR (no DBG)
  // - MODE_MAINT: everything
  bool isErr = (strcmp(lvl, "ERR") == 0);
  bool isInf = (strcmp(lvl, "INF") == 0);
  bool isWrn = (strcmp(lvl, "WRN") == 0);
  bool isDbg = (strcmp(lvl, "DBG") == 0);

  bool allow = false;
  if (isErr) {
    allow = true;
  } else if (gameMode == MODE_OFF) {
    allow = false;
  } else if (gameMode == MODE_INGAME) {
    // No DBG when in-game
    allow = !isDbg;
  } else if (gameMode == MODE_MAINT) {
    allow = true;
  }

  if (!allow) {
    if (isErr) g_errorCount++;  // still count error even if somehow gated
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

void publishHeartbeatIfDue() {
  unsigned long now = millis();
  unsigned long interval = currentHbIntervalMs();
  if (now - lastHbMs < interval) return;
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

  String gm = (gameMode == MODE_OFF) ? "OFF" :
              (gameMode == MODE_INGAME) ? "INGAME" : "MAINT";

  String payload = String("{\"fw\":\"") + FW_VERSION +
                   "\",\"up\":" + String(now / 1000) +
                   ",\"k\":\"maglock_ctrl\"" +
                   ",\"mode\":\"" + gm + "\"" +
                   ",\"locks\":[";
  for (size_t i = 0; i < LOCK_COUNT; i++) {
    if (i > 0) payload += ",";
    payload += "{\"id\":\"";
    payload += locks[i].id;
    payload += "\",\"coil\":";
    payload += (locks[i].coilOn ? "1" : "0");
    payload += ",\"pulses\":";
    payload += String(locks[i].pulseCount);
    payload += ",\"pulse\":";
    payload += (locks[i].pulsing ? "1" : "0");
    payload += ",\"cooldown\":";
    payload += (locks[i].cooldown ? "1" : "0");
    payload += "}";
  }
  payload += "]}";

  mqtt.publish(TOPIC_METRIC, payload.c_str());
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
    logErr("OTA Update.end failed");
    return false;
  }

  publishLog("INF", "OTA OK, rebooting");
  delay(500);
  ESP.restart();
  return true;
}

// ======================= LOCK CONTROL ========================
void applyLockOutput(Lock &lk) {
  digitalWrite(lk.pin, lk.coilOn ? HIGH : LOW);
}

const char* lockStateName(const Lock &lk) {
  // Logical "state" for external world
  if (lk.mode == FAIL_SECURE) {
    // treat pulse as OPEN, otherwise CLOSED
    return lk.coilOn ? "OPEN" : "CLOSED";
  } else {
    // fail-safe: coilOn=locked
    return lk.coilOn ? "CLOSED" : "OPEN";
  }
}

void publishLockState(const Lock &lk, const char *reason) {
  String t = makeLockStateTopic(lk.id);

  String payload = String("{\"fw\":\"") + FW_VERSION +
                   "\",\"up\":" + String(millis() / 1000) +
                   ",\"id\":\"" + lk.id + "\"" +
                   ",\"state\":\"" + String(lockStateName(lk)) + "\"";
  if (reason && reason[0]) {
    payload += ",\"reason\":\"";
    payload += reason;
    payload += "\"";
  }
  payload += ",\"coil\":";
  payload += (lk.coilOn ? "1" : "0");
  payload += ",\"pulses\":";
  payload += String(lk.pulseCount);
  payload += "}";

  mqtt.publish(t.c_str(), payload.c_str());
}

// Fail-secure OPEN pulse with cooldown logic
void startPulse(Lock &lk, const char *reason) {
  if (lk.mode != FAIL_SECURE) {
    publishLog("WRN", String("OPEN on non-failsecure via pulse: ") + lk.id);
    return;
  }
  if (lk.pulsing || lk.cooldown) {
    publishLog("WRN", String("OPEN ignored (pulse/cooldown active) for ") + lk.id);
    return;
  }

  lk.coilOn = true;
  lk.pulsing = true;
  lk.cooldown = false;
  lk.pulseStartMs = millis();
  lk.pulseCount++;
  applyLockOutput(lk);
  publishLockState(lk, reason);
}

// Fail-safe: lock/unlock
void setFailSafe(Lock &lk, bool locked, const char *reason) {
  if (lk.mode != FAIL_SAFE) {
    publishLog("WRN", String("setFailSafe on non-failsafe: ") + lk.id);
    return;
  }
  lk.coilOn = locked;
  lk.pulsing = false;
  lk.cooldown = false;
  applyLockOutput(lk);
  publishLockState(lk, reason);
}

void updatePulseTimers() {
  unsigned long now = millis();
  for (size_t i = 0; i < LOCK_COUNT; i++) {
    Lock &lk = locks[i];

    if (lk.mode == FAIL_SECURE) {
      // handle active pulse
      if (lk.pulsing && (now - lk.pulseStartMs >= PULSE_MS)) {
        lk.pulsing = false;
        lk.coilOn = false;
        applyLockOutput(lk);
        publishLockState(lk, "pulse_done");
        // start cooldown
        lk.cooldown = true;
        lk.cooldownStartMs = now;
      }
      // handle cooldown expiry
      if (lk.cooldown && (now - lk.cooldownStartMs >= COOLDOWN_MS)) {
        lk.cooldown = false;
        publishLockState(lk, "cooldown_done");
      }
    }
  }
}

// ======================= LOCK COMMANDS =======================
void handleLockCommand(Lock &lk, const String &cmd) {
  if (!enabled) {
    publishLog("WRN", String("Lock cmd while DISABLED: ") + lk.id + " cmd=" + cmd);
    return;
  }

  if (cmd == "OPEN") {
    if (lk.mode == FAIL_SECURE) {
      startPulse(lk, "cmd:OPEN");
    } else { // FAIL_SAFE
      // OPEN on fail-safe = unlock (coil off)
      setFailSafe(lk, false, "cmd:OPEN");
    }
    return;
  }

  if (cmd == "CLOSE") {
    if (lk.mode == FAIL_SECURE) {
      // Force OFF, cancel pulse but not cooldown
      lk.coilOn = false;
      lk.pulsing = false;
      applyLockOutput(lk);
      publishLockState(lk, "cmd:CLOSE");
    } else { // FAIL_SAFE
      // CLOSE on fail-safe = lock (coil on)
      setFailSafe(lk, true, "cmd:CLOSE");
    }
    return;
  }

  // ER1 protocol: only OPEN/CLOSE are valid externally
  publishLog("WRN", String("Unknown lock cmd for ") + lk.id + ": " + cmd);
}

bool parseLockIdFromTopic(const String &topic, String &outId) {
  const String prefix = LOCK_CMD_PREFIX; // "esc/ctrl/lock/"
  const String suffix = "/cmd";

  if (!topic.startsWith(prefix) || !topic.endsWith(suffix)) return false;

  int start = prefix.length();
  int end = topic.length() - suffix.length();
  if (end <= start) return false;

  outId = topic.substring(start, end);
  return true;
}

// ======================= NODE CMDS & GAME MODE ===============
void handleNodeCmd(const String &msg) {
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

  publishLog("WRN", String("Unknown node CMD: ") + msg);
}

void handleGameModeMsg(const String &msg) {
  String m = msg;
  m.trim();
  m.toUpperCase();

  GameMode old = gameMode;

  if (m == "INGAME") {
    gameMode = MODE_INGAME;
  } else if (m == "MAINT" || m == "MAINTENANCE") {
    gameMode = MODE_MAINT;
  } else {
    gameMode = MODE_OFF;
  }

  if (gameMode != old) {
    String d = String("{\"from\":\"") +
               ((old == MODE_OFF) ? "OFF" : (old == MODE_INGAME ? "INGAME" : "MAINT")) +
               "\",\"to\":\"" +
               ((gameMode == MODE_OFF) ? "OFF" : (gameMode == MODE_INGAME ? "INGAME" : "MAINT")) +
               "\"}";
    publishLog("INF", "gameMode changed", d);
  }
}

// ======================= MQTT HANDLING =======================
void mqttCallback(char *topicC, byte *payload, unsigned int length) {
  String topic(topicC);
  String msg;
  msg.reserve(length);
  for (unsigned int i = 0; i < length; i++) msg += (char)payload[i];
  msg.trim();

  if (topic == TOPIC_CMD) {
    handleNodeCmd(msg);
    return;
  }

  if (topic == TOPIC_GAME) {
    handleGameModeMsg(msg);
    return;
  }

  // Lock commands: esc/ctrl/lock/<id>/cmd
  String lockId;
  if (parseLockIdFromTopic(topic, lockId)) {
    Lock *lk = findLockById(lockId);
    if (!lk) {
      logErr(String("Lock id not found: ") + lockId);
      return;
    }
    handleLockCommand(*lk, msg);
  }
}

void mqttReconnect() {
  int tries = 0;
  while (!mqtt.connected() && tries < 3) {
    if (mqtt.connect(CLIENT_ID, TOPIC_HB, 0, true, "offline")) {
      publishLog("INF", "MQTT connected");
      mqtt.subscribe(TOPIC_CMD);
      mqtt.subscribe(TOPIC_GAME);
      mqtt.subscribe("esc/ctrl/lock/+/cmd");
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

// ======================= SETUP / LOOP ========================
void setup() {
  Serial.begin(115200);
  delay(200);

  // Init lock pins (all LOW at boot)
  for (size_t i = 0; i < LOCK_COUNT; i++) {
    pinMode(locks[i].pin, OUTPUT);
    locks[i].coilOn = false;
    locks[i].pulsing = false;
    locks[i].cooldown = false;
    locks[i].pulseStartMs = 0;
    locks[i].cooldownStartMs = 0;
    locks[i].pulseCount = 0;
    applyLockOutput(locks[i]);
  }

  setupEthernet();
  mqttReconnect();

  publishLog("INF", "BOOT complete");
}

void loop() {
  if (!mqtt.connected()) {
    mqttReconnect();
  }
  mqtt.loop();

  updatePulseTimers();
  publishMetricsIfDue();
  publishHeartbeatIfDue();
}
