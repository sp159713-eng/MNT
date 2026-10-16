from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget
from qfluentwidgets import CaptionLabel, isDarkTheme, qconfig, themeColor

import gapcard
import theme as theme_module
from qtgui.common import TitledCard

ROW = 28


class GapBars(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows = []
        self.setFixedHeight(ROW)
        qconfig.themeChanged.connect(lambda *_: self.update())

    def paintEvent(self, _event):
        if not self.rows:
            return
        scheme = theme_module.Dark if isDarkTheme() else theme_module.Light
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        small = QFont(self.font())
        small.setPointSizeF(9)
        mono = QFont("Consolas")
        mono.setPointSizeF(8.5)
        width = max(self.width(), 300)
        label_w, value_w = 230, 70
        values = [v for _, v, _ in self.rows]
        reach = QFontMetrics(mono).horizontalAdvance("-0000") + 8
        x0 = label_w + 16 + (reach if min(values) < 0 else 0)
        x1 = width - value_w
        lo, hi = min(0.0, min(values)), max(0.0, max(values))
        span = (hi - lo) or 1.0
        zero = x0 + (-lo / span) * (x1 - x0)
        step = ROW
        for index, (label, value, kind) in enumerate(self.rows):
            y = step * (index + 0.5)
            end = zero + value / span * (x1 - x0)
            a, b = min(zero, end), max(zero, end, min(zero, end) + 2)
            rect = QRectF(a, y - 9, b - a, 18)
            if kind == "ours":
                painter.setPen(Qt.NoPen)
                painter.setBrush(themeColor())
            elif kind == "oracle":
                pen = QPen(QColor(scheme.muted), 1)
                pen.setStyle(Qt.DashLine)
                painter.setPen(pen)
                painter.setBrush(Qt.NoBrush)
            else:
                painter.setPen(QPen(QColor(scheme.muted), 1))
                muted = QColor(scheme.muted)
                muted.setAlpha(60)
                painter.setBrush(muted)
            painter.drawRect(rect)
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QColor(scheme.text if kind == "ours"
                                  else scheme.muted))
            painter.setFont(small)
            metrics = QFontMetrics(small)
            painter.drawText(QPointF(label_w - 10
                                     - metrics.horizontalAdvance(label),
                                     y + metrics.ascent() / 2 - 1), label)
            painter.setFont(mono)
            metrics = QFontMetrics(mono)
            text = f"{value:+.0f}"
            left = (b + 8 if value >= 0
                    else a - 8 - metrics.horizontalAdvance(text))
            painter.drawText(QPointF(left, y + metrics.ascent() / 2 - 1),
                             text)
        painter.setPen(QPen(QColor(scheme.border), 1))
        painter.drawLine(QPointF(zero, 0), QPointF(zero, ROW * len(self.rows)))
        painter.end()


class GapCard(TitledCard):
    EMPTY = "Run a walk-forward in Backtest to see the gap"

    def __init__(self, parent=None):
        super().__init__("How close to perfect",
                         "net excess return, bp per period", parent)
        self.bars = GapBars(self)
        self.body.addWidget(self.bars)
        self.line = CaptionLabel("", self)
        self.line.setWordWrap(True)
        self.body.addWidget(self.line)
        self.note = CaptionLabel("", self)
        self.note.setWordWrap(True)
        self.body.addWidget(self.note)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.refresh()

    def refresh(self):
        try:
            import config

            signal = config.PRODUCTION_SIGNAL
        except Exception:
            signal = None
        record = gapcard.latest_walkforward(signal)
        rows = gapcard.gap_rows(record) if record else []
        self.bars.rows = rows
        self.bars.setFixedHeight(ROW * max(len(rows), 1))
        if not rows:
            self.line.setText(self.EMPTY)
            self.note.setText("")
            self.bars.hide()
        else:
            self.bars.show()
            self.line.setText(gapcard.readout(record))
            self.note.setText(gapcard.caption(record))
        self.bars.update()
