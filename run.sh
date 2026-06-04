#!/usr/bin/env bash
# SYN APSE — Conversor LIF: arranque local.
set -euo pipefail

cd "$(dirname "$0")"

if [ ! -d ".venv" ]; then
  echo "[run.sh] creando .venv"
  python3 -m venv .venv
fi

# shellcheck disable=SC1091
source .venv/bin/activate

echo "[run.sh] instalando dependencias"
pip install --upgrade pip >/dev/null
pip install -r requirements.txt

echo "[run.sh] lanzando app"
python app.py
