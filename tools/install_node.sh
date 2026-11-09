#!/usr/bin/env bash
# Node.js LTS for building the self-service front end (selfservice/frontend) — into ~/.local/share/node, no root.
# The tarball is checked against nodejs.org's SHASUMS256.txt.   tools/install_node.sh [version]
set -euo pipefail
V="${1:-v24.11.1}"; D="$HOME/.local/share/node"; T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
[[ -x "$D/bin/node" && "$("$D/bin/node" -v)" == "$V" ]] && { echo "node $V already in $D"; exit 0; }
cd "$T"
curl -sfO "https://nodejs.org/dist/$V/node-$V-linux-x64.tar.xz"
curl -sfO "https://nodejs.org/dist/$V/SHASUMS256.txt"
grep " node-$V-linux-x64.tar.xz\$" SHASUMS256.txt | sha256sum -c -
rm -rf "$D"; mkdir -p "$D"; tar -xJf "node-$V-linux-x64.tar.xz" -C "$D" --strip-components=1
echo "node $("$D/bin/node" -v) in $D"
