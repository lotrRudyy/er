#include <Arduino.h>
#include <driver/i2s.h>
#include <ArduinoFFT.h>
#include "esp_heap_caps.h"
#include "piano_template.h"

// ============================================================
// ESP32 realtime onset + harmonic gate + template matcher
// QC-ALIGNED onset: same spectral_flux_onset as qc_captures.py
// Big buffers allocated on HEAP (not .bss) to avoid dram0_0_seg overflow
// ============================================================

// ===================== DESIGN MODE (locked) =====================
// FS=48k, "post" feature exactly like matcher.py, harmonic gate on onset
static constexpr uint32_t FS_HZ = 48000;
static constexpr i2s_port_t I2S_PORT = I2S_NUM_0;

// Pins
static constexpr int PIN_I2S_WS  = 25;
static constexpr int PIN_I2S_SCK = 26;
static constexpr int PIN_I2S_SD  = 33;

// I2S DMA
static constexpr size_t I2S_DMA_BUF_LEN = 256;
static constexpr int    I2S_DMA_BUF_CNT = 8;

// Candidate gate (cheap)
static constexpr int   RMS_FRAME_SAMPLES = 480;     // 10ms
static constexpr float RMS_EMA_ALPHA_IDLE = 0.02f;
static constexpr float RMS_MULT = 4.0f;
static constexpr float RMS_ADD  = 0.002f;

static constexpr int   CAND_DEBOUNCE_MS = 160;      // debounce candidate triggers
static constexpr uint32_t NOTE_DEBOUNCE_MS = 150;   // debounce only after ACCEPTED notes

// QC onset (match qc_captures.py defaults)
static constexpr float QC_ONSET_F_LO = 80.0f;
static constexpr float QC_ONSET_F_HI = 3500.0f;
static constexpr int   QC_FRAME_SAMPLES = (int)(FS_HZ * 10 / 1000); // 480
static constexpr int   QC_HOP_SAMPLES   = (int)(FS_HZ *  5 / 1000); // 240
static constexpr int   QC_NFFT          = 512; // next pow2 >= 480
static constexpr int   QC_BASELINE_MS   = 60;
static constexpr int   QC_SEARCH_PRE_MS  = 120;
static constexpr int   QC_SEARCH_POST_MS = 260; // use stress window (more robust live)
static constexpr int   QC_CONSEC = 2;
static constexpr float QC_K_SIGMA = 4.0f; // stress-like for live robustness

// HPS harmonic gate
static constexpr int   HPS_FFT_N = 2048;
static constexpr int   HPS_MIN_HZ = 50;
static constexpr int   HPS_MAX_HZ = 2000;
static constexpr float HPS_RATIO_TH = 6.0f;
static constexpr int   HPS_EVAL_DELAY_MS = 10;
static constexpr int   HPS_EVAL_MS = 80;

// Feature ("post", locked)
static constexpr int   FEAT_SEG_MS = 200;
static constexpr int   FEAT_SEG_SAMPLES = (FS_HZ * FEAT_SEG_MS) / 1000; // 9600
static constexpr int   FEAT_FFT_N = 4096;
static constexpr int   FEAT_HOP = 512;
static constexpr float FEAT_EPS = 1e-7f;
static constexpr int   FEAT_SMOOTH_K = 3;
static constexpr float FEAT_FMIN_HZ = 50.0f;
static constexpr float FEAT_FMAX_HZ = 8000.0f;

// Thresholds (live-tune)
static float T_ABS = 0.95f;
static float T_MARGIN = 0.01f;

// Ring: must cover worst-case (enter + search_post + 200ms post)
// worst ~ 260ms + 200ms = 460ms, plus headroom -> 520ms
static constexpr int RING_MS = 520;
static constexpr int RING_SAMPLES = (FS_HZ * RING_MS) / 1000; // 24960

// ===================== EXECUTION MODE =====================
// - Audio task: fills ring + cheap gate -> enqueue "enter_sid"
// - Loop: for each job:
//     A) QC onset search around enter_sid -> onset_sid
//     B) when onset_sid+200ms available -> feature -> match -> print NOTE/REJ
// ============================================================

// ------------------ Heap buffers ------------------
static int16_t* ring_i16 = nullptr;

static int16_t* post_i16 = nullptr;              // 9600 samples

static float* vReal512 = nullptr;
static float* vImag512 = nullptr;
static float* prevMag512 = nullptr;              // (512/2+1)

static float* vReal2048 = nullptr;
static float* vImag2048 = nullptr;

static float* vReal4096 = nullptr;
static float* vImag4096 = nullptr;

static int16_t* win4096_q15 = nullptr;           // Q15 Hann window
static int16_t* frame_log_q = nullptr;           // int16 [nframes * TMPL_DIMS]

// Feature vector
static float* feat = nullptr;

// Template norms (heap, not bss)
static float* tmpl_norm_flat = nullptr;          // [TMPL_LABELS * TMPL_PER_LABEL]

// ------------------ FFT objects ------------------
static ArduinoFFT<float>* FFT512  = nullptr;
static ArduinoFFT<float>* FFT2048 = nullptr;
static ArduinoFFT<float>* FFT4096 = nullptr;

// ------------------ Ring pointers ------------------
static volatile uint32_t ring_w = 0;
static volatile uint64_t sample_id = 0;

// ------------------ Job queue ------------------
static constexpr int QDEPTH = 32; // plenty, don’t let it explode
struct Job {
  bool used = false;
  uint64_t enter_sid = 0;   // candidate time
  bool onset_done = false;
  bool onset_ok = false;
  uint64_t onset_sid = 0;   // refined QC onset
  bool hps_done = false;
  float hps_ratio = 0.0f;
  bool harmonic_ok = false;
};
static Job q[QDEPTH];
static volatile int q_wi = 0;
static volatile int q_ri = 0;

// ------------------ Gates state ------------------
static float rms_base = 0.0f;
static uint32_t last_note_ms = 0;
static uint32_t last_cand_ms = 0;
static bool cand_armed = true; // re-armed when RMS returns near baseline

static int16_t rms_frame[RMS_FRAME_SAMPLES];
static int rms_i = 0;

// ------------------ Utils ------------------
static inline float i16_to_f(int16_t s) { return (float)s / 32768.0f; }
static inline int16_t i2s32_to_i16(int32_t s32) { return (int16_t)(s32 >> 14); }

static inline int reflect_index(int idx, int n) {
  if (n <= 1) return 0;
  while (idx < 0 || idx >= n) {
    if (idx < 0) idx = -idx;
    if (idx >= n) idx = 2*n - 2 - idx;
  }
  return idx;
}

static const char* label_str_from_progmem(int li) {
  const char* p = (const char*)pgm_read_ptr(&TMPL_LABEL_STR[li]);
  return p;
}

static inline float tmpl_norm(int l, int t) {
  return tmpl_norm_flat[(l * TMPL_PER_LABEL) + t];
}

static void compute_template_norms() {
  for (int l = 0; l < TMPL_LABELS; l++) {
    for (int t = 0; t < TMPL_PER_LABEL; t++) {
      uint32_t ss = 0;
      for (int i = 0; i < TMPL_DIMS; i++) {
        int8_t vb = (int8_t)pgm_read_byte(&TMPL_Q[l][t][i]);
        int v = (int)vb;
        ss += (uint32_t)(v*v);
      }
      tmpl_norm_flat[(l * TMPL_PER_LABEL) + t] = sqrtf((float)ss) + 1e-12f;
    }
  }
}

static bool q_push(uint64_t enter_sid) {
  int next = (q_wi + 1) % QDEPTH;
  if (next == q_ri) return false; // full
  q[q_wi].used = true;
  q[q_wi].enter_sid = enter_sid;
  q[q_wi].onset_done = false;
  q[q_wi].onset_ok = false;
  q[q_wi].onset_sid = 0;
  q[q_wi].hps_done = false;
  q[q_wi].hps_ratio = 0.0f;
  q[q_wi].harmonic_ok = false;
  q_wi = next;
  return true;
}

static bool q_peek(Job*& job) {
  if (q_ri == q_wi) return false;
  job = &q[q_ri];
  return job->used;
}

static void q_pop() {
  q[q_ri].used = false;
  q_ri = (q_ri + 1) % QDEPTH;
}

static void ring_copy_from_sid(int16_t* dst, int n, uint64_t sid_start) {
  uint32_t idx = (uint32_t)(sid_start % (uint64_t)RING_SAMPLES);
  int rem = RING_SAMPLES - (int)idx;
  if (n <= rem) {
    memcpy(dst, &ring_i16[idx], n * sizeof(int16_t));
  } else {
    memcpy(dst, &ring_i16[idx], rem * sizeof(int16_t));
    memcpy(dst + rem, &ring_i16[0], (n - rem) * sizeof(int16_t));
  }
}

static inline int16_t ring_at(uint64_t sid) {
  return ring_i16[(uint32_t)(sid % (uint64_t)RING_SAMPLES)];
}

// ------------------ Robust stats (QC-like) ------------------
static float median_f(float* a, int n) {
  // insertion sort (n <= ~128 here)
  for (int i = 1; i < n; i++) {
    float key = a[i];
    int j = i - 1;
    while (j >= 0 && a[j] > key) { a[j+1] = a[j]; j--; }
    a[j+1] = key;
  }
  return a[n/2];
}

static float median_abs_dev(float* a, int n, float med) {
  for (int i = 0; i < n; i++) a[i] = fabsf(a[i] - med);
  float mad = median_f(a, n);
  return mad;
}

// ------------------ QC-aligned spectral flux onset ------------------
struct FluxFrame { float flux; uint64_t start_sid; };

// returns:
//   -1 : PENDING (need more samples)
//    0 : NOT FOUND (have enough samples, but no onset in window)
//    1 : FOUND
static int qc_spectral_flux_onset_status(
  uint64_t enter_sid,
  uint64_t& onset_sid_out,
  float& thr_out,
  float& mu_out,
  float& sd_out
) {
  const int search_pre  = (FS_HZ * QC_SEARCH_PRE_MS)  / 1000;
  const int search_post = (FS_HZ * QC_SEARCH_POST_MS) / 1000;
  const int baseline_n  = (FS_HZ * QC_BASELINE_MS)    / 1000;

  uint64_t s0 = (enter_sid > (uint64_t)search_pre) ? (enter_sid - (uint64_t)search_pre) : 0;
  uint64_t s1 = enter_sid + (uint64_t)search_post;

  uint64_t b1 = enter_sid;
  uint64_t b0 = (enter_sid > (uint64_t)baseline_n) ? (enter_sid - (uint64_t)baseline_n) : 0;

  uint64_t start = (b0 < s0) ? b0 : s0;
  uint64_t stop  = (b1 > s1) ? b1 : s1;

  // Need stop to be present in ring (plus frame length)
  uint64_t need_last = stop + (uint64_t)QC_FRAME_SAMPLES;
  if (sample_id < need_last) return -1; // PENDING (need more data)

  // Build flux frames over [start, stop] with hop
  static FluxFrame frames[140];
  int nf = 0;

  // Precompute band bins (QC: rfftfreq, searchsorted)
  const float bin_hz = (float)FS_HZ / (float)QC_NFFT;
  int k0 = (int)ceilf(QC_ONSET_F_LO / bin_hz);
  int k1 = (int)floorf(QC_ONSET_F_HI / bin_hz);
  if (k0 < 1) k0 = 1;
  int rmax = (QC_NFFT / 2);
  if (k1 > rmax) k1 = rmax;

  bool prev_valid = false;
  for (uint64_t st = start; st + (uint64_t)QC_FRAME_SAMPLES <= stop && nf < 140; st += (uint64_t)QC_HOP_SAMPLES) {
    // windowed 480 samples, zero-pad to 512
    for (int i = 0; i < QC_NFFT; i++) {
      float x = 0.0f;
      if (i < QC_FRAME_SAMPLES) x = i16_to_f(ring_at(st + (uint64_t)i));
      vReal512[i] = x;
      vImag512[i] = 0.0f;
    }

    FFT512->windowing(FFT_WIN_TYP_HANN, FFT_FORWARD);
    FFT512->compute(FFT_FORWARD);
    FFT512->complexToMagnitude();

    float flux = 0.0f;
    if (!prev_valid) {
      for (int b = 0; b <= rmax; b++) prevMag512[b] = vReal512[b];
      prev_valid = true;
      flux = 0.0f;
    } else {
      float sum = 0.0f;
      for (int b = k0; b <= k1; b++) {
        float m = vReal512[b];
        float d = m - prevMag512[b];
        if (d > 0) sum += d;
        prevMag512[b] = m;
      }
      flux = sum;
    }

    frames[nf].flux = flux;
    frames[nf].start_sid = st;
    nf++;
  }

  if (nf < 8) return 0;

  // Baseline frames where start in [b0, b1)
  static float base[140];
  int nb = 0;
  for (int i = 0; i < nf; i++) {
    uint64_t st = frames[i].start_sid;
    if (st >= b0 && st < b1) base[nb++] = frames[i].flux;
  }

  float mu = 0.0f, sd = 0.0f;
  if (nb >= 8) {
    static float tmp[140];
    for (int i = 0; i < nb; i++) tmp[i] = base[i];
    float med = median_f(tmp, nb);
    for (int i = 0; i < nb; i++) tmp[i] = base[i];
    float mad = median_abs_dev(tmp, nb, med);
    mu = med;
    sd = 1.4826f * mad;

    if (sd < 1e-9f) {
      // fallback mean/std
      float m = 0.0f;
      for (int i = 0; i < nb; i++) m += base[i];
      m /= (float)nb;
      float vv = 0.0f;
      for (int i = 0; i < nb; i++) { float d = base[i] - m; vv += d*d; }
      mu = m;
      sd = sqrtf(vv / (float)nb);
    }
  } else {
    // fallback mean/std on all frames
    float m = 0.0f;
    for (int i = 0; i < nf; i++) m += frames[i].flux;
    m /= (float)nf;
    float vv = 0.0f;
    for (int i = 0; i < nf; i++) { float d = frames[i].flux - m; vv += d*d; }
    mu = m;
    sd = sqrtf(vv / (float)nf);
  }

  float thr = (sd > 1e-9f) ? (mu + QC_K_SIGMA * sd) : (mu * 10.0f + 1e-9f);

  // Search frames where start in [s0, s1)
  int hit = 0;
  for (int i = 0; i < nf; i++) {
    uint64_t st = frames[i].start_sid;
    if (st < s0 || st >= s1) continue;

    if (frames[i].flux > thr) {
      hit++;
      if (hit >= QC_CONSEC) {
        int onset_i = i - (QC_CONSEC - 1);
        onset_sid_out = frames[onset_i].start_sid;
        thr_out = thr; mu_out = mu; sd_out = sd;
        return 1;
      }
    } else {
      hit = 0;
    }
  }

  thr_out = thr; mu_out = mu; sd_out = sd;
  return 0;
}

// ------------------ Feature extraction ("post") ------------------
static void init_hann_q15() {
  for (int i = 0; i < FEAT_FFT_N; i++) {
    float w = 0.5f - 0.5f * cosf(2.0f * (float)M_PI * (float)i / (float)(FEAT_FFT_N - 1));
    int q = (int)lroundf(w * 32767.0f);
    if (q < 0) q = 0;
    if (q > 32767) q = 32767;
    win4096_q15[i] = (int16_t)q;
  }
}

static bool extract_feature_post(const int16_t* post_seg, float* out_feat) {
  const float bin_hz = (float)FS_HZ / (float)FEAT_FFT_N;

  // fmin inclusive: f >= fmin
  int k0 = (int)ceilf(FEAT_FMIN_HZ / bin_hz);

  // fmax upper bound must match template build in matcher.py:
  //
  // Your TMPL_DIMS=677 implies an EXCLUSIVE upper bin index:
  //   k1_excl = floor(fmax/bin_hz)          (exclusive)
  //   dims = k1_excl - k0
  // With FS=48k, FFT_N=4096 => bin_hz=11.71875:
  //   k0=5, k1_excl=682 => dims=677
  int k1_excl = (int)floorf(FEAT_FMAX_HZ / bin_hz);
  const int d = (k1_excl - k0);

  if (d != TMPL_DIMS) {
    Serial.printf("ERR feat dims mismatch: d=%d TMPL_DIMS=%d (k0=%d k1_excl=%d bin_hz=%.6f)\n",
                  d, TMPL_DIMS, k0, k1_excl, bin_hz);
    return false;
  }

  int nframes = 1 + (FEAT_SEG_SAMPLES - FEAT_FFT_N) / FEAT_HOP; // 11
  if (nframes < 1) nframes = 1;
  if (nframes > 11) nframes = 11;

  // frame_log_q is int16 [nframes * TMPL_DIMS], scaled
  // store q = round(log(eps+mag) * 2048)
  const float Q = 2048.0f;

  for (int fi = 0; fi < nframes; fi++) {
    int s0 = fi * FEAT_HOP;
    for (int i = 0; i < FEAT_FFT_N; i++) {
      int si = s0 + i;
      float x = (si < FEAT_SEG_SAMPLES) ? i16_to_f(post_seg[si]) : 0.0f;
      // apply Q15 Hann
      x *= ((float)win4096_q15[i] / 32768.0f);
      vReal4096[i] = x;
      vImag4096[i] = 0.0f;
    }

    FFT4096->windowing(FFT_WIN_TYP_RECTANGLE, FFT_FORWARD); // already windowed
    FFT4096->compute(FFT_FORWARD);
    FFT4096->complexToMagnitude();

    int16_t* row = frame_log_q + fi * TMPL_DIMS;
    for (int bi = 0; bi < TMPL_DIMS; bi++) {
      int k = k0 + bi; // last used is (k1_excl-1)
      float mag = vReal4096[k];
      float lv = logf(FEAT_EPS + mag);
      int qv = (int)lroundf(lv * Q);
      if (qv < -32768) qv = -32768;
      if (qv >  32767) qv =  32767;
      row[bi] = (int16_t)qv;
    }
  }

  // median per bin across frames
  int16_t vals[11];
  for (int bi = 0; bi < TMPL_DIMS; bi++) {
    for (int fi = 0; fi < nframes; fi++) vals[fi] = frame_log_q[fi * TMPL_DIMS + bi];

    // insertion sort small
    for (int i = 1; i < nframes; i++) {
      int16_t key = vals[i];
      int j = i - 1;
      while (j >= 0 && vals[j] > key) { vals[j+1] = vals[j]; j--; }
      vals[j+1] = key;
    }

    out_feat[bi] = ((float)vals[nframes/2]) / Q;
  }

  // smooth reflect
  if (FEAT_SMOOTH_K > 0) {
    static float tmp[TMPL_DIMS]; // small enough
    const int k = FEAT_SMOOTH_K;
    const int w = 2*k + 1;
    for (int i = 0; i < TMPL_DIMS; i++) {
      float sum = 0.0f;
      for (int j = -k; j <= k; j++) sum += out_feat[reflect_index(i + j, TMPL_DIMS)];
      tmp[i] = sum / (float)w;
    }
    memcpy(out_feat, tmp, TMPL_DIMS * sizeof(float));
  }

  // mean-center + L2 normalize
  float mean = 0.0f;
  for (int i = 0; i < TMPL_DIMS; i++) mean += out_feat[i];
  mean /= (float)TMPL_DIMS;

  float ss = 0.0f;
  for (int i = 0; i < TMPL_DIMS; i++) {
    out_feat[i] -= mean;
    ss += out_feat[i] * out_feat[i];
  }
  float nrm = sqrtf(ss) + 1e-12f;
  for (int i = 0; i < TMPL_DIMS; i++) out_feat[i] /= nrm;

  return true;
}

// ------------------ Matcher ------------------
struct TopKItem { int li; float score; };

static void matcher_top3(const float* feat_in, float& s1, float& s2, float& margin,
                         int& best_li, TopKItem top3[3]) {
  static int8_t qv[TMPL_DIMS];

  for (int i = 0; i < TMPL_DIMS; i++) {
    int qi = (int)lroundf(feat_in[i] / TMPL_QSCALE);
    if (qi < -127) qi = -127;
    if (qi >  127) qi =  127;
    qv[i] = (int8_t)qi;
  }

  uint32_t qss = 0;
  for (int i = 0; i < TMPL_DIMS; i++) { int v = (int)qv[i]; qss += (uint32_t)(v*v); }
  float qn = sqrtf((float)qss) + 1e-12f;

  for (int k = 0; k < 3; k++) { top3[k].li = -1; top3[k].score = -1e9f; }

  for (int l = 0; l < TMPL_LABELS; l++) {
    float best = -1e9f;
    for (int t = 0; t < TMPL_PER_LABEL; t++) {
      int32_t dot = 0;
      for (int i = 0; i < TMPL_DIMS; i++) {
        int8_t tv = (int8_t)pgm_read_byte(&TMPL_Q[l][t][i]);
        dot += (int32_t)qv[i] * (int32_t)tv;
      }
      float sim = (float)dot / (qn * tmpl_norm(l, t));
      if (sim > best) best = sim;
    }

    for (int k = 0; k < 3; k++) {
      if (best > top3[k].score) {
        for (int s = 2; s > k; s--) top3[s] = top3[s-1];
        top3[k].li = l;
        top3[k].score = best;
        break;
      }
    }
  }

  best_li = top3[0].li;
  s1 = top3[0].score;
  s2 = top3[1].score;
  margin = s1 - s2;
}

// ------------------ HPS ------------------
static float compute_hps_ratio_2048_from_post(const int16_t* post_start_2048) {
  for (int i = 0; i < HPS_FFT_N; i++) {
    vReal2048[i] = i16_to_f(post_start_2048[i]);
    vImag2048[i] = 0.0f;
  }
  FFT2048->windowing(FFT_WIN_TYP_HANN, FFT_FORWARD);
  FFT2048->compute(FFT_FORWARD);
  FFT2048->complexToMagnitude();

  const float bin_hz = (float)FS_HZ / (float)HPS_FFT_N;

  int b0 = (int)ceilf((float)HPS_MIN_HZ / bin_hz);
  int b1 = (int)floorf((float)HPS_MAX_HZ / bin_hz);

  // Need 4*b to be in range (for H2/H3/H4)
  int rmax = (HPS_FFT_N / 2);
  int b1_lim = rmax / 4;
  if (b0 < 1) b0 = 1;
  if (b1 > b1_lim) b1 = b1_lim;
  if (b1 <= b0) return 0.0f;

  // Build HPS curve H[b] = S[b]*S[2b]*S[3b]*S[4b]
  static float H[300];
  int cnt = 0;
  float maxH = 0.0f;

  for (int b = b0; b <= b1 && cnt < 300; b++) {
    float h = vReal2048[b] * vReal2048[2*b] * vReal2048[3*b] * vReal2048[4*b];
    H[cnt++] = h;
    if (h > maxH) maxH = h;
  }
  if (cnt <= 0) return 0.0f;

  // Robust baseline: median(H) (QC-style robustness vs ringing/noise)
  static float tmp[300];
  for (int i = 0; i < cnt; i++) tmp[i] = H[i];
  float medH = median_f(tmp, cnt);

  return maxH / (medH + 1e-12f);
}

// ------------------ I2S ------------------
static void i2s_init() {
  i2s_config_t cfg = {};
  cfg.mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_RX);
  cfg.sample_rate = FS_HZ;
  cfg.bits_per_sample = I2S_BITS_PER_SAMPLE_32BIT;
  cfg.channel_format = I2S_CHANNEL_FMT_ONLY_LEFT;
  cfg.communication_format = I2S_COMM_FORMAT_STAND_I2S;
  cfg.intr_alloc_flags = ESP_INTR_FLAG_LEVEL1;
  cfg.dma_buf_count = I2S_DMA_BUF_CNT;
  cfg.dma_buf_len = I2S_DMA_BUF_LEN;
  cfg.use_apll = false;
  cfg.tx_desc_auto_clear = false;
  cfg.fixed_mclk = 0;

  i2s_pin_config_t pin = {};
  pin.bck_io_num = PIN_I2S_SCK;
  pin.ws_io_num  = PIN_I2S_WS;
  pin.data_out_num = -1;
  pin.data_in_num  = PIN_I2S_SD;

  esp_err_t e1 = i2s_driver_install(I2S_PORT, &cfg, 0, nullptr);
  esp_err_t e2 = i2s_set_pin(I2S_PORT, &pin);
  i2s_zero_dma_buffer(I2S_PORT);

  Serial.printf("i2s_driver_install=%d i2s_set_pin=%d\n", (int)e1, (int)e2);
}

// ------------------ Audio task ------------------
static void audio_task(void*) {
  static int32_t rx32[I2S_DMA_BUF_LEN];
  size_t bytes_read = 0;

  while (true) {
    if (i2s_read(I2S_PORT, rx32, sizeof(rx32), &bytes_read, portMAX_DELAY) != ESP_OK) continue;
    int ns = (int)(bytes_read / sizeof(int32_t));
    if (ns <= 0) continue;

    for (int i = 0; i < ns; i++) {
      int16_t s = i2s32_to_i16(rx32[i]);

      // write ring
      ring_i16[ring_w] = s;
      ring_w = (ring_w + 1) % RING_SAMPLES;
      uint64_t sid = sample_id++;

      // RMS frame (cheap candidate gate)
      rms_frame[rms_i++] = s;
      if (rms_i >= RMS_FRAME_SAMPLES) {
        double ss = 0.0;
        for (int k = 0; k < RMS_FRAME_SAMPLES; k++) {
          float x = i16_to_f(rms_frame[k]);
          ss += (double)(x*x);
        }
        float rms = sqrtf((float)(ss / (double)RMS_FRAME_SAMPLES));
        rms_i = 0;

        bool idle = (q_ri == q_wi);
        bool in_note_db = (millis() - last_note_ms) < NOTE_DEBOUNCE_MS;
        if (idle && !in_note_db) {
          if (rms_base <= 0.0f) rms_base = rms;
          rms_base = (1.0f - RMS_EMA_ALPHA_IDLE) * rms_base + RMS_EMA_ALPHA_IDLE * rms;
        }

        float rms_thr = max(rms_base * RMS_MULT, rms_base + RMS_ADD);

        // Re-arm once we’re back near baseline (prevents queue spam on sustain/decay)
        if (rms < rms_base * 1.5f) cand_armed = true;

        // Candidate trigger only on RMS (flux is done precisely later QC-style)
        if (cand_armed && (rms > rms_thr)) {
          uint32_t now_ms = millis();
          if ((now_ms - last_cand_ms) >= (uint32_t)CAND_DEBOUNCE_MS) {
            // don’t enqueue during accepted-note debounce
            if (!in_note_db) {
              bool ok = q_push(sid);
              last_cand_ms = now_ms;
              cand_armed = false;
              if (ok) {
                Serial.printf("CAND enq rms=%.5f base=%.5f thr=%.5f q=%d\n",
                              rms, rms_base, rms_thr, (q_wi - q_ri + QDEPTH) % QDEPTH);
              } else {
                Serial.println("CAND FULL");
              }
            }
          }
        }
      }
    }
  }
}

// ------------------ Process one job ------------------
static void process_one_job() {
  Job* job = nullptr;
  if (!q_peek(job)) return;

  const uint64_t now_sid = sample_id;

  // Stage A: refine onset using QC spectral flux around enter
  if (!job->onset_done) {
    float thr=0, mu=0, sd=0;
    uint64_t onset_sid = 0;
    int st = qc_spectral_flux_onset_status(job->enter_sid, onset_sid, thr, mu, sd);

    if (st < 0) return; // PENDING (need more samples)

    job->onset_done = true;
    job->onset_ok = (st > 0);
    job->onset_sid = onset_sid;

    if (!job->onset_ok) {
      Serial.printf("QC_ONSET NOT_FOUND enter=%llu mu=%.2f sd=%.2f thr=%.2f\n",
                    (unsigned long long)job->enter_sid, mu, sd, thr);
    } else {
      Serial.printf("QC_ONSET enter=%llu -> onset=%llu (dt_ms=%.1f) mu=%.2f sd=%.2f thr=%.2f\n",
                    (unsigned long long)job->enter_sid,
                    (unsigned long long)job->onset_sid,
                    1000.0f * (float)((int64_t)job->onset_sid - (int64_t)job->enter_sid) / (float)FS_HZ,
                    mu, sd, thr);
    }
  }

  if (!job->onset_ok) {
    Serial.println("REJ onset_not_found");
    q_pop();
    return;
  }

  // Stage B: wait until 200ms post from onset exists
  if (now_sid < job->onset_sid + (uint64_t)FEAT_SEG_SAMPLES) return;

  // Ring overrun guard
  if (now_sid - job->onset_sid > (uint64_t)(RING_SAMPLES - 1)) {
    Serial.println("REJ ring_overrun");
    q_pop();
    return;
  }

  ring_copy_from_sid(post_i16, FEAT_SEG_SAMPLES, job->onset_sid);

  // HPS
  if (!job->hps_done) {
    int delay_samp = (FS_HZ * HPS_EVAL_DELAY_MS) / 1000;
    if (delay_samp + HPS_FFT_N <= FEAT_SEG_SAMPLES) {
      job->hps_ratio = compute_hps_ratio_2048_from_post(post_i16 + delay_samp);
      job->harmonic_ok = (job->hps_ratio >= HPS_RATIO_TH);
    } else {
      job->hps_ratio = 0.0f;
      job->harmonic_ok = false;
    }
    job->hps_done = true;
  }

  bool feat_ok = extract_feature_post(post_i16, feat);
  if (!feat_ok) {
    Serial.printf("REJ feat_fail hps=%.2f\n", job->hps_ratio);
    q_pop();
    return;
  }

  float s1=-1e9f, s2=-1e9f, margin=-1e9f;
  int best_li=-1;
  TopKItem top3[3];
  matcher_top3(feat, s1, s2, margin, best_li, top3);

  bool accepted = false;
  if (job->harmonic_ok) {
    if (s1 >= 0.985f) {
      accepted = (margin >= 0.0f);
    } else if (s1 >= 0.95f) {
      accepted = (margin >= 0.010f);
    }
  }

  const char* pred = (best_li >= 0) ? label_str_from_progmem(best_li) : "";

  Serial.printf("%s pred=%s s1=%.4f s2=%.4f m=%.4f hps=%.2f harm=%d | top3=[%s %.4f, %s %.4f, %s %.4f]\n",
                accepted ? "NOTE" : "REJ ",
                pred, s1, s2, margin, job->hps_ratio, job->harmonic_ok ? 1 : 0,
                (top3[0].li>=0?label_str_from_progmem(top3[0].li):""), top3[0].score,
                (top3[1].li>=0?label_str_from_progmem(top3[1].li):""), top3[1].score,
                (top3[2].li>=0?label_str_from_progmem(top3[2].li):""), top3[2].score);

  if (accepted) last_note_ms = millis();

  q_pop();
}

// ------------------ Heap alloc ------------------
static bool alloc_or_die() {
  auto alloc8 = [](size_t nbytes) -> void* {
    // 8-bit capable internal heap
    return heap_caps_malloc(nbytes, MALLOC_CAP_8BIT);
  };

  ring_i16 = (int16_t*)alloc8(sizeof(int16_t) * RING_SAMPLES);
  post_i16 = (int16_t*)alloc8(sizeof(int16_t) * FEAT_SEG_SAMPLES);

  vReal512 = (float*)alloc8(sizeof(float) * QC_NFFT);
  vImag512 = (float*)alloc8(sizeof(float) * QC_NFFT);
  prevMag512 = (float*)alloc8(sizeof(float) * (QC_NFFT/2 + 1));

  vReal2048 = (float*)alloc8(sizeof(float) * HPS_FFT_N);
  vImag2048 = (float*)alloc8(sizeof(float) * HPS_FFT_N);

  vReal4096 = (float*)alloc8(sizeof(float) * FEAT_FFT_N);
  vImag4096 = (float*)alloc8(sizeof(float) * FEAT_FFT_N);

  win4096_q15 = (int16_t*)alloc8(sizeof(int16_t) * FEAT_FFT_N);

  // int16 frames: 11 * TMPL_DIMS
  frame_log_q = (int16_t*)alloc8(sizeof(int16_t) * 11 * TMPL_DIMS);

  feat = (float*)alloc8(sizeof(float) * TMPL_DIMS);

  tmpl_norm_flat = (float*)alloc8(sizeof(float) * TMPL_LABELS * TMPL_PER_LABEL);

  if (!ring_i16 || !post_i16 || !vReal512 || !vImag512 || !prevMag512 ||
      !vReal2048 || !vImag2048 || !vReal4096 || !vImag4096 ||
      !win4096_q15 || !frame_log_q || !feat || !tmpl_norm_flat) {
    Serial.println("FATAL: heap alloc failed");
    return false;
  }

  memset(ring_i16, 0, sizeof(int16_t) * RING_SAMPLES);
  memset(post_i16, 0, sizeof(int16_t) * FEAT_SEG_SAMPLES);
  memset(vReal512, 0, sizeof(float) * QC_NFFT);
  memset(vImag512, 0, sizeof(float) * QC_NFFT);
  memset(prevMag512, 0, sizeof(float) * (QC_NFFT/2 + 1));
  memset(vReal2048, 0, sizeof(float) * HPS_FFT_N);
  memset(vImag2048, 0, sizeof(float) * HPS_FFT_N);
  memset(vReal4096, 0, sizeof(float) * FEAT_FFT_N);
  memset(vImag4096, 0, sizeof(float) * FEAT_FFT_N);
  memset(win4096_q15, 0, sizeof(int16_t) * FEAT_FFT_N);
  memset(frame_log_q, 0, sizeof(int16_t) * 11 * TMPL_DIMS);
  memset(feat, 0, sizeof(float) * TMPL_DIMS);
  memset(tmpl_norm_flat, 0, sizeof(float) * TMPL_LABELS * TMPL_PER_LABEL);
  return true;
}

// ------------------ Setup/Loop ------------------
void setup() {
  Serial.begin(921600);
  delay(100);

  Serial.println("ESP32 Piano Detector boot");
  Serial.printf("RING_MS=%d RING_SAMPLES=%d QDEPTH=%d\n", RING_MS, RING_SAMPLES, QDEPTH);
  Serial.printf("QC onset: frame=%d hop=%d nfft=%d band=%.0f..%.0f pre=%dms post=%dms base=%dms k=%.1f consec=%d\n",
                QC_FRAME_SAMPLES, QC_HOP_SAMPLES, QC_NFFT,
                QC_ONSET_F_LO, QC_ONSET_F_HI, QC_SEARCH_PRE_MS, QC_SEARCH_POST_MS, QC_BASELINE_MS,
                QC_K_SIGMA, QC_CONSEC);
  Serial.printf("HPS: fft=%d band=%d..%d ratio_th=%.2f eval_delay=%dms eval_ms=%dms\n",
                HPS_FFT_N, HPS_MIN_HZ, HPS_MAX_HZ, HPS_RATIO_TH, HPS_EVAL_DELAY_MS, HPS_EVAL_MS);
  Serial.printf("Feature(post): seg_ms=%d fft=%d hop=%d fmin=%.0f fmax=%.0f smooth=%d dims=%d\n",
                FEAT_SEG_MS, FEAT_FFT_N, FEAT_HOP, FEAT_FMIN_HZ, FEAT_FMAX_HZ, FEAT_SMOOTH_K, TMPL_DIMS);
  Serial.printf("Templates: labels=%d per_label=%d dims=%d qscale=%g\n",
                TMPL_LABELS, TMPL_PER_LABEL, TMPL_DIMS, TMPL_QSCALE);

  if (!alloc_or_die()) {
    while (1) delay(1000);
  }

  FFT512  = new ArduinoFFT<float>(vReal512,  vImag512,  QC_NFFT,   (float)FS_HZ);
  FFT2048 = new ArduinoFFT<float>(vReal2048, vImag2048, HPS_FFT_N, (float)FS_HZ);
  FFT4096 = new ArduinoFFT<float>(vReal4096, vImag4096, FEAT_FFT_N, (float)FS_HZ);

  init_hann_q15();
  compute_template_norms();

  i2s_init();

  xTaskCreatePinnedToCore(audio_task, "audio", 4096, nullptr, 2, nullptr, 0);

  Serial.println("setup done -> entering loop()");
}

void loop() {
  process_one_job();
  delay(1);
}
