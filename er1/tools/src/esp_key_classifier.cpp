#include "esp_key_classifier.h"

// You MUST generate this header on PC and include it in your firmware project.
// It defines: KC_FS, KC_SEG_MS, KC_FFT_N, KC_FMIN_HZ, KC_FMAX_HZ, KC_SMOOTH_BINS,
//             KC_NUM_LABELS, KC_NUM_EXEMPLARS, KC_FEAT_D,
//             KC_LABELS[], KC_TEMPLATES[][][]
#include "templates_generated.h"

#include <math.h>
#include <string.h>

#if __has_include(<esp_dsp.h>)
  #include <esp_dsp.h>
  #define KC_USE_ESP_DSP 1
#else
  #define KC_USE_ESP_DSP 0
#endif

static inline float clampf_(float x, float lo, float hi) {
  return (x < lo) ? lo : (x > hi) ? hi : x;
}

static inline float fast_rms_(const int16_t* x, int n) {
  double acc = 0.0;
  for (int i = 0; i < n; i++) {
    float v = (float)x[i] / 32768.0f;
    acc += (double)(v * v);
  }
  acc /= (double)(n > 0 ? n : 1);
  return (float)sqrt(acc + 1e-12);
}

static inline void hann_(float* w, int n) {
  for (int i = 0; i < n; i++) {
    float a = 2.0f * (float)M_PI * (float)i / (float)((n > 1) ? (n - 1) : 1);
    w[i] = 0.5f - 0.5f * cosf(a);
  }
}

void KeyClassifier::begin(int fs) {
  fs_ = fs;
  ring_w_ = 0;
  ring_n_ = 0;
  capturing_ = false;
  capture_need_ = 0;
  capture_have_ = 0;
  bed_rms_ = 0.0f;
  prev_rms_ = 0.0f;
  consec_ = 0;
  refractory_samples_left_ = 0;
  fft_inited_ = false;
  init_fft_();
}

void KeyClassifier::init_fft_() {
#if KC_USE_ESP_DSP
  // esp-dsp FFT init
  // This is required once before dsps_fft2r_fc32.
  // If this fails to compile, install esp-dsp or switch to a different FFT lib.
  dsps_fft2r_init_fc32(NULL, KC_FFT_N);
  fft_inited_ = true;
#else
  // No FFT backend available.
  // This demo requires esp-dsp. If you don't have it, add esp-dsp to your project.
  fft_inited_ = false;
#endif
}

static inline void smooth_bins_(float* v, int n, int k) {
  if (k <= 0 || n < (2 * k + 3)) return;
  // reflect-pad moving average
  // in-place via temp buffer
  static float tmp[KC_FEAT_D + 16];
  int pad = k;
  int N = n;
  // build padded buffer into tmp: [pad..pad+N-1]
  for (int i = 0; i < pad; i++) tmp[i] = v[pad - i];               // reflect
  for (int i = 0; i < N; i++) tmp[pad + i] = v[i];
  for (int i = 0; i < pad; i++) tmp[pad + N + i] = v[N - 2 - i];   // reflect

  // prefix sums
  static float csum[KC_FEAT_D + 16];
  int M = pad + N + pad;
  csum[0] = tmp[0];
  for (int i = 1; i < M; i++) csum[i] = csum[i - 1] + tmp[i];

  for (int i = 0; i < N; i++) {
    int a = i;           // start in padded
    int b = i + 2 * pad; // end
    float sum = csum[b] - (a > 0 ? csum[a - 1] : 0.0f);
    v[i] = sum / (float)(2 * pad);
  }
}

static inline void mean_center_l2_(float* v, int n) {
  float mu = 0.0f;
  for (int i = 0; i < n; i++) mu += v[i];
  mu /= (float)(n > 0 ? n : 1);
  for (int i = 0; i < n; i++) v[i] -= mu;

  double acc = 0.0;
  for (int i = 0; i < n; i++) acc += (double)(v[i] * v[i]);
  float norm = (float)sqrt(acc + 1e-12);
  if (norm < 1e-9f) norm = 1.0f;
  for (int i = 0; i < n; i++) v[i] /= norm;
}

void KeyClassifier::compute_feature_(const float* seg, int seg_n, float* out_feat) {
  // Build FFT input: first KC_FFT_N samples from seg, Hann windowed
  static float win[KC_FFT_N];
  static bool win_init = false;
  if (!win_init) { hann_(win, KC_FFT_N); win_init = true; }

  static float fft_buf[KC_FFT_N * 2]; // complex interleaved for esp-dsp (re, im, re, im ...)
  memset(fft_buf, 0, sizeof(fft_buf));

  int n = (seg_n < KC_FFT_N) ? seg_n : KC_FFT_N;
  for (int i = 0; i < n; i++) {
    float x = seg[i] * win[i];
    fft_buf[2 * i + 0] = x;
    fft_buf[2 * i + 1] = 0.0f;
  }

#if KC_USE_ESP_DSP
  dsps_fft2r_fc32(fft_buf, KC_FFT_N);
  dsps_bit_rev_fc32(fft_buf, KC_FFT_N);
  dsps_cplx2reC_fc32(fft_buf, KC_FFT_N);
#else
  // No FFT backend: output zeros (will reject).
  for (int i = 0; i < KC_FEAT_D; i++) out_feat[i] = 0.0f;
  return;
#endif

  // Convert to log magnitude and band-select into out_feat.
  // esp-dsp after dsps_cplx2reC_fc32 packs real/imag? For simplicity we treat fft_buf as complex again:
  // fft_buf[2*k]=re, fft_buf[2*k+1]=im for k=0..N-1 (common esp-dsp pattern).
  const float bin_hz = (float)fs_ / (float)KC_FFT_N;
  const int kmin = (int)ceilf(KC_FMIN_HZ / bin_hz);
  const int kmax = (int)floorf(KC_FMAX_HZ / bin_hz);
  int out_i = 0;

  for (int k = kmin; k <= kmax; k++) {
    float re = fft_buf[2 * k + 0];
    float im = fft_buf[2 * k + 1];
    float mag = sqrtf(re * re + im * im);
    float logm = logf(mag + 1e-8f);
    if (out_i < KC_FEAT_D) out_feat[out_i++] = logm;
  }
  // If FEAT_D mismatched for some reason, pad.
  while (out_i < KC_FEAT_D) out_feat[out_i++] = 0.0f;

  if (KC_SMOOTH_BINS > 0) smooth_bins_(out_feat, KC_FEAT_D, KC_SMOOTH_BINS);
  mean_center_l2_(out_feat, KC_FEAT_D);
}

static inline float dot_(const float* a, const float* b, int n) {
  double acc = 0.0;
  for (int i = 0; i < n; i++) acc += (double)a[i] * (double)b[i];
  return (float)acc;
}

KCResult KeyClassifier::classify_feat_(const float* feat) {
  KCResult r;
  r.accepted = false;
  r.label_index = -1;
  r.s1 = -1e9f;
  r.s2 = -1e9f;
  r.margin = 0.0f;

  // Score each label by max cosine over exemplars.
  for (int li = 0; li < KC_NUM_LABELS; li++) {
    float best = -1e9f;
    for (int ei = 0; ei < KC_NUM_EXEMPLARS; ei++) {
      const float* t = &KC_TEMPLATES[li][ei][0];
      float s = dot_(t, feat, KC_FEAT_D); // templates and feat are normalized
      if (s > best) best = s;
    }

    if (best > r.s1) {
      r.s2 = r.s1;
      r.s1 = best;
      r.label_index = li;
    } else if (best > r.s2) {
      r.s2 = best;
    }
  }

  r.margin = r.s1 - r.s2;
  r.accepted = (r.s1 >= t_abs) && (r.margin >= t_margin);
  if (!r.accepted) r.label_index = -1;
  return r;
}

bool KeyClassifier::process_i16(const int16_t* x, size_t n, KCResult* out) {
  if (!out) return false;
  *out = KCResult{false, -1, -1e9f, -1e9f, 0.0f};

  if (!fft_inited_) {
    // no FFT => always reject
    return false;
  }

  // Update ring buffer
  for (size_t i = 0; i < n; i++) {
    ring_[ring_w_] = x[i];
    ring_w_ = (ring_w_ + 1) % MAX_RING;
    if (ring_n_ < MAX_RING) ring_n_++;
  }

  // If capturing post-onset segment, fill capture buffer
  if (capturing_) {
    for (size_t i = 0; i < n && capture_have_ < capture_need_; i++) {
      capture_f_[capture_have_++] = (float)x[i] / 32768.0f;
    }
    if (capture_have_ >= capture_need_) {
      // compute feature + classify
      static float feat[KC_FEAT_D];
      compute_feature_(capture_f_, capture_need_, feat);
      *out = classify_feat_(feat);
      capturing_ = false;
      capture_have_ = 0;
      capture_need_ = 0;
      // enter refractory
      refractory_samples_left_ = (int)((refractory_ms / 1000.0f) * (float)fs_);
      return true;
    }
    return false;
  }

  // Refractory lockout
  if (refractory_samples_left_ > 0) {
    refractory_samples_left_ -= (int)n;
    if (refractory_samples_left_ < 0) refractory_samples_left_ = 0;
    return false;
  }

  // Onset detection using RMS frames over the incoming buffer
  // Process in hops of frame_hop
  size_t pos = 0;
  while (pos + (size_t)frame_hop <= n) {
    float rms = fast_rms_((const int16_t*)(x + pos), frame_hop);

    // update background bed
    if (bed_rms_ <= 0.0f) bed_rms_ = rms;
    bed_rms_ = (1.0f - rms_ema_alpha) * bed_rms_ + rms_ema_alpha * rms;

    float drms = rms - prev_rms_;
    prev_rms_ = rms;

    bool above = (rms > bed_rms_ * trig_ratio);
    bool attack = (drms > drms_min);

    if (above && attack) {
      consec_++;
      if (consec_ >= need_consec) {
        // Trigger: start capturing post-onset segment
        int seg_samples = (int)((KC_SEG_MS / 1000.0f) * (float)fs_);
        capture_need_ = seg_samples;
        capture_have_ = 0;
        capturing_ = true;

        // Capture starts immediately after detection within this buffer.
        // Fill with remaining samples after current pos.
        size_t start = pos;
        for (size_t j = start; j < n && capture_have_ < capture_need_; j++) {
          capture_f_[capture_have_++] = (float)x[j] / 32768.0f;
        }

        // Reset detector state
        consec_ = 0;

        // If we already collected full segment, classify immediately
        if (capture_have_ >= capture_need_) {
          static float feat[KC_FEAT_D];
          compute_feature_(capture_f_, capture_need_, feat);
          *out = classify_feat_(feat);
          capturing_ = false;
          capture_have_ = 0;
          capture_need_ = 0;
          refractory_samples_left_ = (int)((refractory_ms / 1000.0f) * (float)fs_);
          return true;
        }
        return false;
      }
    } else {
      consec_ = 0;
    }

    pos += (size_t)frame_hop;
  }

  return false;
}
