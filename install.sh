#!/usr/bin/env bash
# Install runstate and verify the installation.
set -euo pipefail

cd "$(dirname "$0")"

echo "==> installing runstate"
python3 -m pip install .

echo "==> running doctor"
runstate doctor
