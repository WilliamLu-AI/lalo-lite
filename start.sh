#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if [[ ! -x .venv/bin/python ]]; then
  echo 'Install the independent Python environment first; see README.md.' >&2
  exit 1
fi
if [[ ! -f web/app.bundle.js ]]; then npm run build; fi
exec .venv/bin/python lalo.py
