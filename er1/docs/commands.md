# MQTT MQTT Quick Commands

All subscription snippets follow the canonical pipeline:

```
mosquitto_sub -h <broker> -t '<topic>' -v \
  | ts '[%d.%m.%Y %H:%M:%S.%N]' \
  | sed -E 's/([0-9]{3})[0-9]{6}]/\1]/'
```

## Local Broker (100.108.1.80)

### Publish

```
mosquitto_pub -h 100.108.1.80 -t 'esc/ctrl/lock/images/cmd' -m "OPEN"
```

### Subscribe (all topics)

```
mosquitto_sub -h 100.108.1.80 -t 'esc/#' -v \
  | ts '[%d.%m.%Y %H:%M:%S.%N]' \
  | sed -E 's/([0-9]{3})[0-9]{6}]/\1]/'
```

## Tailscale Broker (100.108.1.80)

### Publish

```
mosquitto_pub -h 100.108.1.80 -t 'esc/ctrl/lock/images/cmd' -m "OPEN"
```
