#!/usr/bin/env bash
# The self-service app's virtualenv, its database (migrations) and first-time setup (groups, the admin account). Idempotent.
set -euo pipefail
here="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
[[ -x "$here/.venv/bin/python" ]] || python3 -m venv "$here/.venv"
"$here/.venv/bin/pip" install -q --upgrade pip
"$here/.venv/bin/pip" install -q -r "$here/requirements.txt"
cd "$here" && .venv/bin/python manage.py migrate --noinput -v 0 && .venv/bin/python manage.py collectstatic --noinput -v 0 && .venv/bin/python manage.py e3t_setup
echo "selfservice/.venv ready: Django $(.venv/bin/python -c 'import django; print(django.get_version())')"
