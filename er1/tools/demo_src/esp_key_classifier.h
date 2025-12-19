#pragma once
#include <stdint.h>
#include <stddef.h>

struct KCResult {
  bool accepted;
  int label_index;   // -1 if unknown/reject
  float s1;
  float s2;
  float margin;
};

// Standalone 12-key demo classifier:
// - attack-trigger onset detector
// - capture 200ms post-onset segment
// - single-FFT logmag feature
// - cosine max-over-exemplars match
class KeyClassifier {
public:
  // thresholds (from your sweep)
  float t_abs = 0.50f;
  float t_margin = 0.02f;

  // onset detector params (tune later if needed)
  float rms_ema_alpha = 0.01f;     // background bed EMA
  float trig_ratio = 2.0f;         // rms > bed * trig_ratio
  float drms_min = 0.0008f;        // attack slope threshold
  int need_consec = 2;             // consecutive frames
  int refractory_ms = 150;         // lockout after trigger
  int frame_hop = 256;             // samples per RMS frame (48k -> 5.3ms)

  void begin(int fs);

  // Feed streaming audio samples. Returns true if a classification event was produced.
  bool process_i16(const int16_t* x, size_t n, KCResult* out);

private:
  int fs_ = 48000;

  // ring buffer for PRE samples (to support future delta features; kept simple for demo)
  // For this demo we only need post-onset segment capture, but we keep a ring for stable triggering.
  static const int MAX_RING = 48000; // 1 second max ring
  int16_t ring_[MAX_RING];
  int ring_w_ = 0;
  int ring_n_ = 0;

  // capture state
  bool capturing_ = false;
  int capture_need_ = 0;
  int capture_have_ = 0;
  static const int MAX_CAPTURE = 48000; // enough for 200ms + padding
  float capture_f_[MAX_CAPTURE];

  // onset detector state
  float bed_rms_ = 0.0f;
  float prev_rms_ = 0.0f;
  int consec_ = 0;
  int refractory_samples_left_ = 0;

  // FFT workspace + feature buffers
  void init_fft_();
  void compute_feature_(const float* seg, int seg_n, float* out_feat);
  KCResult classify_feat_(const float* feat);

  // feature dimension and templates are taken from templates_generated.h
  bool fft_inited_ = false;
};
