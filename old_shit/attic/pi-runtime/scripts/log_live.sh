#!/usr/bin/env bash
set -euo pipefail

cd "$HOME/er1"
./scripts/mqtt-logs.sh live
