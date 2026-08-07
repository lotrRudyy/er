#include <Arduino.h>
#include <esp_system.h>

// From your existing candles firmware
static constexpr uint8_t CANDLE_PINS[4] = {12, 14, 26, 25};
static constexpr uint8_t MIC_PINS[4]    = {33, 32, 35, 34};

static bool candleState[4] = {false, false, false, false};
static uint32_t lastPrintMs = 0;

const char* resetReasonName(esp_reset_reason_t reason) {
    switch (reason) {
        case ESP_RST_UNKNOWN:   return "UNKNOWN";
        case ESP_RST_POWERON:   return "POWER ON";
        case ESP_RST_EXT:       return "EXTERNAL RESET";
        case ESP_RST_SW:        return "SOFTWARE RESET";
        case ESP_RST_PANIC:     return "CRASH / PANIC";
        case ESP_RST_INT_WDT:   return "INTERRUPT WATCHDOG";
        case ESP_RST_TASK_WDT:  return "TASK WATCHDOG";
        case ESP_RST_WDT:       return "OTHER WATCHDOG";
        case ESP_RST_DEEPSLEEP: return "DEEP SLEEP";
        case ESP_RST_BROWNOUT:  return "BROWNOUT / POWER PROBLEM";
        case ESP_RST_SDIO:      return "SDIO RESET";
        default:                return "UNRECOGNIZED";
    }
}

void setCandle(uint8_t index, bool on) {
    if (index >= 4) {
        return;
    }

    candleState[index] = on;

    // Your original firmware uses HIGH = candle on.
    digitalWrite(CANDLE_PINS[index], on ? HIGH : LOW);

    Serial.printf(
        "Candle %u, GPIO %u: %s\n",
        index + 1,
        CANDLE_PINS[index],
        on ? "ON" : "OFF"
    );
}

void allCandlesOff() {
    for (uint8_t i = 0; i < 4; i++) {
        setCandle(i, false);
    }
}

void printInstructions() {
    Serial.println();
    Serial.println("Commands:");
    Serial.println("  1 = toggle candle output GPIO12");
    Serial.println("  2 = toggle candle output GPIO14");
    Serial.println("  3 = toggle candle output GPIO26");
    Serial.println("  4 = toggle candle output GPIO25");
    Serial.println("  0 = switch all outputs off");
    Serial.println("  ? = print these instructions");
    Serial.println();
}

void setup() {
    // Make sure candle outputs start off.
    for (uint8_t i = 0; i < 4; i++) {
        pinMode(CANDLE_PINS[i], OUTPUT);
        digitalWrite(CANDLE_PINS[i], LOW);
    }

    Serial.begin(115200);
    delay(1500);

    Serial.println();
    Serial.println("================================");
    Serial.println("Candles ESP32 hardware test");
    Serial.println("================================");
    Serial.printf(
        "Reset reason: %s\n",
        resetReasonName(esp_reset_reason())
    );

    analogReadResolution(12);

    for (uint8_t i = 0; i < 4; i++) {
        analogSetPinAttenuation(MIC_PINS[i], ADC_11db);
    }

    printInstructions();
}

void loop() {
    // Handle commands typed into the serial monitor.
    while (Serial.available() > 0) {
        const char command = Serial.read();

        if (command >= '1' && command <= '4') {
            const uint8_t index = command - '1';
            setCandle(index, !candleState[index]);
        } else if (command == '0') {
            allCandlesOff();
        } else if (command == '?') {
            printInstructions();
        }
    }

    // Print microphone values four times per second.
    if (millis() - lastPrintMs >= 250) {
        lastPrintMs = millis();

        Serial.printf(
            "ADC: M1=%4d M2=%4d M3=%4d M4=%4d | "
            "OUT: %d %d %d %d | uptime=%lu ms\n",
            analogRead(MIC_PINS[0]),
            analogRead(MIC_PINS[1]),
            analogRead(MIC_PINS[2]),
            analogRead(MIC_PINS[3]),
            candleState[0],
            candleState[1],
            candleState[2],
            candleState[3],
            millis()
        );
    }
}
