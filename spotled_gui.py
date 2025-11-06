#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import json, os, sys, copy
from dataclasses import dataclass
from typing import List, Optional, Tuple

from PySide6.QtCore import Qt, QSize, QRect, Signal, QObject
from PySide6.QtGui import QPainter, QPen, QBrush, QColor, QIcon, QPixmap, QImage
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QComboBox, QTabWidget, QSlider, QLineEdit, QMessageBox, QToolButton,
    QSizePolicy, QCheckBox, QFileDialog
)

# pip install python-spotled
try:
    import spotled
except Exception:
    spotled = None

GRID_W, GRID_H = 48, 12
CELL = 16
CFG_PATH = os.path.join(os.path.expanduser("~"), ".spotled_gui.json")

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
                return data
        except Exception:
            pass
    return {"mac_history": [], "project_dir": ""}

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

class Main(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("SpotLED – Qt6 GUI (frames)")
        self.cfg = load_cfg()
        self.setWindowIcon(self._build_app_icon())
        self.setFixedSize(self.sizeHint())
        self.setWindowFlag(Qt.MSWindowsFixedSizeDialogHint, True)
        self.setWindowFlag(Qt.WindowMaximizeButtonHint, False)
        self._center_pending = True

        # --- frame model ---
        self.frames: List[List[List[bool]]] = [ [[False]*GRID_W for _ in range(GRID_H)] ]
        self.cur = 0

        # central widget
        cw = QWidget()
        root = QVBoxLayout(cw)
        self.setCentralWidget(cw)

        # Tabs
        self.tabs = QTabWidget()
        self.tabs.setStyleSheet("QTabBar::tab { font-size:32px; padding:12px 18px; }")
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

        # Tools (2× larger)
        tools_col = QVBoxLayout()
        img_h.addLayout(tools_col)

        self.btn_draw = QToolButton(); self.btn_draw.setText("✏️")
        self.btn_shift = QToolButton(); self.btn_shift.setIcon(self._build_shift_icon())
        self.btn_shift.setIconSize(QSize(40, 40))
        self.btn_shift.setToolTip("Przesuń wszystkie piksele")
        self.btn_clear = QToolButton(); self.btn_clear.setText("🧹")
        for b in (self.btn_draw, self.btn_shift, self.btn_clear):
            b.setFixedSize(64, 64)  # 2× larger
            b.setStyleSheet("font-size:24px;")
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

        self.btn_prev = QToolButton(); self.btn_prev.setText("⬅️")
        self.btn_next = QToolButton(); self.btn_next.setText("➡️")
        self.btn_add  = QToolButton(); self.btn_add.setText("➕")
        self.btn_remove = QToolButton(); self.btn_remove.setText("🗑️")
        self.btn_copy_prev = QToolButton(); self.btn_copy_prev.setText("📄")
        self.btn_copy_prev.setToolTip("Kopiuj poprzednią klatkę")
        self.btn_import_png = QToolButton(); self.btn_import_png.setText("🖼️")
        self.btn_import_png.setToolTip("Importuj klatkę z PNG")
        self.btn_undo = QToolButton(); self.btn_undo.setText("↩️")
        self.btn_undo.setToolTip("Cofnij")
        self.btn_redo = QToolButton(); self.btn_redo.setText("↪️")
        self.btn_redo.setToolTip("Ponów")
        for b in (self.btn_prev, self.btn_next, self.btn_add, self.btn_remove, self.btn_copy_prev, self.btn_import_png, self.btn_undo, self.btn_redo):
            b.setFixedSize(64, 64)
            b.setStyleSheet("font-size:24px;")

        self.lbl_counter = QLabel("1/1")

        self.btn_prev.clicked.connect(self._prev_frame)
        self.btn_next.clicked.connect(self._next_frame)
        self.btn_add.clicked.connect(self._add_frame)
        self.btn_remove.clicked.connect(self._remove_current_frame)
        self.btn_copy_prev.clicked.connect(self._copy_from_previous_frame)
        self.btn_import_png.clicked.connect(self._import_image_frame)
        self.btn_undo.clicked.connect(self._undo)
        self.btn_redo.clicked.connect(self._redo)

        frames_row.addWidget(self.btn_prev)
        frames_row.addWidget(self.btn_next)
        frames_row.addWidget(self.btn_add)
        frames_row.addWidget(self.btn_remove)
        frames_row.addWidget(self.btn_copy_prev)
        frames_row.addWidget(self.btn_import_png)
        frames_row.addWidget(self.btn_undo)
        frames_row.addWidget(self.btn_redo)
        frames_row.addStretch(1)
        frames_row.addWidget(self.lbl_counter)

        # Effect + speed for image animations
        img_opts = QHBoxLayout()
        img_v.addLayout(img_opts)
        lbl_effect_img = QLabel("✨")
        lbl_effect_img.setStyleSheet("font-size:32px;")
        img_opts.addWidget(lbl_effect_img)
        self.cb_effect_img = QComboBox()
        self.cb_effect_img.addItems(["NONE","SCROLL_UP","SCROLL_DOWN","SCROLL_LEFT","SCROLL_RIGHT","STACK","EXPAND","LASER"])
        self.cb_effect_img.setStyleSheet("font-size:20px;")
        img_opts.addWidget(self.cb_effect_img)
        img_opts.addSpacing(12)
        lbl_speed_icon_img = QLabel("🏃")
        lbl_speed_icon_img.setStyleSheet("font-size:32px;")
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
        self._update_slider_display(self.sl_speed_img, self.lbl_speed_img, self.sl_speed_img.value())

        # --- Tab: Text ---
        t_txt = QWidget()
        self.tabs.addTab(t_txt, "🅣")
        txt_v = QVBoxLayout(t_txt)

        row1 = QHBoxLayout()
        lbl_text_icon = QLabel("💬")
        lbl_text_icon.setStyleSheet("font-size:32px;")
        row1.addWidget(lbl_text_icon)
        self.le_text = QLineEdit()
        self.le_text.setStyleSheet("font-size:32px; color:#4eff00; background-color:#020302;")
        row1.addWidget(self.le_text, 1)
        txt_v.addLayout(row1)

        row1b = QHBoxLayout()
        self.chk_two_lines = QCheckBox("〰️〰️")
        self.chk_two_lines.setStyleSheet("font-size:32px;")
        row1b.addWidget(self.chk_two_lines)
        row1b.addStretch(1)
        txt_v.addLayout(row1b)

        row2 = QHBoxLayout()
        lbl_effect_txt = QLabel("✨")
        lbl_effect_txt.setStyleSheet("font-size:32px;")
        row2.addWidget(lbl_effect_txt)
        self.cb_effect_txt = QComboBox()
        self.cb_effect_txt.addItems(["NONE","SCROLL_UP","SCROLL_DOWN","SCROLL_LEFT","SCROLL_RIGHT","STACK","EXPAND","LASER"])
        self.cb_effect_txt.setStyleSheet("font-size:20px;")
        row2.addWidget(self.cb_effect_txt)
        row2.addStretch(1)
        lbl_speed_icon_txt = QLabel("🏃")
        lbl_speed_icon_txt.setStyleSheet("font-size:32px;")
        row2.addWidget(lbl_speed_icon_txt)
        self.sl_speed_txt = QSlider(Qt.Horizontal)
        self.sl_speed_txt.setRange(1, 3500)
        self.sl_speed_txt.setSingleStep(50)
        self.sl_speed_txt.setPageStep(100)
        self.sl_speed_txt.setValue(100)
        self.lbl_speed_txt = QLabel("100")
        self.sl_speed_txt.valueChanged.connect(lambda v: self._update_slider_display(self.sl_speed_txt, self.lbl_speed_txt, v))
        row2.addWidget(self.sl_speed_txt, 2)
        row2.addWidget(self.lbl_speed_txt)
        txt_v.addLayout(row2)
        self._update_slider_display(self.sl_speed_txt, self.lbl_speed_txt, self.sl_speed_txt.value())

        # --- Tab: Info ---
        t_info = QWidget()
        self.tabs.addTab(t_info, "ℹ️")
        info_layout = QVBoxLayout(t_info)
        info_layout.addStretch(1)
        lbl_info = QLabel("SpotLED GUI  (2025)\nby Adam Mateusz Brożyński\nbased on python-spotled \nby Izzie Walton")
        lbl_info.setAlignment(Qt.AlignCenter)
        lbl_info.setStyleSheet("font-size:20px; color:#4eff00;")
        info_layout.addWidget(lbl_info)
        info_layout.addStretch(1)

        # --- Shared: MAC + Send ---
        row_mac = QHBoxLayout()
        mac_icon = QLabel("🖧")
        mac_icon.setStyleSheet("font-size:32px;")
        row_mac.addWidget(mac_icon)
        self.cb_mac = QComboBox(); self.cb_mac.setEditable(True)
        self.cb_mac.setStyleSheet("font-size:20px;")
        self.cb_mac.addItems(self.cfg.get("mac_history", []))
        if self.cfg.get("mac_history"):
            self.cb_mac.setCurrentText(self.cfg["mac_history"][0])
        row_mac.addWidget(self.cb_mac, 1)
        self.btn_load = QToolButton(); self.btn_load.setText("📂")
        self.btn_save = QToolButton(); self.btn_save.setText("💾")
        for btn in (self.btn_load, self.btn_save):
            btn.setFixedSize(64, 64)
            btn.setStyleSheet("font-size:32px;")
        self.btn_load.clicked.connect(self._load_project)
        self.btn_save.clicked.connect(self._save_project)
        row_mac.addWidget(self.btn_load)
        row_mac.addWidget(self.btn_save)
        self.btn_send = QToolButton(); self.btn_send.setText("📤")
        self.btn_send.setFixedSize(64, 64)
        self.btn_send.setStyleSheet("font-size:32px;")
        self.btn_send.clicked.connect(self.send_current)
        row_mac.addWidget(self.btn_send)
        root.addLayout(row_mac)

        self._reset_history()
        self._refresh_counter()

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
        self._update_history_buttons()

    def _grid_action_started(self):
        self._begin_action(self.cur)

    def _grid_action_finished(self):
        self._finish_action()

    def _placement_confirmed(self):
        self._commit_imported_image()

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
        self.cb_mac.clear()
        self.cb_mac.addItems(self.cfg["mac_history"])
        self.cb_mac.setCurrentText(mac)

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
