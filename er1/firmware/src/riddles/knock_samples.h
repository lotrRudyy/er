#pragma once

#include <stdint.h>
#include <stddef.h>

static constexpr uint32_t KNOCK_SAMPLE_RATE = 22050;
static constexpr size_t KNOCK_SAMPLE_LEN = 2577;

extern const int16_t knock1_pcm[KNOCK_SAMPLE_LEN];
extern const int16_t knock2_pcm[KNOCK_SAMPLE_LEN];
extern const int16_t knock3_pcm[KNOCK_SAMPLE_LEN];
