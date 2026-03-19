#!/bin/bash
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
source "${DIR}/../config/env.sh" 2>/dev/null || { echo "ERROR: cp config/env.template config/env.sh first"; exit 1; }
echo "=== Deploying AI Bank Foundation ==="
cd "${DIR}/01-aurora" && ./deploy.sh
cd "${DIR}/02-cognito" && ./deploy.sh
echo "=== Foundation complete. Update config/env.sh with output values. ==="
