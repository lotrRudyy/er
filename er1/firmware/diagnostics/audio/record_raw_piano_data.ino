#include <Arduino.h>
#include <driver/i2s.h>
#include <math.h>
#include "esp_heap_caps.h"
#include "esp_dsp.h"

// ================== CONFIG ==================
static const i2s_port_t I2S_PORT = I2S_NUM_0;
static const int I2S_BCLK  = 26;
static const int I2S_LRCLK = 25;
static const int I2S_DIN   = 33;
static const int SHIFT_RIGHT = 8;

// Sampling / FFT
static const int   FS_HZ   = 22050;
static const int   N_FFT   = 4096;     // keep 4096 for resolution
static int         HOP     = 256;      // default ~11.6ms => ~86Hz logging (change via cmd)

// Frequency range of interest
static float F_MIN = 20.0f;
static float F_MAX = 4000.0f;

// Logging / calibration
static const int NUM_KEYS   = 85;      // A0..A7 (85 keys)
static uint32_t  CAL_MS_PER_KEY = 10000; // default 10s (change via cmd)
static uint32_t  GAP_MS = 300;

// Peaks
static int PEAK_K = 8; // log top K peaks (change via cmd)

// ===== additions (requested) =====
static const uint32_t SILENCE_MS = 10000;     // 10s silence recording
static const float    RELEASE_AT = 0.80f;     // release cue at 80%
// ===========================================

// ---------- key naming (A0..A7, 85 keys) ----------
static const char* NOTE_NAMES[12] = {"A","A#","B","C","C#","D","D#","E","F","F#","G","G#"};
static void keyNameFromIndex(int idx, char out[8]) {
  int note = idx % 12;
  int cycles = idx / 12;
  int octave = cycles;
  if (note >= 3) octave += 1; // C starts next octave
  snprintf(out, 8, "%s%d", NOTE_NAMES[note], octave);
}

// ---------- aligned alloc ----------
static void* alloc16(size_t bytes) {
  return heap_caps_aligned_alloc(16, bytes, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
}
static void fatal(const char* msg) {
  Serial.println(msg);
  while (1) delay(1000);
}

// ---------- DSP buffers ----------
static int32_t *ring_buf  = nullptr;   // N_FFT (power of 2)
static int32_t *win_i32   = nullptr;   // N_FFT
static float   *fft_buf   = nullptr;   // 2*N_FFT complex
static float   *mag_buf   = nullptr;   // (N_FFT/2)+1
static float   *fft_table = nullptr;   // twiddle

static int ring_w = 0;
static int ring_count = 0;

// ---------- I2S init ----------
static void i2s_init_inmp441() {
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

  i2s_pin_config_t pins = {};
  pins.bck_io_num = I2S_BCLK;
  pins.ws_io_num  = I2S_LRCLK;
  pins.data_out_num = I2S_PIN_NO_CHANGE;
  pins.data_in_num  = I2S_DIN;

  i2s_driver_install(I2S_PORT, &cfg, 0, nullptr);
  i2s_set_pin(I2S_PORT, &pins);
  i2s_zero_dma_buffer(I2S_PORT);
}

// ---------- ring buffer ----------
static void ring_push_from_i2s(int needed_samples) {
  static int32_t tmp[256];
  int pushed = 0;

  while (pushed < needed_samples) {
    size_t bytes_read = 0;
    esp_err_t err = i2s_read(I2S_PORT, (void*)tmp, sizeof(tmp), &bytes_read, 50 / portTICK_PERIOD_MS);
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

// ---------- time-domain stats ----------
struct TDStats {
  float mean = 0;
  float rms_ac = 0;
  float ac_peak = 0;
  float crest = 0;
  int   zc = 0;        // zero crossings of AC component
  int32_t minv = 0;
  int32_t maxv = 0;
};

static TDStats compute_td_stats(const int32_t *x) {
  TDStats s;
  int32_t mn = x[0], mx = x[0];
  double sum = 0.0;
  for (int i = 0; i < N_FFT; i++) {
    int32_t v = x[i];
    if (v < mn) mn = v;
    if (v > mx) mx = v;
    sum += (double)v;
  }
  s.minv = mn; s.maxv = mx;
  s.mean = (float)(sum / (double)N_FFT);

  double acc = 0.0;
  float prev = (float)x[0] - s.mean;
  s.zc = 0;
  s.ac_peak = 0;

  for (int i = 0; i < N_FFT; i++) {
    float a = (float)x[i] - s.mean;
    acc += (double)a * (double)a;
    float aa = fabsf(a);
    if (aa > s.ac_peak) s.ac_peak = aa;
    if ((prev <= 0 && a > 0) || (prev >= 0 && a < 0)) s.zc++;
    prev = a;
  }
  s.rms_ac = (float)sqrt(acc / (double)N_FFT);
  s.crest = (s.rms_ac > 1e-9f) ? (s.ac_peak / s.rms_ac) : 0.0f;
  return s;
}

static inline int hz_to_bin(float hz) {
  return (int)lroundf(hz * (float)N_FFT / (float)FS_HZ);
}

static float refine_peak_bin(int k, const float *mag) {
  if (k <= 1 || k >= (N_FFT/2 - 1)) return (float)k;
  float a = mag[k - 1], b = mag[k], c = mag[k + 1];
  float denom = (a - 2.0f*b + c);
  if (fabsf(denom) < 1e-12f) return (float)k;
  float delta = 0.5f * (a - c) / denom;
  return (float)k + delta;
}

// ---------- FFT magnitude ----------
static void compute_fft_mag(const int32_t *x, float mean) {
  // Hann
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

struct Peak { float f_hz; float mag; int bin; };

static int pick_top_peaks(Peak *out_peaks, int max_peaks) {
  const int bin_min = max(2, hz_to_bin(F_MIN));
  const int bin_max = min((N_FFT/2 - 2), hz_to_bin(F_MAX));

  for (int i = 0; i < max_peaks; i++) out_peaks[i] = {0, 0, -1};

  for (int k = bin_min + 1; k <= bin_max - 1; k++) {
    float m0 = mag_buf[k - 1], m1 = mag_buf[k], m2 = mag_buf[k + 1];
    if (!(m1 > m0 && m1 > m2)) continue;

    for (int i = 0; i < max_peaks; i++) {
      if (m1 > out_peaks[i].mag) {
        for (int j = max_peaks - 1; j > i; j--) out_peaks[j] = out_peaks[j - 1];
        out_peaks[i].mag = m1;
        out_peaks[i].bin = k;
        float kref = refine_peak_bin(k, mag_buf);
        out_peaks[i].f_hz = kref * ((float)FS_HZ / (float)N_FFT);
        break;
      }
    }
  }

  int n = 0;
  for (int i = 0; i < max_peaks; i++) if (out_peaks[i].bin >= 0) n++;
  return n;
}

// ---------- spectral features ----------
struct SpecFeat {
  float centroid_hz = 0;
  float bandwidth_hz = 0;
  float rolloff85_hz = 0;
  float flatness = 0;
  float total = 0;
  float bands[6] = {0}; // 6 coarse bands
};

// Bands chosen to cover piano-ish range (you can change later)
static void compute_spectral_features(SpecFeat &sf) {
  const int kmin = max(1, hz_to_bin(F_MIN));
  const int kmax = min((N_FFT/2), hz_to_bin(F_MAX));
  const float df = (float)FS_HZ / (float)N_FFT;

  double sum = 0.0;
  double sum_f = 0.0;

  // flatness: geometric mean / arithmetic mean
  double log_sum = 0.0;
  double ar_sum = 0.0;
  int flat_n = 0;

  // bands: [20-80], [80-160], [160-320], [320-640], [640-1280], [1280-4000]
  double b[6] = {0,0,0,0,0,0};

  for (int k = kmin; k <= kmax; k++) {
    float m = mag_buf[k];
    float f = k * df;
    sum += (double)m;
    sum_f += (double)m * (double)f;

    float mm = (m < 1e-12f) ? 1e-12f : m;
    log_sum += log((double)mm);
    ar_sum += (double)mm;
    flat_n++;

    if (f < 80) b[0] += m;
    else if (f < 160) b[1] += m;
    else if (f < 320) b[2] += m;
    else if (f < 640) b[3] += m;
    else if (f < 1280) b[4] += m;
    else b[5] += m;
  }

  sf.total = (float)sum;
  sf.centroid_hz = (sum > 1e-12) ? (float)(sum_f / sum) : 0.0f;

  double bw_acc = 0.0;
  if (sum > 1e-12) {
    for (int k = kmin; k <= kmax; k++) {
      float m = mag_buf[k];
      float f = k * df;
      double d = (double)f - (double)sf.centroid_hz;
      bw_acc += (double)m * d * d;
    }
    sf.bandwidth_hz = (float)sqrt(bw_acc / sum);
  } else sf.bandwidth_hz = 0.0f;

  double target = 0.85 * sum;
  double run = 0.0;
  sf.rolloff85_hz = 0.0f;
  if (sum > 1e-12) {
    for (int k = kmin; k <= kmax; k++) {
      run += (double)mag_buf[k];
      if (run >= target) { sf.rolloff85_hz = k * df; break; }
    }
  }

  if (flat_n > 0) {
    double geo = exp(log_sum / (double)flat_n);
    double ar  = ar_sum / (double)flat_n;
    sf.flatness = (ar > 1e-12) ? (float)(geo / ar) : 0.0f;
  } else sf.flatness = 0.0f;

  for (int i = 0; i < 6; i++) sf.bands[i] = (float)b[i];
}

// ---------- CAL state ----------
static int  cal_next_key = 0;
static bool cal_active = false;
static bool cal_wait_enter = false;
static int  key_lines[NUM_KEYS] = {0};

// ---------- CSV header ----------
static void print_csv_header() {
  Serial.println(
    "type,t_ms,key_idx,key,"
    "mean,rms_ac,ac_peak,crest,zc,min,max,"
    "spec_total,centroid_hz,bandwidth_hz,rolloff85_hz,flatness,"
    "band1_20_80,band2_80_160,band3_160_320,band4_320_640,band5_640_1280,band6_1280_4000,"
    "p1_hz,p1_mag,p2_hz,p2_mag,p3_hz,p3_mag,p4_hz,p4_mag,p5_hz,p5_mag,p6_hz,p6_mag,p7_hz,p7_mag,p8_hz,p8_mag"
  );
}

// ---------- log one window ----------
static void log_cal_win_csv(int key_idx, const char *key, const TDStats &td, const SpecFeat &sf, const Peak *peaks) {
  Serial.print("CAL_WIN,");
  Serial.print((uint32_t)millis()); Serial.print(",");
  Serial.print(key_idx); Serial.print(",");
  Serial.print(key); Serial.print(",");

  Serial.print(td.mean, 2); Serial.print(",");
  Serial.print(td.rms_ac, 2); Serial.print(",");
  Serial.print(td.ac_peak, 2); Serial.print(",");
  Serial.print(td.crest, 3); Serial.print(",");
  Serial.print(td.zc); Serial.print(",");
  Serial.print((long)td.minv); Serial.print(",");
  Serial.print((long)td.maxv); Serial.print(",");

  Serial.print(sf.total, 2); Serial.print(",");
  Serial.print(sf.centroid_hz, 2); Serial.print(",");
  Serial.print(sf.bandwidth_hz, 2); Serial.print(",");
  Serial.print(sf.rolloff85_hz, 2); Serial.print(",");
  Serial.print(sf.flatness, 6); Serial.print(",");

  for (int i = 0; i < 6; i++) {
    Serial.print(sf.bands[i], 2);
    Serial.print(",");
  }

  for (int i = 0; i < 8; i++) {
    Serial.print(peaks[i].f_hz, 2); Serial.print(",");
    Serial.print(peaks[i].mag, 2);
    if (i != 7) Serial.print(",");
  }
  Serial.println();
}

static void prompt_next_key() {
  if (cal_next_key >= NUM_KEYS) {
    Serial.println("=== CAL DONE ALL ===");
    Serial.println("Type: print");
    cal_wait_enter = false;
    return;
  }
  char name[8] = {};
  keyNameFromIndex(cal_next_key, name);

  Serial.println();
  Serial.print("NEXT KEY: ["); Serial.print(cal_next_key); Serial.print("/"); Serial.print(NUM_KEYS-1);
  Serial.print("]  >>>  "); Serial.print(name); Serial.println("  <<<");
  Serial.println("1) Put your finger on that key");
  Serial.println("2) When ready, press ENTER to start capture");
  Serial.println("============================================================");
  cal_wait_enter = true;
}

// ---------- do one key capture (fat logging) ----------
static void capture_key_stream(int key_idx) {
  char name[8] = {};
  keyNameFromIndex(key_idx, name);

  delay(GAP_MS);

  Serial.printf("CAPTURE start key=%s ms=%lu fs=%d N=%d hop=%d\n",
                name, (unsigned long)CAL_MS_PER_KEY, FS_HZ, N_FFT, HOP);
  Serial.print("{\"type\":\"CAP_BEGIN\",\"key_idx\":"); Serial.print(key_idx);
  Serial.print(",\"key\":\""); Serial.print(name);
  Serial.print("\",\"fs\":"); Serial.print(FS_HZ);
  Serial.print(",\"n\":"); Serial.print(N_FFT);
  Serial.print(",\"hop\":"); Serial.print(HOP);
  Serial.print(",\"ms\":"); Serial.print((unsigned long)CAL_MS_PER_KEY);
  Serial.println("}");

  // ===== addition: release cue at 80% =====
  const uint32_t t0 = millis();
  const uint32_t tend = t0 + CAL_MS_PER_KEY;
  const uint32_t trel = t0 + (uint32_t)((float)CAL_MS_PER_KEY * RELEASE_AT);
  bool did_release_cue = false;
  // =======================================

  int windows = 0;
  float best_f = 0.0f;
  float best_conf = -1.0f;
  double rms_sum = 0.0;
  float rms_max = 0.0f;

  while ((int32_t)(millis() - tend) < 0) {
    // ===== addition: one-time release prompt =====
    if (!did_release_cue && (int32_t)(millis() - trel) >= 0) {
      Serial.println(">>> RELEASE NOW (80%) <<<");
      did_release_cue = true;
    }
    // ===========================================

    ring_push_from_i2s(HOP);
    ring_copy_window_i32(win_i32);

    TDStats td = compute_td_stats(win_i32);
    compute_fft_mag(win_i32, td.mean);

    Peak peaks[8] = {};
    pick_top_peaks(peaks, 8);

    SpecFeat sf;
    compute_spectral_features(sf);

    // crude confidence: peak1 / avg_mag_in_range (your earlier-style)
    // keep exactly as before (peak_mag/avg_mag proxy via total/nbins)
    const int kmin = max(1, hz_to_bin(F_MIN));
    const int kmax = min((N_FFT/2), hz_to_bin(F_MAX));
    int nb = max(1, (kmax - kmin + 1));
    float avg_mag = (nb > 0) ? (sf.total / (float)nb) : 1.0f;
    float peak_mag = peaks[0].mag;
    float conf = (avg_mag > 1e-9f) ? (peak_mag / avg_mag) : 0.0f;

    log_cal_win_csv(key_idx, name, td, sf, peaks);
    key_lines[key_idx]++;
    windows++;

    // pick “best” window by conf
    if (conf > best_conf) {
      best_conf = conf;
      best_f = peaks[0].f_hz;
    }

    rms_sum += (double)td.rms_ac;
    if (td.rms_ac > rms_max) rms_max = td.rms_ac;
  }

  Serial.print("{\"type\":\"CAP_END\",\"key_idx\":"); Serial.print(key_idx);
  Serial.print(",\"key\":\""); Serial.print(name);
  Serial.print("\",\"windows\":"); Serial.print(windows);
  Serial.println("}");

  float rms_mean = (windows > 0) ? (float)(rms_sum / (double)windows) : 0.0f;

  Serial.print("FEEDBACK key_idx="); Serial.print(key_idx);
  Serial.print(" key="); Serial.print(name);
  Serial.print("  best_f="); Serial.print(best_f, 2); Serial.print("Hz");
  Serial.print(" best_conf="); Serial.print(best_conf, 3);
  Serial.print("  rms_mean="); Serial.print(rms_mean, 1);
  Serial.print(" rms_max="); Serial.print(rms_max, 1);
  Serial.print("  windows="); Serial.println(windows);
}

// ===== addition: 10s silence capture at start of cal =====
static void capture_silence_stream() {
  const int key_idx = -1;
  const char *name = "SILENCE";

  delay(GAP_MS);

  Serial.printf("CAPTURE start key=%s ms=%lu fs=%d N=%d hop=%d\n",
                name, (unsigned long)SILENCE_MS, FS_HZ, N_FFT, HOP);
  Serial.print("{\"type\":\"CAP_BEGIN\",\"key_idx\":"); Serial.print(key_idx);
  Serial.print(",\"key\":\""); Serial.print(name);
  Serial.print("\",\"fs\":"); Serial.print(FS_HZ);
  Serial.print(",\"n\":"); Serial.print(N_FFT);
  Serial.print(",\"hop\":"); Serial.print(HOP);
  Serial.print(",\"ms\":"); Serial.print((unsigned long)SILENCE_MS);
  Serial.println("}");

  uint32_t t0 = millis();
  uint32_t tend = t0 + SILENCE_MS;

  int windows = 0;
  float best_f = 0.0f;
  float best_conf = -1.0f;
  double rms_sum = 0.0;
  float rms_max = 0.0f;

  while ((int32_t)(millis() - tend) < 0) {
    ring_push_from_i2s(HOP);
    ring_copy_window_i32(win_i32);

    TDStats td = compute_td_stats(win_i32);
    compute_fft_mag(win_i32, td.mean);

    Peak peaks[8] = {};
    pick_top_peaks(peaks, 8);

    SpecFeat sf;
    compute_spectral_features(sf);

    const int kmin = max(1, hz_to_bin(F_MIN));
    const int kmax = min((N_FFT/2), hz_to_bin(F_MAX));
    int nb = max(1, (kmax - kmin + 1));
    float avg_mag = (nb > 0) ? (sf.total / (float)nb) : 1.0f;
    float peak_mag = peaks[0].mag;
    float conf = (avg_mag > 1e-9f) ? (peak_mag / avg_mag) : 0.0f;

    log_cal_win_csv(key_idx, name, td, sf, peaks);
    windows++;

    if (conf > best_conf) {
      best_conf = conf;
      best_f = peaks[0].f_hz;
    }

    rms_sum += (double)td.rms_ac;
    if (td.rms_ac > rms_max) rms_max = td.rms_ac;
  }

  Serial.print("{\"type\":\"CAP_END\",\"key_idx\":"); Serial.print(key_idx);
  Serial.print(",\"key\":\""); Serial.print(name);
  Serial.print("\",\"windows\":"); Serial.print(windows);
  Serial.println("}");

  float rms_mean = (windows > 0) ? (float)(rms_sum / (double)windows) : 0.0f;

  Serial.print("FEEDBACK key_idx="); Serial.print(key_idx);
  Serial.print(" key="); Serial.print(name);
  Serial.print("  best_f="); Serial.print(best_f, 2); Serial.print("Hz");
  Serial.print(" best_conf="); Serial.print(best_conf, 3);
  Serial.print("  rms_mean="); Serial.print(rms_mean, 1);
  Serial.print(" rms_max="); Serial.print(rms_max, 1);
  Serial.print("  windows="); Serial.println(windows);
}
// =======================================================

// ---------- control ----------
static void cal_start() {
  cal_active = true;
  Serial.println();
  Serial.println("=== CAL START (CSV) ===");
  Serial.printf("fs=%d N=%d hop=%d (~%.1f Hz logs) dur_ms=%lu peaks=%d f=[%.1f..%.1f]\n",
                FS_HZ, N_FFT, HOP, (float)FS_HZ/(float)HOP, (unsigned long)CAL_MS_PER_KEY, PEAK_K, F_MIN, F_MAX);
  Serial.println("TIP: set Serial Monitor to 921600 baud.");
  print_csv_header();

  // ===== addition: silence capture =====
  Serial.println();
  Serial.println("=== SILENCE (10s) ===");
  Serial.println("Do NOT touch the piano. Keep the room as quiet as possible.");
  Serial.println("============================================================");
  capture_silence_stream();
  // ================================

  prompt_next_key();
}

static void cal_print_summary() {
  Serial.print("SUMMARY,next_key="); Serial.print(cal_next_key);
  Serial.print(",dur_ms="); Serial.print((unsigned long)CAL_MS_PER_KEY);
  Serial.print(",hop="); Serial.print(HOP);
  Serial.print(",lines=[");
  for (int i = 0; i < NUM_KEYS; i++) {
    if (i) Serial.print(",");
    Serial.print(key_lines[i]);
  }
  Serial.println("]");
}

// ---------- info ----------
static void print_info() {
  Serial.printf("INMP441: BCLK=%d LRCLK=%d DIN=%d shift>>%d left_only\n", I2S_BCLK, I2S_LRCLK, I2S_DIN, SHIFT_RIGHT);
  Serial.printf("DSP: fs=%d N=%d hop=%d (~%.1f Hz logs) df=%.4fHz f=[%.1f..%.1f]\n",
                FS_HZ, N_FFT, HOP, (float)FS_HZ/(float)HOP, (float)FS_HZ/(float)N_FFT, F_MIN, F_MAX);
  Serial.printf("Cal: keys=%d dur_ms=%lu next_key=%d peaks=%d\n",
                NUM_KEYS, (unsigned long)CAL_MS_PER_KEY, cal_next_key, PEAK_K);
}

// ---------- Serial parsing ----------
static bool read_line(String &out) {
  while (Serial.available()) {
    char c = (char)Serial.read();
    if (c == '\r') continue;
    if (c == '\n') return true;
    out += c;
    if (out.length() > 200) out.remove(200);
  }
  return false;
}

void setup() {
  Serial.begin(921600);
  delay(300);

  Serial.println();
  Serial.println("PianoRiddle INMP441 CAL LOGGER (fat CSV, enter-per-key)");
  Serial.println("Set Serial Monitor: 921600 baud");

  ring_buf  = (int32_t*)alloc16(N_FFT * sizeof(int32_t));
  win_i32   = (int32_t*)alloc16(N_FFT * sizeof(int32_t));
  fft_buf   = (float*)  alloc16(2 * N_FFT * sizeof(float));
  mag_buf   = (float*)  alloc16(((N_FFT/2) + 1) * sizeof(float));
  fft_table = (float*)  alloc16(2 * N_FFT * sizeof(float));
  if (!ring_buf || !win_i32 || !fft_buf || !mag_buf || !fft_table) fatal("FATAL: alloc failed");

  esp_err_t r = dsps_fft2r_init_fc32(fft_table, N_FFT);
  if (r != ESP_OK) { Serial.printf("FATAL: fft init failed: %d\n", (int)r); fatal("STOP"); }

  i2s_init_inmp441();

  ring_w = 0; ring_count = 0;
  ring_push_from_i2s(N_FFT);

  memset(key_lines, 0, sizeof(key_lines));

  print_info();
  Serial.println("Commands:");
  Serial.println("  cal              -> start calibration (prints header, then prompts keys)");
  Serial.println("  print            -> print summary (lines per key)");
  Serial.println("  dur <ms>         -> set per-key duration (e.g. dur 10000)");
  Serial.println("  hop <n>          -> set hop samples (128/256/512). smaller=faster logs");
  Serial.println("  peaks <n>        -> set peak count (1..8) (note: header fixed for 8)");
  Serial.println("  frange <min> <max> -> set F_MIN/F_MAX (e.g. frange 20 4000)");
  Serial.println("  startkey <idx>   -> set next key index (e.g. startkey 0)");
  Serial.println("  info             -> print config");
  Serial.println();
  Serial.println("While calibrating: press ENTER (empty line) to capture the prompted key.");
}

void loop() {
  static String line;
  if (!read_line(line)) return;
  line.trim();

  // If we're waiting for ENTER during calibration, an empty line triggers capture.
  if (cal_active && cal_wait_enter && line.length() == 0) {
    if (cal_next_key < NUM_KEYS) {
      capture_key_stream(cal_next_key);
      cal_next_key++;
      prompt_next_key();
    } else {
      Serial.println("=== CAL DONE ALL ===");
      Serial.println("Type: print");
      cal_wait_enter = false;
    }
    line = "";
    return;
  }

  if (line.length() == 0) { line = ""; return; }

  if (line.equalsIgnoreCase("info")) {
    print_info();

  } else if (line.equalsIgnoreCase("print")) {
    cal_print_summary();

  } else if (line.equalsIgnoreCase("cal")) {
    if (!cal_active) cal_start();
    else prompt_next_key();

  } else if (line.startsWith("dur")) {
    int sp = line.indexOf(' ');
    if (sp > 0) {
      uint32_t v = (uint32_t)line.substring(sp + 1).toInt();
      if (v >= 1000 && v <= 600000) {
        CAL_MS_PER_KEY = v;
        Serial.printf("dur_ms=%lu\n", (unsigned long)CAL_MS_PER_KEY);
      } else Serial.println("dur: range 1000..600000");
    } else Serial.printf("dur_ms=%lu\n", (unsigned long)CAL_MS_PER_KEY);

  } else if (line.startsWith("hop")) {
    int sp = line.indexOf(' ');
    if (sp > 0) {
      int v = line.substring(sp + 1).toInt();
      if (v == 128 || v == 256 || v == 512) {
        HOP = v;
        Serial.printf("hop=%d (~%.1f Hz logs)\n", HOP, (float)FS_HZ/(float)HOP);
      } else Serial.println("hop: use 128/256/512");
    } else Serial.printf("hop=%d\n", HOP);

  } else if (line.startsWith("peaks")) {
    int sp = line.indexOf(' ');
    if (sp > 0) {
      int v = line.substring(sp + 1).toInt();
      if (v >= 1 && v <= 8) {
        PEAK_K = v;
        Serial.printf("peaks=%d (note: CSV still prints 8 peak slots)\n", PEAK_K);
      } else Serial.println("peaks: 1..8");
    } else Serial.printf("peaks=%d\n", PEAK_K);

  } else if (line.startsWith("frange")) {
    int sp1 = line.indexOf(' ');
    int sp2 = (sp1 > 0) ? line.indexOf(' ', sp1 + 1) : -1;
    if (sp1 > 0 && sp2 > sp1) {
      float a = line.substring(sp1 + 1, sp2).toFloat();
      float b = line.substring(sp2 + 1).toFloat();
      if (a > 0 && b > a) {
        F_MIN = a; F_MAX = b;
        Serial.printf("frange=%.1f..%.1f\n", F_MIN, F_MAX);
      } else Serial.println("frange invalid");
    } else Serial.println("usage: frange <min> <max>");

  } else if (line.startsWith("startkey")) {
    int sp = line.indexOf(' ');
    if (sp > 0) {
      int idx = line.substring(sp + 1).toInt();
      if (idx >= 0 && idx < NUM_KEYS) {
        cal_next_key = idx;
        Serial.printf("next_key=%d\n", cal_next_key);
        if (cal_active) prompt_next_key();
      } else Serial.println("startkey: 0..84");
    } else Serial.printf("next_key=%d\n", cal_next_key);

  } else {
    Serial.println("Unknown. Type: cal | print | dur <ms> | hop <n> | peaks <n> | frange <min> <max> | startkey <idx> | info");
  }

  line = "";
}
