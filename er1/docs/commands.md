# MQTT Quick Commands

Log format: `[DD.MM.YYYY HH:MM:SS.mmm] topic payload` (date + ms). Logfile pattern on Pi: `/home/rudyy/er1/logs/er1-DD.MM.YYYY.log`.

## Local Broker (127.0.0.1)

### Publish

```
mosquitto_pub -h 127.0.0.1 -t 'esc/ctrl/lock/images/cmd' -m "OPEN"
```

### Subscribe (all topics)

```
./scripts/mqtt-logs.sh live
```

## Tailscale Broker (100.108.1.80)

### Publish

```
mosquitto_pub -h 100.108.1.80 -t 'esc/ctrl/lock/images/cmd' -m "OPEN"
```
