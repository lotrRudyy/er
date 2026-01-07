#include "../include/fw_build_id.h"

#include "fw_build_meta.h"

// Allow runtime override (e.g. test builds) without realloc.
static char kBuildIdOverride[32] = {0};

const char* fwBuildId() {
  if (kBuildIdOverride[0] != '\0') {
    return kBuildIdOverride;
  }
  return FW_BUILD_ID_STR;
}
