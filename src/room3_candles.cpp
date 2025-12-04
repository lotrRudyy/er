#include <Arduino.h>
#include <SPI.h>
#include <Ethernet.h>
#include <PubSubClient.h>

#ifndef ETH_SPI_SCK
#define ETH_SPI_SCK 18
#endif
#ifndef ETH_SPI_MISO
#define ETH_SPI_MISO 19
#endif
#ifndef ETH_SPI_MOSI
#define ETH_SPI_MOSI 23
#endif
#ifndef ETH_CS
#define ETH_CS 15
#endif
#ifndef ETH_RST
#define ETH_RST 2
#endif

static uint8_t MAC[6];
static void genMac(uint8_t out[6]) {
  uint64_t id = ESP.getEfuseMac();
  out[0]=0x02; out[1]=0x00;
  out[2]=(id>>32)&0xFF; out[3]=(id>>24)&0xFF;
  out[4]=(id>>16)&0xFF; out[5]=(id>>8)&0xFF;
}

IPAddress mqttHost(192,168,4,1);
EthernetClient ethClient;
PubSubClient mqtt(ethClient);

static const char* ROOT = "esc";
static const char* ROOM = "room3";
static const char* DEV  = "candles";
String topic_hb  = String(ROOT)+"/"+ROOM+"/"+DEV+"/hb";
String topic_evt = String(ROOT)+"/"+ROOM+"/"+DEV+"/event";
String topic_cmd = String(ROOT)+"/"+ROOM+"/"+DEV+"/cmd";

unsigned long lastHb = 0;

void onCmd(char*, byte*, unsigned int) { }

static void setupEthernet(){
  pinMode(ETH_RST, OUTPUT);
  digitalWrite(ETH_RST, LOW); delay(5);
  digitalWrite(ETH_RST, HIGH); delay(50);
  SPI.begin(ETH_SPI_SCK, ETH_SPI_MISO, ETH_SPI_MOSI, ETH_CS);
  Ethernet.init(ETH_CS);
  genMac(MAC);
  if (Ethernet.begin(MAC) == 0) {
    IPAddress ip(192,168,0,15), dns(0,0,0,0), gw(0,0,0,0), sn(255,255,255,0);
    Ethernet.begin(MAC, ip, dns, gw, sn);
  }
}

static void ensureMqtt(){
  if (mqtt.connected()) return;
  mqtt.setServer(mqttHost, 1883);
  mqtt.setCallback(onCmd);
  mqtt.setKeepAlive(20);
  mqtt.setSocketTimeout(8);
  mqtt.setBufferSize(512);

  String cid = String("er1-")+ROOM+"-"+DEV+"-"+String((uint32_t)ESP.getEfuseMac(),HEX);
  while (!mqtt.connected()) {
    if (mqtt.connect(cid.c_str(), topic_hb.c_str(), 0, true, "offline")) {
      mqtt.subscribe(topic_cmd.c_str());
      String hb = String("{\"online\":true,\"uptime\":") + (millis()/1000) +
                  ",\"ip\":\"" + Ethernet.localIP().toString() + "\"}";
      mqtt.publish(topic_hb.c_str(), hb.c_str(), true);
    } else { delay(500); }
  }
}

void setup(){
  Serial.begin(115200);
  delay(200);
  setupEthernet();
  ensureMqtt();
  // TODO: init hardware for this node
}

void loop(){
  mqtt.loop();
  if (!mqtt.connected()) ensureMqtt();

  unsigned long now = millis();
  if (now - lastHb > 5000) {
    lastHb = now;
    String hb = String("{\"online\":true,\"uptime\":") + (now/1000) +
                ",\"ip\":\"" + Ethernet.localIP().toString() + "\"}";
    mqtt.publish(topic_hb.c_str(), hb.c_str(), true);
  }

  // TODO: core logic; on success:
  // mqtt.publish(topic_evt.c_str(), "{\"type\":\"SOLVED\"}", false);
}
