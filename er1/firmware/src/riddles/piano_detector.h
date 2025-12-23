#pragma once

#include <Arduino.h>

extern "C" void piano_detector_on_result(int accepted, const char* pred, float s1, float s2, float margin,
                                         float hps_ratio, int harmonic_ok, const char* t1, float t1s,
                                         const char* t2, float t2s, const char* t3, float t3s);

void piano_detector_setup();
void piano_detector_loop_once();
