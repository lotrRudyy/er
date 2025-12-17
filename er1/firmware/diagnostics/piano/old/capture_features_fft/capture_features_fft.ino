#include <Arduino.h>
#include <SPIFFS.h>
#include <ArduinoJson.h>

#include <driver/i2s.h>
#include <math.h>
#include <vector>
#include "esp_heap_caps.h"
#include "esp_dsp.h"

// ================== CONFIG ==================
static const i2s_port_t I2S_PORT = I2S_NUM_0;
static const int I2S_BCLK  = 26;
static const int I2S_LRCLK = 25;
static const int I2S_DIN   = 33;
static const int SHIFT_RIGHT = 8;

static const int   FS_HZ = 22050;
static const int   N_FFT = 4096;
static const int   HOP   = 256;

static const float F_MIN = 20.0f;
static const float F_MAX = 4000.0f;

// --- boot noise calibration ---
static const uint32_t NOISE_CAL_MS = 5000;

// --- gating (reject speech/garbage) ---
static const float RMS_SIGMA_GATE   = 3.5f;   // activity gate from room noise
static const float FLAT_SIGMA_GATE  = 6.0f;   // same style as python: flat < med + 6*mad

static const float PEAK_RATIO_MIN   = 10.0f;  // p1_mag / avg_mag in [20..4000]
static const float HARM_CENTS_TOL   = 45.0f;  // harmonic tolerance
static const float HARM_REL_MIN     = 0.10f;  // harmonic peak mag >= 10% of fundamental peak

// --- match acceptance (the “NO KEY” logic) ---
static const float BEST_MAX         = 7.0f;   // lower = stricter (try 6..9)
static const float MARGIN_MIN       = 1.0f;   // higher = stricter (try 0.8..1.5)

// --- timing ---
static const int   ONSET_FRAMES     = 6;      // ~70ms (6 * 11.6ms)
static const uint32_t COOLDOWN_MS   = 150;

// ================== Key naming (optional) ==================
static const char* NOTE_NAMES[12] = {"A","A#","B","C","C#","D","D#","E","F","F#","G","G#"};
static void keyNameFromIndex(int idx, char out[8]) {
  int note = idx % 12;
  int cycles = idx / 12;
  int octave = cycles;
  if (note >= 3) octave += 1;
  snprintf(out, 8, "%s%d", NOTE_NAMES[note], octave);
}

// ================== aligned alloc ==================
static void* alloc16(size_t bytes) {
  return heap_caps_aligned_alloc(16, bytes, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
}
static void fatal(const char* msg) {
  Serial.println(msg);
  while (1) delay(1000);
}

// ================== DSP buffers ==================
static int32_t *ring_buf  = nullptr;
static int32_t *win_i32   = nullptr;
static float   *fft_buf   = nullptr;   // 2*N_FFT complex
static float   *mag_buf   = nullptr;   // (N_FFT/2)+1
static float   *fft_table = nullptr;
static int ring_w = 0;
static int ring_count = 0;

// ================== model structures ==================
struct Cluster {
  int center_cents;    // cents-from-1Hz
  int spread_cents;
  int weight_q15;
};
struct KeyModel {
  int key_idx;
  Cluster c[3];
  int band_med_q15[6];
};
static std::vector<KeyModel> gKeys;

// model noise (for printing)
static float g_model_rms_med=0, g_model_rms_mad=0;
static float g_model_tot_med=0, g_model_tot_mad=0;
static float g_model_flat_med=0, g_model_flat_mad=0;

// runtime noise (room)
static float g_rms_med=0, g_rms_mad=0;
static float g_tot_med=0, g_tot_mad=0;
static float g_flat_med=0, g_flat_mad=0;

// ================== helpers ==================
static inline float log2f_safe(float x) { return logf(x) / logf(2.0f); }
static inline float hz_to_cents_from_1hz(float f_hz) {
  if (f_hz <= 0.0f) return -1e9f;
  return 1200.0f * log2f_safe(f_hz);
}
static inline float safeMad(float mad) { return (mad > 1e-9f) ? mad : 1e-9f; }

static float median(std::vector<float> v) {
  if (v.empty()) return 0.0f;
  size_t n = v.size();
  std::nth_element(v.begin(), v.begin() + n/2, v.end());
  float m = v[n/2];
  if ((n & 1) == 0) {
    std::nth_element(v.begin(), v.begin() + (n/2 - 1), v.end());
    m = 0.5f * (m + v[n/2 - 1]);
  }
  return m;
}
static float robust_mad(std::vector<float> v, float med) {
  if (v.empty()) return 0.0f;
  for (auto &x : v) x = fabsf(x - med);
  size_t n = v.size();
  std::nth_element(v.begin(), v.begin() + n/2, v.end());
  float mad = v[n/2];
  return mad * 1.4826f;
}

static inline int hz_to_bin(float hz) {
  return (int)lroundf(hz * (float)N_FFT / (float)FS_HZ);
}
static inline float bin_to_hz(float k) {
  return k * ((float)FS_HZ / (float)N_FFT);
}

// ================== I2S init ==================
static void i2s_init_inmp441() {
  i2s_config_t cfg = {};
  cfg.mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_RX);
  cfg.sample_rate = FS_HZ;
  cfg.bits_per_sample = I2S_BITS_PER_SAMPLE_32BIT;
  cfg.channel_format = I2S_CHANNEL_FMT_ONLY_LEFT;
  cfg.communication_format = I2S_COMM_FORMAT_I2S;
  cfg.intr_alloc_flags = ESP_INTR_FLAG_LEVEL1;
  cfg.dma_buf_count = 8;
  cfg.dma_buf_len = 256;
  cfg.use_apll = false;
  cfg.tx_desc_auto_clear = false;
  cfg.fixed_mclk = 0;

  i2s_pin_config_t pins = {};
  pins.bck_io_num = I2S_BCLK;
  pins.ws_io_num  = I2S_LRCLK;
  pins.data_out_num = I2S_PIN_NO_CHANGE;
  pins.data_in_num  = I2S_DIN;

  ESP_ERROR_CHECK(i2s_driver_install(I2S_PORT, &cfg, 0, nullptr));
  ESP_ERROR_CHECK(i2s_set_pin(I2S_PORT, &pins));
  ESP_ERROR_CHECK(i2s_zero_dma_buffer(I2S_PORT));
}

// ================== ring buffer ==================
static void ring_push_from_i2s(int needed_samples) {
  static int32_t tmp[256];
  int pushed = 0;
  while (pushed < needed_samples) {
    size_t bytes_read = 0;
    esp_err_t err = i2s_read(I2S_PORT, (void*)tmp, sizeof(tmp), &bytes_read, portMAX_DELAY);
    if (err != ESP_OK || bytes_read == 0) continue;

    int frames = bytes_read / (int)sizeof(int32_t);
    for (int i = 0; i < frames && pushed < needed_samples; i++) {
      int32_t s = tmp[i] >> SHIFT_RIGHT;
      ring_buf[ring_w] = s;
      ring_w = (ring_w + 1) & (N_FFT - 1);
      if (ring_count < N_FFT) ring_count++;
      pushed++;
    }
  }
}
static void ring_copy_window_i32(int32_t *dst) {
  int start = (ring_w - ring_count);
  if (start < 0) start += N_FFT;
  int idx = start;
  for (int i = 0; i < N_FFT; i++) {
    dst[i] = ring_buf[idx];
    idx = (idx + 1) & (N_FFT - 1);
  }
}

// ================== features (logger-matched) ==================
struct TDStats { float mean=0; float rms_ac=0; };

static TDStats compute_td_stats(const int32_t *x) {
  TDStats s;
  double sum = 0.0;
  for (int i = 0; i < N_FFT; i++) sum += (double)x[i];
  s.mean = (float)(sum / (double)N_FFT);

  double acc = 0.0;
  for (int i = 0; i < N_FFT; i++) {
    float a = (float)x[i] - s.mean;
    acc += (double)a * (double)a;
  }
  s.rms_ac = (float)sqrt(acc / (double)N_FFT);
  return s;
}

static void compute_fft_mag(const int32_t *x, float mean) {
  for (int i = 0; i < N_FFT; i++) {
    float w = 0.5f - 0.5f * cosf(2.0f * (float)M_PI * (float)i / (float)(N_FFT - 1));
    fft_buf[2*i + 0] = ((float)x[i] - mean) * w;
    fft_buf[2*i + 1] = 0.0f;
  }

  dsps_fft2r_fc32(fft_buf, N_FFT);
  dsps_bit_rev_fc32(fft_buf, N_FFT);
  dsps_cplx2reC_fc32(fft_buf, N_FFT);

  mag_buf[0] = fabsf(fft_buf[0]);
  for (int k = 1; k < (N_FFT/2); k++) {
    float re = fft_buf[2*k + 0];
    float im = fft_buf[2*k + 1];
    mag_buf[k] = sqrtf(re*re + im*im);
  }
  mag_buf[N_FFT/2] = fabsf(fft_buf[1]);
}

static float refine_peak_bin(int k) {
  if (k <= 1 || k >= (N_FFT/2 - 1)) return (float)k;
  float a = mag_buf[k - 1], b = mag_buf[k], c = mag_buf[k + 1];
  float denom = (a - 2.0f*b + c);
  if (fabsf(denom) < 1e-12f) return (float)k;
  float delta = 0.5f * (a - c) / denom;
  return (float)k + delta;
}

struct Peak { float f_hz=0; float mag=0; float cents1hz=-1e9f; };

static void pick_top_peaks(Peak *out_peaks, int max_peaks) {
  const int bin_min = max(2, hz_to_bin(F_MIN));
  const int bin_max = min((N_FFT/2 - 2), hz_to_bin(F_MAX));

  for (int i = 0; i < max_peaks; i++) out_peaks[i] = {};

  for (int k = bin_min + 1; k <= bin_max - 1; k++) {
    float m0 = mag_buf[k - 1], m1 = mag_buf[k], m2 = mag_buf[k + 1];
    if (!(m1 > m0 && m1 > m2)) continue;

    for (int i = 0; i < max_peaks; i++) {
      if (m1 > out_peaks[i].mag) {
        for (int j = max_peaks - 1; j > i; j--) out_peaks[j] = out_peaks[j - 1];
        out_peaks[i].mag = m1;
        float kref = refine_peak_bin(k);
        float hz = bin_to_hz(kref);
        out_peaks[i].f_hz = hz;
        out_peaks[i].cents1hz = hz_to_cents_from_1hz(hz);
        break;
      }
    }
  }
}

struct SpecFeat { float total=0; float flatness=0; float bands[6]={0}; };

static void compute_spectral_features(SpecFeat &sf) {
  const int kmin = max(1, hz_to_bin(F_MIN));
  const int kmax = min((N_FFT/2), hz_to_bin(F_MAX));
  const float df = (float)FS_HZ / (float)N_FFT;

  double sum = 0.0;
  double log_sum = 0.0;
  double ar_sum  = 0.0;
  int flat_n = 0;
  double b[6] = {0,0,0,0,0,0};

  for (int k = kmin; k <= kmax; k++) {
    float m = mag_buf[k];
    float f = k * df;

    sum += (double)m;

    float mm = (m < 1e-12f) ? 1e-12f : m;
    log_sum += log((double)mm);
    ar_sum  += (double)mm;
    flat_n++;

    if (f < 80) b[0] += m;
    else if (f < 160) b[1] += m;
    else if (f < 320) b[2] += m;
    else if (f < 640) b[3] += m;
    else if (f < 1280) b[4] += m;
    else b[5] += m;
  }

  sf.total = (float)sum;

  if (flat_n > 0) {
    double geo = exp(log_sum / (double)flat_n);
    double ar  = ar_sum / (double)flat_n;
    sf.flatness = (ar > 1e-12) ? (float)(geo / ar) : 0.0f;
  } else sf.flatness = 0.0f;

  for (int i = 0; i < 6; i++) sf.bands[i] = (float)b[i];
  (void)df;
}

// ================== gating helpers ==================
static bool has_harmonic_support(const Peak *peaks, float f0, float p1mag) {
  if (f0 <= 0 || p1mag <= 0) return false;
  float f2 = 2.0f * f0;
  float f3 = 3.0f * f0;

  for (int i = 1; i < 8; i++) {
    if (peaks[i].mag <= 0) continue;
    if (peaks[i].mag < (HARM_REL_MIN * p1mag)) continue;

    float d2 = fabsf(hz_to_cents_from_1hz(peaks[i].f_hz) - hz_to_cents_from_1hz(f2));
    float d3 = fabsf(hz_to_cents_from_1hz(peaks[i].f_hz) - hz_to_cents_from_1hz(f3));
    if (d2 <= HARM_CENTS_TOL || d3 <= HARM_CENTS_TOL) return true;
  }
  return false;
}

// ================== model loading ==================
static bool loadModel(const char* path) {
  File f = SPIFFS.open(path, "r");
  if (!f) return false;

  JsonDocument doc;
  auto err = deserializeJson(doc, f);
  f.close();
  if (err) return false;

  g_model_rms_med  = (float)(doc["noise"]["rms_ac_med"] | 0.0);
  g_model_rms_mad  = (float)(doc["noise"]["rms_ac_mad"] | 0.0);
  g_model_tot_med  = (float)(doc["noise"]["spec_total_med"] | 0.0);
  g_model_tot_mad  = (float)(doc["noise"]["spec_total_mad"] | 0.0);
  g_model_flat_med = (float)(doc["noise"]["flatness_med"] | 0.0);
  g_model_flat_mad = (float)(doc["noise"]["flatness_mad"] | 0.0);

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
  return !gKeys.empty();
}

// ================== room noise calibration ==================
static void calibrateNoiseRoom() {
  Serial.printf("Noise-cal: keep quiet for %lu ms...\n", (unsigned long)NOISE_CAL_MS);

  std::vector<float> vrms, vtot, vflat;
  uint32_t t0 = millis();

  while ((uint32_t)(millis() - t0) < NOISE_CAL_MS) {
    ring_push_from_i2s(HOP);
    ring_copy_window_i32(win_i32);

    TDStats td = compute_td_stats(win_i32);
    compute_fft_mag(win_i32, td.mean);

    SpecFeat sf;
    compute_spectral_features(sf);

    vrms.push_back(td.rms_ac);
    vtot.push_back(sf.total);
    vflat.push_back(sf.flatness);
  }

  g_rms_med  = median(vrms);
  g_tot_med  = median(vtot);
  g_flat_med = median(vflat);

  g_rms_mad  = robust_mad(vrms,  g_rms_med);
  g_tot_mad  = robust_mad(vtot,  g_tot_med);
  g_flat_mad = robust_mad(vflat, g_flat_med);

  Serial.printf("Noise-cal DONE: rms_med=%.2f mad=%.2f | tot_med=%.2f mad=%.2f | flat_med=%.4f mad=%.4f\n",
                g_rms_med, g_rms_mad, g_tot_med, g_tot_mad, g_flat_med, g_flat_mad);

  float rms_thr  = g_rms_med + RMS_SIGMA_GATE * safeMad(g_rms_mad);
  float tot_thr  = g_tot_med + RMS_SIGMA_GATE * safeMad(g_tot_mad);
  float flat_thr = g_flat_med + FLAT_SIGMA_GATE * g_flat_mad;
  Serial.printf("Gate: rms>%.2f AND tot>%.2f AND flat<%.4f plus (peak_ratio>=%.1f, harmonic)\n",
                rms_thr, tot_thr, flat_thr, PEAK_RATIO_MIN);
}

static bool passes_room_gate(float rms, float tot, float flat) {
  float rms_thr  = g_rms_med + RMS_SIGMA_GATE * safeMad(g_rms_mad);
  float tot_thr  = g_tot_med + RMS_SIGMA_GATE * safeMad(g_tot_mad);
  float flat_thr = g_flat_med + FLAT_SIGMA_GATE * g_flat_mad;

  if (rms <= rms_thr) return false;
  if (tot <= tot_thr) return false;
  if (g_flat_mad > 0 && flat >= flat_thr) return false;
  return true;
}

// ================== scoring ==================
static float scoreKey(const Peak *peaks, const int band_q15[6], const KeyModel &km) {
  // peak cluster score: for each model cluster, find nearest observed peak (in cents)
  float wsum = 0.0f;
  float acc = 0.0f;

  for (int j = 0; j < 3; j++) {
    float w = (float)km.c[j].weight_q15 / 32767.0f;
    if (w <= 0.0f) continue;

    float bestDiff = 1e9f;
    for (int i = 0; i < 8; i++) {
      if (peaks[i].mag <= 0.0f) continue;
      float d = fabsf(peaks[i].cents1hz - (float)km.c[j].center_cents);
      if (d < bestDiff) bestDiff = d;
    }

    float spread = (float)km.c[j].spread_cents;
    if (spread < 1.0f) spread = 1.0f;

    float z = bestDiff / spread;          // 0 = perfect
    if (z > 50.0f) z = 50.0f;
    acc += w * z;
    wsum += w;
  }
  float peakScore = (wsum > 1e-6f) ? (acc / wsum) : 50.0f;

  // band distance (L1 normalized)
  float bandScore = 0.0f;
  for (int b = 0; b < 6; b++) {
    bandScore += fabsf((float)band_q15[b] - (float)km.band_med_q15[b]) / 32767.0f;
  }

  // combined
  return 1.00f * peakScore + 0.70f * bandScore;
}

// ================== temporal logic ==================
static int g_candidate = -1;
static int g_cand_count = 0;
static int g_last_printed = -1;
static uint32_t g_last_print_ms = 0;

static void reset_candidate() {
  g_candidate = -1;
  g_cand_count = 0;
}

static void consider_candidate(int key_idx) {
  if (key_idx != g_candidate) {
    g_candidate = key_idx;
    g_cand_count = 1;
    return;
  }

  g_cand_count++;
  if (g_cand_count < ONSET_FRAMES) return;

  uint32_t now = millis();
  if (g_candidate != g_last_printed || (now - g_last_print_ms) > COOLDOWN_MS) {
    g_last_printed = g_candidate;
    g_last_print_ms = now;

    // print KEY index (and optional A0-name)
    char nm[8] = {};
    keyNameFromIndex(g_candidate, nm);
    Serial.printf("KEY %d (%s)\n", g_candidate, nm);
  }

  // cap
  g_cand_count = ONSET_FRAMES;
}

// ================== setup/loop ==================
void setup() {
  Serial.begin(115200);
  delay(300);

  if (!SPIFFS.begin(true)) fatal("ERROR: SPIFFS mount failed");
  if (!SPIFFS.exists("/model_compact.json")) fatal("ERROR: /model_compact.json missing on SPIFFS");
  if (!loadModel("/model_compact.json")) fatal("ERROR: failed to parse model_compact.json");

  Serial.printf("Loaded model: %u keys | MODEL noise tot_med=%.2f\n", (unsigned)gKeys.size(), g_model_tot_med);

  ring_buf  = (int32_t*)alloc16(N_FFT * sizeof(int32_t));
  win_i32   = (int32_t*)alloc16(N_FFT * sizeof(int32_t));
  fft_buf   = (float*)  alloc16(2 * N_FFT * sizeof(float));
  mag_buf   = (float*)  alloc16(((N_FFT/2) + 1) * sizeof(float));
  fft_table = (float*)  alloc16(2 * N_FFT * sizeof(float));
  if (!ring_buf || !win_i32 || !fft_buf || !mag_buf || !fft_table) fatal("FATAL: alloc failed");

  esp_err_t r = dsps_fft2r_init_fc32(fft_table, N_FFT);
  if (r != ESP_OK) fatal("FATAL: dsps_fft2r_init_fc32 failed");

  i2s_init_inmp441();

  ring_w = 0; ring_count = 0;
  ring_push_from_i2s(N_FFT);

  calibrateNoiseRoom();

  Serial.println("Listening. Prints KEY idx only when note-like + model match is confident.");
}

void loop() {
  ring_push_from_i2s(HOP);
  ring_copy_window_i32(win_i32);

  TDStats td = compute_td_stats(win_i32);
  compute_fft_mag(win_i32, td.mean);

  SpecFeat sf;
  compute_spectral_features(sf);

  Peak peaks[8];
  pick_top_peaks(peaks, 8);

  // peak_ratio like your logger used
  const int kmin = max(1, hz_to_bin(F_MIN));
  const int kmax = min((N_FFT/2), hz_to_bin(F_MAX));
  int nb = max(1, (kmax - kmin + 1));
  float avg_mag = (nb > 0) ? (sf.total / (float)nb) : 1.0f;
  float peak_ratio = (avg_mag > 1e-9f) ? (peaks[0].mag / avg_mag) : 0.0f;

  // 1 Hz debug
  static uint32_t lastDbg = 0;
  if (millis() - lastDbg > 1000) {
    lastDbg = millis();
    Serial.printf("DBG rms=%.0f tot=%.0f flat=%.3f top=%.1fHz pr=%.1f\n",
                  td.rms_ac, sf.total, sf.flatness, peaks[0].f_hz, peak_ratio);
  }

  // Stage 0: room gate
  if (!passes_room_gate(td.rms_ac, sf.total, sf.flatness)) { reset_candidate(); return; }

  // Stage 1: note-like gate
  if (peak_ratio < PEAK_RATIO_MIN) { reset_candidate(); return; }
  if (!has_harmonic_support(peaks, peaks[0].f_hz, peaks[0].mag)) { reset_candidate(); return; }

  // Stage 2: compute band_q15 from sf.bands normalized by sf.total (exactly like your python expects)
  int band_q15[6];
  float denom = (sf.total > 1e-9f) ? sf.total : 1.0f;
  for (int i = 0; i < 6; i++) {
    float norm = sf.bands[i] / denom;
    if (norm < 0) norm = 0;
    if (norm > 1) norm = 1;
    band_q15[i] = (int)lroundf(norm * 32767.0f);
  }

  // Stage 3: score all keys
  float bestScore = 1e9f, secondScore = 1e9f;
  int bestIdx = -1;

  for (const auto &km : gKeys) {
    float sc = scoreKey(peaks, band_q15, km);
    if (sc < bestScore) { secondScore = bestScore; bestScore = sc; bestIdx = km.key_idx; }
    else if (sc < secondScore) { secondScore = sc; }
  }

  // Stage 4: reject weak/ambiguous matches (THIS is what stops speech mapping to a key)
  if (bestIdx < 0) { reset_candidate(); return; }
  if (bestScore > BEST_MAX) { reset_candidate(); return; }
  if ((secondScore - bestScore) < MARGIN_MIN) { reset_candidate(); return; }

  // Optional: print a debug line when we *would* accept (comment out if too spammy)
  // Serial.printf("ACCEPT key=%d best=%.2f second=%.2f margin=%.2f\n", bestIdx, bestScore, secondScore, (secondScore-bestScore));

  consider_candidate(bestIdx);
}
