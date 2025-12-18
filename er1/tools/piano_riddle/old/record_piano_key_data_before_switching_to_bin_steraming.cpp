#include <Arduino.h>
#include <driver/i2s.h>
#include <SPIFFS.h>
#include <FS.h>
#include <math.h>
#include "esp_dsp.h"
#include "esp_heap_caps.h"

// ===================== VERSION =====================
static const int SKETCH_VER = 7;

// ===================== FIRMWARE IDENTITY =====================
#ifndef ER1_GIT_REV
#define ER1_GIT_REV "unknown"
#endif
static const char* FW_BUILD   = __DATE__ " " __TIME__;
static const char* FW_GIT_REV = ER1_GIT_REV;
static const char* FW_MIC_MODEL = "INMP441";
static const char* FW_WINDOW    = "hann";

// ===================== HARDWARE =====================
static const i2s_port_t I2S_PORT = I2S_NUM_0;
static const int PIN_BCLK  = 26;
static const int PIN_LRCLK = 25;
static const int PIN_DIN   = 33;

// INMP441 often wired as RIGHT. Keep as you had:
static const bool MIC_USE_LEFT = false;

// ===================== AUDIO / FFT =====================
static const int   FS_HZ   = 48000;
static const int   N_FFT   = 1024;
static const int   HOP     = 256;

// Bands for export (128 log-spaced bins, compact “FFT magnitude bins”)
static const int   N_BANDS128 = 128;
static const float B128_F_LO  = 50.0f;
static const float B128_F_HI  = 3500.0f;
static float b128_edges[N_BANDS128 + 1];

static const float F_MIN = 50.0f;
static const float F_MAX = 3500.0f;

static const float MS_PER_FRAME = 1000.0f * (float)HOP / (float)FS_HZ;

// ===================== PREPROCESS =====================
static const bool  ENABLE_DC_BLOCK = true;
static float dc_y = 0.0f;
static float dc_xprev = 0.0f;
static const float DC_R = 0.995f;

// ===================== FILE NAMING =====================
static const char* FILE_PREFIX = "/raw_";

// ===================== COLLECT CONFIG (MUST BE ABOVE total_pre_ms()) =====================
static int  cfg_pre_ms  = 60;
static int  cfg_post_ms = 250;
static int  cfg_lead_ms = 100;

// stored pre = pre + lead
static int total_pre_ms(){ return cfg_pre_ms + cfg_lead_ms; }

// ===================== SAFE HEAP HELPERS =====================
static void die(const char* msg) {
  Serial.println(msg);
  while (true) delay(1000);
}

static void* xmalloc_caps(size_t n, uint32_t caps, const char* what) {
  void* p = heap_caps_malloc(n, caps);
  if (!p) {
    Serial.printf("FATAL: malloc failed for %s (%u bytes)\n", what, (unsigned)n);
    Serial.printf("Heap free=%u min=%u\n", (unsigned)ESP.getFreeHeap(), (unsigned)ESP.getMinFreeHeap());
    die("OOM");
  }
  memset(p, 0, n);
  return p;
}

static void heap_info(const char* tag) {
  Serial.printf("HEAP[%s]: free=%u min=%u\n", tag, (unsigned)ESP.getFreeHeap(), (unsigned)ESP.getMinFreeHeap());
}

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

static void fsinfo() {
  size_t total = SPIFFS.totalBytes();
  size_t used  = SPIFFS.usedBytes();
  Serial.printf("SPIFFS: total=%u used=%u free=%u\n",
                (unsigned)total, (unsigned)used, (unsigned)(total - used));
}

static bool ensure_space_bytes(size_t needed, size_t reserve = 4096){
  size_t freeb = SPIFFS.totalBytes() - SPIFFS.usedBytes();
  if (freeb <= reserve) return false;
  return needed <= (freeb - reserve);
}

// ===================== BUFFERS (ALLOCATED ON HEAP) =====================
static int32_t* i2s_buf = nullptr;     // [HOP*2]
static float*   hop_f   = nullptr;     // [HOP]
static float*   x_win   = nullptr;     // [N_FFT]
static float*   hannw   = nullptr;     // [N_FFT]
static float*   fft_in  = nullptr;     // [2*N_FFT]
static float*   mag     = nullptr;     // [N_FFT/2]
static float*   prev_mag= nullptr;     // [N_FFT/2]

// ===================== FEATURE ROW (for ring + CSV) =====================
struct FeatRow {
  int   ms_from_event;
  float rms;
  float flux;
  float centroid_hz;
  float spread_hz;
  float rolloff85_hz;
  float flatness;
  float zcr;
  float peak_hz;
  float peak_mag;
  float peak_ratio;
  float b128[N_BANDS128];
};

// ===================== PRE-RING (HEAP) =====================
static FeatRow* pre_rows = nullptr;
static int pre_cap = 0;
static int pre_w = 0;
static bool pre_filled = false;

static int frames_for_ms(int ms){ return (int)ceilf((float)ms / MS_PER_FRAME); }

// ===================== COLLECT STATE =====================
static bool collecting = false;
static bool enter_trigger = false;
static bool ignore_next_empty = false;
static bool last_was_cr = false;

static int  cur_midi = -1;
static int  end_midi = -1;
static int  reps_per_key = 10;
static int  rep_cur = 1;

static char cur_key_name[16] = {0};

static float last_rms = 0.0f;

// ===================== NOTE NAMES (C-based octave system) =====================
static const char* NOTE_NAMES_SHARP_C[12] = {
  "C","C#","D","D#","E","F","F#","G","G#","A","A#","B"
};

// ===================== DSP / FEATURES =====================
static void build_hann() {
  for (int i = 0; i < N_FFT; i++) {
    hannw[i] = 0.5f - 0.5f * cosf(2.0f * M_PI * (float)i / (float)(N_FFT - 1));
  }
}

static void build_b128_edges(){
  float ratio = powf(B128_F_HI / B128_F_LO, 1.0f / (float)N_BANDS128);
  b128_edges[0] = B128_F_LO;
  for (int i=1;i<=N_BANDS128;i++){
    b128_edges[i] = b128_edges[i-1] * ratio;
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

static void band_limits(int &k0, int &k1) {
  k0 = (int)floorf(F_MIN * N_FFT / (float)FS_HZ);
  k1 = (int)ceilf (F_MAX * N_FFT / (float)FS_HZ);
  k0 = imax(1, k0);
  k1 = imin((N_FFT/2)-1, k1);
}

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

// 128 log bins: log10(mag) accumulation
static void compute_bands_128(float out_b128[N_BANDS128]) {
  for (int i=0;i<N_BANDS128;i++) out_b128[i] = 0.0f;

  int kmin = (int)floorf((B128_F_LO * (float)N_FFT) / (float)FS_HZ);
  int kmax = (int)ceilf((B128_F_HI * (float)N_FFT) / (float)FS_HZ);
  if (kmin < 1) kmin = 1;
  if (kmax > (N_FFT/2 - 1)) kmax = (N_FFT/2 - 1);

  int bi = 0;
  float e_hi = b128_edges[1];

  for (int k=kmin;k<=kmax;k++){
    float f = (float)k * (float)FS_HZ / (float)N_FFT;
    while (bi < N_BANDS128-1 && f >= e_hi){
      bi++;
      e_hi = b128_edges[bi+1];
    }
    float v = mag[k];
    out_b128[bi] += log10f(v + 1e-12f);
  }
}

// ===================== I2S =====================
static void i2s_setup() {
  i2s_config_t cfg = {};
  cfg.mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_RX);
  cfg.sample_rate = FS_HZ;
  cfg.bits_per_sample = I2S_BITS_PER_SAMPLE_32BIT;
  cfg.channel_format = I2S_CHANNEL_FMT_RIGHT_LEFT;
  cfg.communication_format = I2S_COMM_FORMAT_I2S; // deprecated warning OK
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

// Clip tracking per hop (24-bit)
static bool read_hop(float* out_f, float& out_rms, float& out_zcr, float& out_clip_frac) {
  size_t bytes_read = 0;
  esp_err_t ok = i2s_read(I2S_PORT, (void*)i2s_buf, sizeof(int32_t) * (HOP * 2), &bytes_read, portMAX_DELAY);
  if (ok != ESP_OK || bytes_read != sizeof(int32_t) * (HOP * 2)) return false;

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

// ===================== FEATURE BUILD =====================
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
  compute_top_peak(r.peak_hz, r.peak_mag);
  r.peak_ratio = compute_peak_ratio(r.peak_mag);
  compute_bands_128(r.b128);
  return r;
}

// ===================== CSV IO =====================
static void write_csv_header(File &out) {
  out.print("ms_from_event,rms,flux,centroid_hz,spread_hz,rolloff85_hz,flatness,zcr,peak_hz,peak_mag,peak_ratio");
  for (int i=0;i<N_BANDS128;i++){
    char name[8];
    snprintf(name, sizeof(name), ",b%03d", i);
    out.print(name);
  }
  out.print("\n");
}

static void write_csv_row(File &out, const FeatRow &r) {
  out.printf("%d,%.6f,%.6f,%.2f,%.2f,%.2f,%.6f,%.6f,%.2f,%.6f,%.4f",
             r.ms_from_event, r.rms, r.flux,
             r.centroid_hz, r.spread_hz, r.rolloff85_hz,
             r.flatness, r.zcr,
             r.peak_hz, r.peak_mag, r.peak_ratio);
  for (int i=0;i<N_BANDS128;i++){
    out.printf(",%.6f", r.b128[i]);
  }
  out.print("\n");
}

// ===================== PRE-RING =====================
static void ring_alloc_for_ms(int total_pre_ms_needed){
  int want_frames = frames_for_ms(total_pre_ms_needed) + 6;
  want_frames = imax(16, imin(want_frames, 256));

  if (pre_rows && pre_cap == want_frames) return;

  if (pre_rows) {
    heap_caps_free(pre_rows);
    pre_rows = nullptr;
  }
  pre_rows = (FeatRow*)xmalloc_caps(sizeof(FeatRow) * (size_t)want_frames, MALLOC_CAP_8BIT, "pre_rows");
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
static int ring_index_of_earliest(){
  if (!pre_rows || pre_cap <= 0) return 0;
  if (pre_filled) return pre_w;
  return 0;
}
static FeatRow ring_peek_earliest(){
  FeatRow z = {};
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
// CHANGED: buffered + flow-controlled serial streaming
static bool dump_file_path(const String& p_in) {
  String p = normPath(p_in);
  File f = SPIFFS.open(p, FILE_READ);
  if (!f) { Serial.printf("ERR: cannot open %s\n", p.c_str()); return false; }

  size_t fsz = (size_t)f.size();
  Serial.printf("----- BEGIN %s (%u bytes) -----\n", p.c_str(), (unsigned)fsz);

  static const size_t CHUNK = 1024;
  uint8_t buf[CHUNK];

  uint32_t last_yield = millis();

  while (f.available()) {
    size_t n = f.read(buf, CHUNK);
    if (n == 0) break;

    size_t off = 0;
    while (off < n) {
      int can = Serial.availableForWrite();
      if (can <= 0) {
        delay(0);
        continue;
      }
      size_t w = (size_t)imin((int)(n - off), can);
      size_t wrote = Serial.write(buf + off, w);
      off += wrote;

      uint32_t now = millis();
      if ((now - last_yield) > 10) {
        yield();
        last_yield = now;
      }
    }
  }

  f.close();
  Serial.printf("\n----- END %s -----\n", p.c_str());
  Serial.flush();
  return true;
}

static void purge_raw_files() {
  File root = SPIFFS.open("/");
  if (!root || !root.isDirectory()) { Serial.println("ERR: open / failed"); return; }

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
}

static void spiffs_format_now() {
  Serial.println("FORMAT: SPIFFS.format() ...");
  SPIFFS.end();
  bool ok = SPIFFS.format();
  Serial.printf("FORMAT: %s\n", ok ? "OK" : "FAIL");
  if (!SPIFFS.begin(true)) {
    Serial.println("ERR: SPIFFS.begin failed after format");
  }
  fsinfo();
}

// ===================== STREAM RESET =====================
static void reset_stream_state_for_collection(){
  ring_alloc_for_ms(total_pre_ms());
  pre_w = 0; pre_filled = false;

  memset(x_win, 0, sizeof(float) * N_FFT);
  memset(prev_mag, 0, sizeof(float) * (N_FFT/2));

  dc_y = 0; dc_xprev = 0;

  float rms0=0, zcr0=0, clip0=0;
  for (int n=0; n<(N_FFT/HOP); n++){
    if (!read_hop(hop_f, rms0, zcr0, clip0)) { n--; continue; }
    push_window(hop_f);
  }
  compute_mag();
  for (int k=0;k<N_FFT/2;k++) prev_mag[k] = mag[k];
}

// ===================== CONSOLE PROMPTS =====================
static void prompt_key(){
  Serial.printf("PRESS %s  rep %d/%d  then hit Enter\n", cur_key_name, rep_cur, reps_per_key);
}
static void finalize_collection(){
  collecting = false;
  Serial.println("COLLECT DONE");
}

// ===================== SEGMENTS (noise <s>, silence <s>) =====================
static void record_segment_csv(const char* seg_label, int secs){
  secs = clamp_int(secs, 1, 120);

  const int total_frames = frames_for_ms(secs * 1000);

  size_t est = 1600 + (size_t)(total_frames + 16) * (size_t)(90 + 12 * N_BANDS128);
  if (!ensure_space_bytes(est)) {
    Serial.printf("ERR: not enough SPIFFS space for segment (need ~%u bytes). Use purge/format.\n", (unsigned)est);
    fsinfo();
    return;
  }

  uint32_t t = (uint32_t)millis();
  char fname[128];
  snprintf(fname, sizeof(fname), "%sseg_%s_%ds_%u.csv", FILE_PREFIX, seg_label, secs, (unsigned)t);
  String path = normPath(String(fname));

  File out = SPIFFS.open(path, FILE_WRITE);
  if (!out){
    Serial.printf("ERR: cannot create csv %s\n", path.c_str());
    fsinfo();
    return;
  }

  out.printf("#FW,ver=%d,build=%s,git_rev=%s,mic_model=%s,mic_channel=%s,window=%s\n",
             SKETCH_VER, FW_BUILD, FW_GIT_REV, FW_MIC_MODEL, (MIC_USE_LEFT ? "L":"R"), FW_WINDOW);

  out.printf("#META,type=segment,seg=%s,secs=%d,fs_hz=%d,hop=%d,n_fft=%d,fmin=%.1f,fmax=%.1f,ms_per_frame=%.5f,t_ms=%u,bands=log128\n",
             seg_label, secs, FS_HZ, HOP, N_FFT, F_MIN, F_MAX, MS_PER_FRAME, (unsigned)t);

  out.print("#BEGIN {");
  out.printf("\"type\":\"segment\",\"seg\":\"%s\",\"secs\":%d,", seg_label, secs);
  out.printf("\"fs_hz\":%d,\"hop\":%d,\"n_fft\":%d,", FS_HZ, HOP, N_FFT);
  out.printf("\"fmin\":%.1f,\"fmax\":%.1f,", F_MIN, F_MAX);
  out.printf("\"ms_per_frame\":%.6f,\"t_ms\":%u,\"bands\":\"log128\"", MS_PER_FRAME, (unsigned)t);
  out.print("}\n");

  write_csv_header(out);

  // Prime window
  float rms0=0, zcr0=0, clip0=0;
  for (int n=0; n<(N_FFT/HOP); n++){
    if (!read_hop(hop_f, rms0, zcr0, clip0)) { n--; continue; }
    push_window(hop_f);
  }
  compute_mag();
  for (int k=0;k<N_FFT/2;k++) prev_mag[k] = mag[k];

  for (int i=0;i<total_frames;i++){
    float rms=0, zcr=0, clip=0;
    if (!read_hop(hop_f, rms, zcr, clip)) { i--; continue; }
    last_rms = rms;

    push_window(hop_f);
    compute_mag();
    float flux = compute_flux();

    int ms = (int)lroundf(i * MS_PER_FRAME);
    FeatRow r = build_row(ms, rms, zcr, flux);
    write_csv_row(out, r);

    for (int k=0;k<N_FFT/2;k++) prev_mag[k] = mag[k];

    if ((i & 0x0F)==0) yield();
  }

  out.print("#END {}\n");

  out.flush();
  out.close();

  Serial.printf("OK: segment %s saved %s (secs=%d)\n", seg_label, path.c_str(), secs);

  dump_file_path(path);
  SPIFFS.remove(path);
}

// ===================== CAPTURE (DUMP PER REP) =====================
static bool capture_event_now(){
  ring_alloc_for_ms(total_pre_ms());

  const int WANT_PRE_FRAMES  = frames_for_ms(total_pre_ms());
  const int WANT_POST_FRAMES = frames_for_ms(cfg_post_ms);

  size_t est = 1600 + (size_t)(WANT_PRE_FRAMES + WANT_POST_FRAMES + 16) * (size_t)(90 + 12 * N_BANDS128);
  if (!ensure_space_bytes(est)) {
    Serial.printf("ERR: not enough SPIFFS space for capture (need ~%u bytes). Use purge/format.\n", (unsigned)est);
    fsinfo();
    return false;
  }

  uint32_t t = (uint32_t)millis();
  char fname[128];
  snprintf(fname, sizeof(fname), "%sclean_%s_r%02d_%u.csv", FILE_PREFIX, cur_key_name, rep_cur, (unsigned)t);
  String path = normPath(String(fname));

  File out = SPIFFS.open(path, FILE_WRITE);
  if (!out){
    Serial.printf("ERR: cannot create csv %s\n", path.c_str());
    fsinfo();
    return false;
  }

  // --- Header comments ---
  out.printf("#FW,ver=%d,build=%s,git_rev=%s,mic_model=%s,mic_channel=%s,window=%s\n",
             SKETCH_VER, FW_BUILD, FW_GIT_REV, FW_MIC_MODEL, (MIC_USE_LEFT ? "L":"R"), FW_WINDOW);

  out.printf("#META,type=keypress,mode=clean,key=%s,rep=%d,fs_hz=%d,hop=%d,n_fft=%d,fmin=%.1f,fmax=%.1f,pre_ms=%d,lead_ms=%d,post_ms=%d,stored_pre_ms=%d,ms_per_frame=%.5f,t_ms=%u,bands=log128\n",
             cur_key_name, rep_cur, FS_HZ, HOP, N_FFT, F_MIN, F_MAX,
             cfg_pre_ms, cfg_lead_ms, cfg_post_ms, total_pre_ms(), MS_PER_FRAME, (unsigned)t);

  // --- “Right format” markers for parsers that expect JSON blocks ---
  out.print("#BEGIN {");
  out.printf("\"type\":\"keypress\",\"mode\":\"clean\",\"key\":\"%s\",\"rep\":%d,", cur_key_name, rep_cur);
  out.printf("\"fs_hz\":%d,\"hop\":%d,\"n_fft\":%d,", FS_HZ, HOP, N_FFT);
  out.printf("\"fmin\":%.1f,\"fmax\":%.1f,", F_MIN, F_MAX);
  out.printf("\"pre_ms\":%d,\"lead_ms\":%d,\"post_ms\":%d,\"stored_pre_ms\":%d,", cfg_pre_ms, cfg_lead_ms, cfg_post_ms, total_pre_ms());
  out.printf("\"ms_per_frame\":%.6f,\"t_ms\":%u,\"bands\":\"log128\"", MS_PER_FRAME, (unsigned)t);
  out.print("}\n");

  // Reserve a fixed-width DIAG line we overwrite after capture
  const size_t diag_pos = out.position();
  out.print("#DIAG,");
  for (int i=0;i<240;i++) out.print(' ');
  out.print("\n");

  write_csv_header(out);

  // --- Build PRE block from ring, compute baseline stats ---
  const int avail = ring_available();
  const int take = imin(WANT_PRE_FRAMES, avail);
  const int missing = WANT_PRE_FRAMES - take;

  FeatRow earliest = {};
  if (avail > 0) earliest = ring_peek_earliest();

  double sr=0.0, sr2=0.0, sf=0.0, sf2=0.0;
  int nb=0;

  int row_idx = 0;

  for(int i=0;i<missing;i++){
    FeatRow r = earliest;
    r.ms_from_event = (int)lroundf((i - (WANT_PRE_FRAMES - 1)) * MS_PER_FRAME);
    write_csv_row(out, r);
    sr += (double)r.rms;  sr2 += (double)r.rms*(double)r.rms;
    sf += (double)r.flux; sf2 += (double)r.flux*(double)r.flux;
    nb++; row_idx++;
    if ((row_idx & 0x0F)==0) yield();
  }

  int start = ring_index_of_earliest();
  for(int i=0;i<take;i++){
    int idx = (start + i) % pre_cap;
    FeatRow r = pre_rows[idx];
    r.ms_from_event = (int)lroundf((missing + i - (WANT_PRE_FRAMES - 1)) * MS_PER_FRAME);
    write_csv_row(out, r);
    sr += (double)r.rms;  sr2 += (double)r.rms*(double)r.rms;
    sf += (double)r.flux; sf2 += (double)r.flux*(double)r.flux;
    nb++; row_idx++;
    if ((row_idx & 0x0F)==0) yield();
  }

  if (nb <= 0) nb = 1;
  double mr = sr / (double)nb;
  double vr = sr2 / (double)nb - mr*mr; if (vr < 0.0) vr = 0.0;
  double mf = sf / (double)nb;
  double vf = sf2 / (double)nb - mf*mf; if (vf < 0.0) vf = 0.0;

  float pre_rms_mean  = (float)mr;
  float pre_rms_std   = (float)sqrt(vr);
  float pre_flux_mean = (float)mf;
  float pre_flux_std  = (float)sqrt(vf);

  // --- Onset detection thresholds (deterministic) ---
  const float K_FLUX = 4.0f;
  const float K_RMS  = 3.0f;
  const int   NEED_CONSEC = 2;

  float thr_flux = pre_flux_mean + K_FLUX * pre_flux_std;
  float thr_rms  = pre_rms_mean  + K_RMS  * pre_rms_std;

  if (pre_flux_std < 1e-9f) thr_flux = pre_flux_mean * 3.0f;
  if (pre_rms_std  < 1e-9f) thr_rms  = pre_rms_mean  * 2.0f;

  int onset_idx = -1;
  int onset_ms  = -1;

  float rms=0, zcr=0, clip=0;
  int consec = 0;
  int consec_start_idx = -1;
  int consec_start_ms  = 0;

  double clip_sum = 0.0; int clip_n = 0;
  double flat_sum = 0.0; int flat_n = 0;

  for(int f=1; f<=WANT_POST_FRAMES; f++){
    if (!read_hop(hop_f, rms, zcr, clip)) { f--; continue; }
    last_rms = rms;

    push_window(hop_f);
    compute_mag();
    float flux = compute_flux();

    int ms = (int)lroundf(f * MS_PER_FRAME);
    FeatRow r = build_row(ms, rms, zcr, flux);
    write_csv_row(out, r);

    if (onset_idx < 0 && ms >= 0 - cfg_lead_ms) {
      bool ok = (r.flux >= thr_flux) && (r.rms >= thr_rms);
      if (ok) {
        consec++;
        if (consec == 1) {
          consec_start_idx = row_idx;
          consec_start_ms  = r.ms_from_event;
        }
        if (consec >= NEED_CONSEC) {
          onset_idx = consec_start_idx;
          onset_ms  = consec_start_ms;
        }
      } else {
        consec = 0;
        consec_start_idx = -1;
      }
    }

    clip_sum += (double)clip; clip_n++;
    if (ms >= 0 && ms <= 180) { flat_sum += (double)r.flatness; flat_n++; }

    ring_push(r);
    for (int k=0;k<N_FFT/2;k++) prev_mag[k] = mag[k];

    row_idx++;
    if ((row_idx & 0x0F)==0) yield();
  }

  float clip_frac = (clip_n > 0) ? (float)(clip_sum / (double)clip_n) : 0.0f;
  float flat_mean = (flat_n > 0) ? (float)(flat_sum / (double)flat_n) : 1.0f;
  float snr_proxy = 1.0f / (flat_mean + 1e-6f);

  // overwrite DIAG line
  char diag[256];
  int n = snprintf(diag, sizeof(diag),
    "#DIAG,onset_ms=%d,onset_idx=%d,baseline_rms_mean=%.6f,baseline_rms_std=%.6f,baseline_flux_mean=%.6f,baseline_flux_std=%.6f,onset_thr_rms=%.6f,onset_thr_flux=%.6f,clip_frac=%.6f,snr_proxy=%.6f",
    onset_ms, onset_idx,
    pre_rms_mean, pre_rms_std,
    pre_flux_mean, pre_flux_std,
    thr_rms, thr_flux,
    clip_frac, snr_proxy
  );
  if (n < 0) n = 0;
  if (n > 240) n = 240;

  out.flush();
  out.seek(diag_pos);
  out.print(diag);
  for (int i=n; i<240; i++) out.print(' ');
  out.print("\n");

  // JSON end marker (kept short on purpose)
  out.print("#END {");
  out.printf("\"onset_ms\":%d,\"clip_frac\":%.6f,\"snr_proxy\":%.6f", onset_ms, clip_frac, snr_proxy);
  out.print("}\n");

  out.flush();
  out.close();

  Serial.printf("OK: captured %s  (rows=%d onset_ms=%d clip=%.4f snr=%.3f)\n",
                path.c_str(), row_idx, onset_ms, clip_frac, snr_proxy);

  // dump immediately and delete (dump-per-rep)
  dump_file_path(path);
  SPIFFS.remove(path);

  return true;
}

// ===================== ADVANCE =====================
static void advance_after_capture(){
  rep_cur++;
  if (rep_cur > reps_per_key) {
    cur_midi++;
    rep_cur = 1;
    if (cur_midi > end_midi) {
      collecting = false;
      Serial.println("COLLECT DONE");
      return;
    }
    midi_to_key_name(cur_midi, cur_key_name);
    Serial.printf("NEXT: %s\n", cur_key_name);
  }
  prompt_key();
}

// ===================== CONSOLE =====================
static String cmdline;
static const int CMD_MAX = 200;

static void print_help() {
  Serial.println("Commands:");
  Serial.println("  help");
  Serial.println("  fsinfo");
  Serial.println("  purge                      delete all /raw_* files from SPIFFS");
  Serial.println("  format                     SPIFFS.format() then remount");
  Serial.println("  dump <path>                dump a specific file (e.g. dump /raw_x.csv)");
  Serial.println("  noise <secs>               record noise segment (CSV) then dump+delete");
  Serial.println("  silence <secs>             record silence segment (CSV) then dump+delete");
  Serial.println("  pre <ms>           (default 60; stored pre is pre+lead)");
  Serial.println("  post <ms>          (default 400)");
  Serial.println("  lead <ms>          (default 150)");
  Serial.println("  end <key>          (default A7)");
  Serial.println("  start <key> <reps> (clean keys; dumps each rep immediately)");
  Serial.println("  stop");
  Serial.println("");
  Serial.println("Collecting: press key + hit Enter (empty line) to capture.");
}

static void prompt() { Serial.print("> "); }

static void start_collection(const String& startKey, int reps){
  int s=-1;
  if (!parse_key_to_midi(startKey, s)) { Serial.println("ERR: start key format (e.g. C4, F#5, Bb3)"); return; }

  collecting = true;
  enter_trigger = false;
  ignore_next_empty = true;

  cur_midi = s;
  reps_per_key = clamp_int(reps, 1, 64);
  rep_cur = 1;

  midi_to_key_name(cur_midi, cur_key_name);
  Serial.printf("START: from %s -> end reps/key=%d pre=%d lead=%d post=%d (stored_pre=%d) bands=log128\n",
                cur_key_name, reps_per_key, cfg_pre_ms, cfg_lead_ms, cfg_post_ms, total_pre_ms());

  reset_stream_state_for_collection();

  // CHANGED: record 1s silence at start (for Python later). Not used in thresholds/stats.
  Serial.println("BASELINE: recording 1s silence segment...");
  record_segment_csv("start_silence", 1);

  // After a blocking segment capture, refresh stream/ring so pre-frames are current for keypress capture.
  reset_stream_state_for_collection();

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
  } else if (cmd == "purge") {
    purge_raw_files();
  } else if (cmd == "format") {
    spiffs_format_now();
  } else if (cmd == "dump") {
    if (arg.length() == 0) { Serial.println("Usage: dump <path>"); return; }
    dump_file_path(arg);
  } else if (cmd == "noise") {
    if (arg.length() == 0) { Serial.println("Usage: noise <secs>"); return; }
    record_segment_csv("noise", arg.toInt());
  } else if (cmd == "silence") {
    if (arg.length() == 0) { Serial.println("Usage: silence <secs>"); return; }
    record_segment_csv("silence", arg.toInt());
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
    if (arg.length() == 0) { Serial.println("Usage: start <key> <reps>"); return; }
    int sp1 = arg.indexOf(' ');
    if (sp1 < 0) { Serial.println("Usage: start <key> <reps>"); return; }
    String k = arg.substring(0, sp1); k.trim();
    String r = arg.substring(sp1+1); r.trim();
    start_collection(k, r.toInt());
  } else if (cmd == "stop") {
    collecting = false;
    Serial.println("STOP");
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
}

// ===================== SETUP / LOOP =====================
void setup() {
  // CHANGED: baud rate
  Serial.begin(921600);
  delay(200);

  Serial.println();
  Serial.println("Boot...");

  if (!SPIFFS.begin(true)) die("ERR: SPIFFS.begin failed");

  esp_err_t e = dsps_fft2r_init_fc32(NULL, CONFIG_DSP_MAX_FFT_SIZE);
  if (e != ESP_OK) die("ERR: dsps_fft2r_init");

  heap_info("before_alloc");

  i2s_buf  = (int32_t*)xmalloc_caps(sizeof(int32_t) * (HOP * 2), MALLOC_CAP_8BIT, "i2s_buf");
  hop_f    = (float*)  xmalloc_caps(sizeof(float)   * HOP,       MALLOC_CAP_8BIT, "hop_f");
  x_win    = (float*)  xmalloc_caps(sizeof(float)   * N_FFT,     MALLOC_CAP_8BIT, "x_win");
  hannw    = (float*)  xmalloc_caps(sizeof(float)   * N_FFT,     MALLOC_CAP_8BIT, "hannw");
  fft_in   = (float*)  xmalloc_caps(sizeof(float)   * (2*N_FFT), MALLOC_CAP_8BIT, "fft_in");
  mag      = (float*)  xmalloc_caps(sizeof(float)   * (N_FFT/2), MALLOC_CAP_8BIT, "mag");
  prev_mag = (float*)  xmalloc_caps(sizeof(float)   * (N_FFT/2), MALLOC_CAP_8BIT, "prev_mag");

  heap_info("after_alloc");

  build_hann();
  build_b128_edges();
  i2s_setup();

  int eidx=-1;
  if (!parse_key_to_midi("A7", eidx)) die("ERR: default end parse (A7)");
  end_midi = eidx;

  ring_alloc_for_ms(total_pre_ms());

  Serial.println();
  Serial.println("ESP32+INMP441 Continuous Features -> Enter-to-Capture CSV (dump-per-rep, log128 bins)");
  Serial.printf("#SESSION,ver=%d,build=%s,git_rev=%s,mic_model=%s,mic_channel=%s,window=%s\n",
                SKETCH_VER, FW_BUILD, FW_GIT_REV, FW_MIC_MODEL, (MIC_USE_LEFT ? "L":"R"), FW_WINDOW);
  Serial.printf("FS=%d N_FFT=%d HOP=%d (%.2f ms/frame)\n", FS_HZ, N_FFT, HOP, MS_PER_FRAME);
  Serial.printf("Bins: %d log bins from %.1f..%.1f Hz\n", N_BANDS128, B128_F_LO, B128_F_HI);
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

    bool ok = capture_event_now();
    if (ok) {
      advance_after_capture();
    } else {
      Serial.println("REC FAIL (retry same rep)");
      prompt_key();
    }
  }

  delay(1);
}
