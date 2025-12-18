// record_piano_key_data.cpp (updated)
// - Dumps after EACH repetition (no dump-per-key batching)
// - Keeps ONLY the most recent rep file on SPIFFS to support `redo` without unbounded growth
//   * After a successful capture: prints BEGIN/END to Serial immediately
//   * Previous rep file is deleted right before writing the next rep
//   * `redo` deletes the last rep file (if present) and decrements rep counter
//   * When advancing to next key / stop / done: deletes the last rep file too
// - Fixes RAM pressure / linker .bss overflow by moving large buffers off .bss into heap
// - Avoids large stack allocations, yields inside long loops, keeps operator workflow

#include <Arduino.h>
#include <driver/i2s.h>
#include <SPIFFS.h>
#include <FS.h>
#include <math.h>
#include "esp_dsp.h"
#include "esp_heap_caps.h"

// ===================== VERSION =====================
static const int SKETCH_VER = 7;

// ===================== FIRMWARE IDENTITY (session) =====================
#ifndef ER1_GIT_REV
#define ER1_GIT_REV "unknown"
#endif
static const char* FW_BUILD    = __DATE__ " " __TIME__;
static const char* FW_GIT_REV  = ER1_GIT_REV;
static const char* FW_MIC_MODEL= "INMP441";
static const char* FW_WINDOW   = "hann";

// ===================== HARDWARE =====================
static const i2s_port_t I2S_PORT = I2S_NUM_0;
static const int PIN_BCLK  = 26;
static const int PIN_LRCLK = 25;
static const int PIN_DIN   = 33;

static const bool MIC_USE_LEFT = false;

// ===================== AUDIO / FFT =====================
static const int   FS_HZ   = 48000;
static const int   N_FFT   = 1024;
static const int   HOP     = 256;

static const float F_MIN   = 50.0f;
static const float F_MAX   = 3500.0f;

static const float MS_PER_FRAME = 1000.0f * (float)HOP / (float)FS_HZ;

// ====== template bands (deterministic) ======
static const int   N_BANDS32 = 32;
static const float B32_F_LO  = 80.0f;
static const float B32_F_HI  = 4000.0f;
static float b32_edges[N_BANDS32+1];

// ===================== PREPROCESS =====================
static const bool  ENABLE_DC_BLOCK = true;
static float dc_y = 0.0f;
static float dc_xprev = 0.0f;
static const float DC_R = 0.995f;

// ===================== FILE NAMING =====================
static const char* FILE_PREFIX = "/raw_"; // files stored in SPIFFS root

// ===================== BUFFERS =====================
static int32_t i2s_buf[HOP * 2];
static float   hop_f[HOP];

static float   x_win[N_FFT];
static float   hannw[N_FFT];

static float   fft_in[2 * N_FFT];
static float   mag[N_FFT/2];
static float   prev_mag[N_FFT/2];

// ===================== FEATURE ROW =====================
struct FeatRow {
  int   ms_from_event;
  float rms;
  float flux;
  float centroid_hz;
  float spread_hz;
  float rolloff85_hz;
  float flatness;
  float zcr;
  float band0, band1, band2, band3, band4, band5;
  float peak_hz;
  float peak_mag;
  float peak_ratio;
  float low_dom;
  float b32[N_BANDS32];
};

// ===================== CAPTURE BUFFERS (HEAP, not .bss) =====================
// NOTE: sized for typical pre(210ms)+post(400ms) at 5.33ms/frame ~ ~115 frames.
// Keep some headroom but not insane.
static const int MAX_CAPTURE_FRAMES = 192;

static FeatRow* cap_rows = nullptr;
static float*   cap_clip = nullptr;

// ===================== PRE-RING (HEAP) =====================
static FeatRow* pre_rows = nullptr;
static int pre_cap = 0;
static int pre_w = 0;
static bool pre_filled = false;

// ===================== COLLECT STATE =====================
enum CollectMode : uint8_t {
  MODE_KEYS_CLEAN = 0,
  MODE_KEYS_NOISE = 1,
};

static bool collecting = false;
static bool enter_trigger = false;
static bool wait_key_finalize = false;     // end-of-key: wait for Enter to advance so redo last rep still possible
static bool ignore_next_empty = false;     // prevents "start ... \n" from counting as capture
static bool last_was_cr = false;           // CRLF guard

static CollectMode collect_mode = MODE_KEYS_CLEAN;

static int  cur_midi = -1;         // C-based octave MIDI numbering (C4=60)
static int  end_midi = -1;
static int  reps_per_key = 10;
static int  rep_cur = 1;

static int  cfg_pre_ms  = 60;   // default
static int  cfg_post_ms = 400;  // default
static int  cfg_lead_ms = 150;  // default

static char cur_key_name[16] = {0};

// Track ONLY the most recent rep file (for redo + bounded SPIFFS usage)
static bool   last_rep_valid = false;
static String last_rep_path;

// ===================== NOTE NAMES (C-based octave system) =====================
static const char* NOTE_NAMES_SHARP_C[12] = {
  "C","C#","D","D#","E","F","F#","G","G#","A","A#","B"
};

// ===================== COOLDOWN =====================
// clean mode: wait until RMS returns near baseline (hard cap 5s)
// noise mode: fixed 3s
static const uint32_t CLEAN_COOLDOWN_HARDCAP_MS = 5000;
static const uint32_t NOISE_COOLDOWN_FIXED_MS   = 3000;
static const int QUIET_FRAMES_REQUIRED = 10; // consecutive frames near baseline (~53ms)

static bool cooldown_active = false;
static uint32_t cooldown_start_ms = 0;
static uint32_t cooldown_until_ms = 0;
static int quiet_frames = 0;

static bool prompt_pending = false;        // set after a capture; we only prompt when cooldown ends

static float last_rms = 0.0f;
static float baseline_mean = 0.0f;
static float baseline_std  = 0.0f;
static bool baseline_ready = false;

// ===================== HELPERS =====================
static int clamp_int(long v, int lo, int hi) {
  if (v < (long)lo) return lo;
  if (v > (long)hi) return hi;
  return (int)v;
}
static int imin(int a, int b) { return (a < b) ? a : b; }
static int imax(int a, int b) { return (a > b) ? a : b; }

static String normPath(const String& p){
  if (p.length() == 0) return "/";
  if (p[0] == '/') return p;
  return String("/") + p;
}
static bool endsWithCsv(const String& p){
  String t = p; t.toLowerCase();
  return t.endsWith(".csv");
}

static void die(const char* msg) {
  Serial.println(msg);
  while (true) delay(1000);
}

static void fsinfo() {
  size_t total = SPIFFS.totalBytes();
  size_t used  = SPIFFS.usedBytes();
  Serial.printf("SPIFFS: total=%u used=%u free=%u\n",
                (unsigned)total, (unsigned)used, (unsigned)(total - used));
}

static void purge_raw_files() {
  File root = SPIFFS.open("/");
  if (!root) { Serial.println("ERR: open / failed"); return; }
  if (!root.isDirectory()) { Serial.println("ERR: / is not a directory"); root.close(); return; }

  int removed = 0;
  File f = root.openNextFile();
  while (f) {
    String name = String(f.name());
    f.close();
    if (name.startsWith(String(FILE_PREFIX))) {
      SPIFFS.remove(name);
      removed++;
    }
    f = root.openNextFile();
  }
  root.close();
  Serial.printf("PURGE: removed %d files under %s*\n", removed, FILE_PREFIX);
  fsinfo();

  // Also clear last rep tracking
  last_rep_valid = false;
  last_rep_path = "";
}

static bool ensure_space_bytes(size_t needed, size_t reserve = 4096){
  size_t freeb = SPIFFS.totalBytes() - SPIFFS.usedBytes();
  if (freeb <= reserve) return false;
  return needed <= (freeb - reserve);
}

// ===================== DSP / FEATURES =====================
static void build_hann() {
  for (int i = 0; i < N_FFT; i++) {
    hannw[i] = 0.5f - 0.5f * cosf(2.0f * M_PI * (float)i / (float)(N_FFT - 1));
  }
}

static float compute_rms(const float* x, int n) {
  double acc = 0.0;
  for (int i = 0; i < n; i++) acc += (double)x[i] * (double)x[i];
  return (float)sqrt(acc / (double)n);
}

static float compute_zcr(const float* x, int n) {
  int zc = 0;
  float prev = x[0];
  for (int i=1;i<n;i++){
    float cur = x[i];
    if ((prev >= 0 && cur < 0) || (prev < 0 && cur >= 0)) zc++;
    prev = cur;
  }
  return (float)zc / (float)(n-1);
}

static void push_window(const float* hop) {
  memmove(x_win, x_win + HOP, sizeof(float) * (N_FFT - HOP));
  memcpy(x_win + (N_FFT - HOP), hop, sizeof(float) * HOP);
}

static float hz_of_bin(int k) { return (k * (float)FS_HZ) / (float)N_FFT; }

static void compute_mag() {
  for (int i = 0; i < N_FFT; i++) {
    fft_in[2*i + 0] = x_win[i] * hannw[i];
    fft_in[2*i + 1] = 0.0f;
  }
  dsps_fft2r_fc32(fft_in, N_FFT);
  dsps_bit_rev_fc32(fft_in, N_FFT);
  dsps_cplx2reC_fc32(fft_in, N_FFT);

  for (int k = 0; k < N_FFT/2; k++) {
    float re = fft_in[2*k + 0];
    float im = fft_in[2*k + 1];
    mag[k] = sqrtf(re*re + im*im);
  }
}

static void band_limits(int &k0, int &k1) {
  k0 = (int)floorf(F_MIN * N_FFT / (float)FS_HZ);
  k1 = (int)ceilf (F_MAX * N_FFT / (float)FS_HZ);
  k0 = imax(1, k0);
  k1 = imin((N_FFT/2)-1, k1);
}

static float compute_flux() {
  int k0, k1; band_limits(k0,k1);
  double pos = 0.0;
  double tot = 1e-9;
  for (int k = k0; k <= k1; k++) {
    float d = mag[k] - prev_mag[k];
    if (d > 0) pos += d;
    tot += mag[k];
  }
  return (float)(pos / tot);
}

static float compute_centroid_hz() {
  int k0, k1; band_limits(k0,k1);
  double num = 0.0, den = 1e-12;
  for (int k = k0; k <= k1; k++) {
    double m = mag[k];
    num += (double)hz_of_bin(k) * m;
    den += m;
  }
  return (float)(num / den);
}

static float compute_spread_hz(float centroid_hz) {
  int k0, k1; band_limits(k0,k1);
  double num = 0.0, den = 1e-12;
  for (int k = k0; k <= k1; k++) {
    double m = mag[k];
    double df = (double)hz_of_bin(k) - (double)centroid_hz;
    num += m * df * df;
    den += m;
  }
  return (float)sqrt(num / den);
}

static float compute_rolloff85_hz() {
  int k0, k1; band_limits(k0,k1);
  double tot = 1e-12;
  for (int k = k0; k <= k1; k++) tot += (double)mag[k];
  double target = 0.85 * tot;
  double acc = 0.0;
  for (int k = k0; k <= k1; k++) {
    acc += (double)mag[k];
    if (acc >= target) return hz_of_bin(k);
  }
  return hz_of_bin(k1);
}

static float compute_flatness() {
  int k0, k1; band_limits(k0,k1);
  double logsum = 0.0;
  double asum = 0.0;
  int n = 0;
  for (int k = k0; k <= k1; k++) {
    double v = (double)mag[k] + 1e-12;
    logsum += log(v);
    asum += v;
    n++;
  }
  double gmean = exp(logsum / (double)n);
  double amean = asum / (double)n;
  return (float)(gmean / (amean + 1e-12));
}

static void build_b32_edges(){
  float ratio = powf(B32_F_HI / B32_F_LO, 1.0f / (float)N_BANDS32);
  b32_edges[0] = B32_F_LO;
  for (int i=1;i<=N_BANDS32;i++){
    b32_edges[i] = b32_edges[i-1] * ratio;
  }
}

static void compute_bands_32(float out_b32[N_BANDS32]) {
  for (int i=0;i<N_BANDS32;i++) out_b32[i] = 0.0f;

  int kmin = (int)floorf((B32_F_LO * (float)N_FFT) / (float)FS_HZ);
  int kmax = (int)ceilf((B32_F_HI * (float)N_FFT) / (float)FS_HZ);
  if (kmin < 1) kmin = 1;
  if (kmax > (N_FFT/2 - 1)) kmax = (N_FFT/2 - 1);

  int bi = 0;
  float e_hi = b32_edges[1];

  for (int k=kmin;k<=kmax;k++){
    float f = (float)k * (float)FS_HZ / (float)N_FFT;
    while (bi < N_BANDS32-1 && f >= e_hi){
      bi++;
      e_hi = b32_edges[bi+1];
    }
    float v = mag[k];
    out_b32[bi] += log10f(v + 1e-12f);
  }
}

static void compute_bands_6(float &b0,float &b1,float &b2,float &b3,float &b4,float &b5) {
  const float edges[7] = {50,120,250,500,1000,2000,3500};
  float *outs[6] = {&b0,&b1,&b2,&b3,&b4,&b5};

  for (int i=0;i<6;i++) {
    int k0 = (int)floorf(edges[i]   * N_FFT / (float)FS_HZ);
    int k1 = (int)ceilf (edges[i+1] * N_FFT / (float)FS_HZ);
    k0 = imax(1, k0);
    k1 = imin((N_FFT/2)-1, k1);

    double acc = 0.0;
    for (int k=k0;k<=k1;k++) acc += (double)mag[k];
    *outs[i] = (float)log(acc + 1e-6);
  }

  float mean = (b0+b1+b2+b3+b4+b5)/6.0f;
  b0-=mean; b1-=mean; b2-=mean; b3-=mean; b4-=mean; b5-=mean;
}

static void compute_top_peak(float &peak_hz, float &peak_mag) {
  int k0, k1; band_limits(k0,k1);
  k0 = imax(2, k0);
  k1 = imin((N_FFT/2)-3, k1);

  int bestk = k0;
  float bestm = 0.0f;
  for (int k=k0;k<=k1;k++) {
    float m = mag[k];
    if (m >= mag[k-1] && m >= mag[k+1] && m > bestm) {
      bestm = m;
      bestk = k;
    }
  }
  peak_hz = hz_of_bin(bestk);
  peak_mag = bestm;
}

static float compute_peak_ratio(float peak_mag) {
  int k0, k1; band_limits(k0,k1);
  double sum = 1e-12;
  int n = 0;
  for (int k=k0;k<=k1;k++){ sum += mag[k]; n++; }
  float mean = (float)(sum / (double)n);
  return (mean > 1e-12f) ? (peak_mag / mean) : 0.0f;
}

static float low_dom_from_bands(float b0,float b1,float b4,float b5) {
  float low  = (b0 + b1) * 0.5f;
  float high = (b4 + b5) * 0.5f;
  float d = low - high;
  return 1.0f / (1.0f + expf(-d));
}

// ===================== I2S =====================
static void i2s_setup() {
  i2s_config_t cfg = {};
  cfg.mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_RX);
  cfg.sample_rate = FS_HZ;
  cfg.bits_per_sample = I2S_BITS_PER_SAMPLE_32BIT;
  cfg.channel_format = I2S_CHANNEL_FMT_RIGHT_LEFT;
  cfg.communication_format = I2S_COMM_FORMAT_I2S; // ok (deprecated warning only)
  cfg.intr_alloc_flags = ESP_INTR_FLAG_LEVEL1;
  cfg.dma_buf_count = 8;
  cfg.dma_buf_len = HOP;

  i2s_pin_config_t pins = {};
  pins.bck_io_num = PIN_BCLK;
  pins.ws_io_num  = PIN_LRCLK;
  pins.data_out_num = -1;
  pins.data_in_num  = PIN_DIN;

  if (i2s_driver_install(I2S_PORT, &cfg, 0, nullptr) != ESP_OK) die("ERR: i2s_driver_install");
  if (i2s_set_pin(I2S_PORT, &pins) != ESP_OK) die("ERR: i2s_set_pin");
  i2s_zero_dma_buffer(I2S_PORT);
}

// Clip tracking: count samples near full-scale (24-bit)
static bool read_hop(float* out_f, float& out_rms, float& out_zcr, float& out_clip_frac) {
  size_t bytes_read = 0;
  esp_err_t ok = i2s_read(I2S_PORT, (void*)i2s_buf, sizeof(i2s_buf), &bytes_read, portMAX_DELAY);
  if (ok != ESP_OK || bytes_read != sizeof(i2s_buf)) return false;

  const float scale = 1.0f / (float)(1 << 23);
  const int32_t CLIP_THRESH = (int32_t)(0.98f * 8388607.0f);

  int clipped = 0;

  for (int i = 0; i < HOP; i++) {
    int32_t L = i2s_buf[2*i + 0];
    int32_t R = i2s_buf[2*i + 1];
    int32_t s32 = MIC_USE_LEFT ? L : R;
    int32_t s24 = (s32 >> 8);

    if (s24 >= CLIP_THRESH || s24 <= -CLIP_THRESH) clipped++;

    float x = (float)s24 * scale;

    if (ENABLE_DC_BLOCK) {
      float y = x - dc_xprev + DC_R * dc_y;
      dc_xprev = x;
      dc_y = y;
      x = y;
    }
    out_f[i] = x;
  }

  out_rms = compute_rms(out_f, HOP);
  out_zcr = compute_zcr(out_f, HOP);
  out_clip_frac = (float)clipped / (float)HOP;
  return true;
}

// ===================== FEATURE BUILD + CSV =====================
static FeatRow build_row(int ms_from_event, float rms, float zcr, float flux) {
  FeatRow r = {};
  r.ms_from_event = ms_from_event;
  r.rms = rms;
  r.zcr = zcr;
  r.flux = flux;
  r.centroid_hz = compute_centroid_hz();
  r.spread_hz = compute_spread_hz(r.centroid_hz);
  r.rolloff85_hz = compute_rolloff85_hz();
  r.flatness = compute_flatness();
  compute_bands_6(r.band0,r.band1,r.band2,r.band3,r.band4,r.band5);
  compute_bands_32(r.b32);
  compute_top_peak(r.peak_hz, r.peak_mag);
  r.peak_ratio = compute_peak_ratio(r.peak_mag);
  r.low_dom = low_dom_from_bands(r.band0,r.band1,r.band4,r.band5);
  return r;
}

static void write_csv_header(File &out) {
  out.println("ms_from_event,rms,flux,centroid_hz,spread_hz,rolloff85_hz,flatness,zcr,band0,band1,band2,band3,band4,band5,peak_hz,peak_mag,peak_ratio,low_dom,b00,b01,b02,b03,b04,b05,b06,b07,b08,b09,b10,b11,b12,b13,b14,b15,b16,b17,b18,b19,b20,b21,b22,b23,b24,b25,b26,b27,b28,b29,b30,b31");
}

static void write_csv_row(File &out, const FeatRow &r) {
  out.printf("%d,%.6f,%.6f,%.2f,%.2f,%.2f,%.6f,%.6f,%.6f,%.6f,%.6f,%.6f,%.6f,%.6f,%.2f,%.6f,%.4f,%.4f",
    r.ms_from_event, r.rms, r.flux,
    r.centroid_hz, r.spread_hz, r.rolloff85_hz,
    r.flatness, r.zcr,
    r.band0,r.band1,r.band2,r.band3,r.band4,r.band5,
    r.peak_hz, r.peak_mag,
    r.peak_ratio, r.low_dom
  );
  for (int i=0;i<N_BANDS32;i++){
    out.printf(",%.6f", r.b32[i]);
  }
  out.print("\n");
}

// ===================== PRE-RING =====================
static int frames_for_ms(int ms){ return (int)ceilf((float)ms / MS_PER_FRAME); }
static int total_pre_ms(){ return cfg_pre_ms + cfg_lead_ms; }

static void ring_alloc_for_ms(int total_pre_ms_needed){
  // Pre typically ~40 frames. Keep bounded to avoid heap spikes.
  int want_frames = frames_for_ms(total_pre_ms_needed) + 8;
  want_frames = imax(24, imin(want_frames, 128));
  if (pre_rows && pre_cap == want_frames) return;

  if (pre_rows) { free(pre_rows); pre_rows = nullptr; }
  pre_rows = (FeatRow*)heap_caps_malloc(sizeof(FeatRow) * (size_t)want_frames, MALLOC_CAP_8BIT);
  if (!pre_rows) die("ERR: ring malloc failed");
  pre_cap = want_frames;
  pre_w = 0;
  pre_filled = false;
}

static void ring_push(const FeatRow& r){
  if (!pre_rows || pre_cap <= 0) return;
  pre_rows[pre_w] = r;
  pre_w = (pre_w + 1) % pre_cap;
  if (pre_w == 0) pre_filled = true;
}

static int ring_available(){ return pre_filled ? pre_cap : pre_w; }

// Earliest index in the ring buffer (oldest sample)
static int ring_index_of_earliest(){
  if (!pre_rows || pre_cap <= 0) return 0;
  if (pre_filled) return pre_w; // pre_w is next write => oldest is at pre_w
  return 0;
}

static FeatRow ring_peek_earliest(){
  FeatRow z = {};
  if (!pre_rows || pre_cap <= 0) return z;
  int avail = ring_available();
  if (avail <= 0) return z;
  int idx = ring_index_of_earliest();
  return pre_rows[idx];
}

// ===================== KEY PARSE (MIDI, C-based octave) =====================
static int note_name_to_index_c(const String& s) {
  String t = s; t.trim(); t.toUpperCase();
  if (t.length() < 1) return -1;

  char L = t[0];
  if (L < 'A' || L > 'G') return -1;

  String note; note += L;

  if (t.length() >= 2) {
    char a = t[1];
    if (a == '#') note += '#';
    else if (a == 'B') {
      if (L == 'D') note = "C#";
      else if (L == 'E') note = "D#";
      else if (L == 'G') note = "F#";
      else if (L == 'A') note = "G#";
      else if (L == 'B') note = "A#";
      else if (L == 'C') note = "B";
      else if (L == 'F') note = "E";
      else return -1;
    }
  }

  if      (note == "C")  return 0;
  else if (note == "C#") return 1;
  else if (note == "D")  return 2;
  else if (note == "D#") return 3;
  else if (note == "E")  return 4;
  else if (note == "F")  return 5;
  else if (note == "F#") return 6;
  else if (note == "G")  return 7;
  else if (note == "G#") return 8;
  else if (note == "A")  return 9;
  else if (note == "A#") return 10;
  else if (note == "B")  return 11;

  return -1;
}

static bool parse_key_to_midi(const String& key, int &out_midi) {
  String t = key; t.trim();
  if (t.length() < 2) return false;

  int n = t.length();
  int pos = n-1;
  while (pos >= 0 && isDigit((unsigned char)t[pos])) pos--;
  int octPos = pos+1;
  if (octPos <= 0 || octPos >= n) return false;

  String notePart = t.substring(0, octPos);
  String octPart  = t.substring(octPos);
  int octave = octPart.toInt();

  int noteIdx = note_name_to_index_c(notePart);
  if (noteIdx < 0) return false;

  out_midi = (octave + 1) * 12 + noteIdx;
  return true;
}

static void midi_to_key_name(int midi, char out[16]) {
  int note = midi % 12;
  int octave = (midi / 12) - 1;
  snprintf(out, 16, "%s%d", NOTE_NAMES_SHARP_C[note], octave);
}

// ===================== FILE OPS =====================
static bool dump_file_path(const String& p_in) {
  String p = normPath(p_in);
  File f = SPIFFS.open(p, FILE_READ);
  if (!f) { Serial.printf("ERR: cannot open %s\n", p.c_str()); return false; }
  Serial.printf("----- BEGIN %s (%u bytes) -----\n", p.c_str(), (unsigned)f.size());
  while (f.available()) Serial.write((uint8_t)f.read());
  f.close();
  Serial.printf("\n----- END %s -----\n", p.c_str());
  return true;
}

static void list_all() {
  File root = SPIFFS.open("/");
  if (!root || !root.isDirectory()) { Serial.println("ERR: root missing"); return; }
  Serial.println("Files:");
  File f = root.openNextFile();
  while (f) {
    String n = normPath(String(f.name()));
    Serial.printf("  %s  (%u bytes)\n", n.c_str(), (unsigned)f.size());
    f = root.openNextFile();
  }
}

static void clear_csv_files() {
  File root = SPIFFS.open("/");
  if (!root || !root.isDirectory()) { Serial.println("ERR: root missing"); return; }

  int removed = 0;
  File f = root.openNextFile();
  while (f) {
    String n = normPath(String(f.name()));
    f = root.openNextFile();
    if (endsWithCsv(n)) {
      if (SPIFFS.remove(n)) removed++;
    }
  }
  Serial.printf("OK: cleared %d .csv files\n", removed);
  fsinfo();

  last_rep_valid = false;
  last_rep_path = "";
}

// ===================== PROMPTS =====================
static void prompt_key(){
  Serial.printf("PRESS %s  rep %d/%d  then hit Enter\n", cur_key_name, rep_cur, reps_per_key);
}

// ===================== STREAM RESET =====================
static void reset_stream_state_for_collection(){
  ring_alloc_for_ms(total_pre_ms());
  pre_w = 0; pre_filled = false;
  memset(x_win, 0, sizeof(x_win));
  memset(prev_mag, 0, sizeof(prev_mag));
  dc_y = 0; dc_xprev = 0;

  float rms0=0, zcr0=0, clip0=0;
  for (int n=0; n<(N_FFT/HOP); n++){
    if (!read_hop(hop_f, rms0, zcr0, clip0)) { n--; continue; }
    push_window(hop_f);
  }
  compute_mag();
  for (int k=0;k<N_FFT/2;k++) prev_mag[k] = mag[k];

  // Clear last rep (bounded SPIFFS)
  if (last_rep_valid) {
    SPIFFS.remove(last_rep_path);
    last_rep_valid = false;
    last_rep_path = "";
  }
}

// ===================== BASELINE SILENCE =====================
static void measure_baseline_silence_5s(){
  const uint32_t dur_ms = 5000;
  const uint32_t t0 = millis();
  double sum = 0.0;
  double sum2 = 0.0;
  int n = 0;

  Serial.println("BASELINE: stay silent for 5 seconds...");

  float rms=0, zcr=0, clip=0;
  while (millis() - t0 < dur_ms) {
    if (!read_hop(hop_f, rms, zcr, clip)) continue;
    push_window(hop_f);
    sum += (double)rms;
    sum2 += (double)rms * (double)rms;
    n++;
    delay(0);
  }

  if (n <= 2) {
    baseline_mean = 0.0f;
    baseline_std = 0.0f;
    baseline_ready = false;
    Serial.println("BASELINE: failed (too few samples)");
    return;
  }

  double mean = sum / (double)n;
  double var = (sum2 / (double)n) - mean*mean;
  if (var < 0) var = 0;
  baseline_mean = (float)mean;
  baseline_std  = (float)sqrt(var);
  baseline_ready = true;

  Serial.printf("BASELINE OK: mean_rms=%.6f std=%.6f\n", baseline_mean, baseline_std);
}

// ===================== COOLDOWN HELPERS =====================
static float quiet_threshold_rms(){
  if (!baseline_ready) return 0.0f;
  float thr = baseline_mean + 3.0f * baseline_std;
  float thr2 = baseline_mean * 1.35f;
  if (thr < thr2) thr = thr2;
  return thr;
}

static void start_cooldown_after_capture(){
  cooldown_active = true;
  cooldown_start_ms = millis();
  quiet_frames = 0;

  if (collect_mode == MODE_KEYS_NOISE) {
    cooldown_until_ms = cooldown_start_ms + NOISE_COOLDOWN_FIXED_MS;
  } else {
    cooldown_until_ms = cooldown_start_ms + CLEAN_COOLDOWN_HARDCAP_MS;
  }

  if (collect_mode == MODE_KEYS_NOISE) {
    Serial.printf("COOLDOWN START (noise): fixed %ums\n", (unsigned)NOISE_COOLDOWN_FIXED_MS);
  } else {
    float thr = baseline_ready ? quiet_threshold_rms() : 0.0f;
    Serial.printf("COOLDOWN START (clean): wait rms<=%.6f for %d frames OR hardcap %ums\n",
                  thr, QUIET_FRAMES_REQUIRED, (unsigned)CLEAN_COOLDOWN_HARDCAP_MS);
  }
}

static void notify_ready_if_needed(){
  if (!prompt_pending) return;
  prompt_pending = false;

  if (wait_key_finalize) {
    Serial.println("READY: cooldown finished (end-of-key). Press Enter to advance, or type 'redo'.");
    return;
  }

  Serial.println("READY: you can press the next repetition now.");
  prompt_key();
}

static void tick_cooldown(){
  if (!cooldown_active) return;

  uint32_t now = millis();

  if (collect_mode == MODE_KEYS_NOISE) {
    if (now >= cooldown_until_ms) {
      cooldown_active = false;
      notify_ready_if_needed();
    }
    return;
  }

  if (baseline_ready) {
    float thr = quiet_threshold_rms();
    if (last_rms <= thr) quiet_frames++;
    else quiet_frames = 0;

    if (quiet_frames >= QUIET_FRAMES_REQUIRED) {
      cooldown_active = false;
      notify_ready_if_needed();
      return;
    }
  }

  if (now >= cooldown_until_ms) {
    cooldown_active = false;
    notify_ready_if_needed();
    return;
  }
}

// ===================== CAPTURE META (per capture) =====================
static int   meta_onset_idx = -1;
static int   meta_onset_ms  = -1;
static float meta_pre_rms_mean  = 0.0f;
static float meta_pre_rms_std   = 0.0f;
static float meta_pre_flux_mean = 0.0f;
static float meta_pre_flux_std  = 0.0f;
static float meta_onset_thr_rms = 0.0f;
static float meta_onset_thr_flux = 0.0f;
static float meta_clip_frac = 0.0f;
static float meta_snr_proxy = 0.0f;

// ===================== CAPTURE =====================
static bool capture_event_now(){
  ring_alloc_for_ms(total_pre_ms());

  const int WANT_PRE_FRAMES  = frames_for_ms(total_pre_ms());
  const int WANT_POST_FRAMES = frames_for_ms(cfg_post_ms);

  // Ensure capture fits our RAM buffers
  // We will store: missing(pre synth) + take(pre ring) + post frames
  // missing+take == WANT_PRE_FRAMES
  const int want_total = WANT_PRE_FRAMES + WANT_POST_FRAMES;
  if (want_total > MAX_CAPTURE_FRAMES) {
    Serial.printf("ERR: capture too long for MAX_CAPTURE_FRAMES=%d (need %d). Reduce pre/lead/post.\n",
                  MAX_CAPTURE_FRAMES, want_total);
    return false;
  }

  // Keep SPIFFS bounded: delete previous rep file BEFORE writing this rep
  if (last_rep_valid) {
    SPIFFS.remove(last_rep_path);
    last_rep_valid = false;
    last_rep_path = "";
  }

  // Space estimate (rough)
  size_t est = 900 + (size_t)(want_total + 16) * 320u;
  if (!ensure_space_bytes(est)) {
    Serial.printf("ERR: not enough SPIFFS space for capture (need ~%u bytes). Use purge/clear or reduce post/lead.\n",
                  (unsigned)est);
    fsinfo();
    return false;
  }

  uint32_t t = (uint32_t)millis();
  char fname[128];
  const char* mode_tag = (collect_mode == MODE_KEYS_NOISE) ? "noise" : "clean";
  snprintf(fname, sizeof(fname), "%s%s_%s_r%02d_%u.csv", FILE_PREFIX, mode_tag, cur_key_name, rep_cur, (unsigned)t);
  String path = normPath(String(fname));

  FeatRow* rows = cap_rows;
  float* row_clip = cap_clip;
  int nrows = 0;

  const int avail = ring_available();
  const int take = imin(WANT_PRE_FRAMES, avail);
  const int missing = WANT_PRE_FRAMES - take;

  FeatRow earliest = {};
  if (avail > 0) earliest = ring_peek_earliest();

  // synthetic missing frames
  for(int i=0;i<missing;i++){
    FeatRow r = earliest;
    r.ms_from_event = (int)lroundf((i - (WANT_PRE_FRAMES - 1)) * MS_PER_FRAME);
    rows[nrows] = r;
    row_clip[nrows] = 0.0f;
    nrows++;
  }

  // real pre frames from ring
  int start = ring_index_of_earliest();
  for(int i=0;i<take;i++){
    int idx = (start + i) % pre_cap;
    FeatRow r = pre_rows[idx];
    r.ms_from_event = (int)lroundf((missing + i - (WANT_PRE_FRAMES - 1)) * MS_PER_FRAME);
    rows[nrows] = r;
    row_clip[nrows] = 0.0f;
    nrows++;
  }

  // post frames (new)
  float rms=0, zcr=0, clip=0;
  for(int f=1; f<=WANT_POST_FRAMES; f++){
    if (!read_hop(hop_f, rms, zcr, clip)) { f--; continue; }
    push_window(hop_f);
    compute_mag();
    float flux = compute_flux();
    FeatRow r = build_row((int)lroundf(f * MS_PER_FRAME), rms, zcr, flux);
    rows[nrows] = r;
    row_clip[nrows] = clip;
    nrows++;

    ring_push(r);
    for (int k=0;k<N_FFT/2;k++) prev_mag[k] = mag[k];

    if ((f & 0x0F)==0) yield();
  }

  // Per-capture baseline from pre frames (ms_from_event < 0)
  double sr=0.0, sr2=0.0, sf=0.0, sf2=0.0;
  int nb=0;
  for (int i=0;i<nrows;i++){
    if (rows[i].ms_from_event < 0){
      float a = rows[i].rms;
      float b = rows[i].flux;
      sr += (double)a; sr2 += (double)a*(double)a;
      sf += (double)b; sf2 += (double)b*(double)b;
      nb++;
    }
  }
  if (nb <= 0){
    nb = imin(10, nrows);
    for (int i=0;i<nb;i++){
      float a = rows[i].rms;
      float b = rows[i].flux;
      sr += (double)a; sr2 += (double)a*(double)a;
      sf += (double)b; sf2 += (double)b*(double)b;
    }
  }

  double mr = sr / (double)nb;
  double vr = sr2 / (double)nb - mr*mr; if (vr < 0.0) vr = 0.0;
  double mf = sf / (double)nb;
  double vf = sf2 / (double)nb - mf*mf; if (vf < 0.0) vf = 0.0;

  meta_pre_rms_mean  = (float)mr;
  meta_pre_rms_std   = (float)sqrt(vr);
  meta_pre_flux_mean = (float)mf;
  meta_pre_flux_std  = (float)sqrt(vf);

  // Onset detection: flux + rms relative to per-capture baseline
  const float K_FLUX = 4.0f;
  const float K_RMS  = 3.0f;
  const int   NEED_CONSEC = 2;

  float thr_flux = meta_pre_flux_mean + K_FLUX * meta_pre_flux_std;
  float thr_rms  = meta_pre_rms_mean  + K_RMS  * meta_pre_rms_std;

  if (meta_pre_flux_std < 1e-9f) thr_flux = meta_pre_flux_mean * 3.0f;
  if (meta_pre_rms_std  < 1e-9f) thr_rms  = meta_pre_rms_mean  * 2.0f;

  meta_onset_thr_flux = thr_flux;
  meta_onset_thr_rms  = thr_rms;

  meta_onset_idx = -1;
  meta_onset_ms  = -1;

  int consec = 0;
  for (int i=0;i<nrows;i++){
    if (rows[i].ms_from_event < -cfg_lead_ms) continue;

    bool ok = (rows[i].flux >= thr_flux) && (rows[i].rms >= thr_rms);
    if (ok) consec++;
    else consec = 0;

    if (consec >= NEED_CONSEC){
      meta_onset_idx = i - (NEED_CONSEC - 1);
      meta_onset_ms  = rows[meta_onset_idx].ms_from_event;
      break;
    }
  }

  // clip fraction over post part
  double clip_sum = 0.0;
  int clip_n = 0;
  for (int i=0;i<nrows;i++){
    if (rows[i].ms_from_event >= 0){
      clip_sum += (double)row_clip[i];
      clip_n++;
    }
  }
  meta_clip_frac = (clip_n > 0) ? (float)(clip_sum / (double)clip_n) : 0.0f;

  // SNR proxy: inverse flatness in first 180ms
  double flat_sum = 0.0;
  int flat_n = 0;
  for (int i=0;i<nrows;i++){
    if (rows[i].ms_from_event >= 0 && rows[i].ms_from_event <= 180){
      flat_sum += (double)rows[i].flatness;
      flat_n++;
    }
  }
  float flat_mean = (flat_n > 0) ? (float)(flat_sum / (double)flat_n) : 1.0f;
  meta_snr_proxy = 1.0f / (flat_mean + 1e-6f);

  // Write file
  File out = SPIFFS.open(path, FILE_WRITE);
  if (!out){
    Serial.printf("ERR: cannot create csv %s\n", path.c_str());
    fsinfo();
    return false;
  }

  out.printf("#FW,ver=%d,build=%s,git_rev=%s,mic_model=%s,mic_channel=%s,window=%s\n",
             SKETCH_VER, FW_BUILD, FW_GIT_REV, FW_MIC_MODEL, (MIC_USE_LEFT ? "L":"R"), FW_WINDOW);

  out.printf("#META,type=keypress,mode=%s,key=%s,rep=%d,fs_hz=%d,hop=%d,n_fft=%d,fmin=%.1f,fmax=%.1f,pre_ms=%d,lead_ms=%d,post_ms=%d,stored_pre_ms=%d,ms_per_frame=%.5f,t_ms=%u\n",
             mode_tag, cur_key_name, rep_cur, FS_HZ, HOP, N_FFT, F_MIN, F_MAX,
             cfg_pre_ms, cfg_lead_ms, cfg_post_ms, total_pre_ms(), MS_PER_FRAME, (unsigned)t);

  out.printf("#DIAG,onset_ms=%d,onset_idx=%d,baseline_rms_mean=%.6f,baseline_rms_std=%.6f,baseline_flux_mean=%.6f,baseline_flux_std=%.6f,onset_thr_rms=%.6f,onset_thr_flux=%.6f,clip_frac=%.6f,snr_proxy=%.6f\n",
             meta_onset_ms, meta_onset_idx,
             meta_pre_rms_mean, meta_pre_rms_std,
             meta_pre_flux_mean, meta_pre_flux_std,
             meta_onset_thr_rms, meta_onset_thr_flux,
             meta_clip_frac, meta_snr_proxy);

  write_csv_header(out);
  for (int i=0;i<nrows;i++){
    if ((i & 0x0F)==0) yield();
    write_csv_row(out, rows[i]);
  }
  out.flush();
  out.close();

  // Dump immediately after each repetition (BEGIN/END boundaries preserved)
  dump_file_path(path);

  // Keep this rep on SPIFFS ONLY until next rep (so redo can delete it)
  last_rep_valid = true;
  last_rep_path = path;

  Serial.printf("OK: captured %s  (rows=%d onset_ms=%d clip=%.4f snr=%.3f)\n",
                path.c_str(), nrows, meta_onset_ms, meta_clip_frac, meta_snr_proxy);
  return true;
}

static void delete_last_rep_if_any(){
  if (!last_rep_valid) return;
  SPIFFS.remove(last_rep_path);
  last_rep_valid = false;
  last_rep_path = "";
}

static void finalize_key_and_advance(){
  // We already dumped each rep, so nothing to dump here.
  // Delete last rep file to keep SPIFFS bounded when moving on.
  delete_last_rep_if_any();

  cur_midi++;
  rep_cur = 1;

  if (cur_midi > end_midi){
    collecting = false;
    wait_key_finalize = false;
    prompt_pending = false;
    cooldown_active = false;
    Serial.println("COLLECT DONE");
    return;
  }

  midi_to_key_name(cur_midi, cur_key_name);
  Serial.printf("NEXT: %s\n", cur_key_name);
  prompt_key();
}

static void advance_after_capture(){
  rep_cur++;
  if (rep_cur > reps_per_key){
    wait_key_finalize = true;
    prompt_pending = false;
    Serial.printf("KEY DONE: %s (%d reps). Press Enter to advance, or type 'redo'.\n", cur_key_name, reps_per_key);
    return;
  }
  prompt_pending = true;
}

// ===================== REDO =====================
static void do_redo(){
  if (!collecting) { Serial.println("ERR: not collecting"); return; }

  if (!last_rep_valid) {
    Serial.println("ERR: nothing to redo (no last rep file present)");
    return;
  }

  if (SPIFFS.remove(last_rep_path)) {
    Serial.printf("REDO: deleted %s\n", last_rep_path.c_str());
  } else {
    Serial.printf("REDO: failed to delete %s\n", last_rep_path.c_str());
  }
  last_rep_valid = false;
  last_rep_path = "";

  prompt_pending = false;
  cooldown_active = false;

  if (wait_key_finalize) {
    wait_key_finalize = false;
    rep_cur = reps_per_key; // redo last rep of key
  } else {
    rep_cur = imax(1, rep_cur - 1);
  }

  prompt_key();
}

// ===================== NOISE/SILENCE SEGMENTS =====================
static void record_segment_csv(const char* label, int secs){
  secs = imax(1, secs);

  const int total_frames = frames_for_ms(secs * 1000);
  size_t est = 700 + (size_t)(total_frames + 8) * 260u;

  if (!ensure_space_bytes(est)) {
    Serial.printf("ERR: not enough SPIFFS space for %s %ds (need~%u bytes). Use purge/clear or record fewer seconds.\n",
                  label, secs, (unsigned)est);
    fsinfo();
    return;
  }

  uint32_t t = (uint32_t)millis();
  char fname[128];
  snprintf(fname, sizeof(fname), "%sseg_%s_%ds_%u.csv", FILE_PREFIX, label, secs, (unsigned)t);
  String path = normPath(String(fname));

  File out = SPIFFS.open(path, FILE_WRITE);
  if (!out){
    Serial.printf("ERR: cannot create segment csv %s\n", path.c_str());
    fsinfo();
    return;
  }

  out.printf("#FW,ver=%d,build=%s,git_rev=%s,mic_model=%s,mic_channel=%s,window=%s\n",
             SKETCH_VER, FW_BUILD, FW_GIT_REV, FW_MIC_MODEL, (MIC_USE_LEFT ? "L":"R"), FW_WINDOW);
  out.printf("#META,type=segment,seg=%s,secs=%d,fs_hz=%d,hop=%d,n_fft=%d,fmin=%.1f,fmax=%.1f,ms_per_frame=%.5f,t_ms=%u\n",
             label, secs, FS_HZ, HOP, N_FFT, F_MIN, F_MAX, MS_PER_FRAME, (unsigned)t);

  write_csv_header(out);

  Serial.printf("SEGMENT %s: recording %d sec (%d frames)\n", label, secs, total_frames);

  float rms=0, zcr=0, clip=0;
  for (int n=0; n<(N_FFT/HOP); n++){
    if (!read_hop(hop_f, rms, zcr, clip)) { n--; continue; }
    push_window(hop_f);
  }
  compute_mag();
  for (int k=0;k<N_FFT/2;k++) prev_mag[k] = mag[k];

  double clip_sum = 0.0;
  int clip_n = 0;

  for(int f=0; f<total_frames; f++){
    if ((f & 0x0F)==0) yield();
    if (!read_hop(hop_f, rms, zcr, clip)) { f--; continue; }
    clip_sum += (double)clip; clip_n++;
    push_window(hop_f);
    compute_mag();
    float flux = compute_flux();
    int ms = (int)lroundf(f * MS_PER_FRAME);
    FeatRow r = build_row(ms, rms, zcr, flux);
    write_csv_row(out, r);
    for (int k=0;k<N_FFT/2;k++) prev_mag[k] = mag[k];
  }

  float seg_clip = (clip_n > 0) ? (float)(clip_sum / (double)clip_n) : 0.0f;
  out.printf("#DIAG,clip_frac=%.6f\n", seg_clip);

  out.flush();
  out.close();
  Serial.printf("SEGMENT OK: %s\n", path.c_str());

  dump_file_path(path);
  SPIFFS.remove(path);
}

// ===================== CONSOLE =====================
static String cmdline;
static const int CMD_MAX = 200;

static void print_help() {
  Serial.println("Commands:");
  Serial.println("  purge                      delete all recorded /raw_* files from SPIFFS");
  Serial.println("  help");
  Serial.println("  fsinfo");
  Serial.println("  list");
  Serial.println("  clear              (deletes ALL .csv files)");
  Serial.println("  dump <path>        (e.g. /raw_clean_C4_r01_123.csv)");
  Serial.println("  pre <ms>           (default 60; stored pre is pre+lead)");
  Serial.println("  post <ms>          (default 400)");
  Serial.println("  lead <ms>          (default 150)");
  Serial.println("  end <key>          (default A7)");
  Serial.println("  start <key> <reps>           (collect keys clean; baseline 5s; adaptive cooldown until quiet or 5s cap)");
  Serial.println("  start noise <key> <reps>     (collect keys tagged noise; fixed cooldown 3s)");
  Serial.println("  stop");
  Serial.println("  redo               (delete last captured rep for current key)");
  Serial.println("  silence [secs]     (segment; dumps immediately)");
  Serial.println("  noise [secs]       (segment; dumps immediately)");
  Serial.println("");
  Serial.println("Collecting: press key + hit Enter (empty line) to capture.");
  Serial.println("After EACH capture: CSV is dumped immediately (BEGIN/END).");
  Serial.println("To keep SPIFFS bounded: only the most recent rep file is kept (for redo) until next capture/advance.");
}

static void prompt() { Serial.print("> "); }

static void start_collection(const String& startKey, int reps, CollectMode mode){
  int s=-1;
  if (!parse_key_to_midi(startKey, s)) { Serial.println("ERR: start key format (e.g. C4, F#5, Bb3)"); return; }

  collecting = true;
  enter_trigger = false;
  wait_key_finalize = false;
  ignore_next_empty = true;
  collect_mode = mode;

  cooldown_active = false;
  quiet_frames = 0;
  prompt_pending = false;

  // Clear last rep file at the start of a run
  delete_last_rep_if_any();

  cur_midi = s;
  reps_per_key = clamp_int(reps, 1, 64);
  rep_cur = 1;

  midi_to_key_name(cur_midi, cur_key_name);
  Serial.printf("START: mode=%s from %s -> end reps/key=%d pre=%d lead=%d post=%d (stored_pre=%d)\n",
                (mode==MODE_KEYS_NOISE ? "noise":"clean"),
                cur_key_name, reps_per_key, cfg_pre_ms, cfg_lead_ms, cfg_post_ms, total_pre_ms());

  reset_stream_state_for_collection();

  if (mode == MODE_KEYS_CLEAN) {
    baseline_ready = false;
    measure_baseline_silence_5s();
  } else {
    baseline_ready = false;
  }

  prompt_key();
}

static void process_line(String line) {
  line.trim();

  if (collecting && line.length() == 0) {
    if (ignore_next_empty) { ignore_next_empty = false; return; }
    enter_trigger = true;
    return;
  }

  if (line.length() == 0) return;

  String low = line; low.toLowerCase();
  int sp = low.indexOf(' ');
  String cmd = (sp < 0) ? low : low.substring(0, sp);
  String arg = (sp < 0) ? ""  : line.substring(sp + 1);
  arg.trim();

  if (cmd == "help" || cmd == "?") {
    print_help();
  } else if (cmd == "fsinfo") {
    fsinfo();
  } else if (cmd == "list") {
    list_all();
  } else if (cmd == "clear") {
    clear_csv_files();
  } else if (cmd == "purge") {
    purge_raw_files();
  } else if (cmd == "dump") {
    if (arg.length() == 0) { Serial.println("Usage: dump <path>"); return; }
    dump_file_path(arg);
  } else if (cmd == "pre") {
    cfg_pre_ms = clamp_int(arg.toInt(), 0, 5000);
    ring_alloc_for_ms(total_pre_ms());
    Serial.printf("OK: pre=%dms (stored pre=%dms)\n", cfg_pre_ms, total_pre_ms());
  } else if (cmd == "post") {
    cfg_post_ms = clamp_int(arg.toInt(), 20, 10000);
    Serial.printf("OK: post=%dms\n", cfg_post_ms);
  } else if (cmd == "lead") {
    cfg_lead_ms = clamp_int(arg.toInt(), 0, 5000);
    ring_alloc_for_ms(total_pre_ms());
    Serial.printf("OK: lead=%dms (stored pre=%dms)\n", cfg_lead_ms, total_pre_ms());
  } else if (cmd == "end") {
    int idx=-1;
    if (!parse_key_to_midi(arg, idx)) { Serial.println("ERR: end key format (e.g. A7, C6, F#5, Bb3)"); return; }
    end_midi = idx;
    char tmp[16]; midi_to_key_name(end_midi, tmp);
    Serial.printf("OK: end=%s\n", tmp);
  } else if (cmd == "start") {
    if (arg.length() == 0) { Serial.println("Usage: start <key> <reps>  OR  start noise <key> <reps>"); return; }

    int sp1 = arg.indexOf(' ');
    if (sp1 < 0) { Serial.println("Usage: start <key> <reps>  OR  start noise <key> <reps>"); return; }
    String a1 = arg.substring(0, sp1); a1.trim();
    String rest = arg.substring(sp1+1); rest.trim();

    if (a1.equalsIgnoreCase("noise")) {
      int sp2 = rest.indexOf(' ');
      if (sp2 < 0) { Serial.println("Usage: start noise <key> <reps>"); return; }
      String k = rest.substring(0, sp2); k.trim();
      String r = rest.substring(sp2+1); r.trim();
      start_collection(k, r.toInt(), MODE_KEYS_NOISE);
    } else {
      String k = a1;
      String r = rest;
      start_collection(k, r.toInt(), MODE_KEYS_CLEAN);
    }
  } else if (cmd == "stop") {
    collecting = false;
    wait_key_finalize = false;
    cooldown_active = false;
    prompt_pending = false;
    delete_last_rep_if_any();
    Serial.println("STOP");
  } else if (cmd == "redo") {
    do_redo();
  } else if (cmd == "silence") {
    int secs = (arg.length() ? clamp_int(arg.toInt(), 1, 600) : 10);
    record_segment_csv("silence", secs);
  } else if (cmd == "noise") {
    int secs = (arg.length() ? clamp_int(arg.toInt(), 1, 600) : 10);
    record_segment_csv("noise", secs);
  } else {
    Serial.println("Unknown command. Type: help");
  }
}

static void handle_console() {
  while (Serial.available()) {
    char c = (char)Serial.read();

    if (c == '\n' && last_was_cr) {
      last_was_cr = false;
      continue;
    }

    if (c == '\r' || c == '\n') {
      last_was_cr = (c == '\r');
      Serial.println();
      String line = cmdline;
      cmdline = "";
      process_line(line);
      prompt();
      continue;
    }

    last_was_cr = false;

    if (c == 8 || c == 127) {
      if (cmdline.length() > 0) {
        cmdline.remove(cmdline.length() - 1);
        Serial.print("\b \b");
      }
      continue;
    }

    if ((uint8_t)c < 32) continue;

    if (cmdline.length() < CMD_MAX) {
      cmdline += c;
      Serial.print(c);
    }
  }
}

// ===================== LOOP: continuous ring update =====================
static void tick_audio_features(){
  float rms=0, zcr=0, clip=0;
  if (!read_hop(hop_f, rms, zcr, clip)) return;
  last_rms = rms;

  push_window(hop_f);
  compute_mag();
  float flux = compute_flux();
  FeatRow r = build_row(0, rms, zcr, flux);
  ring_push(r);
  for (int k=0;k<N_FFT/2;k++) prev_mag[k] = mag[k];

  if (collecting) tick_cooldown();
}

// ===================== SETUP / LOOP =====================
void setup() {
  Serial.begin(115200);
  delay(200);

  if (!SPIFFS.begin(true)) die("ERR: SPIFFS.begin failed");

  esp_err_t e = dsps_fft2r_init_fc32(NULL, CONFIG_DSP_MAX_FFT_SIZE);
  if (e != ESP_OK) die("ERR: dsps_fft2r_init");

  build_hann();
  build_b32_edges();
  i2s_setup();

  // Allocate capture buffers on heap (reduces .bss / linker pressure)
  cap_rows = (FeatRow*)heap_caps_malloc(sizeof(FeatRow) * (size_t)MAX_CAPTURE_FRAMES, MALLOC_CAP_8BIT);
  cap_clip = (float*)heap_caps_malloc(sizeof(float) * (size_t)MAX_CAPTURE_FRAMES, MALLOC_CAP_8BIT);
  if (!cap_rows || !cap_clip) die("ERR: cap buffer malloc failed");

  int eidx=-1;
  if (!parse_key_to_midi("A7", eidx)) die("ERR: default end parse (A7)");
  end_midi = eidx;

  ring_alloc_for_ms(total_pre_ms());

  Serial.println();
  Serial.println("ESP32+INMP441 Continuous Features -> Enter-to-Capture CSV (dump-per-rep, bounded SPIFFS)");
  Serial.printf("#SESSION,ver=%d,build=%s,git_rev=%s,mic_model=%s,mic_channel=%s,window=%s\n",
                SKETCH_VER, FW_BUILD, FW_GIT_REV, FW_MIC_MODEL, (MIC_USE_LEFT ? "L":"R"), FW_WINDOW);
  Serial.printf("FS=%d N_FFT=%d HOP=%d (%.2f ms/frame)\n", FS_HZ, N_FFT, HOP, MS_PER_FRAME);
  Serial.printf("Defaults: pre=%dms lead=%dms (stored pre=%dms) post=%dms end=A7\n",
                cfg_pre_ms, cfg_lead_ms, total_pre_ms(), cfg_post_ms);
  fsinfo();

  print_help();
  prompt();
}

void loop() {
  handle_console();
  tick_audio_features();

  if (!collecting) {
    delay(1);
    return;
  }

  if (enter_trigger){
    enter_trigger = false;

    if (wait_key_finalize) {
      wait_key_finalize = false;
      finalize_key_and_advance();
      delay(1);
      return;
    }

    if (cooldown_active) {
      uint32_t now = millis();
      uint32_t rem = (now < cooldown_until_ms) ? (cooldown_until_ms - now) : 0;

      if (collect_mode == MODE_KEYS_NOISE) {
        Serial.printf("COOLDOWN (noise fixed): %ums remaining\n", (unsigned)rem);
      } else {
        float thr = baseline_ready ? quiet_threshold_rms() : 0.0f;
        Serial.printf("COOLDOWN (clean): rms=%.6f thr=%.6f rem<=%ums\n",
                      last_rms, thr, (unsigned)rem);
      }
      delay(1);
      return;
    }

    bool ok = capture_event_now();
    if (ok) {
      start_cooldown_after_capture();
      advance_after_capture();
    } else {
      Serial.println("REC FAIL (retry same rep)");
      prompt_key();
    }
  }

  delay(1);
}
