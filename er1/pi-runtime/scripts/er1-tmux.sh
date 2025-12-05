#!/usr/bin/env bash
set -euo pipefail

SESSION_NAME="er1"
WORKDIR="/home/rudyy/er1"

if ! command -v tmux >/dev/null 2>&1; then
  echo "tmux is not installed. Please install tmux on the Pi to use this workflow." >&2
  exit 1
fi

if tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
  exec tmux attach -t "$SESSION_NAME"
fi

tmux new-session -d -s "$SESSION_NAME" -c "$WORKDIR" "cd $WORKDIR; clear"
tmux set-option -g allow-rename off
tmux rename-window -t "$SESSION_NAME:0" "er1"
tmux select-pane -t "$SESSION_NAME:0.0" -T "er1-main"

tmux split-window -h -t "$SESSION_NAME:0" -c "$WORKDIR"
tmux select-pane -t "$SESSION_NAME:0.1" -T "er1-pi-log-mqtt"
tmux send-keys -t "$SESSION_NAME:0.1" "cd $WORKDIR; scripts/mqtt-logs.sh live" C-m

tmux split-window -v -t "$SESSION_NAME:0.0" -c "$WORKDIR" "cd $WORKDIR; bash"
tmux select-pane -t "$SESSION_NAME:0.2" -T "er1-pi-log-grep"
tmux send-keys -t "$SESSION_NAME:0.2" "scripts/mqtt-logs.sh grep ERROR"

tmux select-layout -t "$SESSION_NAME" tiled
tmux select-pane -t "$SESSION_NAME:0.0"
tmux attach -t "$SESSION_NAME"
