// =====================================================
// ESP32 Piano Collector — framed UART (COBS + CRC32)
// =====================================================
//
// Commands (ASCII lines, '\n' or '\r' terminated):
//   start <label> <reps>
//     - if <label> parses as piano key (a0..a7 with optional #):
//         chromatic auto-advance in THIS order:
//         a0 a#0 b0 c1 c#1 d1 d#1 e1 f1 f#1 g1 g#1 a1 ... a7
//     - else:
//         fixed-label mode (repeat that label for <reps> then stop)
//   stop
//   <empty line>   (Enter trigger) => capture + stream if run_active
//
// Output: ALL framed (no raw Serial println):
//   TXT: "TXT"+ver+len+msg+crc
//   CAP: "CAP"+ver+label(24)+rep+reps+fs+pre/lead/post+flags+total+clip+maxabs+rsv+crc
//   DA : "DA"+offset+nsamp+pcm+crc
//
// Capture window (locked):
//   pre=300ms, lead=0ms, post=400ms @ 48kHz
//   => full raw PCM before and after Enter trigger.
//
// Memory strategy:
//   - ONLY one big buffer in DRAM: ring[TOTAL_SAMPLES]
//   - We freeze ring writes during streaming so the capture window
//     can't be overwritten even if UART streaming takes > window length.
// =====================================================

#include <Arduino.h>
#include <driver/i2s.h>

// ------------------ Config ------------------
static constexpr uint32_t FS_HZ   = 48000;
static constexpr uint16_t PRE_MS  = 300;
static constexpr uint16_t LEAD_MS = 0;
static constexpr uint16_t POST_MS = 400;

static constexpr uint32_t PRE_SAMPLES     = (FS_HZ * PRE_MS) / 1000;
static constexpr uint32_t LEAD_SAMPLES    = (FS_HZ * LEAD_MS) / 1000;
static constexpr uint32_t POST_SAMPLES    = (FS_HZ * POST_MS) / 1000;
static constexpr uint32_t PRELEAD_SAMPLES = PRE_SAMPLES + LEAD_SAMPLES;
static constexpr uint32_t TOTAL_SAMPLES   = PRELEAD_SAMPLES + POST_SAMPLES;

static constexpr uint32_t UART_BAUD = 921600;
static constexpr uint8_t  PROTO_VER = 1;
static constexpr uint16_t CHUNK_SAMPLES = 512; // DA chunk size

// INMP441 I2S pins (adjust if your wiring differs)
static constexpr i2s_port_t I2S_PORT = I2S_NUM_0;
static constexpr int PIN_I2S_WS  = 25;
static constexpr int PIN_I2S_SCK = 26;
static constexpr int PIN_I2S_SD  = 33;

// ------------------ Buffers ------------------
// Single ring buffer sized to capture window (700ms @ 48kHz = 33600 samples)
static int16_t ring[TOTAL_SAMPLES];
static volatile uint32_t ring_write = 0;

// Freeze ring writes during streaming to prevent overwrite
static volatile bool ring_freeze = false;

// ------------------ Run state ------------------
static bool run_active = false;
static bool mode_chromatic = false;

static char label[24] = {0}; // lowercase, null-terminated
static int reps_per_label = 0;
static int rep_idx = 1;

// Chromatic key state (SPECIAL ORDER):
// a0 a#0 b0 c1 ... g#1 a1 ... a7
static const char* NOTE_SEQ[] = {"a","a#","b","c","c#","d","d#","e","f","f#","g","g#"};
static constexpr int NOTE_COUNT = 12;
static int note_i = 0;   // index in NOTE_SEQ
static int octave = 0;   // piano-style octaves for your labels

// ------------------ CRC32 ------------------
static uint32_t crc32(const uint8_t* d, size_t n) {
  uint32_t c = 0xFFFFFFFFu;
  for (size_t i = 0; i < n; i++) {
    c ^= d[i];
    for (int k = 0; k < 8; k++) {
      uint32_t mask = -(int)(c & 1u);
      c = (c >> 1) ^ (0xEDB88320u & mask);
    }
  }
  return ~c;
}

// ------------------ COBS encode ------------------
static size_t cobs_encode(const uint8_t* in, size_t len, uint8_t* out) {
  size_t ri = 0, wi = 1, ci = 0;
  uint8_t code = 1;
  while (ri < len) {
    if (in[ri] == 0) {
      out[ci] = code;
      code = 1;
      ci = wi++;
      ri++;
    } else {
      out[wi++] = in[ri++];
      if (++code == 0xFF) {
        out[ci] = code;
        code = 1;
        ci = wi++;
      }
    }
  }
  out[ci] = code;
  return wi;
}

static void send_framed(const uint8_t* payload, size_t payload_len) {
  static uint8_t framed[1600];
  size_t fl = cobs_encode(payload, payload_len, framed);
  framed[fl++] = 0x00;
  Serial.write(framed, fl);
}

// ------------------ TXT frame ------------------
static void send_txt(const char* s) {
  static uint8_t pkt[512];
  uint16_t n = (uint16_t)min<size_t>(strlen(s), 450);

  size_t p = 0;
  pkt[p++] = 'T'; pkt[p++] = 'X'; pkt[p++] = 'T';
  pkt[p++] = PROTO_VER;
  memcpy(pkt + p, &n, 2); p += 2;
  memcpy(pkt + p, s, n); p += n;

  uint32_t c = crc32(pkt, p);
  memcpy(pkt + p, &c, 4); p += 4;

  send_framed(pkt, p);
}

static void send_txtf(const char* fmt, ...) {
  char b[256];
  va_list ap;
  va_start(ap, fmt);
  vsnprintf(b, sizeof(b), fmt, ap);
  va_end(ap);
  send_txt(b);
}

// ------------------ Helpers ------------------
static void to_lower_inplace(char* s) {
  for (; *s; s++) *s = (char)tolower((unsigned char)*s);
}

static bool parse_key_label(const char* s_in, int& out_note_i, int& out_oct) {
  // Valid labels in this scheme:
  // octave 0: a0, a#0, b0
  // octave 1..6: a..g# (all)
  // octave 7: a7 only
  if (!s_in || !s_in[0]) return false;

  char s[16] = {0};
  strncpy(s, s_in, sizeof(s) - 1);
  to_lower_inplace(s);

  int idx = 0;
  char l = s[idx++];
  if (l < 'a' || l > 'g') return false;

  char note[3] = {0,0,0};
  note[0] = l;

  if (s[idx] == '#') { note[1] = '#'; idx++; }

  if (!isdigit((unsigned char)s[idx])) return false;
  int oct = s[idx++] - '0';
  if (s[idx] != '\0') return false;
  if (oct < 0 || oct > 7) return false;

  int ni = -1;
  for (int i = 0; i < NOTE_COUNT; i++) {
    if (strcmp(note, NOTE_SEQ[i]) == 0) { ni = i; break; }
  }
  if (ni < 0) return false;

  if (oct == 7 && ni != 0) return false; // only a7
  if (oct == 0 && ni >= 3) return false; // no c0.. etc in your labeling

  out_note_i = ni;
  out_oct = oct;
  return true;
}

static void set_label_to_current_key() {
  snprintf(label, sizeof(label), "%s%d", NOTE_SEQ[note_i], octave);
}

// Advance in your required order:
// - octave increments ONLY at b -> c (b0 -> c1)
// - octave does NOT increment at g# -> a (g#1 -> a1)
static bool next_chromatic_key() {
  if (octave == 7 && note_i == 0) return false; // already at a7

  int next_note = note_i + 1;
  int next_oct = octave;

  if (next_note >= NOTE_COUNT) {
    next_note = 0; // g# -> a (same octave)
  }

  if (note_i == 2 && next_note == 3) { // b -> c
    next_oct = octave + 1;
  }

  if (next_oct == 0 && next_note >= 3) return false;
  if (next_oct == 7 && next_note != 0) return false;
  if (next_oct > 7) return false;

  note_i = next_note;
  octave = next_oct;
  set_label_to_current_key();
  return true;
}

// ------------------ I2S init ------------------
static void i2s_init() {
  i2s_config_t cfg = {
    .mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_RX),
    .sample_rate = FS_HZ,
    .bits_per_sample = I2S_BITS_PER_SAMPLE_32BIT,
    .channel_format = I2S_CHANNEL_FMT_ONLY_LEFT,
#if defined(I2S_COMM_FORMAT_STAND_I2S)
    .communication_format = I2S_COMM_FORMAT_STAND_I2S,
#else
    .communication_format = I2S_COMM_FORMAT_I2S,
#endif
    .intr_alloc_flags = ESP_INTR_FLAG_LEVEL1,
    .dma_buf_count = 4,
    .dma_buf_len = 256,
    .use_apll = false
  };

  i2s_pin_config_t pins = {
    .bck_io_num = PIN_I2S_SCK,
    .ws_io_num  = PIN_I2S_WS,
    .data_out_num = -1,
    .data_in_num  = PIN_I2S_SD
  };

  i2s_driver_install(I2S_PORT, &cfg, 0, nullptr);
  i2s_set_pin(I2S_PORT, &pins);
  i2s_zero_dma_buffer(I2S_PORT);
}

// ------------------ Audio task ------------------
static void audio_task(void*) {
  int32_t buf[256];
  size_t br = 0;

  while (true) {
    i2s_read(I2S_PORT, buf, sizeof(buf), &br, portMAX_DELAY);
    int n = (int)(br / sizeof(int32_t));
    for (int i = 0; i < n; i++) {
      int16_t s = (int16_t)(buf[i] >> 14);

      if (!ring_freeze) {
        ring[ring_write] = s;
        ring_write = (ring_write + 1) % TOTAL_SAMPLES;
      }
      // if frozen: discard samples but keep draining I2S
    }
  }
}

// ------------------ Compute capture indices ------------------
static inline uint32_t cap_start_from_w0(uint32_t w0) {
  // start of capture = w0 - PRELEAD_SAMPLES (mod TOTAL)
  return (w0 + TOTAL_SAMPLES - PRELEAD_SAMPLES) % TOTAL_SAMPLES;
}

// ------------------ Wait for post samples then freeze ------------------
static void wait_post_and_freeze(uint32_t w0) {
  // Wait until POST samples have arrived after w0
  while (true) {
    uint32_t w = ring_write;
    uint32_t delta = (w + TOTAL_SAMPLES - w0) % TOTAL_SAMPLES;
    if (delta >= POST_SAMPLES) break;
    delay(1);
  }
  // Freeze ring so capture window won't be overwritten while streaming
  ring_freeze = true;
}

// ------------------ Read sample i within capture window ------------------
static inline int16_t cap_sample(uint32_t cap_start, uint32_t i) {
  return ring[(cap_start + i) % TOTAL_SAMPLES];
}

// ------------------ Stream CAP + DA (from ring) ------------------
static void send_capture_frames_from_ring(uint32_t cap_start, uint32_t clip_count, uint16_t max_abs) {
  // CAP header
  uint8_t cap[3 + 1 + 24 + 4 + 4 + 4 + 2 + 2 + 2 + 2 + 4 + 4 + 2 + 2 + 4];
  size_t p = 0;

  cap[p++] = 'C'; cap[p++] = 'A'; cap[p++] = 'P';
  cap[p++] = PROTO_VER;

  char lbl24[24] = {0};
  strncpy(lbl24, label, sizeof(lbl24) - 1);
  memcpy(cap + p, lbl24, 24); p += 24;

  int32_t rep_i32  = rep_idx;
  int32_t reps_i32 = reps_per_label;
  uint32_t fs_u32  = FS_HZ;

  memcpy(cap + p, &rep_i32, 4);  p += 4;
  memcpy(cap + p, &reps_i32, 4); p += 4;
  memcpy(cap + p, &fs_u32, 4);   p += 4;

  uint16_t pre = PRE_MS, lead = LEAD_MS, post = POST_MS, flags = 0;
  memcpy(cap + p, &pre, 2);  p += 2;
  memcpy(cap + p, &lead, 2); p += 2;
  memcpy(cap + p, &post, 2); p += 2;
  memcpy(cap + p, &flags, 2);p += 2;

  uint32_t total = TOTAL_SAMPLES;
  memcpy(cap + p, &total, 4); p += 4;
  memcpy(cap + p, &clip_count, 4); p += 4;
  memcpy(cap + p, &max_abs, 2); p += 2;

  uint16_t rsv = 0;
  memcpy(cap + p, &rsv, 2); p += 2;

  uint32_t c = crc32(cap, p);
  memcpy(cap + p, &c, 4); p += 4;

  send_framed(cap, p);

  // DA frames (from ring, stable because ring_freeze=true)
  uint32_t offset = 0;
  static uint8_t da[2 + 4 + 2 + (2 * CHUNK_SAMPLES) + 4];
  static int16_t tmp[CHUNK_SAMPLES];

  while (offset < TOTAL_SAMPLES) {
    uint16_t nsamp = (uint16_t)min<uint32_t>(CHUNK_SAMPLES, TOTAL_SAMPLES - offset);

    for (uint16_t i = 0; i < nsamp; i++) {
      tmp[i] = cap_sample(cap_start, offset + i);
    }

    size_t q = 0;
    da[q++] = 'D'; da[q++] = 'A';
    memcpy(da + q, &offset, 4); q += 4;
    memcpy(da + q, &nsamp, 2);  q += 2;

    memcpy(da + q, (uint8_t*)tmp, (size_t)nsamp * 2);
    q += (size_t)nsamp * 2;

    c = crc32(da, q);
    memcpy(da + q, &c, 4); q += 4;

    send_framed(da, q);
    offset += nsamp;
    delay(0);
  }
}

// ------------------ Run flow ------------------
static void prompt_next() {
  send_txtf("press %s rep %d/%d then hit enter", label, rep_idx, reps_per_label);
}

static void handle_start(const char* in_label, int reps) {
  if (reps <= 0 || reps > 1000) {
    send_txt("err reps must be 1..1000");
    return;
  }

  memset(label, 0, sizeof(label));
  strncpy(label, in_label, sizeof(label) - 1);
  to_lower_inplace(label);

  reps_per_label = reps;
  rep_idx = 1;

  int ni = 0, oc = 0;
  if (parse_key_label(label, ni, oc)) {
    mode_chromatic = true;
    note_i = ni;
    octave = oc;
    set_label_to_current_key(); // normalized key label
    send_txtf("run start %s reps/key=%d", label, reps_per_label);
  } else {
    mode_chromatic = false;
    send_txtf("run start %s reps=%d", label, reps_per_label);
  }

  run_active = true;
  prompt_next();
}

static void handle_stop() {
  run_active = false;
  send_txt("stopped");
}

// ------------------ Enter trigger (capture + stream) ------------------
static void handle_enter_trigger() {
  if (!run_active) return;

  // Ensure ring is not frozen (shouldn't be), but be safe.
  ring_freeze = false;

  // Snapshot the write head at trigger time
  const uint32_t w0 = ring_write;

  // Wait for post window to arrive, then freeze ring writes
  wait_post_and_freeze(w0);

  // Now the full capture window exists in ring and won't be overwritten.
  const uint32_t cap_start = cap_start_from_w0(w0);

  // stats computed from frozen capture window
  uint32_t clip_count = 0;
  uint16_t max_abs = 0;
  for (uint32_t i = 0; i < TOTAL_SAMPLES; i++) {
    int16_t s = cap_sample(cap_start, i);
    uint16_t a = (uint16_t)abs((int)s);
    if (a > max_abs) max_abs = a;
    if (s == 32767 || s == -32768) clip_count++;
  }

  send_txtf("capture %s rep %d/%d", label, rep_idx, reps_per_label);
  send_capture_frames_from_ring(cap_start, clip_count, max_abs);
  send_txtf("sent %s rep %d/%d", label, rep_idx, reps_per_label);

  // Unfreeze so audio keeps updating ring
  ring_freeze = false;

  // advance
  rep_idx++;
  if (rep_idx > reps_per_label) {
    rep_idx = 1;

    if (mode_chromatic) {
      if (!next_chromatic_key()) {
        run_active = false;
        send_txt("done run");
        return;
      }
    } else {
      run_active = false;
      send_txt("done run");
      return;
    }
  }

  prompt_next();
}

void setup() {
  Serial.begin(UART_BAUD);
  delay(300);

  i2s_init();
  xTaskCreatePinnedToCore(audio_task, "audio", 4096, nullptr, 2, nullptr, 0);

  send_txt("ready");
}

void loop() {
  static String line;

  while (Serial.available()) {
    char c = (char)Serial.read();

    if (c == '\n' || c == '\r') {
      String cmd = line;
      cmd.trim();
      line = "";

      if (cmd.length() == 0) {
        handle_enter_trigger();
        continue;
      }

      String cmd_l = cmd;
      cmd_l.toLowerCase();

      if (cmd_l.startsWith("start ")) {
        char lbl[24] = {0};
        int reps = 0;
        if (sscanf(cmd_l.c_str(), "start %23s %d", lbl, &reps) == 2) {
          handle_start(lbl, reps);
        } else {
          send_txt("err usage: start <label> <reps>");
        }
        continue;
      }

      if (cmd_l == "stop") {
        handle_stop();
        continue;
      }

      send_txt("err unknown cmd");
    } else {
      if (line.length() < 120) line += c;
    }
  }

  delay(2);
}
