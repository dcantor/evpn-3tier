#!/usr/bin/env bash
# Run the Robot Framework suites against the lab.
#   tests/run.sh [robot options...]      e.g.  tests/run.sh --exclude disruptive      tests/run.sh suites/06_failures.robot
# Every run gets its own folder results/YYYY-MM-DD_HH-MM-SS/ (and results/latest -> it, which the Lab Hub reads):
#   configs/pre-run/, configs/post-run/   every VyOS node's configuration (+ state/: routing and service state)
#   configs/pre-vs-post.diff              what the run changed in the configurations (empty = nothing)
#   log.html, report.html, output.xml     Robot Framework results
# Suites tagged `disruptive` power nodes off and back on; `--exclude disruptive` runs only the read-only checks (~3 min).
set -uo pipefail
cd "$(dirname "$(readlink -f "$0")")"
ROBOT="${E3T_ROBOT:-$PWD/.venv/bin/robot}"; PY="${E3T_PYTHON:-$PWD/.venv/bin/python}"
[[ -x "$ROBOT" ]] || { echo "error: run tests/setup.sh first (or set E3T_ROBOT)" >&2; exit 1; }
exec 9>/tmp/evpn-3tier-test.lock          # one run at a time: the failure suites would fail each other
flock -n 9 || { echo "==> another test run holds /tmp/evpn-3tier-test.lock — waiting for it"; flock 9; }
ts="$(date +%Y-%m-%d_%H-%M-%S)"; out="$(cd .. && pwd)/results/$ts"; mkdir -p "$out/configs"
echo "==> results: $out"
echo "==> capturing configurations and state (pre-run)"; "$PY" capture.py "$out/configs/pre-run" >/dev/null || echo "warning: capture failed" >&2
args=("$@"); explicit=0; for x in "$@"; do [[ "$x" == *.robot ]] && explicit=1; done; [[ $explicit -eq 1 ]] || args+=(suites/)
"$ROBOT" --outputdir "$out" --name "evpn-3tier lab" --loglevel INFO --pythonpath resources --pythonpath ../tools "${args[@]}"
rc=$?
echo "==> capturing configurations and state (post-run)"; "$PY" capture.py "$out/configs/post-run" >/dev/null || echo "warning: capture failed" >&2
diff -ru -x state -I '^# .* captured ' "$out/configs/pre-run" "$out/configs/post-run" > "$out/configs/pre-vs-post.diff" \
  && echo "    no configuration changes during the run" || echo "    configuration changed during the run, see configs/pre-vs-post.diff"
ln -sfn "$(basename "$out")" ../results/latest
echo "==> report: $out/report.html  (rc=$rc)"
exit $rc
