from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import BodyLabel, CaptionLabel, LineEdit, PushButton

from qtgui.common import DataTable, TitledCard, set_tone
from qtgui.stock_detail import StockDetail
from qtgui.stock_grid import StockGrid, quotes

LEFT = Qt.AlignLeft
CENTER = Qt.AlignHCenter

RESULT_COLUMNS = (("Symbol", 140, LEFT), ("Company", 420, LEFT),
                  ("Listed", 90, CENTER))

HELP = ("A UNIVERSE name is fitted, ranked and can be bought. A "
        "WATCHLIST name is fetched and shown and never reaches "
        "the model. Every name here is one you added, so any of them "
        "can be removed. Names live in artifacts/stocks.json and "
        "survive a reinstall; refit before the Book or Orders "
        "tabs are trusted.")


class UniversePage(QWidget):
    def __init__(self, app, parent=None):
        super().__init__(parent)
        self.app = app
        self.details = []
        self._force_symbol = None
        self.setObjectName("page-Stock")
        root = QVBoxLayout(self)
        root.setContentsMargins(32, 24, 32, 24)
        root.setSpacing(0)
        self.card = TitledCard("Stock", "every name the book can see - last close and day change; double-click for details", self)
        body = self.card.body
        root.addWidget(self.card, 1)

        self.grid = StockGrid("No stocks in the universe yet", self.card)
        self.grid.selectionChanged.connect(self._select)
        self.grid.activated.connect(self._detail)
        body.addWidget(self.grid, 1)

        row = QHBoxLayout()
        row.setSpacing(12)
        row.addWidget(BodyLabel("Symbol", self.card))
        self.entry = LineEdit(self.card)
        self.entry.returnPressed.connect(self.search)
        row.addWidget(self.entry, 1)
        body.addLayout(row)

        self.results = DataTable(RESULT_COLUMNS, "No matches",
                                 stretch=1, parent=self.card)
        self.results.table.setMaximumHeight(170)
        self.results.table.itemSelectionChanged.connect(self._pick_result)
        self.results.hide()
        body.addWidget(self.results)
        self.results_shown = False

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.search_button = PushButton("Search", self.card)
        self.search_button.clicked.connect(self.search)
        self.add_universe_button = PushButton("Add to universe", self.card)
        self.add_universe_button.clicked.connect(lambda: self.add(False))
        self.add_watch_button = PushButton("Add to watchlist", self.card)
        self.add_watch_button.clicked.connect(lambda: self.add(True))
        self.remove_button = PushButton("Remove", self.card)
        self.remove_button.clicked.connect(self.remove)
        self.detail_button = PushButton("Details", self.card)
        self.detail_button.clicked.connect(self._detail)
        self.force_button = PushButton("Add anyway", self.card)
        self.force_button.clicked.connect(self.add_force)
        self.force_button.setEnabled(False)
        for button in (self.search_button, self.add_universe_button,
                       self.add_watch_button, self.remove_button,
                       self.detail_button, self.force_button):
            buttons.addWidget(button)
        buttons.addStretch(1)
        body.addLayout(buttons)

        self.note = CaptionLabel("", self.card)
        self.note.setWordWrap(True)
        set_tone(self.note, "muted")
        body.addWidget(self.note)
        self.help_label = CaptionLabel(HELP, self.card)
        self.help_label.setWordWrap(True)
        set_tone(self.help_label, "muted")
        body.addWidget(self.help_label)

        self._fill()

    def _rows(self):
        import config

        base = {s.upper() for s in config.BASE_UNIVERSE}
        watch = {s.upper() for s in config.WATCHLIST}
        seen, rows = set(), []
        for name in list(config.UNIVERSE) + list(config.WATCHLIST):
            key = name.upper()
            if key in seen:
                continue
            seen.add(key)
            rows.append((name,
                         config.SECTORS.get(name) or "-",
                         "built in" if key in base else "added",
                         "watchlist" if key in watch else "universe"))
        rows.sort(key=lambda item: (item[3] == "watchlist", item[0]))
        return rows

    def _fill(self, message=""):
        import config

        rows = self._rows()
        if self.grid.set_rows(rows):
            symbols = self.grid.symbols()
            self.app.worker.submit(lambda: quotes(symbols),
                                   self.grid.set_quotes, lambda _e: None)
        summary = (f"Universe {len(config.UNIVERSE)} "
                   f"({len(config.USER_UNIVERSE)} added)"
                   f"   |   Watchlist {len(config.WATCHLIST)}")
        self.note.setText(f"{message}   {summary}" if message else summary)
        self._selection_buttons()

    def _selected(self):
        return self.grid.selected()

    def _select(self):
        values = self._selected()
        if values:
            self.entry.setText(values[0])
        self._selection_buttons()

    def _detail(self):
        values = self._selected()
        symbol = values[0] if values else self.entry.text().strip().upper()
        if not symbol:
            self._fill("Pick a stock first, then Details.")
            return
        self.details = [d for d in self.details if d.isVisible()]
        window = StockDetail(self.app, symbol)
        self.details.append(window)
        window.show()

    def _selection_buttons(self):
        values = self._selected()
        self.remove_button.setEnabled(not values or values[2] != "built in")

    def _buttons(self, enabled):
        self.add_universe_button.setEnabled(enabled)
        self.add_watch_button.setEnabled(enabled)
        self.remove_button.setEnabled(enabled)
        self.force_button.setEnabled(enabled and bool(self._force_symbol))

    def search(self):
        query = self.entry.text().strip()
        if not query:
            self._fill("Type a company or ticker first, e.g. Divi or DIVISLAB.")
            return
        self.search_button.setEnabled(False)
        self.note.setText(f"Searching NSE for {query!r}...")

        def work():
            import config

            return config.search_symbols(query)

        self.app.worker.submit(work, self._searched, self._search_failed)

    def _searched(self, rows):
        self.search_button.setEnabled(True)
        if not rows:
            self.results.set_rows([])
            self._show_results(False)
            self._fill(f"Nothing on NSE matched {self.entry.text().strip()!r}.")
            return
        self.results.set_rows([(r["symbol"], r["name"], r["exchange"])
                               for r in rows])
        self._show_results(True)
        self._fill(f"{len(rows)} matches - pick one, then Add to universe or "
                   f"watchlist.")

    def _search_failed(self, error):
        self.search_button.setEnabled(True)
        self._show_results(False)
        self._fill(f"{type(error).__name__}: {error}")

    def _show_results(self, visible):
        self.results.setVisible(visible)
        self.results_shown = visible

    def _pick_result(self):
        table = self.results.table
        row = table.currentRow()
        if row < 0 or not table.selectedItems():
            return
        self.entry.setText(table.item(row, 0).text())

    def add(self, watchlist):
        import config

        symbol = config.valid_symbol(self.entry.text())
        if not symbol:
            self._fill("Type an NSE symbol first, e.g. DIVISLAB.")
            return
        self._force_symbol = None
        self._buttons(False)
        self.note.setText(f"Checking {symbol} for tradeable history...")
        self.app.worker.submit(lambda: self._work(symbol, watchlist),
                               self._done, self._failed)

    def _work(self, symbol, watchlist):
        import config

        if watchlist:
            label = config.detect_sector(symbol)
            added, message = config.add_stock(symbol, watchlist=True,
                                              sector=label)
            return added, message, None
        ok, detail = config.verify_symbol(symbol)
        if not ok:
            short = symbol if "of history, needs" in detail else None
            if short:
                detail += "  Press Add anyway to force it in."
            return False, detail, short
        label = config.detect_sector(symbol)
        added, message = config.add_stock(symbol, watchlist=False,
                                          sector=label)
        return added, f"{detail} {message}", None

    def _done(self, payload):
        ok, detail, short = payload
        self._force_symbol = short
        self._buttons(True)
        if ok:
            self.entry.clear()
        self._fill(detail)

    def add_force(self):
        symbol = self._force_symbol
        if not symbol:
            return
        self._buttons(False)
        self.note.setText(f"Adding {symbol} without the history check...")
        self.app.worker.submit(lambda: self._force_work(symbol),
                               self._done, self._failed)

    def _force_work(self, symbol):
        import config

        label = config.detect_sector(symbol)
        added, message = config.add_stock(symbol, watchlist=False,
                                          sector=label)
        note = (f"{message} Forced in without the history check - the model "
                f"cannot score it until it has a year of bars.")
        return added, note, None

    def _failed(self, error):
        self._buttons(True)
        self._fill(f"{type(error).__name__}: {error}")

    def remove(self):
        import config

        ok, detail = config.remove_stock(self.entry.text())
        if ok:
            self.entry.clear()
        self._fill(detail)

    def on_show(self):
        self._fill()
