# SpotLED GUI

Desktop editor for SpotLED animations built with PySide6. The app lets you create and preview frame‑based LED animations before sending them to SpotLED hardware via BLE.

## Features
- Pixel editor with drawing, erasing, clearing, and whole-frame shifting tools.
- Frame timeline with add/remove, previous/next navigation, and copy-from-previous shortcuts.
- Undo/redo history per frame for quick corrections.
- Animation options for built-in SpotLED effects and playback speed.
- Text mode with optional two-line display and effect controls.
- Project save/load to JSON files plus direct BLE upload to SpotLED devices.

## Technology
- Python 3
- [PySide6](https://doc.qt.io/qtforpython/) for the Qt-based GUI
- [python-spotled](https://github.com/iwalton3/python-spotled) for communicating with SpotLED hardware

## Installation

```bash
git clone https://github.com/ordigital/spotled-gui.git
cd spotled-gui

# (optional) create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate 

# install required packages
pip install PySide6 python-spotled
```

## Running

```bash
python spotled_gui.py
```

The GUI opens immediately. Make sure your SpotLED device is powered on and reachable over BLE before using the send functionality.
