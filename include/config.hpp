#pragma once
#include <Arduino.h>
#include <IPAddress.h>

// ---------- Network / MQTT ----------
static const char* NET_HOSTNAME_PREFIX = "er1-";    // used for client ID
static const IPAddress MQTT_BROKER(192, 168, 4, 1); // your MQTT broker (Raspberry Pi)
static const uint16_t MQTT_PORT = 1883;
static const char MQTT_USER[] = "";
static const char MQTT_PASS[] = "";
static const char* TOPIC_ROOT = "esc";              // root for all topics

// ---------- Cadence (defined also in build_flags) ----------
#ifndef HB_INTERVAL_MS
#define HB_INTERVAL_MS 7000
#endif
#ifndef METRIC_INTERVAL_MS
#define METRIC_INTERVAL_MS 60000
#endif

// ---------- Reconnect / Timeouts ----------
static const uint32_t RECONNECT_BACKOFF_MS = 3000;  // retry MQTT every 3s
static const uint32_t ETHERNET_REINIT_MS   = 10000; // optional periodic reset interval

// ---------- Default Names (overridden per env) ----------
#ifndef ROOM
#define ROOM "undefined_room"
#endif
#ifndef DEVICE
#define DEVICE "undefined_device"
#endif
