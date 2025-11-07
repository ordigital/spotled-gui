#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import copy
import html
import json
import os
import re
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass
from typing import List, Optional, Tuple

from collections import OrderedDict

from PySide6.QtCore import Qt, QSize, QRect, QRectF, Signal, QObject, QTimer, QPointF
from PySide6.QtGui import QPainter, QPen, QBrush, QColor, QIcon, QPixmap, QImage, QTextDocument, QPalette
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QComboBox, QTabWidget, QSlider, QLineEdit, QMessageBox, QToolButton,
    QSizePolicy, QCheckBox, QFileDialog, QStyledItemDelegate, QStyle, QStyleOptionViewItem
)

# pip install python-spotled
try:
    import spotled
except Exception:
    spotled = None

GRID_W, GRID_H = 48, 12
CELL = 16
CFG_PATH = os.path.join(os.path.expanduser("~"), ".spotled_gui.json")
FONT_ID_BUILTIN = "__builtin__"
BLE_SCAN_TIMEOUT = 6
SPOTLED_NAME_PREFIX = "SPOTLED_"
BT_DEVICE_RE = re.compile(r"Device\s+([0-9A-Fa-f:]{17})\s+(.+)")

@dataclass
class Tool:
    DRAW = 1
    SHIFT = 2

def load_cfg():
    if os.path.exists(CFG_PATH):
        try:
            with open(CFG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                if "mac_history" not in data:
                    data["mac_history"] = []
                if "project_dir" not in data:
                    data["project_dir"] = ""
                if "selected_font" not in data:
                    data["selected_font"] = FONT_ID_BUILTIN
                return data
        except Exception:
            pass
    return {"mac_history": [], "project_dir": "", "selected_font": FONT_ID_BUILTIN}

def save_cfg(cfg):
    try:
        with open(CFG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

def bitmap_string_from_pixels(pxs: List[List[bool]]) -> str:
    return ''.join(''.join('1' if pxs[y][x] else '.' for x in range(GRID_W)) for y in range(GRID_H))

class PixelGrid(QWidget):
    changed = Signal()  # signal emitted when a pixel changes
    action_started = Signal()
    action_finished = Signal()
    placement_confirmed = Signal()
    placement_canceled = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.tool = Tool.DRAW
        self.px: List[List[bool]] = [[False for _ in range(GRID_W)] for _ in range(GRID_H)]
        self.prev_px: Optional[List[List[bool]]] = None
        self.setFixedSize(GRID_W * CELL + 1, GRID_H * CELL + 1)
        self._mouse_down = False
        self._action_active = False
        self._shift_start_cell = None
        self._shift_source = None
        self._shift_last_delta = (0, 0)
        self._placement_active = False
        self._placement_pixels: Optional[List[List[bool]]] = None
        self._placement_width = 0
        self._placement_height = 0
        self._placement_offset = (0, 0)
        self._placement_drag_start = None
        self._placement_offset_start = (0, 0)
        self._placement_dragged = False
        self._placement_base: Optional[List[List[bool]]] = None
        self._mouse_button = None
        self._update_cursor()

    def sizeHint(self) -> QSize:
        return QSize(GRID_W * CELL + 1, GRID_H * CELL + 1)

    def setTool(self, tool):
        self.tool = tool
        if self.tool != Tool.SHIFT:
            self._shift_source = None
            self._shift_last_delta = (0, 0)
        self._update_cursor()
        self.update()

    def clearAll(self):
        for y in range(GRID_H):
            for x in range(GRID_W):
                self.px[y][x] = False
        self.update()
        self.changed.emit()

    def invertAll(self):
        if self._placement_active:
            return
        for y in range(GRID_H):
            for x in range(GRID_W):
                self.px[y][x] = not self.px[y][x]
        self.update()
        self.changed.emit()

    def mirrorHorizontal(self):
        if self._placement_active:
            return
        for y in range(GRID_H):
            self.px[y] = list(reversed(self.px[y]))
        self.update()
        self.changed.emit()

    def mirrorVertical(self):
        if self._placement_active:
            return
        self.px = list(reversed(self.px))
        self.update()
        self.changed.emit()

    def setPixels(self, pxs: List[List[bool]]):
        # deep copy so editing does not mutate the source list
        if self._placement_active:
            self._placement_active = False
            self._placement_pixels = None
            self._placement_base = None
            self.placement_canceled.emit()
            self._update_cursor()
        self.px = copy.deepcopy(pxs)
        self.update()

    def setReferencePixels(self, pxs: Optional[List[List[bool]]]):
        self.prev_px = copy.deepcopy(pxs) if pxs is not None else None
        self.update()

    def getPixelsCopy(self) -> List[List[bool]]:
        return copy.deepcopy(self.px)

    def _apply_at(self, x, y, value: bool):
        if self._placement_active:
            return
        if 0 <= x < GRID_W and 0 <= y < GRID_H:
            if self.px[y][x] != value:
                self.px[y][x] = value
                self.update(QRect(x*CELL, y*CELL, CELL, CELL))
                self.changed.emit()

    def mousePressEvent(self, e):
        if e.button() == Qt.RightButton:
            if self._placement_active:
                return
            self._mouse_down = True
            self._mouse_button = Qt.RightButton
            if not self._action_active:
                self._action_active = True
                self.action_started.emit()
            x = int(e.position().x() // CELL)
            y = int(e.position().y() // CELL)
            self._apply_at(x, y, False)
            return

        if e.button() == Qt.LeftButton:
            if self._placement_active:
                self._mouse_down = True
                self._mouse_button = Qt.LeftButton
                cell_x = int(e.position().x() // CELL)
                cell_y = int(e.position().y() // CELL)
                self._placement_drag_start = (cell_x, cell_y)
                self._placement_offset_start = self._placement_offset
                self._placement_dragged = False
                return
            self._mouse_down = True
            self._mouse_button = Qt.LeftButton
            if not self._action_active:
                self._action_active = True
                self.action_started.emit()
            x = int(e.position().x() // CELL)
            y = int(e.position().y() // CELL)
            if self.tool == Tool.SHIFT:
                self._shift_start_cell = (x, y)
                self._shift_source = self.getPixelsCopy()
                self._shift_last_delta = (0, 0)
            else:
                self._apply_at(x, y, True)

    def mouseMoveEvent(self, e):
        if self._placement_active:
            if self._mouse_down and self._placement_drag_start is not None:
                x = int(e.position().x() // CELL)
                y = int(e.position().y() // CELL)
                dx = x - self._placement_drag_start[0]
                dy = y - self._placement_drag_start[1]
                if dx or dy:
                    self._placement_dragged = True
                self._set_placement_offset(
                    self._placement_offset_start[0] + dx,
                    self._placement_offset_start[1] + dy
                )
                self.update()
            return

        if self._mouse_down:
            x = int(e.position().x() // CELL)
            y = int(e.position().y() // CELL)
            if self._mouse_button == Qt.RightButton:
                self._apply_at(x, y, False)
            elif self.tool == Tool.SHIFT and self._shift_source is not None and self._shift_start_cell is not None:
                dx = x - self._shift_start_cell[0]
                dy = y - self._shift_start_cell[1]
                self._apply_shift(dx, dy)
            else:
                self._apply_at(x, y, True)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.RightButton and self._mouse_button == Qt.RightButton:
            self._mouse_down = False
            self._mouse_button = None
            if self._action_active:
                self._action_active = False
                self.action_finished.emit()
            return

        if e.button() == Qt.LeftButton:
            if self._placement_active:
                self._mouse_down = False
                self._mouse_button = None
                if not self._placement_dragged:
                    self.placement_confirmed.emit()
                return
            self._mouse_down = False
            self._mouse_button = None
            if self._action_active:
                self._action_active = False
                self.action_finished.emit()
            self._shift_source = None
            self._shift_start_cell = None
            self._shift_last_delta = (0, 0)

    def _apply_shift(self, dx, dy):
        if self._placement_active:
            return
        if self._shift_source is None:
            return
        if (dx, dy) == self._shift_last_delta:
            return
        self._shift_last_delta = (dx, dy)
        shifted = [[False for _ in range(GRID_W)] for _ in range(GRID_H)]
        for y in range(GRID_H):
            src_y = y - dy
            if 0 <= src_y < GRID_H:
                for x in range(GRID_W):
                    src_x = x - dx
                    if 0 <= src_x < GRID_W and self._shift_source[src_y][src_x]:
                        shifted[y][x] = True
        self.px = shifted
        self.update()
        self.changed.emit()

    def startPlacement(self, pixels: List[List[bool]]):
        if not pixels or not pixels[0]:
            return
        self._placement_pixels = copy.deepcopy(pixels)
        self._placement_height = len(pixels)
        self._placement_width = len(pixels[0])
        self._placement_active = True
        self._mouse_down = False
        self._placement_base = self.getPixelsCopy()
        ox = (GRID_W - self._placement_width) // 2
        oy = (GRID_H - self._placement_height) // 2
        self._set_placement_offset(ox, oy)
        self._placement_drag_start = None
        self._placement_offset_start = self._placement_offset
        self._placement_dragged = False
        self._update_cursor()
        self.update()

    def finalizePlacement(self) -> Optional[List[List[bool]]]:
        if not self._placement_active:
            return None
        frame = self._compose_placement_frame()
        self._placement_active = False
        self._placement_pixels = None
        self._placement_drag_start = None
        self._placement_dragged = False
        self._placement_base = None
        self.px = copy.deepcopy(frame)
        self.update()
        self.changed.emit()
        self._update_cursor()
        return frame

    def isPlacementActive(self) -> bool:
        return self._placement_active

    def _compose_placement_frame(self) -> List[List[bool]]:
        base = self._placement_base if self._placement_base is not None else self.getPixelsCopy()
        frame = copy.deepcopy(base)
        if self._placement_pixels is None:
            return frame
        offset_x, offset_y = self._placement_offset
        for y in range(self._placement_height):
            for x in range(self._placement_width):
                gx = x + offset_x
                gy = y + offset_y
                if 0 <= gx < GRID_W and 0 <= gy < GRID_H:
                    frame[gy][gx] = self._placement_pixels[y][x]
        return frame

    def _placement_pixel_at(self, x: int, y: int) -> bool:
        if self._placement_base is not None and 0 <= y < len(self._placement_base) and 0 <= x < len(self._placement_base[0]):
            base_val = self._placement_base[y][x]
        else:
            base_val = self.px[y][x]
        if not self._placement_active or self._placement_pixels is None:
            return base_val
        offset_x, offset_y = self._placement_offset
        ix = x - offset_x
        iy = y - offset_y
        if 0 <= ix < self._placement_width and 0 <= iy < self._placement_height:
            return self._placement_pixels[iy][ix]
        return base_val

    def _set_placement_offset(self, offset_x: int, offset_y: int):
        if self._placement_pixels is None:
            return
        min_x, max_x = self._placement_offset_limits(self._placement_width, GRID_W)
        min_y, max_y = self._placement_offset_limits(self._placement_height, GRID_H)
        clamped_x = max(min(offset_x, max_x), min_x)
        clamped_y = max(min(offset_y, max_y), min_y)
        self._placement_offset = (clamped_x, clamped_y)

    @staticmethod
    def _placement_offset_limits(image_size: int, grid_size: int) -> Tuple[int, int]:
        return -image_size + 1, grid_size - 1

    def _update_cursor(self):
        if self._placement_active or self.tool == Tool.SHIFT:
            self.setCursor(Qt.SizeAllCursor)
        else:
            self.unsetCursor()

    def paintEvent(self, _):
        p = QPainter(self)
        p.fillRect(self.rect(), QBrush(Qt.black))
        pixel_brush = QBrush(QColor("#4eff00"))
        overlay_brush = QBrush(QColor("#011601"))
        # previous frame overlay
        if self.prev_px:
            for y in range(GRID_H):
                for x in range(GRID_W):
                    cur_val = self._placement_pixel_at(x, y)
                    if self.prev_px[y][x] and not cur_val:
                        p.fillRect(x*CELL+1, y*CELL+1, CELL-1, CELL-1, overlay_brush)
        # pixels
        for y in range(GRID_H):
            for x in range(GRID_W):
                if self._placement_pixel_at(x, y):
                    p.fillRect(x*CELL+1, y*CELL+1, CELL-1, CELL-1, pixel_brush)
        # grid lines
        pen = QPen()
        pen.setColor(QColor("#001800"))
        pen.setWidth(1)
        p.setPen(pen)
        for x in range(GRID_W+1):
            p.drawLine(x*CELL, 0, x*CELL, GRID_H*CELL)
        for y in range(GRID_H+1):
            p.drawLine(0, y*CELL, GRID_W*CELL, y*CELL)


class MacListDelegate(QStyledItemDelegate):
    """Custom delegate that appends italicized device names next to MAC addresses."""

    def paint(self, painter, option, index):
        self.initStyleOption(option, index)
        name = index.data(Qt.UserRole) or ""
        if not name:
            super().paint(painter, option, index)
            return

        mac = index.data(Qt.DisplayRole) or ""
        opt = QStyleOptionViewItem(option)
        opt.text = ""
        style = opt.widget.style() if opt.widget else QApplication.style()

        painter.save()
        style.drawControl(QStyle.CE_ItemViewItem, opt, painter)
        text_rect = style.subElementRect(QStyle.SE_ItemViewItemText, opt, opt.widget)
        painter.translate(text_rect.topLeft())

        doc = QTextDocument()
        doc.setDocumentMargin(0)
        doc.setDefaultFont(opt.font)
        color_role = QPalette.HighlightedText if (opt.state & QStyle.State_Selected) else QPalette.Text
        color = opt.palette.color(color_role)
        safe_mac = html.escape(mac)
        safe_name = html.escape(name)
        doc.setHtml(
            f"<span style='color:{color.name()};'>{safe_mac} "
            f"<span style='font-style:italic;'>({safe_name})</span></span>"
        )
        doc.setTextWidth(text_rect.width())
        doc.drawContents(painter, QRectF(0, 0, text_rect.width(), text_rect.height()))
        painter.restore()

    def sizeHint(self, option, index):
        self.initStyleOption(option, index)
        name = index.data(Qt.UserRole) or ""
        if not name:
            return super().sizeHint(option, index)
        mac = index.data(Qt.DisplayRole) or ""
        doc = QTextDocument()
        doc.setDocumentMargin(0)
        doc.setDefaultFont(option.font)
        safe_mac = html.escape(mac)
        safe_name = html.escape(name)
        doc.setHtml(f"{safe_mac} <span style='font-style:italic;'>({safe_name})</span>")
        doc_size = doc.size().toSize()
        doc_size.setWidth(doc_size.width() + 8)
        doc_size.setHeight(max(doc_size.height(), option.fontMetrics.height()) + 4)
        return doc_size


class Main(QMainWindow):
    scanResultsReady = Signal(object, object, bool)
    def __init__(self):
        super().__init__()
        self.setWindowTitle("SpotLED GUI")
        self.cfg = load_cfg()
        self._font_specs = {}
        self._load_custom_fonts()
        self.setWindowIcon(self._build_app_icon())
        self.setFixedSize(self.sizeHint())
        self.setWindowFlag(Qt.MSWindowsFixedSizeDialogHint, True)
        self.setWindowFlag(Qt.WindowMaximizeButtonHint, False)
        self._center_pending = True
        self._is_playing = False
        self.play_timer = QTimer(self)
        self.play_timer.timeout.connect(self._advance_playback)
        self._base_button_size = 32
        self.ui_scale = 1.0
        self._scalable_buttons: List[QToolButton] = []
        self._button_font_sizes = {}
        self._button_icon_sizes = {}
        self._button_base_sizes = {}
        self._font_widgets = {}
        self._font_extra_styles = {}
        self._font_base_heights = {}
        self._slider_base_heights = {}
        self._font_scale_factor = 1.0
        self._slider_scale_factor = 1.0
        self._current_scale_label = ""
        self._current_project_path: Optional[str] = None
        self._discovered_devices: OrderedDict[str, str] = OrderedDict()
        self._scan_in_progress = False
        self._bluetoothctl_path: Optional[str] = None
        self._ble_scan_timeout = BLE_SCAN_TIMEOUT
        self.scanResultsReady.connect(self._handle_scan_results)
        self._update_window_title()

        # --- frame model ---
        self.frames: List[List[List[bool]]] = [ [[False]*GRID_W for _ in range(GRID_H)] ]
        self.cur = 0

        # central widget
        cw = QWidget()
        root = QVBoxLayout(cw)
        self.setCentralWidget(cw)

        scale_row = QHBoxLayout()
        root.addLayout(scale_row)
        scale_row.addStretch(1)
        lbl_scale = QLabel("UI scale")
        self._register_font_scaled(lbl_scale, 16)
        scale_row.addWidget(lbl_scale)
        self.cb_ui_scale = QComboBox()
        self._scale_presets = [
            ("smallest", 0.82, 0.72, 0.84),
            ("smaller", 0.88, 0.78, 0.8),
            ("normal", 0.93, 0.82, 0.4),
            ("bigger", 1.05, 0.92, 0.85),
            ("max", 1.18, 1.02, 0.95),
        ]
        for label, scale, font_factor, slider_factor in self._scale_presets:
            self.cb_ui_scale.addItem(label)
            idx = self.cb_ui_scale.count() - 1
            self.cb_ui_scale.setItemData(idx, scale, Qt.UserRole)
            self.cb_ui_scale.setItemData(idx, font_factor, Qt.UserRole + 1)
            self.cb_ui_scale.setItemData(idx, slider_factor, Qt.UserRole + 2)
        self.cb_ui_scale.setCurrentIndex(2)
        self.cb_ui_scale.currentIndexChanged.connect(self._change_ui_scale_preset)
        self._register_font_scaled(self.cb_ui_scale, 16, base_height=30)
        scale_row.addWidget(self.cb_ui_scale)

        # Tabs
        self.tabs = QTabWidget()
        root.addWidget(self.tabs)

        # --- Tab: Image ---
        t_img = QWidget()
        self.tabs.addTab(t_img, "🖼️")
        img_v = QVBoxLayout(t_img)

        img_h = QHBoxLayout()
        img_v.addLayout(img_h)

        # Grid
        self.grid = PixelGrid()
        self.grid.setPixels(self.frames[self.cur])
        self.grid.setReferencePixels(None)
        self.grid.changed.connect(self._grid_changed)
        self.grid.action_started.connect(self._grid_action_started)
        self.grid.action_finished.connect(self._grid_action_finished)
        self.grid.placement_confirmed.connect(self._placement_confirmed)
        self.grid.placement_canceled.connect(self._placement_canceled)
        img_h.addWidget(self.grid)

        self._history: List[dict] = []
        self._history_pos = 0
        self._pending_action_before: Optional[List[List[bool]]] = None
        self._pending_action_frame: Optional[int] = None

        # Tools
        tools_col = QVBoxLayout()
        img_h.addLayout(tools_col)

        self.btn_draw = QToolButton(); self.btn_draw.setText("✏️")
        self._register_scalable_button(self.btn_draw, font_size=24)
        self.btn_shift = QToolButton(); self.btn_shift.setIcon(self._build_shift_icon())
        self.btn_shift.setToolTip("Shift all pixels")
        self._register_scalable_button(self.btn_shift, font_size=24, icon_size=40)
        self.btn_clear = QToolButton(); self.btn_clear.setText("🧹")
        self._register_scalable_button(self.btn_clear, font_size=24)
        self.btn_draw.clicked.connect(lambda: self._set_tool(Tool.DRAW))
        self.btn_shift.clicked.connect(lambda: self._set_tool(Tool.SHIFT))
        self.btn_clear.clicked.connect(self._clear_current_grid)
        tools_col.addWidget(self.btn_draw)
        tools_col.addWidget(self.btn_shift)
        tools_col.addWidget(self.btn_clear)
        tools_col.addStretch(1)
        self._update_tool_buttons()

        # Frame controls
        frames_row = QHBoxLayout()
        img_v.addLayout(frames_row)

        self.btn_play = QToolButton(); self.btn_play.setIcon(self._build_play_icon())
        self.btn_play.setToolTip("Odtwarzaj klatki")
        self._register_scalable_button(self.btn_play, font_size=24, icon_size=40)
        self.btn_prev = QToolButton(); self.btn_prev.setText("⬅️")
        self._register_scalable_button(self.btn_prev, font_size=24)
        self.btn_next = QToolButton(); self.btn_next.setText("➡️")
        self._register_scalable_button(self.btn_next, font_size=24)
        self.btn_add  = QToolButton(); self.btn_add.setText("➕")
        self._register_scalable_button(self.btn_add, font_size=24)
        self.btn_remove = QToolButton(); self.btn_remove.setText("🗑️")
        self._register_scalable_button(self.btn_remove, font_size=24)
        self.btn_copy_prev = QToolButton(); self.btn_copy_prev.setText("📄")
        self.btn_copy_prev.setToolTip("Copy previous frame")
        self._register_scalable_button(self.btn_copy_prev, font_size=24)
        self.btn_invert = QToolButton(); self.btn_invert.setIcon(self._build_invert_icon())
        self.btn_invert.setToolTip("Invert pixels")
        self._register_scalable_button(self.btn_invert, font_size=24, icon_size=40)
        self.btn_mirror_h = QToolButton(); self.btn_mirror_h.setIcon(self._build_mirror_horizontal_icon())
        self.btn_mirror_h.setToolTip("Mirror horizontally")
        self._register_scalable_button(self.btn_mirror_h, font_size=24, icon_size=40)
        self.btn_mirror_v = QToolButton(); self.btn_mirror_v.setIcon(self._build_mirror_vertical_icon())
        self.btn_mirror_v.setToolTip("Mirror vertically")
        self._register_scalable_button(self.btn_mirror_v, font_size=24, icon_size=40)
        self.btn_import_png = QToolButton(); self.btn_import_png.setText("🖼️")
        self.btn_import_png.setToolTip("Import frame from PNG")
        self._register_scalable_button(self.btn_import_png, font_size=24)
        self.btn_undo = QToolButton(); self.btn_undo.setText("↩️")
        self.btn_undo.setToolTip("Undo")
        self._register_scalable_button(self.btn_undo, font_size=24)
        self.btn_redo = QToolButton(); self.btn_redo.setText("↪️")
        self.btn_redo.setToolTip("Redo")
        self._register_scalable_button(self.btn_redo, font_size=24)
        self.sl_frame_nav = QSlider(Qt.Horizontal)
        self.sl_frame_nav.setRange(1, len(self.frames))
        self.sl_frame_nav.setSingleStep(1)
        self.sl_frame_nav.setPageStep(1)
        self.sl_frame_nav.valueChanged.connect(self._frame_slider_changed)
        self._register_slider(self.sl_frame_nav, base_height=22)
        self._frame_slider_sync = False
        self.sl_frame_nav.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        self.lbl_counter = QLabel("1/1")
        self._register_font_scaled(self.lbl_counter, 18)

        self.btn_prev.clicked.connect(self._prev_frame)
        self.btn_next.clicked.connect(self._next_frame)
        self.btn_add.clicked.connect(self._add_frame)
        self.btn_remove.clicked.connect(self._remove_current_frame)
        self.btn_copy_prev.clicked.connect(self._copy_from_previous_frame)
        self.btn_invert.clicked.connect(self._invert_current_grid)
        self.btn_mirror_h.clicked.connect(self._mirror_current_grid_horizontal)
        self.btn_mirror_v.clicked.connect(self._mirror_current_grid_vertical)
        self.btn_import_png.clicked.connect(self._import_image_frame)
        self.btn_undo.clicked.connect(self._undo)
        self.btn_redo.clicked.connect(self._redo)
        self.btn_play.clicked.connect(self._toggle_playback)

        frames_row.addWidget(self.btn_play)
        frames_row.addWidget(self.btn_prev)
        frames_row.addWidget(self.btn_next)
        frames_row.addWidget(self.btn_add)
        frames_row.addWidget(self.btn_remove)
        frames_row.addWidget(self.btn_copy_prev)
        frames_row.addWidget(self.btn_invert)
        frames_row.addWidget(self.btn_mirror_h)
        frames_row.addWidget(self.btn_mirror_v)
        frames_row.addWidget(self.btn_import_png)
        frames_row.addWidget(self.btn_undo)
        frames_row.addWidget(self.btn_redo)
        frames_row.addWidget(self.sl_frame_nav, 1)
        frames_row.addWidget(self.lbl_counter)

        # Effect + speed for image animations
        img_opts = QHBoxLayout()
        img_v.addLayout(img_opts)
        lbl_effect_img = QLabel("✨")
        self._register_font_scaled(lbl_effect_img, 26)
        img_opts.addWidget(lbl_effect_img)
        self.cb_effect_img = QComboBox()
        self.cb_effect_img.addItems(["NONE","SCROLL_UP","SCROLL_DOWN","SCROLL_LEFT","SCROLL_RIGHT","STACK","EXPAND","LASER"])
        self._register_font_scaled(self.cb_effect_img, 18, base_height=34)
        img_opts.addWidget(self.cb_effect_img)
        img_opts.addSpacing(12)
        lbl_speed_icon_img = QLabel("🏃")
        self._register_font_scaled(lbl_speed_icon_img, 26)
        img_opts.addWidget(lbl_speed_icon_img)
        self.sl_speed_img = QSlider(Qt.Horizontal)
        self.sl_speed_img.setRange(1, 3500)
        self.sl_speed_img.setSingleStep(10)
        self.sl_speed_img.setPageStep(100)
        self.sl_speed_img.setValue(100)
        self.lbl_speed_img = QLabel("100")
        self.sl_speed_img.valueChanged.connect(lambda v: self._update_slider_display(self.sl_speed_img, self.lbl_speed_img, v))
        img_opts.addWidget(self.sl_speed_img, 1)
        img_opts.addWidget(self.lbl_speed_img)
        self._register_font_scaled(self.lbl_speed_img, 18)
        self._register_slider(self.sl_speed_img, base_height=28)
        self._update_slider_display(self.sl_speed_img, self.lbl_speed_img, self.sl_speed_img.value())

        # --- Tab: Text ---
        t_txt = QWidget()
        self.tabs.addTab(t_txt, "🅣")
        txt_v = QVBoxLayout(t_txt)

        row1 = QHBoxLayout()
        lbl_text_icon = QLabel("💬")
        self._register_font_scaled(lbl_text_icon, 26)
        row1.addWidget(lbl_text_icon)
        self.le_text = QLineEdit()
        self._register_font_scaled(self.le_text, 26, base_height=44, extra_style=" color:#4eff00; background-color:#020302;")
        row1.addWidget(self.le_text, 1)
        txt_v.addLayout(row1)

        row1b = QHBoxLayout()
        self.chk_two_lines = QCheckBox("〰️〰️")
        self._register_font_scaled(self.chk_two_lines, 24, base_height=32)
        row1b.addWidget(self.chk_two_lines)
        row1b.addStretch(1)
        txt_v.addLayout(row1b)

        row_font = QHBoxLayout()
        lbl_font = QLabel("Font")
        self._register_font_scaled(lbl_font, 18)
        row_font.addWidget(lbl_font)
        self.cb_font = QComboBox()
        self._register_font_scaled(self.cb_font, 18, base_height=34)
        self.cb_font.currentIndexChanged.connect(self._font_choice_changed)
        row_font.addWidget(self.cb_font, 1)
        txt_v.addLayout(row_font)
        self._populate_font_combo()

        row2 = QHBoxLayout()
        lbl_effect_txt = QLabel("✨")
        self._register_font_scaled(lbl_effect_txt, 26)
        row2.addWidget(lbl_effect_txt)
        self.cb_effect_txt = QComboBox()
        self.cb_effect_txt.addItems(["NONE","SCROLL_UP","SCROLL_DOWN","SCROLL_LEFT","SCROLL_RIGHT","STACK","EXPAND","LASER"])
        self._register_font_scaled(self.cb_effect_txt, 18, base_height=34)
        row2.addWidget(self.cb_effect_txt)
        row2.addStretch(1)
        lbl_speed_icon_txt = QLabel("🏃")
        self._register_font_scaled(lbl_speed_icon_txt, 26)
        row2.addWidget(lbl_speed_icon_txt)
        self.sl_speed_txt = QSlider(Qt.Horizontal)
        self.sl_speed_txt.setRange(0, 3500)
        self.sl_speed_txt.setSingleStep(10)
        self.sl_speed_txt.setPageStep(100)
        self.sl_speed_txt.setValue(0)
        self.lbl_speed_txt = QLabel("0")
        self.sl_speed_txt.valueChanged.connect(lambda v: self._update_slider_display(self.sl_speed_txt, self.lbl_speed_txt, v))
        row2.addWidget(self.sl_speed_txt, 2)
        row2.addWidget(self.lbl_speed_txt)
        self._register_font_scaled(self.lbl_speed_txt, 18)
        self._register_slider(self.sl_speed_txt, base_height=28)
        txt_v.addLayout(row2)
        self._update_slider_display(self.sl_speed_txt, self.lbl_speed_txt, self.sl_speed_txt.value())

        # --- Tab: Info ---
        t_info = QWidget()
        self.tabs.addTab(t_info, "ℹ️")
        info_layout = QVBoxLayout(t_info)
        info_layout.addStretch(1)
        lbl_info = QLabel("SpotLED GUI (2025)\nby Adam Mateusz Brozynski\nbased on python-spotled \nby Izzie Walton")
        lbl_info.setAlignment(Qt.AlignCenter)
        self._register_font_scaled(lbl_info, 18, extra_style=" color:#4eff00;")
        info_layout.addWidget(lbl_info)
        info_layout.addStretch(1)

        # --- Shared: MAC + Send ---
        row_mac = QHBoxLayout()
        mac_icon = QLabel("🖧")
        self._register_font_scaled(mac_icon, 26)
        row_mac.addWidget(mac_icon)
        self.cb_mac = QComboBox(); self.cb_mac.setEditable(True)
        self._mac_delegate = MacListDelegate(self.cb_mac)
        self.cb_mac.setItemDelegate(self._mac_delegate)
        self._register_font_scaled(self.cb_mac, 18, base_height=34)
        row_mac.addWidget(self.cb_mac, 1)
        self.btn_scan = QToolButton(); self.btn_scan.setText("scan")
        self._register_scalable_button(self.btn_scan, font_size=20, base_size=(74, 40))
        self.btn_scan.clicked.connect(lambda: self._start_ble_scan(auto=False))
        row_mac.addWidget(self.btn_scan)
        self.btn_load = QToolButton(); self.btn_load.setText("📂")
        self._register_scalable_button(self.btn_load, font_size=32, base_size=(48, 40))
        self.btn_save = QToolButton(); self.btn_save.setText("💾")
        self._register_scalable_button(self.btn_save, font_size=32, base_size=(48, 40))
        self.btn_load.clicked.connect(self._load_project)
        self.btn_save.clicked.connect(self._save_project)
        row_mac.addWidget(self.btn_load)
        row_mac.addWidget(self.btn_save)
        self.btn_send = QToolButton(); self.btn_send.setText("📤")
        self._register_scalable_button(self.btn_send, font_size=32, base_size=(48, 40))
        self.btn_send.clicked.connect(self.send_current)
        row_mac.addWidget(self.btn_send)
        root.addLayout(row_mac)
        self._rebuild_mac_combobox(self.cfg["mac_history"][0] if self.cfg.get("mac_history") else None)

        self._tab_bar = self.tabs.tabBar()
        self._playback_locked_widgets = [
            self.grid,
            self.btn_draw, self.btn_shift, self.btn_clear,
            self.btn_prev, self.btn_next, self.btn_add, self.btn_remove, self.btn_copy_prev,
            self.btn_invert, self.btn_mirror_h, self.btn_mirror_v,
            self.btn_import_png, self.btn_undo, self.btn_redo,
            self.cb_effect_img, self.sl_speed_img,
            self.le_text, self.chk_two_lines, self.cb_font, self.cb_effect_txt, self.sl_speed_txt,
            self.cb_mac, self.btn_scan, self.btn_load, self.btn_save, self.btn_send,
            self.cb_ui_scale
        ]
        if hasattr(self, "sl_frame_nav"):
            self._playback_locked_widgets.append(self.sl_frame_nav)

        self._change_ui_scale_preset(self.cb_ui_scale.currentIndex())
        self._reset_history()
        self._refresh_counter()
        QTimer.singleShot(800, lambda: self._start_ble_scan(auto=True))

    # --- model / UI sync ---
    def _build_app_icon(self) -> QIcon:
        pix = QPixmap(128, 128)
        pix.fill(Qt.transparent)
        painter = QPainter(pix)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(pix.rect(), QColor("#050505"))
        glow = QBrush(QColor(78, 255, 0, 120))
        painter.setBrush(glow)
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(16, 16, 96, 96)
        pen = QPen(QColor("#4eff00"))
        pen.setWidth(8)
        painter.setPen(pen)
        painter.setBrush(QBrush(QColor("#4eff00")))
        painter.drawEllipse(36, 36, 56, 56)
        painter.setBrush(QBrush(QColor("#0a0a0a")))
        painter.drawEllipse(52, 52, 24, 24)
        painter.end()
        return QIcon(pix)

    def _build_shift_icon(self) -> QIcon:
        size = 64
        pix = QPixmap(size, size)
        pix.fill(Qt.transparent)
        painter = QPainter(pix)
        painter.setRenderHint(QPainter.Antialiasing)
        pen = QPen(QColor("#4eff00"))
        pen.setWidth(6)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(pen)
        center = size // 2
        margin = 12
        painter.drawLine(center, margin, center, size - margin)
        painter.drawLine(margin, center, size - margin, center)
        arrow = 12
        painter.drawLine(center, margin, center - arrow // 2, margin + arrow)
        painter.drawLine(center, margin, center + arrow // 2, margin + arrow)
        painter.drawLine(center, size - margin, center - arrow // 2, size - margin - arrow)
        painter.drawLine(center, size - margin, center + arrow // 2, size - margin - arrow)
        painter.drawLine(margin, center, margin + arrow, center - arrow // 2)
        painter.drawLine(margin, center, margin + arrow, center + arrow // 2)
        painter.drawLine(size - margin, center, size - margin - arrow, center - arrow // 2)
        painter.drawLine(size - margin, center, size - margin - arrow, center + arrow // 2)
        painter.end()
        return QIcon(pix)

    def _build_invert_icon(self) -> QIcon:
        size = 64
        pix = QPixmap(size, size)
        pix.fill(Qt.transparent)
        painter = QPainter(pix)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = pix.rect().adjusted(6, 6, -6, -6)
        painter.fillRect(rect.x(), rect.y(), rect.width() // 2, rect.height(), QColor("#0a0a0a"))
        painter.fillRect(rect.x() + rect.width() // 2, rect.y(), rect.width() // 2, rect.height(), QColor("#4eff00"))
        pen = QPen(QColor("#4eff00"))
        pen.setWidth(4)
        painter.setPen(pen)
        painter.drawRect(rect)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(QColor("#4eff00")))
        painter.drawEllipse(rect.center(), 10, 10)
        painter.setBrush(QBrush(QColor("#0a0a0a")))
        painter.drawEllipse(rect.center().x() - 4, rect.center().y() - 4, 8, 8)
        painter.end()
        return QIcon(pix)

    def _build_mirror_horizontal_icon(self) -> QIcon:
        size = 64
        pix = QPixmap(size, size)
        pix.fill(Qt.transparent)
        painter = QPainter(pix)
        painter.setRenderHint(QPainter.Antialiasing)
        pen = QPen(QColor("#4eff00"))
        pen.setWidth(6)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(pen)
        center = size // 2
        margin = 10
        painter.drawLine(center, margin, center, size - margin)
        painter.drawLine(margin, center, size - margin, center)
        arrow = 12
        painter.drawLine(margin, center, margin + arrow, center - arrow // 2)
        painter.drawLine(margin, center, margin + arrow, center + arrow // 2)
        painter.drawLine(size - margin, center, size - margin - arrow, center - arrow // 2)
        painter.drawLine(size - margin, center, size - margin - arrow, center + arrow // 2)
        painter.end()
        return QIcon(pix)

    def _build_mirror_vertical_icon(self) -> QIcon:
        size = 64
        pix = QPixmap(size, size)
        pix.fill(Qt.transparent)
        painter = QPainter(pix)
        painter.setRenderHint(QPainter.Antialiasing)
        pen = QPen(QColor("#4eff00"))
        pen.setWidth(6)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(pen)
        center = size // 2
        margin = 10
        painter.drawLine(margin, center, size - margin, center)
        painter.drawLine(center, margin, center, size - margin)
        arrow = 12
        painter.drawLine(center, margin, center - arrow // 2, margin + arrow)
        painter.drawLine(center, margin, center + arrow // 2, margin + arrow)
        painter.drawLine(center, size - margin, center - arrow // 2, size - margin - arrow)
        painter.drawLine(center, size - margin, center + arrow // 2, size - margin - arrow)
        painter.end()
        return QIcon(pix)

    def _build_play_icon(self) -> QIcon:
        size = 64
        pix = QPixmap(size, size)
        pix.fill(Qt.transparent)
        painter = QPainter(pix)
        painter.setRenderHint(QPainter.Antialiasing)
        brush = QBrush(QColor("#4eff00"))
        painter.setBrush(brush)
        painter.setPen(Qt.NoPen)
        margin = 16
        points = [
            QPointF(margin, margin),
            QPointF(size - margin, size // 2),
            QPointF(margin, size - margin)
        ]
        painter.drawPolygon(points)
        painter.end()
        return QIcon(pix)

    def _build_stop_icon(self) -> QIcon:
        size = 64
        pix = QPixmap(size, size)
        pix.fill(Qt.transparent)
        painter = QPainter(pix)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setBrush(QBrush(QColor("#4eff00")))
        painter.setPen(Qt.NoPen)
        margin = 16
        painter.drawRoundedRect(margin, margin, size - 2*margin, size - 2*margin, 8, 8)
        painter.end()
        return QIcon(pix)

    def _register_scalable_button(self, button: QToolButton, font_size: int = 24, icon_size: Optional[int] = None, base_size: Optional[Tuple[int, int]] = None):
        self._scalable_buttons.append(button)
        self._button_font_sizes[button] = font_size
        if icon_size is not None:
            self._button_icon_sizes[button] = icon_size
        if base_size is None:
            base_size = (self._base_button_size, self._base_button_size)
        elif isinstance(base_size, int):
            base_size = (base_size, base_size)
        self._button_base_sizes[button] = base_size

    def _register_font_scaled(self, widget, font_size: int, base_height: Optional[int] = None, extra_style: str = ""):
        self._font_widgets[widget] = font_size
        self._font_extra_styles[widget] = extra_style
        if base_height is not None:
            self._font_base_heights[widget] = base_height

    def _register_slider(self, slider: QSlider, base_height: int = 28):
        self._slider_base_heights[slider] = base_height

    def _change_ui_scale_preset(self, index: int):
        scale = self.cb_ui_scale.itemData(index, Qt.UserRole)
        font_factor = self.cb_ui_scale.itemData(index, Qt.UserRole + 1)
        slider_factor = self.cb_ui_scale.itemData(index, Qt.UserRole + 2)
        if scale is None or font_factor is None or slider_factor is None:
            return
        self._current_scale_label = self.cb_ui_scale.itemText(index)
        self._change_ui_scale(float(scale), float(font_factor), float(slider_factor))

    def _change_ui_scale(self, scale: float, font_factor: Optional[float] = None, slider_factor: Optional[float] = None):
        self.ui_scale = max(0.5, float(scale))
        if font_factor is not None:
            self._font_scale_factor = max(0.4, float(font_factor))
        if slider_factor is not None:
            self._slider_scale_factor = max(0.3, float(slider_factor))
        self._apply_ui_scale()

    def _apply_ui_scale(self):
        font_scale = getattr(self, "_font_scale_factor", self.ui_scale)
        slider_scale = getattr(self, "_slider_scale_factor", self.ui_scale)
        for button in self._scalable_buttons:
            base_w, base_h = self._button_base_sizes.get(button, (self._base_button_size, self._base_button_size))
            width = max(20, int(base_w * self.ui_scale))
            height = max(20, int(base_h * self.ui_scale))
            button.setFixedSize(width, height)
            base_font = self._button_font_sizes.get(button, 24)
            button.setStyleSheet(f"font-size:{max(10, int(base_font * font_scale))}px;")
            if button in self._button_icon_sizes:
                icon_base = self._button_icon_sizes[button]
                icon_val = max(12, int(icon_base * self.ui_scale))
                button.setIconSize(QSize(icon_val, icon_val))
        for widget, base_font in self._font_widgets.items():
            font_val = max(8, int(base_font * font_scale))
            extra = self._font_extra_styles.get(widget, "")
            widget.setStyleSheet(f"font-size:{font_val}px;{extra}")
            if widget in self._font_base_heights:
                height = max(16, int(self._font_base_heights[widget] * font_scale))
                widget.setFixedHeight(height)
        handle_size = 10
        for slider, base_height in self._slider_base_heights.items():
            height = max(10, int(base_height * slider_scale))
            groove = max(3, int(height * 0.25))
            handle = handle_size
            slider_height = max(height, handle)
            slider.setFixedHeight(slider_height)
            slider.setStyleSheet(
                "QSlider::groove:horizontal {{ background:#033003; height:{}px; border-radius:{}px; }} "
                "QSlider::handle:horizontal {{ background:#4eff00; width:{}px; height:{}px; margin:-{}px 0; border-radius:{}px; }}".format(
                    groove, groove // 2, handle, handle, handle // 2, handle // 2
                )
            )
        tab_font = max(10, int(24 * self.ui_scale))
        tab_pad_v = max(4, int(10 * self.ui_scale))
        tab_pad_h = max(6, int(14 * self.ui_scale))
        self.tabs.setStyleSheet(f"QTabBar::tab {{ font-size:{tab_font}px; padding:{tab_pad_v}px {tab_pad_h}px; }}")

    def _update_window_title(self):
        title = "SpotLED GUI"
        path = getattr(self, "_current_project_path", None)
        if path:
            name = os.path.basename(path)
            if name:
                title = f"{title} – {name}"
        self.setWindowTitle(title)

    def showEvent(self, event):
        super().showEvent(event)
        if getattr(self, "_center_pending", False):
            self._center_on_screen()
            self._center_pending = False

    def _center_on_screen(self):
        screen = QApplication.primaryScreen()
        if not screen:
            return
        geo = self.frameGeometry()
        geo.moveCenter(screen.availableGeometry().center())
        self.move(geo.topLeft())

    def _grid_changed(self):
        # update the current frame from the grid buffer
        self.frames[self.cur] = self.grid.getPixelsCopy()

    def _set_tool(self, tool):
        self.grid.setTool(tool)
        self._update_tool_buttons()

    def _update_tool_buttons(self):
        self.btn_draw.setDown(self.grid.tool == Tool.DRAW)
        self.btn_shift.setDown(self.grid.tool == Tool.SHIFT)

    def _refresh_counter(self):
        self.lbl_counter.setText(f"{self.cur+1}/{len(self.frames)}")
        self.btn_copy_prev.setEnabled(self.cur > 0)
        if hasattr(self, "sl_frame_nav"):
            self._frame_slider_sync = True
            self.sl_frame_nav.setRange(1, max(1, len(self.frames)))
            self.sl_frame_nav.setEnabled(len(self.frames) > 1)
            self.sl_frame_nav.blockSignals(True)
            self.sl_frame_nav.setValue(self.cur + 1)
            self.sl_frame_nav.blockSignals(False)
            self._frame_slider_sync = False
        self._update_history_buttons()

    def _grid_action_started(self):
        self._begin_action(self.cur)

    def _grid_action_finished(self):
        self._finish_action()

    def _placement_confirmed(self):
        self._commit_imported_image()

    def _frame_slider_changed(self, value: int):
        if getattr(self, "_frame_slider_sync", False):
            return
        if not self._require_placement_confirmation():
            self._frame_slider_sync = True
            self.sl_frame_nav.blockSignals(True)
            self.sl_frame_nav.setValue(self.cur + 1)
            self.sl_frame_nav.blockSignals(False)
            self._frame_slider_sync = False
            return
        value = max(1, min(len(self.frames), int(value)))
        target = value - 1
        if target == self.cur:
            return
        self.cur = target
        self._load_current_into_grid()
        self._refresh_counter()

    def _commit_imported_image(self) -> bool:
        result = self.grid.finalizePlacement()
        if result is None:
            return False
        prev_ref = self.frames[self.cur-1] if self.cur > 0 else None
        self.grid.setReferencePixels(prev_ref)
        self._finish_action()
        return True

    def _begin_action(self, frame_idx: int):
        if self._pending_action_before is None:
            self._pending_action_before = copy.deepcopy(self.frames[frame_idx])
            self._pending_action_frame = frame_idx

    def _finish_action(self):
        if self._pending_action_before is None or self._pending_action_frame is None:
            return
        frame_idx = self._pending_action_frame
        if frame_idx == self.cur:
            after_state = self.grid.getPixelsCopy()
            self.frames[frame_idx] = copy.deepcopy(after_state)
        else:
            after_state = copy.deepcopy(self.frames[frame_idx])
        if after_state != self._pending_action_before:
            self._history = self._history[:self._history_pos]
            self._history.append({
                "frame": frame_idx,
                "before": copy.deepcopy(self._pending_action_before),
                "after": copy.deepcopy(after_state)
            })
            self._history_pos += 1
        self._pending_action_before = None
        self._pending_action_frame = None
        self._update_history_buttons()

    def _update_history_buttons(self):
        if not hasattr(self, "btn_undo") or not hasattr(self, "btn_redo"):
            return
        can_undo = self._history_pos > 0
        can_redo = self._history_pos < len(self._history)
        self.btn_undo.setEnabled(can_undo)
        self.btn_redo.setEnabled(can_redo)

    def _placement_canceled(self):
        self._cancel_pending_action()

    def _reset_history(self):
        self._history = []
        self._history_pos = 0
        self._pending_action_before = None
        self._pending_action_frame = None
        self._update_history_buttons()

    def _cancel_pending_action(self):
        self._pending_action_before = None
        self._pending_action_frame = None

    def _require_placement_confirmation(self) -> bool:
        if not self.grid.isPlacementActive():
            return True
        QMessageBox.information(
            self,
            "Positioning",
            "Click on a board to set image position."
        )
        return False

    def _clear_current_grid(self):
        if not self._require_placement_confirmation():
            return
        self._begin_action(self.cur)
        self.grid.clearAll()
        self._finish_action()

    def _invert_current_grid(self):
        if not self._require_placement_confirmation():
            return
        self._begin_action(self.cur)
        self.grid.invertAll()
        self._finish_action()

    def _mirror_current_grid_horizontal(self):
        if not self._require_placement_confirmation():
            return
        self._begin_action(self.cur)
        self.grid.mirrorHorizontal()
        self._finish_action()

    def _mirror_current_grid_vertical(self):
        if not self._require_placement_confirmation():
            return
        self._begin_action(self.cur)
        self.grid.mirrorVertical()
        self._finish_action()

    def _toggle_playback(self):
        if self._is_playing:
            self._stop_playback()
        else:
            self._start_playback()

    def _start_playback(self):
        if self._is_playing:
            return
        if not self._require_placement_confirmation():
            return
        self.frames[self.cur] = self.grid.getPixelsCopy()
        self._is_playing = True
        self._set_playback_locked(True)
        self.play_timer.start(self._playback_interval_ms())

    def _stop_playback(self):
        if not self._is_playing:
            return
        self.play_timer.stop()
        self._is_playing = False
        self._set_playback_locked(False)

    def _advance_playback(self):
        if not self._is_playing:
            return
        if not self.frames:
            return
        self.cur = (self.cur + 1) % len(self.frames)
        self._load_current_into_grid()
        self._refresh_counter()

    def _apply_playback_speed(self):
        if not self._is_playing:
            return
        interval = self._playback_interval_ms()
        self.play_timer.setInterval(interval)

    def _playback_interval_ms(self) -> int:
        return max(10, int(self.sl_speed_img.value()))

    def _set_playback_locked(self, locked: bool):
        for widget in self._playback_locked_widgets:
            widget.setEnabled(not locked)
        if self._tab_bar:
            self._tab_bar.setEnabled(not locked)
        if locked:
            self.btn_play.setIcon(self._build_stop_icon())
            self.btn_play.setToolTip("Stop")
        else:
            self.btn_play.setIcon(self._build_play_icon())
            self.btn_play.setToolTip("Play")

    def _copy_from_previous_frame(self):
        if not self._require_placement_confirmation():
            return
        if self.cur == 0:
            return
        self._begin_action(self.cur)
        self.grid.setPixels(self.frames[self.cur-1])
        self.grid.setReferencePixels(self.frames[self.cur-1])
        self.grid.update()
        self._finish_action()

    def _import_image_frame(self):
        base_dir = self._project_dir()
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Import frame from PNG",
            base_dir,
            "PNG Images (*.png)"
        )
        if not path:
            return

        image = QImage(path)
        if image.isNull():
            QMessageBox.critical(self, "Import error", "Could not read the PNG file.")
            return

        self._store_project_dir(os.path.dirname(path))

        image = image.convertToFormat(QImage.Format_ARGB32)
        width = image.width()
        height = image.height()
        pixels = [[False for _ in range(width)] for _ in range(height)]

        for y in range(height):
            for x in range(width):
                color = QColor(image.pixel(x, y))
                if color.alpha() < 128:
                    continue
                if color.lightness() >= 128:
                    pixels[y][x] = True

        if not pixels:
            QMessageBox.information(self, "Import", "Image is empty.")
            return

        self._begin_action(self.cur)
        self.grid.startPlacement(pixels)

    def _apply_history_state(self, frame_idx: int, state: List[List[bool]]):
        if not 0 <= frame_idx < len(self.frames):
            return
        self.frames[frame_idx] = copy.deepcopy(state)
        self.cur = frame_idx
        self._load_current_into_grid()
        self._refresh_counter()

    def _undo(self):
        if not self._require_placement_confirmation():
            return
        if self._history_pos == 0:
            return
        self._history_pos -= 1
        action = self._history[self._history_pos]
        self._pending_action_before = None
        self._pending_action_frame = None
        self._apply_history_state(action["frame"], action["before"])
        self._update_history_buttons()

    def _redo(self):
        if not self._require_placement_confirmation():
            return
        if self._history_pos >= len(self._history):
            return
        action = self._history[self._history_pos]
        self._history_pos += 1
        self._pending_action_before = None
        self._pending_action_frame = None
        self._apply_history_state(action["frame"], action["after"])
        self._update_history_buttons()

    def _load_current_into_grid(self):
        self.grid.setPixels(self.frames[self.cur])
        prev = self.frames[self.cur-1] if self.cur > 0 else None
        self.grid.setReferencePixels(prev)

    def _prev_frame(self):
        if self.cur > 0:
            self.cur -= 1
            self._load_current_into_grid()
            self._refresh_counter()

    def _next_frame(self):
        if self.cur < len(self.frames) - 1:
            self.cur += 1
            self._load_current_into_grid()
            self._refresh_counter()

    def _add_frame(self):
        if self.grid.isPlacementActive():
            if not self._commit_imported_image():
                return
        self.frames.insert(self.cur+1, [[False]*GRID_W for _ in range(GRID_H)])
        self.cur += 1
        self._load_current_into_grid()
        self._reset_history()
        self._refresh_counter()

    def _remove_current_frame(self):
        if len(self.frames) == 1:
            # clear the only frame
            self.frames[0] = [[False]*GRID_W for _ in range(GRID_H)]
            self._load_current_into_grid()
            self._reset_history()
            return
        self.frames.pop(self.cur)
        if self.cur >= len(self.frames):
            self.cur = len(self.frames)-1
        self._load_current_into_grid()
        self._reset_history()
        self._refresh_counter()

    # --- BLE helpers ---
    def _safe_disconnect(self, sender):
        for attr in ("disconnect", "close"):
            m = getattr(sender, attr, None)
            if callable(m):
                try: m()
                except Exception: pass
        for inner in ("device","dev","_device","client","_client","requester"):
            obj = getattr(sender, inner, None)
            if obj:
                for attr in ("disconnect","close"):
                    m = getattr(obj, attr, None)
                    if callable(m):
                        try: m()
                        except Exception: pass
        try: del sender
        except Exception: pass

    def _store_mac(self, mac: str):
        mac = mac.strip().upper()
        if not mac: return
        hist = [mac] + [m.upper() for m in self.cfg.get("mac_history", []) if m]
        uniq = []
        for m in hist:
            if m not in uniq:
                uniq.append(m)
        self.cfg["mac_history"] = uniq[:20]
        save_cfg(self.cfg)
        self._rebuild_mac_combobox(mac)

    def _ordered_mac_entries(self) -> List[str]:
        seen = set()
        ordered: List[str] = []
        for mac in self._discovered_devices.keys():
            norm = mac.strip().upper()
            if norm and norm not in seen:
                ordered.append(norm)
                seen.add(norm)
        for mac in self.cfg.get("mac_history", []):
            norm = mac.strip().upper()
            if norm and norm not in seen:
                ordered.append(norm)
                seen.add(norm)
        return ordered

    def _rebuild_mac_combobox(self, preferred: Optional[str] = None):
        if not hasattr(self, "cb_mac"):
            return
        current_text = (preferred or self.cb_mac.currentText()).strip().upper()
        entries = self._ordered_mac_entries()
        self.cb_mac.blockSignals(True)
        self.cb_mac.clear()
        for mac in entries:
            self.cb_mac.addItem(mac)
            name = self._discovered_devices.get(mac)
            if name:
                idx = self.cb_mac.count() - 1
                self.cb_mac.setItemData(idx, name, Qt.UserRole)
        self.cb_mac.blockSignals(False)
        if current_text:
            self.cb_mac.setCurrentText(current_text)
        elif entries:
            self.cb_mac.setCurrentIndex(0)
        else:
            self.cb_mac.setEditText("")

    # --- Font helpers ---
    def _fonts_dir(self) -> str:
        return os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")

    def _load_custom_fonts(self):
        self._font_specs = {}
        fonts_dir = self._fonts_dir()
        if not os.path.isdir(fonts_dir):
            return
        for entry in sorted(os.listdir(fonts_dir)):
            if not entry.lower().endswith(".slf"):
                continue
            path = os.path.join(fonts_dir, entry)
            try:
                spec = self._parse_slf_font(path)
                if spec:
                    self._font_specs[spec["id"]] = spec
            except Exception as exc:
                print(f"[Font] Could not load {path}: {exc}")

    def _parse_slf_font(self, path: str):
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        name = str(raw.get("name") or os.path.splitext(os.path.basename(path))[0]).strip() or os.path.splitext(os.path.basename(path))[0]
        width = int(raw.get("width") or 0)
        height = int(raw.get("height") or 0)
        if width <= 0 or height <= 0:
            raise ValueError("Font width/height must be positive.")
        chars_raw = raw.get("chars")
        if not isinstance(chars_raw, dict) or not chars_raw:
            raise ValueError("Font file is missing glyph data.")
        normalized = {}
        blank_line = "." * width
        for key, rows in chars_raw.items():
            if not key:
                continue
            char = key[0]
            if not isinstance(rows, list):
                continue
            glyph_rows = []
            for idx in range(height):
                if idx < len(rows):
                    row = rows[idx]
                    if not isinstance(row, str):
                        row = ""
                else:
                    row = ""
                cleaned = row.replace("#", "1").replace(" ", ".")
                if len(cleaned) < width:
                    cleaned = cleaned + "." * (width - len(cleaned))
                elif len(cleaned) > width:
                    cleaned = cleaned[:width]
                glyph_rows.append(cleaned)
            if not glyph_rows:
                glyph_rows = [blank_line for _ in range(height)]
            normalized[char] = tuple(glyph_rows)
        if " " not in normalized:
            normalized[" "] = tuple(blank_line for _ in range(height))
        font_id = os.path.relpath(path, start=os.path.dirname(os.path.abspath(__file__)))
        return {
            "id": font_id,
            "name": name,
            "width": width,
            "height": height,
            "chars": normalized
        }

    def _populate_font_combo(self):
        if not hasattr(self, "cb_font"):
            return
        current_choice = self.cfg.get("selected_font", FONT_ID_BUILTIN)
        self.cb_font.blockSignals(True)
        self.cb_font.clear()
        self.cb_font.addItem("built-in", FONT_ID_BUILTIN)
        for font_id in sorted(self._font_specs.keys()):
            spec = self._font_specs[font_id]
            self.cb_font.addItem(spec["name"], font_id)
        self.cb_font.blockSignals(False)
        idx = self.cb_font.findData(current_choice)
        if idx < 0:
            idx = 0
        self.cb_font.setCurrentIndex(idx)
        self._font_choice_changed(idx)

    def _font_choice_changed(self, idx: int):
        if not hasattr(self, "cb_font"):
            return
        font_id = self.cb_font.itemData(idx, Qt.UserRole) or FONT_ID_BUILTIN
        self.cfg["selected_font"] = font_id
        save_cfg(self.cfg)
        if hasattr(self, "chk_two_lines"):
            allow_two_lines = font_id == FONT_ID_BUILTIN
            self.chk_two_lines.setEnabled(allow_two_lines)
            if not allow_two_lines and self.chk_two_lines.isChecked():
                self.chk_two_lines.setChecked(False)

    def _current_font_choice(self) -> str:
        if not hasattr(self, "cb_font"):
            return FONT_ID_BUILTIN
        data = self.cb_font.currentData(Qt.UserRole)
        return data if data else FONT_ID_BUILTIN

    def _current_font_spec(self):
        font_id = self._current_font_choice()
        return self._font_specs.get(font_id)

    def _ensure_bluetoothctl_available(self, silent: bool) -> bool:
        if self._bluetoothctl_path and os.path.exists(self._bluetoothctl_path):
            return True
        path = shutil.which("bluetoothctl")
        if path:
            self._bluetoothctl_path = path
            return True
        if not silent:
            QMessageBox.warning(self, "Scan unavailable", "Polecenie 'bluetoothctl' nie jest dostępne.")
        return False

    def _start_ble_scan(self, auto: bool):
        if self._scan_in_progress:
            if not auto:
                QMessageBox.information(self, "Scan", "Skanowanie już trwa.")
            return
        if not self._ensure_bluetoothctl_available(silent=auto):
            return
        self._scan_in_progress = True
        if hasattr(self, "btn_scan"):
            self.btn_scan.setEnabled(False)
            self.btn_scan.setText("scan…")
        worker = threading.Thread(target=self._run_ble_scan, args=(auto,), daemon=True)
        worker.start()

    def _run_ble_scan(self, auto: bool):
        cmd_path = self._bluetoothctl_path or shutil.which("bluetoothctl") or "bluetoothctl"
        cmd = [cmd_path, "--timeout", str(self._ble_scan_timeout), "scan", "on"]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        except Exception as e:
            self.scanResultsReady.emit([], str(e), auto)
            return
        chunks = []
        if proc.stdout:
            chunks.append(proc.stdout)
        if proc.stderr:
            chunks.append(proc.stderr)
        output = "\n".join(chunks)
        found = OrderedDict()
        for line in output.splitlines():
            match = BT_DEVICE_RE.search(line)
            if not match:
                continue
            mac = match.group(1).strip().upper()
            name = match.group(2).strip()
            if not name or not name.upper().startswith(SPOTLED_NAME_PREFIX):
                continue
            if mac not in found:
                found[mac] = name
        devices = list(found.items())
        error_msg = None
        if not devices and proc.returncode != 0:
            combined = (proc.stderr or proc.stdout or "").strip()
            if combined:
                error_msg = combined
            else:
                error_msg = f"Skanowanie nie powiodło się (kod {proc.returncode})."
        self.scanResultsReady.emit(devices, error_msg, auto)

    def _handle_scan_results(self, devices, error, auto: bool):
        self._scan_in_progress = False
        if hasattr(self, "btn_scan"):
            self.btn_scan.setEnabled(True)
            self.btn_scan.setText("scan")
        if error:
            if not auto:
                QMessageBox.warning(self, "Scan error", error)
            return
        if not devices:
            if not auto:
                QMessageBox.information(self, "Scan", "Nie znaleziono urządzeń SpotLED_.")
            return

        updated = False
        for mac, name in devices:
            mac = mac.strip().upper()
            name = name.strip()
            if mac not in self._discovered_devices or self._discovered_devices[mac] != name:
                self._discovered_devices[mac] = name
                updated = True
        if updated or not self.cb_mac.count():
            preferred = self.cb_mac.currentText().strip().upper() or devices[0][0]
            self._rebuild_mac_combobox(preferred)

    def _custom_font_char(self, spec, ch: str):
        glyph = spec["chars"].get(ch)
        if glyph is None:
            glyph = spec["chars"].get('?') or spec["chars"].get(' ') or next(iter(spec["chars"].values()))
        return glyph

    def _custom_font_speed_byte(self, slider_value: int) -> int:
        slider_min = getattr(self.sl_speed_txt, "minimum", lambda: 0)()
        slider_max = getattr(self.sl_speed_txt, "maximum", lambda: 255)()
        span = max(1, slider_max - slider_min)
        clamped = max(slider_min, min(slider_max, int(slider_value)))
        ratio = (clamped - slider_min) / span
        speed = int(round(ratio * 255))
        return max(0, min(255, speed))

    def _send_custom_font_text(self, sender, text: str, effect, speed_slider_value: int):
        spec = self._current_font_spec()
        if spec is None:
            raise ValueError("Brak danych czcionki.")
        if not text:
            raise ValueError("Wpisz tekst.")
        if spotled is None:
            raise RuntimeError("Brak biblioteki spotled.")
        width = spec["width"]
        height = spec["height"]
        font_chars = []
        cache = {}
        for ch in text:
            if ch in cache:
                continue
            glyph = self._custom_font_char(spec, ch)
            bitmap = spotled.gen_bitmap(*glyph, min_len=width)
            glyph_data = spotled.FontCharacterData(width, height, ch, bitmap)
            cache[ch] = glyph_data
            font_chars.append(glyph_data)
        if not font_chars:
            raise ValueError("Brak znaków do wysłania.")
        sender.send_data(spotled.SendDataCommand(spotled.FontData(font_chars).serialize()))
        speed_byte = self._custom_font_speed_byte(speed_slider_value)
        sender.send_data(spotled.SendDataCommand(spotled.TextData(text, speed_byte, effect).serialize()))

    def _store_project_dir(self, path: str):
        if not path:
            return
        self.cfg["project_dir"] = path
        save_cfg(self.cfg)

    def _project_dir(self) -> str:
        return self.cfg.get("project_dir") or os.path.expanduser("~")

    def _set_combo_value(self, combo: QComboBox, value: str):
        idx = combo.findText(value)
        if idx >= 0:
            combo.setCurrentIndex(idx)

    def _update_slider_display(self, slider: QSlider, label: QLabel, value: int):
        step = max(1, slider.singleStep())
        min_val = slider.minimum()
        max_val = slider.maximum()
        if value <= min_val:
            snapped = min_val
        else:
            snapped = int(round(value / step) * step)
            snapped = max(step, snapped)
            snapped = min(max_val, snapped)
        if snapped != value:
            slider.blockSignals(True)
            slider.setValue(snapped)
            slider.blockSignals(False)
            value = snapped
        label.setText(str(value))
        if getattr(self, "_is_playing", False) and slider is getattr(self, "sl_speed_img", None):
            self._apply_playback_speed()

    def _serialize_frames(self) -> List[List[str]]:
        frames_data = []
        for frame in self.frames:
            frame_rows = []
            for row in frame:
                frame_rows.append(''.join('1' if cell else '0' for cell in row))
            frames_data.append(frame_rows)
        return frames_data

    def _deserialize_frames(self, frames_data: List[List[str]]) -> List[List[List[bool]]]:
        if not frames_data:
            raise ValueError("No frame data.")
        frames: List[List[List[bool]]] = []
        for frame_rows in frames_data:
            if len(frame_rows) != GRID_H:
                raise ValueError("Invalid frame height.")
            frame: List[List[bool]] = []
            for row in frame_rows:
                if len(row) != GRID_W:
                    raise ValueError("Invalid frame width.")
                frame.append([c == '1' for c in row])
            frames.append(frame)
        return frames

    def _save_project(self):
        if not self._require_placement_confirmation():
            return
        base_dir = self._project_dir()
        suggested = os.path.join(base_dir, "spotled_project.json")
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save animation",
            suggested,
            "JSON (*.json);;All files (*)"
        )
        if not path:
            return

        data = {
            "version": 1,
            "tab": self.tabs.currentIndex(),
            "current_frame": self.cur,
            "image": {
                "frames": self._serialize_frames(),
                "effect": self.cb_effect_img.currentText(),
                "speed": int(self.sl_speed_img.value())
            },
            "text": {
                "content": self.le_text.text(),
                "effect": self.cb_effect_txt.currentText(),
                "speed": int(self.sl_speed_txt.value()),
                "two_lines": self.chk_two_lines.isChecked()
            }
        }

        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            QMessageBox.critical(self, "Save error", f"Could not save the file:\n{e}")
            return

        self._store_project_dir(os.path.dirname(path))
        self._current_project_path = path
        self._update_window_title()

    def _load_project(self):
        base_dir = self._project_dir()
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Load animation",
            base_dir,
            "JSON (*.json);;All files (*)"
        )
        if not path:
            return

        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            QMessageBox.critical(self, "Read error", f"Could not open the file:\n{e}")
            return

        self._store_project_dir(os.path.dirname(path))

        try:
            image_data = data.get("image", {})
            frames = self._deserialize_frames(image_data.get("frames", []))
            self.frames = frames
            self.cur = min(max(0, int(data.get("current_frame", 0))), len(self.frames) - 1)
            self._set_combo_value(self.cb_effect_img, image_data.get("effect", "NONE"))
            img_speed = int(image_data.get("speed", 100))
            self.sl_speed_img.setValue(min(3500, max(1, img_speed)))

            text_data = data.get("text", {})
            self.le_text.setText(text_data.get("content", ""))
            self._set_combo_value(self.cb_effect_txt, text_data.get("effect", "NONE"))
            txt_speed = int(text_data.get("speed", 100))
            self.sl_speed_txt.setValue(min(3500, max(1, txt_speed)))
            self.chk_two_lines.setChecked(bool(text_data.get("two_lines", False)))

            tab_idx = int(data.get("tab", 0))
            if 0 <= tab_idx < self.tabs.count():
                self.tabs.setCurrentIndex(tab_idx)

            self._reset_history()
            self._load_current_into_grid()
            self._refresh_counter()
            self._current_project_path = path
            self._update_window_title()
        except Exception as e:
            QMessageBox.critical(self, "Data error", f"The file has an invalid structure:\n{e}")
            return

    # --- Send ---
    def send_current(self):
        if not self._require_placement_confirmation():
            return
        if spotled is None:
            QMessageBox.critical(self, "Library missing", "Please install: pip install python-spotled")
            return

        mac = self.cb_mac.currentText().strip()
        if not mac:
            QMessageBox.critical(self, "Error", "Enter the MAC address.")
            return
        self._store_mac(mac)

        sender = None
        try:
            sender = spotled.LedConnection(mac)
            tab = self.tabs.currentIndex()

            if tab == 0:
                # IMAGE: multiple frames + effect + speed
                eff_name = self.cb_effect_img.currentText()
                eff = getattr(spotled.Effect, eff_name, spotled.Effect.NONE)
                speed = int(self.sl_speed_img.value())

                # ensure the current buffer is persisted in the model
                self.frames[self.cur] = self.grid.getPixelsCopy()

                frames_data = []
                for fpx in self.frames:
                    bmp = bitmap_string_from_pixels(fpx)
                    frames_data.append(spotled.FrameData(GRID_W, GRID_H, spotled.gen_bitmap(bmp)))

                # new API (AnimationData(frames, speed, ???, effect)) – fallback for older versions
                try:
                    anim = spotled.AnimationData(frames_data, speed, 0, eff)
                except TypeError:
                    # missing speed parameter in this signature?
                    try:
                        anim = spotled.AnimationData(frames_data, 0, 0, eff)
                    except Exception:
                        # ultimately fallback to NONE
                        anim = spotled.AnimationData(frames_data, 0, 0, spotled.Effect.NONE)

                cmd = spotled.SendDataCommand(anim.serialize())
                sender.send_data(cmd)
                QMessageBox.information(self, "OK", f"Sent {len(frames_data)} frames.")
            else:
                # TEXT
                text = self.le_text.text()
                if not text:
                    QMessageBox.critical(self, "Error", "Enter the text.")
                    return
                eff_name = self.cb_effect_txt.currentText()
                eff = getattr(spotled.Effect, eff_name, spotled.Effect.NONE)
                speed = int(self.sl_speed_txt.value())
                use_lines = self.chk_two_lines.isChecked()
                if self._current_font_choice() == FONT_ID_BUILTIN:
                    try:
                        if use_lines:
                            sender.set_text_lines(text, effect=eff, speed=speed)
                        else:
                            sender.set_text(text, effect=eff, speed=speed)
                    except TypeError:
                        if use_lines:
                            sender.set_text_lines(text, effect=eff)
                        else:
                            sender.set_text(text, effect=eff)
                    QMessageBox.information(self, "OK", "Text sent.")
                else:
                    if use_lines:
                        QMessageBox.warning(self, "Font", "Niestandardowe czcionki obsługują tylko pojedynczą linię tekstu.")
                        return
                    try:
                        self._send_custom_font_text(sender, text, eff, speed)
                    except Exception as font_err:
                        QMessageBox.critical(self, "Font error", str(font_err))
                        return
                    QMessageBox.information(self, "OK", "Text sent.")
        except Exception as e:
            QMessageBox.critical(self, "Send error", str(e))
        finally:
            if sender is not None:
                self._safe_disconnect(sender)

def main():
    app = QApplication(sys.argv)
    w = Main()
    w.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
