import csv
import io
import os

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (CaptionLabel, CardWidget, FlowLayout, ScrollArea,
                            StrongBodyLabel, SubtitleLabel,
                            qconfig, themeColor)

from qtgui.common import set_tone

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(BASE_DIR, "data_cache")
NAMES_FILE = os.path.join(CACHE, "EQUITY_L.csv")
NAMES_URL = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"
TILE_W = 196
TILE_H = 104


def company_names():
    text = None
    if os.path.exists(NAMES_FILE):
        with open(NAMES_FILE, encoding="utf-8", errors="replace") as handle:
            text = handle.read()
    else:
        try:
            import requests

            reply = requests.get(NAMES_URL, timeout=20, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                              "AppleWebKit/537.36 Chrome/120 Safari/537.36"})
            if reply.status_code == 200 and reply.text.startswith("SYMBOL"):
                text = reply.text
                os.makedirs(CACHE, exist_ok=True)
                with open(NAMES_FILE, "w", encoding="utf-8") as handle:
                    handle.write(text)
        except Exception:
            text = None
    if not text:
        return {}
    names = {}
    for row in csv.reader(io.StringIO(text)):
        if len(row) > 1 and row[0] != "SYMBOL":
            names[row[0].strip().upper()] = row[1].strip()
    return names


def last_closes(symbol):
    path = os.path.join(CACHE, f"{symbol}_NS_1d_adj.csv")
    try:
        with open(path, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            handle.seek(max(0, handle.tell() - 512))
            lines = handle.read().decode("utf-8", "replace").strip().splitlines()
    except OSError:
        return None
    rows = []
    for line in lines[-2:]:
        parts = line.split(",")
        try:
            rows.append((parts[0], float(parts[4])))
        except (IndexError, ValueError):
            continue
    if not rows:
        return None
    date, close = rows[-1]
    change = (close / rows[0][1] - 1) * 100 if len(rows) == 2 and rows[0][1] else None
    return date, close, change


def quotes(symbols):
    names = company_names()
    out = {}
    for symbol in symbols:
        last = last_closes(symbol)
        out[symbol] = (names.get(symbol.upper(), ""),) + (last or (None, None, None))
    return out


class StockTile(CardWidget):
    picked = Signal(object)
    opened = Signal(object)

    def __init__(self, row, parent=None):
        super().__init__(parent)
        self.row = row
        self.selected = False
        self.setFixedSize(TILE_W, TILE_H)
        self.setCursor(Qt.PointingHandCursor)
        box = QVBoxLayout(self)
        box.setContentsMargins(12, 8, 12, 8)
        box.setSpacing(2)
        top = QHBoxLayout()
        top.setSpacing(4)
        self.symbol = StrongBodyLabel(row[0], self)
        top.addWidget(self.symbol, 1)
        if row[3] == "watchlist":
            badge = CaptionLabel("watch", self)
            set_tone(badge, "warn")
            top.addWidget(badge)
        box.addLayout(top)
        self.company = CaptionLabel("", self)
        set_tone(self.company, "muted")
        box.addWidget(self.company)
        box.addStretch(1)
        bottom = QHBoxLayout()
        bottom.setSpacing(8)
        self.price = SubtitleLabel("-", self)
        bottom.addWidget(self.price)
        bottom.addStretch(1)
        self.change = CaptionLabel("", self)
        bottom.addWidget(self.change, 0, Qt.AlignBottom)
        box.addLayout(bottom)
        self.clicked.connect(lambda: self.picked.emit(self))

    def set_quote(self, name, date, close, change):
        metrics = self.company.fontMetrics()
        self.company.setText(metrics.elidedText(name or "-", Qt.ElideRight,
                                                self.width() - 24))
        self.company.setToolTip(name or "")
        self.price.setText(f"{close:,.2f}" if close is not None else "-")
        self.price.setToolTip(f"close {date}" if date else "no cached prices")
        if change is None:
            self.change.setText("")
            return
        self.change.setText(f"{change:+.2f}%")
        set_tone(self.change, "good" if change >= 0 else "bad")

    def set_selected(self, selected):
        self.selected = selected
        self.update()

    def mouseDoubleClickEvent(self, event):
        super().mouseDoubleClickEvent(event)
        self.opened.emit(self)

    def paintEvent(self, event):
        super().paintEvent(event)
        if not self.selected:
            return
        from PySide6.QtGui import QPainter, QPen

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(themeColor(), 2))
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 6, 6)


class StockGrid(ScrollArea):
    selectionChanged = Signal()
    activated = Signal()

    def __init__(self, empty_text, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.inner = QWidget(self)
        self.inner.setObjectName("stockGridInner")
        self.flow = FlowLayout(self.inner, needAni=False)
        self.flow.setContentsMargins(0, 0, 0, 0)
        self.flow.setHorizontalSpacing(12)
        self.flow.setVerticalSpacing(12)
        self.setWidget(self.inner)
        self.empty = CaptionLabel(empty_text, self)
        self.empty.setAlignment(Qt.AlignCenter)
        set_tone(self.empty, "muted")
        self.tiles = []
        self.rows = []
        self.current = None
        self._restyle()
        qconfig.themeChanged.connect(lambda _t: self._restyle())

    def _restyle(self):
        self.setStyleSheet("QScrollArea{background:transparent;border:none}")
        self.inner.setStyleSheet("#stockGridInner{background:transparent}")

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.empty.setGeometry(0, 0, self.width(), 40)
        self._fit()

    def _fit(self):
        room = self.viewport().width() - 4
        cols = max(1, (room + 12) // (TILE_W + 12))
        width = max(TILE_W, (room - 12 * (cols - 1)) // cols)
        for tile in self.tiles:
            tile.setFixedWidth(width)

    def set_rows(self, rows):
        rows = [tuple(r) for r in rows]
        if rows == self.rows and self.tiles:
            return False
        self.rows = rows
        keep = self.current.row[0] if self.current else None
        self.flow.takeAllWidgets()
        for tile in self.tiles:
            tile.deleteLater()
        self.tiles, self.current = [], None
        for row in rows:
            tile = StockTile(row, self.inner)
            tile.picked.connect(self._pick)
            tile.opened.connect(self._open)
            self.flow.addWidget(tile)
            self.tiles.append(tile)
            if row[0] == keep:
                self._pick(tile, quiet=True)
        self.empty.setVisible(not rows)
        self._fit()
        self.inner.adjustSize()
        return True

    def set_quotes(self, data):
        for tile in self.tiles:
            quote = data.get(tile.row[0])
            if quote:
                tile.set_quote(*quote)

    def symbols(self):
        return [tile.row[0] for tile in self.tiles]

    def selected(self):
        return self.current.row if self.current else None

    def _pick(self, tile, quiet=False):
        if self.current is not None and self.current is not tile:
            self.current.set_selected(False)
        self.current = tile
        tile.set_selected(True)
        if not quiet:
            self.selectionChanged.emit()

    def _open(self, tile):
        self._pick(tile)
        self.activated.emit()
