#pragma once
#include <Arduino.h>
#include <SPI.h>
#include <Ethernet.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include <Update.h>
#include "config.hpp"
#include <functional>
#include <core_state.hpp>

namespace Core {
extern EthernetClient ethClient;
extern PubSubClient mqtt;
void setIdentity(const char* room, const char* device);
void beginNet();
void loopNet();
bool mqttPublish(const String& topic, const String& payload, bool retained=false, int qos=0);
bool mqttPublishJson(const String& topic, JsonDocument& doc, bool retained=false, int qos=0);
void loopTelemetry();
using CmdHandler = ::std::function<void(const String& cmd, const String& arg)>;
void setCmdHandler(CmdHandler h);
String topic(const String& leaf);
String idRoom();
String idDevice();
bool otaUpdateFromURL(const String& url);
}
