#!/bin/bash
cd "$(dirname -- "$(readlink -f -- "${BASH_SOURCE[0]}")")"
echo "Installing deps…"
apt install pkg-config libglib2.0-dev \
    libboost-python-dev python3-gattlib libbluetooth-dev \
    bluez build-essential python3.12-dev \
    libboost-thread-dev libboost-python-dev \
    libglib2.0-dev libbluetooth-dev pkg-config \
    libxcb-cursor0
echo "Creating virtual environment…"
python3 -m venv venv
echo "Activating venv…"
source ./venv/bin/activate
echo "Installing packages…"
pip install PySide6 spotled
echo "Starting SpotLED GUI…"
python3 ./spotled_gui.py
