#!/usr/bin/env bash
set -e
echo "Resetting repo..."
rm -rf .git .pio .vscode
git init
git add .
git commit -m "init: clean ER1 skeleton"
echo "Done."
