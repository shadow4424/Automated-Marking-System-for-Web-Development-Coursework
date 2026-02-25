#!/usr/bin/env bash
# Build the AMS sandbox Docker image.
# Usage:  ./docker/build.sh [--no-cache]
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
docker build \
    -f "$SCRIPT_DIR/Dockerfile.sandbox" \
    -t ams-sandbox:latest \
    "$@" \
    "$SCRIPT_DIR"
echo "[ams] Docker image ams-sandbox:latest built successfully."
