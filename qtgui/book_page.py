from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QHBoxLayout, QHeaderView,
                               QTableWidgetItem, QVBoxLayout, QWidget)
from qfluentwidgets import (CaptionLabel, CardWidget, InfoBar,
                            InfoBarPosition, LargeTitleLabel,
                            PrimaryPushButton, StrongBodyLabel, TableWidget,
                            TitleLabel)

import book
from qtgui.common import StatCard

DESCRIPTION = ("The names the model would hold today, best score first. "
               "Press Compute holdings to work them out from the latest "
               "prices. Nothing here places an order - Orders does that.")

NOTE = ("Feature values are cross-sectional ranks in [-1, 1], not raw "
        "numbers: +1.00 is the strongest name in the universe that day. "
        "These are the model's picks, not advice - the walk-forward "
        "t-statistic on this edge is below 2, so treat the ordering as a "
        "hypothesis.")

TILES = (("names", "positions"), ("each", "per position"),
         ("cost", "round trip"), ("breakeven", "must move"))

HEADERS = ("#", "Symbol", "Score", "6M mom rank", "21D vol rank", "Why")


class BookPage(QWidget):
    def __init__(self, app, parent=None):
        super().__init__(parent)
        self.app = app
        self.setObjectName("page-Book")
        root = QVBoxLayout(self)
        root.setContentsMargins(32, 24, 32, 24)
        root.setSpacing(16)

        top = QHBoxLayout()
        top.setSpacing(12)
        top.addWidget(TitleLabel("Today's book", self))
        top.addStretch(1)
        self.status = CaptionLabel("", self)
        top.addWidget(self.status)
        self.run_button = PrimaryPushButton("Compute holdings", self)
        self.run_button.clicked.connect(self.compute)
        top.addWidget(self.run_button)
        root.addLayout(top)

        description = CaptionLabel(DESCRIPTION, self)
        description.setWordWrap(True)
        root.addWidget(description)

        stats = QHBoxLayout()
        stats.setSpacing(12)
        self.tiles = {}
        for key, label in TILES:
            tile = StatCard(label, self)
            stats.addWidget(tile, 1)
            self.tiles[key] = tile.value
        root.addLayout(stats)

        card = CardWidget(self)
        body = QVBoxLayout(card)
        body.setContentsMargins(16, 16, 16, 16)
        body.setSpacing(8)
        body.addWidget(StrongBodyLabel("Holdings", card))
        body.addWidget(CaptionLabel("ranked by model score, highest first",
                                    card))
        self.table = TableWidget(card)
        self.table.setColumnCount(len(HEADERS))
        self.table.setHorizontalHeaderLabels(HEADERS)
        self.table.verticalHeader().hide()
        self.table.setBorderVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        header = self.table.horizontalHeader()
        header.setStretchLastSection(True)
        header.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        for column, width in enumerate((56, 128, 112, 144, 144)):
            header.setSectionResizeMode(column, QHeaderView.Interactive)
            self.table.setColumnWidth(column, width)
        self.table.hide()
        body.addWidget(self.table, 1)
        self.empty = CaptionLabel(
            "No holdings yet - press Compute holdings", card)
        self.empty.setAlignment(Qt.AlignCenter)
        body.addWidget(self.empty, 1)
        self.note = CaptionLabel("", card)
        self.note.setWordWrap(True)
        body.addWidget(self.note)
        root.addWidget(card, 1)

    def compute(self):
        self.run_button.setEnabled(False)
        self.status.setText("loading prices and scoring...")
        capital = self.app.capital
        self.app.worker.submit(lambda: book.compute(capital), self._done,
                               self._failed)

    def _done(self, result):
        self.run_button.setEnabled(True)
        self.status.setText(
            f"{result['model']}  |  as of {result['date'].date()}")
        self.tiles["names"].setText(str(result["k"]))
        self.tiles["each"].setText(f"{result['each']:,.0f}")
        self.tiles["cost"].setText(f"{result['cost']:.0f}bp")
        self.tiles["breakeven"].setText(f"{result['breakeven']:.2f}%")

        rows = result["rows"]
        self.table.setRowCount(0)
        self.table.setRowCount(len(rows))
        for position, (_, row) in enumerate(rows.iterrows(), start=1):
            values = (position, row["symbol"], f"{row['score']:+.4f}",
                      f"{row['mom_126']:+.2f}", f"{row['vol_21']:+.2f}",
                      row.get("why", ""))
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                if column in (0, 2, 3, 4):
                    item.setTextAlignment(Qt.AlignCenter)
                self.table.setItem(position - 1, column, item)
        has_rows = self.table.rowCount() > 0
        self.table.setVisible(has_rows)
        self.empty.setVisible(not has_rows)
        self.note.setText(NOTE)

    def _failed(self, error):
        self.run_button.setEnabled(True)
        message = str(error)[:80]
        self.status.setText(message)
        InfoBar.error("Compute failed", message, duration=6000,
                      position=InfoBarPosition.TOP_RIGHT, parent=self)
