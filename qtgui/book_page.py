from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (CaptionLabel, CardWidget, InfoBar,
                            InfoBarPosition, PrimaryPushButton,
                            StrongBodyLabel, TitleLabel)

import book
from qtgui.common import StatCard
from qtgui.stock_grid import StockGrid, quotes

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
        self.grid = StockGrid("No holdings yet - press Compute holdings",
                              card, tile_height=268)
        self.grid.activated.connect(self._detail)
        body.addWidget(self.grid, 1)
        self.details = []
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

        frame = result["rows"]
        rows = [(row["symbol"], "-", "pick", "universe")
                for _, row in frame.iterrows()]
        self.grid.rows = []
        self.grid.set_rows(rows)
        for position, (_, row) in enumerate(frame.iterrows(), start=1):
            lines = [(f"#{position}  score {row['score']:+.3f}", "muted"),
                     (f"6M mom {row['mom_126']:+.2f}   "
                      f"21D vol {row['vol_21']:+.2f}", "muted")]
            why = row.get("why", "")
            if why:
                lines.append((why, "good"))
            self.grid.set_details(row["symbol"], lines)
        symbols = self.grid.symbols()
        self.app.worker.submit(lambda: quotes(symbols), self.grid.set_quotes,
                               lambda _e: None)
        self.note.setText(NOTE)

    def _detail(self):
        row = self.grid.selected()
        if not row:
            return
        from qtgui.stock_detail import StockDetail

        self.details = [d for d in self.details if d.isVisible()]
        window = StockDetail(self.app, row[0])
        self.details.append(window)
        window.show()

    def _failed(self, error):
        self.run_button.setEnabled(True)
        message = str(error)[:80]
        self.status.setText(message)
        InfoBar.error("Compute failed", message, duration=6000,
                      position=InfoBarPosition.TOP_RIGHT, parent=self)
