#include <Arduino.h>
#include <SPIFFS.h>
#include <ArduinoJson.h>
#include "driver/i2s.h"
#include <math.h>
#include <vector>

#include <ArduinoFFT.h>

// ================== I2S MIC CONFIG (INMP441) ==================
static const i2s_port_t I2S_PORT = I2S_NUM_0;

// Change to your wiring if needed
static const int I2S_BCLK  = 26;
static const int I2S_LRCLK = 25;
static const int I2S_DIN   = 33;

// INMP441: 24-bit packed into 32-bit
static const int SHIFT_RIGHT = 8;

// ================== DSP CONFIG ==================
static const int FS_HZ = 22050;
static const int N_FFT = 1024;            // ArduinoFFT load; 1024 is stable on ESP32
static const int HOP   = N_FFT / 2;

// Exactly like Python defaults
static const float GATE_SIGMA = 4.0f;     // noise gate sigma
static const float STABLE_SEC = 0.50f;    // must be stable before printing

// Band edges exactly like Python export
static const float BAND_EDGES_HZ[7] = {20, 80, 160, 320, 640, 1280, 4000}; // 6 bands

// Matching weights (peak clusters dominate, band shape helps)
static const float W_PEAK = 1.00f;
static const float W_BAND = 0.70f;

// ================== MODEL STRUCT ==================
struct Cluster {
  int center_cents;     // cents-from-1Hz
  int spread_cents;     // cents
  int weight_q15;       // 0..32767
};

struct KeyModel {
  int key_idx;
  Cluster c[3];
  int band_med_q15[6];
};

static std::vector<KeyModel> gKeys;

static float g_noise_rms_med = 0, g_noise_rms_mad = 0;
static float g_noise_tot_med = 0, g_noise_tot_mad = 0;
static float g_noise_flat_med = 0, g_noise_flat_mad = 0;

// ================== BUFFERS ==================
static float  g_x[N_FFT];
static float  g_win[N_FFT];

// ArduinoFFT uses double
static double vReal[N_FFT];
static double vImag[N_FFT];

static float  g_mag[(N_FFT/2) + 1];

static ArduinoFFT<double> FFT(vReal, vImag, N_FFT, FS_HZ);

// ================== HELPERS ==================
static inline float clampf(float x, float lo, float hi) {
  return (x < lo) ? lo : (x > hi) ? hi : x;
}

static inline float hz_to_cents_from_1hz(float f_hz) {
  if (f_hz <= 0.0f) return -1e9f;
  return 1200.0f * log2f(f_hz); // cents relative to 1Hz
}

static String midiToNoteName(int midi) {
  static const char* NAMES[12] = {"C","C#","D","D#","E","F","F#","G","G#","A","A#","B"};
  int note = ((midi % 12) + 12) % 12;
  int octave = (midi / 12) - 1;
  String s = String(NAMES[note]) + String(octave);
  return s;
}

// key_idx 0 = A0 => MIDI 21
static String keyIdxToNoteName(int key_idx) {
  int midi = 21 + key_idx;
  return midiToNoteName(midi);
}

static void i2s_install() {
  i2s_config_t cfg = {};
  cfg.mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_RX);
  cfg.sample_rate = FS_HZ;
  cfg.bits_per_sample = I2S_BITS_PER_SAMPLE_32BIT;
  cfg.channel_format = I2S_CHANNEL_FMT_ONLY_LEFT;
  cfg.communication_format = I2S_COMM_FORMAT_I2S; // deprecated warning OK
  cfg.intr_alloc_flags = ESP_INTR_FLAG_LEVEL1;
  cfg.dma_buf_count = 8;
  cfg.dma_buf_len = 256;
  cfg.use_apll = false;
  cfg.tx_desc_auto_clear = false;
  cfg.fixed_mclk = 0;

  i2s_pin_config_t pin_cfg = {};
  pin_cfg.bck_io_num = I2S_BCLK;
  pin_cfg.ws_io_num = I2S_LRCLK;
  pin_cfg.data_out_num = -1;
  pin_cfg.data_in_num = I2S_DIN;

  ESP_ERROR_CHECK(i2s_driver_install(I2S_PORT, &cfg, 0, nullptr));
  ESP_ERROR_CHECK(i2s_set_pin(I2S_PORT, &pin_cfg));
  ESP_ERROR_CHECK(i2s_zero_dma_buffer(I2S_PORT));
}

static bool loadModel(const char* path) {
  if (!SPIFFS.exists(path)) {
    Serial.printf("ERROR: %s not found on SPIFFS\n", path);
    return false;
  }
  File f = SPIFFS.open(path, "r");
  if (!f) {
    Serial.printf("ERROR: open %s failed\n", path);
    return false;
  }

  JsonDocument doc;
  auto err = deserializeJson(doc, f);
  f.close();
  if (err) {
    Serial.printf("ERROR: JSON parse: %s\n", err.c_str());
    return false;
  }

  // noise (matches Python NoiseModel fields)
  g_noise_rms_med  = (float)(doc["noise"]["rms_ac_med"] | 0.0);
  g_noise_rms_mad  = (float)(doc["noise"]["rms_ac_mad"] | 0.0);
  g_noise_tot_med  = (float)(doc["noise"]["spec_total_med"] | 0.0);
  g_noise_tot_mad  = (float)(doc["noise"]["spec_total_mad"] | 0.0);
  g_noise_flat_med = (float)(doc["noise"]["flatness_med"] | 0.0);
  g_noise_flat_mad = (float)(doc["noise"]["flatness_mad"] | 0.0);

  gKeys.clear();
  for (JsonObject k : doc["keys"].as<JsonArray>()) {
    KeyModel km;
    km.key_idx = (int)(k["key_idx"] | -1);

    JsonArray cl = k["clusters"].as<JsonArray>();
    for (int i = 0; i < 3; i++) {
      km.c[i].center_cents = (int)(cl[i]["center_cents"] | 0);
      km.c[i].spread_cents = (int)(cl[i]["spread_cents"] | 1);
      km.c[i].weight_q15   = (int)(cl[i]["weight_q15"] | 0);
      if (km.c[i].spread_cents < 1) km.c[i].spread_cents = 1;
    }

    JsonArray bm = k["band_med_q15"].as<JsonArray>();
    for (int i = 0; i < 6; i++) km.band_med_q15[i] = (int)(bm[i] | 0);

    if (km.key_idx >= 0) gKeys.push_back(km);
  }

  Serial.printf("Loaded model: %u keys | noise rms_med=%.2f mad=%.2f | tot_med=%.2f mad=%.2f\n",
                (unsigned)gKeys.size(), g_noise_rms_med, g_noise_rms_mad, g_noise_tot_med, g_noise_tot_mad);
  return !gKeys.empty();
}

// ================== FEATURE EXTRACTION (Python-style) ==================
struct Feat {
  float rms_ac;
  float flatness;
  float spec_total;      // sum of band energies (20..4000)
  int   band_q15[6];     // normalized bands -> Q15
  float peak_hz[8];
  float peak_mag[8];
  float peak_cents_1hz[8];
};

static inline int hzToBin(float hz) {
  // map frequency to FFT bin index
  int k = (int)lroundf(hz * (float)N_FFT / (float)FS_HZ);
  if (k < 1) k = 1;
  if (k > (N_FFT/2)) k = (N_FFT/2);
  return k;
}

static void doFFT_mag() {
  for (int i = 0; i < N_FFT; i++) {
    vReal[i] = (double)(g_x[i] * g_win[i]);
    vImag[i] = 0.0;
  }

  FFT.compute(FFT_FORWARD);
  FFT.complexToMagnitude();

  g_mag[0] = 0.0f;
  for (int k = 1; k <= N_FFT/2; k++) g_mag[k] = (float)vReal[k];
}

static void computeRmsAc(Feat &out) {
  float mean = 0.0f;
  for (int i = 0; i < N_FFT; i++) mean += g_x[i];
  mean /= (float)N_FFT;

  double acc = 0.0;
  for (int i = 0; i < N_FFT; i++) {
    float d = g_x[i] - mean;
    acc += (double)d * (double)d;
  }
  out.rms_ac = (float)sqrt(acc / (double)N_FFT);
}

static void computeFlatness_20_4000(Feat &out) {
  // flatness over 20..4000 Hz to align with band region
  const float eps = 1e-12f;
  int k0 = hzToBin(20.0f);
  int k1 = hzToBin(4000.0f);

  float sum = 0.0f;
  float sumLog = 0.0f;
  int n = 0;

  for (int k = k0; k <= k1; k++) {
    float m = g_mag[k] + eps;
    sum += m;
    sumLog += logf(m);
    n++;
  }
  if (n <= 0) { out.flatness = 0.0f; return; }

  float arith = sum / (float)n;
  float geom  = expf(sumLog / (float)n);
  out.flatness = (arith > eps) ? (geom / arith) : 0.0f;
}

static void computeBandsAndTotal(Feat &out) {
  float band[6] = {0,0,0,0,0,0};

  for (int b = 0; b < 6; b++) {
    int k_lo = hzToBin(BAND_EDGES_HZ[b]);
    int k_hi = hzToBin(BAND_EDGES_HZ[b+1]);
    float s = 0.0f;
    for (int k = k_lo; k <= k_hi; k++) s += g_mag[k];
    band[b] = s;
  }

  float tot = 0.0f;
  for (int b = 0; b < 6; b++) tot += band[b];
  out.spec_total = tot;

  float denom = (tot > 1e-9f) ? tot : 1.0f;
  for (int b = 0; b < 6; b++) {
    float norm = band[b] / denom; // same as Python: bands / sum(bands)
    out.band_q15[b] = (int)lroundf(clampf(norm, 0, 1) * 32767.0f);
  }
}

static void computeTopPeaks8(Feat &out) {
  // local maxima in 20..4000 Hz region, take top 8 by magnitude
  struct P { float m; int k; };
  P top[8];
  for (int i = 0; i < 8; i++) top[i] = {0.0f, 0};

  int k0 = hzToBin(20.0f);
  int k1 = hzToBin(4000.0f);

  for (int k = k0+1; k < k1-1; k++) {
    float m0 = g_mag[k-1], m1 = g_mag[k], m2 = g_mag[k+1];
    if (m1 > m0 && m1 > m2) {
      // insert into top[] if large enough
      int pos = -1;
      for (int i = 0; i < 8; i++) {
        if (m1 > top[i].m) { pos = i; break; }
      }
      if (pos >= 0) {
        for (int j = 7; j > pos; j--) top[j] = top[j-1];
        top[pos] = {m1, k};
      }
    }
  }

  for (int i = 0; i < 8; i++) {
    if (top[i].k > 0) {
      float hz = (float)top[i].k * (float)FS_HZ / (float)N_FFT;
      out.peak_hz[i] = hz;
      out.peak_mag[i] = top[i].m;
      out.peak_cents_1hz[i] = hz_to_cents_from_1hz(hz);
    } else {
      out.peak_hz[i] = 0.0f;
      out.peak_mag[i] = 0.0f;
      out.peak_cents_1hz[i] = -1e9f;
    }
  }
}

static void computeFeat(Feat &out) {
  computeRmsAc(out);
  computeFlatness_20_4000(out);
  computeBandsAndTotal(out);
  computeTopPeaks8(out);
}

// ================== GATING (exactly like Python passes_gate) ==================
static inline float safeMad(float mad) { return (mad > 1e-9f) ? mad : 1e-9f; }

static bool passesGate(const Feat &f) {
  float rms_thr = g_noise_rms_med + GATE_SIGMA * safeMad(g_noise_rms_mad);
  float tot_thr = g_noise_tot_med + GATE_SIGMA * safeMad(g_noise_tot_mad);

  if (f.rms_ac <= rms_thr) return false;
  if (f.spec_total <= tot_thr) return false;

  // reject very flat broadband noise
  if (g_noise_flat_mad > 0) {
    float flat_thr = g_noise_flat_med + 6.0f * g_noise_flat_mad;
    if (f.flatness >= flat_thr) return false;
  }
  return true;
}

// ================== MATCHING (cluster-centric, weight-aware) ==================
static float scoreKey(const Feat &f, const KeyModel &km) {
  // Peak score: for each model cluster, find the closest observed peak in cents,
  // penalize by z = |diff|/spread, then weight by cluster weight_q15.
  float wsum = 0.0f;
  float acc  = 0.0f;

  for (int j = 0; j < 3; j++) {
    float w = (float)km.c[j].weight_q15 / 32767.0f;
    if (w <= 0.0f) continue;

    float bestDiff = 1e9f;
    for (int i = 0; i < 8; i++) {
      if (f.peak_mag[i] <= 0.0f) continue;
      float d = fabsf(f.peak_cents_1hz[i] - (float)km.c[j].center_cents);
      if (d < bestDiff) bestDiff = d;
    }

    float spread = (float)km.c[j].spread_cents;
    if (spread < 1.0f) spread = 1.0f;
    float z = bestDiff / spread;

    acc  += w * clampf(z, 0.0f, 50.0f);
    wsum += w;
  }

  float peakScore = (wsum > 1e-6f) ? (acc / wsum) : 50.0f;

  // Band score: L1 distance in Q15 normalized space (same representation as model_compact)
  float bandScore = 0.0f;
  for (int b = 0; b < 6; b++) {
    bandScore += fabsf((float)f.band_q15[b] - (float)km.band_med_q15[b]) / 32767.0f;
  }

  return W_PEAK * peakScore + W_BAND * bandScore;
}

// ================== STREAMING INPUT ==================
static bool fillFrameFromI2S() {
  static int32_t raw[HOP];
  size_t bytesRead = 0;

  esp_err_t e = i2s_read(I2S_PORT, (void*)raw, sizeof(raw), &bytesRead, portMAX_DELAY);
  if (e != ESP_OK || bytesRead != sizeof(raw)) return false;

  memmove(g_x, g_x + HOP, sizeof(float) * (N_FFT - HOP));
  for (int i = 0; i < HOP; i++) {
    int32_t v = raw[i] >> SHIFT_RIGHT;
    g_x[N_FFT - HOP + i] = (float)v;
  }
  return true;
}

// ================== STABILITY (time-based + margin) ==================
static int g_lastPrinted = -1;
static int g_candidate   = -1;
static uint32_t g_sinceMs = 0;

static void resetStability() {
  g_candidate = -1;
  g_sinceMs = millis();
}

static void updateStable(int bestIdx, float bestScore, float secondScore) {
  // require a little margin so it doesn’t flap between close matches
  // (small margin, not a vote system)
  const float MARGIN = 0.35f; // increase to be stricter
  if (!(bestScore + MARGIN < secondScore)) {
    resetStability();
    return;
  }

  uint32_t now = millis();
  if (bestIdx != g_candidate) {
    g_candidate = bestIdx;
    g_sinceMs = now;
    return;
  }

  if ((now - g_sinceMs) >= (uint32_t)(STABLE_SEC * 1000.0f) && bestIdx != g_lastPrinted) {
    g_lastPrinted = bestIdx;
    Serial.printf("NOTE %s\n", keyIdxToNoteName(bestIdx).c_str());
  }
}

// ================== SETUP / LOOP ==================
void setup() {
  Serial.begin(115200);
  delay(200);

  if (!SPIFFS.begin(true)) {
    Serial.println("ERROR: SPIFFS mount failed");
    while (true) delay(1000);
  }

  if (!loadModel("/model_compact.json")) {
    Serial.println("ERROR: Put model_compact.json on SPIFFS as /model_compact.json");
    while (true) delay(1000);
  }

  for (int i = 0; i < N_FFT; i++) {
    g_win[i] = 0.5f * (1.0f - cosf(2.0f * M_PI * (float)i / (float)(N_FFT - 1)));
    g_x[i] = 0.0f;
  }

  i2s_install();
  Serial.println("Listening... prints NOTE names when stable >= 0.5s (key_idx 0 = A0)");
}

void loop() {
  if (!fillFrameFromI2S()) return;

  doFFT_mag();

  Feat f;
  computeFeat(f);

  if (!passesGate(f)) {
    resetStability();
    return;
  }

  // Find best + second best (needed for margin hysteresis)
  float bestScore = 1e9f, secondScore = 1e9f;
  int bestKeyIdx = -1;

  for (const auto &km : gKeys) {
    float sc = scoreKey(f, km);
    if (sc < bestScore) {
      secondScore = bestScore;
      bestScore = sc;
      bestKeyIdx = km.key_idx;
    } else if (sc < secondScore) {
      secondScore = sc;
    }
  }

  if (bestKeyIdx >= 0) {
    updateStable(bestKeyIdx, bestScore, secondScore);
  } else {
    resetStability();
  }
}
