mosquitto_pub -h 192.168.0.10 -t 'esc/ctrl/lock/images/cmd' -m "OPEN"

mosquitto_sub -h 192.168.0.10 -t 'esc/#' -v | awk '{ ... }'

mosquitto_sub -h 192.168.0.10 -t 'esc/room0/maglock_ctrl/#' -v | ts
mosquitto_sub -h 192.168.0.10 -t 'esc/room1/images_piano/#' -v | ts
...
mosquitto_sub -h 192.168.0.10 -t 'esc/room3/star_slider/#' -v | ts



mosquitto_pub -h 100.108.1.80 -t 'esc/ctrl/lock/images/cmd' -m "OPEN"

mosquitto_sub -h 100.108.1.80 -t 'esc/#' -v | awk '{ ... }'

mosquitto_sub -h 100.108.1.80 -t 'esc/room0/maglock_ctrl/#' -v | ts
mosquitto_sub -h 100.108.1.80 -t 'esc/room1/images_piano/#' -v | ts
...
mosquitto_sub -h 100.108.1.80 -t 'esc/room3/star_slider/#' -v | ts
