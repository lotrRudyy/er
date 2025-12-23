#include <Arduino.h>
#include <SPI.h>
#include <Ethernet.h>
#include <MFRC522.h>

// ---------- SPI pins ----------
#define PIN_SCK   18
#define PIN_MISO  19
#define PIN_MOSI  23

// ---------- W5500 ----------
#define ETH_CS    15
#define ETH_RST   27

// ---------- RC522 readers ----------
#define RFID1_CS  14
#define RFID1_RST 32

#define RFID2_CS  13
#define RFID2_RST 33

#define RFID3_CS  17
#define RFID3_RST 25

#define RFID4_CS  16
#define RFID4_RST 26

// ---------- Network (static) ----------
static byte MAC[6] = { 0xDE, 0xAD, 0xBE, 0xEF, 0x32, 0x13 };
static IPAddress IP(192,168,0,13);
static IPAddress DNS(192,168,0,1);
static IPAddress GW(192,168,0,1);
static IPAddress MASK(255,255,255,0);

// Derive to access protected pins safely (MFRC522 lib)
struct MFRC522X : public MFRC522 {
  using MFRC522::MFRC522;
  byte csPin()  const { return _chipSelectPin; }
  byte rstPin() const { return _resetPowerDownPin; }
};

MFRC522X rfid1(RFID1_CS, RFID1_RST);
MFRC522X rfid2(RFID2_CS, RFID2_RST);
MFRC522X rfid3(RFID3_CS, RFID3_RST);
MFRC522X rfid4(RFID4_CS, RFID4_RST);

// ---------- Helpers ----------
static inline void deselectAll() {
  digitalWrite(ETH_CS, HIGH);
  digitalWrite(RFID1_CS, HIGH);
  digitalWrite(RFID2_CS, HIGH);
  digitalWrite(RFID3_CS, HIGH);
  digitalWrite(RFID4_CS, HIGH);
}

static inline void prepForEth() {
  digitalWrite(RFID1_CS, HIGH);
  digitalWrite(RFID2_CS, HIGH);
  digitalWrite(RFID3_CS, HIGH);
  digitalWrite(RFID4_CS, HIGH);
}

static inline void prepForRfid() {
  digitalWrite(ETH_CS, HIGH);
}

static void resetEth() {
  pinMode(ETH_RST, OUTPUT);
  digitalWrite(ETH_RST, LOW);
  delay(10);
  digitalWrite(ETH_RST, HIGH);
  delay(150);
}

static const char* hwName(EthernetHardwareStatus s) {
  switch (s) {
    case EthernetW5500: return "W5500";
    case EthernetW5200: return "W5200";
    case EthernetW5100: return "W5100";
    case EthernetNoHardware: return "NoHardware";
    default: return "Unknown";
  }
}
static const char* linkName(EthernetLinkStatus s) {
  switch (s) {
    case LinkON:  return "ON";
    case LinkOFF: return "OFF";
    case Unknown: return "UNKNOWN";
    default:      return "?";
  }
}

// ---- RFID init without SPI.begin() or PCD_Init() ----
static void rfid_init_no_spibegin(MFRC522X &r) {
  deselectAll();

  pinMode(r.csPin(), OUTPUT);
  digitalWrite(r.csPin(), HIGH);

  pinMode(r.rstPin(), OUTPUT);
  digitalWrite(r.rstPin(), LOW);
  delay(50);
  digitalWrite(r.rstPin(), HIGH);
  delay(50);

  r.PCD_Reset();

  r.PCD_WriteRegister(MFRC522::TModeReg, 0x80);
  r.PCD_WriteRegister(MFRC522::TPrescalerReg, 0xA9);
  r.PCD_WriteRegister(MFRC522::TReloadRegH, 0x03);
  r.PCD_WriteRegister(MFRC522::TReloadRegL, 0xE8);
  r.PCD_WriteRegister(MFRC522::TxASKReg, 0x40);
  r.PCD_WriteRegister(MFRC522::ModeReg, 0x3D);

  r.PCD_SetAntennaGain(MFRC522::RxGain_max);
  r.PCD_AntennaOn();

  delay(10);
}

// Print UID helper
static void printUid(const MFRC522 &r) {
  for (byte i = 0; i < r.uid.size; i++) Serial.printf("%02X", r.uid.uidByte[i]);
}

// Poll a reader; ONLY prints when a tag is read
static void pollReaderTagOnly(const char* name, MFRC522 &r) {
  prepForRfid();

  if (r.PICC_IsNewCardPresent() && r.PICC_ReadCardSerial()) {
    Serial.print("[RFID ");
    Serial.print(name);
    Serial.print("] TAG UID: ");
    printUid(r);
    Serial.println();

    r.PICC_HaltA();
    r.PCD_StopCrypto1();
  }
}

void setup() {
  Serial.begin(115200);
  delay(500);

  // ---- CS discipline FIRST ----
  pinMode(ETH_CS, OUTPUT);
  pinMode(RFID1_CS, OUTPUT);
  pinMode(RFID2_CS, OUTPUT);
  pinMode(RFID3_CS, OUTPUT);
  pinMode(RFID4_CS, OUTPUT);
  deselectAll();

  // ---- Bring up SPI exactly once ----
  SPI.begin(PIN_SCK, PIN_MISO, PIN_MOSI, -1);

  // ---- Ethernet ----
  Serial.println("\n[ETH] init");
  resetEth();
  Ethernet.init(ETH_CS);
  Ethernet.begin(MAC, IP, DNS, GW, MASK);
  delay(200);

  Serial.print("[ETH] hardware: ");
  Serial.println(hwName(Ethernet.hardwareStatus()));
  Serial.print("[ETH] link: ");
  Serial.println(linkName(Ethernet.linkStatus()));
  Serial.print("[ETH] IP: ");
  Serial.println(Ethernet.localIP());

  // ---- RFID readers ----
  Serial.println("\n[RFID] init reader1");
  rfid_init_no_spibegin(rfid1);
  Serial.print("[RFID1] VersionReg: 0x");
  Serial.println(rfid1.PCD_ReadRegister(MFRC522::VersionReg), HEX);

  Serial.println("\n[RFID] init reader2");
  rfid_init_no_spibegin(rfid2);
  Serial.print("[RFID2] VersionReg: 0x");
  Serial.println(rfid2.PCD_ReadRegister(MFRC522::VersionReg), HEX);

  Serial.println("\n[RFID] init reader3");
  rfid_init_no_spibegin(rfid3);
  Serial.print("[RFID3] VersionReg: 0x");
  Serial.println(rfid3.PCD_ReadRegister(MFRC522::VersionReg), HEX);

  Serial.println("\n[RFID] init reader4");
  rfid_init_no_spibegin(rfid4);
  Serial.print("[RFID4] VersionReg: 0x");
  Serial.println(rfid4.PCD_ReadRegister(MFRC522::VersionReg), HEX);

  Serial.println("\n=== INTEGRATION TEST START (W5500 + 4x RC522) ===");
  Serial.println("Readers print ONLY when they read a tag (move a card around).");
}

void loop() {
  static uint32_t lastRfid = 0;
  static uint32_t lastEth  = 0;
  static int which = 0;

  // Poll one reader every 100ms (each reader every 400ms)
  if (millis() - lastRfid >= 100) {
    lastRfid = millis();

    switch (which) {
      case 0: pollReaderTagOnly("1", rfid1); break;
      case 1: pollReaderTagOnly("2", rfid2); break;
      case 2: pollReaderTagOnly("3", rfid3); break;
      case 3: pollReaderTagOnly("4", rfid4); break;
    }
    which = (which + 1) & 3;
  }

  // Ethernet status every 2000ms (so it doesn't spam)
  if (millis() - lastEth >= 2000) {
    lastEth = millis();
    prepForEth();
    auto hw = Ethernet.hardwareStatus();
    auto lk = Ethernet.linkStatus();
    Serial.print("[ETH] ");
    Serial.print(hwName(hw));
    Serial.print(" link=");
    Serial.println(linkName(lk));
  }
}
