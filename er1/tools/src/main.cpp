#include <Arduino.h>
#include <SPI.h>

#define PIN_MISO 19
#define PIN_MOSI 23
#define PIN_SCK  18
#define PIN_CS   15
#define PIN_RST  26

SPIClass spi(VSPI);

// MFRC522 registers
#define CommandReg     0x01
#define ComIEnReg      0x02
#define DivIEnReg      0x03
#define ComIrqReg      0x04
#define DivIrqReg      0x05
#define ErrorReg       0x06
#define Status1Reg     0x07
#define FIFODataReg    0x09
#define FIFOLevelReg   0x0A
#define ControlReg     0x0C
#define BitFramingReg  0x0D
#define ModeReg        0x11
#define TxControlReg   0x14
#define TxASKReg       0x15
#define CRCResultRegL  0x22
#define CRCResultRegH  0x21
#define VersionReg     0x37

static inline uint8_t rreg(uint8_t reg){
  digitalWrite(PIN_CS, LOW);
  spi.transfer((reg<<1) | 0x80);
  uint8_t v = spi.transfer(0x00);
  digitalWrite(PIN_CS, HIGH);
  return v;
}
static inline void wreg(uint8_t reg, uint8_t val){
  digitalWrite(PIN_CS, LOW);
  spi.transfer((reg<<1) & 0x7E);
  spi.transfer(val);
  digitalWrite(PIN_CS, HIGH);
}

void resetChip(){
  digitalWrite(PIN_RST, LOW); delay(50);
  digitalWrite(PIN_RST, HIGH); delay(50);
  wreg(CommandReg, 0x0F); // soft reset
  delay(50);
}

void antennaOn(){
  uint8_t v = rreg(TxControlReg);
  if ((v & 0x03) != 0x03) wreg(TxControlReg, v | 0x03);
}

void fifoClear(){
  wreg(FIFOLevelReg, 0x80); // flush FIFO
}

void setup() {
  Serial.begin(115200);
  delay(500);

  pinMode(PIN_CS, OUTPUT);
  pinMode(PIN_RST, OUTPUT);
  digitalWrite(PIN_CS, HIGH);
  digitalWrite(PIN_RST, HIGH);

  spi.begin(PIN_SCK, PIN_MISO, PIN_MOSI, PIN_CS);
  spi.beginTransaction(SPISettings(1000000, MSBFIRST, SPI_MODE0));

  Serial.println("\n=== RC522 REQA TEST ===");
  resetChip();

  Serial.printf("VersionReg=0x%02X\n", rreg(VersionReg));

  // Init basics similar to common libs
  wreg(TxASKReg, 0x40);   // 100% ASK
  wreg(ModeReg, 0x3D);    // CRC preset 0x6363 etc (common)
  antennaOn();

  // Prepare transceive of REQA (7 bits)
  fifoClear();
  wreg(BitFramingReg, 0x07); // 7 bits in last byte

  wreg(FIFODataReg, 0x26);   // REQA
  wreg(CommandReg, 0x0C);    // Transceive
  wreg(BitFramingReg, 0x87); // StartSend=1 + 7 bits

  // Wait a bit then read result
  delay(5);
  uint8_t irq = rreg(ComIrqReg);
  uint8_t err = rreg(ErrorReg);
  uint8_t fl  = rreg(FIFOLevelReg);

  Serial.printf("ComIrqReg=0x%02X ErrorReg=0x%02X FIFOLevel=%u\n", irq, err, fl);

  if (fl >= 2) {
    uint8_t a = rreg(FIFODataReg);
    uint8_t b = rreg(FIFODataReg);
    Serial.printf("ATQA bytes: %02X %02X  (tag detected)\n", a, b);
  } else {
    Serial.println("No ATQA (no tag detected or RF not working). Try holding a card right on the antenna.");
  }
}

void loop() {}
