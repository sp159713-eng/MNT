from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (QColor, QFont, QFontMetrics, QPainter, QPen,
                           QPolygonF)
from PySide6.QtWidgets import QSizePolicy, QWidget
from qfluentwidgets import isDarkTheme, qconfig, themeColor

import theme as theme_module
from qtgui.common import PAIRS, tone_color


def _default_formatter(value):
    return f"{value:,.0f}"


def _scheme():
    return theme_module.Dark if isDarkTheme() else theme_module.Light


def _colour(value):
    if value is None:
        return themeColor()
    if isinstance(value, QColor):
        return value
    if value in PAIRS:
        return tone_color(value)
    return QColor(value)


class Chart(QWidget):
    PAD_LEFT, PAD_RIGHT, PAD_TOP, PAD_BOTTOM = 52, 14, 14, 26

    def __init__(self, parent=None, height=200):
        super().__init__(parent)
        self.setMinimumHeight(height)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMouseTracking(True)
        self._render = None
        self._points = []
        self._series = []
        self._readout = None
        self._mouse_x = None
        self.mono = QFont("Consolas")
        self.mono.setPointSizeF(8.5)
        self.small = QFont(self.font())
        self.small.setPointSizeF(9)
        qconfig.themeChanged.connect(lambda *_: self.update())

    def _plot_area(self):
        width = max(self.width(), 260)
        height = max(self.height(), 120)
        return (self.PAD_LEFT, self.PAD_TOP, width - self.PAD_RIGHT,
                height - self.PAD_BOTTOM)

    def _text(self, painter, x, y, text, colour, anchor="w", font=None):
        painter.setFont(font or self.mono)
        painter.setPen(colour)
        metrics = QFontMetrics(painter.font())
        width = metrics.horizontalAdvance(text)
        left = x - width if anchor == "e" else (
            x - width / 2 if anchor == "c" else x)
        painter.drawText(QPointF(left, y + metrics.ascent() / 2 - 1), text)

    def _frame(self, painter, lo, hi, labels, formatter):
        scheme = _scheme()
        x0, y0, x1, y1 = self._plot_area()
        pen = QPen(QColor(scheme.border))
        pen.setStyle(Qt.DashLine)
        for i in range(labels + 1):
            fraction = i / labels
            y = y1 - fraction * (y1 - y0)
            value = lo + fraction * (hi - lo)
            painter.setPen(pen)
            painter.drawLine(QPointF(x0, y), QPointF(x1, y))
            self._text(painter, x0 - 8, y, formatter(value),
                       QColor(scheme.muted), "e")

    def _dashed(self, painter, colour, x0, y, x1, width=1.0):
        pen = QPen(colour, width)
        pen.setStyle(Qt.DashLine)
        painter.setPen(pen)
        painter.drawLine(QPointF(x0, y), QPointF(x1, y))

    def _poly(self, painter, points, colour, width, dashed=False):
        pen = QPen(colour, width)
        pen.setJoinStyle(Qt.RoundJoin)
        pen.setCapStyle(Qt.RoundCap)
        if dashed:
            pen.setStyle(Qt.DashLine)
        painter.setPen(pen)
        painter.drawPolyline(QPolygonF([QPointF(x, y) for x, y in points]))

    def _empty(self, painter, text):
        self._text(painter, self.width() / 2, self.height() / 2, text,
                   QColor(_scheme().muted), "c", self.small)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        self._points = []
        if self._render:
            self._render(painter)
        painter.end()

    def _set(self, render):
        self._render = render
        self._mouse_x = None
        self.update()

    def mouseMoveEvent(self, event):
        if self._readout is not None:
            self._mouse_x = event.position().x()
            self.update()

    def leaveEvent(self, event):
        self._mouse_x = None
        self.update()
        super().leaveEvent(event)

    def _draw_hover(self, painter):
        if (self._readout is None or self._mouse_x is None
                or not self._points):
            return
        scheme = _scheme()
        points = self._points
        index = min(range(len(points)),
                    key=lambda i: abs(points[i][0] - self._mouse_x))
        px, py = points[index]
        _, y0, x1, y1 = self._plot_area()
        painter.setPen(QPen(QColor(scheme.muted), 1, Qt.DashLine))
        painter.drawLine(QPointF(px, y0), QPointF(px, y1))
        painter.setPen(QPen(QColor(scheme.panel), 2))
        painter.setBrush(themeColor())
        painter.drawEllipse(QPointF(px, py), 4, 4)
        painter.setBrush(Qt.NoBrush)
        result = self._readout(index, self._series[index])
        text, colour = (result if isinstance(result, tuple)
                        else (result, scheme.text))
        if not text:
            return
        colour = _colour(colour)
        painter.setFont(self.mono)
        metrics = QFontMetrics(self.mono)
        box = metrics.boundingRect(0, 0, 400, 400, Qt.AlignLeft, text)
        right = px > (self.PAD_LEFT + x1) / 2
        left = px - 12 - box.width() if right else px + 12
        rect = QRectF(left - 7, y0 + 1, box.width() + 14, box.height() + 10)
        painter.setPen(QPen(QColor(scheme.border), 1))
        painter.setBrush(QColor(scheme.panel_high))
        painter.drawRoundedRect(rect, 4, 4)
        painter.setBrush(Qt.NoBrush)
        painter.setPen(colour)
        painter.drawText(QRectF(left, y0 + 6, box.width() + 4,
                                box.height() + 2),
                         Qt.AlignLeft | Qt.AlignTop, text)

    def line(self, series, labels=None, colour=None, fill=True,
             formatter=_default_formatter, baseline=None, readout=None,
             overlay=None, overlay_label=None):
        series = list(series)

        def render(painter):
            scheme = _scheme()
            if len(series) < 2:
                self._empty(painter, "not enough data")
                return
            lo, hi = min(series), max(series)
            if baseline is not None:
                lo, hi = min(lo, baseline), max(hi, baseline)
            if overlay:
                lo, hi = min(lo, min(overlay)), max(hi, max(overlay))
            span = (hi - lo) or 1.0
            lo, hi = lo - span * 0.08, hi + span * 0.08
            self._frame(painter, lo, hi, 4, formatter)
            x0, y0, x1, y1 = self._plot_area()
            step = (x1 - x0) / (len(series) - 1)

            def point(index, value):
                return (x0 + index * step,
                        y1 - (value - lo) / (hi - lo) * (y1 - y0))

            points = [point(i, v) for i, v in enumerate(series)]
            stroke = _colour(colour)
            if fill:
                shade = QColor(themeColor())
                shade.setAlpha(40)
                painter.setPen(Qt.NoPen)
                painter.setBrush(shade)
                painter.drawPolygon(QPolygonF(
                    [QPointF(x, y) for x, y in points]
                    + [QPointF(x1, y1), QPointF(x0, y1)]))
                painter.setBrush(Qt.NoBrush)
            if baseline is not None:
                self._dashed(painter, QColor(scheme.muted), x0,
                             point(0, baseline)[1], x1)
            self._poly(painter, points, stroke, 2.5)
            if overlay and len(overlay) == len(series):
                ceiling = [point(i, v) for i, v in enumerate(overlay)]
                self._poly(painter, ceiling, themeColor(), 1.6, True)
                if overlay_label:
                    self._text(painter, ceiling[-1][0] - 4,
                               ceiling[-1][1] - 10, overlay_label,
                               themeColor(), "e")
            if labels:
                for index in (0, len(labels) - 1):
                    self._text(painter, point(index, series[index])[0],
                               y1 + 13, labels[index],
                               QColor(scheme.muted),
                               "w" if index == 0 else "e")
            if readout:
                self._points = points
            self._draw_hover(painter)

        self._readout = readout
        self._series = series
        self._set(render)

    def lines(self, series_map, labels=None, best=None,
              formatter=_default_formatter, baseline=None):
        self._readout = None

        def render(painter):
            scheme = _scheme()
            drawable = [(name, values) for name, values in series_map
                        if len(values) >= 2]
            if not drawable:
                self._empty(painter, "not enough data")
                return
            every = [v for _, values in drawable for v in values]
            lo, hi = min(every), max(every)
            if baseline is not None:
                lo, hi = min(lo, baseline), max(hi, baseline)
            span = (hi - lo) or 1.0
            lo, hi = lo - span * 0.08, hi + span * 0.08
            self._frame(painter, lo, hi, 4, formatter)
            x0, y0, x1, y1 = self._plot_area()
            if baseline is not None:
                y = y1 - (baseline - lo) / (hi - lo) * (y1 - y0)
                self._dashed(painter, QColor(scheme.muted), x0, y, x1)
            for name, values in drawable:
                step = (x1 - x0) / (len(values) - 1)
                points = [(x0 + i * step,
                           y1 - (v - lo) / (hi - lo) * (y1 - y0))
                          for i, v in enumerate(values)]
                winner = name == best
                good = _colour("good")
                self._poly(painter, points,
                           good if winner else QColor(scheme.faint),
                           2.6 if winner else 1.4)
                self._text(painter, points[-1][0] - 4, points[-1][1] - 9,
                           name, good if winner else QColor(scheme.muted),
                           "e")
            if labels:
                longest = max(drawable, key=lambda item: len(item[1]))[1]
                step = (x1 - x0) / (len(longest) - 1)
                for index in (0, len(labels) - 1):
                    self._text(painter, x0 + index * step, y1 + 13,
                               labels[index], QColor(scheme.muted),
                               "w" if index == 0 else "e")

        self._set(render)

    def bars(self, values, labels, formatter=_default_formatter,
             threshold=None):
        values = list(values)
        self._readout = None

        def render(painter):
            scheme = _scheme()
            if not values:
                return
            lo, hi = min(values + [0.0]), max(values + [0.0])
            if threshold is not None:
                hi = max(hi, threshold)
            span = (hi - lo) or 1.0
            lo, hi = lo - span * 0.08, hi + span * 0.08
            self._frame(painter, lo, hi, 4, formatter)
            x0, y0, x1, y1 = self._plot_area()
            slot = (x1 - x0) / len(values)
            width = min(slot * 0.62, 34)

            def y_of(value):
                return y1 - (value - lo) / (hi - lo) * (y1 - y0)

            zero = y_of(0.0)
            painter.setPen(Qt.NoPen)
            for index, value in enumerate(values):
                centre = x0 + slot * (index + 0.5)
                painter.setBrush(_colour("good" if value >= 0 else "bad"))
                y_val = y_of(value)
                top = min(y_val, zero) - 1
                painter.drawRect(QRectF(centre - width / 2, top, width,
                                        max(y_val, zero) - top))
                self._text(painter, centre, y1 + 13, labels[index],
                           QColor(scheme.muted), "c")
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(QColor(scheme.muted), 1))
            painter.drawLine(QPointF(x0, zero), QPointF(x1, zero))
            if threshold is not None:
                y = y_of(threshold)
                warn = _colour("warn")
                self._dashed(painter, warn, x0, y, x1)
                self._text(painter, x1, y - 8, f"cost {threshold:.0f}bp",
                           warn, "e")

        self._set(render)
