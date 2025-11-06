#!/bin/bash
cd "$(dirname -- "$(readlink -f -- "${BASH_SOURCE[0]}")")"
echo "Creating virtual environment…"
python3 -m venv venv
echo "Activating venv…"
source ./venv/bin/activate
echo "Installing packages…"
pip install PySide6 python-spotled
echo "Starting SpotLED GUI…"
python3 ./spotled_gui.py
