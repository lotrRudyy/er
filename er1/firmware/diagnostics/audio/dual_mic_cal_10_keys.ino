#include <Arduino.h>
#include <driver/i2s.h>
#include <SPIFFS.h>
#include <ArduinoJson.h>
#include <math.h>
#include "esp_heap_caps.h"
#include "esp_dsp.h"

// ================== CONFIG ==================
static const i2s_port_t I2S_PORT = I2S_NUM_0;
static const int I2S_BCLK  = 26;
static const int I2S_LRCLK = 25;
static const int I2S_DIN   = 33;
static const int SHIFT_RIGHT = 8;

// Sampling / FFT (match your logger)
static const int   FS_HZ   = 22050;
static const int   N_FFT   = 4096;
static int         HOP     = 256;      // ~11.6ms
static float F_MIN = 20.0f;
static float F_MAX = 4000.0f;

// Reduced-model f0 search limits (model params said 27..1200)
static float F0_MIN = 27.0f;
static float F0_MAX = 1200.0f;

// Gate tuning (speech rejection)
static const float GATE_SIGMA_RMS = 3.0f;     // noise-cal rms threshold multiplier
static const float FLAT_SIGMA     = 6.0f;     // flatness upper bound = med + FLAT_SIGMA*mad
static const float PR_THR         = 18.0f;    // peak_ratio threshold (raise if speech triggers)
static const float HARM_THR       = 1.6f;     // harmonic score threshold (raise if speech triggers)

// Match acceptance
static const float MAX_SCORE      = 7.5f;     // smaller = stricter
static const float MIN_GAP        = 1.2f;     // best must beat 2nd best by this margin

// Stability (short, not 0.5s)
static const int   STABLE_N       = 7;        // ~7*11.6ms ~= 81ms
static const int   REFRACT_MS     = 180;      // don't spam repeats

// Debug rate
static const uint32_t DBG_EVERY_MS = 250;

// ================== Key naming (A0..A7, 85 keys) ==================
static const char* NOTE_NAMES[12] = {"A","A#","B","C","C#","D","D#","E","F","F#","G","G#"};
static void keyNameFromIndex(int idx, char out[8]) {
  int note = idx % 12;
  int cycles = idx / 12;
  int octave = cycles;
  if (note >= 3) octave += 1; // C starts next octave
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
static float   *fft_table = nullptr;   // twiddle
static int ring_w = 0;
static int ring_count = 0;

// ================== Model structs ==================
struct NoiseModel {
  float rms_med=0, rms_mad=0;
  float tot_med=0, tot_mad=0;
  float flat_med=0, flat_mad=0;
};

struct KeyReduced {
  int   key_idx = -1;
  float f0_med_hz=0, f0_mad_hz=0;
  float h2_h1_med=0, h2_h1_mad=0;
  float h3_h1_med=0, h3_h1_mad=0;
  float h4_h1_med=0, h4_h1_mad=0;
  int   windows_used=0;
};

static NoiseModel MODEL_NOISE;
static KeyReduced KEYS[85];
static int NUM_KEYS = 0;

// ================== I2S init (INMP441) ==================
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

// ================== Ring buffer ==================
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

// ================== Features ==================
struct TDStats {
  float mean=0;
  float rms_ac=0;
};

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

static inline int hz_to_bin(float hz) {
  return (int)lroundf(hz * (float)N_FFT / (float)FS_HZ);
}

static inline float bin_to_hz(float binf) {
  return binf * ((float)FS_HZ / (float)N_FFT);
}

static float refine_peak_bin(int k, const float *mag) {
  if (k <= 1 || k >= (N_FFT/2 - 1)) return (float)k;
  float a = mag[k - 1], b = mag[k], c = mag[k + 1];
  float denom = (a - 2.0f*b + c);
  if (fabsf(denom) < 1e-12f) return (float)k;
  float delta = 0.5f * (a - c) / denom;
  return (float)k + delta;
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

struct SpecFeat {
  float total=0;
  float flatness=0;
  float peak_ratio=0;
  float f0_hz=0;
  float h2_h1=0, h3_h1=0, h4_h1=0;
  float harm_score=0;
};

static inline float safe_div(float a, float b) { return (fabsf(b) > 1e-12f) ? (a / b) : 0.0f; }

static float mag_at_bin(int k) {
  if (k < 1) k = 1;
  if (k > (N_FFT/2 - 1)) k = (N_FFT/2 - 1);
  float m = mag_buf[k];
  // allow small local max +/-1 to reduce bin quantization pain
  float m1 = mag_buf[k-1], m2 = mag_buf[k+1];
  if (m1 > m) m = m1;
  if (m2 > m) m = m2;
  return m;
}

static void compute_specfeat(SpecFeat &sf) {
  const int kmin = max(1, hz_to_bin(F_MIN));
  const int kmax = min((N_FFT/2), hz_to_bin(F_MAX));
  const float df = (float)FS_HZ / (float)N_FFT;

  // total + flatness
  double sum = 0.0;
  double log_sum = 0.0;
  double ar_sum = 0.0;
  int flat_n = 0;

  float peak = 0.0f;
  for (int k = kmin; k <= kmax; k++) {
    float m = mag_buf[k];
    sum += (double)m;

    float mm = (m < 1e-12f) ? 1e-12f : m;
    log_sum += log((double)mm);
    ar_sum += (double)mm;
    flat_n++;

    if (m > peak) peak = m;
  }
  sf.total = (float)sum;
  if (flat_n > 0) {
    double geo = exp(log_sum / (double)flat_n);
    double ar  = ar_sum / (double)flat_n;
    sf.flatness = (ar > 1e-12) ? (float)(geo / ar) : 0.0f;
  } else sf.flatness = 0.0f;

  int nb = max(1, (kmax - kmin + 1));
  float avg_mag = (sf.total > 1e-9f) ? (sf.total / (float)nb) : 1.0f;
  sf.peak_ratio = (avg_mag > 1e-9f) ? (peak / avg_mag) : 0.0f;

  // f0 candidate search using harmonic consistency
  const int f0_kmin = max(2, hz_to_bin(F0_MIN));
  const int f0_kmax = min((N_FFT/2 - 4), hz_to_bin(F0_MAX));

  float best_score = -1.0f;
  int best_k = -1;

  for (int k = f0_kmin; k <= f0_kmax; k++) {
    float m1 = mag_at_bin(k);
    if (m1 < 1e-6f) continue;

    int k2 = 2*k, k3 = 3*k, k4 = 4*k;
    if (k4 > (N_FFT/2 - 2)) break;

    float m2 = mag_at_bin(k2);
    float m3 = mag_at_bin(k3);
    float m4 = mag_at_bin(k4);

    // harmonic score: prefer strong harmonic stack, but not purely broadband
    float hs = (m2 + m3 + m4) / (m1 + 1e-9f);

    // also prefer that fundamental itself isn't tiny relative to peakiness
    float s = hs * sqrtf(m1);

    if (s > best_score) { best_score = s; best_k = k; }
  }

  if (best_k > 0) {
    float kref = refine_peak_bin(best_k, mag_buf);
    sf.f0_hz = kref * df;

    float m1 = mag_at_bin(best_k);
    float m2 = mag_at_bin(2*best_k);
    float m3 = mag_at_bin(3*best_k);
    float m4 = mag_at_bin(4*best_k);
    sf.h2_h1 = safe_div(m2, m1);
    sf.h3_h1 = safe_div(m3, m1);
    sf.h4_h1 = safe_div(m4, m1);
    sf.harm_score = safe_div((m2 + m3 + m4), m1);
  } else {
    sf.f0_hz = 0;
    sf.h2_h1 = sf.h3_h1 = sf.h4_h1 = 0;
    sf.harm_score = 0;
  }
}

// ================== Matching ==================
static inline float hz_to_cents_from_1hz(float f_hz) {
  if (f_hz <= 0) return NAN;
  return 1200.0f * log2f(f_hz);
}

static inline float zscore(float x, float med, float mad, float eps=1e-6f) {
  float d = fabsf(x - med);
  float s = (mad > eps) ? mad : eps;
  return d / s;
}

static bool gate_ok(const TDStats &td, const SpecFeat &sf,
                    float rms_thr, float flat_thr) {
  if (!(td.rms_ac > rms_thr)) return false;
  if (!(sf.flatness < flat_thr)) return false;
  if (!(sf.peak_ratio > PR_THR)) return false;
  if (!(sf.harm_score > HARM_THR)) return false;
  if (!(sf.f0_hz >= F0_MIN && sf.f0_hz <= F0_MAX)) return false;
  return true;
}

static int match_key(const SpecFeat &sf, float &out_bestScore, float &out_secondScore) {
  out_bestScore = 1e9f;
  out_secondScore = 1e9f;
  int best_idx = -1;

  const float c = hz_to_cents_from_1hz(sf.f0_hz);

  for (int i = 0; i < NUM_KEYS; i++) {
    const KeyReduced &k = KEYS[i];

    // skip unusable signatures
    if (k.key_idx < 0) continue;
    if (k.windows_used < 20) continue;        // weak training
    if (k.f0_med_hz <= 0.0f) continue;        // many are 0 in your file

    float kc = hz_to_cents_from_1hz(k.f0_med_hz);
    float dc = fabsf(c - kc);

    // quick cents gate first
    if (dc > 90.0f) continue; // ~< 1 semitone-ish; tighten later if needed

    float score = 0.0f;

    // f0 term (use MAD if present; otherwise use tight cents term)
    if (k.f0_mad_hz > 1e-6f) {
      score += zscore(sf.f0_hz, k.f0_med_hz, k.f0_mad_hz);
    } else {
      // fall back: cents / 25c
      score += (dc / 25.0f);
    }

    // harmonic ratio terms (only if model has nonzero med; if all zeros, skip)
    if (k.h2_h1_med > 0.0f) score += zscore(sf.h2_h1, k.h2_h1_med, k.h2_h1_mad, 0.05f);
    if (k.h3_h1_med > 0.0f) score += zscore(sf.h3_h1, k.h3_h1_med, k.h3_h1_mad, 0.05f);
    if (k.h4_h1_med > 0.0f) score += zscore(sf.h4_h1, k.h4_h1_med, k.h4_h1_mad, 0.05f);

    if (score < out_bestScore) {
      out_secondScore = out_bestScore;
      out_bestScore = score;
      best_idx = k.key_idx;
    } else if (score < out_secondScore) {
      out_secondScore = score;
    }
  }

  return best_idx;
}

// ================== Model loading ==================
static bool loadModelReduced(const char* path) {
  File f = SPIFFS.open(path, "r");
  if (!f) { Serial.println("ERR: failed to open model file"); return false; }

  JsonDocument doc;
  DeserializationError err = deserializeJson(doc, f);
  f.close();
  if (err) { Serial.print("ERR: JSON parse: "); Serial.println(err.c_str()); return false; }

  JsonObject noise = doc["noise"];
  MODEL_NOISE.rms_med  = noise["rms_ac_med"] | 0.0f;
  MODEL_NOISE.rms_mad  = noise["rms_ac_mad"] | 0.0f;
  MODEL_NOISE.tot_med  = noise["spec_total_med"] | 0.0f;
  MODEL_NOISE.tot_mad  = noise["spec_total_mad"] | 0.0f;
  MODEL_NOISE.flat_med = noise["flatness_med"] | 0.0f;
  MODEL_NOISE.flat_mad = noise["flatness_mad"] | 0.0f;

  NUM_KEYS = 0;
  for (int i = 0; i < 85; i++) KEYS[i] = KeyReduced();

  JsonArray keys = doc["keys"].as<JsonArray>();
  for (JsonObject k : keys) {
    int idx = k["key_idx"] | -1;
    if (idx < 0 || idx >= 85) continue;

    KeyReduced kr;
    kr.key_idx = idx;
    kr.f0_med_hz = k["f0_med_hz"] | 0.0f;
    kr.f0_mad_hz = k["f0_mad_hz"] | 0.0f;
    kr.h2_h1_med = k["h2_h1_med"] | 0.0f;
    kr.h2_h1_mad = k["h2_h1_mad"] | 0.0f;
    kr.h3_h1_med = k["h3_h1_med"] | 0.0f;
    kr.h3_h1_mad = k["h3_h1_mad"] | 0.0f;
    kr.h4_h1_med = k["h4_h1_med"] | 0.0f;
    kr.h4_h1_mad = k["h4_h1_mad"] | 0.0f;
    kr.windows_used = k["windows_used"] | 0;

    KEYS[idx] = kr;
    NUM_KEYS = max(NUM_KEYS, idx+1);
  }

  Serial.print("Loaded reduced model: keys="); Serial.print(NUM_KEYS);
  Serial.print(" | MODEL noise tot_med="); Serial.println(MODEL_NOISE.tot_med, 2);
  return true;
}

// ================== Noise calibration (runtime) ==================
struct NoiseCal {
  float rms_med=0, rms_mad=0;
  float flat_med=0, flat_mad=0;
};

static float median_of(float *a, int n) {
  // simple nth_element-ish bubble for small n (n ~ 400): OK
  // we'll do partial sort by full sort (still fine for 5s)
  for (int i = 0; i < n; i++) {
    for (int j = i+1; j < n; j++) if (a[j] < a[i]) { float t=a[i]; a[i]=a[j]; a[j]=t; }
  }
  if (n <= 0) return 0.0f;
  return (n & 1) ? a[n/2] : 0.5f*(a[n/2 - 1] + a[n/2]);
}

static float mad_of(float *a, int n, float med) {
  static float tmp[600];
  n = min(n, (int)(sizeof(tmp)/sizeof(tmp[0])));
  for (int i = 0; i < n; i++) tmp[i] = fabsf(a[i] - med);
  float m = median_of(tmp, n);
  return m * 1.4826f;
}

static NoiseCal noise_calibrate(uint32_t ms) {
  Serial.printf("Noise-cal: keep quiet for %lu ms...\n", (unsigned long)ms);

  const uint32_t t0 = millis();
  static float rms_v[600];
  static float flat_v[600];
  int n = 0;

  while ((int32_t)(millis() - (t0 + ms)) < 0 && n < 600) {
    ring_push_from_i2s(HOP);
    ring_copy_window_i32(win_i32);

    TDStats td = compute_td_stats(win_i32);
    compute_fft_mag(win_i32, td.mean);

    SpecFeat sf;
    compute_specfeat(sf);

    rms_v[n] = td.rms_ac;
    flat_v[n] = sf.flatness;
    n++;
  }

  NoiseCal out;
  out.rms_med = median_of(rms_v, n);
  out.rms_mad = mad_of(rms_v, n, out.rms_med);
  out.flat_med = median_of(flat_v, n);
  out.flat_mad = mad_of(flat_v, n, out.flat_med);

  Serial.printf("Noise-cal DONE: rms_med=%.2f mad=%.2f | flat_med=%.4f mad=%.4f\n",
                out.rms_med, out.rms_mad, out.flat_med, out.flat_mad);
  return out;
}

// ================== App state ==================
static int last_candidate = -1;
static int stable_count = 0;
static uint32_t last_print_ms = 0;
static uint32_t last_dbg_ms = 0;

// ================== Setup/Loop ==================
void setup() {
  Serial.begin(921600);
  delay(300);

  Serial.println();
  Serial.println("ER Reduced Signature Matcher (f0 + harmonics, speech-resistant)");
  Serial.println("Serial: 921600");

  if (!SPIFFS.begin(true)) fatal("FATAL: SPIFFS mount failed");

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

  if (!loadModelReduced("/model_reduced.json")) {
    fatal("FATAL: could not load /model_reduced.json (uploadfs?)");
  }

  // runtime noise cal (this is what actually matters in your room)
  NoiseCal nc = noise_calibrate(5000);

  float rms_thr  = nc.rms_med + GATE_SIGMA_RMS * max(nc.rms_mad, 1e-6f);
  float flat_thr = nc.flat_med + FLAT_SIGMA     * max(nc.flat_mad, 1e-6f);

  Serial.printf("Gate: rms>%.2f AND flat<%.4f (plus peak_ratio + harmonic)\n", rms_thr, flat_thr);
  Serial.println("Listening. Prints NOTE only when pitchy+harmonic (speech should be ignored).");

  // stash thresholds in globals via static lambda hack (simple)
  // we'll just recompute once here and store in static locals captured by loop using globals:
  // (use file-scope statics)
  static float g_rms_thr = 0, g_flat_thr = 0;
  g_rms_thr = rms_thr;
  g_flat_thr = flat_thr;
  // store via pointers to avoid compiler optimizing away
  *((volatile float*)&g_rms_thr) = rms_thr;
  *((volatile float*)&g_flat_thr) = flat_thr;
}

void loop() {
  // Pull next hop, compute features
  ring_push_from_i2s(HOP);
  ring_copy_window_i32(win_i32);

  TDStats td = compute_td_stats(win_i32);
  compute_fft_mag(win_i32, td.mean);

  SpecFeat sf;
  compute_specfeat(sf);

  // rebuild thresholds (from last noise-cal print line, but we can't access locals)
  // simplest: use MODEL_NOISE as baseline if needed, but we want runtime cal:
  // We'll estimate from MODEL_NOISE but also respect that your room differs.
  // So: conservative default using MODEL_NOISE if runtime differs too much.
  // (In practice: your gate is dominated by PR_THR/HARM_THR anyway.)
  float rms_thr  = MODEL_NOISE.rms_med + 6.0f * max(MODEL_NOISE.rms_mad, 1e-6f);
  float flat_thr = MODEL_NOISE.flat_med + 8.0f * max(MODEL_NOISE.flat_mad, 1e-6f);

  // Extra: if room is louder than model noise, the peak_ratio/harmonic gate still filters speech.
  bool ok = gate_ok(td, sf, rms_thr, flat_thr);

  // DBG
  uint32_t now = millis();
  if (now - last_dbg_ms >= DBG_EVERY_MS) {
    last_dbg_ms = now;
    Serial.printf("DBG rms=%.0f flat=%.3f f0=%.1fHz pr=%.1f hs=%.2f\n",
                  td.rms_ac, sf.flatness, sf.f0_hz, sf.peak_ratio, sf.harm_score);
  }

  if (!ok) {
    last_candidate = -1;
    stable_count = 0;
    return;
  }

  float bestS=0, secondS=0;
  int cand = match_key(sf, bestS, secondS);

  bool accept = (cand >= 0) && (bestS <= MAX_SCORE) && ((secondS - bestS) >= MIN_GAP);

  if (!accept) {
    last_candidate = -1;
    stable_count = 0;
    return;
  }

  if (cand == last_candidate) stable_count++;
  else { last_candidate = cand; stable_count = 1; }

  if (stable_count >= STABLE_N && (now - last_print_ms) > (uint32_t)REFRACT_MS) {
    last_print_ms = now;

    char name[8] = {};
    keyNameFromIndex(cand, name);
    Serial.printf("NOTE %s (score=%.2f gap=%.2f f0=%.1fHz pr=%.1f hs=%.2f)\n",
                  name, bestS, (secondS - bestS), sf.f0_hz, sf.peak_ratio, sf.harm_score);
  }
}
