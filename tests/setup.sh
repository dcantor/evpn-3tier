#!/usr/bin/env bash
# The lab's Python environment (netmiko / paramiko for the routers, Robot Framework for the tests).
set -euo pipefail
cd "$(dirname "$0")"
python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt
echo "tests/.venv ready"
