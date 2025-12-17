#include <Arduino.h>
#include <driver/i2s.h>
#include <SPIFFS.h>
#include <math.h>
#include "esp_dsp.h"
#include <ArduinoJson.h>
#include <float.h>

static const char* BUILD_TAG = "cal_v7_shift12_logspec_autoshift";

// =====================================================
// CONFIG
// =====================================================
static const i2s_port_t I2S_PORT = I2S_NUM_0;
static const int I2S_BCLK  = 26;
static const int I2S_LRCLK = 25;
static const int I2S_DIN   = 33;

// Audio / FFT
static const int   FS_HZ   = 48000;
static const int   N_FFT   = 4096;
static const int   HOP     = 256;          // 48k/256 ≈ 187.5 rows/sec
static const float F_MIN   = 20.0f;
static const float F_MAX   = 4000.0f;

// Peaks + bands
static const int   K_PEAKS = 8;
static const int   B_BANDS = 6;

// Calibration schedule (ms)
static const uint32_t PH0_SILENCE_MS = 1000;   // phase 0
static const uint32_t P1_PRESS_MS    = 2000;   // phase 1
static const uint32_t P2_GAP_MS      = 1000;   // phase 2
static const uint32_t P3_PRESS_MS    = 2000;   // phase 3
static const uint32_t P4_GAP_MS      = 1000;   // phase 4
static const uint32_t P5_PRESS_MS    = 2000;   // phase 5
static const uint32_t P6_TAIL_MS     = 1500;   // phase 6 (tail/decay)

static const uint32_t SETTLE_MS      = 200;
static const uint32_t RESP_MS_INIT   = 200;
static const float    ONSET_K_STD    = 8.0f;

// 10s silence capture (stored separately)
static const uint32_t NOISE_CAPTURE_MS = 10000;
static const char* NOISE_PATH = "/noise.json";

// Keys
static const int NUM_KEYS = 85;
static const char* KEY_LABELS[NUM_KEYS] = {
  "A0","A#0","B0",
  "C1","C#1","D1","D#1","E1","F1","F#1","G1","G#1",
  "A1","A#1","B1",
  "C2","C#2","D2","D#2","E2","F2","F#2","G2","G#2",
  "A2","A#2","B2",
  "C3","C#3","D3","D#3","E3","F3","F#3","G3","G#3",
  "A3","A#3","B3",
  "C4","C#4","D4","D#4","E4","F4","F#4","G4","G#4",
  "A4","A#4","B4",
  "C5","C#5","D5","D#5","E5","F5","F#5","G5","G#5",
  "A5","A#5","B5",
  "C6","C#6","D6","D#6","E6","F6","F#6","G6","G#6",
  "A6","A#6","B6",
  "C7","C#7","D7","D#7","E7","F7","F#7","G7","G#7",
  "A7"
};

// =====================================================
// Binary row format (stored to SPIFFS)
// =====================================================
#pragma pack(push, 1)
struct CalRowBin {
  uint32_t t_ms;
  uint8_t  key_idx;
  uint8_t  cap_phase;     // 0..6
  uint16_t reserved;

  float rms_ac;
  float ac_peak;
  float zc;
  float vmin;
  float vmax;

  float spec_total;
  float centroid_hz;
  float bandwidth_hz;
  float rolloff85_hz;
  float flatness;

  float band[B_BANDS];

  float p_hz[K_PEAKS];
  float p_mag[K_PEAKS];
};
#pragma pack(pop)

// =====================================================
// ADC scaling / clipping control
// =====================================================
static int g_shift_right = 12; // default: reduce clipping vs >>11
static uint32_t g_clip_samples = 0;
static uint32_t g_total_samples = 0;

// =====================================================
// Globals for FFT
// =====================================================
static float window_f[N_FFT];
static float fft_in[2 * N_FFT];   // interleaved complex: re, im
static float mag[N_FFT / 2 + 1];

static int16_t ring[N_FFT];
static int ring_fill = 0;

static File curFile;
static String curFilename;

static uint32_t resp_ms_est = RESP_MS_INIT;
static bool resp_locked = false;

// For auto onset detect
static double baseline_mean = 0.0;
static double baseline_m2   = 0.0;
static uint32_t baseline_n  = 0;
static float baseline_std() {
  if (baseline_n < 2) return 0.0f;
  return (float)sqrt(baseline_m2 / (double)(baseline_n - 1));
}
static void baseline_reset() {
  baseline_mean = 0.0; baseline_m2 = 0.0; baseline_n = 0;
}
static void baseline_push(float x) {
  baseline_n++;
  double delta = x - baseline_mean;
  baseline_mean += delta / (double)baseline_n;
  double delta2 = x - baseline_mean;
  baseline_m2 += delta * delta2;
}

// press command timestamps (relative to capture start)
static uint32_t t_cmd_press[3];
static bool onset_found[3];
static uint32_t t_onset_press[3];

// =====================================================
// Simple accumulators (for JSON)
// =====================================================
struct Acc {
  uint32_t n = 0;
  double sum = 0.0;
  double sumsq = 0.0;
  void push(float x) { n++; sum += x; sumsq += (double)x * (double)x; }
  float mean() const { return (n ? (float)(sum / (double)n) : 0.0f); }
  float std() const {
    if (n < 2) return 0.0f;
    double m = sum / (double)n;
    double v = (sumsq / (double)n) - (m*m);
    if (v < 0.0) v = 0.0;
    return (float)sqrt(v);
  }
};

static Acc acc_rms[7];
static Acc acc_flat[7];
static Acc acc_cent[7];
static Acc acc_roll[7];
static Acc acc_band[7][B_BANDS];
static Acc acc_p_hz_press[K_PEAKS];
static Acc acc_p_mag_press[K_PEAKS];

static void acc_reset_all() {
  for (int ph = 0; ph < 7; ph++) {
    acc_rms[ph] = Acc{};
    acc_flat[ph] = Acc{};
    acc_cent[ph] = Acc{};
    acc_roll[ph] = Acc{};
    for (int b = 0; b < B_BANDS; b++) acc_band[ph][b] = Acc{};
  }
  for (int i = 0; i < K_PEAKS; i++) {
    acc_p_hz_press[i] = Acc{};
    acc_p_mag_press[i] = Acc{};
  }
}

// =====================================================
// Helpers
// =====================================================
static uint32_t schedule_total_ms() {
  return PH0_SILENCE_MS + P1_PRESS_MS + P2_GAP_MS + P3_PRESS_MS + P4_GAP_MS + P5_PRESS_MS + P6_TAIL_MS;
}

static uint8_t phase_for_time(uint32_t t_ms) {
  uint32_t t = (t_ms > resp_ms_est) ? (t_ms - resp_ms_est) : 0;
  uint32_t b = PH0_SILENCE_MS;
  if (t < b) return 0;
  b += P1_PRESS_MS; if (t < b) return 1;
  b += P2_GAP_MS;   if (t < b) return 2;
  b += P3_PRESS_MS; if (t < b) return 3;
  b += P4_GAP_MS;   if (t < b) return 4;
  b += P5_PRESS_MS; if (t < b) return 5;
  return 6;
}

static void i2s_setup() {
  i2s_config_t cfg = {};
  cfg.mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_RX);
  cfg.sample_rate = FS_HZ;
  cfg.bits_per_sample = I2S_BITS_PER_SAMPLE_32BIT;
  cfg.channel_format = I2S_CHANNEL_FMT_ONLY_LEFT;
  cfg.communication_format = I2S_COMM_FORMAT_STAND_I2S;
  cfg.intr_alloc_flags = ESP_INTR_FLAG_LEVEL1;
  cfg.dma_buf_count = 8;
  cfg.dma_buf_len = 256;
  cfg.use_apll = false;
  cfg.tx_desc_auto_clear = false;
  cfg.fixed_mclk = 0;

  i2s_pin_config_t pins = {};
  pins.bck_io_num = I2S_BCLK;
  pins.ws_io_num  = I2S_LRCLK;
  pins.data_out_num = -1;
  pins.data_in_num  = I2S_DIN;

  i2s_driver_install(I2S_PORT, &cfg, 0, nullptr);
  i2s_set_pin(I2S_PORT, &pins);
  i2s_zero_dma_buffer(I2S_PORT);
}

static void init_window() {
  for (int i = 0; i < N_FFT; i++) {
    window_f[i] = 0.5f - 0.5f * cosf(2.0f * M_PI * i / (N_FFT - 1));
  }
}

static bool read_audio_samples(int16_t* out, int n) {
  static int32_t buf32[512];
  int need = n;
  int idx = 0;

  while (need > 0) {
    int chunk = (need > 512) ? 512 : need;
    size_t bytes_read = 0;
    esp_err_t ok = i2s_read(I2S_PORT, (void*)buf32, chunk * sizeof(int32_t), &bytes_read, portMAX_DELAY);
    if (ok != ESP_OK || bytes_read == 0) return false;
    int got = bytes_read / sizeof(int32_t);
    uint32_t clip_chunk = 0;
    for (int i = 0; i < got; i++) {
      int32_t v = buf32[i] >> g_shift_right;
      g_total_samples++;
      if (v > 32767) { v = 32767; g_clip_samples++; clip_chunk++; }
      if (v < -32768) { v = -32768; g_clip_samples++; clip_chunk++; }
      out[idx++] = (int16_t)v;
    }
    // simple "auto-gain": if we clipped in this chunk, shift more (quieter) next reads
    if (clip_chunk > 0 && g_shift_right < 15) g_shift_right++;
    need -= got;
  }
  return true;
}

static void compute_fft_mag(const int16_t* x) {
  for (int i = 0; i < N_FFT; i++) {
    float v = (float)x[i] * window_f[i];
    fft_in[2*i + 0] = v;
    fft_in[2*i + 1] = 0.0f;
  }
  dsps_fft2r_fc32(fft_in, N_FFT);
  dsps_bit_rev_fc32(fft_in, N_FFT);
  dsps_cplx2reC_fc32(fft_in, N_FFT);

  mag[0] = fabsf(fft_in[0]);
  for (int k = 1; k < N_FFT/2; k++) {
    float re = fft_in[2*k + 0];
    float im = fft_in[2*k + 1];
    mag[k] = sqrtf(re*re + im*im);
  }
  mag[N_FFT/2] = fabsf(fft_in[1]);
}

static float hz_for_bin(int k) {
  return (float)k * (float)FS_HZ / (float)N_FFT;
}

static void top_k_peaks(float fmin, float fmax, float* out_hz, float* out_mag) {
  for (int i = 0; i < K_PEAKS; i++) { out_hz[i] = 0.0f; out_mag[i] = 0.0f; }

  // Ignore sub-80Hz rumble for peak picking only (does NOT change band energies etc.)
  const float PEAK_FLOOR_HZ = 120.0f;
  float fmin_pk = (fmin < PEAK_FLOOR_HZ) ? PEAK_FLOOR_HZ : fmin;

  int kmin = (int)ceilf(fmin_pk * N_FFT / FS_HZ);
  int kmax = (int)floorf(fmax * N_FFT / FS_HZ);
  if (kmin < 1) kmin = 1;
  if (kmax > N_FFT/2 - 2) kmax = N_FFT/2 - 2;

  for (int k = kmin; k <= kmax; k++) {
    float m0 = mag[k];
    if (m0 <= mag[k-1] || m0 <= mag[k+1]) continue;

    int pos = -1;
    for (int i = 0; i < K_PEAKS; i++) {
      if (m0 > out_mag[i]) { pos = i; break; }
    }
    if (pos >= 0) {
      for (int j = K_PEAKS - 1; j > pos; j--) {
        out_mag[j] = out_mag[j-1];
        out_hz[j]  = out_hz[j-1];
      }
      out_mag[pos] = m0;
      out_hz[pos]  = hz_for_bin(k);
    }
  }
}

static float spectral_flatness(int kmin, int kmax) {
  const float eps = 1e-12f;
  double sum_log = 0.0;
  double sum_lin = 0.0;
  int n = 0;
  for (int k = kmin; k <= kmax; k++) {
    float v = mag[k] + eps;
    sum_log += log((double)v);
    sum_lin += (double)v;
    n++;
  }
  if (n <= 0) return 0.0f;
  double geo = exp(sum_log / (double)n);
  double ari = sum_lin / (double)n;
  return (ari > 0.0) ? (float)(geo / ari) : 0.0f;
}

static void compute_features(const int16_t* x, CalRowBin &row) {
  int32_t vmin =  32767;
  int32_t vmax = -32768;

  double mean = 0.0;
  for (int i = 0; i < N_FFT; i++) mean += x[i];
  mean /= (double)N_FFT;

  double sumsq = 0.0;
  double peak = 0.0;
  int zc = 0;
  int last_sign = (x[0] >= mean) ? 1 : -1;

  for (int i = 0; i < N_FFT; i++) {
    int32_t v = x[i];
    if (v < vmin) vmin = v;
    if (v > vmax) vmax = v;

    double ac = (double)v - mean;
    sumsq += ac * ac;
    double aabs = fabs(ac);
    if (aabs > peak) peak = aabs;

    int s = (v >= mean) ? 1 : -1;
    if (s != last_sign) zc++;
    last_sign = s;
  }

  row.rms_ac  = (float)sqrt(sumsq / (double)N_FFT);
  row.ac_peak = (float)peak;
  row.zc      = (float)zc;
  row.vmin    = (float)vmin;
  row.vmax    = (float)vmax;

  compute_fft_mag(x);

  int kmin = (int)ceilf(F_MIN * N_FFT / FS_HZ);
  int kmax = (int)floorf(F_MAX * N_FFT / FS_HZ);
  if (kmin < 1) kmin = 1;
  if (kmax > N_FFT/2) kmax = N_FFT/2;

  double spec_total = 0.0;
  double sum_fm = 0.0;
  double sum_m = 0.0;

  for (int k = kmin; k <= kmax; k++) {
    double m = (double)mag[k];
    double f = (double)hz_for_bin(k);
    spec_total += m * m;
    sum_fm += f * m;
    sum_m  += m;
  }
  // Prevent CSV/model overflow: store log10(power_norm + 1)
  // power_norm keeps scale stable across N_FFT changes.
  double power_norm = spec_total / (double)N_FFT;
  double spec_log = log10(power_norm + 1.0);
  if (!isfinite(spec_log) || spec_log < 0.0) spec_log = 0.0;
  row.spec_total = (float)spec_log;

  float centroid = (sum_m > 0.0) ? (float)(sum_fm / sum_m) : 0.0f;
  row.centroid_hz = centroid;

  double sum_var = 0.0;
  for (int k = kmin; k <= kmax; k++) {
    double m = (double)mag[k];
    double f = (double)hz_for_bin(k);
    double d = f - (double)centroid;
    sum_var += d * d * m;
  }
  row.bandwidth_hz = (sum_m > 0.0) ? (float)sqrt(sum_var / sum_m) : 0.0f;

  double target = 0.85 * sum_m;
  double acc = 0.0;
  float roll = (float)hz_for_bin(kmax);
  for (int k = kmin; k <= kmax; k++) {
    acc += (double)mag[k];
    if (acc >= target) { roll = hz_for_bin(k); break; }
  }
  row.rolloff85_hz = roll;

  row.flatness = spectral_flatness(kmin, kmax);

  const float edges[B_BANDS+1] = {20, 80, 200, 500, 1280, 2500, 4000};
  for (int b = 0; b < B_BANDS; b++) row.band[b] = 0.0f;

  for (int b = 0; b < B_BANDS; b++) {
    int ka = (int)ceilf(edges[b]   * N_FFT / FS_HZ);
    int kb = (int)floorf(edges[b+1]* N_FFT / FS_HZ);
    if (ka < 1) ka = 1;
    if (kb > N_FFT/2) kb = N_FFT/2;
    double s = 0.0;
    for (int k = ka; k <= kb; k++) s += (double)mag[k];
    row.band[b] = (float)s;
  }

  top_k_peaks(F_MIN, F_MAX, row.p_hz, row.p_mag);
}

// =====================================================
// Serial helpers + command parsing
// =====================================================
static String readLineBlocking() {
  String s;
  while (true) {
    while (Serial.available() > 0) {
      char c = (char)Serial.read();
      if (c == '\r') continue;
      if (c == '\n') return s;
      s += c;
    }
    delay(5);
  }
}

static void coach_line(const char* msg) { Serial.println(msg); }

static int key_idx_from_label(const String& label) {
  for (int i = 0; i < NUM_KEYS; i++) {
    if (label.equalsIgnoreCase(KEY_LABELS[i])) return i;
  }
  return -1;
}

static void print_help() {
  Serial.println("Commands:");
  Serial.println("  <ENTER>            start recording current key");
  Serial.println("  start <idx>        jump to key index (0..84)");
  Serial.println("  start <label>      jump to key label (e.g. start G#1)");
  Serial.println("  redo               redo last recorded key (overwrite in /models.json)");
  Serial.println("  redo prev          redo key before last recorded");
  Serial.println("  noise              record 10s silence -> /noise.json");
  Serial.println("  noise show         show if /noise.json exists");
  Serial.println("  help               show this help");
}

// =====================================================
// CSV dump helpers
// =====================================================
static void dump_header_csv() {
  Serial.print("type,t_ms,key_idx,key,cap_phase,");
  Serial.print("rms_ac,ac_peak,zc,min,max,");
  Serial.print("spec_total_log10,centroid_hz,bandwidth_hz,rolloff85_hz,flatness,");
  Serial.print("band1_20_80,band2_80_200,band3_200_500,band4_500_1280,band5_1280_2500,band6_2500_4000,");
  for (int i = 1; i <= K_PEAKS; i++) {
    Serial.print("p"); Serial.print(i); Serial.print("_hz,");
    Serial.print("p"); Serial.print(i); Serial.print("_mag");
    if (i != K_PEAKS) Serial.print(",");
  }
  Serial.println();
}

static void dump_row_csv(const CalRowBin &r) {
  Serial.print("CAL_WIN,");
  Serial.print(r.t_ms); Serial.print(",");
  Serial.print((int)r.key_idx); Serial.print(",");
  Serial.print(KEY_LABELS[r.key_idx]); Serial.print(",");
  Serial.print((int)r.cap_phase); Serial.print(",");

  Serial.print(r.rms_ac, 3); Serial.print(",");
  Serial.print(r.ac_peak, 3); Serial.print(",");
  Serial.print(r.zc, 1); Serial.print(",");
  Serial.print(r.vmin, 1); Serial.print(",");
  Serial.print(r.vmax, 1); Serial.print(",");

  Serial.print(r.spec_total, 3); Serial.print(",");
  Serial.print(r.centroid_hz, 2); Serial.print(",");
  Serial.print(r.bandwidth_hz, 2); Serial.print(",");
  Serial.print(r.rolloff85_hz, 2); Serial.print(",");
  Serial.print(r.flatness, 6); Serial.print(",");

  for (int b = 0; b < B_BANDS; b++) {
    Serial.print(r.band[b], 3);
    Serial.print(",");
  }

  for (int i = 0; i < K_PEAKS; i++) {
    Serial.print(r.p_hz[i], 2); Serial.print(",");
    Serial.print(r.p_mag[i], 3);
    if (i != K_PEAKS-1) Serial.print(",");
  }
  Serial.println();
}

static void dump_capture_csv(const String& filename) {
  File f = SPIFFS.open(filename, "r");
  if (!f) {
    Serial.println("ERR: cannot open capture file for dump");
    return;
  }
  dump_header_csv();
  CalRowBin r;
  while (f.read((uint8_t*)&r, sizeof(CalRowBin)) == sizeof(CalRowBin)) {
    dump_row_csv(r);
  }
  f.close();
}

// =====================================================
// SPIFFS capture file
// =====================================================
static void begin_capture_file(int key_idx) {
  (void)key_idx;
  curFilename = "/cal_last.bin";
  if (SPIFFS.exists(curFilename)) SPIFFS.remove(curFilename);
  curFile = SPIFFS.open(curFilename, "w");
  if (!curFile) Serial.println("ERR: SPIFFS open failed");
}

static void end_capture_file() { if (curFile) curFile.close(); }
static void write_row_bin(const CalRowBin& r) {
  if (!curFile) return;
  curFile.write((const uint8_t*)&r, sizeof(CalRowBin));
}

// =====================================================
// /models.json (one big json) helpers
// =====================================================
static const char* MODELS_PATH = "/models.json";

static bool load_models_doc(DynamicJsonDocument &doc) {
  doc.clear();
  if (!SPIFFS.exists(MODELS_PATH)) {
    JsonArray arr = doc.to<JsonArray>();
    for (int i = 0; i < NUM_KEYS; i++) arr.add(nullptr);
    return true;
  }
  File f = SPIFFS.open(MODELS_PATH, "r");
  if (!f) return false;
  DeserializationError err = deserializeJson(doc, f);
  f.close();
  if (err) return false;
  if (!doc.is<JsonArray>()) return false;
  JsonArray arr = doc.as<JsonArray>();
  while ((int)arr.size() < NUM_KEYS) arr.add(nullptr);
  return true;
}

static bool save_models_doc(DynamicJsonDocument &doc) {
  File f = SPIFFS.open(MODELS_PATH, "w");
  if (!f) return false;
  serializeJson(doc, f);
  f.close();
  return true;
}

static void write_model_for_key(int key_idx, uint32_t windows) {
  // capacity: tune if you add more fields
  DynamicJsonDocument doc(160 * 1024);

  if (!load_models_doc(doc)) {
    doc.clear();
    JsonArray arr = doc.to<JsonArray>();
    for (int i = 0; i < NUM_KEYS; i++) arr.add(nullptr);
  }

  JsonArray root = doc.as<JsonArray>();
  while (root.size() < NUM_KEYS) root.add(nullptr);

  JsonObject m = root[key_idx].to<JsonObject>();
  m.clear();

  m["key_idx"] = key_idx;
  m["key"] = KEY_LABELS[key_idx];
  m["fs"] = FS_HZ;
  m["n_fft"] = N_FFT;
  m["hop"] = HOP;
  m["windows"] = windows;
  m["resp_ms_est"] = resp_ms_est;

  // Per-phase summary (0..6)
  JsonArray phases = m["phase_stats"].to<JsonArray>();
  for (int ph = 0; ph < 7; ph++) {
    JsonObject p = phases.add<JsonObject>();
    p["cap_phase"] = ph;

    p["rms_mean"]  = acc_rms[ph].mean();
    p["rms_std"]   = acc_rms[ph].std();
    p["flat_mean"] = acc_flat[ph].mean();
    p["flat_std"]  = acc_flat[ph].std();
    p["cent_mean"] = acc_cent[ph].mean();
    p["cent_std"]  = acc_cent[ph].std();
    p["roll_mean"] = acc_roll[ph].mean();
    p["roll_std"]  = acc_roll[ph].std();

    JsonArray bands_mean = p["bands_mean"].to<JsonArray>();
    JsonArray bands_std  = p["bands_std"].to<JsonArray>();
    for (int b = 0; b < B_BANDS; b++) {
      bands_mean.add(acc_band[ph][b].mean());
      bands_std.add(acc_band[ph][b].std());
    }
  }

  // Press-phase peak averages (phases 1/3/5)
  JsonArray press_peaks = m["press_peaks_mean"].to<JsonArray>();
  for (int i = 0; i < K_PEAKS; i++) {
    JsonObject pk = press_peaks.add<JsonObject>();
    pk["idx"] = i;
    pk["hz_mean"]  = acc_p_hz_press[i].mean();
    pk["hz_std"]   = acc_p_hz_press[i].std();
    pk["mag_mean"] = acc_p_mag_press[i].mean();
    pk["mag_std"]  = acc_p_mag_press[i].std();
  }

  if (!save_models_doc(doc)) Serial.println("ERR: save models failed");
}

// =====================================================
// /noise.json helpers (Option B)
// =====================================================
static bool save_noise_json(const Acc &rms, const Acc &flat, const Acc &cent, const Acc &roll, const Acc band[B_BANDS], uint32_t windows) {
  DynamicJsonDocument doc(24 * 1024);
  JsonObject o = doc.to<JsonObject>();
  o["type"] = "silence_profile";
  o["fs"] = FS_HZ;
  o["n_fft"] = N_FFT;
  o["hop"] = HOP;
  o["ms"] = NOISE_CAPTURE_MS;
  o["windows"] = windows;

  o["rms_mean"] = rms.mean();
  o["rms_std"]  = rms.std();
  o["flat_mean"] = flat.mean();
  o["flat_std"]  = flat.std();
  o["cent_mean"] = cent.mean();
  o["cent_std"]  = cent.std();
  o["roll_mean"] = roll.mean();
  o["roll_std"]  = roll.std();

  JsonArray bmean = o["bands_mean"].to<JsonArray>();
  JsonArray bstd  = o["bands_std"].to<JsonArray>();
  for (int b = 0; b < B_BANDS; b++) {
    bmean.add(band[b].mean());
    bstd.add(band[b].std());
  }

  File f = SPIFFS.open(NOISE_PATH, "w");
  if (!f) return false;
  serializeJson(doc, f);
  f.close();
  return true;
}

static void run_noise_capture_10s() {
  coach_line("NOISE_BEGIN {\"type\":\"NOISE_BEGIN\",\"ms\":10000}");
  coach_line("INSTRUCTIONS:");
  coach_line("  - Stay silent for 10 seconds.");
  coach_line("  - Do NOT touch the piano.");
  coach_line("");

  // local accumulators (single “silence phase”)
  Acc nrms, nflat, ncent, nroll;
  Acc nband[B_BANDS];
  for (int b = 0; b < B_BANDS; b++) nband[b] = Acc{};

  // Prime ring buffer
  g_shift_right = 12;
  g_clip_samples = 0;
  g_total_samples = 0;
  ring_fill = 0;
  while (ring_fill < N_FFT) {
    int need = min(HOP, N_FFT - ring_fill);
    if (!read_audio_samples(&ring[ring_fill], need)) {
      Serial.println("ERR: audio read failed during NOISE prime");
      return;
    }
    ring_fill += need;
  }

  uint32_t start_ms = millis();
  uint32_t windows = 0;

  while (true) {
    uint32_t t_ms = millis() - start_ms;
    if (t_ms >= NOISE_CAPTURE_MS) break;

    memmove(ring, ring + HOP, (N_FFT - HOP) * sizeof(int16_t));
    if (!read_audio_samples(ring + (N_FFT - HOP), HOP)) {
      Serial.println("ERR: audio read failed during NOISE");
      break;
    }

    CalRowBin r = {};
    compute_features(ring, r);

    nrms.push(r.rms_ac);
    nflat.push(r.flatness);
    ncent.push(r.centroid_hz);
    nroll.push(r.rolloff85_hz);
    for (int b = 0; b < B_BANDS; b++) nband[b].push(r.band[b]);

    windows++;
    delay(0);
  }

  bool ok = save_noise_json(nrms, nflat, ncent, nroll, nband, windows);
  Serial.print("NOISE_END {\"type\":\"NOISE_END\",\"windows\":");
  Serial.print(windows);
  Serial.print(",\"saved\":");
  Serial.print(ok ? "true" : "false");
  Serial.print(",\"path\":\"");
  Serial.print(NOISE_PATH);
  Serial.println("\"}");
}

// =====================================================
// Capture routine
// =====================================================
static void run_one_key_capture(int key_idx) {
  for (int i = 0; i < 3; i++) { onset_found[i] = false; t_onset_press[i] = 0; }
  resp_ms_est = RESP_MS_INIT;
  resp_locked = false;
  baseline_reset();
  acc_reset_all();
  g_shift_right = 12;
  g_clip_samples = 0;
  g_total_samples = 0;

  t_cmd_press[0] = PH0_SILENCE_MS;
  t_cmd_press[1] = PH0_SILENCE_MS + P1_PRESS_MS + P2_GAP_MS;
  t_cmd_press[2] = PH0_SILENCE_MS + P1_PRESS_MS + P2_GAP_MS + P3_PRESS_MS + P4_GAP_MS;

  const uint32_t total_ms = schedule_total_ms();

  Serial.print("CAP_BEGIN {\"type\":\"CAP_BEGIN\",\"key_idx\":");
  Serial.print(key_idx);
  Serial.print(",\"key\":\"");
  Serial.print(KEY_LABELS[key_idx]);
  Serial.print("\",\"fs\":");
  Serial.print(FS_HZ);
  Serial.print(",\"n\":");
  Serial.print(N_FFT);
  Serial.print(",\"hop\":");
  Serial.print(HOP);
  Serial.print(",\"ms\":");
  Serial.print(total_ms);
  Serial.print(",\"peaks\":");
  Serial.print(K_PEAKS);
  Serial.print(",\"fmin\":");
  Serial.print(F_MIN, 1);
  Serial.print(",\"fmax\":");
  Serial.print(F_MAX, 1);
  Serial.print(",\"resp_ms_init\":");
  Serial.print(RESP_MS_INIT);
  Serial.print(",\"settle_ms\":");
  Serial.print(SETTLE_MS);
  Serial.print(",\"shift_right_init\":");
  Serial.print(g_shift_right);
  Serial.print(",\"spec_total\":\"log10(power/N+1)\"");
  Serial.println("}");

  coach_line("INSTRUCTIONS:");
  coach_line("  - Keep room silent for 1s.");
  coach_line("  - Then PRESS+HOLD when told (3 times), with releases in between.");
  coach_line("  - After final press, let it ring (tail).");
  coach_line("  - Do NOT touch other keys.");
  coach_line("");

  begin_capture_file(key_idx);
  if (!curFile) return;

  ring_fill = 0;
  while (ring_fill < N_FFT) {
    int need = min(HOP, N_FFT - ring_fill);
    if (!read_audio_samples(&ring[ring_fill], need)) return;
    ring_fill += need;
  }

  uint32_t capture_start_ms = millis();
  uint32_t rows = 0;

  bool prompt_done[7] = {false,false,false,false,false,false,false};

  while (true) {
    uint32_t t_ms = millis() - capture_start_ms;
    if (t_ms >= total_ms) break;

    if (!prompt_done[0] && t_ms >= 0) {
      coach_line("SILENCE (1s)...");
      prompt_done[0] = true;
    }
    if (!prompt_done[1] && t_ms >= PH0_SILENCE_MS) {
      coach_line("PRESS + HOLD (2s)...");
      prompt_done[1] = true;
    }
    if (!prompt_done[2] && t_ms >= PH0_SILENCE_MS + P1_PRESS_MS) {
      coach_line("RELEASE (1s)...");
      prompt_done[2] = true;
    }
    if (!prompt_done[3] && t_ms >= PH0_SILENCE_MS + P1_PRESS_MS + P2_GAP_MS) {
      coach_line("PRESS + HOLD (2s)...");
      prompt_done[3] = true;
    }
    if (!prompt_done[4] && t_ms >= PH0_SILENCE_MS + P1_PRESS_MS + P2_GAP_MS + P3_PRESS_MS) {
      coach_line("RELEASE (1s)...");
      prompt_done[4] = true;
    }
    if (!prompt_done[5] && t_ms >= PH0_SILENCE_MS + P1_PRESS_MS + P2_GAP_MS + P3_PRESS_MS + P4_GAP_MS) {
      coach_line("PRESS + HOLD (2s)...");
      prompt_done[5] = true;
    }
    // user-request: remove ONLY this line, so no tail prompt

    memmove(ring, ring + HOP, (N_FFT - HOP) * sizeof(int16_t));
    if (!read_audio_samples(ring + (N_FFT - HOP), HOP)) break;

    CalRowBin r = {};
    r.t_ms = t_ms;
    r.key_idx = (uint8_t)key_idx;
    r.cap_phase = phase_for_time(t_ms);

    compute_features(ring, r);
    write_row_bin(r);
    rows++;

    // baseline (phase 0)
    if (r.cap_phase == 0) baseline_push(r.rms_ac);

    // accumulators for model json
    int ph = r.cap_phase;
    acc_rms[ph].push(r.rms_ac);
    acc_flat[ph].push(r.flatness);
    acc_cent[ph].push(r.centroid_hz);
    acc_roll[ph].push(r.rolloff85_hz);
    for (int b = 0; b < B_BANDS; b++) acc_band[ph][b].push(r.band[b]);

    if (ph == 1 || ph == 3 || ph == 5) {
      for (int i = 0; i < K_PEAKS; i++) {
        acc_p_hz_press[i].push(r.p_hz[i]);
        acc_p_mag_press[i].push(r.p_mag[i]);
      }
    }

    // onset detection
    if (baseline_n > 20) {
      float thr = (float)baseline_mean + ONSET_K_STD * baseline_std();
      for (int i = 0; i < 3; i++) {
        if (onset_found[i]) continue;
        if (t_ms < t_cmd_press[i]) continue;
        if (r.rms_ac > thr) {
          onset_found[i] = true;
          t_onset_press[i] = t_ms;
          uint32_t resp_i = (t_onset_press[i] > t_cmd_press[i]) ? (t_onset_press[i] - t_cmd_press[i]) : 0;
          if (!resp_locked) { resp_ms_est = resp_i; resp_locked = true; }
        }
      }
    }

    delay(0);
  }

  end_capture_file();

  // median resp estimate
  uint32_t resp_list[3];
  int nresp = 0;
  for (int i = 0; i < 3; i++) {
    if (onset_found[i]) resp_list[nresp++] = (t_onset_press[i] > t_cmd_press[i]) ? (t_onset_press[i] - t_cmd_press[i]) : 0;
  }
  if (nresp > 0) {
    for (int i = 0; i < nresp; i++) {
      for (int j = i+1; j < nresp; j++) {
        if (resp_list[j] < resp_list[i]) { uint32_t tmp = resp_list[i]; resp_list[i] = resp_list[j]; resp_list[j] = tmp; }
      }
    }
    resp_ms_est = resp_list[nresp/2];
  }


  // Dump CSV
  Serial.println("CSV_BEGIN");
  dump_capture_csv(curFilename);
  Serial.println("CSV_END");

  Serial.print("CAP_END {\"type\":\"CAP_END\",\"key_idx\":");
  Serial.print(key_idx);
  Serial.print(",\"key\":\"");
  Serial.print(KEY_LABELS[key_idx]);
  Serial.print("\",\"windows\":");
  Serial.print((uint32_t)rows);
  Serial.print(",\"resp_ms_est\":");
  Serial.print(resp_ms_est);
  Serial.print(",\"baseline_rms_mean\":");
  Serial.print((float)baseline_mean, 3);
  Serial.print(",\"baseline_rms_std\":");
  Serial.print(baseline_std(), 3);
  Serial.print(",\"shift_right_final\":");
  Serial.print(g_shift_right);
  Serial.print(",\"clip_pct\":");
  float clip_pct = (g_total_samples ? (100.0f * (float)g_clip_samples / (float)g_total_samples) : 0.0f);
  Serial.print(clip_pct, 3);
  Serial.println("}");

  // Save model (overwrite this key)
  write_model_for_key(key_idx, rows);

  // delete temporary capture
  SPIFFS.remove(curFilename);

  Serial.println("DONE.");
}

// =====================================================
// Setup / Loop + commands
// =====================================================
static int cur_key_idx = 0;
static int last_recorded_idx = -1;

static void print_next_prompt() {
  Serial.println();
  Serial.print("NEXT: key_idx=");
  Serial.print(cur_key_idx);
  Serial.print(" label=");
  Serial.print(KEY_LABELS[cur_key_idx]);
  Serial.println(" — press ENTER to record (or type help)");
}

static void handle_command_line(const String& line) {
  String s = line;
  s.trim();
  if (s.length() == 0) return;

  if (s.equalsIgnoreCase("help")) {
    print_help();
    return;
  }

  if (s.equalsIgnoreCase("noise")) {
    run_noise_capture_10s();
    return;
  }

  if (s.equalsIgnoreCase("noise show")) {
    if (!SPIFFS.exists(NOISE_PATH)) {
      Serial.println("NOISE: /noise.json does not exist yet (run: noise)");
    } else {
      File f = SPIFFS.open(NOISE_PATH, "r");
      size_t sz = f ? f.size() : 0;
      if (f) f.close();
      Serial.print("NOISE: exists at "); Serial.print(NOISE_PATH);
      Serial.print(" size="); Serial.println((uint32_t)sz);
    }
    return;
  }

  if (s.equalsIgnoreCase("redo")) {
    if (last_recorded_idx < 0) {
      Serial.println("ERR: nothing to redo yet");
      return;
    }
    cur_key_idx = last_recorded_idx;
    Serial.print("OK: redo key_idx="); Serial.print(cur_key_idx);
    Serial.print(" label="); Serial.println(KEY_LABELS[cur_key_idx]);
    return;
  }

  if (s.equalsIgnoreCase("redo prev")) {
    if (last_recorded_idx < 1) {
      Serial.println("ERR: no previous key to redo");
      return;
    }
    cur_key_idx = last_recorded_idx - 1;
    Serial.print("OK: redo prev key_idx="); Serial.print(cur_key_idx);
    Serial.print(" label="); Serial.println(KEY_LABELS[cur_key_idx]);
    return;
  }

  if (s.startsWith("start ")) {
    String arg = s.substring(6);
    arg.trim();

    bool is_num = arg.length() > 0;
    for (int i = 0; i < (int)arg.length(); i++) {
      char c = arg[i];
      if (!(c >= '0' && c <= '9')) { is_num = false; break; }
    }

    int idx = -1;
    if (is_num) idx = arg.toInt();
    else idx = key_idx_from_label(arg);

    if (idx < 0 || idx >= NUM_KEYS) {
      Serial.println("ERR: start expects idx 0..84 or a valid label (e.g. G#1)");
      return;
    }

    cur_key_idx = idx;
    Serial.print("OK: start at key_idx="); Serial.print(cur_key_idx);
    Serial.print(" label="); Serial.println(KEY_LABELS[cur_key_idx]);
    return;
  }

  Serial.println("ERR: unknown command (type help)");
}

void setup() {
  Serial.begin(921600);
  delay(200);

  if (!SPIFFS.begin(true)) {
    Serial.println("ERR: SPIFFS mount failed");
  }

  dsps_fft2r_init_fc32(NULL, N_FFT);
  init_window();
  i2s_setup();

  Serial.println();
  Serial.println("Piano calibration (48kHz) - ENTER starts capture.");
  Serial.print("BUILD_TAG="); Serial.println(BUILD_TAG);
  Serial.println();
  Serial.print("FS="); Serial.print(FS_HZ);
  Serial.print(" N="); Serial.print(N_FFT);
  Serial.print(" HOP="); Serial.print(HOP);
  Serial.print(" rows/sec≈"); Serial.println((float)FS_HZ / (float)HOP, 2);
  Serial.print("Models JSON: "); Serial.println(MODELS_PATH);
  Serial.print("Noise JSON:  "); Serial.println(NOISE_PATH);
  Serial.println();
  print_help();
  print_next_prompt();
}

void loop() {
  if (cur_key_idx >= NUM_KEYS) {
    Serial.println("All keys done.");
    while (true) delay(1000);
  }

  String line = readLineBlocking();

  if (line.length() == 0) {
    run_one_key_capture(cur_key_idx);
    last_recorded_idx = cur_key_idx;

    cur_key_idx++;
    if (cur_key_idx >= NUM_KEYS) {
      Serial.println("All keys done.");
      while (true) delay(1000);
    }
    print_next_prompt();
  } else {
    handle_command_line(line);
    print_next_prompt();
  }
}
