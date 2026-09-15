#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

if [ ! -d ".venv" ]; then
    echo "Preparing Barcode Benchmark for first use..."
    python3 -m venv .venv
    .venv/bin/python -m pip install --upgrade pip
    .venv/bin/python -m pip install -r requirements.txt
fi

source .venv/bin/activate
echo "Opening Barcode Benchmark at http://127.0.0.1:5000"
OPEN_BROWSER=1 exec python app.py
