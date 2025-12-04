#!/usr/bin/env bash
set -euo pipefail

echo "[deploy] Starting ER1 deploy on Pi"

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

echo "[deploy] Repo root: $REPO_ROOT"

BRANCH="$(git rev-parse --abbrev-ref HEAD)"
LAST_COMMIT="$(git log -1 --oneline || echo 'no commits')"

echo "[deploy] On branch: $BRANCH"
echo "[deploy] Last commit: $LAST_COMMIT"

cat <<'TODO'
[deploy] TODO: add real deployment steps here
  - build firmware
  - copy artifacts
  - restart services
TODO

echo "[deploy] Nothing to deploy yet (placeholder)."
echo "[deploy] Done."
