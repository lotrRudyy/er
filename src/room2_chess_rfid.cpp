#include <Arduino.h>
#include <SPI.h>
#include <MFRC522.h>

// ======================= FIRMWARE INFO =======================
static const char *FW_VERSION = "chess_rfid_v1.0_4rc522_uart";

// ======================= RFID CONFIG =========================
#define NUM_READERS    4

// SPI (VSPI)
#define RFID_SCK       18
#define RFID_MISO      19
#define RFID_MOSI      23

// RC522 control pins
#define RFID_RST_PIN   22
const uint8_t RFID_CS_PINS[NUM_READERS] = {
  5,   // Reader 0 - king
  17,  // Reader 1 - queen
  16,  // Reader 2 - rook
  4    // Reader 3 - horse
};

// ======================= UART TO NET-ESP =====================
#define UART_RX_PIN    26   // RX2 (from net ESP TX2)
#define UART_TX_PIN    25   // TX2 (to net ESP RX2)
HardwareSerial &NET_SERIAL = Serial2;

// ======================= TIMING ==============================
const uint32_t READER_INTERVAL_MS   = 125;   // 125 ms per reader -> 500 ms per full cycle
const uint32_t SNAPSHOT_INTERVAL_MS = 500;   // send pattern every 500 ms
const uint32_t CARD_LOST_MS         = 2000;  // if no read for 2 s -> treat as NONE

// ======================= GLOBALS =============================
MFRC522 rfid[NUM_READERS];

String   currentUid[NUM_READERS];  // "" means NONE
uint32_t lastSeenMs[NUM_READERS];

uint8_t  currentReader       = 0;
uint32_t lastReaderSwitchMs  = 0;
uint32_t lastSnapshotMs      = 0;

// ======================= HELPERS =============================
String uidToHex(const MFRC522::Uid &uid) {
  String s;
  for (byte i = 0; i < uid.size; i++) {
    if (uid.uidByte[i] < 0x10) s += "0";
    s += String(uid.uidByte[i], HEX);
  }
  s.toUpperCase();
  return s;
}

void pollCurrentReader() {
  uint32_t now = millis();
  uint8_t i = currentReader;
  MFRC522 &r = rfid[i];

  // Try to read this one reader
  if (r.PICC_IsNewCardPresent() && r.PICC_ReadCardSerial()) {
    String uidStr = uidToHex(r.uid);
    currentUid[i] = uidStr;
    lastSeenMs[i] = now;

    // Debug to USB serial
    Serial.print("Reader ");
    Serial.print(i);
    Serial.print(" UID: ");
    Serial.println(uidStr);

    r.PICC_HaltA();
    r.PCD_StopCrypto1();
  } else {
    // No new read; if we had a card and no activity for CARD_LOST_MS -> clear
    if (currentUid[i].length() > 0 && (now - lastSeenMs[i] > CARD_LOST_MS)) {
      currentUid[i] = "";
    }
  }
}

void sendSnapshot() {
  // Format: SNAP R0=XXXX R1=XXXX R2=XXXX R3=XXXX (or NONE)
  NET_SERIAL.print(F("SNAP"));
  for (uint8_t i = 0; i < NUM_READERS; i++) {
    NET_SERIAL.print(F(" R"));
    NET_SERIAL.print(i);
    NET_SERIAL.print('=');
    if (currentUid[i].length() > 0) {
      NET_SERIAL.print(currentUid[i]);
    } else {
      NET_SERIAL.print(F("NONE"));
    }
  }
  NET_SERIAL.print('\n');

  // Optional mirror for debug
  Serial.print(F("SNAP"));
  for (uint8_t i = 0; i < NUM_READERS; i++) {
    Serial.print(F(" R"));
    Serial.print(i);
    Serial.print('=');
    if (currentUid[i].length() > 0) {
      Serial.print(currentUid[i]);
    } else {
      Serial.print(F("NONE"));
    }
  }
  Serial.println();
}

// ======================= SETUP / LOOP ========================
void setup() {
  Serial.begin(115200);
  delay(300);
  Serial.println();
  Serial.print(F("=== CHESS RFID (4x RC522) UART NODE === "));
  Serial.println(FW_VERSION);

  // UART link to net ESP
  NET_SERIAL.begin(115200, SERIAL_8N1, UART_RX_PIN, UART_TX_PIN);
  NET_SERIAL.print(F("# BOOT "));
  NET_SERIAL.println(FW_VERSION);

  // SPI for RC522
  SPI.begin(RFID_SCK, RFID_MISO, RFID_MOSI);

  pinMode(RFID_RST_PIN, OUTPUT);
  digitalWrite(RFID_RST_PIN, HIGH);

  for (uint8_t i = 0; i < NUM_READERS; i++) {
    pinMode(RFID_CS_PINS[i], OUTPUT);
    digitalWrite(RFID_CS_PINS[i], HIGH);
  }

  // Init each reader
  for (uint8_t i = 0; i < NUM_READERS; i++) {
    rfid[i] = MFRC522(RFID_CS_PINS[i], RFID_RST_PIN);
    rfid[i].PCD_Init(RFID_CS_PINS[i], RFID_RST_PIN);
    delay(50);

    Serial.print(F("[RFID] Reader "));
    Serial.print(i);
    Serial.print(F(" (SS="));
    Serial.print(RFID_CS_PINS[i]);
    Serial.println(F(") init"));

    currentUid[i] = "";
    lastSeenMs[i] = 0;
  }

  currentReader       = 0;
  lastReaderSwitchMs  = millis();
  lastSnapshotMs      = millis();
}

void loop() {
  uint32_t now = millis();

  // Round-robin: one reader every READER_INTERVAL_MS
  if (now - lastReaderSwitchMs >= READER_INTERVAL_MS) {
    lastReaderSwitchMs = now;

    pollCurrentReader();

    currentReader++;
    if (currentReader >= NUM_READERS) {
      currentReader = 0;
    }
  }

  // Periodic snapshot to net ESP
  if (now - lastSnapshotMs >= SNAPSHOT_INTERVAL_MS) {
    lastSnapshotMs = now;
    sendSnapshot();
  }
}
