#pragma once
#include "net.hpp"
namespace Core { inline void tick(){ loopNet(); loopTelemetry(); } }