#include <Arduino.h>
#include <driver/i2s.h>
#include <math.h>
#include <string.h>

/* =========================================================
   ESP32 Piano Detector v3 — KEY MAPPING (no A440 mapping)

   - Uses per-key models learned from YOUR calibration logs
   - Rejects speech/ambient via flatness + harmonic consistency
   - Prints only stable key changes
   ========================================================= */

// -------------------- I2S (INMP441) --------------------
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

static const uint32_t DEBUG_PERIOD_MS = 1000;

// -------------------- Model (from your uploaded model_v3.json noise/gates + keys from your log) --------------------
struct NoiseModel {
  float rms_med, rms_mad;
  float spec_med, spec_mad;
  float flat_med, flat_mad;
};

struct Gates {
  float k_rms, k_spec;
  float flat_max;
  float hs_min;      // model had 0.0; we still enforce hits/cov strongly
  int   hits_min;
  float cov_min;

  // classifier behavior
  float k_pitch;     // allowed pitch deviation multiple * key MAD (cents)
  float w_shape;     // cosine weight
  float w_pitch;     // pitch weight
  float score_min;   // minimum score to accept any key
};

struct PitchCfg {
  float fmin, fmax;
  int   max_harm;
  float tol_low, tol_mid, tol_high;
};

struct StabilityCfg {
  uint32_t stable_ms;
  float cents_stable;
  uint32_t hold_ms;
};

// ---- Noise + gates from your model_v3.json ----
static const NoiseModel NOISE = {
  952.67f, 17.71f,
  39799888.0f, 1278344.0f,
  0.6027845f, 0.012128f
};

static Gates G = {
  8.0f, 8.0f,
  0.6842225f,
  0.0f,
  3,
  0.35f,

  // classifier defaults (conservative)
  6.0f,
  0.70f,
  0.30f,
  0.80f
};

static const PitchCfg PITCH = {
  20.0f, 5000.0f,
  10,
  25.0f, 18.0f, 12.0f
};

static const StabilityCfg STAB = { 300, 25.0f, 180 };

// IMPORTANT: tighten gates for real “no hallucinations”
static const int   HITS_MIN_RUNTIME = 4;     // stricter than model's 3
static const float COV_MIN_RUNTIME  = 0.55f; // stricter than model's 0.35

// -------------------- Per-key table (built from your piano_calibration.log) --------------------
struct KeyModel {
  int   key_idx;
  float f0_med_hz;
  float f0_mad_cents;
  float harm_vec_med[MAX_HARM];
};

// 85 keys found in your log (0..84). If you have more keys logged later, regenerate.
static const KeyModel gKeys[] = {
  { 0, 41.557250f, 472.016f, { 0.000000f, 0.000000f, 1.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f } },
  { 1, 41.083000f, 441.224f, { 0.000000f, 0.000000f, 1.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f } },
  { 2, 93.622222f, 336.481f, { 0.593036f, 0.406964f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f } },
  { 3, 93.545000f, 289.515f, { 0.604861f, 0.395139f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f } },
  { 4, 46.101667f, 360.100f, { 0.000000f, 0.000000f, 0.999413f, 0.000587f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f } },
  { 5, 44.537500f, 404.938f, { 0.000000f, 0.000000f, 1.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f } },
  { 6, 100.014000f, 328.375f, { 0.664416f, 0.335584f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f } },
  { 7, 99.405000f, 301.018f, { 0.669886f, 0.330114f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f } },
  { 8, 50.976750f, 340.550f, { 0.000000f, 0.000000f, 0.998690f, 0.001310f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f } },
  { 9, 51.103500f, 321.060f, { 0.000000f, 0.000000f, 0.997771f, 0.002229f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f } },
  { 10, 114.153000f, 326.466f, { 0.656070f, 0.343930f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f } },
  { 11, 114.585000f, 310.398f, { 0.649989f, 0.350011f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f } },
  { 12, 57.168000f, 328.409f, { 0.000000f, 0.000000f, 0.997534f, 0.002466f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f } },
  { 13, 56.736000f, 329.153f, { 0.000000f, 0.000000f, 0.997704f, 0.002296f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f } },
  { 14, 127.017000f, 323.204f, { 0.667155f, 0.332845f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f } },
  { 15, 126.198000f, 316.001f, { 0.669437f, 0.330563f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f } },
  { 16, 63.066000f, 314.459f, { 0.000000f, 0.000000f, 0.998566f, 0.001434f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f } },
  { 17, 62.884500f, 321.727f, { 0.000000f, 0.000000f, 0.998620f, 0.001380f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f } },
  { 18, 141.246000f, 307.905f, { 0.676078f, 0.323922f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f } },
  { 19, 141.003000f, 311.311f, { 0.676334f, 0.323666f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f } },
  { 20, 69.231000f, 317.552f, { 0.000000f, 0.000000f, 0.999193f, 0.000807f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f } },
  { 21, 69.154000f, 312.611f, { 0.000000f, 0.000000f, 0.999214f, 0.000786f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f } },
  { 22, 155.313000f, 311.690f, { 0.682132f, 0.317868f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f } },
  { 23, 155.214000f, 312.242f, { 0.679019f, 0.320981f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f } },
  { 24, 77.295000f, 305.586f, { 0.000000f, 0.000000f, 0.999403f, 0.000597f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f } },
  { 25, 77.218000f, 301.190f, { 0.000000f, 0.000000f, 0.999358f, 0.000642f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f } },
  { 26, 172.107000f, 310.574f, { 0.684509f, 0.315491f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f } },
  { 27, 171.984000f, 310.274f, { 0.687218f, 0.312782f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f } },
  { 28, 86.449500f, 301.536f, { 0.000000f, 0.000000f, 0.999126f, 0.000874f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f } },
  { 29, 86.586000f, 301.565f, { 0.000000f, 0.000000f, 0.999063f, 0.000937f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f } },
  { 30, 192.171000f, 309.471f, { 0.688806f, 0.311194f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f } },
  { 31, 191.829000f, 307.604f, { 0.686092f, 0.313908f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f } },
  { 32, 96.792000f, 297.806f, { 0.000000f, 0.000000f, 0.999062f, 0.000938f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f } },
  { 33, 96.669000f, 298.335f, { 0.000000f, 0.000000f, 0.999058f, 0.000942f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f } },
  { 34, 215.412000f, 304.485f, { 0.691711f, 0.308289f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f } },
  { 35, 215.265000f, 305.516f, { 0.689917f, 0.310083f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f } },
  { 36, 108.018000f, 295.350f, { 0.000000f, 0.000000f, 0.999139f, 0.000861f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f } },
  { 37, 107.736000f, 296.166f, { 0.000000f, 0.000000f, 0.999169f, 0.000831f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f } },
  { 38, 242.256000f, 303.029f, { 0.691076f, 0.308924f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f } },
  { 39, 236.874000f, 297.875f, { 0.694115f, 0.305885f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f } },
  { 40, 118.998000f, 292.640f, { 0.000000f, 0.000000f, 0.999106f, 0.000894f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f } },
  { 41, 118.872000f, 292.996f, { 0.000000f, 0.000000f, 0.999188f, 0.000812f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f } },
  { 42, 277.236000f, 296.993f, { 0.695668f, 0.304332f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f } },
  { 43, 277.236000f, 300.372f, { 0.694418f, 0.305582f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f } },
  { 44, 134.022000f, 290.064f, { 0.000000f, 0.000000f, 0.999139f, 0.000861f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f } },
  { 45, 133.884000f, 289.653f, { 0.000000f, 0.000000f, 0.999225f, 0.000775f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f } },
  { 46, 304.695000f, 293.358f, { 0.694820f, 0.305180f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f } },
  { 47, 304.695000f, 295.000f, { 0.693944f, 0.306056f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f } },
  { 48, 148.875000f, 285.702f, { 0.000000f, 0.000000f, 0.999244f, 0.000756f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f } },
  { 49, 148.743000f, 286.521f, { 0.000000f, 0.000000f, 0.999244f, 0.000756f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f } },
  { 50, 342.000000f, 292.873f, { 0.695480f, 0.304520f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f } },
  { 51, 342.000000f, 292.244f, { 0.696156f, 0.303844f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f } },
  { 52, 167.175000f, 282.933f, { 0.000000f, 0.000000f, 0.999233f, 0.000767f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f } },
  { 53, 167.175000f, 280.350f, { 0.000000f, 0.000000f, 0.999204f, 0.000796f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f } },
  { 54, 379.800000f, 289.906f, { 0.694626f, 0.305374f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f } },
  { 55, 379.800000f, 290.459f, { 0.693983f, 0.306017f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f } },
  { 56, 185.106000f, 279.379f, { 0.000000f, 0.000000f, 0.999223f, 0.000777f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f } },
  { 57, 185.106000f, 279.774f, { 0.000000f, 0.000000f, 0.999232f, 0.000768f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f } },
  { 58, 430.740000f, 290.013f, { 0.694913f, 0.305087f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f } },
  { 59, 430.740000f, 290.318f, { 0.695746f, 0.304254f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f } },
  { 60, 204.300000f, 276.292f, { 0.000000f, 0.000000f, 0.999216f, 0.000784f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f } },
  { 61, 204.300000f, 276.892f, { 0.000000f, 0.000000f, 0.999222f, 0.000778f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f } },
  { 62, 482.800000f, 287.334f, { 0.694768f, 0.305232f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f } },
  { 63, 482.800000f, 287.247f, { 0.695430f, 0.304570f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f } },
  { 64, 230.400000f, 274.062f, { 0.000000f, 0.000000f, 0.999219f, 0.000781f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f } },
  { 65, 230.400000f, 274.365f, { 0.000000f, 0.000000f, 0.999215f, 0.000785f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f } },
  { 66, 556.950000f, 286.134f, { 0.694857f, 0.305143f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f } },
  { 67, 556.950000f, 286.105f, { 0.695000f, 0.305000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f } },
  { 68, 258.525000f, 271.566f, { 0.000000f, 0.000000f, 0.999224f, 0.000776f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f } },
  { 69, 258.525000f, 271.690f, { 0.000000f, 0.000000f, 0.999223f, 0.000777f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f } },
  { 70, 644.100000f, 283.500f, { 0.694741f, 0.305259f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f } },
  { 71, 644.100000f, 283.901f, { 0.694633f, 0.305367f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f } },
  { 72, 288.300000f, 270.194f, { 0.000000f, 0.000000f, 0.999227f, 0.000773f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f } },
  { 73, 288.300000f, 269.979f, { 0.000000f, 0.000000f, 0.999225f, 0.000775f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f } },
  { 74, 742.050000f, 281.393f, { 0.694980f, 0.305020f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f } },
  { 75, 742.050000f, 281.330f, { 0.695220f, 0.304780f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f } },
  { 76, 323.400000f, 267.900f, { 0.000000f, 0.000000f, 0.999224f, 0.000776f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f } },
  { 77, 323.400000f, 268.080f, { 0.000000f, 0.000000f, 0.999226f, 0.000774f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f } },
  { 78, 855.225000f, 279.867f, { 0.695013f, 0.304987f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f } },
  { 79, 855.225000f, 279.759f, { 0.694833f, 0.305167f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f } },
  { 80, 365.400000f, 266.210f, { 0.000000f, 0.000000f, 0.999222f, 0.000778f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f } },
  { 81, 365.400000f, 266.420f, { 0.000000f, 0.000000f, 0.999222f, 0.000778f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f } },
  { 82, 964.200000f, 279.140f, { 0.694731f, 0.305269f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f } },
  { 83, 964.200000f, 279.334f, { 0.694917f, 0.305083f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f } },
  { 84, 413.775000f, 264.900f, { 0.000000f, 0.000000f, 0.999219f, 0.000781f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f } },
};
static const int gNumKeys = (int)(sizeof(gKeys) / sizeof(gKeys[0]));

// -------------------- FFT buffers --------------------
static float win[NFFT];
static float re[NFFT];
static float im[NFFT];
static float ring[NFFT];
static int ringPos = 0;

// -------------------- Rejection counters --------------------
struct RejCounters {
  uint32_t gate_energy = 0;
  uint32_t gate_flat = 0;
  uint32_t gate_harm = 0;
  uint32_t gate_class = 0;
  uint32_t gate_stable = 0;
  uint32_t ok = 0;
} RC;

// -------------------- Small helpers --------------------
static inline float clampf(float x, float a, float b) { return x < a ? a : (x > b ? b : x); }

static inline float centsDiff(float a_hz, float b_hz) {
  if (a_hz <= 0 || b_hz <= 0) return 1e9f;
  return fabsf(1200.0f * log2f(a_hz / b_hz));
}

static inline float tolForHz(float f0) {
  if (f0 < 140.0f) return PITCH.tol_low;
  if (f0 < 900.0f) return PITCH.tol_mid;
  return PITCH.tol_high;
}

static float cosineVec(const float a[MAX_HARM], const float b[MAX_HARM]) {
  float dot=0, na=0, nb=0;
  for (int i=0;i<MAX_HARM;i++) {
    dot += a[i]*b[i];
    na  += a[i]*a[i];
    nb  += b[i]*b[i];
  }
  na = sqrtf(na) + 1e-12f;
  nb = sqrtf(nb) + 1e-12f;
  return dot/(na*nb);
}

// -------------------- Minimal FFT --------------------
static void fftInit() {
  for (int i = 0; i < NFFT; i++) {
    win[i] = 0.5f - 0.5f * cosf(2.0f * M_PI * i / (NFFT - 1));
  }
}

static void fftRadix2(float* r, float* ii, int n) {
  for (int i = 1, j = 0; i < n; i++) {
    int bit = n >> 1;
    for (; j & bit; bit >>= 1) j ^= bit;
    j |= bit;
    if (i < j) {
      float tr = r[i]; r[i] = r[j]; r[j] = tr;
      float ti = ii[i]; ii[i] = ii[j]; ii[j] = ti;
    }
  }
  for (int len = 2; len <= n; len <<= 1) {
    float ang = -2.0f * M_PI / (float)len;
    float wlen_r = cosf(ang);
    float wlen_i = sinf(ang);
    for (int i = 0; i < n; i += len) {
      float wr = 1.0f, wi = 0.0f;
      for (int j = 0; j < len/2; j++) {
        int u = i + j;
        int v = i + j + len/2;
        float vr = r[v] * wr - ii[v] * wi;
        float vi = r[v] * wi + ii[v] * wr;
        r[v]  = r[u] - vr;
        ii[v] = ii[u] - vi;
        r[u]  += vr;
        ii[u] += vi;
        float nwr = wr * wlen_r - wi * wlen_i;
        float nwi = wr * wlen_i + wi * wlen_r;
        wr = nwr; wi = nwi;
      }
    }
  }
}

// -------------------- Peaks --------------------
struct Peak { float hz, mag; };

static void pickTopPeaks(const float* mag, int nBins, float binHz, float fmin, float fmax, Peak out[PEAKS]) {
  for (int i=0;i<PEAKS;i++) out[i] = {0,0};

  int iMin = max(1, (int)floorf(fmin / binHz));
  int iMax = min(nBins-2, (int)ceilf(fmax / binHz));

  for (int i=iMin; i<=iMax; i++) {
    if (!(mag[i] > mag[i-1] && mag[i] >= mag[i+1])) continue;

    // Parabolic interpolation on log-mag
    float m1 = logf(mag[i-1] + 1e-9f);
    float m0 = logf(mag[i]   + 1e-9f);
    float m2 = logf(mag[i+1] + 1e-9f);
    float denom = (m1 - 2*m0 + m2);
    float delta = 0.0f;
    if (fabsf(denom) > 1e-9f) delta = 0.5f * (m1 - m2) / denom;
    delta = clampf(delta, -0.5f, 0.5f);

    float f = (i + delta) * binHz;
    float m = mag[i];

    for (int k=0;k<PEAKS;k++) {
      if (m > out[k].mag) {
        for (int s=PEAKS-1; s>k; s--) out[s] = out[s-1];
        out[k] = {f, m};
        break;
      }
    }
  }
}

// -------------------- Harmonic scoring + F0 voting --------------------
static void harmonicScore(const Peak peaks[PEAKS], float f0, float tolCents, int maxHarm,
                          float& scoreNorm, int& hits, float& cov, float harmMags[MAX_HARM]) {
  for (int i=0;i<MAX_HARM;i++) harmMags[i]=0.0f;
  scoreNorm = 0.0f; hits = 0; cov = 0.0f;
  if (f0 <= 0) return;

  float totalTop = 0.0f;
  for (int i=0;i<PEAKS;i++) totalTop += peaks[i].mag;
  if (totalTop <= 0) totalTop = 1e-9f;

  bool used[PEAKS] = {false,false,false,false,false,false,false,false};
  float matched = 0.0f;
  float score = 0.0f;

  for (int n=1; n<=maxHarm; n++) {
    float target = f0 * n;
    int bestI = -1;
    float bestM = 0.0f;
    for (int i=0;i<PEAKS;i++) {
      if (used[i]) continue;
      if (peaks[i].hz <= 0 || peaks[i].mag <= 0) continue;
      if (centsDiff(peaks[i].hz, target) <= tolCents) {
        if (peaks[i].mag > bestM) { bestM = peaks[i].mag; bestI = i; }
      }
    }
    if (bestI >= 0) {
      used[bestI] = true;
      float w = 1.0f / (1.0f + 0.15f * (n - 1));
      score += w * bestM;
      matched += bestM;
      hits++;
      if (n-1 < MAX_HARM) harmMags[n-1] = bestM;
    }
  }

  cov = matched / totalTop;

  // Normalize score to remove “insane huge number” effect from raw FFT power.
  // This makes hs_min meaningful; but we mainly use hits/cov.
  scoreNorm = score / totalTop;
}

struct F0Res { float f0; float hsNorm; int hits; float cov; float harmVec[MAX_HARM]; };

static bool estimateF0(const Peak peaks[PEAKS], F0Res& out) {
  float bestF0 = 0.0f;
  float bestHS = -1.0f;
  int bestHits = -1;
  float bestCov = -1.0f;
  float tmpVec[MAX_HARM];

  for (int i=0;i<PEAKS;i++) {
    float pf = peaks[i].hz;
    if (pf <= 0) continue;
    for (int h=1; h<=PITCH.max_harm; h++) {
      float f0 = pf / (float)h;
      if (f0 < PITCH.fmin || f0 > PITCH.fmax) continue;

      float hs; int hits; float cov;
      harmonicScore(peaks, f0, tolForHz(f0), PITCH.max_harm, hs, hits, cov, tmpVec);

      if (hs > bestHS || (hs==bestHS && hits>bestHits) || (hs==bestHS && hits==bestHits && cov>bestCov)) {
        bestF0=f0; bestHS=hs; bestHits=hits; bestCov=cov;
        memcpy(out.harmVec, tmpVec, sizeof(tmpVec));
      }
    }
  }

  if (bestF0 <= 0) return false;
  out.f0 = bestF0; out.hsNorm = bestHS; out.hits = bestHits; out.cov = bestCov;
  return true;
}

static void normalizeHarmVec(float v[MAX_HARM]) {
  float s = 0.0f;
  for (int i=0;i<MAX_HARM;i++) s += v[i];
  if (s < 1e-12f) s = 1e-12f;
  for (int i=0;i<MAX_HARM;i++) v[i] /= s;
}

// -------------------- Key classification --------------------
static bool classifyKey(float f0, const float harmVec[MAX_HARM], int& outKey, float& outScore) {
  float bestScore = -1.0f;
  int bestKey = -999;

  for (int i=0;i<gNumKeys;i++) {
    const KeyModel& km = gKeys[i];

    // pitch distance in cents relative to this key’s learned median (NOT equal temperament)
    float dPitch = fabsf(1200.0f * log2f(f0 / (km.f0_med_hz + 1e-12f)));
    float lim = G.k_pitch * km.f0_mad_cents;
    if (dPitch > lim) continue;

    float cos = cosineVec(harmVec, km.harm_vec_med);

    // pitch term: gaussian in cents (sigma from MAD)
    float sigma = clampf(2.0f * km.f0_mad_cents, 8.0f, 80.0f);
    float pitchTerm = expf(-(dPitch*dPitch) / (2.0f*sigma*sigma));

    float score = G.w_shape * cos + G.w_pitch * pitchTerm;
    if (score > bestScore) { bestScore = score; bestKey = km.key_idx; }
  }

  if (bestScore < G.score_min) return false;
  outKey = bestKey;
  outScore = bestScore;
  return true;
}

// Evaluate f0, f0/2, 2*f0 to reduce octave flips.
static bool classifyWithOctaves(const F0Res& r, int& outKey, float& outScore, float& outUsedF0) {
  float v[MAX_HARM];
  memcpy(v, r.harmVec, sizeof(v));
  normalizeHarmVec(v);

  int k0; float s0; bool ok0 = classifyKey(r.f0, v, k0, s0);
  int kH; float sH; bool okH = classifyKey(r.f0 * 0.5f, v, kH, sH);
  int kD; float sD; bool okD = classifyKey(r.f0 * 2.0f, v, kD, sD);

  outScore = -1.0f; outKey = -999; outUsedF0 = r.f0;
  if (ok0 && s0 > outScore) { outScore=s0; outKey=k0; outUsedF0=r.f0; }
  if (okH && sH > outScore) { outScore=sH; outKey=kH; outUsedF0=r.f0*0.5f; }
  if (okD && sD > outScore) { outScore=sD; outKey=kD; outUsedF0=r.f0*2.0f; }

  return outScore >= G.score_min;
}

// -------------------- Feature computation (RMS, spec_total, flatness, peaks) --------------------
static void ringPush(float x) {
  ring[ringPos] = x;
  ringPos = (ringPos + 1) % NFFT;
}

static void ringReadWindow(float* out) {
  int idx = ringPos;
  for (int i=0;i<NFFT;i++) {
    out[i] = ring[idx];
    idx = (idx + 1) % NFFT;
  }
}

static void computeFeatures(float& rms_ac, float& spec_total, float& flatness, Peak peaks[PEAKS]) {
  float x[NFFT];
  ringReadWindow(x);

  double mean = 0.0;
  for (int i=0;i<NFFT;i++) mean += x[i];
  mean /= (double)NFFT;

  double acc = 0.0;
  for (int i=0;i<NFFT;i++) {
    float v = (float)(x[i] - (float)mean);
    acc += (double)v*(double)v;
    re[i] = v * win[i];
    im[i] = 0.0f;
  }
  rms_ac = sqrtf((float)(acc / (double)NFFT));

  fftRadix2(re, im, NFFT);

  const int nBins = NFFT/2;
  static float mag[nBins];

  spec_total = 0.0f;

  float binHz = (float)FS_HZ / (float)NFFT;
  int iMin = max(1, (int)floorf(20.0f / binHz));
  int iMax = min(nBins-1, (int)ceilf(4000.0f / binHz));

  double logSum = 0.0;
  double linSum = 0.0;
  int count = 0;

  for (int i=0;i<nBins;i++) {
    float pwr = re[i]*re[i] + im[i]*im[i];
    mag[i] = pwr + 1e-9f;
    spec_total += mag[i];

    if (i>=iMin && i<=iMax) {
      logSum += log((double)mag[i]);
      linSum += (double)mag[i];
      count++;
    }
  }

  double geo = exp(logSum / (double)max(1, count));
  double ari = linSum / (double)max(1, count);
  flatness = (float)(geo / (ari + 1e-12));

  pickTopPeaks(mag, nBins, binHz, 20.0f, 4000.0f, peaks);
}

// -------------------- I2S --------------------
static void setupI2S() {
  i2s_config_t cfg = {};
  cfg.mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_RX);
  cfg.sample_rate = FS_HZ;
  cfg.bits_per_sample = I2S_BITS_PER_SAMPLE_32BIT;
  cfg.channel_format = I2S_CHANNEL_FMT_ONLY_LEFT;
  cfg.communication_format = I2S_COMM_FORMAT_I2S;
  cfg.intr_alloc_flags = ESP_INTR_FLAG_LEVEL1;
  cfg.dma_buf_count = 8;
  cfg.dma_buf_len = HOP;

  i2s_pin_config_t pins = {};
  pins.bck_io_num = I2S_BCLK;
  pins.ws_io_num = I2S_LRCLK;
  pins.data_out_num = -1;
  pins.data_in_num = I2S_DIN;

  i2s_driver_install(I2S_PORT, &cfg, 0, nullptr);
  i2s_set_pin(I2S_PORT, &pins);
  i2s_zero_dma_buffer(I2S_PORT);
}

// -------------------- Stable note state --------------------
struct NoteState {
  bool active=false;
  int  cand_key=-999;
  float last_f0=0.0f;
  uint32_t since_ms=0;
  uint32_t last_good_ms=0;
  int last_printed=-999;
} S;

// -------------------- Setup/Loop --------------------
void setup() {
  Serial.begin(921600);
  delay(200);
  Serial.println();
  Serial.println("ESP32 Piano Detector v3 — KEY MAPPING (no ET mapping)");

  fftInit();
  setupI2S();

  for (int i=0;i<NFFT;i++) ring[i]=0.0f;

  Serial.printf("Loaded %d key models.\n", gNumKeys);
}

void loop() {
  static int32_t buf[HOP];
  size_t br=0;
  esp_err_t ok = i2s_read(I2S_PORT, buf, sizeof(buf), &br, portMAX_DELAY);
  if (ok != ESP_OK || br != sizeof(buf)) return;

  for (int i=0;i<HOP;i++) {
    float x = (float)(buf[i] >> 8);
    ringPush(x);
  }

  float rms_ac, spec_total, flatness;
  Peak peaks[PEAKS];
  computeFeatures(rms_ac, spec_total, flatness, peaks);

  uint32_t now = millis();

  // Energy gate
  bool energy_ok =
    (rms_ac    > (NOISE.rms_med  + G.k_rms  * NOISE.rms_mad)) &&
    (spec_total> (NOISE.spec_med + G.k_spec * NOISE.spec_mad));

  if (!energy_ok) {
    RC.gate_energy++;
    if (S.active && (now - S.last_good_ms) > STAB.hold_ms) {
      S.active=false; S.cand_key=-999;
    }
  } else {
    // Speech/ambient reject
    if (flatness > G.flat_max) {
      RC.gate_flat++;
    } else {
      // Harmonic F0 (tonal reject)
      F0Res r;
      if (!estimateF0(peaks, r)) {
        RC.gate_harm++;
      } else {
        // enforce hard harmonicity at runtime (stronger than model)
        if (r.hits < max(G.hits_min, HITS_MIN_RUNTIME) || r.cov < max(G.cov_min, COV_MIN_RUNTIME) || r.hsNorm < G.hs_min) {
          RC.gate_harm++;
        } else {
          int key; float score; float usedF0;
          if (!classifyWithOctaves(r, key, score, usedF0)) {
            RC.gate_class++;
          } else {
            // Stability: f0 stability in cents (relative to last f0)
            if (!S.active || key != S.cand_key) {
              S.active=true;
              S.cand_key=key;
              S.last_f0=usedF0;
              S.since_ms=now;
            } else {
              float dF = fabsf(1200.0f * log2f(usedF0 / (S.last_f0 + 1e-12f)));
              S.last_f0 = 0.85f*S.last_f0 + 0.15f*usedF0;

              if (dF > STAB.cents_stable) {
                S.since_ms = now;
                RC.gate_stable++;
              } else {
                if ((now - S.since_ms) >= STAB.stable_ms) {
                  if (key != S.last_printed) {
                    Serial.printf("NOTE key_idx=%d score=%.2f f0=%.2fHz hits=%d cov=%.2f flat=%.3f rms=%.0f\n",
                                  key, score, usedF0, r.hits, r.cov, flatness, rms_ac);
                    S.last_printed = key;
                  }
                  RC.ok++;
                } else {
                  RC.gate_stable++;
                }
              }
            }
            S.last_good_ms = now;
          }
        }
      }
    }
  }

  static uint32_t lastDbg=0;
  if (now - lastDbg >= DEBUG_PERIOD_MS) {
    lastDbg = now;
    Serial.printf("DBG rms=%.0f spec=%.0f flat=%.3f rej[E=%lu F=%lu H=%lu C=%lu S=%lu] ok=%lu\n",
      rms_ac, spec_total, flatness,
      (unsigned long)RC.gate_energy,
      (unsigned long)RC.gate_flat,
      (unsigned long)RC.gate_harm,
      (unsigned long)RC.gate_class,
      (unsigned long)RC.gate_stable,
      (unsigned long)RC.ok
    );
  }
}
