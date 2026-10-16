from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor, QFontMetrics
from PySide6.QtWidgets import (QAbstractItemView, QHBoxLayout, QHeaderView,
                               QStackedWidget, QTableWidgetItem, QVBoxLayout,
                               QWidget)
from qfluentwidgets import (CaptionLabel, CardWidget, InfoBar,
                            InfoBarPosition, LargeTitleLabel, LineEdit,
                            MessageBox, MessageBoxBase, SegmentedWidget,
                            SpinBox, StrongBodyLabel,
                            SubtitleLabel, TableWidget, isDarkTheme, qconfig)


PAIRS = {"good": ("#00794f", "#3ed491"), "bad": ("#e0492c", "#ff6b64"),
         "warn": ("#b06f00", "#f5a623"), "muted": ("#5b6472", "#9aa4b2")}


def tone_color(tone):
    return QColor(PAIRS[tone][1 if isDarkTheme() else 0])


def good_color():
    return tone_color("good")


def bad_color():
    return tone_color("bad")


def band_color(tone):
    colour = tone_color(tone)
    colour.setAlpha(46 if isDarkTheme() else 36)
    return colour


def set_tone(label, tone):
    light, dark = PAIRS[tone]
    label.setTextColor(QColor(light), QColor(dark))


def clear_tone(label):
    label.setTextColor()


class EditSpinBox(SpinBox):
    def _fit(self):
        edit = self.lineEdit()
        width = max(self.width() - 100, 20)
        if edit.width() != width or edit.x() != 10:
            edit.setGeometry(10, 1, width, self.height() - 2)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit()

    def changeEvent(self, event):
        super().changeEvent(event)
        self._fit()

    def showEvent(self, event):
        super().showEvent(event)
        self._fit()

    def paintEvent(self, event):
        self._fit()
        super().paintEvent(event)


class StatCard(CardWidget):
    def __init__(self, label, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(4)
        self.value = LargeTitleLabel("-", self)
        layout.addWidget(self.value)
        layout.addWidget(CaptionLabel(label, self))


class TitledCard(CardWidget):
    def __init__(self, title, subtitle="", parent=None):
        super().__init__(parent)
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(16, 16, 16, 16)
        self.body.setSpacing(8)
        self.title = StrongBodyLabel(title, self)
        self.body.addWidget(self.title)
        if subtitle:
            self.subtitle = CaptionLabel(subtitle, self)
            self.body.addWidget(self.subtitle)


class DataTable(QWidget):
    def __init__(self, columns, empty_text, stretch=None, parent=None):
        super().__init__(parent)
        self.columns = columns
        self.aligns = [align for _, _, align in columns]
        self.tones = []
        self.bands = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.table = TableWidget(self)
        self.table.setColumnCount(len(columns))
        self.table.setHorizontalHeaderLabels([name for name, _, _ in columns])
        self.table.verticalHeader().hide()
        self.table.setBorderVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        header = self.table.horizontalHeader()
        header.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        metrics = QFontMetrics(header.font())
        last = len(columns) - 1
        stretch = last if stretch is None else stretch
        for index, (name, width, _) in enumerate(columns):
            if index == stretch:
                header.setSectionResizeMode(index, QHeaderView.Stretch)
            else:
                header.setSectionResizeMode(index, QHeaderView.Interactive)
                self.table.setColumnWidth(
                    index, max(width, metrics.horizontalAdvance(name) + 40))
        self.table.hide()
        layout.addWidget(self.table, 1)
        self.empty = CaptionLabel(empty_text, self)
        self.empty.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.empty, 1)
        qconfig.themeChanged.connect(self._paint)

    def set_empty_text(self, text):
        self.empty.setText(text)

    def row_count(self):
        return self.table.rowCount()

    def set_rows(self, rows, tones=None, bands=None):
        self.tones = list(tones) if tones else [None] * len(rows)
        self.bands = list(bands) if bands else [None] * len(rows)
        self.table.setRowCount(0)
        self.table.setRowCount(len(rows))
        for row, values in enumerate(rows):
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setTextAlignment(self.aligns[column] | Qt.AlignVCenter)
                self.table.setItem(row, column, item)
        self._paint()
        has_rows = len(rows) > 0
        self.table.setVisible(has_rows)
        self.empty.setVisible(not has_rows)

    def _paint(self):
        for row in range(self.table.rowCount()):
            tone = self.tones[row] if row < len(self.tones) else None
            band = self.bands[row] if row < len(self.bands) else None
            for column in range(self.table.columnCount()):
                item = self.table.item(row, column)
                if item is None:
                    continue
                if tone:
                    item.setForeground(QBrush(tone_color(tone)))
                else:
                    item.setData(Qt.ForegroundRole, None)
                if band:
                    item.setBackground(QBrush(band_color(band)))
                else:
                    item.setData(Qt.BackgroundRole, None)


def confirm(parent, title, message, danger=False, default_yes=True):
    box = MessageBox(title, message, parent.window())
    box.yesButton.setText("Yes")
    box.cancelButton.setText("No")
    if not default_yes:
        box.yesButton.setAutoDefault(False)
        box.yesButton.setDefault(False)
        box.cancelButton.setAutoDefault(True)
        box.cancelButton.setDefault(True)
        box.cancelButton.setFocus()
    if danger:
        box.yesButton.setStyleSheet(
            "PushButton{background:%s;color:white;border:1px solid %s;"
            "border-radius:5px;}" % (bad_color().name(), bad_color().name()))
    return bool(box.exec())


class AmountDialog(MessageBoxBase):
    def __init__(self, title, message, initial, parent):
        super().__init__(parent)
        self.viewLayout.addWidget(SubtitleLabel(title, self))
        text = CaptionLabel(message, self)
        text.setWordWrap(True)
        self.viewLayout.addWidget(text)
        self.entry = LineEdit(self)
        self.entry.setText(initial)
        self.viewLayout.addWidget(self.entry)
        self.yesButton.setText("OK")
        self.cancelButton.setText("Cancel")
        self.widget.setMinimumWidth(360)
        self.amount = None
        self.entry.textChanged.connect(lambda _t: self.entry.setStyleSheet(""))

    def validate(self):
        text = self.entry.text().strip().replace(",", "")
        try:
            amount = float(text)
        except ValueError:
            self._invalid()
            return False
        if amount != amount or amount in (float("inf"), float("-inf")):
            self._invalid()
            return False
        self.amount = amount
        return True

    def _invalid(self):
        self.entry.setStyleSheet("LineEdit{color:%s;}" % bad_color().name())


def ask_amount(parent, title, message, initial=""):
    dialog = AmountDialog(title, message, initial, parent.window())
    if dialog.exec():
        return dialog.amount
    return None


def fail_bar(parent, title, message):
    InfoBar.error(title, message, duration=6000,
                  position=InfoBarPosition.TOP_RIGHT, parent=parent.window())


def title_row(title, parent):
    from qfluentwidgets import TitleLabel

    row = QHBoxLayout()
    row.setSpacing(12)
    row.addWidget(TitleLabel(title, parent))
    row.addStretch(1)
    return row


class TabbedContainer(QWidget):
    def __init__(self, name, views, parent=None):
        super().__init__(parent)
        self.setObjectName(f"page-{name}")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 24, 32, 24)
        layout.setSpacing(16)
        self.pivot = SegmentedWidget(self)
        self.stack = QStackedWidget(self)
        self.views = views
        for title, view in views:
            view.setObjectName(f"view-{name}-{title}")
            self.stack.addWidget(view)
            self.pivot.addItem(
                routeKey=view.objectName(), text=title,
                onClick=lambda _=False, v=view: self.show_view(v))
        self.pivot.setCurrentItem(self.stack.widget(0).objectName())
        layout.addWidget(self.pivot)
        layout.addWidget(self.stack, 1)

    def show_view(self, view):
        self.stack.setCurrentWidget(view)
        self.pivot.setCurrentItem(view.objectName())
        self.on_show()

    def on_show(self):
        handler = getattr(self.stack.currentWidget(), "on_show", None)
        if handler is not None:
            handler()
