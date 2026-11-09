#!/usr/bin/env bash
# Build the front end into dist/ (served by Django at /static/app/). Node: ~/.local/share/node (tools: see README).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"; cd "$HERE"
export PATH="$HOME/.local/share/node/bin:$PATH"
command -v node >/dev/null || "$HERE/../../tools/install_node.sh"
[[ -d node_modules && node_modules/.package-lock.json -nt package-lock.json ]] || npm ci --no-audit --no-fund
npm run build
