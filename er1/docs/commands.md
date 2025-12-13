# Discover commands
er1 help

# Logs
er1 log                # tail today (all devices)
er1 log knocking -n 400
er1 log knocking -live
er1 log knocking -errors
er1 log images_piano --save
er1 log -live --save

# OTA
er1 ota knocking
er1 ota images_piano

# Deploy Pi runtime
er1 deploy

# Maglocks
er1 lock images
er1 lock door_to_r2
er1 lock door_to_r3
er1 lock slider
er1 lock knocking
er1 lock-all

# Misc
er1 mqtt-status
er1 mqtt-restart
er1 syslog
er1 pi              # SSH to Pi
er1 push "msg"      # git add/commit/push from repo root
er1 commit "msg"    # legacy alias for er1 push
