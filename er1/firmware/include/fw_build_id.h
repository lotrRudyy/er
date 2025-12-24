// Build ID helper: produces a deterministic 20-char ID from a seed string.
// Seed defaults to the compile timestamp (__DATE__ __TIME__) unless
// FW_BUILD_SEED is provided via build flags/environment.
#pragma once

// Returns the 20-character build id string (null-terminated).
const char* fwBuildId();
