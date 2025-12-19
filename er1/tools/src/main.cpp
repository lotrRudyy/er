// =====================================================
// ESP32 Piano Collector — framed UART (COBS + CRC32)
// =====================================================
//
// Commands (ASCII lines, '\n' or '\r' terminated):
//   start <label> <reps> [clean|stress]
//     - default mode: clean
//     - if <label> parses as piano key (a0..a7 with optional #):
//         chromatic auto-advance in THIS order:
//         a0 a#0 b0 c1 c#1 d1 d#1 e1 f1 f#1 g1 g#1 a1 ... a7
//     - else:
//         fixed-label mode (repeat that label for <reps> then stop)
//   stop
//   <empty line>   (Enter trigger) => capture + stream if run_active and waiting for Enter
//
// Output: ALL framed (no raw Serial println):
//   TXT: "TXT"+ver+len+msg+crc
//   CAP: "CAP"+ver+label(24)+rep+reps+fs+pre/lead/post+flags+total+clip+maxabs+rsv+crc
//   DA : "DA"+offset+nsamp+pcm+crc
//
// Capture window (LOCKED):
//   pre=300ms, lead=0ms, post=400ms @ 48kHz
//   => full raw PCM before and after Enter trigger.
//
// Clean mode (optional):
//   - At run start, capture 2 seconds of silence baseline (RMS)
//   - Before each rep prompt, wait until RMS is below an adaptive threshold
//   - Helps reduce overlap/ringing contamination in template recordings.
//
// Serial robustness:
//   - Dedicated RX task reads Serial continuously and enqueues complete lines
//   - Main loop pops commands when idle and executes sequentially
//   - Pasting redo.txt (multi-line) is safe; commands won't be missed during streaming.
//
// Constraints:
//   - No PSRAM
//   - Keep RAM ring buffer for capture window
//   - Do not stream continuously; only stream per capture (Enter trigger)
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

// ------------------ Run mode + state ------------------
enum RunMode : uint8_t { MODE_CLEAN = 0, MODE_STRESS = 1 };
enum RunState : uint8_t { ST_IDLE = 0, ST_BASELINE = 1, ST_WAIT_QUIET = 2, ST_WAIT_ENTER = 3, ST_CAPTURING = 4 };

static volatile RunMode  run_mode  = MODE_STRESS;
static volatile RunState run_state = ST_IDLE;

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

// ------------------ Command queue (lines) ------------------
static constexpr int CMDQ_MAX = 64;
static constexpr int CMD_MAX_CHARS = 128;

static char cmdq[CMDQ_MAX][CMD_MAX_CHARS];
static volatile int cmdq_head = 0;
static volatile int cmdq_tail = 0;
static volatile int cmdq_count = 0;

static portMUX_TYPE cmdq_mux = portMUX_INITIALIZER_UNLOCKED;

static bool cmdq_push(const char* s) {
  if (!s) return false;
  portENTER_CRITICAL(&cmdq_mux);
  if (cmdq_count >= CMDQ_MAX) {
    portEXIT_CRITICAL(&cmdq_mux);
    return false;
  }
  strncpy(cmdq[cmdq_tail], s, CMD_MAX_CHARS - 1);
  cmdq[cmdq_tail][CMD_MAX_CHARS - 1] = 0;
  cmdq_tail = (cmdq_tail + 1) % CMDQ_MAX;
  cmdq_count++;
  portEXIT_CRITICAL(&cmdq_mux);
  return true;
}

static bool cmdq_pop(char* out, size_t out_sz) {
  if (!out || out_sz == 0) return false;
  portENTER_CRITICAL(&cmdq_mux);
  if (cmdq_count <= 0) {
    portEXIT_CRITICAL(&cmdq_mux);
    return false;
  }
  strncpy(out, cmdq[cmdq_head], out_sz - 1);
  out[out_sz - 1] = 0;
  cmdq_head = (cmdq_head + 1) % CMDQ_MAX;
  cmdq_count--;
  portEXIT_CRITICAL(&cmdq_mux);
  return true;
}

// ------------------ Baseline + quiet gate ------------------
// Baseline collection in audio_task (no extra buffering)
static volatile bool baseline_active = false;
static volatile bool baseline_done = false;
static volatile uint32_t baseline_remaining = 0;
static volatile uint64_t baseline_sumsq = 0;

static uint32_t baseline_target = 0;
static uint16_t quiet_thr_rms_i16 = 0;   // RMS threshold in int16 units
static int quiet_passes = 0;

static constexpr int QUIET_WIN_MS = 100;        // RMS window for gate
static constexpr int QUIET_CHECK_PERIOD_MS = 20;
static constexpr int QUIET_NEED_PASSES = 5;     // require N consecutive passes

static uint32_t last_quiet_check_ms = 0;

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
  for (; *s; s++) {
    if (*s >= 'A' && *s <= 'Z') *s = (char)(*s - 'A' + 'a');
  }
}

static bool streq(const char* a, const char* b) {
  if (!a || !b) return false;
  while (*a && *b) {
    if (*a != *b) return false;
    a++; b++;
  }
  return *a == 0 && *b == 0;
}

// ------------------ Key parsing (a0..a7, a#0..g#7) ------------------
static bool parse_key_label(const char* lbl, int& out_note_i, int& out_octave) {
  // expects lowercase
  // NOTE_SEQ order indexes: a=0, a#=1, b=2, c=3, c#=4, d=5, d#=6, e=7, f=8, f#=9, g=10, g#=11
  if (!lbl || !lbl[0]) return false;

  char n0 = lbl[0];
  if (n0 < 'a' || n0 > 'g') return false;

  bool sharp = (lbl[1] == '#');
  const char* oct_ptr = sharp ? (lbl + 2) : (lbl + 1);
  if (!oct_ptr[0] || oct_ptr[1]) return false; // exactly one digit
  char od = oct_ptr[0];
  if (od < '0' || od > '7') return false;

  char note_buf[3] = {0,0,0};
  note_buf[0] = n0;
  if (sharp) { note_buf[1] = '#'; note_buf[2] = 0; }
  else { note_buf[1] = 0; }

  int idx = -1;
  for (int i = 0; i < NOTE_COUNT; i++) {
    if (strcmp(note_buf, NOTE_SEQ[i]) == 0) { idx = i; break; }
  }
  if (idx < 0) return false;

  out_note_i = idx;
  out_octave = (od - '0');
  return true;
}

static void set_label_to_current_key() {
  // builds label from note_i/octave in normalized format
  char tmp[24] = {0};
  snprintf(tmp, sizeof(tmp), "%s%d", NOTE_SEQ[note_i], octave);
  memset(label, 0, sizeof(label));
  strncpy(label, tmp, sizeof(label)-1);
}

static bool next_chromatic_key() {
  // advance in NOTE_SEQ; octave increments when we roll over from g# to a
  // End at a7 inclusive.
  if (octave > 7) return false;

  int ni = note_i + 1;
  int oc = octave;
  if (ni >= NOTE_COUNT) { ni = 0; oc += 1; }

  // Stop after a7 (note_i=0, octave=7). Next would be a#7, which we don't want.
  if (oc > 7) return false;

  note_i = ni;
  octave = oc;

  // If we've advanced beyond a7? (i.e. octave==7 and note_i>0) => done.
  if (octave == 7 && note_i > 0) return false;

  set_label_to_current_key();
  return true;
}

// ------------------ I2S init ------------------
static void i2s_init() {
  i2s_config_t cfg = {
    .mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_RX),
    .sample_rate = (int)FS_HZ,
    .bits_per_sample = I2S_BITS_PER_SAMPLE_32BIT,
    .channel_format = I2S_CHANNEL_FMT_ONLY_LEFT,
    .communication_format = I2S_COMM_FORMAT_I2S,
    .intr_alloc_flags = ESP_INTR_FLAG_LEVEL1,
    .dma_buf_count = 8,
    .dma_buf_len = 256,
    .use_apll = false,
    .tx_desc_auto_clear = false,
    .fixed_mclk = 0
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

      // Baseline accumulation (no storage)
      if (baseline_active && baseline_remaining > 0) {
        int32_t si = (int32_t)s;
        baseline_sumsq += (uint64_t)(si * (int64_t)si);
        baseline_remaining--;
        if (baseline_remaining == 0) {
          baseline_active = false;
          baseline_done = true;
        }
      }

      if (!ring_freeze) {
        ring[ring_write] = s;
        ring_write = (ring_write + 1) % TOTAL_SAMPLES;
      }
      // if frozen: discard samples but keep draining I2S
    }
  }
}

// ------------------ Serial RX task ------------------
static void serial_rx_task(void*) {
  char line[CMD_MAX_CHARS];
  int len = 0;

  while (true) {
    while (Serial.available()) {
      char c = (char)Serial.read();

      if (c == '\n' || c == '\r') {
        line[len] = 0;

        // trim leading/trailing spaces
        int s = 0;
        while (line[s] == ' ' || line[s] == '\t') s++;
        int e = (int)strlen(line);
        while (e > s && (line[e-1] == ' ' || line[e-1] == '\t')) e--;
        line[e] = 0;

        const char* trimmed = line + s;

        // empty line => ENTER trigger token (stored as empty string)
        if (trimmed[0] == 0) {
          cmdq_push("");
        } else {
          if (!cmdq_push(trimmed)) {
            // Queue full: drop newest (but keep going)
            // We avoid printing raw Serial here; framed TXT is ok.
            send_txt("warn cmdq full, dropped command");
          }
        }

        len = 0;
        continue;
      }

      if (len < (CMD_MAX_CHARS - 1)) {
        line[len++] = c;
      }
    }

    vTaskDelay(1);
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

    // allow other tasks (incl RX task) to run
    delay(0);
  }
}

// ------------------ Quiet gate helpers ------------------
static void baseline_start_2s() {
  baseline_done = false;
  baseline_sumsq = 0;
  baseline_target = FS_HZ * 2; // 2 seconds
  baseline_remaining = baseline_target;
  baseline_active = true;
}

static uint16_t compute_rms_i16_last_window(int win_ms) {
  const uint32_t win_samp = (uint32_t)((FS_HZ * (uint32_t)win_ms) / 1000u);
  if (win_samp < 8) return 0;

  // snapshot head (avoid tearing)
  uint32_t w = ring_write;

  uint64_t ss = 0;
  for (uint32_t i = 0; i < win_samp; i++) {
    uint32_t idx = (w + TOTAL_SAMPLES - 1 - i) % TOTAL_SAMPLES;
    int32_t s = (int32_t)ring[idx];
    ss += (uint64_t)(s * (int64_t)s);
  }

  double mean = (double)ss / (double)win_samp;
  double r = sqrt(mean);
  if (r < 0) r = 0;
  if (r > 32767.0) r = 32767.0;
  return (uint16_t)lround(r);
}

static void prompt_next() {
  send_txtf("press %s rep %d/%d then hit enter", label, rep_idx, reps_per_label);
}

// ------------------ Run flow ------------------
static void begin_run_after_start() {
  run_active = true;
  quiet_passes = 0;
  last_quiet_check_ms = 0;

  if (run_mode == MODE_CLEAN) {
    send_txt("baseline: stay silent for 2 seconds...");
    baseline_start_2s();
    run_state = ST_BASELINE;
  } else {
    run_state = ST_WAIT_ENTER;
    prompt_next();
  }
}

static void reset_run_state() {
  run_active = false;
  mode_chromatic = false;
  run_state = ST_IDLE;
  rep_idx = 1;
  reps_per_label = 0;
}

static void handle_stop() {
  reset_run_state();
  send_txt("stopped");
}

// ------------------ Parse + handle start ------------------
static void handle_start(const char* in_label, int reps, RunMode mode) {
  if (reps <= 0 || reps > 1000) {
    send_txt("err reps must be 1..1000");
    return;
  }

  memset(label, 0, sizeof(label));
  strncpy(label, in_label, sizeof(label) - 1);
  to_lower_inplace(label);

  reps_per_label = reps;
  rep_idx = 1;
  run_mode = mode;

  int ni = 0, oc = 0;
  if (parse_key_label(label, ni, oc)) {
    mode_chromatic = true;
    note_i = ni;
    octave = oc;
    set_label_to_current_key(); // normalized key label
    send_txtf("run start %s reps/key=%d mode=%s", label, reps_per_label, (run_mode == MODE_CLEAN ? "clean" : "stress"));
  } else {
    mode_chromatic = false;
    send_txtf("run start %s reps=%d mode=%s", label, reps_per_label, (run_mode == MODE_CLEAN ? "clean" : "stress"));
  }

  begin_run_after_start();
}

// ------------------ Enter trigger (capture + stream) ------------------
static void handle_enter_trigger() {
  if (!run_active) return;
  if (run_state != ST_WAIT_ENTER) return;

  run_state = ST_CAPTURING;

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
        reset_run_state();
        send_txt("done run");
        return;
      }
    } else {
      reset_run_state();
      send_txt("done run");
      return;
    }
  }

  // Between reps: in clean mode, gate; in stress, prompt immediately.
  if (run_mode == MODE_CLEAN) {
    quiet_passes = 0;
    last_quiet_check_ms = 0;
    send_txt("waiting for quiet...");
    run_state = ST_WAIT_QUIET;
  } else {
    run_state = ST_WAIT_ENTER;
    prompt_next();
  }
}

// ------------------ Execute queued commands ------------------
static void exec_command_line(const char* cmd_in) {
  if (!cmd_in) return;

  // Empty string => Enter trigger token
  if (cmd_in[0] == 0) {
    handle_enter_trigger();
    return;
  }

  // work on a local mutable copy
  char cmd[CMD_MAX_CHARS];
  strncpy(cmd, cmd_in, sizeof(cmd) - 1);
  cmd[sizeof(cmd) - 1] = 0;

  // lowercase for parsing
  to_lower_inplace(cmd);

  // stop
  if (streq(cmd, "stop")) {
    handle_stop();
    return;
  }

  // start <label> <reps> [clean|stress]
  if (strncmp(cmd, "start ", 6) == 0) {
    char lbl[24] = {0};
    int reps = 0;
    char mode_s[16] = {0};

    int n = sscanf(cmd, "start %23s %d %15s", lbl, &reps, mode_s);
    if (n < 2) {
      send_txt("err usage: start <label> <reps> [clean|stress]");
      return;
    }

    RunMode m = MODE_CLEAN; // default
    if (n >= 3) {
      if (strcmp(mode_s, "stress") == 0) m = MODE_STRESS;
      else if (strcmp(mode_s, "clean") == 0) m = MODE_CLEAN;
      else {
        send_txt("err mode must be clean or stress");
        return;
      }
    }

    handle_start(lbl, reps, m);
    return;
  }

  send_txt("err unknown cmd");
}

// ------------------ Clean-mode state ticks ------------------
static void tick_baseline() {
  if (!run_active || run_mode != MODE_CLEAN) return;
  if (run_state != ST_BASELINE) return;

  if (!baseline_done) {
    // still collecting; yield
    delay(2);
    return;
  }

  // Compute baseline RMS (int16 units)
  baseline_done = false;
  uint64_t ss = baseline_sumsq;
  uint32_t n = baseline_target;
  if (n == 0) n = 1;

  double mean = (double)ss / (double)n;
  double rms = sqrt(mean);
  if (rms < 0) rms = 0;

  uint16_t rms_i16 = (uint16_t)min<double>(32767.0, rms);

  // Adaptive threshold:
  //   thr = rms * 3 + 50 counts
  // This is intentionally simple + deterministic.
  double thr = (double)rms_i16 * 3.0 + 50.0;
  if (thr > 32767.0) thr = 32767.0;
  quiet_thr_rms_i16 = (uint16_t)lround(thr);

  send_txtf("baseline ok: rms_i16=%u thr_i16=%u", (unsigned)rms_i16, (unsigned)quiet_thr_rms_i16);

  // Now gate before the first prompt
  quiet_passes = 0;
  last_quiet_check_ms = 0;
  send_txt("waiting for quiet...");
  run_state = ST_WAIT_QUIET;
}

static void tick_quiet_gate() {
  if (!run_active || run_mode != MODE_CLEAN) return;
  if (run_state != ST_WAIT_QUIET) return;

  // Debounced periodic checks
  uint32_t now = millis();
  if (last_quiet_check_ms != 0 && (now - last_quiet_check_ms) < (uint32_t)QUIET_CHECK_PERIOD_MS) {
    delay(2);
    return;
  }
  last_quiet_check_ms = now;

  uint16_t rms_i16 = compute_rms_i16_last_window(QUIET_WIN_MS);

  // If threshold not set for some reason, be conservative.
  uint16_t thr = quiet_thr_rms_i16;
  if (thr == 0) thr = 200;

  if (rms_i16 <= thr) {
    quiet_passes++;
  } else {
    quiet_passes = 0;
  }

  if (quiet_passes >= QUIET_NEED_PASSES) {
    quiet_passes = 0;
    run_state = ST_WAIT_ENTER;
    prompt_next();
    return;
  }

  // occasional progress ping
  static uint32_t last_ping = 0;
  if (now - last_ping > 1000) {
    last_ping = now;
    send_txtf("quiet gate: rms_i16=%u thr_i16=%u", (unsigned)rms_i16, (unsigned)thr);
  }

  delay(2);
}

// ------------------ Setup/loop ------------------
void setup() {
  Serial.begin(UART_BAUD);
  delay(300);

  i2s_init();
  xTaskCreatePinnedToCore(audio_task, "audio", 4096, nullptr, 2, nullptr, 0);

  // Serial RX task (higher priority than loop)
  xTaskCreatePinnedToCore(serial_rx_task, "ser_rx", 4096, nullptr, 3, nullptr, 1);

  send_txt("ready");
}

void loop() {
  // Clean-mode state machine ticks
  tick_baseline();
  tick_quiet_gate();

  // Execute queued commands sequentially.
  // We still allow processing while waiting for baseline/quiet/etc.
  // Only block command execution during CAPTURING (handle_enter_trigger will set/clear it).
  if (run_state != ST_CAPTURING) {
    char cmd[CMD_MAX_CHARS];
    // Process a small batch each loop to stay responsive
    for (int i = 0; i < 8; i++) {
      if (!cmdq_pop(cmd, sizeof(cmd))) break;

      // If we are waiting for Enter, only ENTER token should trigger capture.
      // But allow stop/start at any time.
      if (cmd[0] == 0) {
        // ENTER token
        if (run_state == ST_WAIT_ENTER) {
          exec_command_line(cmd);
        } else {
          // ignore stray empty lines when not prompted
        }
      } else {
        exec_command_line(cmd);
      }

      // If a command caused capturing, stop this batch immediately.
      if (run_state == ST_CAPTURING) break;
    }
  }

  delay(2);
}
