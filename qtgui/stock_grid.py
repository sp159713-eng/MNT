from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QListView, QListWidget,
                               QListWidgetItem, QStyle, QStyledItemDelegate)
from qfluentwidgets import (CaptionLabel, SmoothScrollDelegate, isDarkTheme,
                            qconfig, themeColor)

from qtgui.common import PAIRS, set_tone
from qtgui.stock_data import quotes

TILE_W = 196
TILE_H = 196
GAP = 12
PAD = 12
LINE = 16


def _font(size, weight=QFont.Normal):
    font = QFont("Segoe UI")
    font.setPixelSize(size)
    font.setWeight(weight)
    return font


FONTS = {"symbol": _font(13, QFont.DemiBold), "small": _font(11),
         "price": _font(20, QFont.DemiBold)}


def _tone(name):
    light, dark = PAIRS[name]
    return QColor(dark if isDarkTheme() else light)


def _ink(alpha):
    return QColor(255, 255, 255, alpha) if isDarkTheme() else QColor(0, 0, 0, alpha)


def _pct_tone(value):
    return "muted" if value is None else ("good" if value >= 0 else "bad")


class TileDelegate(QStyledItemDelegate):
    def __init__(self, grid):
        super().__init__(grid)
        self.grid = grid

    def sizeHint(self, option, index):
        return QSize(self.grid.tile_width, self.grid.tile_height)

    def paint(self, painter, option, index):
        data = index.data(Qt.UserRole)
        if not data:
            return
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        card = QRectF(option.rect).adjusted(GAP / 2, GAP / 2, -GAP / 2, -GAP / 2)
        hover = bool(option.state & QStyle.State_MouseOver)
        selected = bool(option.state & QStyle.State_Selected)
        if isDarkTheme():
            fill = QColor(255, 255, 255, 22 if hover else 13)
        else:
            fill = QColor(255, 255, 255, 255 if hover else 200)
        painter.setPen(QPen(themeColor(), 2) if selected else QPen(_ink(22), 1))
        painter.setBrush(fill)
        painter.drawRoundedRect(card, 6, 6)
        inner = card.adjusted(PAD, 8, -PAD, -8)
        painter.drawPixmap(inner.topLeft(), self._cached(data, inner.size()))
        painter.restore()

    def _cached(self, data, size):
        key = (data["row"], id(data.get("quote")), id(data.get("details")),
               isDarkTheme(), int(size.width()), int(size.height()))
        pixmap = self.grid.cache.get(key)
        if pixmap is None:
            ratio = self.grid.devicePixelRatioF()
            pixmap = QPixmap(int(size.width() * ratio), int(size.height() * ratio))
            pixmap.setDevicePixelRatio(ratio)
            pixmap.fill(Qt.transparent)
            cache_painter = QPainter(pixmap)
            cache_painter.setRenderHint(QPainter.Antialiasing)
            cache_painter.setRenderHint(QPainter.TextAntialiasing)
            self._content(cache_painter, QRectF(0, 0, size.width(), size.height()), data)
            cache_painter.end()
            self.grid.cache[key] = pixmap
        return pixmap

    def _text(self, painter, rect, text, font, colour, align=Qt.AlignLeft):
        painter.setFont(font)
        painter.setPen(colour)
        painter.drawText(rect, align | Qt.AlignVCenter, text)

    def _content(self, painter, box, data):
        row, quote = data["row"], data.get("quote") or {}
        x, w, y = box.left(), box.width(), box.top()
        badges = []
        if quote.get("pick"):
            badges.append(("top 5", "good"))
        if row[3] == "watchlist":
            badges.append(("watch", "warn"))
        right = x + w
        painter.setFont(FONTS["small"])
        for text, tone in badges:
            width = painter.fontMetrics().horizontalAdvance(text) + 18
            self._text(painter, QRectF(right - width, y, width, 18), text,
                       FONTS["small"], _tone(tone), Qt.AlignRight)
            right -= width
        self._text(painter, QRectF(x, y, right - x, 18), row[0],
                   FONTS["symbol"], _ink(235))
        y += 20
        painter.setFont(FONTS["small"])
        name = painter.fontMetrics().elidedText(quote.get("name") or "-",
                                                Qt.ElideRight, int(w))
        self._text(painter, QRectF(x, y, w, LINE), name, FONTS["small"],
                   _ink(150))
        y += LINE + 4
        self._spark(painter, QRectF(x, y, w, 28), quote.get("spark") or [])
        y += 32
        close = quote.get("close")
        self._text(painter, QRectF(x, y, w, 26),
                   f"{close:,.2f}" if close is not None else "-",
                   FONTS["price"], _ink(240))
        day = quote.get("day")
        if day is not None:
            self._text(painter, QRectF(x, y + 6, w, 20), f"{day:+.2f}%",
                       FONTS["small"], _tone(_pct_tone(day)), Qt.AlignRight)
        y += 28
        month, year = quote.get("month"), quote.get("year")
        left = x
        for prefix, value in (("1M", month), ("1Y", year)):
            text = f"{prefix} -" if value is None else f"{prefix} {value:+.1f}%"
            self._text(painter, QRectF(left, y, w, LINE), text, FONTS["small"],
                       _tone(_pct_tone(value)))
            left += painter.fontMetrics().horizontalAdvance(text) + 14
        y += LINE + 6
        low, high = quote.get("low"), quote.get("high")
        painter.setPen(Qt.NoPen)
        painter.setBrush(_ink(40 if isDarkTheme() else 30))
        painter.drawRoundedRect(QRectF(x, y + 1, w, 4), 2, 2)
        if close is not None and low is not None and high > low:
            where = (close - low) / (high - low)
            painter.setBrush(themeColor())
            painter.drawEllipse(QPointF(x + 3 + where * (w - 6), y + 3), 3, 3)
            label = f"52w {low:,.0f} - {high:,.0f}"
        else:
            label = "52w -"
        y += 8
        self._text(painter, QRectF(x, y, w, LINE), label, FONTS["small"],
                   _ink(150))
        y += LINE + 4
        painter.setFont(FONTS["small"])
        for text, tone in data.get("details") or ():
            colour = _tone(tone) if tone != "muted" else _ink(150)
            bound = painter.fontMetrics().boundingRect(
                0, 0, int(w), 1000, Qt.TextWordWrap, text)
            painter.setPen(colour)
            painter.drawText(QRectF(x, y, w, bound.height()),
                             Qt.AlignLeft | Qt.TextWordWrap, text)
            y += bound.height() + 2

    def _spark(self, painter, rect, values):
        if len(values) < 2:
            return
        lo, hi = min(values), max(values)
        span = (hi - lo) or 1.0
        step = rect.width() / (len(values) - 1)
        path = QPainterPath()
        for i, v in enumerate(values):
            point = QPointF(rect.left() + i * step,
                            rect.top() + 2 + (rect.height() - 4)
                            * (1 - (v - lo) / span))
            if i:
                path.lineTo(point)
            else:
                path.moveTo(point)
        painter.setPen(QPen(_tone("good" if values[-1] >= values[0] else "bad"),
                            1.6))
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(path)


class StockGrid(QListWidget):
    selectionChanged = Signal()
    activated = Signal()

    def __init__(self, empty_text, parent=None, tile_height=TILE_H):
        super().__init__(parent)
        self.tile_height = tile_height
        self.tile_width = TILE_W + GAP
        self.rows = []
        self.quotes = {}
        self.details = {}
        self.cache = {}
        self.setViewMode(QListView.IconMode)
        self.setResizeMode(QListView.Adjust)
        self.setMovement(QListView.Static)
        self.setUniformItemSizes(True)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.verticalScrollBar().setSingleStep(24)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setMouseTracking(True)
        self.setFrameShape(QListView.NoFrame)
        self.setItemDelegate(TileDelegate(self))
        self.scroll_delegate = SmoothScrollDelegate(self)
        self.setStyleSheet("QListWidget{background:transparent;border:none;"
                           "outline:none}")
        self.empty = CaptionLabel(empty_text, self)
        self.empty.setAlignment(Qt.AlignCenter)
        set_tone(self.empty, "muted")
        self.itemSelectionChanged.connect(self.selectionChanged.emit)
        self.itemDoubleClicked.connect(lambda _i: self.activated.emit())
        qconfig.themeChanged.connect(lambda _t: self._refresh())

    def _refresh(self):
        self.cache.clear()
        self.viewport().update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.empty.setGeometry(0, 0, self.width(), 40)
        self._fit()

    def _fit(self):
        room = self.viewport().width() - 2
        cols = max(1, room // (TILE_W + GAP))
        width = max(TILE_W + GAP, room // cols)
        if width != self.tile_width:
            self.tile_width = width
            self.setGridSize(QSize(width, self.tile_height))
            self.doItemsLayout()

    def set_rows(self, rows):
        rows = [tuple(r) for r in rows]
        if rows == self.rows and self.count():
            return False
        keep = self.selected()
        self.rows = rows
        self.details = {}
        self.cache.clear()
        self.blockSignals(True)
        self.clear()
        self.setGridSize(QSize(self.tile_width, self.tile_height))
        for row in rows:
            item = QListWidgetItem(row[0])
            item.setData(Qt.UserRole, {"row": row,
                                       "quote": self.quotes.get(row[0])})
            item.setSizeHint(QSize(self.tile_width, self.tile_height))
            self.addItem(item)
            if keep and row[0] == keep[0]:
                item.setSelected(True)
                self.setCurrentItem(item)
        self.blockSignals(False)
        self.empty.setVisible(not rows)
        self._fit()
        return True

    def _update(self, symbol, key, value):
        for i in range(self.count()):
            item = self.item(i)
            data = item.data(Qt.UserRole)
            if data["row"][0] == symbol:
                data = dict(data)
                data[key] = value
                item.setData(Qt.UserRole, data)
                return

    def set_quotes(self, data):
        self.quotes.update(data)
        for i in range(self.count()):
            item = self.item(i)
            current = item.data(Qt.UserRole)
            quote = data.get(current["row"][0])
            if quote:
                item.setData(Qt.UserRole, dict(current, quote=quote))
        self._refresh()

    def set_details(self, symbol, lines):
        self._update(symbol, "details", list(lines))
        self._refresh()

    def symbols(self):
        return [row[0] for row in self.rows]

    def selected(self):
        items = self.selectedItems()
        return items[0].data(Qt.UserRole)["row"] if items else None

    def quote(self, symbol):
        return self.quotes.get(symbol)


__all__ = ["StockGrid", "quotes"]
