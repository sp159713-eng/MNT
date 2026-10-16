import csv
import io
import os

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (CaptionLabel, CardWidget, FlowLayout, ScrollArea,
                            StrongBodyLabel, SubtitleLabel, isDarkTheme,
                            qconfig, themeColor)

from qtgui.common import PAIRS, set_tone

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(BASE_DIR, "data_cache")
NAMES_FILE = os.path.join(CACHE, "EQUITY_L.csv")
NAMES_URL = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"
TILE_W = 196
TILE_H = 196


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


def history(symbol, bars=260):
    path = os.path.join(CACHE, f"{symbol}_NS_1d_adj.csv")
    try:
        with open(path, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            handle.seek(max(0, handle.tell() - 90 * (bars + 2)))
            lines = handle.read().decode("utf-8", "replace").strip().splitlines()
    except OSError:
        return []
    rows = []
    for line in lines[1:][-bars:]:
        parts = line.split(",")
        try:
            rows.append((parts[0], float(parts[4])))
        except (IndexError, ValueError):
            continue
    return rows


def _ret(closes, back):
    if len(closes) <= back or not closes[-1 - back]:
        return None
    return (closes[-1] / closes[-1 - back] - 1) * 100


def top_picks():
    try:
        import json

        with open(os.path.join(BASE_DIR, "artifacts", "predictions.json"),
                  encoding="utf-8") as handle:
            made = json.load(handle)
        return set(made[-1].get("top5") or [])
    except Exception:
        return set()


def quotes(symbols):
    names = company_names()
    picks = top_picks()
    out = {}
    for symbol in symbols:
        rows = history(symbol)
        closes = [c for _, c in rows]
        quote = {"name": names.get(symbol.upper(), ""), "pick": symbol in picks,
                 "date": rows[-1][0] if rows else None,
                 "close": closes[-1] if closes else None,
                 "day": _ret(closes, 1), "month": _ret(closes, 21),
                 "year": _ret(closes, 251), "spark": closes[-63:]}
        if closes:
            year = closes[-252:]
            quote["low"], quote["high"] = min(year), max(year)
        out[symbol] = quote
    return out


class Spark(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.values = []
        self.setFixedHeight(28)

    def set_values(self, values):
        self.values = list(values)
        self.update()

    def paintEvent(self, event):
        if len(self.values) < 2:
            return
        from PySide6.QtCore import QPointF
        from PySide6.QtGui import QPainter, QPainterPath, QPen

        lo, hi = min(self.values), max(self.values)
        span = (hi - lo) or 1.0
        w, h = self.width() - 2, self.height() - 4
        step = w / (len(self.values) - 1)
        path = QPainterPath()
        for i, v in enumerate(self.values):
            point = QPointF(1 + i * step, 2 + h - (v - lo) / span * h)
            if i:
                path.lineTo(point)
            else:
                path.moveTo(point)
        light, dark = PAIRS["good" if self.values[-1] >= self.values[0] else "bad"]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(QColor(dark if isDarkTheme() else light), 1.6))
        painter.drawPath(path)


class RangeBar(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.where = None
        self.setFixedHeight(6)

    def set_where(self, where):
        self.where = where
        self.update()

    def paintEvent(self, event):
        from PySide6.QtGui import QPainter

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        track = QColor(255, 255, 255, 40) if isDarkTheme() else QColor(0, 0, 0, 30)
        painter.setBrush(track)
        painter.drawRoundedRect(0, 1, self.width(), 4, 2, 2)
        if self.where is None:
            return
        x = 3 + self.where * (self.width() - 6)
        painter.setBrush(themeColor())
        painter.drawEllipse(int(x) - 3, 0, 6, 6)


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
        box.setContentsMargins(12, 8, 12, 10)
        box.setSpacing(3)
        top = QHBoxLayout()
        top.setSpacing(6)
        self.symbol = StrongBodyLabel(row[0], self)
        top.addWidget(self.symbol, 1)
        self.pick = CaptionLabel("top 5", self)
        set_tone(self.pick, "good")
        self.pick.hide()
        top.addWidget(self.pick)
        if row[3] == "watchlist":
            badge = CaptionLabel("watch", self)
            set_tone(badge, "warn")
            top.addWidget(badge)
        box.addLayout(top)
        self.company = CaptionLabel("", self)
        set_tone(self.company, "muted")
        box.addWidget(self.company)
        self.spark = Spark(self)
        box.addWidget(self.spark)
        middle = QHBoxLayout()
        middle.setSpacing(8)
        self.price = SubtitleLabel("-", self)
        middle.addWidget(self.price)
        middle.addStretch(1)
        self.change = CaptionLabel("", self)
        middle.addWidget(self.change, 0, Qt.AlignBottom)
        box.addLayout(middle)
        returns = QHBoxLayout()
        returns.setSpacing(12)
        self.month = CaptionLabel("", self)
        self.year = CaptionLabel("", self)
        returns.addWidget(self.month)
        returns.addWidget(self.year)
        returns.addStretch(1)
        box.addLayout(returns)
        self.range = RangeBar(self)
        box.addWidget(self.range)
        self.range_text = CaptionLabel("", self)
        set_tone(self.range_text, "muted")
        box.addWidget(self.range_text)
        self.clicked.connect(lambda: self.picked.emit(self))

    def _pct(self, label, prefix, value):
        if value is None:
            label.setText(f"{prefix} -")
            set_tone(label, "muted")
            return
        label.setText(f"{prefix} {value:+.1f}%")
        set_tone(label, "good" if value >= 0 else "bad")

    def set_quote(self, quote):
        name = quote.get("name") or "-"
        metrics = self.company.fontMetrics()
        self.company.setText(metrics.elidedText(name, Qt.ElideRight,
                                                self.width() - 24))
        self.company.setToolTip(name)
        close = quote.get("close")
        self.price.setText(f"{close:,.2f}" if close is not None else "-")
        date = quote.get("date")
        self.price.setToolTip(f"close {date}" if date else "no cached prices")
        day = quote.get("day")
        self.change.setText(f"{day:+.2f}%" if day is not None else "")
        if day is not None:
            set_tone(self.change, "good" if day >= 0 else "bad")
        self._pct(self.month, "1M", quote.get("month"))
        self._pct(self.year, "1Y", quote.get("year"))
        self.spark.set_values(quote.get("spark") or [])
        self.spark.setToolTip("last 3 months")
        self.pick.setVisible(bool(quote.get("pick")))
        low, high = quote.get("low"), quote.get("high")
        if close is not None and low is not None and high > low:
            self.range.set_where((close - low) / (high - low))
            self.range_text.setText(f"52w {low:,.0f} - {high:,.0f}")
        else:
            self.range.set_where(None)
            self.range_text.setText("52w -")

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
                tile.set_quote(quote)

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
