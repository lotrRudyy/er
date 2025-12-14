#include <Arduino.h>
#include <driver/i2s.h>
#include <math.h>
#include <limits.h>

// ================== CONFIG ==================
static const int   NUM_KEYS      = 85;       // DEBUG VERSION: first 10 keys
static const int   GAP_MS        = 500;      // wait before recording
static const int   RECORD_MS     = 10000;    // 10 seconds per key

// INMP441 (I2S)
static const i2s_port_t I2S_PORT = I2S_NUM_0;
static const int   I2S_BCLK      = 26;
static const int   I2S_LRCLK     = 25;
static const int   I2S_DIN       = 33;
static const int   I2S_FS_HZ     = 16000;    // common, safe

// MAX9814 (ADC)
static const int   ADC_PIN       = 36;       // GPIO36 (ADC1_CH0)
static const int   ADC_FS_HZ     = 8000;
static const int   ADC_CLIP_LO   = 5;        // clip thresholds for counts
static const int   ADC_CLIP_HI   = 4090;
// ============================================

// ---- Streaming stats (Welford + extras) ----
struct StreamStats {
  bool     init = false;
  int64_t  n = 0;
  int64_t  minv = 0;
  int64_t  maxv = 0;
  double   mean = 0.0;
  double   m2 = 0.0;         // sum of squares of diffs from mean (AC power)
  double   ac_peak = 0.0;    // max |x-mean|
  int64_t  clip_low = 0;     // count x <= lo_clip
  int64_t  clip_high = 0;    // count x >= hi_clip
  int64_t  stuck = 0;        // count consecutive repeats
  int64_t  zc = 0;           // zero-crossings of AC component

  bool     has_prev = false;
  int64_t  prev = 0;
  double   prev_ac = 0.0;

  void reset() { *this = StreamStats(); }

  inline void add(int64_t x, int64_t lo_clip = INT64_MIN, int64_t hi_clip = INT64_MAX) {
    if (!init) {
      init = true;
      n = 1;
      minv = maxv = x;
      mean = (double)x;
      m2 = 0.0;
      ac_peak = 0.0;
      clip_low = (x <= lo_clip) ? 1 : 0;
      clip_high = (x >= hi_clip) ? 1 : 0;
      stuck = 0;
      zc = 0;
      prev = x;
      has_prev = true;
      prev_ac = 0.0;
      return;
    }

    if (x < minv) minv = x;
    if (x > maxv) maxv = x;

    if (x <= lo_clip) clip_low++;
    if (x >= hi_clip) clip_high++;

    if (has_prev && x == prev) stuck++;
    prev = x; has_prev = true;

    // Welford update for mean + M2 (AC variance * n)
    n++;
    double dx = (double)x - mean;
    mean += dx / (double)n;
    double dx2 = (double)x - mean;
    m2 += dx * dx2;

    // AC component (relative to current mean)
    double ac = (double)x - mean;

    // ZCR from AC component sign changes
    if ((prev_ac <= 0.0 && ac > 0.0) || (prev_ac >= 0.0 && ac < 0.0)) zc++;
    prev_ac = ac;

    // AC peak
    double a = fabs(ac);
    if (a > ac_peak) ac_peak = a;
  }

  inline double rms_ac() const { return (n > 0) ? sqrt(m2 / (double)n) : 0.0; } // RMS of (x-mean)
  inline double p2p() const { return (double)(maxv - minv); }
  inline double crest() const { double r = rms_ac(); return (r > 0.0) ? (ac_peak / r) : 0.0; }
};

// ---- Per-key record row ----
struct KeyRow {
  char   key[8]{};
  int    record_ms = 0;

  // ADC raw (0..4095)
  uint16_t adc_min = 0;
  uint16_t adc_max = 0;
  double   adc_mean = 0;
  double   adc_rms = 0;
  double   adc_p2p = 0;
  double   adc_ac_peak = 0;
  double   adc_crest = 0;
  double   adc_zcr_hz = 0;
  int64_t  adc_stuck = 0;
  int64_t  adc_clip_low_cnt = 0;
  int64_t  adc_clip_high_cnt = 0;
  int64_t  adc_n = 0;
  double   adc_eff_fs = 0;
  double   adc_headroom_low = 0;   // mean - 0
  double   adc_headroom_high = 0;  // 4095 - mean
  bool     adc_clip_low_flag = false;
  bool     adc_clip_high_flag = false;

  // I2S signed (shifted)
  int32_t  i2s_min = 0;
  int32_t  i2s_max = 0;
  double   i2s_mean = 0;
  double   i2s_rms = 0;
  double   i2s_p2p = 0;
  double   i2s_ac_peak = 0;
  double   i2s_crest = 0;
  double   i2s_zcr_hz = 0;
  int64_t  i2s_stuck = 0;
  int64_t  i2s_n = 0;
  double   i2s_eff_fs = 0;
};

static KeyRow rows[NUM_KEYS];

// ---------- Key name generator (A0 upward incl sharps) ----------
// 85-key pianos are A0..C8. This generator follows the real sequence.
// idx=0 -> A0, 1->A#0, 2->B0, 3->C1, ...
static const char* NOTE_NAMES[12] = {"A","A#","B","C","C#","D","D#","E","F","F#","G","G#"};
static void keyNameFromIndex(int idx, char out[8]) {
  int note = idx % 12;
  int cycles = idx / 12;
  int octave = cycles;
  if (note >= 3) octave += 1; // C starts next octave
  snprintf(out, 8, "%s%d", NOTE_NAMES[note], octave);
}

// ---------- I2S init (INMP441) ----------
static void i2s_init_inmp441() {
  i2s_config_t cfg = {};
  cfg.mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_RX);
  cfg.sample_rate = I2S_FS_HZ;
  cfg.bits_per_sample = I2S_BITS_PER_SAMPLE_32BIT;
  cfg.channel_format = I2S_CHANNEL_FMT_ONLY_LEFT;  // L/R tied to GND => left
  cfg.communication_format = I2S_COMM_FORMAT_I2S;
  cfg.intr_alloc_flags = ESP_INTR_FLAG_LEVEL1;
  cfg.dma_buf_count = 6;
  cfg.dma_buf_len = 256;
  cfg.use_apll = false;
  cfg.tx_desc_auto_clear = false;
  cfg.fixed_mclk = 0;

  i2s_pin_config_t pins = {};
  pins.bck_io_num = I2S_BCLK;
  pins.ws_io_num  = I2S_LRCLK;
  pins.data_out_num = I2S_PIN_NO_CHANGE;
  pins.data_in_num  = I2S_DIN;

  i2s_driver_install(I2S_PORT, &cfg, 0, nullptr);
  i2s_set_pin(I2S_PORT, &pins);
  i2s_zero_dma_buffer(I2S_PORT);
}

// ---------- Build stamps / system info ----------
static void print_run_header_json_open() {
  Serial.println("\n=== JSON BEGIN ===");
  Serial.print("{\"node\":\"piano_cal\",\"version\":\"dual_mic_v2_stats\"");
  Serial.print(",\"build\":\"");
  Serial.print(__DATE__); Serial.print(" "); Serial.print(__TIME__);
  Serial.print("\"");

  Serial.print(",\"sdk\":\"");
  Serial.print(ESP.getSdkVersion());
  Serial.print("\"");

  Serial.print(",\"chip\":{");
  Serial.print("\"model\":\"ESP32\"");
  Serial.print(",\"rev\":");
  Serial.print(ESP.getChipRevision());
  Serial.print(",\"cpu_mhz\":");
  Serial.print(ESP.getCpuFreqMHz());
  Serial.print(",\"flash_mb\":");
  Serial.print(ESP.getFlashChipSize() / (1024 * 1024));
  Serial.print("}");

  Serial.print(",\"mem\":{");
  Serial.print("\"free_heap_start\":");
  Serial.print(ESP.getFreeHeap());
  Serial.print("}");

  Serial.print(",\"num_keys\":");
  Serial.print(NUM_KEYS);
  Serial.print(",\"record_ms\":");
  Serial.print(RECORD_MS);

  Serial.print(",\"adc\":{");
  Serial.print("\"pin\":"); Serial.print(ADC_PIN);
  Serial.print(",\"fs_hz\":"); Serial.print(ADC_FS_HZ);
  Serial.print(",\"width_bits\":12");
  Serial.print(",\"atten\":\"11db\"");
  Serial.print(",\"clip_lo\":"); Serial.print(ADC_CLIP_LO);
  Serial.print(",\"clip_hi\":"); Serial.print(ADC_CLIP_HI);
  Serial.print("}");

  Serial.print(",\"i2s\":{");
  Serial.print("\"bclk\":"); Serial.print(I2S_BCLK);
  Serial.print(",\"lrclk\":"); Serial.print(I2S_LRCLK);
  Serial.print(",\"din\":"); Serial.print(I2S_DIN);
  Serial.print(",\"fs_hz\":"); Serial.print(I2S_FS_HZ);
  Serial.print(",\"bits\":\"32_in\"");
  Serial.print(",\"shift_right\":8");
  Serial.print(",\"channel\":\"left_only\"");
  Serial.print("}");

  Serial.print(",\"rows\":[");
}

// ---------- Per-key capture ----------
static void record_one_key(int key_idx, KeyRow &row) {
  char name[8] = {};
  keyNameFromIndex(key_idx, name);
  strncpy(row.key, name, sizeof(row.key));
  row.record_ms = RECORD_MS;

  // Setup ADC
  analogReadResolution(12);
  analogSetPinAttenuation(ADC_PIN, ADC_11db);

  // Accumulators
  StreamStats adcS; adcS.reset();
  StreamStats i2sS; i2sS.reset();

  // Timing
  const uint32_t t_start_ms = millis();
  const uint32_t t_end_ms   = t_start_ms + RECORD_MS;
  const uint32_t adc_us_per = 1000000UL / (uint32_t)ADC_FS_HZ;
  uint32_t adc_next = micros();

  // I2S read buffer
  static int32_t i2s_buf[256];

  while ((int32_t)(millis() - t_end_ms) < 0) {
    // I2S chunk read
    size_t bytes_read = 0;
    esp_err_t err = i2s_read(I2S_PORT, (void*)i2s_buf, sizeof(i2s_buf), &bytes_read, 20 / portTICK_PERIOD_MS);
    if (err == ESP_OK && bytes_read > 0) {
      int frames = bytes_read / (int)sizeof(int32_t);
      for (int i = 0; i < frames; i++) {
        int32_t raw = i2s_buf[i];
        int32_t s = raw >> 8;                 // typical INMP441 alignment
        i2sS.add((int64_t)s);                 // no clip thresholds here
      }
    }

    // ADC sampling on schedule
    while ((int32_t)(micros() - adc_next) >= 0) {
      adc_next += adc_us_per;
      uint16_t a = (uint16_t)analogRead(ADC_PIN);
      adcS.add((int64_t)a, ADC_CLIP_LO, ADC_CLIP_HI);

      // If we fell behind badly, resync (prevents runaway loops)
      if ((int32_t)(micros() - adc_next) > (int32_t)(5 * adc_us_per)) {
        adc_next = micros() + adc_us_per;
      }
    }
  }

  const double seconds = (double)RECORD_MS / 1000.0;

  // Fill ADC row
  row.adc_min = (uint16_t)adcS.minv;
  row.adc_max = (uint16_t)adcS.maxv;
  row.adc_mean = adcS.mean;
  row.adc_rms  = adcS.rms_ac();
  row.adc_p2p  = adcS.p2p();
  row.adc_ac_peak = adcS.ac_peak;
  row.adc_crest = adcS.crest();
  row.adc_zcr_hz = (seconds > 0.0) ? ((double)adcS.zc / seconds) : 0.0;
  row.adc_stuck = adcS.stuck;
  row.adc_clip_low_cnt = adcS.clip_low;
  row.adc_clip_high_cnt = adcS.clip_high;
  row.adc_n = adcS.n;
  row.adc_eff_fs = (seconds > 0.0) ? ((double)adcS.n / seconds) : 0.0;
  row.adc_headroom_low = row.adc_mean;
  row.adc_headroom_high = 4095.0 - row.adc_mean;
  row.adc_clip_low_flag  = (row.adc_min <= ADC_CLIP_LO) || (row.adc_clip_low_cnt > 0);
  row.adc_clip_high_flag = (row.adc_max >= ADC_CLIP_HI) || (row.adc_clip_high_cnt > 0);

  // Fill I2S row
  row.i2s_min = (int32_t)i2sS.minv;
  row.i2s_max = (int32_t)i2sS.maxv;
  row.i2s_mean = i2sS.mean;
  row.i2s_rms  = i2sS.rms_ac();
  row.i2s_p2p  = i2sS.p2p();
  row.i2s_ac_peak = i2sS.ac_peak;
  row.i2s_crest = i2sS.crest();
  row.i2s_zcr_hz = (seconds > 0.0) ? ((double)i2sS.zc / seconds) : 0.0;
  row.i2s_stuck = i2sS.stuck;
  row.i2s_n = i2sS.n;
  row.i2s_eff_fs = (seconds > 0.0) ? ((double)i2sS.n / seconds) : 0.0;

  // Print one compact row (human)
  Serial.printf(
    "[%s] "
    "ADC{min=%u max=%u mean=%.2f rms=%.2f p2p=%.0f acpk=%.2f crest=%.2f zcr=%.1fHz stuck=%lld clipL=%lld clipH=%lld n=%lld fs=%.1f headL=%.1f headH=%.1f}  "
    "I2S{min=%ld max=%ld mean=%.2f rms=%.2f p2p=%.0f acpk=%.2f crest=%.2f zcr=%.1fHz stuck=%lld n=%lld fs=%.1f}\n",
    row.key,
    row.adc_min, row.adc_max, row.adc_mean, row.adc_rms, row.adc_p2p, row.adc_ac_peak, row.adc_crest,
    row.adc_zcr_hz, (long long)row.adc_stuck, (long long)row.adc_clip_low_cnt, (long long)row.adc_clip_high_cnt,
    (long long)row.adc_n, row.adc_eff_fs, row.adc_headroom_low, row.adc_headroom_high,
    (long)row.i2s_min, (long)row.i2s_max, row.i2s_mean, row.i2s_rms, row.i2s_p2p, row.i2s_ac_peak, row.i2s_crest,
    row.i2s_zcr_hz, (long long)row.i2s_stuck, (long long)row.i2s_n, row.i2s_eff_fs
  );
}

// ---------- JSON output ----------
static void print_json_all_rows_and_close() {
  print_run_header_json_open();

  for (int i = 0; i < NUM_KEYS; i++) {
    const KeyRow &r = rows[i];
    if (i) Serial.print(",");

    Serial.print("{\"key\":\""); Serial.print(r.key); Serial.print("\"");
    Serial.print(",\"record_ms\":"); Serial.print(r.record_ms);

    Serial.print(",\"adc\":{");
      Serial.print("\"min\":"); Serial.print(r.adc_min);
      Serial.print(",\"max\":"); Serial.print(r.adc_max);
      Serial.print(",\"mean\":"); Serial.print(r.adc_mean, 2);
      Serial.print(",\"rms\":"); Serial.print(r.adc_rms, 2);
      Serial.print(",\"p2p\":"); Serial.print(r.adc_p2p, 0);
      Serial.print(",\"ac_peak\":"); Serial.print(r.adc_ac_peak, 2);
      Serial.print(",\"crest\":"); Serial.print(r.adc_crest, 2);
      Serial.print(",\"zcr_hz\":"); Serial.print(r.adc_zcr_hz, 2);
      Serial.print(",\"stuck\":"); Serial.print((long long)r.adc_stuck);
      Serial.print(",\"clip_low_cnt\":"); Serial.print((long long)r.adc_clip_low_cnt);
      Serial.print(",\"clip_high_cnt\":"); Serial.print((long long)r.adc_clip_high_cnt);
      Serial.print(",\"clip_low\":"); Serial.print(r.adc_clip_low_flag ? "true" : "false");
      Serial.print(",\"clip_high\":"); Serial.print(r.adc_clip_high_flag ? "true" : "false");
      Serial.print(",\"n\":"); Serial.print((long long)r.adc_n);
      Serial.print(",\"eff_fs\":"); Serial.print(r.adc_eff_fs, 2);
      Serial.print(",\"headroom_low\":"); Serial.print(r.adc_headroom_low, 2);
      Serial.print(",\"headroom_high\":"); Serial.print(r.adc_headroom_high, 2);
    Serial.print("}");

    Serial.print(",\"i2s\":{");
      Serial.print("\"min\":"); Serial.print((long)r.i2s_min);
      Serial.print(",\"max\":"); Serial.print((long)r.i2s_max);
      Serial.print(",\"mean\":"); Serial.print(r.i2s_mean, 2);
      Serial.print(",\"rms\":"); Serial.print(r.i2s_rms, 2);
      Serial.print(",\"p2p\":"); Serial.print(r.i2s_p2p, 0);
      Serial.print(",\"ac_peak\":"); Serial.print(r.i2s_ac_peak, 2);
      Serial.print(",\"crest\":"); Serial.print(r.i2s_crest, 2);
      Serial.print(",\"zcr_hz\":"); Serial.print(r.i2s_zcr_hz, 2);
      Serial.print(",\"stuck\":"); Serial.print((long long)r.i2s_stuck);
      Serial.print(",\"n\":"); Serial.print((long long)r.i2s_n);
      Serial.print(",\"eff_fs\":"); Serial.print(r.i2s_eff_fs, 2);
    Serial.print("}");

    Serial.print("}");
  }

  Serial.print("]");

  // update end heap
  Serial.print(",\"mem_end\":{");
  Serial.print("\"free_heap_end\":");
  Serial.print(ESP.getFreeHeap());
  Serial.print("}");

  Serial.println("}");
  Serial.println("=== JSON END ===\n");
}

// ---------- Serial command parsing ----------
static bool read_line(String &out) {
  while (Serial.available()) {
    char c = (char)Serial.read();
    if (c == '\r') continue;
    if (c == '\n') return true;
    out += c;
    if (out.length() > 96) out = out.substring(0, 96);
  }
  return false;
}

static void print_prompt(int key_idx) {
  char name[8] = {};
  keyNameFromIndex(key_idx, name);
  Serial.println();
  Serial.printf("Play key %s now.\n", name);
  Serial.printf("Waiting %d ms...\n", GAP_MS);
}

void setup() {
  Serial.begin(115200);
  delay(300);

  Serial.println("dual_mic_cal_10keys booted.");
  Serial.println("Type: start");
  Serial.println("INMP441: BCLK=26 LRCLK=25 DIN=33 (L/R tied to GND => LEFT)");
  Serial.println("MAX9814: ADC GPIO36 (ADC1)");
  Serial.println("This run records BOTH mics per key and prints JSON at end.");

  i2s_init_inmp441();
}

void loop() {
  static bool running = false;
  static String line;

  if (!running) {
    if (read_line(line)) {
      line.trim();
      if (line.equalsIgnoreCase("start")) {
        running = true;
        Serial.println("Starting 10-key capture (A0 upward).");
      } else {
        Serial.println("Type: start");
      }
      line = "";
    }
    return;
  }

  for (int i = 0; i < NUM_KEYS; i++) {
    print_prompt(i);
    delay(GAP_MS);
    Serial.printf("Recording %d ms...\n", RECORD_MS);
    record_one_key(i, rows[i]);
  }

  print_json_all_rows_and_close();

  Serial.println("Done. Type: start (to run again).");
  running = false;
}
