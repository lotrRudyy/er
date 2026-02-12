#include "../include/fw_build_id.h"

#include <array>
#include <cstdint>

// If present, this generated header (written by ota.ps1) provides
// FW_BUILD_ID_STR / FW_BUILD_SEED_STR for deterministic OTA verification.
// It lives in src/ so we include it opportunistically.
#if defined(__has_include)
#  if __has_include("fw_build_meta.h")
#    include "fw_build_meta.h"
#  endif
#endif

#ifndef FW_BUILD_SEED
#define FW_BUILD_SEED ""
#endif

#ifndef FW_BUILD_ID
#define FW_BUILD_ID ""
#endif

namespace {

// Priority for OTA verification id:
//  1) Explicit build-flag override: -DFW_BUILD_ID="..."
//  2) Generated header: FW_BUILD_ID_STR (written by ota.ps1)
//  3) Empty -> fall back to seeded compile-time id
constexpr const char* kBuildIdOverride =
#if (sizeof(FW_BUILD_ID) > 1)
    FW_BUILD_ID
#elif defined(FW_BUILD_ID_STR) && (sizeof(FW_BUILD_ID_STR) > 1)
    FW_BUILD_ID_STR
#else
    ""
#endif
    ;

constexpr const char* kAlphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789";
constexpr std::size_t kAlphabetLen = 36;
constexpr std::size_t kBuildIdLen = 20;

// Choose the seed: use FW_BUILD_SEED when provided, otherwise generated header seed,
// otherwise compile timestamp.
constexpr const char* buildSeed() {
#if defined(FW_BUILD_SEED_STR) && (sizeof(FW_BUILD_SEED_STR) > 1)
  return (FW_BUILD_SEED[0] != '\0') ? FW_BUILD_SEED : FW_BUILD_SEED_STR;
#else
  return (FW_BUILD_SEED[0] != '\0') ? FW_BUILD_SEED : (__DATE__ " " __TIME__);
#endif
}

constexpr uint64_t fnv1a64(const char* str) {
  uint64_t hash = 14695981039346656037ULL;
  for (std::size_t i = 0; str[i] != '\0'; ++i) {
    hash ^= static_cast<uint64_t>(static_cast<unsigned char>(str[i]));
    hash *= 1099511628211ULL;
  }
  return hash;
}

constexpr uint64_t nextState(uint64_t state) {
  return state * 6364136223846793005ULL + 1ULL;
}

template <std::size_t N>
struct BuildId {
  std::array<char, N + 1> chars{};
  constexpr BuildId() : chars{} {
    uint64_t state = fnv1a64(buildSeed());
    for (std::size_t i = 0; i < N; ++i) {
      state = nextState(state);
      chars[i] = kAlphabet[state % kAlphabetLen];
    }
    chars[N] = '\0';
  }
};

constexpr BuildId<kBuildIdLen> kBuildId{};

}  // namespace

const char* fwBuildId() {
  if (kBuildIdOverride[0] != '\0') {
    return kBuildIdOverride;
  }
  return kBuildId.chars.data();
}
