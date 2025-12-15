#include <Arduino.h>
#include <driver/i2s.h>
#include <math.h>
#include <algorithm>

/* =========================================================
   HARDWARE: ESP32 + INMP441
   MODE:     Passive piano note detector (NO A440 mapping)
   ========================================================= */

// -------------------- I2S --------------------
static const i2s_port_t I2S_PORT = I2S_NUM_0;
static const int I2S_BCLK  = 26;
static const int I2S_LRCLK = 25;
static const int I2S_DIN   = 33;

// -------------------- DSP --------------------
static const int   FS_HZ = 22050;
static const int   NFFT  = 4096;
static const int   HOP   = 256;
static const int   PEAKS = 8;
static const int   MAX_HARM = 10;

// -------------------- Model (from model_v3.json) --------------------
struct NoiseModel {
  float rms_med;
  float rms_mad;
  float spec_med;
  float spec_mad;
  float flat_med;
  float flat_mad;
};

struct Gates {
  float k_rms;
  float k_spec;
  float flat_max;
  float hs_min;
  int   hits_min;
  float cov_min;
};

struct Stability {
  uint32_t stable_ms;
  float cents_stable;
  uint32_t hold_ms;
};

static const NoiseModel NOISE = {
  952.67f,
  17.71f,
  39799888.0f,
  1278344.0f,
  0.6027845f,
  0.012128f
};

static const Gates GATES = {
  8.0f,
  8.0f,
  0.6842225f,
  0.0f,
  3,
  0.35f
};

static const Stability STAB = {
  300,
  25.0f,
  180
};

// -------------------- FFT buffers --------------------
static float win[NFFT];
static float re[NFFT];
static float im[NFFT];
static float ring[NFFT];
static int   ringPos = 0;

// -------------------- Utils --------------------
static inline float cents(float a, float b) {
  if (a <= 0 || b <= 0) return 1e9f;
  return fabsf(1200.0f * log2f(a / b));
}

static inline float tolFor(float f0) {
  if (f0 < 140.0f) return 25.0f;
  if (f0 < 900.0f) return 18.0f;
  return 12.0f;
}

// -------------------- FFT --------------------
static void fftInit() {
  for (int i = 0; i < NFFT; i++)
    win[i] = 0.5f - 0.5f * cosf(2.0f * M_PI * i / (NFFT - 1));
}

static void fftRadix2(float* re, float* im, int n) {
  for (int i = 1, j = 0; i < n; i++) {
    int bit = n >> 1;
    for (; j & bit; bit >>= 1) j ^= bit;
    j |= bit;
    if (i < j) {
      swap(re[i], re[j]);
      swap(im[i], im[j]);
    }
  }
  for (int len = 2; len <= n; len <<= 1) {
    float ang = -2 * M_PI / len;
    float wr = cosf(ang), wi = sinf(ang);
    for (int i = 0; i < n; i += len) {
      float ur = 1, ui = 0;
      for (int j = 0; j < len / 2; j++) {
        int a = i + j;
        int b = a + len / 2;
        float tr = re[b] * ur - im[b] * ui;
        float ti = re[b] * ui + im[b] * ur;
        re[b] = re[a] - tr;
        im[b] = im[a] - ti;
        re[a] += tr;
        im[a] += ti;
        float nur = ur * wr - ui * wi;
        ui = ur * wi + ui * wr;
        ur = nur;
      }
    }
  }
}

// -------------------- Peak struct --------------------
struct Peak { float hz, mag; };

// -------------------- Harmonic F0 --------------------
static bool estimateF0(const Peak* p, float& outF0, float& outScore, int& outHits, float& outCov) {
  float bestScore = -1;
  float bestF0 = 0;

  for (int i = 0; i < PEAKS; i++) {
    for (int h = 1; h <= MAX_HARM; h++) {
      float f0 = p[i].hz / h;
      if (f0 < 20 || f0 > 5000) continue;

      float score = 0;
      int hits = 0;
      float used = 0, total = 0;
      bool usedP[PEAKS] = {};

      for (int k = 0; k < PEAKS; k++) total += p[k].mag;

      for (int n = 1; n <= MAX_HARM; n++) {
        float tgt = f0 * n;
        float tol = tolFor(f0);
        int bi = -1;
        float bm = 0;
        for (int j = 0; j < PEAKS; j++) {
          if (usedP[j]) continue;
          if (cents(p[j].hz, tgt) <= tol && p[j].mag > bm) {
            bm = p[j].mag;
            bi = j;
          }
        }
        if (bi >= 0) {
          usedP[bi] = true;
          score += bm / (1 + 0.15f * (n - 1));
          used += bm;
          hits++;
        }
      }

      float cov = used / (total + 1e-9f);
      if (score > bestScore) {
        bestScore = score;
        bestF0 = f0;
        outHits = hits;
        outCov = cov;
      }
    }
  }

  if (bestScore < GATES.hs_min || outHits < GATES.hits_min || outCov < GATES.cov_min)
    return false;

  outF0 = bestF0;
  outScore = bestScore;
  return true;
}

// -------------------- I2S --------------------
static void setupI2S() {
  i2s_config_t cfg = {};
  cfg.mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_RX);
  cfg.sample_rate = FS_HZ;
  cfg.bits_per_sample = I2S_BITS_PER_SAMPLE_32BIT;
  cfg.channel_format = I2S_CHANNEL_FMT_ONLY_LEFT;
  cfg.communication_format = I2S_COMM_FORMAT_I2S;
  cfg.dma_buf_count = 8;
  cfg.dma_buf_len = HOP;

  i2s_pin_config_t pins = {};
  pins.bck_io_num = I2S_BCLK;
  pins.ws_io_num = I2S_LRCLK;
  pins.data_in_num = I2S_DIN;

  i2s_driver_install(I2S_PORT, &cfg, 0, nullptr);
  i2s_set_pin(I2S_PORT, &pins);
  i2s_zero_dma_buffer(I2S_PORT);
}

// -------------------- Setup --------------------
void setup() {
  Serial.begin(921600);
  delay(200);
  Serial.println("\nESP32 Piano Detector v3 (keyless runtime)");

  fftInit();
  setupI2S();
  memset(ring, 0, sizeof(ring));
}

// -------------------- Loop --------------------
void loop() {
  static int32_t buf[HOP];
  size_t br;
  i2s_read(I2S_PORT, buf, sizeof(buf), &br, portMAX_DELAY);

  for (int i = 0; i < HOP; i++) {
    ring[ringPos] = (float)(buf[i] >> 8);
    ringPos = (ringPos + 1) % NFFT;
  }

  float mean = 0;
  for (int i = 0; i < NFFT; i++) mean += ring[i];
  mean /= NFFT;

  float rms = 0;
  for (int i = 0; i < NFFT; i++) {
    float v = ring[(ringPos + i) % NFFT] - mean;
    rms += v * v;
    re[i] = v * win[i];
    im[i] = 0;
  }
  rms = sqrtf(rms / NFFT);

  fftRadix2(re, im, NFFT);

  float spec = 0;
  Peak peaks[PEAKS] = {};
  for (int i = 1; i < NFFT / 2; i++) {
    float m = re[i]*re[i] + im[i]*im[i];
    spec += m;
    for (int k = 0; k < PEAKS; k++) {
      if (m > peaks[k].mag) {
        for (int s = PEAKS-1; s > k; s--) peaks[s] = peaks[s-1];
        peaks[k] = { (float)i * FS_HZ / NFFT, m };
        break;
      }
    }
  }

  if (rms < NOISE.rms_med + GATES.k_rms * NOISE.rms_mad) return;
  if (spec < NOISE.spec_med + GATES.k_spec * NOISE.spec_mad) return;

  float f0, score, cov;
  int hits;
  if (estimateF0(peaks, f0, score, hits, cov)) {
    static uint32_t last = 0;
    if (millis() - last > 300) {
      Serial.printf("NOTE f0=%.2f Hz  hits=%d  cov=%.2f  score=%.1f\n",
                    f0, hits, cov, score);
      last = millis();
    }
  }
}
